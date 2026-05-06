# `spec_from_policy` — design decisions and non-obvious behaviour

`autodoc/spec_from_policy.py` turns a governance policy definition into a doc
section list. The logic is small but has several decisions that look wrong at
first glance. This document records _why_ each choice was made so future
readers don't have to reverse-engineer it.

---

## What it does

`derive_spec(policy_def, doc_type)` accepts whatever Portal wrote into
`policy_def` in the context file and returns a spec dict shaped like the
canonical template for `doc_type` (`mdd`, `vr`, or `mr`), with the
`sections` list replaced by names derived from the policy.

If nothing useful can be extracted, it returns the canonical template
unchanged and logs a warning. The only error it raises is `ValueError` for
an unrecognised `doc_type`.

---

## Decision 1: Two input shapes, not one

**What:** The function accepts two completely different `policy_def` shapes
without the caller needing to specify which one.

**Shape A — flat:**
```json
{
  "required_artifacts": ["Model Card", "Risk Assessment"],
  "sections": ["Intended Use", "Ownership"]
}
```
Used by direct CLI callers or tests that construct policy defs by hand.

**Shape B — Domino policy:**
```json
{
  "id": "pol-1",
  "version": "v1.0",
  "stages": [
    {
      "name": "Intake",
      "evidenceSet": [
        {
          "definition": [
            {"artifactType": "textinput", "details": {"label": "Model Card"}}
          ]
        }
      ]
    }
  ]
}
```
This is the raw response from `GET /api/governance/v1/policies/{id}/definition`,
written verbatim by Portal into the context file.

**Why not normalise on one shape:** Portal owns the governance API response
format; autodoc should not require Portal to pre-process it. Accepting the raw
response means Portal has one fewer place to introduce bugs. The flat shape
stays available for standalone CLI use without a real Domino policy.

**How disambiguation works:** The function checks for `required_artifacts` or
`sections` keys first (flat shape detection). If neither is present, it falls
through to the Domino walker. There is no explicit `"shape"` discriminator.

---

## Decision 2: Approvals are excluded

**What:** `stages[].approvals[]` are intentionally not walked, even though
they contain `evidence.definition[]` artifacts with labels.

**Why:** Approval entries are workflow gate prompts — questions like "Do you
approve this model for production?" or "Does the MRC approve deployment?". They
are not documentation sections. Including them produced nonsensical section
names in the generated `.docx` (a radio-button question as a heading).

**Discovery:** This was caught against the dogfood policy on
cloud-dogfood.domino.tech, where the approval labels literally appeared as
doc sections in the output. Fixed in `feature/mrm-portal-integration` at
commit `974a536`.

**The check:** `_walk_domino_policy` iterates only
`stage.get("evidenceSet")`. The `stage.get("approvals")` path is never
visited.

---

## Decision 3: Stage names lead the section list

**What:** Derived sections are `[stage_names..., artifact_labels...]` — stage
names come first.

**Why:** Stage names (`"Model Development"`, `"Validation"`, `"Production
Approval"`) are structural headings that mirror the governance workflow. They
give the document a natural top-level outline that auditors and reviewers
already understand. Artifact labels (`"Model Card"`, `"Risk Assessment"`) are
the content _within_ each stage — they work better as subsections or as the
first items after the stage heading.

Reversing the order produces a flat list of artifact names with no structural
grouping, which is harder to navigate in a multi-stage policy.

---

## Decision 4: The `{"definition": ...}` envelope

**What:** Before walking stages, `_unwrap_definition_envelope` checks whether
`policy_def` is `{"definition": "<yaml string>"}` or `{"definition": {...dict...}}`
and unwraps one layer if so.

**Why:** `GET /api/governance/v1/policies/{id}/definition` returns the policy
body wrapped in a `definition` key. Portal writes the full raw response into
`ctx.json`, including this wrapper. Without unwrapping, `stages` would never
be found at the top level and the function would always fall back to the
canonical template.

Additionally, some policy stores serialise the inner body as a YAML string
rather than a parsed dict. The unwrapper parses the YAML when it encounters a
string value. If YAML parsing fails, it logs a warning and returns the original
input (which then falls through to canonical fallback).

---

## Decision 5: Graceful fallback, never raises on bad input

**What:** Malformed stages, non-dict artifacts, missing `details`, wrong types
— all are silently filtered out, not raised. The only condition that raises
is `ValueError` for an unrecognised `doc_type`.

**Why:** `policy_def` comes from an external governance API whose schema can
change. A rigid parser would break generation entirely whenever the API
adds a new artifact type or rearranges a field. The fallback guarantees that
generation always produces _something_, and the warning log makes the fallback
observable.

**Specific filtering rules:**
- Non-dict items in `stages` list → skipped
- Stages with blank or missing `name` → stage name not added (artifact labels
  from its `evidenceSet` still extracted)
- `evidenceSet` value that is not a list → skipped entirely for that stage
- `artifactType: "text"` artifacts → skipped (these are free-text guidance
  blurbs, not user-facing artifact names)
- Artifacts with missing or non-dict `details` → skipped
- Labels that are empty strings or whitespace → skipped

---

## Decision 6: Hints are preserved for matching section names

**What:** The derived spec keeps hints only for section names that appear in
both the canonical template's `hints` dict _and_ the derived `sections` list.
Hints for custom policy-specific sections are not generated.

**Why:** Hints are LLM guidance strings baked into the canonical template
(e.g. what to emphasise in "Executive Summary"). They are written for the
canonical section names, not for arbitrary policy artifact labels. Carrying
hints for a name like `"Intake Evidence Checklist"` that the template author
never wrote guidance for would be misleading. The LLM receives no hint for
custom sections and uses the section name + code context alone.

---

## Extending this in the future

**Adding a new input shape:** Detect it in `derive_spec` before the
`_unwrap_definition_envelope` call. Add corresponding tests in
`tests/test_spec_from_policy.py` using the `_DOMINO_POLICY` fixture as a
model.

**Adding a new `doc_type`:** Add the template YAML to `autodoc/templates/`,
add the name to `VALID_DOC_TYPES`, and add a row to the parametrize lists in
the test file.

**Changing the approvals decision:** If a future policy schema distinguishes
"documentation evidence" from "sign-off evidence" at the schema level (e.g.
via an `evidenceRole` field), the walker can be updated to include
`approvals[]` entries that carry `evidenceRole: "documentation"`. The current
exclusion is a blanket rule because no such discriminator exists in the schema
as of 2026-05.
