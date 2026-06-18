# AGENTS.md

## What HiSim is

ETHOS.HiSim (Household Infrastructure and Building Simulator) is a Python package for time-step simulation of household energy systems — it models electricity consumption, heating demand, PV generation, heat pumps, batteries, EVs, hydrogen systems, and more. A simulation is a set of components wired together, iterated over every time step until each step's outputs converge, followed by post-processing (KPIs, plots, CSV/JSON/PDF reports). The top-level layout is: `hisim/` (the package — simulator core, the `Component` model, `components/`, `postprocessing/`, input data under `hisim/inputs/`); `system_setups/` (one `setup_function(sim, sim_params)` per `.py` file, plus the equivalent `.scenario.json` + `.simulation.json` pairs); `tests/` (pytest suite, organized by marker); and `hisim/hisim_main.py` (the CLI entrypoint that runs a setup or JSON scenario).

## Guidelines for coding
- All result files should always end up in the results directory directly underneath the package directory. If that directory doesn't exist it needs to be created.
- No test should leave any calculation artefact in any other directory

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

- `base` — fast core tests that must pass in any case. **Run these.**
- `buildingtest` — systematic sweep of the building component. Slow. Avoid.
- `system_setups` — runs full system setups end to end. Slow. Avoid.
- `mpc` — model-predictive-control system tests. Slow. Avoid.
- `utsp` — exercises the LPG/UTSP connector. **Networked + requires credentials.** Avoid.
- `jsonconfig` — JSON config generation/execution tests.

Deselect the slow/networked ones explicitly when running a broader-than-base subset, e.g.:
```bash
pytest -m "not buildingtest and not system_setups and not mpc and not utsp" -n 32 -q
```

## Do NOT touch

- **Large reference result files** — anything under `system_setups/results/` and any committed reference CSVs used for regression comparison. Don't regenerate, reformat, or "clean up" these; tests compare against them.
- **Bulk input data** under `hisim/inputs/` (weather `NSRDB_15min`/`dwd_*`, `photovoltaic/data_processed`, housing tabula CSVs, load profiles). These are reference datasets, not editable source.
- **The UTSP / LPG connector config** (`hisim/components/loadprofilegenerator_utsp_connector.py` and its config) — it talks to an external service.
- **Anything requiring external credentials** — `UTSP_URL` / `UTSP_API_KEY` (set via a `.env` file or env vars). Don't hardcode, commit, or invent these, and don't write code/tests that depend on a live UTSP service running.

## Run a single simulation quickly (for reproduction)

From the `system_setups/` directory, run a legacy Python setup directly through the documented entrypoint:
```bash
cd system_setups
python ../hisim/hisim_main.py simple_system_setup_one.py
```

Or the JSON-based equivalent (scenario + simulation-parameters pair):
```bash
cd system_setups
python ../hisim/hisim_main.py basic_household.scenario.json 2021_minutely_plots.simulation.json
```

Results land in a `results/` subdirectory next to the scenario. Use `simple_system_setup_one.py` for the cheapest smoke test.
