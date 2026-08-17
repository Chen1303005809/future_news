"""标题过滤与品种分类。

- 标题黑名单：所有栏目都过滤（会议/评选/招标等噪声）。
- 相关词白名单：仅对 analysis 类大杂烩栏目启用，避免抓到无关软文。
- 品种细化：根据标题关键词把频道默认品种标签收窄到具体品种。
"""
from __future__ import annotations

# 品种 → 标题关键词。用于从标题收窄品种标签。
# 注意：不含"连铁"等纯期货词，避免把期货盘面文误判为现货品种。
VARIETY_KEYWORDS: dict[str, list[str]] = {
    "rebar": ["螺纹", "建材", "建筑钢材", "线材", "盘螺"],
    "ironore": ["铁矿石", "铁矿", "进口矿", "国产矿", "球团", "精粉", "铁精粉"],
    "cokingcoal": ["焦煤", "炼焦煤", "肥煤", "瘦煤", "蒙煤", "澳煤"],
    "coke": ["焦炭", "冶金焦"],
}

# 现货基本面相关词（analysis 栏目白名单：命中其一才保留）
RELEVANT_KEYWORDS: list[str] = [
    "早报", "日报", "周报", "月报",
    "库存", "产量", "开工", "调查", "快讯",
    "调价", "出厂", "检修", "复产", "停炉", "焖炉",
    "成交", "需求", "供给", "进口", "出口", "发运", "到港", "疏港",
    "价格", "指数", "市场", "分析", "点评", "展望",
    "钢厂", "焦化", "焦化厂", "矿山", "港口",
    "铁水", "高炉", "电炉",
    "螺纹", "铁矿", "焦煤", "焦炭", "煤焦",
]


def is_blacklisted(title: str, blacklist: list[str]) -> bool:
    """标题命中黑名单词则丢弃。"""
    return any(w in title for w in blacklist)


def is_relevant(title: str) -> bool:
    """标题是否命中现货基本面相关词（analysis 栏目白名单）。"""
    return any(k in title for k in RELEVANT_KEYWORDS)


def should_keep(title: str, blacklist: list[str], apply_whitelist: bool) -> bool:
    """综合判断该文章是否保留。"""
    if is_blacklisted(title, blacklist):
        return False
    if apply_whitelist and not is_relevant(title):
        return False
    return True


def refine_variety(default_varieties: list[str], title: str) -> list[str]:
    """根据标题细化品种标签。

    若标题明确提到具体品种词，用命中词覆盖默认（收窄）；
    否则保留频道默认品种集合。
    """
    hit: list[str] = []
    for variety, keywords in VARIETY_KEYWORDS.items():
        if any(k in title for k in keywords):
            hit.append(variety)
    if hit:
        # 去重保序
        seen: list[str] = []
        for v in hit:
            if v not in seen:
                seen.append(v)
        return seen
    return list(default_varieties)
