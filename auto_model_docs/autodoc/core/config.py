"""Configuration settings for Auto Model Documentation."""

import os
from pathlib import Path
from typing import Literal, Optional

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _get_default_output_path() -> Path:
    """Get default output directory based on environment."""
    if Path("/mnt/data").exists():
        project_name = os.environ.get("DOMINO_PROJECT_NAME", "output")
        return Path(f"/mnt/data/{project_name}")
    return Path("./output")


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
        default=3,
        ge=0,
        le=10,
        description="Max retries for LLM requests",
        validation_alias=AliasChoices("AUTODOC_LLM_MAX_RETRIES", "LLM_MAX_RETRIES"),
    )
    llm_initial_backoff: float = Field(
        default=3.0,
        ge=0.1,
        le=60.0,
        description="Initial backoff delay in seconds",
        validation_alias=AliasChoices("AUTODOC_LLM_INITIAL_BACKOFF", "LLM_INITIAL_BACKOFF"),
    )
    llm_max_backoff: float = Field(
        default=30.0,
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

    # Paths
    code_root: Path = Field(
        default=Path("/mnt/code"),
        description="Root directory of codebase to analyze",
        validation_alias=AliasChoices("AUTODOC_CODE_ROOT", "CODE_ROOT"),
    )
    output_dir: Path = Field(
        default_factory=_get_default_output_path,
        description="Output directory for generated documents",
        validation_alias=AliasChoices("AUTODOC_OUTPUT_DIR", "OUTPUT_DIR"),
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

    # Generation Configuration
    parallel_workers: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Number of parallel content generation workers",
        validation_alias=AliasChoices("AUTODOC_PARALLEL_WORKERS", "PARALLEL_WORKERS"),
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

    # Cache Configuration
    cache_enabled: bool = Field(
        default=True,
        description="Enable LLM response caching",
        validation_alias=AliasChoices("AUTODOC_CACHE_ENABLED", "CACHE_ENABLED"),
    )
    cache_dir: Path = Field(
        default=Path(".autodoc_cache"),
        description="Directory for cache files",
        validation_alias=AliasChoices("AUTODOC_CACHE_DIR", "CACHE_DIR"),
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
        return "gpt-4o"
