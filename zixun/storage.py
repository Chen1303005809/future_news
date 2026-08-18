"""SQLite 存储与 Markdown 导出。"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

from .parser import ArticleData
from .settings import ARTICLES_DIR, DATA_DIR, DB_PATH

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
  body_text      TEXT
);
CREATE INDEX IF NOT EXISTS idx_variety_time ON articles(variety, publish_time);
CREATE INDEX IF NOT EXISTS idx_time         ON articles(publish_time);
CREATE INDEX IF NOT EXISTS idx_report       ON articles(report_type, publish_time);
CREATE INDEX IF NOT EXISTS idx_url_hash     ON articles(url_hash);
"""


def get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    conn = get_conn(db_path)
    conn.executescript(SCHEMA)
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
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        """INSERT OR IGNORE INTO articles
           (url, url_hash, title, variety, report_type, source_channel, source_id,
            publish_time, fetched_at, ai_summary, body_text)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            article.url, h, article.title, ",".join(variety),
            report_type, source_channel, source_id,
            article.publish_time, now, article.ai_summary,
            article.body_text,
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
