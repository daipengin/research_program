from __future__ import annotations

from abc import ABC, abstractmethod

from research_program.simulation2.events import ScheduledEvent
from research_program.simulation2.oscillator import OscillatorState
from research_program.simulation2.runtime import AlgorithmRuntime


class OscillatorAlgorithm(ABC):
    """Strategy interface implemented by each oscillator algorithm file."""

    name: str

    def create_state(self, source_id: int) -> OscillatorState:
        return OscillatorState(source_id=source_id)

    @abstractmethod
    def handle_event(
        self,
        *,
        event: ScheduledEvent,
        state: OscillatorState,
        runtime: AlgorithmRuntime,
    ) -> None:
        """Apply one event to an oscillator state."""
