# Data contract of the EPC engines

Each engine keeps its norm tables and national defaults as data files under `epc/data/<cc>/`, never
as literals in code (requirements R8.2). This file is the contract for those folders: per country
and file its name, content, columns, units, row count, citation and licence decision. An engine's
loader checks each file against the same columns and row counts and refuses a missing file (`E-11`),
a wrong row count (`E-12`) or a malformed header, cell, key or band (`E-19`), naming the file and,
where there is one, the line.

## Structure

```
epc/data/
  README.md            this contract (committed)
  <cc>/                one folder per country, named by its ISO 3166-1 alpha-2 code in lower case
    <table>.csv        one file per table family
    SOURCES.md         citation and SHA-256 of every file of the folder
```

A result lists the digests of the tables it was computed with under `provenance.tables`.

## CSV conventions

- UTF-8, comma as separator, dot as decimal mark, one header row of column names; read with the
  standard library's `csv`.
- The file starts with `#` lines stating the norm, its edition, the table, the page and the
  transcription method.
- File names state the norm, its edition and the tables they hold.
- Units are part of the column name where a column carries one.

## Licence rule

- A licensed norm's tables and values are never committed: the folder stays local and git-ignored,
  and only this contract is published (D7). The same holds for the expected values of the norm's
  worked examples. Formulas and the single constants of the prose clauses stay in code, with their
  citation.
- A norm free of charge is read for its licence first; the decision to commit its tables is
  recorded in the decision register of `roadmap/epc/requirements.md` before any file is committed
  (R8.4).
- No committed test contains a value from a licensed table (R8.5). The tests that read a licensed
  norm's data lie in `epc/tests/<cc>/reference_cases/`, local and git-ignored, test code included
  (D41); the data they read stay here.

## Austria (`at/`)

Licensed (Austrian Standards); local and git-ignored (`.gitignore`: `epc/data/at/`). The norms can be
purchased from Austrian Standards International (https://www.austrian-standards.at); the engine reads
their tables in the form this section states. Edition package
2024-03 (D5): OENORM B 8110-5:2024-03, B 8110-6-1:2024-03, B 8110-6-2:2024-03. The loader is
`epc/engines/at/data_tables.py` with `climate_tables.py`; its columns and row counts are the single
source of this section (I-6). Months are `1` (January) … `12` (December). Climate regions are the
codes of B 8110-5:2024 Bild 1: `W`, `NF`, `N`, `ZA`, `SB`, `S/SO`, `N/SO`. Values stand exactly as the
norm prints them: no rounding, no unit conversion, no interpolation, no value the norm does not print.

| File | Content | Rows |
|---|---|---|
| `reference_climate_b8110_5_2024_annex_a.csv` | B 8110-5 Annex A, Tab. A.1–A.7: the reference climate | 1 164 |
| `temperature_coefficients_b8110_5_2024_annex_b.csv` | B 8110-5 Annex B, Tab. B.1–B.7: coefficients of Eq. (1) | 252 |
| `radiation_coefficients_b8110_5_2024_annex_d.csv` | B 8110-5 Annex D, Tab. D.1–D.14: coefficients of Eq. (2) | 84 |
| `transposition_factors_b8110_5_2024_annex_e.csv` | B 8110-5 Annex E, Tab. E.1–E.7: transposition factors F_T of Eq. (3) | 5 292 |
| `usage_profiles_b8110_5_2024_tab7_tab9.csv` | B 8110-5 Tab. 7 and 9, the residential rows | 3 |
| `temperature_correction_factors_b8110_6_1_2024_tab3_5.csv` | B 8110-6-1 Tab. 3–5, one row per key below | 14 |
| `validation_examples_b8110_6_2_2024.csv` | B 8110-6-2 Sections 4 and 5: inputs and expected values of the worked examples, read only by the local tests in `epc/tests/at/reference_cases/` (D41) | – |
| `SOURCES.md` | per file: norm, edition, tables, pages, transcription, cross-checks, SHA-256 | – |

### `reference_climate_b8110_5_2024_annex_a.csv`

| Column | Unit | Meaning |
|---|---|---|
| `month` | – | 1 … 12 |
| `tilt_deg` | ° | tilt against the horizontal: `0` (Tab. A.1) or 15, 30, 45, 60, 75, 90 |
| `azimuth_deg` | ° | azimuth from south, east negative, west positive: −157.5 … 180 in steps of 22.5; empty for tilt 0 |
| `value_kwh_per_m2` | kWh/m² | monthly radiation sum on the surface |
| `theta_e_c` | °C | monthly mean outdoor temperature (Tab. A.1); only in the rows of tilt 0, empty elsewhere |

Where the norm prints one value for two mirrored orientations, the value stands in both rows, at
+azimuth (west) and −azimuth (east). Rows: 12 × (1 horizontal + 16 orientations × 6 tilts).

### `temperature_coefficients_b8110_5_2024_annex_b.csv`

| Column | Unit | Meaning |
|---|---|---|
| `region` | – | climate region code |
| `band` | – | elevation band as the table header prints it: `<=X`, `X<h<=Y` or `>X`, X and Y in m |
| `month` | – | 1 … 12 |
| `a`, `b` | – | coefficients of Eq. (1) as printed |

Three bands per region that follow one another without gap or overlap, the lowest open below, the
highest open above. Rows: 7 regions × 3 bands × 12 months.

### `radiation_coefficients_b8110_5_2024_annex_d.csv`

| Column | Unit | Meaning |
|---|---|---|
| `region` | – | climate region code |
| `month` | – | 1 … 12 |
| `a2`, `a1`, `a0` | – | coefficients of Eq. (2) as printed |

Rows: 7 regions × 12 months.

### `transposition_factors_b8110_5_2024_annex_e.csv`

| Column | Unit | Meaning |
|---|---|---|
| `band` | – | `E1` … `E7`, one per table |
| `elevation_above_m` | m | lower limit of the band, exclusive, as the table title prints it; empty when open |
| `elevation_up_to_m` | m | upper limit of the band, inclusive, as the table title prints it; empty when open |
| `month` | – | 1 … 12 |
| `azimuth_deg` | ° | 0 … 180 in steps of 22.5 (the norm prints ±; east and west share a value) |
| `tilt_deg` | ° | 0 … 90 in steps of 15 |
| `f_t` | – | transposition factor as printed |

Each band states one elevation range; the seven ranges follow one another without gap or overlap, open
at both ends. Rows: 7 bands × 12 months × 9 azimuths × 7 tilts.

### `usage_profiles_b8110_5_2024_tab7_tab9.csv`

| Column | Unit | Meaning |
|---|---|---|
| `class` | – | dwelling units of the row as `N-M`, or `N+` for the last class, open above |
| `theta_i_h_c` | °C | θ_i,h, Tab. 7 |
| `n_l_hyg_per_h` | 1/h | n_L,hyg, Tab. 7 |
| `q_i_h_w_per_m2` | W/m² | q_i,h, Tab. 9 |
| `wwwb_wh_per_m2_d` | Wh/(m² d) | wwwb, Tab. 9 |

The classes start at 1 dwelling unit and follow one another without gap or overlap.

### `temperature_correction_factors_b8110_6_1_2024_tab3_5.csv`

| Column | Unit | Meaning |
|---|---|---|
| `boundary` | – | the key of the row, one of the list below |
| `f_h`, `f_c` | – | f_i,h (heating case) and f_i,c (cooling case), between 0 and 1 |
| `table` | – | 3, 4 or 5 |
| `row_text` | – | the row text of the norm; where one key covers several printed rows with equal values, their texts joined by ` \| ` |

Keys: Tab. 3 `OUTSIDE_AIR`; Tab. 4 `WALL_TO_UNCONDITIONED_ATTIC`, `CEILING_TO_UNCONDITIONED_ATTIC`,
`CEILING_TO_CLOSED_GARAGE`, `WALL_TO_UNCONDITIONED_CELLAR_UNINSULATED`,
`CEILING_TO_UNCONDITIONED_CELLAR_UNINSULATED`, `WALL_TO_UNCONDITIONED_CELLAR_INSULATED`,
`CEILING_TO_UNCONDITIONED_CELLAR_INSULATED`, `WALL_TO_BUFFER_SPACE`, `CEILING_TO_BUFFER_SPACE`;
Tab. 5 `WALL_TO_GROUND_SHALLOW`, `FLOOR_ON_GROUND_SHALLOW`, `WALL_TO_GROUND_DEEP`,
`FLOOR_ON_GROUND_DEEP`. A conditioned neighbour is outside the thermal envelope and takes no factor
(B 8110-6-1:2024-03 Section 4, p. 8); it has no row.

### `validation_examples_b8110_6_2_2024.csv`

| Column | Unit | Meaning |
|---|---|---|
| `example` | – | `SFH` (Section 4) or `MFH` (Section 5) |
| `variant` | – | the variant as printed (`1`, `2`, `2.1`, …), `all` for object data that hold for every variant |
| `case` | – | `1` … `4` for a result row; `input:<parameter>` for an input row, the parameters listed in the file header |
| `month` | – | `1` … `12` or `annual` for a result row; empty for an input row |
| `value` | as printed | monthly Q_h in kWh/M, annual HWB in kWh/(m² a); inputs in the unit their parameter states |

Every result series has the twelve months and the annual value.
