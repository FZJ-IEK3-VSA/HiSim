# RenoVisor backend: translation layer and API contract — requirements

**Status:** draft
**Date:** 2026-08-27
**Author(s):** assistant (all `[proposed]` items, evidence survey) · Noah Pflugradt (owner; all `[given]` items)
**Reviewers:** HiSim core team · RenoVisor frontend team (§8.2 only)
**Supersedes / related:** `hisim/renovisor/spec.md` (v1 translator spec — superseded by this
document's §8.1, kept until the replacement is accepted) · `roadmap/renovisor/openapi.yaml`
(RenoVisor API v0.3.0-draft, the contract under review) ·
`roadmap/declarative_energy_systems/epic.md` and its `plan.md` P5 row (this work *is* the
RenoVisor half of P5) · `roadmap/cost-spec-v2.md` (the cost engine the result payloads need)
**Companions:** `roadmap/renovisor/field_inventory.md` (the counted survey) ·
`roadmap/renovisor/mockups/heat_pump_household.energy_system.yaml` (what the translator emits)

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

**Required.** (a) A translation layer that turns a home inventory plus a package into an
energy-system file, runs it, and derives the contract's result payloads — replacing both the
`ModularHouseholdConfig` path and the "upload raw files, let the server interpret them"
arrangement. (b) A revised API contract in which every field either has a HiSim consumer, is
declared non-simulation, or is removed — and in which determinism, versioning and the meaning of
a cost `Range` are stated, because the contract promises results that are cacheable forever.

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
   one-shot CLI that pushes to a URL.
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
sign off §8.2 changes). The RenoVisor backend service that will host the translation layer. The
declarative-energy-systems epic (this is its P5 RenoVisor track). The building sizer and HPC
harness, which share the base-file mechanism and will inherit whatever is built here.

**Hypothetical, and therefore not a constraint.** A webtool, a template creator, other
countries beyond Ireland.

### 4.6 Distinguishing current from required

| | |
|---|---|
| **Current behavior** | A CLI translates a v1 request into a `ModularHouseholdConfig`, picks one of ten Python setups, runs it in-process, and POSTs matched result files plus a mapping report to a URL. |
| **Required behavior** | A library turns a home inventory and a package into an energy-system file, runs it deterministically, derives the contract's `kpis` and `costs`, and returns them to a service that serves them under a content-addressed URL. |
| **Assumption** | The service (HTTP routing, job coalescing, caching, auth, storage) is built outside HiSim; HiSim supplies the calculation library. Confirm — Q1. |

## 5. Goals and non-goals

**Goals**

- **G1** Every field the contract keeps has a named consumer: a HiSim configuration field, an
  energy-system file element, a cost-engine input, or an explicit "non-simulation" declaration.
- **G2** The translation layer emits energy-system files, not `ModularHouseholdConfig`, so that
  the epic's EAC5 can be met and a request's simulation is a readable, re-executable artifact.
- **G3** Identical inputs produce identical results, bit for bit, so the contract's
  content-addressing and its `immutable` caching are sound rather than aspirational.
- **G4** The backend derives the contract's result payloads; no consumer of the API needs to
  interpret HiSim output files.
- **G5** A request that cannot be simulated is refused with a machine-readable reason, never
  answered with a plausible-looking wrong number.
- **G6** The contract and the translator are amended together, in one review, so neither is
  signed off against a version of the other that does not exist.

**Non-Goals**

- The HTTP service itself: routing, auth, storage, job coalescing, PDF rendering (§4.6, Q1).
- Countries other than Ireland. The mechanism must not preclude them; only IE is verified.
- Reworking the KPI computation or the cost engine beyond what the payloads need.
- The building sizer and HPC harness tracks of P5, though they share the base files.
- A renovation-*advice* engine: which packages are worth recommending is a product question;
  this document requires only that a package set can be produced and priced (see R6, Q5).

## 6. Use cases and mockups

### UC1 — Create a home inventory and get its package set

*Input.* `POST /homeinventories` with the 79-field body; then
`GET /homeinventories/{hi_id}/packageset`.
*Expected.* The POST returns a content hash. The GET returns `202 queued`, then `202 running`,
then `200` with a `PackagesetResult` whose packages each carry `kpis` and `costs`. A second
identical POST returns the same hash; a second GET after completion is served from cache
without recomputing.

### UC2 — One package, simulated in detail

*Input.* `POST /homeinventories/{hi_id}/packages` with a `PackageDefinition`; then
`GET /homeinventories/{hi_id}/packages/{pkg_id}/detailed-simulation`.
*Expected.* The package's measures are applied to the inventory, an energy-system file is
emitted, HiSim runs it, and the KPI and cost payloads are derived from the run.
The emitted file is the mockup
`roadmap/renovisor/mockups/heat_pump_household.energy_system.yaml` — **the external
representation this document is chiefly about**. Its footer (M1–M5) states what it pins down:
the translator writes only overrides and group flags; ten base files are needed and the
EMS-less variants double that to twenty; every override traces to one named contract field.

### UC3 — A request that cannot be simulated

*Input.* A home inventory with `country_code: IE` and `tabula_building_type: MFH`.
*Expected.* `200` with `status: refused` and a reason the frontend can render — the Irish TABULA
table has 48 `SFH`, 51 `TH` and 19 `AB` rows and **no** `MFH` row. Not a 500, not a silently
substituted archetype.

### UC4 — An inventory field with no consumer

*Input.* An inventory stating `battery_storage.state_of_health_in_percent: 90`.
*Expected.* The simulation ignores it (HiSim has no capacity-fade model), and the run's
translation report says so in as many words. Under R7 no field is silently dropped.

### UC5 — Re-running a stored result

*Input.* The realized record written by a finished calculation, re-executed.
*Expected.* Byte-identical results. This is the epic's identity test applied to RenoVisor's
output, and it is what makes the contract's `Cache-Control: immutable` defensible.

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

**R1 — Emit energy-system files.** `[given; epic EAC5, plan P5]`
The translation layer must produce a `*.energy_system.yaml` document plus a
`*.simulation.yaml` parameters file for each calculation, and must not construct
`ModularHouseholdConfig`, `ArcheTypeConfig` or `EnergySystemConfig`.

**R2 — Base files, one per heating system.** `[given; plan P5 "base file per heating system"]`
A checked-in base energy-system file must exist for each `heating_system.system` value the
contract offers. A base file is authored and reviewed as a system, not generated.

**R3 — Selection is a lookup, not a computation.** `[proposed]`
Which base file a request uses must be a total function of a stated set of inventory fields, so
that the answer is inspectable without running anything. Currently that set is
`heating_system.system` plus whichever fields Q4 resolves (EMS presence, solar-thermal use).

**R4 — The translator writes only overrides and group flags.** `[proposed; from mockup M1]`
Beyond the base file's content, a generated energy-system file may differ only by (a) `config`
field overrides, (b) `constructor` arguments, and (c) `groups.<name>.enabled` flags. The
translator must never author `inputs`, `sizing_sources`, or a component entry. Rationale: wiring
and sizing sources are the base file's reviewed content; a translator that writes them is a
second, untested system description.

**R5 — Apply a package's measures to the inventory before emitting.** `[proposed; extends spec.md §3]`
A `PackageDefinition`'s measures must be applied to a copy of the home inventory, and the
resulting inventory is what the emitter reads. Envelope measures must reach the per-element
`BuildingConfig` U-value and area fields — not, as in v1, be folded into the TABULA
refurbishment variant — because those ten fields exist and are directly consumable
(`field_inventory.md` rows 13–22).

**R6 — Produce a package set.** `[given; contract GET …/packageset]`
Given a home inventory, the layer must produce a set of `PackageDefinition`s covering the
measure combinations the product offers, each with an id computed by the same rule as a
user-registered package. How many, and by what selection rule, is Q5.

**R7 — Report the fate of every field.** `[given; spec.md §6, strengthened]`
Every leaf field present in a request must appear exactly once in a machine-readable
translation report with a status of `used`, `approximated`, `defaulted`, `ignored` or
`non_simulation`. The report must be retrievable for any finished calculation. This is the
mechanism that keeps §4.2's counts honest as HiSim's fidelity grows.

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

**R13 — Callable as a library, on in-memory input.** `[proposed; from executor.py:build_energy_system]`
The layer must be usable from a hosting service without a subprocess and without requiring the
caller to place files at particular paths. Note the current limitation:
`hisim.energy_system.loader.load_energy_system` accepts YAML text, but
`build_energy_system` takes a path.

**R14 — Version stamping.** `[proposed; from the contract's immutable caching]`
Every finished result must carry the version of the calculation that produced it, covering the
translator, the base files, the component models and the cost database.

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

### Assumptions requiring confirmation

- **A-1** `[proposed]` The HTTP service, its storage, its job coalescing and its PDF rendering
  are built outside HiSim; HiSim supplies a calculation library (Q1).
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
| AC1 | Every field of the amended `HomeInventoryInput` is classified in the contract and appears in the translation report of any request that carries it. | R7, A1, A2 |
| AC2 | For each supported `heating_system.system` value, a checked-in base energy-system file exists, loads, resolves, builds and runs. | R1, R2, C4 |
| AC3 | A generated energy-system file differs from its base file only in `config` values, `constructor` arguments and group flags — verified mechanically by diffing against the base. | R4 |
| AC4 | An inventory plus a package whose measures change the roof produces a file whose `building.config.roof_u_value_in_watt_per_m2_per_kelvin` carries the measure's value. | R5, A6, A7 |
| AC5 | Translating and running the same inventory and package twice produces byte-identical energy-system files and realized records, and equal result payloads. | R10, G3 |
| AC6 | Re-executing a stored realized record reproduces the run bit-for-bit. | R10, UC5 |
| AC7 | An `IE` + `MFH` inventory returns `refused` with an enumerated reason naming the offending field, before any simulation starts. | R11, A16, UC3 |
| AC8 | Every `Kpis` and `Costs` field the amended contract keeps is produced by a finished calculation, and each is traceable to a named HiSim KPI or cost-engine output. | R8, R9, A12, A13, A14 |
| AC9 | The three values of a `Range` in one `Costs` object come from one evaluation slot; a test detects a mixed-slot object. | R9, A9 |
| AC10 | A finished result carries a calculation version, and changing any of the translator, base files, component models or cost database changes it. | R14, A15 |
| AC11 | The layer can be driven end to end from another process's memory, with no fixed filesystem layout. | R13 |
| AC12 | The `fast-estimate` and `detailed-simulation` results for one package are both produced and are distinguishable in the payload. | R12 |
| AC13 | All golden suites pass; no result of an existing setup changes. | R15, C1 |
| AC14 | The contract validates against an OpenAPI 3.1 validator after amendment, and every `x-contract-status: provisional` schema names its owner. | A1–A20 |

## 11. Open questions and decisions

No decisions have been taken yet; every item below is open.

---

**Q1 — Does HiSim supply a calculation library, or the HTTP service itself?** · blocks R13, A-1, and the scope of everything in §8.1

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

*Blocks.* R13, A-1, and how much of §8.1 is in scope at all.

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
which the translator specialises by writing overrides and flipping group flags.
**Translation report** — the per-request record of what happened to every field of the request:
`used`, `approximated`, `defaulted`, `ignored` or `non_simulation`.
**Realized record** — the concrete, re-executable copy of an energy system that a run writes back,
with every preset expanded and every `AUTO` replaced by the number it resolved to.
**Slot** — one coherent evaluation world of the cost engine (`LOW`, `BEST_ESTIMATE`, `HIGH`), in
which every cost parameter sits at one end of its band and every revenue parameter at the other.
**Non-simulation field** — a contract field that is a legitimate product input but drives cost,
scheduling or grants rather than the physics.
