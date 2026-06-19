from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Iterable

import pandas as pd

from research_program.config.paths import resolve_project_path
from research_program.io.data_contract import (
    RunDataContract,
    load_data_contract,
    missing_required_files,
)


DEFAULT_RUN_INDEX_PATH = Path("outputs/reports/run_index.json")


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    path: Path
    source_root: Path
    metadata: dict[str, Any]
    missing_files: tuple[str, ...]
    available_files: tuple[str, ...]

    @property
    def record_key(self) -> str:
        return str(self.path.resolve())

    @property
    def tags(self) -> list[str]:
        tags = self.metadata.get("tags", [])
        return tags if isinstance(tags, list) else parse_tags(tags)


def parse_tags(raw_value: Any, separator: str = ";") -> list[str]:
    if raw_value is None:
        return []
    try:
        if pd.isna(raw_value):
            return []
    except TypeError:
        pass
    if isinstance(raw_value, list):
        return [str(tag).strip() for tag in raw_value if str(tag).strip()]
    return [tag.strip() for tag in str(raw_value).split(separator) if tag.strip()]


def extract_device_count(tags: Iterable[str]) -> int | None:
    for tag in tags:
        match = re.fullmatch(r"(\d+)dai", str(tag))
        if match is not None:
            return int(match.group(1))
    return None


def _first_present(row: pd.Series, names: Iterable[str]) -> Any:
    for name in names:
        if name in row.index:
            return row[name]
    return None


def _coerce_metadata_value(value: Any, dtype: str, separator: str | None) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass

    if dtype == "integer":
        return int(value)
    if dtype == "float":
        return float(value)
    if dtype == "list[string]":
        return parse_tags(value, separator or ";")
    return str(value)


def read_metadata(metadata_path: Path, contract: RunDataContract | None = None) -> dict[str, Any]:
    if contract is None:
        contract = load_data_contract()

    df = pd.read_csv(metadata_path)
    if df.empty:
        return {}

    row = df.iloc[0]
    metadata_spec = contract.files.get("metadata")
    metadata: dict[str, Any] = {}

    if metadata_spec is None:
        return row.to_dict()

    for column in metadata_spec.columns:
        value = _first_present(row, column.accepted_names)
        metadata[column.name] = _coerce_metadata_value(
            value=value,
            dtype=column.dtype,
            separator=column.separator,
        )

    for column_name, value in row.items():
        metadata.setdefault(column_name, value)

    return metadata


def _run_signature(run_dir: Path, contract: RunDataContract) -> dict[str, Any]:
    metadata_path = run_dir / "metadata.csv"
    metadata_stat = metadata_path.stat() if metadata_path.exists() else None
    run_stat = run_dir.stat()
    return {
        "path": str(run_dir.resolve()),
        "contract_version": contract.version,
        "required_files": list(contract.required_files),
        "dir_mtime_ns": run_stat.st_mtime_ns,
        "metadata_mtime_ns": metadata_stat.st_mtime_ns if metadata_stat is not None else None,
        "metadata_size": metadata_stat.st_size if metadata_stat is not None else None,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    if pd.isna(value) if not isinstance(value, (list, tuple, dict)) else False:
        return None
    return value


def _load_run_index(index_path: Path) -> dict[str, Any]:
    if not index_path.exists():
        return {"entries": {}}
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"entries": {}}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return {"entries": {}}
    return data


def _run_roots_signature(runs_dirs: Iterable[str | Path]) -> list[dict[str, Any]]:
    signatures: list[dict[str, Any]] = []
    for root_value in runs_dirs:
        source_root = resolve_project_path(root_value)
        if not source_root.exists():
            signatures.append(
                {
                    "path": str(source_root.resolve()),
                    "exists": False,
                    "mtime_ns": None,
                }
            )
            continue
        root_stat = source_root.stat()
        signatures.append(
            {
                "path": str(source_root.resolve()),
                "exists": True,
                "mtime_ns": root_stat.st_mtime_ns,
            }
        )
    return signatures


def _records_from_index_entries(entries: dict[str, Any]) -> list[RunRecord]:
    records: list[RunRecord] = []
    for record_key, entry in sorted(entries.items()):
        if not isinstance(entry, dict):
            continue
        signature = entry.get("signature") if isinstance(entry.get("signature"), dict) else {}
        path_text = signature.get("path") or record_key
        source_root_text = entry.get("source_root") or str(Path(path_text).parent)
        metadata = dict(entry.get("metadata") or {})
        records.append(
            RunRecord(
                run_id=str(entry.get("run_id") or metadata.get("run_id") or Path(path_text).name),
                path=Path(path_text),
                source_root=Path(source_root_text),
                metadata=metadata,
                missing_files=tuple(str(item) for item in entry.get("missing_files", [])),
                available_files=tuple(str(item) for item in entry.get("available_files", [])),
            )
        )
    return records


def _save_run_index(index_path: Path, entries: dict[str, Any], roots_signature: list[dict[str, Any]]) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "roots": roots_signature, "entries": entries}
    tmp_path = index_path.with_suffix(f"{index_path.suffix}.tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(index_path)


def discover_runs(
    runs_dirs: Iterable[str | Path],
    contract: RunDataContract | None = None,
) -> list[RunRecord]:
    if contract is None:
        contract = load_data_contract()

    records: list[RunRecord] = []
    for root_value in runs_dirs:
        source_root = resolve_project_path(root_value)
        if not source_root.exists():
            continue

        for run_dir in sorted(path for path in source_root.iterdir() if path.is_dir()):
            metadata_path = run_dir / "metadata.csv"
            metadata = read_metadata(metadata_path, contract) if metadata_path.exists() else {}
            run_id = str(metadata.get("run_id") or run_dir.name)
            missing_files = tuple(missing_required_files(run_dir, contract))
            available_files = tuple(path.name for path in sorted(run_dir.iterdir()) if path.is_file())
            records.append(
                RunRecord(
                    run_id=run_id,
                    path=run_dir,
                    source_root=source_root,
                    metadata=metadata,
                    missing_files=missing_files,
                    available_files=available_files,
                )
            )

    return records


def discover_runs_with_index(
    runs_dirs: Iterable[str | Path],
    contract: RunDataContract | None = None,
    index_path: str | Path = DEFAULT_RUN_INDEX_PATH,
    force_rescan: bool = False,
) -> list[RunRecord]:
    if contract is None:
        contract = load_data_contract()

    runs_dirs = tuple(runs_dirs)
    resolved_index_path = resolve_project_path(index_path)
    index_data = _load_run_index(resolved_index_path)
    cached_entries = index_data.get("entries", {})
    roots_signature = _run_roots_signature(runs_dirs)

    if (
        not force_rescan
        and cached_entries
        and index_data.get("roots") == roots_signature
    ):
        return _records_from_index_entries(cached_entries)

    records: list[RunRecord] = []
    next_entries: dict[str, Any] = {}

    for root_value in runs_dirs:
        source_root = resolve_project_path(root_value)
        if not source_root.exists():
            continue

        for run_dir in sorted(path for path in source_root.iterdir() if path.is_dir()):
            record_key = str(run_dir.resolve())
            signature = _run_signature(run_dir, contract)
            cached = cached_entries.get(record_key)

            if isinstance(cached, dict) and cached.get("signature") == signature:
                metadata = dict(cached.get("metadata") or {})
                run_id = str(cached.get("run_id") or metadata.get("run_id") or run_dir.name)
                missing_files = tuple(str(item) for item in cached.get("missing_files", []))
                available_files = tuple(str(item) for item in cached.get("available_files", []))
            else:
                metadata_path = run_dir / "metadata.csv"
                metadata = read_metadata(metadata_path, contract) if metadata_path.exists() else {}
                run_id = str(metadata.get("run_id") or run_dir.name)
                missing_files = tuple(missing_required_files(run_dir, contract))
                available_files = tuple(path.name for path in sorted(run_dir.iterdir()) if path.is_file())

            next_entries[record_key] = {
                "signature": signature,
                "run_id": run_id,
                "source_root": str(source_root.resolve()),
                "metadata": _json_safe(metadata),
                "missing_files": list(missing_files),
                "available_files": list(available_files),
            }
            records.append(
                RunRecord(
                    run_id=run_id,
                    path=run_dir,
                    source_root=source_root,
                    metadata=metadata,
                    missing_files=missing_files,
                    available_files=available_files,
                )
            )

    if next_entries != cached_entries or index_data.get("roots") != roots_signature:
        _save_run_index(resolved_index_path, next_entries, roots_signature)
    return records


def records_to_frame(records: Iterable[RunRecord]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for record in records:
        tags = record.tags
        row = {
            "record_key": record.record_key,
            "run_id": record.run_id,
            "path": str(record.path),
            "source_root": str(record.source_root),
            "status": "ok" if not record.missing_files else "missing files",
            "missing_files": ";".join(record.missing_files),
            "available_files": ";".join(record.available_files),
            "tags": ";".join(tags),
            "device_count": extract_device_count(tags),
        }
        row.update(record.metadata)
        if isinstance(row.get("tags"), list):
            row["tags"] = ";".join(row["tags"])
        rows.append(row)

    return pd.DataFrame(rows)


def filter_records(
    records: Iterable[RunRecord],
    coupling_functions: Iterable[str] | None = None,
    numeric_ranges: dict[str, tuple[float, float]] | None = None,
    required_tags: Iterable[str] | None = None,
) -> list[RunRecord]:
    coupling_function_set = set(coupling_functions or [])
    required_tag_set = set(required_tags or [])
    numeric_ranges = numeric_ranges or {}

    result: list[RunRecord] = []
    for record in records:
        metadata = record.metadata
        if coupling_function_set:
            value = metadata.get("coupling_function")
            if str(value) not in coupling_function_set:
                continue

        if required_tag_set and not required_tag_set.issubset(set(record.tags)):
            continue

        keep = True
        for field_name, (min_value, max_value) in numeric_ranges.items():
            value = metadata.get(field_name)
            if value is None:
                keep = False
                break
            numeric_value = float(value)
            if numeric_value < min_value or numeric_value > max_value:
                keep = False
                break

        if keep:
            result.append(record)

    return result
