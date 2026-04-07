"""Tests for cross-project Domino integration in domino_client.

Functions under test: resolve_project, _domino_request, get_project_context,
submit_job.  Tests mock httpx at the transport layer so they run without a
live Domino API.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

# Ensure auto_model_docs is importable (bare module names like domino_client)
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg_dir = os.path.join(_repo_root, "auto_model_docs")
for p in (_repo_root, _pkg_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

import domino_client as dc
from domino_client import resolve_project, _domino_request, get_project_context, submit_job

# No skip markers needed — all functions now exist in domino_client


@pytest.fixture(autouse=True)
def _setup_env(monkeypatch):
    monkeypatch.setenv("DOMINO_API_HOST", "https://domino.example.com")
    monkeypatch.setenv("DOMINO_USER_API_KEY", "test-api-key")
    monkeypatch.setenv("DOMINO_PROJECT_ID", "proj-123")
    monkeypatch.setenv("DOMINO_PROJECT_OWNER", "test_owner")
    monkeypatch.setenv("DOMINO_PROJECT_NAME", "test_project")
    dc._project_cache.clear()
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_response(data: dict, status: int = 200) -> httpx.Response:
    """Build a fake httpx.Response with JSON body."""
    return httpx.Response(
        status_code=status,
        json=data,
        request=httpx.Request("GET", "https://domino.example.com/test"),
    )


def _text_response(text: str, status: int = 200) -> httpx.Response:
    """Build a fake httpx.Response with plain-text body (for JSON-decode failures)."""
    return httpx.Response(
        status_code=status,
        text=text,
        request=httpx.Request("GET", "https://domino.example.com/test"),
    )


_SAMPLE_PROJECT_RESP = {
    "id": "507f1f77bcf86cd799439011",
    "name": "my-model",
    "owner": {"userName": "alice"},
}


# ===================================================================
# resolve_project() tests
# ===================================================================

class TestResolveProject:
    """Tests for resolve_project(project_id) → ProjectInfo | None.

    The current resolve_project is synchronous and returns None on failure
    (no typed exceptions). It uses _domino_request internally.
    """

    def test_cache_hit_returns_cached(self):
        """Second call for same ID returns cached ProjectInfo without API call."""
        with patch.object(dc, "_domino_request") as mock_req:
            mock_req.return_value = _SAMPLE_PROJECT_RESP
            with patch.dict(dc._project_cache, {}, clear=True):
                first = resolve_project("507f1f77bcf86cd799439011")
                second = resolve_project("507f1f77bcf86cd799439011")

        assert first.name == second.name == "my-model"
        assert first is second  # cached
        assert mock_req.call_count == 1

    def test_cache_miss_calls_api(self):
        """First call for an ID hits the API and caches the result."""
        with patch.object(dc, "_domino_request") as mock_req:
            mock_req.return_value = _SAMPLE_PROJECT_RESP
            with patch.dict(dc._project_cache, {}, clear=True):
                info = resolve_project("507f1f77bcf86cd799439011")

        assert info.id == "507f1f77bcf86cd799439011"
        assert info.name == "my-model"
        assert info.owner_username == "alice"

    def test_api_error_returns_none(self):
        """Network/API errors return None (no typed exceptions)."""
        with patch.object(dc, "_domino_request") as mock_req:
            mock_req.side_effect = Exception("network error")
            with patch.dict(dc._project_cache, {}, clear=True):
                info = resolve_project("000000000000000000000000")

        assert info is None

    def test_missing_name_returns_none(self):
        """Response with missing 'name' field should return None."""
        bad_resp = {"id": "507f1f77bcf86cd799439011", "owner": {"userName": "alice"}}
        with patch.object(dc, "_domino_request") as mock_req:
            mock_req.return_value = bad_resp
            with patch.dict(dc._project_cache, {}, clear=True):
                info = resolve_project("507f1f77bcf86cd799439011")

        assert info is None

    def test_missing_owner_returns_none(self):
        """Response with missing 'owner' field should return None."""
        bad_resp = {"id": "507f1f77bcf86cd799439011", "name": "my-model"}
        with patch.object(dc, "_domino_request") as mock_req:
            mock_req.return_value = bad_resp
            with patch.dict(dc._project_cache, {}, clear=True):
                info = resolve_project("507f1f77bcf86cd799439011")

        assert info is None


# ===================================================================
# _domino_request() tests
# ===================================================================

class TestDominoRequest:
    """Tests for the low-level _domino_request() HTTP helper.

    The current implementation is synchronous and uses httpx.Client.
    """

    def test_successful_json_response(self):
        """Successful request returns parsed JSON."""
        import httpx as _httpx

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"key": "value"}
        mock_resp.raise_for_status = MagicMock()
        mock_client.request.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("httpx.Client", return_value=mock_client):
            result = _domino_request("GET", "/v4/projects")

        assert result == {"key": "value"}

    def test_4xx_raises_immediately(self):
        """Client errors (4xx) should raise immediately without retrying."""
        import httpx as _httpx

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.raise_for_status.side_effect = _httpx.HTTPStatusError(
            "400", request=MagicMock(), response=mock_resp,
        )
        mock_client.request.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("httpx.Client", return_value=mock_client):
            with pytest.raises(_httpx.HTTPStatusError):
                _domino_request("GET", "/v4/projects")

        assert mock_client.request.call_count == 1


# ===================================================================
# get_project_context() tests
# ===================================================================

class TestGetProjectContext:
    """Tests for get_project_context(project_id=...) → tuple.

    Returns (project_id, project_name, owner_username).
    """

    def test_with_project_id_calls_resolve(self):
        """When project_id is provided, resolve_project should be called."""
        mock_info = dc.ProjectInfo(
            id="507f1f77bcf86cd799439011", name="my-model", owner_username="alice",
        )
        with patch.object(dc, "resolve_project", return_value=mock_info) as mock_resolve:
            pid, pname, powner = get_project_context(project_id="507f1f77bcf86cd799439011")

        mock_resolve.assert_called_once_with("507f1f77bcf86cd799439011")
        assert pname == "my-model"
        assert powner == "alice"

    def test_without_project_id_returns_nones(self):
        """When no project_id, should return (None, None, None)."""
        pid, pname, powner = get_project_context(project_id=None)
        assert pid is None
        assert pname is None
        assert powner is None


# ===================================================================
# submit_job() tests
# ===================================================================

class TestSubmitJob:
    """Tests for submit_job() — current REST-based implementation."""

    def test_successful_submission_returns_run_id(self):
        """submit_job should return the run ID from the Domino response."""
        with patch.object(dc, "_domino_request") as mock_req:
            mock_req.return_value = {"id": "run-abc-123"}
            run_id = submit_job(
                command=["python", "main.py"],
                branch="main",
                tier_id="small",
                project_id="proj-123",
            )

        assert run_id == "run-abc-123"

    def test_submit_with_branch_passes_git_ref(self):
        """Branch should be passed as mainRepoGitRef in the payload."""
        with patch.object(dc, "_domino_request") as mock_req:
            mock_req.return_value = {"id": "run-xyz"}
            submit_job(command=["python", "main.py"], branch="feature/x", project_id="proj-123")

        payload = mock_req.call_args.kwargs["json"]
        assert payload["mainRepoGitRef"] == {"type": "branches", "value": "feature/x"}

    def test_no_project_id_raises_runtime_error(self):
        """When project_id is None, submit_job should raise."""
        with pytest.raises(RuntimeError, match="No project ID"):
            submit_job(
                command=["python", "main.py"],
                branch="main",
                project_id=None,
            )

    def test_submit_no_branch_no_tier(self):
        """submit_job with no branch or tier should still succeed."""
        with patch.object(dc, "_domino_request") as mock_req:
            mock_req.return_value = {"id": "run-simple"}
            run_id = submit_job(command=["echo", "hello"], branch=None, project_id="proj-123")

        assert run_id == "run-simple"
        payload = mock_req.call_args.kwargs["json"]
        assert "mainRepoGitRef" not in payload
        assert "overrideHardwareTierId" not in payload


# ===================================================================
# _build_job_command_str() tests
# ===================================================================

try:
    from studio.job_engine import _build_job_command_str as _bld_cmd
    from studio.state import JobRequest as _JR
    _HAS_WEBAPP = True
except Exception:
    _HAS_WEBAPP = False

skip_no_webapp = pytest.mark.skipif(not _HAS_WEBAPP, reason="studio package not importable (fasthtml missing)")


def _make_job_request(**overrides) -> Any:
    """Build a JobRequest with sensible defaults for testing.

    Avoids having to specify every required field in each test.
    """
    if not _HAS_WEBAPP:
        pytest.skip("studio package not importable")

    defaults = dict(
        spec_path=None,
        spec_content=None,
        provider="anthropic",
        model=None,
        api_key=None,
        base_url=None,
        code_root=None,
        max_files=None,
        workers=None,
        planning_workers=None,
        timeout=None,
        notebook=True,
        notebook_path=None,
        experiment_names=None,
        model_names=None,
        latest_only=False,
        verbose=False,
        branch="main",
        hardware_tier="small",
        api_key_source="domino_env",
        spec_filename=None,
    )
    defaults.update(overrides)
    return _JR(**defaults)


@skip_no_webapp
class TestBuildJobCommandStr:
    """Tests for _build_job_command_str (in studio/job_engine.py)."""

    def test_includes_notebook_flag(self):
        """Domino jobs should always include --notebook."""
        req = _make_job_request()
        cmd = _bld_cmd(req, spec_path=None)
        assert "--notebook" in cmd

    def test_includes_spec_path(self):
        """Spec path should appear in the command."""
        req = _make_job_request()
        cmd = _bld_cmd(req, spec_path="/mnt/data/specs/my_spec.yaml")
        assert "--spec" in cmd
        assert "/mnt/data/specs/my_spec.yaml" in cmd

    def test_no_artifacts_copy(self):
        """Command should not include any cp or artifacts step."""
        req = _make_job_request()
        cmd = _bld_cmd(req, spec_path=None)
        assert "cp" not in cmd
        assert "artifacts" not in cmd
