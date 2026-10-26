# Golden KPI References

This directory holds the **committed golden KPI references** for HiSim — one JSON
file per `(system setup, parameter set)` pair. They are the known-good baseline the
golden-reference regression gate compares against, so an unintended change to
simulation output (e.g. after a numerical refactor) fails CI with the exact KPIs
that moved.

The scripts that drive this live in [`../scripts/`](../scripts); the full design is
in [`../roadmap/golden_ref_spec.md`](../roadmap/golden_ref_spec.md).

## What is compared

Each run writes `all_kpis.json` (HiSim's KPI collection, produced when a parameter
set enables both `COMPUTE_KPIS` and `WRITE_KPIS_TO_JSON`). Every KPI in it is
resolved by its address (`KpiFinder`, `hisim/postprocessing/kpi_computation/kpi_address.py`)
and stored here, in `<setup_id>__<param_id>.json` (keys sorted, two-space indent), as one
**leaf** keyed by its dotted address (`KpiAddress.dotted`):

```json
{
  "BUI1.Building.Conditioned floor area (Building)": {
    "value": 121.2,
    "unit": "m2",
    "building": "BUI1",
    "tag": "Building",
    "name": "Conditioned floor area",
    "source": {"import": null, "instance": null, "path": [], "member": "Building", "assembly": null, "name": "Building"}
  },
  "BUI1.General.Self-sufficiency rate of electricity": {
    "value": 41.2, "unit": "%", "building": "BUI1", "tag": "General",
    "name": "Self-sufficiency rate of electricity", "source": null
  }
}
```

- `value` is the entry's value (a number, stored as a float; a string; or `null`), `unit` the
  entry's unit verbatim.
- `source` holds the five identity fields of the entry's `KpiSource`; its `display_name` and
  `label` are presentation and stay out. It is `null` for a derived KPI.
- **Identity rule.** The key is the dotted address the leaf's own fields produce, and every
  reader checks it: a leaf whose key disagrees with its fields, that lacks or adds a field, or
  a golden in the old flat form (`{"BUI1.Building.Conditioned floor area": 121.2}`, a bare value
  per key) is refused by `golden_check.py` (before anything runs) and by `golden_update.py`, with
  the instruction to re-bless through the `golden-update` workflow with `force_rewrite`, which
  writes the fresh leaves without reading the old file.

Values are compared numerically with a tight tolerance (`rel_tol = 1e-9`, `abs_tol = 1e-12`, so a
physically zero KPI's rounding noise compares equal);
non-numeric values exactly. **Unit rule:** the unit is compared exactly, and a changed unit is a
failure of its own kind — listed as `[unit]` in `report.txt`, under `unit_changes` in
`report.json`, and counted separately in the summary — whatever the value did. The address
fields under one key are compared exactly too. **Only KPIs are compared** — never plots,
PDFs, logs, or raw CSVs (those are non-deterministic and/or platform-dependent).

`manifest.json` is an informational sidecar (git commit, Python, platform, config
hash, list of golden files). The checker does **not** read it.

## Configuration

[`../scripts/golden_config.json`](../scripts/golden_config.json) is the single,
hand-maintained source of truth for **which** setups and parameter sets are in the
gate. Edit it to add or remove a setup; everything downstream (runner, check,
update, CI matrix) follows. An entry's `path` names a Python setup (`system_setups/<file>.py`)
or an energy system written in YAML alone (`energy_systems/<file>.energy_system.yaml`, such as
`gas_boiler_household`), which runs the way `hisim energy-system run` runs it. Every parameter
set must enable the cost stages plus the
KPIs — `["COMPUTE_OPEX", "COMPUTE_CAPEX", "COMPUTE_KPIS", "WRITE_KPIS_TO_JSON"]`, so
the building-level cost KPIs are pinned — and every setup must be **offline-runnable**
and **KPI-complete**.

The parameter sets, each run by every setup:

| id | window | step | tier |
|---|---|---|---|
| `one_week_60s` | first week of January | 60 s | `golden-check` |
| `july_week_60s` | first week of July | 60 s | `golden-check`, when dispatched with the `july_week` horizon |
| `full_year_60s` | 2021 | 60 s | `golden-year` |
| `one_week_900s` | first week of January | 900 s | `golden-check` |
| `full_year_900s` | 2021 | 900 s | `golden-year` |

A tier picks its sets by their `SimulationParameters` factory (`one_week_only`, `one_week_july`,
`full_year`; `scripts/golden_horizons.py`), not by their id, so a set of another step length joins
the tier of its window with no change to the scripts or the workflows. The step length is the
set's `seconds_per_timestep`; its id names it too, ending in `_60s` or `_900s`.

**Why 900 s.** RenoVisor runs every calculation at 900 s (`hisim/renovisor/simulation.py`), and
some code acts only at steps above the part-load threshold of 600 s: the part-load rules
(`hisim/part_load.py`), `FluidStream.at_part_load` (`hisim/hydronics.py`), and the start-of-step
node of the buffer and of the hot-water tank. At 60 s none of it runs. Without the 900 s sets a
change to it passed the gate unless it moved a total of the time-resolution tests
(`tests/test_space_heating_time_resolution.py`, `tests/test_dhw_chain_time_resolution.py`) by about
1 % (owner decision 2026-10-26, bead hisim-repo-cleanup-o6a.35). Those tests stay: they check the
physics across step lengths, while the 900 s goldens catch any change of behaviour, and a pull
request that changes the 900 s results on purpose needs a bless as one changing the 60 s results
does.

Validate the setups with:

```bash
python scripts/golden_validate.py            # vet the setups listed in the config
python scripts/golden_validate.py --scan-all # probe every system_setups/*.py
```

## Checking (the gate)

```bash
python scripts/golden_check.py                              # all pairs
python scripts/golden_check.py --setup <id> --param <id>    # one pair
python scripts/golden_check.py --pairs <id>:<id> ... --jobs 4 # a CI shard, four pairs at once
python scripts/golden_check.py --mode yaml ...               # the recorded YAML twins
python scripts/golden_check.py --mode composed ...           # the composed files
python scripts/golden_check.py --mode both ...               # python and yaml, side by side, one report each
python scripts/golden_check.py --mode all ...                # the three, side by side, one report each
python scripts/golden_check.py --mode python composed ...    # any modes, side by side
```

Re-runs the pairs, compares KPIs to the goldens here, writes
`results/golden-ref-check/report.{txt,json}` (`golden-ref-check-yaml/` in YAML mode,
`golden-ref-check-composed/` in composed mode), and exits non-zero on any deviation,
unit change, missing or unusable golden, or run failure. Missing and unusable goldens fail
**before** running, so they never waste compute.

### The three modes

Every mode compares with the same goldens, which are blessed from the Python runs; none ever
compares with an earlier run of its own.

- **`python`** runs the `.py` setup: *the reference*. A divergence here is a change of the
  simulation itself.
- **`yaml`** runs the setup's recorded twin, `energy_systems/<stem>.energy_system.yaml`, through
  the declarative executor: *the recorded twin reproduces the reference*. The energy manager's
  `Priority for <port>` KPIs quote an aggregator port, which the two paths name differently
  (C-P3.2), so this mode excludes that family from both sides (`PORT_NAMED_KPIS`, printed per pair).
  An entry that is an energy-system file has no twin; this mode runs that file itself.
- **`composed`** runs the setup's composed file, `energy_systems/<stem>.composed.energy_system.yaml`
  (the site plus imports of `energy_systems/assemblies/`): *the assemblies reproduce the reference*.
  Its KPIs carry the members' addresses (`heating-HeatPump`, with an import, a path and an assembly),
  so each is renamed to the twin's site component through the rename map of the setup's entry in
  `hisim/energy_system/assemblies/twins.py` (`rename_address`; a member the map does not name fails
  the run). Nothing is excluded: the golden's `Priority for <legacy port>` KPIs are read under the
  declarative port names through the parity rig's declared table (`scripts/p3_parity_renamings.py`,
  printed per pair), and a port the table does not declare fails as a missing and a new KPI. A setup
  without an entry in that table is listed as `SKIPPED` in the composed report and does not fail it.

In CI this runs as two tiers (see `.github/workflows/`):

- **`golden-check.yml`** — the January-week pairs of both step lengths (`one_week_60s`,
  `one_week_900s`), on every PR and push to `main`, each pair's Python
  setup, its recorded YAML twin and, where it has one, its composed file side by side
  (`golden_check.py --mode all`, one report per mode), in four shards
  (`scripts/golden_matrix.py --shards 4 --with-yaml --with-composed`) that run four simulations at
  once each. A diverging YAML twin or composed file fails it. The shards are
  balanced by each pair's measured duration, `"seconds"` in `scripts/golden_config.json`;
  `report.json` records a fresh `duration_s` per pair when they need refreshing. The 900 s weights
  are not CI measurements yet: each was measured on a workstation and scaled by its setup's ratio of
  CI to workstation time at 60 s, and a full-year weight that came out below its week's was raised
  to the week's (2026-10-26). Refresh them from the `duration_s` of the first CI runs at 900 s.
- **`golden-year.yml`** — the full-year pairs of both step lengths (`full_year_60s`,
  `full_year_900s`), on every PR and push to `main`, starting at the
  same time as `golden-check` and the rest of CI on the self-hosted runners and reporting as
  ordinary checks of the pull request; a pull request from a fork skips it. One job per 60 s run
  (`golden_matrix.py --runs --with-composed`, `golden_check.py --jobs 1 --mode <mode> --pairs
  <pair>`): each Python setup's full-year pair and, separately, where the setup has one, its
  composed file's, so the tier takes as long as its slowest run. The 900 s years run in about a
  minute each, too short to be worth a job each, so they are packed into four shards instead
  (`golden_check.py --mode python composed --jobs 2 --pairs <its pairs>`), 35 jobs in all; see
  "Packed parameter sets" below. Every setup of the config runs the full year (owner
  decision 2026-10-11), because the first week of January never runs a solar collector's pump;
  only a setup that cannot finish a year yet carries `"horizons": ["week"]`, named with its bead
  in `CANNOT_RUN_A_FULL_YEAR` of `tests/test_golden_matrix.py` (today none). The energy-system file
  `gas_boiler_household`, which has no Python setup, runs the full year too, as its own entry (see
  Configuration).

A missing golden is never a silent pass. `golden_check.py` looks for the golden of every pair it was
given before it runs any, and when one is absent it runs nothing, lists the pair as `[MISSING
GOLDEN]` and exits non-zero: in `golden-check` that fails the whole shard holding the pair, in
`golden-year` the one job of the pair, or the 900 s shard holding it. `golden-update` blesses every pair of the config, so the next
bless writes the missing file: it runs the pair and, finding no file to merge onto, writes the fresh
leaves as they are. That is how the 900 s sets got their first goldens.

### Packed parameter sets

golden-year and the bless give each full-year pair a job of its own, because a 60 s year runs for up
to twelve minutes. A parameter set whose runs are short opts out of that explicitly, with
`"pack_into_shards": N` in its entry of `scripts/golden_config.json`: where a tier would give each of
its pairs a job, they go into N shards of their own instead, packed by their `"seconds"` with the same
rule as golden-check's week shards (`PackedParameterSets` and `_pack` in `scripts/golden_matrix.py`).
Today only `full_year_900s` carries it, with 4 (owner decision 2026-10-26). In golden-year each shard
runs the Python setups and the composed files of its pairs side by side, two runs at once; in the
bless, two pairs at once. A shard runs its pairs in child processes, each killed after three times
its recorded seconds (at least ten minutes), and fails before running anything when one of its pairs
has no golden. Elsewhere the key changes nothing: golden-check cuts the week into shards anyway. The
key is explicit rather than a threshold on the measured seconds, so that refreshing a weight never
moves a pair between a job of its own and a shard unannounced.

## Blessing (updating the goldens)

Do **not** hand-edit these files. Regenerate them via the CI workflow so the
reference environment matches the check environment:

> Actions → **golden-update** → *Run workflow* → give a reason.

It regenerates every pair of every parameter set in the CI container (a job per 60 s full-year
pair, four shards for the 900 s years, four for each week; 35 jobs) and opens a PR with
the updated `golden_references/`. Review the per-KPI diff, then merge — that merge
is the bless. (`scripts/golden_update.py` can be run locally for inspection, but
locally produced goldens are not the canonical committed ones. A local run is
sticky like the CI one — every leaf the gate would still accept (value within
tolerance, unit and address fields equal) stays exactly as committed, a changed unit
always reaches the diff, and nothing is dropped — so add `--force-rewrite` to see the
fresh leaves verbatim.)

## Seeing how the references moved

The git history of this directory is a record of which KPI moved in which pull
request. To draw it:

```bash
python scripts/golden_history.py                    # every pair, PNG + HTML
python scripts/golden_history.py --pairs household_oil_building_sizer --since 2026-09-01
```

Output lands in `results/golden_history/` (gitignored): one figure per pair in both
formats, an `index.html` linking them, a shared `plotly.min.js` the pages reference
relatively, and one `moves.csv` — `commit, date, pr, pair, kpi, previous, value,
relative_change` for every value that changed beyond the gate's tolerance, which is
the greppable answer to "what moved when". Nothing is committed and no CI job runs it.

A figure is one panel per KPI. The x axis is the commits that changed *this* pair's
file, labelled with the date and the PR number; the y axis is the KPI's change
against its first recorded value, in percent. A KPI whose first value is exactly `0`
has no percentage, so its panel shows the absolute change and is marked `[abs]`.
Non-numeric values (`null`, strings) are skipped and leave a gap. Panels are sorted
with the largest total movement first — the three largest carry their rank — and a
KPI that never moved is drawn in grey, so the eye lands on the movers.

The walker is the one reader of *historical* goldens, so it reads both forms the files have
had. A leaf-form series is identified by its fields — `building`, `tag`, `name` and the
source's `import`, `instance`, `member` — so a later change of the serialized source name
(the key's suffix) continues one series with no table entry. A flat-form series is identified
by its dotted key; it continues into a leaf series when the flat key equals the leaf's dotted
key, or its bare `building.tag.name` (the form the old scheme used while a name was unique). A
flat key that could continue two leaf series is refused by name, as is a file mixing the forms.

Flat-form renames are stitched back into one series through
[`../scripts/golden_kpi_renames.py`](../scripts/golden_kpi_renames.py). To add one,
find the commit that renamed the key (diff the key sets of two neighbouring golden
commits), add the old and new spelling under the pair's stem with that commit and its
PR, and run `pytest tests/test_golden_history.py` — it checks every old name against
the real history and every new name against the files as they stand.

