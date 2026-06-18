from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import pandas as pd

from app_config import AGGREGATED_PHASE_GAP_ERROR_PLOT_CONFIG


CFG = AGGREGATED_PHASE_GAP_ERROR_PLOT_CONFIG


def parse_filename(csv_path: Path) -> tuple[str, int]:
    """
    aggregated_stats/KURAMOTO_3.csv のような名前から
    coupling_function と coupling_strength を取り出す。
    """
    stem = csv_path.stem
    m = re.fullmatch(r"(.+)_(\d+)", stem)
    if m is None:
        raise ValueError(f"unexpected filename format: {csv_path.name}")

    coupling_function = m.group(1)
    coupling_strength = int(m.group(2))
    return coupling_function, coupling_strength


def is_target_group(coupling_function: str, coupling_strength: int) -> bool:
    if CFG.target_coupling_functions:
        if coupling_function not in CFG.target_coupling_functions:
            return False

    if CFG.coupling_strength_min is not None:
        if coupling_strength < CFG.coupling_strength_min:
            return False

    if CFG.coupling_strength_max is not None:
        if coupling_strength > CFG.coupling_strength_max:
            return False

    return True


def read_aggregated_stats(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(
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
    )
    return df.sort_values("cycle_index").reset_index(drop=True)


def save_plot(
    csv_path: Path,
    output_dir: Path,
    coupling_function: str,
    coupling_strength: int,
    df: pd.DataFrame,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{csv_path.stem}.png"

    plt.figure(figsize=(CFG.figure_width, CFG.figure_height))

    x = df["cycle_index"]

    if CFG.show_q25_q75_band and "q25" in df.columns and "q75" in df.columns:
        valid_band = (~df["q25"].isna()) & (~df["q75"].isna())
        if valid_band.any():
            plt.fill_between(
                x[valid_band],
                df.loc[valid_band, "q25"],
                df.loc[valid_band, "q75"],
                alpha=0.2,
                label="q25-q75",
            )

    if CFG.show_mean_line and "mean" in df.columns:
        valid_mean = ~df["mean"].isna()
        if valid_mean.any():
            plt.plot(
                x[valid_mean],
                df.loc[valid_mean, "mean"],
                label="mean",
                linewidth=CFG.mean_linewidth,
            )

    if CFG.show_min_line and "min" in df.columns:
        valid_min = ~df["min"].isna()
        if valid_min.any():
            plt.plot(
                x[valid_min],
                df.loc[valid_min, "min"],
                label="min",
                linewidth=CFG.minmax_linewidth,
            )

    if CFG.show_max_line and "max" in df.columns:
        valid_max = ~df["max"].isna()
        if valid_max.any():
            plt.plot(
                x[valid_max],
                df.loc[valid_max, "max"],
                label="max",
                linewidth=CFG.minmax_linewidth,
            )

    if CFG.xlim_min is not None or CFG.xlim_max is not None:
        plt.xlim(left=CFG.xlim_min, right=CFG.xlim_max)

    if CFG.ylim_min is not None or CFG.ylim_max is not None:
        plt.ylim(bottom=CFG.ylim_min, top=CFG.ylim_max)

    plt.xlabel("Cycle number", fontsize=CFG.font_size_label)
    plt.ylabel("Phase gap error ratio", fontsize=CFG.font_size_label)

    if CFG.show_title:
        plt.title(
            f"Aggregated Phase Gap Error Ratio\n"
            f"coupling_function={coupling_function}, coupling_strength={coupling_strength}",
            fontsize=CFG.font_size_title,
        )

    plt.xticks(fontsize=CFG.font_size_ticks)
    plt.yticks(fontsize=CFG.font_size_ticks)

    plt.grid(True)

    if CFG.show_legend:
        plt.legend(fontsize=CFG.font_size_legend)

    plt.tight_layout()
    plt.savefig(output_path, dpi=CFG.save_dpi)
    plt.close()

    return output_path


def process_file(csv_path: Path, output_dir: Path) -> Optional[str]:
    coupling_function, coupling_strength = parse_filename(csv_path)

    if not is_target_group(coupling_function, coupling_strength):
        return None

    df = read_aggregated_stats(csv_path)
    if df.empty:
        return f"skip: {csv_path} (empty csv)"

    output_path = save_plot(
        csv_path=csv_path,
        output_dir=output_dir,
        coupling_function=coupling_function,
        coupling_strength=coupling_strength,
        df=df,
    )
    return f"saved: {output_path}"


def main() -> None:
    aggregated_stats_dir = CFG.aggregated_stats_dir
    graphs_dir = CFG.graphs_dir
    graphs_dir.mkdir(parents=True, exist_ok=True)

    if not aggregated_stats_dir.exists():
        raise FileNotFoundError(f"aggregated_stats folder not found: {aggregated_stats_dir}")

    csv_files = sorted(aggregated_stats_dir.glob("*.csv"))

    if not csv_files:
        print("no aggregated csv files found")
        return

    saved_count = 0
    for csv_path in csv_files:
        result = process_file(csv_path, graphs_dir)
        if result is not None:
            print(result)
            if result.startswith("saved:"):
                saved_count += 1

    print(f"done: saved {saved_count} graph(s)")


if __name__ == "__main__":
    main()