#!/usr/bin/env python3
"""FastHTML UI for Auto Model Documentation — Blueprint Enterprise redesign.

This is the slim orchestrator that imports from the studio package and
assembles the application.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from fasthtml.common import *
from starlette.requests import Request

from autodoc.core.config import Settings

from studio.state import (
    _DOMINO_AVAILABLE,
    _POLL_TASK,
    _STARTUP_WARNINGS,
    _set_target_project,
    _get_default_code_root,
    _get_default_spec_path,
    _get_username,
    domino_client,
    domino_job_store,
    logger,
)
from studio.styles import STUDIO_CSS
from studio.scripts import MAIN_DOM_JS
from studio.ui_components import (
    _render_warnings_banner,
    _render_job_history_table,
    _validate_environment,
)
from studio.job_engine import (
    _poll_domino_jobs,
    _reconcile_stale_jobs,
)
from studio.routes_api import register_api_routes
from studio.routes_spec import register_spec_routes
from studio.routes_job import register_job_routes


# ---------------------------------------------------------------------------
# Create FastHTML app with styles and scripts
# ---------------------------------------------------------------------------

app, rt = fast_app(
    pico=False,
    hdrs=(
        # Load htmx synchronously to ensure it's ready before user interaction
        Script(src="https://unpkg.com/htmx.org@1.9.10"),
        Style(STUDIO_CSS),
        # NOTE: output defaults script is injected per-request in index()
        # so it picks up the resolved target project name.
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

    # Guard: projectId query param is required.  Domino's reverse proxy
    # strips query params from the iframe URL, so if it's missing we serve
    # a bootstrap page whose JS extracts the ID from the parent frame,
    # hash fragment, or postMessage and reloads with it in the URL.
    project_id = req.query_params.get("projectId") or None
    if not project_id:
        return (
            Title("Auto Model Docs Studio"),
            Style(STUDIO_CSS),
            Script(r"""
                (function() {
                    var pid = null;

                    // 1. Hash fragment (#projectId=xxx — survives proxies)
                    if (window.location.hash) {
                        var h = window.location.hash.substring(1);
                        if (h.charAt(0) === '?') h = h.substring(1);
                        pid = new URLSearchParams(h).get('projectId');
                    }

                    // 2. Parent frame (same-origin deployments)
                    if (!pid && window.parent !== window) {
                        try {
                            var pLoc = window.parent.location;
                            pid = new URLSearchParams(pLoc.search).get('projectId');
                            if (!pid && pLoc.hash) {
                                var ph = pLoc.hash.substring(1);
                                if (ph.charAt(0) === '?') ph = ph.substring(1);
                                pid = new URLSearchParams(ph).get('projectId');
                            }
                        } catch(e) { /* cross-origin */ }
                    }

                    // 3. Referrer URL (Domino sets this on the outer page)
                    if (!pid && document.referrer) {
                        try {
                            pid = new URL(document.referrer).searchParams.get('projectId');
                        } catch(e) {}
                    }

                    if (pid) {
                        // Reload with projectId in the query string
                        var url = new URL(window.location.href);
                        url.searchParams.set('projectId', pid);
                        window.location.replace(url.toString());
                        return;
                    }

                    // 4. Listen for postMessage from Domino parent frame
                    var allowedOrigin = window.location.origin;
                    window.addEventListener('message', function(e) {
                        if (e.origin !== allowedOrigin) return;
                        if (e.data && typeof e.data === 'object' && e.data.projectId) {
                            var url = new URL(window.location.href);
                            url.searchParams.set('projectId', e.data.projectId);
                            window.location.replace(url.toString());
                        }
                    });

                    // Show error after a short wait if nothing found
                    setTimeout(function() {
                        var el = document.getElementById('project-id-error');
                        if (el) el.style.display = '';
                    }, 2000);
                })();
            """),
            Div(
                Div(
                    H1("Auto Model Docs Studio", cls="domino-header-title"),
                    P("Enterprise Architectural Documentation Suite", cls="domino-header-subtitle"),
                    cls="domino-header-inner",
                ),
                cls="domino-header",
            ),
            Div(
                Div(
                    Div(
                        Span("Resolving project...",
                             style="color: var(--outline); font-size: 0.875rem;"),
                    ),
                    style="display: flex; justify-content: center; padding-top: 4rem;",
                ),
                Div(
                    Div(
                        H2("Project ID required"),
                        P(
                            "This app must be launched with a ",
                            Code("projectId"),
                            " query parameter so it knows which project to run jobs in, "
                            "store spec files to, and write output to.",
                        ),
                        P(
                            "If you're running this as a Domino App, make sure the app is "
                            "configured to pass the project ID to the iframe URL.",
                            style="color: var(--outline); margin-top: 0.5rem;",
                        ),
                        style="background: rgba(186,26,26,0.06); border-left: 3px solid #ba1a1a; "
                              "border-radius: 2px; padding: 1.5rem; max-width: 640px; "
                              "font-family: Inter, sans-serif;",
                    ),
                    id="project-id-error",
                    style="display: none; justify-content: center; padding-top: 2rem;",
                ),
                cls="page",
            ),
        )

    if _set_target_project(project_id) and _DOMINO_AVAILABLE:
        _reconcile_stale_jobs()

    # Resolve display name from the (now-cached) target project.
    project_display_name: Optional[str] = None
    if _DOMINO_AVAILABLE and project_id:
        info = domino_client.resolve_project(project_id)  # hits _project_cache
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

    # Pre-fetch branches and hardware tiers for server-side rendering
    branch_options = []
    tier_data = []
    default_tier = ""
    if _DOMINO_AVAILABLE:
        if project_id:
            try:
                _branches_raw = domino_client.list_branches_api(project_id)
                branch_options = [Option(b["name"], value=b["name"]) for b in _branches_raw]
            except Exception:
                pass
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

    # ── Build the 3-column layout ────────────────────────────────────────

    # LEFT COLUMN: What to document
    left_col_children = [
        Div(
            H2("What to document"),
            Span("Section 01", cls="step-badge"),
            cls="col-header",
        ),
    ]

    # Spec file card
    spec_card_children = []
    # Hidden field that stores the resolved spec path for form submission
    spec_card_children.append(
        Input(name="spec_path", id="field-spec_path", type="hidden", value=""),
    )

    # Dataset browser + upload
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
                Span("OR", cls="or-divider-text"),
                cls="or-divider",
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
            H4("Auto Model Docs Studio"),
            P("Upload a YAML spec file to define which sections to include in your model documentation. "
              "The system will parse endpoints and data models automatically."),
            cls="insight-card",
        )
    )

    # MIDDLE COLUMN: Configuration & Run
    mid_col_children = [
        Div(
            H2("Configuration & Run"),
            Span("Section 02", cls="step-badge"),
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
            cls="field",
        )
    )
    # Branch
    if branch_options:
        branch_input = Select(*branch_options, name="branch", id="field-branch")
    else:
        branch_input = Input(name="branch", id="field-branch", type="text", value="", placeholder="Default branch")
    run_card_children.append(
        Div(
            Div(
                Label("Branch", for_="field-branch"),
                Span("\u24d8", cls="info-tooltip", data_tooltip="Leave blank to use the project's default branch."),
                cls="label-row",
            ),
            branch_input,
            cls="field",
        )
    )
    # Hardware tier (card grid)
    tier_cards = []
    for t in tier_data if tier_data else []:
        tid = t.get("id", "")
        tname = t.get("name") or tid
        is_default = t.get("isDefault", False) or tid == default_tier
        tier_cards.append(
            Div(
                Div(tname, cls="hw-tier-card-name"),
                cls=f"hw-tier-card{' selected' if is_default else ''}",
                data_tier_id=tid,
                onclick=f"selectHwTier(this, '{tid}')",
            )
        )
    if not tier_cards:
        tier_cards.append(
            Div(
                Div("(default)", cls="hw-tier-card-name"),
                cls="hw-tier-card selected",
                data_tier_id="",
                onclick="selectHwTier(this, '')",
            )
        )
    run_card_children.append(
        Div(
            Div(
                Label("Hardware tier"),
                Span("\u24d8", cls="info-tooltip", data_tooltip="Compute tier for the Domino job."),
                cls="label-row",
            ),
            Input(type="hidden", name="hardware_tier", id="field-hardware_tier",
                  value=default_tier or ""),
            Div(*tier_cards, cls="hw-tier-grid"),
            cls="field",
        )
    )

    # More run settings (expandable)
    more_settings_children = []
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
            style="display: none;",
        )
    )
    # Gear button to open advanced settings modal
    more_settings_children.append(
        Button(
            NotStr('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.32 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>'),
            Span("Advanced settings"),
            type="button",
            id="gear-settings-btn",
            onclick="document.getElementById('gear-popover').style.display='flex'",
        )
    )

    # Gear modal with advanced fields (still inside the form)
    gear_popover_fields = [
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
                    Input(name="planning_workers", id="field-planning_workers", type="number", value="3"),
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
    ]

    more_settings_children.append(
        Div(
            Div(
                Div(
                    Span("Advanced settings", cls="gear-popover-title"),
                    Button(
                        "\u2715",
                        type="button",
                        cls="gear-popover-close",
                        onclick="document.getElementById('gear-popover').style.display='none'",
                    ),
                    id="gear-popover-header",
                ),
                Div(*gear_popover_fields, id="gear-popover-content"),
                id="gear-popover-inner",
            ),
            id="gear-popover",
            style="display: none;",
            onclick="if(event.target===this)this.style.display='none'",
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

    run_card_children.append(
        Div(
            Button("Generate Documentation", type="submit", id="generate-btn", cls="primary"),
            cls="card-footer",
        )
    )

    mid_col_children.append(Div(*run_card_children, cls="bp-card"))

    # RIGHT COLUMN: History
    right_col_children = [
        Div(
            H2("History"),
            Span("Section 03", cls="step-badge"),
            cls="col-header",
        ),
    ]

    right_col_children.append(
        Div(
            Div(
                _render_job_history_table(username),
                id="job-history-content",
            ),
            cls="output-panel",
        )
    )

    return (
        Title("Auto Model Docs Studio"),
        # Header
        Div(
            Div(
                H1("Auto Model Docs Studio", cls="domino-header-title"),
                P("Enterprise Architectural Documentation Suite", cls="domino-header-subtitle"),
                cls="domino-header-inner",
            ),
            cls="domino-header",
        ),
        # Page content
        Div(
            # Environment warnings
            *_render_warnings_banner(_STARTUP_WARNINGS),
            # Form wrapping 3 columns
            Form(
                Div(
                    # Left column
                    Div(*left_col_children, cls="studio-col-left"),
                    # Middle column
                    Div(*mid_col_children, cls="studio-col-mid"),
                    # Right column
                    Div(*right_col_children, cls="studio-col-right"),
                    cls="studio-grid",
                ),
                id="main-form",
                data_execution_mode="domino",
                hx_post="run",
                hx_target="#job-history-content",
                hx_swap="innerHTML",
                hx_encoding="multipart/form-data",
                enctype="multipart/form-data",
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
    from studio.state import auth_context as _auth_context, _DOMINO_AVAILABLE as _da
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
    import studio.state as _state
    _state._STARTUP_WARNINGS = _validate_environment()
    for w in _state._STARTUP_WARNINGS:
        logger.warning(f"Startup: [{w.level}] {w.message} {w.action}")
    if _DOMINO_AVAILABLE:
        # NOTE: init_db() and _reconcile_stale_jobs() are deferred to
        # _set_target_project() — the DB path depends on the target project
        # which is only known after the first page load provides ?projectId.
        _state._POLL_TASK = asyncio.create_task(_poll_domino_jobs())


@app.on_event("shutdown")
async def _on_shutdown():
    import studio.state as _state
    if _state._POLL_TASK:
        _state._POLL_TASK.cancel()


# ---------------------------------------------------------------------------
# Serve
# ---------------------------------------------------------------------------

serve(host=HOST, port=PORT)
