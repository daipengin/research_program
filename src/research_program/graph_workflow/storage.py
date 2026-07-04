from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from research_program.io import sqlite_runs


GRAPH_RUNS_ROOT = Path("outputs") / "graph_runs"
GRAPH_TYPE_INTERVAL_PER_VS_K = "interval_per_vs_k"
RAW_RUN_DB_NAME = "raw_run.sqlite"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS graph_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS simulation_requests (
    request_id TEXT PRIMARY KEY,
    graph_type TEXT NOT NULL,
    graph_key TEXT NOT NULL,
    params_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    coupling_strength REAL NOT NULL,
    repeat_index INTEGER NOT NULL,
    status TEXT NOT NULL,
    raw_path TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_cycle_counts (
    run_id TEXT NOT NULL,
    cycle_index INTEGER NOT NULL,
    expected_packets INTEGER NOT NULL,
    actual_packets INTEGER NOT NULL,
    cumulative_expected_packets INTEGER NOT NULL,
    cumulative_actual_packets INTEGER NOT NULL,
    PRIMARY KEY (run_id, cycle_index)
);

CREATE TABLE IF NOT EXISTS run_interval_per (
    aggregate_set_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    coupling_function TEXT NOT NULL,
    coupling_strength REAL NOT NULL,
    interval_start_ms REAL NOT NULL,
    interval_end_ms REAL NOT NULL,
    interval_cycle_count INTEGER NOT NULL,
    expected_packets INTEGER NOT NULL,
    actual_packets INTEGER NOT NULL,
    per_percent REAL NOT NULL,
    PRIMARY KEY (aggregate_set_id, run_id)
);

CREATE TABLE IF NOT EXISTS aggregate_sets (
    aggregate_set_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    interval_start_ms REAL NOT NULL,
    interval_end_ms REAL NOT NULL,
    per_method TEXT NOT NULL,
    run_filter_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aggregate_interval_per (
    aggregate_set_id TEXT NOT NULL,
    coupling_function TEXT NOT NULL,
    coupling_strength REAL NOT NULL,
    per_percent_mean REAL NOT NULL,
    per_percent_std REAL,
    per_percent_min REAL,
    per_percent_max REAL,
    expected_packets_sum INTEGER,
    actual_packets_sum INTEGER,
    count INTEGER NOT NULL,
    PRIMARY KEY (aggregate_set_id, coupling_strength)
);

CREATE TABLE IF NOT EXISTS plot_settings (
    settings_id TEXT PRIMARY KEY,
    aggregate_set_id TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outputs (
    output_id TEXT PRIMARY KEY,
    aggregate_set_id TEXT,
    output_type TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
        return False


@dataclass(frozen=True)
class GraphJobSummary:
    graph_id: str
    graph_type: str
    graph_key: dict[str, Any]
    status: str
    path: Path
    created_at: str
    updated_at: str
    total_runs: int
    completed_runs: int
    aggregate_count: int


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_graph_runs_root() -> Path:
    GRAPH_RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    return GRAPH_RUNS_ROOT


def create_interval_per_vs_k_job(params: dict[str, Any]) -> GraphJobSummary:
    ensure_graph_runs_root()

    now = utc_now_iso()
    graph_id = _build_graph_id()
    graph_type = GRAPH_TYPE_INTERVAL_PER_VS_K
    graph_key = {"coupling_function": params["coupling_function"]}
    graph_dir = GRAPH_RUNS_ROOT / graph_type / graph_id
    figures_dir = graph_dir / "figures"
    logs_dir = graph_dir / "logs"

    graph_dir.mkdir(parents=True, exist_ok=False)
    figures_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    db_path = graph_dir / "graph_data.sqlite"
    _init_db(db_path)
    raw_conn = sqlite_runs.connect(graph_dir / RAW_RUN_DB_NAME)
    try:
        sqlite_runs.initialize(raw_conn)
    finally:
        raw_conn.close()

    total_runs = len(params["k_values"]) * int(params["runs_per_k"])
    aggregate_set_id = _interval_aggregate_set_id(
        float(params["interval_start_ms"]),
        float(params["interval_end_ms"]),
    )

    request = {
        "request_id": graph_id,
        "graph_type": graph_type,
        "graph_key": graph_key,
        "params": params,
        "created_at": now,
    }
    manifest = {
        "schema_version": 1,
        "graph_id": graph_id,
        "graph_type": graph_type,
        "graph_key": graph_key,
        "created_at": now,
        "updated_at": now,
        "status": "queued",
        "input": params,
        "simulation_base": params.get("simulation_base", {}),
        "sweep": {
            "k_values": params["k_values"],
            "runs_per_k": params["runs_per_k"],
        },
        "outputs": {},
        "run_summary": {
            "total_runs": total_runs,
            "completed_runs": 0,
        },
        "history": [{"event_type": "job_created", "created_at": now}],
    }
    status = {
        "job_id": graph_id,
        "status": "queued",
        "cancel_requested": False,
        "cancel_requested_at": None,
        "cancel_reason": "",
        "total_runs": total_runs,
        "completed_runs": 0,
        "current_run_id": "",
        "started_at": None,
        "updated_at": now,
        "finished_at": None,
        "estimated_finish_at": None,
        "error": "",
    }

    _write_json(graph_dir / "manifest.json", manifest)
    _write_json(graph_dir / "status.json", status)
    _write_json(graph_dir / "requests.json", request)

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO simulation_requests
                (request_id, graph_type, graph_key, params_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                graph_id,
                graph_type,
                json.dumps(graph_key, ensure_ascii=False),
                json.dumps(params, ensure_ascii=False),
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO aggregate_sets
                (aggregate_set_id, label, interval_start_ms, interval_end_ms,
                 per_method, run_filter_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aggregate_set_id,
                f"{params['interval_start_ms']} to {params['interval_end_ms']} ms",
                params["interval_start_ms"],
                params["interval_end_ms"],
                params.get("per_method", "interval_packet_error_rate"),
                "{}",
                now,
            ),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO plot_settings
                (settings_id, aggregate_set_id, settings_json, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                "current",
                aggregate_set_id,
                json.dumps(params.get("plot_settings", {}), ensure_ascii=False),
                now,
            ),
        )

    _insert_meta(
        db_path,
        {
            "schema_version": 1,
            "graph_id": graph_id,
            "graph_type": graph_type,
            "graph_key": graph_key,
            "input_params": params,
            "simulation_base": params.get("simulation_base", {}),
            "sweep": {
                "k_values": params["k_values"],
                "runs_per_k": params["runs_per_k"],
            },
            "created_at": now,
            "storage_policy": {
                "raw_data": "raw_run_sqlite",
                "raw_run_sqlite": RAW_RUN_DB_NAME,
                "sqlite": "metadata_intermediate_aggregate",
            },
        },
    )

    return load_graph_job(graph_dir)


def load_graph_job(graph_dir: Path) -> GraphJobSummary:
    manifest = _read_json(graph_dir / "manifest.json")
    status = _read_json(graph_dir / "status.json")
    db_path = graph_dir / "graph_data.sqlite"
    run_summary = manifest.get("run_summary", {})

    return GraphJobSummary(
        graph_id=manifest.get("graph_id", graph_dir.name),
        graph_type=manifest.get("graph_type", graph_dir.parent.name),
        graph_key=manifest.get("graph_key", {}),
        status=status.get("status", manifest.get("status", "unknown")),
        path=graph_dir,
        created_at=manifest.get("created_at", ""),
        updated_at=status.get("updated_at", manifest.get("updated_at", "")),
        total_runs=int(status.get("total_runs", run_summary.get("total_runs", 0)) or 0),
        completed_runs=int(
            status.get("completed_runs", run_summary.get("completed_runs", 0)) or 0
        ),
        aggregate_count=_count_rows(db_path, "aggregate_sets"),
    )


def list_graph_jobs() -> list[GraphJobSummary]:
    root = ensure_graph_runs_root()
    jobs: list[GraphJobSummary] = []
    for graph_type_dir in sorted(root.iterdir()):
        if not graph_type_dir.is_dir():
            continue
        for graph_dir in sorted(graph_type_dir.iterdir(), reverse=True):
            if not graph_dir.is_dir():
                continue
            if not (graph_dir / "manifest.json").exists():
                continue
            if not (graph_dir / "graph_data.sqlite").exists():
                continue
            jobs.append(load_graph_job(graph_dir))
    return jobs


def get_storage_overview() -> dict[str, Any]:
    root = ensure_graph_runs_root()
    jobs = list_graph_jobs()
    db_count = sum(1 for _ in root.rglob("graph_data.sqlite"))
    raw_db_count = sum(1 for _ in root.rglob(RAW_RUN_DB_NAME))
    raw_dirs = sum(1 for path in root.rglob("raw") if path.is_dir())
    return {
        "root": str(root),
        "job_count": len(jobs),
        "sqlite_count": db_count,
        "raw_sqlite_count": raw_db_count,
        "raw_dir_count": raw_dirs,
    }


def request_cancel_graph_job(graph_dir: str | Path, reason: str = "requested from GUI") -> None:
    graph_path = Path(graph_dir)
    status_path = graph_path / "status.json"
    manifest_path = graph_path / "manifest.json"
    now = utc_now_iso()
    status = _read_json(status_path)
    current_status = str(status.get("status", "unknown"))

    if current_status == "queued":
        status.update(
            {
                "status": "cancelled",
                "cancel_requested": True,
                "cancel_requested_at": now,
                "cancel_reason": reason,
                "updated_at": now,
                "finished_at": now,
            }
        )
        manifest = _read_json(manifest_path)
        manifest.update({"status": "cancelled", "updated_at": now})
        _write_json(manifest_path, manifest)
    else:
        status.update(
            {
                "cancel_requested": True,
                "cancel_requested_at": status.get("cancel_requested_at") or now,
                "cancel_reason": reason,
                "updated_at": now,
            }
        )
        if current_status not in {"completed", "failed", "cancelled"}:
            status["status"] = "cancel_requested"
    _write_json(status_path, status)

    db_path = graph_path / "graph_data.sqlite"
    if db_path.exists():
        with _connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO history (event_type, detail_json, created_at)
                VALUES (?, ?, ?)
                """,
                (
                    "cancel_requested",
                    json.dumps({"reason": reason}, ensure_ascii=False),
                    now,
                ),
            )


def delete_graph_job(graph_dir: str | Path) -> Path:
    root = ensure_graph_runs_root().resolve()
    target = Path(graph_dir).resolve()

    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"delete target must be under {root}: {target}") from exc

    if len(relative.parts) != 2:
        raise ValueError(
            "delete target must be a graph folder like "
            "outputs/graph_runs/<graph_type>/<graph_id>"
        )
    if not (target / "manifest.json").exists() or not (target / "graph_data.sqlite").exists():
        raise ValueError(f"delete target is not a graph-first folder: {target}")

    shutil.rmtree(target)
    return target


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return default or {}
    return json.loads(path.read_text(encoding="utf-8"))


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(db_path: Path) -> None:
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)


def _insert_meta(db_path: Path, data: dict[str, Any]) -> None:
    rows = [(key, json.dumps(value, ensure_ascii=False)) for key, value in data.items()]
    with _connect(db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO graph_meta (key, value) VALUES (?, ?)",
            rows,
        )
        conn.execute(
            """
            INSERT INTO history (event_type, detail_json, created_at)
            VALUES (?, ?, ?)
            """,
            ("job_created", json.dumps(data, ensure_ascii=False), utc_now_iso()),
        )


def _count_rows(db_path: Path, table: str) -> int:
    if not db_path.exists():
        return 0
    with _connect(db_path) as conn:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row["n"])


def _build_graph_id() -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{timestamp}_{uuid4().hex[:8]}"


def _interval_aggregate_set_id(interval_start_ms: float, interval_end_ms: float) -> str:
    start = int(interval_start_ms)
    end = int(interval_end_ms)
    return f"interval_{start}_to_{end}"
