# Energy systems

This directory holds **energy-system files**: declarative descriptions of one simulated household,
written as YAML, with no Python involved. A file states what each component is, how it is
configured, where it takes its inputs from and — only where that would otherwise be ambiguous —
which component it takes each of its sizing facts from. Everything else, including every value a
preset pins and every value the system computes for itself, follows from those declarations.

## What is here

| File | What it is |
|---|---|
| `gas_boiler_household.energy_system.yaml` | A single-family house with a condensing gas boiler, floor heating and grid electricity. The reference system of this directory, and the only hand-written one. |
| `<setup>.energy_system.yaml` | The recorded twin of `system_setups/<setup>.py`, one per setup. Generated; see "Recording a Python setup" below. |
| `<setup>.probes.yaml` | The module configurations `<setup>` is recorded under — the *probe list*. Authored; the first entry is always the class defaults. See "Groups and variants" below. |
| `<setup>.grouping.yaml` | What a person decided each difference between those configurations means. Authored through a workbook and committed in this form. |
| `<setup>.grouped.energy_system.yaml` | The twin again, with the differences that are structure expressed as groups and variants. Generated from the two files above. |
| `one_day_15min.simulation.yaml` | One January day at a quarter-hour resolution. The pair to reach for when trying a file out; the test suite and every recording run against this file too. |
| `2021_minutely.simulation.yaml` | The whole of 2021 at a one-minute resolution with the standard plots. |

Two kinds of file live side by side and are never mixed:

- `*.energy_system.yaml` says what the household **is**. It carries no period, no resolution and no
  post-processing, so the same household can be run over a day and over a year without a second
  copy of it.
- `*.simulation.yaml` says what to **do** with it: the period, the time-step length, the logging
  level and which post-processing to run. A `*.simulation.json` of the same shape is read as well,
  which is what the Python setups in `system_setups/` already ship.

## Running one

Either of these runs the same thing:

```bash
hisim energy-system run energy_systems/gas_boiler_household.energy_system.yaml \
    energy_systems/one_day_15min.simulation.yaml

python hisim/hisim_main.py energy_systems/gas_boiler_household.energy_system.yaml \
    energy_systems/one_day_15min.simulation.yaml
```

The run writes its results, and beside them three files that describe what was actually run:
`realized.energy_system.yaml` — the file again with every preset expanded and every computed value
written out, annotated with where each number came from; `realized.audit.yaml` — the same
provenance as plain data; and `component_connections.json` — the flat log of every connection that
was made. Re-running the realized record decides nothing and reproduces the run exactly:

```bash
hisim energy-system run results/.../realized.energy_system.yaml \
    energy_systems/one_day_15min.simulation.yaml --rerun
```

## Finding out what to write

```bash
hisim energy-system describe hisim.components.generic_boiler.GenericBoiler
hisim energy-system facts energy_systems/gas_boiler_household.energy_system.yaml
```

`describe` prints one class in full: its fields, its named presets and what each of them leaves to
be sized, its named constructors and their parameters, how each sizable field is computed and from
which facts, and which facts it contributes to the rest of a system. `facts` prints, for a whole
file and without running it, the file's knobs — a flag per group and a selected option per variant,
which is everything a consumer of a checked-in system edits — and then every fact somebody
provides, every fact somebody reads, and which provider each read resolved to.

Editors get the same knowledge from `hisim/energy_system_v3.schema.json`, which every file in this
directory binds to with its first line. The schema is generated from the same declarations
`describe` reads — regenerate it with `hisim energy-system schema` whenever a component gains a
preset, a constructor or a field.

## Recording a Python setup

Every `<setup>.energy_system.yaml` here is generated. It was produced by running the Python setup
and writing down what it built — one setup at a time:

```bash
hisim energy-system record system_setups/basic_household.py \
    energy_systems/one_day_15min.simulation.yaml
```

or the whole fleet at once, which is what regenerates this directory:

```bash
python scripts/record_all_setups.py
```

The recorder observes a finished run — it never parses the setup's source — so a twin states what
the setup actually constructed rather than what its code appears to say. Every value is concrete,
a class that carries a preset is written as that preset plus whatever the setup changed, and the
file names no sizing sources, no groups and no variants: those are judgements about intent, and one
run cannot be asked about intent. Before the command returns, the file it wrote is loaded back
through the executor and built, so a twin that does not work is reported as a failed recording
rather than left behind.

Recording is deterministic: the same setup and the same parameters produce the same bytes on any
machine. A twin is therefore regenerated rather than edited — change the setup, re-record, and read
the diff. `python scripts/record_all_setups.py --check` re-records every setup into a throwaway
directory and fails on any difference, which is what the `energy-system-freshness` workflow runs on
every pull request, so a setup cannot change without its twin.

There is no skip list. A setup that cannot be recorded is a defect in the setup or a gap in the
format, and the driver names every one it could not record and exits non-zero. Two setups are in
that state today: `household_gas_solar_thermal`, whose electricity meter is wired to the occupancy
twice — once explicitly and once through the meter's own declared default — so the format refuses
the duplicate feed the setup really does build; and `household_heatpump_car_building_sizer`, whose
`Car` components need a data dictionary computed from the occupancy instance at setup time, which
is a constructor argument the declarative path has no way to supply.

## Groups and variants: probe, decide, apply

A recording observes one run, so it can only describe the branch that ran. The twelve
building-sizer setups driven by a `ModularHouseholdConfig` have branches — a battery that is there
or is not, a rooftop share somebody picks — and which of those differences is *structure* is a
judgement no diff can make. So
there is a second pass with a person in the middle of it, and three files per setup.

```bash
# 1. record the setup under every configuration of its probe list and prefill the workbook
hisim energy-system grouping probe system_setups/household_heatpump_building_sizer.py \
    energy_systems/household_heatpump_building_sizer.probes.yaml

# 2. a person fills in the two right-hand columns of the workbook, then:
hisim energy-system grouping import energy_systems/household_heatpump_building_sizer.grouping.xlsx

# 3. build the grouped file and prove it against every probe
hisim energy-system record system_setups/household_heatpump_building_sizer.py \
    energy_systems/one_day_15min.simulation.yaml \
    --grouping energy_systems/household_heatpump_building_sizer.grouping.yaml
```

The workbook has two sheets. `components` has one row per component in the union of the probe runs
and one cell per probe holding `—` (not there), `=` (there, says the same) or `≠` (there, says
something else). The third state is the point: an electricity meter is present in every
configuration and *wired* differently in one of them, which a presence matrix cannot see and which
is exactly what a group cannot express and a variant can. `configurations` has one row per probe,
the module-configuration fields that produced it, and the switch positions that column stands for.

A row that is `=` everywhere needs nothing and stays empty. Every other row must carry one of
three answers: `group:<name>`, `variant:<name>` or `variant:<name>/<option>`, or `override` — the
difference is a value a consumer sets rather than a question of membership. The tool never decides
a row's meaning itself — it only writes down what it observed and carries a previously committed
decision forward — and a `≠` row left empty is refused by name, twice: once by the importer
against the cells, and once by the second pass against the recordings.

Every probe column is then an assertion the second pass checks: put the grouped file's switches
where that column stands, resolve it, and the result equals that column's flat recording byte for
byte. The baseline column is what makes the grouped file provably the committed twin with structure
added. `override` differences are not in the file at all — the file states the baseline's value and
the pass lists each of them as a *consumer knob*, so a column's verdict says exactly how much the
file itself determined.

Two limits are printed rather than implied. The knob list is what the file does not decide. And a
grouped file's guarantees reach exactly as far as its probe list: a list that toggles each fork on
its own has tested no two of them together, so the report names every combination nobody exercised.

The workbook is a scratch artefact and is git-ignored; the probe list and the grouping decision are
committed and reviewed. Re-probing a setup carries the committed decision back into the workbook, so
a setup that grew a component is asked about that row and no other.

## Simulation-parameters files are shared, never duplicated

A recording does not write a parameters file of its own. It compares the parameters the setup
ended up with against every `*.simulation.yaml` here, references the one that says the same thing,
and only writes a new file when nothing matches — including nothing written earlier in the same
run, so two setups needing identical parameters share one file. The comparison is semantic: the
period, the resolution, the post-processing options as a set, the logging level, the country and
the year. Machine-specific fields take no part in it and are never written, `cache_dir_path` above
all, which eleven setups point at a cluster directory behind an existence probe.

A file written this way is named for its content — the horizon, the resolution and what its option
set is for, as in `one_week_minutely_kpis.simulation.yaml` — and never for the setup that first
needed it, because it is shared from the moment a second setup matches it. The freshness job also
asserts that no two files here describe the same run, so a duplicate cannot be added by hand.

## Relation to `system_setups/`

`system_setups/` is untouched and still holds HiSim's Python setups and their JSON twins; those
keep working exactly as before and are not going anywhere yet. This directory is where new,
declarative systems are written, and where the recorded twins of the old ones land.

`gas_boiler_household.energy_system.yaml` has a second life as a design document:
`roadmap/declarative_energy_systems/energy_system_mockup_minimal.yaml` is the normative mockup of
the file format, and the file here is its runnable, canonically written twin. A test asserts that
the two are identical once both are canonicalised, so a change to one has to be made to the other.
