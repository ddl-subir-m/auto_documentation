"""Tests for autodoc/orchestrator.py — Orchestrator class."""

import asyncio
import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import artifact_layout
import dataset_store

from autodoc.core.models import (
    ArtifactContext,
    CodeContext,
    ContentBlock,
    ContentType,
    DocumentSpec,
    GeneratedContent,
    GenerationContext,
    LanguageProfile,
    ModelInfo,
    PYTHON_PROFILE,
    R_PROFILE,
    SAS_PROFILE,
    SectionPlan,
    SectionResult,
    SectionSpec,
)
from autodoc.orchestrator import Orchestrator


# ---------------------------------------------------------------------------
# In-memory DatasetStore for cache tests
# ---------------------------------------------------------------------------

class _MemStore:
    """Minimal in-memory DatasetStore for testing cache round-trips."""
    dataset_id = "ds-test"
    snapshot_id = "snap-test"

    def __init__(self):
        self._files: dict[str, bytes] = {}

    def write_file(self, path, content):
        self._files[path] = content

    def read_file(self, path):
        if path not in self._files:
            raise FileNotFoundError(path)
        return self._files[path]

    def file_exists(self, path):
        return path in self._files

    def list_files(self, path=""):
        return []


@pytest.fixture(autouse=True)
def _init_layout_and_store():
    """Ensure ArtifactLayout and DatasetStore are available for all tests."""
    artifact_layout.init_layout()
    mem = _MemStore()
    dataset_store._store = mem
    yield
    artifact_layout.reset_layout()
    dataset_store.reset_store()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_llm():
    llm = MagicMock()
    llm.complete_json = AsyncMock(return_value={})
    return llm


def _make_mock_sanitizer():
    return MagicMock()


def _make_spec(sections=None, title="Test Doc"):
    if sections is None:
        sections = [SectionSpec(name="Overview")]
    return DocumentSpec(title=title, authors="tester", sections=sections)


def _make_section_result(number="1", name="Overview", contents=None, errors=None):
    plan = SectionPlan(
        number=number,
        name=name,
        title=name,
        content_blocks=[
            ContentBlock(
                type=ContentType.NARRATIVE,
                purpose="describe",
                data_needed="code",
                specifics={},
                priority="required",
            )
        ],
    )
    if contents is None:
        contents = [
            GeneratedContent(
                block_type=ContentType.NARRATIVE,
                content="Some generated text.",
                metadata={"citation_ids": ["Code:train.py"]},
            )
        ]
    return SectionResult(plan=plan, contents=contents, errors=errors or [])


# ---------------------------------------------------------------------------
# Language detection & profile setup
# ---------------------------------------------------------------------------


class TestLanguageDetection:
    """Orchestrator should detect language and default to Python."""

    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language")
    def test_defaults_to_python_when_no_files(
        self, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs, mock_as
    ):
        mock_detect.return_value = (None, 0)
        orch = Orchestrator(
            llm=_make_mock_llm(),
            sanitizer=_make_mock_sanitizer(),
            code_root=Path("/tmp/empty"),
        )
        assert orch.language_profile is PYTHON_PROFILE
        assert orch.detected_file_count == 0

    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language")
    def test_uses_detected_profile(
        self, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs, mock_as
    ):
        mock_detect.return_value = (R_PROFILE, 12)
        orch = Orchestrator(
            llm=_make_mock_llm(),
            sanitizer=_make_mock_sanitizer(),
            code_root=Path("/tmp/r_project"),
        )
        assert orch.language_profile is R_PROFILE
        assert orch.detected_file_count == 12


# ---------------------------------------------------------------------------
# Sanitizer creation with language-specific patterns
# ---------------------------------------------------------------------------


class TestSanitizerCreation:
    """Sanitizer is rebuilt when the language has secret_patterns."""

    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language")
    @patch("autodoc.orchestrator.ContentSanitizer")
    def test_sanitizer_rebuilt_for_sas_patterns(
        self, mock_san_cls, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs, mock_as
    ):
        """SAS profile has regex secret_patterns, so Orchestrator should create a new sanitizer."""
        mock_detect.return_value = (SAS_PROFILE, 5)
        mock_san_cls.return_value = MagicMock()
        original_sanitizer = _make_mock_sanitizer()

        orch = Orchestrator(
            llm=_make_mock_llm(),
            sanitizer=original_sanitizer,
            code_root=Path("/tmp/sas"),
        )
        # SAS has regex patterns so a new sanitizer is created
        mock_san_cls.assert_called_once()
        assert orch.sanitizer is not original_sanitizer

    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language")
    def test_sanitizer_kept_for_python_no_extra_patterns(
        self, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs, mock_as
    ):
        """Python profile has no secret_patterns, so original sanitizer is kept."""
        mock_detect.return_value = (PYTHON_PROFILE, 10)
        original_sanitizer = _make_mock_sanitizer()

        orch = Orchestrator(
            llm=_make_mock_llm(),
            sanitizer=original_sanitizer,
            code_root=Path("/tmp/py"),
        )
        assert orch.sanitizer is original_sanitizer

    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language")
    @patch("autodoc.orchestrator.ContentSanitizer")
    def test_r_profile_uses_extra_sensitive_files(
        self, mock_san_cls, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs, mock_as
    ):
        """R profile has file-name secret_patterns (.Renviron, .Rprofile)."""
        mock_detect.return_value = (R_PROFILE, 3)
        mock_san_cls.return_value = MagicMock()

        orch = Orchestrator(
            llm=_make_mock_llm(),
            sanitizer=_make_mock_sanitizer(),
            code_root=Path("/tmp/r"),
        )
        # R has plain file-name patterns — passed as extra_sensitive_files
        call_kwargs = mock_san_cls.call_args[1]
        assert call_kwargs.get("extra_sensitive_files") is not None
        assert ".Renviron" in call_kwargs["extra_sensitive_files"]


# ---------------------------------------------------------------------------
# Progress callback invocation order
# ---------------------------------------------------------------------------


class TestProgressCallbacks:
    """The generate() pipeline fires callbacks in Scanning -> Planning -> Generating -> Building order."""

    @patch("autodoc.orchestrator.Orchestrator._save_results_cache")
    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language", return_value=(PYTHON_PROFILE, 3))
    def test_progress_phases_in_order(
        self, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs, mock_as,
        mock_save_cache,
    ):
        llm = _make_mock_llm()
        orch = Orchestrator(
            llm=llm,
            sanitizer=_make_mock_sanitizer(),
            code_root=Path("/tmp"),
        )

        # Mock scanner returns
        orch.code_scanner.scan = AsyncMock(return_value=CodeContext())
        orch.artifact_scanner.scan = AsyncMock(return_value=ArtifactContext())

        # Mock planner: return one plan
        plan = SectionPlan(number="1", name="Overview", title="Overview",
                           content_blocks=[ContentBlock(type=ContentType.NARRATIVE, purpose="desc")])
        orch.planner.plan_section = AsyncMock(return_value=plan)

        # Mock generator
        gen_content = GeneratedContent(block_type=ContentType.NARRATIVE, content="text")
        orch.generator.generate = AsyncMock(return_value=gen_content)

        # Mock builder
        orch.builder.build = AsyncMock(return_value=Path("/tmp/output.docx"))

        # Track progress calls
        progress_log = []

        def on_progress(phase, frac):
            progress_log.append((phase, frac))

        spec = _make_spec()
        asyncio.get_event_loop().run_until_complete(
            orch.generate(spec, on_progress=on_progress)
        )

        phases_seen = []
        for phase, _ in progress_log:
            if not phases_seen or phases_seen[-1] != phase:
                phases_seen.append(phase)

        assert phases_seen == ["Scanning", "Planning", "Generating", "Building"]

    @patch("autodoc.orchestrator.Orchestrator._save_results_cache")
    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language", return_value=(PYTHON_PROFILE, 3))
    def test_scanning_starts_at_zero(
        self, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs, mock_as,
        mock_save_cache,
    ):
        llm = _make_mock_llm()
        orch = Orchestrator(llm=llm, sanitizer=_make_mock_sanitizer(), code_root=Path("/tmp"))

        orch.code_scanner.scan = AsyncMock(return_value=CodeContext())
        orch.artifact_scanner.scan = AsyncMock(return_value=ArtifactContext())
        plan = SectionPlan(number="1", name="S", title="S",
                           content_blocks=[ContentBlock(type=ContentType.NARRATIVE, purpose="d")])
        orch.planner.plan_section = AsyncMock(return_value=plan)
        orch.generator.generate = AsyncMock(
            return_value=GeneratedContent(block_type=ContentType.NARRATIVE, content="t")
        )
        orch.builder.build = AsyncMock(return_value=Path("/tmp/out.docx"))

        calls = []
        asyncio.get_event_loop().run_until_complete(
            orch.generate(_make_spec(), on_progress=lambda p, f: calls.append((p, f)))
        )

        scanning_calls = [(p, f) for p, f in calls if p == "Scanning"]
        assert scanning_calls[0] == ("Scanning", 0.0)


# ---------------------------------------------------------------------------
# Cache serialization / deserialization
# ---------------------------------------------------------------------------


class TestCacheSerialization:
    """_save_results_cache / _load_results_cache round-trip."""

    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language", return_value=(PYTHON_PROFILE, 1))
    def test_round_trip_text_content(
        self, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs, mock_as, tmp_path
    ):
        orch = Orchestrator(
            llm=_make_mock_llm(),
            sanitizer=_make_mock_sanitizer(),
            code_root=Path("/tmp"),
            output_dir=tmp_path,
        )

        spec = _make_spec(title="Round Trip")
        results = [_make_section_result()]

        orch._save_results_cache(spec, results)
        loaded_spec, loaded_results = orch._load_results_cache()

        assert loaded_spec.title == "Round Trip"
        assert len(loaded_results) == 1
        assert loaded_results[0].plan.name == "Overview"
        assert loaded_results[0].contents[0].content == "Some generated text."

    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language", return_value=(PYTHON_PROFILE, 1))
    def test_round_trip_bytes_content(
        self, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs, mock_as, tmp_path
    ):
        """Binary content (e.g. chart PNG) survives serialization via base64."""
        orch = Orchestrator(
            llm=_make_mock_llm(),
            sanitizer=_make_mock_sanitizer(),
            code_root=Path("/tmp"),
            output_dir=tmp_path,
        )

        png_bytes = b"\x89PNG\r\n\x1a\nfake_chart_data"
        results = [
            _make_section_result(
                contents=[
                    GeneratedContent(
                        block_type=ContentType.CHART,
                        content=png_bytes,
                        metadata={"chart_type": "bar"},
                    )
                ]
            )
        ]

        spec = _make_spec()
        orch._save_results_cache(spec, results)
        _, loaded_results = orch._load_results_cache()

        assert loaded_results[0].contents[0].content == png_bytes
        assert loaded_results[0].contents[0].block_type == ContentType.CHART

    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language", return_value=(PYTHON_PROFILE, 1))
    def test_load_cache_missing_raises(
        self, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs, mock_as, tmp_path
    ):
        orch = Orchestrator(
            llm=_make_mock_llm(),
            sanitizer=_make_mock_sanitizer(),
            code_root=Path("/tmp"),
            output_dir=tmp_path,
        )
        with pytest.raises(FileNotFoundError, match="No cached results"):
            orch._load_results_cache()

    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language", return_value=(PYTHON_PROFILE, 1))
    def test_cache_preserves_errors_list(
        self, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs, mock_as, tmp_path
    ):
        orch = Orchestrator(
            llm=_make_mock_llm(),
            sanitizer=_make_mock_sanitizer(),
            code_root=Path("/tmp"),
            output_dir=tmp_path,
        )
        results = [_make_section_result(errors=["narrative: timeout"])]
        spec = _make_spec()
        orch._save_results_cache(spec, results)
        _, loaded = orch._load_results_cache()
        assert loaded[0].errors == ["narrative: timeout"]


# ---------------------------------------------------------------------------
# _serialize_section_result / _deserialize_section_result round-trip
# ---------------------------------------------------------------------------


class TestSerializeDeserializeRoundTrip:
    """Direct round-trip of section serialization helpers."""

    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language", return_value=(PYTHON_PROFILE, 1))
    def test_text_round_trip(
        self, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs, mock_as, tmp_path
    ):
        orch = Orchestrator(
            llm=_make_mock_llm(),
            sanitizer=_make_mock_sanitizer(),
            code_root=Path("/tmp"),
            output_dir=tmp_path,
        )
        result = _make_section_result(number="2", name="Details")
        serialized = orch._serialize_section_result(result)
        deserialized = orch._deserialize_section_result(serialized)

        assert deserialized.plan.number == "2"
        assert deserialized.plan.name == "Details"
        assert deserialized.contents[0].block_type == ContentType.NARRATIVE
        assert deserialized.contents[0].content == "Some generated text."

    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language", return_value=(PYTHON_PROFILE, 1))
    def test_binary_chart_round_trip(
        self, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs, mock_as, tmp_path
    ):
        orch = Orchestrator(
            llm=_make_mock_llm(),
            sanitizer=_make_mock_sanitizer(),
            code_root=Path("/tmp"),
            output_dir=tmp_path,
        )
        raw_bytes = b"\x00\x01binary_chart_data"
        result = _make_section_result(
            contents=[
                GeneratedContent(
                    block_type=ContentType.CHART,
                    content=raw_bytes,
                    metadata={"width": 800},
                )
            ]
        )
        serialized = orch._serialize_section_result(result)
        # Verify base64 encoding in serialized form
        assert serialized["contents"][0]["content_encoding"] == "base64"

        deserialized = orch._deserialize_section_result(serialized)
        assert deserialized.contents[0].content == raw_bytes
        assert deserialized.contents[0].metadata == {"width": 800}

    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language", return_value=(PYTHON_PROFILE, 1))
    def test_content_block_fields_survive_round_trip(
        self, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs, mock_as, tmp_path
    ):
        orch = Orchestrator(
            llm=_make_mock_llm(),
            sanitizer=_make_mock_sanitizer(),
            code_root=Path("/tmp"),
            output_dir=tmp_path,
        )
        plan = SectionPlan(
            number="3",
            name="Perf",
            title="Performance",
            model_name="xgb_v1",
            content_blocks=[
                ContentBlock(
                    type=ContentType.TABLE,
                    purpose="metrics table",
                    data_needed="mlflow",
                    specifics={"columns": ["metric", "value"]},
                    priority="optional",
                )
            ],
        )
        result = SectionResult(plan=plan, contents=[], errors=["table: no data"])

        serialized = orch._serialize_section_result(result)
        deser = orch._deserialize_section_result(serialized)

        assert deser.plan.model_name == "xgb_v1"
        cb = deser.plan.content_blocks[0]
        assert cb.type == ContentType.TABLE
        assert cb.priority == "optional"
        assert cb.specifics == {"columns": ["metric", "value"]}
        assert deser.errors == ["table: no data"]


# ---------------------------------------------------------------------------
# Config fields pass through to CodeScanner
# ---------------------------------------------------------------------------


class TestConfigPassthrough:
    """New config fields are forwarded to the CodeScanner constructor."""

    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language", return_value=(PYTHON_PROFILE, 1))
    def test_exclude_patterns_forwarded(
        self, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs_cls, mock_as
    ):
        Orchestrator(
            llm=_make_mock_llm(),
            sanitizer=_make_mock_sanitizer(),
            code_root=Path("/tmp"),
            exclude_patterns=["*.log", "data/"],
        )
        call_kwargs = mock_cs_cls.call_args[1]
        assert call_kwargs["exclude_patterns"] == ["*.log", "data/"]

    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language", return_value=(PYTHON_PROFILE, 1))
    def test_max_selected_files_forwarded(
        self, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs_cls, mock_as
    ):
        Orchestrator(
            llm=_make_mock_llm(),
            sanitizer=_make_mock_sanitizer(),
            code_root=Path("/tmp"),
            max_selected_files=25,
        )
        call_kwargs = mock_cs_cls.call_args[1]
        assert call_kwargs["max_selected_files"] == 25

    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language", return_value=(PYTHON_PROFILE, 1))
    def test_batch_size_forwarded(
        self, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs_cls, mock_as
    ):
        Orchestrator(
            llm=_make_mock_llm(),
            sanitizer=_make_mock_sanitizer(),
            code_root=Path("/tmp"),
            batch_size=8,
        )
        call_kwargs = mock_cs_cls.call_args[1]
        assert call_kwargs["batch_size"] == 8

    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language", return_value=(PYTHON_PROFILE, 1))
    def test_analysis_timeout_and_scan_retries_forwarded(
        self, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs_cls, mock_as
    ):
        Orchestrator(
            llm=_make_mock_llm(),
            sanitizer=_make_mock_sanitizer(),
            code_root=Path("/tmp"),
            analysis_timeout=120.0,
            scan_retries=5,
        )
        call_kwargs = mock_cs_cls.call_args[1]
        assert call_kwargs["analysis_timeout"] == 120.0
        assert call_kwargs["scan_retries"] == 5

    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language", return_value=(PYTHON_PROFILE, 1))
    def test_scan_workers_forwarded(
        self, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs_cls, mock_as
    ):
        Orchestrator(
            llm=_make_mock_llm(),
            sanitizer=_make_mock_sanitizer(),
            code_root=Path("/tmp"),
            scan_workers=4,
        )
        call_kwargs = mock_cs_cls.call_args[1]
        assert call_kwargs["scan_workers"] == 4


# ---------------------------------------------------------------------------
# Semaphore initialization
# ---------------------------------------------------------------------------


class TestSemaphoreInit:
    """Semaphores are created with the correct worker counts."""

    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language", return_value=(PYTHON_PROFILE, 1))
    def test_default_semaphore_values(
        self, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs, mock_as
    ):
        orch = Orchestrator(
            llm=_make_mock_llm(),
            sanitizer=_make_mock_sanitizer(),
            code_root=Path("/tmp"),
        )
        # Default parallel_workers=4
        assert orch.semaphore._value == 4
        # Default planning_workers=1
        assert orch.planning_semaphore._value == 1

    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language", return_value=(PYTHON_PROFILE, 1))
    def test_custom_semaphore_values(
        self, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs, mock_as
    ):
        orch = Orchestrator(
            llm=_make_mock_llm(),
            sanitizer=_make_mock_sanitizer(),
            code_root=Path("/tmp"),
            parallel_workers=8,
            planning_workers=3,
        )
        assert orch.semaphore._value == 8
        assert orch.planning_semaphore._value == 3

    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language", return_value=(PYTHON_PROFILE, 1))
    def test_single_worker_semaphores(
        self, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs, mock_as
    ):
        orch = Orchestrator(
            llm=_make_mock_llm(),
            sanitizer=_make_mock_sanitizer(),
            code_root=Path("/tmp"),
            parallel_workers=1,
            planning_workers=1,
        )
        assert orch.semaphore._value == 1
        assert orch.planning_semaphore._value == 1


# ---------------------------------------------------------------------------
# Spec fields round-trip through cache
# ---------------------------------------------------------------------------


class TestCacheSpecFields:
    """DocumentSpec fields survive the cache round-trip."""

    @patch("autodoc.orchestrator.ArtifactScanner")
    @patch("autodoc.orchestrator.CodeScanner")
    @patch("autodoc.orchestrator.DocumentBuilder")
    @patch("autodoc.orchestrator.SectionPlanner")
    @patch("autodoc.orchestrator.ContentGenerator")
    @patch("autodoc.orchestrator.detect_language", return_value=(PYTHON_PROFILE, 1))
    def test_spec_with_per_model_sections(
        self, mock_detect, mock_gen, mock_planner, mock_builder, mock_cs, mock_as, tmp_path
    ):
        orch = Orchestrator(
            llm=_make_mock_llm(),
            sanitizer=_make_mock_sanitizer(),
            code_root=Path("/tmp"),
            output_dir=tmp_path,
        )

        spec = DocumentSpec(
            title="Model Card",
            authors="ML Team",
            sections=[
                SectionSpec(name="Overview"),
                SectionSpec(name="Performance", per_model=True, hint="Focus on AUC"),
            ],
            hints={"Overview": "Keep it brief"},
            citation_style="numeric",
            formatting={"font_size": 11},
        )
        results = [_make_section_result()]
        orch._save_results_cache(spec, results)
        loaded_spec, _ = orch._load_results_cache()

        assert loaded_spec.title == "Model Card"
        assert loaded_spec.authors == "ML Team"
        assert len(loaded_spec.sections) == 2
        assert loaded_spec.sections[1].per_model is True
        assert loaded_spec.sections[1].hint == "Focus on AUC"
        assert loaded_spec.hints == {"Overview": "Keep it brief"}
        assert loaded_spec.citation_style == "numeric"
        assert loaded_spec.formatting == {"font_size": 11}
