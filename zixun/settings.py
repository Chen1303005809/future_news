"""项目路径、环境配置与常量。

运行时配置统一从项目根目录的 ``.env`` 读取；环境变量已经存在时优先使用
外部环境变量。结构化的栏目和筛选词仍由 YAML 管理，见 ``config/``。
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

from dotenv import load_dotenv

# 项目根目录（zixun/settings.py 的上两级）
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)


def env_str(name: str, default: str) -> str:
    """读取非空字符串环境变量。"""
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def env_bool(name: str, default: bool) -> bool:
    """读取布尔环境变量，接受 true/false、1/0、yes/no。"""
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"环境变量 {name} 不是有效布尔值：{value!r}")


def env_int(name: str, default: int) -> int:
    """读取整数环境变量。"""
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 不是有效整数：{value!r}") from exc


def env_float(name: str, default: float) -> float:
    """读取浮点环境变量。"""
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value.strip())
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 不是有效数字：{value!r}") from exc


def env_path(name: str, default: Path) -> Path:
    """读取路径环境变量；相对路径相对于项目根目录解析。"""
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    path = Path(value.strip()).expanduser()
    return path if path.is_absolute() else ROOT / path


def env_optional_path(name: str) -> Path | None:
    """读取可为空的路径环境变量。"""
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    path = Path(value.strip()).expanduser()
    return path if path.is_absolute() else ROOT / path

# 主应用路径。默认值保持原有目录结构，机器差异通过 .env 覆盖。
CONFIG_PATH = env_path("ZIXUN_SOURCES_CONFIG", ROOT / "config" / "sources.yaml")
FILTERS_PATH = env_path("ZIXUN_FILTERS_CONFIG", ROOT / "config" / "filters.yaml")
DATA_DIR = env_path("ZIXUN_DATA_DIR", ROOT / "data")
DB_PATH = env_path("ZIXUN_DB_PATH", DATA_DIR / "zixun.db")
ARTICLES_DIR = env_path("ZIXUN_ARTICLES_DIR", ROOT / "articles")
LOGS_DIR = env_path("ZIXUN_LOGS_DIR", ROOT / "logs")
RUN_STATUS_PATH = env_path(
    "ZIXUN_RUN_STATUS_PATH", DATA_DIR / "run.status.json"
)
PANEL_RUN_LOG = env_path(
    "ZIXUN_PANEL_RUN_LOG", LOGS_DIR / "panel-run.log"
)
RUN_SCRIPT = env_path("ZIXUN_RUN_SCRIPT", ROOT / "scripts" / "run.sh")

# 走势预测（面板触发）。预测代码和包都在当前项目环境中。
KLINE_DIR = env_path("ZIXUN_KLINE_DIR", ROOT / "kline_data")
OUTPUTS_DIR = env_path("ZIXUN_OUTPUTS_DIR", ROOT / "outputs")
FORECAST_STATUS_PATH = env_path(
    "ZIXUN_FORECAST_STATUS_PATH", DATA_DIR / "forecast.status.json"
)
FORECAST_LOG = env_path("ZIXUN_FORECAST_LOG", LOGS_DIR / "forecast.log")

# Kronos 运行环境。模型和采样等实验参数保留在 kronos 模块中手动调整。
# KRONOS_PYTHON 默认使用启动面板的同一个解释器。
KRONOS_PYTHON = env_path("KRONOS_PYTHON", Path(sys.executable))
KRONOS_DEVICE = env_str("KRONOS_DEVICE", "auto")
KRONOS_CACHE_DIR = env_optional_path("KRONOS_CACHE_DIR")
KRONOS_LOCAL_FILES_ONLY = env_bool("KRONOS_LOCAL_FILES_ONLY", False)

# 品种中文显示名（面板用）
VARIETY_LABELS = {
    "rebar": "螺纹钢",
    "ironore": "铁矿石",
    "cokingcoal": "焦煤",
    "coke": "焦炭",
}

# 报告类型中文显示名
REPORT_TYPE_LABELS = {
    "daily": "日报/早报",
    "weekly": "周报",
    "monthly": "月报",
    "data": "数据",
    "analysis": "分析/快讯",
    "event": "事件快讯",
}
