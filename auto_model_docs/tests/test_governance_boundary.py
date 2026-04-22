"""Boundary contract tests for the autodoc Job ↔ governance attach-back.

The autodoc Domino Job makes exactly one call to governance: POST the
generated .docx as a bundle attachment at BUILD end, using an ephemeral
token from Domino's ``/access-token`` endpoint. These tests mock that
HTTP boundary and verify the Job handles failures gracefully.

No real network. No real ``time.sleep``.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from autodoc.governance_client import (
    GovernanceClient,
    GovernanceError,
    perform_attach_back,
)
from autodoc.provenance import (
    ProvenanceRecord,
    default_db_path,
    write_provenance_row,
)


BUNDLE_ID = "bundle-abc"
LABEL = "Model Development Document - Auto-Generated (Draft)"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _response(status: int, body: dict | None = None, text: str = ""):
    """Build a MagicMock that looks like a ``requests.Response``."""
    resp = MagicMock(name=f"Response<{status}>")
    resp.status_code = status
    resp.text = text
    resp.json.return_value = body if body is not None else {}
    resp.raise_for_status = MagicMock()
    return resp


def _token_response(token: str):
    return _response(200, text=token)


def _make_http(token_responses, post_responses):
    """Build a MagicMock HTTP with queued ``get`` and ``post`` responses."""
    http = MagicMock(name="http")
    http.get.side_effect = list(token_responses)
    http.post.side_effect = list(post_responses)
    return http


def _make_client(http, sleep=None):
    return GovernanceClient(
        api_host="https://example.domino.com",
        token_url="http://localhost:8899/access-token",
        http=http,
        sleep=sleep if sleep is not None else MagicMock(name="sleep"),
        max_retries=3,
        backoff_base=1.0,
    )


def _write_docx(tmp_path, content: bytes = b"fake-docx-bytes") -> str:
    p = tmp_path / "model_doc.docx"
    p.write_bytes(content)
    return str(p)


def _record() -> ProvenanceRecord:
    return ProvenanceRecord(
        provenance_id="prov-1234abcd",
        bundle_id=BUNDLE_ID,
        policy_version_id="policy-v7",
        commit_sha="deadbeef",
        generated_by_user="alice",
        generated_at="2026-04-22T18:00:00Z",
    )


# ---------------------------------------------------------------------------
# 403 — permission revoked mid-Job
# ---------------------------------------------------------------------------


def test_403_permission_revoked_fails_without_retry_but_preserves_provenance(
    tmp_path, monkeypatch, caplog
):
    """403 on attach-back: Job logs the error, returns failure, does NOT retry.
    Provenance row written earlier in BUILD is still present in the DB.
    """
    # Provenance row written before attach-back (as happens in save_with_provenance).
    db_path = str(tmp_path / "autodoc_provenance.db")
    monkeypatch.setenv("AUTODOC_PROVENANCE_DB", db_path)
    assert default_db_path() == db_path
    record = _record()
    docx_path = _write_docx(tmp_path)
    write_provenance_row(db_path, record, docx_path)

    sleep = MagicMock(name="sleep")
    http = _make_http(
        token_responses=[_token_response("tok-1")],
        post_responses=[_response(403, body={"error": "forbidden"})],
    )
    client = _make_client(http, sleep=sleep)

    with caplog.at_level(logging.ERROR, logger="autodoc.governance_client"):
        ok, attachment_id = perform_attach_back(
            client=client,
            bundle_id=BUNDLE_ID,
            docx_path=docx_path,
            label=LABEL,
        )

    assert ok is False
    assert attachment_id is None
    assert http.post.call_count == 1  # no retry
    sleep.assert_not_called()
    assert any(
        "attach-back failed" in rec.message and BUNDLE_ID in rec.message
        for rec in caplog.records
    )

    # Provenance row is still present despite attach-back failure.
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT bundle_id, generated_by_user FROM provenance WHERE provenance_id = ?",
            (record.provenance_id,),
        ).fetchall()
    assert rows == [(BUNDLE_ID, "alice")]


# ---------------------------------------------------------------------------
# 409 — duplicate attach (B2 idempotency)
# ---------------------------------------------------------------------------


def test_409_duplicate_attach_is_treated_as_success():
    """Second POST of the same (bundle, label) returns 409; Job exits success."""
    sleep = MagicMock(name="sleep")

    # First run: 201 Created.
    http1 = _make_http(
        token_responses=[_token_response("tok-1")],
        post_responses=[_response(201, body={"attachment_id": "att-1"})],
    )
    client1 = _make_client(http1, sleep=sleep)
    id1 = client1.attach_document(BUNDLE_ID, b"bytes", LABEL)
    assert id1 == "att-1"

    # Second run against the same bundle/label: 409 Conflict.
    http2 = _make_http(
        token_responses=[_token_response("tok-1")],
        post_responses=[_response(409, body={"attachment_id": "att-1"})],
    )
    client2 = _make_client(http2, sleep=sleep)
    id2 = client2.attach_document(BUNDLE_ID, b"bytes", LABEL)
    assert id2 == "att-1"
    sleep.assert_not_called()
    assert http2.post.call_count == 1


# ---------------------------------------------------------------------------
# 429 — rate limit with exponential backoff
# ---------------------------------------------------------------------------


def test_429_exponential_backoff_max_three_retries_then_fails():
    """Four consecutive 429s: 1 initial attempt + 3 retries, then raise.
    Sleep called with exponential backoff (1, 2, 4 seconds)."""
    sleep = MagicMock(name="sleep")
    http = _make_http(
        token_responses=[_token_response("tok-1")],
        post_responses=[_response(429) for _ in range(4)],
    )
    client = _make_client(http, sleep=sleep)

    with pytest.raises(GovernanceError) as exc_info:
        client.attach_document(BUNDLE_ID, b"bytes", LABEL)

    assert exc_info.value.status_code == 429
    assert http.post.call_count == 4  # 1 initial + 3 retries
    # Backoff sequence: base * 2^0, 2^1, 2^2 -> 1, 2, 4
    assert [call.args[0] for call in sleep.call_args_list] == [1.0, 2.0, 4.0]


def test_429_recovers_after_transient_rate_limit():
    """One 429 then a 201: client retries once, returns the attachment id."""
    sleep = MagicMock(name="sleep")
    http = _make_http(
        token_responses=[_token_response("tok-1")],
        post_responses=[
            _response(429),
            _response(201, body={"attachment_id": "att-42"}),
        ],
    )
    client = _make_client(http, sleep=sleep)

    attachment_id = client.attach_document(BUNDLE_ID, b"bytes", LABEL)
    assert attachment_id == "att-42"
    assert http.post.call_count == 2
    sleep.assert_called_once_with(1.0)


# ---------------------------------------------------------------------------
# 401 — clock skew on ephemeral token
# ---------------------------------------------------------------------------


def test_401_reacquires_token_once_and_retries_once():
    """After a 401, fetch a fresh token and retry. If the second attempt also
    returns 401, fail."""
    sleep = MagicMock(name="sleep")
    http = _make_http(
        token_responses=[_token_response("tok-1"), _token_response("tok-2")],
        post_responses=[_response(401), _response(401)],
    )
    client = _make_client(http, sleep=sleep)

    with pytest.raises(GovernanceError) as exc_info:
        client.attach_document(BUNDLE_ID, b"bytes", LABEL)
    assert exc_info.value.status_code == 401

    # Token fetched exactly twice (original + one refresh), POST exactly twice.
    assert http.get.call_count == 2
    assert http.post.call_count == 2

    # Verify the second POST carried the refreshed token, not the stale one.
    first_post_headers = http.post.call_args_list[0].kwargs["headers"]
    second_post_headers = http.post.call_args_list[1].kwargs["headers"]
    assert first_post_headers["Authorization"] == "Bearer tok-1"
    assert second_post_headers["Authorization"] == "Bearer tok-2"


def test_401_then_success_after_token_refresh():
    """Clock-skew recovery path: refresh token, succeed on retry."""
    sleep = MagicMock(name="sleep")
    http = _make_http(
        token_responses=[_token_response("tok-old"), _token_response("tok-new")],
        post_responses=[
            _response(401),
            _response(201, body={"attachment_id": "att-ok"}),
        ],
    )
    client = _make_client(http, sleep=sleep)

    attachment_id = client.attach_document(BUNDLE_ID, b"bytes", LABEL)
    assert attachment_id == "att-ok"
    assert http.get.call_count == 2
    assert http.post.call_count == 2
