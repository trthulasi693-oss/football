"""
ui/charts.py
============
图表绘制层。

职责：
  - 封装所有 Matplotlib / Seaborn 绘图逻辑
  - 每个函数接收数据、返回 Figure，不直接调用 st.pyplot
  - 由 app.py 负责 st.pyplot(fig) 的调用

架构原则：
  - 图表函数是纯函数：相同输入 → 相同输出
  - 不依赖 Streamlit（方便在非 Streamlit 环境下测试图表）
  - 颜色、字体从 config.ChartConfig 读取，不硬编码
  - 每个函数均不调用 plt.show()，避免 Streamlit 重复渲染
"""

import matplotlib
import matplotlib.figure
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from typing import List

from config import CHART_CONFIG, DOMAIN

# 【优化】在非交互后端模式下运行，防止多线程下的 GUI 冲突
matplotlib.use("Agg")


# ──────────────────────────────────────────────
# 1. 字体与主题初始化
# ──────────────────────────────────────────────

def setup_matplotlib() -> None:
    """
    初始化 Matplotlib 中文字体与主题。
    在 app.py 启动时调用一次。
    """
    plt.rcParams["font.sans-serif"] = CHART_CONFIG.font_families
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["axes.unicode_minus"] = False
    # seaborn 接受单字体或列表，传列表更稳
    sns.set_theme(style="whitegrid", font=CHART_CONFIG.font_families)


# ──────────────────────────────────────────────
# 2. Tab1：001 vs 其它场次赛果分布
# ──────────────────────────────────────────────

def plot_001_result_distribution(
    df: pd.DataFrame,
) -> matplotlib.figure.Figure:
    """
    绘制 001场次 vs 其它场次 赛果概率分布柱状图。

    Args:
        df: 含 `is_001`、`赛果` 列的 DataFrame

    Returns:
        Matplotlib Figure
    """
    pivot = df.groupby(["is_001", "赛果"]).size().unstack(fill_value=0)
    pivot_pct = pivot.div(pivot.sum(axis=1), axis=0) * 100

    fig, ax = plt.subplots(figsize=CHART_CONFIG.fig_size_normal)
    pivot_pct.plot(
        kind="bar",
        color=CHART_CONFIG.result_colors,
        edgecolor="none",
        ax=ax,
    )
    ax.set_title("001场次 vs 其它场次 赛果概率分布", fontsize=11)
    ax.set_ylabel("发生概率 (%)")
    ax.set_xticklabels(pivot_pct.index, rotation=0)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=3)
    plt.tight_layout()

    # 【优化】调用方（app.py）使用 st.pyplot(fig) 后须关闭 figure，
    #         此处由调用方负责关闭（see app.py 使用规范），
    #         或在此处关闭后返回：直接返回 fig，由 app.py close
    return fig


def get_001_pivot_table(df: pd.DataFrame) -> pd.DataFrame:
    """返回 Tab1 概率对比表格数据（供 st.dataframe 使用）。"""
    pivot = df.groupby(["is_001", "赛果"]).size().unstack(fill_value=0)
    return (pivot.div(pivot.sum(axis=1), axis=0) * 100).round(1)


# ──────────────────────────────────────────────
# 3. Tab2：让球数与赛果关系
# ──────────────────────────────────────────────

def plot_goal_line_result(df: pd.DataFrame) -> matplotlib.figure.Figure:
    """
    绘制让球深度对赛果的稀释效应（堆积柱状图）。

    Args:
        df: 含 `goal_line`、`赛果` 列的 DataFrame
    """
    pivot = df.groupby(["goal_line", "赛果"]).size().unstack(fill_value=0)
    pivot_pct = pivot.div(pivot.sum(axis=1), axis=0) * 100

    fig, ax = plt.subplots(figsize=CHART_CONFIG.fig_size_wide)
    pivot_pct.plot(
        kind="bar",
        stacked=True,
        color=CHART_CONFIG.result_colors,
        alpha=0.8,
        ax=ax,
    )
    ax.set_title("让球深度对赛果的稀释效应", fontsize=11)
    ax.set_ylabel("占比 (%)")
    ax.set_xticklabels(ax.get_xticklabels(), rotation=0)
    plt.tight_layout()
    return fig


# ──────────────────────────────────────────────
# 4. Tab3：总进球数概率分布
# ──────────────────────────────────────────────

def plot_goal_distribution(
    df: pd.DataFrame,
    title_prefix: str,
) -> matplotlib.figure.Figure:
    """
    绘制总进球数概率分布柱状图。

    Args:
        df:           已过滤（含 `总进球数` 列，已 dropna）的 DataFrame
        title_prefix: 图表标题前缀，如「全部赛事」
    """
    goal_counts = df["总进球数"].value_counts().sort_index()
    goal_pct = goal_counts / goal_counts.sum() * 100

    fig, ax = plt.subplots(figsize=CHART_CONFIG.fig_size_small)
    sns.barplot(
        x=goal_pct.index.astype(int),
        y=goal_pct.values,
        color=CHART_CONFIG.color_neutral,
        ax=ax,
        edgecolor="none",
    )
    ax.set_title(f"【{title_prefix}】总进球数概率分布统计", fontsize=11)
    ax.set_xlabel("单场总进球数")
    ax.set_ylabel("分布比例 (%)")
    for i, v in enumerate(goal_pct.values):
        ax.text(i, v + 0.8, f"{v:.1f}%", ha="center", fontsize=9)
    plt.tight_layout()
    return fig


# ──────────────────────────────────────────────
# 5. Tab4：下盘率历史月度趋势
# ──────────────────────────────────────────────

def plot_underdog_trend(
    months: List[str],
    rates: List[float],
    title_prefix: str,
) -> matplotlib.figure.Figure:
    """
    绘制下盘打出率月度走势折线图。

    Args:
        months:       月份字符串列表（X轴）
        rates:        下盘率列表（Y轴）
        title_prefix: 图表标题前缀
    """
    fig, ax = plt.subplots(figsize=CHART_CONFIG.fig_size_small)
    ax.plot(
        months, rates,
        marker="o", linewidth=2,
        color=CHART_CONFIG.color_underdog,
        label="实测下盘率",
    )
    ax.axhline(50, color=CHART_CONFIG.color_favorite, linestyle="--", alpha=0.5, label="50% 理论平衡线")
    ax.set_title(f"【{title_prefix}】历史下盘打出率动态走势趋势", fontsize=11)
    ax.set_xlabel("时间跨度 (月份)")
    ax.set_ylabel("下盘打出率 (%)")
    ax.set_ylim(0, 105)
    ax.legend(frameon=False, loc="lower left")

    for x, y in zip(months, rates):
        ax.text(x, y + 3, f"{y:.1f}%", ha="center", fontsize=9)
    plt.tight_layout()
    return fig


# ──────────────────────────────────────────────
# 6. Tab5：逐场追踪双图（柱状 + 累计折线）
# ──────────────────────────────────────────────

def plot_tracking(
    df_track: pd.DataFrame,
    title_prefix: str,
) -> matplotlib.figure.Figure:
    """
    绘制逐场盘路追踪双联图：
    上图：每场 +1/-1 柱状图
    下图：累计净胜走势折线图

    Args:
        df_track:     含 `outcome_val`、`cum_net`、`match_date` 列的 DataFrame
        title_prefix: 图表标题前缀
    """
    fig, (ax_top, ax_bot) = plt.subplots(
        2, 1,
        figsize=CHART_CONFIG.fig_size_double,
        gridspec_kw={"height_ratios": [1, 2.5]},
        sharex=True,
    )

    # 上图：单场柱状
    colors = df_track["outcome_val"].map({
        1:  CHART_CONFIG.color_underdog,
        -1: CHART_CONFIG.color_favorite,
    })
    ax_top.bar(df_track.index, df_track["outcome_val"], color=colors, width=0.6, alpha=0.8)
    ax_top.set_title(f"【{title_prefix}】单场盘路打出分布 (共{len(df_track)}场)", fontsize=11)
    ax_top.set_yticks([-1, 1])
    ax_top.set_yticklabels(["上盘", "下盘"])
    ax_top.axhline(0, color="black", linewidth=0.8)
    ax_top.grid(axis="x", alpha=0.3)

    # 下图：累计净胜折线
    cum = df_track["cum_net"]
    ax_bot.plot(df_track.index, cum, color=CHART_CONFIG.color_neutral, linewidth=2, label="累计净胜场数")
    ax_bot.fill_between(df_track.index, cum, 0,
                        where=(cum >= 0), color=CHART_CONFIG.color_underdog, alpha=0.15)
    ax_bot.fill_between(df_track.index, cum, 0,
                        where=(cum < 0),  color=CHART_CONFIG.color_favorite, alpha=0.1)
    ax_bot.axhline(0, color="gray", linestyle="--", linewidth=1)
    ax_bot.set_xlabel("比赛场次序号 (按时间由远及近)")
    ax_bot.set_ylabel("下盘累计净胜 (场)")
    ax_bot.legend(loc="upper left", fontsize=9, frameon=True)

    plt.tight_layout()
    return fig
