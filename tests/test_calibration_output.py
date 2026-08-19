from __future__ import annotations

import unittest
from pathlib import Path

from calibration.calibration_engine import apply_calibration
from calibration.cli import _passthrough_result
from calibration.config import CalibrationConfig
from calibration.forecast_loader import load_forecast
from calibration.output_writer import build_output


ROOT = Path(__file__).resolve().parents[1]


class CalibrationOutputTests(unittest.TestCase):
    def test_output_keeps_target_close_at_for_calibrated_days(self) -> None:
        snapshot = load_forecast(
            ROOT / "calibration" / "fixtures" / "forecast_backtest_i2609.json"
        )
        parsed = {
            "view": "bullish",
            "confidence": 0.8,
            "commentary": "regression test",
            "days": {
                str(day): {
                    "agreement": "agree",
                    "prob_shift": 0.0,
                    "return_shift": 0.0,
                }
                for day in (1, 2, 3)
            },
        }
        config = CalibrationConfig()
        result = apply_calibration(snapshot, parsed, config)

        output = build_output(snapshot, result, config)

        expected = [
            value.isoformat(timespec="seconds")
            for value in snapshot.target_close_at
        ]
        self.assertEqual(output["forecast"]["target_close_at"], expected)
        self.assertEqual(
            [day["target_close_at"] for day in output["days"]],
            expected,
        )

    def test_output_keeps_target_close_at_for_passthrough_days(self) -> None:
        snapshot = load_forecast(
            ROOT / "calibration" / "fixtures" / "forecast_backtest_i2609.json"
        )
        result = _passthrough_result(
            snapshot,
            view="range",
            confidence=0.0,
            commentary="no articles",
            llm_meta={"skipped": "no_articles"},
        )

        output = build_output(snapshot, result, CalibrationConfig())

        expected = [
            value.isoformat(timespec="seconds")
            for value in snapshot.target_close_at
        ]
        self.assertEqual(
            [day["target_close_at"] for day in output["days"]],
            expected,
        )


if __name__ == "__main__":
    unittest.main()
