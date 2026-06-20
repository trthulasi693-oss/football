"""
ui/components.py
================
可复用 Streamlit UI 组件库。

职责：
  - 将重复出现的 UI 片段封装为函数
  - 组件只负责渲染，不做数据计算
  - 所有数据通过参数传入，无副作用

架构原则：
  - 单一职责：每个组件只渲染一种 UI 片段
  - 无状态：组件不持有状态，状态由 app.py 统一管理
"""

from typing import Optional, List

import streamlit as st

from analytics import (
    KpiResult, PanStats, StreakResult, StreakRecord, GoalStats
)
from config import APP_CONFIG


# ──────────────────────────────────────────────
# 1. KPI 卡片行
# ──────────────────────────────────────────────

def render_kpi_row(kpi: KpiResult) -> None:
    """渲染顶部四格 KPI 卡片。"""
    col1, col2, col3, col4 = st.columns(4)
    col1.metric(label="数据库总场次",   value=f"{kpi.total_matches} 场")
    col2.metric(label="标记为001的场次", value=f"{kpi.count_001} 场")
    col3.metric(label="大盘平均主胜率",  value=f"{kpi.global_home_win_rate:.1f}%")
    col4.metric(
        label="001场次主胜率",
        value=f"{kpi.home_win_rate_001:.1f}%",
        delta=f"{kpi.delta_001:.1f}%",
        delta_color="inverse",
    )


# ──────────────────────────────────────────────
# 2. 盘路统计 KPI 行
# ──────────────────────────────────────────────

def render_pan_stats_row(stats: PanStats) -> None:
    """渲染盘路统计的四格指标。"""
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("区间有效样本",  f"{stats.total} 场")
    m2.metric("区间总下盘率",  f"{stats.underdog_rate:.1f}%")
    m3.metric("区间总上盘率",  f"{stats.favorite_rate:.1f}%")
    m4.metric("区间平手盘占比", f"{stats.flat_rate:.1f}%")


# ──────────────────────────────────────────────
# 3. 连路卡片（单个维度）
# ──────────────────────────────────────────────

def _render_streak_card(
    record: Optional[StreakRecord],
    is_underdog: bool,
    empty_text: str,
) -> None:
    """渲染单条连路记录（下盘或上盘）。"""
    if record is None:
        st.text(empty_text)
        return

    label = "下盘" if is_underdog else "上盘"
    count_str = f"**{record.count} 连场**"
    date_str = f"📅 `{record.start_date}` 至 `{record.end_date}`"

    if is_underdog:
        st.error(f"最大【连续{label}】: {count_str}")
    else:
        st.success(f"最大【连续{label}】: {count_str}")
    st.caption(date_str)


def render_streak_section(streak_result: StreakResult) -> None:
    """
    渲染单个维度（如大盘/001/单关）的连路极值展示。
    包含左右两列：左=下盘，右=上盘。
    """
    col_l, col_r = st.columns(2)
    with col_l:
        _render_streak_card(
            streak_result.max_underdog,
            is_underdog=True,
            empty_text=f"{streak_result.label}：无连续下盘数据",
        )
    with col_r:
        _render_streak_card(
            streak_result.max_favorite,
            is_underdog=False,
            empty_text=f"{streak_result.label}：无连续上盘数据",
        )


# ──────────────────────────────────────────────
# 4. 极端连路 Expander（汇总三个维度）
# ──────────────────────────────────────────────

def render_streak_expander(
    streak_results: List[StreakResult],
    goal_stats: GoalStats,
) -> None:
    """
    渲染可折叠的极端连路追踪区域。

    Args:
        streak_results: [大盘, 001, 单关] 三个 StreakResult
        goal_stats:     进球数统计
    """
    with st.expander("🔍 点击展开：查看该时间段内极端连路记录与辅助投注信息", expanded=False):
        st.markdown("##### 📈 该区间连击路单峰值追踪")

        section_labels = ["🌍 大盘全部赛事极限", "🎯 001 场次专属极限", "⚔️ 单关 场次专属极限"]
        for label_text, result in zip(section_labels, streak_results):
            st.markdown(f"**{label_text}**")
            render_streak_section(result)

        st.markdown("---")
        st.markdown("##### ⚽ 该区间进球分布规律辅助（大小球/比分投注参考）")

        score_str = " | ".join(goal_stats.top_scores) if goal_stats.top_scores else "暂无数据"
        info_col1, info_col2 = st.columns(2)
        with info_col1:
            st.info(f"📊 场均总进球数: **{goal_stats.avg_goals:.2f} 个**")
        with info_col2:
            st.info(f"🔮 高频比分 TOP 3: **{score_str}**")


# ──────────────────────────────────────────────
# 5. 日期过滤器
# ──────────────────────────────────────────────

def render_date_filter(min_date, max_date):
    """
    渲染日期范围筛选器。

    Returns:
        selected_date_range: tuple(start_date, end_date) 或单个 date
    """
    c_date1, c_date2 = st.columns([2, 3])
    with c_date1:
        selected_range = st.date_input(
            "📅 选择量化分析时间范围",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
            key="global_chart_date_filter",
        )
    return c_date2, selected_range


# ──────────────────────────────────────────────
# 6. 001 过滤 Radio（Tab 内复用）
# ──────────────────────────────────────────────

def render_001_filter_radio(key: str) -> str:
    """
    渲染「全部/仅001/排除001」三选一 Radio。

    Args:
        key: Streamlit widget key（各 Tab 内需不同）

    Returns:
        选中的过滤选项字符串
    """
    return st.radio("选择分析赛事范围", APP_CONFIG.filter_options, key=key)


# ──────────────────────────────────────────────
# 7. 逐场追踪摘要文字
# ──────────────────────────────────────────────

def render_tracking_summary(total: int, underdog_wins: int, net_profit: int) -> None:
    """渲染逐场追踪统计摘要一行文字。"""
    st.write(
        f"📊 选定时间内总结：共 **{total}** 场，"
        f"下盘打出 **{underdog_wins}** 场，"
        f"累计净胜场次为 **{net_profit}** 场。"
    )
