"""Tests for domino_auth.py — API host resolution, auth headers, project ID."""

from __future__ import annotations

import os
import sys

import pytest
from unittest.mock import patch

# Ensure auto_model_docs is importable
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg_dir = os.path.join(_repo_root, "auto_model_docs")
for p in (_repo_root, _pkg_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from domino_auth import resolve_api_host, get_auth_headers, resolve_project_id


# ---------------------------------------------------------------------------
# resolve_api_host
# ---------------------------------------------------------------------------

class TestResolveApiHost:
    def test_returns_host_from_env(self, monkeypatch):
        monkeypatch.setenv("DOMINO_API_HOST", "https://domino.example.com")
        assert resolve_api_host() == "https://domino.example.com"

    def test_strips_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("DOMINO_API_HOST", "https://domino.example.com/")
        assert resolve_api_host() == "https://domino.example.com"

    def test_strips_multiple_trailing_slashes(self, monkeypatch):
        monkeypatch.setenv("DOMINO_API_HOST", "https://domino.example.com///")
        assert resolve_api_host() == "https://domino.example.com"

    def test_returns_empty_when_unset(self, monkeypatch):
        monkeypatch.delenv("DOMINO_API_HOST", raising=False)
        assert resolve_api_host() == ""

    def test_returns_empty_when_blank(self, monkeypatch):
        monkeypatch.setenv("DOMINO_API_HOST", "")
        assert resolve_api_host() == ""


# ---------------------------------------------------------------------------
# get_auth_headers
# ---------------------------------------------------------------------------

class TestGetAuthHeaders:
    def test_prefers_forwarded_jwt(self, monkeypatch):
        """When a request JWT is forwarded via auth_context, it should be used."""
        monkeypatch.setenv("DOMINO_USER_API_KEY", "should-not-be-used")
        with patch("domino_auth.get_request_auth_header", return_value="Bearer user-jwt"):
            headers = get_auth_headers()
        assert headers == {"Authorization": "Bearer user-jwt"}

    def test_raises_when_no_jwt(self):
        """Without a forwarded JWT, get_auth_headers raises."""
        with patch("domino_auth.get_request_auth_header", return_value=None):
            with pytest.raises(RuntimeError, match="No Domino auth credentials"):
                get_auth_headers(required=True)

    def test_returns_empty_when_not_required_and_no_jwt(self):
        with patch("domino_auth.get_request_auth_header", return_value=None):
            headers = get_auth_headers(required=False)
        assert headers == {}

    def test_empty_forwarded_header_raises(self):
        """An empty string from auth_context should not be treated as valid."""
        with patch("domino_auth.get_request_auth_header", return_value=""):
            with pytest.raises(RuntimeError, match="No Domino auth credentials"):
                get_auth_headers(required=True)


# ---------------------------------------------------------------------------
# resolve_project_id
# ---------------------------------------------------------------------------

class TestResolveProjectId:
    def test_returns_given_id(self):
        assert resolve_project_id("abc123") == "abc123"

    def test_raises_when_none(self):
        with pytest.raises(RuntimeError, match="No project ID available"):
            resolve_project_id(None)

    def test_raises_when_empty_string(self):
        with pytest.raises(RuntimeError, match="No project ID available"):
            resolve_project_id("")
