from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = PROJECT_ROOT / "results" / "n_sweep_v2"
FIGURES_ROOT = RESULTS_ROOT / "figures"
FUNCTIONS = ("KURAMOTO", "LINEAR")
FUNCTION_LABELS = {"KURAMOTO": "Kuramoto based", "LINEAR": "Frog chorus based"}
DEVICE_COUNTS = (5, 10, 20, 50)
COLORS = {5: "#0072B2", 10: "#009E73", 20: "#E69F00", 50: "#D55E00"}
MARKERS = {5: "o", 10: "s", 20: "^", 50: "D"}


def main() -> None:
    conditions = pd.read_csv(RESULTS_ROOT / "condition_metrics.csv")
    runs = pd.read_csv(RESULTS_ROOT / "run_metrics.csv")
    metadata = json.loads((RESULTS_ROOT / "metadata.json").read_text(encoding="utf-8"))
    epsilon = {
        int(key): float(value)
        for key, value in metadata["epsilon_tolerance_rad_by_device_count"].items()
    }
    minimum_gap = float(metadata["minimum_collision_free_gap_rad"])
    conditions["final_max_over_epsilon"] = conditions.apply(
        lambda row: row["final_10_cycle_new_max_abs_dev_median"] / epsilon[int(row["device_count"])],
        axis=1,
    )
    conditions["final_min_gap_over_threshold"] = (
        conditions["final_10_cycle_min_gap_median"] / minimum_gap
    )
    conditions["simultaneous_collisions_per_run"] = (
        conditions["simultaneous_collision_count_total"] / conditions["run_count"]
    )
    conservative = (
        runs.assign(
            max_failed_mingap_passed=(~runs["max_converged"].astype(bool))
            & runs["mingap_converged"].astype(bool)
        )
        .groupby(["coupling_function", "device_count", "k"], as_index=False)[
            "max_failed_mingap_passed"
        ]
        .mean()
    )
    conservative["max_failed_mingap_passed_percent"] = (
        conservative["max_failed_mingap_passed"] * 100.0
    )
    conditions = conditions.merge(
        conservative.drop(columns="max_failed_mingap_passed"),
        on=["coupling_function", "device_count", "k"],
        how="left",
    )

    FIGURES_ROOT.mkdir(parents=True, exist_ok=True)
    plot_performance_overview(conditions)
    plot_convergence_details(conditions)
    plot_conservatism_and_collisions(conditions)


def plot_performance_overview(data: pd.DataFrame) -> None:
    metrics = (
        ("overall_per_percent_median", "Full-period PER median (%)", (0, 100)),
        ("ttu_reach_rate_percent", "TTU reach rate (%)", (0, 105)),
        ("max_convergence_rate_percent", "Formal max convergence rate (%)", (0, 105)),
        ("mingap_convergence_rate_percent", "Min-gap convergence rate (%)", (0, 105)),
    )
    fig, axes = plt.subplots(4, 2, figsize=(13.5, 15.5), sharex=True)
    draw_metric_grid(fig, axes, data, metrics)
    fig.suptitle("N-sweep v2: PER, TTU, and convergence rates", y=0.995, fontsize=16)
    save_figure(fig, "performance_overview")


def plot_convergence_details(data: pd.DataFrame) -> None:
    metrics = (
        ("max_convergence_cycle_censored_median", "Formal max censored median cycle", (0, 180)),
        ("mingap_convergence_cycle_censored_median", "Min-gap censored median cycle", (0, 180)),
        ("final_max_over_epsilon", "Final-10 max deviation / epsilon", None),
        ("final_min_gap_over_threshold", "Final-10 min gap / collision threshold", None),
    )
    fig, axes = plt.subplots(4, 2, figsize=(13.5, 15.5), sharex=True)
    draw_metric_grid(fig, axes, data, metrics, threshold_rows={2, 3})
    fig.suptitle("N-sweep v2: censored convergence and final margins", y=0.995, fontsize=16)
    save_figure(fig, "convergence_details")


def plot_conservatism_and_collisions(data: pd.DataFrame) -> None:
    metrics = (
        (
            "max_failed_mingap_passed_percent",
            "Min-gap converged but formal max failed (%)",
            (0, 105),
        ),
        ("simultaneous_collisions_per_run", "Exact-start collision rows per run", None),
    )
    fig, axes = plt.subplots(2, 2, figsize=(13.5, 8.7), sharex=True)
    draw_metric_grid(fig, axes, data, metrics)
    for column_index, function_name in enumerate(FUNCTIONS):
        ax = axes[1, column_index]
        function_data = data[data["coupling_function"] == function_name]
        if float(function_data["simultaneous_collisions_per_run"].max()) == 0.0:
            ax.set_ylim(-0.05, 1.0)
            ax.text(0.5, 0.5, "No exact-start collisions", transform=ax.transAxes, ha="center")
        else:
            ax.set_yscale("symlog", linthresh=0.5)
    fig.suptitle("N-sweep v2: formal-metric conservatism and collisions", y=0.995, fontsize=16)
    save_figure(fig, "conservatism_and_collisions")


def draw_metric_grid(
    fig: plt.Figure,
    axes,
    data: pd.DataFrame,
    metrics: tuple[tuple[str, str, tuple[float, float] | None], ...],
    threshold_rows: set[int] | None = None,
) -> None:
    threshold_rows = threshold_rows or set()
    for column_index, function_name in enumerate(FUNCTIONS):
        function_data = data[data["coupling_function"] == function_name]
        for row_index, (metric, ylabel, ylim) in enumerate(metrics):
            ax = axes[row_index, column_index]
            draw_n_lines(ax, function_data, metric)
            ax.set_ylabel(ylabel)
            if ylim is not None:
                ax.set_ylim(*ylim)
            if row_index in threshold_rows:
                ax.axhline(1.0, color="#444444", linestyle="--", linewidth=1.2)
            if row_index == 0:
                ax.set_title(FUNCTION_LABELS[function_name])
            if row_index == len(metrics) - 1:
                ax.set_xlabel("K (strength ratio = -1e-4)")
            style_axis(ax)
    add_figure_legend(fig)


def draw_n_lines(ax: plt.Axes, data: pd.DataFrame, metric: str) -> None:
    for device_count in DEVICE_COUNTS:
        subset = data[data["device_count"] == device_count].sort_values("k")
        ax.plot(
            subset["k"], subset[metric], color=COLORS[device_count],
            marker=MARKERS[device_count], linewidth=1.8, markersize=5.0,
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
            [], [], color=COLORS[n], marker=MARKERS[n], linewidth=1.8, label=f"N={n}"
        )
        for n in DEVICE_COUNTS
    ]
    fig.legend(handles=handles, loc="upper center", ncol=4, frameon=False, bbox_to_anchor=(0.5, 0.978))


def save_figure(fig: plt.Figure, stem: str) -> None:
    fig.tight_layout(rect=(0, 0, 1, 0.955))
    fig.savefig(FIGURES_ROOT / f"{stem}.png", dpi=180, bbox_inches="tight")
    fig.savefig(FIGURES_ROOT / f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    main()
