"""Spec-related routes: validate, save, list."""

from __future__ import annotations

from fasthtml.common import *
from starlette.requests import Request
from starlette.responses import Response

from autodoc.core.models import DocumentSpec

from .state import (
    _DOMINO_AVAILABLE,
    spec_store,
)


def register_spec_routes(rt):
    """Register all spec-related routes on the given rt decorator."""

    async def validate_spec_route(req: Request):
        """Validate uploaded spec YAML and return inline feedback."""
        form = await req.form()
        spec_upload = form.get("spec_upload")
        content = None
        if spec_upload and hasattr(spec_upload, "read"):
            raw = await spec_upload.read()
            content = raw.decode("utf-8", errors="replace")
        else:
            content = form.get("spec_content")

        if not content or not content.strip():
            return Div(
                Span("No spec content to validate.", style="color: var(--outline);"),
                id="spec-validation-result",
            )

        errors = DocumentSpec.validate_spec(content)
        if errors:
            error_items = [Li(e) for e in errors]
            return Div(
                Div(
                    Span("Spec validation failed", style="font-weight: 600; color: var(--error);"),
                    Ul(*error_items, style="margin: 0.25rem 0 0 0; padding-left: 1.25rem; font-size: 0.8125rem;"),
                    cls="spec-validation-error",
                ),
                id="spec-validation-result",
            )

        return Div(
            Span("Spec is valid", style="color: var(--success); font-weight: 500; font-size: 0.8125rem;"),
            id="spec-validation-result",
        )

    rt("/validate-spec")(validate_spec_route)

    async def save_spec_route(req: Request):
        """Auto-save an uploaded spec file and return the saved path."""
        if not _DOMINO_AVAILABLE:
            return Response("Domino not available", status_code=400)
        form = await req.form()
        filename = form.get("spec_filename", "spec.yaml")
        content = form.get("spec_content", "")
        saved = spec_store.save_spec(filename, content)
        return Response(str(saved), media_type="text/plain")

    rt("/save-spec")(save_spec_route)

    def spec_list(req: Request):
        """Return HTML list of saved spec files."""
        if not _DOMINO_AVAILABLE:
            return Div(P("Domino not available.", cls="history-empty"))
        specs = spec_store.list_specs()
        if not specs:
            return Div(P("No saved spec files.", cls="history-empty"), id="spec-list-content")
        items = []
        for s in specs:
            items.append(
                Div(
                    Span(s["name"], style="font-family: monospace;"),
                    Span(f"{s['size_kb']} KB", style="color: var(--outline); margin: 0 0.75rem;"),
                    cls="spec-list-item",
                )
            )
        return Div(*items, id="spec-list-content", cls="spec-list-modal")

    rt("/spec-list")(spec_list)
