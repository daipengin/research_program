from __future__ import annotations

import math
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import numpy as np
import pandas as pd

from research_program.analysis.calculate_phase_gap_error import (
    compute_mean_abs_gap_error_per_cycle,
)
from research_program.analysis.n_sweep_metrics import (
    compute_cycle_delivery_counts,
    overall_per_percent,
    tolerance_rad,
)
from research_program.graph_workflow.execution import (
    _initial_start_times_by_run,
    _simulation_request_for_k,
)
from research_program.io.send_log import add_detection_time_column
from research_program.simulation.runner import run_simulation_request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MASTER_PATH = PROJECT_ROOT / "experiments" / "n_sweep_v1" / "initial_phase_master.json"


class NewPhaseMetricTest(unittest.TestCase):
    def test_known_five_device_gaps(self) -> None:
        starts = np.array([0.0, 900.0, 2000.0, 3000.0, 4000.0])
        send_df = pd.DataFrame(
            {
                "time": starts,
                "transmission_end_time": starts,
                "oscillator_id": np.arange(5),
                "send_count": np.ones(5, dtype=int),
            }
        )
        send_df = add_detection_time_column(send_df)
        result = compute_mean_abs_gap_error_per_cycle(
            send_df=send_df,
            cycle_starts=np.array([0.0, 5000.0]),
            num_devices=5,
            nominal_cycle_time_ms=5000.0,
        )
        expected_mean = 2.0 * math.pi * 40.0 / 5000.0
        expected_max = 2.0 * math.pi * 100.0 / 5000.0
        self.assertAlmostEqual(result.loc[0, "new_mean_abs_dev"], expected_mean, places=12)
        self.assertAlmostEqual(result.loc[0, "new_max_abs_dev"], expected_max, places=12)
        self.assertEqual(result.loc[0, "observed_device_count"], 5)
        self.assertTrue(bool(result.loc[0, "has_all_device_sends"]))

    def test_epsilon_for_fifty_devices(self) -> None:
        epsilon = tolerance_rad(
            device_count=50,
            nominal_cycle_time_ms=5000.0,
            carrier_sense_duration_ms=5.0,
            airtime_ms=20.544,
        )
        self.assertAlmostEqual(epsilon, 0.09356416904627267, places=12)
        self.assertAlmostEqual(epsilon, 0.0936, places=4)


class InitialPhaseMasterTest(unittest.TestCase):
    def params(self, device_count: int, function_name: str = "KURAMOTO") -> dict:
        return {
            "coupling_function": function_name,
            "k_values": [1, 10],
            "runs_per_k": 3,
            "simulation_base": {
                "seed": 12345,
                "device_count": device_count,
                "cycle_time": 5000,
                "initial_phase_master_path": str(MASTER_PATH),
                "duration_ms": 30000,
                "carrier_sense_duration_ms": 5.0,
                "save_carrier_sense_log": True,
                "lora_payload_bytes": 37,
                "lora_spreading_factor": 7,
                "lora_bandwidth_hz": 500000,
            },
        }

    def test_prefixes_match_across_n_function_and_k(self) -> None:
        starts_5 = _initial_start_times_by_run(self.params(5, "KURAMOTO"))
        starts_10 = _initial_start_times_by_run(self.params(10, "LINEAR"))
        starts_50 = _initial_start_times_by_run(self.params(50, "KURAMOTO"))
        for run_index in range(3):
            self.assertEqual(starts_5[run_index], starts_10[run_index][:5])
            self.assertEqual(starts_10[run_index], starts_50[run_index][:10])

        request = _simulation_request_for_k(
            graph_id="prefix_test",
            graph_key={"coupling_function": "LINEAR"},
            params=self.params(10, "LINEAR"),
            k_value=10,
            output_root=Path("unused.sqlite"),
            num_runs=3,
            initial_start_times_by_run=starts_10,
        )
        self.assertEqual(request.initial_start_times_by_run, starts_10)
        self.assertTrue(request.save_carrier_sense_log)


class SimultaneousCollisionIntegrationTest(unittest.TestCase):
    def test_equal_initial_phases_are_demodulation_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            db_path = Path(temporary_dir) / "collision.sqlite"
            params = {
                "coupling_function": "KURAMOTO",
                "runs_per_k": 1,
                "simulation_base": {
                    "seed": 1,
                    "device_count": 2,
                    "cycle_time": 5000,
                    "duration_ms": 10000,
                    "listening_rate": 25,
                    "strength_ratio": -0.0001,
                    "max_workers": 1,
                    "carrier_sense_duration_ms": 5.0,
                    "save_carrier_sense_log": True,
                    "lora_payload_bytes": 37,
                    "lora_spreading_factor": 7,
                    "lora_bandwidth_hz": 500000,
                    "lora_coding_rate_denominator": 5,
                    "lora_preamble_symbols": 8,
                    "lora_explicit_header": True,
                    "lora_crc_enabled": True,
                    "lora_low_data_rate_optimize": "auto",
                },
            }
            request = _simulation_request_for_k(
                graph_id="collision_test",
                graph_key={"coupling_function": "KURAMOTO"},
                params=params,
                k_value=10,
                output_root=db_path,
                num_runs=1,
                initial_start_times_by_run=((0, 0),),
            )
            result = run_simulation_request(request)[0]
            run_id = result["run_id"]
            with closing(sqlite3.connect(db_path)) as conn:
                send_df = pd.read_sql_query(
                    "SELECT * FROM send_log WHERE run_id = ? ORDER BY time, oscillator_id",
                    conn,
                    params=(run_id,),
                )
                cycle_df = pd.read_sql_query(
                    "SELECT * FROM calculated_cycle_data WHERE run_id = ? ORDER BY cycle_index",
                    conn,
                    params=(run_id,),
                )
                phase_df = pd.read_sql_query(
                    "SELECT * FROM phase_gap_error WHERE run_id = ? ORDER BY cycle_index",
                    conn,
                    params=(run_id,),
                )
            delivery = compute_cycle_delivery_counts(
                send_df=add_detection_time_column(send_df),
                cycle_starts=cycle_df["cycle_start_time"].to_numpy(dtype=float),
                device_count=2,
            )
            self.assertGreater(int(delivery["simultaneous_collision_count"].sum()), 0)
            self.assertEqual(int(delivery["successful_packets"].sum()), 0)
            self.assertEqual(overall_per_percent(delivery), 100.0)
            self.assertGreater(int(phase_df["simultaneous_collision_count"].sum()), 0)
            self.assertEqual(len(phase_df), 2)
            self.assertEqual(result["selected_start_times"], "0;0")


if __name__ == "__main__":
    unittest.main()
