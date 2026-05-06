# Project setup guide — autodoc + MRM Portal

This guide covers what you need to configure in Domino before the Portal's
"Generate documentation" button will work end-to-end. It is the operational
complement to `autodoc-env-image.md` (wheel build + Dockerfile) — that doc
covers _what runs inside the compute container_; this doc covers _how the
surrounding Domino project is wired up_.

## Two projects, one purpose

| Project | Role |
|---------|------|
| **App project** (hosts Portal) | Runs the MRM Portal web application process. Portal reads the PlatformGraph, serves the UI, and submits Domino Jobs. |
| **Target project** (each model team's project) | Where Jobs run, where code lives under `/mnt/code`, where output `.docx` files land under the autodoc dataset, and where the context snapshot lives temporarily under `/mnt/data/{project}/autodoc_inputs/`. |

The App project only ever hosts the Portal process. It never runs autodoc
itself. All autodoc operations are scoped to the target project.

## Compute environment

Point the target project at the environment image built per
`autodoc-env-image.md`. autodoc must be importable at job start:

```
python -c "import autodoc; print('ok')"
```

If that fails, the wheel is not installed — rebuild the image.

## Required environment variables

Set these on **each target project** under Project → Settings → Environment Variables.

| Variable | Required | Example | Notes |
|----------|----------|---------|-------|
| `ANTHROPIC_API_KEY` | Yes (if using Anthropic) | `sk-ant-...` | LLM API key. Use `OPENAI_API_KEY` instead if switching providers. |
| `DOMINO_PROJECT_ID` | Auto-set by Domino | `6612abc...` | Injected by Domino into every Job container. Do not set manually. |
| `DOMINO_API_KEY` | Auto-set by Domino | `<ephemeral>` | Injected by Domino. This is what autodoc uses for any intra-Domino API calls. **Not the same as a PAT** — it rotates per session. |
| `AUTODOC_INPUTS_ROOT` | Yes | `/mnt/data/autodoc_mrm/autodoc_inputs` | Directory Portal writes `ctx_*.json` files into. Must be on a dataset mount visible to both Portal and Job. |
| `AUTODOC_JOB_INPUTS_ROOT` | Yes (same value) | `/mnt/data/autodoc_mrm/autodoc_inputs` | Same path, read by the Job side. Keeping them the same avoids confusion; they are separate env vars so Portal and Job can be decoupled later. |

> **Common mistake:** using a PAT or `DOMINO_USER_API_KEY` for auth inside a
> Job. The correct variable is `DOMINO_API_KEY` and the correct header is
> `X-Domino-Api-Key`. PATs return 401 from the governance endpoints.

## Dataset mount

autodoc writes output to the project's autodoc dataset, which is mounted under
`/mnt/data/{project_name}/` inside the Job container. This mount is created
automatically the first time autodoc runs (via `ensure_dataset` in
`domino_datasets.py`). No manual setup needed — but the Job must have
read-write access to the project's datasets.

## History database

The Portal keeps a job history SQLite database at
`AUTODOC_HISTORY_DB` (env var, defaults to a Portal-local writable path, not
`/mnt/data`). This is Portal-local — it does not need to be on a shared
dataset. Never point this at `/mnt/data`.

## Verifying the setup

After configuring the above, run this one-off Job in the target project using
the autodoc environment image:

```bash
python -c "
import os, autodoc
print('autodoc imported OK')
print('DOMINO_PROJECT_ID:', os.environ.get('DOMINO_PROJECT_ID', 'NOT SET'))
print('ANTHROPIC_API_KEY set:', bool(os.environ.get('ANTHROPIC_API_KEY')))
print('AUTODOC_INPUTS_ROOT:', os.environ.get('AUTODOC_INPUTS_ROOT', 'NOT SET'))
"
```

All four lines should be non-empty/non-NOT-SET. If `DOMINO_PROJECT_ID` is
missing, the dataset store initialisation will fail with `RuntimeError:
DOMINO_PROJECT_ID not set`.

## Full end-to-end smoke test

Once the above passes, run a real generation using a known context file:

```bash
# Write a ctx_test.json manually using the script in docs/deployment/
# (see 'Smoke test' section of autodoc-env-image.md for the script pattern)

python -m autodoc.main \
  --bundle-id <bundle_id> \
  --policy-version-id <policy_version_id> \
  --context-file /mnt/data/autodoc_mrm/autodoc_inputs/ctx_test.json \
  --derive-spec mdd \
  --provider anthropic \
  --verbose
```

Successful output ends with:
```
Success! Document generated:
  docs/model_docs_1.docx
```

The `.docx` file will appear in the autodoc dataset under
`/mnt/data/{project_name}/autodoc_docs/`.
