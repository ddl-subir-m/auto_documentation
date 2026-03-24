"""Tests for domino_job_store.py — SQLite-backed job history."""

from __future__ import annotations

import os
import sys
import tempfile
from unittest.mock import patch

import pytest

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg_dir = os.path.join(_repo_root, "auto_model_docs")
for p in (_repo_root, _pkg_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

import domino_job_store as store


@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path, monkeypatch):
    """Redirect the DB to a temp directory for every test."""
    db_file = tmp_path / "autodoc_jobs.db"
    monkeypatch.setattr(store, "_db_path", lambda: db_file)
    store.init_db()
    yield


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

class TestInitDb:
    def test_creates_table(self, tmp_path):
        import sqlite3
        db_file = tmp_path / "autodoc_jobs.db"
        con = sqlite3.connect(str(db_file))
        con.row_factory = sqlite3.Row
        tables = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        con.close()
        assert any(r["name"] == "domino_jobs" for r in tables)

    def test_idempotent(self):
        """Calling init_db twice should not error."""
        store.init_db()
        store.init_db()


# ---------------------------------------------------------------------------
# create_job / get_job
# ---------------------------------------------------------------------------

class TestCreateAndGetJob:
    def test_create_returns_id(self):
        jid = store.create_job("alice", "main", "small", "/spec.yaml")
        assert isinstance(jid, str)
        assert len(jid) > 0

    def test_create_with_custom_id(self):
        jid = store.create_job("alice", "main", "small", "/spec.yaml", job_id="custom-123")
        assert jid == "custom-123"

    def test_get_job_returns_dict(self):
        jid = store.create_job("alice", "main", "small", "/spec.yaml")
        job = store.get_job(jid)
        assert job is not None
        assert job["username"] == "alice"
        assert job["branch"] == "main"
        assert job["hardware_tier"] == "small"
        assert job["spec_path"] == "/spec.yaml"
        assert job["status"] == "queued"

    def test_get_job_not_found(self):
        assert store.get_job("nonexistent") is None

    def test_create_with_command_and_project_id(self):
        jid = store.create_job(
            "bob", None, None, None,
            command="python main.py", project_id="proj-456",
        )
        job = store.get_job(jid)
        assert job["command"] == "python main.py"
        assert job["project_id"] == "proj-456"

    def test_create_with_nulls(self):
        jid = store.create_job("alice", None, None, None)
        job = store.get_job(jid)
        assert job["branch"] is None
        assert job["hardware_tier"] is None
        assert job["spec_path"] is None

    def test_submitted_at_populated(self):
        jid = store.create_job("alice", "main", "small", "/spec.yaml")
        job = store.get_job(jid)
        assert job["submitted_at"] is not None
        assert "T" in job["submitted_at"]  # ISO format


# ---------------------------------------------------------------------------
# update_job
# ---------------------------------------------------------------------------

class TestUpdateJob:
    def test_update_status(self):
        jid = store.create_job("alice", "main", "small", "/spec.yaml")
        store.update_job(jid, status="running", domino_status="Executing")
        job = store.get_job(jid)
        assert job["status"] == "running"
        assert job["domino_status"] == "Executing"

    def test_update_multiple_fields(self):
        jid = store.create_job("alice", "main", "small", "/spec.yaml")
        store.update_job(jid, status="succeeded", completed_at="2026-03-24T12:00:00Z", job_url="https://example.com")
        job = store.get_job(jid)
        assert job["status"] == "succeeded"
        assert job["completed_at"] == "2026-03-24T12:00:00Z"
        assert job["job_url"] == "https://example.com"

    def test_update_no_fields_is_noop(self):
        jid = store.create_job("alice", "main", "small", "/spec.yaml")
        store.update_job(jid)  # should not error
        job = store.get_job(jid)
        assert job["status"] == "queued"

    def test_update_nonexistent_job_no_error(self):
        store.update_job("ghost-id", status="failed")  # should not error


# ---------------------------------------------------------------------------
# get_user_jobs
# ---------------------------------------------------------------------------

class TestGetUserJobs:
    def test_returns_user_jobs_only(self):
        store.create_job("alice", "main", "small", "/a.yaml")
        store.create_job("bob", "main", "small", "/b.yaml")
        store.create_job("alice", "dev", "large", "/c.yaml")

        alice_jobs = store.get_user_jobs("alice")
        assert len(alice_jobs) == 2
        assert all(j["username"] == "alice" for j in alice_jobs)

    def test_ordered_newest_first(self):
        j1 = store.create_job("alice", "main", "s", "/a.yaml", job_id="job-1")
        j2 = store.create_job("alice", "dev", "s", "/b.yaml", job_id="job-2")
        jobs = store.get_user_jobs("alice")
        # job-2 was created after job-1
        assert jobs[0]["id"] == "job-2"
        assert jobs[1]["id"] == "job-1"

    def test_limit(self):
        for i in range(10):
            store.create_job("alice", "main", "s", "/spec.yaml")
        jobs = store.get_user_jobs("alice", limit=3)
        assert len(jobs) == 3

    def test_empty_for_unknown_user(self):
        assert store.get_user_jobs("nobody") == []


# ---------------------------------------------------------------------------
# count_active_jobs
# ---------------------------------------------------------------------------

class TestCountActiveJobs:
    def test_counts_queued_submitted_running(self):
        store.create_job("alice", "main", "s", "/spec.yaml", job_id="j1")
        store.create_job("alice", "main", "s", "/spec.yaml", job_id="j2")
        store.create_job("alice", "main", "s", "/spec.yaml", job_id="j3")
        store.update_job("j2", status="submitted")
        store.update_job("j3", status="running")
        assert store.count_active_jobs("alice") == 3

    def test_excludes_terminal_statuses(self):
        store.create_job("alice", "main", "s", "/spec.yaml", job_id="j1")
        store.create_job("alice", "main", "s", "/spec.yaml", job_id="j2")
        store.create_job("alice", "main", "s", "/spec.yaml", job_id="j3")
        store.update_job("j1", status="succeeded")
        store.update_job("j2", status="failed")
        store.update_job("j3", status="cancelled")
        assert store.count_active_jobs("alice") == 0

    def test_zero_for_unknown_user(self):
        assert store.count_active_jobs("nobody") == 0

    def test_per_user(self):
        store.create_job("alice", "main", "s", "/spec.yaml")
        store.create_job("bob", "main", "s", "/spec.yaml")
        assert store.count_active_jobs("alice") == 1
        assert store.count_active_jobs("bob") == 1


# ---------------------------------------------------------------------------
# get_oldest_queued_job
# ---------------------------------------------------------------------------

class TestGetOldestQueuedJob:
    def test_returns_oldest(self):
        store.create_job("alice", "main", "s", "/spec.yaml", job_id="old")
        store.create_job("alice", "main", "s", "/spec.yaml", job_id="new")
        oldest = store.get_oldest_queued_job("alice")
        assert oldest is not None
        assert oldest["id"] == "old"

    def test_ignores_non_queued(self):
        store.create_job("alice", "main", "s", "/spec.yaml", job_id="j1")
        store.update_job("j1", status="running")
        store.create_job("alice", "main", "s", "/spec.yaml", job_id="j2")
        oldest = store.get_oldest_queued_job("alice")
        assert oldest["id"] == "j2"

    def test_none_when_no_queued(self):
        store.create_job("alice", "main", "s", "/spec.yaml", job_id="j1")
        store.update_job("j1", status="succeeded")
        assert store.get_oldest_queued_job("alice") is None

    def test_none_for_unknown_user(self):
        assert store.get_oldest_queued_job("nobody") is None


# ---------------------------------------------------------------------------
# clear_terminal_jobs
# ---------------------------------------------------------------------------

class TestClearTerminalJobs:
    def test_deletes_succeeded_failed_cancelled(self):
        store.create_job("alice", "main", "s", "/spec.yaml", job_id="j1")
        store.create_job("alice", "main", "s", "/spec.yaml", job_id="j2")
        store.create_job("alice", "main", "s", "/spec.yaml", job_id="j3")
        store.create_job("alice", "main", "s", "/spec.yaml", job_id="j4")
        store.update_job("j1", status="succeeded")
        store.update_job("j2", status="failed")
        store.update_job("j3", status="cancelled")
        # j4 stays queued

        store.clear_terminal_jobs("alice")

        assert store.get_job("j1") is None
        assert store.get_job("j2") is None
        assert store.get_job("j3") is None
        assert store.get_job("j4") is not None  # still queued

    def test_does_not_affect_other_users(self):
        store.create_job("alice", "main", "s", "/spec.yaml", job_id="a1")
        store.create_job("bob", "main", "s", "/spec.yaml", job_id="b1")
        store.update_job("a1", status="succeeded")
        store.update_job("b1", status="succeeded")

        store.clear_terminal_jobs("alice")

        assert store.get_job("a1") is None
        assert store.get_job("b1") is not None  # bob's job untouched

    def test_noop_when_no_terminal_jobs(self):
        store.create_job("alice", "main", "s", "/spec.yaml", job_id="j1")
        store.clear_terminal_jobs("alice")  # no error
        assert store.get_job("j1") is not None
