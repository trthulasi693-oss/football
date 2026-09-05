"""
crawler/scheduler.py
====================
调度层：定时触发完整采集流程。

整合说明：
  - 流程：spider.fetch_matches → storage.save_matches
  - 增量逻辑：从数据库最新日期往前 lookback_days 天开始抓，避免每次全量
  - 保留原框架的定时调度、命令行入口、日志配置

使用方式：
  python -m crawler.scheduler           # 启动每日定时任务（长驻进程）
  python -m crawler.scheduler --once    # 只运行一次（调试）
  python -m crawler.scheduler --once --from 2026-06-01 --to 2026-06-30
"""

import argparse
import logging
import logging.handlers
import os
import sys
from datetime import date, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from crawler.config import SCHEDULER_CONFIG
from crawler.spider import fetch_matches, DateRangeError, NetworkError, ApiResponseError
from crawler.storage import save_matches, get_latest_match_date

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 日志配置
# ──────────────────────────────────────────────

def setup_logging() -> None:
    log_path = SCHEDULER_CONFIG.log_path
    os.makedirs(os.path.dirname(log_path) if os.path.dirname(log_path) else ".", exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    fh = logging.handlers.TimedRotatingFileHandler(
        log_path, when="midnight", backupCount=30, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)


# ──────────────────────────────────────────────
# 单次采集任务
# ──────────────────────────────────────────────

def run_crawl_job(
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
) -> None:
    """
    执行一次完整的采集流程：spider → storage。

    日期范围确定规则：
      - 手动指定时直接使用（注意：官方接口限制单次不超过 30 天）
      - 不指定时：从数据库最新日期往前 lookback_days 天 → 今天（增量模式）
    """
    today = date.today().strftime("%Y-%m-%d")

    if date_to is None:
        date_to = today

    if date_from is None:
        latest = get_latest_match_date()
        date_from = (
            date.fromisoformat(latest) - timedelta(days=SCHEDULER_CONFIG.lookback_days)
        ).strftime("%Y-%m-%d")

    logger.info("═══ 开始采集任务 %s → %s ═══", date_from, date_to)

    # ── Step 1: 抓取 ──
    try:
        match_list = fetch_matches(date_from, date_to)
        logger.info("Step 1 完成：抓取 %d 条记录", len(match_list))
    except DateRangeError as e:
        logger.error("日期范围错误，任务终止: %s", e)
        return
    except NetworkError as e:
        logger.error("网络错误，任务终止: %s", e)
        return
    except ApiResponseError as e:
        logger.error("API 响应异常，任务终止: %s", e)
        return
    except Exception as e:
        logger.exception("Step 1 未知错误: %s", e)
        return

    if not match_list:
        logger.info("本次无新数据，任务结束。")
        return

    # ── Step 2: 存储 ──
    try:
        inserted, updated, skipped = save_matches(match_list)
        logger.info(
            "Step 2 完成：新增 %d，更新 %d，跳过 %d",
            inserted, updated, skipped,
        )
    except Exception as e:
        logger.exception("Step 2 存储失败: %s", e)
        return

    logger.info("═══ 采集任务完成 ═══")


# ──────────────────────────────────────────────
# 调度器启动
# ──────────────────────────────────────────────

def start_scheduler() -> None:
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        func=run_crawl_job,
        trigger=CronTrigger(
            hour=SCHEDULER_CONFIG.run_hour,
            minute=SCHEDULER_CONFIG.run_minute,
        ),
        id="daily_crawl",
        name="每日竞彩数据采集",
        misfire_grace_time=3600,
        coalesce=True,
    )

    logger.info(
        "调度器启动，每天 %02d:%02d 自动采集（时区：Asia/Shanghai）",
        SCHEDULER_CONFIG.run_hour,
        SCHEDULER_CONFIG.run_minute,
    )

    if SCHEDULER_CONFIG.run_on_start:
        logger.info("run_on_start=True，立即执行一次...")
        run_crawl_job()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("调度器已停止。")


# ──────────────────────────────────────────────
# 命令行入口
# ──────────────────────────────────────────────

def main() -> None:
    setup_logging()

    parser = argparse.ArgumentParser(description="竞彩数据采集调度器")
    parser.add_argument("--once", action="store_true", help="只运行一次后退出")
    parser.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD", help="起始日期")
    parser.add_argument("--to",   dest="date_to",   metavar="YYYY-MM-DD", help="结束日期（默认今天）")
    args = parser.parse_args()

    if args.once:
        run_crawl_job(date_from=args.date_from, date_to=args.date_to)
    else:
        start_scheduler()


if __name__ == "__main__":
    main()
