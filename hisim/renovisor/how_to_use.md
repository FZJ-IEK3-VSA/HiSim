# The RenoVisor translation layer, in one page

One **calculation request** in, one directory of results out. A request is one house inventory
plus one list of catalogue measures; the baseline is the same request with `measures: []`. There
is no variant switch, and nothing in the body identifies the submission: the body is the cache
key.

## The six commands

```bash
python -m hisim.renovisor run          request.yaml --out DIR [--period full_year|one_day_15min|one_week_15min]
python -m hisim.renovisor translate    request.yaml --out DIR
python -m hisim.renovisor validate     request.yaml
python -m hisim.renovisor capabilities --out capabilities.json [--measures measures.yaml]
python -m hisim.renovisor map          [--out translation_map.html]
python -m hisim.renovisor verify       --out DIR [--base-files DIR]
```

`run` is what the backend calls; `--period` defaults to `full_year`, and the two short periods
exist for the verification harness and the end-to-end tests. `translate` stops after the
energy-system file and the mapping report. `validate` prints the problems JSON and writes
nothing. `capabilities` writes the document the backend serves as `GET /measures` for this
image. `map` regenerates `roadmap/renovisor/translation_map.html`. `verify` runs tier 1 of the
path verification (below).

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

## Path verification, tier 1

`verify` answers, for every probe of the capability probe set, whether a setting reaches the right
HiSim field and nothing else — without a simulation (renovisorissues
`specs/path_verification_spec.md`, §9 step 1). Each probe is translated together with its **base**,
the request it is diffed against: the anchor (the vendored mockup with `measures: []`) for a
measure, a block or a pair; `measure:<id>` for an option; the anchor with the field's block (and,
for a SCOP, the heat pump) for an inventory field; and, where the probe sends the value its base
already carries, the sibling probe with another value. Translations are cached by request hash.
Three stages per probe, one matrix column each:

| Column | Stage | ● | ◐ | ○ | ✖ |
| --- | --- | --- | --- | --- | --- |
| `req` | the request diff | exactly the probe's change | | | a stray or an empty change |
| `map` | the mapping report's line for each changed leaf | `used` | approximated / defaulted / not_implemented_yet; for a pair, a status below the announced one (a finding, see below) | | no line, a raise, or `defaulted`/`not_implemented_yet` where the capability document announces `used`/`approximated` |
| `sys` | the energy-system (and economic-context) diff, per config field | something changed — shown, not judged | nothing changed, nothing `used` | nothing changed although a leaf is `used` (a finding) | the translation raised |

It writes `DIR/report.json` (the same content, machine-readable), `DIR/index.html` (the matrix,
filterable by category and state) and `DIR/probes/*.html` (one probe's path, stages 1–3 with
before and after; stages 4–5 read "not run (tier 2)"). It exits **4** when the report lists a
failure — a settable leaf, enum value, range end, measure or option value of the request schema or
the catalogue that no probe changes, a stage-1 diff that is not the probe's change, a single-change
probe's status below the announced one, a translation that raised — and **0** otherwise; findings
never fail it. A finding is a leaf reported `used` whose probe leaves the energy system unchanged
(`no_effect`), or a **pair** probe (one of the probe set's combinations, spec §7) whose status is
below the one the capability document announces for the leaf (`conditional_status`, its `map` cell
◐): a combination's status is conditional — solar thermal's `supplies` beside an oil boiler, a
seasonal efficiency beside a heat pump — and the document cannot state a conditional status until
bead `hisim-5dfc` lands. That is a bridge by owner decision (2026-09-26): `CONDITIONAL_PROBE_KINDS`
in `hisim/renovisor/verify/runner.py`, which becomes `()` when hisim-5dfc lands. CI runs it on every
push and pull request (`.github/workflows/path-verification.yml`) and uploads the directory as the
artifact **`path-verification-report`**.

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
backwards, a stage directory that carries `economic_inputs.json` but no `mapping_report.json`, any
parameter key it does not accept — and exit 3 for an engine failure they cannot. There is no exit 2
without the file.

Its `--parameters` file is the document's own `parameters` block, so a reader can feed a
document's assumptions back in unchanged:

```json
{
  "horizon_years": 20,
  "interest_rate": 0.03,
  "perspective_id": "brownfield_net",
  "financing": { "kind": "cash" },
  "subsidy_mode": "full"
}
```

Every key is optional: `horizon_years`, `interest_rate`, `country`, `price_basis_year`,
`perspective_id`, `subsidy_mode` (`full` | `none`), `financing` (`{"kind": "cash"}` or
`{"kind": "loan", "financed_share"?, "nominal_interest_rate"?, "term_in_years"?}`), `escalation`,
and the two that are accepted and ignored because they describe the run rather than state an
assumption, `simulation_year` and `subsidy_catalog`. **The country and the price basis year come
from the stages** — they are the ones their jobs were priced with, read from a stage's stored
evaluation (`lifecycle_costs.json`) or, for a stage directory that holds only the extract and the
mapping report, from the `country` and `price_basis_year` keys `economic_inputs.json` carries — so
a value here is only checked against theirs and is refused when it differs; stages that state
neither anywhere over a file that states neither is a refusal too, never a silently substituted
`"DE"` and never a basis year re-derived from the simulation year. What the file does not name stays what the stages were priced
under. A `--perspective` flag must agree with a `perspective_id` in the file. The subsidy
catalogue needs no flag: `--subsidy-catalog` wins where it is given, and otherwise the shipped
`hisim/subsidy_catalog` directory is used when it holds `<COUNTRY>.json`, exactly as a translated
run resolves it; a country that ships none runs with no catalogue and the document says
`subsidy_catalog: null` with every subsidy row undetermined.

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
