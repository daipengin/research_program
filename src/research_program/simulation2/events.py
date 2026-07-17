from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class OscillatorEventType(str, Enum):
    LISTEN = "listen"
    RECEIVE = "receive"
    SEND = "send"
    SLEEP = "sleep"


@dataclass(order=True, frozen=True)
class ScheduledEvent:
    time: float
    insertion_order: int
    event_type: OscillatorEventType = field(compare=False)
    source_id: int = field(compare=False)
    revision: int = field(default=0, compare=False)
    sender_id: int | None = field(default=None, compare=False)
