from __future__ import annotations

import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from research_program.config.plot_config import PER_PLOT_CONFIG
from research_program.io.send_log import (
    add_detection_time_column,
    detection_time_values,
    normalize_send_time_columns,
)
from research_program.analysis.calculate_cycle_data import ensure_cycle_data_for_run


CFG = PER_PLOT_CONFIG
WINDOW_WIDTH_CYCLES = CFG.per_window_width_cycles
RESULTS_DIR = Path(os.environ.get("RESEARCH_PROGRAM_RUNS_DIR", CFG.results_dir))
OUTPUT_DIR = CFG.graphs_dir
REFERENCE_GAP_RATIO = 1.3


def read_metadata(metadata_path: Path) -> tuple[float, list[str]]:
    df = pd.read_csv(metadata_path)

    cycle_time = float(df.loc[0, "cycle_time"])

    tags_raw = df.loc[0, "tags"] if "tags" in df.columns else ""
    if pd.isna(tags_raw) or tags_raw == "":
        tags = []
    else:
        tags = [tag.strip() for tag in str(tags_raw).split(";") if tag.strip()]

    if "sec" in tags:
        cycle_time *= 1000.0

    return cycle_time, tags


def extract_device_count_from_tags(tags: list[str]) -> int:
    for tag in tags:
        m = re.fullmatch(r"(\d+)dai", tag)
        if m is not None:
            return int(m.group(1))
    raise ValueError(f"device count tag like '50dai' was not found in tags: {tags}")


def read_send_log(send_log_path: Path) -> pd.DataFrame:
    df = pd.read_csv(
        send_log_path,
        dtype={
            "time": "float64",
            "oscillator_id": "string",
            "send_count": "int64",
        },
    )
    return df.sort_values(["time", "oscillator_id"]).reset_index(drop=True)


def read_calculated_cycle_data(cycle_data_path: Path) -> tuple[int, np.ndarray, np.ndarray]:
    df = pd.read_csv(cycle_data_path)

    reference_id = int(df.loc[0, "reference_id"])
    cycle_starts = df["cycle_start_time"].to_numpy(dtype=np.float64)
    is_original_cycle = df["is_original_cycle"].to_numpy(dtype=bool)

    return reference_id, cycle_starts, is_original_cycle


def normalize_oscillator_id_column(send_df: pd.DataFrame, tags: list[str]) -> pd.DataFrame:
    send_df = send_df.copy()

    if "hex" in tags:
        send_df["oscillator_id"] = send_df["oscillator_id"].map(lambda x: int(str(x), 16))
    else:
        send_df["oscillator_id"] = send_df["oscillator_id"].map(lambda x: int(str(x)))

    return send_df


def normalize_time_column(send_df: pd.DataFrame, tags: list[str]) -> pd.DataFrame:
    return add_detection_time_column(normalize_send_time_columns(send_df, tags))


def assign_cycles_from_reference_windows(
    send_df: pd.DataFrame,
    cycle_starts: np.ndarray,
) -> pd.DataFrame:
    """
    visualize_phase_diff.py と同じ考え方で，
    cycle_starts[i] 以上 cycle_starts[i+1] 未満をサイクル i+1 とする。
    最後の区間は cycle_starts[-1] 以上を最後のサイクルとする。
    """
    df = send_df.copy()
    times = detection_time_values(df)

    cycle_index = np.searchsorted(cycle_starts, times, side="right")
    valid = np.isfinite(times) & (cycle_index > 0)

    df = df.loc[valid].copy()
    df["cycle_index"] = cycle_index[valid].astype(np.int64)

    return df


def compute_per_series(
    send_df: pd.DataFrame,
    cycle_starts: np.ndarray,
    num_devices: int,
    window_width_cycles: int,
) -> tuple[np.ndarray, np.ndarray]:
    send_df = assign_cycles_from_reference_windows(send_df, cycle_starts)

    max_cycle = len(cycle_starts)
    if max_cycle < window_width_cycles:
        return np.array([], dtype=np.int64), np.array([], dtype=np.float64)

    counts_by_cycle = send_df.groupby("cycle_index").size().sort_index()

    counts_full = np.zeros(max_cycle, dtype=np.int64)
    if not counts_by_cycle.empty:
        cycle_indices = counts_by_cycle.index.to_numpy(dtype=np.int64)
        counts_full[cycle_indices - 1] = counts_by_cycle.to_numpy(dtype=np.int64)

    kernel = np.ones(window_width_cycles, dtype=np.int64)
    window_counts = np.convolve(counts_full, kernel, mode="valid")

    denominator = float(num_devices * window_width_cycles)
    success_ratio = window_counts / denominator
    per_percent = (1.0 - success_ratio) * 100.0
    per_percent = np.clip(per_percent, 0.0, None)

    x = np.arange(window_width_cycles, max_cycle + 1, dtype=np.int64)
    return x, per_percent


def compute_per_change_series(
    x: np.ndarray,
    per_percent: np.ndarray,
    change_width_cycles: int,
) -> tuple[np.ndarray, np.ndarray]:
    """
    一定サイクル幅ごとの PER 変化量を返す。

    返り値:
        x_change: 変化量を対応づけるサイクル番号
        per_change: PER[x] - PER[x-change_width_cycles]
    """
    if len(x) == 0 or len(per_percent) == 0:
        return np.array([], dtype=np.int64), np.array([], dtype=np.float64)

    x_to_per = {int(cx): float(py) for cx, py in zip(x, per_percent)}

    x_change_list = []
    per_change_list = []

    for cx in x:
        prev_cycle = int(cx) - change_width_cycles
        if prev_cycle in x_to_per:
            x_change_list.append(int(cx))
            per_change_list.append(float(x_to_per[int(cx)] - x_to_per[prev_cycle]))

    return np.array(x_change_list, dtype=np.int64), np.array(per_change_list, dtype=np.float64)


def save_per_plot(
    run_dir: Path,
    output_dir: Path,
    x: np.ndarray,
    per_percent: np.ndarray,
    window_width_cycles: int,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{run_dir.name}.pdf"

    plt.figure(figsize=(CFG.figure_width, CFG.figure_height))
    plt.scatter(x, per_percent, s=CFG.scatter_size)

    if CFG.xlim_min is not None or CFG.xlim_max is not None:
        plt.xlim(left=CFG.xlim_min, right=CFG.xlim_max)

    if CFG.per_ylim_min is not None or CFG.per_ylim_max is not None:
        plt.ylim(bottom=CFG.per_ylim_min, top=CFG.per_ylim_max)

    plt.xlabel("Cycle number", fontsize=CFG.font_size_label)
    plt.ylabel("PER [%]", fontsize=CFG.font_size_label)

    if CFG.show_title:
        plt.title(
            f"PER vs Cycle\nrun={run_dir.name}, window={window_width_cycles} cycles",
            fontsize=CFG.font_size_title,
        )

    plt.xticks(fontsize=CFG.font_size_ticks)
    plt.yticks(fontsize=CFG.font_size_ticks)
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(output_path, dpi=CFG.save_dpi)
    plt.close()
    pd.DataFrame(
        {
            "cycle_index": x.astype(np.int64),
            "per_percent": per_percent.astype(np.float64),
            "window_width_cycles": int(window_width_cycles),
        }
    ).to_csv(output_path.with_suffix(".csv"), index=False)

    return output_path


def save_per_change_plot(
    run_dir: Path,
    output_dir: Path,
    x_change: np.ndarray,
    per_change: np.ndarray,
    change_width_cycles: int,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{run_dir.name}_change.pdf"

    plt.figure(figsize=(CFG.figure_width, CFG.figure_height))
    plt.scatter(x_change, per_change, s=CFG.scatter_size)

    plt.axhline(0.0, linewidth=1)

    if CFG.xlim_min is not None or CFG.xlim_max is not None:
        plt.xlim(left=CFG.xlim_min, right=CFG.xlim_max)

    if CFG.per_change_ylim_min is not None or CFG.per_change_ylim_max is not None:
        plt.ylim(bottom=CFG.per_change_ylim_min, top=CFG.per_change_ylim_max)

    plt.xlabel("Cycle number", fontsize=CFG.font_size_label)
    plt.ylabel(f"PER change over {change_width_cycles} cycles [%]", fontsize=CFG.font_size_label)

    if CFG.show_title:
        plt.title(
            f"PER Change vs Cycle\nrun={run_dir.name}, change_width={change_width_cycles} cycles",
            fontsize=CFG.font_size_title,
        )

    plt.xticks(fontsize=CFG.font_size_ticks)
    plt.yticks(fontsize=CFG.font_size_ticks)
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(output_path, dpi=CFG.save_dpi)
    plt.close()
    pd.DataFrame(
        {
            "cycle_index": x_change.astype(np.int64),
            "per_change_percent": per_change.astype(np.float64),
            "change_width_cycles": int(change_width_cycles),
        }
    ).to_csv(output_path.with_suffix(".csv"), index=False)

    return output_path


def process_run(run_dir: Path, output_dir: Path, window_width_cycles: int) -> str:
    send_log_path = run_dir / "send_log.csv"
    metadata_path = run_dir / "metadata.csv"
    cycle_data_path = run_dir / "calculated_Cycle_data.csv"

    if not send_log_path.exists() or not metadata_path.exists():
        return f"skip: {run_dir} (missing send_log.csv or metadata.csv)"

    if not cycle_data_path.exists():
        ensure_cycle_data_for_run(run_dir)

    if not cycle_data_path.exists():
        return f"skip: {run_dir} (failed to create calculated_Cycle_data.csv)"

    send_df = read_send_log(send_log_path)
    if send_df.empty:
        return f"skip: {run_dir} (empty send_log.csv)"

    cycle_time, tags = read_metadata(metadata_path)
    num_devices = extract_device_count_from_tags(tags)

    send_df = normalize_oscillator_id_column(send_df, tags)
    send_df = normalize_time_column(send_df, tags)

    reference_id, cycle_starts, _ = read_calculated_cycle_data(cycle_data_path)

    x, per_percent = compute_per_series(
        send_df=send_df,
        cycle_starts=cycle_starts,
        num_devices=num_devices,
        window_width_cycles=window_width_cycles,
    )

    if len(x) == 0:
        return f"skip: {run_dir} (not enough data)"

    output_path = save_per_plot(run_dir, output_dir, x, per_percent, window_width_cycles)

    messages = [f"saved: {output_path}", f"reference id: {reference_id}"]

    if CFG.show_per_change_plot:
        x_change, per_change = compute_per_change_series(
            x=x,
            per_percent=per_percent,
            change_width_cycles=CFG.per_change_width_cycles,
        )

        if len(x_change) > 0:
            output_change_path = save_per_change_plot(
                run_dir=run_dir,
                output_dir=output_dir,
                x_change=x_change,
                per_change=per_change,
                change_width_cycles=CFG.per_change_width_cycles,
            )
            messages.append(f"saved: {output_change_path}")

            best_improve_idx = int(np.argmin(per_change))
            best_improve_cycle = int(x_change[best_improve_idx])
            best_improve_value = float(per_change[best_improve_idx])

            messages.append(
                f"best PER improvement: run={run_dir.name}, "
                f"cycle={best_improve_cycle}, "
                f"delta={best_improve_value:.6f}%"
            )

    best_idx = int(np.argmin(per_percent))
    best_cycle = int(x[best_idx])
    best_per = float(per_percent[best_idx])

    messages.append(
        f"best PER: run={run_dir.name}, cycle={best_cycle}, PER={best_per:.6f}%"
    )

    return "\n".join(messages)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not RESULTS_DIR.exists():
        raise FileNotFoundError(f"results folder not found: {RESULTS_DIR}")

    run_dirs = sorted([p for p in RESULTS_DIR.iterdir() if p.is_dir()])

    if not run_dirs:
        print("no run directories found in results")
        return

    max_workers = min(len(run_dirs), (os.cpu_count() or 1))

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(process_run, run_dir, OUTPUT_DIR, WINDOW_WIDTH_CYCLES)
            for run_dir in run_dirs
        ]

        for future in as_completed(futures):
            print(future.result())


if __name__ == "__main__":
    main()
