"""Unified file store backed by the Domino Datasets API.

All artifact read/write operations go through this store. Replaces direct
filesystem access for target-project artifacts. Uses the same auth and
retry infrastructure as domino_datasets.py.

The store targets a single Domino Dataset (typically named "autodoc")
within the target project. All paths are relative to the dataset root.

Usage:
    from dataset_store import get_store
    store = get_store()
    store.write_file("autodoc/docs/output.docx", content_bytes)
    data = store.read_file("autodoc/docs/output.docx")
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
from typing import Any, Optional

import httpx

from domino_auth import resolve_api_host as _resolve_api_host
from domino_auth import get_auth_headers as _get_auth_headers

logger = logging.getLogger(__name__)

AUTODOC_DATASET_NAME = "autodoc"
AUTODOC_DATASET_DESCRIPTION = (
    "Auto Model Docs artifacts — generated docs, specs, and internal state"
)

_store: Optional["DatasetStore"] = None


class DatasetStore:
    """Sync file store backed by the Domino Datasets v4 API.

    Provides read, write, list, delete, and exists operations for files
    within a single Domino Dataset.
    """

    def __init__(
        self,
        dataset_id: str,
        snapshot_id: str,
        project_id: str,
    ):
        self._dataset_id = dataset_id
        self._snapshot_id = snapshot_id
        self._project_id = project_id
        # Write-through cache: the Domino snapshot API is eventually consistent
        # so reads immediately after writes may return stale/empty content.
        # We cache written content locally so reads always see the latest data.
        self._cache: dict[str, bytes] = {}

    @property
    def dataset_id(self) -> str:
        return self._dataset_id

    @property
    def snapshot_id(self) -> str:
        return self._snapshot_id

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write_file(self, path: str, content: bytes) -> None:
        """Upload a file to the dataset, overwriting if it exists.

        Uses the v4 chunked upload API:
          POST /v4/datasetrw/datasets/{id}/snapshot/file/start
          POST /v4/datasetrw/datasets/{id}/snapshot/file  (chunk)
          GET  /v4/datasetrw/datasets/{id}/snapshot/file/end/{key}
        Confirmed via python-domino routes.py.
        """
        base_url = _resolve_api_host().rstrip("/")
        ds_id = self._dataset_id
        logger.info("DatasetStore.write_file('%s', %d bytes)", path, len(content))

        upload_key = None
        try:
            # Step 1: start upload session
            headers = _get_auth_headers()
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    f"{base_url}/v4/datasetrw/datasets/{ds_id}/snapshot/file/start",
                    json={"filePaths": [path], "fileCollisionSetting": "Overwrite"},
                    headers=headers,
                )
                if resp.status_code >= 400:
                    logger.warning("Upload start failed: %s body=%s", resp.status_code, resp.text[:500])
                resp.raise_for_status()

            upload_key = resp.json()
            if not isinstance(upload_key, str):
                upload_key = (
                    upload_key.get("upload_key")
                    or upload_key.get("uploadKey")
                    or upload_key.get("key")
                )
            if not upload_key:
                raise RuntimeError(f"Failed to start upload for {path}")

            # Step 2: single chunk upload
            identifier = path.replace(".", "-").replace("/", "-")
            checksum = hashlib.md5(content).hexdigest()
            chunk_params = {
                "key": upload_key,
                "resumableChunkNumber": 1,
                "resumableChunkSize": len(content),
                "resumableCurrentChunkSize": len(content),
                "resumableTotalChunks": 1,
                "resumableIdentifier": identifier,
                "resumableRelativePath": path,
                "checksum": checksum,
            }
            chunk_headers = {**_get_auth_headers(), "Csrf-Token": "nocheck"}
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    f"{base_url}/v4/datasetrw/datasets/{ds_id}/snapshot/file",
                    params=chunk_params,
                    files={path: (path, io.BytesIO(content), "application/octet-stream")},
                    headers=chunk_headers,
                )
                if resp.status_code >= 400:
                    logger.warning("Upload chunk failed: %s body=%s", resp.status_code, resp.text[:500])
                resp.raise_for_status()

            # Step 3: finalize
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(
                    f"{base_url}/v4/datasetrw/datasets/{ds_id}/snapshot/file/end/{upload_key}",
                    headers=_get_auth_headers(),
                )
                resp.raise_for_status()

            # Cache locally so subsequent reads don't hit stale snapshot
            self._cache[path] = content
            logger.info("DatasetStore.write_file('%s') complete", path)

        except Exception:
            # Cancel upload on failure (only if we obtained an upload key)
            if upload_key:
                try:
                    with httpx.Client(timeout=10.0) as client:
                        client.get(
                            f"{base_url}/v4/datasetrw/datasets/{ds_id}/snapshot/file/cancel/{upload_key}",
                            headers=_get_auth_headers(),
                        )
                except Exception:
                    pass
            raise

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_file(self, path: str) -> bytes:
        """Download file content from the dataset.

        Returns cached content if the file was recently written (avoids
        stale reads from the eventually-consistent snapshot API).
        Falls back to the snapshot file preview endpoint.
        """
        # Return cached content if available (write-through cache)
        if path in self._cache:
            logger.info("DatasetStore.read_file('%s') [cache hit]", path)
            return self._cache[path]

        base_url = _resolve_api_host().rstrip("/")
        snap_id = self._snapshot_id
        logger.info("DatasetStore.read_file('%s')", path)

        with httpx.Client(timeout=60.0) as client:
            resp = client.get(
                f"{base_url}/v4/datasetrw/snapshot/{snap_id}/file/preview",
                params={"path": path},
                headers=_get_auth_headers(),
            )
            if resp.status_code >= 400:
                logger.warning("Read failed: %s body=%s", resp.status_code, resp.text[:500])
            resp.raise_for_status()

        return resp.content

    def read_file_meta(self, path: str) -> dict[str, Any]:
        """Get file metadata (size, last modified, etc.).

        Endpoint: GET /v4/datasetrw/snapshot/{snapshotId}/file/meta?path=
        """
        base_url = _resolve_api_host().rstrip("/")
        snap_id = self._snapshot_id

        with httpx.Client(timeout=30.0) as client:
            resp = client.get(
                f"{base_url}/v4/datasetrw/snapshot/{snap_id}/file/meta",
                params={"path": path},
                headers=_get_auth_headers(),
            )
            resp.raise_for_status()

        return resp.json()

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    def list_files(self, path: str = "") -> list[dict[str, Any]]:
        """List all files at the given path (directories + files).

        Endpoint: GET /v4/datasetrw/files/{snapshotId}?path=
        Confirmed via domino_datasets.py (already working in production).
        """
        base_url = _resolve_api_host().rstrip("/")
        snap_id = self._snapshot_id

        with httpx.Client(timeout=30.0) as client:
            resp = client.get(
                f"{base_url}/v4/datasetrw/files/{snap_id}",
                params={"path": path},
                headers=_get_auth_headers(),
            )
            resp.raise_for_status()

        data = resp.json()
        rows = data.get("rows", [])

        path_prefix = (path.rstrip("/") + "/") if path else ""
        files: list[dict[str, Any]] = []
        for row in rows:
            name_info = row.get("name", {})
            size_info = row.get("size", {})
            filename = name_info.get("fileName") or name_info.get("label", "")
            is_dir = name_info.get("isDirectory", False)

            if path_prefix and filename.startswith(path_prefix):
                filename = filename[len(path_prefix):]

            files.append({
                "fileName": filename,
                "isDirectory": is_dir,
                "sizeInBytes": size_info.get("sizeInBytes") or name_info.get("sizeInBytes", 0),
                "lastModified": row.get("lastModified"),
            })

        return files

    # ------------------------------------------------------------------
    # Exists
    # ------------------------------------------------------------------

    def file_exists(self, path: str) -> bool:
        """Check if a file exists (cache or metadata endpoint)."""
        if path in self._cache:
            return True
        try:
            self.read_file_meta(path)
            return True
        except Exception:
            return False

    def file_exists_api(self, path: str) -> bool:
        """Check if a file exists via the API only (bypasses cache).

        Use this when you need to verify the file hasn't been deleted
        externally (e.g., by a user in the Domino UI).
        """
        try:
            self.read_file_meta(path)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Singleton management
# ---------------------------------------------------------------------------

def init_store(dataset_id: str, snapshot_id: str, project_id: str) -> DatasetStore:
    """Initialize the global DatasetStore singleton."""
    global _store
    if _store is not None:
        if _store.dataset_id == dataset_id:
            return _store
        logger.warning(
            "init_store called with different dataset: %s (current: %s). Ignoring.",
            dataset_id, _store.dataset_id,
        )
        return _store
    _store = DatasetStore(dataset_id, snapshot_id, project_id)
    logger.info("DatasetStore initialized: dataset=%s snapshot=%s", dataset_id, snapshot_id)
    return _store


def get_store() -> DatasetStore:
    """Return the initialized DatasetStore singleton.

    Raises RuntimeError if init_store() has not been called.
    """
    if _store is None:
        raise RuntimeError(
            "DatasetStore not initialized. Call init_store() first."
        )
    return _store


def reset_store() -> None:
    """Reset the store singleton. Used only in tests."""
    global _store
    _store = None
