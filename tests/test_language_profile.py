"""Tests for LanguageProfile, detection, and registry."""

import tempfile
from pathlib import Path

import pytest

from autodoc.core.models import (
    LANGUAGE_PROFILES,
    LANGUAGE_PRIORITY,
    MATLAB_PROFILE,
    PYTHON_PROFILE,
    R_PROFILE,
    SAS_PROFILE,
    LanguageProfile,
    detect_language,
    get_language_profile,
)


class TestLanguageProfileConstruction:
    def test_python_profile_has_expected_fields(self):
        p = PYTHON_PROFILE
        assert p.name == "python"
        assert p.display_name == "Python"
        assert "*.py" in p.file_extensions
        assert p.code_fence_lang == "python"
        assert "train" in p.priority_keywords

    def test_r_profile_has_expected_fields(self):
        p = R_PROFILE
        assert p.name == "r"
        assert "*.R" in p.file_extensions
        assert "*.Rmd" in p.file_extensions
        assert p.code_fence_lang == "r"
        assert ".Renviron" in p.secret_patterns

    def test_sas_profile_has_expected_fields(self):
        p = SAS_PROFILE
        assert p.name == "sas"
        assert "*.sas" in p.file_extensions
        assert p.code_fence_lang == "sas"
        assert len(p.secret_patterns) > 0

    def test_matlab_profile_has_expected_fields(self):
        p = MATLAB_PROFILE
        assert p.name == "matlab"
        assert "*.m" in p.file_extensions
        assert p.code_fence_lang == "matlab"
        assert len(p.secret_patterns) == 0

    def test_all_four_profiles_in_registry(self):
        assert len(LANGUAGE_PROFILES) == 4
        assert set(LANGUAGE_PROFILES.keys()) == {"python", "r", "sas", "matlab"}


class TestGetLanguageProfile:
    def test_get_python(self):
        assert get_language_profile("python") is PYTHON_PROFILE

    def test_get_r_case_insensitive(self):
        assert get_language_profile("R") is R_PROFILE

    def test_get_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown language"):
            get_language_profile("julia")


class TestDetectLanguage:
    def test_detect_python_project(self, tmp_path):
        (tmp_path / "train.py").write_text("import sklearn")
        (tmp_path / "model.py").write_text("class Model: pass")
        profile, count = detect_language(tmp_path)
        assert profile is PYTHON_PROFILE
        assert count == 2

    def test_detect_r_project(self, tmp_path):
        (tmp_path / "analysis.R").write_text("library(caret)")
        (tmp_path / "model.R").write_text("fit <- train()")
        (tmp_path / "report.Rmd").write_text("---\ntitle: report\n---")
        profile, count = detect_language(tmp_path)
        assert profile is R_PROFILE
        assert count == 3

    def test_detect_sas_project(self, tmp_path):
        (tmp_path / "model.sas").write_text("PROC LOGISTIC;")
        profile, count = detect_language(tmp_path)
        assert profile is SAS_PROFILE
        assert count == 1

    def test_detect_matlab_project(self, tmp_path):
        (tmp_path / "train_model.m").write_text("mdl = fitctree(X, Y);")
        (tmp_path / "predict.m").write_text("pred = predict(mdl, Xtest);")
        profile, count = detect_language(tmp_path)
        assert profile is MATLAB_PROFILE
        assert count == 2

    def test_tiebreak_python_wins_over_r(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1")
        (tmp_path / "b.R").write_text("x <- 1")
        profile, count = detect_language(tmp_path)
        assert profile is PYTHON_PROFILE

    def test_empty_directory(self, tmp_path):
        profile, count = detect_language(tmp_path)
        assert profile is None
        assert count == 0

    def test_no_supported_files(self, tmp_path):
        (tmp_path / "main.scala").write_text("object Main")
        (tmp_path / "app.jl").write_text("println()")
        profile, count = detect_language(tmp_path)
        assert profile is None
        assert count == 0

    def test_nonexistent_directory(self, tmp_path):
        profile, count = detect_language(tmp_path / "does_not_exist")
        assert profile is None
        assert count == 0
