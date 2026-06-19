from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import pandas as pd

from research_program.config.plot_config import CONVERGENCE_ANALYSIS_CONFIG


CFG = CONVERGENCE_ANALYSIS_CONFIG


def parse_filename(csv_path: Path) -> tuple[str, int]:
    stem = csv_path.stem
    m = re.fullmatch(r"(.+)_(\d+)", stem)
    if m is None:
        raise ValueError(f"unexpected filename format: {csv_path.name}")
    return m.group(1), int(m.group(2))


def apply_strength_rule(coupling_function: str, coupling_strength: int) -> bool:
    rule = CFG.coupling_function_strength_rules.get(coupling_function)

    if CFG.target_coupling_functions:
        if coupling_function not in CFG.target_coupling_functions:
            return False

    if rule is not None:
        if rule.strength_min is not None and coupling_strength < rule.strength_min:
            return False
        if rule.strength_max is not None and coupling_strength > rule.strength_max:
            return False
        if rule.step is not None:
            base = rule.strength_min if rule.strength_min is not None else coupling_strength
            if (coupling_strength - base) % rule.step != 0:
                return False
        return True

    if CFG.coupling_strength_min is not None and coupling_strength < CFG.coupling_strength_min:
        return False
    if CFG.coupling_strength_max is not None and coupling_strength > CFG.coupling_strength_max:
        return False
    if CFG.coupling_strength_step is not None:
        base = CFG.coupling_strength_min if CFG.coupling_strength_min is not None else coupling_strength
        if (coupling_strength - base) % CFG.coupling_strength_step != 0:
            return False

    return True


def read_aggregated_stats(csv_path: Path) -> pd.DataFrame:
    return pd.read_csv(
        csv_path,
        dtype={
            "cycle_index": "int64",
            "count": "float64",
            "mean": "float64",
            "min": "float64",
            "max": "float64",
            "median": "float64",
            "std": "float64",
            "q25": "float64",
            "q75": "float64",
        },
    ).sort_values("cycle_index").reset_index(drop=True)


def find_convergence_point(
    df: pd.DataFrame,
    window_cycles: int,
    threshold: float,
) -> tuple[Optional[int], Optional[float]]:
    mean_series = df[["cycle_index", "mean"]].dropna().reset_index(drop=True)

    if len(mean_series) < window_cycles:
        return None, None

    values = mean_series["mean"].to_numpy()
    cycles = mean_series["cycle_index"].to_numpy()

    for i in range(len(values) - window_cycles + 1):
        window = values[i:i + window_cycles]
        if window.max() - window.min() <= threshold:
            convergence_cycle = int(cycles[i])
            convergence_value = float(values[i])
            return convergence_cycle, convergence_value

    return None, None


def collect_convergence_data(aggregated_stats_dir: Path | None = None) -> pd.DataFrame:
    rows = []
    source_dir = aggregated_stats_dir or CFG.aggregated_stats_dir
    csv_files = sorted(source_dir.glob("*.csv"))

    for csv_path in csv_files:
        coupling_function, coupling_strength = parse_filename(csv_path)

        if not apply_strength_rule(coupling_function, coupling_strength):
            continue

        df = read_aggregated_stats(csv_path)
        if df.empty:
            continue

        conv_cycle, conv_value = find_convergence_point(
            df=df,
            window_cycles=CFG.convergence_window_cycles,
            threshold=CFG.convergence_threshold,
        )

        if conv_cycle is None and not CFG.include_not_converged:
            continue

        rows.append(
            {
                "coupling_function": coupling_function,
                "coupling_strength": coupling_strength,
                "convergence_cycle": conv_cycle,
                "post_convergence_fluctuation": conv_value,
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=[
                "coupling_function",
                "coupling_strength",
                "convergence_cycle",
                "post_convergence_fluctuation",
            ]
        )

    return pd.DataFrame(rows).sort_values(
        ["coupling_function", "coupling_strength"]
    ).reset_index(drop=True)


def save_convergence_csv(df: pd.DataFrame, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "convergence_summary.csv"
    df.to_csv(output_path, index=False)
    return output_path


def save_convergence_plot(df: pd.DataFrame, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / CFG.output_filename

    fig, ax_left = plt.subplots(figsize=(CFG.figure_width, CFG.figure_height))
    ax_right = ax_left.twinx()

    unique_functions = sorted(df["coupling_function"].dropna().unique().tolist())

    function_legend_handles = []

    for coupling_function in unique_functions:
        sub = df[df["coupling_function"] == coupling_function].sort_values("coupling_strength")
        if sub.empty:
            continue

        color = CFG.coupling_function_base_colors.get(coupling_function, "tab:blue")

        # 左軸: 収束時間 → 実線
        ax_left.plot(
            sub["coupling_strength"],
            sub["convergence_cycle"],
            color=color,
            linestyle="-",
            linewidth=2.0,
        )

        # 右軸: 収束後の揺らぎ → 点線
        ax_right.plot(
            sub["coupling_strength"],
            sub["post_convergence_fluctuation"],
            color=color,
            linestyle=":",
            linewidth=2.0,
        )

        display_name = "FrogChorus" if coupling_function == "FROGCHORUS" else coupling_function

        function_legend_handles.append(
            Line2D(
                [0], [0],
                color=color,
                linestyle="-",
                linewidth=2.0,
                label=display_name,
            )
        )

    ax_left.set_xlabel("Coupling strength(×-0.0001)", fontsize=CFG.font_size_label)
    ax_left.set_ylabel("Convergence cycle", fontsize=CFG.font_size_label)
    ax_right.set_ylabel("Post-convergence fluctuation", fontsize=CFG.font_size_label)

    if CFG.xlim_min is not None or CFG.xlim_max is not None:
        ax_left.set_xlim(left=CFG.xlim_min, right=CFG.xlim_max)

    if CFG.ylim_left_min is not None or CFG.ylim_left_max is not None:
        ax_left.set_ylim(bottom=CFG.ylim_left_min, top=CFG.ylim_left_max)

    if CFG.ylim_right_min is not None or CFG.ylim_right_max is not None:
        ax_right.set_ylim(bottom=CFG.ylim_right_min, top=CFG.ylim_right_max)

    ax_left.tick_params(axis="both", labelsize=CFG.font_size_ticks)
    ax_right.tick_params(axis="y", labelsize=CFG.font_size_ticks)

    ax_left.grid(True)

    if CFG.show_title:
        ax_left.set_title(
            "Convergence Summary",
            fontsize=CFG.font_size_title,
        )

    # coupling_function 用凡例（色）
    legend1 = ax_left.legend(
        handles=function_legend_handles,
        title="Coupling function",
        fontsize=CFG.font_size_legend,
        loc="upper left",
    )
    ax_left.add_artist(legend1)

    # 線種の意味用凡例
    axis_style_handles = [
        Line2D([0], [0], color="black", linestyle="-", linewidth=2.0, label="Convergence cycle"),
        Line2D([0], [0], color="black", linestyle=":", linewidth=2.0, label="Post-convergence fluctuation"),#(収束後の揺らぎ)
    ]
    ax_left.legend(
        handles=axis_style_handles,
        title="Axis meaning",
        fontsize=CFG.font_size_legend,
        loc="upper right",
    )

    fig.tight_layout()
    fig.savefig(output_path, dpi=CFG.save_dpi)
    plt.close(fig)

    return output_path


def main() -> None:
    aggregated_stats_dir = Path(os.environ.get("RESEARCH_PROGRAM_AGGREGATED_DIR", CFG.aggregated_stats_dir))
    if not aggregated_stats_dir.exists():
        raise FileNotFoundError(f"aggregated_stats folder not found: {aggregated_stats_dir}")

    CFG.graphs_dir.mkdir(parents=True, exist_ok=True)

    df = collect_convergence_data(aggregated_stats_dir)
    csv_path = save_convergence_csv(df, CFG.graphs_dir)
    print(f"saved: {csv_path}")

    if not df.empty:
        plot_path = save_convergence_plot(df, CFG.graphs_dir)
        print(f"saved: {plot_path}")
    else:
        print("no target data found")


if __name__ == "__main__":
    main()
