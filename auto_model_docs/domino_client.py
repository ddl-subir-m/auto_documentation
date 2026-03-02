"""Thin wrapper around the dominodatalab Python SDK for job submission."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# Domino status → local status mapping
_PENDING_STATUSES = {"submitted", "queued", "pending", "initializing", "provisioning"}
_RUNNING_STATUSES = {"running", "executing"}
_SUCCEEDED_STATUSES = {"succeeded", "success", "completed", "done"}
_FAILED_STATUSES = {"failed", "error"}
_CANCELLED_STATUSES = {"stopped", "cancelled", "archived"}


def _get_domino():
    """Construct a Domino SDK client from environment variables."""
    try:
        from domino import Domino  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "dominodatalab package is not installed. Add it to requirements.txt."
        ) from exc

    owner = os.environ.get("DOMINO_PROJECT_OWNER", "")
    project = os.environ.get("DOMINO_PROJECT_NAME", "")
    host = os.environ.get("DOMINO_API_HOST")
    api_proxy = os.environ.get("DOMINO_API_PROXY")
    api_key = os.environ.get("DOMINO_USER_API_KEY")

    return Domino(
        project=f"{owner}/{project}",
        host=host,
        api_proxy=api_proxy,
        api_key=api_key,
    )


def _api_host() -> str:
    return (os.environ.get("DOMINO_API_HOST") or "").rstrip("/")


def _project_owner() -> str:
    return os.environ.get("DOMINO_PROJECT_OWNER", "")


def _project_name() -> str:
    return os.environ.get("DOMINO_PROJECT_NAME", "")


def _auth_headers() -> dict[str, str]:
    api_key = os.environ.get("DOMINO_USER_API_KEY", "")
    return {"X-Domino-Api-Key": api_key} if api_key else {}


def list_branches() -> list[dict[str, Any]]:
    """Return list of git branches for the current project.

    Uses the REST API directly since the SDK does not expose this endpoint.
    Returns a list of dicts with at least 'name' and 'sha' keys.
    Falls back to an empty list with a warning on any error.
    """
    host = _api_host()
    owner = _project_owner()
    project = _project_name()
    if not all([host, owner, project]):
        logger.warning("Domino environment variables not set; cannot list branches.")
        return []

    url = f"{host}/v1/projects/{owner}/{project}/refs/branches"
    try:
        resp = requests.get(url, headers=_auth_headers(), timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # Response shape: {"branches": [{"name": "...", "sha": "..."}, ...]}
        branches = data.get("branches", data) if isinstance(data, dict) else data
        return [b for b in branches if isinstance(b, dict)]
    except Exception as exc:
        logger.warning("Failed to list branches: %s", exc)
        return []


def list_hardware_tiers() -> list[dict[str, Any]]:
    """Return available hardware tiers for the current project.

    Returns a list of dicts with at least 'id', 'name' keys.
    Falls back to empty list on any error.
    """
    host = _api_host()
    if not host:
        return []

    url = f"{host}/v1/hardwareTiers"
    try:
        resp = requests.get(url, headers=_auth_headers(), timeout=10)
        resp.raise_for_status()
        data = resp.json()
        tiers = data.get("hardwareTiers", data) if isinstance(data, dict) else data
        return [t for t in tiers if isinstance(t, dict)]
    except Exception as exc:
        logger.warning("Failed to list hardware tiers: %s", exc)
        return []


def get_project_default_tier() -> Optional[str]:
    """Return the default hardware tier name for the project.

    Checks env var AUTODOC_DEFAULT_HARDWARE_TIER first, then
    DOMINO_DEFAULT_HARDWARE_TIER_ID.  Falls back to None.
    """
    override = os.environ.get("AUTODOC_DEFAULT_HARDWARE_TIER")
    if override:
        return override
    return os.environ.get("DOMINO_DEFAULT_HARDWARE_TIER_ID") or None


def submit_job(
    command: list[str],
    branch: Optional[str],
    tier_name: Optional[str],
    extra_env: Optional[dict[str, str]] = None,
) -> str:
    """Submit a Domino job and return the run ID.

    Passes *branch* as commit_id so Domino resolves the latest commit on that
    branch.  If Domino cannot resolve the branch (e.g. shallow clone), retries
    without commit_id.
    """
    owner = _project_owner()
    project = _project_name()
    title = f"AutoDoc: {project}" + (f" ({branch})" if branch else "")

    domino = _get_domino()

    kwargs: dict[str, Any] = {"title": title}
    if tier_name:
        kwargs["hardware_tier_name"] = tier_name
    if extra_env:
        kwargs["environment_variables"] = extra_env

    try:
        response = domino.job_start(
            command=command,
            commit_id=branch,
            **kwargs,
        )
    except Exception as exc:
        err_msg = str(exc).lower()
        if "commit" in err_msg or "branch" in err_msg or "ref" in err_msg:
            logger.warning(
                "Branch '%s' could not be resolved; retrying without commit_id. Error: %s",
                branch,
                exc,
            )
            response = domino.job_start(command=command, **kwargs)
        else:
            raise

    # The SDK returns different shapes across versions; extract run ID robustly.
    if isinstance(response, dict):
        run_id = (
            response.get("id")
            or response.get("runId")
            or response.get("run_id")
            or response.get("jobId")
        )
    elif hasattr(response, "id"):
        run_id = response.id
    else:
        run_id = str(response)

    if not run_id:
        raise ValueError(f"Domino job_start returned unexpected response: {response!r}")

    return str(run_id)


def get_job_status(run_id: str) -> dict[str, Any]:
    """Return job status dict from Domino.

    Returns a dict with at least 'domino_status' (raw string) and
    'local_status' (one of: queued/submitted/running/succeeded/failed/cancelled).
    """
    domino = _get_domino()
    try:
        resp = domino.job_status(run_id)
    except Exception as exc:
        logger.warning("Failed to get status for run %s: %s", run_id, exc)
        return {"domino_status": "unknown", "local_status": "running"}

    if isinstance(resp, dict):
        raw = (
            resp.get("status")
            or resp.get("jobStatus")
            or resp.get("statuses", {}).get("executionStatus", "")
            or ""
        )
    elif hasattr(resp, "status"):
        raw = resp.status or ""
    else:
        raw = str(resp)

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


def stop_job(run_id: str) -> None:
    """Stop a running Domino job."""
    domino = _get_domino()
    try:
        domino.job_stop(run_id, commit_results=True)
    except Exception as exc:
        logger.warning("Failed to stop run %s: %s", run_id, exc)


def build_job_url(run_id: str) -> str:
    """Return the Domino UI URL for the given run."""
    host = _api_host()
    owner = _project_owner()
    project = _project_name()
    return f"{host}/u/{owner}/{project}/runs/{run_id}"
