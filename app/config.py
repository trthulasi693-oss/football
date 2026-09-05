"""
config.py
=========
全局配置中心。所有硬编码常量、路径、映射关系均在此处声明。
其他模块从此处 import，禁止在业务代码中直接写字符串常量。

架构原则：单一数据源（Single Source of Truth）
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

# 项目根目录（config.py 所在的 app/ 目录的上一层）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ──────────────────────────────────────────────
# 1. 数据库配置
# ──────────────────────────────────────────────

@dataclass(frozen=True)
class DatabaseConfig:
    """数据库连接配置，支持通过环境变量覆盖，便于多环境部署。"""

    path: str = field(
        default_factory=lambda: os.getenv(
            "LOTTERY_DB_PATH",
            str(_PROJECT_ROOT / "data" / "lottery_data.db"),
        )
    )
    table_name: str = "match_records"
    cache_ttl: int = 600  # Streamlit cache 有效期（秒）


# ──────────────────────────────────────────────
# 2. 业务领域常量
# ──────────────────────────────────────────────

@dataclass(frozen=True)
class DomainConfig:
    """领域业务常量：赛果映射、盘路标签、特殊场次标记。"""

    # 赛果映射
    win_flag_map: Dict[str, str] = field(default_factory=lambda: {
        "H": "主胜 (H)",
        "D": "平局 (D)",
        "A": "客胜 (A)",
    })

    # 盘路结果标签
    label_underdog:  str = "✅ 下盘打出"
    label_favorite:  str = "❌ 上盘(正路)"
    label_flat:      str = "⚪ 平手盘"
    label_no_match:  str = "未知/未赛"
    label_abnormal:  str = "盘口异常"
    label_not_raced: str = "未赛"

    # 001场次标记
    label_001:   str = "001场次"
    label_other: str = "其它场次"

    # 单关标记
    label_single:         str = "单关场次"
    label_non_single:     str = "非单关"
    label_unknown_single: str = "未知单关状态"

    # 已完赛状态集合（用于 isin 过滤）
    @property
    def finished_results(self) -> List[str]:
        return ["主胜 (H)", "平局 (D)", "客胜 (A)"]

    # 有效盘路集合
    @property
    def valid_pan_results(self) -> List[str]:
        return [self.label_underdog, self.label_favorite, self.label_flat]

    # 纯上/下盘集合（排除平手盘，用于连路计算）
    @property
    def streak_results(self) -> List[str]:
        return [self.label_underdog, self.label_favorite]

    # 单关字段的真值集合
    single_truthy_values: tuple = ("1", "True", "true", "是", 1)


# ──────────────────────────────────────────────
# 3. 展示层配置
# ──────────────────────────────────────────────

@dataclass(frozen=True)
class DisplayConfig:
    """数据明细表格的展示列顺序。"""
    detail_columns: List[str] = field(default_factory=lambda: [
        "match_date", "match_num_str", "league_name",
        "home_team", "away_team", "goal_line",
        "sections_no999", "半场赛果", "总进球数",
        "赛果", "赛果(比分)", "盘路结果", "is_001",
    ])


def _resolve_cjk_font() -> List[str]:
    """
    按"系统 → 优先级 → 探测"返回**真实存在**的 CJK 字体候选列表。

    关键改进：先用 matplotlib.font_manager 探测系统里实际装了哪些字体，
    再按本系统的候选优先级过滤。matplotlib 只会尝试列表中的字体，
    因此**不会产生 "findfont: ... not found" 警告**。

    跨平台覆盖：
      - Windows: Microsoft YaHei / SimHei / SimSun (自带)
      - macOS:   PingFang SC / Heiti SC / Hiragino Sans GB (自带)
      - Linux:   Noto Sans CJK SC / WenQuanYi Zen Hei (常装)

    如果系统连一个 CJK 字体都没有，返回 matplotlib 内置的默认 sans-serif
    （此时中文会渲染为方框，但**绝不会出现字体缺失警告**）。
    """
    import matplotlib.font_manager as fm

    # 1) 按系统给出"理想候选顺序"
    if sys.platform.startswith("win"):
        ideal = [
            "Microsoft YaHei",
            "Microsoft YaHei UI",
            "SimHei",
            "SimSun",
            "NSimSun",
            "FangSong",
            "KaiTi",
            "Arial Unicode MS",
        ]
    elif sys.platform == "darwin":
        ideal = [
            "PingFang SC",
            "Heiti SC",
            "Hiragino Sans GB",
            "STHeiti",
            "STSong",
        ]
    else:
        ideal = [
            "Noto Sans CJK SC",
            "Noto Sans CJK JP",
            "Noto Sans CJK TC",
            "WenQuanYi Zen Hei",
            "WenQuanYi Micro Hei",
            "Source Han Sans SC",
            "Source Han Sans CN",
            "Source Han Serif SC",
            "AR PL UMing CN",
            "AR PL UKai CN",
        ]

    # 2) 探测系统实际装了哪些字体名（一次性查询，很快）
    try:
        installed = {f.name for f in fm.fontManager.ttflist}
    except Exception:
        installed = set()

    # 3) 严格按理想顺序筛选真实存在的字体
    available = [name for name in ideal if name in installed]

    if available:
        return available

    # 4) 兜底：找不到任何理想字体时，退到系统默认 sans-serif
    #    matplotlib 内置 DejaVu Sans 一定存在，不会产生警告
    #    中文会变方框，但程序不会刷屏
    return ["DejaVu Sans"]


@dataclass(frozen=True)
class ChartConfig:
    """图表颜色与样式配置。"""
    # 颜色
    color_home:     str = "#e63946"
    color_draw:     str = "#457b9d"
    color_away:     str = "#2a9d8f"
    color_neutral:  str = "#f4a261"
    color_underdog: str = "#2a9d8f"
    color_favorite: str = "#e63946"

    # 柱状图三色组
    @property
    def result_colors(self) -> List[str]:
        return [self.color_home, self.color_draw, self.color_away]

    # 跨平台 CJK 字体候选列表（运行时按系统选优先级）
    font_families: List[str] = field(default_factory=_resolve_cjk_font)

    # 图表默认尺寸
    fig_size_wide:   tuple = (10, 3.8)
    fig_size_normal: tuple = (8, 3.8)
    fig_size_small:  tuple = (8, 3.5)
    fig_size_double: tuple = (10, 6)


# ──────────────────────────────────────────────
# 4. 应用配置
# ──────────────────────────────────────────────

@dataclass(frozen=True)
class AppConfig:
    """Streamlit 应用基础配置。"""
    page_title: str = "量化分析终端"
    page_icon:  str = "📈"
    layout:     str = "wide"
    version:    str = "V3.0"

    tab_labels: List[str] = field(default_factory=lambda: [
        "🔥 验证：001场次多下盘",
        "🎲 盘口：让球数与赛果关系",
        "⚽ 进球：总进球数分布",
        "📈 趋势：下盘率历史走势",
        "📉 趋势：下盘走势逐场追踪",
    ])

    filter_options: List[str] = field(default_factory=lambda: [
        "全部赛事", "仅001场次", "排除001场次"
    ])


# ──────────────────────────────────────────────
# 5. 单例导出（模块级常量，直接 import 使用）
# ──────────────────────────────────────────────

DB_CONFIG      = DatabaseConfig()
DOMAIN         = DomainConfig()
DISPLAY_CONFIG = DisplayConfig()
CHART_CONFIG   = ChartConfig()
APP_CONFIG     = AppConfig()
