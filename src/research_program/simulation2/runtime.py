from __future__ import annotations

from typing import Protocol

from research_program.simulation2.config import Simulation2Config
from research_program.simulation2.events import OscillatorEventType
from research_program.simulation2.medium import CarrierSenseResult, Transmission


class AlgorithmRuntime(Protocol):
    """Operations an oscillator algorithm may request from the engine."""

    @property
    def now(self) -> float: ...

    @property
    def config(self) -> Simulation2Config: ...

    def schedule(
        self,
        *,
        time: float,
        source_id: int,
        event_type: OscillatorEventType,
        revision: int = 0,
        sender_id: int | None = None,
    ) -> None: ...

    def carrier_sense(self, *, source_id: int) -> CarrierSenseResult: ...

    def transmit(self, *, source_id: int) -> Transmission: ...
