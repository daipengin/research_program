from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = PROJECT_ROOT / "results" / "n_sweep_v1"
FIGURES_ROOT = RESULTS_ROOT / "figures"
FUNCTIONS = ("KURAMOTO", "LINEAR")
FUNCTION_LABELS = {
    "KURAMOTO": "Kuramoto based",
    "LINEAR": "Frog chorus based",
}
DEVICE_COUNTS = (5, 10, 20, 50)
COLORS = {
    5: "#0072B2",
    10: "#009E73",
    20: "#E69F00",
    50: "#D55E00",
}
MARKERS = {5: "o", 10: "s", 20: "^", 50: "D"}


def main() -> None:
    data = pd.read_csv(RESULTS_ROOT / "condition_metrics.csv")
    metadata = json.loads((RESULTS_ROOT / "metadata.json").read_text(encoding="utf-8"))
    epsilon_by_n = {
        int(key): float(value)
        for key, value in metadata["epsilon_tolerance_rad_by_device_count"].items()
    }
    data["final_mean_over_epsilon"] = data.apply(
        lambda row: row["final_10_cycle_new_mean_abs_dev_median"]
        / epsilon_by_n[int(row["device_count"])],
        axis=1,
    )
    data["final_max_over_epsilon"] = data.apply(
        lambda row: row["final_10_cycle_new_max_abs_dev_median"]
        / epsilon_by_n[int(row["device_count"])],
        axis=1,
    )
    data["simultaneous_collisions_per_run"] = (
        data["simultaneous_collision_count_total"] / data["run_count"]
    )

    FIGURES_ROOT.mkdir(parents=True, exist_ok=True)
    plot_performance_overview(data)
    plot_convergence_details(data)
    plot_simultaneous_collisions(data)


def plot_performance_overview(data: pd.DataFrame) -> None:
    metrics = (
        ("overall_per_percent_median", "Full-period PER median (%)", (0, 100)),
        ("ttu_reach_rate_percent", "TTU reach rate (%)", (0, 105)),
        ("mean_convergence_rate_percent", "Mean-metric convergence rate (%)", (0, 105)),
        ("max_convergence_rate_percent", "Max-metric convergence rate (%)", (0, 105)),
    )
    fig, axes = plt.subplots(4, 2, figsize=(13.5, 15.5), sharex=True)
    for column_index, function_name in enumerate(FUNCTIONS):
        function_data = data[data["coupling_function"] == function_name]
        for row_index, (metric, ylabel, ylim) in enumerate(metrics):
            ax = axes[row_index, column_index]
            draw_n_lines(ax, function_data, metric)
            ax.set_ylabel(ylabel)
            ax.set_ylim(*ylim)
            if row_index == 0:
                ax.set_title(FUNCTION_LABELS[function_name])
            if row_index == len(metrics) - 1:
                ax.set_xlabel("K (strength ratio = -1e-4)")
            style_axis(ax)
    add_figure_legend(fig)
    fig.suptitle("N-sweep v1: PER, TTU, and convergence rates", y=0.995, fontsize=16)
    save_figure(fig, "performance_overview")


def plot_convergence_details(data: pd.DataFrame) -> None:
    metrics = (
        ("mean_convergence_cycle_median", "Mean convergence cycle median", (0, 180)),
        ("max_convergence_cycle_median", "Max convergence cycle median", (0, 180)),
        ("final_mean_over_epsilon", "Final-10 mean deviation / epsilon", None),
        ("final_max_over_epsilon", "Final-10 max deviation / epsilon", None),
    )
    fig, axes = plt.subplots(4, 2, figsize=(13.5, 15.5), sharex=True)
    for column_index, function_name in enumerate(FUNCTIONS):
        function_data = data[data["coupling_function"] == function_name]
        for row_index, (metric, ylabel, ylim) in enumerate(metrics):
            ax = axes[row_index, column_index]
            draw_n_lines(ax, function_data, metric)
            ax.set_ylabel(ylabel)
            if ylim is not None:
                ax.set_ylim(*ylim)
            if metric in {"final_mean_over_epsilon", "final_max_over_epsilon"}:
                ax.axhline(1.0, color="#444444", linestyle="--", linewidth=1.2)
                ax.text(
                    0.015,
                    1.02,
                    "threshold",
                    transform=ax.get_yaxis_transform(),
                    color="#444444",
                    fontsize=9,
                    va="bottom",
                )
            if row_index == 0:
                ax.set_title(FUNCTION_LABELS[function_name])
            if row_index == len(metrics) - 1:
                ax.set_xlabel("K (strength ratio = -1e-4)")
            style_axis(ax)
    add_figure_legend(fig)
    fig.suptitle("N-sweep v1: convergence cycles and residual deviations", y=0.995, fontsize=16)
    save_figure(fig, "convergence_details")


def plot_simultaneous_collisions(data: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.2), sharey=True)
    for ax, function_name in zip(axes, FUNCTIONS, strict=True):
        function_data = data[data["coupling_function"] == function_name]
        draw_n_lines(ax, function_data, "simultaneous_collisions_per_run")
        ax.set_title(FUNCTION_LABELS[function_name])
        ax.set_xlabel("K (strength ratio = -1e-4)")
        ax.set_ylabel("Simultaneous collision rows per run")
        ax.set_yscale("symlog", linthresh=0.5)
        style_axis(ax)
    add_figure_legend(fig)
    fig.suptitle("N-sweep v1: exact-start-time collisions", y=0.99, fontsize=16)
    save_figure(fig, "simultaneous_collisions")


def draw_n_lines(ax: plt.Axes, data: pd.DataFrame, metric: str) -> None:
    for device_count in DEVICE_COUNTS:
        subset = data[data["device_count"] == device_count].sort_values("k")
        ax.plot(
            subset["k"],
            subset[metric],
            color=COLORS[device_count],
            marker=MARKERS[device_count],
            linewidth=1.8,
            markersize=5.5,
            label=f"N={device_count}",
        )


def style_axis(ax: plt.Axes) -> None:
    ax.set_xscale("log")
    ax.grid(True, which="major", color="#d8d8d8", linewidth=0.8)
    ax.grid(True, which="minor", color="#eeeeee", linewidth=0.5)
    ax.spines[["top", "right"]].set_visible(False)


def add_figure_legend(fig: plt.Figure) -> None:
    handles = [
        plt.Line2D(
            [],
            [],
            color=COLORS[device_count],
            marker=MARKERS[device_count],
            linewidth=1.8,
            label=f"N={device_count}",
        )
        for device_count in DEVICE_COUNTS
    ]
    fig.legend(handles=handles, loc="upper center", ncol=4, frameon=False, bbox_to_anchor=(0.5, 0.978))


def save_figure(fig: plt.Figure, stem: str) -> None:
    fig.tight_layout(rect=(0, 0, 1, 0.955))
    fig.savefig(FIGURES_ROOT / f"{stem}.png", dpi=180, bbox_inches="tight")
    fig.savefig(FIGURES_ROOT / f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    main()
