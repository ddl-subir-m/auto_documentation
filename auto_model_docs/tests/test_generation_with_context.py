"""Tests for bundle_context in generation prompts (U6).

Verifies that ContentGenerator threads bundle_context into the LLM prompt when
provided, and that omitting bundle_context leaves prompts byte-identical to
the spec-only flow.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from autodoc.core.models import (
    ArtifactContext,
    CodeContext,
    ContentBlock,
    ContentType,
    GenerationContext,
)
from autodoc.generation.generator import ContentGenerator
from autodoc.llm.prompts import format_bundle_context


BUNDLE_FIXTURE = {
    "bundle_id": "bundle-42",
    "policy_version_id": "policy-v3",
    "bundle": {
        "owner": "Alice Chen",
        "risk_tier": "High",
        "intended_use": "Credit risk scoring for consumer loans, US market.",
    },
    "policy_def": {"version": "1.0", "framework": "SR 11-7"},
}


def _make_ctx(bundle_context=None):
    return GenerationContext(
        code_context=CodeContext(
            model_classes=["XGBClassifier"],
            features=["age", "income"],
            ml_task_type="classification",
            target_variable="default",
            data_sources=["loans.csv"],
            insights="",
        ),
        artifact_context=ArtifactContext(),
        section_name="Purpose",
        bundle_context=bundle_context,
    )


class _FakeResponse:
    def __init__(self, content):
        self.content = content


def _make_llm(response_text="Generated narrative."):
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=_FakeResponse(response_text))
    llm.complete_json = AsyncMock(return_value={})
    return llm


class TestFormatBundleContext:
    def test_none_returns_empty(self):
        assert format_bundle_context(None) == ""

    def test_empty_dict_returns_empty(self):
        assert format_bundle_context({}) == ""

    def test_includes_bundle_fields(self):
        out = format_bundle_context(BUNDLE_FIXTURE)
        assert "Alice Chen" in out
        assert "High" in out
        assert "Credit risk scoring" in out
        assert "cite verbatim" in out.lower()
        assert "factual grounding" in out.lower()


@pytest.mark.asyncio
async def test_narrative_prompt_includes_bundle_when_provided():
    llm = _make_llm("draft narrative")
    gen = ContentGenerator(llm=llm)
    ctx = _make_ctx(bundle_context=BUNDLE_FIXTURE)
    block = ContentBlock(
        type=ContentType.NARRATIVE,
        purpose="Describe the model",
        data_needed="Model overview",
    )

    await gen.generate(block, ctx)

    assert llm.complete.await_count == 1
    prompt = llm.complete.await_args.kwargs.get("prompt") or llm.complete.await_args.args[0]
    assert "Alice Chen" in prompt
    assert "High" in prompt
    assert "Credit risk scoring" in prompt
    assert "factual grounding" in prompt.lower()


@pytest.mark.asyncio
async def test_narrative_prompt_unchanged_when_bundle_context_none():
    """Omitting bundle_context produces the same prompt bytes as before."""
    llm_with = _make_llm()
    llm_without = _make_llm()
    gen = ContentGenerator(llm=llm_with)
    gen_baseline = ContentGenerator(llm=llm_without)
    block = ContentBlock(
        type=ContentType.NARRATIVE,
        purpose="Describe the model",
        data_needed="Model overview",
    )

    await gen.generate(block, _make_ctx(bundle_context=None))
    await gen_baseline.generate(block, _make_ctx(bundle_context=None))

    prompt_a = llm_with.complete.await_args.kwargs["prompt"]
    prompt_b = llm_without.complete.await_args.kwargs["prompt"]
    assert prompt_a == prompt_b
    # No governance grounding section leaked into the baseline prompt.
    # (The integrity rules reference 'Governance Bundle Context block' as
    # context-free guidance; we look for the actual section header.)
    assert "## Governance Bundle Context" not in prompt_a
    assert "factual grounding" not in prompt_a.lower()


@pytest.mark.asyncio
async def test_table_prompt_includes_bundle_when_provided():
    llm = MagicMock()
    llm.complete_json = AsyncMock(
        return_value={
            "caption": "Model metrics",
            "columns": ["metric", "value"],
            "rows": [{"metric": "accuracy", "value": 0.9}],
        }
    )
    gen = ContentGenerator(llm=llm)
    block = ContentBlock(
        type=ContentType.TABLE,
        purpose="Feature descriptions",
        data_needed="feature list",
    )

    await gen.generate(block, _make_ctx(bundle_context=BUNDLE_FIXTURE))

    prompt = llm.complete_json.await_args.kwargs["prompt"]
    assert "Alice Chen" in prompt
    assert "Governance Bundle Context" in prompt


class TestPromptBuilderRegression:
    """Prompt builders produce byte-identical output to the spec-only flow when
    bundle_context is omitted or None."""

    def _narrative_args(self):
        return dict(
            section_name="Overview",
            purpose="describe",
            data_needed="model details",
            model_classes="XGBClassifier",
            ml_task_type="classification",
            target_variable="default",
            features="age, income",
            data_sources="loans.csv",
            model_name=None,
            model_info="",
            insights="",
            artifact_data="",
            code_evidence="",
            mlflow_evidence="",
        )

    def test_narrative_prompt_default_matches_none(self):
        from autodoc.llm.prompts import build_narrative_prompt

        base = self._narrative_args()
        assert build_narrative_prompt(**base) == build_narrative_prompt(
            **base, bundle_context=None
        )

    def test_table_prompt_default_matches_none(self):
        from autodoc.llm.prompts import build_table_prompt

        args = dict(
            purpose="Feature table",
            data_needed="features",
            features="age, income",
            model_classes="XGBClassifier",
            transformations="scaling",
            hyperparameters="n_estimators=100",
        )
        assert build_table_prompt(**args) == build_table_prompt(
            **args, bundle_context=None
        )

    def test_chart_prompt_default_matches_none(self):
        from autodoc.llm.prompts import build_chart_prompt

        args = dict(
            purpose="Performance chart",
            data_needed="metrics",
            chart_type="bar",
            model_classes="XGBClassifier",
            ml_task_type="classification",
        )
        assert build_chart_prompt(**args) == build_chart_prompt(
            **args, bundle_context=None
        )

    def test_list_prompt_default_matches_none(self):
        from autodoc.llm.prompts import build_list_prompt

        args = dict(
            purpose="Top risks",
            data_needed="risks",
            model_classes="XGBClassifier",
            ml_task_type="classification",
            features="age, income",
        )
        assert build_list_prompt(**args) == build_list_prompt(
            **args, bundle_context=None
        )


@pytest.mark.asyncio
async def test_list_prompt_includes_bundle_when_provided():
    llm = MagicMock()
    llm.complete_json = AsyncMock(
        return_value={"title": "Risks", "items": ["Risk A", "Risk B", "Risk C"]}
    )
    gen = ContentGenerator(llm=llm)
    block = ContentBlock(
        type=ContentType.BULLET_LIST,
        purpose="Top risks",
        data_needed="risks",
    )

    await gen.generate(block, _make_ctx(bundle_context=BUNDLE_FIXTURE))

    prompt = llm.complete_json.await_args.kwargs["prompt"]
    assert "Credit risk scoring" in prompt
