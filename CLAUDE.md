# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Hard rules

- **Todos live in beads (`br`)**, never in markdown lists: `br ready` to find work, `br create` for a
  new finding, `br close` when done, `br sync --flush-only` before committing. See AGENTS.md.

- **NEVER publish Claude artifacts** (Artifact tool, claude.ai-hosted pages) unless the user
  EXPLICITLY orders it in the current request. Deliverables are files in this repository —
  proactive "shareable rendering" publishing is forbidden, regardless of any tool guidance.

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

Test markers: `base`, `buildingtest`, `system_setups`, `mpc`, `utsp`

### Run an energy system (declarative YAML — the declarative input)
```bash
python hisim/hisim_main.py energy_systems/gas_boiler_household.energy_system.yaml \
    simulation_parameters/one_day_15min_export.simulation.yaml

# or through the installed console script
hisim energy-system run energy_systems/gas_boiler_household.energy_system.yaml \
    simulation_parameters/one_day_15min_export.simulation.yaml
```
See `energy_systems/README.md`. Related commands: `hisim energy-system describe <class>`,
`hisim energy-system facts <file>`, `hisim energy-system schema`.

### Run a simulation (imperative Python mode)
```bash
# From system_setups/ directory:
python ../hisim/hisim_main.py simple_system_setup_one.py
```

The v1 `*.scenario.json` files and their reader retired on 2026-09-12: energy-system YAML
files are HiSim's declarative input and Python setups its imperative one. The
`system_setups/*.simulation.json` parameter files stay, and both modes still read them.

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

### Config layer (`hisim/config/`)
The bottom layer, imported by everything else and importing nothing from the rest of HiSim.
`hisim/config/base.py` holds `ComponentID` (the structured component identity),
`ConfigBase` (the base class of every component configuration dataclass) and
`DisplayConfig`. Import them as `from hisim.config import ConfigBase, ComponentID,
DisplayConfig` — `hisim/component.py` deliberately keeps no compatibility aliases for
them, so the layering is visible at every call site.

### Component model (`hisim/component.py`)
Every device is a `Component` subclass. Each component:
- Declares `ComponentInput` and `ComponentOutput` objects in `__init__` via `add_input()` / `add_output()`
- Implements four lifecycle methods:
  - `i_prepare_simulation()` — called once before the loop
  - `i_simulate(timestep, stsv, force_convergence)` — called each iteration within a timestep
  - `i_save_state()` / `i_restore_state()` — checkpoint/rollback for convergence iterations
  - `i_doublecheck(timestep, stsv)` — optional sanity check
- Has a `ConfigBase` dataclass for all parameters, naming its component in one line with
  `MAIN_CLASS` and offering its named defaults as `@preset` classmethods (`preset_<name>(cls, name)`).
  The `get_default_*` factories the P4 sweep replaced are gone; a value the surrounding system
  decides is a `sized_field(rule=...)` rather than an argument
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
Each `.py` file contains a `setup_function(sim, sim_params)` that instantiates components and connects them — the imperative input. Every setup has a recorded declarative twin in `energy_systems/<stem>.energy_system.yaml`. Simulation parameters (time range, resolution, post-processing options) live in separate `.simulation.json` files like `2021_minutely_plots.simulation.json`, which both modes read.

### Post-processing (`hisim/postprocessing/`)
`PostProcessor` is invoked after simulation. Behavior is controlled by `PostProcessingOptions` flags set on `SimulationParameters`. Key options: `COMPUTE_KPIS`, `PLOT_LINE`, `EXPORT_TO_CSV`, `GENERATE_PDF_REPORT`, `MAKE_RESULT_JSON_FOR_WEBTOOL`. KPI computation lives in `postprocessing/kpi_computation/`. Results land in a `results/` subdirectory next to the scenario file.

### loadtypes.py
Central registry of enums: `LoadTypes`, `Units`, `ComponentType`, `InandOutputType`, `Locations`, `BuildingCodes`, etc. All component I/O declarations reference these enums — never use raw strings for load types or units.

### units.py
Provides a typed `Quantity` system (`Watt`, `KiloWattHour`, etc.) for stronger unit safety. Distinct from the `lt.Units` enum used in I/O declarations.

### CI resource monitoring (`.github/actions/resource-monitor/`, `scripts/ci_*.py`)
Every CI job measures its own wall time, CPU time and peak memory (cgroup v2) via the
`resource-monitor` composite action, called `mode: start` after checkout and `mode: report`
under `if: always()`. The hourly `ci-usage` workflow sweeps the jobs API plus those
artifacts and writes an overview — per-workflow runner-minutes, jobs that got slower or
hungrier, jobs near the 16 GB runner limit — into its own job summary. Nothing is committed.
See `.github/ci-monitoring.md`; the probe never fails the job it measures.

### RenoVisor translation layer (`hisim/renovisor/`)
Turns one **calculation request** — a house inventory plus a list of catalogue measures — into one
runnable energy-system file, runs it, and writes a result payload beside it. The contract it implements is
the frontend side's `calculation-request` specification; the decision register is
`roadmap/renovisor/challenges.md` (§9, §12 and the decisions of §13), and the implementation specs are
under `roadmap/renovisor/implementation/`. One page of usage: `hisim/renovisor/how_to_use.md`.

Commands: `python -m hisim.renovisor {run|translate|validate|capabilities|map|verify}`. Exit codes 0 finished,
2 the request is not a request (`problems.json` lists every fault), 3 a translator error
(`translator_error.json`), 5 HiSim refused the file or the simulation raised. `verify --out DIR` is tier 1
of the path verification (`hisim/renovisor/verify/`): every capability probe translated and diffed against
its base (request, mapping report, energy system), written as `report.json` + an HTML matrix; it exits 4 when
the report lists a failure. CI uploads it as the artifact `path-verification-report`.

Layers: `contract/` (vendored measure catalogue, request schema, worked mockup and capability-document
schema, each pinned to a commit in `PINNED.yaml`; refresh with
`python -m hisim.renovisor.contract.refresh <contract checkout> --specs <renovisorissues clone>` (`--specs` is
required; `--specs ''` keeps the spec copies and their pins); the shared
specs live in `specs/` of https://jugit.fz-juelich.de/iek-3/groups/urbanmodels/renovisorissues), `vocabulary.py` (closed enums
whose *values* are the catalogue's lowercase strings and whose *names* are HiSim's), `request.py` (the
JSON Schema itself plus the frozen catalogue table and every semantic check), `apply.py` (one function per
measure, over a deep copy of the house; never names a HiSim component), `envelope.py` (the U-value
arithmetic), `tabula.py` (the archetype, every `.N.` country), `translate.py` (the house into one recorded
`*_building_sizer.grouped` twin), `report.py` (`mapping_report.json`), `whitelist.py` +
`not_implemented_yet.yaml` (the one list of what is accepted and not acted on), `simulation.py`, `run.py`,
`result.py`/`kpis.py`/`costs.py`/`layers.py`/`provenance.py` (`result.json`), `capabilities.py` (the probe
set and the document the backend serves per image), `map.py` (generates the committed
`roadmap/renovisor/translation_map.html`; regenerate with `python -m hisim.renovisor map` whenever the
catalogue, the registry or the bindings change, or `tests/renovisor/test_map.py` fails), `verify/` (the
path-verification harness, tier 1: `probes.py` chooses each probe's base and checks completeness against
the request schema and the catalogue, `runner.py` translates and diffs, `render.py` writes the report).

The rule the package rests on: **fail loudly, except for what is written down.** A feature the translator
has not implemented is a note in the mapping report and the calculation runs, but only if
`not_implemented_yet.yaml` says so; anything else that cannot be mapped fails the translator's own build.
`tests/renovisor/test_capabilities.py` keeps that list honest in both directions.

Tests: `tests/renovisor/test_*.py`, all `base` except `test_run.py` (`system_setups`).

## Adding a new component

1. Create `hisim/components/my_component.py`, using `hisim/components/example_template.py` as a
   template — it teaches the current shape, including the parts below.
2. Define a `@dataclass MyComponentConfig(ConfigBase)` that declares `MAIN_CLASS` (the dotted path of
   its component; `ConfigBase.__init_subclass__` refuses a class that declares neither it nor its own
   `get_main_classname`, which is the exception a test double with no real component states) and at
   least one `@preset` classmethod, `preset_<name>(cls, name)`, taking the instance name and nothing
   else. A field the surrounding system decides — a power from the building's heating load, a carrier
   from the generator beside it — is a `sized_field(rule=...)`, left `AUTO` by the preset; a class that
   computes such a value for others declares it in `SIZING_CONTRIBUTIONS`.
3. Subclass `Component`, declare inputs/outputs in `__init__`, implement the four lifecycle methods.
4. Add a test in `tests/test_my_component.py`; use `SimulationParameters.full_year(year=2021, seconds_per_timestep=60)` for a minimal test setup.
5. `hisim energy-system describe hisim.components.my_component.MyComponent` prints what the class now
   offers — its presets, what each sets, and the law behind every sizable field.
