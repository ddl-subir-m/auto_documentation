"""Tests for studio/state.py — shared mutable state, helpers, path resolution."""

from __future__ import annotations

import os
import sys
from dataclasses import fields
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure auto_model_docs is importable
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg_dir = os.path.join(_repo_root, "auto_model_docs")
for p in (_repo_root, _pkg_dir):
    if p not in sys.path:
        sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# Import helpers — state.py tries to load sibling modules at import time,
# so we mock _import_sibling to avoid FileNotFoundError in test env.
# ---------------------------------------------------------------------------

def _get_state_module():
    """Import studio.state with sibling modules mocked."""
    # Patch _import_sibling before the module body runs
    import importlib

    # If already imported, reload with mocks
    mock_domino_client = MagicMock()
    mock_domino_job_store = MagicMock()
    mock_spec_store = MagicMock()
    mock_auth_context = MagicMock()
    mock_domino_datasets = MagicMock()

    # Pre-register the sibling modules so _import_sibling succeeds
    sys.modules["domino_client"] = mock_domino_client
    sys.modules["domino_job_store"] = mock_domino_job_store
    sys.modules["spec_store"] = mock_spec_store
    sys.modules["auth_context"] = MagicMock()
    sys.modules["domino_datasets"] = mock_domino_datasets

    # Remove cached studio.state if present
    sys.modules.pop("studio.state", None)
    sys.modules.pop("studio", None)

    # Ensure studio package is importable
    studio_dir = os.path.join(_pkg_dir, "studio")
    if studio_dir not in sys.path:
        sys.path.insert(0, studio_dir)

    from studio import state
    return state


@pytest.fixture
def state_module():
    """Provide a fresh studio.state module."""
    mod = _get_state_module()
    # Reset mutable globals
    mod._TARGET_PROJECT_ID = None
    mod._TARGET_PROJECT_NAME = None
    mod._POLL_TASK = None
    mod._STARTUP_WARNINGS = []
    yield mod


# ---------------------------------------------------------------------------
# Dataclass structure
# ---------------------------------------------------------------------------

class TestJobRequest:
    def test_has_expected_fields(self, state_module):
        jr = state_module.JobRequest
        field_names = {f.name for f in fields(jr)}
        expected = {
            "spec_path", "spec_content", "provider", "model", "api_key",
            "base_url", "code_root", "max_files", "workers",
            "planning_workers", "timeout", "notebook", "notebook_path",
            "experiment_names", "model_names", "latest_only", "verbose",
            "branch", "hardware_tier", "api_key_source", "spec_filename",
            "project_id",
        }
        assert expected.issubset(field_names)


class TestDominoJobRecord:
    def test_defaults(self, state_module):
        rec = state_module.DominoJobRecord(id="x", username="u")
        assert rec.status == "queued"
        assert rec.domino_run_id is None
        assert rec.project_id is None


class TestEnvironmentWarning:
    def test_fields(self, state_module):
        w = state_module.EnvironmentWarning(
            level="warning", message="Test", action="Fix it",
        )
        assert w.level == "warning"
        assert w.message == "Test"
        assert w.action == "Fix it"


# ---------------------------------------------------------------------------
# _get_username
# ---------------------------------------------------------------------------

class TestGetUsername:
    def test_returns_env_var(self, state_module, monkeypatch):
        monkeypatch.setenv("DOMINO_STARTING_USERNAME", "alice")
        assert state_module._get_username() == "alice"

    def test_defaults_to_local_user(self, state_module, monkeypatch):
        monkeypatch.delenv("DOMINO_STARTING_USERNAME", raising=False)
        assert state_module._get_username() == "local_user"


# ---------------------------------------------------------------------------
# _max_jobs
# ---------------------------------------------------------------------------

class TestMaxJobs:
    def test_default_is_one(self, state_module, monkeypatch):
        monkeypatch.delenv("AUTODOC_MAX_JOBS", raising=False)
        assert state_module._max_jobs() == 1

    def test_reads_from_env(self, state_module, monkeypatch):
        monkeypatch.setenv("AUTODOC_MAX_JOBS", "5")
        assert state_module._max_jobs() == 5


# ---------------------------------------------------------------------------
# _get_default_code_root
# ---------------------------------------------------------------------------

class TestGetDefaultCodeRoot:
    def test_returns_mnt_code_when_exists(self, state_module):
        with patch.object(Path, "exists", return_value=True):
            result = state_module._get_default_code_root()
            assert result == Path("/mnt/code")

    def test_falls_back_to_cwd(self, state_module):
        with patch.object(Path, "exists", return_value=False):
            result = state_module._get_default_code_root()
            assert result == Path(".")


# ---------------------------------------------------------------------------
# _get_default_spec_path
# ---------------------------------------------------------------------------

class TestGetDefaultSpecPath:
    def test_returns_doc_spec_yaml(self, state_module):
        result = state_module._get_default_spec_path()
        assert result.name == "doc_spec.yaml"
        assert "auto_model_docs" in str(result)


# ---------------------------------------------------------------------------
# _resolve_request_project_id
# ---------------------------------------------------------------------------

class TestResolveRequestProjectId:
    def test_from_query_param_projectId(self, state_module):
        req = MagicMock()
        req.query_params = {"projectId": "from-query"}
        result = state_module._resolve_request_project_id(req)
        assert result == "from-query"

    def test_from_query_param_project_id(self, state_module):
        req = MagicMock()
        req.query_params = {"project_id": "from-snake"}
        result = state_module._resolve_request_project_id(req)
        assert result == "from-snake"

    def test_falls_back_to_captured_target(self, state_module):
        state_module._TARGET_PROJECT_ID = "captured-id"
        req = MagicMock()
        req.query_params = {}
        result = state_module._resolve_request_project_id(req)
        assert result == "captured-id"

    def test_returns_none_when_nothing_available(self, state_module):
        state_module._TARGET_PROJECT_ID = None
        req = MagicMock()
        req.query_params = {}
        result = state_module._resolve_request_project_id(req)
        assert result is None


# ---------------------------------------------------------------------------
# _set_target_project
# ---------------------------------------------------------------------------

class TestSetTargetProject:
    def test_first_call_captures_project(self, state_module):
        mock_info = MagicMock()
        mock_info.name = "resolved-name"
        state_module.domino_client.resolve_project.return_value = mock_info
        state_module._DOMINO_AVAILABLE = True

        # _set_target_project references itself via import, mock that
        with patch.dict(sys.modules, {"studio.state": state_module}):
            result = state_module._set_target_project("proj-abc")

        assert result is True

    def test_second_call_is_noop(self, state_module):
        state_module._TARGET_PROJECT_ID = "already-set"
        with patch.dict(sys.modules, {"studio.state": state_module}):
            result = state_module._set_target_project("proj-abc")
        assert result is False

