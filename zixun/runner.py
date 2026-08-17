"""面板触发的后台抓取执行器。

设计：用 subprocess 异步启动 `python -m zixun.cli run`，独立进程执行；
状态写入 data/run.status.json，日志追加到 logs/panel-run.log。
面板通过 get_status() + tail_log() 轮询展示，不阻塞 Streamlit 主循环。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .settings import DATA_DIR, LOGS_DIR, PANEL_RUN_LOG, ROOT, RUN_STATUS_PATH


def _read_status() -> dict:
    if RUN_STATUS_PATH.exists():
        try:
            return json.loads(RUN_STATUS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write_status(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RUN_STATUS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


def get_status() -> dict:
    """读取任务状态；若进程已退出则收敛为 finished。"""
    st = _read_status()
    if st.get("state") == "running":
        pid = st.get("pid")
        if not _pid_alive(pid):
            st["state"] = "finished"
            st["finish_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _write_status(st)
    return st


def start_run(priority: str | None = None, source: str | None = None) -> dict:
    """启动后台抓取。返回 {"ok": bool, "msg": ..., "status": ...}。"""
    st = get_status()
    if st.get("state") == "running":
        return {
            "ok": False,
            "msg": f"已有抓取任务在运行（PID {st.get('pid')}）",
            "status": st,
        }

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_fp = open(PANEL_RUN_LOG, "a", encoding="utf-8")
    log_fp.write(
        f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动抓取 ====\n"
    )
    log_fp.flush()

    cmd = [sys.executable, "-m", "zixun.cli", "run"]
    if priority:
        cmd += ["--priority", priority]
    if source:
        cmd += ["--source", source]

    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # 独立进程组，不受面板退出影响
    )
    status = {
        "state": "running",
        "pid": proc.pid,
        "start_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cmd": " ".join(cmd),
        "priority": priority or "all",
        "source": source,
        "log_path": str(PANEL_RUN_LOG),
    }
    _write_status(status)
    return {"ok": True, "msg": "已启动", "status": status}


def tail_log(n: int = 25) -> str:
    if not PANEL_RUN_LOG.exists():
        return ""
    try:
        lines = PANEL_RUN_LOG.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])
