# Implementation specification: the EPC calculator shell and the Austrian pilot

**Status:** draft (in refinement with the requester)
**Date:** 2026-10-13
**Implements:** `roadmap/epc/requirements.md` (draft of 2026-10-12; Q1 open, blocking nothing of stage 1) and `roadmap/epc/input_contract.md`
**Author(s):** Nicolai Gölz, drafted with Claude · **Reviewers:** Nicolai Gölz; HiSim maintainers on the PR
**Branch / PRs:** `897-epc-tool-spec` for the documents; the sequence of §10 as they open
**What a reviewer must decide here:**

1. The shell is three steps over two dictionaries, laid out as three folders (`inputs/`,
   `engines/`, `reports/`) plus the dictionaries (`model/`) and the data (`data/`); nothing in the
   shell knows a norm, nothing in an engine knows a file (§3, §5.1).
2. The building description is HiSim's building as HiSim computed with it: one element per class
   with area, U-value and adjustment factor, windows per direction, glazing, conditioned floor area,
   dwelling units, site. The calculator derives nothing about the building and knows nothing of
   where HiSim got it (§3.3, §5.2, D24).
3. An element's situation comes from HiSim's factor and the element class, three rules (§3.3, D25).
4. The result dictionary has a common core, free national keys and an assumptions list; every norm
   default an engine applies is visible there (§3.4, §5.3).
5. HiSim writes its building out (`building.expanded.yaml`, D30); nothing under `epc/` imports
   HiSim (§3.3, §3.5).
6. Three pull requests for the shell and the Austrian pilot, after the documents (§10).

Status tags as in the requirements: `[proposed]`, `[decided YYYY-MM-DD]`; decisions are cited by
their register ID (requirements §11.3, D1–D39).

---

## 1. Summary

The calculator reads an expanded building record, hands one country-neutral building description
to the engine of the run's country, and writes the engine's result dictionary. That is the whole
shell; its folders mirror the three steps. The description is HiSim's building, nothing more and
nothing less: the calculator does not average, split, scale or look up anything (D24). An engine is a
package with its norm tables and norm defaults as data; it fills the result with the common core and
its own keys. The Austrian engine is the pilot, a direct transcription of the 2024-03 ÖNORM package
validated against B 8110-6-2; the Irish engine is profiled from the DEAP manual so that the shell is
shaped by two concrete methods; Germany, Spain and the Netherlands are slots of the same shape. New is
the folder `epc/` with everything in it; changed in HiSim are two lines of `pytest.ini` and one of
`.gitignore`. The non-obvious places are the three-line situation rule (§3.3), the two quantities
HiSim does not know and the norm supplies by its own conventions (gross floor area, gross volume,
§3.4), and the assumptions list that makes every engine default visible in the result.

## 2. Requirements coverage matrix

| Req / AC | Design element (§ ref) | Code location (planned) | Test (§9) | Status |
|---|---|---|---|---|
| R1.1 | §5.1 layout, `python -m epc` | `epc/__main__.py` | T-9 | planned |
| R1.2 | §3.5 import boundary | `epc/tests/test_import_boundary.py` | T-10 | planned |
| R1.3 | §5.1 `epc/tests/`, `pytest.ini` | `pytest.ini` | T-12 | planned |
| R2.1 | §3.3 reading; §8 E-02…E-05 | `epc/inputs/record.py` | T-3 | planned |
| R2.2 | §3.3 site block; §8 E-09, E-10 | `epc/inputs/record.py`, `epc/engines/<cc>/method.py` | T-3, T-9 | planned |
| R2.3, R2.4 | §3.2 | `epc/__main__.py`, `epc/inputs/record.py` | T-3, T-9 | planned |
| R3 | §3.2 registry; §8 E-01 | `epc/engines/registry.py` | T-7, T-9 | planned |
| R4.1 | §3.4 engine output | `epc/engines/at/indicators.py` | V-1, V-2 | planned |
| R4.2, R4.3 | §5.3 `EpcResult`, common core (I-7) | `epc/model/result.py` | T-2, T-7 | planned |
| R4.4 | §3.4 assumptions | `epc/model/result.py`, `epc/engines/*/defaults.py` | T-2, T-5 | planned |
| R5.1–R5.13 | §3.4 the Austrian engine; §7.1 tables | `epc/engines/at/*.py` | T-5, V-1, V-2, V-3 | planned |
| R6.1–R6.7 | §3.3 reading rules, situation rule; §7.2; §8 E-05…E-08 | `epc/inputs/record.py` | T-3, T-4 | planned |
| R7.1–R7.4 | §5.5 files; §3.2 batch; §8 E-16 | `epc/reports/*.py`, `epc/batch.py` | T-8, T-9 | planned |
| R8.1–R8.7 | §7.1 data folders, contract, headers; §3.5 | `epc/data/README.md`, `.gitignore`, `test_import_boundary.py` | T-6, T-10, V-5, review | planned |
| R9.1–R9.3 | §9 | `epc/tests/` | T-1 … T-12, V-1 … V-5 | planned |
| R10 | §3.4 engine protocol, registry | `epc/engines/base.py`, `registry.py` | T-7 | planned |
| R11 | §5.3 `run.edition`; §7.1 headers | `epc/engines/at/method.py` | T-8, V-5 | planned |
| R12.1–R12.3 | §3.2 batch; §8 policy | `epc/batch.py` | T-9 | planned |
| R13 | §10 | — | T-11 | planned |
| AC1 | §3.2, §5.3, §5.5 | `epc/tests/test_cli.py` | T-9, V-4 | planned |
| AC2 | §9 | `epc/tests/test_at_validation.py` | V-1, V-2 | planned |
| AC3, AC4 | §8 E-01, E-09, E-10 | `epc/tests/test_cli.py` | T-9 | planned |
| AC5 | §8 E-02, E-04 | `epc/tests/test_reading.py` | T-3 | planned |
| AC6 | §3.5, §9 | `epc/tests/test_import_boundary.py`, review | T-10 | planned |
| AC7 | §3.3, §8 E-06…E-08 | `epc/tests/test_reading.py` | T-3, T-4 | planned |
| AC8 | §3.2 registry | `epc/tests/test_registry.py` | T-7 | planned |
| AC9 | §3.2 batch, §5.5, §8 E-16 | `epc/tests/test_cli.py` | T-9 | planned |
| AC10 | §5.1 fixtures | `epc/tests/fixtures/`, `epc/tests/test_reading.py` | T-3 | planned |
| AC11 | §8 E-11, E-12 | `epc/tests/test_at_data_tables.py` | T-6 | planned |
| AC12 | §10 | base suite, `git diff` on `hisim/` | T-11, T-12 | planned |
| D-R1 `[proposed]` | A negative gain-loss ratio under the Austrian reference climate is refused (H 5050-1 Eq. (9) states no rule); from R5.11. | `epc/engines/at/balance.py` | T-5 | planned |

## 3. Design overview

### 3.1 The three steps and what flows between them

```
 STEP 1  INPUTS                          STEP 2  ENGINE                        STEP 3  REPORT
 epc/inputs/                             epc/engines/<cc>/                     epc/reports/
 ┌──────────────────────────┐            ┌────────────────────────────┐        ┌──────────────────────┐
 │ building.expanded.yaml   │            │ data_tables  (norm tables) │        │ json_report.py       │
 │   (written by HiSim      │  Building  │ defaults     (norm defaults│  Epc   │   <run>/<b>.epc.json │
 │    beside the record;    │─Description│   for unstated inputs)     │─Result─│ table.py             │
 │    site block included)  │            │ climate → usage → envelope │        │   epc_table.csv      │
 │ --country                │            │ → gains → balance          │        │ summary.py           │
 └──────────────────────────┘            │ → indicators               │        │   epc_summary.md     │
                                          └────────────────────────────┘        └──────────────────────┘
                     the two dictionaries live in epc/model/: building.py, result.py
```

Step 1 knows the file and nothing of any norm. Step 2 knows one norm and no file. Step 3 knows the
result dictionary, no norm and no file format of HiSim.

### 3.2 Control flow

```
HiSim run (outside the calculator)
  writes realized.energy_system.yaml and building.expanded.yaml (input_contract.md) before the first time step

python -m epc run <results dir> --country AT --out <dir>
  engine   = engines.registry.for_country("AT")                                  E-01
  engine.load_data()                                                             E-11, E-12 (once per process)
  record   = inputs.record.load(results_dir)                                     E-02 (no expanded record), E-03 (no building), E-04 (version)
  for entry in record.buildings:                                                 one certificate per building (D19)
      building = inputs.record.describe(entry)                                   E-05 (field), E-06 (factor), E-07 (0 units), E-08 (no orientation)
      result   = engine.compute(building)                                        E-09, E-10 (site), E-13, E-14
      reports.json_report.write(result, out / run_path / f"{entry.name}.epc.json")   I-7 checked on write (E-17)

python -m epc batch <results root> --country AT --out <dir>
  refuse an output directory that exists and is not empty                         E-16 (D28)
  for run_dir in sorted(dirs under root holding building.expanded.yaml):        E-15 if none
      try:    as above; row → table
      except EpcError as error: summary += (run, building, error.id, message); continue   (D18)
  reports.table.write(rows, out / "epc_table.csv"); reports.summary.write(counts, refusals, out / "epc_summary.md")
  exit 1 if any refusal else 0
```

`run` stops at the first error (D18). The engine is resolved and its data loaded before any record
is opened, so a wrong country or missing tables refuse in milliseconds. An engine without site
fields (Ireland) ignores the record's site block.

### 3.3 Step 1 in detail: from the expanded record to the building description

HiSim writes its building out: every run reads its `BuildingInformation` attribute by attribute,
with HiSim's origin strings, into `building.expanded.yaml` beside the realized record (the
contract; `hisim/energy_system/building_record.py`). The calculator needs no HiSim to read it.

The reader (`epc/inputs/record.py`) turns that file into a `BuildingDescription` with four rules,
none of which computes a building quantity:

- **Everything is taken as written**: the conditioned floor area; the gross volume where the record
  states one; per element class (floor, wall, roof, window, door) the area, the U-value and HiSim's
  adjustment factor; the window areas per direction; the glazing value and factors; the dwelling
  units; the thermal mass class; the site block. A missing or non-finite field refuses (E-05).
- **Situation** (D25): factor 1 → OUTSIDE_AIR; factor 0 → CONDITIONED_NEIGHBOUR; factor 0.5 →
  UNCONDITIONED_ATTIC for a roof, UNCONDITIONED_CELLAR for a floor, UNCONDITIONED_SPACE for a wall;
  any other factor refuses (E-06); 0.5 on a window or door refuses as well (D33).
- **Windows**: one surface per direction with azimuth from south (S 0°, E −90°, W +90°, N 180°),
  tilt 90°, horizontal tilt 0° and azimuth 0; a direction with area 0 is dropped; the window element keeps its own
  area for the transmission. A window element with area but no direction with area refuses (E-08).
- **Dwelling units**: the record's value; 0 refuses naming HiSim's `number_of_apartments` (E-07).
  The building type (detached, terraced, multi-family, apartment block, or `null` where HiSim's
  code states none) is a field of the record; an engine that needs it and finds `null` applies its
  own rule and records it as an assumption (Austria: F_S,h by dwelling units alone).

The reader has no notion of where HiSim got any value; a hand-written description for a validation
example states the same fields and its situations directly, for a floor also UNCONDITIONED_GARAGE or
GROUND (D36), and for a window its glass area (D38).

### 3.4 Step 2 in detail: an engine, and the Austrian pilot

The shell requires one thing of an engine: `compute(building) -> EpcResult`, plus its country, its
edition and whether it has a site climate. The Austrian engine is laid out in stages, which later
engines follow where it fits them and depart from where their norm is shaped differently:

| File | Responsibility | Austria (pilot) | Ireland (profiled from DEAP 4.2.7) |
|---|---|---|---|
| `method.py` | the stages in order, the result assembled; validates the site fields its method reads | AT, 2024-03 package, `reference+site`, reads `climate_region` and `elevation_in_m` | IE, DEAP 4.2.7 (2025-06), `national`, reads no site field |
| `data_tables.py` | load and cache the CSVs of `epc/data/<cc>/`, completeness checks (I-6), E-11/E-12 | Annex A, B, D, E; Tab. 7/9; Tab. 3–5; the examples | Tab. 1a/1b climate; Tab. 6a–6d windows; Table 8 factors (later) |
| `defaults.py` | the norm's own defaults for inputs the description does not state, as constants with citations; a use appends an `Assumption` | BGF = BF / 0.8 (Sec. 4); V = BGF × 3.00 m (Eq. (65), D34) when the record states none; thermal bridges Eq. (11a); natural ventilation; the Tab. 3–5 row per situation (D29, D32); construction (D3); glass share 0.7 unless stated (D38); F_S,h | thermal bridging y = 0.15 (App. K); infiltration by structure type (App. S); no chimneys or fans unless stated; living-area fraction; thermal-mass category (Table 11 / S10); overshading "average" (Table 6d) |
| `climate.py` | monthly outdoor temperatures and irradiation per surface | reference climate as printed; site climate Eq. (1)–(3); grid lookups (D21) | one national table; six orientation bins; horizontal |
| `usage.py` | set temperature, internal gains basis, air change basis, heating schedule | B 8110-5 Section 8 by dwelling units; θ_i,h of Tab. 7 | occupancy N(TFA); 21/18 °C, 56 h/week; gains per TFA |
| `envelope.py` | transmission and ventilation conductances, envelope area, mean U | Σ f·A·U over HiSim's elements with f by situation (Tab. 3–5, row from `defaults.py`), Eq. (11a), L_V Eq. (13)–(15), U_m, ℓ_c | Σ A·U with U adjusted by R_u for unheated spaces, + y·A_exposed; ventilation from the effective air change rate (Table 2.2) |
| `gains.py` | internal and solar gains per month | Eq. (37); A_g stated or Eq. (40) (D38), (43), (44) with F_S,h by units or terraced house | gains from floor area and water heating; 0.9·A·S·g·FF·Z |
| `balance.py` | losses, utilisation, heating period, monthly heat demand | C, τ, a, η (Eq. 49, 50, 57–59); f_h per H 5050-1 Eq. (9) (RK), f_H per H 5056-1 7.1 (SK), no share where θ_e ≥ θ_i,h (D35); Eq. (52), (53) | mean internal temperature with intermittency and thermal mass; utilisation factor; October–May |
| `indicators.py` | the result keys of this engine | HWB_RK, HWB_SK per BGF; L_T, L_V, U_m, ℓ_c, τ; losses and gains per climate; heating days; HGT 22/14 | space heat use per TFA; heat loss coefficient and HLI; later: delivered and primary energy, BER band |

Two rules hold for every engine:

- **Exactness is per engine.** Austria reproduces the norm to the printed digit (D2, D6), applied to
  HiSim's building (D24). The reviewer of a later engine decides its oracle and precision when its
  norm is read (Q1).
- **Defaults are the norm's, and they are visible.** Every method needs inputs the building
  description does not carry; every norm states what an assessor without data must use.
  `defaults.py` holds those norm defaults as constants with citations, nothing else; they apply to
  any building whose record lacks the value. Each use appends an `Assumption` to the result, so a
  reader sees "V = BGF × 3.00 m, B 8110-6-1 Eq. (65)" or "y = 0.15 W/(m²K), DEAP Appendix K" next to
  the number it influenced, which is what the comparison with HiSim needs.

**Austrian specifics worth naming.** The two quantities HiSim does not know come from the norm's
convention (BGF) and the engine's assumption (V; R5.3, D26, D34), both recorded; a gross volume in the record takes
precedence (P11). Transmission is Σ over HiSim's elements with the factor of the element's situation;
a conditioned neighbour (Section 4, p. 8) is in neither L_T nor A; a cellar ceiling takes the
uninsulated row (D32). F_S,h = 0.65 for ≤ 2 units or a
terraced house, 0.50 for 3–10, 0.40 above; the usage class has 3–9 and ≥ 10 (both as printed). HGT
22/14 per B 8110-5 Eq. (4) with the month's days (Q-D2). Numerics: IEEE double, no intermediate
rounding, February 28 days; a known residual source is Annex A's tilt tables, which are not Annex E's
tables rounded (risk R-1).

### 3.5 Boundaries and dependency rule

`epc/` imports the standard library and the third-party libraries HiSim already depends on
(PyYAML; `csv` from the standard library for the tables), nothing from `hisim`, without
exemption. A static check (ast over `epc/**/*.py`, every import,
relative imports resolved) enforces it, and the same check refuses the string `TABULA` in any `.py` module
under `epc/` (T-10, AC6); fixture records and results carry HiSim's origin
strings verbatim and are not modules. Inside the package: `__main__` → `batch` → `inputs`,
`engines`, `reports` → `model`; `engines/<cc>/` imports `model` and its own files only; `inputs`
imports `model` only; `reports` imports `model` only. The prototype `oenorm_hwb_calculator/` is
never imported or referenced (R8.7).

## 4. Alternatives considered

| # | Design point | Chosen | Rejected, with the deciding consequence |
|---|---|---|---|
| 4.1 | Where the calculator runs | Standalone `epc/`, reading an expanded record | A postprocessing option inside HiSim: five norms tied to HiSim's internals and PR stack (D11, D20). |
| 4.2 | Who writes the building out | HiSim, at run time, from the object in memory (D30) | A tool of the calculator's own that rebuilds `BuildingInformation` with the installed HiSim: HiSim's version string does not change with every commit, so a rebuild can silently differ from the run. Deriving it in the calculator from the archetype database: a second implementation of HiSim's rules and a certificate for a building HiSim did not compute with (D24). |
| 4.3 | What the building is | HiSim's elements as merged by HiSim, one factor each (D24) | Re-splitting elements into the archetype's sub-areas with their own U-values and situations: norm-closer for a party wall, but the certificate then describes a building HiSim did not simulate, and the comparison mixes model and method again. |
| 4.4 | Situation of an element | three rules on HiSim's factor and the element class (D25) | Archetype boundary codes: knowledge the calculator must not have (D24). HiSim's factor as the norm's factor: the norm's factors of Tab. 3–5 are not HiSim's 0.5. |
| 4.5 | Gross floor area and volume | the norm's conventions BF = 0.8·BGF and 3.00 m storey height, as assumptions (D26) | Reading them from the archetype row: D24. Using HiSim's conditioned floor area as BGF: the Austrian indicator is per BGF, and the norm says BF = 0.8·BGF. |
| 4.6 | Folder structure | three step folders + `model/` + `data/` | one flat module list: the three steps invisible; a folder per country at top level: the shell scattered. |
| 4.7 | Engine shape | `compute(building)` as the only requirement; the Austrian stage layout as the pattern | nine mandatory stage files: a corset for DEAP, NTA and the others; one shared balance library: the norms differ in detail, a shared core would blur exactness. |
| 4.8 | Result shape | dictionary with common core, free keys, assumptions | fixed dataclass per country: reporting would know every country. |
| 4.9 | Site | a block of the expanded record, from HiSim's weather configuration (D23) | a site file of the calculator's own: a second input to keep in step with every run; a building at several sites is several records anyway. |
| 4.10 | Package form | folder with tests under `epc/tests/` | own `pyproject.toml` and workflow now: deferred (D15, P7). |
| 4.11 | Off-grid azimuth or tilt (Austria) | refuse in stage 1 (D21) | interpolate now: dead code for every HiSim building and every validation example; comes with P4 (P10). |
| 4.12 | Batch output | an empty output directory per batch (D28) | managing which files belong to which run and removing stale ones: bookkeeping for no gain. |
| 4.13 | Validation of the record | the reader's field checks (E-05…E-08) plus the description's value checks | a JSON Schema file beside them: the field list a third time, for no further guarantee. |

## 5. Public surface

### 5.1 Folder structure `[proposed]`

```
epc/
  README.md                 what it is, the three steps, how to run it, where everything is, the key glossary per engine
  __init__.py               version
  __main__.py               command line: run | batch | countries
  batch.py                  walk a results root, run, collect rows and refusals
  model/                    THE DICTIONARIES
    building.py             Situation, Element, Window, Site, Provenance, BuildingDescription
    result.py               Entry, Assumption, RunInfo, EpcResult, COMMON_CORE
    errors.py               EpcError and its kinds with ids
  inputs/                   STEP 1
    record.py               building.expanded.yaml → ExpandedRecord, BuildingDescription (the four rules of §3.3)
  engines/                  STEP 2
    base.py                 EpcEngine protocol
    registry.py             ENGINES_BY_COUNTRY, for_country, register
    at/                     method, data_tables, defaults, climate, usage, envelope, gains, balance, indicators
    (ie/ de/ es/ nl/)       created with their engines, when the norm is at hand (Q1)
  reports/                  STEP 3
    json_report.py          EpcResult → <run path>/<building>.epc.json
    table.py                rows → epc_table.csv
    summary.py              counts and refusals → epc_summary.md
  data/                     NORM TABLES AND NATIONAL DEFAULTS
    README.md               the data contract: per country and file, columns, units, row counts, citations, licence note
    at/                     local, git-ignored: the ÖNORM tables, the B 8110-6-2 examples, SOURCES.md
    ie/                     DEAP tables (free; committed after the licence note, R8.4)
  results/                  default --out for local runs, git-ignored
  tests/
    fixtures/               building records written by HiSim, unedited; a hand-written description of the B 8110-6-2 SFH (inputs only; expected values stay local)
    test_model.py  test_result.py  test_reading.py  test_registry.py
    test_at_terms.py  test_at_envelope.py  test_at_balance.py  test_at_method.py  test_at_data_tables.py
    test_reports.py  test_cli.py
    test_import_boundary.py  test_at_validation.py (V-*)
```

HiSim: `pytest.ini` gains `epc/tests` in `testpaths` and the marker `epc_norm_data`; `.gitignore`
gains `epc/data/at/` and `epc/results/`. Nothing else outside `epc/` and `roadmap/epc/` changes.

### 5.2 The input dictionary (`epc/model/building.py`) `[proposed]`

```python
class Situation(Enum):
    OUTSIDE_AIR; CONDITIONED_NEIGHBOUR; UNCONDITIONED_ATTIC; UNCONDITIONED_CELLAR; UNCONDITIONED_SPACE
    UNCONDITIONED_GARAGE; GROUND                                    # hand-written descriptions only, floors (D36)

@dataclass(frozen=True)
class Element:        name: Literal["floor", "wall", "roof", "window", "door"]; area_in_m2: float
                      u_value_in_watt_per_m2_per_kelvin: float; adjustment_factor_used_by_hisim: float; situation: Situation
                      u_value_origin: str
@dataclass(frozen=True)
class Window:         name: str; azimuth_in_degree: float; tilt_in_degree: float; area_in_m2: float; g_value: float
                      frame_factor: float | None; non_perpendicular_factor: float | None; external_shading_factor: float | None
                      glass_area_in_m2: float | None = None    # stated by hand (D38); the three factors may be None by hand, the reader requires them
@dataclass(frozen=True)
class Site:           climate_region: str | None; elevation_in_m: float | None      # Spain's zone joins with its engine
@dataclass(frozen=True)
class Provenance:     record_schema: str; producer: str; hisim_building_code: str; configured_fields: tuple[str, ...]
@dataclass(frozen=True)
class BuildingDescription:
    name: str; building_type: Literal["detached", "terraced", "multi_family", "apartment_block"] | None
    conditioned_floor_area_in_m2: float                             # HiSim's reference area; each engine derives its own from it
    gross_volume_in_m3: float | None                                # when the record states one (P11)
    dwelling_units: int; dwelling_units_configured: bool
    heat_capacity_class: str                                        # HiSim's five classes, mapped by each engine
    elements: tuple[Element, ...]; windows: tuple[Window, ...]      # the window element carries the transmission area, windows the gains
    thermal_bridging_surcharge_used_by_hisim: float                 # information only
    site: Site
    provenance: Provenance
    # __post_init__ refuses with E-18 what D31 lists, and a window element with area but no window
```

### 5.3 The result dictionary (`epc/model/result.py`) `[decided 2026-10-10, D14, D22]`

```python
@dataclass(frozen=True)
class Entry:       value: float | str | tuple[float, ...]; unit: str; cites: str = ""; label: str | None = None; note: str | None = None
@dataclass(frozen=True)
class Assumption:  key: str; value: str; source: str
@dataclass(frozen=True)
class RunInfo:     record: str; building: str; country: str; engine: str; edition: str; climate_basis: str; calculator: str; computed_at: str
COMMON_CORE = ("heating_demand_reference_climate", "indicator_reference_area")

class EpcResult:
    def add_indicator(self, key, value, unit, *, cites="", label=None, note=None) -> None     # duplicate key refused (I-8)
    def add_term(self, key, value, unit, *, cites="") -> None
    def add_monthly(self, key, values: Sequence[float], unit) -> None                         # exactly twelve values
    def assume(self, key, value, source) -> None
    def to_dict(self) -> dict                                                                 # checks the common core (I-7, E-17); the JSON of Mockup A
```

Keys: `heating_demand_reference_climate` and `indicator_reference_area` are mandatory;
`heating_demand_site_climate` is present exactly when `run.climate_basis == "reference+site"`;
every other key is the engine's. `epc/README.md` keeps a glossary of every engine's keys. `provenance` in
the JSON is the description's provenance plus the engine's table digests (`tables`, §7.1).

### 5.4 Engine protocol and command line `[proposed]`

```python
class EpcEngine(Protocol):
    country: ClassVar[str]; edition: ClassVar[str]; climate_basis: ClassVar[Literal["reference+site", "national"]]
    def load_data(self) -> None: ...                                  # E-11, E-12; idempotent
    def compute(self, building: BuildingDescription) -> EpcResult: ...   # reads building.site; E-09, E-10
ENGINES_BY_COUNTRY: Mapping[str, type[EpcEngine]]                    # {"AT": AustrianEngine}
def for_country(code: str) -> EpcEngine                               # E-01
def register(engine: type[EpcEngine]) -> None                         # tests and future countries
```

```
python -m epc run   <results dir> --country <CC> [--out DIR]       # --out defaults to epc/results/ (D39)
python -m epc batch <results root> --country <CC> [--out DIR]
python -m epc countries
```

Exit codes: `run` 0 or 2 (`EpcError`, nothing written); `batch` 0, 1 (some run refused, summary
written), 2 (root, arguments or output directory unusable).

### 5.5 Output files `[decided 2026-10-10, D17]`

| File | When | Content |
|---|---|---|
| `<run path>/<building>.epc.json` | every computed run and building | `EpcResult.to_dict()`, requirements Mockup A; the run path relative to the results root, mirrored under the output directory |
| `epc_table.csv` | batch | `run, building, country, engine, edition`, then every indicator key seen, first-seen order, header `key [unit]`, empty where absent |
| `epc_summary.md` | batch | date, counts, one row per refusal: run, building, error id, message |

## 6. Internal structure

| Unit | Responsibility | Must not know |
|---|---|---|
| `__main__.py`, `batch.py` | arguments, exit codes, walking, collecting | norms, record layout |
| `model/*` | the dictionaries, their value checks, serialization | norms, files |
| `inputs/record.py` | expanded record → description by the four rules of §3.3; version check | norms, HiSim's internals |
| `engines/base.py`, `registry.py` | protocol, registry, refusal | files |
| `engines/<cc>/method.py` | the engine's computation in order, result assembly, validation of the site fields it reads, edition | files, other engines |
| `engines/<cc>/data_tables.py` | its CSVs, completeness, E-11/E-12 | the balance |
| `engines/<cc>/defaults.py` | its norm defaults as constants with citations | the record |
| `engines/at/{climate,usage,envelope,gains,balance,indicators}.py` | one stage each, one function per norm term with its citation | files, reporting |
| `reports/*` | JSON, CSV, summary | norms |
| `data/README.md` | the data contract per country | values |

Target ≤ 300 lines per module; a loader that grows past it is split by table family.

## 7. Data, state and invariants

### 7.1 Data folders (R8) `[decided 2026-10-07, D7]`

`epc/data/<cc>/` holds a country's norm tables and national defaults as CSV with `#` headers
(norm, edition, table, page, transcription method), comma separator, dot decimal, read with the
standard library's `csv`. **Austria's folder is local and git-ignored** (licensed, D7); the
repository carries `epc/data/README.md` with, per country and file, name, columns, units, row
count, citation and the licence decision (R8.4); `SOURCES.md` beside the local tables lists
citations and SHA-256, which the result's `provenance.tables` repeats. The loader's row counts and
column names are the single source (I-6); the README states the same numbers.

| Austria | Content |
|---|---|
| `reference_climate_b8110_5_2024_annex_a.csv` | Tab. A.1, A.2, A.3–A.7 |
| `temperature_coefficients_b8110_5_2024_annex_b.csv` | Tab. B.1–B.7 |
| `radiation_coefficients_b8110_5_2024_annex_d.csv` | Tab. D.1–D.14 |
| `transposition_factors_b8110_5_2024_annex_e.csv` | Tab. E.1–E.7 |
| `usage_profiles_b8110_5_2024_tab7_tab9.csv` | residential rows of Tab. 7 and 9 |
| `temperature_correction_factors_b8110_6_1_2024_tab3_5.csv` | 14 rows of Tab. 3–5, among them those the situations of §5.2 map to (keys in `epc/data/README.md`) |
| `validation_examples_b8110_6_2_2024.csv` | inputs and expected values of B 8110-6-2 Sections 4 and 5 |

The Irish engine's files are listed in Appendix A.

What stays in code, published, is the formulas and the single constants of the prose clauses with
their citation (Austria: f_g = 0.7, F_g = 0.9·0.98, 0.34 Wh/(m³K), 2.6 m, BF = 0.8·BGF, 3.00 m,
f_BW per Bauweise as stated after Eq. (50), a₀ = 1, τ₀ = 16, F_S,h 0.65/0.50/0.40, the 0.2/0.75/0.1 of
Eq. (11a), the factor 0 for conditioned neighbours; Ireland: y = 0.15, 0.9 in the solar gain, the
occupancy formula, 21/18 °C). The temperature-correction factors of Tab. 3–5 are a table: local
file only, not printed in any committed document (R8.2).

### 7.2 Situation rule (R6.2, D25) `[decided 2026-10-11]`

| HiSim's adjustment factor | Element class | Situation |
|---|---|---|
| 1 | any | OUTSIDE_AIR |
| 0 | any | CONDITIONED_NEIGHBOUR |
| 0.5 | roof | UNCONDITIONED_ATTIC |
| 0.5 | floor | UNCONDITIONED_CELLAR |
| 0.5 | wall | UNCONDITIONED_SPACE |
| 0.5 | window, door | E-06 (D33) |
| other | | E-06 |

HiSim's factor 0.5 states a reduced temperature difference, not what lies behind the element; the
calculator does not know and does not guess. Each engine maps the situation to one row of its norm as
its default and records that row as an assumption (D29): the Austrian engine in `defaults.py` to
Tab. 4 — UNCONDITIONED_CELLAR to „Decke zu unkonditioniertem, ungedämmtem Keller" (footnote a judges
the cellar's walls and floor, which the description does not state; D32), UNCONDITIONED_ATTIC to
„Decke zu unkonditioniertem, geschlossenem Dachraum", UNCONDITIONED_SPACE to „Wand zu sonstigem
Pufferraum" —, the Irish engine to R_u values of TGD L Appendix A, the German engine to F_x factors.
Whether a case occurs in HiSim's buildings is no criterion (R6.7).

### 7.3 Invariants (checked where stated)

| ID | Invariant | Established | Checked |
|---|---|---|---|
| I-1 | Q_l,j = Q_T,j + Q_V,j | `at.balance` | T-5 |
| I-2 | 0 ≤ f_h,j ≤ 1; 0 < η_j ≤ 1 for γ > 0; Q_h,j ≥ 0 | `at.balance` | assertion, T-5, V-1 |
| I-3 | RK and SK share the building, L_T, L_V, C, τ, a | `at.method` builds them once | V-1 |
| I-4 | BF = 0.8·BGF; A_g = 0.7·A_W; L_ψ+L_χ ≥ 0.1·Σ f A U | `at.envelope`, `at.gains` | T-5 |
| I-5 | A description read from a fixture equals the record's values; nothing computed | `inputs.record` | T-3 |
| I-6 | Every table loads complete | `at.data_tables` | E-12, T-6, V-5 |
| I-7 | Every written result carries the common core and every assumption the engine made | `EpcResult.to_dict` | T-2, T-7, E-17 |
| I-8 | A result key is added once; a monthly series has twelve values | `EpcResult.add_*` | T-2 |
| I-9 | `BuildingDescription` is immutable and validated on construction | `model.building` | T-1 |
| I-10 | A CONDITIONED_NEIGHBOUR element contributes neither to L_T nor to A | `at.envelope` | T-5 |
| I-11 | found = computed + refused in the batch summary | `batch` | T-9 |

Ordering: engine and data before any record; the record's version before its fields; `load_data`
once per process; `compute` is pure.

## 8. Error handling

Policy: fail fast with the offending party named; step 1 guesses nothing (R6.5); an engine assumes
only what its `defaults.py` states, and states it in the result (R4.4). `run` stops at the first
error, `batch` records and continues (D18).

| ID | Condition | Raised by | Message template |
|---|---|---|---|
| E-01 | country without engine | `engines.registry` | "EPC calculation is not possible: no EPC engine is implemented for country '{cc}' yet (implemented: {codes})." |
| E-02 | no `building.expanded.yaml` in the results directory | `inputs.record.load` | "No building.expanded.yaml in '{dir}'; the run was recorded before HiSim wrote it and is re-simulated (input_contract.md)." |
| E-03 | the record has no building | `inputs.record.load` | "The record '{path}' holds no building; the EPC is computed per building." |
| E-04 | `schema` of another major version | `inputs.record.load` | "'{path}' is {found}; this calculator reads building-expanded/1 (input_contract.md)." |
| E-05 | a field missing or not finite | `inputs.record.describe` | "Building '{name}': the expanded record states no valid value for '{field}' (contract §2.3)." |
| E-06 | an adjustment factor other than 1, 0.5, 0 | `inputs.record.describe` | "Building '{name}', element '{element}': HiSim's adjustment factor {b} has no situation; the calculator knows 1, 0.5 and 0 (design.md §7.2)." |
| E-07 | dwelling units 0 | `inputs.record.describe` | "Building '{name}': dwelling units are 0 (HiSim's estimate); state 'number_of_apartments' in the building configuration." |
| E-08 | window area without any direction that has an area | `inputs.record.describe` | "Building '{name}': {area} m² of windows but no direction with an area; the solar gains need orientations (D27)." |
| E-09 | the site block lacks a field the engine reads | `<cc>.method` | "Building '{name}': the site block states no '{field}'; the {cc} engine needs {fields} (from HiSim's weather configuration)." |
| E-10 | a site value not admissible | `<cc>.method` | "Building '{name}': site field '{field}' = '{value}' is not admissible for {cc}; use {admissible}." |
| E-11 | a table file missing | `<cc>.data_tables` | "The {country} engine needs its tables under '{folder}' ({file} is missing); see epc/data/README.md." |
| E-12 | a table incomplete (I-6) | `<cc>.data_tables` | "{file}: expected {n} rows for {dimension}, found {m}." |
| E-13 | window angle off the grid or not finite (Austria, D21) | `at.climate` | "Window '{name}': azimuth {a}° / tilt {t}° is not a tabulated angle of OENORM B 8110-5 Annex A/E (interpolation: P10)." |
| E-14 | γ ≤ 0 under the Austrian reference climate | `at.balance` | "Month {m}: the reference-climate balance has losses ≤ 0 (γ = {value}); H 5050-1:2024 Eq. (9) states no rule (Q-D1)." |
| E-15 | results root holds no expanded record | `batch` | "No building.expanded.yaml found under '{root}'; the runs were recorded before HiSim wrote it." |
| E-16 | output directory exists and is not empty | `batch` | "Output directory '{dir}' is not empty; give a new or empty one (D28)." |
| E-17 | an engine returned no common-core key | `EpcResult.to_dict` | "Engine '{cc}' returned no '{key}'; every engine fills {COMMON_CORE}." |
| E-18 | a value no building can have (D31); an envelope without area | `model.building`, `at.envelope`, `at.defaults` | "Building '{name}': '{field}' = {value} is not admissible ({rule})." |
| E-19 | a table malformed: header, cell, key or band | `<cc>.data_tables` | "{file}, line {line}: {problem}." |

HiSim's writer refuses a building with a missing or non-finite value before the run starts and
writes no partial file; a building type its code does not state is `null`, not an error. Not errors: a window direction with area 0
(dropped); unknown record fields (ignored, contract §2.4); site fields a country does not read
(ignored); an element with factor 0 (I-10); a negative γ_H under the Austrian site climate (H 5056-1
7.1 states the replacement rule).

## 9. Testing strategy

Two kinds, by D8, all under `epc/tests/`: functionality tests (`T-n`, marker `base`, committed, no
norm value in them) and validation (`V-n`, marker `epc_norm_data`, skipped with the folder name
when the data are absent; output attached to the PR, R9.3).

| ID | Verifies | Kind | Fixture | Failure looks like |
|---|---|---|---|---|
| T-1 | I-9 | unit | `BuildingDescription` with each invalid field | silent acceptance |
| T-2 | R4.2–R4.4, I-7, I-8 | unit | a result built by hand: duplicate key, eleven values, missing core, assumptions, `to_dict` | shape drift |
| T-3 | R2.1, R2.4, R6, I-5, AC5, AC7, AC10 | unit | expanded fixture records: a detached house with and without overrides, a record with factor 0.5 on the floor, a record with 0 units, one with windows but no direction, one with factor 0.7, no record, version 2, a missing field | a computed value, a refusal missing or misnamed |
| T-4 | R6.2, D25 | unit | every (factor, element class) pair of §7.2 | a wrong situation |
| T-5 | R5.1–R5.13, D-R1, I-1, I-2, I-4, I-10 | unit, hand-computed | one element per situation, one window, a toy twelve-month climate as values; Eq. (11a) lower bound; the four f_h cases; the cellar row (D32); the BGF and V assumptions recorded; E-13, E-14 | a term deviating from its equation |
| T-6 | R8.6, E-11, E-12, AC11 | unit | `AustrianEngine(data_folder=tmp)` over an empty folder and synthetic incomplete CSVs | a silent default |
| T-7 | R3, R10, AC8, I-7 | unit | a dummy engine "ZZ" with and without the core keys; registered, run, reported | the Austrian engine touched; a result without the core accepted |
| T-8 | R7, R11 | unit | result → JSON; two results → table; refusals → summary | a column lost, a unit missing |
| T-9 | R2.2, R2.3, R12, AC1 (shape), AC3, AC4, AC9, I-11 | integration | `python -m epc` in a subprocess over a fixture results tree with the dummy engine; country DE; run and batch; refusal paths; non-empty output directory; exit codes | a wrong exit code, a batch that stops |
| T-10 | R1.2, R6.1, R8.7, AC6 | static | ast over `epc/**/*.py`: imports only of the standard library, PyYAML and the package, in the directions of §3.5; the string `TABULA` in those modules | an import of `hisim` or the prototype; archetype knowledge in the calculator |
| T-11 | R13, AC12 | review | `git diff --stat origin/main -- hisim tests system_setups energy_systems` empty on every PR | a HiSim file changed |
| T-12 | R1.3, C3 | contract | `pytest --collect-only epc/tests`; marker registered | tests not collected |
| V-1 | AC2, R9.2 | golden against the norm, local | B 8110-6-2 SFH, variant 1 cases 1 and 2, variant 2 case 2 (D37), as a hand-written description from the local examples file | any value off by more than half of the last printed digit |
| V-2 | AC2, R9.2 | golden, local | B 8110-6-2 MFH, variants 1 and 2.1, cases 1 and 2, five dwelling units (D37) | as V-1 |
| V-3 | R5.13, Q-D2 | evidence, local | HGT 22/14 of three real certificates (4169, 3762, 3731 Kd) | the formula drifting |
| V-4 | AC1, R2, C6 | end to end, local | the first ai4c run recorded with building record and site; every key of Mockup A | a record the reader cannot take |
| V-5 | I-6, R8.1, R11 | contract, local | the real tables: complete, headers cite norm, edition, clause, page | an incomplete table |

Not tested deliberately: the engines of the other countries (no norms yet); stage 2 of the Austrian
engine; the transcription beyond V-1/V-2/V-5.

## 10. Migration, compatibility and rollout

Nothing changes for HiSim users (R13). The Austrian tables, transcribed locally, live under
`epc/data/at/` and stay there (D7). The prototype is deleted after V-1 and V-2 pass (D4). Runs
recorded before the site reached HiSim's weather configuration are re-simulated (C6). V-4 therefore
waits for the requester's two changes to HiSim, the building record (issue #949, D30) and the site
(D23); PR-D's gate until then is V-1 to V-3, and V-4 is run and attached when the first such run exists.
The fixtures of PR-B are written by HiSim's writer.

| PR | Content | Tests | Review subject |
|---|---|---|---|
| PR-A | `roadmap/epc/requirements.md`, `input_contract.md`, `design.md` | — | the plan: three steps, folders, dictionaries |
| PR-B | the shell: `model/`, `inputs/`, `engines/{base,registry}`, `reports/`, `batch.py`, `__main__.py`, fixtures, `data/README.md`, `pytest.ini`, `.gitignore`, `epc/README.md` | T-1 … T-4, T-7 … T-12 | the shell |
| PR-C | `engines/at/` data tables, defaults, climate, usage, envelope, gains | T-5 (climate, envelope, gains), T-6; V-5 (local) | the Austrian terms, each with its citation |
| PR-D | `engines/at/` balance, indicators, method; `countries` lists AT | T-5 (balance), T-9 with AT; **V-1, V-2, V-3, V-4** (local, output attached) | the balance and the norm reproduction, the gate |
| later | one PR per further engine, each after its norm is read (Q1): data contract, defaults, computation, its validation | its T-5 and V-* equivalents | one country at a time |

## 11. Risks and unknowns

| # | Risk | Trigger | Effect | Mitigation / when known |
|---|---|---|---|---|
| R-1 | Residuals above half a digit in V-1/V-2 | an intermediate rounding of the norm's software; Annex A vs Annex E | R9.2: find the step, record it in §3.4 | first run of V-1 in PR-D |
| R-2 | HiSim's building record drifts from the building the run computes with | HiSim changes `BuildingInformation` without the writer | a wrong building read | HiSim's own test (`tests/test_building_expanded_record.py`) compares the record with `BuildingInformation` field by field; a format change raises the schema's major version, which the reader refuses (E-04) |
| R-3 | HiSim's merged elements and the norm's conventions for BGF and V make the certificate differ from a real one for the same archetype | D24, D26 | a deviation in the manual tests (P4) | that deviation is the finding the comparison is for; it is not corrected in the calculator |
| R-4 | Transcription errors in the Austrian tables | typing | wrong climate | second transcription done locally; I-6; V-1/V-2 as the end-to-end check |
| R-5 | A later engine needs a quantity the description lacks | DE/IE/ES/NL norms | contract and description extension | each country's appendix names its needs before its PR (Q1); the Irish profile already lists them (§3.4) |
| R-6 | National keys grow inconsistent | free keys | columns with different meanings | the common core is fixed; `epc/README.md` keeps the key glossary per engine |
| R-7 | Maintainers cannot run the Austrian validation on CI | licensed tables local | review friction | functionality tests cover every equation; validation output attached; loader refuses loudly |
| R-8 | HiSim's apartment estimate refuses small scaled archetypes (E-07) | floor area < 92.1 m² without a configured count | the ai4c run must state `number_of_apartments` | the message names the field; the summary lists every such run |
| R-9 | Reusing prototype knowledge by habit | author bias | a prototype deviation reproduced | every term from the norm text with citation; T-10; V-1 and V-3 catch the known deviations |

## 12. Code-review guide

**Where to look first**

- `inputs/record.py`: nothing computed, nothing averaged, nothing looked up; the four rules of
  §3.3 and nothing else; E-04 before any field.
- `engines/at/envelope.py`: Σ over HiSim's elements with the factor of the situation; conditioned
  neighbours in neither sum (I-10); the rows from `defaults.py`; Eq. (11a) with its lower bound.
- `engines/at/defaults.py`: BGF and V by the norm's conventions, each recorded as an assumption; no
  other place derives a building quantity.
- `engines/at/balance.py`: f_h per H 5050-1 Eq. (9g–9j), f_H per H 5056-1 7.1; the γ boundary means
  wrap December → January; E-14 under RK, replacement under SK.

**Invariant checklist**

- [ ] I-1 … I-4, I-10 asserted or tested where §7.3 says
- [ ] I-5: T-3 compares against the fixture's numbers, not the function under test
- [ ] I-7: the dummy engine without the core is refused on write

**Requirement checklist**

- [ ] R1.2 / R6.1: T-10 green; no `hisim` import under `epc/`; no `TABULA` in a `.py` module under `epc/`
- [ ] R4.4: every default in `defaults.py` reaches `assumptions` (T-5)
- [ ] R6.3: no rounding, no `max(1, …)`, no second apartment rule
- [ ] R8.1/R8.2/R8.3/R8.5: citations on every constant; no table value in code, tests or documents; `epc/data/at/` ignored
- [ ] R9.2: tolerances not loosened; V-* output attached
- [ ] R13: HiSim's folders unchanged

**Smells to reject**

- any arithmetic on building quantities in `inputs/` (R6.1)
- a default applied in a stage file instead of `defaults.py`, or one that leaves no assumption
- a norm table value in a test, a module or a document (D7)
- an engine reading a file, or a report knowing a country
- an interpolation (D21) or a nearest-neighbour lookup
- a batch that raises on a refused run (D18)

**Out of scope for this review**: the other engines (Q1); stage 2 of the Austrian engine; the
headline indicators (P6); statistics of the archetype database HiSim builds from — the calculator
handles every value HiSim can write, regardless of its frequency (R6.7).

## 13. Design questions and their answers

**Q-D1 — Negative gain-loss ratio (Austria).** `[answered 2026-10-07]` Site climate: H 5056-1 7.1
replaces a negative γ_H by the next positive month. Reference climate: no rule written; E-14 (D-R1);
unreachable under Annex A, no month of which is as warm as the set temperature.

**Q-D2 — HGT 22/14.** `[answered 2026-10-07]` B 8110-5 Eq. (4) with the month's days over the months
with θ_e,j ≤ 14 °C reproduces three real certificates exactly; V-3 keeps them as evidence.

**Q-D3 — What the building is.** `[answered 2026-10-11, D24]` HiSim's building as HiSim
computed with it: one element per class with HiSim's area, U-value and factor. Whatever HiSim merged
into an element stays merged; the calculator does not know, and must not know, where HiSim got it.

**Q-D4 — Conditioned neighbours.** `[answered 2026-10-07]` B 8110-6-1:2024 Section 4, p. 8: outside
the thermal envelope; factor 0, no part in A (I-10).

**Q-D5 — Situation of an element with factor 0.5.** `[answered 2026-10-11, D25]` An unconditioned
space; which one, the element class says: attic for a roof, cellar for a floor, buffer space for a
wall; each engine's row for them is its norm default, recorded as an assumption (D29, §7.2).

**Q-D6 — Gross floor area and volume.** `[answered 2026-10-11, D26]` BGF = BF / 0.8 (Section 4);
V from the record when it states one, else BGF × 3.00 m (the storey height of Eq. (65), D34); both as assumptions, confirmed by
the certificates of P4.

**Q-D7 — Interpolation.** `[answered 2026-10-10, D21]` Deferred to P10; stage 1 refuses off-grid
angles, which neither HiSim buildings nor the validation examples have.

**Q-D8 — Where the Irish engine gets what the description lacks.** `[answered 2026-10-10]` From
`engines/ie/defaults.py` with DEAP's existing-dwelling defaults (Appendix S, Table 6d, Appendix K),
each use recorded as an assumption.

No design question is open.

## 14. Glossary

- **Expanded record** — `building.expanded.yaml`, written by HiSim beside the realized record, the input of step 1.
- **Building description / Result** — the dictionaries of §5.2 and §5.3; the site is a block of the description.
- **Situation** — what an element borders on, from HiSim's factor and the element class (§7.2); **CONDITIONED_NEIGHBOUR** for factor 0.
- **Engine** — one country's method package; `compute(building)` is all the shell requires.
- **Assumption** — a norm default an engine applied, as the result lists it.
- **Common core** — `heating_demand_reference_climate`, `indicator_reference_area`; plus `heating_demand_site_climate` where the method has a site climate (D22).
- **Functionality test / validation** — `T-n`, committed, everywhere / `V-n`, local, against a norm's examples.

## Appendix A — Profile of the Irish engine (DEAP 4.2.7)

Not built in PR-A to PR-D; kept so that the shell is shaped by two concrete methods (§3.4). The
engine `engines/ie/` reads no site field (`climate_basis = "national"`), takes what the description
lacks from `engines/ie/defaults.py` with DEAP's existing-dwelling defaults (Appendix S, Table 6d,
Appendix K), each use an assumption (Q-D8), and keeps its data under `epc/data/ie/` (free; committed
after the licence note, R8.4):

| File | Content |
|---|---|
| `climate_deap_4_2_7_tab1.csv` | Tab. 1a mean external temperature, Tab. 1b solar radiation by orientation |
| `windows_deap_4_2_7_tab6.csv` | Tab. 6a–6d defaults: U, g, frame factor, solar access |
| `existing_dwellings_deap_4_2_7_appendix_s.csv` | default infiltration, thermal-mass and living-area data |
