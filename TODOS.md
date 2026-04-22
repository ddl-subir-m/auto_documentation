# TODOS

## Test Infrastructure

### Add E2E browser tests (Playwright)
**What:** Add Playwright as a dev dependency and create E2E tests for JS-only behaviors: tab persistence during polling, wizard step navigation, progressive disclosure toggle, keyboard shortcuts.
**Why:** Every JS-only feature (tab reset fix, wizard state, progressive disclosure) is currently untested. As more client-side interactivity is added, the risk of silent JS regressions grows.
**Pros:** Catches tab resets, wizard navigation bugs, keyboard interactions that unit tests can't cover.
**Cons:** Playwright adds ~100MB dev dependency, requires a running app instance for tests, adds CI complexity.
**Context:** Identified during eng review (2026-03-24). The wizard + tab reset fix adds significant JS behavior. Unit tests cover server-side route logic but not the client-side experience.
**Depends on:** Wizard implementation completing first.

## UX Enhancements

### Keyboard shortcut for Generate (Ctrl+Enter / Cmd+Enter)
**What:** Add a keyboard shortcut to trigger the Generate button from anywhere in the form.
**Why:** Domino UX principle "Adapt to repeat users" — power users generating docs for multiple projects shouldn't need to mouse to the button each time.
**Pros:** Faster workflow for repeat users.
**Cons:** Adds ~10 lines of JS event handling.
**Context:** Identified during design review (2026-03-24). The form is the primary interaction surface and repeat users will run this frequently. Bundling into F4 modal implementation (M2) per 2026-04-22 eng review.
**Depends on:** F4 modal implementation (M2).

## MRM-Portal Integration

### Post-MVP generation cache (evaluate once real-world repeat-click data exists)
**What:** Consider reintroducing a generation cache that short-circuits repeat clicks on unchanged bundles. Key would hash the context JSON snapshot + commit_sha + template_version. Add a "Last generated on X, force regenerate?" UI path.
**Why:** LLM generation costs real money. Customer soak data will show whether validators actually re-click on unchanged bundles often enough to justify the cache machinery, or whether per-bundle soft lock alone is sufficient rate-limiting.
**Pros:** Token-spend reduction on repeat clicks if that's a real pattern.
**Cons:** Codex called this out as "one DB, one UI branch, one invalidation matrix" — all measurable cost with speculative benefit. Also complicates audit (cached docs need provenance showing they came from cache).
**Context:** Cut from MVP during 2026-04-22 /plan-eng-review (E-A3 decision). Also re-evaluated 2026-04-22 when content hash was removed entirely (Shape 1 locked). Without content hashing, a cache key would hash the context JSON itself — still feasible, but only worth doing if repeat-click frequency in soak data justifies it.
**Depends on:** Customer-soak metric: "repeat-Generate clicks on unchanged bundles per user per week." If p50 > 3/week, the cache is worth building.

### Week-4 dry-run dual-deploy for M3 soak risk
**What:** Before M3 Studio retirement, add a Week-4 milestone where Portal has Generate+history+attach-back live AND Studio still runs. Validators can click Generate in either surface; audit-log both paths; compare output drift over a 1-week window. If drift >5% (functional differences beyond timestamps), delay M3 and investigate.
**Why:** Codex plan review flagged that the PRD's "soak during M2" (R4 mitigation) is illusory because Studio's power-user routes (`/api/datasets`, `/run`, `/job-history`, `/stop-job-history`) don't have Portal equivalents until M3. So retirement day IS the soak — there's no window where both surfaces run with Portal handling real work. This mitigation creates that window.
**Pros:** Real soak period. Drift detection. Fast rollback if M3 introduces regressions (keep Studio up for an extra week).
**Cons:** Doubles operational surface for 1 week. Audit-log tooling needs to compare across both products.
**Context:** Identified during 2026-04-22 /plan-eng-review (Codex outside-voice point 2). Accepted as mitigation for >50% M3 slip probability.
**Depends on:** M2 complete (Portal Generate+history+attach-back live).

### Create MRM-Portal/DESIGN.md (consolidate design system for the integration)
**What:** Write a project-level DESIGN.md for MRM-Portal capturing: Ant Design 5.x component inventory used by the portal, Domino theme tokens (colors / typography / spacing / borderRadius), accessibility requirements (WCAG AA, 44px touch targets, keyboard patterns), modal vs. drawer vs. inline-panel decision rules, and the autodoc integration component mapping (from the 2026-04-22 design review addendum on the feature/mrm-portal-integration design doc).
**Why:** Neither MRM-Portal nor autodoc has a DESIGN.md today. The global rule files (`~/.claude/rules/usability-design-principles.md`, `~/.claude/rules/how-to-build-domino-apps.md`) are the de-facto system but aren't project-visible. New contributors and future /plan-design-review runs would benefit from a single project-level source of truth.
**Pros:** Cleaner baseline for future design reviews. Explicit component inventory reduces "which Ant component do we use?" friction. Makes onboarding for new Portal contributors faster.
**Cons:** ~1 hour to write; has to stay in sync with the global rules as Domino Design System evolves.
**Context:** Identified during 2026-04-22 /plan-design-review. The design addendum on the feature/mrm-portal-integration design doc contains 15 decisions + an Ant component mapping table that would seed DESIGN.md directly.
**Depends on:** Nothing (can land independently of M1/M2/M3).

### Staleness indicator on Documentation history rows
**What:** Show validators when a doc was generated against a bundle state that has since changed. Not drift during generation (there is no drift anymore under Shape 1 — the context snapshot is frozen at submit). Rather: "this doc was generated on 2026-04-01 against policy version 1.2; the bundle has been updated 5 times since." Small muted-text label on the Documentation history row.
**Why:** Validators reviewing an older auto-generated doc need to know whether it still reflects current state. Without a signal, they either blindly trust old docs or regenerate-everything-just-in-case. A visible "may be outdated" cue enables a targeted "regenerate" click.
**Pros:** Compliance story stronger ("auditor sees which docs may be stale"). Cheap: compare row's `policy_version_id` to bundle's current value; show muted text if differ.
**Cons:** Needs a policy-version comparison on every history render. Lightweight but adds a graph read.
**Context:** Identified during 2026-04-22 /plan-eng-review (as "Drift-detected UI surfacing" against a content_hash that no longer exists). Re-scoped 2026-04-22 when content hash was removed — staleness signal is cheap even without hashing, just compare `policy_version_id`.
**Depends on:** Provenance record shipped (MVP tier includes `policy_version_id`).
