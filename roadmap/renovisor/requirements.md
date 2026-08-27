# RenoVisor calculation library — requirements

**Status:** draft
**Date:** 2026-08-27
**Author(s):** assistant (all `[proposed]` items, evidence survey) · Noah Pflugradt (owner; all `[given]` and `[decided]` items)
**Reviewers:** HiSim core team · RenoVisor frontend team (§8.2 only)
**Supersedes / related:** `hisim/renovisor/spec.md` (the v1 translator spec — superseded by
§8.1, kept until the replacement is accepted) · `roadmap/renovisor/openapi.yaml` (RenoVisor API
v0.3.0-draft) · `roadmap/declarative_energy_systems/epic.md` and its `plan.md` P5 row (this work
*is* the RenoVisor half of P5) · `roadmap/cost-spec-v2.md` (the cost engine)
**Companions:** `roadmap/renovisor/field_inventory.md` (the counted survey) ·
`roadmap/renovisor/mockups/heat_pump_household.energy_system.yaml` (a base file after
parametrisation) · `roadmap/renovisor/mockups/measures.schema.yaml` (the proposed `Measure`) ·
`roadmap/renovisor/requirements.discussion.md` (closed deliberations, withdrawn items, and
contract findings that are not HiSim's problem)

**Tags:** feature, behavior-change, migration, compatibility
**Keywords:** RenoVisor, calculation library, energy-system file, parametrisation, container,
determinism, KPI derivation, cost engine, TABULA, Ireland

---

## 1. Abstract

RenoVisor is a renovation-advice service. Its backend is a C# application in a separate
repository; its calculations are performed by HiSim, delivered as a library in a container that
the service calls with a file. This document specifies what that library must do. HiSim's
existing translator (`hisim/renovisor/`) was written against an older contract, produces a
configuration object that a parallel migration is deleting, and explicitly declines to derive any
result payload — so it is replaced rather than adapted. The document also states, in the opposite
direction, what the API contract must provide before the library can be built against it, because
a survey found that of the contract's 79 dwelling fields, 35 have no simulation consumer at all,
and that its measure model cannot express the renovations it is for.

## 2. Keywords and tags

```
Tags: feature, behavior-change, migration, compatibility
Keywords: RenoVisor, calculation library, container interface, energy-system file,
          parametrisation, determinism, KPI derivation, cost engine, TABULA, Ireland
```

## 3. Executive summary

**What HiSim owes, in one sentence.**

> Take a configuration file and a parameters file → select a base energy-system file →
> parametrise it → run it → return KPIs, costs, a translation report, the parametrised file and
> the realized record → or write `errors.json` saying why not.

Everything in §8.1 is one step of that sentence. Everything in §8.2 is something the API contract
must supply before a step of it can work.

**Problem.** No part of that pipeline exists today. The current translator speaks a different
contract, emits a `ModularHouseholdConfig` for one of ten Python setups — an object the
declarative-energy-systems epic deletes at P5 — and states in its own specification that deriving
result payloads is out of scope. Counted against HiSim as it stands: of the contract's 79 dwelling
fields, 24 are directly consumable, 3 only approximately, 15 are blocked on component work that
has not happened, 2 are wiring rather than configuration, and 35 have no simulation consumer at
all. Of 13 KPI fields, 3 are computable. Of 9 cost ranges, 7 have an engine and 2 of those lack
Irish data. Five of the ten heating systems the contract offers cannot be expressed as an
energy-system file at all yet.

**Required.** (a) A calculation library implementing the pipeline above, packaged as a container
image the C# service reuses across calculations. (b) A revised API contract in which every field
the pipeline reads has a consumer, the measure model can express a renovation, and a cost `Range`
means something definite.

**Affected.** `hisim/renovisor/` (rewritten), `roadmap/renovisor/openapi.yaml` (amended), the
declarative-energy-systems P4 sweep (≈13 modules become blocking), `hisim/economics` (Irish tariff
and subsidy data).

**Cost of inaction.** A contract signed off with 35 fields the simulator cannot read and ten result
fields nothing computes, surfacing as silent placeholder numbers in a renovation recommendation a
homeowner acts on.

## 4. Context and current situation

### 4.1 The pipeline, and the state of each step

| Step | What it needs | State today |
|---|---|---|
| Read a configuration file | An agreed dwelling + package format | Contract exists but is unusable in parts: `Measure` cannot express a renovation (A6); 35 of 79 fields have no simulation consumer (A1, A2) |
| Read a parameters file | Period, resolution, post-processing | **Exists.** `energy_system/executor.py:SimulationParametersReader` reads `*.simulation.yaml` / `.json` (A18) |
| Select a base energy-system file | One reviewed file per supported system | **None written.** Five of ten heating systems are not yet expressible (C4); how many files are needed is open (Q4) |
| Parametrise it | Overrides + group flags onto the base | Format exists and is accepted (P2); the writing layer does not |
| Run it | The executor | **Exists.** `energy_system/executor.py:run_energy_system` |
| Return KPIs | 13 fields | 3 computable; 10 mocked for the MVP (A12) |
| Return costs | 9 ranges | 7 have an engine; `grant_euro` and `energy_costs_euro` lack Irish data (C8, Q8) |
| Return the translation report | Per-field account of the run | Concept exists in the v1 spec (§6); nothing produces it for the new contract |
| Return the parametrised file + realized record | Re-executable artifacts | **Exists.** `energy_system/record.py` writes both |
| Write `errors.json` | Structured failure reporting | **Nothing.** No refusal concept and no machine-readable error surface (R11, R13.2) |

Roughly half the pipeline is already built and belongs to the declarative-energy-systems work; the
half this document is about is the two ends — reading a configuration and returning a result.

### 4.2 Counted inventory

Full survey in `roadmap/renovisor/field_inventory.md`. Headline numbers:

| Measure | Count |
|---|---|
| `HomeInventoryInput` leaf fields | 79 |
| …directly consumable by a HiSim config field | 24 |
| …consumable only approximately (occupancy composition → 66 LPG households) | 3 |
| …blocked on the P4 component sweep | 15 |
| …structural (wiring, not configuration) | 2 |
| …with no HiSim consumer, present or planned | 8 |
| …non-simulation (cost / scheduling / grants) | 27 |
| `Kpis` leaf fields | 13 (**3** computable today) |
| `Costs` `Range` fields | 9 (**7** with an engine, 2 of them blocked on Irish data, 1 with no model) |
| `HeatingSystems` values the contract offers | 10 (**5** expressible as an energy-system file today) |
| Component classes an energy-system file can use today | 8 across 6 modules |
| Component classes the base files need | ≈26 across ≈19 modules |
| Contract operations answered by a HiSim calculation | **1 of 17** (`detailed-simulation`) |
| Irish subsidy catalogues in `hisim/subsidy_catalog/` | 0 (`AT.json`, `DE.json` only) |
| Irish tariff files in `hisim/cost_database/tariffs/` | 0 (`DE_DYNAMIC_SYNTHETIC_2024.json` only) |

### 4.3 What is genuinely new

Two things the v1 translator never did, and which are not refinements of it:

1. **Result derivation.** v1 uploaded HiSim's result *files* and left interpretation to the
   receiving server (`hisim/renovisor/spec.md` §1: "explicitly out of scope for v1"). The new
   contract expects `kpis` and `costs` — 13 + 27 numbers the library must produce.
2. **Refusal as an outcome.** A *valid* configuration may be un-simulable: no Irish TABULA
   archetype for a multi-family building, a solar-thermal wiring combination with no base file. v1
   has exit codes and no way to say this.

A third property is new but is not a capability: **determinism**. The C# service caches finished
results indefinitely, so the same inputs must produce the same result on any machine, at any time,
in any container (R10, R14).

### 4.4 Stakeholders

**Existing.** The **RenoVisor backend service** — a C# application in a separate repository, and
the library's only caller. It owns every HTTP concern of the contract and invokes HiSim through a
reused container: a configuration file in, results as files out (Q1, Q9, Q10 decided 2026-08-27).
It also owns deleting a result directory between calculations and the cleanliness check that
proves nothing was left behind (R13.4). The **RenoVisor frontend** co-owns the contract and is the
reviewer for §8.2. The **declarative-energy-systems epic** — this is its P5 RenoVisor track. The
**building sizer and HPC harness**, which share the base-file mechanism.

**Hypothetical, and therefore not a constraint.** A webtool, other countries beyond Ireland.

### 4.5 Current behaviour versus required

| | |
|---|---|
| **Current** | A CLI translates a v1 request into a `ModularHouseholdConfig`, picks one of ten Python setups, runs it in-process, and POSTs matched result files plus a mapping report to a URL. |
| **Required** | A reused container is handed a configuration file and a parameters file; it selects one of the checked-in energy-system files, parametrises it, runs it, and writes back the result payload, the translation report, the parametrised file and the realized record — or `errors.json`. |
| **Explicitly not** | Generating an energy system from the request. The systems are hand-authored and reviewed; the library chooses among them and fills in values (R1, R4, AC3). |

## 5. Goals and non-goals

**Goals**

- **G1** The pipeline of §3 runs end to end, driven by a caller that imports no Python.
- **G2** Every simulated system is one a person wrote and reviewed; the library selects and
  parametrises, never authors.
- **G3** Identical inputs produce identical results, so the service's caching is sound.
- **G4** Every field of a configuration has a stated fate, and every returned number is traceable
  to a simulation, a cost-engine output, or a declared mock.
- **G5** A configuration that cannot be simulated is refused with a machine-readable reason, never
  answered with a plausible wrong number.

**Non-Goals**

- **The HTTP service**: routing, auth, storage, result caching, job coalescing, content hashing,
  and the scheduling of calculations. All of it belongs to the C# service. HiSim is invoked, never
  polled.
- **Package generation.** `[given 2026-08-27, owner]` Deciding which packages exist for a
  dwelling, enumerating or ranking them. A package arrives already decided.
- **The `fast-estimate` tier.** `[given 2026-08-27, owner]` HiSim performs one kind of
  calculation. What varies between runs is the parameters file (A18).
- **The PDF report.** `[given 2026-08-27, owner]` Rendered outside HiSim from the result payload.
  Note that HiSim's own `GENERATE_PDF_REPORT` post-processing option is a different artifact — a
  developer's view of a run — and not the contract's report.
- Countries other than Ireland. The mechanism must not preclude them; only IE is verified.
- Reworking the KPI computation or the cost engine beyond what the payloads need.
- The building sizer and HPC harness tracks of P5, though they share the base files.

## 6. Use cases and mockups

Every case is at HiSim's boundary: a file arrives, a calculation happens, files come back. There is
no HTTP on this side — no `POST`, no `202`, no polling, no cache. Whether the dwelling and the
package arrive as one document or two is not yet settled; the cases say "a configuration file" and
stay neutral.

### UC1 — One calculation

*Input.* A configuration file describing one dwelling and one package, plus a parameters file.
*Expected.* The base energy-system file for the dwelling's heating system is selected and
parametrised; HiSim runs it; the output location receives the result payload (`kpis`, `costs`), the
translation report, the parametrised energy-system file and the realized record. Nothing is
uploaded and nothing is fetched.

### UC2 — What parametrisation produces

*Input.* The UC1 configuration, for an Irish 1988 detached house with a heat pump, PV and a
battery.
*Expected.* The parametrised file is the mockup
`roadmap/renovisor/mockups/heat_pump_household.energy_system.yaml` — **the external representation
this document is chiefly about**. Its footer (M1–M5) states what it pins down: only overrides and
group flags are written; ten base files are needed and the EMS-less variants double that to
twenty; every override traces to one named contract field.

### UC3 — A configuration that cannot be simulated

*Input.* A configuration with `country_code: IE` and `tabula_building_type: MFH`.
*Expected.* `errors.json` carrying a refusal, distinguishable from a crash, naming the offending
field and a machine-readable reason — the Irish TABULA table has 48 `SFH`, 51 `TH` and 19 `AB` rows
and **no** `MFH` row. Decided before any simulation starts. Not a traceback, and not a silently
substituted archetype.

### UC4 — A field with no consumer

*Input.* A configuration stating `battery_storage.state_of_health_in_percent: 90`.
*Expected.* The simulation ignores it (HiSim has no capacity-fade model) and the translation report
says so in as many words. Under R7 no field is silently dropped.

### UC5 — Re-running a realized record

*Input.* The realized record of a finished calculation, re-executed.
*Expected.* Byte-identical results. This is the epic's identity test applied to RenoVisor's output,
and it is what lets the service treat a finished result as final.

### UC6 — Many calculations in one container

*Input.* A sequence of *differing* configuration files handed to the same container, one after
another (Q9, Q10).
*Expected.* Each produces exactly what it would have produced as the first calculation of a fresh
container, and after each output location is deleted, nothing of it remains on disk. This is the
case C13 says HiSim does not survive today.

## 7. Why it matters

The service caches finished results indefinitely, on the stated grounds that "a finished result
never changes". That is only true if the pipeline behind it is deterministic and versioned, and
today neither holds: HiSim cannot currently run twice in one process without carrying state across
(C13), and nothing stamps a result with the model that produced it. Meanwhile 35 of 79 collected
fields reach no simulation, and ten of thirteen KPI fields will be constants for the MVP — which is
defensible only if a consumer can tell which numbers are real. The failure mode throughout is the
same and it is quiet: a plausible number, cached for a year, that no simulation stands behind.

## 8. Requirements

**R** requirements are on the calculation library, ordered by the pipeline step they serve. **A**
requirements are on the API contract and need the frontend team's agreement. IDs are stable;
withdrawn items keep their number and are recorded in `requirements.discussion.md`.

### 8.1 The calculation library (R)

#### Step 1 — Select a base energy-system file

**R1 — Run from a parametrised energy-system file.** `[given; epic EAC5, plan P5]`
Each calculation must run a `*.energy_system.yaml` obtained by **selecting one of the checked-in
files and parametrising it**, together with a parameters file. The library must not construct
`ModularHouseholdConfig`, `ArcheTypeConfig` or `EnergySystemConfig`, and must not author an energy
system from the request: the set of simulable systems is fixed, hand-written and reviewed, and a
request chooses among them rather than describing a new one.

**R2 — A base file per supported system.** `[given; plan P5 "base file per heating system"]`
A checked-in base energy-system file must exist for each supported `heating_system.system` value. A
base file is authored and reviewed as a system, not generated.

**R3 — Selection is a lookup, not a computation.** `[proposed]`
Which base file a configuration uses must be a total function of a stated set of fields, so the
answer is inspectable without running anything. That set is `heating_system.system` plus whatever
Q4 resolves.

#### Step 2 — Parametrise it

**R4 — Parametrising writes only overrides and group flags.** `[proposed; mockup M1]`
Beyond the base file's content, a parametrised file may differ only by (a) `config` field
overrides, (b) `constructor` arguments, and (c) `groups.<name>.enabled` flags. The library must
never author `inputs`, `sizing_sources`, or a component entry. Wiring and sizing sources are the
base file's reviewed content; a library that writes them is a second, untested system description.
This is what gives R1's "select, don't generate" a testable edge (AC3).

**R5 — Apply the package's measures before parametrising.** `[proposed; extends spec.md §3]`
A package's measures must be applied to a copy of the dwelling configuration, and the result is what
the parametriser reads. Envelope measures must reach the per-element `BuildingConfig` U-value and
area fields — not, as in v1, be folded into the TABULA refurbishment variant — because those ten
fields exist and are directly consumable (`field_inventory.md` rows 13–22).

#### Step 3 — Run it

**R10 — Determinism.** `[proposed; from the service's caching]`
Two runs of the same configuration and parameters must produce byte-identical parametrised files,
byte-identical realized records and equal result payloads, on any machine. Nothing that varies per
request — a timestamp, a job id, a machine name — may enter the emitted files.

**R14 — Version stamping.** `[proposed]`
Every finished result must carry the version of the calculation that produced it, covering the
library, the base files, the component models and the cost database. The container image digest is
a candidate, since it covers all four by construction.

#### Step 4 — Return the result

**R7 — Report the fate of every field.** `[given; spec.md §6, strengthened]`
Every leaf field present in a configuration must appear exactly once in a machine-readable
translation report with a status of `used`, `approximated`, `defaulted`, `ignored` or
`non_simulation`. The report is written beside the results it describes, so a caller holding a
result also holds the account of how faithfully it was simulated. This is what keeps §4.2's counts
honest as HiSim's fidelity grows.

**R8 — Derive the KPI payload, and label what is not derived.** `[given; amended 2026-08-27, owner]`
The library must compute every `Kpis` field it can from the finished run, stating for each the
boundary and period it was computed over. For the ten fields with no HiSim source the MVP serves
constants (A12); each such value must be **labelled as mocked in the result itself**, so no
consumer can mistake a placeholder for a simulated number. A field that is neither computed nor
deliberately mocked is absent, never zero.

**R9 — Derive the cost payload.** `[given; contract Costs]`
The library must compute every `Costs` field the amended contract keeps, using `hisim/economics`,
and must emit the three values of a `Range` from one coherent evaluation slot (A9).

**R16 — Return the parametrised file and the realized record.** `[proposed; from executor.py:write_records]`
Both must be written to the output location for every calculation that runs, before the first
timestep. They are what makes a result auditable after the fact and re-executable (UC5), and
`energy_system.write_records` already produces them.

#### Step 5 — Or fail

**R11 — Refusal is distinct from a crash and from a validation error.** `[given; contract CalculationState.refused]`
The library must distinguish "this input is malformed", "this input is well-formed but not
simulable", and "the simulation failed". It must decide refusals before starting a simulation
wherever the condition allows it.

**R13.2 — Every failure is written to `errors.json`.** `[given 2026-08-27, owner]`
A calculation that does not succeed must write a structured `errors.json` into its output location,
listing every error with a machine-readable reason, so the caller reads what went wrong from a file
rather than parsing a log or a traceback.
- **R13.2.1 — Written on every unsuccessful outcome, at a fixed path.** An outcome with neither a
  result nor an `errors.json` is itself a defect: a container that died without writing one is
  indistinguishable from one that hung.
- **R13.2.2 — Reasons come from a closed, published catalogue.** A reason code the service has
  never seen cannot be rendered to a user or routed to a retry policy.
- **R13.2.3 — An error names what it is about.** The offending field path for a validation error or
  a refusal; the component or stage for a crash.

An unexpected exception is a legitimate entry: a generic code, its message, and enough traceback for
a HiSim developer. The C# side never reads that part, but the alternative is losing it.

#### Cross-cutting — the container interface

**R13 — A file-based container interface is the whole interface.** `[given 2026-08-27, owner]`
The only caller is a C# service that supplies files and collects files, in a container **reused**
across calculations. The interface is a process contract, not a Python API, and must be specified as
one: where the configuration is read from, what is written where, and what signals success, refusal
or failure. It must be usable by a caller that cannot import Python and cannot inspect a traceback.

**R13.1 — A reused container must not carry state between calculations.** `[given 2026-08-27, owner]`
The *n*-th calculation in a container's life must produce exactly what it would have produced as the
first. Because the process outlives the calculation, this is a positive requirement on the
implementation — state must be actively reset between calculations — and C13 records the specific
hazards it has to defeat. Because calculations never overlap (R13.5), a reset at a single point in
the lifecycle satisfies it; nothing has to become concurrent.

**R13.3 — Reuse must be bounded and self-limiting.** `[proposed]`
A long-lived container accumulates result directories, memory and cached data. The library must
either bound that growth or state the limit at which the caller should recycle the container.

**R13.4 — A calculation writes only where it declared it would.** `[given 2026-08-27, owner]`
Everything a calculation produces must land inside its declared output location, so deleting that
location returns the container to its pre-calculation state. Nothing may be written beside the
package, into the working tree, or into a shared temporary area. The owner's plan is a
`git status --porcelain`-style test on the C# side, which is well aimed:
`ResultPathProviderSingleton`'s default `base_path` is
`Path(__file__).resolve().parent.parent / "results"` (`hisim/result_path_provider.py:105`), i.e.
**inside the repository**, so a working-tree check catches precisely the residue that is easiest to
write by accident.

R13.4 catches *filesystem* residue; it cannot see the in-process state of C13, which leaves no file
behind. Neither control substitutes for the other — R13.4 is verified by AC11.5, R13.1 by the reuse
identity test of AC11.1.

**R13.5 — Single occupancy is a stated precondition, not an accident.** `[given 2026-08-27, owner]`
A container serves one calculation at a time. The library may therefore assume it is the only
calculation in its process, and correspondingly must **state in its own documentation that it is not
safe to run two concurrently** — so that neither the caller nor a later HiSim change mistakes the
absence of a crash for thread-safety. Parallelism comes from more containers, which makes
per-container memory the scaling limit R13.3 must bound.

#### Cross-cutting — compatibility

**R15 — Nothing else in HiSim regresses.** `[given; epic C1/E7]`
The golden suites must pass unchanged. The v1 translator and its contract may be deleted only once
the replacement is accepted.

*(R6 and R12, with their questions Q5 and Q6, were withdrawn on 2026-08-27 — package generation and
the fast-estimate tier are outside HiSim. All four are in `requirements.discussion.md` Part 2.)*

### 8.2 What the contract must supply (A)

Each item blocks a step of the pipeline. Contract defects that do **not** block a step are recorded
in `requirements.discussion.md` Part 3 rather than here.

#### For step 1 — reading a configuration

**A1 — Every field needs a declared consumer class.** `[proposed; field_inventory §1]`
Each `HomeInventoryInput` field must be tagged as *simulation input*, *cost/scheduling input*, or
*informational*. Today 27 fields are non-simulation and 8 have no consumer at all, with nothing
distinguishing them from the 24 that drive the physics — which is what R7's report has to state per
request.

**A2 — Remove or reclassify the 8 fields with no consumer.** `[proposed; field_inventory rows 2, 3, 41, 43, 45, 52, 56, 58]`
`location.region`, `location.eircode_or_postcode`,
`photovoltaics.remaining_performance_in_percent`, `battery_storage.power_in_watt`,
`battery_storage.state_of_health_in_percent`, `electric_vehicles[].model`,
`electric_vehicles[].charging_location`, `fossil_vehicles[].model`. Each is removed, or kept with an
explicit statement that it affects no calculation.

**A3 — State that occupancy composition is approximated.** `[proposed; 66 LPG households]`
`residents_count` × `residents_type` × `residents_employment_status` spans far more combinations
than the 66 catalogue households HiSim can simulate. Either the contract offers a closed enum of
supported compositions, or it states that the composition is matched to the nearest catalogue
household and the match is reported.

**A5 — Reconcile mileage with `travel_route_set`.** `[proposed; field_inventory §2]`
v0.3 dropped v1's `kmPerYear` but kept per-km consumption, so a vehicle's annual energy is driven by
an *occupancy* field. Either restore a per-vehicle mileage field with a stated consumer, or state
that mileage comes from `travel_route_set`.

**A18 — Simulation parameters arrive from the caller.** `[decided 2026-08-27, owner]`
The service supplies a `simulationparameters.json` alongside the configuration; period, resolution
and post-processing options are its choice, not a constant of the image. HiSim already reads this
file — `energy_system/executor.py:SimulationParametersReader` accepts `*.simulation.yaml` or
`*.simulation.json` — so what is open is only whether the RenoVisor field set matches what that
reader expects. The parameters are an input to the calculation and therefore to R10 and R14: the
same configuration under different parameters is a different result.

#### For step 2 — applying measures and parametrising

**A6 — Replace `Measure` outright.** `[given 2026-08-27, owner: "needs to be fully revised"]`
The drafted `Measure` is one flat object —
`{type: string, material_id, subtype, thickness_mm, capacity_kw, alternatives}` — for a domain
containing at least five unrelated families: envelope work, heat supply, thermal storage,
electricity, vehicles. Every field is optional and most are meaningless for most types; `type` is
unconstrained; nothing says which element or instance a measure applies to.

**The organising idea of the replacement.** *A measure is a typed, targeted write to the home
inventory, stating the post-measure state of one named block.* Wall insulation, a heat-pump swap and
an electric car have nothing in common as physical work and everything in common as edits to a
`HomeInventoryInput`. The complete proposal is `roadmap/renovisor/mockups/measures.schema.yaml`: a
paste-ready OpenAPI 3.1 fragment, 14 variants under a `type` discriminator, each declaring in
`x-writes` the inventory path it sets.

- **A6.1 — Closed, discriminated vocabulary.** An unknown `type` is rejected, not ignored.
- **A6.2 — Every measure names its target** — the element, the store, the vehicle — wherever more
  than one candidate exists.
- **A6.3 — Replacement semantics, not deltas.** "+100 mm of insulation" is ambiguous about what it
  was added to and does not compose when two measures touch one element; "the roof's U-value is now
  0.16" is neither.
- **A6.4 — Closure over the inventory.** Applying a package's measures to a valid
  `HomeInventoryInput` must yield a valid `HomeInventoryInput`. This is what makes the vocabulary
  unable to express anything the simulator cannot consume — by construction rather than discipline —
  and makes R5 a replay of writes rather than per-measure application logic.
- **A6.5 — Stable, content-derived `measure_id`.** Two clients specifying the same renovation must
  reach the same `pkg_id`, so ids cannot be minted randomly.
- **A6.6 — `alternatives` is removed.** A package is one concrete plan whose hash identifies one
  simulation; a measure carrying alternatives makes it a set of plans and leaves "which alternative
  produced these KPIs?" unanswerable. Alternatives belong to whatever enumerates packages, which is
  outside HiSim (§5).
- **A6.7 — Units follow the inventory's convention** (`_in_watt`, `_in_kwh`, `_in_liters`,
  `_in_degree`), replacing `thickness_mm` / `capacity_kw`, which used a different convention from
  every other schema and left `capacity_kw` ambiguous between thermal and electrical power.
- **A6.8 — A measure carries no price.** Cost, grant and duration are computed by the backend. A
  self-pricing measure would let two packages with identical physical content disagree, and would put
  policy data in a request body.

**A7 — Envelope measures state a U-value; `Material` stays advisory.** `[revised 2026-08-27]`
HiSim consumes U-values — `BuildingConfig` carries all ten per-element fields — and cannot derive one
from a material id, because `/materials` returns `{id, name, description, category, image_url}` with
no thermal conductivity. The measure states `resulting_u_value_in_watt_per_m2_per_kelvin` outright
and treats `material_id` and `thickness_mm` as pricing inputs, so the catalogue can gain conductivity
later without a contract change.

**A8 — State the envelope precedence rule.** `[proposed; field_inventory rows 11, 13–22]`
`retrofit_status` (a TABULA variant) and `envelope_details` (ten explicit U-values and areas) are two
sources of truth for the same envelope. The contract must say which wins, per element, when both are
given.

**A11 — State precedence between given sizes and automatic sizing.** `[proposed; field_inventory rows 12, 29]`
`heating_system.power_in_watt`, `max_thermal_building_demand_in_watt` and the storage volumes compete
with HiSim's sizing laws, which compute the same quantities. The contract must say whether a stated
value pins the size or is only a hint.

**A21 — Reconcile the two building-element vocabularies.** `[proposed; measured 2026-08-27]`
`building_config.envelope_details` has **five** elements — floor, facade, roof, window, door.
`condition_assessment.building_elements` has **nine** — external_facade, exterior_walls,
interior_walls, floors, attic_ceiling, attic_roof, roof_covering, windows, house_door. They do not
nest: `attic_ceiling`, `attic_roof` and `roof_covering` all fall into `roof`; `external_facade` and
`exterior_walls` both fall into `facade`; `interior_walls` maps to nothing thermal. A measure
targeting `attic_ceiling` has no U-value field to write to. Measures target the five; whether the
condition vocabulary shrinks to match or stays a finer survey instrument with a documented mapping is
the frontend team's call, but the mapping must exist.

**A24 — Vehicles need stable ids.** `[proposed; follows A6.2]`
`electric_vehicles[]` and `fossil_vehicles[]` entries carry no identifier, so a measure cannot name
the vehicle it replaces or removes. Positional references are not an option: an index is not stable
under a list reorder, and a package's hash must not change because the inventory was re-serialised.
This is a change to `HomeInventoryInput`, which is why it and the measure model cannot be revised
separately.

#### For step 4 — returning a result

**A4 — State the weather basis in the result.** `[proposed; LocationEnum IE → Dublin 2019 NSRDB]`
A result depends on which weather dataset and year it was simulated against; the contract has no
field for it. A finished result must name its weather basis, or the contract must declare it fixed
per country and part of the calculation version (R14).

**A9 — Redefine `Range` as a coherent evaluation slot.** `[proposed; hisim/economics/uncertainty.py:Slot]`
The cost engine's band is `LOW` / `BEST_ESTIMATE` / `HIGH`, where a slot is a property of the whole
evaluation — every cost parameter at its minimum and every revenue parameter at its maximum within
one slot. The contract's `{low, normal, high}` names the middle slot differently and says nothing
about coherence, which invites a reader to mix slots across fields. Rename `normal`, and state that
the three values of every `Range` in one `Costs` object come from the same slot.

**A10 — Fix `self_sufficiency_in_percent`'s type.** `[proposed; contract Kpis]`
Declared `string`; it is a number.

**A12 — The 10 KPI fields with no HiSim source stay, mocked for the MVP.** `[decided 2026-08-27, owner]`
`energy_label`, `disruption_days_by_level` (4), `indoor_air_quality`, `thermal_insulation_effect`,
`summer_heat_protection`, `comfort.heating`, `comfort.cooling` are implemented over the coming months
and served as **constants** until they are; the frontend builds against the full KPI object from the
start. In exchange, a mocked value must be **labelled as mocked in the result** (R8, AC8.1): a
constant indistinguishable from a computed number is worse than an absent field, because nothing
downstream — a package comparison, a report, a homeowner's decision — can tell. The label is also
what makes the mocks removable, naming exactly which results go stale when a real implementation
lands.

**A13 — Resolve `property_value_increase_in_percent`.** `[proposed; field_inventory §4]`
No model, no data source, no evident path to one. Recommend removal.

**A14 — Disambiguate `energy_demand_kwh` and `emissions_kg_co2`.** `[proposed; field_inventory §3]`
Both need a stated system boundary (delivered / final / primary; which carriers) and a stated period.
HiSim's KPIs are "for simulated period", which is not a contract-level concept.

**A15 — Carry the calculation version in a finished result.** `[proposed; pairs with R14]`
A result cached `immutable, max-age=31536000` under a hash covering only the inputs cannot be
corrected when the model changes. The contract needs a field for the version R14 produces.

**A16 — Refusals need a code, not prose.** `[proposed; contract CalculationStatus.reason]`
`reason` is free text. A frontend explaining "no Irish archetype exists for a 1988 multi-family
building" needs an enumerated reason code plus the offending field paths — the contract-side half of
R13.2.2.

## 9. Constraints, invariants and assumptions

### Known constraints

- **C1** `[given; epic C1/E7]` Golden parity: identical concrete inputs produce identical results
  before and after any change made here.
- **C2** `[given; epic EAC5]` `ModularHouseholdConfig`, `EnergySystemConfig` and `ArcheTypeConfig`
  are deleted at P5. Nothing built here may depend on them.
- **C3** `[proposed; p2_file_format_requirements.md]` Energy-system files are YAML only.
- **C4** `[proposed; plan.md P4]` The base files of R2 cannot be written until the P4 component sweep
  converts ≈13 further modules. **Five of ten heating systems and every optional subsystem** — PV,
  battery, EMS, buffer storage, DHW storage, cars — are blocked on it. This is the largest schedule
  risk in the document.
- **C5** `[proposed; weather.py:LocationEnum]` One weather station per country code; `IE` is Dublin,
  NSRDB 15-minute, year 2019.
- **C6** `[proposed; episcope-tabula.csv]` The Irish TABULA table has 124 rows in types `SFH`, `TH`
  and `AB` only. Some rows carry zero door or window area and crash the `Building` component
  (`hisim/renovisor/spec.md` §4.2 workaround).
- **C7** `[proposed; utspclient.helpers.lpgdata]` Occupancy is one of 66 catalogue households and one
  of 6 travel-route sets; arbitrary compositions cannot be simulated.
- **C8** `[proposed; hisim/subsidy_catalog/, hisim/cost_database/tariffs/]` No Irish subsidy catalogue
  and no Irish tariff exist, so `grant_euro` and `energy_costs_euro` cannot be computed for Ireland
  until they do.
- **C9** `[given; epic EQ1, 2026-08-26]` Preset, field and fact names may change freely until P5 is
  accepted; from then a rename is a breaking change.
- **C11** `[given 2026-08-27, owner]` The caller is a C# application in a separate repository,
  invoking HiSim through a container **reused across calculations**, files in and files out. HiSim's
  deliverable is a container image with a file-based process contract, and no part of the contract's
  HTTP behaviour is HiSim's to implement or test. The container's lifetime spanning many calculations
  is what makes C13 blocking.
- **C12** `[proposed; follows C11]` The interface crosses a language boundary. Anything HiSim wants
  the caller to act on — a refusal reason, a translation report, a version stamp — must be a
  structured document, not a Python object and not a log line.
- **C13** `[proposed; surveyed 2026-08-27]` **HiSim is not currently safe to run twice in one
  process.** Container reuse makes this blocking rather than latent:
  - `sim_repository_singleton.py:SingletonSimRepository` is a process-wide singleton, written from
    **29 call sites across 11 component modules**, and **nothing in `hisim_main.py`, `simulator.py`
    or `energy_system/executor.py` resets it between runs**. `simulator.py:367` clears the
    *per-simulator* `SimRepository`, a different object.
  - Its `clear()` does `del self.my_dict`, so a second run meets a deleted attribute rather than stale
    data. `reset()` exists beside it, its docstring saying it is for callers who need a clean slate
    "across successive simulations" — the hazard is known, the fix is written, and no production path
    calls it.
  - `result_path_provider.py:ResultPathProviderSingleton` captures `datetime.datetime.now()` in
    `__init__`, so the **first** calculation in a container fixes the timestamp every later one uses.
    It has a `reset()` classmethod; nothing in a run calls it.
  - `tests/test_singleton_sim_repository.py:211` clears `SingletonMeta._instances` by hand — the test
    suite working around the same problem.

  Module-level caches (weather data, the TABULA table, pvlib databases), logging level, matplotlib
  global state and RNG seeding are candidates for the same failure mode and have not been surveyed.
  **Bounded by Q10**: since calculations never overlap, every item is a *staleness* problem with a
  reset as its fix, not a *race* needing a redesign. The epic's parking-lot `SingletonSimRepository`
  redesign stays parked.

*(C10, on the contract's co-owned envelope, was dropped when A17 was withdrawn — every remaining §8.2
item is a payload change the backend owns. A17 itself is in `requirements.discussion.md` Part 3.)*

### Assumptions requiring confirmation

- **A-2** `[proposed]` Ireland is the only country that must work; the mechanism must not preclude
  others.
- **A-3** `[proposed]` The 27 non-simulation fields are genuinely wanted by the product, and the work
  of consuming them — a scheduling and replacement model — is planned somewhere. If not, they are 27
  fields collected for nothing.
- **A-4** `[proposed]` The frontend can tolerate absent KPI fields (R8) for anything that is neither
  computed nor deliberately mocked.

## 10. Acceptance criteria

| ID | Criterion | Verifies |
|---|---|---|
| AC1 | Every field of the amended `HomeInventoryInput` is classified in the contract and appears in the translation report of any calculation whose configuration carries it. | R7, A1, A2 |
| AC2 | For each supported `heating_system.system` value, a checked-in base energy-system file exists, loads, resolves, builds and runs. | R1, R2, C4 |
| AC3 | A parametrised energy-system file differs from its base file only in `config` values, `constructor` arguments and group flags — verified mechanically by diffing against the base. | R4 |
| AC4 | A configuration plus a package whose measures change the roof produces a file whose `building.config.roof_u_value_in_watt_per_m2_per_kelvin` carries the measure's value. | R5, A6, A7 |
| AC4.1 | Applying any package's measures to a valid `HomeInventoryInput` yields a valid `HomeInventoryInput` — checked by schema-validating the post-measure inventory, over every measure variant. | A6.4 |
| AC4.2 | Two clients submitting the same renovation produce the same `pkg_id`; reordering a package's measures does not change it. | A6.5, A6.6 |
| AC4.3 | Every measure variant declares the inventory path it writes, and every path it declares exists in `HomeInventoryInput`. | A6.1, A6.2, A24 |
| AC5 | Running the same configuration and parameters twice produces byte-identical parametrised files and realized records, and equal result payloads. | R10, G3 |
| AC6 | Re-executing a stored realized record reproduces the run bit-for-bit. | R10, R16, UC5 |
| AC7 | An `IE` + `MFH` configuration is refused, distinguishably from a crash, with an enumerated reason naming the offending field, before any simulation starts. | R11, A16, UC3 |
| AC8 | Every `Kpis` and `Costs` field the amended contract keeps is produced by a finished calculation, and each is traceable to a named HiSim KPI, a cost-engine output, or a declared mock. | R8, R9, A12, A13, A14 |
| AC8.1 | Every mocked KPI value is labelled as mocked in the result; a consumer can list which KPIs of a result are simulated and which are placeholders without consulting documentation. | R8, A12 |
| AC9 | The three values of a `Range` in one `Costs` object come from one evaluation slot; a test detects a mixed-slot object. | R9, A9 |
| AC10 | A finished result carries a calculation version, and changing any of the library, base files, component models or cost database changes it. | R14, A15 |
| AC11 | A calculation runs end to end through the file interface alone, driven by a caller that imports no Python: configuration in, results out, outcome signalled. | R13, C11 |
| AC11.1 | **The reuse identity test.** A calculation run as the *n*-th in a container's life produces byte-identical outputs to the same calculation run as the first — verified for a sequence of *differing* calculations, since identical ones would not surface the leak. | R13.1, R10, C13 |
| AC11.2 | A refusal, a validation error and a crash each write `errors.json` at the fixed path, and are told apart from its contents alone, with no log parsing. | R13.2, R13.2.1, R11 |
| AC11.2a | Every reason code an `errors.json` can carry is in the published catalogue, and every catalogue entry is reachable by some input. | R13.2.2, A16 |
| AC11.2b | A validation error or refusal names the offending field path; a crash names its stage. | R13.2.3, UC3 |
| AC11.3 | The same image and the same input, run on two different machines, produce equal results and equal version stamps. | R13.1, R10, R14 |
| AC11.4 | Resource growth over a long sequence of calculations is bounded, or the recycle limit is stated and enforced. | R13.3 |
| AC11.5 | After a calculation and the deletion of its output location, a working-tree cleanliness check (`git status --porcelain` or equivalent) reports nothing — no stray `results/`, no file written beside the package. | R13.4 |
| AC11.6 | The container's documentation states that it serves one calculation at a time and is not safe to run two concurrently. | R13.5 |
| AC13 | All golden suites pass; no result of an existing setup changes. | R15, C1 |
| AC14 | The contract validates against an OpenAPI 3.1 validator after amendment, and every `x-contract-status: provisional` schema names its owner. | §8.2 |

## 11. Open questions and decisions

### Decision register

Deliberations behind each of these — options weighed, alternatives rejected — are in
`requirements.discussion.md` Part 1.

| ID | Decision | Date |
|---|---|---|
| Q1 | HiSim supplies a calculation **library** in a container image; the service is a C# application in another repository. HiSim owns no HTTP, store, queue or auth. | 2026-08-27 |
| Q9 | The interface is **files in, files out**, and containers are **reused** across calculations. | 2026-08-27 |
| Q10 | A container runs **one calculation at a time**; parallelism comes from more containers. | 2026-08-27 |
| Q7 / A12 | The 10 KPI fields HiSim cannot compute **stay in the contract as constants** until implemented, each labelled as mocked. | 2026-08-27 |
| A6 | `Measure` is **replaced outright** by a discriminated union — a measure is a typed, targeted write to the home inventory. | 2026-08-27 |
| A18 | The service supplies a **`simulationparameters.json`** per calculation. | 2026-08-27 |
| R13.2 | Failures are reported through a structured **`errors.json`**, with reason codes from a published catalogue. | 2026-08-27 |
| R6 / Q5 | **Package generation is outside HiSim.** | 2026-08-27 |
| R12 / Q6 | **The `fast-estimate` tier is outside HiSim.** | 2026-08-27 |
| — | **The PDF report is outside HiSim.** | 2026-08-27 |
| A17 | **Withdrawn** — a real contract defect, but between the frontend and the C# service. | 2026-08-27 |

---

**Q2 — Does a mocked or model-version change invalidate cached results, and how?** · blocks R14, A15, AC10

*Context.* R14 requires a finished result to carry a version stamp. What the service does with it is
the service's design, but one half of the question is HiSim's: the ten mocked KPI fields (A12) will
be replaced by real implementations over the coming months, and every result computed before each
replacement is wrong afterwards while looking identical. A result cached `immutable,
max-age=31536000` outlives several such replacements.

*Options.* (a) **The stamp changes on every model, base-file, cost-database or mock change**, and the
service treats a stamp change as a cache invalidation. Consequence: correct; a mock landing
invalidates every cached result, which is expensive and right. (b) **The stamp covers only the
code**, with mocks excluded as "not really the model". Consequence: cheap, and it means the exact
values most likely to change silently are the ones the stamp does not cover. (c) **Mocked fields
carry their own per-field version**, so replacing one invalidates only results whose consumers read
it. Consequence: finest-grained and most work; needs the labelling of AC8.1 anyway.

*Recommendation.* (a). It is the only option under which "the stamp changed" and "the answer may
have changed" mean the same thing, and A12's labelling already makes the blast radius visible.

*Blocks.* R14, A15, AC10.

---

**Q3 — Is the dwelling field set re-anchored on energy-system names, or frozen as a library-owned façade?** · blocks R4, C9

*Context.* The contract's field set is coupled to HiSim internals far more tightly than it looks:
`envelope_details` is field-for-field `ArcheTypeConfig`, `heating_system.system` enumerates
`hisim.loadtypes.HeatingSystems` in HiSim's own spelling, and `heat_distribution_system` carries
`lt.HeatDistributionSystemType`'s literal strings, spaces included. `ArcheTypeConfig` is deleted at
P5 (C2), so the anchor disappears. The epic's EQ1 decision leaves HiSim's names free to change until
P5 is accepted, then freezes them (C9). Full evidence in `requirements.discussion.md`.

*Options.* (a) **Re-anchor on the energy-system file's names** — the contract tracks whatever the
converted component configs call these fields. Consequence: no mapping table; the contract inherits
HiSim's freeze at P5 and every later component rename becomes a public API break. (b) **Freeze the
contract's field set as a façade** the library maps onto whatever HiSim calls things. Consequence: a
mapping table to maintain and test, but the two naming spaces evolve independently and the frontend
never has to care that a HiSim class was renamed. (c) **Case by case.** Consequence: the worst of
both; nobody can tell which fields are coupled.

*Recommendation.* (b). The contract is a public interface with an external co-owner; HiSim's
component field names are not, and the epic plans to keep renaming them through P4. The mapping table
is exactly what R7's translation report already has to produce.

*Blocks.* R4, C9.

---

**Q4 — What besides `heating_system.system` selects the base file?** · blocks R2, R3, AC2

*Context.* R2 asks for one base file per heating system: ten. But `energy_system_mockup.yaml`'s open
point O2 establishes that the EMS-less variant of a system must be a *separate base file*, not a
disabled group, because with the EMS off nothing feeds the electricity meter and adding the direct
feeds would double-count while the EMS is on. Solar thermal is similar: `used_for_space_heating` and
`used_for_dhw` change which storage the collector feeds, and a group is a presence flag, not a
rewiring rule (epic E4).

*Options.* (a) **Ten base files, EMS always present.** Consequence: simplest; every simulated
household carries an EMS whether or not it has a battery, changing results for the majority of
dwellings that have neither PV nor battery. (b) **Twenty base files** — each heating system with and
without the EMS. Consequence: correct, and twenty files to author, review and keep in step. (c) **Ten
base files plus a `direct_metering` group** enabled iff `battery_and_ems` is off. Consequence: fewer
files, but "these two groups are mutually exclusive" cannot be expressed in the format and would have
to be enforced by the library — a rule living outside the file it governs.

*Recommendation.* (b), which is the mockup's own recommendation. Twenty authored files is a real
cost; a silently double-counted meter is a wrong number.

*Blocks.* R2, R3, AC2.

---

**Q8 — Does the Irish cost data get built, or does Ireland ship without grants and energy costs?** · blocks R9, C8, AC8

*Context.* `hisim/subsidy_catalog/` contains `AT.json` and `DE.json`; `hisim/cost_database/tariffs/`
contains one German tariff. The contract's `GrantScheme` enum is Irish (`warmer_homes_scheme`,
`enhanced_rate_first_time_buyer`), and `Costs` carries `grant_euro` and `energy_costs_euro` as parts
of every package.

*Options.* (a) **Build `IE.json` and an Irish tariff** to the same standard as the German catalogue,
sourced and dated. Consequence: the contract works as drafted; someone must do the data work and own
its currency as SEAI schemes change. (b) **Ship without them**, serving those two fields as absent.
Consequence: the product's central number — what a renovation costs after grants — is missing.
(c) **Hard-code a small Irish scheme set** in the library. Consequence: fast, and it puts policy data
outside the catalogue that exists to hold policy data.

*Recommendation.* (a). It is the only option under which `Costs` means what it says, and the
catalogue mechanism already exists — this is data entry with provenance, not new machinery.

*Blocks.* R9, C8, AC8.

## 12. Glossary

**Configuration file** — what the caller hands the container: one dwelling and one package. Whether
that is one document or two is not yet settled.
**Base file** — a checked-in, hand-authored energy-system file, one per supported heating system. The
set of base files is the complete set of systems RenoVisor can simulate.
**Parametrise** — to take a base file and fill in a request's values: `config` overrides,
`constructor` arguments and `groups.<name>.enabled` flags, and nothing else (R4). Deliberately not
"generate": no energy system is ever authored from a request, so every system that runs has been read
by a person.
**Package** — a set of renovation measures plus grant selections, a schedule and financing,
identified by the hash of its own contents (`pkg_id`).
**Measure** — one renovation decision, expressed as a typed write to a named block of the dwelling
configuration, stating that block's post-measure state (A6).
**Refused** — a terminal outcome meaning the input was well-formed but not simulable, as distinct
from a validation error and from a crash.
**Translation report** — the per-calculation record of what happened to every field of the
configuration: `used`, `approximated`, `defaulted`, `ignored` or `non_simulation`.
**Realized record** — the concrete, re-executable copy of an energy system that a run writes back,
with every preset expanded and every `AUTO` replaced by the number it resolved to.
**Slot** — one coherent evaluation world of the cost engine (`LOW`, `BEST_ESTIMATE`, `HIGH`), in
which every cost parameter sits at one end of its band and every revenue parameter at the other.
**Non-simulation field** — a configuration field that is a legitimate product input but drives cost,
scheduling or grants rather than the physics.
