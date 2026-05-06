"""Bundle context loader for Shape 1 Portal -> Job integration.

Portal writes a JSON context file to
``/mnt/data/{project}/autodoc_inputs/ctx_{job_uuid}.json`` before submitting
a Domino Job. The Job reads the file at start via :func:`load_context` and
deletes it on exit via :func:`delete_context_file`.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

REQUIRED_KEYS: tuple[tuple[str, type], ...] = (
    ("bundle_id", str),
    ("policy_version_id", str),
    ("bundle", dict),
    ("policy_def", dict),
)
_REQUIRED_KEYS = REQUIRED_KEYS  # backward-compat alias for any external readers


class BundleContextError(Exception):
    """Raised when a bundle context file is missing, malformed, or invalid."""


def load_context(path: str) -> dict[str, Any]:
    """Read and validate a bundle context JSON file.

    Raises :class:`BundleContextError` with a specific message if the file
    cannot be read, is not valid JSON, is not a JSON object, or is missing
    any required key.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError as e:
        raise BundleContextError(f"Bundle context file not found: {path}") from e
    except OSError as e:
        raise BundleContextError(
            f"Failed to read bundle context file {path}: {e}"
        ) from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise BundleContextError(
            f"Bundle context file {path} contains invalid JSON: {e}"
        ) from e

    if not isinstance(data, dict):
        raise BundleContextError(
            f"Bundle context file {path} must contain a JSON object at the top "
            f"level, got {type(data).__name__}"
        )

    for key, expected_type in _REQUIRED_KEYS:
        if key not in data:
            raise BundleContextError(
                f"Bundle context file {path} is missing required key: {key!r}"
            )
        if not isinstance(data[key], expected_type):
            raise BundleContextError(
                f"Bundle context file {path} key {key!r} must be "
                f"{expected_type.__name__}, got {type(data[key]).__name__}"
            )

    return data


def delete_context_file(path: str) -> None:
    """Best-effort removal of the context file.

    Idempotent: silently returns if the file is already gone. Logs and
    swallows permission or I/O errors so cleanup can live in a Job's
    ``finally`` block without masking the underlying failure.
    """
    try:
        os.remove(path)
    except FileNotFoundError:
        return
    except PermissionError as e:
        logger.warning("Permission denied deleting bundle context file %s: %s", path, e)
    except OSError as e:
        logger.warning("Failed to delete bundle context file %s: %s", path, e)
