"""PCO-D logic copied verbatim in meaning from simulation2.algorithms.pco_d.

The surrounding event loop intentionally belongs to simulation3 and uses the
simulation1 medium/CS definitions and CSV schemas.
"""
from __future__ import annotations


def calculate_new_remaining_ms(*, remaining_ms: float, listening_ratio: float,
                               cycle_time_ms: float, alpha: float) -> float:
    """R' = (1 - alpha) R + alpha r T."""
    if remaining_ms < 0:
        raise ValueError("remaining_ms must be non-negative")
    return (1.0 - alpha) * remaining_ms + alpha * listening_ratio * cycle_time_ms
