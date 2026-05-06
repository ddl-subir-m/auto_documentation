"""Centralized LLM prompts for Auto Model Documentation.

This module contains all prompts used throughout the system,
making it easy to review, update, and maintain them in one place.
"""

import json
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from autodoc.core.models import LanguageProfile
    from autodoc.scanning.file_card import FileCard


# =============================================================================
# Doc-type framing (MRM)
# =============================================================================
# When the spec was loaded from a canonical template (mdd/vr/mr), the prompt
# stack injects a short framing block so the LLM understands the regulatory
# voice and emphasis appropriate to the document being produced. Non-canonical
# specs (user-supplied YAML) get no framing — prompts are byte-identical to
# the pre-MRM behavior.

DOC_TYPE_FRAMING: Dict[str, str] = {
    "mdd": (
        "You are documenting an MDD (Model Development Document) — the developer's "
        "primary record of how the model was built, intended for validators and "
        "compliance reviewers under SR 11-7 and EU AI Act Article 11. Emphasize "
        "intended use, data lineage, methodology choices, developmental testing, "
        "and known limitations. Treat governance bundle facts as the source of "
        "truth for risk tier and intended use."
    ),
    "vr": (
        "You are documenting a VR (Validation Report) — written from an "
        "independent validator's perspective, not the developer's. Emphasize "
        "challenger comparisons, backtest stability, conceptual soundness review, "
        "outcomes analysis, and findings. Use measured, critical language; flag "
        "weaknesses explicitly. Do not advocate for the model."
    ),
    "mr": (
        "You are documenting an MR (Monitoring Report) — an ongoing operational "
        "record covering a defined monitoring period. Emphasize population "
        "stability, performance drift, alert thresholds, breaches, remediation "
        "actions, and the disposition of any prior findings. Past tense, "
        "period-bounded statements."
    ),
}

DOC_TYPE_LABELS: Dict[str, str] = {
    "mdd": "Model Development Document",
    "vr": "Validation Report",
    "mr": "Monitoring Report",
}


def doc_type_framing_block(doc_type: Optional[str]) -> str:
    """Return a framing paragraph for the given doc_type, or empty string."""
    if not doc_type:
        return ""
    framing = DOC_TYPE_FRAMING.get(doc_type.lower())
    if not framing:
        return ""
    return f"\n## Document Type Framing\n{framing}\n"


# =============================================================================
# Evidence integrity (anti-fabrication) — single source of truth
# =============================================================================
# Previously this guidance was duplicated across narrative/table/chart/list
# prompts in slightly different wording. Drift between copies is the worst
# possible failure mode for an MRM-grade tool. Keep one canonical block;
# every content prompt appends it.

EVIDENCE_INTEGRITY_RULES = (
    "## Evidence integrity rules (apply to ALL content)\n"
    "- ONLY include facts, metrics, methods, and libraries that are explicitly "
    "present in the context above (code, MLflow evidence, governance bundle).\n"
    "- Do NOT fabricate, estimate, round, or invent any numerical values.\n"
    "- Do NOT claim techniques (cross-validation, SMOTE, calibration, etc.) "
    "unless they are imported and used in the code or recorded in MLflow.\n"
    "- If specific data is not available, say so plainly or omit — never "
    "fill the gap with plausible-sounding defaults.\n"
    "- Any model risk classification, validation status, regulatory mapping, "
    "approval state, or intended use claim must come verbatim from the "
    "Governance Bundle Context block. Do not infer these from code.\n"
    "- When you reference a specific metric, code location, or governance "
    "fact, include a citation marker [@citation_id] using an id from the "
    "evidence sections above. Do not over-cite generic prose."
)


# =============================================================================
# Bundle context redaction + citation minting
# =============================================================================

# Bundle-level keys safe to forward to the LLM as grounding. Anything outside
# this allowlist is dropped during redaction (drops internal IDs, attachments
# blobs, audit fields, etc.). Conservative on purpose — easier to widen later
# than to recall a token leak.
_BUNDLE_KEY_ALLOWLIST: tuple[str, ...] = (
    "name",
    "owner",
    "risk_tier",
    "riskTier",
    "intended_use",
    "intendedUse",
    "status",
    "version",
    "policyId",
    "projectId",
    "createdAt",
    "updatedAt",
)

# Top-level policy_def keys to keep when present. Stages are walked separately
# (excluding approvals[], mirroring spec_from_policy._walk_domino_policy).
_POLICY_KEY_ALLOWLIST: tuple[str, ...] = (
    "id",
    "version",
    "name",
    "framework",
)

# Section keyword → bundle/policy citation prefixes that should be passed in.
# Default (no match) keeps everything — safer than silently suppressing.
_SECTION_KEYWORD_RELEVANCE: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("purpose", "intended use", "executive summary", "business", "overview"),
        ("bundle.intended_use", "bundle.name", "bundle.risk_tier", "bundle.owner",
         "policy.framework", "policy.version"),
    ),
    (
        ("limitation", "weakness", "risk", "regulatory", "compliance"),
        ("bundle.risk_tier", "bundle.intended_use", "policy.framework",
         "policy.stages"),
    ),
    (
        ("governance", "approval", "role", "ownership", "oversight"),
        ("bundle.owner", "bundle.status", "policy.stages", "policy.framework"),
    ),
    (
        ("monitoring", "ongoing", "drift"),
        ("bundle.risk_tier", "policy.stages"),
    ),
    (
        ("validation", "challenger", "outcome", "backtest"),
        ("bundle.risk_tier", "policy.stages", "policy.framework"),
    ),
)


def _walk_policy_for_grounding(policy_def: Any) -> List[Dict[str, Any]]:
    """Return a redacted list of stages from a Domino policy.

    Mirrors ``autodoc.spec_from_policy._walk_domino_policy``: skips
    ``approvals[]`` (workflow gate questions, not documentation) and
    ``artifactType: text`` entries (free-text guidance blurbs). Only emits
    stage name + the ordered list of evidence labels.
    """
    if not isinstance(policy_def, dict):
        return []

    inner = policy_def.get("definition")
    if isinstance(inner, dict):
        policy = inner
    else:
        policy = policy_def

    stages = policy.get("stages")
    if not isinstance(stages, list):
        return []

    out: List[Dict[str, Any]] = []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        name = stage.get("name")
        labels: List[str] = []
        for es in stage.get("evidenceSet") or []:
            if not isinstance(es, dict):
                continue
            for art in (es.get("definition") or es.get("artifacts") or []):
                if not isinstance(art, dict):
                    continue
                if art.get("artifactType") == "text":
                    continue
                details = art.get("details")
                if not isinstance(details, dict):
                    continue
                label = details.get("label")
                if isinstance(label, str) and label.strip():
                    labels.append(label.strip())
        if (isinstance(name, str) and name.strip()) or labels:
            out.append({
                "name": name.strip() if isinstance(name, str) else None,
                "evidence": labels,
            })
    return out


def redact_bundle_context(bundle_context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Strip non-grounding noise from a bundle context dict.

    Returns a small dict with shape::

        {
            "bundle_id": str,
            "policy_version_id": str,
            "bundle": {<allowlisted keys only>},
            "policy": {<allowlisted top-level keys>, "stages": [...]}
        }

    **Idempotent**: passing the result of ``redact_bundle_context`` back in
    yields the same output (the function recognises already-redacted shape
    via the ``policy`` key and the absence of ``policy_def``).

    Empty input → empty dict. Never raises; defensive on every read since
    the input is an external governance API blob.
    """
    if not bundle_context:
        return {}

    out: Dict[str, Any] = {}

    bundle_id = bundle_context.get("bundle_id")
    if isinstance(bundle_id, str) and bundle_id:
        out["bundle_id"] = bundle_id

    pv_id = bundle_context.get("policy_version_id")
    if isinstance(pv_id, str) and pv_id:
        out["policy_version_id"] = pv_id

    bundle = bundle_context.get("bundle")
    if isinstance(bundle, dict):
        kept = {
            k: bundle[k]
            for k in _BUNDLE_KEY_ALLOWLIST
            if k in bundle and bundle[k] not in (None, "", [], {})
        }
        if kept:
            out["bundle"] = kept

    policy_def = bundle_context.get("policy_def")
    if isinstance(policy_def, dict):
        policy: Dict[str, Any] = {}
        # Top-level scalars from the wrapper or unwrapped body.
        inner = policy_def.get("definition") if isinstance(policy_def.get("definition"), dict) else policy_def
        if isinstance(inner, dict):
            for k in _POLICY_KEY_ALLOWLIST:
                if k in inner and inner[k] not in (None, "", [], {}):
                    policy[k] = inner[k]
        # Stages (filtered).
        stages = _walk_policy_for_grounding(policy_def)
        if stages:
            policy["stages"] = stages
        if policy:
            out["policy"] = policy
    else:
        # Already-redacted shape: input has `policy` (post-walk) instead of
        # `policy_def`. Pass through unchanged so re-redaction is a no-op.
        existing_policy = bundle_context.get("policy")
        if isinstance(existing_policy, dict) and existing_policy:
            out["policy"] = existing_policy

    return out


def _mint_bundle_facts(redacted: Dict[str, Any]) -> List[tuple[str, str]]:
    """Flatten a redacted bundle into ``(citation_id, value)`` pairs.

    Stable IDs use dotted paths (``bundle.owner``, ``policy.framework``,
    ``policy.stages.<name>``) so the LLM can cite governance facts the same
    way it cites code or MLflow evidence.
    """
    pairs: List[tuple[str, str]] = []
    if "bundle_id" in redacted:
        pairs.append(("bundle.id", redacted["bundle_id"]))
    if "policy_version_id" in redacted:
        pairs.append(("policy.version_id", redacted["policy_version_id"]))

    bundle = redacted.get("bundle") or {}
    for key, value in bundle.items():
        pairs.append((f"bundle.{key}", _stringify_value(value)))

    policy = redacted.get("policy") or {}
    for key, value in policy.items():
        if key == "stages":
            continue
        pairs.append((f"policy.{key}", _stringify_value(value)))

    return pairs


def _stringify_value(value: Any) -> str:
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, default=str, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def slice_relevant_keys(
    section_name: str, redacted: Dict[str, Any]
) -> Dict[str, Any]:
    """Return only the redacted-bundle subkeys relevant to ``section_name``.

    Keyword match on lowercased section name. No match → return the input
    unchanged (default to broader grounding). This is the per-section
    pruning hook called by SectionPlanner.slice_for_section.
    """
    if not redacted:
        return {}
    name = (section_name or "").lower()
    matched_prefixes: set[str] = set()
    for keywords, prefixes in _SECTION_KEYWORD_RELEVANCE:
        if any(kw in name for kw in keywords):
            matched_prefixes.update(prefixes)
    if not matched_prefixes:
        return redacted

    def _keep_top(top_key: str, sub_key: str) -> bool:
        return (
            f"{top_key}.{sub_key}" in matched_prefixes
            or top_key in matched_prefixes
            or "policy.stages" in matched_prefixes and top_key == "policy" and sub_key == "stages"
        )

    out: Dict[str, Any] = {}
    if "bundle_id" in redacted and ("bundle.id" in matched_prefixes or "bundle" in matched_prefixes):
        out["bundle_id"] = redacted["bundle_id"]
    if "policy_version_id" in redacted and (
        "policy.version_id" in matched_prefixes or "policy" in matched_prefixes
    ):
        out["policy_version_id"] = redacted["policy_version_id"]

    bundle = redacted.get("bundle") or {}
    bundle_kept = {k: v for k, v in bundle.items() if _keep_top("bundle", k)}
    if bundle_kept:
        out["bundle"] = bundle_kept

    policy = redacted.get("policy") or {}
    policy_kept: Dict[str, Any] = {}
    for k, v in policy.items():
        if _keep_top("policy", k):
            policy_kept[k] = v
    if policy_kept:
        out["policy"] = policy_kept

    return out or redacted  # if filter zeroed everything, fall back to full


def format_bundle_context(bundle_context: Optional[Dict[str, Any]]) -> str:
    """Format a governance bundle context dict as a prompt section.

    Returns an empty string when bundle_context is None or empty so that
    prompts are byte-identical to the spec-only flow. Otherwise emits a
    redacted, citation-tagged grounding block. The redactor mirrors the
    ``spec_from_policy`` walker (no approvals, no internal IDs, no schema
    metadata) so prompt grounding stays focused on facts.
    """
    if not bundle_context:
        return ""

    # Always redact (idempotent — see redact_bundle_context). Callers may
    # pass either the raw bundle context or an already-sliced view.
    redacted = redact_bundle_context(bundle_context)
    if not redacted:
        return ""

    facts = _mint_bundle_facts(redacted)
    fact_lines = "\n".join(f"[@{cid}]: {val}" for cid, val in facts) or "(no flat facts)"

    stages = (redacted.get("policy") or {}).get("stages") or []
    stage_lines = ""
    if stages:
        formatted = []
        for s in stages:
            name = s.get("name") or "(unnamed stage)"
            evidence = s.get("evidence") or []
            ev = ", ".join(evidence) if evidence else "(no labeled evidence)"
            formatted.append(f"  - \"{name}\" (evidence: {ev})")
        stage_lines = (
            "\n\nStages (workflow structure, not section content):\n"
            + "\n".join(formatted)
        )

    return (
        "\n\n## Governance Bundle Context "
        "(factual grounding from the governance bundle — cite verbatim where relevant)\n"
        "Use the citation IDs in [@brackets] when quoting these facts.\n\n"
        f"{fact_lines}{stage_lines}"
    )


# =============================================================================
# System Prompts
# =============================================================================

SYSTEM_CODE_ANALYZER = (
    "You are an expert at analyzing machine learning code. "
    "Extract only information that is explicitly present in the provided code. "
    "Do not infer, assume, or fabricate any techniques, libraries, or methods not directly shown in the code."
)

SYSTEM_SECTION_PLANNER = (
    "You are a technical documentation expert. "
    "Plan content blocks based only on data that is actually available in the provided context. "
    "Do not plan content that would require fabricating metrics or methodologies."
)

SYSTEM_NARRATIVE_WRITER = (
    "You are a technical documentation writer for regulated model risk "
    "documentation (SR 11-7, EU AI Act). "
    "Write clear, informative content about machine learning models. "
    "Explain the 'why' behind technical decisions, not just the 'what'. "
    "When the user prompt lists Other sections in this document, do not "
    "duplicate content that those sections will cover. "
    "When you reference specific metrics, parameters, code, or governance "
    "facts, include citation markers using the format [@citation_id] where "
    "citation_id is provided in the evidence sections."
)

SYSTEM_TABLE_GENERATOR = (
    "You are a technical documentation expert. "
    "Generate informative tables for ML documentation. "
    "Include a citation marker [@citation_id] in the table caption when the data comes from a specific source."
)

SYSTEM_CHART_GENERATOR = (
    "You are a data visualization expert. "
    "Generate meaningful chart data. "
    "Include a citation marker [@citation_id] in the chart title when the data comes from a specific source."
)

SYSTEM_LIST_GENERATOR = (
    "You are a technical documentation expert. "
    "Generate clear, informative list items. "
    "Include citation markers [@citation_id] when list items reference specific data sources."
)

SYSTEM_FILE_RANKER = (
    "You are an expert at analyzing ML codebases. "
    "Classify files by their role in the ML pipeline and rank them by documentation importance. "
    "Only classify based on the evidence in the file cards provided."
)


# =============================================================================
# File Ranking Prompts (Stage 2)
# =============================================================================

def build_ranking_prompt(
    file_cards: List["FileCard"],
    profile: Optional["LanguageProfile"] = None,
) -> str:
    """Build prompt for ranking files by ML relevance.

    Args:
        file_cards: List of FileCard objects with extracted metadata.
        profile: Language profile for framework hints.

    Returns:
        Formatted prompt string for LLM ranking.
    """
    cards_text = "\n\n".join(card.to_prompt_text() for card in file_cards)

    framework_line = ""
    if profile:
        framework_line = f"\nLanguage: {profile.display_name}\n{profile.framework_hints}"

    return f"""Analyze the following file cards from an ML codebase and rank them by importance for documentation.
{framework_line}

{cards_text}

For each file, classify its role as one of: entrypoint, training, preprocessing, inference, evaluation, config, utility, irrelevant.

Return the files ranked by documentation importance (most important first). Include only files that are relevant to ML model documentation (exclude irrelevant utility/config files that don't relate to the ML pipeline)."""


RANKING_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "ranked_files": {
            "type": "array",
            "description": "Files ranked by documentation importance, most important first",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path"},
                    "role": {
                        "type": "string",
                        "description": "ML role: entrypoint, training, preprocessing, inference, evaluation, config, utility, irrelevant",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence in classification (0.0 to 1.0)",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Brief reason for this classification",
                    },
                },
                "required": ["path", "role"],
            },
        }
    },
    "required": ["ranked_files"],
}


# =============================================================================
# Code Scanner Prompts
# =============================================================================

def build_code_analysis_prompt(
    code_contents: List[Dict[str, str]],
    profile: Optional["LanguageProfile"] = None,
    file_roles: Optional[Dict[str, str]] = None,
) -> str:
    """Build prompt for analyzing ML codebase.

    Args:
        code_contents: List of dicts with 'file' and 'content' keys.
        profile: Language profile for code-fence and framework hints.
        file_roles: Optional dict mapping file paths to ML roles from ranking.

    Returns:
        Formatted prompt string.
    """
    fence_lang = profile.code_fence_lang if profile else "python"
    parts = []
    for c in code_contents:
        role_hint = ""
        if file_roles and c["file"] in file_roles:
            role_hint = f" (role: {file_roles[c['file']]})"
        parts.append(f"### File: {c['file']}{role_hint}\n```{fence_lang}\n{c['content']}\n```")
    code_text = "\n\n".join(parts)

    # Language-specific framework and library hints
    if profile:
        framework_line = f"\n\nLanguage: {profile.display_name}\n{profile.framework_hints}"
        lib_examples = ", ".join(profile.library_examples)
        transform_cats = ", ".join(profile.transformation_categories)
    else:
        framework_line = ""
        lib_examples = "sklearn, xgboost, tensorflow, pytorch"
        transform_cats = "scaling, encoding, feature engineering"

    return f"""Analyze this machine learning codebase and extract information.
{framework_line}

{code_text}

Extract the following information:
1. Model classes/functions used (e.g., {lib_examples}, etc.)
2. Feature names/columns used in the model
3. Target variable name
4. Data transformations ({transform_cats}, etc.)
5. ML task type (classification, regression, clustering, etc.)
6. Hyperparameters and their values
7. Data sources (files, databases, APIs)
8. Any other insights about the model architecture and training
9. Evidence statements that tie conclusions to specific code locations

CRITICAL INSTRUCTIONS:
- ONLY report techniques, methods, and libraries that are EXPLICITLY present in the code above
- Do NOT infer or assume techniques that are not imported or used in the code
- If you see imbalanced data handling (like class_weight or scale_pos_weight), report ONLY what is actually used
- Do NOT claim SMOTE, cross-validation, or other techniques unless they are explicitly imported and used
- For the "insights" field, only describe what is demonstrably in the code - no assumptions or common practices
- For "code_evidence", provide concise statements that can be quoted in the report.
- Each evidence item must include: statement, file path, symbol (class/function name), and a short code snippet that demonstrates the claim."""


CODE_ANALYSIS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "model_classes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "ML model classes/algorithms used",
        },
        "features": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Feature names/columns",
        },
        "target_variable": {
            "type": "string",
            "description": "Target variable name",
        },
        "transformations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "method": {"type": "string"},
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
            "description": "Data transformations applied",
        },
        "ml_task_type": {
            "type": "string",
            "description": "Type of ML task",
        },
        "hyperparameters": {
            "type": "object",
            "description": "Hyperparameter values",
        },
        "data_sources": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Data sources (files, databases)",
        },
        "insights": {
            "type": "string",
            "description": "Additional insights about the codebase",
        },
        "code_evidence": {
            "type": "array",
            "description": "Evidence statements linking claims to code",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "file": {"type": "string"},
                    "symbol": {"type": "string"},
                    "snippet": {"type": "string"},
                },
                "required": ["statement", "file"],
            },
        },
    },
    "required": ["model_classes", "features"],
}


# =============================================================================
# Section Planner Prompts
# =============================================================================

def build_section_planning_prompt(
    section_name: str,
    hint: Optional[str],
    model_name: Optional[str],
    model_classes: str,
    ml_task_type: str,
    features_preview: str,
    target_variable: str,
    registered_models: str,
    data_sources: str,
    metrics_info: str = "",
    artifacts_info: str = "",
    doc_type: Optional[str] = None,
) -> str:
    """Build prompt for planning section content."""
    model_line = f"\n## Specific Model: {model_name}" if model_name else ""
    framing = doc_type_framing_block(doc_type)

    return f"""Plan content for a model documentation section.
{framing}
## Section: {section_name}
## User Guidance: {hint or 'None provided'}{model_line}

## Project Context
- ML Framework/Models: {model_classes}
- ML Task Type: {ml_task_type}
- Features: {features_preview}
- Target Variable: {target_variable}
- Registered Models: {registered_models}{metrics_info}{artifacts_info}
- Data Sources: {data_sources}

## Task
Determine what content blocks this section should contain to create useful documentation.

Content block types available:
- chart: Visual representation (bar, line, or scatter)
- table: Structured data in rows and columns
- narrative: Explanatory paragraphs (2-4 paragraphs)
- bullet_list: Bulleted list of items
- numbered_list: Numbered/ordered list of steps
- image: Embedded MLflow visualization (feature importance plots, confusion matrices, etc.)

CRITICAL: Only plan content blocks that can be generated from the data provided above.
- Do NOT request tables or charts of metrics that are not explicitly listed in the context
- Do NOT request content about cross-validation unless CV metrics are provided
- Do NOT request visualizations of data that doesn't exist
- Do NOT create both a table and chart showing the same data - choose the most effective format
- If limited data is available, plan fewer content blocks focused on what IS known
- For performance metrics: Use "chart" type to visualize numeric metrics (accuracy, precision, recall, etc.)
- Use "image" type ONLY when specific image artifacts are listed in the artifacts_info above (e.g., confusion_matrix.png, feature_importance.png)
- If metrics are available but no image artifacts, use "chart" to create bar charts showing metric values
- Do NOT request image artifacts that are not explicitly listed in the artifacts_info above
- For "image" blocks: Include a descriptive "title" in the specifics object that adds context beyond the filename
  (e.g., "Feature Importance - XGBoost Credit Risk Model" instead of just "Feature Importance").
  Include the model name, task context, or relevant metric when available.

Consider what would be most valuable for documenting this section. Prefer visual content (images, charts, tables) when data allows. Include 2-4 content blocks."""


SECTION_PLANNING_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "section_title": {
            "type": "string",
            "description": "Display title for the section",
        },
        "content_blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "narrative",
                            "table",
                            "chart",
                            "bullet_list",
                            "numbered_list",
                            "image",
                        ],
                    },
                    "purpose": {
                        "type": "string",
                        "description": "What this content block should accomplish",
                    },
                    "data_needed": {
                        "type": "string",
                        "description": "What data/information to include",
                    },
                    "specifics": {
                        "type": "object",
                        "description": "Additional specifications. For charts: 'chart_type'. For images: 'image_name' and 'title' (a descriptive title with model context, e.g., 'Confusion Matrix - RandomForest Credit Risk Classifier')",
                    },
                },
                "required": ["type", "purpose"],
            },
            "minItems": 1,
            "maxItems": 5,
        },
    },
    "required": ["section_title", "content_blocks"],
}


# =============================================================================
# Content Generator Prompts
# =============================================================================

def _other_sections_block(other_sections: Optional[List[str]], section_name: str) -> str:
    if not other_sections:
        return ""
    others = [s for s in other_sections if s and s != section_name]
    if not others:
        return ""
    bullets = "\n".join(f"  - {s}" for s in others)
    return (
        "\n\n## Other sections in this document (do not duplicate their content)\n"
        f"{bullets}"
    )


def build_narrative_prompt(
    section_name: str,
    purpose: str,
    data_needed: Optional[str],
    model_classes: str,
    ml_task_type: str,
    target_variable: str,
    features: str,
    data_sources: str,
    model_name: Optional[str],
    model_info: str,
    insights: str,
    artifact_data: str = "",
    code_evidence: str = "",
    mlflow_evidence: str = "",
    bundle_context: Optional[Dict[str, Any]] = None,
    doc_type: Optional[str] = None,
    other_sections: Optional[List[str]] = None,
) -> str:
    """Build prompt for generating narrative content."""
    data_line = f"\n## Data Needed: {data_needed}" if data_needed else ""
    model_line = f"\n- Specific Model: {model_name}" if model_name else ""
    artifact_section = f"\n\n## Available Artifact Data\n{artifact_data}" if artifact_data else ""
    code_section = f"\n\n{code_evidence}" if code_evidence else ""
    mlflow_section = f"\n\n{mlflow_evidence}" if mlflow_evidence else ""
    bundle_section = format_bundle_context(bundle_context)
    framing = doc_type_framing_block(doc_type)
    others = _other_sections_block(other_sections, section_name)

    return f"""Write professional documentation content.
{framing}
## Section: {section_name}
## Purpose: {purpose}{data_line}

## Context
- Model Type: {model_classes}
- ML Task: {ml_task_type}
- Target: {target_variable}
- Features: {features}
- Data Sources: {data_sources}{model_line}{model_info}

## Additional Context
{insights or "No additional insights available."}{artifact_section}{code_section}{mlflow_section}{bundle_section}{others}

## Instructions
- Write 2-4 paragraphs of clear, professional prose
- Focus on insights and explanations, not just listing facts
- Explain the "why" behind decisions, not just the "what"
- Use a formal but accessible tone
- Do NOT use markdown formatting (no headers, bullets, or bold)
- Do NOT include a title or heading
- Just write the paragraph content directly

{EVIDENCE_INTEGRITY_RULES}"""


def build_table_prompt(
    purpose: str,
    data_needed: Optional[str],
    features: str,
    model_classes: str,
    transformations: str,
    hyperparameters: str,
    metrics_info: str = "",
    artifact_data: str = "",
    code_evidence: str = "",
    mlflow_evidence: str = "",
    bundle_context: Optional[Dict[str, Any]] = None,
    doc_type: Optional[str] = None,
) -> str:
    """Build prompt for generating table content."""
    artifact_section = f"\n\n## Available Artifact Data\n{artifact_data}" if artifact_data else ""
    code_section = f"\n\n{code_evidence}" if code_evidence else ""
    mlflow_section = f"\n\n{mlflow_evidence}" if mlflow_evidence else ""
    bundle_section = format_bundle_context(bundle_context)
    framing = doc_type_framing_block(doc_type)

    return f"""Generate a data table for documentation.
{framing}
## Purpose: {purpose}
## Data Needed: {data_needed or "Relevant data for this section"}

## Available Context
- Features: {features}
- Model Classes: {model_classes}
- Transformations: {transformations}
- Hyperparameters: {hyperparameters}{metrics_info}{artifact_section}
{code_section}{mlflow_section}{bundle_section}

Generate a useful table with 3-10 rows using ONLY the data provided above.
Include a citation marker [@citation_id] in the table caption referencing the data source.

{EVIDENCE_INTEGRITY_RULES}"""


TABLE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "caption": {
            "type": "string",
            "description": "Table caption/title",
        },
        "columns": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Column headers",
        },
        "rows": {
            "type": "array",
            "items": {"type": "object"},
            "description": "Row data as objects with column names as keys",
        },
    },
    "required": ["caption", "columns", "rows"],
}


def build_chart_prompt(
    purpose: str,
    data_needed: Optional[str],
    chart_type: str,
    model_classes: str,
    ml_task_type: str,
    metrics_hint: str = "",
    artifact_data: str = "",
    code_evidence: str = "",
    mlflow_evidence: str = "",
    bundle_context: Optional[Dict[str, Any]] = None,
    doc_type: Optional[str] = None,
) -> str:
    """Build prompt for generating chart data."""
    artifact_section = f"\n\n## Available Artifact Data\n{artifact_data}" if artifact_data else ""
    code_section = f"\n\n{code_evidence}" if code_evidence else ""
    mlflow_section = f"\n\n{mlflow_evidence}" if mlflow_evidence else ""
    bundle_section = format_bundle_context(bundle_context)
    framing = doc_type_framing_block(doc_type)

    return f"""Generate data for a {chart_type} chart.
{framing}
## Purpose: {purpose}
## Data Needed: {data_needed or "Relevant data for visualization"}{metrics_hint}{artifact_section}

## Context
- Model Type: {model_classes}
- ML Task: {ml_task_type}
{code_section}{mlflow_section}{bundle_section}

## Instructions for Chart Generation:
1. If metrics are provided above (e.g., "roc_auc: 0.6903", "precision: 0.2399"), use them as:
   - labels: The metric names (e.g., ["ROC-AUC", "Precision", "Recall", "F1-Score"])
   - values: The metric values (e.g., [0.6903, 0.2399, 0.3844, 0.2956])
   - title: A descriptive title like "Model Performance Metrics"

2. For performance charts specifically, focus on test/validation metrics (not training metrics).

3. If feature importance data is provided, create a chart of top features.

If NO metrics are provided above, return:
- title: ""
- labels: []
- values: []

Provide labels and values for the chart using ONLY the data provided above.
Include a citation marker [@citation_id] in the chart title referencing the data source.

{EVIDENCE_INTEGRITY_RULES}"""


CHART_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Chart title"},
        "labels": {
            "type": "array",
            "items": {"type": "string"},
            "description": "X-axis labels or categories",
        },
        "values": {
            "type": "array",
            "items": {"type": "number"},
            "description": "Y-axis values",
        },
        "xlabel": {"type": "string", "description": "X-axis label"},
        "ylabel": {"type": "string", "description": "Y-axis label"},
    },
    "required": ["title", "labels", "values"],
}


def build_list_prompt(
    purpose: str,
    data_needed: Optional[str],
    model_classes: str,
    ml_task_type: str,
    features: str,
    code_evidence: str = "",
    mlflow_evidence: str = "",
    bundle_context: Optional[Dict[str, Any]] = None,
    doc_type: Optional[str] = None,
) -> str:
    """Build prompt for generating list content."""
    bundle_section = format_bundle_context(bundle_context)
    framing = doc_type_framing_block(doc_type)

    return f"""Generate a list for documentation.
{framing}
## Purpose: {purpose}
## Data Needed: {data_needed or "Relevant items for this list"}

## Context
- Model Type: {model_classes}
- ML Task: {ml_task_type}
- Features: {features}
{code_evidence}
{mlflow_evidence}{bundle_section}

Generate a descriptive title for this list (e.g., "Key Limitations", "Recommended Actions", "Implementation Steps")
and 5-10 concise, informative items using ONLY the data provided above.
When list items reference specific data from the evidence sections, include citation markers using [@citation_id] format.

{EVIDENCE_INTEGRITY_RULES}"""


LIST_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "A brief, descriptive title for this list section (e.g., 'Key Limitations', 'Recommended Actions')",
        },
        "items": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List items",
            "minItems": 3,
            "maxItems": 15,
        },
    },
    "required": ["items"],  # title optional for backward compatibility
}
