from __future__ import annotations

import unittest

from research_program.simulation2.algorithms.registry import (
    available_algorithms,
    resolve_algorithm,
)
from research_program.simulation2.algorithms.pco_d import (
    calculate_new_remaining_ms,
)
from research_program.simulation2.config import Simulation2Config
from research_program.simulation2.events import OscillatorEventType
from research_program.simulation2.oscillator import OscillatorMode
from research_program.simulation2.scheduler import EventScheduler


class PCODAlgorithmTest(unittest.TestCase):
    def test_registry_exposes_pco_d(self) -> None:
        self.assertEqual(available_algorithms(), ("PCO-D",))
        self.assertEqual(resolve_algorithm("PCO-D").name, "PCO-D")

    def test_receive_while_listening_postpones_send(self) -> None:
        scheduler = EventScheduler(
            config=Simulation2Config(
                listening_ratio=10.0 / 31.0,
                cycle_time_ms=31.0,
                alpha=0.5,
                transmission_duration_ms=1.0,
            ),
            initial_listen_times={0: 5.0, 1: 0.0},
        )

        scheduler.run(until_ms=20.0)

        transmissions = scheduler.medium.transmissions
        self.assertEqual(
            [(item.source_id, item.start, item.end) for item in transmissions],
            [(1, 10.0, 11.0), (0, 18.0, 19.0)],
        )
        self.assertEqual(scheduler.oscillators[0].receive_count, 1)
        self.assertEqual(scheduler.oscillators[0].send_count, 1)
        self.assertEqual(scheduler.oscillators[0].mode, OscillatorMode.SLEEPING)

    def test_receive_during_sleep_does_not_postpone_send(self) -> None:
        scheduler = EventScheduler(
            config=Simulation2Config(
                listening_ratio=10.0 / 31.0,
                cycle_time_ms=31.0,
                alpha=0.5,
                transmission_duration_ms=1.0,
            ),
            initial_listen_times={0: 0.0},
        )
        scheduler.run(until_ms=11.0)

        scheduler.schedule(
            time=15.0,
            source_id=0,
            event_type=OscillatorEventType.RECEIVE,
            sender_id=99,
        )
        scheduler.run(until_ms=15.0)

        state = scheduler.oscillators[0]
        self.assertEqual(state.mode, OscillatorMode.SLEEPING)
        self.assertEqual(state.receive_count, 0)
        self.assertIsNone(state.next_send_at)

    def test_new_remaining_time_uses_r_t_alpha_and_current_remaining(self) -> None:
        new_remaining_ms = calculate_new_remaining_ms(
            remaining_ms=4.0,
            listening_ratio=0.25,
            cycle_time_ms=40.0,
            alpha=0.5,
        )

        self.assertEqual(new_remaining_ms, 7.0)

    def test_each_receive_uses_the_updated_send_time(self) -> None:
        scheduler = EventScheduler(
            config=Simulation2Config(
                listening_ratio=0.25,
                cycle_time_ms=40.0,
                alpha=0.5,
            ),
            initial_listen_times={0: 5.0},
        )
        scheduler.schedule(
            time=11.0,
            source_id=0,
            event_type=OscillatorEventType.RECEIVE,
            sender_id=1,
        )
        scheduler.schedule(
            time=13.0,
            source_id=0,
            event_type=OscillatorEventType.RECEIVE,
            sender_id=2,
        )

        scheduler.run(until_ms=21.0)

        self.assertEqual(scheduler.oscillators[0].receive_count, 2)
        self.assertEqual(
            [(item.source_id, item.start) for item in scheduler.medium.transmissions],
            [(0, 20.5)],
        )

    def test_cycle_durations_include_transmission(self) -> None:
        config = Simulation2Config(
            listening_ratio=0.25,
            cycle_time_ms=40.0,
            alpha=0.5,
            transmission_duration_ms=2.0,
        )

        self.assertEqual(config.listening_duration_ms, 10.0)
        self.assertEqual(config.sleep_duration_ms, 28.0)

    def test_busy_carrier_sense_skips_transmission(self) -> None:
        scheduler = EventScheduler(
            config=Simulation2Config(
                listening_ratio=0.2,
                cycle_time_ms=100.0,
                alpha=0.5,
                carrier_sense_duration_ms=5.0,
                transmission_duration_ms=20.0,
            ),
            initial_listen_times={0: 0.0, 1: 15.0},
        )

        scheduler.run(until_ms=54.0)

        self.assertEqual(
            [(item.source_id, item.start, item.end) for item in scheduler.medium.transmissions],
            [(0, 20.0, 40.0)],
        )
        self.assertEqual(scheduler.oscillators[1].send_count, 0)
        self.assertEqual(scheduler.oscillators[1].skipped_send_count, 1)
        self.assertEqual(scheduler.oscillators[1].pending_sleep_extension_ms, 10.0)
        node_1_sense = next(
            item
            for item in scheduler.medium.carrier_sense_results
            if item.source_id == 1
        )
        self.assertTrue(node_1_sense.is_busy)
        self.assertEqual(node_1_sense.window_start, 30.0)
        self.assertEqual(node_1_sense.window_end, 35.0)

        scheduler.run(until_ms=55.0)
        self.assertEqual(scheduler.oscillators[1].pending_sleep_extension_ms, 0.0)


if __name__ == "__main__":
    unittest.main()
