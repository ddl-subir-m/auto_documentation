"""Domino API client using direct REST calls (no SDK dependency).

Supports an optional ``project_id`` override so the app can target a
different project than the one it is running in.  When *project_id* is
``None`` every helper falls back to the environment variables set by
the Domino platform (``DOMINO_PROJECT_ID``, ``DOMINO_PROJECT_OWNER``,
``DOMINO_PROJECT_NAME``).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domino status → local status mapping
# ---------------------------------------------------------------------------
_PENDING_STATUSES = {"submitted", "queued", "pending", "initializing", "provisioning"}
_RUNNING_STATUSES = {"running", "executing"}
_SUCCEEDED_STATUSES = {"succeeded", "success", "completed", "done"}
_FAILED_STATUSES = {"failed", "error"}
_CANCELLED_STATUSES = {"stopped", "cancelled", "archived"}

# ---------------------------------------------------------------------------
# Retryable HTTP status codes & defaults
# ---------------------------------------------------------------------------
_RETRYABLE_STATUS_CODES = (408, 502, 503, 504)
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# Project info cache
# ---------------------------------------------------------------------------
@dataclass
class ProjectInfo:
    id: str
    name: str
    owner_username: str


_project_cache: dict[str, ProjectInfo] = {}


# ---------------------------------------------------------------------------
# API host / auth resolution
# ---------------------------------------------------------------------------

def _resolve_api_host() -> str:
    """Return the Domino API base URL (proxy-preferred).

    Priority: DOMINO_API_PROXY > DOMINO_API_HOST.
    The proxy (localhost:8899) is scoped to the current project.
    """
    host = os.environ.get("DOMINO_API_PROXY") or os.environ.get("DOMINO_API_HOST") or ""
    return host.rstrip("/")


def _resolve_nucleus_host() -> str:
    """Return the full Domino API host (not the sidecar proxy).

    Use this for cross-project calls that the local proxy doesn't route.
    """
    host = os.environ.get("DOMINO_API_HOST") or ""
    return host.rstrip("/")


def _get_auth_headers() -> dict[str, str]:
    """Build Domino auth headers.

    Priority:
    1. Ephemeral token from Domino sidecar (localhost:8899)
    2. DOMINO_USER_API_KEY / DOMINO_API_KEY env var
    """
    # Try ephemeral token first (available in Domino Apps / Runs)
    try:
        import httpx
        resp = httpx.get("http://localhost:8899/access-token", timeout=3.0)
        if resp.status_code == 200 and resp.text.strip():
            return {"Authorization": f"Bearer {resp.text.strip()}"}
    except Exception:
        pass

    api_key = os.environ.get("DOMINO_USER_API_KEY") or os.environ.get("DOMINO_API_KEY") or ""
    if api_key:
        return {"X-Domino-Api-Key": api_key}

    return {}


# ---------------------------------------------------------------------------
# Core HTTP helper with retry
# ---------------------------------------------------------------------------

def _domino_request(
    method: str,
    path: str,
    *,
    json: Any = None,
    timeout: float = _DEFAULT_TIMEOUT,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    cross_project: bool = False,
) -> Any:
    """Send a synchronous HTTP request to the Domino API with retry logic.

    When *cross_project* is True, bypasses the sidecar proxy and uses
    DOMINO_API_HOST directly (required for cross-project lookups).
    """
    import httpx

    base_url = _resolve_nucleus_host() if cross_project else _resolve_api_host()
    if not base_url:
        raise RuntimeError("Domino API host is not configured. Set DOMINO_API_PROXY or DOMINO_API_HOST.")

    url = f"{base_url}{path}"
    logger.debug("Domino API %s %s (cross_project=%s, base=%s)", method, url, cross_project, base_url)
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        headers = _get_auth_headers()
        headers["Content-Type"] = "application/json"
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.request(method, url, json=json, headers=headers)
                if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < max_retries:
                    import time
                    backoff = 2 ** attempt
                    logger.warning(
                        "Domino API %s %s returned %s, retrying in %ss (attempt %s/%s)",
                        method, path, resp.status_code, backoff, attempt + 1, max_retries,
                    )
                    time.sleep(backoff)
                    continue
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError:
            # Don't retry client errors (4xx) — only transient server errors
            raise
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                import time
                backoff = 2 ** attempt
                logger.warning(
                    "Domino API %s %s failed (%s), retrying in %ss (attempt %s/%s)",
                    method, path, exc, backoff, attempt + 1, max_retries,
                )
                time.sleep(backoff)
                continue
            raise

    raise last_exc or RuntimeError("Domino request failed after retries")


# ---------------------------------------------------------------------------
# Environment-variable helpers (fallbacks when no project_id is provided)
# ---------------------------------------------------------------------------

def _env_project_id() -> str:
    return os.environ.get("DOMINO_PROJECT_ID", "")


def _env_project_owner() -> str:
    return os.environ.get("DOMINO_PROJECT_OWNER", "")


def _env_project_name() -> str:
    return os.environ.get("DOMINO_PROJECT_NAME", "")


# ---------------------------------------------------------------------------
# Project resolution
# ---------------------------------------------------------------------------

def resolve_project(project_id: str) -> Optional[ProjectInfo]:
    """Resolve project name and owner from the Domino v4 projects API.

    Returns cached ``ProjectInfo`` on success, ``None`` on failure.
    """
    if project_id in _project_cache:
        return _project_cache[project_id]

    api_host = _resolve_api_host()
    if not api_host:
        logger.warning("Cannot resolve project %s: no Domino API host configured", project_id)
        return None

    try:
        # Try the internal /v4/ path first (works in many deployments),
        # then fall back to the documented public API path.
        data = None
        for path in (
            f"/v4/projects/{project_id}",
            f"/api/projects/v1/projects/{project_id}",
        ):
            try:
                data = _domino_request("GET", path, cross_project=True)
                break
            except Exception as path_exc:
                logger.debug("Project resolve path %s failed: %s", path, path_exc)
                continue

        if data is None:
            logger.warning("All project resolve paths failed for %s", project_id)
            return None

        name = data.get("name")
        owner = data.get("ownerUsername") or (data.get("owner") or {}).get("userName")

        if not name or not owner:
            logger.warning(
                "Project %s response missing name/owner: %s",
                project_id,
                {k: data.get(k) for k in ("name", "ownerUsername", "owner")},
            )
            return None

        info = ProjectInfo(id=project_id, name=name, owner_username=owner)
        _project_cache[project_id] = info
        logger.info("Resolved project %s → %s/%s", project_id, owner, name)
        return info

    except Exception:
        logger.exception("Error resolving project %s", project_id)
        return None


def get_project_context(
    project_id: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Resolve (project_id, project_name, project_owner).

    When *project_id* is provided, calls the Domino API to look up the
    project name and owner.  Otherwise falls back to env vars.
    """
    if project_id:
        info = resolve_project(project_id)
        if info:
            return info.id, info.name, info.owner_username
        # Resolution failed — return what we have
        return project_id, _env_project_name(), _env_project_owner()

    return (
        _env_project_id(),
        _env_project_name(),
        _env_project_owner(),
    )


# ---------------------------------------------------------------------------
# Branches (read from local git — unchanged)
# ---------------------------------------------------------------------------

def list_branches() -> list[dict[str, Any]]:
    """Return git branches from the local repo."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "branch", "-r", "--format=%(refname:short)"],
            capture_output=True, text=True, timeout=5,
            cwd="/mnt/code",
        )
        if result.returncode == 0 and result.stdout.strip():
            branches = []
            for line in result.stdout.strip().splitlines():
                name = line.strip()
                if name.startswith("origin/"):
                    name = name[len("origin/"):]
                if name == "HEAD" or "->" in name:
                    continue
                if name:
                    branches.append({"name": name})
            if branches:
                return branches

        result = subprocess.run(
            ["git", "branch", "--format=%(refname:short)"],
            capture_output=True, text=True, timeout=5,
            cwd="/mnt/code",
        )
        if result.returncode == 0 and result.stdout.strip():
            return [
                {"name": line.strip()}
                for line in result.stdout.strip().splitlines()
                if line.strip()
            ]
    except Exception as exc:
        logger.warning("Failed to list branches from git: %s", exc)

    return []


# ---------------------------------------------------------------------------
# Hardware tiers
# ---------------------------------------------------------------------------

def list_hardware_tiers(project_id: Optional[str] = None) -> list[dict[str, Any]]:
    """Return available hardware tiers for a project.

    When *project_id* is given, queries that project's tiers via the API.
    Otherwise falls back to env-var project or the global tiers endpoint.
    """
    pid = project_id or _env_project_id()
    if not pid:
        logger.warning("No project ID available to list hardware tiers")
        return []

    # Use the full API host when querying a different project
    is_cross = bool(project_id) and project_id != _env_project_id()

    try:
        # The proxy supports the project-scoped /v4/ path; the documented
        # global endpoint requires nucleus auth, so use /v4/ for both cases
        # and route cross-project calls through DOMINO_API_HOST.
        data = _domino_request("GET", f"/v4/projects/{pid}/hardwareTiers", cross_project=is_cross)
        tiers = data if isinstance(data, list) else data.get("hardwareTiers", data.get("data", []))
        results = []
        for t in tiers:
            hw = t.get("hardwareTier", t) if isinstance(t, dict) else t
            if not isinstance(hw, dict):
                continue
            results.append({
                "id": hw.get("id", ""),
                "name": hw.get("name") or hw.get("id", ""),
                "isDefault": hw.get("hwtFlags", {}).get("isDefault", False)
                    if isinstance(hw.get("hwtFlags"), dict)
                    else hw.get("isDefault", False),
            })
        return results
    except Exception as exc:
        logger.warning("Failed to list hardware tiers: %s", exc)
        return []


def get_project_default_tier() -> Optional[str]:
    """Return the default hardware tier ID for the project."""
    override = os.environ.get("AUTODOC_DEFAULT_HARDWARE_TIER")
    if override:
        return override
    return os.environ.get("DOMINO_HARDWARE_TIER_ID") or None


# ---------------------------------------------------------------------------
# Job submission
# ---------------------------------------------------------------------------

def submit_job(
    command: str | list[str],
    branch: Optional[str],
    tier_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> str:
    """Submit a Domino job via the v1 jobs API and return the job ID.

    When *project_id* is provided the job is started in that project.
    Otherwise uses the env-var project.
    """
    pid, pname, _ = get_project_context(project_id)
    if not pid:
        raise RuntimeError("No project ID available to submit a job.")

    title = f"AutoDoc: {pname or pid}" + (f" ({branch})" if branch else "")

    command_str = " ".join(command) if isinstance(command, list) else command

    payload: dict[str, Any] = {
        "projectId": pid,
        "commandToRun": command_str,
        "title": title,
    }
    if tier_id:
        payload["overrideHardwareTierId"] = tier_id
    if branch:
        payload["mainRepoGitRef"] = {"type": "branches", "value": branch}

    is_cross = bool(project_id) and project_id != _env_project_id()

    logger.info(
        "submit_job: project_id=%s, pid=%s, is_cross=%s",
        project_id, pid, is_cross,
    )

    try:
        data = _domino_request("POST", "/v4/jobs/start", json=payload, cross_project=is_cross)
    except Exception:
        # Retry without commit pin if Domino can't resolve the ref
        if branch and payload.get("mainRepoGitRef"):
            logger.warning("Retrying job start without mainRepoGitRef")
            payload.pop("mainRepoGitRef", None)
            data = _domino_request("POST", "/v4/jobs/start", json=payload, cross_project=is_cross)
        else:
            raise

    logger.info("Domino job_start response: %r", data)

    run_id = (
        data.get("id")
        or data.get("runId")
        or data.get("run_id")
        or data.get("jobId")
    )
    if not run_id:
        raise ValueError(f"Domino job_start returned unexpected response: {data!r}")

    return str(run_id)


# ---------------------------------------------------------------------------
# Job status
# ---------------------------------------------------------------------------

def get_job_status(run_id: str) -> dict[str, Any]:
    """Return job status dict from Domino.

    Uses the v1 jobs API.  Returns a dict with 'domino_status' and
    'local_status'.
    """
    try:
        data = _domino_request("GET", f"/v4/jobs/{run_id}")
    except Exception as exc:
        logger.warning("Failed to get status for run %s: %s", run_id, exc)
        return {"domino_status": "unknown", "local_status": "running"}

    raw = (
        data.get("statuses", {}).get("executionStatus", "")
        or data.get("status")
        or data.get("jobStatus")
        or ""
    )

    raw_lower = raw.lower()
    if raw_lower in _SUCCEEDED_STATUSES:
        local = "succeeded"
    elif raw_lower in _FAILED_STATUSES:
        local = "failed"
    elif raw_lower in _CANCELLED_STATUSES:
        local = "cancelled"
    elif raw_lower in _RUNNING_STATUSES:
        local = "running"
    else:
        local = "submitted"

    return {"domino_status": raw, "local_status": local}


# ---------------------------------------------------------------------------
# Job stop
# ---------------------------------------------------------------------------

def stop_job(run_id: str) -> None:
    """Stop a running Domino job."""
    try:
        _domino_request("POST", "/v4/jobs/stop", json={"jobId": run_id, "commitResults": True})
    except Exception as exc:
        logger.warning("Failed to stop run %s: %s", run_id, exc)


# ---------------------------------------------------------------------------
# UI host resolution & job URL building
# ---------------------------------------------------------------------------

_ui_host: str | None = None


def set_ui_host(request_host: str, scheme: str = "https") -> None:
    """Cache the external Domino UI host from an incoming request header."""
    global _ui_host
    if _ui_host is not None:
        return

    from urllib.parse import urlparse, urlunparse

    raw = (request_host or "").strip()
    if not raw:
        return

    if "://" not in raw:
        raw = f"{scheme}://{raw}"

    parsed = urlparse(raw)
    hostname = (parsed.hostname or "").strip()
    if not hostname:
        return

    if hostname.startswith("apps."):
        hostname = hostname[len("apps."):]
    if not hostname:
        return

    netloc = f"{hostname}:{parsed.port}" if parsed.port else hostname
    _ui_host = urlunparse((parsed.scheme or scheme, netloc, "", "", "", "")).rstrip("/")
    logger.info("Domino UI host resolved from request: %s", _ui_host)


def build_job_url(run_id: str, project_id: Optional[str] = None) -> str | None:
    """Return the Domino UI URL for a job.

    When *project_id* is given, resolves owner/name from the API cache.
    """
    if not _ui_host:
        return None

    _, pname, powner = get_project_context(project_id)
    if not powner or not pname:
        return None
    return f"{_ui_host}/jobs/{powner}/{pname}/{run_id}/logs?status=all"
