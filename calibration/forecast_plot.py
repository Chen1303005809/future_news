"""Render the forecast chart with a calibrated hourly close trajectory."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

os.environ.setdefault(
    "MPLCONFIGDIR",
    str(Path(tempfile.gettempdir()) / "zixun-calibration-matplotlib"),
)

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import numpy as np
import pandas as pd


KRONOS_COLOR = "#2563eb"
CALIBRATED_COLOR = "#d97706"  # amber: deliberately distinct from blue / black


def _read_json_object(path: Path | str, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取{label}：{path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label}必须是 JSON 对象：{path}")
    return value


def _as_timestamp(value: Any, *, field: str) -> pd.Timestamp:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}不是有效时间：{value!r}") from exc
    if pd.isna(timestamp):
        raise ValueError(f"{field}不是有效时间：{value!r}")
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp


def _as_finite_float(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field}不是数值：{value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}不是数值：{value!r}") from exc
    if not np.isfinite(number):
        raise ValueError(f"{field}不是有限数值：{value!r}")
    return number


def _close_path(
    kronos: Mapping[str, Any], field: str, expected_length: int
) -> np.ndarray:
    values = np.asarray(kronos.get(field), dtype=np.float64)
    if values.ndim != 2 or values.shape[1] <= 3:
        raise ValueError(f"kronos.{field}不是包含 close 列的二维路径")
    if len(values) != expected_length:
        raise ValueError(
            f"kronos.{field}长度为 {len(values)}，与目标时间数 {expected_length} 不一致"
        )
    close = values[:, 3]
    if not np.isfinite(close).all():
        raise ValueError(f"kronos.{field}包含非有限 close 值")
    return close


def _load_context(
    source_path: Path | str,
    *,
    origin_timestamp: pd.Timestamp,
    lookback: int,
) -> pd.DataFrame:
    """读取绘图所需的原始 close 上下文，不依赖模型运行时。"""
    payload = _read_json_object(source_path, label="K 线源文件")
    raw_items = payload.get("data")
    if not isinstance(raw_items, list):
        raise ValueError("K 线源文件缺少 data 列表")

    rows = [
        {
            "timestamp": f"{item.get('TeD')} {item.get('T')}",
            "close": item.get("C"),
        }
        for item in raw_items
        if isinstance(item, dict)
    ]
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError("K 线源文件没有可用记录")
    frame["timestamp"] = pd.to_datetime(
        frame["timestamp"], format="%Y%m%d %H:%M:%S", errors="coerce"
    )
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna().sort_values("timestamp", kind="stable")
    frame = frame.loc[frame["timestamp"] <= origin_timestamp].tail(max(lookback, 1))
    if frame.empty:
        raise ValueError("K 线源文件中没有预测起点前的上下文")
    return frame.reset_index(drop=True)


def _applied_return_shifts(calibration: Mapping[str, Any]) -> np.ndarray:
    days = calibration.get("days")
    if not isinstance(days, list):
        raise ValueError("calibration.days必须是列表")

    by_day: dict[int, Mapping[str, Any]] = {}
    for item in days:
        if isinstance(item, Mapping) and isinstance(item.get("day"), int):
            by_day[item["day"]] = item

    shifts: list[float] = []
    for day in range(1, 4):
        item = by_day.get(day)
        if item is None:
            raise ValueError(f"calibration.days缺少第{day}日")
        applied = item.get("applied_shift")
        if not isinstance(applied, Mapping):
            raise ValueError(f"calibration.days[{day}].applied_shift缺失")
        shifts.append(
            _as_finite_float(
                applied.get("return"),
                field=f"calibration.days[{day}].applied_shift.return",
            )
        )
    return np.asarray(shifts, dtype=np.float64)


def _interpolated_offsets(
    bar_count: int,
    day_end_indices: Sequence[int],
    day_end_shifts: Sequence[float],
) -> np.ndarray:
    if bar_count < 1:
        raise ValueError("预测路径不能为空")
    if len(day_end_indices) != 3 or len(day_end_shifts) != 3:
        raise ValueError("三日校准必须包含三个日终索引和偏移")

    indices = [int(index) for index in day_end_indices]
    if (
        indices[0] < 0
        or indices[2] != bar_count - 1
        or not indices[0] < indices[1] < indices[2]
    ):
        raise ValueError(
            "day_end_indices必须严格递增，且最后一个索引必须是预测路径末根"
        )

    offsets = np.empty(bar_count, dtype=np.float64)
    previous_index = -1
    previous_shift = 0.0
    for end_index, target_shift in zip(indices, day_end_shifts):
        count = end_index - previous_index
        offsets[previous_index + 1 : end_index + 1] = np.linspace(
            previous_shift, float(target_shift), count + 1
        )[1:]
        previous_index = end_index
        previous_shift = float(target_shift)
    return offsets


def build_calibrated_return_path(
    predicted_close: Sequence[float],
    origin_close: float,
    calibration: Mapping[str, Any],
    day_end_indices: Sequence[int],
) -> np.ndarray:
    """将三个日终收益率偏移按交易 K 数插值到完整预测路径。"""
    if origin_close == 0:
        raise ValueError("origin_close不能为 0")
    close = np.asarray(predicted_close, dtype=np.float64)
    if close.ndim != 1 or len(close) == 0 or not np.isfinite(close).all():
        raise ValueError("predicted_close必须是非空有限数值序列")
    shifts = _applied_return_shifts(calibration)
    offsets = _interpolated_offsets(len(close), day_end_indices, shifts)
    return close / origin_close - 1.0 + offsets


def render_calibrated_forecast_plot(
    forecast_result_path: Path | str,
    calibration_path: Path | str,
    output_path: Path | str,
) -> Path:
    """Re-render the standard forecast chart with an orange calibration overlay."""
    forecast = _read_json_object(forecast_result_path, label="forecast_result")
    calibration = _read_json_object(calibration_path, label="calibration")
    kronos = forecast.get("kronos")
    if not isinstance(kronos, Mapping):
        raise ValueError("forecast_result缺少 kronos 对象")

    origin_timestamp = _as_timestamp(
        kronos.get("origin_timestamp"), field="kronos.origin_timestamp"
    )
    origin_close = _as_finite_float(
        kronos.get("origin_close"), field="kronos.origin_close"
    )
    if origin_close == 0:
        raise ValueError("kronos.origin_close不能为 0")

    raw_timestamps = kronos.get("target_timestamps")
    if not isinstance(raw_timestamps, list) or not raw_timestamps:
        raise ValueError("kronos.target_timestamps必须是非空列表")
    target_timestamps = pd.DatetimeIndex(
        [_as_timestamp(value, field="kronos.target_timestamps") for value in raw_timestamps]
    )
    day_end_indices = kronos.get("day_end_indices")
    if not isinstance(day_end_indices, list):
        raise ValueError("kronos.day_end_indices必须是列表")

    expected_length = len(target_timestamps)
    actual_close = _close_path(kronos, "actual_path", expected_length)
    q10_close = _close_path(kronos, "q10_path", expected_length)
    q50_close = _close_path(kronos, "median_path", expected_length)
    predicted_close = _close_path(kronos, "predicted_path", expected_length)
    q90_close = _close_path(kronos, "q90_path", expected_length)
    forecast_x = pd.DatetimeIndex([origin_timestamp, *target_timestamps.tolist()])
    actual_y = np.concatenate(([0.0], actual_close / origin_close - 1.0))
    q10_y = np.concatenate(([0.0], q10_close / origin_close - 1.0))
    q50_y = np.concatenate(([0.0], q50_close / origin_close - 1.0))
    q90_y = np.concatenate(([0.0], q90_close / origin_close - 1.0))
    calibrated_y = np.concatenate(
        (
            [0.0],
            build_calibrated_return_path(
                predicted_close,
                origin_close,
                calibration,
                day_end_indices,
            ),
        )
    )

    source = forecast.get("source")
    config = forecast.get("config")
    if not isinstance(source, Mapping) or not source.get("path"):
        raise ValueError("forecast_result缺少 source.path")
    lookback = int(config.get("lookback", 256)) if isinstance(config, Mapping) else 256
    context = _load_context(
        source["path"], origin_timestamp=origin_timestamp, lookback=lookback
    )
    context_y = context["close"].to_numpy(dtype=np.float64) / origin_close - 1.0

    fig, axis = plt.subplots(figsize=(14, 7))
    axis.plot(
        context["timestamp"], context_y,
        color="#6b7280", linewidth=1.2, label="input close",
    )
    axis.plot(forecast_x, actual_y, color="black", linewidth=2.1, label="actual close")
    axis.fill_between(
        forecast_x, q10_y, q90_y,
        color=KRONOS_COLOR, alpha=0.18, label="Kronos 10–90%",
    )
    axis.plot(
        forecast_x, q50_y,
        color=KRONOS_COLOR, linewidth=2.0, linestyle="-", label="Kronos median",
    )
    axis.plot(
        forecast_x, calibrated_y,
        color=CALIBRATED_COLOR, linewidth=1.8, linestyle="--",
        label="Kronos calibrated hourly close",
    )

    for day_end in day_end_indices[:-1]:
        axis.axvline(
            target_timestamps[day_end], color="#9ca3af", linewidth=0.9, linestyle=":"
        )
    target_days = kronos.get("target_days") or []
    sample_count = config.get("sample_count") if isinstance(config, Mapping) else None
    sample_text = f" · samples={sample_count}" if sample_count is not None else ""
    axis.axhline(0.0, color="#9ca3af", linewidth=0.8)
    axis.set_title(
        f"{kronos.get('instrument', 'unknown')} · Kronos forecast + news calibration\n"
        f"target: {', '.join(str(day)[:10] for day in target_days)}{sample_text}"
    )
    axis.set_ylabel("Close return from forecast origin")
    axis.set_xlabel("Timestamp")
    axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.1%}"))
    locator = mdates.AutoDateLocator()
    axis.xaxis.set_major_locator(locator)
    axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    axis.grid(alpha=0.22)
    axis.legend(loc="best", ncol=2)
    fig.tight_layout()

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return destination
