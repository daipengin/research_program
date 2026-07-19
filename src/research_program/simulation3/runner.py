"""Simulation3 runner.

KURAMOTO and LINEAR are delegated without alteration to simulation1.  PCO-D
uses simulation1's airtime parameters, CS window definition, and log schema.
"""
from __future__ import annotations

from dataclasses import dataclass
import csv
import heapq
import itertools
from pathlib import Path
from typing import Literal

from research_program.simulation.lora_airtime import LoRaAirtimeConfig, calculate_lora_airtime_ms
from research_program.simulation.runner import SimulationRequest, run_simulation_request
from research_program.simulation.scheduler import BufferedCsvEventLogger
from .pco_d import calculate_new_remaining_ms

Algorithm = Literal["KURAMOTO", "LINEAR", "PCO_D"]


@dataclass(frozen=True)
class Simulation3Request:
    algorithm: Algorithm
    num_runs: int
    seed: int
    coupling_parameter: float
    cycle_time: float
    duration: float
    listening_ratio: float
    device_count: int
    output_root: Path
    initial_start_times_by_run: tuple[tuple[int, ...], ...] = ()
    strength_ratio: float = -1e-4
    carrier_sense_duration_ms: float = 0.0
    payload_bytes: int = 37
    spreading_factor: int = 7
    bandwidth_hz: int = 500_000
    coding_rate_denominator: int = 5
    preamble_symbols: int = 8

    def __post_init__(self) -> None:
        if self.algorithm not in {"KURAMOTO", "LINEAR", "PCO_D"}:
            raise ValueError("algorithm must be KURAMOTO, LINEAR, or PCO_D")
        if not 0 <= self.listening_ratio <= 1 or self.cycle_time <= 0 or self.num_runs < 1:
            raise ValueError("invalid timing parameters")
        if self.algorithm == "PCO_D" and not 0 <= self.coupling_parameter <= 1:
            raise ValueError("PCO_D coupling_parameter (alpha) must be in [0, 1]")

    @property
    def airtime_ms(self) -> float:
        return calculate_lora_airtime_ms(LoRaAirtimeConfig(
            payload_bytes=self.payload_bytes, spreading_factor=self.spreading_factor,
            bandwidth_hz=self.bandwidth_hz, coding_rate_denominator=self.coding_rate_denominator,
            preamble_symbols=self.preamble_symbols,
        ))


def run_simulation3_request(request: Simulation3Request) -> list[dict[str, object]]:
    """Run all requested runs; K/L deliberately call simulation1 unchanged."""
    if request.algorithm != "PCO_D":
        legacy = SimulationRequest(
            num_runs=request.num_runs, seed=request.seed, coupling_function=request.algorithm,
            coupling_strength=int(request.coupling_parameter), strength_ratio=request.strength_ratio,
            cycle_time=int(request.cycle_time), listening_rate=int(request.listening_ratio * 100),
            device_count=request.device_count, duration=int(request.duration), start_step_count=0,
            start_step=1, tags=("simulation3",), output_root=request.output_root,
            start_timing_mode="random_cycle_ms_with_replacement",
            initial_start_times_by_run=request.initial_start_times_by_run,
            simulation_mode="per_measurement", carrier_sense_duration_ms=request.carrier_sense_duration_ms,
            lora_payload_bytes=request.payload_bytes, lora_spreading_factor=request.spreading_factor,
            lora_bandwidth_hz=request.bandwidth_hz, lora_coding_rate_denominator=request.coding_rate_denominator,
            lora_preamble_symbols=request.preamble_symbols, save_carrier_sense_log=True,
        )
        return run_simulation_request(legacy)
    request.output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    for run_index in range(request.num_runs):
        starts = (request.initial_start_times_by_run[run_index] if request.initial_start_times_by_run
                  else tuple((i * request.cycle_time / request.device_count) for i in range(request.device_count)))
        run_id = f"pco_d_{run_index:04d}"
        output_dir = request.output_root / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        engine = _PCODEngine(request, starts, output_dir)
        engine.run()
        results.append({"run_id": run_id, "random_seed": request.seed,
                        "random_run_index": run_index, "selected_start_times": ";".join(map(str, starts)),
                        "output_dir": str(output_dir), "airtime_ms": request.airtime_ms})
    return results


class _PCODEngine:
    # Event order and medium rules are sourced from simulation1.scheduler.EventScheduler.
    def __init__(self, request: Simulation3Request, starts: tuple[int, ...], output_dir: Path) -> None:
        self.r, self.starts, self.now = request, starts, 0.0
        self.queue: list[tuple[float, int, str, int, int]] = []
        self.order = itertools.count()
        self.intervals: list[tuple[float, float, int]] = []
        self.revision = [0] * request.device_count
        self.mode = ["idle"] * request.device_count
        self.send_count = [0] * request.device_count
        self.logger = BufferedCsvEventLogger(output_dir / "send_log.csv", output_dir / "asleep_log.csv",
                                             output_dir / "carrier_sense_log.csv", output_dir / "metadata.csv",
                                             save_carrier_sense_log=True)
        for device, start in enumerate(starts): self.schedule(float(start), device, "listen")

    def schedule(self, time: float, device: int, kind: str, revision: int = 0) -> None:
        heapq.heappush(self.queue, (time, next(self.order), kind, device, revision))

    def _cs_window(self, time: float) -> tuple[float, float]:
        # PCO-D listens immediately before send, so its awake start is send-rT.
        return time - self.r.carrier_sense_duration_ms, time

    def _blocking(self, device: int, start: float, end: float):
        self.intervals = [x for x in self.intervals if x[1] > start]
        if start >= end: return None
        return next((x for x in self.intervals if x[2] != device and x[0] < end and x[1] > start), None)

    def run(self) -> None:
        while self.queue:
            time, _, kind, d, rev = heapq.heappop(self.queue)
            if time > self.r.duration: break
            self.now = time
            if kind == "listen":
                self.mode[d] = "listening"; self.revision[d] += 1
                self.schedule(time + self.r.listening_ratio * self.r.cycle_time, d, "send", self.revision[d])
            elif kind == "receive" and self.mode[d] == "listening":
                # Same revision invalidation rule as simulation2 PCO-D.
                remaining = max(0.0, self._next_send_time(d) - time)
                updated = calculate_new_remaining_ms(remaining_ms=remaining, listening_ratio=self.r.listening_ratio,
                    cycle_time_ms=self.r.cycle_time, alpha=self.r.coupling_parameter)
                self.revision[d] += 1; self.schedule(time + updated, d, "send", self.revision[d])
            elif kind == "send" and self.mode[d] == "listening" and rev == self.revision[d]:
                start, end = self._cs_window(time); blocking = self._blocking(d, start, end)
                if blocking is not None:
                    self.mode[d] = "idle"
                    self.logger.log_carrier_sense(time, d, "skip_busy", start, end, blocking[2], blocking[0], blocking[1])
                    # Virtual R=0 reception extends this cycle's sleep.
                    self.schedule(time + self.r.airtime_ms, d, "sleep_virtual")
                else:
                    self.mode[d] = "transmitting"; self.send_count[d] += 1
                    tx_end = time + self.r.airtime_ms; self.intervals.append((time, tx_end, d))
                    self.logger.log_send(time, d, self.send_count[d], tx_end, self.r.airtime_ms)
                    self.logger.log_carrier_sense(time, d, "send_clear", start, end, None, None, None)
                    for target in range(self.r.device_count):
                        if target != d: self.schedule(tx_end, target, "receive")
                    self.schedule(tx_end, d, "sleep")
            elif kind in {"sleep", "sleep_virtual"}:
                self.mode[d] = "sleeping"
                extension = (calculate_new_remaining_ms(remaining_ms=0.0, listening_ratio=self.r.listening_ratio,
                    cycle_time_ms=self.r.cycle_time, alpha=self.r.coupling_parameter) if kind == "sleep_virtual" else 0.0)
                self.schedule(time + (self.r.cycle_time - self.r.listening_ratio * self.r.cycle_time - self.r.airtime_ms) + extension, d, "listen")
        self.logger.flush_logs()
        self._write_metadata()

    def _next_send_time(self, device: int) -> float:
        candidates = [event[0] for event in self.queue if event[2] == "send" and event[3] == device and event[4] == self.revision[device]]
        return min(candidates) if candidates else self.now

    def _write_metadata(self) -> None:
        with self.logger.metadata_log_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["algorithm", "coupling_parameter", "coupling_parameter_interpretation", "cycle_time", "listening_ratio", "transmission_time_ms"])
            writer.writeheader(); writer.writerow({"algorithm": "PCO_D", "coupling_parameter": self.r.coupling_parameter,
                "coupling_parameter_interpretation": "alpha", "cycle_time": self.r.cycle_time,
                "listening_ratio": self.r.listening_ratio, "transmission_time_ms": self.r.airtime_ms})
