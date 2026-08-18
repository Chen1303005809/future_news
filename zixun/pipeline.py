"""抓取主流程：读配置 → 列表页 → 过滤 → 去重 → 详情页 → 分类 → 入库 + 导出。"""
from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

import yaml

from .classifier import refine_variety
from .fetcher import Fetcher, FetchError
from . import filters as filters_mod
from .parser import parse_article, parse_list_page
from .settings import CONFIG_PATH, DB_PATH
from .storage import (
    exists_url_hash,
    export_markdown,
    get_conn,
    hash_url,
    insert_article,
)

logger = logging.getLogger(__name__)


def load_config(path: Path = CONFIG_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("sources", [])


def _new_stats() -> dict:
    return {
        "listed": 0,     # 列表页解析出的文章链接数
        "filtered": 0,   # 被筛选规则丢弃的
        "kept": 0,       # 通过筛选
        "new": 0,        # 新入库 / dry-run 中视为新增
        "skipped": 0,    # 已存在跳过
        "failed": 0,     # 抓取/解析失败
    }


def run(
    *,
    dry_run: bool = False,
    source_id: str | None = None,
    max_pages_override: int | None = None,
    config_path: Path = CONFIG_PATH,
    db_path: Path = DB_PATH,
) -> dict[str, dict]:
    """执行抓取。返回每个栏目的统计 {source_id: stats}。"""
    sources = load_config(config_path)
    if source_id:
        sources = [s for s in sources if s["id"] == source_id]
        if not sources:
            logger.error("未找到栏目: %s", source_id)
            return {}
    fetcher = Fetcher()
    conn = None if dry_run else get_conn(db_path)
    filter_cfg = filters_mod.load_filters()
    all_stats: dict[str, dict] = defaultdict(_new_stats)

    try:
        for src in sources:
            sid = src["id"]
            st = all_stats[sid]
            apply_whitelist = src.get("report_type") == "analysis"
            max_pages = (
                max_pages_override
                if max_pages_override
                else src.get("max_pages", 1)
            )

            for page in range(1, max_pages + 1):
                list_url = src["list_url"].format(page=page)
                try:
                    html = fetcher.get_html(list_url)
                except FetchError as exc:
                    logger.error("列表页抓取失败 %s: %s", list_url, exc)
                    st["failed"] += 1
                    break  # 该栏目列表抓失败，放弃该栏目后续页

                items = parse_list_page(html, base_url=list_url)
                st["listed"] += len(items)
                logger.info(
                    "[%s] 第%d页 解析到 %d 篇", sid, page, len(items)
                )

                for art_url, title in items:
                    keep, reason = filters_mod.evaluate(
                        title,
                        filter_cfg,
                        apply_whitelist=apply_whitelist,
                        allow_regional=bool(src.get("allow_regional")),
                        exclude_keyword_exceptions=src.get(
                            "exclude_keyword_exceptions"
                        ),
                    )
                    if keep:
                        keep, reason = filters_mod.evaluate_source(title, src)
                    if not keep:
                        st["filtered"] += 1
                        if dry_run:
                            logger.info(
                                "[dry-run] 丢弃 [%s|%s] %s | %s",
                                sid, reason, art_url, title,
                            )
                        continue
                    st["kept"] += 1

                    if conn and exists_url_hash(conn, hash_url(art_url)):
                        st["skipped"] += 1
                        continue

                    try:
                        ahtml = fetcher.get_html(art_url)
                    except FetchError as exc:
                        logger.warning("详情页抓取失败 %s: %s", art_url, exc)
                        st["failed"] += 1
                        continue

                    art = parse_article(ahtml, art_url, fallback_title=title)
                    classification_text = art.title
                    if len(src["variety"]) > 1:
                        classification_text += " " + (art.ai_summary or art.body_text[:500])
                    variety = refine_variety(src["variety"], classification_text)

                    if dry_run:
                        st["new"] += 1
                        logger.info(
                            "[dry-run] 新文章 [%s|%s] %s | %s",
                            sid, "/".join(variety), art.publish_time or "?", art.title,
                        )
                        continue

                    added = insert_article(
                        conn,
                        article=art,
                        variety=variety,
                        report_type=src["report_type"],
                        source_channel=src["channel"],
                        source_id=sid,
                    )
                    if added:
                        export_markdown(
                            art,
                            variety=variety,
                            report_type=src["report_type"],
                            source_channel=src["channel"],
                        )
                        st["new"] += 1
                        logger.info("新增 [%s] %s", sid, art.title)
                    else:
                        st["skipped"] += 1
    finally:
        if conn:
            conn.close()
        fetcher.close()

    return all_stats
