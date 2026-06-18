from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import pandas as pd

from app_config import PHASE_GAP_ERROR_PLOT_CONFIG
from calculate_phase_gap_error import ensure_phase_gap_error_for_run


CFG = PHASE_GAP_ERROR_PLOT_CONFIG
INPUT_FILENAME = "phase_gap_error.csv"


def read_phase_gap_error(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(
        csv_path,
        dtype={
            "cycle_index": "int64",
            "mean_abs_diff_from_ideal_phase_gap": "float64",
            "mean_abs_diff_from_ideal_phase_gap_ratio": "float64",
        },
    )
    return df.sort_values("cycle_index").reset_index(drop=True)


def save_phase_gap_error_plot(
    run_dir: Path,
    output_dir: Path,
    df: pd.DataFrame,
    y_column: str,
    y_label: str,
    filename_suffix: str = "",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{run_dir.name}{filename_suffix}.png"

    plt.figure(figsize=(CFG.figure_width, CFG.figure_height))

    x = df["cycle_index"]
    y = df[y_column]

    valid = ~y.isna()
    x = x[valid]
    y = y[valid]

    plt.scatter(x, y, s=CFG.scatter_size)

    if CFG.xlim_min is not None or CFG.xlim_max is not None:
        plt.xlim(left=CFG.xlim_min, right=CFG.xlim_max)

    if CFG.ylim_min is not None or CFG.ylim_max is not None:
        plt.ylim(bottom=CFG.ylim_min, top=CFG.ylim_max)

    plt.xlabel("Cycle number", fontsize=CFG.font_size_label)
    plt.ylabel(y_label, fontsize=CFG.font_size_label)

    if CFG.show_title:
        plt.title(
            f"{y_label} vs Cycle\nrun={run_dir.name}",
            fontsize=CFG.font_size_title,
        )

    plt.xticks(fontsize=CFG.font_size_ticks)
    plt.yticks(fontsize=CFG.font_size_ticks)

    plt.grid(True)
    plt.tight_layout()
    plt.savefig(output_path, dpi=CFG.save_dpi)
    plt.close()

    return output_path


def process_run(run_dir: Path, graphs_dir: Path) -> Optional[str]:
    input_path = run_dir / INPUT_FILENAME

    if not input_path.exists():
        ensure_phase_gap_error_for_run(run_dir)

    if not input_path.exists():
        return f"skip: {run_dir} (failed to create {INPUT_FILENAME})"

    df = read_phase_gap_error(input_path)
    if df.empty:
        return f"skip: {run_dir} ({INPUT_FILENAME} is empty)"

    output_path_error = save_phase_gap_error_plot(
        run_dir=run_dir,
        output_dir=graphs_dir,
        df=df,
        y_column="mean_abs_diff_from_ideal_phase_gap",
        y_label="Phase gap error [rad]",
        filename_suffix="",
    )

    output_path_ratio = save_phase_gap_error_plot(
        run_dir=run_dir,
        output_dir=graphs_dir,
        df=df,
        y_column="mean_abs_diff_from_ideal_phase_gap_ratio",
        y_label="Phase gap error ratio",
        filename_suffix="_ratio",
    )

    return f"saved: {output_path_error}\nsaved: {output_path_ratio}"


def main() -> None:
    results_dir = CFG.results_dir
    graphs_dir = CFG.graphs_dir
    graphs_dir.mkdir(parents=True, exist_ok=True)

    if not results_dir.exists():
        raise FileNotFoundError(f"results folder not found: {results_dir}")

    run_dirs = sorted([p for p in results_dir.iterdir() if p.is_dir()])

    if not run_dirs:
        print("no run directories found in results")
        return

    max_workers = min(len(run_dirs), (os.cpu_count() or 1))

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_run, run_dir, graphs_dir) for run_dir in run_dirs]

        for future in as_completed(futures):
            print(future.result())


if __name__ == "__main__":
    main()