"""Governance attach-back client for the autodoc Domino Job.

One responsibility: POST the generated .docx to the governance bundle as
an attachment at BUILD end, using an ephemeral token from Domino's
``/access-token`` endpoint. No reads. No other governance calls.

Error semantics (see IMPLEMENTATION.md, "Test expectations"):

* 403 — permission revoked mid-Job. Raise ``GovernanceError``. No retry.
* 409 — bundle already has this attachment (B2 idempotency). Treat as
  success; return the existing attachment id if present.
* 429 — exponential backoff with at most ``max_retries`` retries (4 total
  attempts by default), then raise ``GovernanceError``.
* 401 — likely clock skew on the ephemeral token. Re-acquire the token
  once and retry once. If the second attempt is also 401, raise.

The client never calls ``time.sleep`` directly; the sleep function is
injectable so tests can run without real delay.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_TOKEN_URL = "http://localhost:8899/access-token"
_DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


class GovernanceError(Exception):
    """Terminal failure of a governance attach-back call."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class GovernanceClient:
    def __init__(
        self,
        api_host: str,
        token_url: str = DEFAULT_TOKEN_URL,
        http=None,
        sleep: Optional[Callable[[float], None]] = None,
        max_retries: int = 3,
        backoff_base: float = 1.0,
    ):
        if http is None:
            import requests  # lazy import; not needed in tests

            http = requests
        self.api_host = api_host.rstrip("/")
        self.token_url = token_url
        self._http = http
        self._sleep = sleep if sleep is not None else _import_time_sleep()
        self.max_retries = max_retries
        self.backoff_base = backoff_base

    def _fetch_token(self) -> str:
        resp = self._http.get(self.token_url, timeout=10)
        resp.raise_for_status()
        return resp.text.strip()

    @staticmethod
    def _auth_headers(token: str) -> dict:
        if token.startswith("Bearer "):
            return {"Authorization": token}
        return {"Authorization": f"Bearer {token}"}

    def _extract_attachment_id(self, resp) -> str:
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            return ""
        if isinstance(body, dict):
            return str(body.get("attachment_id") or body.get("id") or "")
        return ""

    def attach_document(
        self,
        bundle_id: str,
        file_bytes: bytes,
        label: str,
        filename: str = "document.docx",
    ) -> str:
        url = (
            f"{self.api_host}/api/governance/v1/bundles/{bundle_id}/attachments"
        )
        token = self._fetch_token()
        token_refreshed = False
        retry_attempts = 0

        while True:
            headers = self._auth_headers(token)
            files = {"file": (filename, file_bytes, _DOCX_CONTENT_TYPE)}
            data = {"label": label}
            resp = self._http.post(
                url, headers=headers, files=files, data=data, timeout=30
            )
            status = resp.status_code

            if 200 <= status < 300:
                return self._extract_attachment_id(resp)

            if status == 409:
                logger.info(
                    "Bundle %s already has attachment %r; treating 409 as success",
                    bundle_id,
                    label,
                )
                return self._extract_attachment_id(resp)

            if status == 401:
                if token_refreshed:
                    raise GovernanceError(
                        "Authentication failed after token refresh", 401
                    )
                token = self._fetch_token()
                token_refreshed = True
                continue

            if status == 429:
                if retry_attempts >= self.max_retries:
                    raise GovernanceError(
                        f"Rate limit exceeded after {retry_attempts} retries",
                        429,
                    )
                delay = self.backoff_base * (2 ** retry_attempts)
                self._sleep(delay)
                retry_attempts += 1
                continue

            if status == 403:
                raise GovernanceError("Permission denied", 403)

            raise GovernanceError(
                f"Unexpected governance response (status={status})", status
            )


def perform_attach_back(
    client: GovernanceClient,
    bundle_id: str,
    docx_path: str,
    label: str,
) -> tuple[bool, Optional[str]]:
    """Run attach-back at BUILD end.

    Returns ``(True, attachment_id)`` on success, ``(False, None)`` on
    terminal failure. The caller is responsible for process exit code —
    the provenance row is written independently during ``save_with_provenance``
    and is not touched here.
    """
    try:
        with open(docx_path, "rb") as f:
            payload = f.read()
        attachment_id = client.attach_document(bundle_id, payload, label)
        logger.info(
            "Attached .docx to bundle %s (attachment_id=%s)",
            bundle_id,
            attachment_id or "<unknown>",
        )
        return True, attachment_id
    except GovernanceError as exc:
        logger.error(
            "Governance attach-back failed for bundle %s: %s",
            bundle_id,
            exc,
        )
        return False, None


def _import_time_sleep() -> Callable[[float], None]:
    import time

    return time.sleep
