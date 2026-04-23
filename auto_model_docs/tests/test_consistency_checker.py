"""Tests for U18 consistency checker + Findings appendix wiring."""

from __future__ import annotations

import asyncio
from pathlib import Path


def _run(coro):
    """Run ``coro`` in a fresh event loop without poisoning the default loop.

    ``asyncio.run`` closes its loop and leaves the default loop slot empty,
    which breaks later tests that still call ``asyncio.get_event_loop()``.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())

import pytest
from docx import Document

from autodoc.consistency_checker import (
    BUNDLE_FIELDS,
    Finding,
    check_declared_vs_detected,
    detect_gaps,
)
from autodoc.core.models import (
    ContentType,
    DocumentSpec,
    GeneratedContent,
    SectionPlan,
    SectionResult,
    SectionSpec,
)
from autodoc.generation.builder import DocumentBuilder


# ---------------------------------------------------------------------------
# detect_gaps
# ---------------------------------------------------------------------------

def _spec(*names: str) -> DocumentSpec:
    return DocumentSpec(
        title="Test",
        sections=[SectionSpec(name=n) for n in names],
    )


def test_detect_gaps_empty_spec_emits_no_findings():
    # Pydantic enforces min_length=1 on sections, so "empty" means "no gaps":
    # one section that is fully populated.
    spec = _spec("Executive Summary")
    generated = {"Executive Summary": "X" * 200}

    assert detect_gaps(spec, generated) == []


def test_detect_gaps_flags_all_missing_sections():
    spec = _spec("Executive Summary", "Data Overview", "Model Performance")
    findings = detect_gaps(spec, {})

    assert len(findings) == 3
    assert {f.section for f in findings} == {
        "Executive Summary",
        "Data Overview",
        "Model Performance",
    }
    assert all(f.category == "missing_section" for f in findings)
    assert all(f.severity == "S2" for f in findings)


def test_detect_gaps_flags_thin_content():
    spec = _spec("Executive Summary", "Data Overview")
    findings = detect_gaps(
        spec,
        {
            "Executive Summary": "too short",
            "Data Overview": "Y" * 150,
        },
    )

    assert len(findings) == 1
    assert findings[0].section == "Executive Summary"
    assert findings[0].category == "thin_section"


def test_detect_gaps_clean_match_emits_nothing():
    spec = _spec("Executive Summary", "Data Overview")
    findings = detect_gaps(
        spec,
        {
            "Executive Summary": "A" * 200,
            "Data Overview": "B" * 200,
        },
    )

    assert findings == []


def test_detect_gaps_whitespace_only_content_is_thin():
    spec = _spec("Executive Summary")
    findings = detect_gaps(spec, {"Executive Summary": "   \n\n   "})

    assert len(findings) == 1
    assert findings[0].category == "thin_section"


# ---------------------------------------------------------------------------
# check_declared_vs_detected
# ---------------------------------------------------------------------------

def test_declared_vs_detected_none_inputs_are_safe():
    assert check_declared_vs_detected(None, None) == []
    assert check_declared_vs_detected({"bundle": {}}, None) == []
    assert check_declared_vs_detected(None, {"owner": "Alice"}) == []


def test_declared_vs_detected_clean_match_emits_nothing():
    bundle_context = {
        "bundle": {
            "owner": "Alice",
            "risk_tier": "High",
            "intended_use": "Credit scoring",
        }
    }
    scan_result = {
        "owner": "Alice",
        "risk_tier": "High",
        "intended_use": "Credit scoring",
    }

    assert check_declared_vs_detected(bundle_context, scan_result) == []


def test_declared_vs_detected_mismatch_per_bundle_field():
    bundle_context = {
        "bundle": {
            "owner": "Alice",
            "risk_tier": "High",
            "intended_use": "Credit scoring",
        }
    }
    scan_result = {
        "owner": "Bob",
        "risk_tier": "Low",
        "intended_use": "Fraud detection",
    }

    findings = check_declared_vs_detected(bundle_context, scan_result)

    assert len(findings) == len(BUNDLE_FIELDS)
    by_field = {f.section: f for f in findings}
    assert by_field["owner"].declared == "Alice"
    assert by_field["owner"].detected == "Bob"
    assert by_field["risk_tier"].declared == "High"
    assert by_field["intended_use"].detected == "Fraud detection"
    assert all(f.category == "declared_vs_detected" for f in findings)
    assert all(f.severity == "S2" for f in findings)


def test_declared_vs_detected_missing_value_on_either_side_is_silent():
    bundle_context = {"bundle": {"owner": "Alice"}}
    scan_result = {"risk_tier": "High"}

    # Declared-only and detected-only fields produce no mismatches.
    assert check_declared_vs_detected(bundle_context, scan_result) == []


def test_declared_vs_detected_is_case_and_whitespace_insensitive():
    bundle_context = {"bundle": {"owner": "Alice Chen"}}
    scan_result = {"owner": "  alice   chen  "}

    assert check_declared_vs_detected(bundle_context, scan_result) == []


# ---------------------------------------------------------------------------
# Builder wiring — feature flag on/off
# ---------------------------------------------------------------------------

def _make_section_result(name: str, text: str, number: str = "1") -> SectionResult:
    return SectionResult(
        plan=SectionPlan(number=number, name=name, title=name),
        contents=[GeneratedContent(block_type=ContentType.NARRATIVE, content=text)],
    )


def _build_doc(tmp_path, monkeypatch, *, flag: str | None, **builder_kwargs) -> Path:
    """Build a .docx with a stubbed ``_save_document`` so tests stay offline."""
    out_path = tmp_path / "out.docx"

    def _save(self, doc):  # noqa: ANN001
        doc.save(str(out_path))
        return str(out_path)

    monkeypatch.setattr(DocumentBuilder, "_save_document", _save)
    if flag is None:
        monkeypatch.delenv("AUTODOC_AUTO_FINDINGS", raising=False)
    else:
        monkeypatch.setenv("AUTODOC_AUTO_FINDINGS", flag)

    spec = DocumentSpec(
        title="Test doc",
        sections=[
            SectionSpec(name="Executive Summary"),
            SectionSpec(name="Data Overview"),
        ],
    )
    # Executive Summary is intentionally thin (<100 chars) to trigger a finding.
    results = [
        _make_section_result("Executive Summary", "short.", number="1"),
        _make_section_result("Data Overview", "B" * 200, number="2"),
    ]

    builder = DocumentBuilder(output_dir=str(tmp_path), **builder_kwargs)
    _run(builder.build(spec, results))
    return out_path


def _docx_text(path: Path) -> str:
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs)


def test_flag_off_produces_no_findings_heading(tmp_path, monkeypatch):
    out = _build_doc(tmp_path, monkeypatch, flag=None)

    assert "Findings" not in _docx_text(out)


def test_flag_on_with_gap_fixture_adds_findings_heading(tmp_path, monkeypatch):
    out = _build_doc(tmp_path, monkeypatch, flag="true")

    text = _docx_text(out)
    assert "Findings" in text
    # The thin section is named in the body of the finding.
    assert "Executive Summary" in text


def test_flag_on_with_mismatch_fixture_adds_findings_heading(tmp_path, monkeypatch):
    out = _build_doc(
        tmp_path,
        monkeypatch,
        flag="1",
        bundle_context={"bundle": {"owner": "Alice", "risk_tier": "High"}},
        scan_result={"owner": "Bob", "risk_tier": "High"},
    )

    text = _docx_text(out)
    assert "Findings" in text
    assert "owner" in text
    assert "Alice" in text and "Bob" in text


def test_flag_on_clean_doc_does_not_emit_findings_heading(tmp_path, monkeypatch):
    monkeypatch.setattr(
        DocumentBuilder,
        "_save_document",
        lambda self, doc: (doc.save(str(tmp_path / "clean.docx")) or str(tmp_path / "clean.docx")),
    )
    monkeypatch.setenv("AUTODOC_AUTO_FINDINGS", "true")

    spec = DocumentSpec(
        title="Clean",
        sections=[SectionSpec(name="Executive Summary")],
    )
    results = [_make_section_result("Executive Summary", "A" * 300)]

    builder = DocumentBuilder(output_dir=str(tmp_path))
    _run(builder.build(spec, results))

    text = _docx_text(tmp_path / "clean.docx")
    assert "Findings" not in text


@pytest.mark.parametrize("flag_value", ["0", "false", "no", "", "  "])
def test_flag_recognises_only_truthy_values(tmp_path, monkeypatch, flag_value):
    out = _build_doc(tmp_path, monkeypatch, flag=flag_value)

    assert "Findings" not in _docx_text(out)
