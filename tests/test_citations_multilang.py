"""Tests for line-number citation IDs and parsing."""

import pytest

from autodoc.generation.citations import build_code_citation_id, parse_citation_id


class TestBuildCodeCitationIdWithLineNumbers:
    def test_with_line_numbers(self):
        cid = build_code_citation_id("train.R", "fit_model", start_line=42, end_line=58)
        assert cid == "Code:train.R#fit_model:L42-L58"

    def test_with_start_line_only(self):
        cid = build_code_citation_id("train.R", "fit_model", start_line=42)
        assert cid == "Code:train.R#fit_model:L42"

    def test_without_line_numbers(self):
        cid = build_code_citation_id("train.R", "fit_model")
        assert cid == "Code:train.R#fit_model"

    def test_without_symbol_with_lines(self):
        cid = build_code_citation_id("train.R", start_line=10, end_line=20)
        assert cid == "Code:train.R:L10-L20"

    def test_backward_compat_no_args(self):
        cid = build_code_citation_id("train.py", "Model")
        assert cid == "Code:train.py#Model"


class TestParseCitationIdWithLineNumbers:
    def test_parse_with_line_range(self):
        result = parse_citation_id("Code:train.R#fit_model:L42-L58")
        assert result["type"] == "code_file"
        assert result["code_path"] == "train.R"
        assert result["code_symbol"] == "fit_model"
        assert result["start_line"] == 42
        assert result["end_line"] == 58

    def test_parse_with_start_line_only(self):
        result = parse_citation_id("Code:train.R#func:L42")
        assert result["type"] == "code_file"
        assert result["code_path"] == "train.R"
        assert result["code_symbol"] == "func"
        assert result["start_line"] == 42
        assert "end_line" not in result

    def test_parse_without_line_numbers(self):
        result = parse_citation_id("Code:train.py#Model")
        assert result["type"] == "code_file"
        assert result["code_path"] == "train.py"
        assert result["code_symbol"] == "Model"
        assert "start_line" not in result

    def test_parse_legacy_code_format(self):
        result = parse_citation_id("code:train.py#func")
        assert result["type"] == "code_file"
        assert result["code_path"] == "train.py"

    def test_parse_path_without_symbol_with_lines(self):
        result = parse_citation_id("Code:script.sas:L5-L20")
        assert result["type"] == "code_file"
        assert result["code_path"] == "script.sas"
        assert result["code_symbol"] == ""
        assert result["start_line"] == 5
        assert result["end_line"] == 20

    def test_parse_mlflow_run_unchanged(self):
        result = parse_citation_id("Experiment-RunName")
        assert result["type"] == "mlflow_run"

    def test_parse_mlflow_legacy_unchanged(self):
        result = parse_citation_id("mlflow:run/abc123/metric/accuracy")
        assert result["type"] == "mlflow_metric"
