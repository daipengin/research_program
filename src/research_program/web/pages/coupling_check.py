from __future__ import annotations

import importlib
import json
import math
from io import BytesIO
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st

from research_program.simulation import coupling_functions


COUPLING_FUNCTION_VIEW_SETTINGS_PATH = (
    Path("outputs") / "settings" / "coupling_function_view_settings.json"
)

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
    fig, ax = plt.subplots(figsize=(float(figure_width), float(figure_height)), constrained_layout=True)
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
