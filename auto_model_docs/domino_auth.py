"""Shared Domino API host resolution and authentication.

Used by both domino_client.py and domino_datasets.py.  Extended identity
propagation is always on — the viewer's JWT is the primary auth mechanism.
"""

from __future__ import annotations

import os
from typing import Optional

from auth_context import get_request_auth_header


def resolve_api_host() -> str:
    """Return the Domino API host (DOMINO_API_HOST)."""
    host = os.environ.get("DOMINO_API_HOST") or ""
    return host.rstrip("/")


def get_auth_headers(*, required: bool = True) -> dict[str, str]:
    """Build Domino auth headers using the forwarded viewer JWT.

    Falls back to API key for local development.  When *required* is
    True (default), raises if no credentials are available.
    """
    forwarded = get_request_auth_header()
    if forwarded:
        return {"Authorization": forwarded}

    api_key = os.environ.get("DOMINO_USER_API_KEY") or os.environ.get("DOMINO_API_KEY") or ""
    if api_key:
        return {"X-Domino-Api-Key": api_key}

    if required:
        raise RuntimeError(
            "No Domino auth credentials available. "
            "Need a forwarded user token (extended identity propagation) or DOMINO_USER_API_KEY."
        )
    return {}


def resolve_project_id(project_id: Optional[str] = None) -> str:
    """Return the given project ID. Does not fall back to the app project."""
    if not project_id:
        raise RuntimeError("No project ID available")
    return project_id
