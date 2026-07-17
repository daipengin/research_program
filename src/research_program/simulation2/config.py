from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Simulation2Config:
    """Common timing configuration for the simulation2 engine.

    ``listening_ratio`` (r) is the fraction of one nominal cycle ``cycle_time_ms``
    (T) spent listening. The transmission duration is part of T, and the
    remaining time is used for sleep.
    """

    algorithm: str = "PCO-D"
    listening_ratio: float = 0.25
    cycle_time_ms: float = 4_000.0
    alpha: float = 0.5
    carrier_sense_duration_ms: float = 0.0
    transmission_duration_ms: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.listening_ratio <= 1.0:
            raise ValueError("listening_ratio must be between 0 and 1")
        if self.cycle_time_ms <= 0:
            raise ValueError("cycle_time_ms must be positive")
        if not 0.0 <= self.alpha <= 1.0:
            raise ValueError("alpha must be between 0 and 1")
        if self.carrier_sense_duration_ms < 0:
            raise ValueError("carrier_sense_duration_ms must be non-negative")
        if self.transmission_duration_ms < 0:
            raise ValueError("transmission_duration_ms must be non-negative")
        if self.sleep_duration_ms < 0:
            raise ValueError(
                "transmission_duration_ms must not exceed the non-listening "
                "portion of cycle_time_ms"
            )

    @property
    def listening_duration_ms(self) -> float:
        return self.listening_ratio * self.cycle_time_ms

    @property
    def sleep_duration_ms(self) -> float:
        return (
            self.cycle_time_ms
            - self.listening_duration_ms
            - self.transmission_duration_ms
        )
