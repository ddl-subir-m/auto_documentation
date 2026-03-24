"""Tests for DocumentSpec.validate_spec() user-friendly validation."""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import textwrap

import pytest

# Import autodoc.core.models directly (bypasses autodoc/__init__.py which
# pulls in the full generation chain that requires Python 3.10+).
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_models_path = os.path.join(_repo_root, "auto_model_docs", "autodoc", "core", "models.py")
_spec = importlib.util.spec_from_file_location("autodoc.core.models", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["autodoc.core.models"] = _models
_spec.loader.exec_module(_models)
DocumentSpec = _models.DocumentSpec


class TestValidateSpec:
    """Tests for DocumentSpec.validate_spec()."""

    def test_valid_minimal_spec(self):
        content = textwrap.dedent("""\
            title: "My Model Doc"
            sections:
              - Executive Summary
              - Data Overview
        """)
        assert DocumentSpec.validate_spec(content) == []

    def test_valid_spec_with_dict_sections(self):
        content = textwrap.dedent("""\
            title: "My Model Doc"
            sections:
              - name: Executive Summary
                hint: Focus on impact
              - name: "Model Performance"
                per_model: true
        """)
        assert DocumentSpec.validate_spec(content) == []

    def test_valid_spec_with_per_model_string(self):
        content = textwrap.dedent("""\
            title: "My Model Doc"
            sections:
              - "Model Performance: per_model"
              - Conclusion
        """)
        assert DocumentSpec.validate_spec(content) == []

    def test_valid_spec_with_all_optional_fields(self):
        content = textwrap.dedent("""\
            title: "My Model Doc"
            authors: "Alice"
            sections:
              - Executive Summary
            hints:
              "Executive Summary": "Focus on business impact"
            citation_style: numeric
            formatting:
              font: Arial
        """)
        assert DocumentSpec.validate_spec(content) == []

    # ── Missing / invalid required fields ─────────────────────────────

    def test_missing_title(self):
        content = textwrap.dedent("""\
            sections:
              - Executive Summary
        """)
        errors = DocumentSpec.validate_spec(content)
        assert len(errors) == 1
        assert "title" in errors[0].lower()

    def test_empty_title(self):
        content = textwrap.dedent("""\
            title: ""
            sections:
              - Executive Summary
        """)
        errors = DocumentSpec.validate_spec(content)
        assert any("title" in e.lower() and "empty" in e.lower() for e in errors)

    def test_title_too_long(self):
        content = f'title: "{"x" * 501}"\nsections:\n  - Overview\n'
        errors = DocumentSpec.validate_spec(content)
        assert any("too long" in e.lower() for e in errors)

    def test_missing_sections(self):
        content = 'title: "My Doc"\n'
        errors = DocumentSpec.validate_spec(content)
        assert any("sections" in e.lower() for e in errors)

    def test_empty_sections_list(self):
        content = textwrap.dedent("""\
            title: "My Doc"
            sections: []
        """)
        errors = DocumentSpec.validate_spec(content)
        assert any("at least 1" in e for e in errors)

    def test_sections_not_a_list(self):
        content = textwrap.dedent("""\
            title: "My Doc"
            sections: "not a list"
        """)
        errors = DocumentSpec.validate_spec(content)
        assert any("must be a list" in e for e in errors)

    def test_too_many_sections(self):
        sections = "\n".join(f"  - Section {i}" for i in range(51))
        content = f'title: "My Doc"\nsections:\n{sections}\n'
        errors = DocumentSpec.validate_spec(content)
        assert any("51" in e and "50" in e for e in errors)

    # ── Invalid section entries ───────────────────────────────────────

    def test_section_empty_string(self):
        content = textwrap.dedent("""\
            title: "My Doc"
            sections:
              - ""
              - Overview
        """)
        errors = DocumentSpec.validate_spec(content)
        assert any("section 1" in e.lower() and "empty" in e.lower() for e in errors)

    def test_section_name_too_long(self):
        content = f'title: "My Doc"\nsections:\n  - "{"x" * 201}"\n'
        errors = DocumentSpec.validate_spec(content)
        assert any("too long" in e.lower() for e in errors)

    def test_section_dict_missing_name(self):
        content = textwrap.dedent("""\
            title: "My Doc"
            sections:
              - per_model: true
        """)
        errors = DocumentSpec.validate_spec(content)
        assert any("missing" in e.lower() and "name" in e.lower() for e in errors)

    def test_section_dict_empty_name(self):
        content = textwrap.dedent("""\
            title: "My Doc"
            sections:
              - name: ""
        """)
        errors = DocumentSpec.validate_spec(content)
        assert any("section 1" in e.lower() and "empty" in e.lower() for e in errors)

    def test_section_dict_hint_too_long(self):
        content = textwrap.dedent(f"""\
            title: "My Doc"
            sections:
              - name: Overview
                hint: "{'x' * 1001}"
        """)
        errors = DocumentSpec.validate_spec(content)
        assert any("hint" in e.lower() and "too long" in e.lower() for e in errors)

    def test_section_dict_unexpected_keys(self):
        content = textwrap.dedent("""\
            title: "My Doc"
            sections:
              - name: Overview
                foo: bar
                baz: 1
        """)
        errors = DocumentSpec.validate_spec(content)
        assert any("unexpected" in e.lower() for e in errors)

    def test_section_invalid_type(self):
        content = textwrap.dedent("""\
            title: "My Doc"
            sections:
              - 42
        """)
        errors = DocumentSpec.validate_spec(content)
        assert any("section 1" in e.lower() for e in errors)

    # ── Invalid optional field types ──────────────────────────────────

    def test_hints_not_a_dict(self):
        content = textwrap.dedent("""\
            title: "My Doc"
            sections:
              - Overview
            hints: "not a dict"
        """)
        errors = DocumentSpec.validate_spec(content)
        assert any("hints" in e.lower() for e in errors)

    def test_formatting_not_a_dict(self):
        content = textwrap.dedent("""\
            title: "My Doc"
            sections:
              - Overview
            formatting: 123
        """)
        errors = DocumentSpec.validate_spec(content)
        assert any("formatting" in e.lower() for e in errors)

    # ── YAML syntax errors ────────────────────────────────────────────

    def test_invalid_yaml_syntax(self):
        content = "title: [unclosed bracket\n"
        errors = DocumentSpec.validate_spec(content)
        assert len(errors) == 1
        assert "yaml" in errors[0].lower()

    def test_invalid_yaml_tabs(self):
        content = "title: My Doc\nsections:\n\t- Overview\n"
        errors = DocumentSpec.validate_spec(content)
        # Tabs can cause YAML errors
        assert len(errors) >= 1

    def test_yaml_not_a_mapping(self):
        content = "- just a list\n- of items\n"
        errors = DocumentSpec.validate_spec(content)
        assert any("mapping" in e.lower() for e in errors)

    def test_yaml_scalar(self):
        content = "just a string"
        errors = DocumentSpec.validate_spec(content)
        assert any("mapping" in e.lower() for e in errors)

    # ── Multiple errors at once ───────────────────────────────────────

    def test_multiple_errors_reported(self):
        content = textwrap.dedent("""\
            sections:
              - ""
              - 42
            hints: "bad"
        """)
        errors = DocumentSpec.validate_spec(content)
        # Should report: missing title, empty section 1, bad section 2 type, bad hints
        assert len(errors) >= 3
