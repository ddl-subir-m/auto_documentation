"""Job execution, Domino integration, and background polling."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TaskProgressColumn, TimeElapsedColumn
from starlette.requests import Request

from autodoc.core.config import Settings
from autodoc.core.models import DocumentSpec
from autodoc.llm import LLMClient
from autodoc.orchestrator import Orchestrator
from autodoc.scanning import ContentSanitizer

from .state import (
    JobState,
    JobRequest,
    DominoJobRecord,
    JOB_STORE,
    ACTIVE_JOB_ID,
    LAST_API_KEY,
    _DOMINO_AVAILABLE,
    _log,
    _cleanup_job,
    _get_default_output_dir,
    _get_default_code_root,
    _get_default_spec_path,
    _get_username,
    _max_jobs,
    console,
    domino_client,
    domino_job_store,
    spec_store,
    domino_datasets,
    logger,
)
from .ui_components import (
    _sanitize_optional_int,
    _sanitize_optional_float,
    _parse_comma_list,
    _db_record_to_dataclass,
)


# ---------------------------------------------------------------------------
# JobLogHandler
# ---------------------------------------------------------------------------

class JobLogHandler(logging.Handler):
    """Custom logging handler that captures log messages to job.logs."""

    def __init__(self, job: JobState, include_level: bool = False):
        """Initialize the handler.

        Args:
            job: The JobState to append logs to
            include_level: Whether to include log level in the message
        """
        super().__init__()
        self.job = job
        self.include_level = include_level

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record to the job logs."""
        try:
            # Format the message
            msg = self.format(record)

            # Remove any ANSI color codes if present
            msg = re.sub(r'\x1b\[[0-9;]*m', '', msg)

            # Extract just the message part if it has timestamp from formatter
            # Look for pattern like "2024-01-28 12:00:00,000 - module - LEVEL - message"
            parts = msg.split(' - ', 3)
            if len(parts) >= 4:
                # Take just the message part
                msg = parts[-1]
            elif len(parts) >= 2:
                # Might be "LEVEL - message" format
                msg = parts[-1]

            # Optionally prepend log level
            if self.include_level:
                level_name = record.levelname
                if level_name == "WARNING":
                    msg = f"\u26a0 {msg}"
                elif level_name == "ERROR":
                    msg = f"\u2717 {msg}"
                elif level_name == "INFO":
                    # Don't prepend anything for INFO to keep it clean
                    pass

            # Add to job logs with timestamp
            _log(self.job, msg)

        except Exception:
            # Don't let logging errors break the application
            pass


# ---------------------------------------------------------------------------
# Job execution
# ---------------------------------------------------------------------------

async def _run_generation(job: JobState, request: JobRequest) -> None:
    import stitch.state as _state
    progress_ctx = None
    log_handler = None
    try:
        job.status = "running"
        _log(job, "Preparing generation run.")

        # Configure logging based on verbose flag
        if request.verbose:
            # Create our custom handler to capture logs to the job
            log_handler = JobLogHandler(job, include_level=True)
            log_handler.setLevel(logging.INFO)
            # Use a simple formatter that doesn't include timestamp (we add our own)
            log_handler.setFormatter(logging.Formatter('%(name)s - %(levelname)s - %(message)s'))

            # Configure root logging
            logging.basicConfig(
                level=logging.INFO,
                format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                handlers=[logging.StreamHandler()],
                force=True  # Override any existing configuration
            )

            # Add our handler ONLY to the root autodoc logger
            # Child loggers will propagate their messages up to this handler
            autodoc_logger = logging.getLogger('autodoc')
            autodoc_logger.setLevel(logging.INFO)
            autodoc_logger.addHandler(log_handler)

            # Set log levels for child loggers but DON'T add handlers
            # This ensures they log at INFO level but don't duplicate messages
            for module in ['autodoc.scanning', 'autodoc.scanning.artifact_scanner',
                          'autodoc.generation', 'autodoc.generation.planner',
                          'autodoc.generation.generator', 'autodoc.orchestrator']:
                _logger = logging.getLogger(module)
                _logger.setLevel(logging.INFO)
                # Don't add handler here - let propagation handle it

            _log(job, "Verbose logging enabled - detailed progress will be shown.")
        else:
            logging.basicConfig(
                level=logging.WARNING,
                format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                handlers=[logging.StreamHandler()],
                force=True
            )
            _log(job, "Standard logging mode.")

        settings = Settings()
        if request.provider:
            settings.llm_provider = request.provider
        if request.model:
            settings.llm_model = request.model
        if request.max_files is not None:
            settings.max_files = request.max_files
        if request.workers is not None:
            settings.parallel_workers = request.workers
        if request.planning_workers is not None:
            settings.planning_workers = request.planning_workers

        output_dir = (
            Path(request.output_dir)
            if request.output_dir
            else _get_default_output_dir()
        )
        if not output_dir.exists():
            output_dir.mkdir(parents=True, exist_ok=True)

        code_root = (
            Path(request.code_root)
            if request.code_root
            else _get_default_code_root()
        )
        if not code_root.exists():
            code_root = _get_default_code_root()

        job.output_dir = output_dir
        _log(job, f"Code root: {code_root}")
        _log(job, f"Output dir: {output_dir}")
        _log(job, f"Provider: {settings.llm_provider}")
        _log(job, f"Model: {settings.get_model_name()}")

        if request.spec_content:
            spec_path = output_dir / f"doc_spec.{uuid4().hex[:12]}.uploaded.yaml"
            spec_path.write_text(request.spec_content)
            job.spec_path = spec_path
            _log(job, f"Uploaded spec saved to: {spec_path}")
        else:
            raw_path = request.spec_path or str(_get_default_spec_path())
            # Resolve dataset:// references to actual mount paths
            if raw_path.startswith("dataset://"):
                parts = raw_path[len("dataset://"):].split("/", 1)
                raw_path = domino_datasets.build_spec_mount_path(parts[0], parts[1] if len(parts) > 1 else "")
            spec_path = Path(raw_path)

        if not spec_path.exists():
            raise FileNotFoundError(f"Spec not found: {spec_path}")

        # Pre-flight validation with user-friendly errors
        spec_content = spec_path.read_text(encoding="utf-8", errors="replace")
        spec_errors = DocumentSpec.validate_spec(spec_content)
        if spec_errors:
            raise ValueError("Spec validation failed:\n" + "\n".join(f"  - {e}" for e in spec_errors))

        doc_spec = DocumentSpec.from_yaml(str(spec_path))
        _log(job, f"Loaded spec: {doc_spec.title}")

        if request.api_key:
            _state.LAST_API_KEY = request.api_key
        api_key = _state.LAST_API_KEY or settings.get_api_key()
        base_url = request.base_url or settings.openai_base_url
        llm = LLMClient(
            provider=settings.llm_provider,
            model=settings.get_model_name(),
            api_key=api_key,
            base_url=base_url,
            max_retries=settings.llm_max_retries,
            initial_backoff=settings.llm_initial_backoff,
            max_backoff=settings.llm_max_backoff,
            backoff_jitter=settings.llm_backoff_jitter,
            timeout_seconds=request.timeout or 120.0,
        )
        sanitizer = ContentSanitizer()
        orchestrator = Orchestrator(
            llm=llm,
            sanitizer=sanitizer,
            code_root=code_root,
            output_dir=output_dir,
            parallel_workers=settings.parallel_workers,
            planning_workers=settings.planning_workers,
            max_files=settings.max_files,
            generate_notebook=request.notebook or bool(request.notebook_path),
            notebook_path=Path(request.notebook_path)
            if request.notebook_path
            else None,
            experiment_names=_parse_comma_list(request.experiment_names),
            model_names=_parse_comma_list(request.model_names),
            latest_only=request.latest_only,
        )

        # Create rich progress bar for terminal
        progress_ctx = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        )
        progress_ctx.start()

        # Track current phase task
        current_task_id = None
        current_phase_name = None

        def on_progress(phase: str, pct: float) -> None:
            nonlocal current_task_id, current_phase_name
            job.phase = phase
            job.progress = pct

            # Update terminal progress bar
            if current_phase_name != phase:
                # Complete previous task if exists
                if current_task_id is not None:
                    progress_ctx.update(current_task_id, completed=100)

                # Start new task for new phase
                current_phase_name = phase
                current_task_id = progress_ctx.add_task(f"{phase}", total=100)

            # Update progress
            if current_task_id is not None:
                progress_ctx.update(current_task_id, completed=int(pct * 100))

            # Only log phase changes, not every percentage update
            if pct == 0.0 or pct == 1.0:
                _log(job, f"{phase}: {'Started' if pct == 0.0 else 'Complete'}")

        _log(job, "Starting pipeline.")
        _log(job, "Beginning scan: code + MLflow artifacts.")
        console.print("\n[bold green]Starting documentation generation pipeline...[/bold green]\n")

        def on_status(message: str) -> None:
            _log(job, message)

        output_path = await orchestrator.generate(
            doc_spec,
            on_progress,
            on_status=on_status,
        )

        # Complete final task
        if current_task_id is not None:
            progress_ctx.update(current_task_id, completed=100)

        progress_ctx.stop()
        progress_ctx = None

        job.output_path = output_path
        if request.notebook or request.notebook_path:
            job.notebook_path = orchestrator.notebook_builder.notebook_path
        job.status = "completed"

        console.print("\n[bold green]Generation complete![/bold green]")
        _log(job, "Generation complete.")
        _log(job, "Use the download buttons above to save files to your machine.")
        if job.output_path:
            console.print(f"[cyan]Document:[/cyan] {job.output_path}")
            _log(job, f"Document: {job.output_path}")
        if job.notebook_path:
            console.print(f"[cyan]Notebook:[/cyan] {job.notebook_path}")
            _log(job, f"Notebook: {job.notebook_path}")
        console.print()

    except asyncio.CancelledError:
        if progress_ctx:
            progress_ctx.stop()
        job.status = "cancelled"
        console.print("\n[bold yellow]Stop requested. Cancelling run...[/bold yellow]")
        _log(job, "Stop requested. Cancelling run.")
        _cleanup_job(job)
        _log(job, "Cleanup complete.")
        raise
    except Exception as exc:
        if progress_ctx:
            progress_ctx.stop()
        job.status = "failed"
        job.error = str(exc)
        console.print(f"\n[bold red]Error:[/bold red] {exc}")
        _log(job, f"Error: {exc}")
        _log(job, traceback.format_exc())
    finally:
        # Clean up the log handler
        if log_handler:
            try:
                # Remove handler only from the root autodoc logger where we added it
                autodoc_logger = logging.getLogger('autodoc')
                autodoc_logger.removeHandler(log_handler)
                log_handler.close()
            except Exception:
                pass  # Don't let cleanup errors break anything
        # Clean up uploaded spec file (no-op if cancellation already cleared it)
        if getattr(job, 'spec_path', None) and job.spec_path.exists():
            try:
                job.spec_path.unlink()
            except Exception:
                pass
            job.spec_path = None


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

    # projectId: prefer form field, fall back to query param, then env var
    project_id = (
        form.get("target_project")
        or form.get("project_id")
        or req.query_params.get("projectId")
        or os.environ.get("DOMINO_PROJECT_ID")
        or None
    )

    return JobRequest(
        spec_path=form.get("spec_path") or None,
        spec_content=spec_content,
        provider=form.get("provider", "anthropic"),
        model=form.get("model") or None,
        api_key=form.get("api_key") or None,
        base_url=form.get("base_url") or None,
        code_root=form.get("code_root") or None,
        output_dir=form.get("output_dir") or None,
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
# Job start
# ---------------------------------------------------------------------------

def _start_job(job_request: JobRequest) -> JobState:
    import stitch.state as _state
    job = JobState(id=str(uuid4()))
    JOB_STORE[job.id] = job
    _state.ACTIVE_JOB_ID = job.id

    job.task = asyncio.create_task(_run_generation(job, job_request))
    return job


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
    if req.output_dir:
        command += ["--output", req.output_dir]
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

    Wraps the CLI command and appends a copy step to write results
    to /mnt/artifacts/auto_ml so they appear in the Domino job's
    Artifacts tab.
    """
    parts = _build_job_command(req, spec_path)
    cli_cmd = " ".join(parts)
    output_dir = req.output_dir or "/mnt/data"
    artifacts_dir = "/mnt/artifacts/auto_ml"
    return (
        f"{cli_cmd}"
        f" && mkdir -p {artifacts_dir}"
        f" && cp -r {output_dir}/* {artifacts_dir}/"
    )


# ---------------------------------------------------------------------------
# Domino job submission
# ---------------------------------------------------------------------------

async def _submit_domino_job(req: JobRequest, username: str) -> DominoJobRecord:
    """Submit or queue a Domino job and persist it to SQLite."""
    logger.info(
        "Submitting Domino job: project_id=%s, branch=%s, tier=%s",
        req.project_id, req.branch, req.hardware_tier,
    )
    if not _DOMINO_AVAILABLE:
        raise RuntimeError("Domino integration is not available.")

    # Ensure DB is initialised
    domino_job_store.init_db()

    # Resolve spec path
    spec_path: Optional[str] = None
    if req.spec_content and req.spec_filename:
        saved = spec_store.save_spec(req.spec_filename, req.spec_content)
        spec_path = str(saved)
    elif req.spec_path:
        # Resolve dataset:// references to actual mount paths
        if req.spec_path.startswith("dataset://"):
            spec_path = domino_datasets.build_spec_mount_path(
                *req.spec_path[len("dataset://"):].split("/", 1)
            )
        else:
            spec_path = req.spec_path

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

    # Submit immediately
    try:

        run_id = domino_client.submit_job(
            command=command_str,
            branch=req.branch,
            tier_id=req.hardware_tier or None,
            project_id=req.project_id,
        )
        job_url = domino_client.build_job_url(run_id, project_id=req.project_id)
        domino_job_store.update_job(
            job_id,
            domino_run_id=run_id,
            status="submitted",
            job_url=job_url,
            submitted_at=datetime.now(tz=timezone.utc).isoformat(),
        )
    except Exception as exc:
        domino_job_store.update_job(job_id, status="failed", domino_status=str(exc))

    row = domino_job_store.get_job(job_id)
    return _db_record_to_dataclass(row)


# ---------------------------------------------------------------------------
# Background polling
# ---------------------------------------------------------------------------

async def _poll_domino_jobs() -> None:
    """Background loop: poll Domino for active job statuses every 10 s."""
    while True:
        try:
            await asyncio.sleep(10)
            if not _DOMINO_AVAILABLE:
                continue

            domino_job_store.init_db()
            # Gather all active jobs (all users - we'll check per-user queues too)
            import sqlite3

            db_path_str = str(domino_job_store._db_path())
            con = sqlite3.connect(db_path_str, check_same_thread=False)
            con.row_factory = sqlite3.Row
            try:
                rows = con.execute(
                    "SELECT * FROM domino_jobs WHERE status IN ('submitted', 'running')"
                ).fetchall()
                active_jobs = [dict(r) for r in rows]

                # Also check for queued jobs whose user has a free slot
                queued_rows = con.execute(
                    "SELECT DISTINCT username FROM domino_jobs WHERE status = 'queued'"
                ).fetchall()
                queued_users = [r["username"] for r in queued_rows]
                con.commit()
            finally:
                con.close()

            terminal_statuses = {"succeeded", "failed", "cancelled"}

            for job in active_jobs:
                if not job.get("domino_run_id"):
                    continue
                try:
                    status_info = domino_client.get_job_status(job["domino_run_id"])
                    local_status = status_info["local_status"]
                    domino_status = status_info["domino_status"]

                    update_fields: dict[str, Any] = {
                        "status": local_status,
                        "domino_status": domino_status,
                    }
                    if local_status in terminal_statuses:
                        update_fields["completed_at"] = datetime.now(
                            tz=timezone.utc
                        ).isoformat()
                    domino_job_store.update_job(job["id"], **update_fields)
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "Polling error for job %s: %s", job["id"], exc
                    )

            # Promote queued jobs
            for username in queued_users:
                active_count = domino_job_store.count_active_jobs(username)
                if active_count < _max_jobs():
                    oldest = domino_job_store.get_oldest_queued_job(username)
                    if oldest:
                        try:
                            stored_cmd = oldest.get("command") or ""
                            if not stored_cmd:
                                # Fallback for rows created before command column existed
                                sp = oldest.get("spec_path")
                                stored_cmd = "python /mnt/code/auto_model_docs/main.py"
                                if sp:
                                    stored_cmd += f" --spec {sp}"
                            run_id = domino_client.submit_job(
                                command=stored_cmd,
                                branch=oldest.get("branch"),
                                tier_id=oldest.get("hardware_tier"),
                                project_id=oldest.get("project_id"),
                            )
                            job_url = domino_client.build_job_url(run_id, project_id=oldest.get("project_id"))
                            domino_job_store.update_job(
                                oldest["id"],
                                domino_run_id=run_id,
                                status="submitted",
                                job_url=job_url,
                                submitted_at=datetime.now(tz=timezone.utc).isoformat(),
                            )
                        except Exception as exc:
                            domino_job_store.update_job(
                                oldest["id"], status="failed", domino_status=str(exc)
                            )

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logging.getLogger(__name__).warning("Poll loop error: %s", exc)


# ---------------------------------------------------------------------------
# Stale job reconciliation (startup)
# ---------------------------------------------------------------------------

def _reconcile_stale_jobs() -> None:
    """On startup, sync any jobs stuck in active states with Domino's actual status."""
    import sqlite3
    _logger = logging.getLogger(__name__)
    try:
        domino_job_store.init_db()
        db_path = domino_job_store._db_path()
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM domino_jobs WHERE status IN ('queued', 'submitted', 'running')"
        ).fetchall()
        stale_jobs = [dict(r) for r in rows]
        con.close()

        for job in stale_jobs:
            run_id = job.get("domino_run_id")
            if not run_id:
                # Queued but never submitted — mark as failed so it doesn't block
                _logger.info("Marking stale queued job %s as failed (no Domino run ID)", job["id"])
                domino_job_store.update_job(job["id"], status="failed", domino_status="Stale: never submitted")
                continue
            try:
                status_info = domino_client.get_job_status(run_id)
                local_status = status_info["local_status"]
                domino_status = status_info["domino_status"]
                update_fields: dict[str, Any] = {
                    "status": local_status,
                    "domino_status": domino_status,
                }
                if local_status in ("succeeded", "failed", "cancelled"):
                    update_fields["completed_at"] = datetime.now(tz=timezone.utc).isoformat()
                domino_job_store.update_job(job["id"], **update_fields)
                _logger.info("Reconciled job %s (run %s): %s", job["id"], run_id, local_status)
            except Exception as exc:
                _logger.warning("Failed to reconcile job %s (run %s): %s", job["id"], run_id, exc)
                domino_job_store.update_job(job["id"], status="failed", domino_status=f"Reconcile error: {exc}")
    except Exception as exc:
        _logger.warning("Startup job reconciliation failed: %s", exc)
