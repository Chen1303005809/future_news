from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from calibration.cli import _render_calibrated_plot
from calibration.forecast_plot import build_calibrated_return_path


class CalibratedForecastPlotTests(unittest.TestCase):
    def test_segmented_interpolation_matches_all_three_day_endpoints(self) -> None:
        calibration = {
            "days": [
                {"day": 1, "applied_shift": {"return": 0.02}},
                {"day": 2, "applied_shift": {"return": -0.01}},
                {"day": 3, "applied_shift": {"return": 0.03}},
            ]
        }
        predicted_close = np.linspace(100.0, 109.0, 19)
        calibrated_returns = build_calibrated_return_path(
            predicted_close,
            100.0,
            calibration,
            [6, 13, 18],
        )

        expected_offsets = np.concatenate(
            [
                np.linspace(0.0, 0.02, 8)[1:],
                np.linspace(0.02, -0.01, 8)[1:],
                np.linspace(-0.01, 0.03, 6)[1:],
            ]
        )
        expected_returns = predicted_close / 100.0 - 1.0 + expected_offsets
        np.testing.assert_allclose(calibrated_returns, expected_returns)
        np.testing.assert_allclose(
            calibrated_returns[[6, 13, 18]],
            expected_returns[[6, 13, 18]],
        )

    def test_zero_shifts_preserve_original_hourly_path(self) -> None:
        calibration = {
            "days": [
                {"day": 1, "applied_shift": {"return": 0.0}},
                {"day": 2, "applied_shift": {"return": 0.0}},
                {"day": 3, "applied_shift": {"return": 0.0}},
            ]
        }
        close = np.linspace(100.0, 103.0, 19)
        calibrated_returns = build_calibrated_return_path(
            close, 100.0, calibration, [6, 13, 18]
        )

        np.testing.assert_allclose(calibrated_returns, close / 100.0 - 1.0)

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
                            "predicted_path": path_rows,
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
                            {
                                "day": 1,
                                "applied_shift": {"return": 0.005},
                            },
                            {
                                "day": 2,
                                "applied_shift": {"return": -0.002},
                            },
                            {
                                "day": 3,
                                "applied_shift": {"return": 0.003},
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            _render_calibrated_plot(forecast_path, calibration_path)

            self.assertTrue(output_path.exists())
            self.assertGreater(output_path.stat().st_size, 1_000)
            self.assertEqual(output_path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_invalid_day_end_indices_are_rejected(self) -> None:
        calibration = {
            "days": [
                {"day": 1, "applied_shift": {"return": 0.01}},
                {"day": 2, "applied_shift": {"return": 0.01}},
                {"day": 3, "applied_shift": {"return": 0.01}},
            ]
        }
        with self.assertRaises(ValueError):
            build_calibrated_return_path(
                np.full(19, 100.0), 100.0, calibration, [6, 13, 17]
            )

    def test_old_endpoint_shape_is_not_used(self) -> None:
        """The hourly path requires applied shifts, not point labels."""
        calibration = {
            "days": [
                {"day": 1, "calibrated": {"predicted_return": 0.01}},
                {"day": 2, "calibrated": {"predicted_return": 0.02}},
                {"day": 3, "calibrated": {"predicted_return": 0.03}},
            ]
        }
        with self.assertRaises(ValueError):
            build_calibrated_return_path(
                np.full(19, 100.0), 100.0, calibration, [6, 13, 18]
            )

if __name__ == "__main__":
    unittest.main()
