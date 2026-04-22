"""Tests for canonical doc-spec templates (U2).

Verifies that the three shipped templates (MDD, VR, MR) parse cleanly,
carry the expected frontmatter, and conform to the DocumentSpec shape.
"""

import pytest
import yaml

from autodoc.core.models import DocumentSpec
from autodoc.templates import CANONICAL_TEMPLATES, get_template_path


TEMPLATE_IDS = ["mdd", "vr", "mr"]


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_template_file_exists(template_id):
    path = CANONICAL_TEMPLATES[template_id]
    assert path.exists(), f"Template file missing: {path}"
    assert path.is_file()


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_template_parses_as_yaml(template_id):
    path = CANONICAL_TEMPLATES[template_id]
    with open(path) as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), "Template root must be a mapping"


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_template_version_is_1_0(template_id):
    path = CANONICAL_TEMPLATES[template_id]
    with open(path) as f:
        data = yaml.safe_load(f)
    assert "template_version" in data, "template_version frontmatter is required"
    assert data["template_version"] == "1.0", (
        f"template_version must be '1.0' at launch, got {data['template_version']!r}"
    )


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_template_has_id_and_label(template_id):
    path = CANONICAL_TEMPLATES[template_id]
    with open(path) as f:
        data = yaml.safe_load(f)
    assert data.get("template_id") == template_id
    assert isinstance(data.get("template_label"), str)
    assert data["template_label"].strip()


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_template_has_title_and_sections(template_id):
    path = CANONICAL_TEMPLATES[template_id]
    with open(path) as f:
        data = yaml.safe_load(f)

    assert isinstance(data.get("title"), str) and data["title"].strip()
    sections = data.get("sections")
    assert isinstance(sections, list) and len(sections) >= 5, (
        "A regulator-grade outline needs at least 5 sections"
    )
    for section in sections:
        assert isinstance(section, (str, dict))
        if isinstance(section, str):
            assert section.strip()


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_template_hints_cover_sections(template_id):
    path = CANONICAL_TEMPLATES[template_id]
    with open(path) as f:
        data = yaml.safe_load(f)

    hints = data.get("hints", {})
    assert isinstance(hints, dict) and hints, "Template must provide section hints"

    def section_name(s):
        if isinstance(s, str):
            return s.replace(": per_model", "")
        return s.get("name", "")

    section_names = {section_name(s) for s in data["sections"]}
    hinted = set(hints.keys())
    missing = section_names - hinted
    assert not missing, f"Sections without hints: {sorted(missing)}"

    for name, hint in hints.items():
        assert isinstance(hint, str)
        assert len(hint.strip()) >= 80, (
            f"Hint for {name!r} is too short to guide an LLM narrative"
        )


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_template_loads_via_document_spec(template_id):
    path = CANONICAL_TEMPLATES[template_id]
    spec = DocumentSpec.from_yaml(str(path))
    assert spec.title
    assert len(spec.sections) >= 5
    assert all(s.name for s in spec.sections)


def test_get_template_path_resolves_known_ids():
    for tid in TEMPLATE_IDS:
        path = get_template_path(tid)
        assert path.exists()
    assert get_template_path("MDD") == get_template_path("mdd")


def test_get_template_path_rejects_unknown():
    with pytest.raises(KeyError):
        get_template_path("unknown")


def test_canonical_templates_registry_shape():
    assert set(CANONICAL_TEMPLATES.keys()) == set(TEMPLATE_IDS)
