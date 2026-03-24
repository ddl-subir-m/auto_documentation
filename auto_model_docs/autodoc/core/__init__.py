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
    LanguageProfile,
    LANGUAGE_PROFILES,
    LANGUAGE_PRIORITY,
    MATLAB_PROFILE,
    ModelInfo,
    PYTHON_PROFILE,
    R_PROFILE,
    SAS_PROFILE,
    SectionPlan,
    SectionResult,
    SectionSpec,
    detect_language,
    get_language_profile,
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
    "LanguageProfile",
    "LANGUAGE_PROFILES",
    "LANGUAGE_PRIORITY",
    "MATLAB_PROFILE",
    "ModelInfo",
    "PYTHON_PROFILE",
    "R_PROFILE",
    "SAS_PROFILE",
    "SectionPlan",
    "SectionResult",
    "SectionSpec",
    "detect_language",
    "get_language_profile",
]
