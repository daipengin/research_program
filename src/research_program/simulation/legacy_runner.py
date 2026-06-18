from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import re
import sys
from typing import Any

from research_program.config.paths import resolve_project_path


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
    legacy_simulator_dir: Path = Path("make_simulation_data")
    max_workers: int = 1


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
        tags=tuple(str(tag) for tag in simulation.get("tags", [])),
        output_root=resolve_project_path(paths.get("output_runs_dir", "data/runs")),
        legacy_simulator_dir=resolve_project_path(paths.get("legacy_simulator_dir", "make_simulation_data")),
        max_workers=int(simulation.get("max_workers", 1)),
    )


def _ensure_device_count_tag(tags: tuple[str, ...], device_count: int) -> tuple[str, ...]:
    if any(re.fullmatch(r"\d+dai", tag) for tag in tags):
        return tags
    return (*tags, f"{device_count}dai")


def _load_legacy_modules(legacy_simulator_dir: Path) -> dict[str, Any]:
    legacy_path = str(resolve_project_path(legacy_simulator_dir))
    if legacy_path not in sys.path:
        sys.path.insert(0, legacy_path)

    from config_factory import build_run_configs
    from coupling_functions import CouplingFunction
    from range_generators import generate_ranges_same_duration_from_unique_starts
    from scheduler import RunConfig, run_simulation_case, run_simulations_in_parallel

    return {
        "build_run_configs": build_run_configs,
        "CouplingFunction": CouplingFunction,
        "generate_ranges": generate_ranges_same_duration_from_unique_starts,
        "RunConfig": RunConfig,
        "run_simulation_case": run_simulation_case,
        "run_simulations_in_parallel": run_simulations_in_parallel,
    }


def _resolve_coupling_function(enum_class: Any, value: str) -> Any:
    for item in enum_class:
        if item.name == value or item.value == value:
            return item
    allowed = ", ".join(item.value for item in enum_class)
    raise ValueError(f"Unsupported coupling_function: {value}. Allowed: {allowed}")


def run_simulation_request(request: SimulationRequest) -> list[dict[str, Any]]:
    if request.num_runs < 1:
        raise ValueError("num_runs must be at least 1")
    if request.device_count < 1:
        raise ValueError("device_count must be at least 1")
    if request.device_count > request.start_step_count + 1:
        raise ValueError("device_count must be less than or equal to start_step_count + 1")

    modules = _load_legacy_modules(request.legacy_simulator_dir)
    coupling_function = _resolve_coupling_function(
        modules["CouplingFunction"],
        request.coupling_function,
    )
    tags = _ensure_device_count_tag(request.tags, request.device_count)

    base_config = modules["RunConfig"](
        run_id="",
        ranges=[],
        coupling_strength=request.coupling_strength,
        strength_ratio=request.strength_ratio,
        coupling_function=coupling_function,
        cycle_time=request.cycle_time,
        listening_rate=request.listening_rate,
        tags=list(tags),
    )

    def ranges_factory(rng: Any, index: int) -> list[tuple[int, int, int]]:
        return modules["generate_ranges"](
            rng=rng,
            n=request.start_step_count,
            step=request.start_step,
            k=request.device_count,
            duration=request.duration,
            start_device_id=0,
        )

    configs = modules["build_run_configs"](
        num_configs=request.num_runs,
        seed=request.seed,
        base_config=base_config,
        ranges_factory=ranges_factory,
    )

    request.output_root.mkdir(parents=True, exist_ok=True)

    if request.max_workers <= 1:
        return [
            modules["run_simulation_case"](
                config=replace(config, tags=list(tags)),
                output_root=request.output_root,
                verbose=False,
            )
            for config in configs
        ]

    return modules["run_simulations_in_parallel"](
        configs=configs,
        output_root=request.output_root,
        max_workers=request.max_workers,
        verbose=False,
    )
