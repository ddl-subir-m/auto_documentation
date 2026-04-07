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
    domino_datasets,
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
    # --output is not passed: the CLI ignores it after the DatasetStore
    # refactor. Output goes to docs/ in the autodoc dataset via DatasetStore.
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
    # job container can read it from the mounted "autodoc" dataset.
    spec_path: Optional[str] = None
    if req.spec_content and req.spec_filename:
        saved = spec_store.save_spec(req.spec_filename, req.spec_content)
        # Convert dataset-relative path to absolute mount path
        from dataset_store import AUTODOC_DATASET_NAME
        mount_prefix = domino_datasets.get_dataset_mount_prefix()
        spec_path = f"{mount_prefix}/{AUTODOC_DATASET_NAME}/{saved}"
    elif req.spec_path:
        # Resolve dataset:// references to actual mount paths
        if req.spec_path.startswith("dataset://"):
            parts = req.spec_path[len("dataset://"):].split("/", 1)
            dataset_name = parts[0]
            file_path = parts[1] if len(parts) > 1 else ""
            mount_prefix = domino_datasets.get_dataset_mount_prefix()
            spec_path = f"{mount_prefix}/{dataset_name}/{file_path}"
        else:
            spec_path = req.spec_path

    if not spec_path:
        raise ValueError("A spec file is required. Please select or upload a spec before generating documentation.")

    # Verify the spec file still exists in the dataset (it may have been
    # deleted externally via the Domino UI between selection and submission).
    if req.spec_path and req.spec_path.startswith("dataset://"):
        # Extract the dataset-relative path for API verification
        ds_relative = req.spec_path[len("dataset://"):].split("/", 1)
        if len(ds_relative) > 1:
            from dataset_store import get_store
            if not get_store().file_exists_api(ds_relative[1]):
                raise ValueError(
                    f"The selected spec file no longer exists in the dataset. "
                    f"It may have been deleted. Please select or upload a spec file and try again."
                )

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
# Background polling
# ---------------------------------------------------------------------------

async def _poll_domino_jobs() -> None:
    """Background task: poll Domino for active job status updates."""
    while True:
        await asyncio.sleep(10)
        if not _DOMINO_AVAILABLE or not _get_target_project_name():
            continue
        try:
            # Update active jobs (status from Domino Jobs API)
            from datetime import datetime, timezone
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
                    logger.warning("Poll error for run %s: %s", run_id, exc)

            # Promote queued jobs for all users when slots open
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
            logger.warning("Domino poll loop error: %s", exc, exc_info=True)


def _reconcile_stale_jobs() -> None:
    """On startup, mark any submitted/running jobs as failed (app restarted)."""
    try:
        domino_job_store.reconcile_stale_jobs()
    except Exception as exc:
        logger.warning("Reconcile stale jobs failed: %s", exc)
