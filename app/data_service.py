"""
data_service.py
===============
数据层（Data Access Layer）。

职责：
  1. 与 SQLite 数据库交互（唯一访问数据库的模块）
  2. 原始数据清洗
  3. 特征工程（生成分析所需衍生列）
  4. 日期过滤（将原始数据切片为分析用数据集）

架构原则：
  - 依赖倒置：上层模块依赖此层接口，不直接操作数据库
  - 纯函数优先：特征工程函数是纯函数，便于单元测试
  - 显式错误处理：不使用裸 except，所有异常均记录并向上传递语义
"""

import logging
import sqlite3
from contextlib import contextmanager
from typing import Generator, Optional, Tuple

import pandas as pd
import streamlit as st
import os

from config import DB_CONFIG, DOMAIN

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 1. 数据库访问（私有，不对外暴露）
# ──────────────────────────────────────────────

# 【优化】将手动 open/close 改为 contextmanager，
#         确保连接在异常时也能正确关闭，避免连接泄漏
@contextmanager
def _db_connection(db_path: str) -> Generator[sqlite3.Connection, None, None]:
    """
    SQLite 连接上下文管理器。

    Usage:
        with _db_connection(path) as conn:
            df = pd.read_sql(query, conn)

    Raises:
        FileNotFoundError: 数据库文件不存在
        sqlite3.Error:     连接失败
    """
    import os
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"数据库文件不存在：{db_path}")
    conn = sqlite3.connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def _fetch_raw_records(conn: sqlite3.Connection, table: str) -> pd.DataFrame:
    """
    执行 SQL 查询，返回原始 DataFrame。

    Raises:
        pd.io.sql.DatabaseError: 表不存在等
    """
    query = f"SELECT * FROM {table} ORDER BY match_date DESC, match_id DESC"
    return pd.read_sql(query, conn)


# ──────────────────────────────────────────────
# 2. 特征工程（纯函数，可独立单元测试）
# ──────────────────────────────────────────────

def _add_001_flag(df: pd.DataFrame) -> pd.DataFrame:
    """特征：标记 001 场次。"""
    df["is_001"] = df["match_num_str"].apply(
        lambda x: DOMAIN.label_001 if str(x).endswith(("001", "201"))  else DOMAIN.label_other
    )
    return df


def _add_result_columns(df: pd.DataFrame) -> pd.DataFrame:
    """特征：赛果中文名、赛果(比分)。"""
    df["赛果"] = df["win_flag"].map(DOMAIN.win_flag_map).fillna(DOMAIN.label_no_match)

    def _format_with_score(row: pd.Series) -> str:
        res = row["赛果"]
        score = row["sections_no999"]
        if res != DOMAIN.label_no_match and pd.notna(score) and str(score).strip():
            return f"{res} ({score})"
        return res

    df["赛果(比分)"] = df.apply(_format_with_score, axis=1)
    return df


def _parse_half_result(score_str) -> str:
    """将半场比分字符串转换为赛果标签。"""
    if pd.isna(score_str) or not isinstance(score_str, str) or ":" not in score_str:
        return DOMAIN.label_no_match
    try:
        h, a = map(int, score_str.split(":"))
        if h > a:
            return "主胜 (H)"
        elif h == a:
            return "平局 (D)"
        return "客胜 (A)"
    except ValueError:
        return "解析异常"


def _add_half_result(df: pd.DataFrame) -> pd.DataFrame:
    """特征：半场赛果。"""
    df["半场赛果"] = df["sections_no1"].apply(_parse_half_result)
    return df


def _parse_total_goals(score_str) -> Optional[int]:
    """从比分字符串解析总进球数。"""
    try:
        if score_str and ":" in str(score_str):
            h, a = map(int, str(score_str).split(":"))
            return h + a
    except (ValueError, TypeError):
        pass
    return None


def _add_total_goals(df: pd.DataFrame) -> pd.DataFrame:
    """特征：总进球数。"""
    df["总进球数"] = df["sections_no999"].apply(_parse_total_goals)
    return df


def _add_match_month(df: pd.DataFrame) -> pd.DataFrame:
    """特征：比赛月份（YYYY-MM），用于趋势分析。"""
    df["比赛月份"] = df["match_date"].apply(
        lambda x: str(x)[:7] if x else "未知"
    )
    return df


def _determine_underdog(row: pd.Series) -> str:
    """判断单行的盘路结果（下盘/上盘/平手）。"""
    if row["赛果"] == DOMAIN.label_no_match:
        return DOMAIN.label_not_raced
    try:
        gl = float(row["goal_line"])
    except (ValueError, TypeError):
        return DOMAIN.label_abnormal

    win_flag = row["win_flag"]
    if gl < 0:   # 主队让球，D/A 为下盘
        return DOMAIN.label_underdog if win_flag in ("D", "A") else DOMAIN.label_favorite
    elif gl > 0: # 客队让球，H/D 为下盘
        return DOMAIN.label_underdog if win_flag in ("H", "D") else DOMAIN.label_favorite
    return DOMAIN.label_flat


def _add_pan_result(df: pd.DataFrame) -> pd.DataFrame:
    """特征：盘路结果。"""
    df["盘路结果"] = df.apply(_determine_underdog, axis=1)
    return df


def _add_single_flag(df: pd.DataFrame) -> pd.DataFrame:
    """特征：单关标记。兼容字段缺失的情况。"""
    if "betting_single" in df.columns:
        truthy = {str(v) for v in DOMAIN.single_truthy_values}
        df["is_单关"] = df["betting_single"].apply(
            lambda x: DOMAIN.label_single if str(x) in truthy else DOMAIN.label_non_single
        )
    else:
        df["is_单关"] = DOMAIN.label_unknown_single
    return df


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    特征工程总入口（纯函数）。
    按顺序应用所有特征变换，返回新 DataFrame（不修改原始数据）。

    Args:
        df: 从数据库读取的原始 DataFrame

    Returns:
        带有所有衍生特征列的 DataFrame
    """
    if df.empty:
        return df

    df = df.copy()
    transformations = [
        _add_001_flag,
        _add_result_columns,
        _add_half_result,
        _add_total_goals,
        _add_match_month,
        _add_pan_result,
        _add_single_flag,
    ]
    for transform in transformations:
        df = transform(df)

    return df


# ──────────────────────────────────────────────
# 3. 数据加载入口（对外唯一公开接口）
# ──────────────────────────────────────────────

@st.cache_data(ttl=DB_CONFIG.cache_ttl)
def load_and_process_data(_db_last_modified: float) -> pd.DataFrame:
    """
    加载并处理完整数据集（Streamlit 缓存，TTL=10分钟）。

    Returns:
        处理完毕的 DataFrame；如遇任何异常，返回空 DataFrame 并通过 st.warning 提示。
    """
    # 【优化】使用 contextmanager 替换手动 try/finally close，代码更简洁
    try:
        with _db_connection(DB_CONFIG.path) as conn:
            raw_df = _fetch_raw_records(conn, DB_CONFIG.table_name)
        return add_features(raw_df)

    except FileNotFoundError as e:
        logger.warning("数据库文件未找到: %s", e)
        st.warning(f"⚠️ {e}\n\n请先运行爬虫抓取数据，或检查 `LOTTERY_DB_PATH` 环境变量。")
        return pd.DataFrame()

    except Exception as e:
        logger.exception("数据加载失败")
        st.error(f"❌ 数据加载失败：{e}")
        return pd.DataFrame()


# ──────────────────────────────────────────────
# 4. 数据切片工具函数
# ──────────────────────────────────────────────

def get_finished_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    从完整数据集中筛选已完赛记录，并附加标准化日期列。

    Returns:
        包含 `match_date_dt`（datetime.date 类型）的已完赛 DataFrame
    """
    if df.empty:
        return df

    df_finished = df[df["赛果"].isin(DOMAIN.finished_results)].copy()
    if not df_finished.empty:
        df_finished["match_date_dt"] = pd.to_datetime(df_finished["match_date"]).dt.date
    return df_finished


def filter_by_date(
    df: pd.DataFrame,
    date_range: Tuple,
) -> pd.DataFrame:
    """
    根据日期范围过滤已完赛 DataFrame（需提前调用 get_finished_df）。

    Args:
        df:         含 `match_date_dt` 列的 DataFrame
        date_range: (start_date, end_date) 元组；长度不为 2 时返回原 df

    Returns:
        过滤后的 DataFrame
    """
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_d, end_d = date_range
        return df[
            (df["match_date_dt"] >= start_d) &
            (df["match_date_dt"] <= end_d)
        ]
    return df


def apply_001_filter(df: pd.DataFrame, filter_val: str) -> pd.DataFrame:
    """
    按 001 / 非001 / 全部 过滤。
    供图表 Tab 中 radio 按钮联动使用。
    """
    if filter_val == "仅001场次":
        return df[df["is_001"] == DOMAIN.label_001]
    elif filter_val == "排除001场次":
        return df[df["is_001"] == DOMAIN.label_other]
    return df
