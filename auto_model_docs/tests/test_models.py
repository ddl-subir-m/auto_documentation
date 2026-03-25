"""Tests for autodoc.core.models — data models and language detection."""

import textwrap
from pathlib import Path

import pytest

from autodoc.core.models import (
    LANGUAGE_PROFILES,
    MATLAB_PROFILE,
    PYTHON_PROFILE,
    R_PROFILE,
    SAS_PROFILE,
    CodeContext,
    CodeEvidence,
    DocumentSpec,
    SectionSpec,
    detect_language,
    get_language_profile,
)


# ===========================================================================
# Language Profile Instances
# ===========================================================================


class TestPythonProfile:
    """Verify PYTHON_PROFILE attributes."""

    def test_name(self, python_profile):
        assert python_profile.name == "python"
        assert python_profile.display_name == "Python"

    def test_file_extensions(self, python_profile):
        assert "*.py" in python_profile.file_extensions

    def test_priority_keywords(self, python_profile):
        for kw in ["train", "model", "feature", "pipeline", "predict"]:
            assert kw in python_profile.priority_keywords

    def test_exclude_patterns(self, python_profile):
        for pat in ["test_", "__pycache__", ".git", "venv"]:
            assert pat in python_profile.exclude_patterns

    def test_code_fence_lang(self, python_profile):
        assert python_profile.code_fence_lang == "python"


class TestRProfile:
    """Verify R_PROFILE attributes."""

    def test_name(self, r_profile):
        assert r_profile.name == "r"
        assert r_profile.display_name == "R"

    def test_file_extensions(self, r_profile):
        assert "*.R" in r_profile.file_extensions
        assert "*.r" in r_profile.file_extensions
        assert "*.Rmd" in r_profile.file_extensions

    def test_priority_keywords(self, r_profile):
        for kw in ["train", "model", "fit", "recipe", "workflow"]:
            assert kw in r_profile.priority_keywords

    def test_exclude_patterns(self, r_profile):
        for pat in ["tests/", ".git", "renv/"]:
            assert pat in r_profile.exclude_patterns


class TestSASProfile:
    """Verify SAS_PROFILE attributes."""

    def test_name(self, sas_profile):
        assert sas_profile.name == "sas"
        assert sas_profile.display_name == "SAS"

    def test_file_extensions(self, sas_profile):
        assert "*.sas" in sas_profile.file_extensions
        assert "*.SAS" in sas_profile.file_extensions

    def test_priority_keywords(self, sas_profile):
        for kw in ["PROC", "MODEL", "LOGISTIC", "DATA"]:
            assert kw in sas_profile.priority_keywords

    def test_exclude_patterns(self, sas_profile):
        assert "autoexec.sas" in sas_profile.exclude_patterns

    def test_secret_patterns_present(self, sas_profile):
        assert len(sas_profile.secret_patterns) > 0


class TestMATLABProfile:
    """Verify MATLAB_PROFILE attributes."""

    def test_name(self, matlab_profile):
        assert matlab_profile.name == "matlab"
        assert matlab_profile.display_name == "MATLAB"

    def test_file_extensions(self, matlab_profile):
        assert "*.m" in matlab_profile.file_extensions

    def test_priority_keywords(self, matlab_profile):
        for kw in ["fit", "train", "predict", "model"]:
            assert kw in matlab_profile.priority_keywords

    def test_exclude_patterns(self, matlab_profile):
        assert ".git" in matlab_profile.exclude_patterns

    def test_code_fence_lang(self, matlab_profile):
        assert matlab_profile.code_fence_lang == "matlab"


# ===========================================================================
# get_language_profile
# ===========================================================================


class TestGetLanguageProfile:
    """Tests for get_language_profile()."""

    @pytest.mark.parametrize("name", ["python", "r", "sas", "matlab"])
    def test_valid_names(self, name):
        """Supported names return the correct profile."""
        profile = get_language_profile(name)
        assert profile.name == name

    def test_case_insensitive(self):
        """Lookup is case-insensitive."""
        assert get_language_profile("Python").name == "python"
        assert get_language_profile("R").name == "r"
        assert get_language_profile("SAS").name == "sas"

    def test_invalid_name_raises(self):
        """Unknown language raises ValueError with supported list."""
        with pytest.raises(ValueError, match="Unknown language 'julia'"):
            get_language_profile("julia")

    def test_invalid_name_shows_supported(self):
        """Error message lists the supported languages."""
        with pytest.raises(ValueError, match="Supported:"):
            get_language_profile("fortran")


# ===========================================================================
# detect_language
# ===========================================================================


class TestDetectLanguage:
    """Tests for detect_language()."""

    def test_python_files(self, tmp_path):
        """Directory with .py files is detected as Python."""
        (tmp_path / "train.py").write_text("import sklearn")
        (tmp_path / "utils.py").write_text("def foo(): pass")
        profile, count = detect_language(tmp_path)
        assert profile is not None
        assert profile.name == "python"
        assert count == 2

    def test_r_files(self, tmp_path):
        """Directory with .R files is detected as R."""
        (tmp_path / "model.R").write_text("library(caret)")
        (tmp_path / "preprocess.R").write_text("data <- read.csv('x')")
        (tmp_path / "report.Rmd").write_text("---\ntitle: test\n---")
        profile, count = detect_language(tmp_path)
        assert profile is not None
        assert profile.name == "r"
        assert count == 3

    def test_sas_files(self, tmp_path):
        """Directory with .sas files is detected as SAS."""
        (tmp_path / "analysis.sas").write_text("PROC LOGISTIC;")
        profile, count = detect_language(tmp_path)
        assert profile is not None
        assert profile.name == "sas"
        assert count == 1

    def test_matlab_files(self, tmp_path):
        """Directory with .m files is detected as MATLAB."""
        (tmp_path / "train_model.m").write_text("function y = train(x)")
        (tmp_path / "predict.m").write_text("function y = predict(x)")
        profile, count = detect_language(tmp_path)
        assert profile is not None
        assert profile.name == "matlab"
        assert count == 2

    def test_empty_directory(self, tmp_path):
        """Empty directory returns (None, 0)."""
        profile, count = detect_language(tmp_path)
        assert profile is None
        assert count == 0

    def test_no_supported_files(self, tmp_path):
        """Directory with unsupported file types returns (None, 0)."""
        (tmp_path / "data.csv").write_text("a,b,c")
        (tmp_path / "notes.txt").write_text("hello")
        profile, count = detect_language(tmp_path)
        assert profile is None
        assert count == 0

    def test_mixed_files_highest_count_wins(self, tmp_path):
        """When multiple languages are present, the one with most files wins."""
        # 3 Python files
        for i in range(3):
            (tmp_path / f"mod{i}.py").write_text(f"# py {i}")
        # 1 R file
        (tmp_path / "script.R").write_text("library(caret)")
        profile, count = detect_language(tmp_path)
        assert profile is not None
        assert profile.name == "python"
        assert count == 3

    def test_tie_breaks_by_priority(self, tmp_path):
        """Equal counts break in favor of LANGUAGE_PRIORITY order (python first)."""
        (tmp_path / "code.py").write_text("x = 1")
        (tmp_path / "code.R").write_text("x <- 1")
        profile, count = detect_language(tmp_path)
        assert profile is not None
        # python has higher priority than r
        assert profile.name == "python"
        assert count == 1

    def test_nonexistent_directory(self, tmp_path):
        """Non-existent directory returns (None, 0)."""
        fake = tmp_path / "does_not_exist"
        profile, count = detect_language(fake)
        assert profile is None
        assert count == 0

    def test_recursive_scan(self, tmp_path):
        """detect_language recursively scans subdirectories."""
        sub = tmp_path / "src" / "models"
        sub.mkdir(parents=True)
        (sub / "a.py").write_text("import torch")
        (sub / "b.py").write_text("import numpy")
        profile, count = detect_language(tmp_path)
        assert profile is not None
        assert profile.name == "python"
        assert count == 2


# ===========================================================================
# DocumentSpec.from_yaml
# ===========================================================================


class TestDocumentSpecFromYaml:
    """Tests for DocumentSpec.from_yaml()."""

    def test_valid_yaml(self, tmp_path):
        """Valid YAML produces a correctly populated DocumentSpec."""
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(textwrap.dedent("""\
            title: "Credit Risk Model Documentation"
            authors: "ML Team"
            sections:
              - Executive Summary
              - Data Overview
              - "Model Performance: per_model"
            hints:
              "Executive Summary": "Focus on business impact"
            citation_style: numeric
        """))

        spec = DocumentSpec.from_yaml(str(spec_file))
        assert spec.title == "Credit Risk Model Documentation"
        assert spec.authors == "ML Team"
        assert len(spec.sections) == 3
        assert spec.sections[0].name == "Executive Summary"
        assert spec.sections[0].per_model is False
        assert spec.sections[2].name == "Model Performance"
        assert spec.sections[2].per_model is True
        assert spec.hints["Executive Summary"] == "Focus on business impact"
        assert spec.citation_style == "numeric"

    def test_dict_section_format(self, tmp_path):
        """Sections can be specified as dicts with name, per_model, hint."""
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(textwrap.dedent("""\
            title: "Test Doc"
            sections:
              - name: "Architecture"
                per_model: false
                hint: "Describe the architecture"
              - name: "Metrics"
                per_model: true
        """))

        spec = DocumentSpec.from_yaml(str(spec_file))
        assert len(spec.sections) == 2
        assert spec.sections[0].name == "Architecture"
        assert spec.sections[0].hint == "Describe the architecture"
        assert spec.sections[1].per_model is True

    def test_defaults_applied(self, tmp_path):
        """Missing optional fields use defaults."""
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(textwrap.dedent("""\
            title: "Minimal Doc"
            sections:
              - "Intro"
        """))

        spec = DocumentSpec.from_yaml(str(spec_file))
        assert spec.authors == "Data Science Team"
        assert spec.hints == {}
        assert spec.citation_style == "numeric"
        assert spec.formatting == {}


# ===========================================================================
# DocumentSpec.validate_spec
# ===========================================================================


class TestDocumentSpecValidateSpec:
    """Tests for DocumentSpec.validate_spec()."""

    def test_valid_spec_returns_empty(self):
        """A correct spec returns an empty error list."""
        content = textwrap.dedent("""\
            title: "Good Spec"
            sections:
              - "Section One"
              - "Section Two"
        """)
        errors = DocumentSpec.validate_spec(content)
        assert errors == []

    def test_missing_title(self):
        """Missing title field is reported."""
        content = textwrap.dedent("""\
            sections:
              - "Intro"
        """)
        errors = DocumentSpec.validate_spec(content)
        assert any("title" in e.lower() for e in errors)

    def test_empty_title(self):
        """Empty title is reported."""
        content = textwrap.dedent("""\
            title: ""
            sections:
              - "Intro"
        """)
        errors = DocumentSpec.validate_spec(content)
        assert any("title" in e.lower() and "non-empty" in e.lower() for e in errors)

    def test_title_too_long(self):
        """Title exceeding 500 chars is reported."""
        long_title = "A" * 501
        content = f'title: "{long_title}"\nsections:\n  - "Intro"\n'
        errors = DocumentSpec.validate_spec(content)
        assert any("too long" in e.lower() for e in errors)

    def test_missing_sections(self):
        """Missing sections field is reported."""
        content = 'title: "Test"\n'
        errors = DocumentSpec.validate_spec(content)
        assert any("sections" in e.lower() for e in errors)

    def test_empty_sections_list(self):
        """Empty sections list is reported."""
        content = textwrap.dedent("""\
            title: "Test"
            sections: []
        """)
        errors = DocumentSpec.validate_spec(content)
        assert any("at least 1" in e for e in errors)

    def test_too_many_sections(self):
        """More than 50 sections is reported."""
        sections = "\n".join(f'  - "Section {i}"' for i in range(51))
        content = f'title: "Test"\nsections:\n{sections}\n'
        errors = DocumentSpec.validate_spec(content)
        assert any("51" in e and "50" in e for e in errors)

    def test_empty_section_name(self):
        """A section with blank name is reported."""
        content = textwrap.dedent("""\
            title: "Test"
            sections:
              - ""
        """)
        errors = DocumentSpec.validate_spec(content)
        assert any("empty" in e.lower() for e in errors)

    def test_section_name_too_long(self):
        """Section name exceeding 200 chars is reported."""
        long_name = "B" * 201
        content = f'title: "Test"\nsections:\n  - "{long_name}"\n'
        errors = DocumentSpec.validate_spec(content)
        assert any("too long" in e.lower() for e in errors)

    def test_invalid_yaml_syntax(self):
        """Malformed YAML is reported."""
        content = "title: [unterminated\n"
        errors = DocumentSpec.validate_spec(content)
        assert len(errors) > 0
        assert any("yaml" in e.lower() for e in errors)

    def test_spec_not_a_mapping(self):
        """Spec that is a plain scalar (not dict) is reported."""
        content = "just a string\n"
        errors = DocumentSpec.validate_spec(content)
        assert any("mapping" in e.lower() for e in errors)

    def test_hints_not_a_dict(self):
        """'hints' as a non-dict is reported."""
        content = textwrap.dedent("""\
            title: "Test"
            sections:
              - "Intro"
            hints: "not a dict"
        """)
        errors = DocumentSpec.validate_spec(content)
        assert any("hints" in e.lower() and "mapping" in e.lower() for e in errors)

    def test_formatting_not_a_dict(self):
        """'formatting' as a non-dict is reported."""
        content = textwrap.dedent("""\
            title: "Test"
            sections:
              - "Intro"
            formatting: 42
        """)
        errors = DocumentSpec.validate_spec(content)
        assert any("formatting" in e.lower() and "mapping" in e.lower() for e in errors)

    def test_dict_section_missing_name(self):
        """Dict section without 'name' key is reported."""
        content = textwrap.dedent("""\
            title: "Test"
            sections:
              - per_model: true
        """)
        errors = DocumentSpec.validate_spec(content)
        assert any("name" in e.lower() for e in errors)

    def test_dict_section_unexpected_keys(self):
        """Unexpected keys in dict section are reported."""
        content = textwrap.dedent("""\
            title: "Test"
            sections:
              - name: "Good Section"
                color: "red"
        """)
        errors = DocumentSpec.validate_spec(content)
        assert any("unexpected" in e.lower() for e in errors)

    def test_valid_spec_with_dict_sections(self):
        """Valid spec using dict-format sections returns no errors."""
        content = textwrap.dedent("""\
            title: "Full Spec"
            sections:
              - name: "Overview"
                per_model: false
                hint: "High-level summary"
              - name: "Metrics"
                per_model: true
            hints:
              "Overview": "Provide executive summary"
        """)
        errors = DocumentSpec.validate_spec(content)
        assert errors == []


# ===========================================================================
# CodeContext defaults
# ===========================================================================


class TestCodeContext:
    """Tests for CodeContext default values and fields."""

    def test_defaults(self):
        """All collection fields default to empty, Optional fields to None."""
        ctx = CodeContext()
        assert ctx.files == []
        assert ctx.model_classes == []
        assert ctx.features == []
        assert ctx.target_variable is None
        assert ctx.transformations == []
        assert ctx.ml_task_type is None
        assert ctx.hyperparameters == {}
        assert ctx.data_sources == []
        assert ctx.insights == ""
        assert ctx.readme is None
        assert ctx.code_evidence == []
        assert ctx.language == "python"

    def test_skipped_files_default(self):
        """skipped_files defaults to empty list."""
        ctx = CodeContext()
        assert ctx.skipped_files == []

    def test_scan_incomplete_default(self):
        """scan_incomplete defaults to False."""
        ctx = CodeContext()
        assert ctx.scan_incomplete is False

    def test_skipped_files_populated(self):
        """skipped_files can be set with a list of paths."""
        ctx = CodeContext(skipped_files=["big_file.py", "generated.py"])
        assert len(ctx.skipped_files) == 2
        assert "big_file.py" in ctx.skipped_files

    def test_scan_incomplete_set(self):
        """scan_incomplete can be set to True."""
        ctx = CodeContext(scan_incomplete=True)
        assert ctx.scan_incomplete is True


# ===========================================================================
# CodeEvidence defaults
# ===========================================================================


class TestCodeEvidence:
    """Tests for CodeEvidence optional fields."""

    def test_required_fields(self):
        """Required fields must be provided."""
        ev = CodeEvidence(
            path="train.py", symbol="train_model",
            statement="Trains an XGBoost classifier",
            snippet="model = XGBClassifier()",
        )
        assert ev.path == "train.py"
        assert ev.symbol == "train_model"

    def test_optional_fields_default_none(self):
        """start_line and end_line default to None."""
        ev = CodeEvidence(
            path="x.py", symbol="f", statement="s", snippet="code"
        )
        assert ev.start_line is None
        assert ev.end_line is None

    def test_optional_fields_set(self):
        """start_line and end_line can be explicitly set."""
        ev = CodeEvidence(
            path="x.py", symbol="f", statement="s", snippet="code",
            start_line=10, end_line=25,
        )
        assert ev.start_line == 10
        assert ev.end_line == 25
