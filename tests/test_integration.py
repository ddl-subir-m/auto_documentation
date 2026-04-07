"""Integration tests — real HTTP requests through Starlette routes.

Tests the full request -> route handler -> response path with:
- Real HTTP via httpx AsyncClient + Starlette app
- Mocked DatasetStore for job_store and spec_store I/O
- Real auth_context ContextVar propagation through middleware
- Mocked Domino API client (no live API calls)

These tests build a lightweight Starlette app that wires the same route
handlers and middleware as web_app_studio.py, without requiring FastHTML
(which needs Python 3.10+).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

# Ensure auto_model_docs is importable
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg_dir = os.path.join(_repo_root, "auto_model_docs")
for p in (_repo_root, _pkg_dir):
    if p not in sys.path:
        sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# Module loader
# ---------------------------------------------------------------------------

def _load_module(name: str, filename: str) -> ModuleType:
    path = os.path.join(_pkg_dir, "studio", filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Build test app
# ---------------------------------------------------------------------------

def _build_test_app(tmp_path: Path, monkeypatch):
    """Construct a Starlette app wired with real route handlers.

    Uses in-memory DatasetStore mock for job_store and spec_store,
    mocked domino_client, and real auth_context propagation.
    """
    import domino_job_store as store
    import spec_store
    import auth_context
    import dataset_store
    import artifact_layout

    # Set up in-memory file store for DatasetStore
    _mem_files: dict[str, bytes] = {}

    class _MemStore:
        dataset_id = "ds-integration"
        snapshot_id = "snap-integration"

        def write_file(self, path, content):
            _mem_files[path] = content

        def read_file(self, path):
            if path not in _mem_files:
                raise FileNotFoundError(path)
            return _mem_files[path]

        def list_files(self, path=""):
            prefix = (path.rstrip("/") + "/") if path else ""
            results = []
            for k in _mem_files:
                if prefix and not k.startswith(prefix):
                    continue
                name = k[len(prefix):] if prefix else k
                if "/" in name:
                    continue  # skip nested
                results.append({"fileName": name, "isDirectory": False, "sizeInBytes": len(_mem_files[k])})
            return results

        def file_exists(self, path):
            return path in _mem_files

        def file_exists_api(self, path):
            """API-only check (same as file_exists for in-memory store)."""
            return path in _mem_files

        def read_file_meta(self, path):
            if path not in _mem_files:
                raise FileNotFoundError(path)
            return {"sizeInBytes": len(_mem_files[path])}

    mem_store = _MemStore()
    # Pre-seed a spec file so dataset:// path verification passes
    _mem_files["spec.yaml"] = b"title: Test\n"
    dataset_store._store = mem_store
    artifact_layout.init_layout()

    # Mock domino_client
    mock_client = MagicMock()
    mock_client.list_branches_api.return_value = [
        {"name": "main"}, {"name": "develop"},
    ]
    mock_client.list_hardware_tiers.return_value = [
        {"id": "small", "name": "Small", "isDefault": True},
    ]
    mock_client.get_project_default_tier.return_value = "small"
    mock_client.submit_job.return_value = "run-integration"
    mock_client.build_job_url.return_value = "https://domino.test/jobs/run-integration"
    mock_client.get_job_status.return_value = {
        "domino_status": "Succeeded", "local_status": "succeeded",
    }
    mock_info = MagicMock()
    mock_info.name = "test-project"
    mock_info.owner_username = "test-owner"
    mock_info.main_repo_id = "repo-123"
    mock_client.resolve_project.return_value = mock_info
    mock_client.set_ui_host = MagicMock()

    mock_datasets = MagicMock()
    mock_datasets.list_datasets.return_value = [
        {"id": "ds-1", "name": "autodoc-specs", "rwSnapshotId": "snap-1"},
    ]
    mock_datasets.AUTODOC_SPECS_DATASET = "autodoc-specs"
    mock_datasets.build_spec_mount_path.return_value = "/mnt/data/autodoc-specs/spec.yaml"
    mock_datasets.get_rw_snapshot_id.return_value = "snap-1"
    mock_datasets.list_files.return_value = []

    # Set up studio.state
    studio_pkg = ModuleType("studio")
    studio_pkg.__path__ = [os.path.join(_pkg_dir, "studio")]
    studio_pkg.__package__ = "studio"
    sys.modules["studio"] = studio_pkg

    mock_state = ModuleType("studio.state")
    mock_state._DOMINO_AVAILABLE = True
    mock_state.domino_client = mock_client
    mock_state.domino_job_store = store
    mock_state.spec_store = spec_store
    mock_state.domino_datasets = mock_datasets
    mock_state.auth_context = auth_context
    mock_state._TARGET_PROJECT_ID = "proj-integration"
    mock_state._TARGET_PROJECT_NAME = "test-project"
    mock_state._get_username = lambda: "integration_user"
    mock_state._max_jobs = lambda: 2
    mock_state._get_target_project_id = lambda: "proj-integration"
    mock_state._get_target_project_name = lambda: "test-project"
    mock_state._resolve_target_project_name = lambda pid=None: "test-project"
    mock_state._resolve_request_project_id = lambda req: "proj-integration"
    mock_state._get_default_code_root = lambda: Path("/mnt/code")
    mock_state.logger = MagicMock()

    from dataclasses import dataclass
    from typing import Optional

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

    @dataclass
    class EnvironmentWarning:
        level: str
        message: str
        action: str

    mock_state.JobRequest = JobRequest
    mock_state.DominoJobRecord = DominoJobRecord
    mock_state.EnvironmentWarning = EnvironmentWarning

    sys.modules["studio.state"] = mock_state

    # Mock fasthtml.common — provide FT component stubs that return plain HTML strings
    mock_fh = ModuleType("fasthtml.common")

    def _make_ft(tag_name):
        def ft_fn(*children, **attrs):
            parts = [f"<{tag_name}"]
            for k, v in attrs.items():
                if k.startswith("hx_") or k.startswith("data_"):
                    k = k.replace("_", "-")
                if k == "cls":
                    k = "class"
                if k == "for_":
                    k = "for"
                if v is True:
                    parts.append(f" {k}")
                elif v is not False and v is not None:
                    parts.append(f' {k}="{v}"')
            parts.append(">")
            for child in children:
                if child is None:
                    continue
                parts.append(str(child))
            parts.append(f"</{tag_name}>")
            return "".join(parts)
        return ft_fn

    for tag in ("Div", "P", "A", "Span", "H2", "H3", "Select", "Option", "Input",
                "Label", "Table", "Thead", "Tbody", "Tr", "Th", "Td", "Ul", "Li",
                "Pre", "Button", "Details", "Summary", "Code"):
        setattr(mock_fh, tag, _make_ft(tag.lower()))

    class FT:
        pass
    mock_fh.FT = FT

    sys.modules["fasthtml"] = ModuleType("fasthtml")
    sys.modules["fasthtml.common"] = mock_fh

    # Mock autodoc.core.models for routes_spec
    mock_models = ModuleType("autodoc.core.models")
    mock_doc_spec = MagicMock()
    mock_doc_spec.validate_spec.return_value = []
    mock_models.DocumentSpec = mock_doc_spec
    mock_models.detect_language = MagicMock(return_value=(None, 0))
    mock_models.LANGUAGE_PROFILES = {}
    sys.modules["autodoc"] = ModuleType("autodoc")
    sys.modules["autodoc.core"] = ModuleType("autodoc.core")
    sys.modules["autodoc.core.models"] = mock_models

    # Load route modules fresh
    for mod_name in ("studio.ui_components", "studio.job_engine",
                     "studio.routes_api", "studio.routes_job", "studio.routes_spec"):
        sys.modules.pop(mod_name, None)

    ui_mod = _load_module("studio.ui_components", "ui_components.py")
    je_mod = _load_module("studio.job_engine", "job_engine.py")
    api_mod = _load_module("studio.routes_api", "routes_api.py")
    spec_mod = _load_module("studio.routes_spec", "routes_spec.py")
    job_mod = _load_module("studio.routes_job", "routes_job.py")

    # Collect route handlers
    handlers = {}

    def fake_rt(path):
        def decorator(fn):
            handlers[path] = fn
            return fn
        return decorator

    api_mod.register_api_routes(fake_rt)
    spec_mod.register_spec_routes(fake_rt)
    job_mod.register_job_routes(fake_rt)

    # Build Starlette routes from collected handlers
    import asyncio
    import inspect

    def _wrap(handler):
        """Wrap a route handler into a proper Starlette endpoint."""
        if inspect.iscoroutinefunction(handler):
            async def endpoint(request):
                result = await handler(request)
                if isinstance(result, Response):
                    return result
                return Response(str(result), media_type="text/html")
            return endpoint
        else:
            # Check if handler takes no args (like api_download_template)
            sig = inspect.signature(handler)
            params = list(sig.parameters.keys())

            async def endpoint(request):
                if params:
                    result = handler(request)
                else:
                    result = handler()
                if isinstance(result, Response):
                    return result
                return Response(str(result), media_type="text/html")
            return endpoint

    routes = []
    for path, handler in handlers.items():
        # Starlette Route supports both GET and POST
        routes.append(Route(path, _wrap(handler), methods=["GET", "POST"]))

    app = Starlette(routes=routes)

    # Add auth middleware
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware

    class AuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            forwarded = request.headers.get("authorization")
            auth_context.set_request_auth_header(forwarded)
            try:
                response = await call_next(request)
            finally:
                auth_context.set_request_auth_header(None)
            return response

    app.add_middleware(AuthMiddleware)

    return {
        "app": app,
        "store": store,
        "spec_store": spec_store,
        "domino_client": mock_client,
        "domino_datasets": mock_datasets,
        "auth_context": auth_context,
        "doc_spec": mock_doc_spec,
        "_restore": None,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def integration_env(tmp_path, monkeypatch):
    """Provide a wired test app with real HTTP transport."""
    monkeypatch.setenv("DOMINO_API_HOST", "https://domino.test")
    monkeypatch.setenv("DOMINO_USER_API_KEY", "test-key")
    monkeypatch.setenv("DOMINO_STARTING_USERNAME", "integration_user")
    monkeypatch.setenv("AUTODOC_MAX_JOBS", "2")

    saved_modules = {}
    for key in ("studio", "studio.state", "studio.ui_components", "studio.job_engine",
                "studio.routes_api", "studio.routes_job", "studio.routes_spec",
                "fasthtml", "fasthtml.common", "autodoc", "autodoc.core", "autodoc.core.models"):
        saved_modules[key] = sys.modules.get(key)

    env = _build_test_app(tmp_path, monkeypatch)

    yield env

    # Restore
    import dataset_store
    import artifact_layout
    dataset_store.reset_store()
    artifact_layout.reset_layout()

    for key, val in saved_modules.items():
        if val is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = val


@pytest.fixture
def client(integration_env):
    """Provide a synchronous Starlette TestClient."""
    return TestClient(integration_env["app"])


# ===========================================================================
# API route integration tests
# ===========================================================================

class TestApiRoutesIntegration:

    def test_datasets_endpoint(self, client, integration_env):
        resp = client.get("/api/datasets?projectId=proj-integration")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "autodoc-specs"

    def test_datasets_error_returns_500(self, client, integration_env):
        integration_env["domino_datasets"].list_datasets.side_effect = RuntimeError("API down")
        resp = client.get("/api/datasets?projectId=proj-integration")
        assert resp.status_code == 500
        assert "error" in resp.json()
        integration_env["domino_datasets"].list_datasets.side_effect = None
        integration_env["domino_datasets"].list_datasets.return_value = [
            {"id": "ds-1", "name": "autodoc-specs", "rwSnapshotId": "snap-1"},
        ]

    def test_dataset_files_requires_dataset_id(self, client):
        resp = client.get("/api/dataset-files")
        assert resp.status_code == 400

    def test_dataset_files_with_id(self, client, integration_env):
        integration_env["domino_datasets"].list_files.return_value = [
            {"fileName": "spec.yaml", "isDirectory": False},
        ]
        resp = client.get("/api/dataset-files?datasetId=ds-1&projectId=proj-integration")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["fileName"] == "spec.yaml"

    def test_download_template(self, client):
        resp = client.get("/api/download-template")
        assert resp.status_code == 200

    def test_resolve_project(self, client, integration_env):
        resp = client.get("/api/resolve-project?projectId=proj-integration")
        assert resp.status_code == 200
        body = resp.text
        assert "test-owner" in body
        assert "test-project" in body

    def test_resolve_project_empty_id(self, client):
        resp = client.get("/api/resolve-project")
        assert resp.status_code == 200

    def test_branches(self, client, integration_env):
        resp = client.get("/api/branches?projectId=proj-integration")
        assert resp.status_code == 200
        body = resp.text
        assert "main" in body

    def test_hardware_tiers(self, client):
        resp = client.get("/api/hardware-tiers?projectId=proj-integration")
        assert resp.status_code == 200


# ===========================================================================
# Spec route integration tests
# ===========================================================================

class TestSpecRoutesIntegration:

    def test_validate_spec_empty(self, client):
        resp = client.post("/validate-spec", data={})
        assert resp.status_code == 200
        assert "No spec content" in resp.text

    def test_validate_spec_valid(self, client, integration_env):
        integration_env["doc_spec"].validate_spec.return_value = []
        resp = client.post(
            "/validate-spec",
            files={"spec_upload": ("spec.yaml", b"title: Test\n", "application/x-yaml")},
        )
        assert resp.status_code == 200
        integration_env["doc_spec"].validate_spec.assert_called()

    def test_validate_spec_with_errors(self, client, integration_env):
        integration_env["doc_spec"].validate_spec.return_value = ["Missing title field"]
        resp = client.post(
            "/validate-spec",
            files={"spec_upload": ("bad.yaml", b"sections: []\n", "application/x-yaml")},
        )
        assert resp.status_code == 200
        assert "failed" in resp.text.lower() or "Missing title" in resp.text

    def test_spec_list_empty(self, client):
        resp = client.get("/spec-list?projectId=proj-integration")
        assert resp.status_code == 200
        assert "No saved spec" in resp.text

    def test_save_spec(self, client):
        resp = client.post("/save-spec", data={
            "spec_filename": "test.yaml",
            "spec_content": "title: Test\n",
        })
        assert resp.status_code == 200


# ===========================================================================
# Job route integration tests
# ===========================================================================

class TestJobRoutesIntegration:

    def test_job_history_empty(self, client):
        resp = client.get("/job-history")
        assert resp.status_code == 200

    def test_submit_job_creates_record(self, client, integration_env):
        """Submit via HTTP, verify record appears in job index."""
        store = integration_env["store"]

        resp = client.post("/run", data={
            "spec_path": "dataset://autodoc-specs/spec.yaml",
            "provider": "anthropic",
            "target_project": "proj-integration",
        })
        assert resp.status_code == 200

        jobs = store.get_user_jobs("integration_user")
        assert len(jobs) >= 1
        assert jobs[0]["username"] == "integration_user"

    def test_cancel_queued_jobs(self, client, integration_env):
        """Cancel via HTTP, verify status change in job index."""
        store = integration_env["store"]
        job_id = store.create_job(
            "integration_user", "main", "small", "/spec.yaml",
            project_id="proj-integration",
        )

        resp = client.post("/cancel-queued-jobs")
        assert resp.status_code == 200

        job = store.get_job(job_id)
        assert job["status"] == "cancelled"

    def test_stop_job_calls_domino_api(self, client, integration_env):
        """Stop via HTTP, verify Domino API call and job index update."""
        store = integration_env["store"]
        job_id = store.create_job(
            "integration_user", "main", "small", "/spec.yaml",
            project_id="proj-integration",
        )
        store.update_job(job_id, status="submitted", domino_run_id="run-stop-test")

        resp = client.post("/stop-job-history", data={"job_id": job_id})
        assert resp.status_code == 200

        integration_env["domino_client"].stop_job.assert_called_with(
            "run-stop-test", project_id="proj-integration",
        )
        job = store.get_job(job_id)
        assert job["status"] == "cancelled"

    def test_job_history_returns_submitted_jobs(self, client, integration_env):
        """Jobs created via /run appear in /job-history."""
        client.post("/run", data={
            "spec_path": "dataset://autodoc-specs/spec.yaml",
            "provider": "anthropic",
            "target_project": "proj-integration",
        })

        resp = client.get("/job-history")
        assert resp.status_code == 200
        assert len(resp.text) > 50  # not empty


# ===========================================================================
# Auth context middleware integration
# ===========================================================================

class TestAuthMiddlewareIntegration:

    def test_jwt_cleared_after_request(self, client, integration_env):
        """Auth middleware should clear the JWT after each request."""
        ac = integration_env["auth_context"]
        assert ac.get_request_auth_header() is None

        client.get(
            "/api/datasets?projectId=proj-integration",
            headers={"Authorization": "Bearer integration-jwt"},
        )

        # After request completes, JWT should be cleared
        assert ac.get_request_auth_header() is None

    def test_requests_without_auth_succeed(self, client):
        resp = client.get("/api/hardware-tiers?projectId=proj-integration")
        assert resp.status_code == 200


# ===========================================================================
# Cross-cutting integration
# ===========================================================================

class TestCrossCuttingIntegration:

    def test_max_jobs_enforced(self, client, integration_env):
        """Jobs beyond the limit should be queued, not submitted."""
        store = integration_env["store"]

        # Create 2 active jobs (AUTODOC_MAX_JOBS=2)
        for i in range(2):
            jid = store.create_job(
                "integration_user", "main", "small", "/spec.yaml",
                project_id="proj-integration",
            )
            store.update_job(jid, status="submitted", domino_run_id=f"run-{i}")

        # Submit a 3rd via HTTP
        client.post("/run", data={
            "spec_path": "dataset://autodoc-specs/spec.yaml",
            "provider": "anthropic",
            "target_project": "proj-integration",
        })

        jobs = store.get_user_jobs("integration_user")
        newest = jobs[0]
        assert newest["status"] == "queued"

    def test_cancel_then_history_reflects_change(self, client, integration_env):
        """Cancel a job, then verify history endpoint shows updated status."""
        store = integration_env["store"]
        job_id = store.create_job(
            "integration_user", "main", "small", "/spec.yaml",
            project_id="proj-integration",
        )

        # Cancel
        client.post("/cancel-queued-jobs")

        # History should show cancelled
        resp = client.get("/job-history")
        assert resp.status_code == 200
        body = resp.text.upper()
        assert "CANCELLED" in body
