"""Thin wrapper around the dominodatalab Python SDK for job submission."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

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


def list_branches() -> list[dict[str, Any]]:
    """Return list of git branches for the current project.

    Reads branches from the local git repo (always available inside Domino
    workspaces, jobs, and apps).  Falls back to an empty list on any error.
    """
    import subprocess

    try:
        # Try remote branches first (gives the full list from origin)
        result = subprocess.run(
            ["git", "branch", "-r", "--format=%(refname:short)"],
            capture_output=True, text=True, timeout=5,
            cwd="/mnt/code",
        )
        if result.returncode == 0 and result.stdout.strip():
            branches = []
            for line in result.stdout.strip().splitlines():
                name = line.strip()
                # Strip origin/ prefix and skip HEAD pointer
                if name.startswith("origin/"):
                    name = name[len("origin/"):]
                if name == "HEAD" or "->" in name:
                    continue
                if name:
                    branches.append({"name": name})
            if branches:
                return branches

        # Fall back to local branches
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


def list_hardware_tiers() -> list[dict[str, Any]]:
    """Return available hardware tiers for the current project.

    Returns a list of dicts with 'id' and 'name' keys.
    Falls back to empty list on any error.
    """
    try:
        domino = _get_domino()
        raw = domino.hardware_tiers_list()
        return [
            {
                "id": t["hardwareTier"]["id"],
                "name": t["hardwareTier"]["name"],
                "isDefault": t.get("hardwareTier", {}).get("hwtFlags", {}).get("isDefault", False),
            }
            for t in raw
            if isinstance(t, dict) and "hardwareTier" in t
        ]
    except Exception as exc:
        logger.warning("Failed to list hardware tiers: %s", exc)
        return []


def get_project_default_tier() -> Optional[str]:
    """Return the default hardware tier ID for the project.

    Checks env var AUTODOC_DEFAULT_HARDWARE_TIER first, then
    DOMINO_HARDWARE_TIER_ID (the tier of the current workspace/app).
    Falls back to None (list_hardware_tiers returns isDefault flag).
    """
    override = os.environ.get("AUTODOC_DEFAULT_HARDWARE_TIER")
    if override:
        return override
    return os.environ.get("DOMINO_HARDWARE_TIER_ID") or None


def submit_job(
    command: list[str],
    branch: Optional[str],
    tier_id: Optional[str] = None,
) -> str:
    """Submit a Domino job and return the run ID."""
    project = _project_name()
    title = f"AutoDoc: {project}" + (f" ({branch})" if branch else "")

    domino = _get_domino()

    # Domino SDK expects command as a single string, not a list.
    command_str = " ".join(command) if isinstance(command, list) else command

    kwargs: dict[str, Any] = {"title": title}
    if tier_id:
        kwargs["hardware_tier_id"] = tier_id
    if branch:
        kwargs["main_repo_git_ref"] = {"type": "branch", "value": branch}

    logger.info("Submitting Domino job: command=%r, kwargs=%r", command_str, kwargs)
    response = domino.job_start(command=command_str, **kwargs)
    logger.info("Domino job_start response: %r", response)

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
            resp.get("statuses", {}).get("executionStatus", "")
            or resp.get("status")
            or resp.get("jobStatus")
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


def _normalize_domino_ui_host(raw: str | None) -> str | None:
    """Normalize a Domino host value into a UI-safe base URL."""
    from urllib.parse import urlparse, urlunparse

    if not raw or not raw.strip():
        return None
    candidate = raw.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    hostname = (parsed.hostname or "").strip()
    if not hostname:
        return None

    # Domino UI links should resolve to tenant root, not apps subdomain.
    if hostname.startswith("apps."):
        hostname = hostname[len("apps."):]
    if not hostname:
        return None

    netloc = f"{hostname}:{parsed.port}" if parsed.port else hostname
    return urlunparse((parsed.scheme, netloc, "", "", "", ""))


def _resolve_domino_ui_host() -> str | None:
    """Resolve preferred Domino tenant host for user-facing links.

    Tries each candidate in priority order, returning the first that
    normalizes to a valid URL.
    """
    for raw in (
        os.environ.get("DOMINO_USER_HOST"),
        os.environ.get("DOMINO_EXTERNAL_HOST"),
        os.environ.get("DOMINO_LINK_HOST"),
        os.environ.get("DOMINO_API_HOST"),
    ):
        normalized = _normalize_domino_ui_host(raw)
        if normalized:
            return normalized.rstrip("/")
    return None


def build_job_url(run_id: str) -> str | None:
    """Return the Domino UI URL for the given run."""
    host = _resolve_domino_ui_host()
    if not host:
        return None
    owner = _project_owner()
    project = _project_name()
    if not owner or not project:
        return None
    return f"{host}/jobs/{owner}/{project}/{run_id}/logs?status=all"
