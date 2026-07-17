from __future__ import annotations

import heapq
import itertools
from collections.abc import Mapping

from research_program.simulation2.algorithms.base import OscillatorAlgorithm
from research_program.simulation2.algorithms.registry import resolve_algorithm
from research_program.simulation2.config import Simulation2Config
from research_program.simulation2.events import OscillatorEventType, ScheduledEvent
from research_program.simulation2.medium import (
    BroadcastMedium,
    CarrierSenseResult,
    Transmission,
)
from research_program.simulation2.oscillator import OscillatorState


class EventScheduler:
    """Priority-queue runtime shared by simulation2 algorithms."""

    def __init__(
        self,
        *,
        config: Simulation2Config,
        initial_listen_times: Mapping[int, float],
        algorithm: OscillatorAlgorithm | None = None,
    ) -> None:
        self.config = config
        self.algorithm = algorithm or resolve_algorithm(config.algorithm)
        self.medium = BroadcastMedium()
        self.oscillators: dict[int, OscillatorState] = {
            source_id: self.algorithm.create_state(source_id)
            for source_id in initial_listen_times
        }

        self._queue: list[ScheduledEvent] = []
        self._order_counter = itertools.count()
        self._now = 0.0

        for source_id, listen_at in initial_listen_times.items():
            self.schedule(
                time=float(listen_at),
                source_id=source_id,
                event_type=OscillatorEventType.LISTEN,
            )

    @property
    def now(self) -> float:
        return self._now

    def schedule(
        self,
        *,
        time: float,
        source_id: int,
        event_type: OscillatorEventType,
        revision: int = 0,
        sender_id: int | None = None,
    ) -> None:
        heapq.heappush(
            self._queue,
            ScheduledEvent(
                time=float(time),
                insertion_order=next(self._order_counter),
                event_type=event_type,
                source_id=source_id,
                revision=revision,
                sender_id=sender_id,
            ),
        )

    def transmit(self, *, source_id: int) -> Transmission:
        transmission = self.medium.transmit(
            source_id=source_id,
            start=self.now,
            duration_ms=self.config.transmission_duration_ms,
        )
        for receiver_id, receiver in self.oscillators.items():
            if receiver_id == source_id or not receiver.active:
                continue
            self.schedule(
                time=transmission.end,
                source_id=receiver_id,
                event_type=OscillatorEventType.RECEIVE,
                sender_id=source_id,
            )
        return transmission

    def carrier_sense(self, *, source_id: int) -> CarrierSenseResult:
        return self.medium.carrier_sense(
            source_id=source_id,
            time=self.now,
            duration_ms=self.config.carrier_sense_duration_ms,
        )

    def run(self, *, until_ms: float) -> None:
        while self._queue:
            event = heapq.heappop(self._queue)
            if event.time > until_ms:
                heapq.heappush(self._queue, event)
                return

            self._now = event.time
            state = self.oscillators.get(event.source_id)
            if state is None:
                continue
            self.algorithm.handle_event(
                event=event,
                state=state,
                runtime=self,
            )
