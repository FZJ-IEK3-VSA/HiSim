# Spec template: design records with owner decisions

How a HiSim spec is written: when one is needed, what goes into it and what into beads, and how a decision is
taken, amended and recorded. Copy the skeleton below into `roadmap/<topic>_spec.md` (or a subdirectory of
`roadmap/` for an epic with several documents) and replace every `<...>`. The form is drawn from the specs that
work well today, `roadmap/hydronic_coupling_spec.md` and `roadmap/hydronic_part_load_spec.md`, and the decision
register `roadmap/renovisor/challenges.md`. Their older spellings (`*Amended (owner, ...)*`, an amendment ending in
a full stop) stay as they are, since a decision is never rewritten; a new spec uses the spellings below.

For a large new subsystem with a separate requirements phase, `requirements_template.md` and
`implementation_spec_template.md` in this directory are the longer forms. They keep their own convention: status
tags (`[decided YYYY-MM-DD]`, `[superseded YYYY-MM-DD]`) and a decision log, and their `D1, D2 …` are derived
requirements, not owner decisions. The rules below apply to specs written from this template.

## When a spec is needed

A spec is written, and agreed with the owner, before the first pull request when the work:

- **spans several pull requests**, for example a staged migration or a stack of dependent changes;
- **changes a model**: a physical law, a control rule, a step-length adaptation, anything that moves simulation
  results on purpose (`system_docs/modelling_guide.md`, `system_docs/results_and_interfaces.md`);
- **changes a public interface** in a way one pull request cannot finish (`results_and_interfaces.md` §4 and §5).

A single bug fix, a refactoring or a new preset needs no spec: its bead and its PR description are the record.

## What goes into a spec and what into beads

- **The spec holds the design and its reasons**: the problem with numbers, the decisions, the design, what changes
  in results and interfaces, how it is verified, and the measured results.
- **Every todo is a bead**, never a list in the spec (AGENTS.md, "How HiSim uses beads"). The spec's bead table
  names each bead, what it covers and its PR; an open question is an `owner-decision` bead as well as an entry in
  the spec's open questions.
- **The spec is a design record, not documentation of the code.** Docstrings never cite it ("see spec §4"); they
  state the rule themselves (`system_docs/coding_style.md` §6). A guide states its rules itself too and may name
  a spec as the source of an example or a decision. A spec may cite code by file path and
  name; line numbers are cited against a named commit, since they drift.

## How a decision is taken, amended and recorded

- **A decision has a number, a date and an author**: `D<n> — <the decision as one sentence>` with `(owner,
  YYYY-MM-DD)`, then its reason and its consequence. Numbers are never reused or renumbered; other documents,
  beads and commit messages cite them (`D2 of the hydronic coupling spec`).
- **A decision is amended below its own text, never rewritten.** Add `*Amendment (owner, YYYY-MM-DD):* <what
  changes and why>` under the decision. The original text stays, so a reader of an older PR can still follow it.
  A decision that no longer holds gets `*Withdrawn (owner, YYYY-MM-DD):* <why, and what replaces it>`.
- **An amendment is made in the pull request that acts on it**, together with the status line and the bead table,
  so the spec on `main` always describes `main`. A decision taken in a review or a session is written into the spec
  and its bead in the same pull request; nothing stays only in a chat or a PR comment.
- **Only the owner takes and amends a decision.** An agent proposes options with a recommendation (under open
  questions and in an `owner-decision` bead) and records the decision once it is taken, with the owner's date.

---

## Skeleton

```markdown
# Spec: <the result, as one sentence>

Status: <draft | decided (owner, YYYY-MM-DD) | building: <which part> | built (#PR, #PR) | superseded by <file>>,
<one sentence on what is built and what is not>. Owner: <name>. Epic: <hisim-...>. Builds on: <specs, PRs>.

## 1. Problem

<What is wrong or missing, with a concrete example and a number: which setup, which step length, how far off.
Cite code as `path`, `Class.method`; line numbers only with the commit they refer to.>

## 2. Decisions

**D1 — <the decision as one sentence>** (owner, YYYY-MM-DD). <Why, in one or two sentences.> Consequence: <what
follows for the design, the results or other components.>

*Amendment (owner, YYYY-MM-DD):* <what changes, and why.>

**D2 — <...>** (owner, YYYY-MM-DD). <...>

*Withdrawn (owner, YYYY-MM-DD):* <why, and what replaces it.>

## 3. Terms

- **<term>**: <definition, with units>.

## 4. Design

<Who does what, which component of which kind (`system_docs/component_design_principles.md`), the laws with their
units, the ports and their signs (`system_docs/modelling_guide.md`). A diagram or a worked number where it is
faster than prose.>

## 5. What changes and what does not

- Results: <which KPIs move, in which twins; whether 60 s results stay bit-identical>.
- Public interfaces: <renamed or new outputs, KPIs, configuration fields, file format; the heads-up on
  renovisorissues (`system_docs/results_and_interfaces.md` §5)>.
- Goldens and twins: <re-bless through `golden-update.yml`, re-recorded twins>.

## 6. Verification

<The tests that show it works: unit tests of the pure functions, the energy balance, the comparison with the 60 s
reference and its tolerance, the golden gate.>

## 7. Measured results

<Dated measurements: run, step length, commit, the numbers against the reference, as tables.>

## 8. Open questions

- **Q1. <the question>** <The options, each with its consequence, and a recommendation.> Bead <hisim-...>
  (`owner-decision`).

## 9. Beads

Epic <hisim-...>.

| Bead | Content | PR |
|---|---|---|
| hisim-<id> (P<n>) | <what it covers> | #<n> or – |
```
