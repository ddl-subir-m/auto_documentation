"""Shared mutable state, core types, and helpers used across all studio modules."""

from __future__ import annotations

import asyncio
import importlib.util as _imputil
import logging
import os
import ctypes as _ctypes
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from rich.console import Console

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
for _mod_name in ("domino_datasets", "domino_client", "auth_context"):
    logging.getLogger(_mod_name).setLevel(logging.INFO)

# Rich console for terminal output
console = Console()

# ---------------------------------------------------------------------------
# Sibling module imports  (domino_client, domino_job_store, etc.)
# ---------------------------------------------------------------------------

def _import_sibling(name: str):
    """Import a .py file from the auto_model_docs directory (parent of studio/)."""
    import sys
    # studio/state.py -> studio/ -> auto_model_docs/
    path = Path(__file__).resolve().parent.parent / f"{name}.py"
    if not path.exists():
        raise FileNotFoundError(f"Sibling module not found: {path}")
    spec = _imputil.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module spec for {path}")
    mod = _imputil.module_from_spec(spec)
    # Register in sys.modules so @dataclass and other introspection works
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Preload conda's libstdc++ to fix CXXABI version mismatch in Domino Apps.
try:
    for _candidate in (
        os.path.join(os.environ.get("CONDA_PREFIX", "/opt/conda"), "lib", "libstdc++.so.6"),
        "/opt/conda/lib/libstdc++.so.6",
    ):
        if os.path.isfile(_candidate):
            try:
                _ctypes.CDLL(_candidate, mode=_ctypes.RTLD_GLOBAL)
                break
            except OSError:
                continue
except Exception:
    pass

# Domino module references — may be None if import fails
domino_client: Any = None
domino_job_store: Any = None
spec_store: Any = None
auth_context: Any = None
domino_datasets: Any = None
_DOMINO_AVAILABLE: bool = False

try:
    domino_client = _import_sibling("domino_client")
    domino_job_store = _import_sibling("domino_job_store")
    spec_store = _import_sibling("spec_store")
    auth_context = _import_sibling("auth_context")
    domino_datasets = _import_sibling("domino_datasets")
    _DOMINO_AVAILABLE = True
except Exception as _import_exc:
    logging.getLogger(__name__).warning("Domino modules unavailable: %s", _import_exc, exc_info=True)
    _DOMINO_AVAILABLE = False


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class JobState:
    id: str
    status: str = "idle"
    phase: str = "Idle"
    progress: float = 0.0
    logs: list[str] = field(default_factory=list)
    output_path: Optional[Path] = None
    notebook_path: Optional[Path] = None
    output_dir: Optional[Path] = None
    spec_path: Optional[Path] = None
    error: Optional[str] = None
    cancel_requested: bool = False
    task: Optional[asyncio.Task] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    # Progress bar tracking
    progress_ctx: Any = None
    progress_task_id: Optional[int] = None
    current_phase: Optional[str] = None
    log_version: int = 0


@dataclass
class JobRequest:
    spec_path: Optional[str]
    spec_content: Optional[str]
    provider: str
    model: Optional[str]
    api_key: Optional[str]
    base_url: Optional[str]
    code_root: Optional[str]
    output_dir: Optional[str]
    max_files: Optional[int]
    workers: Optional[int]
    planning_workers: Optional[int]
    timeout: Optional[float]
    notebook: bool
    notebook_path: Optional[str]
    experiment_names: Optional[str]  # Comma-separated list
    model_names: Optional[str]  # Comma-separated list
    latest_only: bool
    verbose: bool  # Enable verbose logging
    # Domino job fields
    # execution_mode is auto-inferred: project_id present -> "domino", else -> "app"
    branch: Optional[str] = None
    hardware_tier: Optional[str] = None
    api_key_source: str = "domino_env"  # "domino_env" | "pass_now"
    spec_filename: Optional[str] = None  # original uploaded filename
    project_id: Optional[str] = None     # target Domino project (from ?projectId=)


@dataclass
class DominoJobRecord:
    id: str                              # local UUID
    username: str
    domino_run_id: Optional[str] = None
    branch: Optional[str] = None
    hardware_tier: Optional[str] = None
    status: str = "queued"               # queued | submitted | running | succeeded | failed | cancelled
    domino_status: Optional[str] = None
    job_url: Optional[str] = None
    spec_path: Optional[str] = None
    submitted_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    project_id: Optional[str] = None     # target Domino project ID


@dataclass
class EnvironmentWarning:
    """A startup environment warning."""
    level: str   # "info" | "warning" | "error"
    message: str
    action: str  # suggested action


# ---------------------------------------------------------------------------
# Mutable global state
# ---------------------------------------------------------------------------

JOB_STORE: dict[str, JobState] = {}
ACTIVE_JOB_ID: Optional[str] = None
LAST_API_KEY: Optional[str] = None
_POLL_TASK: Optional[asyncio.Task] = None
_STARTUP_WARNINGS: list = []


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _log(job: JobState, message: str) -> None:
    job.logs.append(f"[{_timestamp()}] {message}")
    job.updated_at = datetime.utcnow()
    job.log_version += 1


def _cleanup_job(job: JobState) -> None:
    _log(job, "Cleaning up artifacts.")
    paths: list[Path] = []
    if job.output_path:
        paths.append(job.output_path)
    if job.notebook_path:
        paths.append(job.notebook_path)
    if job.spec_path:
        paths.append(job.spec_path)

    for path in paths:
        try:
            if path.exists():
                path.unlink()
                _log(job, f"Removed: {path}")
        except Exception as exc:
            _log(job, f"Cleanup failed for {path}: {exc}")

    job.output_path = None
    job.notebook_path = None
    job.spec_path = None


def _resolve_job(job_id: Optional[str]) -> Optional[JobState]:
    if job_id and job_id in JOB_STORE:
        return JOB_STORE[job_id]
    return None


def _get_default_output_dir() -> Path:
    # In Domino, use /mnt/data/{project_name} (persisted via Datasets)
    if Path("/mnt/data").exists():
        project_name = os.environ.get("DOMINO_PROJECT_NAME", "output")
        output = Path(f"/mnt/data/{project_name}")
        output.mkdir(parents=True, exist_ok=True)
        return output
    # Fallback for local development
    output = Path("./output")
    output.mkdir(exist_ok=True)
    return output


def _get_default_code_root() -> Path:
    """Return the default code root: /mnt/code for git projects,
    /mnt for DFS projects, or cwd as fallback."""
    if Path("/mnt/code").exists():
        return Path("/mnt/code")
    if Path("/mnt").exists():
        return Path("/mnt")
    return Path(".")


def _get_default_spec_path() -> Path:
    # spec is in auto_model_docs/ (parent of studio/)
    return Path(__file__).resolve().parent.parent / "doc_spec.yaml"


def _get_username() -> str:
    return os.environ.get("DOMINO_STARTING_USERNAME", "local_user")


def _max_jobs() -> int:
    return int(os.environ.get("AUTODOC_MAX_JOBS", "1"))
