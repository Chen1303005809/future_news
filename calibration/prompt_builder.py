"""构造带时间审计元数据的校准提示词。"""
from __future__ import annotations

from .article_retrieval import ArticleDigest
from .config import CalibrationConfig
from .forecast_loader import ForecastSnapshot
from .instrument_mapping import instrument_to_variety, variety_label

SYSTEM_PROMPT = """你是一名专注黑色系商品（螺纹钢/铁矿石/焦煤/焦炭）现货基本面的资深分析师，擅长结合产业资讯校准技术面预测。

你将收到 Kronos 对三个真实交易收盘端点的预测，以及预测起点之前可获得的产业资讯。请基于资讯做“研判 + 温和数值校准”。

规则：
1. 只使用提供的资讯，不得编造来源或数据；资讯不足时就明说不确定。
2. 只有 available_at <= forecast_origin 的资讯才可使用。统计期、事件发生时间和观察结束时间不能替代 available_at；没有发布时间的资讯必须 abstain。
3. 资讯只能影响列出的、且 target_close_at >= available_at 的收盘端点。不能把自然日 D1/D2/D3 当作端点，也不能影响 available_at 之前已经完成的收盘。
4. price_echo=true 的日报/复盘主要重复预测起点前已经发生的价格变化，默认不对数值做新增调整；只有明确的新基本面事实才可考虑，且要降低信心。
5. 同一 event_key 的跟踪报道若没有新增事实、影响规模或状态变化，只采用首次披露。
6. 校准必须温和：单日上涨概率偏移不超过 ±0.10，单日预测收益率偏移不超过 ±0.015（1.5%）。这是硬约束。
7. 严格按以下 JSON 结构返回，不要输出其他内容：

{
  "view": "bullish | bearish | range",
  "confidence": 0.0 到 1.0 之间的小数,
  "commentary": "结合资讯的市场研判，120-300 字，说明对三日走势的判断依据",
  "days": {
    "1": {"agreement": "agree | disagree", "prob_shift": 数值, "return_shift": 数值},
    "2": {"agreement": "agree | disagree", "prob_shift": 数值, "return_shift": 数值},
    "3": {"agreement": "agree | disagree", "prob_shift": 数值, "return_shift": 数值}
  }
}

字段说明：
- view：三日整体倾向。bullish 偏多 / bearish 偏空 / range 区间震荡。
- confidence：你对上述研判的把握，0-1。
- days[i].agreement：资讯是否支持该日模型预测的方向（agree 支持 / disagree 反对）。
- days[i].prob_shift：基于资讯对该日上涨概率的调整量。同意预测取接近 0，反对时取有方向的偏移。范围 [-0.10, +0.10]。
- days[i].return_shift：对预测收益率的调整量，范围 [-0.015, +0.015]。"""


def _render_article(idx: int, a: ArticleDigest, summary_cap: int, preview_cap: int) -> str:
    summary = a.ai_summary
    if len(summary) > summary_cap:
        summary = summary[:summary_cap] + "…"
    lines = [
        f"[{idx}] {a.title}",
        f"    publish_time: {a.publish_time_at or a.publish_time or '-'}  "
        f"available_at: {a.available_at or '-'}",
    ]
    age = (
        f"{a.effective_age_hours:.2f} 小时"
        if a.effective_age_hours is not None
        else "-"
    )
    delay = (
        f"{a.conclusion_delay_hours:.2f} 小时"
        if a.conclusion_delay_hours is not None
        else "-"
    )
    lines.extend(
        [
            f"    有效年龄: {age}",
            f"    可影响端点: {', '.join(a.eligible_target_close_at) or '-'}",
            f"    事件类型: {a.event_type or a.report_type or '-'}  "
            f"price_echo: {str(a.price_echo).lower()}  "
            f"conclusion_delay: {delay}  event_key: {a.event_key or '-'}",
        ]
    )
    if a.observation_start or a.observation_end or a.event_time:
        lines.append(
            f"    observation: {a.observation_start or '-'} ~ {a.observation_end or '-'}  "
            f"event_time: {a.event_time or '-'}"
        )
    if a.abstain_recommended:
        lines.append("    处理建议: abstain/显著降权（价格复述）")
    if summary:
        lines.append(f"    摘要: {summary}")
    elif a.preview:
        prev = a.preview[:preview_cap]
        lines.append(f"    正文预览: {prev}")
    return "\n".join(lines)


def build_user_message(
    snapshot: ForecastSnapshot,
    articles: list[ArticleDigest],
    config: CalibrationConfig,
) -> str:
    return _build_user_message(snapshot, articles, config)


def _build_user_message(
    snapshot: ForecastSnapshot,
    articles: list[ArticleDigest],
    config: CalibrationConfig,
) -> str:
    lines: list[str] = []

    variety, _ = instrument_to_variety(snapshot.instrument)
    label = variety_label(variety) if variety else snapshot.instrument

    lines.append("【标的】")
    lines.append(f"合约: {snapshot.instrument}  (品种: {label})")
    lines.append(
        f"forecast_origin: {snapshot.origin_timestamp}  "
        f"起点收盘价: {snapshot.origin_close}"
    )
    lines.append("目标收盘端点:")
    if snapshot.target_close_at:
        for day_number, (day, close_at) in enumerate(
            zip(snapshot.target_days, snapshot.target_close_at, strict=True),
            start=1,
        ):
            lines.append(
                f"  D{day_number}: {day} -> "
                f"{close_at.isoformat(timespec='seconds')}"
            )
    else:
        lines.append("  （缺少真实端点，不能进行可靠的端点级校准）")
    lines.append("")

    lines.append("【模型三日预测】")
    for d in snapshot.days:
        direction = "偏多" if d.up_probability >= 0.5 else "偏空"
        endpoint = (
            d.target_close_at.isoformat(timespec="seconds")
            if d.target_close_at is not None
            else "未知"
        )
        lines.append(
            f"第{d.day}日 ({endpoint}): 上涨概率 {d.up_probability:.2f} ({direction}), "
            f"预测收益率 {d.predicted_return:+.2%}"
        )
    lines.append("")

    lines.append(f"【相关资讯（available_at <= forecast_origin，共 {len(articles)} 条）】")
    if not articles:
        lines.append("（时间窗口内无相关资讯）")
    for i, a in enumerate(articles, start=1):
        lines.append(_render_article(i, a, config.ai_summary_cap, config.preview_cap))
    lines.append("")

    lines.append("请基于以上资讯，返回 JSON 校准结果。")
    return "\n".join(lines)


def build_messages(
    snapshot: ForecastSnapshot,
    articles: list[ArticleDigest],
    config: CalibrationConfig,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_message(snapshot, articles, config)},
    ]
