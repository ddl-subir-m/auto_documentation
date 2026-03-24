"""Tests for multi-language prompt parameterization."""

import pytest

from autodoc.core.models import PYTHON_PROFILE, R_PROFILE, SAS_PROFILE, MATLAB_PROFILE
from autodoc.llm.prompts import build_code_analysis_prompt, CODE_ANALYSIS_SCHEMA


class TestCodeFenceLanguage:
    def test_python_fence(self):
        contents = [{"file": "train.py", "content": "import sklearn"}]
        prompt = build_code_analysis_prompt(contents, profile=PYTHON_PROFILE)
        assert "```python" in prompt

    def test_r_fence(self):
        contents = [{"file": "train.R", "content": "library(caret)"}]
        prompt = build_code_analysis_prompt(contents, profile=R_PROFILE)
        assert "```r" in prompt

    def test_sas_fence(self):
        contents = [{"file": "model.sas", "content": "PROC LOGISTIC;"}]
        prompt = build_code_analysis_prompt(contents, profile=SAS_PROFILE)
        assert "```sas" in prompt

    def test_matlab_fence(self):
        contents = [{"file": "train.m", "content": "mdl = fitctree(X,Y);"}]
        prompt = build_code_analysis_prompt(contents, profile=MATLAB_PROFILE)
        assert "```matlab" in prompt

    def test_default_is_python(self):
        contents = [{"file": "train.py", "content": "x = 1"}]
        prompt = build_code_analysis_prompt(contents)
        assert "```python" in prompt


class TestFrameworkHints:
    def test_r_hints_in_prompt(self):
        contents = [{"file": "model.R", "content": "library(caret)"}]
        prompt = build_code_analysis_prompt(contents, profile=R_PROFILE)
        assert "tidymodels" in prompt
        assert "caret" in prompt
        assert "library()" in prompt

    def test_sas_hints_in_prompt(self):
        contents = [{"file": "model.sas", "content": "PROC LOGISTIC;"}]
        prompt = build_code_analysis_prompt(contents, profile=SAS_PROFILE)
        assert "PROC LOGISTIC" in prompt
        assert "PROC FOREST" in prompt

    def test_matlab_hints_in_prompt(self):
        contents = [{"file": "train.m", "content": "mdl = fitctree(X,Y);"}]
        prompt = build_code_analysis_prompt(contents, profile=MATLAB_PROFILE)
        assert "fitcecoc" in prompt
        assert "trainNetwork" in prompt

    def test_python_hints_in_prompt(self):
        contents = [{"file": "train.py", "content": "import sklearn"}]
        prompt = build_code_analysis_prompt(contents, profile=PYTHON_PROFILE)
        assert "scikit-learn" in prompt


class TestLibraryExamples:
    def test_r_library_examples(self):
        contents = [{"file": "model.R", "content": "x"}]
        prompt = build_code_analysis_prompt(contents, profile=R_PROFILE)
        assert "tidymodels" in prompt
        assert "ranger" in prompt

    def test_python_library_examples(self):
        contents = [{"file": "train.py", "content": "x"}]
        prompt = build_code_analysis_prompt(contents, profile=PYTHON_PROFILE)
        assert "sklearn" in prompt
        assert "xgboost" in prompt


class TestSchemaHasLineNumbers:
    def test_code_evidence_has_start_line(self):
        props = CODE_ANALYSIS_SCHEMA["properties"]["code_evidence"]["items"]["properties"]
        assert "start_line" in props
        assert props["start_line"]["type"] == "integer"

    def test_code_evidence_has_end_line(self):
        props = CODE_ANALYSIS_SCHEMA["properties"]["code_evidence"]["items"]["properties"]
        assert "end_line" in props
        assert props["end_line"]["type"] == "integer"


class TestLineNumberInstructions:
    def test_prompt_mentions_line_numbers(self):
        contents = [{"file": "train.py", "content": "1: import sklearn\n2: model.fit()"}]
        prompt = build_code_analysis_prompt(contents, profile=PYTHON_PROFILE)
        assert "start_line" in prompt
        assert "end_line" in prompt
