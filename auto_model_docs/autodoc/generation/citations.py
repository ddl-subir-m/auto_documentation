"""Citation utilities for generated documentation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple


CITATION_MARKER_PATTERN = re.compile(r"\[@([^\]]+)\]")


def _sanitize_name(name: str) -> str:
    """Sanitize a name for use in citation IDs."""
    if not name:
        return "Unknown"
    # Replace spaces and special chars with underscores, keep alphanumeric and hyphens
    sanitized = re.sub(r'[^\w\-]', '_', name)
    # Collapse multiple underscores
    sanitized = re.sub(r'_+', '_', sanitized)
    return sanitized.strip('_') or "Unknown"


def build_code_citation_id(
    path: str,
    symbol: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """Build citation ID for code references.

    Args:
        path: File path relative to code root.
        symbol: Function/class name (optional).
        start_line: Starting line number (optional).
        end_line: Ending line number (optional).

    Returns:
        Citation ID like "Code:script.R#func:L42-L58" or "Code:script.py#func".
    """
    suffix = f"#{symbol}" if symbol else ""
    line_suffix = ""
    if start_line is not None and end_line is not None:
        line_suffix = f":L{start_line}-L{end_line}"
    elif start_line is not None:
        line_suffix = f":L{start_line}"
    return f"Code:{path}{suffix}{line_suffix}"


def build_mlflow_run_citation_id(
    experiment_name: Optional[str],
    run_name: Optional[str],
    run_id: Optional[str] = None
) -> str:
    """Build citation ID for run-level citations (metrics, params, tags, summary).

    All metrics/params/tags from the same run will share this citation ID,
    resulting in consolidated references.
    """
    exp = _sanitize_name(experiment_name) if experiment_name else "Experiment"
    run = _sanitize_name(run_name) if run_name else (run_id[:8] if run_id else "Run")
    return f"{exp}-{run}"


def build_mlflow_artifact_citation_id(
    experiment_name: Optional[str],
    run_name: Optional[str],
    artifact_path: str,
    run_id: Optional[str] = None
) -> str:
    """Build citation ID for artifact-specific citations.

    Each artifact file gets its own citation entry.
    """
    exp = _sanitize_name(experiment_name) if experiment_name else "Experiment"
    run = _sanitize_name(run_name) if run_name else (run_id[:8] if run_id else "Run")
    filename = os.path.basename(artifact_path)
    return f"{exp}-{run}:{filename}"


# Legacy function for backward compatibility
def build_mlflow_citation_id(run_id: str, kind: str, key: str) -> str:
    """Legacy citation ID builder - returns old format for backward compatibility."""
    return f"mlflow:run/{run_id}/{kind}/{key}"


def build_mlflow_summary_citation_id(run_id: str) -> str:
    """Legacy function for summary citations."""
    return build_mlflow_citation_id(run_id, "summary", "all")


def _parse_code_citation(raw: str) -> dict:
    """Parse the raw portion of a Code: citation, handling line number suffix."""
    start_line = None
    end_line = None
    # Check for :L{start}-L{end} or :L{start} suffix
    line_match = re.search(r":L(\d+)(?:-L(\d+))?$", raw)
    if line_match:
        start_line = int(line_match.group(1))
        end_line = int(line_match.group(2)) if line_match.group(2) else None
        raw = raw[:line_match.start()]

    if "#" in raw:
        path, symbol = raw.split("#", 1)
    else:
        path, symbol = raw, ""

    result = {"type": "code_file", "code_path": path, "code_symbol": symbol}
    if start_line is not None:
        result["start_line"] = start_line
    if end_line is not None:
        result["end_line"] = end_line
    return result


def parse_citation_id(citation_id: str) -> dict:
    """Parse a citation ID to extract its components."""
    # New format: Code:path#symbol:L42-L58
    if citation_id.startswith("Code:"):
        return _parse_code_citation(citation_id[len("Code:"):])

    # Legacy format: code:path#symbol
    if citation_id.startswith("code:"):
        return _parse_code_citation(citation_id[len("code:"):])

    # Legacy format: mlflow:run/{run_id}/{kind}/{key}
    if citation_id.startswith("mlflow:run/"):
        parts = citation_id.split("/", 3)
        if len(parts) >= 3:
            _, run_id, kind = parts[:3]
            key = parts[3] if len(parts) == 4 else ""
            return {
                "type": f"mlflow_{kind}",
                "run_id": run_id,
                "source_key": key if kind in {"metric", "param", "tag"} else None,
                "artifact_path": key if kind == "artifact" else None,
            }

    # New format: ExperimentName-RunName:artifact.ext (artifact citation)
    if ":" in citation_id and not citation_id.startswith(("mlflow:", "code:", "Code:")):
        base, artifact = citation_id.rsplit(":", 1)
        if "-" in base:
            exp, run = base.split("-", 1)
            return {
                "type": "mlflow_artifact",
                "experiment_name": exp,
                "run_name": run,
                "artifact_path": artifact,
            }

    # New format: ExperimentName-RunName (run-level citation)
    if "-" in citation_id and ":" not in citation_id:
        exp, run = citation_id.split("-", 1)
        return {
            "type": "mlflow_run",
            "experiment_name": exp,
            "run_name": run,
        }

    return {"type": "unknown"}


def build_run_url(tracking_uri: Optional[str], experiment_id: Optional[str], run_id: str) -> str:
    """Build MLflow UI URL for a run."""
    if not tracking_uri:
        return ""
    base = tracking_uri.rstrip("/")
    if experiment_id:
        return f"{base}/#/experiments/{experiment_id}/runs/{run_id}"
    return f"{base}/#/runs/{run_id}"


@dataclass
class CitationEntry:
    """A single citation entry for the references section."""
    id: str
    type: str
    text: str
    run_url: str = ""
    # Additional metadata for rich references
    experiment_name: str = ""
    run_name: str = ""
    run_id: str = ""
    artifact_path: str = ""
    code_path: str = ""
    code_symbol: str = ""


class CitationRegistry:
    """Tracks citation ordering and formatting with academic-style references."""

    def __init__(self, tracking_uri: Optional[str] = None):
        self.tracking_uri = tracking_uri or os.environ.get("MLFLOW_TRACKING_URI")
        self._order: List[str] = []
        self._entries: Dict[str, CitationEntry] = {}
        # Track run-level citation IDs to enable consolidation
        self._run_citations: Dict[str, str] = {}  # run_id -> citation_id

    def register(self, citation_id: str, details: Optional[dict] = None) -> str:
        """Register a citation and return its display ID (the citation_id itself)."""
        details = details or {}

        # Check for consolidation: if this is a run-level citation type and we already
        # have a citation for this run, reuse it
        citation_type = details.get("type", "")
        run_id = details.get("run_id")

        if run_id and citation_type in {"mlflow_metric", "mlflow_param", "mlflow_tag", "mlflow_summary"}:
            # Check if we already have a run-level citation for this run
            if run_id in self._run_citations:
                existing_id = self._run_citations[run_id]
                return existing_id
            # Register this as the run-level citation for this run
            self._run_citations[run_id] = citation_id

        if citation_id not in self._entries:
            entry = self._build_entry(citation_id, details)
            self._entries[citation_id] = entry
            self._order.append(citation_id)

        return citation_id

    def add_from_metadata(
        self, citation_ids: Iterable[str], details_map: Optional[dict] = None
    ) -> List[str]:
        """Register multiple citations and return their display IDs."""
        display_ids = []
        details_map = details_map or {}
        for citation_id in citation_ids:
            display_id = self.register(citation_id, details_map.get(citation_id))
            display_ids.append(display_id)
        return display_ids

    def _build_entry(self, citation_id: str, details: dict) -> CitationEntry:
        """Build a citation entry with academic-style formatting."""
        parsed = parse_citation_id(citation_id)
        entry_type = details.get("type") or parsed.get("type") or "unknown"

        # Extract all available metadata
        run_id = details.get("run_id") or parsed.get("run_id") or ""
        experiment_id = details.get("experiment_id") or ""
        experiment_name = details.get("experiment_name") or parsed.get("experiment_name") or ""
        run_name = details.get("run_name") or parsed.get("run_name") or ""
        code_path = details.get("code_path") or parsed.get("code_path") or ""
        code_symbol = details.get("code_symbol") or parsed.get("code_symbol") or ""
        artifact_path = details.get("artifact_path") or parsed.get("artifact_path") or ""

        run_url = ""
        if run_id:
            run_url = build_run_url(self.tracking_uri, experiment_id, run_id)

        # Build the reference text based on type
        if entry_type == "code_file":
            location = code_path or "unknown"
            symbol_part = f", Symbol: {code_symbol}()" if code_symbol else ""
            text = f"Source Code\n    File: {location}{symbol_part}"

        elif entry_type == "mlflow_artifact":
            filename = os.path.basename(artifact_path) if artifact_path else "unknown"
            text = f"Artifact\n    File: {filename}"
            if experiment_name:
                text += f"\n    Experiment: {experiment_name}"
            if run_name:
                text += f" | Run: {run_name}"
            elif run_id:
                text += f"\n    Run ID: {run_id}"

        elif entry_type in {"mlflow_run", "mlflow_metric", "mlflow_param", "mlflow_tag", "mlflow_summary"}:
            text = "Model Run"
            if experiment_name:
                text += f"\n    Experiment: {experiment_name}"
            if run_name:
                text += f" | Run: {run_name}"
            if run_id:
                text += f"\n    Run ID: {run_id}"

        else:
            text = f"Reference: {citation_id}"

        return CitationEntry(
            id=citation_id,
            type=entry_type,
            text=text,
            run_url=run_url,
            experiment_name=experiment_name,
            run_name=run_name,
            run_id=run_id,
            artifact_path=artifact_path,
            code_path=code_path,
            code_symbol=code_symbol,
        )

    def get_display_id(self, citation_id: str) -> Optional[str]:
        """Get the display ID for a citation (the citation_id itself)."""
        if citation_id not in self._entries:
            return None
        return citation_id

    # Legacy method for backward compatibility
    def get_number(self, citation_id: str) -> Optional[int]:
        """Get the numeric index for a citation (legacy support)."""
        if citation_id not in self._entries:
            return None
        return self._order.index(citation_id) + 1

    def list_entries(self) -> List[Tuple[str, CitationEntry]]:
        """List all citation entries with their display IDs."""
        return [(cid, self._entries[cid]) for cid in self._order]


def extract_citation_ids(text: str) -> List[str]:
    """Extract citation IDs from text with [@...] markers.

    Also handles malformed combined citations like [@id1; @id2] or [@id1, @id2]
    by splitting them into separate IDs.
    """
    raw_ids = [m.group(1) for m in CITATION_MARKER_PATTERN.finditer(text or "")]

    # Split any combined citation IDs
    result = []
    for raw_id in raw_ids:
        # Check for common separators used by LLM when combining citations
        if '; @' in raw_id or ', @' in raw_id or ',@' in raw_id or ';@' in raw_id:
            # Split on various patterns
            parts = re.split(r'[;,]\s*@', raw_id)
            for i, part in enumerate(parts):
                clean_part = part.strip()
                if clean_part:
                    # First part doesn't need @ prefix restored, others do
                    if i == 0:
                        result.append(clean_part)
                    else:
                        # Check if it looks like a citation ID that lost its prefix
                        if not clean_part.startswith(('Code:', 'code:', 'mlflow:')):
                            result.append(clean_part)
                        else:
                            result.append(clean_part)
        else:
            result.append(raw_id)

    return result


def replace_markers_with_ids(
    text: str,
    registry: CitationRegistry,
    details_map: Optional[dict] = None,
    extra_ids: Optional[List[str]] = None,
    markdown: bool = False,
) -> Tuple[str, List[str]]:
    """Replace [@citation_id] markers with formatted citation references."""
    details_map = details_map or {}
    used_ids: List[str] = []

    def _replace(match: re.Match) -> str:
        citation_id = match.group(1)
        display_id = registry.register(citation_id, details_map.get(citation_id))
        used_ids.append(display_id)
        if markdown:
            return f"[{display_id}](#ref-{display_id})<!-- @cite:{citation_id} -->"
        return f"[{display_id}]"

    replaced = CITATION_MARKER_PATTERN.sub(_replace, text or "")

    if extra_ids:
        for citation_id in extra_ids:
            if citation_id in used_ids:
                continue
            display_id = registry.register(citation_id, details_map.get(citation_id))
            used_ids.append(display_id)
            marker = (
                f"[{display_id}](#ref-{display_id})<!-- @cite:{citation_id} -->"
                if markdown
                else f"[{display_id}]"
            )
            replaced = f"{replaced} {marker}".rstrip()

    return replaced, used_ids


# Legacy function name for backward compatibility
def replace_markers_with_numbers(
    text: str,
    registry: CitationRegistry,
    details_map: Optional[dict] = None,
    extra_ids: Optional[List[str]] = None,
    markdown: bool = False,
) -> Tuple[str, List[str]]:
    """Legacy wrapper - now uses citation IDs instead of numbers."""
    return replace_markers_with_ids(text, registry, details_map, extra_ids, markdown)
