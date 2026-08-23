# Plan: Vectorized full-year PV precomputation (Option A)

**Status: IMPLEMENTED on branch `pv_system_caching_fix` (2026-08-23).**

Measured on a full uncached minutely year (525,600 steps, default CEC config,
single convergence pass, this box):

| variant                          | PV cost (prepare + loop) |
|----------------------------------|--------------------------|
| before (scalar pvlib per step)   | 193.0 s                  |
| vectorized, brentq solver        | 60.6 s                   |
| vectorized, newton solver        | **1.85 s (104x)**        |

Outputs are identical to the last displayed digit (340.55260238113146 W at
timestep 655; 11867.412 kWh annual energy). newton vs brentq deviates by at
most 7.8e-12 W on a 10 kW system, so newton was adopted.

Finding on daylight masking (plan step 4, measured): the weather component
clips the apparent zenith, so the NaN mask skips only ~350 of 525,600 steps —
night steps are computed like day steps and yield the inverter's small negative
standby draw, which is intended physics. Masking is therefore worthless; the
solver switch (brentq -> newton) is where the speedup was.

## Problem

`PVSystem.i_simulate()` in `hisim/components/generic_pv_system.py` runs the full pvlib
chain (`get_relative_airmass` → `perez` → `aoi` → `poa_components` → temperature model →
`sapm` / `calcparams_cec` + `max_power_point(method="brentq")`) once **per timestep with
scalar inputs** whenever no cache file exists. For a minutely year that is 525,600 scalar
pvlib call chains — multiplied further by the simulator's convergence iterations, because
the component recomputes the identical result on every iteration within a timestep.
pvlib is designed to be called once with full time-series inputs; the per-call Python/
numpy dispatch overhead dominates the runtime of every uncached simulation.

Secondary defects of the current caching scheme:
- The cache CSV is only written if the run reaches the exact final timestep
  (`timestep + 1 == self.data_length`), so interrupted runs cache nothing.
- The `predictive_control` precompute branch in `i_prepare_simulation` already computes
  the full year ahead of time, but in a Python loop with one scalar pvlib call per step —
  just as slow as the runtime path.
- The "delete weather entries to save memory" block in `i_simulate` frees nothing: the
  singleton only holds references to lists that the Weather component keeps alive anyway.

## Goal

On a cache miss, compute the whole year **once, vectorized** in `i_prepare_simulation`
and write the cache immediately. `i_simulate` becomes a pure array lookup in all cases.
Numerical results must be unchanged (existing tests assert exact watt values).

## Changes

### 1. `hisim/components/weather.py` — always publish the yearly arrays

- [x] Move the `SingletonSimRepository().set_entry(key=...YEARLYFORECAST...)` block at
      the end of `i_prepare_simulation` (currently guarded by
      `if self.weather_config.predictive_control:`) out of the guard so it always runs.
      The lists already exist as instance attributes; publishing references costs no
      memory. Keep the `predictive_control` config flag itself (other behavior may still
      use it), it just no longer gates publishing.

Consumers audited: `building.py` and `controller_mpc.py` read these entries in their own
`i_prepare_simulation` only (gated on their `predictive` flags) — always-publishing is
purely additive for them.

### 2. `generic_pv_system.py` — restructure `i_prepare_simulation`

- [x] Cache hit path: unchanged (read CSV into
      `ac_power_ratios_for_all_timesteps_output`, validate length).
- [x] Cache miss path (unified — replaces both the `predictive_control` loop and the
      lazily-computed runtime path):
      1. Load module + inverter from database (unchanged).
      2. Fetch the 8 yearly arrays (dni, dni_extra, dhi, ghi, azimuth, apparent_zenith,
         temperature, wind_speed) from `SingletonSimRepository`. Keep a clear error
         message about adding Weather before PVSystem if the keys are missing.
      3. Convert to numpy arrays / pandas Series and call `simulate_cec` or
         `simulate_sandia` **once** over the full year.
      4. Store the result as `ac_power_ratios_for_all_timesteps_output` (list of floats,
         length == timesteps).
      5. Write the cache CSV **immediately** (fixes the interrupted-run gap).
- [x] Delete the now-redundant per-timestep Python loop in the `predictive_control`
      branch; the `predictive` yearly-forecast publication at the end stays and now
      works in every configuration.
- [x] Remove `self.data_length` and `self.ac_power_ratios_for_all_timesteps_data`
      (no longer needed).

### 3. `generic_pv_system.py` — simplify `i_simulate`

- [x] Remove the entire pvlib else-branch and the `hasattr(...)` check;
      `ac_power_ratios_for_all_timesteps_output` is guaranteed by `i_prepare_simulation`.
      Always: look up ratio, set the two output channels.
- [x] Keep the `predictive_control` forecast publication block (per-timestep rolling
      forecast into the dynamic sim repository) unchanged.
- [x] Remove the `timestep == 1` singleton-entry deletion block: it never freed memory
      (the singleton holds references to lists Weather keeps alive), and with Weather now
      always publishing it would only add a behavioral trap for future runtime readers.
- [x] Keep all eight `ComponentInput` channels and the Weather default connections.
      They document the dependency, keep every existing system setup / JSON scenario /
      explicit `connect_input` call working, and enforce component ordering. They are
      simply no longer read in `i_simulate`. (Possible later cleanup, separate PR.)

### 4. `generic_pv_system.py` — vectorize the simulation functions

`simulate_cec`, `simulate_sandia`, and `_calculate_irradiance` become full-year
(array-in, array-out) functions. pvlib handles Series/arrays natively in every function
used; the only scalar-specific code is the NaN handling:

- [x] `_calculate_irradiance`: `get_relative_airmass` returns NaN at night
      (zenith > 90°), which propagates through `perez` into the POA components. After
      `poa_components`, replace NaN with 0.0 (`np.nan_to_num` / `.fillna(0)`) — this is
      the vectorized equivalent of the current scalar `if math.isnan(...): return 0.0`.
- [x] `simulate_cec`: drop the scalar early-return for NaN `poa_global` (handled above).
      Run `calcparams_cec` + `max_power_point` on the full arrays. Keep
      `method="brentq"` for numerical identity with current results. Optional speedup
      (measure first): compute only on the daylight mask (`poa_global > 0`) and fill
      zeros elsewhere — halves the brentq work.
- [x] `simulate_sandia`: same chain, arrays throughout.
- [x] Inverter: `pvlib.inverter.sandia` is vectorized; replace
      `if math.isnan(...): inverter_load = 0` with `np.nan_to_num`.
- [x] Final ratio: `np.nan_to_num` instead of `math.isnan`.
- [x] Sanitize inputs before `max_power_point` — vectorized brentq can raise on NaN
      rather than propagate it, so NaNs must be eliminated upstream.

### 5. Tests

- [x] Existing regression anchors must pass unchanged (they assert exact watt values at
      timestep 655): `tests/test_generic_pv_system.py`,
      `tests/test_pv_module_selection.py`, `tests/test_pv_inverter_selection.py`.
      These validate numerical equivalence of the vectorized path.
- [x] New test: cache round-trip — after `i_prepare_simulation` on a cache miss, the
      cache file exists immediately (before any `i_simulate` call), and a second
      component instance prepared with the same config reads it and produces identical
      outputs.
- [x] Run the `mpc` / predictive-marked tests to confirm the forecast paths still work.

### 6. Verification

- [x] This box has no pvlib/pytest installed — either `pip3 install --user -e .[dev]`
      here or run the test suite on the box where the cProfile was taken.
- [x] Benchmark: time an uncached `basic_household` run before/after on the profiling
      box; expect the PV share of runtime to collapse from dominant to near-zero.

## Risks

- **Numerical drift**: vectorized pvlib should be bit-identical to scalar calls, but the
  NaN-handling rewrite changes code paths at night timesteps. The exact-value test
  assertions are the safety net; if they fail, compare old/new outputs element-wise.
- **brentq on arrays with pathological inputs** (NaN, zero irradiance) can raise instead
  of returning NaN — mitigated by sanitizing inputs first (step 4).
- **Alternative weather sources**: `Weather` in `weather.py` is the only component that
  feeds PV today; any future weather component must publish the same singleton keys.
  The error message in step 2.2 makes this requirement explicit.
- **Non-full-year simulations**: Weather builds its lists for the configured simulation
  range at the configured resolution, so lengths always match `timesteps`; the existing
  length validation on the cache-read path is kept and extended to the computed path.
