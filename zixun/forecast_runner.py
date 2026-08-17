"""面板触发的预测+校准后台执行器。

预测代码和 Kronos 模型包都在当前项目环境中：

  1. ``python -m kronos.prediction_3day_json`` 读取 K 线并写出预测产物；
  2. ``python -m calibration`` 读取 forecast_result.json，写出 calibration.json。

两步仍然由独立 subprocess 串行执行，状态写入
``data/forecast.status.json``，日志追加到 ``logs/forecast.log``。
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from .settings import (
    FORECAST_LOG,
    FORECAST_STATUS_PATH,
    KLINE_DIR,
    KRONOS_CACHE_DIR,
    KRONOS_DEVICE,
    KRONOS_LOCAL_FILES_ONLY,
    KRONOS_PYTHON,
    LOGS_DIR,
    OUTPUTS_DIR,
    ROOT,
)

_CONTRACT_CACHE: list[dict] | None = None


def list_contracts(refresh: bool = False) -> list[dict]:
    """枚举 kline_data 下有效合约（过滤 data 为 null 的空文件）。

    返回 [{contract, variety, bars, path}]，按品种前缀 + 合约号排序。
    模块级缓存，面板重复调用不重复读盘。
    """
    global _CONTRACT_CACHE
    if _CONTRACT_CACHE is not None and not refresh:
        return _CONTRACT_CACHE

    from calibration.instrument_mapping import instrument_to_variety

    out: list[dict] = []
    for f in sorted(KLINE_DIR.glob("kline_*.json")):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            continue  # 空文件（已到期/无数据合约）
        ins = payload.get("Ins") or f.stem.removeprefix("kline_")
        variety, _ = instrument_to_variety(ins)
        out.append(
            {
                "contract": ins,
                "variety": variety or "unknown",
                "bars": len(data),
                "path": str(f),
            }
        )
    out.sort(key=lambda c: (c["variety"], c["contract"]))
    _CONTRACT_CACHE = out
    return out


def contract_output_dir(contract: str) -> Path:
    """某合约的预测产物目录：outputs/<contract>/"""
    return OUTPUTS_DIR / contract


# ── 状态管理（与抓取的 runner 同构，但独立文件互不干扰）─────────────────

def _read_status() -> dict:
    if FORECAST_STATUS_PATH.exists():
        try:
            return json.loads(FORECAST_STATUS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write_status(data: dict) -> None:
    FORECAST_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    FORECAST_STATUS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def get_status() -> dict:
    """读取预测任务状态；进程已退出且产物齐全则收敛为 finished。"""
    st = _read_status()
    if st.get("state") == "running":
        pid = st.get("pid")
        if not _pid_alive(pid):
            # 子进程结束：按产物判定成败
            out = contract_output_dir(st.get("contract") or "")
            calib = out / "calibration.json"
            forecast = out / "forecast_result.json"
            if forecast.exists():
                st["state"] = "finished"
                st["calibrated"] = calib.exists()
            else:
                st["state"] = "failed"
            st["finish_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _write_status(st)
    return st


def _build_command(contract: str) -> str:
    """两步串行命令：本地 Kronos 包预测 → 本地资讯校准。"""
    kline = next(
        (c["path"] for c in list_contracts() if c["contract"] == contract), None
    )
    if kline is None:
        raise ValueError(f"未知合约：{contract}")
    out_dir = contract_output_dir(contract)

    step1_args = [
        str(KRONOS_PYTHON),
        "-m",
        "kronos.prediction_3day_json",
        "--input",
        kline,
        "--output-dir",
        str(out_dir),
        "--device",
        KRONOS_DEVICE,
    ]
    if KRONOS_CACHE_DIR is not None:
        step1_args += ["--cache-dir", str(KRONOS_CACHE_DIR)]
    if KRONOS_LOCAL_FILES_ONLY:
        step1_args.append("--local-files-only")

    step2_args = [
        sys.executable,
        "-m",
        "calibration",
        "--input",
        str(out_dir / "forecast_result.json"),
    ]
    step1 = shlex.join(step1_args)
    step2 = shlex.join(step2_args)
    return f"{step1} && {step2}"


def start_forecast(contract: str) -> dict:
    """启动后台预测+校准。返回 {"ok": bool, "msg": ..., "status": ...}。"""
    if not KRONOS_PYTHON.exists():
        return {"ok": False, "msg": f"Kronos Python 不存在：{KRONOS_PYTHON}"}

    # 预测入口在当前项目内，但模型实现由已安装的 ``kronos-model-arch``
    # 提供。启动后台任务前先做一次轻量导入检查，避免面板只显示一个
    # 无法启动的 subprocess。
    probe = subprocess.run(
        [
            str(KRONOS_PYTHON),
            "-c",
            "from model import Kronos, KronosTokenizer, KronosPredictor",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout).strip().splitlines()
        reason = detail[-1] if detail else "未知导入错误"
        return {
            "ok": False,
            "msg": f"Kronos 包不可用，请安装 kronos-model-arch：{reason}",
        }

    st = get_status()
    if st.get("state") == "running":
        return {
            "ok": False,
            "msg": f"已有预测任务在运行（PID {st.get('pid')}）",
            "status": st,
        }

    cmd = _build_command(contract)

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_fp = open(FORECAST_LOG, "a", encoding="utf-8")
    log_fp.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} 预测+校准 {contract} ====\n")
    log_fp.flush()

    proc = subprocess.Popen(
        ["bash", "-c", cmd],
        cwd=str(ROOT),
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    status = {
        "state": "running",
        "pid": proc.pid,
        "contract": contract,
        "start_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cmd": cmd,
        "log_path": str(FORECAST_LOG),
    }
    _write_status(status)
    return {"ok": True, "msg": "已启动", "status": status}


def tail_log(n: int = 20) -> str:
    if not FORECAST_LOG.exists():
        return ""
    try:
        lines = FORECAST_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])


# ── 产物定位与面板展示数据 ──────────────────────────────────────────────

def load_display_data(contract: str) -> dict | None:
    """读取某合约的最新产物，组装面板渲染所需结构。

    返回 None 表示尚无预测产物。返回结构：
    {
      "forecast": {origin_timestamp, origin_close, target_days, instrument},
      "plot_path": Path | None,
      "calibration": calibration.json 的完整 dict | None,
    }
    """
    out = contract_output_dir(contract)
    forecast_json = out / "forecast_result.json"
    if not forecast_json.exists():
        return None

    display: dict = {}
    try:
        payload = json.loads(forecast_json.read_text(encoding="utf-8"))
        kronos = payload.get("kronos") or {}
        display["forecast"] = {
            "instrument": kronos.get("instrument") or contract,
            "origin_timestamp": kronos.get("origin_timestamp"),
            "origin_close": kronos.get("origin_close"),
            "target_days": kronos.get("target_days") or [],
        }
    except (json.JSONDecodeError, OSError):
        return None

    plot = out / "forecast_plot.png"
    display["plot_path"] = plot if plot.exists() else None

    calib_path = out / "calibration.json"
    if calib_path.exists():
        try:
            display["calibration"] = json.loads(calib_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            display["calibration"] = None
    else:
        display["calibration"] = None

    return display
