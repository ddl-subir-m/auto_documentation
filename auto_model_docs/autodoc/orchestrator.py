"""Pipeline orchestrator for document generation."""

import asyncio
import time
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
    LanguageProfile,
    PYTHON_PROFILE,
    SectionPlan,
    SectionResult,
    SectionSpec,
    detect_language,
)
from autodoc.generation import ContentGenerator, DocumentBuilder, NotebookBuilder, SectionPlanner
from autodoc.llm import LLMClient
from autodoc.scanning import ArtifactScanner, CodeScanner, ContentSanitizer


# Type alias for progress callback
ProgressCallback = Callable[[str, float], None]
StatusCallback = Callable[[str], None]


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
        planning_workers: int = 1,
        max_files: int = 100,
        max_file_size: int = 15000,
        generate_notebook: bool = False,
        exclude_patterns: Optional[List[str]] = None,
        max_selected_files: int = 15,
        batch_size: int = 4,
        analysis_timeout: float = 90.0,
        scan_retries: int = 2,
        scan_workers: int = 2,
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
            planning_workers: Number of parallel planning workers.
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
        self.code_root = code_root
        self.output_dir = output_dir
        self.generate_notebook = generate_notebook
        self.notebook_path = notebook_path

        # Detect language before creating sanitizer and scanner
        detected_profile, detected_count = detect_language(code_root)
        self.language_profile: LanguageProfile = detected_profile or PYTHON_PROFILE
        self.detected_file_count: int = detected_count

        if detected_profile:
            logger.info(
                f"Detected language: {detected_profile.display_name} "
                f"({detected_count} files)"
            )
        else:
            logger.info("No supported files detected, defaulting to Python")

        # Create sanitizer with language-specific secret patterns
        # Separate regex patterns from file-name patterns
        extra_regex = [
            p for p in self.language_profile.secret_patterns
            if any(c in p for c in r"()*+?[]{}|\\")
        ]
        extra_files = [
            p for p in self.language_profile.secret_patterns
            if not any(c in p for c in r"()*+?[]{}|\\")
        ]
        self.sanitizer = ContentSanitizer(
            extra_patterns=extra_regex or None,
            extra_sensitive_files=extra_files or None,
        ) if (extra_regex or extra_files) else sanitizer

        # Initialize components with detected profile
        self.code_scanner = CodeScanner(
            llm=llm,
            sanitizer=self.sanitizer,
            code_root=code_root,
            max_files=max_files,
            max_file_size=max_file_size,
            profile=self.language_profile,
            exclude_patterns=exclude_patterns,
            max_selected_files=max_selected_files,
            batch_size=batch_size,
            analysis_timeout=analysis_timeout,
            scan_retries=scan_retries,
            scan_workers=scan_workers,
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

        # Semaphore for limiting concurrent LLM calls during content generation
        self.semaphore = asyncio.Semaphore(parallel_workers)
        # Semaphore for limiting concurrent LLM calls during planning
        self.planning_semaphore = asyncio.Semaphore(planning_workers)

    async def generate(
        self,
        spec: DocumentSpec,
        on_progress: Optional[ProgressCallback] = None,
        on_status: Optional[StatusCallback] = None,
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

        # Track progress from both scanners
        # Weight: artifact scanner = 60%, code scanner = 40% (artifact is typically slower)
        artifact_weight = 0.6
        code_weight = 0.4
        artifact_progress = 0.0
        code_progress = 0.0

        def update_scanning_progress() -> None:
            """Combine progress from both scanners and report."""
            if on_progress:
                combined = (artifact_progress * artifact_weight) + (
                    code_progress * code_weight
                )
                on_progress("Scanning", combined)

        def on_artifact_progress(progress: float) -> None:
            """Callback for artifact scanner progress."""
            nonlocal artifact_progress
            artifact_progress = progress
            update_scanning_progress()

        def on_code_progress(progress: float) -> None:
            """Callback for code scanner progress."""
            nonlocal code_progress
            code_progress = progress
            update_scanning_progress()

        code_task = asyncio.create_task(
            self.code_scanner.scan(on_progress=on_code_progress)
        )
        artifact_start = time.monotonic()
        if on_status:
            on_status("Scanning MLflow artifacts...")
        artifact_task = asyncio.create_task(
            self.artifact_scanner.scan(on_progress=on_artifact_progress)
        )
        try:
            artifact_ctx = await artifact_task
            if on_status:
                on_status(
                    "MLflow artifact scan completed "
                    f"in {time.monotonic() - artifact_start:.1f}s."
                )
            code_ctx = await code_task
        except (asyncio.CancelledError, Exception):
            for t in (code_task, artifact_task):
                if not t.done():
                    t.cancel()
            await asyncio.gather(code_task, artifact_task, return_exceptions=True)
            raise

        # Log scan summary for debugging
        logger.info("=== SCAN SUMMARY ===")
        logger.info(f"Models found: {len(artifact_ctx.models)}")
        for m in artifact_ctx.models:
            metrics_list = list(m.metrics.keys()) if m.metrics else []
            artifacts_list = m.artifacts if m.artifacts else []
            logger.info(f"  {m.name} v{m.version}: metrics={metrics_list}, artifacts={len(artifacts_list)}")

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
            # Sync notebook filename with docx when no custom path was provided
            if not self.notebook_builder.notebook_path:
                # output_path is a dataset-relative string (e.g. "docs/model_docs_*.docx")
                base = output_path.rsplit(".", 1)[0] if "." in output_path else output_path
                self.notebook_builder.notebook_path = f"{base}.ipynb"
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

    def _get_cache_path(self) -> str:
        """Get the dataset-relative path to the results cache file."""
        from artifact_layout import get_layout
        return get_layout().generation_cache

    def _save_results_cache(
        self, spec: DocumentSpec, results: List[SectionResult]
    ) -> None:
        """Save generation results to cache via DatasetStore."""
        from dataset_store import get_store
        cache_data = {
            "spec": {
                "title": spec.title,
                "authors": spec.authors,
                "sections": [
                    {"name": s.name, "per_model": s.per_model, "hint": s.hint}
                    for s in spec.sections
                ],
                "hints": spec.hints,
                "citation_style": spec.citation_style,
                "formatting": spec.formatting,
            },
            "results": [self._serialize_section_result(r) for r in results],
        }

        cache_path = self._get_cache_path()
        content = json.dumps(cache_data, indent=2).encode("utf-8")
        get_store().write_file(cache_path, content)

    def _load_results_cache(self) -> tuple[DocumentSpec, List[SectionResult]]:
        """Load generation results from cache via DatasetStore.

        Returns:
            Tuple of (DocumentSpec, List[SectionResult]).

        Raises:
            FileNotFoundError: If cache file doesn't exist.
        """
        from dataset_store import get_store
        cache_path = self._get_cache_path()
        store = get_store()
        if not store.file_exists(cache_path):
            raise FileNotFoundError(
                f"No cached results found at {cache_path}. "
                "Run full generation first with --notebook flag."
            )

        content = store.read_file(cache_path)
        cache_data = json.loads(content)

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
            citation_style=spec_data.get("citation_style", "numeric"),
            formatting=spec_data.get("formatting", {}),
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

        # Handle bytes content for any content type (CHART, IMAGE, etc.)
        if isinstance(content.content, bytes):
            serialized["content"] = base64.b64encode(content.content).decode("ascii")
            serialized["content_encoding"] = "base64"
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

        # Decode base64 content back to bytes (for any content type)
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
        # Build list of (section, context, section_number) tuples for all planning tasks
        planning_tasks: List[tuple[SectionSpec, GenerationContext, str]] = []
        section_num = 1

        for section in spec.sections:
            if section.per_model:
                models = artifact_ctx.models or []

                if not models:
                    # No models found, create a generic section
                    context = GenerationContext(
                        code_context=code_ctx,
                        artifact_context=artifact_ctx,
                        section_name=section.name,
                        hint=spec.hints.get(section.name),
                    )
                    planning_tasks.append((section, context, str(section_num)))
                else:
                    for j, model in enumerate(models, 1):
                        context = GenerationContext(
                            code_context=code_ctx,
                            artifact_context=artifact_ctx,
                            section_name=section.name,
                            model_name=model.name,
                            model_run_id=model.run_id,
                            hint=spec.hints.get(section.name),
                        )
                        planning_tasks.append((section, context, f"{section_num}.{j}"))
            else:
                # Regular section
                context = GenerationContext(
                    code_context=code_ctx,
                    artifact_context=artifact_ctx,
                    section_name=section.name,
                    hint=spec.hints.get(section.name),
                )
                planning_tasks.append((section, context, str(section_num)))

            section_num += 1

        total_planning_operations = len(planning_tasks)
        logger.info(f"Planning {total_planning_operations} sections/subsections across {len(spec.sections)} document sections")

        async def plan_one_section(
            section: SectionSpec, context: GenerationContext, number: str
        ) -> SectionPlan:
            """Plan a single section with semaphore control."""
            async with self.planning_semaphore:
                plan = await self.planner.plan_section(section, context)
                plan.number = number
                return plan

        # Create tasks for all planning operations
        tasks = [
            plan_one_section(section, context, number)
            for section, context, number in planning_tasks
        ]

        # Execute with progress tracking
        plans: List[SectionPlan] = []
        completed = 0

        for coro in asyncio.as_completed(tasks):
            plan = await coro
            plans.append(plan)
            completed += 1

            if on_progress:
                progress = completed / total_planning_operations
                on_progress("Planning", progress)

        # Sort plans back to original order by section number
        def sort_key(plan: SectionPlan) -> tuple:
            # Parse section number like "1" or "2.3" for proper sorting
            parts = plan.number.split(".")
            return tuple(int(p) for p in parts)

        plans.sort(key=sort_key)

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
            """Generate content for a single section.

            The semaphore is acquired per-block (not per-section) so blocks
            across all sections compete for the same worker pool, maximising
            LLM call concurrency.
            """
            context = GenerationContext(
                code_context=code_ctx,
                artifact_context=artifact_ctx,
                section_name=plan.name,
                model_name=plan.model_name,
                model_run_id=plan.model_run_id,
            )

            async def _gen_block(block):
                async with self.semaphore:
                    try:
                        return ("ok", await self.generator.generate(block, context))
                    except Exception as e:
                        logger.error(f"Content generation failed for {block.type.value}: {e}")
                        return ("err", f"{block.type.value}: {str(e)}")

            results_raw = await asyncio.gather(
                *[_gen_block(b) for b in plan.content_blocks]
            )
            contents = [v for tag, v in results_raw if tag == "ok" and v is not None]
            errors = [v for tag, v in results_raw if tag == "err"]

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
