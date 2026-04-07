"""Notebook exporter - converts edited notebooks back to Word documents."""

import asyncio
import io
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import nbformat

from autodoc.core.exceptions import BuilderError
from autodoc.core.models import (
    ContentBlock,
    ContentType,
    DocumentSpec,
    GeneratedContent,
    SectionPlan,
    SectionResult,
    SectionSpec,
)
from autodoc.generation.builder import DocumentBuilder
from autodoc.generation.citations import CITATION_MARKER_PATTERN


class NotebookExporter:
    """Exports edited Jupyter notebooks to Word documents.

    Parses notebook cells, reconstructs SectionResult objects,
    and delegates to DocumentBuilder for Word assembly.
    """

    def __init__(self, output_dir: Path = Path("./output")):
        """Initialize the notebook exporter.

        Args:
            output_dir: Directory to save exported documents.
        """
        self.output_dir = output_dir
        self.builder = DocumentBuilder(output_dir=output_dir)

    def export_to_word(
        self,
        notebook_path: Path,
        title: str = "Model Documentation",
        authors: str = "Data Science Team",
    ) -> Path:
        """Export a notebook to Word document.

        Args:
            notebook_path: Path to the Jupyter notebook.
            title: Document title (can be overridden from notebook).
            authors: Document authors (can be overridden from notebook).

        Returns:
            Path to the generated Word document.

        Raises:
            BuilderError: If export fails.
        """
        try:
            # Load the notebook from the dataset
            import io
            from dataset_store import get_store
            content = get_store().read_file(str(notebook_path))
            nb = nbformat.read(io.StringIO(content.decode("utf-8")), as_version=4)

            # Extract document metadata and content
            spec, results = self._parse_notebook(nb, title, authors)

            # Use DocumentBuilder to create Word document
            output_path = self._run_async(self.builder.build(spec, results))

            return output_path

        except Exception as e:
            raise BuilderError(f"Notebook export failed: {e}") from e

    def _run_async(self, coro) -> Path:
        """Run coroutine from sync context, even in notebooks.

        Handles the case where Jupyter already has an event loop running
        by executing the coroutine in a separate thread.
        """
        # Check if there's already a running event loop
        try:
            loop = asyncio.get_running_loop()
            has_running_loop = True
        except RuntimeError:
            has_running_loop = False

        if not has_running_loop:
            # No running loop, safe to use asyncio.run()
            return asyncio.run(coro)

        # There's a running loop (e.g., in Jupyter), use a thread
        result: Dict[str, Any] = {}
        exception: Dict[str, Exception] = {}

        def run_in_thread() -> None:
            try:
                # Create a new event loop for this thread
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    result["value"] = new_loop.run_until_complete(coro)
                finally:
                    new_loop.close()
            except Exception as exc:
                exception["value"] = exc

        thread = threading.Thread(target=run_in_thread)
        thread.start()
        thread.join(timeout=120)  # 2 minute timeout

        if thread.is_alive():
            raise BuilderError("Export timed out after 120 seconds")

        if "value" in exception:
            raise exception["value"]

        return result["value"]

    def _parse_notebook(
        self,
        nb: nbformat.NotebookNode,
        default_title: str,
        default_authors: str,
    ) -> Tuple[DocumentSpec, List[SectionResult]]:
        """Parse notebook cells into DocumentSpec and SectionResults.

        Args:
            nb: Parsed notebook.
            default_title: Default document title.
            default_authors: Default authors.

        Returns:
            Tuple of (DocumentSpec, List[SectionResult]).
        """
        title = default_title
        authors = default_authors
        results: List[SectionResult] = []
        citation_details: Dict[str, Dict[str, Any]] = {}

        current_section: Optional[Dict[str, Any]] = None
        current_contents: List[GeneratedContent] = []

        for cell in nb.cells:
            source = cell.source

            # Check for setup cell to extract metadata
            if cell.cell_type == "code" and "DOCUMENT_TITLE" in source:
                extracted = self._extract_metadata(source)
                title = extracted.get("title", title)
                authors = extracted.get("authors", authors)
                continue

            # Check for section header (## N. Title)
            if cell.cell_type == "markdown":
                section_match = re.match(
                    r"^##\s+([\d.]+)\.\s+(.+)$", source.strip(), re.MULTILINE
                )
                if section_match:
                    # Save previous section if exists
                    if current_section:
                        results.append(
                            self._create_section_result(
                                current_section, current_contents
                            )
                        )

                    # Start new section
                    current_section = {
                        "number": section_match.group(1),
                        "title": section_match.group(2),
                    }
                    current_contents = []
                    continue

            # Skip title cell, export cells, and instructions
            if cell.cell_type == "markdown":
                if source.startswith("# ") and "Authors:" in source:
                    continue
                if source.strip().startswith("## References"):
                    citation_details.update(self._parse_references_cell(source))
                    continue
                if "Export to Word Document" in source:
                    continue
                if "---" in source and len(source.strip()) < 10:
                    continue

            # Skip export code cell
            if cell.cell_type == "code" and "NotebookExporter" in source:
                continue

            # Process content cells within a section
            if current_section:
                content = self._parse_content_cell(cell, citation_details)
                if content:
                    current_contents.append(content)

        # Don't forget the last section
        if current_section:
            results.append(
                self._create_section_result(current_section, current_contents)
            )

        # Create document spec
        spec = DocumentSpec(
            title=title,
            authors=authors,
            sections=[SectionSpec(name=r.plan.name) for r in results],
        )

        return spec, results

    def _extract_metadata(self, code: str) -> Dict[str, str]:
        """Extract document metadata from setup cell code."""
        metadata = {}

        # Extract DOCUMENT_TITLE
        title_match = re.search(r'DOCUMENT_TITLE\s*=\s*["\'](.+?)["\']', code)
        if title_match:
            metadata["title"] = title_match.group(1)

        # Extract DOCUMENT_AUTHORS
        authors_match = re.search(r'DOCUMENT_AUTHORS\s*=\s*["\'](.+?)["\']', code)
        if authors_match:
            metadata["authors"] = authors_match.group(1)

        return metadata

    def _parse_content_cell(
        self,
        cell: nbformat.NotebookNode,
        citation_details: Dict[str, Dict[str, Any]],
    ) -> Optional[GeneratedContent]:
        """Parse a cell into GeneratedContent."""
        if cell.cell_type == "markdown":
            source = self._restore_citation_markers(cell.source)
            # Check if it's a list
            lines = source.strip().split("\n")
            if all(line.strip().startswith("- ") for line in lines if line.strip()):
                items = [line.strip()[2:] for line in lines if line.strip()]
                return GeneratedContent(
                    block_type=ContentType.BULLET_LIST,
                    content=items,
                    metadata={"citation_details": citation_details},
                )
            elif all(
                re.match(r"^\d+\.\s", line.strip()) for line in lines if line.strip()
            ):
                items = [
                    re.sub(r"^\d+\.\s+", "", line.strip())
                    for line in lines
                    if line.strip()
                ]
                return GeneratedContent(
                    block_type=ContentType.NUMBERED_LIST,
                    content=items,
                    metadata={"citation_details": citation_details},
                )
            else:
                # Regular narrative
                return GeneratedContent(
                    block_type=ContentType.NARRATIVE,
                    content=source,
                    metadata={"citation_details": citation_details},
                )

        elif cell.cell_type == "code":
            source = cell.source

            # Check if it's a chart cell
            if "chart_data" in source and "plt." in source:
                content = self._parse_chart_cell(cell)
                content.metadata["citation_details"] = citation_details
                return content

            # Check if it's a table cell
            if "pd.DataFrame" in source or "table_data" in source:
                content = self._parse_table_cell(cell)
                content.metadata["citation_details"] = citation_details
                return content

        return None

    def _restore_citation_markers(self, text: str) -> str:
        """Convert rendered notebook citations back to [@id] markers."""
        # Pattern for new format: [CitationID](#ref-CitationID)<!-- @cite:id -->
        pattern_new = re.compile(
            r"\[([^\]]+)\]\(#ref-[^\)]+\)\s*<!--\s*@cite:([^\s]+)\s*-->"
        )
        # Pattern for old format: [1](#ref-1)<!-- @cite:id -->
        pattern_old = re.compile(
            r"\[(\d+)\]\(#ref-\1\)\s*<!--\s*@cite:([^\s]+)\s*-->"
        )
        result = pattern_new.sub(r"[@\2]", text or "")
        result = pattern_old.sub(r"[@\2]", result)
        return result

    def _parse_references_cell(self, text: str) -> Dict[str, Dict[str, Any]]:
        """Parse references cell to recover citation details."""
        details: Dict[str, Dict[str, Any]] = {}
        for line in text.splitlines():
            match = re.search(r"@cite:([^\\s]+)", line)
            if not match:
                continue
            citation_id = match.group(1)
            run_url_match = re.search(r"/#/experiments/(\\d+)/runs/([A-Fa-f0-9]+)", line)
            if run_url_match:
                experiment_id, run_id = run_url_match.groups()
                details[citation_id] = {
                    "experiment_id": experiment_id,
                    "run_id": run_id,
                }
        return details

    def _parse_chart_cell(self, cell: nbformat.NotebookNode) -> GeneratedContent:
        """Parse a chart code cell and render it to image bytes."""
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # Execute the cell code to get chart_data
        local_vars: Dict[str, Any] = {}

        # Extract chart_data dictionary from the code
        chart_data = self._extract_chart_data(cell.source)

        if not chart_data:
            # Return empty chart
            return GeneratedContent(
                block_type=ContentType.CHART,
                content=b"",
                metadata={"title": "Chart", "chart_type": "bar"},
            )

        # Determine chart type from code
        chart_type = "bar"
        if "ax.plot(" in cell.source:
            chart_type = "line"
        elif "ax.scatter(" in cell.source:
            chart_type = "scatter"

        # Render the chart
        image_bytes = self._render_chart(chart_data, chart_type)

        return GeneratedContent(
            block_type=ContentType.CHART,
            content=image_bytes,
            metadata={
                "title": chart_data.get("title", ""),
                "chart_type": chart_type,
                "chart_data": chart_data,
            },
        )

    def _extract_chart_data(self, code: str) -> Dict[str, Any]:
        """Extract chart_data dictionary from cell code."""
        # Find the chart_data = {...} block
        match = re.search(
            r"chart_data\s*=\s*(\{[^}]+\})", code, re.DOTALL | re.MULTILINE
        )
        if not match:
            return {}

        try:
            # Try to safely evaluate the dictionary
            import ast

            # Clean up the matched string for ast.literal_eval
            dict_str = match.group(1)
            # Handle Python-style dict (with single quotes)
            chart_data = ast.literal_eval(dict_str)
            return chart_data
        except (ValueError, SyntaxError):
            # Try JSON parsing as fallback
            import json

            try:
                # Convert single quotes to double quotes for JSON
                dict_str = match.group(1).replace("'", '"')
                return json.loads(dict_str)
            except json.JSONDecodeError:
                return {}

    def _render_chart(self, data: Dict[str, Any], chart_type: str) -> bytes:
        """Render chart data to PNG bytes."""
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(10, 6))

        labels = data.get("labels", [])
        values = data.get("values", [])

        if not labels or not values:
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

    def _parse_table_cell(self, cell: nbformat.NotebookNode) -> GeneratedContent:
        """Parse a table code cell."""
        source = cell.source

        # Extract table_data and columns from code
        table_data = self._extract_table_data(source)
        columns = self._extract_columns(source)
        caption = self._extract_caption(source)

        return GeneratedContent(
            block_type=ContentType.TABLE,
            content={
                "caption": caption,
                "columns": columns,
                "rows": table_data,
            },
        )

    def _extract_table_data(self, code: str) -> List[Dict[str, Any]]:
        """Extract table_data list from cell code."""
        match = re.search(r"table_data\s*=\s*(\[[^\]]+\])", code, re.DOTALL)
        if not match:
            return []

        try:
            import ast

            return ast.literal_eval(match.group(1))
        except (ValueError, SyntaxError):
            return []

    def _extract_columns(self, code: str) -> List[str]:
        """Extract columns list from cell code."""
        match = re.search(r'columns\s*=\s*(\[[^\]]+\])', code)
        if not match:
            return []

        try:
            import ast

            return ast.literal_eval(match.group(1))
        except (ValueError, SyntaxError):
            return []

    def _extract_caption(self, code: str) -> str:
        """Extract table caption from cell comment."""
        lines = code.strip().split("\n")
        if lines and lines[0].startswith("#"):
            return lines[0][1:].strip()
        return "Table"

    def _strip_citation_markers(self, value: str) -> str:
        """Strip citation markers from a string for rendering in charts."""
        cleaned = CITATION_MARKER_PATTERN.sub("", value)
        return " ".join(cleaned.split()).strip()

    def _create_section_result(
        self,
        section_info: Dict[str, str],
        contents: List[GeneratedContent],
    ) -> SectionResult:
        """Create a SectionResult from parsed data."""
        plan = SectionPlan(
            number=section_info["number"],
            name=section_info["title"],
            title=section_info["title"],
            content_blocks=[],  # Not needed for export
        )

        return SectionResult(
            plan=plan,
            contents=contents,
            errors=[],
        )
