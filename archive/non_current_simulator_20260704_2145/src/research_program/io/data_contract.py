from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from research_program.config.loader import load_toml


DEFAULT_DATA_CONTRACT_PATH = Path("configs/data_format/run_v1.toml")


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    dtype: str
    required: bool = False
    description: str = ""
    unit: str | None = None
    separator: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ColumnSpec:
        return cls(
            name=str(data["name"]),
            dtype=str(data.get("type", "string")),
            required=bool(data.get("required", False)),
            description=str(data.get("description", "")),
            unit=data.get("unit"),
            separator=data.get("separator"),
            aliases=tuple(str(alias) for alias in data.get("aliases", [])),
        )

    @property
    def accepted_names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass(frozen=True)
class FileSpec:
    key: str
    columns: tuple[ColumnSpec, ...]

    @classmethod
    def from_dict(cls, key: str, data: dict[str, Any]) -> FileSpec:
        columns = tuple(ColumnSpec.from_dict(item) for item in data.get("columns", []))
        return cls(key=key, columns=columns)

    @property
    def required_columns(self) -> tuple[ColumnSpec, ...]:
        return tuple(column for column in self.columns if column.required)


@dataclass(frozen=True)
class RunDataContract:
    version: str
    description: str
    layout: dict[str, Any]
    files: dict[str, FileSpec]

    @property
    def required_files(self) -> tuple[str, ...]:
        return tuple(self.layout.get("required_files", []))

    @property
    def derived_files(self) -> tuple[str, ...]:
        return tuple(self.layout.get("derived_files", []))

    def file_spec(self, key: str) -> FileSpec:
        return self.files[key]


def load_data_contract(path: str | Path = DEFAULT_DATA_CONTRACT_PATH) -> RunDataContract:
    data = load_toml(path)
    files = {
        key: FileSpec.from_dict(key, value)
        for key, value in data.get("files", {}).items()
    }
    return RunDataContract(
        version=str(data["version"]),
        description=str(data.get("description", "")),
        layout=dict(data.get("layout", {})),
        files=files,
    )


def missing_required_files(run_dir: Path, contract: RunDataContract) -> list[str]:
    return [
        filename
        for filename in contract.required_files
        if not (run_dir / filename).exists()
    ]


def missing_required_columns(
    available_columns: Iterable[str],
    file_spec: FileSpec,
) -> list[str]:
    available = set(available_columns)
    missing: list[str] = []
    for column in file_spec.required_columns:
        if not any(name in available for name in column.accepted_names):
            missing.append(column.name)
    return missing
