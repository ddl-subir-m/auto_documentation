#!/usr/bin/env python3
"""FastHTML UI for Auto Model Documentation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from fasthtml.common import *
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TaskProgressColumn, TimeElapsedColumn
from starlette.requests import Request
from starlette.responses import FileResponse, Response

from autodoc.core.config import Settings
from autodoc.core.models import DocumentSpec
from autodoc.llm import LLMClient
from autodoc.orchestrator import Orchestrator
from autodoc.scanning import ContentSanitizer

# Import sibling modules by absolute file path so it works regardless of
# sys.path, PYTHONPATH, or how Domino launches the script.
import importlib.util as _imputil

def _import_sibling(name: str):
    """Import a .py file from the same directory as this script."""
    path = Path(__file__).resolve().parent / f"{name}.py"
    spec = _imputil.spec_from_file_location(name, path)
    mod = _imputil.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# Preload conda's libstdc++ to fix CXXABI version mismatch in Domino Apps.
# The App environment's system libstdc++ may lack CXXABI_1.3.15 needed by
# conda-built C extensions (e.g. sqlite3 via libicui18n).
try:
    import ctypes as _ctypes
    for _candidate in (
        os.path.join(os.environ.get("CONDA_PREFIX", "/opt/conda"), "lib", "libstdc++.so.6"),
        "/opt/conda/lib/libstdc++.so.6",
    ):
        if os.path.isfile(_candidate):
            try:
                _ctypes.CDLL(_candidate, mode=_ctypes.RTLD_GLOBAL)
                break
            except OSError:
                continue
except Exception:
    pass

try:
    domino_client = _import_sibling("domino_client")
    domino_job_store = _import_sibling("domino_job_store")
    spec_store = _import_sibling("spec_store")
    _DOMINO_AVAILABLE = True
except Exception:
    _DOMINO_AVAILABLE = False

# Rich console for terminal output
console = Console()


class JobLogHandler(logging.Handler):
    """Custom logging handler that captures log messages to job.logs."""
    
    def __init__(self, job: 'JobState', include_level: bool = False):
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
            import re
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
                    msg = f"⚠ {msg}"
                elif level_name == "ERROR":
                    msg = f"✗ {msg}"
                elif level_name == "INFO":
                    # Don't prepend anything for INFO to keep it clean
                    pass
            
            # Add to job logs with timestamp
            _log(self.job, msg)
            
        except Exception:
            # Don't let logging errors break the application
            pass


@dataclass
class JobState:
    id: str
    status: str = "idle"
    phase: str = "Idle"
    progress: float = 0.0
    logs: list[str] = field(default_factory=list)
    output_path: Optional[Path] = None
    notebook_path: Optional[Path] = None
    output_dir: Optional[Path] = None
    spec_path: Optional[Path] = None
    error: Optional[str] = None
    cancel_requested: bool = False
    task: Optional[asyncio.Task] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    # Progress bar tracking
    progress_ctx: Optional[Progress] = None
    progress_task_id: Optional[int] = None
    current_phase: Optional[str] = None
    log_version: int = 0


@dataclass
class JobRequest:
    spec_path: Optional[str]
    spec_content: Optional[str]
    provider: str
    model: Optional[str]
    api_key: Optional[str]
    base_url: Optional[str]
    code_root: Optional[str]
    output_dir: Optional[str]
    max_files: Optional[int]
    workers: Optional[int]
    planning_workers: Optional[int]
    timeout: Optional[float]
    notebook: bool
    notebook_path: Optional[str]
    experiment_names: Optional[str]  # Comma-separated list
    model_names: Optional[str]  # Comma-separated list
    latest_only: bool
    verbose: bool  # Enable verbose logging
    # Domino job fields
    execution_mode: str = "domino"      # "app" | "domino"
    branch: Optional[str] = None
    hardware_tier: Optional[str] = None
    api_key_source: str = "domino_env"  # "domino_env" | "pass_now"
    spec_filename: Optional[str] = None  # original uploaded filename


@dataclass
class DominoJobRecord:
    id: str                              # local UUID
    username: str
    domino_run_id: Optional[str] = None
    branch: Optional[str] = None
    hardware_tier: Optional[str] = None
    status: str = "queued"               # queued | submitted | running | succeeded | failed | cancelled
    domino_status: Optional[str] = None
    job_url: Optional[str] = None
    spec_path: Optional[str] = None
    submitted_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None


JOB_STORE: dict[str, JobState] = {}
ACTIVE_JOB_ID: Optional[str] = None
LAST_API_KEY: Optional[str] = None
_POLL_TASK: Optional[asyncio.Task] = None


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _log(job: JobState, message: str) -> None:
    job.logs.append(f"[{_timestamp()}] {message}")
    job.updated_at = datetime.utcnow()
    job.log_version += 1


def _cleanup_job(job: JobState) -> None:
    _log(job, "Cleaning up artifacts.")
    paths: list[Path] = []
    if job.output_path:
        paths.append(job.output_path)
    if job.notebook_path:
        paths.append(job.notebook_path)
    if job.spec_path:
        paths.append(job.spec_path)

    for path in paths:
        try:
            if path.exists():
                path.unlink()
                _log(job, f"Removed: {path}")
        except Exception as exc:
            _log(job, f"Cleanup failed for {path}: {exc}")

    job.output_path = None
    job.notebook_path = None
    job.spec_path = None


def _get_default_output_dir() -> Path:
    # In Domino, use /mnt/data/{project_name} (persisted via Datasets)
    if Path("/mnt/data").exists():
        project_name = os.environ.get("DOMINO_PROJECT_NAME", "output")
        output = Path(f"/mnt/data/{project_name}")
        output.mkdir(parents=True, exist_ok=True)
        return output
    # Fallback for local development
    output = Path("./output")
    output.mkdir(exist_ok=True)
    return output


def _get_default_code_root() -> Path:
    if Path("/mnt/code").exists():
        return Path("/mnt/code")
    return Path(".")


def _get_default_spec_path() -> Path:
    return Path(__file__).resolve().parent / "doc_spec.yaml"


def _sanitize_optional_int(value: Optional[str]) -> Optional[int]:
    if value is None or value == "":
        return None
    return int(value)


def _sanitize_optional_float(value: Optional[str]) -> Optional[float]:
    if value is None or value == "":
        return None
    return float(value)


def _parse_comma_list(value: Optional[str]) -> Optional[list[str]]:
    """Parse a comma-separated string into a list of trimmed strings."""
    if not value:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items if items else None


def _resolve_job(job_id: Optional[str]) -> Optional[JobState]:
    if job_id and job_id in JOB_STORE:
        return JOB_STORE[job_id]
    return None


def _field_id(name: str) -> str:
    return f"field-{name}"


def _labeled_input(label_text: str, name: str, **kwargs: str) -> FT:
    return Div(
        Label(label_text, for_=_field_id(name)),
        Input(name=name, id=_field_id(name), **kwargs),
        cls="field",
    )


def _labeled_select(label_text: str, name: str, *options: FT) -> FT:
    return Div(
        Label(label_text, for_=_field_id(name)),
        Select(*options, name=name, id=_field_id(name)),
        cls="field",
    )


def _checkbox_field(label_text: str, name: str) -> FT:
    return Div(
        Label(
            Input(type="checkbox", name=name, id=_field_id(name)),
            Span(label_text),
            cls="checkbox",
        ),
        cls="field",
    )


def _render_progress_bar(job: JobState) -> FT:
    """Render a visual progress bar for the current phase."""
    phases = ["Scanning", "Planning", "Generating", "Building"]
    current_phase = job.phase
    progress_pct = int(job.progress * 100)
    
    phase_items = []
    for phase in phases:
        if phase == current_phase:
            # Current phase - show progress bar
            phase_items.append(
                Div(
                    Div(
                        Span(phase, cls="phase-name"),
                        Span(f"{progress_pct}%", cls="phase-pct"),
                        cls="phase-header",
                    ),
                    Div(
                        Div(cls="phase-bar-fill", style=f"width: {progress_pct}%"),
                        cls="phase-bar",
                    ),
                    cls="phase-item phase-active",
                )
            )
        elif phases.index(phase) < phases.index(current_phase) if current_phase in phases else False:
            # Completed phase
            phase_items.append(
                Div(
                    Div(
                        Span(phase, cls="phase-name"),
                        Span("✓", cls="phase-check"),
                        cls="phase-header",
                    ),
                    Div(
                        Div(cls="phase-bar-fill", style="width: 100%"),
                        cls="phase-bar phase-bar-complete",
                    ),
                    cls="phase-item phase-complete",
                )
            )
        else:
            # Pending phase
            phase_items.append(
                Div(
                    Div(
                        Span(phase, cls="phase-name"),
                        cls="phase-header",
                    ),
                    Div(cls="phase-bar"),
                    cls="phase-item phase-pending",
                )
            )
    
    return Div(*phase_items, cls="progress-phases")


def _render_status(job: Optional[JobState]) -> FT:
    if not job:
        return Div(
            Div(
                H3("Logs"),
                Div(
                    A("Stop", href="#", cls="terminal-action terminal-action-disabled"),
                    A("Clear", href="#", cls="terminal-action terminal-action-disabled"),
                    cls="terminal-actions",
                ),
                cls="terminal-header",
            ),
            Div("Click Generate Documentation to generate your first document.", cls="terminal terminal-idle"),
            cls="terminal-card",
            data_job_status="idle",
            data_log_version="0",
        )

    # Show more logs when verbose mode is on (last 500 lines vs 200)
    log_limit = 500 if len(job.logs) > 200 else 200
    log_text = "\n".join(job.logs[-log_limit:]) if job.logs else "Initializing..."
    status_text = job.status.upper()
    if job.status == "completed":
        status_text = "COMPLETED"
    elif job.status == "failed":
        status_text = "FAILED"
    elif job.status == "cancelled":
        status_text = "CANCELLED"

    is_running = job.status == "running"
    is_terminal = job.status in ("completed", "failed", "cancelled")
    if is_running:
        stop_link = A(
            "Stop",
            hx_post="stop",
            hx_target="#status-panel",
            hx_swap="innerHTML",
            cls="terminal-action",
        )
    elif is_terminal:
        stop_link = None
    else:
        stop_link = A(
            "Stop",
            href="#",
            cls="terminal-action terminal-action-disabled",
        )

    clear_link = A(
        "Clear",
        hx_post="clear-terminal",
        hx_target="#status-panel",
        hx_swap="innerHTML",
        cls="terminal-action" if not is_running else "terminal-action terminal-action-disabled",
    )

    # Build the progress section
    progress_section = []
    if is_running:
        progress_section.append(_render_progress_bar(job))
    
    # Build download links if job completed
    download_section = []
    if job.status == "completed":
        download_links = []
        if job.output_path and job.output_path.exists():
            download_links.append(
                A(
                    "Download Document (.docx)",
                    href=f"download/{job.id}/docx",
                    cls="download-btn",
                    download=True,
                )
            )
        if job.notebook_path and job.notebook_path.exists():
            download_links.append(
                A(
                    "Download Notebook (.ipynb)",
                    href=f"download/{job.id}/notebook",
                    cls="download-btn download-btn-secondary",
                    download=True,
                )
            )
        if download_links:
            download_section.append(
                Div(*download_links, cls="download-section")
            )
    
    return Div(
        Div(
            H3("Logs"),
            Div(
                stop_link,
                clear_link,
                cls="terminal-actions",
            ),
            cls="terminal-header",
        ),
        Div(status_text, cls=f"terminal-status terminal-status-{job.status}"),
        *progress_section,
        *download_section,
        Pre(log_text, cls="terminal"),
        cls="terminal-card",
        data_job_status=job.status,
        data_log_version=str(job.log_version),
    )




async def _run_generation(job: JobState, request: JobRequest) -> None:
    progress_ctx = None
    log_handler = None
    try:
        global LAST_API_KEY
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
                logger = logging.getLogger(module)
                logger.setLevel(logging.INFO)
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
            # else settings.output_dir
            else _get_default_output_dir()
        )
        if not output_dir.exists():
            output_dir.mkdir(parents=True, exist_ok=True)

        code_root = (
            Path(request.code_root)
            if request.code_root
            # else settings.code_root
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
            spec_path = Path(request.spec_path or _get_default_spec_path())

        if not spec_path.exists():
            raise FileNotFoundError(f"Spec not found: {spec_path}")

        doc_spec = DocumentSpec.from_yaml(str(spec_path))
        _log(job, f"Loaded spec: {doc_spec.title}")

        if request.api_key:
            LAST_API_KEY = request.api_key
        api_key = LAST_API_KEY or settings.get_api_key()
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


async def _parse_request(req: Request) -> JobRequest:
    form = await req.form()
    spec_upload = form.get("spec_upload")
    spec_content = None
    spec_filename = None
    if spec_upload and hasattr(spec_upload, "read"):
        content = await spec_upload.read()
        spec_content = content.decode("utf-8", errors="replace")
        spec_filename = getattr(spec_upload, "filename", None)

    execution_mode = form.get("execution_mode", "domino")

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
        execution_mode=execution_mode,
        branch=form.get("branch") or None,
        hardware_tier=form.get("hardware_tier") or None,
        api_key_source=form.get("api_key_source", "domino_env"),
        spec_filename=spec_filename,
    )


def _start_job(job_request: JobRequest) -> JobState:
    global ACTIVE_JOB_ID
    job = JobState(id=str(uuid4()))
    JOB_STORE[job.id] = job
    ACTIVE_JOB_ID = job.id

    job.task = asyncio.create_task(_run_generation(job, job_request))
    return job


def _get_username() -> str:
    return os.environ.get("DOMINO_STARTING_USERNAME", "local_user")


def _max_jobs() -> int:
    return int(os.environ.get("AUTODOC_MAX_JOBS", "1"))


def _db_record_to_dataclass(row: dict) -> DominoJobRecord:
    return DominoJobRecord(
        id=row["id"],
        username=row["username"],
        domino_run_id=row.get("domino_run_id"),
        branch=row.get("branch"),
        hardware_tier=row.get("hardware_tier"),
        status=row.get("status", "queued"),
        domino_status=row.get("domino_status"),
        job_url=row.get("job_url"),
        spec_path=row.get("spec_path"),
        submitted_at=row.get("submitted_at"),
        completed_at=row.get("completed_at"),
    )


def _render_domino_status(record: Optional[DominoJobRecord]) -> FT:
    """Render the terminal panel for a Domino job."""
    if not record:
        return Div(
            Div(
                H3("Domino job"),
                cls="terminal-header",
            ),
            Div(
                "Submit in Domino Job mode to offload compute to a dedicated job container.",
                cls="terminal terminal-idle",
            ),
            cls="terminal-card",
            data_job_status="idle",
        )

    status = record.status
    badge_cls = f"terminal-status terminal-status-{status}"

    # Stop button
    stop_btn = None
    if status in ("queued", "submitted", "running"):
        stop_btn = A(
            "Stop",
            hx_post="stop-domino",
            hx_vals=f'{{"job_id": "{record.id}"}}',
            hx_target="#status-panel",
            hx_swap="innerHTML",
            cls="terminal-action",
        )

    # Job link
    job_link = None
    if record.job_url:
        job_link = A(
            "View job in Domino →",
            href=record.job_url,
            target="_blank",
            cls="domino-job-link",
        )

    # Status message
    status_lines = []
    if record.submitted_at:
        status_lines.append(f"Submitted: {record.submitted_at[:19].replace('T', ' ')} UTC")
    if record.domino_status:
        status_lines.append(f"Domino status: {record.domino_status}")
    if record.completed_at:
        status_lines.append(f"Completed: {record.completed_at[:19].replace('T', ' ')} UTC")
    if not status_lines:
        status_lines.append("Waiting for status...")

    status_text = "\n".join(status_lines)

    return Div(
        Div(
            H3("Domino job"),
            Div(
                stop_btn,
                cls="terminal-actions",
            ) if stop_btn else Div(cls="terminal-actions"),
            cls="terminal-header",
        ),
        Div(status.upper(), cls=badge_cls),
        Div(job_link, cls="domino-job-link-row") if job_link else None,
        Pre(status_text, cls="terminal"),
        id="domino-status-inner",
        cls="terminal-card",
        data_job_status=status,
    )


def _render_job_history_table(username: str) -> FT:
    """Render the job history table for a user."""
    if not _DOMINO_AVAILABLE:
        return Div()
    jobs = domino_job_store.get_user_jobs(username, limit=50)
    if not jobs:
        return Div(
            P("No jobs submitted yet.", cls="history-empty"),
            cls="job-history-content",
        )

    rows = []
    for j in jobs:
        status_cls = f"history-status history-status-{j.get('status', 'queued')}"
        job_url = j.get("job_url")
        link_cell = Td(
            A("View →", href=job_url, target="_blank") if job_url else "—"
        )
        rows.append(
            Tr(
                Td(j.get("branch") or "—"),
                Td(j.get("hardware_tier") or "—"),
                Td(Span(j.get("status", "—").upper(), cls=status_cls)),
                Td((j.get("submitted_at") or "—")[:16].replace("T", " ")),
                link_cell,
            )
        )

    return Div(
        Table(
            Thead(
                Tr(
                    Th("Branch"),
                    Th("Hardware tier"),
                    Th("Status"),
                    Th("Submitted"),
                    Th("Link"),
                )
            ),
            Tbody(*rows),
            cls="history-table",
        ),
        Div(
            A(
                "Clear completed",
                hx_post="clear-job-history",
                hx_target="#job-history-content",
                hx_swap="innerHTML",
                cls="terminal-action",
            ),
            cls="history-actions",
        ),
        id="job-history-content",
        cls="job-history-content",
    )


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
    import shlex

    parts = _build_job_command(req, spec_path)
    cli_cmd = " ".join(shlex.quote(p) for p in parts)
    output_dir = shlex.quote(req.output_dir or "/mnt/data")
    artifacts_dir = shlex.quote("/mnt/artifacts/auto_ml")
    return (
        f"{cli_cmd}"
        f" && mkdir -p {artifacts_dir}"
        f" && cp -r {output_dir}/* {artifacts_dir}/"
    )


async def _submit_domino_job(req: JobRequest, username: str) -> DominoJobRecord:
    """Submit or queue a Domino job and persist it to SQLite."""
    if not _DOMINO_AVAILABLE:
        raise RuntimeError("Domino integration is not available.")

    # Ensure DB is initialised
    domino_job_store.init_db()

    # Save spec file if uploaded in Domino mode
    spec_path: Optional[str] = None
    if req.spec_content and req.spec_filename:
        saved = spec_store.save_spec(req.spec_filename, req.spec_content)
        spec_path = str(saved)
    elif req.spec_path:
        spec_path = req.spec_path

    # Build command and create the DB row (status=queued)
    command_str = _build_job_command_str(req, spec_path)

    job_id = domino_job_store.create_job(
        username=username,
        branch=req.branch,
        tier=req.hardware_tier,
        spec_path=spec_path,
        command=command_str,
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
        )
        job_url = domino_client.build_job_url(run_id)
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
            from pathlib import Path

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
                            )
                            job_url = domino_client.build_job_url(run_id)
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


app, rt = fast_app(
    # Disable default CDN headers and use permissive settings for Domino
    pico=False,  # Disable pico CSS CDN if causing issues
    hdrs=(
        # Load Inter font for Domino design system
        Link(rel="stylesheet", href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"),
        # Load htmx synchronously to ensure it's ready before user interaction
        Script(src="https://unpkg.com/htmx.org@1.9.10"),
        # Smart polling: only fetch full status HTML when log version changes
        Script(r"""
            window.addEventListener('DOMContentLoaded', function() {
                var htmxWorking = false;
                if (typeof htmx !== 'undefined' && typeof htmx.ajax === 'function') {
                    htmxWorking = true;
                }

                // Track last-known version so we only swap when something changed
                var _lastLogVersion = -1;
                var _pollActive = true;
                var TERMINAL_STATES = ['idle', 'completed', 'failed', 'cancelled', 'succeeded'];

                function getCurrentVersion() {
                    var card = document.querySelector('#status-panel [data-log-version]');
                    return card ? parseInt(card.dataset.logVersion, 10) : -1;
                }

                // Full fetch — replaces status panel HTML
                function fetchFullStatus() {
                    var panel = document.getElementById('status-panel');
                    if (!panel) return Promise.resolve();
                    return fetch('status')
                        .then(function(r) { return r.text(); })
                        .then(function(html) {
                            panel.innerHTML = html;
                            _lastLogVersion = getCurrentVersion();
                            // Fire the same event htmx would so styling hooks run
                            document.body.dispatchEvent(new CustomEvent('statusUpdated'));
                        })
                        .catch(function() {});
                }

                // Lightweight check — only fetches full HTML when version changed
                function smartPoll() {
                    if (!_pollActive) return;
                    fetch('status-check')
                        .then(function(r) { return r.json(); })
                        .then(function(data) {
                            var serverVersion = data.logVersion || 0;
                            if (serverVersion !== _lastLogVersion) {
                                fetchFullStatus();
                            }
                            // Stop polling when job reaches a terminal state
                            if (TERMINAL_STATES.indexOf(data.status) !== -1 && serverVersion === _lastLogVersion) {
                                _pollActive = false;
                            }
                        })
                        .catch(function() {});
                }

                // Initialise version from DOM
                _lastLogVersion = getCurrentVersion();

                // Start smart polling for app mode (Domino mode uses HTMX polling)
                var isDominoMode = document.querySelector('input[name="execution_mode"][value="domino"]:checked');
                if (!isDominoMode) {
                    setInterval(smartPoll, 2000);
                }

                // Re-activate polling when a new job starts (after form submit)
                window._activateStatusPolling = function() {
                    _pollActive = true;
                    _lastLogVersion = -1; // Force an immediate update
                };

                // Direct click handler on Generate button
                var generateBtn = document.getElementById('generate-btn');
                if (generateBtn) {
                    generateBtn.addEventListener('click', function(e) {
                        if (htmxWorking) return;
                        e.preventDefault();
                        e.stopPropagation();
                        var form = document.querySelector('form');
                        if (!form) return;
                        var formData = new FormData(form);
                        generateBtn.disabled = true;
                        generateBtn.textContent = 'Starting...';
                        fetch('run', { method: 'POST', body: formData })
                        .then(function(r) { return r.text(); })
                        .then(function(html) {
                            var panel = document.getElementById('status-panel');
                            if (panel) panel.innerHTML = html;
                            generateBtn.disabled = false;
                            generateBtn.textContent = 'Generate Documentation';
                            window._activateStatusPolling();
                        })
                        .catch(function() {
                            generateBtn.disabled = false;
                            generateBtn.textContent = 'Generate Documentation';
                        });
                    });
                }

                // Form submit backup
                var form = document.querySelector('form');
                if (form && !htmxWorking) {
                    form.addEventListener('submit', function(e) {
                        e.preventDefault();
                        var btn = document.getElementById('generate-btn');
                        if (btn) btn.click();
                    });
                }

                // Stop and Clear button delegation
                document.addEventListener('click', function(e) {
                    var target = e.target;
                    if (target.textContent === 'Stop' && !target.classList.contains('terminal-action-disabled')) {
                        if (htmxWorking) return;
                        e.preventDefault();
                        fetch('stop', { method: 'POST' })
                            .then(function(r) { return r.text(); })
                            .then(function(html) {
                                var panel = document.getElementById('status-panel');
                                if (panel) panel.innerHTML = html;
                                _lastLogVersion = getCurrentVersion();
                            });
                    }
                    if (target.textContent === 'Clear' && !target.classList.contains('terminal-action-disabled')) {
                        if (htmxWorking) return;
                        e.preventDefault();
                        fetch('clear-terminal', { method: 'POST' })
                            .then(function(r) { return r.text(); })
                            .then(function(html) {
                                var panel = document.getElementById('status-panel');
                                if (panel) panel.innerHTML = html;
                                _lastLogVersion = getCurrentVersion();
                            });
                    }
                });
            });
        """),
        Style(
            """
            :root {
                /* Backgrounds */
                --bg-page: #FAFAFA;
                --panel: #FFFFFF;
                --panel-border: #E0E0E0;
                --terminal: #1E1E1E;  /* Keep dark for terminal output */

                /* Accent (Domino Purple) */
                --accent: #543FDE;
                --accent-hover: #3B23D1;
                --accent-active: #311EAE;
                --accent-glow: rgba(84, 63, 222, 0.08);

                /* Text */
                --text-primary: #2E2E38;
                --text-secondary: #65657B;
                --text-muted: #8F8FA3;

                /* Status Colors */
                --success: #28A464;
                --warning: #CCB718;
                --error: #C20A29;
                --info: #0070CC;

                /* Domino Header */
                --header-bg: #2E2E38;
            }
            html, body {
                margin: 0;
                padding: 0;
                min-height: 100%;
                background: var(--bg-page);
            }
            body {
                color: var(--text-primary);
                font-family: Inter, Lato, 'Helvetica Neue', Helvetica, Arial, sans-serif;
            }
            h1, h2, h3, h4 { color: var(--text-primary); margin: 0; }
            a { color: var(--accent); text-decoration: none; transition: color 0.2s ease; }
            a:hover { color: var(--accent-hover); }

            /* Domino Header - full width, Domino style */
            .domino-header {
                background: var(--header-bg);
                width: 100%;
                min-height: 48px;
                display: flex;
                align-items: center;
                padding: 0 1.5rem;
                box-sizing: border-box;
            }
            .domino-header-inner {
                max-width: 1500px;
                margin: 0 auto;
                width: 100%;
                display: flex;
                align-items: center;
            }
            .domino-header-title {
                color: #FFFFFF;
                font-size: 1rem;
                font-weight: 600;
                margin: 0;
                letter-spacing: -0.01em;
            }

            /* Page Layout — fill viewport below header */
            .page {
                max-width: 1500px;
                margin: 0 auto;
                padding: 1rem 2rem 2rem;
                box-sizing: border-box;
                width: 100%;
                display: flex;
                flex-direction: column;
                min-height: calc(100vh - 48px); /* 48px = header height */
            }
            .hero {
                text-align: left;
                padding: 0.25rem 0 0.75rem 0;
            }
            .hero .hero-tagline {
                font-size: 1.125rem;
                font-weight: 400;
                color: var(--text-secondary);
                margin: 0;
                line-height: 1.45;
            }

            /* Three cards in a row - responsive horizontal layout */
            .config-grid {
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                align-items: stretch;
                gap: 1rem;
                margin-bottom: 0;
            }
            @media (max-width: 1100px) {
                .config-grid {
                    grid-template-columns: repeat(2, 1fr);
                }
            }
            @media (max-width: 700px) {
                .config-grid {
                    grid-template-columns: 1fr;
                }
            }

            /* Cards */
            .config-grid .card {
                min-width: 0; /* allow shrinking so content fits viewport */
                display: flex;
                flex-direction: column;
            }
            .card {
                background: var(--panel);
                border: 1px solid var(--panel-border);
                border-radius: 8px;
                padding: 1.25rem;
                box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
                transition: border-color 0.2s ease, transform 0.2s ease;
            }
            .card-title {
                font-size: 0.875rem;
                font-weight: 600;
                color: var(--text-secondary);
                margin-bottom: 1rem;
            }
            .card-title-sub {
                font-size: 0.75rem;
                font-weight: 400;
                color: var(--text-muted);
                margin-left: 0.35rem;
            }
            .card-advanced .card-title {
                font-size: 0.8125rem;
                color: var(--text-muted);
            }
            .card-advanced .field label,
            .card-advanced .filter-section-title {
                font-size: 0.75rem;
                color: var(--text-muted);
            }
            
            /* Form Fields */
            .field {
                display: flex;
                flex-direction: column;
                gap: 0.35rem;
                margin-bottom: 1rem;
            }
            .field:last-child {
                margin-bottom: 0;
            }
            .field label {
                color: var(--text-secondary);
                font-size: 0.8rem;
                font-weight: 500;
            }
            .field input[type="text"],
            .field input[type="number"],
            .field select {
                background: var(--panel);
                border: 1px solid var(--panel-border);
                border-radius: 4px;
                padding: 0.625rem 0.75rem;
                color: var(--text-primary);
                font-size: 0.875rem;
                transition: border-color 0.2s ease, box-shadow 0.2s ease;
                min-width: 0; /* shrink inside grid/flex so layout stays responsive */
            }
            .field input:focus,
            .field select:focus {
                outline: none;
                border-color: var(--accent);
                box-shadow: 0 0 0 3px var(--accent-glow);
            }
            .field input::placeholder {
                color: var(--text-muted);
            }
            .field select {
                cursor: pointer;
            }
            
            /* Code root prefix-dropdown + path combo */
            .code-root-wrap {
                display: flex;
                border: 1px solid var(--panel-border);
                border-radius: 4px;
                overflow: hidden;
                background: var(--panel);
                transition: border-color 0.2s ease, box-shadow 0.2s ease;
            }
            .code-root-wrap:focus-within {
                border-color: var(--accent);
                box-shadow: 0 0 0 3px var(--accent-glow);
            }
            .code-root-prefix {
                padding: 0.625rem 0.75rem;
                background: var(--bg-page);
                border: none;
                border-right: 1px solid var(--panel-border);
                font-size: 0.875rem;
                color: var(--text-secondary);
                font-family: inherit;
                white-space: nowrap;
                user-select: none;
            }
            .code-root-suffix {
                flex: 1;
                border: none;
                padding: 0.625rem 0.75rem;
                font-size: 0.875rem;
                color: var(--text-primary);
                background: transparent;
                outline: none;
                min-width: 0;
            }
            .code-root-suffix::placeholder { color: var(--text-muted); }

            /* Inline field with upload button */
            .field-inline {
                display: flex;
                gap: 0.5rem;
                align-items: stretch;
            }
            .field-inline input[type="text"] {
                flex: 1;
                min-width: 0;
            }
            .upload-btn {
                background: var(--bg-page);
                border: 1px solid var(--panel-border);
                border-radius: 4px;
                padding: 0 0.875rem;
                color: var(--text-secondary);
                font-size: 0.8rem;
                font-weight: 500;
                cursor: pointer;
                transition: all 0.2s ease;
                display: flex;
                align-items: center;
                gap: 0.35rem;
            }
            .upload-btn:hover {
                background: var(--panel);
                border-color: var(--accent);
                color: var(--text-primary);
            }
            .hidden-upload {
                display: none;
            }
            .upload-filename {
                font-size: 0.75rem;
                color: var(--accent);
                margin-top: 0.25rem;
            }
            
            /* Checkbox */
            .checkbox-field {
                display: flex;
                align-items: center;
                gap: 0.625rem;
                margin-bottom: 1rem;
                cursor: pointer;
            }
            .checkbox-field input[type="checkbox"] {
                width: 1rem;
                height: 1rem;
                accent-color: var(--accent);
                cursor: pointer;
            }
            .checkbox-field span {
                color: var(--text-primary);
                font-size: 0.875rem;
                font-weight: 500;
            }
            .field-hint {
                margin-top: -0.5rem;
                margin-bottom: 1rem;
                padding-left: 1.625rem;
            }
            /* Advanced Section (Collapsible) */
            .advanced-section {
                margin-top: 0.5rem;
                border-top: 1px solid var(--panel-border);
                padding-top: 0.75rem;
            }
            .advanced-section summary {
                font-size: 0.8rem;
                font-weight: 500;
                color: var(--text-muted);
                cursor: pointer;
                padding: 0.5rem 0;
                list-style: none;
                display: flex;
                align-items: center;
                gap: 0.5rem;
                transition: color 0.2s ease;
            }
            .advanced-section summary::-webkit-details-marker {
                display: none;
            }
            .advanced-section summary::before {
                content: '▶';
                font-size: 0.6rem;
                transition: transform 0.2s ease;
            }
            .advanced-section[open] summary::before {
                transform: rotate(90deg);
            }
            .advanced-section summary:hover {
                color: var(--text-primary);
            }
            .advanced-content {
                padding-top: 0.75rem;
            }
            .advanced-grid {
                display: grid;
                grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
                gap: 0.75rem;
            }
            .advanced-grid .field {
                margin-bottom: 0;
                min-width: 0;
            }
            .advanced-grid .field input[type="number"] {
                width: 100%;
                max-width: 100%;
                box-sizing: border-box;
            }
            
            /* Filtering Section */
            .filter-section {
                margin-top: 1rem;
                padding-top: 1rem;
                border-top: 1px solid var(--panel-border);
            }
            .filter-section-title {
                font-size: 0.875rem;
                font-weight: 600;
                color: var(--text-muted);
                margin-bottom: 0.75rem;
            }
            .filter-section .field {
                margin-bottom: 0.75rem;
            }
            .filter-section .checkbox-field {
                margin-top: 0.5rem;
            }
            .filter-section .checkbox-field span {
                font-size: 0.8rem;
                color: var(--text-secondary);
            }
            .required-star {
                color: var(--error);
                font-weight: 600;
            }
            .field-hint-text {
                display: block;
                font-size: 0.75rem;
                color: var(--text-muted);
                margin-top: 0.25rem;
            }
            .label-row {
                display: flex;
                align-items: center;
                gap: 0.35rem;
            }
            .info-tooltip {
                position: relative;
                cursor: help;
                color: var(--text-muted);
                font-size: 0.75rem;
                line-height: 1;
            }
            .info-tooltip::after {
                content: attr(data-tooltip);
                position: absolute;
                left: 50%;
                transform: translateX(-50%);
                bottom: 100%;
                margin-bottom: 0.35rem;
                padding: 0.4rem 0.6rem;
                background: var(--text-primary);
                color: var(--panel);
                font-size: 0.75rem;
                font-weight: 400;
                white-space: normal;
                min-width: 200px;
                max-width: 320px;
                width: max-content;
                border-radius: 4px;
                pointer-events: none;
                opacity: 0;
                visibility: hidden;
                transition: opacity 0.15s ease, visibility 0.15s ease;
                z-index: 1000;
                box-shadow: 0 2px 8px rgba(0,0,0,0.15);
            }
            .info-tooltip:hover::after,
            .info-tooltip:focus::after {
                opacity: 1;
                visibility: visible;
            }
            
            .advanced-summary-desc {
                font-weight: 400;
                font-size: 0.75rem;
                color: var(--text-muted);
            }
            .filter-section-desc {
                font-size: 0.8rem;
                color: var(--text-secondary);
                margin-bottom: 0.75rem;
                margin-top: -0.5rem;
            }
            .notebook-hint {
                padding-left: 1.625rem;
                margin-top: -0.5rem;
            }
            /* Form stretches to fill left column */
            .split-left form {
                display: flex;
                flex-direction: column;
                flex: 1;
            }
            /* Primary Button - right-aligned, normal size */
            .btn-row {
                display: flex;
                justify-content: flex-end;
                margin-top: 1rem;
            }
            button.primary {
                background: var(--accent);
                border: none;
                border-radius: 4px;
                padding: 0.5rem 1.25rem;
                color: white;
                font-size: 0.875rem;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.2s ease;
                box-shadow: none;
            }
            button.primary:hover {
                background: var(--accent-hover);
            }
            button.primary:active {
                background: var(--accent-active);
            }
            
            /* Terminal Card */
            .terminal-card {
                background: var(--panel);
                border: 1px solid var(--panel-border);
                border-radius: 8px;
                padding: 1rem 1.25rem;
                box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
            }
            .terminal-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                margin-bottom: 0.75rem;
            }
            .terminal-header h3 {
                font-size: 0.9rem;
                font-weight: 600;
            }
            .terminal-actions {
                display: flex;
                gap: 1rem;
            }
            .terminal-action {
                font-size: 0.75rem;
                color: var(--text-primary);
                text-decoration: none;
                cursor: pointer;
                padding: 0.375rem 0.875rem;
                border-radius: 4px;
                background: var(--bg-page);
                border: 1px solid var(--panel-border);
                transition: all 0.2s ease;
                font-weight: 500;
            }
            .terminal-action:hover {
                color: var(--text-primary);
                background: var(--panel);
                border-color: var(--accent);
            }
            .terminal-action-disabled {
                opacity: 0.4;
                pointer-events: none;
                background: var(--bg-page);
                border-color: var(--panel-border);
                color: var(--text-muted);
            }
            .terminal-status {
                display: inline-block;
                font-size: 0.75rem;
                font-weight: 600;
                padding: 0.25rem 0.625rem;
                border-radius: 4px;
                margin-bottom: 0.75rem;
            }
            .terminal-status-idle {
                background: var(--bg-page);
                color: var(--text-muted);
            }
            .terminal-status-running {
                background: rgba(0, 112, 204, 0.1);
                color: var(--info);
            }
            .terminal-status-completed {
                background: rgba(40, 164, 100, 0.1);
                color: var(--success);
            }
            .terminal-status-failed {
                background: rgba(194, 10, 41, 0.1);
                color: var(--error);
            }
            .terminal-status-cancelled {
                background: rgba(204, 183, 24, 0.1);
                color: var(--warning);
            }
            
            /* Progress Phases */
            .progress-phases {
                display: flex;
                gap: 0.375rem;
                margin-bottom: 0.75rem;
                padding: 0.625rem;
                background: var(--bg-page);
                border-radius: 4px;
            }
            .phase-item {
                flex: 1;
                min-width: 0;
            }
            .phase-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 0.3rem;
            }
            .phase-name {
                font-size: 0.75rem;
                font-weight: 600;
                color: var(--text-muted);
                text-transform: uppercase;
                letter-spacing: 0.03em;
            }
            .phase-pct {
                font-size: 0.7rem;
                color: var(--text-muted);
                font-family: ui-monospace, monospace;
            }
            .phase-check {
                color: var(--success);
                font-size: 0.65rem;
            }
            .phase-bar {
                height: 6px;
                background: #E0E0E0;
                border-radius: 2px;
                overflow: hidden;
            }
            .phase-bar-fill {
                height: 100%;
                background: var(--accent);
                border-radius: 2px;
                transition: width 0.3s ease;
            }
            .phase-bar-complete .phase-bar-fill {
                background: var(--success);
            }
            @keyframes shimmer {
                0% { background-position: 100% center; }
                100% { background-position: 0% center; }
            }
            .phase-active .phase-name {
                background: linear-gradient(
                    90deg,
                    rgba(84, 63, 222, 0.5) 0%,
                    rgba(84, 63, 222, 0.5) 40%,
                    rgba(84, 63, 222, 1) 50%,
                    rgba(84, 63, 222, 0.5) 60%,
                    rgba(84, 63, 222, 0.5) 100%
                );
                background-size: 200% 100%;
                background-clip: text;
                -webkit-background-clip: text;
                color: transparent;
                animation: shimmer 2s linear infinite;
            }
            .phase-complete .phase-name {
                color: var(--success);
            }
            .phase-pending .phase-name {
                color: #D0D0D0;
            }
            /* Terminal Output */
            .terminal {
                background: #1E1E1E;
                border-radius: 4px;
                padding: 0.875rem;
                font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
                font-size: 0.8rem;
                line-height: 1.5;
                color: #E0E0E0;
                min-height: 0;
                max-height: 300px;
                overflow-y: auto;
                white-space: pre-wrap;
                margin-top: 0.5rem;
            }
            .terminal-idle {
                min-height: 0;
                color: #808080;
                display: flex;
                align-items: center;
                justify-content: center;
            }
            .field-hint.hidden {
                display: none;
            }
            /* Download section */
            .download-section {
                display: flex;
                gap: 0.75rem;
                margin: 0.75rem 0;
                flex-wrap: wrap;
            }
            .download-btn {
                display: inline-flex;
                align-items: center;
                gap: 0.5rem;
                padding: 0.625rem 1rem;
                background: var(--success);
                color: white;
                border-radius: 4px;
                font-size: 0.85rem;
                font-weight: 600;
                text-decoration: none;
                transition: all 0.2s ease;
                box-shadow: none;
            }
            .download-btn:hover {
                filter: brightness(0.9);
                color: white;
            }
            .download-btn::before {
                content: '↓';
                font-size: 1rem;
            }
            .download-btn-secondary {
                background: white;
                color: var(--success);
                border: 1.5px solid var(--success);
            }
            .download-btn-secondary:hover {
                background: rgba(40, 164, 100, 0.05);
                color: var(--success);
                filter: none;
            }
            .download-btn-secondary::before {
                content: '↓';
                font-size: 1rem;
            }
            /* Terminal line styling */
            .terminal-line-active {
                color: #80AFFF;
            }
            .terminal-line-complete {
                color: #4ADE80;
            }

            /* Execution mode toggle */
            .mode-toggle {
                display: inline-flex;
                background: var(--bg-page);
                border: 1px solid var(--panel-border);
                border-radius: 6px;
                padding: 3px;
                margin-bottom: 1.25rem;
                gap: 2px;
            }
            .mode-toggle-option {
                display: inline-flex;
                align-items: center;
                gap: 0.5rem;
                padding: 0.5rem 1.125rem;
                border-radius: 4px;
                font-size: 0.85rem;
                font-weight: 500;
                color: var(--text-secondary);
                cursor: pointer;
                transition: background 0.15s ease, color 0.15s ease;
                user-select: none;
                white-space: nowrap;
            }
            .mode-toggle-option input[type="radio"] {
                display: none;
            }
            .mode-toggle-option.active {
                background: #EDECFB;
                color: #1820A0;
                font-weight: 600;
                box-shadow: 0 1px 2px rgba(84,63,222,0.12);
            }
            .mode-toggle-option:not(.active):hover {
                background: rgba(84,63,222,0.04);
                color: var(--text-primary);
            }

            /* Domino-specific fields */
            .domino-fields {
                display: flex;
                flex-direction: column;
                gap: 0;
            }

            /* API key source radio */
            .api-key-source {
                display: flex;
                flex-direction: column;
                gap: 0.5rem;
                margin-bottom: 0.5rem;
            }
            .api-key-source-option {
                display: flex;
                align-items: center;
                gap: 0.5rem;
                font-size: 0.85rem;
                color: var(--text-primary);
                cursor: pointer;
            }
            .api-key-source-option input[type="radio"] {
                accent-color: var(--accent);
                cursor: pointer;
            }
            .api-key-callout {
                background: rgba(204, 183, 24, 0.08);
                border: 1px solid rgba(204, 183, 24, 0.4);
                border-radius: 4px;
                padding: 0.5rem 0.75rem;
                font-size: 0.8rem;
                color: #9a7a00;
                margin-top: 0.5rem;
                display: none;
            }

            /* Domino job link */
            .domino-job-link-row {
                margin-bottom: 0.5rem;
            }
            .domino-job-link {
                font-size: 0.85rem;
                font-weight: 500;
                color: var(--accent);
            }

            /* Domino status colors */
            .terminal-status-queued {
                background: rgba(204, 183, 24, 0.1);
                color: var(--warning);
            }
            .terminal-status-submitted {
                background: rgba(0, 112, 204, 0.1);
                color: var(--info);
            }
            .terminal-status-running {
                background: rgba(0, 112, 204, 0.1);
                color: var(--info);
            }
            .terminal-status-succeeded {
                background: rgba(40, 164, 100, 0.1);
                color: var(--success);
            }
            .terminal-status-failed {
                background: rgba(194, 10, 41, 0.1);
                color: var(--error);
            }

            /* Uploaded spec filename (Domino mode) */
            .spec-saved-name {
                font-size: 0.75rem;
                color: var(--accent);
                margin-top: 0.25rem;
            }

            /* Job history */
            .job-history-section {
                margin-top: 1rem;
            }
            .job-history-section summary {
                font-size: 0.875rem;
                font-weight: 600;
                color: var(--text-secondary);
                cursor: pointer;
                padding: 0.75rem 1.25rem;
                background: var(--panel);
                border: 1px solid var(--panel-border);
                border-radius: 8px;
                list-style: none;
                display: flex;
                align-items: center;
                gap: 0.5rem;
            }
            .job-history-section summary::-webkit-details-marker { display: none; }
            .job-history-section summary::before {
                content: '▶';
                font-size: 0.6rem;
                transition: transform 0.2s ease;
            }
            .job-history-section[open] summary::before { transform: rotate(90deg); }
            .job-history-section[open] summary {
                border-radius: 8px 8px 0 0;
                border-bottom: 1px solid var(--panel-border);
            }
            .job-history-content {
                background: var(--panel);
                border: 1px solid var(--panel-border);
                border-top: none;
                border-radius: 0 0 8px 8px;
                padding: 1rem 1.25rem;
            }
            .history-empty {
                color: var(--text-muted);
                font-size: 0.85rem;
                margin: 0;
            }
            .history-table {
                width: 100%;
                border-collapse: collapse;
                font-size: 0.8rem;
            }
            .history-table th {
                text-align: left;
                color: var(--text-secondary);
                font-weight: 600;
                font-size: 0.75rem;
                padding: 0 0.75rem 0.5rem 0;
                border-bottom: 1px solid var(--panel-border);
            }
            .history-table td {
                padding: 0.5rem 0.75rem 0.5rem 0;
                color: var(--text-primary);
                border-bottom: 1px solid var(--panel-border);
            }
            .history-table tr:last-child td { border-bottom: none; }
            .history-status {
                display: inline-block;
                font-size: 0.7rem;
                font-weight: 600;
                padding: 0.15rem 0.5rem;
                border-radius: 3px;
            }
            .history-status-queued { background: rgba(204,183,24,0.1); color: var(--warning); }
            .history-status-submitted { background: rgba(0,112,204,0.1); color: var(--info); }
            .history-status-running { background: rgba(0,112,204,0.1); color: var(--info); }
            .history-status-succeeded { background: rgba(40,164,100,0.1); color: var(--success); }
            .history-status-failed { background: rgba(194,10,41,0.1); color: var(--error); }
            .history-status-cancelled { background: rgba(204,183,24,0.1); color: var(--warning); }
            .history-actions {
                display: flex;
                justify-content: flex-end;
                margin-top: 0.75rem;
            }

            /* Spec manage link */
            .spec-manage-link {
                font-size: 0.75rem;
                color: var(--accent);
                cursor: pointer;
                display: none;
            }
            .spec-list-modal {
                background: var(--panel);
                border: 1px solid var(--panel-border);
                border-radius: 8px;
                padding: 1rem;
                margin-top: 0.5rem;
                display: none;
            }
            .spec-list-item {
                display: flex;
                align-items: center;
                justify-content: space-between;
                padding: 0.4rem 0;
                border-bottom: 1px solid var(--panel-border);
                font-size: 0.8rem;
            }
            .spec-list-item:last-child { border-bottom: none; }

            /* Left/right split layout — stretch to fill page */
            .page-split {
                display: grid;
                grid-template-columns: 1fr clamp(300px, 28%, 380px);
                grid-template-rows: 1fr auto;
                gap: 1rem 1.5rem;
            }
            .page-split > form { grid-column: 1; grid-row: 1; }
            .page-split > .split-right { grid-column: 2; grid-row: 1; display: flex; flex-direction: column; }
            .page-split > .btn-row { grid-column: 1; grid-row: 2; justify-self: end; }
            .output-panel {
                flex: 1;
                min-height: 0;
                background: var(--panel);
                border: 1px solid var(--panel-border);
                border-radius: 8px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.08);
                overflow: hidden;
                display: flex;
                flex-direction: column;
            }
            .tab-bar {
                display: flex;
                border-bottom: 1px solid var(--panel-border);
                padding: 0 1rem;
                background: var(--panel);
            }
            .tab-btn {
                font-size: 0.85rem; font-weight: 500; color: var(--text-secondary);
                padding: 0.75rem 1rem 0.625rem;
                border: none; border-bottom: 2px solid transparent;
                background: none; cursor: pointer;
                transition: color 0.15s, border-color 0.15s;
            }
            .tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }
            .tab-btn:not(.active):hover { color: var(--text-primary); }
            .tab-content { padding: 1rem; flex: 1; overflow-y: auto; min-height: 0; }
            .tab-content.hidden { display: none; }
            .tab-content .terminal-card { border: none; box-shadow: none; padding: 0; }
            @media (max-width: 1100px) {
                .page-split { grid-template-columns: 1fr; grid-template-rows: auto auto auto; }
                .page-split > form { grid-column: 1; grid-row: 1; }
                .page-split > .split-right { grid-column: 1; grid-row: 2; }
                .page-split > .btn-row { grid-column: 1; grid-row: 3; justify-self: end; }
            }
            .target-project-banner {
                padding: 0.75rem 1rem;
                border-radius: 8px;
                background: #EDECFB;
                border: 1px solid #C9C5F2;
                margin-bottom: 1rem;
                font-size: 0.875rem;
                color: #3F4547;
                font-weight: 500;
            }
            .target-project-banner.resolving {
                background: #FFF8E1;
                border-color: #FFE082;
                color: #7F8385;
            }
            .target-project-banner.error {
                background: #FCE4EC;
                border-color: #EF9A9A;
                color: #C62828;
            }
            """
        ),
        Script(f"""
            const DOMINO_OUTPUT_DEFAULT = {json.dumps(str(_get_default_output_dir()))};
            const APP_OUTPUT_DEFAULT = "/mnt/code/output";
        """),
        Script(
            r"""
            document.addEventListener('DOMContentLoaded', function() {

                // ── Cross-project targeting (Extension mode) ─────────────────────
                var urlProjectId = new URLSearchParams(window.location.search).get('projectId');
                var targetBanner = document.getElementById('target-project-banner');
                var targetInput = document.getElementById('field-project-id');
                var genBtn = document.getElementById('generate-btn');

                if (urlProjectId) {
                    if (targetInput) targetInput.value = urlProjectId;

                    if (targetBanner) {
                        targetBanner.style.display = '';
                        targetBanner.textContent = 'Resolving project\u2026';
                        targetBanner.className = 'target-project-banner resolving';
                    }

                    if (genBtn) {
                        genBtn.disabled = true;
                        genBtn.textContent = 'Resolving project\u2026';
                    }

                    fetch('/api/resolve-project?projectId=' + encodeURIComponent(urlProjectId))
                        .then(function(r) {
                            if (!r.ok) throw new Error('Failed to resolve project (' + r.status + ')');
                            return r.json();
                        })
                        .then(function(data) {
                            var displayName = data.owner + '/' + data.name;
                            window._resolvedProjectName = data.name;

                            if (targetBanner) {
                                targetBanner.textContent = 'Generating docs for: ' + displayName;
                                targetBanner.className = 'target-project-banner';
                                targetBanner.style.display = '';
                            }

                            var outputDir = document.getElementById('field-output_dir');
                            if (outputDir) outputDir.value = '/mnt/data/' + data.name;

                            if (genBtn) {
                                genBtn.disabled = false;
                                genBtn.textContent = 'Generate Documentation';
                            }
                        })
                        .catch(function(err) {
                            if (targetBanner) {
                                targetBanner.textContent = 'Could not resolve project: ' + err.message;
                                targetBanner.className = 'target-project-banner error';
                                targetBanner.style.display = '';
                            }

                            if (genBtn) {
                                genBtn.disabled = false;
                                genBtn.textContent = 'Generate Documentation';
                            }
                        });
                }

                // ── All DOM references declared up-front to avoid TDZ errors ──────
                const modeDominoLabel   = document.getElementById('mode-domino-label');
                const modeAppLabel      = document.getElementById('mode-app-label');
                const uploadBtnLabel    = document.querySelector('label.upload-btn');
                const specSavedName     = document.getElementById('spec-saved-name');
                const appModeNote       = document.getElementById('app-mode-note');
                const appNoteHint       = document.getElementById('app-mode-notebook-hint');
                const apiKeyPassField   = document.getElementById('api-key-pass-field');
                const apiKeyCallout     = document.getElementById('api-key-callout');
                const apiKeySourceRadios = document.querySelectorAll('input[name="api_key_source"]');
                const providerSelect    = document.getElementById('field-provider');
                const baseUrlField      = document.getElementById('base-url-field');
                const modelNameField    = document.getElementById('model-name-field');

                // ── Execution mode toggle ──────────────────────────────────────────
                function applyExecutionMode(mode) {
                    const isDomino = mode === 'domino';

                    // Highlight the active toggle pill
                    if (modeDominoLabel) modeDominoLabel.classList.toggle('active', isDomino);
                    if (modeAppLabel)    modeAppLabel.classList.toggle('active', !isDomino);

                    // Show/hide Domino-specific fields
                    document.querySelectorAll('.domino-fields').forEach(function(el) {
                        el.style.display = isDomino ? '' : 'none';
                    });

                    // App-mode upload button (label-based file picker)
                    if (uploadBtnLabel) uploadBtnLabel.style.display = isDomino ? 'none' : '';

                    // App-mode-only elements
                    if (appModeNote)  appModeNote.style.display  = isDomino ? 'none' : '';
                    if (appNoteHint)  appNoteHint.style.display  = isDomino ? 'none' : '';

                    // API key visibility — both modes use the same radio group
                    const src = document.querySelector('input[name="api_key_source"]:checked');
                    applyApiKeySource(src ? src.value : 'domino_env');

                    // Show/hide History tab (Domino-only)
                    const historyTabBtn = document.querySelector('.tab-btn[data-tab="history"]');
                    if (historyTabBtn) {
                        historyTabBtn.style.display = isDomino ? '' : 'none';
                        if (!isDomino && historyTabBtn.classList.contains('active')) {
                            showOutputTab('live');
                        }
                    }

                    // Update polling on status panel
                    const panel = document.getElementById('status-panel');
                    if (panel) {
                        if (isDomino && typeof htmx !== 'undefined') {
                            // Domino mode: use HTMX polling
                            panel.setAttribute('hx-get', 'domino-status');
                            panel.setAttribute('hx-trigger', 'every 10s');
                            htmx.process(panel);
                            htmx.ajax('GET', 'domino-status', {target: '#status-panel', swap: 'innerHTML'});
                        } else {
                            // App mode: remove HTMX polling, smart JS poll handles it
                            panel.removeAttribute('hx-get');
                            panel.removeAttribute('hx-trigger');
                            if (typeof htmx !== 'undefined') htmx.process(panel);
                            // Activate smart polling and force an immediate check
                            if (typeof window._activateStatusPolling === 'function') {
                                window._activateStatusPolling();
                            }
                        }
                    }

                    // Update output directory default for the selected mode
                    // If a cross-project target was resolved, keep its output dir
                    const outputDirField = document.getElementById('field-output_dir');
                    if (outputDirField && !window._resolvedProjectName) {
                        outputDirField.value = isDomino ? DOMINO_OUTPUT_DEFAULT : APP_OUTPUT_DEFAULT;
                    }

                    // Update output directory hint text
                    const outputDirHint = document.getElementById('output-dir-hint');
                    if (outputDirHint) {
                        outputDirHint.setAttribute('data-tooltip', isDomino
                            ? 'Output files are written here by the Domino job.'
                            : 'Output files are written here and available to download.');
                    }
                }

                // Wire clicks on the label elements directly (radio is hidden)
                if (modeDominoLabel) {
                    modeDominoLabel.addEventListener('click', function() {
                        applyExecutionMode('domino');
                    });
                }
                if (modeAppLabel) {
                    modeAppLabel.addEventListener('click', function() {
                        applyExecutionMode('app');
                    });
                }

                // Apply on load based on which radio is checked
                const checkedMode = document.querySelector('input[name="execution_mode"]:checked');
                applyExecutionMode(checkedMode ? checkedMode.value : 'domino');

                // ── Refresh branch dropdown for cross-project targeting ────────────
                const _projectId = new URLSearchParams(window.location.search).get('projectId') || '';
                if (_projectId) {
                    var branchSelect = document.getElementById('field-branch');
                    if (branchSelect) {
                        fetch('api/branches?projectId=' + encodeURIComponent(_projectId))
                            .then(function(r) { return r.text(); })
                            .then(function(html) {
                                branchSelect.outerHTML = html;
                            })
                            .catch(function() {});
                    }
                }

                // ── API key source radio ───────────────────────────────────────────
                function applyApiKeySource(src) {
                    const show = src === 'pass_now';
                    if (apiKeyPassField) apiKeyPassField.style.display = show ? '' : 'none';
                    if (apiKeyCallout) {
                        apiKeyCallout.style.display = show ? 'block' : 'none';
                        if (!apiKeyCallout.textContent.trim()) {
                            apiKeyCallout.textContent = '\u26a0 This key will be visible in the Domino job\u2019s environment metadata to project admins.';
                        }
                    }
                }

                apiKeySourceRadios.forEach(function(r) {
                    r.addEventListener('change', function() { applyApiKeySource(this.value); });
                });

                const checkedSrc = document.querySelector('input[name="api_key_source"]:checked');
                applyApiKeySource(checkedSrc ? checkedSrc.value : 'domino_env');

                // ── Domino mode: spec auto-save via fetch ────────────────────────
                const dominoSpecUpload = document.getElementById('domino-spec-upload');
                if (dominoSpecUpload) {
                    dominoSpecUpload.addEventListener('change', function(e) {
                        const file = e.target.files[0];
                        if (!file) return;

                        // Disable Generate button until upload completes
                        var genBtn = document.getElementById('generate-btn');
                        if (genBtn) { genBtn.disabled = true; genBtn.textContent = 'Uploading spec...'; }
                        if (specSavedName) { specSavedName.textContent = 'Uploading ' + file.name + '...'; specSavedName.style.color = ''; }

                        const reader = new FileReader();
                        reader.onload = function(evt) {
                            const content = evt.target.result;
                            const fd = new FormData();
                            fd.append('spec_filename', file.name);
                            fd.append('spec_content', content);
                            fetch('save-spec', { method: 'POST', body: fd })
                                .then(function(r) {
                                    if (!r.ok) throw new Error('Server returned ' + r.status);
                                    return r.text();
                                })
                                .then(function(path) {
                                    var pathInput = document.getElementById('field-spec_path');
                                    if (pathInput) pathInput.value = path.trim();
                                    if (specSavedName) { specSavedName.textContent = 'Saved: ' + file.name; specSavedName.style.color = '#2e7d32'; }
                                    var contentInput = document.getElementById('domino-spec-content');
                                    if (contentInput) contentInput.value = '';
                                })
                                .catch(function(err) {
                                    if (specSavedName) { specSavedName.textContent = 'Upload failed: ' + err.message; specSavedName.style.color = '#c62828'; }
                                })
                                .finally(function() {
                                    if (genBtn) { genBtn.disabled = false; genBtn.textContent = 'Generate Documentation'; }
                                });
                        };
                        reader.readAsText(file);
                    });
                }

                // ── Toggle base URL and model name fields based on provider selection
                function toggleOpenAIFields() {
                    const isOpenAI = providerSelect && providerSelect.value === 'openai';
                    if (baseUrlField) {
                        baseUrlField.style.display = isOpenAI ? 'flex' : 'none';
                    }
                    if (modelNameField) {
                        modelNameField.style.display = isOpenAI ? 'flex' : 'none';
                    }
                }

                if (providerSelect) {
                    providerSelect.addEventListener('change', toggleOpenAIFields);
                    toggleOpenAIFields();
                }
                
                // Handle file upload and update spec path display
                const specUpload = document.querySelector('input[name="spec_upload"]');
                const specPath = document.getElementById('field-spec_path');
                const uploadFilename = document.getElementById('upload-filename');
                
                if (specUpload && specPath) {
                    specUpload.addEventListener('change', function(e) {
                        const file = e.target.files[0];
                        if (file) {
                            // Update the path field to show the uploaded filename
                            specPath.value = '[Uploaded] ' + file.name;
                            specPath.disabled = true;
                            // Show the filename below
                            if (uploadFilename) {
                                uploadFilename.textContent = 'Using uploaded file: ' + file.name;
                            }
                        } else {
                            // Clear if no file selected
                            specPath.disabled = false;
                            if (uploadFilename) {
                                uploadFilename.textContent = '';
                            }
                        }
                    });
                }
                
                // Highlight active terminal lines with spinner
                function styleTerminalLines() {
                    const terminal = document.querySelector('.terminal:not(.terminal-idle)');
                    if (!terminal) return;
                    
                    const text = terminal.textContent;
                    const lines = text.split('\\n');
                    const totalLines = lines.length;
                    
                    // Check if job is still running (look for completion indicators)
                    const isComplete = lines.some(line => 
                        line.includes('Generation complete') || 
                        line.includes('Error:') || 
                        line.includes('Cancelled') ||
                        line.includes('Cleanup complete')
                    );
                    
                    // Find last active line index (the most recent activity)
                    let lastActiveIndex = -1;
                    if (!isComplete) {
                        for (let i = lines.length - 1; i >= 0; i--) {
                            const line = lines[i].trim();
                            if (line && line.match(/^\[\d{2}:\d{2}:\d{2}\]/)) {
                                lastActiveIndex = i;
                                break;
                            }
                        }
                    }
                    
                    // Style the lines
                    let styledHtml = lines.map((line, index) => {
                        const escapedLine = line.replace(/</g, '&lt;').replace(/>/g, '&gt;');
                        
                        // Show spinner on the last active timestamped line
                        if (index === lastActiveIndex) {
                            return '<span class="terminal-line-active">' + escapedLine + '</span>';
                        }
                        // Style completion messages
                        if (line.includes('Complete') || line.includes('Generation complete')) {
                            return '<span class="terminal-line-complete">' + escapedLine + '</span>';
                        }
                        return escapedLine;
                    }).join('\\n');
                    
                    terminal.innerHTML = styledHtml;
                }

                // Auto-scroll terminal to bottom to show latest logs
                function scrollTerminalToBottom() {
                    const terminal = document.querySelector('.terminal:not(.terminal-idle)');
                    if (terminal) {
                        terminal.scrollTop = terminal.scrollHeight;
                    }
                }

                // Tab switcher — on window so inline onclick can call it
                window.showOutputTab = function(tab) {
                    document.querySelectorAll('.tab-btn').forEach(function(btn) {
                        btn.classList.toggle('active', btn.dataset.tab === tab);
                    });
                    document.querySelectorAll('.tab-content').forEach(function(content) {
                        content.classList.toggle('hidden', content.id !== 'tab-' + tab);
                    });
                };

                // ── Code root prefix+suffix sync ──────────────────────────────────
                (function() {
                    const prefix = document.getElementById('code-root-prefix');
                    const suffix = document.getElementById('code-root-suffix');
                    const hidden = document.getElementById('field-code_root');
                    function sync() {
                        if (!prefix || !hidden) return;
                        const base = prefix.textContent.trim();
                        const sub = suffix ? suffix.value.replace(/^\/+/, '') : '';
                        hidden.value = sub ? base + '/' + sub : base;
                    }
                    if (suffix) suffix.addEventListener('input', sync);
                    sync();
                })();

                // Run styling/scrolling after any status update (HTMX swap or smart poll)
                function onStatusUpdate() {
                    styleTerminalLines();
                    scrollTerminalToBottom();
                }

                document.body.addEventListener('htmx:afterSwap', function(e) {
                    if (e.detail && e.detail.target && e.detail.target.id === 'status-panel') {
                        showOutputTab('live');
                    }
                    onStatusUpdate();
                });

                // Custom event fired by smart polling after DOM update
                document.body.addEventListener('statusUpdated', onStatusUpdate);

                // Re-activate smart polling when HTMX submits the form
                document.body.addEventListener('htmx:afterRequest', function(e) {
                    if (e.detail && e.detail.pathInfo && e.detail.pathInfo.requestPath === '/run') {
                        if (typeof window._activateStatusPolling === 'function') {
                            window._activateStatusPolling();
                        }
                    }
                });

                setInterval(styleTerminalLines, 500);
            });
            """
        ),
    )
)


@rt("/")
def index(req: Request):
    # Cache the external host on first request so Domino job URLs resolve correctly.
    if _DOMINO_AVAILABLE:
        host = req.headers.get("x-forwarded-host") or req.headers.get("host") or ""
        scheme = req.headers.get("x-forwarded-proto", "https")
        domino_client.set_ui_host(host, scheme)

    default_spec = _get_default_spec_path()
    username = _get_username()
    try:
        _settings = Settings()
        _current_model = _settings.get_model_name()
        _current_base_url = _settings.openai_base_url or ""
    except Exception:
        _current_model = ""
        _current_base_url = ""

    # Determine initial status panel content based on latest Domino job
    initial_status_panel: FT
    latest_domino: Optional[DominoJobRecord] = None
    if _DOMINO_AVAILABLE:
        try:
            domino_job_store.init_db()
            jobs = domino_job_store.get_user_jobs(username, limit=1)
            if jobs:
                latest_domino = _db_record_to_dataclass(jobs[0])
        except Exception:
            pass

    # Default to Domino mode if Domino is available
    default_mode = "domino" if _DOMINO_AVAILABLE else "app"

    # Pre-fetch branches and hardware tiers for server-side rendering
    project_id = req.query_params.get("projectId", "").strip()
    if _DOMINO_AVAILABLE:
        try:
            if project_id:
                _branches_raw = domino_client.list_branches_api(project_id)
            else:
                _branches_raw = domino_client.list_branches()
            branch_options = [Option(b["name"], value=b["name"]) for b in _branches_raw]
        except Exception:
            branch_options = []
        if not branch_options:
            branch_options = [Option("main", value="main"), Option("master", value="master")]
        try:
            tier_data = domino_client.list_hardware_tiers()
            default_tier = domino_client.get_project_default_tier()
            tier_options = []
            for t in tier_data:
                tid = t.get("id", "")
                tname = t.get("name") or tid
                is_default = t.get("isDefault", False) or tid == default_tier
                tier_options.append(Option(tname, value=tid, selected=is_default))
        except Exception:
            tier_options = []
        if not tier_options:
            tier_options = [Option("(default)", value="")]
    else:
        branch_options = [Option("(Domino not available)", value="")]
        tier_options = [Option("(Domino not available)", value="")]

    return (
        Title("Auto Model Docs Studio"),
        Div(
            Div(
                H2("Auto Model Docs Studio", cls="domino-header-title"),
                cls="domino-header-inner",
            ),
            cls="domino-header",
        ),
        Div(
            # Tagline (no duplicate app name)
            Div(
                P("Generate model documentation with a single, guided workflow.", cls="hero-tagline"),
                cls="hero",
            ),
            Div(
                Label(
                    Input(
                        type="radio",
                        name="execution_mode",
                        value="domino",
                        checked=(default_mode == "domino"),
                        form="main-form",
                    ),
                    "Run as Domino Job",
                    cls="mode-toggle-option" + (" active" if default_mode == "domino" else ""),
                    id="mode-domino-label",
                ),
                Label(
                    Input(
                        type="radio",
                        name="execution_mode",
                        value="app",
                        checked=(default_mode == "app"),
                        form="main-form",
                    ),
                    "Run in App",
                    cls="mode-toggle-option" + (" active" if default_mode == "app" else ""),
                    id="mode-app-label",
                ),
                cls="mode-toggle",
            ),
            Div(
                id="target-project-banner",
                cls="target-project-banner",
                style="display: none;",
            ),
            Div(
            Form(
                Input(type="hidden", name="target_project", id="field-project-id"),
                # Three cards stacked vertically: What to document | Run | Advanced
                Div(
                    # Card 1: What to document (spec + artifact filtering)
                    Div(
                        Div("What to document", cls="card-title"),
                        Div(
                            Label("Spec file", Span(" *", cls="required-star"), for_="field-spec_path"),
                            Div(
                                Input(
                                    name="spec_path",
                                    id="field-spec_path",
                                    type="text",
                                    value=str(default_spec),
                                    placeholder=str(default_spec),
                                ),
                                Label(
                                    "Upload",
                                    Input(
                                        name="spec_upload",
                                        type="file",
                                        accept=".yaml,.yml",
                                        cls="hidden-upload",
                                    ),
                                    cls="upload-btn",
                                ),
                                cls="field-inline",
                            ),
                            Div(id="upload-filename", cls="upload-filename"),
                            cls="field",
                        ),
                        Div(
                            Label(
                                Input(
                                    type="file",
                                    accept=".yaml,.yml",
                                    id="domino-spec-upload",
                                    cls="hidden-upload",
                                ),
                                "Upload spec",
                                cls="upload-btn",
                                style="margin-top: 0.35rem; display: inline-flex;",
                            ),
                            Span(id="spec-saved-name", cls="spec-saved-name"),
                            Span("ⓘ", cls="info-tooltip", data_tooltip="Upload to save the spec file to Domino dataset storage for the job to access."),
                            cls="domino-fields",
                        ),
                        Div(
                            Div(
                                Label("Model names", for_="field-model_names"),
                                Span("ⓘ", cls="info-tooltip", data_tooltip="Comma-separated. Supports wildcards: * and ?"),
                                cls="label-row",
                            ),
                            Input(
                                name="model_names",
                                id="field-model_names",
                                type="text",
                                placeholder="model1, churn*, fraud-*",
                            ),
                            cls="field",
                        ),
                        Div(
                            Div(
                                Label("Experiment names", for_="field-experiment_names"),
                                Span("ⓘ", cls="info-tooltip", data_tooltip="Comma-separated. Supports wildcards: * and ?"),
                                cls="label-row",
                            ),
                            Input(
                                name="experiment_names",
                                id="field-experiment_names",
                                type="text",
                                placeholder="exp1, exp2, my-experiment*",
                            ),
                            cls="field",
                        ),
                        Label(
                            Input(type="checkbox", name="latest_only", id="field-latest_only", checked=True),
                            Span("Latest version only"),
                            cls="checkbox-field",
                        ),
                        cls="card",
                    ),
                    # Card 2: Run (paths, branch, hardware, API key)
                    Div(
                        Div("Run", cls="card-title"),
                        Div(
                            Label("Code root", for_="code-root-suffix"),
                            Div(
                                Span("/mnt/code", id="code-root-prefix", cls="code-root-prefix"),
                                Input(
                                    id="code-root-suffix",
                                    type="text",
                                    placeholder="subdirectory (optional)",
                                    cls="code-root-suffix",
                                ),
                                Input(
                                    name="code_root",
                                    id="field-code_root",
                                    type="hidden",
                                    value=str(_get_default_code_root()),
                                ),
                                cls="code-root-wrap",
                            ),
                            cls="field",
                        ),
                        Div(
                            Div(
                                Label("Branch", for_="field-branch"),
                                Span("ⓘ", cls="info-tooltip", data_tooltip="Git branch to analyze in the Domino job."),
                                cls="label-row",
                            ),
                            Select(
                                *branch_options,
                                name="branch",
                                id="field-branch",
                            ),
                            cls="field domino-fields",
                        ),
                        Div(
                            Div(
                                Label("Output directory", for_="field-output_dir"),
                                Span(
                                    "ⓘ",
                                    cls="info-tooltip",
                                    data_tooltip="Output files are written here by the Domino job.",
                                    id="output-dir-hint",
                                ),
                                cls="label-row",
                            ),
                            Input(
                                name="output_dir",
                                id="field-output_dir",
                                type="text",
                                value=str(_get_default_output_dir()),
                            ),
                            cls="field domino-fields",
                        ),
                        Div(
                            Div(
                                Label("Hardware tier", for_="field-hardware_tier"),
                                Span("ⓘ", cls="info-tooltip", data_tooltip="Compute tier for the Domino job."),
                                cls="label-row",
                            ),
                            Select(
                                *tier_options,
                                name="hardware_tier",
                                id="field-hardware_tier",
                            ),
                            cls="field domino-fields",
                        ),
                        Div(
                            Label("API key"),
                            Div(
                                Label(
                                    Input(type="radio", name="api_key_source", value="domino_env", checked=True),
                                    "Domino environment variable (recommended)",
                                    cls="api-key-source-option",
                                ),
                                Label(
                                    Input(type="radio", name="api_key_source", value="pass_now"),
                                    "Set key",
                                    cls="api-key-source-option",
                                ),
                                cls="api-key-source",
                            ),
                            Div(id="api-key-callout", cls="api-key-callout"),
                            cls="field",
                            id="api-key-source-field",
                        ),
                        Div(
                            Label("API key", Span(" *", cls="required-star"), for_="field-api_key"),
                            Input(
                                name="api_key",
                                id="field-api_key",
                                type="password",
                                placeholder="Paste your API key",
                                autocomplete="off",
                                spellcheck="false",
                            ),
                            cls="field",
                            id="api-key-pass-field",
                            style="display: none;" if default_mode == "domino" else "",
                        ),
                        cls="card",
                    ),
                    # Card 3: Advanced (workers, timeout, provider, model, notebook)
                    Div(
                        Div("Advanced", cls="card-title"),
                        Div("Generation settings", cls="filter-section-title"),
                        Div(
                            Div(
                                Label("Max files", for_="field-max_files"),
                                Input(name="max_files", id="field-max_files", type="number", value="50"),
                                cls="field",
                            ),
                            Div(
                                Div(
                                    Label("Planning workers", for_="field-planning_workers"),
                                    Span("ⓘ", cls="info-tooltip", data_tooltip="Parallel LLM calls in the planning phase."),
                                    cls="label-row",
                                ),
                                Input(name="planning_workers", id="field-planning_workers", type="number", value="1"),
                                cls="field",
                            ),
                            Div(
                                Div(
                                    Label("Generation workers", for_="field-workers"),
                                    Span("ⓘ", cls="info-tooltip", data_tooltip="Sections generated in parallel."),
                                    cls="label-row",
                                ),
                                Input(name="workers", id="field-workers", type="number", value="4"),
                                cls="field",
                            ),
                            Div(
                                Div(
                                    Label("Timeout (s)", for_="field-timeout"),
                                    Span("ⓘ", cls="info-tooltip", data_tooltip="Seconds before a single LLM call times out."),
                                    cls="label-row",
                                ),
                                Input(name="timeout", id="field-timeout", type="number", value="120"),
                                cls="field",
                            ),
                            cls="advanced-grid",
                        ),
                        Div(
                            Label("Provider", for_="field-provider"),
                            Select(
                                Option("Anthropic", value="anthropic"),
                                Option("OpenAI (Compatible)", value="openai", selected=True),
                                name="provider",
                                id="field-provider",
                            ),
                            cls="field",
                        ),
                        Div(
                            Div(
                                Label("Model", for_="field-model"),
                                Span("ⓘ", cls="info-tooltip", data_tooltip="Leave blank to use default (gpt-4o)"),
                                cls="label-row",
                            ),
                            Input(name="model", id="field-model", type="text", value=_current_model, placeholder="gpt-4o"),
                            cls="field",
                            id="model-name-field",
                            style="display: none;",
                        ),
                        Div(
                            Div(
                                Label("Base URL", for_="field-base_url"),
                                Span("ⓘ", cls="info-tooltip", data_tooltip="For OpenAI-compatible APIs (e.g., Moonshot, Azure)"),
                                cls="label-row",
                            ),
                            Input(
                                name="base_url",
                                id="field-base_url",
                                type="text",
                                value=_current_base_url,
                                placeholder="https://api.openai.com/v1 (optional)",
                            ),
                            cls="field",
                            id="base-url-field",
                            style="display: none;",
                        ),
                        Label(
                            Input(type="checkbox", name="notebook", id="field-notebook", checked=True),
                            Span("Generate notebook"),
                            Span("ⓘ", cls="info-tooltip", data_tooltip="Saved alongside your document in the output directory.", id="app-mode-notebook-hint"),
                            cls="checkbox-field",
                            id="app-mode-note",
                        ),
                        cls="card card-advanced",
                    ),
                    cls="config-grid",
                ),
                id="main-form",
                hx_post="run",
                hx_target="#status-panel",
                hx_swap="innerHTML",
                hx_encoding="multipart/form-data",
                enctype="multipart/form-data",
            ),
            Div(
                Button("Generate Documentation", type="submit", id="generate-btn", cls="primary", form="main-form"),
                cls="btn-row",
            ),
            Div(
                Div(
                    Div(
                        Button("Output", cls="tab-btn active", data_tab="live", onclick="showOutputTab('live')"),
                        Button("History", cls="tab-btn", data_tab="history", onclick="showOutputTab('history')"),
                        cls="tab-bar",
                    ),
                    Div(
                        Div(
                            _render_domino_status(latest_domino) if (default_mode == "domino") else _render_status(_resolve_job(ACTIVE_JOB_ID)),
                            id="status-panel",
                            **({"hx_get": "domino-status", "hx_trigger": "every 10s", "hx_swap": "innerHTML"} if default_mode == "domino" else {}),
                        ),
                        id="tab-live",
                        cls="tab-content",
                    ),
                    Div(
                        Div(
                            _render_job_history_table(username),
                            id="job-history-content",
                            hx_get="job-history",
                            hx_trigger="every 15s",
                            hx_swap="innerHTML",
                        ),
                        id="tab-history",
                        cls="tab-content hidden",
                    ),
                    cls="output-panel",
                ),
                cls="split-right",
            ),
            cls="page-split",
            ),
            cls="page",
        ),
    )


@rt("/run")
async def run(req: Request):
    job_request = await _parse_request(req)

    if job_request.execution_mode == "domino" and _DOMINO_AVAILABLE:
        username = _get_username()
        try:
            record = await _submit_domino_job(job_request, username)
        except Exception as exc:
            err_record = DominoJobRecord(
                id=str(uuid4()),
                username=username,
                status="failed",
                domino_status=str(exc),
            )
            return _render_domino_status(err_record)
        return _render_domino_status(record)

    # App mode (or Domino unavailable)
    active = _resolve_job(ACTIVE_JOB_ID)
    if active and active.status == "running":
        _log(active, "A job is already running. Please wait for completion.")
        return _render_status(active)

    job = _start_job(job_request)
    _log(job, "Job submitted.")
    return _render_status(job)


@rt("/status")
def status():
    job = _resolve_job(ACTIVE_JOB_ID)
    return _render_status(job)


@rt("/status-check")
def status_check():
    """Lightweight endpoint returning only version + status (no HTML)."""
    job = _resolve_job(ACTIVE_JOB_ID)
    if not job:
        return Response(json.dumps({"status": "idle", "logVersion": 0}), media_type="application/json")
    return Response(json.dumps({"status": job.status, "logVersion": job.log_version}), media_type="application/json")


@rt("/clear-terminal")
def clear_terminal():
    job = _resolve_job(ACTIVE_JOB_ID)
    if job and job.status != "running":
        job.logs.clear()
        _log(job, "Logs cleared.")
    elif job and job.status == "running":
        _log(job, "Clear requested during run; preserving logs.")
    return _render_status(job)


@rt("/stop")
def stop():
    job = _resolve_job(ACTIVE_JOB_ID)
    if not job:
        return _render_status(job)

    if job.status == "running" and job.task:
        job.cancel_requested = True
        _log(job, "Stop requested. Attempting to cancel...")
        job.task.cancel()
    else:
        _log(job, "Stop requested, but no active run found.")

    return _render_status(job)


@rt("/download/{job_id}/{artifact}")
def download(job_id: str, artifact: str):
    job = _resolve_job(job_id)
    if not job or job.status != "completed":
        return Response("Not ready", status_code=404)

    if artifact == "docx":
        path = job.output_path
    elif artifact == "notebook":
        path = job.notebook_path
    else:
        return Response("Unknown artifact", status_code=404)

    if not path or not path.exists():
        return Response("File not found", status_code=404)

    # Use the actual filename from the path
    return FileResponse(path, filename=path.name)


@rt("/api/branches")
def api_branches(req: Request):
    """Return an HTML <select> fragment with available git branches."""
    if not _DOMINO_AVAILABLE:
        return Select(Option("(Domino not available)", value=""), name="branch", id="field-branch")
    project_id = req.query_params.get("projectId", "").strip()
    if project_id:
        branches = domino_client.list_branches_api(project_id)
    else:
        branches = domino_client.list_branches()
    options = [Option(b.get("name", ""), value=b.get("name", "")) for b in branches]
    if not options:
        options = [Option("main", value="main"), Option("master", value="master")]
    return Select(*options, name="branch", id="field-branch")


@rt("/api/hardware-tiers")
def api_hardware_tiers():
    """Return an HTML <select> fragment with available hardware tiers."""
    if not _DOMINO_AVAILABLE:
        return Select(Option("(Domino not available)", value=""), name="hardware_tier", id="field-hardware_tier")
    tiers = domino_client.list_hardware_tiers()
    default_tier = domino_client.get_project_default_tier()
    options = []
    for t in tiers:
        tid = t.get("id", "")
        tname = t.get("name") or tid
        is_default = t.get("isDefault", False) or tid == default_tier
        options.append(Option(tname, value=tid, selected=is_default))
    if not options:
        options = [Option("(default)", value="")]
    return Select(*options, name="hardware_tier", id="field-hardware_tier")


@rt("/api/resolve-project")
def api_resolve_project(req: Request):
    """Resolve a Domino project ID to owner/name JSON."""
    project_id = req.query_params.get("projectId", "").strip()
    if not project_id:
        return Response(
            json.dumps({"error": "No project ID provided."}),
            status_code=400, media_type="application/json",
        )
    if not _DOMINO_AVAILABLE:
        return Response(
            json.dumps({"error": "Domino integration is not available."}),
            status_code=503, media_type="application/json",
        )
    try:
        data = domino_client.resolve_project(project_id)
        owner = data.get("owner", {}).get("userName", "") or data.get("ownerUsername", "")
        name = data.get("name", project_id)
        return Response(
            json.dumps({"owner": owner, "name": name, "id": project_id}),
            media_type="application/json",
        )
    except domino_client.ProjectNotFoundError:
        return Response(
            json.dumps({"error": "Project not found. The extension link may be outdated."}),
            status_code=404, media_type="application/json",
        )
    except domino_client.ProjectForbiddenError:
        return Response(
            json.dumps({"error": "You don't have access to this project."}),
            status_code=403, media_type="application/json",
        )
    except domino_client.ProjectAPIError:
        return Response(
            json.dumps({"error": "Could not reach the Domino API. Try again in a moment."}),
            status_code=502, media_type="application/json",
        )


@rt("/stop-domino")
async def stop_domino(req: Request):
    form = await req.form()
    job_id = form.get("job_id")
    username = _get_username()
    if job_id and _DOMINO_AVAILABLE:
        row = domino_job_store.get_job(job_id)
        if row and row.get("domino_run_id"):
            try:
                domino_client.stop_job(row["domino_run_id"])
            except Exception:
                pass
        if row:
            domino_job_store.update_job(job_id, status="cancelled")
            row = domino_job_store.get_job(job_id)
            return _render_domino_status(_db_record_to_dataclass(row))
    return _render_domino_status(None)


@rt("/domino-status")
def domino_status():
    """Return the Domino job status panel for the latest active job of the current user."""
    username = _get_username()
    if not _DOMINO_AVAILABLE:
        return _render_domino_status(None)
    domino_job_store.init_db()
    jobs = domino_job_store.get_user_jobs(username, limit=1)
    if not jobs:
        return _render_domino_status(None)
    return _render_domino_status(_db_record_to_dataclass(jobs[0]))


@rt("/job-history")
def job_history():
    username = _get_username()
    return _render_job_history_table(username)


@rt("/clear-job-history")
def clear_job_history():
    username = _get_username()
    if _DOMINO_AVAILABLE:
        domino_job_store.clear_terminal_jobs(username)
    return _render_job_history_table(username)


@rt("/save-spec")
async def save_spec_route(req: Request):
    """Auto-save an uploaded spec file and return the saved path."""
    if not _DOMINO_AVAILABLE:
        return Response("Domino not available", status_code=400)
    form = await req.form()
    filename = form.get("spec_filename", "spec.yaml")
    content = form.get("spec_content", "")
    saved = spec_store.save_spec(filename, content)
    return Response(str(saved), media_type="text/plain")


@rt("/spec-list")
def spec_list():
    """Return HTML list of saved spec files."""
    if not _DOMINO_AVAILABLE:
        return Div(P("Domino not available.", cls="history-empty"))
    specs = spec_store.list_specs()
    if not specs:
        return Div(P("No saved spec files.", cls="history-empty"), id="spec-list-content")
    items = []
    for s in specs:
        items.append(
            Div(
                Span(s["name"], style="font-family: monospace;"),
                Span(f"{s['size_kb']} KB", style="color: var(--text-muted); margin: 0 0.75rem;"),
                A(
                    "Delete",
                    hx_post="delete-spec",
                    hx_vals=f'{{"filename": "{s["name"]}"}}',
                    hx_target="#spec-list-content",
                    hx_swap="innerHTML",
                    style="color: var(--error); font-size: 0.75rem; cursor: pointer;",
                ),
                cls="spec-list-item",
            )
        )
    return Div(*items, id="spec-list-content", cls="spec-list-modal")


@rt("/delete-spec")
async def delete_spec_route(req: Request):
    form = await req.form()
    filename = form.get("filename", "")
    if filename and _DOMINO_AVAILABLE:
        spec_store.delete_spec(filename)
    return spec_list()


@rt("/cleanup-specs")
def cleanup_specs():
    if _DOMINO_AVAILABLE:
        spec_store.delete_all_specs()
    return Response("OK", media_type="text/plain")


from starlette.middleware.trustedhost import TrustedHostMiddleware

# Domino Apps run on 0.0.0.0:8888 by default
# Use environment variables to allow configuration
HOST = os.environ.get("APP_HOST", "0.0.0.0")
PORT = int(os.environ.get("APP_PORT", "8888"))

# Add middleware for Domino's reverse proxy
# Allow all hosts since Domino uses dynamic URLs
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])

# Add CORS headers for Domino iframe embedding
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    # Allow embedding in Domino's iframe (X-Frame-Options for older browsers)
    response.headers["X-Frame-Options"] = "ALLOWALL"
    # Note: Domino already sets Content-Security-Policy with frame-ancestors, so we don't add it here
    # Allow htmx requests
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "HX-Request, HX-Target, HX-Current-URL, Content-Type"
    return response

def _reconcile_stale_jobs() -> None:
    """On startup, sync any jobs stuck in active states with Domino's actual status."""
    import sqlite3
    logger = logging.getLogger(__name__)
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
                logger.info("Marking stale queued job %s as failed (no Domino run ID)", job["id"])
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
                logger.info("Reconciled job %s (run %s): %s", job["id"], run_id, local_status)
            except Exception as exc:
                logger.warning("Failed to reconcile job %s (run %s): %s", job["id"], run_id, exc)
                domino_job_store.update_job(job["id"], status="failed", domino_status=f"Reconcile error: {exc}")
    except Exception as exc:
        logger.warning("Startup job reconciliation failed: %s", exc)


@app.on_event("startup")
async def _on_startup():
    global _POLL_TASK
    if _DOMINO_AVAILABLE:
        domino_job_store.init_db()
        _reconcile_stale_jobs()
        _POLL_TASK = asyncio.create_task(_poll_domino_jobs())


@app.on_event("shutdown")
async def _on_shutdown():
    global _POLL_TASK
    if _POLL_TASK:
        _POLL_TASK.cancel()


serve(host=HOST, port=PORT)
