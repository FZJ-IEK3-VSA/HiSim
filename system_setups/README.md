# System Setups

> **Note:** This README was generated with the assistance of Claude Code. The
> content was reviewed and approved by Valentin Janser (v.janser@fz-juelich.de).

This folder contains HiSim's **imperative** system setups: one `setup_function(my_sim,
my_simulation_parameters)` per `.py` file, which instantiates components, configures them
and wires them together. It also holds the shared `*.simulation.json` parameter files,
which say how a setup is run rather than what it is.

The **declarative** input lives one directory up, in
[`energy_systems/`](../energy_systems/README.md): one `*.energy_system.yaml` per
household, including a recorded twin of every setup here. The v1 `*.scenario.json` files
that used to sit beside each setup retired on 2026-09-12 — every one of them had a
recorded twin, and the twins are held current by the `energy-system-freshness` gate.

## Running a simulation

From the repository root (or any working directory), run:

```bash
# Imperative Python setup
python hisim/hisim_main.py system_setups/<setup>.py

# Declarative energy system (the recorded twin of the same setup)
python hisim/hisim_main.py energy_systems/<setup>.energy_system.yaml \
    system_setups/<simulation>.simulation.json
```

A Python setup takes an optional module config and an optional parameters file as its
second and third arguments; an energy system takes its parameters file as the second.
Both read either spelling of a parameters file, the `*.simulation.yaml` files beside the
energy systems and the `*.simulation.json` files here.

---

# Format of the simulation-parameters file (`*.simulation.json`)

Describes how the simulation should run, independently of which household is run.

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

| Field                       | Type                     | Required | Default             | Description                                                                                       |
| --------------------------- | ------------------------ | -------- | ------------------- | ------------------------------------------------------------------------------------------------- |
| `start_date`              | ISO 8601 datetime string | yes      | —                  | First timestep of the simulation                                                                  |
| `end_date`                | ISO 8601 datetime string | yes      | —                  | First timestep**after** the simulation ends                                                 |
| `seconds_per_timestep`    | int                      | yes      | —                  | Duration of each timestep in seconds (e.g.`60` for 1-minute resolution)                         |
| `post_processing_options` | array of strings         | no       | `[]`              | Post-processing tasks to run after the simulation (see below)                                     |
| `logging_level`           | int                      | no       | `3` (Information) | Log verbosity:`1` Debug, `2` Profile, `3` Information, `4` Warning, `5` Error           |
| `result_directory`        | string                   | no       | `""`              | Output directory for results. Auto-generated from the input file name if empty                 |
| `skip_finished_results`   | bool                     | no       | `false`           | If`true`, skip recomputation if a result directory already exists                               |
| `log_connections`         | bool                     | no       | `false`           | If`true`, write component connections to `component_connections.json` in the result directory |

### Post-processing options

The `post_processing_options` array accepts any combination of the following string values:

| Value                                             | Description                                                         |
| ------------------------------------------------- | ------------------------------------------------------------------- |
| `PLOT_LINE`                                     | Line plots for all outputs                                          |
| `PLOT_CARPET`                                   | Carpet plots (heat maps over time)                                  |
| `PLOT_SANKEY`                                   | Sankey energy flow diagram                                          |
| `PLOT_SINGLE_DAYS`                              | Detailed plots for representative single days                       |
| `PLOT_MONTHLY_BAR_CHARTS`                       | Monthly bar charts for key outputs                                  |
| `PLOT_SPECIAL_TESTING_SINGLE_DAY`               | Single-day plots used in automated testing                          |
| `OPEN_DIRECTORY_IN_EXPLORER`                    | Open the result directory in the file explorer after the simulation |
| `EXPORT_TO_CSV`                                 | Export all time-series outputs to CSV                               |
| `EXPORT_TO_PKL`                                 | Export all time-series outputs to a pickle file                     |
| `EXPORT_MONTHLY_RESULTS`                        | Export monthly aggregates to CSV                                    |
| `EXPORT_RESULTS_IN_ONE_FILE`                    | Combine all outputs into a single file                              |
| `MAKE_NETWORK_CHARTS`                           | Generate component wiring diagrams                                  |
| `GENERATE_PDF_REPORT`                           | Generate a PDF summary report                                       |
| `WRITE_COMPONENTS_TO_REPORT`                    | Include component descriptions in the PDF report                    |
| `WRITE_ALL_OUTPUTS_TO_REPORT`                   | Include all output plots in the PDF report                          |
| `WRITE_NETWORK_CHARTS_TO_REPORT`                | Include wiring diagrams in the PDF report                           |
| `INCLUDE_CONFIGS_IN_PDF_REPORT`                 | Include component configurations in the PDF report                  |
| `INCLUDE_IMAGES_IN_PDF_REPORT`                  | Include images in the PDF report                                    |
| `COMPUTE_OPEX`                                  | Calculate operational expenditure costs                             |
| `COMPUTE_CAPEX`                                 | Calculate capital expenditure costs                                 |
| `COMPUTE_KPIS`                                  | Calculate key performance indicators                                |
| `PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION`       | Prepare outputs for multi-scenario comparison                       |
| `WRITE_CONFIGS_FOR_SCENARIO_EVALUATION_TO_JSON` | Export configs for scenario evaluation                              |
| `WRITE_COMPONENT_CONFIGS_TO_JSON`               | Write all component configurations to JSON                          |
| `WRITE_KPIS_TO_JSON`                            | Write KPI results to a JSON file                                    |
| `WRITE_KPIS_TO_JSON_FOR_BUILDING_SIZER`         | Write KPIs in the format expected by the building sizer             |
| `MAKE_RESULT_JSON_FOR_WEBTOOL`                  | Generate a result JSON for the HiSim webtool                        |
| `MAKE_OPERATION_RESULTS_FOR_WEBTOOL`            | Generate operational results for the webtool                        |
| `PROVIDE_DETAILED_ITERATION_LOGGING`            | Write per-timestep convergence details to a log file                |

### Pre-defined simulation JSON files

| File                                                      | Resolution | Post-processing                            |
| --------------------------------------------------------- | ---------- | ------------------------------------------ |
| `2021_minutely_plots.simulation.json`                   | 1 min      | Line, carpet, single-day and monthly plots |
| `2021_minutely_full.simulation.json`                    | 1 min      | All options enabled                        |
| `2021_minutely_none.simulation.json`                    | 1 min      | None                                       |
| `2021_15minutely_plots.simulation.json`                 | 15 min     | Line, carpet, single-day and monthly plots |
| `2021_15minutely_noplots.simulation.json`               | 15 min     | KPIs and CSV export only                   |
| `2021_15minutely_noplots_buildingsizer.simulation.json` | 15 min     | KPIs for building sizer                    |
| `2021_hourly_report.simulation.json`                    | 60 min     | Full PDF report with KPIs                  |
