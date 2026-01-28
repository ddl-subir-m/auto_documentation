#!/usr/bin/env python3
"""FastHTML UI for Auto Model Documentation."""

from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
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

# Rich console for terminal output
console = Console()


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


JOB_STORE: dict[str, JobState] = {}
ACTIVE_JOB_ID: Optional[str] = None
LAST_API_KEY: Optional[str] = None


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _log(job: JobState, message: str) -> None:
    job.logs.append(f"[{_timestamp()}] {message}")
    job.updated_at = datetime.utcnow()


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
            Div("Awaiting your first run...", cls="terminal terminal-idle"),
            cls="terminal-card",
        )

    log_text = "\n".join(job.logs[-200:]) if job.logs else "Initializing..."
    status_text = job.status.upper()
    if job.status == "completed":
        status_text = "COMPLETED"
    elif job.status == "failed":
        status_text = "FAILED"
    elif job.status == "cancelled":
        status_text = "CANCELLED"

    is_running = job.status == "running"
    stop_link = A(
        "Stop",
        hx_post="stop",
        hx_target="#status-panel",
        hx_swap="innerHTML",
        cls="terminal-action",
    ) if is_running else A(
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
                    cls="download-btn",
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
    )




async def _run_generation(job: JobState, request: JobRequest) -> None:
    progress_ctx = None
    try:
        global LAST_API_KEY
        job.status = "running"
        _log(job, "Preparing generation run.")

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
            spec_path = output_dir / "doc_spec.uploaded.yaml"
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
            job.notebook_path = (
                Path(request.notebook_path)
                if request.notebook_path
                else output_dir / "model_docs_notebook.ipynb"
            )
        job.status = "completed"
        
        console.print("\n[bold green]Generation complete![/bold green]")
        _log(job, "Generation complete.")
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


async def _parse_request(req: Request) -> JobRequest:
    form = await req.form()
    spec_upload = form.get("spec_upload")
    spec_content = None
    if spec_upload and hasattr(spec_upload, "read"):
        content = await spec_upload.read()
        spec_content = content.decode("utf-8", errors="replace")

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
    )


def _start_job(job_request: JobRequest) -> JobState:
    global ACTIVE_JOB_ID
    job = JobState(id=str(uuid4()))
    JOB_STORE[job.id] = job
    ACTIVE_JOB_ID = job.id

    job.task = asyncio.create_task(_run_generation(job, job_request))
    return job


app, rt = fast_app(
    # Disable default CDN headers and use permissive settings for Domino
    pico=False,  # Disable pico CSS CDN if causing issues
    hdrs=(
        # Load htmx synchronously to ensure it's ready before user interaction
        Script(src="https://unpkg.com/htmx.org@1.9.10"),
        # Fallback vanilla JS polling if htmx fails to load
        Script(r"""
            // Robust form handling that works regardless of HTMX state
            // (Domino CSP may block or interfere with external scripts)
            window.addEventListener('DOMContentLoaded', function() {
                var htmxWorking = false;
                
                // Test if htmx is actually functional
                if (typeof htmx !== 'undefined' && typeof htmx.ajax === 'function') {
                    htmxWorking = true;
                    console.log('htmx loaded and functional');
                } else {
                    console.log('htmx not functional, using vanilla JS');
                }
                
                // Status polling - always set up as backup
                function pollStatus() {
                    var panel = document.getElementById('status-panel');
                    if (!panel) return;
                    
                    fetch('status')
                        .then(function(r) { return r.text(); })
                        .then(function(html) { panel.innerHTML = html; })
                        .catch(function(e) { console.log('Status poll error:', e); });
                }
                
                // Start polling if htmx isn't working (htmx would handle its own polling)
                if (!htmxWorking) {
                    setInterval(pollStatus, 2000);
                }
                
                // Direct click handler on Generate button - works regardless of htmx
                var generateBtn = document.getElementById('generate-btn');
                if (generateBtn) {
                    generateBtn.addEventListener('click', function(e) {
                        // If htmx is working, let it handle the submission
                        if (htmxWorking) {
                            return; // htmx will handle it
                        }
                        
                        // Otherwise, handle manually
                        e.preventDefault();
                        e.stopPropagation();
                        
                        var form = document.querySelector('form');
                        if (!form) return;
                        
                        var formData = new FormData(form);
                        
                        // Disable button to prevent double-clicks
                        generateBtn.disabled = true;
                        generateBtn.textContent = 'Starting...';
                        
                        fetch('run', {
                            method: 'POST',
                            body: formData
                        })
                        .then(function(r) { return r.text(); })
                        .then(function(html) {
                            var panel = document.getElementById('status-panel');
                            if (panel) panel.innerHTML = html;
                            // Re-enable button
                            generateBtn.disabled = false;
                            generateBtn.textContent = 'Generate Documentation';
                            // Start polling for updates
                            if (!htmxWorking) {
                                pollStatus();
                            }
                        })
                        .catch(function(e) {
                            console.log('Form submit error:', e);
                            generateBtn.disabled = false;
                            generateBtn.textContent = 'Generate Documentation';
                        });
                    });
                }
                
                // Also handle form submit event as backup
                var form = document.querySelector('form');
                if (form && !htmxWorking) {
                    form.addEventListener('submit', function(e) {
                        e.preventDefault();
                        // Trigger the button click handler
                        var btn = document.getElementById('generate-btn');
                        if (btn) btn.click();
                    });
                }
                
                // Handle stop and clear button clicks via event delegation
                document.addEventListener('click', function(e) {
                    var target = e.target;
                    
                    // Stop button
                    if (target.textContent === 'Stop' && !target.classList.contains('terminal-action-disabled')) {
                        if (htmxWorking) return; // let htmx handle it
                        e.preventDefault();
                        fetch('stop', { method: 'POST' })
                            .then(function(r) { return r.text(); })
                            .then(function(html) {
                                var panel = document.getElementById('status-panel');
                                if (panel) panel.innerHTML = html;
                            });
                    }
                    
                    // Clear button
                    if (target.textContent === 'Clear' && !target.classList.contains('terminal-action-disabled')) {
                        if (htmxWorking) return; // let htmx handle it
                        e.preventDefault();
                        fetch('clear-terminal', { method: 'POST' })
                            .then(function(r) { return r.text(); })
                            .then(function(html) {
                                var panel = document.getElementById('status-panel');
                                if (panel) panel.innerHTML = html;
                            });
                    }
                });
            });
        """),
        Style(
            """
            :root {
                --panel: #101827;
                --panel-border: #1f2937;
                --terminal: #0b1220;
                --accent: #3b82f6;
                --accent-hover: #60a5fa;
                --accent-glow: rgba(59, 130, 246, 0.08);
                --text-primary: #f9fafb;
                --text-secondary: #e5e7eb;
                --text-muted: #94a3b8;
            }
            html, body {
                margin: 0;
                padding: 0;
                min-height: 100%;
                background: #0f172a;
            }
            body {
                color: var(--text-secondary);
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            }
            h1, h2, h3, h4 { color: var(--text-primary); margin: 0; }
            a { color: #60a5fa; text-decoration: none; transition: color 0.2s ease; }
            a:hover { color: var(--accent-hover); }
            
            /* Page Layout */
            .page {
                max-width: 1100px;
                margin: 0 auto;
                padding: 2rem 2rem;
                box-sizing: border-box;
                width: 100%;
            }
            @media (min-width: 1400px) {
                .page {
                    max-width: 1200px;
                }
            }
            .hero {
                text-align: center;
                padding: 1rem 0 1.5rem 0;
            }
            .hero h1 {
                font-size: 1.75rem;
                font-weight: 700;
                margin-bottom: 0.5rem;
                color: #f9fafb;
            }
            .hero p {
                color: var(--text-muted);
                font-size: 0.95rem;
                margin: 0;
            }
            
            /* Grid Layout - 2 columns */
            .config-grid {
                display: grid;
                grid-template-columns: 1.5fr 1fr;
                gap: 1rem;
                margin-bottom: 1.5rem;
            }
            @media (max-width: 700px) {
                .config-grid {
                    grid-template-columns: 1fr;
                }
            }
            
            /* Cards */
            .card {
                background: var(--panel);
                border: 1px solid var(--panel-border);
                border-radius: 10px;
                padding: 1.25rem;
                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
                transition: border-color 0.2s ease, transform 0.2s ease;
            }
            .card:hover {
                border-color: rgba(59, 130, 246, 0.3);
            }
            .card-title {
                font-size: 0.75rem;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0.05em;
                color: var(--text-muted);
                margin-bottom: 1rem;
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
                color: #cbd5e1;
                font-size: 0.8rem;
                font-weight: 500;
            }
            .field input[type="text"],
            .field input[type="number"],
            .field select {
                background: #0f172a;
                border: 1px solid var(--panel-border);
                border-radius: 6px;
                padding: 0.625rem 0.75rem;
                color: var(--text-secondary);
                font-size: 0.875rem;
                transition: border-color 0.2s ease, box-shadow 0.2s ease;
            }
            .field input:focus,
            .field select:focus {
                outline: none;
                border-color: var(--accent);
                box-shadow: 0 0 0 3px var(--accent-glow);
            }
            .field input::placeholder {
                color: #64748b;
            }
            .field select {
                cursor: pointer;
            }
            
            /* Inline field with upload button */
            .field-inline {
                display: flex;
                gap: 0.5rem;
                align-items: stretch;
            }
            .field-inline input[type="text"] {
                flex: 1;
            }
            .upload-btn {
                background: #1e293b;
                border: 1px solid var(--panel-border);
                border-radius: 6px;
                padding: 0 0.875rem;
                color: var(--text-muted);
                font-size: 0.8rem;
                font-weight: 500;
                cursor: pointer;
                transition: all 0.2s ease;
                display: flex;
                align-items: center;
                gap: 0.35rem;
            }
            .upload-btn:hover {
                background: #334155;
                color: var(--text-secondary);
                border-color: var(--accent);
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
                color: var(--text-secondary);
                font-size: 0.875rem;
                font-weight: 500;
            }
            .field-hint {
                margin-top: -0.5rem;
                margin-bottom: 1rem;
                padding-left: 1.625rem;
            }
            .notebook-path-hint {
                font-size: 0.75rem;
                color: #64748b;
                font-family: ui-monospace, monospace;
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
                color: var(--text-secondary);
            }
            .advanced-content {
                padding-top: 0.75rem;
            }
            .advanced-grid {
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 0.75rem;
            }
            .advanced-grid .field {
                margin-bottom: 0;
            }
            
            /* Filtering Section */
            .filter-section {
                margin-top: 1rem;
                padding-top: 1rem;
                border-top: 1px solid var(--panel-border);
            }
            .filter-section-title {
                font-size: 0.7rem;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0.05em;
                color: var(--text-muted);
                margin-bottom: 0.75rem;
            }
            .filter-section .field {
                margin-bottom: 0.75rem;
            }
            .filter-section .checkbox-field {
                margin-top: 0.5rem;
            }
            .field-hint-text {
                display: block;
                font-size: 0.7rem;
                color: var(--text-muted);
                margin-top: 0.25rem;
            }
            
            /* Primary Button */
            .btn-row {
                display: flex;
                justify-content: center;
                margin-bottom: 1.5rem;
            }
            button.primary {
                background: var(--accent);
                border: none;
                border-radius: 8px;
                padding: 0.75rem 2rem;
                color: white;
                font-size: 0.9rem;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.2s ease;
                box-shadow: 0 2px 4px rgba(0, 0, 0, 0.2);
            }
            button.primary:hover {
                box-shadow: 0 4px 8px rgba(0, 0, 0, 0.25);
            }
            button.primary:active {
                transform: translateY(0);
            }
            
            /* Terminal Card */
            .terminal-card {
                background: var(--panel);
                border: 1px solid var(--panel-border);
                border-radius: 10px;
                padding: 1rem 1.25rem;
                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
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
                color: #e2e8f0;
                text-decoration: none;
                cursor: pointer;
                padding: 0.375rem 0.875rem;
                border-radius: 5px;
                background: #334155;
                border: 1px solid #475569;
                transition: all 0.2s ease;
                font-weight: 500;
            }
            .terminal-action:hover {
                color: #fff;
                background: #475569;
                border-color: var(--accent);
            }
            .terminal-action-disabled {
                opacity: 0.4;
                pointer-events: none;
                background: #1e293b;
                border-color: var(--panel-border);
                color: #64748b;
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
                background: #1e293b;
                color: var(--text-muted);
            }
            .terminal-status-running {
                background: rgba(59, 130, 246, 0.15);
                color: var(--accent);
            }
            .terminal-status-completed {
                background: rgba(34, 197, 94, 0.15);
                color: #22c55e;
            }
            .terminal-status-failed {
                background: rgba(239, 68, 68, 0.15);
                color: #ef4444;
            }
            .terminal-status-cancelled {
                background: rgba(245, 158, 11, 0.15);
                color: #f59e0b;
            }
            
            /* Progress Phases */
            .progress-phases {
                display: flex;
                gap: 0.375rem;
                margin-bottom: 0.75rem;
                padding: 0.625rem;
                background: var(--terminal);
                border-radius: 6px;
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
                font-size: 0.65rem;
                font-weight: 600;
                color: #475569;
                text-transform: uppercase;
                letter-spacing: 0.03em;
            }
            .phase-pct {
                font-size: 0.6rem;
                color: var(--text-muted);
                font-family: ui-monospace, monospace;
            }
            .phase-check {
                color: #22c55e;
                font-size: 0.65rem;
            }
            .phase-bar {
                height: 4px;
                background: #1e293b;
                border-radius: 2px;
                overflow: hidden;
            }
            .phase-bar-fill {
                height: 100%;
                background: #3b82f6;
                border-radius: 2px;
                transition: width 0.3s ease;
            }
            .phase-bar-complete .phase-bar-fill {
                background: #22c55e;
            }
            @keyframes shimmer {
                0% { background-position: 100% center; }
                100% { background-position: 0% center; }
            }
            .phase-active .phase-name {
                background: linear-gradient(
                    90deg,
                    rgba(96, 165, 250, 0.5) 0%,
                    rgba(96, 165, 250, 0.5) 40%,
                    rgba(96, 165, 250, 1) 50%,
                    rgba(96, 165, 250, 0.5) 60%,
                    rgba(96, 165, 250, 0.5) 100%
                );
                background-size: 200% 100%;
                background-clip: text;
                -webkit-background-clip: text;
                color: transparent;
                animation: shimmer 2s linear infinite;
            }
            .phase-complete .phase-name {
                color: #22c55e;
            }
            .phase-pending .phase-name {
                color: #334155;
            }
            /* Terminal Output */
            .terminal {
                background: var(--terminal);
                border-radius: 6px;
                padding: 0.875rem;
                font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
                font-size: 0.8rem;
                line-height: 1.5;
                color: #cbd5e1;
                min-height: 120px;
                max-height: 300px;
                overflow-y: auto;
                white-space: pre-wrap;
                margin-top: 0.5rem;
            }
            .terminal-idle {
                min-height: 80px;
                color: #475569;
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
                background: #22c55e;
                color: white;
                border-radius: 6px;
                font-size: 0.85rem;
                font-weight: 600;
                text-decoration: none;
                transition: all 0.2s ease;
                box-shadow: 0 2px 4px rgba(0, 0, 0, 0.2);
            }
            .download-btn:hover {
                box-shadow: 0 4px 8px rgba(0, 0, 0, 0.25);
                color: white;
            }
            .download-btn::before {
                content: '↓';
                font-size: 1rem;
            }
            /* Terminal line styling */
            .terminal-line-active {
                color: #60a5fa;
            }
            .terminal-line-complete {
                color: #22c55e;
            }
            """
        ),
        Script(
            r"""
            document.addEventListener('DOMContentLoaded', function() {
                const notebookCheckbox = document.getElementById('field-notebook');
                const notebookHint = document.getElementById('notebook-path-hint');

                function toggleNotebookHint() {
                    if (notebookCheckbox && notebookHint) {
                        notebookHint.classList.toggle('hidden', !notebookCheckbox.checked);
                    }
                }

                if (notebookCheckbox) {
                    notebookCheckbox.addEventListener('change', toggleNotebookHint);
                    toggleNotebookHint();
                }

                // Toggle base URL field based on provider selection
                const providerSelect = document.getElementById('field-provider');
                const baseUrlField = document.getElementById('base-url-field');

                function toggleBaseUrlField() {
                    if (providerSelect && baseUrlField) {
                        baseUrlField.style.display = providerSelect.value === 'openai' ? 'flex' : 'none';
                    }
                }

                if (providerSelect) {
                    providerSelect.addEventListener('change', toggleBaseUrlField);
                    toggleBaseUrlField();
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

                // Run on load and whenever htmx swaps content
                document.body.addEventListener('htmx:afterSwap', function() {
                    styleTerminalLines();
                    scrollTerminalToBottom();
                });
                setInterval(styleTerminalLines, 500);
            });
            """
        ),
    )
)


@rt("/")
def index():
    default_spec = _get_default_spec_path()
    return Titled(
        "Auto Model Docs Studio",
        Div(
            # Hero Section
            Div(
                P("Generate model documentation with a single, guided workflow."),
                cls="hero",
            ),
            Form(
                # Two-column config grid
                Div(
                    # Left card: Main Configuration
                    Div(
                        Div("Configuration", cls="card-title"),
                        # Spec file with inline upload
                        Div(
                            Label("Spec file", for_="field-spec_path"),
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
                        # Code root
                        Div(
                            Label("Code root", for_="field-code_root"),
                            Input(
                                name="code_root",
                                id="field-code_root",
                                type="text",
                                placeholder=str(_get_default_code_root()),
                            ),
                            cls="field",
                        ),
                        # Output directory
                        Div(
                            Label("Output directory", for_="field-output_dir"),
                            Input(
                                name="output_dir",
                                id="field-output_dir",
                                type="text",
                                placeholder=str(_get_default_output_dir()),
                            ),
                            cls="field",
                        ),
                        cls="card",
                    ),
                    # Right card: Options
                    Div(
                        Div("Options", cls="card-title"),
                        # Provider dropdown
                        Div(
                            Label("Provider", for_="field-provider"),
                            Select(
                                Option("Anthropic", value="anthropic"),
                                Option("OpenAI", value="openai"),
                                name="provider",
                                id="field-provider",
                            ),
                            cls="field",
                        ),
                        # API key (in-memory only while app is open)
                        Div(
                            Label("API key", for_="field-api_key"),
                            Input(
                                name="api_key",
                                id="field-api_key",
                                type="password",
                                placeholder="Paste your API key",
                                autocomplete="off",
                                spellcheck="false",
                            ),
                            cls="field",
                        ),
                        # Base URL (only shown for OpenAI provider)
                        Div(
                            Label("Base URL", for_="field-base_url"),
                            Input(
                                name="base_url",
                                id="field-base_url",
                                type="text",
                                placeholder="https://api.openai.com/v1 (optional)",
                            ),
                            Span("For OpenAI-compatible APIs (e.g., Moonshot, Azure)", cls="field-hint-text"),
                            cls="field",
                            id="base-url-field",
                            style="display: none;",
                        ),
                        # Generate notebook checkbox (checked by default)
                        Label(
                            Input(type="checkbox", name="notebook", id="field-notebook", checked=True),
                            Span("Generate notebook"),
                            cls="checkbox-field",
                        ),
                        # Notebook output path hint
                        Div(
                            Span(f"{_get_default_output_dir()}/model_docs_notebook.ipynb", cls="notebook-path-hint"),
                            id="notebook-path-hint",
                            cls="field-hint",
                        ),
                        # Advanced section (collapsible)
                        Details(
                            Summary("Advanced options"),
                            Div(
                                Div(
                                    Div(
                                        Label("Max files", for_="field-max_files"),
                                        Input(
                                            name="max_files",
                                            id="field-max_files",
                                            type="number",
                                            value="50",
                                        ),
                                        cls="field",
                                    ),
                                    Div(
                                        Label("Planning workers", for_="field-planning_workers"),
                                        Input(
                                            name="planning_workers",
                                            id="field-planning_workers",
                                            type="number",
                                            value="1",
                                        ),
                                        cls="field",
                                    ),
                                    Div(
                                        Label("Generation workers", for_="field-workers"),
                                        Input(
                                            name="workers",
                                            id="field-workers",
                                            type="number",
                                            value="4",
                                        ),
                                        cls="field",
                                    ),
                                    Div(
                                        Label("Timeout", for_="field-timeout"),
                                        Input(
                                            name="timeout",
                                            id="field-timeout",
                                            type="number",
                                            value="120",
                                        ),
                                        cls="field",
                                    ),
                                    cls="advanced-grid",
                                ),
                                # Filtering subsection
                                Div(
                                    Div("Artifact Filtering", cls="filter-section-title"),
                                    Div(
                                        Label("Experiment names", for_="field-experiment_names"),
                                        Input(
                                            name="experiment_names",
                                            id="field-experiment_names",
                                            type="text",
                                            placeholder="exp1, exp2, my-experiment*",
                                        ),
                                        Span("Comma-separated. Supports wildcards: * and ?", cls="field-hint-text"),
                                        cls="field",
                                    ),
                                    Div(
                                        Label("Model names", for_="field-model_names"),
                                        Input(
                                            name="model_names",
                                            id="field-model_names",
                                            type="text",
                                            placeholder="model1, churn*, fraud-*",
                                        ),
                                        Span("Comma-separated. Supports wildcards: * and ?", cls="field-hint-text"),
                                        cls="field",
                                    ),
                                    Label(
                                        Input(type="checkbox", name="latest_only", id="field-latest_only"),
                                        Span("Latest version only"),
                                        cls="checkbox-field",
                                    ),
                                    cls="filter-section",
                                ),
                                cls="advanced-content",
                            ),
                            cls="advanced-section",
                        ),
                        cls="card",
                    ),
                    cls="config-grid",
                ),
                # Generate button
                Div(
                    Button("Generate Documentation", type="submit", id="generate-btn", cls="primary"),
                    cls="btn-row",
                ),
                hx_post="run",
                hx_target="#status-panel",
                hx_swap="innerHTML",
                hx_encoding="multipart/form-data",
                enctype="multipart/form-data",
            ),
            # Terminal panel - render initial state directly, then poll for updates
            Div(
                _render_status(_resolve_job(ACTIVE_JOB_ID)),
                id="status-panel",
                hx_get="status",
                hx_trigger="every 2s",
                hx_swap="innerHTML",
            ),
            cls="page",
        ),
    )


@rt("/run")
async def run(req: Request):
    active = _resolve_job(ACTIVE_JOB_ID)
    if active and active.status == "running":
        _log(active, "A job is already running. Please wait for completion.")
        return _render_status(active)

    job_request = await _parse_request(req)
    job = _start_job(job_request)
    _log(job, "Job submitted.")
    return _render_status(job)


@rt("/status")
def status():
    job = _resolve_job(ACTIVE_JOB_ID)
    return _render_status(job)


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


import os
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

serve(host=HOST, port=PORT)
