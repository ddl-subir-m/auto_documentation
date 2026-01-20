"""Domain models for Auto Model Documentation."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field


# =============================================================================
# Enums
# =============================================================================


class ContentType(Enum):
    """Types of content blocks that can be generated."""

    NARRATIVE = "narrative"
    TABLE = "table"
    CHART = "chart"
    BULLET_LIST = "bullet_list"
    NUMBERED_LIST = "numbered_list"


# =============================================================================
# Input Specification Models (Pydantic for validation)
# =============================================================================


class SectionSpec(BaseModel):
    """Specification for a document section."""

    name: str = Field(..., min_length=1, max_length=200)
    per_model: bool = False
    hint: Optional[str] = Field(None, max_length=1000)


class DocumentSpec(BaseModel):
    """Complete document specification loaded from YAML."""

    title: str = Field(..., min_length=1, max_length=500)
    authors: str = "Data Science Team"
    sections: List[SectionSpec] = Field(..., min_length=1, max_length=50)
    hints: Dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str) -> "DocumentSpec":
        """Load document specification from a YAML file.

        Args:
            path: Path to the YAML specification file.

        Returns:
            DocumentSpec instance.

        Example YAML:
            title: "Credit Risk Model Documentation"
            authors: "Data Science Team"
            sections:
              - Executive Summary
              - Data Overview
              - "Model Performance: per_model"
            hints:
              "Executive Summary": "Focus on business impact"
        """
        with open(path) as f:
            data = yaml.safe_load(f)

        sections = []
        for section in data.get("sections", []):
            if isinstance(section, str):
                # Handle string format: "Section Name" or "Section Name: per_model"
                if section.endswith(": per_model"):
                    name = section.replace(": per_model", "")
                    sections.append(SectionSpec(name=name, per_model=True))
                else:
                    sections.append(SectionSpec(name=section))
            elif isinstance(section, dict):
                # Handle dict format: {name: "...", per_model: true, hint: "..."}
                sections.append(SectionSpec(**section))

        return cls(
            title=data["title"],
            authors=data.get("authors", "Data Science Team"),
            sections=sections,
            hints=data.get("hints", {}),
        )


# =============================================================================
# Context Models (Dataclasses for simplicity)
# =============================================================================


@dataclass
class CodeContext:
    """Context extracted from code repository via LLM analysis."""

    files: List[str] = field(default_factory=list)
    model_classes: List[str] = field(default_factory=list)
    features: List[str] = field(default_factory=list)
    target_variable: Optional[str] = None
    transformations: List[Dict[str, Any]] = field(default_factory=list)
    ml_task_type: Optional[str] = None
    hyperparameters: Dict[str, Any] = field(default_factory=dict)
    data_sources: List[str] = field(default_factory=list)
    insights: str = ""
    readme: Optional[str] = None


@dataclass
class ModelInfo:
    """Information about a registered ML model from MLflow."""

    name: str
    version: str
    stage: str
    run_id: str
    metrics: Dict[str, float] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[str] = field(default_factory=list)


@dataclass
class ArtifactContext:
    """Context extracted from MLflow and other artifact stores."""

    models: List[ModelInfo] = field(default_factory=list)
    datasets: List[Dict[str, Any]] = field(default_factory=list)
    project_metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def model_names(self) -> List[str]:
        """Get list of registered model names."""
        return [m.name for m in self.models]


@dataclass
class GenerationContext:
    """Combined context passed to content generators."""

    code_context: CodeContext
    artifact_context: ArtifactContext
    section_name: str
    model_name: Optional[str] = None
    hint: Optional[str] = None


# =============================================================================
# Planning Models
# =============================================================================


@dataclass
class ContentBlock:
    """A planned content block within a section."""

    type: ContentType
    purpose: str
    data_needed: str = ""
    specifics: Dict[str, Any] = field(default_factory=dict)
    priority: str = "required"


@dataclass
class SectionPlan:
    """Plan for a document section, including its content blocks."""

    number: str
    name: str
    title: str
    model_name: Optional[str] = None
    content_blocks: List[ContentBlock] = field(default_factory=list)


# =============================================================================
# Generation Output Models
# =============================================================================


@dataclass
class GeneratedContent:
    """Output from a content generator.

    The content field type depends on block_type:
    - NARRATIVE: str (text paragraphs)
    - TABLE: dict with keys: caption, columns, rows
    - CHART: bytes (PNG image data)
    - BULLET_LIST/NUMBERED_LIST: List[str]
    """

    block_type: ContentType
    content: Any
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SectionResult:
    """Result of generating a complete section."""

    plan: SectionPlan
    contents: List[GeneratedContent]
    errors: List[str] = field(default_factory=list)
