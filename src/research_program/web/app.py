from __future__ import annotations

import ctypes
import base64
import hashlib
import importlib
import json
import math
import os
import platform
import shutil
import sqlite3
from contextlib import closing
from io import BytesIO
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st

from research_program.graph_workflow.execution import (
    available_coupling_functions,
    render_interval_per_vs_k_pdf,
    run_interval_per_vs_k_job,
)
from research_program.graph_workflow.storage import (
    RAW_RUN_DB_NAME,
    create_interval_per_vs_k_job,
    delete_graph_job,
    get_storage_overview,
    list_graph_jobs,
    request_cancel_graph_job,
)
from research_program.simulation.lora_airtime import (
    LoRaAirtimeConfig,
    calculate_lora_airtime_ms,
    resolve_low_data_rate_optimize,
)
from research_program.simulation import coupling_functions


st.set_page_config(page_title="Research Program", page_icon="RP", layout="wide")

LAST_INTERVAL_PER_VS_K_PARAMS_PATH = (
    Path("outputs") / "settings" / "last_interval_per_vs_k_params.json"
)
COUPLING_FUNCTION_VIEW_SETTINGS_PATH = (
    Path("outputs") / "settings" / "coupling_function_view_settings.json"
)

DEFAULT_INTERVAL_PER_VS_K_PARAMS: dict[str, Any] = {
    "coupling_function": "KURAMOTO",
    "k_start": 0.0,
    "k_stop": 20.0,
    "k_step": 5.0,
    "runs_per_k": 10,
    "interval_start_ms": 0.0,
    "interval_end_ms": 2_000_000.0,
    "per_method": "interval_packet_error_rate",
    "plot_settings": {
        "xlim_min": None,
        "xlim_max": None,
        "ylim_min": 0.0,
        "ylim_max": 100.0,
        "figure_width": 8.0,
        "figure_height": 5.0,
        "font_size_label": 12,
        "font_size_ticks": 10,
        "font_size_title": 12,
        "marker": "o",
        "marker_size": 6.0,
        "line_style": "-",
        "line_width": 1.5,
        "show_error_bars": True,
        "error_bar_capsize": 4.0,
        "show_title": True,
        "show_grid": True,
        "show_min_annotation": False,
        "min_annotation_font_size": 10,
        "min_annotation_x_offset": 10.0,
        "min_annotation_y_offset": 10.0,
        "save_dpi": 300,
    },
    "simulation_base": {
        "duration_ms": 2_000_000.0,
        "seed": 1,
        "device_count": 20,
        "cycle_time": 30_000,
        "initial_phase_start_percent": 0.0,
        "initial_phase_end_percent": 100.0,
        "listening_rate": 25,
        "strength_ratio": -0.0001,
        "max_workers": 1,
        "simulation_mode": "per_measurement",
        "carrier_sense_duration_ms": 0.0,
        "lora_payload_bytes": 16,
        "lora_spreading_factor": 7,
        "lora_bandwidth_hz": 125_000,
        "lora_coding_rate_denominator": 5,
        "lora_preamble_symbols": 8,
        "lora_explicit_header": True,
        "lora_crc_enabled": True,
        "lora_low_data_rate_optimize": "auto",
    },
}

DEFAULT_COUPLING_FUNCTION_VIEW_SETTINGS: dict[str, Any] = {
    "selected_functions": [],
    "sample_count": 1001,
    "marker_size": 10.0,
    "show_zero_lines": True,
    "show_grid": True,
    "x_label": r"\theta_j-\theta_i",
    "y_label": "Coupling function value",
    "axis_label_font_size": 12,
    "tick_font_size": 10,
    "legend_font_size": 10,
    "title_font_size": 12,
    "figure_width": 8.0,
    "figure_height": 4.8,
    "show_title": True,
    "show_legend": True,
    "legend_location": "best",
    "legend_labels": {},
    "y_auto": True,
    "y_min": -2.0,
    "y_max": 2.0,
}

RUNNING_STATUSES = {"running_simulations", "running_analysis", "rendering_graph", "cancel_requested"}


def main() -> None:
    st.title("Research Program")
    st.caption("Graph-first Web GUI")

    page = st.sidebar.radio(
        "Page",
        ["ジョブ追加", "ジョブ確認", "結果・グラフ確認", "結合関数確認", "その他管理"],
        label_visibility="collapsed",
    )

    if page == "ジョブ追加":
        render_job_add_page()
    elif page == "ジョブ確認":
        render_job_status_page()
    elif page == "結果・グラフ確認":
        render_results_page()
    elif page == "結合関数確認":
        render_coupling_function_page()
    else:
        render_management_page()


def render_job_add_page() -> None:
    st.header("ジョブ追加")
    st.caption("Interval PER vs K は 1 job = 1 coupling function = 1 graph folder で作成します。")

    saved_params = load_last_interval_per_vs_k_params()
    saved_base = dict(saved_params.get("simulation_base") or {})
    saved_plot = dict(saved_params.get("plot_settings") or {})

    with st.form("interval_per_vs_k_job"):
        st.subheader("Graph target")
        col_left, col_right = st.columns(2)
        with col_left:
            graph_type = st.selectbox("graph type", ["Interval PER vs K"])
            coupling_options = available_coupling_functions()
            coupling_function = st.selectbox(
                "coupling function",
                coupling_options,
                index=select_index(coupling_options, str(saved_params.get("coupling_function", "KURAMOTO"))),
            )
            k_start = st.number_input("K start", value=float(saved_params.get("k_start", 0.0)), step=1.0)
            k_stop = st.number_input("K stop", value=float(saved_params.get("k_stop", 20.0)), step=1.0)
            k_step = st.number_input(
                "K step",
                value=float(saved_params.get("k_step", 5.0)),
                min_value=0.000001,
                step=1.0,
            )
            runs_per_k = st.number_input(
                "runs per K",
                min_value=1,
                value=int(saved_params.get("runs_per_k", 10)),
                step=1,
            )
        with col_right:
            interval_start_ms = duration_input_ms(
                "interval start",
                "interval_start",
                float(saved_params.get("interval_start_ms", 0.0)),
                min_value=0.0,
            )
            interval_end_ms = duration_input_ms(
                "interval end",
                "interval_end",
                float(saved_params.get("interval_end_ms", 2_000_000.0)),
                min_value=0.0,
            )
            simulation_duration_ms = duration_input_ms(
                "simulation duration",
                "simulation_duration",
                float(saved_base.get("duration_ms", 2_000_000.0)),
                min_value=1.0,
            )
            cycle_time = int(
                duration_input_ms(
                    "cycle time",
                    "cycle_time",
                    float(saved_base.get("cycle_time", 30_000)),
                    min_value=1.0,
                )
            )
            st.caption(
                "Initial timing for this graph: random 1 ms points in the selected cycle range, with replacement."
            )

        st.subheader("Simulation")
        sim_a, sim_b, sim_c = st.columns(3)
        with sim_a:
            seed = st.number_input("base seed", value=int(saved_base.get("seed", 1)), step=1)
            device_count = st.number_input(
                "device count",
                min_value=1,
                value=int(saved_base.get("device_count", 20)),
                step=1,
            )
            listening_rate = st.number_input(
                "listening rate",
                min_value=0,
                value=int(saved_base.get("listening_rate", 25)),
                step=1,
            )
        with sim_b:
            strength_ratio = st.number_input(
                "strength ratio",
                value=float(saved_base.get("strength_ratio", -0.0001)),
                step=0.0001,
                format="%.6f",
            )
            max_workers = st.number_input(
                "max workers",
                min_value=0,
                value=int(saved_base.get("max_workers", 1)),
                step=1,
            )
            simulation_mode = "per_measurement"
            st.text_input(
                "simulation mode",
                value=simulation_mode,
                disabled=True,
                help="Interval PER vs K uses LoRa airtime as transmission time.",
            )
        with sim_c:
            initial_phase_start_percent = st.number_input(
                "initial phase start %",
                min_value=0.0,
                max_value=100.0,
                value=float(saved_base.get("initial_phase_start_percent", 0.0)),
                step=1.0,
            )
            initial_phase_end_percent = st.number_input(
                "initial phase end %",
                min_value=0.0,
                max_value=100.0,
                value=float(saved_base.get("initial_phase_end_percent", 100.0)),
                step=1.0,
            )
            carrier_sense_duration_ms = duration_input_ms(
                "carrier sense duration",
                "carrier_sense_duration",
                float(saved_base.get("carrier_sense_duration_ms", 0.0)),
                min_value=0.0,
            )

        st.subheader("LoRa")
        lora_col_a, lora_col_b, lora_col_c = st.columns(3)
        with lora_col_a:
            lora_payload_bytes = st.number_input(
                "payload bytes",
                min_value=0,
                value=int(saved_base.get("lora_payload_bytes", 16)),
                step=1,
            )
            lora_spreading_factor = st.number_input(
                "spreading factor",
                min_value=5,
                max_value=12,
                value=int(saved_base.get("lora_spreading_factor", 7)),
                step=1,
            )
        with lora_col_b:
            lora_bandwidth_hz = st.number_input(
                "bandwidth Hz",
                min_value=1,
                value=int(saved_base.get("lora_bandwidth_hz", 125_000)),
                step=1000,
            )
            lora_coding_rate_denominator = st.number_input(
                "coding rate denominator",
                min_value=5,
                max_value=8,
                value=int(saved_base.get("lora_coding_rate_denominator", 5)),
                step=1,
            )
        with lora_col_c:
            lora_preamble_symbols = st.number_input(
                "preamble symbols",
                min_value=0,
                value=int(saved_base.get("lora_preamble_symbols", 8)),
                step=1,
            )
            lora_explicit_header = st.checkbox(
                "explicit header",
                value=bool(saved_base.get("lora_explicit_header", True)),
            )
            lora_crc_enabled = st.checkbox(
                "CRC enabled",
                value=bool(saved_base.get("lora_crc_enabled", True)),
            )
            lora_low_data_rate_optimize_mode = st.selectbox(
                "low data rate optimize",
                ["auto", "true", "false"],
                index=select_index(
                    ["auto", "true", "false"],
                    str(saved_base.get("lora_low_data_rate_optimize", "auto")),
                ),
            )

        plot_settings = render_plot_settings(
            saved_plot,
            key_prefix=f"job_add_plot_{plot_settings_key_fragment(saved_plot)}",
        )
        preview_clicked = st.form_submit_button("Preview airtime and run count")
        submitted = st.form_submit_button("Add job", type="primary")

    lora_config = build_lora_airtime_config(
        payload_bytes=int(lora_payload_bytes),
        spreading_factor=int(lora_spreading_factor),
        bandwidth_hz=int(lora_bandwidth_hz),
        coding_rate_denominator=int(lora_coding_rate_denominator),
        preamble_symbols=int(lora_preamble_symbols),
        explicit_header=bool(lora_explicit_header),
        crc_enabled=bool(lora_crc_enabled),
        low_data_rate_optimize_mode=str(lora_low_data_rate_optimize_mode),
    )
    k_values = build_k_values(k_start, k_stop, k_step)
    total_runs = len(k_values) * int(runs_per_k)

    if preview_clicked or submitted:
        render_job_preview(
            graph_type=graph_type,
            k_values=k_values,
            runs_per_k=int(runs_per_k),
            total_runs=total_runs,
            lora_config=lora_config,
            simulation_mode=str(simulation_mode),
            simulation_duration_ms=float(simulation_duration_ms),
            cycle_time_ms=float(cycle_time),
            device_count=int(device_count),
            interval_start_ms=float(interval_start_ms),
            interval_end_ms=float(interval_end_ms),
            initial_phase_start_percent=float(initial_phase_start_percent),
            initial_phase_end_percent=float(initial_phase_end_percent),
        )

    if not submitted:
        return
    if interval_end_ms <= interval_start_ms:
        st.error("interval end must be larger than interval start.")
        return
    if initial_phase_end_percent <= initial_phase_start_percent:
        st.error("initial phase end % must be larger than initial phase start %.")
        return
    if not k_values:
        st.error("K range did not produce any values.")
        return

    params = {
        "coupling_function": coupling_function,
        "k_start": float(k_start),
        "k_stop": float(k_stop),
        "k_step": float(k_step),
        "k_values": k_values,
        "runs_per_k": int(runs_per_k),
        "interval_start_ms": float(interval_start_ms),
        "interval_end_ms": float(interval_end_ms),
        "per_method": "interval_packet_error_rate",
        "plot_settings": plot_settings,
        "simulation_base": {
            "duration_ms": float(simulation_duration_ms),
            "seed": int(seed),
            "device_count": int(device_count),
            "cycle_time": int(cycle_time),
            "initial_phase_start_percent": float(initial_phase_start_percent),
            "initial_phase_end_percent": float(initial_phase_end_percent),
            "listening_rate": int(listening_rate),
            "strength_ratio": float(strength_ratio),
            "max_workers": int(max_workers),
            "simulation_mode": simulation_mode,
            "carrier_sense_duration_ms": float(carrier_sense_duration_ms),
            "lora_payload_bytes": int(lora_payload_bytes),
            "lora_spreading_factor": int(lora_spreading_factor),
            "lora_bandwidth_hz": int(lora_bandwidth_hz),
            "lora_coding_rate_denominator": int(lora_coding_rate_denominator),
            "lora_preamble_symbols": int(lora_preamble_symbols),
            "lora_explicit_header": bool(lora_explicit_header),
            "lora_crc_enabled": bool(lora_crc_enabled),
            "lora_low_data_rate_optimize": lora_low_data_rate_optimize_mode,
        },
    }
    save_last_interval_per_vs_k_params(params)
    job = create_interval_per_vs_k_job(params)
    st.success("Job added.")
    st.code(str(job.path), language="text")
    with st.spinner("Running simulations, saving raw_run.sqlite, aggregating, and rendering PDF..."):
        result = run_interval_per_vs_k_job(job.path)
    if result.get("output") is None:
        st.warning("Job was cancelled.")
    else:
        st.success("Job completed.")
        st.code(str(result["output"]), language="text")


def render_job_preview(
    *,
    graph_type: str,
    k_values: list[float],
    runs_per_k: int,
    total_runs: int,
    lora_config: LoRaAirtimeConfig,
    simulation_mode: str,
    simulation_duration_ms: float,
    cycle_time_ms: float,
    device_count: int,
    interval_start_ms: float,
    interval_end_ms: float,
    initial_phase_start_percent: float,
    initial_phase_end_percent: float,
) -> None:
    try:
        airtime_ms = calculate_lora_airtime_ms(lora_config)
        low_data_rate_optimize = resolve_low_data_rate_optimize(lora_config)
    except ValueError as exc:
        st.error(f"LoRa airtime could not be calculated: {exc}")
        return

    cols = st.columns(5)
    cols[0].metric("LoRa airtime", f"{airtime_ms:.3f} ms")
    cols[1].metric("symbol time", f"{symbol_duration_ms(lora_config):.3f} ms")
    cols[2].metric("LDRO", "on" if low_data_rate_optimize else "off")
    cols[3].metric("K points", len(k_values))
    cols[4].metric("total runs", total_runs)
    size_estimate = estimate_job_data_size(
        total_runs=total_runs,
        simulation_duration_ms=simulation_duration_ms,
        cycle_time_ms=cycle_time_ms,
        device_count=device_count,
    )
    size_cols = st.columns(4)
    size_cols[0].metric("estimated data size", format_bytes(size_estimate["total_bytes"]))
    size_cols[1].metric("estimated per run", format_bytes(size_estimate["bytes_per_run"]))
    size_cols[2].metric("estimate source", str(size_estimate["source"]))
    size_cols[3].metric("rough range", size_estimate["range_label"])
    st.info(
        f"{graph_type}: K={len(k_values)} points, runs per K={runs_per_k}, "
        f"total runs={total_runs}. Simulation duration={format_duration_ms(simulation_duration_ms)}, "
        f"interval={format_duration_ms(interval_start_ms)} to {format_duration_ms(interval_end_ms)}. "
        f"Initial phase range={format_percent_range(initial_phase_start_percent, initial_phase_end_percent)}. "
        "LoRa airtime is used as TX time."
    )


def estimate_job_data_size(
    *,
    total_runs: int,
    simulation_duration_ms: float,
    cycle_time_ms: float,
    device_count: int,
) -> dict[str, object]:
    empirical_bytes_per_run = empirical_interval_per_vs_k_bytes_per_run()
    if empirical_bytes_per_run is not None:
        bytes_per_run = empirical_bytes_per_run
        source = "existing jobs"
        range_factor_low = 0.7
        range_factor_high = 1.5
    else:
        cycle_count = max(1, int(math.ceil(simulation_duration_ms / max(cycle_time_ms, 1.0))))
        estimated_send_rows = cycle_count * max(device_count, 1)
        bytes_per_run = int(
            64_000
            + estimated_send_rows * 120
            + cycle_count * 260
            + max(device_count, 1) * 120
        )
        source = "rough formula"
        range_factor_low = 0.5
        range_factor_high = 2.0

    total_bytes = max(0, int(bytes_per_run * max(total_runs, 0) + 1_000_000))
    return {
        "bytes_per_run": int(bytes_per_run),
        "total_bytes": total_bytes,
        "source": source,
        "range_label": (
            f"{format_bytes(total_bytes * range_factor_low)} to "
            f"{format_bytes(total_bytes * range_factor_high)}"
        ),
    }


def empirical_interval_per_vs_k_bytes_per_run() -> int | None:
    root = Path("outputs") / "graph_runs" / "interval_per_vs_k"
    if not root.exists():
        return None
    samples: list[float] = []
    for graph_dir in sorted(root.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True):
        if not graph_dir.is_dir():
            continue
        db_path = graph_dir / "graph_data.sqlite"
        raw_db_path = graph_dir / RAW_RUN_DB_NAME
        if not db_path.exists() or not raw_db_path.exists():
            continue
        completed_runs = completed_run_count(db_path)
        if completed_runs <= 0:
            continue
        graph_bytes = sqlite_family_size(db_path)
        raw_bytes = sqlite_family_size(raw_db_path)
        figure_bytes = directory_file_size(graph_dir / "figures")
        samples.append((graph_bytes + raw_bytes + figure_bytes) / completed_runs)
        if len(samples) >= 10:
            break
    if not samples:
        return None
    samples.sort()
    return int(samples[len(samples) // 2])


def completed_run_count(db_path: Path) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            row = conn.execute("SELECT COUNT(*) FROM runs WHERE status = 'completed'").fetchone()
        except sqlite3.Error:
            return 0
    return int(row[0] if row else 0)


def sqlite_family_size(db_path: Path) -> int:
    return sum(
        path.stat().st_size
        for path in [db_path, db_path.with_name(f"{db_path.name}-wal"), db_path.with_name(f"{db_path.name}-shm")]
        if path.exists()
    )


def directory_file_size(directory: Path) -> int:
    if not directory.exists():
        return 0
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())


def render_job_status_page() -> None:
    st.header("ジョブ確認")
    if st.button("Refresh status"):
        st.rerun()

    jobs = list_graph_jobs()
    if not jobs:
        st.info("No jobs yet.")
        return

    for job in jobs:
        with st.container(border=True):
            cols = st.columns([2, 1, 1, 1])
            cols[0].markdown(f"**{job.graph_id}**")
            cols[0].caption(format_graph_key(job.graph_key))
            cols[1].metric("status", job.status)
            cols[2].metric("runs", f"{job.completed_runs}/{job.total_runs}")
            cols[3].metric("aggregate", job.aggregate_count)
            st.caption(f"updated: {job.updated_at}")
            st.code(str(job.path), language="text")

            action_cols = st.columns([1, 1, 1, 1])
            if action_cols[0].button("Run", key=f"run_{job.graph_id}", disabled=job.status != "queued"):
                with st.spinner("Running job..."):
                    result = run_interval_per_vs_k_job(job.path)
                if result.get("output") is None:
                    st.warning("Job was cancelled.")
                else:
                    st.success("Job completed.")
                    st.code(str(result["output"]), language="text")
                st.rerun()

            resume_disabled = not can_resume_job(job)
            if action_cols[1].button("Resume", key=f"resume_{job.graph_id}", disabled=resume_disabled):
                with st.spinner("Resuming missing runs, rebuilding aggregate, and rendering PDF..."):
                    result = run_interval_per_vs_k_job(job.path)
                if result.get("output") is None:
                    st.warning("Job was cancelled.")
                else:
                    st.success("Job resumed and completed.")
                    st.code(str(result["output"]), language="text")
                st.rerun()

            cancel_disabled = job.status in {"completed", "failed", "cancelled"}
            if action_cols[2].button("Cancel", key=f"cancel_{job.graph_id}", disabled=cancel_disabled):
                request_cancel_graph_job(job.path)
                st.warning("Cancel requested. Running code checks this between run completions.")
                st.rerun()

            delete_disabled = job.status in RUNNING_STATUSES
            if action_cols[3].button("Delete history/data", key=f"delete_history_{job.graph_id}", disabled=delete_disabled):
                deleted_path = delete_graph_job(job.path)
                st.success(f"Deleted: {deleted_path}")
                st.rerun()


def can_resume_job(job: Any) -> bool:
    if job.status in RUNNING_STATUSES or job.status in {"queued", "completed"}:
        return False
    if job.status in {"cancelled", "failed"}:
        return True
    return job.completed_runs < job.total_runs


def render_results_page() -> None:
    st.header("結果・グラフ確認")
    jobs = list_graph_jobs()
    visible_jobs = [job for job in jobs if job.status != "cancelled"]
    if not visible_jobs:
        st.info("No graph folders to show.")
        return

    labels = [
        f"{job.graph_id} / {job.graph_type} / {format_graph_key(job.graph_key)}"
        for job in visible_jobs
    ]
    selected = st.selectbox("graph folder", labels)
    job = visible_jobs[labels.index(selected)]
    manifest = read_json(job.path / "manifest.json")
    requests = read_json(job.path / "requests.json")
    params = requests.get("params", manifest.get("input", {}))

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("status", job.status)
    col_b.metric("total runs", job.total_runs)
    col_c.metric("aggregate sets", job.aggregate_count)

    st.subheader("Parameters")
    simulation_base = dict(params.get("simulation_base") or {})
    render_result_parameter_summary(params, simulation_base)

    with st.expander("Full parameter JSON", expanded=False):
        st.json(
            {
                "graph_type": job.graph_type,
                "graph_key": job.graph_key,
                "k_start": params.get("k_start"),
                "k_stop": params.get("k_stop"),
                "k_step": params.get("k_step"),
                "k_count": len(params.get("k_values", [])),
                "runs_per_k": params.get("runs_per_k"),
                "interval_start_ms": params.get("interval_start_ms"),
                "interval_end_ms": params.get("interval_end_ms"),
                "per_method": params.get("per_method"),
                "raw_run_store": RAW_RUN_DB_NAME,
                "simulation_base": simulation_base,
                "plot_settings": current_plot_settings(
                    job.path / "graph_data.sqlite",
                    dict(params.get("plot_settings") or {}),
                ),
            }
        )

    with st.expander("Aggregate data", expanded=False):
        aggregate_df = read_aggregate_interval_per(job.path / "graph_data.sqlite")
        if aggregate_df.empty:
            st.info("No aggregate data yet.")
        else:
            st.dataframe(aggregate_df, width="stretch", hide_index=True)

    output_path = representative_pdf_path(job.path)
    if output_path is not None:
        st.subheader("Representative PDF")
        st.code(str(output_path), language="text")
        render_pdf_preview(output_path)
        st.download_button(
            "Download PDF",
            data=output_path.read_bytes(),
            file_name=output_path.name,
            mime="application/pdf",
        )

    st.subheader("Redraw")
    current_plot = current_plot_settings(
        job.path / "graph_data.sqlite",
        dict(params.get("plot_settings") or {}),
    )
    with st.form(f"redraw_{job.graph_id}"):
        plot_settings = render_plot_settings(
            current_plot,
            key_prefix=f"redraw_plot_{job.graph_id}_{plot_settings_key_fragment(current_plot)}",
        )
        redraw_clicked = st.form_submit_button("Overwrite representative PDF")
    if redraw_clicked:
        aggregate_set_id = f"interval_{int(float(params['interval_start_ms']))}_to_{int(float(params['interval_end_ms']))}"
        output = render_interval_per_vs_k_pdf(
            graph_dir=job.path,
            db_path=job.path / "graph_data.sqlite",
            aggregate_set_id=aggregate_set_id,
            coupling_function=str(job.graph_key.get("coupling_function", "")),
            interval_start_ms=float(params["interval_start_ms"]),
            interval_end_ms=float(params["interval_end_ms"]),
            plot_settings=plot_settings,
            strength_ratio=float(simulation_base.get("strength_ratio", -0.0001)),
        )
        save_plot_settings_and_output(job.path / "graph_data.sqlite", aggregate_set_id, plot_settings, output, job.path)
        st.success(f"Redrawn: {output}")
        st.rerun()

    st.subheader("Data delete")
    st.warning("The selected graph folder will be permanently deleted.")
    delete_disabled = job.status in RUNNING_STATUSES
    confirm_delete = st.checkbox(
        f"Confirm permanent delete: {job.graph_id}",
        key=f"confirm_delete_{job.graph_id}",
        disabled=delete_disabled,
    )
    if st.button(
        "Delete this graph folder permanently",
        key=f"delete_{job.graph_id}",
        type="primary",
        disabled=delete_disabled or not confirm_delete,
    ):
        deleted_path = delete_graph_job(job.path)
        st.success(f"Deleted: {deleted_path}")
        st.rerun()


def render_coupling_function_page() -> None:
    st.header("結合関数確認")

    if st.button("Reload coupling functions"):
        importlib.invalidate_caches()
        importlib.reload(coupling_functions)
        st.success("Reloaded coupling_functions.py")
        st.rerun()

    coupling_options = current_coupling_function_names()
    saved_settings = load_coupling_function_view_settings()
    saved_selected = [
        function_name
        for function_name in saved_settings.get("selected_functions", coupling_options)
        if function_name in coupling_options
    ] or coupling_options
    selected_functions = st.multiselect(
        "coupling function",
        coupling_options,
        default=saved_selected,
    )
    with st.expander("Plot settings", expanded=True):
        cols = st.columns(4)
        sample_count = cols[0].number_input(
            "sample points",
            min_value=51,
            max_value=5001,
            value=int(saved_settings.get("sample_count", 1001)),
            step=50,
        )
        marker_size = cols[1].number_input(
            "point size",
            min_value=1.0,
            value=float(saved_settings.get("marker_size", 10.0)),
            step=1.0,
        )
        show_zero_lines = cols[2].checkbox(
            "show zero lines",
            value=bool(saved_settings.get("show_zero_lines", True)),
        )
        show_grid = cols[3].checkbox(
            "show grid",
            value=bool(saved_settings.get("show_grid", True)),
        )

        label_cols = st.columns(2)
        x_label = label_cols[0].text_input(
            "x axis label",
            value=str(saved_settings.get("x_label", r"\theta_j-\theta_i")),
        )
        y_label = label_cols[1].text_input(
            "y axis label",
            value=str(saved_settings.get("y_label", "Coupling function value")),
        )

        font_cols = st.columns(4)
        axis_label_font_size = font_cols[0].number_input(
            "axis label font size",
            min_value=1,
            value=int(saved_settings.get("axis_label_font_size", 12)),
            step=1,
        )
        tick_font_size = font_cols[1].number_input(
            "tick font size",
            min_value=1,
            value=int(saved_settings.get("tick_font_size", 10)),
            step=1,
        )
        legend_font_size = font_cols[2].number_input(
            "legend font size",
            min_value=1,
            value=int(saved_settings.get("legend_font_size", 10)),
            step=1,
        )
        title_font_size = font_cols[3].number_input(
            "title font size",
            min_value=1,
            value=int(saved_settings.get("title_font_size", 12)),
            step=1,
        )

        layout_cols = st.columns(3)
        figure_width = layout_cols[0].number_input(
            "figure width",
            min_value=1.0,
            value=float(saved_settings.get("figure_width", 8.0)),
            step=0.5,
        )
        figure_height = layout_cols[1].number_input(
            "figure height",
            min_value=1.0,
            value=float(saved_settings.get("figure_height", 4.8)),
            step=0.5,
        )
        show_title = layout_cols[2].checkbox(
            "show title",
            value=bool(saved_settings.get("show_title", True)),
        )

        legend_cols = st.columns(2)
        show_legend = legend_cols[0].checkbox(
            "show legend",
            value=bool(saved_settings.get("show_legend", True)),
        )
        legend_location_options = [
            "best",
            "upper right",
            "upper left",
            "lower left",
            "lower right",
            "right",
            "center left",
            "center right",
            "lower center",
            "upper center",
            "center",
        ]
        legend_location = legend_cols[1].selectbox(
            "legend location",
            legend_location_options,
            index=select_index(
                legend_location_options,
                str(saved_settings.get("legend_location", "best")),
            ),
            disabled=not show_legend,
        )

        legend_labels: dict[str, str] = {}
        saved_legend_labels = dict(saved_settings.get("legend_labels") or {})
        with st.expander("Legend labels", expanded=False):
            for function_name in selected_functions:
                legend_labels[function_name] = st.text_input(
                    f"{function_name} label",
                    value=str(saved_legend_labels.get(function_name, function_name)),
                    key=f"coupling_legend_label_{function_name}",
                    disabled=not show_legend,
                )

        y_auto = st.checkbox("y range auto", value=bool(saved_settings.get("y_auto", True)))

    y_min = float(saved_settings.get("y_min", -2.0))
    y_max = float(saved_settings.get("y_max", 2.0))
    if not y_auto:
        range_cols = st.columns(2)
        y_min = range_cols[0].number_input("y min", value=y_min, step=0.1)
        y_max = range_cols[1].number_input("y max", value=y_max, step=0.1)

    current_settings = {
        "selected_functions": list(selected_functions),
        "sample_count": int(sample_count),
        "marker_size": float(marker_size),
        "show_zero_lines": bool(show_zero_lines),
        "show_grid": bool(show_grid),
        "x_label": str(x_label),
        "y_label": str(y_label),
        "axis_label_font_size": int(axis_label_font_size),
        "tick_font_size": int(tick_font_size),
        "legend_font_size": int(legend_font_size),
        "title_font_size": int(title_font_size),
        "figure_width": float(figure_width),
        "figure_height": float(figure_height),
        "show_title": bool(show_title),
        "show_legend": bool(show_legend),
        "legend_location": str(legend_location),
        "legend_labels": legend_labels,
        "y_auto": bool(y_auto),
        "y_min": float(y_min),
        "y_max": float(y_max),
    }
    save_coupling_function_view_settings(current_settings)

    if not selected_functions:
        st.info("Select at least one coupling function.")
        return

    df = coupling_function_curve_data(selected_functions, int(sample_count))
    fig, ax = plt.subplots(figsize=(float(figure_width), float(figure_height)))
    for function_name in selected_functions:
        ax.scatter(
            df["phase_diff"],
            df[function_name],
            label=format_math_label(legend_labels.get(function_name, function_name)),
            s=float(marker_size),
        )

    ax.set_xlabel(format_math_label(str(x_label)), fontsize=int(axis_label_font_size))
    ax.set_ylabel(format_math_label(str(y_label)), fontsize=int(axis_label_font_size))
    if show_title:
        ax.set_title("Coupling Functions", fontsize=int(title_font_size))
    ax.set_xlim(-math.pi, math.pi)
    ax.set_xticks([-math.pi, -math.pi / 2.0, 0.0, math.pi / 2.0, math.pi])
    ax.set_xticklabels(["-π", "-π/2", "0", "π/2", "π"])
    ax.tick_params(axis="both", labelsize=int(tick_font_size))
    if not y_auto:
        ax.set_ylim(float(y_min), float(y_max))
    if show_zero_lines:
        ax.axhline(0.0, color="0.45", linewidth=0.8)
        ax.axvline(0.0, color="0.45", linewidth=0.8)
    ax.grid(bool(show_grid), alpha=0.35)
    if show_legend:
        ax.legend(loc=str(legend_location), fontsize=int(legend_font_size))
    fig.tight_layout()
    st.pyplot(fig)

    pdf_buffer = BytesIO()
    fig.savefig(pdf_buffer, format="pdf", bbox_inches="tight")
    st.download_button(
        "Download PDF",
        data=pdf_buffer.getvalue(),
        file_name="coupling_function_curves.pdf",
        mime="application/pdf",
    )
    plt.close(fig)

    with st.expander("Curve data", expanded=False):
        display_df = df.copy()
        display_df.insert(1, "phase_diff_over_pi", display_df["phase_diff"] / math.pi)
        st.dataframe(display_df, width="stretch", hide_index=True)
        st.download_button(
            "Download CSV",
            data=display_df.to_csv(index=False).encode("utf-8"),
            file_name="coupling_function_curves.csv",
            mime="text/csv",
        )


def coupling_function_curve_data(function_names: list[str], sample_count: int) -> pd.DataFrame:
    x_values = np.linspace(-math.pi, math.pi, int(sample_count))
    data: dict[str, Any] = {"phase_diff": x_values}
    for function_name in function_names:
        coupling_type = coupling_functions.CouplingFunction(function_name)
        coupling_func = coupling_functions.resolve_coupling_function(coupling_type)
        data[function_name] = [float(coupling_func(float(x))) for x in x_values]
    return pd.DataFrame(data)


def current_coupling_function_names() -> list[str]:
    return [item.value for item in coupling_functions.CouplingFunction]


def render_result_parameter_summary(
    params: dict[str, Any],
    simulation_base: dict[str, Any],
) -> None:
    k_values = list(params.get("k_values", []))
    runs_per_k = int(params.get("runs_per_k", 0) or 0)
    total_runs = len(k_values) * runs_per_k

    graph_cols = st.columns(4)
    graph_cols[0].metric("K points", len(k_values))
    graph_cols[1].metric("runs per K", runs_per_k)
    graph_cols[2].metric("total runs", total_runs)
    graph_cols[3].metric("coupling", str(params.get("coupling_function", "")))

    sim_cols = st.columns(4)
    sim_cols[0].metric("device count", int(simulation_base.get("device_count", 0) or 0))
    sim_cols[1].metric(
        "cycle time",
        format_duration_ms(float(simulation_base.get("cycle_time", 0) or 0)),
    )
    sim_cols[2].metric(
        "duration",
        format_duration_ms(float(simulation_base.get("duration_ms", 0) or 0)),
    )
    sim_cols[3].metric("max workers", int(simulation_base.get("max_workers", 0) or 0))

    interval_cols = st.columns(4)
    interval_cols[0].metric(
        "interval start",
        format_duration_ms(float(params.get("interval_start_ms", 0) or 0)),
    )
    interval_cols[1].metric(
        "interval end",
        format_duration_ms(float(params.get("interval_end_ms", 0) or 0)),
    )
    interval_cols[2].metric(
        "initial phase range",
        format_percent_range(
            float(simulation_base.get("initial_phase_start_percent", 0.0) or 0.0),
            float(simulation_base.get("initial_phase_end_percent", 100.0) or 100.0),
        ),
    )
    interval_cols[3].metric(
        "carrier sense",
        format_duration_ms(float(simulation_base.get("carrier_sense_duration_ms", 0) or 0)),
    )

    lora_summary = build_lora_summary_from_base(simulation_base)
    if "error" in lora_summary:
        st.error(f"LoRa airtime could not be calculated: {lora_summary['error']}")
    else:
        lora_cols = st.columns(4)
        lora_cols[0].metric("LoRa airtime", f"{lora_summary['airtime_ms']:.3f} ms")
        lora_cols[1].metric("symbol time", f"{lora_summary['symbol_time_ms']:.3f} ms")
        lora_cols[2].metric("LDRO", "on" if lora_summary["ldro"] else "off")
        lora_cols[3].metric("simulation mode", "per_measurement")

    st.json(
        {
            "simulation": {
                "device_count": simulation_base.get("device_count"),
                "cycle_time_ms": simulation_base.get("cycle_time"),
                "initial_phase_start_percent": simulation_base.get("initial_phase_start_percent", 0.0),
                "initial_phase_end_percent": simulation_base.get("initial_phase_end_percent", 100.0),
                "duration_ms": simulation_base.get("duration_ms"),
                "listening_rate": simulation_base.get("listening_rate"),
                "strength_ratio": simulation_base.get("strength_ratio"),
                "seed": simulation_base.get("seed"),
                "max_workers": simulation_base.get("max_workers"),
                "simulation_mode": "per_measurement",
                "carrier_sense_duration_ms": simulation_base.get("carrier_sense_duration_ms"),
            },
            "lora": {
                "payload_bytes": simulation_base.get("lora_payload_bytes"),
                "spreading_factor": simulation_base.get("lora_spreading_factor"),
                "bandwidth_hz": simulation_base.get("lora_bandwidth_hz"),
                "coding_rate_denominator": simulation_base.get("lora_coding_rate_denominator"),
                "preamble_symbols": simulation_base.get("lora_preamble_symbols"),
                "explicit_header": simulation_base.get("lora_explicit_header"),
                "crc_enabled": simulation_base.get("lora_crc_enabled"),
                "low_data_rate_optimize": simulation_base.get("lora_low_data_rate_optimize"),
                **lora_summary,
            },
        }
    )


def build_lora_summary_from_base(simulation_base: dict[str, Any]) -> dict[str, Any]:
    try:
        config = build_lora_airtime_config(
            payload_bytes=int(simulation_base.get("lora_payload_bytes", 16)),
            spreading_factor=int(simulation_base.get("lora_spreading_factor", 7)),
            bandwidth_hz=int(simulation_base.get("lora_bandwidth_hz", 125_000)),
            coding_rate_denominator=int(
                simulation_base.get("lora_coding_rate_denominator", 5)
            ),
            preamble_symbols=int(simulation_base.get("lora_preamble_symbols", 8)),
            explicit_header=bool(simulation_base.get("lora_explicit_header", True)),
            crc_enabled=bool(simulation_base.get("lora_crc_enabled", True)),
            low_data_rate_optimize_mode=str(
                simulation_base.get("lora_low_data_rate_optimize", "auto")
            ),
        )
        return {
            "airtime_ms": calculate_lora_airtime_ms(config),
            "symbol_time_ms": symbol_duration_ms(config),
            "ldro": resolve_low_data_rate_optimize(config),
        }
    except (TypeError, ValueError) as exc:
        return {"error": str(exc)}


def render_management_page() -> None:
    st.header("その他管理")
    overview = get_storage_overview()
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("graph folders", overview["job_count"])
    col_b.metric("graph SQLite files", overview["sqlite_count"])
    col_c.metric("raw SQLite files", overview["raw_sqlite_count"])

    st.subheader("Storage layout")
    st.code(
        "\n".join(
            [
                "outputs/graph_runs/<graph_type>/<graph_id>/",
                "  manifest.json",
                "  status.json",
                "  requests.json",
                "  graph_data.sqlite",
                f"  {RAW_RUN_DB_NAME}",
                "  figures/",
                "  logs/",
            ]
        ),
        language="text",
    )

    st.subheader("Server environment")
    system = get_system_status()
    cpu_col, mem_col, disk_col = st.columns(3)
    cpu_col.metric("CPU cores", system["cpu_count"])
    mem_col.metric("Memory", system["memory_label"])
    disk_col.metric("Disk free", system["disk_free_label"])
    st.json(
        {
            "platform": system["platform"],
            "python": system["python"],
            "machine": system["machine"],
            "processor": system["processor"],
            "working_directory": str(Path.cwd()),
            "memory": system["memory"],
            "disk": system["disk"],
        }
    )


def render_plot_settings(saved_plot: dict[str, Any], key_prefix: str) -> dict[str, Any]:
    def widget_key(name: str) -> str:
        return f"{key_prefix}_{name}"

    with st.expander("Plot settings", expanded=False):
        axis_col, font_col, style_col = st.columns(3)
        with axis_col:
            x_auto = st.checkbox(
                "x range auto",
                value=saved_plot.get("xlim_min") is None and saved_plot.get("xlim_max") is None,
                key=widget_key("x_auto"),
            )
            xlim_min = st.number_input(
                "x min",
                value=float(saved_plot.get("xlim_min") or 0.0),
                disabled=x_auto,
                key=widget_key("xlim_min"),
            )
            xlim_max = st.number_input(
                "x max",
                value=float(saved_plot.get("xlim_max") or 20.0),
                disabled=x_auto,
                key=widget_key("xlim_max"),
            )
            y_auto = st.checkbox(
                "y range auto",
                value=saved_plot.get("ylim_min") is None and saved_plot.get("ylim_max") is None,
                key=widget_key("y_auto"),
            )
            ylim_min = st.number_input(
                "y min",
                value=float(saved_plot.get("ylim_min") if saved_plot.get("ylim_min") is not None else 0.0),
                disabled=y_auto,
                key=widget_key("ylim_min"),
            )
            ylim_max = st.number_input(
                "y max",
                value=float(saved_plot.get("ylim_max") if saved_plot.get("ylim_max") is not None else 100.0),
                disabled=y_auto,
                key=widget_key("ylim_max"),
            )
        with font_col:
            figure_width = st.number_input(
                "figure width",
                min_value=1.0,
                value=float(saved_plot.get("figure_width", 8.0)),
                step=0.5,
                key=widget_key("figure_width"),
            )
            figure_height = st.number_input(
                "figure height",
                min_value=1.0,
                value=float(saved_plot.get("figure_height", 5.0)),
                step=0.5,
                key=widget_key("figure_height"),
            )
            font_size_label = st.number_input(
                "label font size",
                min_value=1,
                value=int(saved_plot.get("font_size_label", 12)),
                step=1,
                key=widget_key("font_size_label"),
            )
            font_size_ticks = st.number_input(
                "tick font size",
                min_value=1,
                value=int(saved_plot.get("font_size_ticks", 10)),
                step=1,
                key=widget_key("font_size_ticks"),
            )
            font_size_title = st.number_input(
                "title font size",
                min_value=1,
                value=int(saved_plot.get("font_size_title", 12)),
                step=1,
                key=widget_key("font_size_title"),
            )
        with style_col:
            marker_options = ["o", "s", "^", "D", "x", "+", "."]
            line_style_options = ["-", "--", "-.", ":", "None"]
            marker = st.selectbox(
                "marker",
                marker_options,
                index=select_index(marker_options, str(saved_plot.get("marker", "o"))),
                key=widget_key("marker"),
            )
            marker_size = st.number_input(
                "marker size",
                min_value=0.0,
                value=float(saved_plot.get("marker_size", 6.0)),
                step=0.5,
                key=widget_key("marker_size"),
            )
            line_style = st.selectbox(
                "line style",
                line_style_options,
                index=select_index(line_style_options, str(saved_plot.get("line_style", "-"))),
                key=widget_key("line_style"),
            )
            line_width = st.number_input(
                "line width",
                min_value=0.0,
                value=float(saved_plot.get("line_width", 1.5)),
                step=0.25,
                key=widget_key("line_width"),
            )
            show_error_bars = st.checkbox(
                "show error bars",
                value=bool(saved_plot.get("show_error_bars", True)),
                key=widget_key("show_error_bars"),
            )
            error_bar_capsize = st.number_input(
                "error bar capsize",
                min_value=0.0,
                value=float(saved_plot.get("error_bar_capsize", 4.0)),
                step=0.5,
                key=widget_key("error_bar_capsize"),
            )
            show_title = st.checkbox(
                "show title",
                value=bool(saved_plot.get("show_title", True)),
                key=widget_key("show_title"),
            )
            show_grid = st.checkbox(
                "show grid",
                value=bool(saved_plot.get("show_grid", True)),
                key=widget_key("show_grid"),
            )
            show_min_annotation = st.checkbox(
                "show min value",
                value=bool(saved_plot.get("show_min_annotation", False)),
                key=widget_key("show_min_annotation"),
            )
            min_annotation_font_size = st.number_input(
                "min label font size",
                min_value=1,
                value=int(saved_plot.get("min_annotation_font_size", 10)),
                step=1,
                disabled=not show_min_annotation,
                key=widget_key("min_annotation_font_size"),
            )
            min_annotation_x_offset = st.number_input(
                "min label x offset",
                value=float(saved_plot.get("min_annotation_x_offset", 10.0)),
                step=1.0,
                disabled=not show_min_annotation,
                key=widget_key("min_annotation_x_offset"),
            )
            min_annotation_y_offset = st.number_input(
                "min label y offset",
                value=float(saved_plot.get("min_annotation_y_offset", 10.0)),
                step=1.0,
                disabled=not show_min_annotation,
                key=widget_key("min_annotation_y_offset"),
            )
            save_dpi = st.number_input(
                "save dpi",
                min_value=72,
                value=int(saved_plot.get("save_dpi", 300)),
                step=50,
                key=widget_key("save_dpi"),
            )

    return {
        "xlim_min": None if x_auto else float(xlim_min),
        "xlim_max": None if x_auto else float(xlim_max),
        "ylim_min": None if y_auto else float(ylim_min),
        "ylim_max": None if y_auto else float(ylim_max),
        "figure_width": float(figure_width),
        "figure_height": float(figure_height),
        "font_size_label": int(font_size_label),
        "font_size_ticks": int(font_size_ticks),
        "font_size_title": int(font_size_title),
        "marker": marker,
        "marker_size": float(marker_size),
        "line_style": line_style,
        "line_width": float(line_width),
        "show_error_bars": bool(show_error_bars),
        "error_bar_capsize": float(error_bar_capsize),
        "show_title": bool(show_title),
        "show_grid": bool(show_grid),
        "show_min_annotation": bool(show_min_annotation),
        "min_annotation_font_size": int(min_annotation_font_size),
        "min_annotation_x_offset": float(min_annotation_x_offset),
        "min_annotation_y_offset": float(min_annotation_y_offset),
        "save_dpi": int(save_dpi),
    }


def duration_input_ms(label: str, key: str, default_ms: float, min_value: float = 0.0) -> float:
    unit_options = ["ms", "sec", "min"]
    unit = best_duration_unit(default_ms)
    value = default_ms / duration_unit_factor(unit)
    cols = st.columns([2, 1])
    number = cols[0].number_input(
        label,
        min_value=min_value / duration_unit_factor(unit),
        value=float(value),
        step=duration_step_for_unit(unit),
        key=f"{key}_value",
    )
    selected_unit = cols[1].selectbox(
        "unit",
        unit_options,
        index=select_index(unit_options, unit),
        key=f"{key}_unit",
        label_visibility="collapsed",
    )
    return float(number) * duration_unit_factor(selected_unit)


def best_duration_unit(ms: float) -> str:
    if ms >= 60_000 and ms % 60_000 == 0:
        return "min"
    if ms >= 1000 and ms % 1000 == 0:
        return "sec"
    return "ms"


def duration_unit_factor(unit: str) -> float:
    return {"ms": 1.0, "sec": 1000.0, "min": 60_000.0}[unit]


def duration_step_for_unit(unit: str) -> float:
    return {"ms": 1.0, "sec": 1.0, "min": 0.5}[unit]


def format_duration_ms(ms: float) -> str:
    if ms >= 60_000 and ms % 60_000 == 0:
        return f"{ms / 60_000:g} min"
    if ms >= 1000 and ms % 1000 == 0:
        return f"{ms / 1000:g} sec"
    return f"{ms:g} ms"


def format_percent_range(start_percent: float, end_percent: float) -> str:
    return f"{start_percent:g}% to {end_percent:g}%"


def load_last_interval_per_vs_k_params() -> dict[str, Any]:
    params = json.loads(json.dumps(DEFAULT_INTERVAL_PER_VS_K_PARAMS))
    if not LAST_INTERVAL_PER_VS_K_PARAMS_PATH.exists():
        return params
    try:
        saved = json.loads(LAST_INTERVAL_PER_VS_K_PARAMS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return params
    if isinstance(saved, dict):
        deep_update(params, saved)
    return params


def save_last_interval_per_vs_k_params(params: dict[str, Any]) -> None:
    LAST_INTERVAL_PER_VS_K_PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_INTERVAL_PER_VS_K_PARAMS_PATH.write_text(
        json.dumps(params, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_coupling_function_view_settings() -> dict[str, Any]:
    settings = json.loads(json.dumps(DEFAULT_COUPLING_FUNCTION_VIEW_SETTINGS))
    if not COUPLING_FUNCTION_VIEW_SETTINGS_PATH.exists():
        return settings
    try:
        saved = json.loads(COUPLING_FUNCTION_VIEW_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return settings
    if isinstance(saved, dict):
        deep_update(settings, saved)
    return settings


def save_coupling_function_view_settings(settings: dict[str, Any]) -> None:
    COUPLING_FUNCTION_VIEW_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    COUPLING_FUNCTION_VIEW_SETTINGS_PATH.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def format_math_label(label: object) -> str:
    text = str(label)
    stripped = text.strip()
    if stripped.startswith("$$") and stripped.endswith("$$") and len(stripped) >= 4:
        return f"${stripped[2:-2]}$"
    if stripped.startswith("$") and stripped.endswith("$"):
        return stripped.replace("\\thrta", "\\theta")
    return text


def deep_update(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_update(target[key], value)
        else:
            target[key] = value


def select_index(options: list[str], value: str) -> int:
    try:
        return options.index(value)
    except ValueError:
        return 0


def build_k_values(k_start: float, k_stop: float, k_step: float) -> list[float]:
    values: list[float] = []
    current = float(k_start)
    stop = float(k_stop)
    step = float(k_step)
    while current <= stop + (step * 1e-9):
        values.append(round(current, 10))
        current += step
    return values


def build_lora_airtime_config(
    *,
    payload_bytes: int,
    spreading_factor: int,
    bandwidth_hz: int,
    coding_rate_denominator: int,
    preamble_symbols: int,
    explicit_header: bool,
    crc_enabled: bool,
    low_data_rate_optimize_mode: str,
) -> LoRaAirtimeConfig:
    return LoRaAirtimeConfig(
        payload_bytes=payload_bytes,
        spreading_factor=spreading_factor,
        bandwidth_hz=bandwidth_hz,
        coding_rate_denominator=coding_rate_denominator,
        preamble_symbols=preamble_symbols,
        explicit_header=explicit_header,
        crc_enabled=crc_enabled,
        low_data_rate_optimize=optional_bool(low_data_rate_optimize_mode),
    )


def optional_bool(value: object) -> bool | None:
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


def symbol_duration_ms(config: LoRaAirtimeConfig) -> float:
    return (2**config.spreading_factor) / float(config.bandwidth_hz) * 1000.0


def format_graph_key(graph_key: dict[str, object]) -> str:
    if not graph_key:
        return ""
    return ", ".join(f"{key}={value}" for key, value in graph_key.items())


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_aggregate_interval_per(db_path: Path) -> pd.DataFrame:
    if not db_path.exists():
        return pd.DataFrame()
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            return pd.read_sql_query(
                """
                SELECT aggregate_set_id, coupling_function, coupling_strength,
                       per_percent_mean, per_percent_std, per_percent_min,
                       per_percent_max, expected_packets_sum,
                       actual_packets_sum, count
                FROM aggregate_interval_per
                ORDER BY aggregate_set_id, coupling_function, coupling_strength
                """,
                conn,
            )
        except sqlite3.Error:
            return pd.DataFrame()


def representative_pdf_path(graph_dir: Path) -> Path | None:
    db_path = graph_dir / "graph_data.sqlite"
    if not db_path.exists():
        return None
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            row = conn.execute(
                """
                SELECT relative_path
                FROM outputs
                WHERE output_type = 'representative_pdf'
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
        except sqlite3.Error:
            return None
    if row is None:
        return None
    path = graph_dir / str(row[0])
    return path if path.exists() else None


def current_plot_settings(db_path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    settings = dict(fallback)
    if not db_path.exists():
        return settings
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            row = conn.execute(
                """
                SELECT settings_json
                FROM plot_settings
                WHERE settings_id = 'current'
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
        except sqlite3.Error:
            return settings
    if row is None:
        return settings
    try:
        saved_settings = json.loads(str(row[0]))
    except json.JSONDecodeError:
        return settings
    if isinstance(saved_settings, dict):
        settings.update(saved_settings)
    return settings


def plot_settings_key_fragment(settings: dict[str, Any]) -> str:
    payload = json.dumps(settings, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def render_pdf_preview(pdf_path: Path) -> None:
    pdf_bytes = pdf_path.read_bytes()
    encoded_pdf = base64.b64encode(pdf_bytes).decode("ascii")
    updated_at = pdf_path.stat().st_mtime_ns
    st.markdown(
        f"""
        <iframe
            src="data:application/pdf;base64,{encoded_pdf}#updated={updated_at}"
            width="100%"
            height="720"
            type="application/pdf"
            style="border: 1px solid #ddd; border-radius: 4px;"
        ></iframe>
        """,
        unsafe_allow_html=True,
    )


def save_plot_settings_and_output(
    db_path: Path,
    aggregate_set_id: str,
    plot_settings: dict[str, Any],
    output_path: Path,
    graph_dir: Path,
) -> None:
    now = pd.Timestamp.utcnow().replace(microsecond=0).isoformat()
    relative_output = str(output_path.resolve().relative_to(graph_dir.resolve()))
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO plot_settings
                (settings_id, aggregate_set_id, settings_json, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            ("current", aggregate_set_id, json.dumps(plot_settings, ensure_ascii=False), now),
        )
        conn.execute("DELETE FROM outputs WHERE output_type = ?", ("representative_pdf",))
        conn.execute(
            """
            INSERT INTO outputs
                (output_id, aggregate_set_id, output_type, relative_path, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("representative_pdf", aggregate_set_id, "representative_pdf", relative_output, now),
        )
        conn.commit()


def get_system_status() -> dict[str, object]:
    disk = shutil.disk_usage(Path.cwd())
    memory = get_memory_status()
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count() or 0,
        "memory": memory,
        "memory_label": format_memory_label(memory),
        "disk": {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "total": format_bytes(disk.total),
            "used": format_bytes(disk.used),
            "free": format_bytes(disk.free),
            "used_percent": round((disk.used / disk.total) * 100, 1) if disk.total else None,
        },
        "disk_free_label": format_bytes(disk.free),
    }


def get_memory_status() -> dict[str, object]:
    if platform.system().lower() != "windows":
        return {"available": False, "reason": "memory status is implemented for Windows in this GUI"}

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return {"available": False, "reason": "GlobalMemoryStatusEx failed"}

    used = status.ullTotalPhys - status.ullAvailPhys
    return {
        "available": True,
        "total_bytes": status.ullTotalPhys,
        "used_bytes": used,
        "available_bytes": status.ullAvailPhys,
        "total": format_bytes(status.ullTotalPhys),
        "used": format_bytes(used),
        "available_memory": format_bytes(status.ullAvailPhys),
        "used_percent": int(status.dwMemoryLoad),
    }


def format_memory_label(memory: dict[str, object]) -> str:
    if not memory.get("available"):
        return "unknown"
    return f"{memory['used_percent']}% / {memory['total']}"


def format_bytes(value: int | float) -> str:
    size = float(value)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


if __name__ == "__main__":
    main()
