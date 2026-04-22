"""U8 LLM metadata-citation eval (gates MVP).

Pipeline test: generates an MDD with bundle context = Alice Chen / High /
Credit risk scoring and asserts the .docx cites all three facts verbatim in
the right sections.

Two modes:
  - Mocked (default): CLI runs end-to-end with a fake LLM that returns crafted
    responses. Must pass in CI without any API key.
  - Real LLM (opt-in via `-m llm`): uses the real LLMClient against the
    provider configured by env. Skipped when ANTHROPIC_API_KEY is unset.

The full pipeline runs: Orchestrator → SectionPlanner → ContentGenerator →
DocumentBuilder → python-docx .docx written to an in-memory DatasetStore.
Only SCAN (code + MLflow) is stubbed since it is orthogonal to citation.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner
from docx import Document

# main.py lives next to tests/ at the repo root; import it as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import artifact_layout  # noqa: E402
import dataset_store  # noqa: E402
import main as cli_main  # noqa: E402
from autodoc.core.models import ArtifactContext, CodeContext  # noqa: E402
from autodoc.llm.client import LLMResponse  # noqa: E402


FIXTURES = Path(__file__).parent / "fixtures"
BUNDLE_FIXTURE = FIXTURES / "bundle_alice_chen.json"
SPEC_FIXTURE = FIXTURES / "spec_mdd_minimal.yaml"

OWNER_STRING = "Alice Chen"
RISK_STRING = "High"
INTENDED_USE_SUBSTRING = "Credit risk scoring"


# ---------------------------------------------------------------------------
# Fakes / stubs
# ---------------------------------------------------------------------------


class _MemStore:
    """In-memory DatasetStore standing in for Domino's dataset I/O."""

    dataset_id = "ds-test"
    snapshot_id = "snap-test"

    def __init__(self) -> None:
        self._files: Dict[str, bytes] = {}

    def write_file(self, path: str, content: bytes) -> None:
        self._files[path] = content

    def read_file(self, path: str) -> bytes:
        if path not in self._files:
            raise FileNotFoundError(path)
        return self._files[path]

    def file_exists(self, path: str) -> bool:
        return path in self._files

    def list_files(self, path: str = "") -> list:
        return []

    def find_docx(self) -> bytes:
        for name, data in self._files.items():
            if name.endswith(".docx"):
                return data
        raise AssertionError(
            f"No .docx written to store. Files present: {list(self._files.keys())}"
        )


class _FakeLLMClient:
    """Deterministic LLM stand-in. Reads the section name from the prompt
    and emits a narrative citing the bundle fact for that section. The
    bundle fields are provided at construction time (closure) rather than
    re-parsed from the prompt, to keep the fake decoupled from prompt
    formatting churn.

    Matches LLMClient's public surface used by the pipeline:
      - ``complete(prompt, ...) -> LLMResponse``
      - ``complete_json(prompt, schema=..., system=...) -> dict``
    """

    def __init__(self, bundle: dict) -> None:
        self.provider = "anthropic"
        self.model = "fake-model"
        self._owner = bundle.get("owner", "")
        self._risk = (
            bundle.get("risk_tier") or bundle.get("classificationValue") or ""
        )
        self._intended = bundle.get("intended_use", "")

    async def complete_json(
        self,
        prompt: str,
        schema: Optional[dict] = None,
        system: Optional[str] = None,
        **kwargs,
    ) -> dict:
        section_name = _extract_section_name_from_prompt(prompt) or "Section"
        if schema and "content_blocks" in (schema.get("properties") or {}):
            return {
                "section_title": section_name,
                "content_blocks": [
                    {
                        "type": "narrative",
                        "purpose": f"Document {section_name} from the bundle.",
                        "data_needed": "Bundle facts",
                        "specifics": {},
                    }
                ],
            }
        return {}

    async def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        section = (_extract_section_name_from_prompt(prompt) or "").lower()
        if "owner" in section or "ownership" in section:
            text = (
                f"The model owner of record is {self._owner}. Accountability "
                f"for the model lifecycle rests with {self._owner}, per the "
                "governance bundle."
            )
        elif "risk" in section or "classification" in section:
            text = (
                f"The risk classification for this model is {self._risk}. "
                "This tier was assigned in the governance bundle and drives "
                "validation cadence."
            )
        elif "intended" in section or "purpose" in section or "use" in section:
            text = (
                f"Intended use, per the governance bundle: {self._intended} "
                "The model is not authorized outside this scope."
            )
        else:
            text = "This section describes supporting context for the model."
        return LLMResponse(
            content=text, input_tokens=10, output_tokens=20, model=self.model
        )


# ---------------------------------------------------------------------------
# Prompt introspection helpers for the fake LLM
# ---------------------------------------------------------------------------


_SECTION_HEADER_RX = re.compile(r"##\s*Section:\s*(.+)")


def _extract_section_name_from_prompt(prompt: str) -> Optional[str]:
    """Pick the section name out of the shared prompt format.

    build_narrative_prompt / build_section_planning_prompt both include a line
    like ``## Section: Model Ownership`` or ``## Section Name: ...``.
    """
    m = _SECTION_HEADER_RX.search(prompt)
    if m:
        return m.group(1).strip()
    # Planner prompt format: "Section: <name>" (no ##)
    m2 = re.search(r"Section(?:\s*Name)?:\s*([^\n]+)", prompt)
    return m2.group(1).strip() if m2 else None


# ---------------------------------------------------------------------------
# Shared fixture: prepare env, layout, in-memory store
# ---------------------------------------------------------------------------


@pytest.fixture
def mem_store():
    """Swap DatasetStore for an in-memory store and reset after test."""
    artifact_layout.init_layout()
    store = _MemStore()
    dataset_store._store = store
    try:
        yield store
    finally:
        dataset_store.reset_store()
        artifact_layout.reset_layout()
        # CLI path calls asyncio.run() which leaves the default policy with
        # _set_called=True. Other suites using asyncio.get_event_loop() then
        # fail with "There is no current event loop". Reset to a clean policy.
        asyncio.set_event_loop_policy(None)


@pytest.fixture
def bundle_context_path(tmp_path) -> Path:
    """Copy the Alice Chen bundle fixture into a tmp dir so the CLI's cleanup
    (which deletes the context file on exit) doesn't touch the fixture."""
    payload = json.loads(BUNDLE_FIXTURE.read_text())
    ctx_path = tmp_path / "ctx_alice.json"
    ctx_path.write_text(json.dumps(payload))
    return ctx_path


@pytest.fixture
def empty_code_root(tmp_path) -> Path:
    root = tmp_path / "code"
    root.mkdir()
    return root


# ---------------------------------------------------------------------------
# Test — mocked mode (runs in CI without any API key)
# ---------------------------------------------------------------------------


def _run_cli(
    *,
    spec_path: Path,
    ctx_path: Path,
    code_root: Path,
    env: dict,
    llm_factory,
) -> tuple[int, str]:
    """Invoke the autodoc CLI end-to-end with scanners stubbed and the
    LLMClient replaced by ``llm_factory``.
    """
    # Stub ArtifactScanner and CodeScanner so the pipeline doesn't try to
    # read MLflow or scan the filesystem — both orthogonal to citation.
    code_scanner_instance = MagicMock()
    code_scanner_instance.scan = AsyncMock(return_value=CodeContext())
    artifact_scanner_instance = MagicMock()
    artifact_scanner_instance.scan = AsyncMock(return_value=ArtifactContext())

    runner = CliRunner(env=env)

    with patch.object(cli_main, "_init_cli_dataset_store"), \
         patch.object(cli_main, "LLMClient", side_effect=llm_factory), \
         patch(
            "autodoc.orchestrator.CodeScanner",
            return_value=code_scanner_instance,
         ), \
         patch(
            "autodoc.orchestrator.ArtifactScanner",
            return_value=artifact_scanner_instance,
         ):
        result = runner.invoke(
            cli_main.main,
            [
                "--spec", str(spec_path),
                "--code-root", str(code_root),
                "--provider", "anthropic",
                "--max-files", "1",
                "--generation-workers", "1",
                "--planning-workers", "1",
                "--bundle-id", "b-alice",
                "--policy-version-id", "pv-1",
                "--context-file", str(ctx_path),
            ],
            catch_exceptions=False,
        )
    return result.exit_code, result.output


def _read_docx_paragraphs(docx_bytes: bytes) -> List[str]:
    doc = Document(io.BytesIO(docx_bytes))
    paragraphs: List[str] = []
    for p in doc.paragraphs:
        text = re.sub(r"\s+", " ", p.text).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def _assert_fact_in_section(
    paragraphs: List[str],
    *,
    section_keywords: List[str],
    required_substring: str,
) -> None:
    """Assert ``required_substring`` appears within the body of a section
    whose heading matches any of ``section_keywords`` (case-insensitive).

    Section boundaries are implicit: we match the first heading paragraph
    containing any of the keywords, then scan subsequent paragraphs until
    the next heading-like paragraph (another section name from the spec).
    If we cannot identify the heading, we fall back to a whole-document
    verbatim check — the verbatim substring MUST be present somewhere.
    """
    kw_rx = re.compile(r"|".join(re.escape(k) for k in section_keywords), re.IGNORECASE)

    # Find heading paragraph index.
    heading_idx = None
    for i, p in enumerate(paragraphs):
        if len(p) < 80 and kw_rx.search(p):
            heading_idx = i
            break

    joined = " \n ".join(paragraphs)
    assert required_substring in joined, (
        f"Expected '{required_substring}' somewhere in the generated .docx. "
        f"Paragraphs: {paragraphs[:25]}"
    )

    if heading_idx is None:
        return

    # Find next section heading (short paragraph not matching our keywords).
    next_heading_idx = len(paragraphs)
    for j in range(heading_idx + 1, len(paragraphs)):
        p = paragraphs[j]
        if len(p) < 80 and re.match(r"^[A-Z][A-Za-z ,:/&()-]{2,60}$", p):
            # Heading-like line that is not our current heading
            if not kw_rx.search(p):
                next_heading_idx = j
                break

    section_body = " \n ".join(paragraphs[heading_idx:next_heading_idx])
    assert required_substring in section_body, (
        f"Expected '{required_substring}' in the "
        f"{'/'.join(section_keywords)} section. Section body: {section_body[:400]}"
    )


def test_metadata_citation_mocked(
    mem_store, bundle_context_path, empty_code_root, monkeypatch
):
    """CI gate: pipeline cites bundle facts without any external LLM call."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-for-mocked-mode")
    # Keep any ambient OPENAI_API_KEY / local .env from flipping provider.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    bundle = json.loads(BUNDLE_FIXTURE.read_text())["bundle"]

    def fake_factory(*_args, **_kwargs):
        return _FakeLLMClient(bundle)

    exit_code, output = _run_cli(
        spec_path=SPEC_FIXTURE,
        ctx_path=bundle_context_path,
        code_root=empty_code_root,
        env={"ANTHROPIC_API_KEY": "dummy-for-mocked-mode"},
        llm_factory=fake_factory,
    )
    assert exit_code == 0, f"CLI failed: {output}"

    docx_bytes = mem_store.find_docx()
    paragraphs = _read_docx_paragraphs(docx_bytes)

    assert paragraphs, "Generated .docx had no non-empty paragraphs."

    _assert_fact_in_section(
        paragraphs,
        section_keywords=["Model Ownership", "Owner"],
        required_substring=OWNER_STRING,
    )
    _assert_fact_in_section(
        paragraphs,
        section_keywords=["Risk Classification", "Classification"],
        required_substring=RISK_STRING,
    )
    _assert_fact_in_section(
        paragraphs,
        section_keywords=["Intended Use", "Intended"],
        required_substring=INTENDED_USE_SUBSTRING,
    )


# ---------------------------------------------------------------------------
# Test — real-LLM mode (opt-in via `-m llm`)
# ---------------------------------------------------------------------------


@pytest.mark.llm
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY", "").startswith("sk-ant-"),
    reason=(
        "Real ANTHROPIC_API_KEY not set (expected prefix 'sk-ant-'); "
        "skipping real-LLM eval. CI sets a dummy value for unit tests; "
        "this test requires a genuine key."
    ),
)
def test_metadata_citation_real_llm(
    mem_store, bundle_context_path, empty_code_root
):
    """Smoke-tests the same pipeline against a real LLM. Asserts the three
    facts are cited verbatim somewhere in the generated .docx.

    Does NOT gate CI — the `-m llm` marker means this only runs when the
    caller explicitly opts in. Model quality drift is acceptable; wiring
    regressions are not.
    """
    # Real LLMClient, real prompts, real API calls.
    exit_code, output = _run_cli(
        spec_path=SPEC_FIXTURE,
        ctx_path=bundle_context_path,
        code_root=empty_code_root,
        env={
            "ANTHROPIC_API_KEY": os.environ["ANTHROPIC_API_KEY"],
            # Small, fast model keeps the eval cheap; can be overridden per-run.
            "AUTODOC_LLM_MODEL": os.environ.get(
                "AUTODOC_LLM_MODEL", "claude-haiku-4-5-20251001"
            ),
        },
        llm_factory=cli_main.LLMClient,
    )
    assert exit_code == 0, f"CLI failed: {output}"

    docx_bytes = mem_store.find_docx()
    paragraphs = _read_docx_paragraphs(docx_bytes)
    joined = " \n ".join(paragraphs)

    assert OWNER_STRING in joined, (
        f"Expected '{OWNER_STRING}' verbatim. Paragraphs: {paragraphs[:25]}"
    )
    assert RISK_STRING in joined, (
        f"Expected '{RISK_STRING}' verbatim. Paragraphs: {paragraphs[:25]}"
    )
    assert INTENDED_USE_SUBSTRING in joined, (
        f"Expected '{INTENDED_USE_SUBSTRING}' substring. "
        f"Paragraphs: {paragraphs[:25]}"
    )
