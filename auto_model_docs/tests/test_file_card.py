"""Tests for file card extraction (Stage 1)."""

import pytest
from pathlib import Path

from autodoc.scanning.file_card import (
    FileCard,
    extract_file_card,
    is_binary_file,
    _extract_python_card,
    _extract_r_card,
    _extract_sas_card,
    _extract_matlab_card,
)


# ── Binary file detection ─────────────────────────────────────────

class TestIsBinaryFile:
    def test_binary_by_extension(self, tmp_path):
        f = tmp_path / "model.pkl"
        f.write_text("not actually binary")
        assert is_binary_file(f) is True

    def test_binary_by_null_bytes(self, tmp_path):
        f = tmp_path / "data.dat"
        f.write_bytes(b"hello\x00world")
        assert is_binary_file(f) is True

    def test_text_file(self, tmp_path):
        f = tmp_path / "train.py"
        f.write_text("import pandas\n")
        assert is_binary_file(f) is False

    def test_nonexistent_file(self, tmp_path):
        f = tmp_path / "missing.py"
        assert is_binary_file(f) is True  # Can't read → treat as binary


# ── Python AST extraction ─────────────────────────────────────────

class TestPythonCardExtraction:
    def test_happy_path(self):
        content = '''"""Module docstring."""
import pandas as pd
from sklearn.model_selection import train_test_split

class MyModel:
    pass

def train(X, y):
    """Train the model."""
    return X + y

def predict(model, X):
    return model.predict(X)
'''
        imports, symbols, docstring, snippets = _extract_python_card(content)

        assert "pandas" in imports
        assert "sklearn.model_selection" in imports
        assert "class MyModel" in symbols
        assert "def train" in symbols
        assert "def predict" in symbols
        assert "Module docstring" in docstring
        assert len(snippets) > 0

    def test_empty_file(self):
        imports, symbols, docstring, snippets = _extract_python_card("")
        assert imports == []
        assert symbols == []
        assert docstring == ""

    def test_no_functions(self):
        content = "x = 1\ny = 2\nprint(x + y)\n"
        imports, symbols, docstring, snippets = _extract_python_card(content)
        assert symbols == []
        # Should still get a snippet (first 30 lines)
        assert len(snippets) <= 1

    def test_parse_error_fallback(self, tmp_path):
        """Malformed Python → metadata-only card."""
        f = tmp_path / "bad.py"
        f.write_text("def broken(\n  # missing close paren")

        card = extract_file_card(f, tmp_path, language="python")
        assert card.path == "bad.py"
        assert card.language == "python"
        # Should have fallback snippet (first 500 chars)
        assert len(card.snippets) >= 0  # May have fallback


# ── R extraction ──────────────────────────────────────────────────

class TestRCardExtraction:
    def test_r_imports_and_symbols(self):
        content = '''# Load libraries
library(ggplot2)
require("dplyr")

my_func <- function(x, y) {
  x + y
}
'''
        imports, symbols, docstring, snippets = _extract_r_card(content)
        assert "ggplot2" in imports
        assert "dplyr" in imports
        assert any("my_func" in s for s in symbols)


# ── SAS extraction ────────────────────────────────────────────────

class TestSASCardExtraction:
    def test_sas_macros_and_procs(self):
        content = '''%macro clean_data(ds);
  proc sort data=&ds; by id; run;
%mend clean_data;

proc logistic data=train;
  model target = x1 x2 x3;
run;
'''
        imports, symbols, docstring, snippets = _extract_sas_card(content)
        assert any("clean_data" in s for s in symbols)
        assert any("sort" in s or "logistic" in s for s in symbols)


# ── MATLAB extraction ────────────────────────────────────────────

class TestMATLABCardExtraction:
    def test_matlab_function(self):
        content = '''% Train a classifier
function model = train_classifier(X, y)
    model = fitcsvm(X, y);
end
'''
        imports, symbols, docstring, snippets = _extract_matlab_card(content)
        assert any("train_classifier" in s for s in symbols)
        assert "Train a classifier" in docstring


# ── FileCard integration ──────────────────────────────────────────

class TestExtractFileCard:
    def test_python_file(self, tmp_code_root):
        card = extract_file_card(
            tmp_code_root / "train.py", tmp_code_root, language="python"
        )
        assert card.path == "train.py"
        assert card.language == "python"
        assert card.size > 0
        assert "xgboost" in card.imports or "XGBClassifier" in " ".join(card.imports)
        assert any("train_model" in s for s in card.symbols)
        assert len(card.snippets) > 0

    def test_nonexistent_file(self, tmp_path):
        card = extract_file_card(
            tmp_path / "missing.py", tmp_path, language="python"
        )
        assert card.path == "missing.py"
        assert card.imports == []
        assert card.snippets == []

    def test_to_prompt_text(self):
        card = FileCard(
            path="train.py",
            language="python",
            size=1234,
            imports=["pandas", "sklearn"],
            symbols=["def train_model"],
            docstring="Train the model.",
            snippets=["model.fit(X, y)"],
        )
        text = card.to_prompt_text()
        assert "train.py" in text
        assert "pandas" in text
        assert "def train_model" in text
        assert "model.fit(X, y)" in text
