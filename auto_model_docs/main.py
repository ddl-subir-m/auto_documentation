#!/usr/bin/env python3
"""CLI entry point for Auto Model Documentation."""

import asyncio
import logging
import os
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)

# Add the parent directory to path for local development
sys.path.insert(0, str(Path(__file__).parent))

from autodoc.bundle_context import delete_context_file, load_context
from autodoc.core.config import Settings
from autodoc.core.models import DocumentSpec, SectionSpec
from autodoc.llm import LLMClient
from autodoc.orchestrator import Orchestrator
from autodoc.scanning import ContentSanitizer
from autodoc.spec_from_policy import VALID_DOC_TYPES, derive_spec


console = Console()


@click.command()
@click.option(
    "--spec",
    "-s",
    default=None,
    type=click.Path(exists=True),
    help="Path to YAML document specification (required unless --derive-spec is used)",
)
@click.option(
    "--derive-spec",
    "derive_spec_type",
    default=None,
    type=click.Choice(list(VALID_DOC_TYPES)),
    help=(
        "Derive the spec from policy_def in --context-file using the named "
        "canonical template ({}) instead of loading --spec.".format(
            "|".join(VALID_DOC_TYPES)
        )
    ),
)
@click.option(
    "--code-root",
    "-c",
    type=click.Path(exists=True),
    help="Root directory of codebase to analyze (default: /mnt/code or ./)",
)
@click.option(
    "--provider",
    "-p",
    default="openai",
    type=click.Choice(["anthropic", "openai"]),
    help="LLM provider to use",
)
@click.option(
    "--model",
    "-m",
    default=None,
    help="Model name override (uses provider default if not set)",
)
@click.option(
    "--max-retries",
    default=None,
    type=int,
    help="Max retries for LLM requests",
)
@click.option(
    "--initial-backoff",
    default=None,
    type=float,
    help="Initial backoff delay in seconds",
)
@click.option(
    "--max-backoff",
    default=None,
    type=float,
    help="Maximum backoff delay in seconds",
)
@click.option(
    "--backoff-jitter",
    default=None,
    type=float,
    help="Random jitter factor applied to backoff",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose output",
)
@click.option(
    "--max-files",
    default=50,
    type=int,
    help="Maximum number of files to scan",
)
@click.option(
    "--generation-workers",
    "-w",
    "workers",
    default=4,
    type=int,
    help="Number of parallel workers for content generation",
)
@click.option(
    "--planning-workers",
    default=4,
    type=int,
    help="Number of parallel workers for section planning",
)
@click.option(
    "--notebook",
    is_flag=True,
    help="Also generate editable Jupyter notebook",
)
@click.option(
    "--notebook-from-cache",
    is_flag=True,
    help="Regenerate notebook from cached results (skips full pipeline)",
)
@click.option(
    "--notebook-path",
    type=click.Path(),
    help="Custom path for the generated notebook (default: <output>/model_docs_notebook.ipynb)",
)
@click.option(
    "--timeout",
    default=120.0,
    type=float,
    help="Timeout for individual LLM API calls in seconds (default: 120)",
)
@click.option(
    "--experiments",
    type=str,
    help="Comma-separated list of experiment names/patterns to include. Supports wildcards: * (any) and ? (single char). Example: customer_churn*,fraud_detection",
)
@click.option(
    "--models", 
    type=str,
    help="Comma-separated list of model names/patterns to include. Supports wildcards: * (any) and ? (single char). Example: churn*,fraud_detector",
)
@click.option(
    "--latest-only",
    is_flag=True,
    help="Only include the latest version of each model",
)
@click.option(
    "--disable-project-filtering",
    is_flag=True,
    help="Disable automatic Domino project filtering (scan all projects)",
)
@click.option(
    "--bundle-id",
    default=None,
    type=str,
    help="Governance bundle ID (Shape 1 Portal integration; use with --policy-version-id and --context-file)",
)
@click.option(
    "--policy-version-id",
    default=None,
    type=str,
    help="Governance policy version ID (Shape 1 Portal integration; use with --bundle-id and --context-file)",
)
@click.option(
    "--context-file",
    default=None,
    type=click.Path(),
    help="Path to bundle context JSON file written by Portal (Shape 1 Portal integration; use with --bundle-id and --policy-version-id)",
)
@click.option(
    "--output-file",
    default=None,
    type=click.Path(),
    help="Filesystem path to also write the generated .docx (Portal integration handoff; parent dir is created automatically)",
)
def main(
    spec: str,
    code_root: str | None,
    provider: str,
    model: str | None,
    max_retries: int | None,
    initial_backoff: float | None,
    max_backoff: float | None,
    backoff_jitter: float | None,
    verbose: bool,
    max_files: int,
    workers: int,
    planning_workers: int,
    notebook: bool,
    notebook_from_cache: bool,
    notebook_path: str | None,
    timeout: float,
    experiments: str | None,
    models: str | None,
    latest_only: bool,
    disable_project_filtering: bool,
    bundle_id: str | None,
    policy_version_id: str | None,
    context_file: str | None,
    derive_spec_type: str | None,
    output_file: str | None,
) -> None:
    """Generate model documentation from ML codebases.

    This tool analyzes your ML codebase and generates a professional
    Word document with documentation about your models, features,
    and training pipelines.

    Example usage:

        python main.py --spec doc_spec.yaml --provider anthropic

    Environment variables (can be set in .env file):

        ANTHROPIC_API_KEY - API key for Anthropic Claude
        OPENAI_API_KEY - API key for OpenAI GPT-4

    For full configuration options, see the Settings class in autodoc/core/config.py
    """
    # Validate Shape 1 context flags: all three must appear together or none.
    context_flags = {
        "--bundle-id": bundle_id,
        "--policy-version-id": policy_version_id,
        "--context-file": context_file,
    }
    provided = [name for name, val in context_flags.items() if val]
    if provided and len(provided) < len(context_flags):
        missing = [name for name, val in context_flags.items() if not val]
        console.print(
            "[bold red]Error:[/] --bundle-id, --policy-version-id, and "
            "--context-file must be used together "
            f"(provided: {', '.join(provided)}; missing: {', '.join(missing)})",
            style="red",
        )
        raise SystemExit(2)

    # --derive-spec requires --context-file (and thus the full Shape 1 trio).
    if derive_spec_type and not context_file:
        console.print(
            "[bold red]Error:[/] --derive-spec requires --context-file "
            "(and --bundle-id, --policy-version-id).",
            style="red",
        )
        raise SystemExit(2)

    # Exactly one of --spec or --derive-spec must be present.
    if not spec and not derive_spec_type:
        console.print(
            "[bold red]Error:[/] one of --spec or --derive-spec is required.",
            style="red",
        )
        raise SystemExit(2)

    bundle_context: dict | None = None
    try:
        if context_file:
            bundle_context = load_context(context_file)

        # Configure logging based on verbosity
        log_level = logging.INFO if verbose else logging.WARNING
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[logging.StreamHandler()]
        )
        
        # Load settings from .env and environment
        settings = Settings()

        # Override settings with CLI arguments if provided
        if provider:
            settings.llm_provider = provider
        if model:
            settings.llm_model = model
        if max_retries is not None:
            settings.llm_max_retries = max_retries
        if initial_backoff is not None:
            settings.llm_initial_backoff = initial_backoff
        if max_backoff is not None:
            settings.llm_max_backoff = max_backoff
        if backoff_jitter is not None:
            settings.llm_backoff_jitter = backoff_jitter
        if code_root:
            settings.code_root = Path(code_root)
        if max_files:
            settings.max_files = max_files
        if workers:
            settings.parallel_workers = workers
        if planning_workers:
            settings.planning_workers = planning_workers

        # Initialize artifact layout and dataset store
        from artifact_layout import init_layout, get_layout
        from dataset_store import init_store, AUTODOC_DATASET_NAME
        init_layout()
        _init_cli_dataset_store()
        output_dir = get_layout().docs_dir
        code_dir = settings.code_root if settings.code_root.exists() else _get_default_code_root()

        # Handle --notebook-from-cache mode (regenerate from cache)
        if notebook_from_cache:
            _regenerate_notebook_from_cache(
                output_dir,
                verbose,
                Path(notebook_path) if notebook_path else None,
            )
            return

        # Parse CSV filtering options
        experiment_names = None
        if experiments:
            experiment_names = [name.strip() for name in experiments.split(",") if name.strip()]
            
        model_names = None
        if models:
            model_names = [name.strip() for name in models.split(",") if name.strip()]

        # Load document spec
        if derive_spec_type:
            console.print(
                f"\n[bold blue]Deriving specification from policy_def[/] "
                f"(doc_type={derive_spec_type})"
            )
            derived = derive_spec(
                (bundle_context or {}).get("policy_def"), derive_spec_type
            )
            logging.getLogger("autodoc.spec_from_policy").debug(
                "derived spec: %s", derived
            )
            doc_spec = _doc_spec_from_dict(derived)
        else:
            console.print(f"\n[bold blue]Loading specification:[/] {spec}")
            doc_spec = DocumentSpec.from_yaml(spec)
        console.print(f"[bold]Document:[/] {doc_spec.title}")
        console.print(f"[bold]Sections:[/] {len(doc_spec.sections)}")

        if verbose:
            console.print(f"[dim]Code root:[/] {code_dir}")
            console.print(f"[dim]Output dir:[/] {output_dir}")
            console.print(f"[dim]Provider:[/] {settings.llm_provider}")
            console.print(f"[dim]Model:[/] {settings.get_model_name()}")
            console.print(f"[dim]Max files:[/] {settings.max_files}")
            console.print(f"[dim]Generation workers:[/] {settings.parallel_workers}")
            console.print(f"[dim]Planning workers:[/] {settings.planning_workers}")
            console.print(f"[dim]Notebook:[/] {notebook}")
            console.print(f"[dim]Max retries:[/] {settings.llm_max_retries}")
            console.print(f"[dim]Initial backoff:[/] {settings.llm_initial_backoff}")
            console.print(f"[dim]Max backoff:[/] {settings.llm_max_backoff}")
            console.print(f"[dim]Backoff jitter:[/] {settings.llm_backoff_jitter}")
            console.print(f"[dim]Timeout:[/] {timeout}s")
            
            # Show filtering options
            console.print(f"[dim]Project filtering:[/] {'disabled' if disable_project_filtering else 'enabled'}")
            if experiment_names:
                console.print(f"[dim]Experiments:[/] {', '.join(experiment_names)}")
            if model_names:
                console.print(f"[dim]Models:[/] {', '.join(model_names)}")
            if latest_only:
                console.print(f"[dim]Version filtering:[/] latest only")

        # Get API key from settings
        try:
            api_key = settings.get_api_key()
        except ValueError as e:
            console.print(f"\n[bold red]Error:[/] {e}", style="red")
            console.print("[dim]Set API keys in .env file or environment variables[/]")
            sys.exit(1)

        # Initialize components
        llm = LLMClient(
            provider=settings.llm_provider,
            model=settings.get_model_name(),
            api_key=api_key,
            base_url=settings.openai_base_url,
            max_retries=settings.llm_max_retries,
            initial_backoff=settings.llm_initial_backoff,
            max_backoff=settings.llm_max_backoff,
            backoff_jitter=settings.llm_backoff_jitter,
            timeout_seconds=timeout,
        )
        sanitizer = ContentSanitizer()
        orchestrator = Orchestrator(
            llm=llm,
            sanitizer=sanitizer,
            code_root=code_dir,
            output_dir=output_dir,
            mlflow_tracking_uri=settings.mlflow_tracking_uri,
            parallel_workers=settings.parallel_workers,
            planning_workers=settings.planning_workers,
            max_files=settings.max_files,
            max_file_size=settings.max_file_size,
            generate_notebook=notebook or bool(notebook_path),
            notebook_path=Path(notebook_path) if notebook_path else None,
            exclude_patterns=settings.exclude_patterns,
            max_selected_files=settings.max_selected_files,
            batch_size=settings.batch_size,
            analysis_timeout=settings.analysis_timeout,
            scan_retries=settings.scan_retries,
            scan_workers=settings.scan_workers,
            # Pass filtering options to orchestrator
            experiment_names=experiment_names,
            model_names=model_names,
            latest_only=latest_only,
            disable_project_filtering=disable_project_filtering,
            bundle_context=bundle_context,
        )

        # Run generation with progress
        console.print("\n[bold blue]Starting document generation...[/]\n")

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task("Initializing...", total=100)

            def on_progress(phase: str, pct: float) -> None:
                # Update progress with current phase and percentage
                completed = pct * 100
                progress.update(task_id, completed=completed, description=f"{phase}...")

            # Run async generation
            output_path = asyncio.run(orchestrator.generate(doc_spec, on_progress))

        # Write to handoff filesystem path if Portal requested it.
        if output_file:
            from dataset_store import get_store
            file_bytes = get_store().read_file(output_path)
            dest = Path(output_file)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(file_bytes)
            logging.getLogger(__name__).info("Wrote output to filesystem handoff path: %s", dest)

        # Success!
        console.print(f"\n[bold green]Success![/] Document generated:")
        console.print(f"  [cyan]{output_path}[/]")
        if notebook or notebook_path:
            actual_notebook_path = orchestrator.notebook_builder.notebook_path
            console.print(f"  [cyan]{actual_notebook_path}[/]")
        console.print()

    except FileNotFoundError as e:
        console.print(f"\n[bold red]Error:[/] File not found: {e}", style="red")
        sys.exit(1)
    except ValueError as e:
        console.print(f"\n[bold red]Error:[/] {e}", style="red")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled by user[/]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[bold red]Error:[/] {e}", style="red")
        if verbose:
            console.print_exception()
        sys.exit(1)
    finally:
        if context_file:
            delete_context_file(context_file)


def _regenerate_notebook_from_cache(
    output_dir: Path,
    verbose: bool,
    notebook_path: Path | None = None,
) -> None:
    """Regenerate notebook from cached results without running full pipeline."""
    from autodoc.generation import NotebookBuilder

    console.print("\n[bold blue]Regenerating notebook from cache...[/]\n")

    from artifact_layout import get_layout
    from dataset_store import get_store
    cache_path = get_layout().generation_cache
    if not get_store().file_exists(cache_path):
        console.print(
            f"[bold red]Error:[/] No cached results found at {cache_path}",
            style="red",
        )
        console.print("[dim]Run full generation first with --notebook flag[/]")
        sys.exit(1)

    # Create a minimal orchestrator just for notebook regeneration
    # We don't need LLM client for this
    from autodoc.orchestrator import Orchestrator

    # Create orchestrator with dummy values (won't be used)
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.output_dir = output_dir
    orchestrator.notebook_path = notebook_path
    orchestrator.notebook_builder = NotebookBuilder(
        output_dir=output_dir,
        notebook_path=notebook_path,
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Regenerating notebook...", total=100)

        def on_progress(phase: str, pct: float) -> None:
            completed = pct * 100
            progress.update(task_id, completed=completed, description=f"{phase}...")

        # Regenerate notebook from cache
        result_notebook_path = asyncio.run(orchestrator.regenerate_notebook(on_progress))

    console.print(f"\n[bold green]Success![/] Notebook regenerated:")
    console.print(f"  [cyan]{result_notebook_path}[/]")
    console.print()


def _init_cli_dataset_store() -> None:
    """Initialize the DatasetStore for CLI mode (Domino job container).

    In a Domino job container, we have access to the Domino API and
    need to find or create the autodoc dataset in the current project.
    """
    from dataset_store import init_store, AUTODOC_DATASET_NAME
    project_id = os.environ.get("DOMINO_PROJECT_ID", "")
    if not project_id:
        raise RuntimeError(
            "DOMINO_PROJECT_ID not set. "
            "The CLI requires Domino environment variables."
        )
    # Import here to avoid circular imports at module level
    try:
        from domino_datasets import ensure_dataset, get_rw_snapshot_id
        ds = ensure_dataset(
            project_id=project_id,
            name=AUTODOC_DATASET_NAME,
            description="Auto Model Docs artifacts",
        )
        snap_id = ds.get("rwSnapshotId") or ""
        if not snap_id:
            snap_id = get_rw_snapshot_id(ds["id"], project_id) or ""
        init_store(ds["id"], snap_id, project_id)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to initialize DatasetStore for CLI: {exc}"
        ) from exc


def _doc_spec_from_dict(data: dict) -> DocumentSpec:
    """Build a DocumentSpec from an already-parsed dict (e.g. derived spec)."""
    sections = []
    for section in data.get("sections", []):
        if isinstance(section, str):
            if section.endswith(": per_model"):
                sections.append(SectionSpec(name=section[:-len(": per_model")], per_model=True))
            else:
                sections.append(SectionSpec(name=section))
        elif isinstance(section, dict):
            sections.append(SectionSpec(**section))
    return DocumentSpec(
        title=data["title"],
        authors=data.get("authors", "Data Science Team"),
        sections=sections,
        hints=data.get("hints", {}),
        citation_style=data.get("citation_style", "numeric"),
        formatting=data.get("formatting", {}),
    )


def _get_default_code_root() -> Path:
    """Get default code root directory."""
    # Check Domino environment first
    if Path("/mnt/code").exists():
        return Path("/mnt/code")
    # Fall back to current directory
    return Path(".")


if __name__ == "__main__":
    main()
