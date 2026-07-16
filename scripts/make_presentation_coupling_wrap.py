"""Add a [-pi, pi) wrapped-axis variant of the v2 coupling-function plot."""
from __future__ import annotations

import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import make_presentation_figures_v2 as base

OUT = base.OUT


def wrapped_segments(phi: np.ndarray, value: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    """Reorder the unchanged 0..2pi samples and break genuine mapped jumps."""
    delta = ((phi + math.pi) % (2 * math.pi)) - math.pi
    order = np.argsort(delta, kind="stable")
    x, y = delta[order], value[order]
    # A plotted line must not bridge a jump introduced/retained at the wrapped seam.
    step = np.abs(np.diff(y)); typical = float(np.median(step[step > 0])) if np.any(step > 0) else 0.0
    cuts = np.flatnonzero(step > max(1e-10, typical * 8)) + 1
    return [(a, b) for a, b in zip(np.split(x, cuts), np.split(y, cuts), strict=True)]


def main() -> int:
    base.configure(); original = pd.read_csv(OUT / "pres2_coupling_functions.csv")
    fig, ax = plt.subplots(figsize=(6.3, 3.7), constrained_layout=True); rows = []
    for internal in base.FUNCTIONS:
        p = original[original.coupling_function.eq(internal)]
        phi = p.phase_difference_rad.to_numpy(float); value = p.coupling_function_value.to_numpy(float)
        delta = ((phi + math.pi) % (2 * math.pi)) - math.pi
        for index, (x, y) in enumerate(wrapped_segments(phi, value)):
            ax.plot(x, y, color=base.COLOR[internal], lw=2, label=base.JP[internal] if index == 0 else "_nolegend_")
        rows.append(pd.DataFrame({"coupling_function": internal, "phi_original_rad": phi,
                                  "phase_difference_wrapped_rad": delta, "coupling_function_value": value}))
    ax.axhline(0, color="0.55", lw=.7); ax.axvline(0, color="0.55", lw=.7)
    ax.set_xlim(-math.pi, math.pi); ax.set_xticks([-math.pi, -math.pi/2, 0, math.pi/2, math.pi])
    ax.set_xticklabels(["−π", "−π/2", "0", "π/2", "π"])
    ax.set_xlabel("位相差 [rad]"); ax.set_ylabel("結合関数値"); base.style(ax, logx=False); ax.legend(frameon=False)
    data = pd.concat(rows, ignore_index=True); data.to_csv(OUT / "pres2_coupling_functions_wrap.csv", index=False, lineterminator="\n")
    fig.savefig(OUT / "pres2_coupling_functions_wrap.png", dpi=300, bbox_inches="tight")
    fig.savefig(OUT / "pres2_coupling_functions_wrap.svg", bbox_inches="tight"); plt.close(fig)
    # Exact original samples establish that no function definition changed.
    log = []
    for internal in base.FUNCTIONS:
        p = data[data.coupling_function.eq(internal)]
        original_value = p.iloc[np.argmin(np.abs(p.phi_original_rad - math.pi/2))].coupling_function_value
        wrapped_value = p.iloc[np.argmin(np.abs(p.phase_difference_wrapped_rad - math.pi/2))].coupling_function_value
        assert np.isclose(original_value, wrapped_value)
        log.append(f"wrap PASS {internal}: phi=pi/2 value={original_value:.12g} equals delta=pi/2")
    (OUT / "execution_coupling_wrap.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__": raise SystemExit(main())
