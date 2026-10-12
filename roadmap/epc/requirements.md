# Requirements: the EPC calculator, a standalone tool on HiSim's realized records

**Status:** draft (in refinement with the requester)
**Date:** 2026-10-12
**Author(s):** Nicolai Gölz, drafted with Claude · **Reviewers:** Nicolai Gölz; HiSim maintainers on the PR
**Related:** GitHub issue #897 "EPC-calculator"; `roadmap/epc/design.md` (the implementation
specification: folder structure, the three steps, the dictionaries); `roadmap/epc/input_contract.md`
(the expanded building record); the maintainers' plan of enforced package boundaries inside the
repository, in which the EPC is the first package kept separate from the start.

Status tags: `[given]` stated by the requester · `[proposed]` introduced by the author ·
`[decided YYYY-MM-DD]` confirmed, see the decision register in §11.3.

---

## 1. Abstract

Every country HiSim is used in rates a building by the heating demand of its energy performance
certificate: a norm calculation on a monthly balance with a norm climate, a norm usage and norm
correction factors, computed by a national method (ÖNORM in Austria, DEAP in Ireland, GEG with DIN
in Germany, CTE in Spain, NTA 8800 in the Netherlands). HiSim produces no such figure, so its dynamic
results cannot be compared with the certificates of the buildings it simulates nor with stock datasets
that carry certificate values. This work adds a standalone calculator, `epc/`, that takes the building
exactly as HiSim computed with it, written out in full beside the run's realized record, computes the
certificate heating demand of that building by the national method, and writes a structured report,
for one run or for thousands. The calculator imports nothing from HiSim and knows nothing of how
HiSim built its building; one small tool of its own writes HiSim's building out. Austria is the pilot;
the four other countries are further engines on the same shell.

## 2. Keywords and tags

```
Tags: feature
Keywords: EPC, Energieausweis, BER, DEAP, CEE, Energielabel, heating demand, HWB, ÖNORM B 8110-6-1,
          GEG, DIN V 18599, CTE DB-HE, NTA 8800, monthly balance, realized record, batch
```

## 3. Executive summary

The calculator is three steps, and the shell is nothing but those three steps and the dictionaries
they exchange:

1. **Inputs.** The expanded building record, HiSim's building as HiSim computed with it (every
   element with its area, U-value and adjustment factor, the windows per direction, the glazing, the
   conditioned floor area, the dwelling units, the site), becomes one country-neutral building
   description.
2. **Engine.** The engine of the run's country applies the national method exactly to that building,
   under the national reference climate and, where the method has one, under the site climate, with
   every value the method had to assume written down.
3. **Report.** The result dictionary is written as JSON per run and building; a batch adds one table
   and one summary over a results tree.

The building description and the result dictionary are the two vocabularies every engine shares;
everything national lives inside one engine package and its data folder. The calculator never
re-derives, averages or looks up anything about the building: what HiSim used is what the certificate
is computed from (D25). Austria is specified to the clause and proven against ÖNORM B 8110-6-2;
Ireland is profiled from the DEAP manual at hand; Germany, Spain and the Netherlands are profiled
from what is known and filled in when their norms are procured (Q1). Tests split into functionality
tests that run everywhere and validation against the norms' own examples that runs where licensed
data exist. HiSim itself is not changed by this work.

## 4. Context and current situation

### 4.1 HiSim today (evidence)

- Every run writes `realized.energy_system.yaml` beside its results, the energy-system file with
  every preset expanded and every sized value written out, before the first time step
  (`hisim/energy_system/record.py`). For a `Building` the block is the configuration only: in the
  ai4c run `AT.N.MFH.03.Gen.ReEx.001.001__gas__TRY__R2__Z1__LL21__A1__S1` (HiSim 1.2.4) every
  envelope field is `null`. The envelope the run computed with is not in the record.
- HiSim builds its building in `BuildingInformation` from an archetype database (TABULA) and the
  configured overrides, and what it computes with is this: per element class (floor, wall, roof,
  window, door) **one** area, **one** U-value and **one** transmission adjustment factor b with the
  values 1, 0.5 or 0 (the archetype's sub-elements are merged by HiSim into that one element; a party
  wall to a neighbour may sit inside the wall element with b = 1 and an averaged U-value); window
  areas per direction (South, East, North, West, Horizontal); a glazing value g with frame,
  non-perpendicular and shading factors; a thermal-bridging surcharge; a conditioned floor area; a
  five-class thermal mass category; an apartment count, which for a scaled archetype is HiSim's own
  estimate (conditioned floor area / 92.1 m², truncated); a door whose area and U-value HiSim
  substitutes when the archetype states none. HiSim records per element what it uses and where it
  came from (`element_values`). It has no gross floor area and no gross volume; its ventilation uses
  the conditioned floor area times a room height.
- No output of HiSim writes that building out; the RenoVisor translator reads `element_values`
  internally. The record carries no country. The site (climate region, elevation) is being added to
  HiSim's weather configuration by the requester, outside this work, so this work assumes it in the
  realized record (D24); runs recorded before that change carry no site and are re-simulated.
- Package boundaries: `tests/test_renovisor_import_lint.py` forbids imports of `hisim.renovisor`
  from below it; the maintainers plan enforced packages with versioned, schema-checked files at
  every seam and name the EPC as the package separate from the start.
- Tests: `pytest.ini` has `testpaths = tests` and `--strict-markers`; CI runs `pytest -m base`.

### 4.2 The national methods

| | Austria | Ireland | Germany | Spain | Netherlands |
|---|---|---|---|---|---|
| Certificate | Energieausweis (OIB RL 6) | Building Energy Rating (BER) | Energieausweis (GEG) | Certificado de Eficiencia Energética | Energielabel |
| Method of the heating demand | ÖNORM B 8110-6-1, B 8110-5, H 5050-1, H 5056-1 (2024-03) | DEAP 4.2.7 (June 2025), SEAI; ISO 13790 monthly, SAP-derived | DIN V 18599, or DIN V 4108-6 with DIN V 4701-10 for residential | CTE DB-HE with the recognised procedures (CE3X simplified, HULC hourly) | NTA 8800 |
| Access | licensed (Austrian Standards), at hand | free, at hand (`oenorm_hwb_calculator/ireland/DEAP_Manual.pdf`) | licensed (DIN Media), to be procured; route to decide | free, to be obtained | free, to be obtained |
| Climate | reference climate (Annex A) **and** site climate from region and elevation | one national climate (Tab. 1a/1b), location-independent | one reference climate | climate zone by province and altitude | one national climate |
| Reference area of the indicator | BGF, gross, external dimensions; BF = 0.8 · BGF is the conditioned reference area | total floor area, internal dimensions | Gebäudenutzfläche A_N | useful floor area | gebruiksoppervlakte |
| Usage basis | dwelling-unit class (≤ 2 / 3–9 / ≥ 10), set temperature of Tab. 7 | floor area (occupancy formula), 21/18 °C schedule 56 h/week | 19 °C, per A_N | zone-dependent | per use area |
| Elements to unheated spaces | temperature-correction factors f (Tab. 3–5) | U adjusted by R_u (ISO 13789) | temperature factors F_x | similar | similar |
| Thermal bridges, default | Eq. (11a) on the conductances | y = 0.15 W/(m²K) × exposed area | ΔU_WB = 0.10 W/(m²K) | per procedure | per procedure |
| Heating season | monthly with f_h / d_Heiz | October–May | monthly | monthly | monthly |
| Headline indicator (later stage) | HWB; HEB, PEB, fGEE | primary energy per m², class A1–G | final and primary energy per A_N, class A+–H | non-renewable primary energy and CO₂, class A–G | BENG 1–3, class A++++–G |
| Validation oracle | ÖNORM B 8110-6-2 worked examples | the published DEAP Excel workbook and software | DIN V 18599 Beiblatt examples (licensed) | CE3X / HULC software | NTA 8800 examples |

The columns for Germany, Spain and the Netherlands state what is known to the author and are
confirmed when the documents are procured (Q1). The table shows the shape of the family: every
method is a monthly balance of losses against utilised gains, but climate source, reference area,
usage basis, correction mechanism and thermal-bridging default differ per country, and each method
needs inputs the building description does not state (Ireland: ventilation openings, living-area
fraction, thermal-mass category), for which each norm prescribes its own defaults.

**Austria, verified on the texts.** The 2025-10 editions revise the method (θ_i,h 22 → 20 °C, no
f_h, τ₀ 15 h); only the 2024-03 package is complete and the ai4c certificates were issued under it
(D5). B 8110-6-1:2024 Section 4, p. 8: surfaces against conditioned rooms of other buildings are not
part of the thermal envelope; the same section fixes BF = 0.8 · BGF for residential buildings, and
10.3.2 normalises to a gross storey height of 3.00 m. B 8110-6-1 8.3.1.2.2, p. 40 f.: F_S,h = 0.65 for
one and two dwelling units **and for terraced houses**, 0.50 for three to ten units, 0.40 above ten;
B 8110-5 Tab. 7 uses 3–9 and ≥ 10, so the two classifications place ten units differently, as
printed. Tab. 4 footnote a: a cellar counts as insulated below a U-value threshold of the ceiling.
B 8110-5 Eq. (4) with the month's days reproduces the HGT 22/14 of three real certificates exactly.

### 4.3 Current vs. required behavior

| | |
|---|---|
| **Current** | No certificate value exists for a HiSim run; the building the run computed with is not written anywhere. |
| **Required** | From a run's record, expanded by the calculator's tool, the calculator writes per building the certificate heating demand by the national method for exactly the building HiSim computed with, with terms, inputs, assumptions and provenance, as JSON; in batch one table and one summary. HiSim's outputs are unchanged. |
| **Assumptions** | see §9. |

## 5. Goals and non-goals

**Goals**

- G1 A standalone calculator in the repository, three steps over two dictionaries, that computes the
  certificate heating demand of the building a run computed with, by the method of the run's
  country. `[decided 2026-10-09, D11, D13]`
- G2 The calculator takes HiSim's building exactly as HiSim used it; it never re-derives, averages or
  looks anything up about the building, and it contains no knowledge of the archetype database
  HiSim builds from. `[decided 2026-10-11, D25]`
- G3 The calculator imports nothing from HiSim; the one tool that imports HiSim is the expansion
  script, outside the calculator. `[decided 2026-10-10, D15]`
- G4 Austria first, exactly by the norms; Ireland, Germany, Spain and the Netherlands as further
  engines on the same shell, added without touching the others. `[decided 2026-10-09, D12]`
- G5 One result dictionary for every country: a small common core, free national keys, every
  assumption an engine makes listed in the result. `[decided 2026-10-10, D14]`
- G6 Correctness two ways: functionality tests with hand-given values, committed; validation against
  the norms' own examples, local where the data are licensed. `[decided 2026-10-07, D6, D8]`
- G7 Batch over a results tree: thousands of runs, one table, one summary, no simulation. `[given]`
- G8 Nothing licensed is published; free norm data may be committed once the licence is read. `[decided 2026-10-07, D7]`

**Non-goals (stage 1)**, kept as the parking lot of §11.2

- Headline indicators beyond the heating demand (final or primary energy, CO₂, classes), and the
  Austrian hot-water loss chain. `[given]`
- Any change to HiSim; the maintainers may later adopt the expanded record as HiSim's own output. `[decided 2026-10-10, D15]`
- Resolving the site from a municipality or cadastral code. `[given]`
- A human-readable certificate document; a package of its own with `pyproject.toml` and a workflow of its own. `[decided 2026-10-10, D16, D18]`
- The 2025-10 Austrian edition package. `[decided 2026-10-07, D5]`

## 6. Use cases, examples and mockups

**UC1 — Expand and compute one Austrian run.** `python -m epc.tools.expand <results dir>` writes
`building.expanded.yaml` beside the record; `python -m epc run <results dir> --country AT --out
<dir>` writes `<dir>/<run path>/Building.epc.json` (Mockup A).

**UC2 — Batch over the ai4c results tree.** `python -m epc batch <results root> --country AT --out
<dir>`: one JSON per run and building, `epc_table.csv`, `epc_summary.md`; a refused run is listed and
the batch continues.

**UC3 — Validation (local data).** The SFH of ÖNORM B 8110-6-2:2024 Section 4 is handed to the
Austrian engine as a hand-written building description with its elements and their situations; the
twelve monthly values and the annual HWB of Table 4, cases 1 and 2, come out within half of the last
printed digit. The test code is committed, its data stay local.

**UC4 — Country without an engine.** `--country DE` before the German engine exists: refused naming
the implemented countries, nothing written.

**UC5 — Missing site.** `--country AT` with an expanded record whose `site` block lacks the climate
region or the elevation, or names an unknown region: refused naming the field and the admissible
values. (`--country IE` reads no site field: DEAP is location-independent.)

**UC6 — Missing norm tables.** No tables under `epc/data/at/`: refused naming the folder and the
data contract.

**UC7 — Record not expanded.** A results directory without `building.expanded.yaml`: refused
naming the expansion tool; the calculator never computes from the realized record's configuration.

**UC8 — Building HiSim could not describe for a certificate.** The expanded record states 0 dwelling
units (HiSim's estimate for a small scaled archetype), or a window area with no direction that has
an area (an archetype without window orientations), or an adjustment factor other than 1, 0.5, 0:
refused naming the value and, for the dwelling units, HiSim's `number_of_apartments` field.

**Mockup A — result JSON** (one per run and building; abridged; every entry under `indicators` and
`terms` has the shape `value, unit, cites, label?, note?`):

```json
{
  "schema": "epc-result/1",
  "run": {"record": "results/AT.N.SFH.04…/building.expanded.yaml", "building": "Building",
          "country": "AT", "engine": "at", "edition": "OENORM B 8110-6-1:2024-03 / B 8110-5:2024-03 / H 5050-1:2024-03 / H 5056-1:2024-03",
          "climate_basis": "reference+site", "calculator": "epc 0.1", "computed_at": "2026-10-11T10:12:00"},
  "indicators": {
    "heating_demand_reference_climate": {"value": 304.1, "unit": "kWh/(m2 a)", "label": "HWB_RK", "cites": "B 8110-6-1 Eq. (52), (64); H 5050-1 Eq. (9)"},
    "heating_demand_site_climate":      {"value": 287.6, "unit": "kWh/(m2 a)", "label": "HWB_SK", "cites": "B 8110-6-1 Eq. (53), (64)",
                                         "note": "stage 1: without recoverable hot-water losses (Q_TW,beh = 0)"},
    "indicator_reference_area":         {"value": 143.75, "unit": "m2", "label": "BGF", "cites": "B 8110-6-1 Sec. 4, BF = 0.8 BGF"},
    "heating_degree_days_22_14_site_climate": {"value": 3762, "unit": "Kd", "cites": "B 8110-5 Eq. (4)"}
  },
  "terms": {
    "annual":  {"transmission_losses_site_climate": {"value": 17890.2, "unit": "kWh/a", "cites": "B 8110-6-1 Eq. (29a)"},
                "transmission_conductance": {"value": 476.3, "unit": "W/K", "cites": "Eq. (10a), (11a)"}},
    "monthly": {"heating_demand_site_climate": {"unit": "kWh", "values": [4110.2, 3420.9, "…", 3890.1]}}
  },
  "building": {"conditioned_floor_area_in_m2": 115.0, "gross_volume_in_m3": null,
               "dwelling_units": {"value": 1, "configured": false},
               "elements": [{"name": "wall", "area_in_m2": 179.94, "u_value_in_watt_per_m2_per_kelvin": 0.25, "adjustment_factor": 1.0,
                             "situation": "OUTSIDE_AIR", "u_value_origin": "configured"}],
               "windows": [{"name": "south", "azimuth_in_degree": 0, "tilt_in_degree": 90, "area_in_m2": 3.35, "g_value": 0.65}]},
  "site": {"climate_region": "S/SO", "elevation_in_m": 356},
  "assumptions": [{"key": "gross_floor_area", "value": "BGF = conditioned floor area / 0.8", "source": "B 8110-6-1 Sec. 4"},
                  {"key": "gross_volume", "value": "V = BGF x 3.00 m", "source": "B 8110-6-1 10.3.2; the record states no volume"},
                  {"key": "thermal_bridges", "value": "default of B 8110-6-1 5.3.2 c), Eq. (11a)", "source": "method default, no catalogue"}],
  "provenance": {"record_schema": "building-expanded/1", "producer": "epc.tools.expand 0.1, HiSim 1.2.4",
                 "hisim_building_code": "AT.N.SFH.04.Gen.ReEx.001.001", "configured_fields": ["facade_u_value_in_watt_per_m2_per_kelvin"],
                 "tables": {"reference_climate_b8110_5_2024_annex_a.csv": "sha256:…"}}
}
```

**Mockup B — the site inside the expanded record** (passed through from HiSim's weather
configuration by the expansion tool; a Spanish climate zone takes the same place; an Irish record
has an empty block):

```yaml
# building.expanded.yaml, per building
site:
  climate_region: S/SO        # AT: one of W, NF, N, ZA, SB, S/SO, N/SO (OENORM B 8110-5:2024 Bild 1)
  elevation_in_m: 356         # AT
```

**Mockup C — batch table and summary**

```csv
run,building,country,engine,edition,heating_demand_reference_climate [kWh/(m2 a)],heating_demand_site_climate [kWh/(m2 a)],indicator_reference_area [m2],…
2026-10-01/results/AT.N.SFH.04…,Building,AT,at,OENORM …,304.1,287.6,143.75,…
```

```markdown
# EPC batch summary — 2026-10-11
runs found: 1 204 · computed: 1 198 · refused: 6
| run | building | error | message |
| 2026-10-01/results/AT.N.AB.05…__heatpump__… | Building | E-07 | dwelling units are 0 (HiSim's estimate); state number_of_apartments |
```

**Mockup D — command line**

```
python -m epc.tools.expand <results dir | results root>   # imports HiSim; writes building.expanded.yaml beside each record
python -m epc run   <results dir> --country <CC> --out DIR
python -m epc batch <results root> --country <CC> --out DIR
python -m epc countries                                   # the engines and their editions
```

## 7. Why it matters / cost of inaction

The ai4c validation compares HiSim with real Austrian certificates and with a stock dataset of about
28,000 buildings carrying certificate heating demands; the same comparison is wanted for the other
countries HiSim is used in. Only a certificate computed for exactly the building HiSim computed with
isolates the method difference (dynamic simulation vs. monthly norm balance) from every model
difference; a calculator that re-derived or corrected the building would mix the two again. A
calculator inside HiSim's postprocessing would tie five national norms to HiSim's internals; a
standalone tool on a written-out record is both the research instrument and the first package of the
maintainers' boundary plan.

## 8. Requirements

Functional unless marked. Austrian clause references are to the 2024-03 editions (D5).

**R1 — A standalone calculator inside the repository.** `[decided 2026-10-09, D11; 2026-10-10, D16]`
R1.1 It lives in `epc/` at the repository root and runs as `python -m epc` from there; no packaging
and no workflow of its own; through `pytest.ini` its `base` tests run in HiSim's existing base job (C3). R1.2 No module of the calculator imports `hisim`; a committed static
check enforces it, with `epc/tools/` and the tool's own test as the only exemptions. R1.3 Its tests
live under `epc/tests/`, apart from HiSim's, with the repository's markers. R1.4 It may use
third-party libraries the repository already depends on.

**R2 — Inputs.** `[decided 2026-10-10, D15, D17, D20; 2026-10-11, D24]`
R2.1 The building comes from the expanded record `building.expanded.yaml` that the calculator's
own tool `epc/tools/expand.py` writes beside a run's realized record by importing HiSim once; its
content is defined in `input_contract.md`; a results directory without it, or with another major
version, is refused naming the tool (UC7). R2.2 The site (climate region and elevation; Spain's climate
zone joins the block when its engine is specified) is a block of the expanded record, passed through from HiSim's weather
configuration; an engine reads the fields its method needs and refuses a missing or unknown value
naming the field and the admissible values (UC5); an engine without site fields ignores the block.
R2.3 The country is an explicit argument in ISO 3166-1 alpha-2. R2.4 One certificate per `Building`
of the record.

**R3 — Country-keyed engines, loud refusal.** `[decided 2026-10-09, D12]`
The country selects the engine from a registry; a country without one is refused before any file is
opened: "EPC calculation is not possible: no EPC engine is implemented for country 'DE' yet
(implemented: AT)." Adding a country adds one engine package and one data folder and touches no
other engine, the shell, the dictionaries or the report formats (R10).

**R4 — Scope of stage 1 and the result dictionary.** `[decided 2026-10-10, D14]`
R4.1 Every engine delivers the certificate heating demand under the national reference (or
national) climate, and under the site climate where the method defines one, per the national
reference area. R4.2 The result is one dictionary per run and building (Mockup A): `run`,
`indicators`, `terms` (`annual`, `monthly`), `building`, `site`, `assumptions`, `provenance`.
R4.3 Two indicator keys are the common core every engine fills: `heating_demand_reference_climate`
and `indicator_reference_area`; `heating_demand_site_climate` is present exactly when the method has
a site climate (`run.climate_basis` says which); every other key is the engine's own, added or
omitted without a change to the shell. R4.4 Every value the engine takes from its norm's defaults
because the building description does not state it (a reference area derived by a norm rule, a
volume, a thermal-bridging default, a ventilation default, a living-area fraction) is one entry of
`assumptions` with its source.

**R5 — The Austrian engine follows the norms exactly, applied to HiSim's building.** `[decided 2026-10-07, D2; 2026-10-11, D25]`

| | Quantity | Rule |
|---|---|---|
| R5.1 | Reference climate | B 8110-5 Annex A as printed. |
| R5.2 | Site climate | B 8110-5 Eq. (1) with Annex B; Eq. (2) with Annex D; Eq. (3) with Annex E; grid values exactly; an azimuth or tilt off the grid is refused in stage 1 (P11). |
| R5.3 | Reference area and volume | BGF = conditioned floor area / 0.8 (Section 4, BF = 0.8 · BGF); V from the record when it states a gross volume, else V = BGF × 3.00 m (10.3.2); both listed as assumptions. V enters the balance through the thermal capacity C = f_BW · V (Eq. (50)), hence τ and η, and the indicators ℓ_c (Eq. (1)) and LEK_T (Eq. (63b)). |
| R5.4 | Usage profile | B 8110-5 Section 8 by dwelling units (≤ 2, 3–9, ≥ 10): θ_i,h, n_L,hyg, q_i,h (Tab. 7, 9). |
| R5.5 | Transmission conductance | B 8110-6-1 Eq. (10a) over HiSim's elements, each with its area, its U-value and the factor f of Tab. 3–5 for its situation (R6.2), the row per situation being the engine's norm default (D30), listed as an assumption; a conditioned neighbour is outside the envelope (Section 4, p. 8) and enters neither L_T nor A; a cellar ceiling is "insulated" below the U-value threshold of Tab. 4 footnote a; thermal bridges by the default Eq. (11a) with its lower bound. |
| R5.6 | Ventilation conductance | Eq. (13) V_V = 0.8·BGF·2.6, Eq. (14), Eq. (15a/b) natural ventilation. |
| R5.7 | Losses | Section 7; Q_l = Q_T + Q_V. |
| R5.8 | Internal gains | Eq. (37) Q_i = q_i,h · BGF · 0.8 · t / 1000. |
| R5.9 | Solar gains | Eq. (43), (44) with A_g = 0.7·A_W, F_g = 0.9 × 0.98; F_S,h = 0.65 for ≤ 2 units **or a terraced house**, 0.50 for 3–10, 0.40 above (8.3.1.2.2); g from the building; window areas per direction as HiSim states them. |
| R5.10 | Thermal mass, utilisation | Eq. (50) C = f_BW · V with f_BW per Bauweise as stated after the equation, by HiSim's five classes mapped very light + light → leicht, medium → mittel, heavy + very heavy → schwer (D3); Eq. (49); Eq. (57a/b)–(59), a = 1 + τ/16. |
| R5.11 | Heating period, reference climate | H 5050-1 Eq. (9a)–(9m); γ ≤ 0 refused, not patched. |
| R5.12 | Heating period, site climate | B 8110-6-1 Eq. (53) with d_Heiz = f_H · MT_j from H 5056-1 Eq. (49)–(52), negative γ_H replaced by the next positive month; Q_TW,beh = 0 in stage 1, stated as a `note`. |
| R5.13 | Annual values | Eq. (51), (64) per BGF, (63a), (1), heating days, HGT 22/14 per B 8110-5 Eq. (4) with the month's days. |

**R6 — The building is HiSim's, taken as it is.** `[decided 2026-10-07, D9; 2026-10-11, D25, D26]`
R6.1 The calculator takes every element as HiSim used it: one area, one U-value, one adjustment
factor per element class; the window areas per direction, the glazing values, the conditioned floor
area, the dwelling units, the door, all as written in the expanded record. It never averages,
splits, scales, re-derives or looks up anything about the building, and it contains no knowledge of
the archetype database HiSim builds from. R6.2 An element's situation follows from HiSim's
adjustment factor and the element class: b = 1 is outside air; b = 0 is a conditioned neighbour;
b = 0.5 is an unconditioned space, which is the attic for a roof, the cellar for a floor and a
buffer space for a wall; any other factor refuses the run. R6.3 Dwelling units are the record's
value verbatim; 0 is refused naming HiSim's `number_of_apartments` (UC8). R6.4 Doors and windows
are single surfaces against outside air; the window element's area counts in the transmission, the
per-direction areas in the solar gains, each as HiSim states them; a building with window area but no
direction with an area is refused (UC8). R6.5 A quantity the record lacks refuses the run by name;
nothing is guessed in the reading step; what an engine assumes is an `assumptions` entry (R4.4).
R6.6 HiSim's origin strings, the configured overrides and HiSim's building code pass into
`provenance` unchanged and uninterpreted. R6.7 The calculator handles every value HiSim can write for a
field, regardless of how often it occurs; statistics of the archetype database are no criterion in
this specification or in its review.

**R7 — Output.** `[decided 2026-10-10, D18; 2026-10-11, D29]`
R7.1 Per run and building one `<run path>/<building>.epc.json` of the shape of Mockup A, the run
path relative to the results root mirrored under the output directory. R7.2 In batch mode `epc_table.csv` (one
row per run and building; columns `run, building, country, engine, edition`, then every indicator
key seen, unit in the header) and `epc_summary.md` (counts and every refusal). R7.3 Keys are
English, stable, snake_case; monthly series carry twelve values January to December. R7.4 The
calculator writes only into the output directory given, which must be empty or absent; it never
merges into existing output.

**R8 — Traceability and licensing.** `[decided 2026-10-07, D7; given: scientific standard]`
R8.1 Every constant in code and every table in data names norm, edition, clause/table and page.
R8.2 Norm tables and national defaults are data files with a source header under
`epc/data/<country>/`, never literals in code; the Austrian temperature-correction factors are such
a table and are not printed in any committed document. R8.3 Austrian tables and the examples' values
stay local, git-ignored; `epc/data/README.md` is the committed data contract. R8.4 For a country
whose norm is free of charge, the licence is read and the decision to commit its tables is recorded
in the register first. R8.5 No committed test contains a value from a licensed norm table. R8.6 A
run that needs tables and does not find them is refused naming the folder and the contract (UC6).
R8.7 Nothing in `epc/` imports, links or copies the prototype `oenorm_hwb_calculator/`.

**R9 — Two kinds of tests.** `[decided 2026-10-07, D8]`
R9.1 Functionality tests, marker `base`, committed, run everywhere: every equation with hand-given
values, the reading of fixture records, the situation rule, the registry, the report formats, the
command line, the refusals, the import boundary. R9.2 Validation, marker `epc_norm_data`, skipped
with a reason where the data are absent: for Austria the SFH and MFH of ÖNORM B 8110-6-2:2024
(variants 1 and 2 / 2.1, cases 1 and 2), monthly and annual, within half of the last printed digit
(the tables print two decimals, so 0.005 in the table's unit); larger residuals are defects to be
explained, the tolerance is widened only by the owner's decision. R9.3 The validation output
accompanies the pull request that lands the balance.

**R10 — Modularity.** `[decided 2026-10-09, D12]`
A country is one package `epc/engines/<cc>/` and one data folder `epc/data/<cc>/`, registered under
its code; the only thing the shell requires of an engine is `compute(building) -> result`; the
shell, the dictionaries and the reports are unchanged by adding one.

**R11 — The edition is pinned and stated.** `[decided 2026-10-07, D5]`
Each engine states its norm edition in `run.edition` and in its data headers; a later edition is a
second engine, not a switch.

**R12 — Batch behaviour.** `[decided 2026-10-10, D19]`
R12.1 `run` fails hard on the first error. R12.2 `batch` records a refused run in the summary and
continues; the exit code is non-zero when any run was refused. R12.3 The batch walks the results root
deterministically.

**R13 — HiSim is untouched by the calculator's pull requests.** `[decided 2026-10-10, D15; 2026-10-11, D24]`
No file under `hisim/`, `tests/`, `system_setups/` or `energy_systems/` changes in them; outside `epc/`
and `roadmap/epc/` only `pytest.ini` (test path, marker) and `.gitignore` (local data) change. The site
fields (D24) are the requester's separate change to HiSim.

## 9. Constraints, invariants and assumptions

**Known constraints**

- C1 The expansion tool imports HiSim and runs only where HiSim is installed; it refuses a record
  written by another HiSim version than the installed one. An unchanged version string does not
  prove an unchanged building derivation (Q2).
- C2 The Austrian norms and their tables are licensed (D7); the validation runs where they exist.
- C3 `pytest.ini` needs `epc/tests` in `testpaths` and the marker `epc_norm_data`.
- C4 Code and comments in English; line length 120; `flake8`, `mypy`, `pylint` clean.
- C5 HiSim's building has one element per class with one adjustment factor; whatever HiSim merged
  into an element (a party wall into the wall, two floors of different situations into the floor) is
  one element for the certificate, with HiSim's area, U-value and factor (D25).
- C6 Runs recorded before the site reached HiSim's weather configuration carry no site and are
  re-simulated; the first expanded real run is the end-to-end validation (design V-4).
- C7 The prototype `oenorm_hwb_calculator/` is inspiration only and is deleted after the Austrian
  validation passes (D4).

**Invariants**

- I1 Q_l,j = Q_T,j + Q_V,j for every month.
- I2 0 ≤ f_h,j ≤ 1; 0 < η_h,j ≤ 1; Q_h,j ≥ 0; HWB ≥ 0.
- I3 Reference- and site-climate results share every building quantity; only the climate differs.
- I4 BF = 0.8 · BGF; A_g = 0.7 · A_W; L_ψ + L_χ ≥ 0.1 · (L_e + L_u + L_g).
- I5 The building description read from an expanded record equals the record's values exactly.
- I6 An element against a conditioned neighbour contributes neither to L_T nor to A.
- I7 Every result carries the common-core indicators and every assumption the engine made.

**Assumptions (to confirm)**

- A1 The norm's conventions BGF = BF / 0.8 (Section 4) and V = BGF × 3.00 m (10.3.2) stand in for the
  gross floor area and the gross volume HiSim does not know; confirmed by the manual tests against
  real certificates (P4), where the certificate's printed BGF and V are compared.
- A2 Certificate software applies the thermal-bridge default of Eq. (11a).
- A3 The BGF and V of the SFH example of B 8110-6-2 follow from its printed dimensions (two storeys);
  confirmed by the validation.
- A4 The expanded record's field set suffices for the four other engines' heating-demand methods
  with their own defaults for what it does not state; confirmed as each norm is read (Q1).

## 10. Acceptance criteria

| ID | Criterion | Verifies |
|---|---|---|
| AC1 | An expanded Austrian run with country `AT` yields the JSON of Mockup A with every key of R4.2, the common core, the Austrian indicators and terms, and the assumptions list. | R2, R4, R7.1 |
| AC2 | The SFH and MFH examples of B 8110-6-2:2024 are reproduced monthly and annually within half of the last printed digit; runs where the tables exist, skips elsewhere; the output is attached to the PR. | R5, R9.2, I1–I4 |
| AC3 | `--country DE` is refused with the message of R3; nothing is written. | R3 |
| AC4 | An expanded record whose site block lacks a field the engine needs, or names an unknown value, is refused naming the field and the admissible values; `--country IE` runs with an empty site block. | R2.2 |
| AC5 | A results directory without `building.expanded.yaml`, or with another major version, is refused naming the tool. | R2.1 |
| AC6 | The static check finds no import of `hisim` outside `epc/tools/` and the tool's test, none of the prototype, and the string `TABULA` in no `.py` module under `epc/` except `tools/` (fixture records and results carry HiSim's origin strings verbatim, R6.6); no committed test holds a licensed value (review). | R1.2, R6.1, R8.5, R8.7 |
| AC7 | An expanded record read back equals the record's values exactly; a factor other than 1, 0.5, 0, a window area without a direction, and 0 dwelling units are refused by name. | R6, I5 |
| AC8 | A dummy engine for country "ZZ" registers, computes and reports without a change to the Austrian engine. | R3, R10, I7 |
| AC9 | A batch with one refused run writes the other JSONs, the table and a summary naming the refusal; exit code non-zero; a non-empty output directory is refused. | R7, R12 |
| AC10 | The expansion tool refuses a record of another HiSim version and writes, for three archetypes, exactly the attributes a freshly built `BuildingInformation` holds. | C1, contract |
| AC11 | A loader pointed at an empty folder refuses per R8.6; a synthetic incomplete table is refused naming the dimension. | R8.6 |
| AC12 | `pytest epc/tests -m base` is green without local data; HiSim's own tests are unchanged and green; no file under `hisim/` differs. | R9.1, R13 |

## 11. Open questions, parking lot and decisions

### 11.1 Open questions

**Q1 — Norms and oracles of Germany, Spain and the Netherlands.** · blocks the engines DE, ES, NL (R3, R10, R11) and R8.4 for them
*Context.* Austria's norms are at hand and Ireland's DEAP manual is at hand and free; the German
route (DIN V 18599 or DIN V 4108-6 with 4701-10) is itself a choice and both are licensed; CTE
(Spain) and NTA 8800 (Netherlands) are free but not yet obtained; oracles differ. *Options.* (a)
Procure all now and decide the German route before any further engine is designed. (b) Finish
Austria, profile Ireland next from the manual at hand, and procure each further norm when its engine
is scheduled. *Recommendation.* (b); each country's profile in §4.2 becomes an appendix of this
document when its norm is read. *Blocks.* Nothing of stage 1 for Austria or of the Irish profile.

**Q2 — Who writes the expanded building.** · blocks C1, T-13 and R-2 of the design; nothing of the engines
*Context.* The tool rebuilds `BuildingInformation` with the installed HiSim and checks the record's
HiSim version against it (D15). HiSim's version string does not change with every commit (1.2.4 since
2026-05-20), so an unchanged string does not prove an unchanged building derivation, and a committed
fixture record with a fixed version makes T-13 fail at every version step or pass falsely between
two. *Options.* (a) HiSim writes the block itself at run time, from the object in memory (P8 now): no
tool, no version check, no import exemption, no manual step; one HiSim pull request with the writer
and its test, through the maintainers' review. (b) Keep the tool: T-13 builds its record at test time
instead of reading a committed one, and C1 says only that the record is expanded with the installed
HiSim. *Recommendation.* (a), to be agreed with the maintainers, whose own view is that the realized
record should carry the full envelope; (b) until then. *Blocks.* The fixture of T-13 and the wording
of C1; nothing of the engines or the dictionaries.

### 11.2 Deferred items (parking lot)

| # | Item | Origin |
|---|---|---|
| P1 | Further Austrian values: WWWB, HEB, HTEB, EEB, PEB, CO₂, fGEE, KB | D1 |
| P2 | Site lookup from municipality or cadastral code (Austria), climate zone from municipality (Spain) | D10 |
| P3 | The 2025-10 Austrian edition package as a second engine | D5 |
| P4 | Manual tests against real certificates (Muggauberg, Ludersdorf, Kalsdorf) after merge; they confirm A1 and need P11 | D6 |
| P5 | The Austrian hot-water loss chain (H 5056-1 Ch. 6, H 5056-2 as oracle), reading the run's hot-water components from the record | D1 |
| P6 | Headline indicators and classes per country, which need the heating system from the record | D14 |
| P7 | A package of its own and a path-filtered CI job, later a repository of its own, once the record seam is stable | D16 |
| P8 | HiSim writes the expanded block itself (the maintainers adopt `input_contract.md`); the expansion tool retires; whether this comes first is Q2 | D15 |
| P9 | Mechanical ventilation with heat recovery (B 8110-6-1 6.2.3), MFH variants 2.2/2.3 of B 8110-6-2 | R9.2 |
| P10 | Release of licensed norm-derived data, if the publisher permits | D7 |
| P11 | Linear interpolation of the Austrian radiation tables for off-grid azimuths and tilts (B 8110-6-1 9.1), needed by the real certificates of P4 | R5.2 |
| P12 | A gross volume written by HiSim into the record, replacing the norm's standard assumption of R5.3 | D27 |

### 11.3 Decision register

| ID | Date | Decision |
|---|---|---|
| D1 | 2026-10-07 | Stage 1 is the heating demand only, for residential buildings. |
| D2 | 2026-10-07 | The Austrian engine reproduces the norms exactly. |
| D3 | 2026-10-07 | Thermal-mass classes map 5 → 3 (R5.10). |
| D4 | 2026-10-07 | The prototype is inspiration only, never imported, deleted after the Austrian validation passes. |
| D5 | 2026-10-07 | Austrian edition package 2024-03; 2025-10 is a later, second engine. |
| D6 | 2026-10-07 | ÖNORM B 8110-6-2 is the Austrian oracle, to half of the last printed digit; real certificates are manual tests after merge. |
| D7 | 2026-10-07 | No Austrian norm table or value is committed; formulas and the single constants of the prose clauses are. |
| D8 | 2026-10-07 | Functionality tests committed and run everywhere; validation against the norms' examples, local. |
| D9 | 2026-10-07 | Dwelling units are HiSim's count verbatim; 0 is refused naming `number_of_apartments`. |
| D10 | 2026-10-07 | The site is stated per run (climate region and elevation for Austria), never looked up by the calculator; one site per run. |
| D11 | 2026-10-09 | A standalone calculator in the HiSim repository, as detached as possible: no import from `hisim` in the calculator. |
| D12 | 2026-10-09 | Countries: Austria the pilot; Ireland, Germany, Spain, the Netherlands as further engines on one shell. |
| D13 | 2026-10-09 | Three steps: inputs, engine, report; the shell is those steps and the two dictionaries they exchange. |
| D14 | 2026-10-10 | Target of stage 1 is the heating demand by the national method under reference and, where the method has it, site climate; the result is a dictionary with a common core, free national keys and an assumptions list. |
| D15 | 2026-10-10 | The calculator's own tool `epc/tools/expand.py` imports HiSim once and writes the fully expanded building record; the calculator reads only that file. The maintainers may adopt the format later (P8). |
| D16 | 2026-10-10 | No `pyproject.toml` and no workflow of its own; the tests are separated under `epc/tests/` and, through `pytest.ini`, the `base` ones run in HiSim's existing base job. |
| D17 | 2026-10-10 | The country is an explicit ISO argument. |
| D18 | 2026-10-10 | Output: JSON per run and building, CSV and summary per batch; no certificate document. |
| D19 | 2026-10-10 | `run` fails hard; `batch` records refusals and continues. |
| D20 | 2026-10-10 | One certificate per `Building` of the record. |
| D21 | 2026-10-10 | Pull request #902 (the postprocessing-option design) is closed; this document set replaces it. |
| D22 | 2026-10-10 | Interpolation of the Austrian radiation tables is deferred (P11); stage 1 refuses off-grid angles, which no HiSim building and no validation example has. |
| D23 | 2026-10-10 | The common core is `heating_demand_reference_climate` and `indicator_reference_area`; `heating_demand_site_climate` exists exactly when the method has a site climate. |
| D24 | 2026-10-11 | The site (climate region, elevation; later a climate zone) is added to HiSim's weather configuration by the requester, outside this work, and travels through the expansion tool into the expanded record; the calculator has no site input of its own. A building at several sites is several realized records. |
| D25 | 2026-10-11 | The calculator computes the certificate of exactly the building HiSim computed with: one area, one U-value, one adjustment factor per element class, as HiSim used them; nothing is split, re-averaged or re-derived. It knows nothing of the archetype database HiSim builds from, and the certificate's deviation from a real certificate is a finding of the manual tests, not something the calculator corrects. |
| D26 | 2026-10-11 | An element's situation follows from HiSim's adjustment factor and the element class: 1 outside air, 0 conditioned neighbour, 0.5 an unconditioned space (attic for a roof, cellar for a floor, buffer space for a wall). |
| D27 | 2026-10-11 | The gross floor area is BF / 0.8 by the norm's own convention; the gross volume comes from the expanded record when it states one, else from the norm's standard storey height of 3.00 m; both are listed as assumptions. |
| D28 | 2026-10-11 | A building with window area but no direction that has an area (an archetype without orientations) is refused; the calculator does not invent an orientation. |
| D29 | 2026-10-11 | A batch writes into an empty output directory; it never merges into or cleans up existing output. |
| D30 | 2026-10-12 | A factor 0.5 is mapped by each engine to one row of its norm per element class, as the engine's default and an assumption of the result; Austria: floor → ceiling to an unconditioned cellar (Tab. 4, insulated below the footnote-a threshold), roof → ceiling to an unconditioned closed attic, wall → wall to another buffer space. How often a case occurs in HiSim's buildings is no criterion (R6.7). |

## 12. Glossary

- **Realized record** — `realized.energy_system.yaml`, written by every HiSim run; the configuration, not the computed building.
- **Expanded record** — `building.expanded.yaml`, written by `epc/tools/expand.py` beside the realized record: the building as HiSim computed with it, per `input_contract.md`.
- **Building description** — the calculator's country-neutral dictionary of one building (design §5).
- **Situation** — what an element borders on, from HiSim's adjustment factor and the element class (D26); each engine maps it to its correction (f, R_u, F_x).
- **Engine** — one country's method: data, defaults, computation, indicators.
- **Common core** — the indicator keys every engine fills (D23).
- **Assumptions** — the result's list of values an engine took from its norm's defaults because the building description does not state them.
- **BGF / BF / TFA / A_N** — Austrian gross floor area (external) / Austrian Bezugsfläche 0.8·BGF / Irish total floor area (internal) / German Gebäudenutzfläche.
- **Functionality test / validation** — committed, runs everywhere / local, against a norm's examples (D8).
