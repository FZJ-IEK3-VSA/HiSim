# Why the LPG runs are flaky, and what to do about it

*(The last section widens to every cached component: the worst fault here is a caching pattern, not a pylpg quirk.)*

**Status:** findings, for review · **Date:** 2026-08-31
**Scope:** `hisim/components/loadprofilegenerator_utsp_connector.py` and the `pylpg` package it drives — and, from §7 onward, every component that caches. Half the faults here turned out not to be about load profiles at all.
§13 widens to all six components that cache, because the worst fault is a caching pattern rather than a pylpg quirk.
**Why now:** a golden check that fails on one box and passes in CI, traced to several independent defects.

**Relationship to PR #584 (`cache_service`), read 2026-08-31 — important for anyone comparing the two.**
That specification predates this document (2026-08-22) and **already contains Faults E and F**, including
the solar-thermal feedback case in nearly these words. This document did not discover them; it re-derived
them, and what it adds is evidence that they are live: Fault E is the confirmed cause of a
`scenario-json-freshness` failure in CI on 2026-08-31, and §13's per-component table checks #584's survey
against the code as it stands today.

Faults **A to D** — the load-profile fallback chain, the shared `pylpg` working directory, cleanup only on
success, and the cache filed under a mode that did not run — are **not** in #584 and are this document's own.
The split matters for ownership: E and F belong to the cache service and should be fixed inside
`hisim/caching/` as that specification lays out, while A to D are local to the load-profile connector.

---

## 1. The short version

Two separate faults, often mistaken for one — and, since this was written, a third group behind the second
(§4a, fixes F10–F13): the generator executes one shared binary next to one shared database, does not report
when it dies, and races itself during its own installation.


1. **Any exception in the profile path silently swaps the household.** A transient error — sqlite, IO,
   timeout, a missing `.env` — makes the connector rewrite its own `data_acquisition_mode` and continue with
   the shipped `CHR01 Couple both at Work` profile. The simulation finishes, exits zero, and produces a full
   set of plausible KPIs **for a household nobody asked for**. This needs no concurrency and no shared state;
   it explains a golden-year job failing for a single household in an isolated runner, and passing on re-run.
2. **All local LPG runs share one working directory inside `site-packages`.** `pylpg` derives it from the
   calculation index alone, HiSim defaults that index to `1`, so every process in one virtual environment
   uses `C1`. Two at once corrupt each other; one that dies leaves the directory dirty and the next fails
   before it starts. This explains local flakiness and parallel runs; it explains nothing in CI.

3. **The cache is filed under a name the run did not use.** The key is taken from the *requested*
   configuration before the request; the contents come from whatever actually ran. A downgraded run is
   therefore filed under the name of the mode it failed to use, and every later run with that configuration
   gets a cache hit and the wrong household — with no warning at all, because the fallback is never entered.
   **A transient error becomes permanent and silent.**

4. **Cache entries are written straight to their final path, never atomically.** Two runs computing the same
   key race: one begins writing, the other sees a file that exists, reads it half-written, and either fails to
   parse it or silently gets truncated data. This is what broke `default_connections` in CI while its
   near-twin succeeded beside it, and it affects every cached component (§7).

5. **Cache keys name their owner, not the value's inputs.** Four of the six components cache something
   derived from a neighbour — PV and the building from the weather, the car from the household — under a key
   that cannot tell one neighbour from another. And the solar thermal collector caches a series that depends
   on the storage temperature it is wired to, which no key can ever capture, because that value does not
   exist until the run produces it (§8).

The third is the worst, and it is a *caching* fault rather than a load-profile one, so §13 widens the
verification to all six components that cache.

**The decided fix for the first is deletion, not containment** (§9 F1): a run whose configured profile source
is unavailable fails with an error saying how to configure it differently. That removes the third by
construction, since a mode that is never rewritten cannot diverge from the key taken before it. It does
**not** remove the second, and it raises its priority — see F3.

## 2. What was observed

- `scripts/golden_check.py --setup household_heatpump_building_sizer --param one_week_60s` fails on the
  development box with **67 diverged KPIs**, while the **same commit passes in CI** (`golden-check.yml` on
  `main` at `79020a2a`, success). The failure reproduces on `main` itself, so it belongs to no feature branch.
- The divergences are not noise. `Residents' electricity consumption from grid` moves **35 %**, warm water
  **6 %**, theoretical heating demand **0.5 %**. Every value is physically plausible.
- The run log says why, in a warning nobody reads:

  ```
  WRN: Error while LpgDataAcquisitionMode.USE_LOCAL_LPG request:
       [Errno 39] Directory not empty: '…/site-packages/pylpg/C1/results'
  WRN: LPG data acquisition mode will be set to: USE_PREDEFINED_PROFILE!
  WRN: LPG result folder was None; cleanup skipped.
  ```
- Independently reported by the maintainer: pylpg "randomly produces sqlite errors", and **re-running the
  same test usually works**.
- `site-packages/pylpg/C1/results/` currently holds `Results.HH1.sqlite`, `…-shm` and `…-wal`. The WAL
  sidecars mean a writer exited without checkpointing.

## 3. Fault A — the silent downgrade

### What the code does

`loadprofilegenerator_utsp_connector.py`, four sites rewrite the component's own configured mode:

| Line | From → to | Trigger |
|---|---|---|
| `:891` | `USE_UTSP` → `USE_LOCAL_LPG` | `.env` has no `UTSP_URL` / `UTSP_API_KEY` |
| `:904` | `USE_LOCAL_LPG` → `USE_PREDEFINED_PROFILE` | a `try:` whose body is `pass` and a `# todo` |
| `:1086` | `USE_UTSP` → `USE_LOCAL_LPG` | **any** exception |
| `:1089` | `USE_LOCAL_LPG` → `USE_PREDEFINED_PROFILE` | **any** exception |

The last two sit in `except Exception as e:` inside a two-attempt loop (`:908-911`). The handler logs a
warning, changes the mode, resets `attempt = 1`, and loops. When the chain reaches the predefined profile it
succeeds, because reading a shipped CSV nearly always succeeds.

### Why it produces wrong results rather than errors

The predefined profile is **a different household**. Occupancy drives electricity demand, warm-water draw and
internal heat gains, so every downstream number moves: heat pump duty, storage losses, battery cycling, grid
import, costs, emissions. Nothing is missing and nothing is malformed, so no check downstream can tell.
The only signal is a `WRN` line in a log nobody diffs.

This is the same failure shape as two defects found during P3 — a meter fed twice, and an energy manager
pairing a battery with the wrong control signal. In each case the system kept running and produced numbers
that looked right. A silent fallback is a machine for manufacturing exactly that.

### Why it explains the CI symptom

It needs no shared directory, no concurrency and no second process. One transient error inside one isolated
matrix job is enough:

- *fails sometimes* — whenever a transient error occurs anywhere in the LPG path;
- *passes on re-run* — the transient error does not recur;
- *one household is enough* — one exception is enough;
- *shows up as a KPI mismatch, not a crash* — because the run genuinely completed.

### A second consequence, already known

The component **mutates its own configuration** at run time. The P4 survey recorded this separately as a
recording defect: a realized record written after such a run states `USE_PREDEFINED_PROFILE` even though the
author asked for `USE_LOCAL_LPG`. The same line is therefore both a reproducibility bug and a flakiness bug.

### The committed twins were audited, and they are clean

`data_acquisition_mode` is already an ordinary configuration field (`:71`), so every recorded energy-system
file states the mode it ran with — which makes the defect above **auditable after the fact**. Checked across
all twins in `energy_systems/` on 2026-08-31:

| Twins | Setup asks for | Twin records | Verdict |
|---|---|---|---|
| 11 | `USE_LOCAL_LPG` (set explicitly by the sizer setups) | `USE_LOCAL_LPG` | honest |
| 6 | factory default `USE_PREDEFINED_PROFILE` | `USE_PREDEFINED_PROFILE` | honest |

**No twin captured a downgrade**, so none needs re-recording when F1 lands, and local LPG evidently worked
throughout the recording runs.

This also gives a cheap standing check, worth keeping once F1 makes it rare: *a twin recording
`USE_PREDEFINED_PROFILE` for a setup whose source requests `USE_LOCAL_LPG` is a downgrade frozen into a
committed file.* One grep over `energy_systems/` against `system_setups/` finds it, and the freshness job is
the natural place for it.

### Setting the mode from a file needs no new work

For the avoidance of doubt, since F1 removes the automatic path and leaves configuration as the only route:
the field is already settable from an energy-system file today, and round-trips as an enum by name —

```yaml
config:
  data_acquisition_mode: USE_PREDEFINED_PROFILE
```

The `for_household` constructor takes it as an argument too, though that route is blocked for a different
reason: `configure.py::_call_builder` passes constructor arguments without running them through the codec, so
a file-supplied enum arrives as a bare string. That is P4 gate R2.3 and is out of scope here; the `config:`
route is unaffected.

## 4. Fault B — one working directory for every process

`pylpg/lpg_execution.py` builds its working directory from the calculation index alone (`"C" + str(index)`,
`:195`, `:259`), and that directory lives **inside the installed package**. HiSim picks the index at
`loadprofilegenerator_utsp_connector.py:339`:

```python
self.calculation_index_for_local_lpg = int(os.environ.get("HISIM_LOCAL_LPG_CALC_INDEX", "1"))
```

The environment variable exists precisely so parallel runs can be separated — the comment above it says so —
but **the default is a constant**, and nothing in the test suite, the scripts or the workflows sets it. So
every concurrent run in one virtual environment shares `C1`:

- both write `C1/results/Results.HH1.sqlite`, in WAL mode → "database is locked", "disk I/O error", and the
  other sqlite failures reported;  *(**partly wrong** — see §4a: the sqlite contention outlived this fix, and
  the database it was really about is a different one)*
- the cleanup at `:1097-1116` does `shutil.rmtree(os.path.dirname(result_folder))`, so a finishing run
  **deletes the directory a running one is still using**;
- whichever loses the race fails, and fails differently every time.

## 4a. Fault B was only half of it *(added 2026-09-01, after F3 shipped)*

F3 gave every process its own `C<index>` directory, and the sqlite failures **continued**. A cold parallel
regeneration on a runner with four cores still lost calculations to

```
System.Data.SQLite.SQLiteException occured in CalcStarter.Start
Unhandled exception. code = Busy (5), message = SQLiteException (0x87AF00AA): database is locked
```

so the diagnosis above was incomplete. The directory was shared, and separating it was right, but it was not
the thing the calculations were fighting over.

**The generator runs from the shared directory, not from the copy made for it.** `LPGExecutor.__init__`
copies the whole of `LPG_linux` into `C<index>` — the executable, the dlls and a 51 MB
`profilegenerator.db3` — and then `lpg_simengine_filepath` returns the path in the **source** directory
anyway. Only the working directory is per-calculation; the binary every calculation executes is one file,
and .NET resolves the database beside the executable. So every concurrent calculation opened one
`LPG_linux/profilegenerator.db3`. The isolation was made and discarded in the same constructor.

**And the failure arrived disguised.** `pylpg` runs the binary with `subprocess.run`, passes no
`check=True`, and discards the return value (`lpg_execution.py:511`), so a calculation that died returns
looking exactly like one that worked. HiSim then read the results it expected and failed on whichever file
it opened first:

```
FileNotFoundError: .../pylpg/C200/results/Results/BodilyActivityLevel.High.HH1.json
```

which names neither the calculation nor the generator, and sent three separate investigations after the
wrong thing.

**The same shared directory breaks the install, too.** The binaries are not shipped with `pylpg`;
`LPGExecutor.__init__` checks whether the executable is on disk and, if not, downloads a zip and extracts it
over `LPG_linux`. Check and write are not atomic, so several processes starting together in a fresh
environment — four regenerator workers on a clean CI container — all find it missing and all extract into
the same directory. The first to finish begins executing `simengine2` while the others are still writing it,
and the kernel refuses to write a running executable:

```
OSError: [Errno 26] Text file busy: '.../pylpg/LPG_linux/simengine2'
```

*"Text" is the old Unix name for a program's code segment; the error is about the file being executed, not
about its contents.*

**What remains unexplained.** Running the copy instead of the original took a cold parallel regeneration from
two failures to one — and the survivor still died with `database is locked`. So the generator reaches a
shared database by some route other than its own directory, and that route was never found. F13 works around
it rather than fixing it, which is the honest description and should stay in the document.

## 5. Fault C — cleanup only after success

The `finally` block at `:1097` cleans up only `if result_folder is not None`. A run that fails before that
variable is set logs `LPG result folder was None; cleanup skipped` and **leaves the directory dirty**, so the
next run hits `[Errno 39] Directory not empty` before it starts. One failure therefore poisons the next.
This is why the box degraded over a session rather than failing from the beginning, and why a single
successful run repairs it — its own `rmtree` clears the debris.

## 6. Fault D — the cache is filed under a name the run did not use *(the worst one)*

Promoted from an open question once the ordering was checked. `hisim/utils.py:get_cache_file` builds the key
as `sha256(config.to_json() + simulation_parameters.get_unique_key())`, and `data_acquisition_mode` **is** a
field of that config (`:71`), so the mode is part of the key. The problem is *when* the key is taken:

```
build()  ->  get_list_of_file_exists_bools_and_cache_file_paths()   # :779  key computed HERE
                 |  hash contains the mode as REQUESTED
             ...cache miss...
             self.utsp_config.data_acquisition_mode = ...PREDEFINED  # :891 / :904 / :1086 / :1089
                 |  the mode changes AFTER the key is fixed
             self.cache_results(cache_filepath=cache_filepath, ...)  # :988 / :1056  writes to the OLD key
```

The file is **named after the mode that was asked for** and **filled with the output of the mode that
actually ran**. A run that requested `USE_LOCAL_LPG`, hit a transient error and fell back therefore writes
the predefined household's profile into the file keyed for `USE_LOCAL_LPG`.

Every later run with that configuration then takes a **cache hit** and loads the wrong household — without
entering the fallback, without the warning of §3, without any trace at all. **One transient error becomes a
permanent, silent wrong result.**

This inverts the reassuring evidence. "Re-running usually works" holds only while the cache is cold or was
never written; once a downgraded result is filed, re-running makes the wrong answer *consistent* rather than
correcting it. It also explains why this box drifted from passing golden checks to failing them within one
session, and why the failure then reproduced stably rather than intermittently.

Only this component rewrites its own configuration — the other five cache users were checked and do not — so
the key/contents divergence is unique to it. The *pattern* is not: any component whose cache key is taken
from intent while its contents come from behaviour has the same hole.

## 7. Fault E — cache writes are not atomic, so concurrent runs read half a file

Found 2026-08-31 while diagnosing a CI failure, and it is the one that explains that failure. General to
**all six** cached components, not to load profiles.

`hisim/utils.py:get_cache_file` decides whether an entry exists with `os.path.isfile(path)` and returns the
final path. Every writer then writes **straight to that path** — `to_csv(cache_filepath)` in `weather.py:873`,
`generic_pv_system.py:790`, `solar_thermal_system.py:743`, `generic_car.py:570`; `open(cache_filepath, "w")`
in `loadprofilegenerator_utsp_connector.py:2012` — and every reader reads it directly. Nothing is written to
a temporary name first, and nothing is renamed into place.

So two processes computing the same key race:

```
worker A:  exists = False  ->  computes  ->  begins writing weather_<hash>.csv
worker B:  exists = True   ->  pd.read_csv(...)  ->  reads a half-written file
```

The reader sees a file that exists and is incomplete. Depending on where the writer had got to, it raises a
parse error or — worse — parses successfully and returns truncated data.

**The observed failure.** `scripts/regenerate_scenario_jsons.py` runs setups in parallel (`-j 4` on the CI
runner). Of twenty-one setups, exactly one failed: `default_connections`, immediately after
`automatic_default_connections` succeeded. Those two are near-identical, share their weather and building
configuration, and therefore **share cache keys**. The same command run sequentially on a development box
reports `21 OK, 0 FAILED`.

Note what this is *not*. The regenerator already hands every worker its own `pylpg/C<index>` (`:117-120`), so
Fault B is excluded — that script is the one place that got the working-directory problem right. This is a
second, independent shared resource: `hisim/inputs/cache/`, which no mechanism protects.

**Consequence for the protocol of §8:** any `--jobs > 1` run is unsafe today for setups that share a key, and
weather is shared by almost everything. Parallel regeneration and parallel test runs are both exposed.

## 8. Fault F — cache keys describe their owner, not the value's inputs

Audited 2026-08-31, prompted by the owner's question: *can a component cache a result that varies between
households while filing it under one key?* Yes — four of the six do, and one of them caches something that
must not be cached at all.

`get_cache_file(component_key, parameter_class, my_simulation_parameters)` builds the key from **the
component's own configuration** plus the simulation parameters (start, end, resolution, year, timesteps,
country). It contains nothing about any other component. So any cached value derived from a *neighbour's*
output is filed under a key that cannot distinguish one neighbour from another.

| Component | Cached value | The key covers | Missing |
|---|---|---|---|
| `generic_pv_system` | a year of AC power ratios | `PVSystemConfig`, incl. a `location` **string** | the weather's `data_source` and `source_path`: one location under NSRDB / DWD / TRY, or a different file, gives different arrays under one key |
| `solar_thermal_system` | per-timestep `flat_plate_precalc` output | `coordinates`, `azimuth`, `tilt` | the whole weather — **and see below, it is worse than a narrow key** |
| `building/building` | `solar_heat_gain_through_windows` | `BuildingConfig` | the irradiance series it is computed from |
| `generic_car` | `car_location`, `meters_driven` | `CarConfig`: `source_weight`, `fuel`, `consumption_per_km`, costs | **the household.** The driving profile comes from the occupancy's data and *nothing* household-specific is in the key. Two households with identical car hardware share one entry |
| `weather` | its own series | own config, incl. `source_path` and `data_source` | — sound |
| `loadprofilegenerator_utsp_connector` | the profile | own config, incl. household and mode | — sound in principle; broken by Fault D instead |

The PV case is the mildest only by accident: setups copy one `weather_location` variable into both configs by
convention, so the strings tend to move together. That is a habit, not an invariant, and nothing enforces it.
The car case has no such accident — `CarConfig` contains nothing that varies with the household.

### The solar thermal collector must not be cached at all

`flat_plate_precalc` is called with `temp_collector_inlet=temperature_collector_inlet_deg_c`
(`solar_thermal_system.py:677`), and that value is read from a wired input:

```python
temperature_collector_inlet_deg_c = stsv.get_input_value(self.water_temperature_input_channel)   # :649
```

The inlet temperature comes from the **storage**, and collector efficiency depends on it — that is what the
loss parameters `a_1` and `a_2` are for. The component also declares a `ControlSignal` input beside it. So
the cached series is a function of the simulation's own state, not of the weather: change the storage volume,
the controller, the draw profile or the heat generator, and the cached values are wrong.

No key can fix this. The inlet temperature does not exist until the run produces it, so there is nothing to
hash. The cache-hit branch checks only that the number of values matches the number of timesteps, which the
right count of entirely wrong values passes silently.

### The rule this yields

Not "the key must contain every input" — some inputs **cannot** be keyed, because they are outputs of the
same run.

> **A precomputed series may be cached only if every input is known before the simulation starts.** A wired
> `ComponentInput` read through `get_input_value` and folded into a cached value is proof that it is not.

Measured against that rule: `generic_pv_system`, `generic_car` and `weather` make **no** `get_input_value`
calls at all, so their payloads are decided before the run and are legitimately cacheable — they need wider
keys. `building/building` makes twelve such calls, but the cached quantity uses only the six weather-derived
ones (azimuth, DNI, DHI, GHI, DNI-extra, apparent zenith) and not the feedback inputs beside them, so it is
also a key problem rather than a caching one. `solar_thermal_system` makes ten, and the cached value depends
on one of them.

### Fixes

**F7 — widen the keys of PV, building and car** `[decided 2026-08-31, owner]`

**This fix is deliberately temporary and should be deleted, not defended.** PR #584 §3 answers the problem
properly: a per-calculation DTO plus an automatic code fingerprint, which requires producers to be extracted
into standalone modules first. That extraction is its own phase and lands after the local tier
`[decided 2026-08-31]`. Between now and then, four caches can serve one household's results to another, so
the keys are widened by hand in the meantime and removed when the DTO scheme arrives. Say so in the code, or
someone will maintain it.

- **PV and building** take the weather configuration's hash. Both derive their artifact from weather series
  while keying on their own config; PV's `location` string only tends to track the weather because setups
  copy one variable into both, which is a habit and not an invariant.
- **The car takes a hash of the occupancy's configuration** `[decided 2026-08-31]`, not merely the household
  name. The profile is a function of the whole occupancy setup — household, energy intensity, travel route
  set, transportation device set — and any of those changes the driving pattern. Putting the household name
  into `CarConfig` was rejected: it duplicates state that belongs to the occupancy, and two sources of truth
  drift.
  *How it reaches the car:* the occupancy already publishes the per-car profile into the simulation
  repository, so it publishes the hash of its own configuration alongside, under the same identity. The car
  reads both — the data it consumes and the fingerprint of what produced it — which is the same shape the DTO
  scheme will formalise later, so this stopgap points in the right direction rather than away from it.
- **Solar thermal is not in this list.** F8 restricts its artifact to the solar position, whose inputs are
  already exactly its key, so it needs no widening at all.

**What this does not fix**, and must not be claimed to: a widened key still describes *inputs the component
knows about*. It cannot notice a change in code, in a library version, or in an input nobody thought to add.
Those are what #584's `code_fingerprint` and `third_party_fingerprint` are for, and they are the reason this
is a stopgap rather than a solution.

**F8 — cache the sun position, and nothing else** `[specified 2026-08-31, owner]`

`flat_plate_precalc` (oemof.thermal) computes in three stages, and the boundary falls in a convenient place:

```python
solposition = pvlib.solarposition.get_solarposition(time=data.index, latitude=lat, longitude=long)   # (1)
dni = pvlib.irradiance.dni(ghi=…, dhi=…, zenith=solposition["apparent_zenith"])                      # (2)
total_irradiation = pvlib.irradiance.get_total_irradiance(surface_tilt=…, surface_azimuth=…, …)      # (2)
eta_c = calc_eta_c_flate_plate(eta_0, a_1, a_2, temp_collector_inlet, delta_temp_n, temp_amb, …)     # (3)
data["collectors_heat"] = data["col_ira"] * eta_c
```

| Stage | Depends on | Known before the run? | Cache |
|---|---|---|---|
| (1) solar position — `apparent_zenith`, `azimuth` | latitude, longitude, timestamps | **yes** | **yes** |
| (2) plane-of-array irradiance | (1) + `ghi`, `dhi` from the weather | only if the weather is | no |
| (3) efficiency and collector heat | (2) + **`temp_collector_inlet` from the storage** | **never** | never |

**Cache stage (1) only.** Its inputs are latitude, longitude and the timestamp series, and the timestamps
follow from `start_date`, `end_date` and `seconds_per_timestep`. Every one of those is already in the key:
`coordinates` comes from `SolarThermalSystemConfig`, the rest from the simulation parameters. So this is the
one place in the audit where **restricting what is cached makes the existing key exactly correct**, with no
widening required and no upstream identity to thread through.

What that costs and buys, to be measured rather than assumed: stage (1) is the pvlib call, which is the
expensive part per timestep; stages (2) and (3) are arithmetic on scalars. The cache should therefore keep
most of its value while becoming sound. If the measurement says otherwise, removing the cache entirely is
still preferable to keeping a wrong one.

Concretely:

- Replace the per-timestep `flat_plate_precalc` call with a call whose solar-position part is looked up.
  Either precompute `get_solarposition` for the whole period in `i_prepare_simulation` and pass the row for
  the timestep into the remaining stages, or vectorise the whole of stage (1) once — the second is closer to
  what the PV system already does after its own vectorisation.
- The cached artefact becomes two columns, `apparent_zenith` and `azimuth`, indexed by timestep. It replaces
  the current dump of the full `precalc_data` frame.
- **Delete the length check** at the cache-hit branch. It currently accepts any file with the right number of
  rows, which is precisely how a wrong cache passes unnoticed; with a key that genuinely determines the
  contents, a length mismatch means corruption and should raise rather than warn.
- Stage (2) still consumes weather through wired inputs and stage (3) the storage temperature, so neither is
  cached and neither needs a key.

This also removes `solar_thermal_system` from F7's list: with only stage (1) cached, there is no upstream
weather identity to add.

**F9 — enforce the rule.** A test that fails when a value folded into a cache is derived from
`get_input_value`, so the next precompute cannot quietly acquire a feedback dependency. Without it this
audit is a snapshot, and the audit is the expensive part.

## 9. Fixes

Ordered by value. Each stands alone.

### F1 — delete the fallback chain; fail with an error that says what to do *(the important one)*

**Implemented** on `lpg_flakiness_fix`. All four downgrade sites and the retry loop around the
request are gone; an exception propagates unwrapped with the guidance below printed beside it.

`[decided 2026-08-31, owner]` **All four downgrade sites go.** An earlier draft of this document proposed
keeping the chain behind an opt-in flag; that was the weaker answer and is superseded here, because the
argument for keeping it does not survive examination.

The fallback's only benefit is saving a one-line configuration edit: `data_acquisition_mode` is already an
explicit field with three valid values, so anyone who wants the shipped profile can ask for it. Against that
benefit it converts a misconfiguration into a wrong result that passes every check the project has. An
opt-in does not repair this either — a flag set once and forgotten produces the same silent substitution
later, when nobody remembers setting it.

So: an exception in the UTSP or local-LPG path **propagates**, and the message must be worth reading. It
should name the mode that was configured, quote the underlying failure, and say what to change:

```
Occupancy profile could not be obtained in USE_UTSP mode: <underlying error>.

USE_UTSP needs UTSP_URL and UTSP_API_KEY in a .env file at the repository root.
If you do not have UTSP access, set data_acquisition_mode on UtspLpgConnectorConfig to one of
  USE_LOCAL_LPG          - generate the profile locally with pylpg (slower)
  USE_PREDEFINED_PROFILE - use a shipped profile; NOTE this is a DIFFERENT household
                           and results are not comparable with the other two modes.
```

The last clause matters: the predefined profile is not a degraded version of the requested household, it is
another household. Anyone choosing it should choose it knowing that.

Also in this fix:

- `[decided 2026-08-31]` **Raise everything, unwrapped.** No narrowing, no re-wrapping, no retry: the original
  exception and its traceback reach the caller untouched. Anticipating the failure set before observing it
  would only mistranslate it, and an `AttributeError` dressed up as advice about configuring a profile source
  is worse than the plain traceback. The guidance above is printed alongside, not instead. If a specific
  failure turns out to deserve a retry, that is added then, for that failure, on evidence.
- Delete the `:904` site outright — its `try:` body is `pass` with a `# todo`, so it can only ever have been
  a no-op that made the code look defensive.

**This removes Fault D as a side effect**, which is the strongest argument for it. The cache key is only
dishonest because the mode is rewritten after the key has been taken; if nothing rewrites it, requested and
effective are identical by construction. F2 and F5 below shrink to almost nothing as a result.

### F2 — stop the component rewriting its own configuration *(mostly delivered by F1)*

**Implemented** on `lpg_flakiness_fix`, as the check this section asks for: `tests/test_lpg_utsp_connector_no_downgrade.py`
plus assertions in the two UTSP-marked tests that used to skip themselves on a downgrade.

With the chain gone, the four assignments to `self.utsp_config.data_acquisition_mode` go with it, and the
component no longer edits the object it was configured with. That also closes the recording defect the P4
survey filed separately: a realized record written after such a run stated `USE_PREDEFINED_PROFILE` even
though its author had asked for `USE_LOCAL_LPG`.

What remains is a check rather than a feature: assert in the tests that the configured mode is the mode the
run used. No effective-versus-requested bookkeeping is needed once they cannot diverge.

### F3 — give every process its own pylpg working directory *(more urgent after F1, not less)*

**Implemented** on `lpg_flakiness_fix` in `hisim/components/pylpg_workspace.py`. The base index
defaults to the process id and a multi-household request strides by a hundred, so two base indices
cannot derive the same directory; each calculation claims its directory before pylpg is invoked.

`[decided 2026-08-31]` **Index by process, and fail hard if the directory already exists.** The temporary
directory this document first proposed is not available: `LPGExecutor.__init__` hard-codes
`self.working_directory = pathlib.Path(__file__).parent.absolute()` and derives `"C" + str(index)` beneath
it, so the index is the only isolation the library offers. Deriving it from the process removes the shared
default, and checking the directory first turns the residual collision from silent corruption into an
immediate, named failure — the right trade when the alternative is two runs interleaving in one folder.

**Two callers must change with the default, not just the default.** `scripts/regenerate_scenario_jsons.py`
already hands each worker a distinct index (`:115`) and is the one place that got this right.
`scripts/record_all_setups.py` does the opposite deliberately: it pins `LPG_INDEX = "1"` with the reasoning
that its runs are sequential, so one index serves them all, set explicitly *"so that a machine with a stale
setting records the same thing as a clean one"*. That reasoning is sound about determinism and wrong about
isolation — it makes the recorder safe against itself and defenceless against anything else on the machine,
which is exactly how this box was poisoned while several runs went on at once. The fix must keep the
explicitness (do not fall back to an inherited value) while making the value process-unique.

Independently reproduced 2026-08-31 by an unrelated agent on this box: concurrent runs at index 1 delete each
other's directory mid-calculation and the .NET binary dies inside `Interop.Sys.GetCwd()`; setting
`HISIM_LOCAL_LPG_CALC_INDEX` to a free value made every run deterministic. That is Fault B observed from a
second direction, and — through Fault A — a plausible cause of the sixty-seven diverged indicators of §2:
a collision raises, the fallback swaps the household, and the golden comparison reports physics.

The message must say which index collided and what it means: another run holds it, or a previous one was
killed and left it behind. With F4 in place an ordinary failure cleans up after itself, so a leftover
directory is genuinely exceptional. Disk stays bounded by concurrency rather than by the modulus, since each
directory exists only for the run that made it.

Note the interaction before reading the rest. Today a `C1` collision raises, the fallback swallows it, and the
run continues with the wrong household — wrong, but it finishes. **After F1 that same collision is a hard
failure.** Parallel local runs, and any concurrent use in CI, would begin failing outright instead of lying.
That is the correct behaviour and also a usability regression if F3 does not land with it, so **F1 and F3
should ship together.**

- Default the calculation index to something process-unique (the PID, modulo a sensible range) rather than
  to `1`; keep `HISIM_LOCAL_LPG_CALC_INDEX` as an explicit override.
- Better, if `pylpg` allows it: copy the calculation directory into a temporary location per run and point
  the executor at that, so nothing is ever written inside `site-packages`. Writing into an installed package
  is the underlying mistake; the index only rations the damage.

### F4 — clean up after failure too

**Implemented** on `lpg_flakiness_fix`. The connector records every calculation index it claimed a
directory for and the `finally` releases those, so the cleanup no longer depends on a result folder
that a failing run never produced.

Move the cleanup so it runs whether or not `result_folder` was set: resolve the directory from the
calculation index, which is known before the attempt starts. A failure must not poison the next run.

### F5 — the cache key becomes honest by construction *(delivered by F1)*

**Implemented** on `lpg_flakiness_fix`. The companion metadata lives in `hisim/caching/local.py`
beside the atomic write; the metadata lands before the data, and an entry that is missing metadata or
whose metadata disagrees with its filename is deleted on lookup and recomputed.

Fault D exists only because the mode is rewritten between the key being taken and the results being written.
F1 removes every rewrite, so requested and effective can no longer differ and the key always describes the
contents. No separate mechanism is required.

**A companion metadata file makes the cache self-describing** `[owner proposal 2026-08-31, adopted with one
change]`. Beside every `X.cache`, write `X.cache.meta` holding the raw string that was hashed. Three things
follow: an entry can be read and understood; an entry whose metadata is missing is unvalidatable and is
deleted; and when the key scheme changes, only entries whose metadata does not match the new scheme need to
go, instead of the whole directory.

**The change: write the metadata from the *effective* inputs, not the requested ones.** As first proposed it
records what was hashed, which is exactly what Fault D gets wrong — the downgraded run hashed
`USE_LOCAL_LPG` and stored a different household's data, so the metadata would agree with the filename and
the poisoned entry would look sound. Recording what actually produced the bytes makes validation a
comparison: **recompute the hash from the metadata and check it against the filename**. Agreement proves the
contents belong to the key; disagreement *is* a poisoning, found automatically instead of by clearing
everything. It is also tamper-evident: the metadata cannot be edited into agreement without recomputing the
hash.

Two constraints on the implementation:

- **The data file lands last.** `get_cache_file` decides existence by the data file, so metadata is renamed
  into place first and data second. A crash then leaves an orphan metadata file — harmless and ignorable —
  rather than a data file nobody can validate, which is the very state the deletion rule exists to clean up.
  This composes with F6: both go to temporaries and are renamed, data last.
- **It does not fix an incomplete key.** If the key omits the weather, so does the metadata, and the two
  agree happily. It makes migration systematic and auditing possible; F7 is still required.

Worth raising with #584 before the cache is shared: the hashed string is configuration JSON and contains
absolute paths (`source_path`, `cache_dir_path`). Locally that is only noise; in a shared cache the metadata
would carry machine-specific paths between environments.

With this in place the one-off purge becomes unnecessary — entries written before the fix have no metadata
and are deleted on sight by the same rule that handles every later scheme change. The **guard** of F9 still
stands beside it: a test that fails if a component rewrites its own configuration between the key being
computed and the cache being written.

### F6 — write cache entries atomically *(fixes Fault E, all six components)*

**Implemented** on `atomic_cache_writes`, in `hisim/caching/local.py`.

Write to a temporary file in the same directory and `os.replace()` it into position. `os.replace` is atomic
on POSIX and on Windows, so a reader sees either the previous entry or the complete new one and never a
partial file. The natural home is `hisim/utils.py` beside `get_cache_file` — a small `write_cache_atomically`
helper that the six writers call instead of writing to the path themselves.

Two details worth getting right. The temporary file must be in the **same directory** as the target, or the
rename crosses a filesystem and stops being atomic. And `get_cache_file`'s `exists` result stays advisory:
two processes may still both compute the same entry, which wastes work but is harmless once the write is
atomic — whereas locking to prevent it would add a failure mode of its own.

This is independent of F1 and of everything pylpg. It should ship on its own merits, because it is the only
fault here that corrupts data for components that have nothing to do with load profiles.

### F10 — install the binaries once, under a lock *(fixes the ETXTBSY, shipped in #611)*

`PylpgWorkspace.ensure_binaries_installed` — since renamed `install_binaries_if_missing` — makes the check
and the install atomic with respect to every other process, using an exclusive `flock` on a file beside the
installation. The first process installs while the others wait; by the time they look the executable is
there and `pylpg`'s own check short-circuits without writing. Concurrent *execution* is not locked and needs
no lock: many processes may run one binary, and only writing to a running one is refused.

### F11 — say so when a calculation leaves nothing *(shipped in #611)*

`PylpgWorkspace.verify_results_were_produced` runs the moment the binary returns, while the failure can
still be attributed to the calculation, and raises `LocalLpgCalculationFailedError` naming the index with
the tail of the generator's own `Log.CommandlineCalculation.txt` quoted into the message. Quoting rather
than naming the file is the point: `release` deletes it moments later, and on a runner it goes with the
workspace. That log is what finally identified the sqlite contention, after three investigations had missed
it.

### F12 — run the copy, not the original *(shipped in #611)*

Redirect `calculation_src_directory` to `calculation_directory` after the executor is constructed, so
`lpg_simengine_filepath` resolves to the copy `pylpg` already made. Each calculation then runs its own
binary beside its own database. An improvement, not a cure — see §4a.

### F13 — hold the lock for the whole calculation *(shipped in #611, and meant to be deleted)*

Since F12 did not end the contention and the remaining route was never found, the lock F10 introduced is
held for the length of a calculation and local runs serialise. Of the twenty-two system setups four run
local profiles, so eighteen keep their parallelism and four take turns — and those four were contending
with each other in any case, which is what the failures were. A cold parallel regeneration that had failed
twice, and once with F12 alone, then reported 22 OK with no sqlite error in any log.

**This is a workaround for a defect in a third-party binary, and it should be deleted rather than
maintained.** The cache service (PR #584) removes the need to generate the same profile repeatedly at all,
which is the real answer; when it lands, the serialisation goes with the need for it. The lock's docstring
says so too, so that whoever finds it does not mistake it for design.

## 10. How to verify a fix

**This protocol applies to every cached component, not only to load profiles — see §13.** It is what
distinguishes a cold computation from a cached one:

1. wipe the cache;
2. run once;
3. record the result;
4. run again;
5. compare run 1 against run 2 — they must be identical.

Add to that:

- **Fault A:** force an exception in the local-LPG path (rename the pylpg directory) and assert the run
  **fails** with the default configuration, and that with the opt-in it completes *and* reports the
  substitution.
- **Fault B:** start two runs concurrently in one virtual environment and assert both succeed with identical
  results. Today this reproduces the sqlite errors.
- **Fault C:** kill a run mid-request, then start another; the second must succeed.
- **Fault D:** cause a downgrade with a cold cache, then run again *without* the fault present. The second
  run must not silently reuse the first run's entry. Today it does.
- **Regression:** `household_heatpump_building_sizer / one_week_60s` must pass `golden_check.py` on a
  development box, not only in CI.
- **Every cached component:** the same five steps, per §11's table. PV is expected to fail today.

## 11. What not to do

- **Do not re-bless the golden references** to match a downgraded run. The references are right; the run was
  wrong. Blessing them would make the substituted household the new definition of correct.
- **Do not add retries** around the sqlite errors. Retrying hides Fault B rather than fixing it, and the
  existing two-attempt loop is what turns one transient error into a silent household swap.
- ~~Do not delete the fallback outright.~~ **Superseded 2026-08-31.** This document first argued the
  fallback was worth keeping behind an opt-in. The owner's objection is better: if UTSP is configured and
  unavailable, the useful response is an error telling you to configure it differently, not a different
  household and a warning. Wanting the shipped profile is already expressible — it is one field — so the
  fallback buys a saved keystroke at the price of results nobody can trust. It goes (F1).

## 12. Immediate mitigation

Until the fixes land, a poisoned box needs **both** directories cleared — the pylpg working directory *and*
the HiSim cache, because §6 means wrong profiles may already be filed there under the right name:

```bash
rm -rf <venv>/lib/python3.*/site-packages/pylpg/C*/results
rm -rf <repo>/hisim/inputs/cache/UTSPConnector_*.cache
```

Clearing only the first leaves the poisoned entries in place and the box keeps producing the wrong household
with no warning whatsoever.

and parallel local runs should set `HISIM_LOCAL_LPG_CALC_INDEX` to a distinct value per process.

## 13. Every component that caches, not just this one

The worst fault here (§6) is a **caching pattern**: a key taken from what the caller asked for, contents
produced by what the code actually did. Nothing about it is specific to load profiles, so the verification
protocol of §8 belongs to every cached component, and each needs its own answer to one question — *can this
cache ever be filled with something other than what its key describes?*

Six components use the shared helper `hisim.utils.get_cache_file`:

| Component | Cached | Notes |
|---|---|---|
| `loadprofilegenerator_utsp_connector.py` | occupancy profiles | Faults A–D above. The only one that rewrites its own configuration. |
| `generic_pv_system.py` | a year of AC power ratios | **Second confirmed defect — see below.** |
| `weather.py` | weather series | Feeds nearly every setup; a wrong entry here moves everything. |
| `building/building.py` | building physics | |
| `solar_thermal_system.py` | collector output | |
| `generic_car.py` | car profiles | Its data originates from the occupancy, so Fault D can reach it second-hand. |

### The PV cache does not round-trip exactly

`generic_pv_system.py` writes its cache with `database.to_csv(...)` (`:790`) and reads it back with
`pd.read_csv(...)` (`:669`) — a **text** round trip of float64 data. The P3 parity work already ran into
this: giving both sides of a comparison one cache directory let the second run read the first run's PV
output back out of the CSV, which both masked a real configuration difference **and invented a false one,
because the round trip is not bit-exact**. That was worked around there by giving each side its own empty
cache; it is not fixed.

So a cached PV run and an uncached one are not the same run. Under the §8 protocol, PV is expected to
**fail** today — which makes it the first thing to measure rather than a surprise to discover.

### What the sweep should produce

For each of the six, in the order above: run the protocol, and record whether run 1 and run 2 agree bit for
bit. Where they differ, establish which of the two is right — the cached or the computed value — because
that decides whether the fix is in the writer or the reader. Then answer, per component, whether every input
that changes the result is part of the key. A cache whose key omits a relevant input is Fault D wearing a
different hat.

## 14. Open questions

1. ~~Does the LPG cache key include the effective acquisition mode?~~ **Answered: it includes the requested
   mode, and the contents come from the effective one — promoted to Fault D, §6.**
2. ~~Do the CI runners hit Fault A through an unavailable UTSP, or through something else?~~ **Answered:
   both.** The UTSP really was unavailable — `tests/test_lpg_utsp_connector_scaling.py` called the live
   service and got `Received error code: <Response [504]>` on several runs, which after F1 fails the build
   instead of quietly swapping the household. That test now generates locally and nothing in the suite
   reaches the network. The *other* CI failures, on the scenario-JSON gate, were the shared binary and
   database of §4a, which is a different fault that looked the same from a distance.
3. ~~Should `USE_UTSP` → `USE_LOCAL_LPG` remain automatic?~~ **Answered: no.** It looks safer than the
   second hop because the household is nominally the same, but the two paths run different LPG builds and
   there is no evidence they agree bit for bit — so it is still a silent change of results. If that
   equivalence is ever demonstrated, the hop could be reconsidered on the evidence; until then it is the
   same defect wearing a friendlier face.
4. **By what route do two calculations still reach one sqlite database?** With F12 in place each runs its own
   executable beside its own `profilegenerator.db3`, and one still died with `database is locked`. Some path
   — an absolute location compiled in, a temp directory, a user-profile location — is shared and was not
   found. F13 serialises around it. The question only matters if the serialisation ever has to be lifted
   before the cache service makes it moot, so it is recorded rather than pursued.
