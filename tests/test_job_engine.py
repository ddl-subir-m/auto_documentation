"""Tests for studio/job_engine.py — polling, reconciliation, request parsing, submission."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure auto_model_docs is importable
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg_dir = os.path.join(_repo_root, "auto_model_docs")
for p in (_repo_root, _pkg_dir):
    if p not in sys.path:
        sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# Shared dataclasses (mirror state.py)
# ---------------------------------------------------------------------------

@dataclass
class JobRequest:
    spec_path: Optional[str] = None
    spec_content: Optional[str] = None
    provider: str = "anthropic"
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    code_root: Optional[str] = None
    max_files: Optional[int] = None
    workers: Optional[int] = None
    planning_workers: Optional[int] = None
    timeout: Optional[float] = None
    notebook: bool = False
    notebook_path: Optional[str] = None
    experiment_names: Optional[str] = None
    model_names: Optional[str] = None
    latest_only: bool = False
    verbose: bool = True
    branch: Optional[str] = None
    hardware_tier: Optional[str] = None
    api_key_source: str = "domino_env"
    spec_filename: Optional[str] = None
    project_id: Optional[str] = None


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


# ---------------------------------------------------------------------------
# Import helper
# ---------------------------------------------------------------------------

def _load_module(name: str, path: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _build_mock_state():
    """Create a module-like object for studio.state."""
    mock_state = ModuleType("studio.state")
    mock_state._DOMINO_AVAILABLE = True
    mock_state._get_username = MagicMock(return_value="test_user")
    mock_state._max_jobs = MagicMock(return_value=1)
    mock_state._get_target_project_id = MagicMock(return_value="proj-123")
    mock_state._get_target_project_name = MagicMock(return_value="my-project")
    mock_state._resolve_target_project_name = MagicMock(return_value="my-project")
    mock_state.logger = MagicMock()
    mock_state.JobRequest = JobRequest
    mock_state.DominoJobRecord = DominoJobRecord
    mock_state.domino_client = MagicMock()
    mock_state.domino_job_store = MagicMock()
    mock_state.spec_store = MagicMock()
    mock_state.domino_datasets = MagicMock()
    return mock_state


def _build_mock_ui():
    """Create a module-like object for studio.ui_components."""
    mock_ui = ModuleType("studio.ui_components")
    def _safe_int(v):
        if v in (None, ""):
            return None
        try:
            return int(v)
        except (ValueError, TypeError):
            return None

    def _safe_float(v):
        if v in (None, ""):
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    mock_ui._sanitize_optional_int = _safe_int
    mock_ui._sanitize_optional_float = _safe_float
    mock_ui._db_record_to_dataclass = lambda row: DominoJobRecord(
        id=row["id"], username=row["username"],
        status=row.get("status", "queued"),
        domino_run_id=row.get("domino_run_id"),
    )
    return mock_ui


@pytest.fixture(autouse=True)
def _mock_studio():
    """Provide mocked studio.state and studio.ui_components."""
    mock_state = _build_mock_state()
    mock_ui = _build_mock_ui()

    studio_pkg = ModuleType("studio")
    studio_pkg.__path__ = [os.path.join(_pkg_dir, "studio")]
    studio_pkg.__package__ = "studio"

    saved = {}
    for key in ("studio", "studio.state", "studio.ui_components", "studio.job_engine"):
        saved[key] = sys.modules.get(key)

    sys.modules["studio"] = studio_pkg
    sys.modules["studio.state"] = mock_state
    sys.modules["studio.ui_components"] = mock_ui

    yield mock_state

    for key, val in saved.items():
        if val is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = val


def _import_job_engine():
    """Force-(re)load job_engine from its source file."""
    sys.modules.pop("studio.job_engine", None)
    je_path = os.path.join(_pkg_dir, "studio", "job_engine.py")
    return _load_module("studio.job_engine", je_path)


# ---------------------------------------------------------------------------
# _build_job_command
# ---------------------------------------------------------------------------

class TestBuildJobCommand:
    def test_minimal_command(self):
        je = _import_job_engine()
        req = JobRequest(provider="anthropic")
        cmd = je._build_job_command(req, "/path/spec.yaml")
        assert cmd[0] == "python"
        assert "--spec" in cmd
        assert "/path/spec.yaml" in cmd
        assert "--provider" in cmd
        assert "--notebook" in cmd
        assert "--verbose" in cmd

    def test_all_options(self):
        je = _import_job_engine()
        req = JobRequest(
            provider="openai", model="gpt-4", code_root="/code",
            max_files=10, workers=4, planning_workers=2,
            timeout=30.0, experiment_names="exp1,exp2", model_names="model1",
            latest_only=True, verbose=True,
        )
        cmd = je._build_job_command(req, "/spec.yaml")
        assert "--model" in cmd and "gpt-4" in cmd
        assert "--code-root" in cmd
        # --output is no longer passed (CLI ignores it; output goes via DatasetStore)
        assert "--output" not in cmd
        assert "--max-files" in cmd and "10" in cmd
        assert "--generation-workers" in cmd
        assert "--planning-workers" in cmd
        assert "--timeout" in cmd
        assert "--experiments" in cmd
        assert "--models" in cmd
        assert "--latest-only" in cmd

    def test_no_spec_path(self):
        je = _import_job_engine()
        req = JobRequest(provider="anthropic")
        cmd = je._build_job_command(req, None)
        assert "--spec" not in cmd

    def test_command_str_joins_parts(self):
        je = _import_job_engine()
        req = JobRequest(provider="anthropic")
        cmd_str = je._build_job_command_str(req, "/spec.yaml")
        assert isinstance(cmd_str, str)
        assert "python" in cmd_str
        assert "--spec /spec.yaml" in cmd_str


# ---------------------------------------------------------------------------
# _submit_domino_job
# ---------------------------------------------------------------------------

class TestSubmitDominoJob:
    @pytest.mark.asyncio
    async def test_submit_immediate_when_under_limit(self, _mock_studio):
        je = _import_job_engine()
        store = _mock_studio.domino_job_store
        client = _mock_studio.domino_client

        store.create_job.return_value = "job-1"
        store.count_active_jobs.return_value = 1
        client.submit_job.return_value = "run-abc"
        client.build_job_url.return_value = "https://domino/jobs/run-abc"
        store.get_job.return_value = {
            "id": "job-1", "username": "test_user", "status": "submitted",
            "domino_run_id": "run-abc",
        }

        req = JobRequest(spec_path="/spec.yaml", provider="anthropic", project_id="proj-123")
        result = await je._submit_domino_job(req, "test_user")
        assert result.id == "job-1"
        client.submit_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_queued_when_over_limit(self, _mock_studio):
        je = _import_job_engine()
        store = _mock_studio.domino_job_store

        store.create_job.return_value = "job-2"
        store.count_active_jobs.return_value = 2
        store.get_job.return_value = {
            "id": "job-2", "username": "test_user", "status": "queued",
            "domino_run_id": None,
        }

        req = JobRequest(spec_path="/spec.yaml", provider="anthropic", project_id="proj-123")
        result = await je._submit_domino_job(req, "test_user")
        assert result.status == "queued"
        _mock_studio.domino_client.submit_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_when_no_spec(self, _mock_studio):
        je = _import_job_engine()
        req = JobRequest(provider="anthropic", project_id="proj-123")
        with pytest.raises(ValueError, match="spec file is required"):
            await je._submit_domino_job(req, "test_user")

    @pytest.mark.asyncio
    async def test_raises_when_domino_unavailable(self, _mock_studio):
        je = _import_job_engine()
        # Patch the module-level reference
        with patch.object(je, "_DOMINO_AVAILABLE", False):
            req = JobRequest(spec_path="/spec.yaml", provider="anthropic", project_id="proj-123")
            with pytest.raises(RuntimeError, match="not available"):
                await je._submit_domino_job(req, "test_user")

    @pytest.mark.asyncio
    async def test_submission_failure_marks_job_failed(self, _mock_studio):
        je = _import_job_engine()
        store = _mock_studio.domino_job_store
        client = _mock_studio.domino_client

        store.create_job.return_value = "job-3"
        store.count_active_jobs.return_value = 1
        client.submit_job.side_effect = RuntimeError("API down")
        store.get_job.return_value = {
            "id": "job-3", "username": "test_user", "status": "failed",
            "domino_run_id": None,
        }

        req = JobRequest(spec_path="/spec.yaml", provider="anthropic", project_id="proj-123")
        result = await je._submit_domino_job(req, "test_user")
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_dataset_spec_path_resolved(self, _mock_studio):
        je = _import_job_engine()
        store = _mock_studio.domino_job_store
        client = _mock_studio.domino_client
        datasets = _mock_studio.domino_datasets

        datasets.get_dataset_mount_prefix.return_value = "/mnt/data"
        store.create_job.return_value = "job-4"
        store.count_active_jobs.return_value = 1
        client.submit_job.return_value = "run-xyz"
        client.build_job_url.return_value = "https://domino/jobs/run-xyz"
        store.get_job.return_value = {
            "id": "job-4", "username": "test_user", "status": "submitted",
            "domino_run_id": "run-xyz",
        }

        # Mock the dataset_store.get_store().file_exists_api() check
        mock_ds_store = MagicMock()
        mock_ds_store.file_exists_api.return_value = True
        with patch.dict(sys.modules, {"dataset_store": MagicMock(get_store=lambda: mock_ds_store)}):
            req = JobRequest(
                spec_path="dataset://my-dataset/spec.yaml",
                provider="anthropic", project_id="proj-123",
            )
            await je._submit_domino_job(req, "test_user")
        # Verify the spec path was resolved to an absolute mount path
        call_args = store.create_job.call_args
        spec_in_db = call_args[1].get("spec_path") or call_args[0][3]
        assert spec_in_db == "/mnt/data/my-dataset/spec.yaml"

    @pytest.mark.asyncio
    async def test_dataset_spec_deleted_externally_raises(self, _mock_studio):
        """Spec file deleted in Domino UI between selection and submission."""
        je = _import_job_engine()
        _mock_studio.domino_datasets.get_dataset_mount_prefix.return_value = "/mnt/data"

        mock_ds_store = MagicMock()
        mock_ds_store.file_exists_api.return_value = False
        with patch.dict(sys.modules, {"dataset_store": MagicMock(get_store=lambda: mock_ds_store)}):
            req = JobRequest(
                spec_path="dataset://autodoc/specs/doc_spec.yaml",
                provider="anthropic", project_id="proj-123",
            )
            with pytest.raises(ValueError, match="no longer exists"):
                await je._submit_domino_job(req, "test_user")
        # Job should NOT have been created
        _mock_studio.domino_job_store.create_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_dataset_spec_path_skips_verification(self, _mock_studio):
        """Absolute paths (not dataset://) skip the API check."""
        je = _import_job_engine()
        store = _mock_studio.domino_job_store
        client = _mock_studio.domino_client

        store.create_job.return_value = "job-5"
        store.count_active_jobs.return_value = 1
        client.submit_job.return_value = "run-abc"
        client.build_job_url.return_value = "https://domino/jobs/run-abc"
        store.get_job.return_value = {
            "id": "job-5", "username": "test_user", "status": "submitted",
        }

        req = JobRequest(
            spec_path="/mnt/data/autodoc/specs/doc_spec.yaml",
            provider="anthropic", project_id="proj-123",
        )
        # Should not call dataset_store at all for non-dataset:// paths
        await je._submit_domino_job(req, "test_user")
        client.submit_job.assert_called_once()


# ---------------------------------------------------------------------------
# _reconcile_stale_jobs
# ---------------------------------------------------------------------------

class TestReconcileStaleJobs:
    def test_marks_stale_jobs_as_failed(self, _mock_studio):
        je = _import_job_engine()
        store = _mock_studio.domino_job_store

        je._reconcile_stale_jobs()
        store.reconcile_stale_jobs.assert_called_once()

    def test_handles_exception_gracefully(self, _mock_studio):
        je = _import_job_engine()
        _mock_studio.domino_job_store.reconcile_stale_jobs.side_effect = RuntimeError("error")
        # Should not raise
        je._reconcile_stale_jobs()


# ---------------------------------------------------------------------------
# _poll_domino_jobs (single iteration)
# ---------------------------------------------------------------------------

class TestPollDominoJobs:
    @pytest.mark.asyncio
    async def test_poll_updates_active_jobs(self, _mock_studio):
        je = _import_job_engine()
        store = _mock_studio.domino_job_store
        client = _mock_studio.domino_client

        store.get_active_jobs.return_value = [
            {"id": "job-1", "domino_run_id": "run-1",
             "status": "submitted", "domino_status": "Queued"},
        ]
        store.get_queued_usernames.return_value = []

        client.get_job_status.return_value = {
            "domino_status": "Succeeded",
            "local_status": "succeeded",
        }

        call_count = 0
        async def _break_after_one(t):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=_break_after_one):
            with pytest.raises(asyncio.CancelledError):
                await je._poll_domino_jobs()

        client.get_job_status.assert_called_with("run-1")
        store.update_job.assert_called()

    @pytest.mark.asyncio
    async def test_poll_skips_when_domino_unavailable(self, _mock_studio):
        je = _import_job_engine()

        # Patch the module-level _DOMINO_AVAILABLE
        original = je._DOMINO_AVAILABLE
        je._DOMINO_AVAILABLE = False

        call_count = 0
        async def _break_after_one(t):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=_break_after_one):
            with pytest.raises(asyncio.CancelledError):
                await je._poll_domino_jobs()

        _mock_studio.domino_job_store.get_active_jobs.assert_not_called()
        je._DOMINO_AVAILABLE = original

    @pytest.mark.asyncio
    async def test_poll_promotes_queued_jobs(self, _mock_studio):
        je = _import_job_engine()
        store = _mock_studio.domino_job_store
        client = _mock_studio.domino_client

        store.get_active_jobs.return_value = []
        store.get_queued_usernames.return_value = ["alice"]
        store.count_active_jobs.return_value = 0
        store.get_oldest_queued_job.return_value = {
            "id": "queued-1", "command": "python main.py",
            "branch": "main", "hardware_tier": None,
            "project_id": "proj-123",
        }
        client.submit_job.return_value = "run-promoted"
        client.build_job_url.return_value = "https://domino/jobs/run-promoted"

        call_count = 0
        async def _break_after_one(t):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=_break_after_one):
            with pytest.raises(asyncio.CancelledError):
                await je._poll_domino_jobs()

        client.submit_job.assert_called_once()
        store.update_job.assert_called()
