from __future__ import annotations

import math
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

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
    },
}

DEMO_ORDER = ["kuramoto", "linear", "linear_4", "newsin"]


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    configure_matplotlib()
    frames = load_metrics()

    make_coupling_functions()
    make_demo("uniform_1ms", "fig_demo_uniform")
    make_demo("four_clusters", "fig_demo_clusters")
    make_per_vs_k(frames)
    make_ttu_vs_k(frames)
    make_usable_rate_vs_k(frames)
    make_two_phase_per(frames)
    return 0


def configure_matplotlib() -> None:
    mpl.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
            "font.size": 8,
            "axes.labelsize": 8,
            "axes.titlesize": 8,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
            "legend.fontsize": 6.8,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "figure.dpi": 300,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.02,
        }
    )


def load_metrics() -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for slug, spec in SERIES.items():
        df = pd.read_csv(REANALYSIS_ROOT / str(spec["csv"]))
        frames[slug] = df.sort_values("k").reset_index(drop=True)
    return frames


def make_coupling_functions() -> None:
    deltas = np.linspace(-math.pi, math.pi, 721)
    rows = []
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    for slug, spec in SERIES.items():
        func = resolve_coupling_function(spec["enum"])
        values = np.array([func(float(delta)) for delta in deltas], dtype=float)
        plot_df = pd.DataFrame(
            {
                "function": spec["function"],
                "label": spec["label"],
                "delta_rad": deltas,
                "coupling_value": values,
            }
        )
        rows.append(plot_df)
        ax.plot(
            deltas,
            values,
            label=spec["label"],
            color=spec["color"],
            linestyle=spec["linestyle"],
            linewidth=1.1,
        )
    ax.axhline(0.0, color="0.2", linewidth=0.5)
    ax.set_xlim(-math.pi, math.pi)
    ax.set_xlabel(r"$\delta$ (rad)")
    ax.set_ylabel(r"$f(\delta)$")
    ax.set_xticks([-math.pi, -math.pi / 2, 0, math.pi / 2, math.pi])
    ax.set_xticklabels([r"$-\pi$", r"$-\pi/2$", "0", r"$\pi/2$", r"$\pi$"])
    finish_figure(fig, ax, "fig_coupling_functions", legend_ncol=1)
    write_plot_csv("fig_coupling_functions", rows)


def make_demo(condition: str, stem: str) -> None:
    rows = []
    fig, axes = plt.subplots(2, 2, figsize=(3.5, 3.2), sharex=True, sharey=True)
    for ax, slug in zip(axes.ravel(), DEMO_ORDER):
        spec = SERIES[slug]
        path = DEMO_ROOT / f"{slug}_{condition}_run1.csv"
        df = pd.read_csv(path, usecols=["cycle_index", "device_id", "phase_diff_rad"])
        df["phase_diff_mod_rad"] = np.mod(pd.to_numeric(df["phase_diff_rad"], errors="coerce"), TWO_PI)
        df.insert(0, "label", spec["label"])
        df.insert(0, "function", spec["function"])
        df.insert(0, "condition", condition)
        rows.append(df)

        for _, device_df in df.groupby("device_id", sort=True):
            ax.plot(
                device_df["cycle_index"],
                device_df["phase_diff_mod_rad"],
                color=spec["color"],
                linewidth=0.5,
                alpha=0.7,
            )
        ax.set_title(spec["label"], pad=2)
        ax.set_ylim(0, TWO_PI)
        ax.set_yticks([0, math.pi, TWO_PI])
        ax.set_yticklabels(["0", r"$\pi$", r"$2\pi$"])
        style_axis(ax)
    for ax in axes[-1, :]:
        ax.set_xlabel("Cycle")
    for ax in axes[:, 0]:
        ax.set_ylabel("Phase diff. (rad)")
    fig.tight_layout(pad=0.35, h_pad=0.55, w_pad=0.55)
    fig.savefig(OUTPUT_ROOT / f"{stem}.pdf")
    plt.close(fig)
    write_plot_csv(stem, rows)


def make_per_vs_k(frames: dict[str, pd.DataFrame]) -> None:
    rows = []
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
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
            markersize=2.3,
            linewidth=1.0,
            markevery=max(1, len(plot_df) // 15),
        )
        best = plot_df.loc[plot_df["overall_per_mean"].idxmin()]
        ax.scatter(
            [best["k"]],
            [best["overall_per_mean"]],
            color="red",
            edgecolor="black",
            linewidth=0.25,
            s=13,
            zorder=5,
        )
        ax.annotate(
            f'{best["overall_per_mean"]:.3g}%\nK={best["k"]:g}',
            xy=(best["k"], best["overall_per_mean"]),
            xytext=(3, 3),
            textcoords="offset points",
            fontsize=5.8,
            color="red",
        )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("K")
    ax.set_ylabel("Overall PER (%)")
    finish_figure(fig, ax, "fig_per_vs_k", legend_ncol=1)
    write_plot_csv("fig_per_vs_k", rows)


def make_ttu_vs_k(frames: dict[str, pd.DataFrame]) -> None:
    rows = []
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
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
            markersize=2.3,
            linewidth=1.0,
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
    ax.set_ylabel("Time to usable (min)")
    finish_figure(fig, ax, "fig_ttu_vs_k", legend_ncol=1)
    write_plot_csv("fig_ttu_vs_k", rows)


def make_usable_rate_vs_k(frames: dict[str, pd.DataFrame]) -> None:
    rows = []
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
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
            markersize=2.3,
            linewidth=1.0,
            markevery=max(1, len(plot_df) // 15),
        )
    ax.axhline(95.0, color="0.35", linewidth=0.6, linestyle=":")
    ax.set_xscale("log")
    ax.set_ylim(-2, 102)
    ax.set_xlabel("K")
    ax.set_ylabel("Usable rate (%)")
    finish_figure(fig, ax, "fig_usable_rate_vs_k", legend_ncol=1)
    write_plot_csv("fig_usable_rate_vs_k", rows)


def make_two_phase_per(frames: dict[str, pd.DataFrame]) -> None:
    rows = []
    for slug in DEMO_ORDER:
        df = frames[slug]
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
            }
        )
    plot_df = pd.DataFrame(rows)
    plot_df.to_csv(OUTPUT_ROOT / "fig_two_phase_per.csv", index=False)

    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    x = np.arange(len(plot_df), dtype=float)
    width = 0.36
    transient = positive_for_log(plot_df["transient_per_mean"].to_numpy(dtype=float))
    steady = positive_for_log(plot_df["steady_per_mean"].to_numpy(dtype=float))
    ax.bar(x - width / 2, transient, width, label="Pre-TTU", color="#999999", edgecolor="black", linewidth=0.35)
    ax.bar(x + width / 2, steady, width, label="Post-TTU", color="#FFFFFF", edgecolor="black", linewidth=0.35, hatch="//")
    ax.set_xticks(x)
    ax.set_xticklabels(plot_df["label"], rotation=18, ha="right")
    ax.set_yscale("log")
    ax.set_ylabel("PER (%)")
    finish_figure(fig, ax, "fig_two_phase_per", legend_ncol=2)


def finish_figure(fig: plt.Figure, ax: plt.Axes, stem: str, *, legend_ncol: int = 1) -> None:
    style_axis(ax)
    ax.legend(frameon=False, loc="best", ncol=legend_ncol)
    fig.tight_layout(pad=0.3)
    fig.savefig(OUTPUT_ROOT / f"{stem}.pdf")
    plt.close(fig)


def style_axis(ax: plt.Axes) -> None:
    ax.grid(True, which="both", alpha=0.3, linewidth=0.45)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def write_plot_csv(stem: str, frames: list[pd.DataFrame]) -> None:
    pd.concat(frames, ignore_index=True).to_csv(OUTPUT_ROOT / f"{stem}.csv", index=False)


def cycles_to_minutes(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce") * CYCLE_SECONDS / 60.0


def positive_for_log(values: np.ndarray) -> np.ndarray:
    finite_positive = values[np.isfinite(values) & (values > 0)]
    floor = min(float(np.min(finite_positive)) / 10.0, 1e-6) if finite_positive.size else 1e-6
    return np.where(np.isfinite(values) & (values > 0), values, floor)


if __name__ == "__main__":
    raise SystemExit(main())
