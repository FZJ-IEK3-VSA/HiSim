# simulation_parameters/

What to **do** with an energy system: the period, the time-step length, the logging level and
which post-processing to run. The system itself is described next door in `energy_systems/`, and
the two are deliberately separate — a parameter set belongs to no particular household, and the
same household is run over a day and over a year without a second copy of it.

```bash
hisim energy-system run energy_systems/gas_boiler_household.energy_system.yaml \
    simulation_parameters/one_day_15min_export.simulation.yaml
```

## Which one to reach for

| File | Horizon | Step | What it computes |
|---|---|---|---|
| `one_day_15min_plain.simulation.yaml` | one January day | 15 min | nothing. The fastest way to find out whether a file builds, wires and runs at all |
| `one_day_15min_export.simulation.yaml` | one January day | 15 min | the result table. The pair to reach for when trying a file out; every recording is made with this one |
| `one_week_minutely_kpis_costs.simulation.yaml` | one January week | 1 min | KPIs, OpEx and CapEx. The shape the golden gate checks, runnable by hand |
| `2021_hourly_kpis_costs.simulation.yaml` | the whole of 2021 | 1 h | the same, over a year, in minutes rather than hours. The one to iterate with |
| `2021_15min_kpis_costs_scenarios.simulation.yaml` | the whole of 2021 | 15 min | the same plus the resampled scenario-evaluation CSVs. The shape the building sizer runs |
| `2021_minutely_plots.simulation.yaml` | the whole of 2021 | 1 min | the standard plots |
| `2021_minutely_kpis_costs_export.simulation.yaml` | the whole of 2021 | 1 min | KPIs, costs, the result table and monthly sums. **A production run of one household** |
| `2021_minutely_kpis_costs_lifecycle_plots_report_export.simulation.yaml` | the whole of 2021 | 1 min | everything: the above, the lifecycle cost engine with its report, every plot, the network charts and the PDF |

A full year at one-minute resolution is 525 600 timesteps; budget hours rather than minutes for
the last three, and start with the one-day pair to shake out an install.

## What is in one, and what is deliberately not

```yaml
start_date: "2021-01-01T00:00:00"
end_date: "2022-01-01T00:00:00"
seconds_per_timestep: 60
country: "DE"
logging_level: 3
post_processing_options:
  - COMPUTE_KPIS
  - ...
```

Six keys and nothing else. What describes the *machine* rather than the run — the cache directory
above all, which several setups point at a cluster path — is absent by design, so that two people
running the same file are running the same thing. The option names are the members of
`hisim.postprocessingoptions.PostProcessingOptions`; an unknown one is refused by name.

## The names are derived, not invented

`<horizon>_<resolution>_<purpose>.simulation.yaml`, built by
`hisim/energy_system/parameters_format.py`:

- **horizon** — the year for a whole calendar year, else `one_day`, `two_days`, `one_week`,
  `two_weeks`, `one_month`, else a plain day count.
- **resolution** — `minutely`, `5min`, `10min`, `15min`, `30min`, `hourly`, else the seconds.
- **purpose** — every family the option set touches, in order: `kpis`, `costs`, `lifecycle`,
  `plots`, `report`, `export`, `scenarios`. `plain` when a run asks for no post-processing.

The rule matters because this directory has two authors. A person adds a file here; so does the
**recorder**, when it records a setup whose parameters no file here already describes. Deriving
the name from the content means the two cannot collide or duplicate each other, and
`tests/test_simulation_parameters_library.py` holds every file in this directory to it.

## No two files may mean the same thing

Two parameter sets are the same run when their period, resolution, sorted option set, logging
level, country and year agree — whatever their names, comments or key order. A second file saying
what one already says is refused by `scripts/record_all_setups.py --check`, because a recording
could then reference either of them and two recordings of one run would disagree about which.

So a new sample has to be a genuinely different run. That is also what makes this a library rather
than a pile: every file in it is a distinct answer to "what should be done with a household".

## A run writes its own

A finished run leaves `realized.simulation.yaml` in its results directory — the parameter set it
was actually given, in this same format. It is not part of this library (nothing here references
it), but it can be handed straight back to `hisim energy-system run` together with the
`realized.energy_system.yaml` beside it, which is how a result directory re-runs from its own
contents.
