"""面板"走势预测"区块渲染。

布局（简洁有序，自上而下）：
    🔮 走势预测
    ├─ 合约选择（按品种分组下拉）+ [▶️ 一键预测 + 校准]
    ├─ 任务状态行 + 日志尾部（可折叠）
    └─ 结果区（产物存在时展示）：
        ├─ 元信息行：起点时间 / 起点收盘价 / 目标三日
        ├─ 走势图（forecast_plot.png）
        ├─ 三日概率/收益率对比表（原始 vs 校准 vs 偏移）
        ├─ LLM 研判卡：方向徽章 + 置信度 + 研判全文
        └─ 来源资讯列表
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from .forecast_runner import (
    get_status,
    list_contracts,
    load_display_data,
    start_forecast,
    tail_log,
)
from .settings import VARIETY_LABELS

VIEW_LABELS = {
    "bullish": "🐂 偏多",
    "bearish": "🐻 偏空",
    "range": "⚖️ 震荡",
}


def _contract_label(c: dict) -> str:
    label = VARIETY_LABELS.get(c["variety"], c["variety"])
    return f"{label} {c['contract']}（{c['bars']} 根K线）"


@st.fragment(run_every=5)
def render_forecast_section() -> None:
    st.subheader("🔮 走势预测（Kronos 三日 · 资讯校准）")

    contracts = list_contracts()
    if not contracts:
        st.info("kline_data 下无有效K线数据。")
        return

    # ---- 合约选择 + 触发按钮 ----
    col_sel, col_btn, col_hint = st.columns([2, 1, 2])
    with col_sel:
        idx = st.selectbox(
            "选择合约",
            range(len(contracts)),
            format_func=lambda i: _contract_label(contracts[i]),
            key="fc_contract_idx",
        )
        chosen = contracts[idx]
    with col_btn:
        st.write("")  # 对齐 selectbox
        if st.button(
            "▶️ 一键预测 + 校准", key="btn_forecast", use_container_width=True
        ):
            r = start_forecast(chosen["contract"])
            if r["ok"]:
                st.toast(f"已启动 {chosen['contract']} 预测，稍候自动刷新")
            else:
                st.toast(r["msg"], icon="⚠️")
            st.rerun()
    with col_hint:
        st.caption("预测在后台串行执行：Kronos 三日走势 → 资讯校准（LLM 研判）")

    # ---- 任务状态 ----
    st_r = get_status()
    if st_r.get("state") == "running":
        st.success(
            f"🔄 预测进行中：{st_r.get('contract')} | PID {st_r.get('pid')} "
            f"| 启动于 {st_r.get('start_at')}（每 5 秒自动刷新）"
        )
    elif st_r.get("state") == "failed":
        st.error(
            f"❌ 上次预测失败：{st_r.get('contract')}（结束于 {st_r.get('finish_at')}），"
            f"详见日志"
        )
    elif st_r.get("state") == "finished":
        cal = "✅ 已校准" if st_r.get("calibrated") else "⚠️ 仅预测（校准未完成）"
        st.info(f"✅ 上次任务完成：{st_r.get('contract')} {cal}（{st_r.get('finish_at')}）")

    log_tail = tail_log(15)
    if log_tail:
        with st.expander("预测日志（尾部 15 行）", expanded=False):
            st.code(log_tail, language="text")

    # ---- 结果区 ----
    data = load_display_data(chosen["contract"])
    if data is None:
        st.caption(
            f"「{chosen['contract']}」尚无预测产物，点击上方按钮生成。"
        )
        return

    fc = data["forecast"]
    m1, m2, m3 = st.columns(3)
    m1.metric("预测起点收盘价", fc.get("origin_close"))
    m2.metric("预测起点时间", str(fc.get("origin_timestamp") or "—"))
    m3.metric("目标三日", "、".join(str(d)[:10] for d in fc.get("target_days") or ["—"]))

    if data.get("plot_path") is not None:
        st.image(str(data["plot_path"]), caption="三日走势预测（10%-90% 区间 + 中位数）")

    calib = data.get("calibration")
    if calib is None:
        st.info("预测完成，尚无校准结果（可能校准失败或未运行）。")
        return

    _render_calibration(calib)


def _render_calibration(calib: dict) -> None:
    """渲染 LLM 研判 + 三日对比表 + 来源资讯。"""
    # ---- LLM 研判卡 ----
    view = calib.get("view") or "range"
    conf = calib.get("confidence")
    v1, v2 = st.columns([1, 3])
    with v1:
        st.markdown(f"### {VIEW_LABELS.get(view, view)}")
    with v2:
        st.metric("研判置信度", f"{conf:.0%}" if isinstance(conf, (int, float)) else "—")
    st.markdown(calib.get("commentary") or "（无研判文本）")

    meta = calib.get("meta") or {}
    badges: list[str] = []
    if meta.get("variety_fallback"):
        badges.append("品种兜底：黑色系通用资讯")
    if meta.get("skipped_reason"):
        badges.append(f"未校准：{meta['skipped_reason']}")
    if meta.get("llm_error"):
        badges.append(f"LLM 错误")
    if badges:
        st.caption(" ｜ ".join(badges))

    # ---- 三日对比表 ----
    rows = []
    for d in calib.get("days") or []:
        orig = d.get("original") or {}
        cal = d.get("calibrated") or {}
        shift = d.get("applied_shift") or {}
        rows.append(
            {
                "日": f"第{d.get('day')}日",
                "上涨概率(原)": orig.get("up_probability"),
                "上涨概率(校准)": cal.get("up_probability"),
                "概率偏移": shift.get("prob"),
                "收益率(原)": orig.get("predicted_return"),
                "收益率(校准)": cal.get("predicted_return"),
                "收益率偏移": shift.get("return"),
                "资讯认同": d.get("agreement"),
                "方向反转": "⚠️ 是" if d.get("direction_flipped") else "",
            }
        )
    if rows:
        df = pd.DataFrame(rows)
        fmt = {
            "上涨概率(原)": "{:.2f}",
            "上涨概率(校准)": "{:.2f}",
            "概率偏移": "{:+.2f}",
            "收益率(原)": "{:+.2%}",
            "收益率(校准)": "{:+.2%}",
            "收益率偏移": "{:+.2%}",
        }
        st.dataframe(df.style.format(fmt, na_rep="—"), width="stretch", hide_index=True)

    # ---- 来源资讯 ----
    sources = calib.get("sources") or []
    if sources:
        with st.expander(f"📚 校准参考资讯（{len(sources)} 条）", expanded=False):
            srows = [
                {
                    "发布时间": s.get("publish_time"),
                    "类型": s.get("report_type"),
                    "标题": s.get("title"),
                }
                for s in sources
            ]
            st.dataframe(pd.DataFrame(srows), width="stretch", hide_index=True)
