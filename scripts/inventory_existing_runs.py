from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from research_program.analysis import calculate_cycle_data
from research_program.analysis import calculate_phase_gap_error
from research_program.io import sqlite_runs


DEFAULT_SCAN_ROOTS = (
    PROJECT_ROOT / "data" / "runs",
    PROJECT_ROOT / "data" / "run",
    PROJECT_ROOT / "outputs" / "graph_runs",
    PROJECT_ROOT / "archive",
)
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "results" / "inventory" / "existing_runs_inventory.csv"
UNKNOWN = "unknown"
READABILITY_SAMPLE_ROWS = 100
MAX_DIRECT_SQLITE_BYTES = 512 * 1024 * 1024


CSV_COLUMNS = [
    "path",
    "created_at",
    "format",
    "run_id",
    "source",
    "coupling_function",
    "k",
    "device_count",
    "cycle_time",
    "listening_rate",
    "trial_count",
    "trial_index",
    "simulation_length_cycles",
    "duration",
    "send_log_rows",
    "has_cycle_cache",
    "analysis_readable",
    "analysis_readable_detail",
]


@dataclass(frozen=True)
class InventoryRow:
    path: str
    created_at: str
    format: str
    run_id: str = UNKNOWN
    source: str = UNKNOWN
    coupling_function: str = UNKNOWN
    k: str = UNKNOWN
    device_count: str = UNKNOWN
    cycle_time: str = UNKNOWN
    listening_rate: str = UNKNOWN
    trial_count: str = UNKNOWN
    trial_index: str = UNKNOWN
    simulation_length_cycles: str = UNKNOWN
    duration: str = UNKNOWN
    send_log_rows: str = UNKNOWN
    has_cycle_cache: str = UNKNOWN
    analysis_readable: str = "no"
    analysis_readable_detail: str = UNKNOWN

    def as_dict(self) -> dict[str, str]:
        return {column: str(getattr(self, column)) for column in CSV_COLUMNS}


@dataclass(frozen=True)
class RawRunStats:
    run_ids_by_k: dict[str, list[str]]
    send_log_rows_by_run_id: dict[str, int]
    cycle_cache_run_ids: set[str]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inventory existing simulation runs into a paper-trackable CSV."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Output CSV path. Default: results/inventory/existing_runs_inventory.csv",
    )
    parser.add_argument(
        "roots",
        nargs="*",
        type=Path,
        help="Optional scan roots. Defaults to data/runs, data/run, outputs/graph_runs, archive.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print scan progress to stderr.")
    args = parser.parse_args()

    roots = tuple(resolve_path(root) for root in args.roots) if args.roots else DEFAULT_SCAN_ROOTS
    rows = inventory_roots(roots, verbose=args.verbose)
    write_inventory(args.output, rows)
    print(f"wrote {len(rows)} rows: {args.output}")
    return 0


def inventory_roots(roots: Iterable[Path], *, verbose: bool = False) -> list[InventoryRow]:
    rows: list[InventoryRow] = []
    seen_csv_dirs: set[Path] = set()
    seen_sqlite_paths: set[Path] = set()

    for root in roots:
        root = resolve_path(root)
        if not root.exists():
            continue
        if verbose:
            print(f"scan root: {project_relative(root)}", file=sys.stderr, flush=True)
        for run_dir in find_csv_run_dirs(root):
            if verbose:
                print(f"csv run: {project_relative(run_dir)}", file=sys.stderr, flush=True)
            resolved = run_dir.resolve()
            if resolved in seen_csv_dirs:
                continue
            seen_csv_dirs.add(resolved)
            rows.append(inventory_csv_run_dir(run_dir, root))

        for sqlite_path in find_sqlite_run_stores(root):
            if verbose:
                print(f"sqlite: {project_relative(sqlite_path)}", file=sys.stderr, flush=True)
            resolved = sqlite_path.resolve()
            if resolved in seen_sqlite_paths:
                continue
            seen_sqlite_paths.add(resolved)
            rows.extend(inventory_sqlite_run_store(sqlite_path, root))

    rows.sort(key=lambda row: (row.path, row.run_id))
    return rows


def find_csv_run_dirs(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_dir():
            continue
        has_metadata = (path / "metadata.csv").exists()
        has_send_log = (path / "send_log.csv").exists()
        if has_metadata or has_send_log:
            yield path


def find_sqlite_run_stores(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name.endswith(("-wal", "-shm", "-journal")):
            continue
        if path.suffix.lower() not in sqlite_runs.SQLITE_RUN_EXTENSIONS:
            continue
        if path.name == "raw_run.sqlite" and (path.parent / "graph_data.sqlite").exists():
            continue
        if path.name == "graph_data.sqlite" and (path.parent / "manifest.json").exists():
            yield path
            continue
        if sqlite_inventory_kind(path) != "":
            yield path


def inventory_csv_run_dir(run_dir: Path, root: Path) -> InventoryRow:
    metadata_path = run_dir / "metadata.csv"
    send_log_path = run_dir / "send_log.csv"
    metadata = read_csv_metadata(metadata_path)
    tags = tags_from_metadata(metadata)
    created_at = created_at_iso(run_dir)
    cycle_time = first_known(metadata, "cycle_time")
    duration = duration_from_metadata(metadata)
    analysis_ok, analysis_detail, cycle_count = try_csv_analysis_read(run_dir, metadata, tags)

    return InventoryRow(
        path=project_relative(run_dir),
        created_at=created_at,
        format=csv_format_label(metadata_path, send_log_path),
        run_id=first_known(metadata, "run_id", default=run_dir.name),
        source=project_relative(root),
        coupling_function=first_known(metadata, "coupling_function"),
        k=first_known(metadata, "coupling_strength", "K"),
        device_count=device_count_from_metadata_or_tags(metadata, tags),
        cycle_time=cycle_time,
        listening_rate=first_known(metadata, "listening_rate"),
        trial_count=UNKNOWN,
        trial_index=first_known(metadata, "random_run_index"),
        simulation_length_cycles=cycle_count
        if cycle_count != UNKNOWN
        else cycles_from_duration(duration, cycle_time),
        duration=duration,
        send_log_rows=csv_send_log_rows(send_log_path),
        has_cycle_cache="yes" if (run_dir / "calculated_Cycle_data.csv").exists() else "no",
        analysis_readable="yes" if analysis_ok else "no",
        analysis_readable_detail=analysis_detail,
    )


def inventory_sqlite_run_store(sqlite_path: Path, root: Path) -> list[InventoryRow]:
    if sqlite_path.name == "graph_data.sqlite" and (sqlite_path.parent / "manifest.json").exists():
        return inventory_graph_manifest(sqlite_path.parent, root)

    inventory_kind = sqlite_inventory_kind(sqlite_path)
    if inventory_kind == "graph_metadata_sqlite":
        return inventory_graph_metadata_db(sqlite_path, root)

    graph_repeat_counts = repeat_counts_for_graph(sqlite_path)
    rows: list[InventoryRow] = []
    try:
        with sqlite3.connect(sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            run_rows = conn.execute("SELECT * FROM runs ORDER BY run_id").fetchall()
            readable_by_run = sqlite_analysis_readability(conn)
            for row in run_rows:
                metadata = dict(row)
                run_id = text_or_unknown(metadata.get("run_id"))
                tags = tags_from_metadata(metadata)
                cycle_time = first_known(metadata, "cycle_time")
                duration = duration_from_ranges(metadata.get("ranges"))
                readable, detail, send_log_rows, has_cycle_cache = readable_by_run.get(
                    run_id,
                    (False, "not checked", UNKNOWN, UNKNOWN),
                )
                trial_count = graph_repeat_counts.get(run_id, UNKNOWN)
                if trial_count == UNKNOWN:
                    trial_count = UNKNOWN
                else:
                    trial_count = str(trial_count)
                rows.append(
                    InventoryRow(
                        path=f"{project_relative(sqlite_path)}::{run_id}",
                        created_at=created_at_iso(sqlite_path),
                        format="current_sqlite",
                        run_id=run_id,
                        source=project_relative(root),
                        coupling_function=first_known(metadata, "coupling_function"),
                        k=first_known(metadata, "coupling_strength", "K"),
                        device_count=device_count_from_metadata_or_tags(metadata, tags),
                        cycle_time=cycle_time,
                        listening_rate=first_known(metadata, "listening_rate"),
                        trial_count=trial_count,
                        trial_index=first_known(metadata, "random_run_index"),
                        simulation_length_cycles=cycles_from_duration(duration, cycle_time),
                        duration=duration,
                        send_log_rows=send_log_rows,
                        has_cycle_cache=has_cycle_cache,
                        analysis_readable="yes" if readable else "no",
                        analysis_readable_detail=detail,
                    )
                )
    except Exception as exc:
        return [
            InventoryRow(
                path=project_relative(sqlite_path),
                created_at=created_at_iso(sqlite_path),
                format="unknown",
                source=project_relative(root),
                analysis_readable="no",
                analysis_readable_detail=f"sqlite read failed: {exc}",
            )
        ]

    return rows


def inventory_graph_manifest(graph_dir: Path, root: Path) -> list[InventoryRow]:
    manifest = read_json_file(graph_dir / "manifest.json")
    params = manifest.get("input", {}) if isinstance(manifest, dict) else {}
    simulation_base = params.get("simulation_base", {}) if isinstance(params, dict) else {}
    sweep = manifest.get("sweep", {}) if isinstance(manifest, dict) else {}
    k_values = sweep.get("k_values") or params.get("k_values") or []
    runs_per_k = int(sweep.get("runs_per_k") or params.get("runs_per_k") or 1)
    graph_type = text_or_unknown(manifest.get("graph_type"))
    graph_id = text_or_unknown(manifest.get("graph_id", graph_dir.name))
    coupling_function = graph_coupling_function(
        {
            **(params if isinstance(params, dict) else {}),
            "graph_key": manifest.get("graph_key", {}),
        }
    )
    cycle_time = first_known(simulation_base, "cycle_time")
    duration = duration_from_metadata(simulation_base)
    created_at = text_or_unknown(manifest.get("created_at", created_at_iso(graph_dir)))
    graph_rel = project_relative(graph_dir)
    source_label = f"{project_relative(root)} ({graph_type})"
    device_count = first_known(simulation_base, "device_count")
    listening_rate = first_known(simulation_base, "listening_rate")
    simulation_length_cycles = cycles_from_duration(duration, cycle_time)
    runs_per_k_text = str(runs_per_k)
    raw_stats = graph_run_stats(graph_dir)
    rows: list[InventoryRow] = []
    k_value_list = k_values if isinstance(k_values, list) else []
    for k_value in k_value_list:
        k_text = text_or_unknown(k_value)
        raw_run_ids = raw_stats.run_ids_by_k.get(k_text, [])
        for repeat_index in range(runs_per_k):
            run_id = (
                raw_run_ids[repeat_index]
                if repeat_index < len(raw_run_ids)
                else f"manifest_k_{k_value}_repeat_{repeat_index}"
            )
            send_log_rows = raw_stats.send_log_rows_by_run_id.get(run_id, 0)
            has_cycle_cache = run_id in raw_stats.cycle_cache_run_ids
            rows.append(
                InventoryRow(
                    path=f"{graph_rel}::{run_id}",
                    created_at=created_at,
                    format="current_graph_manifest",
                    run_id=run_id,
                    source=source_label,
                    coupling_function=coupling_function,
                    k=k_text,
                    device_count=device_count,
                    cycle_time=cycle_time,
                    listening_rate=listening_rate,
                    trial_count=runs_per_k_text,
                    trial_index=str(repeat_index),
                    simulation_length_cycles=simulation_length_cycles,
                    duration=duration,
                    send_log_rows=str(send_log_rows) if send_log_rows else "0",
                    has_cycle_cache="yes" if has_cycle_cache else "no",
                    analysis_readable="yes" if send_log_rows > 0 else "no",
                    analysis_readable_detail=(
                        "send_log-derived cycle counts found for this run"
                        if send_log_rows > 0
                        else "manifest inventory; no send_log-derived rows found for run"
                    ),
                )
            )
    if rows:
        return rows
    return [
        InventoryRow(
            path=graph_rel,
            created_at=created_at,
            format="current_graph_manifest",
            run_id=UNKNOWN,
            source=source_label,
            coupling_function=coupling_function,
            cycle_time=cycle_time,
            listening_rate=listening_rate,
            simulation_length_cycles=simulation_length_cycles,
            duration=duration,
            send_log_rows=UNKNOWN,
            has_cycle_cache=UNKNOWN,
            analysis_readable="no",
            analysis_readable_detail="manifest did not contain k_values/runs_per_k",
        )
    ]


def inventory_graph_metadata_db(sqlite_path: Path, root: Path) -> list[InventoryRow]:
    try:
        with sqlite3.connect(sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            run_rows = conn.execute("SELECT * FROM runs ORDER BY run_id").fetchall()
            params = graph_input_params(conn)
    except Exception as exc:
        return [
            InventoryRow(
                path=project_relative(sqlite_path),
                created_at=created_at_iso(sqlite_path),
                format="unknown",
                source=project_relative(root),
                analysis_readable="no",
                analysis_readable_detail=f"graph metadata read failed: {exc}",
            )
        ]

    simulation_base = params.get("simulation_base", {}) if isinstance(params, dict) else {}
    runs_per_k = params.get("runs_per_k", UNKNOWN) if isinstance(params, dict) else UNKNOWN
    rows: list[InventoryRow] = []
    for row in run_rows:
        metadata = dict(row)
        metadata_json = parse_json_object(metadata.get("metadata_json"))
        merged = {**simulation_base, **metadata_json, **metadata}
        cycle_time = first_known(merged, "cycle_time")
        duration = duration_from_metadata(merged)
        rows.append(
            InventoryRow(
                path=f"{project_relative(sqlite_path)}::{text_or_unknown(metadata.get('run_id'))}",
                created_at=created_at_iso(sqlite_path),
                format="current_graph_metadata",
                run_id=first_known(metadata, "run_id"),
                source=project_relative(root),
                coupling_function=first_known(merged, "coupling_function", default=graph_coupling_function(params)),
                k=first_known(merged, "coupling_strength", "K"),
                device_count=first_known(merged, "device_count"),
                cycle_time=cycle_time,
                listening_rate=first_known(merged, "listening_rate"),
                trial_count=text_or_unknown(runs_per_k),
                trial_index=first_known(merged, "repeat_index", "random_run_index"),
                simulation_length_cycles=cycles_from_duration(duration, cycle_time),
                duration=duration,
                send_log_rows=UNKNOWN,
                has_cycle_cache=UNKNOWN,
                analysis_readable="no",
                analysis_readable_detail="graph metadata only; raw send_log is not directly available here",
            )
        )
    return rows


def repeat_counts_for_graph(sqlite_path: Path) -> dict[str, int]:
    graph_db = sqlite_path.parent / "graph_data.sqlite"
    if not graph_db.exists():
        return {}
    try:
        with sqlite3.connect(graph_db) as conn:
            conn.row_factory = sqlite3.Row
            graph_rows = conn.execute(
                "SELECT run_id, coupling_strength FROM runs WHERE status = 'completed'"
            ).fetchall()
    except sqlite3.Error:
        return {}

    counts = Counter(float(row["coupling_strength"]) for row in graph_rows)
    return {
        str(row["run_id"]): int(counts[float(row["coupling_strength"])])
        for row in graph_rows
    }


def sqlite_analysis_readability(conn: sqlite3.Connection) -> dict[str, tuple[bool, str, str, str]]:
    result: dict[str, tuple[bool, str, str, str]] = {}
    try:
        run_rows = conn.execute("SELECT run_id, tags FROM runs ORDER BY run_id").fetchall()
    except sqlite3.Error as exc:
        return {UNKNOWN: (False, f"runs table unreadable: {exc}", UNKNOWN, UNKNOWN)}

    send_log_rows_by_run_id = sqlite_send_log_row_counts(conn)
    cycle_cache_run_ids = sqlite_distinct_run_ids(conn, "calculated_cycle_data")

    for row in run_rows:
        run_id = str(row["run_id"])
        try:
            tags = parse_tags(row["tags"] if "tags" in row.keys() else "")
            calculate_phase_gap_error.extract_device_count_from_tags(tags)
            send_log_rows = send_log_rows_by_run_id.get(run_id, 0)
            has_cycle_cache = run_id in cycle_cache_run_ids
            if send_log_rows <= 0:
                result[run_id] = (False, "send_log has no rows for this run", "0", "yes" if has_cycle_cache else "no")
            else:
                result[run_id] = (
                    True,
                    "send_log rows found for this run",
                    str(send_log_rows),
                    "yes" if has_cycle_cache else "no",
                )
        except Exception as exc:
            result[run_id] = (False, f"sqlite analysis read failed: {exc}", UNKNOWN, UNKNOWN)
    return result


def sqlite_send_log_row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    try:
        rows = conn.execute(
            "SELECT run_id, COUNT(*) AS n FROM send_log GROUP BY run_id"
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {str(row["run_id"]): int(row["n"]) for row in rows}


def sqlite_distinct_run_ids(conn: sqlite3.Connection, table_name: str) -> set[str]:
    try:
        rows = conn.execute(f"SELECT DISTINCT run_id FROM {table_name}").fetchall()
    except sqlite3.Error:
        return set()
    return {str(row["run_id"]) for row in rows}


def raw_run_stats(raw_db_path: Path) -> RawRunStats:
    if not raw_db_path.exists():
        return RawRunStats({}, {}, set())
    try:
        with sqlite3.connect(raw_db_path) as conn:
            conn.row_factory = sqlite3.Row
            run_rows = conn.execute(
                """
                SELECT run_id, coupling_strength
                FROM runs
                ORDER BY coupling_strength, run_id
                """
            ).fetchall()
            send_counts = sqlite_send_log_row_counts(conn)
            cycle_cache_run_ids = sqlite_distinct_run_ids(conn, "calculated_cycle_data")
    except sqlite3.Error:
        return RawRunStats({}, {}, set())

    run_ids_by_k: dict[str, list[str]] = {}
    for row in run_rows:
        k_text = text_or_unknown(row["coupling_strength"])
        run_ids_by_k.setdefault(k_text, []).append(str(row["run_id"]))
    return RawRunStats(run_ids_by_k, send_counts, cycle_cache_run_ids)


def graph_run_stats(graph_dir: Path) -> RawRunStats:
    graph_db_path = graph_dir / "graph_data.sqlite"
    if not graph_db_path.exists():
        return raw_run_stats(graph_dir / "raw_run.sqlite")

    run_ids_by_k: dict[str, list[tuple[int, str]]] = {}
    try:
        with sqlite3.connect(graph_db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT run_id, coupling_strength, repeat_index
                FROM runs
                WHERE status = 'completed'
                ORDER BY coupling_strength, repeat_index
                """
            ).fetchall()
    except sqlite3.Error:
        rows = []

    for row in rows:
        k_text = text_or_unknown(row["coupling_strength"])
        run_ids_by_k.setdefault(k_text, []).append((int(row["repeat_index"]), str(row["run_id"])))

    count_db_path = graph_dir / "interval_per.sqlite"
    if not count_db_path.exists():
        count_db_path = graph_db_path
    send_counts, cycle_cache_run_ids = graph_cycle_count_stats(count_db_path)
    if not send_counts and count_db_path != graph_db_path:
        send_counts, cycle_cache_run_ids = graph_cycle_count_stats(graph_db_path)

    return RawRunStats(
        {
            k_text: [
                run_id
                for _, run_id in sorted(repeat_rows, key=lambda item: item[0])
            ]
            for k_text, repeat_rows in run_ids_by_k.items()
        },
        send_counts,
        cycle_cache_run_ids,
    )


def graph_cycle_count_stats(db_path: Path) -> tuple[dict[str, int], set[str]]:
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT run_id, MAX(cumulative_actual_packets) AS n
                FROM run_cycle_counts
                GROUP BY run_id
                """
            ).fetchall()
    except sqlite3.Error:
        return {}, set()
    counts = {
        str(row["run_id"]): int(row["n"])
        for row in rows
        if row["n"] is not None
    }
    return counts, set(counts)


def csv_send_log_rows(send_log_path: Path) -> str:
    if not send_log_path.exists():
        return "0"
    try:
        with send_log_path.open("r", encoding="utf-8", errors="ignore") as handle:
            row_count = sum(1 for _ in handle)
    except OSError:
        return UNKNOWN
    return str(max(0, row_count - 1))


def try_csv_analysis_read(
    run_dir: Path,
    metadata: dict[str, Any],
    tags: list[str],
) -> tuple[bool, str, str]:
    try:
        cycle_time, cycle_tags = calculate_cycle_data.read_metadata(run_dir / "metadata.csv")
        send_df = pd.read_csv(
            run_dir / "send_log.csv",
            dtype={
                "time": "float64",
                "oscillator_id": "string",
                "send_count": "int64",
            },
            nrows=READABILITY_SAMPLE_ROWS,
        )
        if send_df.empty:
            return False, "send_log.csv is empty", UNKNOWN
        calculate_cycle_data.normalize_time_column(
            calculate_cycle_data.normalize_oscillator_id_column(send_df, cycle_tags),
            cycle_tags,
        )
        phase_tags, num_devices = calculate_phase_gap_error.read_metadata(run_dir / "metadata.csv")
        calculate_phase_gap_error.normalize_time_column(
            calculate_phase_gap_error.normalize_oscillator_id_column(send_df, phase_tags),
            phase_tags,
        )
        cycle_count = cycles_from_existing_cycle_file(run_dir)
        if cycle_count == UNKNOWN:
            cycle_count = cycles_from_duration(duration_from_metadata(metadata), cycle_time)
        return (
            True,
            f"CSV metadata and first {len(send_df)} send_log rows readable by current analysis",
            cycle_count,
        )
    except Exception as exc:
        fallback_cycles = cycles_from_existing_cycle_file(run_dir)
        return False, f"CSV analysis read failed: {exc}", fallback_cycles


def read_csv_metadata(metadata_path: Path) -> dict[str, Any]:
    if not metadata_path.exists():
        return {}
    try:
        df = pd.read_csv(metadata_path)
    except Exception:
        return {}
    if df.empty:
        return {}
    return {str(key): value for key, value in df.iloc[0].to_dict().items()}


def tags_from_metadata(metadata: dict[str, Any]) -> list[str]:
    return parse_tags(metadata.get("tags", ""))


def parse_tags(value: object) -> list[str]:
    if value is None or pd.isna(value):
        return []
    return [tag.strip() for tag in str(value).split(";") if tag.strip()]


def device_count_from_metadata_or_tags(metadata: dict[str, Any], tags: list[str]) -> str:
    for key in ("device_count", "num_devices"):
        value = metadata.get(key)
        if known(value):
            return text_or_unknown(value)
    for tag in tags:
        match = re.fullmatch(r"device_count_(\d+)", tag)
        if match:
            return match.group(1)
        match = re.fullmatch(r"(\d+)dai", tag)
        if match:
            return match.group(1)
    return UNKNOWN


def duration_from_metadata(metadata: dict[str, Any]) -> str:
    for key in ("duration", "duration_ms", "simulation_duration_ms"):
        value = metadata.get(key)
        if known(value):
            return text_or_unknown(value)
    return duration_from_ranges(metadata.get("ranges"))


def duration_from_ranges(value: object) -> str:
    if value is None or pd.isna(value):
        return UNKNOWN
    max_end: float | None = None
    for part in str(value).split("|"):
        pieces = part.split(":")
        if len(pieces) < 2:
            continue
        try:
            end_time = float(pieces[1])
        except ValueError:
            continue
        max_end = end_time if max_end is None else max(max_end, end_time)
    if max_end is None:
        return UNKNOWN
    return format_number(max_end)


def cycles_from_duration(duration: object, cycle_time: object) -> str:
    if not known(duration) or not known(cycle_time):
        return UNKNOWN
    try:
        duration_f = float(duration)
        cycle_time_f = float(cycle_time)
    except (TypeError, ValueError):
        return UNKNOWN
    if cycle_time_f <= 0:
        return UNKNOWN
    return format_number(duration_f / cycle_time_f)


def cycles_from_existing_cycle_file(run_dir: Path) -> str:
    cycle_path = run_dir / "calculated_Cycle_data.csv"
    if not cycle_path.exists():
        return UNKNOWN
    try:
        return str(len(pd.read_csv(cycle_path)))
    except Exception:
        return UNKNOWN


def first_known(metadata: dict[str, Any], *keys: str, default: str = UNKNOWN) -> str:
    for key in keys:
        value = metadata.get(key)
        if known(value):
            return text_or_unknown(value)
    return default


def known(value: object) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return str(value).strip() != ""


def text_or_unknown(value: object) -> str:
    if not known(value):
        return UNKNOWN
    if isinstance(value, float):
        return format_number(value)
    return str(value)


def format_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{float(value):g}"


def csv_format_label(metadata_path: Path, send_log_path: Path) -> str:
    if metadata_path.exists() and send_log_path.exists():
        return "legacy_csv_compatible"
    if metadata_path.exists() or send_log_path.exists():
        return "unknown"
    return "unknown"


def sqlite_inventory_kind(sqlite_path: Path) -> str:
    try:
        with sqlite3.connect(sqlite_path) as conn:
            table_row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='runs'"
            ).fetchone()
            if table_row is None:
                return ""
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(runs)").fetchall()
            }
            if {"coupling_function", "cycle_time", "tags"}.issubset(columns):
                return "raw_run_sqlite"
            if {"request_id", "repeat_index", "metadata_json"}.issubset(columns):
                return "graph_metadata_sqlite"
            return ""
    except sqlite3.Error:
        return ""


def graph_input_params(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        row = conn.execute(
            "SELECT value FROM graph_meta WHERE key = 'input_params' LIMIT 1"
        ).fetchone()
        if row is not None:
            parsed = json.loads(str(row["value"]))
            if isinstance(parsed, dict):
                return parsed
    except (sqlite3.Error, json.JSONDecodeError, KeyError):
        pass
    try:
        row = conn.execute(
            "SELECT params_json FROM simulation_requests ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is not None:
            parsed = json.loads(str(row["params_json"]))
            if isinstance(parsed, dict):
                return parsed
    except (sqlite3.Error, json.JSONDecodeError, KeyError):
        pass
    return {}


def parse_json_object(value: object) -> dict[str, Any]:
    if not known(value):
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def graph_coupling_function(params: dict[str, Any]) -> str:
    if not isinstance(params, dict):
        return UNKNOWN
    value = params.get("coupling_function")
    if known(value):
        return text_or_unknown(value)
    graph_key = params.get("graph_key")
    if isinstance(graph_key, dict):
        return text_or_unknown(graph_key.get("coupling_function"))
    return UNKNOWN


def created_at_iso(path: Path) -> str:
    try:
        timestamp = path.stat().st_ctime
    except OSError:
        return UNKNOWN
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(microsecond=0).isoformat()


def project_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def resolve_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def write_inventory(output_path: Path, rows: list[InventoryRow]) -> None:
    output_path = resolve_path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_dict())


if __name__ == "__main__":
    raise SystemExit(main())
