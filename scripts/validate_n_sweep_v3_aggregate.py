from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import run_n_sweep_v3 as v3  # noqa: E402
from research_program.analysis.calculate_phase_gap_error import (  # noqa: E402
    compute_mean_abs_gap_error_per_cycle,
)
from research_program.io.send_log import add_detection_time_column  # noqa: E402


def main(raw_root: Path) -> None:
    sample_db = raw_root / "linear" / "n_50" / "k_2000.sqlite"
    with sqlite3.connect(sample_db) as conn:
        run = pd.read_sql_query(
            "SELECT * FROM runs ORDER BY random_run_index LIMIT 1", conn
        ).iloc[0]
        run_id = str(run["run_id"])
        send_df = pd.read_sql_query(
            "SELECT time, oscillator_id, send_count, transmission_end_time, "
            "transmission_time_ms FROM send_log WHERE run_id = ? "
            "ORDER BY time, oscillator_id",
            conn,
            params=(run_id,),
        )
        carrier_df = pd.read_sql_query(
            "SELECT time, oscillator_id, action FROM carrier_sense_log "
            "WHERE run_id = ? AND action = 'skip_busy' ORDER BY time, oscillator_id",
            conn,
            params=(run_id,),
        )
        cycle_df = pd.read_sql_query(
            "SELECT cycle_start_time FROM calculated_cycle_data WHERE run_id = ? "
            "ORDER BY cycle_index",
            conn,
            params=(run_id,),
        )
        stored = pd.read_sql_query(
            "SELECT cycle_index, min_gap_rad FROM phase_gap_error WHERE run_id = ? "
            "ORDER BY cycle_index",
            conn,
            params=(run_id,),
        )

    cycle_starts = float(cycle_df.iloc[0, 0]) + np.arange(
        v3.v2.expected_cycles_from_run(run), dtype=float
    ) * float(run["cycle_time"])
    recomputed = compute_mean_abs_gap_error_per_cycle(
        send_df=add_detection_time_column(send_df),
        cycle_starts=cycle_starts,
        num_devices=50,
        nominal_cycle_time_ms=float(run["cycle_time"]),
        carrier_sense_df=carrier_df,
    )
    actual_only = compute_mean_abs_gap_error_per_cycle(
        send_df=add_detection_time_column(send_df),
        cycle_starts=cycle_starts,
        num_devices=50,
        nominal_cycle_time_ms=float(run["cycle_time"]),
    )
    compared = stored.merge(
        recomputed[["cycle_index", "min_gap_rad"]],
        on="cycle_index",
        suffixes=("_stored", "_new"),
    )
    delta = float(
        np.nanmax(
            np.abs(compared["min_gap_rad_stored"] - compared["min_gap_rad_new"])
        )
    )
    if delta > 1e-12:
        differences = np.abs(
            compared["min_gap_rad_stored"] - compared["min_gap_rad_new"]
        )
        worst = compared.loc[differences.idxmax()].to_dict()
        actual_compared = stored.merge(
            actual_only[["cycle_index", "min_gap_rad"]],
            on="cycle_index",
            suffixes=("_stored", "_actual"),
        )
        actual_delta = float(
            np.nanmax(
                np.abs(
                    actual_compared["min_gap_rad_stored"]
                    - actual_compared["min_gap_rad_actual"]
                )
            )
        )
        if not delta < actual_delta:
            raise AssertionError(
                f"stored min-gap is not closer to intended attempts: "
                f"intended_delta={delta}; actual_only_delta={actual_delta}; worst={worst}"
            )
        print(
            f"stored_min_gap_intended_delta={delta:.12g} "
            f"actual_only_delta={actual_delta:.12g}"
        )

    benchmark_db = raw_root / "kuramoto" / "n_5" / "k_2.sqlite"
    started = time.perf_counter()
    rows = v3.aggregate_condition_runs(
        db_path=benchmark_db,
        function_name="KURAMOTO",
        device_count=5,
        k_value=2,
    )
    elapsed = time.perf_counter() - started
    negative = sum(float(row["overall_per_percent"]) < 0.0 for row in rows)
    if len(rows) != 1000 or negative:
        raise AssertionError(f"aggregate rows={len(rows)}, negative_per={negative}")
    print(f"stored_min_gap_max_abs_delta={delta:.3g}")
    print(f"benchmark_rows={len(rows)} elapsed_sec={elapsed:.3f} negative_per={negative}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
