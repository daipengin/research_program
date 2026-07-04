from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from research_program.graph_workflow.execution import (
    available_coupling_functions,
    run_interval_per_vs_k_job,
)
from research_program.graph_workflow.storage import (
    create_interval_per_vs_k_job,
    delete_graph_job,
    get_storage_overview,
    list_graph_jobs,
)
from research_program.simulation.lora_airtime import (
    LoRaAirtimeConfig,
    calculate_lora_airtime_ms,
    resolve_low_data_rate_optimize,
)


st.set_page_config(
    page_title="Research Program",
    page_icon="RP",
    layout="wide",
)


LAST_INTERVAL_PER_VS_K_PARAMS_PATH = (
    Path("outputs") / "settings" / "last_interval_per_vs_k_params.json"
)

DEFAULT_INTERVAL_PER_VS_K_PARAMS: dict[str, object] = {
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
        "save_dpi": 300,
    },
    "simulation_base": {
        "duration_ms": 2_000_000.0,
        "seed": 1,
        "device_count": 20,
        "cycle_time": 30_000,
        "listening_rate": 25,
        "strength_ratio": -0.0001,
        "max_workers": 1,
        "simulation_mode": "standard",
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


def main() -> None:
    st.title("Research Program")
    st.caption("Graph-first Web GUI")

    page = st.sidebar.radio(
        "Page",
        ["ジョブ追加", "ジョブ確認", "結果・グラフ確認", "その他管理"],
        label_visibility="collapsed",
    )

    if page == "ジョブ追加":
        render_job_add_page()
    elif page == "ジョブ確認":
        render_job_status_page()
    elif page == "結果・グラフ確認":
        render_results_page()
    else:
        render_management_page()


def render_job_add_page() -> None:
    st.header("ジョブ追加")
    st.caption("1ジョブは原則として1つの完成グラフを作成します。")

    saved_params = load_last_interval_per_vs_k_params()
    saved_base = dict(saved_params.get("simulation_base") or {})
    saved_plot = dict(saved_params.get("plot_settings") or {})

    graph_type = st.selectbox(
        "グラフ種",
        ["Interval PER vs K"],
        help="初期実装ではInterval PER vs Kのみ作成します。",
    )

    with st.form("interval_per_vs_k_job"):
        col_left, col_right = st.columns(2)
        with col_left:
            coupling_options = available_coupling_functions()
            coupling_function = st.selectbox(
                "coupling function",
                coupling_options,
                index=select_index(
                    coupling_options,
                    str(saved_params.get("coupling_function", "KURAMOTO")),
                ),
            )
            k_start = st.number_input(
                "K start",
                value=float(saved_params.get("k_start", 0.0)),
                step=1.0,
            )
            k_stop = st.number_input(
                "K stop",
                value=float(saved_params.get("k_stop", 20.0)),
                step=1.0,
            )
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
            interval_start_ms = st.number_input(
                "interval start ms",
                min_value=0.0,
                value=float(saved_params.get("interval_start_ms", 0.0)),
                step=1000.0,
            )
            interval_end_ms = st.number_input(
                "interval end ms",
                min_value=0.0,
                value=float(saved_params.get("interval_end_ms", 2_000_000.0)),
                step=1000.0,
            )
            simulation_duration_ms = st.number_input(
                "simulation duration ms",
                min_value=1.0,
                value=float(saved_base.get("duration_ms", 2_000_000.0)),
                step=1000.0,
            )
            seed = st.number_input("base seed", value=int(saved_base.get("seed", 1)), step=1)
            device_count = st.number_input(
                "device count",
                min_value=1,
                value=int(saved_base.get("device_count", 20)),
                step=1,
            )
            cycle_time = st.number_input(
                "cycle time ms",
                min_value=1,
                value=int(saved_base.get("cycle_time", 30000)),
                step=1000,
            )
            st.caption(
                "Interval PER vs K initial timing: random from 0 to cycle_time - 1 ms, "
                "1 ms step, duplicates allowed"
            )
            listening_rate = st.number_input(
                "listening rate",
                min_value=0,
                value=int(saved_base.get("listening_rate", 25)),
                step=1,
            )
            strength_ratio = st.number_input(
                "strength ratio",
                value=float(saved_base.get("strength_ratio", -0.0001)),
                step=0.0001,
                format="%.6f",
            )
            max_workers = st.number_input(
                "max workers",
                min_value=1,
                value=int(saved_base.get("max_workers", 1)),
                step=1,
            )

        st.subheader("LoRa / PER measurement")
        lora_col_a, lora_col_b, lora_col_c = st.columns(3)
        with lora_col_a:
            simulation_mode = st.selectbox(
                "simulation mode",
                ["standard", "per_measurement"],
                index=select_index(
                    ["standard", "per_measurement"],
                    str(saved_base.get("simulation_mode", "standard")),
                ),
            )
            carrier_sense_duration_ms = st.number_input(
                "carrier sense duration ms",
                min_value=0.0,
                value=float(saved_base.get("carrier_sense_duration_ms", 0.0)),
                step=1.0,
            )
            lora_payload_bytes = st.number_input(
                "payload bytes",
                min_value=0,
                value=int(saved_base.get("lora_payload_bytes", 16)),
                step=1,
            )
        with lora_col_b:
            lora_spreading_factor = st.number_input(
                "spreading factor",
                min_value=5,
                max_value=12,
                value=int(saved_base.get("lora_spreading_factor", 7)),
                step=1,
            )
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
        try:
            airtime_ms = calculate_lora_airtime_ms(lora_config)
            low_data_rate_optimize = resolve_low_data_rate_optimize(lora_config)
            airtime_cols = st.columns(4)
            airtime_cols[0].metric("LoRa airtime", f"{airtime_ms:.3f} ms")
            airtime_cols[1].metric("symbol time", f"{symbol_duration_ms(lora_config):.3f} ms")
            airtime_cols[2].metric("LDRO", "on" if low_data_rate_optimize else "off")
            airtime_cols[3].metric(
                "used in simulation",
                "yes" if simulation_mode == "per_measurement" else "reference",
            )
            if simulation_mode != "per_measurement":
                st.caption(
                    "simulation modeがstandardの場合、LoRa airtimeは確認用です。"
                    "per_measurementでは送信時間として使用します。"
                )
        except ValueError as exc:
            airtime_ms = None
            st.error(f"LoRa airtimeを計算できません: {exc}")

        plot_settings = render_plot_settings(saved_plot)

        k_values = build_k_values(k_start, k_stop, k_step)
        total_runs = len(k_values) * int(runs_per_k)
        st.info(
            f"{graph_type}: K={len(k_values)}点, "
            f"runs per K={int(runs_per_k)}, total runs={total_runs}"
        )

        submitted = st.form_submit_button("ジョブを追加", type="primary")

    if submitted:
        if interval_end_ms <= interval_start_ms:
            st.error("interval end msはinterval start msより大きくしてください。")
            return
        if not k_values:
            st.error("K範囲からK値を作成できません。")
            return

        params = {
            "coupling_function": coupling_function,
            "k_start": k_start,
            "k_stop": k_stop,
            "k_step": k_step,
            "k_values": k_values,
            "runs_per_k": int(runs_per_k),
            "interval_start_ms": interval_start_ms,
            "interval_end_ms": interval_end_ms,
            "per_method": "interval_packet_error_rate",
            "plot_settings": plot_settings,
            "simulation_base": {
                "duration_ms": simulation_duration_ms,
                "seed": int(seed),
                "device_count": int(device_count),
                "cycle_time": int(cycle_time),
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
        st.success("ジョブを追加しました。")
        st.code(str(job.path), language="text")
        with st.spinner("シミュレーション、SQLite保存、集計、PDF描画を実行しています..."):
            result = run_interval_per_vs_k_job(job.path)
        st.success("ジョブが完了しました。")
        st.code(str(result["output"]), language="text")


def render_job_status_page() -> None:
    st.header("ジョブ確認")
    jobs = list_graph_jobs()
    if not jobs:
        st.info("ジョブはまだありません。")
        return

    for job in jobs:
        with st.container(border=True):
            cols = st.columns([2, 1, 1, 1])
            cols[0].markdown(f"**{job.graph_id}**")
            cols[0].caption(format_graph_key(job.graph_key))
            cols[1].metric("status", job.status)
            cols[2].metric("runs", f"{job.completed_runs}/{job.total_runs}")
            cols[3].metric("aggregate", job.aggregate_count)
            st.code(str(job.path), language="text")
            st.caption(
                "中止処理の実行部分は次段階で接続します。"
                "仕様では中止時に実行中runとgraph folderを完全削除します。"
            )
            if job.status == "queued":
                if st.button("実行", key=f"run_{job.graph_id}"):
                    with st.spinner("ジョブを実行しています..."):
                        result = run_interval_per_vs_k_job(job.path)
                    st.success("ジョブが完了しました。")
                    st.code(str(result["output"]), language="text")
                    st.rerun()


def render_results_page() -> None:
    st.header("結果・グラフ確認")
    jobs = list_graph_jobs()
    completed_or_known = [job for job in jobs if job.status != "cancelled"]
    if not completed_or_known:
        st.info("表示できるgraph folderはありません。")
        return

    labels = [
        f"{job.graph_id} / {job.graph_type} / {format_graph_key(job.graph_key)}"
        for job in completed_or_known
    ]
    selected = st.selectbox("graph folder", labels)
    job = completed_or_known[labels.index(selected)]
    manifest = read_json(job.path / "manifest.json")
    requests = read_json(job.path / "requests.json")

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("status", job.status)
    col_b.metric("total runs", job.total_runs)
    col_c.metric("aggregate sets", job.aggregate_count)

    st.subheader("パラメーター範囲")
    params = requests.get("params", manifest.get("input", {}))
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
        }
    )

    st.subheader("集計済みデータ")
    aggregate_df = read_aggregate_interval_per(job.path / "graph_data.sqlite")
    if aggregate_df.empty:
        st.info("集計済みデータはまだありません。")
    else:
        st.dataframe(aggregate_df, use_container_width=True, hide_index=True)

    output_path = representative_pdf_path(job.path)
    if output_path is not None:
        st.subheader("代表PDF")
        st.code(str(output_path), language="text")
        st.download_button(
            "PDFをダウンロード",
            data=output_path.read_bytes(),
            file_name=output_path.name,
            mime="application/pdf",
        )

    st.subheader("再描画")
    st.caption(
        "通常の再描画は集計済みデータだけを使い、代表PDF/画像を上書きします。"
        "実際の描画処理は次段階で接続します。"
    )
    with st.form("plot_settings"):
        st.number_input("y min", value=0.0)
        st.number_input("y max", value=100.0)
        st.checkbox("error bar", value=True)
        st.form_submit_button("代表PDFを上書き再描画")

    st.subheader("保存場所")
    st.code(str(job.path), language="text")


    st.subheader("Data delete")
    st.warning("The selected graph folder will be permanently deleted.")
    delete_disabled = job.status in {
        "running_simulations",
        "running_analysis",
        "rendering_graph",
        "cancel_requested",
    }
    if delete_disabled:
        st.info("Running jobs cannot be deleted from this page. Cancel them on the job page.")
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


def render_management_page() -> None:
    st.header("その他管理")
    overview = get_storage_overview()
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("graph folders", overview["job_count"])
    col_b.metric("SQLite files", overview["sqlite_count"])
    col_c.metric("raw dirs", overview["raw_dir_count"])

    st.subheader("保存形式")
    st.code(
        "\n".join(
            [
                "outputs/graph_runs/<graph_type>/<graph_id>/",
                "  manifest.json",
                "  status.json",
                "  requests.json",
                "  graph_data.sqlite",
                "  raw/runs/",
                "  figures/",
                "  logs/",
            ]
        ),
        language="text",
    )

    st.subheader("サーバー環境")
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
            "used_percent": round((disk.used / disk.total) * 100, 1)
            if disk.total
            else None,
        },
        "disk_free_label": format_bytes(disk.free),
    }


def get_memory_status() -> dict[str, object]:
    if platform.system().lower() != "windows":
        return {
            "available": False,
            "reason": "memory status is implemented for Windows in this GUI",
        }

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


def render_plot_settings(saved_plot: dict[str, object]) -> dict[str, object]:
    with st.expander("Plot settings", expanded=False):
        axis_col, font_col, style_col = st.columns(3)
        with axis_col:
            x_auto = st.checkbox(
                "x range auto",
                value=saved_plot.get("xlim_min") is None
                and saved_plot.get("xlim_max") is None,
            )
            xlim_min = st.number_input(
                "x min",
                value=float(saved_plot.get("xlim_min") or 0.0),
                disabled=x_auto,
            )
            xlim_max = st.number_input(
                "x max",
                value=float(saved_plot.get("xlim_max") or 20.0),
                disabled=x_auto,
            )
            y_auto = st.checkbox(
                "y range auto",
                value=saved_plot.get("ylim_min") is None
                and saved_plot.get("ylim_max") is None,
            )
            ylim_min = st.number_input(
                "y min",
                value=float(
                    saved_plot.get("ylim_min")
                    if saved_plot.get("ylim_min") is not None
                    else 0.0
                ),
                disabled=y_auto,
            )
            ylim_max = st.number_input(
                "y max",
                value=float(
                    saved_plot.get("ylim_max")
                    if saved_plot.get("ylim_max") is not None
                    else 100.0
                ),
                disabled=y_auto,
            )
        with font_col:
            figure_width = st.number_input(
                "figure width",
                min_value=1.0,
                value=float(saved_plot.get("figure_width", 8.0)),
                step=0.5,
            )
            figure_height = st.number_input(
                "figure height",
                min_value=1.0,
                value=float(saved_plot.get("figure_height", 5.0)),
                step=0.5,
            )
            font_size_label = st.number_input(
                "label font size",
                min_value=1,
                value=int(saved_plot.get("font_size_label", 12)),
                step=1,
            )
            font_size_ticks = st.number_input(
                "tick font size",
                min_value=1,
                value=int(saved_plot.get("font_size_ticks", 10)),
                step=1,
            )
            font_size_title = st.number_input(
                "title font size",
                min_value=1,
                value=int(saved_plot.get("font_size_title", 12)),
                step=1,
            )
        with style_col:
            marker_options = ["o", "s", "^", "D", "x", "+", "."]
            line_style_options = ["-", "--", "-.", ":", "None"]
            marker = st.selectbox(
                "marker",
                marker_options,
                index=select_index(marker_options, str(saved_plot.get("marker", "o"))),
            )
            marker_size = st.number_input(
                "marker size",
                min_value=0.0,
                value=float(saved_plot.get("marker_size", 6.0)),
                step=0.5,
            )
            line_style = st.selectbox(
                "line style",
                line_style_options,
                index=select_index(
                    line_style_options,
                    str(saved_plot.get("line_style", "-")),
                ),
            )
            line_width = st.number_input(
                "line width",
                min_value=0.0,
                value=float(saved_plot.get("line_width", 1.5)),
                step=0.25,
            )
            show_error_bars = st.checkbox(
                "show error bars",
                value=bool(saved_plot.get("show_error_bars", True)),
            )
            error_bar_capsize = st.number_input(
                "error bar capsize",
                min_value=0.0,
                value=float(saved_plot.get("error_bar_capsize", 4.0)),
                step=0.5,
            )
            show_title = st.checkbox(
                "show title",
                value=bool(saved_plot.get("show_title", True)),
            )
            show_grid = st.checkbox(
                "show grid",
                value=bool(saved_plot.get("show_grid", True)),
            )
            save_dpi = st.number_input(
                "save dpi",
                min_value=72,
                value=int(saved_plot.get("save_dpi", 300)),
                step=50,
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
        "save_dpi": int(save_dpi),
    }


def load_last_interval_per_vs_k_params() -> dict[str, object]:
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


def save_last_interval_per_vs_k_params(params: dict[str, object]) -> None:
    LAST_INTERVAL_PER_VS_K_PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_INTERVAL_PER_VS_K_PARAMS_PATH.write_text(
        json.dumps(params, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def deep_update(target: dict[str, object], source: dict[str, object]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_update(target[key], value)  # type: ignore[arg-type]
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


def read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_aggregate_interval_per(db_path: Path) -> pd.DataFrame:
    if not db_path.exists():
        return pd.DataFrame()
    with sqlite3.connect(db_path) as conn:
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
    with sqlite3.connect(db_path) as conn:
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


if __name__ == "__main__":
    main()
