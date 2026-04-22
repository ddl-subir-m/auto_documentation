"""Tests for U7 MVP provenance stamping."""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from docx import Document

from autodoc import provenance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_row(db_path: str, provenance_id: str) -> dict | None:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM provenance WHERE provenance_id = ?", (provenance_id,)
        )
        row = cur.fetchone()
    return dict(row) if row else None


def _make_record(**overrides) -> provenance.ProvenanceRecord:
    defaults = dict(
        provenance_id="abc123def456",
        bundle_id="bundle-xyz",
        policy_version_id="policy-v42",
        commit_sha="deadbeef" * 5,
        generated_by_user="alice",
        generated_at="2026-04-22T12:34:56Z",
    )
    defaults.update(overrides)
    return provenance.ProvenanceRecord(**defaults)


def _fs_commit(temp_path: str, final_path: str) -> None:
    os.makedirs(os.path.dirname(final_path) or ".", exist_ok=True)
    os.replace(temp_path, final_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_mvp_fields_appear_in_word_custom_properties(tmp_path):
    db_path = str(tmp_path / "prov.db")
    final_path = str(tmp_path / "out.docx")
    record = _make_record()

    doc = Document()
    doc.add_paragraph("Hello")
    provenance.save_with_provenance(doc, final_path, record, db_path, _fs_commit)

    props = provenance.read_custom_properties(final_path)
    assert props["bundle_id"] == "bundle-xyz"
    assert props["policy_version_id"] == "policy-v42"
    assert props["commit_sha"] == "deadbeef" * 5
    assert props["generated_by_user"] == "alice"
    assert props["generated_at"] == "2026-04-22T12:34:56Z"
    assert set(props.keys()) == set(provenance.MVP_FIELDS)


def test_mvp_fields_present_in_sqlite_row(tmp_path):
    db_path = str(tmp_path / "prov.db")
    final_path = str(tmp_path / "out.docx")
    record = _make_record()

    doc = Document()
    provenance.save_with_provenance(doc, final_path, record, db_path, _fs_commit)

    row = _read_row(db_path, record.provenance_id)
    assert row is not None
    assert row["bundle_id"] == "bundle-xyz"
    assert row["policy_version_id"] == "policy-v42"
    assert row["commit_sha"] == "deadbeef" * 5
    assert row["generated_by_user"] == "alice"
    assert row["generated_at"] == "2026-04-22T12:34:56Z"
    assert row["docx_path"] == final_path
    assert isinstance(row["created_ts"], float)


def test_provenance_id_unique_across_runs(monkeypatch):
    monkeypatch.setenv("DOMINO_STARTING_USERNAME", "bob")
    ids = {provenance.capture_context().provenance_id for _ in range(50)}
    assert len(ids) == 50
    # Short-UUID format: 12 hex chars
    for pid in ids:
        assert re.fullmatch(r"[0-9a-f]{12}", pid)


def test_commit_sha_fallback_when_git_fails(monkeypatch):
    def _raise(*_a, **_kw):
        raise FileNotFoundError("git not installed")

    monkeypatch.setenv("DOMINO_WORKING_DIR_GIT_REF_ID", "env-fallback-sha")
    monkeypatch.delenv("DOMINO_STARTING_USERNAME", raising=False)
    with patch.object(subprocess, "run", side_effect=_raise):
        record = provenance.capture_context()

    assert record.commit_sha == "env-fallback-sha"
    assert record.generated_by_user == "unknown"


def test_commit_sha_none_when_git_fails_and_no_env(monkeypatch):
    def _raise(*_a, **_kw):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.delenv("DOMINO_WORKING_DIR_GIT_REF_ID", raising=False)
    with patch.object(subprocess, "run", side_effect=_raise):
        record = provenance.capture_context()

    assert record.commit_sha is None


def test_generated_by_user_fallback_to_unknown(monkeypatch):
    monkeypatch.delenv("DOMINO_STARTING_USERNAME", raising=False)
    record = provenance.capture_context()
    assert record.generated_by_user == "unknown"


def test_atomicity_rolls_back_row_on_commit_failure(tmp_path):
    db_path = str(tmp_path / "prov.db")
    final_path = str(tmp_path / "never_written.docx")
    record = _make_record(provenance_id="rollback123")

    def _failing_commit(_temp, _final):
        raise RuntimeError("simulated write failure")

    doc = Document()
    with pytest.raises(RuntimeError, match="simulated write failure"):
        provenance.save_with_provenance(doc, final_path, record, db_path, _failing_commit)

    assert _read_row(db_path, "rollback123") is None
    assert not Path(final_path).exists()


def test_capture_context_with_none_bundle_and_policy(monkeypatch):
    monkeypatch.setenv("DOMINO_STARTING_USERNAME", "carol")
    record = provenance.capture_context(bundle_id=None, policy_version_id=None)
    assert record.bundle_id is None
    assert record.policy_version_id is None
    assert record.generated_by_user == "carol"
    # generated_at is ISO 8601 UTC Zulu
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", record.generated_at)


def test_none_fields_stamp_as_empty_strings(tmp_path):
    db_path = str(tmp_path / "prov.db")
    final_path = str(tmp_path / "out.docx")
    record = _make_record(bundle_id=None, policy_version_id=None, commit_sha=None)

    doc = Document()
    provenance.save_with_provenance(doc, final_path, record, db_path, _fs_commit)

    props = provenance.read_custom_properties(final_path)
    assert props["bundle_id"] == "" or props["bundle_id"] is None
    assert props["policy_version_id"] == "" or props["policy_version_id"] is None

    row = _read_row(db_path, record.provenance_id)
    assert row["bundle_id"] is None
    assert row["policy_version_id"] is None
    assert row["commit_sha"] is None


def test_default_db_path_respects_env(monkeypatch, tmp_path):
    override = str(tmp_path / "custom.db")
    monkeypatch.setenv("AUTODOC_PROVENANCE_DB", override)
    assert provenance.default_db_path() == override
