# Implementation specification: EPC computation as a postprocessing option

**Status:** draft (in refinement with the requester)
**Date:** 2026-10-07
**Implements:** `roadmap/epc_computation/requirements.md` (draft of 2026-10-07; no open question; written in the same PR by decision D10)
**Author(s):** Nicolai Gölz, drafted with Claude · **Reviewers:** Nicolai Gölz; HiSim maintainers on the PR
**Branch / PRs:** `897-epc-calculator`; PR list in §10 as they open
**What a reviewer must decide here:**

1. The EPC is its own postprocessing step, built like the cost steps: `COMPUTE_EPC` runs in
   `PostProcessor.run` right before `COMPUTE_KPIS`, computes per building, writes a report chapter,
   leaves its results on the data-transfer object, and the KPI preparation files them as KPIs of
   the building (§3.2, §4.1).
2. Every input the certificate needs is checked before the first time step by a `Simulator` check
   beside `check_cost_declarations`, and again at the start of postprocessing; the computation
   itself needs no time series (§3.2, §3.4, §4.11).
3. One neutral set of building quantities with a country-neutral boundary vocabulary separates the
   HiSim side (TABULA codes and overrides → quantities) from the norm side (quantities → f-factors
   and balance); the vocabulary includes "conditioned neighbour" for TABULA's adiabatic sub-areas
   (§3.3, §4.2, §4.13, §7.2).
4. The dwelling-unit count and the door are taken from HiSim verbatim, substitutes included, and
   HiSim is extended to record the origin of both (§3.3, §5.3, §4.14).
5. Radiation lookups interpolate linearly between the tabulated azimuths and tilts, as
   B 8110-6-1:2024 9.1 permits; tabulated values are returned exactly (§3.4, §4.4).
6. The site is two optional fields on `WeatherConfig`; the committed energy-system schema file is
   regenerated with them (§5.2, §10).
7. Norm tables are input data under `hisim/inputs/epc/at/`, local and git-ignored; the repository
   carries the data contract and functionality tests that need no norm data; the norm reproduction
   is a local validation (§7.1, §9).
8. Ten pull requests: one for the documents, eight for stage 1a, one for stage 1b, each one module
   group with its tests (§10).

Status tags as in the requirements: `[proposed]`, `[decided YYYY-MM-DD]`; decisions are cited by
their register ID (requirements §11.3, D1–D21). Requirements the design derives are tagged `D-R1 …`
to keep them apart from that register.

---

## 1. Summary

This document designs the postprocessing option `COMPUTE_EPC` of the requirements (R1–R12). The
central idea: a postprocessing step of the same shape as the OPEX and CAPEX steps reads the one
simulated `Building` and the run's weather, turns them into a neutral set of building quantities and
a site, hands both to the EPC method of the run's country, and leaves the method's values on the
data-transfer object, from which the KPI preparation files them as KPI entries of the building,
with the building's source. Because the calculation is a pure function of configurations, the same
input preparation runs in the `Simulator` before the first time step and refuses a run whose
certificate could never be computed, the way the lifecycle cost engine refuses an unpriceable fleet.
The Austrian method is a direct transcription of ÖNORM B 8110-6-1:2024-03, B 8110-5:2024-03,
H 5050-1:2024-03 and H 5056-1:2024-03, one function per norm term, every constant cited, every table
a local input file. New are the option, one KPI tag, two optional weather fields, one attribute on
the data-transfer object, one check method on the `Simulator`, two origin attributes on
`BuildingInformation`, the package `hisim/postprocessing/epc_computation/` and the data contract
`hisim/inputs/epc/README.md`; changed are the postprocessor (one step, one early check, one
dependency check), the KPI preparation (one reader), `WeatherConfig`, the committed energy-system
schema, the option test framework's dependency map, `.gitignore` and `pytest.ini`; nothing is
deleted. The non-obvious places are the neutral boundary vocabulary that decides which
temperature-correction factor a TABULA sub-area receives, including TABULA's adiabatic sub-areas
(§3.3, §7.2, requirements A2 and §4.3), and the fact that the public CI cannot run the norm reproduction
because the tables are licensed (§9, risk R-10). Stage 1b (recoverable hot-water losses) is designed
as an interface only (§3.5) until ÖNORM H 5056-2 is at hand.

## 2. Requirements coverage matrix

| Req / AC | Design element (§ ref) | Code location (planned) | Test (§9) | Status |
|---|---|---|---|---|
| R1, R1.1 | §5.1 option member 31; §3.2 step runs only when set | `hisim/postprocessingoptions.py` | T-5, T-8 | planned |
| R1.2 | §3.2 results filed with the building's source before sorting; §5.4 | `kpi_computation/kpi_preparation.py:read_epc_results` | T-6, V-4 | planned |
| R1.3 | §8 E-02 dependency check; framework map | `postprocessing_main.py`, `simulator.py`, `tests/postprocessing_option_test_framework.py` | T-5, T-6 | planned |
| R1.4 | §3.2 `Simulator.check_epc_inputs`, `prepare_epc_inputs`; §8 | `hisim/simulator.py`, `epc_computation/__init__.py` | T-6 | planned |
| R2 | §3.2 registry; §8 E-01, E-09 | `epc_computation/method.py`, `simulator.py`, `postprocessing_main.py` | T-5, T-6 | planned |
| R3.1–R3.4 | §3.1 scope of `AustrianEpcMethod` | `epc_computation/at/method.py` | V-1, V-2 | planned |
| R3.5 | §3.5 stage 1b interface, label in description | `epc_computation/at/balance.py`, `kpis.py` | V-1 (case 4, stage 1b) | planned |
| R4.1, R4.2 | §3.3 climate; §7.1 tables A, B, D, E; §3.4 interpolation | `at/climate.py`, `at/data_tables.py` | T-1, V-1, V-5 | planned |
| R4.3 | §3.3 usage profile by dwelling units | `at/usage.py` | T-1 | planned |
| R4.4 | §3.3 L_T with f-factors and Eq. (11a); conditioned neighbour outside the envelope | `at/envelope.py` | T-1, V-1 | planned |
| R4.5, R4.6 | §3.3 L_V, losses | `at/envelope.py`, `at/balance.py` | T-1 | planned |
| R4.7, R4.8 | §3.3 gains | `at/gains.py` | T-1, V-1 | planned |
| R4.9–R4.11 | §3.3 utilisation, heating period, RK/SK balance; §13 Q-D1 | `at/balance.py` | T-1, V-1, V-2 | planned |
| R4.12 | §3.5 interface, equations after H 5056-2 | `at/balance.py`, `at/hot_water.py` | V-1 case 4 | planned (1b) |
| R4.13 | §3.3 annual values; §13 Q-D2 | `at/balance.py`, `at/envelope.py` | V-1, V-3 | planned |
| R5.1 | §3.3 `BuildingQuantities`, §5.3 | `epc_computation/quantities.py` | T-2, V-1 | planned |
| R5.2, R5.3 | §3.3 derivation from the `Building` | `epc_computation/hisim_building.py` | T-3 | planned |
| R5.4 | §3.3 boundary rule; §7.2; §13 Q-D3, Q-D6 | `epc_computation/hisim_building.py` | T-3, T-4 | planned |
| R5.5, R5.7, R5.8 | §3.3 derivation rules | `epc_computation/hisim_building.py` | T-3 | planned |
| R5.6 | §3.3 dwelling units verbatim; §8 E-14; §13 Q-D8 | `epc_computation/hisim_building.py` | T-3, T-6 | planned |
| R5.9 | §3.3 nothing but the `Building`'s values | `epc_computation/hisim_building.py` | T-3 | planned |
| R5.10 | §3.3 origin text from HiSim's recorded origins; §5.3 origin attributes | `components/building/information.py`, `hisim_building.py`, `kpis.py` | T-3 | planned |
| R6 | §5.2 weather fields; §3.2 site; §8 E-03–E-05 | `components/weather/config.py`, `epc_computation/site.py` | T-6, T-9 | planned |
| R7.1–R7.3 | §5.4 KPI names and units | `epc_computation/kpis.py` | V-4 | planned |
| R8.1, R8.2 | §7.1 data files with source headers; citations in docstrings | local `hisim/inputs/epc/at/*.csv` | review, V-5 | planned |
| R8.3 | §7.1 data contract, `.gitignore`; §9 no norm value in a committed test | `hisim/inputs/epc/README.md`, `.gitignore` | review | planned |
| R8.4 | §9 T-10 static check | `tests/test_epc_computation_contracts.py` | T-10 | planned |
| R8.5 | §8 E-12 | `at/data_tables.py` | T-11 | planned |
| R9.1 | §9 functionality tests | `tests/test_epc_*.py` | T-1 … T-11 | planned |
| R9.2–R9.7 | §9 validation V-1, V-2 at 0.005, local | `tests/test_epc_at_validation_examples.py` | V-1, V-2 | planned |
| R10 | §3.2 registry, §5.3 protocol | `epc_computation/method.py` | T-7 | planned |
| R11 | §5.4 edition entry; §7.1 headers | `at/method.py`, local tables | V-4, V-5 | planned |
| R12 | §10 compatibility; fields default unset; origins change no value; data-free CI | — | T-3, T-8, T-9 | planned |
| AC1 | §3.2, §5.4 | `tests/test_epc_computation.py` | V-4 | planned |
| AC2 | §9 | `tests/test_epc_at_validation_examples.py` | V-1, V-2 | planned |
| AC3 | §8 E-01 in the simulator and in postprocessing | `tests/test_postprocessing_options.py`, `tests/test_epc_computation.py` | T-5, T-6 | planned |
| AC4 | §8 E-03, E-04 | `tests/test_epc_computation.py` | T-6 | planned |
| AC5 | §10 | existing golden tests, base suite | T-8 | planned |
| AC6 | §7.1, §9 | `tests/test_epc_at_data_tables.py` | T-11, V-5 | planned |
| AC7 | §9 | `tests/test_epc_computation_contracts.py`, review | T-10 | planned |
| AC8 | §3.2 registry | `tests/test_epc_computation.py` | T-7 | planned |
| AC9, AC10 | §3.3, §7.2 | `tests/test_epc_hisim_building.py` | T-3, T-4 | planned |
| AC11 | §9 | `tests/test_postprocessing_options.py`, CI lint | T-5 | planned |
| AC12 | §8 E-14 | `tests/test_epc_computation.py` | T-6 | planned |
| D-R1 `[proposed]` | Radiation on an arbitrary azimuth or tilt is obtained by linear interpolation between the tabulated values, exact at the grid (B 8110-6-1:2024 9.1); derived from R4.2 and G2. | `at/climate.py` | T-1 | planned |
| D-R2 `[proposed]` | The postprocessing-side input check runs before any other postprocessing step, so that a run refused there writes no plot or export first; derived from R1.4 for results that reach postprocessing without a simulator. | `postprocessing_main.py` | T-5 | planned |
| D-R3 `[proposed]` | A negative gain-loss ratio under the reference climate is refused, because H 5050-1 Eq. (9) states no rule for it; derived from R4.10 and G2. | `at/balance.py` | T-1 | planned |

## 3. Design overview

### 3.1 Concepts

| Noun | One line |
|---|---|
| **EPC step** | The `COMPUTE_EPC` block of `PostProcessor.run`: like `COMPUTE_OPEX`, it calls one function of its sub-package per run, writes a report chapter when the PDF report is on, and leaves its results on the data-transfer object. |
| **Input check** | `prepare_epc_inputs`: the method, the site and the building quantities of every `Building` of the run, or a refusal. Run by the `Simulator` before the first time step and by the postprocessor first thing. |
| **EPC method** | A country's certificate calculation: validates a site, computes values from building quantities, states its norm edition. Registered under the country code. |
| **Building quantities** | The country-neutral description the methods consume: BGF, V, dwelling units, construction class, opaque elements, windows (R5.1). Derived from the `Building`; written by hand only in the local validation. |
| **Boundary** | Country-neutral vocabulary for what an opaque element borders on (outside air, unconditioned attic, conditioned neighbour, …). The HiSim side chooses it from TABULA codes; the Austrian side maps it to a temperature-correction factor. |
| **Site** | Climate region code and elevation, read from the run's weather configuration (R6). |
| **EPC value** | One result of a method: name, unit, value, description with citation. The KPI preparation files it as a `KpiEntry` of the building, with the building's source. |
| **Climate** | Twelve monthly outdoor temperatures and an irradiation lookup by azimuth and tilt, either the reference climate (Annex A) or the site climate (Eq. 1–3). |
| **Data contract** | The committed description of the local norm tables: file names, columns, units, row counts, citations (`hisim/inputs/epc/README.md`). |

### 3.2 Control flow

The step mirrors the cost steps of `PostProcessor.run` (`compute_and_write_opex_costs_to_report`:
a lazily loaded function of the sub-package takes the wrapped components and the simulation
parameters, returns a report table, the report chapter is written when the PDF report is on). The
cost figures reach the KPI collection through the meters' component KPIs and a re-read of the two
cost CSVs (`read_opex_and_capex_costs_from_results`); the EPC figures reach it through one attribute
on the data-transfer object that the KPI preparation reads next to that cost reading. The input
check mirrors `Simulator.check_cost_declarations` (`cost_spec.md` §9.2): the same function runs
before the first time step and, independently, at the start of postprocessing.

```
Simulator.run_all_timesteps()
 ├─ check_cost_declarations()                                               (existing)
 ├─ check_epc_inputs()                                                      NEW
 │     COMPUTE_EPC set?  → COMPUTE_KPIS set?                                (E-02 otherwise)
 │                       → prepare_epc_inputs(wrapped_components, simulation_parameters)
 │                            method = method_for_country(country)          (E-01)
 │                            tables = method.load_tables()                 (E-12, E-13)
 │                            site   = method.validate_site(site_from_weather(wrapped_components))   (E-03, E-04, E-05)
 │                            for each Building component (E-09 if none):
 │                               quantities = quantities_from_building(building)                     (E-06, E-07, E-14)
 │                               inputs[label] = EpcInputs(label, building.kpi_source(), quantities, site, method)
 │                         result discarded; the run is refused on the first error
 └─ time-step loop … → PostProcessor.run(ppdt)

PostProcessor.run(ppdt)
 ├─ COMPUTE_EPC set?  → same two checks, first thing                        (D-R2; second line of defence)
 ├─ … plots, exports, lifecycle costs, OPEX, CAPEX … (existing order)
 ├─ COMPUTE_EPC → compute_and_write_epc_to_report(ppdt, report)             NEW step
 │     epc_calculation = _load_attribute("hisim.postprocessing.epc_computation", "epc_calculation")
 │     ppdt.epc_results, table = epc_calculation(ppdt.wrapped_components, ppdt.simulation_parameters)
 │        inputs = prepare_epc_inputs(...)
 │        for label, inp in inputs: result = inp.method.compute(inp.quantities, inp.site)             (E-08, E-11)
 │                                  epc_results[label] = EpcBuildingResult(inp.source, result)
 │     GENERATE_PDF_REPORT set? → write_new_chapter_with_table_to_report(report, table, ". Energy performance certificate", …)
 └─ COMPUTE_KPIS → KpiGenerator.__post_init__
      ├─ get_all_component_kpis()                              (existing)
      ├─ create_kpi_collection(building) per building          (existing)
      │     … read_opex_and_capex_costs_from_results(building) (existing)
      │     read_epc_results(building)   ── NEW: files ppdt.epc_results[building] as KpiEntry under the EPC tag,
      │                                      source = the stored KpiSource, key = KpiAddress.key_for(name, source)  (E-10 on collision)
      └─ sort_kpi_collection_according_to_kpi_tags()           (existing; the new tag sorts like any other)
```

The sorted collection, the report table of the KPI chapter, `all_kpis.json`, the finder and the
building-sizer JSON see the EPC entries without any change of their own (R1.2). District objects get
no EPC entries: a certificate is per `Building` component (R3.3). `ppdt.epc_results` is empty when
the option is not set, and `read_epc_results` then does nothing. The option test framework and any
caller that starts `PostProcessor.run` on stored results never pass the `Simulator`, which is why
the postprocessing-side check exists (R1.4).

### 3.3 Data flow of the Austrian method

```
Building (TABULA row + configured overrides + HiSim's derived values)      WeatherConfig
   │ hisim_building.quantities_from_building                                   │ site.site_from_weather
   ▼                                                                           ▼
BuildingQuantities ───────────────────────────────┐                      Site(climate_region, elevation_in_m)
  BGF = factor · A_C_ExtDim                        │                           │ at.method.validate_site → region ∈ 7, h ≥ 0
  V   = factor · V_C                               │                           ▼
  dwelling units = number_of_apartments (verbatim) │     at.climate.site_climate(region, h)   at.climate.reference_climate()
  construction = 5 → 3 classes                     │        θ_e,j = a + b·h/100 (Eq. 1, Annex B)     Tab. A.1 temperatures
  opaque: floor, wall, roof, door                  │        I_S = a2h²+a1h+a0 (Eq. 2, Annex D)       Tab. A.1 horizontal, A.2 vertical,
     area, U, Boundary (b + TABULA codes → Boundary)│        I_S,ON = I_S·F_T (Eq. 3, Annex E)         A.3–A.7 tilted
  windows: S, E, N, W (tilt 90), Horizontal (0)    │        linear interpolation in azimuth and tilt (B 8110-6-1 9.1)
     area, U, g = g_gl_n                           ▼               ▼
  origin text (from HiSim's recorded origins)
                                   at.method.AustrianEpcMethod.compute(quantities, site)
                                     usage      = at.usage.profile(dwelling_units)            B 8110-5 Tab. 7, 9; F_S,h per B 8110-6-1 8.3.1.2.2
                                     envelope   = at.envelope: f(Boundary) Tab. 3–5, L_T Eq. (10a)+(11a), L_V Eq. (13)–(15), A, U_m, ℓ_c
                                     gains      = at.gains: Q_i Eq. (37), A_trans Eq. (40),(44), Q_s Eq. (43) per climate
                                     balance    = at.balance: Q_T, Q_V, Q_l (Sec. 7), C, τ, a, η (Eq. 49, 50, 57–59),
                                                  RK: f_h and Q_h,j per H 5050-1 Eq. (9); SK: f_H per H 5056-1 7.1 (Q_TW,beh = 0 in 1a), Q_h,j Eq. (53)
                                     annual     = Q_h,a, HWB_BGF Eq. (64), heating days, HGT 22/14 (B 8110-5 Eq. 4)
                                     → EpcResult(values=[EpcValue(name, unit, value, description), …], edition)
```

Derivation rules of `quantities_from_building` (R5.2–R5.10):

- Areas and U-values per element are read from `BuildingInformation` after HiSim applied the
  configured overrides: `floor_area_in_m2`, `facade_area_in_m2`, `roof_area_in_m2`,
  `window_area_in_m2`, `door_area_in_m2` and the matching `*_u_value_in_watt_per_m2_per_kelvin`
  attributes; the per-direction window areas from `scaled_window_areas_in_m2`; `g` from
  `total_solar_energy_transmittance_for_perpedicular_radiation`. The TABULA `b_Transmission`
  factors are **not** used as factors (R4.4: ÖNORM f-factors instead); they enter the boundary
  rule as codes beside `Code_RoofType`, `Code_AtticCond` and `Code_CellarCond` (§7.2).
- A TABULA element with several sub-areas (`Roof_1`/`Roof_2`, `Floor_1`/`Floor_2`, `Wall_1..3`) is
  one element in HiSim, with the summed area, the area-weighted U-value and the **largest**
  `b_Transmission` of its sub-areas (`BuildingInformation._element_u_value_and_adjustment_factor`).
  The derivation keeps exactly that one element and gives it the boundary of the sub-area, among
  those with area > 0, whose rule yields the highest temperature-correction factor: the same "the
  most exterior sub-area decides" rule HiSim applies to b (R5.4, §13 Q-D3). A sub-area with
  `b_Transmission = 0` and area > 0 is a conditioned neighbour (adiabatic) and yields the factor 0,
  so it decides only when every sub-area of the element is one (§13 Q-D6).
- BGF = `scaling_factor_according_to_conditioned_living_area` × row `A_C_ExtDim`; V = the same
  factor × row `V_C` (R5.3, A1). A row with `A_C_ExtDim` or `V_C` empty or 0 refuses (E-06); the
  102 national rows have none.
- Construction class from `building_heat_capacity_class`: very light, light → light; medium →
  medium; heavy, very heavy → heavy (R5.5).
- Dwelling units = `BuildingInformation.number_of_apartments`, the `int` HiSim computed (configured
  value, TABULA `n_Apartment`, or HiSim's estimate from the conditioned floor area), verbatim
  (R5.6, D18). A count of 0 refuses (E-14); the message names `number_of_apartments` as the field
  that states the count.
- Doors: one opaque element with Boundary outside air, the area and U-value HiSim uses, substitutes
  included (R5.7). Windows: four vertical elements with azimuth 0° (S), −90° (E), +90° (W), 180° (N)
  and one horizontal element with tilt 0°; a direction with area 0 is dropped (every national
  Austrian row has `A_Window_Horizontal = 0`).
- The origin text (R5.10) is assembled from what HiSim records, never re-derived: the TABULA code;
  every override field of `BuildingConfig` that is not `None`; the dwelling-unit count with
  `BuildingInformation.number_of_apartments_origin`; the door's U-value origin from
  `element_values["door"].u_value_origin` and its area origin from
  `BuildingInformation.door_area_origin` (both strings HiSim writes where it decides the value,
  §5.3). Example: `TABULA AT.N.SFH.04.Gen.ReEx.001.001; configured:
  facade_u_value_in_watt_per_m2_per_kelvin; dwelling units 1 (TABULA row n_Apartment); door
  U-value: TABULA row, U_Actual_Door_1; door area: TABULA reference door area A_Door_1`.

### 3.4 The non-obvious choices

**A step like the cost steps, with the cost engine's pre-run check (decide-list 1, 2).**
`COMPUTE_OPEX` and `COMPUTE_CAPEX` are each one block in `PostProcessor.run` that loads one function
of their sub-package lazily, calls it with the wrapped components and the simulation parameters,
and writes a report chapter; the KPI step runs after them and reads what it needs. The EPC step
copies that shape, so a reader who knows the cost steps knows the EPC step. The only addition is the
attribute `epc_results` on the data-transfer object, because, unlike the meters, no component emits
the EPC figures itself; the KPI preparation reads them in `create_kpi_collection`, next to its cost
reading. The certificate depends on no time series, only on configurations and the TABULA row, so
nothing is gained by waiting for the simulation to find out that the country has no method, the
site is missing, the tables are absent or the building lacks a quantity. The current cost engine
settles the same question with `Simulator.check_cost_declarations` ("refuse now rather than after
hours of simulation", `cost_spec.md` §9.2) and repeats the check in its bridge for inputs that
never pass a simulator; `check_epc_inputs` and the postprocessing-side check are the same pair. The
computation stays in postprocessing by D6; the check runs the whole input preparation and discards
the result, which costs one TABULA lookup HiSim performs anyway.

**Neutral quantities and a neutral boundary vocabulary (decide-list 3).** The norm's
temperature-correction factor depends on what an element borders on, which TABULA states only
indirectly (`b_Transmission`, `Code_RoofType`, `Code_AtticCond`, `Code_CellarCond`). Letting the
Austrian method read TABULA codes would tie the norm to one data source; letting the HiSim side
pick f-factors would tie HiSim to one norm. The `Boundary` enum sits between them: HiSim says
"ceiling to an unconditioned cellar", the Austrian method says "0.70". A German method would map the
same vocabulary to DIN factors. The practical reason is the validation oracle: the house of
B 8110-6-2 is not a TABULA archetype, so the norm examples can only be run through a description
that exists without a `Building`. The rule from TABULA codes is one table (§7.2), the only place
where assumption A2 lives. The vocabulary is also the intended content of the results file
of requirements P11: `BuildingQuantities` is plain, serializable data.

**HiSim's values verbatim, with their origin (decide-list 4).** HiSim's apartment count is
truncated and, for a scaled archetype, an estimate from a German average floor area; HiSim's door
can be TABULA's estimated door with a substituted U-value. The certificate takes both as they are
(D15, D18): a second rule would make the certificate describe a building HiSim did not simulate.
What the certificate adds is visibility: the origin KPI names the count and the door with the
origin HiSim recorded. HiSim records the door's U-value origin already (`element_values`); the
apartment count and the door area get the same one-string record, written where HiSim decides the
value, so the certificate never reconstructs HiSim's decision from the outside.

**Interpolation (decide-list 5, D-R1).** B 8110-6-1:2024 9.1 (p. 49) lists the radiation values in
22.5° azimuth and 15° tilt steps and says: "Zwischenwerte für einen beliebigen Azimut bzw. für eine
beliebige Neigung dürfen mittels einer linearen Interpolation durchgeführt werden." The method
therefore interpolates linearly, first in azimuth, then in tilt, between the four surrounding grid
values of Annex E (site climate) and Annex A (reference climate), and returns a grid value exactly
when the window lies on the grid, which is the case for every HiSim window. Annex E is tabulated
for azimuths 0…180° and is used with |azimuth|; the Annex A tables are keyed by the orientations
they print, as signed azimuths from south (east negative, west positive), so an east and a west
value that the norm prints differently stay different. A tilt outside 0…90° or a non-finite angle
is refused (E-08). The same clause allows an *existing* building's geometry to be simplified to
90° azimuth and 45° tilt steps; the method does not simplify, it uses the angles it is given.

**KPIs of the Building (decide-list 1, D19).** The EPC values are not emitted by a component, but
they describe exactly one `Building` and nothing else. They therefore carry that building's
`KpiSource` (`Component.kpi_source()`, captured by the input check) and the key
`"<name> (Building)"`, like the building's own KPIs; a reader of `all_kpis.json` or the finder
addresses them as building KPIs, and a second building in a district run keeps its own set apart.
`kpi_address_spec.md` reserves `source: null` for derived KPIs; its "source of a component KPI"
section gains one sentence saying that certificate KPIs carry the source of the building they are
computed for (PR-9).

**Numerics.** All arithmetic in IEEE double without intermediate rounding; the month's hours are
`days × 24` with 28 days in February (the norm's tables are climatological means, not a calendar
year); every table value is used exactly as printed. If the first reproduction of B 8110-6-2 shows
residuals above the printed precision, the cause is sought in a rounding step the norm prescribes
and recorded in §3.4 (R9.4, risk R-1).

### 3.5 Stage 1b interface (R4.12)

`at.balance` takes `recoverable_hot_water_losses_kwh_per_month: tuple[float, ...] | None`. Stage 1a
passes `None`, which is Q_TW,beh = 0: the site-climate heating-period fraction f_H uses
γ_H = Q_g / Q_l, and the KPI description carries the label of R3.5. Stage 1b adds, on the HiSim
side, `hisim_hot_water.py` with `hot_water_from_components(wrapped_components) -> HotWaterQuantities`
(a neutral record of the run's hot-water system: storage volume, generator kind, distribution
length class, each `None` where HiSim states nothing, so the norm defaults apply), and on the norm
side `at/hot_water.py` (H 5056-1 Chapter 6: Q_TW,WA, Q_TW,WV with the defaults of 6.3.2, Q_TW,WS,
Eq. (15) Q_TW,beh) fed by wwwb of B 8110-5 Tab. 9 and that record; the exact coupling into γ_H and
the balance is written in this section once ÖNORM H 5056-2 is at hand and its "Ausstattung 1" is
known. Nothing in stage 1a's public surface changes for 1b except the description text and one
added KPI (annual recoverable hot-water losses); `at/` keeps importing no HiSim component (§3.6).

### 3.6 Boundaries and dependency rule

The package `hisim.postprocessing.epc_computation` imports `KpiEntry`, `KpiSource`, `KpiAddress`
and `KpiTagEnumClass` from `kpi_computation`, the `Building` and `Weather` component types for
`isinstance` checks, `utils.get_input_directory` for its data, and nothing from the prototype (R8.4).
`postprocessing_main` loads it lazily (`_load_attribute`) in the early check and in the step;
`simulator.check_epc_inputs` imports `prepare_epc_inputs` inside the method, so a run without the
option never imports the package. `kpi_preparation.read_epc_results` imports only
`KpiEntry`/`KpiAddress`, which it already has, and reads plain `EpcValue` tuples and the stored
`KpiSource` from `ppdt.epc_results`. `at/` imports only `quantities`, `site`, `method` and `errors`
of its parent, never HiSim components (requirements C4 is thereby a constraint of the HiSim side
alone).

## 4. Alternatives considered

| # | Design point | Chosen | Rejected, with the deciding consequence |
|---|---|---|---|
| 4.1 | Where the EPC runs and how its values reach the KPIs | Own step before `COMPUTE_KPIS` like OPEX/CAPEX; results on `ppdt`; KPI preparation reads them | Hook inside `KpiGenerator`: hidden inside another step, no report chapter of its own. Second pass after `COMPUTE_KPIS`: misses the KPI report table, mutates the sorted collection the finder caches by identity. Inside `Building.get_component_kpi_entries`: a component would import postprocessing logic and read postprocessing options. A CSV re-read like the cost KPIs: a file written only to be parsed back. |
| 4.2 | Building description | Neutral `BuildingQuantities` + `Boundary` enum | Method reads `BuildingInformation` directly: ties norm code to TABULA; the norm examples could not be run without faking a `Building`. HiSim side picks f-factors: ties HiSim to one norm. A second user-facing description: rejected by D14. |
| 4.3 | Site home | Optional fields on `WeatherConfig` | Block in simulation parameters (the cost engine's `set_economic_parameters` pattern), fields on `BuildingConfig`: rejected by D12. |
| 4.4 | Off-grid azimuth or tilt | Linear interpolation per B 8110-6-1 9.1 | Refuse: the norm permits interpolation. Nearest grid value: silently changes the input. |
| 4.5 | Data location | Local `hisim/inputs/epc/at/*.csv`, git-ignored, with a committed data contract | Committed anywhere, including a package folder like `hisim/cost_database/`: licensed content (D16). Python literals: 1 500 numbers in code, unreviewable against the PDF, and licensed all the same. |
| 4.6 | Data format | CSV, `#` comment header with the citation, `,` separator, `.` decimal | JSON: no comment lines for the citation; YAML: slower to diff, no advantage. |
| 4.7 | Result shape | Method returns `EpcValue` list; the step pairs it with the building's `KpiSource`; KPI preparation builds `KpiEntry` | Method returns `KpiEntry` directly: the method would need the tag, the source and HiSim's KPI types, and a second country could not share the filing code. |
| 4.8 | Country selection | `SimulationParameters.country` string, registry dict | Enum of countries: `country` is already a free string used by the cost lookups. Option per country: rejected by D6. |
| 4.9 | Elevation bands and dwelling-unit classes | Boundaries exactly as the table headers print them (`h ≤ 750`, `750 < h ≤ 1 500`; F_S: ≤ 2, 3–10, > 10 units; usage: ≤ 2, 3–9, ≥ 10) | The prose of B 8110-5 5.2.1 ("unter 750 m") would put 750 m in the middle band; the table header decides, and the validation example at exactly 750 m will confirm it. |
| 4.10 | Tests that need the tables | Validation V-1 … V-5 with marker `epc_norm_data`, skip with reason when absent, output attached to the PR; functionality tests carry no norm value | Commit the tables or spot values for CI: licensed (D16, D17). Mock tables in tests: would test the mock, not the norm. Drop the validation: the norm reproduction is the gate (R9). |
| 4.11 | When the inputs are checked | In the `Simulator` before the first time step and again first thing in postprocessing | Postprocessing only: a year-long run dies after hours for an input that was wrong at the start, the failure mode `cost_spec.md` §9.2 names. Simulator only: the option test framework and stored results never pass the simulator. |
| 4.12 | Source of the EPC KPIs | The building's `KpiSource`, key `"<name> (Building)"` (D19) | `source: null` as for derived KPIs: the finder could not tell two buildings' certificates apart by source, and the entries would not read as the building's. |
| 4.13 | TABULA sub-areas with `b_Transmission = 0` and area > 0 | Boundary `CONDITIONED_NEIGHBOUR`, factor 0, outside the envelope (B 8110-6-1 Section 4, p. 8); decides the merged element only when every sub-area is one | Refuse (E-07): 9 of the 102 national rows (AB.05, SFH.05, TH.03) could never get a certificate. Treat as outside air: counts a party wall HiSim gives no conductance. |
| 4.14 | HiSim's apartment count of 0 | Refuse (E-14) naming `number_of_apartments` | Map 0 into the "≤ 2" class: a count the norm's usage profile does not define would be patched silently (R5.8). Round up to 1: a second apartment rule beside HiSim's (D18). |

## 5. Public surface

### 5.1 Enum members `[proposed]`

```python
class PostProcessingOptions(IntEnum):
    ...
    # Computes the energy performance certificate values of every Building of the run with the
    # EPC method of SimulationParameters.country, writes them to the report and leaves them for the
    # KPI step, which files them as KPIs of the Building under
    # KpiTagEnumClass.ENERGY_PERFORMANCE_CERTIFICATE. Requires COMPUTE_KPIS and the norm tables
    # under hisim/inputs/epc/<country>/. Every input is checked before the first time step
    # (Simulator.check_epc_inputs); a country without a method refuses the run there
    # (roadmap/epc_computation/design.md).
    COMPUTE_EPC = 31

class KpiTagEnumClass(Enum):
    ...
    ENERGY_PERFORMANCE_CERTIFICATE = "Energy Performance Certificate"
```

### 5.2 `WeatherConfig` fields `[decided 2026-10-07, D12]`

```python
#: National climate-region code of the place, as the EPC method of the run's country defines it
#: (Austria: W, NF, N, ZA, SB, S/SO, N/SO per OENORM B 8110-5:2024 Bild 1). Unset for a run that
#: computes no certificate.
climate_region: Optional[str] = None
#: Elevation of the place above sea level in m. Unset for a run that computes no certificate.
elevation_in_m: Optional[float] = None
```

Both are plain fields (not `sized_field`), take no part in `identity()` and therefore in no cache
key, and are written in an energy-system file as config overrides beside a preset or a constructor:

```yaml
  Weather:
    class: hisim.components.weather.Weather
    preset: aachen
    config:
      climate_region: "S/SO"
      elevation_in_m: 356
```

**Why the schema file changes.** `hisim/energy_system_v3.schema.json` is the JSON Schema of the
energy-system YAML format. It is generated from the configuration classes
(`hisim energy-system schema`, `hisim/energy_system/schema_export.py`), every twin binds to it in
its first line for editor validation and completion, it is shipped as package data, and
`tests/test_cli.py::test_schema_writes_exactly_the_committed_file` fails whenever the generated
schema and the committed file differ. Two new fields on `WeatherConfig` appear in the generated
schema, so the file is regenerated and committed in the same PR; otherwise the base test suite goes
red. That is the only reason to touch it.

### 5.3 Package and HiSim API `[proposed]`

```python
# hisim/postprocessing/epc_computation/__init__.py
@dataclass(frozen=True)
class EpcInputs:
    label: str                      # the building label (component_id.building_label)
    source: KpiSource               # Building.kpi_source(), captured here for the KPI filing
    quantities: BuildingQuantities
    site: Site
    method: EpcMethod

def prepare_epc_inputs(
    wrapped_components: Sequence[ComponentWrapper],
    simulation_parameters: SimulationParameters,
) -> dict[str, EpcInputs]
    """Method, tables, site and quantities of every Building of the run, or a refusal.
    Raises E-01, E-03 … E-07, E-09, E-12 … E-14. Pure: no I/O besides reading the tables."""

@dataclass(frozen=True)
class EpcBuildingResult: source: KpiSource; result: EpcResult

def epc_calculation(
    wrapped_components: Sequence[ComponentWrapper],
    simulation_parameters: SimulationParameters,
) -> tuple[dict[str, EpcBuildingResult], list[list[str]]]
    """prepare_epc_inputs, then method.compute per building. Returns the results by building
    label and the report table (one row per value). Raises what prepare_epc_inputs raises, plus
    E-08 and E-11."""

# hisim/simulator.py
class Simulator:
    def check_epc_inputs(self) -> None     # NEW, called beside check_cost_declarations; no-op unless
                                           # COMPUTE_EPC is set; E-02 when COMPUTE_KPIS is missing

# hisim/postprocessing/postprocessing_datatransfer.py
class PostProcessingDataTransfer:
    def __init__(..., epc_results: Optional[Dict[str, "EpcBuildingResult"]] = None, ...)   # NEW kwarg, {} by default

# hisim/components/building/information.py
class BuildingInformation:
    number_of_apartments_origin: str   # NEW: "configured" | "TABULA row n_Apartment" |
                                       # "HiSim estimate: conditioned floor area / 92.1 m2 per apartment (scaled archetype)"
    door_area_origin: str              # NEW public name of the recorded door-area origin; value unchanged

# hisim/postprocessing/epc_computation/quantities.py
class Boundary(Enum):               # country-neutral; the situations HiSim can produce
    OUTSIDE_AIR; CONDITIONED_NEIGHBOUR
    CEILING_TO_UNCONDITIONED_ATTIC; WALL_TO_UNCONDITIONED_ATTIC
    CEILING_TO_UNCONDITIONED_CELLAR_UNINSULATED; WALL_TO_UNCONDITIONED_CELLAR_UNINSULATED
    CEILING_TO_UNCONDITIONED_CELLAR_INSULATED;   WALL_TO_UNCONDITIONED_CELLAR_INSULATED
    CEILING_TO_CLOSED_GARAGE; FLOOR_ON_GROUND_SHALLOW; FLOOR_ON_GROUND_DEEP
    WALL_TO_GROUND_SHALLOW; WALL_TO_GROUND_DEEP; WALL_TO_BUFFER_SPACE; CEILING_TO_BUFFER_SPACE
class ConstructionClass(Enum): LIGHT; MEDIUM; HEAVY

@dataclass(frozen=True)
class OpaqueElement: name: str; area_in_m2: float; u_value_in_watt_per_m2_per_kelvin: float; boundary: Boundary
@dataclass(frozen=True)
class Window: name: str; azimuth_in_degree: float; tilt_in_degree: float; area_in_m2: float
              u_value_in_watt_per_m2_per_kelvin: float; g_value: float
@dataclass(frozen=True)
class BuildingQuantities:
    gross_floor_area_in_m2: float          # BGF
    gross_volume_in_m3: float              # V
    dwelling_units: int
    construction: ConstructionClass
    opaque_elements: tuple[OpaqueElement, ...]
    windows: tuple[Window, ...]
    origin: str                            # provenance text for the KPI (R5.10)
    # __post_init__ refuses non-positive BGF, V, areas, U-values, g outside (0, 1], dwelling_units < 1  -> EpcInputError

# hisim/postprocessing/epc_computation/site.py
@dataclass(frozen=True)
class Site: climate_region: str; elevation_in_m: float
def site_from_weather(wrapped_components: Sequence[ComponentWrapper]) -> Site      # E-03

# hisim/postprocessing/epc_computation/hisim_building.py
def quantities_from_building(building: Building) -> BuildingQuantities                # E-06, E-07, E-14

# hisim/postprocessing/epc_computation/method.py
@dataclass(frozen=True)
class EpcValue: name: str; unit: str; value: float | str; description: str
@dataclass(frozen=True)
class EpcResult: values: tuple[EpcValue, ...]; norm_edition: str
class EpcMethod(Protocol):
    country: ClassVar[str]
    norm_edition: ClassVar[str]
    def load_tables(self) -> None: ...                                                   # E-12, E-13; cached
    def validate_site(self, site: Site) -> Site: ...                                    # E-04, E-05
    def compute(self, quantities: BuildingQuantities, site: Site) -> EpcResult: ...
METHODS_BY_COUNTRY: Mapping[str, type[EpcMethod]]      # {"AT": AustrianEpcMethod}
def method_for_country(country: str) -> EpcMethod                                       # E-01
def register_method(method: type[EpcMethod]) -> None   # for tests (AC8) and future countries

# hisim/postprocessing/kpi_computation/kpi_preparation.py
def read_epc_results(self, building_object: str) -> None   # NEW; files ppdt.epc_results[building_object]; E-10

# hisim/postprocessing/epc_computation/errors.py
class EpcError(ValueError): ...
class EpcNotImplementedError(EpcError): ...       # E-01
class EpcInputError(EpcError): ...                # E-03..E-09, E-11, E-14
class EpcDataError(EpcError): ...                 # E-12, E-13
```

The Austrian sub-package (`at/`) exposes `AustrianEpcMethod` and, for tests, its term functions
(`transmission_conductance`, `ventilation_conductance`, `internal_gains`, `solar_gains`,
`utilisation_factor`, `heating_period_fraction`, `monthly_heating_demand`, `reference_climate`,
`site_climate`, `usage_profile`). Their signatures take plain floats, tuples of twelve floats and the
dataclasses above, so a functionality test can call any term with hand-given inputs and no table
(§9). `AustrianEpcMethod(data_folder=...)` takes the folder of its tables, defaulting to
`utils.get_input_directory()/epc/at`, so the loader tests point it at a synthetic folder without
monkeypatching.

### 5.4 KPI entries `[proposed; units decided 2026-10-07, Q-D4; source decided, D19]`

Tag `Energy Performance Certificate`; source = the building's `KpiSource` as captured by the input
check; key per `KpiAddress.key_for`. Every EPC value is an annual value; the unit spelling follows
the existing KPIs (`kWh/m2` as the TABULA reference entry, `kWh`, `W/K`, `m2`), and each
description says "per year" (R7.2).

| Name | Unit | Value | Description cites |
|---|---|---|---|
| EPC heating demand, reference climate | kWh/m2 | HWB_RK per year | B 8110-6-1 Eq. (52), (64); H 5050-1 Eq. (9) |
| EPC heating demand, site climate | kWh/m2 | HWB_SK per year | B 8110-6-1 Eq. (53), (64); H 5056-1 Eq. (49)–(52); stage 1a: "without recoverable hot-water losses (Q_TW,beh = 0)" |
| EPC transmission losses, reference climate / site climate | kWh | Σ Q_T,j | Eq. (29a) |
| EPC ventilation losses, reference climate / site climate | kWh | Σ Q_V,j | Eq. (30) |
| EPC internal gains | kWh | Σ Q_i,j | Eq. (37) |
| EPC solar gains, reference climate / site climate | kWh | Σ Q_s,j | Eq. (43), (44) |
| EPC transmission conductance L_T | W/K | L_T | Eq. (10a), (11a) |
| EPC ventilation conductance L_V | W/K | L_V | Eq. (13)–(15) |
| EPC mean U-value U_m | W/(m2 K) | L_T / A | Eq. (63a) |
| EPC characteristic length | m | V / A | Eq. (1) |
| EPC time constant | h | τ | Eq. (49) |
| EPC gross floor area BGF | m2 | BGF | Sec. 4 |
| EPC conditioned gross volume V | m3 | V | Sec. 4 |
| EPC envelope area | m2 | A (without conditioned-neighbour elements) | Sec. 4 |
| EPC heating days, site climate | d | Σ f_H,j · MT_j | H 5056-1 Eq. (49) |
| EPC heating degree days 22/14, site climate | Kd | Σ_{θ_e,j ≤ 14 °C} (θ_i,h − θ_e,j)·MT_j | B 8110-5 Eq. (4); §13 Q-D2 |
| EPC dwelling units | - | count, HiSim's | B 8110-5 Sec. 8; D18 |
| EPC dwelling-unit class | - | "1-2" / "3-9" / "10+" | B 8110-5 Sec. 8 |
| EPC climate region | - | string | B 8110-5 Bild 1 |
| EPC elevation | m | h | — |
| EPC construction class | - | "light" / "medium" / "heavy" | B 8110-6-1 9.1.2 |
| EPC building data origin | - | string (R5.10, §3.3) | — |
| EPC norm edition | - | "OENORM B 8110-6-1:2024-03 / B 8110-5:2024-03 / H 5050-1:2024-03 / H 5056-1:2024-03" | — |

### 5.5 Report chapter

When `GENERATE_PDF_REPORT` is set, the step writes the chapter ". Energy performance certificate"
with one row per EPC value and building (name, value, unit), as the OPEX chapter does for costs.

### 5.6 Errors

See §8 for the catalogue; all are `EpcError`, a `ValueError`, so the simulator's and the
postprocessor's existing handling of configuration errors applies.

## 6. Internal structure

| Unit | Responsibility | Must not know |
|---|---|---|
| `postprocessingoptions.py` (+1 member) | wire form of the option | anything else |
| `simulator.py:Simulator.check_epc_inputs` (new, ~15 lines) | the pre-run refusal: E-02, then `prepare_epc_inputs` | norm content, KPI types |
| `postprocessing_main.py:PostProcessor.run` (+1 step, +2 checks) | the same two checks before the first step, the EPC step with its report chapter | norm content |
| `postprocessing_datatransfer.py` (+1 kwarg) | carry `epc_results` from the step to the KPI step | norm content |
| `kpi_computation/kpi_preparation.py:read_epc_results` (new, ~25 lines) | file `EpcValue`s as `KpiEntry`s under the EPC tag with the stored source | norm content, TABULA |
| `kpi_computation/kpi_structure.py` (+1 tag) | the tag | — |
| `components/weather/config.py` (+2 fields) | the site fields | EPC |
| `components/building/information.py` (+2 origin strings) | record where the apartment count and the door area came from, beside the values | EPC |
| `epc_computation/__init__.py` | `EpcInputs`, `prepare_epc_inputs`, `EpcBuildingResult`, `epc_calculation`, docstring, public names | norm content |
| `epc_computation/errors.py` | exception hierarchy | — |
| `epc_computation/quantities.py` | neutral dataclasses and validation | HiSim, norms |
| `epc_computation/site.py` | find the one Weather, read the two fields | norms |
| `epc_computation/hisim_building.py` | `Building` → `BuildingQuantities`; the TABULA → Boundary rule (§7.2); the origin text | norms |
| `epc_computation/method.py` | protocol, result types, registry | HiSim components, norms |
| `epc_computation/at/method.py` | `AustrianEpcMethod`: tables, site validation, orchestration, value list, edition | HiSim components |
| `epc_computation/at/data_tables.py` | load and cache the CSVs from the data folder, band lookups, E-12 and completeness checks at load | balance |
| `epc_computation/at/climate.py` | reference and site climate, irradiation by azimuth and tilt with linear interpolation | building |
| `epc_computation/at/usage.py` | usage profile and F_S,h by dwelling units | climate |
| `epc_computation/at/envelope.py` | f(Boundary), L_T with Eq. (11a), L_V, A, U_m, ℓ_c | climate |
| `epc_computation/at/gains.py` | Q_i, A_trans, Q_s | balance |
| `epc_computation/at/balance.py` | losses, C, τ, a, η, f_h / f_H, Q_h,j, annual values | HiSim |
| `hisim/inputs/epc/README.md` (committed) | the data contract: files, columns, units, row counts, citations | values |
| `hisim/inputs/epc/at/*.csv`, `SOURCES.md` (local, git-ignored) | the norm tables with citations and checksums | — |
| `.gitignore` (+1 line), `pytest.ini` (+1 marker), `postprocessing_option_test_framework.py` (+1 map entry) | keep the tables out of git; mark the validation; declare the option's dependency | — |

Dependency direction: `simulator` → (lazy) `epc_computation`; `postprocessing_main` → (lazy)
`epc_computation` → `at`; `at` → nothing above `quantities`/`site`/`method`/`errors`. The
repository states no line budget; the target is ≤ 300 lines per module, which the
one-function-per-term split meets.

## 7. Data, state and invariants

### 7.1 Data files (R8.1–R8.3, R8.5, R11) `[decided 2026-10-07, D16, D17]`

Location `hisim/inputs/epc/at/`, under the inputs directory (`utils.get_input_directory()`), beside
the shipped reference datasets. **The tables are not committed**: `hisim/inputs/epc/at/` is
git-ignored, the folder exists on the machines of people who hold the norms. What the repository
carries is the data contract `hisim/inputs/epc/README.md` with, per file, its name, columns, units,
row count and source citation, so that anyone with the norms can produce the tables, and the
loader's checks: E-12 when a file is missing, E-13 when a table is incomplete (I-7), which make a
wrong or incomplete transcription fail loudly. The row counts and column names the loader checks
are the single source; the README states the same numbers and is reviewed against the loader.
Beside the tables, local, `SOURCES.md` lists the citations and the checksum of each file. Each CSV
starts with `#` lines naming norm, edition, clause/table, page and the transcription method ("typed
from the PDF, checked by a second independent transcription"); pandas reads them with
`comment="#"`.

| File | Content | Shape |
|---|---|---|
| `reference_climate_b8110_5_2024_annex_a.csv` | Tab. A.1 (θ_e, I_S horizontal), A.2 (vertical by orientation), A.3–A.7 (tilts 75/60/45/30/15) | month, tilt_deg, azimuth_deg (signed from south, as the orientation is printed), value_kwh_per_m2; plus month, theta_e_c |
| `temperature_coefficients_b8110_5_2024_annex_b.csv` | Tab. B.1–B.7 | region, band (`<=750`, `750<h<=1500`, `>1500`), month, a, b |
| `radiation_coefficients_b8110_5_2024_annex_d.csv` | Tab. D.1–D.14 | region, month, a2, a1, a0 |
| `transposition_factors_b8110_5_2024_annex_e.csv` | Tab. E.1–E.7 | band (`E1`…`E7` with their m-ranges in the header), month, azimuth_deg (0…180 in 22.5 steps), tilt_deg (0…90 in 15 steps), f_t |
| `usage_profiles_b8110_5_2024_tab7_tab9.csv` | residential rows of Tab. 7 and 9 | class (`1-2`, `3-9`, `10+`), theta_i_h_c, n_l_hyg_per_h, q_i_h_w_per_m2, wwwb_wh_per_m2_d |
| `temperature_correction_factors_b8110_6_1_2024_tab3_5.csv` | Tab. 3–5 rows that `Boundary` covers | boundary, f_h, f_c, table, row_text |
| `validation_examples_b8110_6_2_2024.csv` | the inputs and the expected monthly and annual values of B 8110-6-2 Sections 4 and 5 (Tables 1–10), the oracle of V-1/V-2 | example, variant, case, month (1–12 or "annual"), value |

What stays in code, published, is the formulas and the single constants written inside them with
their citation beside each (f_g = 0.7, F_g = 0.9·0.98, 0.34 Wh/(m³K), 2.6 m, BF = 0.8·BGF,
f_BW 10/20/30, a₀ = 1, τ₀ = 16, θ_ne,RK = −13 °C, F_S,h 0.65/0.50/0.40, the 0.2/0.75/0.1 of Eq. (11a))
and the factor 0 of a conditioned neighbour (B 8110-6-1 Section 4, p. 8). Everything that is a table in a norm, including
the three usage-profile rows and the f-factor rows, is a local file (D16). No PDF is committed
(R8.3). AGENTS.md's rule not to touch the bulk data under `hisim/inputs` is respected: the folder is
new, nothing existing changes.

### 7.2 TABULA → Boundary rule (R5.4, assumption A2) `[proposed]`

The rule reads each sub-area's `b_Transmission` first, then the code the sub-area's element needs;
sub-areas with area 0 are skipped.

| Element | `b_Transmission` | Codes | Boundary |
|---|---|---|---|
| Wall_1..3 | 1 | any | OUTSIDE_AIR |
| Wall_1..3 | 0.5 | any | WALL_TO_BUFFER_SPACE |
| Wall_1..3 | 0 | any | CONDITIONED_NEIGHBOUR |
| Roof_1..2 | 1 | `Code_RoofType` = FR | OUTSIDE_AIR |
| Roof_1..2 | 1 | `Code_AtticCond` = N (and not FR) | CEILING_TO_UNCONDITIONED_ATTIC |
| Roof_1..2 | 1 | otherwise (AtticCond C, P, "-", 0) | OUTSIDE_AIR |
| Roof_1..2 | 0.5 | any | CEILING_TO_UNCONDITIONED_ATTIC |
| Roof_1..2 | 0 | any | CONDITIONED_NEIGHBOUR |
| Floor_1..2 | 1 | any | OUTSIDE_AIR |
| Floor_1..2 | 0.5 | `Code_CellarCond` ∈ {N, P} | CEILING_TO_UNCONDITIONED_CELLAR_UNINSULATED |
| Floor_1..2 | 0.5 | `Code_CellarCond` ∈ {"-", 0} | FLOOR_ON_GROUND_SHALLOW |
| Floor_1..2 | 0.5 | `Code_CellarCond` = C | FLOOR_ON_GROUND_DEEP |
| Floor_1..2 | 0 | any | CONDITIONED_NEIGHBOUR |
| Window_*, Door_1 | — | always | OUTSIDE_AIR |
| any | other value | | E-07 |

Every (b, code) combination of the 102 national rows (requirements §4.2) has a row here; E-07 is
reachable only by a future row. Sub-areas merged by HiSim (§3.3) take the boundary with the highest
f-factor among their sub-areas with area > 0; an element whose sub-areas are all
CONDITIONED_NEIGHBOUR is one (Q-D6). The rule is data in `hisim_building.py` (a tuple of rules),
tested over all 102 AT national rows (T-4). Changing a rule is a one-line, reviewable change. The
Austrian factor per Boundary (Tables 3–5; Section 4, p. 8 for the conditioned neighbour):
OUTSIDE_AIR 1.00; CONDITIONED_NEIGHBOUR 0 and no part in A; CEILING/WALL_TO_UNCONDITIONED_ATTIC 0.90; CEILING_TO_CLOSED_GARAGE 0.90;
…CELLAR_UNINSULATED 0.70; …CELLAR_INSULATED 0.50; FLOOR_ON_GROUND_SHALLOW 0.70;
FLOOR_ON_GROUND_DEEP 0.50; WALL_TO_GROUND_SHALLOW 0.80; WALL_TO_GROUND_DEEP 0.60;
WALL/CEILING_TO_BUFFER_SPACE 0.70.

### 7.3 Invariants (checked where stated)

| ID | Invariant | Established | Checked |
|---|---|---|---|
| I-1 | Q_l,j = Q_T,j + Q_V,j | `balance.losses` | T-1 |
| I-2 | 0 ≤ f_h,j ≤ 1; 0 < η_j ≤ 1 for γ > 0; Q_h,j ≥ 0 | `balance` | assertion in `balance`, T-1, V-1 |
| I-3 | RK and SK share `BuildingQuantities`, L_T, L_V, C, τ, a | `AustrianEpcMethod.compute` builds them once | V-1 |
| I-4 | BF = 0.8·BGF; A_g = 0.7·A_W; L_ψ+L_χ ≥ 0.1·Σ f A U | `envelope`, `gains` | T-1 |
| I-5 | Σ window areas per direction = window area in L_T | `hisim_building` | T-3 |
| I-6 | Derived quantities of an archetype without overrides equal the row's values and HiSim's derived values | `hisim_building` | T-3 |
| I-7 | Every table loads complete: 7 regions × 3 bands × 12 months (B), 7 × 12 (D), 7 bands × 12 × 9 × 7 (E), 12 × (1 + orientations × 6) (A) | `data_tables` | E-13 at load, T-11, V-5 |
| I-8 | A KPI key is added once per building | `read_epc_results` | E-10 |
| I-9 | `BuildingQuantities` is immutable and validated on construction | `quantities.__post_init__` | T-2 |
| I-10 | Interpolated radiation equals the table value at every grid point | `climate` | T-1 |
| I-11 | A CONDITIONED_NEIGHBOUR element contributes neither to L_T nor to A | `envelope` | T-1 |
| I-12 | The simulator's check and the postprocessor's check call the same function with the same inputs; what one refuses, the other refuses | `prepare_epc_inputs` is the only entry | T-6 |

Ordering: `check_epc_inputs` before the first time step, after `check_cost_declarations`; the
postprocessing-side check before any other postprocessing step; E-02 before the input preparation;
the EPC step after OPEX/CAPEX and before `COMPUTE_KPIS`; `read_epc_results` inside
`create_kpi_collection` before sorting; the method's compute is pure (no I/O besides reading its
cached tables).

## 8. Error handling

Policy: fail fast with the offending party named; nothing is guessed, defaulted or skipped
(requirements R2, R5.8, R6, R8.5; repo rule "fail loudly"). Unlike the lifecycle cost engine, which
logs an accidental engine failure and keeps the legacy outputs, every `EpcError` fails the run: the
certificate was asked for, and there is no second cost path whose outputs could stand in.

| ID | Condition | Raised by | Exception | Message template |
|---|---|---|---|---|
| E-01 | `COMPUTE_EPC` set, country without method | `prepare_epc_inputs` (simulator check and postprocessor) | `EpcNotImplementedError` | "EPC calculation is not possible: no EPC method is implemented for country '{country}' yet (implemented: {countries})." |
| E-02 | `COMPUTE_EPC` set without `COMPUTE_KPIS` | `Simulator.check_epc_inputs`, `PostProcessor.run` | `ValueError` (as `WRITE_KPIS_TO_JSON`) | "PostProcessingOptions.COMPUTE_EPC requires PostProcessingOptions.COMPUTE_KPIS; set both." |
| E-03 | weather has `climate_region` or `elevation_in_m` unset, or no `Weather` in the run | `site_from_weather` | `EpcInputError` | "The EPC needs the site of the run: set '{field}' on the Weather configuration ('{weather name}'). Austria: climate_region one of {regions}, elevation_in_m ≥ 0." |
| E-04 | region not one of the method's codes | `validate_site` | `EpcInputError` | "Climate region '{value}' is not a region of OENORM B 8110-5:2024 Bild 1; use one of {regions}." |
| E-05 | elevation < 0 or not finite | `validate_site` | `EpcInputError` | "Elevation {value} m is not a valid site elevation; give a finite value ≥ 0." |
| E-06 | row lacks `A_C_ExtDim` or `V_C` (empty or 0), or a derived quantity is not finite | `quantities_from_building` | `EpcInputError` | "The TABULA row '{code}' states no {column}; the EPC needs it for {quantity} of building '{name}'." |
| E-07 | no rule for a sub-area's b value and codes | `quantities_from_building` | `EpcInputError` | "No boundary rule for sub-area '{sub_area}' of '{code}' (b={…}, RoofType={…}, AtticCond={…}, CellarCond={…}); extend the rule table in hisim_building.py." |
| E-08 | window tilt outside 0…90° or a non-finite angle | `at.climate` | `EpcInputError` | "Window '{name}': tilt {t}° is outside 0…90° or the azimuth is not a number; OENORM B 8110-5:2024 Annex E tabulates tilts 0…90°." |
| E-09 | `COMPUTE_EPC` set and the run has no `Building` component | `prepare_epc_inputs` | `EpcInputError` | "The run has no Building component; the EPC is computed per Building." |
| E-10 | KPI key already present in the collection | `read_epc_results` | `ValueError` (as `keyed_component_entries`) | "The EPC entry '{key}' already exists in the KPI collection of '{label}'." |
| E-11 | gain-loss ratio γ_h ≤ 0 in a month under the reference climate | `at.balance` | `EpcInputError` | "Month {m}: the reference-climate balance has losses ≤ 0 (γ = {value}); OENORM H 5050-1:2024 Eq. (9) states no rule for this case (design.md Q-D1)." |
| E-12 | a table file is missing | `data_tables` | `EpcDataError` | "The EPC method for Austria needs the OENORM tables under '{folder}' ({file} is missing). They are licensed content of Austrian Standards and are not part of the repository; see hisim/inputs/epc/README.md for the files, their columns and sources." |
| E-13 | a table fails its completeness check at load (I-7) | `data_tables` | `EpcDataError` | "{file}: expected {n} rows for {dimension}, found {m}." |
| E-14 | HiSim's `number_of_apartments` is 0 | `quantities_from_building` | `EpcInputError` | "Building '{name}': HiSim's number of apartments is 0 ({origin}); the EPC usage profile needs at least one dwelling unit. State the count with 'number_of_apartments' in the building configuration." |

Not errors: a direction with window area 0 (dropped, I-5 still holds); a sub-area with area 0
(skipped); a run with several buildings (each gets its own entries); a run with district objects
(they get none); a negative γ_H under the site climate (H 5056-1 7.1 states the replacement rule,
which is applied); an element whose sub-areas are all conditioned neighbours (factor 0, I-11).

## 9. Testing strategy

Two kinds of tests, by decision D17, because the public CI has no norm tables (requirements C6):

- **Functionality tests** (`T-n`) are committed and run everywhere. The term functions take their
  table values as arguments (a twelve-month climate tuple, a usage profile, an f-factor), so T-1
  checks every equation with hand-given numbers that are not norm values; T-3, T-4 and T-7 use
  TABULA and code only; T-11 checks the loader's refusals on synthetic files. No functionality test
  contains a value taken from a norm table (R8.3); the reviewer checks this on the diff (§12).
- **Validation** (`V-n`) carries the marker `epc_norm_data` (added to `pytest.ini`, described like
  `upstream_history`: "needs the licensed OENORM tables under hisim/inputs/epc/at; skipped when
  absent") and calls `pytest.skip` with the folder name when the tables are missing, the pattern of
  `tests/test_utils.py` for the smart-appliances database. It runs on the machines that hold the
  norms; its output is attached to the pull request (R9.7, AC2).

| ID | Verifies | Kind | Fixture | Failure looks like |
|---|---|---|---|---|
| T-1 | R4.1–R4.11, D-R1, D-R3, I-1, I-2, I-4, I-10, I-11 | unit, hand-computed | one opaque element, one window, twelve-month toy climate and toy F_T grid passed as values; the Eq. (11a) lower-bound case (Ū_f > 0.75); the four f_h cases; interpolation exact at grid points and linear between two azimuths and two tilts; a CONDITIONED_NEIGHBOUR element; E-08; E-11 | a term deviating from its equation |
| T-2 | I-9 | unit | `BuildingQuantities` with each invalid field in turn | a silent acceptance |
| T-3 | AC9, AC10, I-5, I-6, R5.2, R5.3, R5.6, R5.7, R5.9, R5.10 | unit | `BuildingConfig.for_tabula_code("AT.N.SFH.04.Gen.ReEx.001.001")` with and without overrides; a row with two roof sub-areas; the copied row frame edited to `V_C = 0` (E-06); a configured floor area that makes HiSim estimate the apartments | a derived quantity not equal to the row or to HiSim, an override ignored, a wrong origin text, a merged element with the wrong boundary |
| T-4 | R5.4, E-07, AC10 | property over data | all 102 `AT.N.` rows; the AB.05, SFH.05 and TH.03 rows assert CONDITIONED_NEIGHBOUR for their b = 0 sub-areas | a row whose sub-area has no boundary rule, a wrong boundary for a known row |
| T-5 | AC3, AC11, C1, R1.3, D-R2 | contract | `test_postprocessing_option_compute_epc` on the framework's default baseline (country "DE"): E-01 from the postprocessing-side check, no file written before it; `DEPENDENCIES_BY_OPTION[COMPUTE_EPC] == (COMPUTE_KPIS,)`; `test_each_postprocessing_option_has_a_named_test` | a plot written before E-01, a wrong error, the guard failing |
| T-6 | AC3, AC4, AC12, R1.4, R2, R6, I-12 | integration | `tests/test_epc_computation.py`: a hand-built setup (Weather `preset_aachen` with the two fields, `Building` for an AT code, one day hourly, country "AT"); the `Simulator` refuses before the first time step for country "DE" (E-01), option without `COMPUTE_KPIS` (E-02), no site (E-03), bad region (E-04), negative elevation (E-05), an 80 m² scaled archetype (E-14); the same setup refused identically by `PostProcessor.run`; each refusal leaves no result file | a run that simulates before refusing, a differing message between the two checks |
| T-7 | AC8, R10 | unit | `register_method` with a dummy country "ZZ" and a stub result | the Austrian package touched to add a country |
| T-8 | AC5, R12 | regression | existing golden-reference tests, `pytest -m base` | any changed golden value |
| T-9 | R6, R12 | contract | `test_schema_writes_exactly_the_committed_file`; loading every `energy_systems/*.yaml` | schema drift, a twin that stops loading |
| T-10 | AC7, R8.4 | static | grep over `hisim/` and `tests/` for `oenorm_hwb_calculator` | a stray import or link |
| T-11 | AC6, R8.5, E-12, E-13 | unit | `AustrianEpcMethod(data_folder=tmp)` over an empty folder (E-12) and over synthetic CSVs with the contract's columns but one row missing (E-13) | a silent default instead of the refusal |
| V-1 | AC2, R9.2 | golden against the norm, local | B 8110-6-2:2024 Tables 1–5: SFH, variants 1 and 2, cases 1 and 2, built as `BuildingQuantities` from the inputs in the local file `validation_examples_b8110_6_2_2024.csv` (BGF 192, V 576, light, ZA 750 m); expected values from the same file | any monthly or annual value off by more than 0.005 |
| V-2 | AC2, R9.2 | golden against the norm, local | B 8110-6-2:2024 Tables 6–10: MFH, variants 1 and 2.1, cases 1 and 2 | as V-1 |
| V-3 | R4.13, Q-D2 | evidence, local | HGT 22/14 of three real certificates (4169, 3762, 3731 Kd) from region S/SO at 498, 356 and 327 m, 22 °C | the formula drifting from the certificate values |
| V-4 | AC1, R1.2, R7, R11 | integration, local | the T-6 setup run to the end with the tables present; every entry of R7.1 in `all_kpis.json` under the tag, keyed `"<name> (Building)"` with the building's source; the report chapter present when the PDF report is on | a missing KPI key, a wrong source, a wrong unit |
| V-5 | AC6, I-7, R8.1, R11 | contract, local | the real tables: every file loads complete, every header names norm, edition, clause and page | an incomplete or duplicated table row, a header without a citation |

Not tested deliberately: stage 1b (no oracle yet, §3.5); the cooling terms (out of scope); the
PDF report rendering beyond the chapter's existence; the transcription itself beyond V-1/V-2/V-5
(it is reviewed locally against the PDF and recorded in `SOURCES.md`).

## 10. Migration, compatibility and rollout

Behavior changes for nobody: the option is opt-in (R1.1), the two weather fields default to unset,
every recorded twin uses `preset:` or a full `config:` and loads unchanged (T-9), the two origin
strings on `BuildingInformation` change no value (T-8), `ppdt.epc_results` is empty unless the
option is set, and `check_epc_inputs` returns at once without it. The committed schema file changes
once and is regenerated with `hisim energy-system schema`. `.gitignore` gains
`hisim/inputs/epc/at/` and `pytest.ini` the marker `epc_norm_data`. No `HISTORY.rst` entry
(AGENTS.md). No prototype code is moved; the prototype folder stays git-ignored and is deleted by
the requester after merge (D7). A user who wants the certificate obtains the norms and produces the
tables per `hisim/inputs/epc/README.md`; without them the run is refused before the first time step
(E-12) and every other output is unaffected. The ai4c runs set `country: "AT"` in their simulation
parameters (requirements §4.1).

PR sequence `[proposed]`: each PR is one module group with its tests and a green base suite, so the
review bots and the maintainers comment on one subject per round; each PR targets the previous one
(AGENTS.md stacking rules); fixes land as new commits on the PR they belong to.

| PR | Content | Tests | Review subject |
|---|---|---|---|
| PR-1 | `roadmap/epc_computation/requirements.md`, `design.md` | — | the plan |
| PR-2 | `epc_computation/{__init__ (docstring only),errors,quantities,method}.py` | T-2, T-7 | the neutral layer and the registry |
| PR-3 | `hisim/inputs/epc/README.md` (data contract), `.gitignore` entry, `pytest.ini` marker, `at/data_tables.py` with E-12, E-13 and I-7; the tables themselves stay local | T-11; V-5 (local) | the data contract and the loader; the tables are reviewed locally against the PDF |
| PR-4 | `at/climate.py` (reference and site climate, interpolation) | T-1 climate part | Eq. (1)–(3), Annex A/B/D/E lookups |
| PR-5 | `at/usage.py`, `at/envelope.py` | T-1 envelope part | f-factors, Eq. (10a), (11a), (13)–(15), I-11 |
| PR-6 | `at/gains.py`, `at/balance.py`, `at/method.py` | T-1 balance part; **V-1, V-2, V-3** (local, output attached) | the balance and the norm reproduction, the gate |
| PR-7 | `components/building/information.py` (two origin strings), `hisim_building.py` | T-3, T-4, T-8 | HiSim building → quantities, boundary rule, origins |
| PR-8 | `WeatherConfig` fields, schema regeneration, `site.py` | T-9 | the two fields and the schema |
| PR-9 | option member, tag, `EpcInputs`, `prepare_epc_inputs`, `epc_calculation`, `Simulator.check_epc_inputs`, `PostProcessor` step and checks, `ppdt.epc_results`, `read_epc_results`, framework map entry, one sentence in `kpi_address_spec.md`, docs | T-5, T-6, T-8, T-10; V-4 (local) | the wiring and the pre-run check |
| PR-10 (stage 1b) | `hisim_hot_water.py`, `at/hot_water.py`, balance coupling, case-4 validation, §3.5 of this document | V-1 case 4 | after H 5056-2 is at hand |

Beads: one EPC epic with one bead per PR above and one per parking-lot row of the requirements,
created with PR-1 as AGENTS.md prescribes; `.beads/issues.jsonl` is staged with that PR.

## 11. Risks and unknowns

| # | Risk | Trigger | Effect | Mitigation / when known |
|---|---|---|---|---|
| R-1 | Residuals above the printed precision in V-1/V-2 | the norm's software rounds an intermediate (θ_e, I_S,ON, η) | R9.4: find the step, record it in §3.4 | first run of V-1 in PR-6 |
| R-2 | Boundary rule (§7.2) differs from certificate practice | A2 wrong for a code combination | wrong f for that element | T-4 shows coverage; P7 manual tests against real certificates; one-line rule change |
| R-3 | TABULA `V_C` or `A_C_ExtDim` not what the norm means by V or BGF | definition mismatch | wrong C and BGF | A1; checked on the three real buildings in P7 |
| R-4 | Transcription errors in 1 500 table values | typing | wrong climate | second independent transcription locally, I-7 completeness, V-1/V-2 as the end-to-end check, cross-check of a handful of values against the prototype's CSVs during review (read-only, no import) |
| R-5 | The weather fields surprise a sizing or recording path | `describe`/`record` list every field | noise in `describe` output | fields are plain and optional; T-9 |
| R-6 | `ppdt.epc_results` read by a KPI step of a run that never set the option | stale object reuse in tests | wrong entries | the attribute is `{}` by default and only the step fills it; `read_epc_results` is a no-op on `{}` |
| R-7 | Stage 1b equations differ from the sketch in §3.5 | H 5056-2 content | redesign of the coupling | §3.5 rewritten before PR-10 |
| R-8 | Reusing prototype knowledge by habit | author bias | a prototype deviation reproduced (its b-factors as f, its tilt-only interpolation, its heating-degree-day weighting) | every term written from the norm text with citation; V-1 and V-3 catch the known deviations |
| R-9 | Validation example under-specified (door, frame, bridges) | A4 wrong | V-1 cannot pass at 0.005 | the deviation pattern tells which default differs; recorded in §3.4 |
| R-10 | The maintainers cannot run the validation on CI and may hesitate to merge a feature they cannot verify | licensed tables stay local (D16) | review friction, or a merged regression nobody on CI sees | functionality tests cover every equation on CI; the data contract is public; the local V-1/V-2/V-4/V-5 output is attached to each PR; the loader refuses loudly (E-12) so a run without tables never produces a wrong number; precedent: the `utsp` and `upstream_history` tests also run only where their prerequisites exist |
| R-11 | HiSim's apartment estimate makes small scaled archetypes refuse (E-14) | conditioned floor area < 92.1 m² without a configured count | the ai4c run must state `number_of_apartments` | the message names the field; the count then comes from the configuration, which HiSim honours everywhere (R5.2) |

## 12. Code-review guide

**Where to look first**

- `at/balance.py`: the heating-period fraction (H 5050-1 Eq. 9g–9j; H 5056-1 7.1 for SK) and the two
  balances (Eq. 52, 53). Check the γ boundary means wrap December→January, the min/max assignment,
  that SK uses f_H·MT/MT, i.e. the same factor, that E-11 fires under RK and the replacement rule
  runs under SK.
- `at/envelope.py`: Eq. (11a) with its lower bound, that `Σ A_i` includes windows and doors, and
  that a CONDITIONED_NEIGHBOUR element is in neither sum (I-11).
- `hisim_building.py`: BGF from `A_C_ExtDim`, not from the conditioned floor area; the rule table
  matches §7.2 line by line; merged elements take the highest-f boundary; the apartment count is
  HiSim's `int` untouched; the origin text is assembled from recorded strings only.
- `simulator.py` and `postprocessing_main.py`: both checks call `prepare_epc_inputs` and nothing
  else; the simulator's runs before the first time step; the postprocessor's before the first plot.
- `at/climate.py`: interpolation exact at the grid (I-10); Annex E with |azimuth|, Annex A with the
  printed orientations.

**Invariant checklist**

- [ ] I-1 … I-4, I-10, I-11 asserted or tested where §7.3 says
- [ ] I-6: T-3 compares against the row and against `BuildingInformation`, not against the function under test
- [ ] I-7: every loader checks shape at load; the README states the same counts
- [ ] I-8: E-10 raised, not overwritten
- [ ] I-12: one `prepare_epc_inputs`, two callers, no second validation path

**Requirement checklist**

- [ ] R1.4: `check_epc_inputs` is called next to `check_cost_declarations` and is a no-op without the option (T-6)
- [ ] R2: E-01 is raised before the first plot/export in `PostProcessor.run` (T-5 asserts no file)
- [ ] R4.4: no `b_Transmission` used as a factor anywhere in `epc_computation`
- [ ] R5.6: no rounding, no `max(1, …)`, no second apartment rule
- [ ] R5.9: no new `BuildingConfig` field
- [ ] R5.10: the two origin strings are written in `information.py` where the value is decided, and no value changes (T-8)
- [ ] R8.1: every constant in `at/` has a citation comment; every CSV a `#` header
- [ ] R8.3: no table value in the diff, in `hisim/` or in `tests/`; `hisim/inputs/epc/at/` git-ignored
- [ ] R8.4: T-10 present and green
- [ ] R9: V-1/V-2 tolerances are 0.005, not loosened; the V-* output is attached to the PR

**Smells to reject**

- any `try/except` that substitutes a default for a missing row value or a missing table (R5.8, R8.5)
- a KPI entry built inside `at/` (4.7)
- an import of `hisim.components` inside `at/` (§3.6)
- a `print` or a file write anywhere in the package (C3)
- a nearest-neighbour lookup where §3.4 says interpolate
- a norm table value written into a test or a module (D17)
- a second derivation of an origin HiSim already records (R5.10)
- a check in the postprocessor that the simulator does not perform, or the reverse (I-12)

**Out of scope for this review**: stage 1b arithmetic (PR-10); the design of a TABULA-free
`Building` (P9); other certificate values (P1); the results file of P11.

## 13. Design questions and their answers

**Q-D1 — Negative gain-loss ratio.** `[answered 2026-10-07: as the norm states it]`
*What the norms say.* H 5056-1:2024 7.1 (p. 40), which governs the heating days d_Heiz of the
site-climate balance (B 8110-6-1 Eq. 53): "Negative γ_H-Werte müssen durch den Wert des nächsten
Monats ersetzt werden, der einen positiven γ_H-Wert aufweist." H 5050-1:2024 Eq. (9), which governs
the reference-climate f_h, states no rule for γ ≤ 0; neither does B 8110-6-1 9.2.1. A negative γ
needs Q_l < 0, i.e. a monthly mean above the set temperature of 22 °C; the reference climate's
warmest month is 21.12 °C (Tab. A.1), so it cannot occur there, and Austrian site climates reach
it only at very low elevations in July, if at all.
*Design.* Site climate: the H 5056-1 replacement rule, as written. Reference climate: no rule is
written, so the case is refused with E-11 instead of inventing one (D-R3); it is unreachable with the
norm's own data.

**Q-D2 — Heating degree days HGT 22/14.** `[answered 2026-10-07: as the norm states it, confirmed by certificates]`
*What the norm says.* B 8110-5:2024 Eq. (4), p. 7: HGT22/14 = Σ_j (θ_i,h − θ_e,j) für θ_e,j ≤ 14 °C,
with θ_e,j the monthly mean (Tab. 1) and the unit Kd/a (Tab. 1), which the sum only yields when
each month's difference is multiplied by its days.
*Evidence.* Computed with the norm's climate model for the three real buildings at hand (region
S/SO, 22 °C):

| Building, elevation | Certificate | Eq. (4) × days, months θ_e,j ≤ 14 °C | all positive months × days | weighted with heating days |
|---|---|---|---|---|
| Muggauberg, 498 m | 4169 Kd | **4169** | 4736 | 4736 |
| Ludersdorf, 356 m | 3762 Kd | **3762** | 4460 | 4135 |
| Kalsdorf, 327 m | 3731 Kd | **3731** | 4403 | 4028 |

*Design.* Eq. (4) with the month's days and the 14 °C month filter; V-3 keeps the three values as
evidence.

**Q-D3 — Merged TABULA sub-areas.** `[answered 2026-10-07: exactly as HiSim does]`
HiSim keeps one element per class with the summed area, the area-weighted U-value and the largest
b-factor of its sub-areas; the derivation keeps that one element and gives it the boundary with
the highest f-factor among the sub-areas with area > 0 (§3.3, §7.2, R5.4). Consequence, accepted:
a party wall merged with an outside wall (SFH.05, TH.03) counts as outside wall in L_T and A, as it
does in HiSim's conductance.

**Q-D4 — KPI units.** `[answered 2026-10-07]`
Every EPC value is annual; the heating demand is reported in `kWh/m2` like the existing TABULA
reference entry, energies in `kWh`, each description says "per year" (§5.4, R7.2).

**Q-D5 — Expected values of the validation examples.** `[answered 2026-10-07, D16]`
They stay in the local file `validation_examples_b8110_6_2_2024.csv`; the validation reads inputs
and expectations from it and skips when it is absent.

**Q-D6 — TABULA sub-areas with `b_Transmission = 0`.** `[answered 2026-10-07: as the norm states it; the boundary rule stays proposed]`
*What the data say.* Nine national rows carry sub-areas with area > 0 and b = 0: AB.05 (every
opaque sub-area), SFH.05 (Wall_2), TH.03 (Wall_3, Roof_2). TABULA's b = 0 marks a party wall or
ceiling to a conditioned neighbour; HiSim gives such a sub-area no conductance and, when it merges
it with a b = 1 sub-area, counts its area at b = 1.
*What the norm says.* B 8110-6-1:2024 Section 4, p. 8: "Jene Flächen, die an konditionierte Räume
in anderen Gebäuden/Gebäudeteilen grenzen, werden nicht zur Gebäudehülle gezählt"; item g) puts the
boundary on the centre line of the shared construction. Such a surface has no factor in Tables 3–5
and no part in A (requirements §4.3).
*Design.* Boundary `CONDITIONED_NEIGHBOUR` with factor 0 and no part in A (I-11); it decides a
merged element only when every sub-area is one, which mirrors HiSim (Q-D3). Alternatives in §4.13.

**Q-D7 — When the inputs are checked.** `[answered 2026-10-07, D21]`
*Context.* The certificate reads configurations and the TABULA row, never a time series; the
lifecycle cost engine refuses an unpriceable fleet in `Simulator.check_cost_declarations` before
the first time step and repeats the check in its bridge (`cost_spec.md` §9.2–9.3).
*Design.* `Simulator.check_epc_inputs` runs `prepare_epc_inputs` and discards the result; the
postprocessor runs the same function first thing (D-R2). One function, two callers (I-12).
Alternatives in §4.11.

**Q-D8 — Dwelling units.** `[answered 2026-10-07, D18, D20]`
HiSim's `number_of_apartments` is taken verbatim: `int()` of the configured value, of the row's
`n_Apartment` for an unscaled archetype, or of the conditioned floor area divided by 92.1 m² for a
scaled one. The certificate neither rounds nor substitutes. A count of 0 (a scaled archetype under
92.1 m² without a configured count) is refused with E-14 naming `number_of_apartments`
(alternatives in §4.14). The origin KPI states the count and HiSim's origin string.

**Q-D9 — Source of the EPC KPIs.** `[answered 2026-10-07, D19]`
The building's `KpiSource`, captured by the input check and stored beside the result
(`EpcBuildingResult.source`); key `"<name> (Building)"`. `kpi_address_spec.md` gains one sentence
in PR-9 (§3.4).

No design question is open.

## 14. Glossary

- **RK / SK** — Referenzklima / Standortklima: reference climate (B 8110-5 Annex A) / site climate (Eq. 1–3).
- **BGF / BF / V** — conditioned gross floor area (external dimensions) / reference area 0.8·BGF / conditioned gross volume.
- **f_h, f_H** — monthly heating-period fraction under RK (H 5050-1 Eq. 9) / under SK with γ_H (H 5056-1 7.1).
- **Boundary** — the neutral vocabulary of §5.3 for what an element borders on; **CONDITIONED_NEIGHBOUR** is the member for TABULA's adiabatic sub-areas (b = 0).
- **Origin text** — the provenance string of R5.10 naming the TABULA code, the configured overrides, the dwelling-unit count and the door, each with HiSim's recorded origin.
- **EPC step** — the `COMPUTE_EPC` block of `PostProcessor.run`, shaped like the OPEX and CAPEX blocks.
- **Input check** — `prepare_epc_inputs`, run by `Simulator.check_epc_inputs` before the first time step and by the postprocessor first thing.
- **Data contract** — `hisim/inputs/epc/README.md`, the committed description of the local tables.
- **Functionality test / validation** — a committed test (`T-n`) that runs everywhere without norm data / the local reproduction of the norm's examples (`V-n`, marker `epc_norm_data`, skipped without the tables).
