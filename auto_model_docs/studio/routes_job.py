"""Job-related routes: run, stop, history."""

from __future__ import annotations

from fasthtml.common import *
from starlette.requests import Request

from .state import (
    _DOMINO_AVAILABLE,
    _get_username,
    domino_client,
    domino_job_store,
)
from .ui_components import (
    _render_job_history_table,
)
from .job_engine import (
    _parse_request,
    _submit_domino_job,
)


def register_job_routes(rt):
    """Register all job-related routes on the given rt decorator."""

    async def run(req: Request):
        job_request = await _parse_request(req)
        username = _get_username()
        if not job_request.project_id:
            job_id = domino_job_store.create_job(
                username=username, branch=None, tier=None, spec_path=None,
            )
            domino_job_store.update_job(
                job_id, status="failed",
                domino_status="No target project ID. Reload the app with ?projectId= in the URL.",
            )
            return _render_job_history_table(username)
        try:
            await _submit_domino_job(job_request, username)
        except Exception as exc:
            job_id = domino_job_store.create_job(
                username=username, branch=job_request.branch,
                tier=job_request.hardware_tier, spec_path=job_request.spec_path,
                project_id=job_request.project_id,
            )
            domino_job_store.update_job(job_id, status="failed", domino_status=str(exc))
        return _render_job_history_table(username)

    rt("/run")(run)

    def job_history():
        username = _get_username()
        return _render_job_history_table(username)

    rt("/job-history")(job_history)

    def cancel_queued_jobs():
        """Cancel all queued (not yet submitted) jobs for the current user."""
        username = _get_username()
        if _DOMINO_AVAILABLE:
            domino_job_store.cancel_queued_jobs(username)
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
                    domino_client.stop_job(
                        row["domino_run_id"],
                        project_id=row.get("project_id"),
                    )
                except Exception:
                    pass
            if row:
                domino_job_store.update_job(job_id, status="cancelled")
        return _render_job_history_table(username)

    rt("/stop-job-history")(stop_job_history)
