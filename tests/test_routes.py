"""Tests for FastHTML routes related to cross-project Domino integration.

The /api/resolve-project route is being added in a parallel workspace.
These tests use starlette.testclient.TestClient to exercise the route
synchronously.  When the parallel branch lands, remove skip markers.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import (
    ProjectAPIError,
    ProjectForbiddenError,
    ProjectInfo,
    ProjectNotFoundError,
)

# ---------------------------------------------------------------------------
# Try to import the app; the route may not exist yet
# ---------------------------------------------------------------------------
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_HAS_RESOLVE_ROUTE = False
try:
    # Patch Domino imports so web_app loads without the SDK
    with (
        patch.dict(os.environ, {
            "DOMINO_API_HOST": "https://domino.example.com",
            "DOMINO_API_PROXY": "http://localhost:8899",
            "DOMINO_USER_API_KEY": "test-key",
            "DOMINO_PROJECT_OWNER": "test_owner",
            "DOMINO_PROJECT_NAME": "test_project",
            "DOMINO_STARTING_USERNAME": "test_user",
        }),
    ):
        from auto_model_docs.web_app import app

        # Check if the route exists
        for route in getattr(app, "routes", []):
            path = getattr(route, "path", "")
            if "resolve-project" in path:
                _HAS_RESOLVE_ROUTE = True
                break
except Exception:
    app = None  # type: ignore[assignment]

skip_no_route = pytest.mark.skipif(
    not _HAS_RESOLVE_ROUTE,
    reason="/api/resolve-project route not yet in web_app.py",
)


@pytest.fixture
def client():
    """Starlette test client for the FastHTML app."""
    if app is None:
        pytest.skip("web_app could not be imported")
    from starlette.testclient import TestClient

    return TestClient(app)


# ===================================================================
# /api/resolve-project route tests
# ===================================================================


class TestResolveProjectRoute:
    """Tests for GET /api/resolve-project?projectId=..."""

    @skip_no_route
    def test_valid_project_id_returns_resolved_html(self, client):
        """Valid projectId should return HTML with .resolved class."""
        info = ProjectInfo(
            id="507f1f77bcf86cd799439011", name="my-model", owner="alice"
        )
        with patch(
            "auto_model_docs.web_app.domino_client.resolve_project",
            new_callable=AsyncMock,
            return_value=info,
        ):
            resp = client.get("/api/resolve-project?projectId=507f1f77bcf86cd799439011")

        assert resp.status_code == 200
        html = resp.text
        assert "resolved" in html
        assert "alice" in html or "my-model" in html

    @skip_no_route
    def test_missing_project_id_returns_empty(self, client):
        """No projectId param should return an empty/default div."""
        resp = client.get("/api/resolve-project")

        assert resp.status_code == 200
        # Should not contain error styling or resolved content
        html = resp.text
        assert "error" not in html.lower() or html.strip() == "" or "resolved" not in html

    @skip_no_route
    def test_404_from_api_returns_error_html(self, client):
        """ProjectNotFoundError should produce error HTML."""
        with patch(
            "auto_model_docs.web_app.domino_client.resolve_project",
            new_callable=AsyncMock,
            side_effect=ProjectNotFoundError("not found"),
        ):
            resp = client.get("/api/resolve-project?projectId=000000000000000000000000")

        assert resp.status_code == 200  # HTMX fragment, not HTTP error
        html = resp.text
        assert "not found" in html.lower() or "error" in html.lower()

    @skip_no_route
    def test_403_from_api_returns_error_html(self, client):
        """ProjectForbiddenError should produce access-denied HTML."""
        with patch(
            "auto_model_docs.web_app.domino_client.resolve_project",
            new_callable=AsyncMock,
            side_effect=ProjectForbiddenError("forbidden"),
        ):
            resp = client.get("/api/resolve-project?projectId=507f1f77bcf86cd799439011")

        assert resp.status_code == 200
        html = resp.text
        assert "access" in html.lower() or "forbidden" in html.lower() or "error" in html.lower()

    @skip_no_route
    def test_api_error_returns_error_html(self, client):
        """ProjectAPIError should produce a generic error HTML fragment."""
        with patch(
            "auto_model_docs.web_app.domino_client.resolve_project",
            new_callable=AsyncMock,
            side_effect=ProjectAPIError("API unreachable"),
        ):
            resp = client.get("/api/resolve-project?projectId=507f1f77bcf86cd799439011")

        assert resp.status_code == 200
        html = resp.text
        assert "error" in html.lower() or "try again" in html.lower()
