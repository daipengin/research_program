from __future__ import annotations

from collections.abc import Callable

from research_program.simulation2.algorithms.base import OscillatorAlgorithm
from research_program.simulation2.algorithms.pco_d import PCODAlgorithm


AlgorithmFactory = Callable[[], OscillatorAlgorithm]

_ALGORITHM_FACTORIES: dict[str, AlgorithmFactory] = {
    PCODAlgorithm.name: PCODAlgorithm,
}


def available_algorithms() -> tuple[str, ...]:
    return tuple(_ALGORITHM_FACTORIES)


def resolve_algorithm(name: str) -> OscillatorAlgorithm:
    try:
        factory = _ALGORITHM_FACTORIES[name]
    except KeyError as exc:
        allowed = ", ".join(available_algorithms())
        raise ValueError(f"Unsupported oscillator algorithm: {name}. Allowed: {allowed}") from exc
    return factory()
