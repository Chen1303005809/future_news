from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from calibration.article_retrieval import retrieve_articles
from calibration.forecast_loader import load_forecast
from calibration.prompt_builder import build_messages
from calibration.config import CalibrationConfig
from zixun.time_alignment import (
    AlignmentPolicy,
    ForecastEndpoint,
    align_article,
    parse_shanghai_datetime,
)
from kronos.three_day_json_forecast import _parse_provider_payload


def endpoint(index: int, value: str, trading_day: str) -> ForecastEndpoint:
    return ForecastEndpoint(
        index=index,
        day=index + 1,
        trading_day=trading_day,
        target_close_at=parse_shanghai_datetime(value),
    )


class TimeAlignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.origin = parse_shanghai_datetime("2026-08-07T14:00:00+08:00")
        self.endpoints = (
            endpoint(0, "2026-08-07T15:00:00+08:00", "2026-08-07"),
            endpoint(1, "2026-08-07T23:00:00+08:00", "2026-08-07"),
            endpoint(2, "2026-08-10T15:00:00+08:00", "2026-08-10"),
        )

    def test_day_session_article_can_reach_same_15_close(self) -> None:
        result = align_article(
            {"id": 1, "title": "钢厂检修", "report_type": "event", "publish_time": "2026-08-07 14:30:00"},
            forecast_origin="2026-08-07 14:45:00",
            endpoints=self.endpoints,
        )
        self.assertEqual(result.eligible_endpoint_indices, (0, 1, 2))

    def test_after_day_close_starts_at_night_endpoint(self) -> None:
        result = align_article(
            {"id": 2, "title": "事故快讯", "report_type": "event", "publish_time": "2026-08-07 15:01:00"},
            forecast_origin="2026-08-07 16:00:00",
            endpoints=self.endpoints,
        )
        self.assertEqual(result.eligible_endpoint_indices, (1, 2))

    def test_night_article_can_reach_night_close(self) -> None:
        result = align_article(
            {"id": 3, "title": "矿山运输中断", "report_type": "event", "publish_time": "2026-08-07 21:30:00"},
            forecast_origin="2026-08-07 22:00:00",
            endpoints=self.endpoints,
        )
        self.assertEqual(result.eligible_endpoint_indices, (1, 2))

    def test_friday_night_weekend_and_holiday_are_explicit_endpoints(self) -> None:
        result = align_article(
            {"id": 4, "title": "周末政策正式发布", "report_type": "event", "publish_time": "2026-08-07 22:30:00"},
            forecast_origin="2026-08-07 23:00:00",
            endpoints=self.endpoints,
        )
        self.assertEqual(result.eligible_endpoint_indices, (1, 2))
        self.assertEqual(result.eligible_target_close_at[1], "2026-08-10T15:00:00+08:00")

    def test_weekend_or_holiday_article_uses_next_explicit_close(self) -> None:
        endpoints = (
            endpoint(0, "2026-10-09T15:00:00+08:00", "2026-10-09"),
            endpoint(1, "2026-10-12T15:00:00+08:00", "2026-10-12"),
        )
        result = align_article(
            {
                "id": 41,
                "title": "节假日期间港口运输恢复",
                "report_type": "event",
                "publish_time": "2026-10-06 10:00:00",
            },
            forecast_origin="2026-10-08 10:00:00",
            endpoints=endpoints,
        )
        self.assertEqual(result.eligible_endpoint_indices, (0, 1))

    def test_statistical_period_does_not_replace_first_publish_time(self) -> None:
        result = align_article(
            {
                "id": 5,
                "title": "铁矿发运周度数据",
                "report_type": "data",
                "publish_time": "2026-08-07 13:30:00",
                "observation_start": "2026-07-27 00:00:00",
                "observation_end": "2026-08-02 23:59:59",
            },
            forecast_origin=self.origin,
            endpoints=self.endpoints,
        )
        self.assertEqual(result.available_at, parse_shanghai_datetime("2026-08-07T13:30:00+08:00"))
        self.assertEqual(result.observation_end, parse_shanghai_datetime("2026-08-02T23:59:59+08:00"))
        self.assertAlmostEqual(result.conclusion_delay_hours or 0.0, 109.5003, places=3)

    def test_available_at_is_never_before_publish_time(self) -> None:
        result = align_article(
            {
                "id": 51,
                "title": "结构化数据",
                "report_type": "data",
                "publish_time": "2026-08-07 13:30:00",
                "available_at": "2026-08-07 12:00:00",
            },
            forecast_origin=self.origin,
            endpoints=self.endpoints,
        )
        self.assertEqual(result.available_at, parse_shanghai_datetime("2026-08-07T13:30:00+08:00"))

    def test_daily_price_recap_is_price_echo_and_has_delay(self) -> None:
        result = align_article(
            {
                "id": 6,
                "title": "Mysteel日报：今日铁矿期货收盘复盘",
                "report_type": "daily",
                "publish_time": "2026-08-07 17:00:00",
                "observation_end": "2026-08-07 15:00:00",
            },
            forecast_origin=self.origin,
            endpoints=self.endpoints,
        )
        self.assertTrue(result.price_echo)
        self.assertEqual(result.event_type, "daily_recap")
        self.assertGreater(result.conclusion_delay_hours or 0.0, 0.0)
        self.assertEqual(result.abstain_recommended, True)

    def test_event_window_is_configurable(self) -> None:
        policy = AlignmentPolicy(impact_window_hours={"flash_event": 2.0})
        result = align_article(
            {
                "id": 7,
                "title": "事故快讯",
                "report_type": "event",
                "source_id": "black_market_flash",
                "publish_time": "2026-08-07 21:30:00",
            },
            forecast_origin="2026-08-07 22:00:00",
            endpoints=self.endpoints,
            policy=policy,
        )
        self.assertEqual(result.eligible_endpoint_indices, (1,))

    def test_missing_publish_time_abstains(self) -> None:
        result = align_article(
            {"id": 8, "title": "无发布时间的快讯", "report_type": "event"},
            forecast_origin=self.origin,
            endpoints=self.endpoints,
        )
        self.assertEqual(result.abstain_reason, "missing_publish_time")
        self.assertEqual(result.eligible_endpoint_indices, ())

    def test_retrieval_marks_missing_publish_time_as_abstain(self) -> None:
        with patch(
            "calibration.article_retrieval.queries.list_articles",
            return_value=[
                {"id": 81, "title": "无时间", "report_type": "event", "ai_summary": "事件正文足够长"}
            ],
        ):
            articles, meta = retrieve_articles(
                variety="ironore",
                origin_timestamp="2026-08-07 14:00:00",
                lookback_days=3,
                max_articles=15,
                fallback_to_black_sector=False,
            )
        self.assertEqual(articles, [])
        self.assertEqual(meta.abstain_reason, "missing_publish_time")

    def test_future_article_is_excluded_by_retrieval(self) -> None:
        rows = [
            {"id": 9, "title": "未来文章", "report_type": "event", "publish_time": "2026-08-07 14:01:00", "ai_summary": "new facts"},
            {"id": 10, "title": "已知文章", "report_type": "event", "publish_time": "2026-08-07 13:59:00", "ai_summary": "known facts"},
        ]
        with patch("calibration.article_retrieval.queries.list_articles", return_value=rows):
            articles, _ = retrieve_articles(
                variety="ironore",
                origin_timestamp="2026-08-07 14:00:00",
                lookback_days=3,
                max_articles=15,
                fallback_to_black_sector=False,
            )
        self.assertEqual([article.id for article in articles], [10])

    def test_repeated_event_keeps_first_disclosure_and_new_increment(self) -> None:
        rows = [
            {"id": 12, "title": "事故后续", "report_type": "event", "event_key": "mine-1", "publish_time": "2026-08-07 13:30:00", "ai_summary": "first"},
            {"id": 13, "title": "事故跟踪", "report_type": "event", "event_key": "mine-1", "publish_time": "2026-08-07 13:40:00", "ai_summary": "same"},
            {"id": 14, "title": "事故影响扩大", "report_type": "event", "event_key": "mine-1", "information_increment": 1, "publish_time": "2026-08-07 13:50:00", "ai_summary": "expanded"},
        ]
        with patch("calibration.article_retrieval.queries.list_articles", return_value=rows):
            articles, _ = retrieve_articles(
                variety="ironore",
                origin_timestamp="2026-08-07 14:00:00",
                lookback_days=3,
                max_articles=15,
                fallback_to_black_sector=False,
            )
        self.assertEqual(sorted(article.id for article in articles), [12, 14])

    def test_prompt_contains_alignment_metadata_and_real_endpoints(self) -> None:
        result = align_article(
            {"id": 15, "title": "运输中断", "report_type": "event", "publish_time": "2026-08-07 13:00:00"},
            forecast_origin=self.origin,
            endpoints=self.endpoints,
        )
        from calibration.article_retrieval import ArticleDigest
        from calibration.forecast_loader import DayForecast, ForecastSnapshot

        digest = ArticleDigest.from_alignment(result, ai_summary="运输受阻", preview="")
        snapshot = ForecastSnapshot(
            instrument="i2609",
            origin_timestamp="2026-08-07T14:00:00+08:00",
            origin_trading_day="2026-08-07",
            origin_close=100.0,
            target_days=("2026-08-07", "2026-08-10", "2026-08-11"),
            target_close_at=tuple(item.target_close_at for item in self.endpoints),
            days=(
                DayForecast(1, 0.5, 0.01, "test", self.endpoints[0].target_close_at),
                DayForecast(2, 0.5, 0.01, "test", self.endpoints[1].target_close_at),
                DayForecast(3, 0.5, 0.01, "test", self.endpoints[2].target_close_at),
            ),
        )
        message = build_messages(snapshot, [digest], CalibrationConfig())[1]["content"]
        self.assertIn("available_at", message)
        self.assertIn("有效年龄", message)
        self.assertIn("price_echo", message)
        self.assertIn("2026-08-10T15:00:00+08:00", message)

    def test_loader_reads_explicit_non_consecutive_close_endpoints(self) -> None:
        payload = {
            "kronos": {
                "instrument": "i2609",
                "forecast_origin": "2026-08-07T14:00:00+08:00",
                "origin_timestamp": "2026-08-07T14:00:00+08:00",
                "origin_trading_day": "2026-08-07",
                "origin_close": 100,
                "target_days": ["2026-08-07", "2026-08-10", "2026-08-11"],
                "target_close_at": [
                    "2026-08-07T15:00:00+08:00",
                    "2026-08-10T15:00:00+08:00",
                    "2026-08-11T15:00:00+08:00",
                ],
                "day1_up_probability": 0.5,
                "day2_up_probability": 0.5,
                "day3_up_probability": 0.5,
                "day_end_indices": [0, 1, 2],
                "predicted_path": [[101, 101, 99, 101, 1, 1], [102, 102, 100, 102, 1, 1], [103, 103, 101, 103, 1, 1]],
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "forecast_result.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            snapshot = load_forecast(path)
        self.assertEqual(
            [value.isoformat() for value in snapshot.target_close_at],
            [
                "2026-08-07T15:00:00+08:00",
                "2026-08-10T15:00:00+08:00",
                "2026-08-11T15:00:00+08:00",
            ],
        )

    def test_provider_ti_d_is_trading_day_and_c_closes_after_t(self) -> None:
        frame, _ = _parse_provider_payload("kline_data/kline_i2609.json")
        first = frame.iloc[0]
        self.assertEqual(str(first["trading_day"].date()), "2025-09-15")
        self.assertEqual(first["timestamps"].isoformat(), "2025-09-12T21:00:00+08:00")
        self.assertEqual(first["close_timestamps"].isoformat(), "2025-09-12T22:00:00+08:00")
        day_close = frame.loc[frame["bar_time"] == "14:00:00"].iloc[0]
        self.assertEqual(day_close["close_timestamps"].isoformat()[-14:], "15:00:00+08:00")


if __name__ == "__main__":
    unittest.main()
