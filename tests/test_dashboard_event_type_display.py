from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from zixun import cron, filters as filters_mod, queries, runner
import zixun.forecast_dashboard as forecast_dashboard
from zixun.forecast_dashboard import _calibration_source_rows
from zixun.settings import event_type_display


ROOT = Path(__file__).resolve().parents[1]


class DashboardEventTypeDisplayTests(unittest.TestCase):
    def test_event_type_display_keeps_unknown_values_and_labels_missing_ones(self) -> None:
        self.assertEqual(event_type_display(None), "未分类")
        self.assertEqual(event_type_display("custom_event"), "custom_event")

    def test_calibration_source_table_displays_the_content_event_type(self) -> None:
        rows = _calibration_source_rows(
            [
                {
                    "publish_time": "2026-08-20 10:00:00",
                    "report_type": "event",
                    "event_type": "transport_disruption",
                    "title": "某矿山运输中断",
                }
            ]
        )
        self.assertEqual(rows[0]["报告类型"], "event")
        self.assertEqual(rows[0]["事件类型"], "运输中断")

    def test_article_list_displays_the_content_event_type(self) -> None:
        article = {
            "id": 1,
            "url": "https://example.test/event",
            "title": "某矿山运输中断",
            "variety": "ironore",
            "report_type": "event",
            "source_channel": "news",
            "source_id": "black_market_flash",
            "publish_time": "2026-08-20 10:00:00",
            "ai_summary": "运输受阻",
            "body_text": "运输受阻导致到港节奏放缓。",
            "preview": "运输受阻导致到港节奏放缓。",
            "event_type": "transport_disruption",
        }
        filters = {
            "enabled": True,
            "drop_regional": True,
            "regional_keywords": [],
            "global_keywords": [],
            "exclude_keywords": [],
            "include_keywords": [],
        }

        with (
            patch.object(cron, "list_entries", return_value=[]),
            patch.object(runner, "get_status", return_value={}),
            patch.object(runner, "tail_log", return_value=""),
            patch.object(filters_mod, "load_filters", return_value=filters),
            patch.object(queries, "count_today", return_value=0),
            patch.object(queries, "count_total", return_value=1),
            patch.object(queries, "count_by_variety", return_value={"ironore": 1}),
            patch.object(queries, "count_by_day", return_value=[]),
            patch.object(queries, "list_articles", return_value=[article]),
            patch.object(queries, "get_article", return_value=article),
            patch.object(forecast_dashboard, "render_forecast_section"),
        ):
            app = AppTest.from_file(str(ROOT / "zixun" / "dashboard.py"))
            app.run(timeout=30)

        self.assertFalse(app.exception)
        article_table = next(
            element.value
            for element in app.dataframe
            if "标题" in element.value.columns
        )
        self.assertIn("事件类型", article_table.columns)
        self.assertEqual(article_table.iloc[0]["事件类型"], "运输中断")


if __name__ == "__main__":
    unittest.main()
