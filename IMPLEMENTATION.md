# Implementation Plan — MRM Portal × autodoc integration

> Last updated: 2026-05-05 (Phase C shipped in `domino-field/MRM-Portal@feature/autodoc-integration`; `--canonical-spec` CLI flag added; "Derive from policy" ungreyed in modal; next: deploy autodoc wheel to Domino compute env)
> Source of truth for **what to build and in what order**.
> For product rationale see `PRD_v2.md`. For architecture decisions see the design doc at `~/.gstack/projects/ddl-subir-m-auto_documentation/subirmansukhani-feature-mrm-portal-integration-design-20260422-111457.md`. This file is the implementer's roadmap.

## The solution in one paragraph

A validator opens a model page in the MRM Portal, clicks **Generate documentation**, and gets a compliant Word doc in minutes. The doc cites real bundle facts (owner, risk tier, intended use) from Portal's PlatformGraph instead of LLM-inferred boilerplate, stamps its provenance so auditors can trace it, and attaches back to the governance bundle as evidence. The standalone autodoc Studio app is retired; everything lives behind one Portal process plus on-demand Domino Jobs for generation.

## Four moving parts

1. **Portal submit route** (`POST /api/docs/generate`) — reads bundle + policy from the PlatformGraph, resolves a doc-spec, writes a context JSON snapshot to `/mnt/data/{project}/autodoc_inputs/`, submits a Domino Job.
2. **autodoc Domino Job** — reads the context file, runs SCAN → PLAN → GENERATE → BUILD, attaches the .docx back to the bundle, stamps provenance, deletes the context file.
3. **Portal UI** — Generate button + modal + Documentation history tab on the model detail page.
4. **Portal cleanup cron** — orphan file sweep in `autodoc_inputs/` every hour.

## Locked decisions

| Area | Decision |
|---|---|
| Context source | PlatformGraph read by Portal only. 15-min refresh is the freshness boundary, with a force-refresh on the generate path for the specific bundle. |
| Context transport | JSON snapshot at `/mnt/data/{project}/autodoc_inputs/ctx_{job_uuid}.json`. Portal writes at submit, Job reads at start, Job deletes on exit. |
| Auth for Job → governance | Domino's ephemeral `/access-token` endpoint (re-acquired per call). |
| Job → governance API | One call only: POST attachment at BUILD end. Zero reads. |
| Generation cache | OUT of MVP. Every click runs a Job (after per-bundle lock check). |
| Content hash | REMOVED. No hash, no drift detection. |
| Spec source | Canonical template (`--canonical-spec`) and derive from policy (`--derive-spec`) both available. Upload deferred post-MVP behind `AUTODOC_INLINE_SPEC_EDIT` flag. |
| F4.1 inline spec editor | Deferred post-MVP behind `AUTODOC_INLINE_SPEC_EDIT` flag. |
| Studio retirement | Full port, ships last. Expect >50% slip risk. Week-0 dual-deploy dry-run mitigation (see TODOS.md). |
| Modal vs drawer | Ant Modal (~560px, centered). |
| Close-on-submit | Modal closes on submit; progress surfaces on Documentation history row + toast. |
| History layout | Ant DominoTable (dense rows, sortable). |
| Responsive | Desktop-first. Tablet best-effort. Mobile banner ("desktop required"). |
| Provenance | 8 fields stamped: 5 MVP (`bundle_id`, `policy_version_id`, `commit_sha`, `generated_by_user`, `generated_at`) + 3 phase-2 (`generator_version`, `template_version`, `run_environment`). Phase-2 shipped in PR #15. |
| Attach-back | Create-then-delete-by-ID (per governance swagger). `prior_attachment_id` tracked in Portal SQLite. |

## Status summary

| Unit | Phase | Status | Landed in |
|---|---|---|---|
| U2 — Canonical doc-spec templates | A | ✅ Shipped | PR #6 (`5e54091`) |
| U3 — `bundle_context.py` loader | A | ✅ Shipped | PR #5 (`c1e9773`) |
| U4 — Policy-to-spec derivation | A | ⏭️ Deferred → U17 | — |
| U5 — `main.py` CLI flags | B | ✅ Shipped | PR #9 (`395e4c9`) |
| U6 — Orchestrator bundle-context plumbing | B | ✅ Shipped | PR #8 (`ef65979`) |
| U7 — Provenance stamping | B | ✅ Shipped | PR #7 (`426a668`) |
| U8 — LLM metadata-citation eval (gates M1) | B | ✅ Shipped | PR #10 (`a78e934`) + skipif fix PR #11 (`7a96bc4`) |
| U9 — Portal attachment wrapper | C | ✅ Shipped | `domino-field/MRM-Portal@feature/autodoc-integration` |
| U10 — Portal autodoc blueprint | C | ✅ Shipped | `domino-field/MRM-Portal@feature/autodoc-integration` |
| U10.5 — Cleanup cron | C | ✅ Shipped | `domino-field/MRM-Portal@feature/autodoc-integration` |
| U11 — Portal soft lock | C | ✅ Shipped | `domino-field/MRM-Portal@feature/autodoc-integration` |
| U12 — Model detail UI | C | ✅ Shipped | `domino-field/MRM-Portal@feature/autodoc-integration` |
| U13 — Playwright E2E | C | ✅ Shipped | `domino-field/MRM-Portal@feature/autodoc-integration` |
| U14 — `MRM-Portal/DESIGN.md` | D | ⏸️ Not started | — |
| U15 — Studio retirement | D | ⏸️ Not started (post-MVP soak) | — |
| U16 — F4.1 inline spec editor | D | ⏸️ Not started (flagged) | — |
| U17 — Policy-to-spec derivation | D | ✅ Shipped | PR #16 (`a97f5b2`) |
| U18 — Gap findings + consistency checker | D | ✅ Shipped (flagged off) | PR #17 (`fdf80fb`) |

### Non-unit follow-ons (shipped)

| Item | Status | Landed in |
|---|---|---|
| B3 — autodoc wheel packaging + env-image deployment guide | ✅ Shipped | PR #13 (`14b433f`) |
| Governance boundary contract tests (403/409/429/401) | ✅ Shipped | PR #14 (`61a1773`) |
| Phase-2 provenance fields (generator_version, template_version, run_environment) | ✅ Shipped | PR #15 (`de37554`) |
| `--canonical-spec` CLI flag + "Derive from policy" ungreyed in modal | ✅ Shipped | `a8a9789` (autodoc) + `95159d1` (Portal) |

**Infra shipped:**
- `.github/workflows/ci.yml` — pytest on PRs/pushes to `feature/mrm-portal-integration`.
- `.github/workflows/auto-merge.yml` — `workflow_run`-gated squash-merge on green CI (PR #12 / `fc240c7` fixed the earlier `gh pr merge --auto` silent-fallback bug).
- Both workflows now live on `master` (PR #18 / `03cdc49`). Required because GitHub's `workflow_run` trigger only fires when the listener workflow is on the default branch; during the U17/U18 wave the 5 PRs had green CI but Auto-merge never fired (squash-merged by hand). Fixed going forward.
- Target branch stays `feature/mrm-portal-integration`; master stays human-review-only.

**Next step:** Deploy the autodoc wheel (`auto_model_docs/dist/auto_model_docs-0.1.0-py3-none-any.whl`) to the Domino compute environment image per `docs/deployment/autodoc-env-image.md`, then run an end-to-end smoke test in Domino.

## The sequence

> End-to-end runtime flow (Portal → Job → back): [`diagrams/autodoc-e2e-sequence.mmd`](diagrams/autodoc-e2e-sequence.mmd). Legacy spec-only flowchart (pre-integration): [`diagrams/explanations/auto-documentation-workflow.mmd`](diagrams/explanations/auto-documentation-workflow.mmd).

Each unit is one Claude Code session / one PR. Phase A units are independent of each other. Phase B depends on A. Phase C depends on A. Phase D is optional or post-MVP.

### Phase A — Foundation (any order)

**U2. Canonical doc-spec templates.** Create `auto_model_docs/autodoc/templates/{mdd,vr,mr}_spec.yaml`. Each file has a `template_version` frontmatter field. Seeded from SR 11-7 / EU AI Act common requirements. Files only; no code changes.

**U3. `autodoc/bundle_context.py`.** New module with `load_context(path) -> dict`: reads `ctx_{job_uuid}.json` from shared FS, validates required keys (`bundle_id`, `policy_version_id`, `bundle`, `policy_def`), returns a dict. Raises clean errors on malformed input. ~60 LOC.

**U4. Skipped for MVP.** (`autodoc/spec_from_policy.py` moved to Phase D as U17 — canonical-only at MVP.)

### Phase B — autodoc integration (depends on Phase A)

**U5. `autodoc/main.py` CLI flags.** Add `--bundle-id X --policy-version-id Y --context-file ctx_{job_uuid}.json`. Existing `--spec` flow keeps working (regression test required). When `--context-file` present, load via U3 and hand to orchestrator.

**U6. Orchestrator bundle-context plumbing.** Modify `autodoc/orchestrator.py`. SCAN phase receives bundle context as additional input alongside code + MLflow. SectionPlanner passes relevant slice per section; ContentGenerator receives it as factual grounding. The existing no-context flow MUST still work (regression).

**U7. Provenance stamping.** Modify the BUILD phase (likely `autodoc/generation/builder.py`). Create `autodoc/provenance.py` module. Stamp 5 MVP fields as Word custom properties on the .docx. Write the same fields plus `provenance_id` (short UUID) as a row to `autodoc_provenance.db` SQLite (co-located with `autodoc_jobs.db`).

**U8. LLM metadata-citation eval.** New `tests/evals/test_metadata_citation.py`. Fixture: a known bundle with owner = "Alice Chen", risk tier = "High", intended use = "Credit risk scoring for consumer loans, US market." Run the pipeline with this context. Assert the output .docx contains `"Alice Chen"` (exact), `"High"` (exact), and `"Credit risk scoring"` (substring) in the right sections. **This gates M1.**

### Phase C — Portal surface (depends on Phase A)

**U9. Portal `post_bundle_attachment` wrapper.** In `MRM-Portal/shared/domino_client.py`, add `post_bundle_attachment(bundle_id, file_bytes, label) -> attachment_id` (POST) and `delete_bundle_attachment(bundle_id, attachment_id)` (DELETE). Per B2 swagger, idempotent replace = create-new-first, then delete-old-by-id. Unit tests with mocked governance.

**U10. Portal autodoc blueprint.** New `MRM-Portal/routes/autodoc.py`. Three public routes:
- `POST /api/docs/generate` — read bundle+policy from graph (force-refresh this specific bundle first for freshness), resolve spec from canonical template, acquire soft lock (U11), write `ctx_{job_uuid}.json` to `/mnt/data/{project}/autodoc_inputs/`, submit Domino Job via existing Portal `domino_client`, return `{job_id, history_id}`.
- `GET /api/docs/status/<job_id>` — poll Domino Jobs API, return status + progress.
- `GET /api/docs/history/<project_id>` — return prior generations from Portal SQLite.

**U10.5. Cleanup cron.** Portal background task scanning `autodoc_inputs/` every hour; deletes files older than 60 minutes. Flask-APScheduler or a simple threading loop. ~30 LOC.

**U11. Portal soft lock.** New `MRM-Portal/shared/autodoc_lock.py`. SQLite file at Portal's local writable dir (NOT `/mnt/data`). Unique constraint on `(bundle_id, template)`. Methods: `acquire(bundle_id, template, run_id, user)`, `release(run_id)`, `override(bundle_id, template, new_run_id, override_reason, user)`, `cleanup_stale(max_age_minutes=20)` (covers 15-min Job hard timeout + buffer).

**U12. Model detail UI.** Modify `MRM-Portal/templates/model_detail.html` and `MRM-Portal/static/js/model_detail.js`. Ant Design 5.x via CDN per `how-to-build-domino-apps.md` rule. Add:
- Generate button (sentence case: "Generate documentation") in page header
- Ant Modal (~560px, centered) on click: doc type radio, "Advanced options" collapsible (HW tier + branch + spec source), primary "Generate" + secondary "Cancel"
- Documentation history tab (Tabs + DominoTable) with columns: Date / Template / Status / User / Attached / Actions
- Status polling every 10s via `/api/docs/status/<job_id>`; row updates inline; toast on completion
- Empty states (no docs yet; no bundle)
- Lock contention UI: Ant Modal + Alert + "Wait" / "Override with reason" (TextArea, required)
- Attach-back indicator in evidence panel: "Auto-generated" Ant Tag
- Ctrl+Enter submits modal; Escape closes; focus trap; focus return on close
- ARIA: `role="dialog" aria-modal="true"` on modal; `role="tabpanel"` on history tab; `aria-live="polite"` on status pill; `role="status"` on toast
- Touch targets ≥44px

**U13. Playwright E2E.** Add Playwright to Portal's dev deps. E2E tests:
- Happy path: click Generate → wait → row appears in history with Success status → attachment visible on evidence panel
- Locked bundle: two users trigger same (bundle, template); second sees lock UI; override with reason succeeds
- Failure + retry: kill Job mid-run; verify row shows Failed with retry action; retry works
- a11y: axe-core check on the modal + history tab, zero violations

### Phase D — Polish / post-MVP

**U14. `MRM-Portal/DESIGN.md`.** Consolidate Ant Design component mapping + Domino theme tokens + a11y requirements from the design review. Seed content is already in the design doc's Design Review Addendum. ~1 hr.

**U15. Studio retirement.** Port Studio's Flask-friendly surfaces to Portal blueprints (`/api/datasets`, `/api/upload-spec-to-dataset`, `/run`, `/job-history`, `/stop-job-history`). Week-0 dual-deploy dry-run per TODOS.md ("Week-4 dry-run dual-deploy" item). ~4,049 LOC of FastHTML → Flask/Jinja/vanilla-JS. Expect slip.

**U16. F4.1 inline spec editor (M2.5).** Behind `AUTODOC_INLINE_SPEC_EDIT=true`. Add section CRUD + hint override inside the Generate modal. "Save per-user" persistence for reuse.

**U17. Policy-to-spec derivation.** Originally F3 in PRD. Deferred because canonical templates cover most SR 11-7 / EU AI Act cases, and derivation quality varies per customer policy. Add when a concrete customer need surfaces.

**U18. Gap findings + consistency checker.** Tier-3 in PRD. Behind `AUTODOC_AUTO_FINDINGS=true`. `autodoc/consistency_checker.py` diffs declared vs. detected fields; emit S2 findings for mismatches. Gap detection emits findings for missing required sections. Watch for false-positive churn.

## Recommended session grouping for Claude Code

**Session 1 (autodoc repo):** U2 + U3 in one conversation. Both small, independent, no Portal dep. Land as one PR.

**Session 2 (autodoc repo):** U5 → U6 → U7 → U8 sequentially. Each depends on the prior. One conversation with clean commit boundaries.

**Session 3 (MRM-Portal repo):** U9 + U11 first (both independent), then U10 + U10.5 (depend on U9 + U11), then U12 (depends on U10 + U11), then U13 (depends on U12). One long conversation in the Portal repo.

**Soak checkpoint.** After Session 3, the Generate button works end-to-end. Deploy internally; collect feedback before touching U15.

**Session 4 (MRM-Portal repo):** U15 Studio retirement. Start-of-port dual-deploy window for drift comparison. Ship when drift < 5%.

**Optional later:** U14 (DESIGN.md), U16 (F4.1), U17 (policy derivation), U18 (gap findings) in any order as needed.

## Blockers tracker — all closed

| Blocker | Status |
|---|---|
| ~~B1 bundle-revision endpoint check~~ | **CLOSED_NEGATIVE 2026-04-22** — swagger_1.json confirmed no revision field. Moot: content hash removed, so B1 no longer matters. |
| ~~B2 attachment endpoint idempotency~~ | **CLOSED_PARTIAL 2026-04-22** — POST+DELETE only, no label-based replace. Create-then-delete-by-ID pattern adopted in U9. |
| ~~B3 autodoc packaging~~ | **CLOSED 2026-04-22** — wheel packaging + env-image deployment guide shipped in PR #13 (`14b433f`). `pyproject.toml` scopes packages to `autodoc*` and ships `main.py` as a top-level py-module so the `autodoc` console script works from a wheel install. `docs/deployment/autodoc-env-image.md` documents the one-time tenant setup (Dockerfile snippet, pin, rebuild, smoke test, release process). Internal PyPI publishing remains a post-MVP optimization when release cadence justifies it. autodoc-context half of B3 was moot (package archived when content hash removed). |

**No open blockers.** All three M1 prerequisites resolved.

## Test expectations

Pull the full spec from `~/.gstack/projects/ddl-subir-m-auto_documentation/subirmansukhani-feature-mrm-portal-integration-eng-review-test-plan-*.md`. Highlights:

- U6 includes a **regression test** proving the existing local-dev flow (`python -m autodoc.main --spec doc_spec.yaml`, no context file) still works. Mandatory.
- U8 LLM metadata-citation eval **gates M1** — not advisory. Generated doc MUST cite the fixture bundle's owner/risk_tier/intended_use exactly.
- U13 E2E runs axe-core inside Playwright for every test; zero a11y violations.
- Boundary contract tests for Job ↔ governance: 403 on attach-back (permission revoked mid-Job), 409 (duplicate attach, B2 idempotency), rate-limit backoff, clock skew on ephemeral tokens. **Shipped in PR #14.**

## Environment flags

| Flag | Default | Effect |
|---|---|---|
| `AUTODOC_INLINE_SPEC_EDIT` | `false` | Enables F4.1 spec editor in the modal (U16). |
| `AUTODOC_AUTO_FINDINGS` | `false` | Enables gap detection + consistency checker (U18). |
| `AUTODOC_MAX_JOBS` | `1` | Per-user concurrent Job cap (existing). |
| `AUTODOC_HMAC_SECRET` | — | **NOT USED.** Shape 1 (file-based context) has no HMAC. Flag listed to prevent accidental reintroduction. |
| `AUTODOC_CACHE_RETENTION_DAYS` | — | **NOT USED.** Cache cut from MVP. |
| `DOMINO_API_HOST` | required | Existing. |
| `DOMINO_STARTING_USERNAME` | required | Existing. |

## Definition of done for MVP

Validator opens `/model/credit_risk_v3` in Portal, clicks **Generate documentation**, accepts the default (canonical MDD template, derive from bundle), submits. A .docx lands in `/mnt/data/{project}/autodoc_output/` within p50 < 4 min (15-min hard timeout). The doc cites Alice Chen, High, and "Credit risk scoring" from the bundle. The .docx metadata shows the 5 MVP provenance fields. The doc is attached to the bundle as evidence with label "Model Development Document - Auto-Generated (Draft)". Studio is NOT retired yet (that's the post-MVP port). The Week-4 dual-deploy dry-run kicks off after MVP ships.
