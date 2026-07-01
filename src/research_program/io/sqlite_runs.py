from __future__ import annotations

from pathlib import Path
import sqlite3
import time
from typing import Any, Iterable

import pandas as pd


SQLITE_RUN_EXTENSIONS = {".sqlite", ".sqlite3", ".db"}


RUN_COLUMNS = [
    "run_id",
    "coupling_strength",
    "strength_ratio",
    "coupling_function",
    "cycle_time",
    "listening_rate",
    "start_timing_mode",
    "random_sampling_method",
    "random_seed",
    "random_run_index",
    "random_start_min",
    "random_start_max",
    "start_step",
    "start_step_count",
    "random_start_candidate_count",
    "selected_start_times",
    "simulation_mode",
    "save_asleep_log",
    "save_carrier_sense_log",
    "carrier_sense_duration_ms",
    "transmission_time_ms",
    "lora_payload_bytes",
    "lora_spreading_factor",
    "lora_bandwidth_hz",
    "lora_coding_rate_denominator",
    "lora_preamble_symbols",
    "lora_explicit_header",
    "lora_crc_enabled",
    "lora_low_data_rate_optimize",
    "tags",
    "ranges",
]


def is_sqlite_run_store(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SQLITE_RUN_EXTENSIONS


def sqlite_record_key(sqlite_path: str | Path, run_id: str) -> str:
    return f"{Path(sqlite_path).resolve()}::{run_id}"


def parse_sqlite_record_key(record_key: str) -> tuple[Path, str] | None:
    if "::" not in record_key:
        return None
    sqlite_path_text, run_id = record_key.rsplit("::", 1)
    sqlite_path = Path(sqlite_path_text)
    if not is_sqlite_run_store(sqlite_path):
        return None
    return sqlite_path, run_id


def connect(sqlite_path: str | Path) -> sqlite3.Connection:
    path = Path(sqlite_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60.0)
    conn.execute("PRAGMA busy_timeout = 60000")
    for attempt in range(20):
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt >= 19:
                raise
            time.sleep(min(0.05 * (attempt + 1), 1.0))
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    schema_sql = """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            coupling_strength INTEGER,
            strength_ratio REAL,
            coupling_function TEXT,
            cycle_time REAL,
            listening_rate REAL,
            start_timing_mode TEXT,
            random_sampling_method TEXT,
            random_seed INTEGER,
            random_run_index INTEGER,
            random_start_min INTEGER,
            random_start_max INTEGER,
            start_step INTEGER,
            start_step_count INTEGER,
            random_start_candidate_count INTEGER,
            selected_start_times TEXT,
            simulation_mode TEXT,
            save_asleep_log TEXT,
            save_carrier_sense_log TEXT,
            carrier_sense_duration_ms REAL,
            transmission_time_ms REAL,
            lora_payload_bytes INTEGER,
            lora_spreading_factor INTEGER,
            lora_bandwidth_hz INTEGER,
            lora_coding_rate_denominator INTEGER,
            lora_preamble_symbols INTEGER,
            lora_explicit_header TEXT,
            lora_crc_enabled TEXT,
            lora_low_data_rate_optimize TEXT,
            tags TEXT,
            ranges TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS send_log (
            run_id TEXT NOT NULL,
            time REAL NOT NULL,
            oscillator_id TEXT NOT NULL,
            send_count INTEGER,
            transmission_end_time REAL,
            transmission_time_ms REAL
        );
        CREATE INDEX IF NOT EXISTS idx_send_log_run
            ON send_log(run_id);

        CREATE TABLE IF NOT EXISTS asleep_log (
            run_id TEXT NOT NULL,
            current_time REAL NOT NULL,
            next_time REAL NOT NULL,
            oscillator_id TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_asleep_log_run
            ON asleep_log(run_id);

        CREATE TABLE IF NOT EXISTS carrier_sense_log (
            run_id TEXT NOT NULL,
            time REAL NOT NULL,
            oscillator_id TEXT NOT NULL,
            action TEXT NOT NULL,
            carrier_sense_start REAL,
            carrier_sense_end REAL,
            blocking_oscillator_id TEXT,
            blocking_transmission_start REAL,
            blocking_transmission_end REAL
        );
        CREATE INDEX IF NOT EXISTS idx_carrier_sense_log_run
            ON carrier_sense_log(run_id);

        CREATE TABLE IF NOT EXISTS calculated_cycle_data (
            run_id TEXT NOT NULL,
            cycle_index INTEGER NOT NULL,
            cycle_start_time REAL NOT NULL,
            is_original_cycle INTEGER NOT NULL,
            reference_id INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cycle_data_run_cycle
            ON calculated_cycle_data(run_id, cycle_index);

        CREATE TABLE IF NOT EXISTS phase_gap_error (
            run_id TEXT NOT NULL,
            cycle_index INTEGER NOT NULL,
            mean_abs_diff_from_ideal_phase_gap REAL,
            mean_abs_diff_from_ideal_phase_gap_ratio REAL
        );
        CREATE INDEX IF NOT EXISTS idx_phase_gap_error_run_cycle
            ON phase_gap_error(run_id, cycle_index);
        """
    for attempt in range(20):
        try:
            conn.executescript(schema_sql)
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt >= 19:
                raise
            time.sleep(min(0.05 * (attempt + 1), 1.0))


def delete_run(conn: sqlite3.Connection, run_id: str) -> None:
    with conn:
        for table_name in [
            "runs",
            "send_log",
            "asleep_log",
            "carrier_sense_log",
            "calculated_cycle_data",
            "phase_gap_error",
        ]:
            conn.execute(f"DELETE FROM {table_name} WHERE run_id = ?", (run_id,))


def run_metadata_row(config: Any) -> dict[str, Any]:
    ranges_as_text = "|".join(
        f"{start}:{end}:{device_id}"
        for start, end, device_id in config.ranges
    )
    selected_start_times_as_text = ";".join(str(start) for start, _, _ in config.ranges)
    tags_as_text = ";".join(config.tags)
    is_random_start = config.start_timing_mode == "random"
    random_start_min = 0 if is_random_start else None
    random_start_max = (
        int(config.start_step) * int(config.start_step_count)
        if is_random_start and config.start_step is not None and config.start_step_count is not None
        else None
    )
    random_start_candidate_count = (
        int(config.start_step_count) + 1
        if is_random_start and config.start_step_count is not None
        else None
    )
    return {
        "run_id": config.run_id,
        "coupling_strength": config.coupling_strength,
        "strength_ratio": config.strength_ratio,
        "coupling_function": config.coupling_function.value,
        "cycle_time": config.cycle_time,
        "listening_rate": config.listening_rate,
        "start_timing_mode": config.start_timing_mode,
        "random_sampling_method": config.random_sampling_method if is_random_start else "",
        "random_seed": config.random_seed,
        "random_run_index": config.random_run_index,
        "random_start_min": random_start_min,
        "random_start_max": random_start_max,
        "start_step": config.start_step,
        "start_step_count": config.start_step_count,
        "random_start_candidate_count": random_start_candidate_count,
        "selected_start_times": selected_start_times_as_text,
        "simulation_mode": config.simulation_mode,
        "save_asleep_log": str(config.save_asleep_log),
        "save_carrier_sense_log": str(config.save_carrier_sense_log),
        "carrier_sense_duration_ms": config.carrier_sense_duration_ms if config.simulation_mode == "per_measurement" else 0.0,
        "transmission_time_ms": config.transmission_time_ms,
        "lora_payload_bytes": config.lora_payload_bytes,
        "lora_spreading_factor": config.lora_spreading_factor,
        "lora_bandwidth_hz": config.lora_bandwidth_hz,
        "lora_coding_rate_denominator": config.lora_coding_rate_denominator,
        "lora_preamble_symbols": config.lora_preamble_symbols,
        "lora_explicit_header": str(config.lora_explicit_header),
        "lora_crc_enabled": str(config.lora_crc_enabled),
        "lora_low_data_rate_optimize": "" if config.lora_low_data_rate_optimize is None else str(config.lora_low_data_rate_optimize),
        "tags": tags_as_text,
        "ranges": ranges_as_text,
    }


def insert_run_metadata(conn: sqlite3.Connection, metadata: dict[str, Any]) -> None:
    columns = RUN_COLUMNS
    placeholders = ", ".join("?" for _ in columns)
    with conn:
        conn.execute(
            f"INSERT OR REPLACE INTO runs ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(metadata.get(column) for column in columns),
        )


def insert_rows(
    conn: sqlite3.Connection,
    table_name: str,
    columns: Iterable[str],
    rows: Iterable[Iterable[Any]],
) -> None:
    columns = list(columns)
    placeholders = ", ".join("?" for _ in columns)
    with conn:
        conn.executemany(
            f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})",
            rows,
        )


def replace_dataframe(
    conn: sqlite3.Connection,
    table_name: str,
    run_id: str,
    df: pd.DataFrame,
) -> None:
    with conn:
        conn.execute(f"DELETE FROM {table_name} WHERE run_id = ?", (run_id,))
    if df.empty:
        return
    out = df.copy()
    out.insert(0, "run_id", run_id)
    out.to_sql(table_name, conn, if_exists="append", index=False)


def table_frame(
    conn: sqlite3.Connection,
    table_name: str,
    run_id: str,
    order_by: str = "",
) -> pd.DataFrame:
    order_sql = f" ORDER BY {order_by}" if order_by else ""
    return pd.read_sql_query(
        f"SELECT * FROM {table_name} WHERE run_id = ?{order_sql}",
        conn,
        params=(run_id,),
    )


def list_run_rows(sqlite_path: str | Path) -> list[dict[str, Any]]:
    path = Path(sqlite_path)
    if not path.exists():
        return []
    with connect(path) as conn:
        initialize(conn)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM runs ORDER BY run_id").fetchall()
        return [dict(row) for row in rows]


def available_files_for_run(sqlite_path: str | Path, run_id: str) -> tuple[str, ...]:
    return available_files_for_runs(sqlite_path, [run_id]).get(
        run_id,
        ("metadata.csv", "send_log.csv"),
    )


def available_files_for_runs(
    sqlite_path: str | Path,
    run_ids: Iterable[str],
) -> dict[str, tuple[str, ...]]:
    run_id_list = [str(run_id) for run_id in run_ids]
    files_by_run_id = {
        run_id: ["metadata.csv", "send_log.csv"]
        for run_id in run_id_list
    }
    if not files_by_run_id:
        return {}

    path = Path(sqlite_path)
    with connect(path) as conn:
        initialize(conn)
        checks = [
            ("asleep_log", "asleep_log.csv"),
            ("carrier_sense_log", "carrier_sense_log.csv"),
            ("calculated_cycle_data", "calculated_Cycle_data.csv"),
            ("phase_gap_error", "phase_gap_error.csv"),
        ]
        for table_name, filename in checks:
            rows = conn.execute(f"SELECT DISTINCT run_id FROM {table_name}").fetchall()
            for row in rows:
                row_run_id = str(row[0])
                if row_run_id in files_by_run_id:
                    files_by_run_id[row_run_id].append(filename)
    return {run_id: tuple(files) for run_id, files in files_by_run_id.items()}


def export_run_to_directory(sqlite_path: str | Path, run_id: str, output_dir: str | Path) -> None:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with connect(sqlite_path) as conn:
        initialize(conn)
        metadata_df = table_frame(conn, "runs", run_id)
        if metadata_df.empty:
            raise ValueError(f"run_id not found in sqlite store: {run_id}")
        metadata_df = metadata_df[[column for column in RUN_COLUMNS if column in metadata_df.columns]]
        # Keep the historical typo column for existing readers.
        if "strength_ratio" in metadata_df.columns and "strengrh_ratio" not in metadata_df.columns:
            metadata_df.insert(2, "strengrh_ratio", metadata_df["strength_ratio"])
        metadata_df.to_csv(out_dir / "metadata.csv", index=False)

        export_specs = [
            (
                "send_log",
                "send_log.csv",
                ["time", "oscillator_id", "send_count", "transmission_end_time", "transmission_time_ms"],
            ),
            (
                "asleep_log",
                "asleep_log.csv",
                ["current_time", "next_time", "oscillator_id"],
            ),
            (
                "carrier_sense_log",
                "carrier_sense_log.csv",
                [
                    "time",
                    "oscillator_id",
                    "action",
                    "carrier_sense_start",
                    "carrier_sense_end",
                    "blocking_oscillator_id",
                    "blocking_transmission_start",
                    "blocking_transmission_end",
                ],
            ),
            (
                "calculated_cycle_data",
                "calculated_Cycle_data.csv",
                ["cycle_index", "cycle_start_time", "is_original_cycle", "reference_id"],
            ),
            (
                "phase_gap_error",
                "phase_gap_error.csv",
                [
                    "cycle_index",
                    "mean_abs_diff_from_ideal_phase_gap",
                    "mean_abs_diff_from_ideal_phase_gap_ratio",
                ],
            ),
        ]
        for table_name, filename, columns in export_specs:
            order_by = {
                "send_log": "time, oscillator_id",
                "asleep_log": "current_time, oscillator_id",
                "carrier_sense_log": "time, oscillator_id",
                "calculated_cycle_data": "cycle_index",
                "phase_gap_error": "cycle_index",
            }.get(table_name, "")
            df = table_frame(conn, table_name, run_id, order_by=order_by)
            if df.empty and table_name != "send_log":
                continue
            export_columns = [column for column in columns if column in df.columns]
            df[export_columns].to_csv(out_dir / filename, index=False)
