"""FT (FastHTML) UI component helpers for the Stitch UI."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fasthtml.common import *

from .state import (
    JobState,
    DominoJobRecord,
    EnvironmentWarning,
    _DOMINO_AVAILABLE,
    _get_default_code_root,
    _get_default_output_dir,
    _max_jobs,
    domino_job_store,
)


# ---------------------------------------------------------------------------
# Sanitizers / parsers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Environment validation
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Form field helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Warnings banner
# ---------------------------------------------------------------------------

def _render_warnings_banner(warnings: list) -> list:
    """Render environment warnings as dismissible HTML banners."""
    if not warnings:
        return []
    banners = []
    style_map = {
        "info": "background: rgba(93,95,239,0.06); border-left: 3px solid #5d5fef; color: #191b22;",
        "warning": "background: rgba(144,68,0,0.06); border-left: 3px solid #904400; color: #191b22;",
        "error": "background: rgba(186,26,26,0.06); border-left: 3px solid #ba1a1a; color: #191b22;",
    }
    for w in warnings:
        style = style_map.get(w.level, style_map["info"])
        banners.append(
            Div(
                Span(f"{w.message} {w.action}", style="flex: 1; font-family: Inter, sans-serif; font-size: 0.8125rem;"),
                Button(
                    "\u00d7", type="button",
                    style="background: none; border: none; font-size: 1.2rem; cursor: pointer; padding: 0 0.5rem; color: #464555;",
                    onclick="this.parentElement.remove();",
                ),
                style=f"{style} padding: 0.625rem 1rem; border-radius: 2px; margin-bottom: 0.5rem; "
                      "display: flex; align-items: center;",
            )
        )
    return banners


# ---------------------------------------------------------------------------
# Progress bar
# ---------------------------------------------------------------------------

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
                        Span("\u2713", cls="phase-check"),
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


# ---------------------------------------------------------------------------
# Status panels
# ---------------------------------------------------------------------------

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


def _render_domino_status(record: Optional[DominoJobRecord]) -> FT:
    """Render the terminal panel for a Domino job."""
    if not record:
        return Div(
            Div(
                H3("Domino job"),
                cls="terminal-header",
            ),
            Div(
                "Click Generate Documentation to start.",
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
            "View job in Domino \u2192",
            href=record.job_url,
            target="_blank",
            cls="domino-job-link",
        )

    # Queue-full explanation for queued jobs
    queue_banner = None
    if status == "queued" and not record.domino_run_id:
        max_j = _max_jobs()
        queue_banner = Div(
            Span("\u26a0 "),
            Span(f"Job queued \u2014 you already have {max_j} active job{'s' if max_j != 1 else ''}. "
                 "It will start automatically when a slot opens. To free a slot: stop a running job above, "
                 "or switch to the History tab and use "),
            Span("Cancel queued", style="font-weight: 600;"),
            Span(" to remove pending jobs."),
            style="background: rgba(144,68,0,0.06); border-left: 3px solid #904400; "
                  "border-radius: 2px; padding: 0.625rem 1rem; margin-bottom: 0.75rem; "
                  "font-size: 0.8125rem; color: #191b22; line-height: 1.5; font-family: Inter, sans-serif;",
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
        if status == "queued" and not record.domino_run_id:
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


# ---------------------------------------------------------------------------
# Job history table
# ---------------------------------------------------------------------------

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
            A("View \u2192", href=job_url, target="_blank") if job_url else "\u2014"
        )
        branch_val = j.get("branch") or "\u2014"
        tier_val = j.get("hardware_tier") or "\u2014"
        rows.append(
            Tr(
                Td(branch_val, title=branch_val),
                Td(tier_val, title=tier_val),
                Td(Span(j.get("status", "\u2014").upper(), cls=status_cls)),
                Td((j.get("submitted_at") or "\u2014")[:16].replace("T", " ")),
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
            ) if any(j.get("status") == "queued" and not j.get("domino_run_id") for j in jobs) else None,
            cls="history-actions",
        ),
        id="job-history-content",
        cls="job-history-content",
    )
