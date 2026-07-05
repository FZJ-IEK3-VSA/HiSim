# HiSim RenoVisor Translator — Specification (v1 draft)

The RenoVisor translator is a standalone CLI in the `hisim` package that takes a JSON request
containing a home inventory (per the RenoVisor↔HiSim contract in `scripts/hisim_spec.md`),
translates it into (a) the choice of one of the existing `*_building_sizer` Python system setups
and (b) a `ModularHouseholdConfig` parameter file for that setup, runs the simulation, and
submits selected result files to a server via REST.

Deriving RenoVisor's `CalcResult` from the HiSim outputs is explicitly **out of scope for v1**:
the translator uploads raw HiSim result files; interpreting them is the receiving server's job.

```
request.json ──▶ validate ──▶ (apply measures) ──▶ map ──▶ run HiSim ──▶ collect ──▶ upload
                                                    │
                                                    ├─ setup selection (household_*_building_sizer.py)
                                                    └─ ModularHouseholdConfig JSON
```

---

## 1. CLI

```
python -m hisim.renovisor run <request.json> --variant {base|measures} [options]
```

| Option | Default | Meaning |
|---|---|---|
| `--variant base\|measures` | required | `base`: simulate the inventory as-is, ignore `measures`. `measures`: apply **all** measures to the inventory first, then simulate. One invocation = one simulation. |
| `--result-dir DIR` | temp dir | Where HiSim writes results. |
| `--no-upload` | off | Run everything but skip the REST submission (local testing). |
| `--keep-files` | off | Don't delete the result dir after a successful upload. |

Exit codes: `0` success · `2` request validation failed · `3` simulation failed · `4` upload failed
(after retries). For codes 3 and 4 a failure report is POSTed to the submission URL (see §7).

## 2. Input: wrapper envelope

The translator input wraps the unmodified RenoVisor payload (`scripts/hisim_spec.md`) in a job
envelope. The RenoVisor contract itself is **not** extended; whoever dispatches jobs adds the
wrapper.

```json
{
  "job": {
    "jobId": "abc-123",
    "submission": {
      "url": "https://renovisor.example/api/results",
      "files": ["all_kpis.json", "*_kpi_config_for_building_sizer.json"],
      "authToken": "..."
    },
    "simulationOverrides": {
      "year": 2019,
      "secondsPerTimestep": 900,
      "postProcessingOptions": ["EXPORT_TO_CSV"]
    }
  },
  "request": {
    "contractVersion": "1.0.0",
    "location": { "...": "..." },
    "homeInputs": { "...": "..." },
    "measures": [ { "...": "..." } ]
  }
}
```

| Field | Required | Notes |
|---|---|---|
| `job.jobId` | yes | Opaque correlation ID, echoed in every upload and status POST. |
| `job.submission.url` | yes | Target for result upload and failure reports. |
| `job.submission.files` | yes | Glob patterns matched **relative to the HiSim result directory** (recursive). Files matching no pattern are not uploaded. The mapping report (§6) is always uploaded regardless. |
| `job.submission.authToken` | no | Sent as `Authorization: Bearer <token>` on every POST. |
| `job.simulationOverrides` | no | Partial overrides of the defaults in §5. |
| `request` | yes | Verbatim RenoVisor payload, contract version `1.x`. Unknown keys inside are ignored (forward compatibility, per the contract). |

Validation errors (missing required fields, unknown `heating.primary`, non-`1.x`
`contractVersion`) abort before simulation with exit code 2 — nothing is POSTed, since a
malformed request may not even contain a usable URL.

## 3. Measures application (`--variant measures`)

Measures are applied to a **copy of `homeInputs`** before mapping, following the semantics in
`scripts/hisim_spec.md` §4 (all measures together, one package):

| Measure | Effect on the inventory copy |
|---|---|
| `heat_pump` | `heating.primary = "heat_pump"` (changes setup selection; `kW` ignored in v1 — the setup auto-sizes). |
| `pv` | `pv.kWp = params.kWp` (new total). |
| `battery` | `battery.kWh = params.kWh` (new total). |
| `solar_thermal` | `solarThermal.mode = "hot_water"` (affects setup selection, §4.1). |
| `roof_insulation`, `wall_insulation`, `floor_insulation`, `windows`, `doors`, `air_sealing`, `ventilation` | Counted as *envelope measures*; they influence the TABULA refurbishment variant (§4.2) but not individual U-values in v1. Their `params` are recorded in the mapping report as approximated. |

## 4. Mapping to a system setup + ModularHouseholdConfig

The translator is **best-effort**: fields that HiSim cannot represent yet are accepted, recorded
in the mapping report (§6), and skipped. Fidelity grows over time as HiSim gains parameters.

### 4.1 Setup selection

Selection is driven by (post-measures) `heating.primary` and `solarThermal.mode`:

| `heating.primary` | `solarThermal.mode` | System setup |
|---|---|---|
| `gas` | `none` | `household_gas_building_sizer.py` |
| `gas` | not `none` | `household_gas_solar_thermal_building_sizer.py` |
| `oil` | any | `household_oil_building_sizer.py` |
| `heat_pump` | `none` | `household_heatpump_building_sizer.py` |
| `heat_pump` | not `none` | `household_heatpump_solar_thermal_building_sizer.py` |
| `direct_electric` | any | `household_electric_heating_building_sizer.py` |
| `district` | any | `household_district_heating_building_sizer.py` |
| `wood` | any | `household_pellets_building_sizer.py` *(approximation)* |
| `solid_fuel` | any | `household_pellets_building_sizer.py` *(approximation — no coal/peat setup exists)* |

Solar thermal combined with a primary other than gas/heat pump has no setup; the solar thermal
system is dropped and flagged in the mapping report. `irish_cooking_range` and
`heating.secondary` are unmapped in v1.

### 4.2 ArcheTypeConfig

| ArcheTypeConfig field | Source | Rule |
|---|---|---|
| `building_code` | `dwellingType`, `constructionYear`, `envelopeState`, envelope measures | TABULA code `IE.N.<TYPE>.<band>.Gen.ReEx.001.<variant>`, see below. |
| `conditioned_floor_area_in_m2` | `floorAreaM2` | Direct. |
| `number_of_dwellings_per_building` | `dwellingType` | 1; for `apartment` also 1 (the AB archetype models the dwelling). |
| `construction_year` | `constructionYear` | Direct. |
| `weather_location` | `location.countryCode` | `IE` → `Dublin` (NSRDB 2019 file, already in `hisim/components/weather.py`). Other country codes → that country's `LocationEnum` entry if present, else validation error. `region` / `eircodeOrPostcode` are recorded but unused in v1 (one weather station per country). |
| `pv_rooftop_capacity_in_kilowatt` | `pv.kWp` | Direct; `share_of_maximum_pv_potential` on the energy-system side controls whether it is built (§4.3). |
| `pv_azimuth` | `pv.orientation` | `south`→180, `south_east`→135, `south_west`→225, `east_west`→90 *(approximation: modelled as a single east-facing array)*, absent→180. |
| `pv_tilt` | `roof.construction` | `flat`→10°, otherwise 30° (contract: roof-typical tilt, tilt is not asked). |
| `lpg_households` | `occupants` | Fixed lookup table, §4.4. |
| `building_name`, `building_id` | `job.jobId` | `"BUI1"`, jobId. |
| `building_postal_code`, `building_location`, coordinates | `location` | Postcode passed through if present; location string = `region` or country name; coordinates = weather-station coordinates. |

**TABULA code construction.** The processed TABULA table
(`hisim/inputs/housing/data_processed/episcope-tabula.csv`) contains 124 Irish variants covering
building types `SFH`, `TH`, `AB` and age bands `01`–`10`, each in refurbishment variants
`.001`/`.002`/`.003` (existing / usual / advanced refurbishment).

- Type: `detached_sfh`, `bungalow`, `other` → `SFH`; `semi_detached_sfh`, `terraced_sfh` → `TH`
  *(semi-detached as terraced is an approximation)*; `apartment` → `AB`.
- Age band: pick the band whose `Year1_Building`–`Year2_Building` range (from the TABULA CSV)
  contains `constructionYear`; clamp to the first/last band outside the covered range. The
  band table is generated from the CSV at build time, not hard-coded.
- Refurbishment variant: from `envelopeState` — `unrenovated`→`.001`, `usual_refurb`→`.002`,
  `advanced_refurb`→`.003`. Under `--variant measures`, envelope measures upgrade this floor:
  1–2 distinct envelope measure types → at least `.002`; 3 or more → `.003`. This is the v1
  stand-in until per-element U-values are supported in HiSim; per-element `insulation` fields
  and expert `uValueWPerM2K` overrides are recorded as approximated/ignored.
- If the exact code is missing from the CSV (some IE type/band combinations have gaps), fall
  back to the nearest available age band of the same type and flag it.
- **Workaround:** TABULA rows without door or window geometry (``A_Door_1``/``A_Window_*`` = 0,
  e.g. all of ``IE.N.SFH.05.*``, ``IE.N.TH.04/06.*``, ``IE.N.AB.08–10.*``) crash the HiSim
  ``Building`` component (``simulation_issues.md`` item 1). Until that is fixed in HiSim, such
  rows are excluded from selection and the nearest *usable* age band is chosen instead, with an
  explicit note in the mapping report.

### 4.3 EnergySystemConfig

| EnergySystemConfig field | Source | Rule |
|---|---|---|
| `heating_system` | setup selection | The `HeatingSystems` enum member matching §4.1. |
| `heat_distribution_system` | `heating.emitter` | `underfloor`→`HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING`, `steel_panel_radiators`/`cast_iron`→`HEAT_DISTRIBUTION_SYSTEM_RADIATOR`, absent→floor heating (building-sizer default). |
| `share_of_maximum_pv_potential` | `pv.kWp` | `0.0` if `kWp == 0`, else `1.0` (the rooftop capacity in ArcheTypeConfig carries the size). |
| `use_battery_and_ems` | `battery.kWh` | `kWh > 0` → `True`, else `False`. Battery **size** is auto-sized by the setup; the requested `kWh` is recorded as approximated. |

### 4.4 Occupancy lookup

`occupants` → `lpg_households` via a fixed table (single profile per request, deterministic;
frozen in `hisim/renovisor/mapping.py`):

| `occupants` | LPG household (`utspclient.helpers.lpgdata.Households`) |
|---|---|
| 1 | `CHR07_Single_with_work` |
| 2 | `CHR01_Couple_both_at_Work` |
| 3 | `CHR03_Family_1_child_both_at_work` |
| 4 | `CHR27_Family_both_at_work_2_children` |
| ≥5 | `CHR41_Family_with_3_children_both_at_work` |

`homeOfficeDaysPerWeek` is unmapped in v1 (flagged).

### 4.5 Unmapped fields (v1)

Accepted, flagged in the mapping report, otherwise ignored — to be implemented in HiSim over
time: `targetTempC`, `smartThermostats`, `homeOfficeDaysPerWeek`, `storeys`, all envelope area
overrides (`roofAreaM2`, `wallAreaM2`, `groundFloorAreaM2`, `windowAreaM2`, `doorAreaM2`),
per-element `construction`/`insulation` detail beyond the refurbishment variant, all
`uValueWPerM2K` overrides, `heating.secondary`, `heating.irishCookingRange`,
`heating.seasonalEfficiencyPct`, `heating.flowTempC`, `solarThermal.storageLiters`,
`solarThermal.collectorType`, `solarThermal.collectorAreaM2`, `battery.chemistry`,
`battery.smartCharging`, `pv.inverterAgeYears`, `vehicles` (entire array — no car in the
selected setups), `ventilation`/`air_sealing` measure params beyond the variant bump.

## 5. Simulation parameters

Defaults (overridable via `job.simulationOverrides`):

| Parameter | Default | Rationale |
|---|---|---|
| Period | full year **2019** | Matches the Dublin NSRDB weather dataset year. |
| Resolution | 900 s (15 min) | Same as the building-sizer setups. |
| Post-processing | `COMPUTE_KPIS`, `COMPUTE_OPEX`, `COMPUTE_CAPEX`, `WRITE_KPIS_TO_JSON`, `WRITE_KPIS_TO_JSON_FOR_BUILDING_SIZER` | Produces `all_kpis.json` and `*_kpi_config_for_building_sizer.json`. `postProcessingOptions` overrides are **added** to this set (e.g. `EXPORT_TO_CSV`, `GENERATE_PDF_REPORT` when those files are requested for upload). |

The simulation runs **in-process** (same mechanism as `hisim_main.py`: import the setup module,
call `setup_function`, with `my_module_config` pointing at the generated ModularHouseholdConfig
JSON). One request, one simulation, blocking until done.

## 6. Mapping report

`renovisor_mapping_report.json` is written to the result directory and **always uploaded**,
independent of `job.submission.files`:

```json
{
  "jobId": "abc-123",
  "variant": "measures",
  "translatorVersion": "1.0.0",
  "selectedSetup": "household_heatpump_building_sizer.py",
  "moduleConfig": { "...": "the full generated ModularHouseholdConfig" },
  "fields": [
    { "path": "homeInputs.pv.kWp", "status": "used", "note": "pv_rooftop_capacity_in_kilowatt=8.0" },
    { "path": "homeInputs.heating.emitter", "status": "used", "note": "radiator distribution" },
    { "path": "homeInputs.walls.insulation", "status": "approximated", "note": "folded into TABULA variant .002" },
    { "path": "homeInputs.targetTempC", "status": "ignored", "note": "not yet supported by HiSim" },
    { "path": "homeInputs.storeys", "status": "defaulted", "note": "implied by archetype IE.N.SFH.05" }
  ]
}
```

`status` ∈ `used` | `approximated` | `defaulted` | `ignored`. Every leaf field present in the
request appears exactly once. This is the contract that lets the RenoVisor side see how
faithfully a request was simulated while HiSim fidelity grows.

## 7. Result submission (REST)

**Started:** immediately after successful validation and setup selection (before the simulation
begins), `POST <job.submission.url>` with JSON body:

```json
{
  "jobId": "abc-123",
  "variant": "base",
  "status": "started",
  "translatorVersion": "1.0.0",
  "selectedSetup": "household_heatpump_building_sizer.py",
  "startedAt": "2026-07-05T14:03:00Z"
}
```

The server can use this to distinguish "still running" from "never arrived". A failed `started`
POST is logged but **non-fatal** — the simulation proceeds and the terminal POST is still sent
(no retries for this event beyond one immediate re-attempt).

**Success:** one `POST <job.submission.url>` as `multipart/form-data`:

- Form fields: `jobId`, `variant` (`base`/`measures`), `status` = `"succeeded"`,
  `translatorVersion`.
- One file part per matched file, part name `files`, `filename` = path relative to the result
  directory (forward slashes). The mapping report is always included.
- Header `Authorization: Bearer <authToken>` when a token is present.
- Success = any 2xx response.

**Failure of the simulation** (exit code 3): `POST` the same URL with JSON body instead:

```json
{
  "jobId": "abc-123",
  "variant": "base",
  "status": "failed",
  "stage": "mapping | simulation | collection",
  "errorMessage": "...",
  "logTail": "last ~100 lines of the HiSim log"
}
```

**Upload retries:** 3 attempts with exponential backoff (5 s, 25 s, 125 s) on network errors and
5xx; 4xx is not retried (it's a contract error). If all attempts fail → exit code 4; result
files are left on disk (as with `--keep-files`) so the upload can be repeated manually.

## 8. Package layout

```
hisim/renovisor/
  __init__.py
  __main__.py      # CLI (argparse): run subcommand, exit codes
  spec.md          # this document
  schema.py        # dataclasses for the wrapper envelope + RenoVisor request (dataclasses_json)
  measures.py      # apply measures to a homeInputs copy (§3)
  mapping.py       # setup selection + ModularHouseholdConfig generation + mapping report (§4, §6)
  tabula_ie.py     # IE age-band/variant lookup generated from episcope-tabula.csv (§4.2)
  runner.py        # in-process simulation run with SimulationParameters from §5
  uploader.py      # multipart upload, failure report, retries (§7)
tests/
  test_renovisor_mapping.py    # request → expected setup + ModularHouseholdConfig (pure, fast)
  test_renovisor_measures.py   # measure application semantics
  test_renovisor_upload.py     # multipart + retry behaviour against a local mock server
  test_renovisor_end_to_end.py # marked `system_setups`: full pipeline with --no-upload
```

`mapping.py` must be pure (request in → config + report out, no I/O) so the mapping table can be
unit-tested exhaustively against contract examples.

## 9. Open items

- Remove the unusable-TABULA-row workaround (§4.2) once `simulation_issues.md` item 1 (zero
  door area crash in `Building`) is fixed in HiSim, and restore exact age-band selection.
- `heat_pump` measure `kW` and battery `kWh` sizing: honour requested sizes once the
  building-sizer setups accept explicit sizes instead of auto-sizing.
- Non-IE countries: works wherever a weather `LocationEnum` entry and TABULA rows exist, but
  only IE mapping is verified in v1.
- Per-element U-values, ventilation/air-sealing, target temperature, flow temperature: HiSim
  parameter work, tracked outside this spec; the mapping report keeps the gap visible.
