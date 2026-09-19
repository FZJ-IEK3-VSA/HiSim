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
P1 sizing kernel ──► P2 file format & executor ──► P3 recording & setup migration ──► P5 consumers ──► P6
        │                       │                                                          ▲                 ▲
        │                       └──► P2.1 exclusive variants ───────────────────────────────┘                 │
        │                            (format amendment; P3's grouping pass emits it)                          │
        └──► P4 component sweep (batches; each batch needs P1, later batches use P2 fixtures) ─────────────────┘

P6 takes the parity rig down once everything above it is merged: P4 re-records the fleet on every batch,
so the rig is the migration's safety net for as long as the migration is running.
```

| Phase | Delivers | Depends on | Review gate | Requirements |
|---|---|---|---|---|
| **P1 Sizing kernel** | `hisim/config` reworked: `Catalog` without `component_id`; `AUTO`/`sized_field`/laws unchanged; the fact binding rule as a Python API (`resolve_all(configs, sources=…)`); scalar cardinality only; contract tests; introspection API | PR #582 (`config_base_move`) | pure code + tests; inventory; pilots pass | `p1_sizing_kernel_requirements.md` |
| **P2 File format & executor** | schema v3 (components / `inputs` / `preset` / `config` / `sizing_sources` / groups), YAML + JSON loading with duplicate-key detection, hard-error catalogue, `${var}` paths, JSON Schema export, realized record + audit companion, `describe`/`facts` CLI | P1 | the three mockups load, resolve, build and run; identity test | `p2_file_format_requirements.md` |
| **P2.1 Exclusive variants** | `variants: {selected, options}` — exactly one option live, options are complete alternative worlds so two of them may wire the same component differently; the case is the RenoVisor backend's "EMS with battery, or a bare meter" | P2 | the UC2 mockup's variant loads and resolves; identity test per (variant, option) | `p2_file_format_requirements.md` R15 (amendment) |
| **P3 Recording & setup migration** | recorder (`setup_function` → energy-system file), every in-scope setup recorded and checked in, the grouping pass (R10), the temporary parity rig (R11) | P2 | golden suites green on recorded files; every rig triple identical | `p3_recording_requirements.md` |
| **P4 Component sweep** | ~85 factories → presets + laws; setup-side sizing moved into classes; dead SimRepository sizing keys deleted | P1 (P2 for fixtures) | per batch: contract test, golden parity | `p4_component_sweep_requirements.md` (per-class registry; survey in `p4_class_survey.md`) |
| **P5 Consumer integration** | RenoVisor, building sizer and HPC harness on energy-system files; `ModularHouseholdConfig` deleted | P2, P2.1, P3 | consumers' own tests | written after P2 acceptance |
| **P6 Retire the scaffolding** | the temporary parity rig (workflow, config, scripts) deleted; whichever setups earned it join the permanent golden gate | **P1–P5 all merged** | the stack is green without the rig | `p3_recording_requirements.md` R11.8 (amended) |

## P1 — Sizing kernel

- [x] Requirements document accepted (Q-P1.9 decided against `roadmap/declarative_energy_systems/preset_naming_supplement.md`; then rename `BuildingConfig.presets.german_single_family_home` → `standard`, pick the canonical PV preset) *(done 2026-08-26: all P1 questions decided, requirements accepted)*
- [x] Solution design: binding rule API, how a preset acquires its instance name (was Q-P1.1), introspection surface *(2026-08-25)*
- [x] `FactScope`, adjacency, pre-seed removed; qualified-name lookup + `sources` mapping *(2026-08-25)*
- [x] Uniqueness evaluated over the given set; error lists candidates, ready to paste *(2026-08-25)*
- [x] Presets carry no `component_id`; name injected at construction *(2026-08-25)*
- [x] Contract test: fact-name collisions across non-interchangeable classes; one-vs-many misuse *(2026-08-25)*
- [x] Introspection: fields, presets, sizable fields + facts read, facts provided (data, not CLI) *(2026-08-25)*
- [x] Pilots (boiler, HDS, EMS) green on the new kernel; `tests/test_sizing_engine.py` rewritten *(2026-08-25)*
- [x] This plan's §P1 checked off; the former plan and review agenda on `config_presets` deleted, `random_findings.md` moved here *(2026-08-25)*
- [x] PR body of #586 rewritten to the P1 scope; branch pushed *(2026-08-26)*

## P2 — File format & executor

- [x] Requirements decisions taken 2026-08-26 (Q8 keep comments; nested CLI; `energy_systems/` alongside `system_setups/`; R3.8 accepted; AC-P2.1 amended; RQ3 dropped) — document acceptance pending review
- [x] Convert UC1's classes to presets/constructors: Weather, UtspLpgConnector, GenericBoilerController, ElectricityMeter, HeatDistributionController (PR-3) *(2026-08-26)*
- [x] Solution design against the three mockups (they are the fixtures) *(`p2_implementation_spec.md` + PR amendments, 2026-08-26)*
- [x] Schema v3 model + YAML-only loader with duplicate-key detection + structural validation (`hisim/energy_system/`, PR-1) *(2026-08-26)*
- [x] Consumer-side `inputs` (bare / explicit wire / aggregator feed) replaces grouped-by-source connections (PR-1/PR-3) *(2026-08-26)*
- [x] Groups: parse, "off" rule, uniqueness over enabled set; class-bound validation, config decoding, sizing bridge to the kernel (PR-2) *(2026-08-26)*
- [x] Executor: resolve (P1 API) → construct → connect; error catalogue with both ends named; UC1 runs end to end (PR-3) *(2026-08-26)*
- [x] Realized record (presets expanded, `AUTO` → numbers, disabled groups absent) + audit companion + wire log; provenance as YAML comments (ruamel, write-only) (PR-4) *(2026-08-26)*
- [x] Identity test over all mockups and groups (PR-2 on the model, PR-4 on the record) *(2026-08-26)*
- [x] JSON Schema export; `describe <class>`, `facts <energy_system>` CLI *(PR-5: `hisim energy-system describe|facts|schema|run`, 2026-08-26)*
- [x] `${var}` path resolver carried over from `json_v2` *(PR-1, 2026-08-26)*
- [x] C10 — v3 fixtures spell UTSP `JsonReference`s as `Name`/`Guid`/`StrVal`; one local-LPG run verifies (own commit, before/after) *(PR-6: the record round-trips the references with their real spelling; a live UTSP run needs `UTSP_URL`/`UTSP_API_KEY`, left to P5, 2026-08-26)*

## P2.1 — Exclusive variants (amendment to the merged P2)

Requirements: `p2_file_format_requirements.md` R15, C-P2.5, AC-P2.17–AC-P2.19 (added 2026-08-28, re-answering Q-P2.2).
Asked for by the RenoVisor backend: a house has either an EMS with a battery **or** a plain electricity meter, and the
meter is wired differently in the two worlds — a group can add or remove components but cannot rewire one that survives.
Nothing in P3 or P4 consumes variants except P3's grouping pass (R10). `[decided 2026-08-28]` **P2.1 is built before P3
starts** — the format is cheaper to settle while three mockups and one real file exist than after twenty-one recorded
files do, and it answers the RenoVisor requirement in code rather than on paper.

- [x] `variants: {name: {selected, options: {name: {components}}}}` in the model, loader and JSON Schema *(2026-08-31)*
- [x] Selection folded into the existing group-expansion pre-pass; the selected option's components join the top level, so nothing downstream sees a variant *(2026-08-31)*
- [x] The five R15.5 rejections with their messages *(2026-08-31: EF-55 unknown selection, EF-56 empty options, EF-57 two variants, EF-52 for the two name collisions)*
- [x] Identity test extended to every (mockup, variant, option) triple; audit records the selection *(2026-08-31: `tests/test_energy_system_variants.py`; both directions of UC2's metering pinned, AC-P2.17–AC-P2.19)*
- [x] `facts` reports groups and variants as one knob surface (R15.8) *(2026-08-31)*
- [x] UC2 mockup: move the `battery_and_ems` group and the meter into an `electricity_management` variant. This
      belongs to the code change, not to the requirements: a mockup is an executable fixture (AC-P2.1), so the
      block may only enter it once the loader reads it. Until then the syntax lives in R15's example.
      *(2026-08-31: `meter` written out in both options, O2 resolved in the mockup's footer)*

## P3 — Recording & setup migration

Requirements: `p3_recording_requirements.md` (accepted 2026-09-06 with the stack's review rounds; inventory in
`p3_setup_inventory.md`). Implementation: `p3_implementation_spec.md` (accepted 2026-09-06; PR-1 … PR-6 in its §10, the teardown moved to P6,
its §13 design questions all decided during the rounds).

- [x] All requirements questions decided *(2026-08-28: Q-P3.1 record now and re-record per P4 batch · Q-P3.2 KPI parity is the oracle · Q-P3.3 the semi-manual grouping pass R10 · Q-P3.4 files live in `energy_systems/` · Q-P3.5 the three unrecordable setups are deleted, not excluded — **amended 2026-09-02**, see the removal item below: two were retired independently and the third became recordable · Q-P3.6 parameters emitted only when new, never duplicated · Q-P3.7 the temporary parity rig R11)*
- [x] Requirements document accepted at review *(2026-09-06: every stack PR #632–#638 went through a 19-reviewer round, findings verified, decided and answered in the PRs' commits)*
- [x] Implementation spec written; DQ1–DQ5 decided *(2026-08-28: subpackage · rename the illegal names and enforce the rule in `Component.__init__` · guard `component_id` now and decide in P5 · hand-authored renaming table · the rig compares every result column through it, R11.3 and C-P3.2 amended)*
- [x] Implementation spec accepted at review *(2026-09-06, same rounds)*
- [x] P2.1 first (see above), then the removal commit (R5.2) — *what R5.2 asked for happened, but only partly by this branch's hand.* `simple_weather_data_import.py` and `basic_household_with_weather_data_request.py` were moved to `obsolete/` on main by #596, so the deletion half of the commit is a no-op on rebase. `air_conditioned_house.py` **stays**: R5.2 named it because it deleted every file in `hisim/inputs/cache` before building, and #605 removed exactly that call and gave the setup a passing one-day KPI test, so the premise for deleting it is gone and the fleet policy that replaced it — every setup is made to work rather than dropped — applies instead. It is recorded like any other setup and the fleet is twenty-two, not twenty-one. What the commit still does is empty the freshness `--exclude` list and delete the option, which is now better justified than when it was written: the two names it excluded are no longer in `system_setups/` at all. After this the recorder needs no skip list.
- [x] Rename the five illegal component-name literals in three setups and the `ExampleTransformer` class default; regenerate the three v1 twins; then enforce the identifier rule in `Component.__init__` (own commit). No golden reference is affected *(2026-09-05, #632)*
- [x] Port `json_v2:parity.py` (wiring snapshot, port renaming, result comparison) and the preset half of `json_v2:templating.py` *(2026-09-06, #635; the comparison semantics hardened — NaN, index, rename collisions, a zero-reference noise floor — in the same round)*
- [x] Recorder: run a `setup_function` under a recording simulator, emit an energy-system file in canonical style *(2026-09-06, #635)*
- [x] Every setup recorded flat; recorded files checked in to `energy_systems/<stem>.energy_system.yaml` *(2026-09-06, #636: all twenty-two, no skip list, freshness-gated; first green freshness run on main the same day — the AC-P3.4 cross-machine evidence)*
- [x] Parameter files emitted only where the setup's parameters match nothing shipped, deduplicated by normalised content, named for what they are (R8) *(2026-09-06, #636)*
- [x] Golden suites run on recorded files; setups themselves kept until P5 confirms no consumer needs them *(2026-09-06: the golden-yaml check shipped with #636; #642 gated the full fleet, 22 of 22 at week resolution or better)*
- [x] Grouping pass (R10), after the flat files exist: probe list per setup, prefilled workbook, `grouping import` to a committed `<stem>.grouping.yaml`, second recorder pass building groups and variants *(2026-09-06, #638: machinery complete, the heat-pump sizer grouped as the exemplar; the fleet-wide grouping worklist landed with #643)*
- [x] Every probe column asserted byte for byte against its flat recording (R10.6) — the grouping pass needs no new golden runs *(2026-09-06, #638; five probe columns on the exemplar, including the building-code cascade)*

Migration parity rig (R11) — temporary, `workflow_dispatch` only, exists to make the migration safe and is removed with it:

- [x] `one_week_july` parameter set next to `one_week_only`, so cooling and solar-thermal setups are measured somewhere other than their annual minimum *(2026-09-06, #637)*
- [x] Rig: run each (setup, probe configuration, window) triple twice in one container — Python path and recorded file — and compare component set, wire set, shared result columns and KPIs at **exact equality** (same machine, so no tolerance is needed) *(2026-09-06, #637)*
- [x] Structural verdict for the seven setups whose KPI layer crashes today, so they are covered without waiting for repairs *(2026-09-06, #637; amended 2026-09-05 — an unavailable stage fails its triple, and the crashes have since healed, so the case is pinned synthetically)*
- [x] One dispatch prints one table of every triple; failures upload both KPI sets, both CSVs and the wire diff *(2026-09-06, #637; the fleet-wide baseline dispatch, AC-P3.17, is still to run)*
- [ ] The rig stays until **P6** (R11.8, amended 2026-08-31): P4 re-records the fleet on every batch, so the rig is what proves a re-recorded file still reproduces its setup

Not blocking P3 — the KPI-layer repair list found by `golden_validate.py --scan-all` (2026-08-28):

- [x] `dynamic_components` (CHP1) and `electrolyzer_with_renewables` (transformer/rectifier): components with no KPI method *(2026-09-06: CHP KPIs #640, the electrolyzer setup's four components #641; both setups gated with #642)*
- [x] `basic_household_only_heating`: `NoneType * float` inside KPI computation *(healed by the intervening repairs; the 2026-09-05 re-scan found it gone and the setup is week-gated with a fresh blessing)*
- [x] `simple_air_conditioner_household_building_sizer`: division by zero on a January window *(healed; 2026-09-05 re-scan, week-gated)*
- [x] `household_gas_solar_thermal`: grid import 21.72 kWh above total consumption 10.9 kWh — an energy-balance inconsistency, worth fixing on its own merits *(fixed by #617's duplicate-feed refusal; verified 2026-09-05, grid import equals consumption to the watt-hour)*
- [x] `simple_system_setup_one`/`_two`: toy examples whose components will never carry meaningful KPIs — exclude rather than implement *(resolved the opposite way: #616 let a component that models no device answer for itself, so the toys are gated rather than excluded)*

## P4 — Component sweep (batches; each a mechanical PR)

Requirements: `p4_component_sweep_requirements.md` (per-class table R3, gates R2, physics changes R5,
decisions D-1…D-33 with full text in `p4_class_survey.md`). Its R7 reordered the batches by recorded-setup
impact, and that is the order the sweep ran in; the list below follows R7. The original B1–B8 list of
this file is struck through where R7 renamed it.

- [x] Requirements document accepted (D-1…D-32 decided; D-33 added 2026-09-14)

Gates before the first batch (each its own commit, never bundled with a conversion because each changes
results or deletes code):

- [x] D13 — the components that cannot be built from their own defaults: the zombie third left with `obsolete/` in #590; `controller_l1_heatpump` (D-2), `advanced_fuel_cell_controller` (D-8) and `generic_smart_device` (D-30) followed; the H₂/RSOC classes whose only builders read absent files went with D-25 and D-29 *(all executed 2026-09-10/11)*
- [x] C11 — buffer storage sized from the generator's power, not the building load — decided as D-9, executed 2026-09-11 with the five golden sizers re-blessed
- [x] Q-P1.8 / D-11 — HDS controller threshold 16 → 18 °C for the three legacy setups, converted and diffed 2026-09-11; the `heating_system` law from construction year stays a plain default until its Building facts exist
- [x] Expect on every converted class: capex fields carry `None` defaults, one `SizingContext` per setup — both are the R1.1 shape now

Batches in R7 order (presets replace `get_default_*`/`get_scaled_*`; laws replace setup-side arithmetic;
`SIZING_CONTRIBUTIONS` declared; call sites moved; twins re-recorded; golden parity):

- [x] B1 Legacy-factory removals on converted classes — Weather, UTSP, ElectricityMeter, HDS controller, boiler controller *(2026-09-12/13, #737)*
- [x] B2 Electricity — PV (rooftop law), battery (sized to PV), gas/fuel/heating meters (carrier copied from the generator, D-15) *(2026-09-13, #738–#744)*
- [x] B3 Storages — DHW storage (250 l per apartment), buffer storage (`sizing_option` field and l/kW law, D-10) *(2026-09-14, #747–#748)*
- [x] R1.2 — the sixteen converted classes brought to the R1.1 shape in one sweep; `ConfigBase.MAIN_CLASS` *(2026-09-15, #753)*
- [x] B4 Heat generators — hplib heat pump (`air_water`), electric (`resistive`) and district heating, generic heat pump (`vitocal_300_a` + `for_device`), idealized heater, simple heat source (three presets), air conditioners (D-3 constructors), solar thermal (`flat_plate`), CHP ×2 (`gas`/`hydrogen`, D-5) *(2026-09-15, #754–#759)*
- [x] B5 Generator controllers — hplib SH/DHW, generic heat pump, district heating, electric heating (specific-load law), air conditioners, solar thermal, night setback, CHP controller (four presets, D-4 numbers preserved) *(2026-09-15, #760–#763)*
- [x] B6 Providers — Building `roof_area` fact *(2026-09-11)*, D-22 sparse overrides *(2026-09-11)*, sum builder and transformer presets *(2026-09-15, #764)*; D-21 revised and executed *(2026-09-17)* — the weather owns the design temperature as a plain field, contributes it, and the building reads it, so the fact keeps one provider and no per-station DIN 12831 table is needed
- [x] B7 Mobility and H₂ — CSV loader (`for_csv_file`, no preset), car battery (`standard`) and charging station (`for_charging_station_set`, no preset, D-24) *(2026-09-16, #766)*; the electrolyzer (`alkaline` + `for_device`), its L1 controller, the PtX controller (constructor only), the XtP controller (the preset D-25 left it without), the fuel cell and its controller (`pem`), and the two electrolyzer-with-storage classes (D-28, D-29) *(2026-09-16, #767)*
- [x] B8 Examples and templates — the five example/simple classes converted (`standard` ×4, `thermal` for the storage), `example_template.py` now teaching `MAIN_CLASS`, a `@preset`, a `sized_field` law and the D-31 contribution comment together; F-18 fixed (a preset's own law renders beside `pinned:`/`AUTO:`); `describe` reviewed for all 56 pinned classes (R13), six opaque laws given a `note=`, three larger gaps recorded as F-21…F-23. **The "nine legacy plain dataclasses" phrase is stale:** D-8, D-16 and D-29 had already moved all but one, and B8 moved that one, `GasHeaterConfig`, to `obsolete/components/configuration_gas_heater.py`; `configuration.py` now holds only the two emission-factor tables, `PhysicsConfig` and the live `HouseholdWarmWaterDemandConfig`, which R3 marks exempt *(2026-09-16)*
- [x] Close-out decisions, all settled *(2026-09-17)* — D-21 revised, the weather owning the design temperature (see B6); F-16, the dead `efficiency` field, deleted from `ElectricHeatingConfig` (the schema loses one property; no twin, no golden); F-19, the night setback's two `dataclasses_json` aliases, dropped, so `to_dict` spells the hour fields the way `describe` and the schema already did and the repository has no field alias left; and `MAIN_CLASS` enforced at class definition — `CarConfig` and `TariffProviderConfig`, the last two overriders, declare it instead, and `ConfigBase.__init_subclass__` now refuses a subclass that declares neither a non-empty `MAIN_CLASS` nor its own `get_main_classname` (the override stays as the exception a test double with no real component states). F-21 and F-22, the two gaps R13 left in `describe`, close it: a preset prints the plain fields it sets and their values, and a law built from a callable states the arithmetic it computes instead of the qualified name of the lambda — which for the borrowed heating-threshold law had been the name of another class entirely. With B7 recorded in the line above it, P4 has no open item left

~~B1 Heat generators · B2 Heat generator controllers · B3 Heat distribution · B4 Storages · B5 Electricity · B6 Occupancy, weather, building presets~~ — the original list, superseded by R7 above; every item in it is done under its R7 name.

## P5 — Consumer integration (outline; document later)

- [ ] RenoVisor: base file per heating system; `mapping.py` → overrides + group flags; post-processing selection moves to the simulation-parameters file
- [ ] Building sizer: same path; `ModularHouseholdConfig`, `EnergySystemConfig`, `ArcheTypeConfig` deleted
- [ ] HPC harness: payload = energy-system string + simulation parameters; worker loads from string

## P6 — Retire the migration scaffolding

Requirements: `p3_recording_requirements.md` R11.8 and R11.9 (amended 2026-08-31), AC-P3.20.

The parity rig is scaffolding, and scaffolding comes down when the building stands — not when the floor that
needed it is finished. Its first spelling had P3 delete it, which was decided before P4's shape was clear:
P4 re-records the fleet on **every batch**, and its own assumption A1 reviews each batch against the recorded
file diff, so the rig is the only thing proving a re-recorded file still reproduces its setup while 88 config
classes change how those files are written. The permanent golden gate is not a substitute — it watches eight
setups against blessed references; the rig watches every recorded setup — twenty-two today, forty-four triples — across two windows, needs no references, and covers
eight setups that have no KPI oracle at all.

**Entry condition: P1, P2, P2.1, P3, P4 and P5 are all merged.** Until then the rig is dispatched by every
batch that re-records, and its renaming tables are kept current as P4 renames the legacy aggregator ports.

**Accumulated evidence so far** (the first checklist item below is about exactly this). Fleet-wide dispatches
of the rig on 2026-09-18 (`8f619e4a`) and 2026-09-19 (`dd6cb78b`) were both green: twenty-two triples each,
January window, `rel_tol` and `abs_tol` both zero, so every recorded setup reproduced its Python original at
exact equality. That is the whole fleet, across the P4 close-out, the D-21 move and the kernel cleanups.
`roadmap/p3_cleanup_todos.md`'s expectation of "seven KPI-broken setups" no longer holds -- there are none,
and the baseline the rig leaves behind is full parity rather than a list of exceptions. Two consecutive green
fleet runs are a start on the second item as well: the candidates for the permanent gate are now every setup,
which makes the question one of cost rather than of trust.

- [ ] Confirm the whole stack is green with the rig still in place, over every runnable window
      (July is fenced pending `roadmap/midyear_start_epic.md`; R11.5 as amended 2026-09-06)
- [ ] Decide which setups earned a place in the permanent gate, on the rig's accumulated evidence — the six the
      2026-08-28 scan cleared are the candidates, not the answer
- [ ] Add those to `scripts/golden_config.json` and bless their references
- [ ] Delete `.github/workflows/p3-parity.yml`, `scripts/p3_parity_*.py` and the renaming tables
- [ ] Confirm the repository contains no reference to any of it (AC-P3.20)

## Parking lot (deferred; trigger named)

| Item | Trigger |
|---|---|
| Many-cardinality laws (epic E3, P1 leaves the hook) | first real consumer: buffer storage over a hybrid generator pair, or battery/price signal over several PVs |
| Climate facts from the Weather (design temperature, PV yield, heating season) | first law that reads them; inventory §1a |
| Template / repeat layer (preprocessor to a flat file) | MFH work after the multi-zone Building (UC3, O8) |
| Multi-zone `Building`, per-unit facts, aggregating occupancy input | separate epic |
| Nested groups, inter-group `requires` | only if flat groups prove insufficient in real files |
| `at_least` / `at_most` law operators | first law that needs a clamp; otherwise delete (Q-P1.2) |
| Runtime half of `SingletonSimRepository` (MPC/PID heat-flux and price forecasts) — still live, needs its own redesign, probably proper wiring | separate decision after P4 removes the dead construction-time keys |

2026-09-12: the weather half of that row is done — the Weather's ten full-year series and the occupancy's heating-by-residents forecast now travel through the per-simulation `SimRepository`, under key names owned by their writers (`Weather.YEARLY_*`, `UtspLpgConnector.YEARLY_HEATING_BY_RESIDENTS`). The readers are unchanged otherwise: the PV system and the predictive branch of the `Building`. What is left in the singleton's runtime half is the MPC/PID heat-flux forecasts, the price forecasts, the PV yearly forecast, and the two process-wide strings postprocessing reads. Later the same day the two strings left it too: `RESULT_SCENARIO_NAME` and `DESCRIPTION` became `Simulator.scenario_name` / `Simulator.description`, and a declarative run now carries its file's `name`, plus the option each variant selected, as the scenario name; a Python run that names no scenario is named after its module file. The row is closed — module removed 2026-09-12: `hisim/sim_repository_singleton.py` was deleted with no shim, and its thread-safe `SingletonMeta` moved to `hisim/result_path_provider.py`, beside the one class that legitimately is process-wide.
