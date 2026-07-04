from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import traceback
from typing import Any
import uuid

import pandas as pd

from research_program.config.paths import resolve_project_path
from research_program.simulation.jobs import (
    simulation_request_from_dict,
    simulation_request_to_dict,
)
from research_program.simulation.runner import SimulationRequest, run_simulation_request


GRAPH_RUN_ROOT = Path("outputs/graph_runs/interval_per_vs_k")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _load_json(path: Path) -> dict[str, Any]:
    last_error: OSError | json.JSONDecodeError | None = None
    for attempt in range(5):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.02 * (attempt + 1))
    if last_error is not None:
        raise last_error
    return {}


def _graph_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def _sweep_label(value: int | float | str) -> str:
    return str(value).replace("-", "m").replace(".", "p")


def build_interval_per_vs_k_requests(
    *,
    base_request: SimulationRequest,
    graph_id: str,
    graph_runs_dir: Path,
    coupling_functions: list[str],
    coupling_strengths: list[int],
) -> list[SimulationRequest]:
    requests: list[SimulationRequest] = []
    for coupling_function in coupling_functions:
        for coupling_strength in coupling_strengths:
            tags = (
                *base_request.tags,
                "graph_first",
                f"graph_id_{graph_id}",
                "graph_interval_per_vs_k",
                f"coupling_function_{_sweep_label(coupling_function)}",
                f"coupling_strength_{_sweep_label(coupling_strength)}",
            )
            requests.append(
                replace(
                    base_request,
                    coupling_function=coupling_function,
                    coupling_strength=int(coupling_strength),
                    output_root=graph_runs_dir,
                    tags=tags,
                )
            )
    return requests


def create_interval_per_vs_k_job(
    *,
    base_request: SimulationRequest,
    coupling_functions: list[str],
    coupling_strengths: list[int],
    interval_start_ms: float,
    interval_end_ms: float,
    plot_overrides: dict[str, Any] | None = None,
    job_root: str | Path = GRAPH_RUN_ROOT,
) -> tuple[str, Path, Path]:
    if not coupling_functions:
        raise ValueError("Select at least one coupling function")
    if not coupling_strengths:
        raise ValueError("Select at least one coupling strength")
    if interval_end_ms <= interval_start_ms:
        raise ValueError("Interval end must be larger than interval start")

    resolved_root = resolve_project_path(job_root)
    job_id = _graph_id()
    graph_dir = resolved_root / job_id
    runs_dir = graph_dir / "runs"
    figures_dir = graph_dir / "figures"
    status_path = graph_dir / "status.json"
    requests = build_interval_per_vs_k_requests(
        base_request=base_request,
        graph_id=job_id,
        graph_runs_dir=runs_dir,
        coupling_functions=coupling_functions,
        coupling_strengths=coupling_strengths,
    )
    graph_dir.mkdir(parents=True, exist_ok=False)
    runs_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "job_id": job_id,
        "status": "queued",
        "pid": None,
        "created_at": _now_iso(),
        "started_at": None,
        "updated_at": _now_iso(),
        "finished_at": None,
        "graph_type": "interval_per_vs_k_by_coupling_function",
        "graph_dir": str(graph_dir),
        "runs_dir": str(runs_dir),
        "figures_dir": str(figures_dir),
        "interval_start_ms": float(interval_start_ms),
        "interval_end_ms": float(interval_end_ms),
        "coupling_functions": list(coupling_functions),
        "coupling_strengths": [int(value) for value in coupling_strengths],
        "plot_overrides": dict(plot_overrides or {}),
        "total_conditions": len(requests),
        "current_condition": 0,
        "total_runs": sum(request.num_runs for request in requests),
        "completed_runs": 0,
        "current_run_id": "",
        "error": "",
        "requests": [simulation_request_to_dict(request) for request in requests],
        "results": [],
        "outputs": {},
    }
    _atomic_write_json(status_path, payload)
    _atomic_write_json(graph_dir / "manifest.json", payload)
    _atomic_write_json(graph_dir / "requests.json", {"requests": payload["requests"]})
    return job_id, graph_dir, status_path


def load_interval_per_vs_k_status(path: str | Path) -> dict[str, Any]:
    return _load_json(Path(path))


def load_interval_per_vs_k_statuses(
    job_root: str | Path = GRAPH_RUN_ROOT,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    root = resolve_project_path(job_root)
    if not root.exists():
        return []
    statuses: list[dict[str, Any]] = []
    for path in root.glob("*/status.json"):
        try:
            status = load_interval_per_vs_k_status(path)
        except (OSError, json.JSONDecodeError, PermissionError):
            continue
        status["_path"] = str(path)
        statuses.append(status)
    statuses.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return statuses[:limit] if limit is not None else statuses


def _clean_plot_overrides(values: dict[str, Any]) -> dict[str, Any]:
    excluded_fields = {
        "results_dir",
        "graphs_dir",
        "interval_start_ms",
        "interval_end_ms",
        "target_coupling_functions",
        "coupling_strength_min",
        "coupling_strength_max",
        "use_existing_csv_if_available",
    }
    return {
        key: value
        for key, value in values.items()
        if value is not None and key not in excluded_fields
    }


def _write_frame(df: pd.DataFrame, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return str(path)


def _render_interval_per_vs_k_outputs(payload: dict[str, Any]) -> dict[str, Any]:
    import research_program.plotting.plot_per_by_coupling_strength_interval as plot_module

    runs_dir = Path(str(payload["runs_dir"]))
    figures_dir = Path(str(payload["figures_dir"]))
    interval_start_ms = float(payload["interval_start_ms"])
    interval_end_ms = float(payload["interval_end_ms"])
    plot_overrides = _clean_plot_overrides(dict(payload.get("plot_overrides") or {}))

    plot_module.CFG = replace(
        plot_module.CFG,
        results_dir=runs_dir,
        graphs_dir=figures_dir,
        interval_start_ms=interval_start_ms,
        interval_end_ms=interval_end_ms,
        target_coupling_functions=tuple(),
        coupling_strength_min=None,
        coupling_strength_max=None,
        use_existing_csv_if_available=False,
        **plot_overrides,
    )
    rows = [
        result
        for run_dir in sorted(path for path in runs_dir.iterdir() if path.is_dir())
        if (result := plot_module.process_run(run_dir)) is not None
    ]
    raw_df = pd.DataFrame(rows) if rows else plot_module._empty_raw_frame()
    agg_df = plot_module.aggregate_results(raw_df)
    csv_paths = plot_module.save_graph_data_csvs(agg_df, figures_dir)
    plot_paths = plot_module.save_plots(agg_df, figures_dir)
    raw_path = figures_dir / "raw_interval_per_results.csv"
    agg_path = figures_dir / "aggregated_interval_per_results.csv"
    _write_frame(raw_df, raw_path)
    _write_frame(agg_df, agg_path)
    return {
        "raw_results_csv": str(raw_path),
        "aggregated_results_csv": str(agg_path),
        "per_graph_csvs": [str(path) for path in csv_paths],
        "pdfs": [str(path) for path in plot_paths],
    }


def run_interval_per_vs_k_job_file(status_path: str | Path) -> int:
    path = Path(status_path)
    payload = load_interval_per_vs_k_status(path)
    requests = [simulation_request_from_dict(item) for item in payload.get("requests", [])]
    total_runs = sum(request.num_runs for request in requests)
    results: list[dict[str, Any]] = []
    completed_runs = 0
    started_at = payload.get("started_at") or _now_iso()
    payload.update(
        {
            "status": "running_simulations",
            "pid": os.getpid(),
            "started_at": started_at,
            "updated_at": started_at,
            "total_conditions": len(requests),
            "total_runs": total_runs,
            "completed_runs": 0,
            "current_condition": 0,
            "current_run_id": "",
            "error": "",
            "results": [],
        }
    )
    _atomic_write_json(path, payload)

    try:
        for condition_index, request in enumerate(requests, start=1):
            payload["current_condition"] = condition_index
            payload["updated_at"] = _now_iso()
            _atomic_write_json(path, payload)

            def on_progress(completed: int, total: int, result: dict[str, Any]) -> None:
                nonlocal completed_runs
                completed_runs += 1
                result_row = {
                    "condition_index": condition_index,
                    "condition_count": len(requests),
                    **result,
                }
                results.append(result_row)
                payload.update(
                    {
                        "status": "running_simulations",
                        "completed_runs": completed_runs,
                        "current_run_id": str(result.get("run_id", "")),
                        "updated_at": _now_iso(),
                        "results": results,
                    }
                )
                _atomic_write_json(path, payload)

            run_simulation_request(request, progress_callback=on_progress)

        payload.update(
            {
                "status": "rendering_graph",
                "current_run_id": "",
                "updated_at": _now_iso(),
                "results": results,
            }
        )
        _atomic_write_json(path, payload)
        outputs = _render_interval_per_vs_k_outputs(payload)
        finished_at = _now_iso()
        payload.update(
            {
                "status": "completed",
                "updated_at": finished_at,
                "finished_at": finished_at,
                "outputs": outputs,
                "results": results,
            }
        )
        _atomic_write_json(path, payload)
        _atomic_write_json(Path(str(payload["graph_dir"])) / "manifest.json", payload)
        return 0
    except Exception as exc:
        finished_at = _now_iso()
        payload.update(
            {
                "status": "failed",
                "updated_at": finished_at,
                "finished_at": finished_at,
                "error": f"{exc}\n{traceback.format_exc()}",
                "results": results,
            }
        )
        _atomic_write_json(path, payload)
        _atomic_write_json(Path(str(payload["graph_dir"])) / "manifest.json", payload)
        return 1


def redraw_interval_per_vs_k_graph(
    status_path: str | Path,
    *,
    interval_start_ms: float | None = None,
    interval_end_ms: float | None = None,
    plot_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = Path(status_path)
    payload = load_interval_per_vs_k_status(path)
    if interval_start_ms is not None:
        payload["interval_start_ms"] = float(interval_start_ms)
    if interval_end_ms is not None:
        payload["interval_end_ms"] = float(interval_end_ms)
    if float(payload["interval_end_ms"]) <= float(payload["interval_start_ms"]):
        raise ValueError("Interval end must be larger than interval start")
    if plot_overrides:
        payload["plot_overrides"] = {
            **dict(payload.get("plot_overrides") or {}),
            **dict(plot_overrides),
        }

    payload.update(
        {
            "status": "rendering_graph",
            "updated_at": _now_iso(),
            "error": "",
        }
    )
    _atomic_write_json(path, payload)
    outputs = _render_interval_per_vs_k_outputs(payload)
    finished_at = _now_iso()
    payload.update(
        {
            "status": "completed",
            "updated_at": finished_at,
            "finished_at": finished_at,
            "outputs": outputs,
        }
    )
    _atomic_write_json(path, payload)
    _atomic_write_json(Path(str(payload["graph_dir"])) / "manifest.json", payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m research_program.pipelines.interval_per_vs_k <status-json>", file=sys.stderr)
        return 2
    return run_interval_per_vs_k_job_file(args[0])


if __name__ == "__main__":
    raise SystemExit(main())
