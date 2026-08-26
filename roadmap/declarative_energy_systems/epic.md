# Epic: declarative energy systems — presets, auto-sizing and re-executable records — requirements

**Status:** in review
**Date:** 2026-08-25
**Author(s):** Noah Pflugradt (owner; all `[given]` items) · assistant (all `[proposed]` items, evidence survey)
**Reviewers:** HiSim core team
**Supersedes / related:** `roadmap/declarative_energy_systems/config_requirements_spec.md` (first draft, superseded); the design documents
`system_docs/config_defaults_spec.md` (branch `config_presets`) and `system_docs/json_scenario_v2_spec.md`
(branch `json_v2`) become *solution-design input* for the phases, not requirements.
**Children:** `roadmap/declarative_energy_systems/plan.md` (phased plan) · `roadmap/declarative_energy_systems/p1_sizing_kernel_requirements.md` ·
`roadmap/declarative_energy_systems/p2_file_format_requirements.md` · P3/P5 documents are written when P1 is accepted.
**Companions:** `roadmap/declarative_energy_systems/sizing_fact_inventory.md` (counted survey) · `roadmap/declarative_energy_systems/energy_system_mockup_minimal.yaml`,
`roadmap/declarative_energy_systems/energy_system_mockup.yaml`, `roadmap/declarative_energy_systems/energy_system_mockup_mfh.yaml` (mockups — the contract for P2).

**Tags:** feature, behavior-change, migration, technical-debt, developer-experience
**Keywords:** energy-system file, YAML, presets, AUTO, sizing facts, component groups, realized record, RenoVisor, building sizer, HPC harness

---

## 1. Abstract

HiSim assembles household energy systems from ~70 configurable components. Systems are defined in Python setup functions or in JSON dumps of every parameter; component sizes come from ~85 hand-written factories and setup-side arithmetic that pass building data around by hand, with cross-component facts travelling through a global repository with silent fallbacks. Files are unreadable, sizing is untraceable, and external consumers depend on a stand-in object instead of the energy system. Required outcome: one declarative energy-system file in which components are named by preset, sizes may be left to be computed from *unambiguously identified* facts, wiring is stated once at the consuming component, and every run writes back a concrete, re-executable record with provenance — delivered in phases whose per-component cost stays inside that component's module.

## 3. Executive Summary

**Problem.** Sizing knowledge is scattered and untraceable; the JSON format is a dump nobody hand-writes; the v1 executor cannot even handle classes with several defaults; RenoVisor and the building sizer use `ModularHouseholdConfig` because no energy system format was usable. The `config_presets` branch introduced presets, `AUTO` and a fact engine, but review (2026-08-25) found its three-way fact scoping too implicit once two components of one class share a house.

**Required.** An energy-system file that declares components, their inputs, their configuration by preset + overrides, and — only where ambiguous — which other component's facts a sized field uses. One principle throughout: **unambiguous → nothing to write; anything else → written out; never inferred.** Delivered as: P1 sizing kernel (Python), P2 file format & executor, P3 recording & setup migration, P4 component sweep, P5 consumer integration.

**Affected.** ~70 component configs, ~50 setups, the v1 executor, three external consumers. **Cost of inaction:** every new component adds a factory pair and a consumer knob; sizing defects stay undiagnosable; MFH and multi-generator systems need a Python setup per variant.

## 4. Context and Current Situation

Detailed evidence lives in the phase documents and `roadmap/declarative_energy_systems/sizing_fact_inventory.md`; the epic keeps the headline numbers.

| Current state | Count / reference |
|---|---|
| Default/scaled config factories | ~85 across 52 modules (`hisim/components/`) |
| Sized fields today | ~35 in 14 components; ~20 plain copies; 5 non-trivial math cases |
| Primary sizing facts | 7, all from the Building; weather location |
| Deepest fact chain | 3 levels, acyclic; **0** readers consume several providers |
| Cross-component facts at run time | `SingletonSimRepository` enum keys; `WATERMASSFLOWRATEOFHEATGENERATOR` has 2 readers, 0 writers |
| Python setups | ~50 in `system_setups/` |
| v1 executor default lookup | method-name matching, `hisim/json_executor.py:_get_default_config` |
| Existing design work | `config_presets` (18 commits: `hisim/config/`, 3 pilots), `json_v2` (`hisim/scenario_v2/`, grouped connections, realized record, A3/A4 decisions) |

**Stakeholders and consumers.** Existing: RenoVisor (`hisim/renovisor/mapping.py:_select_setup`), building sizer (`hisim/building_sizer_utils/interface_configs/`), HPC harness (`hpc_harnes_spec.md` on `json_v2`). Hypothetical, not constraints: webtool, template creator.

**Kind of change:** behavior change + migration. Format, executor and sizing mechanism are replaced; identical concrete inputs must yield identical results (C1).

## 5. Goals and Non-Goals

**Goals** — G1 one file describes one system, no Python needed to run it · G2 sizing declared once per class, resolved from unambiguous facts, no arithmetic in files · G3 every run leaves a re-executable record with provenance · G4 converting a component touches only its module · G5 the three real consumers use the file directly.

**Non-Goals** — a modeling language (no expressions/conditionals in files) · replacing Python setups as a *development* tool · reducing dynamic-connection verbosity · a template/repeat layer in this epic (must not be precluded) · results/KPI formats · multi-zone Building physics (separate work; C3).

## 6. Use Cases and Mockups

The three mockups are the shared contract; every phase is checked against them.

| | Mockup | Shows |
|---|---|---|
| UC1 | `energy_system_mockup_minimal.yaml` — gas boiler, 8 components | zero sizing text when every fact has one provider |
| UC2 | `energy_system_mockup.yaml` — heat pump, 2 PV, battery/EMS, electric backup, 4 groups | the ambiguity rule; groups on/off; a many-reader |
| UC3 | `energy_system_mockup_mfh.yaml` — 3 apartments, central heat pump/PV, 35 components | scale; explicit per-apartment values while the Building is single-zone |
| UC4 | footer of `energy_system_mockup.yaml` | RenoVisor: base file per heating system + overrides + group flags |
| UC5 | footers | realized record re-executes bit-for-bit, sizes nothing |

## 8. Epic-level requirements

These hold across all phases; each phase document owns the detailed requirements listed in its scope.

- **E1 `[decided 2026-08-25]` Governing principle.** Unambiguous → nothing to write; anything else → written out; never inferred. Applies to connections, sizing sources, presets, group toggles and error handling alike.
- **E2 `[decided 2026-08-25]` Fact binding rule** (owned by P1, spelled in P2). Every sizing fact a component declares is addressable as `<component-name>.<fact>`; a bare fact name resolves only if exactly one enabled component provides it; otherwise the consumer maps it explicitly (`sizing_sources`) and the executor errors listing the candidates. Providers declare nothing; realized records always write the mapping.
- **E3 `[decided 2026-08-25]` No math in files.** A source is one reference or an explicit list of references; aggregation is the consuming class's law. Rejected: `all`, expression objects, helper components.
- **E4 `[decided 2026-08-25]` Structure never carries sizing semantics.** Groups are sets with a flag, not scopes; any future template layer is a preprocessor to a flat file.
- **E5 `[decided 2026-08-25]` No privileged root.** Any component may provide and consume facts; acyclicity is the only structural constraint; provider-side fact computation happens at build time.
- **E6 `[proposed]` Locality of conversion.** Adding or converting a component changes only its module (presets, laws, default connections).
- **E7 `[given]` Golden parity.** Identical concrete inputs produce identical results before and after; the golden suites are the oracle.
- **E8 `[proposed]` Wire format is public.** Preset, field and fact names are stable once released; renames are breaking changes.

### Phase scope (detail in `declarative_energy_systems_plan.md`)

| Phase | Owns | Document |
|---|---|---|
| P1 Sizing kernel | presets, `AUTO`, laws, binding rule as Python API, scalar cardinality, contract tests | `p1_sizing_kernel_requirements.md` |
| P2 File format & executor | components/inputs/config/preset/sizing_sources/groups, hard errors, schema, paths, realized record, provenance | `p2_file_format_requirements.md` |
| P3 Recording & setup migration | Python → file recorder, all setups converted, golden parity | written after P1 acceptance |
| P4 Component sweep | ~85 factories → presets/laws in batches; describe CLI, docs | batch checklist in the plan, no requirements document |
| P5 Consumer integration | RenoVisor, building sizer, HPC payload; delete `ModularHouseholdConfig` | written after P2 acceptance |
| Deferred | many-cardinality, climate facts, template layer, multi-zone | parking lot in the plan |

## 9. Constraints, Invariants and Assumptions

- **C1** `[given]` Golden parity (E7); `.github/workflows/golden-*.yml`.
- **C2** `[proposed]` The `Building` is one thermal zone (`hisim/components/building/`); per-apartment quantities are explicit until that changes.
- **C3** `[proposed]` The `Building` has one occupancy input; several apartments need an aggregating input — component work outside this epic.
- **C4** `[proposed]` `hisim/config/` imports nothing from the rest of HiSim except `hisim.log` (layering rule on `config_presets`).
- **C5** `[proposed; inventory §4]` The scalar law algebra on `config_presets` covers 100 % of sizing math in use.
- **A1** `[proposed]` The `json_v2` decisions A3 (YAML canonical for generated files) and A4 (three-file audit layout, YAML-comment provenance) carry over — confirm in P2 (Q8).
- **A2** `[proposed]` Many-cardinality has no present consumer (inventory §2) and can wait for the first real one.

## 10. Acceptance Criteria (epic level)

| ID | Criterion | Verifies |
|---|---|---|
| EAC1 | Every phase document's acceptance criteria are met and the phase is accepted. | all |
| EAC2 | The three mockups run end to end without Python setup code and their realized records re-execute bit-for-bit. | E1, E2, E3, E4, E5, G1–G3 |
| EAC3 | All golden suites pass on recorded setups. | E7 |
| EAC4 | A new component with presets and laws appears in an energy system with no change outside its module. | E6 |
| EAC5 | RenoVisor, building sizer and HPC harness run from energy-system files; `ModularHouseholdConfig` is deleted. | G5 |
| EAC6 | A wire-format test pins every released preset, field and fact name; renaming one fails the test until the change is recorded as breaking. | E8 |

## 11. Open Questions and Decision Register

**Answered**

| ID | Question | Blocks | Status |
|---|---|---|---|
| Q1 | Per-apartment heating physics a goal? | C2, template layer | `[answered 2026-08-25]` yes, after multi-zone Building; nothing here may preclude it |
| Q2 | Where do derived facts live? | E3 | `[answered 2026-08-25]` in the consuming class |
| Q3 | Does structure affect sizing? | E4 | `[answered 2026-08-25]` no |
| Q4 | Reduce dynamic-connection verbosity? | P2 | `[answered 2026-08-25]` no |
| Q5 | Which consumers constrain? | P5 | `[answered 2026-08-25]` RenoVisor, building sizer, HPC harness |
| Q6 | Where does the modular-household layer live? | P5 | `[answered 2026-08-25]` nowhere: base file + overrides + groups |
| B11 (branch agenda) | Scope rule GLOBAL vs CONNECTED for contributed facts (HDS controller vs boiler)? | E2 | `[superseded 2026-08-25]` no scopes exist any more; every fact is `<name>.<fact>`, ambiguity is per consumer (E2) |
| Q7 | Do the `[proposed]` items stand? | — | `[withdrawn 2026-08-25]` not a question; the two items that need a decision are EQ1 and EQ2 below, the rest (E6 locality, C2–C5 as stated facts) are confirmed by silence at review |

**Open**

**EQ1 — Once released, may a preset, field or fact name ever be renamed, and if so how?** · blocks E8, P1 R3.4, P2 R3.6, EAC6
*Context.* Energy-system files reference presets by name (`preset: condensing_gas`), fields by name (`config: {maximal_thermal_power_in_watt: …}`) and facts by name (`sizing_sources: {pv_peak_power_in_watt: …}`). RenoVisor and the building sizer will write these names into files they generate (Q6); the HPC harness stores them in job payloads. Today nothing is released, so any name can still change for free. On `config_presets` the three pilots already define ~20 preset names (`condensing_gas`, `condensing_gas_12kw`, `oil`, `pellets`, `standard`, `optimize_own_consumption`, …) and the inventory lists 9 fact names.
*Options.* (a) **Frozen after release** — a rename is a breaking change requiring a `schema_version` bump and a migration note; a wire-format test pins every name (EAC6). Consequence: names must be chosen carefully in P4; consumers never break silently. (b) **Deprecation aliases** — old names keep loading for N releases with a warning; the loader carries an alias table. Consequence: friendlier, but an alias table is exactly the "silent repair" R7 forbids, and it grows forever. (c) **Free renames until the first external consumer ships** (P5), then (a). Consequence: P4 can fix bad names cheaply; the freeze point is a dated decision.
*Recommendation.* (c): names are cheap to fix during the sweep and expensive afterwards; after P5 acceptance, (a) applies with no aliases.

**EQ2 — Is deferring many-cardinality (several providers feeding one law) until its first real consumer acceptable, or must P1 ship it?** · blocks E3 wording, A2, P1 R4.4, plan parking lot
*Context.* The inventory found **0** readers that consume several providers today (`sizing_fact_inventory.md` §2); the only candidates are hypothetical (a buffer storage over a hybrid heat pump + boiler pair; a battery or price signal over several PV systems — UC2/UC3 in the mockups). The file syntax for it (a list under `sizing_sources`) is fixed by E3 regardless. Implementing it in P1 means: a `many` term in the law algebra, a tuple side-table in `SizingContext`, `sum/max/count` aggregates, ~100 lines plus tests, all without a consumer to validate against.
*Options.* (a) **Defer** — P1 declares `many` in the algebra so a law can be written, but evaluation raises `NotImplementedError`; implemented with the first converted class that needs it (P4 batch B4 or B5). Consequence: P1 is smaller and reviewable against real pilots; the mockups' EMS example (UC2) is not executable until then. (b) **Ship in P1** — implement against the UC2 mockup's EMS lists as a synthetic consumer. Consequence: P1 grows by roughly a third; the design is validated on a fabricated use case and may be revised when a real one arrives.
*Recommendation.* (a); the syntax is fixed, the mechanism can wait for evidence, and UC2 stays a format test rather than an execution test until then.

## 12. Glossary

**Fact** — named, unit-suffixed quantity a law may read, declared by a provider's class · **Law** — the class-side formula turning facts into a field value · **AUTO** — field value meaning "compute by the law" · **Preset** — class-provided named default configuration · **Provider / consumer** — class declaring a fact / law reading it · **Cardinality** — one provider or a list · **`sizing_sources`** — consumer-side fact → `<name>.<fact>` mapping, written only when ambiguous · **Group** — named set of components with an `enabled` flag, not a namespace · **Realized record** — concrete, re-executable copy of an energy system written by a run · **Audit companion** — its machine-readable provenance.
