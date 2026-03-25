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

from auth_context import get_request_auth_header

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


def _get_auth_headers(cross_project: bool = False) -> dict[str, str]:
    """Build auth headers for Domino Datasets API calls.

    Prefers the forwarded user JWT when available — it carries the
    viewer's identity and has full RBAC permissions (reads AND writes).
    Falls back to the sidecar ephemeral token for same-project calls
    when no JWT is available (e.g. sync route handlers where the
    ContextVar may not propagate).

    Cross-project calls require the forwarded JWT — the sidecar token
    is scoped to the app's own project.
    """
    # 1. Forwarded user JWT (preferred — has full user permissions)
    forwarded = get_request_auth_header()
    if forwarded:
        logger.info("Auth: using forwarded user JWT")
        return {"Authorization": forwarded}

    if cross_project:
        raise RuntimeError(
            "Cross-project datasets API call requires a forwarded user token, "
            "but none was captured from the incoming request."
        )

    # 2. Sidecar ephemeral token (same-project fallback — read-only safe)
    try:
        resp = httpx.get("http://localhost:8899/access-token", timeout=3.0)
        if resp.status_code == 200 and resp.text.strip():
            logger.info("Auth: using sidecar ephemeral token (no forwarded JWT)")
            return {"Authorization": f"Bearer {resp.text.strip()}"}
        logger.warning("Sidecar /access-token returned status=%s body=%r", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("Sidecar /access-token failed: %s", exc)

    # 3. API key from environment
    api_key = os.environ.get("DOMINO_USER_API_KEY") or os.environ.get("DOMINO_API_KEY") or ""
    if api_key:
        logger.info("Auth: using DOMINO_USER_API_KEY")
        return {"X-Domino-Api-Key": api_key}

    raise RuntimeError(
        "No Domino auth credentials available. "
        "Need a forwarded user token, sidecar ephemeral token, or DOMINO_USER_API_KEY."
    )


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
    """Authenticated request to the Domino Datasets API."""
    base = _resolve_api_host() if not cross_project else _resolve_nucleus_host()
    if not base:
        raise RuntimeError("No Domino API host configured")

    url = f"{base}{path}"
    logger.debug("Datasets API %s %s (cross_project=%s)", method, path, cross_project)
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        headers = _get_auth_headers(cross_project=cross_project)
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
                if resp.status_code >= 400:
                    logger.warning(
                        "Datasets API %s %s → %s body=%s",
                        method, path, resp.status_code, resp.text[:500],
                    )
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
    """List datasets for a project.

    Uses the v2 API without minimumPermission (that param isn't a valid
    enum value and caused 500s).
    """
    pid = _resolve_project_id(project_id)
    cross = _is_cross_project(pid)
    logger.info("Listing datasets for project %s (cross=%s)", pid, cross)

    datasets: list[dict[str, Any]] = []
    offset = 0
    page_size = 50

    while True:
        resp = _api_request(
            "GET", "/api/datasetrw/v2/datasets",
            cross_project=cross,
            params={
                "projectIdsToInclude": pid,
                "offset": offset,
                "limit": page_size,
            },
        )
        data = resp.json()
        # v2 wraps each item: {"datasets": [{"dataset": {...}, "projectInfo": {...}}, ...]}
        items = data.get("datasets") or data.get("items") or []
        if not items:
            break
        for item in items:
            ds = item.get("dataset", item) if isinstance(item, dict) else item
            snapshot_ids = ds.get("snapshotIds") or []
            datasets.append({
                "id": ds.get("datasetId") or ds.get("id", ""),
                "name": ds.get("datasetName") or ds.get("name", ""),
                "description": ds.get("description", ""),
                "rwSnapshotId": ds.get("readWriteSnapshotId") or (snapshot_ids[0] if snapshot_ids else None),
            })
        if len(items) < page_size:
            break
        offset += page_size

    logger.info("Found %d datasets for project %s", len(datasets), pid)
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
            logger.info("Create dataset response: %s", data)
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

    resp = _api_request(
        "GET", f"/v4/datasetrw/files/{snapshot_id}",
        cross_project=cross, params={"path": path},
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

async def upload_file(
    dataset_id: str,
    file_path: str,
    content: bytes,
    project_id: Optional[str] = None,
) -> None:
    """Upload a file to a dataset via the v4 chunked upload API.

    Uses httpx.AsyncClient (matching AutoML Extension's pattern) — the
    sidecar proxy returns 403 on multipart POSTs from sync httpx.Client.
    """
    import hashlib
    import io

    pid = _resolve_project_id(project_id)
    # Always route uploads through the sidecar proxy — it injects auth
    # headers that the v4 multipart upload endpoints require (per AutoML).
    headers = _get_auth_headers(cross_project=False)
    base = _resolve_api_host()
    base_url = base.rstrip("/")
    logger.info("Uploading '%s' (%d bytes) to dataset %s", file_path, len(content), dataset_id)

    # Step 1: start upload session
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(
            "POST",
            f"{base_url}/v4/datasetrw/datasets/{dataset_id}/snapshot/file/start",
            json={"filePaths": [file_path], "fileCollisionSetting": "Overwrite"},
            headers=headers,
        )
        if resp.status_code >= 400:
            logger.warning("Upload start failed: %s body=%s", resp.status_code, resp.text[:500])
        resp.raise_for_status()

    upload_key = resp.json()
    if not isinstance(upload_key, str):
        upload_key = upload_key.get("upload_key") or upload_key.get("uploadKey") or upload_key.get("key")
    if not upload_key:
        raise RuntimeError(f"Failed to start upload session for dataset {dataset_id}")

    try:
        # Step 2: upload single chunk
        identifier = file_path.replace(".", "-").replace("/", "-")
        checksum = hashlib.md5(content).hexdigest()
        chunk_params = {
            "key": upload_key,
            "resumableChunkNumber": 1,
            "resumableChunkSize": len(content),
            "resumableCurrentChunkSize": len(content),
            "resumableTotalChunks": 1,
            "resumableIdentifier": identifier,
            "resumableRelativePath": file_path,
            "checksum": checksum,
        }
        # Csrf-Token: nocheck bypasses Play framework CSRF protection on multipart POSTs
        chunk_headers = {**headers, "Csrf-Token": "nocheck"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                "POST",
                f"{base_url}/v4/datasetrw/datasets/{dataset_id}/snapshot/file",
                params=chunk_params,
                files={file_path: (file_path, io.BytesIO(content), "application/octet-stream")},
                headers=chunk_headers,
            )
            if resp.status_code >= 400:
                logger.warning("Upload chunk failed: %s body=%s", resp.status_code, resp.text[:500])
            resp.raise_for_status()

        # Step 3: finalize
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                "GET",
                f"{base_url}/v4/datasetrw/datasets/{dataset_id}/snapshot/file/end/{upload_key}",
                headers=headers,
            )
            resp.raise_for_status()
        logger.info("Upload complete: '%s' → dataset %s", file_path, dataset_id)

    except Exception:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.request(
                    "GET",
                    f"{base_url}/v4/datasetrw/datasets/{dataset_id}/snapshot/file/cancel/{upload_key}",
                    headers=headers,
                )
        except Exception:
            pass
        raise


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
