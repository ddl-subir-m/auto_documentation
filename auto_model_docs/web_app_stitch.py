#!/usr/bin/env python3
"""FastHTML UI for Auto Model Documentation — Blueprint Enterprise redesign.

This is the slim orchestrator that imports from the stitch package and
assembles the application.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from fasthtml.common import *
from starlette.requests import Request

from autodoc.core.config import Settings

from stitch.state import (
    JobState,
    DominoJobRecord,
    JOB_STORE,
    ACTIVE_JOB_ID,
    _DOMINO_AVAILABLE,
    _POLL_TASK,
    _STARTUP_WARNINGS,
    _resolve_job,
    _get_default_code_root,
    _get_default_output_dir,
    _get_default_spec_path,
    _get_username,
    domino_client,
    domino_job_store,
    logger,
)
from stitch.styles import STITCH_CSS
from stitch.scripts import SMART_POLLING_JS, MAIN_DOM_JS, get_output_defaults_script
from stitch.ui_components import (
    _render_status,
    _render_domino_status,
    _render_warnings_banner,
    _render_job_history_table,
    _validate_environment,
    _db_record_to_dataclass,
)
from stitch.job_engine import (
    _poll_domino_jobs,
    _reconcile_stale_jobs,
)
from stitch.routes_api import register_api_routes
from stitch.routes_spec import register_spec_routes
from stitch.routes_job import register_job_routes


# ---------------------------------------------------------------------------
# Create FastHTML app with styles and scripts
# ---------------------------------------------------------------------------

app, rt = fast_app(
    pico=False,
    hdrs=(
        # Load htmx synchronously to ensure it's ready before user interaction
        Script(src="https://unpkg.com/htmx.org@1.9.10"),
        # Smart polling / HTMX JS
        Script(SMART_POLLING_JS),
        Style(STITCH_CSS),
        Script(get_output_defaults_script()),
        Script(MAIN_DOM_JS),
    )
)


# ---------------------------------------------------------------------------
# index() — Blueprint Enterprise 3-Column Layout
# ---------------------------------------------------------------------------

@rt("/")
def index(req: Request):
    # Cache the external host on first request so Domino job URLs resolve correctly.
    if _DOMINO_AVAILABLE:
        host = req.headers.get("x-forwarded-host") or req.headers.get("host") or ""
        scheme = req.headers.get("x-forwarded-proto", "https")
        domino_client.set_ui_host(host, scheme)

    # Capture projectId from query string (fallback for non-proxied access;
    # Domino's reverse proxy strips query params from the iframe URL).
    project_id = req.query_params.get("projectId") or None

    # If a cross-project ID was given, resolve its metadata eagerly so the
    # cache is warm for later job submissions and hardware-tier lookups.
    project_display_name: Optional[str] = None
    if project_id and _DOMINO_AVAILABLE:
        info = domino_client.resolve_project(project_id)
        if info:
            project_display_name = f"{info.owner_username}/{info.name}"

    default_spec = _get_default_spec_path()
    username = _get_username()
    try:
        _settings = Settings()
        _current_model = "kimi-k2-0905-preview"
        _current_base_url = _settings.openai_base_url or "https://api.moonshot.ai/v1"
    except Exception:
        _current_model = "kimi-k2-0905-preview"
        _current_base_url = "https://api.moonshot.ai/v1"

    # Determine initial status panel content based on latest Domino job
    initial_status_panel: FT
    latest_domino: Optional[DominoJobRecord] = None
    if _DOMINO_AVAILABLE:
        try:
            domino_job_store.init_db()
            jobs = domino_job_store.get_user_jobs(username, limit=1)
            if jobs:
                latest_domino = _db_record_to_dataclass(jobs[0])
        except Exception:
            pass

    # Auto-infer execution mode: projectId or Domino env -> domino, else -> app
    inferred_mode = "app"
    if project_id and _DOMINO_AVAILABLE:
        inferred_mode = "domino"
    elif not project_id and _DOMINO_AVAILABLE and os.environ.get("DOMINO_PROJECT_ID"):
        inferred_mode = "domino"
    default_mode = inferred_mode

    # Pre-fetch branches and hardware tiers for server-side rendering
    if _DOMINO_AVAILABLE:
        try:
            _branches_raw = domino_client.list_branches()
            branch_options = [Option(b["name"], value=b["name"]) for b in _branches_raw]
        except Exception:
            branch_options = []
        if not branch_options:
            branch_options = [Option("main", value="main"), Option("master", value="master")]
        try:
            tier_data = domino_client.list_hardware_tiers(project_id=project_id)
            default_tier = domino_client.get_project_default_tier()
            tier_options = []
            for t in tier_data:
                tid = t.get("id", "")
                tname = t.get("name") or tid
                is_default = t.get("isDefault", False) or tid == default_tier
                tier_options.append(Option(tname, value=tid, selected=is_default))
        except Exception:
            tier_options = []
        if not tier_options:
            tier_options = [Option("(default)", value="")]
    else:
        branch_options = [Option("(Domino not available)", value="")]
        tier_options = [Option("(Domino not available)", value="")]

    # ── Build the 3-column layout ────────────────────────────────────────

    # LEFT COLUMN: What to document
    left_col_children = [
        Div(
            H2("What to document"),
            Span("STEP 01", cls="step-badge"),
            cls="col-header",
        ),
    ]

    # Spec file card
    spec_card_children = []
    # Hidden field that stores the resolved spec path for form submission
    spec_card_children.append(
        Input(name="spec_path", id="field-spec_path", type="hidden",
              value=str(default_spec) if default_mode == "app" else ""),
    )

    if default_mode == "domino":
        # Domino mode: dataset browser
        spec_card_children.append(
            Div(
                Label("Spec file", Span(" *", cls="required-star")),
                Div(
                    Select(
                        Option("Loading datasets...", value="", disabled=True, selected=True),
                        id="spec-dataset-select",
                    ),
                    cls="field",
                ),
                Div(id="spec-breadcrumb", cls="spec-breadcrumb"),
                Div(
                    Span("Select a dataset to browse spec files", style="color: var(--outline); font-size: 0.8125rem;"),
                    id="spec-file-list",
                    cls="spec-file-list",
                ),
                Div(
                    Span("Selected: ", style="color: var(--outline);"),
                    Span(id="spec-selected-name", style="font-weight: 600; color: var(--on-surface);"),
                    id="spec-selected-indicator",
                    style="display: none; padding: 8px 0; font-size: 0.8125rem;",
                ),
                Div(
                    Label(
                        "Upload from my machine",
                        Input(
                            type="file",
                            accept=".yaml,.yml",
                            id="spec-machine-upload",
                            cls="hidden-upload",
                        ),
                        cls="upload-btn",
                    ),
                    Span(id="spec-upload-status", cls="spec-upload-status"),
                    A("Download reference template", href="api/download-template",
                      download="doc_spec_template.yaml",
                      style="color: var(--primary); font-size: 0.8125rem; margin-left: auto;"),
                    cls="spec-actions-row",
                ),
                cls="field",
            )
        )
    else:
        # App mode: simple file path + upload
        spec_card_children.append(
            Div(
                Label("Spec file", Span(" *", cls="required-star"), for_="field-spec_path_display"),
                Div(
                    Input(
                        id="field-spec_path_display",
                        type="text",
                        value=str(default_spec),
                        placeholder=str(default_spec),
                        oninput="document.getElementById('field-spec_path').value = this.value;",
                    ),
                    Label(
                        "Upload",
                        Input(
                            name="spec_upload",
                            type="file",
                            accept=".yaml,.yml",
                            cls="hidden-upload",
                        ),
                        cls="upload-btn",
                    ),
                    cls="field-inline",
                ),
                Div(id="upload-filename", cls="upload-filename"),
                cls="field",
            )
        )

    spec_card_children.append(Div(id="spec-validation-result"))

    # Filters section
    spec_card_children.append(
        Details(
            Summary("Filters", cls="advanced-section-summary"),
            Div(
                Div(
                    Div(
                        Label("Model names", for_="field-model_names"),
                        Span("\u24d8", cls="info-tooltip", data_tooltip="Comma-separated. Supports wildcards: * and ?"),
                        cls="label-row",
                    ),
                    Input(
                        name="model_names",
                        id="field-model_names",
                        type="text",
                        placeholder="model1, churn*, fraud-*",
                    ),
                    cls="field",
                ),
                Div(
                    Div(
                        Label("Experiment names", for_="field-experiment_names"),
                        Span("\u24d8", cls="info-tooltip", data_tooltip="Comma-separated. Supports wildcards: * and ?"),
                        cls="label-row",
                    ),
                    Input(
                        name="experiment_names",
                        id="field-experiment_names",
                        type="text",
                        placeholder="exp1, exp2, my-experiment*",
                    ),
                    cls="field",
                ),
                Label(
                    Input(type="checkbox", name="latest_only", id="field-latest_only", checked=True),
                    Span("Latest version only"),
                    cls="checkbox-field",
                ),
                cls="advanced-content",
            ),
            cls="advanced-section",
            open=True,
        )
    )

    left_col_children.append(Div(*spec_card_children, cls="bp-card"))

    # Insight card
    left_col_children.append(
        Div(
            H4("Architectural insight"),
            P("Upload a YAML spec file to define which sections to include in your model documentation. "
              "The system will parse endpoints and data models automatically."),
            cls="insight-card",
        )
    )

    # MIDDLE COLUMN: Configuration & Run
    mid_col_children = [
        Div(
            H2("Configuration & Run"),
            Span("STEP 02", cls="step-badge"),
            cls="col-header",
        ),
    ]

    # Run settings card
    run_card_children = []

    # Code root
    run_card_children.append(
        Div(
            Label("Code root path", for_="code-root-suffix"),
            Div(
                Span(str(_get_default_code_root()), id="code-root-prefix", cls="code-root-prefix"),
                Input(
                    id="code-root-suffix",
                    type="text",
                    placeholder="subdirectory (optional)",
                    cls="code-root-suffix",
                ),
                Input(
                    name="code_root",
                    id="field-code_root",
                    type="hidden",
                    value=str(_get_default_code_root()),
                ),
                cls="code-root-wrap",
            ),
            cls="field",
        )
    )
    # Hidden detected language field
    run_card_children.append(
        Input(type="hidden", name="detected_language", id="field-detected-language", value="python"),
    )

    # Language detection row (shown after code root is set)
    run_card_children.append(
        Div(
            Span("Detected: ", style="color: var(--outline);"),
            Span(id="lang-detected-name", style="color: var(--on-surface); font-weight: 600;"),
            Span(id="lang-detected-count", style="color: var(--outline); margin-left: 4px;"),
            Button(
                "Override",
                id="lang-override-btn",
                type="button",
                style="background: none; border: none; color: var(--primary); cursor: pointer; "
                      "padding: 8px 12px; min-height: 44px; font-size: inherit; margin-left: 8px;",
                aria_label="Override detected language",
                onclick="document.getElementById('lang-override-select').style.display = "
                        "document.getElementById('lang-override-select').style.display === 'none' ? 'inline-block' : 'none';",
            ),
            Select(
                Option("Python", value="python"),
                Option("R", value="r"),
                Option("SAS", value="sas"),
                Option("MATLAB", value="matlab"),
                id="lang-override-select",
                style="display: none; border: 1px solid var(--ghost-border); border-radius: 2px; "
                      "padding: 4px 8px; margin-left: 4px; font-size: 0.8125rem;",
                onchange="handleLanguageOverride(this.value)",
            ),
            id="lang-detection-row",
            style="display: none; padding: 8px 0; font-size: 0.8125rem;",
        )
    )

    # Domino-specific fields
    if default_mode == "domino":
        # Target project
        run_card_children.append(
            Div(
                Div(
                    Label("Target project", for_="field-project-id"),
                    Span("\u24d8", cls="info-tooltip", data_tooltip="Domino project ID to run the job in. Leave blank to use the current project."),
                    cls="label-row",
                ),
                Input(
                    name="target_project",
                    id="field-project-id",
                    type="text",
                    value="",
                    placeholder="Leave blank for current project",
                    autocomplete="off",
                ),
                Div(
                    (f"{project_display_name}" if project_display_name else ""),
                    id="project-id-resolved",
                    cls="resolved" if project_display_name else "",
                ),
                cls="field domino-fields",
            )
        )
        # Branch
        run_card_children.append(
            Div(
                Div(
                    Label("Branch", for_="field-branch"),
                    Span("\u24d8", cls="info-tooltip", data_tooltip="Git branch to analyze in the Domino job."),
                    cls="label-row",
                ),
                Select(
                    *branch_options,
                    name="branch",
                    id="field-branch",
                ),
                cls="field domino-fields",
            )
        )
        # Hardware tier
        run_card_children.append(
            Div(
                Div(
                    Label("Hardware tier", for_="field-hardware_tier"),
                    Span("\u24d8", cls="info-tooltip", data_tooltip="Compute tier for the Domino job."),
                    cls="label-row",
                ),
                Select(
                    *tier_options,
                    name="hardware_tier",
                    id="field-hardware_tier",
                ),
                cls="field domino-fields",
            )
        )

    # More run settings (expandable)
    more_settings_children = []
    if default_mode == "domino":
        more_settings_children.append(
            Div(
                Div(
                    Label("Output directory", for_="field-output_dir"),
                    Span(
                        "\u24d8",
                        cls="info-tooltip",
                        data_tooltip="Output files are written here by the Domino job.",
                        id="output-dir-hint",
                    ),
                    cls="label-row",
                ),
                Input(
                    name="output_dir",
                    id="field-output_dir",
                    type="text",
                    value=str(_get_default_output_dir()),
                ),
                cls="field domino-fields",
            )
        )
    more_settings_children.append(
        Div(
            Label("API key"),
            Div(
                Label(
                    Input(type="radio", name="api_key_source", value="domino_env", checked=True),
                    "Domino environment variable (recommended)",
                    cls="api-key-source-option",
                ),
                Label(
                    Input(type="radio", name="api_key_source", value="pass_now"),
                    "Set key",
                    cls="api-key-source-option",
                ),
                cls="api-key-source",
            ),
            Div(id="api-key-callout", cls="api-key-callout"),
            cls="field",
            id="api-key-source-field",
        )
    )
    more_settings_children.append(
        Div(
            Label("API key", Span(" *", cls="required-star"), for_="field-api_key"),
            Input(
                name="api_key",
                id="field-api_key",
                type="password",
                placeholder="Paste your API key",
                autocomplete="new-password",
                spellcheck="false",
            ),
            cls="field",
            id="api-key-pass-field",
            style="display: none;" if default_mode == "domino" else "",
        )
    )

    run_card_children.append(
        Details(
            Summary("More run settings", cls="advanced-section-summary"),
            Div(*more_settings_children, cls="advanced-content"),
            cls="advanced-section",
            open=True,
        )
    )

    mid_col_children.append(Div(*run_card_children, cls="bp-card"))

    # Advanced card
    advanced_card_children = []
    advanced_card_children.append(
        Details(
            Summary(
                Span("Advanced", style="font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 0;"),
                Span("Generation settings, provider, output options", cls="advanced-summary-desc"),
                cls="advanced-section-summary",
            ),
            Div(
                Div("Generation settings", cls="filter-section-title"),
                Div(
                    Div(
                        Label("Max files", for_="field-max_files"),
                        Input(name="max_files", id="field-max_files", type="number", value="50"),
                        cls="field",
                    ),
                    Div(
                        Div(
                            Label("Planning workers", for_="field-planning_workers"),
                            Span("\u24d8", cls="info-tooltip", data_tooltip="Parallel LLM calls in the planning phase."),
                            cls="label-row",
                        ),
                        Input(name="planning_workers", id="field-planning_workers", type="number", value="1"),
                        cls="field",
                    ),
                    Div(
                        Div(
                            Label("Generation workers", for_="field-workers"),
                            Span("\u24d8", cls="info-tooltip", data_tooltip="Sections generated in parallel."),
                            cls="label-row",
                        ),
                        Input(name="workers", id="field-workers", type="number", value="4"),
                        cls="field",
                    ),
                    Div(
                        Div(
                            Label("Timeout (s)", for_="field-timeout"),
                            Span("\u24d8", cls="info-tooltip", data_tooltip="Seconds before a single LLM call times out."),
                            cls="label-row",
                        ),
                        Input(name="timeout", id="field-timeout", type="number", value="120"),
                        cls="field",
                    ),
                    cls="advanced-grid",
                ),
                Div(
                    Label("Provider", for_="field-provider"),
                    Select(
                        Option("Anthropic", value="anthropic"),
                        Option("OpenAI (Compatible)", value="openai", selected=True),
                        name="provider",
                        id="field-provider",
                    ),
                    cls="field",
                ),
                Div(
                    Div(
                        Label("Model", for_="field-model"),
                        Span("\u24d8", cls="info-tooltip", data_tooltip="Leave blank to use default (kimi-k2-0905-preview)"),
                        cls="label-row",
                    ),
                    Input(name="model", id="field-model", type="text", value=_current_model, placeholder="kimi-k2-0905-preview"),
                    cls="field",
                    id="model-name-field",
                    style="display: none;",
                ),
                Div(
                    Div(
                        Label("Base URL", for_="field-base_url"),
                        Span("\u24d8", cls="info-tooltip", data_tooltip="For OpenAI-compatible APIs (e.g., Moonshot, Azure)"),
                        cls="label-row",
                    ),
                    Input(
                        name="base_url",
                        id="field-base_url",
                        type="text",
                        value=_current_base_url,
                        placeholder="https://api.moonshot.ai/v1",
                    ),
                    cls="field",
                    id="base-url-field",
                    style="display: none;",
                ),
                Label(
                    Input(type="checkbox", name="notebook", id="field-notebook", checked=True),
                    Span("Generate notebook"),
                    Span("\u24d8", cls="info-tooltip", data_tooltip="Saved alongside your document in the output directory.", id="app-mode-notebook-hint"),
                    cls="checkbox-field",
                    id="app-mode-note",
                ),
                cls="advanced-content",
            ),
            cls="advanced-section",
            open=True,
        )
    )

    mid_col_children.append(Div(*advanced_card_children, cls="bp-card", style="margin-top: 1rem;"))

    # RIGHT COLUMN: Output & History
    right_col_children = [
        Div(
            H2("Output & History"),
            Span("STEP 03", cls="step-badge"),
            cls="col-header",
        ),
    ]

    # Tabbed output panel
    right_col_children.append(
        Div(
            Div(
                Button("Current Run", type="button", cls="tab-btn active", data_tab="live", onclick="showOutputTab('live')"),
                Button("History", type="button", cls="tab-btn", data_tab="history", onclick="showOutputTab('history')"),
                cls="tab-bar",
            ),
            Div(
                Div(
                    _render_domino_status(latest_domino) if (default_mode == "domino") else _render_status(_resolve_job(ACTIVE_JOB_ID)),
                    id="status-panel",
                    **({"hx_get": "domino-status", "hx_trigger": "every 10s", "hx_swap": "innerHTML settle:0"} if default_mode == "domino" else {}),
                ),
                id="tab-live",
                cls="tab-content",
            ),
            Div(
                Div(
                    _render_job_history_table(username),
                    id="job-history-content",
                ),
                id="tab-history",
                cls="tab-content hidden",
            ),
            cls="output-panel",
        )
    )

    return (
        Title("Auto Model Docs Studio"),
        # Header
        Div(
            Div(
                H2("Auto Model Docs Studio", cls="domino-header-title"),
                cls="domino-header-inner",
            ),
            cls="domino-header",
        ),
        # Page content
        Div(
            # Tagline
            Div(
                P("Generate model documentation with a single, guided workflow.", cls="hero-tagline"),
                cls="hero",
            ),
            # Environment warnings
            *_render_warnings_banner(_STARTUP_WARNINGS),
            # Form wrapping 3 columns
            Form(
                Div(
                    # Left column
                    Div(*left_col_children, cls="stitch-col-left"),
                    # Middle column
                    Div(*mid_col_children, cls="stitch-col-mid"),
                    # Right column
                    Div(*right_col_children, cls="stitch-col-right"),
                    cls="stitch-grid",
                ),
                id="main-form",
                data_execution_mode=inferred_mode,
                hx_post="run",
                hx_target="#status-panel",
                hx_swap="innerHTML",
                hx_encoding="multipart/form-data",
                enctype="multipart/form-data",
            ),
            # Sticky action bar
            Div(
                Div(
                    Div(cls="action-bar-dot"),
                    Span("Ready", cls="action-bar-label"),
                    cls="action-bar-status",
                ),
                Div(
                    Button("Generate Documentation", type="submit", id="generate-btn", cls="primary", form="main-form"),
                    cls="btn-row",
                    style="margin-top: 0;",
                ),
                cls="action-bar",
            ),
            cls="page",
        ),
    )


# ---------------------------------------------------------------------------
# Register route modules
# ---------------------------------------------------------------------------

register_api_routes(rt)
register_spec_routes(rt)
register_job_routes(rt)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

from starlette.middleware.trustedhost import TrustedHostMiddleware

HOST = os.environ.get("APP_HOST", "0.0.0.0")
PORT = int(os.environ.get("APP_PORT", "8888"))

app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "ALLOWALL"
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "HX-Request, HX-Target, HX-Current-URL, Content-Type"
    return response


@app.middleware("http")
async def capture_auth_context(request, call_next):
    from stitch.state import auth_context as _auth_context, _DOMINO_AVAILABLE as _da
    if _da:
        forwarded = request.headers.get("authorization")
        _auth_context.set_request_auth_header(forwarded)
    try:
        response = await call_next(request)
    finally:
        if _da:
            _auth_context.set_request_auth_header(None)
    return response


# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def _on_startup():
    import stitch.state as _state
    _state._STARTUP_WARNINGS = _validate_environment()
    for w in _state._STARTUP_WARNINGS:
        logger.warning(f"Startup: [{w.level}] {w.message} {w.action}")
    if _DOMINO_AVAILABLE:
        domino_job_store.init_db()
        _reconcile_stale_jobs()
        _state._POLL_TASK = asyncio.create_task(_poll_domino_jobs())


@app.on_event("shutdown")
async def _on_shutdown():
    import stitch.state as _state
    if _state._POLL_TASK:
        _state._POLL_TASK.cancel()


# ---------------------------------------------------------------------------
# Serve
# ---------------------------------------------------------------------------

serve(host=HOST, port=PORT)
