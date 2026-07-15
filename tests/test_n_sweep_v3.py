from __future__ import annotations

import math
import json
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from research_program.analysis.calculate_phase_gap_error import (
    compute_mean_abs_gap_error_per_cycle,
)
from research_program.analysis.n_sweep_metrics import (
    bounded_delivery_totals,
    convergence_safe_k_bands,
    overall_per_percent,
)
from research_program.io.send_log import add_detection_time_column


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MASTER_PATH = PROJECT_ROOT / "experiments" / "n_sweep_v3" / "initial_phase_master.json"


class FixedDenominatorPerTest(unittest.TestCase):
    def test_one_excess_send_cannot_make_per_negative(self) -> None:
        delivery = pd.DataFrame(
            {
                "expected_packets": [5, 5],
                "actual_packets": [5, 6],
                "successful_packets": [5, 6],
            }
        )
        totals = bounded_delivery_totals(delivery)
        self.assertEqual(totals["expected_packets"], 10)
        self.assertEqual(totals["actual_packets"], 10)
        self.assertEqual(totals["successful_packets"], 10)
        self.assertEqual(overall_per_percent(delivery), 0.0)


class IntendedMinGapTest(unittest.TestCase):
    def test_skip_busy_attempt_reveals_three_ms_gap(self) -> None:
        send_df = add_detection_time_column(
            pd.DataFrame(
                {
                    "time": [1000.0, 2000.0, 3000.0, 4000.0],
                    "transmission_end_time": [1000.0, 2000.0, 3000.0, 4000.0],
                    "oscillator_id": [0, 2, 3, 4],
                    "send_count": [1, 1, 1, 1],
                }
            )
        )
        carrier_sense_df = pd.DataFrame(
            {"time": [1003.0], "oscillator_id": [1], "action": ["skip_busy"]}
        )
        result = compute_mean_abs_gap_error_per_cycle(
            send_df=send_df,
            cycle_starts=np.array([0.0, 5000.0]),
            num_devices=5,
            nominal_cycle_time_ms=5000.0,
            carrier_sense_df=carrier_sense_df,
        )
        expected_min_gap = 2.0 * math.pi * 3.0 / 5000.0
        occupied_threshold = 2.0 * math.pi * 25.544 / 5000.0
        self.assertAlmostEqual(result.loc[0, "min_gap_rad"], expected_min_gap, places=12)
        self.assertLess(result.loc[0, "min_gap_rad"], occupied_threshold)
        self.assertEqual(result.loc[0, "observed_device_count"], 4)


class SafeKBandTest(unittest.TestCase):
    def test_ninety_five_percent_band_and_values(self) -> None:
        conditions = pd.DataFrame(
            {
                "coupling_function": ["KURAMOTO"] * 4,
                "device_count": [5] * 4,
                "k": [1, 2, 3, 5],
                "rate": [94.9, 95.0, 99.0, 94.0],
            }
        )
        result = convergence_safe_k_bands(conditions, rate_column="rate").iloc[0]
        self.assertEqual(result["safe_k_lower"], 2.0)
        self.assertEqual(result["safe_k_upper"], 3.0)
        self.assertEqual(result["safe_k_values"], "2;3")


class InitialPhaseMasterV3Test(unittest.TestCase):
    def test_all_prefixes_are_unique_for_all_thousand_runs(self) -> None:
        master = json.loads(MASTER_PATH.read_text(encoding="utf-8"))
        self.assertEqual(master["seed"], 20_261_990)
        self.assertEqual(master["sampling"], "uniform_without_replacement")
        self.assertEqual(len(master["start_times_by_run"]), 1_000)
        for n in (5, 10, 20, 50):
            for values in master["start_times_by_run"]:
                prefix = values[:n]
                self.assertEqual(len(prefix), n)
                self.assertEqual(len(set(prefix)), n)


if __name__ == "__main__":
    unittest.main()
