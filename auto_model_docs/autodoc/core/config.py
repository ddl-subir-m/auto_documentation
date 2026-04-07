"""Configuration settings for Auto Model Documentation."""

import os
from pathlib import Path
from typing import Literal, Optional

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables.

    Loads configuration from:
    1. .env file (if present)
    2. Environment variables (with or without AUTODOC_ prefix)
    3. Default values
    """

    # LLM Configuration
    llm_provider: Literal["anthropic", "openai"] = Field(
        default="anthropic",
        description="LLM provider to use",
        validation_alias=AliasChoices("AUTODOC_LLM_PROVIDER", "LLM_PROVIDER"),
    )
    llm_model: Optional[str] = Field(
        default=None,
        description="Model name override (uses provider default if not set)",
        validation_alias=AliasChoices("AUTODOC_LLM_MODEL", "LLM_MODEL"),
    )
    llm_max_retries: int = Field(
        default=5,
        ge=0,
        le=10,
        description="Max retries for LLM requests",
        validation_alias=AliasChoices("AUTODOC_LLM_MAX_RETRIES", "LLM_MAX_RETRIES"),
    )
    llm_initial_backoff: float = Field(
        default=10.0,
        ge=0.1,
        le=60.0,
        description="Initial backoff delay in seconds",
        validation_alias=AliasChoices("AUTODOC_LLM_INITIAL_BACKOFF", "LLM_INITIAL_BACKOFF"),
    )
    llm_max_backoff: float = Field(
        default=120.0,
        ge=1.0,
        le=300.0,
        description="Maximum backoff delay in seconds",
        validation_alias=AliasChoices("AUTODOC_LLM_MAX_BACKOFF", "LLM_MAX_BACKOFF"),
    )
    llm_backoff_jitter: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Random jitter factor applied to backoff",
        validation_alias=AliasChoices("AUTODOC_LLM_BACKOFF_JITTER", "LLM_BACKOFF_JITTER"),
    )
    anthropic_api_key: Optional[SecretStr] = Field(
        default=None,
        description="Anthropic API key (can use ANTHROPIC_API_KEY or AUTODOC_ANTHROPIC_API_KEY)",
        validation_alias=AliasChoices("AUTODOC_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    )
    openai_api_key: Optional[SecretStr] = Field(
        default=None,
        description="OpenAI API key (can use OPENAI_API_KEY or AUTODOC_OPENAI_API_KEY)",
        validation_alias=AliasChoices("AUTODOC_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    openai_base_url: Optional[str] = Field(
        default=None,
        description="OpenAI-compatible API base URL (e.g., https://api.moonshot.ai/v1)",
        validation_alias=AliasChoices("AUTODOC_OPENAI_BASE_URL", "OPENAI_BASE_URL"),
    )

    # Paths
    code_root: Path = Field(
        default=Path("/mnt/code"),
        description="Root directory of codebase to analyze",
        validation_alias=AliasChoices("AUTODOC_CODE_ROOT", "CODE_ROOT"),
    )

    # Scanning Configuration
    max_files: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of files to scan",
        validation_alias=AliasChoices("AUTODOC_MAX_FILES", "MAX_FILES"),
    )
    max_file_size: int = Field(
        default=50000,
        ge=1000,
        le=200000,
        description="Maximum file size in characters",
        validation_alias=AliasChoices("AUTODOC_MAX_FILE_SIZE", "MAX_FILE_SIZE"),
    )

    # Two-Pass Scanning Configuration
    exclude_patterns: list = Field(
        default=["tests/", "test_", "__pycache__/", ".git/", "node_modules/",
                 "vendor/", "vendored/", ".tox/", ".venv/", "venv/",
                 ".egg-info/", "dist/", "build/", ".mypy_cache/"],
        description="Path patterns to exclude from scanning",
        validation_alias=AliasChoices("AUTODOC_EXCLUDE_PATTERNS", "EXCLUDE_PATTERNS"),
    )
    max_selected_files: int = Field(
        default=15,
        ge=1,
        le=50,
        description="Maximum files selected for deep analysis after ranking",
        validation_alias=AliasChoices("AUTODOC_MAX_SELECTED_FILES", "MAX_SELECTED_FILES"),
    )
    batch_size: int = Field(
        default=4,
        ge=1,
        le=10,
        description="Files per deep analysis batch",
        validation_alias=AliasChoices("AUTODOC_BATCH_SIZE", "BATCH_SIZE"),
    )
    analysis_timeout: float = Field(
        default=90.0,
        ge=10.0,
        le=300.0,
        description="Per-batch timeout in seconds for scanning LLM calls",
        validation_alias=AliasChoices("AUTODOC_ANALYSIS_TIMEOUT", "ANALYSIS_TIMEOUT"),
    )
    scan_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Retries per scanning batch (fail fast)",
        validation_alias=AliasChoices("AUTODOC_SCAN_RETRIES", "SCAN_RETRIES"),
    )
    scan_workers: int = Field(
        default=2,
        ge=1,
        le=8,
        description="Parallel batch workers for scanning (separate from generation workers)",
        validation_alias=AliasChoices("AUTODOC_SCAN_WORKERS", "SCAN_WORKERS"),
    )

    # Generation Configuration
    parallel_workers: int = Field(
        default=4,
        ge=1,
        le=10,
        description="Number of parallel content generation workers",
        validation_alias=AliasChoices("AUTODOC_PARALLEL_WORKERS", "PARALLEL_WORKERS"),
    )
    planning_workers: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Number of parallel planning workers",
        validation_alias=AliasChoices("AUTODOC_PLANNING_WORKERS", "PLANNING_WORKERS"),
    )

    # MLflow Configuration
    mlflow_tracking_uri: Optional[str] = Field(
        default=None,
        description="MLflow tracking URI",
        validation_alias=AliasChoices("AUTODOC_MLFLOW_TRACKING_URI", "MLFLOW_TRACKING_URI"),
    )
    mlflow_experiment_name: Optional[str] = Field(
        default=None,
        description="MLflow experiment name to query",
        validation_alias=AliasChoices("AUTODOC_MLFLOW_EXPERIMENT_NAME", "MLFLOW_EXPERIMENT_NAME"),
    )

    _repo_root = Path(__file__).resolve().parents[3]
    model_config = SettingsConfigDict(
        env_file=str(_repo_root / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    def get_api_key(self) -> str:
        """Get the API key for the configured provider."""
        if self.llm_provider == "anthropic":
            if self.anthropic_api_key:
                return self.anthropic_api_key.get_secret_value()
            raise ValueError("AUTODOC_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY not set")
        else:
            if self.openai_api_key:
                return self.openai_api_key.get_secret_value()
            raise ValueError("AUTODOC_OPENAI_API_KEY or OPENAI_API_KEY not set")

    def get_model_name(self) -> str:
        """Get the model name, using defaults if not explicitly set."""
        if self.llm_model:
            return self.llm_model
        if self.llm_provider == "anthropic":
            return "claude-sonnet-4-20250514"
        return "kimi-k2-0905-preview"
