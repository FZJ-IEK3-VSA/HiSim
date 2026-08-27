# RenoVisor backend: translation layer and API contract — requirements

**Status:** draft (Q1, Q9, Q10 decided 2026-08-27: a reused container running one calculation at a time, files in and files out, driven by a C# service in another repo)
**Date:** 2026-08-27
**Author(s):** assistant (all `[proposed]` items, evidence survey) · Noah Pflugradt (owner; all `[given]` items)
**Reviewers:** HiSim core team · RenoVisor frontend team (§8.2 only)
**Supersedes / related:** `hisim/renovisor/spec.md` (v1 translator spec — superseded by this
document's §8.1, kept until the replacement is accepted) · `roadmap/renovisor/openapi.yaml`
(RenoVisor API v0.3.0-draft, the contract under review) ·
`roadmap/declarative_energy_systems/epic.md` and its `plan.md` P5 row (this work *is* the
RenoVisor half of P5) · `roadmap/cost-spec-v2.md` (the cost engine the result payloads need)
**Companions:** `roadmap/renovisor/field_inventory.md` (the counted survey) ·
`roadmap/renovisor/mockups/heat_pump_household.energy_system.yaml` (a base file after parametrisation)

**Tags:** feature, behavior-change, migration, compatibility, technical-debt
**Keywords:** RenoVisor, API contract, OpenAPI, home inventory, package, energy-system file,
content addressing, determinism, KPI derivation, cost engine, TABULA, Ireland

---

## 1. Abstract

RenoVisor is a renovation-advice service whose calculations are performed by HiSim. Its
frontend and backend teams have drafted a shared HTTP contract (`openapi.yaml` v0.3.0-draft):
content-addressed home inventories, packages of renovation measures, and calculation results
read through cacheable `GET`s. HiSim's side of that contract is a translator written against a
different, older contract, which produces a stand-in configuration object rather than a
simulation description and explicitly declines to derive any result payload. Meanwhile HiSim
itself is mid-migration to declarative energy-system files, which will delete the object the
translator produces. This document establishes what the translation layer must do to satisfy
the drafted contract, and — in the opposite direction — which parts of the drafted contract
cannot be satisfied by HiSim as specified and must change before sign-off.

## 2. Keywords and tags

```
Tags: feature, behavior-change, migration, compatibility, technical-debt
Keywords: RenoVisor, OpenAPI, home inventory, package, energy-system YAML, content hash,
          determinism, caching, KPI derivation, cost engine, subsidies, TABULA, Ireland
```

## 3. Executive summary

**Problem.** Two contracts and one implementation, none of which line up. The drafted API
(v0.3) speaks of home inventories, packages, fast estimates and detailed simulations, and
expects each finished calculation to carry 13 KPI fields and 9 cost ranges. The existing
translator (`hisim/renovisor/`, 7 modules, ~1,700 lines) speaks the v1 contract, emits a
`ModularHouseholdConfig` for one of ten Python setups, and states in its own specification that
deriving result payloads is out of scope. Counted against HiSim as it stands: of the contract's
79 home-inventory fields, 24 are directly consumable, 3 only approximately, 15 are blocked on
component work that has not happened, and 35 have no simulation consumer at all; of the 13 KPI
fields, 3 exist; of the 9 cost ranges, 7 have an engine and 2 of those lack Irish data. Five of
the ten heating systems the contract offers cannot be expressed as an energy-system file today.

**Required.** (a) A translation layer that, from a home inventory plus a package, **selects one
of a small set of hand-authored energy-system files and parametrises it** — overrides and group
flags only, never a system authored from the request — runs it, and derives the contract's
result payloads. This replaces both the `ModularHouseholdConfig` path and the "upload raw files,
let the server interpret them" arrangement. It is a library, delivered as a container image that
a C# service in a separate repository reuses across calculations, handing each one a house
configuration as a file and collecting its results as files; HiSim implements none of the
contract's HTTP behaviour (decided 2026-08-27, §11 Q1, Q9, Q10). (b) A revised API contract in
which every field either has a HiSim consumer, is declared non-simulation, or is removed — and in
which determinism, versioning and the meaning of a cost `Range` are stated, because the contract
promises results that are cacheable forever.

**Affected.** `hisim/renovisor/` (rewritten), `roadmap/renovisor/openapi.yaml` (amended), the
declarative-energy-systems P4 sweep (13 modules become blocking), `hisim/economics` (Irish
tariff and subsidy data), and the RenoVisor frontend team, who co-own the contract's stable
envelope.

**Cost of inaction.** The contract gets signed off with 35 fields the simulator cannot read and
10 result fields nothing can compute, and the mismatch surfaces as silent zeros in a
user-facing renovation recommendation.

## 4. Context and current situation

### 4.1 Three moving parts

| Part | State today | Reference |
|---|---|---|
| RenoVisor API contract | v0.3.0-draft; 15 paths, 17 operations, 20 schemas; envelope declared stable, payloads declared provisional | `roadmap/renovisor/openapi.yaml` |
| HiSim translation layer | v1 translator against the *old* contract (camelCase, `homeInputs`, `measures`); result derivation explicitly out of scope | `hisim/renovisor/spec.md` §1 |
| HiSim system description | migrating from Python setups + `ModularHouseholdConfig` to declarative energy-system files; P1 and P2 accepted, P3–P5 open | `roadmap/declarative_energy_systems/plan.md` |

The three do not currently meet. The v1 translator's output object is scheduled for deletion:
the epic's acceptance criterion EAC5 reads "RenoVisor, building sizer and HPC harness run from
energy-system files; `ModularHouseholdConfig` is deleted", and P5's first checkbox is "RenoVisor:
base file per heating system; `mapping.py` → overrides + group flags". This document is the
requirements input for that checkbox, widened to cover the contract itself because the contract
was drafted against the object being deleted (see §4.4).

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
| Component classes the ten base files need | ≈26 across ≈19 modules |
| Contract operations that need a HiSim result | 9 of 17 |
| Irish subsidy catalogues in `hisim/subsidy_catalog/` | 0 (`AT.json`, `DE.json` only) |
| Irish tariff files in `hisim/cost_database/tariffs/` | 0 (`DE_DYNAMIC_SYNTHETIC_2024.json` only) |

### 4.3 What the contract asks for that v1 never did

Five capabilities are new, not refinements:

1. **Result derivation.** v1 uploaded HiSim's result *files* and left interpretation to the
   receiving server (`spec.md` §1: "explicitly out of scope for v1"). v0.3's `CalculationStatus.
   result` is a `Package` carrying `kpis` and `costs` — 13 + 27 numbers the backend must produce.
2. **Package generation.** `GET /homeinventories/{hi_id}/packageset` is documented as returning
   "100+ packages once finished; the frontend picks three to display". Nothing generates,
   scores or ranks renovation packages today.
3. **Two calculation tiers.** `fast-estimate` and `detailed-simulation` are separate resources
   with separate result URLs. HiSim has one tier: a full simulation. There is no fast path.
4. **A read-through, content-addressed service.** Results are `GET`, lazily started, coalesced
   across duplicate requests, and — once finished — cached `immutable` for a year. v1 is a
   one-shot CLI that pushes to a URL. *This one lands entirely on the C# side (C11) and is
   listed here only because it is what makes determinism (R10) and versioning (R14) load-bearing
   for HiSim: a result the service will cache for a year had better be reproducible.*
5. **Refusal as a first-class outcome.** `CalculationState` includes `refused`. v1 has exit
   codes; the notion that a *valid* request may be un-simulable (no TABULA archetype, an
   unsupported combination) has no representation in it.

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

### 4.5 Stakeholders and consumers

**Existing.** The RenoVisor frontend (co-owner of the contract's stable envelope; only it can
sign off §8.2 changes). **The RenoVisor backend service — a C# application in a separate
repository, which is the translation layer's only caller.** It owns every HTTP concern of the
contract and invokes HiSim through a **reused** container: a house configuration goes in as a
file, results come back as files, and the same container serves the next calculation
(Q1, Q9, decided 2026-08-27). It also owns the deletion of a result directory between
calculations and the cleanliness check that proves nothing was left behind (R13.4). The declarative-energy-systems epic (this
is its P5 RenoVisor track). The building sizer and HPC harness, which share the base-file
mechanism and will inherit whatever is built here.

**Hypothetical, and therefore not a constraint.** A webtool, a template creator, other
countries beyond Ireland.

### 4.6 Distinguishing current from required

| | |
|---|---|
| **Current behavior** | A CLI translates a v1 request into a `ModularHouseholdConfig`, picks one of ten Python setups, runs it in-process, and POSTs matched result files plus a mapping report to a URL. |
| **Required behavior** | A reused container is handed a house configuration; inside it, a library **selects one of the checked-in energy-system files and parametrises it** — overrides and group flags, nothing authored from scratch — runs it deterministically, derives the contract's `kpis` and `costs`, and hands them back. The C# service serves them under a content-addressed URL. |
| **Explicitly not** | Generating an energy system from the request. The systems are hand-authored and reviewed; the translator chooses among them and fills in values. What it may write is fenced by R4 and checked by AC3. |
| **Decided** | `[decided 2026-08-27, owner]` The service is a C# application in another repository. HiSim supplies a calculation library, packaged as a container image the service reuses across calculations, calling it with a file (Q1, Q9, Q10). HiSim owns no HTTP, no store, no queue and no auth, and nothing in §8.1 may assume otherwise. |

## 5. Goals and non-goals

**Goals**

- **G1** Every field the contract keeps has a named consumer: a HiSim configuration field, an
  energy-system file element, a cost-engine input, or an explicit "non-simulation" declaration.
- **G2** The translation layer runs a request by parametrising one of a small set of
  hand-authored energy-system files, not by building a `ModularHouseholdConfig` and not by
  generating a system per request, so that the epic's EAC5 can be met, every simulated system
  has been reviewed by a person, and a request's simulation is a readable, re-executable
  artifact.
- **G3** Identical inputs produce identical results, bit for bit, so the contract's
  content-addressing and its `immutable` caching are sound rather than aspirational.
- **G4** The backend derives the contract's result payloads; no consumer of the API needs to
  interpret HiSim output files.
- **G5** A request that cannot be simulated is refused with a machine-readable reason, never
  answered with a plausible-looking wrong number.
- **G6** The contract and the translator are amended together, in one review, so neither is
  signed off against a version of the other that does not exist.

**Non-Goals**

- The HTTP service itself: routing, auth, storage, result caching, job coalescing, content
  hashing, PDF rendering, and the scheduling of calculations. All of it belongs to the C#
  service (§4.6, Q1 decided). HiSim is invoked, never polled.
- Countries other than Ireland. The mechanism must not preclude them; only IE is verified.
- Reworking the KPI computation or the cost engine beyond what the payloads need.
- The building sizer and HPC harness tracks of P5, though they share the base files.
- A renovation-*advice* engine: which packages are worth recommending is a product question;
  this document requires only that a package set can be produced and priced (see R6, Q5).

## 6. Use cases and mockups

**Whose use cases these are.** Every case below is written at HiSim's boundary: a file arrives,
a calculation happens, files come back. There is no HTTP on this side — no `POST`, no `202`, no
polling, no cache. The contract's endpoints appear in this document only as §8.2's subject
matter and as the reason a payload has the fields it has; the C# service maps between the two
(C11), and how it does so is not HiSim's to specify.

One thing Q9's answer did not settle: whether the inventory and the package arrive as one
document or two. The cases below say "a configuration file" and stay deliberately neutral;
whoever writes the process contract decides (Q9 sub-item, noted in R13).

### UC1 — One calculation

*Input.* The translation layer is called with a configuration file describing one dwelling, one
package of measures and which tier is wanted.
*Expected.* It selects the base energy-system file for the dwelling's heating system,
parametrises it, runs it, and leaves behind the result payload (`kpis`, `costs`), the
translation report of R7, the parametrised energy-system file and the run's realized record.
Nothing is uploaded, and nothing is fetched.

### UC2 — What parametrisation produces

*Input.* The UC1 configuration, for an Irish 1988 detached house with a heat pump, PV and a
battery.
*Expected.* The parametrised file is the mockup
`roadmap/renovisor/mockups/heat_pump_household.energy_system.yaml` — **the external
representation this document is chiefly about**. Its footer (M1–M5) states what it pins down:
only overrides and group flags are written; ten base files are needed and the EMS-less variants
double that to twenty; every override traces to one named contract field.

### UC3 — A configuration that cannot be simulated

*Input.* A configuration with `country_code: IE` and `tabula_building_type: MFH`.
*Expected.* A refusal, signalled so the caller can tell it from a crash, naming the offending
field and carrying a machine-readable reason — the Irish TABULA table has 48 `SFH`, 51 `TH` and
19 `AB` rows and **no** `MFH` row. Decided before any simulation starts. Not a traceback, and not
a silently substituted archetype. What the C# service turns this into is its own affair.

### UC4 — A field with no consumer

*Input.* A configuration stating `battery_storage.state_of_health_in_percent: 90`.
*Expected.* The simulation ignores it (HiSim has no capacity-fade model), and the run's
translation report says so in as many words. Under R7 no field is silently dropped.

### UC5 — Re-running a realized record

*Input.* The realized record written by a finished calculation, re-executed.
*Expected.* Byte-identical results. This is the epic's identity test applied to RenoVisor's
output, and it is what lets the C# side treat a finished result as final.

### UC6 — Many calculations in one container

*Input.* A sequence of *differing* configuration files handed to the same container, one after
another (Q9, Q10).
*Expected.* Each produces exactly what it would have produced as the first calculation of a
fresh container, and after each one's output location is deleted, nothing of it remains on disk.
This is the case C13 says HiSim does not survive today, and it is what AC11.1 and AC11.5 test.

## 7. Why it matters

The contract's own text says results "may be cached indefinitely" because "a finished result
never changes". That promise is only as good as the determinism of the pipeline behind it, and
the pipeline does not yet exist. Signing the contract before the two sides are reconciled has
three concrete consequences. Thirty-five of seventy-nine inventory fields would be collected
from users, hashed into an identity, and then ignored — including the entire 18-field condition
assessment. Ten of thirteen KPI fields and two of nine cost ranges would have to be served as
`null` or, worse, as invented numbers, in a product whose whole purpose is to tell a homeowner
what a renovation will cost and save. And a result cached for a year under a hash that does not
cover the model version cannot be corrected when the model is fixed.

## 8. Requirements

Two families. **R** requirements are on the HiSim translation layer; **A** requirements are on
the API contract and need the frontend team's agreement. Provenance is on every item.

### 8.1 Translation layer (R)

**R1 — Run from a parametrised energy-system file.** `[given; epic EAC5, plan P5; corrected 2026-08-27, owner]`
Each calculation must run a `*.energy_system.yaml` document obtained by **selecting one of the
checked-in files and parametrising it** (R2, R3, R4), together with a `*.simulation.yaml`
parameters file. The layer must not construct `ModularHouseholdConfig`, `ArcheTypeConfig` or
`EnergySystemConfig`, and must not author an energy system from the request: the set of
simulable systems is fixed, hand-written and reviewed, and a request chooses among them rather
than describing a new one.

**R2 — Base files, one per heating system.** `[given; plan P5 "base file per heating system"]`
A checked-in base energy-system file must exist for each `heating_system.system` value the
contract offers. A base file is authored and reviewed as a system, not generated.

**R3 — Selection is a lookup, not a computation.** `[proposed]`
Which base file a request uses must be a total function of a stated set of inventory fields, so
that the answer is inspectable without running anything. Currently that set is
`heating_system.system` plus whichever fields Q4 resolves (EMS presence, solar-thermal use).

**R4 — Parametrising writes only overrides and group flags.** `[proposed; from mockup M1]`
Beyond the base file's content, a parametrised energy-system file may differ only by (a) `config`
field overrides, (b) `constructor` arguments, and (c) `groups.<name>.enabled` flags. The
translator must never author `inputs`, `sizing_sources`, or a component entry. Rationale: wiring
and sizing sources are the base file's reviewed content; a translator that writes them is a
second, untested system description. This requirement is what gives R1's "select, don't
generate" a testable edge — AC3 diffs the parametrised file against its base.

**R5 — Apply a package's measures to the inventory before parametrising.** `[proposed; extends spec.md §3]`
A `PackageDefinition`'s measures must be applied to a copy of the home inventory, and the
resulting inventory is what the emitter reads. Envelope measures must reach the per-element
`BuildingConfig` U-value and area fields — not, as in v1, be folded into the TABULA
refurbishment variant — because those ten fields exist and are directly consumable
(`field_inventory.md` rows 13–22).

**R6 — Enumerate a package set.** `[given; from the contract's packageset resource, which the C# service backs]`
Given a dwelling configuration, the layer must be able to produce the set of packages the
product offers for it, each with an id computed by the same rule as a package supplied to it
directly — so that a package reached either way collapses to the same identity. Enumerating a
set and calculating one package are separate operations; whether the C# service asks for the
set and then requests calculations one at a time, or something else, is its decision. How many
packages, and by what selection rule, is Q5.

**R7 — Report the fate of every field.** `[given; spec.md §6, strengthened]`
Every leaf field present in a configuration must appear exactly once in a machine-readable
translation report with a status of `used`, `approximated`, `defaulted`, `ignored` or
`non_simulation`. The report is written beside the results of the calculation it describes, so
that a caller holding a result also holds the account of how faithfully it was simulated. This
is the mechanism that keeps §4.2's counts honest as HiSim's fidelity grows.

**R8 — Derive the KPI payload.** `[given; contract Kpis]`
The layer must compute every `Kpis` field the amended contract keeps, from the finished run,
and must state for each the boundary and period it was computed over. A field it cannot compute
must be absent, never zero.

**R9 — Derive the cost payload.** `[given; contract Costs]`
The layer must compute every `Costs` field the amended contract keeps, using
`hisim/economics`, and must emit the three values of a `Range` from one coherent evaluation
slot each (see A9).

**R10 — Determinism.** `[proposed; from the contract's own caching claim]`
Two runs of the same home inventory and package must produce byte-identical energy-system
files, byte-identical realized records and equal result payloads. Nothing that varies per
request — a timestamp, a job id, a machine name — may enter the emitted files.

**R11 — Refusal.** `[given; contract CalculationState.refused]`
The layer must distinguish "this input is malformed" (a validation error), "this input is
well-formed but not simulable" (a refusal, with a machine-readable code and the offending
fields) and "the simulation failed" (a failure). It must decide refusals before starting a
simulation wherever the condition allows it.

**R12 — Two calculation tiers.** `[given; contract fast-estimate / detailed-simulation]`
The layer must offer two tiers with a stated difference in method and a stated difference in
accuracy. A `fast-estimate` result and a `detailed-simulation` result for the same package must
be distinguishable in the payload. What the fast tier *is* is Q6.

**R13 — A file-based container interface is the whole interface.** `[given 2026-08-27; supersedes the in-memory phrasing of R13, which assumed a Python caller]`
The layer's only caller is a C# service that supplies a house configuration as a file and
collects the results as files, in a **reused** container that serves many calculations in
succession. The interface is therefore a process contract, not a Python API, and it must be
specified as one: where the configuration is read from, what is written where, and what
signals that a calculation succeeded, was refused or failed. It must be usable by a caller
that cannot import Python and cannot inspect a traceback.

**R13.1 — A reused container must not carry state between calculations.** `[given 2026-08-27; replaces the "no ambient state" phrasing, which assumed a fresh process per run]`
The *n*-th calculation in a container's life must produce exactly what it would have produced
as the first. Because the process now outlives the calculation, this is a positive requirement
on the implementation — state must be actively reset between calculations — not, as originally
drafted, a prohibition on reading ambient state. It is what makes R10's determinism hold across
a reused process rather than only within one run, and C13 records the specific hazards it has to
defeat. Because calculations never overlap (R13.5), a reset at a single point in the lifecycle
satisfies this; nothing has to become concurrent.

**R13.3 — Reuse must be bounded and self-limiting.** `[proposed; follows from R13.1]`
A long-lived container accumulates result directories, memory and cached data across
calculations. The layer must either bound that growth or state the limit at which the caller
should recycle the container, so the C# side can act on it rather than discover it in
production.

**R13.4 — A calculation writes only where it declared it would.** `[given 2026-08-27, owner]`
Everything a calculation produces must land inside its declared output location, so that
deleting that location returns the container to its pre-calculation state. Nothing may be
written beside the package, into the working tree, or into a shared temporary area. The check
is a filesystem-cleanliness assertion after a calculation — the owner's plan is a
`git status --porcelain`-style test on the C# side, which is well aimed: `ResultPathProviderSingleton`'s
default `base_path` is `Path(__file__).resolve().parent.parent / "results"`
(`hisim/result_path_provider.py:105`), i.e. **inside the repository**, so a working-tree check
catches precisely the residue that is easiest to write by accident.

Note the boundary. R13.4 catches *filesystem* residue and is worth having for that alone; it
cannot see the in-process state of C13, which leaves no file behind. The two controls are
complementary and neither substitutes for the other — R13.4 is verified by AC11.5, R13.1 by
the reuse identity test of AC11.1.

**R13.5 — Single occupancy is a stated precondition, not an accident.** `[given 2026-08-27, owner]`
A container serves one calculation at a time (Q10). The layer may therefore assume it is the
only calculation in its process, and correspondingly it must **state that it is not safe to run
two concurrently** — in the container's own documentation, so that neither the C# side nor a
later HiSim change mistakes the absence of a crash for thread-safety. Parallelism is obtained by
running more containers, which makes per-container memory the scaling limit and is what R13.3
must bound.

**R13.2 — Failures are reportable to a caller that cannot read Python.** `[proposed; follows from R11]`
A refusal, a validation error and a crash must each be distinguishable from the container's
output alone — an exit status plus a structured document — without the C# service parsing a
log. This is R11's requirement restated at the process boundary, and it is where R11 actually
gets tested.

**R14 — Version stamping.** `[proposed; from the contract's immutable caching]`
Every finished result must carry the version of the calculation that produced it, covering the
translator, the base files, the component models and the cost database. The container image
digest is a candidate for this stamp, since it covers all four by construction; whether it is
sufficient depends on how the image is built (Q9).

**R15 — Compatibility: nothing else in HiSim regresses.** `[given; epic C1/E7]`
The golden suites must pass unchanged. The v1 translator and its contract may be deleted only
once the replacement is accepted.

### 8.2 API contract (A)

Each item names a defect or a gap in `roadmap/renovisor/openapi.yaml` v0.3.0-draft that
prevents HiSim from satisfying it as written.

**A1 — Every field needs a declared consumer class.** `[proposed; field_inventory §1]`
Each `HomeInventoryInput` field must be tagged in the contract as *simulation input*,
*cost/scheduling input*, or *informational*. Today 27 fields are non-simulation and 8 have no
consumer at all, with nothing in the contract distinguishing them from the 24 that drive the
physics.

**A2 — Remove or reclassify the 8 fields with no consumer.** `[proposed; field_inventory §1 rows 2, 3, 41, 43, 45, 52, 56, 58]`
`location.region`, `location.eircode_or_postcode`,
`photovoltaics.remaining_performance_in_percent`, `battery_storage.power_in_watt`,
`battery_storage.state_of_health_in_percent`, `electric_vehicles[].model`,
`electric_vehicles[].charging_location`, `fossil_vehicles[].model`. Each is either removed, or
kept with an explicit statement that it does not affect any calculation.

**A3 — State that occupancy composition is approximated.** `[proposed; 66 LPG households]`
`residents_count` × `residents_type` × `residents_employment_status` spans far more
combinations than the 66 catalogue households HiSim can simulate. Either the contract offers a
closed enum of supported compositions, or it states that the composition is matched to the
nearest catalogue household and the match is reported.

**A4 — State the weather basis in the result.** `[proposed; LocationEnum IE → Dublin 2019 NSRDB]`
A result depends on which weather dataset and year it was simulated against; the contract has
no field for it and no way to select it. A finished result must name its weather basis, or the
contract must declare it fixed per country and part of the calculation version (R14).

**A5 — Reconcile mileage with `travel_route_set`.** `[proposed; field_inventory §2]`
v0.3 dropped v1's `kmPerYear` but kept per-km consumption. A vehicle's annual energy is
therefore driven by an *occupancy* field. Either restore a per-vehicle mileage field with a
stated consumer, or state that mileage comes from `travel_route_set`.

**A6 — Give `Measure` a closed vocabulary.** `[proposed; contract Measure schema]`
`Measure` is `{type: string, material_id, subtype, thickness_mm, capacity_kw, alternatives}`
with `type` unconstrained and no target element. The translator must know which building
element a measure changes and to what value. Required: an enumerated `type`, a target element
where one applies, and a stated rule for turning `material_id` + `thickness_mm` into a U-value.

**A7 — `Material` must carry thermal properties.** `[proposed; follows from A6]`
`/materials` returns `{id, name, description, category, image_url}`. If a measure is specified
as a material and a thickness, the same catalogue must carry the thermal conductivity that
makes the conversion possible, and it must be the *same* catalogue the calculation uses.

**A8 — State the envelope precedence rule.** `[proposed; field_inventory rows 11, 13–22]`
`retrofit_status` (a TABULA variant) and `envelope_details` (ten explicit U-values and areas)
are two sources of truth for the same envelope. The contract must say which wins, per element,
when both are given.

**A9 — Redefine `Range` as a coherent evaluation slot.** `[proposed; hisim/economics/uncertainty.py:Slot]`
The cost engine's band is `LOW` / `BEST_ESTIMATE` / `HIGH`, where a slot is a property of the
whole evaluation — every cost parameter at its minimum and every revenue parameter at its
maximum within one slot. The contract's `{low, normal, high}` names the middle slot differently
and says nothing about coherence, which invites a reader to mix slots across fields. Rename
`normal`, and state that the three values of every `Range` in one `Costs` object come from the
same slot.

**A10 — Fix `self_sufficiency_in_percent`'s type.** `[proposed; contract Kpis]`
Declared `string`; it is a number.

**A11 — State precedence between given sizes and automatic sizing.** `[proposed; field_inventory rows 12, 29]`
`heating_system.power_in_watt`, `max_thermal_building_demand_in_watt` and the storage volumes
compete with HiSim's sizing laws, which compute the same quantities. The contract must say
whether a stated value pins the size or is only a hint.

**A12 — Resolve the 10 KPI fields with no HiSim source.** `[proposed; field_inventory §3]`
`energy_label`, `disruption_days_by_level` (4), `indoor_air_quality`,
`thermal_insulation_effect`, `summer_heat_protection`, `comfort.heating`, `comfort.cooling`.
Each must be dropped, deferred with a stated owner outside HiSim, or specified precisely enough
to be implemented. See Q7.

**A13 — Resolve `property_value_increase_in_percent`.** `[proposed; field_inventory §4]`
No model, no data source, and no evident path to one. Recommend removal.

**A14 — Disambiguate `energy_demand_kwh` and `emissions_kg_co2`.** `[proposed; field_inventory §3]`
Both need a stated system boundary (delivered / final / primary; which carriers) and a stated
period. HiSim's KPIs are "for simulated period", which is not a contract-level concept.

**A15 — Carry the calculation version in a finished result.** `[proposed; follows R14]`
A result cached `immutable, max-age=31536000` under a hash that covers only the inputs cannot be
corrected when the model changes. Either the version enters the hash, or results are namespaced
by version, or the immutability claim is weakened.

**A16 — Refusals need a code, not prose.** `[proposed; contract CalculationStatus.reason]`
`reason` is a free-text string. A frontend that must explain "no Irish archetype exists for a
1988 multi-family building" needs an enumerated reason code plus the offending field paths.

**A17 — Reconcile per-user endpoints with content addressing.** `[proposed; contract internal defect]`
`GET /homeinventories` is summarised as "list all home inventories **for the current user**" and
`DELETE /homeinventories/{hash}` can return `403 Forbidden` ("valid token, wrong user"), yet
`security: []` makes both anonymous and the store is content-addressed and deduplicated, so one
inventory may belong to several users. Deleting it for one user would remove another's. The
contract must state whether the store is per-content or per-user, and what `DELETE /users/me`
cascades to.

**A18 — State the simulation parameters the tiers use.** `[proposed; follows R12]`
The period, resolution and weather year of each tier are part of what makes a result what it is.
They are not in the contract, and the contract has no field through which a caller could set
them — which is correct, but then they must be pinned by the contract version.

**A19 — Note the enum coupling to HiSim internals.** `[proposed; §4.4]`
`heating_system.system`, `heat_distribution_system` and the `envelope_details` field names are
HiSim's own identifiers. The contract should say so, so that neither team renames one by
accident. The epic's decision EQ1 freezes such names at P5 acceptance; before then they are
free to change.

**A20 — State the Irish scope.** `[proposed; 0 IE subsidy catalogues, 0 IE tariffs, 0 IE MFH archetypes]`
`GrantScheme` is Ireland-first by its own description, but nothing in the contract says which
country codes are actually supported, and `tabula_building_type` offers `MFH`, which has no
Irish archetype. The contract must carry the supported set explicitly.

## 9. Constraints, invariants and assumptions

### Known constraints

- **C1** `[given; epic C1/E7]` Golden parity: identical concrete inputs produce identical
  results before and after any change made here.
- **C2** `[given; epic EAC5]` `ModularHouseholdConfig`, `EnergySystemConfig` and
  `ArcheTypeConfig` are deleted at P5. Nothing built here may depend on them.
- **C3** `[proposed; roadmap/declarative_energy_systems/p2_file_format_requirements.md]` Energy-system files are YAML only; JSON is
  not an accepted input format for them.
- **C4** `[proposed; plan.md P4]` The base files of R2 cannot be written until the P4 component
  sweep converts ≈13 further modules. Five of ten heating systems and every optional subsystem
  (PV, battery, EMS, buffer storage, DHW storage, cars) are blocked on it.
- **C5** `[proposed; hisim/components/weather.py:LocationEnum]` One weather station per country
  code; `IE` is Dublin, NSRDB 15-minute, year 2019.
- **C6** `[proposed; hisim/inputs/housing/data_processed/episcope-tabula.csv]` The Irish TABULA
  table has 124 rows in types `SFH`, `TH` and `AB` only. Some rows carry zero door or window
  area and crash the `Building` component (`hisim/renovisor/spec.md` §4.2 workaround).
- **C7** `[proposed; utspclient.helpers.lpgdata]` Occupancy is one of 66 catalogue households
  and one of 6 travel-route sets; arbitrary compositions cannot be simulated.
- **C8** `[proposed; hisim/subsidy_catalog/, hisim/cost_database/tariffs/]` No Irish subsidy
  catalogue and no Irish tariff exist. `grant_euro` and `energy_costs_euro` cannot be computed
  for Ireland until they do.
- **C9** `[given; epic EQ1 decision 2026-08-26]` Preset, field and fact names may change freely
  until P5 is accepted; from then a rename is a breaking change.
- **C10** `[proposed; contract description]` The contract's envelope — endpoint shapes, status
  model, error format, auth, IDs, naming — is co-owned and changes need frontend sign-off. A17
  is the only §8.2 item that touches it; the rest are payload changes the backend owns.
- **C11** `[given 2026-08-27, owner]` The calling service is a C# application in a separate
  repository. It invokes HiSim through a container that is **reused across calculations**,
  passing a house configuration in as a file and collecting results as files. HiSim's
  deliverable is therefore a container image with a file-based process contract, and no part of
  the contract's HTTP behaviour — caching, coalescing, hashing, auth, polling — is HiSim's to
  implement or to test. The container's lifetime spanning many calculations is what makes C13
  blocking.
- **C12** `[proposed; follows from C11]` The interface crosses a language boundary. Anything
  HiSim wants the caller to act on — a refusal reason, a translation report, a version stamp —
  must be a structured document the C# side can deserialise, not a Python object and not a log
  line.
- **C13** `[proposed; surveyed 2026-08-27]` **HiSim is not currently safe to run twice in one
  process.** Container reuse (C11) makes this a blocking constraint rather than a latent one.
  The survey found:
  - `hisim/sim_repository_singleton.py:SingletonSimRepository` is a process-wide singleton,
    written from **29 call sites across 11 component modules**, and **nothing in
    `hisim_main.py`, `simulator.py` or `energy_system/executor.py` resets it between runs**.
    `simulator.py:367` clears the *per-simulator* `SimRepository`, which is a different object.
  - Its own `clear()` does `del self.my_dict`, so a second run in the same process meets a
    deleted attribute rather than stale data. `reset()` exists beside it and its docstring says
    it is for callers who "need a clean slate … across successive simulations" — the hazard is
    known, the fix is written, and no production path calls it.
  - `result_path_provider.py:ResultPathProviderSingleton` captures
    `datetime.datetime.now()` in `__init__`, so the **first** calculation in a container fixes
    the timestamp every later one uses. It has a `reset()` classmethod; nothing in a run calls it.
  - `tests/test_singleton_sim_repository.py:211` clears `SingletonMeta._instances` by hand,
    which is the test suite working around the same problem.

  Beyond the two singletons, module-level caches (weather data, the TABULA table, pvlib
  databases), logging level, matplotlib global state and RNG seeding are all candidates for the
  same failure mode and have not been surveyed. Whatever mechanism satisfies R13.1 must cover
  them, and the survey above is a starting point, not a complete list.

  **Bounded by Q10** `[decided 2026-08-27]`: since calculations never overlap, every item above
  is a *staleness* problem with a reset as its fix, not a *race* with a redesign as its fix. The
  epic's parking-lot entry "runtime half of `SingletonSimRepository` … needs its own redesign,
  probably proper wiring" stays parked and is not a dependency of this work.

### Assumptions requiring confirmation

- **A-1** `[superseded 2026-08-27]` ~~The HTTP service is built outside HiSim.~~ Confirmed by
  the owner and promoted to C11; the service is a C# application in another repository.
- **A-2** `[proposed]` Ireland is the only country that must work; the mechanism must not
  preclude others.
- **A-3** `[proposed]` The 27 non-simulation fields are genuinely wanted by the product, and
  the work of consuming them (a scheduling and replacement model) is planned somewhere. If not,
  they are 27 fields collected for nothing.
- **A-4** `[proposed]` The frontend can tolerate absent KPI fields (R8) rather than requiring
  every field to be present.

## 10. Acceptance criteria

| ID | Criterion | Verifies |
|---|---|---|
| AC1 | Every field of the amended `HomeInventoryInput` is classified in the contract and appears in the translation report of any calculation whose configuration carries it. | R7, A1, A2 |
| AC2 | For each supported `heating_system.system` value, a checked-in base energy-system file exists, loads, resolves, builds and runs. | R1, R2, C4 |
| AC3 | A parametrised energy-system file differs from its base file only in `config` values, `constructor` arguments and group flags — verified mechanically by diffing against the base. | R4 |
| AC4 | An inventory plus a package whose measures change the roof produces a file whose `building.config.roof_u_value_in_watt_per_m2_per_kelvin` carries the measure's value. | R5, A6, A7 |
| AC5 | Translating and running the same inventory and package twice produces byte-identical parametrised energy-system files and realized records, and equal result payloads. | R10, G3 |
| AC6 | Re-executing a stored realized record reproduces the run bit-for-bit. | R10, UC5 |
| AC7 | An `IE` + `MFH` configuration is refused, distinguishably from a crash, with an enumerated reason naming the offending field, before any simulation starts. | R11, A16, UC3 |
| AC8 | Every `Kpis` and `Costs` field the amended contract keeps is produced by a finished calculation, and each is traceable to a named HiSim KPI or cost-engine output. | R8, R9, A12, A13, A14 |
| AC9 | The three values of a `Range` in one `Costs` object come from one evaluation slot; a test detects a mixed-slot object. | R9, A9 |
| AC10 | A finished result carries a calculation version, and changing any of the translator, base files, component models or cost database changes it. | R14, A15 |
| AC11 | A calculation runs end to end through the file interface alone, driven by a caller that imports no Python: configuration in, results out, outcome signalled. | R13, C11 |
| AC11.1 | **The reuse identity test.** A calculation run as the *n*-th in a container's life produces byte-identical outputs to the same calculation run as the first — verified for a sequence of *differing* calculations, since identical ones would not surface the leak. | R13.1, R10, C13 |
| AC11.2 | A refusal, a validation error and a crash are told apart from the container's outcome signal and output document, with no log parsing. | R13.2, R11, A16 |
| AC11.3 | The same image and the same input, run on two different machines, produce equal results and equal version stamps. | R13.1, R10, R14 |
| AC11.4 | Resource growth over a long sequence of calculations is bounded, or the recycle limit is stated and enforced. | R13.3 |
| AC11.5 | After a calculation and the deletion of its output location, a working-tree cleanliness check (`git status --porcelain` or equivalent) reports nothing — no stray `results/`, no file written beside the package. | R13.4 |
| AC11.6 | The container's documentation states that it serves one calculation at a time and is not safe to run two concurrently. | R13.5 |
| AC12 | The `fast-estimate` and `detailed-simulation` results for one package are both produced and are distinguishable in the payload. | R12 |
| AC13 | All golden suites pass; no result of an existing setup changes. | R15, C1 |
| AC14 | The contract validates against an OpenAPI 3.1 validator after amendment, and every `x-contract-status: provisional` schema names its owner. | A1–A20 |

## 11. Open questions and decisions

### Decision register

| ID | Decision | Date, owner |
|---|---|---|
| Q1 | HiSim supplies a calculation **library**, packaged as a container image. The service is a C# application in a separate repository. HiSim owns no HTTP, store, queue or auth. Rejected: HiSim hosting the service; a thin reference service in this repository. | 2026-08-27, owner |
| Q9 | The interface is **files in, files out**, and containers are **reused** across calculations to avoid start-up cost. Rejected: stdin/stdout JSON; a hybrid; one container per calculation. | 2026-08-27, owner |
| Q10 | A container runs **one calculation at a time**; parallelism comes from running more containers. Rejected: concurrent calculations in threads; concurrent calculations in subprocesses. | 2026-08-27, owner |

Together these bound the isolation problem sharply. Reuse (Q9) means HiSim must survive being
run repeatedly in one process, which it does not today (C13); single occupancy (Q10) means the
fix is a **reset between calculations**, not thread-safety across 11 component modules. The
`SingletonSimRepository` redesign stays in the epic's parking lot.

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

**Q2 — Does the calculation version enter the content hash, namespace the results, or weaken the immutability claim?** · blocks R14, A15, AC10

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

*Recommendation.* (a) for correctness, with the version exposed in the finished result so a
frontend can display it. The expense is real but a wrong renovation cost cached for a year is
worse.

*Blocks.* R14, A15, AC10.

---

**Q3 — Is the home-inventory field set re-anchored on energy-system names, or frozen as a translator-owned façade?** · blocks A19, R4, C9

*Context.* §4.4: `envelope_details` is field-for-field `ArcheTypeConfig`,
`heating_system.system` is `hisim.loadtypes.HeatingSystems`, `heat_distribution_system` carries
`lt.HeatDistributionSystemType`'s literal strings. `ArcheTypeConfig` is deleted at P5 (epic
EAC5). The epic's EQ1 decision leaves HiSim's names free to change until P5 is accepted, then
freezes them.

*Options.* (a) **Re-anchor on the energy-system file's names** — the contract's inventory tracks
whatever the converted component configs call these fields. Consequence: no translation table to
maintain; the contract inherits HiSim's freeze at P5 and every later component rename becomes a
public API break. (b) **Freeze the contract's field set as a façade** the translator maps onto
whatever HiSim calls things. Consequence: a mapping table to maintain and test, but the two
naming spaces can evolve independently — and the RenoVisor frontend never has to care that a
HiSim class was renamed. (c) **Case by case**, keeping the coincidental matches and mapping the
rest. Consequence: the worst of both; nobody can tell which fields are coupled.

*Recommendation.* (b). The contract is a public interface with an external co-owner; HiSim's
component field names are not, and the epic explicitly plans to keep renaming them through P4.
The mapping table is exactly what R7's translation report already has to produce.

*Blocks.* A19, R4, C9.

---

**Q4 — What besides `heating_system.system` selects the base file?** · blocks R2, R3, AC2

*Context.* R2 asks for one base file per heating system: ten. But `energy_system_mockup.yaml`'s
open point O2 establishes that the EMS-less variant of a system must be a *separate base file*,
not a disabled group, because with the EMS off nothing feeds the electricity meter and adding
the direct feeds would double-count while the EMS is on. Solar thermal is similar:
`used_for_space_heating` and `used_for_dhw` change which storage the collector feeds, and a
group is a presence flag, not a rewiring rule (epic E4).

*Options.* (a) **Ten base files, EMS always present.** Consequence: simplest; every simulated
household carries an EMS whether or not it has a battery, which changes results for the
majority of inventories that have neither PV nor battery. (b) **Twenty base files** — each
heating system with and without the EMS. Consequence: correct, and twenty files to author,
review and keep in step. (c) **Ten base files plus a `direct_metering` group** whose enabled
state is the negation of `battery_and_ems`. Consequence: fewer files, but the constraint "these
two groups are mutually exclusive" cannot be expressed in the format (mockup O2 says so
explicitly) and would have to be enforced by the translator — a rule living outside the file it
governs.

*Recommendation.* (b), which is the mockup's own recommendation. Twenty authored files is a
real cost; a silently double-counted meter is a wrong number.

*Blocks.* R2, R3, AC2.

---

**Q5 — What generates the 100+ packages, and who owns the rule?** · blocks R6, UC1

*Context.* The contract documents `GET …/packageset` as returning "100+ packages once finished;
the frontend picks three to display". Nothing in HiSim generates renovation packages, scores
them or ranks them; the v1 translator ran one simulation per invocation and the caller chose the
measures. If each of 100 packages needs a detailed simulation, one package set is 100 full-year
simulations.

*Options.* (a) **A combinatorial generator in the translation layer**, over an enumerated
measure vocabulary (A6), with the product's selection rule expressed as data. Consequence:
HiSim owns a product decision; the rule needs a product owner to state it. (b) **The package set
is supplied to the backend** as a catalogue the product team maintains, and the backend only
prices it. Consequence: clean separation; requires the catalogue to exist. (c) **The package set
is computed by the fast tier only**, with detailed simulation reserved for packages the user
opens. Consequence: makes the cost of (a) or (b) bearable — this is orthogonal to who owns the
rule, and probably required regardless.

*Recommendation.* (b) for ownership plus (c) for cost. HiSim should price and simulate packages,
not decide which renovations to recommend.

*Sharpened by Q1's answer (2026-08-27).* With calculations run as containers started by the C#
service, a package set of 100+ packages is 100+ container starts unless the unit of work is
widened (Q9). That makes (c) not merely advisable but close to forced, and it moves the
batching decision to the C# side, which owns scheduling.

*Blocks.* R6, UC1.

---

**Q6 — What is the `fast-estimate` tier?** · blocks R12, A18, AC12

*Context.* The contract has two calculation resources per package with identical shapes. HiSim
has one method: a time-step simulation, 15-minute resolution, a full year for the building-sizer
setups. There is no reduced-order path, no cached-response surrogate and no steady-state
calculation in the repository.

*Options.* (a) **A shorter simulation** — one representative week or a set of design days,
scaled up. Consequence: cheap to build (it is a different `*.simulation.yaml`), and the accuracy
loss on annual energy is unquantified. (b) **A steady-state or degree-day calculation** beside
HiSim. Consequence: genuinely fast, a second physics implementation to validate against the
first. (c) **A surrogate fitted to detailed runs.** Consequence: fastest, needs a training
corpus and a stated validity domain, and it is a research project. (d) **The same detailed
simulation, with the tiers differing only in queue priority.** Consequence: no accuracy question
at all; the contract's two resources become a scheduling hint, and the frontend's "fast" promise
becomes false.

*Recommendation.* (a) as the first implementation, with the accuracy loss measured against the
detailed tier on a fixture set and stated in the result. It is the only option that is both
cheap and honest about what it is.

*Blocks.* R12, A18, AC12.

---

**Q7 — Who owns the 10 KPI fields HiSim cannot compute?** · blocks A12, R8, AC8

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

*Blocks.* A12, R8, AC8.

---

**Q8 — Does the Irish cost data get built, or does Ireland ship without grants and energy costs?** · blocks R9, A20, C8, AC8

*Context.* `hisim/subsidy_catalog/` contains `AT.json` and `DE.json`;
`hisim/cost_database/tariffs/` contains one German tariff. The contract's `GrantScheme` enum is
Irish (`warmer_homes_scheme`, `enhanced_rate_first_time_buyer`), and `Costs` carries
`grant_euro` and `energy_costs_euro` as required parts of every package.

*Options.* (a) **Build `IE.json` and an Irish tariff** to the same standard as the German
catalogue, sourced and dated. Consequence: the contract works as drafted; someone must do the
data work and own its currency as SEAI schemes change. (b) **Ship without them**, serving
`grant_euro` and `energy_costs_euro` as absent. Consequence: the product's central number —
what a renovation actually costs after grants — is missing. (c) **Hard-code a small Irish
scheme set** in the translation layer. Consequence: fast, and it puts policy data outside the
catalogue that exists precisely to hold policy data.

*Recommendation.* (a). It is the only option under which the contract's `Costs` object means
what it says, and the catalogue mechanism already exists — this is data entry with provenance,
not new machinery.

*Blocks.* R9, A20, C8, AC8.

---

**Q9 — What exactly does the container take in and give back?** · `[answered 2026-08-27]` — **files in, files out, container reused.** Entry kept for the alternatives it rejected.

*Context.* Q1's answer fixes the mechanism (the C# service starts a container with a house
configuration) but not its shape, and every detail of that shape is a thing the C# side has to
code against. Four sub-decisions are entangled and should be taken together: **how input
arrives** (a mounted volume with a file at a fixed path, an argument, or stdin), **how output
leaves** (files in a mounted result directory, or one JSON document on stdout), **what the exit
status means** (R13.2 needs success, refusal, validation error and crash to be four
distinguishable outcomes), and **what the unit of work is** — one package per container, or one
container computing a whole package set. The last is not cosmetic: at 100+ packages per set
(contract, `packageset`), the choice is between 100 container starts and one, and HiSim's import
time is paid once per start.

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
package, one tier). It matches what HiSim already produces, keeps the R7 report and the R10
record as first-class files rather than blobs, and leaves batching to the C# side, which is
where the scheduling decisions already live. The package-set cost this implies is real and
belongs in Q5's answer, not here.

*Answer.* `[decided 2026-08-27, owner]` **(a) files in, files out**, with the unit of work one
calculation — but the container is **reused** across calculations rather than started per
calculation, which is what pays off the start-up cost that made the per-calculation model
expensive. The C# side additionally provides a function to delete a result directory outright
and a working-tree cleanliness test after each calculation (R13.4, AC11.5).

*What the answer costs.* Reuse converts a cheap guarantee into an expensive one. With a fresh
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

## 12. Glossary

**Home inventory** — the contract's description of a dwelling as it stands today; content-addressed,
immutable, identified by the hash of its canonicalised form (`hi_id`).
**Package** — a set of renovation measures plus grant selections, a schedule and financing,
identified by the hash of its own contents (`pkg_id`).
**Package set** — the 100+ packages the backend derives from one home inventory.
**Fast estimate / detailed simulation** — the contract's two calculation tiers for one package.
**Refused** — a terminal calculation state meaning the input was well-formed but not simulable,
as distinct from a validation error and from a failure.
**Base file** — a checked-in, hand-authored energy-system file, one per supported heating system,
which the translator specialises by writing overrides and flipping group flags. The set of base
files is the complete set of systems RenoVisor can simulate.
**Parametrise** — to take a base file and fill in a request's values: `config` overrides,
`constructor` arguments and `groups.<name>.enabled` flags, and nothing else (R4). Deliberately
not "generate": no energy system is ever authored from a request, so every system that runs has
been read by a person.
**Translation report** — the per-request record of what happened to every field of the request:
`used`, `approximated`, `defaulted`, `ignored` or `non_simulation`.
**Realized record** — the concrete, re-executable copy of an energy system that a run writes back,
with every preset expanded and every `AUTO` replaced by the number it resolved to.
**Slot** — one coherent evaluation world of the cost engine (`LOW`, `BEST_ESTIMATE`, `HIGH`), in
which every cost parameter sits at one end of its band and every revenue parameter at the other.
**Non-simulation field** — a contract field that is a legitimate product input but drives cost,
scheduling or grants rather than the physics.
