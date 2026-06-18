from __future__ import annotations

from enum import Enum
from typing import Callable, Dict
import numpy as np


class CouplingFunction(Enum):
    KURAMOTO = "KURAMOTO"
    LINEAR = "LINEAR"
    NewSIN = "NewSIN"


CouplingFuncType = Callable[[float], float]


def kuramoto_coupling(phase_diff: float) -> float:
    return np.sin(phase_diff)


def linear_coupling(phase_diff: float) -> float:
    return -1*(phase_diff)/np.pi%2 -1

def NewSin_coupling(phase_diff: float) -> float:
    phase_diff %= (np.pi*2)
    if phase_diff < np.pi:
        return 1-np.sin(phase_diff)
    else:
        return -1-np.sin(phase_diff)



COUPLING_FUNCTION_MAP: Dict[CouplingFunction, CouplingFuncType] = {
    CouplingFunction.KURAMOTO: kuramoto_coupling,
    CouplingFunction.LINEAR: linear_coupling,
    CouplingFunction.NewSIN: NewSin_coupling,
}


def resolve_coupling_function(coupling_type: CouplingFunction) -> CouplingFuncType:
    if coupling_type not in COUPLING_FUNCTION_MAP:
        raise ValueError(f"Unsupported coupling function: {coupling_type}")
    return COUPLING_FUNCTION_MAP[coupling_type]