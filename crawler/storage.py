"""
crawler/storage.py
==================
存储写入层。

职责：只负责"写入数据库"，不发请求，不做解析。

写入策略（幂等）：
  - 新记录 → INSERT
  - 已存在且无赛果 → UPDATE 赛果字段
  - 已存在且已有赛果 → SKIP（不覆盖已完赛记录）
"""

import logging
import os
from typing import List, Tuple

from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import sessionmaker, Session

from crawler.config import STORAGE_CONFIG
from crawler.models import Base, MatchData, MatchRecord

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 数据库引擎初始化（模块级单例）
# ──────────────────────────────────────────────

def _init_engine():
    """初始化数据库引擎、PRAGMA 设置，并确保表结构存在。"""
    # 确保 data 目录存在
    db_path = STORAGE_CONFIG.db_url.replace("sqlite:///", "")
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    engine = create_engine(
        STORAGE_CONFIG.db_url,
        echo=STORAGE_CONFIG.echo_sql,
        connect_args={"check_same_thread": False},  # SQLite 多线程安全
    )

    # 【优化】移除原代码中的冗余函数 _create_engine_and_tables，
    #         合并为此单一初始化函数，消除重复逻辑
    # WAL 模式：允许读写并发，避免 Streamlit 读取时被采集写入阻塞
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA synchronous=NORMAL"))
        conn.commit()

    Base.metadata.create_all(engine)
    logger.info("数据库引擎初始化完成: %s", STORAGE_CONFIG.db_url)
    return engine


try:
    _engine = _init_engine()
except Exception as e:
    logger.error("数据库初始化失败: %s", e)
    raise

SessionLocal = sessionmaker(bind=_engine)


# ──────────────────────────────────────────────
# 核心写入函数
# ──────────────────────────────────────────────

def save_matches(match_data_list: List[MatchData]) -> Tuple[int, int, int]:
    """
    批量幂等写入比赛数据。

    Args:
        match_data_list: spider 返回的 MatchData 列表

    Returns:
        (inserted, updated, skipped) 三个计数

    Raises:
        Exception: 数据库操作失败时 rollback 后向上抛出
    """
    if not match_data_list:
        return 0, 0, 0

    inserted = updated = skipped = 0
    session: Session = SessionLocal()

    try:
        for md in match_data_list:
            existing: MatchRecord = (
                session.query(MatchRecord)
                .filter_by(match_id=md.match_id)
                .first()
            )

            if not existing:
                session.add(MatchRecord.from_match_data(md))
                inserted += 1
            elif not existing.win_flag and md.win_flag:
                existing.update_result(md)
                updated += 1
            else:
                skipped += 1

        session.commit()
        logger.info(
            "✅ 入库完成：新增 %d 条，更新 %d 条，跳过 %d 条",
            inserted, updated, skipped,
        )

    except Exception as e:
        session.rollback()
        logger.error("❌ 数据库保存失败: %s", e)
        raise

    finally:
        session.close()

    return inserted, updated, skipped


# ──────────────────────────────────────────────
# 查询辅助（供 scheduler 增量抓取使用）
# ──────────────────────────────────────────────

def get_latest_match_date() -> str:
    """
    查询数据库中最新的比赛日期。
    数据库为空时返回 "2020-01-01" 作为兜底起点。

    Returns:
        "YYYY-MM-DD" 格式字符串
    """
    session: Session = SessionLocal()
    try:
        result = session.query(func.max(MatchRecord.match_date)).scalar()
        return result if result else "2020-01-01"
    except Exception as e:
        logger.warning("查询最新日期失败: %s，返回默认起点", e)
        return "2020-01-01"
    finally:
        session.close()
