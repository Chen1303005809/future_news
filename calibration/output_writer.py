"""组装并写出 calibration.json。

sources 只回写追溯元数据（id/title/publish_time/report_type），正文已在 prompt
消费、不回写以避免产物臃肿；需全文可用 id 回查 DB。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .article_retrieval import ArticleDigest
from .calibration_engine import CalibrationResult
from .config import CalibrationConfig
from .forecast_loader import ForecastSnapshot
from .instrument_mapping import instrument_to_variety, variety_label

CALIBRATION_VERSION = "1.0"


def _source_entry(a: ArticleDigest) -> dict[str, Any]:
    return {
        "id": a.id,
        "publish_time": a.publish_time,
        "title": a.title,
        "report_type": a.report_type,
    }


def build_output(
    snapshot: ForecastSnapshot,
    result: CalibrationResult,
    config: CalibrationConfig,
    *,
    llm_model: str | None = None,
    llm_tokens: dict[str, int] | None = None,
    llm_attempts: int | None = None,
    skipped_reason: str | None = None,
    llm_error: str | None = None,
) -> dict[str, Any]:
    """组装 calibration.json 的完整 dict。"""
    variety, _ = instrument_to_variety(snapshot.instrument)

    meta: dict[str, Any] = {
        "calibration_version": CALIBRATION_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if llm_model is not None:
        meta["llm_model"] = llm_model
    if llm_tokens is not None:
        meta["llm_tokens"] = llm_tokens
    if llm_attempts is not None:
        meta["llm_attempts"] = llm_attempts
    if result.variety_fallback:
        meta["variety_fallback"] = True
        meta["variety_queried"] = list(result.variety_queried)
    if skipped_reason:
        meta["skipped_reason"] = skipped_reason
    if llm_error:
        meta["llm_error"] = llm_error
    meta["config"] = {
        "lookback_days": config.lookback_days,
        "max_articles": config.max_articles,
        "max_prob_shift": config.max_prob_shift,
        "max_return_shift": config.max_return_shift,
    }

    forecast = {
        "instrument": snapshot.instrument,
        "variety_code": variety,
        "variety_label": variety_label(variety) if variety else snapshot.instrument,
        "origin_timestamp": snapshot.origin_timestamp,
        "origin_trading_day": snapshot.origin_trading_day,
        "origin_close": snapshot.origin_close,
        "target_days": list(snapshot.target_days),
    }

    days = []
    for d in result.days:
        days.append(
            {
                "day": d.day,
                "original": {
                    "up_probability": round(d.original_probability, 6),
                    "predicted_return": round(d.original_return, 6),
                },
                "calibrated": {
                    "up_probability": round(d.calibrated_probability, 6),
                    "predicted_return": round(d.calibrated_return, 6),
                },
                "applied_shift": {
                    "prob": round(d.applied_prob_shift, 6),
                    "return": round(d.applied_return_shift, 6),
                },
                "agreement": d.agreement,
                "direction_flipped": d.direction_flipped,
            }
        )

    return {
        "meta": meta,
        "forecast": forecast,
        "view": result.view,
        "confidence": result.confidence,
        "commentary": result.commentary,
        "days": days,
        "sources": [_source_entry(a) for a in result.used_articles],
    }


def write_calibration(
    output: dict[str, Any],
    out_path: Path | str,
) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    return out_path
