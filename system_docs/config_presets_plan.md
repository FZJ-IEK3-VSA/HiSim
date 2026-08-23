# Config presets — revised phased implementation plan

**Status:** active plan, supersedes §8b ("two PRs") of `system_docs/config_defaults_spec.md`.
The design itself (design B revised: field-declared sizing laws, `AUTO`, presets via
`Catalog`, the fact engine) is unchanged and stays specified there; this document plans
the *landing*, phase by phase, so sessions can pick up exactly where the last one stopped.
**Date:** 2026-08-23
**Why revised:** the original two-PR plan predates three events: the building cleanup
landed on main as its own PR stack (#577–#580), constructor uniformity merged (#575),
and PR #576 (the combined foundation PR) was closed as half-obsolete because its diff
still showed the already-merged building commits. The foundation work itself is alive
and rebased; it re-lands in smaller, reviewable phases below.

**Tracking rule:** every phase has a checkbox list. The session that finishes a step
checks it off *in the same commit* as the work (same convention as the spec-upkeep rule
in `roadmap/template_spike_workplan.md`). A session picking up this plan starts by
reading §1 and the first unchecked box.

---

## 1. As-built inventory — where every piece lives today (verified 2026-08-23)

### On `main` (badb7446)

- The building package split and cleanup (`hisim/components/building/` with `config.py`,
  `information.py`, `window.py`, `building.py`) — #577–#580.
- Constructor uniformity — #575.
- **Not yet on main:** anything under `hisim/config/`; all preset/sizing machinery.

### PR #582 — `config_base_move` (open, rebased onto badb7446, mergeable)

The mechanical layering PR: `hisim/config/` package with `base.py` (`ComponentID`,
`ConfigBase`, `DisplayConfig` moved out of `hisim/component.py`, no compatibility
aliases) plus the repo-wide import sweep (~230 files). Provable by the untouched golden
suites. **Everything below stacks on this.**

### Local branch `config_presets` (rebased 2026-08-23: main + 8 commits, 0 behind)

The first commit is tree-identical to `origin/config_base_move`; the seven commits on
top are the design-B foundation:

- `hisim/config/sizing.py` (~640 lines): `AUTO` (copy-stable singleton), `Sizable`,
  `sized_field` (law + wire codec in field metadata, `value_type=` for enum fields),
  the `Size.*` expression terms derived from `SizingContext`'s fields, `SizingLaw`
  algebra (`*`, `.at_least`, `.at_most`, `.rounded`, constants, function laws with
  mandatory `reads=`), `resolve()`, `sizing_record` provenance, `concrete()`.
- `hisim/config/engine.py` (~440 lines): the §8.4 fact engine — declared
  contributions (`SIZING_CONTRIBUTIONS`), static output names, graph validation
  (unprovided fact / cycle = hard errors naming parties), fixed-point resolution,
  **CONNECTED scope with adjacency-first + flat-pool-fallback hybrid** (the spike's
  refinement is already in this rebuild), `resolve_all` for Python setups.
- `hisim/config/presets.py` (~110 lines): `Catalog` (fresh instance per access,
  `canonical`, iteration) **including the preset-provenance stamp**
  (`preset_provenance` non-field attribute; the spike's stamp is already in this rebuild).
- `hisim/component.py`: the central check — a config carrying `AUTO` anywhere raises
  `ConfigSizingError` in `Component.__init__`, printing each field with its law.
- Building payload: `building/config.py` + `building/information.py` contribute the
  building sizing facts (and the preset archetype).
- Three pilots fully converted, legacy factories deleted, call sites moved:
  `generic_boiler.py` (**`GenericBoilerConfig` only** — the controller configs in the
  same file are still legacy), `heat_distribution_system.py` (**`HeatDistributionConfig`
  only** — the controller config is still legacy), `controller_l2_energy_management_system.py`.
- Tests: `tests/test_sizing.py` (309 lines), `tests/test_sizing_engine.py` (308 lines);
  34 tests green on the rebase.
- Regenerated scenario JSONs for the boiler conversion; the specs and findings log.

### Branch `json_v2` (the spike — old module layout: `hisim/sizing.py`, `hisim/sizing_engine.py`)

Main-compatible parts **not yet in the rebuilt package** (phase 3 ports these):

- Design-B conversions of the heat pump example's chain:
  `more_advanced_heat_pump_hplib.py` (heat pump + its SH/DHW controllers),
  `simple_water_storage.py` (both storages), `advanced_battery_bslib.py`,
  `generic_pv_system.py` (incl. the per-preset `scaled_power_law` workaround for B6),
  extended `heat_distribution_system.py` (controller-side facts),
  `electricity_meter.py`, `weather.py` (the B9 `for_location(...)` pattern),
  `building_config.py` extensions (now `building/config.py`), EMS mypy channel fixes.
- The sizing-fact registry extensions for that chain (water mass flow, storage
  temperatures, PV peak power, generator power band).

v2-track parts that stay on `json_v2` (consumed by its own MR plan, **out of scope
here**): entry-level `preset` references in schema/executor/template-creator, the §8.3
audit dump, the closing-the-loop template test, grouped connections.

### Legacy surface remaining (the sweep denominator)

- **~85 default/scaled config factories across 52 component modules** (see the §6
  inventory; excludes `get_default_connections_*`).
- ~70 `ConfigBase` subclasses in `hisim/components/`.
- `hisim/json_executor.py::_get_default_config` (v1 executor) discovers defaults by
  name matching — it has **no preset awareness** and errors on classes with zero or
  multiple `*default*` methods. Converted classes lose their factories, so this must
  learn `presets.canonical` (phase 4) before the sweep deletes factories broadly.
- A stray tracked file `main` (20 bytes, content "small_fixes_5 -") sits in the repo
  root — commit accident, delete opportunistically.

---

## 2. Decision register

**Decided** (recorded in `config_defaults_spec.md` and
`roadmap/design_review_questions.md` on `json_v2`):

- Design B revised; JSON contract (b) preset-reference + sparse overrides; preset
  names are wire format.
- A1–A4 (entry-level presets; grouped connections; YAML canonical for generated
  files; audit layout + provenance comments).
- B5 (b1): enum-typed sizable fields pass `value_type=`.
- Generator emits templates; realized record moves to the result side (§8.1/§8.3).

**Open — every item marked (sweep-gating) multiplies by ~70 classes if decided late:**

| # | Question | State | Recommendation on file |
|---|---|---|---|
| B6 | laws reading sibling fields of their own config (PV share) | open, 3 options | (a2) field-granular fixed point |
| B7 | cross-field laws (pellet 1/12) | follows B6 | (a2) ⇒ `Field(...)` term |
| B8 | `Catalog[ConfigT]` generics (sweep-gating) | open | do before sweep |
| B9 | which factories become presets (Weather lesson) (sweep-gating) | open | normative sentence: *identifier-parameterized lookups keep a constructor; presets are named variant sets* |
| B10 | author-declared `note=`/`source=` on values (sweep-gating) | open | optional, populate only where source known |
| C10 | UTSP `JsonReference` key-spelling bug | open | own commit + local-LPG verification |
| C11 | buffer storage sized from load vs generator power | open | bless status quo or schedule physics change |
| C12 | `heating_system` sizes to constant `FLOORHEATING` | open | bless as "AUTO = usual choice" + spec sentence |
| D13 | 14 unbuildable components: delete vs `obsolete/` | open | decide before sweep — shrinks it |
| Q5 | normalize preset *names* (64 legacy spellings) in the sweep? (sweep-gating) | open | yes — names are wire format, settle once |

---

## 3. Phases

Each phase is one session-sized unit with its own PR (pushes happen from the user's
other machine; this box never pushes). A phase is *done* when its gates pass and its
boxes are checked.

### Phase 0 — merge the layering base (no new code)

- [ ] PR #582 (`config_base_move`) merged into main.
- [ ] `config_presets` rebased onto the merge (drops its first commit; the seven
      design-B commits remain).

Gates: CI on #582 (golden suites untouched).

### Phase 1 — land the design-B foundation (replaces closed #576)

Open a **fresh PR** from the rebased `config_presets` with exactly the seven design-B
commits (§1). No new implementation work; the session's job is verification and PR
authoring.

- [ ] Full `pytest -m base` + `pytest tests/test_sizing.py tests/test_sizing_engine.py`.
- [ ] `pytest -m jsonconfig` (regenerated scenario JSONs are fresh:
      `scripts/regenerate_scenario_jsons.py` produces no diff).
- [ ] Golden gates: `scripts/golden_check.py` / `golden_validate.py` unchanged
      (presets must reproduce old factory values byte-identically).
- [ ] mypy + flake8 clean on the touched files.
- [ ] PR opened (base: main), describing machinery + three pilots; #576 linked as
      the closed predecessor.

### Phase 2 — port the spike's main-compatible conversions from `json_v2`

Port, adapting `hisim.sizing`/`hisim.sizing_engine` imports to `hisim.config` and
`building_config.py` targets to `building/config.py`. Use `git show json_v2:<file>` as
the source of truth; do **not** cherry-pick blindly — the module layout differs.

- [ ] `more_advanced_heat_pump_hplib.py` — heat pump + SH/DHW controller configs.
- [ ] `simple_water_storage.py` — `SimpleHotWaterStorage` + `SimpleDHWStorage` configs.
- [ ] `advanced_battery_bslib.py` (sizes from PV peak power via flat-pool fallback).
- [ ] `generic_pv_system.py` (carries the B6 workaround; convert as spiked, note the
      known trap — it is resolved in phase 4 when B6 is decided).
- [ ] `heat_distribution_system.py` — the controller-side fact contributions the spike
      added (the controller *config conversion itself* is batch S1).
- [ ] `electricity_meter.py`, `weather.py` (B9 pattern: `for_location(...)`),
      `building/config.py` extensions, EMS mypy channel fixes.
- [ ] Sizing-fact registry extensions on `SizingContext` for the above.
- [ ] Setups/tests of the touched components moved to `presets.X` / `resolve`; their
      legacy factories deleted; scenario JSONs regenerated.

Gates: same as phase 1, plus the touched components' own test files
(`test_more_advanced_heat_pump_hplib*.py`, `test_simple_hot_water_storage.py`,
`test_advanced_battery_bslib.py`, …). Value parity with the old factories is the
acceptance bar (golden + scenario freshness enforce it).

### Phase 3 — team review (blocks the sweep by design)

Agenda: `roadmap/design_review_questions.md` (on `json_v2`) + the artifact pair
(`heatpump_house_v2_template` / `_realized` / `_audit`). Not a coding session.

- [ ] B6 decided (and B7 with it). — [ ] B8. — [ ] B9. — [ ] B10.
- [ ] C10, C11, C12 each decided (physics/value changes are never bundled with sweeps).
- [ ] D13 decided (delete vs `obsolete/`).
- [ ] Q5: preset naming convention ratified (wire format — settle once).
- [ ] Decisions recorded in `config_defaults_spec.md` / review doc, statuses updated.

### Phase 4 — pre-sweep machinery finalization

Everything the sweep repeats ~70×, finished first:

- [ ] B8: `Catalog[ConfigT]` generics; preset access statically typed.
- [ ] B6 implementation per decision — (a2) field-granular fixed point + `Field("…")`
      term (contained in `config/sizing.py` + `config/engine.py`), or (a1)
      `reads_own=` for plain siblings. Rework the PV conversion to it; pellet/wood-chip
      minimal-power law per B7 outcome.
- [ ] `json_executor._get_default_config`: prefer `presets.canonical` when the class
      has a `Catalog`; keep the legacy name-match for unconverted classes; delete the
      heuristic entirely at sweep end (phase 9).
- [ ] B9 sentence + Q5 naming convention written into `config_defaults_spec.md`.
- [ ] B10 mechanism if adopted (`sized_field(..., note=)` / preset note).
- [ ] D13 executed: zombies/defective components deleted or archived
      (`controller_l1_building_heating`, `controller_l1_heatpump`,
      `controller_l1_generic_runtime`, `generic_battery`, `generic_ev_charger`, …
      per the D13 list) — shrinks the sweep denominator.
- [ ] Contract test skeleton: iterate every `Catalog`-bearing class ×
      {as-is, resolved(fixture ctx)}, invalid cells declared (`NothingToSizeError`),
      so each sweep batch only *extends coverage* instead of writing new test shapes.
- [ ] Delete the stray tracked `main` file in the repo root.

### Phases 5–8 — the repo-wide sweep, four batches (S1–S4)

Per-class recipe, applied identically in every batch (this is the "don't miss
anything" checklist — see also §5):

1. Classify per B9: variant set → presets; identifier lookup → named constructor
   (`for_…`), no presets for the open space.
2. Presets `Catalog` on the config class; sizable fields → `sized_field` with laws
   (function laws declare `reads=`); `SIZING_CONTRIBUTIONS` where the class feeds
   sibling facts; preset names per the Q5 convention.
3. Parity: new presets reproduce the old factory values exactly (assert in the batch
   commit if not already golden-covered).
4. Move every call site: `system_setups/*.py`, tests, other components.
5. Delete the legacy factories (never leave twins behind).
6. `scripts/regenerate_scenario_jsons.py`; commit the JSON diffs with the batch.
7. mypy, flake8, `scripts/check_config_attrs.py`.
8. Findings → `roadmap/random_findings.md` (`[bug]/[friction]/[spec]/[elegance]`).

Batch boundaries (counts = legacy factories from the §6 inventory; adjust after D13):

**Phase 5 = S1, heating generation & controllers (~20):**
- [ ] `generic_boiler.py` — the 4 `GenericBoilerControllerConfig` factories
      (power band via CONNECTED facts from the sized boiler)
- [ ] `heat_distribution_system.py` — `HeatDistributionControllerConfig` (1; C12 applies)
- [ ] `advanced_heat_pump_hplib.py` (3) — [ ] `generic_heat_pump.py` (2)
- [ ] `simple_heat_source.py` (4) — [ ] `generic_district_heating.py` (2)
- [ ] `generic_electric_heating.py` (2) — [ ] `idealized_electric_heater.py` (1)
- [ ] `night_setback_controller.py` (1)

**Phase 6 = S2, cooling, renewables, e-mobility (~15):**
- [ ] `air_conditioner.py` (3) — [ ] `simple_air_conditioner.py` (2)
- [ ] `controller_pid.py` (1) — [ ] `controller_mpc.py` (1)
- [ ] `solar_thermal_system.py` (2) — [ ] `generic_windturbine.py` (1)
- [ ] `advanced_ev_battery_bslib.py` (1) — [ ] `controller_l1_generic_ev_charge.py` (1)
- [ ] `generic_car.py` (2) — [ ] `generic_smart_device.py` (1)

**Phase 7 = S3, hydrogen & CHP (~16):**
- [ ] `generic_chp.py` (2) — [ ] `controller_l1_chp.py` (4)
- [ ] `advanced_fuel_cell.py` (1) — [ ] `generic_fuel_cell.py` (1)
- [ ] `controller_l1_fuel_cell.py` (1)
- [ ] `generic_electrolyzer.py` (1) — [ ] `generic_electrolyzer_h2.py` (1)
- [ ] `generic_electrolyzer_and_h2_storage.py` (2)
- [ ] `controller_l1_electrolyzer.py` (1) — [ ] `controller_l1_electrolyzer_h2.py` (1)
- [ ] `generic_hydrogen_storage.py` (1)

**Phase 8 = S4, meters, infrastructure, examples (~16):**
- [ ] `gas_meter.py` (1) — [ ] `fuel_meter.py` (1) — [ ] `heating_meter.py` (1)
- [ ] `sumbuilder.py` (1) — [ ] `transformer_rectifier.py` (1)
- [ ] `generic_price_signal.py` (1) — [ ] `random_numbers.py` (1)
- [ ] `loadprofilegenerator_utsp_connector.py` (1; C10 lands separately first)
- [ ] `configuration.py` (2)
- [ ] `example_component.py`, `example_template.py`, `example_storage.py`,
      `example_transformer.py`, `controller_l1_example_controller.py` (5 — these are
      the documented templates for component authors: update
      `CLAUDE.md`/how-to docs in the same batch)

Gates per batch: full `pytest -m base`, batch components' test files, jsonconfig
freshness, golden suites, mypy/flake8. One PR per batch.

### Phase 9 — closeout

- [ ] Delete `json_executor._get_default_config`'s legacy name-matching branch (all
      buildable classes now have `presets.canonical`).
- [ ] Contract test covers every config class; `DEFAULT_CONFIG_ARGUMENTS`-style
      exception tables gone.
- [ ] Grep gate: zero `def get_.*(default|scaled)` (non-connection) left under
      `hisim/components/`.
- [ ] `Component[TConfig]` generics ride-along (sweep-6 deferral in
      `roadmap/json_cleanup.md`) and remaining mypy escape hatches fall.
- [ ] Spec statuses updated (`config_defaults_spec.md` §8b marked superseded by this
      plan; this plan marked complete).
- [ ] Handoff note to the `json_v2` MR plan: preset names frozen (wire format),
      executor preset-reference work may proceed.

---

## 4. Out of scope (owned by the `json_v2` MR plan)

Entry-level `preset` in the v2 schema/executor, the template creator, the §8.3 audit
dump, grouped connections, the recording API, aggregator cutovers, setup conversion to
v2, v1 deletion. This plan only promises them a stable substrate: the `Catalog` API,
frozen preset names, and the fact engine.

## 5. Invariants for every phase (repeated here so no session forgets)

- **Numerical neutrality:** presets reproduce legacy factory values exactly; any
  deliberate value change is a C-item with its own commit and evidence. Golden +
  scenario-freshness gates enforce this.
- **No twins:** a converted class keeps no legacy factory.
- **Preset names are API** (wire format) — follow the Q5 convention; renames after
  landing are breaking changes.
- **Docstrings** ≥2–3 sentences on every new class/function; class-scope constants,
  no module-level mutable state.
- **Findings log:** append to `roadmap/random_findings.md`, full capture.
- **This box never pushes**; PRs are opened/pushed from the user's other machine.
  GitHub state is read via anonymous `curl https://api.github.com/repos/FZJ-IEK3-VSA/HiSim/...`.

## 6. Sweep inventory snapshot (2026-08-23, non-connection default/scaled factories)

52 modules, ~85 factories. Files already covered by phases 1–2 are marked; the batch
column binds every file to exactly one phase so nothing is orphaned.

| Module | Factories | Covered by |
|---|---|---|
| `simple_water_storage.py` | 5 | phase 2 |
| `more_advanced_heat_pump_hplib.py` | 4 | phase 2 |
| `generic_pv_system.py` | 3 | phase 2 |
| `advanced_battery_bslib.py` | 2 | phase 2 |
| `electricity_meter.py` | 1 | phase 2 |
| `weather.py` | 1 | phase 2 (B9 constructor) |
| `generic_boiler.py` (controllers) | 4 | S1 |
| `heat_distribution_system.py` (controller) | 1 | S1 |
| `advanced_heat_pump_hplib.py` | 3 | S1 |
| `generic_heat_pump.py` | 2 | S1 |
| `simple_heat_source.py` | 4 | S1 |
| `generic_district_heating.py` | 2 | S1 |
| `generic_electric_heating.py` | 2 | S1 |
| `idealized_electric_heater.py` | 1 | S1 |
| `night_setback_controller.py` | 1 | S1 |
| `controller_l1_heatpump.py` | 3 | D13 (zombie — delete/archive in phase 4) |
| `air_conditioner.py` | 3 | S2 |
| `simple_air_conditioner.py` | 2 | S2 |
| `controller_pid.py` | 1 | S2 |
| `controller_mpc.py` | 1 | S2 |
| `solar_thermal_system.py` | 2 | S2 |
| `generic_windturbine.py` | 1 | S2 |
| `advanced_ev_battery_bslib.py` | 1 | S2 |
| `controller_l1_generic_ev_charge.py` | 1 | S2 |
| `generic_car.py` | 2 | S2 |
| `generic_smart_device.py` | 1 | S2 |
| `generic_chp.py` | 2 | S3 |
| `controller_l1_chp.py` | 4 | S3 |
| `advanced_fuel_cell.py` | 1 | S3 |
| `generic_fuel_cell.py` | 1 | S3 |
| `controller_l1_fuel_cell.py` | 1 | S3 |
| `generic_electrolyzer.py` | 1 | S3 |
| `generic_electrolyzer_h2.py` | 1 | S3 |
| `generic_electrolyzer_and_h2_storage.py` | 2 | S3 |
| `controller_l1_electrolyzer.py` | 1 | S3 |
| `controller_l1_electrolyzer_h2.py` | 1 | S3 |
| `generic_hydrogen_storage.py` | 1 | S3 |
| `gas_meter.py` | 1 | S4 |
| `fuel_meter.py` | 1 | S4 |
| `heating_meter.py` | 1 | S4 |
| `sumbuilder.py` | 1 | S4 |
| `transformer_rectifier.py` | 1 | S4 |
| `generic_price_signal.py` | 1 | S4 |
| `random_numbers.py` | 1 | S4 |
| `loadprofilegenerator_utsp_connector.py` | 1 | S4 (after C10) |
| `configuration.py` | 2 | S4 |
| `example_component.py` | 1 | S4 |
| `example_template.py` | 1 | S4 |
| `example_storage.py` | 1 | S4 |
| `example_transformer.py` | 1 | S4 |
| `controller_l1_example_controller.py` | 1 | S4 |

(Regenerate this table when D13 lands:
`grep -rE "def get_[A-Za-z_]*([Dd]efault|scaled)[A-Za-z_]*\(" hisim/components/ | grep -v connection`.)
