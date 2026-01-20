"""Core components: configuration, models, and exceptions."""

from autodoc.core.config import Settings
from autodoc.core.exceptions import (
    AutoDocError,
    BuilderError,
    ConfigurationError,
    GenerationError,
    LLMError,
    SanitizationError,
    ScannerError,
)
from autodoc.core.models import (
    ArtifactContext,
    CodeContext,
    ContentBlock,
    ContentType,
    DocumentSpec,
    GeneratedContent,
    GenerationContext,
    ModelInfo,
    SectionPlan,
    SectionResult,
    SectionSpec,
)

__all__ = [
    # Config
    "Settings",
    # Exceptions
    "AutoDocError",
    "BuilderError",
    "ConfigurationError",
    "GenerationError",
    "LLMError",
    "SanitizationError",
    "ScannerError",
    # Models
    "ArtifactContext",
    "CodeContext",
    "ContentBlock",
    "ContentType",
    "DocumentSpec",
    "GeneratedContent",
    "GenerationContext",
    "ModelInfo",
    "SectionPlan",
    "SectionResult",
    "SectionSpec",
]
