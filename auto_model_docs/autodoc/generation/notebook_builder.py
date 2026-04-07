"""Jupyter notebook builder for editable documentation."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import nbformat
from nbclient import NotebookClient
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

from autodoc.core.exceptions import BuilderError
from autodoc.core.models import (
    ContentType,
    DocumentSpec,
    GeneratedContent,
    SectionResult,
)
from autodoc.generation.citations import (
    CITATION_MARKER_PATTERN,
    CitationRegistry,
    build_mlflow_summary_citation_id,
    parse_citation_id,
    replace_markers_with_numbers,
)

# Regex pattern to match emojis and other problematic unicode
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map symbols
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"  # dingbats
    "\U000024C2-\U0001F251"  # enclosed characters
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols and pictographs extended-A
    "\U00002600-\U000026FF"  # misc symbols
    "]+",
    flags=re.UNICODE,
)


class NotebookBuilder:
    """Builds Jupyter notebooks from generated content.

    Creates editable notebooks that can be modified in Jupyter/Domino
    and then exported back to Word documents.
    """

    # Default dependencies required for generated notebooks
    DEFAULT_DEPENDENCIES: List[str] = [
        "matplotlib>=3.7.0",
        "pandas>=2.0.0",
        "pydantic>=2.0.0",
        "pydantic-settings>=2.0.0",
        "pyyaml>=6.0",
        "python-docx>=1.0.0",
        "nbformat>=5.9.0",
        "nbclient>=0.8.0",
    ]

    def __init__(
        self,
        output_dir: str = "docs",
        dependencies: List[str] | None = None,
        notebook_path: str | None = None,
    ):
        """Initialize the notebook builder.

        Args:
            output_dir: Logical output directory (dataset-relative path).
                Actual I/O goes through DatasetStore.
            dependencies: List of package names to check/install. Defaults to
                DEFAULT_DEPENDENCIES if not provided.
            notebook_path: Custom path for the generated notebook. If not provided,
                uses <output_dir>/model_docs_notebook.ipynb.
        """
        self.output_dir = output_dir
        self.dependencies = (
            dependencies if dependencies is not None else self.DEFAULT_DEPENDENCIES.copy()
        )
        self.notebook_path = notebook_path

    def _sanitize_for_notebook(self, text: str) -> str:
        """Remove emojis and problematic unicode from text.

        Jupyter notebooks can have issues serializing certain unicode
        characters (especially emojis) which causes UnicodeEncodeError.

        Args:
            text: Input text that may contain emojis.

        Returns:
            Sanitized text with emojis removed.
        """
        return EMOJI_PATTERN.sub("", text)

    def _sanitize_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively sanitize all strings in a dictionary.

        Args:
            data: Dictionary that may contain strings with emojis.

        Returns:
            Dictionary with all strings sanitized.
        """
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self._sanitize_for_notebook(value)
            elif isinstance(value, dict):
                result[key] = self._sanitize_dict(value)
            elif isinstance(value, list):
                result[key] = self._sanitize_list(value)
            else:
                result[key] = value
        return result

    def _sanitize_list(self, data: List[Any]) -> List[Any]:
        """Recursively sanitize all strings in a list.

        Args:
            data: List that may contain strings with emojis.

        Returns:
            List with all strings sanitized.
        """
        result = []
        for item in data:
            if isinstance(item, str):
                result.append(self._sanitize_for_notebook(item))
            elif isinstance(item, dict):
                result.append(self._sanitize_dict(item))
            elif isinstance(item, list):
                result.append(self._sanitize_list(item))
            else:
                result.append(item)
        return result

    async def build(
        self,
        spec: DocumentSpec,
        results: List[SectionResult],
    ) -> str:
        """Build a Jupyter notebook from generated content.

        Args:
            spec: Document specification.
            results: List of generated section results.

        Returns:
            Path to the generated notebook.

        Raises:
            BuilderError: If notebook building fails.
        """
        try:
            # Create new notebook with Python 3 kernel
            nb = new_notebook()
            nb.metadata.kernelspec = {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            }
            nb.metadata.language_info = {
                "name": "python",
                "version": "3.10",
            }

            # Add dependency check cell (runs first)
            nb.cells.append(self._create_dependency_cell())

            # Add setup cell
            nb.cells.append(self._create_setup_cell(spec))

            # Add title cell
            nb.cells.append(self._create_title_cell(spec))

            # Add table of contents cell
            nb.cells.append(self._create_toc_cell(results))

            registry = CitationRegistry(tracking_uri=os.environ.get("MLFLOW_TRACKING_URI"))

            # Add section cells
            for result in results:
                self._add_section_cells(nb, result, registry)

            # Add references section
            if registry.list_entries():
                nb.cells.append(self._create_references_cell(registry))

            # Add export section
            nb.cells.append(self._create_export_instructions_cell())
            nb.cells.append(self._create_export_cell())

            # Execute all cells except the export cell to generate outputs
            nb = self._execute_notebook(nb)

            # Save notebook with outputs
            output_path = self._save_notebook(nb)

            return output_path

        except Exception as e:
            raise BuilderError(f"Notebook building failed: {e}") from e

    def _execute_notebook(
        self, nb: nbformat.NotebookNode
    ) -> nbformat.NotebookNode:
        """Execute notebook cells to generate outputs.

        Executes all cells except the export cell at the end.

        Args:
            nb: The notebook to execute.

        Returns:
            The executed notebook with outputs.
        """
        # Mark the export cell to skip execution by adding a tag
        # The export cell is the last code cell
        export_cell_index = len(nb.cells) - 1

        # Create a client to execute the notebook
        client = NotebookClient(
            nb,
            timeout=120,  # 2 minute timeout per cell
            kernel_name="python3",
            resources={"metadata": {"path": str(self.output_dir)}},
        )

        try:
            # Execute cells one by one, skipping the export cell
            client.km, client.kc = client.create_kernel_manager()
            client.start_new_kernel()
            client.start_new_kernel_client()

            for index, cell in enumerate(nb.cells):
                # Skip markdown cells and the export cell
                if cell.cell_type != "code":
                    continue
                if index == export_cell_index:
                    continue

                try:
                    client.execute_cell(cell, index)
                except Exception as cell_error:
                    # Add error output to cell but continue
                    cell.outputs = [{
                        "output_type": "error",
                        "ename": "ExecutionError",
                        "evalue": str(cell_error),
                        "traceback": [str(cell_error)],
                    }]

            client.stop_kernel()
        except Exception as e:
            # If kernel fails to start, return notebook without outputs
            pass

        return nb

    def _create_setup_cell(self, spec: DocumentSpec) -> nbformat.NotebookNode:
        """Create the setup cell with imports and configuration."""
        font_size = spec.formatting.get("narrative_font_size", 12)
        title_size = spec.formatting.get("heading_font_size", 14)
        axes_label_size = spec.formatting.get("axes_label_size", font_size)
        code = f'''# Document Configuration
# Edit these values as needed

DOCUMENT_TITLE = "{spec.title}"
DOCUMENT_AUTHORS = "{spec.authors}"

# Imports
import matplotlib
matplotlib.use('module://matplotlib_inline.backend_inline')
import matplotlib.pyplot as plt
import pandas as pd
from pathlib import Path

# Enable inline plotting for Jupyter
%matplotlib inline

# Plot styling
plt.rcParams['figure.figsize'] = (10, 6)
plt.rcParams['font.size'] = {font_size}
plt.rcParams['axes.titlesize'] = {title_size}
plt.rcParams['axes.labelsize'] = {axes_label_size}

print(f"Document: {{DOCUMENT_TITLE}}")
print(f"Authors: {{DOCUMENT_AUTHORS}}")
print("Setup complete!")'''
        return new_code_cell(source=code)

    def _create_dependency_cell(self) -> nbformat.NotebookNode:
        """Create a cell that checks and installs required dependencies."""
        packages_repr = repr(self.dependencies)
        code = f'''# Dependency Check
import subprocess
import sys
from importlib.util import find_spec

REQUIRED_PACKAGES = {packages_repr}

def check_and_install_packages(packages):
    missing = []
    for package in packages:
        # Extract base package name (remove version specifier)
        base_package = package.split('>=')[0].split('==')[0].split('<')[0].split('>')[0]
        import_name = base_package.replace("-", "_")
        if find_spec(import_name) is None:
            missing.append(package)

    if missing:
        print(f"Installing missing packages: {{', '.join(missing)}}")
        for package in missing:
            subprocess.run([sys.executable, "-m", "pip", "install", package, "-q"], check=True)
        print("All packages installed!")
    else:
        print("All required packages are already installed.")

check_and_install_packages(REQUIRED_PACKAGES)'''
        return new_code_cell(source=code)

    def _create_title_cell(self, spec: DocumentSpec) -> nbformat.NotebookNode:
        """Create the title markdown cell."""
        date_str = datetime.now().strftime("%B %d, %Y")
        content = f"""# {spec.title}

**Authors:** {spec.authors}

**Date:** {date_str}

---

*This notebook contains editable documentation. Modify charts, tables, and text as needed, then run the export cell at the bottom to generate an updated Word document.*"""
        return new_markdown_cell(source=content)

    def _create_toc_cell(self, results: List[SectionResult]) -> nbformat.NotebookNode:
        """Create a markdown cell with a linked table of contents."""
        lines = ["## Table of Contents", ""]
        for result in results:
            title = f"{result.plan.number}. {result.plan.title}"
            # Build Jupyter-compatible anchor from heading text
            anchor = title.lower().replace(" ", "-")
            anchor = re.sub(r"[^\w\-]", "", anchor)
            lines.append(f"- [{title}](#{anchor})")
        return new_markdown_cell(source="\n".join(lines))

    def _add_section_cells(
        self,
        nb: nbformat.NotebookNode,
        result: SectionResult,
        registry: CitationRegistry,
    ) -> None:
        """Add cells for a section."""
        # Section header
        header_content = f"## {result.plan.number}. {result.plan.title}"
        nb.cells.append(new_markdown_cell(source=header_content))

        section_citation_ids: List[str] = []
        section_citation_details: dict = {}

        # Content cells
        for content in result.contents:
            cleaned_content, citation_ids, citation_details = self._sanitize_content_for_section(
                content
            )
            section_citation_ids.extend(citation_ids)
            section_citation_details.update(citation_details)
            cells = self._create_content_cells(cleaned_content, registry)
            for cell in cells:
                if cell:
                    nb.cells.append(cell)

        # Add error notes if any
        if result.errors:
            error_content = "**Note:** Some content could not be generated:\n"
            for error in result.errors:
                error_content += f"- {error}\n"
            nb.cells.append(new_markdown_cell(source=error_content))

        # Register all section citations for the references (but don't add "Sources:" cell)
        section_citation_ids, section_citation_details = self._normalize_section_citations(
            section_citation_ids, section_citation_details
        )
        for cid in section_citation_ids:
            registry.register(cid, section_citation_details.get(cid))

    def _merge_citations(self, citations: List[str], extra: List[str]) -> List[str]:
        merged: List[str] = []
        seen = set()
        for cid in list(citations or []) + list(extra or []):
            if not cid or cid in seen:
                continue
            seen.add(cid)
            merged.append(cid)
        return merged

    def _strip_citation_markers(self, text: str) -> tuple[str, List[str]]:
        if not text:
            return text, []

        found_ids: List[str] = []

        def _replace(match) -> str:
            citation_id = match.group(1)
            if citation_id:
                found_ids.append(citation_id)
            return ""

        cleaned = CITATION_MARKER_PATTERN.sub(_replace, text)
        cleaned = " ".join(cleaned.split())
        return cleaned, found_ids

    def _strip_markers_from_table(
        self, table_data: dict, found_ids: List[str]
    ) -> dict:
        if not isinstance(table_data, dict):
            return table_data

        cleaned = dict(table_data)
        caption = cleaned.get("caption")
        if isinstance(caption, str):
            cleaned_caption, found = self._strip_citation_markers(caption)
            cleaned["caption"] = cleaned_caption
            found_ids.extend(found)

        columns = cleaned.get("columns")
        if isinstance(columns, list):
            cleaned_columns = []
            for col in columns:
                if isinstance(col, str):
                    cleaned_col, found = self._strip_citation_markers(col)
                    cleaned_columns.append(cleaned_col)
                    found_ids.extend(found)
                else:
                    cleaned_columns.append(col)
            cleaned["columns"] = cleaned_columns

        rows = cleaned.get("rows")
        if isinstance(rows, list):
            cleaned_rows = []
            for row in rows:
                if not isinstance(row, dict):
                    cleaned_rows.append(row)
                    continue
                cleaned_row = {}
                for key, value in row.items():
                    if isinstance(value, str):
                        cleaned_value, found = self._strip_citation_markers(value)
                        cleaned_row[key] = cleaned_value
                        found_ids.extend(found)
                    else:
                        cleaned_row[key] = value
                cleaned_rows.append(cleaned_row)
            cleaned["rows"] = cleaned_rows

        return cleaned

    def _sanitize_content_for_section(
        self, content: GeneratedContent
    ) -> tuple[GeneratedContent, List[str], dict]:
        metadata = dict(content.metadata or {})
        citations = list(metadata.get("citations", []) or [])
        details = dict(metadata.get("citation_details", {}) or {})
        found_ids: List[str] = []

        if content.block_type == ContentType.NARRATIVE:
            cleaned_text, found = self._strip_citation_markers(content.content or "")
            found_ids.extend(found)
            safe_metadata = dict(metadata)
            safe_metadata["citations"] = []
            safe_metadata["citation_details"] = {}
            safe_content = cleaned_text
            block_citations = []
            block_details = {}
        elif content.block_type in (ContentType.BULLET_LIST, ContentType.NUMBERED_LIST):
            cleaned_items: List[str] = []
            for item in content.content or []:
                cleaned_item, found = self._strip_citation_markers(item)
                found_ids.extend(found)
                cleaned_items.append(cleaned_item)
            safe_metadata = dict(metadata)
            safe_metadata["citations"] = []
            safe_metadata["citation_details"] = {}
            safe_content = cleaned_items
            block_citations = []
            block_details = {}
        elif content.block_type == ContentType.TABLE:
            safe_metadata = dict(metadata)
            safe_content = self._strip_markers_from_table(
                content.content or {}, found_ids
            )
            block_citations = self._merge_citations(citations, found_ids)
            block_details = {
                cid: details[cid] for cid in block_citations if cid in details
            }
            safe_metadata["citations"] = block_citations
            safe_metadata["citation_details"] = block_details
        elif content.block_type in (ContentType.CHART, ContentType.IMAGE):
            safe_metadata = dict(metadata)
            title = safe_metadata.get("title")
            if isinstance(title, str):
                cleaned_title, found = self._strip_citation_markers(title)
                safe_metadata["title"] = cleaned_title
                found_ids.extend(found)
            safe_content = content.content
            block_citations = self._merge_citations(citations, found_ids)
            block_details = {
                cid: details[cid] for cid in block_citations if cid in details
            }
            safe_metadata["citations"] = block_citations
            safe_metadata["citation_details"] = block_details
        else:
            safe_metadata = dict(metadata)
            safe_content = content.content
            block_citations = self._merge_citations(citations, found_ids)
            block_details = {
                cid: details[cid] for cid in block_citations if cid in details
            }
            safe_metadata["citations"] = block_citations
            safe_metadata["citation_details"] = block_details

        section_citations = self._merge_citations(citations, found_ids)
        section_details = {cid: details[cid] for cid in details}

        cleaned_content = GeneratedContent(
            block_type=content.block_type,
            content=safe_content,
            metadata=safe_metadata,
        )

        return cleaned_content, section_citations, section_details

    def _normalize_section_citations(
        self, citation_ids: List[str], citation_details: dict
    ) -> tuple[List[str], dict]:
        if not citation_ids:
            return [], {}

        experiment_by_run: dict = {}
        for detail in citation_details.values():
            run_id = detail.get("run_id")
            experiment_id = detail.get("experiment_id")
            if run_id and experiment_id:
                experiment_by_run[run_id] = experiment_id

        normalized_ids: List[str] = []
        normalized_details: dict = {}
        for citation_id in citation_ids:
            parsed = parse_citation_id(citation_id)
            if str(parsed.get("type", "")).startswith("mlflow_"):
                run_id = parsed.get("run_id")
                if run_id:
                    summary_id = build_mlflow_summary_citation_id(run_id)
                    normalized_ids.append(summary_id)
                    if summary_id not in normalized_details:
                        normalized_details[summary_id] = {
                            "type": "mlflow_summary",
                            "run_id": run_id,
                            "experiment_id": experiment_by_run.get(run_id),
                        }
                    continue
            normalized_ids.append(citation_id)
            if citation_id in citation_details and citation_id not in normalized_details:
                normalized_details[citation_id] = citation_details[citation_id]

        deduped: List[str] = []
        seen = set()
        for cid in normalized_ids:
            if cid in seen:
                continue
            seen.add(cid)
            deduped.append(cid)

        filtered_details = {
            cid: normalized_details.get(cid, {})
            for cid in deduped
            if cid in normalized_details
        }

        return deduped, filtered_details

    def _create_content_cells(
        self, content: GeneratedContent, registry: CitationRegistry
    ) -> List[nbformat.NotebookNode]:
        """Create one or more cells for a content block."""
        cells: List[nbformat.NotebookNode] = []

        if content.block_type == ContentType.NARRATIVE:
            cells.append(
                self._create_narrative_cell(content.content, content.metadata, registry)
            )

        elif content.block_type == ContentType.TABLE:
            cells.append(self._create_table_cell(content.content))

        elif content.block_type == ContentType.CHART:
            # Add markdown title with citations above chart
            title = content.metadata.get("title", "")
            if title:
                title_cell = self._render_markdown_with_citations(
                    f"**{title}**",
                    content.metadata,
                    registry,
                )
                cells.append(new_markdown_cell(source=self._sanitize_for_notebook(title_cell)))
            cells.append(self._create_chart_cell(content.metadata))

        elif content.block_type == ContentType.IMAGE:
            cells.append(self._create_image_cell(content.content, content.metadata))

        elif content.block_type in (ContentType.BULLET_LIST, ContentType.NUMBERED_LIST):
            cells.append(
                self._create_list_cell(
                    content.content, content.block_type, content.metadata, registry
                )
            )

        return [cell for cell in cells if cell]

    def _create_narrative_cell(
        self, text: str, metadata: Dict[str, Any], registry: CitationRegistry
    ) -> nbformat.NotebookNode:
        """Create a markdown cell for narrative text."""
        rendered = self._render_markdown_with_citations(text, metadata, registry)
        return new_markdown_cell(source=self._sanitize_for_notebook(rendered))

    def _create_image_cell(
        self, image_bytes: bytes, metadata: Dict[str, Any]
    ) -> nbformat.NotebookNode:
        """Create a code cell that displays an embedded MLflow image."""
        import base64

        title = metadata.get("title", "MLflow Artifact")
        path = metadata.get("path", "")
        image_format = metadata.get("format", "png")

        # Encode image bytes to base64
        b64_data = base64.b64encode(image_bytes).decode("utf-8")

        code = f'''# {title}
# Source: {path}
from IPython.display import Image, display
import base64

# Embedded image data from MLflow artifact
image_data = "{b64_data}"

# Display the image
display(Image(data=base64.b64decode(image_data), format="{image_format}"))'''
        return new_code_cell(source=code)

    def _create_table_cell(self, data: Dict[str, Any]) -> nbformat.NotebookNode:
        """Create a code cell for table display using pandas."""
        # Sanitize all data to remove emojis
        data = self._sanitize_dict(data)

        caption = data.get("caption", "Table")
        columns = data.get("columns", [])
        rows = data.get("rows", [])

        # Generate code to create and display DataFrame
        rows_repr = json.dumps(rows, indent=4, ensure_ascii=True)
        columns_repr = json.dumps(columns, ensure_ascii=True)

        code = f'''# {caption}
# Edit the data below to modify the table

table_data = {rows_repr}

columns = {columns_repr}

df = pd.DataFrame(table_data)
if columns:
    df = df[columns]  # Reorder columns

print("{caption}")
df'''
        return new_code_cell(source=code)

    def _create_chart_cell(self, metadata: Dict[str, Any]) -> nbformat.NotebookNode:
        """Create a code cell for chart visualization."""
        chart_data = metadata.get("chart_data", {})
        chart_type = metadata.get("chart_type", "bar")
        title = metadata.get("title", "Chart")

        return new_code_cell(source=self._serialize_chart(chart_data, chart_type))

    def _serialize_chart(self, data: Dict[str, Any], chart_type: str) -> str:
        """Serialize chart data to executable matplotlib code.

        Args:
            data: Chart data dictionary with labels, values, title, etc.
            chart_type: Type of chart (bar, line, scatter).

        Returns:
            Python code string for creating the chart.
        """
        # Sanitize data to remove emojis
        data = self._sanitize_dict(data)

        # Strip citation markers from title/labels for rendered chart
        def strip_markers(value: str) -> str:
            cleaned = CITATION_MARKER_PATTERN.sub("", value)
            return " ".join(cleaned.split()).strip()

        title = strip_markers(data.get("title", "Chart"))
        labels = data.get("labels", [])
        values = data.get("values", [])
        xlabel = strip_markers(data.get("xlabel", ""))
        ylabel = strip_markers(data.get("ylabel", ""))

        # Generate the data dictionary representation
        data_repr = json.dumps(
            {
                "labels": labels,
                "values": values,
                "title": title,
                "xlabel": xlabel,
                "ylabel": ylabel,
            },
            indent=4,
            ensure_ascii=True,
        )

        # Generate chart-specific plotting code
        if chart_type == "line":
            plot_code = self._serialize_line_chart()
        elif chart_type == "scatter":
            plot_code = self._serialize_scatter_chart()
        else:  # Default to bar
            plot_code = self._serialize_bar_chart()

        return f'''# {title}
# Edit the data or styling below, then run this cell to preview

chart_data = {data_repr}

fig, ax = plt.subplots(figsize=(10, 6))

{plot_code}

ax.set_xlabel(chart_data["xlabel"])
ax.set_ylabel(chart_data["ylabel"])
plt.xticks(rotation=45, ha="right")
plt.tight_layout()
plt.show()'''

    def _serialize_bar_chart(self) -> str:
        """Generate bar chart plotting code."""
        return '''ax.bar(chart_data["labels"], chart_data["values"], color="#4361ee")'''

    def _serialize_line_chart(self) -> str:
        """Generate line chart plotting code."""
        return '''ax.plot(chart_data["labels"], chart_data["values"], marker="o", color="#4361ee", linewidth=2)'''

    def _serialize_scatter_chart(self) -> str:
        """Generate scatter chart plotting code."""
        return '''x = list(range(len(chart_data["values"])))
ax.scatter(x, chart_data["values"], color="#4361ee", s=100)
ax.set_xticks(x)
ax.set_xticklabels(chart_data["labels"])'''

    def _create_list_cell(
        self,
        items: List[str],
        list_type: ContentType,
        metadata: Dict[str, Any],
        registry: CitationRegistry,
    ) -> nbformat.NotebookNode:
        """Create a markdown cell for a list."""
        if not items:
            return new_markdown_cell(source="")

        # Sanitize items to remove emojis
        items = [self._sanitize_for_notebook(item) for item in items]

        lines = []
        for i, item in enumerate(items):
            rendered = self._render_markdown_with_citations(
                item, metadata, registry, include_extra=False
            )
            if list_type == ContentType.NUMBERED_LIST:
                lines.append(f"{i+1}. {rendered}")
            else:
                lines.append(f"- {rendered}")

        return new_markdown_cell(source="\n".join(lines))

    def _render_markdown_with_citations(
        self,
        text: str,
        metadata: Dict[str, Any],
        registry: CitationRegistry,
        include_extra: bool = True,
    ) -> str:
        details = (metadata or {}).get("citation_details", {})
        extra_ids = (metadata or {}).get("citations", []) if include_extra else []
        rendered, _ = replace_markers_with_numbers(
            text,
            registry,
            details_map=details,
            extra_ids=extra_ids,
            markdown=True,
        )
        return rendered

    def _create_source_cell(
        self, metadata: Dict[str, Any], registry: CitationRegistry
    ) -> nbformat.NotebookNode | None:
        if not metadata or not metadata.get("citations"):
            return None
        source_text = "Source:"
        if metadata.get("path"):
            source_text = f"Source: {metadata.get('path')}"
        rendered, _ = replace_markers_with_numbers(
            source_text,
            registry,
            details_map=metadata.get("citation_details", {}),
            extra_ids=metadata.get("citations", []),
            markdown=True,
        )
        return new_markdown_cell(source=rendered)

    def _create_section_source_cell(
        self,
        citation_ids: List[str],
        citation_details: Dict[str, Any],
        registry: CitationRegistry,
    ) -> nbformat.NotebookNode | None:
        if not citation_ids:
            return None
        rendered, _ = replace_markers_with_numbers(
            "Sources:",
            registry,
            details_map=citation_details,
            extra_ids=citation_ids,
            markdown=True,
        )
        return new_markdown_cell(source=rendered)

    def _create_references_cell(self, registry: CitationRegistry) -> nbformat.NotebookNode:
        lines = ["## References", ""]
        for idx, (display_id, entry) in enumerate(registry.list_entries()):
            anchor = f'<a id="ref-{display_id}"></a>'
            # Build single-line reference
            parts = []
            if entry.type == "mlflow_artifact":
                parts.append(display_id)
                parts.append(f"Artifact: {entry.artifact_path}")
            elif entry.type in {"mlflow_run", "mlflow_summary", "mlflow_metric", "mlflow_param", "mlflow_tag"}:
                parts.append(display_id)
                parts.append("Model Run")
            elif entry.type == "code_file":
                # Format code reference - strip __init__ and similar dunder methods
                code_path = entry.code_path or ""
                code_symbol = entry.code_symbol or ""

                clean_symbol = code_symbol
                clean_symbol = clean_symbol.replace(".__init__", "")
                clean_symbol = clean_symbol.replace(".__call__", "")
                clean_symbol = clean_symbol.replace(".__new__", "")

                if code_path and clean_symbol:
                    parts.append(f"Code: {code_path}#{clean_symbol}")
                elif code_path:
                    parts.append(f"Code: {code_path}")
                else:
                    # Fallback: clean up display_id
                    clean_display = display_id
                    clean_display = re.sub(r',\s*@?Code:', ', ', clean_display)
                    clean_display = re.sub(r';\s*@?Code:', '; ', clean_display)
                    clean_display = clean_display.replace(".__init__", "")
                    parts.append(clean_display)
            else:
                # Clean up the display_id for unknown types
                clean_display = display_id
                clean_display = re.sub(r',\s*@?Code:', ', ', clean_display)
                clean_display = re.sub(r';\s*@?Code:', '; ', clean_display)
                clean_display = clean_display.replace(".__init__", "")
                parts.append(clean_display)

            if entry.experiment_name:
                parts.append(f"Experiment: {entry.experiment_name}")
            if entry.run_name:
                parts.append(f"Run: {entry.run_name}")
            if entry.run_url:
                parts.append(f"[Link]({entry.run_url})")
            line = f"{anchor}**[{idx + 1}]** {' | '.join(parts)} <!-- @cite:{entry.id} -->"
            lines.append(line)
        return new_markdown_cell(source="\n".join(lines))

    def _create_export_instructions_cell(self) -> nbformat.NotebookNode:
        """Create markdown cell with export instructions."""
        content = """---

## Export to Word Document

After making your edits above, run the cell below to export this notebook to a Word document.

**Instructions:**
1. Save this notebook (Ctrl+S or Cmd+S)
2. Run all cells to ensure charts are up to date
3. Run the export cell below
4. Find your Word document in the output directory"""
        return new_markdown_cell(source=content)

    def _create_export_cell(self) -> nbformat.NotebookNode:
        """Create the export code cell with embedded paths.

        Paths are dataset-relative strings (e.g. "docs"). The export cell
        embeds them so the notebook can find its outputs when run interactively.
        """
        # Get absolute path to auto_model_docs directory (where autodoc package lives)
        auto_model_docs_dir = Path(__file__).parent.parent.parent.resolve()
        output_dir = self.output_dir  # dataset-relative string

        # Determine the notebook path
        if self.notebook_path:
            notebook_path_str = str(self.notebook_path)
        else:
            notebook_path_str = f"{output_dir}/model_docs_notebook.ipynb"

        code = f'''# Export to Word Document
import sys
sys.path.insert(0, "{auto_model_docs_dir}")  # Embedded at generation time

from autodoc.generation.notebook_exporter import NotebookExporter
from pathlib import Path

output_dir = Path("{output_dir}")  # Embedded at generation time
notebook_path = Path("{notebook_path_str}")  # Embedded at generation time

exporter = NotebookExporter(output_dir=output_dir)
output_path = exporter.export_to_word(
    notebook_path=notebook_path,
    title=DOCUMENT_TITLE,
    authors=DOCUMENT_AUTHORS,
)
print(f"Exported to: {{output_path}}")'''
        return new_code_cell(source=code)

    def _save_notebook(self, nb: nbformat.NotebookNode) -> str:
        """Save the notebook to the dataset via DatasetStore."""
        import io
        from artifact_layout import get_layout
        from dataset_store import get_store

        if self.notebook_path:
            # Custom path: use the filename but place it in docs dir
            filename = str(self.notebook_path).rsplit("/", 1)[-1]
        else:
            filename = "model_docs_notebook.ipynb"

        dataset_path = f"{get_layout().docs_dir}/{filename}"

        # Write notebook to in-memory buffer, then upload
        buffer = io.StringIO()
        nbformat.write(nb, buffer)
        get_store().write_file(dataset_path, buffer.getvalue().encode("utf-8"))

        return dataset_path
