"""构造 LLM 的 system / user 消息。

- system：角色定义（黑色系商品分析师）+ 输出 JSON 契约 + 温和校准约束。
- user：标的（品种中文名、合约、origin_close）、三日原始预测（概率+收益率）、
  时间窗口内编号化资讯（字段截断防上下文爆炸）。
"""
from __future__ import annotations

from .article_retrieval import ArticleDigest
from .config import CalibrationConfig
from .forecast_loader import ForecastSnapshot
from .instrument_mapping import instrument_to_variety, variety_label

SYSTEM_PROMPT = """你是一名专注黑色系商品（螺纹钢/铁矿石/焦煤/焦炭）现货基本面的资深分析师，擅长结合产业资讯校准技术面预测。

你将收到一份 Kronos 时序模型对目标合约未来三个交易日的预测（每日的上涨概率与预测收益率），以及预测时点之前一段时间的相关产业资讯。请基于资讯对模型预测做"研判 + 温和数值校准"。

规则：
1. 只使用提供的资讯，不得编造来源或数据；资讯不足时就明说不确定。
2. 校准必须温和：单日上涨概率偏移不超过 ±0.10，单日预测收益率偏移不超过 ±0.015（1.5%）。这是硬约束。
3. 严格按以下 JSON 结构返回，不要输出其他内容：

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
        f"    时间: {a.publish_time}  类型: {a.report_type or '-'}",
    ]
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
        f"预测起点: {snapshot.origin_timestamp}  "
        f"起点收盘价: {snapshot.origin_close}"
    )
    lines.append(f"目标三日: {', '.join(snapshot.target_days)}")
    lines.append("")

    lines.append("【模型三日预测】")
    for d in snapshot.days:
        direction = "偏多" if d.up_probability >= 0.5 else "偏空"
        lines.append(
            f"第{d.day}日: 上涨概率 {d.up_probability:.2f} ({direction}), "
            f"预测收益率 {d.predicted_return:+.2%}"
        )
    lines.append("")

    lines.append(f"【相关资讯（预测起点前，共 {len(articles)} 条）】")
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
