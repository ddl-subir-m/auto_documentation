"""Content generators for different block types."""

from __future__ import annotations

import io
import logging
import re
from typing import Any, Dict, Iterable, List

from autodoc.core.exceptions import GenerationError

logger = logging.getLogger(__name__)
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
from autodoc.generation.citations import (
    CITATION_MARKER_PATTERN,
    build_code_citation_id,
    build_mlflow_citation_id,
    build_mlflow_run_citation_id,
    build_mlflow_artifact_citation_id,
    build_mlflow_summary_citation_id,
    extract_citation_ids,
    parse_citation_id,
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
        model_suffix = f" for model '{context.model_name}'" if context.model_name else ""
        logger.info(f"Generating {block.type.value}: {block.purpose}{model_suffix}")
        
        try:
            result = None
            if block.type == ContentType.NARRATIVE:
                result = await self._generate_narrative(block, context)
            elif block.type == ContentType.TABLE:
                result = await self._generate_table(block, context)
            elif block.type == ContentType.CHART:
                result = await self._generate_chart(block, context)
            elif block.type == ContentType.IMAGE:
                result = await self._generate_image(block, context)
            elif block.type in (ContentType.BULLET_LIST, ContentType.NUMBERED_LIST):
                result = await self._generate_list(block, context)
            else:
                raise GenerationError(f"Unknown content type: {block.type}")
            
            if result:
                logger.info(f"  ✓ Successfully generated {block.type.value}")
            else:
                logger.warning(f"  ⚠ No content generated for {block.type.value}")
            return result
            
        except GenerationError:
            logger.error(f"  ✗ Failed to generate {block.type.value}: {block.purpose}")
            raise
        except Exception as e:
            logger.error(f"  ✗ Unexpected error generating {block.type.value}: {e}")
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
        if context.model_run_id or context.model_name:
            for model in context.artifact_context.models:
                # Match by run_id first (more precise), fallback to name
                if (context.model_run_id and model.run_id == context.model_run_id) or \
                   (not context.model_run_id and context.model_name and model.name == context.model_name):
                    if model.metrics:
                        metrics_str = ", ".join(
                            f"{k}: {v:.4f}" for k, v in list(model.metrics.items())[:5]
                        )
                        model_info = f"\n- ACTUAL Logged Metrics (use only these): {metrics_str}"
                        has_metrics = True
                    # Include non-image artifact data for narratives
                    if model.artifact_data:
                        for artifact_path, data in model.artifact_data.items():
                            # Skip image artifacts
                            if isinstance(data, dict) and data.get("type") == "image":
                                artifact_data_str += f"\n\n## {artifact_path}: [Image Available]"
                            else:
                                artifact_data_str += f"\n\n## {artifact_path}:\n{data}"
                    break

        if not has_metrics:
            model_info = "\n- NOTE: No metrics data available from MLflow. Do not invent metrics."

        code_evidence = self._format_code_evidence(context.code_context)
        mlflow_evidence = self._format_mlflow_evidence(context)

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
            code_evidence=code_evidence,
            mlflow_evidence=mlflow_evidence,
        )

        response = await self.llm.complete(
            prompt=prompt,
            temperature=0.7,
            system=SYSTEM_NARRATIVE_WRITER,
        )

        text = response.content.strip()

        # Insert citations for quantitative values (metrics) found in the text
        text = self._insert_quantitative_citations(text, context)

        citations, citation_details = self._collect_citations_for_text(
            text, context
        )

        return GeneratedContent(
            block_type=ContentType.NARRATIVE,
            content=text,
            metadata={
                "citations": citations,
                "citation_details": citation_details,
            },
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

        if context.model_run_id or context.model_name:
            for model in context.artifact_context.models:
                # Match by run_id first (more precise), fallback to name
                if (context.model_run_id and model.run_id == context.model_run_id) or \
                   (not context.model_run_id and context.model_name and model.name == context.model_name):
                    if model.metrics:
                        metrics_info += f"\nLogged Metrics: {dict(model.metrics)}"
                        has_real_data = True
                    if model.params:
                        metrics_info += f"\nLogged Parameters: {dict(model.params)}"
                        has_real_data = True
                    # Include non-image artifact data
                    if model.artifact_data:
                        for artifact_path, data in model.artifact_data.items():
                            # Skip image artifacts
                            if isinstance(data, dict) and data.get("type") == "image":
                                continue
                            artifact_data_str += f"\n\n## {artifact_path}:\n{data}"
                            has_real_data = True
                    break

        if not has_real_data:
            metrics_info += "\nNo metrics data available from MLflow."
            metrics_info += "\nDo NOT fabricate metrics - only document what is known from code analysis."

        # Early return for metrics-based tables when no metrics are available
        if not has_real_data and self._is_metrics_table(block):
            logger.warning(f"  ⚠ Skipping metrics table for '{block.purpose}': no metrics available")
            return None

        transformations = context.code_context.transformations[:5] if context.code_context.transformations else "Unknown"

        code_evidence = self._format_code_evidence(context.code_context)
        mlflow_evidence = self._format_mlflow_evidence(context)

        prompt = build_table_prompt(
            purpose=block.purpose,
            data_needed=block.data_needed,
            features=", ".join(context.code_context.features[:30]),
            model_classes=", ".join(context.code_context.model_classes),
            transformations=str(transformations),
            hyperparameters=str(context.code_context.hyperparameters or "Unknown"),
            metrics_info=metrics_info,
            artifact_data=artifact_data_str,
            code_evidence=code_evidence,
            mlflow_evidence=mlflow_evidence,
        )

        result = await self.llm.complete_json(
            prompt=prompt,
            schema=TABLE_SCHEMA,
            system=SYSTEM_TABLE_GENERATOR,
        )

        citations, citation_details = self._collect_citations_for_table(
            result, context
        )
        result = self._strip_table_citation_markers(result)

        # Skip tables that have no real content (all "Not Available" or similar)
        if not self._table_has_real_content(result):
            logger.warning(f"  ⚠ Skipping table generation for '{block.purpose}': no real data in table")
            return None

        return GeneratedContent(
            block_type=ContentType.TABLE,
            content=result,
            metadata={
                "citations": citations,
                "citation_details": citation_details,
            },
        )

    async def _generate_chart(
        self,
        block: ContentBlock,
        context: GenerationContext,
    ) -> GeneratedContent:
        """Generate chart as PNG image bytes."""
        chart_type = block.specifics.get("chart_type", "bar")

        # Get actual metrics if available - with clear labeling and formatting
        metrics_hint = ""
        has_metrics = False
        artifact_data_str = ""
        formatted_metrics = []
        
        if context.model_run_id or context.model_name:
            for model in context.artifact_context.models:
                # Match by run_id first (more precise), fallback to name
                if (context.model_run_id and model.run_id == context.model_run_id) or \
                   (not context.model_run_id and context.model_name and model.name == context.model_name):
                    if model.metrics:
                        # Format metrics for easier chart generation
                        for key, value in model.metrics.items():
                            # Skip train metrics for performance charts (focus on test/validation)
                            if not key.startswith('train_'):
                                formatted_metrics.append(f"  - {key}: {value:.4f}")
                        
                        if formatted_metrics:
                            metrics_hint = "\n\n## ACTUAL AVAILABLE METRICS (use these exact values for the chart):\n"
                            metrics_hint += "\n".join(formatted_metrics)
                            metrics_hint += "\n\nIMPORTANT: Create a chart showing these specific metrics with their exact values."
                            has_metrics = True
                        else:
                            # All metrics were training metrics
                            metrics_hint = f"\n\n## ACTUAL AVAILABLE METRICS: {dict(model.metrics)}"
                            has_metrics = True
                    
                    # Include non-image artifact data for charts (CSV/TXT data)
                    if model.artifact_data:
                        for artifact_path, data in model.artifact_data.items():
                            # Skip image artifacts - only include parseable data
                            if isinstance(data, dict) and data.get("type") == "image":
                                continue
                            # For CSV data (list of dicts), format nicely
                            if isinstance(data, list) and len(data) > 0:
                                # Check for feature importance data
                                if 'feature' in artifact_path.lower() or 'importance' in artifact_path.lower():
                                    first_record = data[0]
                                    keys = list(first_record.keys())
                                    if len(keys) >= 2:
                                        artifact_data_str += f"\n\n## FEATURE IMPORTANCE DATA from {artifact_path}:\n"
                                        for r in data[:15]:  # Top 15 features
                                            artifact_data_str += f"  - {r[keys[0]]}: {r[keys[1]]}\n"
                                        has_metrics = True
                                else:
                                    artifact_data_str += f"\n\n## {artifact_path}:\n{data[:10]}"
                                    has_metrics = True
                            elif isinstance(data, str):
                                artifact_data_str += f"\n\n## {artifact_path}:\n{data}"
                                has_metrics = True
                    break
        else:
            # No specific model - comparative chart across all models
            all_models = context.artifact_context.models or []
            if all_models:
                metrics_hint = "\n\n## COMPARATIVE METRICS ACROSS ALL MODELS (use these exact values for the chart):\n"
                for model in all_models:
                    if model.metrics:
                        model_metrics = []
                        for key, value in model.metrics.items():
                            if not key.startswith('train_'):
                                model_metrics.append(f"    - {key}: {value:.4f}")
                        if model_metrics:
                            metrics_hint += f"\n### {model.name}:\n"
                            metrics_hint += "\n".join(model_metrics)
                            has_metrics = True

                if has_metrics:
                    metrics_hint += "\n\nIMPORTANT: Create a comparative chart showing these models side by side with their metrics."

        if not has_metrics:
            # Log that we're skipping chart generation
            logger.warning(f"  ⚠ Skipping chart generation for '{block.purpose}': no metrics available")
            return None

        code_evidence = self._format_code_evidence(context.code_context)
        mlflow_evidence = self._format_mlflow_evidence(context)

        prompt = build_chart_prompt(
            purpose=block.purpose,
            data_needed=block.data_needed,
            chart_type=chart_type,
            model_classes=", ".join(context.code_context.model_classes),
            ml_task_type=context.code_context.ml_task_type or "Unknown",
            metrics_hint=metrics_hint,
            artifact_data=artifact_data_str,
            code_evidence=code_evidence,
            mlflow_evidence=mlflow_evidence,
        )

        data = await self.llm.complete_json(
            prompt=prompt,
            schema=CHART_SCHEMA,
            system=SYSTEM_CHART_GENERATOR,
        )

        # Create the chart using matplotlib
        data = self._sanitize_chart_data(data)
        image_bytes = self._render_chart(data, chart_type)

        citations, citation_details = self._collect_citations_for_chart(
            data, context
        )

        return GeneratedContent(
            block_type=ContentType.CHART,
            content=image_bytes,
            metadata={
                "title": data.get("title", ""),
                "chart_type": chart_type,
                "chart_data": data,  # Store raw data for notebook serialization
                "citations": citations,
                "citation_details": citation_details,
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
            logger.warning(f"  ⚠ Chart has no data: labels={labels}, values={values}")
            plt.close(fig)  # Clean up the figure
            return None
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

        ax.set_xlabel(self._strip_citation_markers(data.get("xlabel", "")), fontsize=12)
        ax.set_ylabel(self._strip_citation_markers(data.get("ylabel", "")), fontsize=12)

        plt.xticks(rotation=45, ha="right")
        plt.tight_layout()

        # Save to bytes
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)

        return buf.read()

    def _sanitize_chart_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize chart labels/values to 1D numeric arrays."""
        labels = data.get("labels", [])
        values = data.get("values", [])

        if not isinstance(labels, list):
            labels = list(labels) if labels is not None else []
        if not isinstance(values, list):
            values = list(values) if values is not None else []

        if not labels and values:
            labels = [str(i + 1) for i in range(len(values))]

        if labels and values:
            min_len = min(len(labels), len(values))
            labels = labels[:min_len]
            values = values[:min_len]

        cleaned_labels: List[str] = []
        cleaned_values: List[float] = []

        for label, value in zip(labels, values):
            numeric = self._coerce_numeric(value)
            if numeric is None:
                continue
            cleaned_labels.append(str(label))
            cleaned_values.append(numeric)

        data["labels"] = cleaned_labels
        data["values"] = cleaned_values
        return data

    def _coerce_numeric(self, value: Any) -> float | None:
        """Best-effort coercion to float."""
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
        if isinstance(value, (list, tuple)):
            for item in value:
                numeric = self._coerce_numeric(item)
                if numeric is not None:
                    return numeric
            return None
        if isinstance(value, dict):
            for item in value.values():
                numeric = self._coerce_numeric(item)
                if numeric is not None:
                    return numeric
            return None
        try:
            return float(value)
        except Exception:
            return None

    async def _generate_image(
        self,
        block: ContentBlock,
        context: GenerationContext,
    ) -> GeneratedContent:
        """Generate image content from MLflow artifacts."""
        import base64

        # Find the matching model's artifact data
        for model in context.artifact_context.models:
            # Match by run_id first (more precise), fallback to name
            if (context.model_run_id and model.run_id == context.model_run_id) or \
               (not context.model_run_id and context.model_name and model.name == context.model_name):
                # Look for image artifacts
                for path, data in model.artifact_data.items():
                    if isinstance(data, dict) and data.get("type") == "image":
                        # Check if image matches the purpose
                        if self._image_matches_purpose(path, block.purpose, block.specifics):
                            # Decode base64 to bytes
                            image_bytes = base64.b64decode(data["data"])
                            citation_id = build_mlflow_artifact_citation_id(
                                model.experiment_name, model.name, path, model.run_id
                            )
                            citation_details = {
                                citation_id: {
                                    "type": "mlflow_artifact",
                                    "run_id": model.run_id,
                                    "experiment_id": model.experiment_id,
                                    "experiment_name": model.experiment_name,
                                    "run_name": model.name,
                                    "artifact_path": path,
                                }
                            }
                            return GeneratedContent(
                                block_type=ContentType.IMAGE,
                                content=image_bytes,
                                metadata={
                                    "path": path,
                                    "format": data["format"],
                                    "title": self._generate_image_title(path, block.purpose, block.specifics),
                                    "citations": [citation_id],
                                    "citation_details": citation_details,
                                },
                            )
                # If no specific match found, return the first available image
                for path, data in model.artifact_data.items():
                    if isinstance(data, dict) and data.get("type") == "image":
                        image_bytes = base64.b64decode(data["data"])
                        citation_id = build_mlflow_artifact_citation_id(
                            model.experiment_name, model.name, path, model.run_id
                        )
                        citation_details = {
                            citation_id: {
                                "type": "mlflow_artifact",
                                "run_id": model.run_id,
                                "experiment_id": model.experiment_id,
                                "experiment_name": model.experiment_name,
                                "run_name": model.name,
                                "artifact_path": path,
                            }
                        }
                        return GeneratedContent(
                            block_type=ContentType.IMAGE,
                            content=image_bytes,
                            metadata={
                                "path": path,
                                "format": data["format"],
                                "title": self._generate_image_title(path, block.purpose, block.specifics),
                                "citations": [citation_id],
                                "citation_details": citation_details,
                            },
                        )
                break

        # Handle multi-model context - search ALL models when no specific model is set
        if not context.model_run_id and not context.model_name:
            # First pass: look for images that match the purpose
            for model in context.artifact_context.models or []:
                for path, data in model.artifact_data.items():
                    if isinstance(data, dict) and data.get("type") == "image":
                        if self._image_matches_purpose(path, block.purpose, block.specifics):
                            image_bytes = base64.b64decode(data["data"])
                            citation_id = build_mlflow_artifact_citation_id(
                                model.experiment_name, model.name, path, model.run_id
                            )
                            citation_details = {
                                citation_id: {
                                    "type": "mlflow_artifact",
                                    "run_id": model.run_id,
                                    "experiment_id": model.experiment_id,
                                    "experiment_name": model.experiment_name,
                                    "run_name": model.name,
                                    "artifact_path": path,
                                }
                            }
                            return GeneratedContent(
                                block_type=ContentType.IMAGE,
                                content=image_bytes,
                                metadata={
                                    "path": path,
                                    "format": data["format"],
                                    "title": self._generate_image_title(path, block.purpose, block.specifics),
                                    "citations": [citation_id],
                                    "citation_details": citation_details,
                                },
                            )
            # Second pass: return first available image from any model
            for model in context.artifact_context.models or []:
                for path, data in model.artifact_data.items():
                    if isinstance(data, dict) and data.get("type") == "image":
                        image_bytes = base64.b64decode(data["data"])
                        citation_id = build_mlflow_artifact_citation_id(
                            model.experiment_name, model.name, path, model.run_id
                        )
                        citation_details = {
                            citation_id: {
                                "type": "mlflow_artifact",
                                "run_id": model.run_id,
                                "experiment_id": model.experiment_id,
                                "experiment_name": model.experiment_name,
                                "run_name": model.name,
                                "artifact_path": path,
                            }
                        }
                        return GeneratedContent(
                            block_type=ContentType.IMAGE,
                            content=image_bytes,
                            metadata={
                                "path": path,
                                "format": data["format"],
                                "title": self._generate_image_title(path, block.purpose, block.specifics),
                                "citations": [citation_id],
                                "citation_details": citation_details,
                            },
                        )

        logger.warning(f"  ⚠ No matching image artifact found for '{block.purpose}'")
        return None

    def _image_matches_purpose(self, path: str, purpose: str, specifics: dict) -> bool:
        """Check if an image artifact matches the intended purpose."""
        path_lower = path.lower()
        purpose_lower = purpose.lower()

        # Check if a specific image name was requested
        if specifics and specifics.get("image_name"):
            requested_name = specifics["image_name"].lower()
            if requested_name in path_lower:
                return True

        # Match based on common patterns
        keyword_mappings = {
            "feature": ["feature", "importance", "shap"],
            "importance": ["feature", "importance", "shap"],
            "confusion": ["confusion", "matrix"],
            "roc": ["roc", "auc", "curve"],
            "precision": ["precision", "recall", "pr_curve"],
            "learning": ["learning", "curve", "training"],
            "performance": ["performance", "metrics", "roc", "confusion"],
            "distribution": ["distribution", "histogram"],
            "residual": ["residual", "error"],
        }

        for keyword, patterns in keyword_mappings.items():
            if keyword in purpose_lower:
                for pattern in patterns:
                    if pattern in path_lower:
                        return True

        return False

    def _generate_image_title(self, path: str, purpose: str, specifics: dict | None = None) -> str:
        """Generate a human-readable title for an image.

        Args:
            path: Path to the image artifact.
            purpose: Purpose description for the image block.
            specifics: Optional specifics dict that may contain an LLM-generated title.

        Returns:
            A descriptive title for the image.
        """
        # Use LLM-generated title if available
        if specifics and specifics.get("title"):
            return specifics["title"]
        # Fall back to filename-based title
        filename = path.split("/")[-1].rsplit(".", 1)[0]
        # Convert underscores/hyphens to spaces and title case
        title = filename.replace("_", " ").replace("-", " ").title()
        return title

    async def _generate_list(
        self,
        block: ContentBlock,
        context: GenerationContext,
    ) -> GeneratedContent:
        """Generate a bulleted or numbered list."""
        code_evidence = self._format_code_evidence(context.code_context)
        mlflow_evidence = self._format_mlflow_evidence(context)

        prompt = build_list_prompt(
            purpose=block.purpose,
            data_needed=block.data_needed,
            model_classes=", ".join(context.code_context.model_classes),
            ml_task_type=context.code_context.ml_task_type or "Unknown",
            features=", ".join(context.code_context.features[:10]),
            code_evidence=code_evidence,
            mlflow_evidence=mlflow_evidence,
        )

        result = await self.llm.complete_json(
            prompt=prompt,
            schema=LIST_SCHEMA,
            system=SYSTEM_LIST_GENERATOR,
        )

        citations, citation_details = self._collect_citations_for_list(
            result.get("items", []), context
        )

        return GeneratedContent(
            block_type=block.type,
            content=result.get("items", []),
            metadata={
                "citations": citations,
                "citation_details": citation_details,
            },
        )

    def _format_code_evidence(self, code_context) -> str:
        if not code_context.code_evidence:
            return ""
        lines = []
        for evidence in code_context.code_evidence[:20]:
            citation_id = build_code_citation_id(evidence.path, evidence.symbol)
            snippet = " ".join(evidence.snippet.split())
            if len(snippet) > 180:
                snippet = snippet[:180] + "..."
            location = (
                f"{evidence.path}#{evidence.symbol}"
                if evidence.symbol
                else evidence.path
            )
            lines.append(
                f"- Statement: {evidence.statement}\n"
                f"  Source: {location}\n"
                f"  Citation ID: [@{citation_id}]\n"
                f"  Snippet: {snippet}"
            )
        return "## Code Evidence (cite when referenced)\n" + "\n".join(lines)

    def _format_mlflow_evidence(self, context: GenerationContext) -> str:
        model_info = self._get_model_info(context)
        if not model_info:
            return ""
        lines = []
        if model_info.metrics or model_info.params or model_info.tags:
            run_citation_id = build_mlflow_run_citation_id(
                model_info.experiment_name, model_info.name, model_info.run_id
            )
            lines.append(f"- MLflow run summary: [@{run_citation_id}]")
            if model_info.metrics:
                metric_keys = ", ".join(list(model_info.metrics.keys())[:10])
                lines.append(f"- Metrics: {metric_keys}")
            if model_info.params:
                param_keys = ", ".join(list(model_info.params.keys())[:10])
                lines.append(f"- Params: {param_keys}")
            if model_info.tags:
                tag_keys = ", ".join(list(model_info.tags.keys())[:10])
                lines.append(f"- Tags: {tag_keys}")
        for path in list(model_info.artifacts)[:10]:
            citation_id = build_mlflow_artifact_citation_id(
                model_info.experiment_name, model_info.name, path, model_info.run_id
            )
            lines.append(f"- Artifact {path}: [@{citation_id}]")
        return "## MLflow Evidence (cite when referenced)\n" + "\n".join(lines)

    def _get_model_info(self, context: GenerationContext):
        if context.model_run_id:
            for model in context.artifact_context.models:
                if model.run_id == context.model_run_id:
                    return model
        if context.model_name:
            for model in context.artifact_context.models:
                if model.name == context.model_name:
                    return model
        if len(context.artifact_context.models) == 1:
            return context.artifact_context.models[0]
        return None

    def _match_keys(self, text: str, keys: Iterable[str]) -> List[str]:
        matches = []
        lowered = text.lower()
        for key in keys:
            if not key:
                continue
            pattern = re.compile(rf"\\b{re.escape(key.lower())}\\b")
            if pattern.search(lowered):
                matches.append(key)
        return matches

    def _collapse_mlflow_citations(self, citations: List[str]) -> List[str]:
        collapsed: List[str] = []
        for citation_id in citations:
            parsed = parse_citation_id(citation_id)
            if parsed.get("type") in {"mlflow_metric", "mlflow_param", "mlflow_tag"}:
                run_id = parsed.get("run_id")
                if run_id:
                    citation_id = build_mlflow_summary_citation_id(run_id)
            collapsed.append(citation_id)
        return collapsed

    def _strip_citation_markers(self, value: str) -> str:
        cleaned = CITATION_MARKER_PATTERN.sub("", value)
        return " ".join(cleaned.split()).strip()

    def _is_metrics_table(self, block: ContentBlock) -> bool:
        """Check if this table is meant to show performance metrics."""
        purpose_lower = block.purpose.lower()
        data_needed_lower = (block.data_needed or "").lower()

        metrics_keywords = [
            "metric", "performance", "accuracy", "precision", "recall",
            "f1", "auc", "roc", "score", "evaluation", "validation",
            "test set", "train set", "confusion"
        ]

        combined = purpose_lower + " " + data_needed_lower
        return any(keyword in combined for keyword in metrics_keywords)

    def _table_has_real_content(self, table_data: dict) -> bool:
        """Check if table contains any real values (not all placeholders)."""
        placeholder_values = {
            "not available", "n/a", "na", "-", "—", "none",
            "unknown", "no data", ""
        }

        rows = table_data.get("rows", [])
        if not rows:
            return False

        for row in rows:
            if not isinstance(row, dict):
                continue
            for key, value in row.items():
                if isinstance(value, str):
                    if value.strip().lower() not in placeholder_values:
                        return True
                elif value is not None:
                    return True

        return False

    def _insert_quantitative_citations(self, text: str, context: GenerationContext) -> str:
        """Insert citations after quantitative values that match MLflow metrics.

        Only cites when actual numeric values from metrics appear in the text.
        """
        if not text:
            return text

        # Build a map of metric values to citation IDs
        value_to_citation: Dict[str, str] = {}

        for model in context.artifact_context.models or []:
            citation_id = build_mlflow_run_citation_id(
                model.experiment_name, model.name, model.run_id
            )

            # Add metrics
            for metric_name, metric_value in (model.metrics or {}).items():
                if isinstance(metric_value, (int, float)):
                    # Store multiple string representations of the value
                    # Include various precisions and rounding methods
                    for precision in [2, 3, 4, 5]:
                        # Standard formatting
                        formatted = f"{metric_value:.{precision}f}"
                        value_to_citation[formatted] = citation_id

                        # Also try rounding up/down for edge cases
                        import math
                        factor = 10 ** precision
                        rounded_up = math.ceil(metric_value * factor) / factor
                        rounded_down = math.floor(metric_value * factor) / factor
                        value_to_citation[f"{rounded_up:.{precision}f}"] = citation_id
                        value_to_citation[f"{rounded_down:.{precision}f}"] = citation_id

                    # Also store percentage format if it's a ratio
                    if 0 <= metric_value <= 1:
                        pct = metric_value * 100
                        for precision in [1, 2]:
                            value_to_citation[f"{pct:.{precision}f}%"] = citation_id
                            value_to_citation[f"{pct:.{precision}f} %"] = citation_id
                            value_to_citation[f"{pct:.{precision}f}"] = citation_id

        if not value_to_citation:
            return text

        # Sort by length descending to match longer values first (e.g., "0.9523" before "0.95")
        sorted_values = sorted(value_to_citation.keys(), key=len, reverse=True)

        # Track positions where we've already inserted citations to avoid duplicates
        cited_positions: set = set()
        result = text

        for value in sorted_values:
            citation_id = value_to_citation[value]
            # Find all occurrences of this value
            pattern = re.compile(re.escape(value) + r'(?!\d)')  # Not followed by more digits

            # Find matches and insert citations (working backwards to preserve positions)
            matches = list(pattern.finditer(result))
            for match in reversed(matches):
                end_pos = match.end()
                # Skip if we've already cited near this position
                if any(abs(end_pos - pos) < 15 for pos in cited_positions):
                    continue
                # Skip if already has a citation marker after it
                remaining = result[end_pos:end_pos + 20]
                if remaining.startswith('[@') or remaining.startswith(' [@'):
                    continue
                # Insert citation
                citation_marker = f"[@{citation_id}]"
                result = result[:end_pos] + citation_marker + result[end_pos:]
                cited_positions.add(end_pos)

        return result

    def _strip_table_citation_markers(self, table_data: Dict[str, Any]) -> Dict[str, Any]:
        if not table_data:
            return table_data
        caption = table_data.get("caption")
        if isinstance(caption, str):
            table_data["caption"] = self._strip_citation_markers(caption)
        columns = table_data.get("columns")
        if isinstance(columns, list):
            cleaned_columns = []
            for col in columns:
                if isinstance(col, str):
                    cleaned_columns.append(self._strip_citation_markers(col))
                else:
                    cleaned_columns.append(col)
            table_data["columns"] = cleaned_columns
        rows = table_data.get("rows")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for key, value in row.items():
                    if isinstance(value, str):
                        row[key] = self._strip_citation_markers(value)
        return table_data

    def _collect_mlflow_details(self, context: GenerationContext) -> dict:
        model_info = self._get_model_info(context)
        details = {}

        if model_info:
            # Single model context
            if model_info.metrics or model_info.params or model_info.tags:
                cid = build_mlflow_run_citation_id(
                    model_info.experiment_name, model_info.name, model_info.run_id
                )
                details[cid] = {
                    "type": "mlflow_summary",
                    "run_id": model_info.run_id,
                    "experiment_id": model_info.experiment_id,
                    "experiment_name": model_info.experiment_name,
                    "run_name": model_info.name,
                }
            for path in model_info.artifacts:
                cid = build_mlflow_artifact_citation_id(
                    model_info.experiment_name, model_info.name, path, model_info.run_id
                )
                details[cid] = {
                    "type": "mlflow_artifact",
                    "run_id": model_info.run_id,
                    "experiment_id": model_info.experiment_id,
                    "experiment_name": model_info.experiment_name,
                    "run_name": model_info.name,
                    "artifact_path": path,
                }
        else:
            # Multi-model context - collect details for all models
            for model in context.artifact_context.models or []:
                if model.metrics or model.params or model.tags:
                    cid = build_mlflow_run_citation_id(
                        model.experiment_name, model.name, model.run_id
                    )
                    details[cid] = {
                        "type": "mlflow_summary",
                        "run_id": model.run_id,
                        "experiment_id": model.experiment_id,
                        "experiment_name": model.experiment_name,
                        "run_name": model.name,
                    }
                for path in model.artifacts:
                    cid = build_mlflow_artifact_citation_id(
                        model.experiment_name, model.name, path, model.run_id
                    )
                    details[cid] = {
                        "type": "mlflow_artifact",
                        "run_id": model.run_id,
                        "experiment_id": model.experiment_id,
                        "experiment_name": model.experiment_name,
                        "run_name": model.name,
                        "artifact_path": path,
                    }

        return details

    def _collect_code_details(self, context: GenerationContext) -> dict:
        details = {}
        for evidence in context.code_context.code_evidence:
            cid = build_code_citation_id(evidence.path, evidence.symbol)
            details[cid] = {
                "type": "code_file",
                "code_path": evidence.path,
                "code_symbol": evidence.symbol,
                "evidence_text": evidence.statement,
            }
        return details

    def _collect_citations_for_text(self, text: str, context: GenerationContext):
        """Collect only citations explicitly placed by LLM with [@citation_id] markers."""
        details = {}

        # Build details lookup for all potential citations
        details.update(self._collect_mlflow_details(context))
        details.update(self._collect_code_details(context))

        # Only extract citations that were explicitly placed in the text
        marker_ids = extract_citation_ids(text)

        # De-dupe while preserving order
        deduped = []
        seen = set()
        for cid in marker_ids:
            if cid in seen:
                continue
            seen.add(cid)
            deduped.append(cid)

        return deduped, details

    def _collect_citations_for_table(self, table_data: Dict[str, Any], context: GenerationContext):
        text_parts = []
        caption = table_data.get("caption", "")
        if caption:
            text_parts.append(caption)
        columns = table_data.get("columns", [])
        text_parts.extend([str(c) for c in columns])
        rows = table_data.get("rows", [])
        for row in rows:
            if isinstance(row, dict):
                text_parts.extend([str(v) for v in row.values()])
        return self._collect_citations_for_text(" ".join(text_parts), context)

    def _collect_citations_for_chart(self, chart_data: Dict[str, Any], context: GenerationContext):
        parts = [str(chart_data.get("title", ""))]
        parts.extend([str(v) for v in chart_data.get("labels", [])])
        return self._collect_citations_for_text(" ".join(parts), context)

    def _collect_citations_for_list(self, items: List[str], context: GenerationContext):
        return self._collect_citations_for_text(" ".join(items), context)
