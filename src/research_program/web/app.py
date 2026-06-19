from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import BytesIO
from itertools import product
import json
import math
import os
from pathlib import Path
import shutil
import sys
import subprocess
import tempfile
import time
from typing import Any
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import matplotlib.pyplot as plt
import pandas as pd
from PIL import Image
import streamlit as st

from research_program.config.loader import load_toml
from research_program.config.paths import resolve_project_path
from research_program.io.cleanup import (
    CleanupResult,
    cleanup_experiment_outputs,
)
from research_program.io.data_contract import RunDataContract, load_data_contract
from research_program.io.figures import (
    FigureAsset,
    convert_raster_image,
    discover_figures,
    figures_to_frame,
    original_mime_type,
    read_original_bytes,
)
from research_program.io.run_store import (
    RunRecord,
    discover_runs_with_index,
    filter_records,
    records_to_frame,
)
from research_program.plotting.phase_gap import (
    DEFAULT_Y_COLUMN,
    build_phase_gap_error_figure,
    figure_to_bytes,
)
from research_program.plotting.jobs import (
    create_graph_creation_job,
    load_graph_creation_job_statuses,
)
from research_program.config.plot_config import (
    AGGREGATED_PHASE_GAP_ERROR_OVERLAY_PLOT_CONFIG,
    AGGREGATED_PHASE_GAP_ERROR_PLOT_CONFIG,
    COMPARE_PER_BY_DEVICES_INTERVAL_CONFIG,
    CONVERGENCE_ANALYSIS_CONFIG,
    PER_ALIGNED_PLOT_CONFIG,
    PER_PLOT_CONFIG,
    PHASE_GAP_ERROR_PLOT_CONFIG,
    VISUALIZE_PHASE_DIFF_CONFIG,
)
from research_program.simulation.runner import (
    SimulationRequest,
    effective_carrier_sense_duration_for_request,
    fixed_start_times_for_request,
    lora_airtime_config_from_request,
    lora_airtime_ms_for_request,
    normalize_simulation_tags,
    request_from_config,
    resolve_max_workers,
    run_simulation_request,
)
from research_program.simulation.jobs import (
    create_simulation_job,
    load_simulation_job_statuses,
)
from research_program.simulation.range_generators import (
    generate_even_interval_start_times,
    parse_start_times_text,
)


DEFAULT_WEB_CONFIG = Path("configs/web/default.toml")
LAST_SIMULATION_REQUEST_PATH = PROJECT_ROOT / "outputs" / "reports" / "last_simulation_request.json"
COUPLING_FUNCTION_OPTIONS = ["KURAMOTO", "LINEAR", "NewSIN", "NONE"]

SWEEP_PARAMETER_SPECS: dict[str, dict[str, Any]] = {
    "coupling_strength": {
        "label": "結合強度(Coupling strength)",
        "type": "int",
        "step": 10,
    },
    "strength_ratio": {
        "label": "強度倍率(Strength ratio)",
        "type": "float",
        "step": 0.0001,
        "format": "%.8f",
    },
    "cycle_time": {
        "label": "周期時間[ms](Cycle time [ms])",
        "type": "int",
        "min": 1,
        "step": 1000,
    },
    "listening_rate": {
        "label": "待機率[%](Listening rate [%])",
        "type": "int",
        "min": 1,
        "max": 99,
        "step": 1,
    },
    "device_count": {
        "label": "デバイス数(Devices)",
        "type": "int",
        "min": 1,
        "step": 1,
    },
    "duration": {
        "label": "シミュレーション時間[ms](Duration [ms])",
        "type": "int",
        "min": 1,
        "step": 30000,
    },
    "start_step": {
        "label": "開始時刻ステップ[ms](Start step [ms])",
        "type": "int",
        "min": 1,
        "step": 1,
    },
    "start_step_count": {
        "label": "開始時刻ステップ数(Start step count)",
        "type": "int",
        "min": 1,
        "step": 1,
    },
    "fixed_start_interval": {
        "label": "固定開始間隔[ms](Fixed start interval [ms])",
        "type": "int",
        "min": 0,
        "step": 1,
    },
    "fixed_start_offset": {
        "label": "固定開始オフセット[ms](Fixed start offset [ms])",
        "type": "int",
        "min": 0,
        "step": 1,
    },
    "carrier_sense_duration_ms": {
        "label": "キャリアセンス時間[ms](Carrier-sense duration [ms])",
        "type": "float",
        "min": 0.0,
        "step": 1.0,
    },
    "lora_payload_bytes": {
        "label": "LoRaペイロード[bytes](LoRa payload [bytes])",
        "type": "int",
        "min": 0,
        "step": 1,
    },
    "lora_spreading_factor": {
        "label": "LoRa SF(LoRa spreading factor)",
        "type": "int",
        "min": 6,
        "max": 12,
        "step": 1,
    },
    "lora_bandwidth_hz": {
        "label": "LoRa帯域[Hz](LoRa bandwidth [Hz])",
        "type": "int",
        "min": 1,
        "step": 125000,
    },
    "lora_coding_rate_denominator": {
        "label": "LoRa符号化率 denominator(LoRa coding-rate denominator)",
        "type": "int",
        "min": 5,
        "max": 8,
        "step": 1,
    },
    "lora_preamble_symbols": {
        "label": "LoRaプリアンブル[symbols](LoRa preamble [symbols])",
        "type": "int",
        "min": 0,
        "step": 1,
    },
}

COMMON_SWEEP_FIELDS = [
    "coupling_strength",
    "strength_ratio",
    "cycle_time",
    "listening_rate",
    "device_count",
    "duration",
]
RANDOM_START_SWEEP_FIELDS = ["start_step", "start_step_count"]
FIXED_START_SWEEP_FIELDS = ["fixed_start_interval", "fixed_start_offset"]
PER_MEASUREMENT_SWEEP_FIELDS = [
    "carrier_sense_duration_ms",
    "lora_payload_bytes",
    "lora_spreading_factor",
    "lora_bandwidth_hz",
    "lora_coding_rate_denominator",
    "lora_preamble_symbols",
]

Y_COLUMN_LABELS = {
    DEFAULT_Y_COLUMN: "平均絶対位相ギャップ誤差率(Mean absolute phase-gap error ratio)",
    "mean_abs_diff_from_ideal_phase_gap": "平均絶対位相ギャップ誤差(Mean absolute phase-gap error)",
}

RUN_COLUMN_LABELS = {
    "run_id": "run ID(Run ID)",
    "coupling_function": "結合関数(Coupling function)",
    "coupling_strength": "結合強度(Coupling strength)",
    "strength_ratio": "強度倍率(Strength ratio)",
    "cycle_time": "周期時間(Cycle time)",
    "listening_rate": "待機率(Listening rate)",
    "device_count": "デバイス数(Device count)",
    "simulation_mode": "シミュレーションモード(Simulation mode)",
    "carrier_sense_duration_ms": "キャリアセンス時間[ms](Carrier-sense [ms])",
    "transmission_time_ms": "送信時間[ms](Transmission time [ms])",
    "tags": "タグ(Tags)",
    "status": "状態(Status)",
    "path": "パス(Path)",
}

FIGURE_COLUMN_LABELS = {
    "figure_scope": "データ区分(Data scope)",
    "graph_type": "グラフ種類(Graph type)",
    "graph_description": "説明(Description)",
    "name": "ファイル名(Name)",
    "relative_path": "相対パス(Relative path)",
    "extension": "拡張子(Extension)",
    "source_root": "探索元(Source root)",
    "size_kb": "サイズ[KB](Size [KB])",
    "path": "パス(Path)",
}

CONTRACT_COLUMN_LABELS = {
    "file": "ファイル(File)",
    "column": "列(Column)",
    "type": "型(Type)",
    "required": "必須(Required)",
    "unit": "単位(Unit)",
    "aliases": "別名(Aliases)",
    "description": "説明(Description)",
}

CLEANUP_COLUMN_LABELS = {
    "path": "パス(Path)",
    "kind": "種類(Kind)",
    "size_kb": "サイズ[KB](Size [KB])",
}

NUMERIC_FIELD_LABELS = {
    "coupling_strength": "結合強度(Coupling strength)",
    "strength_ratio": "強度倍率(Strength ratio)",
    "cycle_time": "周期時間(Cycle time)",
    "listening_rate": "待機率(Listening rate)",
}

CLEANUP_TARGET_LABELS = {
    "runs": "runデータ(Runs)",
    "aggregated": "集約データ(Aggregated)",
    "figures": "画像(Figures)",
    "reports": "レポート(Reports)",
    "raw_real": "実機元データ(Raw real-device data)",
    "raw_simulation": "シミュレーション元データ(Raw simulation data)",
}

PREPROCESS_COMMANDS = {
    "calculate-cycle-data": {
        "label": "周期データ作成(Calculate cycle data)",
        "output": "data/runs/*/calculated_Cycle_data.csv",
    },
    "calculate-phase-gap-error": {
        "label": "位相ギャップ誤差作成(Calculate phase-gap error)",
        "output": "data/runs/*/phase_gap_error.csv",
    },
    "aggregate-phase-gap-error": {
        "label": "位相ギャップ誤差集約(Aggregate phase-gap error)",
        "output": "data/aggregated/*.csv",
    },
}

GRAPH_CREATION_COMMANDS = {
    "plot-phase-diff": {
        "label": "位相差グラフ(Phase-difference graphs)",
        "output": "outputs/figures/phase_diff_graphs/*.pdf",
    },
    "plot-phase-gap-error": {
        "label": "位相ギャップ誤差グラフ(Phase-gap error graphs)",
        "output": "outputs/figures/phase_gap_error_graphs/*.pdf",
    },
    "plot-per": {
        "label": "PERグラフ(PER graphs)",
        "output": "outputs/figures/per_graphs/*.pdf",
    },
    "plot-per-aligned": {
        "label": "基準周期そろえPERグラフ(Aligned PER graphs)",
        "output": "outputs/figures/per_aligned_graphs/*",
    },
    "compare-per": {
        "label": "台数・送信間隔別PER比較(PER comparison by devices and interval)",
        "output": "outputs/figures/compare_per_graphs/*.pdf",
    },
    "plot-aggregated-phase-gap-error": {
        "label": "集約位相ギャップ誤差グラフ(Aggregated phase-gap error graphs)",
        "output": "outputs/figures/aggregated_stats_graphs/*.pdf",
    },
    "plot-aggregated-phase-gap-error-overlay": {
        "label": "集約位相ギャップ誤差重ね描き(Aggregated phase-gap error overlay)",
        "output": "outputs/figures/aggregated_stats_overlay_graphs/*",
    },
    "plot-convergence-summary": {
        "label": "収束サマリーグラフ(Convergence summary graph)",
        "output": "outputs/figures/convergence_graphs/*",
    },
}

PLOT_CONFIG_BY_GRAPH_COMMAND: dict[str, tuple[str, Any]] = {
    "plot-phase-diff": ("VISUALIZE_PHASE_DIFF_CONFIG", VISUALIZE_PHASE_DIFF_CONFIG),
    "plot-phase-gap-error": ("PHASE_GAP_ERROR_PLOT_CONFIG", PHASE_GAP_ERROR_PLOT_CONFIG),
    "plot-per": ("PER_PLOT_CONFIG", PER_PLOT_CONFIG),
    "plot-per-aligned": ("PER_ALIGNED_PLOT_CONFIG", PER_ALIGNED_PLOT_CONFIG),
    "compare-per": ("COMPARE_PER_BY_DEVICES_INTERVAL_CONFIG", COMPARE_PER_BY_DEVICES_INTERVAL_CONFIG),
    "plot-aggregated-phase-gap-error": (
        "AGGREGATED_PHASE_GAP_ERROR_PLOT_CONFIG",
        AGGREGATED_PHASE_GAP_ERROR_PLOT_CONFIG,
    ),
    "plot-aggregated-phase-gap-error-overlay": (
        "AGGREGATED_PHASE_GAP_ERROR_OVERLAY_PLOT_CONFIG",
        AGGREGATED_PHASE_GAP_ERROR_OVERLAY_PLOT_CONFIG,
    ),
    "plot-convergence-summary": ("CONVERGENCE_ANALYSIS_CONFIG", CONVERGENCE_ANALYSIS_CONFIG),
}

PLOT_RANGE_FIELD_PAIRS = [
    ("xlim_min", "xlim_max", "x軸範囲(X-axis range)"),
    ("ylim_min", "ylim_max", "y軸範囲(Y-axis range)"),
    ("per_ylim_min", "per_ylim_max", "PER y軸範囲(PER Y-axis range)"),
    ("per_change_ylim_min", "per_change_ylim_max", "PER変化量 y軸範囲(PER-change Y-axis range)"),
    ("ylim_left_min", "ylim_left_max", "左y軸範囲(Left Y-axis range)"),
    ("ylim_right_min", "ylim_right_max", "右y軸範囲(Right Y-axis range)"),
]

GRAPH_TYPE_BY_OUTPUT_DIR = {
    "phase_diff_graphs": GRAPH_CREATION_COMMANDS["plot-phase-diff"]["label"],
    "phase_gap_error_graphs": GRAPH_CREATION_COMMANDS["plot-phase-gap-error"]["label"],
    "per_graphs": GRAPH_CREATION_COMMANDS["plot-per"]["label"],
    "per_aligned_graphs": GRAPH_CREATION_COMMANDS["plot-per-aligned"]["label"],
    "compare_per_graphs": GRAPH_CREATION_COMMANDS["compare-per"]["label"],
    "aggregated_stats_graphs": GRAPH_CREATION_COMMANDS["plot-aggregated-phase-gap-error"]["label"],
    "aggregated_stats_overlay_graphs": GRAPH_CREATION_COMMANDS["plot-aggregated-phase-gap-error-overlay"]["label"],
    "convergence_graphs": GRAPH_CREATION_COMMANDS["plot-convergence-summary"]["label"],
}

FIGURE_SCOPE_LABELS = {
    "single": "単体データ(Single data)",
    "multiple": "複数データ/平均・集約(Multiple data / average)",
    "other": "その他(Other)",
}

FIGURE_SCOPE_BY_OUTPUT_DIR = {
    "phase_diff_graphs": "single",
    "phase_gap_error_graphs": "single",
    "per_graphs": "single",
    "per_aligned_graphs": "single",
    "compare_per_graphs": "multiple",
    "aggregated_stats_graphs": "multiple",
    "aggregated_stats_overlay_graphs": "multiple",
    "convergence_graphs": "multiple",
}

GRAPH_DESCRIPTION_BY_OUTPUT_DIR = {
    "phase_diff_graphs": "1つのrunの送信時刻から位相差を表示(Uses one run to show phase differences)",
    "phase_gap_error_graphs": "1つのrunの位相ギャップ誤差を表示(Uses one run to show phase-gap error)",
    "per_graphs": "1つのrunのPERを表示(Uses one run to show PER)",
    "per_aligned_graphs": "1つのrunのPERを基準周期にそろえて表示(Uses one run aligned to a base cycle)",
    "compare_per_graphs": "複数runを台数・送信間隔で平均して比較(Compares averages by devices and interval)",
    "aggregated_stats_graphs": "複数runの位相ギャップ誤差統計を表示(Uses aggregated phase-gap error statistics)",
    "aggregated_stats_overlay_graphs": "複数runの集約統計を重ね描き(Overlays aggregated statistics)",
    "convergence_graphs": "複数runの集約統計から収束傾向を表示(Uses aggregated statistics for convergence)",
}

FIGURE_SCOPE_SORT_ORDER = {
    "single": 0,
    "multiple": 1,
    "other": 2,
}

GRAPH_TABLE_COLUMN_LABELS = {
    "command": "コマンド(Command)",
    "scope": "データ区分(Data scope)",
    "label": "種類(Type)",
    "description": "説明(Description)",
    "output": "出力先(Output)",
}

COMMAND_RESULT_COLUMN_LABELS = {
    "command": "コマンド(Command)",
    "status": "状態(Status)",
    "message": "メッセージ(Message)",
}


def _cache_token(name: str) -> int:
    return int(st.session_state.get(f"{name}_refresh_token", 0))


def _bump_cache_token(name: str) -> None:
    key = f"{name}_refresh_token"
    st.session_state[key] = int(st.session_state.get(key, 0)) + 1


@st.cache_data(show_spinner=False)
def _load_runtime(web_config_path: str | Path = DEFAULT_WEB_CONFIG) -> tuple[dict[str, Any], RunDataContract]:
    web_config = load_toml(web_config_path)
    contract = load_data_contract(web_config["paths"]["data_format_config"])
    return web_config, contract


@st.cache_data(show_spinner="run一覧を読み込み中(Loading runs)...", ttl=10)
def _discover_records_cached(
    runs_dirs: tuple[str, ...],
    data_format_config: str,
    refresh_token: int,
    force_rescan: bool,
) -> list[RunRecord]:
    contract = load_data_contract(data_format_config)
    return discover_runs_with_index(runs_dirs, contract, force_rescan=force_rescan)


def _discover_records(
    web_config: dict[str, Any],
    contract: RunDataContract,
    refresh_token: int = 0,
    force_rescan: bool = False,
) -> list[RunRecord]:
    return _discover_records_cached(
        tuple(str(path) for path in web_config["paths"].get("runs_dirs", [])),
        str(web_config["paths"]["data_format_config"]),
        refresh_token,
        force_rescan,
    )


@st.cache_data(show_spinner="画像一覧を読み込み中(Loading figures)...", ttl=10)
def _discover_figures_cached(
    figure_dirs: tuple[str, ...],
    extensions: tuple[str, ...],
    refresh_token: int,
) -> list[FigureAsset]:
    return discover_figures(figure_dirs, extensions=extensions)


def _discover_figures_for_web(web_config: dict[str, Any], refresh_token: int = 0) -> list[FigureAsset]:
    figure_config = web_config.get("figures", {})
    return _discover_figures_cached(
        tuple(str(path) for path in web_config["paths"].get("figure_dirs", [])),
        tuple(str(extension) for extension in figure_config.get("extensions", [])),
        refresh_token,
    )


def _all_tags(records: list[RunRecord]) -> list[str]:
    return sorted({tag for record in records for tag in record.tags})


def _numeric_values(records: list[RunRecord], field_name: str) -> list[float]:
    values: list[float] = []
    for record in records:
        value = record.metadata.get(field_name)
        if value is None:
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return values


def _filter_controls(
    records: list[RunRecord],
    web_config: dict[str, Any],
    key_prefix: str = "runs",
) -> list[RunRecord]:
    filter_config = web_config.get("filters", {})
    coupling_options = sorted(
        {
            str(record.metadata.get("coupling_function"))
            for record in records
            if record.metadata.get("coupling_function") is not None
        }
    )

    selected_coupling_functions = st.multiselect(
        "結合関数(Coupling function)",
        coupling_options,
        default=coupling_options,
        key=f"{key_prefix}_coupling_functions",
    )

    numeric_ranges: dict[str, tuple[float, float]] = {}
    for field_name in filter_config.get("numeric_fields", []):
        values = _numeric_values(records, field_name)
        if not values:
            continue
        min_value = min(values)
        max_value = max(values)
        if min_value == max_value:
            continue
        selected_range = st.slider(
            NUMERIC_FIELD_LABELS.get(field_name, f"{field_name}({field_name})"),
            min_value=float(min_value),
            max_value=float(max_value),
            value=(float(min_value), float(max_value)),
            key=f"{key_prefix}_{field_name}_range",
        )
        numeric_ranges[field_name] = (float(selected_range[0]), float(selected_range[1]))

    tag_options = _all_tags(records)
    selected_tags = st.multiselect("タグ(Tags)", tag_options, key=f"{key_prefix}_tags")

    return filter_records(
        records=records,
        coupling_functions=selected_coupling_functions,
        numeric_ranges=numeric_ranges,
        required_tags=selected_tags,
    )


def _render_runs_tab(records: list[RunRecord], web_config: dict[str, Any]) -> None:
    filtered_records = _filter_controls(records, web_config)
    filtered_df = records_to_frame(filtered_records)

    st.metric("実行数(Runs)", len(filtered_records))
    if filtered_df.empty:
        st.dataframe(pd.DataFrame())
        return

    visible_columns = [
        column
        for column in [
            "run_id",
            "coupling_function",
            "coupling_strength",
            "strength_ratio",
            "cycle_time",
            "listening_rate",
            "device_count",
            "simulation_mode",
            "carrier_sense_duration_ms",
            "transmission_time_ms",
            "tags",
            "status",
            "path",
        ]
        if column in filtered_df.columns
    ]
    st.dataframe(
        filtered_df[visible_columns].rename(columns=RUN_COLUMN_LABELS),
        width="stretch",
        hide_index=True,
    )

    with st.expander("簡易グラフ(Quick graph)", expanded=False):
        y_column = st.selectbox(
            "Y軸の列(Y column)",
            [
                DEFAULT_Y_COLUMN,
                "mean_abs_diff_from_ideal_phase_gap",
            ],
            format_func=lambda value: Y_COLUMN_LABELS.get(value, value),
        )
        max_runs = st.number_input("グラフに描画する最大run数(Max graph runs)", min_value=1, value=50)
        output_format = st.selectbox(
            "グラフ形式(Graph format)",
            web_config.get("figures", {}).get("generated_graph_formats", ["pdf", "png", "svg"]),
        )

        if not st.button("簡易グラフを作成(Create quick graph)"):
            st.caption("ボタンを押した時だけ phase_gap_error.csv を読み込みます(Reads phase_gap_error.csv only when the button is pressed).")
            return

        fig, used_count = build_phase_gap_error_figure(
            filtered_records,
            y_column=y_column,
            max_runs=int(max_runs),
        )
        if fig is None:
            st.warning("フィルタ後のrunに phase_gap_error.csv が見つかりませんでした(phase_gap_error.csv was not found in the filtered runs).")
            return

        st.metric("描画したrun数(Plotted runs)", used_count)
        st.pyplot(fig, clear_figure=False)
        graph_bytes, mime, filename = figure_to_bytes(fig, output_format)
        st.download_button(
            "グラフをダウンロード(Download graph)",
            data=graph_bytes,
            file_name=filename,
            mime=mime,
        )
        plt.close(fig)


def _manual_tags_text(tags: tuple[str, ...]) -> str:
    manual_tags = [
        tag
        for tag in tags
        if not tag.strip().endswith("dai") or not tag.strip()[:-3].isdigit()
    ]
    manual_tags = [
        tag
        for tag in manual_tags
        if tag not in {"start_random", "start_fixed", "mode_standard", "mode_per_measurement"}
    ]
    return ";".join(manual_tags)


def _display_project_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _simulation_request_to_dict(request: SimulationRequest) -> dict[str, Any]:
    return {
        "num_runs": request.num_runs,
        "seed": request.seed,
        "coupling_function": request.coupling_function,
        "coupling_strength": request.coupling_strength,
        "strength_ratio": request.strength_ratio,
        "cycle_time": request.cycle_time,
        "listening_rate": request.listening_rate,
        "device_count": request.device_count,
        "duration": request.duration,
        "start_step_count": request.start_step_count,
        "start_step": request.start_step,
        "tags": list(request.tags),
        "output_root": _display_project_path(request.output_root),
        "max_workers": request.max_workers,
        "start_timing_mode": request.start_timing_mode,
        "fixed_start_times": list(request.fixed_start_times),
        "fixed_start_interval": request.fixed_start_interval,
        "fixed_start_offset": request.fixed_start_offset,
        "simulation_mode": request.simulation_mode,
        "carrier_sense_duration_ms": request.carrier_sense_duration_ms,
        "lora_payload_bytes": request.lora_payload_bytes,
        "lora_spreading_factor": request.lora_spreading_factor,
        "lora_bandwidth_hz": request.lora_bandwidth_hz,
        "lora_coding_rate_denominator": request.lora_coding_rate_denominator,
        "lora_preamble_symbols": request.lora_preamble_symbols,
        "lora_explicit_header": request.lora_explicit_header,
        "lora_crc_enabled": request.lora_crc_enabled,
        "lora_low_data_rate_optimize": request.lora_low_data_rate_optimize,
    }


def _simulation_request_from_dict(data: dict[str, Any], fallback: SimulationRequest) -> SimulationRequest:
    return SimulationRequest(
        num_runs=int(data.get("num_runs", fallback.num_runs)),
        seed=int(data.get("seed", fallback.seed)),
        coupling_function=str(data.get("coupling_function", fallback.coupling_function)),
        coupling_strength=int(data.get("coupling_strength", fallback.coupling_strength)),
        strength_ratio=float(data.get("strength_ratio", fallback.strength_ratio)),
        cycle_time=int(data.get("cycle_time", fallback.cycle_time)),
        listening_rate=int(data.get("listening_rate", fallback.listening_rate)),
        device_count=int(data.get("device_count", fallback.device_count)),
        duration=int(data.get("duration", fallback.duration)),
        start_step_count=int(data.get("start_step_count", fallback.start_step_count)),
        start_step=int(data.get("start_step", fallback.start_step)),
        tags=tuple(str(tag) for tag in data.get("tags", fallback.tags)),
        output_root=resolve_project_path(data.get("output_root", _display_project_path(fallback.output_root))),
        max_workers=int(data.get("max_workers", fallback.max_workers)),
        start_timing_mode=str(data.get("start_timing_mode", fallback.start_timing_mode)),  # type: ignore[arg-type]
        fixed_start_times=tuple(int(value) for value in data.get("fixed_start_times", fallback.fixed_start_times)),
        fixed_start_interval=int(data.get("fixed_start_interval", fallback.fixed_start_interval)),
        fixed_start_offset=int(data.get("fixed_start_offset", fallback.fixed_start_offset)),
        simulation_mode=str(data.get("simulation_mode", fallback.simulation_mode)),  # type: ignore[arg-type]
        carrier_sense_duration_ms=float(data.get("carrier_sense_duration_ms", fallback.carrier_sense_duration_ms)),
        lora_payload_bytes=int(data.get("lora_payload_bytes", fallback.lora_payload_bytes)),
        lora_spreading_factor=int(data.get("lora_spreading_factor", fallback.lora_spreading_factor)),
        lora_bandwidth_hz=int(data.get("lora_bandwidth_hz", fallback.lora_bandwidth_hz)),
        lora_coding_rate_denominator=int(data.get("lora_coding_rate_denominator", fallback.lora_coding_rate_denominator)),
        lora_preamble_symbols=int(data.get("lora_preamble_symbols", fallback.lora_preamble_symbols)),
        lora_explicit_header=bool(data.get("lora_explicit_header", fallback.lora_explicit_header)),
        lora_crc_enabled=bool(data.get("lora_crc_enabled", fallback.lora_crc_enabled)),
        lora_low_data_rate_optimize=data.get("lora_low_data_rate_optimize", fallback.lora_low_data_rate_optimize),
    )


def _load_last_simulation_request(fallback: SimulationRequest) -> SimulationRequest:
    cached_request = st.session_state.get("last_simulation_request_defaults")
    if isinstance(cached_request, SimulationRequest):
        return cached_request

    if not LAST_SIMULATION_REQUEST_PATH.exists():
        return fallback

    try:
        data = json.loads(LAST_SIMULATION_REQUEST_PATH.read_text(encoding="utf-8"))
        request = _simulation_request_from_dict(data, fallback)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return fallback

    st.session_state["last_simulation_request_defaults"] = request
    return request


def _save_last_simulation_request(request: SimulationRequest) -> None:
    LAST_SIMULATION_REQUEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_SIMULATION_REQUEST_PATH.write_text(
        json.dumps(_simulation_request_to_dict(request), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    st.session_state["last_simulation_request_defaults"] = request


def _start_simulation_job(requests: list[SimulationRequest]) -> tuple[str, Path]:
    job_id, job_path = create_simulation_job(requests)
    log_path = job_path.with_suffix(".log")
    log_file = log_path.open("ab")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            [sys.executable, "-m", "research_program.simulation.jobs", str(job_path)],
            cwd=PROJECT_ROOT,
            env=_python_subprocess_env(),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
    finally:
        log_file.close()
    return job_id, job_path


def _parse_job_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _job_progress_text(status: dict[str, Any]) -> str:
    completed = int(status.get("completed_runs") or 0)
    total = int(status.get("total_runs") or 0)
    started_at = _parse_job_datetime(status.get("started_at") or status.get("created_at"))
    elapsed_seconds: float | None = None
    remaining_seconds: float | None = None
    finish_text = "計算中(Calculating)"
    if started_at is not None:
        elapsed_seconds = (datetime.now(timezone.utc) - started_at).total_seconds()
        if completed > 0 and total > completed:
            remaining_seconds = (elapsed_seconds / completed) * (total - completed)
            finish_at = datetime.now() + timedelta(seconds=remaining_seconds)
            finish_text = finish_at.strftime("%H:%M:%S")
        elif total > 0 and completed >= total:
            remaining_seconds = 0.0
            finish_text = "完了(Done)"

    current_run_id = str(status.get("current_run_id") or "")
    parts = [
        f"{completed}/{total} 完了(Completed)",
        f"経過(Elapsed): {_format_duration(elapsed_seconds)}",
        f"残り(ETA): {_format_duration(remaining_seconds)}",
        f"終了予測(Finish): {finish_text}",
    ]
    if current_run_id:
        parts.append(f"直近run(Latest run): {current_run_id}")
    return " | ".join(parts)


def _simulation_job_summary_frame(statuses: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for status in statuses:
        rows.append(
            {
                "ジョブID(Job ID)": status.get("job_id", ""),
                "状態(Status)": status.get("status", ""),
                "進捗(Progress)": f"{status.get('completed_runs', 0)}/{status.get('total_runs', 0)}",
                "条件数(Conditions)": status.get("total_conditions", 0),
                "更新時刻(Updated)": status.get("updated_at", ""),
                "完了時刻(Finished)": status.get("finished_at", ""),
            }
        )
    return pd.DataFrame(rows)


def _render_simulation_job_monitor() -> None:
    statuses = load_simulation_job_statuses(limit=20)
    active_statuses = [
        status
        for status in statuses
        if status.get("status") in {"queued", "running"}
    ]

    st.subheader("進行中ジョブ(Running jobs)")
    col_refresh, col_count = st.columns([1, 3])
    if col_refresh.button("ジョブ状況を更新(Refresh jobs)"):
        st.rerun()
    col_count.metric("進行中ジョブ数(Active jobs)", len(active_statuses))

    if not active_statuses:
        st.caption("進行中のシミュレーションジョブはありません(No running simulation jobs).")

    for status in active_statuses:
        job_id = str(status.get("job_id", ""))
        completed = int(status.get("completed_runs") or 0)
        total = int(status.get("total_runs") or 0)
        with st.container(border=True):
            st.write(f"**{job_id}**")
            st.progress(_progress_ratio(completed, total), text=_job_progress_text(status))
            st.caption(f"状態(Status): {status.get('status', '')} / PID: {status.get('pid', '')} / 更新(Updated): {status.get('updated_at', '')}")
            results = status.get("results") or []
            if results:
                st.dataframe(pd.DataFrame(results[-20:]), width="stretch", hide_index=True)

    with st.expander("最近のジョブ(Recent jobs)", expanded=bool(statuses)):
        if statuses:
            st.dataframe(_simulation_job_summary_frame(statuses), width="stretch", hide_index=True)
            selected_job_id = st.selectbox(
                "詳細を見るジョブ(Job details)",
                options=[str(status.get("job_id", "")) for status in statuses],
            )
            selected_status = next(
                (status for status in statuses if str(status.get("job_id", "")) == selected_job_id),
                None,
            )
            if selected_status is not None:
                if selected_status.get("error"):
                    st.error(str(selected_status["error"]).splitlines()[0])
                results = selected_status.get("results") or []
                if results:
                    st.dataframe(pd.DataFrame(results), width="stretch", hide_index=True)
        else:
            st.caption("まだジョブ履歴はありません(No job history yet).")


def _start_graph_creation_job(
    *,
    commands_to_run: list[str],
    selected_graph_commands: list[str],
    selected_target_records: list[RunRecord],
    all_run_count: int,
    env_overrides: dict[str, str],
    web_config: dict[str, Any],
) -> tuple[str, Path]:
    figure_config = web_config.get("figures", {})
    job_id, job_path = create_graph_creation_job(
        commands=commands_to_run,
        selected_graph_commands=selected_graph_commands,
        selected_run_paths=[str(record.path) for record in selected_target_records],
        all_run_count=all_run_count,
        env_overrides=env_overrides,
        figure_dirs=[str(path) for path in web_config["paths"].get("figure_dirs", [])],
        figure_extensions=[str(extension) for extension in figure_config.get("extensions", [])],
    )
    log_path = job_path.with_suffix(".log")
    log_file = log_path.open("ab")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            [sys.executable, "-m", "research_program.plotting.jobs", str(job_path)],
            cwd=PROJECT_ROOT,
            env=_python_subprocess_env(),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
    finally:
        log_file.close()
    return job_id, job_path


def _graph_job_progress_text(status: dict[str, Any]) -> str:
    completed = int(status.get("completed_commands") or 0)
    total = int(status.get("total_commands") or 0)
    started_at = _parse_job_datetime(status.get("started_at") or status.get("created_at"))
    elapsed_seconds: float | None = None
    remaining_seconds: float | None = None
    finish_text = "計算中(Calculating)"
    if started_at is not None:
        elapsed_seconds = (datetime.now(timezone.utc) - started_at).total_seconds()
        if completed > 0 and total > completed:
            remaining_seconds = (elapsed_seconds / completed) * (total - completed)
            finish_at = datetime.now() + timedelta(seconds=remaining_seconds)
            finish_text = finish_at.strftime("%H:%M:%S")
        elif total > 0 and completed >= total:
            remaining_seconds = 0.0
            finish_text = "完了(Done)"

    current_command = str(status.get("current_command") or "")
    parts = [
        f"{completed}/{total} コマンド完了(Commands completed)",
        f"対象run(Target runs): {status.get('selected_run_count', 0)}",
        f"経過(Elapsed): {_format_duration(elapsed_seconds)}",
        f"残り(ETA): {_format_duration(remaining_seconds)}",
        f"終了予測(Finish): {finish_text}",
    ]
    if current_command:
        parts.append(f"実行中(Current): {current_command}")
    return " | ".join(parts)


def _graph_creation_job_summary_frame(statuses: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for status in statuses:
        rows.append(
            {
                "ジョブID(Job ID)": status.get("job_id", ""),
                "状態(Status)": status.get("status", ""),
                "進捗(Progress)": f"{status.get('completed_commands', 0)}/{status.get('total_commands', 0)}",
                "対象run(Target runs)": status.get("selected_run_count", 0),
                "作成/更新画像(Generated/updated figures)": status.get("generated_or_updated_figures", 0),
                "更新時刻(Updated)": status.get("updated_at", ""),
                "完了時刻(Finished)": status.get("finished_at", ""),
            }
        )
    return pd.DataFrame(rows)


def _render_graph_creation_job_monitor() -> None:
    statuses = load_graph_creation_job_statuses(limit=20)
    active_statuses = [
        status
        for status in statuses
        if status.get("status") in {"queued", "running"}
    ]

    st.subheader("進行中のグラフ作成ジョブ(Running graph jobs)")
    col_refresh, col_count = st.columns([1, 3])
    if col_refresh.button("グラフジョブ状況を更新(Refresh graph jobs)"):
        st.rerun()
    col_count.metric("進行中ジョブ数(Active jobs)", len(active_statuses))

    if not active_statuses:
        st.caption("進行中のグラフ作成ジョブはありません(No running graph creation jobs).")

    for status in active_statuses:
        job_id = str(status.get("job_id", ""))
        completed = int(status.get("completed_commands") or 0)
        total = int(status.get("total_commands") or 0)
        with st.container(border=True):
            st.write(f"**{job_id}**")
            st.progress(_progress_ratio(completed, total), text=_graph_job_progress_text(status))
            st.caption(f"状態(Status): {status.get('status', '')} / PID: {status.get('pid', '')} / 更新(Updated): {status.get('updated_at', '')}")
            results = status.get("results") or []
            if results:
                st.dataframe(_command_result_frame(results), width="stretch", hide_index=True)

    with st.expander("最近のグラフ作成ジョブ(Recent graph jobs)", expanded=bool(statuses)):
        if statuses:
            st.dataframe(_graph_creation_job_summary_frame(statuses), width="stretch", hide_index=True)
            selected_job_id = st.selectbox(
                "詳細を見るジョブ(Job details)",
                options=[str(status.get("job_id", "")) for status in statuses],
                key="graph_job_details",
            )
            selected_status = next(
                (status for status in statuses if str(status.get("job_id", "")) == selected_job_id),
                None,
            )
            if selected_status is not None:
                if selected_status.get("error"):
                    st.error(str(selected_status["error"]).splitlines()[0])
                results = selected_status.get("results") or []
                if results:
                    st.dataframe(_command_result_frame(results), width="stretch", hide_index=True)
        else:
            st.caption("まだグラフ作成ジョブ履歴はありません(No graph job history yet).")


def _simulation_review_frame(request: SimulationRequest) -> pd.DataFrame:
    effective_tags = normalize_simulation_tags(
        tags=request.tags,
        device_count=request.device_count,
        start_timing_mode=request.start_timing_mode,
        simulation_mode=request.simulation_mode,
    )
    effective_workers = resolve_max_workers(request.num_runs, request.max_workers)
    requested_workers = "自動(Auto)" if request.max_workers <= 0 else str(request.max_workers)
    start_timing_mode = "ランダム(Random)" if request.start_timing_mode == "random" else "固定(Fixed)"
    simulation_mode = (
        "PER測定(PER measurement)"
        if request.simulation_mode == "per_measurement"
        else "標準(Standard)"
    )
    if request.start_timing_mode == "fixed":
        start_times = fixed_start_times_for_request(request)
        start_timing_detail = ",".join(str(value) for value in start_times)
    else:
        start_timing_detail = f"0..{request.start_step_count * request.start_step}, step={request.start_step}"

    rows = [
        ("run数(Runs)", request.num_runs),
        ("乱数シード(Seed)", request.seed),
        ("結合関数(Coupling function)", request.coupling_function),
        ("結合強度(Coupling strength)", request.coupling_strength),
        ("強度倍率(Strength ratio)", request.strength_ratio),
        ("周期時間[ms](Cycle time [ms])", request.cycle_time),
        ("待機率[%](Listening rate [%])", request.listening_rate),
        ("デバイス数(Devices)", request.device_count),
        ("シミュレーション時間[ms](Duration [ms])", request.duration),
        ("シミュレーションモード(Simulation mode)", simulation_mode),
        ("開始タイミング(Start timing)", start_timing_mode),
        ("開始タイミング詳細(Start timing detail)", start_timing_detail),
        ("手動タグ(Manual tags)", ";".join(request.tags)),
        ("実際に使うタグ(Effective tags)", ";".join(effective_tags)),
        ("出力先(Output runs dir)", _display_project_path(request.output_root)),
        ("指定ワーカー数(Requested max workers)", requested_workers),
        ("実際に使うワーカー数(Effective max workers)", effective_workers),
    ]
    if request.start_timing_mode == "random":
        rows.insert(11, ("開始時刻ステップ数(Start step count)", request.start_step_count))
        rows.insert(11, ("開始時刻ステップ[ms](Start step [ms])", request.start_step))
    if request.simulation_mode == "per_measurement":
        lora_config = lora_airtime_config_from_request(request)
        rows.extend(
            [
                ("有効キャリアセンス時間[ms](Effective carrier-sense duration [ms])", round(effective_carrier_sense_duration_for_request(request), 6)),
                ("LoRa送信時間[ms](LoRa airtime [ms])", round(lora_airtime_ms_for_request(request), 6)),
                ("LoRaペイロード[bytes](LoRa payload [bytes])", lora_config.payload_bytes),
                ("LoRa SF(LoRa spreading factor)", lora_config.spreading_factor),
                ("LoRa帯域[Hz](LoRa bandwidth [Hz])", lora_config.bandwidth_hz),
                ("LoRa符号化率(LoRa coding rate)", f"4/{lora_config.coding_rate_denominator}"),
                ("LoRaプリアンブル[symbols](LoRa preamble [symbols])", lora_config.preamble_symbols),
                ("LoRa明示ヘッダー(LoRa explicit header)", lora_config.explicit_header),
                ("LoRa CRC(LoRa CRC)", lora_config.crc_enabled),
                ("LoRa LDRO(LoRa low-data-rate optimize)", "自動(Auto)" if request.lora_low_data_rate_optimize is None else request.lora_low_data_rate_optimize),
            ]
        )
    return pd.DataFrame(
        [
            {"項目(Parameter)": str(parameter), "値(Value)": str(value)}
            for parameter, value in rows
        ]
    )


def _available_sweep_fields(start_timing_mode: str, fixed_start_preset: str, simulation_mode: str) -> list[str]:
    fields = list(COMMON_SWEEP_FIELDS)
    if start_timing_mode == "random":
        fields.extend(RANDOM_START_SWEEP_FIELDS)
    elif fixed_start_preset == "even_interval":
        fields.extend(FIXED_START_SWEEP_FIELDS)
    if simulation_mode == "per_measurement":
        fields.extend(PER_MEASUREMENT_SWEEP_FIELDS)
    return fields


def _format_sweep_field(field_name: str) -> str:
    return str(SWEEP_PARAMETER_SPECS.get(field_name, {}).get("label", field_name))


def _sweep_number_input(
    label: str,
    value: int | float,
    key: str,
    spec: dict[str, Any],
    *,
    is_step: bool = False,
) -> int | float:
    kwargs: dict[str, Any] = {"label": label, "value": value, "key": key}
    if "min" in spec:
        kwargs["min_value"] = spec["min"]
    if "max" in spec:
        kwargs["max_value"] = spec["max"]
    if "format" in spec:
        kwargs["format"] = spec["format"]

    if spec.get("type") == "int":
        kwargs["step"] = max(1, int(spec.get("step", 1)))
        if is_step:
            kwargs["min_value"] = 1
        return int(st.number_input(**kwargs))

    kwargs["step"] = float(spec.get("step", 1.0))
    if is_step:
        kwargs["min_value"] = 0.00000001
    return float(st.number_input(**kwargs))


def _build_sweep_values(
    field_name: str,
    start_value: int | float,
    stop_value: int | float,
    step_value: int | float,
) -> list[int | float]:
    spec = SWEEP_PARAMETER_SPECS[field_name]
    if step_value <= 0:
        raise ValueError(f"{_format_sweep_field(field_name)} の刻み幅は正の値にしてください(step must be positive)")
    if start_value > stop_value:
        raise ValueError(f"{_format_sweep_field(field_name)} は開始値を終了値以下にしてください(start must be <= end)")

    if spec.get("type") == "int":
        return list(range(int(start_value), int(stop_value) + 1, int(step_value)))

    values: list[float] = []
    current = float(start_value)
    stop = float(stop_value)
    step = float(step_value)
    epsilon = abs(step) * 1e-9
    while current <= stop + epsilon:
        values.append(round(current, 10))
        current += step
    return values


def _sweep_value_label(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _sweep_tag(field_name: str, value: Any) -> str:
    safe_value = _sweep_value_label(value).replace("-", "m").replace(".", "p")
    return f"{field_name}_{safe_value}"


def _build_sweep_requests(
    base_request: SimulationRequest,
    coupling_functions: list[str],
    sweep_values_by_field: dict[str, list[int | float]],
) -> list[SimulationRequest]:
    if not coupling_functions:
        raise ValueError("結合関数を1つ以上選んでください(Select at least one coupling function)")

    field_names = list(sweep_values_by_field)
    value_lists = [sweep_values_by_field[field_name] for field_name in field_names]
    requests: list[SimulationRequest] = []

    for coupling_function in coupling_functions:
        combinations = product(*value_lists) if value_lists else [tuple()]
        for combination in combinations:
            updates = dict(zip(field_names, combination))
            tags = [*base_request.tags]
            if field_names or len(coupling_functions) > 1:
                tags.append("sweep")
            for field_name, value in updates.items():
                tags.append(_sweep_tag(field_name, value))
            if len(coupling_functions) > 1:
                tags.append(_sweep_tag("coupling_function", coupling_function))

            requests.append(
                replace(
                    base_request,
                    coupling_function=coupling_function,
                    tags=tuple(tags),
                    **updates,
                )
            )

    return requests


def _simulation_requests_summary_frame(requests: list[SimulationRequest]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for index, request in enumerate(requests, start=1):
        rows.append(
            {
                "条件(Condition)": index,
                "run数(Runs)": request.num_runs,
                "結合関数(Coupling function)": request.coupling_function,
                "結合強度(Coupling strength)": request.coupling_strength,
                "強度倍率(Strength ratio)": request.strength_ratio,
                "周期時間[ms](Cycle time [ms])": request.cycle_time,
                "待機率[%](Listening rate [%])": request.listening_rate,
                "デバイス数(Devices)": request.device_count,
                "シミュレーション時間[ms](Duration [ms])": request.duration,
                "開始タイミング(Start timing)": request.start_timing_mode,
                "モード(Mode)": request.simulation_mode,
                "ワーカー数(Workers)": resolve_max_workers(request.num_runs, request.max_workers),
                "タグ(Tags)": ";".join(
                    normalize_simulation_tags(
                        request.tags,
                        request.device_count,
                        request.start_timing_mode,
                        request.simulation_mode,
                    )
                ),
            }
        )
    return pd.DataFrame(rows)


def _render_simulation_tab(web_config: dict[str, Any]) -> None:
    simulation_config_path = web_config["paths"].get(
        "simulation_config",
        "configs/experiments/default_simulation.toml",
    )
    simulation_config = load_toml(simulation_config_path)
    config_defaults = request_from_config(simulation_config)
    defaults = _load_last_simulation_request(config_defaults)
    start_timing_mode = st.radio(
        "開始タイミング(Start timing)",
        options=["random", "fixed"],
        index=0 if defaults.start_timing_mode == "random" else 1,
        format_func=lambda value: "ランダム(Random)" if value == "random" else "固定(Fixed)",
        horizontal=True,
        help="ランダムはrunごとに範囲内で異なる開始時刻を作ります。固定は全runで同じ開始時刻を使います(Random creates different start timings per run within the range. Fixed uses the same timings for all runs).",
    )
    fixed_start_preset = "even_interval"
    if start_timing_mode == "fixed":
        fixed_start_preset = st.selectbox(
            "固定開始プリセット(Fixed start preset)",
            options=["even_interval", "custom"],
            format_func=lambda value: "一定間隔(Even interval)" if value == "even_interval" else "手入力(Custom)",
        )
    simulation_mode = st.radio(
        "シミュレーションモード(Simulation mode)",
        options=["standard", "per_measurement"],
        index=0 if defaults.simulation_mode == "standard" else 1,
        format_func=lambda value: "標準(Standard)" if value == "standard" else "PER測定(PER measurement)",
        horizontal=True,
        help="PER測定ではLoRa送信時間とキャリアセンスを使い、チャネルが忙しいサイクルの送信をスキップします(PER measurement uses LoRa airtime and carrier sense to skip busy-channel transmissions).",
    )
    _render_simulation_job_monitor()

    with st.form("simulation"):
        coupling_function = st.selectbox(
            "結合関数(Coupling function)",
            COUPLING_FUNCTION_OPTIONS,
            index=COUPLING_FUNCTION_OPTIONS.index(defaults.coupling_function)
            if defaults.coupling_function in COUPLING_FUNCTION_OPTIONS
            else 1,
        )
        col_left, col_right = st.columns(2)
        with col_left:
            num_runs = st.number_input("run数(Runs)", min_value=1, value=defaults.num_runs)
            seed = st.number_input("乱数シード(Seed)", min_value=0, value=defaults.seed)
            coupling_strength = st.number_input(
                "結合強度(Coupling strength)",
                value=defaults.coupling_strength,
            )
            strength_ratio = st.number_input(
                "強度倍率(Strength ratio)",
                value=defaults.strength_ratio,
                format="%.8f",
            )
            max_workers = st.number_input(
                "最大ワーカー数(Max workers)",
                min_value=0,
                max_value=32,
                value=max(0, defaults.max_workers),
                help="0にするとrun数に応じた最大ワーカー数を使います(0 uses the maximum worker count for the number of runs).",
            )
        with col_right:
            cycle_time = st.number_input("周期時間[ms](Cycle time [ms])", min_value=1, value=defaults.cycle_time)
            listening_rate = st.number_input(
                "待機率[%](Listening rate [%])",
                min_value=1,
                max_value=99,
                value=defaults.listening_rate,
            )
            device_count = st.number_input("デバイス数(Devices)", min_value=1, value=defaults.device_count)
            duration = st.number_input("シミュレーション時間[ms](Duration [ms])", min_value=1, value=defaults.duration)

        start_step = defaults.start_step
        start_step_count = defaults.start_step_count
        if start_timing_mode == "random":
            start_step = st.number_input("開始時刻ステップ[ms](Start step [ms])", min_value=1, value=defaults.start_step)
            start_step_count = st.number_input(
                "開始時刻ステップ数(Start step count)",
                min_value=1,
                value=defaults.start_step_count,
            )

        carrier_sense_duration_ms = defaults.carrier_sense_duration_ms
        lora_payload_bytes = defaults.lora_payload_bytes
        lora_spreading_factor = defaults.lora_spreading_factor
        lora_bandwidth_hz = defaults.lora_bandwidth_hz
        lora_coding_rate_denominator = defaults.lora_coding_rate_denominator
        lora_preamble_symbols = defaults.lora_preamble_symbols
        lora_explicit_header = defaults.lora_explicit_header
        lora_crc_enabled = defaults.lora_crc_enabled
        lora_low_data_rate_optimize = defaults.lora_low_data_rate_optimize
        if simulation_mode == "per_measurement":
            st.subheader("PER測定設定(PER measurement settings)")
            carrier_sense_duration_ms = st.number_input(
                "キャリアセンス時間[ms](Carrier-sense duration [ms])",
                min_value=0.0,
                value=float(defaults.carrier_sense_duration_ms),
                help="0にすると送信前の待機時間を自動で使います(0 uses the pre-send awake duration automatically).",
            )
            lora_col_left, lora_col_right = st.columns(2)
            with lora_col_left:
                lora_payload_bytes = st.number_input(
                    "LoRaペイロード[bytes](LoRa payload [bytes])",
                    min_value=0,
                    value=defaults.lora_payload_bytes,
                )
                lora_spreading_factor = st.number_input(
                    "LoRa SF(LoRa spreading factor)",
                    min_value=6,
                    max_value=12,
                    value=defaults.lora_spreading_factor,
                )
                lora_bandwidth_hz = st.number_input(
                    "LoRa帯域[Hz](LoRa bandwidth [Hz])",
                    min_value=1,
                    value=defaults.lora_bandwidth_hz,
                )
                lora_coding_rate_denominator = st.number_input(
                    "LoRa符号化率 denominator(LoRa coding-rate denominator)",
                    min_value=5,
                    max_value=8,
                    value=defaults.lora_coding_rate_denominator,
                    help="5=4/5, 8=4/8",
                )
            with lora_col_right:
                lora_preamble_symbols = st.number_input(
                    "LoRaプリアンブル[symbols](LoRa preamble [symbols])",
                    min_value=0,
                    value=defaults.lora_preamble_symbols,
                )
                lora_explicit_header = st.checkbox(
                    "LoRa明示ヘッダー(LoRa explicit header)",
                    value=defaults.lora_explicit_header,
                )
                lora_crc_enabled = st.checkbox(
                    "LoRa CRC(LoRa CRC)",
                    value=defaults.lora_crc_enabled,
                )
                ldro_options = ["auto", "on", "off"]
                if defaults.lora_low_data_rate_optimize is True:
                    ldro_index = 1
                elif defaults.lora_low_data_rate_optimize is False:
                    ldro_index = 2
                else:
                    ldro_index = 0
                ldro_value = st.selectbox(
                    "LoRa LDRO(LoRa low-data-rate optimize)",
                    options=ldro_options,
                    index=ldro_index,
                    format_func=lambda value: {
                        "auto": "自動(Auto)",
                        "on": "有効(On)",
                        "off": "無効(Off)",
                    }[value],
                )
                lora_low_data_rate_optimize = (
                    None if ldro_value == "auto" else ldro_value == "on"
                )

        fixed_start_interval = defaults.fixed_start_interval
        fixed_start_offset = defaults.fixed_start_offset
        fixed_start_times_text = ""
        if start_timing_mode == "fixed":
            default_fixed_start_times = defaults.fixed_start_times or tuple(
                generate_even_interval_start_times(
                    k=defaults.device_count,
                    interval=defaults.fixed_start_interval,
                    start_time=defaults.fixed_start_offset,
                )
            )
            if fixed_start_preset == "even_interval":
                fixed_col_left, fixed_col_right = st.columns(2)
                with fixed_col_left:
                    fixed_start_interval = st.number_input(
                        "固定開始間隔[ms](Fixed start interval [ms])",
                        min_value=0,
                        value=defaults.fixed_start_interval,
                    )
                with fixed_col_right:
                    fixed_start_offset = st.number_input(
                        "固定開始オフセット[ms](Fixed start offset [ms])",
                        min_value=0,
                        value=defaults.fixed_start_offset,
                    )
            else:
                fixed_start_times_text = st.text_area(
                    "固定開始時刻[ms](Fixed start times [ms])",
                    value=",".join(str(value) for value in default_fixed_start_times),
                    help="手入力の場合に使います。カンマ、セミコロン、改行で区切れます(Used for custom mode. Separate values with commas, semicolons, or newlines).",
                )
        tags = st.text_input(
            "手動タグ(Manual tags)",
            value=_manual_tags_text(defaults.tags),
            help="台数タグはデバイス数から自動で付与されます(Device-count tag is added automatically).",
        )
        output_root = st.text_input("run出力ディレクトリ(Output runs dir)", value=str(defaults.output_root.relative_to(PROJECT_ROOT)))

        sweep_enabled = st.checkbox(
            "パラメータ範囲を一括実行(Sweep parameter ranges)",
            value=False,
            help="選んだパラメータの開始・終了・刻み幅から全組み合わせを作ります(Creates all combinations from start/end/step values).",
        )
        sweep_coupling_functions = [coupling_function]
        selected_sweep_fields: list[str] = []
        sweep_ranges: dict[str, tuple[int | float, int | float, int | float]] = {}
        if sweep_enabled:
            sweep_coupling_functions = st.multiselect(
                "一括実行する結合関数(Coupling functions to sweep)",
                options=COUPLING_FUNCTION_OPTIONS,
                default=[coupling_function],
            )
            available_sweep_fields = _available_sweep_fields(
                start_timing_mode,
                fixed_start_preset,
                simulation_mode,
            )
            selected_sweep_fields = st.multiselect(
                "範囲で変化させる数値パラメータ(Numeric parameters to sweep)",
                options=available_sweep_fields,
                format_func=_format_sweep_field,
            )
            current_sweep_values: dict[str, int | float] = {
                "coupling_strength": int(coupling_strength),
                "strength_ratio": float(strength_ratio),
                "cycle_time": int(cycle_time),
                "listening_rate": int(listening_rate),
                "device_count": int(device_count),
                "duration": int(duration),
                "start_step": int(start_step),
                "start_step_count": int(start_step_count),
                "fixed_start_interval": int(fixed_start_interval),
                "fixed_start_offset": int(fixed_start_offset),
                "carrier_sense_duration_ms": float(carrier_sense_duration_ms),
                "lora_payload_bytes": int(lora_payload_bytes),
                "lora_spreading_factor": int(lora_spreading_factor),
                "lora_bandwidth_hz": int(lora_bandwidth_hz),
                "lora_coding_rate_denominator": int(lora_coding_rate_denominator),
                "lora_preamble_symbols": int(lora_preamble_symbols),
            }
            for field_name in selected_sweep_fields:
                spec = SWEEP_PARAMETER_SPECS[field_name]
                current_value = current_sweep_values[field_name]
                st.markdown(f"**{_format_sweep_field(field_name)}**")
                col_start, col_stop, col_step = st.columns(3)
                with col_start:
                    start_value = _sweep_number_input(
                        "開始(Start)",
                        current_value,
                        f"sweep_{field_name}_start",
                        spec,
                    )
                with col_stop:
                    stop_value = _sweep_number_input(
                        "終了(End)",
                        current_value,
                        f"sweep_{field_name}_stop",
                        spec,
                    )
                with col_step:
                    step_value = _sweep_number_input(
                        "刻み幅(Step)",
                        spec.get("step", 1),
                        f"sweep_{field_name}_step",
                        spec,
                        is_step=True,
                    )
                sweep_ranges[field_name] = (start_value, stop_value, step_value)

        submitted = st.form_submit_button("パラメーターを確認(Review parameters)")

    if submitted:
        try:
            if start_timing_mode == "fixed" and fixed_start_preset == "even_interval":
                fixed_start_times = tuple()
            elif start_timing_mode == "fixed":
                fixed_start_times = tuple(parse_start_times_text(fixed_start_times_text))
            else:
                fixed_start_times = tuple()
        except ValueError as exc:
            st.error(f"固定開始時刻を読み取れませんでした(Could not parse fixed start times): {exc}")
            return

        pending_request = SimulationRequest(
            num_runs=int(num_runs),
            seed=int(seed),
            coupling_function=coupling_function,
            coupling_strength=int(coupling_strength),
            strength_ratio=float(strength_ratio),
            cycle_time=int(cycle_time),
            listening_rate=int(listening_rate),
            device_count=int(device_count),
            duration=int(duration),
            start_step_count=int(start_step_count),
            start_step=int(start_step),
            tags=tuple(tag.strip() for tag in tags.split(";") if tag.strip()),
            output_root=resolve_project_path(output_root),
            max_workers=int(max_workers),
            start_timing_mode=start_timing_mode,
            fixed_start_times=fixed_start_times,
            fixed_start_interval=int(fixed_start_interval),
            fixed_start_offset=int(fixed_start_offset),
            simulation_mode=simulation_mode,
            carrier_sense_duration_ms=float(carrier_sense_duration_ms),
            lora_payload_bytes=int(lora_payload_bytes),
            lora_spreading_factor=int(lora_spreading_factor),
            lora_bandwidth_hz=int(lora_bandwidth_hz),
            lora_coding_rate_denominator=int(lora_coding_rate_denominator),
            lora_preamble_symbols=int(lora_preamble_symbols),
            lora_explicit_header=bool(lora_explicit_header),
            lora_crc_enabled=bool(lora_crc_enabled),
            lora_low_data_rate_optimize=lora_low_data_rate_optimize,
        )
        try:
            sweep_values_by_field = {
                field_name: _build_sweep_values(field_name, *range_values)
                for field_name, range_values in sweep_ranges.items()
            }
            pending_requests = (
                _build_sweep_requests(
                    pending_request,
                    coupling_functions=list(sweep_coupling_functions),
                    sweep_values_by_field=sweep_values_by_field,
                )
                if sweep_enabled
                else [pending_request]
            )
            for request in pending_requests:
                if request.start_timing_mode == "fixed":
                    fixed_start_times_for_request(request)
                if request.simulation_mode == "per_measurement":
                    lora_airtime_ms_for_request(request)
        except ValueError as exc:
            st.error(f"設定を確認してください(Please check settings): {exc}")
            return

        st.session_state["pending_simulation_requests"] = pending_requests
        st.session_state.pop("pending_simulation_request", None)
        st.session_state.pop("last_simulation_results", None)

    pending_requests = st.session_state.get("pending_simulation_requests")
    legacy_pending_request = st.session_state.get("pending_simulation_request")
    if pending_requests is None and legacy_pending_request is not None:
        pending_requests = [legacy_pending_request]
    if pending_requests:
        st.subheader("実行前確認(Parameter review)")
        try:
            if len(pending_requests) == 1:
                review_df = _simulation_review_frame(pending_requests[0])
            else:
                total_runs = sum(request.num_runs for request in pending_requests)
                st.metric("一括実行の総run数(Total sweep runs)", total_runs)
                review_df = _simulation_requests_summary_frame(pending_requests)
        except ValueError as exc:
            st.error(f"設定を確認してください(Please check settings): {exc}")
            return
        st.dataframe(review_df, width="stretch", hide_index=True)

        col_run, col_cancel = st.columns(2)
        run_confirmed = col_run.button("この条件で実行(Run with these parameters)", type="primary")
        cancel_confirmed = col_cancel.button("確認を取り消す(Cancel review)")

        if cancel_confirmed:
            st.session_state.pop("pending_simulation_requests", None)
            st.session_state.pop("pending_simulation_request", None)
            st.session_state.pop("last_simulation_results", None)
            st.rerun()

        if run_confirmed:
            try:
                job_id, job_path = _start_simulation_job(pending_requests)
            except Exception as exc:
                st.error(f"ジョブを開始できませんでした(Could not start job): {exc}")
                return
            st.session_state["last_started_simulation_job_id"] = job_id
            st.session_state.pop("pending_simulation_requests", None)
            st.session_state.pop("pending_simulation_request", None)
            _bump_cache_token("runs")
            _save_last_simulation_request(pending_requests[-1])
            st.success(
                f"シミュレーションジョブを開始しました(Started simulation job): {job_id}"
            )
            st.caption(f"ジョブ状態ファイル(Job status file): {_display_project_path(job_path)}")
            st.rerun()

    last_results = st.session_state.get("last_simulation_results")
    if last_results:
        st.subheader("直近の実行結果(Latest simulation results)")
        st.dataframe(pd.DataFrame(last_results), width="stretch", hide_index=True)


def _python_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    pythonpath_parts = [str(SRC_ROOT)]
    if existing_pythonpath:
        pythonpath_parts.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    env["MPLBACKEND"] = "Agg"
    return env


def _run_research_program_command(command_name: str) -> str:
    return _run_research_program_command_with_env(command_name, env_overrides={})


def _run_research_program_command_with_env(command_name: str, env_overrides: dict[str, str]) -> str:
    env = _python_subprocess_env()
    env.update(env_overrides)
    completed = subprocess.run(
        [sys.executable, "-m", "research_program.cli", command_name],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = "\n".join(part.strip() for part in [completed.stdout, completed.stderr] if part.strip())
    if completed.returncode != 0:
        raise RuntimeError(output or f"command failed: {command_name}")
    return output or "完了しました(Completed)."


def _copy_selected_runs_to_temp(records: list[RunRecord], temp_runs_dir: Path) -> None:
    temp_runs_dir.mkdir(parents=True, exist_ok=True)
    used_names: set[str] = set()
    for index, record in enumerate(records):
        run_dir_name = record.path.name
        if run_dir_name in used_names:
            run_dir_name = f"{run_dir_name}_{index:04d}"
        used_names.add(run_dir_name)
        shutil.copytree(record.path, temp_runs_dir / run_dir_name)


def _aggregate_graph_command_selected(commands: list[str]) -> bool:
    aggregate_graph_commands = {
        "plot-aggregated-phase-gap-error",
        "plot-aggregated-phase-gap-error-overlay",
        "plot-convergence-summary",
    }
    return bool(aggregate_graph_commands.intersection(commands))


def _record_coupling_function(record: RunRecord) -> str | None:
    value = record.metadata.get("coupling_function")
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip()


def _record_coupling_strength(record: RunRecord) -> int | None:
    value = record.metadata.get("coupling_strength")
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _selected_coupling_functions(records: list[RunRecord]) -> set[str]:
    return {
        coupling_function
        for record in records
        if (coupling_function := _record_coupling_function(record)) is not None
    }


def _selected_coupling_pairs(records: list[RunRecord]) -> set[tuple[str, int]]:
    pairs: set[tuple[str, int]] = set()
    for record in records:
        coupling_function = _record_coupling_function(record)
        coupling_strength = _record_coupling_strength(record)
        if coupling_function is not None and coupling_strength is not None:
            pairs.add((coupling_function, coupling_strength))
    return pairs


def _config_allows_coupling_pair(config: Any, coupling_function: str, coupling_strength: int) -> bool:
    target_functions = getattr(config, "target_coupling_functions", [])
    if target_functions and coupling_function not in set(target_functions):
        return False

    strength_rules = getattr(config, "coupling_function_strength_rules", {})
    rule = strength_rules.get(coupling_function) if strength_rules else None
    if rule is not None:
        strength_min = getattr(rule, "strength_min", None)
        strength_max = getattr(rule, "strength_max", None)
        step = getattr(rule, "step", None)
    else:
        strength_min = getattr(config, "coupling_strength_min", None)
        strength_max = getattr(config, "coupling_strength_max", None)
        step = getattr(config, "coupling_strength_step", None)

    if strength_min is not None and coupling_strength < strength_min:
        return False
    if strength_max is not None and coupling_strength > strength_max:
        return False
    if step is not None:
        base = strength_min if strength_min is not None else coupling_strength
        if (coupling_strength - base) % step != 0:
            return False
    return True


def _estimated_figure_count(command_name: str, records: list[RunRecord]) -> tuple[int, str]:
    run_count = len(records)
    coupling_functions = _selected_coupling_functions(records)
    coupling_pairs = _selected_coupling_pairs(records)

    if command_name == "plot-phase-diff":
        return run_count, "対象runごとに1枚(1 figure per target run)"
    if command_name == "plot-phase-gap-error":
        return run_count * 2, "対象runごとに誤差/比率の2枚(2 figures per target run)"
    if command_name == "plot-per":
        per_figures_per_run = 1 + (1 if PER_PLOT_CONFIG.show_per_change_plot else 0)
        return run_count * per_figures_per_run, f"対象runごとに{per_figures_per_run}枚({per_figures_per_run} figure(s) per target run)"
    if command_name == "plot-per-aligned":
        count = 0
        parts: list[str] = []
        if PER_ALIGNED_PLOT_CONFIG.save_individual_plots:
            count += run_count
            parts.append(f"個別{run_count}枚(individual {run_count})")
        if PER_ALIGNED_PLOT_CONFIG.save_overlay_plot and run_count > 0:
            count += 1
            parts.append("重ね描き1枚(overlay 1)")
        return count, " + ".join(parts) if parts else "設定上は出力なし(No output enabled)"
    if command_name == "compare-per":
        count = len(coupling_functions)
        target_functions = set(COMPARE_PER_BY_DEVICES_INTERVAL_CONFIG.target_coupling_functions)
        has_combined_target = bool(coupling_functions) if not target_functions else bool(coupling_functions.intersection(target_functions))
        if COMPARE_PER_BY_DEVICES_INTERVAL_CONFIG.show_combined_method_plot and has_combined_target:
            count += 1
        return count, "結合関数ごとの図 + 条件に合えば手法比較1枚(per coupling function plus optional combined plot)"
    if command_name == "plot-aggregated-phase-gap-error":
        allowed_pairs = {
            pair
            for pair in coupling_pairs
            if _config_allows_coupling_pair(AGGREGATED_PHASE_GAP_ERROR_PLOT_CONFIG, pair[0], pair[1])
        }
        return len(allowed_pairs), "結合関数×結合強度ごとの集約CSVにつき1枚(1 per aggregated coupling pair)"
    if command_name == "plot-aggregated-phase-gap-error-overlay":
        allowed_pairs = {
            pair
            for pair in coupling_pairs
            if _config_allows_coupling_pair(AGGREGATED_PHASE_GAP_ERROR_OVERLAY_PLOT_CONFIG, pair[0], pair[1])
        }
        return (1 if allowed_pairs else 0), "設定条件に合う集約データがあれば1枚(1 if any aggregated data matches config)"
    if command_name == "plot-convergence-summary":
        allowed_pairs = {
            pair
            for pair in coupling_pairs
            if _config_allows_coupling_pair(CONVERGENCE_ANALYSIS_CONFIG, pair[0], pair[1])
        }
        return (1 if allowed_pairs else 0), "設定条件に合う集約データがあれば1枚(1 if any aggregated data matches config)"
    return 0, "予測対象外(Not estimated)"


def _estimated_figure_frame(commands: list[str], records: list[RunRecord]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for command_name in commands:
        count, basis = _estimated_figure_count(command_name, records)
        rows.append(
            {
                "種類(Type)": GRAPH_CREATION_COMMANDS[command_name]["label"],
                "予測枚数(Estimated figures)": count,
                "根拠(Basis)": basis,
            }
        )
    return pd.DataFrame(rows)


def _nullable_float_plot_input(label: str, current_value: float | int | None, key: str) -> float | None:
    auto = st.checkbox(
        f"{label} を自動(Auto)",
        value=current_value is None,
        key=f"{key}_auto",
    )
    if auto:
        return None
    default_value = 0.0 if current_value is None else float(current_value)
    return float(
        st.number_input(
            label,
            value=default_value,
            step=1.0,
            key=key,
        )
    )


def _int_plot_input(label: str, current_value: int | None, key: str, min_value: int = 0) -> int:
    default_value = min_value if current_value is None else int(current_value)
    return int(
        st.number_input(
            label,
            value=default_value,
            min_value=min_value,
            step=1,
            key=key,
        )
    )


def _float_plot_input(
    label: str,
    current_value: float | int | None,
    key: str,
    *,
    min_value: float | None = None,
    step: float = 0.01,
) -> float:
    default_value = 0.0 if current_value is None else float(current_value)
    return float(
        st.number_input(
            label,
            value=default_value,
            min_value=min_value,
            step=step,
            key=key,
        )
    )


def _add_range_plot_inputs(
    values: dict[str, Any],
    config: Any,
    prefix: str,
    *,
    title: str,
    min_field: str,
    max_field: str,
    min_label: str,
    max_label: str,
) -> None:
    if not hasattr(config, min_field) or not hasattr(config, max_field):
        return
    st.markdown(f"**{title}**")
    col_min, col_max = st.columns(2)
    with col_min:
        values[min_field] = _nullable_float_plot_input(
            min_label,
            getattr(config, min_field),
            f"{prefix}_{min_field}",
        )
    with col_max:
        values[max_field] = _nullable_float_plot_input(
            max_label,
            getattr(config, max_field),
            f"{prefix}_{max_field}",
        )


def _add_common_plot_size_inputs(values: dict[str, Any], config: Any, prefix: str) -> None:
    if not all(hasattr(config, field_name) for field_name in ["figure_width", "figure_height", "save_dpi"]):
        return
    st.markdown("**画像サイズ(Image size)**")
    col_width, col_height, col_dpi = st.columns(3)
    with col_width:
        values["figure_width"] = _float_plot_input(
            "幅[inch](Width [inch])",
            getattr(config, "figure_width"),
            f"{prefix}_figure_width",
            min_value=1.0,
            step=0.5,
        )
    with col_height:
        values["figure_height"] = _float_plot_input(
            "高さ[inch](Height [inch])",
            getattr(config, "figure_height"),
            f"{prefix}_figure_height",
            min_value=1.0,
            step=0.5,
        )
    with col_dpi:
        values["save_dpi"] = _int_plot_input(
            "保存DPI(Save DPI)",
            getattr(config, "save_dpi"),
            f"{prefix}_save_dpi",
            min_value=1,
        )


def _add_standard_xy_plot_inputs(values: dict[str, Any], config: Any, prefix: str) -> None:
    _add_range_plot_inputs(
        values,
        config,
        prefix,
        title="x軸範囲(X-axis range)",
        min_field="xlim_min",
        max_field="xlim_max",
        min_label="x軸下限(X min)",
        max_label="x軸上限(X max)",
    )
    _add_range_plot_inputs(
        values,
        config,
        prefix,
        title="y軸範囲(Y-axis range)",
        min_field="ylim_min",
        max_field="ylim_max",
        min_label="y軸下限(Y min)",
        max_label="y軸上限(Y max)",
    )


def _collect_plot_parameter_values(command_name: str, config: Any, prefix: str) -> dict[str, Any]:
    values: dict[str, Any] = {}

    if command_name == "plot-phase-diff":
        _add_range_plot_inputs(
            values,
            config,
            prefix,
            title="x軸範囲(X-axis range)",
            min_field="xlim_min",
            max_field="xlim_max",
            min_label="x軸下限(X min)",
            max_label="x軸上限(X max)",
        )
        y_mode_options = ["minus_pi_to_pi", "0_to_2pi"]
        current_mode = getattr(config, "y_range_mode", "minus_pi_to_pi")
        values["y_range_mode"] = st.selectbox(
            "y軸範囲モード(Y-axis range mode)",
            options=y_mode_options,
            index=y_mode_options.index(current_mode) if current_mode in y_mode_options else 0,
            format_func=lambda value: {
                "minus_pi_to_pi": "-pi から pi(-pi to pi)",
                "0_to_2pi": "0 から 2pi(0 to 2pi)",
            }.get(value, value),
            key=f"{prefix}_y_range_mode",
        )
        _add_common_plot_size_inputs(values, config, prefix)
        return values

    if command_name == "plot-per":
        _add_range_plot_inputs(
            values,
            config,
            prefix,
            title="x軸範囲(X-axis range)",
            min_field="xlim_min",
            max_field="xlim_max",
            min_label="x軸下限(X min)",
            max_label="x軸上限(X max)",
        )
        _add_range_plot_inputs(
            values,
            config,
            prefix,
            title="PER y軸範囲(PER Y-axis range)",
            min_field="per_ylim_min",
            max_field="per_ylim_max",
            min_label="PER y軸下限(PER Y min)",
            max_label="PER y軸上限(PER Y max)",
        )
        _add_range_plot_inputs(
            values,
            config,
            prefix,
            title="PER変化量 y軸範囲(PER-change Y-axis range)",
            min_field="per_change_ylim_min",
            max_field="per_change_ylim_max",
            min_label="PER変化量 y軸下限(PER-change Y min)",
            max_label="PER変化量 y軸上限(PER-change Y max)",
        )
        st.markdown("**PER計算(PER calculation)**")
        col_window, col_change = st.columns(2)
        with col_window:
            values["per_window_width_cycles"] = _int_plot_input(
                "PER計算窓幅[cycle](PER window width [cycles])",
                getattr(config, "per_window_width_cycles"),
                f"{prefix}_per_window_width_cycles",
                min_value=1,
            )
        with col_change:
            values["per_change_width_cycles"] = _int_plot_input(
                "PER変化量幅[cycle](PER-change width [cycles])",
                getattr(config, "per_change_width_cycles"),
                f"{prefix}_per_change_width_cycles",
                min_value=1,
            )
        _add_common_plot_size_inputs(values, config, prefix)
        return values

    if command_name == "plot-per-aligned":
        _add_standard_xy_plot_inputs(values, config, prefix)
        st.markdown("**PER整列(PER alignment)**")
        col_window, col_mode, col_cycle = st.columns(3)
        with col_window:
            values["per_window_width_cycles"] = _int_plot_input(
                "PER計算窓幅[cycle](PER window width [cycles])",
                getattr(config, "per_window_width_cycles"),
                f"{prefix}_per_window_width_cycles",
                min_value=1,
            )
        base_mode_options = ["fixed", "best_per", "largest_improvement", "first_available"]
        current_mode = getattr(config, "base_cycle_mode", "fixed")
        with col_mode:
            values["base_cycle_mode"] = st.selectbox(
                "基準周期の決め方(Base-cycle mode)",
                options=base_mode_options,
                index=base_mode_options.index(current_mode) if current_mode in base_mode_options else 0,
                format_func=lambda value: {
                    "fixed": "固定(Fixed)",
                    "best_per": "PER最小(Best PER)",
                    "largest_improvement": "改善最大(Largest improvement)",
                    "first_available": "最初の有効周期(First available)",
                }.get(value, value),
                key=f"{prefix}_base_cycle_mode",
            )
        with col_cycle:
            if values["base_cycle_mode"] == "fixed":
                values["fixed_base_cycle"] = _int_plot_input(
                    "固定基準周期(Fixed base cycle)",
                    getattr(config, "fixed_base_cycle"),
                    f"{prefix}_fixed_base_cycle",
                    min_value=0,
                )
            elif values["base_cycle_mode"] == "largest_improvement":
                values["per_change_width_cycles"] = _int_plot_input(
                    "改善判定幅[cycle](Improvement width [cycles])",
                    getattr(config, "per_change_width_cycles"),
                    f"{prefix}_per_change_width_cycles",
                    min_value=1,
                )
        _add_common_plot_size_inputs(values, config, prefix)
        return values

    if command_name == "compare-per":
        _add_standard_xy_plot_inputs(values, config, prefix)
        st.markdown("**PER比較(PER comparison)**")
        col_cycle, col_window = st.columns(2)
        with col_cycle:
            values["target_cycle"] = _int_plot_input(
                "比較対象周期(Target cycle)",
                getattr(config, "target_cycle"),
                f"{prefix}_target_cycle",
                min_value=0,
            )
        with col_window:
            values["per_window_width_cycles"] = _int_plot_input(
                "PER計算窓幅[cycle](PER window width [cycles])",
                getattr(config, "per_window_width_cycles"),
                f"{prefix}_per_window_width_cycles",
                min_value=1,
            )
        _add_common_plot_size_inputs(values, config, prefix)
        return values

    if command_name == "plot-aggregated-phase-gap-error-overlay":
        _add_standard_xy_plot_inputs(values, config, prefix)
        st.markdown("**収束表示(Convergence marker)**")
        col_window, col_threshold = st.columns(2)
        with col_window:
            values["convergence_window_cycles"] = _int_plot_input(
                "収束判定窓幅[cycle](Convergence window [cycles])",
                getattr(config, "convergence_window_cycles"),
                f"{prefix}_convergence_window_cycles",
                min_value=1,
            )
        with col_threshold:
            values["convergence_threshold"] = _float_plot_input(
                "収束しきい値(Convergence threshold)",
                getattr(config, "convergence_threshold"),
                f"{prefix}_convergence_threshold",
                min_value=0.0,
                step=0.001,
            )
        _add_common_plot_size_inputs(values, config, prefix)
        return values

    if command_name == "plot-convergence-summary":
        _add_range_plot_inputs(
            values,
            config,
            prefix,
            title="x軸範囲(X-axis range)",
            min_field="xlim_min",
            max_field="xlim_max",
            min_label="x軸下限(X min)",
            max_label="x軸上限(X max)",
        )
        _add_range_plot_inputs(
            values,
            config,
            prefix,
            title="左y軸範囲(Left Y-axis range)",
            min_field="ylim_left_min",
            max_field="ylim_left_max",
            min_label="左y軸下限(Left Y min)",
            max_label="左y軸上限(Left Y max)",
        )
        _add_range_plot_inputs(
            values,
            config,
            prefix,
            title="右y軸範囲(Right Y-axis range)",
            min_field="ylim_right_min",
            max_field="ylim_right_max",
            min_label="右y軸下限(Right Y min)",
            max_label="右y軸上限(Right Y max)",
        )
        st.markdown("**収束判定(Convergence detection)**")
        col_window, col_threshold = st.columns(2)
        with col_window:
            values["convergence_window_cycles"] = _int_plot_input(
                "収束判定窓幅[cycle](Convergence window [cycles])",
                getattr(config, "convergence_window_cycles"),
                f"{prefix}_convergence_window_cycles",
                min_value=1,
            )
        with col_threshold:
            values["convergence_threshold"] = _float_plot_input(
                "収束しきい値(Convergence threshold)",
                getattr(config, "convergence_threshold"),
                f"{prefix}_convergence_threshold",
                min_value=0.0,
                step=0.001,
            )
        _add_common_plot_size_inputs(values, config, prefix)
        return values

    _add_standard_xy_plot_inputs(values, config, prefix)
    _add_common_plot_size_inputs(values, config, prefix)
    return values


def _render_plot_parameter_controls(selected_graph_commands: list[str]) -> dict[str, dict[str, Any]]:
    st.subheader("グラフパラメーター(Graph parameters)")
    if not selected_graph_commands:
        st.info("画像種類を選ぶと、変更できるグラフパラメーターが表示されます(Select graph types to edit plot parameters).")
        return {}

    use_web_parameters = st.checkbox(
        "Web上の値でグラフ設定を上書き(Override graph settings from Web)",
        value=False,
    )
    if not use_web_parameters:
        st.caption("チェックを入れると、作成時だけ軸範囲などをWeb入力値で上書きします(Enable this to override plot settings only for this creation run).")
        return {}

    st.caption("各範囲で自動(Auto)を選ぶと、その軸はMatplotlibの自動範囲になります(Auto lets Matplotlib choose that axis range).")
    overrides: dict[str, dict[str, Any]] = {}
    for command_name in selected_graph_commands:
        config_pair = PLOT_CONFIG_BY_GRAPH_COMMAND.get(command_name)
        if config_pair is None:
            continue
        config_name, config = config_pair
        with st.expander(GRAPH_CREATION_COMMANDS[command_name]["label"], expanded=False):
            values = _collect_plot_parameter_values(
                command_name,
                config,
                f"plot_override_{command_name}",
            )
            if values:
                overrides[config_name] = values
    return overrides


def _invalid_plot_override_ranges(plot_overrides: dict[str, dict[str, Any]]) -> list[str]:
    messages: list[str] = []
    command_by_config_name = {
        config_name: command_name
        for command_name, (config_name, _) in PLOT_CONFIG_BY_GRAPH_COMMAND.items()
    }
    for config_name, values in plot_overrides.items():
        command_name = command_by_config_name.get(config_name)
        graph_label = GRAPH_CREATION_COMMANDS.get(command_name or "", {}).get("label", config_name)
        for min_field, max_field, range_label in PLOT_RANGE_FIELD_PAIRS:
            min_value = values.get(min_field)
            max_value = values.get(max_field)
            if min_value is None or max_value is None:
                continue
            if float(min_value) >= float(max_value):
                messages.append(
                    f"{graph_label}: {range_label} は下限を上限より小さくしてください(Min must be smaller than max)."
                )
    return messages


def _figure_snapshot(web_config: dict[str, Any]) -> dict[Path, tuple[int, int]]:
    figure_config = web_config.get("figures", {})
    assets = discover_figures(
        web_config["paths"].get("figure_dirs", []),
        extensions=figure_config.get("extensions", []),
    )
    return {
        asset.path.resolve(): (asset.path.stat().st_mtime_ns, asset.size_bytes)
        for asset in assets
    }


def _changed_figure_assets(
    web_config: dict[str, Any],
    before_snapshot: dict[Path, tuple[int, int]],
) -> list[FigureAsset]:
    figure_config = web_config.get("figures", {})
    assets = discover_figures(
        web_config["paths"].get("figure_dirs", []),
        extensions=figure_config.get("extensions", []),
    )
    return [
        asset
        for asset in assets
        if before_snapshot.get(asset.path.resolve()) != (asset.path.stat().st_mtime_ns, asset.size_bytes)
    ]


def _figure_output_dir(asset: FigureAsset) -> str:
    try:
        relative_path = asset.path.relative_to(asset.source_root)
        return relative_path.parts[0] if len(relative_path.parts) > 1 else ""
    except ValueError:
        return asset.path.parent.name


def _figure_graph_type(asset: FigureAsset) -> str:
    graph_dir = _figure_output_dir(asset)
    if not graph_dir:
        return "その他(Other)"
    return GRAPH_TYPE_BY_OUTPUT_DIR.get(graph_dir, f"その他(Other): {graph_dir}")


def _figure_scope_key(asset: FigureAsset) -> str:
    graph_dir = _figure_output_dir(asset)
    if graph_dir == "per_aligned_graphs" and asset.name.startswith("overlay_"):
        return "multiple"
    return FIGURE_SCOPE_BY_OUTPUT_DIR.get(graph_dir, "other")


def _figure_scope_label(asset: FigureAsset) -> str:
    return FIGURE_SCOPE_LABELS[_figure_scope_key(asset)]


def _figure_graph_description(asset: FigureAsset) -> str:
    graph_dir = _figure_output_dir(asset)
    if graph_dir == "per_aligned_graphs" and asset.name.startswith("overlay_"):
        return "複数runのPERを基準周期にそろえて重ね描き(Overlays multiple runs aligned to a base cycle)"
    return GRAPH_DESCRIPTION_BY_OUTPUT_DIR.get(graph_dir, "保存先フォルダから自動分類(Auto-classified from output folder)")


def _figure_sort_key(asset: FigureAsset, sort_by: str) -> tuple[Any, ...]:
    scope_key = _figure_scope_key(asset)
    scope_order = FIGURE_SCOPE_SORT_ORDER.get(scope_key, FIGURE_SCOPE_SORT_ORDER["other"])
    scope_label = _figure_scope_label(asset)
    graph_type = _figure_graph_type(asset)
    if sort_by == "figure_scope":
        return (scope_order, graph_type, asset.relative_path.lower())
    if sort_by == "name":
        return (asset.name.lower(), scope_order, graph_type, asset.relative_path.lower())
    if sort_by == "extension":
        return (asset.extension, scope_order, graph_type, asset.name.lower())
    if sort_by == "size_kb":
        return (asset.size_bytes, scope_order, graph_type, asset.name.lower())
    if sort_by == "relative_path":
        return (asset.relative_path.lower(), scope_order, graph_type, asset.name.lower())
    return (graph_type, scope_label, asset.relative_path.lower(), asset.name.lower())


def _figures_to_display_frame(assets: list[FigureAsset]) -> pd.DataFrame:
    df = figures_to_frame(assets)
    if df.empty:
        return pd.DataFrame(columns=list(FIGURE_COLUMN_LABELS))
    df.insert(0, "figure_scope", [_figure_scope_label(asset) for asset in assets])
    df.insert(1, "graph_type", [_figure_graph_type(asset) for asset in assets])
    df.insert(2, "graph_description", [_figure_graph_description(asset) for asset in assets])
    return df


def _style_figure_display_frame(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    display_df = df.rename(columns=FIGURE_COLUMN_LABELS)
    scope_column = FIGURE_COLUMN_LABELS["figure_scope"]
    single_label = FIGURE_SCOPE_LABELS["single"]
    multiple_label = FIGURE_SCOPE_LABELS["multiple"]

    def style_row(row: pd.Series) -> list[str]:
        scope = row.get(scope_column, "")
        if scope == single_label:
            style = "background-color: #eaf4ff; color: #17324d;"
        elif scope == multiple_label:
            style = "background-color: #fff3d6; color: #493300;"
        else:
            style = "background-color: #f4f4f5; color: #27272a;"
        return [style for _ in row]

    return display_df.style.apply(style_row, axis=1)


def _graph_command_output_dir(command_info: dict[str, str]) -> str:
    output = command_info.get("output", "")
    parts = Path(output).parts
    try:
        figures_index = parts.index("figures")
    except ValueError:
        return ""
    next_index = figures_index + 1
    if next_index >= len(parts):
        return ""
    return parts[next_index]


def _graph_command_scope_label(command_info: dict[str, str]) -> str:
    output_dir = _graph_command_output_dir(command_info)
    scope_key = FIGURE_SCOPE_BY_OUTPUT_DIR.get(output_dir, "other")
    return FIGURE_SCOPE_LABELS[scope_key]


def _graph_command_description(command_info: dict[str, str]) -> str:
    output_dir = _graph_command_output_dir(command_info)
    return GRAPH_DESCRIPTION_BY_OUTPUT_DIR.get(output_dir, "保存先フォルダから自動分類(Auto-classified from output folder)")


def _style_graph_command_frame(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    display_df = df.rename(columns=GRAPH_TABLE_COLUMN_LABELS)
    scope_column = GRAPH_TABLE_COLUMN_LABELS["scope"]
    single_label = FIGURE_SCOPE_LABELS["single"]
    multiple_label = FIGURE_SCOPE_LABELS["multiple"]

    def style_row(row: pd.Series) -> list[str]:
        scope = row.get(scope_column, "")
        if scope == single_label:
            style = "background-color: #eaf4ff; color: #17324d;"
        elif scope == multiple_label:
            style = "background-color: #fff3d6; color: #493300;"
        else:
            style = "background-color: #f4f4f5; color: #27272a;"
        return [style for _ in row]

    return display_df.style.apply(style_row, axis=1)


def _command_result_frame(results: list[dict[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(results).rename(columns=COMMAND_RESULT_COLUMN_LABELS)


def _format_duration(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return "計算中(Calculating)"
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}時間{minutes}分{secs}秒({hours}h {minutes}m {secs}s)"
    if minutes:
        return f"{minutes}分{secs}秒({minutes}m {secs}s)"
    return f"{secs}秒({secs}s)"


def _progress_status_text(
    completed: int,
    total: int,
    started_at: float,
    *,
    current_label: str | None = None,
) -> str:
    elapsed = time.perf_counter() - started_at
    remaining_seconds: float | None = None
    if completed > 0 and total > completed:
        remaining_seconds = (elapsed / completed) * (total - completed)
    elif total > 0 and completed >= total:
        remaining_seconds = 0.0

    if remaining_seconds is None:
        finish_text = "計算中(Calculating)"
    else:
        finish_at = datetime.now() + timedelta(seconds=remaining_seconds)
        finish_text = finish_at.strftime("%H:%M:%S")

    parts = [
        f"{completed}/{total} 完了(Completed)",
        f"経過(Elapsed): {_format_duration(elapsed)}",
        f"残り(ETA): {_format_duration(remaining_seconds)}",
        f"終了予測(Finish): {finish_text}",
    ]
    if current_label:
        parts.append(f"実行中(Current): {current_label}")
    return " | ".join(parts)


def _progress_ratio(completed: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return min(1.0, max(0.0, completed / total))


def _run_simulation_requests_with_progress(requests: list[SimulationRequest]) -> list[dict[str, Any]]:
    total_runs = sum(request.num_runs for request in requests)
    started_at = time.perf_counter()
    progress_bar = st.progress(
        0.0,
        text=_progress_status_text(0, total_runs, started_at, current_label="準備中(Preparing)"),
    )
    status_placeholder = st.empty()
    partial_results_placeholder = st.empty()
    completed_results: list[dict[str, Any]] = []
    all_results: list[dict[str, Any]] = []
    total_completed = 0

    try:
        for request_index, request in enumerate(requests, start=1):
            condition_label = f"条件 {request_index}/{len(requests)}"

            def on_progress(completed: int, total: int, result: dict[str, Any]) -> None:
                nonlocal total_completed
                total_completed += 1
                result_row = {
                    "condition_index": request_index,
                    "condition_count": len(requests),
                    **result,
                }
                completed_results.append(result_row)
                run_label = str(result.get("run_id", "run"))
                status_text = _progress_status_text(
                    total_completed,
                    total_runs,
                    started_at,
                    current_label=f"{condition_label}: {run_label} 完了(done)",
                )
                progress_bar.progress(_progress_ratio(total_completed, total_runs), text=status_text)
                status_placeholder.info(status_text)
                partial_results_placeholder.dataframe(
                    pd.DataFrame(completed_results),
                    width="stretch",
                    hide_index=True,
                )

            request_results = run_simulation_request(request, progress_callback=on_progress)
            for result in request_results:
                all_results.append(
                    {
                        "condition_index": request_index,
                        "condition_count": len(requests),
                        **result,
                    }
                )
    except Exception:
        failed_text = _progress_status_text(
            total_completed,
            total_runs,
            started_at,
            current_label="失敗(Failed)",
        )
        progress_bar.progress(_progress_ratio(total_completed, total_runs), text=failed_text)
        status_placeholder.error(failed_text)
        raise

    done_text = _progress_status_text(total_runs, total_runs, started_at, current_label="完了(Done)")
    progress_bar.progress(1.0, text=done_text)
    status_placeholder.success(done_text)
    return all_results


def _run_simulation_request_with_progress(request: SimulationRequest) -> list[dict[str, Any]]:
    return _run_simulation_requests_with_progress([request])


def _render_graph_creation_tab(web_config: dict[str, Any], records: list[RunRecord]) -> None:
    _render_graph_creation_job_monitor()

    graph_rows = [
        {
            "command": command_name,
            "scope": _graph_command_scope_label(command_info),
            "label": command_info["label"],
            "description": _graph_command_description(command_info),
            "output": command_info["output"],
        }
        for command_name, command_info in GRAPH_CREATION_COMMANDS.items()
    ]
    st.dataframe(
        _style_graph_command_frame(pd.DataFrame(graph_rows)),
        width="stretch",
        hide_index=True,
    )

    selected_graph_commands = st.multiselect(
        "作成する画像データ(Image data to create)",
        options=list(GRAPH_CREATION_COMMANDS),
        default=list(GRAPH_CREATION_COMMANDS),
        format_func=lambda command: GRAPH_CREATION_COMMANDS[command]["label"],
    )
    plot_overrides = _render_plot_parameter_controls(selected_graph_commands)
    invalid_plot_ranges = _invalid_plot_override_ranges(plot_overrides)
    for message in invalid_plot_ranges:
        st.error(message)

    st.subheader("対象データ(Target data)")
    filtered_target_records = _filter_controls(records, web_config, key_prefix="graph_creation_target")
    target_selection_mode = st.radio(
        "対象runの選び方(Target run selection)",
        options=["filtered_all", "manual"],
        horizontal=True,
        format_func=lambda value: {
            "filtered_all": "フィルタ結果をすべて使う(Use all filtered runs)",
            "manual": "個別に選択(Select individually)",
        }[value],
    )
    if target_selection_mode == "filtered_all":
        selected_target_records = filtered_target_records
        st.caption("個別runリストは表示せず、フィルタ後のrunを全て対象にします(The individual run list is skipped for speed).")
    else:
        search_text = st.text_input(
            "run検索(Run search)",
            help="run IDまたはパスの一部で候補を絞ります(Filter candidates by run ID or path).",
        )
        candidate_records = filtered_target_records
        if search_text.strip():
            keywords = [keyword.lower() for keyword in search_text.split() if keyword.strip()]
            candidate_records = [
                record
                for record in filtered_target_records
                if all(
                    keyword in f"{record.run_id} {record.path}".lower()
                    for keyword in keywords
                )
            ]
        max_candidates = st.number_input(
            "選択候補の最大表示数(Max displayed candidates)",
            min_value=1,
            value=500,
        )
        displayed_candidates = candidate_records[: int(max_candidates)]
        record_by_key = {record.record_key: record for record in displayed_candidates}
        selected_target_keys = st.multiselect(
            "画像作成に使うrun(Target runs)",
            options=list(record_by_key),
            default=[],
            format_func=lambda key: f"{record_by_key[key].run_id} ({_display_project_path(record_by_key[key].path)})",
        )
        selected_target_records = [
            record_by_key[key]
            for key in selected_target_keys
            if key in record_by_key
        ]
        st.caption(
            f"候補 {len(candidate_records)} 件中 {len(displayed_candidates)} 件を表示中"
            f"(Showing {len(displayed_candidates)} of {len(candidate_records)} candidates)."
        )
    col_target_count, col_total_count = st.columns(2)
    col_target_count.metric("対象run数(Target runs)", len(selected_target_records))
    col_total_count.metric("全run数(All runs)", len(records))

    estimated_df = _estimated_figure_frame(selected_graph_commands, selected_target_records)
    estimated_total = int(estimated_df["予測枚数(Estimated figures)"].sum()) if not estimated_df.empty else 0
    st.metric("作成・更新される画像の予測枚数(Estimated generated/updated figures)", estimated_total)
    st.dataframe(estimated_df, width="stretch", hide_index=True)
    st.caption("予測枚数は、対象run数と現在のグラフ設定から計算します。データ不足、対象cycle不足、設定フィルタにより実際の作成枚数が少なくなる場合があります(Actual count may be lower if data is missing, too short, or filtered by plot settings).")

    run_preprocess = st.checkbox(
        "必要な前処理も実行(Run preprocessing)",
        value=True,
    )

    if not st.button(
        "選択した画像データを作成(Create selected image data)",
        disabled=not selected_graph_commands or not selected_target_records or bool(invalid_plot_ranges),
        type="primary",
    ):
        return

    commands_to_run = list(selected_graph_commands)
    if run_preprocess:
        commands_to_run = [*PREPROCESS_COMMANDS, *commands_to_run]

    all_record_keys = {record.record_key for record in records}
    selected_record_keys = {record.record_key for record in selected_target_records}
    uses_subset = selected_record_keys != all_record_keys
    if uses_subset and _aggregate_graph_command_selected(selected_graph_commands) and "aggregate-phase-gap-error" not in commands_to_run:
        commands_to_run = ["aggregate-phase-gap-error", *commands_to_run]

    base_env_overrides: dict[str, str] = {}
    if plot_overrides:
        base_env_overrides["RESEARCH_PROGRAM_PLOT_OVERRIDES"] = json.dumps(
            plot_overrides,
            ensure_ascii=False,
        )

    try:
        job_id, job_path = _start_graph_creation_job(
            commands_to_run=commands_to_run,
            selected_graph_commands=list(selected_graph_commands),
            selected_target_records=selected_target_records,
            all_run_count=len(records),
            env_overrides=base_env_overrides,
            web_config=web_config,
        )
    except Exception as exc:
        st.error(f"グラフ作成ジョブを開始できませんでした(Could not start graph job): {exc}")
        return

    _bump_cache_token("figures")
    if run_preprocess:
        _bump_cache_token("runs")
    st.success(f"グラフ作成ジョブを開始しました(Started graph creation job): {job_id}")
    st.caption(f"ジョブ状態ファイル(Job status file): {_display_project_path(job_path)}")
    st.rerun()


@st.cache_data(show_spinner=False)
def _render_pdf_pages(
    pdf_path_text: str,
    pdf_mtime_ns: int,
    max_pages: int,
    dpi: int,
) -> tuple[bytes, ...]:
    renderer = shutil.which("pdftoppm")
    if renderer is None:
        return tuple()

    pdf_path = Path(pdf_path_text)
    with tempfile.TemporaryDirectory() as temp_dir:
        output_prefix = Path(temp_dir) / "page"
        subprocess.run(
            [
                renderer,
                "-f",
                "1",
                "-l",
                str(max_pages),
                "-r",
                str(dpi),
                "-png",
                str(pdf_path),
                str(output_prefix),
            ],
            check=True,
            capture_output=True,
        )
        page_paths = sorted(Path(temp_dir).glob("page-*.png"))
        return tuple(path.read_bytes() for path in page_paths)


def _render_pdf_preview(pdf_path: Path, max_pages: int = 5, dpi: int = 120) -> None:
    try:
        page_images = _render_pdf_pages(
            pdf_path_text=str(pdf_path),
            pdf_mtime_ns=pdf_path.stat().st_mtime_ns,
            max_pages=max_pages,
            dpi=dpi,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        st.warning(f"PDFプレビューを作成できませんでした(PDF preview could not be generated): {exc}")
        return

    if not page_images:
        st.info("PDFプレビューには pdftoppm が必要です。下のダウンロードボタンを使ってください(PDF preview requires pdftoppm. Please use the download button below).")
        return

    for page_number, page_image in enumerate(page_images, start=1):
        st.image(
            page_image,
            width="stretch",
            caption=f"{pdf_path.name} - ページ(page) {page_number}",
        )


def _convert_raster_page_bytes(page_bytes: bytes, output_format: str) -> tuple[bytes, str]:
    image_format = "JPEG" if output_format in {"jpg", "jpeg"} else output_format.upper()
    mime = "image/jpeg" if image_format == "JPEG" else f"image/{output_format}"
    with Image.open(BytesIO(page_bytes)) as image:
        if image_format == "JPEG" and image.mode in {"RGBA", "LA", "P"}:
            image = image.convert("RGB")
        buffer = BytesIO()
        image.save(buffer, format=image_format)
        return buffer.getvalue(), mime


def _rasterize_pdf_for_download(
    pdf_path: Path,
    output_format: str,
    *,
    max_pages: int,
    dpi: int = 120,
) -> tuple[bytes, str, str]:
    page_images = _render_pdf_pages(
        pdf_path_text=str(pdf_path),
        pdf_mtime_ns=pdf_path.stat().st_mtime_ns,
        max_pages=max_pages,
        dpi=dpi,
    )
    if not page_images:
        raise RuntimeError("PDF rasterization requires pdftoppm")

    if len(page_images) == 1:
        data, mime = _convert_raster_page_bytes(page_images[0], output_format)
        return data, mime, f"{pdf_path.stem}_page_1.{output_format}"

    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for page_number, page_bytes in enumerate(page_images, start=1):
            data, _ = _convert_raster_page_bytes(page_bytes, output_format)
            archive.writestr(f"{pdf_path.stem}_page_{page_number}.{output_format}", data)
    return zip_buffer.getvalue(), "application/zip", f"{pdf_path.stem}_{output_format}_pages.zip"


def _render_figures_tab(web_config: dict[str, Any]) -> None:
    figure_config = web_config.get("figures", {})
    if st.button("画像一覧を更新(Refresh figures)"):
        _bump_cache_token("figures")
        st.rerun()

    assets = _discover_figures_for_web(web_config, _cache_token("figures"))
    assets_df = _figures_to_display_frame(assets)

    st.metric("画像数(Figures)", len(assets))
    if assets_df.empty:
        st.dataframe(pd.DataFrame())
        return

    scope_options = list(dict.fromkeys(assets_df["figure_scope"].tolist()))
    selected_scopes = st.multiselect(
        "データ区分(Data scope)",
        scope_options,
        default=scope_options,
    )
    scope_filtered_df = assets_df[assets_df["figure_scope"].isin(selected_scopes)]
    graph_type_options = list(dict.fromkeys(scope_filtered_df["graph_type"].tolist()))
    selected_graph_types = st.multiselect(
        "グラフ種類(Graph type)",
        graph_type_options,
        default=graph_type_options,
    )
    extension_options = sorted(assets_df["extension"].unique())
    selected_extensions = st.multiselect(
        "拡張子(Extensions)",
        extension_options,
        default=extension_options,
    )
    sort_by = st.selectbox(
        "並び替え(Sort by)",
        options=["figure_scope", "graph_type", "name", "extension", "relative_path", "size_kb"],
        format_func=lambda value: {
            "figure_scope": "データ区分(Data scope)",
            "graph_type": "グラフ種類(Graph type)",
            "name": "ファイル名(Name)",
            "extension": "拡張子(Extension)",
            "relative_path": "相対パス(Relative path)",
            "size_kb": "サイズ(Size)",
        }[value],
    )
    descending = st.checkbox("降順(Descending)", value=False)

    selected_scope_set = set(selected_scopes)
    selected_graph_type_set = set(selected_graph_types)
    selected_extension_set = set(selected_extensions)
    filtered_assets = [
        asset
        for asset in assets
        if _figure_scope_label(asset) in selected_scope_set
        and _figure_graph_type(asset) in selected_graph_type_set
        and asset.extension in selected_extension_set
    ]
    filtered_assets = sorted(
        filtered_assets,
        key=lambda asset: _figure_sort_key(asset, sort_by),
        reverse=descending,
    )
    filtered_df = _figures_to_display_frame(filtered_assets)
    st.dataframe(_style_figure_display_frame(filtered_df), width="stretch", hide_index=True)

    if not filtered_assets:
        return

    selected_asset = st.selectbox(
        "画像(Figure)",
        filtered_assets,
        format_func=lambda asset: f"{_figure_graph_type(asset)} / {asset.relative_path} ({asset.extension})",
    )

    pdf_download_pages = 1
    if selected_asset.is_raster or selected_asset.extension == ".svg":
        st.image(str(selected_asset.path), width="stretch")
    elif selected_asset.extension == ".pdf":
        max_pages = st.number_input("PDFプレビューページ数(PDF preview pages)", min_value=1, max_value=20, value=5)
        pdf_download_pages = int(max_pages)
        _render_pdf_preview(selected_asset.path, max_pages=int(max_pages))
    else:
        st.write(selected_asset.name)

    if selected_asset.extension == ".pdf":
        download_options = figure_config.get("raster_download_formats", ["png", "jpeg", "webp"])
    else:
        download_options = ["original"]
    if selected_asset.is_raster:
        download_options.extend(figure_config.get("raster_download_formats", ["png", "jpeg", "webp"]))
    selected_format = st.selectbox(
        "ダウンロード形式(Download format)",
        download_options,
        format_func=lambda value: "元形式(original)" if value == "original" else value,
    )

    if selected_format == "original":
        data = read_original_bytes(selected_asset.path)
        mime = original_mime_type(selected_asset.path)
        filename = selected_asset.name
    elif selected_asset.extension == ".pdf":
        try:
            data, mime, filename = _rasterize_pdf_for_download(
                selected_asset.path,
                selected_format,
                max_pages=pdf_download_pages,
            )
        except RuntimeError as exc:
            st.warning(f"PDFをラスター化できませんでした(Could not rasterize PDF): {exc}")
            return
    else:
        data, mime, filename = convert_raster_image(selected_asset.path, selected_format)

    st.download_button(
        "画像をダウンロード(Download figure)",
        data=data,
        file_name=filename,
        mime=mime,
    )


def _render_contract_tab(contract: RunDataContract) -> None:
    st.write(contract.version)
    st.json(contract.layout)

    rows: list[dict[str, Any]] = []
    for file_key, file_spec in contract.files.items():
        for column in file_spec.columns:
            rows.append(
                {
                    "file": file_key,
                    "column": column.name,
                    "type": column.dtype,
                    "required": column.required,
                    "unit": column.unit or "",
                    "aliases": ";".join(column.aliases),
                    "description": column.description,
                }
            )
    st.dataframe(pd.DataFrame(rows).rename(columns=CONTRACT_COLUMN_LABELS), width="stretch", hide_index=True)


def _cleanup_result_to_frame(result: CleanupResult) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "path": str(item.path.relative_to(PROJECT_ROOT)),
                "kind": "ディレクトリ(directory)" if item.is_dir else "ファイル(file)",
                "size_kb": round(item.size_bytes / 1024, 1),
            }
            for item in result.items
        ]
    )


def _render_maintenance_tab() -> None:
    st.subheader("実験結果の削除(Delete Experiment Outputs)")
    st.warning("生成された実験結果を削除します。実機の元データはデフォルトでは選択されません(This deletes generated experiment outputs. Raw real-device data is not selected by default).")

    target_options = {
        "runs": "data/runs",
        "aggregated": "data/aggregated",
        "figures": "outputs/figures",
        "reports": "outputs/reports",
        "raw_real": "data/raw/real",
        "raw_simulation": "data/raw/simulation",
    }
    selected_targets = st.multiselect(
        "削除対象(Targets)",
        options=list(target_options),
        default=["runs", "aggregated", "figures"],
        format_func=lambda key: f"{CLEANUP_TARGET_LABELS.get(key, key)}: {target_options[key]}",
    )

    preview_result = cleanup_experiment_outputs(
        target_names=tuple(selected_targets),
        dry_run=True,
    )
    col_count, col_size = st.columns(2)
    col_count.metric("項目数(Items)", preview_result.deleted_count)
    col_size.metric("サイズ[MB](Size [MB])", f"{preview_result.deleted_size_mb:.3f}")

    preview_df = _cleanup_result_to_frame(preview_result)
    st.dataframe(preview_df.rename(columns=CLEANUP_COLUMN_LABELS), width="stretch", hide_index=True)

    confirmation = st.text_input("削除を有効にするには DELETE と入力(Type DELETE to enable deletion)")
    delete_disabled = confirmation != "DELETE" or not selected_targets or preview_result.deleted_count == 0

    if st.button("選択した結果を削除(Delete selected outputs)", disabled=delete_disabled, type="primary"):
        result = cleanup_experiment_outputs(
            target_names=tuple(selected_targets),
            dry_run=False,
        )
        st.success(f"{result.deleted_count} 件、{result.deleted_size_mb:.3f} MB を削除しました(Deleted {result.deleted_count} item(s), {result.deleted_size_mb:.3f} MB).")
        _bump_cache_token("runs")
        _bump_cache_token("figures")
        st.cache_data.clear()
        st.rerun()


def main() -> None:
    st.set_page_config(
        page_title="研究プログラム(Research Program)",
        layout="wide",
    )
    st.title("研究プログラム(Research Program)")

    web_config, contract = _load_runtime()
    page_options = {
        "runs": "実行結果(Runs)",
        "simulation": "シミュレーション(Simulation)",
        "graph_creation": "グラフ作成(Graph creation)",
        "figures": "画像(Figures)",
        "maintenance": "管理(Maintenance)",
        "contract": "データ形式(Data format)",
    }
    page = st.sidebar.radio(
        "ページ(Page)",
        options=list(page_options),
        format_func=lambda key: page_options[key],
    )

    records: list[RunRecord] | None = None
    if page in {"runs", "graph_creation"}:
        refresh_runs_clicked = st.sidebar.button("run一覧を更新(Refresh runs)")
        rebuild_runs_clicked = st.sidebar.button(
            "runインデックス再構築(Rebuild run index)",
            help="metadata.csvを手で編集した場合など、インデックスを作り直したい時に使います(Use this after manual metadata edits).",
        )
        if refresh_runs_clicked or rebuild_runs_clicked:
            _bump_cache_token("runs")
        records = _discover_records(
            web_config,
            contract,
            _cache_token("runs"),
            force_rescan=rebuild_runs_clicked,
        )
        st.sidebar.metric("読み込み済みrun数(Loaded runs)", len(records))

    if page == "runs":
        assert records is not None
        _render_runs_tab(records, web_config)
    elif page == "simulation":
        _render_simulation_tab(web_config)
    elif page == "graph_creation":
        assert records is not None
        _render_graph_creation_tab(web_config, records)
    elif page == "figures":
        _render_figures_tab(web_config)
    elif page == "maintenance":
        _render_maintenance_tab()
    elif page == "contract":
        _render_contract_tab(contract)


if __name__ == "__main__":
    main()
