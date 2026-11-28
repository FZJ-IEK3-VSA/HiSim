# Spec: Every component reaches its Rust twin through one protocol that cleans up the Python first, pins it with tests that check behaviour, and turns every golden miss into a test

Status: draft, decisions P1–P6 taken (owner, 2026-11-25). Steps 3–6 are in use: Opus wrote the harnesses, traces and
gates of the heat pump, the building, both storages and the energy manager, and local agents converted the heat pump
and the building (§7); the inspection (steps 1–2), the coverage and mutation bar of step 3 and the miss tool of step 7
are not built. A living document: §7 collects what each conversion finds, and the protocol changes with it.
Owner: Noah Pflugradt. Epic: hisim-renovisor-rust-engine-4fhq. Builds on: `roadmap/rust_ideas/renovisor_rust_engine_spec.md`
(D1–D15; called *the engine spec* below), `roadmap/rust_ideas/hisim_rust_recommended_cleanup_steps.md`.

## 1. Problem

The engine spec converts the components RenoVisor uses into Rust twins with local coding agents, accepting a twin when
it gives Python's results. That makes the Python code the definition of every twin, with its defects. Preparing a
single brief, the heat pump's, found three:

- the hplib cache key leaves out the minimal thermal power, so a grid point's answer is whichever call computed it
  first (harmless today, since every operation mode always passes the same minimum, but a trap for the next change);
- the start-up ramp uses `np.exp`, whose last bit differs from the C library's in about 5 % of inputs on this
  machine, so the Python results themselves depend on the CPU's vector unit;
- no real run exercises the SCOP calibration curve, so nothing would have noticed a twin that got it wrong.

Converting such code as it is copies its defects into a second language, where a later fix costs twice and is
indistinguishable from a translation error. And the acceptance the engine spec started with, golden KPIs over a full
year, says *that* a twin differs, not *where*: it is a slow, coarse loop for an agent and a poor guide for what to fix.

## 2. Decisions

**P1 — Every conversion and every update of a twin follows the protocol of §4: inspect, fix what blocks, pin with
tests, convert, gate, run the goldens, turn every miss into a test** (owner, 2026-11-25). The conversion is the
occasion to clean up the component, and the tests it needs are worth having for the Python model too. Consequence:
a component is converted from a frozen commit after its blocking findings are fixed, never from code under review.

**P2 — A local agent inspects; it changes nothing in Python** (owner, 2026-11-25). D4 of the engine spec applies to
inspection as to conversion. Consequence: the inspection's output is a findings report against a fixed checklist
(§4.2); the owner or Opus triages it into beads, and Python changes go through ordinary pull requests.

**P3 — A finding is blocking or not; only blocking findings come before the conversion** (owner, 2026-11-25).
Blocking is what makes the Python reference ambiguous for a twin (§4.3); everything else is a bead that does not hold
the conversion up. Consequence: cleanup does not become a precondition that never ends, and a non-blocking cleanup
that lands later re-converts the twin once, through its provenance manifest (D8).

**P4 — A twin is accepted on behaviour at the component's boundary; tests of internal helpers check the Python model,
not the twin** (owner, 2026-11-25). Functional tests (expected values: energy balance, limits, mode transitions) say
whether the Python model is right; differential tests (the same inputs to Python and to the twin) say whether the twin
is faithful. Both are needed, for different questions, and only boundary-level cases can run against a twin.
Consequence: a component's harness is judged by branch coverage *and* mutation score of its step code (§4.4), not by
the number of its tests.

**P5 — The goldens are not part of the agent's loop: the host runs them after the gate, and a golden miss becomes a
harness case before the twin is fixed** (owner, 2026-11-25). Goldens are slow (1 min at 900 s, 8–13 min at 60 s for a
heat-pump year) and coarse; a trace diff localises a miss to one call. Consequence: the brief of a conversion task no
longer tells the agent to run goldens; a miss goes through §4.6.

**P6 — The four running heat-pump pilot sessions keep their brief** (owner, 2026-11-25), so the pilot's numbers stay a
clean measurement; the protocol applies from the next task on, and to the heat pump retroactively (§8, Q4).

## 3. Terms

- **Step code**: what one `i_simulate` call runs, with `i_save_state`, `i_restore_state` and every function they call;
  not `__init__`, configuration, KPI, cost or report code.
- **Finding**: one entry of an inspection report: the checklist item, file and line, consequence, class (blocking or
  not), proposed fix, and whether the fix changes results.
- **Blocking finding**: one that makes the Python reference ambiguous for a twin (§4.3).
- **Boundary case**: a harness case that drives the component only through its ports, save, restore and the
  hand-over, so it runs unchanged against Python, the stand-in and the twin.
- **Mutation score**: of the mutants a mutation tool makes of the step code, the share the harness's `python` cases
  detect; a mutant that survives marks code the harness runs without checking.
- **Miss**: a golden that fails in mixed mode while the gate passed.
- **Numerical amplification**: a miss whose first diverging call agrees within D7's tolerance and grows only over
  the run, through a threshold or a convergence decision the difference flips.

## 4. Design

### 4.1 The protocol

| # | Step | Who | Output |
|---|---|---|---|
| 1 | Inspect the component against the checklist (§4.2), with the mechanical checks run first | local agent, read-only | findings report |
| 2 | Triage: blocking or not (§4.3), one bead each; fix the blocking ones | owner or Opus; ordinary PRs | beads; fixes, with a re-bless where results change |
| 3 | Pin: boundary harness with functional and differential cases, traces recorded (§4.4) | Opus, or a test-writing agent with review | `tests/rust_twins/test_<component>.py`, coverage and mutation score; **frozen commit** |
| 4 | Convert against the harness and the visible traces | local agents | crate under `rust/` |
| 5 | Gate: build, harness, held-out traces | host | accept or refuse |
| 6 | Goldens in mixed mode, full year, every covered setup, both step lengths | host | pass, or a miss |
| 7 | A miss: trace diff, then a harness case or a tolerance question (§4.6); back to 4 | host, Opus | new case; or an `owner-decision` bead |
| 8 | Update after a Python change: the same steps, limited to the diff (§4.7) | as above | re-converted twin |

Steps 1–3 of the next component run while step 4 of the current one does. The bottleneck is the review of the step-2
pull requests, not the agents.

**Whole components** (D14 of the engine spec, 2026-11-28). A conversion so far produces the twin of the step:
`i_simulate`, `i_save_state` and `i_restore_state`, built from the hand-over Python writes after preparation. The goal
is now the whole component in Rust (construction from the configuration, preparation, ports, `i_doublecheck`). The
protocol stays the same and runs twice per component: first for the step, with the hand-over as its input; then for
the rest, with the hand-over as its oracle (the component built from its configuration in Rust must hold the
attributes the Python hand-over carries). Step code in §3 widens accordingly for the second pass.

### 4.2 The inspection checklist

Mechanical checks first, run by a script, their results handed to the inspecting agent:

- **State completeness**: record a trace of a short run and replay it on the component (`python -m hisim.rust_engine
  replay`); a call that does not reproduce means state outside save/restore.
- **numpy in the step**: every `np.` call in the step code (vector-unit arithmetic: `exp`, `log`, `power`; numpy
  formulas: `interp`, `polyval`, `round`).
- **Mutable state beyond the instance**: module-level or class-level mutables written in the step; `SimRepository`
  reads or writes in `i_simulate`.
- **Coverage and mutation score** of the step code under the current tests (§4.4).

Then the agent reads the step code against these items, each with the rule it comes from:

| Area | Item | Source |
|---|---|---|
| State | everything the step reads from one call to the next is saved and restored; every cache is a pure function of its complete key | engine spec §4.4, Appendix A of `rust_simulator_spec.md` |
| Arithmetic | no CPU-dependent call where a portable one is meant; exact `==` on floats only where the value is exact by construction; `round(x, n)`, `int()` and NaN handling deliberate | modelling guide |
| Ports | inputs read only through ports; an unconnected optional input's branch deliberate; which outputs a call leaves unset, and why | `component_design_principles.md` |
| Role | the component keeps to its kind (physics, L1, L2, meter); decides nothing its controller decides | `component_design_principles.md` |
| Physics | energy ports declared and balanced; signs and units stated; every parameter sourced; limits clamped or refused, never silently passed | `modelling_guide.md` |
| Structure | step code apart from KPI, cost and report code; no dead branches; no logic duplicated with another component | `coding_style.md` |
| Tests | which existing tests are boundary cases and which test helpers; what the surviving mutants show unchecked | P4 |

Every finding names its item, the file and line, the consequence in a sentence, its class, a proposed fix and whether
the fix changes results. A finding without a consequence is dropped.

### 4.3 Blocking or not

A finding is **blocking** if a twin converted from the code as it is could not be judged right or wrong:

- state outside save/restore (the replay of §4.2 fails);
- history dependence beyond what the component declares (a cache whose answer depends on which call came first, when
  the calls differ in something the key omits);
- nondeterminism (unseeded randomness, iteration over unordered sets that changes results);
- platform-dependent arithmetic whose effect D7's tolerance does not absorb.

Everything else is **non-blocking**: design, naming, structure, latent traps, test gaps the harness closes. A
non-blocking fix that lands after the conversion changes the twin's source files, so the twin goes stale and is
re-converted once (D5, D8); collect such fixes and land them together.

A fix that changes results needs the owner's decision and a re-bless; it is cheaper in Python than during a port, where
the same difference looks like a translation error.

### 4.4 Pinning the component

- **Boundary cases only count for the twin** (P4): operating points per configuration, multi-step sequences with
  repeated and forced passes, generated sequences, unconnected optional inputs, refused inputs, replays of recorded
  traces. The heat pump's harness is the template.
- **Functional assertions** where the case has a known answer (the energy balance, a limit held, a mode change at the
  documented condition) run against every implementation; one that fails on Python is a Python finding, not a twin
  problem.
- **Configurations**: every configuration the covered setups use, plus every branch-relevant option (the calibrated
  heat pump was added because no real run states a SCOP).
- **Quality bar**: branch coverage of the step code (the pilot takes 90 %) and a mutation score of the step code
  (threshold: Q1). Both are measured under the `python` case and recorded in §7.
- **Traces**: visible ones for the agent, held-out ones of other setups and seasons for the gate.
- **Frozen commit**: the conversion task is built from one commit; the twin's provenance manifest (D8) names it.

### 4.5 Converting and gating

The engine spec's pipeline (§4.5 there), with one change by P5: the brief no longer asks the agent to run goldens. The
agent's loop is the harness and the visible traces; the gate adds the held-out traces. A gate passes or refuses; it
does not run goldens.

### 4.6 After the gate: goldens and misses

The host runs the goldens of every covered setup in mixed mode, full year, at 900 s and 60 s. A miss goes through:

1. Re-run the missing setup with the trace recorder on, once in Python and once in mixed mode.
2. `python -m hisim.rust_engine diff-traces` names the first call where they part.
3. If that call's outputs differ beyond D7's tolerance: a tool (§9) turns the call (inputs, state before, expected
   outputs and state after) into a fixed harness case, `tests/rust_twins/cases/<component>/*.json`, which every
   implementation replays. The case must fail on the twin; then the twin is re-converted against it (step 4).
4. If the first diverging call agrees within D7 and the difference only grows over the run (numerical amplification),
   the twin is not wrong: the question is the tolerance (Q1 of the engine spec, decided for the pilot by D7), and it
   becomes an `owner-decision` bead with the trace diff attached.

Every miss is a line in §7: which setup, which call, which branch, which case it became.

### 4.7 Updates

When the Python side of a twin changes (D5): inspect the diff (§4.2, limited to the changed lines), add or change the
harness cases for the changed behaviour, re-record the traces, re-convert from the new frozen commit, gate, goldens.

## 5. What changes and what does not

- Results: only through blocking fixes, each with its own decision and re-bless.
- Public interfaces: none.
- Goldens and twins: re-blessed by blocking fixes that change results; twins re-converted when a non-blocking fix lands.

## 6. Verification

The protocol is checked against itself: the heat pump goes through steps 1–3 retroactively (Q4), and the inspection's
findings are compared with the three found by hand (§7). An inspection that misses them is not good enough yet.

## 7. Findings along the way

A log, newest last. Class B is blocking, N non-blocking.

| Date | Component | Finding | Class | Bead | State |
|---|---|---|---|---|---|
| 2026-11-25 | heat pump | The hplib cache key leaves out the minimal thermal power, so a grid point's answer is the first call's; today every operation mode always passes the same minimum, so no result depends on it | N (latent) | hisim-component-physics-4g9.72 | open |
| 2026-11-25 | heat pump | `np.exp` in the start-up ramp (`fixed_flow_operating_point`, storage in series or none) differs from the C library's `exp` in the last bit for about 5 % of inputs on this machine, so results depend on the CPU; not on RenoVisor's path (parallel storage) | N under D7 | hisim-component-physics-4g9.73 | open, owner decision |
| 2026-11-25 | heat pump | No golden or RenoVisor run states a SCOP, so the calibration curve (`ScopCalibration.factor`, `np.interp`) runs in no real run | N, test gap | – | closed by the harness configuration `air_scop_calibrated_900s` |
| 2026-11-25 | heat pump | The step's state is exactly the 15 `state.*` fields: a week's 4,534 calls replay bit for bit; the hplib `delta_t` is set before every use and carries nothing | – (confirms) | – | – |
| 2026-11-25 | heat pump | One 900 s step of the heat-pump household took 27 passes (mean 6.3); the pass count drives the loop's time | N, speed | hisim-renovisor-rust-engine-4fhq.19 | open |
| 2026-11-25 | harness | One gate check reported 8 trace-replay errors that three re-runs did not reproduce; the gate keeps the full report and retries once on errors without failures | N, tooling | hisim-renovisor-rust-engine-4fhq.20 | open, watched |
| 2026-11-25 | hydronics | Nothing: the module was pure and its twin bit-identical in 8 of 8 conversions | – | – | – |
| 2026-11-25 | heat pump (pilot) | 4 of 4 agent conversions pass the gate on their first attempt; every twin is bit-identical to Python on all 49,079 recorded calls, the held-out ones included; a step costs 91–96 µs in Python, 6.7–9.1 µs through PyO3, 3.0–5.1 µs in Rust alone | – (measures) | – | – |
| 2026-11-25 | all 30 RenoVisor classes | The audit (`HISIM_TRACE_COMPONENTS=*`, `HISIM_TRACE_AUDIT=1`; 12 setups, a January week at 900 s, about 680,000 calls): 29 of 30 classes replay without a disagreement in the recorded *and* a shuffled order, so their step is already a function of (state, inputs). State is small: at most 30 scalar paths (the heat pump), one sequence in the building, the energy manager and the solar thermal system; no step mutates a container | – (answers "how realistic is a pure step") | – | – |
| 2026-11-25 | heat distribution controller | In a forced pass it republishes its decision by reading its own output back from the value vector (`int(stsv.values[...])`), so its step is no function of (state, inputs); a twin, which receives only inputs, cannot do this | B (for its twin) | hisim-component-physics-4g9.74 | open |
| 2026-11-25 | heat distribution system | `np.exp` in its thermal lag, on RenoVisor's path (10 of 12 setups): the CPU dependence of the heat pump finding is not confined to fixed-flow configurations | N under D7; Q3 | hisim-component-physics-4g9.73 | open, owner decision |
| 2026-11-25 | building | Reaches the simulation repository from its step through two weather lookups; no result depends on it (the shuffled replay agrees) | N | hisim-component-physics-4g9.75 | open |
| 2026-11-25 | all setups | Long steps (>10 passes) are 0–5 % of steps; in each, after the controllers freeze, the generator and the hot-water tank converge geometrically (the gap halving per pass) down to the absolute 1e-4 threshold, 1e-8 relative on a 9.6 kW flow: a real, slowly contracting fixed point, not a defect; the ~6 passes of an ordinary step cost more in total | N, speed | hisim-renovisor-rust-engine-4fhq.19 | measured |
| 2026-11-25 | audit tooling | The first audit attempts mistook three things for defects: a `NamedTuple` state (restored as a bare tuple, enums as raw values), state that `i_save_state` sets between calls, and containers of objects (fingerprinted at every call, two hours a week). The snapshot now walks `NamedTuple`s and frozen dataclasses by field and replaces them in their parent, counts a path that differs anywhere in a trace as state, and leaves containers of objects to the static scan | – (tooling) | – | fixed |

| 2026-11-27 | building (agent `hy4`) | The shared crate reads a 17-digit decimal of the hand-over one bit low (serde_json without `float_roundtrip`), so 3–5 % of the building's replayed calls disagreed until the agent enabled the feature in its crate; a wheel built alone still misses it | N, tooling (shared crate) | hisim-renovisor-rust-engine-4fhq.30 | open |
| 2026-11-27 | building, heat pump (agent `hy4`) | Both conversions pass their gates on the first attempt, bit-identical on every replayed call (25,079 and 23,735) and on 8 full-year golden runs each | – (measures) | – | – |
| 2026-11-28 | every twin in mixed mode and native | `i_doublecheck` stays the Python component's and reads Python state the twin never updates, so a twinned component's plausibility checks check nothing (the hot-water tank's sees its initial 50 °C all year) | N, but a lost check | hisim-renovisor-rust-engine-4fhq.31 | open |
| 2026-11-28 | space-heating buffer | The start-of-step node neither clips nor refuses a step that turns the water over many times: a 60 l buffer at 900 s with household-scale flows returns water at −63.6 °C, then 816 °C, and reaches 1e41 within 48 steps; the iterated mixed node refuses such a step (`MixedNodeStepTooLongError`) | N (the twin reproduces it), modelling | hisim-component-physics-4g9.80 | open |
| 2026-11-28 | space-heating buffer, heat pump | A state field starts as an int and later holds floats (`temperature_loss_in_celsius_per_timestep` = 0, like the heat pump's `delta_t_secondary_side` = 5) | N | hisim-component-physics-4g9.78 (heat pump); the shared crate's type absorbs both | open |
| 2026-11-28 | energy manager | It derives its participants, their order and its battery and heat-pump inputs from the wiring in its first `i_simulate`, so its first call is no function of (state, inputs); the hand-over does the same work when it is taken and carries the result by port name, and its dynamic outputs are recorded so the stand-in grows them again | N, structure | hisim-component-physics-4g9.81 | open |
| 2026-11-28 | energy manager | Its refusals start with `EMS:`, which the harness took for the exception's class; the stand-in now raises a step's `ValueError` as `<class>: <message>`, as the twin contract states | – (tooling) | – | fixed |
| 2026-11-28 | building, storages, energy manager | Their harnesses (57, 128, 198 and 167 cases) were written without measuring the branch coverage §4.4 asks for, and their conversions started anyway | N, protocol | hisim-renovisor-rust-engine-4fhq.39 | open |
| 2026-11-28 | gates | A gate's checkout is read-only, so a test that runs a real energy system needs the cache variables `tests/conftest.py` clears per test; the Rust loop's first gate failed on that, not on the loop | – (tooling) | – | fixed |
| 2026-11-28 | goldens in the gate | Every gate built so far still runs the goldens itself, against P5 | N, protocol | hisim-renovisor-rust-engine-4fhq.18 | open |

Measurements of the protocol itself (inspection precision, harness scores, misses per twin) go here as they come.

## 8. Open questions

- **Q1. The mutation-score threshold.** Options: a fixed bar (for example 80 % of the step code's mutants killed), or
  no bar but every surviving mutant reviewed and either killed by a case or marked equivalent. Recommendation: the
  review, until the heat pump's score shows what a bar should be. Bead hisim-renovisor-rust-engine-4fhq.16
  (`owner-decision`).
- **Q2. Who writes the harness**: Opus, or a local agent in a test-writing role with a person's review. Recommendation:
  Opus for the heat pump and the next two components, then measure a local test writer against them. Bead
  hisim-renovisor-rust-engine-4fhq.17 (`owner-decision`). *So far Opus wrote five (heat pump, building, both
  storages, energy manager); no local test writer has been measured.*
- **Q3. `np.exp` in the heat pump's ramp**: change it to `math.exp` (results of the series and no-storage
  configurations move in the last bits; the goldens of setups that use them are re-blessed), or keep it under D7.
  Recommendation: change it; a model whose results depend on the CPU's vector unit is a defect regardless of Rust. Bead hisim-component-physics-4g9.73 (`owner-decision`).
- **Q4. The heat pump retroactively**: run the inspection (step 1) on it now, while the pilot runs, and compare with
  the findings above. Recommendation: yes; it is the cheapest calibration of the inspector. Bead hisim-renovisor-rust-engine-4fhq.13.

## 9. Beads

Epic hisim-renovisor-rust-engine-4fhq; component findings under hisim-component-physics-4g9.

| Bead | Content | PR |
|---|---|---|
| hisim-renovisor-rust-engine-4fhq.12 (P2) | the inspection step: mechanical checks and the checklist read by a local agent (§4.2) | – |
| hisim-renovisor-rust-engine-4fhq.13 (P2) | the heat pump inspected retroactively, compared with §7 (Q4), after .12 | – |
| hisim-renovisor-rust-engine-4fhq.14 (P2) | the tool that turns a first diverging call into a harness case (§4.6) | – |
| hisim-renovisor-rust-engine-4fhq.15 (P2) | the heat pump's mutation score, surviving mutants killed or marked equivalent (§4.4) | – |
| hisim-renovisor-rust-engine-4fhq.16 (P2) | Q1, the mutation-score bar (`owner-decision`), after .15 | – |
| hisim-renovisor-rust-engine-4fhq.17 (P3) | Q2, who writes a harness (`owner-decision`) | – |
| hisim-renovisor-rust-engine-4fhq.18 (P2) | the goldens leave the gate and run after it (P5) | – |
| hisim-renovisor-rust-engine-4fhq.19 (P2) | the 27-pass step (§7) | – |
| hisim-renovisor-rust-engine-4fhq.20 (P3) | the intermittent harness errors in one gate check (§7) | – |
| hisim-component-physics-4g9.72 (P3) | the hplib cache key without the minimal power (§7) | – |
| hisim-component-physics-4g9.73 (P3) | Q3, `np.exp` or `math.exp` in the start-up ramp (`owner-decision`) | – |
| hisim-renovisor-rust-engine-4fhq.30 (P2) | `float_roundtrip` in the shared crate (§7) | – |
| hisim-renovisor-rust-engine-4fhq.31 (P2) | `i_doublecheck` of a twinned component (§7) | – |
| hisim-renovisor-rust-engine-4fhq.39 (P2) | the harness quality bar for the building, both storages and the energy manager (§7) | – |
| hisim-component-physics-4g9.80 (P2) | the buffer's start-of-step node on a step that is too long (§7) | – |
| hisim-component-physics-4g9.81 (P3) | the energy manager's wiring derived in its first step (§7) | – |
