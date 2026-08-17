"""校准配置。

运行时环境配置从项目根目录的 ``.env`` 读取；``config/calibration.yaml``
保留为兼容旧调用的可选基础配置。CLI 参数优先级最高，其次是环境变量，
最后才是显式传入的 YAML 和代码默认值。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from zixun.settings import (
    ROOT,
    env_bool,
    env_float,
    env_int,
    env_str,
)

DEFAULT_CONFIG_PATH = ROOT / "config" / "calibration.yaml"

# ── 温和校准边界默认值（与 config/calibration.yaml 保持一致）──────────────
DEFAULT_MAX_PROB_SHIFT = 0.10        # 单日上涨概率偏移上限（±10pp）
DEFAULT_MAX_RETURN_SHIFT = 0.015     # 单日收益率偏移上限（±1.5%）
DEFAULT_PROB_CLAMP = (0.0, 1.0)      # 概率绝对区间
DEFAULT_RETURN_CLAMP = (-0.10, 0.10)  # 三日累计收益率绝对区间（超 10% 视为停板级异常）

# ── 检索默认值 ──────────────────────────────────────────────────────────
DEFAULT_LOOKBACK_DAYS = 3
DEFAULT_MAX_ARTICLES = 15
DEFAULT_AI_SUMMARY_CAP = 200
DEFAULT_PREVIEW_CAP = 160

# ── LLM 默认值 ─────────────────────────────────────────────────────────
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_RETRIES = 2
DEFAULT_TIMEOUT = 60.0


@dataclass(frozen=True)
class CalibrationConfig:
    """校准运行配置（不可变，作为模块间传递的边界契约）。"""

    # 检索
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    max_articles: int = DEFAULT_MAX_ARTICLES
    fallback_to_black_sector: bool = True
    ai_summary_cap: int = DEFAULT_AI_SUMMARY_CAP
    preview_cap: int = DEFAULT_PREVIEW_CAP

    # LLM
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    temperature: float = DEFAULT_TEMPERATURE
    max_retries: int = DEFAULT_MAX_RETRIES
    timeout_seconds: float = DEFAULT_TIMEOUT

    # 校准边界
    max_prob_shift: float = DEFAULT_MAX_PROB_SHIFT
    max_return_shift: float = DEFAULT_MAX_RETURN_SHIFT
    prob_clamp: tuple[float, float] = DEFAULT_PROB_CLAMP
    return_clamp: tuple[float, float] = DEFAULT_RETURN_CLAMP

    @classmethod
    def _from_yaml(cls, path: Path | str | None = None) -> "CalibrationConfig":
        """从 YAML 加载，缺失键使用代码默认值。"""
        if path is None:
            path = DEFAULT_CONFIG_PATH
        path = Path(path)
        if not path.exists():
            return cls()

        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        retrieval = data.get("retrieval", {}) or {}
        llm = data.get("llm", {}) or {}
        calib = data.get("calibration", {}) or {}

        prob_clamp = tuple(calib.get("prob_clamp", DEFAULT_PROB_CLAMP))
        return_clamp = tuple(calib.get("return_clamp", DEFAULT_RETURN_CLAMP))

        return cls(
            lookback_days=int(retrieval.get("lookback_days", DEFAULT_LOOKBACK_DAYS)),
            max_articles=int(retrieval.get("max_articles", DEFAULT_MAX_ARTICLES)),
            fallback_to_black_sector=bool(
                retrieval.get("fallback_to_black_sector", True)
            ),
            ai_summary_cap=int(retrieval.get("ai_summary_cap", DEFAULT_AI_SUMMARY_CAP)),
            preview_cap=int(retrieval.get("preview_cap", DEFAULT_PREVIEW_CAP)),
            base_url=str(llm.get("base_url", DEFAULT_BASE_URL)),
            model=str(llm.get("model", DEFAULT_MODEL)),
            temperature=float(llm.get("temperature", DEFAULT_TEMPERATURE)),
            max_retries=int(llm.get("max_retries", DEFAULT_MAX_RETRIES)),
            timeout_seconds=float(llm.get("timeout_seconds", DEFAULT_TIMEOUT)),
            max_prob_shift=float(calib.get("max_prob_shift", DEFAULT_MAX_PROB_SHIFT)),
            max_return_shift=float(
                calib.get("max_return_shift", DEFAULT_MAX_RETURN_SHIFT)
            ),
            prob_clamp=prob_clamp,  # type: ignore[arg-type]
            return_clamp=return_clamp,  # type: ignore[arg-type]
        )

    @classmethod
    def from_yaml(cls, path: Path | str | None = None) -> "CalibrationConfig":
        """兼容旧调用：只从 YAML 加载，不读取环境变量。"""
        return cls._from_yaml(path)

    @classmethod
    def from_env(cls, path: Path | str | None = None) -> "CalibrationConfig":
        """从 .env 加载运行配置，可用显式 YAML 作为缺省基础值。

        环境变量命名空间为 ``CALIBRATION_*``。这样既能避免把 API key
        写入 YAML，也能在不同机器/进程间切换端点和超时配置。
        """
        base = cls._from_yaml(path) if path is not None else cls()

        prob_clamp = _env_pair(
            "CALIBRATION_PROB_CLAMP", base.prob_clamp
        )
        return_clamp = _env_pair(
            "CALIBRATION_RETURN_CLAMP", base.return_clamp
        )

        return cls(
            lookback_days=env_int(
                "CALIBRATION_LOOKBACK_DAYS", base.lookback_days
            ),
            max_articles=env_int(
                "CALIBRATION_MAX_ARTICLES", base.max_articles
            ),
            fallback_to_black_sector=env_bool(
                "CALIBRATION_FALLBACK_TO_BLACK_SECTOR",
                base.fallback_to_black_sector,
            ),
            ai_summary_cap=env_int(
                "CALIBRATION_AI_SUMMARY_CAP", base.ai_summary_cap
            ),
            preview_cap=env_int(
                "CALIBRATION_PREVIEW_CAP", base.preview_cap
            ),
            base_url=env_str("CALIBRATION_BASE_URL", base.base_url),
            model=env_str("CALIBRATION_MODEL", base.model),
            temperature=env_float(
                "CALIBRATION_TEMPERATURE", base.temperature
            ),
            max_retries=env_int(
                "CALIBRATION_MAX_RETRIES", base.max_retries
            ),
            timeout_seconds=env_float(
                "CALIBRATION_TIMEOUT_SECONDS", base.timeout_seconds
            ),
            max_prob_shift=env_float(
                "CALIBRATION_MAX_PROB_SHIFT", base.max_prob_shift
            ),
            max_return_shift=env_float(
                "CALIBRATION_MAX_RETURN_SHIFT", base.max_return_shift
            ),
            prob_clamp=prob_clamp,
            return_clamp=return_clamp,
        )

    def replace(self, **kwargs) -> "CalibrationConfig":
        """返回一份替换了指定字段的新配置（用于 CLI 覆盖）。"""
        from dataclasses import replace as _replace

        return _replace(self, **kwargs)


def _env_pair(
    name: str, default: tuple[float, float]
) -> tuple[float, float]:
    """读取形如 ``0,1`` 的两个浮点数环境变量。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) != 2:
        raise ValueError(f"环境变量 {name} 应为两个逗号分隔数字：{raw!r}")
    try:
        return float(parts[0]), float(parts[1])
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 不是有效数字对：{raw!r}") from exc
