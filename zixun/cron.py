"""crontab 管理（只增删改带 "# zixun-cron" 标记的条目，保留其他条目不动）。

条目格式：
    MIN HOUR DOM MON DOW  /abs/path/scripts/run.sh # zixun-cron:说明
禁用格式（行首加 # ）：
    # MIN HOUR DOM MON DOW  /abs/path/scripts/run.sh # zixun-cron:说明
"""
from __future__ import annotations

import subprocess

from .settings import RUN_SCRIPT

MARK = "# zixun-cron"

# 预设（minute, hour, label）
PRESET_THREE = [(13, 9, "每天3次-早"), (17, 13, "每天3次-午"), (23, 18, "每天3次-晚")]
PRESET_ONE = [(37, 8, "每天1次")]


def _read() -> list[str]:
    """读取当前 crontab 全部行；无 crontab 则返回空列表。"""
    r = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True
    )
    if r.returncode != 0 or not r.stdout.strip():
        return []
    return r.stdout.splitlines()


def _write(lines: list[str]) -> None:
    subprocess.run(
        ["crontab", "-"], input="\n".join(lines) + "\n",
        text=True, check=True,
    )


def _is_disabled(line: str) -> bool:
    return line.lstrip().startswith("#")


def _entry_content(line: str) -> str:
    """去掉禁用前缀，返回条目正文。"""
    return line.lstrip("# ").rstrip()


def list_entries() -> list[dict]:
    """列出所有 zixun 条目。"""
    entries: list[dict] = []
    for line in _read():
        if MARK not in line:
            continue
        content = _entry_content(line)
        parts = content.split(None, 5)
        if len(parts) < 6:
            continue
        schedule = " ".join(parts[:5])
        rest = parts[5]
        label = ""
        if MARK in rest:
            after = rest.split(MARK, 1)[-1]
            label = after.lstrip(": ").strip()
        entries.append(
            {
                "schedule": schedule,
                "label": label,
                "enabled": not _is_disabled(line),
                "raw": line,
            }
        )
    return entries


def _make_line(minute: int, hour: int, label: str = "") -> str:
    line = f"{minute} {hour} * * * {RUN_SCRIPT} {MARK}"
    if label:
        line += f":{label}"
    return line


def add_entry(minute: int, hour: int, label: str = "") -> bool:
    """添加一条。同 schedule 已存在则跳过，返回是否新增。"""
    lines = _read()
    target_sched = f"{minute} {hour}"
    for l in lines:
        if MARK in l:
            content = _entry_content(l)
            if content.startswith(target_sched + " "):
                return False
    lines.append(_make_line(minute, hour, label))
    _write(lines)
    return True


def remove_by_index(idx: int) -> bool:
    """删除第 idx 个 zixun 条目（按 list_entries 顺序）。"""
    lines = _read()
    new: list[str] = []
    count = 0
    removed = False
    for l in lines:
        if MARK in l:
            if count == idx:
                removed = True
                count += 1
                continue
            count += 1
        new.append(l)
    if removed:
        _write(new)
    return removed


def toggle_by_index(idx: int, enable: bool) -> None:
    """启用/禁用第 idx 个 zixun 条目。"""
    lines = _read()
    new: list[str] = []
    count = 0
    for l in lines:
        if MARK in l:
            if count == idx:
                disabled = _is_disabled(l)
                content = _entry_content(l)
                if enable and disabled:
                    new.append(content)
                elif (not enable) and (not disabled):
                    new.append("# " + content)
                else:
                    new.append(l)
                count += 1
                continue
            count += 1
        new.append(l)
    _write(new)
