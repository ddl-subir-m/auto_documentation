"""Manages uploaded spec files saved to disk for Domino job mode."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def _specs_dir() -> Path:
    if Path("/mnt/data").exists():
        project = os.environ.get("DOMINO_PROJECT_NAME", "autodoc")
        base = Path(f"/mnt/data/{project}/autodoc_specs")
    else:
        base = Path("./autodoc_specs")
    base.mkdir(parents=True, exist_ok=True)
    return base


def save_spec(original_filename: str, content: str) -> Path:
    """Write spec content to disk with a UUID prefix.

    Returns the Path of the saved file.
    """
    safe_name = Path(original_filename).name  # strip any path components
    dest = _specs_dir() / f"{uuid4()}_{safe_name}"
    dest.write_text(content, encoding="utf-8")
    return dest


def list_specs() -> list[dict[str, Any]]:
    """Return metadata for all saved spec files, newest first."""
    specs_dir = _specs_dir()
    results: list[dict[str, Any]] = []
    for p in sorted(specs_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_file():
            stat = p.stat()
            results.append(
                {
                    "name": p.name,
                    "path": str(p),
                    "size_kb": round(stat.st_size / 1024, 1),
                    "created_at": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                }
            )
    return results


def delete_spec(filename: str) -> None:
    """Delete a spec file by its filename (basename only)."""
    target = _specs_dir() / Path(filename).name
    if target.exists():
        target.unlink()


def delete_all_specs() -> None:
    """Delete all spec files in the specs directory."""
    for p in _specs_dir().iterdir():
        if p.is_file():
            p.unlink()
