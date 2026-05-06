"""Tests for autodoc.spec_from_policy (U17)."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from click.testing import CliRunner

from autodoc.spec_from_policy import VALID_DOC_TYPES, derive_spec


# Shape check: these are the top-level keys every canonical template has.
_CANONICAL_TOP_LEVEL_KEYS = {
    "template_version",
    "template_id",
    "template_label",
    "title",
    "authors",
    "citation_style",
    "sections",
    "hints",
}


def _load_canonical(doc_type: str) -> dict:
    templates_dir = Path(__file__).resolve().parent.parent / "autodoc" / "templates"
    with open(templates_dir / f"{doc_type}_spec.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Happy-path derivation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("doc_type", VALID_DOC_TYPES)
def test_derive_happy_path_seeds_sections_from_both_keys(doc_type):
    policy_def = {
        "required_artifacts": ["Model Card", "Risk Assessment"],
        "sections": ["Ownership", "Intended Use"],
    }
    spec = derive_spec(policy_def, doc_type)

    assert spec["sections"] == [
        "Model Card",
        "Risk Assessment",
        "Ownership",
        "Intended Use",
    ]
    # Canonical template metadata preserved.
    canonical = _load_canonical(doc_type)
    assert spec["title"] == canonical["title"]
    assert spec["template_id"] == canonical["template_id"]
    assert spec["template_version"] == canonical["template_version"]


def test_derive_dedupes_and_preserves_order():
    policy_def = {
        "required_artifacts": ["Alpha", "Beta", "Alpha"],
        "sections": ["Beta", "Gamma"],
    }
    spec = derive_spec(policy_def, "mdd")
    assert spec["sections"] == ["Alpha", "Beta", "Gamma"]


def test_derive_only_required_artifacts():
    policy_def = {"required_artifacts": ["A", "B"]}
    spec = derive_spec(policy_def, "mdd")
    assert spec["sections"] == ["A", "B"]


def test_derive_only_sections():
    policy_def = {"sections": ["X", "Y"]}
    spec = derive_spec(policy_def, "mdd")
    assert spec["sections"] == ["X", "Y"]


def test_derive_keeps_hint_when_section_name_matches_canonical():
    canonical = _load_canonical("mdd")
    matching_name = next(iter(canonical["hints"]))  # e.g. "Executive Summary"
    policy_def = {"sections": [matching_name, "New Custom Section"]}

    spec = derive_spec(policy_def, "mdd")
    assert spec["hints"].get(matching_name) == canonical["hints"][matching_name]
    assert "New Custom Section" not in spec["hints"]


# ---------------------------------------------------------------------------
# Missing-key fallback
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("doc_type", VALID_DOC_TYPES)
def test_fallback_when_both_keys_missing(doc_type, caplog):
    caplog.set_level(logging.WARNING, logger="autodoc.spec_from_policy")
    policy_def = {"id": "pv-1", "version": "1.0", "framework": "SR 11-7"}

    spec = derive_spec(policy_def, doc_type)

    assert spec == _load_canonical(doc_type)
    assert any("falling back" in rec.message for rec in caplog.records)


def test_fallback_when_policy_def_is_none(caplog):
    caplog.set_level(logging.WARNING, logger="autodoc.spec_from_policy")
    spec = derive_spec(None, "mdd")
    assert spec == _load_canonical("mdd")
    assert caplog.records


def test_fallback_when_policy_def_is_not_a_dict(caplog):
    caplog.set_level(logging.WARNING, logger="autodoc.spec_from_policy")
    spec = derive_spec(["not", "a", "dict"], "mdd")  # type: ignore[arg-type]
    assert spec == _load_canonical("mdd")
    assert caplog.records


def test_fallback_when_keys_present_but_empty(caplog):
    caplog.set_level(logging.WARNING, logger="autodoc.spec_from_policy")
    spec = derive_spec({"required_artifacts": [], "sections": []}, "mdd")
    assert spec == _load_canonical("mdd")
    assert any("no usable section names" in rec.message for rec in caplog.records)


def test_fallback_never_raises_on_malformed_policy():
    # Ints, strings, wrong types under required_artifacts are filtered out,
    # not raised.
    policy_def = {"required_artifacts": [1, None, {}, "ok"], "sections": "not_a_list"}
    spec = derive_spec(policy_def, "mdd")
    assert spec["sections"] == ["ok"]


# ---------------------------------------------------------------------------
# Domino policy shape (what Portal actually serializes into the context file)
# ---------------------------------------------------------------------------

# Mirrors the YAML schema documented in MRM-Portal's shared/policy_prompts.py
# and the value Portal's routes/autodoc.py writes verbatim into the context
# file (raw response from GET /api/governance/v1/policies/{id}/definition).
_DOMINO_POLICY = {
    "id": "pol-1",
    "version": "v1.0",
    "stages": [
        {
            "policyEntityId": "11111111-1111-1111-1111-111111111111",
            "name": "Intake",
            "evidenceSet": [
                {
                    "id": "Local.intake-evidence",
                    "name": "Intake Evidence",
                    "definition": [
                        {
                            "policyEntityId": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                            "artifactType": "textinput",
                            "details": {"label": "Model Card"},
                        },
                        {
                            "policyEntityId": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                            "artifactType": "text",
                            "details": {"text": "Section guidance — should be skipped."},
                        },
                    ],
                }
            ],
            "approvals": [
                {
                    "policyEntityId": "22222222-2222-2222-2222-222222222222",
                    "name": "Intake Sign Off",
                    "evidence": {
                        "id": "Local.intake-signoff",
                        "definition": [
                            {
                                "artifactType": "radio",
                                "details": {"label": "Approve Intake?"},
                            }
                        ],
                    },
                }
            ],
        },
        {
            "policyEntityId": "33333333-3333-3333-3333-333333333333",
            "name": "Validation",
            "evidenceSet": [
                {
                    "name": "Validation Evidence",
                    # Computed-policy shape uses "artifacts", not "definition".
                    "artifacts": [
                        {
                            "artifactType": "textarea",
                            "details": {"label": "Risk Assessment"},
                        },
                    ],
                }
            ],
        },
    ],
}


@pytest.mark.parametrize("doc_type", VALID_DOC_TYPES)
def test_derive_walks_domino_policy_stage_names_first(doc_type):
    spec = derive_spec(_DOMINO_POLICY, doc_type)
    # Stage names lead the outline; artifact labels follow.
    assert spec["sections"][:2] == ["Intake", "Validation"]
    # Both definition[] (raw) and artifacts[] (computed) shapes contribute.
    assert "Model Card" in spec["sections"]
    assert "Risk Assessment" in spec["sections"]
    # Approval sign-off questions are excluded — not documentation sections.
    assert "Approve Intake?" not in spec["sections"]
    # Guidance entries (artifactType=text) are skipped — no label attribute.
    assert all("Section guidance" not in s for s in spec["sections"])


def test_derive_unwraps_definition_envelope_dict():
    wrapped = {"definition": _DOMINO_POLICY}
    spec = derive_spec(wrapped, "mdd")
    assert "Intake" in spec["sections"]
    assert "Model Card" in spec["sections"]


def test_derive_unwraps_definition_envelope_yaml_string():
    wrapped = {"definition": yaml.safe_dump(_DOMINO_POLICY)}
    spec = derive_spec(wrapped, "mdd")
    assert "Intake" in spec["sections"]
    assert "Model Card" in spec["sections"]


def test_derive_handles_unparseable_yaml_string_envelope(caplog):
    caplog.set_level(logging.WARNING, logger="autodoc.spec_from_policy")
    spec = derive_spec({"definition": "::: not: valid: : yaml :::"}, "mdd")
    assert spec == _load_canonical("mdd")


def test_derive_falls_back_when_stages_list_is_empty(caplog):
    caplog.set_level(logging.WARNING, logger="autodoc.spec_from_policy")
    spec = derive_spec({"id": "p1", "version": "1.0", "stages": []}, "mdd")
    assert spec == _load_canonical("mdd")
    assert any("no usable stages" in rec.message for rec in caplog.records)


def test_derive_skips_malformed_stages_and_artifacts():
    policy = {
        "stages": [
            "not a dict",
            {"name": "OK Stage", "evidenceSet": "not a list"},
            {
                "name": "",  # blank stage name dropped
                "evidenceSet": [
                    {"definition": [{"artifactType": "textinput", "details": "not a dict"}]},
                    {"definition": [{"artifactType": "textinput"}]},  # missing details
                    {"definition": [{"artifactType": "textinput", "details": {"label": "Kept"}}]},
                ],
            },
        ]
    }
    spec = derive_spec(policy, "mdd")
    assert spec["sections"] == ["OK Stage", "Kept"]


def test_derive_dedupes_across_stages_and_evidence():
    policy = {
        "stages": [
            {
                "name": "Same",
                "evidenceSet": [
                    {"definition": [{"artifactType": "textinput", "details": {"label": "Doc"}}]},
                ],
            },
            {
                "name": "Same",  # duplicate stage name
                "evidenceSet": [
                    {"artifacts": [{"artifactType": "textinput", "details": {"label": "Doc"}}]},
                ],
            },
        ]
    }
    spec = derive_spec(policy, "mdd")
    assert spec["sections"] == ["Same", "Doc"]


# ---------------------------------------------------------------------------
# Shape matches canonical template
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("doc_type", VALID_DOC_TYPES)
def test_derived_spec_has_same_top_level_keys_as_canonical(doc_type):
    policy_def = {"required_artifacts": ["A"], "sections": ["B"]}
    spec = derive_spec(policy_def, doc_type)
    canonical = _load_canonical(doc_type)
    assert set(spec.keys()) == set(canonical.keys())
    assert set(canonical.keys()) >= _CANONICAL_TOP_LEVEL_KEYS


# ---------------------------------------------------------------------------
# Invalid doc_type is the only condition that raises
# ---------------------------------------------------------------------------

def test_invalid_doc_type_raises():
    with pytest.raises(ValueError, match="Unknown doc_type"):
        derive_spec({"sections": ["X"]}, "bogus")


# ---------------------------------------------------------------------------
# CLI integration: --derive-spec --context-file logs derived spec at DEBUG
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main as cli_main  # noqa: E402


def _write_ctx(tmp_path: Path) -> Path:
    p = tmp_path / "ctx.json"
    p.write_text(
        json.dumps(
            {
                "bundle_id": "b-1",
                "policy_version_id": "pv-1",
                "bundle": {"owner": "Alice"},
                "policy_def": {
                    "required_artifacts": ["Model Card"],
                    "sections": ["Intended Use"],
                },
            }
        ),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def patched_pipeline():
    settings = MagicMock()
    settings.code_root = Path("/nonexistent/code")
    settings.get_api_key.return_value = "fake-key"
    settings.get_model_name.return_value = "gpt-4"
    settings.llm_provider = "openai"
    settings.openai_base_url = None
    settings.llm_max_retries = 3
    settings.llm_initial_backoff = 1.0
    settings.llm_max_backoff = 10.0
    settings.llm_backoff_jitter = 0.1
    settings.max_files = 50
    settings.parallel_workers = 4
    settings.planning_workers = 4
    settings.max_file_size = 15000
    settings.exclude_patterns = None
    settings.max_selected_files = 15
    settings.batch_size = 4
    settings.analysis_timeout = 90.0
    settings.scan_retries = 2
    settings.scan_workers = 2
    settings.mlflow_tracking_uri = None

    layout = MagicMock()
    layout.docs_dir = Path("output")

    with patch.object(cli_main, "_init_cli_dataset_store"), \
         patch("artifact_layout.init_layout"), \
         patch("artifact_layout.get_layout", return_value=layout), \
         patch.object(cli_main, "Settings", return_value=settings), \
         patch.object(cli_main, "LLMClient"), \
         patch.object(cli_main, "ContentSanitizer"), \
         patch.object(cli_main, "Orchestrator") as Orchestrator_m, \
         patch.object(cli_main.asyncio, "run",
                      return_value="output/model_docs_1.docx"):
        yield {"Orchestrator": Orchestrator_m}


def test_cli_derive_spec_logs_at_debug(tmp_path, patched_pipeline, caplog):
    ctx = _write_ctx(tmp_path)
    runner = CliRunner()

    with caplog.at_level(logging.DEBUG, logger="autodoc.spec_from_policy"):
        result = runner.invoke(
            cli_main.main,
            [
                "--bundle-id", "b-1",
                "--policy-version-id", "pv-1",
                "--context-file", str(ctx),
                "--derive-spec", "mdd",
                "--verbose",
            ],
        )

    assert result.exit_code == 0, result.output
    debug_logs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("derived spec" in r.message for r in debug_logs), (
        f"expected DEBUG log of derived spec, got: "
        f"{[(r.levelname, r.message) for r in caplog.records]}"
    )
    # Orchestrator received a DocumentSpec built from the derived dict.
    kwargs = patched_pipeline["Orchestrator"].call_args.kwargs
    assert kwargs["bundle_context"]["policy_def"]["sections"] == ["Intended Use"]


def test_cli_derive_spec_without_context_file_rejected(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli_main.main, ["--derive-spec", "mdd"])
    assert result.exit_code == 2
    flat = " ".join(result.output.split())
    assert "--derive-spec requires --context-file" in flat


def test_cli_requires_spec_or_derive_spec():
    runner = CliRunner()
    result = runner.invoke(cli_main.main, [])
    assert result.exit_code == 2
    flat = " ".join(result.output.split())
    assert "--derive-spec" in flat and "required" in flat
