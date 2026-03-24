"""Tests for auth_context.py — ContextVar-based auth forwarding."""

from __future__ import annotations

import sys
import os

import pytest

# Ensure auto_model_docs is importable
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg_dir = os.path.join(_repo_root, "auto_model_docs")
for p in (_repo_root, _pkg_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from auth_context import (
    get_request_auth_header,
    get_user_auth_headers,
    set_request_auth_header,
)


class TestSetGetAuthHeader:
    def test_default_is_none(self):
        set_request_auth_header(None)
        assert get_request_auth_header() is None

    def test_set_and_get(self):
        set_request_auth_header("Bearer test-jwt-token")
        assert get_request_auth_header() == "Bearer test-jwt-token"
        set_request_auth_header(None)  # cleanup

    def test_overwrite(self):
        set_request_auth_header("Bearer first")
        set_request_auth_header("Bearer second")
        assert get_request_auth_header() == "Bearer second"
        set_request_auth_header(None)

    def test_clear(self):
        set_request_auth_header("Bearer token")
        set_request_auth_header(None)
        assert get_request_auth_header() is None


class TestGetUserAuthHeaders:
    def test_returns_auth_dict(self):
        set_request_auth_header("Bearer my-jwt")
        headers = get_user_auth_headers()
        assert headers == {"Authorization": "Bearer my-jwt"}
        set_request_auth_header(None)

    def test_raises_when_no_token(self):
        set_request_auth_header(None)
        with pytest.raises(RuntimeError, match="No forwarded user token"):
            get_user_auth_headers()

    def test_preserves_exact_value(self):
        """The header value should be forwarded exactly as received."""
        set_request_auth_header("Token custom-scheme-value")
        headers = get_user_auth_headers()
        assert headers["Authorization"] == "Token custom-scheme-value"
        set_request_auth_header(None)
