"""Domino job submission, command building, and background polling."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from starlette.requests import Request

from .state import (
    JobRequest,
    DominoJobRecord,
    _DOMINO_AVAILABLE,
    _max_jobs,
    domino_client,
    domino_job_store,
    spec_store,
    _get_target_project_id,
    _get_target_project_name,
    logger,
)
from .ui_components import (
    _sanitize_optional_int,
    _sanitize_optional_float,
    _db_record_to_dataclass,
)


# ---------------------------------------------------------------------------
# Request parsing
# ---------------------------------------------------------------------------

async def _parse_request(req: Request) -> JobRequest:
    form = await req.form()
    spec_upload = form.get("spec_upload")
    spec_content = None
    spec_filename = None
    if spec_upload and hasattr(spec_upload, "read"):
        content = await spec_upload.read()
        spec_content = content.decode("utf-8", errors="replace")
        spec_filename = getattr(spec_upload, "filename", None)

    # projectId: prefer form field, fall back to captured target or query param
    project_id = (
        form.get("target_project")
        or form.get("project_id")
        or _get_target_project_id()
        or req.query_params.get("projectId")
    )
    if not project_id:
        raise RuntimeError("No target project ID available. The app requires ?projectId= in the URL.")

    return JobRequest(
        spec_path=form.get("spec_path") or None,
        spec_content=spec_content,
        provider=form.get("provider", "anthropic"),
        model=form.get("model") or None,
        api_key=form.get("api_key") or None,
        base_url=form.get("base_url") or None,
        code_root=form.get("code_root") or None,
        max_files=_sanitize_optional_int(form.get("max_files")),
        workers=_sanitize_optional_int(form.get("workers")),
        planning_workers=_sanitize_optional_int(form.get("planning_workers")),
        timeout=_sanitize_optional_float(form.get("timeout")),
        notebook=form.get("notebook") in ("on", "true", "1", "yes"),
        notebook_path=form.get("notebook_path") or None,
        experiment_names=form.get("experiment_names") or None,
        model_names=form.get("model_names") or None,
        latest_only=form.get("latest_only") in ("on", "true", "1", "yes"),
        verbose=True,
        branch=form.get("branch") or None,
        hardware_tier=form.get("hardware_tier") or None,
        api_key_source=form.get("api_key_source", "domino_env"),
        spec_filename=spec_filename,
        project_id=project_id,
    )


# ---------------------------------------------------------------------------
# Domino job command building
# ---------------------------------------------------------------------------

def _build_job_command(req: JobRequest, spec_path: Optional[str]) -> list[str]:
    """Build the CLI command list for a Domino job from a JobRequest."""
    command = ["python", "/mnt/code/auto_model_docs/main.py"]
    if spec_path:
        command += ["--spec", spec_path]
    if req.provider:
        command += ["--provider", req.provider]
    if req.model:
        command += ["--model", req.model]
    if req.code_root:
        command += ["--code-root", req.code_root]
    # --output is not passed: the CLI writes output to /mnt/artifacts/docs/
    # via direct filesystem I/O.
    if req.max_files:
        command += ["--max-files", str(req.max_files)]
    if req.workers:
        command += ["--generation-workers", str(req.workers)]
    if req.planning_workers:
        command += ["--planning-workers", str(req.planning_workers)]
    if req.timeout:
        command += ["--timeout", str(req.timeout)]
    if req.experiment_names:
        command += ["--experiments", req.experiment_names]
    if req.model_names:
        command += ["--models", req.model_names]
    if req.latest_only:
        command += ["--latest-only"]
    # Always generate notebook for Domino jobs
    command += ["--notebook"]
    if req.verbose:
        command += ["--verbose"]
    return command


def _build_job_command_str(req: JobRequest, spec_path: Optional[str]) -> str:
    """Build the full shell command for a Domino job.

    Quotes arguments that contain spaces to prevent shell splitting.
    """
    import shlex
    parts = _build_job_command(req, spec_path)
    return " ".join(shlex.quote(p) for p in parts)


# ---------------------------------------------------------------------------
# Domino job submission
# ---------------------------------------------------------------------------

async def _submit_domino_job(req: JobRequest, username: str) -> DominoJobRecord:
    """Submit or queue a Domino job and persist it to the job index."""
    logger.info(
        "Submitting Domino job: project_id=%s, branch=%s, tier=%s",
        req.project_id, req.branch, req.hardware_tier,
    )
    if not _DOMINO_AVAILABLE:
        raise RuntimeError("Domino integration is not available.")

    # Ensure DB is initialised
    domino_job_store.init_db()

    # Resolve spec path — must be an absolute mount path so the Domino
    # job container can read it from /mnt/artifacts/.
    from domino_artifacts import ARTIFACTS_MOUNT
    spec_path: Optional[str] = None
    if req.spec_content and req.spec_filename:
        saved = spec_store.save_spec(req.spec_filename, req.spec_content)
        spec_path = f"{ARTIFACTS_MOUNT}/{saved.lstrip('/')}"
    elif req.spec_path:
        spec_path = f"{ARTIFACTS_MOUNT}/{req.spec_path.lstrip('/')}"

    if not spec_path:
        raise ValueError("A spec file is required. Please select or upload a spec before generating documentation.")

    # Verify the spec file still exists in artifacts (it may have been
    # deleted externally via the Domino UI between selection and submission).
    if req.spec_path:
        from domino_artifacts import get_store as _get_artifact_store
        try:
            if not _get_artifact_store().file_exists(req.spec_path):
                raise ValueError(
                    "The selected spec file no longer exists in artifacts. "
                    "It may have been deleted. Please select or upload a spec file and try again."
                )
        except RuntimeError as exc:
            logger.warning("Skipping spec existence check: %s", exc)

    # Build command and create the DB row (status=queued)
    command_str = _build_job_command_str(req, spec_path)

    job_id = domino_job_store.create_job(
        username=username,
        branch=req.branch,
        tier=req.hardware_tier,
        spec_path=spec_path,
        command=command_str,
        project_id=req.project_id,
    )

    # count_active_jobs includes the row we just created (status=queued)
    # so if active > max_jobs, at least one other job is already running/queued
    active = domino_job_store.count_active_jobs(username)
    if active > _max_jobs():
        # Leave as queued; background loop will submit it when a slot opens
        row = domino_job_store.get_job(job_id)
        return _db_record_to_dataclass(row)

    # Under the limit — submit immediately
    try:
        run_id = domino_client.submit_job(
            command_str,
            branch=req.branch,
            tier_id=req.hardware_tier,
            project_id=req.project_id,
        )
        job_url = domino_client.build_job_url(run_id, project_id=req.project_id)
        domino_job_store.update_job(
            job_id,
            status="submitted",
            domino_run_id=run_id,
            job_url=job_url,
        )
    except Exception as exc:
        domino_job_store.update_job(
            job_id,
            status="failed",
            domino_status=str(exc),
        )
        logger.error("Domino job submission failed: %s", exc, exc_info=True)

    row = domino_job_store.get_job(job_id)
    return _db_record_to_dataclass(row)


# ---------------------------------------------------------------------------
# Status refresh (called during HTMX poll with viewer's JWT)
# ---------------------------------------------------------------------------

def _refresh_active_job_statuses() -> None:
    """Check Domino API for active job status updates.

    Called from the job-history HTMX endpoint, which runs in request
    context with the viewer's JWT for proper auth.
    """
    from datetime import datetime, timezone
    try:
        active_jobs = domino_job_store.get_active_jobs()
        for row in active_jobs:
            run_id = row.get("domino_run_id")
            if not run_id:
                continue
            try:
                status_info = domino_client.get_job_status(run_id)
                domino_status = status_info.get("domino_status", "")
                mapped = status_info.get("local_status", "submitted")
                updates: dict[str, Any] = {}
                if domino_status != row.get("domino_status"):
                    updates["domino_status"] = domino_status
                if mapped != row.get("status"):
                    updates["status"] = mapped
                if mapped in ("succeeded", "failed", "cancelled"):
                    updates["completed_at"] = datetime.now(tz=timezone.utc).isoformat()
                if updates:
                    domino_job_store.update_job(row["id"], **updates)
            except Exception as exc:
                logger.warning("Status refresh error for run %s: %s", run_id, exc)

        # Promote queued jobs when slots open
        for uname in domino_job_store.get_queued_usernames():
            active = domino_job_store.count_active_jobs(uname)
            if active > _max_jobs():
                continue
            oldest = domino_job_store.get_oldest_queued_job(uname)
            if not oldest or oldest.get("domino_run_id"):
                continue
            try:
                cmd = oldest.get("command", "")
                run_id = domino_client.submit_job(
                    cmd,
                    branch=oldest.get("branch"),
                    tier_id=oldest.get("hardware_tier"),
                    project_id=oldest.get("project_id"),
                )
                job_url = domino_client.build_job_url(run_id, project_id=oldest.get("project_id"))
                domino_job_store.update_job(
                    oldest["id"],
                    status="submitted",
                    domino_run_id=run_id,
                    job_url=job_url,
                )
            except Exception as exc:
                logger.warning("Failed to promote queued job %s: %s", oldest["id"], exc)
    except Exception as exc:
        logger.warning("Job status refresh error: %s", exc, exc_info=True)


async def _poll_domino_jobs() -> None:
    """Background task: kept for compatibility but no longer polls Domino.

    Status refreshes now happen during HTMX polls (job-history endpoint)
    which run in request context with the viewer's JWT.
    """
    # No-op — status refresh moved to _refresh_active_job_statuses()
    while True:
        await asyncio.sleep(3600)  # Sleep indefinitely


def _reconcile_stale_jobs() -> None:
    """On startup, mark any submitted/running jobs as failed (app restarted)."""
    try:
        domino_job_store.reconcile_stale_jobs()
    except Exception as exc:
        logger.warning("Reconcile stale jobs failed: %s", exc)
