"""Job submission index backed by a JSON file in the Datasets API.

Tracks which jobs were submitted by this app, with metadata needed for
display (user, branch, spec, tier). Actual job status comes live from
the Domino Jobs API — we don't duplicate it.

The index file lives at ``.autodoc/jobs_index.json`` in the autodoc
dataset, read/written via DatasetStore.

Local queue: jobs waiting for a slot are tracked in the index with
``domino_run_id: null``. Once submitted, the run ID is filled in and
status comes from Domino.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

_INDEX_PATH = ".autodoc/jobs_index.json"
_MAX_COMPLETED_JOBS = 50  # Keep at most this many completed jobs per user
_COMPLETED_STATUSES = {"succeeded", "failed", "cancelled"}
_INDEX_LOCK = threading.Lock()  # Serialize read-modify-write on the JSON index


# ---------------------------------------------------------------------------
# Index I/O
# ---------------------------------------------------------------------------

def _read_index() -> list[dict[str, Any]]:
    """Load the job index from the dataset."""
    from dataset_store import get_store
    store = get_store()
    try:
        if not store.file_exists(_INDEX_PATH):
            return []
        content = store.read_file(_INDEX_PATH)
        return json.loads(content)
    except Exception as exc:
        logger.warning("Failed to read job index: %s", exc)
        return []


def _write_index(jobs: list[dict[str, Any]]) -> None:
    """Write the job index to the dataset, pruning old completed jobs."""
    from dataset_store import get_store

    # Prune: keep all active jobs, cap completed jobs per user
    active = [j for j in jobs if j.get("status") not in _COMPLETED_STATUSES]
    completed = [j for j in jobs if j.get("status") in _COMPLETED_STATUSES]

    # Group completed by user, keep newest N per user
    by_user: dict[str, list[dict[str, Any]]] = {}
    for j in completed:
        by_user.setdefault(j.get("username", ""), []).append(j)
    pruned_completed = []
    for user_jobs in by_user.values():
        user_jobs.sort(key=lambda j: j.get("submitted_at", ""), reverse=True)
        pruned_completed.extend(user_jobs[:_MAX_COMPLETED_JOBS])

    jobs = active + pruned_completed

    content = json.dumps(jobs, indent=2).encode("utf-8")
    get_store().write_file(_INDEX_PATH, content)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Public API (same interface as before, minus init_db)
# ---------------------------------------------------------------------------

def init_db() -> None:
    """No-op for backwards compatibility. Index is created on first write."""
    pass


def create_job(
    username: str,
    branch: Optional[str],
    tier: Optional[str],
    spec_path: Optional[str],
    command: Optional[str] = None,
    job_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> str:
    """Record a new job submission in the index."""
    jid = job_id or str(uuid4())
    with _INDEX_LOCK:
        jobs = _read_index()
        jobs.append({
            "id": jid,
            "username": username,
            "domino_run_id": None,
            "branch": branch,
            "hardware_tier": tier,
            "status": "queued",
            "domino_status": None,
            "job_url": None,
            "spec_path": spec_path,
            "command": command,
            "submitted_at": _now_iso(),
            "completed_at": None,
            "project_id": project_id,
        })
        _write_index(jobs)
    return jid


def update_job(job_id: str, **fields: Any) -> None:
    """Update fields on a job record in the index."""
    if not fields:
        return
    with _INDEX_LOCK:
        jobs = _read_index()
        for job in jobs:
            if job["id"] == job_id:
                job.update(fields)
                break
        _write_index(jobs)


def get_job(job_id: str) -> Optional[dict[str, Any]]:
    """Return a single job record, or None."""
    jobs = _read_index()
    for job in jobs:
        if job["id"] == job_id:
            return job
    return None


def get_user_jobs(username: str, limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent jobs for a user, newest first."""
    jobs = _read_index()
    user_jobs = [j for j in jobs if j.get("username") == username]
    user_jobs.sort(key=lambda j: j.get("submitted_at", ""), reverse=True)
    return user_jobs[:limit]


def count_active_jobs(username: str) -> int:
    """Count queued + submitted + pending + running jobs for a user."""
    active_statuses = {"queued", "submitted", "pending", "running"}
    jobs = _read_index()
    return sum(
        1 for j in jobs
        if j.get("username") == username and j.get("status") in active_statuses
    )


def get_oldest_queued_job(username: str) -> Optional[dict[str, Any]]:
    """Return the oldest queued job for a user, or None."""
    jobs = _read_index()
    queued = [
        j for j in jobs
        if j.get("username") == username and j.get("status") == "queued"
    ]
    if not queued:
        return None
    queued.sort(key=lambda j: j.get("submitted_at", ""))
    return queued[0]


def get_active_jobs() -> list[dict[str, Any]]:
    """Return all jobs with status submitted, pending, or running."""
    active_statuses = {"submitted", "pending", "running"}
    jobs = _read_index()
    return [j for j in jobs if j.get("status") in active_statuses]


def get_queued_usernames() -> list[str]:
    """Return distinct usernames that have queued jobs."""
    jobs = _read_index()
    return list({
        j["username"] for j in jobs
        if j.get("status") == "queued"
    })


def reconcile_stale_jobs() -> None:
    """Mark submitted/running jobs with no run ID as failed (app restarted)."""
    with _INDEX_LOCK:
        jobs = _read_index()
        changed = False
        for job in jobs:
            if (
                job.get("status") in ("submitted", "pending", "running")
                and not job.get("domino_run_id")
            ):
                job["status"] = "failed"
                job["domino_status"] = "App restarted"
                changed = True
        if changed:
            _write_index(jobs)


def cancel_queued_jobs(username: str) -> None:
    """Cancel all queued (not yet submitted) jobs for a user."""
    with _INDEX_LOCK:
        jobs = _read_index()
        changed = False
        for job in jobs:
            if (
                job.get("username") == username
                and job.get("status") == "queued"
                and not job.get("domino_run_id")
            ):
                job["status"] = "cancelled"
                changed = True
        if changed:
            _write_index(jobs)
