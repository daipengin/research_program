from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Sequence

import matplotlib.pyplot as plt
from matplotlib.figure import Figure
import pandas as pd

from research_program.io.run_store import RunRecord


DEFAULT_Y_COLUMN = "mean_abs_diff_from_ideal_phase_gap_ratio"


def read_phase_gap_error(run_dir: Path) -> pd.DataFrame | None:
    path = run_dir / "phase_gap_error.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    if "cycle_index" not in df.columns:
        return None
    return df


def build_phase_gap_error_figure(
    records: Sequence[RunRecord],
    y_column: str = DEFAULT_Y_COLUMN,
    max_runs: int = 50,
) -> tuple[Figure | None, int]:
    fig, ax = plt.subplots(figsize=(10, 6))
    used = 0

    for record in records[:max_runs]:
        df = read_phase_gap_error(record.path)
        if df is None or y_column not in df.columns:
            continue
        ax.plot(
            df["cycle_index"],
            df[y_column],
            linewidth=1.2,
            alpha=0.45,
            label=record.run_id if used < 12 else None,
        )
        used += 1

    if used == 0:
        plt.close(fig)
        return None, 0

    ax.set_xlabel("Cycle")
    ax.set_ylabel(y_column)
    ax.grid(True, alpha=0.3)
    if used <= 12:
        ax.legend(fontsize=8)
    fig.tight_layout()
    return fig, used


def figure_to_bytes(fig: Figure, output_format: str) -> tuple[bytes, str, str]:
    output_format = output_format.lower()
    mime = {
        "png": "image/png",
        "pdf": "application/pdf",
        "svg": "image/svg+xml",
    }.get(output_format, "application/octet-stream")
    buffer = BytesIO()
    fig.savefig(buffer, format=output_format, dpi=300, bbox_inches="tight")
    return buffer.getvalue(), mime, f"phase_gap_error.{output_format}"
