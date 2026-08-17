"""合约 instrument → 资讯 variety 的映射。

K线合约代码形如 ``rb2609`` / ``i2609`` / ``jm2609``，字母前缀对应品种：
    i  → ironore（铁矿石）
    rb → rebar（螺纹钢）
    j  → coke（焦炭）
    jm → cokingcoal（焦煤）

注意 ``j`` 是焦炭、``jm`` 是焦煤；``rb`` / ``jm`` 是多字母前缀，贪婪正则天然
匹配最长字母序列，无需排序技巧。
"""
from __future__ import annotations

import re

from zixun.settings import VARIETY_LABELS

# 合约字母前缀 → 资讯 variety 标识
PREFIX_TO_VARIETY: dict[str, str] = {
    "i": "ironore",
    "rb": "rebar",
    "j": "coke",
    "jm": "cokingcoal",
}

# 黑色系通用兜底品种（目标品种 DB 无数据时退到这些）
BLACK_SECTOR_FALLBACK: tuple[str, ...] = ("ironore", "rebar")

_PREFIX_RE = re.compile(r"^([a-zA-Z]+)")


def extract_prefix(instrument: str) -> str:
    """提取合约代码的字母前缀，如 ``rb2609`` → ``rb``。无字母返回空串。"""
    if not instrument:
        return ""
    m = _PREFIX_RE.match(instrument.strip())
    if not m:
        return ""
    return m.group(1).lower()


def instrument_to_variety(instrument: str) -> tuple[str | None, str]:
    """合约 → (variety_code, prefix)。

    命中返回 (variety, prefix)；未知前缀返回 (None, prefix)，由调用方决定降级。
    """
    prefix = extract_prefix(instrument)
    variety = PREFIX_TO_VARIETY.get(prefix)
    return variety, prefix


def variety_label(variety_code: str) -> str:
    """variety → 中文名，复用 zixun.settings.VARIETY_LABELS，未知返回原值。"""
    return VARIETY_LABELS.get(variety_code, variety_code)
