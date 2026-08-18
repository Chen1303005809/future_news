from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from calibration.article_retrieval import retrieve_articles
from zixun import filters
from zixun.cli import build_parser
from zixun.parser import parse_article, parse_list_page
from zixun.pipeline import load_config
from zixun.storage import get_conn, init_db


ROOT = Path(__file__).resolve().parent.parent
SOURCES_PATH = ROOT / "config" / "sources.yaml"


class SourceConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sources = load_config(SOURCES_PATH)

    def test_sources_have_no_priority_tier(self) -> None:
        self.assertTrue(self.sources)
        self.assertTrue(all("priority" not in source for source in self.sources))

    def test_curated_sources_include_short_term_data_and_events(self) -> None:
        source_ids = {source["id"] for source in self.sources}
        self.assertTrue(
            {
                "rebar_inventory",
                "iron_shipments",
                "iron_arrivals",
                "coal_surveys",
                "coal_auction",
                "coal_price_events",
                "black_market_flash",
            }.issubset(source_ids)
        )
        self.assertTrue(
            {
                "rebar_monthly",
                "iron_monthly",
                "coal_monthly",
                "rebar_editor_view",
            }.isdisjoint(source_ids)
        )

    def test_source_ids_are_unique(self) -> None:
        ids = [source["id"] for source in self.sources]
        self.assertEqual(len(ids), len(set(ids)))

    def test_flash_feed_requires_both_black_topic_and_event(self) -> None:
        flash = next(s for s in self.sources if s["id"] == "black_market_flash")
        self.assertEqual(
            filters.evaluate_source("山西一座焦煤矿宣布复产", flash),
            (True, "保留"),
        )
        self.assertFalse(filters.evaluate_source("山西旅游市场恢复", flash)[0])
        self.assertFalse(filters.evaluate_source("焦煤现货价格平稳", flash)[0])

    def test_event_sources_can_bypass_regional_recap_filter(self) -> None:
        cfg = {
            "enabled": True,
            "drop_regional": True,
            "regional_keywords": ["山西"],
            "global_keywords": ["全国"],
            "exclude_keywords": [],
        }
        self.assertFalse(filters.evaluate("山西煤矿复产", cfg)[0])
        self.assertTrue(
            filters.evaluate("山西煤矿复产", cfg, allow_regional=True)[0]
        )

    def test_source_can_keep_a_material_global_exclude_keyword(self) -> None:
        cfg = {
            "enabled": True,
            "drop_regional": False,
            "exclude_keywords": ["招标"],
        }
        self.assertFalse(filters.evaluate("进口焦煤采购招标", cfg)[0])
        self.assertTrue(
            filters.evaluate(
                "进口焦煤采购招标",
                cfg,
                exclude_keyword_exceptions=["招标"],
            )[0]
        )


class PriorityRemovalTests(unittest.TestCase):
    def test_cli_no_longer_exposes_priority(self) -> None:
        args = build_parser().parse_args(["run", "--dry-run"])
        self.assertFalse(hasattr(args, "priority"))
        with (
            self.assertRaises(SystemExit),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            build_parser().parse_args(["run", "--priority", "legacy"])

    def test_calibration_queries_all_articles_without_priority_filter(self) -> None:
        with patch(
            "calibration.article_retrieval.queries.list_articles",
            return_value=[],
        ) as list_articles:
            retrieve_articles(
                variety="ironore",
                origin_timestamp="2026-08-18 14:00:00",
                lookback_days=3,
                max_articles=15,
                fallback_to_black_sector=False,
            )

        self.assertNotIn("priority", list_articles.call_args.kwargs)

    def test_new_database_schema_has_no_priority_column(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.db"
            init_db(db_path)
            conn = get_conn(db_path)
            try:
                columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(articles)").fetchall()
                }
            finally:
                conn.close()

        self.assertNotIn("priority", columns)


class ParserCoverageTests(unittest.TestCase):
    def test_list_parser_resolves_relative_article_urls(self) -> None:
        html = """
        <html><body>
          <a href="/a/26081810/ABCDEF12.html" title="煤矿复产快讯">详情</a>
        </body></html>
        """
        self.assertEqual(
            parse_list_page(html, base_url="https://news.mysteel.com/"),
            [
                (
                    "https://news.mysteel.com/a/26081810/ABCDEF12.html",
                    "煤矿复产快讯",
                )
            ],
        )

    def test_list_parser_accepts_mobile_flash_urls(self) -> None:
        html = """
        <a href="//coal.m.mysteel.com/jiaotan/a/26081716/188CF7B01A1F9166_abc.html">
          Mysteel快讯：山西煤矿复产
        </a>
        """
        self.assertEqual(
            parse_list_page(html, base_url="https://m.mysteel.com/"),
            [
                (
                    "https://coal.m.mysteel.com/jiaotan/a/26081716/188CF7B01A1F9166_abc.html",
                    "Mysteel快讯：山西煤矿复产",
                )
            ],
        )

    def test_mobile_article_uses_specific_title_and_source_timestamp(self) -> None:
        html = """
        <html><body>
          <h1 class="title">煤焦热点</h1>
          <h2 class="article-title">Mysteel快讯：山西煤矿复产</h2>
          <span class="source">2026-08-17 16:37</span>
          <div class="ai-summary__text">复产煤矿涉及产能90万吨。</div>
        </body></html>
        """
        article = parse_article(
            html,
            "https://coal.m.mysteel.com/a/26081716/188CF7B01A1F9166_abc.html",
        )
        self.assertEqual(article.title, "Mysteel快讯：山西煤矿复产")
        self.assertEqual(article.publish_time, "2026-08-17 16:37")

    def test_meta_publish_timestamp_normalizes_iso_separator(self) -> None:
        html = '<meta name="publish" content="2026-08-18T10:44:40+08:00">'
        article = parse_article(
            html,
            "https://factory.mysteel.com/a/26081810/ABCDEF12.html",
            fallback_title="钢厂检修",
        )
        self.assertEqual(article.publish_time, "2026-08-18 10:44:40")


if __name__ == "__main__":
    unittest.main()
