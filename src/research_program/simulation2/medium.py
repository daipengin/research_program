from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Transmission:
    source_id: int
    start: float
    end: float


@dataclass(frozen=True)
class CarrierSenseResult:
    source_id: int
    time: float
    window_start: float
    window_end: float
    blocking_transmission: Transmission | None

    @property
    def is_busy(self) -> bool:
        return self.blocking_transmission is not None


class BroadcastMedium:
    """Broadcast medium with transmission history and carrier sense."""

    def __init__(self) -> None:
        self.transmissions: list[Transmission] = []
        self.carrier_sense_results: list[CarrierSenseResult] = []

    def carrier_sense(
        self,
        *,
        source_id: int,
        time: float,
        duration_ms: float,
    ) -> CarrierSenseResult:
        window_start = time - duration_ms
        blocking_transmission = None
        if duration_ms > 0:
            for transmission in reversed(self.transmissions):
                if transmission.source_id == source_id:
                    continue
                if transmission.start < time and transmission.end > window_start:
                    blocking_transmission = transmission
                    break

        result = CarrierSenseResult(
            source_id=source_id,
            time=time,
            window_start=window_start,
            window_end=time,
            blocking_transmission=blocking_transmission,
        )
        self.carrier_sense_results.append(result)
        return result

    def transmit(
        self,
        *,
        source_id: int,
        start: float,
        duration_ms: float,
    ) -> Transmission:
        transmission = Transmission(
            source_id=source_id,
            start=start,
            end=start + duration_ms,
        )
        self.transmissions.append(transmission)
        return transmission
