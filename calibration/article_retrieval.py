"""资讯检索：以 origin_timestamp 为上界、往前回溯 N 天，复用 zixun.queries。

时间窗口设计：
    上界 date_to = origin_timestamp（context 末根 bar 时刻，夜盘常落在 22:00）。
    下界 date_from = origin_timestamp - lookback_days。
严格 ``publish_time <= origin_timestamp`` 避免引入未来资讯（信息泄露）。

origin_timestamp 可能是 ISO（``2026-08-13T22:00:00``）或带时区，统一归一成
``YYYY-MM-DD HH:MM:SS``（与 SQLite DATETIME 存储格式一致，字符串比较正确）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from zixun import queries

from .instrument_mapping import BLACK_SECTOR_FALLBACK


@dataclass(frozen=True)
class ArticleDigest:
    """喂给 prompt 的单条资讯摘要。"""

    id: int
    publish_time: str
    title: str
    report_type: str | None
    ai_summary: str
    preview: str


@dataclass(frozen=True)
class RetrievalMeta:
    """检索元信息（用于来源追溯与审计）。"""

    variety_queried: tuple[str, ...]   # 实际查询用的 variety 列表
    variety_fallback: bool             # 是否触发了黑色系通用兜底
    date_from: str
    date_to: str
    total_found: int                   # 去掉空信号前的命中数


def _normalize_timestamp(ts: str) -> str:
    """ISO / 带时区时间戳 → 'YYYY-MM-DD HH:MM:SS'。

    SQLite DATETIME 字符串比较要求空格分隔。origin_timestamp 来自 pd.Timestamp
    序列化，形如 '2026-08-13T22:00:00' 或 '2026-08-13 22:00:00+00:00'。
    """
    s = str(ts).strip()
    # 去掉时区后缀：若带时区，先解析再按本地（naive）格式化
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s[:19], fmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    # 兜底：直接替换 T 为空格并截到秒
    return s.replace("T", " ")[:19]


def _row_to_digest(row: dict) -> ArticleDigest:
    return ArticleDigest(
        id=int(row["id"]),
        publish_time=str(row.get("publish_time") or ""),
        title=str(row.get("title") or "").strip(),
        report_type=(row.get("report_type") or None),
        ai_summary=str(row.get("ai_summary") or "").strip(),
        preview=str(row.get("preview") or "").strip(),
    )


def _has_signal(digest: ArticleDigest) -> bool:
    """信号预过滤：ai_summary 与正文预览都过短则丢弃（信号密度太低）。"""
    text = (digest.ai_summary + digest.preview).strip()
    return len(text) >= 10


def retrieve_articles(
    *,
    variety: str | None,
    origin_timestamp: str,
    lookback_days: int,
    max_articles: int,
    fallback_to_black_sector: bool = True,
    over_fetch: int = 5,
) -> tuple[list[ArticleDigest], RetrievalMeta]:
    """检索时间窗口内相关资讯。

    variety 为 None（未知合约前缀）时直接返回空——调用方应据此退出（退出码 2）。

    over_fetch：多取若干条用于信号预过滤后的补偿，保证过滤后仍接近 max_articles。
    """
    date_to = _normalize_timestamp(origin_timestamp)
    dt_to = datetime.strptime(date_to, "%Y-%m-%d %H:%M:%S")
    date_from = (dt_to - timedelta(days=lookback_days)).strftime("%Y-%m-%d %H:%M:%S")

    if variety is None:
        return [], RetrievalMeta(
            variety_queried=(),
            variety_fallback=False,
            date_from=date_from,
            date_to=date_to,
            total_found=0,
        )

    # 第一次查询：目标品种
    rows = queries.list_articles(
        variety=[variety],
        date_from=date_from,
        date_to=date_to,
        priority="core",
        limit=max_articles + over_fetch,
    )

    variety_queried: tuple[str, ...] = (variety,)
    variety_fallback = False

    # 目标品种命中不足 & 允许兜底 → 退到黑色系通用
    if len(rows) == 0 and fallback_to_black_sector:
        rows = queries.list_articles(
            variety=list(BLACK_SECTOR_FALLBACK),
            date_from=date_from,
            date_to=date_to,
            priority="core",
            limit=max_articles + over_fetch,
        )
        variety_queried = BLACK_SECTOR_FALLBACK
        variety_fallback = True

    total_found = len(rows)
    digests = [_row_to_digest(r) for r in rows]
    digests = [d for d in digests if _has_signal(d)]
    digests = digests[:max_articles]  # list_articles 已按 publish_time DESC，这里二次截断

    meta = RetrievalMeta(
        variety_queried=variety_queried,
        variety_fallback=variety_fallback,
        date_from=date_from,
        date_to=date_to,
        total_found=total_found,
    )
    return digests, meta
