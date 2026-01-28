"""Content generators for different block types."""

import io
from typing import Any, Dict

from autodoc.core.exceptions import GenerationError
from autodoc.core.models import (
    ContentBlock,
    ContentType,
    GeneratedContent,
    GenerationContext,
)
from autodoc.llm import LLMClient
from autodoc.llm.prompts import (
    CHART_SCHEMA,
    LIST_SCHEMA,
    SYSTEM_CHART_GENERATOR,
    SYSTEM_LIST_GENERATOR,
    SYSTEM_NARRATIVE_WRITER,
    SYSTEM_TABLE_GENERATOR,
    TABLE_SCHEMA,
    build_chart_prompt,
    build_list_prompt,
    build_narrative_prompt,
    build_table_prompt,
)


class ContentGenerator:
    """Generates content for document sections.

    Supports generating narratives, tables, charts, and lists
    based on the content block type and available context.
    """

    def __init__(self, llm: LLMClient):
        """Initialize the content generator.

        Args:
            llm: LLM client for content generation.
        """
        self.llm = llm

    async def generate(
        self,
        block: ContentBlock,
        context: GenerationContext,
    ) -> GeneratedContent:
        """Generate content for a content block.

        Args:
            block: Content block specification.
            context: Generation context with code and artifact info.

        Returns:
            GeneratedContent with the generated content.

        Raises:
            GenerationError: If generation fails.
        """
        try:
            if block.type == ContentType.NARRATIVE:
                return await self._generate_narrative(block, context)
            elif block.type == ContentType.TABLE:
                return await self._generate_table(block, context)
            elif block.type == ContentType.CHART:
                return await self._generate_chart(block, context)
            elif block.type in (ContentType.BULLET_LIST, ContentType.NUMBERED_LIST):
                return await self._generate_list(block, context)
            else:
                raise GenerationError(f"Unknown content type: {block.type}")
        except GenerationError:
            raise
        except Exception as e:
            raise GenerationError(f"Content generation failed: {e}") from e

    async def _generate_narrative(
        self,
        block: ContentBlock,
        context: GenerationContext,
    ) -> GeneratedContent:
        """Generate narrative text (paragraphs)."""
        # Build context for the narrative with clear labeling
        model_info = ""
        has_metrics = False
        artifact_data_str = ""
        if context.model_name:
            for model in context.artifact_context.models:
                if model.name == context.model_name:
                    if model.metrics:
                        metrics_str = ", ".join(
                            f"{k}: {v:.4f}" for k, v in list(model.metrics.items())[:5]
                        )
                        model_info = f"\n- ACTUAL Logged Metrics (use only these): {metrics_str}"
                        has_metrics = True
                    # Include all artifact data for narratives
                    if model.artifact_data:
                        for artifact_path, data in model.artifact_data.items():
                            artifact_data_str += f"\n\n## {artifact_path}:\n{data}"
                    break

        if not has_metrics:
            model_info = "\n- NOTE: No metrics data available from MLflow. Do not invent metrics."

        prompt = build_narrative_prompt(
            section_name=context.section_name,
            purpose=block.purpose,
            data_needed=block.data_needed,
            model_classes=", ".join(context.code_context.model_classes) or "Unknown",
            ml_task_type=context.code_context.ml_task_type or "Unknown",
            target_variable=context.code_context.target_variable or "Unknown",
            features=", ".join(context.code_context.features[:15]) or "Unknown",
            data_sources=", ".join(context.code_context.data_sources) or "Unknown",
            model_name=context.model_name,
            model_info=model_info,
            insights=context.code_context.insights,
            artifact_data=artifact_data_str,
        )

        response = await self.llm.complete(
            prompt=prompt,
            temperature=0.7,
            system=SYSTEM_NARRATIVE_WRITER,
        )

        return GeneratedContent(
            block_type=ContentType.NARRATIVE,
            content=response.content.strip(),
        )

    async def _generate_table(
        self,
        block: ContentBlock,
        context: GenerationContext,
    ) -> GeneratedContent:
        """Generate table data."""
        # Get real metrics if available - with clear labeling
        metrics_info = "\n\n## ACTUAL AVAILABLE DATA (use only these values):"
        has_real_data = False
        artifact_data_str = ""

        if context.model_name:
            for model in context.artifact_context.models:
                if model.name == context.model_name:
                    if model.metrics:
                        metrics_info += f"\nLogged Metrics: {dict(model.metrics)}"
                        has_real_data = True
                    if model.params:
                        metrics_info += f"\nLogged Parameters: {dict(model.params)}"
                        has_real_data = True
                    # Include all artifact data
                    if model.artifact_data:
                        for artifact_path, data in model.artifact_data.items():
                            artifact_data_str += f"\n\n## {artifact_path}:\n{data}"
                            has_real_data = True
                    break

        if not has_real_data:
            metrics_info += "\nNo metrics data available from MLflow."
            metrics_info += "\nDo NOT fabricate metrics - only document what is known from code analysis."

        transformations = context.code_context.transformations[:5] if context.code_context.transformations else "Unknown"

        prompt = build_table_prompt(
            purpose=block.purpose,
            data_needed=block.data_needed,
            features=", ".join(context.code_context.features[:30]),
            model_classes=", ".join(context.code_context.model_classes),
            transformations=str(transformations),
            hyperparameters=str(context.code_context.hyperparameters or "Unknown"),
            metrics_info=metrics_info,
            artifact_data=artifact_data_str,
        )

        result = await self.llm.complete_json(
            prompt=prompt,
            schema=TABLE_SCHEMA,
            system=SYSTEM_TABLE_GENERATOR,
        )

        return GeneratedContent(
            block_type=ContentType.TABLE,
            content=result,
        )

    async def _generate_chart(
        self,
        block: ContentBlock,
        context: GenerationContext,
    ) -> GeneratedContent:
        """Generate chart as PNG image bytes."""
        chart_type = block.specifics.get("chart_type", "bar")

        # Get actual metrics if available - with clear labeling
        metrics_hint = ""
        has_metrics = False
        artifact_data_str = ""
        if context.model_name:
            for model in context.artifact_context.models:
                if model.name == context.model_name:
                    if model.metrics:
                        metrics_hint = f"\n\n## ACTUAL AVAILABLE METRICS (use only these values): {dict(model.metrics)}"
                        has_metrics = True
                    # Include all artifact data for charts
                    if model.artifact_data:
                        for artifact_path, data in model.artifact_data.items():
                            artifact_data_str += f"\n\n## {artifact_path}:\n{data}"
                            has_metrics = True
                    break

        if not has_metrics:
            metrics_hint = "\n\nNOTE: No metrics data available. Do NOT fabricate values for the chart."

        prompt = build_chart_prompt(
            purpose=block.purpose,
            data_needed=block.data_needed,
            chart_type=chart_type,
            model_classes=", ".join(context.code_context.model_classes),
            ml_task_type=context.code_context.ml_task_type or "Unknown",
            metrics_hint=metrics_hint,
            artifact_data=artifact_data_str,
        )

        data = await self.llm.complete_json(
            prompt=prompt,
            schema=CHART_SCHEMA,
            system=SYSTEM_CHART_GENERATOR,
        )

        # Create the chart using matplotlib
        image_bytes = self._render_chart(data, chart_type)

        return GeneratedContent(
            block_type=ContentType.CHART,
            content=image_bytes,
            metadata={
                "title": data.get("title", ""),
                "chart_type": chart_type,
                "chart_data": data,  # Store raw data for notebook serialization
            },
        )

    def _render_chart(self, data: Dict[str, Any], chart_type: str) -> bytes:
        """Render chart data to PNG bytes."""
        import matplotlib

        matplotlib.use("Agg")  # Non-interactive backend
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(10, 6))

        labels = data.get("labels", [])
        values = data.get("values", [])

        if not labels or not values:
            # Return empty chart if no data
            ax.text(0.5, 0.5, "No data available", ha="center", va="center")
        elif chart_type == "bar":
            ax.bar(labels, values, color="#4361ee")
        elif chart_type == "line":
            ax.plot(labels, values, marker="o", color="#4361ee", linewidth=2)
        elif chart_type == "scatter":
            x = list(range(len(values)))
            ax.scatter(x, values, color="#4361ee", s=100)
            ax.set_xticks(x)
            ax.set_xticklabels(labels)
        else:
            ax.bar(labels, values, color="#4361ee")

        ax.set_title(data.get("title", ""), fontsize=14, fontweight="bold")
        ax.set_xlabel(data.get("xlabel", ""), fontsize=12)
        ax.set_ylabel(data.get("ylabel", ""), fontsize=12)

        plt.xticks(rotation=45, ha="right")
        plt.tight_layout()

        # Save to bytes
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)

        return buf.read()

    async def _generate_list(
        self,
        block: ContentBlock,
        context: GenerationContext,
    ) -> GeneratedContent:
        """Generate a bulleted or numbered list."""
        prompt = build_list_prompt(
            purpose=block.purpose,
            data_needed=block.data_needed,
            model_classes=", ".join(context.code_context.model_classes),
            ml_task_type=context.code_context.ml_task_type or "Unknown",
            features=", ".join(context.code_context.features[:10]),
        )

        result = await self.llm.complete_json(
            prompt=prompt,
            schema=LIST_SCHEMA,
            system=SYSTEM_LIST_GENERATOR,
        )

        return GeneratedContent(
            block_type=block.type,
            content=result.get("items", []),
        )
