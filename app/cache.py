"""
cache.py
========
Redis 缓存层（跨进程共享分析结果）。

职责：
  1. 提供 Redis 连接（懒连接 + 静默降级）
  2. 提供 @redis_cache 装饰器，透明地为 calc_* 函数添加 Redis 缓存
  3. 提供 cache_key / get / set / delete 等基础工具函数
  4. 提供 invalidate() 用于按版本号批量失效

设计原则：
  - **零破坏**：Redis 任何异常都被静默吞掉，调用方走原计算路径
  - **懒连接**：第一次使用时才建连接，连不上就永久降级
  - **数据版本化**：key 含 db_mtime，DB 文件更新后所有旧 key 自然失效
  - **参数安全**：DataFrame 等大对象只哈希其"指纹"（前 N 行 + 长度），不真的序列化整张表

key 命名：
    lottery:v{db_mtime}:{func_name}:{filter_signature}
    例：
      lottery:v1715845123.45:pan_stats:a3f2c1
      lottery:v1715845123.45:streaks:2024-01-01_2024-12-31:b9d1
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import pickle
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any, Callable, Optional, Tuple

from config import REDIS_CONFIG

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 1. Redis 客户端（懒连接 + 永久降级）
# ──────────────────────────────────────────────

_client = None              # redis.Redis 实例（懒加载）
_client_checked = False     # 是否已尝试连接过（用于永久降级）
_connect_failed = False     # 连接是否失败过（True 则后续不再重试）


def _get_client():
    """
    获取 Redis 客户端（懒连接）。

    Returns:
        redis.Redis 实例；若不可用返回 None。

    行为：
      - 第一次调用时尝试连接；连接成功则返回客户端
      - 连接失败或任何 redis 异常 → 永久降级（不重复尝试连接）
      - 这避免每次请求都重试连接浪费 2 秒超时
    """
    global _client, _client_checked, _connect_failed

    if not REDIS_CONFIG.enabled:
        return None

    if _connect_failed:
        return None

    if _client is None and not _client_checked:
        _client_checked = True
        try:
            import redis  # 延迟导入，避免无 Redis 环境 import 即报错
            _client = redis.Redis(
                host=REDIS_CONFIG.host,
                port=REDIS_CONFIG.port,
                db=REDIS_CONFIG.db,
                password=REDIS_CONFIG.password,
                socket_timeout=REDIS_CONFIG.timeout,
                socket_connect_timeout=REDIS_CONFIG.timeout,
                decode_responses=False,   # 我们自己处理 pickle/JSON
                health_check_interval=30,
            )
            # 触发一次 ping，失败则标记降级
            _client.ping()
            logger.info(
                "Redis 连接成功: %s:%s db=%s",
                REDIS_CONFIG.host, REDIS_CONFIG.port, REDIS_CONFIG.db,
            )
        except ImportError:
            logger.warning("redis 包未安装，缓存功能已禁用。请 pip install redis")
            _connect_failed = True
            return None
        except Exception as e:
            logger.warning(
                "Redis 连接失败 (%s:%s db=%s): %s。已永久降级到原始计算路径。",
                REDIS_CONFIG.host, REDIS_CONFIG.port, REDIS_CONFIG.db, e,
            )
            _connect_failed = True
            return None

    return _client


def is_redis_available() -> bool:
    """供 UI 显示 Redis 是否可用（不抛异常）。"""
    return _get_client() is not None


def reset_client() -> None:
    """测试用：重置客户端缓存以触发重新连接。"""
    global _client, _client_checked, _connect_failed
    _client = None
    _client_checked = False
    _connect_failed = False


# ──────────────────────────────────────────────
# 2. key 生成
# ──────────────────────────────────────────────

def make_versioned_key(
    func_name: str,
    db_mtime: Optional[float],
    filter_signature: str = "",
) -> str:
    """
    构造一个版本化的缓存 key。

    Args:
        func_name:        被装饰的函数名
        db_mtime:         数据库文件修改时间（数据版本号）
        filter_signature: 过滤参数指纹

    Returns:
        类似 "lottery:v1715845123.45:pan_stats:a3f2c1"
    """
    version = f"{db_mtime:.2f}" if db_mtime else "0.0"
    if filter_signature:
        return f"{REDIS_CONFIG.key_prefix}:v{version}:{func_name}:{filter_signature}"
    return f"{REDIS_CONFIG.key_prefix}:v{version}:{func_name}"


def hash_dataframe_fingerprint(df: Any, n_rows: int = 200) -> str:
    """
    计算 DataFrame 的指纹（用于 cache key）。

    策略：
      - 取前 n_rows + 总行数 + 列名
      - 用 hash_pandas_object 计算前 n_rows 的 hash
      - 不真的序列化整张表（避免几 MB DataFrame 哈希几秒）

    Args:
        df:     pandas DataFrame（也接受 None / 空 DataFrame）
        n_rows: 参与哈希的行数

    Returns:
        16 字符 hex 字符串
    """
    import pandas as pd
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return "empty"

    n_total = len(df)
    head = df.head(n_rows)

    try:
        # hash_pandas_object 对每行算 hash，再聚合
        row_hash = pd.util.hash_pandas_object(head, index=False).values
        head_digest = hashlib.md5(row_hash.tobytes()).hexdigest()[:12]
    except Exception:
        head_digest = "fallback"

    # 包含：head hash + 总行数 + 列名（防止列结构变化时缓存命中错误）
    sig_input = f"{head_digest}|n={n_total}|cols={','.join(map(str, head.columns))}"
    return hashlib.md5(sig_input.encode("utf-8")).hexdigest()[:16]


def make_filter_signature(args: Tuple, kwargs: dict) -> str:
    """
    从函数参数中提取可哈希的"过滤指纹"。

    规则：
      - pandas DataFrame → hash_dataframe_fingerprint(df)
      - tuple (start_date, end_date) → ISO 字符串拼接
      - 其它 (str/int/float) → repr
      - 不识别类型 → 跳过（不会破坏，只是不参与 key）

    Args:
        args:   位置参数
        kwargs: 关键字参数

    Returns:
        短 hex 串（如 "a3f2c1"）；空参时返回 ""。
    """
    parts = []

    for i, arg in enumerate(args):
        sig = _signature_one(i, arg)
        if sig is not None:
            parts.append(sig)

    for k, v in sorted(kwargs.items()):
        sig = _signature_one(k, v)
        if sig is not None:
            parts.append(sig)

    if not parts:
        return ""

    raw = "|".join(parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _signature_one(key, value):
    """对单个值计算其签名片段。"""
    import pandas as pd

    if isinstance(value, pd.DataFrame):
        return f"{key}={hash_dataframe_fingerprint(value)}"

    if isinstance(value, tuple) and len(value) == 2:
        a, b = value
        # (start_date, end_date) 类型
        if isinstance(a, (date, datetime)) and isinstance(b, (date, datetime)):
            return f"{key}={a.isoformat()}_{b.isoformat()}"

    if isinstance(value, (str, int, float, bool)):
        return f"{key}={value!r}"

    return None  # 不识别，不参与 key


# ──────────────────────────────────────────────
# 3. dataclass 类型注册表
# ──────────────────────────────────────────────

# 注册所有需要自动还原的 dataclass 类型
# 格式：类型名（字符串）→ 类型对象
_DATACLASS_REGISTRY: dict[str, type] = {}


def register_dataclass(cls: type) -> type:
    """装饰器：注册一个 dataclass 到反序列化注册表。

    用法：
        @register_dataclass
        @dataclass
        class KpiResult:
            total_matches: int = 0
            ...
    """
    if is_dataclass(cls):
        _DATACLASS_REGISTRY[cls.__name__] = cls
    return cls


def _auto_register_analytics_classes():
    """延迟导入并注册 analytics.py 中的所有 dataclass。"""
    if _DATACLASS_REGISTRY:
        return  # 已注册过，跳过
    try:
        from analytics import (
            KpiResult,
            StreakRecord,
            StreakResult,
            PanStats,
            GoalStats,
            UnderdogTrendPoint,
            TrackingStats,
            FullAnalyticsResult,
        )
        for cls in (
            KpiResult,
            StreakRecord,
            StreakResult,
            PanStats,
            GoalStats,
            UnderdogTrendPoint,
            TrackingStats,
            FullAnalyticsResult,
        ):
            if is_dataclass(cls):
                _DATACLASS_REGISTRY[cls.__name__] = cls
        logger.debug("已注册 %d 个 dataclass 类型", len(_DATACLASS_REGISTRY))
    except ImportError as e:
        logger.warning("无法导入 analytics 模块注册 dataclass: %s", e)


# ──────────────────────────────────────────────
# 4. 序列化 / 反序列化
# ──────────────────────────────────────────────

def serialize(value: Any) -> bytes:
    """
    把 Python 对象序列化为 bytes。

    策略（按顺序选择第一个匹配）：
      1. DataFrame             → pickle
      2. 含 DataFrame 的 tuple → 整个 pickle（保留异构结构）
      3. dataclass             → {"__type__": "ClassName", "data": asdict} → JSON
      4. tuple / list          → JSON（递归处理内部 dataclass，带类型标记）
      5. 其它                   → JSON

    设计：dataclass 序列化时添加类型标记，反序列化时可完整还原为原类型。
    """
    if value is None:
        return b""

    # 延迟导入（避免无 pandas 环境 import 失败）
    try:
        import pandas as pd
    except ImportError:
        pd = None

    # 1) 纯 DataFrame → pickle
    if pd is not None and isinstance(value, pd.DataFrame):
        return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)

    # 2) tuple / list 中含 DataFrame → 整个 pickle（保证异构结构）
    if isinstance(value, (tuple, list)) and pd is not None:
        if any(isinstance(x, pd.DataFrame) for x in value):
            return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)

    # 3) dataclass → 包装成 {"__type__": "ClassName", "data": {...}}
    # 使用 _wrap_for_serialize 递归处理嵌套 dataclass
    if is_dataclass(value):
        wrapped_data = {}
        for field_name, field_value in value.__dict__.items():
            wrapped_data[field_name] = _wrap_for_serialize(field_value)
        wrapped = {"__type__": type(value).__name__, "data": wrapped_data}
        return json.dumps(wrapped, ensure_ascii=False, default=_json_default).encode("utf-8")

    # 4) tuple / list → JSON（递归处理内部 dataclass，带类型标记）
    if isinstance(value, (list, tuple)):
        normalized = [_wrap_for_serialize(x) for x in value]
        return json.dumps(normalized, ensure_ascii=False, default=_json_default).encode("utf-8")

    # 5) dict → JSON
    if isinstance(value, dict):
        return json.dumps(_to_jsonable(value), ensure_ascii=False, default=_json_default).encode("utf-8")

    # 6) 兜底：JSON
    return json.dumps(_to_jsonable(value), ensure_ascii=False, default=_json_default).encode("utf-8")


def deserialize(blob: bytes, expected_type: Optional[type] = None) -> Any:
    """
    从 bytes 反序列化为 Python 对象。

    策略：
      - 空 bytes → None
      - 试 pickle（DataFrame）
      - 失败则 JSON（可能含 __type__ 标记 → 还原 dataclass）
    """
    if blob is None or blob == b"":
        return None

    # 1. 先试 pickle（DataFrame 走这条路）
    try:
        result = pickle.loads(blob)
        # pickle 返回的结果也需要递归检查 dataclass
        if isinstance(result, (list, tuple)):
            return _deep_restore(result)
        if isinstance(result, dict):
            return _deep_restore(result)
        return result
    except Exception:
        pass

    # 2. 再试 JSON
    try:
        data = json.loads(blob.decode("utf-8"))

        # 3. 检查是否是需要还原的 dataclass
        if isinstance(data, dict) and "__type__" in data:
            return _restore_dataclass(data)

        # 4. 检查 list/tuple/dict 中是否有 dataclass
        return _deep_restore(data)
    except Exception as e:
        logger.warning("缓存反序列化失败: %s", e)
        return None


def _restore_dataclass(data: dict) -> Any:
    """
    根据 __type__ 标记还原 dataclass 实例。

    Args:
        data: {"__type__": "ClassName", "data": {...}}

    Returns:
        对应类型的 dataclass 实例
    """
    type_name = data.get("__type__")
    raw_payload = data.get("data", {})

    # 延迟注册（避免循环导入）
    _auto_register_analytics_classes()

    cls = _DATACLASS_REGISTRY.get(type_name)
    if cls is None:
        logger.warning("未知类型 %s，无法还原 dataclass", type_name)
        return raw_payload  # 降级：返回原始 dict

    try:
        # 递归还原每个字段（处理嵌套的 dataclass）
        # raw_payload 可能是 dict 或 list，都需要递归处理
        payload = _deep_restore(raw_payload)
        return cls(**payload)
    except Exception as e:
        logger.warning("还原 %s 失败: %s，返回原始 dict", type_name, e)
        return raw_payload


def _deep_restore(obj):
    """
    递归还原任何嵌套结构中的 dataclass。

    处理：
    - dict 中的 {"__type__": ..., "data": ...} → dataclass
    - dict 中的普通键值对 → 递归处理值
    - list/tuple 中的元素 → 递归处理
    """
    if isinstance(obj, dict):
        if "__type__" in obj and "data" in obj:
            # 这是一个需要还原的 dataclass
            return _restore_dataclass(obj)
        # 普通 dict，递归处理每个值
        return {k: _deep_restore(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        restored = [_deep_restore(x) for x in obj]
        return tuple(restored) if isinstance(obj, tuple) else restored
    return obj


def _to_jsonable(obj):
    """递归把 dataclass / list / tuple 转 dict。"""
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj


def _wrap_for_serialize(obj):
    """
    递归包装：dataclass → {"__type__": ..., "data": ...}，其他类型保持不变。

    用于 list/tuple 中嵌套 dataclass 的序列化。

    关键：对于 dataclass，递归处理每个字段而不是用 asdict()，
    这样可以保留嵌套 dataclass 的类型标记。
    """
    if is_dataclass(obj):
        # 手动构建包装结构，递归处理每个字段
        wrapped_data = {}
        for field_name, field_value in obj.__dict__.items():
            wrapped_data[field_name] = _wrap_for_serialize(field_value)
        return {"__type__": type(obj).__name__, "data": wrapped_data}
    if isinstance(obj, (list, tuple)):
        return [_wrap_for_serialize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _wrap_for_serialize(v) for k, v in obj.items()}
    return obj


def _json_default(o):
    """json.dumps 兜底序列化器（处理 datetime / numpy 等）。"""
    # datetime
    if hasattr(o, "isoformat"):
        return o.isoformat()
    # numpy
    try:
        import numpy as np
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:
        pass
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


# ──────────────────────────────────────────────
# 4. 底层 get / set（静默失败）
# ──────────────────────────────────────────────

def cache_get(key: str) -> Optional[bytes]:
    """从 Redis 读 bytes。失败返回 None。"""
    client = _get_client()
    if client is None:
        return None
    try:
        return client.get(key)
    except Exception as e:
        logger.debug("Redis GET 失败（已忽略）: %s | key=%s", e, key)
        return None


def cache_set(key: str, value: bytes, ttl: int) -> bool:
    """写 Redis（带 TTL）。失败返回 False。"""
    client = _get_client()
    if client is None:
        return False
    try:
        client.setex(key, ttl, value)
        return True
    except Exception as e:
        logger.debug("Redis SETEX 失败（已忽略）: %s | key=%s", e, key)
        return False


def cache_delete(*keys: str) -> int:
    """删除指定 key。失败返回 0。"""
    client = _get_client()
    if client is None or not keys:
        return 0
    try:
        return int(client.delete(*keys))
    except Exception as e:
        logger.debug("Redis DEL 失败（已忽略）: %s", e)
        return 0


def cache_ping() -> bool:
    """探活。失败返回 False。"""
    client = _get_client()
    if client is None:
        return False
    try:
        return bool(client.ping())
    except Exception:
        return False


# ──────────────────────────────────────────────
# 5. @redis_cache 装饰器
# ──────────────────────────────────────────────

def redis_cache(
    func: Optional[Callable] = None,
    *,
    ttl: Optional[int] = None,
    use_args: bool = True,
):
    """
    Redis 装饰器：自动给函数添加 Redis 缓存。

    用法：
        @redis_cache                              # 用默认 TTL
        def calc_kpi(df_all, df_finished): ...

        @redis_cache(ttl=3600)
        def calc_streak(df): ...

    行为：
      1. 拼 cache_key = lottery:v{db_mtime}:{func_name}:{args_hash}
         - 如果 Redis 可用但 db_mtime 拿不到 → 不缓存（走原计算）
      2. 查 Redis：命中 → 反序列化 → 返回
      3. 未命中 → 调原函数 → 序列化 → 写 Redis（异步语义：失败也不影响返回值）

    Args:
        func:      被装饰的函数（@redis_cache 无括号时由解释器传入）
        ttl:       缓存过期时间（秒）；None 时按函数名猜默认 TTL
        use_args:  是否把 args 计入 key（默认 True）

    Notes:
      - db_mtime 从环境变量 LOTTERY_DB_MTIME 或文件 mtime 获取；
        若文件不存在则跳过缓存（不影响功能）。
      - 任何异常都被吞掉，函数照常执行。
    """
    def _resolve_ttl(name: str) -> int:
        if ttl is not None:
            return ttl
        # 按函数名猜默认 TTL
        if "tracking" in name:
            return REDIS_CONFIG.ttl_tracking
        if name in ("calc_kpi", "calc_pan_stats"):
            return REDIS_CONFIG.ttl_base
        return REDIS_CONFIG.ttl_filtered

    def _get_db_mtime() -> Optional[float]:
        """从 env 或 SQLite 文件读 mtime。"""
        import os
        from config import DB_CONFIG
        # 优先环境变量（避免每次都 stat）
        env_val = os.getenv("LOTTERY_DB_MTIME")
        if env_val:
            try:
                return float(env_val)
            except ValueError:
                pass
        try:
            return float(os.path.getmtime(DB_CONFIG.path))
        except (OSError, FileNotFoundError, ValueError):
            return None

    def decorator(fn: Callable) -> Callable:
        fn_name = fn.__name__
        default_ttl = _resolve_ttl(fn_name)
        # 函数返回类型的注解（用于检测旧缓存）
        return_type = fn.__annotations__.get("return")

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            # 1. 拿数据版本
            db_mtime = _get_db_mtime()
            if db_mtime is None:
                # 拿不到版本 → 不缓存，直接算
                return fn(*args, **kwargs)

            # 2. 拼 key
            if use_args and (args or kwargs):
                sig = make_filter_signature(args, kwargs)
            else:
                sig = ""
            key = make_versioned_key(fn_name, db_mtime, sig)

            # 3. 查 Redis
            blob = cache_get(key)
            if blob is not None:
                cached = deserialize(blob)
                if cached is not None:
                    # 类型检查：如果是 dataclass 注解但返回了 dict，说明是旧格式缓存
                    if (
                        return_type is not None
                        and is_dataclass(return_type)
                        and isinstance(cached, dict)
                        and "__type__" not in cached
                    ):
                        logger.warning(
                            "⚠️ 检测到旧格式缓存（无 __type__ 标记），已删除: %s",
                            key,
                        )
                        cache_delete(key)
                        # 重新计算
                    else:
                        logger.debug("✅ Redis 命中: %s", key)
                        return cached

            # 4. 未命中 → 算
            result = fn(*args, **kwargs)

            # 5. 写 Redis（失败不影响返回值）
            try:
                payload = serialize(result)
                cache_set(key, payload, default_ttl)
            except Exception as e:
                logger.debug("序列化/写入 Redis 失败（已忽略）: %s | key=%s", e, key)

            return result

        return wrapper

    # 兼容两种用法：
    #   @redis_cache                  → func 是函数
    #   @redis_cache(ttl=3600)        → func 是 None
    if callable(func):
        return decorator(func)
    return decorator


# ──────────────────────────────────────────────
# 6. 批量失效（按版本号）
# ──────────────────────────────────────────────

def invalidate_by_version(db_mtime: float) -> int:
    """
    删除指定版本的所有 key。

    实现：scan + delete（生产环境安全，keys 命令会阻塞 Redis）。
    """
    client = _get_client()
    if client is None:
        return 0

    pattern = f"{REDIS_CONFIG.key_prefix}:v{db_mtime:.2f}:*"
    deleted = 0
    try:
        for key in client.scan_iter(match=pattern, count=500):
            client.delete(key)
            deleted += 1
        logger.info("已失效版本 v%s 的 %d 个缓存 key", f"{db_mtime:.2f}", deleted)
    except Exception as e:
        logger.warning("批量失效失败: %s", e)
    return deleted


def cache_stats() -> dict:
    """
    返回当前 Redis 缓存统计（供调试 / 健康检查）。

    Returns:
        dict with keys: enabled, available, prefix, sample_keys (list)
    """
    client = _get_client()
    info = {
        "enabled":    REDIS_CONFIG.enabled,
        "available":  client is not None,
        "host":       REDIS_CONFIG.host,
        "port":       REDIS_CONFIG.port,
        "db":         REDIS_CONFIG.db,
        "prefix":     REDIS_CONFIG.key_prefix,
        "sample_keys": [],
    }
    if client is not None:
        try:
            pattern = f"{REDIS_CONFIG.key_prefix}:*"
            sample = []
            for key in client.scan_iter(match=pattern, count=20):
                sample.append(key.decode("utf-8", errors="replace") if isinstance(key, bytes) else key)
                if len(sample) >= 10:
                    break
            info["sample_keys"] = sample
        except Exception as e:
            info["error"] = str(e)
    return info
