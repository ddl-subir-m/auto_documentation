"""Per-request context for forwarded authentication headers.

Domino's App proxy forwards the visiting user's JWT in the standard
``Authorization`` header.  The middleware in ``web_app_studio.py`` captures it
into a ContextVar so that outbound Domino API calls (datasets, jobs)
run as the *viewer*, not the app owner.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

_auth_header_var: ContextVar[Optional[str]] = ContextVar(
    "forwarded_authorization_header", default=None
)


def set_request_auth_header(value: Optional[str]) -> None:
    _auth_header_var.set(value)


def get_request_auth_header() -> Optional[str]:
    return _auth_header_var.get()


def get_user_auth_headers() -> dict[str, str]:
    """Build auth headers from the forwarded user token.

    Raises ``RuntimeError`` if no token was forwarded, preventing
    silent escalation to the app-owner identity.
    """
    forwarded = get_request_auth_header()
    if not forwarded:
        raise RuntimeError(
            "No forwarded user token available. "
            "Domino API calls require a user token from the incoming request."
        )
    return {"Authorization": forwarded}
