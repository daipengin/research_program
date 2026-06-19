from __future__ import annotations

import math
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from research_program.config.plot_config import VISUALIZE_PHASE_DIFF_CONFIG
from research_program.analysis.calculate_cycle_data import ensure_cycle_data_for_run


CFG = VISUALIZE_PHASE_DIFF_CONFIG


def wrap_angle_array(angle: np.ndarray, y_range_mode: str) -> np.ndarray:
    if y_range_mode == "0_to_2pi":
        return np.mod(angle, 2.0 * math.pi)

    if y_range_mode == "minus_pi_to_pi":
        return (angle + math.pi) % (2.0 * math.pi) - math.pi

    raise ValueError(f"Unknown y_range_mode: {y_range_mode}")


def get_y_axis_range(y_range_mode: str) -> tuple[float, float]:
    if y_range_mode == "0_to_2pi":
        return 0.0, 2.0 * math.pi

    if y_range_mode == "minus_pi_to_pi":
        return -math.pi, math.pi

    raise ValueError(f"Unknown y_range_mode: {y_range_mode}")


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


def read_calculated_cycle_data(cycle_data_path: Path) -> tuple[int, np.ndarray, np.ndarray]:
    df = pd.read_csv(cycle_data_path)

    reference_id = int(df.loc[0, "reference_id"])
    cycle_starts = df["cycle_start_time"].to_numpy(dtype=np.float64)
    is_original_cycle = df["is_original_cycle"].to_numpy(dtype=bool)

    return reference_id, cycle_starts, is_original_cycle


def normalize_oscillator_id_column(send_df: pd.DataFrame, tags: list[str]) -> pd.DataFrame:
    send_df = send_df.copy()

    if "hex" in tags:
        send_df["oscillator_id"] = send_df["oscillator_id"].map(
            lambda x: int(str(x), 16)
        )
    else:
        send_df["oscillator_id"] = send_df["oscillator_id"].map(
            lambda x: int(str(x))
        )

    return send_df


def normalize_time_column(send_df: pd.DataFrame, tags: list[str]) -> pd.DataFrame:
    send_df = send_df.copy()

    if "sec" in tags:
        send_df["time"] = send_df["time"] * 1000.0

    return send_df


def group_send_times_by_id(send_df: pd.DataFrame) -> dict[int, np.ndarray]:
    grouped = {}
    for osc_id, group in send_df.groupby("oscillator_id"):
        grouped[int(osc_id)] = np.sort(group["time"].to_numpy(dtype=np.float64))
    return grouped


def build_cycle_lengths(
    cycle_starts: np.ndarray,
    cycle_time: float,
    mode: str,
) -> np.ndarray:
    if mode == "cycle_time":
        return np.full(len(cycle_starts), cycle_time, dtype=np.float64)

    if mode == "actual_interval":
        cycle_lengths = np.empty(len(cycle_starts), dtype=np.float64)

        if len(cycle_starts) == 1:
            cycle_lengths[0] = cycle_time
            return cycle_lengths

        cycle_lengths[:-1] = np.diff(cycle_starts)
        cycle_lengths[-1] = cycle_lengths[-2]
        return cycle_lengths

    raise ValueError(f"Unknown phase_diff_mode: {mode}")


def find_times_in_cycle_windows(
    times: np.ndarray,
    cycle_starts: np.ndarray,
) -> np.ndarray:
    result = np.full(len(cycle_starts), np.nan, dtype=np.float64)

    if times.size == 0 or cycle_starts.size == 0:
        return result

    for i in range(len(cycle_starts)):
        start = cycle_starts[i]

        if i + 1 < len(cycle_starts):
            end = cycle_starts[i + 1]
            mask = (times >= start) & (times < end)
        else:
            mask = times >= start

        candidates = times[mask]
        if candidates.size > 0:
            result[i] = candidates[0]

    return result


def build_phase_diff_series(
    send_df: pd.DataFrame,
    cycle_time: float,
    reference_id: int,
    cycle_starts: np.ndarray,
    is_original_cycle: np.ndarray,
) -> tuple[int, dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]]]:
    grouped = group_send_times_by_id(send_df)
    cycle_lengths = build_cycle_lengths(cycle_starts, cycle_time, CFG.phase_diff_mode)
    cycle_indices = np.arange(1, len(cycle_starts) + 1, dtype=np.int64)

    series: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}

    for osc_id, times in grouped.items():
        selected_times = find_times_in_cycle_windows(times, cycle_starts)

        time_diff = selected_times - cycle_starts
        phase_diff = 2.0 * math.pi * (time_diff / cycle_lengths)
        phase_diff = wrap_angle_array(phase_diff, CFG.y_range_mode)

        series[osc_id] = (cycle_indices, phase_diff, is_original_cycle)

    return reference_id, series


def save_phase_diff_plot(
    run_dir: Path,
    output_dir: Path,
    reference_id: int,
    series: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{run_dir.name}.pdf"

    plt.figure(figsize=(CFG.figure_width, CFG.figure_height))

    for osc_id, (x, y, is_original_cycle) in sorted(series.items()):
        valid = ~np.isnan(y)

        if CFG.hide_filled_cycles:
            valid = valid & is_original_cycle

        x_valid = x[valid]
        y_valid = y[valid]

        label = f"id={osc_id}"
        if osc_id == reference_id:
            label += " (reference)"

        plt.scatter(x_valid, y_valid, s=CFG.scatter_size, label=label,rasterized=True)

    ymin, ymax = get_y_axis_range(CFG.y_range_mode)
    plt.ylim(ymin, ymax)

    if CFG.y_range_mode == "0_to_2pi":
        plt.axhline(0.0, linewidth=1)
        plt.axhline(2.0 * math.pi, linewidth=1)
    elif CFG.y_range_mode == "minus_pi_to_pi":
        plt.axhline(-math.pi, linewidth=1)
        plt.axhline(0.0, linewidth=1)
        plt.axhline(math.pi, linewidth=1)

    if CFG.xlim_min is not None or CFG.xlim_max is not None:
        plt.xlim(left=CFG.xlim_min, right=CFG.xlim_max)

    #plt.xlabel("経過サイクル数", fontsize=CFG.font_size_label)
    #plt.ylabel("基準との位相差 [rad]", fontsize=CFG.font_size_label)
    plt.xlabel("Cycle number", fontsize=CFG.font_size_label)
    plt.ylabel("Phase difference [rad]", fontsize=CFG.font_size_label)

    if CFG.show_title:
        plt.title(
            f"Phase Difference vs Cycle\nrun={run_dir.name}, reference_id={reference_id}",
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


def process_run(run_dir: Path, graphs_dir: Path) -> Optional[str]:
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
    send_df = normalize_oscillator_id_column(send_df, tags)
    send_df = normalize_time_column(send_df, tags)

    reference_id, cycle_starts, is_original_cycle = read_calculated_cycle_data(cycle_data_path)

    reference_id, series = build_phase_diff_series(
        send_df=send_df,
        cycle_time=cycle_time,
        reference_id=reference_id,
        cycle_starts=cycle_starts,
        is_original_cycle=is_original_cycle,
    )

    output_path = save_phase_diff_plot(run_dir, graphs_dir, reference_id, series)
    return f"saved: {output_path}"


def main() -> None:
    results_dir = Path(os.environ.get("RESEARCH_PROGRAM_RUNS_DIR", CFG.results_dir))
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
