"""读取 Kronos ``forecast_result.json``，提取校准所需元数据 + 三日预测。

关键：``forecast_result.json`` 的 ``kronos`` 块经 ``_record_summary`` 白名单过滤
（见 kronos/three_day_json_forecast.py:947-955），**包含** ``day1/2/3_up_probability``
与 ``predicted_path`` / ``day_end_indices`` / ``origin_close``，但**不包含**
``day1/2/3_predicted_return``（仅回测时存于 ``metrics.three_day``）。

因此三日预测收益率需自行重算（与 ``_endpoint_returns`` 等价）：
    predicted_return[day] = predicted_path[day_end_indices[day-1]][3] / origin_close - 1
（close 是 MODEL_FEATURES=[open,high,low,close,volume,amount] 的第 3 列）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from zixun.time_alignment import (
    DEFAULT_BAR_DURATION,
    ForecastEndpoint,
    bar_close_time,
    format_shanghai_datetime,
    parse_shanghai_datetime,
)

CLOSE_FEATURE_INDEX = 3  # MODEL_FEATURES = [open, high, low, close, volume, amount]


class ForecastLoadError(Exception):
    """forecast_result.json 缺关键字段或结构非法。"""


@dataclass(frozen=True)
class DayForecast:
    """单日预测快照。"""

    day: int  # 1 / 2 / 3
    up_probability: float
    predicted_return: float
    # predicted_return 的来源：metrics(回测现成) | predicted_path | median_path | mean_path
    predicted_return_source: str
    target_close_at: datetime | None = None


@dataclass(frozen=True)
class ForecastSnapshot:
    """三日预测快照（校准输入）。"""

    instrument: str
    origin_timestamp: str          # ISO 字符串
    origin_trading_day: str
    origin_close: float
    target_days: tuple[str, ...]   # 3 个目标交易日
    days: tuple[DayForecast, DayForecast, DayForecast]
    target_close_at: tuple[datetime, datetime, datetime] = ()

    @property
    def forecast_origin(self) -> datetime:
        """Aware Asia/Shanghai prediction generation time."""
        return parse_shanghai_datetime(self.origin_timestamp, required=True)

    @property
    def endpoints(self) -> tuple[ForecastEndpoint, ...]:
        return tuple(
            ForecastEndpoint(
                index=index,
                day=index + 1,
                trading_day=self.target_days[index],
                target_close_at=value,
            )
            for index, value in enumerate(self.target_close_at)
        )


def _compute_returns_from_path(
    path: list, day_end_indices: list[int], origin_close: float
) -> list[float]:
    """从一条 OHLCV+amount 路径重算三日末根收益率。

    path 形如 [[open,high,low,close,vol,amount], ...]，长度 = pred_len。
    """
    if origin_close == 0:
        raise ForecastLoadError("origin_close 为 0，无法计算收益率")
    if len(day_end_indices) != 3:
        raise ForecastLoadError(
            f"day_end_indices 应有 3 个元素，实际 {len(day_end_indices)}"
        )
    returns: list[float] = []
    for idx in day_end_indices:
        if idx < 0 or idx >= len(path):
            raise ForecastLoadError(
                f"day_end_index {idx} 越界（path 长度 {len(path)}）"
            )
        bar = path[idx]
        if len(bar) <= CLOSE_FEATURE_INDEX:
            raise ForecastLoadError(f"path[{idx}] 缺 close 列：{bar}")
        close = float(bar[CLOSE_FEATURE_INDEX])
        returns.append(close / origin_close - 1.0)
    return returns


def _extract_predicted_returns(
    kronos_block: dict, metrics_block: dict | None
) -> tuple[list[float], list[str]]:
    """三日预测收益率的回退链：

    1. metrics.three_day.kronos.endpoints.day{N}.predicted_return（回测现成值）
    2. kronos.predicted_path（默认 = median_path）
    3. kronos.median_path
    4. kronos.mean_path

    返回 (returns, sources)。sources 每项标记该日收益率来源。
    """
    # 1. 优先用回测 metrics 里的现成值
    three_day = (metrics_block or {}).get("three_day") or {}
    kronos_metrics = three_day.get("kronos") or {}
    endpoints = kronos_metrics.get("endpoints")
    if isinstance(endpoints, dict) and all(
        isinstance(endpoints.get(str(d)), dict)
        and "predicted_return" in endpoints[str(d)]
        for d in (1, 2, 3)
    ):
        returns = [float(endpoints[str(d)]["predicted_return"]) for d in (1, 2, 3)]
        return returns, ["metrics"] * 3

    # 2~4. 从路径重算
    day_end_indices = kronos_block.get("day_end_indices")
    origin_close = kronos_block.get("origin_close")
    if not isinstance(day_end_indices, list) or len(day_end_indices) != 3:
        raise ForecastLoadError("回退重算需要 kronos.day_end_indices（3 个元素）")
    if origin_close is None:
        raise ForecastLoadError("回退重算需要 kronos.origin_close")

    for key in ("predicted_path", "median_path", "mean_path"):
        path = kronos_block.get(key)
        if isinstance(path, list) and len(path) >= max(day_end_indices) + 1:
            try:
                returns = _compute_returns_from_path(
                    path, day_end_indices, float(origin_close)
                )
            except ForecastLoadError:
                continue
            return returns, [key] * 3

    raise ForecastLoadError(
        "无法获取三日预测收益率：metrics.three_day 缺失且 kronos 无可用路径"
        "（predicted_path/median_path/mean_path）"
    )


def _extract_probabilities(kronos_block: dict) -> list[float]:
    """读取 day1/2/3_up_probability（白名单保证存在）。"""
    probs = []
    for d in (1, 2, 3):
        key = f"day{d}_up_probability"
        if key not in kronos_block:
            raise ForecastLoadError(f"kronos 缺 {key}")
        probs.append(float(kronos_block[key]))
    return probs


def _normalize_target_days(target_days) -> tuple[str, ...]:
    """Normalize trading-day labels to local ``YYYY-MM-DD`` strings."""
    out = []
    for d in target_days:
        parsed = parse_shanghai_datetime(d)
        out.append(parsed.date().isoformat() if parsed else str(d)[:10])
    return tuple(out)


def _extract_target_close_at(
    kronos_block: dict,
    target_days: tuple[str, ...],
) -> tuple[datetime, datetime, datetime]:
    """Read explicit endpoint times, with a conservative legacy adapter."""
    raw_explicit = kronos_block.get("target_close_at")
    if isinstance(raw_explicit, list) and len(raw_explicit) == 3:
        values = tuple(
            parse_shanghai_datetime(value, required=True) for value in raw_explicit
        )
        return values  # type: ignore[return-value]

    indices = kronos_block.get("day_end_indices")
    if not isinstance(indices, list) or len(indices) != 3:
        raise ForecastLoadError(
            "缺少 kronos.target_close_at；legacy 预测至少需要 day_end_indices"
        )

    # New producer field: close timestamps for every predicted bar.
    raw_close_timestamps = kronos_block.get("target_close_timestamps")
    if isinstance(raw_close_timestamps, list):
        try:
            values = tuple(
                parse_shanghai_datetime(raw_close_timestamps[int(index)], required=True)
                for index in indices
            )
            return values  # type: ignore[return-value]
        except (IndexError, TypeError, ValueError) as exc:
            raise ForecastLoadError("kronos.target_close_timestamps 结构非法") from exc

    # Legacy artifacts only contain bar starts. The project K-line contract is
    # hourly and T is the bar start, so derive the real close at T + 1 hour.
    raw_timestamps = kronos_block.get("target_timestamps")
    if isinstance(raw_timestamps, list):
        try:
            bar_starts = [
                parse_shanghai_datetime(raw_timestamps[int(index)], required=True)
                for index in indices
            ]
            if any(value.strftime("%H:%M:%S") != "14:00:00" for value in bar_starts):
                raise ForecastLoadError(
                    "legacy 预测端点未到 14:00 日盘收盘，必须重新生成 K 线预测"
                )
            values = tuple(
                bar_close_time(value, duration=DEFAULT_BAR_DURATION)
                for value in bar_starts
            )
            return values  # type: ignore[return-value]
        except (IndexError, TypeError, ValueError) as exc:
            raise ForecastLoadError("kronos.target_timestamps 结构非法") from exc

    # Old backtest fixtures did not retain target bar timestamps. Their
    # target_days are already provider trading-day labels, so retain a
    # compatibility adapter at the known 15:00 daily close and make the
    # omission visible in the caller's legacy artifact audit. New artifacts
    # always carry explicit target_close_at and never use this path.
    if len(target_days) == 3:
        values = tuple(
            parse_shanghai_datetime(f"{day[:10]} 15:00:00", required=True)
            for day in target_days
        )
        return values  # type: ignore[return-value]

    raise ForecastLoadError("无法确定三日真实 target_close_at")


def load_forecast(path: Path | str) -> ForecastSnapshot:
    """读取 forecast_result.json → ForecastSnapshot。"""
    path = Path(path)
    if not path.exists():
        raise ForecastLoadError(f"forecast_result.json 不存在：{path}")

    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except json.JSONDecodeError as e:
        raise ForecastLoadError(f"JSON 解析失败：{path} - {e}") from e

    kronos_block = payload.get("kronos")
    if not isinstance(kronos_block, dict):
        raise ForecastLoadError("缺少 kronos 块或非对象")

    instrument = kronos_block.get("instrument") or payload.get("source", {}).get(
        "instrument"
    )
    if not instrument:
        raise ForecastLoadError("缺少 instrument（kronos.instrument / source.instrument）")

    origin_timestamp = kronos_block.get("forecast_origin") or kronos_block.get(
        "origin_timestamp"
    )
    if not origin_timestamp:
        raise ForecastLoadError("缺少 kronos.forecast_origin/origin_timestamp")
    forecast_origin = parse_shanghai_datetime(
        kronos_block.get("forecast_origin") or origin_timestamp,
        required=True,
    )
    origin_trading_day = str(
        kronos_block.get("origin_trading_day") or forecast_origin.date().isoformat()
    )

    target_days_raw = kronos_block.get("target_days")
    if not isinstance(target_days_raw, list) or len(target_days_raw) != 3:
        raise ForecastLoadError("kronos.target_days 必须是 3 个元素的列表")
    target_days = _normalize_target_days(target_days_raw)

    origin_close = kronos_block.get("origin_close")
    if origin_close is None:
        raise ForecastLoadError("缺少 kronos.origin_close")

    probs = _extract_probabilities(kronos_block)
    returns, sources = _extract_predicted_returns(kronos_block, payload.get("metrics"))
    target_close_at = _extract_target_close_at(kronos_block, target_days)

    days = tuple(
        DayForecast(
            day=i + 1,
            up_probability=probs[i],
            predicted_return=returns[i],
            predicted_return_source=sources[i],
            target_close_at=target_close_at[i],
        )
        for i in range(3)
    )

    return ForecastSnapshot(
        instrument=str(instrument),
        origin_timestamp=format_shanghai_datetime(forecast_origin) or str(origin_timestamp),
        origin_trading_day=origin_trading_day,
        origin_close=float(origin_close),
        target_days=target_days,
        days=days,
        target_close_at=target_close_at,
    )
