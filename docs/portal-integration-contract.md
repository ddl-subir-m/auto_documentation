# Portal ↔ autodoc integration contract

This document defines the exact interface between the MRM Portal and the
autodoc Domino Job. Both sides must honour it. Any drift — extra keys in the
context file, a changed CLI flag order, a different output path — will silently
break generation.

**Portal side lives in:** `domino-field/MRM-Portal @ feature/autodoc-integration`  
**autodoc side lives in:** `domino-field/auto_documentation @ feature/mrm-portal-integration`

---

## 1. Context file

### Who writes it

`routes/autodoc.py` in Portal writes the file immediately before submitting
the Domino Job.

### Path

```
/mnt/data/{project}/autodoc_inputs/ctx_{run_id}.json
```

- `{project}` — the target project name (same project the model bundle lives in)
- `{run_id}` — a UUID4 hex string, unique per generation run
- Directory is created with `os.makedirs(..., exist_ok=True)` at write time
- File permissions: `0o600`

### Schema

```json
{
  "bundle_id":         "b94e8f84-bb2c-4f45-a3ad-51356b992bed",
  "policy_version_id": "86b13861-d52b-4487-9c12-96cce1ad4b24",
  "bundle":            { ...raw bundle dict from PlatformGraph... },
  "policy_def":        { ...raw response from GET /api/governance/v1/policies/{id}/definition... }
}
```

**Exactly four top-level keys, all required.** `bundle_context.py::load_context`
validates this at Job start and raises `BundleContextError` if any key is
missing or the wrong type.

| Key | Type | Source |
|-----|------|--------|
| `bundle_id` | `str` | From request body |
| `policy_version_id` | `str` | From request body |
| `bundle` | `dict` | `graph.nodes[f"GovernanceBundle:{bundle_id}"]` — force-refreshed for this bundle before snapshotting |
| `policy_def` | `dict` | Raw JSON from `GET /api/governance/v1/policies/{policy_id}/definition` |

> `policy_def` is the **raw API response**, not a re-shaped version. autodoc's
> `spec_from_policy.py` knows how to walk both the Domino "stages" shape and the
> simpler "required_artifacts/sections" flat shape. Do not pre-process it.

### Who deletes it

The autodoc Job deletes the file in its `finally` block
(`bundle_context.delete_context_file`). This is best-effort — the file is
silently dropped if already gone. Portal's hourly cleanup cron
(`shared/autodoc_cleanup.py`) removes any orphans older than 60 minutes that
the Job did not clean up (e.g. crash before `finally`).

---

## 2. Job submission

### Command Portal submits

```
python -m autodoc.main \
  --bundle-id {bundle_id} \
  --policy-version-id {policy_version_id} \
  --context-file {context_file_path} \
  --derive-spec {template_lower} \
  --provider anthropic \
  --output-file {output_file_path}
```

Where:
- `{template_lower}` is `mdd`, `vr`, or `mr` (lowercase of the UI template choice)
- `{output_file_path}` is the filesystem path Portal will read the `.docx` back from (see section 3)
- `--canonical-spec` is used instead of `--derive-spec` when the user selects "Use canonical template" in the modal

### Required env vars in the Job container

| Variable | Provided by |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Target project settings |
| `DOMINO_PROJECT_ID` | Domino (auto-injected) |
| `DOMINO_API_KEY` | Domino (auto-injected) |
| `AUTODOC_JOB_INPUTS_ROOT` | Target project settings |

### Job submitted via

`POST /v4/jobs` (platform API, not governance API). Auth uses the Portal
service account's credentials, not the viewer's JWT — the viewer's identity is
recorded in the history DB and the provenance stamp but does not flow into the
Job auth.

---

## 3. Output handoff

### Path convention

```
/mnt/data/{project}/autodoc_docs/{model}_{template}_{yyyymmdd}_v{N}.docx
```

autodoc writes to its dataset store first (logical path `docs/...`), then
copies bytes to the filesystem path specified via `--output-file` so Portal can
read it via the shared dataset mount.

### How Portal reads it

When `GET /api/docs/status/{job_id}` detects the terminal `success` state, it
reads the `.docx` from `--output-file` path and calls:

```
shared.domino_client.post_bundle_attachment(bundle_id, file_bytes, label)
```

Label format: `"autodoc:{template}:{yyyymmdd}"` (e.g. `"autodoc:mdd:20260506"`).

If a prior autodoc attachment exists for this `(bundle_id, template)` pair
(tracked in `autodoc_history.attachment_id`), the new attachment is created
first, then the old one is deleted. This ensures the bundle is never left
without an attachment during the transition.

---

## 4. Lock / contention

Portal holds a per-bundle soft lock (`shared/autodoc_lock.py`) for the duration
of a generation run. The lock is:
- **Acquired** before writing the context file
- **Released** on Job success, Job terminal failure, or Portal error after acquire

If a second request arrives while a lock is held:
- Without `override_reason`: Portal returns `409 {code: "locked", holder: {...}}`
- With `override_reason` (≥ 10 chars): Portal calls `lock.override(...)` and proceeds

autodoc itself does not check or manage the lock — that is Portal's
responsibility.

---

## 5. Failure modes

| Failure | Behaviour |
|---------|-----------|
| `BundleContextError` at Job start (malformed ctx file) | Job exits non-zero. Portal detects `failed` on next status poll. Lock released. History row marked `failed` with error text. |
| LLM API error mid-generation | Job exits non-zero. Same as above. |
| Job killed / OOM | Context file becomes an orphan; cleaned up by Portal cron within 60 min. Lock TTL expires; released by cron. |
| `--output-file` path missing after success | Portal cannot read the `.docx`; marks history row `failed`. The dataset-store copy still exists under `/mnt/data/{project}/autodoc_docs/`. |
| Portal restarts mid-poll | On restart, existing `running` history rows are re-polled on next `/api/docs/status` call. No state is lost. |

---

## 6. Divergence risks

Things that have broken this integration in the past or are likely to break it:

- **Extra keys in ctx JSON** — `load_context` currently only validates the four
  required keys and ignores extras. Safe to add extras on the Portal side, but
  do not remove any of the four.
- **Wrong policy_def shape** — if Portal pre-processes `policy_def` before
  writing it (e.g. flattening stages into a list), `spec_from_policy.py` will
  fall back to the canonical template silently. Always write the raw API
  response verbatim.
- **Compute environment out of sync** — if the autodoc wheel in the compute
  environment is an older version than the Portal expects, CLI flags may not
  exist. Keep `AUTODOC_VERSION` in the environment image in sync with the
  autodoc release tag.
- **`--derive-spec` vs `--canonical-spec`** — these are not interchangeable.
  `--derive-spec` walks `policy_def` to produce the section list.
  `--canonical-spec` ignores `policy_def` and uses the fixed template. The UI
  modal controls which one Portal submits; make sure the UI selection maps to
  the right flag.
