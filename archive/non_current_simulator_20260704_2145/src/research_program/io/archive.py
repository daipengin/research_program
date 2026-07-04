from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Iterable

from research_program.config.paths import PROJECT_ROOT, resolve_project_path
from research_program.io.cleanup import PRESERVED_FILENAMES


DEFAULT_TEMP_ARCHIVE_ROOT = Path("data/archives/temp")
MANIFEST_FILENAME = "manifest.json"


@dataclass(frozen=True)
class ArchiveItem:
    source_path: Path
    archived_path: Path
    is_dir: bool
    size_bytes: int
    status: str = "ok"
    message: str = ""


@dataclass(frozen=True)
class ArchiveResult:
    archive_dir: Path
    dry_run: bool
    item_count: int
    total_bytes: int
    items: tuple[ArchiveItem, ...]

    @property
    def total_size_mb(self) -> float:
        return self.total_bytes / (1024 * 1024)


def archive_run_directories(
    run_paths: Iterable[str | Path],
    archive_root: str | Path = DEFAULT_TEMP_ARCHIVE_ROOT,
    dry_run: bool = True,
) -> ArchiveResult:
    archive_root_path = resolve_project_path(archive_root).resolve()
    _assert_project_child(archive_root_path)
    archive_id = _new_archive_id()
    archive_dir = archive_root_path / archive_id
    archive_runs_dir = archive_dir / "runs"

    items = _collect_archive_items(run_paths, archive_runs_dir)

    if not dry_run:
        archive_runs_dir.mkdir(parents=True, exist_ok=False)
        for item in items:
            item.archived_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(item.source_path), str(item.archived_path))
        _write_manifest(archive_dir, items)

    return ArchiveResult(
        archive_dir=archive_dir,
        dry_run=dry_run,
        item_count=len(items),
        total_bytes=sum(item.size_bytes for item in items),
        items=tuple(items),
    )


def list_temp_archives(
    archive_root: str | Path = DEFAULT_TEMP_ARCHIVE_ROOT,
) -> list[Path]:
    archive_root_path = resolve_project_path(archive_root).resolve()
    if not archive_root_path.exists():
        return []
    return sorted(
        [
            archive_dir
            for archive_dir in archive_root_path.iterdir()
            if archive_dir.is_dir()
            and (archive_dir / MANIFEST_FILENAME).exists()
            and _archive_has_existing_item(archive_dir)
        ],
        key=lambda path: path.name,
        reverse=True,
    )


def restore_archive(
    archive_dir: str | Path,
    dry_run: bool = True,
) -> ArchiveResult:
    archive_path = resolve_project_path(archive_dir).resolve()
    _assert_project_child(archive_path)
    manifest = _read_manifest(archive_path)

    items: list[ArchiveItem] = []
    for raw_item in manifest.get("items", []):
        source_path = Path(str(raw_item["source_path"])).resolve()
        archived_path = Path(str(raw_item["archived_path"])).resolve()
        _assert_project_child(source_path)
        _assert_project_child(archived_path)

        status = "ok"
        message = ""
        if not archived_path.exists():
            status = "missing_archive_item"
            message = "Archived item does not exist."
        elif source_path.exists():
            status = "restore_conflict"
            message = "Original path already exists."

        items.append(
            ArchiveItem(
                source_path=source_path,
                archived_path=archived_path,
                is_dir=bool(raw_item.get("is_dir", True)),
                size_bytes=int(raw_item.get("size_bytes", 0)),
                status=status,
                message=message,
            )
        )

    if not dry_run:
        blocked = [item for item in items if item.status != "ok"]
        if blocked:
            messages = "; ".join(f"{item.source_path}: {item.message}" for item in blocked)
            raise ValueError(f"Cannot restore archive because some items are blocked: {messages}")
        for item in items:
            item.source_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(item.archived_path), str(item.source_path))

    return ArchiveResult(
        archive_dir=archive_path,
        dry_run=dry_run,
        item_count=len(items),
        total_bytes=sum(item.size_bytes for item in items),
        items=tuple(items),
    )


def _collect_archive_items(
    run_paths: Iterable[str | Path],
    archive_runs_dir: Path,
) -> list[ArchiveItem]:
    items: list[ArchiveItem] = []
    seen_paths: set[Path] = set()
    seen_names: set[str] = set()

    for run_path in run_paths:
        resolved = resolve_project_path(run_path).resolve()
        _assert_project_child(resolved)
        if resolved in seen_paths or not resolved.is_dir():
            continue
        if resolved.name in PRESERVED_FILENAMES:
            continue
        if _path_is_relative_to(resolved, archive_runs_dir.parent):
            continue
        if resolved.name in seen_names:
            raise ValueError(f"Duplicate run directory name cannot be archived together: {resolved.name}")

        archived_path = archive_runs_dir / resolved.name
        if archived_path.exists():
            raise ValueError(f"Archive destination already exists: {archived_path}")

        seen_paths.add(resolved)
        seen_names.add(resolved.name)
        items.append(
            ArchiveItem(
                source_path=resolved,
                archived_path=archived_path,
                is_dir=True,
                size_bytes=_path_size(resolved),
            )
        )

    items.sort(key=lambda item: str(item.source_path).lower())
    return items


def _write_manifest(archive_dir: Path, items: list[ArchiveItem]) -> None:
    payload = {
        "version": 1,
        "archive_id": archive_dir.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "items": [
            {
                "source_path": str(item.source_path),
                "archived_path": str(item.archived_path),
                "is_dir": item.is_dir,
                "size_bytes": item.size_bytes,
            }
            for item in items
        ],
    }
    manifest_path = archive_dir / MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_manifest(archive_dir: Path) -> dict:
    manifest_path = archive_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"archive manifest not found: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _archive_has_existing_item(archive_dir: Path) -> bool:
    try:
        manifest = _read_manifest(archive_dir)
    except (OSError, json.JSONDecodeError, KeyError):
        return False
    for raw_item in manifest.get("items", []):
        archived_path = Path(str(raw_item.get("archived_path", "")))
        if archived_path.exists():
            return True
    return False


def _new_archive_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _assert_project_child(path: Path) -> None:
    project_root = PROJECT_ROOT.resolve()
    if path == project_root:
        raise ValueError("Refusing to archive the project root itself.")
    if not _path_is_relative_to(path, project_root):
        raise ValueError(f"Refusing to archive a path outside the project: {path}")


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _path_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())
    return 0
