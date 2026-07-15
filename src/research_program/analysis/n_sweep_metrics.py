from __future__ import annotations

import math

import numpy as np
import pandas as pd

from research_program.analysis.calculate_phase_gap_error import (
    assign_cycles_from_reference_windows,
)


def tolerance_rad(
    *,
    device_count: int,
    nominal_cycle_time_ms: float,
    carrier_sense_duration_ms: float,
    airtime_ms: float,
) -> float:
    if device_count < 1:
        raise ValueError("device_count must be positive")
    if nominal_cycle_time_ms <= 0:
        raise ValueError("nominal_cycle_time_ms must be positive")
    occupied_time_ms = float(carrier_sense_duration_ms) + float(airtime_ms)
    return (2.0 * math.pi / device_count) - (
        2.0 * math.pi * occupied_time_ms / nominal_cycle_time_ms
    )


def simultaneous_collision_mask(send_df: pd.DataFrame) -> pd.Series:
    if send_df.empty:
        return pd.Series(False, index=send_df.index, dtype=bool)
    starts = pd.to_numeric(send_df["time"], errors="coerce")
    return starts.notna() & starts.duplicated(keep=False)


def compute_cycle_delivery_counts(
    *,
    send_df: pd.DataFrame,
    cycle_starts: np.ndarray,
    device_count: int,
) -> pd.DataFrame:
    indexed = assign_cycles_from_reference_windows(send_df, cycle_starts)
    cycle_count = len(cycle_starts)
    actual = np.zeros(cycle_count, dtype=np.int64)
    collisions = np.zeros(cycle_count, dtype=np.int64)

    for cycle_index, cycle_df in indexed.groupby("cycle_index", sort=False):
        output_index = int(cycle_index) - 1
        if output_index < 0 or output_index >= cycle_count:
            continue
        actual[output_index] = len(cycle_df)
        collisions[output_index] = int(simultaneous_collision_mask(cycle_df).sum())

    successful = np.maximum(actual - collisions, 0)
    expected = np.full(cycle_count, int(device_count), dtype=np.int64)
    return pd.DataFrame(
        {
            "cycle_index": np.arange(1, cycle_count + 1, dtype=np.int64),
            "expected_packets": expected,
            "actual_packets": actual,
            "simultaneous_collision_count": collisions,
            "successful_packets": successful,
        }
    )


def first_consecutive_below(
    *,
    cycle_indices: np.ndarray,
    values: np.ndarray,
    threshold: float,
    consecutive_cycles: int = 10,
) -> int | None:
    if consecutive_cycles < 1:
        raise ValueError("consecutive_cycles must be positive")
    streak = 0
    previous_cycle: int | None = None
    for cycle_raw, value_raw in zip(cycle_indices, values, strict=True):
        cycle = int(cycle_raw)
        value = float(value_raw)
        is_consecutive = previous_cycle is None or cycle == previous_cycle + 1
        ok = math.isfinite(value) and value < threshold
        streak = streak + 1 if ok and is_consecutive else (1 if ok else 0)
        if streak >= consecutive_cycles:
            return cycle - consecutive_cycles + 1
        previous_cycle = cycle
    return None


def first_consecutive_above(
    *,
    cycle_indices: np.ndarray,
    values: np.ndarray,
    threshold: float,
    consecutive_cycles: int = 10,
) -> int | None:
    """Return the first cycle of a consecutive strict-above-threshold window."""
    if consecutive_cycles < 1:
        raise ValueError("consecutive_cycles must be positive")
    streak = 0
    previous_cycle: int | None = None
    for cycle_raw, value_raw in zip(cycle_indices, values, strict=True):
        cycle = int(cycle_raw)
        value = float(value_raw)
        is_consecutive = previous_cycle is None or cycle == previous_cycle + 1
        ok = math.isfinite(value) and value > threshold
        streak = streak + 1 if ok and is_consecutive else (1 if ok else 0)
        if streak >= consecutive_cycles:
            return cycle - consecutive_cycles + 1
        previous_cycle = cycle
    return None


def censored_convergence_median(
    convergence_cycles: pd.Series,
    converged: pd.Series,
) -> float:
    """Median with non-converged runs treated as +infinity.

    This is intentionally not the nanmedian among converged runs. With right
    censoring, the median is finite only when strictly more than half of runs
    converged; otherwise the reported value is NaN/NULL.
    """
    converged_mask = converged.astype(bool)
    count = len(converged_mask)
    if count == 0 or int(converged_mask.sum()) <= count / 2:
        return math.nan
    finite_cycles = pd.to_numeric(
        convergence_cycles.loc[converged_mask], errors="coerce"
    ).dropna()
    if len(finite_cycles) != int(converged_mask.sum()):
        raise ValueError("converged rows must have a finite convergence cycle")
    censored = np.full(count, np.inf, dtype=np.float64)
    censored[converged_mask.to_numpy()] = finite_cycles.to_numpy(dtype=np.float64)
    median = float(np.median(censored))
    return median if math.isfinite(median) else math.nan


def first_ttu_cycle(
    cycle_counts: pd.DataFrame,
    *,
    window_cycles: int = 10,
    per_threshold_percent: float = 1.0,
) -> int | None:
    if window_cycles < 1 or len(cycle_counts) < window_cycles:
        return None
    expected = cycle_counts["expected_packets"].to_numpy(dtype=np.float64)
    successful = cycle_counts["successful_packets"].to_numpy(dtype=np.float64)
    expected_cumulative = np.concatenate(([0.0], np.cumsum(expected)))
    successful_cumulative = np.concatenate(([0.0], np.cumsum(successful)))
    cycles = cycle_counts["cycle_index"].to_numpy(dtype=np.int64)
    for start in range(len(cycle_counts) - window_cycles + 1):
        end = start + window_cycles
        expected_window = expected_cumulative[end] - expected_cumulative[start]
        successful_window = successful_cumulative[end] - successful_cumulative[start]
        per_percent = 100.0 * (expected_window - successful_window) / expected_window
        if per_percent < per_threshold_percent:
            return int(cycles[end - 1])
    return None


def overall_per_percent(cycle_counts: pd.DataFrame) -> float:
    expected = int(cycle_counts["expected_packets"].sum())
    successful = int(cycle_counts["successful_packets"].sum())
    if expected <= 0:
        return math.nan
    return 100.0 * (expected - successful) / expected
