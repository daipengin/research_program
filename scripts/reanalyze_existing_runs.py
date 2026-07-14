from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))


RESULTS_ROOT = PROJECT_ROOT / "results" / "reanalysis"
LOCAL_RUN_ROOT = PROJECT_ROOT / "results_local" / "reanalysis_runs"
ERROR_LOG_PATH = LOCAL_RUN_ROOT / "errors.csv"
GRAPH_ROOT = PROJECT_ROOT / "outputs" / "graph_runs" / "interval_per_vs_k"

FUNCTION_SLUGS = {
    "KURAMOTO": "kuramoto",
    "LINEAR": "linear",
    "LINEAR_4": "linear_4",
    "NewSIN": "newsin",
}


@dataclass(frozen=True)
class KTask:
    graph_dir: Path
    coupling_function: str
    k_value: float
    device_count: int
    expected_cycle_count: int
    epsilon: float
    stable_window: int
    usable_per_threshold: float
    moving_window_cycles: int
    force: bool


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch reanalyze existing graph-first runs.")
    parser.add_argument("--epsilon", type=float, default=0.005)
    parser.add_argument("--stable-window", type=int, default=20)
    parser.add_argument("--usable-per-threshold", type=float, default=1.0)
    parser.add_argument("--moving-window-cycles", type=int, default=10)
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--functions", nargs="*", choices=sorted(FUNCTION_SLUGS), default=None)
    parser.add_argument("--k-values", nargs="*", type=float, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--phase-error-only",
        action="store_true",
        help="Only append final-window residual phase-spacing errors to local parquet and aggregate them.",
    )
    args = parser.parse_args()

    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    LOCAL_RUN_ROOT.mkdir(parents=True, exist_ok=True)

    graph_dirs = discover_graph_dirs()
    if args.functions:
        graph_dirs = {
            name: path
            for name, path in graph_dirs.items()
            if name in set(args.functions)
        }
    tasks = build_tasks(
        graph_dirs=graph_dirs,
        epsilon=args.epsilon,
        stable_window=args.stable_window,
        usable_per_threshold=args.usable_per_threshold,
        moving_window_cycles=args.moving_window_cycles,
        force=args.force,
        phase_error_only=args.phase_error_only,
    )
    if args.k_values:
        wanted = {float(value) for value in args.k_values}
        tasks = [task for task in tasks if float(task.k_value) in wanted]
    print(f"tasks: {len(tasks)}")

    errors: list[dict[str, Any]] = []
    if tasks:
        max_workers = max(1, int(args.max_workers))
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            worker = process_phase_error_task if args.phase_error_only else process_k_task
            future_by_task = {executor.submit(worker, task): task for task in tasks}
            completed = 0
            for future in as_completed(future_by_task):
                task = future_by_task[future]
                completed += 1
                try:
                    output_path = future.result()
                    print(
                        f"[{completed}/{len(tasks)}] {task.coupling_function} K={task.k_value:g}: {output_path}",
                        flush=True,
                    )
                except Exception as exc:
                    detail = {
                        "coupling_function": task.coupling_function,
                        "k": f"{task.k_value:g}",
                        "graph_dir": str(task.graph_dir),
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    }
                    errors.append(detail)
                    print(
                        f"[{completed}/{len(tasks)}] FAILED {task.coupling_function} K={task.k_value:g}: {exc}",
                        flush=True,
                    )

    if errors:
        pd.DataFrame(errors).to_csv(ERROR_LOG_PATH, index=False)

    aggregate_targets = args.functions or sorted(graph_dirs)
    for coupling_function in aggregate_targets:
        if not args.phase_error_only:
            aggregate_function(coupling_function)
        aggregate_phase_error_function(coupling_function)

    return 0


def discover_graph_dirs() -> dict[str, Path]:
    graph_dirs: dict[str, Path] = {}
    for graph_dir in sorted(GRAPH_ROOT.glob("*")):
        manifest_path = graph_dir / "manifest.json"
        graph_db_path = graph_dir / "graph_data.sqlite"
        raw_db_path = graph_dir / "raw_run.sqlite"
        if not manifest_path.exists() or not graph_db_path.exists() or not raw_db_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        graph_key = manifest.get("graph_key") or {}
        coupling_function = str(graph_key.get("coupling_function") or manifest.get("input", {}).get("coupling_function") or "")
        if coupling_function in FUNCTION_SLUGS:
            graph_dirs[coupling_function] = graph_dir
    return graph_dirs


def build_tasks(
    *,
    graph_dirs: dict[str, Path],
    epsilon: float,
    stable_window: int,
    usable_per_threshold: float,
    moving_window_cycles: int,
    force: bool,
    phase_error_only: bool = False,
) -> list[KTask]:
    tasks: list[KTask] = []
    for coupling_function, graph_dir in graph_dirs.items():
        device_count, expected_cycle_count = graph_expected_shape(graph_dir)
        for k_value in k_values_for_graph(graph_dir):
            task = KTask(
                graph_dir=graph_dir,
                coupling_function=coupling_function,
                k_value=k_value,
                device_count=device_count,
                expected_cycle_count=expected_cycle_count,
                epsilon=epsilon,
                stable_window=stable_window,
                usable_per_threshold=usable_per_threshold,
                moving_window_cycles=moving_window_cycles,
                force=force,
            )
            if phase_error_only:
                if not phase_error_checkpoint_needs_update(task):
                    continue
            elif not force and checkpoint_path(task).exists():
                continue
            tasks.append(task)
    return sorted(tasks, key=lambda task: (task.coupling_function, task.k_value))


def phase_error_checkpoint_needs_update(task: KTask) -> bool:
    output_path = checkpoint_path(task)
    if task.force or not output_path.exists():
        return True
    try:
        pd.read_parquet(output_path, columns=["residual_phase_spacing_error", "residual_3min"])
    except Exception:
        return True
    return False


def graph_expected_shape(graph_dir: Path) -> tuple[int, int]:
    manifest = json.loads((graph_dir / "manifest.json").read_text(encoding="utf-8"))
    base = dict(manifest.get("simulation_base") or manifest.get("input", {}).get("simulation_base") or {})
    device_count = int(base.get("device_count", 50))
    duration = float(base.get("duration_ms", base.get("duration", 1_800_000)))
    cycle_time = float(base.get("cycle_time", 10_000))
    expected_cycle_count = int(round(duration / cycle_time))
    return device_count, expected_cycle_count


def k_values_for_graph(graph_dir: Path) -> list[float]:
    with sqlite3.connect(graph_dir / "graph_data.sqlite") as conn:
        rows = conn.execute(
            "SELECT DISTINCT coupling_strength FROM runs ORDER BY coupling_strength"
        ).fetchall()
    return [float(row[0]) for row in rows]


def process_k_task(task: KTask) -> str:
    output_path = checkpoint_path(task)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    graph_db_path = task.graph_dir / "graph_data.sqlite"
    raw_db_path = task.graph_dir / "raw_run.sqlite"
    run_df = read_runs_for_k(graph_db_path, task.k_value)
    if run_df.empty:
        empty_run_metrics(task).to_parquet(output_path, index=False)
        return str(output_path)

    run_ids = run_df["run_id"].astype(str).tolist()
    cycle_df = read_cycle_counts_for_runs(graph_db_path, run_ids)
    phase_df = read_phase_errors_for_runs(raw_db_path, run_ids)
    cycle_by_run = {
        str(run_id): group.sort_values("cycle_index")
        for run_id, group in cycle_df.groupby("run_id", sort=False)
    }
    phase_by_run = {
        str(run_id): group.sort_values("cycle_index")
        for run_id, group in phase_df.groupby("run_id", sort=False)
    }

    rows = []
    for run in run_df.itertuples(index=False):
        run_id = str(run.run_id)
        try:
            rows.append(
                compute_run_metrics(
                    task,
                    run,
                    cycle_by_run.get(run_id, pd.DataFrame()),
                    phase_by_run.get(run_id, pd.DataFrame()),
                )
            )
        except Exception as exc:
            rows.append(
                {
                    "run_id": run_id,
                    "coupling_function": task.coupling_function,
                    "k": task.k_value,
                    "repeat_index": int(run.repeat_index),
                    "error": str(exc),
                    "valid": False,
                }
            )
    pd.DataFrame(rows).to_parquet(output_path, index=False)
    return str(output_path)


def process_phase_error_task(task: KTask) -> str:
    output_path = checkpoint_path(task)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    graph_db_path = task.graph_dir / "graph_data.sqlite"
    raw_db_path = task.graph_dir / "raw_run.sqlite"
    run_df = read_runs_for_k(graph_db_path, task.k_value)
    if run_df.empty:
        empty_run_metrics(task).to_parquet(output_path, index=False)
        return str(output_path)

    run_ids = run_df["run_id"].astype(str).tolist()
    phase_df = read_phase_errors_for_runs(raw_db_path, run_ids)
    phase_by_run = {
        str(run_id): group.sort_values("cycle_index")
        for run_id, group in phase_df.groupby("run_id", sort=False)
    }

    rows = []
    for run in run_df.itertuples(index=False):
        run_id = str(run.run_id)
        try:
            rows.append(
                {
                    "run_id": run_id,
                    "residual_phase_spacing_error": final_window_phase_error(
                        phase_by_run.get(run_id, pd.DataFrame()),
                        task.expected_cycle_count,
                    ),
                    "residual_3min": phase_error_for_cycle_window(
                        phase_by_run.get(run_id, pd.DataFrame()),
                        start_cycle=9,
                        end_cycle=18,
                    ),
                    "valid_phase_error": True,
                    "error_phase_error": "",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "run_id": run_id,
                    "residual_phase_spacing_error": np.nan,
                    "residual_3min": np.nan,
                    "valid_phase_error": False,
                    "error_phase_error": str(exc),
                }
            )

    residual_df = pd.DataFrame(rows)
    if output_path.exists():
        existing = pd.read_parquet(output_path)
        output = existing.drop(
            columns=[
                "residual_phase_spacing_error",
                "residual_3min",
                "valid_phase_error",
                "error_phase_error",
            ],
            errors="ignore",
        ).merge(residual_df, how="left", on="run_id")
    else:
        output = run_df.copy()
        output["coupling_function"] = task.coupling_function
        output["k"] = float(task.k_value)
        output = output.merge(residual_df, how="left", on="run_id")
    output.to_parquet(output_path, index=False)
    return str(output_path)


def read_runs_for_k(graph_db_path: Path, k_value: float) -> pd.DataFrame:
    with sqlite3.connect(graph_db_path) as conn:
        return pd.read_sql_query(
            """
            SELECT run_id, coupling_strength, repeat_index
            FROM runs
            WHERE status = 'completed'
              AND coupling_strength = ?
            ORDER BY repeat_index, run_id
            """,
            conn,
            params=(k_value,),
        )


def read_cycle_counts_for_runs(graph_db_path: Path, run_ids: list[str]) -> pd.DataFrame:
    return read_table_for_run_ids(
        db_path=graph_db_path,
        table_name="run_cycle_counts",
        columns=[
            "run_id",
            "cycle_index",
            "expected_packets",
            "actual_packets",
            "cumulative_expected_packets",
            "cumulative_actual_packets",
        ],
        run_ids=run_ids,
        order_by="run_id, cycle_index",
    )


def read_phase_errors_for_runs(raw_db_path: Path, run_ids: list[str]) -> pd.DataFrame:
    return read_table_for_run_ids(
        db_path=raw_db_path,
        table_name="phase_gap_error",
        columns=[
            "run_id",
            "cycle_index",
            "mean_abs_diff_from_ideal_phase_gap",
        ],
        run_ids=run_ids,
        order_by="run_id, cycle_index",
    )


def read_table_for_run_ids(
    *,
    db_path: Path,
    table_name: str,
    columns: list[str],
    run_ids: list[str],
    order_by: str,
) -> pd.DataFrame:
    if not run_ids:
        return pd.DataFrame(columns=columns)
    placeholders = ",".join("?" for _ in run_ids)
    sql = (
        f"SELECT {', '.join(columns)} FROM {table_name} "
        f"WHERE run_id IN ({placeholders}) ORDER BY {order_by}"
    )
    with sqlite3.connect(db_path) as conn:
        return pd.read_sql_query(sql, conn, params=run_ids)


def compute_run_metrics(
    task: KTask,
    run: Any,
    run_cycles: pd.DataFrame,
    run_phase: pd.DataFrame,
) -> dict[str, Any]:
    run_id = str(run.run_id)
    if run_cycles.empty:
        raise ValueError("missing run_cycle_counts")
    if run_phase.empty:
        raise ValueError("missing phase_gap_error")

    cycle_indices = np.arange(1, task.expected_cycle_count + 1, dtype=np.int64)
    actual_by_cycle = np.zeros(task.expected_cycle_count, dtype=np.float64)
    count_cycles = run_cycles["cycle_index"].to_numpy(dtype=np.int64)
    count_actual = run_cycles["actual_packets"].to_numpy(dtype=np.float64)
    valid_cycle_mask = (count_cycles >= 1) & (count_cycles <= task.expected_cycle_count)
    actual_by_cycle[count_cycles[valid_cycle_mask] - 1] = count_actual[valid_cycle_mask]
    expected_by_cycle = np.full(task.expected_cycle_count, float(task.device_count), dtype=np.float64)
    expected_total = float(np.nansum(expected_by_cycle))
    actual_total = float(np.nansum(actual_by_cycle))
    num_sends = int(actual_total)
    num_skipped = int(round(expected_total - actual_total))

    errors = run_phase["mean_abs_diff_from_ideal_phase_gap"].to_numpy(dtype=np.float64)
    phase_cycles = run_phase["cycle_index"].to_numpy(dtype=np.int64)
    residual_phase_spacing_error = final_window_phase_error(run_phase, task.expected_cycle_count)
    residual_3min = phase_error_for_cycle_window(run_phase, start_cycle=9, end_cycle=18)
    converged_cycle = first_stable_cycle(
        phase_cycles=phase_cycles,
        errors=errors,
        epsilon=task.epsilon,
        stable_window=task.stable_window,
    )
    converged_cycle_eps020 = first_stable_cycle(
        phase_cycles=phase_cycles,
        errors=errors,
        epsilon=0.02,
        stable_window=task.stable_window,
    )
    if converged_cycle is None:
        fluctuation = np.nan
    else:
        post_errors = errors[phase_cycles >= converged_cycle]
        fluctuation = float(np.nanstd(post_errors, ddof=1)) if np.sum(np.isfinite(post_errors)) > 1 else 0.0
    if converged_cycle_eps020 is None:
        fluctuation_eps020 = np.nan
    else:
        post_errors_eps020 = errors[phase_cycles >= converged_cycle_eps020]
        fluctuation_eps020 = (
            float(np.nanstd(post_errors_eps020, ddof=1))
            if np.sum(np.isfinite(post_errors_eps020)) > 1
            else 0.0
        )

    time_to_usable_legacy = moving_window_usable_cycle(
        cycle_indices=cycle_indices,
        expected_by_cycle=expected_by_cycle,
        actual_by_cycle=actual_by_cycle,
        window=task.moving_window_cycles,
        threshold_percent=task.usable_per_threshold,
        return_cycle="start",
    )
    time_to_usable = moving_window_usable_cycle(
        cycle_indices=cycle_indices,
        expected_by_cycle=expected_by_cycle,
        actual_by_cycle=actual_by_cycle,
        window=task.moving_window_cycles,
        threshold_percent=task.usable_per_threshold,
        return_cycle="end",
    )
    if np.isfinite(time_to_usable):
        transient_per = per_for_mask(cycle_indices < int(time_to_usable), expected_by_cycle, actual_by_cycle)
        steady_per = per_for_mask(cycle_indices >= int(time_to_usable), expected_by_cycle, actual_by_cycle)
    else:
        transient_per = np.nan
        steady_per = np.nan

    return {
        "run_id": run_id,
        "coupling_function": task.coupling_function,
        "k": float(task.k_value),
        "repeat_index": int(run.repeat_index),
        "converged_cycle": np.nan if converged_cycle is None else int(converged_cycle),
        "converged_cycle_eps020": np.nan if converged_cycle_eps020 is None else int(converged_cycle_eps020),
        "post_convergence_fluctuation": fluctuation,
        "post_convergence_fluctuation_eps020": fluctuation_eps020,
        "residual_phase_spacing_error": residual_phase_spacing_error,
        "residual_3min": residual_3min,
        "overall_per": per_from_counts(expected_total, actual_total),
        "transient_per": transient_per,
        "steady_per": steady_per,
        "time_to_usable_legacy": time_to_usable_legacy,
        "time_to_usable": time_to_usable,
        "num_sends": num_sends,
        "num_skipped": num_skipped,
        "valid": True,
        "error": "",
    }


def first_stable_cycle(
    *,
    phase_cycles: np.ndarray,
    errors: np.ndarray,
    epsilon: float,
    stable_window: int,
) -> int | None:
    finite_ok = np.isfinite(errors) & (errors < epsilon)
    streak = 0
    for index, ok in enumerate(finite_ok):
        streak = streak + 1 if bool(ok) else 0
        if streak >= stable_window:
            start_index = index - stable_window + 1
            return int(phase_cycles[start_index])
    return None


def final_window_phase_error(run_phase: pd.DataFrame, expected_cycle_count: int) -> float:
    final_start = max(1, int(expected_cycle_count) - 9)
    return phase_error_for_cycle_window(
        run_phase,
        start_cycle=final_start,
        end_cycle=int(expected_cycle_count),
    )


def phase_error_for_cycle_window(run_phase: pd.DataFrame, *, start_cycle: int, end_cycle: int) -> float:
    if run_phase.empty:
        raise ValueError("missing phase_gap_error")
    cycles = pd.to_numeric(run_phase["cycle_index"], errors="coerce")
    values = pd.to_numeric(run_phase["mean_abs_diff_from_ideal_phase_gap"], errors="coerce")
    mask = (cycles >= int(start_cycle)) & (cycles <= int(end_cycle))
    window_values = values[mask].dropna()
    if window_values.empty:
        return np.nan
    return float(window_values.mean())


def moving_window_usable_cycle(
    *,
    cycle_indices: np.ndarray,
    expected_by_cycle: np.ndarray,
    actual_by_cycle: np.ndarray,
    window: int,
    threshold_percent: float,
    return_cycle: str,
) -> float:
    if window < 1 or len(cycle_indices) < window:
        return np.nan
    expected_cum = np.concatenate([[0.0], np.cumsum(expected_by_cycle)])
    actual_cum = np.concatenate([[0.0], np.cumsum(actual_by_cycle)])
    for start in range(0, len(cycle_indices) - window + 1):
        end = start + window
        expected = expected_cum[end] - expected_cum[start]
        actual = actual_cum[end] - actual_cum[start]
        if per_from_counts(expected, actual) < threshold_percent:
            if return_cycle == "start":
                return float(cycle_indices[start])
            if return_cycle == "end":
                return float(cycle_indices[end - 1])
            raise ValueError(f"unsupported return_cycle: {return_cycle}")
    return np.nan


def per_for_mask(mask: np.ndarray, expected_by_cycle: np.ndarray, actual_by_cycle: np.ndarray) -> float:
    if not bool(np.any(mask)):
        return np.nan
    return per_from_counts(float(np.nansum(expected_by_cycle[mask])), float(np.nansum(actual_by_cycle[mask])))


def per_from_counts(expected: float, actual: float) -> float:
    if expected <= 0:
        return np.nan
    return max(0.0, (1.0 - (actual / expected)) * 100.0)


def aggregate_function(coupling_function: str) -> None:
    slug = FUNCTION_SLUGS[coupling_function]
    files = sorted((LOCAL_RUN_ROOT / slug).glob("k_*.parquet"))
    if not files:
        return
    df = pd.concat([pd.read_parquet(path) for path in files], ignore_index=True)
    valid = df[df["valid"].astype(bool)].copy()
    grouped_rows = []
    for k_value, group in df.groupby("k", dropna=False):
        valid_group = group[group["valid"].astype(bool)]
        grouped_rows.append(
            {
                "coupling_function": coupling_function,
                "k": float(k_value),
                "valid_run_count": int(len(valid_group)),
                "failed_run_count": int(len(group) - len(valid_group)),
                "convergence_rate_percent": percent_non_null(valid_group["converged_cycle"]),
                "convergence_rate_eps020_percent": percent_non_null(valid_group["converged_cycle_eps020"]),
                "usable_rate_percent": percent_non_null(valid_group["time_to_usable"]),
                "usable_rate_legacy_percent": percent_non_null(valid_group["time_to_usable_legacy"]),
                **metric_quantiles(valid_group, "converged_cycle"),
                **metric_quantiles(valid_group, "converged_cycle_eps020"),
                **metric_quantiles(valid_group, "time_to_usable"),
                **metric_quantiles(valid_group, "time_to_usable_legacy"),
                **metric_quantiles(valid_group, "post_convergence_fluctuation"),
                **metric_quantiles(valid_group, "post_convergence_fluctuation_eps020"),
                "overall_per_mean": mean_or_nan(valid_group["overall_per"]),
                "overall_per_std": std_or_nan(valid_group["overall_per"]),
                "transient_per_mean": mean_or_nan(valid_group["transient_per"]),
                "transient_per_std": std_or_nan(valid_group["transient_per"]),
                "steady_per_mean": mean_or_nan(valid_group["steady_per"]),
                "steady_per_std": std_or_nan(valid_group["steady_per"]),
            }
        )
    output = pd.DataFrame(grouped_rows).sort_values("k").reset_index(drop=True)
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    output.to_csv(RESULTS_ROOT / f"{slug}_metrics.csv", index=False)


def aggregate_phase_error_function(coupling_function: str) -> None:
    slug = FUNCTION_SLUGS[coupling_function]
    files = sorted((LOCAL_RUN_ROOT / slug).glob("k_*.parquet"))
    if not files:
        return
    df = pd.concat([pd.read_parquet(path) for path in files], ignore_index=True)
    if "residual_phase_spacing_error" not in df.columns:
        return

    grouped_rows = []
    for k_value, group in df.groupby("k", dropna=False):
        valid_group = group
        if "valid" in valid_group.columns:
            valid_group = valid_group[valid_group["valid"].astype(bool)]
        if "valid_phase_error" in valid_group.columns:
            valid_group = valid_group[valid_group["valid_phase_error"].fillna(True).astype(bool)]
        values = pd.to_numeric(valid_group["residual_phase_spacing_error"], errors="coerce").dropna()
        if "residual_3min" in valid_group.columns:
            values_3min = pd.to_numeric(valid_group["residual_3min"], errors="coerce").dropna()
        else:
            values_3min = pd.Series(dtype=float)
        grouped_rows.append(
            {
                "k": float(k_value),
                "residual_median": float(values.median()) if not values.empty else np.nan,
                "residual_q1": float(values.quantile(0.25)) if not values.empty else np.nan,
                "residual_q3": float(values.quantile(0.75)) if not values.empty else np.nan,
                "residual_3min_median": float(values_3min.median()) if not values_3min.empty else np.nan,
                "residual_3min_q1": float(values_3min.quantile(0.25)) if not values_3min.empty else np.nan,
                "residual_3min_q3": float(values_3min.quantile(0.75)) if not values_3min.empty else np.nan,
                "n_runs": int(len(values)),
            }
        )
    output = pd.DataFrame(grouped_rows).sort_values("k").reset_index(drop=True)
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    output.to_csv(RESULTS_ROOT / f"{slug}_phase_error.csv", index=False)


def metric_quantiles(df: pd.DataFrame, column: str) -> dict[str, float]:
    values = pd.to_numeric(df[column], errors="coerce").dropna()
    if values.empty:
        return {
            f"{column}_median": np.nan,
            f"{column}_q1": np.nan,
            f"{column}_q3": np.nan,
        }
    return {
        f"{column}_median": float(values.median()),
        f"{column}_q1": float(values.quantile(0.25)),
        f"{column}_q3": float(values.quantile(0.75)),
    }


def percent_non_null(series: pd.Series) -> float:
    if len(series) == 0:
        return np.nan
    return float(series.notna().sum() * 100.0 / len(series))


def mean_or_nan(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return float(values.mean()) if not values.empty else np.nan


def std_or_nan(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return float(values.std()) if len(values) > 1 else np.nan


def checkpoint_path(task: KTask) -> Path:
    slug = FUNCTION_SLUGS[task.coupling_function]
    return LOCAL_RUN_ROOT / slug / f"k_{safe_number(task.k_value)}.parquet"


def empty_run_metrics(task: KTask) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "coupling_function": task.coupling_function,
                "k": task.k_value,
                "valid": False,
                "error": "no completed runs for K",
            }
        ]
    )


def safe_number(value: float) -> str:
    text = f"{value:g}".replace("-", "m").replace(".", "p")
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text)


if __name__ == "__main__":
    raise SystemExit(main())
