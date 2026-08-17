from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from calibration.cli import _render_calibrated_plot
from calibration.forecast_plot import (
    calibrated_day_endpoints,
)


class CalibratedForecastPlotTests(unittest.TestCase):
    def test_day_endpoints_use_calibrated_returns_and_probabilities(self) -> None:
        calibration = {
            "days": [
                {"day": 1, "calibrated": {"predicted_return": 0.01, "up_probability": 0.6}},
                {"day": 2, "calibrated": {"predicted_return": -0.02, "up_probability": 0.4}},
                {"day": 3, "calibrated": {"predicted_return": 0.03, "up_probability": 0.7}},
            ]
        }
        timestamps = pd.DatetimeIndex(
            ["2026-01-02 14:00:00", "2026-01-03 14:00:00", "2026-01-04 14:00:00"]
        )

        x_values, y_values, labels = calibrated_day_endpoints(
            calibration,
            origin_timestamp=pd.Timestamp("2026-01-01 14:00:00"),
            target_timestamps=timestamps,
            day_end_indices=[0, 1, 2],
        )

        self.assertEqual(list(x_values), [pd.Timestamp("2026-01-01 14:00:00"), *list(timestamps)])
        np.testing.assert_allclose(y_values, [0.0, 0.01, -0.02, 0.03])
        self.assertEqual(
            labels,
            ["D1: +1.0%\np=60%", "D2: -2.0%\np=40%", "D3: +3.0%\np=70%"],
        )

    def test_successful_calibration_replaces_forecast_plot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "kline.json"
            forecast_path = root / "forecast_result.json"
            calibration_path = root / "calibration.json"
            output_path = root / "forecast_plot.png"
            source_path.write_text(
                json.dumps(
                    {
                        "data": [
                            {"TeD": "20260101", "T": "09:00:00", "C": 98},
                            {"TeD": "20260101", "T": "10:00:00", "C": 99},
                            {"TeD": "20260101", "T": "14:00:00", "C": 100},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            path_rows = [[100, 101, 99, close] for close in (101, 102, 103)]
            forecast_path.write_text(
                json.dumps(
                    {
                        "source": {"path": str(source_path)},
                        "config": {"lookback": 3, "sample_count": 10},
                        "kronos": {
                            "instrument": "rb2701",
                            "origin_timestamp": "2026-01-01T14:00:00",
                            "origin_close": 100,
                            "target_days": ["2026-01-02", "2026-01-03", "2026-01-04"],
                            "target_timestamps": [
                                "2026-01-02T14:00:00",
                                "2026-01-03T14:00:00",
                                "2026-01-04T14:00:00",
                            ],
                            "day_end_indices": [0, 1, 2],
                            "actual_path": path_rows,
                            "q10_path": path_rows,
                            "median_path": path_rows,
                            "q90_path": path_rows,
                        },
                    }
                ),
                encoding="utf-8",
            )
            calibration_path.write_text(
                json.dumps(
                    {
                        "days": [
                            {"day": 1, "calibrated": {"predicted_return": 0.005, "up_probability": 0.55}},
                            {"day": 2, "calibrated": {"predicted_return": 0.01, "up_probability": 0.6}},
                            {"day": 3, "calibrated": {"predicted_return": 0.015, "up_probability": 0.65}},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            _render_calibrated_plot(forecast_path, calibration_path)

            self.assertTrue(output_path.exists())
            self.assertGreater(output_path.stat().st_size, 1_000)
            self.assertEqual(output_path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
