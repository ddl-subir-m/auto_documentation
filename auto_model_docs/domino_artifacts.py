"""Domino Artifacts (DFS) file store via REST API.

Replaces dataset_store.py and domino_datasets.py. All artifact I/O goes
through this module — upload, download, list, and existence checks.

The DFS is separate from the git code repository:
- DFS artifacts mount at /mnt/artifacts/ in job containers
- The PUT upload API always writes to the default DFS branch
- Jobs on any git branch see the same DFS artifacts

In a job container, callers should use the filesystem helpers
(write_artifact / read_artifact) instead of the REST API.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote as _url_quote

import httpx

from domino_auth import resolve_api_host as _resolve_api_host
from domino_auth import get_auth_headers as _raw_get_auth_headers

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ARTIFACTS_MOUNT = "/mnt/artifacts"

_RETRYABLE_STATUS_CODES = (408, 502, 503, 504)
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_RETRIES = 3

_store: Optional["ArtifactStore"] = None


# ---------------------------------------------------------------------------
# Filesystem helpers (for use in job containers)
# ---------------------------------------------------------------------------

def write_artifact(path: str, content: bytes) -> None:
    """Write content to /mnt/artifacts/{path} via filesystem.

    Creates parent directories automatically. Use this in job containers
    instead of the REST API for better performance.
    """
    dest = Path(ARTIFACTS_MOUNT) / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)


def read_artifact(path: str) -> bytes:
    """Read content from /mnt/artifacts/{path} via filesystem."""
    return (Path(ARTIFACTS_MOUNT) / path).read_bytes()


def artifact_exists(path: str) -> bool:
    """Check if /mnt/artifacts/{path} exists."""
    return (Path(ARTIFACTS_MOUNT) / path).exists()


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _get_auth_headers() -> dict[str, str]:
    return _raw_get_auth_headers(required=True)


# ---------------------------------------------------------------------------
# Core HTTP helper
# ---------------------------------------------------------------------------

def _artifact_request(
    method: str,
    path: str,
    *,
    client: httpx.Client | None = None,
    content: bytes | None = None,
    files: dict[str, Any] | None = None,
    json: Any = None,
    params: dict[str, Any] | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    expect_json: bool = True,
) -> Any:
    """Send an HTTP request to the Domino API with retry logic."""
    base_url = _resolve_api_host()
    if not base_url:
        raise RuntimeError("Domino API host is not configured. Set DOMINO_API_HOST.")

    url = f"{base_url}{path}"
    logger.debug("Artifacts API %s %s", method, url)
    last_exc: Exception | None = None

    def _do_request(c: httpx.Client) -> Any:
        headers = _get_auth_headers()
        # Domino's Play framework rejects POST/PUT without a CSRF token
        if method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
            headers["Csrf-Token"] = "nocheck"
        if files is not None:
            # multipart/form-data — let httpx set Content-Type with boundary
            return c.request(method, url, files=files, headers=headers)
        elif content is not None:
            headers["Content-Type"] = "application/octet-stream"
            return c.request(method, url, content=content, headers=headers)
        elif json is not None:
            headers["Content-Type"] = "application/json"
            return c.request(method, url, json=json, params=params, headers=headers)
        else:
            return c.request(method, url, params=params, headers=headers)

    for attempt in range(max_retries + 1):
        try:
            if client:
                resp = _do_request(client)
            else:
                with httpx.Client(timeout=timeout) as c:
                    resp = _do_request(c)

            if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < max_retries:
                backoff = 2 ** attempt
                logger.warning(
                    "Artifacts API %s %s returned %s, retrying in %ss (attempt %s/%s)",
                    method, path, resp.status_code, backoff, attempt + 1, max_retries,
                )
                time.sleep(backoff)
                continue
            resp.raise_for_status()
            return resp.json() if expect_json else resp.content

        except httpx.HTTPStatusError:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                backoff = 2 ** attempt
                logger.warning(
                    "Artifacts API %s %s failed (%s), retrying in %ss (attempt %s/%s)",
                    method, path, exc, backoff, attempt + 1, max_retries,
                )
                time.sleep(backoff)
                continue
            raise

    raise last_exc or RuntimeError("Artifacts request failed after retries")


# ---------------------------------------------------------------------------
# ArtifactStore
# ---------------------------------------------------------------------------

class ArtifactStore:
    """File store backed by Domino Artifacts (DFS) REST API.

    All paths are artifact-relative (e.g. "specs/my_spec.yaml").
    """

    def __init__(self, owner: str, project_name: str, project_id: str):
        self._owner = owner
        self._project = project_name
        self._project_id = project_id
        self._head_commit: str | None = None
        self._client = httpx.Client(timeout=_DEFAULT_TIMEOUT)

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def project_name(self) -> str:
        return self._project

    @property
    def project_id(self) -> str:
        return self._project_id

    @staticmethod
    def _split_path(path: str) -> tuple[str, str]:
        """Split 'dir/file.txt' into ('dir', 'file.txt')."""
        parts = path.rsplit("/", 1)
        if len(parts) == 1:
            return "", parts[0]
        return parts[0], parts[1]

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write_file(self, path: str, content: bytes) -> None:
        """Upload a file to artifacts via multipart POST.

        Uses the v4 private API:
        POST /v4/projects/{projectId}/commits/head/files/{path}

        Subdirectories are created automatically. Overwrites existing files.
        """
        encoded_path = _url_quote(path, safe="")
        api_path = (
            f"/v4/projects/{self._project_id}/commits/head"
            f"/files/{encoded_path}"
        )
        filename = path.rsplit("/", 1)[-1]
        logger.info("ArtifactStore.write_file('%s', %d bytes)", path, len(content))
        _artifact_request(
            "POST", api_path, client=self._client,
            files={"upfile": (filename, content)},
            timeout=60.0,
            expect_json=False,
        )
        self._head_commit = None
        logger.info("ArtifactStore.write_file('%s') complete", path)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_file(self, path: str) -> bytes:
        """Download file content from artifacts.

        Uses the documented public API:
        GET /api/projects/v1/projects/{projectId}/files/{commitId}/{path}/content

        Raises FileNotFoundError if the file doesn't exist.
        """
        commit_id = self.get_head_commit()
        encoded_path = _url_quote(path, safe="")
        api_path = (
            f"/api/projects/v1/projects/{self._project_id}"
            f"/files/{commit_id}/{encoded_path}/content"
        )
        logger.info("ArtifactStore.read_file('%s') commit=%s", path, commit_id[:8])
        try:
            return _artifact_request(
                "GET", api_path, client=self._client,
                expect_json=False, timeout=60.0,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise FileNotFoundError(f"File not found in artifacts: {path}") from exc
            raise

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    def list_files(self, dir_path: str = "") -> list[dict[str, Any]]:
        """List files in a directory. Returns normalized dicts with keys:
        name, size, key, path, lastModified.

        Uses the v4 private API:
        GET /v4/projects/{projectId}/commits/head/files/{path}

        Auto-resolves HEAD commit — no separate get_head_commit() call needed.
        """
        encoded_path = _url_quote(dir_path, safe="") if dir_path else ""
        api_path = (
            f"/v4/projects/{self._project_id}/commits/head"
            f"/files/{encoded_path}"
        )
        logger.info("ArtifactStore.list_files('%s')", dir_path)
        raw = _artifact_request(
            "GET", api_path, client=self._client,
        )
        result = []
        for f in raw:
            raw_path = f.get("path", "")
            # v4 API returns path as an object with canonicalizedPathString
            if isinstance(raw_path, dict):
                file_path = raw_path.get("canonicalizedPathString", "")
            else:
                file_path = raw_path
            name = file_path.rsplit("/", 1)[-1] if file_path else ""
            if not name:
                continue
            result.append({
                "name": name,
                "size": f.get("size", 0),
                "key": f.get("key", ""),
                "path": file_path,
                "lastModified": f.get("lastModified"),
            })
        return result

    # ------------------------------------------------------------------
    # Exists
    # ------------------------------------------------------------------

    def file_exists(self, path: str) -> bool:
        """Check if a file exists in artifacts."""
        dir_path, filename = self._split_path(path)
        try:
            files = self.list_files(dir_path)
            return any(f["name"] == filename for f in files)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # HEAD commit
    # ------------------------------------------------------------------

    def get_head_commit(self) -> str:
        """Resolve the HEAD commitId for the default DFS branch.

        Uses the documented public API:
        GET /api/projects/beta/projects/{projectId}/commits/head

        Cached until invalidated by a write.
        """
        if self._head_commit:
            return self._head_commit

        api_path = f"/api/projects/beta/projects/{self._project_id}/commits/head"
        data = _artifact_request(
            "GET", api_path, client=self._client,
        )
        commit_id = data.get("commitId", "")
        if not commit_id:
            raise RuntimeError(
                f"Could not resolve HEAD commit for {self._owner}/{self._project}"
            )
        self._head_commit = commit_id
        logger.info("ArtifactStore HEAD commit: %s", commit_id[:8])
        return commit_id

    def invalidate_cache(self) -> None:
        """Clear cached HEAD commit. Call after external writes."""
        self._head_commit = None


# ---------------------------------------------------------------------------
# Singleton management
# ---------------------------------------------------------------------------

def init_store(owner: str, project_name: str, project_id: str) -> ArtifactStore:
    """Initialize the global ArtifactStore singleton."""
    global _store
    if _store is not None:
        if _store.project_id == project_id:
            return _store
        logger.warning(
            "init_store called with different project: %s (current: %s). Ignoring.",
            project_id, _store.project_id,
        )
        return _store
    _store = ArtifactStore(owner, project_name, project_id)
    logger.info(
        "ArtifactStore initialized: %s/%s (id=%s)",
        owner, project_name, project_id,
    )
    return _store


def get_store() -> ArtifactStore:
    """Return the initialized ArtifactStore singleton.

    Raises RuntimeError if init_store() has not been called.
    """
    if _store is None:
        raise RuntimeError(
            "ArtifactStore not initialized. Call init_store() first."
        )
    return _store


def reset_store() -> None:
    """Reset the store singleton. Used only in tests."""
    global _store
    if _store is not None and hasattr(_store, "_client"):
        _store._client.close()
    _store = None
