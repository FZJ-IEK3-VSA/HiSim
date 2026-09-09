# Feasibility Analysis: Converting HiSim from Python to Rust

**Analyzed commit:** `67ba1ca9` (main, 2026-09) · **Repo:** FZJ-IEK3-VSA/HiSim (`/home/noah/HiSim`)
**Method:** Each module is analyzed in isolation and this document is updated immediately after each module analysis, rather than one big retrospective. A cross-module synthesis is added at the end.

---

## 1. Repository Overview and Methodology

### 1.1 Codebase at a glance

| Module | Py files | LOC | Role |
|---|---|---|---|
| `hisim/components/` | 67 | 46,142 | Component library (devices, controllers, loads, weather, building) |
| `hisim/economics/` | 51 | 21,623 | Lifecycle cost engine (new: financing, tariffs, subsidies, audit) |
| `hisim/energy_system/` | 55 | 19,406 | Declarative YAML energy-system format: loader, schema, executor, sizing, recording, grouping |
| `hisim/postprocessing/` | 16 | 6,472 | KPIs, charts, PDF/HTML/CSV reports |
| `hisim/config/` | 12 | 3,896 | Config layer (`ConfigBase`, `ComponentID`, `DisplayConfig`) |
| `hisim/renovisor/` | 8 | 1,636 | RenoVisor REST translator (measures → setup) |
| `hisim/building_sizer_utils/` | 6 | 1,570 | Building-sizer interface configs |
| `hisim/caching/` | 5 | 1,309 | Result caching client (local + remote tiers) |
| `hisim/` top level | ~15 | ~7,700 | Core engine: `simulator.py`, `component.py`, `json_executor.py`, CLI |
| `system_setups/` | 23 | 7,675 | One setup function per file + `.scenario.json` twins |
| `tests/` | 208 | 55,322 | pytest suite (markers: base, buildingtest, system_setups, mpc, utsp, jsonconfig) |
| `tools/` + `scripts/` | 61 | 12,966 | Worked-example converter, parity/golden tooling, HPC harness |
| **Total Python** | **~560** | **~186,000** | |
| `hisim/inputs/` | — | 569 MB | Reference data: weather (NSRDB/DWD), PV processed, TABULA, load profiles |
| `system_setups/results/` + goldens | — | large | Regression reference outputs (do not regenerate) |

### 1.2 What kind of system HiSim is

HiSim is a discrete time-step co-simulation: components wired through typed input/output ports, iterated per time step until convergence, then post-processed. Three architectural layers matter for a language conversion:

1. **Simulation core** — a small engine (`simulator.py`, `component.py`, `component_wrapper.py`, `sim_repository.py`) with a component base class and a generic per-timestep fixed-point loop. This is the *most Rust-friendly* part: it is a tight, imperative, allocation-heavy numeric loop.
2. **Component library** — ~60 device/controller models, each a `Component` subclass with a `ConfigBase` dataclass config. Heterogeneous physics: ODEs, lookup tables, curve fits, control logic.
3. **Ecosystem layers** — declarative YAML loading with reflection-based instantiation, JSON scenario execution, KPI/chart/PDF reporting, cost engine, LPG/UTSP network connector, caching, webtool JSON export.

### 1.3 Global, cross-cutting findings (preview)

These recur in nearly every module and dominate the total conversion cost — details per module below:

- **Domain-library gap (the single biggest risk).** HiSim's physics is not self-contained. It directly imports `pvlib`, `hplib`, `bslib`, `oemof.thermal`, `windpowerlib`, `pygfunction`, `control` + `casadi` (MPC), `numpy_financial`, `pylpg` (load profiles), `utspclient` (network service). **None of these have production-grade Rust equivalents.** Converting HiSim means either reimplementing them (pvlib alone is a multi-person-year effort) or keeping a Python island via PyO3.
- **Reflection-based instantiation.** `.scenario.json` files and YAML energy-system files name components by fully-qualified Python class path (`hisim.components.generic_heat_pump.GenericHeatPump`); `json_executor.py`/`energy_system/classes.py` do `importlib.import_module` + `getattr`. Rust has no reflection — this needs an explicit component registry (an inventory that today the language generates for free, and which ~60 components and ~25 setup files would need to join).
- **Config layer is runtime-serialized dataclasses.** 141 files use `@dataclass`; configs round-trip to JSON via `dataclasses_json`/`pyhumps` with snake_case↔camelCase conversion and `get_default_*` factory classmethods. Rust maps this well (serde + defaults), but every config's *exact* field names, defaults, and enum spellings are public interface — the JSON files in `system_setups/` pin them.
- **Dynamic Python in the seams.** 63 files use `getattr`/`setattr`/`hasattr`; 23 use `importlib`; `DynamicComponent` resolves ports at runtime by matching unit/type tags; 69 enum classes; the `SimRepository` is a shared mutable string-keyed dict (recently de-singleton'd, still duck-typed).
- **Numeric-equivalence burden.** The golden/regression tests compare against committed CSV results computed with NumPy/pandas float semantics and library-internal curve fits. Bit-level reproduction in Rust is *not guaranteed* even for identical formulas (fma, libm differences, summation order); tolerances would have to be re-baselined.
- **Moving target.** The last merge alone changed 859 files (+100k/−60k). A multi-year rewrite would chase a codebase under heavy active development — this is an organizational, not technical, feasibility factor.

### 1.4 Rating scheme used below

- **Challenge:** 🟢 Low (mechanical translation) / 🟡 Medium (design decisions needed) / 🟠 High (substantial redesign) / 🔴 Very High (no direct equivalent; reimplementation or interop required)
- **Effort:** S (< 1 person-week) / M (weeks) / L (person-months) / XL (person-years)

## 2. Core Simulation Engine

**Files:** `hisim/simulator.py` (660), `component_wrapper.py` (257), `sim_repository.py` (125), `sim_repository_singleton.py` (236), `simulationparameters.py` (319), `utils.py`, `log.py`, `result_path_provider.py` — ~2,000 LOC total.

### 2.1 What it does and how it maps

The engine (`Simulator.process_one_timestep`, simulator.py:162–227) runs a fixed-point iteration per time step: save all component states → loop { restore state; `i_simulate` every component in registration order; check convergence } → `i_doublecheck`. Convergence = every output within **absolute 1e-4** of the previous iteration (`SingleTimeStepValues.is_close_enough_to_previous`, component.py:190–196); after >10 iterations convergence is forced, >100 raises.

**This is the most Rust-friendly code in the repository:**

- `SingleTimeStepValues` is already a flat float array indexed by a global output index — in Rust a `Vec<f64>` plus typed port handles. No allocation in the hot loop needed (two buffers swapped).
- The `i_save_state`/`i_restore_state` checkpoint pattern (components keep a cloned copy or `deepcopy` themselves) maps 1:1 to a `#[derive(Clone)]` state struct + `std::mem::swap` — cleaner and much faster than Python's deepcopy.
- Execution is single-threaded, deterministic, registration-order dependent — all trivially preserved. Rust would plausibly give a **10–50× speedup** on this loop (no interpreter, no boxing of floats), which is the main *performance* argument for the whole conversion.

### 2.2 Challenges

| Issue | Detail | Rust answer | Severity |
|---|---|---|---|
| Shared mutable state | `SimRepository` = `dict[str, Any]` + `dict[ComponentType, dict[int, Any]]` (sim_repository.py:15–16); a module-level `SingletonSimRepository` singleton carries weather forecasts → building, prices → MPC, PID coefficients | Typed `SimContext` struct (enum-keyed), passed explicitly; singleton becomes a field of the engine | 🟡 design work, not hard |
| pandas seam at the end of the run | Whole-year results accumulate in `all_result_lines: List[List[float]]` (simulator.py:312, 337) → `pd.DataFrame` + `date_range` index → `resample()` to monthly/daily/hourly, split mean-vs-sum per unit via `UNITS_USING_MEAN_AGGREGATION` (simulator.py:492–585) | Own time-bucketing module over `Vec<f64>`, or Arrow/Polars; **DST/bucket-boundary semantics of pandas resample must be replicated** (tz-aware Europe/Berlin indices) | 🟠 the sneakiest part of the core |
| Default-connection machinery | `connect_everything_automatically` (simulator.py:587–660): isinstance checks against `DynamicComponent`, class-name-keyed dicts | Registry keyed by a component-kind enum; resolves at wiring time | 🟡 |
| Registration-time side effects | Output registration mutates `output.global_index`; duplicate-name checks; connection logging appends to a JSON file mid-wiring (component.py:392–447 rewrites the last byte of the file!) | Two-phase build (declare → resolve indices), buffered connection log | 🟢 |
| Decorators/singleton utilities | `utils.measure_execution_time`, `ResultPathProviderSingleton` | Plain functions, `once_cell::sync::Lazy` or explicit config | 🟢 |

### 2.3 Verdict

**Challenge: 🟡 Medium. Effort: M (≈4–8 person-weeks)** including a test harness. The loop itself is a near-mechanical translation; the work is in the typed redesign of the repository/singleton and in reproducing the pandas resample seam. This module should be ported **first** — it is small, well-tested (`tests/test_component.py`, sim tests), and defines the contract everything else compiles against.

---

## 3. Component Model and Config Layer

**Files:** `hisim/component.py` (736), `dynamic_component.py` (627), `loadtypes.py` (399), `units.py` (381), and the `hisim/config/` package (12 files, ~3,900 LOC: `base.py` 422, `sizing.py` 537, `engine.py` 500, `laws.py` 499, `channels.py` 465, `presets.py` 453, `introspection.py` 340, `names.py` 131, …).

### 3.1 Component runtime contract (`component.py`)

The `Component` base class defines: lifecycle hooks (`i_prepare_simulation`, `i_simulate`, `i_save_state`/`i_restore_state`, `i_doublecheck`), `add_input`/`add_output` ports, string-name-based `connect_input`, default connections, and the **cost/KPI hooks** `get_cost_opex`/`get_cost_capex`/`get_component_kpi_entries` — which take a `pd.DataFrame` parameter, so the pandas seam reaches into the component API itself. `MODELS_NO_DEVICE: ClassVar[bool]` gates whether a component may return zero costs.

- **Rust mapping:** a `Component` trait with `simulate(&mut self, &mut TimestepValues, force_convergence: bool)`, `Clone`-based state, and a separate `CostModel` trait whose input is a typed results table (not a DataFrame). The ClassVar flag becomes a trait method or associated const. ~40 components override the cost hooks; the API redesign is felt repo-wide but each change is mechanical.
- Ports matched by string names with identifier validation (`NameSyntax.require_identifier`) → in Rust, names remain the serialization surface, but wiring resolves to typed `(component_idx, port_idx)` handles.

### 3.2 DynamicComponent — runtime-variable ports

`DynamicComponent` (627 LOC) grows one input per participant (the electricity meter, the L2 EMS are subclasses). Ports are matched at runtime by load type, unit, `ComponentType`/`InandOutputType` tags, and `source_weight` (dynamic_component.py:62–88); a `CHANNELS` ClassVar declares accepted declarative feeds. It even injects attributes dynamically: `vars(self)[label] = label` (dynamic_component.py:167).

- **Rust mapping:** a `Vec<ChannelDescriptor>` with tag-matched lookup — *no dynamic attributes needed*; the tag matching is a plain filter. The recently added `DuplicateComponentFeedError` (a double-feed protection) ports directly.
- **Severity: 🟡** — conceptually simple, but the L2 EMS builds on it with `getattr`-based dispatch (see §5), so the typed redesign must be coherent across both.

### 3.3 The config layer — the most "dynamic Python" in the codebase

This is where a Rust port stops being a translation and becomes a **redesign**:

- `ConfigBase` (config/base.py:238) uses `__init_subclass__` to validate builder declarations and to **complete enum codecs by reading class annotations textually** (base.py:48–134) — runtime metaprogramming Rust cannot do.
- `@dataclass_json` injects `to_json`/`from_dict`/`from_json` at runtime; field names are dumped verbatim snake_case (base.py:246–249). The TYPE_CHECKING-only `__getattr__`/`__setattr__` escape hatches (base.py:282–312) exist purely because components store configs in base-typed slots.
- The sizing subsystem: a copy-stable `AUTO` sentinel singleton with custom `__deepcopy__` (sizing.py:60–97), `Sizable[T]` unions, `sized_field` metadata, a `SizingLaw` algebra (laws.py), `@preset`/`@constructor` builders (presets.py), and a cross-component **fact engine** reading `SIZING_CONTRIBUTIONS` ClassVars by name (engine.py). `Component.__init__` hard-rejects configs that still carry AUTO (component.py:272–287).
- `introspection.py` (340 LOC) reads annotations at runtime; `scripts/check_config_attrs.py` exists as an AST-based *external* checker compensating for the `Any`-typed escape hatch.

**Rust redesign sketch:** serde structs with explicit `#[serde(default)]`; `enum Sizable<T> { Auto, Value(T) }` with the wire spelling `"AUTO"` preserved; sizing as an **explicit pre-pass** that takes unresolved configs + a facts context and returns resolved ones (the Python flow is already: resolve before construction — the check at component.py:272 just moves to compile time); presets = plain constructor functions; the fact engine = a typed registry of fact providers. The *wire format* (snake_case JSON) is stable and simple; what disappears is the runtime self-inspection, which Rust replaces with compile-time guarantees. Note the CI quality gate `check_config_attrs.py` becomes unnecessary — the type system does it.

### 3.4 loadtypes and units

69 enum classes (`LoadTypes`, `Units`, `ComponentType`, `InandOutputType`, `BuildingCodes`, …) → direct Rust enums with exact spelling preserved (`str`-Enums are pinned by every `.scenario.json`). `units.py` is a typed `Quantity` system (Watt, KiloWattHour, …) distinct from the I/O `Units` enum → newtypes with `std::ops`. 🟢 trivial.

### 3.5 Verdict

| Sub-area | Challenge | Effort |
|---|---|---|
| `component.py` contract + ports | 🟡 Medium | S–M |
| `dynamic_component.py` | 🟡 Medium | S |
| config layer (`config/`, 3.9k LOC) | 🟠 High — deliberate redesign of sizing/presets | M (weeks) |
| loadtypes/units | 🟢 Low | S |

**Overall: 🟠 High (because the config layer is the type-system backbone every one of ~60 components and every scenario file depends on). Effort: M–L.** Get this right and the rest of the port is repetitive; get it wrong and every later module pays. The existing `tests/test_config_contracts.py` (475 LOC) and `test_component_id.py` pin the wire behavior and translate almost directly to Rust tests.

## 4. Component Library I — Thermal / Building / Weather (~22,900 LOC)

**Scope:** 22 files: `building/` (building.py 2096, information.py 791, window.py, config.py), `weather.py` (1453), heat pumps (`generic_heat_pump.py` 868, `more_advanced_heat_pump_hplib.py` 2754), `generic_boiler.py` (1647 — oil/pellets/wood-chips/H₂ are presets at :199–246, not separate files), `generic_district_heating.py` (1238), `simple_water_storage.py` (2160), `heat_distribution_system.py` (1571), `solar_thermal_system.py` (1087), heating controllers, electric heating, air conditioners, `simple_heat_source.py`, `building_sizer_utils/` (1570).

### 4.1 The headline: the physics is easy, the plumbing is hard

**There is no ODE solver anywhere in this scope.** Every model is scalar f64 arithmetic: the building is closed-form Crank–Nicolson per ISO 13790 (building.py:1694–1732, 1851–1929); the water storage is a 0D fully-mixed tank; heat distribution is a first-order exponential lag (`np.exp(-dt/τ)`, heat_distribution_system.py:544–551); curve fits are degree-1 `np.polyfit` (generic_heat_pump.py:367, air_conditioner.py:458–467). NumPy is used as a calculator. Roughly 25–40% of the big files is KPI/cost/report boilerplate (`get_cost_capex/opex/...`), separable from physics.

### 4.2 Library dependence audit

| Library | Used for | Rust situation | Difficulty |
|---|---|---|---|
| **pvlib** | Solar position (weather.py:893, 1444; solar_thermal:670), extraterrestrial radiation (weather:891), AOI/total irradiance transposition (window.py:79,136; solar_thermal:716), GHI→DNI back-derivation with NaN-fill (solar_thermal:713–721) | No mature Rust solar stack (`spa` crate covers position only); must hand-port solar geometry + transposition (~300–600 LOC of well-documented formulas) | 🟠 **Medium — the numerical-parity linchpin**: small deviations propagate into every solar gain and heating demand |
| **hplib** (==1.9) | `get_parameters` + `HeatPump.simulate` in 4 modes (more_advanced_heat_pump_hplib.py:369–370, 1528); HiSim *mutates* `heatpump.delta_t` across the boundary (:1020, :1049) | No equivalent; port the fit equations (~300–500 LOC) or precompute parameters offline in Python and ship as data | 🟠 Medium (M→L depending on approach) |
| **oemof.thermal** | One function: `calc_eta_c_flate_plate` (solar_thermal:725) — a 10-line formula | Port verbatim | 🟢 |
| **pygfunction** | Only `media.Fluid` for brine cp/ρ (simple_heat_source:407–412) | Correlation table | 🟢 |
| **pandas** | Quirky CSV parsing (cp1252, `sep=";"`, `decimal=","` — information.py:274–280; fixed-width DWD `.dat` — weather.py:1228), tz-aware `resample().mean().interpolate("linear")` (weather:880–931, 1017–1031), KPI Series reductions | `csv`/`encoding_rs` + hand-written resample (~100 LOC) or `polars`; **DST bucket boundaries are the hazard** (`chrono-tz`) | 🟡 Low–Medium |
| **numpy** | polyfit/polyval, `np.exp`, `np.pi`, one `np.transpose` | Closed-form least squares (~20 LOC) or `argmin` | 🟢 |
| **utspclient** | LPG household registry in building_sizer_utils (archetype_config:280–295) | Static name table; external coupling remains | 🟢 |

### 4.3 Python-specific obstacles

- **`importlib` + `getattr` default connections — ~30 sites in 11 files** (building.py:498–540, more_advanced_heat_pump:892–928, ...). Exists solely to dodge circular imports; in Rust a static registry makes the dynamism evaporate. 🟡
- **Optional-attribute duck typing**: `if hasattr(self, "solar_gain_through_windows") is False` (building.py:559, 588) → `Option<Vec<f64>>`. 🟢
- **SingletonSimRepository data handoffs**: weather publishes 11 yearly forecast arrays (weather.py:976–1012) which the building reads at prepare time (building.py:795–806); AC publishes fit coefficients (air_conditioner:467–475) → typed context struct. 🟡
- **Caches keyed by config hash** (class-level cached DataFrame — information.py:263–282; `lru_cache` on a method — window.py:103; sha256 result cache — more_advanced:1523) → `OnceLock`/HashMap; fine.
- **Snapshot/rollback** via `self_copy`/clone → Rust `Clone` + swap. 🟢 (maps *better* than Python)
- Thin inheritance only (`SimpleHotWaterStorage(SimpleWaterStorage)` — use composition). 🟢

### 4.4 Numerics to preserve, not "fix"

NaN semantics (`math.isnan(poa_direct)` → 0, window.py:147; `.fillna(0)`, solar_thermal:721); exact float comparisons in control flow (`water_mass_flow_rate == 0` — heat_distribution:442; `delta_t == 0` → set `1e-8` — more_advanced:1051–1052); `force_convergence` early-returns in controllers. Bit-level drift can change convergence iteration counts → golden-reference validation is mandatory.

### 4.5 Verdict

**Challenge: 🟠 High. Effort: L (≈3–5 person-months) for this scope alone**, excluding the engine it plugs into. Per-file ratings range 🟢 (controllers, idealized heater, meters: S) over 🟡 (boiler, district heating, electric heating, storage, building: M) to 🟠 (weather, hplib heat pump: M–L).

**Top-3 problems:** (1) pvlib parity — port + regression harness against pvlib outputs; (2) hplib port incl. the `delta_t` mutation contract; (3) framework coupling — components lean on `Component`, `SingleTimeStepValues`, default connections, SimRepository, and the pandas-typed cost/KPI interfaces, so **the engine contract (§2–3) must exist first**.

---

## 5. Component Library II — Electrical / Hydrogen / Controls (~22,400 LOC)

**Scope:** 42 files: PV (`generic_pv_system.py` 1279), wind (`generic_windturbine.py` 487), batteries (bslib ×2, generic), `generic_car.py` + `lpg_car_information.py`, the hydrogen chain (electrolyzers ×4, H₂ storage, fuel cells ×3, RSOC ×3, transformer_rectifier), CHP, controllers (L1 ×many, L2 EMS 1181, PTX, XTP, PID 667, **MPC 1469**), meters ×4, loads (csvloader, smart device, price signal, random_numbers), `configuration.py` (1030), examples/templates.

### 5.1 Library dependence audit — where the real blockers live

| Library | Used for | Rust situation | Difficulty |
|---|---|---|---|
| **casadi** | `controller_mpc.py`: multiple-shooting NLP over 24h horizon (`Opti`/MX, `ca.if_else` disjunctions, IPOPT+MUMPS/MA27, max_iter 500 — :729–991); ~57 s per optimization at 1-min sampling per code comment :968 | **No mature Rust NLP ecosystem.** Options: (a) `ipopt` crate bindings (stale), (b) casadi codegen→C+FFI, (c) **recommended: reformulate as MILP** — dynamics/cost are linear between disjunctions → binaries + big-M, solved by HiGHS via `good_lp` | 🔴 **Very High — the single hardest component in the entire codebase**; either path needs revalidation vs the `mpc` tests and accepts changed numerics |
| **pvlib** | `generic_pv_system.py` only: SAM module/inverter databases, SAPM + CEC single-diode (incl. iterative MPP solve), Sandia inverter, Perez transposition, airmass (:842–1258); NaN/night handling under `np.errstate` (:1024, 1070–1078) | Hand-port ~10 functions (~1.5–3k LOC) + vendor SAM CSVs; community `pvlib-rs` is early-stage | 🟠 High, effort M (weeks) — output parity vs pvlib is the goal |
| **bslib** | `ACBatMod(system_id, p_inv, e_bat).simulate(p_load, soc, dt)` (advanced_battery_bslib:194, 283; EV variant :134) | Tiny API but opaque internals + per-system parameter DB; bslib not vendored — port requires obtaining its source | 🟡 Medium, S–M |
| **windpowerlib** | `ModelChain.run_model` (generic_windturbine:188–199): log-law hub extrapolation + density correction + power curve | Hand-port ~300 LOC + sha256 cache (:441) | 🟡 Low–Medium, S |
| **control** | `controller_pid.py`: `TransferFunction` + `forced_response` on a *scalar first-order* system only (:251–255) | Closed-form response `y(t)=K(1−e^{at})` — ~30 LOC, no crate needed | 🟢 surprise finding: trivial |
| **scipy** | `interp1d(kind="quadratic")` ×2 (electrolyzer_h2:557, fuel_cell:447) | Hand-roll quadratic spline (~50 LOC) | 🟢 |
| **numpy** | `np.interp` (6+ sites: part-load curves), linspace/zeros/argmin, `np.where` NaN cleanup, `np.repeat` | `Vec<f64>` + helpers or `ndarray` | 🟢 |
| **pandas** | `read_csv` (csvloader:310, price_signal:272), `read_excel` (advanced_fuel_cell:151 → `calamine`), and the cross-cutting `postprocessing_results: DataFrame` param in cost/KPI functions of 11 files (e.g. electricity_meter:665–745) | `polars`/`csv` crate; **the DataFrame-typed interface must be redesigned** to typed tables | 🟡 Low–Medium, mechanical |
| **utspclient** | EV charge controller: static dataset constants only (:12–13) | Vendor as consts | 🟢 |

### 5.2 Python-specific obstacles

- **L2 EMS dynamic dispatch**: `getattr(self, inputs[i].source_component_class)` (controller_l2_energy_management_system.py:646) and dispatch by class-name-embedded-in-field-name sniffing, admitted fragile in its own docstring (:931–939); inherits `DynamicComponent`; iterative surplus dispatch over source-weight-sorted components (:700–778) whose **iteration order must be preserved** for float-identical accumulation. Needs a typed channel-registry redesign. 🟠
- **SimRepository handoffs**: car profile handoff (`lpg_car_information.py:281–335`, consumed via `getattr(self, "simulation_repository", None)` in generic_car:290); price signal → ~20 MPC forecast keys (price_signal:230–234 → mpc:428–504); PID reads 6 thermal coefficients (:134–142). → typed context. 🟡
- **Checkpointing** via `copy.deepcopy` of state (electrolyzer_h2:394–405, advanced_fuel_cell:271, 415) → `Clone` + swap; maps *perfectly*. 🟢
- `CarDataProvider` Protocol → trait object. 39 configs → serde. 🟢
- `random_numbers.py`: seeded Mersenne Twister (`random.Random`, :132) → `rand_mt` crate for bit-identical series, or accept different draws with `rand`. 🟢

### 5.3 Numerics style

No ODEs (all explicit Euler); interpolation dominates (linear `np.interp` + two quadratic splines); exact float comparisons in control flow (`control_signal == 0.0` — advanced_fuel_cell:438, 447; exact `==` in PID time-constant search :277) — replicate bit-for-bit, don't "improve". MPC is the only iterative solver.

### 5.4 Verdict

| Group | Challenge | Effort |
|---|---|---|
| MPC (casadi/IPOPT) | 🔴 Very High | L |
| PV (pvlib port) | 🟠 High | M |
| L2 EMS dispatch redesign | 🟡 Medium | M |
| Wind, batteries (bslib) | 🟡 Medium | S–M |
| Hydrogen chain (electrolyzers, storage, fuel cells, RSOC ~5.6k LOC) | 🟢–🟡 | M (mechanical, repetitive) |
| L1 controllers, CHP, transformer, meters, loads, examples | 🟢 Low | M combined |

**Overall: 🟠 High. Effort: L (≈4–7 person-months including validation).** Long pole: MPC without casadi. Runner-up: bslib source availability. Favorable: no ODEs; clone-based checkpointing maps 1:1; flat configs → serde.

## 6. Postprocessing (6,513 LOC)

**Files:** 16 files: `postprocessing_main.py` (1345, orchestrator dispatching 27 options flags), `charts.py` (293), `chartbase.py` (268), `chart_singleday.py` (221), `system_chart.py` (329, Graphviz wiring diagrams), `reportgenerator.py` (365, PDF), `kpi_computation/kpi_preparation.py` (1920, all KPI math), `kpi_structure.py` (155), `cost_and_emission_computation/` (~826).

**Corrections to the dependency folklore:** `pyam` is **never imported** — `postprocessing_main.py:889–1110` hand-rolls the IAMC long format (model/scenario/region/variable/unit/year/value) from plain dicts, with fragile positional column-name parsing (:1074–1085). `html2image`, `seaborn`, `plotly`, `openpyxl` are not used in this module. Actual deps: matplotlib (Agg, dpi=600, 4 chart forms incl. carpet `pcolormesh`), reportlab (Platypus + auto-TOC via `multiBuild` two-pass, reportgenerator.py:83–91, 360–365), pydot (shells out to the Graphviz binary, failures swallowed — system_chart.py:159–165), pandas, `dataclasses_json` (camelCase KPI entries, kpi_structure.py:60–93), **pickle** (DataFrame export, postprocessing_main.py:542–575).

### 6.1 The architectural blocker: it is not self-contained

KPI collection calls `component.component_kpi_entries(all_outputs, results)` (kpi_preparation.py:1848); OPEX/CAPEX call `get_cost_opex`/`capital_cost_data` with **dynamic dispatch into ~50 component classes**, plus `isinstance` classification against concrete heat-pump/meter/EMS classes (opex_and_capex_cost_calculation.py:103–110). There is even a file-based feedback loop: KPI prep re-reads the cost CSVs that the OPEX step wrote earlier *in the same run*, keyed by magic row names `"Total"` etc. (kpi_preparation.py:732–776). **Postprocessing cannot be ported before the component trait (incl. its cost/KPI interface) exists.**

### 6.2 Parity traps

- **~100 magic-string KPI keys** matched by string equality across files (kpi_preparation.py:694–717); the building-sizer lookup matches whole sentences (postprocessing_main.py:1174+). Rust: extract to constants — mechanical, and turns silent KeyError into compile errors.
- **Banker's rounding** on every KPI (`round(x, 1)`, kpi_preparation.py:160–202) vs Rust's half-away `f64::round` → implement Python-style rounding or goldens diff.
- The self-consumption computation uses a delicate `pd.concat` + `groupby(level=0).sum()` **element-wise-min trick** (kpi_preparation.py:321–332) → rewrite as a plain zip loop (simpler in Rust, but parity must be proven).
- Fixed 30-day months in single-day slicing (chart_singleday.py:120–121), regex camelCase title splitting feeding filenames (chartbase.py:159–181) — replicate verbatim for artifact-name parity.
- matplotlib global state machine + explicit `gc.collect(2)` to survive hundreds of plots (charts.py:163–192) — becomes owned figure structs in Rust (better design, no port).

### 6.3 Library equivalents

pandas: only a small surface (iloc selection, clip, sum(axis=1), concat/groupby, resample, to_csv/read_csv) → polars or hand-rolled reductions; **CSV float formatting parity** matters for golden tests. matplotlib → `plotters` (no native heatmap/colorbar) or `plotly.rs`+kaleido (external binary); **pixel parity is unattainable** either way. reportlab → `printpdf`/`genpdf` (TOC needs manual two-pass) or typst subprocess. pydot → `graphviz` crate (same external binary as today). pickle → **no Rust equivalent; must become Parquet/CSV — a breaking change for downstream consumers** (decision, not port).

### 6.4 Verdict

- **Full-fidelity port (charts + PDF): 🟠 High, effort L (~3–5 person-months)** — much of it low-value visual re-implementation.
- **Compute core (KPI + OPEX/CAPEX + CSV/JSON exports): 🟡 Medium, effort M (~4–8 person-weeks)**, *provided* the component cost/KPI interface is frozen or ported first.
- **Recommendation: split it.** The codebase itself proves visualization is optional — Docker mode (postprocessing_main.py:160–177) disables every chart and the PDF, keeping only CSV/KPI/costs/JSON. A Rust core should emit CSV/Parquet + JSON artifacts (KPIs, IAMC-style, building-sizer, webtool) and leave visualization to external tooling; the PDF report is better generated from a JSON manifest (e.g. typst) than reimplemented.

---

## 7. Economics / Lifecycle-Cost Engine (21,623 LOC)

**Files:** `hisim/economics/` (51 files) + `cost_database/` (20 JSON + spot-price CSVs) + `subsidy_catalog/` (5 JSON) + `tools/worked_examples/` (xlsx→YAML converter). The newest large module.

### 7.1 Architecture — and why it maps so well

A **self-contained post-simulation cost engine**: it prices a finished simulation's outputs into NPV/annuity lifecycle costs per perspective. Layering: kernel/value types (~3,100 LOC: `UncertainValue` band arithmetic, `CashFlowEntry` timeline, financing, provenance), data layer (~4,700: cost DB + overlays, tariffs, subsidy catalog with a tri-state condition **AST evaluated by an op table, no `eval`** — catalog.py:51, 79), engine (~3,600: `evaluator.py` 968 + 15 pure calculators), results/IO (~5,400 incl. `economic_inputs.json` round-trip — the seam contract), presentation (~3,150: self-contained HTML+inline-SVG report, markdown golden, matplotlib PNGs), CLI (649).

**Crucial scope fact: it is a leaf.** Nothing in `hisim/` imports it (all 21 importers are tests/tools); its only core dependencies are two enum modules. **Library dependence is almost nil**: pure scalar f64 math — no numpy/pandas/numpy_financial at runtime (pandas in exactly one audit function; matplotlib only for PNGs). All JSON I/O is hand-rolled (31 `json.load/dump` sites) — no dataclasses_json/pyhumps magic to reverse-engineer.

### 7.2 What a Rust port looks like

Natural crate boundaries already exist (the spec's four "seams"); the evaluator is documented as a **pure function of a JSON file**. `Protocol` → trait; tagged benefit/condition unions → serde internally-tagged enums (the JSON already carries tags); `UncertainValue` → small linear algebra over `[f64; 3]`; 2ⁿ subsidy-cumulation solver is bounded and fine. Data-driven throughout — new countries/schemes need no code changes.

### 7.3 The traps (why it's Medium, not Low)

1. **Byte-exact report goldens** (`tests/goldens/cost_summary.md`, `lifecycle_report.html`, 237 KB incl. SVG coordinates; only today's date is normalized): every view/SVG computation must reproduce f64 op order, plus Python-identical float formatting (`:.1f`, SVG `:.4f`, magnitude-based `_fmt` in reporting/summary.py:40) and thousands separators. Attainable (both languages round-half-even on the same binary double) but unforgiving.
2. **Silent numeric conventions**: Python banker's `round()` at **8 integer-year decision sites** (calculators/investment.py:185, 190; context_resolution.py:294, 313, 324, 444) — half-even vs half-away *changes replacement/credit years*, shifting the whole timeline; and the relative **1e-9 band snap** in `UncertainValue` (uncertainty.py:95–104). Both must be replicated exactly.
3. **Insertion-ordered dicts** everywhere in pivots/exports → `IndexMap`/ordered structs or golden ordering drifts.
4. **Worked-example Excel tooling**: openpyxl formula tokenizer + SHA-256 attestations has no drop-in Rust equivalent (`calamine` can't read formula strings). Recommendation: keep the converter in Python — its YAML output is the stable contract.

### 7.4 Verdict

**Challenge: 🟡 Medium (low end). Effort: L (~2 person-months full port incl. tests; ~1 person-month for engine+data+CLI without presentation).** The unusually strong test suite (376 tests: hand-computed worked examples at 0.01 tolerance, invariants at 1e-9, an independent `numpy_financial` cross-check, goldens) doubles directly as the Rust port's acceptance harness. Recommended cut line: kernel → data → engine → serialization/CLI first; presentation goldens second pass; xlsx converter stays Python. **If a Rust pilot is wanted, this module — or a slice of it — is the place to start.**

## 8. Declarative Energy-System Layer and Setup Front Ends — NOT YET ANALYZED

**Scope that belongs here:** `hisim/energy_system/` (55 files, 19,406 LOC — loader, schema, codec,
executor, wiring, channel matching, feed resolution, sizing bridge, grouping, recording, audit,
parity), `hisim/json_executor.py` (334), `hisim/hisim_main.py` + `hisim_convert_to_json.py`,
`system_setups/` (25 files, 8,267 LOC) and the `energy_systems/*.yaml` corpus.

This section is missing, and its absence is the largest hole in the document — see review item
**R1** in §11. Every effort total below is therefore incomplete by the second-largest module in the
repository. Nothing here should be read as "this layer is easy"; it is the layer where
reflection-based instantiation, preset resolution, tag-matched wiring and the byte-identity
freshness gates all live.

---

## 9. Input Data, Caching, and External Connectors (~5,800 LOC + 569 MB data)

**Files:** `hisim/caching/` (5 files, 1,309), `hisim/utils.py` (675), `log.py` (205), `result_path_provider.py` (357), `sim_repository_singleton.py` (236), `loadprofilegenerator_utsp_connector.py` (2,364) + `pylpg_workspace.py` (561), `hisim/inputs/` (206 data files, 569 MB), `hisim/renovisor/` (8 files, 1,636).

### 9.1 Caching — surprisingly Rust-friendly, one byte-parity trap

The legacy scheme (the one every component actually uses; the new `caching/keys.py` scheme has no consumers yet) computes `sha256(config.cache_key_view().to_json() + sim_params.get_unique_key())` (utils.py:385–413): a deep-copied config with `component_id.building` cleared and per-config non-key fields stripped (config/base.py:333), plus start/end/resolution/year/country joined with `###`. **Cache entries are plain CSV (pandas `to_csv`), not pickle** — reads use `float_precision="round_trip"` because pandas' default parser loses bits (weather.py:834–839). Atomicity via `mkstemp` + `os.replace` with a `.meta` sha256 sidecar (caching/local.py:160–232). The remote HTTP/shared-directory tiers are parsed-but-**not-implemented** — greenfield for Rust.

Rust: `sha2` + `serde_json` + `tempfile`/`fs2` — a clean S-sized port. The trap: **cache keys are hashes of `dataclasses_json` output**, so serde must reproduce that string byte-for-byte (field order, float repr, enum spelling) or the 250 MB of committed caches and every cross-machine share invalidate at once. The new scheme's AST import-closure fingerprinting is inherently Python-runtime-specific and must be redesigned as build-time hashing.

### 9.2 Input data — parsing is easy, the computations behind it are not

Runtime-parsed formats: DWD TRY fixed-width headers (`lat = lines[1][20:37]` — weather.py:1178–1183) + `skiprows=31` tables; NSRDB hourly/15-min CSVs; wetterdienst CSVs; LPG minute profiles (`sep=";"`, **cp1252 from UTSP vs utf-8 from disk**); TABULA housing (latin-1, `;`); CEC/Sandia PV CSVs (multi-level headers, read **at import time** — module_selection.py:116); battery `.xlsx`/`.npy`/`.npz`. Rust: `csv`/polars, `calamine`, `ndarray-npy`, `encoding_rs`, manual byte-slicing for fixed-width. Memory improves 10–20× (`Vec<f64>` vs Python float lists). The hard 80% is what happens *after* parsing: `resample("1min").interpolate` + **pvlib** solar position/irradiance (weather.py:862–915) — no Rust equivalent (see §4.2/§5.1).

### 9.3 UTSP/LPG connector — trivial protocol, heavy ecosystem

The wire protocol is plain HTTP POST + JSON with an API-key header (verified from the utspclient wheel; no protobuf); results arrive as base64-encoded, zlib-compressed files; the client polls by re-POSTing every 100 s (connector:1714–1716). reqwest + serde + base64 + flate2 covers it in a day. The real work: (a) re-declaring the generated .NET LPG JSON bindings (hundreds of catalogue classes) with byte-identical serialization — configs feed cache keys; (b) the **local mode depends on a closed-source .NET binary** (simengine2 + 51 MB sqlite) that pylpg shells out to; HiSim already contains a workaround layer (workspace claiming, fcntl flock serialization, locked-db retries, ETXTBSY-safe install — pylpg_workspace.py:187–283, 356–402) that a Rust port must faithfully re-implement against the same binary via `std::process::Command`. **The Python library is not the forever-dependency — the .NET binary is.** Note: `pytz`'s private `_utc_transition_times` is used for LPG→UTC conversion (utils.py:258, 296) — DST logic must be re-derived carefully with `chrono-tz`.

### 9.4 RenoVisor — confirmed easy

Manual schema validation, pure dict transforms, one cached CSV read, a REST uploader with a clean retry policy, argparse CLI with exit codes. Injectable `post_fn`/`sleep_fn`/runner-Protocol seams map directly to Rust traits — no monkeypatching. Textbook reqwest + serde + clap + anyhow port. One caveat: `runner.py` executes `hisim_main.main` in-process, so the "run a simulation" stage inherits the full simulator's difficulty. **🟢 Low, S.**

### 9.5 Verdict

| Sub-area | Challenge | Effort |
|---|---|---|
| `hisim/caching/` package | 🟢 Low | S (<1 pw) |
| Cache-key byte parity (consumers) | 🟠 High | folded into the above port |
| Input-data reading | 🟡 Medium | M (weeks); pvlib parity is the L–XL tail |
| UTSP/LPG connector | 🟠 High | L (person-months) |
| RenoVisor | 🟢 Low | S |
| Supporting cast (utils/log/rpp/singleton) | 🟢 Low | S |

**Top-3 problems:** cache-key byte compatibility; the LPG/.NET ecosystem; pvlib/pandas parity in the weather→PV pipeline. Overall this layer is unusually Rust-friendly for Python infrastructure — no pickle in the cache path, no monkeypatching, explicit locking, env-var config — the risks are ecosystem risks, not code-structure risks.

---

## 10. Test Suite and Tooling (55,322 LOC tests + ~13,000 LOC scripts/tools)

### 10.1 Taxonomy

190 test files, ~1,322 test functions. **153 files (~87% of functions) are `mark.base`** — the fast, network-free set. Remainder: `system_setups` (14 files), `buildingtest` (7), `extendedbase` (10), `utsp` (2, networked + credentials), `jsonconfig`, `postprocessingoptions` (28 tests), `hpcharness` (13 files, ~89 tests), `worked_examples`. By content: component unit tests ~20,000 LOC, economics ~8,430, declarative energy-system ~9,000, core ~5,200, config contracts ~2,500, e2e smoke tests ~2,250 (assert only `finished.flag` exists), caching ~1,830, HPC harness ~1,720, postprocessing ~1,810, golden/parity machinery ~3,330, CLI ~570.

### 10.2 Five golden/regression mechanisms — and what survives a port

1. **KPI goldens** (`golden_references/*.json`, 32 files): 22 setups × 2 param sets re-run via subprocess, `all_kpis.json` flattened, compared with `math.isclose` at **rel_tol=1e-9** (`scripts/golden_kpis.py:19–20`). Subprocess-driven and artifact-comparing → **survives a port intact and can drive a Rust binary as happily as `hisim_main.py`.**
2. **Building snapshots**: whole TABULA catalogue (2.1 MB) + every output vector of a synthetic day, floats via Python `repr` (bit-exact round-trip), NaN/inf as string sentinels; missing golden = hard error. → needs float-formatting parity or re-baselining.
3. **Economics report goldens**: byte-compared markdown/HTML (only the date normalized). → hardest parity case (see §7.3).
4. **P3 parity rig** (`scripts/p3_parity_*.py`, `hisim/energy_system/parity.py`): compares a Python setup vs its declarative twin — wiring sets, then **every column of `all_results.csv` with exact equality by default** (tolerance = "a finding"). → **this is precisely the template for a Python-vs-Rust parity rig.**
5. **Freshness gates**: two *blocking* byte-identity gates regenerating every `.scenario.json` / `.energy_system.yaml`. → Python-artifact generators; need a Rust recorder with byte-identical output or semantic comparison.

CSV byte-comparison also pins **pandas' shortest-round-trip float formatting** (postprocessing_main.py:520) — the sane move is switching all comparisons to parsed-value numeric comparison.

### 10.3 pytest features with no Rust equivalent — and what they force

- **monkeypatching (21 files)**: patches `utils.get_cache_file` (solar-gain cache isolation — without it, runs are order-dependent via the shared disk cache), `subprocess.run`, env vars. Rust has no monkeypatching → forces **dependency-injection seams in the core** (cache backend trait, injected clock, env at construction). This is a core-architecture change billed to the test scope.
- **Autouse fixtures**: per-test `HISIM_CACHE_*` scrubbing and a `git status --porcelain` stray-file guard around *every* test (conftest.py:29, 181) → per-test helpers or (better) a CI-level post-suite check.
- **Singleton resets** (`SingletonSimRepository` fixtures), **`__dict__`-scanning** output-index assignment in the test helper (functions_for_testing.py) → both evaporate with the typed core redesign (§2–3).
- Mechanics: `parametrize`→`rstest`, `tmp_path`→`tempfile`, `capsys`→`assert_cmd`, `pytest.approx` (beware the hidden default rel_tol=1e-6 that many tests lean on)→`approx` crate, `raises`→`#[should_panic]`, `hypothesis` (1 file)→`proptest`, **markers as test selection**→test binaries/features + `cargo-nextest` filters, xdist → cargo parallelism.

### 10.4 CI and HPC harness

14 workflows. The golden family (check/year/json/update) survives as a pattern (subprocess + artifacts). `quality.yml`'s mypy/pylint/AST-config-check map to clippy/rustfmt — and `scripts/check_config_attrs.py` (which exists to compensate for untyped config escape hatches) becomes free. The **HPC harness** (~6,300 LOC FastAPI queue server + SQLite + worker fleet + ~89 tests) ports route-for-route to axum/tokio/rusqlite at M effort — and its warm-pool fork-server layer (~700 LOC), which exists solely to amortize Python interpreter startup, **can be deleted outright** in Rust (`tokio::process` spawn is ~ms).

### 10.5 Verdict

**Challenge: 🟠 High. Effort: L→XL boundary (≈0.5–0.75 person-years)** — parity rig + golden tooling 4–6 pw; base-suite rewrite (~1,150 functions, mostly mechanical once the Rust API exists) 10–14 pw; slow/e2e + freshness gates 4–6 pw; HPC harness 3–4 pw; CI 2–3 pw. Of the 55k test LOC, **~25–30% is behavior-pinning** (the rest is scaffolding that must be rewritten against the new API regardless). The most portable safety nets: the worked examples (oracle is *Excel*, an independent implementation) and the subprocess-driven golden corpus. **The trap:** during any incremental port the Python goldens stop guarding the Rust code, and re-blessing them against Rust output removes protection for Python — hence the parity rig must come first.

<!-- APPEND -->




## 11. Critical Review of This Document

**Review date:** 2026-09-09 · **Reviewed at:** `f91c3eb1` (the analysis was written at `67ba1ca9`)
**Note for the author's workflow:** new module analyses should be inserted *above* the `<!-- APPEND -->`
marker; this review is deliberately last so it can be re-run against a grown document.

The per-module method worked and the document's specificity is its main strength — file-and-line
citations, the "corrections to folklore" pass, and the surprise findings (`control` collapsing to a
30-line closed form; `hisim/economics/` being a leaf with no numpy/pandas at runtime) are the kind of
thing only a real read produces. Spot-checks against the current tree confirmed the load-bearing
structural claims: the convergence test really is a hardcoded absolute `0.0001` over every output
(`hisim/component.py:194`), `pyam` really is never imported anywhere under `hisim/`, `casadi` really
is confined to `hisim/components/controller_mpc.py`, `pickle` really is on the result-export path
(`postprocessing_main.py:50,493,621`), and `pvlib` really is used by exactly four component modules
(the other four hits are a comment, a docstring and two warning-filter mentions).

What follows is what the document still needs before it can carry a decision. Items are ordered by
how much they change the answer, not by page order.

### R1 — §8 is missing, and it is the module that matters most for a port

The heading sequence jumps 7 → 9. The gap is `hisim/energy_system/` (55 files, 19,406 LOC), plus
`json_executor.py`, `hisim_main.py` and `system_setups/` (25 files, 8,267 LOC) — together the
second-largest body of code in the repository and, by the document's own §1.3 reasoning, the most
Rust-hostile: reflection-based instantiation, preset and sizing resolution, tag-matched wiring
(`channel_matching.py`, `feed_resolution.py`, `wiring.py`), the recording/grouping passes, the audit
trail, and the two *blocking byte-identity* freshness gates. A stub has been inserted at §8 so the
hole is visible in the structure. Until it is filled, every total in this document is incomplete by
roughly 15% of the repository's Python and by an unknown share of its difficulty.

Also uncovered, in descending order of importance: the **webtool JSON export contract**
(`MAKE_RESULT_JSON_FOR_WEBTOOL` — an external consumer whose format is a public interface),
`hisim/building_sizer_utils/` (listed in §1.1, analyzed nowhere), Docker packaging and the PyPI
distribution story, and the CI-only Python under `scripts/` and `.github/` — the last of which
should be explicitly declared *out of scope* rather than silently folded into §10's 13,000 LOC,
since a Rust core does not require porting its own CI tooling.

### R2 — There is no total, and §1's promised synthesis does not exist

The method paragraph promises "a cross-module synthesis is added at the end"; there is none. Adding
the per-module verdicts as stated gives roughly **20–30 person-months**, and that figure *excludes*
§8, excludes the physics-library reimplementation tails the document itself calls multi-person-year
(§1.3), and excludes any hardening, documentation or migration period. Stated honestly with those
exclusions restored, this is a **3–5 person-year program with a wide band** — a categorically
different conversation from what a column of "M" and "L" letters conveys to a reader skimming
verdicts. The document should say the number out loud, with its uncertainty and its exclusions
listed underneath, and should lead with it.

### R3 — The benefit side is never quantified, so there is no case to weigh

The entire performance argument is one sentence: a "plausible 10–50× speedup" on the timestep loop
(§2.1), with no measurement behind it. Nothing in the document establishes **where a representative
run actually spends its wall time**. That profile is a day of work and it gates everything else: if a
year-long minutely run spends most of its time in pvlib/hplib calls, CSV/weather parsing, pandas
resampling and waiting on UTSP, then a Rust core delivers a small fraction of that 10–50× and the
program's justification collapses regardless of feasibility. Required addition: a profile of two or
three representative setups broken down into timestep loop / library calls / input parsing /
postprocessing / external waiting, with the resulting Amdahl ceiling computed explicitly. Also state
the actual goal — is it speed, memory, deployability, type safety, or maintainability? Different
goals pick different subsets of this work, and the document never commits to one.

### R4 — The alternatives are not evaluated, so "convert to Rust" has nothing to be compared against

A feasibility study offering only *do it* or *don't* is missing the options most likely to win:

- **PyO3/maturin extension for the hot loop only**, keeping the Python ecosystem intact — the
  document itself identifies the loop as small, well-tested and the most separable piece (§2).
- **numba or Cython on the same loop**, at a fraction of the cost and with no FFI boundary.
- **Vectorizing over timesteps in NumPy** — there is already a plan on the shelf
  (`roadmap/pv_vectorization_plan.md`) that should be cross-referenced.
- **Doing only the architecture cleanups** (typed context, no reflection, typed result tables), which
  captures most of the design benefit in Python and is a prerequisite for the Rust path anyway.

Each deserves a paragraph with rough effort and rough payoff. Without them the document reads as an
answer to "how would we do this" when the open question is "should we".

### R5 — The hybrid path is mentioned twice and designed nowhere

§1.3 floats "keeping a Python island via PyO3" and §10.5 names the killer trap — during an
incremental port the Python goldens stop guarding the Rust code, and re-blessing them against Rust
output removes protection for Python. That is the most decision-relevant problem in the document and
it gets one sentence. It needs a section: which direction the FFI boundary points (a Rust engine
calling Python physics, versus a Python driver calling a Rust engine), the **per-timestep FFI cost
for a component that must call pvlib** — at 525,600 timesteps a year, a boundary crossing per
component per iteration can erase the entire speedup — whether both implementations can live in one
CI, and what the abort criteria are. A rewrite without a designed strangler path is a rewrite that
must complete or be thrown away, and that should be stated as a risk.

### R6 — The parity target is self-contradictory, and several ratings depend on resolving it

The document asserts bit-exactness as a requirement in at least a dozen places ("replicate
bit-for-bit, don't improve" — §5.3, §4.4) while simultaneously conceding in §1.3 that bit-level
reproduction "is *not guaranteed* even for identical formulas". Both cannot stand. Pick a policy —
for example: re-baseline every golden once against declared tolerances (relative 1e-6 for physics,
1e-9 for financial invariants), and keep byte comparison only where byte identity is itself the point
(the freshness gates) — then **re-rate the modules whose colour comes from parity fear rather than
from complexity**: the §6 chart/PDF work, the §7 report goldens, and the §9 cache-key hash. At least
two of those plausibly drop a severity level, which materially changes R2's total.

Related and missing entirely: **the "port the bugs" question.** Bit-parity as a goal means faithfully
reproducing things this document itself flags as defects — fixed 30-day months
(`chart_singleday.py:120`), exact float `==` in control flow, banker's rounding of *integer decision
years* (§7.3), `delta_t == 0 → 1e-8`. Each needs a per-item ruling: reproduce, or fix and re-bless.
Any "fix" answer forces the re-baseline of R6 anyway, which is an argument for deciding the policy
first.

### R7 — The estimates have no stated basis and no calibration

"≈3–5 person-months" for 22,900 LOC implies a translation rate that is never given, never validated
against a measured pilot, and never risk-adjusted. Recommendation: add an explicit LOC/week
assumption per severity class so the arithmetic is auditable, and then **run one calibration pilot**
before anyone believes the totals. §7.4 nominates the economics module; that is the wrong pilot for
this purpose precisely because it is a leaf with no library dependence and no framework coupling — it
would measure the easiest case. The informative pilot is a *component plus the engine contract it
needs* (`simple_water_storage.py` or `generic_boiler.py` on top of a minimal §2/§3 core), because it
exercises the trait design, the config layer, the state checkpoint and the golden harness at once.
Until a pilot exists, mark every number as ±2×.

### R8 — Team, contribution model and distribution are absent

Nothing in the document addresses who would write and maintain the Rust. HiSim's contributors are
energy-systems researchers whose most common contribution is *adding a component* — often a PhD
student with a physics model and one semester. A Rust core changes the cost of that contribution and
the size of the contributor pool, which is a larger programme risk than casadi. The document should
state the required Rust headcount and ramp-up, what happens to external contributions, and what
`pip install hisim` becomes (maturin wheels per platform, or a Python front end over a Rust core).

### R9 — The moving-target risk is named but not sized

§1.3 makes the point with one anecdote ("the last merge alone changed 859 files"). Size it properly:
commits per month touching `hisim/components/` and `hisim/economics/` over the last six months, and
what fraction would have required a parallel change on the Rust side. A multi-year rewrite against
that rate either freezes the Python side — organizationally impossible for a research group with
funded deliverables — or doubles the cost of every change for the duration. This is the factor most
likely to actually decide the answer, and it deserves numbers rather than an aside.

### R10 — Stale and unsourced specifics

The structural claims held up; several numbers have drifted or need sourcing.

- **Line counts have moved** in three commits: `simulator.py` is 783 (doc: 660), `component.py` 804
  (736), `hisim/components/` 47,057 (46,142), `tests/` 60,834 in 220 files (55,322 in 208). Either
  stamp each number with its commit or drop to orders of magnitude — precise LOC in a long-lived
  planning document is a maintenance liability. The `hisim/` top-level row of §1.1 is already
  approximate ("~15 files, ~7,700") where the rest is exact; make the whole table one or the other.
- **`hisim/inputs/` is stated as 569 MB.** Git-tracked content is ~319 MB; this working copy holds
  1.7 GB because generated caches live *inside* the data directory. Distinguish tracked reference
  data from generated cache — the conflation matters for the §9 caching analysis and for the
  clone-size argument.
- **`module_selection.py:116`** (§9.2, "read at import time") is worth an explicit path: it is
  `hisim/inputs/photovoltaic/module_selection.py` — executable Python living inside the *data*
  directory. A reader will not find it under `hisim/components/`.
- **Three load-bearing numbers carry no evidence**: the 10–50× speedup (§2.1), "pvlib alone is a
  multi-person-year effort" (§1.3), and the ~57 s per MPC optimization (§5.1 — that one at least
  cites a code comment, which should be said in the text). The pvlib claim is cheap to ground: state
  pvlib's own LOC and the subset HiSim actually calls, since §4.2 and §5.1 already scope that subset
  to roughly 2–4k LOC of formulas, which is *not* multi-person-year and quietly contradicts §1.3.
- §6's header says 6,513 LOC where §1.1's table says 6,472 (current: 6,551). Minor, but the document
  should not disagree with itself.
- The `seaborn`/`plotly`/`html2image` correction in §6 is right about the module but should note that
  all three are still declared in `requirements.txt` — the folklore has a source, and the dependency
  list is itself a cleanup target.

### R11 — The rating scheme hides the axis that matters

🟢–🔴 conflates "no Rust equivalent exists" with "needs a redesign decision", and a module can be
trivially portable yet blocked on a human choice. Add two columns to every verdict table: **risk**
(probability the estimate is wrong by more than 2×) and, more importantly, **decision** — the choices
this work would force on someone. Collected, those decisions are the actual output a roadmap document
should produce: the MPC formulation, pickle → parquet, charts in or out of core scope, the parity
policy, the FFI direction, whether the LPG/.NET subprocess may remain a core dependency. Right now
they are scattered through nine sections as asides inside effort estimates.

### R12 — Presentation

Add a one-page executive summary at the top: verdict, total with band, top five blockers,
recommendation, and the decision this document feeds. Nobody reads 350 lines to find the answer, and
the answer is currently nowhere — the document ends mid-analysis. Date each module section
individually (they will rot at different rates, and §7's economics analysis is already the freshest).
State who the document is for. And keep the two habits that make it good: the folklore-correction
pass and the per-site line citations.

