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

logger = logging.getLogger(__name__)
from starlette.responses import FileResponse, Response, StreamingResponse

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
    import sys
    path = Path(__file__).resolve().parent / f"{name}.py"
    if not path.exists():
        raise FileNotFoundError(f"Sibling module not found: {path}")
    spec = _imputil.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module spec for {path}")
    mod = _imputil.module_from_spec(spec)
    # Register in sys.modules so @dataclass and other introspection works
    sys.modules[name] = mod
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
    auth_context = _import_sibling("auth_context")
    domino_datasets = _import_sibling("domino_datasets")
    _DOMINO_AVAILABLE = True
except Exception as _import_exc:
    logging.getLogger(__name__).warning("Domino modules unavailable: %s", _import_exc, exc_info=True)
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
    # execution_mode is auto-inferred: project_id present → "domino", else → "app"
    branch: Optional[str] = None
    hardware_tier: Optional[str] = None
    api_key_source: str = "domino_env"  # "domino_env" | "pass_now"
    spec_filename: Optional[str] = None  # original uploaded filename
    project_id: Optional[str] = None     # target Domino project (from ?projectId=)


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
    project_id: Optional[str] = None     # target Domino project ID


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
    """Return the default code root: /mnt/code for git projects,
    /mnt for DFS projects, or cwd as fallback."""
    if Path("/mnt/code").exists():
        return Path("/mnt/code")
    if Path("/mnt").exists():
        return Path("/mnt")
    return Path(".")


def _get_default_spec_path() -> Path:
    return Path(__file__).resolve().parent / "doc_spec.yaml"


@dataclass
class EnvironmentWarning:
    """A startup environment warning."""
    level: str   # "info" | "warning" | "error"
    message: str
    action: str  # suggested action


def _validate_environment() -> list:
    """Validate environment and return warnings. Never raises."""
    warnings = []
    code_root = _get_default_code_root()

    # Check code directory
    if not code_root.exists() or code_root == Path("."):
        warnings.append(EnvironmentWarning(
            level="warning",
            message="Code directory not found at /mnt/code.",
            action="Documents will be generated from MLflow artifacts only.",
        ))

    # Check MLflow
    if not os.environ.get("MLFLOW_TRACKING_URI"):
        warnings.append(EnvironmentWarning(
            level="info",
            message="MLflow not configured.",
            action="Document generation will use code analysis only.",
        ))

    # Check Domino API (only if Domino env detected)
    if os.environ.get("DOMINO_PROJECT_ID"):
        if not os.environ.get("DOMINO_API_HOST"):
            warnings.append(EnvironmentWarning(
                level="warning",
                message="Domino API host not configured.",
                action="Job submission may fail. Set DOMINO_API_HOST.",
            ))

    # Ensure output directory exists
    try:
        _get_default_output_dir()
    except Exception as exc:
        warnings.append(EnvironmentWarning(
            level="error",
            message=f"Could not create output directory: {exc}",
            action="Check disk permissions.",
        ))

    # Ensure cache directory exists
    try:
        Path(".autodoc_cache").mkdir(exist_ok=True)
    except Exception:
        pass  # Non-critical

    return warnings


_STARTUP_WARNINGS: list = []


def _render_warnings_banner(warnings: list) -> list:
    """Render environment warnings as dismissible HTML banners."""
    if not warnings:
        return []
    banners = []
    style_map = {
        "info": "background: #EEF6FF; border: 1px solid #B3D4FC; color: #1A4971;",
        "warning": "background: #FFF8E1; border: 1px solid #FFE082; color: #5D4037;",
        "error": "background: #FFEBEE; border: 1px solid #EF9A9A; color: #B71C1C;",
    }
    for w in warnings:
        style = style_map.get(w.level, style_map["info"])
        banners.append(
            Div(
                Span(f"{w.message} {w.action}", style="flex: 1;"),
                Button(
                    "\u00d7", type="button",
                    style="background: none; border: none; font-size: 1.2rem; cursor: pointer; padding: 0 0.5rem;",
                    onclick="this.parentElement.remove();",
                ),
                style=f"{style} padding: 0.5rem 0.75rem; border-radius: 6px; margin-bottom: 0.5rem; "
                      "display: flex; align-items: center; font-size: 0.875rem;",
            )
        )
    return banners


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
        project_id=row.get("project_id"),
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

    # Queue-full explanation for queued jobs
    queue_banner = None
    if status == "queued" and not record.run_id:
        max_j = _max_jobs()
        queue_banner = Div(
            Span("⚠ "),
            Span(f"Job queued — you already have {max_j} active job{'s' if max_j != 1 else ''}. "
                 "It will start automatically when a slot opens. To free a slot: stop a running job above, "
                 "or switch to the History tab and use "),
            Span("Cancel queued", style="font-weight: 600;"),
            Span(" to remove pending jobs."),
            style="background: rgba(204,183,24,0.1); border: 1px solid rgba(204,183,24,0.3); "
                  "border-radius: 6px; padding: 0.5rem 0.75rem; margin-bottom: 0.75rem; "
                  "font-size: 0.8125rem; color: var(--text-primary); line-height: 1.5;",
            role="alert",
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
        if status == "queued" and not record.run_id:
            status_lines.append("Waiting for a slot to open...")
        else:
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
        queue_banner,
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
        branch_val = j.get("branch") or "—"
        tier_val = j.get("hardware_tier") or "—"
        rows.append(
            Tr(
                Td(branch_val, title=branch_val),
                Td(tier_val, title=tier_val),
                Td(Span(j.get("status", "—").upper(), cls=status_cls)),
                Td((j.get("submitted_at") or "—")[:16].replace("T", " ")),
                link_cell,
            )
        )

    return Div(
        Div(
            Table(
                Thead(
                    Tr(
                        Th("Branch"),
                        Th("Tier"),
                        Th("Status"),
                        Th("Submitted"),
                        Th("Link"),
                    )
                ),
                Tbody(*rows),
                cls="history-table",
            ),
            cls="history-table-wrap",
        ),
        Div(
            A(
                "Clear completed",
                hx_post="clear-job-history",
                hx_target="#job-history-content",
                hx_swap="innerHTML",
                cls="terminal-action",
            ),
            A(
                "Cancel queued",
                hx_post="cancel-queued-jobs",
                hx_target="#job-history-content",
                hx_swap="innerHTML",
                cls="terminal-action",
                title="Cancel all queued jobs that haven't been submitted yet",
            ) if any(j.get("status") == "queued" and not j.get("run_id") for j in jobs) else None,
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
    parts = _build_job_command(req, spec_path)
    cli_cmd = " ".join(parts)
    output_dir = req.output_dir or "/mnt/data"
    artifacts_dir = "/mnt/artifacts/auto_ml"
    return (
        f"{cli_cmd}"
        f" && mkdir -p {artifacts_dir}"
        f" && cp -r {output_dir}/* {artifacts_dir}/"
    )


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
                    console.log('htmx loaded and functional');
                } else {
                    console.log('htmx not functional, using vanilla JS');
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
                        .catch(function(e) { console.log('Status fetch error:', e); });
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
                        .catch(function(e) { console.log('Status check error:', e); });
                }

                // Initialise version from DOM
                _lastLogVersion = getCurrentVersion();

                // Start smart polling for app mode
                var formEl = document.getElementById('main-form');
                var inferredMode = formEl ? formEl.getAttribute('data-execution-mode') : 'app';
                if (inferredMode !== 'domino') {
                    setInterval(smartPoll, 2000);
                }

                // Re-activate polling when a new job starts (after form submit)
                window._activateStatusPolling = function() {
                    _pollActive = true;
                    _lastLogVersion = -1; // Force an immediate update
                    _tabInitialized = false; // Reset so next swap shows Output tab
                    showOutputTab('live'); // Immediately show Output tab on new job
                };

                // Direct click handler on Generate button
                var generateBtn = document.getElementById('generate-btn');
                if (generateBtn) {
                    generateBtn.addEventListener('click', function(e) {
                        if (htmxWorking) return;
                        e.preventDefault();
                        e.stopPropagation();
                        // Block submission if spec validation failed
                        if (window._specValid === false) {
                            var resultEl = document.getElementById('spec-validation-result');
                            if (resultEl) resultEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
                            return;
                        }
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
                        .catch(function(e) {
                            console.log('Form submit error:', e);
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
            .cross-project-banner {
                margin-top: 0.5rem;
                padding: 0.5rem 0.75rem;
                background: #EDECFB;
                border: 1px solid #C9C5F2;
                border-radius: 6px;
                color: #1820A0;
                font-size: 0.875rem;
            }
            #project-id-resolved {
                font-size: 0.78rem;
                color: var(--text-muted);
                padding: 0.25rem 0 0 0.15rem;
            }
            #project-id-resolved.resolved {
                color: #1820A0;
                font-weight: 500;
            }
            #project-id-resolved.error {
                color: var(--error);
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

            /* Dataset spec browser */
            .spec-breadcrumb {
                display: flex;
                align-items: center;
                gap: 4px;
                font-size: 0.8125rem;
                color: #7F8385;
                padding: 4px 0;
                flex-wrap: wrap;
            }
            .spec-breadcrumb-link {
                color: #3B3BD3;
                cursor: pointer;
                text-decoration: none;
            }
            .spec-breadcrumb-link:hover { text-decoration: underline; }
            .spec-breadcrumb-sep { color: #DBE4E8; margin: 0 2px; }
            .spec-breadcrumb-current { color: #3F4547; font-weight: 600; }
            .spec-file-list {
                border: 1px solid #DBE4E8;
                border-radius: 6px;
                max-height: 220px;
                overflow-y: auto;
                background: #fff;
            }
            .spec-file-item {
                display: flex;
                align-items: center;
                gap: 8px;
                padding: 8px 12px;
                font-size: 0.875rem;
                cursor: pointer;
                border-bottom: 1px solid #f0f0f0;
                transition: background 0.1s;
            }
            .spec-file-item:last-child { border-bottom: none; }
            .spec-file-item:hover { background: #f7f7fb; }
            .spec-file-item.selected { background: #EDECFB; }
            .spec-file-icon { flex-shrink: 0; width: 18px; text-align: center; }
            .spec-file-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
            .spec-file-size { color: #7F8385; font-size: 0.75rem; flex-shrink: 0; }
            .spec-file-empty {
                padding: 24px 12px;
                text-align: center;
                color: #7F8385;
                font-size: 0.875rem;
            }
            .spec-actions-row {
                display: flex;
                align-items: center;
                gap: 12px;
                padding-top: 8px;
                flex-wrap: wrap;
            }
            .spec-upload-status {
                font-size: 0.8125rem;
                color: #7F8385;
            }
            .spec-validation-error {
                background: #fef2f2;
                border: 1px solid #fecaca;
                border-radius: 6px;
                padding: 0.5rem 0.75rem;
                margin-top: 0.375rem;
                font-size: 0.8125rem;
                color: #991b1b;
            }
            .spec-validation-error ul { color: #7f1d1d; }

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
                transition: background 0.3s ease, color 0.3s ease;
            }
            @keyframes fadeIn {
                from { opacity: 0; transform: translateY(4px); }
                to { opacity: 1; transform: translateY(0); }
            }
            .log-line {
                animation: fadeIn 0.2s ease forwards;
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
            .history-table-wrap {
                overflow-x: auto;
                -webkit-overflow-scrolling: touch;
            }
            .history-table {
                width: 100%;
                border-collapse: collapse;
                font-size: 0.8rem;
                min-width: 420px;
            }
            .history-table th {
                text-align: left;
                color: var(--text-secondary);
                font-weight: 600;
                font-size: 0.75rem;
                padding: 0 0.5rem 0.5rem 0;
                border-bottom: 1px solid var(--panel-border);
                white-space: nowrap;
            }
            .history-table td {
                padding: 0.5rem 0.5rem 0.5rem 0;
                color: var(--text-primary);
                border-bottom: 1px solid var(--panel-border);
                max-width: 120px;
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
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
            """
        ),
        Script(f"""
            const DOMINO_OUTPUT_DEFAULT = {json.dumps(str(_get_default_output_dir()))};
            const APP_OUTPUT_DEFAULT = "/mnt/code/output";
        """),
        Script(
            r"""
            document.addEventListener('DOMContentLoaded', function() {

                // ── Auto-fill projectId from URL or postMessage ──
                // Domino Apps run inside a cross-origin iframe; the proxy strips
                // query params.  Try what we can; the user can always type it manually.
                (function() {
                    function setProjectId(pid) {
                        if (!pid) return;
                        var input = document.getElementById('field-project-id');
                        if (input && !input.value) {
                            input.value = pid;
                            input.dataset.autoDocSet = 'true';
                            input.dispatchEvent(new Event('change'));
                        }
                    }
                    var pid = null;
                    // Diagnostic: log origin info
                    if (window.parent !== window) {
                        try {
                        } catch(e) {
                        }
                    }
                    // 1. Own query string (direct / non-proxied access)
                    pid = new URLSearchParams(window.location.search).get('projectId');
                    // 2. Own hash fragment (#projectId=xxx — survives proxies)
                    if (!pid && window.location.hash) {
                        var h = window.location.hash.substring(1);
                        if (h.charAt(0) === '?') h = h.substring(1);
                        pid = new URLSearchParams(h).get('projectId');
                    }
                    // 3. Parent frame (same-origin deployments where parent
                    //    and iframe share the same host)
                    if (!pid && window.parent !== window) {
                        try {
                            var pLoc = window.parent.location;
                            pid = new URLSearchParams(pLoc.search).get('projectId');
                            if (!pid && pLoc.hash) {
                                var ph = pLoc.hash.substring(1);
                                if (ph.charAt(0) === '?') ph = ph.substring(1);
                                pid = new URLSearchParams(ph).get('projectId');
                            }
                        } catch(e) { /* cross-origin — ignore */ }
                    }
                    if (pid) {
                        setProjectId(pid);
                    }
                    // 4. Listen for postMessage from Domino parent frame
                    window.addEventListener('message', function(e) {
                        if (e.data && typeof e.data === 'object' && e.data.projectId) {
                            setProjectId(e.data.projectId);
                        }
                    });
                })();

                // ── Language detection ────────────────────────────────────────────
                var langRow = document.getElementById('lang-detection-row');
                var langName = document.getElementById('lang-detected-name');
                var langCount = document.getElementById('lang-detected-count');
                var langInput = document.getElementById('field-detected-language');
                var langSelect = document.getElementById('lang-override-select');

                function detectLanguage(codeRoot) {
                    var url = 'api/detect-language';
                    if (codeRoot) url += '?code_root=' + encodeURIComponent(codeRoot);
                    fetch(url)
                        .then(function(r) { return r.json(); })
                        .then(function(data) {
                            if (langRow) langRow.style.display = '';
                            if (data.language) {
                                if (langName) langName.textContent = data.display_name;
                                if (langCount) langCount.textContent = '(' + data.file_count + ' files)';
                                if (langInput) langInput.value = data.language;
                                if (langSelect) langSelect.value = data.language;
                            } else {
                                if (langName) langName.textContent = '';
                                if (langCount) langCount.textContent = '';
                                if (langRow) {
                                    langRow.innerHTML = '<span style="color:#7F8385;">No supported source files found. Supports Python, R, SAS, MATLAB.</span>';
                                    langRow.style.display = '';
                                }
                            }
                        })
                        .catch(function() {});
                }

                window.handleLanguageOverride = function(lang) {
                    if (langInput) langInput.value = lang;
                    var cr = document.getElementById('field-code_root');
                    detectLanguage(cr ? cr.value : undefined);
                };

                function detectLanguageFromCodeRoot() {
                    var cr = document.getElementById('field-code_root');
                    detectLanguage(cr ? cr.value : undefined);
                }

                detectLanguageFromCodeRoot();

                // ── All DOM references declared up-front to avoid TDZ errors ──────
                // Mode toggle removed — mode is auto-inferred server-side
                const uploadBtnLabel    = document.querySelector('label.upload-btn');
                // specSavedName removed — replaced by dataset browser UI
                const appModeNote       = document.getElementById('app-mode-note');
                const appNoteHint       = document.getElementById('app-mode-notebook-hint');
                const apiKeyPassField   = document.getElementById('api-key-pass-field');
                const apiKeyCallout     = document.getElementById('api-key-callout');
                const apiKeySourceRadios = document.querySelectorAll('input[name="api_key_source"]');
                const providerSelect    = document.getElementById('field-provider');
                const baseUrlField      = document.getElementById('base-url-field');
                const modelNameField    = document.getElementById('model-name-field');

                // ── Mode is server-rendered (no toggle) ─────────────────────────
                // Domino fields are conditionally rendered server-side.
                // Nothing to toggle at runtime.

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

                // ── Resolve project, refresh tiers & output dir on change ─────
                var projectIdInput = document.getElementById('field-project-id');
                if (projectIdInput) {
                    var refreshTimer = null;
                    function onProjectIdChange() {
                        clearTimeout(refreshTimer);
                        refreshTimer = setTimeout(function() {
                            var pid = projectIdInput.value.trim();
                            var qs = pid ? '?projectId=' + encodeURIComponent(pid) : '';
                            // Resolve project name
                            fetch('api/resolve-project' + qs)
                                .then(function(r) { return r.text(); })
                                .then(function(html) {
                                    var el = document.getElementById('project-id-resolved');
                                    if (el) el.outerHTML = html;
                                    // Update output dir from resolved name
                                    var newEl = document.getElementById('project-id-resolved');
                                    var name = newEl ? newEl.getAttribute('data-project-name') : null;
                                    var outputDir = document.getElementById('field-output_dir');
                                    if (outputDir) {
                                        outputDir.value = name ? '/mnt/data/' + name : DOMINO_OUTPUT_DEFAULT;
                                    }
                                })
                                .catch(function() {});
                            // Refresh hardware tiers
                            if (typeof htmx !== 'undefined') {
                                htmx.ajax('GET', 'api/hardware-tiers' + qs, {
                                    target: '#field-hardware_tier',
                                    swap: 'outerHTML'
                                });
                            }
                        }, 300);
                    }
                    projectIdInput.addEventListener('change', onProjectIdChange);
                    projectIdInput.addEventListener('blur', onProjectIdChange);
                }

                // ── Dataset spec browser (Domino mode) ───────────────────────────
                var specDatasetSelect = document.getElementById('spec-dataset-select');
                var specFileList = document.getElementById('spec-file-list');
                var specBreadcrumb = document.getElementById('spec-breadcrumb');
                var specSelectedIndicator = document.getElementById('spec-selected-indicator');
                var specSelectedName = document.getElementById('spec-selected-name');
                var specMachineUpload = document.getElementById('spec-machine-upload');
                var specUploadStatus = document.getElementById('spec-upload-status');
                var specPathHidden = document.getElementById('field-spec_path');

                // State
                var _specDatasets = [];
                var _specCurrentDatasetId = '';
                var _specCurrentDatasetName = '';
                var _specCurrentSnapshotId = '';
                var _specCurrentPath = '';
                var _specAutoDocSpecsId = '';

                function getProjectIdParam() {
                    var formEl = document.getElementById('main-form');
                    var pid = '';
                    // Check projectId from query string
                    var params = new URLSearchParams(window.location.search);
                    pid = params.get('projectId') || params.get('project_id') || '';
                    // Also check the project-id field
                    if (!pid) {
                        var pidInput = document.getElementById('field-project-id');
                        if (pidInput) pid = pidInput.value.trim();
                    }
                    return pid ? '&projectId=' + encodeURIComponent(pid) : '';
                }

                function loadDatasets() {
                    if (!specDatasetSelect) return;
                    console.log('[spec-browser] Loading writable datasets...');
                    var qs = '?' + getProjectIdParam().replace(/^&/, '');
                    fetch('api/datasets' + qs)
                        .then(function(r) { return r.json(); })
                        .then(function(datasets) {
                            if (datasets.error) {
                                console.error('[spec-browser] Error loading datasets:', datasets.error);
                                specDatasetSelect.innerHTML = '<option value="">Error: ' + datasets.error + '</option>';
                                return;
                            }
                            _specDatasets = datasets;
                            console.log('[spec-browser] Loaded ' + datasets.length + ' datasets:', datasets.map(function(d) { return d.name; }));
                            if (datasets.length === 0) {
                                specDatasetSelect.innerHTML = '<option value="">No datasets found for this project</option>';
                                console.warn('[spec-browser] No writable datasets returned — upload a spec file to auto-create one');
                                return;
                            }
                            var html = '<option value="">Choose a dataset...</option>';
                            for (var i = 0; i < datasets.length; i++) {
                                html += '<option value="' + datasets[i].id + '" data-name="' + datasets[i].name + '" data-snapshot="' + (datasets[i].rwSnapshotId || '') + '">'
                                    + datasets[i].name + '</option>';
                            }
                            specDatasetSelect.innerHTML = html;

                            // Auto-select autodoc-specs if it exists
                            for (var j = 0; j < datasets.length; j++) {
                                if (datasets[j].name === 'autodoc-specs') {
                                    specDatasetSelect.value = datasets[j].id;
                                    _specAutoDocSpecsId = datasets[j].id;
                                    onDatasetChange();
                                    return;
                                }
                            }
                        })
                        .catch(function(err) {
                            console.error('[spec-browser] Failed to load datasets:', err);
                            specDatasetSelect.innerHTML = '<option value="">Failed to load datasets</option>';
                        });
                }

                function onDatasetChange() {
                    if (!specDatasetSelect) return;
                    var opt = specDatasetSelect.options[specDatasetSelect.selectedIndex];
                    console.log('[spec-browser] Dataset selected:', opt ? opt.getAttribute('data-name') : 'none');
                    _specCurrentDatasetId = specDatasetSelect.value;
                    _specCurrentDatasetName = opt ? opt.getAttribute('data-name') || '' : '';
                    _specCurrentSnapshotId = opt ? opt.getAttribute('data-snapshot') || '' : '';
                    _specCurrentPath = '';
                    if (_specCurrentDatasetId) {
                        browseFiles('');
                    } else {
                        if (specFileList) specFileList.innerHTML = '<span class="spec-file-empty">Select a dataset to browse spec files</span>';
                        if (specBreadcrumb) specBreadcrumb.innerHTML = '';
                    }
                }

                function browseFiles(path) {
                    _specCurrentPath = path;
                    if (!specFileList) return;
                    console.log('[spec-browser] Browsing path:', path || '(root)', 'in dataset:', _specCurrentDatasetName);
                    specFileList.innerHTML = '<span class="spec-file-empty">Loading...</span>';
                    renderBreadcrumb(path);

                    var qs = '?datasetId=' + encodeURIComponent(_specCurrentDatasetId);
                    if (_specCurrentSnapshotId) qs += '&snapshotId=' + encodeURIComponent(_specCurrentSnapshotId);
                    if (path) qs += '&path=' + encodeURIComponent(path);
                    qs += getProjectIdParam();

                    fetch('api/dataset-files' + qs)
                        .then(function(r) { return r.json(); })
                        .then(function(files) {
                            if (files.error) {
                                console.error('[spec-browser] File listing error:', files.error);
                                specFileList.innerHTML = '<span class="spec-file-empty">Error: ' + files.error + '</span>';
                                return;
                            }
                            console.log('[spec-browser] Found ' + files.length + ' items at path:', path || '(root)');
                            if (files.length === 0) {
                                specFileList.innerHTML = '<span class="spec-file-empty">No YAML files found in this location</span>';
                                return;
                            }
                            var html = '';
                            // Sort: directories first, then files
                            files.sort(function(a, b) {
                                if (a.isDirectory && !b.isDirectory) return -1;
                                if (!a.isDirectory && b.isDirectory) return 1;
                                return a.fileName.localeCompare(b.fileName);
                            });
                            for (var i = 0; i < files.length; i++) {
                                var f = files[i];
                                var icon = f.isDirectory ? '\ud83d\udcc1' : '\ud83d\udcc4';
                                var size = f.isDirectory ? '' : formatBytes(f.sizeInBytes || 0);
                                var fullPath = path ? path + '/' + f.fileName : f.fileName;
                                html += '<div class="spec-file-item" data-path="' + fullPath + '" data-dir="' + f.isDirectory + '" data-name="' + f.fileName + '">'
                                    + '<span class="spec-file-icon">' + icon + '</span>'
                                    + '<span class="spec-file-name">' + f.fileName + '</span>'
                                    + '<span class="spec-file-size">' + size + '</span>'
                                    + '</div>';
                            }
                            specFileList.innerHTML = html;

                            // Attach click handlers
                            var items = specFileList.querySelectorAll('.spec-file-item');
                            for (var j = 0; j < items.length; j++) {
                                items[j].addEventListener('click', onFileClick);
                            }
                        })
                        .catch(function() {
                            specFileList.innerHTML = '<span class="spec-file-empty">Failed to load files</span>';
                        });
                }

                function onFileClick(e) {
                    var el = e.currentTarget;
                    var isDir = el.getAttribute('data-dir') === 'true';
                    var path = el.getAttribute('data-path');
                    if (isDir) {
                        browseFiles(path);
                    } else {
                        // Select this file
                        var items = specFileList.querySelectorAll('.spec-file-item');
                        for (var i = 0; i < items.length; i++) items[i].classList.remove('selected');
                        el.classList.add('selected');
                        selectSpecFile(_specCurrentDatasetName, path);
                    }
                }

                function selectSpecFile(datasetName, filePath) {
                    console.log('[spec-browser] Selected:', datasetName + '/' + filePath);
                    if (specSelectedIndicator) specSelectedIndicator.style.display = '';
                    if (specSelectedName) specSelectedName.textContent = datasetName + '/' + filePath;
                    // Build mount path and set the hidden form field
                    // The server will resolve the correct mount prefix
                    if (specPathHidden) {
                        // Use a marker so the server knows this is a dataset reference
                        specPathHidden.value = 'dataset://' + datasetName + '/' + filePath;
                    }
                }

                function renderBreadcrumb(path) {
                    if (!specBreadcrumb) return;
                    var parts = path ? path.split('/').filter(Boolean) : [];
                    var html = '<span class="spec-breadcrumb-link" onclick="window._specBrowse(\'\')">root</span>';
                    var cumulative = '';
                    for (var i = 0; i < parts.length; i++) {
                        cumulative += (i > 0 ? '/' : '') + parts[i];
                        html += '<span class="spec-breadcrumb-sep">/</span>';
                        if (i === parts.length - 1) {
                            html += '<span class="spec-breadcrumb-current">' + parts[i] + '</span>';
                        } else {
                            html += '<span class="spec-breadcrumb-link" onclick="window._specBrowse(\'' + cumulative + '\')">' + parts[i] + '</span>';
                        }
                    }
                    specBreadcrumb.innerHTML = html;
                }

                function formatBytes(bytes) {
                    if (bytes === 0) return '';
                    if (bytes < 1024) return bytes + ' B';
                    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
                    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
                }

                // Global for breadcrumb onclick
                window._specBrowse = function(path) { browseFiles(path); };

                // Upload from machine → autodoc-specs dataset
                if (specMachineUpload) {
                    specMachineUpload.addEventListener('change', function(e) {
                        var file = e.target.files[0];
                        if (!file) return;
                        console.log('[spec-browser] Upload from machine:', file.name, '(' + file.size + ' bytes)');
                        if (specUploadStatus) { specUploadStatus.textContent = 'Uploading ' + file.name + '...'; specUploadStatus.style.color = ''; }
                        // Validate spec content before uploading
                        if (typeof validateSpecContent === 'function') validateSpecContent(file);

                        // Ensure autodoc-specs dataset exists, then upload
                        var qs = '?' + getProjectIdParam().replace(/^&/, '');
                        fetch('api/ensure-autodoc-specs' + qs, { method: 'POST' })
                            .then(function(r) { return r.json(); })
                            .then(function(ds) {
                                if (ds.error) throw new Error(ds.error);
                                console.log('[spec-browser] autodoc-specs dataset ensured: id=' + ds.id);
                                _specAutoDocSpecsId = ds.id;
                                var fd = new FormData();
                                fd.append('datasetId', ds.id);
                                fd.append('datasetName', ds.name || 'autodoc-specs');
                                fd.append('file', file);
                                return fetch('api/upload-spec-to-dataset' + qs, { method: 'POST', body: fd });
                            })
                            .then(function(r) { return r.json(); })
                            .then(function(result) {
                                if (result.error) throw new Error(result.error);
                                console.log('[spec-browser] Upload success:', result.fileName, '→', result.mountPath);
                                if (specUploadStatus) { specUploadStatus.textContent = 'Uploaded: ' + result.fileName; specUploadStatus.style.color = '#2e7d32'; }
                                // Select the uploaded file
                                selectSpecFile('autodoc-specs', result.fileName);
                                // Refresh datasets if autodoc-specs was just created
                                loadDatasets();
                            })
                            .catch(function(err) {
                                console.error('[spec-browser] Upload failed:', err.message);
                                if (specUploadStatus) { specUploadStatus.textContent = 'Upload failed: ' + err.message; specUploadStatus.style.color = '#c62828'; }
                            });
                    });
                }

                // Wire dataset select change
                if (specDatasetSelect) {
                    specDatasetSelect.addEventListener('change', onDatasetChange);
                    loadDatasets();
                }

                // ── Toggle base URL and model name fields based on provider selection
                var OPENAI_DEFAULT_MODEL = 'kimi-k2-0905-preview';
                var ANTHROPIC_DEFAULT_MODEL = 'claude-sonnet-4-20250514';
                function toggleOpenAIFields() {
                    const isOpenAI = providerSelect && providerSelect.value === 'openai';
                    if (baseUrlField) {
                        baseUrlField.style.display = isOpenAI ? 'flex' : 'none';
                    }
                    if (modelNameField) {
                        modelNameField.style.display = isOpenAI ? 'flex' : 'none';
                    }
                    var modelInput = document.getElementById('field-model');
                    if (modelInput) {
                        if (isOpenAI) {
                            if (!modelInput.value || modelInput.value === ANTHROPIC_DEFAULT_MODEL) {
                                modelInput.value = OPENAI_DEFAULT_MODEL;
                            }
                        } else {
                            if (!modelInput.value || modelInput.value === OPENAI_DEFAULT_MODEL) {
                                modelInput.value = ANTHROPIC_DEFAULT_MODEL;
                            }
                        }
                    }
                }

                if (providerSelect) {
                    providerSelect.addEventListener('change', toggleOpenAIFields);
                    toggleOpenAIFields();
                }
                
                // Handle file upload and update spec path display (app mode)
                var specUploadApp = document.querySelector('input[name="spec_upload"]');
                var specPathDisplay = document.getElementById('field-spec_path_display');
                var specPathHiddenApp = document.getElementById('field-spec_path');
                var uploadFilenameEl = document.getElementById('upload-filename');

                // ── Spec validation helper ────────────────────────────────────
                window._specValid = true; // tracks latest validation state
                function validateSpecContent(file) {
                    var fd = new FormData();
                    fd.append('spec_upload', file);
                    var resultEl = document.getElementById('spec-validation-result');
                    if (resultEl) resultEl.innerHTML = '<span style="color:var(--text-muted);font-size:0.8125rem;">Validating spec...</span>';
                    fetch('validate-spec', { method: 'POST', body: fd })
                        .then(function(r) { return r.text(); })
                        .then(function(html) {
                            if (resultEl) resultEl.outerHTML = html;
                            // Check if validation passed
                            window._specValid = html.indexOf('validation failed') === -1;
                        })
                        .catch(function() {
                            if (resultEl) resultEl.innerHTML = '';
                            window._specValid = true; // don't block on network errors
                        });
                }

                if (specUploadApp && specPathDisplay) {
                    specUploadApp.addEventListener('change', function(e) {
                        var file = e.target.files[0];
                        if (file) {
                            specPathDisplay.value = '[Uploaded] ' + file.name;
                            specPathDisplay.disabled = true;
                            if (specPathHiddenApp) specPathHiddenApp.value = '[Uploaded] ' + file.name;
                            if (uploadFilenameEl) uploadFilenameEl.textContent = 'Using uploaded file: ' + file.name;
                            validateSpecContent(file);
                        } else {
                            specPathDisplay.disabled = false;
                            if (uploadFilenameEl) uploadFilenameEl.textContent = '';
                            var resultEl = document.getElementById('spec-validation-result');
                            if (resultEl) resultEl.innerHTML = '';
                            window._specValid = true;
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
                    if (suffix) {
                        suffix.addEventListener('input', sync);
                        var langTimer = null;
                        suffix.addEventListener('input', function() {
                            clearTimeout(langTimer);
                            langTimer = setTimeout(function() { detectLanguageFromCodeRoot(); }, 400);
                        });
                    }
                    sync();
                })();

                // Run styling/scrolling after any status update (HTMX swap or smart poll)
                function onStatusUpdate() {
                    styleTerminalLines();
                    scrollTerminalToBottom();
                }

                // One-shot flag: show Output tab on first swap only (form submit),
                // not on subsequent polling swaps (which would reset the user's tab choice).
                var _tabInitialized = false;
                document.body.addEventListener('htmx:afterSwap', function(e) {
                    if (e.detail && e.detail.target && e.detail.target.id === 'status-panel') {
                        if (!_tabInitialized) {
                            showOutputTab('live');
                            _tabInitialized = true;
                        }
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

    # Capture projectId from query string (fallback for non-proxied access;
    # Domino's reverse proxy strips query params from the iframe URL).
    project_id = req.query_params.get("projectId") or None

    # If a cross-project ID was given, resolve its metadata eagerly so the
    # cache is warm for later job submissions and hardware-tier lookups.
    project_display_name: Optional[str] = None
    if project_id and _DOMINO_AVAILABLE:
        info = domino_client.resolve_project(project_id)
        if info:
            project_display_name = f"{info.owner_username}/{info.name}"

    default_spec = _get_default_spec_path()
    username = _get_username()
    try:
        _settings = Settings()
        _current_model = "kimi-k2-0905-preview"
        _current_base_url = _settings.openai_base_url or "https://api.moonshot.ai/v1"
    except Exception:
        _current_model = "kimi-k2-0905-preview"
        _current_base_url = "https://api.moonshot.ai/v1"

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

    # Auto-infer execution mode: projectId or Domino env → domino, else → app
    inferred_mode = "app"
    if project_id and _DOMINO_AVAILABLE:
        inferred_mode = "domino"
    elif not project_id and _DOMINO_AVAILABLE and os.environ.get("DOMINO_PROJECT_ID"):
        inferred_mode = "domino"
    default_mode = inferred_mode

    # Pre-fetch branches and hardware tiers for server-side rendering
    if _DOMINO_AVAILABLE:
        try:
            _branches_raw = domino_client.list_branches()
            branch_options = [Option(b["name"], value=b["name"]) for b in _branches_raw]
        except Exception:
            branch_options = []
        if not branch_options:
            branch_options = [Option("main", value="main"), Option("master", value="master")]
        try:
            tier_data = domino_client.list_hardware_tiers(project_id=project_id)
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
            # Environment warnings (dismissible)
            *_render_warnings_banner(_STARTUP_WARNINGS),
            # Mode auto-inferred: projectId or DOMINO_PROJECT_ID → domino, else → app
            Div(
                Span("Detected: ", style="color: #7F8385;"),
                Span(id="lang-detected-name", style="color: #3F4547; font-weight: 600;"),
                Span(id="lang-detected-count", style="color: #7F8385; margin-left: 4px;"),
                Button(
                    "Override",
                    id="lang-override-btn",
                    type="button",
                    style="background: none; border: none; color: #3B3BD3; cursor: pointer; "
                          "padding: 8px 12px; min-height: 44px; font-size: inherit; margin-left: 8px;",
                    aria_label="Override detected language",
                    onclick="document.getElementById('lang-override-select').style.display = "
                            "document.getElementById('lang-override-select').style.display === 'none' ? 'inline-block' : 'none';",
                ),
                Select(
                    Option("Python", value="python"),
                    Option("R", value="r"),
                    Option("SAS", value="sas"),
                    Option("MATLAB", value="matlab"),
                    id="lang-override-select",
                    style="display: none; border: 1px solid #DBE4E8; border-radius: 4px; "
                          "padding: 4px 8px; margin-left: 4px;",
                    onchange="handleLanguageOverride(this.value)",
                ),
                id="lang-detection-row",
                style="display: none; padding: 6px 16px; font-size: 14px;",
            ),
            Div(
            Form(
                Input(type="hidden", name="detected_language", id="field-detected-language", value="python"),
                # Three cards stacked vertically: What to document | Run | Advanced
                Div(
                    # Card 1: What to document (spec + artifact filtering)
                    Div(
                        Div("What to document", cls="card-title"),
                        # Hidden field that stores the resolved spec path for form submission
                        Input(name="spec_path", id="field-spec_path", type="hidden",
                              value=str(default_spec) if default_mode == "app" else ""),
                        # ── Domino mode: dataset browser ──────────────────────
                        *([ Div(
                            Label("Spec file", Span(" *", cls="required-star")),
                            # Dataset selector
                            Div(
                                Select(
                                    Option("Loading datasets...", value="", disabled=True, selected=True),
                                    id="spec-dataset-select",
                                ),
                                cls="field",
                            ),
                            # Breadcrumb navigation
                            Div(id="spec-breadcrumb", cls="spec-breadcrumb"),
                            # File browser
                            Div(
                                Span("Select a dataset to browse spec files", style="color: #7F8385; font-size: 0.875rem;"),
                                id="spec-file-list",
                                cls="spec-file-list",
                            ),
                            # Selected file indicator
                            Div(
                                Span("Selected: ", style="color: #7F8385;"),
                                Span(id="spec-selected-name", style="font-weight: 600; color: #3F4547;"),
                                id="spec-selected-indicator",
                                style="display: none; padding: 8px 0; font-size: 0.875rem;",
                            ),
                            # Upload from machine + download template
                            Div(
                                Label(
                                    "Upload from my machine",
                                    Input(
                                        type="file",
                                        accept=".yaml,.yml",
                                        id="spec-machine-upload",
                                        cls="hidden-upload",
                                    ),
                                    cls="upload-btn",
                                ),
                                Span(id="spec-upload-status", cls="spec-upload-status"),
                                A("Download reference template", href="api/download-template",
                                  download="doc_spec_template.yaml",
                                  style="color: #3B3BD3; font-size: 0.875rem; margin-left: auto;"),
                                cls="spec-actions-row",
                            ),
                            cls="field",
                        )] if default_mode == "domino" else [
                        # ── App mode: simple file path + upload ───────────────
                        Div(
                            Label("Spec file", Span(" *", cls="required-star"), for_="field-spec_path_display"),
                            Div(
                                Input(
                                    id="field-spec_path_display",
                                    type="text",
                                    value=str(default_spec),
                                    placeholder=str(default_spec),
                                    oninput="document.getElementById('field-spec_path').value = this.value;",
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
                        )]),
                        Div(id="spec-validation-result"),
                        Details(
                            Summary("Filters", cls="advanced-section-summary"),
                            Div(
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
                                cls="advanced-content",
                            ),
                            cls="advanced-section",
                            open=True,
                        ),
                        cls="card",
                    ),
                    # Card 2: Run (paths, branch, hardware, API key)
                    Div(
                        Div("Run", cls="card-title"),
                        Div(
                            Label("Code root", for_="code-root-suffix"),
                            Div(
                                Span(str(_get_default_code_root()), id="code-root-prefix", cls="code-root-prefix"),
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
                                Label("Target project", for_="field-project-id"),
                                Span("ⓘ", cls="info-tooltip", data_tooltip="Domino project ID to run the job in. Leave blank to use the current project."),
                                cls="label-row",
                            ),
                            Input(
                                name="target_project",
                                id="field-project-id",
                                type="text",
                                value="",
                                placeholder="Leave blank for current project",
                                autocomplete="off",
                            ),
                            Div(
                                (f"{project_display_name}" if project_display_name else ""),
                                id="project-id-resolved",
                                cls="resolved" if project_display_name else "",
                            ),
                            cls="field domino-fields",
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
                        Details(
                            Summary("More run settings", cls="advanced-section-summary"),
                            Div(
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
                                        autocomplete="new-password",
                                        spellcheck="false",
                                    ),
                                    cls="field",
                                    id="api-key-pass-field",
                                    style="display: none;" if default_mode == "domino" else "",
                                ),
                                cls="advanced-content",
                            ),
                            cls="advanced-section",
                            open=True,
                        ),
                        cls="card",
                    ),
                    # Card 3: Advanced (collapsed by default — all fields optional)
                    Div(
                        Details(
                            Summary(
                                Span("Advanced", cls="card-title", style="margin-bottom: 0;"),
                                Span("Generation settings, provider, output options", cls="advanced-summary-desc"),
                                cls="advanced-section-summary",
                            ),
                            Div(
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
                                        Span("ⓘ", cls="info-tooltip", data_tooltip="Leave blank to use default (kimi-k2-0905-preview)"),
                                        cls="label-row",
                                    ),
                                    Input(name="model", id="field-model", type="text", value=_current_model, placeholder="kimi-k2-0905-preview"),
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
                                        placeholder="https://api.moonshot.ai/v1",
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
                                cls="advanced-content",
                            ),
                            cls="advanced-section",
                            open=True,
                        ),
                        cls="card card-advanced",
                    ),
                    cls="config-grid",
                ),
                id="main-form",
                data_execution_mode=inferred_mode,
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

    if job_request.project_id and _DOMINO_AVAILABLE:
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
def api_branches():
    """Return an HTML <select> fragment with available git branches."""
    if not _DOMINO_AVAILABLE:
        return Select(Option("(Domino not available)", value=""), name="branch", id="field-branch")
    branches = domino_client.list_branches()
    options = [Option(b.get("name", ""), value=b.get("name", "")) for b in branches]
    if not options:
        options = [Option("main", value="main"), Option("master", value="master")]
    return Select(*options, name="branch", id="field-branch")


@rt("/api/hardware-tiers")
def api_hardware_tiers(req: Request):
    """Return an HTML <select> fragment with available hardware tiers."""
    if not _DOMINO_AVAILABLE:
        return Select(Option("(Domino not available)", value=""), name="hardware_tier", id="field-hardware_tier")
    project_id = req.query_params.get("projectId") or None
    tiers = domino_client.list_hardware_tiers(project_id=project_id)
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


@rt("/status-progress")
def status_progress(req: Request):
    """Return progress bar HTML fragment for incremental polling."""
    job_id = req.query_params.get("job_id", "")
    job = JOB_STORE.get(job_id or ACTIVE_JOB_ID or "")
    if not job:
        return Div("", id="progress-bar-container")
    pct = int(job.progress * 100)
    return Div(
        Div(
            Div(
                style=f"width: {pct}%; height: 100%; background: var(--accent, #543FDE); "
                      "border-radius: 4px; transition: width 0.3s ease;",
            ),
            style="height: 6px; background: #E0E0E0; border-radius: 4px; overflow: hidden;",
        ),
        Span(f"{job.phase} — {pct}%", style="font-size: 0.8rem; color: #7F8385; margin-top: 4px;"),
        id="progress-bar-container",
    )


@rt("/status-badge")
def status_badge(req: Request):
    """Return status badge HTML fragment for incremental polling."""
    job_id = req.query_params.get("job_id", "")
    job = JOB_STORE.get(job_id or ACTIVE_JOB_ID or "")
    if not job:
        return Span("idle", cls="terminal-status terminal-status-idle", id="status-badge")
    badge_cls = f"terminal-status terminal-status-{job.status}"
    return Span(job.status.upper(), cls=badge_cls, id="status-badge")


@rt("/status-logs-since")
def status_logs_since(req: Request):
    """Return only new log lines since a given version for append-only updates."""
    job_id = req.query_params.get("job_id", "")
    since = int(req.query_params.get("since", "0"))
    job = JOB_STORE.get(job_id or ACTIVE_JOB_ID or "")
    if not job or since >= len(job.logs):
        return Response("", media_type="text/html")
    new_lines = job.logs[since:]
    html_lines = "".join(
        f'<div class="log-line" style="opacity:0;animation:fadeIn 0.2s forwards;">{line}</div>'
        for line in new_lines
    )
    return Response(html_lines, media_type="text/html")


@rt("/sse/job-stream")
async def sse_job_stream(req: Request):
    """SSE endpoint for real-time app-mode job updates."""
    job_id = req.query_params.get("job_id", "")

    # Only serve SSE for in-app jobs (JOB_STORE)
    job = JOB_STORE.get(job_id) if job_id else None
    if not job:
        return Response("Job not found", status_code=404)

    async def event_generator():
        last_log_count = 0
        last_status = ""
        last_progress = -1.0
        start_time = asyncio.get_event_loop().time()
        heartbeat_counter = 0

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > 300:  # 5-minute max lifetime
                yield "event: timeout\ndata: {}\n\n"
                break

            current_job = JOB_STORE.get(job_id)
            if not current_job:
                yield "event: error\ndata: {\"message\": \"Job not found\"}\n\n"
                break

            # Stream new log lines (append-only)
            if len(current_job.logs) > last_log_count:
                new_lines = current_job.logs[last_log_count:]
                for line in new_lines:
                    yield f"event: log\ndata: {json.dumps({'line': line})}\n\n"
                last_log_count = len(current_job.logs)

            # Stream progress changes
            if current_job.progress != last_progress:
                last_progress = current_job.progress
                yield f"event: progress\ndata: {json.dumps({'phase': current_job.phase, 'progress': current_job.progress})}\n\n"

            # Stream status changes
            if current_job.status != last_status:
                last_status = current_job.status
                yield f"event: status\ndata: {json.dumps({'status': current_job.status})}\n\n"

            # Check terminal state
            if current_job.status in ("complete", "error", "cancelled"):
                output_path = str(current_job.output_path) if current_job.output_path else None
                yield f"event: complete\ndata: {json.dumps({'status': current_job.status, 'output_path': output_path})}\n\n"
                break

            # Heartbeat every 30 iterations (0.5s * 60 = 30s)
            heartbeat_counter += 1
            if heartbeat_counter >= 60:
                yield "event: heartbeat\ndata: {}\n\n"
                heartbeat_counter = 0

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@rt("/api/detect-language")
def api_detect_language(req: Request):
    """Detect project language by counting source files in code_root."""
    from autodoc.core.models import detect_language as _detect_lang, LANGUAGE_PROFILES

    code_root_param = req.query_params.get("code_root", "")
    if code_root_param:
        code_root = Path(code_root_param)
    else:
        code_root = _get_default_code_root()

    profile, count = _detect_lang(code_root)
    if profile:
        return Response(
            json.dumps({
                "language": profile.name,
                "display_name": profile.display_name,
                "file_count": count,
            }),
            media_type="application/json",
        )
    return Response(
        json.dumps({
            "language": None,
            "display_name": None,
            "file_count": 0,
            "supported": list(LANGUAGE_PROFILES.keys()),
        }),
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# Dataset browsing & spec upload API
# ---------------------------------------------------------------------------

def _resolve_request_project_id(req: Request) -> Optional[str]:
    """Resolve project ID from query params or env."""
    for key in ("projectId", "project_id"):
        pid = req.query_params.get(key)
        if pid:
            return pid
    return os.environ.get("DOMINO_PROJECT_ID", "") or None


@rt("/api/datasets")
def api_datasets(req: Request):
    """List writable datasets for the project."""
    if not _DOMINO_AVAILABLE:
        return Response(json.dumps([]), media_type="application/json")
    pid = _resolve_request_project_id(req)
    logger.info("GET /api/datasets — project=%s", pid)
    try:
        datasets = domino_datasets.list_datasets(pid)
        logger.info("GET /api/datasets — returned %d datasets", len(datasets))
        return Response(json.dumps(datasets), media_type="application/json")
    except Exception as exc:
        logger.warning("Failed to list datasets: %s", exc, exc_info=True)
        return Response(
            json.dumps({"error": str(exc)}),
            status_code=500,
            media_type="application/json",
        )


@rt("/api/dataset-files")
def api_dataset_files(req: Request):
    """Browse files in a dataset (directories + yaml only)."""
    if not _DOMINO_AVAILABLE:
        return Response(json.dumps([]), media_type="application/json")

    dataset_id = req.query_params.get("datasetId", "")
    snapshot_id = req.query_params.get("snapshotId", "")
    path = req.query_params.get("path", "")
    pid = _resolve_request_project_id(req)

    if not dataset_id:
        return Response(
            json.dumps({"error": "datasetId required"}),
            status_code=400,
            media_type="application/json",
        )

    # Resolve snapshot ID if not provided
    if not snapshot_id:
        snapshot_id = domino_datasets.get_rw_snapshot_id(dataset_id, pid)
    if not snapshot_id:
        return Response(
            json.dumps({"error": "Could not resolve snapshot for dataset"}),
            status_code=400,
            media_type="application/json",
        )

    logger.info("GET /api/dataset-files — dataset=%s snapshot=%s path='%s'", dataset_id, snapshot_id, path)
    try:
        files = domino_datasets.list_files(snapshot_id, path, pid)
        logger.info("GET /api/dataset-files — returned %d items", len(files))
        return Response(json.dumps(files), media_type="application/json")
    except Exception as exc:
        logger.warning("Failed to list files: %s", exc, exc_info=True)
        return Response(
            json.dumps({"error": str(exc)}),
            status_code=500,
            media_type="application/json",
        )


@rt("/api/ensure-autodoc-specs")
async def api_ensure_autodoc_specs(req: Request):
    """Ensure the autodoc-specs dataset exists, return its metadata."""
    if not _DOMINO_AVAILABLE:
        return Response(
            json.dumps({"error": "Domino not available"}),
            status_code=400,
            media_type="application/json",
        )
    pid = _resolve_request_project_id(req)
    logger.info("POST /api/ensure-autodoc-specs — project=%s", pid)
    try:
        ds = domino_datasets.ensure_dataset(pid)
        if not ds.get("id"):
            logger.warning("ensure-autodoc-specs returned dataset with empty id: %s", ds)
            raise RuntimeError("Dataset was created/found but has no ID — check Domino Datasets API response")
        # Resolve snapshot if not included
        if not ds.get("rwSnapshotId"):
            ds["rwSnapshotId"] = domino_datasets.get_rw_snapshot_id(ds["id"], pid)
        logger.info("POST /api/ensure-autodoc-specs — dataset id=%s name=%s", ds.get("id"), ds.get("name"))
        return Response(json.dumps(ds), media_type="application/json")
    except Exception as exc:
        logger.warning("Failed to ensure autodoc-specs: %s", exc, exc_info=True)
        return Response(
            json.dumps({"error": str(exc)}),
            status_code=500,
            media_type="application/json",
        )


@rt("/api/upload-spec-to-dataset")
async def api_upload_spec_to_dataset(req: Request):
    """Upload a spec file from the user's machine into a dataset."""
    if not _DOMINO_AVAILABLE:
        return Response(
            json.dumps({"error": "Domino not available"}),
            status_code=400,
            media_type="application/json",
        )

    form = await req.form()
    dataset_id = form.get("datasetId", "")
    file_upload = form.get("file")
    pid = _resolve_request_project_id(req)

    if not dataset_id or not file_upload or not hasattr(file_upload, "read"):
        return Response(
            json.dumps({"error": "datasetId and file are required"}),
            status_code=400,
            media_type="application/json",
        )

    filename = getattr(file_upload, "filename", "spec.yaml")
    content = await file_upload.read()
    logger.info("POST /api/upload-spec-to-dataset — file='%s' (%d bytes) → dataset=%s", filename, len(content), dataset_id)

    try:
        domino_datasets.upload_file(dataset_id, filename, content, pid)
        dataset_name = form.get("datasetName", domino_datasets.AUTODOC_SPECS_DATASET)
        mount_path = domino_datasets.build_spec_mount_path(dataset_name, filename)
        logger.info("POST /api/upload-spec-to-dataset — success, mount=%s", mount_path)
        return Response(
            json.dumps({"mountPath": mount_path, "fileName": filename}),
            media_type="application/json",
        )
    except Exception as exc:
        logger.warning("Failed to upload spec: %s", exc, exc_info=True)
        return Response(
            json.dumps({"error": str(exc)}),
            status_code=500,
            media_type="application/json",
        )


@rt("/api/download-template")
def api_download_template():
    """Serve the bundled doc_spec.yaml as a downloadable reference template."""
    template_path = Path(__file__).resolve().parent / "doc_spec.yaml"
    if not template_path.exists():
        return Response("Template not found", status_code=404)
    return FileResponse(
        str(template_path),
        media_type="application/x-yaml",
        filename="doc_spec_template.yaml",
    )


@rt("/api/resolve-project")
def api_resolve_project(req: Request):
    """Return resolved project name for a given project ID."""
    pid = req.query_params.get("projectId", "").strip()
    if not pid or not _DOMINO_AVAILABLE:
        return Div(id="project-id-resolved")
    info = domino_client.resolve_project(pid)
    if info:
        return Div(
            f"{info.owner_username}/{info.name}",
            id="project-id-resolved",
            cls="resolved",
            data_project_name=info.name,
        )
    return Div(
        "Could not resolve project ID",
        id="project-id-resolved",
        cls="error",
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


@rt("/cancel-queued-jobs")
def cancel_queued_jobs():
    """Cancel all queued (not yet submitted) jobs for the current user."""
    username = _get_username()
    if _DOMINO_AVAILABLE:
        with domino_job_store._conn() as con:
            con.execute(
                """
                UPDATE domino_jobs
                SET status = 'cancelled'
                WHERE username = ? AND status = 'queued' AND run_id IS NULL
                """,
                (username,),
            )
    return _render_job_history_table(username)



@rt("/validate-spec")
async def validate_spec_route(req: Request):
    """Validate uploaded spec YAML and return inline feedback."""
    form = await req.form()
    spec_upload = form.get("spec_upload")
    content = None
    if spec_upload and hasattr(spec_upload, "read"):
        raw = await spec_upload.read()
        content = raw.decode("utf-8", errors="replace")
    else:
        content = form.get("spec_content")

    if not content or not content.strip():
        return Div(
            Span("No spec content to validate.", style="color: var(--text-muted);"),
            id="spec-validation-result",
        )

    errors = DocumentSpec.validate_spec(content)
    if errors:
        error_items = [Li(e) for e in errors]
        return Div(
            Div(
                Span("Spec validation failed", style="font-weight: 600; color: var(--error);"),
                Ul(*error_items, style="margin: 0.25rem 0 0 0; padding-left: 1.25rem; font-size: 0.8125rem;"),
                cls="spec-validation-error",
            ),
            id="spec-validation-result",
        )

    return Div(
        Span("Spec is valid", style="color: #2e7d32; font-weight: 500; font-size: 0.8125rem;"),
        id="spec-validation-result",
    )


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

# Capture the visiting user's JWT so outbound Domino API calls
# (datasets, jobs) run as the viewer, not the app owner.
@app.middleware("http")
async def capture_auth_context(request, call_next):
    if _DOMINO_AVAILABLE:
        forwarded = request.headers.get("authorization")
        auth_context.set_request_auth_header(forwarded)
    try:
        response = await call_next(request)
    finally:
        if _DOMINO_AVAILABLE:
            auth_context.set_request_auth_header(None)
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
    global _POLL_TASK, _STARTUP_WARNINGS
    _STARTUP_WARNINGS = _validate_environment()
    for w in _STARTUP_WARNINGS:
        logger.warning(f"Startup: [{w.level}] {w.message} {w.action}")
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
