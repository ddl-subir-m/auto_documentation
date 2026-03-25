"""Tests for ranking prompt and modified analysis prompt."""

import pytest

from autodoc.scanning.file_card import FileCard
from autodoc.llm.prompts import (
    build_ranking_prompt,
    build_code_analysis_prompt,
    RANKING_SCHEMA,
    CODE_ANALYSIS_SCHEMA,
)
from autodoc.core.models import PYTHON_PROFILE


# ── Ranking prompt ────────────────────────────────────────────────

class TestBuildRankingPrompt:
    def test_includes_all_file_cards(self):
        cards = [
            FileCard(path="train.py", language="python", size=1000,
                     imports=["sklearn"], symbols=["def train"], snippets=["model.fit(X)"]),
            FileCard(path="utils.py", language="python", size=500,
                     imports=[], symbols=["def normalize"], snippets=["x / x.max()"]),
        ]
        prompt = build_ranking_prompt(cards, profile=PYTHON_PROFILE)
        assert "train.py" in prompt
        assert "utils.py" in prompt
        assert "sklearn" in prompt
        assert "model.fit(X)" in prompt

    def test_includes_language_hints(self):
        cards = [FileCard(path="a.py", language="python", size=100)]
        prompt = build_ranking_prompt(cards, profile=PYTHON_PROFILE)
        assert "Python" in prompt

    def test_ranking_schema_structure(self):
        assert "ranked_files" in RANKING_SCHEMA["properties"]
        items = RANKING_SCHEMA["properties"]["ranked_files"]["items"]
        assert "path" in items["properties"]
        assert "role" in items["properties"]
        assert items["required"] == ["path", "role"]


# ── Analysis prompt (modified) ────────────────────────────────────

class TestBuildCodeAnalysisPrompt:
    def test_no_line_number_instructions(self):
        """Prompt should NOT mention line-numbered source or start_line/end_line."""
        contents = [{"file": "train.py", "content": "import sklearn\n"}]
        prompt = build_code_analysis_prompt(contents, profile=PYTHON_PROFILE)
        assert "line-numbered source" not in prompt
        assert "start_line" not in prompt
        assert "end_line" not in prompt
        assert "42: code_here" not in prompt

    def test_includes_file_roles(self):
        contents = [{"file": "train.py", "content": "model.fit(X, y)\n"}]
        roles = {"train.py": "training"}
        prompt = build_code_analysis_prompt(contents, profile=PYTHON_PROFILE, file_roles=roles)
        assert "(role: training)" in prompt

    def test_no_roles_when_not_provided(self):
        contents = [{"file": "train.py", "content": "model.fit(X, y)\n"}]
        prompt = build_code_analysis_prompt(contents, profile=PYTHON_PROFILE)
        assert "(role:" not in prompt

    def test_schema_no_line_number_fields(self):
        """CODE_ANALYSIS_SCHEMA should not ask LLM for start_line/end_line."""
        evidence_props = CODE_ANALYSIS_SCHEMA["properties"]["code_evidence"]["items"]["properties"]
        assert "start_line" not in evidence_props
        assert "end_line" not in evidence_props
        # Should still have statement, file, symbol, snippet
        assert "statement" in evidence_props
        assert "file" in evidence_props
        assert "symbol" in evidence_props
        assert "snippet" in evidence_props
