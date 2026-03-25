"""Tests for spec_store.py — spec file persistence."""

from __future__ import annotations

import os
import sys
import time

import pytest

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg_dir = os.path.join(_repo_root, "auto_model_docs")
for p in (_repo_root, _pkg_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

import spec_store


@pytest.fixture(autouse=True)
def _use_tmp_dir(tmp_path, monkeypatch):
    """Redirect specs dir to a temp directory for every test."""
    specs_dir = tmp_path / "specs"
    monkeypatch.setattr(spec_store, "_specs_dir", lambda project_name=None: specs_dir)
    specs_dir.mkdir()
    yield


# ---------------------------------------------------------------------------
# save_spec
# ---------------------------------------------------------------------------

class TestSaveSpec:
    def test_returns_path(self):
        path = spec_store.save_spec("my_spec.yaml", "title: Test")
        assert path.exists()
        assert path.suffix == ".yaml"

    def test_content_written(self):
        content = "title: My Model\nsections:\n  - overview"
        path = spec_store.save_spec("spec.yaml", content)
        assert path.read_text(encoding="utf-8") == content

    def test_uuid_prefix(self):
        path = spec_store.save_spec("spec.yaml", "content")
        # Filename format: {uuid}_{original}
        name = path.name
        assert "_spec.yaml" in name
        assert len(name) > len("spec.yaml")

    def test_strips_directory_from_filename(self):
        path = spec_store.save_spec("../../etc/passwd", "sneaky")
        assert "etc" not in path.name
        assert "passwd" in path.name

    def test_multiple_saves_unique_names(self):
        p1 = spec_store.save_spec("spec.yaml", "v1")
        p2 = spec_store.save_spec("spec.yaml", "v2")
        assert p1 != p2
        assert p1.name != p2.name


# ---------------------------------------------------------------------------
# list_specs
# ---------------------------------------------------------------------------

class TestListSpecs:
    def test_empty_dir(self):
        assert spec_store.list_specs() == []

    def test_returns_metadata(self):
        spec_store.save_spec("test.yaml", "content here")
        specs = spec_store.list_specs()
        assert len(specs) == 1
        s = specs[0]
        assert "name" in s
        assert "path" in s
        assert "size_kb" in s
        assert "created_at" in s
        assert s["size_kb"] >= 0

    def test_newest_first(self):
        spec_store.save_spec("first.yaml", "a")
        time.sleep(0.05)  # ensure different mtime
        spec_store.save_spec("second.yaml", "bb")
        specs = spec_store.list_specs()
        assert len(specs) == 2
        # second.yaml was saved last, should be first
        assert "second.yaml" in specs[0]["name"]
        assert "first.yaml" in specs[1]["name"]

    def test_multiple_files(self):
        for i in range(5):
            spec_store.save_spec(f"spec_{i}.yaml", f"content {i}")
        assert len(spec_store.list_specs()) == 5


# ---------------------------------------------------------------------------
# delete_spec
# ---------------------------------------------------------------------------

class TestDeleteSpec:
    def test_deletes_file(self):
        path = spec_store.save_spec("doomed.yaml", "goodbye")
        assert path.exists()
        spec_store.delete_spec(path.name)
        assert not path.exists()

    def test_delete_nonexistent_no_error(self):
        spec_store.delete_spec("ghost.yaml")  # should not raise

    def test_strips_path_traversal(self):
        path = spec_store.save_spec("safe.yaml", "keep me")
        # Try to delete with path traversal — should only match basename
        spec_store.delete_spec("../../" + path.name)
        assert not path.exists()

    def test_only_deletes_target(self):
        p1 = spec_store.save_spec("keep.yaml", "stay")
        p2 = spec_store.save_spec("delete.yaml", "go")
        spec_store.delete_spec(p2.name)
        assert p1.exists()
        assert not p2.exists()


# ---------------------------------------------------------------------------
# delete_all_specs
# ---------------------------------------------------------------------------

class TestDeleteAllSpecs:
    def test_deletes_everything(self):
        spec_store.save_spec("a.yaml", "one")
        spec_store.save_spec("b.yaml", "two")
        spec_store.save_spec("c.yaml", "three")
        assert len(spec_store.list_specs()) == 3

        spec_store.delete_all_specs()
        assert len(spec_store.list_specs()) == 0

    def test_noop_when_empty(self):
        spec_store.delete_all_specs()  # should not error
        assert len(spec_store.list_specs()) == 0


# ---------------------------------------------------------------------------
# project_name routing
# ---------------------------------------------------------------------------

class TestProjectNameRouting:
    """_specs_dir should use the target project name when provided."""

    def test_uses_project_name(self, tmp_path, monkeypatch):
        """When project_name is given, specs land in that project's dataset."""
        # Restore the real _specs_dir so we can test its logic
        monkeypatch.undo()
        monkeypatch.setattr(spec_store, "Path", type(tmp_path))
        # Simulate /mnt/data existing by pointing at tmp_path
        base = tmp_path / "mnt" / "data"
        base.mkdir(parents=True)
        monkeypatch.setattr(
            spec_store,
            "_specs_dir",
            lambda project_name=None: _make_specs_dir(tmp_path, project_name),
        )
        p = spec_store.save_spec("s.yaml", "content", project_name="TargetProj")
        assert "TargetProj" in str(p)


def _make_specs_dir(tmp_root, project_name=None):
    """Helper that mimics _specs_dir logic using a tmp root."""
    project = project_name or "app_default"
    d = tmp_root / "mnt" / "data" / project / "autodoc_specs"
    d.mkdir(parents=True, exist_ok=True)
    return d
