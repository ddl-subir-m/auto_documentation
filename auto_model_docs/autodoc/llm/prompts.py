"""Centralized LLM prompts for Auto Model Documentation.

This module contains all prompts used throughout the system,
making it easy to review, update, and maintain them in one place.
"""

from typing import Any, Dict, List, Optional


# =============================================================================
# System Prompts
# =============================================================================

SYSTEM_CODE_ANALYZER = (
    "You are an expert at analyzing machine learning code. "
    "Extract all relevant information about the ML pipeline."
)

SYSTEM_SECTION_PLANNER = (
    "You are a technical documentation expert. "
    "Plan clear, informative content for ML model documentation."
)

SYSTEM_NARRATIVE_WRITER = (
    "You are a technical documentation writer. "
    "Write clear, informative content about machine learning models."
)

SYSTEM_TABLE_GENERATOR = (
    "You are a technical documentation expert. "
    "Generate informative tables for ML documentation."
)

SYSTEM_CHART_GENERATOR = (
    "You are a data visualization expert. "
    "Generate meaningful chart data."
)

SYSTEM_LIST_GENERATOR = (
    "You are a technical documentation expert. "
    "Generate clear, informative list items."
)


# =============================================================================
# Code Scanner Prompts
# =============================================================================

def build_code_analysis_prompt(code_contents: List[Dict[str, str]]) -> str:
    """Build prompt for analyzing ML codebase.

    Args:
        code_contents: List of dicts with 'file' and 'content' keys.

    Returns:
        Formatted prompt string.
    """
    code_text = "\n\n".join([
        f"### File: {c['file']}\n```python\n{c['content']}\n```"
        for c in code_contents
    ])

    return f"""Analyze this machine learning codebase and extract information.

{code_text}

Extract the following information:
1. Model classes used (e.g., sklearn models, xgboost, tensorflow, pytorch, etc.)
2. Feature names/columns used in the model
3. Target variable name
4. Data transformations (scaling, encoding, feature engineering, etc.)
5. ML task type (classification, regression, clustering, etc.)
6. Hyperparameters and their values
7. Data sources (files, databases, APIs)
8. Any other insights about the model architecture and training"""


CODE_ANALYSIS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "model_classes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "ML model classes/algorithms used",
        },
        "features": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Feature names/columns",
        },
        "target_variable": {
            "type": "string",
            "description": "Target variable name",
        },
        "transformations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "method": {"type": "string"},
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
            "description": "Data transformations applied",
        },
        "ml_task_type": {
            "type": "string",
            "description": "Type of ML task",
        },
        "hyperparameters": {
            "type": "object",
            "description": "Hyperparameter values",
        },
        "data_sources": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Data sources (files, databases)",
        },
        "insights": {
            "type": "string",
            "description": "Additional insights about the codebase",
        },
    },
    "required": ["model_classes", "features"],
}


# =============================================================================
# Section Planner Prompts
# =============================================================================

def build_section_planning_prompt(
    section_name: str,
    hint: Optional[str],
    model_name: Optional[str],
    model_classes: str,
    ml_task_type: str,
    features_preview: str,
    target_variable: str,
    registered_models: str,
    data_sources: str,
    metrics_info: str = "",
) -> str:
    """Build prompt for planning section content.

    Args:
        section_name: Name of the section.
        hint: Optional user guidance for this section.
        model_name: Specific model name (for per-model sections).
        model_classes: Comma-separated ML framework/model names.
        ml_task_type: Type of ML task.
        features_preview: Preview of feature names.
        target_variable: Target variable name.
        registered_models: Comma-separated registered model names.
        data_sources: Comma-separated data source names.
        metrics_info: Optional metrics information string.

    Returns:
        Formatted prompt string.
    """
    model_line = f"\n## Specific Model: {model_name}" if model_name else ""

    return f"""Plan content for a model documentation section.

## Section: {section_name}
## User Guidance: {hint or 'None provided'}{model_line}

## Project Context
- ML Framework/Models: {model_classes}
- ML Task Type: {ml_task_type}
- Features: {features_preview}
- Target Variable: {target_variable}
- Registered Models: {registered_models}{metrics_info}
- Data Sources: {data_sources}

## Task
Determine what content blocks this section should contain to create useful documentation.

Content block types available:
- narrative: Explanatory paragraphs (2-4 paragraphs)
- table: Structured data in rows and columns
- chart: Visual representation (bar, line, or scatter)
- bullet_list: Bulleted list of items
- numbered_list: Numbered/ordered list of steps

Consider what would be most valuable for documenting this section. Include 2-4 content blocks."""


SECTION_PLANNING_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "section_title": {
            "type": "string",
            "description": "Display title for the section",
        },
        "content_blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "narrative",
                            "table",
                            "chart",
                            "bullet_list",
                            "numbered_list",
                        ],
                    },
                    "purpose": {
                        "type": "string",
                        "description": "What this content block should accomplish",
                    },
                    "data_needed": {
                        "type": "string",
                        "description": "What data/information to include",
                    },
                    "specifics": {
                        "type": "object",
                        "description": "Additional specifications (e.g., chart_type for charts)",
                    },
                },
                "required": ["type", "purpose"],
            },
            "minItems": 1,
            "maxItems": 5,
        },
    },
    "required": ["section_title", "content_blocks"],
}


# =============================================================================
# Content Generator Prompts
# =============================================================================

def build_narrative_prompt(
    section_name: str,
    purpose: str,
    data_needed: Optional[str],
    model_classes: str,
    ml_task_type: str,
    target_variable: str,
    features: str,
    data_sources: str,
    model_name: Optional[str],
    model_info: str,
    insights: str,
) -> str:
    """Build prompt for generating narrative content.

    Args:
        section_name: Name of the section.
        purpose: Purpose of this content block.
        data_needed: What data should be included.
        model_classes: Comma-separated model class names.
        ml_task_type: Type of ML task.
        target_variable: Target variable name.
        features: Comma-separated feature names.
        data_sources: Comma-separated data source names.
        model_name: Specific model name (optional).
        model_info: Additional model metrics info.
        insights: Additional insights from code analysis.

    Returns:
        Formatted prompt string.
    """
    data_line = f"\n## Data Needed: {data_needed}" if data_needed else ""
    model_line = f"\n- Specific Model: {model_name}" if model_name else ""

    return f"""Write professional documentation content.

## Section: {section_name}
## Purpose: {purpose}{data_line}

## Context
- Model Type: {model_classes}
- ML Task: {ml_task_type}
- Target: {target_variable}
- Features: {features}
- Data Sources: {data_sources}{model_line}{model_info}

## Additional Context
{insights or "No additional insights available."}

## Instructions
- Write 2-4 paragraphs of clear, professional prose
- Focus on insights and explanations, not just listing facts
- Use a formal but accessible tone
- Do NOT use markdown formatting (no headers, bullets, or bold)
- Do NOT include a title or heading
- Just write the paragraph content directly

CRITICAL: Only describe metrics, results, and methodologies that are explicitly mentioned in the context above.
If cross-validation or other specific techniques are not mentioned in the context, do NOT claim they were performed.
If specific metrics are not provided, do not invent values - instead note what metrics are available.
Do NOT fabricate, estimate, or invent any metrics, statistics, or numerical values."""


def build_table_prompt(
    purpose: str,
    data_needed: Optional[str],
    features: str,
    model_classes: str,
    transformations: str,
    hyperparameters: str,
    metrics_info: str = "",
) -> str:
    """Build prompt for generating table content.

    Args:
        purpose: Purpose of this table.
        data_needed: What data should be included.
        features: Comma-separated feature names.
        model_classes: Comma-separated model class names.
        transformations: Transformation info string.
        hyperparameters: Hyperparameters info string.
        metrics_info: Optional metrics information.

    Returns:
        Formatted prompt string.
    """
    return f"""Generate a data table for documentation.

## Purpose: {purpose}
## Data Needed: {data_needed or "Relevant data for this section"}

## Available Context
- Features: {features}
- Model Classes: {model_classes}
- Transformations: {transformations}
- Hyperparameters: {hyperparameters}{metrics_info}

CRITICAL INSTRUCTIONS:
- ONLY include metrics and values that are explicitly provided in the "Available Context" above
- Do NOT fabricate, estimate, or invent any metrics, statistics, or numerical values
- Do NOT generate cross-validation metrics unless CV results are explicitly provided above
- If specific data is not available, either omit that row/column or mark it as "Not Available"
- Use the exact metric values provided - do not round, estimate, or modify them

Generate a useful table with 3-10 rows using ONLY the data provided above."""


TABLE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "caption": {
            "type": "string",
            "description": "Table caption/title",
        },
        "columns": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Column headers",
        },
        "rows": {
            "type": "array",
            "items": {"type": "object"},
            "description": "Row data as objects with column names as keys",
        },
    },
    "required": ["caption", "columns", "rows"],
}


def build_chart_prompt(
    purpose: str,
    data_needed: Optional[str],
    chart_type: str,
    model_classes: str,
    ml_task_type: str,
    metrics_hint: str = "",
) -> str:
    """Build prompt for generating chart data.

    Args:
        purpose: Purpose of this chart.
        data_needed: What data should be visualized.
        chart_type: Type of chart (bar, line, scatter).
        model_classes: Comma-separated model class names.
        ml_task_type: Type of ML task.
        metrics_hint: Optional actual metrics available.

    Returns:
        Formatted prompt string.
    """
    return f"""Generate data for a {chart_type} chart.

## Purpose: {purpose}
## Data Needed: {data_needed or "Relevant data for visualization"}{metrics_hint}

## Context
- Model Type: {model_classes}
- ML Task: {ml_task_type}

CRITICAL: Only visualize data that is explicitly provided above.
Do NOT fabricate or estimate any values. If the requested visualization
cannot be created with available data, use placeholder labels like "Metric 1", "Metric 2"
with the actual values from the metrics provided, or indicate what data would be needed.

Provide labels and values for the chart using ONLY the data provided above."""


CHART_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Chart title"},
        "labels": {
            "type": "array",
            "items": {"type": "string"},
            "description": "X-axis labels or categories",
        },
        "values": {
            "type": "array",
            "items": {"type": "number"},
            "description": "Y-axis values",
        },
        "xlabel": {"type": "string", "description": "X-axis label"},
        "ylabel": {"type": "string", "description": "Y-axis label"},
    },
    "required": ["title", "labels", "values"],
}


def build_list_prompt(
    purpose: str,
    data_needed: Optional[str],
    model_classes: str,
    ml_task_type: str,
    features: str,
) -> str:
    """Build prompt for generating list content.

    Args:
        purpose: Purpose of this list.
        data_needed: What items should be included.
        model_classes: Comma-separated model class names.
        ml_task_type: Type of ML task.
        features: Comma-separated feature names.

    Returns:
        Formatted prompt string.
    """
    return f"""Generate a list for documentation.

## Purpose: {purpose}
## Data Needed: {data_needed or "Relevant items for this list"}

## Context
- Model Type: {model_classes}
- ML Task: {ml_task_type}
- Features: {features}

CRITICAL: Only include information that is explicitly provided in the context above.
Do NOT fabricate metrics, statistics, or claim methodologies that are not mentioned.
If specific data is not available, focus on what IS known from the context.

Generate 5-10 concise, informative items using ONLY the data provided above."""


LIST_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List items",
            "minItems": 3,
            "maxItems": 15,
        }
    },
    "required": ["items"],
}
