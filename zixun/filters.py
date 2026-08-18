"""抓取筛选规则：地区资讯过滤 + 排除词 + 白名单。

全局筛选逻辑（evaluate）：
  1. enabled=False → 全部保留
  2. 命中 exclude_keywords → 丢弃
  3. drop_regional 且命中地区词、且不含全局词 → 丢弃（地区性资讯）
  4. analysis 类栏目（apply_whitelist）须命中 include_keywords，否则丢弃
  5. 其余保留

栏目级筛选逻辑（evaluate_source）：
  - title_exclude_keywords：命中任一词即丢弃
  - title_include_keywords：至少命中一个词
  - required_keyword_groups：每组至少命中一个词

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
    allow_regional: bool = False,
    exclude_keyword_exceptions: list[str] | None = None,
) -> tuple[bool, str]:
    """返回 (是否保留, 原因)。"""
    if not cfg.get("enabled", True):
        return True, "过滤未启用"

    # 通用排除词
    exceptions = set(exclude_keyword_exceptions or [])
    for w in cfg.get("exclude_keywords", []):
        if w and w not in exceptions and w in title:
            return False, f"排除词:{w}"

    # 地区性过滤
    if cfg.get("drop_regional", True) and not allow_regional:
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


def evaluate_source(title: str, source_cfg: dict) -> tuple[bool, str]:
    """应用单个栏目自己的标题约束。"""
    for word in source_cfg.get("title_exclude_keywords", []) or []:
        if word and word in title:
            return False, f"栏目排除词:{word}"

    include = source_cfg.get("title_include_keywords", []) or []
    if include and not any(word in title for word in include if word):
        return False, "未命中栏目关键词"

    groups = source_cfg.get("required_keyword_groups", []) or []
    for index, group in enumerate(groups, start=1):
        if not group or not any(word in title for word in group if word):
            return False, f"未命中栏目关键词组:{index}"

    return True, "保留"
