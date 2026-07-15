from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = PROJECT_ROOT / "results" / "n_sweep_v2"
RAW_ROOT = PROJECT_ROOT / "outputs" / "n_sweep_v2" / "raw"
MASTER_PATH = PROJECT_ROOT / "experiments" / "n_sweep_v2" / "initial_phase_master.json"
FUNCTIONS = ("KURAMOTO", "LINEAR")
DEVICE_COUNTS = (5, 10, 20, 50)
K_VALUES = (1, 2, 3, 5, 7, 10, 15, 20, 30, 50, 70, 100, 150, 200, 300, 500, 700, 1000, 1500, 2000)


def main() -> None:
    runs = pd.read_csv(RESULTS_ROOT / "run_metrics.csv")
    conditions = pd.read_csv(RESULTS_ROOT / "condition_metrics.csv")
    metadata = json.loads((RESULTS_ROOT / "metadata.json").read_text(encoding="utf-8"))
    master = json.loads(MASTER_PATH.read_text(encoding="utf-8"))

    assert len(runs) == 8_000
    assert len(conditions) == 160
    assert set(runs["coupling_function"]) == set(FUNCTIONS)
    assert set(runs["device_count"]) == set(DEVICE_COUNTS)
    assert set(runs["k"]) == set(K_VALUES)
    assert (runs.groupby(["coupling_function", "device_count", "k"]).size() == 50).all()
    assert metadata["condition_count"] == 160
    assert metadata["total_run_count"] == 8_000

    for stem in ("max", "mingap", "aux_mean"):
        cycle = runs[f"{stem}_convergence_cycle"]
        converged = runs[f"{stem}_converged"].astype(bool)
        assert (cycle.notna() == converged).all()
        rate = conditions[f"{stem}_convergence_rate_percent"]
        median = conditions[f"{stem}_convergence_cycle_censored_median"]
        assert (((rate > 50.0) & median.notna()) | ((rate <= 50.0) & median.isna())).all()

    starts_by_n: dict[int, dict[int, tuple[int, ...]]] = {}
    for n in DEVICE_COUNTS:
        subset = runs[runs["device_count"] == n]
        assert (subset.groupby("run_index")["selected_start_times"].nunique() == 1).all()
        starts_by_n[n] = {
            int(run_index): tuple(int(value) for value in values.split(";"))
            for run_index, values in subset.groupby("run_index")["selected_start_times"].first().items()
        }
    for run_index in range(50):
        master_values = tuple(master["start_times_by_run"][run_index])
        for n in DEVICE_COUNTS:
            assert starts_by_n[n][run_index] == master_values[:n]
            assert len(set(starts_by_n[n][run_index])) == n

    first_cycle_collisions = 0
    db_run_count = 0
    for function_name in FUNCTIONS:
        for n in DEVICE_COUNTS:
            for k in K_VALUES:
                db_path = RAW_ROOT / function_name.lower() / f"n_{n}" / f"k_{k}.sqlite"
                with sqlite3.connect(db_path) as conn:
                    db_run_count += int(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
                    first_cycle_collisions += int(conn.execute(
                        "SELECT COALESCE(SUM(simultaneous_collision_count), 0) "
                        "FROM phase_gap_error WHERE cycle_index = 1"
                    ).fetchone()[0])
                    missing_min_gap = int(conn.execute(
                        "SELECT COUNT(*) FROM phase_gap_error "
                        "WHERE observed_device_count >= 2 AND min_gap_rad IS NULL"
                    ).fetchone()[0])
                    assert missing_min_gap == 0
    assert db_run_count == 8_000
    assert first_cycle_collisions == 0

    messages = [
        "validation output_integrity=PASS condition_rows=160 run_rows=8000 runs_per_condition=50",
        "validation censored_median_rules=PASS metrics=max,mingap,aux_mean",
        "validation initial_phase_reuse=PASS functions=2 K=20 N_prefixes=4 runs=50",
        "validation raw_phase_gap=PASS db_runs=8000 first_cycle_collision_rows=0 min_gap_missing=0",
    ]
    log_path = RESULTS_ROOT / "execution.log"
    with log_path.open("a", encoding="utf-8", newline="\n") as handle:
        for message in messages:
            line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
            print(line)
            handle.write(line + "\n")


if __name__ == "__main__":
    main()
