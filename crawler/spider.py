"""
crawler/spider.py
=================
HTTP 请求层

整合说明：
  - API 地址、请求头、翻页逻辑完整来自真实 crawler.py
  - 新增：指数退避重试、自定义异常类型、日志替代 print
  - 保留：30天限制校验、默认取昨天~今天的日期逻辑、翻页间隔 1 秒
  - 移除：裸 except（改为具体异常类型）

职责：只负责"从 API 拿到原始 JSON 列表"，不做解析，不写数据库。
"""

import logging
import time
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional

import requests

from crawler.config import API_CONFIG
from crawler.models import MatchData

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 自定义异常
# ──────────────────────────────────────────────

class DateRangeError(ValueError):
    """日期范围不合法（超过 30 天或起止日期颠倒）。"""


class ApiResponseError(Exception):
    """API 返回结构异常，无法找到预期字段。"""


class NetworkError(Exception):
    """网络连接失败（超时、连接拒绝等）。"""


# ──────────────────────────────────────────────
# 日期工具
# ──────────────────────────────────────────────

def _resolve_date_range(
    begin: Optional[str],
    end: Optional[str],
) -> tuple[str, str]:
    """
    确定最终使用的日期范围。
    - 两个参数都传入时做合法性校验（来自真实 crawler.py 逻辑）
    - 都不传时默认昨天 → 今天

    Returns:
        (begin_str, end_str)，格式 "YYYY-MM-DD"

    Raises:
        DateRangeError: 超过 30 天或起止颠倒
    """
    if begin and end:
        d_begin = datetime.strptime(begin, "%Y-%m-%d").date()
        d_end   = datetime.strptime(end, "%Y-%m-%d").date()

        if d_begin > d_end:
            raise DateRangeError(
                f"matchBeginDate ({begin}) 不能晚于 matchEndDate ({end})，请调整日期范围。"
            )
        days_diff = (d_end - d_begin).days
        if days_diff > API_CONFIG.max_date_range_days:
            raise DateRangeError("日期跨度过大。")
        return begin, end

    # 默认：昨天 → 今天
    today     = date.today()
    yesterday = today - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


# ──────────────────────────────────────────────
# 单页请求（带重试）
# ──────────────────────────────────────────────

def _fetch_page(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    请求单页数据，失败时按指数退避重试。

    Returns:
        API response["value"] 字段的内容

    Raises:
        NetworkError:      网络连通性问题
        ApiResponseError:  响应结构非预期
    """
    last_exc = None

    for attempt in range(1, API_CONFIG.max_retries + 1):
        try:
            resp = requests.get(
                API_CONFIG.base_url,
                params=params,
                headers=API_CONFIG.headers,
                timeout=API_CONFIG.timeout_seconds,
                proxies={"http": None, "https": None},
            )
            resp.raise_for_status()
            payload = resp.json()

            # 校验响应结构
            if "value" not in payload or "matchResult" not in payload["value"]:
                raise ApiResponseError(
                    f"接口返回数据格式异常，无法找到 matchResult 字段。"
                    f"原始响应前 200 字符：{str(payload)[:200]}"
                )

            return payload["value"]

        except requests.exceptions.Timeout as e:
            last_exc = NetworkError(f"请求超时（第 {attempt} 次）: {e}")
            logger.warning("请求超时，第 %d/%d 次重试，等待 %.1f 秒...",
                           attempt, API_CONFIG.max_retries,
                           API_CONFIG.retry_backoff ** attempt)
            time.sleep(API_CONFIG.retry_backoff ** attempt)

        except requests.exceptions.ConnectionError as e:
            last_exc = NetworkError(f"连接失败（第 {attempt} 次）: {e}")
            logger.warning("连接失败，第 %d/%d 次重试...", attempt, API_CONFIG.max_retries)
            time.sleep(API_CONFIG.retry_backoff ** attempt)

        except requests.exceptions.HTTPError as e:
            # 4xx 不重试
            if resp.status_code < 500:
                raise ApiResponseError(f"HTTP {resp.status_code}: {e}")
            last_exc = NetworkError(f"服务端错误 {resp.status_code}，第 {attempt} 次重试...")
            time.sleep(API_CONFIG.retry_backoff ** attempt)

        except ApiResponseError:
            raise   # 结构异常不重试

    raise last_exc or NetworkError("未知网络错误")


# ──────────────────────────────────────────────
# 核心抓取函数（自动翻页 + 解析）
# ──────────────────────────────────────────────

def fetch_matches(
    match_begin_date: Optional[str] = None,
    match_end_date:   Optional[str] = None,
) -> List[MatchData]:
    """
    抓取指定日期范围内的所有比赛，自动翻页，返回 MatchData 列表。

    整合了真实 crawler.py 的完整业务逻辑：
      - 日期范围校验（最多 30 天）
      - 自动翻页（pageNo 递增，翻页间隔 1 秒）
      - 使用 MatchData.from_json 解析，解析失败的条目自动跳过

    Args:
        match_begin_date: 开始日期 "YYYY-MM-DD"，默认昨天
        match_end_date:   结束日期 "YYYY-MM-DD"，默认今天

    Returns:
        解析成功的 MatchData 列表

    Raises:
        DateRangeError:    日期范围不合法
        NetworkError:      网络连通性问题
        ApiResponseError:  API 响应结构异常
    """
    begin, end = _resolve_date_range(match_begin_date, match_end_date)
    logger.info("开始抓取比赛数据 %s → %s", begin, end)

    # 构造基础请求参数（来自真实 crawler.py）
    params = {
        **API_CONFIG.fixed_params,
        "matchBeginDate": begin,
        "matchEndDate":   end,
        "pageNo":         "1",
    }

    # ── 第一页 ──
    first_page = _fetch_page(params)
    raw_records: List[dict] = list(first_page["matchResult"])
    total_pages: int = int(first_page.get("pages", 1))

    logger.info("总页数: %d，第 1 页数据: %d 条", total_pages, len(raw_records))

    # ── 后续页 ──
    for page in range(2, total_pages + 1):
        logger.info("正在抓取第 %d / %d 页...", page, total_pages)
        time.sleep(API_CONFIG.page_request_delay)   # 避免频控封 IP
        params["pageNo"] = str(page)
        page_data = _fetch_page(params)
        raw_records.extend(page_data["matchResult"])

    logger.info("全部页抓取完成，共 %d 条原始记录", len(raw_records))

    # ── 解析 ──
    parsed: List[MatchData] = []
    failed = 0
    for item in raw_records:
        match_obj = MatchData.from_json(item)
        if match_obj:
            parsed.append(match_obj)
        else:
            failed += 1

    if failed:
        logger.warning("解析失败 %d 条（已跳过），成功 %d 条", failed, len(parsed))
    else:
        logger.info("全部解析成功，共 %d 条", len(parsed))

    return parsed
