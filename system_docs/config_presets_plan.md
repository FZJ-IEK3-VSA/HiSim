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

Main-compatible parts **not yet in the rebuilt package** (phase 4 ports these):

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
  learn `presets.canonical` (phase 3) before the sweep deletes factories broadly.
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

The arc in one sentence: **land the foundation (0–1), make every repeating decision
once (2), finish the machinery those decisions shape (3), then convert components —
first the eight the spike already proved (4), then everything else in four
domain-sized batches (5–8) — and finally remove the last legacy paths (9).** The
ordering rule behind it: anything that would have to be redone per class if it changed
(names, laws' capabilities, typing, test shape) is settled before the first
repetition.

Each phase is one session-sized unit with its own PR (pushes happen from the user's
other machine; this box never pushes). A phase is *done* when its gates pass and its
boxes are checked.

### Phase 0 — merge the layering base (no new code)

**Goal:** the `hisim/config/` package (with `ConfigBase`, `ComponentID`,
`DisplayConfig` in `base.py`) exists on main, so every later phase has the layer it
builds on. This is purely getting the already-open PR #582 over the line — no new
implementation.

- [ ] PR #582 (`config_base_move`) merged into main.
- [ ] `config_presets` rebased onto the merge. Its first commit is the same content
      as #582 and disappears in the rebase; the seven design-B commits remain.

Gates: CI on #582. The golden suites must be untouched — this PR only moves code and
rewrites imports, so any numeric change would mean the move went wrong.

### Phase 1 — land the design-B foundation (replaces closed #576)

**Goal:** the sizing machinery (`sizing.py`, `engine.py`, `presets.py`), the central
`AUTO` check in `Component.__init__`, the building's fact contributions, and the three
pilot conversions (boiler, heat distribution, EMS) are merged into main. After this
phase, any component *can* be converted to presets — the rest of the plan is about
converting them in the right order.

The code already exists on the rebased `config_presets` branch (§1); the session's job
is verification and PR authoring, not implementation. This is the successor of the
closed PR #576, re-opened now that the obsolete building commits are out of its diff.

- [ ] Full `pytest -m base` plus the machinery's own suites,
      `pytest tests/test_sizing.py tests/test_sizing_engine.py`, all green.
- [ ] `pytest -m jsonconfig` green, and `scripts/regenerate_scenario_jsons.py`
      produces no diff (the checked-in scenario JSONs match what the converted
      configs actually serialize to — the "freshness" gate).
- [ ] Golden gates unchanged (`scripts/golden_check.py` / `golden_validate.py`):
      the pilots' presets must reproduce the deleted factories' values exactly, so
      simulation results cannot move.
- [ ] mypy + flake8 clean on the touched files.
- [ ] Fresh PR opened against main (pushed from the other machine), describing the
      machinery and the three pilots; #576 linked as the closed predecessor.

### Phase 2 — team review: settle every question that shapes per-class work

**Goal:** each open design question from §2 has a recorded decision, so that no later
phase converts a class against rules that might still change. Not a coding session.
The agenda is `roadmap/design_review_questions.md` (on `json_v2`, where every question
is written out in full) plus the artifact pair the spike produced
(`heatpump_house_v2_template` / `_realized` / `_audit`); nothing beyond phase 1 is
needed, because the artifacts live on `json_v2` and the machinery is reviewable in the
phase-1 PR.

**Why the review sits here and not after the ports** (reordered 2026-08-23): preset
names are wire format, so every class converted before the naming convention (Q5)
risks a breaking rename; and the PV conversion exists on `json_v2` only in its
B6-workaround form — porting it before B6 is decided means converting it twice. The
decisions are cheap to make now and expensive to retrofit; conversions wait for them.

The machinery/API questions (each multiplies by ~70 classes if decided late):

- [ ] **B6 — may a sizing law read another field of the same config?** The concrete
      case: the PV power law needs `share_of_maximum_pv_potential`, a sibling field.
      Options: (a1) allow reads of plain (non-sizable) siblings only; (a2) lower the
      engine's unit of progress from config to field, so any field can wait on any
      concrete sibling (recommendation on file); (b) promote the share to a context
      fact. B7 (a law reading the *resolved* value of a sizable sibling — the pellet
      boiler's minimum power is 1/12 of its sized maximum) is decided by the same
      choice: under (a2) it becomes a `Field("…")` term; otherwise law composition
      stays the idiom.
- [ ] **B8 — make `Catalog` generic** (`Catalog[GenericBoilerConfig]`), so preset
      access is statically typed instead of `Any`. Small contained change; the
      question is only whether to do it before the sweep (recommended) or never.
- [ ] **B9 — which factories become presets at all.** Ratify the rule of thumb the
      Weather conversion established: *identifier-parameterized lookups (dozens of
      locations, TABULA codes, LPG households) keep a plain named constructor like
      `for_location(...)`; presets are only for genuine named variant sets.*
- [ ] **B10 — author-declared source notes.** Should hardcoded numbers be able to
      carry a citation as data (`sized_field(..., note="VDI 4645")`) so it reaches
      the audit record? Decide the shape now (strictly optional, only where a source
      is actually known) or reject it — half-maintained citations are worse than none.

The physics/value questions — each would change simulation results, so each needs its
own explicit decision and, if accepted, its own commit with before/after evidence
(never bundled into a conversion batch):

- [ ] **C10 —** the UTSP connector fixtures spell `JsonReference` keys in snake_case
      while the dataclass fields are `Name`/`Guid`/`StrVal`, so they silently
      deserialize to empty references (invisible only because the fixtures run on the
      predefined profile). Fix the spelling and verify a local-LPG run.
- [ ] **C11 —** buffer storages in the combustion setups are sized from the *building
      heating load* although the parameter is named after the *generator power* (a
      different number once the boiler is sized). Bless the status quo or schedule
      the change as a deliberate physics decision.
- [ ] **C12 —** `HeatDistributionControllerConfig.heating_system`'s law is the
      constant `FLOORHEATING`: `"AUTO"` there means "the usual choice", not "derived
      from the building" (TABULA has no heat-distribution data). Bless that semantics
      with a spec sentence, or commission a building-age→radiator heuristic (a
      result-changing decision for old buildings).

Process questions:

- [ ] **D13 —** 14 components cannot be built from their own default configs (three
      zombies importing long-deleted modules, six defective, five legitimately
      data-dependent). Decide: delete outright, or move to `obsolete/`.
- [ ] **Q5 — the preset naming convention.** Today's 64 factory-name spellings must
      collapse into one scheme (casing, fuel/variant order, when to suffix a rating
      like `_12kw`). Names become wire format the moment the v2 executor accepts
      preset references, so this is an API-naming decision — settle it once, here.
- [ ] Every decision recorded in `config_defaults_spec.md` / the review doc in the
      same session, statuses flipped from "open" to "decided".

### Phase 3 — pre-sweep machinery finalization (implements the review's outcomes)

**Goal:** the machinery and conventions are in their *final* shape, so that the ~60
class conversions in phases 4–8 are purely mechanical repetition and none of them ever
has to be revisited. Everything in this phase is something the conversions would
otherwise repeat or contradict.

- [ ] Implement B8: make `Catalog` generic (`Catalog[ConfigT]`), so `presets.oil`
      has a real static type. Every conversion after this point gets typed preset
      access for free; without it, the sweep would replicate `Any`-typed access ~70×.
- [ ] Implement the B6 decision — under (a2): field-granular fixed-point resolution
      plus a `Field("…")` law term, contained in `config/sizing.py` +
      `config/engine.py`; under (a1): a `reads_own=` declaration restricted to plain
      sibling fields. Adjust the pellet/wood-chip minimal-power law (already on
      `config_presets`) to the outcome. The PV conversion itself waits for phase 4 —
      no interim workaround gets committed to main.
- [ ] Teach the **v1 executor** about presets: `json_executor._get_default_config`
      currently discovers a default by name-matching `*default*` methods and errors
      on classes with zero or several — which after conversion is *every* converted
      class. Change it to prefer `presets.canonical` when the class has a `Catalog`,
      keeping the legacy name-match for not-yet-converted classes. (The heuristic is
      deleted entirely in phase 9, when no class needs it anymore.)
- [ ] Write the B9 rule ("lookups keep a constructor, presets are variant sets") and
      the Q5 naming convention into `config_defaults_spec.md`, then audit the three
      pilots' existing preset names against the convention and rename where they
      deviate — renaming is still free, nothing referencing them has shipped.
- [ ] Implement the B10 note mechanism if the review adopted it
      (`sized_field(..., note=)` and/or a per-preset note).
- [ ] Execute the D13 decision: delete or archive the unbuildable components
      (`controller_l1_building_heating`, `controller_l1_heatpump`,
      `controller_l1_generic_runtime`, `generic_battery`, `generic_ev_charger`, …
      per the D13 list). Doing this *before* the sweep shrinks it — nobody converts
      a class that is about to be deleted.
- [ ] Build the contract-test skeleton: one parameterized test that iterates every
      `Catalog`-bearing config class over {preset as-is, preset resolved against a
      fixture context}, with invalid cells declared rather than try/excepted
      (`NothingToSizeError` for classes with nothing to size; surviving `AUTO`
      expected in unsized cells). Each sweep batch then only *adds classes to
      coverage* instead of inventing new test shapes.
- [ ] Delete the stray tracked `main` file in the repo root (§1, commit accident).

*Fallback if the review cannot be scheduled promptly:* the decision-independent items
(executor bridge, contract-test skeleton, stray-file deletion) may be pulled forward;
everything whose shape a review question determines (generics API, B6, names, notes,
D13) stays blocked — that blockage is the point of the ordering.

### Phase 4 — port the spike's conversions from `json_v2` into the new package

**Goal:** the eight component modules the spike already converted on `json_v2` are
converted on main too — written against the *final* machinery and naming from phases
2–3, so the heat-pump example's whole thermal chain sizes through the fact engine.
This is the last "porting" phase; everything after it is fresh conversion work.

Method: use `git show json_v2:<file>` as the reference, then adapt — the spike uses
the old module layout (`hisim.sizing`, `hisim.sizing_engine`, `building_config.py`),
which maps to `hisim.config` and `building/config.py` here. Do **not** cherry-pick
commits blindly; where phase-2/3 outcomes (names per Q5, B6 mechanism, generics)
disagree with the spike, the outcomes win and the ported code deviates deliberately.

- [ ] `more_advanced_heat_pump_hplib.py` — the heat pump config plus its
      space-heating and DHW controller configs.
- [ ] `simple_water_storage.py` — `SimpleHotWaterStorage` + `SimpleDHWStorage`
      configs (buffer and DHW volume laws; C11's blessing/decision applies here).
- [ ] `advanced_battery_bslib.py` — sizes from the PV peak power fact; this is the
      case that needs the engine's flat-pool fallback, because battery and PV are
      two wiring hops apart (both connect only to the EMS).
- [ ] `generic_pv_system.py` — written directly against the B6 decision; the spike's
      per-preset `scaled_power_law` workaround is reference material, not the target.
- [ ] `heat_distribution_system.py` — the controller-side fact *contributions* the
      spike added (water mass flow, temperatures). The controller's own config
      conversion is batch S1, not here.
- [ ] `electricity_meter.py`; `weather.py` (the B9 pattern: presets only for
      `aachen`/`seville`, a `for_location(...)` constructor for the open location
      space); `building/config.py` extensions; the EMS mypy channel fixes.
- [ ] Extend `SizingContext` with the facts this chain reads (water mass flow,
      storage set-temperatures, PV peak power, generator power band) — the `Size.*`
      term vocabulary follows automatically from the dataclass fields.
- [ ] Move all call sites (system setups, tests) of the touched components to
      `presets.X` / `resolve(ctx)`; delete their legacy factories; regenerate the
      scenario JSONs.

Gates: same as phase 1, plus the touched components' own test files
(`test_more_advanced_heat_pump_hplib*.py`, `test_simple_hot_water_storage.py`,
`test_advanced_battery_bslib.py`, …). Value parity with the old factories is the
acceptance bar (golden + scenario freshness enforce it).

### Phases 5–8 — the repo-wide sweep, four batches (S1–S4)

**Goal (shared by all four batches):** every remaining config class in
`hisim/components/` uses presets and sized fields instead of `get_*default*`/
`get_scaled_*` factories, with values reproduced exactly. Each batch is one
session-sized PR over one domain of components, so a half-done sweep is always a set
of *whole* converted classes, never a class in limbo.

Per-class recipe, applied identically to every class in every batch (this is the
"don't miss anything" checklist — see also §5):

1. **Classify** per the B9 rule: a genuine set of named variants becomes presets; an
   identifier-parameterized lookup (locations, database model names, household
   definitions) keeps a plain named constructor (`for_…`) and gets no presets for
   the open space.
2. **Convert:** a `Catalog` of presets on the config class (names per the Q5
   convention); fields whose values the old `get_scaled_*` twin computed become
   `sized_field`s with their law declared at the field (function laws declare
   `reads=`; enum-typed fields pass `value_type=`); if other components size against
   this one, declare its `SIZING_CONTRIBUTIONS`.
3. **Prove parity:** the new presets must reproduce the old factory values exactly —
   assert it in the batch commit wherever the golden suites don't already cover the
   component.
4. **Move every call site:** `system_setups/*.py`, tests, and other components stop
   calling the old factories and use `presets.X` / `resolve(ctx)`.
5. **Delete the legacy factories** — a converted class keeps no twin.
6. **Regenerate** the scenario JSONs (`scripts/regenerate_scenario_jsons.py`) and
   commit the JSON diffs with the batch.
7. **Static checks:** mypy, flake8, `scripts/check_config_attrs.py`.
8. **Log findings** in `roadmap/random_findings.md`
   (`[bug]/[friction]/[spec]/[elegance]`) — full capture, not curation.

Batch boundaries below; the number after each file is its legacy factory count from
the §6 inventory (recount after D13 removes components):

**Phase 5 = S1, heating generation & controllers (~20).** Starts with the two
controller configs that live in already-converted pilot files, because they exercise
the hardest sizing pattern (facts from a *sibling component*, not the building):

- [ ] `generic_boiler.py` — the 4 `GenericBoilerControllerConfig` factories; the
      controller's min/max power band is derived from the sized boiler it is
      connected to, i.e. a CONNECTED-scoped fact the boiler already contributes
- [ ] `heat_distribution_system.py` — `HeatDistributionControllerConfig` (1); its
      `heating_system` field carries the C12 semantics ("AUTO = the usual choice")
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

**Goal:** no legacy default machinery survives anywhere — not in components, not in
the executor, not in tests — and the presets/sizing layer is declared stable so the
`json_v2` track can build its wire format on top of it.

- [ ] Delete `json_executor._get_default_config`'s legacy name-matching branch: after
      the sweep, every buildable class has `presets.canonical`, so the phase-3 bridge
      is the only path and the heuristic is dead code.
- [ ] The contract test covers every config class, and any per-class exception
      tables it needed along the way (the `DEFAULT_CONFIG_ARGUMENTS` pattern) are
      gone — a class either enumerates cleanly or declares its invalid cells.
- [ ] Grep gate: zero non-connection `def get_.*(default|scaled)` methods left under
      `hisim/components/` (the §6 regeneration command returns nothing).
- [ ] Ride-alongs the sweep unblocks: `Component[TConfig]` generics (the sweep-6
      deferral in `roadmap/json_cleanup.md`) and the remaining mypy escape hatches
      around config typing fall away.
- [ ] Spec statuses updated: `config_defaults_spec.md` §8b marked superseded by this
      plan; this plan marked complete.
- [ ] Handoff note to the `json_v2` MR plan: preset names are frozen (wire format
      from here on — any later rename is a breaking change with a migration note),
      so the executor's preset-reference work may proceed.

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
| `simple_water_storage.py` | 5 | phase 4 |
| `more_advanced_heat_pump_hplib.py` | 4 | phase 4 |
| `generic_pv_system.py` | 3 | phase 4 |
| `advanced_battery_bslib.py` | 2 | phase 4 |
| `electricity_meter.py` | 1 | phase 4 |
| `weather.py` | 1 | phase 4 (B9 constructor) |
| `generic_boiler.py` (controllers) | 4 | S1 |
| `heat_distribution_system.py` (controller) | 1 | S1 |
| `advanced_heat_pump_hplib.py` | 3 | S1 |
| `generic_heat_pump.py` | 2 | S1 |
| `simple_heat_source.py` | 4 | S1 |
| `generic_district_heating.py` | 2 | S1 |
| `generic_electric_heating.py` | 2 | S1 |
| `idealized_electric_heater.py` | 1 | S1 |
| `night_setback_controller.py` | 1 | S1 |
| `controller_l1_heatpump.py` | 3 | D13 (zombie — delete/archive in phase 3) |
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
