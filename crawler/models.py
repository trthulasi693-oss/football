"""
crawler/models.py
=================
数据模型层（整合自真实 models.py）。

包含三部分：
  1. FIELD_MAPPING     — API 字段名 → 中文含义，供 UI 展示使用
  2. MatchData         — 内存传输的数据类（爬虫 → 存储的中间载体）
  3. MatchRecord / Base — SQLAlchemy ORM 表结构（来自真实 database.py）

架构原则：
  - 数据模型集中在一处，spider / storage 均从此处 import
  - MatchData 是纯数据类（无 IO），MatchRecord 是 ORM 映射类（与 DB 绑定）
  - 两者通过 MatchData → MatchRecord 的转换函数解耦
"""

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import Column, Integer, String, Float
from sqlalchemy.orm import declarative_base


# ──────────────────────────────────────────────
# 0. 字段中文映射表（来自真实 models.py，完整保留）
# ──────────────────────────────────────────────

FIELD_MAPPING = {
    "matchId":           "比赛ID",
    "matchDate":         "比赛日期",
    "matchNum":          "比赛数字编号",
    "matchNumStr":       "竞彩场次编号",
    "leagueId":          "联赛ID",
    "leagueName":        "联赛全称",
    "leagueNameAbbr":    "联赛简称",
    "leagueBackColor":   "联赛标签背景色",
    "homeTeamId":        "主队ID",
    "homeTeam":          "主队名称",
    "allHomeTeam":       "主队全称",
    "awayTeamId":        "客队ID",
    "awayTeam":          "客队名称",
    "allAwayTeam":       "客队全称",
    "h":                 "主胜赔率",
    "d":                 "平局赔率",
    "a":                 "客胜赔率",
    "goalLine":          "让球数",
    "bettingSingle":     "是否支持单关",
    "sectionsNo1":       "半场比分",
    "sectionsNo999":     "全场比分",
    "winFlag":           "彩果胜平负标志",
    "matchResultStatus": "赛事结果状态码",
    "poolStatus":        "奖池/派奖状态",
    "resultStatus":      "附加结果状态",
}


# ──────────────────────────────────────────────
# 1. 类型安全辅助函数
# ──────────────────────────────────────────────

def _safe_float(val, default: float = 0.0) -> float:
    """安全转 float，空值/非法值返回 default。"""
    try:
        return float(val) if val not in (None, "", "None") else default
    except (ValueError, TypeError):
        return default


def _safe_int(val, default: int = 0) -> int:
    """安全转 int，空值/非法值返回 default。"""
    try:
        return int(val) if val not in (None, "", "None") else default
    except (ValueError, TypeError):
        return default


# ──────────────────────────────────────────────
# 2. 内存数据类（爬虫 ↔ 存储的传输载体）
# ──────────────────────────────────────────────

@dataclass
class MatchData:
    """
    单场比赛的结构化数据。
    由 spider.py 构造，传递给 storage.py 写入数据库。
    不依赖任何 IO，可直接用于单元测试。
    """
    match_id:             str
    match_date:           str
    match_num:            str
    match_num_str:        str
    league_id:            int
    league_name:          str
    league_name_abbr:     str
    league_back_color:    str

    home_team_id:         int
    home_team:            str
    all_home_team:        str

    away_team_id:         int
    away_team:            str
    all_away_team:        str

    odds_h:               float
    odds_d:               float
    odds_a:               float
    goal_line:            str       # 保留字符串，如 "-1"、"+1.5"、"0"

    betting_single:       int
    sections_no1:         str       # 半场比分，未赛时为空串
    sections_no999:       str       # 全场比分，未赛时为空串
    win_flag:             str       # H / D / A，未赛时为空串
    match_result_status:  str
    pool_status:          str
    result_status:        str

    @classmethod
    def from_json(cls, data: dict) -> Optional["MatchData"]:
        """
        从 API 原始 JSON 安全构造 MatchData。
        任何字段解析失败均返回 None，由调用方过滤。

        字段映射来自真实 API 响应结构（sporttery.cn）。
        """
        try:
            return cls(
                match_id            = str(data.get("matchId",           "")),
                match_date          = str(data.get("matchDate",         "")),
                match_num           = str(data.get("matchNum",          "")),
                match_num_str       = str(data.get("matchNumStr",       "")),
                league_id           = _safe_int(data.get("leagueId")),
                league_name         = str(data.get("leagueName",        "")),
                league_name_abbr    = str(data.get("leagueNameAbbr",    "")),
                league_back_color   = str(data.get("leagueBackColor",   "")),

                home_team_id        = _safe_int(data.get("homeTeamId")),
                home_team           = str(data.get("homeTeam",          "")),
                all_home_team       = str(data.get("allHomeTeam",       "")),

                away_team_id        = _safe_int(data.get("awayTeamId")),
                away_team           = str(data.get("awayTeam",          "")),
                all_away_team       = str(data.get("allAwayTeam",       "")),

                odds_h              = _safe_float(data.get("h")),
                odds_d              = _safe_float(data.get("d")),
                odds_a              = _safe_float(data.get("a")),
                goal_line           = str(data.get("goalLine",          "")),

                betting_single      = _safe_int(data.get("bettingSingle")),
                sections_no1        = str(data.get("sectionsNo1",       "")),
                sections_no999      = str(data.get("sectionsNo999",     "")),
                win_flag            = str(data.get("winFlag",           "")),
                match_result_status = str(data.get("matchResultStatus", "")),
                pool_status         = str(data.get("poolStatus",        "")),
                result_status       = str(data.get("resultStatus",      "")),
            )
        except Exception as e:
            import logging
            match_id = data.get("matchId") if isinstance(data, dict) else "N/A"
            logging.getLogger(__name__).warning(
                "MatchData.from_json 解析失败 matchId=%s: %s", match_id, e
            )
            return None


# ──────────────────────────────────────────────
# 3. SQLAlchemy ORM 表结构（来自真实 database.py / models.py）
# ──────────────────────────────────────────────

Base = declarative_base()


class MatchRecord(Base):
    """
    数据库表 match_records 的 ORM 映射。
    字段与 MatchData 一一对应，通过 MatchRecord.from_match_data() 转换。
    """
    __tablename__ = "match_records"

    id                  = Column(Integer,     primary_key=True, autoincrement=True)
    match_id            = Column(String(50),  unique=True, index=True, nullable=False)
    match_date          = Column(String(20))
    match_num           = Column(String(20))
    match_num_str       = Column(String(50),  index=True)

    league_id           = Column(Integer)
    league_name         = Column(String(100))
    league_name_abbr    = Column(String(50))
    league_back_color   = Column(String(20))

    home_team_id        = Column(Integer)
    home_team           = Column(String(100))
    all_home_team       = Column(String(100))

    away_team_id        = Column(Integer)
    away_team           = Column(String(100))
    all_away_team       = Column(String(100))

    odds_h              = Column(Float)
    odds_d              = Column(Float)
    odds_a              = Column(Float)
    goal_line           = Column(String(20))        # 保留字符串，如 "-1"、"+1.5"

    betting_single      = Column(Integer)
    sections_no1        = Column(String(20),  nullable=True)   # 半场比分
    sections_no999      = Column(String(20),  nullable=True)   # 全场比分
    win_flag            = Column(String(10),  nullable=True)   # 赛果 H/D/A
    match_result_status = Column(String(20),  nullable=True)
    pool_status         = Column(String(50),  nullable=True)
    result_status       = Column(String(50),  nullable=True)

    @classmethod
    def from_match_data(cls, md: MatchData) -> "MatchRecord":
        """将 MatchData 转换为 ORM 对象，用于写入数据库。"""
        return cls(
            match_id            = md.match_id,
            match_date          = md.match_date,
            match_num           = md.match_num,
            match_num_str       = md.match_num_str,
            league_id           = md.league_id,
            league_name         = md.league_name,
            league_name_abbr    = md.league_name_abbr,
            league_back_color   = md.league_back_color,
            home_team_id        = md.home_team_id,
            home_team           = md.home_team,
            all_home_team       = md.all_home_team,
            away_team_id        = md.away_team_id,
            away_team           = md.away_team,
            all_away_team       = md.all_away_team,
            odds_h              = md.odds_h,
            odds_d              = md.odds_d,
            odds_a              = md.odds_a,
            goal_line           = md.goal_line,
            betting_single      = md.betting_single,
            sections_no1        = md.sections_no1,
            sections_no999      = md.sections_no999,
            win_flag            = md.win_flag,
            match_result_status = md.match_result_status,
            pool_status         = md.pool_status,
            result_status       = md.result_status,
        )

    def update_result(self, md: MatchData) -> None:
        """
        更新赛果字段（用于比赛结束后补录比分）。
        只更新可变结果字段，不覆盖基础信息。
        """
        self.sections_no1        = md.sections_no1
        self.sections_no999      = md.sections_no999
        self.win_flag            = md.win_flag
        self.match_result_status = md.match_result_status
        self.pool_status         = md.pool_status
        self.result_status       = md.result_status

    def to_dict_with_cn_keys(self) -> dict:
        """
        将 ORM 对象转为中文 key 字典，供 UI 层展示使用。
        中文映射来自 FIELD_MAPPING。
        """
        raw = {
            "matchId":      self.match_id,
            "matchDate":    self.match_date,
            "matchNumStr":  self.match_num_str,
            "leagueName":   self.league_name,
            "homeTeam":     self.home_team,
            "awayTeam":     self.away_team,
            "h":            self.odds_h,
            "d":            self.odds_d,
            "a":            self.odds_a,
            "goalLine":     self.goal_line,
            "sectionsNo999": self.sections_no999,
            "winFlag":      self.win_flag,
        }
        return {FIELD_MAPPING.get(k, k): v for k, v in raw.items()}
