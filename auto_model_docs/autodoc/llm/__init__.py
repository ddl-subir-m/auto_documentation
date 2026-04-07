"""LLM client, prompts, and caching."""

from autodoc.llm.client import LLMClient, LLMResponse
from autodoc.llm.prompts import (
    # System prompts
    SYSTEM_CHART_GENERATOR,
    SYSTEM_CODE_ANALYZER,
    SYSTEM_LIST_GENERATOR,
    SYSTEM_NARRATIVE_WRITER,
    SYSTEM_SECTION_PLANNER,
    SYSTEM_TABLE_GENERATOR,
    # Schemas
    CHART_SCHEMA,
    CODE_ANALYSIS_SCHEMA,
    LIST_SCHEMA,
    SECTION_PLANNING_SCHEMA,
    TABLE_SCHEMA,
    # Prompt builders
    build_chart_prompt,
    build_code_analysis_prompt,
    build_list_prompt,
    build_narrative_prompt,
    build_section_planning_prompt,
    build_table_prompt,
)

__all__ = [
    # Client
    "LLMClient",
    "LLMResponse",
    # System prompts
    "SYSTEM_CHART_GENERATOR",
    "SYSTEM_CODE_ANALYZER",
    "SYSTEM_LIST_GENERATOR",
    "SYSTEM_NARRATIVE_WRITER",
    "SYSTEM_SECTION_PLANNER",
    "SYSTEM_TABLE_GENERATOR",
    # Schemas
    "CHART_SCHEMA",
    "CODE_ANALYSIS_SCHEMA",
    "LIST_SCHEMA",
    "SECTION_PLANNING_SCHEMA",
    "TABLE_SCHEMA",
    # Prompt builders
    "build_chart_prompt",
    "build_code_analysis_prompt",
    "build_list_prompt",
    "build_narrative_prompt",
    "build_section_planning_prompt",
    "build_table_prompt",
]
