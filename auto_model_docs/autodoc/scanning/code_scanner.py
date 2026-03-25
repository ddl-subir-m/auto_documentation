"""Two-pass relevance-aware code scanner for ML codebases.

Pipeline:
  Stage 0: Hard filter (binary detection, exclude patterns)
  Stage 1: File card indexing (AST/regex, no LLM)
  Stage 2: LLM relevance ranking (1 cheap call, fallback to heuristics)
  Stage 3: Batched deep analysis (parallel LLM calls)
  Stage 4: Reducer merge (programmatic, no LLM)
"""

from __future__ import annotations

import ast
import asyncio
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from autodoc.core.exceptions import ScannerError
from autodoc.core.models import CodeContext, CodeEvidence, LanguageProfile, PYTHON_PROFILE
from autodoc.llm import LLMClient
from autodoc.llm.prompts import (
    CODE_ANALYSIS_SCHEMA,
    RANKING_SCHEMA,
    SYSTEM_CODE_ANALYZER,
    SYSTEM_FILE_RANKER,
    build_code_analysis_prompt,
    build_ranking_prompt,
)
from autodoc.scanning.file_card import FileCard, extract_file_card, is_binary_file
from autodoc.scanning.sanitizer import ContentSanitizer

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float], None]


class CodeScanner:
    """Two-pass relevance-aware code scanner.

    Stage 0-1: Discover and index files into compact file cards (no LLM).
    Stage 2:   LLM ranks files by ML relevance.
    Stage 3:   Deep analysis of top-ranked files in parallel batches.
    Stage 4:   Merge batch results into a single CodeContext.
    """

    def __init__(
        self,
        llm: LLMClient,
        sanitizer: ContentSanitizer,
        code_root: Path = Path("/mnt/code"),
        max_files: int = 100,
        max_file_size: int = 15000,
        profile: Optional[LanguageProfile] = None,
        exclude_patterns: Optional[List[str]] = None,
        max_selected_files: int = 15,
        batch_size: int = 4,
        analysis_timeout: float = 90.0,
        scan_retries: int = 2,
        scan_workers: int = 2,
    ):
        self.llm = llm
        self.sanitizer = sanitizer
        self.code_root = code_root
        self.max_files = max_files
        self.max_file_size = max_file_size
        self.profile = profile or PYTHON_PROFILE
        self.exclude_patterns = exclude_patterns or []
        self.max_selected_files = max_selected_files
        self.batch_size = batch_size
        self.analysis_timeout = analysis_timeout
        self.scan_retries = scan_retries
        self.scan_workers = scan_workers

    async def scan(
        self, on_progress: Optional[ProgressCallback] = None
    ) -> CodeContext:
        """Run the full 5-stage scanning pipeline.

        Returns:
            CodeContext with extracted ML information.

        Raises:
            ScannerError: If scanning fails entirely.
        """

        def report(progress: float) -> None:
            if on_progress:
                on_progress(progress)

        try:
            report(0.0)

            # ── Stage 0: Find and filter files ────────────────────────
            files = self._find_source_files()
            report(0.05)

            if not files:
                report(1.0)
                return CodeContext(
                    files=[],
                    insights=f"No {self.profile.display_name} files found in codebase.",
                    language=self.profile.name,
                )

            # ── Stage 1: Build file cards ─────────────────────────────
            file_cards = self._build_file_cards(files)
            report(0.10)

            if not file_cards:
                report(1.0)
                return CodeContext(
                    files=[str(f) for f in files],
                    insights=f"Could not extract file cards from {self.profile.display_name} files.",
                    language=self.profile.name,
                )

            # ── Stage 2: LLM ranking (with heuristic fallback) ───────
            ranked_files, file_roles = await self._rank_files(file_cards)
            report(0.25)

            # Select top files for deep analysis
            selected_paths = ranked_files[:self.max_selected_files]
            logger.info(
                "Stage 2: Selected %d/%d files for deep analysis",
                len(selected_paths), len(file_cards),
            )

            # ── README ────────────────────────────────────────────────
            readme_content = self._read_readme()

            # ── Stage 3: Batched deep analysis ────────────────────────
            batch_results, skipped_files = await self._batch_analyze(
                selected_paths, file_roles, report,
            )
            report(0.90)

            if not batch_results:
                raise ScannerError(
                    "All analysis batches failed. No results to merge. "
                    "Check LLM provider connectivity and timeout settings."
                )

            # ── Stage 4: Merge results ────────────────────────────────
            context = self._merge_results(batch_results, ranked_files)
            context.readme = readme_content
            context.language = self.profile.name
            context.skipped_files = skipped_files
            context.scan_incomplete = len(skipped_files) > 0

            if skipped_files:
                logger.warning(
                    "Scan incomplete: %d files skipped due to batch failures: %s",
                    len(skipped_files), skipped_files,
                )

            report(1.0)
            return context

        except ScannerError:
            raise
        except Exception as e:
            raise ScannerError(f"Code scanning failed: {e}") from e

    # ──────────────────────────────────────────────────────────────────
    # Stage 0: File discovery with hard filtering
    # ──────────────────────────────────────────────────────────────────

    def _find_source_files(self) -> List[Path]:
        """Find source files, excluding binaries, tests, and config patterns."""
        if not self.code_root.exists():
            return []

        # Combine profile excludes with config-driven excludes
        all_excludes = list(self.profile.exclude_patterns) + list(self.exclude_patterns)

        files = []
        for ext_pattern in self.profile.file_extensions:
            for path in self.code_root.rglob(ext_pattern):
                # Binary check
                if is_binary_file(path):
                    continue

                # Exclude pattern check
                try:
                    rel_path = str(path.relative_to(self.code_root))
                except ValueError:
                    rel_path = str(path)
                if any(ex in rel_path for ex in all_excludes):
                    continue

                files.append(path)

        # Sort by priority keywords (heuristic pre-ranking)
        keywords = self.profile.priority_keywords

        def priority_score(p: Path) -> int:
            name_lower = p.name.lower()
            return -sum(1 for kw in keywords if kw in name_lower)

        files.sort(key=lambda p: (priority_score(p), p.name))

        # Cap at max_files (default 100 for ranking, Stage 2 narrows further)
        return files[:self.max_files]

    # ──────────────────────────────────────────────────────────────────
    # Stage 1: File card extraction
    # ──────────────────────────────────────────────────────────────────

    def _build_file_cards(self, files: List[Path]) -> List[FileCard]:
        """Extract file cards from source files."""
        cards = []
        for filepath in files:
            card = extract_file_card(
                filepath, self.code_root, language=self.profile.name,
            )
            # Sanitize snippets before they reach any LLM prompt
            for i, snippet in enumerate(card.snippets):
                try:
                    rel = card.path
                    result = self.sanitizer.sanitize_file_content(rel, snippet)
                    card.snippets[i] = result.sanitized_content
                except Exception:
                    pass
            cards.append(card)

        logger.info("Stage 1: Built %d file cards", len(cards))
        return cards

    # ──────────────────────────────────────────────────────────────────
    # Stage 2: LLM ranking with heuristic fallback
    # ──────────────────────────────────────────────────────────────────

    async def _rank_files(
        self, file_cards: List[FileCard]
    ) -> tuple[List[str], Dict[str, str]]:
        """Rank files by ML relevance using LLM. Falls back to heuristic sort.

        Returns:
            (ranked_paths, file_roles) where file_roles maps path -> ML role.
        """
        file_roles: Dict[str, str] = {}

        try:
            prompt = build_ranking_prompt(file_cards, profile=self.profile)
            result = await asyncio.wait_for(
                self.llm.complete_json(
                    prompt=prompt,
                    schema=RANKING_SCHEMA,
                    system=SYSTEM_FILE_RANKER,
                ),
                timeout=self.analysis_timeout,
            )

            ranked = result.get("ranked_files", [])
            if not ranked:
                raise ValueError("LLM returned empty ranked_files")

            # Validate paths exist in our file cards
            card_paths = {c.path for c in file_cards}
            ranked_paths = []
            for entry in ranked:
                path = entry.get("path", "")
                role = entry.get("role", "unknown")
                if path in card_paths:
                    ranked_paths.append(path)
                    file_roles[path] = role

            if not ranked_paths:
                raise ValueError("LLM ranking returned no valid paths")

            logger.info(
                "Stage 2: LLM ranked %d files (top roles: %s)",
                len(ranked_paths),
                ", ".join(f"{p}: {file_roles[p]}" for p in ranked_paths[:3]),
            )
            return ranked_paths, file_roles

        except Exception as e:
            logger.warning("Stage 2 LLM ranking failed (%s), using heuristic fallback", e)
            # Heuristic fallback: files are already sorted by priority_keywords
            return [c.path for c in file_cards], file_roles

    # ──────────────────────────────────────────────────────────────────
    # Stage 3: Batched deep analysis
    # ──────────────────────────────────────────────────────────────────

    async def _batch_analyze(
        self,
        selected_paths: List[str],
        file_roles: Dict[str, str],
        report: Callable[[float], None],
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        """Analyze selected files in parallel batches.

        Returns:
            (batch_results, skipped_files)
        """
        # Group into batches
        batches: List[List[str]] = []
        for i in range(0, len(selected_paths), self.batch_size):
            batches.append(selected_paths[i : i + self.batch_size])

        logger.info(
            "Stage 3: Analyzing %d files in %d batches (workers=%d)",
            len(selected_paths), len(batches), self.scan_workers,
        )

        # Run batches with semaphore for concurrency control
        semaphore = asyncio.Semaphore(self.scan_workers)
        results: List[Optional[Dict[str, Any]]] = [None] * len(batches)
        skipped_files: List[str] = []

        async def analyze_batch(batch_idx: int, batch_paths: List[str]) -> None:
            async with semaphore:
                try:
                    result = await self._analyze_single_batch(batch_paths, file_roles)
                    results[batch_idx] = result
                except Exception as e:
                    logger.warning(
                        "Batch %d failed (%d files): %s",
                        batch_idx, len(batch_paths), e,
                    )
                    skipped_files.extend(batch_paths)

                # Update progress (Stage 3 spans 0.25 to 0.90)
                done = sum(1 for r in results if r is not None) + len(skipped_files) // max(self.batch_size, 1)
                frac = done / max(len(batches), 1)
                report(0.25 + frac * 0.65)

        tasks = [
            analyze_batch(i, batch)
            for i, batch in enumerate(batches)
        ]
        await asyncio.gather(*tasks)

        return [r for r in results if r is not None], skipped_files

    async def _analyze_single_batch(
        self, batch_paths: List[str], file_roles: Dict[str, str]
    ) -> Dict[str, Any]:
        """Analyze a single batch of files with the LLM.

        Sends raw code (no line annotations) to keep prompts small.
        Line numbers are resolved post-hoc via AST/string matching.
        """
        code_contents = []
        # Keep raw contents for post-hoc line resolution
        raw_contents: Dict[str, str] = {}

        for rel_path in batch_paths:
            filepath = self.code_root / rel_path
            try:
                content = filepath.read_text(encoding="utf-8", errors="ignore")
                raw_contents[rel_path] = content

                if len(content) > self.max_file_size:
                    content = content[:self.max_file_size] + "\n... (truncated)"

                # Sanitize (no line annotation — raw code sent to LLM)
                sanitized = self.sanitizer.sanitize_file_content(rel_path, content)
                code_contents.append({
                    "file": rel_path,
                    "content": sanitized.sanitized_content,
                })
            except Exception:
                logger.warning("Could not read file for batch analysis: %s", rel_path)
                continue

        if not code_contents:
            raise ScannerError(f"No readable files in batch: {batch_paths}")

        prompt = build_code_analysis_prompt(
            code_contents, profile=self.profile, file_roles=file_roles,
        )

        # Use per-call timeout (wraps around the client's own timeout)
        result = None
        for attempt in range(self.scan_retries + 1):
            try:
                result = await asyncio.wait_for(
                    self.llm.complete_json(
                        prompt=prompt,
                        schema=CODE_ANALYSIS_SCHEMA,
                        system=SYSTEM_CODE_ANALYZER,
                    ),
                    timeout=self.analysis_timeout,
                )
                break
            except asyncio.TimeoutError:
                if attempt < self.scan_retries:
                    logger.warning(
                        "Batch timeout (attempt %d/%d), retrying...",
                        attempt + 1, self.scan_retries + 1,
                    )
                    continue
                raise
            except Exception:
                if attempt < self.scan_retries:
                    continue
                raise

        if result is None:
            raise ScannerError("Batch analysis exhausted all retries")

        # Post-hoc line number resolution
        evidence = result.get("code_evidence", []) or []
        self._resolve_line_numbers(evidence, raw_contents)

        return result

    # ──────────────────────────────────────────────────────────────────
    # Post-hoc line number resolution
    # ──────────────────────────────────────────────────────────────────

    def _resolve_line_numbers(
        self,
        evidence: List[Dict[str, Any]],
        file_contents: Dict[str, str],
    ) -> None:
        """Resolve line numbers for evidence items using AST and string matching.

        Modifies evidence dicts in place, adding start_line/end_line.
        Parses each file once and reuses across all evidence items.
        """
        # Cache parsed ASTs and split lines per file
        ast_cache: Dict[str, ast.Module] = {}
        lines_cache: Dict[str, List[str]] = {}

        for path, content in file_contents.items():
            lines_cache[path] = content.split("\n")
            if path.endswith(".py"):
                try:
                    ast_cache[path] = ast.parse(content)
                except SyntaxError:
                    pass

        for item in evidence:
            filepath = item.get("file", "")
            content = file_contents.get(filepath, "")
            if not content:
                continue
            lines = lines_cache.get(filepath, [])

            # 1. AST symbol lookup (Python only)
            symbol = item.get("symbol", "")
            if symbol and filepath in ast_cache:
                start, end = self._find_symbol_span(ast_cache[filepath], symbol)
                if start:
                    item["start_line"] = start
                    item["end_line"] = end

            # 2. Snippet string match
            snippet = item.get("snippet", "")
            if snippet:
                snippet_clean = snippet.strip()
                # Fast path: single-line match
                matched = False
                for i, line in enumerate(lines, 1):
                    if snippet_clean in line:
                        if "start_line" not in item or item.get("start_line") is None:
                            item["start_line"] = i
                        item["end_line"] = i
                        matched = True
                        break

                # Slow path: multi-line substring match
                if not matched:
                    pos = content.find(snippet_clean)
                    if pos >= 0:
                        line_num = content[:pos].count("\n") + 1
                        end_num = line_num + snippet_clean.count("\n")
                        if "start_line" not in item or item.get("start_line") is None:
                            item["start_line"] = line_num
                        item["end_line"] = end_num

    @staticmethod
    def _find_symbol_span(
        tree: ast.Module, symbol: str
    ) -> Tuple[Optional[int], Optional[int]]:
        """Find the line span of a symbol using depth-first AST traversal.

        Handles bare names ("train_model") and dotted names ("MyClass.train").
        If duplicates exist, prefers the one with the longest body.
        """
        parts = symbol.split(".")
        target_name = parts[-1]

        candidates: List[Tuple[int, int]] = []

        def visit(node: ast.AST) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name == target_name:
                    end = getattr(node, "end_lineno", node.lineno)
                    candidates.append((node.lineno, end))
            for child in ast.iter_child_nodes(node):
                visit(child)

        visit(tree)

        if not candidates:
            return None, None
        if len(candidates) == 1:
            return candidates[0]
        # Prefer longest span (most likely the real definition)
        candidates.sort(key=lambda c: c[1] - c[0], reverse=True)
        return candidates[0]

    # ──────────────────────────────────────────────────────────────────
    # Stage 4: Merge batch results
    # ──────────────────────────────────────────────────────────────────

    def _merge_results(
        self,
        batch_results: List[Dict[str, Any]],
        ranked_paths: List[str],
    ) -> CodeContext:
        """Merge multiple batch results into a single CodeContext.

        List fields: union with deduplication.
        Single-valued fields: prefer batch containing highest-ranked file.
        """
        if len(batch_results) == 1:
            return self._parse_result(batch_results[0])

        # Determine batch rank: min rank of files in each batch's evidence
        def batch_rank(result: Dict[str, Any]) -> int:
            evidence_files = {
                e.get("file", "") for e in result.get("code_evidence", [])
            }
            ranks = [
                ranked_paths.index(f) if f in ranked_paths else 999
                for f in evidence_files
            ]
            return min(ranks) if ranks else 999

        # Sort batch results by rank (best first)
        sorted_results = sorted(batch_results, key=batch_rank)

        # Merge list fields (union)
        all_model_classes: List[str] = []
        all_features: List[str] = []
        all_transformations: List[Dict[str, Any]] = []
        all_data_sources: List[str] = []
        all_evidence: List[CodeEvidence] = []
        all_insights: List[str] = []
        merged_hyperparams: Dict[str, Any] = {}
        all_files: List[str] = []

        # Single-valued fields: take from highest-ranked batch
        target_variable = None
        ml_task_type = None

        for result in sorted_results:
            all_model_classes.extend(result.get("model_classes", []))
            all_features.extend(result.get("features", []))
            all_transformations.extend(result.get("transformations", []))
            all_data_sources.extend(result.get("data_sources", []))

            # Merge hyperparameters (later batches don't overwrite)
            for k, v in result.get("hyperparameters", {}).items():
                if k not in merged_hyperparams:
                    merged_hyperparams[k] = v

            # Single-valued: first (highest-ranked) wins
            if target_variable is None and result.get("target_variable"):
                target_variable = result["target_variable"]
            if ml_task_type is None and result.get("ml_task_type"):
                ml_task_type = result["ml_task_type"]

            # Insights: concatenate
            insight = result.get("insights", "")
            if insight:
                all_insights.append(insight)

            # Evidence
            for item in result.get("code_evidence", []) or []:
                try:
                    all_evidence.append(CodeEvidence(
                        path=item.get("file", ""),
                        symbol=item.get("symbol", ""),
                        statement=item.get("statement", ""),
                        snippet=item.get("snippet", ""),
                        start_line=item.get("start_line"),
                        end_line=item.get("end_line"),
                    ))
                except Exception:
                    continue

        # Deduplicate lists
        seen_classes = set()
        deduped_classes = []
        for c in all_model_classes:
            if c not in seen_classes:
                seen_classes.add(c)
                deduped_classes.append(c)

        seen_features = set()
        deduped_features = []
        for f in all_features:
            if f not in seen_features:
                seen_features.add(f)
                deduped_features.append(f)

        seen_sources = set()
        deduped_sources = []
        for s in all_data_sources:
            if s not in seen_sources:
                seen_sources.add(s)
                deduped_sources.append(s)

        return CodeContext(
            files=ranked_paths[:self.max_selected_files],
            model_classes=deduped_classes,
            features=deduped_features,
            target_variable=target_variable,
            transformations=all_transformations,
            ml_task_type=ml_task_type,
            hyperparameters=merged_hyperparams,
            data_sources=deduped_sources,
            insights=" | ".join(all_insights) if all_insights else "",
            code_evidence=all_evidence,
        )

    def _parse_result(self, result: Dict[str, Any]) -> CodeContext:
        """Parse a single batch result into CodeContext."""
        evidence_items = []
        for item in result.get("code_evidence", []) or []:
            try:
                evidence_items.append(CodeEvidence(
                    path=item.get("file", ""),
                    symbol=item.get("symbol", ""),
                    statement=item.get("statement", ""),
                    snippet=item.get("snippet", ""),
                    start_line=item.get("start_line"),
                    end_line=item.get("end_line"),
                ))
            except Exception:
                continue

        return CodeContext(
            files=[],
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

    # ──────────────────────────────────────────────────────────────────
    # Utility
    # ──────────────────────────────────────────────────────────────────

    def _read_readme(self) -> Optional[str]:
        """Read README file if present."""
        for name in ["README.md", "README.rst", "README.txt", "README"]:
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
