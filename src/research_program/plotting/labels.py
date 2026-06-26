from __future__ import annotations


COUPLING_STRENGTH_MULTIPLIER_TEXT = "× -0.0001"


def coupling_strength_axis_label(base_label: str) -> str:
    normalized_label = base_label.lower()
    if "0.0001" in normalized_label or "strength_ratio" in normalized_label:
        return base_label
    return f"{base_label} (tick {COUPLING_STRENGTH_MULTIPLIER_TEXT})"


def coupling_strength_value_label(value: float) -> str:
    return f"K={value:g} ({COUPLING_STRENGTH_MULTIPLIER_TEXT})"
