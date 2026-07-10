from __future__ import annotations

import hashlib
import json
from typing import Any

import streamlit as st


def render_plot_settings(saved_plot: dict[str, Any], key_prefix: str) -> dict[str, Any]:
    def widget_key(name: str) -> str:
        return f"{key_prefix}_{name}"

    with st.expander("描画設定 / Plot settings", expanded=False):
        axis_col, font_col, style_col = st.columns(3)
        with axis_col:
            x_auto = st.checkbox(
                "x範囲自動 / x range auto",
                value=saved_plot.get("xlim_min") is None
                and saved_plot.get("xlim_max") is None,
                key=widget_key("x_auto"),
            )
            xlim_min = st.number_input(
                "x最小 / x min",
                value=float(saved_plot.get("xlim_min") or 0.0),
                disabled=x_auto,
                key=widget_key("xlim_min"),
            )
            xlim_max = st.number_input(
                "x最大 / x max",
                value=float(saved_plot.get("xlim_max") or 20.0),
                disabled=x_auto,
                key=widget_key("xlim_max"),
            )
            y_auto = st.checkbox(
                "y範囲自動 / y range auto",
                value=saved_plot.get("ylim_min") is None
                and saved_plot.get("ylim_max") is None,
                key=widget_key("y_auto"),
            )
            ylim_min = st.number_input(
                "y最小 / y min",
                value=float(
                    saved_plot.get("ylim_min")
                    if saved_plot.get("ylim_min") is not None
                    else 0.0
                ),
                disabled=y_auto,
                key=widget_key("ylim_min"),
            )
            ylim_max = st.number_input(
                "y最大 / y max",
                value=float(
                    saved_plot.get("ylim_max")
                    if saved_plot.get("ylim_max") is not None
                    else 100.0
                ),
                disabled=y_auto,
                key=widget_key("ylim_max"),
            )
        with font_col:
            figure_width = st.number_input(
                "図の幅 / Figure width",
                min_value=1.0,
                value=float(saved_plot.get("figure_width", 8.0)),
                step=0.5,
                key=widget_key("figure_width"),
            )
            figure_height = st.number_input(
                "図の高さ / Figure height",
                min_value=1.0,
                value=float(saved_plot.get("figure_height", 5.0)),
                step=0.5,
                key=widget_key("figure_height"),
            )
            font_size_label = st.number_input(
                "軸ラベル文字サイズ / Label font size",
                min_value=1,
                value=int(saved_plot.get("font_size_label", 12)),
                step=1,
                key=widget_key("font_size_label"),
            )
            font_size_ticks = st.number_input(
                "目盛文字サイズ / Tick font size",
                min_value=1,
                value=int(saved_plot.get("font_size_ticks", 10)),
                step=1,
                key=widget_key("font_size_ticks"),
            )
            font_size_title = st.number_input(
                "タイトル文字サイズ / Title font size",
                min_value=1,
                value=int(saved_plot.get("font_size_title", 12)),
                step=1,
                key=widget_key("font_size_title"),
            )
        with style_col:
            marker_options = ["o", "s", "^", "D", "x", "+", "."]
            line_style_options = ["-", "--", "-.", ":", "None"]
            marker = st.selectbox(
                "マーカー / Marker",
                marker_options,
                index=select_index(marker_options, str(saved_plot.get("marker", "o"))),
                key=widget_key("marker"),
            )
            marker_size = st.number_input(
                "マーカーサイズ / Marker size",
                min_value=0.0,
                value=float(saved_plot.get("marker_size", 6.0)),
                step=0.5,
                key=widget_key("marker_size"),
            )
            line_style = st.selectbox(
                "線種 / Line style",
                line_style_options,
                index=select_index(
                    line_style_options, str(saved_plot.get("line_style", "-"))
                ),
                key=widget_key("line_style"),
            )
            line_width = st.number_input(
                "線幅 / Line width",
                min_value=0.0,
                value=float(saved_plot.get("line_width", 1.5)),
                step=0.25,
                key=widget_key("line_width"),
            )
            show_error_bars = st.checkbox(
                "エラーバー表示 / Show error bars",
                value=bool(saved_plot.get("show_error_bars", True)),
                key=widget_key("show_error_bars"),
            )
            error_bar_capsize = st.number_input(
                "エラーバー端サイズ / Error bar capsize",
                min_value=0.0,
                value=float(saved_plot.get("error_bar_capsize", 4.0)),
                step=0.5,
                key=widget_key("error_bar_capsize"),
            )
            show_title = st.checkbox(
                "タイトル表示 / Show title",
                value=bool(saved_plot.get("show_title", True)),
                key=widget_key("show_title"),
            )
            show_grid = st.checkbox(
                "グリッド表示 / Show grid",
                value=bool(saved_plot.get("show_grid", True)),
                key=widget_key("show_grid"),
            )
            show_min_annotation = st.checkbox(
                "最小値表示 / Show min value",
                value=bool(saved_plot.get("show_min_annotation", False)),
                key=widget_key("show_min_annotation"),
            )
            min_annotation_font_size = st.number_input(
                "最小値ラベル文字サイズ / Min label font size",
                min_value=1,
                value=int(saved_plot.get("min_annotation_font_size", 10)),
                step=1,
                disabled=not show_min_annotation,
                key=widget_key("min_annotation_font_size"),
            )
            min_annotation_x_offset = st.number_input(
                "最小値ラベルxオフセット / Min label x offset",
                value=float(saved_plot.get("min_annotation_x_offset", 10.0)),
                step=1.0,
                disabled=not show_min_annotation,
                key=widget_key("min_annotation_x_offset"),
            )
            min_annotation_y_offset = st.number_input(
                "最小値ラベルyオフセット / Min label y offset",
                value=float(saved_plot.get("min_annotation_y_offset", 10.0)),
                step=1.0,
                disabled=not show_min_annotation,
                key=widget_key("min_annotation_y_offset"),
            )
            save_dpi = st.number_input(
                "保存DPI / Save dpi",
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


def plot_settings_key_fragment(settings: dict[str, Any]) -> str:
    payload = json.dumps(settings, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def select_index(options: list[str], value: str) -> int:
    try:
        return options.index(value)
    except ValueError:
        return 0
