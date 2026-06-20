"""
analytics.py
============
业务分析层（Business Logic Layer）。

职责：
  - 接收已过滤的 DataFrame，输出业务指标（纯计算，无 UI 代码）
  - KPI 汇总
  - 连路（Streak）算法（去重后单一实现，支持任意过滤条件）
  - 进球数统计
  - 下盘趋势聚合

架构原则：
  - 此层不依赖 Streamlit，所有函数均为纯 Python，可独立测试
  - 返回值为结构化 dataclass，不返回裸 dict，防止 KeyError
  - 依赖 data_service 提供的数据集，不直接访问数据库
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import pandas as pd

from config import DOMAIN

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 1. 返回值数据结构
# ──────────────────────────────────────────────

@dataclass
class KpiResult:
    """KPI 汇总指标。"""
    total_matches:        int   = 0
    count_001:            int   = 0
    global_home_win_rate: float = 0.0
    home_win_rate_001:    float = 0.0
    delta_001:            float = 0.0  # 001场次主胜率 - 大盘主胜率


@dataclass
class StreakRecord:
    """单条连路记录。"""
    result_label: str = ""
    count:        int = 0
    start_date:   str = ""
    end_date:     str = ""


@dataclass
class StreakResult:
    """某一过滤范围内的最大连路结果。"""
    label:        str                    = ""  # 标签，如 "大盘全部" / "001场次"
    max_underdog: Optional[StreakRecord] = None
    max_favorite: Optional[StreakRecord] = None


@dataclass
class PanStats:
    """盘路核心统计。"""
    total:          int   = 0
    underdog_count: int   = 0
    favorite_count: int   = 0
    flat_count:     int   = 0
    underdog_rate:  float = 0.0
    favorite_rate:  float = 0.0
    flat_rate:      float = 0.0


@dataclass
class GoalStats:
    """进球数统计。"""
    avg_goals:  float     = 0.0
    top_scores: List[str] = field(default_factory=list)  # ["1:0 (12场)", ...]


@dataclass
class UnderdogTrendPoint:
    """单月下盘率数据点。"""
    month: str
    rate:  float


@dataclass
class TrackingStats:
    """逐场追踪统计摘要。"""
    total:         int = 0
    underdog_wins: int = 0
    net_profit:    int = 0


@dataclass
class FullAnalyticsResult:
    """一次完整分析的所有指标，供视图层一次性取用。"""
    kpi:          KpiResult
    pan_stats:    PanStats
    streaks:      List[StreakResult]       # [大盘, 001, 单关]
    goal_stats:   GoalStats
    trend_points: List[UnderdogTrendPoint]


# ──────────────────────────────────────────────
# 2. KPI 计算
# ──────────────────────────────────────────────

def calc_kpi(df_all: pd.DataFrame, df_finished: pd.DataFrame) -> KpiResult:
    """
    计算顶部 KPI 卡片所需指标。

    Args:
        df_all:      完整数据集（含未完赛）
        df_finished: 已完赛数据集

    Returns:
        KpiResult
    """
    result = KpiResult()
    result.total_matches = len(df_all)
    result.count_001 = int((df_all["is_001"] == DOMAIN.label_001).sum())

    if df_finished.empty:
        return result

    total_f = len(df_finished)
    home_wins = int((df_finished["赛果"] == "主胜 (H)").sum())
    result.global_home_win_rate = home_wins / total_f * 100

    df_001 = df_finished[df_finished["is_001"] == DOMAIN.label_001]
    if not df_001.empty:
        result.home_win_rate_001 = (
            int((df_001["赛果"] == "主胜 (H)").sum()) / len(df_001) * 100
        )

    result.delta_001 = result.home_win_rate_001 - result.global_home_win_rate
    return result


# ──────────────────────────────────────────────
# 3. 盘路统计
# ──────────────────────────────────────────────

def calc_pan_stats(df: pd.DataFrame) -> PanStats:
    """
    计算有效盘路的上/下/平手分布。

    Args:
        df: 已过滤的 DataFrame（含 `盘路结果` 列）
    """
    df_valid = df[df["盘路结果"].isin(DOMAIN.valid_pan_results)]
    total = len(df_valid)

    if total == 0:
        return PanStats()

    u    = int((df_valid["盘路结果"] == DOMAIN.label_underdog).sum())
    fav  = int((df_valid["盘路结果"] == DOMAIN.label_favorite).sum())
    flat = int((df_valid["盘路结果"] == DOMAIN.label_flat).sum())

    return PanStats(
        total=total,
        underdog_count=u,
        favorite_count=fav,
        flat_count=flat,
        underdog_rate=u    / total * 100,
        favorite_rate=fav  / total * 100,
        flat_rate=flat     / total * 100,
    )


# ──────────────────────────────────────────────
# 4. 连路算法（核心去重实现）
# ──────────────────────────────────────────────

def _calc_max_streak_for_label(
    groups: pd.DataFrame, result_label: str
) -> Optional[StreakRecord]:
    """
    从已聚合的 streak_groups 中找出指定标签的最大连路。
    私有辅助函数。
    """
    sub = groups[groups["盘路结果"] == result_label]
    if sub.empty:
        return None
    row = sub.sort_values("连续次数", ascending=False).iloc[0]
    return StreakRecord(
        result_label=result_label,
        count=int(row["连续次数"]),
        start_date=str(row["开始日期"]),
        end_date=str(row["结束日期"]),
    )


def calc_streak(df: pd.DataFrame, label: str = "") -> StreakResult:
    """
    计算给定 DataFrame 内的最大连路（连续下盘 & 连续上盘）。

    这是三处重复连路算法合并后的唯一实现。
    调用方通过提前 filter 好的 df 传入，实现大盘/001/单关 三种场景复用。

    Args:
        df:    已排序（按 match_date 升序）、已过滤的 DataFrame
        label: 可读标签，用于 StreakResult.label

    Returns:
        StreakResult（max_underdog / max_favorite 任一可能为 None）
    """
    result = StreakResult(label=label)

    df_pure = df[df["盘路结果"].isin(DOMAIN.streak_results)].copy()
    if df_pure.empty:
        return result

    # 标记连续段 ID
    df_pure["streak_id"] = (
        df_pure["盘路结果"] != df_pure["盘路结果"].shift()
    ).cumsum()

    groups = df_pure.groupby("streak_id").agg(
        盘路结果=("盘路结果", "first"),
        连续次数=("盘路结果", "size"),
        开始日期=("match_date", "min"),
        结束日期=("match_date", "max"),
    )

    result.max_underdog = _calc_max_streak_for_label(groups, DOMAIN.label_underdog)
    result.max_favorite = _calc_max_streak_for_label(groups, DOMAIN.label_favorite)
    return result


def calc_all_streaks(df_chart_source: pd.DataFrame) -> List[StreakResult]:
    """
    一次性计算三个维度的连路：大盘、001场次、单关场次。

    Args:
        df_chart_source: 当前日期范围内已完赛 DataFrame

    Returns:
        [大盘 StreakResult, 001 StreakResult, 单关 StreakResult]
    """
    df_sorted = df_chart_source.sort_values("match_date", ascending=True).reset_index(drop=True)

    return [
        calc_streak(df_sorted, label="大盘全部赛事"),
        calc_streak(
            df_sorted[df_sorted["is_001"] == DOMAIN.label_001].copy(),
            label="001场次",
        ),
        calc_streak(
            df_sorted[df_sorted["is_单关"] == DOMAIN.label_single].copy(),
            label="单关场次",
        ),
    ]


# ──────────────────────────────────────────────
# 5. 进球数统计
# ──────────────────────────────────────────────

def calc_goal_stats(df: pd.DataFrame, top_n: int = 3) -> GoalStats:
    """
    计算场均进球数与高频比分。

    Args:
        df:    数据源 DataFrame
        top_n: 展示高频比分的条数
    """
    df_valid_goals = df.dropna(subset=["总进球数"])
    avg = float(df_valid_goals["总进球数"].mean()) if not df_valid_goals.empty else 0.0

    top_scores_series = df["sections_no999"].value_counts().head(top_n)
    top_scores = [f"{k} ({v}场)" for k, v in top_scores_series.items()]

    return GoalStats(avg_goals=avg, top_scores=top_scores)


# ──────────────────────────────────────────────
# 6. 下盘趋势聚合
# ──────────────────────────────────────────────

def calc_underdog_trend(df: pd.DataFrame) -> List[UnderdogTrendPoint]:
    """
    按月份聚合下盘打出率，用于趋势折线图。

    Args:
        df: 含 `比赛月份` 和 `盘路结果` 列的 DataFrame

    Returns:
        按月份排序的 UnderdogTrendPoint 列表
    """
    df_streak = df[df["盘路结果"].isin(DOMAIN.streak_results)]
    if df_streak.empty:
        return []

    # 【优化】使用 transform + groupby 代替 apply(include_groups=False)，
    #         兼容 pandas 2.x，消除 DeprecationWarning
    monthly = df_streak.groupby("比赛月份")["盘路结果"].agg(
        total="count",
        underdog=lambda s: (s == DOMAIN.label_underdog).sum(),
    ).reset_index()
    monthly.columns = ["月份", "total", "underdog"]
    monthly["下盘率"] = monthly["underdog"] / monthly["total"] * 100
    monthly = monthly.sort_values("月份")

    return [
        UnderdogTrendPoint(month=row["月份"], rate=float(row["下盘率"]))
        for _, row in monthly.iterrows()
    ]


# ──────────────────────────────────────────────
# 7. 逐场追踪数据
# ──────────────────────────────────────────────

def calc_tracking_data(df: pd.DataFrame) -> Tuple[pd.DataFrame, TrackingStats]:
    """
    生成逐场追踪所需数据：outcome_val（+1/-1）和累计净胜（cum_net）。

    Args:
        df: 含 `盘路结果` 列的 DataFrame

    Returns:
        (处理后的 DataFrame, TrackingStats)
    """
    df_track = (
        df[df["盘路结果"].isin(DOMAIN.streak_results)]
        .copy()
        .sort_values("match_date", ascending=True)
        .reset_index(drop=True)
    )

    if df_track.empty:
        return df_track, TrackingStats()

    df_track["outcome_val"] = df_track["盘路结果"].apply(
        lambda x: 1 if x == DOMAIN.label_underdog else -1
    )
    df_track["cum_net"] = df_track["outcome_val"].cumsum()

    stats = TrackingStats(
        total=len(df_track),
        underdog_wins=int((df_track["outcome_val"] == 1).sum()),
        net_profit=int(df_track["cum_net"].iloc[-1]),
    )
    return df_track, stats
