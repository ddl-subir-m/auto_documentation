"""Shared fixtures and stub exception classes for cross-project Domino tests.

The typed exceptions (ProjectNotFoundError, ProjectForbiddenError,
ProjectAPIError, DominoAPIError) are being added in a parallel workspace.
We try to import them from domino_client; if they don't exist yet we define
compatible stubs here so the test suite runs against either branch.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Ensure auto_model_docs is importable
# ---------------------------------------------------------------------------
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

# ---------------------------------------------------------------------------
# Stub exception hierarchy — used only when the real ones aren't available yet
# ---------------------------------------------------------------------------


class _ProjectNotFoundError(Exception):
    """Project ID returned 404."""


class _ProjectForbiddenError(Exception):
    """Project ID returned 403 — viewer lacks access."""


class _ProjectAPIError(Exception):
    """Domino API returned 5xx, network error, or malformed response."""


class _DominoAPIError(Exception):
    """Generic Domino API error (bad JSON, unexpected status, etc.)."""


def _load_exceptions():
    """Import real exception classes from domino_client, falling back to stubs."""
    try:
        from auto_model_docs.domino_client import ProjectNotFoundError
    except ImportError:
        ProjectNotFoundError = _ProjectNotFoundError

    try:
        from auto_model_docs.domino_client import ProjectForbiddenError
    except ImportError:
        ProjectForbiddenError = _ProjectForbiddenError

    try:
        from auto_model_docs.domino_client import ProjectAPIError
    except ImportError:
        ProjectAPIError = _ProjectAPIError

    try:
        from auto_model_docs.domino_client import DominoAPIError
    except ImportError:
        DominoAPIError = _DominoAPIError

    return ProjectNotFoundError, ProjectForbiddenError, ProjectAPIError, DominoAPIError


(
    ProjectNotFoundError,
    ProjectForbiddenError,
    ProjectAPIError,
    DominoAPIError,
) = _load_exceptions()


# ---------------------------------------------------------------------------
# ProjectInfo stub — the real dataclass is being added in the parallel branch
# ---------------------------------------------------------------------------
try:
    from auto_model_docs.domino_client import ProjectInfo
except ImportError:

    @dataclass
    class ProjectInfo:  # type: ignore[no-redef]
        """Resolved project metadata."""

        id: str
        name: str
        owner: str


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure Domino env vars are set to safe defaults for every test."""
    defaults = {
        "DOMINO_API_HOST": "https://domino.example.com",
        "DOMINO_API_PROXY": "http://localhost:8899",
        "DOMINO_USER_API_KEY": "test-api-key",
        "DOMINO_PROJECT_OWNER": "test_owner",
        "DOMINO_PROJECT_NAME": "test_project",
        "DOMINO_STARTING_USERNAME": "test_user",
    }
    for key, val in defaults.items():
        monkeypatch.setenv(key, val)


@pytest.fixture
def sample_project_info():
    """A resolved ProjectInfo for use in tests."""
    return ProjectInfo(id="507f1f77bcf86cd799439011", name="my-model", owner="alice")
