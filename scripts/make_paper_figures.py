from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_ROOT = PROJECT_ROOT / "results" / "reanalysis"
OUTPUT_ROOT = PROJECT_ROOT / "results" / "paper_figures"

CYCLE_SECONDS = 10.0

SERIES = {
    "kuramoto": {
        "csv": "kuramoto_metrics.csv",
        "function": "KURAMOTO",
        "label": "Kuramoto",
        "color": "#D55E00",
        "marker": "o",
        "linestyle": "-",
        "optimal_k": 571.0,
    },
    "linear": {
        "csv": "linear_metrics.csv",
        "function": "LINEAR",
        "label": "Frog chorus",
        "color": "#0072B2",
        "marker": "s",
        "linestyle": "--",
        "optimal_k": 10.0,
    },
    "linear_4": {
        "csv": "linear_4_metrics.csv",
        "function": "LINEAR_4",
        "label": "Frog chorus (x4)",
        "color": "#009E73",
        "marker": "^",
        "linestyle": "-.",
        "optimal_k": 9.0,
    },
    "newsin": {
        "csv": "newsin_metrics.csv",
        "function": "NewSIN",
        "label": "NewSIN",
        "color": "#CC79A7",
        "marker": "D",
        "linestyle": ":",
        "optimal_k": 24.0,
    },
}


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    configure_matplotlib()
    frames = load_metrics()

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
            "legend.fontsize": 7,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "figure.figsize": (3.5, 2.6),
            "figure.dpi": 300,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.02,
        }
    )


def load_metrics() -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for slug, spec in SERIES.items():
        path = INPUT_ROOT / str(spec["csv"])
        df = pd.read_csv(path)
        df = df.sort_values("k").reset_index(drop=True)
        frames[slug] = df
    return frames


def make_per_vs_k(frames: dict[str, pd.DataFrame]) -> None:
    rows = []
    fig, ax = new_axis()
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
            markersize=2.4,
            linewidth=1.0,
            markevery=max(1, len(plot_df) // 15),
        )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("K")
    ax.set_ylabel("Overall PER (%)")
    finish_figure(fig, ax, "fig_per_vs_k")
    write_plot_csv("fig_per_vs_k", rows)


def make_ttu_vs_k(frames: dict[str, pd.DataFrame]) -> None:
    rows = []
    fig, ax = new_axis()
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
            markersize=2.4,
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
    finish_figure(fig, ax, "fig_ttu_vs_k")
    write_plot_csv("fig_ttu_vs_k", rows)


def make_usable_rate_vs_k(frames: dict[str, pd.DataFrame]) -> None:
    rows = []
    fig, ax = new_axis()
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
            markersize=2.4,
            linewidth=1.0,
            markevery=max(1, len(plot_df) // 15),
        )
    ax.set_xscale("log")
    ax.set_ylim(-2, 102)
    ax.set_xlabel("K")
    ax.set_ylabel("Usable rate (%)")
    finish_figure(fig, ax, "fig_usable_rate_vs_k")
    write_plot_csv("fig_usable_rate_vs_k", rows)


def make_two_phase_per(frames: dict[str, pd.DataFrame]) -> None:
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
            }
        )
    plot_df = pd.DataFrame(rows)
    plot_df.to_csv(OUTPUT_ROOT / "fig_two_phase_per.csv", index=False)

    fig, ax = new_axis()
    x = np.arange(len(plot_df), dtype=float)
    width = 0.36
    transient = positive_for_log(plot_df["transient_per_mean"].to_numpy(dtype=float))
    steady = positive_for_log(plot_df["steady_per_mean"].to_numpy(dtype=float))
    ax.bar(x - width / 2, transient, width, label="Transient", color="#999999", edgecolor="black", linewidth=0.35)
    ax.bar(x + width / 2, steady, width, label="Steady", color="#E69F00", edgecolor="black", linewidth=0.35, hatch="//")
    ax.set_xticks(x)
    ax.set_xticklabels(plot_df["label"], rotation=18, ha="right")
    ax.set_yscale("log")
    ax.set_ylabel("PER (%)")
    finish_figure(fig, ax, "fig_two_phase_per")


def new_axis() -> tuple[plt.Figure, plt.Axes]:
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    return fig, ax


def finish_figure(fig: plt.Figure, ax: plt.Axes, stem: str) -> None:
    ax.grid(True, which="both", alpha=0.3, linewidth=0.45)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(frameon=False, loc="best")
    fig.tight_layout(pad=0.3)
    fig.savefig(OUTPUT_ROOT / f"{stem}.pdf")
    plt.close(fig)


def write_plot_csv(stem: str, frames: list[pd.DataFrame]) -> None:
    pd.concat(frames, ignore_index=True).to_csv(OUTPUT_ROOT / f"{stem}.csv", index=False)


def cycles_to_minutes(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce") * CYCLE_SECONDS / 60.0


def positive_for_log(values: np.ndarray) -> np.ndarray:
    finite_positive = values[np.isfinite(values) & (values > 0)]
    if finite_positive.size == 0:
        floor = 1e-6
    else:
        floor = min(float(np.min(finite_positive)) / 10.0, 1e-6)
    return np.where(np.isfinite(values) & (values > 0), values, floor)


if __name__ == "__main__":
    raise SystemExit(main())
