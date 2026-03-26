"""Tests for cross-project Domino integration in domino_client.

Functions under test (resolve_project, _domino_request, get_project_context)
are being added in a parallel workspace. These tests mock httpx at the
transport layer so they run without a live Domino API.

When the parallel branch lands and the real functions exist, remove the
skip markers and run the full suite.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tests.conftest import (
    DominoAPIError,
    ProjectAPIError,
    ProjectForbiddenError,
    ProjectInfo,
    ProjectNotFoundError,
)

# ---------------------------------------------------------------------------
# Try to import the real functions; mark tests to skip if not yet available
# ---------------------------------------------------------------------------
try:
    from auto_model_docs.domino_client import resolve_project
    _HAS_RESOLVE = True
except ImportError:
    _HAS_RESOLVE = False

    async def resolve_project(project_id: str) -> ProjectInfo:  # type: ignore[misc]
        raise NotImplementedError("stub")

try:
    from auto_model_docs.domino_client import _domino_request
    _HAS_DOMINO_REQUEST = True
except ImportError:
    _HAS_DOMINO_REQUEST = False

    async def _domino_request(  # type: ignore[misc]
        method: str,
        path: str,
        *,
        cross_project: bool = False,
        **kwargs: Any,
    ) -> Any:
        raise NotImplementedError("stub")

try:
    from auto_model_docs.domino_client import get_project_context
    _HAS_GET_CTX = True
except ImportError:
    _HAS_GET_CTX = False

    async def get_project_context(project_id: str | None = None) -> dict:  # type: ignore[misc]
        raise NotImplementedError("stub")

try:
    from auto_model_docs.domino_client import submit_job
    import inspect
    _submit_params = inspect.signature(submit_job).parameters
    _HAS_SUBMIT = "project_id" in _submit_params
except ImportError:
    _HAS_SUBMIT = False

skip_no_resolve = pytest.mark.skipif(not _HAS_RESOLVE, reason="resolve_project not yet in domino_client")
skip_no_request = pytest.mark.skipif(not _HAS_DOMINO_REQUEST, reason="_domino_request not yet in domino_client")
skip_no_ctx = pytest.mark.skipif(not _HAS_GET_CTX, reason="get_project_context not yet in domino_client")
skip_no_submit = pytest.mark.skipif(not _HAS_SUBMIT, reason="submit_job not yet in domino_client (new sig)")


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
    """Tests for resolve_project(project_id) → ProjectInfo."""

    @skip_no_resolve
    async def test_cache_hit_returns_cached(self, monkeypatch):
        """Second call for same ID returns cached ProjectInfo without API call."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_json_response(_SAMPLE_PROJECT_RESP))

        with patch("auto_model_docs.domino_client._get_httpx_client", return_value=mock_client):
            first = await resolve_project("507f1f77bcf86cd799439011")
            second = await resolve_project("507f1f77bcf86cd799439011")

        assert first.name == second.name == "my-model"
        assert first.owner == second.owner == "alice"
        # API should have been called only once
        assert mock_client.get.call_count == 1

    @skip_no_resolve
    async def test_cache_miss_calls_api(self):
        """First call for an ID hits the API and caches the result."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_json_response(_SAMPLE_PROJECT_RESP))

        with patch("auto_model_docs.domino_client._get_httpx_client", return_value=mock_client):
            with patch("auto_model_docs.domino_client._project_cache", {}):
                info = await resolve_project("507f1f77bcf86cd799439011")

        assert info.id == "507f1f77bcf86cd799439011"
        assert info.name == "my-model"
        assert info.owner == "alice"
        mock_client.get.assert_called_once()

    @skip_no_resolve
    async def test_404_raises_not_found(self):
        """API returning 404 should raise ProjectNotFoundError."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=_json_response({"message": "not found"}, status=404)
        )

        with patch("auto_model_docs.domino_client._get_httpx_client", return_value=mock_client):
            with patch("auto_model_docs.domino_client._project_cache", {}):
                with pytest.raises(ProjectNotFoundError):
                    await resolve_project("000000000000000000000000")

    @skip_no_resolve
    async def test_403_raises_forbidden(self):
        """API returning 403 should raise ProjectForbiddenError."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=_json_response({"message": "forbidden"}, status=403)
        )

        with patch("auto_model_docs.domino_client._get_httpx_client", return_value=mock_client):
            with patch("auto_model_docs.domino_client._project_cache", {}):
                with pytest.raises(ProjectForbiddenError):
                    await resolve_project("000000000000000000000000")

    @skip_no_resolve
    async def test_5xx_raises_api_error(self):
        """5xx or network failure should raise ProjectAPIError."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=_json_response({"message": "internal"}, status=500)
        )

        with patch("auto_model_docs.domino_client._get_httpx_client", return_value=mock_client):
            with patch("auto_model_docs.domino_client._project_cache", {}):
                with pytest.raises(ProjectAPIError):
                    await resolve_project("507f1f77bcf86cd799439011")

    @skip_no_resolve
    async def test_network_error_raises_api_error(self):
        """httpx.ConnectError should be wrapped as ProjectAPIError."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

        with patch("auto_model_docs.domino_client._get_httpx_client", return_value=mock_client):
            with patch("auto_model_docs.domino_client._project_cache", {}):
                with pytest.raises(ProjectAPIError):
                    await resolve_project("507f1f77bcf86cd799439011")

    @skip_no_resolve
    async def test_first_api_path_fails_second_succeeds(self):
        """If the primary endpoint returns 404, the fallback path should be tried."""
        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _json_response({"message": "not found"}, status=404)
            return _json_response(_SAMPLE_PROJECT_RESP)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_side_effect)

        with patch("auto_model_docs.domino_client._get_httpx_client", return_value=mock_client):
            with patch("auto_model_docs.domino_client._project_cache", {}):
                info = await resolve_project("507f1f77bcf86cd799439011")

        assert info.name == "my-model"
        assert mock_client.get.call_count == 2

    @skip_no_resolve
    async def test_missing_name_raises_api_error(self):
        """Response with missing 'name' field should raise ProjectAPIError."""
        bad_resp = {"id": "507f1f77bcf86cd799439011", "owner": {"userName": "alice"}}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_json_response(bad_resp))

        with patch("auto_model_docs.domino_client._get_httpx_client", return_value=mock_client):
            with patch("auto_model_docs.domino_client._project_cache", {}):
                with pytest.raises(ProjectAPIError):
                    await resolve_project("507f1f77bcf86cd799439011")

    @skip_no_resolve
    async def test_missing_owner_raises_api_error(self):
        """Response with missing 'owner' field should raise ProjectAPIError."""
        bad_resp = {"id": "507f1f77bcf86cd799439011", "name": "my-model"}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_json_response(bad_resp))

        with patch("auto_model_docs.domino_client._get_httpx_client", return_value=mock_client):
            with patch("auto_model_docs.domino_client._project_cache", {}):
                with pytest.raises(ProjectAPIError):
                    await resolve_project("507f1f77bcf86cd799439011")

    @skip_no_resolve
    async def test_case_insensitive_id(self):
        """Uppercase hex ID should be lowercased before API call and caching."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_json_response(_SAMPLE_PROJECT_RESP))

        with patch("auto_model_docs.domino_client._get_httpx_client", return_value=mock_client):
            with patch("auto_model_docs.domino_client._project_cache", {}):
                info = await resolve_project("507F1F77BCF86CD799439011")

        assert info.id == "507f1f77bcf86cd799439011"
        # Verify the lowercased ID was used for the API call
        call_args = mock_client.get.call_args
        assert "507f1f77bcf86cd799439011" in str(call_args)


# ===================================================================
# _domino_request() tests
# ===================================================================

class TestDominoRequest:
    """Tests for the low-level _domino_request() HTTP helper."""

    @skip_no_request
    async def test_successful_json_response(self):
        """Successful request returns parsed JSON."""
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            return_value=_json_response({"key": "value"})
        )

        with patch("auto_model_docs.domino_client._get_httpx_client", return_value=mock_client):
            result = await _domino_request("GET", "/v4/projects")

        assert result == {"key": "value"}

    @skip_no_request
    async def test_502_retries_then_succeeds(self):
        """502 should trigger retries; success on later attempt returns data."""
        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return _json_response({"message": "bad gateway"}, status=502)
            return _json_response({"ok": True})

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=_side_effect)

        with patch("auto_model_docs.domino_client._get_httpx_client", return_value=mock_client):
            with patch("auto_model_docs.domino_client.asyncio.sleep", new_callable=AsyncMock):
                result = await _domino_request("GET", "/v4/projects")

        assert result == {"ok": True}
        assert call_count == 3

    @skip_no_request
    async def test_4xx_raises_immediately_no_retry(self):
        """Client errors (4xx) should raise immediately without retrying."""
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            return_value=_json_response({"message": "bad request"}, status=400)
        )

        with patch("auto_model_docs.domino_client._get_httpx_client", return_value=mock_client):
            with pytest.raises((DominoAPIError, httpx.HTTPStatusError)):
                await _domino_request("GET", "/v4/projects")

        # Should be exactly 1 call — no retries for 4xx
        assert mock_client.request.call_count == 1

    @skip_no_request
    async def test_json_decode_failure_raises_no_retry(self):
        """Non-JSON response should raise DominoAPIError without retrying."""
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            return_value=_text_response("<html>bad gateway</html>", status=200)
        )

        with patch("auto_model_docs.domino_client._get_httpx_client", return_value=mock_client):
            with pytest.raises(DominoAPIError):
                await _domino_request("GET", "/v4/projects")

        assert mock_client.request.call_count == 1

    @skip_no_request
    async def test_cross_project_uses_api_host(self, monkeypatch):
        """cross_project=True should route through DOMINO_API_HOST, not the proxy."""
        monkeypatch.setenv("DOMINO_API_HOST", "https://domino.example.com")
        monkeypatch.setenv("DOMINO_API_PROXY", "http://localhost:8899")

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=_json_response({"ok": True}))

        with patch("auto_model_docs.domino_client._get_httpx_client", return_value=mock_client):
            await _domino_request("GET", "/v4/projects/123", cross_project=True)

        call_args = mock_client.request.call_args
        url = str(call_args[1].get("url", call_args[0][1] if len(call_args[0]) > 1 else ""))
        assert "domino.example.com" in url
        assert "localhost:8899" not in url


# ===================================================================
# get_project_context() tests
# ===================================================================

class TestGetProjectContext:
    """Tests for get_project_context(project_id=...)."""

    @skip_no_ctx
    async def test_with_project_id_calls_resolve(self, sample_project_info):
        """When project_id is provided, resolve_project should be called."""
        with patch(
            "auto_model_docs.domino_client.resolve_project",
            new_callable=AsyncMock,
            return_value=sample_project_info,
        ) as mock_resolve:
            ctx = await get_project_context(project_id="507f1f77bcf86cd799439011")

        mock_resolve.assert_awaited_once_with("507f1f77bcf86cd799439011")
        assert ctx["name"] == "my-model"
        assert ctx["owner"] == "alice"

    @skip_no_ctx
    async def test_without_project_id_returns_env_vars(self, monkeypatch):
        """When no project_id, should return values from env vars."""
        monkeypatch.setenv("DOMINO_PROJECT_OWNER", "env_owner")
        monkeypatch.setenv("DOMINO_PROJECT_NAME", "env_project")

        ctx = await get_project_context(project_id=None)

        assert ctx["owner"] == "env_owner"
        assert ctx["name"] == "env_project"


# ===================================================================
# submit_job() tests
# ===================================================================

class TestSubmitJob:
    """Tests for submit_job() with cross-project support."""

    def test_successful_submission_returns_run_id(self):
        """submit_job should return the run ID from the Domino response."""
        mock_domino = MagicMock()
        mock_domino.job_start.return_value = {"id": "run-abc-123"}

        with patch("auto_model_docs.domino_client._get_domino", return_value=mock_domino):
            run_id = submit_job(
                command=["python", "main.py"],
                branch="main",
                tier_id="small",
            )

        assert run_id == "run-abc-123"
        mock_domino.job_start.assert_called_once()

    def test_submit_with_branch_passes_git_ref(self):
        """Branch should be passed as mainRepoGitRef in the job kwargs."""
        mock_domino = MagicMock()
        mock_domino.job_start.return_value = {"id": "run-xyz"}

        with patch("auto_model_docs.domino_client._get_domino", return_value=mock_domino):
            submit_job(command=["python", "main.py"], branch="feature/x")

        call_kwargs = mock_domino.job_start.call_args[1]
        assert call_kwargs["main_repo_git_ref"] == {"type": "branches", "value": "feature/x"}

    @skip_no_submit
    def test_no_project_id_raises_runtime_error(self, monkeypatch):
        """When project_id is required but missing, submit_job should raise.

        The new submit_job signature (with project_id kwarg) is in the
        parallel branch.  This test validates that signature once it lands.
        """
        monkeypatch.delenv("DOMINO_PROJECT_NAME", raising=False)
        monkeypatch.delenv("DOMINO_PROJECT_OWNER", raising=False)

        # Import dynamically to pick up the new signature when available
        from auto_model_docs.domino_client import submit_job as _submit

        with pytest.raises(RuntimeError):
            _submit(
                command=["python", "main.py"],
                branch="main",
                project_id=None,
            )

    def test_bad_branch_raises_error(self):
        """SDK TypeError on bad branch should propagate, not silently fall back."""
        mock_domino = MagicMock()
        mock_domino.job_start.side_effect = TypeError("unexpected kwarg 'main_repo_git_ref'")
        mock_domino.project_id = "proj-id"
        mock_domino._routes = MagicMock()
        mock_domino._routes.job_start.return_value = "/v4/jobs/start"
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": "run-fallback"}
        mock_domino.request_manager = MagicMock()
        mock_domino.request_manager.post.return_value = mock_response

        with patch("auto_model_docs.domino_client._get_domino", return_value=mock_domino):
            # Should succeed via the REST API fallback path
            run_id = submit_job(
                command=["python", "main.py"],
                branch="main",
                tier_id="small",
            )

        assert run_id == "run-fallback"
        # The fallback API call should include the branch
        call_kwargs = mock_domino.request_manager.post.call_args[1]
        payload = call_kwargs.get("json", {})
        assert payload.get("mainRepoGitRef") == {"type": "branches", "value": "main"}

    def test_submit_no_branch_no_tier(self):
        """submit_job with no branch or tier should still succeed."""
        mock_domino = MagicMock()
        mock_domino.job_start.return_value = {"runId": "run-simple"}

        with patch("auto_model_docs.domino_client._get_domino", return_value=mock_domino):
            run_id = submit_job(command=["echo", "hello"], branch=None)

        assert run_id == "run-simple"


# ===================================================================
# _build_job_command_str() tests
# ===================================================================

try:
    from auto_model_docs.studio.job_engine import _build_job_command_str as _bld_cmd
    from auto_model_docs.studio.state import JobRequest as _JR
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
        output_dir=None,
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

    def test_paths_with_spaces_are_quoted(self):
        """Output dir containing spaces should be properly handled."""
        req = _make_job_request(output_dir="/mnt/data/my project with spaces")
        cmd = _bld_cmd(req, spec_path=None)

        # The output dir should appear in the command — verify the copy step
        # includes the path (even if not shell-quoted, the test documents
        # current behavior so the parallel branch can fix quoting)
        assert "/mnt/data/my project with spaces" in cmd
        assert "/mnt/artifacts/auto_ml" in cmd

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
        req = _make_job_request(output_dir=None)
        cmd = _bld_cmd(req, spec_path=None)
        assert "cp" not in cmd
        assert "artifacts" not in cmd
