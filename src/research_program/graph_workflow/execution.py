from __future__ import annotations

import json
import math
import random
import shutil
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from research_program.analysis.calculate_cycle_data import ensure_cycle_data_for_run
from research_program.io import sqlite_runs
from research_program.plotting.plot_per_by_coupling_strength import (
    assign_cycles_from_reference_windows,
    extract_device_count_from_tags,
    normalize_oscillator_id_column,
    normalize_time_column,
    read_calculated_cycle_data,
    read_metadata,
    read_send_log,
)
from research_program.plotting.plot_per_by_coupling_strength_interval import (
    compute_interval_per_from_cycle_counts,
)
from research_program.simulation.coupling_functions import CouplingFunction
from research_program.simulation.runner import SimulationRequest, run_simulation_request

from .storage import INTERVAL_PER_DB_NAME, load_graph_job, utc_now_iso


RAW_RUN_DB_NAME = "raw_run.sqlite"
PAPER_RESULTS_ROOT = Path("results")
PAPER_RESULTS_CSV_NAME = "final_values.csv"


class JobCancelled(RuntimeError):
    pass


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
        return False


def available_coupling_functions() -> list[str]:
    return [item.value for item in CouplingFunction]


def run_interval_per_vs_k_job(graph_dir: Path) -> dict[str, Any]:
    graph_dir = Path(graph_dir)
    manifest_path = graph_dir / "manifest.json"
    status_path = graph_dir / "status.json"
    db_path = graph_dir / "graph_data.sqlite"
    interval_db_path = _interval_per_db_path(graph_dir, db_path)
    raw_run_db_path = graph_dir / RAW_RUN_DB_NAME
    figures_dir = graph_dir / "figures"

    manifest = _read_json(manifest_path)
    status = _read_json(status_path)
    params = dict(manifest.get("input") or {})
    graph_key = dict(manifest.get("graph_key") or {})
    source_mode = str(params.get("source_mode", "new_simulation"))

    figures_dir.mkdir(parents=True, exist_ok=True)
    raw_conn = sqlite_runs.connect(raw_run_db_path)
    try:
        sqlite_runs.initialize(raw_conn)
    finally:
        raw_conn.close()

    k_values = [float(value) for value in params.get("k_values", [])]
    runs_per_k = int(params.get("runs_per_k", 1))
    if source_mode == "existing_graph" and params.get("selected_run_count") is not None:
        total_runs = int(params.get("selected_run_count", 0) or 0)
    else:
        total_runs = len(k_values) * runs_per_k
    initial_start_times_by_run = _initial_start_times_by_run(params)
    aggregate_set_id = _aggregate_set_id(
        float(params["interval_start_ms"]),
        float(params["interval_end_ms"]),
    )
    completed_pairs = _completed_run_pairs(db_path, aggregate_set_id, interval_db_path)
    expected_pairs = [
        (float(k_value), int(repeat_index))
        for k_value in k_values
        for repeat_index in range(runs_per_k)
    ]
    initial_completed = sum(1 for pair in expected_pairs if pair in completed_pairs)
    pending_pairs = [
        pair for pair in expected_pairs if pair not in completed_pairs
    ]
    if initial_completed > 0 or str(status.get("status")) in {"cancelled", "failed"}:
        _append_history(
            db_path,
            "job_resume_started",
            {
                "completed_runs": initial_completed,
                "pending_runs": len(pending_pairs),
                "total_runs": total_runs,
            },
        )

    status.update(
        {
            "status": "running_simulations",
            "started_at": status.get("started_at") or utc_now_iso(),
            "updated_at": utc_now_iso(),
            "total_runs": total_runs,
            "completed_runs": initial_completed,
            "current_run_id": "",
            "cancel_requested": False,
            "cancel_requested_at": None,
            "cancel_reason": "",
            "finished_at": None,
            "error": "",
        }
    )
    _write_json(status_path, status)

    completed = initial_completed
    try:
        for k_value, repeat_index in pending_pairs:
            request = _simulation_request_for_k(
                graph_id=str(manifest["graph_id"]),
                graph_key=graph_key,
                params=params,
                k_value=k_value,
                output_root=raw_run_db_path,
                num_runs=1,
                initial_start_times_by_run=(initial_start_times_by_run[repeat_index],),
            )
            _raise_if_cancel_requested(status_path, db_path)

            def on_progress(done: int, total: int, result: dict[str, Any]) -> None:
                nonlocal completed
                run_id = str(result["run_id"])
                if _cancel_requested(status_path):
                    _delete_raw_run(raw_run_db_path, run_id)
                    raise JobCancelled(f"Job cancelled while run was active: {run_id}")
                completed += 1
                _save_run_record(
                    db_path=db_path,
                    request_id=str(manifest["graph_id"]),
                    run_id=run_id,
                    coupling_strength=k_value,
                    repeat_index=repeat_index,
                    raw_path=f"{RAW_RUN_DB_NAME}::{run_id}",
                    metadata=result,
                )
                _save_run_intermediate_and_interval_from_raw_sqlite(
                    db_path=interval_db_path,
                    raw_db_path=raw_run_db_path,
                    run_id=run_id,
                    aggregate_set_id=aggregate_set_id,
                    interval_start_ms=float(params["interval_start_ms"]),
                    interval_end_ms=float(params["interval_end_ms"]),
                )
                status.update(
                    {
                        "status": "running_simulations",
                        "completed_runs": completed,
                        "current_run_id": run_id,
                        "updated_at": utc_now_iso(),
                    }
                )
                _write_json(status_path, status)

            run_simulation_request(request, progress_callback=on_progress)

        if not pending_pairs:
            _append_history(
                db_path,
                "job_resume_no_missing_runs",
                {"completed_runs": completed, "total_runs": total_runs},
            )

        status.update(
            {
                "status": "running_analysis",
                "current_run_id": "",
                "updated_at": utc_now_iso(),
            }
        )
        _write_json(status_path, status)

        aggregate_rows = rebuild_interval_aggregate(interval_db_path, aggregate_set_id)

        status.update({"status": "rendering_graph", "updated_at": utc_now_iso()})
        _write_json(status_path, status)

        output_path = render_interval_per_vs_k_pdf(
            graph_dir=graph_dir,
            db_path=interval_db_path,
            aggregate_set_id=aggregate_set_id,
            coupling_function=str(graph_key.get("coupling_function", "")),
            interval_start_ms=float(params["interval_start_ms"]),
            interval_end_ms=float(params["interval_end_ms"]),
            plot_settings=params.get("plot_settings", {}),
            strength_ratio=float(dict(params.get("simulation_base") or {}).get("strength_ratio", -0.0001)),
        )
        _save_output(db_path, aggregate_set_id, output_path, graph_dir)
        paper_outputs = _publish_paper_results(
            graph_dir=graph_dir,
            graph_type=str(manifest.get("graph_type", graph_dir.parent.name)),
            aggregate_set_id=aggregate_set_id,
            aggregate_db_path=interval_db_path,
            output_path=output_path,
        )

        finished_at = utc_now_iso()
        status.update(
            {
                "status": "completed",
                "updated_at": finished_at,
                "finished_at": finished_at,
                "completed_runs": completed,
                "current_run_id": "",
                "error": "",
            }
        )
        manifest.update(
            {
                "status": "completed",
                "updated_at": finished_at,
                "outputs": {
                    "representative_pdf": _relative_to_graph(graph_dir, output_path),
                    **paper_outputs,
                },
                "run_summary": {
                    "total_runs": total_runs,
                    "completed_runs": completed,
                },
            }
        )
        _write_json(status_path, status)
        _write_json(manifest_path, manifest)
        _append_history(
            db_path,
            "job_completed",
            {
                "aggregate_rows": aggregate_rows,
                "output": str(output_path),
                "paper_outputs": paper_outputs,
            },
        )
    except Exception as exc:
        failed_at = utc_now_iso()
        if isinstance(exc, JobCancelled):
            status.update(
                {
                    "status": "cancelled",
                    "updated_at": failed_at,
                    "finished_at": failed_at,
                    "current_run_id": "",
                    "error": str(exc),
                }
            )
            manifest.update({"status": "cancelled", "updated_at": failed_at})
            _write_json(status_path, status)
            _write_json(manifest_path, manifest)
            _append_history(db_path, "job_cancelled", {"error": str(exc)})
            return {"job": load_graph_job(graph_dir), "output": None, "aggregate_rows": 0}
        status.update(
            {
                "status": "failed",
                "updated_at": failed_at,
                "finished_at": failed_at,
                "error": str(exc),
            }
        )
        manifest.update({"status": "failed", "updated_at": failed_at})
        _write_json(status_path, status)
        _write_json(manifest_path, manifest)
        _append_history(db_path, "job_failed", {"error": str(exc)})
        raise

    return {
        "job": load_graph_job(graph_dir),
        "output": output_path,
        "aggregate_rows": aggregate_rows,
    }


def run_convergence_cycle_vs_k_job(graph_dir: Path) -> dict[str, Any]:
    graph_dir = Path(graph_dir)
    manifest_path = graph_dir / "manifest.json"
    status_path = graph_dir / "status.json"
    db_path = graph_dir / "graph_data.sqlite"
    raw_run_db_path = graph_dir / RAW_RUN_DB_NAME
    figures_dir = graph_dir / "figures"

    manifest = _read_json(manifest_path)
    status = _read_json(status_path)
    params = dict(manifest.get("input") or {})
    graph_key = dict(manifest.get("graph_key") or {})
    source_mode = str(params.get("source_mode", "new_simulation"))

    figures_dir.mkdir(parents=True, exist_ok=True)
    if source_mode == "new_simulation":
        raw_conn = sqlite_runs.connect(raw_run_db_path)
        try:
            sqlite_runs.initialize(raw_conn)
        finally:
            raw_conn.close()

    k_values = [float(value) for value in params.get("k_values", [])]
    runs_per_k = int(params.get("runs_per_k", 1))
    total_runs = len(k_values) * runs_per_k
    aggregate_set_id = _convergence_aggregate_set_id(
        int(params["stable_cycle_count"]),
        float(params["phase_gap_change_threshold"]),
    )

    status.update(
        {
            "status": "running_simulations" if source_mode == "new_simulation" else "running_analysis",
            "started_at": status.get("started_at") or utc_now_iso(),
            "updated_at": utc_now_iso(),
            "total_runs": total_runs,
            "completed_runs": 0,
            "current_run_id": "",
            "cancel_requested": False,
            "cancel_requested_at": None,
            "cancel_reason": "",
            "finished_at": None,
            "error": "",
        }
    )
    _write_json(status_path, status)

    completed = 0
    try:
        if source_mode == "existing_graph":
            source_graph_dir = Path(str(params["source_graph_dir"]))
            completed = _build_convergence_from_existing_graph(
                target_db_path=db_path,
                source_graph_dir=source_graph_dir,
                aggregate_set_id=aggregate_set_id,
                stable_cycle_count=int(params["stable_cycle_count"]),
                phase_gap_change_threshold=float(params["phase_gap_change_threshold"]),
                selected_k_values=[
                    float(value) for value in params.get("k_values", [])
                ],
                repeat_index_min=(
                    None
                    if params.get("repeat_index_min") is None
                    else int(params["repeat_index_min"])
                ),
                repeat_index_max=(
                    None
                    if params.get("repeat_index_max") is None
                    else int(params["repeat_index_max"])
                ),
                status_path=status_path,
                status=status,
            )
        else:
            completed = _run_convergence_simulations(
                graph_dir=graph_dir,
                db_path=db_path,
                raw_run_db_path=raw_run_db_path,
                manifest=manifest,
                graph_key=graph_key,
                params=params,
                aggregate_set_id=aggregate_set_id,
                status_path=status_path,
                status=status,
            )

        status.update(
            {
                "status": "running_analysis",
                "completed_runs": completed,
                "current_run_id": "",
                "updated_at": utc_now_iso(),
            }
        )
        _write_json(status_path, status)
        aggregate_rows = rebuild_convergence_aggregate(db_path, aggregate_set_id)

        status.update({"status": "rendering_graph", "updated_at": utc_now_iso()})
        _write_json(status_path, status)
        output_path = render_convergence_cycle_vs_k_pdf(
            graph_dir=graph_dir,
            db_path=db_path,
            aggregate_set_id=aggregate_set_id,
            coupling_function=str(params.get("coupling_function", graph_key.get("coupling_function", ""))),
            stable_cycle_count=int(params["stable_cycle_count"]),
            phase_gap_change_threshold=float(params["phase_gap_change_threshold"]),
            plot_settings=params.get("plot_settings", {}),
            strength_ratio=float(dict(params.get("simulation_base") or {}).get("strength_ratio", -0.0001)),
        )
        _save_output(db_path, aggregate_set_id, output_path, graph_dir)
        paper_outputs = _publish_paper_results(
            graph_dir=graph_dir,
            graph_type=str(manifest.get("graph_type", graph_dir.parent.name)),
            aggregate_set_id=aggregate_set_id,
            aggregate_db_path=db_path,
            output_path=output_path,
        )

        finished_at = utc_now_iso()
        status.update(
            {
                "status": "completed",
                "updated_at": finished_at,
                "finished_at": finished_at,
                "completed_runs": completed,
                "current_run_id": "",
                "error": "",
            }
        )
        manifest.update(
            {
                "status": "completed",
                "updated_at": finished_at,
                "outputs": {
                    "representative_pdf": _relative_to_graph(graph_dir, output_path),
                    **paper_outputs,
                },
                "run_summary": {"total_runs": total_runs, "completed_runs": completed},
            }
        )
        _write_json(status_path, status)
        _write_json(manifest_path, manifest)
        _append_history(
            db_path,
            "job_completed",
            {
                "aggregate_rows": aggregate_rows,
                "output": str(output_path),
                "paper_outputs": paper_outputs,
            },
        )
    except Exception as exc:
        failed_at = utc_now_iso()
        if isinstance(exc, JobCancelled):
            status.update(
                {
                    "status": "cancelled",
                    "updated_at": failed_at,
                    "finished_at": failed_at,
                    "current_run_id": "",
                    "error": str(exc),
                }
            )
            manifest.update({"status": "cancelled", "updated_at": failed_at})
            _write_json(status_path, status)
            _write_json(manifest_path, manifest)
            _append_history(db_path, "job_cancelled", {"error": str(exc)})
            return {"job": load_graph_job(graph_dir), "output": None, "aggregate_rows": 0}
        status.update(
            {
                "status": "failed",
                "updated_at": failed_at,
                "finished_at": failed_at,
                "error": str(exc),
            }
        )
        manifest.update({"status": "failed", "updated_at": failed_at})
        _write_json(status_path, status)
        _write_json(manifest_path, manifest)
        _append_history(db_path, "job_failed", {"error": str(exc)})
        raise

    return {
        "job": load_graph_job(graph_dir),
        "output": output_path,
        "aggregate_rows": aggregate_rows,
    }


def run_phase_gap_error_vs_k_job(graph_dir: Path) -> dict[str, Any]:
    graph_dir = Path(graph_dir)
    manifest_path = graph_dir / "manifest.json"
    status_path = graph_dir / "status.json"
    db_path = graph_dir / "graph_data.sqlite"
    raw_run_db_path = graph_dir / RAW_RUN_DB_NAME
    figures_dir = graph_dir / "figures"

    manifest = _read_json(manifest_path)
    status = _read_json(status_path)
    params = dict(manifest.get("input") or {})
    graph_key = dict(manifest.get("graph_key") or {})
    source_mode = str(params.get("source_mode", "new_simulation"))

    figures_dir.mkdir(parents=True, exist_ok=True)
    if source_mode == "new_simulation":
        raw_conn = sqlite_runs.connect(raw_run_db_path)
        try:
            sqlite_runs.initialize(raw_conn)
        finally:
            raw_conn.close()

    k_values = [float(value) for value in params.get("k_values", [])]
    runs_per_k = int(params.get("runs_per_k", 1))
    if source_mode == "existing_graph" and params.get("selected_run_count") is not None:
        total_runs = int(params.get("selected_run_count", 0) or 0)
    else:
        total_runs = len(k_values) * runs_per_k
    target_cycle_mode = str(params.get("target_cycle_mode", "last"))
    target_cycle_index = params.get("target_cycle_index")
    aggregate_set_id = _phase_gap_error_aggregate_set_id(
        target_cycle_mode,
        target_cycle_index,
    )

    status.update(
        {
            "status": "running_simulations" if source_mode == "new_simulation" else "running_analysis",
            "started_at": status.get("started_at") or utc_now_iso(),
            "updated_at": utc_now_iso(),
            "total_runs": total_runs,
            "completed_runs": 0,
            "current_run_id": "",
            "cancel_requested": False,
            "cancel_requested_at": None,
            "cancel_reason": "",
            "finished_at": None,
            "error": "",
        }
    )
    _write_json(status_path, status)

    completed = 0
    try:
        if source_mode == "existing_graph":
            completed = _build_phase_gap_error_from_existing_graph(
                target_db_path=db_path,
                source_graph_dir=Path(str(params["source_graph_dir"])),
                aggregate_set_id=aggregate_set_id,
                target_cycle_mode=target_cycle_mode,
                target_cycle_index=target_cycle_index,
                selected_k_values=[float(value) for value in params.get("k_values", [])],
                repeat_index_min=(
                    None
                    if params.get("repeat_index_min") is None
                    else int(params["repeat_index_min"])
                ),
                repeat_index_max=(
                    None
                    if params.get("repeat_index_max") is None
                    else int(params["repeat_index_max"])
                ),
                status_path=status_path,
                status=status,
            )
        else:
            completed = _run_phase_gap_error_simulations(
                graph_dir=graph_dir,
                db_path=db_path,
                raw_run_db_path=raw_run_db_path,
                manifest=manifest,
                graph_key=graph_key,
                params=params,
                aggregate_set_id=aggregate_set_id,
                target_cycle_mode=target_cycle_mode,
                target_cycle_index=target_cycle_index,
                status_path=status_path,
                status=status,
            )

        status.update(
            {
                "status": "running_analysis",
                "completed_runs": completed,
                "current_run_id": "",
                "updated_at": utc_now_iso(),
            }
        )
        _write_json(status_path, status)
        aggregate_rows = rebuild_phase_gap_error_aggregate(db_path, aggregate_set_id)

        status.update({"status": "rendering_graph", "updated_at": utc_now_iso()})
        _write_json(status_path, status)
        output_path = render_phase_gap_error_vs_k_pdf(
            graph_dir=graph_dir,
            db_path=db_path,
            aggregate_set_id=aggregate_set_id,
            coupling_function=str(params.get("coupling_function", graph_key.get("coupling_function", ""))),
            target_cycle_mode=target_cycle_mode,
            target_cycle_index=target_cycle_index,
            plot_settings=params.get("plot_settings", {}),
            strength_ratio=float(dict(params.get("simulation_base") or {}).get("strength_ratio", -0.0001)),
        )
        _save_output(db_path, aggregate_set_id, output_path, graph_dir)
        paper_outputs = _publish_paper_results(
            graph_dir=graph_dir,
            graph_type=str(manifest.get("graph_type", graph_dir.parent.name)),
            aggregate_set_id=aggregate_set_id,
            aggregate_db_path=db_path,
            output_path=output_path,
        )

        finished_at = utc_now_iso()
        status.update(
            {
                "status": "completed",
                "updated_at": finished_at,
                "finished_at": finished_at,
                "completed_runs": completed,
                "current_run_id": "",
                "error": "",
            }
        )
        manifest.update(
            {
                "status": "completed",
                "updated_at": finished_at,
                "outputs": {
                    "representative_pdf": _relative_to_graph(graph_dir, output_path),
                    **paper_outputs,
                },
                "run_summary": {"total_runs": total_runs, "completed_runs": completed},
            }
        )
        _write_json(status_path, status)
        _write_json(manifest_path, manifest)
        _append_history(
            db_path,
            "job_completed",
            {
                "aggregate_rows": aggregate_rows,
                "output": str(output_path),
                "paper_outputs": paper_outputs,
            },
        )
    except Exception as exc:
        failed_at = utc_now_iso()
        if isinstance(exc, JobCancelled):
            status.update(
                {
                    "status": "cancelled",
                    "updated_at": failed_at,
                    "finished_at": failed_at,
                    "current_run_id": "",
                    "error": str(exc),
                }
            )
            manifest.update({"status": "cancelled", "updated_at": failed_at})
            _write_json(status_path, status)
            _write_json(manifest_path, manifest)
            _append_history(db_path, "job_cancelled", {"error": str(exc)})
            return {"job": load_graph_job(graph_dir), "output": None, "aggregate_rows": 0}
        status.update(
            {
                "status": "failed",
                "updated_at": failed_at,
                "finished_at": failed_at,
                "error": str(exc),
            }
        )
        manifest.update({"status": "failed", "updated_at": failed_at})
        _write_json(status_path, status)
        _write_json(manifest_path, manifest)
        _append_history(db_path, "job_failed", {"error": str(exc)})
        raise

    return {
        "job": load_graph_job(graph_dir),
        "output": output_path,
        "aggregate_rows": aggregate_rows,
    }


def rebuild_interval_aggregate(db_path: Path, aggregate_set_id: str) -> int:
    with _connect(db_path) as conn:
        raw_df = pd.read_sql_query(
            """
            SELECT aggregate_set_id, coupling_function, coupling_strength,
                   interval_start_ms, interval_end_ms, expected_packets,
                   actual_packets, per_percent, interval_cycle_count
            FROM run_interval_per
            WHERE aggregate_set_id = ?
            """,
            conn,
            params=(aggregate_set_id,),
        )
        conn.execute(
            "DELETE FROM aggregate_interval_per WHERE aggregate_set_id = ?",
            (aggregate_set_id,),
        )
        if raw_df.empty:
            return 0
        agg_df = (
            raw_df.groupby(["aggregate_set_id", "coupling_function", "coupling_strength"], as_index=False)
            .agg(
                per_percent_mean=("per_percent", "mean"),
                per_percent_std=("per_percent", "std"),
                per_percent_min=("per_percent", "min"),
                per_percent_max=("per_percent", "max"),
                expected_packets_sum=("expected_packets", "sum"),
                actual_packets_sum=("actual_packets", "sum"),
                count=("per_percent", "size"),
            )
            .sort_values(["coupling_function", "coupling_strength"])
        )
        rows = [
            (
                row.aggregate_set_id,
                row.coupling_function,
                float(row.coupling_strength),
                float(row.per_percent_mean),
                _nullable_float(row.per_percent_std),
                _nullable_float(row.per_percent_min),
                _nullable_float(row.per_percent_max),
                int(row.expected_packets_sum),
                int(row.actual_packets_sum),
                int(row.count),
            )
            for row in agg_df.itertuples(index=False)
        ]
        conn.executemany(
            """
            INSERT INTO aggregate_interval_per
                (aggregate_set_id, coupling_function, coupling_strength,
                 per_percent_mean, per_percent_std, per_percent_min,
                 per_percent_max, expected_packets_sum, actual_packets_sum, count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    _append_history(db_path, "aggregate_rebuilt", {"aggregate_set_id": aggregate_set_id})
    return len(rows)


def rebuild_convergence_aggregate(db_path: Path, aggregate_set_id: str) -> int:
    with _connect(db_path) as conn:
        raw_df = pd.read_sql_query(
            """
            SELECT aggregate_set_id, coupling_function, coupling_strength,
                   convergence_cycle, converged
            FROM run_convergence_cycles
            WHERE aggregate_set_id = ?
            """,
            conn,
            params=(aggregate_set_id,),
        )
        conn.execute(
            "DELETE FROM aggregate_convergence_cycles WHERE aggregate_set_id = ?",
            (aggregate_set_id,),
        )
        if raw_df.empty:
            return 0

        rows = []
        for (set_id, coupling_function, coupling_strength), group in raw_df.groupby(
            ["aggregate_set_id", "coupling_function", "coupling_strength"],
            sort=True,
        ):
            converged = group[group["converged"].astype(bool)]
            cycles = converged["convergence_cycle"].dropna().astype(float)
            rows.append(
                (
                    set_id,
                    coupling_function,
                    float(coupling_strength),
                    _nullable_float(cycles.mean()) if not cycles.empty else None,
                    _nullable_float(cycles.std()) if len(cycles) > 1 else None,
                    _nullable_float(cycles.min()) if not cycles.empty else None,
                    _nullable_float(cycles.max()) if not cycles.empty else None,
                    float(100.0 * len(converged) / len(group)),
                    int(len(group)),
                    int(len(converged)),
                )
            )
        conn.executemany(
            """
            INSERT INTO aggregate_convergence_cycles
                (aggregate_set_id, coupling_function, coupling_strength,
                 convergence_cycle_mean, convergence_cycle_std,
                 convergence_cycle_min, convergence_cycle_max,
                 convergence_rate_percent, count, converged_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    _append_history(db_path, "convergence_aggregate_rebuilt", {"aggregate_set_id": aggregate_set_id})
    return len(rows)


def rebuild_phase_gap_error_aggregate(db_path: Path, aggregate_set_id: str) -> int:
    with _connect(db_path) as conn:
        raw_df = pd.read_sql_query(
            """
            SELECT aggregate_set_id, coupling_function, coupling_strength,
                   phase_gap_error, phase_gap_error_ratio, has_value
            FROM run_phase_gap_error_points
            WHERE aggregate_set_id = ?
            """,
            conn,
            params=(aggregate_set_id,),
        )
        conn.execute(
            "DELETE FROM aggregate_phase_gap_error_points WHERE aggregate_set_id = ?",
            (aggregate_set_id,),
        )
        if raw_df.empty:
            return 0

        rows = []
        for (set_id, coupling_function, coupling_strength), group in raw_df.groupby(
            ["aggregate_set_id", "coupling_function", "coupling_strength"],
            sort=True,
        ):
            valid = group[group["has_value"].astype(bool)]
            errors = valid["phase_gap_error"].dropna().astype(float)
            ratios = valid["phase_gap_error_ratio"].dropna().astype(float)
            rows.append(
                (
                    set_id,
                    coupling_function,
                    float(coupling_strength),
                    _nullable_float(errors.mean()) if not errors.empty else None,
                    _nullable_float(errors.std()) if len(errors) > 1 else None,
                    _nullable_float(errors.min()) if not errors.empty else None,
                    _nullable_float(errors.max()) if not errors.empty else None,
                    _nullable_float(ratios.mean()) if not ratios.empty else None,
                    int(len(errors)),
                    int(len(group)),
                )
            )
        conn.executemany(
            """
            INSERT INTO aggregate_phase_gap_error_points
                (aggregate_set_id, coupling_function, coupling_strength,
                 phase_gap_error_mean, phase_gap_error_std,
                 phase_gap_error_min, phase_gap_error_max,
                 phase_gap_error_ratio_mean, valid_count, count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    _append_history(db_path, "phase_gap_error_aggregate_rebuilt", {"aggregate_set_id": aggregate_set_id})
    return len(rows)


def _completed_run_pairs(
    db_path: Path,
    aggregate_set_id: str,
    interval_db_path: Path | None = None,
) -> set[tuple[float, int]]:
    if not db_path.exists():
        return set()
    per_db_path = interval_db_path or db_path
    if per_db_path == db_path:
        with _connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT r.coupling_strength, r.repeat_index
                FROM runs AS r
                INNER JOIN run_interval_per AS p
                    ON p.run_id = r.run_id
                WHERE r.status = 'completed'
                  AND p.aggregate_set_id = ?
                """,
                (aggregate_set_id,),
            ).fetchall()
    else:
        with _connect(per_db_path) as per_conn:
            completed_run_ids = [
                str(row["run_id"])
                for row in per_conn.execute(
                    """
                    SELECT run_id
                    FROM run_interval_per
                    WHERE aggregate_set_id = ?
                    """,
                    (aggregate_set_id,),
                ).fetchall()
            ]
        if not completed_run_ids:
            return set()
        placeholders = ", ".join("?" for _ in completed_run_ids)
        with _connect(db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT coupling_strength, repeat_index
                FROM runs
                WHERE status = 'completed'
                  AND run_id IN ({placeholders})
                """,
                completed_run_ids,
            ).fetchall()
    return {
        (float(row["coupling_strength"]), int(row["repeat_index"]))
        for row in rows
    }


def _run_convergence_simulations(
    *,
    graph_dir: Path,
    db_path: Path,
    raw_run_db_path: Path,
    manifest: dict[str, Any],
    graph_key: dict[str, Any],
    params: dict[str, Any],
    aggregate_set_id: str,
    status_path: Path,
    status: dict[str, Any],
) -> int:
    k_values = [float(value) for value in params.get("k_values", [])]
    runs_per_k = int(params.get("runs_per_k", 1))
    initial_start_times_by_run = _initial_start_times_by_run(params)
    completed = 0
    for k_value in k_values:
        for repeat_index in range(runs_per_k):
            request = _simulation_request_for_k(
                graph_id=str(manifest["graph_id"]),
                graph_key=graph_key,
                params=params,
                k_value=k_value,
                output_root=raw_run_db_path,
                num_runs=1,
                initial_start_times_by_run=(initial_start_times_by_run[repeat_index],),
            )
            _raise_if_cancel_requested(status_path, db_path)

            def on_progress(done: int, total: int, result: dict[str, Any]) -> None:
                nonlocal completed
                run_id = str(result["run_id"])
                if _cancel_requested(status_path):
                    _delete_raw_run(raw_run_db_path, run_id)
                    raise JobCancelled(f"Job cancelled while run was active: {run_id}")
                completed += 1
                _save_run_record(
                    db_path=db_path,
                    request_id=str(manifest["graph_id"]),
                    run_id=run_id,
                    coupling_strength=k_value,
                    repeat_index=repeat_index,
                    raw_path=f"{RAW_RUN_DB_NAME}::{run_id}",
                    metadata=result,
                )
                _save_run_convergence_from_raw_sqlite(
                    db_path=db_path,
                    raw_db_path=raw_run_db_path,
                    run_id=run_id,
                    aggregate_set_id=aggregate_set_id,
                    stable_cycle_count=int(params["stable_cycle_count"]),
                    phase_gap_change_threshold=float(params["phase_gap_change_threshold"]),
                )
                status.update(
                    {
                        "status": "running_simulations",
                        "completed_runs": completed,
                        "current_run_id": run_id,
                        "updated_at": utc_now_iso(),
                    }
                )
                _write_json(status_path, status)

            run_simulation_request(request, progress_callback=on_progress)
    return completed


def _build_convergence_from_existing_graph(
    *,
    target_db_path: Path,
    source_graph_dir: Path,
    aggregate_set_id: str,
    stable_cycle_count: int,
    phase_gap_change_threshold: float,
    selected_k_values: list[float] | None = None,
    repeat_index_min: int | None = None,
    repeat_index_max: int | None = None,
    status_path: Path,
    status: dict[str, Any],
) -> int:
    source_db_path = source_graph_dir / "graph_data.sqlite"
    source_raw_db_path = source_graph_dir / RAW_RUN_DB_NAME
    if not source_db_path.exists():
        raise FileNotFoundError(f"source graph_data.sqlite not found: {source_db_path}")
    if not source_raw_db_path.exists():
        raise FileNotFoundError(f"source raw_run.sqlite not found: {source_raw_db_path}")

    where_clauses = ["status = 'completed'"]
    query_params: list[Any] = []
    if selected_k_values:
        placeholders = ", ".join("?" for _ in selected_k_values)
        where_clauses.append(f"coupling_strength IN ({placeholders})")
        query_params.extend(float(value) for value in selected_k_values)
    if repeat_index_min is not None:
        where_clauses.append("repeat_index >= ?")
        query_params.append(int(repeat_index_min))
    if repeat_index_max is not None:
        where_clauses.append("repeat_index <= ?")
        query_params.append(int(repeat_index_max))

    with _connect(source_db_path) as source_conn:
        source_runs = source_conn.execute(
            f"""
            SELECT run_id, request_id, coupling_strength, repeat_index, status,
                   raw_path, metadata_json, created_at, updated_at
            FROM runs
            WHERE {' AND '.join(where_clauses)}
            ORDER BY coupling_strength, repeat_index
            """,
            query_params,
        ).fetchall()

    completed = 0
    for source_run in source_runs:
        _raise_if_cancel_requested(status_path, target_db_path)
        run_id = str(source_run["run_id"])
        with _connect(target_db_path) as target_conn:
            target_conn.execute(
                """
                INSERT OR REPLACE INTO runs
                    (run_id, request_id, coupling_strength, repeat_index, status,
                     raw_path, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(source_run["request_id"]),
                    float(source_run["coupling_strength"]),
                    int(source_run["repeat_index"]),
                    "completed",
                    f"{source_raw_db_path}::{run_id}",
                    str(source_run["metadata_json"]),
                    str(source_run["created_at"]),
                    utc_now_iso(),
                ),
            )
        _save_run_convergence_from_raw_sqlite(
            db_path=target_db_path,
            raw_db_path=source_raw_db_path,
            run_id=run_id,
            aggregate_set_id=aggregate_set_id,
            stable_cycle_count=stable_cycle_count,
            phase_gap_change_threshold=phase_gap_change_threshold,
        )
        completed += 1
        status.update(
            {
                "status": "running_analysis",
                "completed_runs": completed,
                "current_run_id": run_id,
                "updated_at": utc_now_iso(),
            }
        )
        _write_json(status_path, status)
    return completed


def _run_phase_gap_error_simulations(
    *,
    graph_dir: Path,
    db_path: Path,
    raw_run_db_path: Path,
    manifest: dict[str, Any],
    graph_key: dict[str, Any],
    params: dict[str, Any],
    aggregate_set_id: str,
    target_cycle_mode: str,
    target_cycle_index: object | None,
    status_path: Path,
    status: dict[str, Any],
) -> int:
    k_values = [float(value) for value in params.get("k_values", [])]
    runs_per_k = int(params.get("runs_per_k", 1))
    initial_start_times_by_run = _initial_start_times_by_run(params)
    completed = 0
    for k_value in k_values:
        for repeat_index in range(runs_per_k):
            request = _simulation_request_for_k(
                graph_id=str(manifest["graph_id"]),
                graph_key=graph_key,
                params=params,
                k_value=k_value,
                output_root=raw_run_db_path,
                num_runs=1,
                initial_start_times_by_run=(initial_start_times_by_run[repeat_index],),
            )
            _raise_if_cancel_requested(status_path, db_path)

            def on_progress(done: int, total: int, result: dict[str, Any]) -> None:
                nonlocal completed
                run_id = str(result["run_id"])
                if _cancel_requested(status_path):
                    _delete_raw_run(raw_run_db_path, run_id)
                    raise JobCancelled(f"Job cancelled while run was active: {run_id}")
                completed += 1
                _save_run_record(
                    db_path=db_path,
                    request_id=str(manifest["graph_id"]),
                    run_id=run_id,
                    coupling_strength=k_value,
                    repeat_index=repeat_index,
                    raw_path=f"{RAW_RUN_DB_NAME}::{run_id}",
                    metadata=result,
                )
                _save_run_phase_gap_error_from_raw_sqlite(
                    db_path=db_path,
                    raw_db_path=raw_run_db_path,
                    run_id=run_id,
                    aggregate_set_id=aggregate_set_id,
                    target_cycle_mode=target_cycle_mode,
                    target_cycle_index=target_cycle_index,
                )
                status.update(
                    {
                        "status": "running_simulations",
                        "completed_runs": completed,
                        "current_run_id": run_id,
                        "updated_at": utc_now_iso(),
                    }
                )
                _write_json(status_path, status)

            run_simulation_request(request, progress_callback=on_progress)
    return completed


def _build_phase_gap_error_from_existing_graph(
    *,
    target_db_path: Path,
    source_graph_dir: Path,
    aggregate_set_id: str,
    target_cycle_mode: str,
    target_cycle_index: object | None,
    selected_k_values: list[float] | None = None,
    repeat_index_min: int | None = None,
    repeat_index_max: int | None = None,
    status_path: Path,
    status: dict[str, Any],
) -> int:
    source_db_path = source_graph_dir / "graph_data.sqlite"
    source_raw_db_path = source_graph_dir / RAW_RUN_DB_NAME
    if not source_db_path.exists():
        raise FileNotFoundError(f"source graph_data.sqlite not found: {source_db_path}")
    if not source_raw_db_path.exists():
        raise FileNotFoundError(f"source raw_run.sqlite not found: {source_raw_db_path}")

    where_clauses = ["status = 'completed'"]
    query_params: list[Any] = []
    if selected_k_values:
        placeholders = ", ".join("?" for _ in selected_k_values)
        where_clauses.append(f"coupling_strength IN ({placeholders})")
        query_params.extend(float(value) for value in selected_k_values)
    if repeat_index_min is not None:
        where_clauses.append("repeat_index >= ?")
        query_params.append(int(repeat_index_min))
    if repeat_index_max is not None:
        where_clauses.append("repeat_index <= ?")
        query_params.append(int(repeat_index_max))

    with _connect(source_db_path) as source_conn:
        source_runs = source_conn.execute(
            f"""
            SELECT run_id, request_id, coupling_strength, repeat_index, status,
                   raw_path, metadata_json, created_at, updated_at
            FROM runs
            WHERE {' AND '.join(where_clauses)}
            ORDER BY coupling_strength, repeat_index
            """,
            query_params,
        ).fetchall()

    completed = 0
    for source_run in source_runs:
        _raise_if_cancel_requested(status_path, target_db_path)
        run_id = str(source_run["run_id"])
        with _connect(target_db_path) as target_conn:
            target_conn.execute(
                """
                INSERT OR REPLACE INTO runs
                    (run_id, request_id, coupling_strength, repeat_index, status,
                     raw_path, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(source_run["request_id"]),
                    float(source_run["coupling_strength"]),
                    int(source_run["repeat_index"]),
                    "completed",
                    f"{source_raw_db_path}::{run_id}",
                    str(source_run["metadata_json"]),
                    str(source_run["created_at"]),
                    utc_now_iso(),
                ),
            )
        _save_run_phase_gap_error_from_raw_sqlite(
            db_path=target_db_path,
            raw_db_path=source_raw_db_path,
            run_id=run_id,
            aggregate_set_id=aggregate_set_id,
            target_cycle_mode=target_cycle_mode,
            target_cycle_index=target_cycle_index,
        )
        completed += 1
        status.update(
            {
                "status": "running_analysis",
                "completed_runs": completed,
                "current_run_id": run_id,
                "updated_at": utc_now_iso(),
            }
        )
        _write_json(status_path, status)
    return completed


def render_interval_per_vs_k_pdf(
    *,
    graph_dir: Path,
    db_path: Path,
    aggregate_set_id: str,
    coupling_function: str,
    interval_start_ms: float,
    interval_end_ms: float,
    plot_settings: dict[str, Any] | None = None,
    strength_ratio: float | None = None,
) -> Path:
    settings = plot_settings or {}
    with _connect(db_path) as conn:
        df = pd.read_sql_query(
            """
            SELECT coupling_strength, per_percent_mean, per_percent_std, count
            FROM aggregate_interval_per
            WHERE aggregate_set_id = ?
            ORDER BY coupling_strength
            """,
            conn,
            params=(aggregate_set_id,),
        )

    if df.empty:
        raise ValueError("No aggregate data to render")

    figures_dir = graph_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    output_path = figures_dir / (
        f"{_safe_filename(coupling_function)}_per_by_k_"
        f"interval_{int(interval_start_ms)}_to_{int(interval_end_ms)}ms.pdf"
    )

    plt.figure(
        figsize=(float(settings.get("figure_width", 8.0)), float(settings.get("figure_height", 5.0))),
        constrained_layout=True,
    )
    yerr = df["per_percent_std"].fillna(0.0).to_numpy(dtype=float)
    show_error_bars = bool(settings.get("show_error_bars", True))
    line_style = str(settings.get("line_style", "-"))
    if line_style == "None":
        line_style = "None"
    if show_error_bars:
        plt.errorbar(
            df["coupling_strength"],
            df["per_percent_mean"],
            yerr=yerr,
            marker=str(settings.get("marker", "o")),
            markersize=float(settings.get("marker_size", 6.0)),
            linestyle=line_style,
            linewidth=float(settings.get("line_width", 1.5)),
            capsize=float(settings.get("error_bar_capsize", 4.0)),
        )
    else:
        plt.plot(
            df["coupling_strength"],
            df["per_percent_mean"],
            marker=str(settings.get("marker", "o")),
            markersize=float(settings.get("marker_size", 6.0)),
            linestyle=line_style,
            linewidth=float(settings.get("line_width", 1.5)),
        )

    if settings.get("ylim_min") is not None or settings.get("ylim_max") is not None:
        plt.ylim(bottom=settings.get("ylim_min"), top=settings.get("ylim_max"))
    else:
        plt.ylim(bottom=0)
    if settings.get("xlim_min") is not None or settings.get("xlim_max") is not None:
        plt.xlim(left=settings.get("xlim_min"), right=settings.get("xlim_max"))

    if bool(settings.get("show_min_annotation", False)):
        ax = plt.gca()
        min_row = df.loc[df["per_percent_mean"].idxmin()]
        min_x = float(min_row["coupling_strength"])
        min_y = float(min_row["per_percent_mean"])
        ax.scatter(
            [min_x],
            [min_y],
            marker="*",
            s=max(float(settings.get("marker_size", 6.0)) * 28.0, 80.0),
            color="tab:red",
            zorder=5,
            clip_on=False,
        )
        y_offset = float(settings.get("min_annotation_y_offset", 10.0))
        y_low, y_high = ax.get_ylim()
        if y_high > y_low and min_y > y_low + (y_high - y_low) * 0.8:
            y_offset = -abs(y_offset)
        va = "top" if y_offset < 0 else "bottom"
        ax.annotate(
            f"min PER: {min_y:.3g}%\nK={min_x:g}",
            xy=(min_x, min_y),
            xytext=(
                float(settings.get("min_annotation_x_offset", 10.0)),
                y_offset,
            ),
            textcoords="offset points",
            fontsize=int(settings.get("min_annotation_font_size", 10)),
            color="tab:red",
            arrowprops={"arrowstyle": "->", "linewidth": 0.8, "color": "tab:red"},
            bbox={"boxstyle": "round,pad=0.25", "fc": "white", "ec": "tab:red", "alpha": 0.9},
            annotation_clip=False,
            va=va,
            zorder=6,
        )

    ax = plt.gca()
    plt.xlabel("Coupling strength K", fontsize=int(settings.get("font_size_label", 12)))
    plt.ylabel("Interval PER [%]", fontsize=int(settings.get("font_size_label", 12)))
    if bool(settings.get("show_title", True)):
        plt.title(
            f"{coupling_function}: {interval_start_ms:g} to {interval_end_ms:g} ms",
            fontsize=int(settings.get("font_size_title", 12)),
        )
    plt.xticks(fontsize=int(settings.get("font_size_ticks", 10)))
    plt.yticks(fontsize=int(settings.get("font_size_ticks", 10)))
    _add_x_axis_multiplier(ax, strength_ratio, int(settings.get("font_size_ticks", 10)))
    plt.grid(bool(settings.get("show_grid", True)))
    plt.savefig(output_path, dpi=int(settings.get("save_dpi", 300)), bbox_inches="tight")
    plt.close()
    _append_history(db_path, "pdf_rendered", {"output": _relative_to_graph(graph_dir, output_path)})
    return output_path


def render_convergence_cycle_vs_k_pdf(
    *,
    graph_dir: Path,
    db_path: Path,
    aggregate_set_id: str,
    coupling_function: str,
    stable_cycle_count: int,
    phase_gap_change_threshold: float,
    plot_settings: dict[str, Any] | None = None,
    strength_ratio: float | None = None,
) -> Path:
    settings = plot_settings or {}
    with _connect(db_path) as conn:
        df = pd.read_sql_query(
            """
            SELECT coupling_strength, convergence_cycle_mean,
                   convergence_cycle_std, convergence_rate_percent, count,
                   converged_count
            FROM aggregate_convergence_cycles
            WHERE aggregate_set_id = ?
            ORDER BY coupling_strength
            """,
            conn,
            params=(aggregate_set_id,),
        )
    if df.empty:
        raise ValueError("No convergence aggregate data to render")

    figures_dir = graph_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    output_path = figures_dir / (
        f"{_safe_filename(coupling_function)}_convergence_cycle_by_k_"
        f"{int(stable_cycle_count)}cycles_thr_{_safe_filename(f'{phase_gap_change_threshold:g}')}.pdf"
    )

    plot_df = df.dropna(subset=["convergence_cycle_mean"]).copy()
    if plot_df.empty:
        raise ValueError("No converged runs to render")

    plt.figure(
        figsize=(float(settings.get("figure_width", 8.0)), float(settings.get("figure_height", 5.0))),
        constrained_layout=True,
    )
    yerr = plot_df["convergence_cycle_std"].fillna(0.0).to_numpy(dtype=float)
    show_error_bars = bool(settings.get("show_error_bars", True))
    line_style = str(settings.get("line_style", "-"))
    if line_style == "None":
        line_style = "None"
    if show_error_bars:
        plt.errorbar(
            plot_df["coupling_strength"],
            plot_df["convergence_cycle_mean"],
            yerr=yerr,
            marker=str(settings.get("marker", "o")),
            markersize=float(settings.get("marker_size", 6.0)),
            linestyle=line_style,
            linewidth=float(settings.get("line_width", 1.5)),
            capsize=float(settings.get("error_bar_capsize", 4.0)),
        )
    else:
        plt.plot(
            plot_df["coupling_strength"],
            plot_df["convergence_cycle_mean"],
            marker=str(settings.get("marker", "o")),
            markersize=float(settings.get("marker_size", 6.0)),
            linestyle=line_style,
            linewidth=float(settings.get("line_width", 1.5)),
        )

    if settings.get("ylim_min") is not None or settings.get("ylim_max") is not None:
        plt.ylim(bottom=settings.get("ylim_min"), top=settings.get("ylim_max"))
    else:
        plt.ylim(bottom=0)
    if settings.get("xlim_min") is not None or settings.get("xlim_max") is not None:
        plt.xlim(left=settings.get("xlim_min"), right=settings.get("xlim_max"))

    if bool(settings.get("show_min_annotation", False)):
        ax = plt.gca()
        min_row = plot_df.loc[plot_df["convergence_cycle_mean"].idxmin()]
        min_x = float(min_row["coupling_strength"])
        min_y = float(min_row["convergence_cycle_mean"])
        ax.scatter(
            [min_x],
            [min_y],
            marker="*",
            s=max(float(settings.get("marker_size", 6.0)) * 28.0, 80.0),
            color="tab:red",
            zorder=5,
            clip_on=False,
        )
        ax.annotate(
            f"min cycle: {min_y:.3g}\nK={min_x:g}",
            xy=(min_x, min_y),
            xytext=(
                float(settings.get("min_annotation_x_offset", 10.0)),
                float(settings.get("min_annotation_y_offset", 10.0)),
            ),
            textcoords="offset points",
            fontsize=int(settings.get("min_annotation_font_size", 10)),
            color="tab:red",
            arrowprops={"arrowstyle": "->", "linewidth": 0.8, "color": "tab:red"},
            bbox={"boxstyle": "round,pad=0.25", "fc": "white", "ec": "tab:red", "alpha": 0.9},
            annotation_clip=False,
            zorder=6,
        )

    ax = plt.gca()
    plt.xlabel("Coupling strength K", fontsize=int(settings.get("font_size_label", 12)))
    plt.ylabel("Convergence cycle", fontsize=int(settings.get("font_size_label", 12)))
    if bool(settings.get("show_title", True)):
        plt.title(
            (
                f"{coupling_function}: convergence cycle "
                f"(N={stable_cycle_count}, threshold={phase_gap_change_threshold:g})"
            ),
            fontsize=int(settings.get("font_size_title", 12)),
        )
    plt.xticks(fontsize=int(settings.get("font_size_ticks", 10)))
    plt.yticks(fontsize=int(settings.get("font_size_ticks", 10)))
    _add_x_axis_multiplier(ax, strength_ratio, int(settings.get("font_size_ticks", 10)))
    plt.grid(bool(settings.get("show_grid", True)))
    plt.savefig(output_path, dpi=int(settings.get("save_dpi", 300)), bbox_inches="tight")
    plt.close()
    _append_history(db_path, "pdf_rendered", {"output": _relative_to_graph(graph_dir, output_path)})
    return output_path


def render_phase_gap_error_vs_k_pdf(
    *,
    graph_dir: Path,
    db_path: Path,
    aggregate_set_id: str,
    coupling_function: str,
    target_cycle_mode: str,
    target_cycle_index: object | None,
    plot_settings: dict[str, Any] | None = None,
    strength_ratio: float | None = None,
) -> Path:
    settings = plot_settings or {}
    with _connect(db_path) as conn:
        df = pd.read_sql_query(
            """
            SELECT coupling_strength, phase_gap_error_mean,
                   phase_gap_error_std, phase_gap_error_min,
                   phase_gap_error_max, phase_gap_error_ratio_mean,
                   valid_count, count
            FROM aggregate_phase_gap_error_points
            WHERE aggregate_set_id = ?
            ORDER BY coupling_strength
            """,
            conn,
            params=(aggregate_set_id,),
        )
    if df.empty:
        raise ValueError("No phase-gap error aggregate data to render")

    figures_dir = graph_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    point_label = (
        f"cycle_{int(target_cycle_index or 0)}"
        if target_cycle_mode == "cycle_index"
        else "last"
    )
    output_path = figures_dir / (
        f"{_safe_filename(coupling_function)}_phase_gap_error_by_k_{point_label}.pdf"
    )

    plot_df = df.dropna(subset=["phase_gap_error_mean"]).copy()
    if plot_df.empty:
        raise ValueError("No valid phase-gap error values to render")

    plt.figure(
        figsize=(float(settings.get("figure_width", 8.0)), float(settings.get("figure_height", 5.0))),
        constrained_layout=True,
    )
    yerr = plot_df["phase_gap_error_std"].fillna(0.0).to_numpy(dtype=float)
    show_error_bars = bool(settings.get("show_error_bars", True))
    line_style = str(settings.get("line_style", "-"))
    if line_style == "None":
        line_style = "None"
    if show_error_bars:
        plt.errorbar(
            plot_df["coupling_strength"],
            plot_df["phase_gap_error_mean"],
            yerr=yerr,
            marker=str(settings.get("marker", "o")),
            markersize=float(settings.get("marker_size", 6.0)),
            linestyle=line_style,
            linewidth=float(settings.get("line_width", 1.5)),
            capsize=float(settings.get("error_bar_capsize", 4.0)),
        )
    else:
        plt.plot(
            plot_df["coupling_strength"],
            plot_df["phase_gap_error_mean"],
            marker=str(settings.get("marker", "o")),
            markersize=float(settings.get("marker_size", 6.0)),
            linestyle=line_style,
            linewidth=float(settings.get("line_width", 1.5)),
        )

    if settings.get("ylim_min") is not None or settings.get("ylim_max") is not None:
        plt.ylim(bottom=settings.get("ylim_min"), top=settings.get("ylim_max"))
    else:
        plt.ylim(bottom=0)
    if settings.get("xlim_min") is not None or settings.get("xlim_max") is not None:
        plt.xlim(left=settings.get("xlim_min"), right=settings.get("xlim_max"))

    if bool(settings.get("show_min_annotation", False)):
        ax = plt.gca()
        min_row = plot_df.loc[plot_df["phase_gap_error_mean"].idxmin()]
        min_x = float(min_row["coupling_strength"])
        min_y = float(min_row["phase_gap_error_mean"])
        ax.scatter(
            [min_x],
            [min_y],
            marker="*",
            s=max(float(settings.get("marker_size", 6.0)) * 28.0, 80.0),
            color="tab:red",
            zorder=5,
            clip_on=False,
        )
        ax.annotate(
            f"min error: {min_y:.3g} rad\nK={min_x:g}",
            xy=(min_x, min_y),
            xytext=(
                float(settings.get("min_annotation_x_offset", 10.0)),
                float(settings.get("min_annotation_y_offset", 10.0)),
            ),
            textcoords="offset points",
            fontsize=int(settings.get("min_annotation_font_size", 10)),
            color="tab:red",
            arrowprops={"arrowstyle": "->", "linewidth": 0.8, "color": "tab:red"},
            bbox={"boxstyle": "round,pad=0.25", "fc": "white", "ec": "tab:red", "alpha": 0.9},
            annotation_clip=False,
            zorder=6,
        )

    ax = plt.gca()
    plt.xlabel("Coupling strength K", fontsize=int(settings.get("font_size_label", 12)))
    plt.ylabel("Mean abs phase-gap error [rad]", fontsize=int(settings.get("font_size_label", 12)))
    if bool(settings.get("show_title", True)):
        point_text = (
            f"cycle {int(target_cycle_index or 0)}"
            if target_cycle_mode == "cycle_index"
            else "final available cycle"
        )
        plt.title(
            f"{coupling_function}: phase-gap error at {point_text}",
            fontsize=int(settings.get("font_size_title", 12)),
        )
    plt.xticks(fontsize=int(settings.get("font_size_ticks", 10)))
    plt.yticks(fontsize=int(settings.get("font_size_ticks", 10)))
    _add_x_axis_multiplier(ax, strength_ratio, int(settings.get("font_size_ticks", 10)))
    plt.grid(bool(settings.get("show_grid", True)))
    plt.savefig(output_path, dpi=int(settings.get("save_dpi", 300)), bbox_inches="tight")
    plt.close()
    _append_history(db_path, "pdf_rendered", {"output": _relative_to_graph(graph_dir, output_path)})
    return output_path


def _add_x_axis_multiplier(ax: Any, strength_ratio: float | None, font_size: int) -> None:
    if strength_ratio is None:
        return
    ax.text(
        1.0,
        -0.105,
        f"$\\times$ {_format_scientific(strength_ratio)}",
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=font_size,
    )


def _format_scientific(value: float) -> str:
    text = f"{value:.1e}"
    text = text.replace("e-0", "e-").replace("e+0", "e+")
    return text


def _convergence_aggregate_set_id(stable_cycle_count: int, threshold: float) -> str:
    threshold_text = f"{threshold:g}".replace("-", "m").replace(".", "p")
    return f"convergence_{int(stable_cycle_count)}cycles_thr_{threshold_text}"


def _phase_gap_error_aggregate_set_id(
    target_cycle_mode: str,
    target_cycle_index: object | None,
) -> str:
    if target_cycle_mode == "cycle_index":
        return f"phase_gap_cycle_{int(target_cycle_index or 0)}"
    return "phase_gap_last"


def _initial_start_times_by_run(params: dict[str, Any]) -> tuple[tuple[int, ...], ...]:
    base = dict(params.get("simulation_base") or {})
    runs_per_k = int(params.get("runs_per_k", 1))
    cycle_time = int(base.get("cycle_time", 30000))
    device_count = int(base.get("device_count", 20))
    seed = int(base.get("seed", 1))

    if runs_per_k < 1:
        raise ValueError("runs_per_k must be at least 1")
    if cycle_time < 1:
        raise ValueError("cycle_time must be at least 1")
    if device_count < 1:
        raise ValueError("device_count must be at least 1")

    start_ms, end_ms = _initial_phase_range_ms(base, cycle_time)
    rng = random.Random(seed)
    start_times_by_run: list[tuple[int, ...]] = []
    for _ in range(runs_per_k):
        starts = [rng.randrange(start_ms, end_ms) for _ in range(device_count)]
        starts.sort()
        start_times_by_run.append(tuple(starts))
    return tuple(start_times_by_run)


def _initial_phase_range_ms(base: dict[str, Any], cycle_time: int) -> tuple[int, int]:
    start_percent = float(base.get("initial_phase_start_percent", 0.0))
    end_percent = float(base.get("initial_phase_end_percent", 100.0))
    if not 0.0 <= start_percent <= 100.0:
        raise ValueError("initial_phase_start_percent must be between 0 and 100")
    if not 0.0 <= end_percent <= 100.0:
        raise ValueError("initial_phase_end_percent must be between 0 and 100")
    if end_percent <= start_percent:
        raise ValueError("initial_phase_end_percent must be larger than initial_phase_start_percent")

    start_ms = int(math.floor(cycle_time * start_percent / 100.0))
    end_ms = int(math.ceil(cycle_time * end_percent / 100.0))
    start_ms = max(0, min(start_ms, cycle_time - 1))
    end_ms = max(start_ms + 1, min(end_ms, cycle_time))
    return start_ms, end_ms


def _simulation_request_for_k(
    *,
    graph_id: str,
    graph_key: dict[str, Any],
    params: dict[str, Any],
    k_value: float,
    output_root: Path,
    num_runs: int | None = None,
    initial_start_times_by_run: tuple[tuple[int, ...], ...] = tuple(),
) -> SimulationRequest:
    base = dict(params.get("simulation_base") or {})
    coupling_function = str(graph_key["coupling_function"])
    return SimulationRequest(
        num_runs=int(num_runs if num_runs is not None else params.get("runs_per_k", 1)),
        seed=int(base.get("seed", 1)),
        coupling_function=coupling_function,
        coupling_strength=int(k_value),
        strength_ratio=float(base.get("strength_ratio", -0.0001)),
        cycle_time=int(base.get("cycle_time", 30000)),
        listening_rate=int(base.get("listening_rate", 25)),
        device_count=int(base.get("device_count", 20)),
        duration=int(base.get("duration_ms", 2_000_000)),
        start_step_count=int(base.get("start_step_count", 1000)),
        start_step=int(base.get("start_step", 10)),
        tags=(
            "graph_first",
            f"graph_id_{graph_id}",
            "graph_interval_per_vs_k",
            f"coupling_function_{coupling_function}",
            f"coupling_strength_{int(k_value)}",
        ),
        output_root=output_root,
        max_workers=int(base.get("max_workers", 1)),
        start_timing_mode=str(
            base.get("start_timing_mode", "random_cycle_ms_with_replacement")
        ),  # type: ignore[arg-type]
        initial_start_times_by_run=initial_start_times_by_run,
        simulation_mode="per_measurement",
        carrier_sense_duration_ms=float(base.get("carrier_sense_duration_ms", 0.0)),
        lora_payload_bytes=int(base.get("lora_payload_bytes", 16)),
        lora_spreading_factor=int(base.get("lora_spreading_factor", 7)),
        lora_bandwidth_hz=int(base.get("lora_bandwidth_hz", 125_000)),
        lora_coding_rate_denominator=int(base.get("lora_coding_rate_denominator", 5)),
        lora_preamble_symbols=int(base.get("lora_preamble_symbols", 8)),
        lora_explicit_header=bool(base.get("lora_explicit_header", True)),
        lora_crc_enabled=bool(base.get("lora_crc_enabled", True)),
        lora_low_data_rate_optimize=_optional_bool(
            base.get("lora_low_data_rate_optimize", None)
        ),
    )


def _save_run_record(
    *,
    db_path: Path,
    request_id: str,
    run_id: str,
    coupling_strength: float,
    repeat_index: int,
    raw_path: str,
    metadata: dict[str, Any],
) -> None:
    now = utc_now_iso()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO runs
                (run_id, request_id, coupling_strength, repeat_index, status,
                 raw_path, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                request_id,
                float(coupling_strength),
                int(repeat_index),
                "completed",
                raw_path,
                json.dumps(metadata, ensure_ascii=False),
                now,
                now,
            ),
        )


def _save_run_intermediate_and_interval(
    *,
    db_path: Path,
    graph_dir: Path,
    run_id: str,
    run_dir: Path,
    aggregate_set_id: str,
    interval_start_ms: float,
    interval_end_ms: float,
) -> None:
    cycle_data_path = ensure_cycle_data_for_run(run_dir)
    tags, coupling_function, coupling_strength = read_metadata(run_dir / "metadata.csv")
    num_devices = extract_device_count_from_tags(tags)
    send_df = read_send_log(run_dir / "send_log.csv")
    send_df = normalize_oscillator_id_column(send_df, tags)
    send_df = normalize_time_column(send_df, tags)
    _, cycle_starts, _ = read_calculated_cycle_data(cycle_data_path)

    send_df = assign_cycles_from_reference_windows(send_df, cycle_starts)
    max_cycle = len(cycle_starts)
    counts_full = np.zeros(max_cycle, dtype=np.int64)
    if max_cycle > 0 and not send_df.empty:
        counts_by_cycle = send_df.groupby("cycle_index").size().sort_index()
        cycle_indices = counts_by_cycle.index.to_numpy(dtype=np.int64)
        valid_indices = (cycle_indices >= 1) & (cycle_indices <= max_cycle)
        counts_full[cycle_indices[valid_indices] - 1] = counts_by_cycle.to_numpy(dtype=np.int64)[valid_indices]
    cumulative_counts = np.concatenate(
        [np.array([0], dtype=np.int64), np.cumsum(counts_full, dtype=np.int64)]
    )

    metrics = compute_interval_per_from_cycle_counts(
        cycle_starts=cycle_starts,
        cumulative_counts=cumulative_counts,
        num_devices=num_devices,
        interval_start_ms=interval_start_ms,
        interval_end_ms=interval_end_ms,
    )
    if metrics is None:
        raise ValueError(f"Could not compute interval PER for run {run_id}")

    with _connect(db_path) as conn:
        conn.execute("DELETE FROM run_cycle_counts WHERE run_id = ?", (run_id,))
        conn.executemany(
            """
            INSERT INTO run_cycle_counts
                (run_id, cycle_index, expected_packets, actual_packets,
                 cumulative_expected_packets, cumulative_actual_packets)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    int(index + 1),
                    int(num_devices),
                    int(count),
                    int((index + 1) * num_devices),
                    int(cumulative_counts[index + 1]),
                )
                for index, count in enumerate(counts_full)
            ],
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO run_interval_per
                (aggregate_set_id, run_id, coupling_function, coupling_strength,
                 interval_start_ms, interval_end_ms, interval_cycle_count,
                 expected_packets, actual_packets, per_percent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aggregate_set_id,
                run_id,
                coupling_function,
                float(coupling_strength),
                interval_start_ms,
                interval_end_ms,
                int(metrics["interval_cycle_count"]),
                int(metrics["expected_packets"]),
                int(metrics["actual_packets"]),
                float(metrics["per_percent"]),
            ),
        )
    _append_history(
        db_path,
        "run_saved",
        {
            "run_id": run_id,
            "raw_path": _relative_to_graph(graph_dir, run_dir),
            "per_percent": metrics["per_percent"],
        },
    )


def _save_run_intermediate_and_interval_from_raw_sqlite(
    *,
    db_path: Path,
    raw_db_path: Path,
    run_id: str,
    aggregate_set_id: str,
    interval_start_ms: float,
    interval_end_ms: float,
) -> None:
    raw_conn = sqlite_runs.connect(raw_db_path)
    try:
        raw_conn.row_factory = sqlite3.Row
        sqlite_runs.initialize(raw_conn)
        metadata_row = raw_conn.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if metadata_row is None:
            raise ValueError(f"raw run not found in {raw_db_path}: {run_id}")

        tags_raw = metadata_row["tags"] if "tags" in metadata_row.keys() else ""
        tags = [tag.strip() for tag in str(tags_raw).split(";") if tag.strip()]
        coupling_function = str(metadata_row["coupling_function"])
        coupling_strength = float(metadata_row["coupling_strength"])
        num_devices = extract_device_count_from_tags(tags)

        send_df = pd.read_sql_query(
            """
            SELECT time, oscillator_id, send_count, transmission_end_time, transmission_time_ms
            FROM send_log
            WHERE run_id = ?
            ORDER BY time, oscillator_id
            """,
            raw_conn,
            params=(run_id,),
        )
        cycle_df = pd.read_sql_query(
            """
            SELECT cycle_index, cycle_start_time, is_original_cycle, reference_id
            FROM calculated_cycle_data
            WHERE run_id = ?
            ORDER BY cycle_index
            """,
            raw_conn,
            params=(run_id,),
        )
    finally:
        raw_conn.close()

    if send_df.empty:
        raise ValueError(f"send_log is empty for run {run_id}")
    if cycle_df.empty:
        raise ValueError(f"calculated_cycle_data is empty for run {run_id}")

    send_df = normalize_oscillator_id_column(send_df, tags)
    send_df = normalize_time_column(send_df, tags)
    cycle_starts = cycle_df["cycle_start_time"].to_numpy(dtype=np.float64)

    send_df = assign_cycles_from_reference_windows(send_df, cycle_starts)
    max_cycle = len(cycle_starts)
    counts_full = np.zeros(max_cycle, dtype=np.int64)
    if max_cycle > 0 and not send_df.empty:
        counts_by_cycle = send_df.groupby("cycle_index").size().sort_index()
        cycle_indices = counts_by_cycle.index.to_numpy(dtype=np.int64)
        valid_indices = (cycle_indices >= 1) & (cycle_indices <= max_cycle)
        counts_full[cycle_indices[valid_indices] - 1] = counts_by_cycle.to_numpy(dtype=np.int64)[valid_indices]
    cumulative_counts = np.concatenate(
        [np.array([0], dtype=np.int64), np.cumsum(counts_full, dtype=np.int64)]
    )

    metrics = compute_interval_per_from_cycle_counts(
        cycle_starts=cycle_starts,
        cumulative_counts=cumulative_counts,
        num_devices=num_devices,
        interval_start_ms=interval_start_ms,
        interval_end_ms=interval_end_ms,
    )
    if metrics is None:
        raise ValueError(f"Could not compute interval PER for run {run_id}")

    with _connect(db_path) as conn:
        conn.execute("DELETE FROM run_cycle_counts WHERE run_id = ?", (run_id,))
        conn.executemany(
            """
            INSERT INTO run_cycle_counts
                (run_id, cycle_index, expected_packets, actual_packets,
                 cumulative_expected_packets, cumulative_actual_packets)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    int(index + 1),
                    int(num_devices),
                    int(count),
                    int((index + 1) * num_devices),
                    int(cumulative_counts[index + 1]),
                )
                for index, count in enumerate(counts_full)
            ],
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO run_interval_per
                (aggregate_set_id, run_id, coupling_function, coupling_strength,
                 interval_start_ms, interval_end_ms, interval_cycle_count,
                 expected_packets, actual_packets, per_percent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aggregate_set_id,
                run_id,
                coupling_function,
                coupling_strength,
                interval_start_ms,
                interval_end_ms,
                int(metrics["interval_cycle_count"]),
                int(metrics["expected_packets"]),
                int(metrics["actual_packets"]),
                float(metrics["per_percent"]),
            ),
        )
    _append_history(
        db_path,
        "run_saved",
        {
            "run_id": run_id,
            "raw_path": f"{RAW_RUN_DB_NAME}::{run_id}",
            "per_percent": metrics["per_percent"],
        },
    )


def _save_run_convergence_from_raw_sqlite(
    *,
    db_path: Path,
    raw_db_path: Path,
    run_id: str,
    aggregate_set_id: str,
    stable_cycle_count: int,
    phase_gap_change_threshold: float,
) -> None:
    result = _compute_run_convergence_from_raw_sqlite(
        raw_db_path=raw_db_path,
        run_id=run_id,
        stable_cycle_count=stable_cycle_count,
        phase_gap_change_threshold=phase_gap_change_threshold,
    )
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO run_convergence_cycles
                (aggregate_set_id, run_id, coupling_function, coupling_strength,
                 stable_cycle_count, phase_gap_change_threshold,
                 convergence_cycle, converged, checked_cycle_count,
                 max_gap_change_at_convergence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aggregate_set_id,
                run_id,
                result["coupling_function"],
                float(result["coupling_strength"]),
                int(stable_cycle_count),
                float(phase_gap_change_threshold),
                result["convergence_cycle"],
                int(result["converged"]),
                int(result["checked_cycle_count"]),
                result["max_gap_change_at_convergence"],
            ),
        )
    _append_history(
        db_path,
        "run_convergence_saved",
        {
            "run_id": run_id,
            "convergence_cycle": result["convergence_cycle"],
            "converged": result["converged"],
        },
    )


def _compute_run_convergence_from_raw_sqlite(
    *,
    raw_db_path: Path,
    run_id: str,
    stable_cycle_count: int,
    phase_gap_change_threshold: float,
) -> dict[str, Any]:
    if stable_cycle_count < 1:
        raise ValueError("stable_cycle_count must be at least 1")
    if phase_gap_change_threshold < 0:
        raise ValueError("phase_gap_change_threshold must be non-negative")

    raw_conn = sqlite_runs.connect(raw_db_path)
    try:
        raw_conn.row_factory = sqlite3.Row
        sqlite_runs.initialize(raw_conn)
        metadata_row = raw_conn.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if metadata_row is None:
            raise ValueError(f"raw run not found in {raw_db_path}: {run_id}")
        tags_raw = metadata_row["tags"] if "tags" in metadata_row.keys() else ""
        tags = [tag.strip() for tag in str(tags_raw).split(";") if tag.strip()]
        coupling_function = str(metadata_row["coupling_function"])
        coupling_strength = float(metadata_row["coupling_strength"])
        num_devices = extract_device_count_from_tags(tags)
        send_df = pd.read_sql_query(
            """
            SELECT time, oscillator_id, send_count, transmission_end_time, transmission_time_ms
            FROM send_log
            WHERE run_id = ?
            ORDER BY time, oscillator_id
            """,
            raw_conn,
            params=(run_id,),
        )
        cycle_df = pd.read_sql_query(
            """
            SELECT cycle_index, cycle_start_time, is_original_cycle, reference_id
            FROM calculated_cycle_data
            WHERE run_id = ?
            ORDER BY cycle_index
            """,
            raw_conn,
            params=(run_id,),
        )
    finally:
        raw_conn.close()

    if send_df.empty:
        raise ValueError(f"send_log is empty for run {run_id}")
    if cycle_df.empty:
        raise ValueError(f"calculated_cycle_data is empty for run {run_id}")

    send_df = normalize_oscillator_id_column(send_df, tags)
    send_df = normalize_time_column(send_df, tags)
    cycle_starts = cycle_df["cycle_start_time"].to_numpy(dtype=np.float64)
    cycle_lengths = _cycle_interval_lengths(cycle_starts)
    indexed_df = assign_cycles_from_reference_windows(send_df, cycle_starts)

    if indexed_df.empty:
        return {
            "coupling_function": coupling_function,
            "coupling_strength": coupling_strength,
            "converged": False,
            "convergence_cycle": None,
            "checked_cycle_count": 0,
            "max_gap_change_at_convergence": None,
        }

    first_sends = (
        indexed_df.sort_values(["cycle_index", "detection_time", "oscillator_id"], kind="stable")
        .drop_duplicates(subset=["cycle_index", "oscillator_id"], keep="first")
    )

    previous_gaps: np.ndarray | None = None
    stable_streak = 0
    checked_cycles = 0
    convergence_cycle: int | None = None
    convergence_max_change: float | None = None

    for cycle_index, cycle_df_for_index in first_sends.groupby("cycle_index", sort=True):
        result_index = int(cycle_index) - 1
        if result_index < 0 or result_index >= len(cycle_starts):
            previous_gaps = None
            stable_streak = 0
            continue
        cycle_length = float(cycle_lengths[result_index])
        if (
            not np.isfinite(cycle_length)
            or cycle_length <= 0
            or len(cycle_df_for_index) < num_devices
        ):
            previous_gaps = None
            stable_streak = 0
            continue

        times = cycle_df_for_index["detection_time"].to_numpy(dtype=np.float64)
        phases = 2.0 * math.pi * ((times - float(cycle_starts[result_index])) / cycle_length)
        phases = np.mod(phases, 2.0 * math.pi)
        phases.sort()
        gaps = np.concatenate(
            [
                np.diff(phases),
                np.array([(phases[0] + 2.0 * math.pi) - phases[-1]], dtype=np.float64),
            ]
        )
        checked_cycles += 1

        if previous_gaps is None or len(previous_gaps) != len(gaps):
            previous_gaps = gaps
            stable_streak = 0
            continue

        gap_changes = np.abs(gaps - previous_gaps)
        max_change = float(np.max(gap_changes))
        if bool(np.all(gap_changes <= phase_gap_change_threshold)):
            stable_streak += 1
        else:
            stable_streak = 0
        previous_gaps = gaps

        if stable_streak >= stable_cycle_count:
            convergence_cycle = int(cycle_index)
            convergence_max_change = max_change
            break

    return {
        "coupling_function": coupling_function,
        "coupling_strength": coupling_strength,
        "converged": convergence_cycle is not None,
        "convergence_cycle": convergence_cycle,
        "checked_cycle_count": checked_cycles,
        "max_gap_change_at_convergence": convergence_max_change,
    }


def _save_run_phase_gap_error_from_raw_sqlite(
    *,
    db_path: Path,
    raw_db_path: Path,
    run_id: str,
    aggregate_set_id: str,
    target_cycle_mode: str,
    target_cycle_index: object | None,
) -> None:
    result = _compute_run_phase_gap_error_from_raw_sqlite(
        raw_db_path=raw_db_path,
        run_id=run_id,
        target_cycle_mode=target_cycle_mode,
        target_cycle_index=target_cycle_index,
    )
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO run_phase_gap_error_points
                (aggregate_set_id, run_id, coupling_function, coupling_strength,
                 target_cycle_mode, target_cycle_index, selected_cycle_index,
                 phase_gap_error, phase_gap_error_ratio, has_value)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aggregate_set_id,
                run_id,
                result["coupling_function"],
                float(result["coupling_strength"]),
                str(target_cycle_mode),
                None if target_cycle_index is None else int(target_cycle_index),
                result["selected_cycle_index"],
                result["phase_gap_error"],
                result["phase_gap_error_ratio"],
                int(result["has_value"]),
            ),
        )
    _append_history(
        db_path,
        "run_phase_gap_error_saved",
        {
            "run_id": run_id,
            "selected_cycle_index": result["selected_cycle_index"],
            "phase_gap_error": result["phase_gap_error"],
        },
    )


def _compute_run_phase_gap_error_from_raw_sqlite(
    *,
    raw_db_path: Path,
    run_id: str,
    target_cycle_mode: str,
    target_cycle_index: object | None,
) -> dict[str, Any]:
    raw_conn = sqlite_runs.connect(raw_db_path)
    try:
        raw_conn.row_factory = sqlite3.Row
        sqlite_runs.initialize(raw_conn)
        metadata_row = raw_conn.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if metadata_row is None:
            raise ValueError(f"raw run not found in {raw_db_path}: {run_id}")
        coupling_function = str(metadata_row["coupling_function"])
        coupling_strength = float(metadata_row["coupling_strength"])

        if target_cycle_mode == "cycle_index":
            selected_row = raw_conn.execute(
                """
                SELECT cycle_index, mean_abs_diff_from_ideal_phase_gap,
                       mean_abs_diff_from_ideal_phase_gap_ratio
                FROM phase_gap_error
                WHERE run_id = ?
                  AND cycle_index = ?
                LIMIT 1
                """,
                (run_id, int(target_cycle_index or 0)),
            ).fetchone()
        else:
            selected_row = raw_conn.execute(
                """
                SELECT cycle_index, mean_abs_diff_from_ideal_phase_gap,
                       mean_abs_diff_from_ideal_phase_gap_ratio
                FROM phase_gap_error
                WHERE run_id = ?
                  AND mean_abs_diff_from_ideal_phase_gap IS NOT NULL
                ORDER BY cycle_index DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
    finally:
        raw_conn.close()

    if selected_row is None or selected_row["mean_abs_diff_from_ideal_phase_gap"] is None:
        return {
            "coupling_function": coupling_function,
            "coupling_strength": coupling_strength,
            "selected_cycle_index": None if target_cycle_mode != "cycle_index" else int(target_cycle_index or 0),
            "phase_gap_error": None,
            "phase_gap_error_ratio": None,
            "has_value": False,
        }

    return {
        "coupling_function": coupling_function,
        "coupling_strength": coupling_strength,
        "selected_cycle_index": int(selected_row["cycle_index"]),
        "phase_gap_error": float(selected_row["mean_abs_diff_from_ideal_phase_gap"]),
        "phase_gap_error_ratio": (
            None
            if selected_row["mean_abs_diff_from_ideal_phase_gap_ratio"] is None
            else float(selected_row["mean_abs_diff_from_ideal_phase_gap_ratio"])
        ),
        "has_value": True,
    }


def _cycle_interval_lengths(cycle_starts: np.ndarray) -> np.ndarray:
    lengths = np.full(len(cycle_starts), np.nan, dtype=np.float64)
    if len(cycle_starts) < 2:
        return lengths
    lengths[:-1] = np.diff(cycle_starts)
    lengths[-1] = lengths[-2]
    return lengths


def _publish_paper_results(
    *,
    graph_dir: Path,
    graph_type: str,
    aggregate_set_id: str,
    aggregate_db_path: Path,
    output_path: Path,
) -> dict[str, str]:
    results_dir = PAPER_RESULTS_ROOT / _safe_filename(graph_type) / _safe_filename(graph_dir.name)
    results_dir.mkdir(parents=True, exist_ok=True)

    csv_path = results_dir / PAPER_RESULTS_CSV_NAME
    pdf_path = results_dir / output_path.name

    df = _paper_results_frame(
        graph_type=graph_type,
        db_path=aggregate_db_path,
        aggregate_set_id=aggregate_set_id,
    )
    df.to_csv(csv_path, index=False)
    shutil.copy2(output_path, pdf_path)

    _append_history(
        aggregate_db_path,
        "paper_results_exported",
        {
            "csv": _relative_to_project(csv_path),
            "pdf": _relative_to_project(pdf_path),
        },
    )
    return {
        "paper_results_csv": _relative_to_project(csv_path),
        "paper_results_pdf": _relative_to_project(pdf_path),
        "paper_results_dir": _relative_to_project(results_dir),
    }


def _paper_results_frame(
    *,
    graph_type: str,
    db_path: Path,
    aggregate_set_id: str,
) -> pd.DataFrame:
    if graph_type == "interval_per_vs_k":
        return _interval_per_paper_results_frame(db_path, aggregate_set_id)
    if graph_type == "convergence_cycle_vs_k":
        return _convergence_paper_results_frame(db_path, aggregate_set_id)
    if graph_type == "phase_gap_error_vs_k":
        return _phase_gap_error_paper_results_frame(db_path, aggregate_set_id)
    raise ValueError(f"Unsupported graph_type for paper results export: {graph_type}")


def _interval_per_paper_results_frame(db_path: Path, aggregate_set_id: str) -> pd.DataFrame:
    with _connect(db_path) as conn:
        df = pd.read_sql_query(
            """
            SELECT aggregate_set_id, coupling_function, coupling_strength,
                   interval_start_ms, interval_end_ms, interval_cycle_count,
                   expected_packets, actual_packets, per_percent
            FROM run_interval_per
            WHERE aggregate_set_id = ?
            """,
            conn,
            params=(aggregate_set_id,),
        )
    if df.empty:
        return pd.DataFrame(
            columns=[
                "aggregate_set_id",
                "coupling_function",
                "coupling_strength",
                "interval_start_ms",
                "interval_end_ms",
                "per_percent_mean",
                "per_percent_median",
                "per_percent_q1",
                "per_percent_q3",
                "per_percent_std",
                "per_percent_min",
                "per_percent_max",
                "expected_packets_sum",
                "actual_packets_sum",
                "interval_cycle_count_mean",
                "count",
            ]
        )
    return (
        df.groupby(["aggregate_set_id", "coupling_function", "coupling_strength"], as_index=False)
        .agg(
            interval_start_ms=("interval_start_ms", "first"),
            interval_end_ms=("interval_end_ms", "first"),
            per_percent_mean=("per_percent", "mean"),
            per_percent_median=("per_percent", "median"),
            per_percent_q1=("per_percent", lambda s: s.quantile(0.25)),
            per_percent_q3=("per_percent", lambda s: s.quantile(0.75)),
            per_percent_std=("per_percent", "std"),
            per_percent_min=("per_percent", "min"),
            per_percent_max=("per_percent", "max"),
            expected_packets_sum=("expected_packets", "sum"),
            actual_packets_sum=("actual_packets", "sum"),
            interval_cycle_count_mean=("interval_cycle_count", "mean"),
            count=("per_percent", "size"),
        )
        .sort_values(["coupling_function", "coupling_strength"])
        .reset_index(drop=True)
    )


def _convergence_paper_results_frame(db_path: Path, aggregate_set_id: str) -> pd.DataFrame:
    with _connect(db_path) as conn:
        df = pd.read_sql_query(
            """
            SELECT aggregate_set_id, coupling_function, coupling_strength,
                   stable_cycle_count, phase_gap_change_threshold,
                   convergence_cycle, converged
            FROM run_convergence_cycles
            WHERE aggregate_set_id = ?
            """,
            conn,
            params=(aggregate_set_id,),
        )
    columns = [
        "aggregate_set_id",
        "coupling_function",
        "coupling_strength",
        "stable_cycle_count",
        "phase_gap_change_threshold",
        "convergence_cycle_mean",
        "convergence_cycle_median",
        "convergence_cycle_q1",
        "convergence_cycle_q3",
        "convergence_cycle_std",
        "convergence_cycle_min",
        "convergence_cycle_max",
        "convergence_rate_percent",
        "count",
        "converged_count",
    ]
    if df.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, Any]] = []
    for (set_id, coupling_function, coupling_strength), group in df.groupby(
        ["aggregate_set_id", "coupling_function", "coupling_strength"],
        sort=True,
    ):
        converged = group[group["converged"].astype(bool)]
        cycles = pd.to_numeric(converged["convergence_cycle"], errors="coerce").dropna()
        rows.append(
            {
                "aggregate_set_id": set_id,
                "coupling_function": coupling_function,
                "coupling_strength": float(coupling_strength),
                "stable_cycle_count": int(group["stable_cycle_count"].iloc[0]),
                "phase_gap_change_threshold": float(group["phase_gap_change_threshold"].iloc[0]),
                "convergence_cycle_mean": _series_stat(cycles, "mean"),
                "convergence_cycle_median": _series_stat(cycles, "median"),
                "convergence_cycle_q1": _series_quantile(cycles, 0.25),
                "convergence_cycle_q3": _series_quantile(cycles, 0.75),
                "convergence_cycle_std": _series_stat(cycles, "std"),
                "convergence_cycle_min": _series_stat(cycles, "min"),
                "convergence_cycle_max": _series_stat(cycles, "max"),
                "convergence_rate_percent": float(100.0 * len(converged) / len(group)),
                "count": int(len(group)),
                "converged_count": int(len(converged)),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _phase_gap_error_paper_results_frame(db_path: Path, aggregate_set_id: str) -> pd.DataFrame:
    with _connect(db_path) as conn:
        df = pd.read_sql_query(
            """
            SELECT aggregate_set_id, coupling_function, coupling_strength,
                   target_cycle_mode, target_cycle_index, selected_cycle_index,
                   phase_gap_error, phase_gap_error_ratio, has_value
            FROM run_phase_gap_error_points
            WHERE aggregate_set_id = ?
            """,
            conn,
            params=(aggregate_set_id,),
        )
    columns = [
        "aggregate_set_id",
        "coupling_function",
        "coupling_strength",
        "target_cycle_mode",
        "target_cycle_index",
        "selected_cycle_index_median",
        "phase_gap_error_mean",
        "phase_gap_error_median",
        "phase_gap_error_q1",
        "phase_gap_error_q3",
        "phase_gap_error_std",
        "phase_gap_error_min",
        "phase_gap_error_max",
        "phase_gap_error_ratio_mean",
        "phase_gap_error_ratio_median",
        "valid_count",
        "count",
    ]
    if df.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, Any]] = []
    for (set_id, coupling_function, coupling_strength), group in df.groupby(
        ["aggregate_set_id", "coupling_function", "coupling_strength"],
        sort=True,
    ):
        valid = group[group["has_value"].astype(bool)]
        errors = pd.to_numeric(valid["phase_gap_error"], errors="coerce").dropna()
        ratios = pd.to_numeric(valid["phase_gap_error_ratio"], errors="coerce").dropna()
        selected_cycles = pd.to_numeric(valid["selected_cycle_index"], errors="coerce").dropna()
        rows.append(
            {
                "aggregate_set_id": set_id,
                "coupling_function": coupling_function,
                "coupling_strength": float(coupling_strength),
                "target_cycle_mode": str(group["target_cycle_mode"].iloc[0]),
                "target_cycle_index": group["target_cycle_index"].iloc[0],
                "selected_cycle_index_median": _series_stat(selected_cycles, "median"),
                "phase_gap_error_mean": _series_stat(errors, "mean"),
                "phase_gap_error_median": _series_stat(errors, "median"),
                "phase_gap_error_q1": _series_quantile(errors, 0.25),
                "phase_gap_error_q3": _series_quantile(errors, 0.75),
                "phase_gap_error_std": _series_stat(errors, "std"),
                "phase_gap_error_min": _series_stat(errors, "min"),
                "phase_gap_error_max": _series_stat(errors, "max"),
                "phase_gap_error_ratio_mean": _series_stat(ratios, "mean"),
                "phase_gap_error_ratio_median": _series_stat(ratios, "median"),
                "valid_count": int(len(errors)),
                "count": int(len(group)),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _series_stat(series: pd.Series, name: str) -> float | None:
    if series.empty:
        return None
    value = getattr(series, name)()
    return _nullable_float(value)


def _series_quantile(series: pd.Series, q: float) -> float | None:
    if series.empty:
        return None
    return _nullable_float(series.quantile(q))


def _save_output(
    db_path: Path,
    aggregate_set_id: str,
    output_path: Path,
    graph_dir: Path,
) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM outputs WHERE output_type = ?", ("representative_pdf",))
        conn.execute(
            """
            INSERT INTO outputs
                (output_id, aggregate_set_id, output_type, relative_path, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "representative_pdf",
                aggregate_set_id,
                "representative_pdf",
                _relative_to_graph(graph_dir, output_path),
                utc_now_iso(),
            ),
        )


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    return conn


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_history(db_path: Path, event_type: str, detail: dict[str, Any]) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO history (event_type, detail_json, created_at)
            VALUES (?, ?, ?)
            """,
            (event_type, json.dumps(detail, ensure_ascii=False), utc_now_iso()),
        )


def _cancel_requested(status_path: Path) -> bool:
    try:
        status = _read_json(status_path)
    except (OSError, json.JSONDecodeError):
        return False
    return bool(status.get("cancel_requested")) or str(status.get("status")) == "cancel_requested"


def _raise_if_cancel_requested(status_path: Path, db_path: Path) -> None:
    if _cancel_requested(status_path):
        _append_history(db_path, "job_cancelled_before_next_run", {})
        raise JobCancelled("Job cancelled before starting next run")


def _delete_raw_run(raw_db_path: Path, run_id: str) -> None:
    conn = sqlite_runs.connect(raw_db_path)
    try:
        sqlite_runs.initialize(conn)
        sqlite_runs.delete_run(conn, run_id)
    finally:
        conn.close()


def _aggregate_set_id(interval_start_ms: float, interval_end_ms: float) -> str:
    return f"interval_{int(interval_start_ms)}_to_{int(interval_end_ms)}"


def _interval_per_db_path(graph_dir: Path, fallback_db_path: Path) -> Path:
    db_path = graph_dir / INTERVAL_PER_DB_NAME
    return db_path if db_path.exists() else fallback_db_path


def _relative_to_graph(graph_dir: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(graph_dir.resolve()))
    except ValueError:
        return str(path)


def _relative_to_project(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def _safe_filename(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value) or "graph"


def _nullable_float(value: Any) -> float | None:
    if value is None:
        return None
    result = float(value)
    if math.isnan(result):
        return None
    return result


def _optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"auto", "none", "null"}:
        return None
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Unsupported optional bool value: {value}")
