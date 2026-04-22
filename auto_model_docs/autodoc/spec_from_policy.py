"""Policy-to-spec derivation (U17, F3).

Given a governance policy definition (``policy_def`` from the bundle
context file written by Portal), produce a spec dict shaped like the
canonical templates in ``autodoc/templates/{mdd,vr,mr}_spec.yaml``.

Sections are seeded from ``policy_def["required_artifacts"]`` and
``policy_def["sections"]``. When the policy is too sparse to seed
sections (neither key is a list, or both resolve to empty), the
canonical template for ``doc_type`` is returned unchanged and a
warning is logged. Invalid ``doc_type`` is the only condition that
raises.
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


def derive_spec(policy_def: dict[str, Any] | None, doc_type: str) -> dict[str, Any]:
    """Derive a spec dict from a governance policy definition.

    Args:
        policy_def: Policy definition dict extracted from the bundle context.
            May be ``None`` or incomplete; the function degrades gracefully.
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

    required_artifacts = policy_def.get("required_artifacts")
    policy_sections = policy_def.get("sections")

    ra_is_list = isinstance(required_artifacts, list)
    ps_is_list = isinstance(policy_sections, list)
    if not ra_is_list and not ps_is_list:
        logger.warning(
            "policy_def missing required keys 'required_artifacts' and 'sections'; "
            "falling back to canonical %s template",
            doc_type,
        )
        return template

    combined: list[Any] = []
    if ra_is_list:
        combined.extend(required_artifacts)
    if ps_is_list:
        combined.extend(policy_sections)
    derived_sections = _dedupe_preserving_order(combined)

    if not derived_sections:
        logger.warning(
            "policy_def provided no usable section names; "
            "falling back to canonical %s template",
            doc_type,
        )
        return template

    spec = dict(template)
    spec["sections"] = derived_sections
    template_hints = template.get("hints") or {}
    spec["hints"] = {k: v for k, v in template_hints.items() if k in derived_sections}
    return spec
