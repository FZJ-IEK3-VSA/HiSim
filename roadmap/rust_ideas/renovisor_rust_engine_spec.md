# Spec: A RenoVisor calculation runs in Rust alone, in under 5 s and 2 s as the target, with Python as the reference every Rust component is checked against

Status: building, on the branch `renovisor-rust-engine-spec`; the code reaches `main` in the pull requests that follow this spec. The agent tooling, the Python half of the twins, the Rust loop (§4.7) and the twins of the heat pump and the building are built; the twins of the two storages and the energy manager are being converted. D13–D15 (2026-11-28) widen the goal from twins of the step to whole components and a run without Python (§4.8). Owner decisions D1–D17 taken. Owner: Noah Pflugradt.
Epic: hisim-renovisor-rust-engine-4fhq. Builds on: `roadmap/rust_ideas/rust_simulator_spec.md` (options O2–O7,
O10 and the discarded ideas D1–D6 there), `roadmap/rust_ideas/hisim_rust_recommended_cleanup_steps.md`,
`roadmap/rust_ideas/feasibility_rust_hisim.md`. How each component gets from Python to its twin, and what each conversion finds, is
`roadmap/rust_ideas/conversion_protocol_spec.md`.

## 1. Problem

The frontend needs a full-year RenoVisor calculation in under about 20 s within the next months, preferably less.
Today it takes 43 s.

Measured 2026-11-25 at `6cbaef54`, `python -m hisim.renovisor run` on the vendored worked example
`hisim/renovisor/contract/calculation-request.mockup-1.yaml` (heat pump, two tanks, PV, 13 components), 900 s,
full year, warm cache, one process in the `hisim-agent` image on the 32-core host:

| Phase | Time |
|---|---|
| Interpreter start and imports | 1.7 s |
| Reading and translating the request | 0.1 s |
| Preparing the components (`i_prepare_simulation`, weather, LPG, building) | 2.3 s |
| **Timestep loop** (35,040 steps) | **36.8 s** |
| Postprocessing, KPIs, energy balance, lifecycle costs (1.3 s) | 2.5 s |
| Writing `result.json` and the manifest | 0.4 s |
| **Total** | **43.0 s** |

The loop runs **6.3 iterations per timestep** on average (221,967 calls of each `i_simulate` over 35,040 steps),
2.9 million component calls at about 13 µs each. Under cProfile the loop's time splits into:

| Component (cProfile, cumulative, share of the loop) | Share |
|---|---|
| `MoreAdvancedHeatPumpHPLib.i_simulate` | 39 % |
| `SimpleHotWaterStorage` + `SimpleDHWStorage` | 19 % |
| `Building.i_simulate` | 9 % |
| the two heat-pump controllers (`explicit_state_controller`) | 9 % |
| meters, heat distribution, heating curve, PV, LPG, weather | ~10 % |
| engine (`process_one_timestep`, restore, convergence, `set_output_value` 36 M calls) | the rest |

One pure-Python hotspot inside the loop: `MoreAdvancedHeatPumpHPLib.publish_operation` does a pandas row lookup
on every call (222,000 `DataFrame.__getitem__`), about 9 % of the loop.

**The budget this sets.** Everything outside the loop is 6.2 s. Under 20 s therefore needs the loop under about
13 s, a factor of three. Python fixes alone (the pandas lookup, fewer iterations per step) plausibly reach 17–23 s:
too close to the line to plan on. A loop in Rust with every component native plausibly takes the loop to 1–2 s and
the run to about 8 s; then the 6.2 s outside it becomes the next target.

**The goal since 2026-11-28** is stricter: under 5 s for a calculation to be usable, 2 s the target (D13). That
budget has no room for a Python process (importing HiSim alone takes 2.3–2.7 s), so the plan now ends in a run
without Python (D14, D15, §4.8); the 20 s of D1 is the step on the way.

## 2. Decisions

**D1 — A full-year RenoVisor calculation finishes in under about 20 s, preferably less, within the next months**
(owner, 2026-11-25). It is a frontend need. Consequence: the loop must get at least three times faster (§1), which
a loop in Rust with every RenoVisor component native achieves and Python fixes alone likely do not.
*Superseded as the goal by D13 (2026-11-28); 20 s stays the near-term step.*

**D2 — Python stays the master; the main simulator loop and every component RenoVisor uses get a Rust twin,
switched on by a command; everything else stays Python only** (owner, 2026-11-25). The Python implementation is
the definition of every result; a twin is an accelerator and never the source of truth. Consequence: a run whose
graph holds a component without a fresh twin runs in Python.

*Amendment (owner, 2026-11-25):* not the whole run: only that component runs in Python, inside the Rust engine
(D8, §4.3).

*Amendment (owner, 2026-11-28, D14):* "everything else stays Python only" no longer holds: whole components and the
layers around the loop move to Rust too. Python stays the definition of every result.

**D3 — A twin is accepted when Python and Rust give the same results for the full-year runs across the golden
reference suite** (owner, 2026-11-25). Consequence: the acceptance set is every golden setup whose graph the twins
cover completely (§4.6); "the same" is the golden comparison's tolerance unless Q1 decides otherwise.

**D4 — No commit from a local Rust agent changes the Python side** (owner, 2026-11-25). Consequence: the host
accepts an agent's diff only under `rust/`; the Python half of the bridge (§4.2), the trace recorder (§4.5) and
any Python fix that makes a port easier go through the normal pull requests, written by a person or by Opus.

**D5 — An LLM keeps the twins current when the Python side changes** (owner, 2026-11-25). Consequence: a change to
a mirrored Python module marks its twin stale (§4.4) and queues a re-conversion for the local agents.

**D6 — A throwaway conversion of the heat pump by the local DeepSeek agents tests the process first** (owner,
2026-11-25). The heat pump is the largest and most-changed twin, so it tests the agents on the hardest case; it is
thrown away because the hydronic epic (`hisim-hydronics-fxix`) is still rewriting it. Consequence: the pilot runs
the Rust heat pump inside the Python loop (§4.3, mixed mode) so D3's acceptance applies to it alone.

**D7 — "The same results" is the goldens' own tolerance, rel 1e-9 and abs 1e-12, for the pilot** (owner,
2026-11-25). Answers Q1 for now. Consequence: a flipped hysteresis or convergence decision is a twin bug, fixed by
matching the Python arithmetic; the tolerance is revisited after the pilot with its first-divergence causes in hand.

**D8 — Every twin documents the exact Python files it was converted from, and the engine checks them at start-up:
a twin whose files are unchanged runs in Rust, every other component runs in Python** (owner, 2026-11-25). Answers
Q2: a stale twin blocks nothing; it costs speed until the agents re-convert it, never correctness. Consequence:
the engine runs Rust and Python components side by side in one loop (§4.3), and the file list must be complete,
checked mechanically (§4.4).

**D9 — Before a component is converted, a test harness of defined cases pins its behaviour, and the same pytest
tests run once against the Python component and once against its twin** (owner, 2026-11-25). A difference
between the two that the goldens or traces find and the harness did not is a missing test: the case is added to
the harness before the twin is fixed. Consequence: the harness, not the goldens, is what guides a converting
agent; the goldens stay the acceptance (D3). See §4.5.

**D10 — 20 s is the ceiling, not the goal: a calculation should finish as fast as achievable, because the
frontend's users wait for it** (owner, 2026-11-25). Consequence: once the loop is in Rust, the 6.2 s outside it
(imports 1.7 s, preparation 2.3 s, postprocessing 2.5 s) is the larger part and gets its own work; a warm worker
process that has already imported HiSim, as the HPC harness's warm pool does, removes the import share. Whether
a cold cache counts against the ceiling stays Q3. *Amended by D13 (2026-11-28): the ceiling for use is 5 s and the
target 2 s, which a warm Python worker does not reach (§4.8).*

**D11 — Which components run on Rust twins is a list in the simulation parameters, `rust_twins`; the environment
variable `HISIM_TWINS` only overrides it** (owner, 2026-11-25). One variable per component would not scale, and a
variable is not part of the run's record. Consequence: `rust_twins: []` (the default) is pure Python, `["*"]` every
registered twin that is installed (later: fresh, D8), `["heat_pump"]` exactly those, and a twin named but not
installed fails the run; the list is written to `realized.simulation.yaml` when it is not empty and is part of no
cache key, since a twin must not change results.

**D12 — The step values are floats** (owner, 2026-11-26). `SingleTimeStepValues.set_output_value` stores
`float(value)` and an unconnected input reads `0.0`, so the Python loop and the Rust loop's vector of 64-bit floats
hand every component the same values and give result tables of the same types. Measured: all 115 golden pairs pass;
the 31 outputs that only ever held ints (counters, controller modes) become float columns, so exports print `3.0`
where they printed `3`; a run costs about 1 % more Python time (`float()` on 38 million published values of a
full-year heat-pump run).

**D13 — A calculation must finish in under 5 s to be usable, and 2 s is the target** (owner, 2026-11-28). Replaces
the 20 s of D1 and D10 as the long-term goal; 20 s stays the near-term step. Consequence: Python cannot be in the
process. Importing HiSim alone takes 2.3–2.7 s and Python's fixed costs outside the loop about 8 s (§4.8), so the
2 s needs a run in which no Python executes at all.

**D14 — Rust converts whole components and the whole run, not only the step; Python stays the reference and the
oracle** (owner, 2026-11-28). Amends D2. A Rust component is built from its configuration, prepares itself, runs its
step, saves and restores its state, checks itself (`i_doublecheck`) and declares its ports, as the Python component
does; around the components, Rust reads the energy-system and simulation files, sizes, wires, reads the prepared
data and writes the results. Python stays where components are designed and changed first, and the definition of
every result: each Rust layer is accepted by comparing it with what Python produces for the same input (§4.8). D4
holds unchanged. Consequence: the twins of the step are the first part of each Rust component, not a separate
artifact; the hand-over, the loop plan and the goldens, which today feed Rust, become the oracles the new Rust layers
are tested against.

**D15 — The first run without Python is one RenoVisor energy system, from its YAML to its results** (owner,
2026-11-28). `energy_systems/household_heatpump_building_sizer.energy_system.yaml`, full year at 900 s, with the KPIs
RenoVisor reads; weather, load profiles and other prepared data are read from the cache a Python run wrote. It
matches its golden (D7's tolerance). Consequence: the 15 components of that system get whole Rust components first,
and the layers around them only as far as that system needs.

**D16 — A Rust-only run refuses a stale twin; D8's check is built once the household's 15 components have twins**
(owner, 2026-11-28). A run without Python (D14) has nothing to fall back to, so freshness becomes a build-time
guarantee: the binary carries its twins' manifests, CI fails on a twin that is stale against the Python of the same
commit, and a binary that finds the Python sources at start and sees them changed refuses to run, naming the twin
and the file. In a Python-hosted run D8 stands, and a stale twin's fallback to Python comes with a prominent warning,
not only a log line. Consequence: the manifests and the start-up check (bead 4fhq.9) follow the conversions of
§4.8 step 1 and cover every twin at once; the binary's check is bead 4fhq.41.

**D17 — The RenoVisor translator, its result payload and the lifecycle cost engine get Rust implementations too, so
a RenoVisor calculation runs from its request to its `result.json` without Python** (owner, 2026-11-28, from the
review of this spec). D15's household is the first milestone; a full RenoVisor run is the end of the plan.
Consequence: two more layers in §4.8, the translator (`hisim/renovisor/` without its tooling, about 11k lines) and
the result side with the cost engine RenoVisor requires (`hisim/economics/`, about 43k lines, more than every
component together), and whole components for every class the capability probes reach, not only the household's 15.
Their oracles exist: path verification tier 1 translates every capability probe, and a Python run writes the
`result.json` and economics result to compare with.

## 3. Terms

- **Twin**: the Rust implementation of one Python component class, or of a shared helper module
  (`hisim/hydronics.py`, `hisim/part_load.py`, `hisim/explicit_state_controller.py`, ...), or of the engine.
- **Provenance manifest**: the file beside each twin listing every source it was converted from (§4.4).
- **Fresh**: a twin whose every manifest entry's SHA-256 equals the hash of that file now.
- **Rust engine**: the timestep loop in Rust. It calls fresh twins natively and every other component back in
  Python (§4.3).
- **Native run**: a run of the Rust engine in which every component is a fresh twin; no Python call per timestep.
- **Mixed mode**: the Python loop runs, and a component with a twin is a thin Python shim that calls its twin
  once per `i_simulate`. Slower than Python for small components; for the test harness (§4.5) and the pilot.
- **Covered setup**: a golden setup whose every component class has a fresh twin.
- **Component harness**: the pytest suite of defined cases for one component, parametrized over the Python
  implementation and its twin (§4.5).
- **Trace**: per component call, the timestep, `force_convergence`, the inputs, the state before, the outputs and
  the state after, plus the save/restore sequence; recorded from Python.

## 4. Design

### 4.1 Layout

- `rust/` — a Cargo workspace, the only place local agents write (D4). As built (2026-11-28, on the branch):
  - `hisim-twin`, the shared crate every twin builds on: the `Twin` trait the engine calls (`from_handover`,
    `simulate(step, inputs, outputs)`, `save_state`, `restore_state`, `get_state`, `set_state`), the hand-over read
    so that a missing value fails with its path, `input_ports!`/`output_ports!` binding port names to positions once,
    `state_record!` and `Memento` for the state dataclass with Python's int and float kinds, Python's numerics
    (`round`, `min`/`max`, `math.fsum`, `np.interp`, float `repr`) and the `Refusal` named by its Python exception
    class. Written by people and Opus; a conversion's gate refuses a change to it.
  - one plain crate per twin (`hisim-heat-pump`) or shared Python module (`hisim-hydronics`), with no pyo3
    dependency, so a twin cannot call into Python; the gate checks the dependency tree. `hisim-building`,
    `hisim-dhw-storage`, `hisim-hot-water-storage` and `hisim-ems` are placeholders that refuse every hand-over until
    their conversions are accepted (the building's passed its gate, §7).
  - `hisim-twin-py` and, under `python/`, one three-line crate per twin whose `python_twin!` makes it the extension
    module `hisim.rust_engine.twins` registers (`hisim_rust_heat_pump`).
  - `hisim-engine`, the loop of §4.7 in plain Rust: the loop plan, the value vector, save/restore, the convergence
    test with the plan's threshold, the evaluation order as computed by Python's `hisim/evaluation_order.py` and
    handed over, not recomputed. It is generic over the callback component and the hooks with an error type its
    caller chooses, and holds the registry of the twins it runs natively behind a dyn-compatible wrapper of `Twin`
    (which is `Sized`). `python/hisim-rust-engine` makes it the module `hisim_rust_engine`.
- `hisim/rust_engine/` — the Python half: the switch, the shims, the hand-over of prepared data, the loading of
  results. Python-owned, normal pull requests.
- The wheels are built into the RenoVisor image only (planned, bead 4fhq.27). `pip install hisim` does not need a
  Rust toolchain; a run that names a twin or the Rust loop whose wheel is missing fails instead of silently running
  Python (D11, §4.3).

### 4.2 What crosses the boundary, and when

This section describes the twins of the step, as built. D14 moves preparation, ports and postprocessing to Rust as
well; until each layer of §4.8 is accepted, the boundary is the one below.

- **Preparation stays Python.** `i_prepare_simulation` runs as today (weather, LPG, pvlib, hplib parameters, the
  TABULA building data). Each twin's brief defines the hand-over struct: what preparation produced that the loop
  reads (arrays, fitted parameters, the resolved config). It crosses once.
- **The loop crosses never** in native mode. Rust runs every timestep and returns the results matrix
  (`all_result_lines`) as one array.
- **Postprocessing stays Python.** The KPI and cost hooks read the results table and the config, not state left
  behind by the loop — checked for the heat pump (`MoreAdvancedHeatPumpHPLib.get_component_kpi_entries` reads
  `postprocessing_results` and `self.config` only); every brief must check it for its own class.
- **Ports are generated, not translated.** The Rust input/output structs are generated from the Python port
  declarations (option O6 of the simulator spec), so a renamed or added port breaks the Rust build.
- **hplib at runtime.** The heat pump calls `hplib.simulate` on a cache miss inside the loop (108 times in the
  measured run). Native mode needs those fit equations in Rust; mixed mode may call back into Python.

### 4.3 The switch and the per-component choice (D8)

**As built (2026-11-28).** `rust_twins` in the simulation parameters (D11; `["*"]` for every twin, `HISIM_TWINS`
overrides it) switches the twins on; on the Python loop they run in mixed mode. `HISIM_ENGINE=rust` runs every time
step on the Rust loop (`hisim/rust_engine/rust_loop.py`): a component whose twin is switched on and which the
engine registers (`hisim_rust_engine.native_twins()`) runs natively, every other one is called back into Python.
`HISIM_ENGINE=reference` runs the Python reference of the loop, `rust-standin` the Python stand-in of the engine
module. A run that asks for detailed iteration logging, the trace recorder or the loop census keeps the Python loop
and says why; a named engine module that is not installed fails the run. The log names the native components.
What follows is planned, not built: the manifests and the freshness check (D8), the `--engine` option and the
record of each component's implementation in the run's artifacts.

**Planned.** At start-up the engine reads every
twin's manifest, hashes the listed files (about 30 files, milliseconds) and picks per component: a fresh twin runs
in Rust, everything else in Python. It logs one line per component naming the choice, and for a stale twin the
file that changed. The choice is written into the run's artifacts (`calculation.json` for RenoVisor), so a result
states which implementation produced it. Without the wheel, or with `--engine python`, everything runs as today.
`--engine mixed` is the Python loop with twins behind shims, for the harness and debugging. RenoVisor's `run` sets
`--engine rust` in its image.

A Python component inside the Rust engine is called back once per iteration: the engine gathers its inputs into a
preallocated Python list, calls `i_simulate` against a small value-store object over that list, and scatters its
outputs back (option O3 of the simulator spec; a per-value call into Rust is slower than Python, D3 there). Each
fallback costs its Python time plus about one crossing per call. With the heat pump stale, the measured run
would spend roughly 15 s of its loop in the heat pump and land near 22 s: slower, not wrong.

### 4.4 The provenance manifest and keeping the twins current (D5, D8)

Each twin carries `rust/<twin crate>/provenance.toml`:

```toml
twin = "hisim.components.more_advanced_heat_pump.heat_pump.MoreAdvancedHeatPumpHPLib"
converted_at = "2026-12-03"
converted_by = "deepseek-v4.1 via pi, brief 2026-12-02, reviewed by claude-opus-5-5"
python_commit = "6cbaef54"

[[file]]
path = "hisim/components/more_advanced_heat_pump/heat_pump.py"
sha256 = "..."
size = 141803
committed = "2026-11-24"   # date of the last commit touching it at python_commit

[[package]]
name = "hplib"
version = "1.9"
```

- **The SHA-256 decides; size and date are for people.** A checkout resets file dates and a same-size edit
  exists, so neither may decide freshness. The date is the last commit's date, not the file's mtime.
- **Packages count.** A twin that reimplements a library's equations (hplib's fits) lists the package and its
  version; a different installed version makes the twin stale like a changed file.
- **The file list is checked for completeness, not trusted.** A test computes the import closure of the twin's
  module inside `hisim/` and fails if a module in it is neither in the manifest nor on a reviewed exemption list
  (`component.py`, `loadtypes.py`, logging: code the twin does not mirror). Without that test a change to
  `hisim/part_load.py` would leave a heat-pump twin "fresh" and wrong.
- **The manifest is written by a tool, not by an agent**: `python -m hisim.rust_engine manifest <twin>` records
  the hashes of the files at the commit the brief named, so an agent cannot declare a twin fresh against code it
  never saw.

A CI job `rust-parity` runs the covered goldens with `--engine rust` on every push to `main` and lists the stale
twins with the Python diff since each manifest's commit; each stale twin queues a re-conversion brief for the
local agents. Nothing is blocked (D8).

### 4.5 The component harness and the agent pipeline (D9)

**The component harness comes first.** For each component, before its conversion:

- `tests/rust_twins/test_<component>.py`, one pytest module whose fixture `implementation` is parametrized over
  `python` and `rust`. The test builds the component from a preset config, runs `i_prepare_simulation`, then
  drives `i_simulate` with defined input values through a value store and checks the outputs. The `rust` case is
  the mixed-mode shim, so one test body drives both; the `rust` case is skipped, not failed, when no twin exists.
- **Three kinds of case.** *Defined operating points*: tables of inputs a modeller picks — the part-load range, the
  limits, zero flow, a sign change, each mode of a controller. *Sequences*: a few timesteps with
  `i_save_state`/`i_restore_state` and repeated iterations, with `force_convergence` both ways, because most
  ported bugs hide in state. *Generated inputs*: hypothesis draws inputs within each port's declared range and
  asserts Python and Rust agree (rel 1e-9, D7) — the cheapest way to find the cases nobody thought of.
- **Two kinds of assertion.** Differential (Python and Rust give the same outputs and the same next state), and
  physical where the case has a known answer (the energy balance of the ports, a limit held). The physical
  assertions run against Python too, so the harness also finds Python bugs; those go to their own beads, never into
  the twin.
- **Enough tests is measured, not felt.** The harness must reach a stated branch coverage of the component's
  loop code (`i_simulate` and what it calls; the pilot takes 90 %) under the `python` case before conversion
  starts.
- **A miss becomes a case.** When the goldens or the held-out traces find a difference the harness passed, the
  first diverging trace record (§4.5, trace recorder) is turned into a harness case, which must fail on the twin;
  only then is the twin fixed. The harness grows from every miss, and its misses per twin are a pilot measurement.
- **Who writes it.** The harness is Python, so not the converting agent (D4). Opus writes it, or a local agent in a
  separate test-writing role whose pull request a person reviews; the converting agent sees it read-only. Keeping
  the two roles apart is what stops a twin and its tests from agreeing on the same mistake.

**The pipeline:**

- **Briefs and review**: Opus writes one brief per twin (the Python sources, the hand-over struct, the generated
  port structs, the shared kernels it may use, the harness, the traces it may see) and reviews each diff that
  already passed the mechanical gates — looking for answers keyed to trace or harness values, `unsafe`, panics,
  reordered floating-point arithmetic.
- **Workers**: Pi Agent with a local model in one Docker container per task, the repository read-only except
  `rust/` (`tools/rust_agents/`): DeepSeek V4.1 Flash for the hydronics shakedown and the pilot, `hy4` since
  2026-11-27 (§7); the worker's loop is `cargo build` plus `pytest tests/rust_twins/test_<component>.py`. The task
  snapshot leaves out the committed twin's source (`hide.txt`), so a re-conversion cannot copy it.
- **Host gates, in order, outside the container on a fresh checkout of the diff**: the diff touches only `rust/`
  and no manifest, trace, tolerance or golden file (D4); `cargo build`, `clippy -D warnings`; the component
  harness, both cases; replay of **held-out traces** from capability probes the worker never saw; the covered
  goldens in mixed mode (one twin) or with the Rust engine (all twins); then Opus review; then the tool writes the
  manifest (§4.4). Only a diff that passes every gate is merged.
- **What a gate needs of its container** (learnt 2026-11-28): the checkout is read-only, so a test that runs a real
  energy system needs a writable cache, and `tests/conftest.py` clears every `HISIM_CACHE_*` variable per test; a
  test that runs real setups restores the variables the suite started with (`tests/test_rust_loop.py`). The suite's
  stray-file guard runs `git status`, so a container that runs the test suite needs the checkout's `.git`.
- **Order**: the engine and the shared kernels first, one at a time; the components after them, in parallel up to
  the eight sessions. The components of the hydronic chain wait for `hisim-hydronics-fxix` (§8, risks).
- **The trace recorder** (Python, at the one choke point `ComponentWrapper.calculate_component`, off by default)
  is the agents' debugging tool: the goldens say *that* a run differs, the first diverging trace record says
  *where*. It also answers M5 and M6 of the simulator spec. What exists today is narrower: the post-processing
  option `PROVIDE_DETAILED_ITERATION_LOGGING` appends to `Detailed_Iteration_Log.txt`, from the third pass of a
  step on, the outputs that changed by more than 1e-4, as text with two decimals
  (`SingleTimeStepValues.get_differences_for_error_msg`). It names the outputs that keep a step iterating (M4 of
  the simulator spec), but it records no inputs, no state, no single component call and no exact value, so the
  recorder is new work beside it, not an extension of it.

### 4.6 The acceptance set (D3)

Measured 2026-11-25: the components of RenoVisor's `*_building_sizer*` energy systems are 30 classes. Of the 23
golden setups, 15 use only those classes, with 5 periods each — 75 golden files, 30 of them full-year (60 s and
900 s): `automatic_default_connections`, `basic_household_only_heating`, the eleven `household_*_building_sizer`
setups, `household_gas_solar_thermal` and `simple_air_conditioner_household_building_sizer`. The other 8 run in
Python under `--engine rust` and do not count. The golden comparison is `math.isclose` at rel 1e-9, abs 1e-12
(`hisim/postprocessing/kpi_computation/tolerances.py`) over about 1,000 KPI values per file.

The pilot's set is the four with the heat pump: `household_heatpump_building_sizer`,
`household_heatpump_car_building_sizer`, `household_heatpump_solar_thermal_building_sizer`,
`automatic_default_connections`.

### 4.7 The Rust loop (bead 4fhq.25)

The loop is the part that reaches the 20 s: twins only save time once nothing crosses into Python per call. It is
built and accepted apart from any twin, with every component still Python.

**What it must reproduce**, read from `Simulator.run_all_timesteps` and `process_one_timestep` (2026-11-26):

1. The values start at 0.0 for step 0. Every later step starts from a copy of the vector the step before converged
   to (the warm start), and so does the "previous values" vector the convergence test compares with.
2. `i_save_state` once per step, for every component **in the order added**, before the first pass.
3. A pass calls, for every component **in evaluation order** (`Simulator.evaluation_plan`, computed by Python and
   handed over), `i_restore_state` and then `i_simulate(timestep, stsv, force_convergence)`.
4. After a pass the step has converged when no value moved by more than 1e-4 (`abs(previous - current) > 0.0001`
   for none; a NaN fails that comparison, so a NaN counts as converged).
5. Then, converged or not: if the pass counter (0 for the first pass) exceeds 10, `force_convergence` is true from
   the next pass on, so the 13th pass is the first forced one; if it exceeds the step-length limit
   (`maximum_passes_for_step_length`), the run fails with the message naming every value still moving; the current
   vector becomes the previous one; the counter grows by one.
6. After convergence `i_doublecheck(timestep, stsv)` for every component in the order added.
7. The converged vector is the step's result row.
8. Around the loop: the `LOOP_START`, `PERIODIC` (at most every 5 s of wall time, with `step + 1` steps done) and
   `LOOP_END` progress events and their log line, the end-of-run pass-count summary, and Ctrl-C, which the Rust loop
   checks once per step (`Python::check_signals`).
9. Fall back to the Python loop for a run that asks for what only it offers: `PROVIDE_DETAILED_ITERATION_LOGGING`,
   the trace recorder, the audit and the loop census.

**What crosses into Rust once**, a loop plan Python builds after the setup rules: the number of values; per
component in the order added its kind, its output slots and, per input, the slot of its source or none; the
evaluation order; the step length, the number of steps and the pass limit; for every component with a fresh twin
its hand-over. **What comes back**: the results matrix and the pass counts; the native twins' final states are still to come
(bead 4fhq.31).

**A Python component inside the Rust loop** is called back once per pass with the values of its input sources and
of its own output slots (`HeatDistributionController` reads its own output past its ports), against a value store
over a preallocated list, and its output slots are copied back. That is exact only if every value a component
publishes is a float: the Rust vector holds `f64`.

**What the components exchange today**, measured by the loop census (`HISIM_LOOP_CENSUS=1`,
`hisim/rust_engine/census.py`) over the 23 full-year 900 s golden setups, 42 component classes, on 2026-11-26:

- 31 classes publish values that are not Python floats: 45.7 million ints and 0.8 million `numpy.float64` against
  402 million floats. 31 outputs only ever hold ints (counters, controller modes, `CarLocation`, the gas meter's
  totals while no gas flows); pandas makes such a column `int64`.
- 11 classes read unconnected inputs, which `get_input_value` returns as the int `0`.
- No component writes another component's outputs; only `HeatDistributionController` reads the values past its
  ports, its own output (bead hisim-component-physics-4g9.74). Gathering a component's input sources and its own
  output slots is therefore enough.

**The float-only loop.** With `set_output_value` storing `float(value)` and an unconnected input reading `0.0`, all
115 golden pairs pass (the one Sankey layout fault of `air_conditioned_house / july_week_60s` happens without the
change too). With it the Python loop and an `f64` vector agree by construction; without it the Rust loop's result
columns that hold only ints become floats where Python's are `int64`. The owner chose the float-only loop (D12).

**The reference loop exists** (`hisim/rust_engine/reference_loop.py`, `HISIM_ENGINE=reference`): `LoopPlan` and
`ReferenceLoop` as above. `tests/test_reference_loop.py` runs the heat-pump household and
`automatic_default_connections` over a winter day at 60 s and 900 s through both loops; the result frames are
identical, with and without the float-only change.

**Acceptance, in order** (all three met on the branch, 2026-11-28):

1. A Python *reference loop* that runs from the loop plan with the gather/scatter callbacks, still in Python, gives
   every golden bit for bit. It proves the plan and the callback protocol before any Rust exists, and it is the
   specification the Rust loop is tested against. *Met: 114 of 115 golden pairs on the reference loop; the one
   fault is the Sankey layout fault above, which happens without it.*
2. The Rust loop with every component called back gives the same result rows and pass counts as the reference loop
   on recorded weeks of the golden setups, then every golden. *Met: the protocol on 400 generated plans call by
   call against the stand-in, two setups over a winter day at 60 s and 900 s bit for bit against the Python loop,
   and the full-year 900 s goldens of all 23 golden setups.*
3. The heat pump twin runs natively inside it, and the full-year goldens still match. *Met: the four heat-pump
   setups.*

**The contract as built.** `hisim/rust_engine/rust_loop.py` states it in its module docstring: the engine module
exports `native_twins()` and `Loop(plan_json, components, hooks)` with `run()`, which returns the result rows as
native-endian `f64` bytes and the pass counts. A callback component works on a value store of its own: before each
call the loop writes its gathered slots (its input sources and its own outputs) into the store's list and afterwards
reads back its output slots. The hooks are `progress`, `pass_limit` (raises the simulator's error; a hook that
returns is a `RuntimeError`) and `doublecheck`. `hisim/rust_engine/engine_standin.py` is the same contract in about
100 lines of Python, the reference the Rust module is tested against call by call. What does not come back yet is
the native twins' final state: `doublecheck` runs the Python components' checks, which read Python state a native
twin never updates (bead 4fhq.31).

**What it showed.** With every component called back, the Rust loop is no faster than the Python one (39.6 s against
39.8 s for a full year of the heat-pump household at 900 s): the time is in the components, not in the loop. With
only the heat pump native it takes 24.3 s. Time is saved only by components that run natively, which is why §4.8
plans them all.

### 4.8 The run without Python (D13–D15)

**Where a run's time goes without the loop**, measured 2026-11-28 at `7c9d1da6`: `household_heatpump_building_sizer`,
full year at 900 s, in the `hisim-rust-agent` image, warm cache, Python only.

| Phase | Time |
|---|---|
| `import hisim` | 2.3–2.7 s |
| building the system (YAML, sizing, construction, `i_prepare_simulation`) and postprocessing (KPIs, costs) | about 6 s |
| the loop | 40–50 s (the machine was loaded on the second number) |
| exporting every result to CSV (`EXPORT_TO_CSV`, pandas `to_csv`), when asked for | about 20 s more |

Under cProfile the loop splits as §1 found, plus `set_output_value` (7 %) and pandas lookups inside steps (6 %). A
loop with every component native does about 3.5 million component calls a year, well under a second in Rust; the
budget of 2 s then goes to reading the prepared data and writing the results, both of which Rust does in
milliseconds where Python and pandas take seconds.

**The layers, and what checks each against Python:**

| Layer | Python today | Rust | Its oracle |
|---|---|---|---|
| reading the energy-system and simulation YAML, presets, sizing | `hisim/energy_system/`, `hisim/config/sizing.py` | to write | the resolved configurations Python realizes (`realized.simulation.yaml`, each component's `config` in its hand-over) |
| wiring: explicit and default connections, dynamic ports, evaluation order | `hisim/simulator.py`, `hisim/evaluation_order.py`, `DynamicComponent` | to write | the loop plan (`rust_loop.plan_of`): slots, input sources, evaluation order, compared as JSON |
| preparing a component (`i_prepare_simulation`, the step's first-call derivations) | each component | per component | its hand-over's attributes and extra (`twins.TwinSpec.handover`), compared value by value |
| prepared data: weather, load profiles (the LPG is an external binary), PV, solar gains | `hisim/caching`, data-source components | readers of the cache Python writes | the hand-over's series and the data sources' outputs in a trace |
| the step, save and restore | each component | the twins (§4.5) | the harness, traces and goldens, as today |
| `i_doublecheck` | each component | per component | the harness: a case whose state goes implausible fails in both (bead 4fhq.31) |
| the loop | `Simulator.run_timestep_loop` | `hisim-engine` (§4.7) | the stand-in and the goldens, as today |
| results, KPIs, costs, the files RenoVisor reads | `hisim/postprocessing/`, `hisim/renovisor/result.py` | to write | the golden's KPIs and the results table, D7's tolerance |
| the RenoVisor translator: a calculation request into an energy system and its mapping report (D17) | `hisim/renovisor/` (request, measures, envelope, TABULA, translate, report) | to write | path verification tier 1: the energy-system YAML and `mapping_report.json` of every capability probe and worked mockup, and `problems.json` of a refused request |
| RenoVisor's result payload and the lifecycle cost engine (D17) | `hisim/renovisor/` (result, KPIs, costs, layers, provenance), `hisim/economics/` | to write | Python's `result.json`, `economic_inputs.json` and economics result for the same run, per capability probe |

**A whole component** extends the twin of §4.5: next to `from_handover` it gets a constructor from its configuration
and the run's parameters that does what `__init__` and `i_prepare_simulation` do, and the two must agree: built from
the configuration, it holds the attributes the Python hand-over carries. Its ports come from the same declarations
the Python class makes, so the wiring layer can connect it without Python. The hand-over path stays: it is how a
component is checked one layer at a time, and how mixed runs keep working while the layers around it are written.

**The order of work.** (1) The step twins of the 15 components of `household_heatpump_building_sizer` (heat pump,
building and, in conversion, both storages and the energy manager; then the controllers, meter, battery, PV, heat
distribution, heating curve, weather and LPG connector), each native in the Rust loop, which measures how close the
mixed run comes. (2) Each of them whole: construction and preparation against its hand-over, `i_doublecheck`.
(3) The prepared-data readers. (4) Wiring against the loop plan. (5) Reading and sizing the energy-system YAML
against the realized configurations. (6) Results and KPIs against the golden. (7) A Rust executable that runs the
system from its two files, timed against D13. Then D17: (8) the translator against the capability probes; (9) the
result payload and the cost engine; (10) whole components for every class the probes reach; (11) a RenoVisor
calculation from its request to its `result.json` in Rust alone, timed against D13.

## 5. What changes and what does not

- Results: none. Python stays the definition (D2); a twin that differs is not accepted (D3).
- Public interfaces: `rust_twins` in the simulation parameters (built) and, planned, an `--engine {python,rust,mixed}`
  option, default `python`, with the run's artifacts naming the implementation of every component (§4.3); today the
  environment variables `HISIM_TWINS` and `HISIM_ENGINE` serve for testing. A run without Python (§4.8) adds a Rust
  executable beside `hisim`.
- Goldens and twins: unchanged. A Python change that re-blesses a golden makes the covered twins re-pass (§4.4).

## 6. Verification

The component harnesses and the host gates of §4.5; `tests/test_rust_loop.py`, the loop's protocol on generated plans
call by call against the Python stand-in and winter days bit for bit against the Python loop (§4.7); the `rust-parity` CI job (§4.4); the manifest completeness
test (§4.4); a test that a changed file, a changed package version or a missing manifest makes the engine run that
component in Python and say so; a test that a run with every twin stale gives the Python results.

## 7. Measured results

§1 (2026-11-25, `6cbaef54`).

**Step 1, the agent shakedown** (2026-11-25, `deepseek-v4.1-flash` on 8 A40s, Pi 1.5.2, `tools/rust_agents/`): the
task `tasks/hydronics` converts `hisim/hydronics.py` (pure functions, `math.fsum`, `expm1`, NaN rules) to the PyO3
module `hisim_rust_hydronics`.

| | One session | Eight in parallel |
|---|---|---|
| Passed the gate (200,000 random cases, held-out cases, a seed drawn at gate time) | 1 of 1 | 8 of 8 |
| Results bit-identical to Python | all 213,133 | all 213,133, in every run |
| Wall time | 7.3 min | 14.9–23.5 min (median 18) |
| Assistant turns / output tokens | 31 / 31k | 23–63 / 18.8k–41.5k |
| Malformed tool calls, writes outside `rust/` | 0 / 0 | 0 / 0 |

Every session fetched CPython's `mathmodule.c` to reproduce `fsum` exactly; 7 of 8 tried to read the hidden
`/task/holdout` (covered by an empty tmpfs) and 4 read `gate.sh`, whose fixed seed the first session reused: since
then the gate draws its seed when it runs. Anything an agent can read holds no secret.

**Step 2, the Python half** (`hisim/rust_engine/`, `tests/rust_twins/`): a one-week run of
`household_heatpump_building_sizer` at 900 s traced the heat pump in 4,534 calls (672 steps, up to 27 passes in one
step). Replaying the trace on a heat pump rebuilt from its hand-over reproduces every call bit for bit, so the 15
`state.*` paths are all the state its step depends on (M6 holds for it; the hplib `delta_t` is set before every use
and carries nothing). The same week in mixed mode on the Python stand-in of the twin gives a trace identical in every
call. The heat pump's harness runs 140 cases; under the `python` case it covers 97.5 % of the lines and 91.2 % of the
branches of the code a step runs (`i_save_state` to `publish_operation` and the hplib cache), the rest being refusals
of invalid configurations.

**The pilot, D6** (2026-11-25, `tools/rust_agents/tasks/heat_pump/`, four sessions in parallel, seeded with the
hydronics twin): 4 of 4 twins of `MoreAdvancedHeatPumpHPLib` pass the gate (82 harness cases, the replay of two visible
and three held-out traces, the four heat-pump golden setups for a full year at 900 s and 60 s in mixed mode), each on its
first attempt. Every twin is bit-identical to Python on all 49,079 recorded calls; numpy's `exp` does not occur on the
traced (parallel-storage) path. Per session 40–66 min, 60–87 turns, 71k–91k output tokens, no malformed tool call, no
write outside `rust/`. A step costs 91–96 µs in Python, 6.7–9.1 µs through the PyO3 boundary and 3.0–5.1 µs in Rust
alone, 10–14 and 20–30 times faster. The first gate refused all four for two faults of the gate itself (git unusable in
a worktree container; an import pattern matching `"hplib_parameters"`), fixed before the re-gate.

**Conversions by `hy4`** (2026-11-27/28, one session each, the same tooling; wall time, assistant turns, output
tokens):

| Task | Result | Session |
|---|---|---|
| heat pump (`tasks/heat_pump`, the committed twin hidden) | gate passed: 82 harness cases, the visible and held-out traces (23,735 calls bit-identical), the 8 full-year golden runs in mixed mode; 22 unit tests of its own | 2.4 h, 68 turns, 155k |
| building (`tasks/building`) | gate passed: 57 harness cases, traces (25,079 calls bit-identical), 8 full-year golden runs | 1.7 h, 68 turns, 75k |
| the Rust loop (`tasks/engine_loop`) | gate passed: 415 cases of `tests/test_rust_loop.py` (400 generated plans), the 23 full-year 900 s goldens with every component called back, the 4 heat-pump goldens with the heat pump native | 1.8 h, 90 turns, 101k |

The building run found a defect of the shared crate: `Handover::parse` uses serde_json without `float_roundtrip`,
whose fast parser reads a 17-digit decimal such as 201.81934079999994 one bit low, so 3–5 % of the building's
replayed calls disagreed until the agent enabled the feature in its own crate. Cargo unifies features only across
packages built together, so a wheel built alone still misses it (bead 4fhq.30). The loop run's first gate failed on
the gate itself (the cache of §4.5), not on the loop. Reviewed by Opus: the loop follows the stand-in's order of
operations, allocates nothing per pass on the Rust side, holds the GIL for the run and checks for Ctrl-C once per
step.

**Mixed mode and the loop, timed** (`household_heatpump_building_sizer`, full year, "Simulation took", one run each,
2026-11-27 and 28):

| Variant | 900 s | 60 s |
|---|---|---|
| Python | 40.3 s | 516.6 s |
| heat pump twin, mixed mode | 27.5 s | 360.4 s |
| building twin, mixed mode | 38.0 s | 492.9 s |
| both twins, mixed mode | 25.1 s | 342.2 s |
| Rust loop, every component called back | 39.6 s | – |
| Rust loop, heat pump native | 24.3 s | – |

The time outside the loop, and why 2 s needs a run without Python, is §4.8.

## 8. Open questions

- **Q1. What "the same results" means across languages.** At rel 1e-9 over a year with 6.3 iterations per step,
  one ulp of difference (numpy's `exp` and `interp` are not glibc's, and Rust may reorder a sum) can flip a
  hysteresis or a convergence decision and move a KPI by far more than 1e-9. Options: (a) keep 1e-9 and treat
  every flip as a twin bug to be fixed by matching the arithmetic; (b) a declared looser tolerance per KPI family.
  Recommendation: (a) for the pilot, and decide after it with its first-divergence causes in hand. *Decided for
  the pilot: D7.*
- **Q2. What a stale twin blocks.** (a) nothing: the run falls back to Python and is slow until the agents catch
  up; (b) the RenoVisor image build; (c) merges to `main`. *Decided: (a), per component, D8.* What remains open
  under it is whether the RenoVisor image should at least warn when it ships with stale twins, since the frontend's
  20 s then does not hold.
- **Q3. Whether D1's 20 s includes a cold cache** (weather, LPG and solar gains computed for a new location or
  household). The measurement in §1 is warm. D10 sets 20 s as the ceiling; since a user waits on a new address as
  much as on a known one, the recommendation is to measure a cold run next and count it, unless the backend
  pre-warms the caches. Bead hisim-renovisor-rust-engine-4fhq.8 (`owner-decision`).

Risks, not questions: the hydronic epic is rewriting exactly the components that hold about 60 % of the loop
(`hisim-hydronics-fxix`, "Live RenoVisor results are wrong until this lands"), 75 commits on them since
2026-09-01, so the production twins of the hydronic chain start after it lands or are re-converted repeatedly;
the local agents' tool-call reliability under Pi has not been measured (the GLM deployment showed silent
tool-call corruption until 2026-09-25); and the iteration count may change with the hydronic work in either
direction, which moves §1's budget. With D14, the risk of the hydronic epic grows: whole components mean more Rust to
re-convert per Python change, so the order of §4.8 puts the components of the hydronic chain after it lands where it
can.

## 9. Beads

Epic hisim-renovisor-rust-engine-4fhq.

| Bead | Content | PR |
|---|---|---|
| hisim-renovisor-rust-engine-4fhq.1 (P2) | the pandas row lookup in `publish_operation`, Python only | – |
| hisim-renovisor-rust-engine-4fhq.2 (P2) | the trace recorder (§4.5) | – |
| hisim-renovisor-rust-engine-4fhq.3 (P2) | the Rust engine with Python fallback components and `--engine` (§4.1–4.3) | – |
| hisim-renovisor-rust-engine-4fhq.4 (P2) | the agent containers and the host gates (§4.5) | – |
| hisim-renovisor-rust-engine-4fhq.5 (P2) | the throwaway heat-pump pilot (D6); closed: go, 4 of 4 passed, hp-2 kept as the reference twin in `rust/hisim-heat-pump` | – |
| hisim-renovisor-rust-engine-4fhq.6 | Q1, closed by D7 | – |
| hisim-renovisor-rust-engine-4fhq.7 | Q2, closed by D8 | – |
| hisim-renovisor-rust-engine-4fhq.8 (P2) | Q3, cold cache in the ceiling (`owner-decision`) | – |
| hisim-renovisor-rust-engine-4fhq.9 (P2) | the provenance manifests and the start-up choice (D8, §4.4) | – |
| hisim-renovisor-rust-engine-4fhq.10 (P2) | the heat pump's component harness (D9, §4.5), blocked by .3 | – |
| hisim-renovisor-rust-engine-4fhq.11 (P2) | the 6.2 s outside the loop (D10) | – |
| hisim-renovisor-rust-engine-4fhq.21 (P2) | the shared crate `hisim-twin`: native twin trait, typed hand-over, ports by index, state snapshot, Python numerics, refusal, PyO3 macro; component crates without pyo3 | – |
| hisim-renovisor-rust-engine-4fhq.22 (P2) | Rust coding guidelines and the lints that check them | – |
| hisim-renovisor-rust-engine-4fhq.23 (P2) | the scaffold command that writes a twin crate from a hand-over, blocked by .21, .24 | – |
| hisim-renovisor-rust-engine-4fhq.24 (P2) | strict-JSON hand-over with the state's int and float kinds | – |
| hisim-renovisor-rust-engine-4fhq.25 (P2) | the Rust loop, bit-identical on every golden with all components called back into Python, blocked by .21; blocks .3; acceptance met on the branch (§4.7) | – |
| hisim-renovisor-rust-engine-4fhq.26 (P2) | the heat-pump household on native twins and its first timing, blocked by .23, .25; §4.8 step 1 | – |
| hisim-renovisor-rust-engine-4fhq.27 (P3) | the wheel in the RenoVisor image and the CI parity job, blocked by .9, .26 | – |
| hisim-renovisor-rust-engine-4fhq.28 | Q: are the step values floats; closed by D12 | – |
| hisim-renovisor-rust-engine-4fhq.29 (P3) | `hisim-hydronics` takes positional what Python takes keyword-only; named fields instead | – |
| hisim-renovisor-rust-engine-4fhq.30 (P2) | `float_roundtrip` in the shared crate, so every wheel reads the hand-over's floats exactly | – |
| hisim-renovisor-rust-engine-4fhq.31 (P2) | a twinned component's `i_doublecheck` reads Python state the twin never updates | – |
| hisim-renovisor-rust-engine-4fhq.32 | closed as a duplicate of .26 | – |
| hisim-renovisor-rust-engine-4fhq.33 (P2) | whole Rust components: construction, preparation, ports, `i_doublecheck`, against the hand-over (D14), blocked by .26 | – |
| hisim-renovisor-rust-engine-4fhq.34 (P2) | Rust readers of the prepared data in the cache (§4.8 step 3) | – |
| hisim-renovisor-rust-engine-4fhq.35 (P2) | wiring in Rust, equal to the loop plan (§4.8 step 4) | – |
| hisim-renovisor-rust-engine-4fhq.36 (P2) | the YAML and sizing in Rust, equal to the realized configurations (§4.8 step 5) | – |
| hisim-renovisor-rust-engine-4fhq.37 (P2) | results, KPIs and costs in Rust, equal to the golden (§4.8 step 6), blocked by .35, .36 | – |
| hisim-renovisor-rust-engine-4fhq.38 (P2) | the run without Python: under 5 s, 2 s the target (D13, D15), blocked by .26 and .33–.37 | – |
| hisim-renovisor-rust-engine-4fhq.40 (P2) | the shared crate binds ports by run-time names and reads lists and optional values | – |
| hisim-renovisor-rust-engine-4fhq.41 (P2) | the Rust-only binary's freshness: embedded manifests, CI, refusal at start (D16) | – |
| hisim-renovisor-rust-engine-4fhq.42 (P2) | the RenoVisor translator in Rust, equal for every capability probe (D17) | – |
| hisim-renovisor-rust-engine-4fhq.43 (P2) | the result payload and the lifecycle cost engine in Rust (D17) | – |
| hisim-renovisor-rust-engine-4fhq.44 (P2) | a RenoVisor calculation from its request to its `result.json` in Rust alone, under 5 s (D17), blocked by .38, .42, .43 | – |
| hisim-renovisor-rust-engine-4fhq.39 (P2) | the harness quality bar of the conversion protocol for the building, both storages and the energy manager | – |
