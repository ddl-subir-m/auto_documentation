"""Tests for studio route handlers — routes_api.py, routes_job.py, routes_spec.py."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure auto_model_docs is importable
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg_dir = os.path.join(_repo_root, "auto_model_docs")
for p in (_repo_root, _pkg_dir):
    if p not in sys.path:
        sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# Shared test types
# ---------------------------------------------------------------------------

@dataclass
class _MockJobRequest:
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
class _MockDominoJobRecord:
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_studio_modules():
    """Set up mock studio modules for route imports."""
    # state module
    mock_state = ModuleType("studio.state")
    mock_state._DOMINO_AVAILABLE = True
    mock_state._get_username = MagicMock(return_value="test_user")
    mock_state._max_jobs = MagicMock(return_value=1)
    mock_state._get_target_project_id = MagicMock(return_value="proj-123")
    mock_state._get_target_project_name = MagicMock(return_value="my-project")
    mock_state._resolve_request_project_id = MagicMock(return_value="proj-123")
    mock_state._resolve_target_project_name = MagicMock(return_value="my-project")
    mock_state._get_default_code_root = MagicMock(return_value=Path("/mnt/code"))
    mock_state.logger = MagicMock()
    mock_state.JobRequest = _MockJobRequest
    mock_state.DominoJobRecord = _MockDominoJobRecord
    mock_state.domino_client = MagicMock()
    mock_state.domino_job_store = MagicMock()
    mock_state.spec_store = MagicMock()
    mock_state.domino_datasets = MagicMock()

    # ui_components module
    mock_ui = ModuleType("studio.ui_components")
    mock_ui._render_job_history_table = MagicMock(return_value=MagicMock())
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
    mock_ui._db_record_to_dataclass = lambda row: _MockDominoJobRecord(
        id=row["id"], username=row["username"],
    )

    # job_engine module
    mock_job_engine = ModuleType("studio.job_engine")
    mock_job_engine._parse_request = AsyncMock(return_value=_MockJobRequest(
        spec_path="/spec.yaml", project_id="proj-123",
    ))
    mock_job_engine._submit_domino_job = AsyncMock()

    # fasthtml.common — provide real Response import + FT component stubs
    from starlette.responses import Response as StarletteResponse, FileResponse as StarletteFileResponse
    mock_fh_common = ModuleType("fasthtml.common")
    fh_names = ("Div", "P", "A", "Span", "H3", "Select", "Option", "Input",
                "Label", "Table", "Thead", "Tbody", "Tr", "Th", "Td",
                "Ul", "Li", "Pre", "Button", "Details", "Summary")
    for name in fh_names:
        setattr(mock_fh_common, name, lambda *a, _n=name, **kw: MagicMock(__ft_name__=_n))
    mock_fh_common.FT = type("FT", (), {})

    mock_fh = ModuleType("fasthtml")
    mock_fh.common = mock_fh_common

    # autodoc.core.models — for routes_spec
    mock_autodoc_models = ModuleType("autodoc.core.models")
    mock_autodoc_models.DocumentSpec = MagicMock()
    mock_autodoc_models.detect_language = MagicMock()
    mock_autodoc_models.LANGUAGE_PROFILES = {}

    # studio package
    studio_pkg = ModuleType("studio")
    studio_pkg.__path__ = [os.path.join(_pkg_dir, "studio")]
    studio_pkg.__package__ = "studio"

    saved = {}
    mod_keys = (
        "studio", "studio.state", "studio.ui_components", "studio.job_engine",
        "studio.routes_api", "studio.routes_job", "studio.routes_spec",
        "fasthtml", "fasthtml.common",
        "autodoc", "autodoc.core", "autodoc.core.models",
    )
    for key in mod_keys:
        saved[key] = sys.modules.get(key)

    sys.modules["studio"] = studio_pkg
    sys.modules["studio.state"] = mock_state
    sys.modules["studio.ui_components"] = mock_ui
    sys.modules["studio.job_engine"] = mock_job_engine
    sys.modules["fasthtml"] = mock_fh
    sys.modules["fasthtml.common"] = mock_fh_common
    sys.modules["autodoc"] = ModuleType("autodoc")
    sys.modules["autodoc.core"] = ModuleType("autodoc.core")
    sys.modules["autodoc.core.models"] = mock_autodoc_models

    yield {
        "state": mock_state,
        "ui": mock_ui,
        "job_engine": mock_job_engine,
        "autodoc_models": mock_autodoc_models,
    }

    for key, val in saved.items():
        if val is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = val


def _make_request(query_params=None, form_data=None):
    """Create a mock Starlette Request."""
    req = MagicMock()
    req.query_params = query_params or {}

    if form_data is not None:
        async def _form():
            return form_data
        req.form = _form
    else:
        async def _empty_form():
            return {}
        req.form = _empty_form

    return req


def _import_routes_api():
    sys.modules.pop("studio.routes_api", None)
    path = os.path.join(_pkg_dir, "studio", "routes_api.py")
    return _load_module("studio.routes_api", path)


def _import_routes_job():
    sys.modules.pop("studio.routes_job", None)
    path = os.path.join(_pkg_dir, "studio", "routes_job.py")
    return _load_module("studio.routes_job", path)


def _import_routes_spec():
    sys.modules.pop("studio.routes_spec", None)
    path = os.path.join(_pkg_dir, "studio", "routes_spec.py")
    return _load_module("studio.routes_spec", path)


def _register(mod, register_fn_name="register_api_routes"):
    """Call a register_*_routes function and capture the handlers."""
    routes = {}

    def fake_rt(path):
        def decorator(fn):
            routes[path] = fn
            return fn
        return decorator

    getattr(mod, register_fn_name)(fake_rt)
    return routes


# ===========================================================================
# routes_api.py
# ===========================================================================

class TestApiRoutes:
    def test_branches_returns_select_when_available(self, _mock_studio_modules):
        mod = _import_routes_api()
        routes = _register(mod, "register_api_routes")
        client = _mock_studio_modules["state"].domino_client
        client.list_branches_api.return_value = [{"name": "main"}, {"name": "develop"}]
        req = _make_request(query_params={"projectId": "proj-123"})
        routes["/api/branches"](req)
        client.list_branches_api.assert_called_once_with("proj-123", search="")

    def test_branches_fallback_when_no_branches(self, _mock_studio_modules):
        mod = _import_routes_api()
        routes = _register(mod, "register_api_routes")
        _mock_studio_modules["state"].domino_client.list_branches_api.return_value = []
        req = _make_request(query_params={"projectId": "proj-123"})
        routes["/api/branches"](req)  # should not raise

    def test_branches_fallback_when_no_project_id(self, _mock_studio_modules):
        mod = _import_routes_api()
        routes = _register(mod, "register_api_routes")
        req = _make_request(query_params={})
        routes["/api/branches"](req)  # should not raise

    def test_hardware_tiers_returns_select(self, _mock_studio_modules):
        mod = _import_routes_api()
        routes = _register(mod, "register_api_routes")
        client = _mock_studio_modules["state"].domino_client
        client.list_hardware_tiers.return_value = [
            {"id": "tier-1", "name": "Small", "isDefault": True},
        ]
        client.get_project_default_tier.return_value = "tier-1"
        req = _make_request(query_params={"projectId": "proj-123"})
        routes["/api/hardware-tiers"](req)
        client.list_hardware_tiers.assert_called_once()

    def test_hardware_tiers_when_domino_unavailable(self, _mock_studio_modules):
        _mock_studio_modules["state"]._DOMINO_AVAILABLE = False
        mod = _import_routes_api()
        routes = _register(mod, "register_api_routes")
        req = _make_request()
        routes["/api/hardware-tiers"](req)

    def test_datasets_returns_json(self, _mock_studio_modules):
        mod = _import_routes_api()
        routes = _register(mod, "register_api_routes")
        ds = _mock_studio_modules["state"].domino_datasets
        ds.list_datasets.return_value = [{"id": "ds-1", "name": "test-ds"}]
        req = _make_request(query_params={"projectId": "proj-123"})
        result = routes["/api/datasets"](req)
        ds.list_datasets.assert_called_once()

    def test_datasets_returns_empty_when_domino_unavailable(self, _mock_studio_modules):
        _mock_studio_modules["state"]._DOMINO_AVAILABLE = False
        mod = _import_routes_api()
        routes = _register(mod, "register_api_routes")
        req = _make_request()
        result = routes["/api/datasets"](req)
        body = json.loads(result.body.decode())
        assert body == []

    def test_datasets_error_returns_500(self, _mock_studio_modules):
        mod = _import_routes_api()
        routes = _register(mod, "register_api_routes")
        ds = _mock_studio_modules["state"].domino_datasets
        ds.list_datasets.side_effect = RuntimeError("API error")
        req = _make_request(query_params={"projectId": "proj-123"})
        result = routes["/api/datasets"](req)
        assert result.status_code == 500

    def test_dataset_files_requires_dataset_id(self, _mock_studio_modules):
        mod = _import_routes_api()
        routes = _register(mod, "register_api_routes")
        req = _make_request(query_params={})
        result = routes["/api/dataset-files"](req)
        assert result.status_code == 400

    def test_dataset_files_returns_files(self, _mock_studio_modules):
        mod = _import_routes_api()
        routes = _register(mod, "register_api_routes")
        ds = _mock_studio_modules["state"].domino_datasets
        ds.get_rw_snapshot_id.return_value = "snap-1"
        ds.list_files.return_value = [{"fileName": "spec.yaml"}]
        req = _make_request(query_params={"datasetId": "ds-1", "projectId": "proj-123"})
        result = routes["/api/dataset-files"](req)
        ds.list_files.assert_called_once()

    def test_download_template(self, _mock_studio_modules):
        mod = _import_routes_api()
        routes = _register(mod, "register_api_routes")
        result = routes["/api/download-template"]()
        # Either FileResponse (exists) or Response(404)
        assert result is not None

    def test_resolve_project(self, _mock_studio_modules):
        mod = _import_routes_api()
        routes = _register(mod, "register_api_routes")
        client = _mock_studio_modules["state"].domino_client
        mock_info = MagicMock()
        mock_info.name = "my-project"
        mock_info.owner_username = "alice"
        client.resolve_project.return_value = mock_info
        req = _make_request(query_params={"projectId": "proj-123"})
        routes["/api/resolve-project"](req)
        client.resolve_project.assert_called_with("proj-123")

    def test_resolve_project_empty_id(self, _mock_studio_modules):
        mod = _import_routes_api()
        routes = _register(mod, "register_api_routes")
        req = _make_request(query_params={})
        routes["/api/resolve-project"](req)  # should not raise


# ===========================================================================
# routes_job.py
# ===========================================================================

class TestJobRoutes:
    @pytest.mark.asyncio
    async def test_run_submits_job(self, _mock_studio_modules):
        mod = _import_routes_job()
        routes = _register(mod, "register_job_routes")
        req = _make_request()
        await routes["/run"](req)
        _mock_studio_modules["job_engine"]._submit_domino_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_handles_submission_error(self, _mock_studio_modules):
        mod = _import_routes_job()
        routes = _register(mod, "register_job_routes")
        _mock_studio_modules["job_engine"]._submit_domino_job.side_effect = RuntimeError("fail")
        store = _mock_studio_modules["state"].domino_job_store
        store.create_job.return_value = "err-job"
        req = _make_request()
        await routes["/run"](req)
        store.update_job.assert_called()

    def test_job_history(self, _mock_studio_modules):
        mod = _import_routes_job()
        routes = _register(mod, "register_job_routes")
        routes["/job-history"]()
        _mock_studio_modules["ui"]._render_job_history_table.assert_called_with("test_user")

    def test_cancel_queued_jobs(self, _mock_studio_modules):
        mod = _import_routes_job()
        routes = _register(mod, "register_job_routes")
        routes["/cancel-queued-jobs"]()
        _mock_studio_modules["state"].domino_job_store.cancel_queued_jobs.assert_called_with("test_user")

    @pytest.mark.asyncio
    async def test_stop_job(self, _mock_studio_modules):
        mod = _import_routes_job()
        routes = _register(mod, "register_job_routes")
        store = _mock_studio_modules["state"].domino_job_store
        store.get_job.return_value = {
            "id": "j1", "domino_run_id": "run-1", "project_id": "proj-123",
        }
        req = _make_request(form_data={"job_id": "j1"})
        await routes["/stop-job-history"](req)
        _mock_studio_modules["state"].domino_client.stop_job.assert_called_once()
        store.update_job.assert_called_with("j1", status="cancelled")


# ===========================================================================
# routes_spec.py
# ===========================================================================

class TestSpecRoutes:
    @pytest.mark.asyncio
    async def test_validate_spec_valid(self, _mock_studio_modules):
        mod = _import_routes_spec()
        routes = _register(mod, "register_spec_routes")
        _mock_studio_modules["autodoc_models"].DocumentSpec.validate_spec.return_value = []

        mock_upload = MagicMock()
        mock_upload.read = AsyncMock(return_value=b"title: Test\nsections: []")
        req = _make_request(form_data={"spec_upload": mock_upload})
        await routes["/validate-spec"](req)
        _mock_studio_modules["autodoc_models"].DocumentSpec.validate_spec.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_spec_with_errors(self, _mock_studio_modules):
        mod = _import_routes_spec()
        routes = _register(mod, "register_spec_routes")
        _mock_studio_modules["autodoc_models"].DocumentSpec.validate_spec.return_value = ["Missing title"]

        mock_upload = MagicMock()
        mock_upload.read = AsyncMock(return_value=b"sections: []")
        req = _make_request(form_data={"spec_upload": mock_upload})
        await routes["/validate-spec"](req)

    @pytest.mark.asyncio
    async def test_validate_spec_empty_content(self, _mock_studio_modules):
        mod = _import_routes_spec()
        routes = _register(mod, "register_spec_routes")
        req = _make_request(form_data={})
        await routes["/validate-spec"](req)

    @pytest.mark.asyncio
    async def test_save_spec(self, _mock_studio_modules):
        mod = _import_routes_spec()
        routes = _register(mod, "register_spec_routes")
        store = _mock_studio_modules["state"].spec_store
        store.save_spec.return_value = Path("/saved/spec.yaml")
        req = _make_request(form_data={
            "spec_filename": "my_spec.yaml", "spec_content": "title: Test",
        })
        result = await routes["/save-spec"](req)
        store.save_spec.assert_called_once()

    def test_spec_list_with_specs(self, _mock_studio_modules):
        mod = _import_routes_spec()
        routes = _register(mod, "register_spec_routes")
        store = _mock_studio_modules["state"].spec_store
        store.list_specs.return_value = [
            {"name": "spec1.yaml", "size_kb": "2.1"},
        ]
        req = _make_request(query_params={"projectId": "proj-123"})
        routes["/spec-list"](req)
        store.list_specs.assert_called_once()

    def test_spec_list_empty(self, _mock_studio_modules):
        mod = _import_routes_spec()
        routes = _register(mod, "register_spec_routes")
        _mock_studio_modules["state"].spec_store.list_specs.return_value = []
        req = _make_request(query_params={"projectId": "proj-123"})
        routes["/spec-list"](req)

    # delete_spec and cleanup_specs routes removed — Domino Datasets API
    # does not support file-level deletion.
