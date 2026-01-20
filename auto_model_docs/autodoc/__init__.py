"""Auto Model Documentation - LLM-powered ML codebase documentation generator."""

from autodoc.core.config import Settings
from autodoc.core.exceptions import (
    AutoDocError,
    BuilderError,
    GenerationError,
    LLMError,
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
    SectionSpec,
)
from autodoc.orchestrator import Orchestrator

__version__ = "0.1.0"

__all__ = [
    # Models
    "DocumentSpec",
    "SectionSpec",
    "CodeContext",
    "ArtifactContext",
    "ModelInfo",
    "GenerationContext",
    "SectionPlan",
    "ContentBlock",
    "ContentType",
    "GeneratedContent",
    # Core
    "Orchestrator",
    "Settings",
    # Exceptions
    "AutoDocError",
    "ScannerError",
    "LLMError",
    "GenerationError",
    "BuilderError",
]
