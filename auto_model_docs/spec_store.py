"""Manages uploaded spec files saved to disk for Domino job mode."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)
_warned_no_project: bool = False


def _specs_dir(project_name: Optional[str] = None) -> Path:
    """Return the specs directory, optionally scoped to a target project.

    When *project_name* is given the specs land in that project's dataset
    so the Domino job (which may run in a different project) can access them.
    """
    if Path("/mnt/data").exists():
        global _warned_no_project
        if not project_name and not _warned_no_project:
            logger.warning("No target project name for spec store; defaulting to 'autodoc'")
            _warned_no_project = True
        project = project_name or "autodoc"
        base = Path(f"/mnt/data/{project}/autodoc_specs")
    else:
        base = Path("./autodoc_specs")
    base.mkdir(parents=True, exist_ok=True)
    return base


def save_spec(original_filename: str, content: str, project_name: Optional[str] = None) -> Path:
    """Write spec content to disk with a UUID prefix.

    Returns the Path of the saved file.
    """
    safe_name = Path(original_filename).name  # strip any path components
    dest = _specs_dir(project_name) / f"{uuid4()}_{safe_name}"
    dest.write_text(content, encoding="utf-8")
    return dest


def list_specs(project_name: Optional[str] = None) -> list[dict[str, Any]]:
    """Return metadata for all saved spec files, newest first."""
    specs_dir = _specs_dir(project_name)
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


def delete_spec(filename: str, project_name: Optional[str] = None) -> None:
    """Delete a spec file by its filename (basename only)."""
    target = _specs_dir(project_name) / Path(filename).name
    if target.exists():
        target.unlink()


def delete_all_specs(project_name: Optional[str] = None) -> None:
    """Delete all spec files in the specs directory."""
    for p in _specs_dir(project_name).iterdir():
        if p.is_file():
            p.unlink()
