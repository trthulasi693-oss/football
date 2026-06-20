"""
ui/app.py
=========
Streamlit 应用入口（组装层 / Composition Root）。

职责：
  - 唯一调用 st.set_page_config 的地方
  - 按顺序调用各层，组装完整应用
  - 管理 Streamlit Session State（过滤条件等）
  - 不包含任何业务计算逻辑，计算均委托给 analytics 层

数据流：
  data_service → analytics → ui/components + ui/charts

使用方式：
  streamlit run app/ui/app.py
"""

import sys
import os

# 确保项目根目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib.pyplot as plt
import streamlit as st

from config import APP_CONFIG, DISPLAY_CONFIG,DB_CONFIG
from data_service import (
    load_and_process_data,
    get_finished_df,
    filter_by_date,
    apply_001_filter,
)
from analytics import (
    calc_kpi,
    calc_pan_stats,
    calc_all_streaks,
    calc_goal_stats,
    calc_underdog_trend,
    calc_tracking_data,
)
from ui.styles import inject_global_css, sample_count_html
from ui.components import (
    render_kpi_row,
    render_pan_stats_row,
    render_streak_expander,
    render_date_filter,
    render_001_filter_radio,
    render_tracking_summary,
)
from ui.charts import (
    setup_matplotlib,
    plot_001_result_distribution,
    get_001_pivot_table,
    plot_goal_line_result,
    plot_goal_distribution,
    plot_underdog_trend,
    plot_tracking,
)


# ──────────────────────────────────────────────
# 辅助：渲染图表并释放内存
# ──────────────────────────────────────────────

def _show_fig(fig) -> None:
    """
    渲染 Matplotlib Figure 并立即关闭，防止跨 rerun 的内存泄漏。

    【优化】原代码直接调用 st.pyplot(fig) 后未关闭 figure，
            在 Streamlit 频繁 rerun 时会累积大量未释放的图形对象。
    """
    st.pyplot(fig)
    plt.close(fig)


# ══════════════════════════════════════════════
# 0. 应用初始化（每次脚本运行执行一次）
# ══════════════════════════════════════════════

st.set_page_config(
    page_title=APP_CONFIG.page_title,
    layout=APP_CONFIG.layout,
    page_icon=APP_CONFIG.page_icon,
)
inject_global_css()
setup_matplotlib()


# ══════════════════════════════════════════════
# 1. 数据加载
# ══════════════════════════════════════════════
try:
    db_mtime = os.path.getmtime(DB_CONFIG.path)
except FileNotFoundError:
    db_mtime = 0.0
    
df_all = load_and_process_data(db_mtime)

if df_all.empty or "赛果" not in df_all.columns:
    st.warning("⚠️ 数据库为空或暂无已完赛数据。请先运行爬虫抓取历史完赛数据。")
    st.stop()

df_finished = get_finished_df(df_all)

if df_finished.empty:
    st.warning("⚠️ 暂无已完赛比赛数据。")
    st.stop()


# ══════════════════════════════════════════════
# 2. 页面标题 & KPI
# ══════════════════════════════════════════════

st.markdown(f"### 📈 {APP_CONFIG.page_title} {APP_CONFIG.version}")

kpi = calc_kpi(df_all, df_finished)
render_kpi_row(kpi)


# ══════════════════════════════════════════════
# 3. 赛事数据明细库
# ══════════════════════════════════════════════

st.markdown("##### 🗂️ 赛事数据明细库")

# 只展示已存在的列（兼容数据库字段变化）
available_cols = [c for c in DISPLAY_CONFIG.detail_columns if c in df_all.columns and c not in ['sections_no999']]
st.dataframe(df_all[available_cols], use_container_width=True, height=500)


# ══════════════════════════════════════════════
# 4. 日期过滤器 & 图表数据源切片
# ══════════════════════════════════════════════

min_date = df_finished["match_date_dt"].min()
max_date = df_finished["match_date_dt"].max()

c_date2, selected_date_range = render_date_filter(min_date, max_date)
df_chart_source = filter_by_date(df_finished, selected_date_range)

with c_date2:
    st.markdown(
        sample_count_html(len(df_chart_source)),
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════
# 5. 盘路核心统计 & 极端路单追踪
# ══════════════════════════════════════════════

st.markdown("##### 📊 当前筛选范围：盘路核心统计与极端路单追踪")

pan_stats = calc_pan_stats(df_chart_source)

if pan_stats.total > 0:
    render_pan_stats_row(pan_stats)

    streak_results = calc_all_streaks(df_chart_source)
    goal_stats = calc_goal_stats(df_chart_source)
    render_streak_expander(streak_results, goal_stats)
else:
    st.info("当前选择的日期范围内没有完赛的盘路统计数据。")


# ══════════════════════════════════════════════
# 6. 多维量化图表交互大厅（5 个 Tab）
# ══════════════════════════════════════════════

st.markdown("##### 📊 策略假设验证中心")

tabs = st.tabs(APP_CONFIG.tab_labels)


# ──────────────────────────────────────────────
# Tab 1：001场次多下盘验证
# ──────────────────────────────────────────────
with tabs[0]:
    if not df_chart_source.empty:
        col_chart, col_table = st.columns([2, 1])
        with col_chart:
            _show_fig(plot_001_result_distribution(df_chart_source))
        with col_table:
            st.markdown("**📝 概率量化对比**")
            pivot_table = get_001_pivot_table(df_chart_source)
            st.dataframe(
                pivot_table.style.format("{:.1f}%"),
                use_container_width=True,
            )
    else:
        st.info("当前选择的日期范围内没有完赛数据。")


# ──────────────────────────────────────────────
# Tab 2：让球数与赛果关系
# ──────────────────────────────────────────────
with tabs[1]:
    if not df_chart_source.empty:
        _show_fig(plot_goal_line_result(df_chart_source))
    else:
        st.info("当前选择的日期范围内没有完赛数据。")


# ──────────────────────────────────────────────
# Tab 3：总进球数分布（含001筛选）
# ──────────────────────────────────────────────
with tabs[2]:
    c_radio, c_chart = st.columns([1, 4])
    with c_radio:
        filter_3 = render_001_filter_radio(key="f3")
    with c_chart:
        df_f3 = apply_001_filter(df_chart_source, filter_3).dropna(subset=["总进球数"])
        if not df_f3.empty:
            _show_fig(plot_goal_distribution(df_f3, title_prefix=filter_3))
        else:
            st.info("当前筛选范围或日期区间内暂无有效进球数据。")


# ──────────────────────────────────────────────
# Tab 4：下盘率历史走势（含001筛选）
# ──────────────────────────────────────────────
with tabs[3]:
    c_radio, c_chart = st.columns([1, 4])
    with c_radio:
        filter_4 = render_001_filter_radio(key="f4")
    with c_chart:
        df_f4 = apply_001_filter(df_chart_source, filter_4)
        trend_points = calc_underdog_trend(df_f4)

        if trend_points:
            months = [p.month for p in trend_points]
            rates  = [p.rate  for p in trend_points]
            _show_fig(plot_underdog_trend(months, rates, title_prefix=filter_4))
        else:
            st.info("当前筛选时间段内暂无足够月度数据。")


# ──────────────────────────────────────────────
# Tab 5：下盘走势逐场追踪（含001筛选）
# ──────────────────────────────────────────────
with tabs[4]:
    c_radio, c_chart = st.columns([1, 4])
    with c_radio:
        filter_5 = render_001_filter_radio(key="f5")
        st.info(
            "💡 **走势说明**\n\n"
            "**上图**：每根柱子代表一场，向上(绿)为下盘，向下(红)为正路。\n"
            "**下图**：累计净胜走势。曲线往上走说明这段时间下盘在持续收米。"
        )
    with c_chart:
        df_f5 = apply_001_filter(df_chart_source, filter_5)
        df_track, track_stats = calc_tracking_data(df_f5)

        if not df_track.empty:
            _show_fig(plot_tracking(df_track, title_prefix=filter_5))
            render_tracking_summary(
                total=track_stats.total,
                underdog_wins=track_stats.underdog_wins,
                net_profit=track_stats.net_profit,
            )
        else:
            st.info("当前筛选的时间范围及赛事范围内，暂无足够的有效盘路记录。")
