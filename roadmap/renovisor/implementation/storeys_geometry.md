# Storeys, dwelling types and envelope geometry → epic hisim-4vho

**Status:** design with every decision taken (owner, 2026-10-06), not implemented. It replaces the draft
of 2026-09-29, whose geometry rule (a calibrated hybrid) and open decisions D1–D9 are superseded by §9.
The work is epic **hisim-4vho**; **hisim-9b0m** closes when the translator bead deletes the
`not_implemented_yet` entry. **Until then:** `house.building.number_of_storeys` is
`not_implemented_yet`, and a bungalow (and an `other` building) is simulated as the detached (TABULA
SFH) archetype with that archetype's storeys and shape; the mapping report says both in words
(renovisorissues #76 point 4).

---

## 1. The rule

**Nothing stated, nothing changes.** A request (or a `BuildingConfig`) that states no geometry input
gets today's areas: the TABULA row's roof, wall, floor, window and door areas, scaled linearly to the
conditioned floor area, bit-identical.

**Anything stated switches the areas to the estimate.** As soon as one geometry input is given —
`number_of_storeys`, `attic_condition`, `basement_condition`, `attached_neighbours`,
`footprint_complexity`, `roof_complexity`, or `building_type: bungalow` (which states one storey) — the
Building derives all five element areas from the published TABULA/EnergyProfile envelope-area
estimate, parameter set **EU.01** (§4). Inputs not stated take the row's own codes and are reported
as defaulted.

**Only areas.** U-values, b-factors, thermal bridging, air exchange, thermal mass and every system
still come from the TABULA row (or from the request). A stated element `area_in_m2` keeps winning for
its element, whichever method computed the others.

The cost of the rule is a step: confirming an input moves the result even when the confirmed value
equals the row's own (§8: Irish demand median −5 %, extremes ±20 %). The mapping report names the
method, and the frontend's help text says that confirming geometry changes it (hisim-4vho.2).

## 2. Sources

The method is not ours, and the earlier draft's "reconstruction" of it was a rediscovery:

| Source | What it contributes |
|---|---|
| T. Loga, N. Diefenbach, J. Knissel: *Kurzverfahren Energieprofil*, IWU Darmstadt 2005, Part I "Flächenschätzverfahren" (§3–§7) | The statistical basis: regressions of element area on living area per storey over 4016 German buildings, differentiated by attic and cellar state, neighbours, floor-plan type; the validation (H_tr standard deviation 15 %) |
| TABULA Calculation Method 2013, parameter set EU.01; re-estimated and validated in EPISCOPE ("TABULA Data Eval 2015") | The parameters HiSim's TABULA table carries (`A_Estim_*`, `p_Ceiling`, `f_AtticCond`, `f_ComplexFootprint`, …) and the version this spec uses |
| T. Loga, M. Großklos, A. Müller, S. Swiderek, G. Behem: *Realbilanzierung für den Verbrauch-Bedarf-Vergleich* (MOBASY), IWU 2021, ISBN 978-3-941140-67-7 | EU.01 as a worked sheet (Annex B.4, Bild 45/46), the indicator definitions (Annex B.1: `n_Storey`, `Code_AtticCond`, `Code_CellarCond`, `Code_AttachedNeighbours`, `Code_ComplexFootprint`, `Code_ComplexRoof`), a plausibility check of stated against estimated areas, uncertainty classes per input |

The two IWU reports are not vendored (copyright); they are cited.

## 3. Comparison of the approaches

| | **Draft 2026-09-29** | **Kurzverfahren Energieprofil 2005** | **TABULA EU.01 (MOBASY 2021)** | **This spec** |
|---|---|---|---|---|
| Basis | Reverse-engineered from the CSV | Regression on 4016 German buildings | 2005 reworked for TABULA, validated in EPISCOPE | EU.01, cited |
| Size variable | Conditioned floor area | Living area (gross floor = living / 0.75) | A_C,Ref; other area types converted | Conditioned floor area as A_C,Ref |
| Storeys | Stated | Input `n_VG`, full storeys | Input `n_Storey`, full storeys without attic and cellar | Input, MOBASY definition |
| Attic / cellar state | The row's | Inputs (0 / 50 / 100 % heated) | Inputs (`-`/N/P/C, NI/PI) | Inputs, else the row's |
| Neighbours | From `building_type` | Input 0/1/2 | Input N0/N1/N2, a neighbour counts if it covers >50 % of the wall | Explicit input, checked against `building_type` |
| Footprint / roof complexity | The row's | Compact / stretched; dormers × 1.3 | Simple / standard / complex: façade × 0.9/1.0/1.2, roof × 0.9/1.0/1.3 | Inputs, else the row's |
| Ceiling height | — | Façade × h/2.5 m | `f_Corr_CeilingHeight` | The row's (later: input) |
| Link to the row | Calibration k = row / estimate per element | None | None | None (pure estimate) when stated; the row itself when not |
| Windows, doors | The row's | 0.20 · living area | 0.18 · A − doors; doors 0.01 · A + 1.5 | Estimated |
| Stated areas | Win | — | Plausibility-checked against the estimate | Win, noted outside 0.5–2.0 × the estimate |
| Uncertainty | — | Validated: 15 % SD of H_tr | Classes A–E per input, propagated | Later bead |
| Apartments | Not applied | Building level only | Building level only | Not applied |

## 4. The method: TABULA EU.01

With A the conditioned floor area (A_C,Ref) and n the full storeys:

    n_eff     = n + 0.7·f_attic,cond + f_cellar,cond          f: unheated 0, partly 0.5, heated 1
    n_env     = n + 0.7·f_attic,env  + f_cellar,env           (the row: n_eff + (n_Storey_effective_envelope − n_Storey_effective))
    A_s       = A / n_eff                                     conditioned area per storey
    roof      = f_cx,roof · (p_roof · A_s + q_roof)            roof plane
    ceiling   = p_ceil · A_s + q_ceil                          top ceiling
    gross     = (0.70 · A_s + q_nb) · f_cx,footprint · f_ceiling_height     per storey; q_nb 50 / 25 / 5 for 0 / 1 / 2 neighbours
    wall_soil = 0.5 · f_cellar,env · gross
    door      = 0.01 · A + 1.5
    window    = 0.18 · A − door
    wall      = n_env · gross − wall_soil − window − door      to outside air
    floor     = 1.20 · A_s + 5

| Attic code | roof p / q | ceiling p / q |
|---|---|---|
| `-` flat roof | 1.2 / 5 | — |
| `N` unheated | — | 1.2 / 5 |
| `P` partly heated | 0.8 / 7 | 0.6 / 3 |
| `C` heated | 1.6 / 15 | — |
| `NI`, `PI` (not or partly heated, envelope in the roof plane) | 1.6 / 15 | — |

Complexity factors: roof simple 0.9, standard 1.0, complex 1.3 (simple does not apply to a flat
roof); footprint simple 0.9, standard 1.0, complex 1.2. The party wall to a neighbour is adiabatic.

HiSim aggregates per element: **roof** = roof plane + top ceiling, **wall** = to outside air + to soil,
**floor**, **window** (split by the row's orientation shares), **door**. The script
`storeys_geometry/eval_areas.py` implements exactly this and reproduces TABULA's own `A_Estim_*`
columns at every row's area and codes within 0.09 m².

## 5. Inputs

| Request field (house.building) | BuildingConfig field | TABULA code | Values |
|---|---|---|---|
| `number_of_storeys` (exists) | `number_of_storeys` | `n_Storey` | 1–6; full storeys without attic and basement, even when lived in |
| `attic_condition` (new) | `attic_condition` | `Code_AtticCond` | unheated → N, partly_heated → P, heated → C; only with `roof.shape: pitched` |
| `basement_condition` (new) | `cellar_condition` | `Code_CellarCond` | none → `-`, unheated → N, partly_heated → P, heated → C |
| `attached_neighbours` (new) | `attached_neighbours` | `Code_AttachedNeighbours` | 0 → B_Alone, 1 → B_N1, 2 → B_N2 |
| `footprint_complexity` (new) | `footprint_complexity` | `Code_ComplexFootprint` | simple / standard / complex |
| `roof_complexity` (new) | `roof_complexity` | `Code_ComplexRoof` | simple / standard / complex; only with a pitched roof |
| `building_type: bungalow` | `number_of_storeys = 1` | | implies one storey |

Not stated: the row's own code (`NI`/`PI` are reachable only that way). The floor area the estimate
scales with is `absolute_conditioned_floor_area_in_m2`, read as A_C,Ref; `living_area_in_m2` stays an
economics input. `building_type` keeps choosing the row (SFH or TH) for U-values and systems.

The new request fields are a contract change (hisim-4vho.1, filed on renovisorissues by hand-off);
the frontend must stop pre-filling storeys (hisim-4vho.2): today it always sends
`number_of_storeys: 2`, which under §1 would switch every request to the estimate.

## 6. Checks

Refused (exit 2):

- a bungalow with `number_of_storeys` ≠ 1;
- `attached_neighbours` contradicting `building_type`: detached and bungalow need 0, semi-detached 1,
  terraced 1 or 2 (an end terrace has 1); `other` takes any;
- `attic_condition` or `roof_complexity` with `roof.shape: flat` (a flat roof has no attic);
- a conditioned area per storey A / n_eff below 20 m² (the 2005 plausibility floor); the Building
  also refuses a net wall ≤ 0.

Noted in the mapping report, the run proceeds:

- the geometry method (`tabula_row` or `tabula_eu01_estimate`) and every geometry input as stated or
  defaulted from the row;
- the roof (floor) U-value as **approximated** when the stated attic (cellar) state differs from the
  row's and the request states no U-value for that element: the row's U-value describes its own top
  (bottom) element, and TABULA fills Roof_1/Roof_2 inconsistently (73 of 83 unheated-attic rows keep
  the top ceiling in Roof_1; only 29 state a second roof U-value), so there is no reliable U-value for
  the other element;
- a stated element `area_in_m2` outside 0.5–2.0 × the estimate (the stated area wins);
- A / n_eff above 300 m² per storey;
- for an apartment, that the geometry inputs are not applied.

## 7. Where it lives

In the `Building` component, so plain HiSim users get it too:

- `BuildingConfig` gains the six optional fields of §5, all `None` by default; all `None` is the row
  path, bit-identical, so goldens do not change.
- A pure module `hisim/components/building/geometry.py` holds the EU.01 parameters and the estimate.
- `BuildingInformation` computes the element areas from the estimate when any field is set, after
  the conditioned-floor-area scaling; a configured `*_area_in_m2` wins per element.
- `element_areas()` records an origin per element (`tabula_row_scaled`, `tabula_eu01_estimate`,
  `configured`), which `layers.ElementAreas` and the economics' area origin read; the stated or
  defaulted state of each input is exposed for the mapping report.

Cost: a core change and a new Building source hash (solar-gains cache rebuild).

## 8. Evaluation: row scaling against the estimate

Run `storeys_geometry/eval_areas.py` and `storeys_geometry/eval_runs.py` (both write only to the
output directory given). The estimate uses each row's own codes, so these numbers are the step a user
takes by confirming geometry that matches the row; all 223 national SFH/TH rows (variant 001, 17
countries) at 150 m², H_tr as HiSim computes it (area-weighted U × the element's largest b, plus
thermal bridging).

| Estimate vs row scaling | p5 | p25 | median | p75 | p95 | beyond ±20 % |
|---|---|---|---|---|---|---|
| H_tr | −36 % | −17 % | −1 % | +10 % | +36 % | 36 % of rows |
| roof | −42 % | −9 % | +5 % | +18 % | +66 % | 39 % |
| wall | −58 % | −25 % | +3 % | +28 % | +84 % | 60 % |
| floor | −41 % | −12 % | +7 % | +20 % | +54 % | 43 % |
| window | −48 % | −27 % | −13 % | +6 % | +41 % | 48 % |

Median ΔH_tr by country: SE −29 %, RS −27 %, CY −26 %, AT −21 %, BG −15 %, FR −8 %, IE −5 %, CZ −2 %,
HU −2 %, DK −1 %, PL +2 %, DE +5 %, BE +6 %, NO +9 %, SI +12 %, NL +15 %. Much of the spread is rows
whose codes contradict their survey: every Austrian row coded attached has detached-sized walls
(about 250 m²), which the estimate cuts to 88–128 m² (hisim-4vho.5).

Energy demand, full-year RenoVisor runs (gas boiler, no measures, 150 m²) on the 19 Irish rows, the
same request once without and once with the estimated areas stated:

| | p5 | p25 | median | p75 | p95 | min / max |
|---|---|---|---|---|---|---|
| ΔH_tr | −21 % | −10 % | −5 % | +2 % | +9 % | −24 / +27 % |
| Δ heating demand | −17 % | −8 % | −5 % | +1 % | +6 % | −20 / +18 % |
| Δ space-heating gas | −17 % | −8 % | −5 % | +1 % | +6 % | −19 / +16 % |
| Δ heating load | −18 % | −9 % | −5 % | +2 % | +7 % | −19 / +16 % |

Demand moves at about 0.76 × ΔH_tr. The Dutch rows could not run: every NL calculation exits 5 for
want of NL cost data (hisim-wec7).

The effect the feature is for, under the estimate at 150 m²: one storey against two raises H_tr by
21 % (median; SFH +12 %, TH +28 %; p5–p95 +1…+44 %), three against two lowers it by 2 %. Because the
method step (IQR −17…+10 %) is as large as the storey effect, the estimate is used only when geometry
is stated (D3), never as a silent replacement for the row.

## 9. Decision register (owner, 2026-10-06)

| # | Question | Decision |
|---|---|---|
| D1 | Geometry rule | The pure EU.01 estimate, not the calibrated hybrid of the draft |
| D2 | Location | The Building component (§7) |
| D3 | Scope | The estimate only when a geometry input is stated; the row's linear scaling otherwise, for RenoVisor and plain HiSim alike (decided on the numbers of §8) |
| D4 | Storey definition | Full storeys without attic and basement, even when lived in (MOBASY `n_Storey`) |
| D5 | The frontend's pre-filled storeys: 2 | The frontend sends geometry only when the user confirmed it, with the wording of §5; blocking before this ships (hisim-4vho.2) |
| D6 | Bungalow with storeys ≠ 1 | Refused; a bungalow alone states one storey and so triggers the estimate |
| D7 | Apartments | Not applied; a conditional `not_implemented_yet` entry; `dwelling_position` later (hisim-4vho.6) |
| D8 | Floor area per storey | Refused below 20 m², noted above 300 m² |
| D9 | The `approximated` flag | Dropped for semi-detached, terraced and bungalow; kept for `other` and apartments |
| D10 | Inputs | Storeys, attic state, basement state, attached neighbours, footprint and roof complexity |
| D11 | Inputs not stated | The row's own codes, reported as defaulted |
| D12 | Area basis | The conditioned floor area as A_C,Ref |
| D13 | Neighbours | An explicit `attached_neighbours` field; hard contradictions with `building_type` refused |
| D14 | Windows and doors | Estimated too |
| D15 | Stated element areas | Win; outside 0.5–2.0 × the estimate a mapping-report note |
| D16 | Attic and roof shape | A flat roof has no attic; `attic_condition` or `roof_complexity` with a flat roof is refused |
| D17 | Row choice | Unchanged: `building_type` picks SFH or TH |
| D18 | U-value on a changed attic/cellar state | The row's U-value, marked approximated unless the request states one |
| D19 | Uncertainty of the estimate | Later (hisim-4vho.7) |

## 10. Dwelling types

| `building_type` | Row | Neighbours | Storeys | Estimate |
|---|---|---|---|---|
| `detached_sfh` | SFH | 0 if stated | stated or the row's | when any input is stated |
| `semi_detached_sfh` | TH | 1 if stated | stated or the row's | when any input is stated |
| `terraced_sfh` | TH | 1 or 2 if stated | stated or the row's | when any input is stated |
| `bungalow` | SFH | 0 if stated | 1 | always |
| `other` | SFH | any if stated | stated or the row's | when any input is stated |
| `apartment` | AB | — | — | never (D7) |

The rows disagree with the building types today (IE TH alternates B_N1/B_N2 by band; 10 SFH rows are
coded attached); with an explicit `attached_neighbours` the stated value wins over the row's code.

## 11. Work

| Bead | What | Priority |
|---|---|---|
| hisim-4vho | Epic | P3 |
| hisim-4vho.1 | Hand-off: file the contract issue on renovisorissues (the new request fields and the storey definition, text in the bead) | P0 until filed, then P3 external |
| hisim-4vho.2 | Hand-off: file the frontend issue on renovisorissues (send geometry only when confirmed, wording) | P0 until filed, then P3 external |
| hisim-4vho.3 | The Building: config fields, `geometry.py`, origins, guards, tests | P3 |
| hisim-4vho.4 | The translator: mapping, refusals, mapping report, probes, the `not_implemented_yet` entry; blocked by .1–.3 | P3 |
| hisim-4vho.5 | TABULA rows whose codes contradict their surveyed areas | P3 |
| hisim-4vho.6–.9 | Later: `dwelling_position`; the uncertainty band; PV on the roof plane; ceiling height, footprint input and unheated neighbours | P4 |
| hisim-9b0m | The `not_implemented_yet` entry; closes with .4 | P3 |

## 12. Tests

- `geometry.py` reproduces TABULA's `A_Estim_*` columns over every row (the evaluation gets 0.09 m²).
- Identity: all geometry fields `None` gives today's areas, bit-identical.
- A monotonicity and positivity grid over storeys 1–6, every attic, cellar and neighbour code, A
  20–1000 m²: net wall > 0 wherever A / n_eff ≥ 20 m².
- `buildingtest`: heating demand for 1 against 2 storeys, and 1 against 2 neighbours.
- The translator's refusals and notes of §6, and capability probes for every new field.
- The payload's area origin names the estimate.

## 13. Later

- The position of a flat in its block (`dwelling_position`, hisim-4vho.6).
- A demand band from the estimate's uncertainty (hisim-4vho.7).
- PV and solar thermal on the roof plane rather than the top ceiling (hisim-4vho.8).
- Ceiling height, the footprint area as the starting point (GIS), unheated neighbours (hisim-4vho.9).
