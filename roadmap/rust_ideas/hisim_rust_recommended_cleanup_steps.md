# Cleanups and Architecture Work to Do Before Any Rust Conversion

**Date:** 2026-09-09 · **Owner:** Noah Pflugradt · **Surveyed at:** `f91c3eb1`
**Companion to:** `roadmap/rust_ideas/feasibility_rust_hisim.md` (the feasibility analysis and its §11 review)

## What this list is, and the rule it follows

Every item below is worth doing to HiSim as a Python codebase. That is the entrance criterion, not a
coincidence: a cleanup justified only by a hypothetical rewrite is a cleanup that will be abandoned
the first time the rewrite slips, and it will have cost real review time in the meantime. Each item
therefore states its payoff *now* separately from its payoff *for a port* — and if the port never
happens, nothing here was wasted.

The second reason for the rule is that this list is also the cheapest available test of the
feasibility question. Most of the conversion cost the companion document identifies is not
translation cost; it is the cost of untangling Python-specific coupling — reflection, shared mutable
globals, DataFrame-typed interfaces, string-keyed lookups. Doing that untangling in Python is
strictly less risky than doing it while simultaneously changing language, and afterwards the
feasibility estimate can be re-derived against a codebase whose seams are visible. If the cleanups
turn out to be too expensive to finish, that is itself the answer to the Rust question.

Nothing here is sequenced against a conversion start date. This is long-term work, to be picked up
between features.

---

## Step 0 — Measure before changing anything

- [ ] **Profile a representative run and publish the breakdown.** Two or three setups (one minutely
  full year, one 15-min short run, one with MPC), broken into: timestep loop, third-party library
  calls (pvlib/hplib/bslib/windpowerlib), input parsing, postprocessing, external waiting
  (UTSP/LPG). This is a day of work and it is the gate on every performance argument in the
  companion document — including whether Tier 1 should be reordered around whatever actually
  dominates. **Payoff now:** it will almost certainly find optimizable Python nobody has looked at.
  **Payoff for a port:** it converts "10–50× on the loop" into an Amdahl ceiling for the whole run.

---

## Tier 1 — The four boundaries that decide whether a second implementation is possible at all

These are the items that a port cannot route around, and each one improves the Python codebase on its
own terms. If only one tier is ever done, it should be this one.

- [ ] **A physics-library adapter layer.** Today `pvlib`, `hplib`, `bslib`, `windpowerlib`,
  `oemof.thermal` and `pygfunction` are called inline from the components that need them —
  `weather.py`, `generic_pv_system.py`, `building/window.py`, `solar_thermal_system.py`,
  `more_advanced_heat_pump_hplib.py`, `simple_heat_source.py` — and library objects cross the
  boundary in both directions (HiSim mutates `heatpump.delta_t` on an hplib object mid-simulation).
  Give each library one HiSim-owned module with typed scalar/array inputs and outputs and no library
  type in its signatures. **Payoff now:** upgrading or pinning pvlib stops being a repo-wide risk
  (the pin in `requirements.txt` is commented out, which is its own finding); the calls become
  mockable, so component tests stop needing real solar-position math; and one place can record
  reference inputs/outputs per library call. **Payoff for a port:** this boundary *is* the FFI seam,
  and it is the difference between a hybrid being designable and not.

- [ ] **A typed simulation context, replacing `SingletonSimRepository` and the string-keyed
  `SimRepository`.** 17 modules under `hisim/` still reach for the singleton, including the
  weather → building forecast handoff (11 yearly arrays), price signal → MPC (~20 forecast keys), the
  air-conditioner fit coefficients and the PID thermal coefficients; `generic_car.py:293` reads its
  repository through `getattr(self, "simulation_repository", None)`. Replace with an explicit typed
  context constructed by the engine and passed to components. **Payoff now:** mypy sees these
  handoffs, a missing publisher fails at wiring time instead of as a `KeyError` in timestep 0, test
  fixtures that reset a global disappear, and run isolation stops depending on discipline.
  **Payoff for a port:** removes the single largest piece of untyped shared mutable state.

- [ ] **A typed results table, replacing `pd.DataFrame` in the component cost/KPI hooks.** 92
  signatures under `hisim/components/` take `postprocessing_results: pd.DataFrame`, so pandas is
  part of the component API and every cost/KPI implementation depends on column-name conventions.
  Introduce a narrow view type (per-output series accessor plus the reductions actually used) and
  migrate the hooks behind it. **Payoff now:** KPI and cost code becomes unit-testable without
  constructing a DataFrame; the column-name coupling that §6.2 of the companion document describes as
  "positional column-name parsing" becomes a compile-time-visible interface. **Payoff for a port:**
  removes pandas from the component trait, which is otherwise a hard blocker for porting any
  component before porting all of postprocessing.

- [ ] **A static component registry, replacing reflection-based instantiation.** 29 `importlib`
  call sites across 9 component modules exist purely to dodge circular imports in default
  connections; 22 `.scenario.json` files and 28 `.energy_system.yaml` files name components by
  fully-qualified Python path (`class: hisim.components.weather.Weather`). Add a name → constructor
  table with a test that pins its contents, keep the fully-qualified paths accepted as aliases, and
  let the declarative loaders resolve through the table. **Payoff now:** a typo in a scenario file
  fails at load with a suggestion instead of an `ImportError` from deep inside `importlib`; the
  circular-import dodges can be deleted; the component inventory becomes enumerable for docs, the
  webtool and `hisim energy-system describe`. **Payoff for a port:** this is the inventory Rust
  cannot generate for itself, and building it in Python proves the list is complete.

---

## Tier 2 — Interfaces a second implementation would have to honour

- [ ] **Decide the numeric-parity policy, then make the goldens implement it.** Today the regression
  corpus mixes byte comparison of CSV and markdown with `math.isclose` at rel_tol 1e-9 and
  `pytest.approx` at its implicit 1e-6 default. Byte comparison pins pandas' float formatting, which
  is why pandas is pinned to an exact version. Move comparisons to parsed-value numeric comparison at
  declared per-domain tolerances, and keep byte identity only where byte identity is the point (the
  `.scenario.json` / `.energy_system.yaml` freshness gates). **Payoff now:** goldens stop breaking on
  formatting changes in a dependency, and every tolerance becomes a stated decision rather than a
  library default nobody chose. **Payoff for a port:** this is the acceptance harness; see review item
  R6 in the companion document for why the current contradiction blocks the estimate itself.

- [ ] **Retire the magic-string KPI keys.** The companion document counts ~100 KPI names matched by
  string equality across files, with a building-sizer lookup matching whole sentences. Work is
  already specified in `roadmap/kpi_address_spec.md` — align with it rather than forking a second
  scheme. **Payoff now:** a renamed KPI fails loudly instead of silently zeroing a downstream number.

- [ ] **Kill the intra-run file feedback loop in postprocessing.** KPI preparation re-reads the cost
  CSVs that the OPEX step wrote earlier *in the same run*, keyed by magic row names such as `"Total"`
  (`kpi_preparation.py:732–776`). Pass the values in memory. **Payoff now:** removes an ordering
  dependency between postprocessing options and a whole class of "works alone, fails in the full
  run" bugs.

- [ ] **Replace the pickle result export with parquet or CSV+JSON.** `postprocessing_main.py`
  pickles a DataFrame; pickle has no cross-language reader and is a security and
  version-compatibility liability in Python too. This one has external consumers, so it needs an
  announcement and a deprecation window — start it early precisely because of that.

- [ ] **Separate physics from reporting boilerplate in the large component modules.** §4.1 of the
  companion document estimates 25–40% of the big files is cost/KPI/report code. Split per component,
  one PR each, no behaviour change. **Payoff now:** `building.py` at 2,099 lines and
  `more_advanced_heat_pump_hplib.py` at ~2,750 become reviewable, and the physics gets testable
  without the reporting stack.

- [ ] **Stop the byte surgery in the connection log.** `component.py:447` seeks to
  `st_size - 1` and overwrites the closing bracket of `component_connections.json` to append an
  entry. Buffer the connections and write the file once when wiring completes. **Payoff now:** an
  interrupted run stops leaving a syntactically broken JSON file behind.

- [ ] **Make the convergence criterion explicit and unit-aware.** `component.py:194` compares every
  output against a hardcoded absolute `0.0001` — watts, joules, kelvin, euros and dimensionless
  ratios on one scale — and the forced-convergence thresholds (>10 iterations, >100 raises) are
  hardcoded next to it. At minimum make all three configurable and document the choice; ideally scale
  per unit. **Payoff now:** the tolerance becomes a modelling decision that can be justified, and
  the "runs converge but nobody knows how tightly" question gets an answer.

---

## Tier 3 — Removing Python-only crutches

- [ ] **Explicit state structs for `i_save_state` / `i_restore_state`.** Five component modules
  `deepcopy` themselves or their state each iteration. An explicit small state object is faster in
  Python and removes the "which attributes are state?" ambiguity. **Payoff now:** measurable — this
  runs once per component per convergence iteration per timestep.

- [ ] **Inject the cache backend, the clock and the RNG instead of patching them in tests.** 22 test
  files use `monkeypatch`, 8 of them around `utils.get_cache_file`; without that patching, runs are
  order-dependent through the shared on-disk cache, and `conftest.py` scrubs `HISIM_CACHE_*` around
  every test. Turn these into constructor parameters. **Payoff now:** order-independent tests, less
  autouse machinery, and the stale-cache class of false failures loses its main route in.

- [ ] **Make the cache key an explicit versioned serializer.** The key is
  `sha256(dataclasses_json output + sim-params string)`, so field order, float repr and enum spelling
  from a third-party library are load-bearing for ~319 MB of committed cache and every cross-machine
  share. Write an owned key serializer with a stable field order, a schema version in the key, and a
  test that pins the exact string for a fixture config. **Payoff now:** upgrading `dataclasses_json`
  or `pyhumps` stops silently invalidating every cache entry in the fleet.

- [ ] **Audit the exact float comparisons in control flow.** Roughly 60 `== 0`-style comparisons sit
  in component control paths (`water_mass_flow_rate == 0`, `control_signal == 0.0`, `delta_t == 0`
  patched to `1e-8`). Replace with documented epsilons and re-bless the goldens once. Do this in
  Python, where re-blessing is cheap and reviewable, rather than discovering it during a port where
  the change is indistinguishable from a translation bug.

- [ ] **Move executable code out of the data directory and stop reading data at import time.**
  `hisim/inputs/photovoltaic/module_selection.py` is Python inside `hisim/inputs/`, and it reads PV
  module databases at import. Relocate to `hisim/components/` and make the read lazy. **Payoff now:**
  import time, and the data directory becomes purely data.

- [ ] **Separate tracked reference data from generated cache.** `hisim/inputs/` holds ~319 MB of
  git-tracked reference data; on a working copy it reaches 1.7 GB because generated caches live in
  the same tree. Move the cache out of `hisim/inputs/`, and consider distributing the reference data
  as a versioned package or download. **Payoff now:** clone size, and cache from one branch stops
  masquerading as input data in another.

- [ ] **Prune `requirements.txt`.** `seaborn`, `plotly` and `html2image` are declared but imported
  nowhere under `hisim/`, `tests/`, `tools/`, `scripts/` or `system_setups/`; there are
  commented-out pins (`pvlib`, `windpowerlib`,
  `wetterdienst`) and a migration TODO. An accurate dependency list is a prerequisite for reasoning
  about the domain-library gap at all.

- [ ] **Converge on one front end.** There are three ways to describe a system: 23 setup functions in
  `system_setups/*.py`, 22 `.scenario.json` twins, and 28 `.energy_system.yaml` files, with byte-
  identity freshness gates keeping the generated ones in sync. Per the declarative-energy-systems
  plan the YAML is the intended survivor; finish that migration and retire the others. **Payoff now:**
  one loader to keep correct instead of three, and one place where a new component must be
  registered. **Payoff for a port:** the single largest reduction in porting surface available
  anywhere in this list.

---

## Tier 4 — Decisions to take before a port, not cleanups

These need a human ruling. None of them is coding work; all of them block estimation.

- [ ] **MPC and casadi.** Reformulate as a MILP (the companion document argues the dynamics are
  linear between disjunctions), accept a permanent Python island, or declare MPC out of core scope.
  This is the only 🔴 in the whole analysis; leaving it open leaves the total open.
- [ ] **Charts and the PDF report.** Docker mode already runs with every chart and the PDF disabled,
  which proves they are optional. Decide whether they are in core scope; if not, formalize the JSON
  manifest that external tooling would render.
- [ ] **The LPG/.NET dependency.** The forever-dependency is the closed-source .NET binary, not the
  Python wrapper. Decide whether the core simulator may depend on a subprocess at all.
- [ ] **The parity policy** (Tier 2, first item) and **the FFI direction** (review item R5) — both are
  decisions masquerading as engineering tasks.

---

## Explicitly not recommended

- **Do not chase bit-exactness.** It is unattainable across languages and, per review item R6, it also
  means faithfully reproducing known defects. Decide tolerances instead.
- **Do not rewrite tests ahead of a parity rig.** The P3 parity rig
  (`scripts/p3_parity_*.py`, `hisim/energy_system/parity.py`) is the template; it compares two
  implementations of the same setup, which is exactly the Python-versus-Rust question.
- **Do not start any cleanup whose only justification is the port.** See the rule at the top.
- **Do not freeze feature work for this.** Review item R9 makes the point quantitatively: the repo's
  change rate is the factor most likely to decide the Rust question, and a freeze is not available.
