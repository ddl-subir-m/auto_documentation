"""API routes: branches, hardware tiers, language detection, spec files, etc."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from fasthtml.common import *
from starlette.requests import Request
from starlette.responses import FileResponse, Response

from .state import (
    _DOMINO_AVAILABLE,
    _get_default_code_root,
    _resolve_request_project_id,
    domino_client,
)

logger = logging.getLogger(__name__)


def register_api_routes(rt):
    """Register all /api/* routes on the given rt decorator."""

    def api_branches(req: Request):
        """Return an HTML fragment for branch selection.

        Fetches branches from the target project's git repo via the
        Domino API.  Falls back to a text input if the API fails.
        """
        project_id = req.query_params.get("projectId") or None
        search = req.query_params.get("search", "")
        if project_id and _DOMINO_AVAILABLE:
            branches = domino_client.list_branches_api(project_id, search=search)
            if branches:
                options = [Option(b["name"], value=b["name"]) for b in branches]
                return Select(*options, name="branch", id="field-branch")
        # Fallback: free-text input
        return Input(name="branch", id="field-branch", type="text", value="", placeholder="Default branch")

    rt("/api/branches")(api_branches)

    def api_hardware_tiers(req: Request):
        """Return an HTML <select> fragment with available hardware tiers."""
        if not _DOMINO_AVAILABLE:
            return Select(Option("(Domino not available)", value=""), name="hardware_tier", id="field-hardware_tier")
        project_id = req.query_params.get("projectId") or None
        tiers = domino_client.list_hardware_tiers(project_id=project_id)
        default_tier = domino_client.get_project_default_tier()
        options = []
        for t in tiers:
            tid = t.get("id", "")
            tname = t.get("name") or tid
            is_default = t.get("isDefault", False) or tid == default_tier
            options.append(Option(tname, value=tid, selected=is_default))
        if not options:
            options = [Option("(default)", value="")]
        return Select(*options, name="hardware_tier", id="field-hardware_tier")

    rt("/api/hardware-tiers")(api_hardware_tiers)

    def api_detect_language(req: Request):
        """Detect project language by counting source files in code_root."""
        from autodoc.core.models import detect_language as _detect_lang, LANGUAGE_PROFILES

        code_root_param = req.query_params.get("code_root", "")
        if code_root_param:
            code_root = Path(code_root_param)
        else:
            code_root = _get_default_code_root()

        profile, count = _detect_lang(code_root)
        if profile:
            return Response(
                json.dumps({
                    "language": profile.name,
                    "display_name": profile.display_name,
                    "file_count": count,
                }),
                media_type="application/json",
            )
        return Response(
            json.dumps({
                "language": None,
                "display_name": None,
                "file_count": 0,
                "supported": list(LANGUAGE_PROFILES.keys()),
            }),
            media_type="application/json",
        )

    rt("/api/detect-language")(api_detect_language)

    # ------------------------------------------------------------------
    # Spec files (backed by Domino Artifacts DFS)
    # ------------------------------------------------------------------

    def api_spec_files(req: Request):
        """List spec files in the target project's artifacts."""
        if not _DOMINO_AVAILABLE:
            return Response(json.dumps([]), media_type="application/json")
        logger.info("GET /api/spec-files")
        try:
            from .state import spec_store
            specs = spec_store.list_specs()
            logger.info("GET /api/spec-files — returned %d files", len(specs))
            return Response(json.dumps(specs), media_type="application/json")
        except Exception as exc:
            logger.warning("Failed to list spec files: %s", exc, exc_info=True)
            return Response(
                json.dumps({"error": str(exc)}),
                status_code=500,
                media_type="application/json",
            )

    rt("/api/spec-files")(api_spec_files)

    async def api_upload_spec(req: Request):
        """Upload a spec file to the target project's artifacts."""
        if not _DOMINO_AVAILABLE:
            return Response(
                json.dumps({"error": "Domino not available"}),
                status_code=400,
                media_type="application/json",
            )

        form = await req.form()
        file_upload = form.get("file")

        if not file_upload or not hasattr(file_upload, "read"):
            return Response(
                json.dumps({"error": "file is required"}),
                status_code=400,
                media_type="application/json",
            )

        raw_filename = getattr(file_upload, "filename", "spec.yaml")
        filename = raw_filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or "spec.yaml"
        content = await file_upload.read()
        logger.info("POST /api/upload-spec — file='%s' (%d bytes)", filename, len(content))

        try:
            from .state import spec_store
            upload_path = spec_store.save_spec(filename, content.decode("utf-8", errors="replace"))
            logger.info("POST /api/upload-spec — success, path=%s", upload_path)
            return Response(
                json.dumps({"path": upload_path, "fileName": filename}),
                media_type="application/json",
            )
        except Exception as exc:
            logger.warning("Failed to upload spec: %s", exc, exc_info=True)
            return Response(
                json.dumps({"error": str(exc)}),
                status_code=500,
                media_type="application/json",
            )

    rt("/api/upload-spec")(api_upload_spec)

    def api_download_template():
        """Serve the bundled doc_spec.yaml as a downloadable reference template."""
        template_path = Path(__file__).resolve().parent.parent / "doc_spec.yaml"
        if not template_path.exists():
            return Response("Template not found", status_code=404)
        return FileResponse(
            str(template_path),
            media_type="application/x-yaml",
            filename="doc_spec_template.yaml",
        )

    rt("/api/download-template")(api_download_template)

    def api_resolve_project(req: Request):
        """Return resolved project name for a given project ID."""
        pid = req.query_params.get("projectId", "").strip()
        if not pid or not _DOMINO_AVAILABLE:
            return Div(id="project-id-resolved")
        info = domino_client.resolve_project(pid)
        if info:
            return Div(
                f"{info.owner_username}/{info.name}",
                id="project-id-resolved",
                cls="resolved",
                data_project_name=info.name,
            )
        return Div(
            "Could not resolve project ID",
            id="project-id-resolved",
            cls="error",
        )

    rt("/api/resolve-project")(api_resolve_project)
