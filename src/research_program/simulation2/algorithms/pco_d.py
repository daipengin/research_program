from __future__ import annotations

from research_program.simulation2.algorithms.base import OscillatorAlgorithm
from research_program.simulation2.events import OscillatorEventType, ScheduledEvent
from research_program.simulation2.oscillator import OscillatorMode, OscillatorState
from research_program.simulation2.runtime import AlgorithmRuntime


class PCODAlgorithm(OscillatorAlgorithm):
    """PCO-D algorithm frame: listen, send, then sleep.

    A packet received while listening postpones the pending SEND event. Old
    SEND events remain in the priority queue, but their revision no longer
    matches the oscillator state and they are therefore ignored.
    """

    name = "PCO-D"

    def handle_event(
        self,
        *,
        event: ScheduledEvent,
        state: OscillatorState,
        runtime: AlgorithmRuntime,
    ) -> None:
        if not state.active:
            return

        if event.event_type is OscillatorEventType.LISTEN:
            self._start_listening(state=state, runtime=runtime)
            return
        if event.event_type is OscillatorEventType.RECEIVE:
            self._receive(event=event, state=state, runtime=runtime)
            return
        if event.event_type is OscillatorEventType.SEND:
            self._send(event=event, state=state, runtime=runtime)
            return
        if event.event_type is OscillatorEventType.SLEEP:
            self._sleep(state=state, runtime=runtime)
            return
        raise ValueError(f"Unsupported PCO-D event: {event.event_type}")

    def _start_listening(
        self,
        *,
        state: OscillatorState,
        runtime: AlgorithmRuntime,
    ) -> None:
        state.mode = OscillatorMode.LISTENING
        state.listen_count += 1
        state.listening_started_at = runtime.now
        state.received_at.clear()

        send_at = runtime.now + runtime.config.listening_duration_ms
        self._replace_send_event(state=state, runtime=runtime, send_at=send_at)

    def _receive(
        self,
        *,
        event: ScheduledEvent,
        state: OscillatorState,
        runtime: AlgorithmRuntime,
    ) -> None:
        if state.mode is not OscillatorMode.LISTENING:
            return

        state.receive_count += 1
        state.received_at.append(runtime.now)

        planned_send_at = state.next_send_at
        if planned_send_at is None:
            return

        remaining_ms = max(0.0, planned_send_at - runtime.now)
        new_remaining_ms = calculate_new_remaining_ms(
            remaining_ms=remaining_ms,
            listening_ratio=runtime.config.listening_ratio,
            cycle_time_ms=runtime.config.cycle_time_ms,
            alpha=runtime.config.alpha,
        )
        self._replace_send_event(
            state=state,
            runtime=runtime,
            send_at=runtime.now + new_remaining_ms,
        )

    def _replace_send_event(
        self,
        *,
        state: OscillatorState,
        runtime: AlgorithmRuntime,
        send_at: float,
    ) -> None:
        state.send_revision += 1
        state.next_send_at = send_at
        runtime.schedule(
            time=send_at,
            source_id=state.source_id,
            event_type=OscillatorEventType.SEND,
            revision=state.send_revision,
        )

    def _send(
        self,
        *,
        event: ScheduledEvent,
        state: OscillatorState,
        runtime: AlgorithmRuntime,
    ) -> None:
        if state.mode is not OscillatorMode.LISTENING:
            return
        if event.revision != state.send_revision:
            return

        state.next_send_at = None

        carrier_sense_result = runtime.carrier_sense(source_id=state.source_id)
        if carrier_sense_result.is_busy:
            state.mode = OscillatorMode.IDLE
            state.skipped_send_count += 1
            # Treat the busy carrier sense at the planned SEND instant as a
            # virtual reception. At that instant R=0, so the PCO-D update
            # becomes R' = alpha * r * T. The send remains skipped; the same
            # phase shift is applied by extending this cycle's sleep.
            state.pending_sleep_extension_ms = calculate_new_remaining_ms(
                remaining_ms=0.0,
                listening_ratio=runtime.config.listening_ratio,
                cycle_time_ms=runtime.config.cycle_time_ms,
                alpha=runtime.config.alpha,
            )
            runtime.schedule(
                time=runtime.now + runtime.config.transmission_duration_ms,
                source_id=state.source_id,
                event_type=OscillatorEventType.SLEEP,
            )
            return

        state.mode = OscillatorMode.TRANSMITTING
        state.send_count += 1
        transmission = runtime.transmit(source_id=state.source_id)
        runtime.schedule(
            time=transmission.end,
            source_id=state.source_id,
            event_type=OscillatorEventType.SLEEP,
        )

    def _sleep(
        self,
        *,
        state: OscillatorState,
        runtime: AlgorithmRuntime,
    ) -> None:
        state.mode = OscillatorMode.SLEEPING
        state.sleep_count += 1
        sleep_extension_ms = state.pending_sleep_extension_ms
        state.pending_sleep_extension_ms = 0.0
        runtime.schedule(
            time=(
                runtime.now
                + runtime.config.sleep_duration_ms
                + sleep_extension_ms
            ),
            source_id=state.source_id,
            event_type=OscillatorEventType.LISTEN,
        )


def calculate_new_remaining_ms(
    *,
    remaining_ms: float,
    listening_ratio: float,
    cycle_time_ms: float,
    alpha: float,
) -> float:
    """Calculate PCO-D's remaining time after a reception.

    R' = (1 - alpha) * R + alpha * r * T
    """

    if remaining_ms < 0:
        raise ValueError("remaining_ms must be non-negative")
    return (
        (1.0 - alpha) * remaining_ms
        + alpha * listening_ratio * cycle_time_ms
    )
