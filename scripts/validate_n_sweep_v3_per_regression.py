from __future__ import annotations

import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

import run_n_sweep_v3 as v3  # noqa: E402
from research_program.graph_workflow.execution import _simulation_request_for_k  # noqa: E402
from research_program.simulation.runner import run_simulation_request  # noqa: E402


INITIAL_STARTS = (2703, 2809, 2042, 2109, 1792)


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary_dir:
        raw_root = Path(temporary_dir)
        for k_value in (15, 20):
            db_path = raw_root / f"k_{k_value}.sqlite"
            request = _simulation_request_for_k(
                graph_id=f"n_sweep_v3_per_regression_k_{k_value}",
                graph_key={"coupling_function": "KURAMOTO"},
                params=v3.experiment_params("KURAMOTO", 5, 1, 1),
                k_value=float(k_value),
                output_root=db_path,
                num_runs=1,
                initial_start_times_by_run=(INITIAL_STARTS,),
            )
            run_simulation_request(request)
            row = v3.aggregate_condition_runs(
                db_path=db_path,
                function_name="KURAMOTO",
                device_count=5,
                k_value=k_value,
            )[0]
            assert row["raw_send_packets"] == 901, row
            assert row["simultaneous_collision_count"] == 0, row
            assert row["expected_packets"] == 900, row
            assert row["actual_packets"] == row["expected_packets"], row
            assert row["successful_packets"] == row["expected_packets"], row
            assert row["overall_per_percent"] == 0.0, row
            print(
                f"PASS K={k_value} raw={row['raw_send_packets']} "
                f"expected={row['expected_packets']} actual={row['actual_packets']} "
                f"PER={row['overall_per_percent']}"
            )


if __name__ == "__main__":
    main()
