from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import closing
from itertools import groupby
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

import run_n_sweep_v2 as v2  # noqa: E402
from research_program.analysis.n_sweep_metrics import (  # noqa: E402
    bounded_delivery_totals,
    compute_cycle_delivery_counts,
    convergence_safe_k_bands,
    first_consecutive_above,
    first_consecutive_below,
    first_ttu_cycle,
    overall_per_percent,
    tolerance_rad,
)
from research_program.graph_workflow.execution import (  # noqa: E402
    _initial_start_times_by_run,
    _simulation_request_for_k,
)
from research_program.io.send_log import add_detection_time_column  # noqa: E402
from research_program.simulation.runner import run_simulation_request  # noqa: E402


FUNCTIONS = ("KURAMOTO", "LINEAR")
DEVICE_COUNTS = (5, 10, 20, 50)
K_VALUES = (1, 2, 3, 5, 7, 10, 15, 20, 30, 50, 70, 100, 150, 200, 300, 500, 700, 1000, 1500, 2000)
RUNS_PER_CONDITION = 1_000
NOMINAL_CYCLE_TIME_MS = 5_000
DURATION_MS = 900_000
CARRIER_SENSE_DURATION_MS = 5.0
MASTER_SEED = 20_261_990
MASTER_PATH = PROJECT_ROOT / "experiments" / "n_sweep_v3" / "initial_phase_master.json"
RESULTS_ROOT = PROJECT_ROOT / "results" / "n_sweep_v3"
STORAGE_PLAN_PATH = PROJECT_ROOT / "experiments" / "n_sweep_v3" / "storage_plan.json"
CONSECUTIVE_CYCLES = 10
MINIMUM_FREE_BYTES = 50 * 1024**3


def main() -> int:
    parser = argparse.ArgumentParser(description="Run and aggregate the N-sweep v3 experiment.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--smoke-a", action="store_true")
    mode.add_argument("--size-smoke", action="store_true")
    parser.add_argument("--aggregate-only", action="store_true")
    parser.add_argument("--raw-root", type=Path)
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()

    if args.smoke_a:
        functions = ("KURAMOTO",)
        device_counts = (5, 50)
        k_values = (10,)
        runs_per_condition = 3
        raw_root = args.raw_root or PROJECT_ROOT / "outputs" / "n_sweep_v3_smoke_a" / "raw"
        results_root = PROJECT_ROOT / "results" / "n_sweep_v3_smoke_a"
        experiment_name = "n_sweep_v3_smoke_a"
        enforce_external_storage = False
    elif args.size_smoke:
        functions = ("KURAMOTO",)
        device_counts = DEVICE_COUNTS
        k_values = (10,)
        runs_per_condition = 3
        if args.raw_root is None:
            raise ValueError("--size-smoke requires --raw-root on the selected external drive")
        raw_root = args.raw_root / "size_smoke"
        results_root = PROJECT_ROOT / "results" / "n_sweep_v3_size_smoke"
        experiment_name = "n_sweep_v3_size_smoke"
        enforce_external_storage = True
    else:
        functions = FUNCTIONS
        device_counts = DEVICE_COUNTS
        k_values = K_VALUES
        runs_per_condition = RUNS_PER_CONDITION
        raw_root = resolve_full_raw_root(args.raw_root)
        results_root = RESULTS_ROOT
        experiment_name = "n_sweep_v3"
        enforce_external_storage = True

    raw_root = raw_root.resolve()
    validate_raw_root(raw_root, enforce_external_storage=enforce_external_storage)
    raw_root.mkdir(parents=True, exist_ok=True)
    results_root.mkdir(parents=True, exist_ok=True)
    log_path = results_root / "execution.log"
    condition_count = len(functions) * len(device_counts) * len(k_values)
    run_count = condition_count * runs_per_condition
    started = time.perf_counter()
    v2.log(
        log_path,
        f"start conditions={condition_count} runs={run_count} raw_root={raw_root} "
        f"functions={list(functions)} N={list(device_counts)} K={list(k_values)}",
    )
    validate_master_prefixes()
    v2.log(log_path, "validation master_unique_prefixes=PASS N=[5,10,20,50] runs=1000")

    for function_name in functions:
        for device_count in device_counts:
            starts_by_run = initial_starts(device_count, runs_per_condition)
            for k_value in k_values:
                free_bytes = shutil.disk_usage(raw_root).free
                if free_bytes < MINIMUM_FREE_BYTES:
                    v2.log(
                        log_path,
                        f"safe-stop free_bytes={free_bytes} threshold_bytes={MINIMUM_FREE_BYTES} "
                        f"before function={function_name} N={device_count} K={k_value}",
                    )
                    return 2
                db_path = v2.condition_db_path(raw_root, function_name, device_count, k_value)
                completed = v2.completed_run_count(db_path)
                if completed == runs_per_condition:
                    v2.log(log_path, f"resume-skip function={function_name} N={device_count} K={k_value}")
                    continue
                if completed != 0:
                    raise RuntimeError(
                        f"partial condition database requires review: {db_path} "
                        f"({completed}/{runs_per_condition} runs)"
                    )
                if args.aggregate_only:
                    raise RuntimeError(f"missing completed condition database: {db_path}")
                request = _simulation_request_for_k(
                    graph_id=f"n_sweep_v3_{function_name}_{device_count}_{k_value}",
                    graph_key={"coupling_function": function_name},
                    params=experiment_params(function_name, device_count, runs_per_condition, args.max_workers),
                    k_value=float(k_value),
                    output_root=db_path,
                    num_runs=runs_per_condition,
                    initial_start_times_by_run=starts_by_run,
                )
                condition_started = time.perf_counter()
                run_simulation_request(request)
                v2.log(
                    log_path,
                    f"completed function={function_name} N={device_count} K={k_value} "
                    f"runs={runs_per_condition} elapsed_sec={time.perf_counter() - condition_started:.3f} "
                    f"free_bytes={shutil.disk_usage(raw_root).free}",
                )

    aggregate_jobs = [
        (v2.condition_db_path(raw_root, function_name, device_count, k_value),
         function_name, device_count, k_value)
        for function_name in functions
        for device_count in device_counts
        for k_value in k_values
    ]
    run_rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {
            executor.submit(
                aggregate_condition_runs,
                db_path=db_path,
                function_name=function_name,
                device_count=device_count,
                k_value=k_value,
            ): (function_name, device_count, k_value)
            for db_path, function_name, device_count, k_value in aggregate_jobs
        }
        for completed_count, future in enumerate(as_completed(futures), start=1):
            function_name, device_count, k_value = futures[future]
            condition_rows = future.result()
            run_rows.extend(condition_rows)
            v2.log(
                log_path,
                f"aggregated function={function_name} N={device_count} K={k_value} "
                f"runs={len(condition_rows)} conditions={completed_count}/{len(aggregate_jobs)}",
            )
    run_df = pd.DataFrame(run_rows).sort_values(
        ["coupling_function", "device_count", "k", "run_index"]
    )
    condition_df = enrich_condition_metrics(v2.aggregate_conditions(run_df))
    run_df.to_csv(results_root / "run_metrics.csv", index=False, lineterminator="\n")
    condition_df.to_csv(results_root / "condition_metrics.csv", index=False, lineterminator="\n")
    negative_count = int((run_df["overall_per_percent"] < 0).sum())
    if negative_count:
        v2.log(log_path, f"ERROR negative_per_run_count={negative_count}")
        raise RuntimeError(f"negative PER detected in {negative_count} runs")
    v2.log(log_path, "validation negative_per_run_count=0")

    metadata = build_metadata(
        experiment_name=experiment_name,
        functions=functions,
        device_counts=device_counts,
        k_values=k_values,
        runs_per_condition=runs_per_condition,
        condition_count=condition_count,
        run_count=run_count,
        raw_root=raw_root,
        elapsed_seconds=time.perf_counter() - started,
    )
    (results_root / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    v2.log(
        log_path,
        f"finished conditions={condition_count} runs={run_count} "
        f"elapsed_sec={time.perf_counter() - started:.3f}",
    )
    return 0


def resolve_full_raw_root(cli_path: Path | None) -> Path:
    raw = cli_path or (Path(os.environ["N_SWEEP_V3_RAW_ROOT"]) if os.environ.get("N_SWEEP_V3_RAW_ROOT") else None)
    if raw is None:
        raise ValueError("Set N_SWEEP_V3_RAW_ROOT or pass --raw-root for the full experiment")
    return raw


def validate_raw_root(path: Path, *, enforce_external_storage: bool) -> None:
    if enforce_external_storage and path.drive.upper() == PROJECT_ROOT.drive.upper():
        raise ValueError(f"raw output on repository drive is forbidden: {path}")


def experiment_params(
    function_name: str, device_count: int, runs_per_k: int, max_workers: int
) -> dict[str, Any]:
    return {
        "coupling_function": function_name,
        "k_values": list(K_VALUES),
        "runs_per_k": runs_per_k,
        "interval_start_ms": 0.0,
        "interval_end_ms": float(DURATION_MS),
        "simulation_base": {
            "duration_ms": float(DURATION_MS),
            "seed": MASTER_SEED,
            "device_count": device_count,
            "cycle_time": NOMINAL_CYCLE_TIME_MS,
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
    return _initial_start_times_by_run(
        experiment_params("KURAMOTO", device_count, run_count, 1)
    )


def validate_master_prefixes() -> None:
    master = json.loads(MASTER_PATH.read_text(encoding="utf-8"))
    if master.get("seed") != MASTER_SEED or len(master.get("start_times_by_run", [])) != 1_000:
        raise ValueError("invalid v3 master metadata")
    for n in DEVICE_COUNTS:
        for run_index, values in enumerate(master["start_times_by_run"]):
            prefix = values[:n]
            if len(prefix) != n or len(set(prefix)) != n:
                raise ValueError(f"duplicate initial phase: N={n}, run={run_index}")


def aggregate_condition_runs(
    *, db_path: Path, function_name: str, device_count: int, k_value: int
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        run_by_id = {
            str(run["run_id"]): run
            for run in conn.execute(
                "SELECT * FROM runs ORDER BY random_run_index, run_id"
            ).fetchall()
        }
        cycle_start_by_id = {
            str(run_id): float(cycle_start)
            for run_id, cycle_start in conn.execute(
                "SELECT run_id, cycle_start_time FROM calculated_cycle_data "
                "WHERE cycle_index = 1"
            )
        }
        phase_all = pd.read_sql_query(
            "SELECT run_id, cycle_index, mean_abs_diff_from_ideal_phase_gap, "
            "mean_abs_diff_from_ideal_phase_gap_ratio, new_mean_abs_dev, "
            "new_max_abs_dev, min_gap_rad, observed_device_count, "
            "expected_device_count, has_all_device_sends, skipped_device_count, "
            "simultaneous_collision_count FROM phase_gap_error ORDER BY rowid",
            conn,
        )
        phase_by_id = {
            str(run_id): group.drop(columns="run_id").sort_values("cycle_index")
            for run_id, group in phase_all.groupby("run_id", sort=False)
        }
        del phase_all

        send_columns = [
            "time", "oscillator_id", "send_count", "transmission_end_time",
            "transmission_time_ms",
        ]
        send_cursor = conn.execute(
            "SELECT run_id, time, oscillator_id, send_count, transmission_end_time, "
            "transmission_time_ms FROM send_log ORDER BY rowid"
        )
        seen_run_ids: set[str] = set()
        for run_id, send_rows in groupby(send_cursor, key=lambda record: str(record[0])):
            if run_id in seen_run_ids:
                raise RuntimeError(f"non-contiguous send_log rows for run_id={run_id}")
            seen_run_ids.add(run_id)
            run = run_by_id[run_id]
            send_df = add_detection_time_column(
                pd.DataFrame(
                    [tuple(record)[1:] for record in send_rows],
                    columns=send_columns,
                )
            )
            expected_cycles = v2.expected_cycles_from_run(run)
            cycle_starts = cycle_start_by_id[run_id] + (
                np.arange(expected_cycles, dtype=np.float64) * float(run["cycle_time"])
            )
            phase_df = phase_by_id[run_id]
            phase_df = phase_df[phase_df["cycle_index"] <= expected_cycles]
            delivery = compute_cycle_delivery_counts(
                send_df=send_df, cycle_starts=cycle_starts, device_count=device_count
            )
            totals = bounded_delivery_totals(delivery)
            carrier_ms = float(run["carrier_sense_duration_ms"])
            airtime_ms = float(run["transmission_time_ms"])
            occupied_ms = carrier_ms + airtime_ms
            epsilon = tolerance_rad(
                device_count=device_count,
                nominal_cycle_time_ms=float(run["cycle_time"]),
                carrier_sense_duration_ms=carrier_ms,
                airtime_ms=airtime_ms,
            )
            min_gap_threshold = 2.0 * math.pi * occupied_ms / float(run["cycle_time"])
            cycles = phase_df["cycle_index"].to_numpy(dtype=np.int64)
            max_cycle = first_consecutive_below(
                cycle_indices=cycles, values=phase_df["new_max_abs_dev"].to_numpy(float),
                threshold=epsilon, consecutive_cycles=CONSECUTIVE_CYCLES,
            )
            min_cycle = first_consecutive_above(
                cycle_indices=cycles, values=phase_df["min_gap_rad"].to_numpy(float),
                threshold=min_gap_threshold, consecutive_cycles=CONSECUTIVE_CYCLES,
            )
            mean_cycle = first_consecutive_below(
                cycle_indices=cycles, values=phase_df["new_mean_abs_dev"].to_numpy(float),
                threshold=epsilon, consecutive_cycles=CONSECUTIVE_CYCLES,
            )
            ttu = first_ttu_cycle(delivery, window_cycles=10, per_threshold_percent=1.0)
            final = phase_df.tail(10)
            rows.append({
                "coupling_function": function_name, "device_count": device_count,
                "k": k_value, "run_index": int(run["random_run_index"]), "run_id": run_id,
                "selected_start_times": str(run["selected_start_times"]), "cycle_count": len(delivery),
                **totals,
                "raw_send_packets": int(delivery["actual_packets"].sum()),
                "simultaneous_collision_count": int(delivery["simultaneous_collision_count"].sum()),
                "overall_per_percent": overall_per_percent(delivery),
                "ttu_cycle": ttu, "ttu_reached": ttu is not None,
                "max_convergence_cycle": max_cycle, "max_converged": max_cycle is not None,
                "mingap_convergence_cycle": min_cycle, "mingap_converged": min_cycle is not None,
                "aux_mean_convergence_cycle": mean_cycle, "aux_mean_converged": mean_cycle is not None,
                "final_10_cycle_new_mean_abs_dev": float(final["new_mean_abs_dev"].mean()),
                "final_10_cycle_new_max_abs_dev": float(final["new_max_abs_dev"].mean()),
                "final_10_cycle_min_gap_median": float(final["min_gap_rad"].median()),
                "epsilon_tolerance_rad": epsilon,
                "minimum_collision_free_gap_rad": min_gap_threshold,
                "carrier_sense_duration_ms": carrier_ms, "airtime_ms": airtime_ms,
                "occupied_time_ms": occupied_ms,
            })
        missing_run_ids = set(run_by_id).difference(seen_run_ids)
        if missing_run_ids:
            raise RuntimeError(
                f"runs without send_log rows in {db_path}: {len(missing_run_ids)}"
            )
    return rows


def enrich_condition_metrics(condition_df: pd.DataFrame) -> pd.DataFrame:
    result = condition_df.copy()
    for prefix, rate_column in (
        ("max", "max_convergence_rate_percent"),
        ("mingap", "mingap_convergence_rate_percent"),
    ):
        bands = convergence_safe_k_bands(result, rate_column=rate_column).rename(columns={
            "safe_k_lower": f"{prefix}_safe_k_ge95_lower",
            "safe_k_upper": f"{prefix}_safe_k_ge95_upper",
            "safe_k_values": f"{prefix}_safe_k_ge95_values",
            "safe_k_count": f"{prefix}_safe_k_ge95_count",
        })
        result = result.merge(bands, on=["coupling_function", "device_count"], how="left")
    best = (
        result.sort_values(["overall_per_percent_median", "k"])
        .groupby(["coupling_function", "device_count"], as_index=False)
        .first()[["coupling_function", "device_count", "k"]]
        .rename(columns={"k": "per_optimal_k"})
    )
    return result.merge(best, on=["coupling_function", "device_count"], how="left")


def build_metadata(**values: Any) -> dict[str, Any]:
    airtime_ms = v2.configured_airtime_ms()
    epsilon = {
        str(n): tolerance_rad(
            device_count=n, nominal_cycle_time_ms=NOMINAL_CYCLE_TIME_MS,
            carrier_sense_duration_ms=CARRIER_SENSE_DURATION_MS, airtime_ms=airtime_ms,
        )
        for n in values["device_counts"]
    }
    storage_plan = (
        json.loads(STORAGE_PLAN_PATH.read_text(encoding="utf-8"))
        if STORAGE_PLAN_PATH.exists() else None
    )
    return {
        "schema_version": 3,
        "experiment": values["experiment_name"],
        "coupling_functions": list(values["functions"]),
        "device_counts": list(values["device_counts"]),
        "k_values": list(values["k_values"]),
        "strength_ratio": -1e-4,
        "runs_per_condition": values["runs_per_condition"],
        "condition_count": values["condition_count"],
        "total_run_count": values["run_count"],
        "cycle_time_ms": NOMINAL_CYCLE_TIME_MS, "duration_ms": DURATION_MS,
        "carrier_sense_duration_ms": CARRIER_SENSE_DURATION_MS,
        "airtime_ms": airtime_ms, "occupied_time_ms": CARRIER_SENSE_DURATION_MS + airtime_ms,
        "epsilon_tolerance_rad_by_device_count": epsilon,
        "initial_phase_master_path": str(MASTER_PATH.relative_to(PROJECT_ROOT)),
        "master_seed": MASTER_SEED, "initial_phase_sampling": "uniform_without_replacement",
        "raw_data_root": str(values["raw_root"]),
        "minimum_free_bytes": MINIMUM_FREE_BYTES,
        "storage_plan": storage_plan,
        "changes_from_v2": [
            "fixed-denominator delivery totals saturate excess physical sends at expected packets",
            "min_gap_rad uses actual sends union carrier-sense skip_busy intended times",
            "safe K bands use convergence rate >=95 percent",
            "1000 runs per condition",
        ],
        "elapsed_seconds": values["elapsed_seconds"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
