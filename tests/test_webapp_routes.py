"""Tests for web_app.py route handlers.

These tests require fasthtml/starlette which are only available in the
Domino environment. They will be skipped locally.
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg_dir = os.path.join(_repo_root, "auto_model_docs")
for p in (_repo_root, _pkg_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

# ---------------------------------------------------------------------------
# Try to import the app
# ---------------------------------------------------------------------------
_APP_AVAILABLE = False
try:
    with patch.dict(os.environ, {
        "DOMINO_API_HOST": "https://domino.example.com",
        "DOMINO_API_PROXY": "http://localhost:8899",
        "DOMINO_USER_API_KEY": "test-key",
        "DOMINO_PROJECT_OWNER": "test_owner",
        "DOMINO_PROJECT_NAME": "test_project",
        "DOMINO_PROJECT_ID": "proj-test-123",
        "DOMINO_STARTING_USERNAME": "test_user",
    }):
        from auto_model_docs.web_app import app
        _APP_AVAILABLE = True
except Exception:
    app = None  # type: ignore[assignment]

skip_no_app = pytest.mark.skipif(not _APP_AVAILABLE, reason="web_app requires fasthtml (Domino env)")


@pytest.fixture
def client():
    if app is None:
        pytest.skip("web_app could not be imported")
    from starlette.testclient import TestClient
    return TestClient(app)


# ===================================================================
# GET / (index)
# ===================================================================

class TestIndex:
    @skip_no_app
    def test_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Auto Model Docs" in resp.text


# ===================================================================
# POST /run
# ===================================================================

class TestRunRoute:
    @skip_no_app
    def test_app_mode_starts_job(self, client):
        with patch("auto_model_docs.web_app._run_generation") as mock_run:
            mock_run.return_value = None
            resp = client.post("/run", data={
                "spec_path": "/path/to/spec.yaml",
                "provider": "anthropic",
                "code_root": "/mnt/code",
                "output_dir": "/mnt/code/output",
            })
        # Should return status panel HTML
        assert resp.status_code == 200

    @skip_no_app
    def test_domino_mode_submits_job(self, client):
        with (
            patch("auto_model_docs.web_app._submit_domino_job") as mock_submit,
            patch("auto_model_docs.web_app._render_status_panel") as mock_render,
        ):
            mock_submit.return_value = "job-123"
            mock_render.return_value = "<div>status</div>"
            resp = client.post("/run", data={
                "spec_path": "/path/to/spec.yaml",
                "provider": "openai",
                "code_root": "/mnt/code",
            })
        assert resp.status_code == 200


# ===================================================================
# POST /stop
# ===================================================================

class TestStopRoute:
    @skip_no_app
    def test_stop_returns_200(self, client):
        resp = client.post("/stop")
        assert resp.status_code == 200


# ===================================================================
# POST /clear-terminal
# ===================================================================

class TestClearTerminal:
    @skip_no_app
    def test_returns_200(self, client):
        resp = client.post("/clear-terminal")
        assert resp.status_code == 200


# ===================================================================
# GET /api/branches
# ===================================================================

class TestApiBranches:
    @skip_no_app
    def test_returns_select(self, client):
        with patch("auto_model_docs.web_app.domino_client.list_branches", return_value=[
            {"name": "main"}, {"name": "dev"},
        ]):
            resp = client.get("/api/branches")
        assert resp.status_code == 200
        assert "main" in resp.text
        assert "dev" in resp.text


# ===================================================================
# GET /api/hardware-tiers
# ===================================================================

class TestApiHardwareTiers:
    @skip_no_app
    def test_returns_select(self, client):
        with patch("auto_model_docs.web_app.domino_client.list_hardware_tiers", return_value=[
            {"id": "small", "name": "Small", "isDefault": True},
            {"id": "large", "name": "Large GPU", "isDefault": False},
        ]):
            resp = client.get("/api/hardware-tiers")
        assert resp.status_code == 200
        assert "Small" in resp.text


# ===================================================================
# GET /api/detect-language
# ===================================================================

class TestApiDetectLanguage:
    @skip_no_app
    def test_returns_json(self, client):
        with patch("auto_model_docs.web_app._get_default_code_root", return_value=MagicMock()):
            with patch("autodoc.core.models.detect_language") as mock_detect:
                mock_profile = MagicMock()
                mock_profile.name = "python"
                mock_profile.display_name = "Python"
                mock_detect.return_value = (mock_profile, 42)
                resp = client.get("/api/detect-language")
        assert resp.status_code == 200
        data = resp.json()
        assert data["language"] == "python"
        assert data["file_count"] == 42

    @skip_no_app
    def test_no_files_found(self, client):
        with patch("auto_model_docs.web_app._get_default_code_root", return_value=MagicMock()):
            with patch("autodoc.core.models.detect_language", return_value=(None, 0)):
                resp = client.get("/api/detect-language")
        assert resp.status_code == 200
        data = resp.json()
        assert data["language"] is None


# ===================================================================
# POST /stop-domino
# ===================================================================

class TestStopDomino:
    @skip_no_app
    def test_stops_and_returns(self, client):
        with (
            patch("auto_model_docs.web_app.domino_client.stop_job"),
            patch("auto_model_docs.web_app.domino_job_store.update_job"),
        ):
            resp = client.post("/stop-domino", data={"run_id": "run-123", "job_id": "job-456"})
        assert resp.status_code == 200


# ===================================================================
# GET /domino-status
# ===================================================================

class TestDominoStatus:
    @skip_no_app
    def test_returns_html(self, client):
        with patch("auto_model_docs.web_app.domino_job_store.get_user_jobs", return_value=[]):
            resp = client.get("/domino-status")
        assert resp.status_code == 200


# ===================================================================
# GET /job-history
# ===================================================================

class TestJobHistory:
    @skip_no_app
    def test_returns_html(self, client):
        with patch("auto_model_docs.web_app.domino_job_store.get_user_jobs", return_value=[]):
            resp = client.get("/job-history")
        assert resp.status_code == 200

    @skip_no_app
    def test_with_jobs(self, client):
        jobs = [{
            "id": "j1", "username": "test_user", "branch": "main",
            "hardware_tier": "small", "status": "succeeded",
            "domino_status": "Succeeded", "domino_run_id": "run-1",
            "job_url": None, "spec_path": "/spec.yaml",
            "command": "python main.py", "submitted_at": "2026-03-24T12:00:00Z",
            "completed_at": "2026-03-24T12:05:00Z", "project_id": None,
        }]
        with patch("auto_model_docs.web_app.domino_job_store.get_user_jobs", return_value=jobs):
            resp = client.get("/job-history")
        assert resp.status_code == 200
        assert "succeeded" in resp.text.lower() or "history" in resp.text.lower()


# ===================================================================
# POST /clear-job-history
# ===================================================================

class TestClearJobHistory:
    @skip_no_app
    def test_clears_and_returns(self, client):
        with (
            patch("auto_model_docs.web_app.domino_job_store.clear_terminal_jobs"),
            patch("auto_model_docs.web_app.domino_job_store.get_user_jobs", return_value=[]),
        ):
            resp = client.post("/clear-job-history")
        assert resp.status_code == 200


# ===================================================================
# POST /cancel-queued-jobs
# ===================================================================

class TestCancelQueuedJobs:
    @skip_no_app
    def test_cancels_and_returns(self, client):
        with (
            patch("auto_model_docs.web_app.domino_job_store.get_user_jobs", return_value=[]),
        ):
            resp = client.post("/cancel-queued-jobs")
        assert resp.status_code == 200


# ===================================================================
# POST /save-spec
# ===================================================================

class TestSaveSpec:
    @skip_no_app
    def test_saves_and_returns_path(self, client):
        from pathlib import Path
        with patch("auto_model_docs.web_app.spec_store.save_spec", return_value=Path("/mnt/data/test/specs/abc_spec.yaml")):
            resp = client.post("/save-spec", data={
                "spec_filename": "spec.yaml",
                "spec_content": "title: Test",
            })
        assert resp.status_code == 200
        assert "spec.yaml" in resp.text or "/mnt/data" in resp.text


# ===================================================================
# GET /spec-list
# ===================================================================

class TestSpecList:
    @skip_no_app
    def test_empty(self, client):
        with patch("auto_model_docs.web_app.spec_store.list_specs", return_value=[]):
            resp = client.get("/spec-list")
        assert resp.status_code == 200

    @skip_no_app
    def test_with_specs(self, client):
        with patch("auto_model_docs.web_app.spec_store.list_specs", return_value=[
            "abc_spec.yaml", "def_config.yml",
        ]):
            resp = client.get("/spec-list")
        assert resp.status_code == 200


# ===================================================================
# POST /delete-spec
# ===================================================================

class TestDeleteSpec:
    @skip_no_app
    def test_deletes_and_returns_list(self, client):
        with (
            patch("auto_model_docs.web_app.spec_store.delete_spec"),
            patch("auto_model_docs.web_app.spec_store.list_specs", return_value=[]),
        ):
            resp = client.post("/delete-spec", data={"filename": "old_spec.yaml"})
        assert resp.status_code == 200
