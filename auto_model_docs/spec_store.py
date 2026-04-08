"""Manages uploaded spec files via the Domino Artifacts (DFS) API.

All I/O goes through ArtifactStore (domino_artifacts.py).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

import artifact_layout
import domino_artifacts

logger = logging.getLogger(__name__)


def save_spec(original_filename: str, content: str) -> str:
    """Write spec content to artifacts with a UUID prefix.

    Returns the artifact-relative path of the saved file (e.g. "specs/{uuid}_{name}").
    """
    store = domino_artifacts.get_store()
    layout = artifact_layout.get_layout()
    # Strip any path components from filename for safety
    safe_name = original_filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    relative_path = f"{layout.specs_dir}/{uuid4()}_{safe_name}"
    store.write_file(relative_path, content.encode("utf-8"))
    return relative_path


def list_specs() -> list[dict[str, Any]]:
    """Return metadata for all saved spec files."""
    store = domino_artifacts.get_store()
    layout = artifact_layout.get_layout()
    try:
        files = store.list_files(layout.specs_dir)
    except Exception:
        return []
    return [
        {
            "name": f["name"],
            "path": f.get("path") or f"{layout.specs_dir}/{f['name']}",
            "size_kb": round(f.get("size", 0) / 1024, 1),
            "created_at": f.get("lastModified", ""),
        }
        for f in files
        if f.get("name")
    ]
