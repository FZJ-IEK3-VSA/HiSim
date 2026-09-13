# AGENTS.md

## What HiSim is

ETHOS.HiSim (Household Infrastructure and Building Simulator) is a Python package for time-step simulation of household energy systems — it models electricity consumption, heating demand, PV generation, heat pumps, batteries, EVs, hydrogen systems, and more. A simulation is a set of components wired together, iterated over every time step until each step's outputs converge, followed by post-processing (KPIs, plots, CSV/JSON/PDF reports). The top-level layout is: `hisim/` (the package — simulator core, the `Component` model, `components/`, `postprocessing/`, input data under `hisim/inputs/`); `system_setups/` (one `setup_function(sim, sim_params)` per `.py` file — the imperative input — plus the shared `.simulation.json` parameter files); `energy_systems/` (the declarative input: `*.energy_system.yaml`, one recorded twin per setup, plus `*.simulation.yaml`); `tests/` (pytest suite, organized by marker); and `hisim/hisim_main.py` (the CLI entrypoint that runs either).

## Guidelines for coding
- All result files should always end up in the results directory directly underneath the repository directory. If that directory doesn't exist it needs to be created.
- No test should leave any calculation artefact in any other directory
- When components expose string constants for field names, connection names, output names, or class-derived identifiers, reference those constants instead of duplicating the literal strings. This keeps connections safe when the constants are renamed or adjusted.
- Avoid monkeypatching whenever practical, especially in shared fixtures. Prefer explicit configuration, constructor arguments, or small test helpers; use monkeypatching only when the alternative would be substantially more fragile or invasive.

## Pull requests and merging

- Never add an entry to `HISTORY.rst` in a pull request. The history is assembled at release; `CONTRIBUTING.rst` says the same.
- Every pull request gets exactly one CI run per version of its head, and the owner merges by hand at roughly one PR per half hour. The cost to avoid is fan-out: a merge that triggers a rebase and a rerun on every other open PR.
- Independent work goes on a sibling branch off `main`, never stacked. Stack only on a real dependency — the child's code needs the parent's.
- A stacked PR targets its parent branch, so it gets no CI until its turn: the workflows run only for pull requests against `main`.
- After a merge, touch nothing else. No rebase, no push of other open PRs, unless GitHub reports an actual conflict on one. A PR whose CI ran against a slightly older `main` is still mergeable.
- When a parent merges, handle only the next PR in the stack: rebase it onto main (`git rebase --onto origin/main <old parent head> <branch>`), push once, let its one CI run finish, merge. The rebase at its turn is not optional — `main` receives the parent as a squash, and a child cut before the parent's late fix commits would silently revert them on merge.
- Delete the merged PR's head branch from the merge screen's own button, immediately. GitHub then retargets the child PR to `main`. Deleting the branch later, from the branches page, closes the child PR instead — it cannot be reopened, and a new PR has to be created.
- Review fixes land as new commits on the PR branch, never by amending a pushed commit. Fix commits on a parent are what make the rebase above necessary for its children.
- Test only what changed: the targeted pytest files, and the CI lint invocations on the changed files. Never the full suite in a fix loop.

## Test commands

- **Base set (use this by default):**
  ```bash
  pytest -m base -n 32 -q
  ```
  Fast, no network, no external credentials. This is the set to run when validating a change.

- **Full suite (humans only — slow, networked, can take a very long time):**
  ```bash
  pytest
  ```
  Do not run the full suite as an agent unless explicitly asked; it pulls in the slow and networked markers below.

## Test markers

Markers are defined in `pytest.ini`:

- `base` — unit-scale tests that must pass in any case: no full simulation of a system setup, a few seconds at most. **Run these.**
- `extendedbase`, `extendedbase2` — the full-simulation tests, hand-balanced across two shards of roughly equal wall time. Slow. Avoid.
- `buildingtest` — systematic sweep of the building component. Slow. Avoid.
- `system_setups` — runs full system setups end to end. Slow. Avoid.
- `mpc` — model-predictive-control system tests. Slow. Avoid.
- `utsp` — exercises the LPG/UTSP connector. **Networked + requires credentials.** Avoid.

Deselect the slow/networked ones explicitly when running a broader-than-base subset, e.g.:
```bash
pytest -m "not extendedbase and not extendedbase2 and not buildingtest and not system_setups and not mpc and not utsp" -n 32 -q
```

## Do NOT touch

- **Large reference result files** — anything under `system_setups/results/` and any committed reference CSVs used for regression comparison. Don't regenerate, reformat, or "clean up" these; tests compare against them.
- **Bulk input data** under `hisim/inputs/` (weather `NSRDB_15min`/`dwd_*`, `photovoltaic/data_processed`, housing tabula CSVs, load profiles). These are reference datasets, not editable source.
- **The UTSP / LPG connector config** (`hisim/components/loadprofilegenerator_utsp_connector.py` and its config) — it talks to an external service.
- **`obsolete/`** — frozen text. Never edit it and never import from it, not even to fix a call to a symbol that has been deleted.
- **Anything requiring external credentials** — `UTSP_URL` / `UTSP_API_KEY` (set via a `.env` file or env vars). Don't hardcode, commit, or invent these, and don't write code/tests that depend on a live UTSP service running.

## Run a single simulation quickly (for reproduction)

From the `system_setups/` directory, run a legacy Python setup directly through the documented entrypoint:
```bash
cd system_setups
python ../hisim/hisim_main.py simple_system_setup_one.py
```

Or the declarative equivalent (energy system + simulation-parameters pair):
```bash
python hisim/hisim_main.py energy_systems/simple_system_setup_one.energy_system.yaml \
    energy_systems/one_day_15min.simulation.yaml
```

Results land in a `results/` subdirectory next to the input file. Use `simple_system_setup_one.py` for the cheapest smoke test.


