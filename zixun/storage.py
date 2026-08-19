"""SQLite 存储与 Markdown 导出。"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

from .parser import ArticleData
from .settings import ARTICLES_DIR, DATA_DIR, DB_PATH
from .time_alignment import (
    SHANGHAI,
    article_timing_from_row,
    format_sql_local_datetime,
)


ARTICLE_METADATA_COLUMNS = {
    "observation_start": "TEXT",
    "observation_end": "TEXT",
    "event_time": "TEXT",
    "available_at": "TEXT",
    "event_type": "TEXT",
    "event_key": "TEXT",
    "information_increment": "INTEGER",
    "price_echo": "INTEGER",
    "conclusion_delay_hours": "REAL",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  url            TEXT NOT NULL,
  url_hash       TEXT UNIQUE NOT NULL,
  title          TEXT NOT NULL,
  variety        TEXT NOT NULL,
  report_type    TEXT,
  source_channel TEXT,
  source_id      TEXT,
  publish_time   DATETIME,
  fetched_at     DATETIME NOT NULL,
  ai_summary     TEXT,
  body_text      TEXT,
  observation_start TEXT,
  observation_end TEXT,
  event_time     TEXT,
  available_at   TEXT,
  event_type     TEXT,
  event_key      TEXT,
  information_increment INTEGER,
  price_echo     INTEGER,
  conclusion_delay_hours REAL
);
CREATE INDEX IF NOT EXISTS idx_variety_time ON articles(variety, publish_time);
CREATE INDEX IF NOT EXISTS idx_time         ON articles(publish_time);
CREATE INDEX IF NOT EXISTS idx_report       ON articles(report_type, publish_time);
CREATE INDEX IF NOT EXISTS idx_url_hash     ON articles(url_hash);
CREATE INDEX IF NOT EXISTS idx_available_time ON articles(available_at);
"""


def _ensure_article_metadata_columns(conn: sqlite3.Connection) -> None:
    """Add only nullable metadata columns; never rewrite/drop legacy columns."""
    existing = {
        row[1] for row in conn.execute("PRAGMA table_info(articles)").fetchall()
    }
    for name, sql_type in ARTICLE_METADATA_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE articles ADD COLUMN {name} {sql_type}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_available_time ON articles(available_at)"
    )
    conn.commit()


def get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    # Existing deployments may have a retired tier column and none of the
    # timing columns. Migration is additive and does not read/remove old data.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            url_hash TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            variety TEXT NOT NULL,
            report_type TEXT,
            source_channel TEXT,
            source_id TEXT,
            publish_time DATETIME,
            fetched_at DATETIME NOT NULL,
            ai_summary TEXT,
            body_text TEXT
        )"""
    )
    _ensure_article_metadata_columns(conn)
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    conn = get_conn(db_path)
    conn.executescript(SCHEMA)
    _ensure_article_metadata_columns(conn)
    conn.commit()
    conn.close()


def hash_url(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def exists_url_hash(conn: sqlite3.Connection, h: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM articles WHERE url_hash=? LIMIT 1", (h,)
    ).fetchone()
    return row is not None


def insert_article(
    conn: sqlite3.Connection,
    *,
    article: ArticleData,
    variety: list[str],
    report_type: str,
    source_channel: str,
    source_id: str,
) -> bool:
    """入库一篇文章。已存在则跳过。返回是否新增。"""
    h = hash_url(article.url)
    now = datetime.now(SHANGHAI).isoformat(timespec="seconds")
    timing = article_timing_from_row(
        {
            "title": article.title,
            "report_type": report_type,
            "source_id": source_id,
            "publish_time": article.publish_time,
            "observation_start": article.observation_start,
            "observation_end": article.observation_end,
            "event_time": article.event_time,
            "available_at": article.available_at,
            "event_type": article.event_type,
            "event_key": article.event_key,
            "information_increment": article.information_increment,
            "price_echo": article.price_echo,
        }
    )
    cur = conn.execute(
        """INSERT OR IGNORE INTO articles
           (url, url_hash, title, variety, report_type, source_channel, source_id,
            publish_time, fetched_at, ai_summary, body_text,
            observation_start, observation_end, event_time, available_at,
            event_type, event_key, information_increment, price_echo,
            conclusion_delay_hours)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            article.url, h, article.title, ",".join(variety),
            report_type, source_channel, source_id,
            article.publish_time, now, article.ai_summary, article.body_text,
            format_sql_local_datetime(timing.observation_start),
            format_sql_local_datetime(timing.observation_end),
            format_sql_local_datetime(timing.event_time),
            format_sql_local_datetime(timing.available_at),
            timing.event_type,
            timing.event_key,
            (None if timing.information_increment is None else int(timing.information_increment)),
            int(timing.price_echo),
            timing.conclusion_delay_hours,
        ),
    )
    conn.commit()
    return cur.rowcount > 0


def export_markdown(
    article: ArticleData,
    *,
    variety: list[str],
    report_type: str,
    source_channel: str,
    base_dir: Path = ARTICLES_DIR,
) -> Path | None:
    """把文章导出为 Markdown 文件，路径 articles/<品种>/<日期>/<slug>.md。"""
    if not article.publish_time:
        # 无时间戳的文章不导出（基本信息不全）
        return None
    date_str = article.publish_time[:10]
    primary_variety = variety[0] if variety else "unknown"
    folder = base_dir / primary_variety / date_str
    folder.mkdir(parents=True, exist_ok=True)

    slug = article.url.rsplit("/", 1)[-1].replace(".html", "")
    path = folder / f"{slug}.md"

    variety_disp = "/".join(variety)
    lines = [
        f"# {article.title}",
        "",
        f"- 品种: {variety_disp}",
        f"- 类型: {report_type}",
        f"- 频道: {source_channel}",
        f"- 发布时间: {article.publish_time}",
        f"- 来源: {article.source or '我的钢铁网'}",
        f"- 原文: {article.url}",
        "",
    ]
    if article.ai_summary:
        lines += ["## AI 摘要", "", article.ai_summary, ""]
    lines += ["## 正文", "", article.body_text or "（正文为空）", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
