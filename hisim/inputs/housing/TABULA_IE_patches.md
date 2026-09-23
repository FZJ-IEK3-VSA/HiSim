# Patches to the TABULA table (`data_processed/episcope-tabula.csv`)

The table is reference data and is not edited, except by an owner decision recorded here. Each
patch says which rows and columns changed, from what to what, where the new values come from, and
why. The raw sources under `data_raw/` are unchanged, so re-running `process_data.ipynb` drops
every patch below; re-apply them after it.

## 2026-09-23 — IE detached houses, band 05 (1967–1977): the missing door

**Decision:** owner decision of 2026-09-20 to patch the row (HiSim bead `hisim-epc.18`), with the
values chosen with Noah on 2026-09-23.

**Why:** `IE.N.SFH.05.Gen.ReEx.001.001`, `.002` and `.003` carry no door at all: `A_Door_1`
empty, no door construction, `U_Actual_Door_1 = 0`. Every other Irish band (01–08) has one. The
Building guards the zero area, so the rows ran, but a 1970s detached house was simulated without
a door, and until HiSim #795 the RenoVisor translator skipped the band for band 06 (1978–1982),
a different building.

**Changed columns, all three rows:**

| Column | Before | After | Source |
| --- | --- | --- | --- |
| `A_Door_1`, `A_Calc_Door_1` | empty / `0,0` | `2,7` | the same row's `A_Estim_Door`, TABULA's own estimate |
| `Code_Door_1` | empty | `IE.Door.ReEx.01.01` | the existing door of every Irish band 01–08 |
| `U_Door_1` | empty | `3` | that construction's U-value |
| `R_Before_Door_1` | `0,00` | `0,33` | that construction, as in bands 04 and 06 |

**Existing state, `…001.001`:** `U_Measure_Door_1` `3,00`, `U_Actual_Door_1` `3,000`,
`H_Transmission_Door_1` `8,10` (= 3.0 × 2.7), as band 04 and 06's `001` rows.

**Refurbished, `…001.002` and `…001.003`:** the door is replaced as in bands 04 and 06's `002` /
`003` rows: `Code_Measure_Door_1` `IE.Door.PVC door.01`, `Code_MeasureType_Door_1` `Replace`,
`R_PredefinedMeasure_Door_1` `0,625`, `R_Measure_Door_1` `0,63`, `U_Measure_Door_1` `1,60`,
`U_Actual_Door_1` `1,600`, `H_Transmission_Door_1` `4,32` (= 1.6 × 2.7).

**Not changed:** the rows' totals (`h_Transmission`, `q_ht_tr`, `q_ht`, `q_h_nd`, …) are TABULA's
as published and do not include the door, about 8 W/K for the existing state. HiSim computes the
transmission itself from each element's U-value and area; the totals only feed the reported
"TABULA reference heating demand" KPI.

**Status:** TO BE REVIEWED by the physics owner, like every number the RenoVisor translator
decides (`hisim-epc.8`).
