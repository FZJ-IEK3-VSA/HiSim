

# Requirements Engineering Specification for AI Agents

You are writing a concise **Requirements Engineering (RE) specification**
for an engineering team.

This document is the **input to a subsequent solution-engineering/design phase**.
Its purpose is to **establish a shared and unambiguous understanding of the problem
before anyone decides how to solve it**.

The audience is PhD students, researchers and software developers, technically
competent but **time-constrained**. A reader should be able to understand the
problem, why it matters, the required outcome, the important use cases, the
constraints, and how success will be verified within a few minutes.

## Important boundary

This is a **requirements document, not a solution-design document**.

Your job is to establish:

> **What problem are we solving, what outcome is required, and what constraints
> must the eventual solution satisfy?**

Do **not** prematurely decide:

> **How should we implement the solution?**

Solution alternatives, architecture, implementation details, technology choices,
and engineering estimates belong to the subsequent **solution-engineering phase**.

---

## 1. Abstract

Write a very short abstract, typically **3–6 sentences**.

The abstract should state:

- the context
- the problem
- why it matters
- the required outcome

It should stand alone and allow someone unfamiliar with the work to understand
what the specification is about.

Do not include detailed implementation ideas.


## 2. Keywords and Tags

Provide a short list of keywords that make the document easy to search and classify.

Also assign one or more **request-type tags**.

Use the most appropriate tags from this vocabulary where possible:

- `bug`
- `feature`
- `code-improvement`
- `refactoring`
- `behavior-change`
- `migration`
- `technical-debt`
- `compatibility`
- `performance`
- `reliability`
- `documentation`
- `developer-experience`


Add a more specific tag only when useful.

**Example:**

`Tags: bug, compatibility, migration`
`Keywords: serialization, JSON, LPG, household reference, deserialization`

Do not add tags merely to make the list longer.


## 3. Executive Summary

Give a concise summary that a busy engineer can read in under one minute.

Cover:

* What is the problem?
* What needs to be achieved?
* Who or what is affected?
* What is the intended outcome?


Do not repeat the abstract word-for-word. The abstract describes the topic; the executive summary should emphasize the **engineering significance and required outcome.**


## 4. Context and Current Situation

Describe only the context needed to understand the requirements.

Explain:

* How does the system behave today?
* What is problematic, missing, ambiguous, or limiting?
* What existing behavior or constraints matter?
* Is this a new capability, correction, migration, refactoring, or behavioral change?

When investigating an existing repository, base statements on actual evidence.

Use references such as:

`path/to/file.py:ClassName.method_name`

when they materially help establish an important claim.

Do not fill the document with code references.

### Important distinction

Always distinguish between:

**Current behavior**
What the system actually does today.

**Required behavior**
What the system is expected to do after the work.

**Assumptions**
Things believed to be true but not established as requirements.

Do not treat current implementation behavior as the desired behavior merely because it exists.


## 5. Goals and Non-Goals

### Goals

List the concrete outcomes this work must achieve.

### Non-Goals

List important things explicitly outside the scope.

Keep both lists short.

Do not use non-goals to list every unrelated feature in the system.


## 6. Use Cases and Examples

Describe the most important real-world or system use cases.

For each important case, show:

* Situation / input
* Expected behavior / outcome

Use concrete examples where they make the requirement easier to understand.

Examples should clarify the requirement, not prescribe an implementation.

Prefer 2–5 useful examples over a long catalogue.


## 7. Why It Matters / Cost of Inaction

Briefly explain why this requirement matters and what happens if it is not addressed.

Focus on concrete consequences such as:

* incorrect behavior
* user or developer impact
* maintenance burden
* compatibility problems
* inability to support an intended use case
* growing technical debt
* increased risk of future changes

Avoid repeating the problem statement.

Do not exaggerate consequences that are not supported by evidence.


## 8. Requirements

Create a numbered list of clear, testable requirements.

Use stable IDs:

* R1
* R2
* R3

Each requirement must describe **what the system must do or guarantee**, not how it should implement it.

### Good

**R1 — Preset names must be statically validated.**
Invalid preset names must be detectable by the project’s static type checker.

### Not appropriate

**R1 — Implement Catalog using a generic descriptor.**

The second example is a solution-design decision.

Where useful, classify requirements:

* **Functional** — what the system must do
* **Behavioral** — how it must behave
* **Compatibility** — what existing behavior must remain
* **Data/API** — required external representation or contract
* **Quality** — performance, reliability, maintainability, etc.

Do not create categories when they do not add useful information.

### Requirement quality

Every requirement should be:

* necessary
* unambiguous
  testable
* implementation-independent
* consistent with the stated goals and constraints

Avoid vague requirements such as:

“The system should be easier to use.”

Prefer something observable and testable.


## 9. Constraints and Invariants

Record facts that constrain the eventual solution.

Examples:

* Existing external formats must remain compatible.
* Existing scenarios must produce identical results.
* A preset name becomes part of a public API.
* A particular data source does not contain information required for an inference.
* The domain currently models one heat-distribution loop per building.

Separate:

### Known constraints / invariants

Facts that the solution must respect.

### Assumptions

Things believed to be true but requiring confirmation.

Do not turn an assumption into a requirement without evidence.

When a constraint is derived from the repository, cite the relevant source when useful.


## 10. Acceptance Criteria

Define how completion can be verified.

Acceptance criteria should demonstrate that the requirements have been met without specifying the implementation.

Examples:

* A valid scenario produces the expected result.
* An invalid identifier is rejected.
* Existing scenarios remain unchanged.
* A particular use case works under the relevant configuration.
* Required information appears in the resulting artifact.
* A required static/type check rejects invalid usage.

Where appropriate, distinguish:

* functional verification
* regression verification
* compatibility verification
* static/type verification

Do not prescribe a particular test implementation unless it is itself a requirement.


## 11. Open Questions and Decisions

List only questions that genuinely need resolution before or during solution engineering.

Use:

| ID | Question | Why it matters |
| -- | -------- | -------------- |
| Q1 | ...      | ...            |
| Q2 | ...      | ...            |


Do not invent answers to open questions.

If a decision has already been made, record it as a **decision/constraint** rather than presenting it as an open question.

For unresolved questions, explain only enough to make the decision understandable.


## 12. Summary

End with a very short summary containing:

* the core problem
* the required outcome
* the most important constraints
* unresolved decisions that affect solution engineering

Do not introduce new information here.


## Repository Investigation

When generating this document from an existing repository:

1. Inspect the relevant implementation, tests, configuration, and documentation.
2. Establish the actual current behavior before describing the problem.
3. Search for callers/usages that may constrain compatibility.
4. Identify existing behavior that must be preserved.
5. Identify assumptions and unknowns.
6. Look for existing tests or examples that establish intended behavior.
7. Do not expand the requirements based on unrelated technical debt.
8. Cite repository locations only where they provide useful evidence.
9. Never infer a requirement merely because a particular implementation would be convenient.
10. Do not assume that existing behavior is correct simply because it is implemented.



### Evidence

When an important statement is derived from the repository, prefer evidence over speculation.

Use concise references such as:

`src/foo.py:BarConfig.build`

Only include references when they materially support the specification.


## Requirements vs. Solution Design

Keep this boundary strict.

### Belongs in this document

* What the system must achieve
* Required behavior
* Required inputs and outputs
* Compatibility requirements
* Domain rules
* Invariants
* Constraints
* Important use cases
* Acceptance criteria
* Decisions that determine what the system is expected to do
* Known limitations that the eventual solution must address

### Does NOT belong here

Unless explicitly required by the requester:

* class/module design
* API implementation details
* database/schema design
* algorithms
* design patterns
* detailed architecture
* file-by-file implementation plans
* technology comparisons
* detailed migration implementation
* engineering estimates
* code structure
* detailed test implementation
* solution trade-off analysis

These belong to the **solution-engineering/design phase.**

If you have ideas about how the problem could be solved, do not turn them into requirements.

Only record a solution idea as an **observation for solution engineering** when it is already present in the system, explicitly required by an external constraint, or genuinely important for understanding the problem.

Do not allow an implementation idea to become a requirement merely because it appears to be the easiest solution.


## Handling Behavioral Changes

When the requested work may change existing behavior, explicitly state:

1. Current behavior
2. Required behavior
3. Compatibility expectation
4. Domain/product decision that determines the difference

Do not silently classify a behavioral change as a refactoring.

If preserving existing behavior is a requirement, state it explicitly.

If changing existing behavior is intentional, describe the required new behavior and identify the relevant decision or justification.


## Style and Length

Target approximately **1–3 pages of Markdown** for a medium-sized requirement.

Prefer shorter documents.

Every section must earn its place.

Remove information that is already clearly stated elsewhere.

Use:

* short paragraphs
* bullets
* small tables
* numbered requirements
* concrete examples
* concise repository references

Avoid:

* long introductions
* repeated explanations
* generic engineering advice
* speculative implementation details
* unnecessary technical jargon
* excessive code excerpts


A solution engineer should be able to answer:

**What exactly are we trying to solve, what must the result achieve, what constraints must it respect, and how will we know it is correct?**

without having to rediscover the requirements from the codebase.


## Final Quality Check

Before finishing, verify that:

* The abstract explains the problem and intended outcome.
* The executive summary is understandable in under one minute.
* The request type and keywords/tags are useful.
* Current behavior and required behavior are clearly separated.
* Goals and non-goals are clear.
* Important use cases have concrete examples.
* The cost of inaction is concrete and not exaggerated.
* Every requirement is necessary, unambiguous, testable, and implementation-independent.
* Important constraints and invariants are explicit.
* Assumptions are clearly identified.
* Acceptance criteria can demonstrate that the requirements were met.
* Open questions are genuinely unresolved and have not been answered by assumption.
* Repository-derived claims have evidence where appropriate.
* No implementation has been prematurely prescribed.
* The document does not repeat itself.
* The document is short enough for a time-constrained engineer to read quickly.
* The document gives a solution engineer everything needed to begin solution design without rediscovering the requirements.
