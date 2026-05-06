"""Policy-to-spec derivation (U17, F3).

Given a governance policy definition (``policy_def`` from the bundle
context file written by Portal), produce a spec dict shaped like the
canonical templates in ``autodoc/templates/{mdd,vr,mr}_spec.yaml``.

Two input shapes are supported:

* **Flat shape** — ``policy_def["required_artifacts"]`` and/or
  ``policy_def["sections"]`` as lists of strings. Used by direct CLI
  callers that pre-flatten a policy.
* **Domino policy shape** — the raw response from
  ``GET /api/governance/v1/policies/{id}/definition``, optionally wrapped
  as ``{"definition": "<yaml>"}`` or ``{"definition": {...}}``. This is
  what Portal's ``routes/autodoc.py`` writes verbatim into the bundle
  context file. The walker pulls stage names and artifact labels out of
  ``stages[].evidenceSet[].(definition|artifacts)[]`` and
  ``stages[].approvals[].evidence.(definition|artifacts)[]``.

When neither shape yields any usable section names, the canonical
template for ``doc_type`` is returned unchanged and a warning is logged.
Invalid ``doc_type`` is the only condition that raises.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

VALID_DOC_TYPES: tuple[str, ...] = ("mdd", "vr", "mr")
_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _load_template(doc_type: str) -> dict[str, Any]:
    path = _TEMPLATES_DIR / f"{doc_type}_spec.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _dedupe_preserving_order(items: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        stripped = item.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        out.append(stripped)
    return out


def _build_spec(template: dict[str, Any], sections: list[str]) -> dict[str, Any]:
    spec = dict(template)
    spec["sections"] = sections
    template_hints = template.get("hints") or {}
    spec["hints"] = {k: v for k, v in template_hints.items() if k in sections}
    return spec


def _unwrap_definition_envelope(policy_def: dict[str, Any]) -> dict[str, Any]:
    """Unwrap ``{"definition": ...}`` if Portal handed us the raw API response.

    ``GET /api/governance/v1/policies/{id}/definition`` returns either
    ``{"definition": "<yaml string>"}`` or ``{"definition": {...dict...}}``.
    Portal writes that response verbatim into the context file, so we may
    need to unwrap one layer and parse YAML before walking ``stages``.
    Falls back to the input on parse failure.
    """
    inner = policy_def.get("definition")
    if isinstance(inner, str):
        try:
            parsed = yaml.safe_load(inner)
        except yaml.YAMLError as e:
            logger.warning("Failed to parse policy definition YAML: %s", e)
            return policy_def
        if isinstance(parsed, dict):
            return parsed
        return policy_def
    if isinstance(inner, dict):
        return inner
    return policy_def


def _artifact_label(artifact: Any) -> str | None:
    """Extract a human-readable label from a policy artifact entry.

    Domino policy artifacts carry the user-visible string under
    ``details.label``. Guidance entries with ``artifactType: text`` and
    items missing a label are skipped.
    """
    if not isinstance(artifact, dict):
        return None
    if artifact.get("artifactType") == "text":
        return None
    details = artifact.get("details")
    if not isinstance(details, dict):
        return None
    label = details.get("label")
    if isinstance(label, str) and label.strip():
        return label.strip()
    return None


def _walk_domino_policy(policy: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return ``(stage_names, artifact_labels)`` from a parsed Domino policy.

    Walks ``stages[].evidenceSet[]`` for both ``definition`` (raw YAML
    shape) and ``artifacts`` (computed-policy shape). Approval sign-off
    questions (``stages[].approvals[]``) are skipped — those are workflow
    gate questions, not documentation sections.
    """
    stage_names: list[str] = []
    artifact_labels: list[str] = []

    stages = policy.get("stages")
    if not isinstance(stages, list):
        return stage_names, artifact_labels

    for stage in stages:
        if not isinstance(stage, dict):
            continue
        name = stage.get("name")
        if isinstance(name, str) and name.strip():
            stage_names.append(name.strip())

        for es in stage.get("evidenceSet") or []:
            if not isinstance(es, dict):
                continue
            for art in (es.get("definition") or es.get("artifacts") or []):
                label = _artifact_label(art)
                if label:
                    artifact_labels.append(label)

    return stage_names, artifact_labels


def derive_spec(policy_def: dict[str, Any] | None, doc_type: str) -> dict[str, Any]:
    """Derive a spec dict from a governance policy definition.

    Args:
        policy_def: Policy definition dict extracted from the bundle context.
            Accepts the flat shape (``required_artifacts``/``sections``) used
            by direct CLI callers, or the Domino policy shape (raw response
            from ``/policies/{id}/definition``, optionally wrapped in a
            ``definition`` envelope) that Portal serializes. ``None`` or
            unrecognized shapes degrade to the canonical template.
        doc_type: Canonical template identifier. One of :data:`VALID_DOC_TYPES`.

    Returns:
        Spec dict with the same top-level keys as the canonical template for
        ``doc_type``. Sections are seeded from the policy when possible, else
        the canonical template is returned unchanged.

    Raises:
        ValueError: If ``doc_type`` is not one of :data:`VALID_DOC_TYPES`.
    """
    if doc_type not in VALID_DOC_TYPES:
        raise ValueError(
            f"Unknown doc_type {doc_type!r}; must be one of {VALID_DOC_TYPES}"
        )

    template = _load_template(doc_type)

    if not isinstance(policy_def, dict):
        logger.warning(
            "policy_def is not a dict (got %s); falling back to canonical %s template",
            type(policy_def).__name__,
            doc_type,
        )
        return template

    flat_required = policy_def.get("required_artifacts")
    flat_sections = policy_def.get("sections")
    flat_required_is_list = isinstance(flat_required, list)
    flat_sections_is_list = isinstance(flat_sections, list)

    if flat_required_is_list or flat_sections_is_list:
        combined: list[Any] = []
        if flat_required_is_list:
            combined.extend(flat_required)
        if flat_sections_is_list:
            combined.extend(flat_sections)
        derived_sections = _dedupe_preserving_order(combined)
        if not derived_sections:
            logger.warning(
                "policy_def provided no usable section names; "
                "falling back to canonical %s template",
                doc_type,
            )
            return template
        return _build_spec(template, derived_sections)

    policy = _unwrap_definition_envelope(policy_def)
    stage_names, artifact_labels = _walk_domino_policy(policy)
    derived_sections = _dedupe_preserving_order(stage_names + artifact_labels)
    if derived_sections:
        return _build_spec(template, derived_sections)

    logger.warning(
        "policy_def has no usable stages or required_artifacts/sections; "
        "falling back to canonical %s template",
        doc_type,
    )
    return template
