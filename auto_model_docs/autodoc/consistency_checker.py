"""U18 consistency checks: gap detection + declared-vs-detected diffing.

Produces :class:`Finding` records. Gated at the call site by the
``AUTODOC_AUTO_FINDINGS`` env flag; the checker itself is a pure library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from autodoc.core.models import DocumentSpec

MIN_SECTION_CONTENT_CHARS = 100

BUNDLE_FIELDS: tuple[str, ...] = ("owner", "risk_tier", "intended_use")


@dataclass(frozen=True)
class Finding:
    category: str
    section: str
    description: str
    severity: str = "S2"
    declared: Optional[str] = None
    detected: Optional[str] = None


def detect_gaps(
    spec: DocumentSpec, generated_doc: Mapping[str, str]
) -> list[Finding]:
    """Flag spec sections that are missing from ``generated_doc`` or too thin.

    ``generated_doc`` maps section name -> assembled text content for that
    section. A section is "missing" if it has no key in the mapping; it is
    "thin" if its content (after whitespace stripping) is shorter than
    ``MIN_SECTION_CONTENT_CHARS``.
    """
    findings: list[Finding] = []
    for section in spec.sections:
        name = section.name
        if name not in generated_doc:
            findings.append(
                Finding(
                    category="missing_section",
                    section=name,
                    description=(
                        f"Required section {name!r} has no heading in the "
                        f"generated document."
                    ),
                )
            )
            continue

        content = (generated_doc.get(name) or "").strip()
        if len(content) < MIN_SECTION_CONTENT_CHARS:
            findings.append(
                Finding(
                    category="thin_section",
                    section=name,
                    description=(
                        f"Section {name!r} has only {len(content)} chars of "
                        f"content (minimum {MIN_SECTION_CONTENT_CHARS})."
                    ),
                )
            )
    return findings


def check_declared_vs_detected(
    bundle_context: Optional[Mapping[str, Any]],
    scan_result: Optional[Mapping[str, Any]],
    fields: Sequence[str] = BUNDLE_FIELDS,
) -> list[Finding]:
    """Diff declared bundle fields against SCAN-detected values.

    A finding is emitted only when both a declared value and a detected value
    are present and differ (case-insensitive string compare, trimmed). Missing
    values on either side are silently skipped — those are gap-detection's job,
    not this checker's.
    """
    findings: list[Finding] = []
    if not bundle_context or not scan_result:
        return findings

    declared_source = bundle_context.get("bundle") or {}
    if not isinstance(declared_source, Mapping):
        return findings

    for field_name in fields:
        declared = _as_scalar(declared_source.get(field_name))
        detected = _as_scalar(scan_result.get(field_name))
        if declared is None or detected is None:
            continue
        if _normalize(declared) == _normalize(detected):
            continue
        findings.append(
            Finding(
                category="declared_vs_detected",
                section=field_name,
                declared=declared,
                detected=detected,
                description=(
                    f"Declared {field_name} {declared!r} does not match "
                    f"detected value {detected!r}."
                ),
            )
        )
    return findings


def _as_scalar(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _normalize(value: str) -> str:
    return " ".join(value.split()).lower()
