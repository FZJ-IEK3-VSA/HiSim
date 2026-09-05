# RenoVisor backend — discussion, rejected alternatives and adjacent findings

**Companion to** `roadmap/renovisor/requirements.md` · **Date:** 2026-08-27
**Status:** not a requirements document. Nothing here is binding.

`requirements.md` states one thing and states it tightly:

> Take a configuration file and a parameters file → select a base energy-system file →
> parametrise it → run it → return KPIs, costs, a translation report, the parametrised file and
> the realized record → or write `errors.json` saying why not.

Everything that does not bear directly on that sentence lives here instead. Three kinds of
thing, and it is worth knowing which you are reading:

1. **Deliberations behind decisions that are already taken.** The requirements document records
   the outcome in one line; the reasoning, the options weighed and the alternatives rejected are
   kept here so that a reviewer who argued for a different answer can see it was considered, and
   so that a future reader who wants to reopen a decision knows what it cost.
2. **Requirements and questions that were withdrawn** when the scope narrowed. Kept, per the
   decision-log rule, because a deleted requirement is indistinguishable from one nobody thought
   of.
3. **Real findings that are not HiSim's problem.** Defects in the API contract that a survey
   turned up and that somebody should fix, but which do not block the pipeline above. They are
   here rather than in the requirements so that §8.2 stays a list of things that block work, and
   they are here rather than nowhere because they are true and were expensive to find.

---

## Part 1 — Deliberations behind decisions already taken

Each of these is a closed question. `requirements.md` §11 carries the one-line outcome in its
decision register; what follows is the analysis that produced it.

---

**Q1 — Does HiSim supply a calculation library, or the HTTP service itself?** · `[answered 2026-08-27]` — **(a) library, invoked as a container.** Entry kept below for the reviewer who argued the alternatives. Blocked R13, A-1, and the scope of §8.1.

*Context.* The contract specifies 15 paths with lazy start, job coalescing, ETags, RFC 7807
errors, Supabase JWT validation and a GDPR cascade. None of that is simulation work, and none of
it exists in HiSim. The existing translator is a CLI that pushes results to a URL
(`hisim/renovisor/spec.md` §7), which is the opposite arrangement to a service that is polled.
`hisim/energy_system/executor.py:build_energy_system` takes a filesystem path, so a hosting
service would today have to materialise files per request.

*Options.* (a) **HiSim supplies a library**; a separate backend service owns HTTP, storage,
caching and auth. Consequence: §8.1 shrinks to translation, execution and derivation; R13
becomes a real requirement (in-memory entry points); someone outside this repository must build
and staff the service. (b) **HiSim supplies the service**; the API lives in this repository.
Consequence: HiSim acquires a web framework, a job queue, a result store and an auth
dependency, all of which are new categories of code for a simulation package. (c) **A thin
service in this repository over a library seam**, so the seam is testable and the service is
replaceable. Consequence: middle cost; the seam of (a) plus a reference implementation.

*Recommendation.* (a). The contract's own framing — "calculation service over non-PII,
content-addressed inputs" — describes a caching layer in front of a pure function, and that pure
function is the only part HiSim is qualified to own.

*Answer.* `[decided 2026-08-27, owner]` **(a)**, with the invocation mechanism specified: the
service is C#, lives in another repository, and calls HiSim by starting a container with a house
configuration in it. Note what this changes about (a) as it was drafted — the seam is a
*process* boundary, not a Python import, so R13's original demand for in-memory entry points is
the wrong requirement and has been replaced (R13, R13.1, R13.2, C11, C12). It also makes the
container image a natural carrier for the version stamp of R14.

---

**Q7 — Who owns the 10 KPI fields HiSim cannot compute?** · `[answered 2026-08-27]` — **HiSim does; constants until implemented, labelled as mocked.** Entry kept for the alternatives it rejected.

*Context.* `field_inventory.md` §3: of 13 `Kpis` fields, HiSim can produce 3.
`energy_label` needs an Irish BER/DEAP rating procedure. `disruption_days_by_level` (4 fields)
is construction logistics. `indoor_air_quality`, `thermal_insulation_effect` and
`summer_heat_protection` are 1-to-5 or low/medium/high scores with no stated derivation.
`comfort.heating` and `comfort.cooling` are derivable in principle from set-point deviation
hours, which HiSim simulates but does not report.

*Options.* (a) **Drop all ten** from the contract until each has an owner and a definition.
Consequence: the frontend loses ten display fields it has presumably designed around. (b) **Keep
them, mark each `nullable`, and serve null** until implemented. Consequence: honest, and the
frontend must handle a mostly-empty KPI object indefinitely. (c) **Split them out** into a
separate `assessment` block with its own owner outside HiSim, so the contract stops implying
they come from the simulation. Consequence: makes the ownership visible, which is the actual
problem; the fields still need definitions.

*Recommendation.* (c), then (a) for whichever of the ten still has no owner at sign-off. Two of
the ten (`comfort.*`) are worth keeping and specifying, since HiSim already simulates what they
need.

*Answer.* `[decided 2026-08-27, owner]` **A fourth option: keep all ten and serve constants**
until the real implementations land over the coming months. HiSim owns them; nothing is dropped
and nothing is split out. The frontend builds against the full KPI object from day one.

*What the answer requires in exchange.* A mocked value must be **labelled as mocked in the
result** (A12, R8, AC8.1). A constant that is indistinguishable from a computed number is worse
than an absent field: it is a plausible figure in a renovation recommendation with no simulation
behind it, and no downstream consumer — a package comparison, a report, a homeowner's decision —
can tell. The label is also what makes the mocks removable, since it names exactly which cached
results go stale when a real implementation ships (Q2, R14).

*Blocks.* Closed.

---

**Q9 — What exactly does the container take in and give back?** · `[answered 2026-08-27]` — **files in, files out, container reused.** Entry kept for the alternatives it rejected.

*Context.* Q1's answer fixes the mechanism (the C# service starts a container with a house
configuration) but not its shape, and every detail of that shape is a thing the C# side has to
code against. Four sub-decisions are entangled and should be taken together: **how input
arrives** (a mounted volume with a file at a fixed path, an argument, or stdin), **how output
leaves** (files in a mounted result directory, or one JSON document on stdout), **what the exit
status means** (R13.2 needs success, refusal, validation error and crash to be four
distinguishable outcomes), and **what the unit of work is** — one package per container, or one
container computing several. The last is not cosmetic: a caller wanting many packages priced
pays one container occupancy each, and HiSim's import time is paid once per container start.

*Options.* (a) **Files in, files out**: a mounted input directory holding the house
configuration, a mounted output directory receiving the result document, the translation report,
the parametrised energy-system file and the realized record. Consequence: the natural fit for what
HiSim already writes, and the emitted YAML and record survive as debuggable artifacts for free;
the C# side manages two mounts per call. (b) **stdin/stdout JSON**: one document in, one
document out, nothing mounted. Consequence: the simplest thing to call from C#, and every
artifact of R7 and UC5 has to be embedded in the response or thrown away. (c) **A hybrid** —
configuration on stdin, results into a mounted directory, a summary document on stdout.
Consequence: covers both, at the cost of two mechanisms to document.

*Recommendation.* (a), with the unit of work being one *calculation* (one inventory, one
package, one parameters file). It matches what HiSim already produces, keeps the R7 report and the R10
record as first-class files rather than blobs, and leaves batching to the C# side, which is
where the scheduling decisions already live.

*Answer.* `[decided 2026-08-27, owner]` **(a) files in, files out**, with the unit of work one
calculation — but the container is **reused** across calculations rather than started per
calculation, which is what pays off the start-up cost that made the per-calculation model
expensive. The C# side additionally provides a function to delete a result directory outright
and a working-tree cleanliness test after each calculation (R13.4, AC11.5).

*What the answer costs.* Reuse largely settles the throughput worry on its own — the import
cost is paid once per container, not once per calculation — but it converts a cheap guarantee
into an expensive one. With a fresh
process per calculation, R13.1's isolation was free; with a reused process it must be built,
and C13 records that HiSim does not have it today — a process-wide `SingletonSimRepository`
written from 29 call sites that no production path resets, and a `ResultPathProviderSingleton`
that freezes a timestamp at first construction. This is now the main technical risk in §8.1
and the reason AC11.1 is written as a sequence of *differing* calculations.

*Blocks.* Closed. R13, R13.1, R13.2, R13.3, R13.4, R14, AC11–AC11.5 are now stated; Q10
carries the one sub-decision that remains.

---

**Q10 — Does a reused container run one calculation at a time, or several concurrently?** · `[answered 2026-08-27]` — **one calculation at a time.** Entry kept for the alternatives it rejected.

*Context.* Q9 settled reuse but not concurrency, and the two isolation problems are not the same
size. `SingletonSimRepository` is a process-wide singleton written from 29 call sites (C13); its
metaclass takes a lock only on first construction, and the repository's own `set_entry` /
`get_entry` are plain dict operations with no locking. Sequential reuse therefore needs a reset
between calculations — one mechanism, called at one place. Concurrent reuse needs the singleton
to stop being process-wide at all, because two simulations sharing one dict is a data race, not
a staleness problem. The epic already carries "runtime half of `SingletonSimRepository` … needs
its own redesign, probably proper wiring" in its parking lot, with no trigger date.

*Options.* (a) **One calculation at a time per container**, with the C# side running several
containers for parallelism. Consequence: isolation is a reset between calculations, which the
existing `reset()` methods almost provide; parallelism costs container memory rather than
engineering. (b) **Several calculations concurrently in one container**, in threads.
Consequence: pulls the parking-lot `SingletonSimRepository` redesign into this work as a
blocker, and every module-level cache in C13's unsurveyed list becomes a race. (c) **Several
concurrently, in subprocesses inside the container.** Consequence: isolation is free again
(separate address spaces) and the start-up cost comes back, though a forked worker pool pays the
Python import once.

*Recommendation.* (a) first, (c) if throughput demands it. (b) turns a bounded reset problem
into an open-ended thread-safety audit across 11 component modules for a benefit the C# side can
get by running more containers.

*Answer.* `[decided 2026-08-27, owner]` **(a)** — one calculation at a time per container;
parallelism comes from running more containers. Consequences: R13.1 is satisfied by a reset
between calculations rather than by making shared state concurrent; the parking-lot
`SingletonSimRepository` redesign is **not** pulled into this work; and the cost of parallelism
moves to container memory, which is what R13.3 has to bound.

*Blocks.* Closed.


---

### Q2's caching strategy — the half that is not HiSim's

`requirements.md` keeps the half of Q2 that is a requirement on HiSim: a finished result carries
a version stamp covering the translator, the base files, the component models and the cost
database (R14, A15). What the *service* then does with that stamp — whether it enters the
content hash, namespaces the result store, or weakens the `immutable` claim — is the service's
design decision, and the analysis is kept here rather than posed as an open question there.

**Q2 — Does the calculation version enter the content hash, namespace the results, or weaken the immutability claim?** · DECIDED · was blocking R14, A15, AC10

*Context.* The contract states a finished result "never changes and may be cached indefinitely
(`Cache-Control: public, max-age=31536000, immutable`)" because the input is content-addressed.
But the result is a function of the input *and* the model: the component physics, the base
files, the cost database, the weather file. Any of those changing changes the result under an
unchanged hash. The contract has no version field anywhere.

*Options.* (a) **Version in the hash** — `hi_id` and `pkg_id` stay input-only, but the result
URL resolves per version. Consequence: a model update invalidates every cached result, which is
correct and expensive. (b) **Version namespaces the result store**, with the URL unchanged and
an internal generation counter. Consequence: cheap for the frontend, but a client that cached a
year-old response still shows stale numbers. (c) **Weaken `immutable`** to a short max-age plus
an ETag. Consequence: correctness at the cost of the contract's central caching claim.

*Decision (2026-08-27, agreed in review).* (a), for correctness, with the version exposed in the
finished result so a frontend can display it. The expense is real but a wrong renovation cost
cached for a year is worse. Review concurred: a model change must recompute; (b) causes stale
cached results with no way for a client to tell; (c) is (a) with fewer guarantees and no
advantage.

*Settles.* R14, A15, AC10 proceed on this basis.


---

## Part 2 — Withdrawn requirements and questions

The scope of HiSim's side narrowed over the course of 2026-08-27 from "the backend" to "the
calculation". Four items went with it. None of them was wrong; all of them turned out to belong
to somebody else.

The pattern is worth naming, because it will recur. Each of these was written because the API
*contract* has a resource for it — a `packageset` endpoint, a `fast-estimate` endpoint, a
`report.pdf` endpoint — and the contract was read as a specification of what HiSim owes. It is
not. It is a specification of what the *service* owes, and the service is mostly not HiSim. Of
seventeen operations in the contract, HiSim answers one.

---

**R6 — Enumerate a package set.** `[withdrawn 2026-08-27, owner]`
~~Given a dwelling configuration, the layer must produce the set of packages the product offers
for it.~~ Package generation is outside HiSim (§5 non-goals). A package arrives already decided;
the layer calculates the one it is given. ID stability still matters and did not go with this
requirement — it lives on in A6.5, because two clients specifying the same renovation must reach
the same `pkg_id` however the package was arrived at.

**R12 — Two calculation tiers.** `[withdrawn 2026-08-27, owner]`
~~The layer must offer a fast tier and a detailed tier.~~ The `fast-estimate` is produced outside
HiSim. HiSim performs one kind of calculation — a simulation — and needs no notion of a tier at
all. What varies between runs is the parameters file the caller sends (A18), which is a
parameter of a calculation and not a mode of one.

**Q5 — What generates the packages, and who owns the rule?** · `[withdrawn 2026-08-27, owner]`

Package generation is outside HiSim (§5 non-goals, R6 withdrawn), so this is not a question this
document answers. Kept as a marker because the reasoning it carried has to land somewhere: the
cost of calculating a large package set is real — one calculation per package, one container
occupancy each (Q9, Q10) — and it now sits entirely with whoever schedules the calculations. If
that party wants a cheaper package set, the lever is the parameters file it sends (A18), not
anything HiSim decides.

---

**Q6 — What is the `fast-estimate` tier?** · `[withdrawn 2026-08-27, owner]`

The fast tier is produced outside HiSim, so this is not a question this document answers. It was
the one place a second physics implementation might have entered the repository — a degree-day
calculation or a fitted surrogate beside the simulation — and that risk goes with it. HiSim has
one method: run the system. What a caller varies is the parameters file (A18).


### The PDF report

`report.pdf` is rendered outside HiSim, from the result payload HiSim returns. Recorded in
`requirements.md` as a non-goal rather than dropped silently, because of a trap: HiSim's own
`GENERATE_PDF_REPORT` post-processing option is a *different* artifact — a developer's view of a
run, reachable through the parameters file — and someone who finds it in `PostProcessingOptions`
could easily conclude the requirement is already met.

---

## Part 3 — Contract findings that are not HiSim's problem

These are defects in `roadmap/renovisor/openapi.yaml` v0.3.0-draft. They are real, and each was
found by trying to build something against the contract. None of them blocks the pipeline
`requirements.md` specifies, which is why they are not requirements there — but the RenoVisor
frontend and the C# service teams should see them, and this file is where they are written down
until somebody owns them.

---

**A17 — Reconcile per-user endpoints with content addressing.** `[withdrawn 2026-08-27, owner]`
~~`GET /homeinventories` is per-user while `security: []` makes it anonymous and the store is
content-addressed.~~ A real contradiction in the draft, but between the frontend and the C#
service; nothing about it reaches HiSim, which owns no store and no auth (C11). Dropped from
this document's scope. Left in place per the decision-log rule so the reviewer who reads §8.2
for a second time can see it was considered and why it went. Note that with A17 gone, **every
remaining §8.2 item is a payload change the backend owns**, and C10's "the envelope needs
frontend sign-off" no longer binds any requirement here.

The finding itself, as originally written:

**A17 — Reconcile per-user endpoints with content addressing.** `[proposed; contract internal defect]`
`GET /homeinventories` is summarised as "list all home inventories **for the current user**" and
`DELETE /homeinventories/{hash}` can return `403 Forbidden` ("valid token, wrong user"), yet
`security: []` makes both anonymous and the store is content-addressed and deduplicated, so one
inventory may belong to several users. Deleting it for one user would remove another's. The
contract must state whether the store is per-content or per-user, and what `DELETE /users/me`
cascades to.

---

**A22 — `Schedule` must reference measures by id.** `[proposed; contract Schedule schema]`
`Schedule.phase_order` is `[string]`, and measures have no identity, so the strings name nothing.
No conforming client could write a valid schedule against the draft. With A6.5's `measure_id`,
`Schedule` becomes phases that list measure ids, and every measure of a package appears in
exactly one phase.

*Why it is here.* A package's schedule says *when* its measures happen. The pipeline runs one
simulation of one post-measure state, so phasing never reaches it; it is a cost and presentation
concern. The defect is real — no conforming client can write a valid schedule against the draft
— but fixing it changes nothing HiSim does. Note that the fix is already available: A6.5 gives
measures a stable `measure_id` for exactly this kind of reference.

---

**A23 — Ventilation has a condition but no existence.** `[proposed; measured 2026-08-27]`
`condition_assessment.energy_systems.ventilation_system` lets a user report the condition of a
ventilation system, but `energy_system_config` has no ventilation block, so the inventory cannot
state that one exists or what it is. A ventilation measure has nowhere to write. Either
`HomeInventoryInput` gains the block, or ventilation leaves the contract; it cannot stay as
drafted. Note that HiSim has no infiltration or MVHR parameter either, so adding the block buys
reporting and pricing, not simulation, until that changes.

*Why it is here.* Ventilation has no HiSim consumer in either direction: there is no
infiltration or MVHR parameter in `BuildingConfig`, so adding the missing inventory block would
buy reporting and pricing, not simulation. The contract's internal inconsistency stands on its
own merits and someone should resolve it; the pipeline is indifferent either way.

---

**A19 — Note the enum coupling to HiSim internals.** `[proposed; §4.4]`
`heating_system.system`, `heat_distribution_system` and the `envelope_details` field names are
HiSim's own identifiers. The contract should say so, so that neither team renames one by
accident. The epic's decision EQ1 freezes such names at P5 acceptance; before then they are
free to change.

*Why it is here.* An observation rather than a requirement — nothing breaks today because of
it. It matters as context for Q3 (whether the contract's field set is re-anchored on
energy-system names or frozen as a façade), which `requirements.md` keeps open, and the fuller
evidence for the coupling is in §4.4 below.

---

**A20 — State the Irish scope.** `[proposed; 0 IE subsidy catalogues, 0 IE tariffs, 0 IE MFH archetypes]`
`GrantScheme` is Ireland-first by its own description, but nothing in the contract says which
country codes are actually supported, and `tabula_building_type` offers `MFH`, which has no
Irish archetype. The contract must carry the supported set explicitly.

*Why it is here.* HiSim refuses what it cannot simulate regardless of what the contract
advertises (R11), so the pipeline is safe without this. Stating the supported set is contract
hygiene that saves users from filling in a form that will be refused — worth doing, not worth
blocking on.

---

### 4.4 The contract was drafted against the layer being deleted

This is the single most consequential observation of the survey and it is not visible from the
contract alone. `HomeInventoryInput.building_config.envelope_details` is field-for-field
`ArcheTypeConfig`'s per-element U-value block, down to the names
(`floor_u_value_in_watt_per_m2_per_kelvin`, `door_area_in_m2`).
`energy_system_config.heating_system.system` enumerates exactly the ten members of
`hisim.loadtypes.HeatingSystems`, in HiSim's own spelling (`GasSolarThermal`,
`WoodChipHeating`). `heat_distribution_system` carries the literal
`lt.HeatDistributionSystemType` strings, spaces included (`"Conventional Radiator"`).

Two consequences follow. First, the contract's "provisional payload" half is in fact tightly
coupled to HiSim internals, which is good for fidelity and bad for stability: renaming a HiSim
enum becomes a public API break. Second, `ArcheTypeConfig` and `ModularHouseholdConfig` are
scheduled for deletion (epic EAC5), so the contract's anchor disappears. Whether the field set
is re-anchored on the energy-system file's own names or frozen as a translator-owned façade is
an open question (Q3).

*Why it is here.* This is the single most consequential observation of the original survey and
it is not visible from the contract alone, so it is preserved in full. `requirements.md` keeps a
two-sentence version, because what it actually drives is one open question (Q3): whether the
contract's field set tracks HiSim's names or is frozen as a façade the translator maps onto.

---

## Appendix — how the scope narrowed, for the record

The requirements document was first drafted against the whole of the API contract, on the
assumption that "the RenoVisor backend" and "HiSim" named the same thing. Five owner decisions
on 2026-08-27 established that they do not:

| What was assumed | What is actually true |
|---|---|
| HiSim serves the API | A C# service in another repository serves it; HiSim is a library in a container (Q1) |
| HiSim is called in-process | It is called with files, in a container reused across calculations (Q9, Q10) |
| HiSim enumerates package sets | Package generation happens outside HiSim (R6 withdrawn) |
| HiSim offers a fast and a detailed tier | The fast estimate happens outside HiSim; HiSim has one kind of calculation (R12 withdrawn) |
| HiSim renders the PDF report | The report is rendered outside HiSim from the result payload |

Each narrowing removed work, and two of them removed risk that was not obvious at the time:

- **Withdrawing the fast tier** removed the one place a second physics implementation might have
  entered the repository. A degree-day calculation or a fitted surrogate beside the simulation
  would have meant two models producing numbers a user compares side by side, with a validity
  domain nobody had specified.
- **Deciding one calculation at a time per container** (Q10) kept the `SingletonSimRepository`
  redesign in the epic's parking lot. Concurrency inside one container would have pulled a
  thread-safety audit across 11 component modules into this work as a blocker.

One narrowing *added* a risk, and it is now the main technical risk in the requirements
document: reusing a container (Q9) means HiSim must survive being run repeatedly in one process,
which today it does not (C13).
