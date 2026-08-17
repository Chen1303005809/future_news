"""Streamlit 前端面板：黑色系现货资讯一览。

启动：
    streamlit run zixun/dashboard.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

# streamlit run 把脚本所在目录加入 sys.path，但项目根不在；
# 此处把项目根加入，使 `from zixun import ...` 可用。
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import streamlit as st  # noqa: E402

from zixun import cron, filters as filters_mod, queries, runner  # noqa: E402
from zixun.forecast_dashboard import render_forecast_section  # noqa: E402
from zixun.settings import (  # noqa: E402
    REPORT_TYPE_LABELS,
    RUN_STATUS_PATH,
    RUN_SCRIPT,
    VARIETY_LABELS,
)

st.set_page_config(
    page_title="黑色系现货资讯库", page_icon="📊", layout="wide"
)


def variety_display(s: str) -> str:
    return "/".join(VARIETY_LABELS.get(v, v) for v in (s or "").split(","))


# ============ 侧边栏筛选 ============
with st.sidebar:
    st.header("🔍 筛选")
    all_v = list(VARIETY_LABELS.keys())
    variety_sel = st.multiselect(
        "品种", all_v, default=all_v, format_func=lambda k: VARIETY_LABELS[k]
    )
    all_r = list(REPORT_TYPE_LABELS.keys())
    rt_sel = st.multiselect(
        "报告类型", all_r, default=all_r,
        format_func=lambda k: REPORT_TYPE_LABELS[k],
    )
    today = date.today()
    date_from = st.date_input("开始日期", today - timedelta(days=7))
    date_to = st.date_input("结束日期", today)
    keyword = st.text_input("关键词（标题/正文/摘要）")
    priority = st.selectbox("优先级", ["全部", "core", "optional"])

df_from = date_from.isoformat()
df_to = date_to.isoformat()
pri = None if priority == "全部" else priority
variety = variety_sel or None
report_type = rt_sel or None
kw = keyword.strip() or None


# ============ 顶部指标卡 ============
st.title("📊 黑色系现货资讯库")


# ============ 抓取与定时（自动刷新区）============
@st.fragment(run_every=5)
def _management_section() -> None:
    """抓取触发 + 定时任务管理。包在 fragment 里以独立 5s 周期自动刷新。"""
    st.subheader("⚙️ 抓取与定时")

    # ---- 状态展示 ----
    st_r = runner.get_status()
    if st_r.get("state") == "running":
        st.success(
            f"🔄 抓取进行中：PID {st_r.get('pid')} | 优先级 {st_r.get('priority')} "
            f"| 启动于 {st_r.get('start_at')}"
        )
    else:
        st.info(
            f"✅ 无抓取任务（上次结束于 {st_r.get('finish_at', '—')}）"
        )

    # ---- 触发按钮 ----
    rc1, rc2, rc3 = st.columns([1, 1, 4])
    with rc1:
        if st.button("🚀 抓取全部", key="btn_run_all", use_container_width=True):
            r = runner.start_run()
            if r["ok"]:
                st.toast("已启动抓取")
            else:
                st.toast(r["msg"], icon="⚠️")
    with rc2:
        if st.button("⚡ 仅抓 core", key="btn_run_core", use_container_width=True):
            r = runner.start_run(priority="core")
            if r["ok"]:
                st.toast("已启动 core 抓取")
            else:
                st.toast(r["msg"], icon="⚠️")
    with rc3:
        st.caption(f"日志：`{runner.PANEL_RUN_LOG}`（仅展示尾部 20 行）")

    # ---- 日志尾部 ----
    log_tail = runner.tail_log(20)
    if log_tail:
        with st.expander("抓取日志（尾部 20 行）", expanded=False):
            st.code(log_tail, language="text")

    st.divider()

    # ---- 定时任务管理 ----
    st.markdown("**🕐 定时任务（crontab）**")

    cc1, cc2 = st.columns([1, 3])
    with cc1:
        if st.button("应用预设：每天 3 次（9:13/13:17/18:23）", key="preset3"):
            added = sum(cron.add_entry(m, h, lbl) for m, h, lbl in cron.PRESET_THREE)
            st.toast(f"已添加/确认 {added} 条" if added else "预设条目均已存在")
    with cc2:
        if st.button("应用预设：每天 1 次（08:37）", key="preset1"):
            added = cron.add_entry(*cron.PRESET_ONE[0][:2], cron.PRESET_ONE[0][2])
            st.toast("已添加/确认" if added else "该预设已存在")

    # 自定义时间
    cust_cols = st.columns([1, 1, 2, 1])
    cust_minute = cust_cols[0].number_input("分", 0, 59, 7, key="cust_min")
    cust_hour = cust_cols[1].number_input("时", 0, 23, 9, key="cust_hour")
    cust_label = cust_cols[2].text_input("说明（可选）", key="cust_label")
    if cust_cols[3].button("➕ 添加", key="add_custom", use_container_width=True):
        if cron.add_entry(int(cust_minute), int(cust_hour), cust_label.strip()):
            st.toast(f"已添加 {cust_hour}:{cust_minute:02d}")
        else:
            st.toast("该时间已存在", icon="⚠️")

    # 已有定时任务列表
    entries = cron.list_entries()
    if not entries:
        st.caption("尚未配置任何定时任务。可应用上面的预设，或添加自定义时间。")
    else:
        for i, e in enumerate(entries):
            ec1, ec2, ec3, ec4 = st.columns([4, 3, 2, 1])
            ec1.code(f"{e['schedule']}  {RUN_SCRIPT}")
            ec2.write(f"📋 {e['label'] or '—'}")
            ec3.write("✅ 启用" if e["enabled"] else "⛔ 已禁用")
            ops = ec4.columns(2)
            if ops[0].button("禁用" if e["enabled"] else "启用", key=f"tg{i}"):
                cron.toggle_by_index(i, enable=not e["enabled"])
                st.rerun()
            if ops[1].button("删除", key=f"rm{i}"):
                cron.remove_by_index(i)
                st.rerun()


_management_section()


# ============ 抓取筛选规则 ============
@st.fragment
def _filter_section() -> None:
    """在线编辑抓取筛选规则（地区过滤 / 排除词 / 白名单）。"""
    with st.expander("🎯 抓取筛选规则（点击展开编辑）", expanded=False):
        cfg = filters_mod.load_filters()

        fc1, fc2 = st.columns(2)
        with fc1:
            cfg["enabled"] = st.checkbox(
                "启用筛选", value=cfg.get("enabled", True), key="f_enabled"
            )
        with fc2:
            cfg["drop_regional"] = st.checkbox(
                "丢弃地区性资讯（无全局词时，含地区词即丢）",
                value=cfg.get("drop_regional", True), key="f_regional",
                help='例如保留"全国建筑钢材早报"，丢弃"山东建筑钢材早报"',
            )

        tabs = st.tabs(["地区词", "全局词", "排除词", "白名单"])
        labels = [
            ("regional_keywords", "每行一个地区词（省份/城市/港口/区域）"),
            ("global_keywords", "每行一个全局词（命中则不视为地区资讯）"),
            ("exclude_keywords", "每行一个排除词（任何栏目命中即丢）"),
            ("include_keywords", "每行一个相关词（analysis 类须命中其一）"),
        ]
        for tab, (key, hint) in zip(tabs, labels):
            with tab:
                words = cfg.get(key, []) or []
                text = st.text_area(
                    hint, value="\n".join(words), height=150, key=f"f_{key}"
                )
                cfg[key] = [w.strip() for w in text.splitlines() if w.strip()]

        sc1, sc2 = st.columns([1, 5])
        if sc1.button("💾 保存规则", type="primary", use_container_width=True):
            filters_mod.save_filters(cfg)
            st.toast("筛选规则已保存")
            st.rerun()
        sc2.caption(
            "配置文件：`config/filters.yaml`；保存后对后续抓取生效。"
            " 可用 `python -m zixun.cli run --dry-run --source <id>` 预览。"
        )


_filter_section()

st.divider()


# ============ 顶部指标卡 ============
c1, c2, c3, c4 = st.columns(4)
c1.metric("今日新增", queries.count_today())
week_ago = (today - timedelta(days=7)).isoformat()
c2.metric("近 7 天总数", queries.count_total(date_from=week_ago))
c3.metric(
    "当前筛选结果",
    queries.count_total(
        variety=variety, date_from=df_from, date_to=df_to, priority=pri
    ),
)
vc = queries.count_by_variety(date_from=df_from, date_to=df_to, priority=pri)
c4.metric("涉及品种", len(vc))

# 品种分布小条
if vc:
    vc_disp = {VARIETY_LABELS.get(k, k): v for k, v in vc.items()}
    c4.caption(" / ".join(f"{k} {v}" for k, v in vc_disp.items()))


# ============ 资讯密度图 ============
st.subheader("📈 资讯密度（按日 × 品种）")
day_data = queries.count_by_day(
    variety=variety, date_from=df_from, date_to=df_to, priority=pri
)
if day_data:
    ddf = pd.DataFrame(day_data)
    ddf["variety_label"] = ddf["variety"].map(lambda v: VARIETY_LABELS.get(v, v))
    fig = px.bar(
        ddf, x="day", y="count", color="variety_label",
        labels={"day": "日期", "count": "篇数", "variety_label": "品种"},
        height=360,
    )
    st.plotly_chart(fig, width="stretch")
else:
    st.info("当前筛选条件下无数据。")


# ============ 文章列表 + 详情 ============
arts = queries.list_articles(
    variety=variety, report_type=report_type,
    date_from=df_from, date_to=df_to,
    keyword=kw, priority=pri, limit=500,
)

st.subheader(f"📰 文章列表（共 {len(arts)} 篇，最多展示 500）")
if not arts:
    st.info("无匹配文章。")
else:
    adf = pd.DataFrame(arts)
    adf["品种"] = adf["variety"].map(variety_display)
    adf["类型"] = adf["report_type"].map(lambda r: REPORT_TYPE_LABELS.get(r, r))
    show = adf[["publish_time", "品种", "类型", "title", "preview"]].copy()
    show.columns = ["发布时间", "品种", "类型", "标题", "摘要预览"]
    st.dataframe(show, width="stretch", height=420, hide_index=True)

    st.subheader("🔎 查看详情")
    options = {
        f"#{a['id']}  {(a['publish_time'] or '?')[:10]}  {a['title'][:40]}": a["id"]
        for a in arts
    }
    sel = st.selectbox("选择文章查看全文", list(options.keys()))
    if sel:
        detail = queries.get_article(options[sel])
        if detail:
            st.markdown(f"### {detail['title']}")
            m1, m2, m3 = st.columns(3)
            m1.markdown(f"**品种**：{variety_display(detail['variety'])}")
            m2.markdown(
                f"**类型**：{REPORT_TYPE_LABELS.get(detail['report_type'], detail['report_type'])}"
            )
            m3.markdown(f"**发布时间**：{detail['publish_time']}")
            st.caption(
                f"频道：{detail['source_channel']} | 栏目：{detail['source_id']}"
            )
            if detail.get("ai_summary"):
                st.markdown("**🤖 AI 摘要**")
                st.info(detail["ai_summary"])
            st.markdown("**正文**")
            st.markdown(detail["body_text"] or "（正文为空）")
            st.markdown(f"[🔗 查看原文]({detail['url']})")


# ============ 走势预测（Kronos 三日 · 资讯校准）============
st.divider()
render_forecast_section()
