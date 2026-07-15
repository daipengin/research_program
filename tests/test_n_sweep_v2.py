from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from research_program.analysis.calculate_phase_gap_error import (
    compute_mean_abs_gap_error_per_cycle,
)
from research_program.analysis.n_sweep_metrics import censored_convergence_median
from research_program.graph_workflow.execution import _initial_start_times_by_run
from research_program.io.send_log import add_detection_time_column


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MASTER_PATH = PROJECT_ROOT / "experiments" / "n_sweep_v2" / "initial_phase_master.json"


class PhaseMetricsV2Test(unittest.TestCase):
    def test_known_gaps_include_minimum_gap(self) -> None:
        starts = np.array([0.0, 900.0, 2000.0, 3000.0, 4000.0])
        send_df = add_detection_time_column(pd.DataFrame({
            "time": starts,
            "transmission_end_time": starts,
            "oscillator_id": np.arange(5),
            "send_count": np.ones(5, dtype=int),
        }))
        result = compute_mean_abs_gap_error_per_cycle(
            send_df=send_df,
            cycle_starts=np.array([0.0, 5000.0]),
            num_devices=5,
            nominal_cycle_time_ms=5000.0,
        )
        self.assertAlmostEqual(
            result.loc[0, "new_mean_abs_dev"], 2.0 * math.pi * 40.0 / 5000.0, places=12
        )
        self.assertAlmostEqual(
            result.loc[0, "new_max_abs_dev"], 2.0 * math.pi * 100.0 / 5000.0, places=12
        )
        self.assertAlmostEqual(
            result.loc[0, "min_gap_rad"], 2.0 * math.pi * 900.0 / 5000.0, places=12
        )


class CensoredMedianTest(unittest.TestCase):
    def test_sixty_percent_convergence_is_finite(self) -> None:
        cycles = pd.Series([10.0, 20.0, 30.0, math.nan, math.nan])
        converged = pd.Series([True, True, True, False, False])
        self.assertEqual(censored_convergence_median(cycles, converged), 30.0)

    def test_fifty_percent_or_less_is_null(self) -> None:
        cycles = pd.Series([10.0, 20.0, math.nan, math.nan])
        converged = pd.Series([True, True, False, False])
        self.assertTrue(math.isnan(censored_convergence_median(cycles, converged)))


class InitialPhaseMasterV2Test(unittest.TestCase):
    @staticmethod
    def params(device_count: int, function_name: str, k_values: list[int]) -> dict:
        return {
            "coupling_function": function_name,
            "k_values": k_values,
            "runs_per_k": 50,
            "simulation_base": {
                "seed": 20_260_715,
                "device_count": device_count,
                "cycle_time": 5000,
                "initial_phase_master_path": str(MASTER_PATH),
            },
        }

    def test_all_prefixes_have_no_duplicates(self) -> None:
        data = json.loads(MASTER_PATH.read_text(encoding="utf-8"))
        self.assertEqual(data["sampling"], "uniform_without_replacement")
        self.assertEqual(len(data["start_times_by_run"]), 50)
        for n in (5, 10, 20, 50):
            for values in data["start_times_by_run"]:
                self.assertEqual(len(values[:n]), n)
                self.assertEqual(len(set(values[:n])), n)

    def test_run_index_reuse_across_function_k_and_n(self) -> None:
        starts_5 = _initial_start_times_by_run(self.params(5, "KURAMOTO", [1]))
        starts_20 = _initial_start_times_by_run(self.params(20, "LINEAR", [70]))
        starts_50 = _initial_start_times_by_run(self.params(50, "KURAMOTO", [2000]))
        for run_index in (0, 17, 49):
            self.assertEqual(starts_5[run_index], starts_20[run_index][:5])
            self.assertEqual(starts_20[run_index], starts_50[run_index][:20])


if __name__ == "__main__":
    unittest.main()
