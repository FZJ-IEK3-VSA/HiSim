# EPC calculator

The calculator computes the energy-performance-certificate heating demand of exactly the building a
HiSim run computed with, by the national method of the country given, and writes it as a structured
report: for one run or for every run under a results tree. It imports nothing from HiSim; its only
input is the `building.expanded.yaml` HiSim writes beside every run's realized record.

Specification: `roadmap/epc/requirements.md`, `roadmap/epc/design.md`, `roadmap/epc/input_contract.md`.

## The three steps

| Step | Folder | Knows | Does not know |
|---|---|---|---|
| 1 Inputs | `inputs/` | the expanded record | any norm |
| 2 Engine | `engines/<cc>/` | one national method | any file |
| 3 Report | `reports/` | the result dictionary | any norm, any HiSim format |

Between the steps travel two dictionaries, both in `model/`: the `BuildingDescription` (HiSim's
building: one element per class with area, U-value and adjustment factor, windows per direction,
glazing, conditioned floor area, dwelling units, site) and the `EpcResult` (a common core, the
engine's own keys, and every norm default the engine applied as an assumption).

The reader of step 1 computes nothing about the building. It takes every value as written, derives
each element's situation from HiSim's adjustment factor and the element class (1 outside air,
0 conditioned neighbour, 0.5 the attic, cellar or buffer space the class names), makes one window
surface per direction that has an area, and refuses what it cannot take, naming the field.

## Running it

From the repository root:

```
python -m epc run   <results dir>  --country <CC> [--out DIR]
python -m epc batch <results root> --country <CC> [--out DIR]
python -m epc countries
```

`--country` is an ISO 3166-1 alpha-2 code; `countries` lists the implemented engines and their
editions. `--out` defaults to `epc/results/` (git-ignored).

| Command | Writes | Exit code |
|---|---|---|
| `run` | `<out>/<results dir name>/<building>.epc.json` per building | 0; 2 on a refusal, nothing written |
| `batch` | `<out>/<run path>/<building>.epc.json` per computed building, `epc_table.csv`, `epc_summary.md` | 0; 1 if any run was refused; 2 if the country, the engine's data, the root or the output directory is unusable |

The run path is the run directory relative to the results root. A batch writes only into an output
directory that is new or empty, and records a refused run in the summary and continues.

Every refusal carries an id (`E-01` … `E-18`) and names what is missing; the ids and messages are in
`model/errors.py` (design §8).

## Where everything is

```
epc/
  __main__.py, batch.py     command line; walking a results root, running, collecting
  model/                    building.py, result.py, errors.py: the dictionaries and the refusals
  inputs/record.py          step 1: building.expanded.yaml -> BuildingDescription
  engines/                  step 2: base.py (the engine protocol), registry.py (engines by country)
  reports/                  step 3: json_report.py, table.py, summary.py
  data/                     norm tables per country; README.md is the data contract
  results/                  default output of local runs, git-ignored
  tests/                    functionality tests (marker base); fixtures written by HiSim
```

An engine is one package `engines/<cc>/` with its data under `data/<cc>/`, registered in
`engines/registry.py` under its country code. Nothing else changes when a country is added.

## Tests

```
pytest epc/tests -m base
```

Functionality tests (`base`) run everywhere and hold no norm value. Validation against a norm's own
worked examples carries the marker `epc_norm_data` and is skipped where the licensed data are absent.

## Result keys

Every engine fills the common core:

| Key | Meaning |
|---|---|
| `heating_demand_reference_climate` | the certificate heating demand under the national reference (or national) climate, per the national reference area |
| `indicator_reference_area` | the national reference area the indicators are given per |
| `heating_demand_site_climate` | the same under the site climate; present exactly when the engine's `climate_basis` is `reference+site` |

Every further key is an engine's own and is listed here, per engine, with that engine. No engine is
implemented yet.
