"""Job-related routes: run, Domino status, stop, history."""

from __future__ import annotations

from uuid import uuid4

from fasthtml.common import *
from starlette.requests import Request

from .state import (
    DominoJobRecord,
    _DOMINO_AVAILABLE,
    _get_username,
    domino_client,
    domino_job_store,
)
from .ui_components import (
    _render_domino_status,
    _render_job_history_table,
    _db_record_to_dataclass,
)
from .job_engine import (
    _parse_request,
    _submit_domino_job,
)


def register_job_routes(rt):
    """Register all job-related routes on the given rt decorator."""

    async def run(req: Request):
        job_request = await _parse_request(req)
        if not job_request.project_id:
            err_record = DominoJobRecord(
                id=str(uuid4()),
                username=_get_username(),
                status="failed",
                domino_status="No target project ID. Reload the app with ?projectId= in the URL.",
            )
            return _render_domino_status(err_record)
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

    rt("/run")(run)

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
        """Return the Domino job status panel for the latest active job."""
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

    async def stop_job_history(req: Request):
        """Stop a job and return the updated history table."""
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
        return _render_job_history_table(username)

    rt("/stop-job-history")(stop_job_history)
