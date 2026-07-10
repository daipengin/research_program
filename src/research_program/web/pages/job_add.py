from __future__ import annotations

import math
import sqlite3
from contextlib import closing
from pathlib import Path

import streamlit as st

from research_program.graph_workflow.execution import (
    available_coupling_functions,
)
from research_program.graph_workflow.storage import (
    RAW_RUN_DB_NAME,
    create_convergence_cycle_vs_k_job,
    create_interval_per_vs_k_job,
    create_phase_gap_error_vs_k_job,
    list_graph_jobs,
    load_graph_job,
)
from research_program.simulation.lora_airtime import (
    LoRaAirtimeConfig,
    calculate_lora_airtime_ms,
    resolve_low_data_rate_optimize,
)
from research_program.web.components.plot_settings import (
    plot_settings_key_fragment,
    render_plot_settings,
)
from research_program.web.utils import (
    build_k_values,
    build_lora_airtime_config,
    duration_input_ms,
    format_bytes,
    format_duration_ms,
    format_percent_range,
    load_last_convergence_cycle_vs_k_params,
    load_last_interval_per_vs_k_params,
    load_last_phase_gap_error_vs_k_params,
    read_json,
    save_last_convergence_cycle_vs_k_params,
    save_last_interval_per_vs_k_params,
    save_last_phase_gap_error_vs_k_params,
    select_index,
    symbol_duration_ms,
)


def render_job_add_page() -> None:
    st.header("ジョブ追加 / Add Job")
    st.caption(
        "作成するグラフ種を先に選ぶと、必要な設定だけを表示します。 / "
        "Select a graph target first; only the required settings are shown."
    )

    saved_interval_params = load_last_interval_per_vs_k_params()
    saved_interval_base = dict(saved_interval_params.get("simulation_base") or {})
    saved_interval_plot = dict(saved_interval_params.get("plot_settings") or {})
    saved_convergence_params = load_last_convergence_cycle_vs_k_params()
    saved_convergence_base = dict(saved_convergence_params.get("simulation_base") or {})
    saved_convergence_plot = dict(saved_convergence_params.get("plot_settings") or {})
    saved_phase_gap_params = load_last_phase_gap_error_vs_k_params()
    saved_phase_gap_base = dict(saved_phase_gap_params.get("simulation_base") or {})
    saved_phase_gap_plot = dict(saved_phase_gap_params.get("plot_settings") or {})

    graph_target = st.selectbox(
        "グラフ種 / Graph target",
        ["Interval PER vs K", "Convergence cycle vs K", "Phase-gap error vs K"],
        help="1ジョブで1つのgraph folderを作ります。 / One job creates one graph folder.",
    )

    if graph_target == "Interval PER vs K":
        render_interval_per_vs_k_job_add_page(
            saved_interval_params,
            saved_interval_base,
            saved_interval_plot,
        )
    elif graph_target == "Convergence cycle vs K":
        render_convergence_job_add_page(
            saved_convergence_params,
            saved_convergence_base,
            saved_convergence_plot,
        )
    else:
        render_phase_gap_error_job_add_page(
            saved_phase_gap_params,
            saved_phase_gap_base,
            saved_phase_gap_plot,
        )


def render_interval_per_vs_k_job_add_page(
    saved_params: dict[str, object],
    saved_base: dict[str, object],
    saved_plot: dict[str, object],
) -> None:
    st.subheader("Interval PER vs K")
    st.caption(
        "指定した時間区間のPERをKごとに平均します。 / "
        "Average interval PER for each K over the selected time window."
    )

    with st.form("interval_per_vs_k_job"):
        st.subheader("対象グラフ / Graph Target")
        col_left, col_right = st.columns(2)
        with col_left:
            graph_type = "Interval PER vs K"
            st.text_input("グラフ種 / Graph type", value=graph_type, disabled=True)
            coupling_options = available_coupling_functions()
            coupling_function = st.selectbox(
                "結合関数 / Coupling function",
                coupling_options,
                index=select_index(coupling_options, str(saved_params.get("coupling_function", "KURAMOTO"))),
            )
            k_start = st.number_input("K開始 / K start", value=float(saved_params.get("k_start", 0.0)), step=1.0)
            k_stop = st.number_input("K終了 / K stop", value=float(saved_params.get("k_stop", 20.0)), step=1.0)
            k_step = st.number_input(
                "K刻み / K step",
                value=float(saved_params.get("k_step", 5.0)),
                min_value=0.000001,
                step=1.0,
            )
            runs_per_k = st.number_input(
                "Kごとのrun数 / Runs per K",
                min_value=1,
                value=int(saved_params.get("runs_per_k", 10)),
                step=1,
            )
        with col_right:
            interval_start_ms = duration_input_ms(
                "PER計算開始 / Interval start",
                "interval_start",
                float(saved_params.get("interval_start_ms", 0.0)),
                min_value=0.0,
            )
            interval_end_ms = duration_input_ms(
                "PER計算終了 / Interval end",
                "interval_end",
                float(saved_params.get("interval_end_ms", 2_000_000.0)),
                min_value=0.0,
            )
            simulation_duration_ms = duration_input_ms(
                "シミュレーション時間 / Simulation duration",
                "simulation_duration",
                float(saved_base.get("duration_ms", 2_000_000.0)),
                min_value=1.0,
            )
            cycle_time = int(
                duration_input_ms(
                    "周期時間 / Cycle time",
                    "cycle_time",
                    float(saved_base.get("cycle_time", 30_000)),
                    min_value=1.0,
                )
            )
            st.caption(
                "初期タイミング: 指定した周期範囲から1 ms刻みで重複ありランダム選択。 / "
                "Initial timing: random 1 ms points in the selected cycle range, with replacement."
            )

        st.subheader("シミュレーション / Simulation")
        sim_a, sim_b, sim_c = st.columns(3)
        with sim_a:
            seed = st.number_input("基準seed / Base seed", value=int(saved_base.get("seed", 1)), step=1)
            device_count = st.number_input(
                "端末数 / Device count",
                min_value=1,
                value=int(saved_base.get("device_count", 20)),
                step=1,
            )
            listening_rate = st.number_input(
                "待受率 / Listening rate",
                min_value=0,
                value=int(saved_base.get("listening_rate", 25)),
                step=1,
            )
        with sim_b:
            strength_ratio = st.number_input(
                "強度倍率 / Strength ratio",
                value=float(saved_base.get("strength_ratio", -0.0001)),
                step=0.0001,
                format="%.6f",
            )
            max_workers = st.number_input(
                "最大worker数 / Max workers",
                min_value=0,
                value=int(saved_base.get("max_workers", 1)),
                step=1,
            )
            simulation_mode = "per_measurement"
            st.text_input(
                "シミュレーションモード / Simulation mode",
                value=simulation_mode,
                disabled=True,
                help="Interval PER vs KではLoRa airtimeを送信時間として使います。 / Interval PER vs K uses LoRa airtime as transmission time.",
            )
        with sim_c:
            initial_phase_start_percent = st.number_input(
                "初期位相範囲 開始% / Initial phase start %",
                min_value=0.0,
                max_value=100.0,
                value=float(saved_base.get("initial_phase_start_percent", 0.0)),
                step=1.0,
            )
            initial_phase_end_percent = st.number_input(
                "初期位相範囲 終了% / Initial phase end %",
                min_value=0.0,
                max_value=100.0,
                value=float(saved_base.get("initial_phase_end_percent", 100.0)),
                step=1.0,
            )
            carrier_sense_duration_ms = duration_input_ms(
                "キャリアセンス時間 / Carrier sense duration",
                "carrier_sense_duration",
                float(saved_base.get("carrier_sense_duration_ms", 0.0)),
                min_value=0.0,
            )

        st.subheader("LoRa設定 / LoRa Settings")
        lora_col_a, lora_col_b, lora_col_c = st.columns(3)
        with lora_col_a:
            lora_payload_bytes = st.number_input(
                "ペイロードbytes / Payload bytes",
                min_value=0,
                value=int(saved_base.get("lora_payload_bytes", 16)),
                step=1,
            )
            lora_spreading_factor = st.number_input(
                "拡散率 / Spreading factor",
                min_value=5,
                max_value=12,
                value=int(saved_base.get("lora_spreading_factor", 7)),
                step=1,
            )
        with lora_col_b:
            lora_bandwidth_hz = st.number_input(
                "帯域幅Hz / Bandwidth Hz",
                min_value=1,
                value=int(saved_base.get("lora_bandwidth_hz", 125_000)),
                step=1000,
            )
            lora_coding_rate_denominator = st.number_input(
                "符号化率分母 / Coding rate denominator",
                min_value=5,
                max_value=8,
                value=int(saved_base.get("lora_coding_rate_denominator", 5)),
                step=1,
            )
        with lora_col_c:
            lora_preamble_symbols = st.number_input(
                "プリアンブルsymbol数 / Preamble symbols",
                min_value=0,
                value=int(saved_base.get("lora_preamble_symbols", 8)),
                step=1,
            )
            lora_explicit_header = st.checkbox(
                "明示ヘッダー / Explicit header",
                value=bool(saved_base.get("lora_explicit_header", True)),
            )
            lora_crc_enabled = st.checkbox(
                "CRC有効 / CRC enabled",
                value=bool(saved_base.get("lora_crc_enabled", True)),
            )
            lora_low_data_rate_optimize_mode = st.selectbox(
                "低データレート最適化 / Low data rate optimize",
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
        preview_clicked = st.form_submit_button("airtimeとrun数を確認 / Preview airtime and run count")
        submitted = st.form_submit_button("ジョブ追加 / Add job", type="primary")

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

    if submitted:
        if interval_end_ms <= interval_start_ms:
            st.error("PER計算終了は開始より大きくしてください。 / Interval end must be larger than interval start.")
            return
        if initial_phase_end_percent <= initial_phase_start_percent:
            st.error("初期位相範囲の終了%は開始%より大きくしてください。 / Initial phase end % must be larger than initial phase start %.")
            return
        if not k_values:
            st.error("K範囲から値を生成できませんでした。 / K range did not produce any values.")
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
        st.success("ジョブを追加しました。 / Job added.")
        st.code(str(job.path), language="text")
        st.info("ジョブ確認ページで Run を押すと実行できます。 / Open Job Status and press Run to execute it.")
        return
        with st.spinner("シミュレーション実行、raw_run.sqlite保存、集計、PDF描画中... / Running simulations, saving raw_run.sqlite, aggregating, and rendering PDF..."):
            st.info("ジョブ確認ページで Run を押すと実行できます。 / Open Job Status and press Run to execute it.")
        if True:
            st.info("Job is queued. Open Job Status and press Run.")
        else:
            st.success("ジョブが完了しました。 / Job completed.")
            st.code(str(result["output"]), language="text")

def render_convergence_job_add_page(
    saved_params: dict[str, object],
    saved_base: dict[str, object],
    saved_plot: dict[str, object],
) -> None:
    st.subheader("収束サイクル vs K / Convergence cycle vs K")
    st.caption(
        "Kごとに収束したサイクル数を平均します。 / "
        "Average the convergence cycle for each K."
    )
    with st.expander("収束定義 / Convergence definition", expanded=True):
        st.markdown(
            """
**判定の流れ / Procedure**

1. 各サイクルで、各振動子の最初の送信時刻を位相に変換します。  
   Convert each oscillator's first transmission time in a cycle into phase.
2. 位相順に並べ、隣接する振動子間の位相差を計算します。最後と最初の周回差も含めます。  
   Sort by phase and calculate adjacent phase gaps, including the wrap-around gap.
3. 前の評価サイクルと比べて、すべての隣接位相差の変化が閾値以下なら、そのサイクルを stable とします。  
   A cycle is stable if every adjacent-gap change from the previous evaluated cycle is at or below the threshold.
4. stable が `N` サイクル連続した最初のサイクルを収束サイクルとします。  
   The convergence cycle is the first cycle where stable continues for `N` consecutive cycles.

**注意 / Notes**

- 全振動子が揃っていないサイクルは判定せず、連続カウントをリセットします。  
  Cycles missing any oscillator are skipped and reset the consecutive count.
- 未収束runは平均サイクルには入れず、収束率として別に集計します。  
  Non-converged runs are excluded from mean cycle values and counted in the convergence rate.
"""
        )


    existing_jobs = [
        job
        for job in list_graph_jobs()
        if job.status == "completed" and (job.path / RAW_RUN_DB_NAME).exists()
    ]
    source_options = ["new_simulation", "existing_graph"]
    source_labels: list[str] = []
    if existing_jobs:
        source_labels = [
            f"{job.graph_id} / {job.graph_type} / {job.completed_runs}/{job.total_runs}"
            for job in existing_jobs
        ]

    source_mode = st.selectbox(
        "データ元 / Data source",
        source_options,
        format_func=lambda value: "新規シミュレーション / New simulation"
        if value == "new_simulation"
        else "既存graph folder / Existing graph folder",
        index=select_index(source_options, str(saved_params.get("source_mode", "new_simulation"))),
        key="conv_source_mode",
    )
    selected_source_label = ""
    source_job = None
    source_summary = []
    if source_mode == "existing_graph":
        external_source_path = st.text_input(
            "外部graph folder path / External graph folder path",
            value=external_source_path_default(saved_params),
            key="conv_external_source_graph_path",
            help="例 / Example: F:\\researchDatas\\20260704_230626_8d964914",
        )
        if external_source_path.strip():
            source_job, source_error = load_external_source_job(external_source_path)
            if source_error:
                st.error(source_error)
        elif not source_labels:
            st.warning("選択できるローカルgraph folderがありません。外部パスを入力してください。 / No local graph folder is available. Enter an external path.")
        else:
            selected_source_label = st.selectbox(
                "元graph folder / Source graph folder",
                source_labels,
                key="conv_source_graph",
            )
            source_job = existing_jobs[source_labels.index(selected_source_label)]
        if source_job is not None:
            selected_source_label = str(source_job.path)
            source_summary = source_run_summary(source_job.path / "graph_data.sqlite")
            source_manifest = read_json(source_job.path / "manifest.json")
            source_requests = read_json(source_job.path / "requests.json")
            source_params = source_requests.get("params", source_manifest.get("input", {}))
            source_base = dict(source_params.get("simulation_base") or {})
            render_source_graph_summary(source_job, source_params, source_base, source_summary)
            st.caption(
                "選択した既存runから収束集計とPDFを作ります。raw dataは元graph folderを参照します。 / "
                "The convergence aggregate and PDF are built from selected existing runs; raw data stays in the source graph folder."
            )

    with st.form("convergence_cycle_vs_k_job"):
        graph_col, condition_col = st.columns(2)
        with graph_col:
            coupling_options = available_coupling_functions()
            coupling_function = st.selectbox(
                "結合関数 / Coupling function",
                coupling_options,
                index=select_index(coupling_options, str(saved_params.get("coupling_function", "KURAMOTO"))),
                disabled=source_mode == "existing_graph",
                key="conv_coupling_function",
            )
            k_start = st.number_input(
                "K開始 / K start",
                value=float(saved_params.get("k_start", 0.0)),
                step=1.0,
                disabled=source_mode == "existing_graph",
                key="conv_k_start",
            )
            k_stop = st.number_input(
                "K終了 / K stop",
                value=float(saved_params.get("k_stop", 20.0)),
                step=1.0,
                disabled=source_mode == "existing_graph",
                key="conv_k_stop",
            )
            k_step = st.number_input(
                "K刻み / K step",
                value=float(saved_params.get("k_step", 5.0)),
                min_value=0.000001,
                step=1.0,
                disabled=source_mode == "existing_graph",
                key="conv_k_step",
            )
            runs_per_k = st.number_input(
                "Kごとのrun数 / Runs per K",
                min_value=1,
                value=int(saved_params.get("runs_per_k", 10)),
                step=1,
                disabled=source_mode == "existing_graph",
                key="conv_runs_per_k",
            )

            selected_k_values = []
            repeat_index_min = None
            repeat_index_max = None
            selected_run_count = 0
            if source_mode == "existing_graph" and source_summary:
                k_options = [float(row["coupling_strength"]) for row in source_summary]
                selected_k_values = k_options
                st.info(
                    f"使用K範囲 / K range used: {min(k_options):g} to {max(k_options):g} "
                    f"({len(k_options)} points). 全Kを使用します。 / All K values are used."
                )
                filtered_summary = source_summary
                if filtered_summary:
                    repeat_min_available = min(int(row["repeat_min"]) for row in filtered_summary)
                    repeat_max_available = max(int(row["repeat_max"]) for row in filtered_summary)
                    repeat_index_min, repeat_index_max = st.slider(
                        "使用するrepeat index範囲 / Repeat index range to use",
                        min_value=repeat_min_available,
                        max_value=repeat_max_available,
                        value=clamped_repeat_range(
                            saved_params.get("repeat_index_min"),
                            saved_params.get("repeat_index_max"),
                            repeat_min_available,
                            repeat_max_available,
                        ),
                        step=1,
                        key="conv_repeat_range",
                    )
                    selected_run_count = count_source_runs_in_repeat_range(
                        source_job.path / "graph_data.sqlite",
                        int(repeat_index_min),
                        int(repeat_index_max),
                    ) if source_job is not None else 0
                    st.info(
                        f"選択run数 / Selected runs: {selected_run_count}"
                    )
                else:
                    st.warning("元データにKがありません。 / No K values were found in the source data.")
        with condition_col:
            stable_cycle_count = st.number_input(
                "安定継続サイクル数N / Stable cycle count N",
                min_value=1,
                value=int(saved_params.get("stable_cycle_count", 5)),
                step=1,
            )
            phase_gap_change_threshold = st.number_input(
                "位相差変化の閾値[rad] / Phase gap change threshold [rad]",
                min_value=0.0,
                value=float(saved_params.get("phase_gap_change_threshold", 0.01)),
                step=0.001,
                format="%.6f",
            )
            if False:
                st.caption(
                    "最後の有効サイクルを使う場合、この値は保存時に無視されます。 / "
                    "When using the final available cycle, this value is ignored when the job is saved."
                )
            if source_mode == "new_simulation":
                simulation_duration_ms = duration_input_ms(
                    "シミュレーション時間 / Simulation duration",
                    "conv_simulation_duration",
                    float(saved_base.get("duration_ms", 2_000_000.0)),
                    min_value=1.0,
                )
                cycle_time = int(
                    duration_input_ms(
                        "周期時間 / Cycle time",
                        "conv_cycle_time",
                        float(saved_base.get("cycle_time", 30_000)),
                        min_value=1.0,
                    )
                )
            elif source_job is not None:
                source_manifest = read_json(source_job.path / "manifest.json")
                source_requests = read_json(source_job.path / "requests.json")
                source_params = source_requests.get("params", source_manifest.get("input", {}))
                source_base = dict(source_params.get("simulation_base") or {})
                st.metric(
                    "元データの周期時間 / Source cycle time",
                    format_duration_ms(float(source_base.get("cycle_time", 0) or 0)),
                )
                st.metric(
                    "元データのシミュレーション時間 / Source simulation duration",
                    format_duration_ms(float(source_base.get("duration_ms", 0) or 0)),
                )

        with st.expander("新規シミュレーション設定 / Simulation settings for new simulation", expanded=False):
            sim_a, sim_b, sim_c = st.columns(3)
            with sim_a:
                seed = st.number_input("基準seed / Base seed", value=int(saved_base.get("seed", 1)), step=1, key="conv_seed")
                device_count = st.number_input(
                    "端末数 / Device count",
                    min_value=1,
                    value=int(saved_base.get("device_count", 20)),
                    step=1,
                    key="conv_device_count",
                )
                listening_rate = st.number_input(
                    "待受率 / Listening rate",
                    min_value=0,
                    value=int(saved_base.get("listening_rate", 25)),
                    step=1,
                    key="conv_listening_rate",
                )
            with sim_b:
                strength_ratio = st.number_input(
                    "強度倍率 / Strength ratio",
                    value=float(saved_base.get("strength_ratio", -0.0001)),
                    step=0.0001,
                    format="%.6f",
                    key="conv_strength_ratio",
                )
                max_workers = st.number_input(
                    "最大worker数 / Max workers",
                    min_value=0,
                    value=int(saved_base.get("max_workers", 1)),
                    step=1,
                    key="conv_max_workers",
                )
                carrier_sense_duration_ms = duration_input_ms(
                    "キャリアセンス時間 / Carrier sense duration",
                    "conv_carrier_sense_duration",
                    float(saved_base.get("carrier_sense_duration_ms", 0.0)),
                    min_value=0.0,
                )
            with sim_c:
                initial_phase_start_percent = st.number_input(
                    "初期位相範囲 開始% / Initial phase start %",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(saved_base.get("initial_phase_start_percent", 0.0)),
                    step=1.0,
                    key="conv_initial_phase_start_percent",
                )
                initial_phase_end_percent = st.number_input(
                    "初期位相範囲 終了% / Initial phase end %",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(saved_base.get("initial_phase_end_percent", 100.0)),
                    step=1.0,
                    key="conv_initial_phase_end_percent",
                )

        with st.expander("新規シミュレーション用LoRa設定 / LoRa settings for new simulation", expanded=False):
            lora_a, lora_b, lora_c = st.columns(3)
            with lora_a:
                lora_payload_bytes = st.number_input(
                    "ペイロードbytes / Payload bytes",
                    min_value=0,
                    value=int(saved_base.get("lora_payload_bytes", 16)),
                    step=1,
                    key="conv_lora_payload_bytes",
                )
                lora_spreading_factor = st.number_input(
                    "拡散率 / Spreading factor",
                    min_value=5,
                    max_value=12,
                    value=int(saved_base.get("lora_spreading_factor", 7)),
                    step=1,
                    key="conv_lora_spreading_factor",
                )
            with lora_b:
                lora_bandwidth_hz = st.number_input(
                    "帯域幅Hz / Bandwidth Hz",
                    min_value=1,
                    value=int(saved_base.get("lora_bandwidth_hz", 125_000)),
                    step=1000,
                    key="conv_lora_bandwidth_hz",
                )
                lora_coding_rate_denominator = st.number_input(
                    "符号化率分母 / Coding rate denominator",
                    min_value=5,
                    max_value=8,
                    value=int(saved_base.get("lora_coding_rate_denominator", 5)),
                    step=1,
                    key="conv_lora_coding_rate_denominator",
                )
            with lora_c:
                lora_preamble_symbols = st.number_input(
                    "プリアンブルsymbol数 / Preamble symbols",
                    min_value=0,
                    value=int(saved_base.get("lora_preamble_symbols", 8)),
                    step=1,
                    key="conv_lora_preamble_symbols",
                )
                lora_explicit_header = st.checkbox(
                    "明示ヘッダー / Explicit header",
                    value=bool(saved_base.get("lora_explicit_header", True)),
                    key="conv_lora_explicit_header",
                )
                lora_crc_enabled = st.checkbox(
                    "CRC有効 / CRC enabled",
                    value=bool(saved_base.get("lora_crc_enabled", True)),
                    key="conv_lora_crc_enabled",
                )
                lora_low_data_rate_optimize_mode = st.selectbox(
                    "低データレート最適化 / Low data rate optimize",
                    ["auto", "true", "false"],
                    index=select_index(
                        ["auto", "true", "false"],
                        str(saved_base.get("lora_low_data_rate_optimize", "auto")),
                    ),
                    key="conv_lora_low_data_rate_optimize",
                )

        render_convergence_size_estimate(
            source_mode=source_mode,
            selected_run_count=int(selected_run_count),
            k_count=(
                len(selected_k_values)
                if source_mode == "existing_graph"
                else len(build_k_values(k_start, k_stop, k_step))
            ),
            new_simulation_total_runs=(
                0
                if source_mode == "existing_graph"
                else len(build_k_values(k_start, k_stop, k_step)) * int(runs_per_k)
            ),
            simulation_duration_ms=(
                float(source_base.get("duration_ms", 0) or 0)
                if source_mode == "existing_graph" and source_job is not None
                else (0.0 if source_mode == "existing_graph" else float(simulation_duration_ms))
            ),
            cycle_time_ms=(
                float(source_base.get("cycle_time", 0) or 0)
                if source_mode == "existing_graph" and source_job is not None
                else (0.0 if source_mode == "existing_graph" else float(cycle_time))
            ),
            device_count=(
                int(source_base.get("device_count", 0) or 0)
                if source_mode == "existing_graph" and source_job is not None
                else (0 if source_mode == "existing_graph" else int(device_count))
            ),
        )

        plot_settings = render_plot_settings(
            dict(saved_plot),
            key_prefix=f"conv_plot_{plot_settings_key_fragment(dict(saved_plot))}",
        )
        submitted = st.form_submit_button("収束ジョブ追加 / Add convergence job", type="primary")

    if not submitted:
        return

    if source_mode == "existing_graph":
        if not selected_source_label:
            st.error("元graph folderを選択してください。 / Source graph folder is required.")
            return
        if source_job is None:
            source_job = existing_jobs[source_labels.index(selected_source_label)]
        source_manifest = read_json(source_job.path / "manifest.json")
        source_requests = read_json(source_job.path / "requests.json")
        source_params = source_requests.get("params", source_manifest.get("input", {}))
        source_base = dict(source_params.get("simulation_base") or {})
        if not selected_k_values:
            st.error("元データにKがありません。 / No K values were found in the source data.")
            return
        if selected_run_count <= 0:
            st.error("選択条件に一致するrunがありません。 / No runs match the selected source filters.")
            return
        params = {
            "source_mode": "existing_graph",
            "source_graph_id": source_job.graph_id,
            "source_graph_type": source_job.graph_type,
            "source_graph_dir": str(source_job.path),
            "coupling_function": source_params.get("coupling_function", ""),
            "k_start": min(selected_k_values),
            "k_stop": max(selected_k_values),
            "k_step": source_params.get("k_step"),
            "k_values": selected_k_values,
            "runs_per_k": source_params.get("runs_per_k", 1),
            "repeat_index_min": int(repeat_index_min),
            "repeat_index_max": int(repeat_index_max),
            "selected_run_count": int(selected_run_count),
            "stable_cycle_count": int(stable_cycle_count),
            "phase_gap_change_threshold": float(phase_gap_change_threshold),
            "plot_settings": plot_settings,
            "simulation_base": source_base,
        }
    else:
        k_values = build_k_values(k_start, k_stop, k_step)
        if not k_values:
            st.error("K範囲から値を生成できませんでした。 / K range did not produce any values.")
            return
        if initial_phase_end_percent <= initial_phase_start_percent:
            st.error("初期位相範囲の終了%は開始%より大きくしてください。 / Initial phase end % must be larger than initial phase start %.")
            return
        params = {
            "source_mode": "new_simulation",
            "coupling_function": coupling_function,
            "k_start": float(k_start),
            "k_stop": float(k_stop),
            "k_step": float(k_step),
            "k_values": k_values,
            "runs_per_k": int(runs_per_k),
            "stable_cycle_count": int(stable_cycle_count),
            "phase_gap_change_threshold": float(phase_gap_change_threshold),
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
                "simulation_mode": "per_measurement",
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

    save_last_convergence_cycle_vs_k_params(params)
    job = create_convergence_cycle_vs_k_job(params)
    st.success("Convergence job added.")
    st.code(str(job.path), language="text")
    st.info("ジョブ確認ページで Run を押すと実行できます。 / Open Job Status and press Run to execute it.")
    return
    st.success("収束ジョブを追加しました。 / Convergence job added.")
    st.code(str(job.path), language="text")
    with st.spinner("収束集計とPDF描画中... / Building convergence aggregate and rendering PDF..."):
        st.info("ジョブ確認ページで Run を押すと実行できます。 / Open Job Status and press Run to execute it.")
    if True:
        st.info("Job is queued. Open Job Status and press Run.")
    else:
        st.success("収束ジョブが完了しました。 / Convergence job completed.")
        st.code(str(result["output"]), language="text")


def render_phase_gap_error_job_add_page(
    saved_params: dict[str, object],
    saved_base: dict[str, object],
    saved_plot: dict[str, object],
) -> None:
    st.subheader("最終揺らぎ vs K / Phase-gap error vs K")
    st.caption(
        "指定時点で、隣接振動子間の位相差が理想位相差からどれだけずれているかをKごとに平均します。 / "
        "Average the mean absolute adjacent phase-gap error for each K at the selected point."
    )
    with st.expander("定義 / Definition", expanded=True):
        st.markdown(
            """
**縦軸 / Y axis**

各runについて、対象サイクルの `mean_abs_diff_from_ideal_phase_gap` を1つ取り出します。
これは、隣接する振動子間の位相差と、理想位相差との差の平均絶対値です。

For each run, one `mean_abs_diff_from_ideal_phase_gap` value is read from the selected cycle.
It is the mean absolute difference between adjacent oscillator phase gaps and the ideal phase gap.

**横軸 / X axis**

結合強度 `K` です。同じKのrunを平均し、標準偏差をエラーバーとして描画できます。
"""
        )

    existing_jobs = [
        job
        for job in list_graph_jobs()
        if job.status == "completed" and (job.path / RAW_RUN_DB_NAME).exists()
    ]
    source_options = ["new_simulation", "existing_graph"]
    source_labels: list[str] = []
    if existing_jobs:
        source_labels = [
            f"{job.graph_id} / {job.graph_type} / {job.completed_runs}/{job.total_runs}"
            for job in existing_jobs
        ]

    source_mode = st.selectbox(
        "データ元 / Data source",
        source_options,
        format_func=lambda value: "新規シミュレーション / New simulation"
        if value == "new_simulation"
        else "既存graph folder / Existing graph folder",
        index=select_index(source_options, str(saved_params.get("source_mode", "new_simulation"))),
        key="phase_source_mode",
    )

    selected_source_label = ""
    source_job = None
    source_summary: list[dict[str, object]] = []
    source_params: dict[str, object] = {}
    source_base: dict[str, object] = {}
    if source_mode == "existing_graph":
        external_source_path = st.text_input(
            "外部graph folder path / External graph folder path",
            value=external_source_path_default(saved_params),
            key="phase_external_source_graph_path",
            help="例 / Example: F:\\researchDatas\\20260704_230626_8d964914",
        )
        if external_source_path.strip():
            source_job, source_error = load_external_source_job(external_source_path)
            if source_error:
                st.error(source_error)
        elif not source_labels:
            st.warning("選択できるローカルgraph folderがありません。外部パスを入力してください。 / No local graph folder is available. Enter an external path.")
        else:
            selected_source_label = st.selectbox(
                "元graph folder / Source graph folder",
                source_labels,
                key="phase_source_graph",
            )
            source_job = existing_jobs[source_labels.index(selected_source_label)]
        if source_job is not None:
            selected_source_label = str(source_job.path)
            source_summary = source_run_summary(source_job.path / "graph_data.sqlite")
            source_manifest = read_json(source_job.path / "manifest.json")
            source_requests = read_json(source_job.path / "requests.json")
            source_params = source_requests.get("params", source_manifest.get("input", {}))
            source_base = dict(source_params.get("simulation_base") or {})
            render_source_graph_summary(source_job, source_params, source_base, source_summary)
            st.caption(
                "既存raw_run.sqliteを参照して、揺らぎ集計とPDFだけを新しいgraph folderに作ります。 / "
                "Raw data is referenced from the source graph folder; only the aggregate and PDF are created."
            )

    with st.form("phase_gap_error_vs_k_job"):
        graph_col, point_col = st.columns(2)
        selected_k_values: list[float] = []
        repeat_index_min = None
        repeat_index_max = None
        selected_run_count = 0

        with graph_col:
            coupling_options = available_coupling_functions()
            coupling_function = st.selectbox(
                "結合関数 / Coupling function",
                coupling_options,
                index=select_index(coupling_options, str(saved_params.get("coupling_function", "KURAMOTO"))),
                disabled=source_mode == "existing_graph",
                key="phase_coupling_function",
            )
            k_start = st.number_input("K開始 / K start", value=float(saved_params.get("k_start", 0.0)), step=1.0, disabled=source_mode == "existing_graph", key="phase_k_start")
            k_stop = st.number_input("K終了 / K stop", value=float(saved_params.get("k_stop", 20.0)), step=1.0, disabled=source_mode == "existing_graph", key="phase_k_stop")
            k_step = st.number_input("K刻み / K step", value=float(saved_params.get("k_step", 5.0)), min_value=0.000001, step=1.0, disabled=source_mode == "existing_graph", key="phase_k_step")
            runs_per_k = st.number_input("Kごとのrun数 / Runs per K", min_value=1, value=int(saved_params.get("runs_per_k", 10)), step=1, disabled=source_mode == "existing_graph", key="phase_runs_per_k")

            if source_mode == "existing_graph" and source_summary:
                selected_k_values = [float(row["coupling_strength"]) for row in source_summary]
                st.info(
                    f"使用K範囲 / K range used: {min(selected_k_values):g} to {max(selected_k_values):g} "
                    f"({len(selected_k_values)} points). すべてのKを使います。 / All K values are used."
                )
                repeat_min_available = min(int(row["repeat_min"]) for row in source_summary)
                repeat_max_available = max(int(row["repeat_max"]) for row in source_summary)
                repeat_index_min, repeat_index_max = st.slider(
                    "使用するrepeat index範囲 / Repeat index range to use",
                    min_value=repeat_min_available,
                    max_value=repeat_max_available,
                    value=clamped_repeat_range(saved_params.get("repeat_index_min"), saved_params.get("repeat_index_max"), repeat_min_available, repeat_max_available),
                    step=1,
                    key="phase_repeat_range",
                )
                selected_run_count = count_source_runs_in_repeat_range(source_job.path / "graph_data.sqlite", int(repeat_index_min), int(repeat_index_max)) if source_job is not None else 0
                st.info(f"選択run数 / Selected runs: {selected_run_count}")

        with point_col:
            target_cycle_mode = st.selectbox(
                "対象時点 / Target point",
                ["last", "cycle_index"],
                format_func=lambda value: "最後の有効サイクル / Final available cycle" if value == "last" else "指定サイクル / Specific cycle index",
                index=select_index(["last", "cycle_index"], str(saved_params.get("target_cycle_mode", "last"))),
                key="phase_target_cycle_mode",
            )
            target_cycle_index = st.number_input(
                "対象cycle index / Target cycle index",
                min_value=1,
                value=int_or_default(saved_params.get("target_cycle_index"), 1),
                step=1,
                key="phase_target_cycle_index",
            )
            if source_mode == "new_simulation":
                simulation_duration_ms = duration_input_ms("シミュレーション時間 / Simulation duration", "phase_simulation_duration", float(saved_base.get("duration_ms", 2_000_000.0)), min_value=1.0)
                cycle_time = int(duration_input_ms("周期時間 / Cycle time", "phase_cycle_time", float(saved_base.get("cycle_time", 30_000)), min_value=1.0))
            elif source_job is not None:
                st.metric("元データの周期時間 / Source cycle time", format_duration_ms(float(source_base.get("cycle_time", 0) or 0)))
                st.metric("元データのシミュレーション時間 / Source duration", format_duration_ms(float(source_base.get("duration_ms", 0) or 0)))

        with st.expander("新規シミュレーション設定 / Simulation settings for new simulation", expanded=False):
            sim_a, sim_b, sim_c = st.columns(3)
            with sim_a:
                seed = st.number_input("Base seed", value=int(saved_base.get("seed", 1)), step=1, key="phase_seed")
                device_count = st.number_input("端末数 / Device count", min_value=1, value=int(saved_base.get("device_count", 20)), step=1, key="phase_device_count")
                listening_rate = st.number_input("受信率 / Listening rate", min_value=0, value=int(saved_base.get("listening_rate", 25)), step=1, key="phase_listening_rate")
            with sim_b:
                strength_ratio = st.number_input("Strength ratio", value=float(saved_base.get("strength_ratio", -0.0001)), step=0.0001, format="%.6f", key="phase_strength_ratio")
                max_workers = st.number_input("Max workers", min_value=0, value=int(saved_base.get("max_workers", 1)), step=1, key="phase_max_workers")
                carrier_sense_duration_ms = duration_input_ms("Carrier sense duration", "phase_carrier_sense_duration", float(saved_base.get("carrier_sense_duration_ms", 0.0)), min_value=0.0)
            with sim_c:
                initial_phase_start_percent = st.number_input("初期位相範囲 開始% / Initial phase start %", min_value=0.0, max_value=100.0, value=float(saved_base.get("initial_phase_start_percent", 0.0)), step=1.0, key="phase_initial_phase_start_percent")
                initial_phase_end_percent = st.number_input("初期位相範囲 終了% / Initial phase end %", min_value=0.0, max_value=100.0, value=float(saved_base.get("initial_phase_end_percent", 100.0)), step=1.0, key="phase_initial_phase_end_percent")

        with st.expander("新規シミュレーション用LoRa設定 / LoRa settings for new simulation", expanded=False):
            lora_a, lora_b, lora_c = st.columns(3)
            with lora_a:
                lora_payload_bytes = st.number_input("Payload bytes", min_value=0, value=int(saved_base.get("lora_payload_bytes", 16)), step=1, key="phase_lora_payload_bytes")
                lora_spreading_factor = st.number_input("Spreading factor", min_value=5, max_value=12, value=int(saved_base.get("lora_spreading_factor", 7)), step=1, key="phase_lora_spreading_factor")
            with lora_b:
                lora_bandwidth_hz = st.number_input("Bandwidth Hz", min_value=1, value=int(saved_base.get("lora_bandwidth_hz", 125_000)), step=1000, key="phase_lora_bandwidth_hz")
                lora_coding_rate_denominator = st.number_input("Coding rate denominator", min_value=5, max_value=8, value=int(saved_base.get("lora_coding_rate_denominator", 5)), step=1, key="phase_lora_coding_rate_denominator")
            with lora_c:
                lora_preamble_symbols = st.number_input("Preamble symbols", min_value=0, value=int(saved_base.get("lora_preamble_symbols", 8)), step=1, key="phase_lora_preamble_symbols")
                lora_explicit_header = st.checkbox("Explicit header", value=bool(saved_base.get("lora_explicit_header", True)), key="phase_lora_explicit_header")
                lora_crc_enabled = st.checkbox("CRC enabled", value=bool(saved_base.get("lora_crc_enabled", True)), key="phase_lora_crc_enabled")
                lora_low_data_rate_optimize_mode = st.selectbox("Low data rate optimize", ["auto", "true", "false"], index=select_index(["auto", "true", "false"], str(saved_base.get("lora_low_data_rate_optimize", "auto"))), key="phase_lora_low_data_rate_optimize")

        render_phase_gap_error_size_estimate(
            source_mode=source_mode,
            selected_run_count=int(selected_run_count),
            k_count=len(selected_k_values) if source_mode == "existing_graph" else len(build_k_values(k_start, k_stop, k_step)),
            new_simulation_total_runs=0 if source_mode == "existing_graph" else len(build_k_values(k_start, k_stop, k_step)) * int(runs_per_k),
            simulation_duration_ms=float(source_base.get("duration_ms", 0) or 0) if source_mode == "existing_graph" and source_job is not None else (0.0 if source_mode == "existing_graph" else float(simulation_duration_ms)),
            cycle_time_ms=float(source_base.get("cycle_time", 0) or 0) if source_mode == "existing_graph" and source_job is not None else (0.0 if source_mode == "existing_graph" else float(cycle_time)),
            device_count=int(source_base.get("device_count", 0) or 0) if source_mode == "existing_graph" and source_job is not None else (0 if source_mode == "existing_graph" else int(device_count)),
        )

        plot_settings = render_plot_settings(dict(saved_plot), key_prefix=f"phase_plot_{plot_settings_key_fragment(dict(saved_plot))}")
        submitted = st.form_submit_button("揺らぎジョブ追加 / Add phase-gap error job", type="primary")

    if not submitted:
        return

    if source_mode == "existing_graph":
        if not selected_source_label:
            st.error("元graph folderを選択してください。 / Source graph folder is required.")
            return
        if source_job is None:
            source_job = existing_jobs[source_labels.index(selected_source_label)]
        source_manifest = read_json(source_job.path / "manifest.json")
        source_requests = read_json(source_job.path / "requests.json")
        source_params = source_requests.get("params", source_manifest.get("input", {}))
        source_base = dict(source_params.get("simulation_base") or {})
        if not selected_k_values:
            st.error("元データにKがありません。 / No K values were found in the source data.")
            return
        if selected_run_count <= 0:
            st.error("選択条件に一致するrunがありません。 / No runs match the selected source filters.")
            return
        params = {
            "source_mode": "existing_graph",
            "source_graph_id": source_job.graph_id,
            "source_graph_type": source_job.graph_type,
            "source_graph_dir": str(source_job.path),
            "coupling_function": source_params.get("coupling_function", ""),
            "k_start": min(selected_k_values),
            "k_stop": max(selected_k_values),
            "k_step": source_params.get("k_step"),
            "k_values": selected_k_values,
            "runs_per_k": source_params.get("runs_per_k", 1),
            "repeat_index_min": int(repeat_index_min),
            "repeat_index_max": int(repeat_index_max),
            "selected_run_count": int(selected_run_count),
            "target_cycle_mode": str(target_cycle_mode),
            "target_cycle_index": None if target_cycle_mode == "last" else int(target_cycle_index),
            "plot_settings": plot_settings,
            "simulation_base": source_base,
        }
    else:
        k_values = build_k_values(k_start, k_stop, k_step)
        if not k_values:
            st.error("K範囲から値を生成できませんでした。 / K range did not produce any values.")
            return
        if initial_phase_end_percent <= initial_phase_start_percent:
            st.error("初期位相範囲の終了%は開始%より大きくしてください。 / Initial phase end % must be larger than start %.")
            return
        params = {
            "source_mode": "new_simulation",
            "coupling_function": coupling_function,
            "k_start": float(k_start),
            "k_stop": float(k_stop),
            "k_step": float(k_step),
            "k_values": k_values,
            "runs_per_k": int(runs_per_k),
            "target_cycle_mode": str(target_cycle_mode),
            "target_cycle_index": None if target_cycle_mode == "last" else int(target_cycle_index),
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
                "simulation_mode": "per_measurement",
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

    save_last_phase_gap_error_vs_k_params(params)
    job = create_phase_gap_error_vs_k_job(params)
    st.success("揺らぎジョブを追加しました。 / Phase-gap error job added.")
    st.code(str(job.path), language="text")
    st.info("ジョブ確認ページで Run を押すと実行できます。 / Open Job Status and press Run to execute it.")
    return
    st.info("ジョブ確認ページで Run を押すと実行できます。 / Open Job Status and press Run to execute it.")
    return
    with st.spinner("揺らぎ集計とPDF描画中... / Building phase-gap error aggregate and rendering PDF..."):
        st.info("ジョブ確認ページで Run を押すと実行できます。 / Open Job Status and press Run to execute it.")
    if True:
        st.info("Job is queued. Open Job Status and press Run.")
    else:
        st.success("揺らぎジョブが完了しました。 / Phase-gap error job completed.")
        st.code(str(result["output"]), language="text")


def render_phase_gap_error_size_estimate(
    *,
    source_mode: str,
    selected_run_count: int,
    k_count: int,
    new_simulation_total_runs: int,
    simulation_duration_ms: float,
    cycle_time_ms: float,
    device_count: int,
) -> None:
    if source_mode == "existing_graph":
        target_runs = max(selected_run_count, 0)
        raw_added_bytes = 0
        raw_source_label = "0 B / existing raw_run.sqlite is referenced"
    else:
        target_runs = max(new_simulation_total_runs, 0)
        raw_estimate = estimate_job_data_size(
            total_runs=target_runs,
            simulation_duration_ms=simulation_duration_ms,
            cycle_time_ms=cycle_time_ms,
            device_count=device_count,
        )
        raw_added_bytes = int(raw_estimate["total_bytes"])
        raw_source_label = format_bytes(raw_added_bytes)

    aggregate_bytes = estimate_phase_gap_error_aggregate_bytes(run_count=target_runs, k_count=k_count)
    total_added_bytes = raw_added_bytes + aggregate_bytes

    st.markdown("**データ量予測 / Estimated data size**")
    cols = st.columns(4)
    cols[0].metric("対象run数 / Target runs", target_runs)
    cols[1].metric("raw追加量 / Raw added", raw_source_label)
    cols[2].metric("揺らぎ集計追加量 / Phase-gap aggregate", format_bytes(aggregate_bytes))
    cols[3].metric("合計追加量 / Total added", format_bytes(total_added_bytes))


def estimate_phase_gap_error_aggregate_bytes(*, run_count: int, k_count: int) -> int:
    return int(1_000_000 + max(run_count, 0) * 260 + max(k_count, 0) * 512 + 300_000)


def render_convergence_size_estimate(
    *,
    source_mode: str,
    selected_run_count: int,
    k_count: int,
    new_simulation_total_runs: int,
    simulation_duration_ms: float,
    cycle_time_ms: float,
    device_count: int,
) -> None:
    if source_mode == "existing_graph":
        target_runs = max(selected_run_count, 0)
        raw_added_bytes = 0
        raw_source_label = "0 B（既存raw_run.sqliteを参照 / existing raw_run.sqlite is referenced）"
    else:
        target_runs = max(new_simulation_total_runs, 0)
        raw_estimate = estimate_job_data_size(
            total_runs=target_runs,
            simulation_duration_ms=simulation_duration_ms,
            cycle_time_ms=cycle_time_ms,
            device_count=device_count,
        )
        raw_added_bytes = int(raw_estimate["total_bytes"])
        raw_source_label = format_bytes(raw_added_bytes)

    aggregate_bytes = estimate_convergence_aggregate_bytes(
        run_count=target_runs,
        k_count=k_count,
    )
    total_added_bytes = raw_added_bytes + aggregate_bytes

    st.markdown("**データ量予測 / Estimated data size**")
    cols = st.columns(4)
    cols[0].metric("対象run数 / Target runs", target_runs)
    cols[1].metric("raw追加量 / Raw added", raw_source_label)
    cols[2].metric("収束集計追加量 / Convergence aggregate", format_bytes(aggregate_bytes))
    cols[3].metric("合計追加量 / Total added", format_bytes(total_added_bytes))

    if source_mode == "existing_graph":
        st.caption(
            "既存データ利用時はraw dataをコピーせず、収束集計テーブルとPDFだけを新しいgraph folderに作成します。 / "
            "When using existing data, raw data is not copied; only convergence aggregate tables and the PDF are created in the new graph folder."
        )
    else:
        st.caption(
            "新規シミュレーション時はraw_run.sqlite、収束集計、PDFが新しいgraph folderに作成されます。 / "
            "For a new simulation, raw_run.sqlite, convergence aggregates, and the PDF are created in the new graph folder."
        )


def estimate_convergence_aggregate_bytes(*, run_count: int, k_count: int) -> int:
    # run_convergence_cycles is one row per run; aggregate_convergence_cycles is one row per K.
    return int(1_000_000 + max(run_count, 0) * 260 + max(k_count, 0) * 512 + 300_000)


def render_source_graph_summary(
    source_job: object,
    source_params: dict[str, object],
    source_base: dict[str, object],
    source_summary: list[dict[str, object]],
) -> None:
    k_values = [float(row["coupling_strength"]) for row in source_summary]
    k_label = "unknown"
    if k_values:
        k_label = f"{min(k_values):g} to {max(k_values):g} ({len(k_values)} points)"

    with st.container(border=True):
        st.markdown("**元データ情報 / Source data summary**")
        row_a = st.columns(4)
        row_a[0].metric(
            "結合関数 / Coupling function",
            str(source_params.get("coupling_function", source_job.graph_key.get("coupling_function", ""))),
        )
        row_a[1].metric("グラフ種 / Graph type", str(source_job.graph_type))
        row_a[2].metric("K範囲 / K range", k_label)
        row_a[3].metric(
            "Kごとのrun数 / Runs per K",
            str(source_params.get("runs_per_k", source_summary[0]["run_count"] if source_summary else "")),
        )

        row_b = st.columns(4)
        row_b[0].metric(
            "端末数 / Device count",
            str(source_base.get("device_count", "")),
        )
        row_b[1].metric(
            "周期時間 / Cycle time",
            format_duration_ms(float(source_base.get("cycle_time", 0) or 0)),
        )
        row_b[2].metric(
            "シミュレーション時間 / Duration",
            format_duration_ms(float(source_base.get("duration_ms", 0) or 0)),
        )
        row_b[3].metric(
            "完了run / Completed runs",
            f"{source_job.completed_runs}/{source_job.total_runs}",
        )


def source_run_summary(db_path: Path) -> list[dict[str, object]]:
    if not db_path.exists():
        return []
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            rows = conn.execute(
                """
                SELECT coupling_strength,
                       COUNT(*) AS run_count,
                       MIN(repeat_index) AS repeat_min,
                       MAX(repeat_index) AS repeat_max
                FROM runs
                WHERE status = 'completed'
                GROUP BY coupling_strength
                ORDER BY coupling_strength
                """
            ).fetchall()
        except sqlite3.Error:
            return []
    return [
        {
            "coupling_strength": float(row[0]),
            "run_count": int(row[1]),
            "repeat_min": int(row[2]),
            "repeat_max": int(row[3]),
        }
        for row in rows
    ]


def count_source_runs_in_repeat_range(
    db_path: Path,
    repeat_index_min: int,
    repeat_index_max: int,
) -> int:
    if not db_path.exists():
        return 0
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM runs
                WHERE status = 'completed'
                  AND repeat_index >= ?
                  AND repeat_index <= ?
                """,
                (int(repeat_index_min), int(repeat_index_max)),
            ).fetchone()
        except sqlite3.Error:
            return 0
    return int(row[0] if row else 0)


def clamped_repeat_range(
    saved_min: object,
    saved_max: object,
    available_min: int,
    available_max: int,
) -> tuple[int, int]:
    try:
        value_min = int(saved_min)
    except (TypeError, ValueError):
        value_min = int(available_min)
    try:
        value_max = int(saved_max)
    except (TypeError, ValueError):
        value_max = int(available_max)
    value_min = max(int(available_min), min(value_min, int(available_max)))
    value_max = max(int(available_min), min(value_max, int(available_max)))
    if value_max < value_min:
        value_min, value_max = value_max, value_min
    return value_min, value_max


def external_source_path_default(saved_params: dict[str, object]) -> str:
    value = saved_params.get("source_graph_dir")
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    try:
        path = Path(text)
    except (OSError, ValueError):
        return ""
    return text if path.is_absolute() else ""


def load_external_source_job(path_text: str) -> tuple[object | None, str]:
    text = path_text.strip().strip('"')
    if not text:
        return None, ""
    try:
        graph_dir = Path(text)
    except (OSError, ValueError) as exc:
        return None, f"Invalid external graph folder path: {exc}"
    if graph_dir.name == "manifest.json":
        graph_dir = graph_dir.parent
    if graph_dir.is_dir() and not (graph_dir / "manifest.json").exists():
        matches = [
            path.parent
            for path in graph_dir.rglob("manifest.json")
            if (path.parent / "graph_data.sqlite").exists()
            and (path.parent / RAW_RUN_DB_NAME).exists()
        ]
        if len(matches) == 1:
            graph_dir = matches[0]
        elif len(matches) > 1:
            examples = ", ".join(str(path) for path in matches[:3])
            return (
                None,
                "External path contains multiple graph folders. "
                f"Please specify one graph folder directly. Examples: {examples}",
            )

    required_files = ["manifest.json", "graph_data.sqlite", RAW_RUN_DB_NAME]
    missing = [name for name in required_files if not (graph_dir / name).exists()]
    if missing:
        return (
            None,
            "External graph folder must contain "
            f"{', '.join(required_files)}. Missing: {', '.join(missing)}",
        )
    try:
        job = load_graph_job(graph_dir)
    except Exception as exc:
        return None, f"Could not load external graph folder: {exc}"
    if job.status != "completed":
        return None, f"External graph folder status must be completed. Current status: {job.status}"
    return job, ""


def int_or_default(value: object, default: int) -> int:
    if value is None:
        return int(default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


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
        st.error(f"LoRa airtimeを計算できませんでした。 / LoRa airtime could not be calculated: {exc}")
        return

    cols = st.columns(5)
    cols[0].metric("LoRa airtime", f"{airtime_ms:.3f} ms")
    cols[1].metric("symbol time", f"{symbol_duration_ms(lora_config):.3f} ms")
    cols[2].metric("LDRO", "on" if low_data_rate_optimize else "off")
    cols[3].metric("K点数 / K points", len(k_values))
    cols[4].metric("総run数 / Total runs", total_runs)
    size_estimate = estimate_job_data_size(
        total_runs=total_runs,
        simulation_duration_ms=simulation_duration_ms,
        cycle_time_ms=cycle_time_ms,
        device_count=device_count,
    )
    size_cols = st.columns(4)
    size_cols[0].metric("予想データサイズ / Estimated data size", format_bytes(size_estimate["total_bytes"]))
    size_cols[1].metric("runごとの予想 / Estimated per run", format_bytes(size_estimate["bytes_per_run"]))
    size_cols[2].metric("推定元 / Estimate source", str(size_estimate["source"]))
    size_cols[3].metric("概算範囲 / Rough range", size_estimate["range_label"])
    st.info(
        f"{graph_type}: K={len(k_values)}点 / points, Kごとのrun数 / runs per K={runs_per_k}, "
        f"総run数 / total runs={total_runs}. シミュレーション時間 / Simulation duration={format_duration_ms(simulation_duration_ms)}, "
        f"PER区間 / interval={format_duration_ms(interval_start_ms)} to {format_duration_ms(interval_end_ms)}. "
        f"初期位相範囲 / Initial phase range={format_percent_range(initial_phase_start_percent, initial_phase_end_percent)}. "
        "LoRa airtimeをTX時間として使います。 / LoRa airtime is used as TX time."
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

