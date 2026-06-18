from __future__ import annotations

from dataclasses import dataclass
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
