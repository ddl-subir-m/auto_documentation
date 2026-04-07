"""Tests for artifact_layout.py — logical path resolver."""

from __future__ import annotations

import os
import sys

import pytest

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg_dir = os.path.join(_repo_root, "auto_model_docs")
for p in (_repo_root, _pkg_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

import artifact_layout
from artifact_layout import ArtifactLayout, init_layout, get_layout, reset_layout


@pytest.fixture(autouse=True)
def _reset():
    """Reset the singleton before and after each test."""
    reset_layout()
    yield
    reset_layout()


class TestArtifactLayout:
    def test_docs_dir(self):
        layout = ArtifactLayout()
        assert layout.docs_dir == "docs"

    def test_specs_dir(self):
        layout = ArtifactLayout()
        assert layout.specs_dir == "specs"

    def test_internal_dir(self):
        layout = ArtifactLayout()
        assert layout.internal_dir == ".autodoc"

    def test_generation_cache(self):
        layout = ArtifactLayout()
        assert layout.generation_cache == ".autodoc/cache.json"


class TestSingleton:
    def test_init_and_get(self):
        init_layout()
        layout = get_layout()
        assert layout.docs_dir == "docs"

    def test_get_before_init_raises(self):
        with pytest.raises(RuntimeError, match="not initialized"):
            get_layout()

    def test_init_returns_same_instance(self):
        layout1 = init_layout()
        layout2 = init_layout()
        assert layout1 is layout2

    def test_reset_clears_singleton(self):
        init_layout()
        reset_layout()
        with pytest.raises(RuntimeError):
            get_layout()

    def test_reset_then_reinit(self):
        init_layout()
        reset_layout()
        layout = init_layout()
        assert layout.docs_dir == "docs"
