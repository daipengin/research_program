from __future__ import annotations

import base64
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from research_program.graph_workflow.storage import (
    INTERVAL_PER_DB_NAME,
    ensure_interval_per_db,
)
from research_program.simulation.lora_airtime import LoRaAirtimeConfig
from research_program.web.settings import (
    DEFAULT_CONVERGENCE_CYCLE_VS_K_PARAMS,
    DEFAULT_INTERVAL_PER_VS_K_PARAMS,
    DEFAULT_PHASE_GAP_ERROR_VS_K_PARAMS,
    LAST_CONVERGENCE_CYCLE_VS_K_PARAMS_PATH,
    LAST_INTERVAL_PER_VS_K_PARAMS_PATH,
    LAST_PHASE_GAP_ERROR_VS_K_PARAMS_PATH,
)


def duration_input_ms(label: str, key: str, default_ms: float, min_value: float = 0.0) -> float:
    unit_options = ["ms", "sec", "min"]
    unit = best_duration_unit(default_ms)
    value = default_ms / duration_unit_factor(unit)
    cols = st.columns([2, 1])
    number = cols[0].number_input(
        label,
        min_value=min_value / duration_unit_factor(unit),
        value=float(value),
        step=duration_step_for_unit(unit),
        key=f"{key}_value",
    )
    selected_unit = cols[1].selectbox(
        "unit",
        unit_options,
        index=select_index(unit_options, unit),
        key=f"{key}_unit",
        label_visibility="collapsed",
    )
    return float(number) * duration_unit_factor(selected_unit)

def best_duration_unit(ms: float) -> str:
    if ms >= 60_000 and ms % 60_000 == 0:
        return "min"
    if ms >= 1000 and ms % 1000 == 0:
        return "sec"
    return "ms"

def duration_unit_factor(unit: str) -> float:
    return {"ms": 1.0, "sec": 1000.0, "min": 60_000.0}[unit]

def duration_step_for_unit(unit: str) -> float:
    return {"ms": 1.0, "sec": 1.0, "min": 0.5}[unit]

def format_duration_ms(ms: float) -> str:
    if ms >= 60_000 and ms % 60_000 == 0:
        return f"{ms / 60_000:g} min"
    if ms >= 1000 and ms % 1000 == 0:
        return f"{ms / 1000:g} sec"
    return f"{ms:g} ms"

def format_percent_range(start_percent: float, end_percent: float) -> str:
    return f"{start_percent:g}% to {end_percent:g}%"

def load_last_interval_per_vs_k_params() -> dict[str, Any]:
    params = json.loads(json.dumps(DEFAULT_INTERVAL_PER_VS_K_PARAMS))
    if not LAST_INTERVAL_PER_VS_K_PARAMS_PATH.exists():
        return params
    try:
        saved = json.loads(LAST_INTERVAL_PER_VS_K_PARAMS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return params
    if isinstance(saved, dict):
        deep_update(params, saved)
    return params

def save_last_interval_per_vs_k_params(params: dict[str, Any]) -> None:
    LAST_INTERVAL_PER_VS_K_PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_INTERVAL_PER_VS_K_PARAMS_PATH.write_text(
        json.dumps(params, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

def load_last_convergence_cycle_vs_k_params() -> dict[str, Any]:
    params = json.loads(json.dumps(DEFAULT_CONVERGENCE_CYCLE_VS_K_PARAMS))
    if not LAST_CONVERGENCE_CYCLE_VS_K_PARAMS_PATH.exists():
        return params
    try:
        saved = json.loads(
            LAST_CONVERGENCE_CYCLE_VS_K_PARAMS_PATH.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return params
    if isinstance(saved, dict):
        deep_update(params, saved)
    return params

def save_last_convergence_cycle_vs_k_params(params: dict[str, Any]) -> None:
    LAST_CONVERGENCE_CYCLE_VS_K_PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_CONVERGENCE_CYCLE_VS_K_PARAMS_PATH.write_text(
        json.dumps(params, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

def load_last_phase_gap_error_vs_k_params() -> dict[str, Any]:
    params = json.loads(json.dumps(DEFAULT_PHASE_GAP_ERROR_VS_K_PARAMS))
    if not LAST_PHASE_GAP_ERROR_VS_K_PARAMS_PATH.exists():
        return params
    try:
        saved = json.loads(
            LAST_PHASE_GAP_ERROR_VS_K_PARAMS_PATH.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return params
    if isinstance(saved, dict):
        deep_update(params, saved)
    return params

def save_last_phase_gap_error_vs_k_params(params: dict[str, Any]) -> None:
    LAST_PHASE_GAP_ERROR_VS_K_PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_PHASE_GAP_ERROR_VS_K_PARAMS_PATH.write_text(
        json.dumps(params, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

def deep_update(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_update(target[key], value)
        else:
            target[key] = value

def select_index(options: list[str], value: str) -> int:
    try:
        return options.index(value)
    except ValueError:
        return 0

def build_k_values(k_start: float, k_stop: float, k_step: float) -> list[float]:
    values: list[float] = []
    current = float(k_start)
    stop = float(k_stop)
    step = float(k_step)
    while current <= stop + (step * 1e-9):
        values.append(round(current, 10))
        current += step
    return values

def build_lora_airtime_config(
    *,
    payload_bytes: int,
    spreading_factor: int,
    bandwidth_hz: int,
    coding_rate_denominator: int,
    preamble_symbols: int,
    explicit_header: bool,
    crc_enabled: bool,
    low_data_rate_optimize_mode: str,
) -> LoRaAirtimeConfig:
    return LoRaAirtimeConfig(
        payload_bytes=payload_bytes,
        spreading_factor=spreading_factor,
        bandwidth_hz=bandwidth_hz,
        coding_rate_denominator=coding_rate_denominator,
        preamble_symbols=preamble_symbols,
        explicit_header=explicit_header,
        crc_enabled=crc_enabled,
        low_data_rate_optimize=optional_bool(low_data_rate_optimize_mode),
    )

def optional_bool(value: object) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"auto", "none", "null"}:
        return None
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Unsupported optional bool value: {value}")

def symbol_duration_ms(config: LoRaAirtimeConfig) -> float:
    return (2**config.spreading_factor) / float(config.bandwidth_hz) * 1000.0

def format_graph_key(graph_key: dict[str, object]) -> str:
    if not graph_key:
        return ""
    return ", ".join(f"{key}={value}" for key, value in graph_key.items())

def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))

def read_aggregate_interval_per(db_path: Path) -> pd.DataFrame:
    db_path = interval_per_db_path(db_path)
    if not db_path.exists():
        return pd.DataFrame()
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            return pd.read_sql_query(
                """
                SELECT aggregate_set_id, coupling_function, coupling_strength,
                       per_percent_mean, per_percent_std, per_percent_min,
                       per_percent_max, expected_packets_sum,
                       actual_packets_sum, count
                FROM aggregate_interval_per
                ORDER BY aggregate_set_id, coupling_function, coupling_strength
                """,
                conn,
            )
        except sqlite3.Error:
            return pd.DataFrame()


def interval_per_db_path(db_path_or_graph_dir: Path) -> Path:
    path = Path(db_path_or_graph_dir)
    graph_dir = path if path.is_dir() else path.parent
    split_db = graph_dir / INTERVAL_PER_DB_NAME
    if split_db.exists():
        return split_db
    migrated_db = ensure_interval_per_db(graph_dir)
    if migrated_db.exists():
        return migrated_db
    if path.is_dir():
        return path / "graph_data.sqlite"
    return path


def read_aggregate_convergence_cycles(db_path: Path) -> pd.DataFrame:
    if not db_path.exists():
        return pd.DataFrame()
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            return pd.read_sql_query(
                """
                SELECT aggregate_set_id, coupling_function, coupling_strength,
                       convergence_cycle_mean, convergence_cycle_std,
                       convergence_cycle_min, convergence_cycle_max,
                       convergence_rate_percent, count, converged_count
                FROM aggregate_convergence_cycles
                ORDER BY aggregate_set_id, coupling_function, coupling_strength
                """,
                conn,
            )
        except sqlite3.Error:
            return pd.DataFrame()

def read_aggregate_phase_gap_error_points(db_path: Path) -> pd.DataFrame:
    if not db_path.exists():
        return pd.DataFrame()
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            return pd.read_sql_query(
                """
                SELECT aggregate_set_id, coupling_function, coupling_strength,
                       phase_gap_error_mean, phase_gap_error_std,
                       phase_gap_error_min, phase_gap_error_max,
                       phase_gap_error_ratio_mean, valid_count, count
                FROM aggregate_phase_gap_error_points
                ORDER BY aggregate_set_id, coupling_function, coupling_strength
                """,
                conn,
            )
        except sqlite3.Error:
            return pd.DataFrame()

def representative_pdf_path(graph_dir: Path) -> Path | None:
    db_path = graph_dir / "graph_data.sqlite"
    if not db_path.exists():
        return None
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            row = conn.execute(
                """
                SELECT relative_path
                FROM outputs
                WHERE output_type = 'representative_pdf'
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
        except sqlite3.Error:
            return None
    if row is None:
        return None
    path = graph_dir / str(row[0])
    return path if path.exists() else None

def current_plot_settings(db_path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    settings = dict(fallback)
    if not db_path.exists():
        return settings
    with closing(sqlite3.connect(db_path)) as conn:
        try:
            row = conn.execute(
                """
                SELECT settings_json
                FROM plot_settings
                WHERE settings_id = 'current'
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
        except sqlite3.Error:
            return settings
    if row is None:
        return settings
    try:
        saved_settings = json.loads(str(row[0]))
    except json.JSONDecodeError:
        return settings
    if isinstance(saved_settings, dict):
        settings.update(saved_settings)
    return settings

def render_pdf_preview(pdf_path: Path) -> None:
    pdf_bytes = pdf_path.read_bytes()
    encoded_pdf = base64.b64encode(pdf_bytes).decode("ascii")
    updated_at = pdf_path.stat().st_mtime_ns
    st.markdown(
        f"""
        <iframe
            src="data:application/pdf;base64,{encoded_pdf}#updated={updated_at}"
            width="100%"
            height="720"
            type="application/pdf"
            style="border: 1px solid #ddd; border-radius: 4px;"
        ></iframe>
        """,
        unsafe_allow_html=True,
    )

def save_plot_settings_and_output(
    db_path: Path,
    aggregate_set_id: str,
    plot_settings: dict[str, Any],
    output_path: Path,
    graph_dir: Path,
) -> None:
    now = pd.Timestamp.utcnow().replace(microsecond=0).isoformat()
    relative_output = str(output_path.resolve().relative_to(graph_dir.resolve()))
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO plot_settings
                (settings_id, aggregate_set_id, settings_json, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            ("current", aggregate_set_id, json.dumps(plot_settings, ensure_ascii=False), now),
        )
        conn.execute("DELETE FROM outputs WHERE output_type = ?", ("representative_pdf",))
        conn.execute(
            """
            INSERT INTO outputs
                (output_id, aggregate_set_id, output_type, relative_path, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("representative_pdf", aggregate_set_id, "representative_pdf", relative_output, now),
        )
        conn.commit()

def format_bytes(value: int | float) -> str:
    size = float(value)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


if __name__ == "__main__":
    main()

