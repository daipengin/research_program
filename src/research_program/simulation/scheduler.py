from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple, Any
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
import ctypes
import heapq
import itertools
import os
import time

import pandas as pd
import numpy as np

from research_program.io import sqlite_runs
from research_program.simulation.coupling_functions import CouplingFunction, resolve_coupling_function
from research_program.simulation.oscillator import Oscillator


DEFAULT_SIMULATION_OUTPUT_ROOT = Path("data/run/simulation_runs.sqlite")
SQLITE_MIN_EVENT_BATCH_ROWS = 10_000
SQLITE_TARGET_BATCH_MEMORY_FRACTION = 0.25
SQLITE_FALLBACK_TARGET_BATCH_MEMORY_BYTES = 256 * 1024 * 1024
SQLITE_SEND_ROW_MEMORY_BYTES = 512
SQLITE_ASLEEP_ROW_MEMORY_BYTES = 384
SQLITE_CARRIER_SENSE_ROW_MEMORY_BYTES = 768


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _available_memory_bytes() -> int | None:
    if os.name == "nt":
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return int(status.ullAvailPhys)
        except (AttributeError, OSError):
            return None
        return None

    try:
        pages = os.sysconf("SC_AVPHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, OSError, ValueError):
        return None
    if pages <= 0 or page_size <= 0:
        return None
    return int(pages * page_size)


def _sqlite_target_batch_memory_bytes() -> int:
    available_memory = _available_memory_bytes()
    if available_memory is None:
        return SQLITE_FALLBACK_TARGET_BATCH_MEMORY_BYTES
    return max(
        SQLITE_FALLBACK_TARGET_BATCH_MEMORY_BYTES,
        int(available_memory * SQLITE_TARGET_BATCH_MEMORY_FRACTION),
    )


def sqlite_event_batch_size(
    *,
    save_asleep_log: bool = False,
    save_carrier_sense_log: bool = False,
    target_memory_bytes: int | None = None,
) -> int:
    row_memory_bytes = SQLITE_SEND_ROW_MEMORY_BYTES
    if save_asleep_log:
        row_memory_bytes += SQLITE_ASLEEP_ROW_MEMORY_BYTES
    if save_carrier_sense_log:
        row_memory_bytes += SQLITE_CARRIER_SENSE_ROW_MEMORY_BYTES

    memory_budget = (
        _sqlite_target_batch_memory_bytes()
        if target_memory_bytes is None
        else max(1, int(target_memory_bytes))
    )
    estimated_rows = memory_budget // row_memory_bytes
    return max(
        SQLITE_MIN_EVENT_BATCH_ROWS,
        int(estimated_rows),
    )


class OscillatorEventType(Enum):
    SEND_Event = 0
    ASLEEP_Event = 1
    AWAKE_Event = 2
    ADD_Oscillator_Event = 3
    REMOVE_Oscillator_Event = 4
    RECEIVE_Event = 5


@dataclass
class RunConfig:
    run_id: str
    ranges: List[Tuple[int, int, int]]
    coupling_strength:int
    strength_ratio:float
    coupling_function: CouplingFunction
    cycle_time: int
    listening_rate: int
    tags: List[str] = field(default_factory=list)
    start_timing_mode: str = "random"
    random_sampling_method: str = ""
    random_seed: Optional[int] = None
    random_run_index: Optional[int] = None
    start_step: Optional[int] = None
    start_step_count: Optional[int] = None
    simulation_mode: str = "standard"
    carrier_sense_duration_ms: float = 0.0
    transmission_time_ms: float = 0.0
    lora_payload_bytes: int = 16
    lora_spreading_factor: int = 7
    lora_bandwidth_hz: int = 125_000
    lora_coding_rate_denominator: int = 5
    lora_preamble_symbols: int = 8
    lora_explicit_header: bool = True
    lora_crc_enabled: bool = True
    lora_low_data_rate_optimize: Optional[bool] = None
    save_asleep_log: bool = False
    save_carrier_sense_log: bool = False


def per_measurement_enabled(config: RunConfig) -> bool:
    return config.simulation_mode == "per_measurement"


def effective_carrier_sense_duration_ms(config: RunConfig) -> float:
    if not per_measurement_enabled(config):
        return 0.0
    return float(config.carrier_sense_duration_ms)


def effective_transmission_time_ms(config: RunConfig) -> float:
    if not per_measurement_enabled(config):
        return 0.0
    return max(0.0, float(config.transmission_time_ms))


class BufferedCsvEventLogger:
    def __init__(
        self,
        send_log_path: str | Path,
        asleep_log_path: str | Path,
        carrier_sense_log_path: str | Path,
        metadata_log_path: str | Path,
        save_asleep_log: bool = False,
        save_carrier_sense_log: bool = False,
    ) -> None:
        self.send_log_path = Path(send_log_path)
        self.asleep_log_path = Path(asleep_log_path)
        self.carrier_sense_log_path = Path(carrier_sense_log_path)
        self.metadata_log_path = Path(metadata_log_path)
        self.save_asleep_log = save_asleep_log
        self.save_carrier_sense_log = save_carrier_sense_log

        self.send_rows: List[List[Any]] = []
        self.asleep_rows: List[List[Any]] = []
        self.carrier_sense_rows: List[List[Any]] = []

    def log_send(
        self,
        time_: float,
        oscillator_id: int,
        send_count: int,
        transmission_end_time: float,
        transmission_time_ms: float,
    ) -> None:
        self.send_rows.append(
            [
                time_,
                oscillator_id,
                send_count,
                transmission_end_time,
                transmission_time_ms,
            ]
        )

    def log_asleep(self, current_time: float, next_time: float, oscillator_id: int) -> None:
        if not self.save_asleep_log:
            return
        self.asleep_rows.append([current_time, next_time, oscillator_id])

    def log_carrier_sense(
        self,
        time_: float,
        oscillator_id: int,
        action: str,
        carrier_sense_start: float,
        carrier_sense_end: float,
        blocking_oscillator_id: Optional[int],
        blocking_transmission_start: Optional[float],
        blocking_transmission_end: Optional[float],
    ) -> None:
        if not self.save_carrier_sense_log:
            return
        self.carrier_sense_rows.append(
            [
                time_,
                oscillator_id,
                action,
                carrier_sense_start,
                carrier_sense_end,
                "" if blocking_oscillator_id is None else blocking_oscillator_id,
                "" if blocking_transmission_start is None else blocking_transmission_start,
                "" if blocking_transmission_end is None else blocking_transmission_end,
            ]
        )

    def flush_logs(self) -> None:
        self.send_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.metadata_log_path.parent.mkdir(parents=True, exist_ok=True)

        with self.send_log_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "time",
                    "oscillator_id",
                    "send_count",
                    "transmission_end_time",
                    "transmission_time_ms",
                ]
            )
            writer.writerows(self.send_rows)

        if self.save_asleep_log:
            self.asleep_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.asleep_log_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["current_time", "next_time", "oscillator_id"])
                writer.writerows(self.asleep_rows)

        if self.save_carrier_sense_log:
            self.carrier_sense_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.carrier_sense_log_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "time",
                        "oscillator_id",
                        "action",
                        "carrier_sense_start",
                        "carrier_sense_end",
                        "blocking_oscillator_id",
                        "blocking_transmission_start",
                        "blocking_transmission_end",
                    ]
                )
                writer.writerows(self.carrier_sense_rows)

    def flush_metadata(self, config: RunConfig) -> None:
        ranges_as_text = "|".join(
            f"{start}:{end}:{device_id}"
            for start, end, device_id in config.ranges
        )
        selected_start_times_as_text = ";".join(str(start) for start, _, _ in config.ranges)
        tags_as_text = ";".join(config.tags)
        is_random_start = config.start_timing_mode in {
            "random",
            "random_cycle_ms_with_replacement",
        }
        random_start_min = 0 if is_random_start else ""
        random_start_max = (
            int(config.start_step) * int(config.start_step_count)
            if is_random_start and config.start_step is not None and config.start_step_count is not None
            else ""
        )
        random_start_candidate_count = (
            int(config.start_step_count) + 1
            if is_random_start and config.start_step_count is not None
            else ""
        )

        with self.metadata_log_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "run_id",
                    "coupling_strength",
                    "strengrh_ratio",
                    "coupling_function",
                    "cycle_time",
                    "listening_rate",
                    "start_timing_mode",
                    "random_sampling_method",
                    "random_seed",
                    "random_run_index",
                    "random_start_min",
                    "random_start_max",
                    "start_step",
                    "start_step_count",
                    "random_start_candidate_count",
                    "selected_start_times",
                    "simulation_mode",
                    "save_asleep_log",
                    "save_carrier_sense_log",
                    "carrier_sense_duration_ms",
                    "transmission_time_ms",
                    "lora_payload_bytes",
                    "lora_spreading_factor",
                    "lora_bandwidth_hz",
                    "lora_coding_rate_denominator",
                    "lora_preamble_symbols",
                    "lora_explicit_header",
                    "lora_crc_enabled",
                    "lora_low_data_rate_optimize",
                    "tags",
                    "ranges",
                ]
            )
            writer.writerow(
                [
                    config.run_id,
                    config.coupling_strength,
                    config.strength_ratio,
                    config.coupling_function.value,
                    config.cycle_time,
                    config.listening_rate,
                    config.start_timing_mode,
                    config.random_sampling_method if is_random_start else "",
                    "" if config.random_seed is None else config.random_seed,
                    "" if config.random_run_index is None else config.random_run_index,
                    random_start_min,
                    random_start_max,
                    "" if config.start_step is None else config.start_step,
                    "" if config.start_step_count is None else config.start_step_count,
                    random_start_candidate_count,
                    selected_start_times_as_text,
                    config.simulation_mode,
                    config.save_asleep_log,
                    config.save_carrier_sense_log,
                    effective_carrier_sense_duration_ms(config),
                    config.transmission_time_ms,
                    config.lora_payload_bytes,
                    config.lora_spreading_factor,
                    config.lora_bandwidth_hz,
                    config.lora_coding_rate_denominator,
                    config.lora_preamble_symbols,
                    config.lora_explicit_header,
                    config.lora_crc_enabled,
                    "" if config.lora_low_data_rate_optimize is None else config.lora_low_data_rate_optimize,
                    tags_as_text,
                    ranges_as_text,
                ]
            )

    def flush_all(self, config: RunConfig) -> None:
        self.flush_logs()
        self.flush_metadata(config)

    def row_counts(self) -> dict[str, int]:
        send_log_rows = len(self.send_rows)
        asleep_log_rows = len(self.asleep_rows)
        carrier_sense_log_rows = len(self.carrier_sense_rows)
        total_event_log_rows = send_log_rows + asleep_log_rows + carrier_sense_log_rows
        metadata_rows = 1
        return {
            "send_log_rows": send_log_rows,
            "asleep_log_rows": asleep_log_rows,
            "carrier_sense_log_rows": carrier_sense_log_rows,
            "metadata_rows": metadata_rows,
            "total_event_log_rows": total_event_log_rows,
            "total_csv_data_rows": total_event_log_rows + metadata_rows,
        }

    def output_file_sizes(self) -> dict[str, int]:
        def file_size(path: Path, *, enabled: bool = True) -> int:
            if not enabled or not path.exists():
                return 0
            return path.stat().st_size

        send_log_bytes = file_size(self.send_log_path)
        asleep_log_bytes = file_size(self.asleep_log_path, enabled=self.save_asleep_log)
        carrier_sense_log_bytes = file_size(
            self.carrier_sense_log_path,
            enabled=self.save_carrier_sense_log,
        )
        metadata_bytes = file_size(self.metadata_log_path)
        return {
            "send_log_bytes": send_log_bytes,
            "asleep_log_bytes": asleep_log_bytes,
            "carrier_sense_log_bytes": carrier_sense_log_bytes,
            "metadata_bytes": metadata_bytes,
            "total_output_bytes": (
                send_log_bytes
                + asleep_log_bytes
                + carrier_sense_log_bytes
                + metadata_bytes
            ),
        }


class SQLiteEventLogger:
    def __init__(
        self,
        sqlite_path: str | Path,
        run_id: str,
        save_asleep_log: bool = False,
        save_carrier_sense_log: bool = False,
        batch_size: int | None = None,
    ) -> None:
        self.sqlite_path = Path(sqlite_path)
        self.run_id = run_id
        self.save_asleep_log = save_asleep_log
        self.save_carrier_sense_log = save_carrier_sense_log
        self.target_batch_memory_bytes = _sqlite_target_batch_memory_bytes()
        self.batch_size = (
            sqlite_event_batch_size(
                save_asleep_log=save_asleep_log,
                save_carrier_sense_log=save_carrier_sense_log,
                target_memory_bytes=self.target_batch_memory_bytes,
            )
            if batch_size is None
            else max(1, int(batch_size))
        )

        self.conn = sqlite_runs.connect(self.sqlite_path)
        sqlite_runs.initialize(self.conn)
        sqlite_runs.delete_run(self.conn, self.run_id)

        self.send_buffer: list[tuple[Any, ...]] = []
        self.asleep_buffer: list[tuple[Any, ...]] = []
        self.carrier_sense_buffer: list[tuple[Any, ...]] = []
        self.send_log_rows = 0
        self.asleep_log_rows = 0
        self.carrier_sense_log_rows = 0

    def log_send(
        self,
        time_: float,
        oscillator_id: int,
        send_count: int,
        transmission_end_time: float,
        transmission_time_ms: float,
    ) -> None:
        self.send_buffer.append(
            (
                self.run_id,
                time_,
                str(oscillator_id),
                send_count,
                transmission_end_time,
                transmission_time_ms,
            )
        )
        self.send_log_rows += 1
        if len(self.send_buffer) >= self.batch_size:
            self._flush_send_buffer()

    def log_asleep(self, current_time: float, next_time: float, oscillator_id: int) -> None:
        if not self.save_asleep_log:
            return
        self.asleep_buffer.append((self.run_id, current_time, next_time, str(oscillator_id)))
        self.asleep_log_rows += 1
        if len(self.asleep_buffer) >= self.batch_size:
            self._flush_asleep_buffer()

    def log_carrier_sense(
        self,
        time_: float,
        oscillator_id: int,
        action: str,
        carrier_sense_start: float,
        carrier_sense_end: float,
        blocking_oscillator_id: Optional[int],
        blocking_transmission_start: Optional[float],
        blocking_transmission_end: Optional[float],
    ) -> None:
        if not self.save_carrier_sense_log:
            return
        self.carrier_sense_buffer.append(
            (
                self.run_id,
                time_,
                str(oscillator_id),
                action,
                carrier_sense_start,
                carrier_sense_end,
                None if blocking_oscillator_id is None else str(blocking_oscillator_id),
                blocking_transmission_start,
                blocking_transmission_end,
            )
        )
        self.carrier_sense_log_rows += 1
        if len(self.carrier_sense_buffer) >= self.batch_size:
            self._flush_carrier_sense_buffer()

    def _flush_send_buffer(self) -> None:
        if not self.send_buffer:
            return
        sqlite_runs.insert_rows(
            self.conn,
            "send_log",
            [
                "run_id",
                "time",
                "oscillator_id",
                "send_count",
                "transmission_end_time",
                "transmission_time_ms",
            ],
            self.send_buffer,
        )
        self.send_buffer.clear()

    def _flush_asleep_buffer(self) -> None:
        if not self.asleep_buffer:
            return
        sqlite_runs.insert_rows(
            self.conn,
            "asleep_log",
            ["run_id", "current_time", "next_time", "oscillator_id"],
            self.asleep_buffer,
        )
        self.asleep_buffer.clear()

    def _flush_carrier_sense_buffer(self) -> None:
        if not self.carrier_sense_buffer:
            return
        sqlite_runs.insert_rows(
            self.conn,
            "carrier_sense_log",
            [
                "run_id",
                "time",
                "oscillator_id",
                "action",
                "carrier_sense_start",
                "carrier_sense_end",
                "blocking_oscillator_id",
                "blocking_transmission_start",
                "blocking_transmission_end",
            ],
            self.carrier_sense_buffer,
        )
        self.carrier_sense_buffer.clear()

    def _send_buffer_frame(self) -> pd.DataFrame | None:
        if not self.send_buffer or len(self.send_buffer) != self.send_log_rows:
            return None
        return pd.DataFrame(
            self.send_buffer,
            columns=[
                "run_id",
                "time",
                "oscillator_id",
                "send_count",
                "transmission_end_time",
                "transmission_time_ms",
            ],
        ).drop(columns=["run_id"])

    def flush_logs(self) -> None:
        self._flush_send_buffer()
        self._flush_asleep_buffer()
        self._flush_carrier_sense_buffer()

    def flush_metadata(self, config: RunConfig) -> None:
        sqlite_runs.insert_run_metadata(
            self.conn,
            sqlite_runs.run_metadata_row(config),
        )

    def flush_derived_data(self, config: RunConfig, send_df: pd.DataFrame | None = None) -> None:
        if self.send_log_rows == 0:
            return

        from research_program.analysis import calculate_cycle_data
        from research_program.analysis import calculate_phase_gap_error

        if send_df is None:
            send_df = pd.read_sql_query(
                """
                SELECT time, oscillator_id, send_count, transmission_end_time, transmission_time_ms
                FROM send_log
                WHERE run_id = ?
                ORDER BY time, oscillator_id
                """,
                self.conn,
                params=(self.run_id,),
            )
        else:
            send_df = send_df.sort_values(["time", "oscillator_id"], kind="stable").reset_index(drop=True)
        if send_df.empty:
            return

        tags = list(config.tags)
        cycle_time = float(config.cycle_time)
        if "sec" in tags:
            cycle_time *= 1000.0

        normalized_send_df = calculate_cycle_data.normalize_oscillator_id_column(send_df, tags)
        normalized_send_df = calculate_cycle_data.normalize_time_column(normalized_send_df, tags)
        reference_id, cycle_starts, is_original_cycle = calculate_cycle_data.build_cycle_starts(
            normalized_send_df,
            cycle_time,
            tags,
        )
        cycle_df = pd.DataFrame(
            {
                "cycle_index": range(1, len(cycle_starts) + 1),
                "cycle_start_time": cycle_starts,
                "is_original_cycle": [int(value) for value in is_original_cycle],
                "reference_id": [int(reference_id)] * len(cycle_starts),
            }
        )
        sqlite_runs.replace_dataframe(
            self.conn,
            "calculated_cycle_data",
            self.run_id,
            cycle_df,
        )

        try:
            num_devices = calculate_phase_gap_error.extract_device_count_from_tags(tags)
        except ValueError:
            return

        phase_df = calculate_phase_gap_error.compute_mean_abs_gap_error_per_cycle(
            send_df=normalized_send_df,
            cycle_starts=cycle_starts,
            num_devices=num_devices,
        )
        new_metric_columns = [
            "new_mean_abs_dev",
            "new_max_abs_dev",
            "min_gap_rad",
            "observed_device_count",
            "expected_device_count",
            "has_all_device_sends",
            "skipped_device_count",
            "simultaneous_collision_count",
        ]
        range_durations = [float(end) - float(start) for start, end, _ in config.ranges]
        expected_cycle_count = (
            int(round(min(range_durations) / cycle_time)) if range_durations else 0
        )
        if expected_cycle_count > 0 and len(cycle_starts) > 0:
            nominal_cycle_starts = float(cycle_starts[0]) + (
                np.arange(expected_cycle_count, dtype=np.float64) * cycle_time
            )
            nominal_phase_df = calculate_phase_gap_error.compute_mean_abs_gap_error_per_cycle(
                send_df=normalized_send_df,
                cycle_starts=nominal_cycle_starts,
                num_devices=num_devices,
                nominal_cycle_time_ms=cycle_time,
            )
            phase_df = phase_df.drop(columns=new_metric_columns).merge(
                nominal_phase_df[["cycle_index", *new_metric_columns]],
                on="cycle_index",
                how="outer",
                sort=True,
            )
        sqlite_runs.replace_dataframe(
            self.conn,
            "phase_gap_error",
            self.run_id,
            phase_df,
        )

    def flush_all(self, config: RunConfig) -> None:
        send_df = self._send_buffer_frame()
        self.flush_logs()
        self.flush_metadata(config)
        self.flush_derived_data(config, send_df=send_df)

    def row_counts(self) -> dict[str, int]:
        total_event_log_rows = self.send_log_rows + self.asleep_log_rows + self.carrier_sense_log_rows
        metadata_rows = 1
        return {
            "send_log_rows": self.send_log_rows,
            "asleep_log_rows": self.asleep_log_rows,
            "carrier_sense_log_rows": self.carrier_sense_log_rows,
            "metadata_rows": metadata_rows,
            "total_event_log_rows": total_event_log_rows,
            "total_csv_data_rows": total_event_log_rows + metadata_rows,
        }

    def output_file_sizes(self) -> dict[str, int]:
        paths = [
            self.sqlite_path,
            self.sqlite_path.with_name(f"{self.sqlite_path.name}-wal"),
            self.sqlite_path.with_name(f"{self.sqlite_path.name}-shm"),
        ]
        sqlite_store_bytes = sum(path.stat().st_size for path in paths if path.exists())
        return {
            "send_log_bytes": 0,
            "asleep_log_bytes": 0,
            "carrier_sense_log_bytes": 0,
            "metadata_bytes": 0,
            "sqlite_batch_size": self.batch_size,
            "sqlite_batch_target_memory_bytes": self.target_batch_memory_bytes,
            "sqlite_store_bytes": sqlite_store_bytes,
            "total_output_bytes": sqlite_store_bytes,
        }

    def close(self) -> None:
        self.conn.close()


@dataclass(order=True)
class ScheduledEvent:
    time: float
    insertion_order: int
    event_id: int = field(compare=False)
    event_type: OscillatorEventType = field(compare=False)
    source_id: int = field(compare=False)
    session_id: int = field(compare=False)


class EventScheduler:
    def __init__(
        self,
        config: RunConfig,
        logger: Optional[BufferedCsvEventLogger] = None,
        verbose: bool = False,
    ) -> None:
        self.config = config
        self.logger = logger
        self.verbose = verbose

        self._queue: List[ScheduledEvent] = []
        self._event_counter = itertools.count()
        self._order_counter = itertools.count()
        self._session_counter = itertools.count()

        self.oscillators: Dict[int, Oscillator] = {}
        self._transmission_intervals: List[Tuple[float, float, int]] = []

        self._coupling_function = resolve_coupling_function(config.coupling_function)
        self._per_measurement_enabled = per_measurement_enabled(config)
        self._transmission_time_ms = effective_transmission_time_ms(config)
        self._carrier_sense_duration_ms = effective_carrier_sense_duration_ms(config)

    def get_or_create_oscillator(self, source_id: int) -> Oscillator:
        if source_id not in self.oscillators:
            self.oscillators[source_id] = Oscillator(
                source_id=source_id,
                coupling_strength=self.config.coupling_strength,
                strength_ratio=self.config.strength_ratio,
                cycle_time=self.config.cycle_time,
                listening_rate=self.config.listening_rate,
                coupling_function=self._coupling_function,
                event_type_enum=OscillatorEventType,
            )
        return self.oscillators[source_id]

    def create_session(self, source_id: int) -> int:
        return next(self._session_counter)

    def schedule_event(
        self,
        time_: float,
        source_id: int,
        session_id: int,
        event_type: OscillatorEventType,
    ) -> int:
        event_id = next(self._event_counter)
        insertion_order = next(self._order_counter)

        event = ScheduledEvent(
            time=time_,
            insertion_order=insertion_order,
            event_id=event_id,
            event_type=event_type,
            source_id=source_id,
            session_id=session_id,
        )

        heapq.heappush(self._queue, event)
        return event_id

    def invalidate_all_events_for_session(self, session_id: int) -> None:
        return

    def _discard_event_reference(self, event_id: int, session_id: int) -> None:
        return

    def initialize_from_ranges(self) -> None:
        for start_time, end_time, source_id in self.config.ranges:
            self.get_or_create_oscillator(source_id)
            session_id = self.create_session(source_id)

            self.schedule_event(
                time_=start_time,
                source_id=source_id,
                session_id=session_id,
                event_type=OscillatorEventType.ADD_Oscillator_Event,
            )
            self.schedule_event(
                time_=end_time,
                source_id=source_id,
                session_id=session_id,
                event_type=OscillatorEventType.REMOVE_Oscillator_Event,
            )

    def run(self) -> None:
        while self._queue:
            event = heapq.heappop(self._queue)
            self._handle_event(event)

    def _broadcast_receive(self, sender_id: int, current_time: float) -> None:
        sender = self.oscillators[sender_id]

        for receiver_id, receiver in self.oscillators.items():
            if receiver_id == sender_id:
                continue
            if not receiver.active:
                continue

            receiver.on_receive(
                sender_id=sender_id,
                sender_phase=sender.phase,
                current_time=current_time,
            )

    def _carrier_sense_window(self, oscillator: Oscillator, current_time: float) -> Tuple[float, float]:
        duration = self._carrier_sense_duration_ms
        carrier_sense_start = current_time - duration
        if oscillator.current_awake_start_time is not None:
            carrier_sense_start = max(float(oscillator.current_awake_start_time), carrier_sense_start)
        return carrier_sense_start, current_time

    def _find_blocking_transmission(
        self,
        source_id: int,
        carrier_sense_start: float,
        carrier_sense_end: float,
    ) -> Optional[Tuple[float, float, int]]:
        self._transmission_intervals = [
            interval
            for interval in self._transmission_intervals
            if interval[1] > carrier_sense_start
        ]

        if carrier_sense_start >= carrier_sense_end:
            return None

        for transmission_start, transmission_end, sender_id in self._transmission_intervals:
            if sender_id == source_id:
                continue
            if transmission_start < carrier_sense_end and transmission_end > carrier_sense_start:
                return transmission_start, transmission_end, sender_id
        return None

    def _record_transmission_interval(self, source_id: int, current_time: float) -> Tuple[float, float]:
        transmission_time = self._transmission_time_ms
        transmission_start = current_time
        transmission_end = current_time + transmission_time
        if transmission_time > 0:
            self._transmission_intervals.append((transmission_start, transmission_end, source_id))
        return transmission_start, transmission_end

    def _handle_event(self, event: ScheduledEvent) -> None:
        current_time = event.time
        source_id = event.source_id
        session_id = event.session_id
        event_type = event.event_type

        oscillator = self.oscillators.get(source_id)
        if oscillator is None:
            return

        if event_type == OscillatorEventType.REMOVE_Oscillator_Event:
            oscillator.on_remove(current_time)
            return

        if event_type == OscillatorEventType.ADD_Oscillator_Event:
            next_type, next_time = oscillator.on_add(current_time)
            self.schedule_event(next_time, source_id, session_id, next_type)

        elif event_type == OscillatorEventType.RECEIVE_Event:
            if not oscillator.active:
                return
            self._broadcast_receive(sender_id=source_id, current_time=current_time)

        elif event_type == OscillatorEventType.SEND_Event:
            if not oscillator.active:
                return

            carrier_sense_start, carrier_sense_end = self._carrier_sense_window(
                oscillator=oscillator,
                current_time=float(current_time),
            )
            blocking_transmission = None
            if self._per_measurement_enabled:
                blocking_transmission = self._find_blocking_transmission(
                    source_id=source_id,
                    carrier_sense_start=carrier_sense_start,
                    carrier_sense_end=carrier_sense_end,
                )

            if blocking_transmission is not None:
                would_be_transmission_end = (
                    float(current_time) + self._transmission_time_ms
                )
                next_type, next_time = oscillator.on_skip_send(
                    current_time,
                    phase_reference_time=would_be_transmission_end,
                )
                blocking_start, blocking_end, blocking_source_id = blocking_transmission

                if self.logger is not None:
                    self.logger.log_carrier_sense(
                        time_=current_time,
                        oscillator_id=source_id,
                        action="skip_busy",
                        carrier_sense_start=carrier_sense_start,
                        carrier_sense_end=carrier_sense_end,
                        blocking_oscillator_id=blocking_source_id,
                        blocking_transmission_start=blocking_start,
                        blocking_transmission_end=blocking_end,
                    )

                self.schedule_event(next_time, source_id, session_id, next_type)
                return

            phase_reference_time = float(current_time)
            if self._per_measurement_enabled:
                phase_reference_time += self._transmission_time_ms

            next_type, next_time = oscillator.on_send(
                current_time,
                phase_reference_time=phase_reference_time,
            )
            _, transmission_end = self._record_transmission_interval(
                source_id=source_id,
                current_time=float(current_time),
            )

            if self.logger is not None:
                self.logger.log_send(
                    time_=current_time,
                    oscillator_id=source_id,
                    send_count=oscillator.send_count,
                    transmission_end_time=transmission_end,
                    transmission_time_ms=self._transmission_time_ms,
                )
                if self._per_measurement_enabled:
                    self.logger.log_carrier_sense(
                        time_=current_time,
                        oscillator_id=source_id,
                        action="send_clear",
                        carrier_sense_start=carrier_sense_start,
                        carrier_sense_end=carrier_sense_end,
                        blocking_oscillator_id=None,
                        blocking_transmission_start=None,
                        blocking_transmission_end=None,
                    )

            if transmission_end <= float(current_time):
                self._broadcast_receive(sender_id=source_id, current_time=current_time)
            else:
                self.schedule_event(
                    time_=transmission_end,
                    source_id=source_id,
                    session_id=session_id,
                    event_type=OscillatorEventType.RECEIVE_Event,
                )
            self.schedule_event(next_time, source_id, session_id, next_type)

        elif event_type == OscillatorEventType.ASLEEP_Event:
            if not oscillator.active:
                return

            next_type, next_time = oscillator.on_asleep(current_time)

            if self.logger is not None:
                self.logger.log_asleep(
                    current_time=current_time,
                    next_time=next_time,
                    oscillator_id=source_id,
                )

            self.schedule_event(next_time, source_id, session_id, next_type)

        elif event_type == OscillatorEventType.AWAKE_Event:
            if not oscillator.active:
                return

            next_type, next_time = oscillator.on_awake(current_time)
            self.schedule_event(next_time, source_id, session_id, next_type)

        else:
            raise ValueError(f"Unknown event type: {event_type}")


def run_simulation_case(
    config: RunConfig,
    output_root: str | Path = DEFAULT_SIMULATION_OUTPUT_ROOT,
    verbose: bool = False,
) -> dict:
    output_root_path = Path(output_root)
    storage_kind = "sqlite" if sqlite_runs.is_sqlite_run_store(output_root_path) else "directory"
    if storage_kind == "sqlite":
        output_root_path.parent.mkdir(parents=True, exist_ok=True)
        output_dir = output_root_path
        logger = SQLiteEventLogger(
            sqlite_path=output_root_path,
            run_id=config.run_id,
            save_asleep_log=config.save_asleep_log,
            save_carrier_sense_log=config.save_carrier_sense_log,
        )
    else:
        output_dir = output_root_path / config.run_id
        output_dir.mkdir(parents=True, exist_ok=True)

        logger = BufferedCsvEventLogger(
            send_log_path=output_dir / "send_log.csv",
            asleep_log_path=output_dir / "asleep_log.csv",
            carrier_sense_log_path=output_dir / "carrier_sense_log.csv",
            metadata_log_path=output_dir / "metadata.csv",
            save_asleep_log=config.save_asleep_log,
            save_carrier_sense_log=config.save_carrier_sense_log,
        )

    scheduler = EventScheduler(config=config, logger=logger, verbose=verbose)
    scheduler.initialize_from_ranges()

    t0 = time.perf_counter()
    scheduler.run()
    simulation_elapsed = time.perf_counter() - t0

    row_counts = logger.row_counts()
    save_t0 = time.perf_counter()
    logger.flush_all(config)
    save_elapsed = time.perf_counter() - save_t0
    if hasattr(logger, "close"):
        logger.close()
    file_sizes = logger.output_file_sizes()

    return {
        "run_id": config.run_id,
        "random_seed": config.random_seed,
        "random_run_index": config.random_run_index,
        "selected_start_times": ";".join(str(start) for start, _, _ in config.ranges),
        "output_dir": str(output_dir),
        "storage_kind": storage_kind,
        "elapsed_sec": simulation_elapsed,
        "simulation_elapsed_sec": simulation_elapsed,
        "save_elapsed_sec": save_elapsed,
        "total_elapsed_sec": simulation_elapsed + save_elapsed,
        **row_counts,
        **file_sizes,
    }


def run_simulations_in_parallel(
    configs: List[RunConfig],
    output_root: str | Path = DEFAULT_SIMULATION_OUTPUT_ROOT,
    max_workers: Optional[int] = None,
    verbose: bool = False,
    progress_callback: Callable[[int, int, dict], None] | None = None,
) -> List[dict]:
    results: List[dict] = []
    max_workers = default_max_workers(len(configs)) if max_workers is None or max_workers <= 0 else max_workers
    total_count = len(configs)

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        pending = {}
        config_iter = iter(configs)

        # 最初に max_workers 個だけ投入
        for _ in range(max_workers):
            try:
                config = next(config_iter)
            except StopIteration:
                break
            future = executor.submit(run_simulation_case, config, output_root, verbose)
            pending[future] = config

        while pending:
            for future in as_completed(list(pending.keys()), timeout=None):
                config = pending.pop(future)
                result = future.result()
                results.append(result)
                if progress_callback is not None:
                    progress_callback(len(results), total_count, result)

                try:
                    next_config = next(config_iter)
                    next_future = executor.submit(run_simulation_case, next_config, output_root, verbose)
                    pending[next_future] = next_config
                except StopIteration:
                    pass

                break

    results.sort(key=lambda x: x["run_id"])
    return results


def default_max_workers(num_cases: int) -> int:
    return min(os.cpu_count() or 1, num_cases)
