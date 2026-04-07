"""Tests for domino_datasets.py — Domino Datasets API client."""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# Ensure auto_model_docs is importable
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg_dir = os.path.join(_repo_root, "auto_model_docs")
for p in (_repo_root, _pkg_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

import httpx
from auth_context import set_request_auth_header
import domino_datasets as ds


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _setup_env(monkeypatch):
    """Set safe Domino env defaults and a valid auth token for every test."""
    monkeypatch.setenv("DOMINO_API_HOST", "https://domino.example.com")
    monkeypatch.setenv("DOMINO_API_PROXY", "http://localhost:8899")
    monkeypatch.setenv("DOMINO_PROJECT_ID", "proj-123")
    set_request_auth_header("Bearer test-jwt")
    yield
    set_request_auth_header(None)


def _mock_response(status_code=200, json_data=None, text=""):
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text or json.dumps(json_data or {})
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        http_error = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp,
        )
        resp.raise_for_status.side_effect = http_error
    return resp


# ---------------------------------------------------------------------------
# _resolve_project_id
# ---------------------------------------------------------------------------

class TestResolveProjectId:
    def test_uses_provided_id(self):
        assert ds._resolve_project_id("proj-999") == "proj-999"

    def test_raises_when_none(self):
        """No longer falls back to env — raises when no ID given."""
        with pytest.raises(RuntimeError, match="No project ID"):
            ds._resolve_project_id(None)

    def test_raises_when_empty(self):
        with pytest.raises(RuntimeError, match="No project ID"):
            ds._resolve_project_id("")



# ---------------------------------------------------------------------------
# list_datasets
# ---------------------------------------------------------------------------

class TestListDatasets:
    @patch.object(ds, "_api_request")
    def test_single_page(self, mock_req):
        """v2 API returns datasets with nested 'dataset' wrapper."""
        mock_req.return_value = _mock_response(json_data={
            "datasets": [
                {"dataset": {"datasetId": "ds-1", "datasetName": "my-data",
                             "readWriteSnapshotId": "snap-1"}},
                {"dataset": {"datasetId": "ds-2", "datasetName": "autodoc-specs",
                             "readWriteSnapshotId": "snap-2"}},
            ]
        })
        result = ds.list_datasets("proj-123")
        assert len(result) == 2
        assert result[0]["name"] == "my-data"
        assert result[0]["rwSnapshotId"] == "snap-1"

    @patch.object(ds, "_api_request")
    def test_pagination(self, mock_req):
        page1 = _mock_response(json_data={
            "datasets": [{"dataset": {"datasetId": f"ds-{i}", "datasetName": f"ds{i}"}}
                         for i in range(50)]
        })
        page2 = _mock_response(json_data={
            "datasets": [{"dataset": {"datasetId": "ds-50", "datasetName": "ds50"}}]
        })
        mock_req.side_effect = [page1, page2]
        result = ds.list_datasets("proj-123")
        assert len(result) == 51
        assert mock_req.call_count == 2

    @patch.object(ds, "_api_request")
    def test_no_minimum_permission(self, mock_req):
        """Must NOT send minimumPermission (it causes 500)."""
        mock_req.return_value = _mock_response(json_data={"datasets": []})
        ds.list_datasets("proj-123")
        params = mock_req.call_args.kwargs.get("params", {})
        assert "minimumPermission" not in params

    @patch.object(ds, "_api_request")
    def test_uses_v2_endpoint(self, mock_req):
        mock_req.return_value = _mock_response(json_data={"datasets": []})
        ds.list_datasets("proj-123")
        assert "/api/datasetrw/v2/datasets" in mock_req.call_args.args[1]

    @patch.object(ds, "_api_request")
    def test_snapshot_ids_fallback(self, mock_req):
        """snapshotIds array used when readWriteSnapshotId is absent."""
        mock_req.return_value = _mock_response(json_data={
            "datasets": [
                {"dataset": {"datasetId": "ds-1", "datasetName": "t", "snapshotIds": ["snap-a"]}},
                {"dataset": {"datasetId": "ds-2", "datasetName": "t2"}},
            ]
        })
        result = ds.list_datasets("proj-123")
        assert result[0]["rwSnapshotId"] == "snap-a"
        assert result[1]["rwSnapshotId"] is None

    @patch.object(ds, "_api_request")
    def test_empty_datasets(self, mock_req):
        mock_req.return_value = _mock_response(json_data={"datasets": []})
        result = ds.list_datasets("proj-123")
        assert result == []


# ---------------------------------------------------------------------------
# ensure_dataset
# ---------------------------------------------------------------------------

class TestEnsureDataset:
    @patch.object(ds, "_create_dataset")
    def test_create_succeeds(self, mock_create):
        mock_create.return_value = {"id": "ds-new", "name": "autodoc-specs", "rwSnapshotId": "snap"}
        result = ds.ensure_dataset("proj-123")
        assert result["id"] == "ds-new"
        mock_create.assert_called_once()

    @patch.object(ds, "list_datasets")
    @patch.object(ds, "_create_dataset")
    def test_create_fails_finds_existing(self, mock_create, mock_list):
        mock_create.side_effect = RuntimeError("already exists")
        mock_list.return_value = [
            {"id": "ds-existing", "name": "autodoc", "rwSnapshotId": "snap"},
        ]
        result = ds.ensure_dataset("proj-123")
        assert result["id"] == "ds-existing"

    @patch.object(ds, "list_datasets")
    @patch.object(ds, "_create_dataset")
    def test_create_fails_not_found_raises(self, mock_create, mock_list):
        mock_create.side_effect = RuntimeError("permission denied")
        mock_list.return_value = [{"id": "ds-other", "name": "other-dataset"}]
        with pytest.raises(RuntimeError, match="Failed to create or find"):
            ds.ensure_dataset("proj-123")

    @patch.object(ds, "_create_dataset")
    def test_custom_name(self, mock_create):
        mock_create.return_value = {"id": "ds-custom", "name": "my-specs"}
        ds.ensure_dataset("proj-123", name="my-specs", description="Custom")
        mock_create.assert_called_once_with("proj-123", "my-specs", "Custom")


# ---------------------------------------------------------------------------
# get_rw_snapshot_id
# ---------------------------------------------------------------------------

class TestGetRwSnapshotId:
    @patch.object(ds, "_api_request")
    def test_returns_active_snapshot(self, mock_req):
        mock_req.return_value = _mock_response(json_data={
            "snapshots": [
                {"id": "snap-old", "status": "Completed"},
                {"id": "snap-active", "status": "Active"},
            ]
        })
        assert ds.get_rw_snapshot_id("ds-1", project_id="proj-123") == "snap-active"

    @patch.object(ds, "_api_request")
    def test_fallback_to_first(self, mock_req):
        mock_req.return_value = _mock_response(json_data={
            "snapshots": [{"id": "snap-only", "status": "Completed"}]
        })
        assert ds.get_rw_snapshot_id("ds-1", project_id="proj-123") == "snap-only"

    @patch.object(ds, "_api_request")
    def test_empty_snapshots(self, mock_req):
        mock_req.return_value = _mock_response(json_data={"snapshots": []})
        assert ds.get_rw_snapshot_id("ds-1", project_id="proj-123") is None

    @patch.object(ds, "_api_request")
    def test_api_error_returns_none(self, mock_req):
        mock_req.side_effect = RuntimeError("network error")
        assert ds.get_rw_snapshot_id("ds-1", project_id="proj-123") is None


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------

class TestListFiles:
    @patch.object(ds, "_api_request")
    def test_filters_to_yaml_and_dirs(self, mock_req):
        mock_req.return_value = _mock_response(json_data={
            "rows": [
                {"name": {"fileName": "spec.yaml", "isDirectory": False}, "size": {"sizeInBytes": 1024}},
                {"name": {"fileName": "data.csv", "isDirectory": False}, "size": {"sizeInBytes": 5000}},
                {"name": {"fileName": "subdir", "isDirectory": True}, "size": {}},
                {"name": {"fileName": "config.yml", "isDirectory": False}, "size": {"sizeInBytes": 512}},
                {"name": {"fileName": "README.md", "isDirectory": False}, "size": {"sizeInBytes": 200}},
            ]
        })
        files = ds.list_files("snap-1", project_id="proj-123")
        names = [f["fileName"] for f in files]
        assert "spec.yaml" in names
        assert "config.yml" in names
        assert "subdir" in names
        assert "data.csv" not in names
        assert "README.md" not in names

    @patch.object(ds, "_api_request")
    def test_passes_path_param(self, mock_req):
        mock_req.return_value = _mock_response(json_data={"rows": []})
        ds.list_files("snap-1", path="models/v2", project_id="proj-123")
        call_kwargs = mock_req.call_args
        assert call_kwargs.kwargs["params"]["path"] == "models/v2"

    @patch.object(ds, "_api_request")
    def test_empty_directory(self, mock_req):
        mock_req.return_value = _mock_response(json_data={"rows": []})
        assert ds.list_files("snap-1", project_id="proj-123") == []

    @patch.object(ds, "_api_request")
    def test_case_insensitive_yaml(self, mock_req):
        mock_req.return_value = _mock_response(json_data={
            "rows": [
                {"name": {"fileName": "SPEC.YAML", "isDirectory": False}, "size": {}},
                {"name": {"fileName": "Config.YML", "isDirectory": False}, "size": {}},
            ]
        })
        files = ds.list_files("snap-1", project_id="proj-123")
        assert len(files) == 2


# ---------------------------------------------------------------------------
# upload_file
# ---------------------------------------------------------------------------

class TestUploadFile:
    @pytest.mark.asyncio
    @patch("httpx.AsyncClient")
    async def test_three_step_upload(self, mock_client_cls):
        """Upload uses async httpx with 3-step v4 chunked flow."""
        mock_client = MagicMock()
        # Step 1: start — returns upload key as plain string
        start_resp = MagicMock()
        start_resp.status_code = 200
        start_resp.json.return_value = "uk-abc123"
        start_resp.raise_for_status = MagicMock()
        # Steps 2 & 3: chunk + finalize
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.raise_for_status = MagicMock()

        mock_client.request = AsyncMock(side_effect=[start_resp, ok_resp, ok_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await ds.upload_file("ds-1", "my_spec.yaml", b"title: My Model", project_id="proj-123")
        assert mock_client.request.call_count == 3

        # Verify step 1: start
        start_call = mock_client.request.call_args_list[0]
        assert "file/start" in start_call.args[1]
        assert start_call.kwargs["json"]["filePaths"] == ["my_spec.yaml"]

        # Verify step 2: chunk uses resumable params
        chunk_call = mock_client.request.call_args_list[1]
        assert chunk_call.kwargs["params"]["key"] == "uk-abc123"

        # Verify step 3: finalize
        end_call = mock_client.request.call_args_list[2]
        assert "file/end/uk-abc123" in end_call.args[1]


# ---------------------------------------------------------------------------
# Mount path helpers
# ---------------------------------------------------------------------------

class TestMountPaths:
    def test_git_project_prefix(self):
        with patch("os.path.isdir", return_value=False):
            assert ds.get_dataset_mount_prefix() == "/mnt/data"

    def test_dfs_project_prefix(self):
        with patch("os.path.isdir", return_value=True):
            assert ds.get_dataset_mount_prefix() == "/domino/datasets/local"

    def test_build_dataset_mount_path_git(self):
        with patch("os.path.isdir", return_value=False):
            path = ds.build_dataset_mount_path("autodoc", "specs/my_spec.yaml")
            assert path == "/mnt/data/autodoc/specs/my_spec.yaml"

    def test_build_dataset_mount_path_dfs(self):
        with patch("os.path.isdir", return_value=True):
            path = ds.build_dataset_mount_path("autodoc", "specs/sub/spec.yaml")
            assert path == "/domino/datasets/local/autodoc/specs/sub/spec.yaml"

    def test_build_dataset_mount_path_strips_leading_slash(self):
        with patch("os.path.isdir", return_value=False):
            path = ds.build_dataset_mount_path("autodoc", "/specs/spec.yaml")
            assert path == "/mnt/data/autodoc/specs/spec.yaml"


# ---------------------------------------------------------------------------
# _api_request (integration-level)
# ---------------------------------------------------------------------------

class TestApiRequest:
    @patch("httpx.Client")
    def test_prefers_forwarded_jwt(self, mock_client_cls):
        """Forwarded JWT is preferred over API key for all calls."""
        mock_resp = _mock_response(json_data={"ok": True})
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        # Mock _get_auth_headers directly to avoid ContextVar cross-module issues
        with patch.object(ds, "_get_auth_headers", return_value={"Authorization": "Bearer test-jwt"}):
            ds._api_request("GET", "/api/test")
        headers = mock_client.request.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer test-jwt"

    @patch("httpx.Client")
    def test_falls_back_to_api_key(self, mock_client_cls, monkeypatch):
        """When no forwarded JWT, falls back to API key."""
        monkeypatch.setenv("DOMINO_USER_API_KEY", "test-api-key")
        set_request_auth_header(None)
        mock_resp = _mock_response(json_data={})
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        ds._api_request("GET", "/api/test")
        headers = mock_client.request.call_args.kwargs["headers"]
        assert headers.get("X-Domino-Api-Key") == "test-api-key"
        set_request_auth_header("Bearer test-jwt")  # restore

    def test_raises_without_any_auth(self, monkeypatch):
        monkeypatch.delenv("DOMINO_USER_API_KEY", raising=False)
        monkeypatch.delenv("DOMINO_API_KEY", raising=False)
        set_request_auth_header(None)
        with pytest.raises(RuntimeError, match="No Domino auth credentials"):
            ds._api_request("GET", "/api/test")
        set_request_auth_header("Bearer test-jwt")  # restore
