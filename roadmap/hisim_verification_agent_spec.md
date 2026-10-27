# Implementation spec: Verification suite for the HiSim building model

**Audience:** a coding agent (e.g. Claude Code) working in the HiSim repository, plus the
human reviewer.
**Status:** Draft, 2026-10-15
**Related:** HiSim SoftwareX paper, section "Verification suite" and illustrative example
"Verification against ANSI/ASHRAE 140 and EN 15265".

---

## How to use this spec

- **Human:** complete the checklist in §A before handing the spec to the agent. Items
  marked *before step N* can be finished later, but must be done before the agent reaches
  that implementation step (§11).
- **Agent:** read §0, then start with the gap analysis (§5) and stop for review. Do not
  start feature work before the human has confirmed the gap analysis.

## A. Human checklist (before handover)

### Documents and data
- [ ] Obtain ANSI/ASHRAE 140-2023 (ordered via the institute library). *Before step 6.*
- [ ] Obtain EN 15265:2007 (withdrawn edition; see §7.1). *Before step 7.*
- [ ] Download the IEA BESTEST report (1995) and the ASHRAE 140-2023 supplemental files
      (§7.1) and place them where the agent can read them. *Before step 3.*
- [ ] Fill in `ashrae140_ranges.csv` from the standard / BESTEST report (§7.3). *Before step 6.*
- [ ] Select the EN 15265 tests to implement and fill in their reference file. *Before step 7.*

### Decisions
- [ ] Licence: may ASHRAE 140 / IEA BESTEST / EN 15265 reference results be published in
      the repository? Until answered, treat them as private (§12).
- [ ] Location of private reference data (private repo or storage) and name of the CI
      secret. *Before step 8.*
- [ ] CI frequency per time step (proposal: 60 and 15 min nightly, 1 min weekly). *Before step 8.*
- [ ] Warm-up period for all cases (proposal: as specified in the standards; otherwise
      one month). *Before step 3.*
- [ ] Output folder name: `verification_output` (recommended) or `validation_output`.

## 0. Rules for the agent

1. **Explore before you build.** Your first deliverable is the gap analysis in §5. Stop
   after it and wait for confirmation.
2. **Never invent reference values.** All reference numbers come from files provided by
   the human (§7). If a reference file or value is missing, the test is **skipped** with
   a clear message — it must never pass silently and must never use a made-up number.
3. **Never tune the model to pass.** Do not change the 5R1C equations, coefficients or
   defaults to make a test pass. Report deviations; the human decides whether they become
   known deviations (§8.3).
4. **Values marked [verify]** are from memory. Take the authoritative values from the
   documents provided by the human, not from this spec.
5. **Match the repository's style:** package layout, naming, typing, docstrings, logging,
   test conventions and CI setup. Read `CONTRIBUTING`/`README` and existing tests first.
6. **Do not download data from the internet.** The human provides all documents and data.
7. **Small, reviewable PRs** in the order of §11. Each step must leave the test suite
   green (verification tests may be skipped until their data exists).
8. **Ask instead of guessing** when a case definition is ambiguous (e.g. solar radiation
   on vertical surfaces in EN 15265). Note the question in the PR description.

## 1. Goal

Add a verification suite to HiSim that

- runs two suites for the building thermal model:
  1. ANSI/ASHRAE Standard 140 (IEA BESTEST) thermal fabric cases,
  2. EN 15265:2007 test cases,
- compares **heating demand, cooling demand and room temperature**,
- runs every case at **three time steps: 60 min, 15 min and 1 min**,
- **fails** (pytest / CI) when deviations exceed the defined acceptance criteria,
- exports all results to a `verification_output/` directory (tables, JSON, plots,
  summary report) that can be used directly in the paper.

Terminology: this is **verification** against reference solutions and reference
programs, not validation against measured data. Use "verification" in code, docs and
outputs.

## 2. Scope

### In scope (phase 1)
| Suite | Cases | Compared quantities | Reference resolution |
|---|---|---|---|
| `ashrae140` | 600, 900, 600FF, 900FF | annual heating and cooling energy, peak heating and cooling load, annual min/max/mean zone temperature (FF) | annual values / hourly (as published) |
| `en15265` | tests selected by the human from the 12 tests of EN 15265:2007 | heating and cooling demand, room temperature as required by the standard | as defined in the standard [verify] |

### Time steps
- Every case is simulated at **3600 s, 900 s and 60 s**.
- References exist only at the published resolution. HiSim results at all three time
  steps are aggregated to that resolution and compared (FR10).
- The differences between the three time steps are reported separately (time-step
  consistency, §8.4). This is not a sub-hourly verification; sub-hourly references do
  not exist in these standards.

### Out of scope
- Code-to-code comparison with EnergyPlus: carried out separately by a partner
  organisation for the paper.
- HVAC component tests (heat pumps, storage, PV).
- Validation against measured data.
- Time-varying ventilation; all cases use constant infiltration/ventilation.
- Changes to the physics of the building model.
- Further ASHRAE cases (610, 620, 640, 650, 9xx variants).

## 3. Context: HiSim building model

- Building model: 5R1C according to EN ISO 13790 (hourly method).
- Usual parametrisation: TABULA building types.
- For the test cases the building must be parametrised **explicitly** from geometry,
  constructions and windows.
- EN ISO 13790 formulates the method for a 1-hour time step. Whether HiSim's
  implementation is consistent at 15 min and 1 min is checked in the gap analysis.

## 4. Functional requirements

| ID | Requirement |
|---|---|
| FR1 | Test cases are defined declaratively in YAML files, one file per case, validated against a schema. |
| FR2 | The building can be created from an explicit description (zone geometry, opaque constructions with layers or U-value + heat capacity, windows with orientation, area, U-value, g-value). |
| FR3 | 5R1C parameters (e.g. H_tr_em, H_tr_ms, H_tr_is, H_tr_w, H_ve, C_m, A_m) are derived according to EN ISO 13790 and written to the output for traceability. |
| FR4 | Ideal heating and cooling with setpoints and optional power limits; output is delivered thermal energy/power. |
| FR5 | Free-floating mode (no heating/cooling). |
| FR6 | Weather input from the file specified in the case, with a fixed, documented interpolation to sub-hourly time steps (default: linear). No implicit default weather. |
| FR7 | Constant or scheduled internal gains with radiative/convective split as specified; constant infiltration in ACH. |
| FR8 | Simulation time step selectable per run: 3600 s, 900 s, 60 s. All suites run as a matrix over these time steps. |
| FR9 | Outputs per case and time step: annual and monthly heating/cooling energy, peak heating/cooling load with timestamp, time series of zone air (and operative, if available) temperature and heating/cooling power, annual min/max/mean temperature. |
| FR10 | Aggregation of HiSim time series to the reference resolution (mean for temperatures and power, sum for energy), documented in the output. Peak loads and min/max temperatures compared with references are computed from the aggregated series; sub-hourly peaks are reported for information only. |
| FR11 | Comparison with reference data according to §8; result status per output: `pass`, `fail`, `known_deviation`, `skipped`. |
| FR12 | pytest integration: one test per (suite, case, time step, output); failing comparisons fail the test (§9). |
| FR13 | Export of all results to `verification_output/` (§10). |
| FR14 | CLI entry point, e.g. `python -m hisim.verification --suite ashrae140 [--case 600] [--timestep 900] [--output-dir ...]`. |
| FR15 | CI job running the suites (§9.3). |

## 5. Step 1: Gap analysis (first deliverable)

Fill in this table in `docs/verification/gap_analysis.md` and link it in the PR
description:

| Capability | Where in code | Status (exists / partial / missing) | Proposed change |
|---|---|---|---|
| Explicit building parametrisation without TABULA | | | |
| EN ISO 13790 parameter derivation from constructions | | | |
| Window orientation, area, U, g, frame fraction | | | |
| Ideal heater/cooler with setpoints and power limit | | | |
| Free-floating run | | | |
| Weather from arbitrary file (TMY/EPW) | | | |
| Sub-hourly weather interpolation | | | |
| Internal gains with schedule and rad/conv split | | | |
| How gains are distributed to 5R1C nodes | | | |
| Infiltration/ventilation as constant ACH | | | |
| Time steps 3600/900/60 s: supported? numerically consistent? | | | |
| Warm-up period | | | |
| Outputs of zone air / operative temperature and HVAC power | | | |
| Run time of an annual simulation at 60 s | | | |
| Existing test framework, markers, CI workflows | | | |

**Stop and wait for confirmation.** In particular, flag any item that needs changes to
public interfaces of existing components, and any sign that the 5R1C implementation is
not consistent at sub-hourly time steps.

## 6. Directory layout (adapt to repo conventions)

```
hisim/verification/
  __init__.py
  __main__.py              # CLI (FR14)
  schema.py                # case file schema (pydantic or repo's validation approach)
  case_loader.py           # YAML → validated case object
  system_builder.py        # case object → HiSim system (building, ideal HVAC, weather, gains)
  runner.py                # run simulation for a given time step, collect outputs
  aggregate.py             # aggregation to reference resolution (FR10)
  metrics.py               # annual/monthly sums, peaks, min/max/mean, RMSE
  compare.py               # acceptance logic (§8)
  export.py                # verification_output (§10)
  plots.py
  suites/
    ashrae140/
      suite.yaml           # metadata, case list, defaults, reference source, time steps
      cases/case600.yaml, case900.yaml, case600ff.yaml, case900ff.yaml
    en15265/
      suite.yaml
      cases/...            # tests selected by the human
reference_data/            # public reference data only (§7.2)
tests/verification/
  test_schema.py           # fast: all case files valid
  test_parameter_derivation.py  # fast: 5R1C derivation vs. hand calculation
  test_aggregation.py      # fast: aggregation logic
  test_compare.py          # fast: acceptance logic with synthetic numbers
  test_verification_suites.py   # slow: runs cases, marker `verification`
docs/verification/
  README.md                # purpose, usage, limitations, data/licence notes
  gap_analysis.md          # step 1
```

## 7. Reference data

Reference data are kept **separate from code and case definitions**, because their
licence may differ (§12).

### 7.1 Documents required for this spec

Only these documents are needed to implement the suite. The human provides them (rule 6).

| Document | Needed for | Source ID | How to obtain | Status |
|---|---|---|---|---|
| ANSI/ASHRAE 140-2023 | Case definitions and acceptance criteria for `ashrae140` | `ashrae140_2023` | institute library / [ASHRAE bookstore](https://www.ashrae.org/bookstore) | ordered |
| ASHRAE 140-2023 supplemental files | Weather file and results templates for `ashrae140` | `ashrae140_2023_supplemental` | free: [Standard 140 resource files](https://data.ashrae.org/standard140/accompany.html) | to download |
| IEA BESTEST report (Judkoff & Neymark 1995, NREL/TP-472-6231) | Freely citable case definitions and reference program results for 600/900 (public baseline) | `iea_bestest_1995` | free: [OSTI](https://www.osti.gov/biblio/90674), [PDF (UNT library)](https://digital.library.unt.edu/ark:/67531/metadc670858/m2/1/high_res_d/171372.pdf) | to download |
| EN 15265:2007 (withdrawn; replaced by EN ISO 52016-1 / 52017-1) | Test definitions, reference results and criteria for `en15265` | `en15265` | institute library / [DIN Media](https://dinmedia.de/en/standard/din-en-15265/96624119); cite as "EN 15265:2007 (withdrawn)" | to order |

### 7.2 Storage and loading
| Source ID | Location |
|---|---|
| `iea_bestest_1995` | public, `reference_data/iea_bestest_1995/` (if licence check OK, else private) |
| `ashrae140_2023` | private |
| `ashrae140_2023_supplemental` | private until redistribution terms are checked |
| `en15265` | private |

- Private data: directory given by environment variable `HISIM_VERIFICATION_DATA`
  (CI: from a secret / private repository checkout).
- Each suite declares in `suite.yaml` which source it uses. If the data are not
  available, all tests of that suite are **skipped** with the message
  `"Reference data '<source_id>' not found (set HISIM_VERIFICATION_DATA)"`.

### 7.3 Format
```csv
# <source_dir>/ashrae140_ranges.csv
case,output,unit,min,max,median,source_note
600,annual_heating,MWh,,,,"Table X, p. Y"
600,annual_cooling,MWh,,,,
600,peak_heating,kW,,,,
600,peak_cooling,kW,,,,
600FF,max_temperature,degC,,,,
```
EN 15265 references use an analogous file, defined once the standard is available.
Values are filled in by the human. Empty values → `skipped`.

## 8. Comparison and acceptance logic

### 8.1 ASHRAE 140 / BESTEST (range-based)
- `pass` if `min ≤ value ≤ max` of the reference program range.
- Optional suite-level `range_margin` (default 0); if used, report it in the output.
- If ASHRAE 140-2023 defines formal acceptance criteria that differ from the plain range,
  implement them as a separate criterion type selected in `suite.yaml`.
- Always report: value, min, max, median, relative position in range, deviation from
  median in %.

### 8.2 EN 15265
- Use the acceptance criteria / accuracy levels defined in the standard [verify];
  implement as a criterion type configured in `suite.yaml`.

### 8.3 Known deviations
A case file may declare:
```yaml
known_deviations:
  - output: max_temperature
    timestep_s: 3600          # optional; omit for all time steps
    reason: "Single-capacity 5R1C model underestimates temperature swing in heavyweight free-floating case"
    approved_by: "<name>"
    approved_on: "YYYY-MM-DD"
```
- Status `known_deviation`; pytest marks them `xfail(strict=False)`.
- Only the human adds entries. The agent may propose them in the PR description.

### 8.4 Time-step consistency (reported, not pass/fail)
For each case: differences in annual demand, peak loads and temperatures between the
3600 s, 900 s and 60 s runs. Shown in the report and in a dedicated plot.

## 9. Testing and CI

### 9.1 pytest
- Marker `verification` for slow suite tests (register in pytest config).
- Parametrise by (suite, case, time step, output), e.g.
  `test_verification[ashrae140-600-900-annual_heating]`.
- Each (case, time step) is simulated **once** per session (session-scoped cache).
- Failure message: value, unit, status, deviation and path to the exported plot. Do not
  print private reference values in logs.
- Default `pytest` excludes `verification`; run with `pytest -m verification`.
- Option to restrict time steps, e.g. `--verification-timesteps 3600,900`.

### 9.2 Fast tests (every push/PR)
- Schema validation of all case files.
- 5R1C parameter derivation against a hand calculation for case 600.
- Aggregation and comparison logic with synthetic values.

### 9.3 CI workflow (adapt to existing CI, likely GitHub Actions)
- Fast tests: on every push/PR (existing workflow).
- Verification suite: scheduled and on `workflow_dispatch`; frequency per time step as
  decided in §A.
- Private reference data fetched via secret into `HISIM_VERIFICATION_DATA`. Without the
  secret (e.g. forks) the affected suites skip, not fail.
- Upload a **reduced public report** (HiSim values and status, no private reference
  values) as CI artifact; write the full report to private storage.
- The job fails if any comparison has status `fail`.

## 10. Export: `verification_output/`

```
verification_output/
  summary.md                 # human-readable report (table per suite and time step, status counts)
  summary.json               # machine-readable: metadata + all results
  results.csv                # one row per (suite, case, time step, output)
  parameters/<case>.json     # derived 5R1C parameters + key inputs (FR3)
  timeseries/<case>_<timestep>.csv   # zone temperature, heating/cooling power
  plots/
    ashrae140_annual.png     # value as point within reference range per case/output and time step
    ashrae140_ff_temps.png
    en15265_<case>.png
    timestep_consistency.png
```

- `summary.json` metadata: HiSim version and git commit, date/time, Python version, time
  steps, weather interpolation method, suite versions, reference source IDs,
  `range_margin`, criteria used.
- `results.csv` columns:
  `suite,case,timestep_s,output,unit,value,ref_value,ref_min,ref_max,criterion,deviation_abs,deviation_rel_pct,status,note`
- A `--public` flag produces the reduced report without reference columns (§9.3).
- Plots publication-ready; `--plot-format pdf|svg|png`.

## 11. Implementation order (one PR each)

| Step | Content | Needs from §A |
|---|---|---|
| 1 | Gap analysis (§5) — **stop for review** | – |
| 2 | Missing capabilities in HiSim (explicit building, ideal HVAC, weather input and interpolation, gains, time-step support), with unit tests | confirmed gap analysis |
| 3 | Schema, case loader, system builder; case 600 runs end to end at all three time steps | IEA BESTEST report, supplemental files, warm-up decision |
| 4 | Aggregation, metrics, comparison logic, fast tests (§9.2) | – |
| 5 | Export (§10) and CLI (FR14) | – |
| 6 | ASHRAE 140 suite + pytest integration (§9.1) | ASHRAE 140-2023, filled reference file |
| 7 | EN 15265 suite | EN 15265:2007, test selection, filled reference file |
| 8 | CI workflow (§9.3) and documentation (`docs/verification/README.md`) | private data location, CI secret, CI frequency |

## 12. Licensing notes (for the human; the agent does not decide)

- Do not commit text, tables or PDFs copied from ANSI/ASHRAE 140 or EN 15265.
- Case definitions as model input parameters are commonly implemented in open-source
  tools; publishing reference results from current standards needs clarification.
- Public baseline: IEA BESTEST 1995 (NREL report).
- EN 15265 is distributed by national standards bodies (in Germany DIN Media); terms for
  reproduction in software are likely similar to those of the VDI. Treat as private.
- Freely downloadable is not the same as freely redistributable: check the terms of the
  ASHRAE supplemental files before committing them.
- VDI 6007-1 is not used: its terms for use in software require written permission and
  licence fees, the full text must be shipped with the software, and transfer into
  databases is prohibited.

## 13. Definition of done

- [ ] Gap analysis reviewed and missing capabilities implemented with unit tests
- [ ] `pytest -m verification` runs both suites at 3600 s, 900 s and 60 s
- [ ] Tests fail on `fail`, skip without data, xfail on approved known deviations
- [ ] `verification_output/` contains summary.md/json, results.csv, parameters, time series, plots incl. time-step consistency
- [ ] Reduced public report available via `--public`
- [ ] CLI works for single case, single time step and whole suite
- [ ] CI jobs run on schedule and upload the public report
- [ ] `docs/verification/README.md` explains purpose, usage, data handling, limitations
- [ ] No reference value in the repository that was not provided by the human

## 14. Related literature

Not needed to implement this spec. Grouped by when it is useful.

### For understanding (useful now)
| Literature | Why |
|---|---|
| [ASHRAE 140 User's Manual, Part I (PDF)](https://data.ashrae.org/standard140/140UsersManual-PartI-Final%20(050825).pdf) | Overview of how the ASHRAE 140 test procedure is applied |
| [Solina et al. 2018, E3S Web of Conferences](https://www.e3s-conferences.org/10.1051/e3sconf/20184900040) | Free description of the 12 EN 15265 tests and their application; notes ambiguities in the standard |
| Michalak (2019), Energy and Buildings 202, 109337, [DOI](https://doi.org/10.1016/j.enbuild.2019.109337) | Precedent: 5R1C model verified against BESTEST, EN 15265 and EnergyPlus; comparison values for the paper |
| Michalak (2017), Energy and Buildings (Simulink 5R1C model) | Earlier verification of a 5R1C implementation |
| Lauster et al. (2014), Building and Environment 73, 223–231 | How a reduced-order RC model (VDI 6007, TEASER/AixLib) was verified with standard test cases |

### For later extensions
| Literature | Relevant when |
|---|---|
| [ASHRAE 140-2023 Addendum a (PDF)](https://www.ashrae.org/file%20library/technical%20resources/standards%20and%20guidelines/standards%20addenda/140_2023_a_20250829.pdf) | Adding the time-series acceptance criteria for thermal fabric tests |
| [ASHRAE 140-2023 Addendum b (PDF)](https://www.ashrae.org/file%20library/technical%20resources/standards%20and%20guidelines/standards%20addenda/140_2023_b_20260424.pdf) | Adding empirical (measured-data) test cases |
| EN ISO 52016-1:2017 and EN ISO 52017-1:2017 | Successors of EN ISO 13790 / EN 15265; check for successor test cases |
| VDI 6007-1:2015-06 | Only if written permission from the VDI is obtained |
| EN 14825 / Heat Pump Keymark data | Adding heat pump component checks |
