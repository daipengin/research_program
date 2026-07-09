from __future__ import annotations

from enum import Enum
from typing import Callable, Dict
import numpy as np


class CouplingFunction(Enum):
    KURAMOTO = "KURAMOTO"
    LINEAR = "LINEAR"
    LINEAR_4 = "LINEAR_4"
    LINEAR_16 = "LINEAR_16"
    NewSIN = "NewSIN"
    expSIN = "expSIN"
    exp_4 = "exp_4"
    NONE = "NONE"


CouplingFuncType = Callable[[float], float]


def kuramoto_coupling(phase_diff: float) -> float:
    return np.sin(phase_diff)


def linear_coupling(phase_diff: float) -> float:
    return -1*(phase_diff)/np.pi%2 -1

def linear_4_coupling(phase_diff: float) -> float:
    pow = 4
    return -1*(phase_diff*pow)/np.pi%2 -1

def linear_16_coupling(phase_diff: float) -> float:
    pow = 16
    return -1*(phase_diff*pow)/np.pi%2 -1

def NewSin_coupling(phase_diff: float) -> float:
    phase_diff %= (np.pi*2)
    if phase_diff < np.pi:
        return 1-np.sin(phase_diff)
    else:
        return -1-np.sin(phase_diff)

def expsin_coupling(phase_diff: float) -> float:
    phase_diff %= (np.pi*2)
    pow = 4
    if phase_diff < np.pi:
        return np.exp(-phase_diff) * np.sin(phase_diff)
    else:
        return np.exp(phase_diff - 2*np.pi) * np.sin(phase_diff)

def exp_4_coupling(phase_diff: float) -> float:
    phase_diff %= (np.pi*2)
    pow = 4 
    if phase_diff < np.pi:
        return np.exp(-phase_diff*pow)
    else:
        return -np.exp((phase_diff- 2*np.pi)*pow)

def none_coupling(phase_diff: float) -> float:
    return 0.0


COUPLING_FUNCTION_MAP: Dict[CouplingFunction, CouplingFuncType] = {
    CouplingFunction.KURAMOTO: kuramoto_coupling,
    CouplingFunction.LINEAR: linear_coupling,
    CouplingFunction.LINEAR_4: linear_4_coupling,
    CouplingFunction.LINEAR_16: linear_16_coupling,
    CouplingFunction.NewSIN: NewSin_coupling,
    CouplingFunction.expSIN: expsin_coupling,
    CouplingFunction.exp_4: exp_4_coupling,
    CouplingFunction.NONE: none_coupling,
}


def resolve_coupling_function(coupling_type: CouplingFunction) -> CouplingFuncType:
    if coupling_type not in COUPLING_FUNCTION_MAP:
        raise ValueError(f"Unsupported coupling function: {coupling_type}")
    return COUPLING_FUNCTION_MAP[coupling_type]
