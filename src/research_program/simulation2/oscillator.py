from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class OscillatorMode(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSMITTING = "transmitting"
    SLEEPING = "sleeping"


@dataclass
class OscillatorState:
    """Algorithm-owned mutable state for one oscillator."""

    source_id: int
    active: bool = True
    mode: OscillatorMode = OscillatorMode.IDLE

    listen_count: int = 0
    receive_count: int = 0
    send_count: int = 0
    skipped_send_count: int = 0
    sleep_count: int = 0

    listening_started_at: float | None = None
    next_send_at: float | None = None
    send_revision: int = 0
    pending_sleep_extension_ms: float = 0.0
    received_at: list[float] = field(default_factory=list)
