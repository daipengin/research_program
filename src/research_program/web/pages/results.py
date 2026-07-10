from __future__ import annotations

from typing import Any

import streamlit as st

from research_program.graph_workflow.execution import (
    render_convergence_cycle_vs_k_pdf,
    render_interval_per_vs_k_pdf,
    render_phase_gap_error_vs_k_pdf,
)
from research_program.graph_workflow.storage import (
    RAW_RUN_DB_NAME,
    delete_graph_job,
    list_graph_jobs,
)
from research_program.simulation.lora_airtime import (
    calculate_lora_airtime_ms,
    resolve_low_data_rate_optimize,
)
from research_program.web.components.plot_settings import (
    plot_settings_key_fragment,
    render_plot_settings,
)
from research_program.web.constants import RUNNING_STATUSES
from research_program.web.utils import (
    build_lora_airtime_config,
    current_plot_settings,
    format_duration_ms,
    format_graph_key,
    format_percent_range,
    interval_per_db_path,
    read_aggregate_convergence_cycles,
    read_aggregate_interval_per,
    read_aggregate_phase_gap_error_points,
    read_json,
    render_pdf_preview,
    representative_pdf_path,
    save_plot_settings_and_output,
    symbol_duration_ms,
)


def render_results_page() -> None:
    st.header("結果・グラフ確認")
    jobs = list_graph_jobs()
    visible_jobs = [job for job in jobs if job.status != "cancelled"]
    if not visible_jobs:
        st.info("No graph folders to show.")
        return

    graph_types = sorted({job.graph_type for job in visible_jobs})
    selected_graph_type = st.selectbox("graph type", graph_types)
    type_jobs = [job for job in visible_jobs if job.graph_type == selected_graph_type]
    labels = [
        f"{job.graph_id} / {format_graph_key(job.graph_key)}"
        for job in type_jobs
    ]
    selected = st.selectbox("graph folder", labels)
    job = type_jobs[labels.index(selected)]
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
                "stable_cycle_count": params.get("stable_cycle_count"),
                "phase_gap_change_threshold": params.get("phase_gap_change_threshold"),
                "target_cycle_mode": params.get("target_cycle_mode"),
                "target_cycle_index": params.get("target_cycle_index"),
                "source_mode": params.get("source_mode"),
                "source_graph_id": params.get("source_graph_id"),
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
        if job.graph_type == "convergence_cycle_vs_k":
            aggregate_df = read_aggregate_convergence_cycles(job.path / "graph_data.sqlite")
        elif job.graph_type == "phase_gap_error_vs_k":
            aggregate_df = read_aggregate_phase_gap_error_points(job.path / "graph_data.sqlite")
        else:
            aggregate_df = read_aggregate_interval_per(job.path)
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
        if job.graph_type == "convergence_cycle_vs_k":
            aggregate_set_id = convergence_aggregate_set_id(
                int(params["stable_cycle_count"]),
                float(params["phase_gap_change_threshold"]),
            )
            output = render_convergence_cycle_vs_k_pdf(
                graph_dir=job.path,
                db_path=job.path / "graph_data.sqlite",
                aggregate_set_id=aggregate_set_id,
                coupling_function=str(job.graph_key.get("coupling_function", "")),
                stable_cycle_count=int(params["stable_cycle_count"]),
                phase_gap_change_threshold=float(params["phase_gap_change_threshold"]),
                plot_settings=plot_settings,
                strength_ratio=float(simulation_base.get("strength_ratio", -0.0001)),
            )
        elif job.graph_type == "phase_gap_error_vs_k":
            aggregate_set_id = phase_gap_error_aggregate_set_id(
                str(params.get("target_cycle_mode", "last")),
                params.get("target_cycle_index"),
            )
            output = render_phase_gap_error_vs_k_pdf(
                graph_dir=job.path,
                db_path=job.path / "graph_data.sqlite",
                aggregate_set_id=aggregate_set_id,
                coupling_function=str(job.graph_key.get("coupling_function", "")),
                target_cycle_mode=str(params.get("target_cycle_mode", "last")),
                target_cycle_index=params.get("target_cycle_index"),
                plot_settings=plot_settings,
                strength_ratio=float(simulation_base.get("strength_ratio", -0.0001)),
            )
        else:
            aggregate_set_id = f"interval_{int(float(params['interval_start_ms']))}_to_{int(float(params['interval_end_ms']))}"
            output = render_interval_per_vs_k_pdf(
                graph_dir=job.path,
                db_path=interval_per_db_path(job.path),
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

    if "stable_cycle_count" in params or "phase_gap_change_threshold" in params:
        st.subheader("Convergence Definition")
        conv_cols = st.columns(3)
        conv_cols[0].metric("stable cycle count N", int(params.get("stable_cycle_count", 0) or 0))
        conv_cols[1].metric(
            "threshold [rad]",
            f"{float(params.get('phase_gap_change_threshold', 0.0) or 0.0):g}",
        )
        conv_cols[2].metric("source mode", str(params.get("source_mode", "new_simulation")))
        st.info(
            "For each evaluated cycle, oscillator first-send phases are sorted and adjacent phase gaps "
            "including the wrap-around gap are calculated. The graph treats a run as converged at the "
            "first cycle where every adjacent-gap change from the previous evaluated cycle is at or below "
            "the threshold for N consecutive cycles. Cycles missing any oscillator reset the consecutive count."
        )

    if "target_cycle_mode" in params:
        st.subheader("Phase-gap Error Point")
        point_cols = st.columns(3)
        mode = str(params.get("target_cycle_mode", "last"))
        point_cols[0].metric("target point", "final available cycle" if mode == "last" else "cycle index")
        point_cols[1].metric(
            "target cycle index",
            "-" if mode == "last" else str(int(params.get("target_cycle_index", 0) or 0)),
        )
        point_cols[2].metric("source mode", str(params.get("source_mode", "new_simulation")))
        st.info(
            "For each run, the graph reads the phase-gap error table and takes one value: "
            "the final valid cycle by default, or the specified cycle index. The plotted value is "
            "the mean absolute difference from the ideal adjacent phase gap, averaged by K."
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


def convergence_aggregate_set_id(stable_cycle_count: int, threshold: float) -> str:
    threshold_text = f"{threshold:g}".replace("-", "m").replace(".", "p")
    return f"convergence_{int(stable_cycle_count)}cycles_thr_{threshold_text}"


def phase_gap_error_aggregate_set_id(
    target_cycle_mode: str,
    target_cycle_index: object | None,
) -> str:
    if target_cycle_mode == "cycle_index":
        return f"phase_gap_cycle_{int(target_cycle_index or 0)}"
    return "phase_gap_last"

