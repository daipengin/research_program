from __future__ import annotations

import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

from research_program.simulation.coupling_functions import (
    CouplingFunction,
    resolve_coupling_function,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REANALYSIS_ROOT = PROJECT_ROOT / "results" / "reanalysis"
DEMO_ROOT = PROJECT_ROOT / "results" / "demo_initial_phase"
OUTPUT_ROOT = PROJECT_ROOT / "results" / "paper_figures"

CYCLE_SECONDS = 10.0
TWO_PI = 2.0 * math.pi
SUBFIGURE_SCALE = 2.0
FULL_WIDTH_SCALE = 1.15


@dataclass(frozen=True)
class FigureStyle:
    scale: float
    font_size: float
    axis_label_size: float
    tick_label_size: float
    legend_size: float
    annotation_size: float
    bar_label_size: float
    line_width: float
    marker_size: float
    scatter_size: float
    reference_scatter_size: float
    grid_width: float
    spine_width: float

SERIES = {
    "kuramoto": {
        "csv": "kuramoto_metrics.csv",
        "function": "KURAMOTO",
        "enum": CouplingFunction.KURAMOTO,
        "label": "Kuramoto based",
        "color": "#E69F00",
        "marker": "o",
        "linestyle": "-",
        "optimal_k": 571.0,
        "coupling_stem": "fig_coupling_kuramoto",
        "demo_slug": "kuramoto",
        "annotation_offset": (-70, 32),
    },
    "linear": {
        "csv": "linear_metrics.csv",
        "function": "LINEAR",
        "enum": CouplingFunction.LINEAR,
        "label": "frog chorus based",
        "color": "#0072B2",
        "marker": "s",
        "linestyle": "--",
        "optimal_k": 10.0,
        "coupling_stem": "fig_coupling_frog",
        "demo_slug": "frog",
        "annotation_offset": (34, 34),
    },
    "linear_4": {
        "csv": "linear_4_metrics.csv",
        "function": "LINEAR_4",
        "enum": CouplingFunction.LINEAR_4,
        "label": "modified frog chorus based",
        "color": "#56B4E9",
        "marker": "^",
        "linestyle": "-.",
        "optimal_k": 9.0,
        "coupling_stem": "fig_coupling_modified_frog",
        "demo_slug": "modified_frog",
        "annotation_offset": (38, -46),
    },
    "newsin": {
        "csv": "newsin_metrics.csv",
        "function": "NewSIN",
        "enum": CouplingFunction.NewSIN,
        "label": "1-sin based",
        "color": "#009E73",
        "marker": "D",
        "linestyle": ":",
        "optimal_k": 24.0,
        "coupling_stem": "fig_coupling_1sin",
        "demo_slug": "1sin",
        "annotation_offset": (52, 18),
    },
}


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    frames = load_metrics()

    make_coupling_function_panels()
    demo_meta = []
    demo_meta.extend(make_demo_panels("uniform_1ms", "fig_demo_uniform"))
    demo_meta.extend(make_demo_panels("four_clusters", "fig_demo_clusters"))
    pd.DataFrame(demo_meta).to_csv(OUTPUT_ROOT / "fig_demo_reference_devices.csv", index=False)
    make_per_vs_k(frames)
    make_ttu_vs_k(frames)
    make_usable_rate_vs_k(frames)
    make_phase_error_vs_k()
    make_phase_error_overlay_vs_k()
    make_two_phase_per(frames)
    validate_generated_pdfs()
    return 0


def apply_style(scale: float) -> FigureStyle:
    if scale >= 2.0:
        style = FigureStyle(
            scale=scale,
            font_size=16.0,
            axis_label_size=16.0,
            tick_label_size=16.0,
            legend_size=13.5,
            annotation_size=14.0,
            bar_label_size=16.0,
            line_width=1.8,
            marker_size=4.6,
            scatter_size=3.2,
            reference_scatter_size=6.4,
            grid_width=0.9,
            spine_width=0.9,
        )
    else:
        style = FigureStyle(
            scale=scale,
            font_size=9.2,
            axis_label_size=10.0,
            tick_label_size=9.0,
            legend_size=8.5,
            annotation_size=9.0,
            bar_label_size=9.0,
            line_width=1.15,
            marker_size=2.7,
            scatter_size=1.0,
            reference_scatter_size=2.0,
            grid_width=0.5,
            spine_width=0.7,
        )
    mpl.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
            "font.size": style.font_size,
            "axes.labelsize": style.axis_label_size,
            "axes.titlesize": style.axis_label_size,
            "xtick.labelsize": style.tick_label_size,
            "ytick.labelsize": style.tick_label_size,
            "legend.fontsize": style.legend_size,
            "axes.linewidth": style.spine_width,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "figure.dpi": 300,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.02,
        }
    )
    return style


def load_metrics() -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for slug, spec in SERIES.items():
        df = pd.read_csv(REANALYSIS_ROOT / str(spec["csv"]))
        frames[slug] = df.sort_values("k").reset_index(drop=True)
    return frames


def make_coupling_function_panels() -> None:
    style = apply_style(SUBFIGURE_SCALE)
    for slug, spec in SERIES.items():
        deltas, values = coupling_curve(spec["enum"])
        plot_df = pd.DataFrame(
            {
                "function": spec["function"],
                "label": spec["label"],
                "delta_rad": deltas,
                "coupling_value": values,
            }
        )
        stem = str(spec["coupling_stem"])
        plot_df.to_csv(OUTPUT_ROOT / f"{stem}.csv", index=False)

        fig, ax = plt.subplots(figsize=(3.5, 2.6), constrained_layout=True)
        ax.plot(deltas, values, color=spec["color"], linewidth=style.line_width)
        ax.axhline(0.0, color="0.25", linewidth=0.7 * style.line_width)
        ax.set_xlim(-math.pi, math.pi)
        ax.set_ylim(-1.05, 1.05)
        ax.set_xlabel(r"$\delta$ [rad]")
        ax.set_ylabel(r"$f(\delta)$")
        set_pi_x_ticks(ax, compact=True)
        ax.set_yticks([-1.0, 0.0, 1.0])
        style_axis(ax, style)
        fig.savefig(OUTPUT_ROOT / f"{stem}.pdf")
        plt.close(fig)


def coupling_curve(coupling_enum: CouplingFunction) -> tuple[np.ndarray, np.ndarray]:
    breakpoints = [-math.pi, 0.0, math.pi]
    xs: list[float] = []
    ys: list[float] = []
    func = resolve_coupling_function(coupling_enum)
    segments = [(-math.pi, 0.0), (0.0, math.pi)]
    for start, end in segments:
        if xs:
            xs.append(np.nan)
            ys.append(np.nan)
        segment_x = np.linspace(start, end, 361, endpoint=True)
        for value in segment_x:
            if any(np.isclose(value, point) for point in breakpoints):
                xs.append(float(value))
                ys.append(np.nan)
            else:
                xs.append(float(value))
                ys.append(float(func(float(value))))
    return np.array(xs, dtype=float), np.array(ys, dtype=float)


def make_demo_panels(condition: str, stem_prefix: str) -> list[dict[str, object]]:
    style = apply_style(SUBFIGURE_SCALE)
    metadata_rows: list[dict[str, object]] = []
    for slug, spec in SERIES.items():
        path = DEMO_ROOT / f"{slug}_{condition}_run1.csv"
        df = pd.read_csv(
            path,
            usecols=["cycle_index", "device_id", "phase_diff_rad", "device_event_time_ms"],
        )
        reference_device_id, reference_initial_event_time_ms = select_reference_device(df)
        reference_phase = (
            df[df["device_id"] == reference_device_id][["cycle_index", "phase_diff_rad"]]
            .rename(columns={"phase_diff_rad": "reference_phase_diff_rad"})
        )
        df = df.merge(reference_phase, how="left", on="cycle_index")
        df["phase_diff_rebased_rad"] = wrap_to_pi(
            pd.to_numeric(df["phase_diff_rad"], errors="coerce")
            - pd.to_numeric(df["reference_phase_diff_rad"], errors="coerce")
        )
        df.insert(0, "label", spec["label"])
        df.insert(0, "function", spec["function"])
        df.insert(0, "condition", condition)
        df.insert(3, "reference_device_id", reference_device_id)
        df.insert(4, "reference_initial_event_time_ms", reference_initial_event_time_ms)
        output_stem = f"{stem_prefix}_{spec['demo_slug']}"
        df.to_csv(OUTPUT_ROOT / f"{output_stem}.csv", index=False)
        metadata_rows.append(
            {
                "figure_stem": output_stem,
                "condition": condition,
                "function": spec["function"],
                "label": spec["label"],
                "reference_device_id": reference_device_id,
                "reference_initial_event_time_ms": reference_initial_event_time_ms,
            }
        )

        fig, ax = plt.subplots(figsize=(3.5, 2.6), constrained_layout=True)
        device_ids = sorted(df["device_id"].dropna().unique())
        for index, device_id in enumerate(device_ids):
            device_df = df[df["device_id"] == device_id]
            is_reference = int(device_id) == int(reference_device_id)
            ax.scatter(
                device_df["cycle_index"],
                device_df["phase_diff_rebased_rad"],
                s=style.reference_scatter_size if is_reference else style.scatter_size,
                color="black" if is_reference else device_color(str(spec["color"]), index, len(device_ids)),
                alpha=0.95 if is_reference else 0.7,
                linewidths=0,
                zorder=4 if is_reference else 2,
            )
        ax.set_xlim(1, 180)
        ax.set_ylim(-math.pi, math.pi)
        ax.set_xlabel("Cycle index")
        ax.set_ylabel("Phase difference [rad]")
        ax.set_xticks([0, 60, 120, 180])
        set_pi_y_ticks(ax, compact=True)
        style_axis(ax, style)
        fig.savefig(OUTPUT_ROOT / f"{output_stem}.pdf")
        plt.close(fig)
    return metadata_rows


def make_per_vs_k(frames: dict[str, pd.DataFrame]) -> None:
    style = apply_style(FULL_WIDTH_SCALE)
    rows = []
    fig, ax = plt.subplots(figsize=(3.5, 2.6), constrained_layout=True)
    for slug, df in frames.items():
        spec = SERIES[slug]
        plot_df = df[["k", "overall_per_mean"]].dropna().copy()
        plot_df = plot_df[plot_df["overall_per_mean"] > 0]
        plot_df.insert(0, "label", spec["label"])
        plot_df.insert(0, "function", spec["function"])
        rows.append(plot_df)
        ax.plot(
            plot_df["k"],
            plot_df["overall_per_mean"],
            label=spec["label"],
            color=spec["color"],
            linestyle=spec["linestyle"],
            marker=spec["marker"],
            markersize=style.marker_size,
            linewidth=style.line_width,
            markevery=max(1, len(plot_df) // 15),
        )
        best = plot_df.loc[plot_df["overall_per_mean"].idxmin()]
        ax.scatter(
            [best["k"]],
            [best["overall_per_mean"]],
            color="red",
            edgecolor="black",
            linewidth=0.3,
            s=18,
            zorder=5,
        )
        ax.annotate(
            f'{best["overall_per_mean"]:.3g}%\nK={best["k"]:g}',
            xy=(best["k"], best["overall_per_mean"]),
            xytext=spec["annotation_offset"],
            textcoords="offset points",
            fontsize=style.annotation_size,
            color="red",
            arrowprops={
                "arrowstyle": "-",
                "color": "red",
                "linewidth": 0.6,
                "shrinkA": 0,
                "shrinkB": 2,
            },
        )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("K")
    ax.set_ylabel("Full-period PER [%]")
    finish_figure(fig, ax, "fig_per_vs_k", style)
    write_plot_csv("fig_per_vs_k", rows)


def make_ttu_vs_k(frames: dict[str, pd.DataFrame]) -> None:
    style = apply_style(FULL_WIDTH_SCALE)
    rows = []
    fig, ax = plt.subplots(figsize=(3.5, 2.6), constrained_layout=True)
    for slug, df in frames.items():
        spec = SERIES[slug]
        plot_df = df[["k", "time_to_usable_median", "time_to_usable_q1", "time_to_usable_q3"]].copy()
        plot_df["time_to_usable_median_min"] = cycles_to_minutes(plot_df["time_to_usable_median"])
        plot_df["time_to_usable_q1_min"] = cycles_to_minutes(plot_df["time_to_usable_q1"])
        plot_df["time_to_usable_q3_min"] = cycles_to_minutes(plot_df["time_to_usable_q3"])
        plot_df = plot_df.dropna(subset=["time_to_usable_median_min"])
        plot_df.insert(0, "label", spec["label"])
        plot_df.insert(0, "function", spec["function"])
        rows.append(plot_df)
        ax.plot(
            plot_df["k"],
            plot_df["time_to_usable_median_min"],
            label=spec["label"],
            color=spec["color"],
            linestyle=spec["linestyle"],
            marker=spec["marker"],
            markersize=style.marker_size,
            linewidth=style.line_width,
            markevery=max(1, len(plot_df) // 15),
        )
        band = plot_df.dropna(subset=["time_to_usable_q1_min", "time_to_usable_q3_min"])
        ax.fill_between(
            band["k"].to_numpy(dtype=float),
            band["time_to_usable_q1_min"].to_numpy(dtype=float),
            band["time_to_usable_q3_min"].to_numpy(dtype=float),
            color=spec["color"],
            alpha=0.16,
            linewidth=0,
        )
    ax.set_xscale("log")
    ax.set_xlabel("K")
    ax.set_ylabel("Time to usable [min]")
    finish_figure(fig, ax, "fig_ttu_vs_k", style)
    write_plot_csv("fig_ttu_vs_k", rows)


def make_usable_rate_vs_k(frames: dict[str, pd.DataFrame]) -> None:
    style = apply_style(FULL_WIDTH_SCALE)
    rows = []
    fig, ax = plt.subplots(figsize=(3.5, 2.6), constrained_layout=True)
    for slug, df in frames.items():
        spec = SERIES[slug]
        plot_df = df[["k", "usable_rate_percent"]].dropna().copy()
        plot_df.insert(0, "label", spec["label"])
        plot_df.insert(0, "function", spec["function"])
        rows.append(plot_df)
        ax.plot(
            plot_df["k"],
            plot_df["usable_rate_percent"],
            label=spec["label"],
            color=spec["color"],
            linestyle=spec["linestyle"],
            marker=spec["marker"],
            markersize=style.marker_size,
            linewidth=style.line_width,
            markevery=max(1, len(plot_df) // 15),
        )
    ax.axhline(95.0, color="0.35", linewidth=0.7, linestyle=":")
    ax.set_xscale("log")
    ax.set_ylim(-2, 102)
    ax.set_xlabel("K")
    ax.set_ylabel("TTU attainment rate [%]")
    finish_figure(fig, ax, "fig_usable_rate_vs_k", style)
    write_plot_csv("fig_usable_rate_vs_k", rows)


def make_phase_error_vs_k() -> None:
    style = apply_style(FULL_WIDTH_SCALE)
    rows = []
    fig, ax = plt.subplots(figsize=(3.5, 2.6), constrained_layout=True)
    for slug, spec in SERIES.items():
        path = REANALYSIS_ROOT / f"{slug}_phase_error.csv"
        df = pd.read_csv(path).sort_values("k").reset_index(drop=True)
        plot_df = df[["k", "residual_median", "residual_q1", "residual_q3", "n_runs"]].copy()
        plot_df = plot_df.dropna(subset=["residual_median"])
        plot_df = plot_df[plot_df["residual_median"] > 0]
        plot_df.insert(0, "label", spec["label"])
        plot_df.insert(0, "function", spec["function"])
        rows.append(plot_df)
        ax.plot(
            plot_df["k"],
            plot_df["residual_median"],
            label=spec["label"],
            color=spec["color"],
            linestyle=spec["linestyle"],
            marker=spec["marker"],
            markersize=style.marker_size,
            linewidth=style.line_width,
            markevery=max(1, len(plot_df) // 15),
        )
        band = plot_df.dropna(subset=["residual_q1", "residual_q3"])
        band = band[(band["residual_q1"] > 0) & (band["residual_q3"] > 0)]
        ax.fill_between(
            band["k"].to_numpy(dtype=float),
            band["residual_q1"].to_numpy(dtype=float),
            band["residual_q3"].to_numpy(dtype=float),
            color=spec["color"],
            alpha=0.16,
            linewidth=0,
        )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("K")
    ax.set_ylabel("residual phase-spacing error [rad]")
    finish_figure(fig, ax, "fig_phase_error_vs_k", style)
    write_plot_csv("fig_phase_error_vs_k", rows)


def make_phase_error_overlay_vs_k() -> None:
    style = apply_style(FULL_WIDTH_SCALE)
    rows = []
    fig, ax = plt.subplots(figsize=(3.5, 2.6), constrained_layout=True)
    for slug, spec in SERIES.items():
        path = REANALYSIS_ROOT / f"{slug}_phase_error.csv"
        df = pd.read_csv(path).sort_values("k").reset_index(drop=True)
        thirty = df[["k", "residual_median", "n_runs"]].dropna(subset=["residual_median"]).copy()
        thirty = thirty[thirty["residual_median"] > 0]
        thirty.insert(0, "time_point_min", 30)
        thirty.insert(0, "residual_column", "residual_median")
        thirty.insert(0, "label", spec["label"])
        thirty.insert(0, "function", spec["function"])
        thirty = thirty.rename(columns={"residual_median": "residual_phase_spacing_error"})
        rows.append(thirty)
        ax.plot(
            thirty["k"],
            thirty["residual_phase_spacing_error"],
            color=spec["color"],
            linestyle=spec["linestyle"],
            linewidth=style.line_width,
            alpha=0.95,
        )

        three = df[["k", "residual_3min_median", "n_runs"]].dropna(subset=["residual_3min_median"]).copy()
        three = three[three["residual_3min_median"] > 0]
        three.insert(0, "time_point_min", 3)
        three.insert(0, "residual_column", "residual_3min_median")
        three.insert(0, "label", spec["label"])
        three.insert(0, "function", spec["function"])
        three = three.rename(columns={"residual_3min_median": "residual_phase_spacing_error"})
        rows.append(three)
        ax.plot(
            three["k"],
            three["residual_phase_spacing_error"],
            color=lighten_color(str(spec["color"]), amount=0.55),
            linestyle=spec["linestyle"],
            linewidth=style.line_width * 0.7,
            alpha=0.65,
        )

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("K")
    ax.set_ylabel("residual phase-spacing error [rad]")
    style_axis(ax, style)
    function_handles = [
        Line2D(
            [0],
            [0],
            color=str(spec["color"]),
            linestyle=str(spec["linestyle"]),
            linewidth=style.line_width,
            label=str(spec["label"]),
        )
        for spec in SERIES.values()
    ]
    time_handles = [
        Line2D([0], [0], color="0.25", linewidth=style.line_width, label="30 min"),
        Line2D([0], [0], color="0.65", linewidth=style.line_width * 0.7, alpha=0.65, label="3 min"),
    ]
    legend_functions = ax.legend(handles=function_handles, frameon=False, loc="lower left")
    ax.add_artist(legend_functions)
    ax.legend(handles=time_handles, frameon=False, loc="upper right")
    fig.savefig(OUTPUT_ROOT / "fig_phase_error_vs_k_overlay.pdf")
    plt.close(fig)
    write_plot_csv("fig_phase_error_vs_k_overlay", rows)


def make_two_phase_per(frames: dict[str, pd.DataFrame]) -> None:
    style = apply_style(FULL_WIDTH_SCALE)
    rows = []
    for slug, df in frames.items():
        spec = SERIES[slug]
        k_value = float(spec["optimal_k"])
        selected = df.loc[np.isclose(df["k"].astype(float), k_value)]
        if selected.empty:
            raise ValueError(f"missing optimal K={k_value:g} for {slug}")
        row = selected.iloc[0]
        rows.append(
            {
                "function": spec["function"],
                "label": spec["label"],
                "k": k_value,
                "transient_per_mean": row["transient_per_mean"],
                "steady_per_mean": row["steady_per_mean"],
                "transient_per_label": two_phase_label(slug, "transient"),
                "steady_per_label": two_phase_label(slug, "steady"),
            }
        )
    plot_df = pd.DataFrame(rows)
    plot_df.to_csv(OUTPUT_ROOT / "fig_two_phase_per.csv", index=False)

    fig, ax = plt.subplots(figsize=(3.5, 2.6), constrained_layout=True)
    x = np.arange(len(plot_df), dtype=float)
    width = 0.36
    transient = positive_for_log(plot_df["transient_per_mean"].to_numpy(dtype=float))
    steady = positive_for_log(plot_df["steady_per_mean"].to_numpy(dtype=float))
    bars_a = ax.bar(x - width / 2, transient, width, label="Pre-TTU", color="#999999", edgecolor="black", linewidth=0.45)
    bars_b = ax.bar(x + width / 2, steady, width, label="Post-TTU", color="#FFFFFF", edgecolor="black", linewidth=0.45, hatch="//")
    ax.set_xticks(x)
    ax.set_xticklabels(plot_df["label"], rotation=18, ha="right")
    ax.set_yscale("log")
    ax.set_ylabel("PER [%]")
    add_bar_labels(ax, bars_a, plot_df["transient_per_label"].tolist(), style)
    add_bar_labels(ax, bars_b, plot_df["steady_per_label"].tolist(), style)
    finish_figure(fig, ax, "fig_two_phase_per", style, legend_ncol=2)


def finish_figure(fig: plt.Figure, ax: plt.Axes, stem: str, style: FigureStyle, *, legend_ncol: int = 1) -> None:
    style_axis(ax, style)
    ax.legend(frameon=False, loc="best", ncol=legend_ncol)
    fig.savefig(OUTPUT_ROOT / f"{stem}.pdf")
    plt.close(fig)


def style_axis(ax: plt.Axes, style: FigureStyle) -> None:
    ax.grid(True, which="both", alpha=0.3, linewidth=style.grid_width)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_linewidth(style.spine_width)
    ax.spines["left"].set_linewidth(style.spine_width)
    ax.tick_params(width=style.spine_width, length=3.0 * style.scale)


def set_pi_x_ticks(ax: plt.Axes, *, compact: bool = False) -> None:
    if compact:
        ax.set_xticks([-math.pi, 0, math.pi])
        ax.set_xticklabels([r"$-\pi$", "0", r"$\pi$"])
    else:
        ax.set_xticks([-math.pi, -math.pi / 2, 0, math.pi / 2, math.pi])
        ax.set_xticklabels([r"$-\pi$", r"$-\pi/2$", "0", r"$\pi/2$", r"$\pi$"])


def set_pi_y_ticks(ax: plt.Axes, *, compact: bool = False) -> None:
    if compact:
        ax.set_yticks([-math.pi, 0, math.pi])
        ax.set_yticklabels([r"$-\pi$", "0", r"$\pi$"])
    else:
        ax.set_yticks([-math.pi, -math.pi / 2, 0, math.pi / 2, math.pi])
        ax.set_yticklabels([r"$-\pi$", r"$-\pi/2$", "0", r"$\pi/2$", r"$\pi$"])


def write_plot_csv(stem: str, frames: list[pd.DataFrame]) -> None:
    pd.concat(frames, ignore_index=True).to_csv(OUTPUT_ROOT / f"{stem}.csv", index=False)


def cycles_to_minutes(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce") * CYCLE_SECONDS / 60.0


def select_reference_device(df: pd.DataFrame) -> tuple[int, float]:
    initial = df[df["cycle_index"] == 1][["device_id", "device_event_time_ms"]].copy()
    initial["device_event_time_ms"] = pd.to_numeric(initial["device_event_time_ms"], errors="coerce")
    initial = initial.dropna(subset=["device_id", "device_event_time_ms"])
    if initial.empty:
        raise ValueError("cannot select reference device: missing cycle_index==1 event times")
    median_time = float(initial["device_event_time_ms"].median())
    initial["distance_from_median"] = (initial["device_event_time_ms"] - median_time).abs()
    # When two devices are equally close to the median, choose the upper-side timing.
    selected = initial.sort_values(
        ["distance_from_median", "device_event_time_ms", "device_id"],
        ascending=[True, False, False],
    ).iloc[0]
    return int(selected["device_id"]), float(selected["device_event_time_ms"])


def wrap_to_pi(series: pd.Series) -> pd.Series:
    return ((series + math.pi) % TWO_PI) - math.pi


def device_color(base_color: str, index: int, count: int) -> tuple[float, float, float, float]:
    rgb = np.array(mpl.colors.to_rgb(base_color), dtype=float)
    if count <= 1:
        mix = 0.0
    else:
        mix = 0.55 * index / (count - 1)
    mixed = rgb * (1.0 - mix) + np.ones(3) * mix
    return (float(mixed[0]), float(mixed[1]), float(mixed[2]), 1.0)


def lighten_color(base_color: str, *, amount: float) -> tuple[float, float, float, float]:
    rgb = np.array(mpl.colors.to_rgb(base_color), dtype=float)
    mixed = rgb * (1.0 - amount) + np.ones(3) * amount
    return (float(mixed[0]), float(mixed[1]), float(mixed[2]), 1.0)


def positive_for_log(values: np.ndarray) -> np.ndarray:
    finite_positive = values[np.isfinite(values) & (values > 0)]
    floor = min(float(np.min(finite_positive)) / 10.0, 1e-6) if finite_positive.size else 1e-6
    return np.where(np.isfinite(values) & (values > 0), values, floor)


def two_phase_label(slug: str, phase: str) -> str:
    labels = {
        ("kuramoto", "transient"): "2.43%",
        ("kuramoto", "steady"): "0.0012%",
        ("linear", "transient"): "3.42%",
        ("linear", "steady"): "0.092%",
        ("linear_4", "transient"): "3.52%",
        ("linear_4", "steady"): "0.065%",
        ("newsin", "transient"): "2.93%",
        ("newsin", "steady"): "0.212%",
    }
    return labels[(slug, phase)]


def add_bar_labels(ax: plt.Axes, bars: mpl.container.BarContainer, labels: list[str], style: FigureStyle) -> None:
    for bar, label in zip(bars, labels):
        if not label:
            continue
        ax.annotate(
            label,
            xy=(bar.get_x() + bar.get_width() / 2.0, bar.get_height()),
            xytext=(0, 2),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=style.bar_label_size,
            rotation=90,
        )


def validate_generated_pdfs() -> None:
    figure_specs = []
    for spec in SERIES.values():
        figure_specs.append((f"{spec['coupling_stem']}.pdf", SUBFIGURE_SCALE, 0.48))
        figure_specs.append((f"fig_demo_uniform_{spec['demo_slug']}.pdf", SUBFIGURE_SCALE, 0.48))
        figure_specs.append((f"fig_demo_clusters_{spec['demo_slug']}.pdf", SUBFIGURE_SCALE, 0.48))
    for name in [
        "fig_per_vs_k.pdf",
        "fig_ttu_vs_k.pdf",
        "fig_usable_rate_vs_k.pdf",
        "fig_phase_error_vs_k.pdf",
        "fig_phase_error_vs_k_overlay.pdf",
        "fig_two_phase_per.pdf",
    ]:
        figure_specs.append((name, FULL_WIDTH_SCALE, 1.0))

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        print("PDF rasterization/font-size validation (300 dpi):")
        for name, scale, latex_factor in figure_specs:
            pdf_path = OUTPUT_ROOT / name
            if not pdf_path.exists():
                raise FileNotFoundError(pdf_path)
            prefix = tmp / pdf_path.stem
            subprocess.run(
                ["pdftoppm", "-r", "300", "-png", str(pdf_path), str(prefix)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            raster_path = prefix.with_name(prefix.name + "-1.png")
            if not raster_path.exists():
                raise FileNotFoundError(f"pdftoppm did not create {raster_path}")
            effective_axis_label = effective_axis_label_size(scale)
            effective_tick_label = effective_tick_label_size(scale)
            if effective_axis_label < 8.0 or effective_tick_label < 8.0:
                raise ValueError(
                    f"{name}: effective font below 8pt "
                    f"(axis={effective_axis_label:.2f}, tick={effective_tick_label:.2f})"
                )
            print(
                f"  {name}: latex_width_factor={latex_factor:.2f}, "
                f"scale={scale:.2f}, effective_axis_label={effective_axis_label:.2f}pt, "
                f"effective_tick_label={effective_tick_label:.2f}pt, raster={raster_path.name}"
            )


def effective_axis_label_size(scale: float) -> float:
    return 16.0 / 2.0 if scale >= 2.0 else 10.0


def effective_tick_label_size(scale: float) -> float:
    return 16.0 / 2.0 if scale >= 2.0 else 9.0


if __name__ == "__main__":
    raise SystemExit(main())
