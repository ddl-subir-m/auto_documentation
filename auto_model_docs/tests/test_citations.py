"""Tests for autodoc/generation/citations.py — citation utilities."""

import pytest

from autodoc.generation.citations import (
    CitationEntry,
    CitationRegistry,
    _sanitize_name,
    build_code_citation_id,
    build_mlflow_artifact_citation_id,
    build_mlflow_run_citation_id,
    extract_citation_ids,
    parse_citation_id,
)


# ---------------------------------------------------------------------------
# _sanitize_name()
# ---------------------------------------------------------------------------


class TestSanitizeName:
    """_sanitize_name replaces non-alphanumeric chars and collapses underscores."""

    def test_spaces_become_underscores(self):
        assert _sanitize_name("my experiment") == "my_experiment"

    def test_special_chars_replaced(self):
        assert _sanitize_name("run@#$%v1") == "run_v1"

    def test_multiple_underscores_collapsed(self):
        assert _sanitize_name("a   b") == "a_b"

    def test_leading_trailing_underscores_stripped(self):
        assert _sanitize_name("  name  ") == "name"

    def test_empty_returns_unknown(self):
        assert _sanitize_name("") == "Unknown"

    def test_all_special_chars_returns_unknown(self):
        assert _sanitize_name("@#$%") == "Unknown"

    def test_unicode_preserved(self):
        # Word chars in regex include unicode letters
        result = _sanitize_name("modele_accentue")
        assert result == "modele_accentue"

    def test_hyphens_preserved(self):
        assert _sanitize_name("my-run-name") == "my-run-name"

    def test_mixed_special_and_normal(self):
        assert _sanitize_name("exp (copy)") == "exp_copy"


# ---------------------------------------------------------------------------
# build_code_citation_id()
# ---------------------------------------------------------------------------


class TestBuildCodeCitationId:
    """Citation IDs for code references follow the Code:path#symbol:Lstart-Lend format."""

    def test_path_only(self):
        result = build_code_citation_id("train.py")
        assert result == "Code:train.py"

    def test_path_and_symbol(self):
        result = build_code_citation_id("model.py", symbol="train_model")
        assert result == "Code:model.py#train_model"

    def test_path_symbol_and_lines(self):
        result = build_code_citation_id("script.R", symbol="fit", start_line=10, end_line=50)
        assert result == "Code:script.R#fit:L10-L50"

    def test_path_and_start_line_only(self):
        result = build_code_citation_id("utils.py", start_line=42)
        assert result == "Code:utils.py:L42"

    def test_nested_path(self):
        result = build_code_citation_id("src/models/classifier.py", symbol="predict")
        assert result == "Code:src/models/classifier.py#predict"

    def test_special_chars_in_path_are_kept(self):
        # Path characters are not sanitized — they are literal
        result = build_code_citation_id("my-project/model_v2.py")
        assert result == "Code:my-project/model_v2.py"


# ---------------------------------------------------------------------------
# build_mlflow_run_citation_id()
# ---------------------------------------------------------------------------


class TestBuildMlflowRunCitationId:
    """Run-level citation IDs: {sanitized_experiment}-{sanitized_run}."""

    def test_basic_format(self):
        result = build_mlflow_run_citation_id("MyExperiment", "training-run-1")
        assert result == "MyExperiment-training-run-1"

    def test_experiment_with_spaces(self):
        result = build_mlflow_run_citation_id("Credit Risk Exp", "run 1")
        assert result == "Credit_Risk_Exp-run_1"

    def test_no_experiment_name(self):
        result = build_mlflow_run_citation_id(None, "run_a")
        assert result == "Experiment-run_a"

    def test_no_run_name_uses_run_id(self):
        result = build_mlflow_run_citation_id("exp1", None, run_id="abc12345def")
        assert result == "exp1-abc12345"

    def test_no_run_name_no_run_id(self):
        result = build_mlflow_run_citation_id("exp1", None, run_id=None)
        assert result == "exp1-Run"


# ---------------------------------------------------------------------------
# build_mlflow_artifact_citation_id()
# ---------------------------------------------------------------------------


class TestBuildMlflowArtifactCitationId:
    """Artifact citation IDs: {experiment}-{run}:{filename}."""

    def test_basic_artifact(self):
        result = build_mlflow_artifact_citation_id("Exp1", "Run1", "model.pkl")
        assert result == "Exp1-Run1:model.pkl"

    def test_nested_artifact_path_uses_basename(self):
        result = build_mlflow_artifact_citation_id(
            "Exp1", "Run1", "artifacts/models/model.pkl"
        )
        assert result == "Exp1-Run1:model.pkl"

    def test_spaces_sanitized(self):
        result = build_mlflow_artifact_citation_id(
            "My Experiment", "My Run", "feature_importance.png"
        )
        assert result == "My_Experiment-My_Run:feature_importance.png"

    def test_no_experiment_or_run(self):
        result = build_mlflow_artifact_citation_id(None, None, "data.csv")
        assert result == "Experiment-Run:data.csv"

    def test_run_id_fallback(self):
        result = build_mlflow_artifact_citation_id("E", None, "file.txt", run_id="abcdef12")
        assert result == "E-abcdef12:file.txt"


# ---------------------------------------------------------------------------
# parse_citation_id()
# ---------------------------------------------------------------------------


class TestParseCitationId:
    """parse_citation_id dispatches to the correct parser for each format."""

    # --- Code citations ---

    def test_code_citation_path_only(self):
        result = parse_citation_id("Code:train.py")
        assert result["type"] == "code_file"
        assert result["code_path"] == "train.py"
        assert result["code_symbol"] == ""

    def test_code_citation_with_symbol(self):
        result = parse_citation_id("Code:model.py#fit")
        assert result["type"] == "code_file"
        assert result["code_path"] == "model.py"
        assert result["code_symbol"] == "fit"

    def test_code_citation_with_lines(self):
        result = parse_citation_id("Code:script.R#func:L10-L50")
        assert result["type"] == "code_file"
        assert result["code_path"] == "script.R"
        assert result["code_symbol"] == "func"
        assert result["start_line"] == 10
        assert result["end_line"] == 50

    def test_code_citation_start_line_only(self):
        result = parse_citation_id("Code:utils.py:L42")
        assert result["type"] == "code_file"
        assert result["code_path"] == "utils.py"
        assert result["start_line"] == 42
        assert "end_line" not in result

    def test_lowercase_code_prefix(self):
        result = parse_citation_id("code:legacy.py#old_func")
        assert result["type"] == "code_file"
        assert result["code_path"] == "legacy.py"

    # --- MLflow legacy citations ---

    def test_legacy_mlflow_metric(self):
        result = parse_citation_id("mlflow:run/abc123/metric/accuracy")
        assert result["type"] == "mlflow_metric"
        assert result["run_id"] == "abc123"
        assert result["source_key"] == "accuracy"

    def test_legacy_mlflow_artifact(self):
        result = parse_citation_id("mlflow:run/abc123/artifact/model.pkl")
        assert result["type"] == "mlflow_artifact"
        assert result["artifact_path"] == "model.pkl"

    # --- New format: run-level ---

    def test_new_format_run_level(self):
        result = parse_citation_id("MyExp-run1")
        assert result["type"] == "mlflow_run"
        assert result["experiment_name"] == "MyExp"
        assert result["run_name"] == "run1"

    # --- New format: artifact ---

    def test_new_format_artifact(self):
        result = parse_citation_id("MyExp-run1:model.pkl")
        assert result["type"] == "mlflow_artifact"
        assert result["experiment_name"] == "MyExp"
        assert result["run_name"] == "run1"
        assert result["artifact_path"] == "model.pkl"

    # --- Unknown ---

    def test_unknown_format(self):
        result = parse_citation_id("some_random_string_no_hyphen")
        assert result["type"] == "unknown"


# ---------------------------------------------------------------------------
# extract_citation_ids()
# ---------------------------------------------------------------------------


class TestExtractCitationIds:
    """extract_citation_ids finds all [@...] markers in text."""

    def test_single_marker(self):
        text = "As shown in [@Code:train.py#fit] the model..."
        result = extract_citation_ids(text)
        assert result == ["Code:train.py#fit"]

    def test_multiple_markers(self):
        text = "See [@Code:a.py] and [@Code:b.py] and [@Exp-run1]."
        result = extract_citation_ids(text)
        assert result == ["Code:a.py", "Code:b.py", "Exp-run1"]

    def test_no_markers(self):
        result = extract_citation_ids("This text has no citations.")
        assert result == []

    def test_empty_string(self):
        assert extract_citation_ids("") == []

    def test_none_input(self):
        assert extract_citation_ids(None) == []

    def test_combined_semicolon_markers(self):
        """Handles malformed combined citations like [@id1; @id2]."""
        text = "Results shown in [@Code:a.py; @Code:b.py]."
        result = extract_citation_ids(text)
        assert "Code:a.py" in result
        assert "Code:b.py" in result

    def test_combined_comma_markers(self):
        text = "See [@Code:x.py, @Code:y.py]."
        result = extract_citation_ids(text)
        assert "Code:x.py" in result
        assert "Code:y.py" in result


# ---------------------------------------------------------------------------
# CitationRegistry
# ---------------------------------------------------------------------------


class TestCitationRegistry:
    """CitationRegistry tracks ordering and numbering of citations."""

    def test_register_returns_citation_id(self):
        reg = CitationRegistry()
        display_id = reg.register("Code:train.py")
        assert display_id == "Code:train.py"

    def test_get_number_returns_1_based_index(self):
        reg = CitationRegistry()
        reg.register("Code:a.py")
        reg.register("Code:b.py")
        assert reg.get_number("Code:a.py") == 1
        assert reg.get_number("Code:b.py") == 2

    def test_get_number_unknown_returns_none(self):
        reg = CitationRegistry()
        assert reg.get_number("Code:nonexistent.py") is None

    def test_duplicate_register_preserves_order(self):
        reg = CitationRegistry()
        reg.register("Code:a.py")
        reg.register("Code:b.py")
        reg.register("Code:a.py")  # duplicate
        assert reg.get_number("Code:a.py") == 1
        assert reg.get_number("Code:b.py") == 2

    def test_list_entries_ordering(self):
        reg = CitationRegistry()
        reg.register("Code:first.py")
        reg.register("Exp-run1")
        reg.register("Code:second.py")

        entries = reg.list_entries()
        assert len(entries) == 3
        assert entries[0][0] == "Code:first.py"
        assert entries[1][0] == "Exp-run1"
        assert entries[2][0] == "Code:second.py"

    def test_list_entries_returns_citation_entry_objects(self):
        reg = CitationRegistry()
        reg.register("Code:train.py", {"type": "code_file", "code_path": "train.py"})
        entries = reg.list_entries()
        cid, entry = entries[0]
        assert isinstance(entry, CitationEntry)
        assert entry.type == "code_file"
        assert entry.code_path == "train.py"

    def test_run_level_consolidation(self):
        """Multiple metrics from same run_id consolidate to one citation."""
        reg = CitationRegistry()
        reg.register("Exp-run1", {"type": "mlflow_metric", "run_id": "abc123"})
        display_id = reg.register("Exp-run1-metric2", {"type": "mlflow_metric", "run_id": "abc123"})

        # Second registration for the same run_id should return the first citation id
        assert display_id == "Exp-run1"

    def test_get_display_id_registered(self):
        reg = CitationRegistry()
        reg.register("Code:x.py")
        assert reg.get_display_id("Code:x.py") == "Code:x.py"

    def test_get_display_id_unregistered(self):
        reg = CitationRegistry()
        assert reg.get_display_id("Code:x.py") is None

    def test_entry_text_for_code_file(self):
        reg = CitationRegistry()
        reg.register(
            "Code:model.py#train",
            {"type": "code_file", "code_path": "model.py", "code_symbol": "train"},
        )
        entries = reg.list_entries()
        _, entry = entries[0]
        assert "Source Code" in entry.text
        assert "model.py" in entry.text
        assert "train" in entry.text

    def test_entry_text_for_mlflow_artifact(self):
        reg = CitationRegistry()
        reg.register(
            "Exp-Run:data.csv",
            {
                "type": "mlflow_artifact",
                "experiment_name": "Exp",
                "run_name": "Run",
                "artifact_path": "data.csv",
            },
        )
        entries = reg.list_entries()
        _, entry = entries[0]
        assert "Artifact" in entry.text
        assert "data.csv" in entry.text

    def test_entry_text_for_mlflow_run(self):
        reg = CitationRegistry()
        reg.register(
            "Exp-RunA",
            {
                "type": "mlflow_run",
                "experiment_name": "Exp",
                "run_name": "RunA",
            },
        )
        entries = reg.list_entries()
        _, entry = entries[0]
        assert "Model Run" in entry.text
        assert "Exp" in entry.text

    def test_tracking_uri_generates_run_url(self):
        reg = CitationRegistry(tracking_uri="http://mlflow:5000")
        reg.register(
            "legacy",
            {
                "type": "mlflow_metric",
                "run_id": "r123",
                "experiment_id": "e456",
            },
        )
        entries = reg.list_entries()
        _, entry = entries[0]
        assert "http://mlflow:5000" in entry.run_url
        assert "r123" in entry.run_url
