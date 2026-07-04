from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from research_program.analysis.calculate_cycle_data import ensure_cycle_data_for_run
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

from .storage import load_graph_job, utc_now_iso


def available_coupling_functions() -> list[str]:
    return [item.value for item in CouplingFunction]


def run_interval_per_vs_k_job(graph_dir: Path) -> dict[str, Any]:
    graph_dir = Path(graph_dir)
    manifest_path = graph_dir / "manifest.json"
    status_path = graph_dir / "status.json"
    db_path = graph_dir / "graph_data.sqlite"
    raw_runs_dir = graph_dir / "raw" / "runs"
    figures_dir = graph_dir / "figures"

    manifest = _read_json(manifest_path)
    status = _read_json(status_path)
    params = dict(manifest.get("input") or {})
    graph_key = dict(manifest.get("graph_key") or {})

    raw_runs_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    k_values = [float(value) for value in params.get("k_values", [])]
    runs_per_k = int(params.get("runs_per_k", 1))
    total_runs = len(k_values) * runs_per_k
    aggregate_set_id = _aggregate_set_id(
        float(params["interval_start_ms"]),
        float(params["interval_end_ms"]),
    )

    status.update(
        {
            "status": "running_simulations",
            "started_at": status.get("started_at") or utc_now_iso(),
            "updated_at": utc_now_iso(),
            "total_runs": total_runs,
            "completed_runs": 0,
            "current_run_id": "",
            "error": "",
        }
    )
    _write_json(status_path, status)

    completed = 0
    try:
        for k_value in k_values:
            request = _simulation_request_for_k(
                graph_id=str(manifest["graph_id"]),
                graph_key=graph_key,
                params=params,
                k_value=k_value,
                output_root=raw_runs_dir,
            )

            def on_progress(done: int, total: int, result: dict[str, Any]) -> None:
                nonlocal completed
                completed += 1
                run_id = str(result["run_id"])
                _save_run_record(
                    db_path=db_path,
                    request_id=str(manifest["graph_id"]),
                    run_id=run_id,
                    coupling_strength=k_value,
                    repeat_index=done - 1,
                    raw_path=_relative_to_graph(graph_dir, Path(result["output_dir"])),
                    metadata=result,
                )
                _save_run_intermediate_and_interval(
                    db_path=db_path,
                    graph_dir=graph_dir,
                    run_id=run_id,
                    run_dir=Path(result["output_dir"]),
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

        status.update(
            {
                "status": "running_analysis",
                "current_run_id": "",
                "updated_at": utc_now_iso(),
            }
        )
        _write_json(status_path, status)

        aggregate_rows = rebuild_interval_aggregate(db_path, aggregate_set_id)

        status.update({"status": "rendering_graph", "updated_at": utc_now_iso()})
        _write_json(status_path, status)

        output_path = render_interval_per_vs_k_pdf(
            graph_dir=graph_dir,
            db_path=db_path,
            aggregate_set_id=aggregate_set_id,
            coupling_function=str(graph_key.get("coupling_function", "")),
            interval_start_ms=float(params["interval_start_ms"]),
            interval_end_ms=float(params["interval_end_ms"]),
            plot_settings=params.get("plot_settings", {}),
        )
        _save_output(db_path, aggregate_set_id, output_path, graph_dir)

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
                "outputs": {"representative_pdf": _relative_to_graph(graph_dir, output_path)},
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
            {"aggregate_rows": aggregate_rows, "output": str(output_path)},
        )
    except Exception as exc:
        failed_at = utc_now_iso()
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


def render_interval_per_vs_k_pdf(
    *,
    graph_dir: Path,
    db_path: Path,
    aggregate_set_id: str,
    coupling_function: str,
    interval_start_ms: float,
    interval_end_ms: float,
    plot_settings: dict[str, Any] | None = None,
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

    plt.figure(figsize=(float(settings.get("figure_width", 8.0)), float(settings.get("figure_height", 5.0))))
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

    plt.xlabel("Coupling strength K", fontsize=int(settings.get("font_size_label", 12)))
    plt.ylabel("Interval PER [%]", fontsize=int(settings.get("font_size_label", 12)))
    if bool(settings.get("show_title", True)):
        plt.title(
            f"{coupling_function}: {interval_start_ms:g} to {interval_end_ms:g} ms",
            fontsize=int(settings.get("font_size_title", 12)),
        )
    plt.xticks(fontsize=int(settings.get("font_size_ticks", 10)))
    plt.yticks(fontsize=int(settings.get("font_size_ticks", 10)))
    plt.grid(bool(settings.get("show_grid", True)))
    plt.tight_layout()
    plt.savefig(output_path, dpi=int(settings.get("save_dpi", 300)))
    plt.close()
    _append_history(db_path, "pdf_rendered", {"output": _relative_to_graph(graph_dir, output_path)})
    return output_path


def _simulation_request_for_k(
    *,
    graph_id: str,
    graph_key: dict[str, Any],
    params: dict[str, Any],
    k_value: float,
    output_root: Path,
) -> SimulationRequest:
    base = dict(params.get("simulation_base") or {})
    coupling_function = str(graph_key["coupling_function"])
    return SimulationRequest(
        num_runs=int(params.get("runs_per_k", 1)),
        seed=int(base.get("seed", 1)) + int(round(k_value * 1000)),
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
        simulation_mode=str(base.get("simulation_mode", "standard")),  # type: ignore[arg-type]
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
    conn = sqlite3.connect(db_path)
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


def _aggregate_set_id(interval_start_ms: float, interval_end_ms: float) -> str:
    return f"interval_{int(interval_start_ms)}_to_{int(interval_end_ms)}"


def _relative_to_graph(graph_dir: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(graph_dir.resolve()))
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
