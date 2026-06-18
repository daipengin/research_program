from __future__ import annotations

import math
import numpy as np
import matplotlib.pyplot as plt

from research_program.simulation.coupling_functions import CouplingFunction, resolve_coupling_function


def main() -> None:
    x = np.linspace(-math.pi, math.pi, 1000)

    function_types = [
        CouplingFunction.KURAMOTO,
        CouplingFunction.LINEAR,
        CouplingFunction.NewSIN,
        
    ]

    plt.figure(figsize=(10, 6))

    for function_type in function_types:
        func = resolve_coupling_function(function_type)
        y = [func(v) for v in x]
        plt.scatter(x, y, label=function_type.value,s=10)

    plt.xlabel("phase difference [rad]")
    plt.ylabel("coupling function output")
    plt.title("Coupling Functions")
    plt.xlim(-math.pi, math.pi)
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
