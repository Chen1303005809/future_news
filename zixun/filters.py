"""抓取筛选规则：地区资讯过滤 + 排除词 + 白名单。

筛选逻辑（evaluate）：
  1. enabled=False → 全部保留
  2. 命中 exclude_keywords → 丢弃
  3. drop_regional 且命中地区词、且不含全局词 → 丢弃（地区性资讯）
  4. analysis 类栏目（apply_whitelist）须命中 include_keywords，否则丢弃
  5. 其余保留

配置文件：config/filters.yaml（面板可在线编辑后回写）。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .settings import FILTERS_PATH


def load_filters(path: Path = FILTERS_PATH) -> dict:
    if Path(path).exists():
        try:
            with open(path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            return {}
    return {}


def save_filters(cfg: dict, path: Path = FILTERS_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


def evaluate(
    title: str,
    cfg: dict,
    apply_whitelist: bool = False,
) -> tuple[bool, str]:
    """返回 (是否保留, 原因)。"""
    if not cfg.get("enabled", True):
        return True, "过滤未启用"

    # 通用排除词
    for w in cfg.get("exclude_keywords", []):
        if w and w in title:
            return False, f"排除词:{w}"

    # 地区性过滤
    if cfg.get("drop_regional", True):
        regional = cfg.get("regional_keywords", [])
        global_kw = cfg.get("global_keywords", [])
        if regional and any(r in title for r in regional if r):
            if not (global_kw and any(g in title for g in global_kw if g)):
                return False, "地区资讯"

    # analysis 类白名单
    if apply_whitelist:
        include = cfg.get("include_keywords", [])
        if not (include and any(k in title for k in include if k)):
            return False, "非相关"

    return True, "保留"
