"""Tests for the two-pass code scanner pipeline (Stages 0-4)."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from autodoc.scanning.code_scanner import CodeScanner
from autodoc.core.models import PYTHON_PROFILE


# ── Stage 0: File discovery & hard filtering ──────────────────────

class TestStage0FindSourceFiles:
    def test_excludes_binary_files(self, tmp_code_root, sanitizer, mock_llm):
        scanner = CodeScanner(
            llm=mock_llm, sanitizer=sanitizer,
            code_root=tmp_code_root, profile=PYTHON_PROFILE,
        )
        files = scanner._find_source_files()
        paths = [str(f.relative_to(tmp_code_root)) for f in files]
        assert not any(p.endswith(".pkl") for p in paths)

    def test_excludes_test_directory(self, tmp_code_root, sanitizer, mock_llm):
        scanner = CodeScanner(
            llm=mock_llm, sanitizer=sanitizer,
            code_root=tmp_code_root, profile=PYTHON_PROFILE,
        )
        files = scanner._find_source_files()
        paths = [str(f.relative_to(tmp_code_root)) for f in files]
        assert not any("tests/" in p or "test_" in p for p in paths)

    def test_respects_max_files_cap(self, tmp_path, sanitizer, mock_llm):
        # Create 10 files but cap at 3
        for i in range(10):
            (tmp_path / f"file_{i}.py").write_text(f"x = {i}\n")

        scanner = CodeScanner(
            llm=mock_llm, sanitizer=sanitizer,
            code_root=tmp_path, max_files=3, profile=PYTHON_PROFILE,
        )
        files = scanner._find_source_files()
        assert len(files) == 3

    def test_custom_exclude_patterns(self, tmp_path, sanitizer, mock_llm):
        (tmp_path / "main.py").write_text("x = 1\n")
        (tmp_path / "generated.py").write_text("x = 2\n")

        scanner = CodeScanner(
            llm=mock_llm, sanitizer=sanitizer,
            code_root=tmp_path, profile=PYTHON_PROFILE,
            exclude_patterns=["generated"],
        )
        files = scanner._find_source_files()
        paths = [f.name for f in files]
        assert "main.py" in paths
        assert "generated.py" not in paths

    def test_empty_directory(self, tmp_path, sanitizer, mock_llm):
        scanner = CodeScanner(
            llm=mock_llm, sanitizer=sanitizer,
            code_root=tmp_path, profile=PYTHON_PROFILE,
        )
        files = scanner._find_source_files()
        assert files == []

    def test_priority_keyword_sorting(self, tmp_path, sanitizer, mock_llm):
        (tmp_path / "utils.py").write_text("x = 1\n")
        (tmp_path / "train.py").write_text("x = 2\n")
        (tmp_path / "model.py").write_text("x = 3\n")

        scanner = CodeScanner(
            llm=mock_llm, sanitizer=sanitizer,
            code_root=tmp_path, profile=PYTHON_PROFILE,
        )
        files = scanner._find_source_files()
        names = [f.name for f in files]
        # "train" and "model" are priority keywords, should come before "utils"
        train_idx = names.index("train.py")
        utils_idx = names.index("utils.py")
        assert train_idx < utils_idx


# ── Stage 1: File card building ───────────────────────────────────

class TestStage1FileCards:
    def test_builds_cards_for_all_files(self, tmp_code_root, sanitizer, mock_llm):
        scanner = CodeScanner(
            llm=mock_llm, sanitizer=sanitizer,
            code_root=tmp_code_root, profile=PYTHON_PROFILE,
        )
        files = scanner._find_source_files()
        cards = scanner._build_file_cards(files)
        assert len(cards) == len(files)
        assert all(c.language == "python" for c in cards)

    def test_sanitizes_snippets(self, tmp_path, sanitizer, mock_llm):
        secret_file = tmp_path / "secret.py"
        secret_file.write_text('API_KEY = "sk-ant-secret123"\ndef main(): pass\n')

        scanner = CodeScanner(
            llm=mock_llm, sanitizer=sanitizer,
            code_root=tmp_path, profile=PYTHON_PROFILE,
        )
        files = scanner._find_source_files()
        cards = scanner._build_file_cards(files)
        # Snippets should have secrets redacted
        for card in cards:
            for snippet in card.snippets:
                assert "sk-ant-secret123" not in snippet


# ── Stage 2: LLM ranking with fallback ───────────────────────────

class TestStage2Ranking:
    async def test_llm_ranking_success(self, tmp_code_root, sanitizer, mock_llm):
        mock_llm.complete_json.return_value = {
            "ranked_files": [
                {"path": "train.py", "role": "training", "confidence": 0.9},
                {"path": "utils.py", "role": "utility", "confidence": 0.3},
            ]
        }

        scanner = CodeScanner(
            llm=mock_llm, sanitizer=sanitizer,
            code_root=tmp_code_root, profile=PYTHON_PROFILE,
        )
        files = scanner._find_source_files()
        cards = scanner._build_file_cards(files)
        ranked_paths, roles = await scanner._rank_files(cards)

        assert ranked_paths[0] == "train.py"
        assert roles["train.py"] == "training"

    async def test_heuristic_fallback_on_llm_failure(self, tmp_code_root, sanitizer, mock_llm):
        mock_llm.complete_json.side_effect = Exception("LLM unavailable")

        scanner = CodeScanner(
            llm=mock_llm, sanitizer=sanitizer,
            code_root=tmp_code_root, profile=PYTHON_PROFILE,
        )
        files = scanner._find_source_files()
        cards = scanner._build_file_cards(files)
        ranked_paths, roles = await scanner._rank_files(cards)

        # Should fall back to heuristic sort (all card paths returned)
        assert len(ranked_paths) == len(cards)

    async def test_heuristic_fallback_on_malformed_json(self, tmp_code_root, sanitizer, mock_llm):
        mock_llm.complete_json.return_value = {"ranked_files": []}

        scanner = CodeScanner(
            llm=mock_llm, sanitizer=sanitizer,
            code_root=tmp_code_root, profile=PYTHON_PROFILE,
        )
        files = scanner._find_source_files()
        cards = scanner._build_file_cards(files)
        ranked_paths, roles = await scanner._rank_files(cards)

        # Empty result → fallback
        assert len(ranked_paths) == len(cards)


# ── Stage 3: Batched deep analysis ───────────────────────────────

class TestStage3BatchAnalysis:
    async def test_batch_grouping(self, tmp_code_root, sanitizer, mock_llm):
        mock_llm.complete_json.return_value = {
            "model_classes": ["XGBClassifier"],
            "features": ["x1"],
            "code_evidence": [],
        }

        scanner = CodeScanner(
            llm=mock_llm, sanitizer=sanitizer,
            code_root=tmp_code_root, profile=PYTHON_PROFILE,
            batch_size=2, analysis_timeout=30.0,
        )
        paths = ["train.py", "utils.py", "config.py"]
        results, skipped = await scanner._batch_analyze(
            paths, {}, lambda p: None,
        )
        # 3 files / batch_size 2 = 2 batches
        assert mock_llm.complete_json.call_count == 2
        assert len(results) == 2
        assert skipped == []

    async def test_partial_failure(self, tmp_code_root, sanitizer, mock_llm):
        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "model_classes": ["XGBClassifier"],
                    "features": [],
                    "code_evidence": [],
                }
            raise Exception("Batch 2 failed")

        mock_llm.complete_json.side_effect = side_effect

        scanner = CodeScanner(
            llm=mock_llm, sanitizer=sanitizer,
            code_root=tmp_code_root, profile=PYTHON_PROFILE,
            batch_size=2, scan_retries=0, analysis_timeout=30.0,
        )
        paths = ["train.py", "utils.py", "config.py"]
        results, skipped = await scanner._batch_analyze(
            paths, {}, lambda p: None,
        )
        assert len(results) == 1
        assert len(skipped) > 0

    async def test_all_batches_fail(self, tmp_code_root, sanitizer, mock_llm):
        mock_llm.complete_json.side_effect = Exception("All fail")

        scanner = CodeScanner(
            llm=mock_llm, sanitizer=sanitizer,
            code_root=tmp_code_root, profile=PYTHON_PROFILE,
            batch_size=2, scan_retries=0, analysis_timeout=30.0,
        )
        paths = ["train.py", "utils.py"]
        results, skipped = await scanner._batch_analyze(
            paths, {}, lambda p: None,
        )
        assert len(results) == 0
        assert len(skipped) == 2


# ── Stage 4: Merge results ───────────────────────────────────────

class TestStage4Merge:
    def test_single_batch_passthrough(self, sanitizer, mock_llm):
        scanner = CodeScanner(
            llm=mock_llm, sanitizer=sanitizer,
            code_root=Path("/tmp"), profile=PYTHON_PROFILE,
        )
        result = scanner._merge_results(
            [{
                "model_classes": ["XGBClassifier"],
                "features": ["x1", "x2"],
                "target_variable": "y",
                "ml_task_type": "classification",
                "hyperparameters": {"n_estimators": 100},
                "data_sources": ["data.csv"],
                "insights": "Uses gradient boosting.",
                "transformations": [],
                "code_evidence": [],
            }],
            ranked_paths=["train.py"],
        )
        assert result.model_classes == ["XGBClassifier"]
        assert result.target_variable == "y"

    def test_list_field_union_with_dedup(self, sanitizer, mock_llm):
        scanner = CodeScanner(
            llm=mock_llm, sanitizer=sanitizer,
            code_root=Path("/tmp"), profile=PYTHON_PROFILE,
        )
        result = scanner._merge_results(
            [
                {"model_classes": ["XGB", "RF"], "features": ["x1"], "code_evidence": [],
                 "target_variable": None, "ml_task_type": None, "hyperparameters": {},
                 "data_sources": [], "insights": "", "transformations": []},
                {"model_classes": ["RF", "SVM"], "features": ["x1", "x2"], "code_evidence": [],
                 "target_variable": None, "ml_task_type": None, "hyperparameters": {},
                 "data_sources": [], "insights": "", "transformations": []},
            ],
            ranked_paths=["train.py", "model.py"],
        )
        assert result.model_classes == ["XGB", "RF", "SVM"]
        assert result.features == ["x1", "x2"]

    def test_single_value_conflict_uses_highest_ranked(self, sanitizer, mock_llm):
        scanner = CodeScanner(
            llm=mock_llm, sanitizer=sanitizer,
            code_root=Path("/tmp"), profile=PYTHON_PROFILE,
        )
        result = scanner._merge_results(
            [
                {"model_classes": [], "features": [], "code_evidence": [
                    {"file": "train.py", "statement": "s1"}
                ], "target_variable": "y_pred", "ml_task_type": "classification",
                 "hyperparameters": {}, "data_sources": [], "insights": "Batch 1",
                 "transformations": []},
                {"model_classes": [], "features": [], "code_evidence": [
                    {"file": "eval.py", "statement": "s2"}
                ], "target_variable": "label", "ml_task_type": "regression",
                 "hyperparameters": {}, "data_sources": [], "insights": "Batch 2",
                 "transformations": []},
            ],
            ranked_paths=["train.py", "eval.py"],
        )
        # train.py is rank 0 → its batch should win
        assert result.ml_task_type == "classification"
        assert result.target_variable == "y_pred"

    def test_insights_concatenated(self, sanitizer, mock_llm):
        scanner = CodeScanner(
            llm=mock_llm, sanitizer=sanitizer,
            code_root=Path("/tmp"), profile=PYTHON_PROFILE,
        )
        result = scanner._merge_results(
            [
                {"model_classes": [], "features": [], "code_evidence": [],
                 "target_variable": None, "ml_task_type": None, "hyperparameters": {},
                 "data_sources": [], "insights": "Uses XGBoost", "transformations": []},
                {"model_classes": [], "features": [], "code_evidence": [],
                 "target_variable": None, "ml_task_type": None, "hyperparameters": {},
                 "data_sources": [], "insights": "StandardScaler applied", "transformations": []},
            ],
            ranked_paths=[],
        )
        assert "Uses XGBoost" in result.insights
        assert "StandardScaler applied" in result.insights
