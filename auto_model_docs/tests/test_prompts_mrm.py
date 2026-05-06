"""Tests for the MRM-aware prompt layer.

Covers:
- Doc-type framing (mdd / vr / mr) injection
- Bundle-context redaction (approvals, internal IDs stripped)
- Citation-id minting from redacted facts
- Per-section keyword slicing
- Anti-fabrication rules consolidated into a single block
- Other-sections block appears when provided
- REQUIRED_KEYS export from autodoc.bundle_context
"""

from __future__ import annotations

import pytest

from autodoc import bundle_context as bundle_context_module
from autodoc.llm.prompts import (
    DOC_TYPE_FRAMING,
    DOC_TYPE_LABELS,
    EVIDENCE_INTEGRITY_RULES,
    build_chart_prompt,
    build_list_prompt,
    build_narrative_prompt,
    build_section_planning_prompt,
    build_table_prompt,
    doc_type_framing_block,
    format_bundle_context,
    redact_bundle_context,
    slice_relevant_keys,
)


# ---------------------------------------------------------------------------
# Doc-type framing
# ---------------------------------------------------------------------------


class TestDocTypeFraming:
    def test_three_canonical_types_present(self):
        assert set(DOC_TYPE_FRAMING.keys()) == {"mdd", "vr", "mr"}
        assert set(DOC_TYPE_LABELS.keys()) == {"mdd", "vr", "mr"}

    def test_unknown_doc_type_returns_empty(self):
        assert doc_type_framing_block(None) == ""
        assert doc_type_framing_block("") == ""
        assert doc_type_framing_block("custom_yaml") == ""

    def test_known_doc_type_appears_in_block(self):
        for key in ("mdd", "vr", "mr"):
            block = doc_type_framing_block(key)
            assert "Document Type Framing" in block
            assert DOC_TYPE_FRAMING[key].split(".")[0] in block

    def test_narrative_prompt_includes_framing_when_doc_type_set(self):
        args = _narrative_args()
        without = build_narrative_prompt(**args)
        with_mdd = build_narrative_prompt(**args, doc_type="mdd")
        with_vr = build_narrative_prompt(**args, doc_type="vr")
        assert "Document Type Framing" not in without
        assert "Document Type Framing" in with_mdd
        assert "MDD" in with_mdd
        assert "Validation Report" in with_vr or "VR" in with_vr
        # MDD and VR framings differ
        assert with_mdd != with_vr

    def test_table_chart_list_section_planner_accept_doc_type(self):
        # Smoke test that the kwarg threads through every builder.
        build_table_prompt(
            purpose="t", data_needed=None, features="", model_classes="",
            transformations="", hyperparameters="", doc_type="mdd",
        )
        build_chart_prompt(
            purpose="c", data_needed=None, chart_type="bar",
            model_classes="", ml_task_type="", doc_type="vr",
        )
        build_list_prompt(
            purpose="l", data_needed=None, model_classes="",
            ml_task_type="", features="", doc_type="mr",
        )
        out = build_section_planning_prompt(
            section_name="Limitations", hint=None, model_name=None,
            model_classes="", ml_task_type="", features_preview="",
            target_variable="", registered_models="", data_sources="",
            doc_type="vr",
        )
        assert "Document Type Framing" in out


# ---------------------------------------------------------------------------
# Bundle redaction
# ---------------------------------------------------------------------------


_DOMINO_BUNDLE = {
    "bundle_id": "bundle-42",
    "policy_version_id": "policy-v3",
    "bundle": {
        "name": "Loan Default Bundle",
        "owner": "Alice Chen",
        "risk_tier": "High",
        "intended_use": "Credit risk scoring for consumer loans, US market.",
        # Nuisance keys that should NOT be forwarded.
        "internalAuditId": "audit-9999",
        "rawAttachments": [{"id": "a1", "blob": "x" * 10000}],
        "sensitive_token": "secret",
    },
    "policy_def": {
        "id": "pol-1",
        "version": "v1.0",
        "framework": "SR 11-7",
        "stages": [
            {
                "name": "Intake",
                "evidenceSet": [
                    {"definition": [
                        {"artifactType": "textinput",
                         "details": {"label": "Model Card"}},
                        {"artifactType": "text",
                         "details": {"label": "Workflow guidance blurb — drop me"}},
                    ]},
                ],
                "approvals": [
                    {"evidence": {"definition": [
                        {"artifactType": "textinput",
                         "details": {"label": "Do you approve this model?"}}
                    ]}}
                ],
            },
            {
                "name": "Validation",
                "evidenceSet": [
                    {"artifacts": [
                        {"artifactType": "fileupload",
                         "details": {"label": "Backtest Results"}},
                    ]},
                ],
            },
        ],
    },
}


class TestRedactor:
    def test_keeps_allowlisted_bundle_keys(self):
        out = redact_bundle_context(_DOMINO_BUNDLE)
        assert out["bundle"]["owner"] == "Alice Chen"
        assert out["bundle"]["risk_tier"] == "High"
        assert "intended_use" in out["bundle"]
        assert out["bundle"]["name"] == "Loan Default Bundle"

    def test_drops_non_allowlisted_bundle_keys(self):
        out = redact_bundle_context(_DOMINO_BUNDLE)
        assert "internalAuditId" not in out["bundle"]
        assert "rawAttachments" not in out["bundle"]
        assert "sensitive_token" not in out["bundle"]

    def test_keeps_policy_top_level(self):
        out = redact_bundle_context(_DOMINO_BUNDLE)
        assert out["policy"]["framework"] == "SR 11-7"
        assert out["policy"]["version"] == "v1.0"

    def test_walks_stages_skipping_approvals_and_text(self):
        out = redact_bundle_context(_DOMINO_BUNDLE)
        stages = out["policy"]["stages"]
        names = [s["name"] for s in stages]
        assert names == ["Intake", "Validation"]
        # Approval question + free-text guidance both filtered.
        all_evidence = [e for s in stages for e in s["evidence"]]
        assert "Model Card" in all_evidence
        assert "Backtest Results" in all_evidence
        assert "Do you approve this model?" not in all_evidence
        assert "Workflow guidance blurb — drop me" not in all_evidence

    def test_empty_input_returns_empty(self):
        assert redact_bundle_context(None) == {}
        assert redact_bundle_context({}) == {}

    def test_redactor_idempotent_via_format(self):
        # Already-redacted dict shouldn't double-redact the structure.
        once = redact_bundle_context(_DOMINO_BUNDLE)
        formatted = format_bundle_context(once)
        assert "Alice Chen" in formatted
        assert "Backtest Results" in formatted
        assert "Do you approve" not in formatted


class TestCitationMinting:
    def test_format_emits_dotted_ids(self):
        formatted = format_bundle_context(_DOMINO_BUNDLE)
        assert "[@bundle.id]:" in formatted
        assert "[@bundle.owner]:" in formatted
        assert "[@bundle.risk_tier]:" in formatted
        assert "[@policy.framework]:" in formatted

    def test_unredacted_size_strictly_smaller(self):
        # The redacted prompt section must be smaller than naive json dump
        # of the input (drops blobs + approvals).
        import json
        naive = json.dumps(_DOMINO_BUNDLE, indent=2, sort_keys=True, default=str)
        formatted = format_bundle_context(_DOMINO_BUNDLE)
        assert len(formatted) < len(naive), (
            "Redaction must produce a smaller grounding block than naive dump"
        )


# ---------------------------------------------------------------------------
# Section keyword slicing
# ---------------------------------------------------------------------------


class TestSectionSlicing:
    def setup_method(self):
        self.redacted = redact_bundle_context(_DOMINO_BUNDLE)

    def test_purpose_section_keeps_intended_use(self):
        out = slice_relevant_keys("Intended Use and Business Purpose", self.redacted)
        assert "bundle" in out
        assert "intended_use" in out["bundle"]

    def test_governance_section_keeps_owner(self):
        out = slice_relevant_keys("Governance, Roles, and Approvals", self.redacted)
        assert "bundle" in out
        assert "owner" in out["bundle"]

    def test_unknown_section_returns_full_redacted(self):
        # Sections without a matching keyword keep all redacted facts (broad
        # grounding > silent suppression).
        out = slice_relevant_keys("Methodology and Model Design", self.redacted)
        assert out == self.redacted

    def test_empty_redacted_returns_empty(self):
        assert slice_relevant_keys("anything", {}) == {}


# ---------------------------------------------------------------------------
# Evidence integrity rules — DRY
# ---------------------------------------------------------------------------


class TestIntegrityRules:
    @pytest.mark.parametrize("builder,kwargs", [
        (build_narrative_prompt, _narrative_args := lambda: dict(
            section_name="X", purpose="y", data_needed=None,
            model_classes="", ml_task_type="", target_variable="",
            features="", data_sources="", model_name=None, model_info="",
            insights="",
        )),
    ])
    def test_narrative_includes_rules(self, builder, kwargs):
        out = builder(**kwargs())
        assert "Evidence integrity rules" in out

    def test_table_includes_rules(self):
        out = build_table_prompt(
            purpose="t", data_needed=None, features="",
            model_classes="", transformations="", hyperparameters="",
        )
        assert "Evidence integrity rules" in out

    def test_chart_includes_rules(self):
        out = build_chart_prompt(
            purpose="c", data_needed=None, chart_type="bar",
            model_classes="", ml_task_type="",
        )
        assert "Evidence integrity rules" in out

    def test_list_includes_rules(self):
        out = build_list_prompt(
            purpose="l", data_needed=None, model_classes="",
            ml_task_type="", features="",
        )
        assert "Evidence integrity rules" in out

    def test_rules_mention_governance_grounding(self):
        # MRM-specific clause must be present.
        assert "Governance Bundle Context" in EVIDENCE_INTEGRITY_RULES
        assert "intended use" in EVIDENCE_INTEGRITY_RULES.lower() or \
               "validation status" in EVIDENCE_INTEGRITY_RULES.lower()


# ---------------------------------------------------------------------------
# Other-sections block (do not duplicate other sections)
# ---------------------------------------------------------------------------


class TestOtherSectionsBlock:
    def test_omitted_when_none(self):
        prompt = build_narrative_prompt(**_narrative_args())
        assert "Other sections in this document" not in prompt

    def test_present_when_provided(self):
        prompt = build_narrative_prompt(
            **_narrative_args(),
            other_sections=["Executive Summary", "Limitations"],
        )
        assert "Other sections in this document" in prompt
        assert "Executive Summary" in prompt
        assert "Limitations" in prompt

    def test_self_section_filtered(self):
        prompt = build_narrative_prompt(
            **_narrative_args(),
            other_sections=["X-section", "Other"],
        )
        # _narrative_args's section_name is "Overview"; both stay.
        assert "X-section" in prompt
        # Now pass section_name itself — it shouldn't appear under "other".
        args = _narrative_args()
        args["section_name"] = "Overview"
        prompt2 = build_narrative_prompt(
            **args,
            other_sections=["Overview", "Limitations"],
        )
        # The "Limitations" line should appear, but the bullet list should
        # not contain the section's own name.
        bullet_section_start = prompt2.find("Other sections in this document")
        bullet_section = prompt2[bullet_section_start:]
        assert "  - Limitations" in bullet_section
        assert "  - Overview" not in bullet_section


# ---------------------------------------------------------------------------
# REQUIRED_KEYS export from bundle_context
# ---------------------------------------------------------------------------


class TestRequiredKeysExport:
    def test_public_required_keys_exported(self):
        assert hasattr(bundle_context_module, "REQUIRED_KEYS")
        keys = {k for k, _ in bundle_context_module.REQUIRED_KEYS}
        assert keys == {"bundle_id", "policy_version_id", "bundle", "policy_def"}

    def test_backward_compat_alias_still_present(self):
        # Don't break any consumer that read the underscore name.
        assert bundle_context_module._REQUIRED_KEYS is bundle_context_module.REQUIRED_KEYS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _narrative_args():
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
    )
