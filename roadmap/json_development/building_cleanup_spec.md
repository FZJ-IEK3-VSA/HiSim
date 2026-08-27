# Building cleanup — phased specification

**Status:** agreed plan, 2026-08-21. **Branch:** `building_cleanup` (off main), one PR per
phase. **Motivation:** `hisim/components/building.py` is 3,082 lines holding five classes
with very different jobs — `BuildingConfig` (~130 lines), `BuildingState` (~30),
`Building` (~2,000), `Window` (~135), `BuildingInformation` (~690) — and the smaller
classes carry accumulated hazards (an 11-tuple return unpacked positionally, temporal
coupling through self-mutation, in-place mutation of TABULA reference data, a fake
dataclass decoration, tenfold copy-paste of the element-parameter pattern). The cleanup
runs BEFORE the config-presets redesign is rebuilt on top of it, so that the design PR
lands on clean, small files and its diff shows design, not archaeology.

## Ground rules (all phases)

1. **No value changes, ever.** The cleanup is behavior-identical by definition. Physics
   oddities discovered along the way (the `delta_U_ThermalBridging = 0.1` patch-in-place,
   the 92.1 m² magic apartment size, the door-area guard, ventilation constants) are
   LOGGED as findings for the design review's C-section, never fixed in a cleanup commit.
2. **The harness decides.** Phase 1's snapshots are the referee for every later commit; a
   red snapshot means the commit is wrong, not the snapshot. Regenerating a snapshot
   requires the explicit regeneration flag AND a commit-message justification; during this
   cleanup the only legal justification is a phase-2 metadata field (module paths), never
   a number.
3. **Harness imports only the public path** (`hisim.components.building`), which phase 2
   keeps alive via the package `__init__`. The harness is written once in phase 1 and is
   not edited in any later phase — a harness that must change mid-refactor proves nothing.
4. Repo conventions apply: extensive docstrings, class-scoped constants, one concern per
   commit, findings appended to `roadmap/declarative_energy_systems/random_findings.md`.

## Phase 1 — the three-layer testing harness (first PR, pure addition)

**Layer 1: `BuildingInformation` characterization snapshot.**
A parametrized test instantiates `BuildingInformation` for **every building code in the
TABULA housing CSV** and compares every derived public attribute (floats exact, no
rounding) against a committed golden JSON (`tests/goldens/building_information.json`,
sorted keys). Details:

- The CSV read is cached by a test fixture (module-scoped memoization of the raw
  DataFrame), because the production class currently re-reads the file per
  instantiation — fixing that is phase 3, and the harness must be fast *now* (target:
  the full sweep in seconds).
- Codes that currently **crash** the class are pinned as `"raises: <ErrorType>:
  <message-prefix>"` snapshot values — current breakage becomes recorded behavior, not a
  test failure. If a later phase accidentally *fixes* a crash, the snapshot goes red and
  the fix is moved to its own reviewed commit.
- The sweep also runs a second config variant for a handful of codes (explicit envelope
  U-values/areas, scaled floor area, `total_base_area` instead of absolute) so the
  config-override branches of every `set_*` method are inside the net.

**Layer 2: one-day `Building` simulation snapshot.**
A component-level test that constructs `Building` from the default config, feeds 96
timesteps (15-min) of **synthetic fixed input vectors** directly through the
`i_simulate` loop (sinusoidal outdoor temperature, a fixed irradiance profile, constant
occupancy gains, a stepped thermal-power delivery) — no full simulator, no weather
files — and snapshots **all output vectors** (not aggregates: vectors catch sign flips
that averages hide) to `tests/goldens/building_one_day.json`. One save/restore round
mid-run exercises the convergence path. A second run with a scaled config (different
floor area) covers the scaling branches. Target runtime: seconds.

*Amended 2026-08-21 (findings entry 25):* layer-2 float comparison is exact **up to a
few-ULP platform band** (`PLATFORM_ULP_TOLERANCE`), not bitwise — the trig-derived
columns (window model `math.cos`, pvlib) legitimately differ in the last bit between
libm builds, which turned CI red on 1-ULP shifts the day after the merge. Layer 1
contains only IEEE basic arithmetic and stays strictly bitwise; it remains the referee
for summation-order-level changes (the `sum()` and door-round-trip catches were layer-1
catches and remain reproducible).

**Layer 3: the existing golden / parity / scenario-freshness CI gates.**
Nothing to build — declared here as the slow backstop every phase PR runs behind, exactly
like every sweep before it.

Snapshot regeneration: `pytest tests/test_building_characterization.py --regenerate`
(or an env var, implementer's choice) rewrites the goldens; the diff of the golden file
is part of the PR under rule 2.

**Harness lifetime:** layer 1 stays permanently (cheap, high-value regression net);
layer 2 stays under the `buildingtest` marker; both may be relaxed once the eventual
`Building` redesign supersedes them — a decision for then, not now.

## Phase 2 — the mechanical split (pure moves, zero behavior change)

Target layout — split by responsibility, not one-file-per-class dogma
(`BuildingState` is ~30 lines used only by `Building`'s save/restore and stays with it):

```
hisim/components/building/
    __init__.py        # re-exports ALL public names; keeps hisim.components.building.* alive
    config.py          # BuildingConfig
    information.py     # BuildingInformation
    window.py          # Window
    building.py        # Building + BuildingState
```

Rules and consequences:

- **Pure moves**: `git mv`-style relocation, no reformatting, no renames, no cleanup in
  the same commit — blame must survive.
- **The module path is wire format.** 23 committed scenario JSONs reference
  `hisim.components.building.Building`; `get_full_classname()` derives from
  `__module__`. The re-exporting `__init__` keeps every existing JSON string AND every
  Python import resolving (importlib finds `Building` as an attribute of the package),
  but newly *generated* JSONs will carry the submodule path — so the scenario JSONs are
  regenerated once, as their own commit inside the phase-2 PR, values verified unchanged
  by the freshness gate (precedent: the `BuildingConfig` module move in the presets
  spike).
- Check and note anything that scans the components directory (`check_config_attrs.py`,
  executor discovery, docs tooling) for flat-module assumptions.
- The harness runs unchanged before and after — that is the phase's entire proof.

## Phase 3 — `BuildingInformation` stage 1: the hazards

Behavior-identical fixes, one concern per commit, snapshot-verified:

1. Replace the 11-tuple return of `get_some_reference_data_from_tabula` with named
   attribute assignments (or a small frozen dataclass) — the file's worst
   positional-swap hazard.
2. Remove the fake `@dataclass_json @dataclass` decoration (hand-written `__init__`,
   zero fields — the decorators are no-ops that misadvertise the class).
3. Delete the ~40 dead bare annotations at the top of `__init__`.
4. Stop mutating the TABULA reference row (`A_C_Ref` write-back,
   `delta_U_ThermalBridging` patch): read once into an immutable snapshot, keep
   corrections as explicit local values with a comment naming the C-section finding.
5. Cache the housing-CSV read at class scope (one read per process, keyed by nothing —
   the file is static; the harness fixture's memoization becomes redundant but harmless).
6. Deduplicate the twin branches in `get_scaling_factor_according_to_conditioned_living_area`
   (absolute-area vs base-area — identical arithmetic) and in `get_number_of_apartments`.
7. Simplify the door `(u * area) / area` computation; fix the copy-paste-wrong
   docstrings ("roof" on the floor method); name the magic numbers as class-scoped
   constants (92.1, 0.34, 9.1, 4.5, 3.45) without changing them.

## Phase 4 — `BuildingInformation` stage 2: the element table

Collapse the ten copy-paste methods (`set_{floor,wall,roof,window,door}_area_parameter`
+ five `_heat_transfer_parameter` twins) into one class-scoped element table
(element → TABULA area columns, U-value columns, transmission-adjustment columns and
rule, config-override field names) driving a single `_area()` and a single
`_conductance()` helper. ~330 lines become ~70; adding an element becomes a table row.
Window keeps its extra per-direction scaling as the one legitimate special case.
Temporal coupling is dissolved as far as this class goes: derived quantities become an
explicit ordered pipeline (or `cached_property` chain) instead of mutation-order
convention.

## Phase 5 — `Window` (detailed 2026-08-21)

Two commits: **5a** a small unit pin, **5b** the refactor. Physics untouched throughout;
`building/window.py` plus exactly one sanctioned call-site edit in `building/building.py`.

**5a — unit pin (pure addition).** Layer 2 exercises the window model only through
`Building`; before restructuring, pin `calc_solar_heat_gains` directly: a small
parametrized test over a fixed grid of (tilt, azimuth, irradiance tuple, zenith) inputs
covering the normal path, the `poa_direct` NaN → `0` return, and the `None`-azimuth
fallback (result equals azimuth-0, and the `log.warning` fires exactly once per
instance). Literal expected values pinned in the test (no new golden file needed at this
size). This pin — not layer 2 alone — is what licenses the cache restructure in 5b.

**5b — the refactor**, one concern per change, harness + 5a green after the commit:

1. **Delete the dead methods.** `calc_direct_solar_factor` and
   `calc_diffuse_solar_factor` have zero callers repo-wide (verified 2026-08-21 across
   `hisim/`, `tests/`, `system_setups/`, `obsolete/`). They go, and the
   `window_tilt_angle_rad` side-effect mutation dies with them. Deletion is
   behavior-neutral by definition; record it in the findings log in case the team wants
   the formulas preserved somewhere (they remain in git history and the cited papers).
2. **Resolve the `@lru_cache` wart.** The method-level `lru_cache(maxsize=16)` is why
   the caller re-passes the window's own attributes into the call
   (`building.py` hands back `window.window_tilt_angle`, `window.window_azimuth_angle`,
   `window.reduction_factor_with_area` so they land in the cache key), and a cache bound
   to the class holds instance references for the process lifetime. Fix: the
   irradiance computation becomes a pure cached `staticmethod` (or an explicit
   small memo — implementer's call) taking only genuine inputs; the instance method
   wraps it, supplying its own attributes, so the **caller stops re-passing them**.
   The one sanctioned `building.py` edit is slimming that call site accordingly.
   Caching affects speed only — the function is pure — so layer 2 red after this step
   means an arithmetic slip, not a caching difference.
3. **Keep the `None`-azimuth fallback behavior exactly**: default to 0 (south) with the
   warn-once `log.warning` per instance — but move the warn-once flag handling out of
   the cached function so the cache cannot swallow or duplicate the warning
   (5a pins the message count).
4. **Constructor honesty.** All seven parameters default to `None`, yet the single
   production construction site (`building.py`, the per-direction window loop) passes
   every one, and `reduction_factor` is computed unconditionally in `__init__` (a real
   `None` crashes today, just later and less clearly). Make the parameters required
   with real types; `window_azimuth_angle` stays `Optional` — the fallback in (3)
   exists precisely for it (the Horizontal direction carries no azimuth). Delete the
   dead `incident_solar` annotation and the now-orphaned `window_tilt_angle_rad`
   initialization.
5. **Fix the citations.** The `(** Check header)` markers point at the old module
   header, which since phase 2 lives in the package `__init__` docstring — repoint them
   or inline the actual citation (`rc_buildingsimulator`/Jayathissa; the Quaschning &
   Hanitsch reference belongs to the deleted method and goes with it).

## Phase 6 — the `Building` component (first draft 2026-08-21; go/no-go per sub-phase, decided AFTER phases 3–5)

A full read of the split-out `building/building.py` (2,071 lines) gives this
responsibility map, which is the basis of the draft:

| block | ~lines | notes |
|---|---|---|
| `BuildingState` | 30 | fine as is |
| `__init__` channel/output declarations | 270 | mechanical component surface, stays |
| default connections (5 methods) | 150 | stays (v2 C6 will rework them anyway) |
| `i_simulate` orchestration | 205 | incl. solar-cache side effects |
| lifecycle + MPC forecast block in `i_prepare_simulation` | 110 | SingletonSimRepository reads/writes |
| `build()` + `get_windows()` | 130 | incl. six Singleton 5R1C-parameter writes and the solar disk-cache read |
| report / `__str__` | 65 | stays |
| cost opex/capex | 55 | stays — replaced wholesale by the new cost module |
| KPI computation (4 methods) | 245 | pure postprocessing living in the component |
| 5R1C model: conductances, heat-flow split, Crank–Nicolson, ISO 13790 C.4 demand | 700 | pure math over BuildingInformation constants |
| dead code (see 6a.4) | ~55 | |

### 6a — split-outs that go FIRST (each low-risk, harness-covered, own commit/PR)

1. **KPI module** (`building/kpi.py`, ~245 lines): `get_component_kpi_entries` and its
   three helpers are postprocessing that only reads `BuildingInformation`, the set
   temperatures and the results frame. The component method stays as a thin delegate
   (the framework calls it on the component); the bodies move. Near-pure move.
2. **Predictive/MPC block** (`building/predictive.py`, ~110 lines): the
   `i_prepare_simulation` forecast computation and `build()`'s six
   SingletonSimRepository writes of 5R1C parameters (PID/MPC channel). Moving both
   isolates ALL of the component's singleton traffic in one file — directly useful for
   the pending singleton redesign (see the dead-keys finding in
   `roadmap/declarative_energy_systems/random_findings.md`): the channel becomes one greppable module instead of
   code woven through lifecycle methods.
3. **Solar-gains provider** (`building/solar_gains.py`, ~130 lines): `get_windows()`,
   `get_solar_heat_gain_through_windows()` and the disk-cache read/write. This
   isolates the known cache order-dependence (harness findings entry 9: with a warm
   cache `i_simulate` never evaluates the window model) into one small file, making
   the later fix decision a local one. CRITICAL: the `utils.get_cache_file` key inputs
   (component name, config, simulation parameters) must stay byte-identical, or every
   user's cache silently invalidates — acceptable but then *deliberate*.
4. **Dead-code deletion** (verified 2026-08-21): `get_building_params()` returns a
   10-tuple whose order does NOT match its unpacking in `build()` — six of the ten
   assigned attributes hold the wrong quantity (`facade_area_in_m2` receives the floor
   U-value, etc.). Nothing ever reads any of the ten, which is why the bug is latent:
   the whole method + unpack is dead and gets deleted, with the findings log recording
   it as the live specimen of the tuple-unpack hazard class (the same class phase 3
   removed from `BuildingInformation`). Also gone: the commented-out `heat_loss`
   computation lines and the stale `# class Building(dynamic_component...)` remnant.

### 6b — the model extraction (the actual go/no-go)

Extract the 5R1C mathematics (~700 lines: the `H_tr_1/2/3` properties, conductance
assembly, `calc_internal_heat_flows_*`, `calc_crank_nicolson` and its five helper
equations, the ISO C.4 theoretical-demand three-step) into a
`ThermalModel5R1C` class in `building/thermal_model.py`, constructed once in `build()`
from what the math actually reads: the six conductances, the thermal capacity,
effective mass area, total internal surface area, the h_ms fixed value and
`seconds_per_timestep` — all of which come from `BuildingInformation` or
`SimulationParameters`. The component keeps `BuildingState`, the channels and the
orchestration; `i_simulate` calls the model. Layer 2 pins every output vector; the
extraction is behavior-identical by the same rules as everything else. Payoff beyond
readability: the model becomes directly unit-testable and reusable (the theoretical
demand C.4 block is what the building sizer conceptually re-implements today).

### 6c — optional, decided after 6b

Slim `i_simulate` into pure orchestration (input reads → model call → output writes),
and revisit the `hasattr(self, "solar_gain_through_windows")` cache-mode branching
once 6a.3 has localized it. Alignment note: the v2 C6 aggregator conversion (building
heat-source inputs become dynamic connections) touches `__init__` and the default
connections — sequence 6x and C6 so they do not collide; the split-outs above make
that collision surface smaller, not larger.

Expected end state: `building/building.py` at roughly 800 lines of honest component
glue, with model, KPI, predictive channel and solar-gain provider each in their own
reviewable file.

## Integration points

- **`config_presets` is rebuilt on top of phase 4** (not after phase 6): the design-B
  payload (presets Catalog, `SIZING_CONTRIBUTIONS`, `_building_sizing_facts`,
  `SizingContext.for_building`) re-lands in `building/config.py` /
  `building/information.py`; the sizing machinery, engine and the boiler/HDS/EMS pilots
  rebase mechanically. The old `config_presets` branch is force-replaced — nobody has
  reviewed it.
- **`json_v2` stays frozen** as the spike/demo line (artifacts, closing-the-loop proof,
  design-questions doc). It is not rebased onto the new layout; the v2 work reaches main
  by carving fresh MRs against the new layout, per the existing MR plan.
- Phases 3–5 findings that are physics questions go to
  `roadmap/design_review_questions.md` section C, not into cleanup commits.
