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
**Context:** Identified during design review (2026-03-24). The form is the primary interaction surface and repeat users will run this frequently.
**Depends on:** Nothing.
