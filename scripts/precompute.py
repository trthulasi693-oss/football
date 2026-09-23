"""
scripts/precompute.py
=====================
预热入口：爬虫完成后调用，把"常用视图"的分析结果提前算好写入 Redis。

使用方式：
  python scripts/precompute.py                  # 默认：用 7/30/90/全部 四档日期窗口预热
  python scripts/precompute.py --windows 7 30   # 自定义窗口（天）

为什么需要预热？
  Streamlit 用户首次打开页面时拖滑块 → 第一次过滤的 DataFrame 之前没算过 → 必须现场算（2-3 秒）。
  通过预热脚本在爬虫完成后提前算好"最常见的日期窗口"，
  让用户首次访问也能命中 Redis（< 100ms 返回）。
"""

import argparse
import logging
import sys
import time
from datetime import timedelta
from pathlib import Path

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from config import DB_CONFIG  # noqa: E402
from data_service import (  # noqa: E402
    load_and_process_data,
    get_finished_df,
    filter_by_date,
    apply_001_filter,
)
from analytics import (  # noqa: E402
    calc_kpi,
    calc_pan_stats,
    calc_all_streaks,
    calc_goal_stats,
    calc_underdog_trend,
    calc_tracking_data,
)
from cache import cache_stats, is_redis_available  # noqa: E402

logger = logging.getLogger(__name__)


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _precompute_one_window(df_all, df_finished, start_date, end_date, label):
    """对单个日期窗口预热所有指标。"""
    logger.info("  → 窗口 [%s]：%s → %s", label, start_date, end_date)
    df_chart = filter_by_date(df_finished, (start_date, end_date))

    # 1. KPI（独立 df_all / df_finished，不依赖日期）
    calc_kpi(df_all, df_finished)

    # 2. 盘路
    if not df_chart.empty:
        calc_pan_stats(df_chart)

        # 3. 连路（大盘 / 001 / 单关 三个维度）
        calc_all_streaks(df_chart)

        # 4. 进球
        calc_goal_stats(df_chart)

        # 5. 下盘趋势（三种过滤）
        for f in ("全部赛事", "仅001场次", "排除001场次"):
            df_f = apply_001_filter(df_chart, f)
            calc_underdog_trend(df_f)

        # 6. 逐场追踪（三种过滤）
        for f in ("全部赛事", "仅001场次", "排除001场次"):
            df_f = apply_001_filter(df_chart, f)
            calc_tracking_data(df_f)

    logger.info("    ✓ 窗口 [%s] 预热完成", label)


def run_precompute(windows: list = None):
    """
    执行预热。

    Args:
        windows: 日期窗口列表（天数）。None → 默认 [7, 30, 90, 全部]
    """
    _setup_logging()

    if windows is None:
        windows = [7, 30, 90, "all"]

    logger.info("═══ Redis 预热任务开始 ═══")

    # 健康检查
    stats = cache_stats()
    logger.info("Redis 状态：%s", "可用" if stats["available"] else "不可用（将跳过预热）")
    if not stats["available"]:
        logger.warning("Redis 不可用，预热将失败。检查连接或 LOTTERY_REDIS_ENABLED=0")
        return False

    # 加载数据
    logger.info("加载数据库：%s", DB_CONFIG.path)
    import os
    try:
        mtime = os.path.getmtime(DB_CONFIG.path)
    except OSError:
        mtime = 0.0
    df_all = load_and_process_data(mtime)
    if df_all.empty:
        logger.error("数据库为空，无需预热")
        return False

    df_finished = get_finished_df(df_all)
    if df_finished.empty:
        logger.error("暂无已完赛数据，无需预热")
        return False

    max_date = df_finished["match_date_dt"].max()
    min_date = df_finished["match_date_dt"].min()
    logger.info("数据时间范围：%s → %s (共 %d 条)",
                min_date, max_date, len(df_finished))

    started = time.time()
    cached_count = 0

    for w in windows:
        if w == "all":
            start_date = min_date
            label = "全部历史"
        else:
            start_date = max_date - timedelta(days=int(w) - 1)
            label = f"近{w}天"

        try:
            _precompute_one_window(df_all, df_finished, start_date, max_date, label)
            cached_count += 1
        except Exception as e:
            logger.exception("窗口 [%s] 预热失败: %s", label, e)

    elapsed = time.time() - started
    logger.info("═══ 预热完成：%d 个窗口，耗时 %.1fs ═══", cached_count, elapsed)
    return True


def main():
    parser = argparse.ArgumentParser(description="Redis 缓存预热")
    parser.add_argument(
        "--windows",
        nargs="+",
        help="日期窗口（天数），空格分隔。例如: --windows 7 30 90 all",
    )
    args = parser.parse_args()

    windows = args.windows if args.windows else None
    success = run_precompute(windows)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
