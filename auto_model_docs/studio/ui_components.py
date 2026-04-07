"""FT (FastHTML) UI component helpers for the Stitch UI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from fasthtml.common import *

from .state import (
    DominoJobRecord,
    EnvironmentWarning,
    _DOMINO_AVAILABLE,
    _get_default_code_root,
    _max_jobs,
    domino_job_store,
)


# ---------------------------------------------------------------------------
# Sanitizers / parsers
# ---------------------------------------------------------------------------

def _sanitize_optional_int(value: Optional[str]) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _sanitize_optional_float(value: Optional[str]) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


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

    # Output and cache are managed via DatasetStore — no local directories needed.

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
# Status panels
# ---------------------------------------------------------------------------

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
            hx_post="stop-job-history",
            hx_vals=json.dumps({"job_id": record.id}),
            hx_target="#job-history-content",
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
    try:
        jobs = domino_job_store.get_user_jobs(username, limit=50)
    except RuntimeError:
        # Project not yet resolved (pre-bootstrap page load)
        return Div()
    if not jobs:
        return Div(
            P("No jobs submitted yet.", cls="history-empty"),
        )

    _ACTIVE_STATUSES = {"queued", "submitted", "pending", "running"}

    def _job_row(j):
        status = j.get("status", "queued")
        status_cls = f"history-status history-status-{status}"
        job_url = j.get("job_url")
        link_cell = Td(
            A("View \u2192", href=job_url, target="_blank") if job_url else "\u2014"
        )
        branch_val = j.get("branch") or "\u2014"
        tier_val = j.get("hardware_tier") or "\u2014"
        action_cell = Td()
        if status in _ACTIVE_STATUSES:
            action_cell = Td(
                A(
                    "Stop",
                    hx_post="stop-job-history",
                    hx_vals=json.dumps({"job_id": j.get("id", "")}),
                    hx_target="#job-history-content",
                    hx_swap="innerHTML",
                    cls="terminal-action",
                    title="Stop this job",
                ),
            )
        return Tr(
            Td(branch_val, title=branch_val),
            Td(tier_val, title=tier_val),
            Td(Span(status.upper(), cls=status_cls)),
            Td((j.get("submitted_at") or "\u2014")[:16].replace("T", " ")),
            link_cell,
            action_cell,
        )

    active_jobs = [j for j in jobs if j.get("status", "queued") in _ACTIVE_STATUSES]
    completed_jobs = [j for j in jobs if j.get("status", "queued") not in _ACTIVE_STATUSES]

    header = Thead(Tr(
        Th("Branch"), Th("Tier"), Th("Status"), Th("Submitted"), Th("Link"), Th(""),
    ))

    # Queue-full warning when any job is queued
    queue_banner = None
    has_queued = any(j.get("status") == "queued" and not j.get("domino_run_id") for j in jobs)
    if has_queued:
        max_j = _max_jobs()
        queue_banner = Div(
            Span("\u26a0 "),
            Span(f"Job queued \u2014 you already have {max_j} active job{'s' if max_j != 1 else ''}. "
                 "It will start automatically when a slot opens. To free a slot, stop a running job or use "),
            Span("Cancel queued", style="font-weight: 600;"),
            Span(" below."),
            style="background: rgba(144,68,0,0.06); border-left: 3px solid #904400; "
                  "border-radius: 2px; padding: 0.625rem 1rem; margin-bottom: 0.75rem; "
                  "font-size: 0.8125rem; color: #191b22; line-height: 1.5; font-family: Inter, sans-serif;",
            role="alert",
        )

    sections = [queue_banner]

    # Active jobs — always visible
    if active_jobs:
        sections.append(Div(
            Table(header, Tbody(*[_job_row(j) for j in active_jobs]), cls="history-table"),
            cls="history-table-wrap",
        ))

    # Completed jobs — collapsible
    if completed_jobs:
        n = len(completed_jobs)
        label = f"Show {n} completed job{'s' if n != 1 else ''}"
        sections.append(
            Details(
                Summary(label, cls="history-toggle"),
                Div(
                    Table(header, Tbody(*[_job_row(j) for j in completed_jobs]), cls="history-table"),
                    cls="history-table-wrap",
                ),
                # Auto-open when there are no active jobs
                open=not active_jobs,
            )
        )

    # Actions row — only cancel-queued when relevant
    if has_queued:
        sections.append(Div(
            A(
                "Cancel queued",
                hx_post="cancel-queued-jobs",
                hx_target="#job-history-content",
                hx_swap="innerHTML",
                cls="terminal-action",
                title="Cancel all queued jobs that haven't been submitted yet",
            ),
            cls="history-actions",
        ))

    return Div(*sections)
