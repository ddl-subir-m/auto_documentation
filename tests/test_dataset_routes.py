"""Tests for dataset browsing and spec upload API routes."""

from __future__ import annotations

import io
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import the app with safe Domino env vars
# ---------------------------------------------------------------------------
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg_dir = os.path.join(_repo_root, "auto_model_docs")
for p in (_repo_root, _pkg_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

_HAS_DATASET_ROUTES = False
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

        for route in getattr(app, "routes", []):
            path = getattr(route, "path", "")
            if "api/datasets" in path:
                _HAS_DATASET_ROUTES = True
                break
except Exception:
    app = None  # type: ignore[assignment]

skip_no_routes = pytest.mark.skipif(
    not _HAS_DATASET_ROUTES,
    reason="Dataset routes not yet in web_app.py",
)


@pytest.fixture
def client():
    if app is None:
        pytest.skip("web_app could not be imported")
    from starlette.testclient import TestClient
    return TestClient(app)


# ===================================================================
# GET /api/datasets
# ===================================================================

class TestApiDatasets:
    @skip_no_routes
    def test_returns_dataset_list(self, client):
        datasets = [
            {"id": "ds-1", "name": "my-data", "description": "", "rwSnapshotId": "snap-1"},
            {"id": "ds-2", "name": "autodoc-specs", "description": "", "rwSnapshotId": "snap-2"},
        ]
        with patch("auto_model_docs.web_app.domino_datasets.list_datasets", return_value=datasets):
            resp = client.get("/api/datasets?projectId=proj-test-123")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["name"] == "my-data"

    @skip_no_routes
    def test_returns_empty_when_no_datasets(self, client):
        with patch("auto_model_docs.web_app.domino_datasets.list_datasets", return_value=[]):
            resp = client.get("/api/datasets?projectId=proj-test-123")
        assert resp.status_code == 200
        assert resp.json() == []

    @skip_no_routes
    def test_returns_500_on_api_error(self, client):
        with patch(
            "auto_model_docs.web_app.domino_datasets.list_datasets",
            side_effect=RuntimeError("API unreachable"),
        ):
            resp = client.get("/api/datasets?projectId=proj-test-123")
        assert resp.status_code == 500
        assert "error" in resp.json()

    @skip_no_routes
    def test_uses_project_id_from_query(self, client):
        with patch("auto_model_docs.web_app.domino_datasets.list_datasets", return_value=[]) as mock:
            client.get("/api/datasets?projectId=proj-custom")
        mock.assert_called_once_with("proj-custom")


# ===================================================================
# GET /api/dataset-files
# ===================================================================

class TestApiDatasetFiles:
    @skip_no_routes
    def test_returns_files(self, client):
        files = [
            {"fileName": "spec.yaml", "isDirectory": False, "sizeInBytes": 1024},
            {"fileName": "models", "isDirectory": True, "sizeInBytes": 0},
        ]
        with (
            patch("auto_model_docs.web_app.domino_datasets.list_files", return_value=files),
            patch("auto_model_docs.web_app.domino_datasets.get_rw_snapshot_id", return_value="snap-1"),
        ):
            resp = client.get("/api/dataset-files?datasetId=ds-1&projectId=proj-test-123")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    @skip_no_routes
    def test_missing_dataset_id(self, client):
        resp = client.get("/api/dataset-files")
        assert resp.status_code == 400
        assert "datasetId required" in resp.json()["error"]

    @skip_no_routes
    def test_uses_provided_snapshot_id(self, client):
        with patch("auto_model_docs.web_app.domino_datasets.list_files", return_value=[]) as mock:
            client.get("/api/dataset-files?datasetId=ds-1&snapshotId=snap-override&projectId=proj-test-123")
        call_args = mock.call_args
        assert call_args.args[0] == "snap-override"

    @skip_no_routes
    def test_passes_path_for_subdirectory(self, client):
        with (
            patch("auto_model_docs.web_app.domino_datasets.list_files", return_value=[]) as mock,
            patch("auto_model_docs.web_app.domino_datasets.get_rw_snapshot_id", return_value="snap-1"),
        ):
            client.get("/api/dataset-files?datasetId=ds-1&path=models/v2&projectId=proj-test-123")
        assert mock.call_args.args[1] == "models/v2"

    @skip_no_routes
    def test_no_snapshot_returns_400(self, client):
        with patch("auto_model_docs.web_app.domino_datasets.get_rw_snapshot_id", return_value=None):
            resp = client.get("/api/dataset-files?datasetId=ds-1&projectId=proj-test-123")
        assert resp.status_code == 400
        assert "snapshot" in resp.json()["error"].lower()


# ===================================================================
# POST /api/ensure-autodoc-specs
# ===================================================================

class TestApiEnsureAutodocSpecs:
    @skip_no_routes
    def test_creates_and_returns_dataset(self, client):
        ds_info = {"id": "ds-new", "name": "autodoc-specs", "rwSnapshotId": "snap-rw"}
        with patch("auto_model_docs.web_app.domino_datasets.ensure_dataset", return_value=ds_info):
            resp = client.post("/api/ensure-autodoc-specs?projectId=proj-test-123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "ds-new"
        assert data["name"] == "autodoc-specs"

    @skip_no_routes
    def test_resolves_snapshot_if_missing(self, client):
        ds_info = {"id": "ds-1", "name": "autodoc-specs", "rwSnapshotId": None}
        with (
            patch("auto_model_docs.web_app.domino_datasets.ensure_dataset", return_value=ds_info),
            patch("auto_model_docs.web_app.domino_datasets.get_rw_snapshot_id", return_value="snap-resolved") as mock_snap,
        ):
            resp = client.post("/api/ensure-autodoc-specs?projectId=proj-test-123")
        assert resp.json()["rwSnapshotId"] == "snap-resolved"
        mock_snap.assert_called_once()

    @skip_no_routes
    def test_returns_500_on_failure(self, client):
        with patch(
            "auto_model_docs.web_app.domino_datasets.ensure_dataset",
            side_effect=RuntimeError("permission denied"),
        ):
            resp = client.post("/api/ensure-autodoc-specs?projectId=proj-test-123")
        assert resp.status_code == 500


# ===================================================================
# POST /api/upload-spec-to-dataset
# ===================================================================

class TestApiUploadSpecToDataset:
    @skip_no_routes
    def test_successful_upload(self, client):
        with (
            patch("auto_model_docs.web_app.domino_datasets.upload_file") as mock_upload,
            patch("auto_model_docs.web_app.domino_datasets.build_spec_mount_path", return_value="/mnt/data/autodoc-specs/spec.yaml"),
        ):
            resp = client.post(
                "/api/upload-spec-to-dataset?projectId=proj-test-123",
                data={"datasetId": "ds-1", "datasetName": "autodoc-specs"},
                files={"file": ("spec.yaml", b"title: My Model", "application/x-yaml")},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["mountPath"] == "/mnt/data/autodoc-specs/spec.yaml"
        assert data["fileName"] == "spec.yaml"
        mock_upload.assert_called_once()

    @skip_no_routes
    def test_missing_dataset_id(self, client):
        resp = client.post(
            "/api/upload-spec-to-dataset",
            files={"file": ("spec.yaml", b"content", "application/x-yaml")},
        )
        assert resp.status_code == 400

    @skip_no_routes
    def test_missing_file(self, client):
        resp = client.post(
            "/api/upload-spec-to-dataset",
            data={"datasetId": "ds-1"},
        )
        assert resp.status_code == 400

    @skip_no_routes
    def test_upload_failure_returns_500(self, client):
        with patch(
            "auto_model_docs.web_app.domino_datasets.upload_file",
            side_effect=RuntimeError("upload failed"),
        ):
            resp = client.post(
                "/api/upload-spec-to-dataset?projectId=proj-test-123",
                data={"datasetId": "ds-1", "datasetName": "autodoc-specs"},
                files={"file": ("spec.yaml", b"content", "application/x-yaml")},
            )
        assert resp.status_code == 500
        assert "error" in resp.json()


# ===================================================================
# GET /api/download-template
# ===================================================================

class TestApiDownloadTemplate:
    @skip_no_routes
    def test_returns_yaml_file(self, client):
        resp = client.get("/api/download-template")
        assert resp.status_code == 200
        assert "yaml" in resp.headers.get("content-type", "").lower()
        # Should contain valid YAML content
        assert len(resp.content) > 0
