# Storeys, dwelling types and envelope geometry → hisim-9b0m

**Status:** design, not implemented. Draft of 2026-09-29; **owner decision (2026-09-30):** not built
now, tracked as bead **hisim-9b0m (P3)**. This file records a design for when the bead is picked up;
it promises nothing. **Until then:** `house.building.number_of_storeys` is `not_implemented_yet`, and a
bungalow (and an `other` building) is simulated as the detached (TABULA SFH) archetype with that
archetype's storeys and shape; the mapping report says both in words (renovisorissues #76 point 4:
a 1-storey bungalow and a 2-storey detached house of 1960 and 170 m² gave identical results).
PR #868's factor correction (roof/floor × n0/n, opaque wall × √(n/n0)) was rejected as a hack and
closed.

---

## 1. Why storeys matter

At a given conditioned floor area the storey count sets the shape: footprint (roof and ground floor)
versus facade. Roof, floor and wall have different U-values and b-factors, so the transmission loss
changes; the roof area also bounds PV. HiSim today scales the TABULA row's areas linearly to the
floor area, so the row's own `n_Storey` shape is always simulated.

## 2. TABULA's own geometry (reconstructed, verified on 403 generic rows)

The TABULA calculator's simplified envelope-area estimation; its intermediate columns are in
`episcope-tabula.csv` (separator `;`, latin-1, decimal comma). With A the conditioned floor area and
n the storeys:

    n_eff(n)   = n + 0.7*f_AtticCond + f_CellarCond      (attic C 1, P/PI 0.5; cellar C 1, P 0.5)
    n_env(n)   = n_eff(n) + (n_Storey_effective_envelope - n_Storey_effective)
    A_s        = A / n_eff(n)                             (A_C_Storey)
    Roof_est   = f_ComplexRoof*(p_Roof*A_s + q_Roof) + (p_Ceiling*A_s + q_Ceiling)
    Floor_est  = 1.2*A_s + 5
    Gross_est  = (0.7*A_s + q_nb)*f_ComplexFootprint*f_Corr_CeilingHeight*n_env(n)
                 q_nb: B_Alone 50, B_N1 25, B_N2 5 (the party wall is adiabatic)
    Window_est = 0.17*A - 1.5,   Door_est = 0.01*A + 1.5  (independent of n and neighbours)
    Wall_est   = Gross_est - Window_est - Door_est

This reproduces the stored columns within 0.11 m² (wall sum 1.4 m²). The rows' surveyed areas differ
from the estimate by ±30–60 % (median ratio ~0.95–1.2 per element), so the estimate gives the shape
and the row gives the calibration.

## 3. Proposed rule: calibrated estimate

Per element e, with Row_e(A) the Building's scaled row area and (n0, nb0) the row's `n_Storey` and
neighbours:

    k_e     = Row_e(A) / Est_e(A, n0, nb0)
    Roof    = k_roof * Roof_est(A, n);  Floor = k_floor * Floor_est(A, n)
    Gross   = k_gross * Gross_est(A, n, nb), k_gross = (Wall+Window+Door)_row / Gross_est(A, n0, nb0)
    Wall    = Gross - Window_row - Door_row;  Window, Door = the row's

- Identity at (n0, nb0) as a short-circuit, so the goldens stay bit-identical.
- U-values, b-factors, ventilation (h_room·A), thermal mass and internal surface (per A) and the
  window orientation split stay as they are.
- Net wall > 0 over all 725 SFH/TH rows, A 20–1000 m², n 1–6 (smallest net/gross 0.093); guarded
  anyway.

H_tr impact at constant A (W/K):

| Row | Case | Change against the row |
|---|---|---|
| IE.N.SFH.04, 170 m² | row 803.8 W/K; n=1 | reference |
| IE.N.SFH.04, 170 m² | n=2 | −4.4 % |
| IE.N.SFH.04, 170 m² | n=3 | +3.8 % |
| IE.N.SFH.06 | n=1 | +10.3 % |
| DE.N.SFH.03 (attic C) | n=1 | +9.4 % (PR #868 gave +27 %) |
| IE.N.TH.03 | n=1 | +20.2 % |

## 4. Where it lives (recommended)

In the `Building` component:

- `BuildingConfig.number_of_storeys: Optional[float]` and
  `attached_neighbours: Optional[AttachedNeighbours]` (NONE/ONE/TWO = B_Alone/B_N1/B_N2);
- a pure module `hisim/components/building/geometry.py` (`TabulaEnvelopeEstimate`, `DwellingShape`);
- `BuildingInformation` reshapes roof, floor and wall after scaling, skips stated areas, and records
  an origin per element (`element_areas()`), which `layers.ElementAreas` and
  `economics._area_origin` read. The area config fields stay `None` for derived areas.

Cost: a core change and a new Building source hash (solar-gains cache rebuild); the goldens are
unchanged, because the fields default to `None`.

## 5. Dwelling types

| `building_type` | Row | Neighbours | Storeys |
|---|---|---|---|
| `detached_sfh` | SFH | NONE | stated |
| `semi_detached_sfh` | TH | ONE | stated |
| `terraced_sfh` | TH | TWO (an end terrace is a semi) | stated |
| `bungalow` | SFH | NONE | 1 |
| `other` | SFH | — | stated |
| `apartment` | AB | — | **not applied** |

A flat keeps the block-average envelope share. Its real envelope depends on its position (top: roof ×
n_eff, floor 0; middle: neither; ground: floor × n_eff), which needs a new request field
`dwelling_position` (a spec change on renovisorissues). IE.N.AB.07, 80 m²: average 137.3 W/K, top
+18 %, middle −16 %, ground +31 %; applying storeys = 1 naively gives +64 %.

The rows disagree with the building types today: IE TH alternates B_N1/B_N2 by band, and 10 SFH rows
are attached.

## 6. Open owner decisions (when this is picked up)

| # | Question | Options (recommendation first) |
|---|---|---|
| D1 | Geometry rule | calibrated TABULA estimate (rec.) / pure estimate / own footprint model |
| D2 | Location | Building component (rec.) / translator |
| D3 | Neighbours from `building_type` | always (rec., announce the baseline changes) / only with storeys / never |
| D4 | Storey definition | TABULA's full conditioned storeys without attic or cellar (rec.) |
| D5 | The frontend pre-fills and always sends storeys: 2 (`dwelling.ts:38`, `calculation-request.ts:187`) | ask the frontend to send it only when confirmed — blocking before this ships (rec.) |
| D6 | A bungalow with storeys ≠ 1 | refuse as a contradiction (rec.) / the stated value wins / force 1 |
| D7 | Apartments | not applied, a conditional `not_implemented_yet` entry and a spec request for `dwelling_position` (rec.) |
| D8 | A per-storey floor-area floor (~20 m²/storey) | a refusal (rec.) / the wall guard only |
| D9 | The `approximated` flag | drop it for semi-detached, keep it for bungalow (rec.) |

## 7. Later beads

- Flat position (`dwelling_position`).
- Unheated neighbours (a party-wall b-factor).
- Explicit footprint inputs.
- The calibrated estimate replacing linear area scaling for every request.
- PV sizing on the real roof (`A_SolarPotential`) rather than `A_Roof` under a cold attic.
- A per-storey plausibility limit.

## 8. Validation plan

- A TABULA reproduction test over all rows.
- An identity test: the row's own shape equals no shape, bit-identical.
- A monotonicity and positivity grid.
- `buildingtest`: heating demand for 1 vs 2 storeys, and terraced vs semi-detached.
- Capability probes for `number_of_storeys` and `building_type`.
- The payload's `area_source` names the reshape.
