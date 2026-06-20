"""
crawler/config.py
=================
数据采集层专属配置。

整合说明：
  - API 地址、请求头、参数均来自真实 crawler.py
  - 数据库连接方式改为 SQLAlchemy（来自真实 database.py）
  - 支持环境变量覆盖，便于多环境部署（12-Factor App）
"""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ApiConfig:
    """竞彩官网 API 连接配置。"""

    # ── 接口地址（来自真实 crawler.py）──
    base_url: str = (
        "https://webapi.sporttery.cn/gateway/uniform/football"
        "/getUniformMatchResultV1.qry"
    )

    # ── 请求约束 ──
    max_date_range_days: int = 30      # 官方接口限制：单次查询不超过 30 天
    page_size:           int = 30      # 官方接口每页最大条数
    page_request_delay:  float = 1.0   # 翻页间隔（秒），避免触发频控

    # ── 请求头（完整复刻浏览器指纹，来自真实 crawler.py）──
    @property
    def headers(self) -> dict:
        return {
            "accept":              "application/json, text/javascript, */*; q=0.01",
            "accept-language":     "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "cache-control":       "no-cache",
            "origin":              "https://www.sporttery.cn",
            "pragma":              "no-cache",
            "priority":            "u=1, i",
            "referer":             "https://www.sporttery.cn/",
            "sec-ch-ua":           '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile":    "?0",
            "sec-ch-ua-platform":  '"Windows"',
            "sec-fetch-dest":      "empty",
            "sec-fetch-mode":      "cors",
            "sec-fetch-site":      "same-site",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/148.0.0.0 Safari/537.36"
            ),
        }

    # ── 固定请求参数（来自真实 crawler.py）──
    @property
    def fixed_params(self) -> dict:
        return {
            "leagueId":  "",
            "pageSize":  str(self.page_size),
            "isFix":     "0",
            "matchPage": "1",
            "pcOrWap":   "1",
        }

    # ── 超时与重试 ──
    timeout_seconds: int   = 15
    max_retries:     int   = 3
    retry_backoff:   float = 2.0   # 指数退避基数（秒）


@dataclass(frozen=True)
class SchedulerConfig:
    """定时调度配置。"""
    run_hour:   int   = field(default_factory=lambda: int(os.getenv("CRAWLER_RUN_HOUR",   "11")))
    run_minute: int   = field(default_factory=lambda: int(os.getenv("CRAWLER_RUN_MINUTE", "0")))
    # 增量抓取往前追溯天数（覆盖近几天可能补录的数据）
    lookback_days: int = field(default_factory=lambda: int(os.getenv("CRAWLER_LOOKBACK_DAYS", "3")))
    run_on_start:  bool = field(default_factory=lambda: os.getenv("CRAWLER_RUN_ON_START", "true").lower() == "true")
    log_path: str = field(default_factory=lambda: os.getenv("CRAWLER_LOG_PATH", "logs/crawler.log"))


@dataclass(frozen=True)
class StorageConfig:
    """数据库连接配置（SQLAlchemy，来自真实 database.py）。"""
    db_url: str = field(
        default_factory=lambda: os.getenv(
            "LOTTERY_DB_URL",
            "sqlite:///data/lottery_data.db"
        )
    )
    echo_sql: bool = False   # 调试时可设为 True，打印所有 SQL


# ── 单例导出 ──
API_CONFIG       = ApiConfig()
SCHEDULER_CONFIG = SchedulerConfig()
STORAGE_CONFIG   = StorageConfig()
