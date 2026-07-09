# HiSim Input Specification — Draft 1 (for review)

What RenoVisor sends to the Python calc service (HiSim + surrogate) to simulate a building:
the **home inventory** as the user entered it, plus a list of **planned renovation measures**.

This document mirrors what the GUI collects today (`HomeInventoryForm.tsx`, `home-options.ts`)
and the wire contract (`packages/shared/src/home-inputs.ts`, `measures.ts`, `calc-contract.ts`).
Per specification.md §8 the payload is deliberately **not a physics model**: we send the user's
raw answers verbatim and HiSim derives U-values, material properties, climate data and load
profiles itself. Unknown keys must be ignored (forward compatibility); new fields are always
optional.

---

## 1. Request envelope

Every call to `/surrogate`, `/simulate` and `/generate-packages` carries the same body:

| Field             | Type     | Required | Notes |
|-------------------|----------|----------|-------|
| `contractVersion` | string   | yes      | Currently `"1.0.0"`. |
| `location`        | object   | yes      | See §2. HiSim picks the climate/weather file from this. |
| `homeInputs`      | object   | yes      | See §3. The building as it stands today. |
| `measures`        | array    | yes      | See §4. Empty array = simulate the baseline ("your house today"). Ignored by `/generate-packages`, which proposes its own measure sets. |

## 2. Location

| Field               | Type   | Required | Example | HiSim use |
|---------------------|--------|----------|---------|-----------|
| `countryCode`       | string (ISO-3166-1 alpha-2) | yes | `"IE"`, `"ES"`, `"NL"` | Country pack: climate region, fuel prices, grid CO₂ factor, energy-label scheme. |
| `region`            | string | no       | `"Dublin"` | Refine weather file / climate zone within the country. |
| `eircodeOrPostcode` | string | no       | `"D14 AB12"` | Optional finer localisation (future: address lookup). |

## 3. Home inventory (`homeInputs`)

All values come straight from the "My home" step. Fields marked *(customize)* or *(expert)*
sit behind the corresponding disclosure in the UI and are frequently absent — HiSim must
have a documented default/derivation for each optional field.

### 3.1 The dwelling

| Field | Type | Required | Allowed values / range | Notes |
|-------|------|----------|------------------------|-------|
| `dwellingType` | enum | yes | `detached_sfh`, `semi_detached_sfh`, `terraced_sfh`, `bungalow`, `apartment`, `other` | Home type. Drives geometry archetype and party-wall assumptions. |
| `constructionYear` | int | yes | 1700–2100 | Baseline for age-typical construction defaults. |
| `floorAreaM2` | number | yes | > 0 | **Heated living area**, not envelope area. |
| `storeys` | int | yes (customize) | 1–6 | Defaults to 2 in the form. |
| `occupants` | int | yes | > 0 | Occupancy profile / DHW demand. |
| `targetTempC` | number | yes | 12–28 | Living-room heating setpoint (form default 21 °C). |
| `homeOfficeDaysPerWeek` | number | no (customize) | 0–7 | Shifts occupancy & electricity profile to daytime. |
| `smartThermostats` | boolean | no (customize) | — | May justify a control/setback credit. |
| `roofAreaM2` | number | no (customize) | ≥ 0 | Envelope area override; otherwise derive from footprint & roof type. |
| `wallAreaM2` | number | no (customize) | ≥ 0 | Envelope area override. |
| `groundFloorAreaM2` | number | no (customize) | ≥ 0 | Envelope ground-floor area — **distinct from `floorAreaM2`**. |
| `windowAreaM2` | number | no (customize) | ≥ 0 | Envelope area override. |
| `doorAreaM2` | number | no (customize) | ≥ 0 | Envelope area override. |

### 3.2 Building envelope

| Field | Type | Required | Allowed values |
|-------|------|----------|----------------|
| `envelopeState` | enum | yes (default `unrenovated`) | `unrenovated`, `usual_refurb`, `advanced_refurb` — coarse chip; the per-element fields below refine it. |
| `roof.construction` | string | yes | `pitched`, `flat`, `room_in_roof` |
| `roof.insulation` | string | yes | `none`, `100mm_ceiling`, `200mm_added_ceiling`, `300mm_ceiling` |
| `roof.uValueWPerM2K` | number | no (expert) | 0–10; **override** — if set, use it instead of deriving from construction/insulation. |
| `walls.construction` | string | yes | `300mm_cavity`, `solid_wall`, `timber_frame` |
| `walls.insulation` | string | yes | `none`, `partial`, `cavity_beads`, `beads_drylining`, `external_100mm` |
| `walls.uValueWPerM2K` | number | no (expert) | Override, as above. |
| `floor.construction` | string | yes | `solid_ground`, `solid_ground_low_pa`, `suspended_timber` |
| `floor.insulation` | string | yes | `none`, `50mm_below_screed`, `100mm_below_screed` |
| `floor.uValueWPerM2K` | number | no (expert) | Override. |
| `windows.type` | string | yes | `single`, `pvc_double_air`, `pvc_double_argon`, `triple_argon` |
| `windows.uValueWPerM2K` | number | no (expert) | Override. |
| `doors.type` | string | yes | `solid_wood`, `composite_insulated`, `glazed_double`, `old_draughty` |
| `doors.uValueWPerM2K` | number | no (expert) | Override. |

Derivation rules:

- HiSim maps (construction, insulation, constructionYear, country) → U-value per element
  unless the expert `uValueWPerM2K` override is present.
- **Per-element fields win over `envelopeState`**: the coarse chip is a GUI shortcut that
  pre-fills the element rows; whenever element-level values are present, HiSim uses them
  and treats `envelopeState` as informational only.
- Country-specific defaults (age-typical constructions, U-values for unknown or missing
  ids) come from the **TABULA** building-typology data tables for the respective country.
  The construction/insulation ids are country-pack-flavoured (currently the Irish catalog);
  the calc service maps the ids it knows and falls back to the TABULA age-band default.

### 3.3 Heating system

| Field | Type | Required | Allowed values / range |
|-------|------|----------|------------------------|
| `heating.primary` | enum | yes | `gas`, `oil`, `solid_fuel`, `heat_pump`, `wood`, `direct_electric` (contract also reserves `irish_cooking_range`, `district`) |
| `heating.secondary` | string \| null | no (customize) | `open_fireplace`, `wood_stove`, `electric_heater` |
| `heating.irishCookingRange` | boolean | no | The "+ Irish cooking range" add-on chip. |
| `heating.emitter` | string | no (customize) | `steel_panel_radiators`, `underfloor`, `cast_iron` — matters for heat-pump flow temps. |
| `heating.seasonalEfficiencyPct` | number | no (expert) | 1–400 (>100 = heat-pump SCOP). Otherwise derive from primary source + age. |
| `heating.flowTempC` | number | no (expert) | 20–90. Design flow temperature. |

### 3.4 Solar thermal

| Field | Type | Required | Allowed values |
|-------|------|----------|----------------|
| `solarThermal.mode` | enum | yes (default `none`) | `none`, `hot_water`, `hot_water_and_heating` |
| `solarThermal.storageLiters` | number | no (customize) | ≥ 0 |
| `solarThermal.collectorType` | string | no (customize) | `flat_plate`, `evacuated_tube` |
| `solarThermal.collectorAreaM2` | number | no (expert) | ≥ 0. If absent, HiSim auto-sizes from mode + occupants (DHW demand). |

### 3.5 Photovoltaics

| Field | Type | Required | Allowed values |
|-------|------|----------|----------------|
| `pv.kWp` | number | yes (default 0) | ≥ 0. Set from the preset chip (none → 0, balcony → 0.8, medium → 3, large → 8) — **authoritative size**. |
| `pv.sizePreset` | string | no | `none`, `balcony_lt1kw`, `medium_1_5kw`, `large_roof` — GUI provenance only. |
| `pv.orientation` | string | no (customize) | `south`, `east_west`, `south_east`, `south_west` |
| `pv.inverterAgeYears` | number | no (customize) | ≥ 0 — degradation / replacement horizon. |

Tilt is not asked; assume roof-typical tilt from `roof.construction` and country.

### 3.6 Battery storage

| Field | Type | Required | Allowed values |
|-------|------|----------|----------------|
| `battery.kWh` | number | yes (default 0) | ≥ 0. Set from preset (none → 0, small → 4, medium → 8, large → 12) — authoritative size. |
| `battery.sizePreset` | string | no | `none`, `small_lt5`, `medium_lt10`, `large_gt10` |
| `battery.chemistry` | enum | no (customize) | `lfp`, `nmc`, `lead_acid` |
| `battery.smartCharging` | boolean | no (customize) | Tariff/PV-aware dispatch vs. naive greedy charging. |

### 3.7 Vehicles

`vehicles` is an array (default `[]`), one entry per car:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `type` | enum | yes | `gasoline`, `electric` (contract also allows `diesel`). |
| `kmPerYear` | number | yes | Annual mileage (form default 15 000). |
| `litersPer100km` | number | gasoline only | Form default 7. |
| `kWhPerKm` | number | electric only | Form default 0.2. **Adds home-charging load** to the electricity profile — interacts with PV, battery and tariff. |

**All vehicles are modelled**, not just EVs: gasoline/diesel cars contribute fuel cost and
CO₂ to the household baseline so that scenarios (e.g. switching to an EV) compare against a
complete picture of the previous solution. Only electric vehicles affect the building's
electricity simulation itself.

## 4. Planned renovation measures (`measures`)

A measure list describes one scenario relative to the inventory above. HiSim applies each
measure to the baseline building model, then simulates. `params` is an open map so options
can grow without a contract change; unknown params are ignored.

| `type` | Meaning | `params` used by the GUI today |
|--------|---------|--------------------------------|
| `roof_insulation` | Add/upgrade roof insulation | `material`: `wood_fibre` \| `sheeps_wool` \| `stone_wool`; `mm`: 100 \| 200 \| 300 |
| `wall_insulation` | Add/upgrade wall insulation | `material`: `cellulose` \| `wood_fibre` \| `eps`; `mm`: 50 \| 100 \| 150 |
| `floor_insulation` | Insulate ground floor | `mm`: 50 \| 100 |
| `windows` | Replace windows | `glazing`: `double` \| `triple` |
| `doors` | Replace external doors | *(no params yet — implies insulated composite door)* |
| `heat_pump` | Replace primary heating with a heat pump | `kW`: 6 \| 8 \| 10 \| 12 (thermal output) |
| `solar_thermal` | Add solar thermal system | *(no params yet — size per DHW demand)* |
| `pv` | Set PV capacity | `kWp`: 3 \| 5 \| 8 \| 12 \| 21 — **new total**, not additional (matches the size-chip UX). |
| `battery` | Set battery capacity | `kWh`: 5 \| 10 \| 15 — **new total**, not additional. |
| `ventilation` | Add mechanical ventilation | `system`: `mvhr` \| `exhaust`; `heatRecoveryPct`: 75 \| 85 \| 92 (MVHR only). |
| `air_sealing` | Airtightness improvement | `targetN50`: 5 \| 3 \| 1.5 — air changes per hour at 50 Pa after sealing. |

Semantics HiSim should apply:

- **Insulation measures** replace the corresponding element's derived U-value with the
  post-measure U-value (existing construction + added `mm` of `material`); the material also
  feeds embodied-CO₂ and summer-comfort (heat-capacity) effects.
- **`windows`** replaces `windows.type` with the chosen glazing standard.
- **`heat_pump`** replaces `heating.primary`; emitter compatibility (`heating.emitter`) and
  post-insulation heat demand determine achievable flow temp and SCOP. If `kW` is absent,
  auto-size from the simulated peak heating load.
- **`pv` / `battery`** set the **new total** system size; any existing capacity in the
  inventory is replaced/extended up to that value. **`solar_thermal`** adds a system sized
  from `collectorAreaM2` if given, otherwise auto-sized from DHW demand (see §3.4).
- **`ventilation` and `air_sealing`** modify the building's ventilation parameters:
  `air_sealing` lowers the infiltration rate (to `targetN50`), `ventilation` replaces
  window/infiltration ventilation with a mechanical system, recovering heat per
  `heatRecoveryPct` for MVHR. Applied together, sealing + MVHR is the standard
  deep-retrofit combination.
- Measures within one request are applied **together** (one package), not evaluated singly.

## 5. Example payload

```json
{
  "contractVersion": "1.0.0",
  "location": { "countryCode": "IE", "region": "Dublin", "eircodeOrPostcode": "D14 AB12" },
  "homeInputs": {
    "dwellingType": "detached_sfh",
    "constructionYear": 1968,
    "floorAreaM2": 157,
    "storeys": 2,
    "occupants": 2,
    "targetTempC": 22,
    "homeOfficeDaysPerWeek": 2,
    "smartThermostats": false,
    "roofAreaM2": 90,
    "wallAreaM2": 120,
    "groundFloorAreaM2": 80,
    "windowAreaM2": 20,
    "doorAreaM2": 4,
    "envelopeState": "unrenovated",
    "roof": { "construction": "pitched", "insulation": "100mm_ceiling" },
    "walls": { "construction": "300mm_cavity", "insulation": "partial" },
    "floor": { "construction": "solid_ground", "insulation": "none" },
    "windows": { "type": "pvc_double_air" },
    "doors": { "type": "solid_wood" },
    "heating": {
      "primary": "oil",
      "secondary": null,
      "irishCookingRange": false,
      "emitter": "steel_panel_radiators",
      "seasonalEfficiencyPct": 90,
      "flowTempC": 55
    },
    "solarThermal": { "mode": "none", "collectorType": "flat_plate" },
    "pv": { "kWp": 0, "sizePreset": "none", "orientation": "south" },
    "battery": { "kWh": 0, "sizePreset": "none", "chemistry": "lfp", "smartCharging": false },
    "vehicles": [
      { "type": "gasoline", "kmPerYear": 15000, "litersPer100km": 7 },
      { "type": "electric", "kmPerYear": 15000, "kWhPerKm": 0.2 }
    ]
  },
  "measures": [
    { "type": "roof_insulation", "params": { "material": "wood_fibre", "mm": 300 } },
    { "type": "wall_insulation", "params": { "material": "cellulose", "mm": 100 } },
    { "type": "windows", "params": { "glazing": "triple" } },
    { "type": "heat_pump", "params": { "kW": 8 } },
    { "type": "pv", "params": { "kWp": 8 } },
    { "type": "battery", "params": { "kWh": 10 } },
    { "type": "air_sealing", "params": { "targetN50": 3.0 } },
    { "type": "ventilation", "params": { "system": "mvhr", "heatRecoveryPct": 85 } }
  ]
}
```

Expected response: `CalcResult` — annual energy cost range, final/primary energy,
CO₂ per m², energy label, heating/cooling comfort, optional peak heating load
(see `packages/shared/src/calc-contract.ts`).

## 6. Resolved decisions

1. **Solar thermal sizing** — collector area becomes an expert-options field
   (`solarThermal.collectorAreaM2`); when absent, HiSim auto-sizes the system.
2. **PV/battery measures** — the measure's `kWp`/`kWh` is the **new total** capacity,
   not an addition to the existing system.
3. **`envelopeState` vs. per-element detail** — filled per-element fields always win;
   the chip is a GUI pre-fill shortcut.
4. **Vehicles** — all vehicles (gasoline, diesel, electric) are modelled so the baseline
   captures the full cost/CO₂ of the previous solution; EVs additionally add home-charging
   load to the electricity simulation.
5. **Country-specific catalogs** — per-country defaults and fallbacks for unknown
   construction/insulation ids come from the TABULA building-typology data tables.
6. **`ventilation` / `air_sealing`** — both become real measures in the UI and in this
   contract, influencing the building's ventilation parameters (infiltration rate,
   mechanical ventilation with heat recovery). See §4.

## 7. Remaining open items

- **`doors` measure** — still has no params or UI; define default assumptions (implies
  insulated composite door) before enabling it in packages.
