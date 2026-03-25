"""Job-related routes: run, status, stop, download, Domino status, history, SSE."""

from __future__ import annotations

import asyncio
import json
from typing import Optional
from uuid import uuid4

from fasthtml.common import *
from starlette.requests import Request
from starlette.responses import Response, FileResponse, StreamingResponse

from .state import (
    JobState,
    DominoJobRecord,
    JOB_STORE,
    ACTIVE_JOB_ID,
    _DOMINO_AVAILABLE,
    _log,
    _resolve_job,
    _get_username,
    domino_client,
    domino_job_store,
)
from .ui_components import (
    _render_status,
    _render_domino_status,
    _render_job_history_table,
    _db_record_to_dataclass,
)
from .job_engine import (
    _parse_request,
    _start_job,
    _submit_domino_job,
)


def register_job_routes(rt):
    """Register all job-related routes on the given rt decorator."""

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
        import studio.state as _state
        active = _resolve_job(_state.ACTIVE_JOB_ID)
        if active and active.status == "running":
            _log(active, "A job is already running. Please wait for completion.")
            return _render_status(active)

        job = _start_job(job_request)
        _log(job, "Job submitted.")
        return _render_status(job)

    rt("/run")(run)

    def status():
        import studio.state as _state
        job = _resolve_job(_state.ACTIVE_JOB_ID)
        return _render_status(job)

    rt("/status")(status)

    def status_check():
        """Lightweight endpoint returning only version + status (no HTML)."""
        import studio.state as _state
        job = _resolve_job(_state.ACTIVE_JOB_ID)
        if not job:
            return Response(json.dumps({"status": "idle", "logVersion": 0}), media_type="application/json")
        return Response(json.dumps({"status": job.status, "logVersion": job.log_version}), media_type="application/json")

    rt("/status-check")(status_check)

    def clear_terminal():
        import studio.state as _state
        job = _resolve_job(_state.ACTIVE_JOB_ID)
        if job and job.status != "running":
            job.logs.clear()
            _log(job, "Logs cleared.")
        elif job and job.status == "running":
            _log(job, "Clear requested during run; preserving logs.")
        return _render_status(job)

    rt("/clear-terminal")(clear_terminal)

    def stop():
        import studio.state as _state
        job = _resolve_job(_state.ACTIVE_JOB_ID)
        if not job:
            return _render_status(job)

        if job.status == "running" and job.task:
            job.cancel_requested = True
            _log(job, "Stop requested. Attempting to cancel...")
            job.task.cancel()
        else:
            _log(job, "Stop requested, but no active run found.")

        return _render_status(job)

    rt("/stop")(stop)

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

    rt("/download/{job_id}/{artifact}")(download)

    def status_progress(req: Request):
        """Return progress bar HTML fragment for incremental polling."""
        import studio.state as _state
        job_id = req.query_params.get("job_id", "")
        job = JOB_STORE.get(job_id or _state.ACTIVE_JOB_ID or "")
        if not job:
            return Div("", id="progress-bar-container")
        pct = int(job.progress * 100)
        return Div(
            Div(
                Div(
                    style=f"width: {pct}%; height: 100%; background: var(--primary); "
                          "border-radius: 2px; transition: width 0.3s ease;",
                ),
                style="height: 3px; background: var(--surface-container-high); border-radius: 2px; overflow: hidden;",
            ),
            Span(f"{job.phase} \u2014 {pct}%", style="font-size: 0.75rem; color: var(--outline); margin-top: 4px;"),
            id="progress-bar-container",
        )

    rt("/status-progress")(status_progress)

    def status_badge(req: Request):
        """Return status badge HTML fragment for incremental polling."""
        import studio.state as _state
        job_id = req.query_params.get("job_id", "")
        job = JOB_STORE.get(job_id or _state.ACTIVE_JOB_ID or "")
        if not job:
            return Span("idle", cls="terminal-status terminal-status-idle", id="status-badge")
        badge_cls = f"terminal-status terminal-status-{job.status}"
        return Span(job.status.upper(), cls=badge_cls, id="status-badge")

    rt("/status-badge")(status_badge)

    def status_logs_since(req: Request):
        """Return only new log lines since a given version for append-only updates."""
        import studio.state as _state
        job_id = req.query_params.get("job_id", "")
        since = int(req.query_params.get("since", "0"))
        job = JOB_STORE.get(job_id or _state.ACTIVE_JOB_ID or "")
        if not job or since >= len(job.logs):
            return Response("", media_type="text/html")
        new_lines = job.logs[since:]
        html_lines = "".join(
            f'<div class="log-line" style="opacity:0;animation:fadeIn 0.2s forwards;">{line}</div>'
            for line in new_lines
        )
        return Response(html_lines, media_type="text/html")

    rt("/status-logs-since")(status_logs_since)

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
                if current_job.status in ("completed", "failed", "cancelled"):
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

    rt("/sse/job-stream")(sse_job_stream)

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

    rt("/stop-domino")(stop_domino)

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

    rt("/domino-status")(domino_status)

    def job_history():
        username = _get_username()
        return _render_job_history_table(username)

    rt("/job-history")(job_history)

    def clear_job_history():
        username = _get_username()
        if _DOMINO_AVAILABLE:
            domino_job_store.clear_terminal_jobs(username)
        return _render_job_history_table(username)

    rt("/clear-job-history")(clear_job_history)

    def cancel_queued_jobs():
        """Cancel all queued (not yet submitted) jobs for the current user."""
        username = _get_username()
        if _DOMINO_AVAILABLE:
            with domino_job_store._conn() as con:
                con.execute(
                    """
                    UPDATE domino_jobs
                    SET status = 'cancelled'
                    WHERE username = ? AND status = 'queued' AND domino_run_id IS NULL
                    """,
                    (username,),
                )
        return _render_job_history_table(username)

    rt("/cancel-queued-jobs")(cancel_queued_jobs)
