"""LLM-based section planner for document content."""

import logging
from autodoc.core.models import (
    ContentBlock,
    ContentType,
    GenerationContext,
    SectionPlan,
    SectionSpec,
)
from autodoc.llm import LLMClient
from autodoc.llm.prompts import (
    SECTION_PLANNING_SCHEMA,
    SYSTEM_SECTION_PLANNER,
    build_section_planning_prompt,
)
from autodoc.scanning.sanitizer import ContentSanitizer

logger = logging.getLogger(__name__)


class SectionPlanner:
    """Plans section content using LLM analysis.

    For each section in the document spec, the planner determines
    what content blocks should be included (narratives, tables, charts, etc.)
    based on the available context from code and artifact scanning.
    """

    def __init__(self, llm: LLMClient, sanitizer: ContentSanitizer):
        """Initialize the section planner.

        Args:
            llm: LLM client for planning.
            sanitizer: Content sanitizer (for future use).
        """
        self.llm = llm
        self.sanitizer = sanitizer

    async def plan_section(
        self,
        section: SectionSpec,
        context: GenerationContext,
    ) -> SectionPlan:
        """Generate a content plan for a section.

        Args:
            section: Section specification from the document spec.
            context: Generation context with code and artifact information.

        Returns:
            SectionPlan with content blocks to generate.
        """
        model_suffix = f" (model: {context.model_name})" if context.model_name else ""
        logger.info(f"Planning section: {section.name}{model_suffix}")
        
        try:
            # Build context summary for the LLM
            features_preview = ", ".join(context.code_context.features[:20]) or "Unknown"
            if len(context.code_context.features) > 20:
                features_preview += f" (+{len(context.code_context.features) - 20} more)"

            model_classes = ", ".join(context.code_context.model_classes) or "Unknown"
            registered_models = ", ".join(context.artifact_context.model_names) or "None"
            data_sources = ", ".join(context.code_context.data_sources) or "Unknown"

            # Get metrics if this is a per-model section
            metrics_info = ""
            if context.model_name:
                for model in context.artifact_context.models:
                    if model.name == context.model_name:
                        if model.metrics:
                            metrics_info = f"\n- Available Metrics: {', '.join(model.metrics.keys())}"
                        break

            prompt = build_section_planning_prompt(
                section_name=section.name,
                hint=section.hint or context.hint,
                model_name=context.model_name,
                model_classes=model_classes,
                ml_task_type=context.code_context.ml_task_type or "Unknown",
                features_preview=features_preview,
                target_variable=context.code_context.target_variable or "Unknown",
                registered_models=registered_models,
                data_sources=data_sources,
                metrics_info=metrics_info,
            )

            logger.info(f"Calling LLM for section planning: {section.name}{model_suffix}")
            result = await self.llm.complete_json(
                prompt=prompt,
                schema=SECTION_PLANNING_SCHEMA,
                system=SYSTEM_SECTION_PLANNER,
            )
            logger.info(f"LLM planning completed for: {section.name}{model_suffix}")
        except Exception as e:
            logger.error(f"Failed to plan section {section.name}{model_suffix}: {e}")
            raise

        # Convert result to SectionPlan
        content_blocks = []
        for block_data in result.get("content_blocks", []):
            try:
                block_type = ContentType(block_data["type"])
            except ValueError:
                block_type = ContentType.NARRATIVE

            content_blocks.append(ContentBlock(
                type=block_type,
                purpose=block_data.get("purpose", ""),
                data_needed=block_data.get("data_needed", ""),
                specifics=block_data.get("specifics", {}),
            ))

        # Use model name in title for per-model sections
        title = result.get("section_title", section.name)
        if context.model_name and context.model_name not in title:
            title = f"{title} - {context.model_name}"

        plan = SectionPlan(
            number="",  # Will be set by orchestrator
            name=section.name,
            title=title,
            model_name=context.model_name,
            model_run_id=context.model_run_id,
            content_blocks=content_blocks,
        )
        
        logger.info(f"Successfully planned section: {section.name}{model_suffix} with {len(content_blocks)} content blocks")
        return plan
