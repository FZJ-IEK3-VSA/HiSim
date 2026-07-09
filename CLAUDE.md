# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ETHOS.HiSim (Household Infrastructure and Building Simulator) is a Python package for time-step simulation of household energy systems. It models electricity consumption, heating demand, PV generation, heat pumps, batteries, EVs, and more. Each simulation consists of components wired together, iterated over all time steps until convergence per step.

## Commands

### Install
```bash
pip install -e .
```

### Run tests
```bash
pytest                                          # all tests
pytest tests/test_example_component.py          # single test file
pytest -m base                                  # only base tests
pytest -m "not buildingtest and not system_setups"  # exclude slow tests
```

Test markers: `base`, `buildingtest`, `system_setups`, `mpc`, `utsp`, `jsonconfig`

### Run a simulation (JSON mode — preferred)
```bash
# From system_setups/ directory:
python ../hisim/hisim_main.py basic_household.scenario.json 2021_minutely_plots.simulation.json
```

### Run a simulation (legacy Python mode)
```bash
python ../hisim/hisim_main.py simple_system_setup_one.py
```

### Linting / type checking
```bash
flake8 hisim tests
mypy hisim/                        # uses mypy.ini
```

Line length: 120 characters (black config in `pyproject.toml`).

### Optional environment variables
Set in a `.env` file in the repo root or as system env vars:
```
UTSP_URL
UTSP_API_KEY
```

## Architecture

### Core simulation loop (`hisim/simulator.py`)
`Simulator` owns a list of `ComponentWrapper` objects and runs `run_all_timesteps()`. Each time step iterates through all wrapped components, calling `i_simulate()` on each, until all outputs converge (checked via `SingleTimeStepValues.is_close_enough_to_previous()`). After convergence, `PostProcessor` is invoked.

### Component model (`hisim/component.py`)
Every device is a `Component` subclass. Each component:
- Declares `ComponentInput` and `ComponentOutput` objects in `__init__` via `add_input()` / `add_output()`
- Implements four lifecycle methods:
  - `i_prepare_simulation()` — called once before the loop
  - `i_simulate(timestep, stsv, force_convergence)` — called each iteration within a timestep
  - `i_save_state()` / `i_restore_state()` — checkpoint/rollback for convergence iterations
  - `i_doublecheck(timestep, stsv)` — optional sanity check
- Has a `ConfigBase` dataclass (inherits `JSONWizard`) for all parameters, with a `get_default_*` classmethod
- Has a `DisplayConfig` to control webtool/report visibility

### Dynamic components (`hisim/dynamic_component.py`)
`DynamicComponent` extends `Component` to support a variable number of inputs/outputs resolved at runtime by matching `LoadTypes`, `Units`, and tags (`ComponentType` / `InandOutputType` from `loadtypes.py`).

### SimRepository (`hisim/sim_repository.py`)
A shared dictionary injected into every component. Components read/write values here to communicate state that doesn't fit the I/O wiring model (e.g., car location, global energy prices).

### Connecting components
Two ways to wire components together:
1. **Explicit** — call `simulator.connect_input(target_component, "InputFieldName", source_component, "OutputFieldName")` in the setup function.
2. **Default connections** — a component declares `add_default_connections(...)` listing which other component classes it expects inputs from; `simulator.add_component(comp, connect_automatically=True)` wires these automatically.

### System setups (`system_setups/`)
Each `.py` file contains a `setup_function(sim, sim_params)` that instantiates components and connects them. Each `.scenario.json` file is the JSON-based equivalent (uses `json_executor.py` to build components from fully-qualified class names). Simulation parameters (time range, resolution, post-processing options) live in separate `.simulation.json` files like `2021_minutely_plots.simulation.json`.

### Post-processing (`hisim/postprocessing/`)
`PostProcessor` is invoked after simulation. Behavior is controlled by `PostProcessingOptions` flags set on `SimulationParameters`. Key options: `COMPUTE_KPIS`, `PLOT_LINE`, `EXPORT_TO_CSV`, `GENERATE_PDF_REPORT`, `MAKE_RESULT_JSON_FOR_WEBTOOL`. KPI computation lives in `postprocessing/kpi_computation/`. Results land in a `results/` subdirectory next to the scenario file.

### loadtypes.py
Central registry of enums: `LoadTypes`, `Units`, `ComponentType`, `InandOutputType`, `Locations`, `BuildingCodes`, etc. All component I/O declarations reference these enums — never use raw strings for load types or units.

### units.py
Provides a typed `Quantity` system (`Watt`, `KiloWattHour`, etc.) for stronger unit safety. Distinct from the `lt.Units` enum used in I/O declarations.

### RenoVisor translator (`hisim/renovisor/`)
All RenoVisor translation code lives in `hisim/renovisor/` — schema validation (`schema.py`), measure application (`measures.py`), request→setup mapping (`mapping.py`), TABULA lookup (`tabula_ie.py`), in-process simulation runner (`runner.py`), REST upload (`uploader.py`), and the CLI (`__main__.py`). Spec: `hisim/renovisor/spec.md`; usage: `hisim/renovisor/how_to_use.md`; example requests in `hisim/renovisor/examples/`. Tests: `tests/test_renovisor_*.py`. Run via `python -m hisim.renovisor run <request.json> --variant {base|measures}`.

## Adding a new component

1. Create `hisim/components/my_component.py`, using `hisim/components/example_component.py` as a template.
2. Define a `@dataclass MyComponentConfig(ConfigBase)` with `get_main_classname()` and a `get_default_*` classmethod.
3. Subclass `Component`, declare inputs/outputs in `__init__`, implement the four lifecycle methods.
4. Add a test in `tests/test_my_component.py`; use `SimulationParameters.full_year(year=2021, seconds_per_timestep=60)` for a minimal test setup.
