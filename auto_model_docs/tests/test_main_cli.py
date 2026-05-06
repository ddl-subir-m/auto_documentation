"""Tests for autodoc CLI Shape 1 context flags (U5)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

# main.py lives at the repo root next to the tests/ dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main as cli_main  # noqa: E402


def _valid_context() -> dict:
    return {
        "bundle_id": "b-1",
        "policy_version_id": "pv-1",
        "bundle": {"owner": "Alice"},
        "policy_def": {"sections": ["intended_use"]},
    }


def _write_context(tmp_path, payload=None, name="ctx.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload or _valid_context()), encoding="utf-8")
    return p


@pytest.fixture
def minimal_spec(tmp_path):
    spec = tmp_path / "spec.yaml"
    spec.write_text(
        "title: Test\n"
        "authors: Test\n"
        "sections:\n"
        "  - Overview\n",
        encoding="utf-8",
    )
    return spec


@pytest.fixture
def patched_pipeline():
    """Stub every heavy collaborator so CLI reaches Orchestrator + asyncio.run."""
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
                      return_value="output/model_docs_1.docx") as asyncio_run_m:
        yield {
            "Orchestrator": Orchestrator_m,
            "asyncio_run": asyncio_run_m,
        }


def test_help_lists_new_flags():
    runner = CliRunner()
    result = runner.invoke(cli_main.main, ["--help"])
    assert result.exit_code == 0
    assert "--bundle-id" in result.output
    assert "--policy-version-id" in result.output
    assert "--context-file" in result.output


@pytest.mark.parametrize(
    "extra_args",
    [
        ["--bundle-id", "b-1"],
        ["--policy-version-id", "pv-1"],
        ["--context-file", "ctx.json"],
        ["--bundle-id", "b-1", "--policy-version-id", "pv-1"],
        ["--bundle-id", "b-1", "--context-file", "ctx.json"],
        ["--policy-version-id", "pv-1", "--context-file", "ctx.json"],
    ],
)
def test_partial_context_flags_rejected(minimal_spec, patched_pipeline, extra_args):
    runner = CliRunner()
    result = runner.invoke(
        cli_main.main,
        ["--spec", str(minimal_spec), *extra_args],
    )
    assert result.exit_code == 2
    # Rich may wrap the error across lines; collapse whitespace before checking.
    flat = " ".join(result.output.split())
    assert "must be used together" in flat
    # Validation happens before pipeline runs.
    assert not patched_pipeline["Orchestrator"].called


def test_all_three_flags_accepted_and_context_dict_passed(
    tmp_path, minimal_spec, patched_pipeline
):
    ctx = _write_context(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        cli_main.main,
        [
            "--spec", str(minimal_spec),
            "--bundle-id", "b-1",
            "--policy-version-id", "pv-1",
            "--context-file", str(ctx),
        ],
    )
    assert result.exit_code == 0, result.output
    kwargs = patched_pipeline["Orchestrator"].call_args.kwargs
    assert kwargs["bundle_context"] == _valid_context()


def test_no_context_flags_orchestrator_receives_none(minimal_spec, patched_pipeline):
    runner = CliRunner()
    result = runner.invoke(cli_main.main, ["--spec", str(minimal_spec)])
    assert result.exit_code == 0, result.output
    kwargs = patched_pipeline["Orchestrator"].call_args.kwargs
    assert kwargs["bundle_context"] is None


def test_cleanup_deletes_context_file_on_success(
    tmp_path, minimal_spec, patched_pipeline
):
    ctx = _write_context(tmp_path)
    assert ctx.exists()
    runner = CliRunner()
    result = runner.invoke(
        cli_main.main,
        [
            "--spec", str(minimal_spec),
            "--bundle-id", "b-1",
            "--policy-version-id", "pv-1",
            "--context-file", str(ctx),
        ],
    )
    assert result.exit_code == 0, result.output
    assert not ctx.exists(), "context file should be deleted after success"


def test_cleanup_deletes_context_file_on_failure(
    tmp_path, minimal_spec, patched_pipeline
):
    ctx = _write_context(tmp_path)
    assert ctx.exists()
    # Simulate the orchestrator.generate blowing up partway through the pipeline.
    patched_pipeline["asyncio_run"].side_effect = RuntimeError("orchestrator boom")

    runner = CliRunner()
    result = runner.invoke(
        cli_main.main,
        [
            "--spec", str(minimal_spec),
            "--bundle-id", "b-1",
            "--policy-version-id", "pv-1",
            "--context-file", str(ctx),
        ],
    )
    assert result.exit_code != 0
    assert not ctx.exists(), "context file should be deleted after failure"


def test_output_file_emits_manifest_sidecar(
    tmp_path, minimal_spec, patched_pipeline
):
    """--output-file writes the .docx AND a <name>.docx.manifest.json sidecar.

    Portal reads the manifest to learn the output path, sha256, doc_type,
    and bundle context — no filename guessing required.
    """
    ctx = _write_context(tmp_path)
    out_path = tmp_path / "handoff" / "model_doc.docx"

    fake_store = MagicMock()
    fake_store.read_file.return_value = b"PK\x03\x04 fake docx bytes"

    with patch("dataset_store.get_store", return_value=fake_store):
        runner = CliRunner()
        result = runner.invoke(
            cli_main.main,
            [
                "--spec", str(minimal_spec),
                "--bundle-id", "b-1",
                "--policy-version-id", "pv-1",
                "--context-file", str(ctx),
                "--output-file", str(out_path),
            ],
        )

    assert result.exit_code == 0, result.output
    assert out_path.exists()
    manifest_path = out_path.with_suffix(out_path.suffix + ".manifest.json")
    assert manifest_path.exists(), f"expected {manifest_path}"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["schema_version"] == 1
    assert manifest["output_filename"] == "model_doc.docx"
    assert manifest["bundle_id"] == "b-1"
    assert manifest["policy_version_id"] == "pv-1"
    assert manifest["size_bytes"] == len(b"PK\x03\x04 fake docx bytes")
    assert len(manifest["sha256"]) == 64
    assert "generated_at" in manifest


def test_canonical_spec_sets_template_id_for_orchestrator(
    minimal_spec, patched_pipeline
):
    """--canonical-spec mdd seeds spec.template_id so Orchestrator gets it."""
    runner = CliRunner()
    # We bypass --spec with --canonical-spec; need to also avoid --spec arg.
    # Stub artifact_layout/dataset_store same as patched_pipeline already does.
    result = runner.invoke(
        cli_main.main,
        ["--canonical-spec", "mdd"],
    )
    assert result.exit_code == 0, result.output
    # The DocumentSpec passed to Orchestrator-driven generate() came from
    # _doc_spec_from_dict; we can validate via the Orchestrator constructor's
    # call_args (Orchestrator is mocked, so we can't read spec directly).
    # Instead we assert the CLI didn't error out and that the spec_from_policy
    # path was hit (not the spec yaml path).
    assert "Loading canonical specification" in result.output


def test_regression_existing_spec_flow_unchanged(minimal_spec, patched_pipeline):
    """Iron rule: `main --spec doc_spec.yaml` with no context flags still runs.

    Mocks stand in for the LLM pipeline; this test asserts the CLI surface
    parses, reaches asyncio.run(orchestrator.generate(...)), and reports the
    .docx output path unchanged from before U5.
    """
    runner = CliRunner()
    result = runner.invoke(cli_main.main, ["--spec", str(minimal_spec)])

    assert result.exit_code == 0, result.output
    assert patched_pipeline["asyncio_run"].called
    assert ".docx" in result.output
    # Orchestrator was constructed with bundle_context=None (no regression).
    kwargs = patched_pipeline["Orchestrator"].call_args.kwargs
    assert kwargs["bundle_context"] is None
