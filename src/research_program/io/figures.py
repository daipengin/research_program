from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterable

import pandas as pd
from PIL import Image

from research_program.config.paths import resolve_project_path


RASTER_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
DEFAULT_FIGURE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".pdf", ".svg")


@dataclass(frozen=True)
class FigureAsset:
    path: Path
    source_root: Path

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def extension(self) -> str:
        return self.path.suffix.lower()

    @property
    def relative_path(self) -> str:
        try:
            return str(self.path.relative_to(self.source_root))
        except ValueError:
            return self.name

    @property
    def size_bytes(self) -> int:
        return self.path.stat().st_size

    @property
    def is_raster(self) -> bool:
        return self.extension in RASTER_EXTENSIONS


def discover_figures(
    figure_dirs: Iterable[str | Path],
    extensions: Iterable[str] = DEFAULT_FIGURE_EXTENSIONS,
) -> list[FigureAsset]:
    extension_set = {extension.lower() for extension in extensions}
    assets: list[FigureAsset] = []

    for root_value in figure_dirs:
        source_root = resolve_project_path(root_value)
        if not source_root.exists():
            continue
        for path in sorted(item for item in source_root.rglob("*") if item.is_file()):
            if path.suffix.lower() in extension_set:
                assets.append(FigureAsset(path=path, source_root=source_root))

    return assets


def figures_to_frame(assets: Iterable[FigureAsset]) -> pd.DataFrame:
    rows = [
        {
            "name": asset.name,
            "relative_path": asset.relative_path,
            "extension": asset.extension,
            "source_root": str(asset.source_root),
            "size_kb": round(asset.size_bytes / 1024, 1),
            "path": str(asset.path),
        }
        for asset in assets
    ]
    return pd.DataFrame(rows)


def original_mime_type(path: Path) -> str:
    extension = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".pdf": "application/pdf",
        ".svg": "image/svg+xml",
    }.get(extension, "application/octet-stream")


def read_original_bytes(path: Path) -> bytes:
    return path.read_bytes()


def convert_raster_image(path: Path, output_format: str) -> tuple[bytes, str, str]:
    output_format = output_format.lower()
    image_format = "JPEG" if output_format in {"jpg", "jpeg"} else output_format.upper()
    mime = "image/jpeg" if image_format == "JPEG" else f"image/{output_format}"
    filename = f"{path.stem}.{output_format}"

    with Image.open(path) as image:
        if image_format == "JPEG" and image.mode in {"RGBA", "LA", "P"}:
            image = image.convert("RGB")

        buffer = BytesIO()
        image.save(buffer, format=image_format)
        return buffer.getvalue(), mime, filename
