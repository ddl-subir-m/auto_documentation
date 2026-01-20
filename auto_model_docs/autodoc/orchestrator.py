"""Pipeline orchestrator for document generation."""

import asyncio
from pathlib import Path
from typing import Callable, List, Optional

from autodoc.core.models import (
    ArtifactContext,
    CodeContext,
    DocumentSpec,
    GenerationContext,
    SectionPlan,
    SectionResult,
)
from autodoc.generation import ContentGenerator, DocumentBuilder, SectionPlanner
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
        output_dir: Path = Path("/mnt/artifacts"),
        mlflow_tracking_uri: Optional[str] = None,
        parallel_workers: int = 4,
        max_files: int = 50,
        max_file_size: int = 50000,
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
        """
        self.llm = llm
        self.sanitizer = sanitizer
        self.code_root = code_root
        self.output_dir = output_dir

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
        )
        self.planner = SectionPlanner(llm=llm, sanitizer=sanitizer)
        self.generator = ContentGenerator(llm=llm)
        self.builder = DocumentBuilder(output_dir=output_dir)

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

        # Phase 4: Build
        if on_progress:
            on_progress("Building", 0.0)

        output_path = await self.builder.build(spec, results)

        if on_progress:
            on_progress("Building", 1.0)

        return output_path

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
        total_sections = len(spec.sections)

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

            section_num += 1

            # Update progress
            if on_progress:
                progress = (i + 1) / total_sections
                on_progress("Planning", progress)

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
