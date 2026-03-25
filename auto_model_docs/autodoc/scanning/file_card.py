"""File card extraction for the two-pass code analysis pipeline.

Stage 1: Extracts compact "file cards" from source files using AST (Python)
or regex (R/SAS/MATLAB). File cards contain path, imports, symbols, docstrings,
and sampled snippets — enough for an LLM to rank relevance without seeing
full file contents.
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

BINARY_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".pkl", ".pickle",
    ".h5", ".hdf5", ".parquet", ".feather", ".arrow", ".npy", ".npz",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".tiff", ".svg",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z",
    ".whl", ".egg", ".class", ".jar", ".war",
    ".o", ".a", ".lib", ".dylib",
    ".db", ".sqlite", ".sqlite3",
    ".mp3", ".mp4", ".wav", ".avi", ".mov",
}


@dataclass
class FileCard:
    """Compact representation of a source file for LLM ranking."""

    path: str
    language: str
    size: int
    imports: List[str] = field(default_factory=list)
    symbols: List[str] = field(default_factory=list)
    docstring: str = ""
    snippets: List[str] = field(default_factory=list)
    ml_role: str = "unknown"

    def to_prompt_text(self) -> str:
        """Format the file card for inclusion in an LLM prompt."""
        parts = [f"### {self.path} ({self.size} bytes, {self.language})"]
        if self.imports:
            parts.append(f"Imports: {', '.join(self.imports[:20])}")
        if self.symbols:
            parts.append(f"Symbols: {', '.join(self.symbols[:20])}")
        if self.docstring:
            parts.append(f"Docstring: {self.docstring[:300]}")
        if self.snippets:
            for i, snippet in enumerate(self.snippets[:3], 1):
                parts.append(f"Snippet {i}:\n{snippet}")
        return "\n".join(parts)


def is_binary_file(filepath: Path) -> bool:
    """Check if a file is binary by extension or null-byte detection."""
    if filepath.suffix.lower() in BINARY_EXTENSIONS:
        return True
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(8192)
            return b"\x00" in chunk
    except (OSError, IOError):
        return True


def extract_file_card(
    filepath: Path,
    code_root: Path,
    language: str = "python",
) -> FileCard:
    """Extract a compact file card from a source file.

    Falls back to metadata-only card if parsing fails.
    """
    try:
        rel_path = str(filepath.relative_to(code_root))
    except ValueError:
        rel_path = str(filepath)

    size = filepath.stat().st_size if filepath.exists() else 0

    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        logger.warning("Could not read file for card: %s", rel_path)
        return FileCard(path=rel_path, language=language, size=size)

    try:
        if language == "python":
            imports, symbols, docstring, snippets = _extract_python_card(content)
        else:
            imports, symbols, docstring, snippets = _extract_regex_card(content, language)
    except Exception:
        logger.warning("Card extraction failed for %s, using metadata-only", rel_path)
        # Metadata-only fallback: first 500 chars as snippet
        fallback_snippet = content[:500].strip()
        return FileCard(
            path=rel_path,
            language=language,
            size=size,
            snippets=[fallback_snippet] if fallback_snippet else [],
        )

    return FileCard(
        path=rel_path,
        language=language,
        size=size,
        imports=imports,
        symbols=symbols,
        docstring=docstring,
        snippets=snippets,
    )


def _extract_python_card(content: str) -> Tuple[List[str], List[str], str, List[str]]:
    """AST-based extraction for Python files."""
    tree = ast.parse(content)

    # Imports
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.append(module)

    # Top-level symbols (functions and classes)
    symbols = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(f"def {node.name}")
        elif isinstance(node, ast.ClassDef):
            symbols.append(f"class {node.name}")

    # Module docstring
    docstring = ast.get_docstring(tree) or ""
    if len(docstring) > 300:
        docstring = docstring[:300] + "..."

    # Sampled snippets: first function body, main block, or longest function
    snippets = _sample_python_snippets(tree, content)

    return imports, symbols, docstring, snippets


def _sample_python_snippets(tree: ast.Module, content: str) -> List[str]:
    """Extract 1-3 representative code snippets from a Python AST."""
    lines = content.split("\n")
    snippets = []

    # Find top-level functions/classes
    funcs = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno - 1
            end = node.end_lineno or (start + 1)
            body = "\n".join(lines[start:end])
            funcs.append((len(body), body, node.name))

    if not funcs:
        # No functions/classes — take first 500 chars
        snippet = "\n".join(lines[:30])[:500]
        if snippet.strip():
            snippets.append(snippet)
        return snippets

    # Sort by size descending, take up to 3
    funcs.sort(key=lambda x: x[0], reverse=True)
    for _, body, _ in funcs[:3]:
        # Cap each snippet at 500 chars
        if len(body) > 500:
            body = body[:500] + "\n..."
        snippets.append(body)

    return snippets


def _extract_regex_card(
    content: str, language: str
) -> Tuple[List[str], List[str], str, List[str]]:
    """Regex-based extraction for R, SAS, and MATLAB files."""
    if language == "r":
        return _extract_r_card(content)
    elif language == "sas":
        return _extract_sas_card(content)
    elif language == "matlab":
        return _extract_matlab_card(content)
    else:
        # Generic fallback
        return [], [], "", [content[:500]] if content.strip() else []


def _extract_r_card(content: str) -> Tuple[List[str], List[str], str, List[str]]:
    """Extract imports, symbols, docstring, snippets from R code."""
    # Imports: library() and require()
    imports = re.findall(r'(?:library|require)\s*\(\s*["\']?(\w+)["\']?\s*\)', content)

    # Symbols: function definitions
    symbols = [
        f"def {m.group(1)}"
        for m in re.finditer(r'(\w+)\s*<-\s*function\s*\(', content)
    ]

    # First comment block as docstring
    lines = content.split("\n")
    doc_lines = []
    for line in lines:
        if line.strip().startswith("#"):
            doc_lines.append(line.strip().lstrip("# "))
        elif doc_lines:
            break
    docstring = " ".join(doc_lines)[:300]

    # Snippets: first 500 chars
    snippets = [content[:500]] if content.strip() else []

    return imports, symbols, docstring, snippets


def _extract_sas_card(content: str) -> Tuple[List[str], List[str], str, List[str]]:
    """Extract macros, procs, and symbols from SAS code."""
    # Imports: %include and libname
    imports = re.findall(r'%include\s+["\']?([^"\';\s]+)', content, re.IGNORECASE)
    imports += re.findall(r'libname\s+(\w+)', content, re.IGNORECASE)

    # Symbols: %macro names and PROC names
    symbols = [
        f"macro {m}" for m in re.findall(r'%macro\s+(\w+)', content, re.IGNORECASE)
    ]
    symbols += [
        f"proc {m}" for m in re.findall(r'proc\s+(\w+)', content, re.IGNORECASE)
    ]

    docstring = ""
    snippets = [content[:500]] if content.strip() else []

    return imports, symbols, docstring, snippets


def _extract_matlab_card(content: str) -> Tuple[List[str], List[str], str, List[str]]:
    """Extract function declarations from MATLAB code."""
    imports = []

    # Symbols: function declarations
    symbols = [
        f"def {m.group(1)}"
        for m in re.finditer(r'function\s+(?:\[?\w+(?:,\s*\w+)*\]?\s*=\s*)?(\w+)\s*\(', content)
    ]

    # First comment block as docstring
    lines = content.split("\n")
    doc_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("%"):
            doc_lines.append(stripped.lstrip("% "))
        elif doc_lines:
            break
    docstring = " ".join(doc_lines)[:300]

    snippets = [content[:500]] if content.strip() else []

    return imports, symbols, docstring, snippets
