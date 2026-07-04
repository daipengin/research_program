from __future__ import annotations

from pathlib import Path
import tomllib
from typing import Any

from research_program.config.paths import resolve_project_path


def load_toml(path: str | Path) -> dict[str, Any]:
    config_path = resolve_project_path(path)
    with config_path.open("rb") as f:
        return tomllib.load(f)
