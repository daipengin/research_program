from __future__ import annotations

from pathlib import Path
from typing import Any


LAST_INTERVAL_PER_VS_K_PARAMS_PATH = (
    Path("outputs") / "settings" / "last_interval_per_vs_k_params.json"
)
LAST_CONVERGENCE_CYCLE_VS_K_PARAMS_PATH = (
    Path("outputs") / "settings" / "last_convergence_cycle_vs_k_params.json"
)
LAST_PHASE_GAP_ERROR_VS_K_PARAMS_PATH = (
    Path("outputs") / "settings" / "last_phase_gap_error_vs_k_params.json"
)

DEFAULT_INTERVAL_PER_VS_K_PARAMS: dict[str, Any] = {
    "coupling_function": "KURAMOTO",
    "k_start": 0.0,
    "k_stop": 20.0,
    "k_step": 5.0,
    "runs_per_k": 10,
    "interval_start_ms": 0.0,
    "interval_end_ms": 2_000_000.0,
    "per_method": "interval_packet_error_rate",
    "plot_settings": {
        "xlim_min": None,
        "xlim_max": None,
        "ylim_min": 0.0,
        "ylim_max": 100.0,
        "figure_width": 8.0,
        "figure_height": 5.0,
        "font_size_label": 12,
        "font_size_ticks": 10,
        "font_size_title": 12,
        "marker": "o",
        "marker_size": 6.0,
        "line_style": "-",
        "line_width": 1.5,
        "show_error_bars": True,
        "error_bar_capsize": 4.0,
        "show_title": True,
        "show_grid": True,
        "show_min_annotation": False,
        "min_annotation_font_size": 10,
        "min_annotation_x_offset": 10.0,
        "min_annotation_y_offset": 10.0,
        "save_dpi": 300,
    },
    "simulation_base": {
        "duration_ms": 2_000_000.0,
        "seed": 1,
        "device_count": 20,
        "cycle_time": 30_000,
        "initial_phase_start_percent": 0.0,
        "initial_phase_end_percent": 100.0,
        "listening_rate": 25,
        "strength_ratio": -0.0001,
        "max_workers": 1,
        "simulation_mode": "per_measurement",
        "carrier_sense_duration_ms": 0.0,
        "lora_payload_bytes": 16,
        "lora_spreading_factor": 7,
        "lora_bandwidth_hz": 125_000,
        "lora_coding_rate_denominator": 5,
        "lora_preamble_symbols": 8,
        "lora_explicit_header": True,
        "lora_crc_enabled": True,
        "lora_low_data_rate_optimize": "auto",
    },
}

DEFAULT_CONVERGENCE_CYCLE_VS_K_PARAMS: dict[str, Any] = {
    "source_mode": "new_simulation",
    "coupling_function": "KURAMOTO",
    "k_start": 0.0,
    "k_stop": 20.0,
    "k_step": 5.0,
    "runs_per_k": 10,
    "stable_cycle_count": 5,
    "phase_gap_change_threshold": 0.01,
    "repeat_index_min": None,
    "repeat_index_max": None,
    "selected_run_count": None,
    "plot_settings": DEFAULT_INTERVAL_PER_VS_K_PARAMS["plot_settings"].copy(),
    "simulation_base": DEFAULT_INTERVAL_PER_VS_K_PARAMS["simulation_base"].copy(),
}

DEFAULT_PHASE_GAP_ERROR_VS_K_PARAMS: dict[str, Any] = {
    "source_mode": "new_simulation",
    "coupling_function": "KURAMOTO",
    "k_start": 0.0,
    "k_stop": 20.0,
    "k_step": 5.0,
    "runs_per_k": 10,
    "target_cycle_mode": "last",
    "target_cycle_index": 1,
    "repeat_index_min": None,
    "repeat_index_max": None,
    "selected_run_count": None,
    "plot_settings": {
        **DEFAULT_INTERVAL_PER_VS_K_PARAMS["plot_settings"],
        "ylim_min": 0.0,
        "ylim_max": None,
    },
    "simulation_base": DEFAULT_INTERVAL_PER_VS_K_PARAMS["simulation_base"].copy(),
}
