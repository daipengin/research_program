from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import os
import re
from typing import Optional
import numpy as np
import pandas as pd


RESULTS_DIR = Path(os.environ.get("RESEARCH_PROGRAM_RUNS_DIR", "data/runs"))
REFERENCE_GAP_RATIO = 1.3
OUTPUT_FILENAME = "calculated_Cycle_data.csv"


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

def extract_fixed_reference_id(tags: list[str]) -> Optional[int]:
    for tag in tags:
        m = re.fullmatch(r"fix_ref_(\d+)", tag)
        if m is not None:
            return int(m.group(1))

    return None



def choose_reference_id(send_df: pd.DataFrame,tags: list[str]) -> int:
    first_row = send_df.sort_values(["time", "oscillator_id"]).iloc[0]

    num = extract_fixed_reference_id(tags)

    if num != None:
        return num
    
    #return 9
    
    return int(first_row["oscillator_id"])


def group_send_times_by_id(send_df: pd.DataFrame) -> dict[int, np.ndarray]:
    grouped: dict[int, np.ndarray] = {}
    for osc_id, group in send_df.groupby("oscillator_id"):
        grouped[int(osc_id)] = np.sort(group["time"].to_numpy(dtype=np.float64))
    return grouped


def fill_reference_times(
    reference_times: np.ndarray,
    cycle_time: float,
    gap_ratio: float = 1.3,
) -> tuple[np.ndarray, np.ndarray]:
    if reference_times.size == 0:
        return reference_times.copy(), np.array([], dtype=bool)

    filled = [float(reference_times[0])]
    is_original = [True]
    threshold = cycle_time * gap_ratio

    for current in reference_times[1:]:
        prev = filled[-1]
        gap = current - prev

        if gap >= threshold:
            missing_count = int(round(gap / cycle_time)) - 1
            for k in range(1, missing_count + 1):
                filled.append(prev + cycle_time * k)
                is_original.append(False)

        filled.append(float(current))
        is_original.append(True)

    return np.array(filled, dtype=np.float64), np.array(is_original, dtype=bool)


def build_cycle_starts(send_df: pd.DataFrame, cycle_time: float,tags:list[str]) -> tuple[int, np.ndarray, np.ndarray]:
    grouped = group_send_times_by_id(send_df)
    reference_id = choose_reference_id(send_df,tags)
    raw_reference_times = grouped[reference_id]
    cycle_starts, is_original_cycle = fill_reference_times(
        raw_reference_times,
        cycle_time,
        gap_ratio=REFERENCE_GAP_RATIO,
    )
    return reference_id, cycle_starts, is_original_cycle


def save_calculated_cycle_data(
    output_path: Path,
    reference_id: int,
    cycle_starts: np.ndarray,
    is_original_cycle: np.ndarray,
) -> None:
    df = pd.DataFrame(
        {
            "cycle_index": np.arange(1, len(cycle_starts) + 1, dtype=np.int64),
            "cycle_start_time": cycle_starts,
            "is_original_cycle": is_original_cycle,
            "reference_id": np.full(len(cycle_starts), reference_id, dtype=np.int64),
        }
    )
    df.to_csv(output_path, index=False)


def process_run(run_dir: Path) -> str:
    send_log_path = run_dir / "send_log.csv"
    metadata_path = run_dir / "metadata.csv"
    output_path = run_dir / OUTPUT_FILENAME

    if not send_log_path.exists() or not metadata_path.exists():
        return f"skip: {run_dir} (missing send_log.csv or metadata.csv)"

    send_df = read_send_log(send_log_path)
    if send_df.empty:
        return f"skip: {run_dir} (empty send_log.csv)"

    cycle_time, tags = read_metadata(metadata_path)
    send_df = normalize_oscillator_id_column(send_df, tags)
    send_df = normalize_time_column(send_df, tags)

    reference_id, cycle_starts, is_original_cycle = build_cycle_starts(send_df, cycle_time,tags)

    save_calculated_cycle_data(
        output_path=output_path,
        reference_id=reference_id,
        cycle_starts=cycle_starts,
        is_original_cycle=is_original_cycle,
    )

    return f"saved: {output_path} (reference_id={reference_id}, cycles={len(cycle_starts)})"


def ensure_cycle_data_for_run(run_dir: Path) -> Path:
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
