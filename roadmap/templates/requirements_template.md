# Requirements Engineering Specification for AI Agents

You are writing a concise Requirements Engineering (RE) specification for an engineering
team. This document is the input to a subsequent solution-engineering/design phase. Its
purpose is to establish a shared and unambiguous understanding of the problem before
anyone decides how to solve it.

The audience is PhD students, researchers and software developers — technically
competent but time-constrained. A reader should be able to understand the problem, why it
matters, the required outcome, the important use cases, the constraints, and how success
will be verified within a few minutes. The document is **reviewed repeatedly** while it
matures; everything about its structure serves a reviewer who wants to see only what
changed since the last round.

Think of this as a short engineering paper: structured, evidence-based, precise, and easy
to scan — but substantially shorter and less formal than an academic paper.

## Important boundary

This is a **requirements** document, not a solution-design document.

Your job is to establish: *What problem are we solving, what outcome is required, and
what constraints must the eventual solution satisfy?*

Do not prematurely decide: *How should we implement the solution?*

Solution alternatives, architecture, implementation details, technology choices, and
engineering estimates belong to the subsequent solution-engineering phase. The one
exception is spelled out in §6: **mockups of an external representation** (a file format,
an API payload, a CLI output) are requirements material when that representation is
itself what is being specified — and are preferred over prose.

## Which sections to write

Twelve sections do not fit in three pages; most are optional and scale with the request.

| Section | Small request (bug, one-field change) | Medium | Large / redesign |
|---|---|---|---|
| 0 Header, status tags, decision log | required | required | required |
| 1 Abstract | required | required | required |
| 2 Keywords and tags | required | required | required |
| 3 Executive summary | — (abstract suffices) | required | required |
| 4 Context and current situation | required, short | required | required, with quantified inventory |
| 5 Goals and non-goals | — | required | required |
| 6 Use cases, examples, mockups | when behavior or a representation changes | required | required |
| 7 Why it matters / cost of inaction | fold one sentence into §3 or §1 | one paragraph | required |
| 8 Requirements | required | required | required |
| 9 Constraints, invariants, assumptions | required | required | required |
| 10 Acceptance criteria | required | required | required |
| 11 Open questions and decisions | required (may be empty) | required | required |
| 12 Glossary | — | when the document introduces terms | required |

**Several delivery tracks.** If a request spans several independently reviewable tracks
(e.g. a library kernel, a file format, a migration, consumer integration), do not write one
long document. Write an **epic** document (this template, short: principles, cross-cutting
constraints, decision register, phase scope table, epic-level acceptance), a **phased plan**
(ordering, dependencies, review gates, checkboxes, parking lot), and **one requirements
document per phase** (this template again, 1–3 pages, opening with "what a reviewer must
decide here" and citing the epic's decisions by ID instead of repeating them). Phase
documents are written one at a time, each after its predecessor is accepted.

There is no closing summary section: the abstract and the executive summary already serve
that purpose, and a third restatement costs the reviewer a paragraph without adding
information.

---

## Document sections

### 0. Header, status tags and decision log

Start the document with a short header:

```
**Status:** draft | in review | accepted | superseded
**Date:** YYYY-MM-DD (last substantive change)
**Author(s):** …   **Reviewers:** …
**Supersedes / related:** links to earlier documents, plans, PRs
```

**Status tags on every item.** Every requirement, constraint, question and decision
carries one tag so a reviewer can skip what has not changed:

- `[given]` — stated by the requester or owner; not up for discussion here.
- `[proposed]` — introduced by the author; awaiting confirmation.
- `[decided YYYY-MM-DD]` — confirmed; the date lets a reviewer find what changed since
  their last read.
- `[superseded YYYY-MM-DD]` — kept in place, struck through or collapsed, with one line
  on why it was dropped. Do not delete decisions; a reviewer who argued for the
  alternative needs to see that it was considered.

**Decision log.** Answered questions move out of §11 into a dated decision (in place, in
the section they affect, or in a short "Decision register" list). A decision records
*what* was decided, *when*, *by whom* if relevant, and the alternatives rejected. An open
question that was answered by assumption is a defect, not a decision.

### 1. Abstract

Write a very short abstract, typically 3–6 sentences. It should state:

- the context
- the problem
- why it matters
- the required outcome

It should stand alone and allow someone unfamiliar with the work to understand what the
specification is about. Do not include detailed implementation ideas.

### 2. Keywords and Tags

Provide a short list of keywords that make the document easy to search and classify, and
assign one or more request-type tags. Use the most appropriate tags from this vocabulary
where possible:

`bug` · `feature` · `code-improvement` · `refactoring` · `behavior-change` · `migration` ·
`technical-debt` · `compatibility` · `performance` · `reliability` · `documentation` ·
`developer-experience`

Add a more specific tag only when useful. Do not add tags merely to make the list longer.

Example:

```
Tags: bug, compatibility, migration
Keywords: serialization, JSON, LPG, household reference, deserialization
```

### 3. Executive Summary

Give a concise summary that a busy engineer can read in under one minute. Cover:

- What is the problem?
- What needs to be achieved?
- Who or what is affected?
- What is the intended outcome?
- For small and medium requests: one sentence on the cost of inaction (see §7).

Do not repeat the abstract word-for-word. The abstract describes the topic; the executive
summary should emphasize the engineering significance and required outcome.

### 4. Context and Current Situation

Describe only the context needed to understand the requirements. Explain:

- How does the system behave today?
- What is problematic, missing, ambiguous, or limiting?
- What existing behavior or constraints matter?
- Is this a new capability, correction, migration, refactoring, or behavioral change?

When investigating an existing repository, base statements on actual evidence. Use
references such as `path/to/file.py:ClassName.method_name` when they materially help
establish an important claim. Do not fill the document with code references.

**Quantify.** Where the problem concerns many instances — components, call sites, config
fields, file formats, users — **count them** and state the counts ("~85 factories across
52 modules; 0 of them read more than one provider"). Counted evidence is the single best
protection against designing for cases that do not exist. Put the raw survey in an
appendix or a companion file (`<name>_inventory.md`) and keep only the headline numbers
and their consequences in the main document.

**Stakeholders and consumers.** Name who produces and who consumes the artifact or
behavior being changed — tools, services, scripts, people. Mark each as **existing**
(with a reference) or **hypothetical**. Only existing consumers constrain the solution; a
hypothetical one is recorded so that nobody designs for it by accident.

**Important distinction.** Always distinguish between:

| | |
|---|---|
| **Current behavior** | What the system actually does today. |
| **Required behavior** | What the system is expected to do after the work. |
| **Assumptions** | Things believed to be true but not established as requirements. |

Do not treat current implementation behavior as the desired behavior merely because it
exists.

### 5. Goals and Non-Goals

- **Goals** — list the concrete outcomes this work must achieve.
- **Non-Goals** — list important things explicitly outside the scope.

Keep both lists short. Do not use non-goals to list every unrelated feature in the system.

### 6. Use Cases, Examples and Mockups

Describe the most important real-world or system use cases. For each important case,
show:

- Situation / input
- Expected behavior / outcome

Use concrete examples where they make the requirement easier to understand. Prefer 2–5
useful examples over a long catalogue.

**Mockups are preferred over prose whenever a decision concerns an external
representation.** If the requirement is about a file format, a configuration schema, an
API payload, a CLI or report output, write the artifact as it should look — a complete,
realistic example, checked for syntactic validity where a parser exists — rather than
describing it. A mockup exposes ambiguities that prose hides, and reviewers decide faster
on a concrete thing. Keep mockups in companion files next to the document
(`<name>_mockup.<ext>`), reference them from here, and let the decisions they trigger flow
back into §8 and §11 with dates.

The boundary stays: mockups of **external** representations are requirements; sketches of
**internal** structure (classes, modules, algorithms) are solution design and do not
belong here.

### 7. Why It Matters / Cost of Inaction

Briefly explain why this requirement matters and what happens if it is not addressed.
Focus on concrete consequences such as:

- incorrect behavior
- user or developer impact
- maintenance burden
- compatibility problems
- inability to support an intended use case
- growing technical debt
- increased risk of future changes

Avoid repeating the problem statement. Do not exaggerate consequences that are not
supported by evidence. For small and medium requests, one sentence inside §3 is enough.

### 8. Requirements

Create a numbered list of clear, testable requirements with stable IDs (`R1`, `R2`, `R3`,
…; sub-items `R4.1`, `R4.2`). IDs are never renumbered or reused once the document has
been reviewed; a dropped requirement stays with a `[superseded]` tag.

Each requirement must describe **what** the system must do or guarantee, not **how**
it should implement it.

Good:

> **R1 — Preset names must be statically validated.** `[given]`
> Invalid preset names must be detectable by the project's static type checker.

Not appropriate:

> **R1 — Implement Catalog using a generic descriptor.**

The second example is a solution-design decision.

**Provenance.** Every requirement states where it came from, in its tag or a trailing
note: stated by the requester (`[given]`), derived from evidence (`[proposed; from
path/to/file.py:Symbol]` or from a counted inventory), or introduced by the author on
judgement (`[proposed]`). Reviewers weigh these differently, and a requirement without
provenance is the first thing they will question.

Where useful, classify requirements:

- **Functional** — what the system must do
- **Behavioral** — how it must behave
- **Compatibility** — what existing behavior must remain
- **Data/API** — required external representation or contract
- **Quality** — performance, reliability, maintainability, etc.

Do not create categories when they do not add useful information.

**Requirement quality.** Every requirement should be:

- necessary
- unambiguous
- testable
- implementation-independent
- consistent with the stated goals and constraints

Avoid vague requirements such as "The system should be easier to use." Prefer something
observable and testable.

### 9. Constraints and Invariants

Record facts that constrain the eventual solution. Examples:

- Existing external formats must remain compatible.
- Existing scenarios must produce identical results.
- A preset name becomes part of a public API.
- A particular data source does not contain information required for an inference.
- The domain currently models one heat-distribution loop per building.

Separate:

| | |
|---|---|
| **Known constraints / invariants** | Facts that the solution must respect. |
| **Assumptions** | Things believed to be true but requiring confirmation. |

Do not turn an assumption into a requirement without evidence. When a constraint is
derived from the repository, cite the relevant source when useful.

### 10. Acceptance Criteria

Define how completion can be verified. Acceptance criteria should demonstrate that the
requirements have been met without specifying the implementation. Examples:

- A valid scenario produces the expected result.
- An invalid identifier is rejected.
- Existing scenarios remain unchanged.
- A particular use case works under the relevant configuration.
- Required information appears in the resulting artifact.
- A required static/type check rejects invalid usage.

**Traceability.** Each criterion names the requirement IDs it verifies (`AC3 — verifies
R2, R4.1`). A requirement no criterion covers, or a criterion covering no requirement, is
a gap the reviewer should be able to find mechanically.

Where appropriate, distinguish functional, regression, compatibility and static/type
verification. Do not prescribe a particular test implementation unless it is itself a
requirement.

### 11. Open Questions and Decisions

List only questions that genuinely need resolution before or during solution
engineering. Each question names the requirements it blocks:

| ID | Question | Blocks | Status / answer |
|---|---|---|---|
| Q1 | … | R3, R7 | `[answered YYYY-MM-DD]` … |

**Q2 — <one-sentence question>** · blocks R4, R9
*Context.* … *Options.* (a) … → …; (b) … → … *Recommendation.* … because …

Do not invent answers to open questions. When a question is answered, it becomes a dated
decision (§0) — leave the entry in place with `[answered YYYY-MM-DD]` and a one-line
answer, so the reviewer who asked it can see the outcome without searching.

**Every open question must be decidable from the entry alone.** The reader must be able to
answer it without opening code, other documents, or searching the rest of this document.
Each unresolved question therefore carries, in this order:

1. **Question** — one sentence, answerable with a choice or a yes/no.
2. **Context** — the two to four facts that make it a question (what exists today, what
   the requirement needs), with the evidence inline (a count, a quoted value, a file
   reference), not as a pointer to go and look.
3. **Options** — each with its consequence for the requirements and for the work
   (what becomes simpler, what breaks, what it costs).
4. **Recommendation** — the author's pick and the one reason; or "none" and why.
5. **Blocks** — the requirement IDs that cannot be finalised until this is answered.

Questions that need more than this are not one question; split them. A catch-all entry
("do the proposed items stand?") is not a question: list the items that actually need a
decision, each with its context, and let the rest be confirmed by silence at review.
Use the table form only for answered questions; open ones use the five-part entry.

### 12. Glossary

One line per term **that this document introduces or uses in a specific sense** — not a
general glossary of the project. A reviewer arriving cold should never have to infer what
a "fact", a "preset" or a "realized record" means from context. Omit the section when the
document introduces no terms.

---

## Repository Investigation

When generating this document from an existing repository:

1. Inspect the relevant implementation, tests, configuration, and documentation.
2. Establish the actual current behavior before describing the problem.
3. Search for callers/usages that may constrain compatibility.
4. Identify existing behavior that must be preserved.
5. Identify assumptions and unknowns.
6. Look for existing tests or examples that establish intended behavior.
7. Count what you find (§4) before generalizing about it.
8. Do not expand the requirements based on unrelated technical debt.
9. Cite repository locations only where they provide useful evidence.
10. Never infer a requirement merely because a particular implementation would be
    convenient.
11. Do not assume that existing behavior is correct simply because it is implemented.

**Evidence.** When an important statement is derived from the repository, prefer evidence
over speculation. Use concise references such as `src/foo.py:BarConfig.build`. Only
include references when they materially support the specification.

---

## Requirements vs. Solution Design

Keep this boundary strict.

**Belongs in this document**

- What the system must achieve
- Required behavior
- Required inputs and outputs, including mockups of external representations (§6)
- Compatibility requirements
- Domain rules
- Invariants
- Constraints
- Important use cases
- Acceptance criteria
- Decisions that determine what the system is expected to do
- Known limitations that the eventual solution must address

**Does NOT belong here** (unless explicitly required by the requester)

- class/module design
- API implementation details
- database/schema design
- algorithms
- design patterns
- detailed architecture
- file-by-file implementation plans
- technology comparisons
- detailed migration implementation
- engineering estimates
- code structure
- detailed test implementation
- solution trade-off analysis

These belong to the solution-engineering/design phase.

If you have ideas about how the problem could be solved, do not turn them into
requirements. Only record a solution idea as an observation for solution engineering when
it is already present in the system, explicitly required by an external constraint, or
genuinely important for understanding the problem. Do not allow an implementation idea to
become a requirement merely because it appears to be the easiest solution.

---

## Handling Behavioral Changes

When the requested work may change existing behavior, explicitly state:

1. Current behavior
2. Required behavior
3. Compatibility expectation
4. Domain/product decision that determines the difference

Do not silently classify a behavioral change as a refactoring. If preserving existing
behavior is a requirement, state it explicitly. If changing existing behavior is
intentional, describe the required new behavior and identify the relevant decision or
justification.

---

## Style and Length

Target approximately 1–3 pages of Markdown for a medium-sized requirement; a large
redesign may run longer, but then the inventory and mockups live in companion files and
the main document stays scannable. Prefer shorter documents. Every section must earn its
place. Remove information that is already clearly stated elsewhere.

Use: short paragraphs · bullets · small tables · numbered requirements with status tags ·
concrete examples and mockups · counted evidence · concise repository references.

Avoid: long introductions · repeated explanations · generic engineering advice ·
speculative implementation details · unnecessary technical jargon · excessive code
excerpts.

The document should feel like a concise engineering paper, not a ticket and not an
architecture document. A solution engineer should be able to answer — *What exactly are
we trying to solve, what must the result achieve, what constraints must it respect, and
how will we know it is correct?* — without having to rediscover the requirements from the
codebase.

---

## Final Quality Check

Before finishing, verify the items a reviewer can check in a minute:

- [ ] Header present; every requirement, constraint, question and decision carries a
      status tag; superseded items are kept, not deleted.
- [ ] The abstract explains the problem and intended outcome.
- [ ] Current behavior and required behavior are clearly separated.
- [ ] Where the problem concerns many instances, they are counted.
- [ ] Producers and consumers are named and marked existing or hypothetical.
- [ ] Every requirement has provenance and is testable and implementation-independent.
- [ ] Every requirement is covered by an acceptance criterion, and every criterion names
      the requirements it verifies.
- [ ] Every open question is decidable from its entry alone (question, context with evidence, options with consequences, recommendation, blocks); none has been answered by assumption.
- [ ] Decisions on external representations are backed by a mockup, not only prose.
- [ ] Repository-derived claims have evidence where appropriate.
- [ ] No implementation has been prematurely prescribed.
