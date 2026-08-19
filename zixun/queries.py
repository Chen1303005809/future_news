"""面板数据查询函数（参数化、防注入）。

variety 字段在库里是逗号分隔多值（如 "cokingcoal,coke"）。
- 按品种过滤时用 LIKE 匹配任一品种。
- 按品种聚合（密度图/分布）时取"主品种"（逗号前第一个）归类，避免重复计数。
"""
from __future__ import annotations

import datetime

from .storage import get_conn


def _build_where(
    variety: list[str] | None = None,
    report_type: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    keyword: str | None = None,
) -> tuple[str, list]:
    clauses: list[str] = ["1=1"]
    params: list = []

    if variety:
        ors = ["variety LIKE ?" for _ in variety]
        clauses.append("(" + " OR ".join(ors) + ")")
        params += [f"%{v}%" for v in variety]

    if report_type:
        clauses.append(f"report_type IN ({','.join('?' * len(report_type))})")
        params += report_type

    if date_from:
        clauses.append("publish_time >= ?")
        params.append(date_from)

    if date_to:
        end = date_to + " 23:59:59" if len(date_to) == 10 else date_to
        clauses.append("publish_time <= ?")
        params.append(end)

    if keyword:
        clauses.append(
            "(title LIKE ? OR body_text LIKE ? OR ai_summary LIKE ?)"
        )
        k = f"%{keyword}%"
        params += [k, k, k]

    return " AND ".join(clauses), params


def list_articles(
    *,
    variety: list[str] | None = None,
    report_type: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    keyword: str | None = None,
    limit: int = 300,
) -> list[dict]:
    where, params = _build_where(
        variety, report_type, date_from, date_to, keyword
    )
    sql = f"""
        SELECT id, url, title, variety, report_type, source_channel, source_id,
               publish_time, ai_summary,
               observation_start, observation_end, event_time, available_at,
               event_type, event_key, information_increment, price_echo,
               conclusion_delay_hours,
               substr(body_text, 1, 160) AS preview,
               length(body_text) AS body_len
        FROM articles WHERE {where}
        ORDER BY publish_time DESC LIMIT ?
    """
    conn = get_conn()
    try:
        rows = conn.execute(sql, params + [limit]).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_by_day(
    *,
    variety: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """按日 × 主品种 的文章数。"""
    where, params = _build_where(
        variety, None, date_from, date_to, None
    )
    where += " AND publish_time IS NOT NULL"
    sql = f"""
        SELECT substr(publish_time,1,10) AS day, variety, COUNT(*) AS n
        FROM articles WHERE {where}
        GROUP BY day, variety
        ORDER BY day
    """
    conn = get_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    agg: dict[tuple[str, str], int] = {}
    for r in rows:
        primary = (r["variety"] or "unknown").split(",")[0]
        key = (r["day"], primary)
        agg[key] = agg.get(key, 0) + r["n"]
    return [
        {"day": d, "variety": v, "count": c}
        for (d, v), c in sorted(agg.items())
    ]


def count_by_variety(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, int]:
    where, params = _build_where(None, None, date_from, date_to, None)
    sql = f"SELECT variety FROM articles WHERE {where}"
    conn = get_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    agg: dict[str, int] = {}
    for r in rows:
        primary = (r["variety"] or "unknown").split(",")[0]
        agg[primary] = agg.get(primary, 0) + 1
    return agg


def count_total(
    *,
    variety: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> int:
    where, params = _build_where(
        variety, None, date_from, date_to, None
    )
    sql = f"SELECT COUNT(*) AS n FROM articles WHERE {where}"
    conn = get_conn()
    try:
        return conn.execute(sql, params).fetchone()["n"]
    finally:
        conn.close()


def count_today() -> int:
    today = datetime.date.today().isoformat()
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM articles WHERE publish_time >= ?",
            (today,),
        ).fetchone()["n"]
    finally:
        conn.close()


def get_article(article_id: int) -> dict | None:
    conn = get_conn()
    try:
        r = conn.execute(
            "SELECT * FROM articles WHERE id = ?", (article_id,)
        ).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()
