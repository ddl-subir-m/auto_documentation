"""Pipeline orchestrator for document generation."""

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

from autodoc.core.models import (
    ArtifactContext,
    CodeContext,
    ContentBlock,
    ContentType,
    DocumentSpec,
    GeneratedContent,
    GenerationContext,
    SectionPlan,
    SectionResult,
    SectionSpec,
)
from autodoc.generation import ContentGenerator, DocumentBuilder, NotebookBuilder, SectionPlanner
from autodoc.llm import LLMClient
from autodoc.scanning import ArtifactScanner, CodeScanner, ContentSanitizer


# Type alias for progress callback
ProgressCallback = Callable[[str, float], None]


class Orchestrator:
    """Coordinates the document generation pipeline.

    Executes the 4-phase pipeline:
    1. Scan - Analyze code and artifacts
    2. Plan - Plan section content
    3. Generate - Generate content blocks
    4. Build - Assemble Word document
    """

    def __init__(
        self,
        llm: LLMClient,
        sanitizer: ContentSanitizer,
        code_root: Path = Path("/mnt/code"),
        output_dir: Path = Path("./output"),
        mlflow_tracking_uri: Optional[str] = None,
        parallel_workers: int = 4,
        max_files: int = 50,
        max_file_size: int = 50000,
        generate_notebook: bool = False,
        notebook_path: Optional[Path] = None,
        experiment_names: Optional[List[str]] = None,
        model_names: Optional[List[str]] = None,
        latest_only: bool = False,
        disable_project_filtering: bool = False,
    ):
        """Initialize the orchestrator.

        Args:
            llm: LLM client for analysis and generation.
            sanitizer: Content sanitizer for security.
            code_root: Root directory of codebase to analyze.
            output_dir: Output directory for generated documents.
            mlflow_tracking_uri: MLflow tracking server URI.
            parallel_workers: Number of parallel content generation workers.
            max_files: Maximum files to scan.
            max_file_size: Maximum file size in characters.
            generate_notebook: Whether to also generate an editable Jupyter notebook.
            notebook_path: Custom path for the generated notebook. If not provided,
                uses <output_dir>/model_docs_notebook.ipynb.
            experiment_names: List of experiment names to include.
            model_names: List of specific model names to include.
            latest_only: Only include the latest version of each model.
            disable_project_filtering: Disable automatic Domino project filtering.
        """
        self.llm = llm
        self.sanitizer = sanitizer
        self.code_root = code_root
        self.output_dir = output_dir
        self.generate_notebook = generate_notebook
        self.notebook_path = notebook_path

        # Initialize components
        self.code_scanner = CodeScanner(
            llm=llm,
            sanitizer=sanitizer,
            code_root=code_root,
            max_files=max_files,
            max_file_size=max_file_size,
        )
        self.artifact_scanner = ArtifactScanner(
            tracking_uri=mlflow_tracking_uri,
            experiment_names=experiment_names,
            model_names=model_names,
            latest_only=latest_only,
            disable_project_filtering=disable_project_filtering,
        )
        self.planner = SectionPlanner(llm=llm, sanitizer=sanitizer)
        self.generator = ContentGenerator(llm=llm)
        self.builder = DocumentBuilder(output_dir=output_dir)

        # Optional notebook builder
        if generate_notebook:
            self.notebook_builder = NotebookBuilder(
                output_dir=output_dir,
                notebook_path=notebook_path,
            )

        # Semaphore for limiting concurrent LLM calls
        self.semaphore = asyncio.Semaphore(parallel_workers)

    async def generate(
        self,
        spec: DocumentSpec,
        on_progress: Optional[ProgressCallback] = None,
    ) -> Path:
        """Execute the full document generation pipeline.

        Args:
            spec: Document specification.
            on_progress: Optional callback for progress updates.
                        Called with (phase_name, progress_fraction).

        Returns:
            Path to the generated Word document.
        """
        # Phase 1: Scan
        if on_progress:
            on_progress("Scanning", 0.0)

        code_ctx, artifact_ctx = await asyncio.gather(
            self.code_scanner.scan(),
            self.artifact_scanner.scan(),
        )

        if on_progress:
            on_progress("Scanning", 1.0)

        # Phase 2: Plan
        if on_progress:
            on_progress("Planning", 0.0)

        plans = await self._plan_all_sections(spec, code_ctx, artifact_ctx, on_progress)

        if on_progress:
            on_progress("Planning", 1.0)

        # Phase 3: Generate
        if on_progress:
            on_progress("Generating", 0.0)

        results = await self._generate_all_content(
            plans, code_ctx, artifact_ctx, on_progress
        )

        if on_progress:
            on_progress("Generating", 1.0)

        # Phase 4: Build Word document
        if on_progress:
            on_progress("Building", 0.0)

        output_path = await self.builder.build(spec, results)

        if on_progress:
            on_progress("Building", 0.5)

        # Phase 4b: Also build notebook if requested
        if self.generate_notebook:
            await self.notebook_builder.build(spec, results)

        # Save results to cache for --notebook-from-cache rebuilds
        self._save_results_cache(spec, results)

        if on_progress:
            on_progress("Building", 1.0)

        return output_path

    async def regenerate_notebook(
        self,
        on_progress: Optional[ProgressCallback] = None,
    ) -> Path:
        """Regenerate notebook from cached results without running full pipeline.

        Args:
            on_progress: Optional callback for progress updates.

        Returns:
            Path to the generated notebook.

        Raises:
            FileNotFoundError: If no cached results exist.
        """
        if on_progress:
            on_progress("Loading cache", 0.0)

        # Load cached results
        spec, results = self._load_results_cache()

        if on_progress:
            on_progress("Loading cache", 1.0)

        # Build notebook
        if on_progress:
            on_progress("Building notebook", 0.0)

        if not hasattr(self, "notebook_builder"):
            self.notebook_builder = NotebookBuilder(
                output_dir=self.output_dir,
                notebook_path=self.notebook_path if hasattr(self, "notebook_path") else None,
            )

        notebook_path = await self.notebook_builder.build(spec, results)

        if on_progress:
            on_progress("Building notebook", 1.0)

        return notebook_path

    def _get_cache_path(self) -> Path:
        """Get path to the results cache file."""
        return self.output_dir / ".autodoc_cache.json"

    def _save_results_cache(
        self, spec: DocumentSpec, results: List[SectionResult]
    ) -> None:
        """Save generation results to cache for later notebook regeneration."""
        cache_data = {
            "spec": {
                "title": spec.title,
                "authors": spec.authors,
                "sections": [
                    {"name": s.name, "per_model": s.per_model, "hint": s.hint}
                    for s in spec.sections
                ],
                "hints": spec.hints,
            },
            "results": [self._serialize_section_result(r) for r in results],
        }

        cache_path = self._get_cache_path()
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2)

    def _load_results_cache(self) -> tuple[DocumentSpec, List[SectionResult]]:
        """Load generation results from cache.

        Returns:
            Tuple of (DocumentSpec, List[SectionResult]).

        Raises:
            FileNotFoundError: If cache file doesn't exist.
        """
        cache_path = self._get_cache_path()
        if not cache_path.exists():
            raise FileNotFoundError(
                f"No cached results found at {cache_path}. "
                "Run full generation first with --notebook flag."
            )

        with open(cache_path, "r", encoding="utf-8") as f:
            cache_data = json.load(f)

        # Reconstruct DocumentSpec
        spec_data = cache_data["spec"]
        spec = DocumentSpec(
            title=spec_data["title"],
            authors=spec_data["authors"],
            sections=[
                SectionSpec(name=s["name"], per_model=s["per_model"], hint=s.get("hint"))
                for s in spec_data["sections"]
            ],
            hints=spec_data.get("hints", {}),
        )

        # Reconstruct SectionResults
        results = [
            self._deserialize_section_result(r) for r in cache_data["results"]
        ]

        return spec, results

    def _serialize_section_result(self, result: SectionResult) -> Dict[str, Any]:
        """Serialize a SectionResult to JSON-compatible dict."""
        return {
            "plan": {
                "number": result.plan.number,
                "name": result.plan.name,
                "title": result.plan.title,
                "model_name": result.plan.model_name,
                "content_blocks": [
                    {
                        "type": b.type.value,
                        "purpose": b.purpose,
                        "data_needed": b.data_needed,
                        "specifics": b.specifics,
                        "priority": b.priority,
                    }
                    for b in result.plan.content_blocks
                ],
            },
            "contents": [self._serialize_content(c) for c in result.contents],
            "errors": result.errors,
        }

    def _serialize_content(self, content: GeneratedContent) -> Dict[str, Any]:
        """Serialize GeneratedContent to JSON-compatible dict."""
        serialized = {
            "block_type": content.block_type.value,
            "metadata": content.metadata,
        }

        # Handle different content types
        if content.block_type == ContentType.CHART:
            # Encode bytes as base64
            if isinstance(content.content, bytes):
                serialized["content"] = base64.b64encode(content.content).decode("ascii")
                serialized["content_encoding"] = "base64"
            else:
                serialized["content"] = content.content
        else:
            serialized["content"] = content.content

        return serialized

    def _deserialize_section_result(self, data: Dict[str, Any]) -> SectionResult:
        """Deserialize a SectionResult from JSON dict."""
        plan_data = data["plan"]
        plan = SectionPlan(
            number=plan_data["number"],
            name=plan_data["name"],
            title=plan_data["title"],
            model_name=plan_data.get("model_name"),
            content_blocks=[
                ContentBlock(
                    type=ContentType(b["type"]),
                    purpose=b["purpose"],
                    data_needed=b.get("data_needed", ""),
                    specifics=b.get("specifics", {}),
                    priority=b.get("priority", "required"),
                )
                for b in plan_data.get("content_blocks", [])
            ],
        )

        contents = [self._deserialize_content(c) for c in data["contents"]]

        return SectionResult(
            plan=plan,
            contents=contents,
            errors=data.get("errors", []),
        )

    def _deserialize_content(self, data: Dict[str, Any]) -> GeneratedContent:
        """Deserialize GeneratedContent from JSON dict."""
        block_type = ContentType(data["block_type"])
        content = data["content"]

        # Decode base64 for chart content
        if data.get("content_encoding") == "base64":
            content = base64.b64decode(content)

        return GeneratedContent(
            block_type=block_type,
            content=content,
            metadata=data.get("metadata", {}),
        )

    async def _plan_all_sections(
        self,
        spec: DocumentSpec,
        code_ctx: CodeContext,
        artifact_ctx: ArtifactContext,
        on_progress: Optional[ProgressCallback] = None,
    ) -> List[SectionPlan]:
        """Plan all sections in the document."""
        plans: List[SectionPlan] = []
        section_num = 1
        
        # Calculate total number of planning operations for accurate progress
        total_planning_operations = 0
        for section in spec.sections:
            if section.per_model:
                models = artifact_ctx.models or []
                total_planning_operations += max(1, len(models))  # At least 1 for generic section
            else:
                total_planning_operations += 1
        
        logger.info(f"Planning {total_planning_operations} sections/subsections across {len(spec.sections)} document sections")
        completed_operations = 0

        for i, section in enumerate(spec.sections):
            if section.per_model:
                # Create a subsection for each registered model
                models = artifact_ctx.models or []

                if not models:
                    # No models found, create a generic section
                    context = GenerationContext(
                        code_context=code_ctx,
                        artifact_context=artifact_ctx,
                        section_name=section.name,
                        hint=spec.hints.get(section.name),
                    )
                    plan = await self.planner.plan_section(section, context)
                    plan.number = str(section_num)
                    plans.append(plan)
                    completed_operations += 1
                else:
                    for j, model in enumerate(models, 1):
                        context = GenerationContext(
                            code_context=code_ctx,
                            artifact_context=artifact_ctx,
                            section_name=section.name,
                            model_name=model.name,
                            hint=spec.hints.get(section.name),
                        )
                        plan = await self.planner.plan_section(section, context)
                        plan.number = f"{section_num}.{j}"
                        plans.append(plan)
                        completed_operations += 1
                        
                        # Update progress after each model subsection
                        if on_progress:
                            progress = completed_operations / total_planning_operations
                            on_progress("Planning", progress)
            else:
                # Regular section
                context = GenerationContext(
                    code_context=code_ctx,
                    artifact_context=artifact_ctx,
                    section_name=section.name,
                    hint=spec.hints.get(section.name),
                )
                plan = await self.planner.plan_section(section, context)
                plan.number = str(section_num)
                plans.append(plan)
                completed_operations += 1
                
                # Update progress after each regular section
                if on_progress:
                    progress = completed_operations / total_planning_operations
                    on_progress("Planning", progress)

            section_num += 1

        return plans

    async def _generate_all_content(
        self,
        plans: List[SectionPlan],
        code_ctx: CodeContext,
        artifact_ctx: ArtifactContext,
        on_progress: Optional[ProgressCallback] = None,
    ) -> List[SectionResult]:
        """Generate content for all sections in parallel."""

        async def generate_section(plan: SectionPlan) -> SectionResult:
            """Generate content for a single section."""
            async with self.semaphore:
                context = GenerationContext(
                    code_context=code_ctx,
                    artifact_context=artifact_ctx,
                    section_name=plan.name,
                    model_name=plan.model_name,
                )

                contents = []
                errors = []

                for block in plan.content_blocks:
                    try:
                        content = await self.generator.generate(block, context)
                        contents.append(content)
                    except Exception as e:
                        error_msg = f"{block.type.value}: {str(e)}"
                        errors.append(error_msg)

                return SectionResult(
                    plan=plan,
                    contents=contents,
                    errors=errors,
                )

        # Create tasks for all sections
        tasks = [generate_section(plan) for plan in plans]

        # Execute with progress tracking
        results: List[SectionResult] = []
        completed = 0

        for coro in asyncio.as_completed(tasks):
            result = await coro
            results.append(result)
            completed += 1

            if on_progress:
                progress = completed / len(tasks)
                on_progress("Generating", progress)

        # Sort results back to original order
        plan_order = {plan.number: i for i, plan in enumerate(plans)}
        results.sort(key=lambda r: plan_order.get(r.plan.number, 999))

        return results
