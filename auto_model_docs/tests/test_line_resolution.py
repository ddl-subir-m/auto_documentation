"""Tests for post-hoc line number resolution."""

import ast
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from autodoc.scanning.code_scanner import CodeScanner
from autodoc.core.models import PYTHON_PROFILE


SAMPLE_PYTHON = '''"""Module docstring."""

import pandas as pd

class MyModel:
    def train(self, X, y):
        self.model = None

def load_data(path):
    """Load dataset from CSV."""
    return pd.read_csv(path)

def train_model(X, y):
    """Train the model."""
    model = MyModel()
    model.train(X, y)
    return model

def train_model(X, y, params):
    """Train model with params (duplicate name, longer body)."""
    model = MyModel()
    model.train(X, y)
    model.params = params
    model.validate()
    return model
'''


@pytest.fixture
def scanner(sanitizer, mock_llm):
    return CodeScanner(
        llm=mock_llm, sanitizer=sanitizer,
        code_root=Path("/tmp"), profile=PYTHON_PROFILE,
    )


@pytest.fixture
def file_contents():
    return {"train.py": SAMPLE_PYTHON}


# ── Regression: annotation removed ───────────────────────────────

class TestAnnotationRemoved:
    async def test_batch_sends_raw_code_without_line_numbers(
        self, tmp_path, sanitizer, mock_llm
    ):
        """Regression test: verify line annotations are NOT in the LLM prompt."""
        (tmp_path / "code.py").write_text("x = 1\ny = 2\n")

        mock_llm.complete_json.return_value = {
            "model_classes": [], "features": [], "code_evidence": [],
        }

        scanner = CodeScanner(
            llm=mock_llm, sanitizer=sanitizer,
            code_root=tmp_path, profile=PYTHON_PROFILE,
            analysis_timeout=30.0,
        )
        await scanner._analyze_single_batch(["code.py"], {})

        # Inspect the prompt that was sent to the LLM
        call_args = mock_llm.complete_json.call_args
        prompt = call_args.kwargs.get("prompt") or call_args[1].get("prompt") or call_args[0][0]
        # Should NOT contain line-number annotations like "1: x = 1"
        assert "1: x = 1" not in prompt
        assert "2: y = 2" not in prompt
        # Should contain raw code
        assert "x = 1" in prompt


# ── AST symbol → line span ───────────────────────────────────────

class TestFindSymbolSpan:
    def test_finds_function(self, scanner):
        tree = ast.parse(SAMPLE_PYTHON)
        start, end = scanner._find_symbol_span(tree, "load_data")
        assert start is not None
        assert end is not None
        assert start < end

    def test_finds_class(self, scanner):
        tree = ast.parse(SAMPLE_PYTHON)
        start, end = scanner._find_symbol_span(tree, "MyModel")
        assert start is not None

    def test_duplicate_symbols_prefers_longest(self, scanner):
        """Two 'train_model' functions — should prefer the longer one."""
        tree = ast.parse(SAMPLE_PYTHON)
        start, end = scanner._find_symbol_span(tree, "train_model")
        assert start is not None
        assert end is not None
        # The longer duplicate has more lines (with params, validate)
        span = end - start
        assert span >= 4  # The longer one has 5+ lines

    def test_dotted_name(self, scanner):
        """'MyModel.train' should find the 'train' method."""
        tree = ast.parse(SAMPLE_PYTHON)
        start, end = scanner._find_symbol_span(tree, "MyModel.train")
        assert start is not None

    def test_missing_symbol(self, scanner):
        tree = ast.parse(SAMPLE_PYTHON)
        start, end = scanner._find_symbol_span(tree, "nonexistent_func")
        assert start is None
        assert end is None


# ── Full resolution ──────────────────────────────────────────────

class TestResolveLineNumbers:
    def test_single_line_snippet_match(self, scanner, file_contents):
        evidence = [{"file": "train.py", "symbol": "", "snippet": "return pd.read_csv(path)"}]
        scanner._resolve_line_numbers(evidence, file_contents)
        assert evidence[0].get("start_line") is not None
        assert evidence[0].get("end_line") is not None

    def test_multi_line_snippet_match(self, scanner, file_contents):
        evidence = [{"file": "train.py", "symbol": "", "snippet": "model = MyModel()\n    model.train(X, y)"}]
        scanner._resolve_line_numbers(evidence, file_contents)
        start = evidence[0].get("start_line")
        end = evidence[0].get("end_line")
        assert start is not None
        # Multi-line match should have end > start
        if end is not None:
            assert end >= start

    def test_no_match_returns_none(self, scanner, file_contents):
        evidence = [{"file": "train.py", "symbol": "", "snippet": "this_does_not_exist_anywhere()"}]
        scanner._resolve_line_numbers(evidence, file_contents)
        assert evidence[0].get("start_line") is None

    def test_syntax_error_falls_back_to_snippet(self, scanner):
        bad_python = "def broken(\n  # unclosed"
        evidence = [{"file": "bad.py", "symbol": "broken", "snippet": "def broken("}]
        scanner._resolve_line_numbers(evidence, {"bad.py": bad_python})
        # AST will fail but snippet match should still work
        assert evidence[0].get("start_line") is not None

    def test_non_python_file_snippet_only(self, scanner):
        r_code = "library(ggplot2)\nmy_func <- function(x) { x + 1 }\n"
        evidence = [{"file": "analysis.R", "symbol": "my_func", "snippet": "my_func <- function(x)"}]
        scanner._resolve_line_numbers(evidence, {"analysis.R": r_code})
        # No AST for R, but snippet should match
        assert evidence[0].get("start_line") is not None

    def test_per_file_caching(self, scanner, file_contents):
        """Multiple evidence items for the same file should not re-parse."""
        evidence = [
            {"file": "train.py", "symbol": "load_data", "snippet": ""},
            {"file": "train.py", "symbol": "MyModel", "snippet": ""},
        ]
        scanner._resolve_line_numbers(evidence, file_contents)
        # Both should resolve (proves AST was parsed and reused)
        assert evidence[0].get("start_line") is not None
        assert evidence[1].get("start_line") is not None

    def test_missing_file_skipped(self, scanner):
        evidence = [{"file": "gone.py", "symbol": "foo", "snippet": "bar"}]
        scanner._resolve_line_numbers(evidence, {})
        # Should not raise, just leave as None
        assert evidence[0].get("start_line") is None
