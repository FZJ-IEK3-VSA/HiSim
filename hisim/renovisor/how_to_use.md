# How to use the HiSim RenoVisor translator

This guide is self-contained: it explains how to call the translator, exactly what JSON it
expects, and exactly what HTTP traffic your server will receive. It is written so it can be
handed to a developer (or LLM) building the RenoVisor backend **without** access to the HiSim
repository. The authoritative specification is `spec.md` in the same folder; ready-to-run
example inputs are in `examples/`.

## 1. What it does

The translator is a command-line tool inside the HiSim simulation package. One invocation:

1. reads a **request JSON** (your home inventory + job envelope, described below),
2. picks a matching HiSim simulation setup and generates its parameter file,
3. runs a **full-year household energy simulation** (electricity, heating, DHW, PV, battery),
4. POSTs lifecycle events and the selected **result files** to *your* server URL.

One invocation = one simulation. To compare "house today" vs. "house after renovation" you
invoke it twice with different `--variant` values (same request file) and correlate the two
uploads via your `jobId`s.

## 2. Prerequisites

- A HiSim **source checkout** with `pip install -e .` (the simulation setups are not shipped
  in the wheel). Python ≥ 3.10.
- Runtime per simulation: minutes (first run per building/occupancy combination is slower
  because load profiles are computed and cached).
- Currently verified country: **Ireland** (`countryCode: "IE"`, Dublin weather, Irish TABULA
  building typology). Other countries work only if HiSim has weather + typology data for them.

## 3. Command line

```bash
python -m hisim.renovisor run <request.json> --variant {base|measures} [options]
```

| Option | Meaning |
|---|---|
| `--variant base` | Simulate the house exactly as described in `homeInputs`; `measures` are ignored. |
| `--variant measures` | Apply **all** entries of `measures` to the inventory first, then simulate (one package, not one-by-one). |
| `--result-dir DIR` | Keep results under `DIR` (never auto-deleted). Each run writes into its own `DIR/<jobId>_<variant>/` subdirectory, so the base and measures runs — and unrelated jobs — may share one `DIR`, even when running in parallel. Default: a temp dir, deleted after successful upload. |
| `--no-upload` | Run everything but send no HTTP requests (local testing). Files stay on disk. |
| `--keep-files` | Keep the temp result dir even after a successful upload. |

**Exit codes** (poll these if you wrap the CLI in a job runner):

| Code | Meaning | Did your server hear about it? |
|---|---|---|
| 0 | success | yes — multipart `succeeded` upload |
| 2 | request invalid (bad JSON, missing fields, unknown heating system, unknown country) | **no** — nothing is POSTed; fix the request |
| 3 | simulation failed | yes — JSON `failed` report |
| 4 | upload failed after all retries | tried; result files are left on disk for manual retry |

## 4. The request JSON you must produce

Top level is a **wrapper envelope** around the RenoVisor calc-contract payload (contract 1.x,
camelCase, unknown keys ignored everywhere):

```json
{
  "job": {
    "jobId": "your-correlation-id",
    "submission": {
      "url": "https://your-backend/api/results",
      "files": ["all_kpis.json", "*_kpi_config_for_building_sizer.json"],
      "authToken": "optional-bearer-token"
    },
    "simulationOverrides": {
      "year": 2019,
      "secondsPerTimestep": 900,
      "postProcessingOptions": ["EXPORT_TO_CSV"]
    }
  },
  "request": {
    "contractVersion": "1.0.0",
    "location": { "countryCode": "IE", "region": "Dublin", "eircodeOrPostcode": "D14 AB12" },
    "homeInputs": { "... see below ..." },
    "measures": [ { "type": "heat_pump", "params": { "kW": 8 } } ]
  }
}
```

### 4.1 `job` (added by your backend, not by the frontend)

- `jobId` (required): opaque string; echoed verbatim in every POST you receive. Use distinct
  ids for the base and measures runs of the same user request.
- `submission.url` (required): one endpoint receives everything (started event, result upload,
  failure report).
- `submission.files` (required): glob patterns selecting which result files to upload, matched
  recursively against paths relative to the result directory and against bare filenames.
  The mapping report (section 7) is **always** uploaded even if no pattern matches it.
- `submission.authToken` (optional): sent as `Authorization: Bearer <token>` on every POST.
- `simulationOverrides` (optional): partial overrides. Defaults: year **2019** (matches the
  Dublin weather dataset — don't change it for IE without a matching weather year), timestep
  **900 s**, KPI post-processing enabled. `postProcessingOptions` are *added* to the defaults;
  useful values: `EXPORT_TO_CSV` (time series CSVs), `GENERATE_PDF_REPORT` (add `"report.pdf"`
  to `files` too). Setting `secondsPerTimestep` above 900 is currently not reliable (a known
  HiSim DHW-control instability at hourly resolution).

### 4.2 `request` (the RenoVisor payload, unchanged)

Exactly the calc-contract shape the frontend produces. Required minimum that validation
enforces: `contractVersion` (1.x), `location.countryCode`, `homeInputs.dwellingType`,
`homeInputs.constructionYear` (1700–2100), `homeInputs.floorAreaM2` (> 0),
`homeInputs.occupants` (> 0), `homeInputs.heating.primary`, and `measures` (may be `[]`).
Everything else is optional and gets a documented default.

Key enums the translator understands:

- `dwellingType`: `detached_sfh`, `semi_detached_sfh`, `terraced_sfh`, `bungalow`,
  `apartment`, `other`
- `heating.primary`: `gas`, `oil`, `heat_pump`, `direct_electric`, `district`, `wood`,
  `solid_fuel`, `irish_cooking_range` (the last three are approximated by a pellet-heating
  model and flagged as such)
- `heating.emitter`: `underfloor` → floor heating; `steel_panel_radiators`/`cast_iron` →
  radiators (matters a lot for heat-pump results)
- `solarThermal.mode != "none"`: only effective combined with `gas` or `heat_pump` heating;
  otherwise dropped with a note
- `pv.kWp`, `battery.kWh`: numbers; `0` means none. Battery > 0 enables a battery+energy-
  management system but the size is currently auto-dimensioned (flagged in the report)
- `pv.orientation`: `south`, `south_east`, `south_west`, `east_west` (east/west approximated)
- `envelopeState`: `unrenovated` / `usual_refurb` / `advanced_refurb` — selects the building
  typology's refurbishment level
- `measures[].type`: `heat_pump`, `pv`, `battery`, `solar_thermal`, `roof_insulation`,
  `wall_insulation`, `floor_insulation`, `windows`, `doors`, `air_sealing`, `ventilation`.
  `pv`/`battery` params are **new totals**. Envelope measures currently upgrade the typology's
  refurbishment level (1–2 distinct types → "usual refurb", ≥ 3 → "advanced refurb") rather
  than setting exact U-values.

Fields the simulation cannot honor yet (accepted, reported as `ignored`): `targetTempC`,
`vehicles`, per-element U-value overrides, `seasonalEfficiencyPct`, `flowTempC`, ventilation/
air-sealing params beyond the refurb bump, solar-thermal sizing, `smartThermostats`,
`homeOfficeDaysPerWeek`, `storeys`, envelope area overrides. The mapping report (section 7)
tells you per request which fields were used.

## 5. What your server endpoint must accept

All three request types go to `submission.url`. Distinguish them by `Content-Type` and the
`status` value. Reply with any **2xx** to acknowledge; **5xx** and network errors are retried
(3 retries: 5 s, 25 s, 125 s); **4xx** is treated as a permanent contract error (no retry).

### 5.1 `started` — JSON, right before the simulation begins

```
POST <url>
Content-Type: application/json
Authorization: Bearer <token>          (if configured)

{
  "jobId": "your-correlation-id",
  "variant": "base",
  "status": "started",
  "translatorVersion": "1.0.0",
  "selectedSetup": "household_gas_building_sizer.py",
  "startedAt": "2026-07-05T14:03:00.123456+00:00"
}
```

Delivery is best-effort (one immediate re-attempt): treat it as "running" telemetry, never as a
precondition for accepting the terminal POST.

### 5.2 `succeeded` — multipart/form-data, after the simulation

```
POST <url>
Content-Type: multipart/form-data
Authorization: Bearer <token>

form fields:  jobId, variant, status="succeeded", translatorVersion
file parts:   name="files", one part per file
              filename = path relative to the result directory (posix slashes)
              content-type = application/octet-stream
```

You always receive at least `renovisor_mapping_report.json`; with the recommended `files`
globs you also get `all_kpis.json` (all computed KPIs, JSON) and
`*_kpi_config_for_building_sizer.json` (compact KPI subset). Parse KPI JSONs by key — they
include annual electricity/fuel consumption, heating demand, PV self-consumption, OPEX/CAPEX
figures, etc.

### 5.3 `failed` — JSON, when the simulation crashes

```json
{
  "jobId": "your-correlation-id",
  "variant": "measures",
  "status": "failed",
  "stage": "simulation",
  "errorMessage": "...",
  "logTail": "last ~100 traceback lines"
}
```

A job therefore ends in exactly one of: multipart `succeeded`, JSON `failed`, or silence
(validation error exit 2 / upload failure exit 4 — monitor CLI exit codes for those).

## 6. Typical backend flow

```
frontend request
  └─ backend creates two files: {jobId: "req42-base", ...}, {jobId: "req42-meas", ...}
       ├─ python -m hisim.renovisor run req42.json --variant base
       └─ python -m hisim.renovisor run req42.json --variant measures
            (sequentially or in parallel; each posts started → succeeded/failed)
  └─ backend receives 2 result sets, computes deltas (energy, cost, CO2) for the UI
```

For local development, run with `--no-upload --result-dir ./out` and inspect
`./out/<jobId>_<variant>/`; that directory contains exactly the files that would have been
uploaded, plus everything else the simulation produced.

## 7. The mapping report — read it

`renovisor_mapping_report.json` is the fidelity contract between your inventory and what was
actually simulated:

```json
{
  "jobId": "...", "variant": "measures", "translatorVersion": "1.0.0",
  "selectedSetup": "household_heatpump_building_sizer.py",
  "moduleConfig": { "...the full generated simulation parameter set..." },
  "fields": [
    {"path": "homeInputs.pv.kWp", "status": "used", "note": "PV capacity 8.0 kWp"},
    {"path": "homeInputs.constructionYear", "status": "approximated",
     "note": "TABULA code IE.N.SFH.04.Gen.ReEx.001.002; band 05 ... lacks door/window geometry ..."},
    {"path": "homeInputs.targetTempC", "status": "ignored", "note": "not yet supported by the translator (v1)"}
  ]
}
```

Every leaf of your request appears exactly once with `used` | `approximated` | `defaulted` |
`ignored`. Suggested backend use: surface `approximated`/`ignored` entries as caveats, and log
them in aggregate — they are the prioritized wishlist for simulation improvements.

## 8. Known v1 limitations (summary)

- One weather station per country (IE = Dublin); `region`/postcode don't refine climate yet.
- Battery and heat-pump sizes are auto-dimensioned; requested sizes are recorded, not enforced.
- Envelope detail collapses to a 3-level refurbishment scale; no per-element U-values yet.
- Vehicles/EV charging not simulated; solar thermal only with gas or heat-pump heating.
- Some Irish typology entries have data gaps; affected requests are shifted to the nearest
  usable construction-age band (always visible in the mapping report note on
  `homeInputs.constructionYear`).
- Keep `secondsPerTimestep` at 900 and `year` at 2019 for IE.
