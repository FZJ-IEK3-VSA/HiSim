# Implementation Specification for AI Agents

**Status:** first draft, 2026-08-25 · companion to `roadmap/templates/requirements_template.md`

You are writing a concise **implementation specification** for one requirements document
(an epic phase, or a stand-alone requirement). It is the bridge between the requirements
and the code, and it serves two readers at two times:

1. **Before implementation — the design reviewer.** The document lets them see how the
   requirements will be met, reason about the design without reconstructing it from
   hints, and object *before* code exists.
2. **During code review — the code reviewer.** The document tells them what to look for:
   which requirement each change serves, which invariants must hold, where the risk is,
   and what "done" looks like.

The audience is the same as for the requirements: technically competent, time-constrained.
Everything the requirements document already settled is **cited by ID, never repeated**.
Everything here is the author's proposal and carries the same status tags
(`[proposed]`, `[decided YYYY-MM-DD]`, `[superseded YYYY-MM-DD]`) so a second review round
reads only what changed.

## Boundary

The requirements document says *what* and *why*; this document says *how*. It must not
re-open a decided requirement (raise it as an open question with the evidence instead),
and it must not silently add requirements — a capability the design needs that the
requirements do not demand is listed in §3 as a derived requirement, tagged `[proposed]`,
and either accepted into the requirements document or cut.

Detail level: enough that a competent engineer unfamiliar with the design would build
essentially the same thing, and a reviewer can tell from a diff whether the code follows
it. Not: the code itself. Signatures of public APIs and wire shapes, yes; method bodies,
no. Where a picture explains a data flow or a state machine faster than prose, draw it
(ASCII or Mermaid); where a concrete example of a wire format or an error message explains
a rule faster than prose, write the example — the requirements' mockups are the first
fixtures and should be referenced, not re-invented.

## Which sections to write

| Section | Small change (one module, ≤ 3 requirements) | Medium | Large / new subsystem |
|---|---|---|---|
| 0 Header, decide-list, status tags | required | required | required |
| 1 Summary | required | required | required |
| 2 Requirements coverage matrix | required | required | required |
| 3 Design overview | short | required | required, with diagram |
| 4 Alternatives considered | one line per rejected option | required | required |
| 5 Public surface (APIs, formats, errors) | required for what changes | required | required |
| 6 Internal structure | — | as needed | required |
| 7 Data, state and invariants | when state exists | required | required |
| 8 Error handling | required | required | required |
| 9 Testing strategy (per acceptance criterion) | required | required | required |
| 10 Migration, compatibility, rollout | when behavior changes | required | required |
| 11 Risks and unknowns | — | required | required |
| 12 Code-review guide | required | required | required |
| 13 Open design questions | required (may be empty) | required | required |
| 14 Glossary | — | when new terms appear | required |

No closing summary. No engineering estimates unless the requester asked for them.

---

## Document sections

### 0. Header, decide-list and status tags

```
**Status:** draft | in review | accepted | superseded | implemented
**Date:** YYYY-MM-DD
**Implements:** <requirements document> (version/date of the accepted revision)
**Author(s):** …   **Reviewers:** …
**Branch / PRs:** planned branch name; PR list filled in as they open
**What a reviewer must decide here:** 3–6 items, each naming the section that argues it
```

Status tags on every design decision, alternative, risk and open question, exactly as in
the requirements template. A design decision the reviewer accepts becomes
`[decided YYYY-MM-DD]`; one they reject stays in §4 as `[superseded YYYY-MM-DD]` with the
reason. During implementation, a deviation from the accepted design is recorded here first
(as a dated amendment), then coded — the document stays the truth the code review checks
against.

### 1. Summary

Five to eight sentences: which requirements this implements, the central design idea in
one sentence, what is new, what is changed, what is deleted, and the one or two places
where the design is non-obvious or risky. A reviewer who reads only this should know
where to spend their attention.

### 2. Requirements coverage matrix

The traceability backbone. One row per requirement and acceptance criterion of the
requirements document, in their order:

| Req / AC | Design element (§ ref) | Code location (planned) | Test (§9) | Status |
|---|---|---|---|---|
| R4.3 | §3.2 binding rule, §5.1 `resolve_all(sources=)` | `hisim/config/engine.py` | T-4, T-5 | planned |
| AC-P1.2 | §8 error catalogue E-02 | `tests/test_sizing_engine.py::test_ambiguous_provider` | T-5 | planned |

Rules: every requirement appears (a requirement no design element serves is a gap to
declare, not to hide); every acceptance criterion maps to at least one test; **derived
requirements** the design needs but the requirements document lacks are added at the
bottom as `D1, D2 …` `[proposed]` with the requirement they derive from. The `Status`
column is updated during implementation (planned → in PR #n → merged) so the matrix
doubles as the progress record.

### 3. Design overview

The design in the shape a reviewer can reason about:

- **Concepts and their relations** — the three to seven nouns the design introduces or
  relies on, each defined in one line, and how they relate (a small diagram if there are
  more than four).
- **Data flow / control flow** — from input to output, for the main path: what is read,
  transformed, produced, by which part. One diagram or a numbered sequence.
- **Boundaries** — what this design owns, what it calls, what calls it; the layering or
  dependency rule it must respect (cite the constraint ID).
- **The non-obvious choice(s)** — one paragraph each on the design points where a
  reasonable engineer might have done it differently, with the reason. These are the
  items the decide-list in §0 points at.

Cite requirement IDs inline wherever a design element exists because of a requirement
(`… ordered by intra-config dependencies (R4.3, Q-P1.4)`).

### 4. Alternatives considered

For each significant design point: the chosen option and the rejected ones, each with
the consequence that decided it (one or two lines). Include alternatives the requirements
review already rejected only by reference. A design with no alternatives listed is
suspect — either the choice was obvious (say so in one line) or it was not examined.

### 5. Public surface

Everything another module, a file, a user or a tool can observe — the part of the design
that is expensive to change later:

- **APIs** — signatures with types, one-line contract each, including what is raised.
  Mark what is new, changed, deprecated, deleted.
- **Wire formats** — file shapes, message shapes, CLI arguments and output; reference the
  requirements' mockups as the normative examples and add only what they do not show.
- **Names** — new public names (classes, presets, facts, fields, CLI commands) with the
  convention they follow (cite it). Names are the hardest thing to change; list them all.
- **Errors** — the catalogue of error conditions this surface raises, each with an ID
  (`E-01 …`), the exception/exit code, and the message template with the variables it
  names (the requirements usually demand that messages name the offending parties — show
  that they do).

### 6. Internal structure

Modules/classes/functions to add, change, delete — as a list with one line of
responsibility each and the dependency direction between them. Line-count budget per
module if the project has one (cite the rule). Do not describe method bodies; describe
what each unit is responsible for and what it must not know about.

### 7. Data, state and invariants

- Data structures the design introduces (fields, types, mutability) and where they live.
- **Invariants** — statements that must hold at all times, numbered (`I-1 …`), each with
  where it is established and where it is checked (assertion, validator, contract test).
  Invariants are what the code reviewer verifies; write them so they can be checked from
  the diff.
- Lifecycle/ordering rules (what must happen before what), especially where the
  requirements demanded determinism or order-independence.

### 8. Error handling

The policy (fail-fast? recover? where is the boundary?), then the catalogue from §5
mapped to where each is detected and what the caller sees. State explicitly which
conditions are **not** errors (warnings, ignored) and why the requirements allow it.
Every "silently" in this section is a defect unless a requirement sanctions it.

### 9. Testing strategy

One entry per test or test group, `T-1 …`, each naming: the acceptance criteria and
invariants it verifies, the kind (unit / contract / golden / property / identity /
integration), the fixture it uses (the requirements' mockups first), and what failure
would look like. Then, in one paragraph: what is deliberately **not** tested and why.
Property-style tests that the requirements demand (an identity test, a round-trip test,
a "no I/O during parse" test) get their own entries with the exact property spelled out.

### 10. Migration, compatibility and rollout

- What existing behavior changes, for whom, and how the requirements' compatibility
  constraints are met (golden parity, unchanged outputs, deprecation).
- Order of landing (PR sequence) if more than one PR; what is reviewable independently;
  what must land together.
- Data/fixture regeneration, documentation updates, and deletion of the replaced code —
  named explicitly so nothing lingers.

### 11. Risks and unknowns

Numbered, each: the risk, its trigger, its effect, the mitigation or the point at which
it will be known. Include performance and scale where relevant (cite the counts from the
requirements' inventory). Unknowns that block the design are §13 questions instead.

### 12. Code-review guide

The section the code reviewer reads first. Written **for the reviewer**, in the
imperative:

- **Where to look first** — the two or three files or changes that carry the design
  risk, and what to check in each.
- **Invariant checklist** — the `I-n` invariants from §7 as checkboxes, each with where
  in the diff to verify it.
- **Requirement checklist** — the coverage matrix rows as checkboxes: "R4.3 — verify
  that `resolve_all` raises when ≥ 2 providers and no `sources` entry (T-5)".
- **Smells to reject** — concrete patterns that would violate this design (e.g. "any
  import from `hisim.components` inside `hisim/config/`", "any `entry_exists` fallback",
  "a preset carrying `component_id`"), each with the rule it breaks.
- **Out of scope for this review** — what the reviewer should *not* spend time on
  because a later phase owns it (cite the plan).

Keep it to one screen. A reviewer who has only this page and the diff should be able to
review.

### 13. Open design questions

Same rule as in the requirements template: every open question is decidable from its
entry alone — question, context with evidence, options with consequences, recommendation,
blocks. Design questions block design elements (§ refs) rather than requirement IDs.
Answered questions become dated decisions in place.

### 14. Glossary

Only terms this document introduces beyond the requirements' glossary.

---

## Working rules

- **Requirements first.** Do not start this document before the requirements document is
  accepted (or, in an epic, before its phase document is). If writing it reveals a gap in
  the requirements, fix the requirements document (dated), then continue.
- **Evidence over intention.** Claims about the current code ("the engine already
  iterates to a fixed point") carry a `path:Symbol` reference. Claims about behavior after
  the change carry a test ID.
- **The mockups are the fixtures.** If the requirements ship examples of the external
  representation, the tests load those files; do not write parallel fixtures that can
  drift.
- **Diagrams for structure, examples for rules.** A reviewer should never have to build
  a mental model from prose alone when a picture or a concrete example would do.
- **Amend, don't rewrite.** During implementation, deviations are dated amendments in the
  affected section; the code review checks the diff against the amended document, and
  the final status becomes `implemented` only when matrix and code agree.
- **Length.** 2–5 pages for a medium change; the coverage matrix and the review guide are
  the two sections that must never be cut for length.

## Final quality check

- [ ] Every requirement and acceptance criterion of the source document appears in the
      coverage matrix; derived requirements are tagged and few.
- [ ] Each non-obvious design choice has its alternatives and the deciding consequence.
- [ ] Every public name, signature, wire shape and error message is written out.
- [ ] Invariants are numbered, checkable from a diff, and each has a checking location.
- [ ] Every acceptance criterion maps to a test entry; property tests spell out the property.
- [ ] Every "silently" is sanctioned by a cited requirement or removed.
- [ ] The migration section names what is deleted.
- [ ] The code-review guide fits one screen and needs no other document.
- [ ] No decided requirement is re-opened outside §13; no new requirement hides in the design.
- [ ] Status tags present; superseded alternatives kept with their reason.
