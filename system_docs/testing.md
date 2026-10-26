# Testing HiSim

How HiSim is tested: which marker a test carries, how long a `base` test may take, what a test must prove, how
fixtures and configuration are written, and what to run before a change counts as done. It applies to people and
agents alike, and reviews check it. Code style, including the docstrings of tests, is `system_docs/coding_style.md`.

## 1. What a change must pass

Before a change counts as done, on the files it touches:

1. **The checks of `system_docs/coding_style.md` §8**: flake8, pylint with `pylintrc-critical-only`, the three
   mypy runs and `semgrep --config .semgrep/ --error hisim/`.
2. **The targeted tests**: the test files of the changed modules, plus every test file that imports them
   (`grep -rl <module> tests/`), with `pytest <files> -n 8 -q`. Once before pushing, the `base` set:
   `pytest -m base -n 32 -q`, and `pytest -m renovisor_probes -q` when the change touches `hisim/renovisor/`.
3. **The golden gate**, when the change can move simulation results: `python scripts/golden_check.py` on the
   affected pairs (`--pairs setup:param`), at both step lengths when the change touches code that acts only above
   600 s, such as the part-load rules or a start-of-step node (`household_gas_building_sizer:one_week_900s`; the
   parameter sets are listed in `golden_references/README.md`). A golden reference is blessed only through the CI
   workflow `golden-update.yml`; `scripts/golden_update.py` may run locally to inspect what a bless would write, and
   what it writes locally is never committed. A change that moves results says so in its PR description, with the KPI
   deltas (`system_docs/results_and_interfaces.md`).

The full suite (`pytest` without `-m`) is for CI. Locally, the slow markers run only for the test files a change
touches.

## 2. Markers: which one a test carries

Every marker is declared in `pytest.ini`, and `--strict-markers` refuses an undeclared one. Each job of
`.github/workflows/tests.yml`, the CI of every pull request, runs one job marker, except the catch-all
`pytest (others)`, which runs every test that carries none of the other jobs' markers. So the marker decides where
and how often a test runs. `.gitlab-ci.yml` runs the same markers on demand only, one job per marker and none
split, and has no `renovisor_probes` job: its `pytest-others` takes those tests.

| Marker | What belongs there | CI job |
|---|---|---|
| `base` | Unit-scale tests: a pure function, a component stepped by hand, a config check, a file format. No full simulation of a system setup; a few seconds at most (§3). | `pytest (base)`, 4 workers |
| `extendedbase`, `extendedbase2` | Tests that run a full simulation: a setup, an energy-system file, a twin. `extendedbase` runs two processes per job, each under 8 GB; `extendedbase2` holds the tests of which one needs most of a 16 GB runner, and runs them serially. | `pytest (extendedbase-1)` to `-3`, `pytest (extendedbase2-1)` and `-2` |
| `system_setups` | Tests that run one of the Python setup files under `system_setups/` end to end. Nothing else. | `pytest (system-setups)` |
| `buildingtest` | The systematic sweeps of the building component. | `pytest (buildingtest)` |
| `assemblies` | Every test under `tests/assemblies/` except the nightly tier. `tests/assemblies/conftest.py` adds it and refuses `base`, `extendedbase` and `extendedbase2` there; never written by hand. Needs the local LoadProfileGenerator. | `pytest (assemblies)` |
| `nightly` | The Latin hypercube tier of the assembly test contracts. | `assemblies nightly tier` in `assemblies-nightly.yml`, daily at 02:00 UTC |
| `renovisor_probes` | The RenoVisor tests that read the capability document or the translation map rendered from it (the session fixtures of `tests/renovisor/conftest.py`, which build the whole probe set). `tests/renovisor/conftest.py` refuses such a test without this marker or with `base`. | `pytest (renovisor-probes)`, one process |
| `postprocessingoptions` | One named test per `PostProcessingOptions` value. | `pytest (postprocessingoptions)` |
| `utsp` | Tests that need the LPG/UTSP connector. Networked, needs credentials. | `pytest (utsp)` |
| `mpc` | Model-predictive-control system tests. | `pytest (others)`; no test carries it today |
| `hpcharness` | The HPC-harness tests. | `pytest (others)` |
| `upstream_history` | Tests that need the unsquashed upstream history of `golden_references/`; carried instead of `base`. | `pytest (others)`, whose checkout fetches the full history |

Two markers select a group inside a job and are carried beside a job marker: `worked_examples` (the cost
module's hand-computed examples, inside `base`) and `xdist_group` (pytest-xdist's grouping).

Rules:

- **A test carries exactly one job marker of the table.** A test with none runs only in the catch-all
  `pytest (others)` job and is missed by `pytest -m base`; a test with two can run in two jobs. A hook enforces it:
  `tests/job_markers.py`, registered by `tests/conftest.py`, fails the collection of every run, `-m base`
  included, naming each test that carries none or more than one, after `tests/assemblies/conftest.py` has added
  its marker. Its list `JobMarkers.NAMES` is this table; `tests/test_job_markers.py` keeps the two equal, keeps
  every marker of `pytest.ini` a job marker or one of the two group markers (`JobMarkers.GROUP_NAMES`), and keeps
  the `marker:` of each `tests.yml` job, and the markers `pytest (others)` excludes, in step with the table's CI
  job column. So a new job marker goes into the table, `pytest.ini`, that list and `tests.yml` together.
  Under pytest-xdist (`-n`, which most `tests.yml` jobs use) a collection guard's refusal loses its message: the
  worker that raised it dies, and the run ends with `INTERNALERROR> RuntimeError: Unexpectedly no active workers
  available` (exit 3) without naming a test. This holds for this hook and for the assemblies and probe guards
  (`AssemblyTier`, `ProbeTier`). That error during collection means a guard refused; run the same selection
  without `-n` to see which tests and why (owner decision 2026-10-16, hisim-x7dq).
- **`system_setups` is only for the Python setup files.** A test that runs an energy-system file, a twin or a rig
  of a few components is `extendedbase` or `extendedbase2`. Some older modules carry `system_setups` for other
  runs (for example `tests/test_water_storage_temperature_check.py`, which runs an energy-system file, and
  `tests/test_example_component.py`, which steps one component by hand); they move when next touched.
- **Memory decides between `extendedbase` and `extendedbase2`, not wall time.** A test that runs beside a second
  process in under 8 GB is `extendedbase`; one that needs most of a 16 GB runner is `extendedbase2` (the comments
  of the matrix in `tests.yml` give each job's peak). CI splits each marker over its jobs by the committed weights
  of `tests/ci_durations.json` (`tests/ci_split.py`, `--ci-group`), so a new test needs no balancing by hand: a
  module the weights do not name yet gets their default until they are refreshed from the jobs' uploads.

## 3. The runtime budget of `base`

`base` is the edit loop: about 9,200 tests in about 250 modules (October 2026) that every agent and contributor
runs many times a day.

- **One `base` test takes a few seconds at most** (`pytest.ini`). Anything longer, and anything that runs a full
  simulation, is `extendedbase` or `extendedbase2`.
- **Expensive setup is built once.** A fixture that builds something costly -- a translated energy system, a
  capability document, a simulated week -- has `scope="module"` or `scope="session"` and is not rebuilt per test.
  Bead hisim-ci-speed-60sv.1 found the RenoVisor capability document built six times per `base` run, about 100 s each.
- **Measure before adding.** `pytest <file> --durations=10` shows the slowest tests of a module; CI prints the
  slowest of every job, 20 by default and 40 in `pytest (postprocessingoptions)` (`durations` in `tests.yml`). A
  new test that shows up there is moved or made faster in the same pull request.
- **Simulate the shortest period that shows the effect**: a day or a week, not a year, unless the test is about the
  year.

## 4. What a test must prove

- **Every bug fix comes with a regression test that fails without the fix.** Write the test first, see it fail on
  the unfixed code, then fix. Before pushing, check once more: revert the fix, keep the test, run the test, see it
  fail, restore the fix. Revert every file of the fix, also those outside `hisim/` (an energy-system file, a setup,
  input data), and make sure the revert took effect. An uncommitted fix is stashed with
  `git stash push --include-untracked -- <its files>`, which must report that it saved them, and comes back with
  `git stash pop`. A committed one is reverted with `git checkout <the commit before it> -- <its files>`, deleting
  a file it added, and comes back with `git checkout HEAD -- <its files>`. A test that passes on the unfixed code
  proves nothing; the review of #880 found such a test.
- **A test asserts the behaviour, not that the code ran.** Compare with an expected value obtained independently
  -- by hand, from a physical law, from a datasheet -- not with the value the code under test produced. "The run
  finished" and "the output is not `None`" check the wiring, not the result.
- **A test can fail.** No assertion inside a loop over a collection that may be empty, no `try`/`except` around the
  assertion, no tolerance so wide that any plausible value passes. Where a loop is unavoidable, assert first that
  it ran (`assert len(rows) == 8760`).
- **Tolerances are stated and justified.** `pytest.approx` gets an explicit `rel` or `abs`, and a comment says why
  that tolerance: an exact integration gets `1e-9`; a coarse-step model compared with the 60 s reference gets 1 %
  unless its spec states and justifies another (`system_docs/modelling_guide.md` §3).
- **A test's docstring names the failure it catches** (`coding_style.md` §6): what would be wrong in the code if
  this test failed.
- **Pure functions are tested directly**, edge cases and invalid input included, without building a simulation;
  system tests only check the wiring (`coding_style.md` §1 and §2, rule 8). A controller's state machine gets one
  test per transition (`coding_style.md` §3, "Testing state machines").
- **Tests are deterministic and independent** of each other and of their order: seeded random numbers, no network
  in `base`, no reliance on another test's files or cache entries. CI runs them under pytest-xdist with
  `--dist loadfile`, or `loadgroup` where a job says so: the tests of one module share a worker (under
  `loadgroup`, a test that declares an `xdist_group` goes with its group instead), the modules run in any order.

## 5. Fixtures, configuration and files

- **Explicit configuration over monkeypatching.** Configure the code under test through its config, its
  constructor arguments or a small test helper. Monkeypatching is the last resort, used only when the alternative
  would be substantially more fragile or invasive, and never in a shared fixture. Why: a monkeypatch tests code
  that does not run in production, and a patch in a shared fixture changes every test that uses it. Example: a
  test that needs a short run builds `SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)`
  instead of patching the time range. A test that must set an environment variable uses `monkeypatch.setenv`,
  which pytest undoes after the test.
- **Shared helpers live in `tests/`**: `tests/functions_for_testing.py` (configs, sizing contexts),
  `tests/_helpers.py` (the system-setup smoke contract), the rigs `tests/part_load_rigs.py` and
  `tests/idealized_heater_rig.py`. Search them before writing a new helper (`coding_style.md` §1, DRY).
- **Files go to `tmp_path` or the result directory.** The autouse fixture `guard_against_stray_files` in
  `tests/conftest.py` fails a module that leaves a file git would track anywhere else. The `HISIM_CACHE_*`
  variables are cleared for every test by `_isolate_cache_environment`; a test that needs one sets it itself.
- **A stale input cache fakes failures.** `hisim/inputs/cache` may hold entries written by another branch. When a
  test fails for a reason the change does not explain, rerun it in a clean worktree before calling the failure
  pre-existing.

## 6. Test only what changed

- **In a fix loop, run the targeted test files**, not the full suite and not `base` after every edit. The `base`
  set runs once before pushing (§1).
- **The full suite, the slow markers and whole simulations are not run "to be safe".** They take long and need
  much memory; CI runs them for every pull request against `main`.
- **A pull request's Result section lists the tests that ran** (`coding_style.md` §10), so a reviewer knows what
  did not run.
