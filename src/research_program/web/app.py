from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from research_program.config.loader import load_toml
from research_program.config.paths import resolve_project_path
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
    discover_runs,
    filter_records,
    records_to_frame,
)
from research_program.plotting.phase_gap import (
    DEFAULT_Y_COLUMN,
    build_phase_gap_error_figure,
    figure_to_bytes,
)
from research_program.simulation.legacy_runner import (
    SimulationRequest,
    request_from_config,
    run_simulation_request,
)


DEFAULT_WEB_CONFIG = Path("configs/web/default.toml")


def _load_runtime(web_config_path: str | Path = DEFAULT_WEB_CONFIG) -> tuple[dict[str, Any], RunDataContract]:
    web_config = load_toml(web_config_path)
    contract = load_data_contract(web_config["paths"]["data_format_config"])
    return web_config, contract


def _discover_records(web_config: dict[str, Any], contract: RunDataContract) -> list[RunRecord]:
    return discover_runs(web_config["paths"].get("runs_dirs", []), contract)


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
        "Coupling function",
        coupling_options,
        default=coupling_options,
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
            field_name,
            min_value=float(min_value),
            max_value=float(max_value),
            value=(float(min_value), float(max_value)),
        )
        numeric_ranges[field_name] = (float(selected_range[0]), float(selected_range[1]))

    tag_options = _all_tags(records)
    selected_tags = st.multiselect("Tags", tag_options)

    return filter_records(
        records=records,
        coupling_functions=selected_coupling_functions,
        numeric_ranges=numeric_ranges,
        required_tags=selected_tags,
    )


def _render_runs_tab(records: list[RunRecord], web_config: dict[str, Any]) -> None:
    filtered_records = _filter_controls(records, web_config)
    filtered_df = records_to_frame(filtered_records)

    st.metric("Runs", len(filtered_records))
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
            "tags",
            "status",
            "path",
        ]
        if column in filtered_df.columns
    ]
    st.dataframe(filtered_df[visible_columns], use_container_width=True, hide_index=True)

    y_column = st.selectbox(
        "Y column",
        [
            DEFAULT_Y_COLUMN,
            "mean_abs_diff_from_ideal_phase_gap",
        ],
    )
    max_runs = st.number_input("Max graph runs", min_value=1, max_value=500, value=50)
    output_format = st.selectbox(
        "Graph format",
        web_config.get("figures", {}).get("generated_graph_formats", ["png", "pdf", "svg"]),
    )

    fig, used_count = build_phase_gap_error_figure(
        filtered_records,
        y_column=y_column,
        max_runs=int(max_runs),
    )
    if fig is None:
        st.warning("phase_gap_error.csv was not found in the filtered runs.")
        return

    st.metric("Plotted runs", used_count)
    st.pyplot(fig, clear_figure=False)
    graph_bytes, mime, filename = figure_to_bytes(fig, output_format)
    st.download_button(
        "Download graph",
        data=graph_bytes,
        file_name=filename,
        mime=mime,
    )
    plt.close(fig)


def _render_simulation_tab(web_config: dict[str, Any]) -> None:
    simulation_config_path = web_config["paths"].get(
        "simulation_config",
        "configs/experiments/default_simulation.toml",
    )
    simulation_config = load_toml(simulation_config_path)
    defaults = request_from_config(simulation_config)

    with st.form("simulation"):
        coupling_function = st.selectbox(
            "Coupling function",
            ["KURAMOTO", "LINEAR", "NewSIN"],
            index=["KURAMOTO", "LINEAR", "NewSIN"].index(defaults.coupling_function)
            if defaults.coupling_function in {"KURAMOTO", "LINEAR", "NewSIN"}
            else 1,
        )
        col_left, col_right = st.columns(2)
        with col_left:
            num_runs = st.number_input("Runs", min_value=1, max_value=1000, value=defaults.num_runs)
            seed = st.number_input("Seed", min_value=0, value=defaults.seed)
            coupling_strength = st.number_input(
                "Coupling strength",
                value=defaults.coupling_strength,
            )
            strength_ratio = st.number_input(
                "Strength ratio",
                value=defaults.strength_ratio,
                format="%.8f",
            )
            max_workers = st.number_input(
                "Max workers",
                min_value=1,
                max_value=32,
                value=max(1, defaults.max_workers),
            )
        with col_right:
            cycle_time = st.number_input("Cycle time [ms]", min_value=1, value=defaults.cycle_time)
            listening_rate = st.number_input(
                "Listening rate [%]",
                min_value=1,
                max_value=99,
                value=defaults.listening_rate,
            )
            device_count = st.number_input("Devices", min_value=1, value=defaults.device_count)
            duration = st.number_input("Duration [ms]", min_value=1, value=defaults.duration)
            start_step = st.number_input("Start step [ms]", min_value=1, value=defaults.start_step)

        start_step_count = st.number_input(
            "Start step count",
            min_value=1,
            value=defaults.start_step_count,
        )
        tags = st.text_input("Tags", value=";".join(defaults.tags))
        output_root = st.text_input("Output runs dir", value=str(defaults.output_root.relative_to(PROJECT_ROOT)))

        submitted = st.form_submit_button("Run simulation")

    if not submitted:
        return

    request = SimulationRequest(
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
        legacy_simulator_dir=defaults.legacy_simulator_dir,
        max_workers=int(max_workers),
    )

    with st.spinner("Running simulation"):
        try:
            results = run_simulation_request(request)
        except Exception as exc:
            st.error(str(exc))
            return

    st.success(f"Finished {len(results)} run(s).")
    st.dataframe(pd.DataFrame(results), use_container_width=True, hide_index=True)


def _render_figures_tab(web_config: dict[str, Any]) -> None:
    figure_config = web_config.get("figures", {})
    assets = discover_figures(
        web_config["paths"].get("figure_dirs", []),
        extensions=figure_config.get("extensions", []),
    )
    assets_df = figures_to_frame(assets)

    st.metric("Figures", len(assets))
    if assets_df.empty:
        st.dataframe(pd.DataFrame())
        return

    extension_options = sorted(assets_df["extension"].unique())
    selected_extensions = st.multiselect(
        "Extensions",
        extension_options,
        default=extension_options,
    )
    filtered_assets = [
        asset
        for asset in assets
        if asset.extension in set(selected_extensions)
    ]
    filtered_df = figures_to_frame(filtered_assets)
    st.dataframe(filtered_df, use_container_width=True, hide_index=True)

    if not filtered_assets:
        return

    selected_asset = st.selectbox(
        "Figure",
        filtered_assets,
        format_func=lambda asset: f"{asset.relative_path} ({asset.extension})",
    )

    if selected_asset.is_raster or selected_asset.extension == ".svg":
        st.image(str(selected_asset.path), use_container_width=True)
    else:
        st.write(selected_asset.name)

    download_options = ["original"]
    if selected_asset.is_raster:
        download_options.extend(figure_config.get("raster_download_formats", ["png", "jpeg", "webp"]))
    selected_format = st.selectbox("Download format", download_options)

    if selected_format == "original":
        data = read_original_bytes(selected_asset.path)
        mime = original_mime_type(selected_asset.path)
        filename = selected_asset.name
    else:
        data, mime, filename = convert_raster_image(selected_asset.path, selected_format)

    st.download_button(
        "Download figure",
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
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(
        page_title="Research Program",
        layout="wide",
    )
    st.title("Research Program")

    web_config, contract = _load_runtime()
    records = _discover_records(web_config, contract)

    runs_tab, simulation_tab, figures_tab, contract_tab = st.tabs(
        ["Runs", "Simulation", "Figures", "Data format"]
    )
    with runs_tab:
        _render_runs_tab(records, web_config)
    with simulation_tab:
        _render_simulation_tab(web_config)
    with figures_tab:
        _render_figures_tab(web_config)
    with contract_tab:
        _render_contract_tab(contract)


if __name__ == "__main__":
    main()
