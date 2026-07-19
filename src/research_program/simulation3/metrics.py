"""simulation1/v3 metric pipeline applied to simulation3 directory runs."""
from __future__ import annotations
import math
from pathlib import Path
import numpy as np
import pandas as pd
from research_program.analysis.calculate_phase_gap_error import compute_mean_abs_gap_error_per_cycle
from research_program.analysis.n_sweep_metrics import (bounded_delivery_totals, compute_cycle_delivery_counts, first_consecutive_above, first_consecutive_below, first_ttu_cycle, overall_per_percent, tolerance_rad)
from research_program.io.send_log import add_detection_time_column


def run_metrics(*, run_dir: Path, run_index: int, device_count: int, cycle_time: float,
                duration: float, carrier_sense_duration_ms: float, airtime_ms: float,
                coupling_parameter: float, window_mode: str) -> dict[str, object]:
    send = add_detection_time_column(pd.read_csv(run_dir / "send_log.csv"))
    cs_path = run_dir / "carrier_sense_log.csv"
    cs = pd.read_csv(cs_path) if cs_path.exists() else pd.DataFrame()
    cycles = np.arange(0.0, duration, cycle_time)
    phase = compute_mean_abs_gap_error_per_cycle(send_df=send, cycle_starts=cycles,
        num_devices=device_count, nominal_cycle_time_ms=cycle_time, carrier_sense_df=cs)
    delivery = compute_cycle_delivery_counts(send_df=send, cycle_starts=cycles, device_count=device_count)
    totals = bounded_delivery_totals(delivery)
    epsilon = tolerance_rad(device_count=device_count, nominal_cycle_time_ms=cycle_time,
        carrier_sense_duration_ms=carrier_sense_duration_ms, airtime_ms=airtime_ms)
    occupied = carrier_sense_duration_ms + airtime_ms
    c = phase["cycle_index"].to_numpy(dtype=np.int64)
    max_cycle = first_consecutive_below(cycle_indices=c, values=phase["new_max_abs_dev"].to_numpy(float), threshold=epsilon)
    mean_cycle = first_consecutive_below(cycle_indices=c, values=phase["new_mean_abs_dev"].to_numpy(float), threshold=epsilon)
    gap_cycle = first_consecutive_above(cycle_indices=c, values=phase["min_gap_rad"].to_numpy(float), threshold=2*math.pi*occupied/cycle_time)
    final = phase.tail(10)
    return {"coupling_function":"PCO_D", "coupling_parameter":coupling_parameter, "alpha":coupling_parameter,
        "window_mode":window_mode, "device_count":device_count, "run_index":run_index, "run_id":run_dir.name,
        **totals, "raw_send_packets":int(delivery.actual_packets.sum()),
        "simultaneous_collision_count":int(delivery.simultaneous_collision_count.sum()), "overall_per_percent":overall_per_percent(delivery),
        "ttu_cycle":first_ttu_cycle(delivery), "max_convergence_cycle":max_cycle, "mingap_convergence_cycle":gap_cycle,
        "aux_mean_convergence_cycle":mean_cycle, "max_converged":max_cycle is not None, "mingap_converged":gap_cycle is not None,
        "aux_mean_converged":mean_cycle is not None, "final_10_cycle_new_mean_abs_dev":float(final.new_mean_abs_dev.mean()),
        "final_10_cycle_new_max_abs_dev":float(final.new_max_abs_dev.mean()), "final_10_cycle_min_gap_median":float(final.min_gap_rad.median()),
        "epsilon_tolerance_rad":epsilon, "minimum_collision_free_gap_rad":2*math.pi*occupied/cycle_time,
        "carrier_sense_duration_ms":carrier_sense_duration_ms, "airtime_ms":airtime_ms, "occupied_time_ms":occupied}
