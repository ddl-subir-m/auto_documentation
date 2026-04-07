"""Tests for domino_client.py — current REST-based API (no SDK dependency).

Covers: list_hardware_tiers, get_project_default_tier,
get_job_status, stop_job, set_ui_host, build_job_url,
resolve_project, submit_job with current signatures.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg_dir = os.path.join(_repo_root, "auto_model_docs")
for p in (_repo_root, _pkg_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

import domino_client as dc


@pytest.fixture(autouse=True)
def _setup_env(monkeypatch):
    monkeypatch.setenv("DOMINO_API_HOST", "https://domino.example.com")
    monkeypatch.setenv("DOMINO_USER_API_KEY", "test-api-key")
    monkeypatch.setenv("DOMINO_PROJECT_ID", "proj-123")
    monkeypatch.setenv("DOMINO_PROJECT_OWNER", "test_owner")
    monkeypatch.setenv("DOMINO_PROJECT_NAME", "test_project")
    # Clear caches
    dc._project_cache.clear()
    dc._ui_host = None  # type: ignore[assignment]
    yield


# ---------------------------------------------------------------------------
# Host resolution — _resolve_api_host is re-exported from domino_auth
# ---------------------------------------------------------------------------

class TestHostResolution:
    def test_api_host(self):
        assert dc._resolve_api_host() == "https://domino.example.com"

    def test_api_host_strips_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("DOMINO_API_HOST", "https://domino.example.com/")
        assert dc._resolve_api_host() == "https://domino.example.com"


# ---------------------------------------------------------------------------
# list_hardware_tiers
# ---------------------------------------------------------------------------

class TestListHardwareTiers:
    @patch.object(dc, "_domino_request")
    def test_parses_tiers(self, mock_req):
        mock_req.return_value = [
            {"hardwareTier": {"id": "small", "name": "Small", "hwtFlags": {"isDefault": True}}},
            {"hardwareTier": {"id": "large", "name": "Large GPU", "hwtFlags": {"isDefault": False}}},
        ]
        tiers = dc.list_hardware_tiers(project_id="proj-123")
        assert len(tiers) == 2
        assert tiers[0] == {"id": "small", "name": "Small", "isDefault": True}
        assert tiers[1] == {"id": "large", "name": "Large GPU", "isDefault": False}

    @patch.object(dc, "_domino_request")
    def test_nested_data_format(self, mock_req):
        mock_req.return_value = {"hardwareTiers": [
            {"id": "medium", "name": "Medium"},
        ]}
        tiers = dc.list_hardware_tiers(project_id="proj-123")
        assert len(tiers) == 1
        assert tiers[0]["id"] == "medium"

    @patch.object(dc, "_domino_request")
    def test_api_error_returns_empty(self, mock_req):
        mock_req.side_effect = RuntimeError("API down")
        assert dc.list_hardware_tiers(project_id="proj-123") == []

    def test_no_project_id_returns_empty(self):
        assert dc.list_hardware_tiers(project_id=None) == []

    @patch.object(dc, "_domino_request")
    def test_cross_project(self, mock_req):
        mock_req.return_value = []
        dc.list_hardware_tiers(project_id="other-proj")
        # Verify it called with the project ID in the path
        call_path = mock_req.call_args.args[1]
        assert "other-proj" in call_path


# ---------------------------------------------------------------------------
# get_project_default_tier
# ---------------------------------------------------------------------------

class TestGetProjectDefaultTier:
    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AUTODOC_DEFAULT_HARDWARE_TIER", "gpu-xl")
        assert dc.get_project_default_tier() == "gpu-xl"

    def test_domino_env(self, monkeypatch):
        monkeypatch.delenv("AUTODOC_DEFAULT_HARDWARE_TIER", raising=False)
        monkeypatch.setenv("DOMINO_HARDWARE_TIER_ID", "small-k8s")
        assert dc.get_project_default_tier() == "small-k8s"

    def test_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("AUTODOC_DEFAULT_HARDWARE_TIER", raising=False)
        monkeypatch.delenv("DOMINO_HARDWARE_TIER_ID", raising=False)
        assert dc.get_project_default_tier() is None


# ---------------------------------------------------------------------------
# get_job_status
# ---------------------------------------------------------------------------

class TestGetJobStatus:
    @patch.object(dc, "_domino_request")
    def test_succeeded(self, mock_req):
        mock_req.return_value = {"statuses": {"executionStatus": "Succeeded"}}
        result = dc.get_job_status("run-1")
        assert result["local_status"] == "succeeded"
        assert result["domino_status"] == "Succeeded"

    @patch.object(dc, "_domino_request")
    def test_failed(self, mock_req):
        mock_req.return_value = {"statuses": {"executionStatus": "Failed"}}
        result = dc.get_job_status("run-1")
        assert result["local_status"] == "failed"

    @patch.object(dc, "_domino_request")
    def test_running(self, mock_req):
        mock_req.return_value = {"statuses": {"executionStatus": "Executing"}}
        result = dc.get_job_status("run-1")
        assert result["local_status"] == "running"

    @patch.object(dc, "_domino_request")
    def test_cancelled(self, mock_req):
        mock_req.return_value = {"statuses": {"executionStatus": "Stopped"}}
        result = dc.get_job_status("run-1")
        assert result["local_status"] == "cancelled"

    @patch.object(dc, "_domino_request")
    def test_pending(self, mock_req):
        mock_req.return_value = {"statuses": {"executionStatus": "Queued"}}
        result = dc.get_job_status("run-1")
        # "queued" maps to _PENDING_STATUSES → "pending"
        assert result["local_status"] == "pending"

    @patch.object(dc, "_domino_request")
    def test_api_error_returns_running(self, mock_req):
        mock_req.side_effect = RuntimeError("timeout")
        result = dc.get_job_status("run-1")
        assert result["local_status"] == "running"
        assert result["domino_status"] == "unknown"

    @patch.object(dc, "_domino_request")
    def test_fallback_status_field(self, mock_req):
        mock_req.return_value = {"status": "Completed"}
        result = dc.get_job_status("run-1")
        assert result["local_status"] == "succeeded"


# ---------------------------------------------------------------------------
# stop_job
# ---------------------------------------------------------------------------

class TestStopJob:
    @patch.object(dc, "_domino_request")
    def test_calls_api(self, mock_req):
        dc.stop_job("run-1", project_id="proj-123")
        mock_req.assert_called_once_with(
            "POST", "/v4/jobs/stop",
            json={"jobId": "run-1", "commitResults": True, "projectId": "proj-123"},
        )

    def test_raises_without_project_id(self):
        with pytest.raises(ValueError, match="project_id is required"):
            dc.stop_job("run-1")

    @patch.object(dc, "_domino_request")
    def test_api_error_is_swallowed(self, mock_req):
        mock_req.side_effect = RuntimeError("already stopped")
        dc.stop_job("run-1", project_id="proj-123")  # should not raise


# ---------------------------------------------------------------------------
# set_ui_host / build_job_url
# ---------------------------------------------------------------------------

class TestSetUiHost:
    def test_basic(self):
        dc.set_ui_host("example.domino.tech")
        assert dc._ui_host == "https://example.domino.tech"

    def test_strips_apps_prefix(self):
        dc.set_ui_host("apps.domino.example.com")
        assert dc._ui_host == "https://domino.example.com"

    def test_with_scheme(self):
        dc.set_ui_host("https://custom.host:8443")
        assert dc._ui_host == "https://custom.host:8443"

    def test_caches_first_value(self):
        dc.set_ui_host("first.example.com")
        dc.set_ui_host("second.example.com")
        assert "first" in dc._ui_host

    def test_empty_is_noop(self):
        dc.set_ui_host("")
        assert dc._ui_host is None


class TestBuildJobUrl:
    def test_builds_url(self):
        dc.set_ui_host("domino.example.com")
        # build_job_url uses get_project_context which needs resolve_project
        with patch.object(dc, "get_project_context", return_value=("proj-123", "test_project", "test_owner")):
            url = dc.build_job_url("run-123", project_id="proj-123")
        assert url == "https://domino.example.com/jobs/test_owner/test_project/run-123/logs?status=all"

    def test_none_without_ui_host(self):
        assert dc.build_job_url("run-123") is None

    @patch.object(dc, "resolve_project")
    def test_with_cross_project(self, mock_resolve):
        dc.set_ui_host("domino.example.com")
        mock_resolve.return_value = dc.ProjectInfo(
            id="other-proj", name="other-model", owner_username="bob",
        )
        url = dc.build_job_url("run-456", project_id="other-proj")
        assert "/bob/other-model/" in url


# ---------------------------------------------------------------------------
# resolve_project
# ---------------------------------------------------------------------------

class TestResolveProject:
    @patch.object(dc, "_domino_request")
    def test_caches_result(self, mock_req):
        mock_req.return_value = {"name": "my-model", "ownerUsername": "alice"}
        info1 = dc.resolve_project("proj-abc")
        info2 = dc.resolve_project("proj-abc")
        assert info1 is info2
        assert mock_req.call_count == 1  # cached

    @patch.object(dc, "_domino_request")
    def test_returns_project_info(self, mock_req):
        mock_req.return_value = {"name": "my-model", "ownerUsername": "alice"}
        info = dc.resolve_project("proj-abc")
        assert info.name == "my-model"
        assert info.owner_username == "alice"

    @patch.object(dc, "_domino_request")
    def test_returns_none_on_missing_fields(self, mock_req):
        mock_req.return_value = {"name": "my-model"}  # no owner
        info = dc.resolve_project("proj-missing")
        assert info is None

    @patch.object(dc, "_domino_request")
    def test_returns_none_on_api_error(self, mock_req):
        mock_req.side_effect = Exception("network error")
        info = dc.resolve_project("proj-err")
        assert info is None


# ---------------------------------------------------------------------------
# submit_job
# ---------------------------------------------------------------------------

class TestSubmitJob:
    @patch.object(dc, "get_project_context", return_value=("proj-123", "test_project", "test_owner"))
    @patch.object(dc, "_domino_request")
    def test_returns_run_id(self, mock_req, _mock_ctx):
        mock_req.return_value = {"id": "run-abc"}
        run_id = dc.submit_job("python main.py", "main", project_id="proj-123")
        assert run_id == "run-abc"

    @patch.object(dc, "get_project_context", return_value=("proj-123", "test_project", "test_owner"))
    @patch.object(dc, "_domino_request")
    def test_includes_branch(self, mock_req, _mock_ctx):
        mock_req.return_value = {"id": "run-1"}
        dc.submit_job("python main.py", "feature/x", project_id="proj-123")
        payload = mock_req.call_args.kwargs["json"]
        assert payload["mainRepoGitRef"] == {"type": "branches", "value": "feature/x"}

    @patch.object(dc, "get_project_context", return_value=("proj-123", "test_project", "test_owner"))
    @patch.object(dc, "_domino_request")
    def test_no_branch(self, mock_req, _mock_ctx):
        mock_req.return_value = {"id": "run-1"}
        dc.submit_job("python main.py", None, project_id="proj-123")
        payload = mock_req.call_args.kwargs["json"]
        assert "mainRepoGitRef" not in payload

    @patch.object(dc, "get_project_context", return_value=("proj-123", "test_project", "test_owner"))
    @patch.object(dc, "_domino_request")
    def test_includes_tier(self, mock_req, _mock_ctx):
        mock_req.return_value = {"id": "run-1"}
        dc.submit_job("python main.py", None, tier_id="gpu-large", project_id="proj-123")
        payload = mock_req.call_args.kwargs["json"]
        assert payload["overrideHardwareTierId"] == "gpu-large"

    @patch.object(dc, "get_project_context", return_value=("proj-123", "test_project", "test_owner"))
    @patch.object(dc, "_domino_request")
    def test_raises_on_missing_run_id(self, mock_req, _mock_ctx):
        mock_req.return_value = {"unexpected": "response"}
        with pytest.raises(ValueError, match="unexpected response"):
            dc.submit_job("python main.py", None, project_id="proj-123")

    def test_raises_on_no_project(self):
        with pytest.raises(RuntimeError, match="No project ID"):
            dc.submit_job("python main.py", None, project_id=None)

    @patch.object(dc, "get_project_context", return_value=("proj-123", "test_project", "test_owner"))
    @patch.object(dc, "_domino_request")
    def test_retry_without_git_ref(self, mock_req, _mock_ctx):
        """If branch-pinned start fails, retries without mainRepoGitRef.

        Note: the payload dict is mutated in-place (pop) before the retry,
        so both call_args entries share the same dict reference.  We verify
        the retry happened (2 calls) and the final payload lacks the key.
        """
        call_count = 0
        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("bad ref")
            return {"id": "run-retry"}

        mock_req.side_effect = side_effect
        run_id = dc.submit_job("python main.py", "bad-branch", project_id="proj-123")
        assert run_id == "run-retry"
        assert mock_req.call_count == 2
        # Final payload (shared reference) should not have mainRepoGitRef
        final_payload = mock_req.call_args.kwargs["json"]
        assert "mainRepoGitRef" not in final_payload
