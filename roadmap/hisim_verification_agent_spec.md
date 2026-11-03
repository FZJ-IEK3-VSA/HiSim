# Implementation spec: Verification suite for the HiSim building model

**Audience:** a coding agent (e.g. Claude Code) working in the HiSim repository, plus the
human reviewer.
**Status:** Draft, revision 8, 2026-11-03 (revision 1: 2026-10-15; revisions 2 to 5: 2026-11-02;
revisions 6 and 7: 2026-11-03)
**Related:** HiSim SoftwareX paper, section "Verification suite" and illustrative example
"Verification against ANSI/ASHRAE 140 and EN ISO 52016-1".

**Changes in revision 8** (ASHRAE 140 [verify] marks resolved against the PDF, gap analysis §9,
2026-11-03)

- The clause and table numbers of ANSI/ASHRAE 140-2023 cited in §2, §3, §7.3 and §8.1 were
  checked in the standard and lose their [verify] marks; the few values and clauses not checked
  keep them (gap analysis §9.2). Finding F5: cases 640 and 940 ramp the heating set point
  linearly from 10 °C to 20 °C between 07:00 and 08:00 (7.2.2.1.4 c), which §3.2.10 did not
  state; `NightSetbackController` steps, so D18 is open (§3.2.10, §A).

**Changes in revision 7** (D6 revised before step 2b, gap analysis §8, 2026-11-03)

- One parameter path instead of two: the explicit building description and
  `hisim/components/building/explicit.py` are dropped. Step 2b adds overrides on the TABULA row
  for every value the 5R1C model reads (a window list with tilt, azimuth and the optics factors
  per window, both air change rates, the air volume, the transmission adjustment factors, C_m, A_m
  and A_tot) and an origin record that the suite checks: a case runs only when
  nothing of the carrier row is used (D16, revising D6; §3.1, §3.2, FR2, §11, §A).
- The §3.2 rules 1 to 4 are all applied in the suite's system builder, which passes the results
  to HiSim; rule 5 stays HiSim's. `Building.get_windows` takes the window geometries from
  `BuildingInformation` (tilt, azimuth and optics per window, so F_w per orientation for cases 620
  and 920); the volume replaces the room height (D17, folded into 2b).

**Changes in revision 6** (re-check against `origin/main` after the merge of 2026-11-03, gap
analysis §7)

- The mass-state finding F1 is fixed on `main` (PR #1079): the state carries θ_m,t. The F1
  register entry leaves §3.1 and §8.3; bead hisim-x6c7.1 is proposed for closure (D15).
- The ideal heater has an L1 controller on `main` (PR #924); the suite wires Building →
  controller → heater → Building (§3.1, FR4). The setback input is
  `BuildingTemperatureModifierInKelvin` (§3.2.10, FR4).
- Every test carries exactly one job marker (`system_docs/testing.md` §2); the marker
  `verification` needs D14 (§5, §9.1, §9.2, §11 step 2a, §A).
- The EPW reader also supplies the relative humidity and an entry in the station-header reader;
  the weather is window-aware on `main` (gap analysis §7.2 row 9), the rest of FR6 is unchanged.

**Changes in revision 5** (decisions on §3.2.1, 3.2.3 and 3.2.4; standards opened, 2026-11-02)

- §3.2.1, 3.2.3 and 3.2.4 decided as recommended: printed U-values and no thermal bridges; the
  ISO 13786 effective-thickness rule with all three conditions; actual A_tot and A_m by formula
  (gap analysis D10 to D12).
- The explicit building description takes κ per element; the layer-to-κ rule of §3.2.3 is applied
  in the suite's system builder, not in HiSim (FR2, §11 steps 2 and 3; two step-2 PRs).
- ISO 13790 is available as the FDIS text of 2007, the text HiSim was built from; ISO 13786 is
  not available. The ISO 13790 [verify] marks of §3.1, §3.2 and Appendix B are resolved against
  the FDIS; conditions (a) and (b) of §3.2.3 stay [verify] (§7.1).
- The agent reads the standards in the private folder to resolve [verify] marks; only clause and
  table numbers and the suite's own derived values enter the repository (§A, §12). Added
  2026-11-03: the standards are read in place; no text of a standard is extracted or copied
  into any file, not even a temporary one.
- Added 2026-11-03: `verification_spec` branched from `main` on 2026-10-12 and `main` has moved
  since (building, weather, CSV loader, component base, pytest configuration). Before step 2,
  `origin/main` is merged into `verification_spec` and the gap-analysis rows for the changed
  files are re-checked (§A).

**Changes in revision 4** (decisions of the human on the gap analysis,
`docs/verification/gap_analysis.md`, 2026-11-02)

- The mass-state finding F1 (state = mid-step average, 2·C_m dynamics) is recorded as a `model`
  register entry; no code change before the first full report (§3.1, §8.3).
- The thermostat is the Building's C.4 step; the ideal heater stays unchanged; air control in
  every suite (§3.1, §3.2.10, FR4).
- §3.2.8 and §3.2.9 resolved; ground reflectance 0.2 is the standard's value (F3, F4).
- Warm-up: two-pass run instead of a prepended month (§3.2.11).
- The `iso52016` suite is deferred: feeding irradiance per orientation needs a new Building
  input, which rule 3 excludes for phase 1 (§2.1, §7.4, §11).
- Explicit building description as a second parameter source (FR2, gap analysis D6, option C);
  constant gains through the existing `CSVLoader` (FR7); no new component besides the EPW reader.
- Rule added: a case is `not_modelled` when its defining feature is missing; a mechanism missing
  for all cases alike is a register entry (§0 rule 3).
- Write guard on Windows: runs use `HISIM_WRITE_GUARD=collect` (§10).

**Changes in revision 3** (answers of the human to the checklist of revision 2)

- Both standards and the ISO climate spreadsheet are available. The spreadsheet carries
  irradiance per orientation and no beam/diffuse split; the ISO suite therefore feeds
  irradiance per orientation directly into the model, nothing is reconstructed (§7.4).
- Publication boundary fixed: nothing is published that pyBuildingEnergy does not publish
  (§12). Private data live outside the repository (§7.2).
- Development is local first; CI comes later on a private JuGit (GitLab) repository.
  CI frequency is deferred. Pass/fail gating is switched on by the human only, after the
  first full report has been reviewed (§9.3).
- Output folder renamed to `BESTEST_output/`, created inside the calculation's result
  directory because of HiSim's write guard (§10).
- Phase-2 extensions are not planned. The cases that need them stay `not_modelled` (§2.3).
- Deviation register added: every point where the input mapping or the model departs from
  the standard's description is listed, with clause and treatment (§8.3, FR18).
- Input-mapping rules sharpened (§3.2) and a worked derivation for cases 600 and 900 added
  for checking (Appendix B).

**Changes in revision 2**

- EN 15265:2007 (withdrawn) replaced by the verification cases of EN ISO 52016-1:2017,
  clause 7.2 (§2.1, §7, §8.2).
- ASHRAE acceptance logic changed from "range of the reference programs" to the normative
  Annex A3 of ANSI/ASHRAE 140-2023, with per-group accounting (§8.1).
- ASHRAE case list extended from 4 to 18 cases (§2.2); cases that need shading devices,
  scheduled ventilation or two zones listed separately (§2.3, §2.4).
- Input-mapping rules from the case descriptions to the 5R1C parameters added (§3.2).
- Gap analysis pre-filled from a reading of the code (§5).
- pyBuildingEnergy (BSD-3) added as the source of case data, reference-file templates and
  the public Denver weather file (§7, §12).

---

## How to use this spec

- **Human:** complete the open items of the checklist in §A before handing the spec to the
  agent. Items marked *before step N* can be finished later, but must be done before the
  agent reaches that implementation step (§11).
- **Agent:** read §0, then start with the gap analysis (§5) and stop for review. Do not
  start feature work before the human has confirmed the gap analysis.

## A. Human checklist

### Documents and data
- [x] ANSI/ASHRAE 140-2023 available to the reviewer (`06_Norms/ANSI-ASHRAE/`, outside the
      repository).
- [x] EN ISO 52016-1:2017 and its climate spreadsheet available (`06_Norms/EN-ISO-52016/`).
- [x] Climate spreadsheet checked: no beam/diffuse split, see §7.4.
- [x] `shared_resources/725650TYCST.epw` downloaded from the BESTEST-GSR repository, branch
      `main` (<https://github.com/NatLabRockies/BESTEST-GSR>), placed at its repository
      location (§6) on 2026-11-02; blob SHA-1 identical to the repository file and to the
      pyBuildingEnergy copy; details in `PROVENANCE.md` beside it.
- [x] `LICENSE.md` of the same repository placed unchanged beside the EPW on 2026-11-02; blob
      SHA-1 `69accd1dfc2459acf66b5000b2f2aa11e5d67d4e` verified against the repository file.
- [x] Hour-by-hour comparison of the EPW with the normative `725650TY.CSV` done on 2026-11-02:
      all 8 760 rows identical in 13 fields; recorded in `PROVENANCE.md`.
- [x] ASHRAE 140-2023 supplemental files (`725650TY.csv`, `Std140_TF_Output.xlsx`, `S140outNotes.txt`)
      placed in the private folder (§7.2) on 2026-11-02; local use only, never committed.
- [ ] Fill `ashrae140_annex_a3.json` from Annex A3, Tables A3-1 to A3-4 (§7.3). *Before step 6.*
- [ ] Confirm the group sizes and minimum counts of Table A3-14 assumed in §8.1. *Before step 6.*
- [ ] Check whether Annex A3 or the modelling rules of Section 7 say anything about cases a
      program cannot model; note the result in `docs/verification/README.md`. *Before step 6.*
- [ ] Fill `iso52016_tables_28_34.json` from Tables 28 to 34, using the corrected Case 900
      columns (§7.3). *Before step 7; deferred with the ISO suite (§2.1).*
- [x] `origin/main` merged into `verification_spec` on 2026-11-03 (`389ac8c0`); gap analysis §7
      re-checks the rows for the files changed on `main` since 2026-10-12 (bead hisim-x6c7.7).
- [x] D14: the new job marker `verification`, run by the catch-all CI job, the suite's unit tests
      carrying `base` (option (a)); D15: hisim-x6c7.1 closed as superseded by `main` PR #1079.
      Both decided 2026-11-03 (gap analysis §7.4).
- [ ] Verify the [verify] items of §3.2 and Appendix B against the standards. ISO 13790 items
      checked against the FDIS 2007 text on 2026-11-02 (§3.1, §3.2.3 to 3.2.5, 3.2.9, 3.2.10,
      Appendix B.4). ASHRAE 140 clause and table numbers checked in the PDF on 2026-11-03 (gap
      analysis §9; finding F5, the 640/940 ramp hour, D18). Open: the layer data of Appendix B
      (Tables 7-2, 7-27; the human), the few ASHRAE values of gap analysis §9.2, and conditions
      (a) and (b) of the ISO 13786 rule (standard not available). *Before step 3.*
- [ ] Optional: IEA BESTEST 1995 report for the public report.

### Decisions
- [x] Publication boundary: nothing is published that pyBuildingEnergy does not publish; in
      doubt, private (§12).
- [x] Private data location: `06_Norms/hisim_BESTEST_data/` on the developer's machine,
      exported as `HISIM_VERIFICATION_DATA` (§7.2). Never inside any HiSim repository.
- [x] Development local first; later a private repository on JuGit with GitLab CI running
      the suites (§9.3).
- [x] CI frequency: deferred.
- [x] Output folder: `BESTEST_output/` (§10).
- [x] Phase 2: not planned. Cases that need shading, scheduled ventilation or two zones stay
      `not_modelled` (§2.3, §2.4).
- [x] The human reviews the first full report before any pass/fail gating, tolerance or
      known deviation is set. The agent never sets these (rule 10).
- [x] The four conditions of the BESTEST-GSR licence (§12) accepted on 2026-11-02 for the
      redistribution of `725650TYCST.epw` and `LICENSE.md` in the HiSim repository.
- [x] Gap analysis reviewed on 2026-11-02; decisions D1 to D9 recorded in
      `docs/verification/gap_analysis.md` §4 and in this revision.
- [x] Warm-up: two-pass run (§3.2.11), decided 2026-11-02.
- [x] Input-mapping decisions of §3.2.1, 3.2.3 and 3.2.4 confirmed on 2026-11-02 as
      recommended (gap analysis D10 to D12); §3.2.8 to 3.2.11 were decided on 2026-11-02.
- [x] The agent reads the standards in the private folder to resolve [verify] marks; only
      clause and table numbers and the suite's own derived values enter the repository
      (2026-11-02, gap analysis D13). The standards are read in place; no text of a standard
      is extracted or copied into any file, not even a temporary one (2026-11-03).
- [x] Thermostat variable: air control with a register entry (§3.2.10), decided 2026-11-02; the
      ideal heater stays unchanged.
- [x] `iso52016` suite deferred (2026-11-02): phase 1 runs `ashrae140` only (§2.1).
- [x] D16 (2026-11-03): one parameter path, overrides on the TABULA row with an origin record;
      replaces D6 (gap analysis §8).
- [x] D17 (2026-11-03): `Building.get_windows` reads the window geometries from
      `BuildingInformation` in step 2b itself, no step 2c (gap analysis §8.3).
- [x] D18 (2026-11-03, for now): cases 640 and 940 keep the step at 07:00 instead of the ramp of
      7.2.2.1.4 c, registered as `input_mapping`; revisited with the first full report (gap
      analysis §9.3).

## 0. Rules for the agent

1. **Explore before you build.** Your first deliverable is the gap analysis in §5. Stop
   after it and wait for confirmation.
2. **Never invent reference values.** All reference numbers come from files provided by
   the human (§7). If a reference file or value is missing, the test is **skipped** with
   a clear message — it must never pass silently and must never use a made-up number.
3. **Never tune the model to pass, never change anything silently.** Three kinds of change
   are distinguished:
   (a) changes to the 5R1C equations, coefficients or defaults — forbidden;
   (b) missing capabilities listed in §11, step 2 — required, with unit tests;
   (c) input-mapping rules (§3.2) — fixed and documented before the first comparison;
   the agent proposes, the human confirms.
   A case whose defining feature is missing in HiSim is `not_modelled` (§2.3); a mechanism
   missing for all cases alike is a register entry and the cases run. No model or control code
   is added to make a case runnable (decision of 2026-11-02); (b) covers input-side
   infrastructure only.
   Every point where the inputs or the model depart from the standard's description is an
   entry in the deviation register (§8.3, FR18). No fix, approximation or default without
   an entry.
4. **Values marked [verify]** are from memory or from secondary sources. Clause, table and
   figure numbers of the standards quoted in this spec were taken from the pyBuildingEnergy
   documentation and from memory: treat every one of them as [verify] and confirm it
   against the standard before using it in code comments, case files or reports.
5. **Match the repository's style:** package layout, naming, typing, docstrings, logging,
   test conventions, configuration schema conventions, the write guard and CI setup. Read
   `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING`/`README` and existing tests first. Open work and
   findings are tracked as beads (`br`), as `CLAUDE.md` requires; the checklists of this spec
   belong to the human, they are not the agent's todo list.
6. **Do not download data from the internet.** The human provides all documents and data.
7. **Small, reviewable PRs** in the order of §11. Each step must leave the test suite
   green (verification tests may be skipped until their data exists).
8. **Ask instead of guessing** when a case definition is ambiguous. Note the question in
   the PR description.
9. **Attribute reused work.** Code or data taken from pyBuildingEnergy keeps its BSD-3
   copyright notice and is cited (§12). Case data are re-derived from the standard, not
   copied blindly (§3.2.3 is an example where pyBuildingEnergy's value must not be reused).
10. **The human keeps the hand on the results.** The agent never sets or changes acceptance
    criteria, tolerances, statuses, known deviations or the CI gating switch (§9.3). It
    produces the report and proposes; the human decides.

## 1. Goal

Add a verification suite to HiSim that

- runs two suites for the building thermal model (phase 1 runs the first; the second is
  deferred, §2.1):
  1. ANSI/ASHRAE Standard 140-2023, Section 7, building thermal envelope and fabric load
     tests (BESTEST), single-zone cases;
  2. EN ISO 52016-1:2017, clause 7.2, verification cases of the hourly method;
- compares **heating demand, cooling demand and zone temperature**,
- runs every case at **three time steps: 60 min, 15 min and 1 min**,
- reports every result against the standard's criterion where one exists and, once the
  human has switched gating on, **fails** (pytest / CI) when a result leaves an acceptance
  range (§8, §9.3),
- reports, per ASHRAE test group, how many ranges were attempted, met and not modelled,
  and never claims conformity with Standard 140 (§8.1),
- lists every deviation from the standards' descriptions in a deviation register (§8.3),
- exports all results to `BESTEST_output/` (tables, JSON, plots, summary report) in a form
  that can be used directly in the paper.

Terminology: this is **verification** against reference results and reference
programs, not validation against measured data. Use "verification" in code, docs and
outputs. `BESTEST` is the folder name the human chose for the exports; both suites derive
from the BESTEST test cell.

## 2. Scope

### 2.1 Suites (phase 1)

| Suite | Cases | Compared quantities | Criterion |
|---|---|---|---|
| `ashrae140` | 600, 620, 640, 660, 670, 680, 685, 695, 900, 920, 940, 980, 985, 995, 600FF, 680FF, 900FF, 980FF | annual heating and annual sensible cooling (absolute for 600 and 900, difference to the parent case otherwise); diagnostics: peak heating and cooling load with timestamp, monthly loads, hourly loads and temperatures on 1 February and 14 July (Table 7-47, 7.3.8), free-float mean/min/max zone air temperature, 1 °C temperature bins for 900FF (7.3.7) | Annex A3 ranges, Tables A3-1 to A3-4; diagnostics have no range |
| `iso52016` (**deferred**, 2026-11-02: the §7.4 bypass needs a new Building input, excluded by rule 3) | 600, 640, 900, 940, 600FF, 900FF (Table 27 [verify]) | monthly and annual heating and cooling need (Tables 28, 29), monthly and annual mean operative temperature (Table 30), peak loads (Table 31), free-float max/min/mean operative temperature (Table 32), hourly load and operative temperature on 4 January (Tables 33, 34) [verify] | reported deviation; the standard defines no tolerance (§8.2) |

Phase 1 runs the `ashrae140` suite. The `iso52016` suite keeps its definition in this spec for a
later phase; its step (§11 step 7) and its data items (§A) wait.

### 2.2 ASHRAE case derivation

Every case is defined relative to its base case. The derivation below is the input of the
`derives_from` mechanism of the case files (FR1); the details come from the standard.

| Case | Derived from | Change |
|---|---|---|
| 600 | – | lightweight base case, 7.2.1 |
| 620 | 600 | 6 m² window east and 6 m² west, no south window |
| 640 | 600 | heating setback 10 °C from 23:00 to 07:00, linear ramp to 20 °C from 07:00 to 08:00 (7.2.2.1.4) |
| 660 | 600 | low-emissivity double glazing, Tables 7-17 to 7-20 |
| 670 | 600 | single glazing, Tables 7-21 to 7-24 |
| 680 | 600 | increased insulation, Table 7-25 |
| 685 | 600 | thermostat 20/20 |
| 695 | 680 | thermostat 20/20 |
| 900 | 600 | heavyweight walls and floor, Table 7-27 |
| 920 | 900 | as 620 |
| 940 | 900 | as 640 |
| 980 | 900 | increased insulation, Table 7-31 |
| 985 | 900 | thermostat 20/20 |
| 995 | 980 | thermostat 20/20 |
| 600FF, 680FF, 900FF, 980FF | base case | no heating and no cooling, 7.2.2.3 [verify] |

Checked against the standard on 2026-11-03: the tables of 660, 670, 680, 900 and 980 and the clauses
7.2.2.1.4 (640), 7.2.2.1.6 (660), 7.2.2.1.7 (670) and 7.2.2.2.1 (900). Still [verify]: the window
placement of 620, the 20/20 set points of 685, 695, 985 and 995, and 7.2.2.3.

### 2.3 Not modelled (decision 2026-11-02: no extension planned)

| Cases | Missing capability |
|---|---|
| 610, 630, 910, 930 | shading of direct radiation by an overhang (610, 910) and by overhang plus side fins (630, 930); geometry per Figures 7-3, 7-5, 7-6 [verify] |
| 650, 950, 650FF, 950FF | scheduled ventilation: night vent fan 1700 standard m³/h (7.2.2.1.5) from 18:00 to 07:00, no heating in 650/950 [schedule: verify]; time-dependent H_ve |

These cases appear in the group accounting as `not_modelled` (§8.1). Should the human
later reopen the decision, §8.1 contains the rule that tells whether an extension can
change the formal result at all.

### 2.4 Out of scope

- Case 960 (two-zone sunspace): no multi-zone model in HiSim; `not_modelled` in the group
  accounting (§8.1).
- In-depth diagnostic cases of Section 7.2.3, Case 195 (7.2.3.5) to Case 810 (7.2.3.25).
- Other test groups of Table A3-14 (cooling, heating and air-side equipment).
- Code-to-code comparison with EnergyPlus: carried out separately by a partner
  organisation for the paper.
- HVAC component tests (heat pumps, storage, PV).
- Validation against measured data.
- Changes to the physics of the building model (rule 3a).

### 2.5 Time steps

- Every case is simulated at **3600 s, 900 s and 60 s**.
- References exist only at the published resolution (annual, monthly, hourly). HiSim
  results at all three time steps are aggregated to that resolution and compared (FR10).
- The differences between the three time steps are reported separately (time-step
  consistency, §8.4). This is not a sub-hourly verification; sub-hourly references do
  not exist in these standards.

## 3. Context

### 3.1 HiSim building model

- Simple hourly method of EN ISO 13790:2008: 5R1C network (five conductances, one
  capacity), Crank–Nicolson scheme of Annex C. The standard was withdrawn in 2017 and
  succeeded by ISO 52016-1, whose hourly method is a different, surface-resolved model.
  Cite as "EN ISO 13790:2008 (withdrawn), simple hourly method".
- The thermostat is the Building's two-step demand procedure (ISO 13790, clause C.4) on the zone
  air temperature, which is what ASHRAE 140 requires (7.2.1.13.1 b: the thermostat senses only the
  air temperature); the setpoints are
  `BuildingConfig` fields; the ideal heater delivers the power its L1 controller sets, and that
  controller copies the Building's demand, so the delivered power is the demand (gap analysis
  F2 and §7.1).
- The thermal-mass update divides C_m by the simulation time step, not by 3600 s
  (`hisim/components/building/building.py`, equation (C.4)), so the scheme is time-step aware and
  consistent at 900 s and 60 s (gap analysis §6.1). The state carried into the next step is
  θ_m,t of equation (C.4) since `main` PR #1079; the mid-step average of (C.9) serves the surface
  and air equations of the step and is published as the mass-temperature output (equations
  checked against the FDIS 2007 text). The 2·C_m finding of 2026-11-02 concerned the code before
  that PR (gap analysis F1 and §7.1; D15).
- Solar gains enter the model as irradiance on each window's plane times the window's
  effective collecting area (ISO 13790 clause 11 [verify]); the model itself needs no
  beam/diffuse split. HiSim's window optics compute that plane irradiance from DNI, DHI
  and GHI with a constant F_w per window; a bypass for the ISO suite would need a new Building
  input (§7.4, deferred).
- The building is parametrised from a TABULA/EPISCOPE row. Step 2b adds an override for every
  value the 5R1C model reads from the row, plus an origin record; the suite names a carrier row
  and overrides all of it (§5, §11, gap analysis D16, which revised D6 on 2026-11-03).

### 3.2 Input-mapping rules (case description → 5R1C parameters)

The test cases are described as layered constructions, areas and windows; the model takes
conductances and one capacity. These rules fix the bridge. They are applied in
`system_builder.py` (rules 1 to 4 and 6 to 11), which passes the results to HiSim as overrides on
the TABULA row (FR2, D16); rule 5 is HiSim's own (`Building.get_conductances`). Every derived
value is written to `parameters/<case>.json` (FR3), and
the values for cases 600 and 900 are checked against the hand calculation of Appendix B
(`test_parameter_derivation.py`). Items marked *[decision]* are confirmed by the human (§A).
Every rule that departs from the standard's description is also an entry in the deviation
register (§8.3).

1. **Opaque transmission.** H_tr_op = Σ U_i·A_i over floor, walls and roof. Thermal bridges
   are zero in both suites (ASHRAE: the constructions are plane layers and the term does not occur
   in the standard, checked 2026-11-03; ISO [verify]). The U-values are the air-to-air values printed
   in the standards' construction tables (ASHRAE Tables 7-2, 7-25, 7-27, 7-31, checked; ISO Tables
   23, 24 [verify]). They include the standards' combined surface coefficients, which is
   consistent with a model that has no separate long-wave exchange. Recomputation from the
   layers serves only as a check (pyBuildingEnergy notes a 0.01 m²K/W discrepancy between
   Informative Table 7-28 and the layers of Table 7-27 for the Case 900 floor; table numbers
   checked). The floor faces outdoor air in both suites (ASHRAE 7.2.1.5 raised floor, checked; ISO
   Table 23 footnote a [verify]) and receives no solar radiation. *[decided 2026-11-02: as stated; the printed
   value wins where the layer recomputation differs]*
2. **Window transmission.** H_tr_w = Σ U_w·A_w with the printed air-to-air window U-values
   (ASHRAE Normative Table 7-10 with Informative Table 7-11, Tables 7-17 and 7-21 for 660 and 670,
   checked; ISO clause 7.2.2.6 [verify]).
3. **Internal heat capacity.** C_m = Σ κ_j·A_j over all elements facing the zone (ISO 13790
   12.3.1; FDIS 2007: 12.3.1.1, equation (68)). κ_j = Σ ρ·c·d over the layers from the inside up
   to the effective thickness of ISO 13786, Annex A, for a period of one day: the smallest of
   (a) half the total thickness of the element [verify], (b) the thickness up to the first
   thermal insulation layer [verify], (c) 0.10 m (the cap ISO 13790 states for a one-day period,
   FDIS 2007 Table 11, referring to ISO 13786 for the method). The heat-capacity class table of
   ISO 13790 is not used. κ and C_m are derived in the system builder; HiSim receives C_m as an
   override (D16).
   pyBuildingEnergy sums all layers because its model distributes the mass differently;
   that value must not be reused. Appendix B applies the rule to cases 600 and 900 and
   shows where condition (a) matters. *[decided 2026-11-02: rule with all three conditions;
   (a) and (b) stay [verify] until ISO 13786 is available]*
4. **Effective mass area and total internal surface.** A_m = C_m² / Σ A_j·κ_j² (ISO 13790
   12.2.2; FDIS 2007 equation (66)). A_tot is the actual area of all surfaces facing the zone
   (floor, ceiling, four walls including the windows), which is how ISO 13790 7.2.2.2 defines
   it; Λ_at = 4.5 is the value the clause offers as an assumption and is not used.
   Appendix B gives the value. Both are derived in the system builder and passed as overrides
   (D16). *[decided 2026-11-02: actual A_tot, A_m by the formula]*
5. **Coupling conductances.** H_tr_is = 3.45 W/(m²K)·A_tot, H_tr_ms = 9.1 W/(m²K)·A_m,
   H_tr_em = 1 / (1/H_tr_op − 1/H_tr_ms) (ISO 13790 12.2.2, FDIS 2007 equations (64) and (65);
   h_is = 3.45 in 7.2.2.2). Already implemented;
   unchanged.
6. **Ventilation.** H_ve = (ρ·c)_air · n · V / 3600. HiSim's constant is
   0.34 Wh/(m³K) = 1224 J/(m³K) (`information.py`). ASHRAE specifies 0.5 h⁻¹ at site
   density (Denver, 1650 m); the rate passed to HiSim is n = 0.5 · (ρ·c_p)_site / 1224 with
   (ρ·c_p)_site from the standard (7.2.1.6.1, Table 7-4 and Annex B3.1; the references checked, the
   site values are read from B3.1 in step 3), so that H_ve is
   the standard's. The ISO rate of 0.41 h⁻¹ is already altitude-adjusted (7.2.2.14 [verify])
   for the air heat capacity ISO 52016-1 assumes [verify]; if that constant differs from
   1224 J/(m³K), n is scaled the same way. Scaled n and H_ve are written to the parameters
   output; the scaling is a register entry.
7. **Internal gains.** 200 W, constant, sensible, fed as a constant source into the
   building's device heat-gain input. The model's split onto the nodes is fixed by ISO 13790
   C.1 to C.3 (half of the internal gains to the air node, the rest to surface and mass by
   area ratio); the 60 % radiative / 40 % convective split of ASHRAE (7.2.1.7, checked) and
   the split of ISO 52016-1 cannot be set. Register entry in both suites.
8. **Solar gains through windows.** Windows are explicit per orientation (area, azimuth,
   tilt 90°), with g_n, frame fraction 0 and no shading devices (Table 7-10: curtains, blinds,
   frames, spacers and mullions none; checked). Non-perpendicular
   incidence: for the ASHRAE suite F_w is the irradiance-weighted annual mean of
   SHGC(θ)/SHGC_n from Table 7-12, computed per orientation from the weather file (method
   of pyBuildingEnergy's `effective_window_fw`: beam from the angular table, diffuse from
   the hemispherical value, isotropic sky, ground reflectance 0.2); for the ISO
   suite F_w = 0.90 (7.2.2.6, Table B.22 [verify]). HiSim applies F_w as a
   constant per window on the total plane irradiance (isotropic sky; ground reflectance 0.2 as
   the standard specifies, 7.2.1.1.2.5, checked). The weighted F_w is computed once before any
   result is looked at, written to the parameters output and reported beside the hemispherical
   ratio 0.670/0.769 = 0.871 of Table 7-12 (0.769 at normal incidence checked; the hemispherical
   0.670 is pyBuildingEnergy's reading [verify]). Register entries: `input_mapping` (one constant per
   orientation; 7.2.1.11.3 a) lets a program without angle-dependent inputs skip that section,
   checked) and `model` (isotropic sky).
   *[decided 2026-11-02]*
9. **Opaque solar absorption and sky radiation.** α = 0.6 (Table 7-5 with 7.2.1.8.1; the table
   checked, the interior value 0.6 read, the exterior column [verify]). HiSim
   implements neither the opaque solar gain nor the sky radiation term of ISO 13790 11.3.4 and
   11.3.5 (clause numbers checked against the FDIS 2007 text); H_tr_em sees the outdoor air temperature and α is not used. Register entry
   `model`; no sol-air temperature or other workaround through the inputs. *[decided 2026-11-02]*
10. **Thermostat.** ASHRAE: zone air temperature, non-proportional, unlimited capacity,
    setpoints 20/27 °C (7.2.1.13.1, checked); 685, 695, 985, 995 use 20/20 °C [verify]; 640 and 940
    (7.2.2.1.4, checked 2026-11-03) heat to 10 °C from 23:00 to 07:00, ramp the set point linearly
    from 10 °C to 20 °C between 07:00 and 08:00 (sub-hourly steps follow the ramp, hourly programs
    use 20 °C for that hour) and heat to 20 °C from 08:00 to 23:00; cooling at 27 °C at all hours.
    The ramp hour was missing from this spec until 2026-11-03; `NightSetbackController` steps at
    07:00. Decided 2026-11-03 (D18, for now): the step at 07:00 stays, registered as `input_mapping`
    naming 7.2.2.1.4 c; revisited with the first full report (gap analysis §9.3).
    ISO: control on the operative temperature (7.2.2.1 [verify]), same setpoints and the same
    setback for 640 and 940 (7.2.2.15 [verify]). Operative temperature in the 5R1C model:
    θ_op = 0.3·θ_air + 0.7·θ_s (ISO 13790 Annex C, equation (C.12)). The thermostat is the
    Building's C.4 step, not the heater's (gap analysis F2), so an operative-temperature option
    would be a Building change. Decided 2026-11-02: air control in every suite, register entry
    `model` ("Thermostat acts on θ_air (ISO 13790, clause C.4 procedure); EN ISO 52016-1 controls
    on the operative temperature"). The 640/940 setback uses the existing `NightSetbackController`
    and `BuildingTemperatureModifierInKelvin` (heating setpoint only, 23:00 ≤ t < 07:00; the ramp hour
    per D18); capacity is
    unlimited. *[decided 2026-11-02]*
11. **Warm-up.** HiSim lays exactly one calendar year and can neither prepend a month nor start
    in December (gap analysis row 17). Decided 2026-11-02: two-pass run per (case, time step):
    pass 1 the full year from `initial_internal_temperature_in_celsius`, pass 2 the full year
    from the mass temperature at the end of pass 1; results from pass 2 only. The method and the
    carried state are written to the parameters output; register entry `input_mapping`. The
    initialisation month of the ISO spreadsheet cannot be used for the same reason, should that
    suite return. *[decided 2026-11-02]*
12. **Site and weather.** ASHRAE: Denver International Airport, 39.833 N, 104.650 W, 1650 m,
    UTC−7 (Table A1-1, checked; the EPW header says the same), EPW of §7.2, linear interpolation to sub-hourly steps (FR6).
    ISO: site per 7.2.2.12 [verify], irradiance per orientation from the climate spreadsheet
    (§7.4), linear interpolation to sub-hourly steps.

## 4. Functional requirements

| ID | Requirement |
|---|---|
| FR1 | Test cases are defined declaratively in YAML files, one file per case, validated against a schema. A case may declare `derives_from: <case>` and override fields; the loader resolves the inheritance and writes the resolved case to the output. |
| FR2 | The building takes every value the 5R1C model needs as an override on a TABULA row: element areas, U-values and transmission adjustment factors, a window list (area, azimuth, tilt and the four optics factors per window; `Building.get_windows` reads the geometries from `BuildingInformation`), both air change rates, the air volume, C_m, A_m and A_tot, the apartments and the maximum demand. `BuildingInformation` records the origin of each value (configured or TABULA row); the suite names a carrier row, overrides everything, refuses a case whose record still names the row and writes the record to `parameters/<case>.json`. *Revised 2026-11-03 (gap analysis D16): replaces the second parameter source of D6 (`explicit_description`, `explicit.py`), dropped so that one parameter path is maintained; the TABULA path and its goldens are unchanged.* |
| FR3 | The 5R1C parameters (H_tr_op, H_tr_w, H_tr_em, H_tr_ms, H_tr_is, H_ve, C_m, A_m, A_tot, scaled ACH, F_w per window) are derived by the rules of §3.2 and written to `parameters/<case>.json`. |
| FR4 | Ideal heating and cooling with setpoints (`BuildingConfig`), unlimited capacity, the 640/940 setback through `NightSetbackController` and `BuildingTemperatureModifierInKelvin`, control on the air temperature; the ideal heater and its L1 controller are used unchanged, wired Building → controller → heater → Building: the controller sets the heater's power to the Building's demand and the heater's output is the delivered thermal power (gap analysis §7.1). No power limit and no control-variable option in phase 1 (decided 2026-11-02). |
| FR5 | Free-floating mode (no heating/cooling). |
| FR6 | Weather input from the file specified in the case. EPW is read natively (reader labelling the records in the station's fixed UTC offset from the file's LOCATION line). *Amendment (2026-11-03, step 2a):* `interpolate` pads the year in the zone the series' own index carries instead of a hard-coded one; the zone is derived from the series, not passed as a parameter, so the two cannot disagree, and the German sources keep the same padding instants. The ISO climate path of §7.4 is deferred with that suite. Sub-hourly values by a fixed, documented interpolation (default: linear, as in the weather component). No implicit default weather. |
| FR7 | Constant internal gains in W through the existing `CSVLoader` (no new component); constant infiltration in ACH with the density scaling of §3.2.6. The node split is the model's (§3.2.7) and is documented. |
| FR8 | Simulation time step selectable per run: 3600 s, 900 s, 60 s. All suites run as a matrix over these time steps. |
| FR9 | Outputs per case and time step: annual and monthly heating/cooling energy, peak heating/cooling load with timestamp, time series of zone air temperature, operative temperature (θ_op = 0.3·θ_air + 0.7·θ_s, computed by the runner from the Building's air and surface outputs), heating and cooling power; annual min/max/mean of both temperatures. |
| FR10 | Aggregation of HiSim time series to the reference resolution (mean for temperatures and power, sum for energy), documented in the output. Peak loads and min/max temperatures compared with references are computed from the hourly aggregate; sub-hourly peaks are reported for information only. |
| FR11 | Comparison with reference data according to §8; status per output: `pass`, `fail`, `known_deviation`, `skipped` (reference missing), `info` (diagnostic, no criterion), `not_modelled` (case excluded by §2.3 or §2.4). |
| FR12 | pytest integration: one test per (suite, case, time step, output) with a criterion; failing comparisons fail the test only when gating is on (§9.3), otherwise the test reports and passes. No test is generated for `info` and `not_modelled`. |
| FR13 | Export of all results to `BESTEST_output/` inside the calculation's result directory (§10). |
| FR14 | CLI entry point: `python -m hisim.verification --suite ashrae140 [--case 600] [--timestep 900] [--output-dir ...] [--public]`. |
| FR15 | CI job running the suites (§9.3), implemented when the private repository exists. |
| FR16 | Group accounting for the ASHRAE suite per §8.1: ranges total, minimum required, attempted, met, not modelled, formal result. |
| FR17 | HiSim's own results per (suite, case, time step) are committed as regression references under `golden_references/bestest/`, following the repository's golden-reference system (`golden_ref_spec.md`); they contain HiSim values only. |
| FR18 | Deviation register (§8.3): every departure from the standards' descriptions, with clause, HiSim treatment and category, exported as `deviations.md` and included in `summary.md` and `summary.json`. |

## 5. Step 1: Gap analysis (first deliverable)

Complete this table in `docs/verification/gap_analysis.md` and link it in the PR
description. The columns "Where in code" and "Status" were pre-filled from a reading of
the code on 2026-11-02; confirm or correct every row and fill "Proposed change".

| Capability | Where in code (pre-filled) | Status (pre-filled) | Proposed change |
|---|---|---|---|
| Explicit building parametrisation without TABULA | `hisim/components/building/config.py` (`building_code` mandatory), `information.py` (`BuildingInformation.__init__` pipeline: TABULA row → areas → heat transfer → constants) | partial: U-values and areas of floor, facade, roof, window, door are overridable; window distribution per orientation, g_n, F_w, frame fraction, shading factor, infiltration and use air change, room height, thermal bridging and apartment count come only from the TABULA row | |
| EN ISO 13790 parameter derivation from constructions (§3.2.3 to 3.2.5) | `information.py`, `get_constants`: C_m and A_m from the heat-capacity class times floor area, A_tot = Λ_at × floor area (constant ratio) | missing: detailed C_m, A_m from κ and areas; A_tot from actual surfaces | |
| Window orientation, area, U, g, frame fraction | `building/solar_gains.py` (`WindowGeometry`), `building/window.py`; geometry built from TABULA per-direction areas | partial: the optics take an explicit window list; the list is not configurable | |
| Window optics: incidence angle, diffuse and ground-reflected share, F_w (§3.2.8) | `building/window.py` (`calc_direct_solar_factor`, `calc_diffuse_solar_factor`) | to be read: how the constant F_w from TABULA and the angular factor interact | |
| Irradiance per orientation as direct input, bypassing the optics (§7.4) | `building/solar_gains.py` (`SolarGainsInputs`, `produce_solar_gains`) | missing | |
| Opaque solar absorption and sky radiation (§3.2.9) | `building.py`: solar gains enter through windows only (`solar_heat_gain_through_windows`) | to be confirmed: probably missing | |
| Ideal heater/cooler with setpoints, schedule, power limit, control variable | `hisim/components/idealized_electric_heater.py`: two setpoints, air temperature, no schedule, no power limit | partial | |
| Free-floating run | `building.py`, `i_simulate`: delivered power is read only if the input is connected | exists (unconnected heater input gives 0 W); confirm with a test | |
| Weather from an EPW file | `hisim/components/weather/calculation.py`, `WeatherDataSourceEnum`: DWD_TRY, NSRDB, NSRDB_15MIN, DWD_10MIN, DWD_15MIN, ERA5 | missing: EPW reader and a catalogue entry for Denver in `weather/config.py` (`LocationEnum`) | |
| Weather from the ISO climate spreadsheet (§7.4) | – | missing: reader for temperature, wind and irradiance per orientation (xls, `xlrd` or converted CSV) | |
| Sub-hourly weather interpolation | `calculation.py`: `resample_to_one_minute` (linear), `aggregate_to_timestep` (mean) | exists; document | |
| Constant internal gains with a fixed value | `building.py` inputs `occupancy_heat_gain_channel`, `device_heat_gain_channel` (W) | partial: inputs exist; a constant-source component is needed | |
| Distribution of gains to the 5R1C nodes | `building.py`, `calc_internal_heat_flows_from_internal_gains_and_solar_gains`: ISO 13790 C.1 to C.3 | exists; split not configurable (§3.2.7) | |
| Infiltration as constant ACH with explicit volume | `information.py`, `_ventilation_conductance_in_watt_per_kelvin`: TABULA `n_air_use` + `n_air_infiltration`, `h_room` × floor area, constant 0.34 Wh/(m³K) | partial: formula exists, inputs only from TABULA | |
| Operative temperature output | `building.py`: surface temperature computed in `calc_temperature_of_internal_room_surfaces_in_celsius`, not output | missing output | |
| Time steps 3600/900/60 s: supported? numerically consistent? | `building.py`, `calc_next_thermal_mass_temperature_in_celsius`: C_m / seconds_per_timestep | supported; consistency to be shown numerically (case 600 at the three steps) | |
| Warm-up period | – | to be read: whether the simulator supports a pre-run or the case must prepend a month in the weather series | |
| Result directory and write guard for `BESTEST_output/` (§10) | `hisim/write_guard.py`, `hisim/calculation_scope.py`, `ResultPathProviderSingleton` | exists; the exporter must write inside the registered result directory | |
| Golden-reference integration (FR17) | `golden_references/`, `scripts/golden_*.py`, `golden_ref_spec.md` | exists for system-setup KPIs; the verification results need their own entry point or a KPI export | |
| Run time of an annual simulation at 60 s | – | to be measured | |
| Existing test framework, markers, CI workflows | `pytest.ini` (markers `base`, `extendedbase`, `buildingtest`, …), `.github/workflows/` (`tests.yml`, `golden-*.yml`, …) | exists; every test carries exactly one job marker (`system_docs/testing.md` §2, `tests/job_markers.py`); the marker `verification` follows D14 (gap analysis §7.4); CI target is GitLab on JuGit later (§9.3) | |

Done on 2026-11-02: the completed table, the findings and the decisions are in
`docs/verification/gap_analysis.md`.

**Stop and wait for confirmation.** In particular, flag any item that needs changes to
public interfaces of existing components, any sign that the 5R1C implementation is not
consistent at sub-hourly time steps, and the findings on §3.2.8 and §3.2.9.

## 6. Directory layout (adapt to repo conventions)

```
hisim/verification/
  __init__.py
  __main__.py              # CLI (FR14)
  schema.py                # case file schema (pydantic or repo's validation approach)
  case_loader.py           # YAML → validated case object, resolves derives_from (FR1)
  system_builder.py        # case object → HiSim system; applies §3.2 (FR2, FR3)
  runner.py                # run simulation for a given time step, collect outputs
  aggregate.py             # aggregation to reference resolution (FR10)
  metrics.py               # annual/monthly sums, peaks, min/max/mean, bins
  compare.py               # acceptance logic and group accounting (§8)
  deviations.py            # deviation register (§8.3)
  export.py                # BESTEST_output (§10)
  plots.py
  suites/
    ashrae140/
      suite.yaml           # metadata, case list, range structure, group definition, register entries
      reference_template.json   # Annex A3 template, values null (§7.3)
      cases/case600.yaml … case995.yaml, case600ff.yaml … case980ff.yaml
    iso52016/
      suite.yaml
      reference_template.json   # Tables 28–34 template, values null (§7.3)
      cases/case600.yaml, case640.yaml, case900.yaml, case940.yaml, case600ff.yaml, case900ff.yaml
hisim/inputs/weather/EPW/Denver_Intl_AP/
  725650TYCST.epw                            # public, original name kept (§12)
  LICENSE.md                                 # copied unchanged from the BESTEST-GSR repository
  PROVENANCE.md                              # origin, download date, hashes, identity check result
golden_references/bestest/                   # HiSim's own results as regression references (FR17)
tests/verification/
  test_schema.py           # fast: all case files valid, inheritance resolves
  test_parameter_derivation.py  # fast: §3.2 for cases 600 and 900 vs. Appendix B
  test_aggregation.py      # fast: aggregation logic
  test_compare.py          # fast: acceptance logic and group accounting with synthetic numbers
  test_verification_suites.py   # slow: runs cases, job marker `verification` (D14); skips without HISIM_VERIFICATION_DATA
docs/verification/
  README.md                # purpose, usage, input mapping, deviation register, limitations, data/licence notes
  gap_analysis.md          # step 1
```

The weather folder follows the existing catalogue layout (`hisim/inputs/weather/<source>/<location>/`)
and gets a `LocationEnum` entry with a new `WeatherDataSourceEnum.EPW`.

## 7. Reference data

Reference data are kept **separate from code and case definitions**, because their
licence differs (§12). Case *structure* (which cases exist, which are differential, which
parent) is public and lives in `suite.yaml`; reference *values* are private.

### 7.1 Documents and data

| Document / data | Needed for | Source ID | How to obtain |
|---|---|---|---|
| ANSI/ASHRAE 140-2023 | case definitions (Section 7), acceptance ranges (Annex A3, Tables A3-1 to A3-4), group criteria (Table A3-14) | `ashrae140_2023` | available, `06_Norms/ANSI-ASHRAE/` |
| ASHRAE 140-2023 supplemental files | EPW cross-check against `725650TY.CSV`, output templates | `ashrae140_2023_supplemental` | human downloads from the ASHRAE Standard 140 resource page into the private folder |
| Denver TMY3 EPW `725650TYCST.epw` (header: DENVER INTL AP, CO, USA, TMY3, WMO 725650, 39.83 N, 104.65 W, UTC−7, 1650 m; NREL TMY data set 2008) | weather of the `ashrae140` suite | `denver_tmy3_epw` | human downloads `shared_resources/725650TYCST.epw` and `LICENSE.md` from the BESTEST-GSR repository (<https://github.com/NatLabRockies/BESTEST-GSR>); the agent checks the file against the copy in the pyBuildingEnergy clone (`USA_CO_Denver.Intl.AP.725650_TMY3.epw`) and against `725650TY.CSV` |
| EN ISO 52016-1:2017 | case definitions (clause 7.2.2), reference results (clause 7.2.4, Tables 28 to 34) | `iso52016_2017` | available, `06_Norms/EN-ISO-52016/` |
| ISO 52016-1 BESTEST climate spreadsheet `ISO_52016_1_BESTEST_ClimData_2016.08.24.xls` | weather of the `iso52016` suite (clause 7.2.2.12) | `iso52016_climate` | available, `06_Norms/EN-ISO-52016/` |
| EPB Center, collection of comments on EN ISO 52016-1:2017, comment no. 28 [verify] | corrected Case 900 heating and cooling columns of Tables 28 and 29 | `epb_center_comments` | public web page, <https://epb.center/support/documents/iso_52016-1_details/> |
| ISO 13790 | clause and equation numbers behind the register entries and §3.1, §3.2.3 to 3.2.5, 3.2.9, 3.2.10, Appendix B.4 | `iso13790` | available as the FDIS text of 2007, `06_Norms/HiSim_Base-ISO-FDIS-13790/`, the text HiSim was built from; numbers checked against it on 2026-11-02 (the published 2008 edition may number equations differently) |
| ISO 13786:2007, Annex A | conditions (a) and (b) of the effective-thickness rule (§3.2.3) | `iso13786` | not available on 2026-11-02; the rule is implemented with all three conditions and (a), (b) stay [verify] |
| pyBuildingEnergy repository (EURAC, BSD-3, Zenodo DOI 10.5281/zenodo.10887919) | case data (layers, windows, site, infiltration correction), reference-file templates, report structure | `pybuildingenergy` | local clone, `02_Repositories/pyBuildingEnergy/` |
| Optional: IEA BESTEST report (Judkoff & Neymark 1995, NREL/TP-472-6231) | public context for the report; not a criterion | `iea_bestest_1995` | free (OSTI) |

### 7.2 Storage and loading

Private folder on the developer's machine, exported as `HISIM_VERIFICATION_DATA`
(for example in the git-ignored `.env` of the repository, if HiSim's environment loading
covers it; the agent checks and documents):

```
$HISIM_VERIFICATION_DATA/                       # private folder outside every repository
  ashrae140_annex_a3.json                       # filled from Tables A3-1 to A3-4
  iso52016_tables_28_34.json                    # filled from Tables 28 to 34, Case 900 corrected
  ISO_52016_1_BESTEST_ClimData_2016.08.24.xls   # copy of 06_Norms/EN-ISO-52016/, or a path in suite.yaml
  ashrae140_2023_supplemental/                  # 725650TY.CSV, output templates
```

| Source ID | Location |
|---|---|
| `denver_tmy3_epw` | public, `hisim/inputs/weather/EPW/Denver_Intl_AP/725650TYCST.epw` with `LICENSE.md` and `PROVENANCE.md` (§6) |
| `ashrae140_2023` (ranges) | private, `$HISIM_VERIFICATION_DATA/ashrae140_annex_a3.json` |
| `iso52016_2017` (tables) | private, `$HISIM_VERIFICATION_DATA/iso52016_tables_28_34.json` |
| `iso52016_climate` | private, `$HISIM_VERIFICATION_DATA/ISO_52016_1_BESTEST_ClimData_2016.08.24.xls` |
| `ashrae140_2023_supplemental` | private, `$HISIM_VERIFICATION_DATA/ashrae140_2023_supplemental/` |
| `iea_bestest_1995` | not stored; cited only |

- Private data never enter a HiSim repository, not even the later private one on JuGit:
  the repository may be mirrored or published later and history is hard to scrub. The
  private CI reads them from a protected location (secret, protected CI variable or a
  separate private data repository), decided when the CI is set up (§9.3).
- Each `suite.yaml` declares which sources it uses. If a source is missing, all tests that
  need it are **skipped** with the message
  `"Reference data '<source_id>' not found (set HISIM_VERIFICATION_DATA)"`.
  The simulations still run and the report shows HiSim values with status `skipped`.
- `golden_references/bestest/` (FR17) holds HiSim's own results and is public.

### 7.3 Formats

`suite.yaml` (public) carries the structure, following pyBuildingEnergy's case tables:

```yaml
suite: ashrae140
reference_sources: [ashrae140_2023, denver_tmy3_epw]
gating: false             # §9.3; only the human sets true
groups:
  low_mass:
    ranges_total: 21        # Table A3-14, checked 2026-11-03
    minimum_required: 18    # Table A3-14, checked
    range_cases:
      - {case: "600", metric: heating}
      - {case: "600", metric: cooling}
      - {case: "610", parent: "600", metric: heating}
      - {case: "610", parent: "600", metric: cooling}
      # … 650 has a cooling range only (Table A3-2, checked)
  high_mass:
    ranges_total: 19        # Table A3-14, checked
    minimum_required: 17    # Table A3-14, checked
    range_cases:
      - {case: "910", parent: "900", metric: heating, reverse: true}   # value = 900 − 910 (Tables A3-3, A3-4, checked)
      # …
deviation_register:        # §8.3; entries of category model and input_mapping
  - id: gains_split
    category: model
    standard_clause: "ASHRAE 140-2023, 7.2.1.7"
    description: "60 % radiative / 40 % convective internal gains"
    hisim_treatment: "ISO 13790 C.1–C.3 split: 50 % to the air node, remainder to surface and mass nodes by area ratio"
```

Private reference values (templates in the suite folders, same structure as
pyBuildingEnergy's templates; every value `null` until the human fills it):

```json
{
  "source": "ANSI/ASHRAE Standard 140-2023, Normative Annex A3, Tables A3-1 to A3-4",
  "units": "MWh/year",
  "ranges": {
    "600": {"heating": [null, null], "cooling": [null, null]},
    "610": {"heating": [null, null], "cooling": [null, null]},
    "650": {"heating": null, "cooling": [null, null]}
  }
}
```

```json
{
  "source": "EN ISO 52016-1:2017, clause 7.2.4, Tables 28 to 34",
  "monthly_heating_kwh":  {"600": [null, "…12 values"], "640": [], "900": [], "940": []},
  "monthly_cooling_kwh":  {},
  "annual_heating_kwh":   {"600": null, "640": null, "900": null, "940": null},
  "annual_cooling_kwh":   {},
  "monthly_operative_temperature_c": {"600": [], "640": [], "900": [], "940": [], "600FF": [], "900FF": []},
  "annual_operative_temperature_c":  {},
  "peak_load_kw":         {"600": [null, null]},
  "free_float_operative_temperature_c": {"600FF": [null, null, null], "900FF": [null, null, null]},
  "january_4_hourly_load_wh": {"600": [null, "…24 values"]},
  "january_4_hourly_operative_temperature_c": {"600FF": [null, "…24 values"], "900FF": []},
  "notes": {"900": "heating/cooling columns of Tables 28/29 swapped in print; corrected per EPB Center comment no. 28 [verify]"}
}
```

Empty values → status `skipped` for that output.

### 7.4 ISO climate spreadsheet

**Deferred with the `iso52016` suite (decision 2026-11-02).** The bypass below needs a new optional
Building input for the solar gain through windows (gap analysis F3, D9), which rule 3 excludes for
phase 1; the analysis stays for a later phase.

What the file contains (header read on 2026-11-02; the same facts are stated in
pyBuildingEnergy's `iso_climate_loader.py`):

- A first month for initialisation only, a copy of December, followed by the 8760 h of
  the year (9504 rows).
- Weather: the original BESTEST DRYCOLD.TMY, converted according to draft ISO/FDIS 52010-1
  (spreadsheet version 2015.09.06).
- Columns: hours/calc, month, week, day/week, hours/week, `theta_e;air` [°C], `I_sol`
  [W/m²] for NV, EV, SV, WV, N45, S45, VOID, H, `v_w` [m/s], humidity ratio [g/kg].

Consequences:

- The irradiance is already the total irradiance on the vertical north, east, south and
  west planes, on the 45° north and south planes and on the horizontal; there is no
  beam/diffuse split and no direct normal irradiance. The underlying weather is the
  original BESTEST file DRYCOLD.TMY, not the TMY3 of the ASHRAE suite, so the Denver EPW
  is not a substitute (pyBuildingEnergy reports a Case 900 heating deviation of about
  +24 % with the EPW against about +5 % with this file).
- **Decision: no reconstruction of DNI and DHI.** Inverting the ISO 52010-1 conversion is
  ill-posed and would reproduce that conversion only approximately. The 5R1C model needs
  the irradiance on each window plane and nothing else (§3.1); for the ISO suite F_w is the
  constant 0.90 (§3.2.8), so the window optics add nothing. The ISO suite therefore feeds
  `I_sol` of the window's orientation (south: `SV`; 620-type cases do not exist in this
  suite) directly into the solar-gain calculation, with the air temperature from
  `theta_e;air`. Wind speed and humidity are not used by the model. If §3.2.9 turns out to
  be implemented, the opaque surfaces take `NV`, `EV`, `SV`, `WV` and `H`.
- The initialisation month cannot be used by HiSim (§3.2.11); the two-pass run replaces it and
  results are reported for the 8760 h of the calendar year only.
- Register entry: "ISO suite: irradiance per orientation taken from the standard's
  climate file; HiSim's window optics bypassed; F_w = 0.90 as specified".

## 8. Comparison and acceptance logic

### 8.1 ASHRAE 140 — Annex A3

- Per range case: value = annual heating or sensible cooling of the case (600, 900), or the
  difference to the parent case as the table defines it (`parent`, `reverse` in
  `suite.yaml`). `pass` if lower ≤ value ≤ upper, else `fail`. Report value, lower, upper,
  position within the range and distance to the nearer bound; the bounds appear only in the
  private report.
- Diagnostics (peaks, monthly loads, hourly profiles, free-float temperatures, bins):
  status `info`, no range, no test.
- **Group accounting** (FR16), per group and time step:
  `ranges_total`, `minimum_required`, `attempted`, `met`, `failed`, `known_deviation`,
  `skipped`, `not_modelled`, `formal_result`. A range that is not attempted counts as not
  met: `formal_result = pass` only if `met ≥ minimum_required`, else `not_passed`. With the
  phase-1 case list the low-mass group has 5 ranges `not_modelled` (610, 630 both metrics,
  650 cooling) and the high-mass group 7 (910, 930, 960 both metrics, 950 cooling), so
  both groups are `not_passed` by construction. The report says so explicitly and never
  states conformity with Standard 140; the text "attempted ranges met: m of n" is the
  headline of the verification.
- **Information for a later decision on §2.3 (low-mass group).** With 3 misses allowed
  (21 − 18, Table A3-14), shading alone (650 still not modelled) would leave 2 misses among the other 20
  ranges, shading plus night ventilation 3 among 21. Let F be the number of `fail` among the
  16 attempted low-mass ranges at 3600 s: F ≥ 4 → no extension can change the formal
  result; F ≤ 3 → an extension could. The high-mass group cannot be passed by any
  single-zone model: Case 960 alone uses the 2 allowed misses (19 − 17, Table A3-14). The report states
  this as a property of the test procedure.

### 8.2 EN ISO 52016-1 — reported deviations

- For every quantity of Tables 28 to 34: HiSim value, deviation absolute and relative to
  the reference (relative only for energies and loads; temperatures as K). Status `info`
  unless the human sets a tolerance in `suite.yaml`.
- `suite.yaml` may carry `tolerance: {annual_energy_pct: …, annual_operative_temperature_k: …}`,
  set by the human only. When set, the annual outputs get `pass`/`fail` against it and the
  report states that the tolerance is the project's own, not the standard's.
- Case 900 is compared with the corrected columns (§7.3 notes).
- Monthly and hourly outputs are plotted against the reference (private report) and as
  HiSim-only curves (public report).

### 8.3 Deviation register (FR18)

One register per suite, in `suite.yaml`, printed in every report and exported as
`deviations.md`. Nothing departs from the standards' descriptions without an entry.
Three categories:

| Category | Meaning | Who adds | Effect on status |
|---|---|---|---|
| `model` | the model cannot represent the feature (§3.2.7, §3.2.9, §3.2.10, the isotropic sky of §3.2.8) | agent proposes, human confirms | none |
| `input_mapping` | the input is represented, but through an approximation or a rule the standard leaves to the program (§3.2.1, 3.2.3, 3.2.4, 3.2.6, 3.2.8, 3.2.11, §7.4) | agent proposes, human confirms | none |
| `known_deviation` | a result is outside its range and the human accepts that for now | human only | status `known_deviation`, pytest `xfail(strict=False)`, counts as not met in the group accounting |

Entry fields: `id`, `category`, `standard_clause` (with [verify] until confirmed),
`description` of the standard's requirement, `hisim_treatment`, `approved_by`,
`approved_on`. A `known_deviation` additionally names `case`, `output` and optionally
`timestep_s`:

```yaml
  - id: case900_cooling_3600
    category: known_deviation
    case: "900"
    output: annual_cooling
    timestep_s: 3600          # optional; omit for all time steps
    description: "annual sensible cooling above the Table A3-4 upper bound"
    hisim_treatment: "single-capacity 5R1C model; no change"
    approved_by: "<name>"
    approved_on: "YYYY-MM-DD"
```

### 8.4 Time-step consistency (reported, not pass/fail)

For each case: differences in annual demand, peak loads and temperatures between the
3600 s, 900 s and 60 s runs. Shown in the report and in a dedicated plot.

## 9. Testing and CI

### 9.1 pytest
- Marker `verification` for slow suite tests (register in `pytest.ini`).
- Parametrise by (suite, case, time step, output), e.g.
  `test_verification[ashrae140-620-900-annual_cooling_diff]`.
- Each (case, time step) is simulated **once** per session (session-scoped cache).
- Failure message: value, unit, status, deviation and path to the exported plot. Do not
  print private reference values in logs; differences between HiSim results are not
  private.
- The suite tests carry the job marker `verification` (D14) and skip unless
  `HISIM_VERIFICATION_DATA` is set; run with `pytest -m verification`.
- Option to restrict time steps, e.g. `--verification-timesteps 3600,900`.
- Regression: HiSim's own results are compared with `golden_references/bestest/` (FR17)
  with the tolerance mechanism of the golden-reference system; blessing follows that
  system's procedure and is done by the human.

### 9.2 Fast tests (every push/PR)
- Schema validation of all case files, including inheritance resolution.
- §3.2 derivation for cases 600 and 900 against Appendix B (H_tr_op, H_tr_w, C_m, A_m,
  A_tot, H_ve, scaled ACH), after the human has verified Appendix B.
- Aggregation, comparison, group-accounting and deviation-register logic with synthetic
  values.

### 9.3 Gating and CI
- `gating: false` in every `suite.yaml` until the human has reviewed the first full
  report of the suites in scope at all three time steps. While gating is off, criterion tests
  report their status and pass. The human switches gating on per suite; from then on
  status `fail` fails the test.
- Development runs locally. CI comes with the private repository on JuGit (GitLab CI);
  the public GitHub Actions workflows of HiSim are not touched by this spec. Frequency per
  time step is decided then.
- Private reference data reach the CI from a protected location (§7.2). Without them the
  affected tests skip, not fail.
- The CI publishes the **public report** (HiSim values, statuses, group accounting,
  deviation register, no reference values) as a job artifact and writes the full report to
  protected storage.

## 10. Export: `BESTEST_output/`

HiSim's write guard (`hisim/write_guard.py`, `CalculationScope`) fails a run that writes
outside the registered directories. `BESTEST_output/` is therefore created inside the
calculation's result directory; `--output-dir` is accepted only as a registered target.
Nothing is written to the repository, the working directory or the user's home.
On Windows the guard refuses the `\\.\nul` open that `numpy.testing` performs when `pvlib` is
imported inside the scope (bead hisim-repo-cleanup-o6a.36); the verification runner sets
`HISIM_WRITE_GUARD=collect` until that is fixed (decision 2026-11-02).

```
<result directory>/BESTEST_output/
  summary.md                 # per suite and time step: status counts, group accounting, deviation register
  summary.json               # machine-readable: metadata + all results + group accounting + register
  deviations.md              # deviation register (§8.3)
  results.csv                # one row per (suite, case, time step, output)
  parameters/<case>.json     # derived 5R1C parameters, scaled inputs, warm-up, resolved case (FR1, FR3)
  timeseries/<case>_<timestep>.csv   # zone air and operative temperature, heating and cooling power
  plots/
    ashrae140_annex_a3.png   # each range case: value as point within its range, per time step (private)
    ashrae140_annex_a3_public.png   # same without the range bounds
    ashrae140_ff_temperatures.png
    iso52016_monthly_<case>.png
    iso52016_january4_<case>.png
    timestep_consistency.png
```

- `summary.json` metadata: HiSim version and git commit, date/time, Python version, time
  steps, interpolation method, suite versions, reference source IDs, gating state,
  tolerances used, warm-up, standards cited with [verify] flags resolved or open.
- `results.csv` columns:
  `suite,group,case,parent_case,timestep_s,output,unit,value,ref_value,ref_min,ref_max,criterion,deviation_abs,deviation_rel_pct,status,note`
- `--public` produces the reduced report without `ref_*` columns and without range bounds
  in plots (§12).
- Plots publication-ready; `--plot-format pdf|svg|png`.

## 11. Implementation order (one PR each)

| Step | Content | Needs from §A |
|---|---|---|
| 1 | Gap analysis (§5) — **stop for review** | – |
| 2 | Missing capabilities, each with unit tests, in two PRs. 2a: EPW reader labelling in the station's fixed UTC offset and Denver catalogue entry (FR6), the Denver EPW with licence and provenance, the relative-humidity column and the station-header entry the weather requires since the merge (gap analysis §7.2 row 9), the job marker `verification` per D14 in `pytest.ini`, `tests/job_markers.py` and `system_docs/testing.md` (bead hisim-x6c7.4). 2b: the overrides of FR2 on the TABULA row (window list with angles and optics per window, air change rates, air volume, transmission adjustment factors, C_m, A_m, A_tot) and the origin record in `BuildingInformation`, which also builds the `WindowGeometry` list that `Building.get_windows` now takes instead of building it from the five TABULA directions; the schema regenerated (FR2; gap analysis D16 and D17, revising D6; bead hisim-x6c7.6). Unit tests: each override reaches the Building's conductances and windows exactly, the origin record, the refusal of a list beside `window_area_in_m2`, the TABULA path bit-identical (same window geometries, same solar-gains cache key, goldens unchanged). The rules of §3.2.3 and 3.2.4 (κ, C_m, A_m, A_tot) belong to the system builder, step 3. Not in this step (decided 2026-11-02): heater changes, a constant-gain component (`CSVLoader` is used), an operative-temperature output (runner), the ISO items (deferred) | gap analysis confirmed 2026-11-02 |
| 3 | Schema, case loader with inheritance, system builder (derives κ, C_m, A_m and A_tot by §3.2.3 and 3.2.4 and sets the overrides of FR2), deviation register, two-pass warm-up; case 600 of the ASHRAE suite runs end to end at all three time steps; parameters output; EPW identity check documented in `PROVENANCE.md` | EPW and supplemental files in the private folder, Appendix B verified (§3.2.1/3.2.3/3.2.4 decided 2026-11-02) |
| 4 | Aggregation, metrics, comparison, group accounting, fast tests (§9.2) | – |
| 5 | Export (§10) and CLI (FR14) | – |
| 6 | `ashrae140` suite, 18 cases, pytest integration (§9.1), first full report for review | filled Annex A3 file, confirmed Table A3-14 counts |
| 7 | `iso52016` suite, 6 cases, first full report for review — **deferred** (2026-11-02, §2.1) | filled Tables 28–34 file; a solar-gain input on the Building (§7.4) |
| 8 | Golden references for HiSim's own results (FR17) and documentation (`docs/verification/README.md`) | human blesses the goldens after reviewing the reports |
| 9 | CI on the private JuGit repository (§9.3) | repository, protected data location |

Phase-2 extensions (§2.3) are not planned and have no step.

## 12. Licensing and publication boundary (for the human; the agent does not decide)

The boundary is what pyBuildingEnergy publishes. In doubt, private; the agent asks before
adding any number taken from a standard to a public file.

| Published by pyBuildingEnergy, therefore allowed | Not published by pyBuildingEnergy, therefore private |
|---|---|
| case inputs as model parameters with table citations (geometry, layers, U-values, κ, window data, setpoints, gains, infiltration) | text, tables, figures or PDFs of the standards |
| the Denver TMY3 EPW with its BSD-style licence | the Annex A3 range bounds |
| reference-file templates with `null` values and table names | the values of Tables 28 to 34 |
| the structure of the range cases (parent, reverse) and the counts of Table A3-14 | the ISO climate spreadsheet |
| per-range PASS/FAIL without bounds, "ranges met: m of n" per group | the ASHRAE supplemental files |
| HiSim's own results, tables and plots without reference values | any reference value in logs, plots, test output or commit messages |

Further notes:

- Private data never enter a HiSim repository (§7.2).
- pyBuildingEnergy is BSD-3-Clause (Energy Efficient Buildings group, EURAC). Reused code
  or data keep the copyright notice; cite the Zenodo DOI in `docs/verification/README.md`
  and in the paper.
- The EPB Center comment list is a public web page; cite it for the Case 900 correction and
  state that it is an external correction, not an official ISO reprint.
- The Denver EPW `725650TYCST.epw` is redistributed under the `LICENSE.md` of the
  BESTEST-GSR repository (Copyright (c) 2026, Alliance for Energy Innovation, LLC), not
  under HiSim's MIT licence. The licence is BSD-style with four conditions (text read on
  2026-11-02; the file governs): (1) source redistributions retain copyright notice,
  conditions and disclaimer; (2) binary redistributions reproduce them in the accompanying
  materials; (3) a research entity that redistributes makes the entire corresponding
  source publicly available under this licence; (4) an unmodified redistribution refers
  to the software by its designation "Building Energy Simulation Test - Generation
  Simulation and Reporting", a modified one may not use that designation. How HiSim meets
  them: the file is committed byte for byte (`.gitattributes`: `*.epw -text`) under its
  original name; `LICENSE.md` lies unchanged beside it; `PROVENANCE.md` records origin,
  commit, hashes and the identity check against the normative CSV; the README names the software by its
  designation in the Acknowledgement and excludes the file from the MIT licence in the
  License section; the folder ships in the sdist and wheel (`include_package_data`,
  `MANIFEST.in`). pyBuildingEnergy renamed the file, ships no licence text and labels it
  "BSD-style" in one README line; HiSim does not follow that. The human confirms that
  the four conditions are acceptable for the HiSim repository before step 3.
- IEA BESTEST 1995 (NREL report) may be cited as public context; its tables are not
  reproduced unless the human clears it.
- VDI 6007-1 is not used: its terms for use in software require written permission and
  licence fees, the full text must be shipped with the software, and transfer into
  databases is prohibited.

## 13. Definition of done

- [x] Gap analysis reviewed (2026-11-02)
- [ ] Missing capabilities implemented with unit tests
- [ ] `pytest -m verification` runs the `ashrae140` suite at 3600 s, 900 s and 60 s (`iso52016` deferred)
- [ ] With gating off, criterion tests report and pass; with gating on, they fail on `fail`; tests skip without data, xfail on approved known deviations; no test for `info` and `not_modelled`
- [ ] `BESTEST_output/` contains summary.md/json, deviations.md, results.csv, parameters, time series, plots incl. time-step consistency, inside the result directory
- [ ] Group accounting per ASHRAE group and time step in summary and plots; no conformity claim anywhere
- [ ] Deviation register complete: every §3.2 rule and §7.4 treatment that departs from the standard has an entry
- [ ] Reduced public report available via `--public`; it contains nothing from the right column of §12
- [ ] HiSim's own results blessed as golden references by the human
- [ ] CLI works for single case, single time step and whole suite
- [ ] `docs/verification/README.md` explains purpose, usage, input mapping (§3.2), deviation register, data handling, limitations
- [ ] Every [verify] of this spec resolved in the README or in code comments against the standard
- [ ] No reference value in the repository that was not provided by the human; none of the private files in any repository

## 14. Related literature

Not needed to implement this spec. Grouped by when it is useful.

### For understanding (useful now)
| Literature | Why |
|---|---|
| pyBuildingEnergy, EURAC, Zenodo DOI 10.5281/zenodo.10887919; `examples/ashrae140_bestest/README.md` and `examples/iso52016_bestest/README.md` | Precedent for both suites with an ISO 52016-1 model: case inputs, mapping decisions, private-data handling, result tables for a code-to-code comparison in the paper |
| [ASHRAE 140 User's Manual, Part I (PDF)](https://data.ashrae.org/standard140/140UsersManual-PartI-Final%20(050825).pdf) | Overview of how the ASHRAE 140 test procedure is applied |
| Michalak (2019), Energy and Buildings 202, 109337, [DOI](https://doi.org/10.1016/j.enbuild.2019.109337) | Precedent: 5R1C model verified against BESTEST, EN 15265 and EnergyPlus; comparison values for the paper |
| Michalak (2017), Energy and Buildings (Simulink 5R1C model) | Earlier verification of a 5R1C implementation |
| Lauster et al. (2014), Building and Environment 73, 223–231 | How a reduced-order RC model (VDI 6007, TEASER/AixLib) was verified with standard test cases |
| ISO 13786:2007, Annex A | Effective-thickness rule behind κ (§3.2.3) |
| CEN ISO/TR 52016-2:2017 | Explanation of the ISO 52016-1 hourly method; context for the differences to the 13790 method |
| [EPB Center, comments on EN ISO 52016-1:2017](https://epb.center/support/documents/iso_52016-1_details/) | Case 900 correction of Tables 28/29 |

### For later extensions
| Literature | Relevant when |
|---|---|
| [ASHRAE 140-2023 Addendum a (PDF)](https://www.ashrae.org/file%20library/technical%20resources/standards%20and%20guidelines/standards%20addenda/140_2023_a_20250829.pdf) | Adding time-series acceptance criteria for thermal fabric tests |
| [ASHRAE 140-2023 Addendum b (PDF)](https://www.ashrae.org/file%20library/technical%20resources/standards%20and%20guidelines/standards%20addenda/140_2023_b_20260424.pdf) | Adding empirical (measured-data) test cases |
| ANSI/ASHRAE 140-2023, Section 7.2.3 | In-depth diagnostic cases, if a diagnostic series is wanted |
| EN ISO 52016-1:2017, Annex F | Shading formulas F.4 to F.6 and F.19 to F.22, should §2.3 ever be reopened [verify] |
| VDI 6007-1:2015-06 | Only if written permission from the VDI is obtained |
| EN 14825 / Heat Pump Keymark data | Adding heat pump component checks |

## Appendix B. Worked derivation for cases 600 and 900 (to be verified)

Purpose: make §3.2.3 and §3.2.4 checkable by hand and give `test_parameter_derivation.py`
its expected values. Layer data are pyBuildingEnergy's transcription of ASHRAE Tables 7-2
and 7-27 [verify against the standard]; U-values are not derived here (§3.2.1 takes the
printed ones). All values below were computed on 2026-11-02 and are to be confirmed by the
human before they enter the test.

### B.1 Geometry

| Surface | Area [m²] |
|---|---:|
| Floor | 48.0 |
| Ceiling (roof) | 48.0 |
| North wall | 21.6 |
| South wall, opaque part | 9.6 |
| South window | 12.0 |
| East wall | 16.2 |
| West wall | 16.2 |
| **A_tot** (all surfaces facing the zone) | **171.6** |
| Opaque wall area, sum | 63.6 |
| Floor area A_f | 48.0 |
| Volume V | 129.6 m³ |

Λ_at = A_tot / A_f = 3.575 (the ISO 13790 default would be 4.5 → 216.0 m²).

### B.2 Layers, inside to outside (d [m], ρ [kg/m³], c [J/(kg K)])

| Element | Layer 1 | Layer 2 | Layer 3 | Total d [m] |
|---|---|---|---|---:|
| 600 wall | plasterboard 0.012 / 950 / 840 | insulation 0.066 / 12 / 840 | wood siding 0.009 / 530 / 900 | 0.087 |
| 600 roof | plasterboard 0.010 / 950 / 840 | insulation 0.1118 / 12 / 840 | roof deck 0.019 / 530 / 900 | 0.1408 |
| 600 floor | timber 0.025 / 650 / 1200 | insulation 1.003 / 0 / 0 | – | 1.028 |
| 900 wall | concrete block 0.100 / 1400 / 1000 | insulation 0.0615 / 10 / 1400 | wood siding 0.009 / 530 / 900 | 0.1705 |
| 900 floor | concrete slab 0.080 / 1400 / 1000 | insulation 1.007 / 0 / 0 | – | 1.087 |
| 900 roof | as 600 roof | | | |

### B.3 κ per element under the ISO 13786 effective-thickness rule (§3.2.3)

| Element | (a) half thickness [m] | (b) up to insulation [m] | (c) cap [m] | effective d [m] | κ [J/(m²K)] |
|---|---:|---:|---:|---:|---:|
| 600 wall | 0.0435 | 0.012 | 0.10 | 0.012 | 9 576 |
| 600 roof | 0.0704 | 0.010 | 0.10 | 0.010 | 7 980 |
| 600 floor | 0.514 | 0.025 | 0.10 | 0.025 | 19 500 |
| 900 wall | **0.08525** | 0.100 | 0.10 | 0.08525 if (a) applies, else 0.100 | **119 350** if (a) applies, else 140 000 |
| 900 floor | 0.5435 | 0.080 | 0.10 | 0.080 | 112 000 |
| 900 roof | 0.0704 | 0.010 | 0.10 | 0.010 | 7 980 |

Condition (a) is decisive for the Case 900 wall only. Confirm in ISO 13786, Annex A,
whether the half-thickness condition is part of the rule; the test takes the confirmed
variant. ISO 13790 itself (FDIS 2007, 12.3.1.1 and Table 11) states only the 0.10 m cap and
refers to ISO 13786 for the method; ISO 13786 was not available on 2026-11-02, so the rule is
implemented with all three conditions and (a), (b) stay [verify] (decision of 2026-11-02). For comparison, pyBuildingEnergy uses 14 534 (600 wall) and 145 154 (900 wall)
by summing all layers, which is the wrong rule for a 13790 model.

### B.4 C_m, A_m and the ISO class defaults

| Quantity | Case 600 | Case 900, (a) applies | Case 900, (a) does not apply |
|---|---:|---:|---:|
| C_m = Σ κ_j·A_j [MJ/K] | 1.928 | 13.350 | 14.663 |
| C_m / A_f [J/(m²K)] | 40 168 | 278 119 | 305 480 |
| Σ A_j·κ_j² [J²/(m²K²)] | 2.7141 × 10¹⁰ | 1.5111 × 10¹² | 1.8517 × 10¹² |
| A_m = C_m² / Σ A_j·κ_j² [m²] | 137.0 | 117.9 | 116.1 |

ISO 13790 Table 12 defaults (FDIS 2007 12.3.1.2, checked 2026-11-02), for comparison only: "very light" C_m = 80 000·A_f =
3.84 MJ/K with A_m = 2.5·A_f = 120 m²; "heavy" 260 000·A_f = 12.48 MJ/K with 3.0·A_f =
144 m²; "very heavy" 370 000·A_f = 17.76 MJ/K with 3.5·A_f = 168 m². The Case 600 cell is
lighter than the lightest class, which is why §3.2.3 does not use the class table.

### B.5 Coupling conductances from these values (§3.2.5)

| Quantity | Case 600 | Case 900, (a) applies | Case 900, (a) does not apply |
|---|---:|---:|---:|
| H_tr_is = 3.45·A_tot [W/K] | 592.0 | 592.0 | 592.0 |
| H_tr_ms = 9.1·A_m [W/K] | 1 246.4 | 1 073.2 | 1 056.6 |

H_tr_op, H_tr_w and H_tr_em follow from the printed U-values (§3.2.1, §3.2.2) and are
filled in by the agent once the human has confirmed those values.
