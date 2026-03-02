"""SQLite-backed store for Domino job history."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _db_path() -> Path:
    if Path("/mnt/data").exists():
        project = os.environ.get("DOMINO_PROJECT_NAME", "autodoc")
        base = Path(f"/mnt/data/{project}")
    else:
        base = Path(".")
    base.mkdir(parents=True, exist_ok=True)
    return base / "autodoc_jobs.db"


@contextmanager
def _conn():
    path = _db_path()
    con = sqlite3.connect(str(path), check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    """Create the domino_jobs table if it does not exist."""
    with _conn() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS domino_jobs (
                id              TEXT PRIMARY KEY,
                username        TEXT NOT NULL,
                domino_run_id   TEXT,
                branch          TEXT,
                hardware_tier   TEXT,
                status          TEXT NOT NULL DEFAULT 'queued',
                domino_status   TEXT,
                job_url         TEXT,
                spec_path       TEXT,
                submitted_at    TEXT,
                completed_at    TEXT
            )
            """
        )


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def create_job(
    username: str,
    branch: Optional[str],
    tier: Optional[str],
    spec_path: Optional[str],
    job_id: Optional[str] = None,
) -> str:
    """Insert a new job row and return its id."""
    import uuid

    jid = job_id or str(uuid.uuid4())
    with _conn() as con:
        con.execute(
            """
            INSERT INTO domino_jobs
                (id, username, branch, hardware_tier, status, spec_path, submitted_at)
            VALUES (?, ?, ?, ?, 'queued', ?, ?)
            """,
            (jid, username, branch, tier, spec_path, _now_iso()),
        )
    return jid


def update_job(job_id: str, **fields: Any) -> None:
    """Update arbitrary columns on a job row."""
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [job_id]
    with _conn() as con:
        con.execute(
            f"UPDATE domino_jobs SET {set_clause} WHERE id = ?",
            values,
        )


def get_job(job_id: str) -> Optional[dict[str, Any]]:
    """Return a single job row as a dict, or None."""
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM domino_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    return dict(row) if row else None


def get_user_jobs(username: str, limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent jobs for a user, newest first."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT * FROM domino_jobs
            WHERE username = ?
            ORDER BY submitted_at DESC
            LIMIT ?
            """,
            (username, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def count_active_jobs(username: str) -> int:
    """Count queued + submitted + running jobs for a user."""
    with _conn() as con:
        row = con.execute(
            """
            SELECT COUNT(*) as cnt FROM domino_jobs
            WHERE username = ? AND status IN ('queued', 'submitted', 'running')
            """,
            (username,),
        ).fetchone()
    return row["cnt"] if row else 0


def get_oldest_queued_job(username: str) -> Optional[dict[str, Any]]:
    """Return the oldest queued job for a user, or None."""
    with _conn() as con:
        row = con.execute(
            """
            SELECT * FROM domino_jobs
            WHERE username = ? AND status = 'queued'
            ORDER BY submitted_at ASC
            LIMIT 1
            """,
            (username,),
        ).fetchone()
    return dict(row) if row else None


def clear_terminal_jobs(username: str) -> None:
    """Delete completed/failed/cancelled rows for a user (soft-clear history)."""
    with _conn() as con:
        con.execute(
            """
            DELETE FROM domino_jobs
            WHERE username = ? AND status IN ('succeeded', 'failed', 'cancelled')
            """,
            (username,),
        )
