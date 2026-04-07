"""Central path resolver for all autodoc artifacts.

All artifact paths flow through this module. Paths are logical paths
relative to the dataset root, used with the DatasetStore for actual I/O.

Directory structure within the autodoc dataset:
    docs/          — Generated .docx and .ipynb files
    specs/         — Uploaded YAML spec files
    .autodoc/      — Internal artifacts
        cache.json — Generation results cache
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_layout: Optional["ArtifactLayout"] = None


class ArtifactLayout:
    """Logical path resolver for autodoc artifacts within a dataset.

    Paths returned are relative to the dataset root. All actual I/O
    goes through DatasetStore (dataset_store.py).
    """

    # -- User-visible paths (relative to dataset root) --

    @property
    def docs_dir(self) -> str:
        return "docs"

    @property
    def specs_dir(self) -> str:
        return "specs"

    # -- Internal paths --

    @property
    def internal_dir(self) -> str:
        return ".autodoc"

    @property
    def generation_cache(self) -> str:
        return ".autodoc/cache.json"


def init_layout() -> ArtifactLayout:
    """Initialize the global artifact layout singleton."""
    global _layout
    if _layout is not None:
        return _layout
    _layout = ArtifactLayout()
    logger.info("Artifact layout initialized")
    return _layout


def get_layout() -> ArtifactLayout:
    """Return the initialized artifact layout.

    Raises RuntimeError if init_layout() has not been called.
    """
    if _layout is None:
        raise RuntimeError(
            "ArtifactLayout not initialized. Call init_layout() first."
        )
    return _layout


def reset_layout() -> None:
    """Reset the layout singleton. Used only in tests."""
    global _layout
    _layout = None
