"""Domain models for Auto Model Documentation."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel, Field


# =============================================================================
# Language Profile
# =============================================================================


@dataclass
class LanguageProfile:
    """Per-language configuration for code scanning and analysis."""

    name: str
    display_name: str
    file_extensions: List[str]
    priority_keywords: List[str]
    exclude_patterns: List[str]
    framework_hints: str
    library_examples: List[str]
    transformation_categories: List[str]
    secret_patterns: List[str]
    code_fence_lang: str


# Built-in language profiles
PYTHON_PROFILE = LanguageProfile(
    name="python",
    display_name="Python",
    file_extensions=["*.py"],
    priority_keywords=["train", "model", "feature", "pipeline", "main", "predict"],
    exclude_patterns=[
        "test_", "_test.py", "conftest.py", "__pycache__", ".git",
        "venv", ".venv", "node_modules", ".pytest_cache", "__init__.py",
    ],
    framework_hints=(
        "Look for usage of scikit-learn (sklearn), PyTorch, TensorFlow, XGBoost, "
        "LightGBM, Keras, pandas, numpy. Note: Python uses import statements, "
        "class definitions, and decorator patterns."
    ),
    library_examples=["sklearn", "xgboost", "tensorflow", "pytorch", "lightgbm", "keras"],
    transformation_categories=["scaling", "encoding", "feature engineering", "imputation"],
    secret_patterns=[],
    code_fence_lang="python",
)

R_PROFILE = LanguageProfile(
    name="r",
    display_name="R",
    file_extensions=["*.R", "*.r", "*.Rmd", "*.rmd"],
    priority_keywords=["train", "model", "feature", "predict", "fit", "recipe", "workflow"],
    exclude_patterns=[
        "test-", "tests/", "man/", "vignettes/", ".Rproj.user",
        ".git", "renv/", "packrat/",
    ],
    framework_hints=(
        "Look for usage of tidymodels (recipes, parsnip, workflows, tune, yardstick), "
        "caret, mlr3, xgboost, ranger, glmnet, randomForest, and e1071. "
        "Note: R uses library() for imports, <- for assignment, and formula syntax "
        "(y ~ x1 + x2) for model specification."
    ),
    library_examples=[
        "tidymodels", "caret", "mlr3", "xgboost", "ranger",
        "glmnet", "randomForest", "e1071", "recipes", "parsnip",
    ],
    transformation_categories=[
        "recipe steps", "formula transformations", "feature engineering",
        "data preprocessing", "imputation",
    ],
    secret_patterns=[".Renviron", ".Rprofile"],
    code_fence_lang="r",
)

SAS_PROFILE = LanguageProfile(
    name="sas",
    display_name="SAS",
    file_extensions=["*.sas", "*.SAS"],
    priority_keywords=["PROC", "MODEL", "LOGISTIC", "FOREST", "GRADBOOST", "DATA"],
    exclude_patterns=["autoexec.sas", ".git"],
    framework_hints=(
        "Look for usage of PROC LOGISTIC, PROC FOREST, PROC GRADBOOST, "
        "PROC HPFOREST, SAS Visual Data Mining, and SAS Model Studio macros. "
        "Note: SAS uses PROC statements for procedures, DATA steps for data "
        "manipulation, and macro variables (%let, &var) for parameterization."
    ),
    library_examples=[
        "PROC LOGISTIC", "PROC FOREST", "PROC GRADBOOST",
        "PROC HPFOREST", "PROC REG", "PROC GLM",
    ],
    transformation_categories=[
        "DATA step transformations", "PROC STDIZE", "PROC MI",
        "variable encoding", "feature selection",
    ],
    secret_patterns=[
        r"(?i)%let\s+(password|pwd)\s*=",
        r"(?i)libname\s+\w+.*\b(user|password)\s*=",
    ],
    code_fence_lang="sas",
)

MATLAB_PROFILE = LanguageProfile(
    name="matlab",
    display_name="MATLAB",
    file_extensions=["*.m"],
    priority_keywords=["fit", "train", "predict", "model", "classify", "regression"],
    exclude_patterns=["test_", "+", ".git"],
    framework_hints=(
        "Look for usage of Statistics and ML Toolbox (fitcecoc, fitctree, "
        "fitcensemble, fitcsvm, fitrgp), Deep Learning Toolbox (trainNetwork, "
        "dlnetwork), Regression Learner, and Classification Learner apps. "
        "Note: MATLAB uses function files, scripts, and object-oriented patterns "
        "with classdef."
    ),
    library_examples=[
        "fitcecoc", "fitctree", "fitcensemble", "fitcsvm",
        "fitrgp", "trainNetwork", "dlnetwork",
    ],
    transformation_categories=[
        "feature normalization", "PCA", "feature selection",
        "data preprocessing", "table operations",
    ],
    secret_patterns=[],
    code_fence_lang="matlab",
)

# Registry for lookup by name
LANGUAGE_PROFILES: Dict[str, LanguageProfile] = {
    "python": PYTHON_PROFILE,
    "r": R_PROFILE,
    "sas": SAS_PROFILE,
    "matlab": MATLAB_PROFILE,
}

# Tie-break order (higher priority first)
LANGUAGE_PRIORITY: List[str] = ["python", "r", "sas", "matlab"]


def get_language_profile(name: str) -> LanguageProfile:
    """Get a language profile by name.

    Args:
        name: Language name (python, r, sas, matlab).

    Returns:
        The matching LanguageProfile.

    Raises:
        ValueError: If the language name is not recognized.
    """
    profile = LANGUAGE_PROFILES.get(name.lower())
    if profile is None:
        supported = ", ".join(LANGUAGE_PROFILES.keys())
        raise ValueError(f"Unknown language '{name}'. Supported: {supported}")
    return profile


def detect_language(code_root: "Path") -> Tuple[Optional[LanguageProfile], int]:
    """Detect the project language by counting file extensions.

    Args:
        code_root: Root directory to scan.

    Returns:
        Tuple of (detected profile or None, file count).
        Returns (None, 0) if no supported files found.
    """
    from pathlib import Path

    if not code_root.exists():
        return None, 0

    counts: Dict[str, int] = {}
    for lang_name, profile in LANGUAGE_PROFILES.items():
        count = 0
        for ext in profile.file_extensions:
            count += len(list(code_root.rglob(ext)))
        if count > 0:
            counts[lang_name] = count

    if not counts:
        return None, 0

    # Find max count, break ties using LANGUAGE_PRIORITY
    max_count = max(counts.values())
    for lang in LANGUAGE_PRIORITY:
        if counts.get(lang, 0) == max_count:
            return LANGUAGE_PROFILES[lang], max_count

    return None, 0


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
    IMAGE = "image"  # For embedded MLflow images (feature importance plots, etc.)


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
    citation_style: str = "numeric"
    formatting: Dict[str, Any] = Field(default_factory=dict)

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
            citation_style=data.get("citation_style", "numeric"),
            formatting=data.get("formatting", {}),
        )

    @classmethod
    def validate_spec(cls, content: str) -> List[str]:
        """Validate spec YAML content and return user-friendly error messages.

        Args:
            content: Raw YAML string.

        Returns:
            List of error strings. Empty list means the spec is valid.
        """
        errors: List[str] = []

        # 1. Parse YAML
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            msg = "Invalid YAML syntax"
            if hasattr(exc, "problem_mark"):
                mark = exc.problem_mark
                msg += f" at line {mark.line + 1}, column {mark.column + 1}"
            if hasattr(exc, "problem"):
                msg += f": {exc.problem}"
            errors.append(msg)
            return errors

        if not isinstance(data, dict):
            errors.append("Spec file must be a YAML mapping (key: value pairs), not a "
                          + type(data).__name__)
            return errors

        # 2. Required field: title
        if "title" not in data:
            errors.append("Missing required field: 'title'")
        elif not isinstance(data["title"], str) or not data["title"].strip():
            errors.append("'title' must be a non-empty string")
        elif len(data["title"]) > 500:
            errors.append(f"'title' is too long ({len(data['title'])} characters, max 500)")

        # 3. Required field: sections
        if "sections" not in data:
            errors.append("Missing required field: 'sections'")
        elif not isinstance(data["sections"], list):
            errors.append("'sections' must be a list")
        elif len(data["sections"]) == 0:
            errors.append("'sections' must contain at least 1 section")
        elif len(data["sections"]) > 50:
            errors.append(f"Too many sections ({len(data['sections'])}). Maximum is 50.")
        else:
            for i, section in enumerate(data["sections"], 1):
                if isinstance(section, str):
                    name = section.replace(": per_model", "") if section.endswith(": per_model") else section
                    if not name.strip():
                        errors.append(f"Section {i}: name must not be empty")
                    elif len(name) > 200:
                        errors.append(f"Section {i} ('{name[:30]}...'): name too long ({len(name)} chars, max 200)")
                elif isinstance(section, dict):
                    if "name" not in section:
                        errors.append(f"Section {i}: missing required key 'name'")
                    elif not isinstance(section["name"], str) or not section["name"].strip():
                        errors.append(f"Section {i}: 'name' must be a non-empty string")
                    elif len(section["name"]) > 200:
                        errors.append(f"Section {i} ('{section['name'][:30]}...'): name too long "
                                      f"({len(section['name'])} chars, max 200)")
                    if "hint" in section and isinstance(section["hint"], str) and len(section["hint"]) > 1000:
                        errors.append(f"Section {i}: hint too long ({len(section['hint'])} chars, max 1000)")
                    unknown = set(section.keys()) - {"name", "per_model", "hint"}
                    if unknown:
                        errors.append(f"Section {i}: unexpected keys {sorted(unknown)}")
                else:
                    errors.append(f"Section {i}: must be a string or mapping, got {type(section).__name__}")

        # 4. Optional field types
        if "hints" in data and not isinstance(data["hints"], dict):
            errors.append("'hints' must be a mapping of section name to hint text")

        if "formatting" in data and not isinstance(data["formatting"], dict):
            errors.append("'formatting' must be a mapping")

        return errors


# =============================================================================
# Context Models (Dataclasses for simplicity)
# =============================================================================


@dataclass
class CodeEvidence:
    """Evidence item linking a statement to code."""

    path: str
    symbol: str
    statement: str
    snippet: str
    start_line: Optional[int] = None
    end_line: Optional[int] = None


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
    code_evidence: List[CodeEvidence] = field(default_factory=list)
    language: str = "python"
    skipped_files: List[str] = field(default_factory=list)
    scan_incomplete: bool = False


@dataclass
class ModelInfo:
    """Information about a registered ML model from MLflow."""

    name: str
    version: str
    stage: str
    run_id: str
    experiment_id: Optional[str] = None
    experiment_name: Optional[str] = None
    metrics: Dict[str, float] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)
    tags: Dict[str, str] = field(default_factory=dict)
    artifacts: List[str] = field(default_factory=list)
    artifact_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ArtifactContext:
    """Context extracted from MLflow and other artifact stores."""

    models: List[ModelInfo] = field(default_factory=list)
    datasets: List[Dict[str, Any]] = field(default_factory=list)
    project_metadata: Dict[str, Any] = field(default_factory=dict)
    mlflow_metrics: List[Dict[str, str]] = field(default_factory=list)
    mlflow_params: List[Dict[str, str]] = field(default_factory=list)
    mlflow_tags: List[Dict[str, str]] = field(default_factory=list)
    mlflow_artifacts: List[Dict[str, str]] = field(default_factory=list)

    @property
    def model_names(self) -> List[str]:
        """Get list of registered model names."""
        return [m.name for m in self.models]


@dataclass
class Citation:
    """Auto-derived citation entry."""

    id: str
    type: str
    run_id: Optional[str] = None
    source_key: Optional[str] = None
    artifact_path: Optional[str] = None
    code_path: Optional[str] = None
    code_symbol: Optional[str] = None
    evidence_text: Optional[str] = None
    url: Optional[str] = None



@dataclass
class GenerationContext:
    """Combined context passed to content generators."""

    code_context: CodeContext
    artifact_context: ArtifactContext
    section_name: str
    model_name: Optional[str] = None
    model_run_id: Optional[str] = None
    hint: Optional[str] = None
    language: str = "python"


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
    model_run_id: Optional[str] = None
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
