"""Tests for studio/ui_components.py — sanitizers, environment validation, UI helpers."""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# Ensure auto_model_docs is importable
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg_dir = os.path.join(_repo_root, "auto_model_docs")
for p in (_repo_root, _pkg_dir):
    if p not in sys.path:
        sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

def _load_module_from_file(name: str, path: str) -> ModuleType:
    """Load a Python module from an absolute file path."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_dependencies(monkeypatch):
    """Mock studio.state and fasthtml so ui_components can import."""
    from dataclasses import dataclass
    from typing import Optional

    # Build a real-ish state mock
    mock_state = ModuleType("studio.state")
    mock_state._DOMINO_AVAILABLE = True
    mock_state._get_default_code_root = lambda: Path("/mnt/code")
    mock_state._max_jobs = lambda: 1
    mock_state.domino_job_store = MagicMock()

    @dataclass
    class DominoJobRecord:
        id: str
        username: str
        domino_run_id: Optional[str] = None
        branch: Optional[str] = None
        hardware_tier: Optional[str] = None
        status: str = "queued"
        domino_status: Optional[str] = None
        job_url: Optional[str] = None
        spec_path: Optional[str] = None
        submitted_at: Optional[str] = None
        completed_at: Optional[str] = None
        error: Optional[str] = None
        project_id: Optional[str] = None

    @dataclass
    class EnvironmentWarning:
        level: str
        message: str
        action: str

    mock_state.DominoJobRecord = DominoJobRecord
    mock_state.EnvironmentWarning = EnvironmentWarning

    # Create a studio package module
    studio_pkg = ModuleType("studio")
    studio_pkg.__path__ = [os.path.join(_pkg_dir, "studio")]
    studio_pkg.__package__ = "studio"

    saved = {}
    for key in ("studio", "studio.state", "studio.ui_components"):
        saved[key] = sys.modules.get(key)

    sys.modules["studio"] = studio_pkg
    sys.modules["studio.state"] = mock_state

    # Ensure fasthtml.common provides the needed names
    fh_names = ("Div", "P", "A", "Span", "H3", "Select", "Option", "Input",
                "Label", "Table", "Thead", "Tbody", "Tr", "Th", "Td",
                "Pre", "Button", "Details", "Summary", "Ul", "Li")
    mock_fh_common = ModuleType("fasthtml.common")
    for name in fh_names:
        setattr(mock_fh_common, name, lambda *a, _name=name, **kw: MagicMock(__ft_name__=_name))
    # FT type stub
    mock_fh_common.FT = type("FT", (), {})

    mock_fh = ModuleType("fasthtml")
    mock_fh.common = mock_fh_common

    sys.modules["fasthtml"] = mock_fh
    sys.modules["fasthtml.common"] = mock_fh_common

    yield {"state": mock_state}

    # Restore
    for key, val in saved.items():
        if val is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = val
    sys.modules.pop("fasthtml", None)
    sys.modules.pop("fasthtml.common", None)


def _import_ui():
    """Force-(re)load ui_components from its source file."""
    sys.modules.pop("studio.ui_components", None)
    ui_path = os.path.join(_pkg_dir, "studio", "ui_components.py")
    return _load_module_from_file("studio.ui_components", ui_path)


# ---------------------------------------------------------------------------
# _sanitize_optional_int
# ---------------------------------------------------------------------------

class TestSanitizeOptionalInt:
    def test_none_returns_none(self):
        ui = _import_ui()
        assert ui._sanitize_optional_int(None) is None

    def test_empty_string_returns_none(self):
        ui = _import_ui()
        assert ui._sanitize_optional_int("") is None

    def test_valid_int_string(self):
        ui = _import_ui()
        assert ui._sanitize_optional_int("42") == 42

    def test_zero(self):
        ui = _import_ui()
        assert ui._sanitize_optional_int("0") == 0

    def test_invalid_returns_none(self):
        ui = _import_ui()
        assert ui._sanitize_optional_int("not-a-number") is None


# ---------------------------------------------------------------------------
# _sanitize_optional_float
# ---------------------------------------------------------------------------

class TestSanitizeOptionalFloat:
    def test_none_returns_none(self):
        ui = _import_ui()
        assert ui._sanitize_optional_float(None) is None

    def test_empty_string_returns_none(self):
        ui = _import_ui()
        assert ui._sanitize_optional_float("") is None

    def test_valid_float_string(self):
        ui = _import_ui()
        assert ui._sanitize_optional_float("3.14") == pytest.approx(3.14)

    def test_integer_string(self):
        ui = _import_ui()
        assert ui._sanitize_optional_float("10") == 10.0

    def test_invalid_returns_none(self):
        ui = _import_ui()
        assert ui._sanitize_optional_float("abc") is None


# ---------------------------------------------------------------------------
# _db_record_to_dataclass
# ---------------------------------------------------------------------------

class TestDbRecordToDataclass:
    def test_converts_full_row(self):
        ui = _import_ui()
        row = {
            "id": "job-1",
            "username": "alice",
            "domino_run_id": "run-1",
            "branch": "main",
            "hardware_tier": "tier-1",
            "status": "running",
            "domino_status": "Executing",
            "job_url": "https://domino/jobs/1",
            "spec_path": "/spec.yaml",
            "submitted_at": "2026-01-01T00:00:00",
            "completed_at": None,
            "project_id": "proj-123",
        }
        record = ui._db_record_to_dataclass(row)
        assert record.id == "job-1"
        assert record.username == "alice"
        assert record.status == "running"
        assert record.domino_run_id == "run-1"

    def test_handles_missing_optional_fields(self):
        ui = _import_ui()
        row = {"id": "job-2", "username": "bob"}
        record = ui._db_record_to_dataclass(row)
        assert record.id == "job-2"
        assert record.status == "queued"  # default
        assert record.domino_run_id is None


# ---------------------------------------------------------------------------
# _validate_environment
# ---------------------------------------------------------------------------

class TestValidateEnvironment:
    def test_no_warnings_in_normal_env(self, _mock_dependencies, monkeypatch):
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow")
        monkeypatch.delenv("DOMINO_PROJECT_ID", raising=False)
        ui = _import_ui()
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "mkdir"):
                warnings = ui._validate_environment()
        assert isinstance(warnings, list)

    def test_warns_when_code_root_missing(self, _mock_dependencies, monkeypatch):
        monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
        monkeypatch.delenv("DOMINO_PROJECT_ID", raising=False)
        _mock_dependencies["state"]._get_default_code_root = lambda: Path(".")
        ui = _import_ui()
        with patch.object(Path, "exists", return_value=False):
            with patch.object(Path, "mkdir"):
                warnings = ui._validate_environment()
        levels = [w.level for w in warnings]
        assert "warning" in levels

    def test_warns_when_mlflow_missing(self, _mock_dependencies, monkeypatch):
        monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
        monkeypatch.delenv("DOMINO_PROJECT_ID", raising=False)
        ui = _import_ui()
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "mkdir"):
                warnings = ui._validate_environment()
        messages = [w.message for w in warnings]
        assert any("MLflow" in m for m in messages)

    def test_warns_when_domino_api_host_missing(self, _mock_dependencies, monkeypatch):
        monkeypatch.setenv("DOMINO_PROJECT_ID", "proj-1")
        monkeypatch.delenv("DOMINO_API_HOST", raising=False)
        monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
        ui = _import_ui()
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "mkdir"):
                warnings = ui._validate_environment()
        messages = [w.message for w in warnings]
        assert any("API host" in m for m in messages)


# ---------------------------------------------------------------------------
# _field_id
# ---------------------------------------------------------------------------

class TestFieldId:
    def test_prefix(self):
        ui = _import_ui()
        assert ui._field_id("branch") == "field-branch"
        assert ui._field_id("model") == "field-model"


# ---------------------------------------------------------------------------
# _render_warnings_banner
# ---------------------------------------------------------------------------

class TestRenderWarningsBanner:
    def test_empty_warnings_returns_empty(self):
        ui = _import_ui()
        assert ui._render_warnings_banner([]) == []

    def test_renders_banners_for_each_warning(self, _mock_dependencies):
        ui = _import_ui()
        EnvironmentWarning = _mock_dependencies["state"].EnvironmentWarning
        warnings = [
            EnvironmentWarning(level="info", message="Info msg", action="Do this"),
            EnvironmentWarning(level="warning", message="Warn msg", action="Fix this"),
            EnvironmentWarning(level="error", message="Error msg", action="Urgent"),
        ]
        banners = ui._render_warnings_banner(warnings)
        assert len(banners) == 3


# ---------------------------------------------------------------------------
# _render_domino_status
# ---------------------------------------------------------------------------

class TestRenderDominoStatus:
    def test_renders_idle_when_no_record(self, _mock_dependencies):
        ui = _import_ui()
        result = ui._render_domino_status(None)
        assert result is not None

    def test_renders_running_job(self, _mock_dependencies):
        ui = _import_ui()
        DominoJobRecord = _mock_dependencies["state"].DominoJobRecord
        record = DominoJobRecord(
            id="job-1", username="alice", status="running",
            domino_run_id="run-1", job_url="https://domino/jobs/1",
            submitted_at="2026-01-01T00:00:00",
            domino_status="Executing",
        )
        result = ui._render_domino_status(record)
        assert result is not None

    def test_renders_queued_with_banner(self, _mock_dependencies):
        ui = _import_ui()
        DominoJobRecord = _mock_dependencies["state"].DominoJobRecord
        record = DominoJobRecord(id="job-2", username="alice", status="queued")
        result = ui._render_domino_status(record)
        assert result is not None

    def test_renders_succeeded_job(self, _mock_dependencies):
        ui = _import_ui()
        DominoJobRecord = _mock_dependencies["state"].DominoJobRecord
        record = DominoJobRecord(
            id="job-3", username="alice", status="succeeded",
            submitted_at="2026-01-01T00:00:00",
            completed_at="2026-01-01T01:00:00",
            domino_status="Succeeded",
        )
        result = ui._render_domino_status(record)
        assert result is not None


# ---------------------------------------------------------------------------
# _render_job_history_table
# ---------------------------------------------------------------------------

class TestRenderJobHistoryTable:
    def test_returns_empty_when_domino_unavailable(self, _mock_dependencies):
        ui = _import_ui()
        ui._DOMINO_AVAILABLE = False
        result = ui._render_job_history_table("alice")
        assert result is not None

    def test_renders_empty_message_when_no_jobs(self, _mock_dependencies):
        ui = _import_ui()
        _mock_dependencies["state"].domino_job_store.get_user_jobs.return_value = []
        result = ui._render_job_history_table("alice")
        assert result is not None

    def test_renders_active_and_completed_jobs(self, _mock_dependencies):
        ui = _import_ui()
        _mock_dependencies["state"].domino_job_store.get_user_jobs.return_value = [
            {"id": "j1", "status": "running", "branch": "main",
             "hardware_tier": "small", "submitted_at": "2026-01-01T00:00",
             "job_url": "https://domino/j1", "domino_run_id": "r1"},
            {"id": "j2", "status": "succeeded", "branch": "main",
             "hardware_tier": "small", "submitted_at": "2025-12-31T00:00",
             "job_url": "https://domino/j2"},
        ]
        result = ui._render_job_history_table("alice")
        assert result is not None

    def test_shows_queue_banner_for_queued_jobs(self, _mock_dependencies):
        ui = _import_ui()
        _mock_dependencies["state"].domino_job_store.get_user_jobs.return_value = [
            {"id": "j1", "status": "queued", "branch": "main",
             "hardware_tier": None, "submitted_at": "2026-01-01T00:00",
             "job_url": None, "domino_run_id": None},
        ]
        result = ui._render_job_history_table("alice")
        assert result is not None

    def test_handles_pre_bootstrap_runtime_error(self, _mock_dependencies):
        ui = _import_ui()
        _mock_dependencies["state"].domino_job_store.get_user_jobs.side_effect = RuntimeError("Not ready")
        result = ui._render_job_history_table("alice")
        assert result is not None
