# Result changes and public interfaces

How a change that alters simulation results is made, and which names other systems read, so that renaming them
is a planned change and not an accident. It applies to people and agents alike, and reviews check it. The test
rules are in `TESTING.md`, the commit and PR form in `coding_style.md` §9 and §10.

## 1. What counts as a result change

- **A result change is any KPI that moves beyond the golden tolerance** (§3) in a pair of
  `scripts/golden_config.json`, or a KPI that appears, disappears or changes its unit or address. The golden
  gate decides: on every pull request against `main`, `golden-check.yml` runs the one-week pairs and
  `golden-year.yml` the full-year pairs, both at once and beside the rest of CI. A stacked pull request gets
  neither until it is retargeted to `main`; dispatching both workflows on its branch checks it earlier.
- **A changed output that no golden KPI shows is still a result change** when the change intends it, for example
  a new output column or a component's time series that changed shape. Say so in the PR description as well.

## 2. How a result-changing change is made

1. **Refactoring and result changes are separate commits.** A commit that restructures code (renames, moves,
   extracts functions, cleans up) changes no KPI beyond the tolerance. A result change is its own commit, whose
   message says what moves and why. Why: a reviewer can then trust the refactoring on the gate's word and spend
   the review on the model change. A refactoring that moves a KPI is a bug in the refactoring, or a model change
   that must be named as one.
2. **The PR description lists the KPI deltas** in its Result section (`coding_style.md` §10): per pair, every KPI
   that moved, old value -> new value with its unit, and the reason. Take them from the gate's report
   (`report.txt` / `report.json` of `scripts/golden_check.py`, or the job summary of the CI run). Where many KPIs
   move, a table of the leading ones plus a count of the rest. Example, from #941:

   | twin, step | collector heat, kWh | backup, kWh | pump hours |
   |---|---|---|---|
   | gas + solar, 60 s | 358.7 -> 400.6 | 2761.5 -> 2719.9 | 426 -> 673 |

3. **The goldens are blessed only in CI.** Dispatch `.github/workflows/golden-update.yml` on the pull request's
   branch, with a reason that names the PR ("#942: the solar controller stops the pump at its own 80 °C
   maximum"). The workflow re-runs every pair in the CI container and opens a bless PR into that branch. Review
   its per-KPI diff against the deltas the PR description announced, then merge it into the branch, so the
   result change and its goldens reach `main` together. Why: a golden written on another machine differs in the
   last bits and fails the check (`golden_ref_spec.md` §7).
   - Never commit a golden written by a local run. `scripts/golden_update.py` may run locally to inspect what a
     bless would write, with `--golden-dir` pointing at a scratch directory: by default it writes into the
     committed `golden_references/`. Nothing it writes is committed.
   - `force_rewrite` is only for retiring a KPI, clearing accumulated noise or repairing an unreadable golden.
     A normal bless is sticky: it rewrites only the KPIs that moved beyond the tolerance.
   - A pull request stacked on a re-blessed parent is blessed again on its own branch when its own change moves
     results.
4. **A changed setup or configuration is re-recorded.** When a Python setup or a component's configuration
   changes, run `python scripts/record_all_setups.py` and commit the re-recorded twins under `energy_systems/`;
   `energy-system-freshness.yml` fails a pull request against `main` whose twins are stale.
5. **The other side is told** when the change moves what RenoVisor shows (§5).

## 3. The tolerance policy

- **Two KPI values are equal when `math.isclose(got, ref, rel_tol=1e-9, abs_tol=1e-12)` holds**, the absolute
  tolerance in the KPI's own unit. The values live in `hisim/postprocessing/kpi_computation/tolerances.py`, which
  every golden comparer reads; the reasoning is `golden_ref_spec.md` §7 (decision of hisim-b3b.6). The assembly
  harness's monotone check reads only the relative tolerance there and keeps its own absolute floor
  (`MONOTONE_ABS_FLOOR` in `hisim/energy_system/assemblies/testing/checks.py`).
- **Why so tight.** On one machine HiSim is byte-exact run to run, so the relative tolerance only absorbs
  cross-platform drift in the last bits. The absolute floor exists for KPIs that are physically zero and come
  out as rounding noise (the electric-heating house's temperature deviation below 20 °C, about 1.4e-9 K·h).
- **Units and addresses compare exactly.** A changed unit is a failure of its own kind, whatever the value did
  (`golden_references/README.md`).
- **The tolerance is not widened to make a pull request pass**, and no KPI gets a tolerance of its own. A change
  to `tolerances.py` is an owner decision.

## 4. Public interfaces

These names are read outside the code that defines them: by RenoVisor, the webtool, the recorded twins and
assemblies, and the golden references. Renaming one is a result change in the sense of §2, with the extra
steps of §5.

| Interface | Defined in | Read by |
|---|---|---|
| Component output and input names | the `ComponentOutput` / `ComponentInput` field names, as class constants of the component | the connections of energy-system files and assemblies, the result CSV columns, the webtool JSON |
| KPI addresses | the building, the `KpiEntry` name and tag, and for a component KPI its `KpiSource` (`hisim/postprocessing/kpi_computation/kpi_structure.py`, `kpi_address.py`) | the golden references (one leaf per address), RenoVisor's `KpiSources` in `hisim/renovisor/kpis.py` (guarded by `tests/renovisor/test_kpis.py`), the cost engine, the webtool |
| Configuration field names and preset names | the `ConfigBase` dataclasses and their `@preset` class methods | every `*.energy_system.yaml` and assembly file, the RenoVisor translator (`hisim/renovisor/translate.py`), `hisim energy-system describe` |
| Component class paths | `MAIN_CLASS` and the module path of the class | the `class:` key of every energy-system file and assembly |
| The energy-system file format | `schema_version` 3 (flat) and 4 (with assemblies), `hisim/energy_system/loader.py`; the committed schemas `hisim/energy_system_v3.schema.json` and `hisim/assembly_v4.schema.json` | the RenoVisor translator, editors through the `$schema` line, every committed file |
| RenoVisor's payloads | `result.json`, `mapping_report.json`, `problems.json`, `translator_error.json`, the capability document and the exit codes of `python -m hisim.renovisor` (`hisim/renovisor/how_to_use.md`) | the RenoVisor backend and frontend |

Rules:

- **A public name is referenced through its class constant**, never repeated as a string literal (AGENTS.md,
  "Guidelines for coding"), so a rename inside HiSim's Python code is one edit. The files that spell the name --
  energy-system files, assemblies, goldens -- are renamed as §5 says.
- **A new public name follows the naming rules from the start**: units in the name (AGENTS.md), `space_heating`
  not `sh` (`coding_style.md` §5). Existing names that break a rule are renamed only in a planned migration, not
  in passing.
- **A KPI name is a display label beside its mandatory `unit` field** (AGENTS.md, "Guidelines for coding"). The
  KPI "Unmet DHW heat demand" (`hisim/components/simple_water_storage.py`) is to be renamed, as one pull request
  under §5 (owner, 2026-10-15; bead hisim-h2ib.7).
- **The committed schemas follow the code.** A change to the format regenerates
  `hisim/energy_system_v3.schema.json` and `hisim/assembly_v4.schema.json` with `hisim energy-system schema`;
  `tests/test_energy_system_schema.py` and `tests/assemblies/test_assembly_format.py` fail otherwise. A change under which existing
  files can no longer be read is a new `schema_version`, as the assemblies were (version 4).

## 5. Renaming a public name

A rename is one pull request that does all of the following:

1. Renames the name at its definition and in every reader inside HiSim: the class constants, the energy-system
   files and assemblies, RenoVisor's bindings (`hisim/renovisor/kpis.py`, the translator), tests.
2. Re-records the twins (`scripts/record_all_setups.py`) and re-blesses the goldens (§2, step 3). A renamed KPI
   shows in the gate as one KPI missing and one new; the PR description lists the pairs.
3. Tells the RenoVisor side when they read the name, directly or through a payload: an issue in renovisorissues
   (https://jugit.fz-juelich.de/iek-3/groups/urbanmodels/renovisorissues) labelled `from:hisim`, `heads-up` and
   `to:` each package that reads it (`renovisorbackend`, `contract-owner`, `RenoSFHFrontend`), stating the old
   and the new name and the pull request that changes it. The PR description links the issue.
4. States in its PR description that it is a rename of a public interface, so the reviewer checks the readers.

A rename that cannot be done in one pull request, for example because the backend must change first, is a spec
with a migration plan (`roadmap/`), not a pull request.
