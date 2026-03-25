# Auto Model Docs Studio

Generate ML model documentation automatically by scanning codebases and MLflow artifacts with LLM-powered analysis. Produces Word documents and Jupyter notebooks with evidence-traced citations back to source code.

## How it works

```
Codebase + MLflow ──> Scan ──> Plan ──> Generate ──> Build
                       |         |          |           |
                   File cards  Section   Narrative    .docx
                   + LLM rank  plans     + tables    + .ipynb
                                         + charts
```

The scanner uses a **two-pass relevance-aware pipeline**:

1. **Hard filter** -- exclude tests, binaries, vendored code, lockfiles
2. **File cards** -- extract imports, symbols, docstrings, and snippets via AST (Python) or regex (R/SAS/MATLAB) without any LLM call
3. **LLM ranking** -- a single cheap LLM call classifies files by ML role (training, preprocessing, inference, evaluation) and ranks by documentation importance
4. **Batched deep analysis** -- only the top-ranked files are analyzed in parallel batches, with per-call timeouts and partial failure handling
5. **Post-hoc line resolution** -- citation line numbers are resolved deterministically via AST/string matching after the LLM responds, keeping prompts small and citations accurate

## Supported languages

| Language | File card extraction | Deep analysis |
|----------|---------------------|---------------|
| Python   | AST (full)          | Yes           |
| R        | Regex               | Yes           |
| SAS      | Regex               | Yes           |
| MATLAB   | Regex               | Yes           |

## Quick start

### Web UI (Domino App)

```bash
python auto_model_docs/web_app.py
# Opens at http://0.0.0.0:8888
```

The web UI provides a 3-column workflow: spec file selection, configuration, and output/history. Supports both "Run in App" (interactive) and "Domino Job" (batch) execution modes.

### Stitch UI (Blueprint Enterprise)

```bash
python auto_model_docs/web_app_studio.py
# Opens at http://0.0.0.0:8888
```

Redesigned UI with the Blueprint Enterprise design system: tonal surface layering, Manrope/Inter typography, hardware tier card grid, and glassmorphism action bar.

### CLI

```bash
python auto_model_docs/main.py --spec doc_spec.yaml --code-root /path/to/code
```

## Configuration

All settings can be configured via environment variables (with or without `AUTODOC_` prefix) or a `.env` file.

### LLM provider

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `anthropic` | `anthropic` or `openai` (OpenAI-compatible) |
| `LLM_MODEL` | Provider default | Model name override |
| `ANTHROPIC_API_KEY` | -- | Anthropic API key |
| `OPENAI_API_KEY` | -- | OpenAI/compatible API key |
| `OPENAI_BASE_URL` | -- | Base URL for OpenAI-compatible APIs (e.g., Moonshot) |

### Scanning pipeline

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_FILES` | `100` | Max files discovered for ranking |
| `MAX_FILE_SIZE` | `15000` | Max chars per file in deep analysis |
| `MAX_SELECTED_FILES` | `15` | Files selected for deep analysis after ranking |
| `BATCH_SIZE` | `4` | Files per deep analysis batch |
| `ANALYSIS_TIMEOUT` | `90` | Per-batch LLM timeout in seconds |
| `SCAN_RETRIES` | `2` | Retries per batch (fail fast) |
| `SCAN_WORKERS` | `2` | Parallel batch workers for scanning |
| `EXCLUDE_PATTERNS` | tests, __pycache__, .git, ... | Path patterns to exclude |

### Generation

| Variable | Default | Description |
|----------|---------|-------------|
| `PARALLEL_WORKERS` | `1` | Parallel content generation workers |
| `PLANNING_WORKERS` | `1` | Parallel section planning workers |
| `CACHE_ENABLED` | `true` | Enable LLM response caching |
| `MLFLOW_TRACKING_URI` | -- | MLflow tracking server URI |

## Architecture

```
auto_model_docs/
  autodoc/                    # Core package
    core/
      config.py               # Settings (pydantic-settings)
      models.py               # Data models, language profiles, spec validation
      exceptions.py           # Exception hierarchy
    scanning/
      code_scanner.py         # Two-pass scanning pipeline (Stages 0-4)
      file_card.py            # File card extraction (AST/regex)
      artifact_scanner.py     # MLflow artifact scanning
      sanitizer.py            # Secret redaction before LLM calls
    llm/
      client.py               # LLM client (Anthropic + OpenAI-compatible)
      prompts.py              # All prompt templates and JSON schemas
      cache.py                # LLM response caching
    generation/
      planner.py              # Section content planning
      generator.py            # Content generation (narrative, table, chart, list)
      builder.py              # Word document assembly
      notebook_builder.py     # Jupyter notebook generation
      citations.py            # Citation building, parsing, registry
    orchestrator.py           # 4-phase pipeline coordinator
  web_app.py                  # FastHTML web UI (original)
  web_app_studio.py           # FastHTML web UI (Blueprint Enterprise)
  studio/                     # Studio UI package (styles, scripts, routes)
  main.py                     # CLI entry point
  doc_spec.yaml               # Default spec file
```

### Scanning pipeline detail

```
code_root
  |
  v
Stage 0: _find_source_files()
  Glob by language extensions, exclude binaries + patterns, cap at max_files
  |
  v
Stage 1: _build_file_cards()
  AST (Python) or regex (R/SAS/MATLAB) extraction per file
  Output: FileCard with path, imports, symbols, docstring, snippets
  |
  v
Stage 2: _rank_files()
  1 LLM call with all file cards (~75-100K chars)
  Classify by ML role, rank by importance
  Fallback: heuristic sort by priority keywords if LLM fails
  |
  v
Stage 3: _batch_analyze()
  Top 15 files in parallel batches of 4
  Raw code (no line annotations) sent to LLM
  Per-call timeout + partial failure handling
  |
  v
Stage 4: _merge_results() + _resolve_line_numbers()
  Programmatic merge: union lists, resolve single-value conflicts
  AST/string match for citation line numbers (post-hoc)
  |
  v
CodeContext (same output contract for downstream)
```

## Domino integration

When deployed as a Domino App:

- **Execution modes**: "Run in App" (interactive, results in browser) or "Domino Job" (batch, results in Artifacts tab)
- **Dataset browser**: Browse Domino Datasets for spec files
- **Hardware tiers**: Select compute tier for Domino Jobs (card grid UI)
- **Job history**: SQLite-backed job tracking with status polling
- **Cross-project**: Run jobs against other Domino projects via `projectId` query param

Key environment variables for Domino:
- `DOMINO_API_HOST`, `DOMINO_API_PROXY`, `DOMINO_USER_API_KEY`
- `DOMINO_PROJECT_NAME`, `DOMINO_PROJECT_OWNER`
- `AUTODOC_MAX_JOBS` (default: 1 per user)

## Testing

```bash
# Run all tests (requires Python 3.10+)
.venv/bin/python -m pytest tests/ -v

# Run a specific test file
.venv/bin/python -m pytest tests/test_sanitizer.py -v
```

Test suite: 440+ tests covering security (sanitizer patterns), LLM client (retry/timeout logic), data models (spec validation, language detection), scanning pipeline (all 5 stages), line resolution, config validation, citations, caching, and exceptions.

## Spec file format

Documentation structure is defined in a YAML spec file:

```yaml
title: "Model Documentation"
authors: "Data Science Team"
sections:
  - "Model Overview"
  - "Data Description"
  - "Training Pipeline"
  - "Evaluation Metrics per_model"  # Generates one section per MLflow model
  - "Deployment Guide"
```

## License

MIT
