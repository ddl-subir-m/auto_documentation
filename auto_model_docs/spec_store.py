"""Manages uploaded spec files via the Domino Datasets API.

All I/O goes through DatasetStore (dataset_store.py). No direct filesystem access.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from artifact_layout import get_layout
from dataset_store import get_store

logger = logging.getLogger(__name__)


def save_spec(original_filename: str, content: str) -> str:
    """Write spec content to the dataset with a UUID prefix.

    Returns the dataset-relative path of the saved file.
    """
    store = get_store()
    layout = get_layout()
    # Strip any path components from filename for safety
    safe_name = original_filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    relative_path = f"{layout.specs_dir}/{uuid4()}_{safe_name}"
    store.write_file(relative_path, content.encode("utf-8"))
    return relative_path


def list_specs() -> list[dict[str, Any]]:
    """Return metadata for all saved spec files."""
    store = get_store()
    layout = get_layout()
    try:
        files = store.list_files(layout.specs_dir)
    except Exception:
        return []
    results: list[dict[str, Any]] = []
    for f in files:
        if f.get("isDirectory"):
            continue
        results.append({
            "name": f["fileName"],
            "path": f"{layout.specs_dir}/{f['fileName']}",
            "size_kb": round((f.get("sizeInBytes") or 0) / 1024, 1),
            "created_at": f.get("lastModified", ""),
        })
    return results


