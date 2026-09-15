# Simulator Hot-Path Redesign — Options Register and Measurement Plan

**Date:** 2026-09-09 · **Owner:** Noah Pflugradt · **Surveyed at:** `f91c3eb1`
**Related:** `roadmap/rust_ideas/feasibility_rust_hisim.md` (§2, §11), `roadmap/rust_ideas/hisim_rust_recommended_cleanup_steps.md`

## What this document is

An options register, not a decision. Every option below is a candidate for the value-exchange
path between the simulator and its components — the `SingleTimeStepValues` write path, the
convergence check, and the boundary a Rust implementation would sit on. Each option records what it
is, what it is expected to buy, what it costs, and **which measurement has to exist before it can be
judged**. Nothing here should be implemented before its gating measurement is in hand: one plausible
idea in this space has already been benchmarked and lost (numpy storage), which is the reason this
document exists.

The ordering is deliberate: measurements first, then options, then the ideas that were considered and
discarded — with reasons, so they are not proposed again.

---

## 1. Verified ground truth

Established by inspection at `f91c3eb1`. Re-check before relying on any of it, since the hot path is
under active development.

| Fact | Source |
|---|---|
| `SingleTimeStepValues.values` is a **Python list** of boxed floats (`[0.0] * n`) | `hisim/component.py:168` |
| Writes go through a method frame plus an attribute deref: `set_output_value(output, v)` → `values[output.global_index] = v` | `hisim/component.py:187–189` |
| Convergence is an **interpreted loop** over **every** output at **absolute 1e-4** | `hisim/component.py:191–197` |
| Convergence is forced after >10 iterations and raises after >100 | `hisim/simulator.py:266–270` |
| Per iteration the engine also does one full-array `copy_values_from_other`, and two `clone()` per timestep | `hisim/component.py:171–179`, `hisim/simulator.py:239–241` |
| **484** `set_output_value` and **233** `get_input_value` call sites under `hisim/components/` | grep |
| `stsv.values` has exactly **one** consumer outside `component.py` | `hisim/simulator.py:460` |
| An output's `global_index` is assigned in registration order per wrapper → **each component's outputs occupy a contiguous block** | `hisim/component_wrapper.py:105` |
| An input reads `component_input.source_output.global_index` → **input reads are scattered by construction** | `hisim/component.py:181–185` |
| A real 14-component setup registers **141 outputs**, has **55 connections** drawing on **41 distinct source outputs** — i.e. ~71% of outputs are read by no component | `pr9_results/1_germany_heatpump_pv_battery_retrofit/{hisim_simulation.log,component_connections.json}` |
| All `SimRepository` **reads** in components happen in `i_prepare_simulation`, before the loop; the 7 writes inside `i_simulate` (weather, price signal, tariff provider, PV, LPG) are read by nobody during iteration → **the wiring graph is the complete intra-timestep coupling graph** | AST scan of `hisim/components/**` |
| Converting the value store to numpy was benchmarked and came out **slower**: the vectorized compare gain is smaller than the loss on hundreds of individual scalar stores | prior measurement (team) |

---

## 2. Measurement program

These gate everything in §3. All of them are small, and none requires a Rust toolchain except **M2**.

- [ ] **M0 — per-element container costs on our CPython.** Read and write cost for `list`,
  `array.array('d')`, `memoryview` format `'d'`, and numpy scalar indexing. Establishes where the
  boxing conversion is cheapest to pay. Expected ordering (to be confirmed, not assumed): list
  fastest per element, every unboxed buffer slower, numpy slowest — which is why an unboxed buffer
  handed to Python is the wrong shape.
- [ ] **M1 — call-site variants.** One representative component, four `i_simulate` bodies:
  (a) current `stsv.set_output_value(self.channel, v)`; (b) `o[self.channel.local_index] = v`;
  (c) `o[self.IDX_X] = v` with a class-level int; (d) `o[2] = v` with the index hoisted to a local.
  Decides whether rewriting 717 call sites earns its churn, and which spelling to rewrite them to.
- [ ] **M2 — PyO3 boundary stub.** One extension call that gathers 10 scattered f64 into a Python
  list and scatters 7 back. Yields the real crossing cost and the real per-float boxing cost on our
  platform, which every estimate in §3 depends on. This is also the cheapest available probe of the
  Rust build and wheel story.
- [ ] **M3 — loop profile.** Per-iteration time split into component physics versus value plumbing
  (writes + reads + compare + copies), on two or three representative setups. Without this, every
  speedup figure below is unanchored.
- [ ] **M4 — residual argmax.** Per timestep, log which output index fails
  `is_close_enough_to_previous` last. Answers two questions at once: whether the outputs driving the
  iteration count are wired signals or write-only diagnostics, and whether any output's magnitude
  makes the **absolute** 1e-4 threshold unsatisfiable (a counter at 1e9 needs ~1e-13 relative, so it
  would fail on every iteration and silently force the 10-iteration cutoff on every timestep).
- [ ] **M5 — iteration histogram and memory high-water.** Distribution of iterations per timestep,
  and the peak size of `all_result_lines`. A year at 1 min × 141 outputs is ~593 MB as raw f64 but
  roughly 2–3 GB as a Python list of lists of boxed floats; at 500 outputs it is ~2 GB versus
  ~10 GB. Given the 16 GB runner ceiling tracked by `ci-usage`, this may be worth more than any CPU
  figure here.
- [ ] **M6 — state-leak test (correctness, not performance).** Run a timestep, then force one extra
  iteration, and assert the outputs are identical. This checks the premise that `i_restore_state`
  undoes everything `i_simulate` mutates. If it fails anywhere, the loop is not a fixed-point
  iteration, iteration count changes results, and several arguments in §3 and Appendix A do not
  hold. Worth having regardless of this whole programme.

---

## 3. Options register

Effect columns are **estimates to be replaced by measurements**, computed for the 141-output,
14-component setup at ~3 iterations per timestep and a minutely year. They are order-of-magnitude
claims, not commitments.

### O1 — Double-buffer swap instead of per-iteration copy (pure Python)

Alternate which of two lists the engine hands to components, instead of calling
`copy_values_from_other` each iteration. Safe because components hold no reference to `stsv` between
calls.

- **Effect:** removes ~5.6 µs/iteration, ~9 s/run. **Cost:** a few lines in `simulator.py`.
- **Gate:** none. **Status:** do it; it is free and it shrinks the delta for O2.

### O2 — Rust `SingleTimeStepValues` drop-in

A PyO3 `#[pyclass]` with the same constructor and method names, owning a `Vec<f64>`. Carries:
double buffering with an internal **swap** rather than a copy; `converged()` doing the comparison in
Rust; an optional **per-output tolerance vector**; `commit_row()` accumulating the run's results in
Rust and handing back one array at the end; `values` and `set_output_value` kept as compatibility
shims.

- **Effect:** compare ~8.5 µs → ~0.2 µs per iteration; copies → ~0; the single external `.values`
  consumer (`simulator.py:460`) becomes `commit_row()`, removing 525,600 list appends and the
  list-of-lists → DataFrame construction. Estimated ~55 s → ~19 s of plumbing per run, plus the
  memory reduction in **M5**.
- **Non-effect, important:** the write path barely improves. A **per-value** Rust setter would be
  *slower* than today — see D3.
- **Cost:** the whole Rust build burden (toolchain in CI, maturin wheels per platform × Python
  version, a second build path for contributors) for one class. The counter-argument is that this is
  the cheapest possible way to find out what that burden costs, on an artifact small enough to throw
  away.
- **Gate:** M2, M3, M5. **Status:** leading candidate for the first Rust artifact.

### O3 — Batched per-component gather/scatter over list buffers

Each component gets two preallocated Python **lists** (`_i` of length M_in, `_o` of length M_out) at
wiring time; Rust holds `Py<PyList>` handles plus the component's input source indices and its
contiguous output offset. Per component per iteration the engine gathers into `_i`, calls
`i_simulate`, and scatters `_o` into its `Vec`.

The elegance: **gather and scatter add zero crossings**, because they happen on the Rust side of the
call the engine already makes. The list is deliberately *not* a buffer view — per M0, an unboxed
buffer is slower for Python's per-element access, so the conversion is better paid once per component
in a tight Rust loop.

- **Effect:** for a 10-in/7-out component, ~2.2 µs → ~1.2 µs of plumbing, ≈2×. Beats naive
  tuple-passing (~1.3×) because the component writes into a preallocated list rather than building
  one per call.
- **Floor to be aware of:** every float crossing into or out of Python is boxed at ~25–30 ns, which
  no boundary design removes. At 20 components × ~17 ports × 3 iterations × 525,600 timesteps that is
  **~16 s/run of irreducible boxing tax** for as long as the components are Python. Only porting a
  component removes its share.
- **Cost:** rewrites all 717 call sites and requires local indices — see O6 for the spelling.
- **Gate:** M1, M2. **Status:** the natural successor to O2; do not start before M1 says the rewrite
  pays.

### O4 — Functional component signature

`def i_simulate(self, timestep, inputs: XInputs, force_convergence: bool) -> XOutputs`, with slotted
record types, the engine doing gather and scatter.

- **Why it pays three times:** fastest write path per unit of elegance; it is **literally the Rust
  trait signature**, so the boundary never has to be redesigned; and it makes each component a pure
  function of `(state, inputs)`, which is what the differential-verification plan in
  `hisim_rust_recommended_cleanup_steps.md` needs — record inputs and state, replay, compare.
- **Cost:** the largest migration on this list. It rewrites every `i_simulate` **body**, not just its
  write statements.
- **Gate:** M1, M3, and O3 in place. **Status:** the endgame shape; not a near-term step.

### O5 — Slotted output object as the write surface

`self.out.thermal_power = value`, where `self.out` is a `__slots__` dataclass instance allocated once
per component. A slot store is a C-level offset write, so **names cost nothing** — the current
~130 ns is the method frame plus the `global_index` deref, not the naming.

- **Effect:** ~130 ns → ~45 ns per write, with types visible to mypy.
- **Open question:** whether the slot object is canonical storage (readers then need wiring-resolved
  accessors) or a front for the flat array (one bulk sync per component per iteration). O3 makes the
  second cheap.
- **Gate:** M1 (variant (b)/(c) approximate it). **Status:** an alternative spelling for O3's
  component side; measure both.

### O6 — Generate the port plumbing

Ports are already declared with name, load type, unit and description. Declare once and generate the
local-index constants, the `Inputs`/`Outputs` record types, and eventually the Rust struct — behind a
byte-identity freshness gate, the pattern already used for `.scenario.json` and
`.energy_system.yaml`.

- **Why:** it is the only way to get O3's full win (`o[2] = v`, ~35 ns) while keeping the code
  readable, and it is the same generator that later emits the Rust side. Fits the
  generated-artifact-plus-gate discipline the repo already runs.
- **Gate:** M1 shows the gap between spellings (b)/(c) and (d) is worth a generator.

### O7 — Rust `process_one_timestep`

Port the run loop only: the fixed-point iteration and the value arrays. **Leave in Python:** wiring
and `connect_everything_automatically` (runs once, no payoff, needs the component registry first),
the results/pandas resample seam (`simulator.py:492–585`, tz-aware, mean-vs-sum per unit),
`SimulationParameters`, logging, result paths. The Rust engine receives an already-wired component
list.

- **Effect:** with all components still Python, the win is the compare, the copies and loop overhead
  minus added marshalling — **plausibly break-even**. Its value is that it establishes and measures
  the boundary, not that it is fast. Do not sell it internally as a speedup.
- **Mechanics that will bite:** hold the GIL across the timestep rather than re-acquiring per call;
  call `Python::check_signals()` once per timestep or Ctrl-C stops working on long runs; decide how
  `PyErr` from a component becomes a Rust error and back with the traceback intact, including the
  existing raise at 100 iterations.
- **Gate:** O2 shipped, M2, M3. **Status:** the step after O2 proves the boundary.

### O8 — Per-output tolerance vector

Replace the single absolute 1e-4 with a per-output (or per-unit) tolerance, supplied at construction.

- **Why:** it is the cheap, targeted fix for the real defect M4 is looking for — an absolute
  threshold applied across watts, joules, kelvin, euros and dimensionless ratios. Near-free inside
  O2; also implementable in Python alone.
- **Caution:** it changes iteration counts and therefore results. Needs a one-time golden re-bless
  and a stated policy, per §11/R6 of the feasibility document.
- **Gate:** M4. **Status:** ship inside O2, or standalone if O2 is deferred.

### O9 — Signal/record classification (deprioritized)

Classify outputs from the wiring graph: **signals** are read by another component and drive the
convergence test; **records** are read by nobody, are written once per timestep after convergence,
and are checked once at exit rather than every iteration. Derivable automatically — the wiring pass
already knows the connection set — so no annotation of 484 call sites is needed.

- **Why deprioritized:** O2 moves the compare from ~8.5 µs to ~0.2 µs, at which point shrinking the
  compared vector from 141 to 41 buys nothing measurable. What remains is the write-volume cut
  (~3.4× fewer hot-loop writes on the measured setup), and O8 addresses the semantic half more
  cheaply.
- **Do not implement the naive form.** Excluding records from the termination test *without* an exit
  check is unsound: see Appendix A.
- **Gate:** M4 and the post-O2 re-measurement. **Status:** parked; revive only if M4 shows records
  are driving iteration counts, or if M3 shows write volume still dominates after O2 and O3.

### O10 — Mixed tree: Rust components behind the same registry

The long-term path — Rust components as drop-in implementations swapped by name per run, with
per-component harness verification. Specified in `hisim_rust_recommended_cleanup_steps.md`; recorded
here only because O2/O3/O4 are its prerequisites and their designs should not foreclose it. Note the
direction: Python owns the process (cdylib built with maturin), and the Rust engine calls back out to
Python components — **not** a Rust binary embedding CPython (see D6).

---

## 4. Invariants any option must preserve

- Registration-order iteration, single-threaded, deterministic.
- The fixed-point semantics as they stand: force convergence after >10 iterations, raise after >100.
- `stsv.values` compatibility, or the one consumer at `simulator.py:460` migrated deliberately.
- Golden-corpus parity at declared tolerances. O8 and O9 change iteration counts and therefore
  require an explicit one-time re-bless, not a silent drift.
- The public interface is untouched: class-level port name constants, wiring by string name, and the
  `.scenario.json` / `.energy_system.yaml` wire formats. Every option here changes only how a name
  binds to storage on the hot path.
- Ctrl-C responsiveness, once any loop runs in Rust.

---

## 5. Discarded, with reasons

Recorded so they are not re-proposed.

- **D1 — numpy as the value store.** Measured slower. The vectorized compare gain (~60 ns/element →
  ~1) is outweighed by hundreds of individual scalar stores per iteration going from ~35 ns (list) to
  ~130 ns (numpy scalar assignment). The write path dominates once the compare is cheap.
- **D2 — zero-copy numpy view over a Rust-owned buffer for Python components.** Same defect as D1:
  the Python side pays the numpy per-element penalty on every access. This was proposed earlier in
  the discussion and is wrong.
- **D3 — a per-value Rust setter.** Crossing into Rust to extract a `ComponentOutput`, read its
  `global_index` through a Python attribute lookup, and store one float costs more than the ~130 ns
  Python frame it replaces. Batch or do not cross.
- **D4 — moving derived or summed outputs into postprocessing.** Withdrawn. A running sum is *state*
  with a recurrence, not a readout; reconstructing it downstream is valid only for an unconditional
  cumulative sum of a fully recorded quantity — any clamp, reset, saturation or branch makes the
  reconstruction silently wrong. Even where valid it changes summation order and breaks golden
  parity. And anything read inside the loop is a signal by construction, so it was never a
  candidate. The narrow surviving case (pointwise same-timestep functions of recorded outputs) buys
  a multiply and risks silent wrongness.
- **D5 — excluding records from convergence with no exit check.** Unsound; see Appendix A.
- **D6 — a Rust binary embedding CPython** (`pyo3` with `auto-initialize`). Buys nothing during
  transition and costs interpreter discovery, venv/site-packages resolution and a new deployment
  story. Build a cdylib with maturin instead and let Python own the process.

---

## Appendix A — Why unwired outputs may leave the termination test, and what that does not license

The provable part. Within a timestep every iteration begins with `restore_state()`, so component
state is constant across iterations and `timestep` is fixed; each component is therefore a map from
its inputs to its outputs. An input can only bind to a `ComponentOutput`, and the one plausible
bypass — the `SimRepository` side channel — was checked: all component reads of it happen in
`i_prepare_simulation`, before the loop, and the seven writes inside `i_simulate` are read by nobody
during iteration. So the wiring graph is the complete intra-timestep coupling graph.

Partition the outputs into wired `S` and unwired `R`. The iteration is then

```
S_{k+1} = g(S_k)
R_{k+1} = h(S_k)
```

and `R` appears on no right-hand side. It follows rigorously that **`R` cannot influence whether the
iteration converges or where the fixed point lies** — including `R` in the termination test can only
add iterations or manufacture a spurious failure. That is the entire legitimate argument, and it
concerns the termination criterion only.

What does **not** follow is that a tolerance on `S` bounds the error in `R`. At exit
`R = h(S_k)` while the converged value is `h(S*)`, so the error is bounded by
`L_h · ‖S_k − S*‖`: a record with high sensitivity — a difference of two large near-equal signals, a
division by something small, a value near a mode threshold — can be badly or qualitatively wrong
while `S` looks settled to 1e-4. Today such a record is *loud*, because it holds the loop open;
dropping it from the test makes it silent. Hence O9's exit check, and hence O8 being the better first
move.

Three premises the argument rests on, each worth verifying rather than assuming:

- **`force_convergence` makes `g` non-stationary** — it changes at iteration 11. The
  `R`-does-not-feed-back argument survives, but any exit check must use the same `force_convergence`
  value as the accepted iterate or it compares two different maps.
- **State constancy assumes `i_restore_state` undoes everything `i_simulate` mutates.** That is
  measurement **M6**, and if it fails the loop is not a fixed-point iteration at all.
- **Classification is per-setup, not per-component.** An output unwired in one setup is wired in
  another, so the criterion becomes setup-dependent and must be recorded in the run artifacts for
  reproducibility.
