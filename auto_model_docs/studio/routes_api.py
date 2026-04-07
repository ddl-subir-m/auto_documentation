"""API routes: branches, hardware tiers, language detection, datasets, etc."""

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
    domino_datasets,
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

    def api_datasets(req: Request):
        """List writable datasets for the project."""
        if not _DOMINO_AVAILABLE:
            return Response(json.dumps([]), media_type="application/json")
        pid = _resolve_request_project_id(req)
        logger.info("GET /api/datasets — project=%s", pid)
        try:
            datasets = domino_datasets.list_datasets(pid)
            logger.info("GET /api/datasets — returned %d datasets", len(datasets))
            return Response(json.dumps(datasets), media_type="application/json")
        except Exception as exc:
            logger.warning("Failed to list datasets: %s", exc, exc_info=True)
            return Response(
                json.dumps({"error": str(exc)}),
                status_code=500,
                media_type="application/json",
            )

    rt("/api/datasets")(api_datasets)

    def api_dataset_files(req: Request):
        """Browse files in a dataset (directories + yaml only)."""
        if not _DOMINO_AVAILABLE:
            return Response(json.dumps([]), media_type="application/json")

        dataset_id = req.query_params.get("datasetId", "")
        snapshot_id = req.query_params.get("snapshotId", "")
        path = req.query_params.get("path", "")
        pid = _resolve_request_project_id(req)

        if not dataset_id:
            return Response(
                json.dumps({"error": "datasetId required"}),
                status_code=400,
                media_type="application/json",
            )

        # Resolve snapshot ID if not provided
        if not snapshot_id:
            snapshot_id = domino_datasets.get_rw_snapshot_id(dataset_id, pid)
        if not snapshot_id:
            return Response(
                json.dumps({"error": "Could not resolve snapshot for dataset"}),
                status_code=400,
                media_type="application/json",
            )

        logger.info("GET /api/dataset-files — dataset=%s snapshot=%s path='%s'", dataset_id, snapshot_id, path)
        try:
            files = domino_datasets.list_files(snapshot_id, path, pid)
            logger.info("GET /api/dataset-files — returned %d items", len(files))
            return Response(json.dumps(files), media_type="application/json")
        except Exception as exc:
            logger.warning("Failed to list files: %s", exc, exc_info=True)
            return Response(
                json.dumps({"error": str(exc)}),
                status_code=500,
                media_type="application/json",
            )

    rt("/api/dataset-files")(api_dataset_files)

    async def api_ensure_autodoc_specs(req: Request):
        """Ensure the autodoc dataset exists, return its metadata.

        Uses the unified 'autodoc' dataset (not the legacy 'autodoc-specs').
        Specs live under the specs/ subdirectory within this dataset.
        """
        if not _DOMINO_AVAILABLE:
            return Response(
                json.dumps({"error": "Domino not available"}),
                status_code=400,
                media_type="application/json",
            )
        pid = _resolve_request_project_id(req)
        logger.info("POST /api/ensure-autodoc-specs — project=%s", pid)
        try:
            from dataset_store import AUTODOC_DATASET_NAME
            ds = domino_datasets.ensure_dataset(
                pid,
                name=AUTODOC_DATASET_NAME,
                description="Auto Model Docs artifacts",
            )
            if not ds.get("id"):
                logger.warning("ensure-autodoc-specs returned dataset with empty id: %s", ds)
                raise RuntimeError("Dataset was created/found but has no ID — check Domino Datasets API response")
            if not ds.get("rwSnapshotId"):
                ds["rwSnapshotId"] = domino_datasets.get_rw_snapshot_id(ds["id"], pid)
            logger.info("POST /api/ensure-autodoc-specs — dataset id=%s name=%s", ds.get("id"), ds.get("name"))
            return Response(json.dumps(ds), media_type="application/json")
        except Exception as exc:
            logger.warning("Failed to ensure autodoc dataset: %s", exc, exc_info=True)
            return Response(
                json.dumps({"error": str(exc)}),
                status_code=500,
                media_type="application/json",
            )

    rt("/api/ensure-autodoc-specs")(api_ensure_autodoc_specs)

    async def api_upload_spec_to_dataset(req: Request):
        """Upload a spec file via the DatasetStore."""
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
        # Sanitize: strip path components to prevent directory traversal
        filename = raw_filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or "spec.yaml"
        content = await file_upload.read()
        logger.info("POST /api/upload-spec-to-dataset — file='%s' (%d bytes)", filename, len(content))

        try:
            from dataset_store import get_store
            from artifact_layout import get_layout
            store = get_store()
            upload_path = f"{get_layout().specs_dir}/{filename}"
            store.write_file(upload_path, content)
            logger.info("POST /api/upload-spec-to-dataset — success, path=%s", upload_path)
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

    rt("/api/upload-spec-to-dataset")(api_upload_spec_to_dataset)

    def api_download_template():
        """Serve the bundled doc_spec.yaml as a downloadable reference template."""
        # doc_spec.yaml is in auto_model_docs/ (parent of studio/)
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
