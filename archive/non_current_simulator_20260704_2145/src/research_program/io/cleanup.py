from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Iterable

from research_program.config.paths import PROJECT_ROOT, resolve_project_path


DEFAULT_TARGETS = ("runs", "aggregated", "figures")
TARGET_PATHS = {
    "runs": Path("data/runs"),
    "aggregated": Path("data/aggregated"),
    "figures": Path("outputs/figures"),
    "reports": Path("outputs/reports"),
    "raw_real": Path("data/raw/real"),
    "raw_simulation": Path("data/raw/simulation"),
}
PRESERVED_FILENAMES = {".gitkeep"}


@dataclass(frozen=True)
class CleanupItem:
    path: Path
    is_dir: bool
    size_bytes: int


@dataclass(frozen=True)
class CleanupResult:
    target_names: tuple[str, ...]
    dry_run: bool
    deleted_count: int
    deleted_bytes: int
    items: tuple[CleanupItem, ...]

    @property
    def deleted_size_mb(self) -> float:
        return self.deleted_bytes / (1024 * 1024)


def resolve_cleanup_targets(target_names: tuple[str, ...]) -> dict[str, Path]:
    targets: dict[str, Path] = {}
    for name in target_names:
        if name not in TARGET_PATHS:
            allowed = ", ".join(sorted(TARGET_PATHS))
            raise ValueError(f"Unknown cleanup target: {name}. Allowed: {allowed}")

        path = resolve_project_path(TARGET_PATHS[name]).resolve()
        _assert_safe_target(path)
        targets[name] = path

    return targets


def collect_cleanup_items(target_names: tuple[str, ...]) -> tuple[CleanupItem, ...]:
    items: list[CleanupItem] = []
    for target_path in resolve_cleanup_targets(target_names).values():
        if not target_path.exists():
            continue

        for child in sorted(target_path.iterdir(), key=lambda p: str(p).lower()):
            if child.name in PRESERVED_FILENAMES:
                continue
            items.append(
                CleanupItem(
                    path=child,
                    is_dir=child.is_dir(),
                    size_bytes=_path_size(child),
                )
            )

    return tuple(items)


def cleanup_experiment_outputs(
    target_names: tuple[str, ...] = DEFAULT_TARGETS,
    dry_run: bool = True,
) -> CleanupResult:
    items = collect_cleanup_items(target_names)

    if not dry_run:
        for item in items:
            _delete_item(item.path)

    return CleanupResult(
        target_names=target_names,
        dry_run=dry_run,
        deleted_count=len(items),
        deleted_bytes=sum(item.size_bytes for item in items),
        items=items,
    )


def cleanup_run_directories(
    run_paths: Iterable[str | Path],
    dry_run: bool = True,
    calculate_size: bool = True,
) -> CleanupResult:
    items: list[CleanupItem] = []
    seen_paths: set[Path] = set()
    for run_path in run_paths:
        resolved = resolve_project_path(run_path).resolve()
        _assert_safe_target(resolved)
        if resolved in seen_paths or not resolved.is_dir():
            continue
        seen_paths.add(resolved)
        items.append(
            CleanupItem(
                path=resolved,
                is_dir=True,
                size_bytes=_path_size(resolved) if calculate_size else 0,
            )
        )

    items.sort(key=lambda item: str(item.path).lower())
    if not dry_run:
        for item in items:
            _delete_item(item.path)

    return CleanupResult(
        target_names=("filtered_runs",),
        dry_run=dry_run,
        deleted_count=len(items),
        deleted_bytes=sum(item.size_bytes for item in items),
        items=tuple(items),
    )


def format_cleanup_result(result: CleanupResult) -> str:
    mode = "dry-run" if result.dry_run else "deleted"
    lines = [
        f"mode: {mode}",
        f"targets: {', '.join(result.target_names)}",
        f"items: {result.deleted_count}",
        f"size_mb: {result.deleted_size_mb:.3f}",
    ]
    for item in result.items:
        kind = "dir" if item.is_dir else "file"
        relative_path = item.path.relative_to(PROJECT_ROOT)
        lines.append(f"- {kind}: {relative_path} ({item.size_bytes} bytes)")
    return "\n".join(lines)


def _assert_safe_target(path: Path) -> None:
    project_root = PROJECT_ROOT.resolve()
    if path == project_root:
        raise ValueError("Refusing to clean the project root itself.")
    if not path.is_relative_to(project_root):
        raise ValueError(f"Refusing to clean a path outside the project: {path}")


def _delete_item(path: Path) -> None:
    resolved = path.resolve()
    _assert_safe_target(resolved)
    if resolved.name in PRESERVED_FILENAMES:
        return
    if resolved.is_dir():
        shutil.rmtree(resolved)
    else:
        resolved.unlink(missing_ok=True)


def _path_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())
    return 0
