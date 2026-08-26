# Declarative energy systems epic — phased plan

**Status:** draft, 2026-08-25 · **Parent:** `roadmap/declarative_energy_systems/epic.md`
**Supersedes:** the `roadmap/declarative_energy_systems/plan.md` on branch `config_presets` (written for the
superseded three-way fact scope; its §1 as-built inventory remains accurate for the branch state).

**Tracking rule:** every phase has a checkbox list; the session that finishes a step checks it
off in the same commit as the work. A session picking up the epic reads the epic, this plan,
and the first phase with an unchecked box.

**Freeze order:** epic principles → this plan → phase requirements one at a time → solution
design per phase → PRs. A phase requirements document is written only when its predecessor
is accepted, so it cites decisions instead of re-arguing them.

---

## Phases, dependencies, gates

```
P1 sizing kernel ──► P2 file format & executor ──► P3 recording & setup migration ──► P5 consumers
        │                       │
        └──► P4 component sweep (batches; each batch needs P1, later batches use P2 fixtures)
```

| Phase | Delivers | Depends on | Review gate | Requirements |
|---|---|---|---|---|
| **P1 Sizing kernel** | `hisim/config` reworked: `Catalog` without `component_id`; `AUTO`/`sized_field`/laws unchanged; the fact binding rule as a Python API (`resolve_all(configs, sources=…)`); scalar cardinality only; contract tests; introspection API | PR #582 (`config_base_move`) | pure code + tests; inventory; pilots pass | `p1_sizing_kernel_requirements.md` |
| **P2 File format & executor** | schema v3 (components / `inputs` / `preset` / `config` / `sizing_sources` / groups), YAML + JSON loading with duplicate-key detection, hard-error catalogue, `${var}` paths, JSON Schema export, realized record + audit companion, `describe`/`facts` CLI | P1 | the three mockups load, resolve, build and run; identity test | `p2_file_format_requirements.md` |
| **P3 Recording & setup migration** | recorder (`setup_function` → energy-system file), all ~50 setups recorded and checked in, golden parity | P2 | golden suites green on recorded files | written after P1 acceptance |
| **P4 Component sweep** | ~85 factories → presets + laws; setup-side sizing moved into classes; dead SimRepository sizing keys deleted | P1 (P2 for fixtures) | per batch: contract test, golden parity | batch checklist below; no requirements document |
| **P5 Consumer integration** | RenoVisor, building sizer and HPC harness on energy-system files; `ModularHouseholdConfig` deleted | P2, P3 | consumers' own tests | written after P2 acceptance |

## P1 — Sizing kernel

- [ ] Requirements document accepted (Q-P1.9 decided against `roadmap/declarative_energy_systems/preset_naming_supplement.md`; then rename `BuildingConfig.presets.german_single_family_home` → `standard`, pick the canonical PV preset)
- [x] Solution design: binding rule API, how a preset acquires its instance name (was Q-P1.1), introspection surface *(2026-08-25)*
- [x] `FactScope`, adjacency, pre-seed removed; qualified-name lookup + `sources` mapping *(2026-08-25)*
- [x] Uniqueness evaluated over the given set; error lists candidates, ready to paste *(2026-08-25)*
- [x] Presets carry no `component_id`; name injected at construction *(2026-08-25)*
- [x] Contract test: fact-name collisions across non-interchangeable classes; one-vs-many misuse *(2026-08-25)*
- [x] Introspection: fields, presets, sizable fields + facts read, facts provided (data, not CLI) *(2026-08-25)*
- [x] Pilots (boiler, HDS, EMS) green on the new kernel; `tests/test_sizing_engine.py` rewritten *(2026-08-25)*
- [x] This plan's §P1 checked off; the former plan and review agenda on `config_presets` deleted, `random_findings.md` moved here *(2026-08-25)*
- [ ] PR body of #586 rewritten to the P1 scope; branch pushed

## P2 — File format & executor

- [x] Requirements decisions taken 2026-08-26 (Q8 keep comments; nested CLI; `energy_systems/` alongside `system_setups/`; R3.8 accepted; AC-P2.1 amended; RQ3 dropped) — document acceptance pending review
- [x] Convert UC1's classes to presets/constructors: Weather, UtspLpgConnector, GenericBoilerController, ElectricityMeter, HeatDistributionController (PR-3) *(2026-08-26)*
- [ ] Solution design against the three mockups (they are the fixtures)
- [x] Schema v3 model + YAML-only loader with duplicate-key detection + structural validation (`hisim/energy_system/`, PR-1) *(2026-08-26)*
- [x] Consumer-side `inputs` (bare / explicit wire / aggregator feed) replaces grouped-by-source connections (PR-1/PR-3) *(2026-08-26)*
- [x] Groups: parse, "off" rule, uniqueness over enabled set; class-bound validation, config decoding, sizing bridge to the kernel (PR-2) *(2026-08-26)*
- [x] Executor: resolve (P1 API) → construct → connect; error catalogue with both ends named; UC1 runs end to end (PR-3) *(2026-08-26)*
- [ ] Realized record (presets expanded, `AUTO` → numbers, disabled groups absent) + audit companion; YAML comments per A4 if Q8 confirms
- [ ] Identity test over all mockups and groups
- [ ] JSON Schema export; `describe <class>`, `facts <energy_system>` CLI
- [ ] `${var}` path resolver carried over from `json_v2`
- [ ] C10 — v3 fixtures spell UTSP `JsonReference`s as `Name`/`Guid`/`StrVal`; one local-LPG run verifies (own commit, before/after)

## P3 — Recording & setup migration (outline; document later)

- [ ] Recorder: run a `setup_function` under a recording simulator, emit an energy-system file in canonical style
- [ ] Every setup recorded; recorded files checked in next to the setups
- [ ] Golden suites run on recorded files; setups themselves kept until P5 confirms no consumer needs them

## P4 — Component sweep (batches; each a mechanical PR)

Gates before the first batch (inherited from the branch agenda; each is its own commit,
never bundled with a conversion because each changes results or deletes code):

- [ ] D13 — the 14 components that cannot be built from their own defaults (3 zombies importing deleted modules: `controller_l1_building_heating`, `controller_l1_heatpump`, `controller_l1_generic_runtime`; 6 defective: `generic_battery` ×2, `generic_ev_charger` ×4; 5 legitimately data-dependent): delete outright or move to `obsolete/` — decide, execute, so nobody converts a class about to be deleted
- [ ] C11 — buffer storage sized from *building load* (legacy gas/oil/pellet/wood-chip setups) although the parameter is the *generator power* (≈1.1 × max(load, DHW) once the boiler is sized): bless the status quo byte-identically, or schedule the physics change with result diffs — decide before B4
- [ ] Q-P1.8 outcome applied before B3: `HeatDistributionControllerConfig.heating_system` gets a law reading the building's construction year and renovation level (new Building facts from the TABULA code) — physics change, own commit with result diffs; until then a plain default, not `AUTO`
- [ ] Expect on every converted class (random_findings): trailing capex fields need `None` defaults once sized fields carry defaults; regenerated fixtures show int → float literal drift (golden-neutral, must be committed); one `SizingContext` per setup threaded through, not one per component

Order by dependency and by how many consumers a family unlocks (inventory §2–§3). Each batch:
presets replace `get_default_*`/`get_scaled_*`; laws replace setup-side arithmetic; `SIZING_CONTRIBUTIONS`
declared; call sites moved; regenerated fixtures; golden parity.

- [ ] B1 Heat generators: hplib heat pumps (both), electric heating, district heating, generic heat pump, CHP/fuel cell (constants only)
- [ ] B2 Heat generator controllers: heat pump SH/DHW, boiler controllers, electric/district heating controllers, L1 heat source controller
- [ ] B3 Heat distribution: HDS controller (specific load, threshold), HDS (remaining legacy controller config)
- [ ] B4 Storages: hot water storage (per-generator volume presets), DHW storage; delete `WATERMASSFLOWRATEOFHEATGENERATOR`
- [ ] B5 Electricity: PV (per-preset share law), battery, meter, price signal, wind turbine
- [ ] B6 Occupancy, weather (`for_location` pattern), building presets (`german_multi_family_home`)
- [ ] B7 Mobility and remaining: cars, chargers, smart devices, H₂ chain, air conditioners, solar thermal
- [ ] B8 Examples and templates; `example_template.py` shows the preset/law pattern; delete the nine legacy plain dataclasses in `configuration.py` instead of converting them (supplement conflict 10)
- [ ] `describe` output and generated docs reviewed for every converted class (R13)

## P5 — Consumer integration (outline; document later)

- [ ] RenoVisor: base file per heating system; `mapping.py` → overrides + group flags; post-processing selection moves to the simulation-parameters file
- [ ] Building sizer: same path; `ModularHouseholdConfig`, `EnergySystemConfig`, `ArcheTypeConfig` deleted
- [ ] HPC harness: payload = energy-system string + simulation parameters; worker loads from string

## Parking lot (deferred; trigger named)

| Item | Trigger |
|---|---|
| Many-cardinality laws (epic E3, P1 leaves the hook) | first real consumer: buffer storage over a hybrid generator pair, or battery/price signal over several PVs |
| Climate facts from the Weather (design temperature, PV yield, heating season) | first law that reads them; inventory §1a |
| Template / repeat layer (preprocessor to a flat file) | MFH work after the multi-zone Building (UC3, O8) |
| Multi-zone `Building`, per-unit facts, aggregating occupancy input | separate epic |
| Nested groups, inter-group `requires` | only if flat groups prove insufficient in real files |
| `at_least` / `at_most` law operators | first law that needs a clamp; otherwise delete (Q-P1.2) |
| Runtime half of `SingletonSimRepository` (MPC/PID heat-flux, weather and price forecasts) — still live, needs its own redesign, probably proper wiring | separate decision after P4 removes the dead construction-time keys |
