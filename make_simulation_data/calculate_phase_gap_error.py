from __future__ import annotations

import math
import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from calculate_cycle_data import ensure_cycle_data_for_run


RESULTS_DIR = Path("results")
OUTPUT_FILENAME = "phase_gap_error.csv"





def read_metadata(metadata_path: Path) -> tuple[list[str], int]:
    df = pd.read_csv(metadata_path)

    tags_raw = df.loc[0, "tags"] if "tags" in df.columns else ""
    if pd.isna(tags_raw) or tags_raw == "":
        tags = []
    else:
        tags = [tag.strip() for tag in str(tags_raw).split(";") if tag.strip()]

    num_devices = extract_device_count_from_tags(tags)
    return tags, num_devices


def extract_device_count_from_tags(tags: list[str]) -> int:
    for tag in tags:
        m = re.fullmatch(r"(\d+)dai", tag)
        if m is not None:
            return int(m.group(1))
    raise ValueError(f"device count tag like '20dai' was not found in tags: {tags}")


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


def read_calculated_cycle_data(cycle_data_path: Path) -> tuple[np.ndarray, np.ndarray]:
    df = pd.read_csv(cycle_data_path)
    cycle_starts = df["cycle_start_time"].to_numpy(dtype=np.float64)
    is_original_cycle = df["is_original_cycle"].to_numpy(dtype=bool)
    return cycle_starts, is_original_cycle


def normalize_oscillator_id_column(send_df: pd.DataFrame, tags: list[str]) -> pd.DataFrame:
    send_df = send_df.copy()

    if "hex" in tags:
        send_df["oscillator_id"] = send_df["oscillator_id"].map(lambda x: int(str(x), 16))
    else:
        send_df["oscillator_id"] = send_df["oscillator_id"].map(lambda x: int(str(x)))

    return send_df


def normalize_time_column(send_df: pd.DataFrame, tags: list[str]) -> pd.DataFrame:
    send_df = send_df.copy()

    if "sec" in tags:
        send_df["time"] = send_df["time"] * 1000.0

    return send_df


def compute_cycle_interval_lengths(cycle_starts: np.ndarray) -> np.ndarray:
    """
    各サイクルの長さを返す。
    最後のサイクルは直前のサイクル長を使う。
    サイクルが1個しかない場合は NaN にする。
    """
    n = len(cycle_starts)
    if n == 0:
        return np.array([], dtype=np.float64)

    lengths = np.full(n, np.nan, dtype=np.float64)

    if n == 1:
        return lengths

    lengths[:-1] = np.diff(cycle_starts)
    lengths[-1] = lengths[-2]
    return lengths


def assign_cycles_from_reference_windows(
    send_df: pd.DataFrame,
    cycle_starts: np.ndarray,
) -> pd.DataFrame:
    """
    calculated_Cycle_data.csv の cycle_start_time を使って，
    各送信がどのサイクル区間に属するかを決める。
    cycle_index は1始まり。
    """
    df = send_df.copy()
    times = df["time"].to_numpy(dtype=np.float64)

    cycle_index = np.searchsorted(cycle_starts, times, side="right")
    valid = cycle_index > 0

    df = df.loc[valid].copy()
    df["cycle_index"] = cycle_index[valid].astype(np.int64)

    return df


def compute_mean_abs_gap_error_per_cycle(
    send_df: pd.DataFrame,
    cycle_starts: np.ndarray,
    num_devices: int,
) -> pd.DataFrame:
    """
    各サイクルについて，
    同じサイクル内の送信時刻から位相を計算し，
    隣接振動子間の位相差と理想位相差 2π/N のずれの絶対値平均を返す。

    同じサイクル内の最後と最初の差も含める。
    """
    ideal_gap = 2.0 * math.pi / num_devices

    cycle_lengths = compute_cycle_interval_lengths(cycle_starts)
    indexed_df = assign_cycles_from_reference_windows(send_df, cycle_starts)

    rows: list[dict] = []

    for cycle_idx in range(1, len(cycle_starts) + 1):
        cycle_start = cycle_starts[cycle_idx - 1]
        cycle_length = cycle_lengths[cycle_idx - 1]

        if not np.isfinite(cycle_length) or cycle_length <= 0:
            rows.append(
                {
                    "cycle_index": cycle_idx,
                    "mean_abs_diff_from_ideal_phase_gap": np.nan,
                }
            )
            continue

        cycle_df = indexed_df.loc[indexed_df["cycle_index"] == cycle_idx, ["time", "oscillator_id"]].copy()

        if cycle_df.empty:
            rows.append(
                {
                    "cycle_index": cycle_idx,
                    "mean_abs_diff_from_ideal_phase_gap": np.nan,
                }
            )
            continue

        # 同一サイクル内で同じ振動子が複数回送信している場合は，
        # 最初の1回だけ採用する。
        cycle_df = cycle_df.sort_values(["time", "oscillator_id"]).drop_duplicates(subset=["oscillator_id"], keep="first")

        if len(cycle_df) < 2:
            rows.append(
                {
                    "cycle_index": cycle_idx,
                    "mean_abs_diff_from_ideal_phase_gap": np.nan,
                }
            )
            continue

        times = cycle_df["time"].to_numpy(dtype=np.float64)
        phases = 2.0 * math.pi * ((times - cycle_start) / cycle_length)
        phases = np.mod(phases, 2.0 * math.pi)
        phases.sort()

        diffs = np.diff(phases)
        wrap_diff = (phases[0] + 2.0 * math.pi) - phases[-1]    #最初と最後の差を求めている
        all_diffs = np.concatenate([diffs, np.array([wrap_diff], dtype=np.float64)])

        mean_abs_error = float(np.mean(np.abs(all_diffs - ideal_gap)))
        mean_abs_error_ratio = float(mean_abs_error / ideal_gap)

        rows.append(
            {
                "cycle_index": cycle_idx,
                "mean_abs_diff_from_ideal_phase_gap": mean_abs_error,
                "mean_abs_diff_from_ideal_phase_gap_ratio": mean_abs_error_ratio,
            }
        )

    return pd.DataFrame(rows)


def process_run(run_dir: Path) -> str:
    send_log_path = run_dir / "send_log.csv"
    metadata_path = run_dir / "metadata.csv"
    cycle_data_path = run_dir / "calculated_Cycle_data.csv"
    output_path = run_dir / OUTPUT_FILENAME

    if not send_log_path.exists() or not metadata_path.exists():
        return f"skip: {run_dir} (missing send_log.csv or metadata.csv)"

    if not cycle_data_path.exists():
        ensure_cycle_data_for_run(run_dir)

    if not cycle_data_path.exists():
        return f"skip: {run_dir} (failed to create calculated_Cycle_data.csv)"

    send_df = read_send_log(send_log_path)
    if send_df.empty:
        return f"skip: {run_dir} (empty send_log.csv)"

    tags, num_devices = read_metadata(metadata_path)
    send_df = normalize_oscillator_id_column(send_df, tags)
    send_df = normalize_time_column(send_df, tags)

    cycle_starts, _ = read_calculated_cycle_data(cycle_data_path)

    result_df = compute_mean_abs_gap_error_per_cycle(
        send_df=send_df,
        cycle_starts=cycle_starts,
        num_devices=num_devices,
    )

    result_df.to_csv(output_path, index=False)
    return f"saved: {output_path}"

def ensure_phase_gap_error_for_run(run_dir: Path) -> Path:
    output_path = run_dir / OUTPUT_FILENAME

    if output_path.exists():
        return output_path

    process_run(run_dir)
    return output_path



def main() -> None:
    if not RESULTS_DIR.exists():
        raise FileNotFoundError(f"results folder not found: {RESULTS_DIR}")

    run_dirs = sorted([p for p in RESULTS_DIR.iterdir() if p.is_dir()])

    if not run_dirs:
        print("no run directories found in results")
        return

    max_workers = min(len(run_dirs), (os.cpu_count() or 1))

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_run, run_dir) for run_dir in run_dirs]

        for future in as_completed(futures):
            print(future.result())


if __name__ == "__main__":
    main()