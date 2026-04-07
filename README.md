# Auto Model Documentation

Automatically generate professional ML model documentation from codebases and MLflow artifacts using LLM-powered analysis.

Auto Model Docs scans your ML codebase (Python, R, SAS, or MATLAB), queries MLflow for model metadata (metrics, parameters, artifacts), and uses an LLM to produce comprehensive Word documents and Jupyter notebooks — no manual writing required.


## Key Features

- **LLM-powered code analysis** — understands model architecture, feature engineering, and training logic from source code
- **MLflow integration** — pulls registered models, experiment runs, metrics, parameters, and artifact images
- **Multiple output formats** — generates `.docx` Word documents and editable `.ipynb` Jupyter notebooks
- **Multi-provider LLM support** — works with Anthropic Claude and OpenAI (including OpenAI-compatible endpoints)
- **Document specification via YAML** — define sections, per-model breakdowns, and content hints
- **Web UI and CLI** — use the FastHTML web interface or the Click-based command line
- **Parallel generation** — async pipeline with configurable worker pools for fast generation
- **Smart caching** — cache LLM responses for instant notebook regeneration
- **Content sanitization** — automatically strips secrets and credentials before sending code to the LLM
- **Domino-ready** — built-in defaults for Domino Data Lab environments

## Architecture

The pipeline runs in four phases:

```
 SCAN ──────────► PLAN ──────────► GENERATE ──────────► BUILD
 (parallel)       (parallel)       (parallel)           (sequential)

 CodeScanner      SectionPlanner   ContentGenerator     DocumentBuilder
 └─ Python files  └─ LLM plans     └─ Narratives        └─ .docx
 └─ LLM analysis     content       └─ Tables            NotebookBuilder
                     blocks         └─ Charts            └─ .ipynb
 ArtifactScanner                   └─ Lists             CacheManager
 └─ MLflow models                  └─ Images            └─ .autodoc_cache.json
 └─ Metrics/params
```

## Quick Start

### 1. Install

Create an environment and install:

```bash
cd auto_model_docs
pip install -e ".[dev]"
```

Requires **Python 3.10+**.

### 2. Configure

Copy the example environment file and add your API key:

```bash
cp .env.example .env
```

```env
# Required — at least one API key
ANTHROPIC_API_KEY=sk-ant-...
# or
OPENAI_API_KEY=sk-...

# Optional
LLM_PROVIDER=openai          # or anthropic
MLFLOW_TRACKING_URI=http://localhost:5000
CODE_ROOT=/path/to/your/ml/project
```

### 3. Create a document spec

Define which sections to generate in a YAML file (see [`doc_spec.yaml`](auto_model_docs/doc_spec.yaml) for a full example):

```yaml
title: "Machine Learning Model Documentation"
authors: "Data Science Team"

sections:
  - Executive Summary
  - Data Overview
  - Feature Engineering
  - Model Architecture
  - "Model Performance: per_model"   # creates a subsection per registered model
  - Deployment Considerations

hints:
  "Executive Summary": >
    Focus on business impact and key metrics.
    Keep it suitable for non-technical stakeholders.
```

### 4. Run

**CLI:**

```bash
cd auto_model_docs
python main.py --spec doc_spec.yaml --provider openai
```

**Web UI (Studio):**

```bash
cd auto_model_docs
python web_app_studio.py
# Open http://localhost:8888
```

The generated document will be saved to the `output/` directory.

## CLI Reference

```
python main.py --spec <YAML> [OPTIONS]
```

| Option | Short | Description |
|---|---|---|
| `--spec` | `-s` | Path to YAML document specification (required) |
| `--provider` | `-p` | LLM provider: `anthropic` or `openai` (default: `openai`) |
| `--model` | `-m` | Model name override (e.g. `gpt-4o`, `claude-sonnet-4-20250514`) |
| `--code-root` | `-c` | Root directory of the ML codebase to analyze |
| `--output` | `-o` | Output directory for generated documents |
| `--notebook` | | Also generate an editable Jupyter notebook |
| `--notebook-from-cache` | | Regenerate notebook from cached results (skips full pipeline) |
| `--notebook-path` | | Custom path for the generated notebook |
| `--experiments` | | Comma-separated experiment name patterns (supports `*` and `?` wildcards) |
| `--models` | | Comma-separated model name patterns (supports wildcards) |
| `--latest-only` | | Only include the latest version of each model |
| `--generation-workers` | `-w` | Parallel content generation workers (default: 4) |
| `--planning-workers` | | Parallel section planning workers (default: 4) |
| `--max-files` | | Maximum number of source files to scan (default: 50) |
| `--timeout` | | Timeout per LLM API call in seconds (default: 120) |
| `--max-retries` | | Max retries for LLM requests |
| `--initial-backoff` | | Initial backoff delay in seconds |
| `--max-backoff` | | Maximum backoff delay in seconds |
| `--backoff-jitter` | | Random jitter factor applied to backoff |
| `--disable-project-filtering` | | Disable automatic Domino project filtering (scan all projects) |
| `--verbose` | `-v` | Enable verbose logging |

### Examples

```bash
# Generate docs with Anthropic Claude
python main.py -s doc_spec.yaml -p anthropic

# Generate docs + notebook, filtering to specific models
python main.py -s doc_spec.yaml --notebook --models "churn_*" --latest-only

# Quickly regenerate notebook from cache (no LLM calls)
python main.py -s doc_spec.yaml --notebook-from-cache

# Use a custom OpenAI-compatible endpoint
OPENAI_BASE_URL=http://localhost:8080/v1 python main.py -s doc_spec.yaml -p openai
```

## Web UI

The web interface provides form-based configuration, real-time progress monitoring, log streaming, and file downloads.

<p align="center">
  <img src="diagrams/screenshots/auto_model_config_screen.png" alt="Configuration" width="400"/>
  <img src="diagrams/screenshots/auto_model_in_progress.png" alt="In Progress" width="400"/>
  <img src="diagrams/screenshots/auto_model_completed.png" alt="Completed" width="400"/>
  <img src="diagrams/screenshots/auto_model_full_screen.png" alt="Full Screen" width="400"/>
</p>

```bash
python web_app_studio.py
```


## Configuration Reference

All settings can be set via environment variables, a `.env` file, or CLI flags. Variables can optionally be prefixed with `AUTODOC_` for namespace isolation.

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `openai` | LLM provider (`anthropic` or `openai`) |
| `LLM_MODEL` | Provider default | Model name override |
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `OPENAI_BASE_URL` | — | Custom OpenAI-compatible endpoint URL |
| `CODE_ROOT` | `/mnt/code` or `.` | Codebase root directory |
| `OUTPUT_DIR` | `/mnt/data/{project}` or `./output` | Output directory |
| `MLFLOW_TRACKING_URI` | — | MLflow tracking server URI |
| `MAX_FILES` | `50` | Max Python files to scan (1-200) |
| `MAX_FILE_SIZE` | `15000` | Max file size in characters |
| `PARALLEL_WORKERS` | `4` | Content generation workers |
| `CACHE_ENABLED` | `true` | Enable LLM response caching |

## Sample Projects

Three realistic ML projects are included for testing under [`sample_projects/`](sample_projects/):

| Project | Task | Models |
|---|---|---|
| `01_customer_churn` | Binary classification (10K samples) | Logistic Regression, Random Forest, XGBoost |
| `02_price_prediction` | Regression (8K samples) | Ridge, Gradient Boosting, Neural Network |
| `03_fraud_detection` | Imbalanced classification (50K samples) | Random Forest, XGBoost, LightGBM, Ensemble |

To run them:

```bash
cd sample_projects

# Start a local MLflow server if running locally
mlflow server --backend-store-uri sqlite:///mlflow.db --default-artifact-root ./mlruns --port 5000 &

# Train all three projects (creates 9 experiments, 10 model versions)
cd 01_customer_churn && python train.py --generate-data && cd ..
cd 02_price_prediction && python train.py --generate-data && cd ..
cd 03_fraud_detection && python train.py --generate-data && cd ..

# Generate documentation
cd ../auto_model_docs
python main.py -s doc_spec.yaml -p openai --notebook -v
```

## Project Structure

```
auto_model_docs/
├── main.py                  # CLI entry point
├── web_app_studio.py        # FastHTML web UI (Studio)
├── doc_spec.yaml            # Example document specification
├── domino_auth.py           # Shared Domino API host + auth resolution
├── domino_client.py         # Domino API client (jobs, projects, branches)
├── domino_datasets.py       # Domino Datasets API client (browse, upload)
├── domino_job_store.py      # SQLite job history (per-project)
├── spec_store.py            # Spec file persistence
├── auth_context.py          # Per-request JWT forwarding via ContextVar
├── studio/                  # Studio UI package
│   ├── state.py             # Shared mutable state, dataclasses, helpers
│   ├── job_engine.py        # Job submission, command building, background polling
│   ├── routes_api.py        # API routes (branches, tiers, datasets, language detection)
│   ├── routes_job.py        # Job routes (run, stop, history)
│   ├── routes_spec.py       # Spec routes (validate, save, list, delete)
│   ├── ui_components.py     # FT component helpers (forms, status panels, tables)
│   ├── styles.py            # CSS generation
│   └── scripts.py           # JS generation
├── autodoc/
│   ├── orchestrator.py      # 4-phase pipeline coordinator
│   ├── core/
│   │   ├── config.py        # Pydantic settings (env vars)
│   │   ├── models.py        # Domain models (CodeContext, ArtifactContext, etc.)
│   │   └── exceptions.py    # Custom exceptions
│   ├── scanning/
│   │   ├── code_scanner.py  # Two-pass code analysis (Python, R, SAS, MATLAB)
│   │   ├── artifact_scanner.py  # MLflow metadata extraction
│   │   ├── file_card.py     # Source file extraction and language detection
│   │   └── sanitizer.py     # Secret removal before LLM calls
│   ├── generation/
│   │   ├── planner.py       # Section content planning
│   │   ├── generator.py     # Content generation (narratives, tables, charts, lists)
│   │   ├── builder.py       # Word document assembly
│   │   ├── notebook_builder.py   # Jupyter notebook generation
│   │   ├── notebook_exporter.py  # Notebook export and execution
│   │   └── citations.py     # Citation management
│   └── llm/
│       ├── client.py        # Unified LLM client (Anthropic/OpenAI)
│       ├── cache.py         # Response caching
│       └── prompts.py       # Prompt templates
├── tests/                   # Unit tests (pytest)
sample_projects/             # 3 demo ML projects with MLflow integration
diagrams/                    # Screenshots and workflow diagrams
```

## Testing

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
python -m pytest tests/

# Run with coverage
python -m pytest tests/ --cov=auto_model_docs --cov-config=auto_model_docs/pyproject.toml --cov-report=term-missing
```

## License

MIT
