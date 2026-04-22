# Auto-Documentation Integration with MRM-Portal — Product Requirements Document

**Author:** Subir Mansukhani
**Status:** Draft v2 (2026-04-21) — **superseded in key architecture sections by `IMPLEMENTATION.md` (2026-04-22)**
**Last Updated:** April 21, 2026 (architecture amendments folded in on April 22, 2026)

---

> ## ⚠️ STATUS NOTICE — Read `IMPLEMENTATION.md` first
>
> This PRD captures the original product intent and tiered feature set. It remains the reference for **what we're shipping and why**. Three follow-up reviews (office-hours, eng-review, design-review on 2026-04-22) revised several architectural sections. When this PRD disagrees with `IMPLEMENTATION.md` or the design doc at `~/.gstack/projects/ddl-subir-m-auto_documentation/*-design-*.md`, **those newer documents win.**
>
> Key amendments since Draft v2:
>
> 1. **§9 Data Flow** — replaced with "Shape 1": Portal reads bundle+policy from the PlatformGraph (not direct governance API), writes a JSON context file to `/mnt/data/{project}/autodoc_inputs/`, Job reads the file at start. No HMAC, no internal Portal endpoints.
> 2. **§9.1 Caching** — generation cache CUT from MVP. Every click runs a Job (gated by the per-bundle soft lock). Cache becomes a TODOS.md candidate for post-MVP soak data.
> 3. **§9.1 / §9.2 Content hashing** — REMOVED entirely. No `content_hash_at_submit`, no drift detection, no schema version. Context-file contents ARE the ground truth for the Job.
> 4. **§7 F3 Policy → doc-spec deriver** — deferred post-MVP. MVP ships canonical-only. Derivation added when a concrete customer policy is available to calibrate against.
> 5. **§7 F4.1 Spec preview & edit** — split out of M2 into post-MVP behind `AUTODOC_INLINE_SPEC_EDIT=true`.
> 6. **§7 F8 Attachment idempotency** — governance API has no label-based replace (verified 2026-04-22 via swagger). Implementation: create-new-first, then delete-old-by-ID.
> 7. **Provenance** — trimmed from 14 fields to 5 MVP fields: `bundle_id, policy_version_id, commit_sha, generated_by_user, generated_at`. Full phase-2 field set documented in the design doc.
> 8. **Auth** — Job uses Domino's ephemeral `/access-token` endpoint (re-acquired per call). No JWT-lifetime concerns. No HMAC plumbing.
> 9. **Milestones (§11)** — the weekly timeline is no longer the right framing since implementation is CC-accelerated. See `IMPLEMENTATION.md` for the sequenced unit roadmap (U2, U3, U5, U6…) without temporal weeks.
>
> Sections below preserved in their Draft v2 form for traceability. Read with the amendments above in mind.

---

## 1. Summary

This PRD proposes integrating the Auto Model Documentation project (`auto_model_docs`) with the Domino MRM-Portal to deliver a unified Model Risk Management experience. The integration transforms `auto_model_docs` from a standalone documentation generator into the **Scene 7 (Auto-Documentation)** layer of the MRM Solution Set, closing the loop between governance policy, model evidence, and generated regulatory documentation.

**Headline changes:**

- Auto-documentation is triggered from the Portal's model detail page
- Generated docs are informed by the model's governance bundle (owner, risk tier, evidence answers) rather than inferred by the LLM
- Document specs can be derived automatically from governance policy YAMLs
- Generated documents are attached back to the bundle as evidence
- Gaps detected during generation become governance findings
- The `auto_model_docs` Studio UI is folded into Portal, eliminating one always-on Domino App

---

## 2. Problem Statement

`auto_model_docs` today runs as a standalone Domino App. It generates Word and Jupyter documentation by scanning code and MLflow, then using an LLM to write narrative sections. **It knows nothing about the model's governance context.**

As a result:

- Users re-enter metadata (owner, risk tier, business purpose) that already lives in Domino governance bundles
- Generated docs are generic and LLM-flavored because the model has no factual context to cite
- Docs are disconnected from the governance bundle they should fulfill — a generated MDD doesn't satisfy the bundle's Documentation policy automatically
- Inconsistencies between code and evidence go undetected
- Two always-on Domino Apps (Portal + Studio) must be operated for what is conceptually one product

MRM-Portal has the missing half: an in-memory graph of every model's bundle, policies, evidence, findings, and approvals. It does not generate documents. `auto_model_docs` generates documents but cannot see any of this context.

---

## 3. Goals & Non-Goals

### Goals

1. **G1.** A validator clicking "Generate Documentation" on a model in the MRM Portal produces a compliant, bank-specific document without further configuration.
2. **G2.** Generated documents cite actual bundle metadata (owner, risk tier, intended use) as first-class facts, not inferred boilerplate.
3. **G3.** Documentation requirements encoded in governance policy YAMLs drive the structure of generated docs, so updates to policy are reflected automatically.
4. **G4.** Generated docs land back on the bundle as evidence attachments, fulfilling the corresponding policy stage.
5. **G5.** Gaps detected during generation (missing sensitivity analysis, mismatched declared vs. actual model type) become actionable governance findings.
6. **G6.** Operational footprint decreases from two always-on Domino Apps to one plus on-demand Domino Jobs for generation.

### Non-Goals

- Replacing the MRM-Portal's PlatformGraph with a different data store
- Building an AI copilot or conversational interface
- Supporting non-Domino deployments (local/CLI flow remains for developer use only)
- Generating fundamentally new report types (Executive, Quarterly) — those remain Portal's responsibility
- Changing the underlying Domino governance API surface

---

## 4. Target Users

### Primary

- **Model validators** at banks using Domino for MRM. They open Portal, review models, want to produce validation reports and MDDs without configuration gymnastics.
- **Model developers** submitting new models. They need starter MDDs scaffolded from intake evidence.

### Secondary

- **Compliance teams** who maintain governance policies. Their policy edits should propagate into generated docs automatically.
- **MRM program owners** configuring the deployment.
- **Internal Domino SAs** demoing the MRM Solution Set.

### 4.1 Authorization

| Action | Validator | Developer | Compliance | Admin |
|---|:---:|:---:|:---:|:---:|
| Trigger generation | ✓ | ✓ | — | ✓ |
| Edit spec in preview modal | ✓ | ✓ | — | ✓ |
| Auto-attach to bundle | ✓ | — | — | ✓ |
| Auto-create findings | — | — | ✓ (toggle) | ✓ |
| Dismiss consistency-check findings | ✓ | ✓ | — | ✓ |
| Override a running generation | — | — | — | ✓ |
| Edit canonical templates in `autodoc/templates/` | — | — | ✓ | ✓ |

Roles are resolved from Domino's existing RBAC via the user's JWT (already forwarded per-request via `auth_context.py`). No new permission primitives required.

---

## 5. Current State

### auto_model_docs (this repo)

- Standalone FastHTML web app (`web_app_studio.py`) on port 8888
- Scope is per-target-project via `?projectId=` query param
- Submits Domino Jobs to run the generation pipeline (scan → plan → generate → build)
- Outputs `.docx` and optional `.ipynb` to `/mnt/data/{project}/`
- Job history in SQLite at `/mnt/data/{project}/autodoc_jobs.db`
- Supports Anthropic and OpenAI providers, prompt/response caching

### MRM-Portal

- Flask app with server-rendered Jinja + vanilla JS
- Maintains an in-memory `PlatformGraph` refreshed every ~15 min from Domino APIs
- Pages: inventory, model detail, findings, monitoring, compliance, reports, graph, intake
- All relationships are statically coded (API foreign keys → typed edges)
- Evidence search via hardcoded keyword alias table (`EVIDENCE_ALIASES`)
- Governance policy YAMLs in `example_yamls/` cover Lifecycle, Cross-Cutting, Regulatory

### Gap

No integration point between the two. Users must open Studio separately, manually configure spec and project, and download docs to their desktop to attach back to Domino.

---

## 6. Proposed Solution Overview

Three architectural moves:

### A. Fold Studio's UI into Portal as a Flask blueprint

- Reduces to one always-on app
- `auto_model_docs/autodoc/` remains a standalone library
- Generation runs as a Domino Job (unchanged)
- `web_app_studio.py` stays as developer-only local dev tool

### B. Introduce bundle context as a first-class input to the orchestrator

- New input alongside existing code + MLflow inputs
- Flows from Portal's graph → JSON blob → autodoc Job
- Used by `SectionPlanner` to inform section hints

### C. Add a doc-spec deriver that converts governance policy YAMLs to doc specs

- Default "spec source" for bundles with mature policies
- Canonical template fallback
- Custom upload as escape hatch

---

## 7. Feature Scope

Organized by tier and ordered by value-per-effort.

### Tier 1 — MVP (Weeks 1–3)

| ID | Feature | Owner | Depends on |
|---|---|---|---|
| **F1** | Bundle-context injection into orchestrator | autodoc | — |
| **F2** | Canonical doc-spec templates (MDD, VR, MR) | autodoc | — |
| **F3** | Policy → doc-spec deriver | autodoc | F2 |
| **F4** | Portal launch surface ("Generate Documentation" button + modal) | Portal | F5 |
| **F4.1** | Spec preview & edit step in modal | Portal | F3, F4 |
| **F5** | Portal → Domino Job launcher | Portal | F1 |

#### F1. Bundle-context injection

- `autodoc` accepts `--bundle-context <json>` CLI flag
- Orchestrator threads it through SCAN phase as an additional context source
- `SectionPlanner` slices context per section (e.g., "Data Overview" gets data-stage evidence only)
- `ContentGenerator` receives relevant slice as factual grounding
- **Generation cache key is computed at SCAN entry; hit short-circuits the pipeline** (see §9.1)
- **Per-generation token budget cap** (default 200K tokens combined input+output); exceeding budget aborts the job and emits a finding
- **Blast radius:** SCAN, PLAN, GENERATE phases

#### F2. Canonical doc-spec templates

Three templates in `auto_model_docs/autodoc/templates/`:

- `mdd_spec.yaml` — Model Development Document
- `vr_spec.yaml` — Validation Report
- `mr_spec.yaml` — Monitoring Report

Drawn from SR 11-7 / EU AI Act common requirements. **Blast radius:** new files only.

**Template governance:**

- Templates checked into git; compliance review required before merge
- Each template has a `template_version` field incremented on change
- Generated docs record which template version produced them (see §9.2)
- Changes to canonical templates do **not** re-trigger regeneration of existing docs — they only affect future generations (predictable behavior for validators)

#### F3. Policy → doc-spec deriver

- New module `autodoc/spec_from_policy.py`
- Converts a Domino governance policy (fetched via `client.get_policy_definition`) to a `doc_spec`
- Stages become sections; evidence questions become hints
- Falls back to canonical template if policy is sparse
- **Derived-spec dedup:** caches output keyed on `(policy_id, policy_version)` so repeat calls against an unchanged policy reuse the result
- **Dependency:** Domino Governance API already wrapped in Portal's `domino_client.py`

#### F4. Portal launch surface

- "Generate Documentation" button on `model_detail.html`
- Modal pickers: document type (MDD / VR / MR / Custom), spec source (Derive / Template / Upload), hardware tier, branch
- Submits to new Portal route `POST /api/docs/generate`
- **Owner:** Portal (new `routes/autodoc.py` blueprint)

#### F4.1. Spec preview & edit

- After the user selects document type and spec source, the modal shows the resolved `doc_spec` (sections + hints) in a collapsible preview **before submit**
- User can edit sections inline (add/remove/reorder), override hints, then submit
- Edited specs are saved per-user as "my custom MDD spec" for reuse
- Submitting without edits uses the derived/template spec unchanged
- **Why:** catches low-quality policy-derived specs (R1) before LLM cost is incurred

#### F5. Portal → Domino Job launcher

- Portal resolves spec (derive | template | upload)
- Portal extracts bundle context from graph → JSON
- Portal POSTs to Domino Jobs API with: `python -m autodoc.main --spec <path> --bundle-context <path>`
- Job ID returned to UI; status polled via Domino Jobs API
- **Per-bundle soft lock:** before POSTing, Portal checks for a running job on `(bundle_id, template)`. If one exists, the modal shows *"A generation is already running (started by Priya 2 minutes ago). [Wait] [Override]"*. Override cancels the running job before submitting, and is audit-logged
- **Hard job timeout:** 15 minutes wall clock. Beyond that, Portal marks job `timeout`, surfaces to UI, writes audit event
- **Failure & retry behavior:**
  - SCAN and PLAN phases auto-retry up to 2x on transient failures (network, 5xx from Domino or LLM)
  - GENERATE is not retried automatically (partial LLM output is preserved in cache; user can resume)
  - BUILD is atomic — fails cleanly, no partial `.docx` written
  - On terminal failure: job status = `failed`, failure reason logged, a toast notifies the user, **no finding is created** (failures are operational, not governance issues)
  - User can re-trigger from the modal; prior SCAN/PLAN cache hits skip completed work
- **Dependency:** `autodoc` package installed in the Job environment

### Tier 2 — Integration that closes the loop (Weeks 3–5)

| ID | Feature | Owner |
|---|---|---|
| **F6** | Fold Studio UI into Portal as Flask blueprints | Portal + autodoc |
| **F7** | Documentation history tab on model detail page | Portal |
| **F7.1** | Doc feedback signal (accepted / needed edits / rejected) | Portal |
| **F8** | Generated doc → bundle attachment (evidence) | autodoc |

#### F6. Fold Studio UI into Portal

- Port `studio/routes_api.py`, `routes_job.py`, `routes_spec.py` to Flask blueprints
- Retire Studio as a deployed Domino App
- Keep `web_app_studio.py` as developer-mode local tool

#### F7. Documentation history tab

- New tab on model detail page reads `/mnt/data/{project}/autodoc_jobs.db`
- Displays: timestamp, template, status, download links, job log, feedback state (F7.1)

#### F7.1. Doc feedback signal

- On Documentation tab, each doc row has a 4-state pill: `Not reviewed` / `Accepted as-is` / `Needed edits` / `Rejected`
- Emits `autodoc.doc_feedback` telemetry event — feeds the "zero-edit approval" metric in §12
- Low-friction: one click, no modal

#### F8. Generated doc → bundle attachment

- After BUILD phase, autodoc POSTs `.docx` as bundle attachment with label matching the template (e.g., `"Model Development Document — Auto-Generated (Draft)"`)
- Attachment visible in Portal evidence panel automatically
- Latest generation replaces the prior auto-generated attachment on the bundle; manually uploaded attachments are never overwritten (see §9.2)
- Toggle in the generation modal (default: on)

### Tier 3 — Enforcement + consistency (Weeks 5–7)

| ID | Feature | Owner |
|---|---|---|
| **F9** | Gap detection → governance findings | autodoc |
| **F10** | Code-vs-evidence consistency check | autodoc |
| **F11** | Shared `EVIDENCE_ALIASES` vocabulary | Portal (source); autodoc imports |

#### F9. Gap detection → findings

- When LLM cannot find source material for a required section, `ContentGenerator` emits a `missing_material` signal
- Orchestrator creates a `GovernanceFinding` (severity S2) via Domino API
- Portal's findings dashboard displays without any new UI

#### F10. Code-vs-evidence consistency check

- New module `autodoc/consistency_checker.py`
- Takes `scan_result` and `bundle_context`, diffs declared vs. detected fields (model type, framework, feature count)
- Mismatches become S2 findings with diff details
- Corresponds to **Scene 4 Doc-vs-Code suite** in MRM Solution Set

#### F11. Shared evidence-alias vocabulary

- Extract `EVIDENCE_ALIASES` to shared module both Portal and autodoc import
- Autodoc uses canonical labels in generated docs so Portal's evidence search finds them

### Tier 4 — Advanced orchestration (Weeks 7+)

| ID | Feature | Owner |
|---|---|---|
| **F12** | Intake-triggered starter docs | Portal + autodoc |
| **F13** | Per-section evidence slicing | autodoc |
| **F14** | Notebook regeneration with bundle-context override | autodoc |

#### F12. Intake-triggered starter docs

- On successful bundle creation in Portal's `routes/intake.py`, fire autodoc in "starter" mode
- Generates scaffolded MDD prefilled from intake evidence, no full LLM narrative
- Developer arrives to a half-filled document

#### F13. Per-section evidence slicing

- `SectionPlanner` picks relevant evidence subset per section (Data → data stages; Performance → test stages)
- Keeps token usage sane on mature governance bundles (60+ Q&A pairs)

#### F14. Notebook regeneration with bundle-context override

- `notebook-from-cache` mode already exists
- Add `--bundle-context` flag to refresh narrative with current bundle state without re-calling LLM

---

## 8. Architecture Decision

**Decision:** Portal absorbs the Studio UI. Autodoc's pipeline continues to run as a Domino Job.

### Rationale

- **One always-on app vs two:** 50% reduction in deployment surface and idle compute
- Portal already has the user's context (model, bundle, owner, evidence) — most Studio form fields become redundant
- Autodoc's core pipeline (`autodoc/`) is already a clean Python package; the UI layer was always the duplicated piece
- Heavy generation on Domino Jobs preserves pay-per-run economics

### Alternatives Considered

| Alternative | Verdict | Why |
|---|---|---|
| Keep both apps always-on | Rejected | Doubles ops surface and idle cost for no UX benefit |
| Merge autodoc pipeline into Portal process | Rejected | Long-running LLM generation should not share a process with a real-time dashboard |
| Scale-to-zero Studio | Rejected | Domino Apps do not cleanly support this; cold starts are bad UX |

---

## 9. Data Flow (Tier 1 End State)

1. Validator visits `/model/credit_risk_v3` in Portal
2. Clicks "Generate Documentation" → modal opens with MDD preselected and "Derive from policy" preselected
3. Modal shows derived spec in preview (F4.1); user accepts or edits
4. Submits
5. Portal route `/api/docs/generate`:
   1. Resolves `doc_spec` (derive | template | upload)
   2. Queries `PlatformGraph` for bundle context → flat JSON
   3. Computes **generation cache key** (§9.1); if hit, returns prior `.docx` immediately
   4. Checks per-bundle lock (§9 F5); blocks or prompts override if another job is running
   5. Writes spec + bundle-context JSON to `/mnt/data/{project}/autodoc_inputs/`
   6. POSTs to `/v4/jobs/start` with command `python -m autodoc.main --spec <p> --bundle-context <p>`
   7. Returns `job_id` to UI; polling starts
6. Job runs autodoc pipeline (unchanged structure; three inputs now):
   - **(a) doc spec** — structure
   - **(b) bundle context** — facts
   - **(c) code + MLflow** — technical detail
7. Job writes `credit_risk_v3_MDD_v{N}_{yyyymmdd}.docx` to `/mnt/data/{project}/autodoc_output/`
8. Optionally attaches `.docx` to bundle as evidence
9. Optionally emits findings for missing/inconsistent sections
10. Portal's model detail "Documentation" tab shows the new doc; evidence panel shows the attachment; findings dashboard shows any emitted findings

### 9.1 Idempotency & Caching

To avoid regenerating unchanged documents and control LLM spend, generation is gated by a two-level cache.

**Generation cache key:**

```
sha256(doc_spec_yaml + bundle_context_json + code_commit_sha + policy_version)
```

**On "Generate" click:**

1. Portal computes the cache key
2. **Cache hit** → return the prior `.docx` immediately, show *"Last generated on 2026-04-18. Force regenerate?"*
3. **Cache miss** → submit a new Domino Job

**Derived-spec dedup:** the policy → spec deriver (F3) caches its output keyed on `(policy_id, policy_version)`. A bundle that reuses the same policy version does not re-derive.

**LLM-response cache:** autodoc's existing `.autodoc_cache.json` (keyed by prompt hash) remains — it absorbs redundant LLM calls within a generation.

**Invalidation triggers** (any one bumps the key, forcing regeneration):

- New evidence artifact added, updated, or deleted on the bundle
- Policy edited (new `policy_version`)
- New code commit on the target branch
- User toggles "Force regenerate" in the modal

**Cache location:** `/mnt/data/{project}/autodoc_cache/generations.db` (SQLite, per-project).

### 9.2 Versioning & Retention

**Filename convention:** `{model_name}_{template}_v{N}_{yyyymmdd}.docx` (e.g., `credit_risk_v3_MDD_v3_20260421.docx`). `N` auto-increments per `(model, template)` pair.

**Bundle attachment policy:** latest generation replaces the prior auto-generated attachment on the bundle (attachment label: `"{template} — Auto-Generated (Draft)"`). Manually uploaded attachments are never overwritten.

**Retention:** all generations are kept on `/mnt/data/{project}/autodoc_output/` until the project owner explicitly prunes. The Documentation tab (F7) surfaces all versions with download links.

**Template-version stamping:** every generated doc records `autodoc_template_version` (e.g. `mdd_spec.yaml v1.2`) and `autodoc_pipeline_version` in its metadata properties, so any generated doc is reproducible.

### 9.3 Context Resolution Fallbacks

Not every model has mature governance. The resolver handles four degraded cases:

| Scenario | Behavior |
|---|---|
| Model has no governance bundle | Modal prompts: "No bundle found. Create one via intake, or generate with a canonical template only?" |
| Bundle exists but no policy attached | Fall back to canonical template (F2). Warn: "Generated without policy context — review before attaching to bundle." |
| Policy attached but evidence fields empty | Proceed; emit F9 findings for each missing required section; generated doc includes placeholder text ("[Evidence required: model owner]"). |
| Bundle exists but viewer lacks read permission | Block with clear error, link to bundle owner. |

---

## 10. Dependencies

### External

- Domino Governance API (policies, bundles, results, findings, attachments) — already wrapped in Portal's `domino_client.py`
- Domino Jobs API (`POST /v4/jobs/start`, `GET /v4/jobs/{id}`) — already used in both repos
- Domino Model Registry API (unchanged)

### Internal

- `autodoc` package pip-installed in the Domino Job environment (or vendored)
- Shared filesystem (`/mnt/data/{project}/`) accessible from both Portal and the Job
- Shared `EVIDENCE_ALIASES` (once F11 lands)

**Nothing new on the Domino side.** All existing surface.

### 10.1 Observability & Audit Trail

**Telemetry event** emitted per generation to the Portal's standard event stream:

```json
{
  "event": "autodoc.generation",
  "user_id": "...",
  "project_id": "...",
  "bundle_id": "...",
  "model_name": "...",
  "template": "MDD",
  "spec_source": "derive|template|upload",
  "cache_hit": true,
  "duration_ms": 182000,
  "llm_tokens_input": 45123,
  "llm_tokens_output": 12890,
  "status": "success|failure|timeout",
  "findings_emitted": 2
}
```

**Success metrics (§12) are derived from this stream.** Primary dashboard: generation time p50, cache hit rate, zero-edit approval rate (joined with feedback-loop events from F7.1).

**Audit trail:** every generation + every bundle attachment write posts a Domino audit event (`type=autodoc.generation`, `type=autodoc.attachment`). Required for SR 11-7 compliance — auditors must be able to see who generated what, when.

**Job logs:** written to `/mnt/data/{project}/autodoc_logs/{job_id}.log`. Retained 90 days.

---

## 11. Milestones

| Milestone | Target | Features | Success criteria |
|---|---|---|---|
| **M1** — Bundle-context MVP | End of Week 2 | F1, F2, F3 | Generation runs with bundle context from CLI; demo doc has real owner/risk-tier citations |
| **M2** — Portal launch surface | End of Week 3 | F4, F4.1, F5 | "Generate Documentation" button in Portal triggers the end-to-end flow |
| **M3** — Studio retirement | End of Week 5 | F6, F7, F7.1, F8 | Studio no longer deployed as a Domino App; docs attach back to bundles automatically. **Rollback:** if M3 regressions are blocking, `web_app_studio.py` can be re-deployed as a Domino App within 30 minutes (image is retained post-retirement for 90 days). |
| **M4** — Enforcement features | End of Week 7 | F9, F10, F11 | Findings created automatically for gaps and inconsistencies |
| **M5** — Advanced orchestration | End of Quarter | F12, F13, F14 | Intake flow seeds starter docs; token usage stable on mature bundles |

---

## 12. Success Metrics

### Primary

| Metric | Target |
|---|---|
| Doc generation time (click → downloadable .docx) | < 4 minutes p50 |
| Fraction of generated docs with zero-edit approval path | > 40% within 90 days |
| Governance findings auto-created per week on active tenants | ≥ 5 / week |
| Cache hit rate on repeat "Generate" clicks | > 60% within 30 days |

### Secondary

- Studio app retirement date: target M3
- Reduction in always-on app count: 2 → 1
- User-reported satisfaction (survey to pilot bank): target NPS > 30 within 60 days

### Qualitative

- Pilot bank validator produces a complete MDD without leaving Portal in a single session
- Compliance team updates a policy YAML and sees the change reflected in next-generated doc structure with no code change

---

## 13. Risks & Mitigations

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| **R1** | Policy-to-spec conversion quality varies by customer | High | Medium | Canonical template fallback (F2). Start with customers whose policies follow `Lifecycle: N` naming. Spec preview/edit step in the modal (F4.1) before submitting. |
| **R2** | Bundle context too large for LLM context window on mature bundles | Medium | Medium | F13 per-section slicing. Hard caps on per-section token budget. Summarize long evidence answers before inclusion. |
| **R3** | Auto-attached docs create finding churn if validators reject them | Medium | Low | Attachment is toggle-off-able. Use clear labeling ("Draft – Auto-Generated") distinguishable from committed attachments. |
| **R4** | Studio UI port to Flask introduces regressions for power users | Medium | Medium | Retain `web_app_studio.py` as local-only dev tool. Port in phases, not big-bang. Soak period during M2 with both deployed. |
| **R5** | False positive findings from code-vs-evidence mismatches on legitimate refactors | High | Medium | Confidence threshold on consistency checker. Clear diff in finding body. "Dismiss with reason" UI in Portal (existing finding primitives support this). |
| **R6** | Domino Jobs API rate limits when many validators trigger generation simultaneously | Low | Medium | Per-user concurrency cap (already exists: `AUTODOC_MAX_JOBS=1`). Queue indication in UI. |
| **R7** | Two users trigger generation on the same bundle simultaneously, producing divergent outputs and inconsistent attachments | Low | Medium | Per-`(bundle_id, template)` soft lock at submission (see F5). Override is explicit and audit-logged. |
| **R8** | `bundle_context.json` contains PII / regulated content and is written to shared filesystem | Medium | High | Files land in `/mnt/data/{project}/autodoc_inputs/` with project-scoped filesystem perms (inherited from Domino). Deleted on job success. Encryption-at-rest is the customer's storage-layer responsibility. Document this in deployment guide. |

---

## 14. Open Questions

| ID | Question | Blocks |
|---|---|---|
| **Q1** | Where does the `autodoc` package get installed in the Job environment? Options: pip install from PyPI (not published), vendored copy per tenant, Domino environment image. Needs SA decision. | M1 |
| **Q2** | Who owns `EVIDENCE_ALIASES` when it is shared? Probably Portal, with autodoc importing via a small shared package. Needs confirmation before F11. | F11 |
| **Q3** | Do generated docs need digital signing or watermarking for audit trails? Not in scope today; may be required by some regulators. | Future |
| **Q4** | Bundle attachment API — which endpoint exactly? Needs confirmation against Domino's governance API docs; path may vary by version. | F8 |
| **Q5** | Should the Portal modal support scheduling (regenerate weekly) or is it only on-demand? Recommend on-demand only for v1. | F4 |
| **Q6** | Should the generation cache (§9.1) be per-user or per-project? Default is per-project, but validators may want isolation from drafts in flight. | M1 |
| **Q7** | When a canonical template version bumps, should existing docs be flagged as "generated with outdated template"? Not in scope today but worth deciding before M4. | M4 |

---

## 15. Appendix

### A. Out-of-scope features considered and deferred

- Real-time collaboration on generated docs
- AI copilot for doc editing
- Direct export to Confluence / SharePoint
- Cross-model documentation (portfolio-level narrative)
- Graphiti or other knowledge graph layer
- Scheduled (cron) regeneration — v1 is on-demand only

### B. Related documents

- MRM Solution Set Build Plan (`MRM-Portal/MRM_solution_set_complete_build_plan.md`)
- `auto_model_docs` README and DESIGN.md
- Domino Design System & Usability Principles

### C. Glossary

| Term | Meaning |
|---|---|
| **MDD** | Model Development Document — developer-facing record of model construction |
| **VR** | Validation Report — independent validator's assessment |
| **MR** | Monitoring Report — ongoing model performance and drift review |
| **Bundle** | Domino governance object attaching policies + evidence + findings to a project |
| **Evidence** | Question/answer pairs inside a policy stage |
| **PlatformGraph** | Portal's in-memory entity graph of Domino state |
| **Generation cache key** | `sha256(spec + bundle_context + commit_sha + policy_version)` — governs whether a new generation runs |
| **Template version** | Monotonic integer on each canonical spec in `autodoc/templates/`; stamped into generated docs |
