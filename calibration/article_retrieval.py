"""Forecast-origin-safe Mysteel article retrieval.

The SQL window is only a coarse performance filter. The authoritative
eligibility check is the centralized ``zixun.time_alignment`` module, which
compares aware Asia/Shanghai ``available_at`` values with the explicit real
close endpoints from ``forecast_result.json``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from zixun import queries
from zixun.time_alignment import (
    AlignmentPolicy,
    ArticleAlignment,
    ForecastEndpoint,
    align_article,
    article_timing_from_row,
    deduplicate_event_alignments,
    format_shanghai_datetime,
    format_sql_local_datetime,
    parse_shanghai_datetime,
)

from .instrument_mapping import BLACK_SECTOR_FALLBACK


@dataclass(frozen=True)
class ArticleDigest:
    """One article with all forecast-specific timing metadata for the prompt."""

    # Legacy fields retained for callers and old calibration artifacts.
    id: int
    publish_time: str
    title: str
    report_type: str | None
    ai_summary: str
    preview: str

    # Explicit timezone/availability audit fields.
    publish_time_at: str | None = None
    available_at: str | None = None
    observation_start: str | None = None
    observation_end: str | None = None
    event_time: str | None = None
    effective_age_hours: float | None = None
    eligible_endpoint_indices: tuple[int, ...] = ()
    eligible_target_close_at: tuple[str, ...] = ()
    event_type: str = "unknown"
    price_echo: bool = False
    conclusion_delay_hours: float | None = None
    # Alias with the domain field name; value is hours for JSON/prompt safety.
    conclusion_delay: float | None = None
    event_key: str | None = None
    abstain_recommended: bool = False

    @classmethod
    def from_alignment(
        cls,
        alignment: ArticleAlignment,
        *,
        ai_summary: str = "",
        preview: str = "",
    ) -> "ArticleDigest":
        timing = alignment.timing
        return cls(
            id=int(timing.id or 0),
            publish_time=format_sql_local_datetime(timing.publish_time) or "",
            title=timing.title,
            report_type=timing.report_type,
            ai_summary=ai_summary.strip(),
            preview=preview.strip(),
            publish_time_at=alignment.publish_time_iso,
            available_at=alignment.available_at_iso,
            observation_start=alignment.observation_start_iso,
            observation_end=alignment.observation_end_iso,
            event_time=alignment.event_time_iso,
            effective_age_hours=alignment.effective_age_hours,
            eligible_endpoint_indices=alignment.eligible_endpoint_indices,
            eligible_target_close_at=alignment.eligible_target_close_at,
            event_type=timing.event_type,
            price_echo=timing.price_echo,
            conclusion_delay_hours=timing.conclusion_delay_hours,
            conclusion_delay=timing.conclusion_delay_hours,
            event_key=timing.event_key,
            abstain_recommended=timing.abstain_recommended,
        )


@dataclass(frozen=True)
class RetrievalMeta:
    """Retrieval and time-alignment audit metadata."""

    variety_queried: tuple[str, ...]
    variety_fallback: bool
    date_from: str
    date_to: str
    total_found: int
    forecast_origin: str | None = None
    target_close_at: tuple[str, ...] = ()
    excluded_after_origin: int = 0
    duplicates_removed: int = 0
    abstain_reason: str | None = None


def _normalize_timestamp(ts: Any) -> str:
    """Normalize any input timestamp to the legacy local SQL format."""
    parsed = parse_shanghai_datetime(ts, required=True)
    return format_sql_local_datetime(parsed)  # type: ignore[return-value]


def _row_to_alignment(
    row: Mapping[str, Any],
    *,
    forecast_origin: datetime,
    endpoints: Sequence[ForecastEndpoint] | None,
    policy: AlignmentPolicy,
) -> ArticleAlignment | None:
    if endpoints is None:
        # Compatibility path for old direct callers. It still enforces the
        # forecast-origin information barrier; production CLI always supplies
        # concrete endpoints and therefore gets endpoint-level mapping.
        timing = article_timing_from_row(row)
        if timing.publish_time is None or timing.available_at is None:
            return align_article(
                timing,
                forecast_origin=forecast_origin,
                endpoints=(),
                policy=policy,
            )
        if timing.available_at > forecast_origin:
            return align_article(
                timing,
                forecast_origin=forecast_origin,
                endpoints=(),
                policy=policy,
            )
        return ArticleAlignment(
            timing=timing,
            forecast_origin=forecast_origin,
            effective_age_hours=(forecast_origin - timing.available_at).total_seconds() / 3600.0,
            eligible_endpoint_indices=(),
            eligible_target_close_at=(),
        )
    return align_article(
        row,
        forecast_origin=forecast_origin,
        endpoints=endpoints,
        policy=policy,
    )


def _aligned_digests(
    rows: Sequence[Mapping[str, Any]],
    *,
    forecast_origin: datetime,
    endpoints: Sequence[ForecastEndpoint] | None,
    policy: AlignmentPolicy,
) -> tuple[list[ArticleDigest], int, int, int]:
    alignments: list[ArticleAlignment] = []
    excluded_after_origin = 0
    missing_or_unusable = 0
    for row in rows:
        alignment = _row_to_alignment(
            row,
            forecast_origin=forecast_origin,
            endpoints=endpoints,
            policy=policy,
        )
        if alignment is None:
            missing_or_unusable += 1
            continue
        if alignment.abstain_reason == "available_after_forecast_origin":
            excluded_after_origin += 1
            continue
        if alignment.abstain_reason in {"missing_publish_time", "missing_available_at"}:
            missing_or_unusable += 1
            continue
        # With explicit endpoints an article that cannot reach any future
        # endpoint is not a calibration input. Without endpoints, the legacy
        # compatibility path intentionally retains it for old callers.
        if endpoints is not None and alignment.abstain_reason:
            missing_or_unusable += 1
            continue
        alignments.append(alignment)

    deduped = deduplicate_event_alignments(alignments)
    duplicate_count = len(alignments) - len(deduped)
    digests: list[ArticleDigest] = []
    for alignment in deduped:
        row = next(
            (
                item
                for item in rows
                if item.get("id") is not None
                and alignment.timing.id is not None
                and int(item["id"]) == alignment.timing.id
            ),
            {},
        )
        digest = ArticleDigest.from_alignment(
            alignment,
            ai_summary=str(row.get("ai_summary") or ""),
            preview=str(row.get("preview") or ""),
        )
        if _has_signal(digest):
            digests.append(digest)
    # Keep first disclosure during deduplication, then prioritize the newest
    # independent signals within the prompt size budget (legacy behavior was
    # newest-first as well).
    digests.sort(key=lambda item: (item.available_at or "", item.id), reverse=True)
    return digests, excluded_after_origin, duplicate_count, missing_or_unusable


def _has_signal(digest: ArticleDigest) -> bool:
    """Drop empty rows but keep explicit metadata-only events."""
    text = (digest.ai_summary + digest.preview).strip()
    return len(text) >= 10 or bool(digest.event_type not in {"unknown", "daily_recap"})


def _coerce_endpoints(
    target_close_at: Sequence[Any] | None,
) -> tuple[ForecastEndpoint, ...] | None:
    if target_close_at is None:
        return None
    result: list[ForecastEndpoint] = []
    for index, value in enumerate(target_close_at):
        parsed = parse_shanghai_datetime(value, required=True)
        result.append(
            ForecastEndpoint(
                index=index,
                day=index + 1,
                trading_day=parsed.date().isoformat(),
                target_close_at=parsed,
            )
        )
    return tuple(result)


def retrieve_articles(
    *,
    variety: str | None,
    origin_timestamp: str,
    lookback_days: int,
    max_articles: int,
    fallback_to_black_sector: bool = True,
    over_fetch: int = 5,
    endpoints: Sequence[ForecastEndpoint] | None = None,
    target_close_at: Sequence[Any] | None = None,
    alignment_policy: AlignmentPolicy = AlignmentPolicy(),
) -> tuple[list[ArticleDigest], RetrievalMeta]:
    """Retrieve only articles available at ``forecast_origin``.

    ``endpoints`` is the preferred interface. ``target_close_at`` is a small
    adapter for callers that only have three explicit close timestamps. When
    neither is provided, the legacy path still enforces the information barrier
    but cannot claim endpoint-level eligibility.
    """
    forecast_origin = parse_shanghai_datetime(origin_timestamp, required=True)
    date_to = _normalize_timestamp(forecast_origin)
    date_from = format_sql_local_datetime(
        forecast_origin - timedelta(days=lookback_days)
    )
    endpoint_tuple = tuple(endpoints) if endpoints is not None else _coerce_endpoints(target_close_at)
    endpoint_strings = tuple(item.target_close_at_iso for item in endpoint_tuple or ())

    base_meta = {
        "date_from": date_from or "",
        "date_to": date_to,
        "forecast_origin": format_shanghai_datetime(forecast_origin),
        "target_close_at": endpoint_strings,
    }

    if variety is None:
        return [], RetrievalMeta(
            variety_queried=(),
            variety_fallback=False,
            total_found=0,
            abstain_reason="unknown_variety",
            **base_meta,
        )

    def query(queried: Sequence[str]) -> list[dict]:
        return queries.list_articles(
            variety=list(queried),
            date_from=date_from,
            date_to=date_to,
            limit=max_articles + over_fetch,
        )

    rows = query((variety,))
    variety_queried: tuple[str, ...] = (variety,)
    variety_fallback = False
    aligned, excluded, duplicates, unusable = _aligned_digests(
        rows,
        forecast_origin=forecast_origin,
        endpoints=endpoint_tuple,
        policy=alignment_policy,
    )

    if not aligned and fallback_to_black_sector:
        fallback_rows = query(BLACK_SECTOR_FALLBACK)
        fallback_aligned, fallback_excluded, fallback_duplicates, fallback_unusable = _aligned_digests(
            fallback_rows,
            forecast_origin=forecast_origin,
            endpoints=endpoint_tuple,
            policy=alignment_policy,
        )
        if fallback_aligned:
            rows = fallback_rows
            aligned = fallback_aligned
            excluded += fallback_excluded
            duplicates += fallback_duplicates
            unusable += fallback_unusable
            variety_queried = BLACK_SECTOR_FALLBACK
            variety_fallback = True

    total_found = len(rows)
    reason = None
    if not aligned:
        reason = "no_eligible_articles"
        if unusable and not any(row.get("publish_time") for row in rows):
            reason = "missing_publish_time"

    meta = RetrievalMeta(
        variety_queried=variety_queried,
        variety_fallback=variety_fallback,
        total_found=total_found,
        excluded_after_origin=excluded,
        duplicates_removed=duplicates,
        abstain_reason=reason,
        **base_meta,
    )
    return aligned[:max_articles], meta


__all__ = ["ArticleDigest", "RetrievalMeta", "retrieve_articles"]
