from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from research_program.analysis.n_sweep_metrics import (  # noqa: E402
    compute_cycle_delivery_counts,
    first_consecutive_below,
    first_ttu_cycle,
    overall_per_percent,
    tolerance_rad,
)
from research_program.analysis.calculate_phase_gap_error import (  # noqa: E402
    compute_mean_abs_gap_error_per_cycle,
)
from research_program.graph_workflow.execution import (  # noqa: E402
    _initial_start_times_by_run,
    _simulation_request_for_k,
)
from research_program.io.send_log import add_detection_time_column  # noqa: E402
from research_program.simulation.lora_airtime import (  # noqa: E402
    LoRaAirtimeConfig,
    calculate_lora_airtime_ms,
)
from research_program.simulation.runner import run_simulation_request  # noqa: E402


FUNCTIONS = ("KURAMOTO", "LINEAR")
DEVICE_COUNTS = (5, 10, 20, 50)
K_VALUES = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000)
RUNS_PER_CONDITION = 50
NOMINAL_CYCLE_TIME_MS = 5_000
DURATION_MS = 900_000
CARRIER_SENSE_DURATION_MS = 5.0
MASTER_SEED = 12_345
MASTER_PATH = PROJECT_ROOT / "experiments" / "n_sweep_v1" / "initial_phase_master.json"
RAW_ROOT = PROJECT_ROOT / "outputs" / "n_sweep_v1" / "raw"
RESULTS_ROOT = PROJECT_ROOT / "results" / "n_sweep_v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run and aggregate the N-sweep v1 experiment.")
    parser.add_argument("--smoke", action="store_true", help="Run N=5, K=10, KURAMOTO, 3 runs.")
    parser.add_argument("--aggregate-only", action="store_true")
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()

    if args.smoke:
        functions = ("KURAMOTO",)
        device_counts = (5,)
        k_values = (10,)
        runs_per_condition = 3
        duration_ms = DURATION_MS
        raw_root = PROJECT_ROOT / "outputs" / "n_sweep_v1_smoke_180cycles" / "raw"
        results_root = PROJECT_ROOT / "results" / "n_sweep_v1_smoke_180cycles"
    else:
        functions = FUNCTIONS
        device_counts = DEVICE_COUNTS
        k_values = K_VALUES
        runs_per_condition = RUNS_PER_CONDITION
        duration_ms = DURATION_MS
        raw_root = RAW_ROOT
        results_root = RESULTS_ROOT

    results_root.mkdir(parents=True, exist_ok=True)
    raw_root.mkdir(parents=True, exist_ok=True)
    log_path = results_root / "execution.log"
    condition_count = len(functions) * len(device_counts) * len(k_values)
    run_count = condition_count * runs_per_condition
    started = time.perf_counter()
    log(
        log_path,
        f"start conditions={condition_count} runs={run_count} "
        f"functions={list(functions)} N={list(device_counts)} K={list(k_values)}",
    )

    for function_name in functions:
        for device_count in device_counts:
            starts_by_run = initial_starts(device_count, runs_per_condition)
            for k_value in k_values:
                db_path = condition_db_path(raw_root, function_name, device_count, k_value)
                completed = completed_run_count(db_path)
                if completed == runs_per_condition:
                    log(log_path, f"resume-skip function={function_name} N={device_count} K={k_value}")
                    continue
                if completed != 0:
                    raise RuntimeError(
                        f"partial condition database requires manual review: {db_path} "
                        f"({completed}/{runs_per_condition} runs)"
                    )
                if args.aggregate_only:
                    raise RuntimeError(f"missing completed condition database: {db_path}")

                db_path.parent.mkdir(parents=True, exist_ok=True)
                params = experiment_params(
                    function_name=function_name,
                    device_count=device_count,
                    k_values=(k_value,),
                    runs_per_k=runs_per_condition,
                    duration_ms=duration_ms,
                    max_workers=args.max_workers,
                )
                request = _simulation_request_for_k(
                    graph_id=f"n_sweep_v1_{function_name}_{device_count}_{k_value}",
                    graph_key={"coupling_function": function_name},
                    params=params,
                    k_value=float(k_value),
                    output_root=db_path,
                    num_runs=runs_per_condition,
                    initial_start_times_by_run=starts_by_run,
                )
                condition_started = time.perf_counter()
                run_simulation_request(request)
                elapsed = time.perf_counter() - condition_started
                log(
                    log_path,
                    f"completed function={function_name} N={device_count} K={k_value} "
                    f"runs={runs_per_condition} elapsed_sec={elapsed:.3f}",
                )

    run_rows: list[dict[str, Any]] = []
    for function_name in functions:
        for device_count in device_counts:
            for k_value in k_values:
                db_path = condition_db_path(raw_root, function_name, device_count, k_value)
                run_rows.extend(
                    aggregate_condition_runs(
                        db_path=db_path,
                        function_name=function_name,
                        device_count=device_count,
                        k_value=k_value,
                    )
                )

    run_df = pd.DataFrame(run_rows).sort_values(
        ["coupling_function", "device_count", "k", "run_index"]
    )
    condition_df = aggregate_conditions(run_df)
    run_df.to_csv(results_root / "run_metrics.csv", index=False)
    condition_df.to_csv(results_root / "condition_metrics.csv", index=False)

    airtime_ms = configured_airtime_ms()
    epsilon_by_n = {
        str(device_count): tolerance_rad(
            device_count=device_count,
            nominal_cycle_time_ms=NOMINAL_CYCLE_TIME_MS,
            carrier_sense_duration_ms=CARRIER_SENSE_DURATION_MS,
            airtime_ms=airtime_ms,
        )
        for device_count in device_counts
    }
    elapsed_total = time.perf_counter() - started
    metadata = {
        "schema_version": 1,
        "experiment": "n_sweep_v1_smoke" if args.smoke else "n_sweep_v1",
        "coupling_functions": list(functions),
        "device_counts": list(device_counts),
        "k_values": list(k_values),
        "strength_ratio": -1e-4,
        "runs_per_condition": runs_per_condition,
        "condition_count": condition_count,
        "total_run_count": run_count,
        "cycle_time_ms": NOMINAL_CYCLE_TIME_MS,
        "duration_ms": duration_ms,
        "carrier_sense_duration_ms": CARRIER_SENSE_DURATION_MS,
        "airtime_ms": airtime_ms,
        "occupied_time_ms": CARRIER_SENSE_DURATION_MS + airtime_ms,
        "epsilon_tolerance_rad_by_device_count": epsilon_by_n,
        "consecutive_convergence_cycles": 10,
        "ttu_window_cycles": 10,
        "ttu_per_threshold_percent": 1.0,
        "master_seed": MASTER_SEED,
        "initial_phase_master_path": str(MASTER_PATH.relative_to(PROJECT_ROOT)),
        "initial_phase_prefix_policy": "device-ID-order first N values from each unsorted 50-device run",
        "simultaneous_collision_policy": "all send_log rows sharing an exact start time within a cycle fail demodulation",
        "elapsed_seconds": elapsed_total,
    }
    (results_root / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    log(
        log_path,
        f"finished conditions={condition_count} runs={run_count} elapsed_sec={elapsed_total:.3f}",
    )
    return 0


def experiment_params(
    *,
    function_name: str,
    device_count: int,
    k_values: tuple[int, ...],
    runs_per_k: int,
    duration_ms: int,
    max_workers: int,
) -> dict[str, Any]:
    return {
        "coupling_function": function_name,
        "k_values": list(k_values),
        "runs_per_k": runs_per_k,
        "interval_start_ms": 0.0,
        "interval_end_ms": float(duration_ms),
        "simulation_base": {
            "duration_ms": float(duration_ms),
            "seed": MASTER_SEED,
            "device_count": device_count,
            "cycle_time": NOMINAL_CYCLE_TIME_MS,
            "initial_phase_start_percent": 0.0,
            "initial_phase_end_percent": 100.0,
            "initial_phase_master_path": str(MASTER_PATH),
            "listening_rate": 25,
            "strength_ratio": -1e-4,
            "max_workers": max_workers,
            "simulation_mode": "per_measurement",
            "carrier_sense_duration_ms": CARRIER_SENSE_DURATION_MS,
            "save_carrier_sense_log": True,
            "lora_payload_bytes": 37,
            "lora_spreading_factor": 7,
            "lora_bandwidth_hz": 500_000,
            "lora_coding_rate_denominator": 5,
            "lora_preamble_symbols": 8,
            "lora_explicit_header": True,
            "lora_crc_enabled": True,
            "lora_low_data_rate_optimize": "auto",
        },
    }


def initial_starts(device_count: int, run_count: int) -> tuple[tuple[int, ...], ...]:
    params = experiment_params(
        function_name="KURAMOTO",
        device_count=device_count,
        k_values=(1,),
        runs_per_k=run_count,
        duration_ms=DURATION_MS,
        max_workers=1,
    )
    return _initial_start_times_by_run(params)


def configured_airtime_ms() -> float:
    return calculate_lora_airtime_ms(
        LoRaAirtimeConfig(
            payload_bytes=37,
            spreading_factor=7,
            bandwidth_hz=500_000,
            coding_rate_denominator=5,
            preamble_symbols=8,
            explicit_header=True,
            crc_enabled=True,
            low_data_rate_optimize=None,
        )
    )


def condition_db_path(root: Path, function_name: str, device_count: int, k_value: int) -> Path:
    return root / function_name.lower() / f"n_{device_count}" / f"k_{k_value}.sqlite"


def completed_run_count(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            row = conn.execute("SELECT COUNT(*) FROM runs").fetchone()
        except sqlite3.OperationalError:
            return 0
    return int(row[0]) if row else 0


def aggregate_condition_runs(
    *,
    db_path: Path,
    function_name: str,
    device_count: int,
    k_value: int,
) -> list[dict[str, Any]]:
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    rows: list[dict[str, Any]] = []
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        runs = conn.execute(
            "SELECT * FROM runs ORDER BY random_run_index, run_id"
        ).fetchall()
        for run in runs:
            run_id = str(run["run_id"])
            send_df = pd.read_sql_query(
                """
                SELECT time, oscillator_id, send_count,
                       transmission_end_time, transmission_time_ms
                FROM send_log WHERE run_id = ? ORDER BY time, oscillator_id
                """,
                conn,
                params=(run_id,),
            )
            send_df = add_detection_time_column(send_df)
            cycle_df = pd.read_sql_query(
                """
                SELECT cycle_index, cycle_start_time
                FROM calculated_cycle_data WHERE run_id = ? ORDER BY cycle_index
                """,
                conn,
                params=(run_id,),
            )
            legacy_phase_df = pd.read_sql_query(
                """
                SELECT cycle_index, mean_abs_diff_from_ideal_phase_gap,
                       mean_abs_diff_from_ideal_phase_gap_ratio
                FROM phase_gap_error WHERE run_id = ? ORDER BY cycle_index
                """,
                conn,
                params=(run_id,),
            )
            expected_cycle_count = expected_cycles_from_run(run)
            if cycle_df.empty:
                raise ValueError(f"calculated_cycle_data is empty for run {run_id}")
            cycle_starts = float(cycle_df.iloc[0]["cycle_start_time"]) + (
                np.arange(expected_cycle_count, dtype=np.float64) * float(run["cycle_time"])
            )
            new_phase_df = compute_mean_abs_gap_error_per_cycle(
                send_df=send_df,
                cycle_starts=cycle_starts,
                num_devices=device_count,
                nominal_cycle_time_ms=float(run["cycle_time"]),
            )
            new_metric_columns = [
                "new_mean_abs_dev",
                "new_max_abs_dev",
                "observed_device_count",
                "expected_device_count",
                "has_all_device_sends",
                "skipped_device_count",
                "simultaneous_collision_count",
            ]
            phase_df = legacy_phase_df.merge(
                new_phase_df[["cycle_index", *new_metric_columns]],
                on="cycle_index",
                how="outer",
                sort=True,
            )
            conn.execute("DELETE FROM phase_gap_error WHERE run_id = ?", (run_id,))
            stored_phase_df = phase_df.copy()
            stored_phase_df.insert(0, "run_id", run_id)
            stored_phase_df.to_sql("phase_gap_error", conn, if_exists="append", index=False)
            conn.commit()
            phase_df = phase_df[phase_df["cycle_index"] <= expected_cycle_count].copy()
            delivery = compute_cycle_delivery_counts(
                send_df=send_df,
                cycle_starts=cycle_starts,
                device_count=device_count,
            )
            carrier_sense_ms = float(run["carrier_sense_duration_ms"])
            airtime_ms = float(run["transmission_time_ms"])
            epsilon = tolerance_rad(
                device_count=device_count,
                nominal_cycle_time_ms=float(run["cycle_time"]),
                carrier_sense_duration_ms=carrier_sense_ms,
                airtime_ms=airtime_ms,
            )
            phase_cycles = phase_df["cycle_index"].to_numpy(dtype=np.int64)
            mean_convergence = first_consecutive_below(
                cycle_indices=phase_cycles,
                values=phase_df["new_mean_abs_dev"].to_numpy(dtype=np.float64),
                threshold=epsilon,
                consecutive_cycles=10,
            )
            max_convergence = first_consecutive_below(
                cycle_indices=phase_cycles,
                values=phase_df["new_max_abs_dev"].to_numpy(dtype=np.float64),
                threshold=epsilon,
                consecutive_cycles=10,
            )
            ttu = first_ttu_cycle(delivery, window_cycles=10, per_threshold_percent=1.0)
            final_phase = phase_df.tail(10)
            rows.append(
                {
                    "coupling_function": function_name,
                    "device_count": device_count,
                    "k": k_value,
                    "run_index": int(run["random_run_index"]),
                    "run_id": run_id,
                    "selected_start_times": str(run["selected_start_times"]),
                    "cycle_count": len(delivery),
                    "expected_packets": int(delivery["expected_packets"].sum()),
                    "actual_packets": int(delivery["actual_packets"].sum()),
                    "successful_packets": int(delivery["successful_packets"].sum()),
                    "simultaneous_collision_count": int(
                        delivery["simultaneous_collision_count"].sum()
                    ),
                    "overall_per_percent": overall_per_percent(delivery),
                    "ttu_cycle": ttu,
                    "ttu_reached": ttu is not None,
                    "mean_convergence_cycle": mean_convergence,
                    "mean_converged": mean_convergence is not None,
                    "max_convergence_cycle": max_convergence,
                    "max_converged": max_convergence is not None,
                    "final_10_cycle_new_mean_abs_dev": float(
                        final_phase["new_mean_abs_dev"].mean()
                    ),
                    "final_10_cycle_new_max_abs_dev": float(
                        final_phase["new_max_abs_dev"].mean()
                    ),
                    "epsilon_tolerance_rad": epsilon,
                    "carrier_sense_duration_ms": carrier_sense_ms,
                    "airtime_ms": airtime_ms,
                    "occupied_time_ms": carrier_sense_ms + airtime_ms,
                }
            )
    return rows


def expected_cycles_from_run(run: sqlite3.Row) -> int:
    ranges = str(run["ranges"] or "").split("|")
    durations: list[float] = []
    for item in ranges:
        parts = item.split(":")
        if len(parts) != 3:
            continue
        durations.append(float(parts[1]) - float(parts[0]))
    if not durations:
        raise ValueError(f"run {run['run_id']} has no usable ranges metadata")
    return int(round(min(durations) / float(run["cycle_time"])))


def aggregate_conditions(run_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for keys, group in run_df.groupby(["coupling_function", "device_count", "k"], sort=True):
        function_name, device_count, k_value = keys
        mean_cycles = pd.to_numeric(group["mean_convergence_cycle"], errors="coerce").dropna()
        max_cycles = pd.to_numeric(group["max_convergence_cycle"], errors="coerce").dropna()
        ttu_cycles = pd.to_numeric(group["ttu_cycle"], errors="coerce").dropna()
        rows.append(
            {
                "coupling_function": function_name,
                "device_count": int(device_count),
                "k": float(k_value),
                "run_count": len(group),
                "overall_per_percent_median": float(group["overall_per_percent"].median()),
                "ttu_cycle_median": float(ttu_cycles.median()) if not ttu_cycles.empty else math.nan,
                "ttu_reach_rate_percent": float(group["ttu_reached"].mean() * 100.0),
                "mean_convergence_cycle_median": (
                    float(mean_cycles.median()) if not mean_cycles.empty else math.nan
                ),
                "mean_convergence_rate_percent": float(group["mean_converged"].mean() * 100.0),
                "max_convergence_cycle_median": (
                    float(max_cycles.median()) if not max_cycles.empty else math.nan
                ),
                "max_convergence_rate_percent": float(group["max_converged"].mean() * 100.0),
                "final_10_cycle_new_mean_abs_dev_median": float(
                    group["final_10_cycle_new_mean_abs_dev"].median()
                ),
                "final_10_cycle_new_max_abs_dev_median": float(
                    group["final_10_cycle_new_max_abs_dev"].median()
                ),
                "simultaneous_collision_count_total": int(
                    group["simultaneous_collision_count"].sum()
                ),
            }
        )
    return pd.DataFrame(rows).sort_values(["coupling_function", "device_count", "k"])


def log(path: Path, message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line, flush=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
