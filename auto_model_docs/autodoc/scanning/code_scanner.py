"""LLM-based code scanner for semantic analysis of ML codebases."""

from pathlib import Path
from typing import Callable, Dict, List, Optional

from autodoc.core.exceptions import ScannerError
from autodoc.core.models import CodeContext, CodeEvidence
from autodoc.llm import LLMClient
from autodoc.llm.prompts import (
    CODE_ANALYSIS_SCHEMA,
    SYSTEM_CODE_ANALYZER,
    build_code_analysis_prompt,
)
from autodoc.scanning.sanitizer import ContentSanitizer

# Type alias for progress callback
ProgressCallback = Callable[[float], None]


class CodeScanner:
    """Scans codebase using LLM for semantic understanding.

    This scanner uses an LLM to analyze Python code and extract
    information about ML models, features, transformations, etc.
    """

    # Keywords for prioritizing files (more relevant files first)
    PRIORITY_KEYWORDS = ["train", "model", "feature", "pipeline", "main", "predict"]

    # Patterns to exclude from scanning
    EXCLUDE_PATTERNS = {
        "test_",
        "_test.py",
        "conftest.py",
        "__pycache__",
        ".git",
        "venv",
        ".venv",
        "node_modules",
        ".pytest_cache",
        "__init__.py",
    }

    def __init__(
        self,
        llm: LLMClient,
        sanitizer: ContentSanitizer,
        code_root: Path = Path("/mnt/code"),
        max_files: int = 50,
        max_file_size: int = 50000,
    ):
        """Initialize the code scanner.

        Args:
            llm: LLM client for analysis.
            sanitizer: Content sanitizer for removing secrets.
            code_root: Root directory of the codebase.
            max_files: Maximum number of files to analyze.
            max_file_size: Maximum size per file in characters.
        """
        self.llm = llm
        self.sanitizer = sanitizer
        self.code_root = code_root
        self.max_files = max_files
        self.max_file_size = max_file_size

    async def scan(
        self, on_progress: Optional[ProgressCallback] = None
    ) -> CodeContext:
        """Scan the codebase and extract context using LLM.

        Args:
            on_progress: Optional callback for progress updates (0.0 to 1.0).

        Returns:
            CodeContext with extracted information.

        Raises:
            ScannerError: If scanning fails.
        """

        def report_progress(progress: float) -> None:
            """Report progress if callback is provided."""
            if on_progress:
                on_progress(progress)

        try:
            report_progress(0.0)

            # Find Python files
            files = self._find_python_files()

            report_progress(0.1)

            if not files:
                report_progress(1.0)
                return CodeContext(
                    files=[],
                    insights="No Python files found in codebase.",
                )

            # Read and sanitize code
            code_contents = self._read_files(files)

            report_progress(0.3)

            if not code_contents:
                report_progress(1.0)
                return CodeContext(
                    files=[str(f) for f in files],
                    insights="Could not read any Python files.",
                )

            # Check for README
            readme_content = self._read_readme()

            report_progress(0.4)

            # Analyze with LLM (this is the slowest part)
            context = await self._analyze_code(code_contents)
            context.readme = readme_content

            report_progress(1.0)

            return context

        except Exception as e:
            raise ScannerError(f"Code scanning failed: {e}") from e

    def _find_python_files(self) -> List[Path]:
        """Find Python files, excluding tests and configs."""
        if not self.code_root.exists():
            return []

        files = []

        for path in self.code_root.rglob("*.py"):
            # Skip excluded patterns
            path_str = str(path)
            if any(ex in path_str for ex in self.EXCLUDE_PATTERNS):
                continue
            files.append(path)

        # Sort by likely importance (priority keywords in filename)
        def priority_score(p: Path) -> int:
            name_lower = p.name.lower()
            return -sum(1 for kw in self.PRIORITY_KEYWORDS if kw in name_lower)

        files.sort(key=lambda p: (priority_score(p), p.name))

        return files[: self.max_files]

    def _read_files(self, files: List[Path]) -> List[Dict[str, str]]:
        """Read and sanitize file contents."""
        code_contents = []

        for filepath in files:
            try:
                content = filepath.read_text(encoding="utf-8", errors="ignore")

                # Truncate large files
                if len(content) > self.max_file_size:
                    content = content[: self.max_file_size] + "\n... (truncated)"

                # Sanitize
                rel_path = str(filepath.relative_to(self.code_root))
                sanitized = self.sanitizer.sanitize_file_content(rel_path, content)

                code_contents.append({
                    "file": rel_path,
                    "content": sanitized.sanitized_content,
                })

            except Exception:
                # Skip files that can't be read
                continue

        return code_contents

    def _read_readme(self) -> str | None:
        """Read README file if present."""
        readme_names = ["README.md", "README.rst", "README.txt", "README"]

        for name in readme_names:
            readme_path = self.code_root / name
            if readme_path.exists():
                try:
                    content = readme_path.read_text(encoding="utf-8", errors="ignore")
                    if len(content) > 5000:
                        content = content[:5000] + "\n... (truncated)"
                    return content
                except Exception:
                    pass

        return None

    async def _analyze_code(self, code_contents: List[Dict[str, str]]) -> CodeContext:
        """Send code to LLM for analysis."""
        prompt = build_code_analysis_prompt(code_contents)

        result = await self.llm.complete_json(
            prompt=prompt,
            schema=CODE_ANALYSIS_SCHEMA,
            system=SYSTEM_CODE_ANALYZER,
        )

        evidence_items = []
        for item in result.get("code_evidence", []) or []:
            try:
                evidence_items.append(
                    CodeEvidence(
                        path=item.get("file", ""),
                        symbol=item.get("symbol", ""),
                        statement=item.get("statement", ""),
                        snippet=item.get("snippet", ""),
                    )
                )
            except Exception:
                continue

        return CodeContext(
            files=[c["file"] for c in code_contents],
            model_classes=result.get("model_classes", []),
            features=result.get("features", []),
            target_variable=result.get("target_variable"),
            transformations=result.get("transformations", []),
            ml_task_type=result.get("ml_task_type"),
            hyperparameters=result.get("hyperparameters", {}),
            data_sources=result.get("data_sources", []),
            insights=result.get("insights", ""),
            code_evidence=evidence_items,
        )
