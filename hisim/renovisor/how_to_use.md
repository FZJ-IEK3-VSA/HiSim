# The RenoVisor translation layer, in one page

One **calculation request** in, one directory of results out. A request is one house inventory
plus one list of catalogue measures; the baseline is the same request with `measures: []`. There
is no variant switch, and nothing in the body identifies the submission: the body is the cache
key.

## The five commands

```bash
python -m hisim.renovisor run          request.yaml --out DIR [--period full_year|one_day_15min|one_week_15min]
python -m hisim.renovisor translate    request.yaml --out DIR
python -m hisim.renovisor validate     request.yaml
python -m hisim.renovisor capabilities --out capabilities.json [--measures measures.yaml]
python -m hisim.renovisor map          [--out translation_map.html]
```

`run` is what the backend calls; `--period` defaults to `full_year`, and the two short periods
exist for the verification harness and the end-to-end tests. `translate` stops after the
energy-system file and the mapping report. `validate` prints the problems JSON and writes
nothing. `capabilities` writes the document the backend serves as `GET /measures` for this
image. `map` regenerates `roadmap/renovisor/translation_map.html`.

## The files

**In:** one request, JSON or YAML, conforming to
`hisim/renovisor/contract/calculation-request.schema.json`. The vendored
`calculation-request.mockup-1.yaml` beside it is a complete worked example.

**Out**, in `--out`:

| File | What it is |
| --- | --- |
| `renovisor_<hash>.energy_system.yaml` | the file that ran; `<hash>` is the first 16 hex of the SHA-256 of the canonical request |
| `mapping_report.json` | what the translator did with every leaf of the request and every measure |
| `realized.energy_system.yaml`, `realized.audit.yaml`, `realized.simulation.yaml`, `component_connections.json` | what HiSim made of it, written before the first timestep |
| `results/` | the simulation's own outputs, including `all_kpis.json` |
| `result.json` | the KPIs and the costs, every value with its provenance |
| `calculation.json` | the success manifest: the translator version, the image digest, the options used |
| `problems.json` | only on exit 2: every fault of the request, by path and code |
| `translator_error.json` | only on exit 3: what the translator could not map |

Nothing is written outside `--out` except the cache directory `--cache-dir` names, which is
shared state rather than output. `run` and `translate` also take `--base-files`, the directory
of recorded energy-system files to translate against; it defaults to `energy_systems/`.

## Exit codes

| Exit | Meaning |
| --- | --- |
| 0 | finished |
| 2 | the request is not a valid request; `problems.json` lists every fault at once |
| 3 | a translator error: something the translator cannot map and nobody wrote down |
| 5 | HiSim refused the energy-system file, or the simulation raised |

The last line on standard error for 3 and 5 is one line, which the backend shows as the job's
error message.

## The one rule worth knowing

**Fail loudly, except for what is written down.** An unknown key, an unknown value, a value out
of range, a measure named twice, a country with no TABULA typology, a `measures[i].cost` price
band whose cheap end is above its expensive end or whose prices are negative: each is a refusal
(exit 2) naming every problem at once. A feature the translator has not implemented is a **note**
in the mapping report and the calculation runs — but only if it is an entry of
`hisim/renovisor/not_implemented_yet.yaml`. Anything the translator cannot map that is not in
that file fails the translator's own build (exit 3), never the user's request — an insulation
build-up position with no cost-database asset class and a building whose archetype or design
temperature the existing generator cannot be sized from are both of that kind.

The same two codes hold for `python -m hisim.economics staged`, which prices a plan out of
finished jobs: exit 2 with a `problems.json` for a plan the caller can fix — stage years that run
backwards, a stage directory that carries `economic_inputs.json` but no `mapping_report.json` —
and exit 3 for an engine failure they cannot.

The list is kept honest in both directions by `T-NIY`, which runs the whole probe set and
asserts that every `not_implemented_yet` line has an entry and that every entry is reached by at
least one probe. It can only shrink by someone implementing the item, and it can only grow by
someone writing the sentence users will read.

## What the MVP does not do

Every household is simulated as the precomputed `CHR01 Couple both at Work` profile, because the
image ships no LoadProfileGenerator (decision D-C). The base files are the recorded grouped
twins in `energy_systems/` as they stand (decision D-D), so a night setback, an air conditioner,
an energy management system without a battery, a solar thermal collector on a third generator
and an electric vehicle are all `not_implemented_yet` until that base-file work is done. Every
one of those is an entry in the list with a note, and the capability document announces them
before a request is ever submitted.
