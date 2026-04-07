"""Tests for autodoc.core.config — Settings class."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import SecretStr, ValidationError

from autodoc.core.config import Settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(**overrides):
    """Create a Settings instance with env vars cleared so defaults apply.

    pydantic-settings reads real environment variables AND .env files,
    so we patch os.environ to an empty dict and override _env_file to
    prevent loading the project's .env file.
    """
    env = {k: v for k, v in overrides.items()}
    with patch.dict(os.environ, env, clear=True):
        return Settings(_env_file=None)


# ===========================================================================
# Default values for all fields
# ===========================================================================

class TestDefaultValues:
    """Every field should have a sensible default."""

    def test_llm_provider_default(self):
        s = _make_settings()
        assert s.llm_provider == "anthropic"

    def test_llm_model_default_none(self):
        s = _make_settings()
        assert s.llm_model is None

    def test_llm_max_retries_default(self):
        s = _make_settings()
        assert s.llm_max_retries == 5

    def test_llm_initial_backoff_default(self):
        s = _make_settings()
        assert s.llm_initial_backoff == 10.0

    def test_llm_max_backoff_default(self):
        s = _make_settings()
        assert s.llm_max_backoff == 120.0

    def test_llm_backoff_jitter_default(self):
        s = _make_settings()
        assert s.llm_backoff_jitter == 0.2

    def test_api_keys_default_none(self):
        s = _make_settings()
        assert s.anthropic_api_key is None
        assert s.openai_api_key is None

    def test_openai_base_url_default_none(self):
        s = _make_settings()
        assert s.openai_base_url is None

    def test_code_root_default(self):
        s = _make_settings()
        assert s.code_root == Path("/mnt/code")

    def test_max_files_default(self):
        s = _make_settings()
        assert s.max_files == 50

    def test_max_file_size_default(self):
        s = _make_settings()
        assert s.max_file_size == 50000

    def test_parallel_workers_default(self):
        s = _make_settings()
        assert s.parallel_workers == 4

    def test_planning_workers_default(self):
        s = _make_settings()
        assert s.planning_workers == 3

    def test_mlflow_defaults_none(self):
        s = _make_settings()
        assert s.mlflow_tracking_uri is None
        assert s.mlflow_experiment_name is None

    # cache_enabled and cache_dir removed — LLMCache was dead code


# ===========================================================================
# New scanning config fields
# ===========================================================================

class TestScanningConfigFields:
    """Two-pass scanning configuration defaults and constraints."""

    def test_exclude_patterns_default(self):
        s = _make_settings()
        assert isinstance(s.exclude_patterns, list)
        assert "tests/" in s.exclude_patterns
        assert ".git/" in s.exclude_patterns
        assert "node_modules/" in s.exclude_patterns
        assert "__pycache__/" in s.exclude_patterns

    def test_max_selected_files_default(self):
        s = _make_settings()
        assert s.max_selected_files == 15

    def test_batch_size_default(self):
        s = _make_settings()
        assert s.batch_size == 4

    def test_analysis_timeout_default(self):
        s = _make_settings()
        assert s.analysis_timeout == 90.0

    def test_scan_retries_default(self):
        s = _make_settings()
        assert s.scan_retries == 2

    def test_scan_workers_default(self):
        s = _make_settings()
        assert s.scan_workers == 2


# ===========================================================================
# get_api_key()
# ===========================================================================

class TestGetApiKey:
    """Retrieving the API key for the active provider."""

    def test_anthropic_key_returned(self):
        s = _make_settings(ANTHROPIC_API_KEY="test-anthropic-key-123")
        assert s.get_api_key() == "test-anthropic-key-123"

    def test_openai_key_returned(self):
        s = _make_settings(LLM_PROVIDER="openai", OPENAI_API_KEY="test-openai-key-456")
        assert s.get_api_key() == "test-openai-key-456"

    def test_anthropic_key_missing_raises(self):
        s = _make_settings()
        assert s.llm_provider == "anthropic"
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            s.get_api_key()

    def test_openai_key_missing_raises(self):
        s = _make_settings(LLM_PROVIDER="openai")
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            s.get_api_key()

    def test_prefixed_env_var_works(self):
        """Keys supplied with the AUTODOC_ prefix should also be accepted."""
        s = _make_settings(AUTODOC_ANTHROPIC_API_KEY="prefixed-key-789")
        assert s.get_api_key() == "prefixed-key-789"

    def test_api_key_is_secret_str(self):
        s = _make_settings(ANTHROPIC_API_KEY="my-secret")
        assert isinstance(s.anthropic_api_key, SecretStr)
        # str() should not reveal the value
        assert "my-secret" not in str(s.anthropic_api_key)


# ===========================================================================
# get_model_name()
# ===========================================================================

class TestGetModelName:
    """Model name resolution with provider-specific defaults."""

    def test_anthropic_default_model(self):
        s = _make_settings()
        assert "claude" in s.get_model_name().lower()

    def test_openai_default_model(self):
        s = _make_settings(LLM_PROVIDER="openai")
        model = s.get_model_name()
        # Should return the kimi model default
        assert "kimi" in model.lower()

    def test_custom_model_overrides_default(self):
        s = _make_settings(LLM_MODEL="my-custom-model-v2")
        assert s.get_model_name() == "my-custom-model-v2"

    def test_custom_model_with_openai_provider(self):
        s = _make_settings(LLM_PROVIDER="openai", LLM_MODEL="gpt-4o-mini")
        assert s.get_model_name() == "gpt-4o-mini"


# ===========================================================================
# Field validation ranges (ge, le constraints)
# ===========================================================================

class TestFieldValidation:
    """Pydantic ge/le constraints reject out-of-range values."""

    def test_llm_max_retries_too_high(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_LLM_MAX_RETRIES="11")

    def test_llm_max_retries_negative(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_LLM_MAX_RETRIES="-1")

    def test_llm_initial_backoff_too_low(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_LLM_INITIAL_BACKOFF="0.01")

    def test_llm_initial_backoff_too_high(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_LLM_INITIAL_BACKOFF="61")

    def test_llm_max_backoff_too_low(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_LLM_MAX_BACKOFF="0.5")

    def test_llm_max_backoff_too_high(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_LLM_MAX_BACKOFF="301")

    def test_llm_backoff_jitter_too_high(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_LLM_BACKOFF_JITTER="1.5")

    def test_max_files_too_low(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_MAX_FILES="0")

    def test_max_files_too_high(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_MAX_FILES="201")

    def test_max_file_size_too_low(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_MAX_FILE_SIZE="999")

    def test_max_file_size_too_high(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_MAX_FILE_SIZE="200001")

    def test_parallel_workers_too_low(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_PARALLEL_WORKERS="0")

    def test_parallel_workers_too_high(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_PARALLEL_WORKERS="11")

    def test_max_selected_files_too_low(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_MAX_SELECTED_FILES="0")

    def test_max_selected_files_too_high(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_MAX_SELECTED_FILES="51")

    def test_batch_size_too_low(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_BATCH_SIZE="0")

    def test_batch_size_too_high(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_BATCH_SIZE="11")

    def test_analysis_timeout_too_low(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_ANALYSIS_TIMEOUT="5")

    def test_analysis_timeout_too_high(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_ANALYSIS_TIMEOUT="301")

    def test_scan_retries_negative(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_SCAN_RETRIES="-1")

    def test_scan_retries_too_high(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_SCAN_RETRIES="6")

    def test_scan_workers_too_low(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_SCAN_WORKERS="0")

    def test_scan_workers_too_high(self):
        with pytest.raises(ValidationError):
            _make_settings(AUTODOC_SCAN_WORKERS="9")

    def test_valid_boundary_values_accepted(self):
        """Values at the exact boundary limits should be accepted."""
        s = _make_settings(
            AUTODOC_LLM_MAX_RETRIES="0",
            AUTODOC_MAX_FILES="1",
            AUTODOC_MAX_FILE_SIZE="1000",
            AUTODOC_PARALLEL_WORKERS="1",
            AUTODOC_BATCH_SIZE="1",
            AUTODOC_SCAN_RETRIES="0",
            AUTODOC_SCAN_WORKERS="1",
        )
        assert s.llm_max_retries == 0
        assert s.max_files == 1
        assert s.max_file_size == 1000
        assert s.parallel_workers == 1
        assert s.batch_size == 1
        assert s.scan_retries == 0
        assert s.scan_workers == 1

    def test_valid_upper_boundary_values_accepted(self):
        s = _make_settings(
            AUTODOC_LLM_MAX_RETRIES="10",
            AUTODOC_MAX_FILES="200",
            AUTODOC_MAX_FILE_SIZE="200000",
            AUTODOC_PARALLEL_WORKERS="10",
            AUTODOC_BATCH_SIZE="10",
            AUTODOC_SCAN_RETRIES="5",
            AUTODOC_SCAN_WORKERS="8",
        )
        assert s.llm_max_retries == 10
        assert s.max_files == 200
        assert s.max_file_size == 200000
        assert s.parallel_workers == 10
        assert s.batch_size == 10
        assert s.scan_retries == 5
        assert s.scan_workers == 8

    def test_invalid_provider_rejected(self):
        with pytest.raises(ValidationError):
            _make_settings(LLM_PROVIDER="gemini")


# ===========================================================================
# Default output path selection
# ===========================================================================

# TestDefaultOutputPath removed — output path logic moved to ArtifactLayout.
# See tests/test_artifact_layout.py for path resolution tests.


# ===========================================================================
# Environment variable aliases
# ===========================================================================

class TestEnvVarAliases:
    """Both prefixed (AUTODOC_) and bare env var names should work."""

    def test_bare_code_root(self):
        s = _make_settings(CODE_ROOT="/tmp/myrepo")
        assert s.code_root == Path("/tmp/myrepo")

    def test_prefixed_code_root(self):
        s = _make_settings(AUTODOC_CODE_ROOT="/tmp/myrepo2")
        assert s.code_root == Path("/tmp/myrepo2")

    def test_bare_max_files(self):
        s = _make_settings(MAX_FILES="100")
        assert s.max_files == 100

    def test_prefixed_max_files(self):
        s = _make_settings(AUTODOC_MAX_FILES="75")
        assert s.max_files == 75

    # test_bare_cache_enabled removed — cache_enabled field deleted
