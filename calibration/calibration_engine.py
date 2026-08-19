"""温和数值校准引擎。

LLM 是文本模型，不能直接改预测数值。校准做法：
- 从 LLM 输出的 JSON 提取 ``view / confidence / commentary`` 与每日的
  ``agreement / prob_shift / return_shift``。
- 对偏移做**嵌套 clamp**：先 ``clamp(shift, ±MAX)`` 限制偏移幅度（温和），再
  ``clamp(original + shift, CLAMP)`` 限制绝对区间。
- 记录实际生效的偏移（``applied_shift``，裁剪后），便于审计 LLM 是否被约束；
  方向反转（校准后概率跨过 0.5）记录 ``direction_flipped`` 标志——温和校准应极少触发。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .config import CalibrationConfig
from .forecast_loader import ForecastSnapshot

VIEWS = ("bullish", "bearish", "range")
AGREEMENTS = ("agree", "disagree")


class CalibrationParseError(Exception):
    """LLM 返回的 JSON 结构非法。"""


def _as_float(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalibrationParseError(f"字段 {field} 不是数值：{value!r}")
    return float(value)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _validate_and_coerce(parsed: dict[str, Any]) -> dict[str, Any]:
    """宽松校验 LLM 输出，越界值不丢弃（由引擎裁剪），只校验结构/类型。"""
    view = parsed.get("view")
    if view not in VIEWS:
        raise CalibrationParseError(f"view 必须是 {VIEWS} 之一，实际 {view!r}")

    confidence = _as_float(parsed.get("confidence", 0.0), "confidence")
    confidence = _clamp(confidence, 0.0, 1.0)

    commentary = str(parsed.get("commentary") or "").strip()

    days_raw = parsed.get("days")
    if not isinstance(days_raw, dict):
        raise CalibrationParseError("缺少 days 对象")

    days: dict[str, dict[str, Any]] = {}
    for i in (1, 2, 3):
        d = days_raw.get(str(i))
        if not isinstance(d, dict):
            raise CalibrationParseError(f"days.{i} 缺失或非对象")
        agreement = d.get("agreement")
        if agreement not in AGREEMENTS:
            raise CalibrationParseError(f"days.{i}.agreement 必须是 {AGREEMENTS}，实际 {agreement!r}")
        # 越界偏移这里只做类型校验，数值边界由 apply 时裁剪
        prob_shift = _as_float(d.get("prob_shift", 0.0), f"days.{i}.prob_shift")
        return_shift = _as_float(d.get("return_shift", 0.0), f"days.{i}.return_shift")
        days[str(i)] = {
            "agreement": agreement,
            "prob_shift": prob_shift,
            "return_shift": return_shift,
        }

    return {"view": view, "confidence": confidence, "commentary": commentary, "days": days}


@dataclass(frozen=True)
class DayCalibration:
    """单日校准结果。"""

    day: int
    original_probability: float
    calibrated_probability: float
    original_return: float
    calibrated_return: float
    applied_prob_shift: float   # 裁剪后实际生效的概率偏移
    applied_return_shift: float  # 裁剪后实际生效的收益率偏移
    agreement: str
    direction_flipped: bool
    target_close_at: datetime | None = None


@dataclass(frozen=True)
class CalibrationResult:
    """完整校准结果（含研判文本与来源资讯）。"""

    view: str
    confidence: float
    commentary: str
    days: tuple[DayCalibration, DayCalibration, DayCalibration]
    variety_fallback: bool
    variety_queried: tuple[str, ...]
    used_articles: list[Any]      # ArticleDigest 列表（追溯用）
    llm_meta: dict[str, Any]      # model / tokens / attempt / raw_text / parse_error


def apply_calibration(
    snapshot: ForecastSnapshot,
    parsed: dict[str, Any],
    config: CalibrationConfig,
    *,
    variety_fallback: bool = False,
    variety_queried: tuple[str, ...] = (),
    used_articles: list[Any] | None = None,
    llm_meta: dict[str, Any] | None = None,
) -> CalibrationResult:
    """把 LLM 研判应用到三日预测，产出校准结果。"""
    coerced = _validate_and_coerce(parsed)

    prob_lo, prob_hi = config.prob_clamp
    ret_lo, ret_hi = config.return_clamp

    day_results: list[DayCalibration] = []
    for i, snap_day in enumerate(snapshot.days):
        day_num = i + 1
        raw = coerced["days"][str(day_num)]

        orig_prob = snap_day.up_probability
        orig_ret = snap_day.predicted_return

        # 嵌套 clamp：先限偏移幅度，再限绝对区间
        prob_shift = _clamp(raw["prob_shift"], -config.max_prob_shift, config.max_prob_shift)
        ret_shift = _clamp(raw["return_shift"], -config.max_return_shift, config.max_return_shift)
        cal_prob = _clamp(orig_prob + prob_shift, prob_lo, prob_hi)
        cal_ret = _clamp(orig_ret + ret_shift, ret_lo, ret_hi)

        # 实际生效的偏移（二次 clamp 后可能与原始请求不同，用于审计）
        applied_prob_shift = cal_prob - orig_prob
        applied_return_shift = cal_ret - orig_ret

        # 方向反转：严格从偏多跨到偏空（或反之）才算，压线 0.5 的中性不算
        orig_dir = orig_prob > 0.5
        cal_dir = cal_prob > 0.5
        direction_flipped = orig_dir != cal_dir

        day_results.append(
            DayCalibration(
                day=day_num,
                original_probability=orig_prob,
                calibrated_probability=cal_prob,
                original_return=orig_ret,
                calibrated_return=cal_ret,
                applied_prob_shift=applied_prob_shift,
                applied_return_shift=applied_return_shift,
                agreement=raw["agreement"],
                direction_flipped=direction_flipped,
                target_close_at=snap_day.target_close_at,
            )
        )

    return CalibrationResult(
        view=coerced["view"],
        confidence=coerced["confidence"],
        commentary=coerced["commentary"],
        days=(day_results[0], day_results[1], day_results[2]),
        variety_fallback=variety_fallback,
        variety_queried=variety_queried,
        used_articles=list(used_articles or []),
        llm_meta=dict(llm_meta or {}),
    )
