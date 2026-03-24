"""Tests for multi-language code scanner."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from autodoc.core.models import PYTHON_PROFILE, R_PROFILE, SAS_PROFILE, MATLAB_PROFILE
from autodoc.scanning.code_scanner import CodeScanner
from autodoc.scanning.sanitizer import ContentSanitizer


@pytest.fixture
def mock_llm():
    llm = MagicMock()
    llm.complete_json = AsyncMock(return_value={
        "model_classes": ["RandomForest"],
        "features": ["x1", "x2"],
        "target_variable": "y",
        "transformations": [],
        "ml_task_type": "classification",
        "hyperparameters": {},
        "data_sources": [],
        "insights": "Test insights",
        "code_evidence": [
            {
                "statement": "Model trained",
                "file": "train.py",
                "symbol": "train_model",
                "snippet": "model.fit(X, y)",
                "start_line": 10,
                "end_line": 15,
            }
        ],
    })
    return llm


@pytest.fixture
def sanitizer():
    return ContentSanitizer()


class TestFindSourceFiles:
    def test_finds_python_files(self, tmp_path, mock_llm, sanitizer):
        (tmp_path / "train.py").write_text("import sklearn")
        (tmp_path / "test_train.py").write_text("# test")
        scanner = CodeScanner(mock_llm, sanitizer, tmp_path, profile=PYTHON_PROFILE)
        files = scanner._find_source_files()
        names = [f.name for f in files]
        assert "train.py" in names
        assert "test_train.py" not in names  # excluded

    def test_finds_r_files(self, tmp_path, mock_llm, sanitizer):
        (tmp_path / "analysis.R").write_text("library(caret)")
        (tmp_path / "report.Rmd").write_text("---\ntitle: r\n---")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test-model.R").write_text("test_that()")
        scanner = CodeScanner(mock_llm, sanitizer, tmp_path, profile=R_PROFILE)
        files = scanner._find_source_files()
        names = [f.name for f in files]
        assert "analysis.R" in names
        assert "report.Rmd" in names

    def test_finds_sas_files(self, tmp_path, mock_llm, sanitizer):
        (tmp_path / "model.sas").write_text("PROC LOGISTIC;")
        (tmp_path / "autoexec.sas").write_text("OPTIONS;")
        scanner = CodeScanner(mock_llm, sanitizer, tmp_path, profile=SAS_PROFILE)
        files = scanner._find_source_files()
        names = [f.name for f in files]
        assert "model.sas" in names
        assert "autoexec.sas" not in names  # excluded

    def test_finds_matlab_files(self, tmp_path, mock_llm, sanitizer):
        (tmp_path / "train_model.m").write_text("mdl = fitctree(X, Y);")
        scanner = CodeScanner(mock_llm, sanitizer, tmp_path, profile=MATLAB_PROFILE)
        files = scanner._find_source_files()
        assert len(files) == 1
        assert files[0].name == "train_model.m"

    def test_priority_sorting_r(self, tmp_path, mock_llm, sanitizer):
        (tmp_path / "utils.R").write_text("helper()")
        (tmp_path / "train_model.R").write_text("fit()")
        scanner = CodeScanner(mock_llm, sanitizer, tmp_path, profile=R_PROFILE)
        files = scanner._find_source_files()
        assert files[0].name == "train_model.R"

    def test_empty_directory(self, tmp_path, mock_llm, sanitizer):
        scanner = CodeScanner(mock_llm, sanitizer, tmp_path, profile=R_PROFILE)
        files = scanner._find_source_files()
        assert files == []

    def test_default_profile_is_python(self, tmp_path, mock_llm, sanitizer):
        scanner = CodeScanner(mock_llm, sanitizer, tmp_path)
        assert scanner.profile is PYTHON_PROFILE


class TestLineNumberAnnotation:
    def test_read_files_adds_line_numbers(self, tmp_path, mock_llm, sanitizer):
        code = "line_one\nline_two\nline_three"
        (tmp_path / "script.py").write_text(code)
        scanner = CodeScanner(mock_llm, sanitizer, tmp_path, profile=PYTHON_PROFILE)
        files = scanner._find_source_files()
        contents = scanner._read_files(files)
        assert len(contents) == 1
        lines = contents[0]["content"].split("\n")
        assert lines[0] == "1: line_one"
        assert lines[1] == "2: line_two"
        assert lines[2] == "3: line_three"


class TestScanSetsLanguage:
    @pytest.mark.asyncio
    async def test_scan_sets_language_field(self, tmp_path, mock_llm, sanitizer):
        (tmp_path / "model.R").write_text("library(caret)\nmodel <- train()")
        scanner = CodeScanner(mock_llm, sanitizer, tmp_path, profile=R_PROFILE)
        ctx = await scanner.scan()
        assert ctx.language == "r"

    @pytest.mark.asyncio
    async def test_scan_empty_sets_language(self, tmp_path, mock_llm, sanitizer):
        scanner = CodeScanner(mock_llm, sanitizer, tmp_path, profile=R_PROFILE)
        ctx = await scanner.scan()
        assert ctx.language == "r"
        assert "No R files found" in ctx.insights

    @pytest.mark.asyncio
    async def test_scan_evidence_has_line_numbers(self, tmp_path, mock_llm, sanitizer):
        (tmp_path / "train.py").write_text("import sklearn\nmodel.fit(X, y)")
        scanner = CodeScanner(mock_llm, sanitizer, tmp_path, profile=PYTHON_PROFILE)
        ctx = await scanner.scan()
        assert len(ctx.code_evidence) == 1
        assert ctx.code_evidence[0].start_line == 10
        assert ctx.code_evidence[0].end_line == 15
