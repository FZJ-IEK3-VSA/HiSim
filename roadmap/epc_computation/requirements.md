# Requirements: energy performance certificate (EPC) computation as a postprocessing option

**Status:** draft (in refinement with the requester)
**Date:** 2026-10-07
**Author(s):** Nicolai Gölz, drafted with Claude · **Reviewers:** Nicolai Gölz; HiSim maintainers on the PR
**Related:** GitHub issue #897 "EPC-calculator"; branch `897-epc-calculator`;
`roadmap/epc_computation/design.md` (the implementation specification of this document);
`roadmap/kpi_address_spec.md` (the KPI addressing the output follows); `cost_spec.md` §9.2–9.3 (the
pre-run input check the option copies); the standalone prototype `oenorm_hwb_calculator/` (local,
git-ignored, inspiration only, deleted after merge).

Status tags: `[given]` stated by the requester · `[proposed]` introduced by the author ·
`[decided YYYY-MM-DD]` confirmed, see the decision register in §11.3.

---

## 1. Abstract

HiSim simulates buildings with a dynamic model, but Austrian practice rates a building by the
heating demand of its energy performance certificate (Energieausweis), a monthly-balance figure
defined by ÖNORM B 8110-6-1 with the climate and usage profiles of ÖNORM B 8110-5 and the heating
period of ÖNORM H 5050-1 / H 5056-1. HiSim produces no such figure today, so its results cannot be
compared with the certificates of the buildings it simulates, nor with the stock dataset whose rows
carry certificate values. This work adds a postprocessing option that computes the certificate
heating demand of the one building a run simulated, exactly as the norms prescribe, from that
building's own configuration and an explicit site, and reports it as KPIs of the building beside the
simulated ones. The option is the first member of a country-keyed family, so a second country's
method can be added without touching the Austrian one.

## 2. Keywords and tags

```
Tags: feature
Keywords: EPC, Energieausweis, Heizwärmebedarf, HWB, ÖNORM B 8110-6-1, ÖNORM B 8110-5,
          ÖNORM H 5050-1, ÖNORM H 5056-1, ÖNORM B 8110-6-2, monthly balance, postprocessing,
          KPI, TABULA, Austria, reference climate, site climate
```

## 3. Executive summary

The certificate heating demand (HWB) is a norm calculation, not a simulation result: a monthly
balance with norm climate (reference climate, and site climate from climate region and elevation),
norm usage profile, norm correction factors and norm defaults. HiSim's `Building` already knows the
envelope the balance needs (areas, U-values, windows per orientation, g-value, thermal mass class,
apartments) from its TABULA row and from the per-element overrides its configuration allows; it does
not know the site (climate region, elevation) and it does not apply any ÖNORM rule. The work adds an
opt-in `PostProcessingOptions` member `COMPUTE_EPC` that, for a run whose country has an EPC method,
computes the HWB under reference and site climate for the building the run simulated and files the
results as KPIs of that building under their own tag. There is exactly one building per result: the
simulated one; whatever its configuration states, TABULA row or manual values, is what the
certificate is computed from, including the values HiSim itself derived (apartment count, door).
The calculation needs no time series, so every input it depends on is checked before the first
simulated time step, the way the lifecycle cost engine checks its inputs. Stage 1 covers the heating
demand of residential buildings for Austria on the 2024-03 edition package, in two increments: first
without, then with the recoverable hot-water losses the certificate's site-climate value contains.
The norm tables are licensed and stay local; the repository carries the formulas, the data contract
and functionality tests that run without any norm data; the reproduction of ÖNORM B 8110-6-2's worked
examples, to the precision the norm prints, is a local validation whose output accompanies the pull
requests. Without this, HiSim's only certificate-like figure is TABULA's national `q_h_nd`, computed
with a different climate and a different indoor temperature, which is not the certificate value and
misleads any comparison.

## 4. Context and current situation

### 4.1 HiSim today (evidence)

- Postprocessing is driven by `hisim/postprocessingoptions.py:PostProcessingOptions`, an `IntEnum`
  of 28 members numbered 1–30 (21 and 25 are retired gaps); numbers are wire format, never reused,
  and the next free number is 31.
- Postprocessing steps that compute derived figures follow one shape: the `COMPUTE_OPEX` and
  `COMPUTE_CAPEX` blocks of `PostProcessor.run` (`postprocessing_main.py:422–445`) each load one
  function of the sub-package `cost_and_emission_computation` lazily, call it with the wrapped
  components, the outputs, the results and the simulation parameters, write a CSV into the result
  directory and, when the PDF report is on, a report chapter. The KPI step runs afterwards; its
  `read_opex_and_capex_costs_from_results` (`kpi_preparation.py:694`) sums the meters' component
  KPIs and re-reads the two cost CSVs from the result directory.
- The current cost engine, `hisim.economics` (`cost_spec.md`, option `COMPUTE_LIFECYCLE_COSTS`),
  checks its inputs before the first time step: `Simulator.check_cost_declarations`
  (`hisim/simulator.py:513`, called at `:598`) refuses a run whose components cannot be priced
  "now rather than after hours of simulation" (`cost_spec.md` §9.2); its bridge repeats the check
  independently in postprocessing, because a stored input file re-priced later passes through no
  simulator.
- KPIs are `KpiEntry` objects (`hisim/postprocessing/kpi_computation/kpi_structure.py`) with a
  `KpiTagEnumClass` tag (42 tags today) and a structured `KpiSource`; the collection is keyed
  `"<name> (<source.name>)"` per building (`KpiAddress.key_for`), sorted by tag once, and written to
  `all_kpis.json` by `WRITE_KPIS_TO_JSON`, which depends on `COMPUTE_KPIS`
  (`tests/postprocessing_option_test_framework.py:DEPENDENCIES_BY_OPTION`). Two entries of one
  building with one key are refused (`keyed_component_entries`). A component's source is built by
  `Component.kpi_source()` (`hisim/component.py:706`); `roadmap/kpi_address_spec.md` gives derived
  KPIs (General tag, cost totals) no source.
- `hisim/components/building/config.py:BuildingConfig` always requires a TABULA `building_code`;
  there is no TABULA-free building in HiSim. The configuration may override, per element, the area and
  the U-value of floor, facade, roof, window and door, the conditioned floor area (or the base area)
  and the number of apartments; a configured value replaces the row's value in every downstream
  computation (`information.py:BuildingInformation._scaled_element_area_in_m2`,
  `_element_u_value_and_adjustment_factor`). Window orientation shares, g-value, volume and the
  boundary codes always come from the row. The configured conditioned floor area (or base area)
  scales the whole archetype by the factor configured area / `A_C_Ref`
  (`BuildingInformation.get_scaling_factor_according_to_conditioned_living_area`), so every
  row-derived area and volume follows that factor; `floor_area_in_m2` is the floor *element*, not
  the conditioned floor area.
- `BuildingInformation` derives from row and overrides: floor, wall, roof, window and door areas and
  U-values (each with its TABULA `b_Transmission` factor, a merged element taking the largest), window
  areas per direction (South, East, North, West, Horizontal), `g_gl_n`, `F_sh_vert`, `F_f`, `F_w`,
  the thermal-bridging surcharge ΔU (0.1 W/(m²K) substituted when the row says 0), air change rates,
  the thermal-mass class in five steps ("very light" … "very heavy"), the scaled conditioned floor
  area (TABULA `A_C_Ref` times the scaling factor) and the number of apartments. It records, per
  element, the U-value it uses and where it came from (`element_values`,
  `ElementValues.u_value_origin`: "configured", the row's columns, or a substitute). It holds no
  gross floor area, no volume, no site and no ÖNORM factor.
- Two of HiSim's building values are its own estimates, not the row's: the **door** of a row whose
  reference door area is 0 takes TABULA's `A_Estim_Door`, and its U-value, when the row states none,
  the mean of the country's national rows or a default (`information.py:785–867`); the **number of
  apartments** is `int()` of the configured value, else of the row's `n_Apartment` when the archetype
  is unscaled, else of the conditioned floor area divided by 92.1 m² (German average, 2021) — so an
  archetype configured with a conditioned floor area always takes the estimate, truncated
  (`information.py:520`, `:989–1022`).
- The `Building` already reports KPIs from this information (heating load, conditioned floor area,
  roof area, specific heating load, TABULA reference energy need for heating) and from its time
  series (theoretical heating and cooling demand, solar and internal gains)
  (`building.py:Building.get_building_kpis_from_building_information`).
- Place properties live on the weather: `hisim/components/weather/config.py:WeatherConfig` states the
  station (`location`), the data file and the outside design temperature, which "belongs to the place
  the building stands in rather than to the building" (decision D-21 of the sizing design). Every run
  has exactly one weather. In an energy-system file, `config:` beside a `preset:` or a `constructor:`
  assigns fields (`hisim/energy_system/entries.py:143`); the committed schema
  `hisim/energy_system_v3.schema.json` is generated from the configuration classes and pinned by
  `tests/test_cli.py::test_schema_writes_exactly_the_committed_file`.
- `SimulationParameters.country` is a free string with default `"DE"`, read today by the cost and
  emission lookups (`hisim/simulationparameters.py:46`). The ai4c runs set `country: "DE"`
  (`ai4c/renovation_measures/renovation_measures.simulation.yaml:16`) and switch to `"AT"` for the
  certificate `[decided 2026-10-07]`.
- `tests/test_postprocessing_options.py` has one named test per option and a guard that fails when
  an option has no test. The option framework (`tests/postprocessing_option_test_framework.py`) runs
  two fixed setups, `simple_system_setup_one` and `basic_household` (German archetype), with the
  default country and no site; it cannot run an Austrian certificate. Tests whose prerequisites exist
  only on some machines skip with a reason (`tests/test_utils.py` for a missing database, marker
  `upstream_history` for a missing git history).
- The ai4c runs (`ai4c/ai4c/hisim_execution.py`, `renovation_measures/templates/*.energy_system.yaml`)
  configure buildings by TABULA code (`AT.N.SFH.…`) plus conditioned floor area; U-value overrides
  are the foreseen next step.

### 4.2 TABULA data for Austria (evidence, `hisim/inputs/housing/data_processed/episcope-tabula.csv`)

| Fact | Count / value |
|---|---|
| Austrian rows / national rows (`AT.N.`) | 171 / 102 (34 archetypes × 3 refurbishment states) |
| `A_C_Ref` = `A_C_IntDim` = 0.8 × `A_C_ExtDim` | 93 of 102 national rows (exception: `AT.N.SFH.05` with ratio 1.0; the 6 synthetic rows ≈ 0.85) |
| `A_C_ExtDim` definition (info workbook) | "conditioned gross floor area … on the basis of external dimensions" |
| `V_C` definition (info workbook) | "conditioned volume of the building (external dimensions)" |
| rows with `A_C_ExtDim` or `V_C` empty or 0 | 0 |
| `Code_ConstructionBorder_*` | empty for every Austrian row |
| `Code_AtticCond` / `Code_CellarCond` / `Code_RoofType` | values N, P, C, "-", 0 / N, P, C, "-", 0 / FR, TR, 0 |
| `b_Transmission` values | 0, 0.5, 1 |
| sub-elements with area > 0 and `b_Transmission` = 0 | `AT.N.AB.05`: every opaque sub-element; `AT.N.SFH.05`: Wall_2; `AT.N.TH.03`: Wall_3, Roof_2 (3 rows each) |
| (RoofType, AtticCond) combinations | 10, among them (0, N) for `TH.01` and (FR, N), (FR, C) for `MFH.08`, `SFH.08` |
| floors with `b_Transmission` = 1 | with CellarCond N, with C (`MFH.08`) and with P (`MFH.05`) |
| rows with two roof or two floor sub-areas | 30 of 102 each |
| `n_Apartment` | 1 … 25 |
| `A_Window_Horizontal` | 0 in every national row |
| national boundary conditions | θ_i 20 °C, `n_air_use` 0.4 h⁻¹, `h_room` 2.5 m: TABULA's, not ÖNORM's |

Consequences: HiSim's "conditioned floor area" for an Austrian archetype is the ÖNORM reference area
BF (0.8 × BGF), not the gross floor area BGF; BGF and V are available from the same row. A
`b_Transmission` of 0 on a sub-element with area marks a party wall or ceiling to a conditioned
neighbour (adiabatic), which HiSim gives no conductance.

### 4.3 The norms (the binding sources)

Available to the author, with edition. None of them is committed: the documents and their tables are
licensed content (C6).

| Norm | Title (short) | Editions at hand | Role for stage 1 |
|---|---|---|---|
| ÖNORM B 8110-6-1 | Heizwärmebedarf und Kühlbedarf | 2024-03, 2025-10 | the monthly balance |
| ÖNORM B 8110-5 | Klimamodell und Nutzungsprofile | 2024-03, 2025-10 | climate, usage profile, hot-water demand (Tab. 9) |
| ÖNORM B 8110-6-2 | Validierung | 2024-03 | worked examples (validation oracle) |
| ÖNORM H 5050-1 | Gesamtenergieeffizienz | 2024-03 | heating-period fraction for RK and SK (6.2.5, 6.4.1, 6.5.1) |
| ÖNORM H 5056-1 | Heiztechnikenergiebedarf | 2024-03, 2025-10 | heating days (7.1); hot-water loss chain and Q_TW,beh (Ch. 6, Eq. (15)) |
| ÖNORM H 5056-2 | Validierung Heiztechnik | being procured | reference equipment "Ausstattung 1" behind B 8110-6-2 cases 3 and 4; oracle of stage 1b |
| ÖNORM H 5057-1 / 5058-1 / 5059-1 | RLT, Kühltechnik, Beleuchtung | 2019-01 | out of scope |
| OIB-Richtlinie 6, Leitfaden | | 2011, 2015 | context only |

**The 2025-10 editions are a methodological revision, not a renumbering** (verified on the texts):

| Item | 2024-03 | 2025-10 |
|---|---|---|
| Indoor set temperature θ_i,h, residential (B 8110-5 Tab. 7) | 22 °C | 20 °C |
| Monthly HWB, reference climate (B 8110-6-1) | Q_h,j = (Q_l − η·Q_g) · f_h, Eq. (52) | Q_h,j = Q_l − η·Q_g, zero if γ > 2 or (γ ≤ 0 and Q_g > 0), Eq. (80)–(82); no f_h |
| Monthly HWB, site climate | Q_h,j = (Q_l − η·Q_g) · d_Heiz/MT, Eq. (53) | as above, Eq. (83)–(85); no d_Heiz |
| Utilisation parameter a = a₀ + τ/τ₀ | τ₀ = 16.0, Eq. (59) | τ₀ = 15.0, Eq. (98); extra η cases for γ ≤ 0 |
| Heating days (H 5056-1) | d_Heiz = f_H · d, Eq. (49) | d_Heiz = d, Eq. (59); f_H kept for the HVAC part |
| Heating degree days HGT 22/14 (B 8110-5 5.2.3) | defined, Eq. (4) | section removed |
| f-factors, thermal-bridge default, V_V = 0.8·BGF·2.6, F_g, f_g = 0.7, F_S defaults, f_BW, reference climate, regression and transposition tables, usage tables 6/8/9 | | unchanged |

No 2025 edition of B 8110-6-2 or H 5050-1 is at hand; only the 2024-03 package is complete and
consistent, and the certificates of the ai4c buildings were issued under its 22 °C regime (D8).

**Effect of the recoverable hot-water losses Q_TW,beh** (B 8110-6-2:2024, reference climate, case 1
without vs. case 3 with Q_TW,beh of the reference equipment). They shorten the heating period and
enter the balance as a utilised gain: in January of the SFH, a full heating month in both cases, the
monthly value drops from 2 551 to 2 302 kWh.

| Example | HWB case 1 | HWB case 3 | Difference |
|---|---|---|---|
| SFH variant 1 (U_wall 0.26) | 61.32 | 52.60 | −14 % |
| SFH variant 2 (U_wall 0.10) | 16.91 | 10.47 | −38 % |
| MFH variant 1 (U_wall 1.39) | 181.93 | 179.06 | −1.6 % |
| MFH variant 2.1 (U_wall 0.10) | 19.28 | 17.31 | −10 % |

The certificate's site-climate HWB (case 4) includes Q_TW,beh. The method of Q_TW,beh is in
H 5056-1 Chapter 6 (at hand); H 5056-2 is needed for the equipment definition of the validation cases
and as their oracle (D11).

**Interpolation.** B 8110-6-1:2024 9.1 (p. 49) lists the radiation values in 22.5° azimuth and 15°
tilt steps and permits linear interpolation for any other azimuth or tilt ("Zwischenwerte … dürfen
mittels einer linearen Interpolation durchgeführt werden").

**Heating degree days.** B 8110-5:2024 Eq. (4), p. 7, reads HGT22/14 = Σ_j (θ_i,h − θ_e,j) for
θ_e,j ≤ 14 °C with θ_e,j the monthly mean and the unit Kd/a, which the sum only yields when each
month's difference is multiplied by its days. Read that way, the norm's climate model reproduces the
HGT printed on the three real certificates at hand exactly (4169, 3762 and 3731 Kd for region S/SO at
498, 356 and 327 m).

**Conditioned neighbours.** B 8110-6-1:2024 Section 4 "Gebäudegeometrie", p. 8: "Jene Flächen, die
an konditionierte Räume in anderen Gebäuden/Gebäudeteilen grenzen, werden nicht zur Gebäudehülle
gezählt", and item g): where the conditioned volume borders a conditioned building, the centre line
of the construction between them is the boundary. A party wall or ceiling to a conditioned
neighbour is therefore outside the thermal envelope: it has no factor in Tables 3–5 and no part in
L_T or A.

### 4.4 Current vs. required behavior

| | |
|---|---|
| **Current** | No certificate value exists. A run reports the simulated theoretical heating demand and TABULA's `q_h_nd`. |
| **Required** | With the new option set, a run with an Austrian method reports the certificate HWB under reference and site climate for the simulated building, computed exactly by the norms from that building's configuration, plus its building and climate inputs, as KPIs of the building. An input the certificate cannot be computed from refuses the run before the first time step. All other outputs are unchanged. |
| **Assumptions** | see §9. |

This is a new capability; no existing behavior changes.

## 5. Goals and non-goals

**Goals**

- G1 A postprocessing option that computes the Austrian certificate heating demand (HWB) of the
  residential building a run simulated, under reference climate and under site climate. `[decided 2026-10-07, D1]`
- G2 The calculation reproduces the norm exactly, with every number traceable to a clause, table or
  equation; norm defaults are applied wherever the norm prescribes or permits them; where the norm
  defines a value, no substitute from TABULA or HiSim is used. `[decided 2026-10-07, D4]`
- G3 The option is the first member of a country-keyed family; adding a second country is additive. `[given]`
- G4 Correctness is demonstrated two ways: functionality tests of every equation with hand-given
  values, committed and run on CI; and the reproduction of ÖNORM B 8110-6-2's worked examples to the
  precision the norm prints, run locally where the norm data exist. `[decided 2026-10-07, D9, D13, D17]`
- G5 One building only: the certificate is computed for exactly the building HiSim simulated, from
  whatever its configuration states, a TABULA row or manually given values, and from the values HiSim
  derived for it; the method never introduces a second building description and never re-derives a
  value HiSim already holds. `[decided 2026-10-07, D14, D15, D18]`
- G6 Stage 1b: the site-climate HWB includes the recoverable hot-water losses as the certificate does,
  validated against B 8110-6-2 case 4 once H 5056-2 is at hand. `[decided 2026-10-07, D11]`
- G7 Nothing licensed is published: the norm tables stay local, the repository carries formulas,
  constants and the data contract, and no committed test contains a norm value. `[decided 2026-10-07, D16, D17]`
- G8 A run that cannot produce the certificate it was asked for is refused before the first time
  step, so a year-long simulation never dies in postprocessing for an input that was wrong from the
  start. `[decided 2026-10-07, D21]`

**Non-goals (stage 1)**, kept as the parking lot of §11.2

- Other certificate values: WWWB as a reported value, HEB, HTEB, EEB, PEB, CO₂, fGEE, cooling demand KB. `[given]`
- The 2025-10 edition package. `[decided 2026-10-07, D8]`
- Non-residential buildings; mechanical ventilation with heat recovery; unconditioned glass porches;
  transparent insulation; detailed shading (Tables 11–13) and detailed thermal bridges (ISO 10211).
- Resolving climate region and elevation from a municipality or cadastral code. `[given]`
- A comparison KPI against the simulated heating demand. `[given]`
- A separate output file with the monthly balance. `[decided 2026-10-07, D6]`
- A building description of the method's own, and any certificate-only configuration field on the
  `Building`; the certificate uses exactly what the `Building` uses (R5.9). `[decided 2026-10-07, D14, D15]`
- Making HiSim's `Building` run without a TABULA code (P9).
- A results file in which HiSim describes every parameter it used, as the certificate's input (P11).

## 6. Use cases, examples and mockups

**UC1 — Austrian archetype, certificate KPIs.** A run with `country: "AT"`, a `Building` with a
TABULA code `AT.N.SFH.04.Gen.ReEx.001.001`, a site (climate region and elevation) and the options
`COMPUTE_KPIS`, `COMPUTE_EPC`, `WRITE_KPIS_TO_JSON`. Expected: `all_kpis.json` carries, under a tag
of its own and keyed to the building, the HWB under reference and site climate, the annual loss and
gain terms, the conductances, and the building and site quantities they were computed from (Mockup A).

**UC2 — Archetype with manually given envelope values.** As UC1, but the building configuration sets
`facade_u_value_in_watt_per_m2_per_kelvin: 0.25` and `window_u_value_in_watt_per_m2_per_kelvin: 1.1`.
Expected: the certificate uses exactly the values HiSim's building uses, so the overrides enter L_T
and the KPIs state them.

**UC3 — Validation example (local).** The SFH of ÖNORM B 8110-6-2:2024 Section 4 (12.00 m × 8.00 m ×
6.00 m, BGF 192 m², V 576 m³; 24.00 m² of windows: 12.00 S, 4.80 E, 4.80 W, 2.40 N; light
construction; region ZA at 750 m; U-value variant 1) is handed to the method's calculation core
directly, as the same set of quantities the method otherwise derives from a `Building`. Expected: the
twelve monthly values and the annual HWB of Table 4, cases 1 and 2, to the printed precision (R9).
This is a validation path on a machine that holds the norms, not a user input (G5) and not a
committed test (D17).

**UC4 — Country without a method.** `country: "DE"` and `COMPUTE_EPC` set. Expected: the run is
refused before the first time step with the message of R2.

**UC5 — Missing site.** `country: "AT"`, `COMPUTE_EPC` set, no climate region or elevation given.
Expected: the run is refused before the first time step with a message naming the missing fields and
the admissible climate regions.

**UC6 — Missing norm tables.** `country: "AT"`, `COMPUTE_EPC` set, no tables under
`hisim/inputs/epc/at/`. Expected: the run is refused before the first time step with a message naming
the folder, the norms and the data contract (R8.5); nothing else of the run changes.

**UC7 — Apartment count HiSim estimated as 0.** `country: "AT"`, `COMPUTE_EPC` set, an archetype with
a configured conditioned floor area of 80 m² and no configured apartment count; HiSim's estimate
truncates to 0. Expected: the run is refused before the first time step with a message naming the
building, the estimate and the configuration field `number_of_apartments` that states the count.

**Mockup A — KPI entries in `all_kpis.json`** (the shape is the existing contract of
`kpi_address_spec.md`; the source is the building's; names and units per R7):

```json
"Energy Performance Certificate": {
  "EPC heating demand, reference climate (Building)": {
    "name": "EPC heating demand, reference climate", "unit": "kWh/m2", "value": 304.1,
    "description": "HWB_RK per year, OENORM B 8110-6-1:2024-03 Eq. (52), (64) with H 5050-1:2024-03 Eq. (9)",
    "tag": "Energy Performance Certificate",
    "source": {"import": null, "instance": null, "path": [], "member": "Building",
               "assembly": null, "name": "Building", "display_name": "Building", "label": null}
  },
  "EPC heating demand, site climate (Building)": {
    "name": "...", "unit": "kWh/m2", "value": 287.6,
    "description": "HWB_SK per year, OENORM B 8110-6-1:2024-03 Eq. (53), (64); stage 1a: without recoverable hot-water losses (Q_TW,beh = 0)", "...": "..."},
  "EPC gross floor area BGF (Building)":            {"unit": "m2",  "value": 143.75, "...": "..."},
  "EPC conditioned gross volume V (Building)":      {"unit": "m3",  "value": 539.0,  "...": "..."},
  "EPC transmission conductance L_T (Building)":    {"unit": "W/K", "value": 476.3,  "...": "..."},
  "EPC ventilation conductance L_V (Building)":     {"unit": "W/K", "value": 28.5,   "...": "..."},
  "EPC mean U-value U_m (Building)":                {"unit": "W/(m2 K)", "value": 0.86, "...": "..."},
  "EPC heating days, site climate (Building)":      {"unit": "d",   "value": 251.3,  "...": "..."},
  "EPC dwelling units (Building)":                  {"unit": "-",   "value": 1,      "...": "..."},
  "EPC climate region (Building)":                  {"unit": "-",   "value": "S/SO", "...": "..."},
  "EPC building data origin (Building)":            {"unit": "-",   "value": "TABULA AT.N.SFH.04.Gen.ReEx.001.001; configured: facade_u_value_in_watt_per_m2_per_kelvin, window_u_value_in_watt_per_m2_per_kelvin; dwelling units 1 (TABULA row n_Apartment); door U-value: TABULA row, U_Actual_Door_1; door area: TABULA reference door area A_Door_1", "...": "..."},
  "EPC norm edition (Building)":                    {"unit": "-",   "value": "OENORM B 8110-6-1:2024-03 / B 8110-5:2024-03 / H 5050-1:2024-03 / H 5056-1:2024-03", "...": "..."}
}
```

**Mockup B — site input on the weather, in the energy-system YAML** (D12; the two fields are
optional and default to unset, so no existing file changes; `config:` beside a `constructor:` or a
`preset:` is the file format's way of assigning fields):

```yaml
components:
  Weather:
    class: hisim.components.weather.Weather
    constructor:
      for_data_file:
        path: "${inputs}/weather/TRY_AT/TRY__R4__Z1__LL11__A1__S1"
        data_source: DWD_TRY
        heating_reference_temperature_in_celsius: -13.0
    config:
      climate_region: "S/SO"      # one of W, NF, N, ZA, SB, S/SO, N/SO (OENORM B 8110-5:2024 Bild 1)
      elevation_in_m: 356         # elevation of the site above sea level
```

## 7. Why it matters / cost of inaction

The ai4c validation compares HiSim with real Austrian certificates and with a stock dataset of about
28,000 buildings carrying certificate HWB values. Without a norm HWB from HiSim's own building
description, every comparison mixes the method difference (dynamic simulation vs. monthly norm balance)
with the model difference (archetype vs. real building), and nothing can be attributed. The TABULA
`q_h_nd` HiSim reports today is computed with a national climate and 20 °C and is not the certificate
figure, so it invites false conclusions.

## 8. Requirements

Functional unless marked. Clause references are to the 2024-03 editions (D8).

**R1 — A postprocessing option `COMPUTE_EPC` computes certificate values.** `[decided 2026-10-07, D6]`
R1.1 The option is opt-in: when it is not set, no output of any run changes. `[proposed; repo convention]`
R1.2 Its results are KPI entries of the building they describe, carrying the building's source and
keyed like the building's own KPIs, so they reach every KPI consumer (`all_kpis.json`, report table,
finder) without a new writer. `[decided 2026-10-07, D19]`
R1.3 It depends on `COMPUTE_KPIS` the way `WRITE_KPIS_TO_JSON` does. `[proposed]`
R1.4 Every input the certificate depends on (country, option dependency, site, norm tables, the
building quantities) is checked before the first simulated time step, and a run whose inputs cannot
serve the certificate is refused there with the message the respective requirement prescribes; the
same check runs again at the start of postprocessing, for results that reach postprocessing without a
simulator. `[decided 2026-10-07, D21; from cost_spec.md §9.2–9.3, simulator.py:513]`

**R2 — One option; the method is selected by country and fails loudly otherwise.** `[decided 2026-10-07, D6]`
The run's `country` selects the method; `"AT"` selects the ÖNORM method. For a country without a
method the run is refused before the first time step with a message stating that the EPC calculation
is not possible because no EPC method is implemented for that country, naming the country and the
countries that have one (English, like every HiSim message; e.g. "EPC calculation is not possible: no
EPC method is implemented for country 'DE' yet (implemented: AT)."). A run with the option but
without a building is refused likewise.

**R3 — Scope of the Austrian method, stage 1.** `[decided 2026-10-07, D1, D11]`
Residential buildings only (R3.1); the heating demand HWB under reference climate and under site
climate (R3.2); for the building of the run (R3.3). Cooling demand and all other certificate values are
out of scope (R3.4). Stage 1a computes the site-climate HWB with Q_TW,beh = 0 and says so in the KPI
description; stage 1b adds the recoverable hot-water losses per R4.12 and removes that label (R3.5).

**R4 — The calculation follows the norms exactly.** `[decided 2026-10-07, D4]`
Every quantity is computed by its clause, with the norm's defaults where the norm prescribes or
permits them, and with no HiSim or TABULA substitute for a quantity the norm defines:

| | Quantity | Rule |
|---|---|---|
| R4.1 | Reference climate | B 8110-5 Annex A (Tab. A.1 temperatures and horizontal radiation, Tab. A.2 radiation on vertical surfaces per orientation, Tab. A.3–A.7 tilted surfaces; θ_ne = −13 °C). Orientations are used as the tables print them. |
| R4.2 | Site climate | B 8110-5 Eq. (1) θ_e,j = a + b·h/100 with Annex B coefficients of the region and elevation band; Eq. (2) I_S from Annex D; Eq. (3) I_S,ON = I_S · F_T with Annex E (band by elevation, azimuth, tilt). Values for an azimuth or tilt between the tabulated ones by linear interpolation (B 8110-6-1 9.1); tabulated values exactly. |
| R4.3 | Usage profile | B 8110-5 Section 8 by the number of dwelling units (≤ 2, 3–9, ≥ 10): θ_i,h, n_L,hyg, q_i,h (Tab. 7, 9). |
| R4.4 | Transmission conductance | B 8110-6-1 Eq. (10a) L_T,h = Σ f_i,h·A_i·U_i + L_ψ + L_χ with the temperature-correction factors of Tables 3–5; thermal bridges always by the default of 5.3.2 c), Eq. (11a) (no catalogue available), including its lower bound 0.1·(L_e + L_u + L_g). An element that borders a conditioned neighbouring building is not part of the thermal envelope (Section 4, p. 8; §4.3) and enters neither L_T nor the envelope area A. |
| R4.5 | Ventilation conductance | B 8110-6-1 Eq. (13) V_V = 0.8·BGF·2.6, Eq. (14) L_V = 0.34·v_V, Eq. (15a/b) v_V = n_L,hyg·V_V (natural ventilation). |
| R4.6 | Losses | Section 7: Q_T, Q_V from L_T, L_V, θ_i,h − θ_e,j and the month's hours; Q_l = Q_T + Q_V. |
| R4.7 | Internal gains | Eq. (37) Q_i = q_i,h · BGF · 0.8 · t / 1000. |
| R4.8 | Solar gains | Eq. (43), (44): A_trans = A_g · F_s · g · F_g with A_g = 0.7·A_W (Eq. (40)), F_s,h = 0.65 / 0.50 / 0.40 for ≤ 2 / 3–10 / > 10 dwelling units (8.3.1.2.2), F_g = 0.9 × 0.98; g is the building's glazing value (R5). |
| R4.9 | Thermal mass, time constant, utilisation | Eq. (50) C = f_BW · V with f_BW = 10 / 20 / 30; Eq. (49) τ; Eq. (57a/b)–(59) η_h with a = 1 + τ/16. |
| R4.10 | Heating period, reference climate | H 5050-1 Eq. (9a)–(9m): γ per month, boundary means with the neighbouring months, γ_lim = (a+1)/a, f_h cases, Q_h,j = (Q_l − η·Q_g)·f_h. The norm states no rule for γ ≤ 0 under the reference climate; such a month is refused, not patched (the reference climate's warmest month is 21.12 °C, so it cannot occur with the norm's own data). |
| R4.11 | Heating period, site climate | B 8110-6-1 Eq. (53) with d_Heiz = f_H · MT_j from H 5056-1 Eq. (49)–(52), including its rule that a negative γ_H is replaced by the next month's positive value; γ_H with Q_TW,beh per H 5050-1 6.5.1 (stage 1a: Q_TW,beh = 0). |
| R4.12 | Recoverable hot-water losses (stage 1b) | H 5056-1 Chapter 6 and Eq. (15): emission, distribution (defaults of 6.3.2) and storage losses of the hot-water system and their recoverable share, from the daily hot-water demand wwwb of B 8110-5 Tab. 9 and the building's hot-water system as HiSim knows it, with the norm's defaults where HiSim states nothing; entering γ_H and the balance as H 5050-1 6.4.1/6.5.1 and H 5056-1 prescribe; validated against B 8110-6-2 case 4 with the "Ausstattung 1" of H 5056-2. `[decided 2026-10-07, D11]` |
| R4.13 | Annual values | Eq. (51) Q_h,a = Σ Q_h,j; Eq. (64) HWB_BGF = Q_h,a / BGF; Eq. (63a) U_m = L_T / A; Eq. (1) ℓ_c = V / A; heating days Σ f_H·MT_j; HGT 22/14 per B 8110-5 Eq. (4) with the month's days, under site climate (§4.3). |

**R5 — The building is the simulated one, read from its own configuration and HiSim's derivation.** `[decided 2026-10-07, D14, D15, D18]`
R5.1 The method's calculation core consumes one neutral set of building quantities (BGF, V,
dwelling units, construction class, opaque elements with area, U-value and boundary category,
windows with azimuth, tilt, area, U-value and g-value) and never reads HiSim or TABULA objects
itself. In a run that set is derived from the simulated `Building`; in the local validation it is
written out directly (UC3). `[proposed]`
R5.2 The derivation uses the values HiSim's building itself uses, so every configured override of
areas, U-values, floor area and apartments is honoured (UC2). `[given]`
R5.3 BGF is the conditioned gross floor area by external dimensions (TABULA `A_C_ExtDim`, scaled like
the other areas); V is the conditioned volume by external dimensions (TABULA `V_C`, scaled);
BF = 0.8·BGF per B 8110-6-1 Section 4. HiSim's conditioned floor area (scaled `A_C_Ref`) is **not**
used as BGF. `[proposed; from §4.2]`
R5.4 Each envelope sub-element is assigned one boundary category by a documented, data-driven rule
over its TABULA `b_Transmission` value and boundary codes (`Code_RoofType`, `Code_AtticCond`,
`Code_CellarCond`): b = 1 is outside air, b = 0 is a conditioned neighbour (adiabatic), b = 0.5 is the
buffer or ground category the codes name; every (b, code) combination of the 102 national rows has a
rule, and any other combination refuses the run. An element HiSim merged from several sub-areas stays
one element and takes the category of its sub-area with the highest factor, mirroring HiSim's own
rule of taking the largest `b_Transmission` for the whole merged area; sub-areas of area 0 are ignored.
`[proposed; from §4.2]`
R5.5 The thermal-mass class maps very light + light → leicht, medium → mittel, heavy + very heavy →
schwer. `[decided 2026-10-07, D5]`
R5.6 The dwelling-unit count is the building's `number_of_apartments` exactly as HiSim computed it
(configured value, TABULA row, or HiSim's floor-area estimate, truncated), without rounding or
substitution of the method's own; a count of 0 refuses the run naming the configuration field that
states the count (UC7). `[decided 2026-10-07, D18, D20]`
R5.7 Doors are opaque elements against outside air (f = 1.00, no solar gain), with the area and
U-value HiSim uses, substitutes included; horizontal windows are glazing at tilt 0°. `[proposed]`
R5.8 A quantity the simulated building cannot provide (e.g. `V_C` missing in the row) refuses the run
by name; nothing is guessed. `[given; repo rule "fail loudly"]`
R5.9 The derivation uses exactly what HiSim's `Building` uses and nothing else: the configured
overrides where they exist, HiSim's derived values where HiSim derives them, the TABULA row for
everything else (BGF, V, boundary codes, window split, g-value). No configuration field is added for
the certificate; fields for a TABULA-free building come with HiSim's own TABULA-free `Building` (P9).
`[decided 2026-10-07, D15]`
R5.10 The KPIs state where the building data came from: the TABULA code, the names of the
overridden configuration fields, the dwelling-unit count with its origin, and the origin of the
door's area and U-value, each as HiSim records it (Mockup A, "building data origin"). Where HiSim
uses a value without recording its origin, HiSim is extended to record it; the certificate never
re-derives an origin. `[proposed]`

**R6 — The site is an explicit input.** `[decided 2026-10-07, D3, D12]`
The site is the climate region, one of the seven of B 8110-5 Bild 1 (W, NF, N, ZA, SB, S/SO, N/SO),
and the elevation above sea level in m. There is one site per run. Both live as optional fields
`climate_region` and `elevation_in_m` on `WeatherConfig`, the owner of the run's place properties
(Mockup B); they default to unset. A missing or unknown value is refused with a message naming the
field and the admissible values (UC5).

**R7 — Output.** `[proposed; units decided 2026-10-07]`
R7.1 One KPI tag of its own; entries for: HWB under reference and under site climate [kWh/m2, per
year]; annual transmission losses, ventilation losses, internal gains, solar gains under each climate
[kWh]; L_T, L_V [W/K]; U_m [W/(m2 K)]; ℓ_c [m]; τ [h]; BGF [m2]; V [m3]; envelope area A [m2];
heating days [d] and HGT 22/14 [Kd] under site climate; dwelling units [-]; in stage 1b the annual
recoverable hot-water losses [kWh]; climate region, elevation, dwelling-unit class, thermal mass
class, building data origin and the norm edition as string-valued entries.
R7.2 Every value is annual; the unit spelling follows the existing KPIs (`kWh/m2` as the TABULA
reference entry, `kWh`, `W/K`, `m2`) and each description says "per year".
R7.3 Names are English, unique per building, and stable (they become golden-reference keys).

**R8 — Traceability and licensing.** `[given: scientific standard; D16]`
R8.1 Every constant in code and every table in data names norm, edition, clause/table and, where
possible, page. R8.2 Norm tables are data files with a source header, not literals in code.
R8.3 Neither the norm PDFs nor any norm table nor any norm value is committed: coefficient tables,
f-factor rows, usage-profile rows and the validation examples' inputs and expected values are
licensed content of Austrian Standards and stay local under `hisim/inputs/epc/at/`, git-ignored;
no committed test contains a value taken from a norm table. The repository carries the formulas, the
single constants written inside them with their citations, and the data contract (file names,
columns, units, row counts, source citations) so that anyone with the norms can produce the tables.
`[decided 2026-10-07, D16, D17]` R8.4 No code of the new module imports, links or otherwise depends
on the prototype `oenorm_hwb_calculator/`. `[decided 2026-10-07, D7]` R8.5 A run that needs the
tables and does not find them is refused with a message naming the folder, the norms and the data
contract (UC6). `[proposed; from R8.3]`

**R9 — Two kinds of tests: functionality on CI, validation locally.** `[decided 2026-10-07, D9, D13, D17]`
R9.1 Functionality tests are committed and run on the public CI without any norm data: every
equation and rule of the method with hand-given values, the derivation from a `Building`, the
boundary rule over all 102 national rows, the wiring, the refusals, the registry. R9.2 Validation
runs only where the norm tables exist: stage 1a reproduces ÖNORM B 8110-6-2:2024 Section 4 (SFH,
variants 1 and 2) and Section 5 (MFH, variants 1 and 2.1) for the balance cases 1 and 2 (reference
climate), every monthly value and the annual value, to the precision the tables print:
|Δ| ≤ 0.005 kWh/M monthly and ≤ 0.005 kWh/(m² a) annually. R9.3 Stage 1b reproduces case 4 of the
same examples to the same precision. R9.4 A larger residual is a defect: it is explained by an
identified step of the norm's procedure (a rounding the norm prescribes, a misread table) and fixed
or documented; the tolerance is widened only by the owner's decision, recorded here. R9.5 Variants
with heat recovery (MFH 2.2, 2.3) are out of scope. R9.6 Where an example under-specifies an input
(door, thermal bridges, frame fraction), the norm default applies and the validation states the
assumption. R9.7 The examples' inputs and expected values are norm content and stay local with the
tables (R8.3); the validation output is attached to the pull request that lands the balance.

**R10 — Modularity.** `[given]`
A second country's method is added by adding a method and its data, without changing the Austrian
method, the option, the building quantities' shape, or the output contract.

**R11 — The implemented edition is pinned and stated.** `[decided 2026-10-07, D8]`
All clauses, tables and examples come from the 2024-03 edition package (B 8110-5, B 8110-6-1,
B 8110-6-2, H 5050-1, H 5056-1, H 5056-2), and the package is stated in the output (R7.1) and in the
data headers. A later edition is a second method (P3), not a switch inside this one.

**R12 — Compatibility.** `[proposed]`
Existing KPI names, golden references and every output of runs without the option are unchanged;
the optional configuration fields of R6 default to unset so every existing energy-system file keeps
its meaning; any addition to HiSim's `Building` for R5.10 records an origin and changes no value;
the public test suite stays green without the local tables (C6).

## 9. Constraints, invariants and assumptions

**Known constraints**

- C1 `PostProcessingOptions` numbers are wire format; a new option takes 31 and a named test in
  `tests/test_postprocessing_options.py` (guard test). The option framework behind that test runs
  fixed German setups, so the named test can only exercise the refusal of R2.
- C2 Component KPI names must be unique per component; the key is `"<name> (<source.name>)"`.
- C3 Run code writes only inside the result directory (write guard); stage 1 writes no file of its own.
- C4 HiSim's building is a TABULA archetype with per-element overrides: five element classes, five
  window directions, one g-value, one U-value per element, one `b_Transmission` per sub-area with
  the values 0, 0.5 and 1; TABULA-AT rows carry no `Code_ConstructionBorder`. A TABULA-free HiSim
  building does not exist today (D15, P9).
- C5 Natural ventilation only is modelled in HiSim's building, so B 8110-6-1 6.2.3 (mechanical) is not needed.
- C6 The norm texts and their tables are licensed: neither the PDFs nor any transcribed table or
  value may be published in the open-source repository (D16, D17). Consequence: the public CI has no
  norm data, so the validation runs only where the tables exist and skips with a reason elsewhere;
  the arithmetic is tested on CI with hand-given values.
- C7 Code and comments in English; `pytest -m base` must stay green; targeted tests only.

**Invariants**

- I1 Q_l,j = Q_T,j + Q_V,j for every month.
- I2 0 ≤ f_h,j ≤ 1; 0 < η_h,j ≤ 1; Q_h,j ≥ 0; HWB ≥ 0.
- I3 Reference- and site-climate results share every building quantity; only the climate differs.
- I4 BF = 0.8 · BGF; A_g = 0.7 · A_W; L_ψ + L_χ ≥ 0.1 · (L_e + L_u + L_g).
- I5 The sum of the per-direction window areas equals the window area used in L_T.
- I6 The building quantities derived from a `Building` without overrides equal the TABULA row's values
  (areas, U-values, g, scaled `A_C_ExtDim` and `V_C`) and HiSim's derived values (apartments, door)
  exactly.
- I7 An interpolated radiation value equals the table value at every tabulated azimuth and tilt.
- I8 An element against a conditioned neighbour contributes neither to L_T nor to A.

**Assumptions (to confirm)**

- A1 TABULA `V_C` scales linearly with the floor-area scaling factor (constant height).
- A2 Boundary rule: b = 1 → outside air for every element; b = 0 → conditioned neighbour (adiabatic);
  b = 0.5 → for walls a wall to other buffer space (0.70); for roofs a ceiling to an unconditioned
  attic (0.90); for floors, by `Code_CellarCond`: N or P → ceiling to an unconditioned, uninsulated
  cellar (0.70), "-" or 0 → floor on ground ≤ 1.5 m (0.70), C → cellar floor on ground > 1.5 m
  (0.50). A roof with b = 1 under an unconditioned attic (`Code_AtticCond = N`, `Code_RoofType ≠ FR`)
  is the ceiling to that attic (0.90), because the norm's factor, not TABULA's, applies (R4.4).
- A3 Certificate software applies the thermal-bridge default of Eq. (11a). The prototype matched a
  real certificate within 0.6 % using TABULA's ΔU instead; whether Eq. (11a) also does is a finding of
  the later manual tests (P7), not a requirement.
- A4 The validation examples of B 8110-6-2 are fully specified once the norm defaults are applied
  (frame fraction, F_s, F_g, thermal bridges, no door); the examples' geometry (L × B × H) yields BGF
  and V by external dimensions (SFH: 192 m², 576 m³; the annual value 61.32 kWh/(m² a) times 192 m²
  reproduces the monthly sum of Table 4, which confirms BGF = 192 m²).
- A5 HiSim's hot-water components (storage volume, generator type) can be mapped to the inputs of
  H 5056-1 Chapter 6, with the norm's defaults for what they do not state (stage 1b).

## 10. Acceptance criteria

| ID | Criterion | Verifies |
|---|---|---|
| AC1 | A run with an Austrian archetype, country `AT`, a site and the option writes every entry of R7.1 into `all_kpis.json` under the new tag, keyed to the building per `kpi_address_spec.md` (runs where the norm tables exist). | R1, R2, R6, R7 |
| AC2 | The SFH and MFH examples of B 8110-6-2:2024, cases 1 and 2, are reproduced monthly and annually within 0.005 of the printed unit (stage 1a); case 4 likewise (stage 1b). Runs where the norm tables exist, skips with a reason elsewhere; the local output is attached to the PR. | R4, R9, I1–I4 |
| AC3 | Country `DE` with the option set is refused before the first time step with the message of R2 naming `DE` and `AT`; the same message results when postprocessing is started directly on such a run. | R1.4, R2 |
| AC4 | Missing climate region or elevation is refused before the first time step with a message naming the field and the admissible values. | R1.4, R6 |
| AC5 | A run without the option produces byte-identical `all_kpis.json`; the committed golden references pass unchanged; existing energy-system files load unchanged; `pytest -m base` is green without the local tables. | R1.1, R12, C6 |
| AC6 | Every data table has a source header (reviewed locally). A loader given a missing or incomplete table refuses with the message of R8.5 or names the incomplete dimension (runs everywhere, on synthetic files); the real tables load complete (runs where they exist). | R8.1, R8.2, R8.5, R11 |
| AC7 | A static check finds no import of or reference to `oenorm_hwb_calculator` in `hisim/` and `tests/`, and no committed test file contains a norm table value. | R8.3, R8.4 |
| AC8 | Adding a stub method for a second country touches no file of the Austrian method (demonstrated by a test that registers a dummy country). | R10 |
| AC9 | An Austrian archetype with overridden facade and window U-values yields an L_T that reflects the overrides, and the origin KPI names them, the dwelling-unit count with its origin and the door's origins. | R5.2, R5.6, R5.10 |
| AC10 | For an archetype without overrides, the derived building quantities equal the TABULA row's values and HiSim's derived values exactly; every one of the 102 national rows derives without refusal, and the rows of §4.2 with b = 0 yield the conditioned-neighbour boundary for those sub-areas. | I6, R5.3, R5.4 |
| AC11 | The option has its named test and the guard test passes; `flake8`, `mypy`, `pylint` are clean on the new files. | C1, C7 |
| AC12 | An archetype whose apartment count HiSim estimates as 0 is refused before the first time step with a message naming `number_of_apartments`. | R1.4, R5.6 |

## 11. Open questions, parking lot and decisions

### 11.1 Open questions

None.

### 11.2 Deferred items (parking lot, to become beads under an EPC epic)

| # | Item | Origin |
|---|---|---|
| P1 | Further certificate values: WWWB as reported value, HEB, HTEB (H 5056-1), EEB, PEB, CO₂, fGEE (H 5050-1), KB (B 8110-6-1) | D1 |
| P2 | Lookup of climate region and elevation from municipality / cadastral code (the prototype had an offline resolver) | D3 |
| P3 | 2025-10 edition package as a second method, once B 8110-6-2:2025 and H 5050-1:2025 exist | D8 |
| P4 | Monthly-balance export file per building | D6 |
| P5 | Comparison KPI norm HWB vs. simulated specific heating demand | D2 |
| P6 | Mechanical ventilation with heat recovery (B 8110-6-1 6.2.3), MFH variants 2.2/2.3 of B 8110-6-2 | R9.5 |
| P7 | Manual tests against real certificates (Muggauberg, Ludersdorf, Kalsdorf) after merge | D9 |
| P8 | A second country (Germany, DIN V 18599 / GEG) | G3 |
| P9 | Certificate inputs for a TABULA-free HiSim building (BGF, V, boundary categories, window split, g) together with HiSim's own TABULA-free `Building` | D15 |
| P10 | Release of norm-derived data, if Austrian Standards permits any of it | D16 |
| P11 | A results file in which HiSim describes every parameter it used for the building, so the certificate (and any other derived figure) can be computed from that file alone; the neutral building quantities of R5.1 are its intended content | G5 |

### 11.3 Decision register (requester, 2026-10-07)

| ID | Decision |
|---|---|
| D1 | Stage 1 is the heating demand HWB only, under reference and site climate, for residential buildings. |
| D2 | A norm calculation from the building's static data; no comparison KPI against the simulated heating demand. |
| D3 | Site (climate region, elevation) is an explicit input; one site per run. |
| D4 | Exact norm reproduction: ÖNORM temperature-correction factors, thermal bridges always by the norm's default formula, usage profile strictly from the norm; the building otherwise as precisely as HiSim knows it. |
| D5 | Thermal-mass classes map 5 → 3 as in R5.5. |
| D6 | One option `COMPUTE_EPC`, package `epc_computation`, country from `SimulationParameters.country`, refusal message for a country without method (R2); no monthly export file. |
| D7 | The prototype is inspiration only, never imported or linked, deleted after merge. |
| D8 | Edition package 2024-03; the 2025-10 package is a later, second method (P3). |
| D9 | ÖNORM B 8110-6-2 is the validation oracle; real certificates are manual tests after merge. |
| D10 | Specification before code: requirements, then design, one PR for both, then beads, then implementation PRs. |
| D11 | Site-climate HWB as the certificate computes it: stage 1a with Q_TW,beh = 0 and labelled, stage 1b with the hot-water loss chain of H 5056-1; H 5056-2 is procured as its oracle. |
| D12 | The site lives as two optional fields `climate_region` and `elevation_in_m` on `WeatherConfig` (Mockup B). Rejected: a block in the simulation parameters; fields on the building. |
| D13 | Validation to the printed precision; residuals are defects to be explained, not tolerated (R9). |
| D14 | One building only, the simulated one; no building description of the method's own. |
| D15 | The certificate uses exactly what HiSim's `Building` uses: configured overrides where they exist, HiSim's derived values, the TABULA row for the rest; no certificate-only configuration fields (R5.9, P9). |
| D16 | No norm table of any kind is committed: coefficient tables, f-factor rows, usage-profile rows and the validation examples' values stay local under `hisim/inputs/epc/at/`. Formulas and the single constants written inside them are published. Any later release is a separate decision (P10). |
| D17 | Tests are of two kinds: functionality tests, committed and run on CI without norm data, and validation against the norm's examples, local only. No committed test contains a norm value (R9, R8.3). |
| D18 | The dwelling-unit count is HiSim's `number_of_apartments` verbatim, including HiSim's truncation and its floor-area estimate; the certificate neither rounds nor substitutes it (R5.6). |
| D19 | The certificate values are KPIs of the `Building`: they carry the building's `KpiSource` and are keyed like the building's own KPIs (R1.2, Mockup A). |
| D20 | A dwelling-unit count of 0 refuses the run before the first time step, naming `number_of_apartments`; the count is never mapped into a usage class or raised to 1 (R5.6, UC7). |
| D21 | Every input of the certificate is checked before the first simulated time step, and again at the start of postprocessing, after the pattern of the lifecycle cost engine (R1.4, G8). |

## 12. Glossary

- **EPC / Energieausweis** — the Austrian energy performance certificate; its headline value for stage 1 is the HWB.
- **HWB** — Heizwärmebedarf, the annual heating demand per BGF in kWh/(m² a), under reference climate (HWB_RK) or site climate (HWB_SK).
- **RK / SK** — Referenzklima / Standortklima: the reference climate of B 8110-5 Annex A / the site climate from climate region and elevation (Eq. 1–3).
- **BGF / BF / V** — conditioned gross floor area by external dimensions / reference area 0.8·BGF / conditioned gross volume by external dimensions.
- **f-factor (f_i,h)** — temperature-correction factor of B 8110-6-1 Tables 3–5 for what an element borders on; replaces TABULA's `b_Transmission` in the certificate.
- **Q_TW,beh** — recoverable losses of the hot-water system (H 5056-1), entering the site-climate balance in stage 1b.
- **Site** — climate region (one of seven) and elevation of the place the building stands in, stated on the weather.
- **Functionality test / validation** — a committed test that runs on CI without norm data / the local reproduction of the norm's worked examples (D17).
