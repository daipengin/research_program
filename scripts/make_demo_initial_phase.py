from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from research_program.simulation.runner import SimulationRequest, run_simulation_request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = PROJECT_ROOT / "results" / "demo_initial_phase"
LOCAL_ROOT = PROJECT_ROOT / "results_local" / "demo_initial_phase"
RAW_SQLITE = LOCAL_ROOT / "raw_runs.sqlite"

DEVICE_COUNT = 50
CYCLE_TIME_MS = 10_000
DURATION_MS = 1_800_000
CYCLE_COUNT = DURATION_MS // CYCLE_TIME_MS
LISTENING_RATE = 25
STRENGTH_RATIO = -0.0001
RUNS_PER_CONDITION = 3

FUNCTIONS = [
    ("kuramoto", "KURAMOTO", 571),
    ("newsin", "NewSIN", 24),
    ("linear_4", "LINEAR_4", 9),
    ("linear", "LINEAR", 10),
]

INITIAL_CONDITIONS = {
    "uniform_1ms": tuple(range(DEVICE_COUNT)),
    "four_clusters": (
        tuple(range(0, 13))
        + tuple(range(2500, 2513))
        + tuple(range(5000, 5012))
        + tuple(range(7500, 7512))
    ),
}


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    LOCAL_ROOT.mkdir(parents=True, exist_ok=True)

    for function_slug, coupling_function, coupling_strength in FUNCTIONS:
        for condition_slug, start_times in INITIAL_CONDITIONS.items():
            results = run_demo_condition(
                coupling_function=coupling_function,
                coupling_strength=coupling_strength,
                start_times=start_times,
                seed=seed_for(function_slug, condition_slug),
            )
            for run_number, result in enumerate(results, start=1):
                run_id = str(result["run_id"])
                phase_df = phase_differences_for_run(
                    sqlite_path=RAW_SQLITE,
                    run_id=run_id,
                    function_slug=function_slug,
                    coupling_function=coupling_function,
                    coupling_strength=coupling_strength,
                    initial_condition=condition_slug,
                    run_number=run_number,
                )
                output_path = OUTPUT_ROOT / f"{function_slug}_{condition_slug}_run{run_number}.csv"
                phase_df.to_csv(output_path, index=False)
                print(output_path)
    return 0


def run_demo_condition(
    *,
    coupling_function: str,
    coupling_strength: int,
    start_times: tuple[int, ...],
    seed: int,
) -> list[dict]:
    request = SimulationRequest(
        num_runs=RUNS_PER_CONDITION,
        seed=seed,
        coupling_function=coupling_function,
        coupling_strength=coupling_strength,
        strength_ratio=STRENGTH_RATIO,
        cycle_time=CYCLE_TIME_MS,
        listening_rate=LISTENING_RATE,
        device_count=DEVICE_COUNT,
        duration=DURATION_MS,
        start_step_count=CYCLE_TIME_MS,
        start_step=1,
        tags=("demo_initial_phase",),
        output_root=RAW_SQLITE,
        max_workers=RUNS_PER_CONDITION,
        start_timing_mode="random_cycle_ms_with_replacement",
        initial_start_times_by_run=tuple(start_times for _ in range(RUNS_PER_CONDITION)),
        simulation_mode="per_measurement",
        carrier_sense_duration_ms=5.0,
        lora_payload_bytes=50,
        lora_spreading_factor=7,
        lora_bandwidth_hz=500_000,
        lora_coding_rate_denominator=5,
        lora_preamble_symbols=8,
        lora_explicit_header=True,
        lora_crc_enabled=True,
        lora_low_data_rate_optimize=None,
        save_asleep_log=False,
        save_carrier_sense_log=True,
    )
    return run_simulation_request(request)


def phase_differences_for_run(
    *,
    sqlite_path: Path,
    run_id: str,
    function_slug: str,
    coupling_function: str,
    coupling_strength: int,
    initial_condition: str,
    run_number: int,
) -> pd.DataFrame:
    event_df = read_timing_events(sqlite_path, run_id)
    first_event = first_event_by_cycle_and_device(event_df)

    index = pd.MultiIndex.from_product(
        [range(1, CYCLE_COUNT + 1), range(DEVICE_COUNT)],
        names=["cycle_index", "device_id"],
    )
    out = index.to_frame(index=False)
    out = out.merge(first_event, how="left", on=["cycle_index", "device_id"])
    out["reference_device_id"] = 0

    references = (
        first_event[first_event["device_id"] == 0][
            ["cycle_index", "device_event_time_ms", "device_event_type"]
        ].rename(
            columns={
                "device_event_time_ms": "reference_event_time_ms",
                "device_event_type": "reference_event_type",
            }
        )
    )
    out = out.merge(references, how="left", on="cycle_index")
    out["time_diff_ms"] = wrap_time_diff(
        out["device_event_time_ms"].to_numpy(dtype=float) - out["reference_event_time_ms"].to_numpy(dtype=float),
        CYCLE_TIME_MS,
    )
    out["phase_diff_rad"] = out["time_diff_ms"] * (2.0 * math.pi / CYCLE_TIME_MS)
    out["has_device_event"] = out["device_event_time_ms"].notna()
    out["has_reference_event"] = out["reference_event_time_ms"].notna()

    out.insert(0, "run_id", run_id)
    out.insert(0, "run_number", run_number)
    out.insert(0, "initial_condition", initial_condition)
    out.insert(0, "coupling_strength", coupling_strength)
    out.insert(0, "coupling_function", coupling_function)
    out.insert(0, "function_slug", function_slug)
    out["cycle_start_ms"] = (out["cycle_index"] - 1) * CYCLE_TIME_MS
    return out[
        [
            "function_slug",
            "coupling_function",
            "coupling_strength",
            "initial_condition",
            "run_number",
            "run_id",
            "cycle_index",
            "cycle_start_ms",
            "device_id",
            "reference_device_id",
            "reference_event_time_ms",
            "reference_event_type",
            "device_event_time_ms",
            "device_event_type",
            "time_diff_ms",
            "phase_diff_rad",
            "has_reference_event",
            "has_device_event",
        ]
    ]


def read_timing_events(sqlite_path: Path, run_id: str) -> pd.DataFrame:
    with sqlite3.connect(sqlite_path) as conn:
        send_df = pd.read_sql_query(
            """
            SELECT time, oscillator_id, 'sent' AS event_type
            FROM send_log
            WHERE run_id = ?
            ORDER BY time, oscillator_id
            """,
            conn,
            params=(run_id,),
        )
        skip_df = pd.read_sql_query(
            """
            SELECT time, oscillator_id, 'skipped' AS event_type
            FROM carrier_sense_log
            WHERE run_id = ?
              AND action LIKE 'skip%'
            ORDER BY time, oscillator_id
            """,
            conn,
            params=(run_id,),
        )
    return pd.concat([send_df, skip_df], ignore_index=True)


def first_event_by_cycle_and_device(event_df: pd.DataFrame) -> pd.DataFrame:
    if event_df.empty:
        return pd.DataFrame(
            columns=["cycle_index", "device_id", "device_event_time_ms", "device_event_type"]
        )
    df = event_df.copy()
    df["device_event_time_ms"] = pd.to_numeric(df["time"], errors="coerce")
    df["device_id"] = pd.to_numeric(df["oscillator_id"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["device_event_time_ms", "device_id"])
    df["device_id"] = df["device_id"].astype(int)
    df["cycle_index"] = np.floor(df["device_event_time_ms"] / CYCLE_TIME_MS).astype(int) + 1
    df = df[(df["cycle_index"] >= 1) & (df["cycle_index"] <= CYCLE_COUNT)]
    first = (
        df.sort_values(["cycle_index", "device_id", "device_event_time_ms"])
        .drop_duplicates(subset=["cycle_index", "device_id"], keep="first")
        [["cycle_index", "device_id", "device_event_time_ms", "event_type"]]
        .rename(columns={"event_type": "device_event_type"})
        .reset_index(drop=True)
    )
    return first


def wrap_time_diff(diff_ms: np.ndarray, cycle_time_ms: float) -> np.ndarray:
    return ((diff_ms + cycle_time_ms / 2.0) % cycle_time_ms) - cycle_time_ms / 2.0


def seed_for(*parts: Iterable[str] | str) -> int:
    text = ":".join(str(part) for part in parts)
    return 20_260_711 + sum((index + 1) * ord(char) for index, char in enumerate(text))


if __name__ == "__main__":
    raise SystemExit(main())
