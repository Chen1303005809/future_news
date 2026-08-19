"""集中式资讯—预测时间语义。

这个模块是资讯检索、K 线产物和校准 prompt 共同使用的时间 seam。所有可比较
的时间都在这里变成带 ``Asia/Shanghai`` 时区的 aware ``datetime``；数据库中
保留的旧 ``YYYY-MM-DD HH:MM:SS`` 字符串只在 SQL 查询边界使用。

重要约定：

* ``T`` 是本项目小时 K 线的 bar 起始标签，``C`` 是该 bar 完成时的 close；
  因而 14:00 bar 的收盘时点是 15:00。
* ``available_at`` 默认等于 ``publish_time``，且永远不会早于它。
* 文章能影响的端点由 ``target_close_at >= available_at`` 决定；可选影响
  窗口只来自配置，不在调用方凭感觉写死。
* 观察区间和事件发生时间只接受已有结构化字段/明确元数据，不从正文推断。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
DEFAULT_BAR_DURATION = timedelta(hours=1)


def parse_shanghai_datetime(
    value: Any,
    *,
    required: bool = False,
) -> datetime | None:
    """Parse a date/time as an aware ``Asia/Shanghai`` datetime.

    Existing SQLite rows and Mysteel pages use naive local strings. They are
    interpreted as Shanghai time at this single seam. An explicitly zoned
    value is converted to Shanghai rather than having its offset discarded.
    """
    if value is None or value == "":
        if required:
            raise ValueError("missing datetime")
        return None

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    else:
        # pandas.Timestamp and similar objects expose to_pydatetime without
        # making this shared module depend on pandas.
        to_python = getattr(value, "to_pydatetime", None)
        if callable(to_python):
            parsed = to_python()
        else:
            text = str(value).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            text = text.replace("/", "-")
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                # The provider and old DB occasionally omit seconds.
                parsed = None
                for fmt in (
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%d %H:%M",
                    "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%dT%H:%M",
                    "%Y-%m-%d",
                ):
                    try:
                        parsed = datetime.strptime(text, fmt)
                        break
                    except ValueError:
                        continue
                if parsed is None:
                    if required:
                        raise ValueError(f"invalid datetime: {value!r}")
                    return None

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    else:
        parsed = parsed.astimezone(SHANGHAI)
    return parsed


def format_shanghai_datetime(value: Any) -> str | None:
    """Return a canonical, explicit ``...+08:00`` ISO timestamp."""
    parsed = parse_shanghai_datetime(value)
    return parsed.isoformat(timespec="seconds") if parsed else None


def format_sql_local_datetime(value: Any) -> str | None:
    """Format an aware time for legacy SQLite ``DATETIME`` comparisons."""
    parsed = parse_shanghai_datetime(value)
    return parsed.strftime("%Y-%m-%d %H:%M:%S") if parsed else None


def bar_close_time(
    bar_start: Any,
    *,
    duration: timedelta = DEFAULT_BAR_DURATION,
) -> datetime:
    """Return the real close time represented by a provider bar label."""
    parsed = parse_shanghai_datetime(bar_start, required=True)
    return parsed + duration


@dataclass(frozen=True)
class AlignmentPolicy:
    """Configurable event impact policy.

    ``None`` means that an article may reach every explicit future target
    endpoint in the current forecast horizon. This is deliberately the safe
    default for first-disclosure events: a shorter window must be learned from
    historical event studies and then supplied in configuration.
    """

    timezone_name: str = "Asia/Shanghai"
    bar_duration: timedelta = DEFAULT_BAR_DURATION
    impact_window_hours: Mapping[str, float | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timezone_name != "Asia/Shanghai":
            raise ValueError("only Asia/Shanghai is supported by the project")
        for event_type, hours in self.impact_window_hours.items():
            if hours is not None and hours <= 0:
                raise ValueError(
                    f"impact window for {event_type!r} must be positive or null"
                )

    def window_for(self, event_type: str) -> timedelta | None:
        if event_type in self.impact_window_hours:
            hours = self.impact_window_hours[event_type]
        else:
            hours = self.impact_window_hours.get("default")
        return timedelta(hours=hours) if hours is not None else None


@dataclass(frozen=True)
class ForecastEndpoint:
    """One real predicted close endpoint, not a natural-day placeholder."""

    index: int
    day: int
    trading_day: str
    target_close_at: datetime
    bar_index: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "target_close_at",
            parse_shanghai_datetime(self.target_close_at, required=True),
        )

    @property
    def target_close_at_iso(self) -> str:
        return self.target_close_at.isoformat(timespec="seconds")


@dataclass(frozen=True)
class ArticleTiming:
    """Timing metadata derived from an article row without inventing facts."""

    id: int | None
    title: str
    report_type: str | None
    source_id: str | None
    publish_time: datetime | None
    available_at: datetime | None
    observation_start: datetime | None
    observation_end: datetime | None
    event_time: datetime | None
    event_type: str
    price_echo: bool
    conclusion_delay_hours: float | None
    event_key: str | None
    information_increment: bool | None

    @property
    def conclusion_delay(self) -> timedelta | None:
        if self.conclusion_delay_hours is None:
            return None
        return timedelta(hours=self.conclusion_delay_hours)

    @property
    def abstain_recommended(self) -> bool:
        return self.price_echo and self.information_increment is not True


@dataclass(frozen=True)
class ArticleAlignment:
    """Article timing plus its forecast-specific eligibility."""

    timing: ArticleTiming
    forecast_origin: datetime
    effective_age_hours: float | None
    eligible_endpoint_indices: tuple[int, ...]
    eligible_target_close_at: tuple[str, ...]
    abstain_reason: str | None = None

    def __getattr__(self, name: str) -> Any:
        # Keeps the useful timing fields ergonomic for callers/tests while
        # retaining one immutable nested timing object as the implementation.
        if hasattr(self.timing, name):
            return getattr(self.timing, name)
        raise AttributeError(name)

    @property
    def publish_time_iso(self) -> str | None:
        return format_shanghai_datetime(self.timing.publish_time)

    @property
    def available_at_iso(self) -> str | None:
        return format_shanghai_datetime(self.timing.available_at)

    @property
    def effective_age(self) -> timedelta | None:
        if self.effective_age_hours is None:
            return None
        return timedelta(hours=self.effective_age_hours)

    @property
    def observation_start_iso(self) -> str | None:
        return format_shanghai_datetime(self.timing.observation_start)

    @property
    def observation_end_iso(self) -> str | None:
        return format_shanghai_datetime(self.timing.observation_end)

    @property
    def event_time_iso(self) -> str | None:
        return format_shanghai_datetime(self.timing.event_time)


def _as_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "是", "有"}:
        return True
    if text in {"0", "false", "no", "n", "否", "无"}:
        return False
    return None


def _event_type(row: Mapping[str, Any], title: str) -> str:
    explicit = str(row.get("event_type") or "").strip()
    if explicit:
        return explicit
    report_type = str(row.get("report_type") or "").strip().lower()
    source_id = str(row.get("source_id") or "").strip().lower()
    text = f"{title} {row.get('ai_summary') or ''} {row.get('preview') or ''}"

    if report_type in {"daily", "analysis"} or any(
        word in text for word in ("复盘", "行情回顾", "早报", "午报", "晚报", "收盘")
    ):
        return "daily_recap"
    if report_type in {"weekly", "monthly"}:
        return f"{report_type}_data"
    if source_id == "black_market_flash":
        return "flash_event"
    if any(word in text for word in ("事故", "灾害", "爆炸")):
        return "accident"
    if any(word in text for word in ("运输中断", "铁路中断", "港口中断", "闭关", "通关")):
        return "transport_disruption"
    if any(word in text for word in ("政策", "通知", "正式发布", "环保")):
        return "policy"
    if any(word in text for word in ("停产", "复产", "检修", "限产", "减产")):
        return "production_event"
    if any(word in text for word in ("提涨", "提降", "调价", "结算价")):
        return "price_adjustment"
    if any(word in text for word in ("发运", "离港", "到港")):
        return "shipment_arrival"
    if any(word in text for word in ("库存", "产量", "开工", "产能利用率", "调研")):
        return "supply_data"
    return report_type or "unknown"


def _price_echo(row: Mapping[str, Any], title: str, report_type: str | None) -> bool:
    explicit = _as_bool(row.get("price_echo"))
    if explicit is not None:
        return explicit
    text = f"{title} {row.get('ai_summary') or ''} {row.get('preview') or ''}"
    recap_words = (
        "复盘", "行情回顾", "收盘", "盘面", "早报", "午报", "晚报", "今日价格",
        "价格涨跌", "走势回顾", "市场回顾",
    )
    if report_type in {"daily", "analysis"}:
        return True
    return any(word in text for word in recap_words)


_EVENT_KEY_NOISE = re.compile(r"(?:mysteel|快讯|跟踪|后续|最新|消息|：|:|\s+)")


def _event_key(row: Mapping[str, Any], title: str, event_type: str) -> str | None:
    explicit = str(row.get("event_key") or "").strip()
    if explicit:
        return explicit
    if event_type in {"unknown", "daily_recap", "weekly_data", "monthly_data", "supply_data"}:
        return None
    key = _EVENT_KEY_NOISE.sub("", title).lower()
    return key or None


def article_timing_from_row(row: Mapping[str, Any]) -> ArticleTiming:
    """Build timing only from stored/explicit fields and publish metadata."""
    title = str(row.get("title") or "").strip()
    report_type = str(row.get("report_type") or "").strip() or None
    publish_time = parse_shanghai_datetime(row.get("publish_time"))
    explicit_available = parse_shanghai_datetime(row.get("available_at"))
    available_at = explicit_available or publish_time
    if publish_time and available_at and available_at < publish_time:
        # A bad/old metadata value cannot make information available before
        # Mysteel says it was published.
        available_at = publish_time

    observation_start = parse_shanghai_datetime(row.get("observation_start"))
    observation_end = parse_shanghai_datetime(row.get("observation_end"))
    event_time = parse_shanghai_datetime(row.get("event_time"))
    event_type = _event_type(row, title)
    increment = _as_bool(row.get("information_increment"))
    delay = None
    if publish_time and observation_end:
        delay = (publish_time - observation_end).total_seconds() / 3600.0

    return ArticleTiming(
        id=int(row["id"]) if row.get("id") is not None else None,
        title=title,
        report_type=report_type,
        source_id=(str(row.get("source_id")) if row.get("source_id") else None),
        publish_time=publish_time,
        available_at=available_at,
        observation_start=observation_start,
        observation_end=observation_end,
        event_time=event_time,
        event_type=event_type,
        price_echo=_price_echo(row, title, report_type),
        conclusion_delay_hours=delay,
        event_key=_event_key(row, title, event_type),
        information_increment=increment,
    )


def align_article(
    row: Mapping[str, Any] | ArticleTiming,
    *,
    forecast_origin: Any,
    endpoints: Sequence[ForecastEndpoint],
    policy: AlignmentPolicy = AlignmentPolicy(),
) -> ArticleAlignment:
    """Align one article to a forecast without allowing future information."""
    timing = row if isinstance(row, ArticleTiming) else article_timing_from_row(row)
    origin = parse_shanghai_datetime(forecast_origin, required=True)
    if timing.publish_time is None:
        return ArticleAlignment(timing, origin, None, (), (), "missing_publish_time")
    if timing.available_at is None:
        return ArticleAlignment(timing, origin, None, (), (), "missing_available_at")
    if timing.available_at > origin:
        return ArticleAlignment(timing, origin, None, (), (), "available_after_forecast_origin")

    age_hours = (origin - timing.available_at).total_seconds() / 3600.0
    window = policy.window_for(timing.event_type)
    latest = timing.available_at + window if window else None
    eligible: list[ForecastEndpoint] = []
    for item in endpoints:
        close_at = parse_shanghai_datetime(item.target_close_at, required=True)
        if close_at < timing.available_at:
            continue
        if latest is not None and close_at > latest:
            continue
        eligible.append(item)

    reason = None if eligible else "no_eligible_endpoint"
    return ArticleAlignment(
        timing=timing,
        forecast_origin=origin,
        effective_age_hours=age_hours,
        eligible_endpoint_indices=tuple(item.index for item in eligible),
        eligible_target_close_at=tuple(item.target_close_at_iso for item in eligible),
        abstain_reason=reason,
    )


def deduplicate_event_alignments(
    alignments: Sequence[ArticleAlignment],
) -> list[ArticleAlignment]:
    """Keep first disclosure; keep a later row only when it states an increment."""
    ordered = sorted(
        alignments,
        key=lambda item: (
            item.timing.available_at or datetime.max.replace(tzinfo=SHANGHAI),
            item.timing.id if item.timing.id is not None else 0,
        ),
    )
    seen: set[str] = set()
    kept: list[ArticleAlignment] = []
    for item in ordered:
        key = item.timing.event_key
        if key and key in seen and item.timing.information_increment is not True:
            continue
        if key:
            seen.add(key)
        kept.append(item)
    return kept


def endpoint_from_bar(
    *,
    index: int,
    day: int,
    trading_day: Any,
    bar_start: Any,
    bar_index: int | None = None,
    policy: AlignmentPolicy = AlignmentPolicy(),
) -> ForecastEndpoint:
    """Create an endpoint from an hourly K-line bar's start label."""
    parsed_day = parse_shanghai_datetime(trading_day, required=True)
    return ForecastEndpoint(
        index=index,
        day=day,
        trading_day=parsed_day.date().isoformat(),
        target_close_at=bar_close_time(bar_start, duration=policy.bar_duration),
        bar_index=bar_index,
    )


__all__ = [
    "AlignmentPolicy",
    "ArticleAlignment",
    "ArticleTiming",
    "DEFAULT_BAR_DURATION",
    "ForecastEndpoint",
    "SHANGHAI",
    "align_article",
    "article_timing_from_row",
    "bar_close_time",
    "deduplicate_event_alignments",
    "endpoint_from_bar",
    "format_shanghai_datetime",
    "format_sql_local_datetime",
    "parse_shanghai_datetime",
]
