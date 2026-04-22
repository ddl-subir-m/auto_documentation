"""Canonical doc-spec templates for autodoc.

Three regulator-aligned templates ship with autodoc at MVP:

- ``mdd`` — Model Development Document
- ``vr``  — Validation Report
- ``mr``  — Monitoring Report

Each template is a YAML file with a ``template_version`` field so generated
docs can record which template produced them (see PRD §9.2).
"""

from pathlib import Path
from typing import Dict

_TEMPLATES_DIR = Path(__file__).parent

CANONICAL_TEMPLATES: Dict[str, Path] = {
    "mdd": _TEMPLATES_DIR / "mdd_spec.yaml",
    "vr": _TEMPLATES_DIR / "vr_spec.yaml",
    "mr": _TEMPLATES_DIR / "mr_spec.yaml",
}


def get_template_path(template_id: str) -> Path:
    """Return the filesystem path for a canonical template id.

    Raises:
        KeyError: if ``template_id`` is not one of the known canonical ids.
    """
    key = template_id.lower()
    if key not in CANONICAL_TEMPLATES:
        known = ", ".join(sorted(CANONICAL_TEMPLATES))
        raise KeyError(f"Unknown template id '{template_id}'. Known: {known}")
    return CANONICAL_TEMPLATES[key]
