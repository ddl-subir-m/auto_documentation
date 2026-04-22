"""Tests for autodoc.bundle_context (U3)."""

from __future__ import annotations

import json
import logging
import os
import stat
import sys

import pytest

from autodoc.bundle_context import (
    BundleContextError,
    delete_context_file,
    load_context,
)


def _valid_payload() -> dict:
    return {
        "bundle_id": "b-123",
        "policy_version_id": "pv-456",
        "bundle": {"name": "Credit Model", "owner": "Alice"},
        "policy_def": {"sections": ["intended_use"]},
    }


def _write(tmp_path, payload, name="ctx.json") -> str:
    p = tmp_path / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


def test_load_context_happy_path(tmp_path):
    payload = _valid_payload()
    path = _write(tmp_path, payload)

    result = load_context(path)

    assert result == payload
    assert result["bundle"]["owner"] == "Alice"


@pytest.mark.parametrize(
    "missing_key",
    ["bundle_id", "policy_version_id", "bundle", "policy_def"],
)
def test_load_context_missing_required_key(tmp_path, missing_key):
    payload = _valid_payload()
    del payload[missing_key]
    path = _write(tmp_path, payload)

    with pytest.raises(BundleContextError) as excinfo:
        load_context(path)

    assert missing_key in str(excinfo.value)


def test_load_context_malformed_json(tmp_path):
    path = tmp_path / "ctx.json"
    path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(BundleContextError) as excinfo:
        load_context(str(path))

    assert "invalid JSON" in str(excinfo.value)


def test_load_context_path_does_not_exist(tmp_path):
    path = tmp_path / "nope.json"

    with pytest.raises(BundleContextError) as excinfo:
        load_context(str(path))

    assert "not found" in str(excinfo.value)


def test_load_context_top_level_not_object(tmp_path):
    path = tmp_path / "ctx.json"
    path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

    with pytest.raises(BundleContextError) as excinfo:
        load_context(str(path))

    assert "JSON object" in str(excinfo.value)


def test_load_context_wrong_type_for_key(tmp_path):
    payload = _valid_payload()
    payload["bundle"] = "should be dict"
    path = _write(tmp_path, payload)

    with pytest.raises(BundleContextError) as excinfo:
        load_context(path)

    assert "bundle" in str(excinfo.value)


def test_delete_context_file_nonexistent_is_noop(tmp_path):
    missing = tmp_path / "gone.json"

    delete_context_file(str(missing))  # must not raise


def test_delete_context_file_removes_existing(tmp_path):
    path = tmp_path / "ctx.json"
    path.write_text("{}", encoding="utf-8")
    assert path.exists()

    delete_context_file(str(path))

    assert not path.exists()


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="POSIX permission semantics required",
)
def test_delete_context_file_permission_error_logs_and_swallows(
    tmp_path, caplog, monkeypatch
):
    path = tmp_path / "ctx.json"
    path.write_text("{}", encoding="utf-8")

    def _raise_permission(_p):
        raise PermissionError("read-only parent")

    monkeypatch.setattr(os, "remove", _raise_permission)

    with caplog.at_level(logging.WARNING, logger="autodoc.bundle_context"):
        delete_context_file(str(path))  # must not raise

    assert any("Permission denied" in r.message for r in caplog.records)
