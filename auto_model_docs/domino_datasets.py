"""Domino Datasets API client using forwarded user identity.

All requests use the viewer's JWT captured by ``auth_context`` middleware,
ensuring dataset operations respect the visiting user's permissions — not
the app owner's.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

import httpx

from auth_context import get_user_auth_headers

logger = logging.getLogger(__name__)

AUTODOC_SPECS_DATASET = "autodoc-specs"
AUTODOC_SPECS_DESCRIPTION = (
    "Auto Model Docs spec files — auto-created by Auto Model Docs Studio"
)

_RETRYABLE_STATUS_CODES = (408, 502, 503, 504)
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_RETRIES = 2


# ---------------------------------------------------------------------------
# Host resolution (mirrors domino_client.py)
# ---------------------------------------------------------------------------

def _resolve_api_host() -> str:
    host = os.environ.get("DOMINO_API_PROXY") or os.environ.get("DOMINO_API_HOST") or ""
    return host.rstrip("/")


def _resolve_nucleus_host() -> str:
    host = os.environ.get("DOMINO_API_HOST") or ""
    return host.rstrip("/")


def _resolve_project_id(project_id: Optional[str] = None) -> str:
    pid = project_id or os.environ.get("DOMINO_PROJECT_ID", "")
    if not pid:
        raise RuntimeError("No project ID available")
    return pid


def _is_cross_project(project_id: str) -> bool:
    return project_id != os.environ.get("DOMINO_PROJECT_ID", "")


# ---------------------------------------------------------------------------
# Core HTTP helper
# ---------------------------------------------------------------------------

def _api_request(
    method: str,
    path: str,
    *,
    cross_project: bool = False,
    json: Any = None,
    params: Optional[dict[str, Any]] = None,
    files: Optional[dict[str, Any]] = None,
    data: Optional[dict[str, Any]] = None,
    timeout: float = _DEFAULT_TIMEOUT,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> httpx.Response:
    """Authenticated request using the forwarded user JWT."""
    base = _resolve_nucleus_host() if cross_project else _resolve_api_host()
    if not base:
        raise RuntimeError("No Domino API host configured")

    url = f"{base}{path}"
    logger.debug("Datasets API %s %s (cross_project=%s)", method, path, cross_project)
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        headers = get_user_auth_headers()
        # Only set Content-Type for JSON requests (not multipart)
        if json is not None and files is None:
            headers["Content-Type"] = "application/json"
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.request(
                    method, url,
                    json=json, params=params,
                    files=files, data=data,
                    headers=headers,
                )
                if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < max_retries:
                    backoff = 2 ** attempt
                    logger.warning(
                        "Datasets API %s %s → %s, retry in %ss (%s/%s)",
                        method, path, resp.status_code, backoff, attempt + 1, max_retries,
                    )
                    time.sleep(backoff)
                    continue
                resp.raise_for_status()
                return resp
        except httpx.HTTPStatusError:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            break

    raise RuntimeError(f"Datasets API {method} {path} failed: {last_exc}")


# ---------------------------------------------------------------------------
# List datasets (writable only)
# ---------------------------------------------------------------------------

def list_datasets(project_id: Optional[str] = None) -> list[dict[str, Any]]:
    """List writable datasets for a project (minimumPermission=DatasetRwEditor)."""
    pid = _resolve_project_id(project_id)
    cross = _is_cross_project(pid)
    logger.info("Listing writable datasets for project %s (cross=%s)", pid, cross)

    datasets: list[dict[str, Any]] = []
    offset = 0
    page_size = 50

    while True:
        try:
            resp = _api_request(
                "GET", "/api/datasetrw/v2/datasets",
                cross_project=cross,
                params={
                    "projectIdsToInclude": pid,
                    "minimumPermission": "DatasetRwEditor",
                    "offset": offset,
                    "limit": page_size,
                },
            )
            data = resp.json()
            items = data.get("items", [])
            if not items:
                break
            for item in items:
                ds = item.get("dataset", item)
                datasets.append({
                    "id": ds.get("datasetId") or ds.get("id", ""),
                    "name": ds.get("datasetName") or ds.get("name", ""),
                    "description": ds.get("description", ""),
                    "rwSnapshotId": ds.get("readWriteSnapshotId"),
                })
            if len(items) < page_size:
                break
            offset += page_size
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 500):
                logger.warning(
                    "v2 datasets API returned %s, falling back to v1",
                    exc.response.status_code,
                )
                return _list_datasets_v1(pid, cross)
            raise

    logger.info("Found %d writable datasets for project %s", len(datasets), pid)
    return datasets


def _list_datasets_v1(
    project_id: str, cross_project: bool = False,
) -> list[dict[str, Any]]:
    """Fallback: list datasets via v1 API (no minimumPermission filter)."""
    datasets: list[dict[str, Any]] = []
    offset = 0
    page_size = 50

    while True:
        resp = _api_request(
            "GET", "/api/datasetrw/v1/datasets",
            cross_project=cross_project,
            params={"projectId": project_id, "offset": offset, "limit": page_size},
        )
        data = resp.json()
        items = data.get("items", [])
        if not items:
            break
        for ds in items:
            datasets.append({
                "id": ds.get("datasetId") or ds.get("id", ""),
                "name": ds.get("datasetName") or ds.get("name", ""),
                "description": ds.get("description", ""),
                "rwSnapshotId": ds.get("readWriteSnapshotId"),
            })
        if len(items) < page_size:
            break
        offset += page_size

    return datasets


# ---------------------------------------------------------------------------
# Create / ensure dataset
# ---------------------------------------------------------------------------

def _create_dataset(
    project_id: str, name: str, description: str,
) -> dict[str, Any]:
    cross = _is_cross_project(project_id)
    payloads = [
        {"name": name, "projectId": project_id, "description": description},
        {"name": name, "projectId": project_id},
        {"datasetName": name, "projectId": project_id, "description": description},
    ]

    last_error = None
    for payload in payloads:
        try:
            resp = _api_request(
                "POST", "/api/datasetrw/v1/datasets",
                cross_project=cross, json=payload,
            )
            data = resp.json()
            return {
                "id": data.get("datasetId") or data.get("id", ""),
                "name": data.get("datasetName") or data.get("name", name),
                "rwSnapshotId": data.get("readWriteSnapshotId"),
            }
        except httpx.HTTPStatusError as exc:
            last_error = exc.response.text if exc.response else str(exc)
            if exc.response is not None and "marked for deletion" in exc.response.text:
                raise RuntimeError(
                    f"Dataset '{name}' is marked for deletion. "
                    "A Domino admin must complete the deletion."
                )
        except Exception as exc:
            last_error = str(exc)

    raise RuntimeError(f"Failed to create dataset '{name}': {last_error}")


def ensure_dataset(
    project_id: Optional[str] = None,
    name: str = AUTODOC_SPECS_DATASET,
    description: str = AUTODOC_SPECS_DESCRIPTION,
) -> dict[str, Any]:
    """Find or create the named dataset.  Create-first pattern."""
    pid = _resolve_project_id(project_id)

    # Try create first (single fast call)
    try:
        created = _create_dataset(pid, name, description)
        logger.info("Created dataset '%s' in project %s", name, pid)
        return created
    except Exception:
        logger.debug("Create failed for '%s', looking up existing", name, exc_info=True)

    # Find existing
    datasets = list_datasets(pid)
    for ds in datasets:
        if ds["name"] == name:
            logger.info("Found existing dataset '%s' (id=%s)", name, ds["id"])
            return ds

    raise RuntimeError(f"Failed to create or find dataset '{name}' in project {pid}")


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

def get_rw_snapshot_id(
    dataset_id: str, project_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve the read-write (active) snapshot ID for a dataset."""
    pid = _resolve_project_id(project_id)
    cross = _is_cross_project(pid)

    try:
        resp = _api_request(
            "GET", f"/api/datasetrw/v1/datasets/{dataset_id}/snapshots",
            cross_project=cross, params={"limit": 5},
        )
        data = resp.json()
        for s in data.get("snapshots", []):
            if s.get("status", "").lower() == "active":
                return s.get("id")
        # Fallback: first snapshot
        snapshots = data.get("snapshots", [])
        if snapshots:
            return snapshots[0].get("id")
    except Exception:
        logger.warning("Failed to get snapshots for dataset %s", dataset_id, exc_info=True)
    return None


# ---------------------------------------------------------------------------
# File browsing
# ---------------------------------------------------------------------------

def list_files(
    snapshot_id: str,
    path: str = "",
    project_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """List files in a dataset snapshot, returning only directories and yaml files."""
    pid = _resolve_project_id(project_id)
    cross = _is_cross_project(pid)
    logger.debug("Browsing files in snapshot %s, path='%s'", snapshot_id, path)

    params: dict[str, str] = {}
    if path:
        params["path"] = path

    resp = _api_request(
        "GET", f"/v4/datasetrw/files/{snapshot_id}",
        cross_project=cross, params=params,
    )
    data = resp.json()
    rows = data.get("rows", [])

    files: list[dict[str, Any]] = []
    for row in rows:
        name_info = row.get("name", {})
        size_info = row.get("size", {})
        filename = name_info.get("fileName") or name_info.get("label", "")
        is_dir = name_info.get("isDirectory", False)

        if is_dir or filename.lower().endswith((".yaml", ".yml")):
            files.append({
                "fileName": filename,
                "isDirectory": is_dir,
                "sizeInBytes": size_info.get("sizeInBytes") or name_info.get("sizeInBytes", 0),
                "lastModified": row.get("lastModified"),
            })

    logger.info("Listed %d items (from %d total) in snapshot %s path='%s'",
                len(files), len(rows), snapshot_id, path)
    return files


# ---------------------------------------------------------------------------
# File upload (v4 chunked API)
# ---------------------------------------------------------------------------

def upload_file(
    dataset_id: str,
    file_path: str,
    content: bytes,
    project_id: Optional[str] = None,
) -> None:
    """Upload a file to a dataset via the v4 chunked upload API."""
    pid = _resolve_project_id(project_id)
    cross = _is_cross_project(pid)
    logger.info("Uploading '%s' (%d bytes) to dataset %s", file_path, len(content), dataset_id)

    # Step 1: start upload
    resp = _api_request(
        "POST", f"/v4/datasetrw/datasets/{dataset_id}/snapshot/file/start",
        cross_project=cross,
        json={
            "filePath": file_path,
            "datasetId": dataset_id,
            "collisionSetting": "Overwrite",
        },
    )
    upload_key = resp.json().get("uploadKey") or resp.text.strip().strip('"')

    # Step 2: upload single chunk (spec files are small)
    _api_request(
        "POST", f"/v4/datasetrw/datasets/{dataset_id}/snapshot/file",
        cross_project=cross,
        files={"file": (file_path.split("/")[-1], content)},
        data={
            "uploadKey": upload_key,
            "chunkIndex": "0",
            "chunkCount": "1",
            "filePath": file_path,
        },
    )

    # Step 3: finalize
    _api_request(
        "GET", f"/v4/datasetrw/datasets/{dataset_id}/snapshot/file/end/{upload_key}",
        cross_project=cross,
    )
    logger.info("Upload complete: '%s' → dataset %s", file_path, dataset_id)


# ---------------------------------------------------------------------------
# Mount-path helpers
# ---------------------------------------------------------------------------

def get_dataset_mount_prefix() -> str:
    """Return the dataset mount prefix based on project type."""
    if os.path.isdir("/domino/datasets/local"):
        return "/domino/datasets/local"
    return "/mnt/data"


def build_spec_mount_path(dataset_name: str, file_path: str) -> str:
    """Build the full mount path for a spec file in a dataset."""
    prefix = get_dataset_mount_prefix()
    return f"{prefix}/{dataset_name}/{file_path.lstrip('/')}"
