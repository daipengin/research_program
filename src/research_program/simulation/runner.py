from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import re
from typing import Any, Callable, Literal

from research_program.config.paths import resolve_project_path
from research_program.simulation.config_factory import build_run_configs
from research_program.simulation.coupling_functions import CouplingFunction
from research_program.simulation.lora_airtime import (
    LoRaAirtimeConfig,
    calculate_lora_airtime_ms,
    resolve_low_data_rate_optimize,
)
from research_program.simulation.range_generators import (
    generate_even_interval_start_times,
    generate_ranges_from_start_times,
    generate_ranges_same_duration_from_unique_starts,
)
from research_program.simulation.scheduler import (
    RunConfig,
    default_max_workers,
    run_simulation_case,
    run_simulations_in_parallel,
)


DEVICE_COUNT_TAG_PATTERN = re.compile(r"\d+dai")
DEVICE_COUNT_LABEL_TAG_PATTERN = re.compile(r"device_count_\d+")
START_TIMING_TAGS = {"start_random", "start_fixed"}
SIMULATION_MODE_TAGS = {"mode_standard", "mode_per_measurement"}
StartTimingMode = Literal["random", "fixed"]
SimulationMode = Literal["standard", "per_measurement"]


def _parse_optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"auto", "none", "null"}:
        return None
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Unsupported boolean value: {value}")


@dataclass(frozen=True)
class SimulationRequest:
    num_runs: int
    seed: int
    coupling_function: str
    coupling_strength: int
    strength_ratio: float
    cycle_time: int
    listening_rate: int
    device_count: int
    duration: int
    start_step_count: int
    start_step: int
    tags: tuple[str, ...]
    output_root: Path
    max_workers: int = 0
    start_timing_mode: StartTimingMode = "random"
    fixed_start_times: tuple[int, ...] = tuple()
    fixed_start_interval: int = 10
    fixed_start_offset: int = 0
    simulation_mode: SimulationMode = "standard"
    carrier_sense_duration_ms: float = 0.0
    lora_payload_bytes: int = 16
    lora_spreading_factor: int = 7
    lora_bandwidth_hz: int = 125_000
    lora_coding_rate_denominator: int = 5
    lora_preamble_symbols: int = 8
    lora_explicit_header: bool = True
    lora_crc_enabled: bool = True
    lora_low_data_rate_optimize: bool | None = None
    save_asleep_log: bool = False
    save_carrier_sense_log: bool = False


def request_from_config(config: dict[str, Any]) -> SimulationRequest:
    paths = config.get("paths", {})
    simulation = config.get("simulation", {})
    return SimulationRequest(
        num_runs=int(simulation.get("num_runs", 1)),
        seed=int(simulation.get("seed", 12345)),
        coupling_function=str(simulation.get("coupling_function", "LINEAR")),
        coupling_strength=int(simulation.get("coupling_strength", 10)),
        strength_ratio=float(simulation.get("strength_ratio", -0.0001)),
        cycle_time=int(simulation.get("cycle_time", 30000)),
        listening_rate=int(simulation.get("listening_rate", 25)),
        device_count=int(simulation.get("device_count", 20)),
        duration=int(simulation.get("duration", 30000000)),
        start_step_count=int(simulation.get("start_step_count", 100)),
        start_step=int(simulation.get("start_step", 10)),
        start_timing_mode=str(simulation.get("start_timing_mode", "random")),  # type: ignore[arg-type]
        fixed_start_times=tuple(int(value) for value in simulation.get("fixed_start_times", [])),
        fixed_start_interval=int(simulation.get("fixed_start_interval", simulation.get("start_step", 10))),
        fixed_start_offset=int(simulation.get("fixed_start_offset", 0)),
        simulation_mode=str(simulation.get("simulation_mode", "standard")),  # type: ignore[arg-type]
        carrier_sense_duration_ms=float(simulation.get("carrier_sense_duration_ms", 0.0)),
        lora_payload_bytes=int(simulation.get("lora_payload_bytes", 16)),
        lora_spreading_factor=int(simulation.get("lora_spreading_factor", 7)),
        lora_bandwidth_hz=int(simulation.get("lora_bandwidth_hz", 125_000)),
        lora_coding_rate_denominator=int(simulation.get("lora_coding_rate_denominator", 5)),
        lora_preamble_symbols=int(simulation.get("lora_preamble_symbols", 8)),
        lora_explicit_header=bool(simulation.get("lora_explicit_header", True)),
        lora_crc_enabled=bool(simulation.get("lora_crc_enabled", True)),
        lora_low_data_rate_optimize=_parse_optional_bool(simulation.get("lora_low_data_rate_optimize", None)),
        save_asleep_log=bool(simulation.get("save_asleep_log", False)),
        save_carrier_sense_log=bool(simulation.get("save_carrier_sense_log", False)),
        tags=tuple(str(tag) for tag in simulation.get("tags", [])),
        output_root=resolve_project_path(paths.get("output_runs_dir", "data/runs")),
        max_workers=int(simulation.get("max_workers", 0)),
    )


def normalize_simulation_tags(
    tags: tuple[str, ...],
    device_count: int,
    start_timing_mode: StartTimingMode = "random",
    simulation_mode: SimulationMode = "standard",
) -> tuple[str, ...]:
    normalized_tags: list[str] = []
    seen: set[str] = set()

    for raw_tag in tags:
        tag = str(raw_tag).strip()
        if not tag:
            continue
        if DEVICE_COUNT_TAG_PATTERN.fullmatch(tag):
            continue
        if DEVICE_COUNT_LABEL_TAG_PATTERN.fullmatch(tag):
            continue
        if tag in START_TIMING_TAGS:
            continue
        if tag in SIMULATION_MODE_TAGS:
            continue
        if tag in seen:
            continue
        normalized_tags.append(tag)
        seen.add(tag)

    normalized_tags.append(f"device_count_{device_count}")
    normalized_tags.append(f"{device_count}dai")
    normalized_tags.append(f"start_{start_timing_mode}")
    normalized_tags.append(f"mode_{simulation_mode}")
    return tuple(normalized_tags)


def resolve_max_workers(num_cases: int, requested_max_workers: int) -> int:
    if requested_max_workers <= 0:
        return default_max_workers(num_cases)
    return max(1, min(requested_max_workers, num_cases))


def _resolve_coupling_function(value: str) -> CouplingFunction:
    for item in CouplingFunction:
        if item.name == value or item.value == value:
            return item
    allowed = ", ".join(item.value for item in CouplingFunction)
    raise ValueError(f"Unsupported coupling_function: {value}. Allowed: {allowed}")


def fixed_start_times_for_request(request: SimulationRequest) -> tuple[int, ...]:
    if request.fixed_start_times:
        start_times = tuple(int(value) for value in request.fixed_start_times)
    else:
        start_times = tuple(
            generate_even_interval_start_times(
                k=request.device_count,
                interval=request.fixed_start_interval,
                start_time=request.fixed_start_offset,
            )
        )

    if len(start_times) != request.device_count:
        raise ValueError(
            "fixed_start_times must contain exactly device_count values "
            f"({request.device_count}), got {len(start_times)}"
        )
    if any(start_time < 0 for start_time in start_times):
        raise ValueError("fixed_start_times must be non-negative")
    return start_times


def lora_airtime_config_from_request(request: SimulationRequest) -> LoRaAirtimeConfig:
    return LoRaAirtimeConfig(
        payload_bytes=request.lora_payload_bytes,
        spreading_factor=request.lora_spreading_factor,
        bandwidth_hz=request.lora_bandwidth_hz,
        coding_rate_denominator=request.lora_coding_rate_denominator,
        preamble_symbols=request.lora_preamble_symbols,
        explicit_header=request.lora_explicit_header,
        crc_enabled=request.lora_crc_enabled,
        low_data_rate_optimize=request.lora_low_data_rate_optimize,
    )


def lora_airtime_ms_for_request(request: SimulationRequest) -> float:
    return calculate_lora_airtime_ms(lora_airtime_config_from_request(request))


def effective_carrier_sense_duration_for_request(request: SimulationRequest) -> float:
    if request.simulation_mode != "per_measurement":
        return 0.0
    return float(request.carrier_sense_duration_ms)


def _random_ranges_factory(request: SimulationRequest):
    seen_start_sets: set[tuple[int, ...]] = set()

    def ranges_factory(rng: Any, index: int) -> list[tuple[int, int, int]]:
        max_unique_points = request.start_step_count + 1
        if request.device_count > max_unique_points:
            raise ValueError("device_count must be less than or equal to start_step_count + 1")

        for _ in range(1000):
            ranges = generate_ranges_same_duration_from_unique_starts(
                rng=rng,
                n=request.start_step_count,
                step=request.start_step,
                k=request.device_count,
                duration=request.duration,
                start_device_id=0,
            )
            start_set = tuple(start for start, _, _ in ranges)
            if start_set not in seen_start_sets:
                seen_start_sets.add(start_set)
                return ranges

        raise ValueError("failed to generate unique random start timings for each run")

    return ranges_factory


def _fixed_ranges_factory(request: SimulationRequest):
    start_times = fixed_start_times_for_request(request)

    def ranges_factory(rng: Any, index: int) -> list[tuple[int, int, int]]:
        return generate_ranges_from_start_times(
            start_times=list(start_times),
            duration=request.duration,
            start_device_id=0,
        )

    return ranges_factory


def _ranges_factory_for_request(request: SimulationRequest):
    if request.start_timing_mode == "random":
        return _random_ranges_factory(request)
    if request.start_timing_mode == "fixed":
        return _fixed_ranges_factory(request)
    raise ValueError("start_timing_mode must be 'random' or 'fixed'")


def run_simulation_request(
    request: SimulationRequest,
    progress_callback: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    if request.num_runs < 1:
        raise ValueError("num_runs must be at least 1")
    if request.device_count < 1:
        raise ValueError("device_count must be at least 1")
    if request.simulation_mode not in {"standard", "per_measurement"}:
        raise ValueError("simulation_mode must be 'standard' or 'per_measurement'")
    if request.start_timing_mode == "random" and request.device_count > request.start_step_count + 1:
        raise ValueError("device_count must be less than or equal to start_step_count + 1")
    if request.carrier_sense_duration_ms < 0:
        raise ValueError("carrier_sense_duration_ms must be non-negative")

    coupling_function = _resolve_coupling_function(request.coupling_function)
    tags = normalize_simulation_tags(
        tags=request.tags,
        device_count=request.device_count,
        start_timing_mode=request.start_timing_mode,
        simulation_mode=request.simulation_mode,
    )

    lora_config = lora_airtime_config_from_request(request)
    transmission_time_ms = lora_airtime_ms_for_request(request) if request.simulation_mode == "per_measurement" else 0.0

    base_config = RunConfig(
        run_id="",
        ranges=[],
        coupling_strength=request.coupling_strength,
        strength_ratio=request.strength_ratio,
        coupling_function=coupling_function,
        cycle_time=request.cycle_time,
        listening_rate=request.listening_rate,
        tags=list(tags),
        start_timing_mode=request.start_timing_mode,
        random_sampling_method=(
            "uniform_without_replacement"
            if request.start_timing_mode == "random"
            else ""
        ),
        start_step=request.start_step if request.start_timing_mode == "random" else None,
        start_step_count=request.start_step_count if request.start_timing_mode == "random" else None,
        simulation_mode=request.simulation_mode,
        carrier_sense_duration_ms=request.carrier_sense_duration_ms,
        transmission_time_ms=transmission_time_ms,
        lora_payload_bytes=lora_config.payload_bytes,
        lora_spreading_factor=lora_config.spreading_factor,
        lora_bandwidth_hz=lora_config.bandwidth_hz,
        lora_coding_rate_denominator=lora_config.coding_rate_denominator,
        lora_preamble_symbols=lora_config.preamble_symbols,
        lora_explicit_header=lora_config.explicit_header,
        lora_crc_enabled=lora_config.crc_enabled,
        lora_low_data_rate_optimize=resolve_low_data_rate_optimize(lora_config),
        save_asleep_log=request.save_asleep_log,
        save_carrier_sense_log=request.save_carrier_sense_log,
    )

    configs = build_run_configs(
        num_configs=request.num_runs,
        seed=request.seed,
        base_config=base_config,
        ranges_factory=_ranges_factory_for_request(request),
    )

    request.output_root.mkdir(parents=True, exist_ok=True)

    max_workers = resolve_max_workers(len(configs), request.max_workers)

    if max_workers <= 1:
        results: list[dict[str, Any]] = []
        for config in configs:
            result = run_simulation_case(
                config=replace(config, tags=list(tags)),
                output_root=request.output_root,
                verbose=False,
            )
            results.append(result)
            if progress_callback is not None:
                progress_callback(len(results), len(configs), result)
        return results

    return run_simulations_in_parallel(
        configs=configs,
        output_root=request.output_root,
        max_workers=max_workers,
        verbose=False,
        progress_callback=progress_callback,
    )
