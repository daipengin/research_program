from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
import heapq
import itertools
import os
import time

from research_program.simulation.coupling_functions import CouplingFunction, resolve_coupling_function
from research_program.simulation.oscillator import Oscillator


class OscillatorEventType(Enum):
    SEND_Event = 0
    ASLEEP_Event = 1
    AWAKE_Event = 2
    ADD_Oscillator_Event = 3
    REMOVE_Oscillator_Event = 4


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


class BufferedCsvEventLogger:
    def __init__(
        self,
        send_log_path: str | Path,
        asleep_log_path: str | Path,
        metadata_log_path: str | Path,
    ) -> None:
        self.send_log_path = Path(send_log_path)
        self.asleep_log_path = Path(asleep_log_path)
        self.metadata_log_path = Path(metadata_log_path)

        self.send_rows: List[List[int]] = []
        self.asleep_rows: List[List[int]] = []

    def log_send(self, time_: int, oscillator_id: int, send_count: int) -> None:
        self.send_rows.append([time_, oscillator_id, send_count])

    def log_asleep(self, current_time: int, next_time: int, oscillator_id: int) -> None:
        self.asleep_rows.append([current_time, next_time, oscillator_id])

    def flush_logs(self) -> None:
        self.send_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.asleep_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.metadata_log_path.parent.mkdir(parents=True, exist_ok=True)

        with self.send_log_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["time", "oscillator_id", "send_count"])
            writer.writerows(self.send_rows)

        with self.asleep_log_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["current_time", "next_time", "oscillator_id"])
            writer.writerows(self.asleep_rows)

    def flush_metadata(self, config: RunConfig) -> None:
        ranges_as_text = "|".join(
            f"{start}:{end}:{device_id}"
            for start, end, device_id in config.ranges
        )
        tags_as_text = ";".join(config.tags)

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
                    tags_as_text,
                    ranges_as_text,
                ]
            )

    def flush_all(self, config: RunConfig) -> None:
        self.flush_logs()
        self.flush_metadata(config)


@dataclass(order=True)
class ScheduledEvent:
    time: int
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
        self._event_valid: Dict[int, bool] = {}
        self._events_by_session: Dict[int, set[int]] = {}
        self._session_to_source: Dict[int, int] = {}

        self._coupling_function = resolve_coupling_function(config.coupling_function)

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
        session_id = next(self._session_counter)
        self._events_by_session[session_id] = set()
        self._session_to_source[session_id] = source_id
        return session_id

    def schedule_event(
        self,
        time_: int,
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
        self._event_valid[event_id] = True
        self._events_by_session.setdefault(session_id, set()).add(event_id)
        return event_id

    def invalidate_all_events_for_session(self, session_id: int) -> None:
        event_ids = self._events_by_session.get(session_id, set())
        for event_id in event_ids:
            self._event_valid[event_id] = False
        self._events_by_session[session_id] = set()

    def _discard_event_reference(self, event_id: int, session_id: int) -> None:
        if session_id in self._events_by_session:
            self._events_by_session[session_id].discard(event_id)

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

            if not self._event_valid.get(event.event_id, False):
                continue

            self._discard_event_reference(event.event_id, event.session_id)
            self._event_valid[event.event_id] = False
            self._handle_event(event)

    def _broadcast_receive(self, sender_id: int, current_time: int) -> None:
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

    def _handle_event(self, event: ScheduledEvent) -> None:
        current_time = event.time
        source_id = event.source_id
        session_id = event.session_id
        event_type = event.event_type

        oscillator = self.oscillators.get(source_id)
        if oscillator is None:
            return

        if event_type == OscillatorEventType.REMOVE_Oscillator_Event:
            self.invalidate_all_events_for_session(session_id)
            oscillator.on_remove(current_time)
            return

        if event_type == OscillatorEventType.ADD_Oscillator_Event:
            next_type, next_time = oscillator.on_add(current_time)
            self.schedule_event(next_time, source_id, session_id, next_type)

        elif event_type == OscillatorEventType.SEND_Event:
            if not oscillator.active:
                return

            next_type, next_time = oscillator.on_send(current_time)

            if self.logger is not None:
                self.logger.log_send(
                    time_=current_time,
                    oscillator_id=source_id,
                    send_count=oscillator.send_count,
                )

            self._broadcast_receive(sender_id=source_id, current_time=current_time)
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
    output_root: str | Path = "data/runs",
    verbose: bool = False,
) -> dict:
    output_dir = Path(output_root) / config.run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    logger = BufferedCsvEventLogger(
        send_log_path=output_dir / "send_log.csv",
        asleep_log_path=output_dir / "asleep_log.csv",
        metadata_log_path=output_dir / "metadata.csv",
    )

    scheduler = EventScheduler(config=config, logger=logger, verbose=verbose)
    scheduler.initialize_from_ranges()

    t0 = time.perf_counter()
    scheduler.run()
    elapsed = time.perf_counter() - t0

    logger.flush_all(config)

    return {
        "run_id": config.run_id,
        "output_dir": str(output_dir),
        "elapsed_sec": elapsed,
    }


def run_simulations_in_parallel(
    configs: List[RunConfig],
    output_root: str | Path = "data/runs",
    max_workers: Optional[int] = None,
    verbose: bool = False,
) -> List[dict]:
    results: List[dict] = []

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
