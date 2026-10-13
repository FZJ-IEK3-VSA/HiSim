# Data contract of the EPC engines

Each engine keeps its norm tables and national defaults as data files under `epc/data/<cc>/`, never
as literals in code (requirements R8.2). This file is the contract for those folders: per country
and file its name, content, columns, units, row count, citation and licence decision. An engine's
loader checks each file against the same columns and row counts and refuses a missing file (`E-11`)
or an incomplete table (`E-12`), naming the folder and this contract.

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

Licensed (Austrian Standards); local and git-ignored (`.gitignore`: `epc/data/at/`). Edition package
2024-03 (D5).

| File | Content |
|---|---|
| `reference_climate_b8110_5_2024_annex_a.csv` | ÖNORM B 8110-5:2024 Tab. A.1, A.2, A.3–A.7 |
| `temperature_coefficients_b8110_5_2024_annex_b.csv` | ÖNORM B 8110-5:2024 Tab. B.1–B.7 |
| `radiation_coefficients_b8110_5_2024_annex_d.csv` | ÖNORM B 8110-5:2024 Tab. D.1–D.14 |
| `transposition_factors_b8110_5_2024_annex_e.csv` | ÖNORM B 8110-5:2024 Tab. E.1–E.7 |
| `usage_profiles_b8110_5_2024_tab7_tab9.csv` | ÖNORM B 8110-5:2024, the residential rows of Tab. 7 and 9 |
| `temperature_correction_factors_b8110_6_1_2024_tab3_5.csv` | ÖNORM B 8110-6-1:2024, the rows of Tab. 3–5 the situations map to, with the footnote-a threshold of Tab. 4 |
| `validation_examples_b8110_6_2_2024.csv` | ÖNORM B 8110-6-2:2024 Sections 4 and 5: inputs and expected values of the worked examples |
| `SOURCES.md` | citation and SHA-256 of each file |

Columns, units and row counts of each file are stated here together with the Austrian loader that
checks them; the loader's numbers are the single source (I-6).
