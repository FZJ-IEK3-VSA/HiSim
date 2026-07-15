# System Setups

> **Note:** This README was generated with the assistance of Claude Code. The
> content was reviewed and approved by Valentin Janser (v.janser@fz-juelich.de).

This folder contains system setup definitions for ETHOS.HiSim simulations. Each setup
describes which energy system components are used, how they are configured, and how they
are connected to each other.

## Running a simulation

From the repository root (or any working directory), run:

```bash
# JSON mode (recommended)
python hisim/hisim_main.py <scenario>.scenario.json <simulation>.simulation.json

# Legacy Python mode
python hisim/hisim_main.py <setup>.py
```


### JSON setups (recommended)

A JSON setup is split into two separate files that are passed as the first and second
command-line arguments respectively:

| File | Purpose |
|---|---|
| `<name>.scenario.json` | What will be simulated? Which components, how are they connected? |
| `<name>.simulation.json` | How is the scenario simulated? Which time range, post-processing? |

Separating these two concerns makes it easy to reuse the same scenario with different
time ranges or post-processing options, or to compare multiple scenarios under identical
simulation settings.

### Legacy Python setups

Each file exports a `setup_function(my_sim, my_simulation_parameters)` that imperatively
instantiates components, configures them, and wires them together. This mode is flexible
and supports arbitrary Python logic (loops, conditionals, helper functions). It remains
fully supported for now but is not recommended for new setups.





---
# Format of the two JSON files

## Scenario JSON (`*.scenario.json`)

Describes the physical system: its components, their configurations, and how they are
wired together.

### Top-level structure

```json
{
    "name": "My scenario",
    "description": "Short description shown in logs and reports.",
    "multiple_buildings": false,
    "components": [ ... ],
    "connections": [ ... ]
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Human-readable scenario name |
| `description` | string | no | Short description; shown in the result directory name and reports |
| `multiple_buildings` | bool | no (default `false`) | Enable multi-building mode |
| `components` | array | yes | List of component definitions (see below) |
| `connections` | array | no | Explicit connections between component outputs and inputs (see below) |

### Component definition

Each entry in `components` describes one component instance:

```json
{
    "component_full_classname": "hisim.components.weather.Weather",
    "config_full_classname": "hisim.components.weather.WeatherConfig",
    "configuration": { ... },
    "inputs": [ ... ],
    "outputs": [ ... ],
    "connect_automatically": true
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `component_full_classname` | string | yes | Fully qualified Python class name of the component |
| `config_full_classname` | string | no | Fully qualified Python class name of the config. Derived automatically from the component's `__init__` type hint if omitted |
| `configuration` | object | no | Config fields as key-value pairs. If absent or `{}`, the component's `get_default_*` classmethod is called automatically |
| `inputs` | array | no | Additional dynamic or static inputs to register on the component |
| `outputs` | array | no | Additional dynamic or static outputs to register on the component |
| `connect_automatically` | bool | yes | If `true`, the simulator wires default connections automatically based on the component's `add_default_connections()` declarations |

#### Using default configurations

When `configuration` is absent or empty, HiSim looks for a classmethod whose name
contains `default` on the config class and calls it with no arguments. This only works
if the config class has **exactly one** such method; an error is raised if there are
zero or multiple candidates.

#### Special placeholders in `configuration`

Two components require path placeholders that are resolved at runtime:

- **UtspLpgConnector** — set `result_dir_path` to `"<<utils.HISIMPATH['utsp_results']>>"`;
  the executor replaces this with the actual UTSP results directory.
- **Weather** — set `source_path` to a string starting with `"<<utils.get_input_directory()>>"`;
  the remainder of the path is appended using the OS-appropriate separator.

#### Dynamic input definition

Entries in `inputs` with `"dynamic": true` connect a component to an output of another
component matched by load type, unit, tags, and weight:

```json
{
    "dynamic": true,
    "source_component_output": "ElectricityOutput",
    "source_object_name": "PVSystem",
    "source_load_type": "Electricity",
    "source_unit": "W",
    "source_tags": ["PV", "ElectricityProduction"],
    "source_weight": 999
}
```

#### Static input definition

Entries in `inputs` with `"dynamic": false` register a named input port:

```json
{
    "dynamic": false,
    "object_name": "MyComponent",
    "field_name": "MyInputField",
    "load_type": "Electricity",
    "unit": "W",
    "mandatory": true
}
```

#### Dynamic output definition

```json
{
    "dynamic": true,
    "source_output_name": "ElectricityOutput",
    "source_tags": ["PV", "ElectricityProduction"],
    "source_load_type": "Electricity",
    "source_unit": "W",
    "source_weight": 0,
    "output_description": "PV electricity production",
    "source_component_class": null
}
```

#### Static output definition

```json
{
    "dynamic": false,
    "object_name": "MyComponent",
    "field_name": "MyOutputField",
    "load_type": "Electricity",
    "unit": "W",
    "postprocessing_flag": null,
    "sankey_flow_direction": null,
    "output_description": "Description shown in reports"
}
```

### Connections

Explicit wiring between a named output field of one component and a named input field of
another. Only needed when `connect_automatically` is `false` and the connection is not
handled by dynamic input matching.

```json
{
    "source": {
        "component_name": "PVSystem",
        "field_name": "ElectricityOutput"
    },
    "target": {
        "component_name": "ElectricityMeter",
        "field_name": "Input_PVSystem_ElectricityOutput_0"
    }
}
```

`component_name` refers to the `name` field inside the component's `configuration`
(e.g. `"name": "PVSystem"`), not the class name.

---

## Simulation JSON (`*.simulation.json`)

Describes how the simulation should run, independently of which scenario is used.

### Full structure

```json
{
    "start_date": "2021-01-01T00:00:00",
    "end_date": "2022-01-01T00:00:00",
    "seconds_per_timestep": 60,
    "post_processing_options": ["PLOT_LINE", "COMPUTE_KPIS"],
    "logging_level": 3,
    "result_directory": "",
    "skip_finished_results": false,
    "log_connections": false
}
```

### Field reference

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `start_date` | ISO 8601 datetime string | yes | — | First timestep of the simulation |
| `end_date` | ISO 8601 datetime string | yes | — | First timestep **after** the simulation ends |
| `seconds_per_timestep` | int | yes | — | Duration of each timestep in seconds (e.g. `60` for 1-minute resolution) |
| `post_processing_options` | array of strings | no | `[]` | Post-processing tasks to run after the simulation (see below) |
| `logging_level` | int | no | `3` (Information) | Log verbosity: `1` Debug, `2` Profile, `3` Information, `4` Warning, `5` Error |
| `result_directory` | string | no | `""` | Output directory for results. Auto-generated from the scenario file name if empty |
| `skip_finished_results` | bool | no | `false` | If `true`, skip recomputation if a result directory already exists |
| `log_connections` | bool | no | `false` | If `true`, write component connections to `component_connections.json` in the result directory |

### Post-processing options

The `post_processing_options` array accepts any combination of the following string values:

| Value | Description |
|---|---|
| `PLOT_LINE` | Line plots for all outputs |
| `PLOT_CARPET` | Carpet plots (heat maps over time) |
| `PLOT_SANKEY` | Sankey energy flow diagram |
| `PLOT_SINGLE_DAYS` | Detailed plots for representative single days |
| `PLOT_MONTHLY_BAR_CHARTS` | Monthly bar charts for key outputs |
| `PLOT_SPECIAL_TESTING_SINGLE_DAY` | Single-day plots used in automated testing |
| `OPEN_DIRECTORY_IN_EXPLORER` | Open the result directory in the file explorer after the simulation |
| `EXPORT_TO_CSV` | Export all time-series outputs to CSV |
| `EXPORT_TO_PKL` | Export all time-series outputs to a pickle file |
| `EXPORT_MONTHLY_RESULTS` | Export monthly aggregates to CSV |
| `EXPORT_RESULTS_IN_ONE_FILE` | Combine all outputs into a single file |
| `MAKE_NETWORK_CHARTS` | Generate component wiring diagrams |
| `GENERATE_PDF_REPORT` | Generate a PDF summary report |
| `WRITE_COMPONENTS_TO_REPORT` | Include component descriptions in the PDF report |
| `WRITE_ALL_OUTPUTS_TO_REPORT` | Include all output plots in the PDF report |
| `WRITE_NETWORK_CHARTS_TO_REPORT` | Include wiring diagrams in the PDF report |
| `INCLUDE_CONFIGS_IN_PDF_REPORT` | Include component configurations in the PDF report |
| `INCLUDE_IMAGES_IN_PDF_REPORT` | Include images in the PDF report |
| `COMPUTE_OPEX` | Calculate operational expenditure costs |
| `COMPUTE_CAPEX` | Calculate capital expenditure costs |
| `COMPUTE_KPIS` | Calculate key performance indicators |
| `PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION` | Prepare outputs for multi-scenario comparison |
| `WRITE_CONFIGS_FOR_SCENARIO_EVALUATION_TO_JSON` | Export configs for scenario evaluation |
| `WRITE_COMPONENT_CONFIGS_TO_JSON` | Write all component configurations to JSON |
| `WRITE_KPIS_TO_JSON` | Write KPI results to a JSON file |
| `WRITE_KPIS_TO_JSON_FOR_BUILDING_SIZER` | Write KPIs in the format expected by the building sizer |
| `MAKE_RESULT_JSON_FOR_WEBTOOL` | Generate a result JSON for the HiSim webtool |
| `MAKE_OPERATION_RESULTS_FOR_WEBTOOL` | Generate operational results for the webtool |
| `PROVIDE_DETAILED_ITERATION_LOGGING` | Write per-timestep convergence details to a log file |
| `GENERATE_CSV_FOR_HOUSING_DATA_BASE` | Export results in the housing database CSV format |

### Pre-defined simulation JSON files

| File | Resolution | Post-processing |
|---|---|---|
| `2021_minutely_plots.simulation.json` | 1 min | Line, carpet, single-day and monthly plots |
| `2021_minutely_full.simulation.json` | 1 min | All options enabled |
| `2021_minutely_none.simulation.json` | 1 min | None |
| `2021_15minutely_plots.simulation.json` | 15 min | Line, carpet, single-day and monthly plots |
| `2021_15minutely_noplots.simulation.json` | 15 min | KPIs and CSV export only |
| `2021_15minutely_noplots_buildingsizer.simulation.json` | 15 min | KPIs for building sizer |
| `2021_hourly_report.simulation.json` | 60 min | Full PDF report with KPIs |
