# RenoVisor measure catalogue v2 — required changes in HiSim

**Status:** draft 2, for discussion
**Date:** 2026-09-13
**Author(s):** assistant (survey, all `[proposed]` items) · Noah Pflugradt (owner)
**Reviewers:** HiSim core team · RenoVisor frontend team (§8.2 only)
**Builds on:** `requirements.md` (the calculation-library requirements; its R/A/Q ids are reused
here unchanged) · `field_inventory.md` · `mockups/measures.schema.yaml` (the previous measure
model, now superseded) · `mockups/heat_pump_household.energy_system.yaml`
**Subject:** `mockups/measures.yaml`, checked in verbatim beside this document
**Code surveyed at:** `4612b899` (identical to `main` for everything cited here)

---

## 1. Summary

RenoVisor sent a catalogue of 33 renovation measures. HiSim must apply any package of those
measures to a home inventory and simulate the result. This document lists what that needs.

The pipeline in `requirements.md` does not change: select a base energy-system file, fill in
values, run, return results. What changes is the vocabulary that the "fill in values" step reads.

Five findings drive the requirements:

1. **Envelope measures state a material and a thickness, not a U-value.** `requirements.md`
   A7 decided the opposite, because HiSim cannot turn a material name into a U-value. Now it
   has to. That needs a conductivity table, a default thickness per measure, and a formula.
   15 of the 33 measures depend on this (§5.1).
2. **Several measures write the same U-value field.** Four measures write the wall U-value,
   five the floor, four the roof. Two insulation layers on one wall must add up, not overwrite
   each other (§5.3).
3. **PV and battery are sized as "percent of roof area" and "days to cover".** HiSim needs
   watts and kilowatt-hours. Both conversions need a rule that does not exist yet (§5.2).
4. **The energy-system format's `variants:` block covers the solar-thermal, DHW and EMS
   choices.** So one base file per heat generator (11) is enough; no file explosion (§5.4).
5. **"Stage 0" measures describe the existing building in the measure vocabulary.** HiSim
   should keep reading the inventory for the base state and ignore them (§5.5).

State of the catalogue against HiSim today (§4.1): 2 measures work as stated, 15 need the
U-value derivation, 7 wait for the P4 component sweep, 9 have no model. 4 of those 9 need
only a new `BuildingConfig` field each.

Two inputs are missing from this repository and must be fetched before implementation:
the current `openapi.yaml` revision and `dependencies.yaml` (§3).

## 2. Scope

**In scope:** what HiSim's translation layer and components must do to apply the 33 measures;
the data structure that holds the measure→HiSim mapping (§7); the changes the catalogue needs
before HiSim can consume it (§8.2).

**Out of scope, unchanged from `requirements.md`:** the container interface (R13), determinism
(R10), `errors.json` (R13.2), the translation report (R7), KPI and cost derivation (R8, R9),
package generation, the HTTP service.

## 3. Sources

| Source | Version | Location |
|---|---|---|
| Measure catalogue | received 2026-09-13 | `mockups/measures.yaml` |
| Calculation-library requirements | 2026-08-27 | `requirements.md` |
| Field inventory | 2026-08-27 | `field_inventory.md` |
| Previous measure model | superseded | `mockups/measures.schema.yaml` |
| RenoVisor API contract | **v0.3.0-draft, 2026-08-27 — stale** | `openapi.yaml` |
| Measure dependency rules | **missing** | `dependencies.yaml` |

**The `openapi.yaml` here is older than the one the catalogue refers to.** The catalogue's
comments cite these contract fields, none of which exist in the copy in this repository:

- `worst_window_glazing_panes` (building inventory)
- `hvo_heating` and the snake_case heating-system values (`heat_pump`, `gas_heating`, …).
  The copy here has `HeatPump`, `GasHeating`, …, `GasSolarThermal`, `HeatPumpSolarThermal`.
- snake_case `heat_distribution_system` values (`surface_heating`, …). The copy here has
  `Floorheating`, `Conventional Radiator`, `Low Temperature Radiator`.
- an `energy_system_config.ventilation_system` block

Every statement below that names an inventory field is provisional until the current contract
is in the repository.

**`dependencies.yaml` is missing.** The catalogue defers to it for which measures may follow
which building state. HiSim needs it to answer two questions: which base-file combinations a
request can reach (§5.4), and whether HiSim must refuse physically contradictory packages
itself or may trust the caller (§5.3).

## 4. The catalogue in numbers

Counted from `mockups/measures.yaml` with a script; the numbers are reproducible.

| | Count |
|---|---|
| Measures | 33 |
| Options (over all measures) | 33 |
| Options at access level `Everyone` | 22 |
| Options at access level `Experts` | 11 (10 × `thickness_in_mm`, 1 × `air barrier`) |
| Measures with no options | 10 (4 written `options:` = null, 6 written `options: []`) |
| Categories | 5: building envelope 16, appliances 6, heating system 5, behaviour 4, ventilation 2 |
| Distinct material strings | 12 |
| Distinct materials after normalising case and spelling | 9 |

### 4.1 What HiSim can do with each measure today

| Group | Count | Measures | What is needed |
|---|---|---|---|
| **Works today** | 2 | `heating installation`, `change room temperature` | Config overrides on existing fields (details below) |
| **Field exists, value must be derived** | 15 | 13 opaque-envelope measures, `window replacement`, `door replacement` | The U-value derivation of §5.1 |
| **Waits for P4** | 7 | `heating system` (4 of 11 values), `hot water system` (heat-pump half), `solar thermal system`, `photovoltaic system`, `battery system`, `air conditioners`, `electric vehicle` | Component conversion (presets/constructors), plus sizing rules for PV and battery (§5.2) |
| **No model** | 9 | `outside shading`, `shallow air tightness measures`, `diy sealing of air leaks`, `ventilation system`, `thermocover for the windows`, `temperature control system`, `replace white appliances`, `install new led lights`, `optimize behaviour for self-consumption of PV` | See §4.2 |

`hot water system` appears in two rows because its two option values differ: `Heat pump` maps
to the hplib heat pump's `with_domestic_hot_water_preparation` (unconverted, P4 B1); `Direct
electric` has no component in HiSim at all (§4.2). It is counted once, in the P4 row:
2 + 15 + 7 + 9 = 33.

**Details on the two that work today:**

- `heating installation` → `HeatDistributionControllerConfig.heating_system`. The controller has
  a `standard` preset, and energy-system files write enum values by member name:
  `FLOORHEATING`, `RADIATOR`, `LOW_TEMPERATURE_RADIATOR`
  (`hisim/components/heat_distribution_system.py:58-60`). The catalogue's `surface_heating` →
  `FLOORHEATING`, `conventional_radiator` → `RADIATOR`, `low_temperature_radiator` →
  `LOW_TEMPERATURE_RADIATOR`.
- `change room temperature` → **two** fields, not one: `BuildingConfig.set_heating_temperature_in_celsius`
  (`building/building.py:154`) and `HeatDistributionControllerConfig.set_heating_temperature_for_building_in_celsius`
  (`heat_distribution_system.py:911`). The building and the controller each read their own copy.
  A measure that writes only one leaves the two out of step.

**Details on `heating system`'s 11 values:**

| Value | State | Note |
|---|---|---|
| `gas_heating`, `oil_heating`, `pellet_heating`, `woodchip_heating`, `hydrogen_heating` | works | `generic_boiler` has one preset per fuel |
| `hvo_heating` | new preset | HVO burns in an oil boiler; needs a new fuel entry (emission factor, price) and a `generic_boiler` preset. Not blocked on P4. |
| `biomass_heating` | decision needed | Pellets and wood chips are both biomass. Either alias it to one of them or ask the frontend what it means. |
| `heat_pump`, `electric_heating`, `district_heating` | waits for P4 | `more_advanced_heat_pump_hplib`, `generic_electric_heating`, `generic_district_heating` are unconverted (B1) |
| `hybrid_heat_pump` | no component | Heat pump plus boiler with a switching strategy. `generic_boiler.py:1035` has a hook for acting as the secondary generator; the heat pump and the strategy do not exist. |

**Details on `electric vehicle`:** `generic_car.CarConfig.for_household` exists, so a car as a
plain consumption profile may be buildable today. Its battery (`advanced_ev_battery_bslib`) and
charger (`controller_l1_generic_ev_charge`) are unconverted, so a car whose charging the EMS
controls waits for P4 B7. Which of the two the product wants decides the row.

### 4.2 The measures with no model

| Measure | Why there is no model | Cost to add one |
|---|---|---|
| `outside shading` | The shading factor `F_sh_vert` is read from the TABULA row (`building/information.py:349`); no config field overrides it | Small: one `Optional[float]` on `BuildingConfig` |
| `shallow air tightness measures`, `diy sealing of air leaks` | The infiltration rate `n_air_infiltration` is read from the TABULA row (`information.py:644`); no override | Small: one field |
| `ventilation system` | The air-change rate `n_air_use` is read from the TABULA row (`information.py:643`); no override, and no heat-recovery term | Small for the rate, medium for heat recovery: one or two fields. `DemandControlledExtract` varies the rate over time, which a scalar cannot express. |
| `hot water system: Direct electric` | No electric water-heater component | Medium: a new component, or a heating-element mode on the DHW storage |
| `thermocover for the windows` | Window U-value would have to change by time of day | No cheap path |
| `temperature control system` | The set-point is a scalar; the difference between a thermostat and a smart control is a schedule | No cheap path |
| `replace white appliances`, `install new led lights` | Household electricity is one LPG profile; there is no appliance or lighting sub-load to change | No cheap path (§9, Q-N4) |
| `optimize behaviour for self-consumption of PV` | Same: the profile is fixed | No cheap path (§9, Q-N4) |

The three "small" fields have the same shape as the ten envelope override fields
`BuildingConfig` already has: `Optional[float]`, `None` means "use the TABULA value". Unset,
nothing changes and the golden suites stay green.

## 5. Findings

### 5.1 Envelope measures need a U-value HiSim has to derive

`mockups/measures.schema.yaml` required `resulting_u_value_in_watt_per_m2_per_kelvin` on every
envelope measure and made material and thickness pricing-only. Its note F8 explains why: HiSim
consumes U-values and cannot derive one from a material id, because the `/materials` endpoint
carries no thermal conductivity. `requirements.md` A7 adopted that.

The new catalogue has no U-value field. Envelope measures carry `material` (Everyone) and
`thickness_in_mm` (Experts). Window and door replacement carry `glazing_panes`. So HiSim must
derive the U-value, and for that it needs:

1. **Thermal conductivity per material.** Nine materials after normalising spelling: EPS, XPS,
   mineral wool, glass wool, PIR, wood fibre, open-cell spray foam, thermal laminate drylining
   board, liquid insulation. The last two are products, not materials; their conductivity
   depends on the product.
2. **A default thickness per measure and material.** `thickness_in_mm` is an Experts option and
   will be absent from most requests. Without a default, a material name determines nothing.
3. **The element's current U-value.** From `envelope_details` when the inventory states it,
   from the TABULA row otherwise. `requirements.md` A8 (which of the two wins) is still open
   and is now an input to a calculation, not just a reporting question.
4. **A formula.** For adding a layer to an existing element: `U_new = 1 / (1/U_old + d/λ)`.
   Whether to add a thermal-bridge surcharge must be decided and written down.
5. **A panes → U-value table** for windows and doors, per country. The catalogue's
   `glazing_panes` ∈ {0, 2, 3} is a product class, not a physical quantity.

Three measures — `add internal dry lining`, `basement internal insulation`,
`top floor ceiling insulation` — have no options at all. For them the derivation needs a fixed
default U-value or a fixed default build-up.

`cavity wall insulation` has a thickness and no material. The thickness is the cavity width,
which is a fact about the building, not a choice. It belongs in the inventory (§8.2, N7).

How the result is consumed is already in place: a non-`None` `BuildingConfig` U-value field
replaces the TABULA area-weighted average for the whole element
(`building/information.py`, `EnvelopeElement.configured_u_value_field`). When configured, the
floor's transmission adjustment factor becomes a fixed 0.5 and wall/roof become 1.0 instead of
the TABULA `b_Transmission` value. That is fine for a slab or a basement ceiling and should be
checked for a suspended floor over a ventilated crawl space.

**Recommendation.** Make the derivation a checked-in data table with a source per row, not
code (§7.2, L2). If the contract later carries a U-value directly, it overrides the table row.

### 5.2 PV and battery arrive as relative sizes

| Measure | Option | Inventory field to write | Missing |
|---|---|---|---|
| `photovoltaic system` | `size in percent of roof area` | `photovoltaics.power_in_watt` | the usable roof area and a W/m² module density |
| `battery system` | `days to cover` | `battery_storage.capacity_in_kwh` | which daily demand, over which period |

**PV.** `PVSystemConfig` already has `share_of_maximum_pv_potential` and applies it as
`power_in_watt × share` in a constructor (`generic_pv_system.py:159`). So the shape of the rule
exists. What is missing is the maximum: the only roof area HiSim knows is
`envelope_details.roof_area_in_m2`, the thermal envelope area of the whole roof. On a pitched
roof roughly half of it faces the wrong way, and none of it is corrected for obstructions.
A rule "usable area = f(roof area, roof form) × W/m²" has to be written and its inputs stated.

**Battery.** `days to cover × daily demand`. Which demand: household electricity only, or
including heat pump and EV? Which day: an annual average or a winter day? The answers differ
by a factor of several. The true daily demand is only known after the simulation, so the rule
must use an a-priori estimate.

**Recommendation.** Both become named laws in the P1 sizing kernel, each with stated inputs.
Both are reported as `approximated`, naming the law. Both conversions happen in the
measure-application step, so the post-measure inventory is in the inventory's own units and
`requirements.md` A6.4 (a package applied to a valid inventory yields a valid inventory) holds.

### 5.3 Fifteen measures write three U-value fields

`BuildingConfig` has one U-value per element. The catalogue has:

| Element | Measures that write it |
|---|---|
| `facade` | external insulation · internal dry lining insulation · cavity wall insulation · add internal dry lining |
| `floor` | basement ceiling insulation · basement internal insulation · basement external insulation · solid ground floor insulation · suspended ground floor insulation |
| `roof` | warm roof insulation · rafter insulation · rolled out attic insulation · top floor ceiling insulation |
| `window` | window replacement |
| `door` | door replacement |

Two consequences:

**Insulation layers must add up.** `requirements.md` A6.3 says a measure states the post-measure
state and the last write wins. That was correct when a measure carried the final U-value. With
material and thickness it is wrong: external insulation plus internal dry lining on one wall are
two layers in series. The rule must be: start from the element's current U-value, add `d/λ` for
every measure on that element, write the result once. Window and door replacement keep the
replacement semantics — a new window replaces the old one.

**Some combinations are physically impossible, and HiSim cannot see it.** The five floor
measures describe three different constructions (unheated basement below, slab on ground,
suspended timber floor). A building has one. `floor_u_value_in_watt_per_m2_per_kelvin` cannot
tell them apart, so a package insulating both a basement ceiling and a suspended timber floor
produces a number. Whether such a package is rejected before it reaches HiSim is
`dependencies.yaml`'s job. Whether HiSim must also refuse it (`requirements.md` R11) needs a
decision (§8.2, N5).

The mapping of the catalogue's measures onto the five thermal elements is the same folding
`measures.schema.yaml` note F5 did for the nine survey elements. It should become a checked-in
table (§7.2, L1) instead of a footnote.

### 5.4 One base file per heat generator is enough

The catalogue removed `gas_solar_thermal` and `heat_pump_solar_thermal` from the generator list.
`solar thermal system` is now its own measure with `supplies` ∈ {`dhw_only`,
`space_heating_only`, `dhw_and_space_heating`}, combinable with any generator. `hot water system`
makes the DHW generator independent of the space-heating generator. Multiplied out as separate
files that would be 11 generators × 4 solar-thermal states × 2 DHW supplies × 2 EMS states = 176.

It does not have to be files. The energy-system format has two switches:

- `groups:` — a named set of components with an on/off flag.
- `variants:` — an exclusive choice between named options. Each option writes its components
  **in full, including their wiring**, and exactly one option is live
  (`hisim/energy_system/groups.py`, module docstring).

`variants:` is what an earlier draft of this document, and `requirements.md` Q4, assumed did
not exist. It ships today: `energy_systems/household_heatpump_building_sizer.grouped.energy_system.yaml`
has an `electricity_management` variant with options `ems_with_battery` and `metered_directly`
that wire the `ElectricityMeter` differently. That is the EMS-less variant `requirements.md` Q4
wanted a second file for.

So the cost is:

| Choice | Cost |
|---|---|
| 11 heat generators | 11 base files |
| solar thermal (4 states) | one `variants:` block in each file |
| DHW supply (2) | one `variants:` block in each file |
| EMS on/off (2) | one `variants:` block in each file (exists) |

Eleven hand-authored files, each with up to 16 combinations. `requirements.md` R1/R2/G2 (every
system a person wrote and reviewed) hold. R4 needs one addition: the library may also write
`variants.<name>.selected` (§8.1, M10).

Three rules of the format constrain how the blocks are written:

1. **A component belongs to at most one variant** (`energy_system/validation.py`, error
   `EF-57`). The `ElectricityMeter` is owned by `electricity_management`; a solar-thermal option
   cannot redefine it.
2. **Inputs from switched-off components are dropped.** A meter or EMS in one variant may list
   `inputs` from a component that another variant only sometimes provides. When that component
   is not selected, the expansion drops the input and records it in the `ExpansionRecord`. This
   is what lets the blocks combine. It is also silent: a mis-wired combination does not fail, it
   runs with a missing feed.
3. **A scalar `sizing_sources` reference to a switched-off component is an error**, not a drop.
   A solar-thermal store that sizes from a generator in another variant will refuse to run.

Consequence: the 176 combinations are a test-coverage question, not an authoring question. Only
one combination runs per request, so CI must cover the others (§9, Q-N3).

### 5.5 "Stage 0" measures describe the existing building

The catalogue says an installed generator "becomes a stage-0 `heating system` measure carrying
its type", likewise for the distribution system. So the existing building is expressible in the
measure vocabulary, and renovations are later stages.

For a dependency engine this is useful: rules like "X may only be installed from state Y" need
both sides in one vocabulary. For HiSim it creates a second description of the base state next to
`HomeInventoryInput`. Two descriptions of one state is the problem `requirements.md` A8 already
records for `retrofit_status` versus `envelope_details`.

**Recommendation.** Write into the contract: `HomeInventoryInput` is the only source for the base
simulation; stage-0 measures are for the dependency engine and HiSim does not read them. Each
variant is the inventory plus one package of stage ≥ 1 measures (§8.1, M9).

## 6. Effect on `requirements.md`

| Item | Status | Reason |
|---|---|---|
| A7 — measures state a U-value; material is advisory | reversed | Catalogue has material and thickness, no U-value (§5.1) |
| A6.3 — replacement semantics | amend | Insulation layers add up; last-write-wins loses a layer (§5.3) |
| A6.4 — closure over the inventory | holds if units convert first | Percent and days must become watts and kWh before the write (§5.2) |
| A6.5 — stable content-derived `measure_id` | holds, but the catalogue has no ids | `display_name` is a label, not a key (§8.2, N1) |
| A6.2 — every measure names its target | dropped by the catalogue | `window replacement` replaces all windows; `electric vehicle: number` adds n identical cars |
| A24 — vehicles need stable ids | moot while A6.2 stays dropped | Nothing names a vehicle |
| A21 — reconcile building-element vocabularies | widened | Third vocabulary: 15 measures over 3 elements (§5.3) |
| Q4 — what selects the base file | answered: 11 files + variants | (§5.4) |
| R4 — library writes only overrides and group flags | add variant selection | (§5.4, M10) |
| AC3 — parametrised file differs from base only in config/constructor/groups | add variant selection | (M10) |
| A11 — given sizes vs. automatic sizing | now required | PV and battery only arrive as relative sizes (§5.2) |
| A8 — envelope precedence | now required | Current U-value is an input to the derivation (§5.1) |
| `mockups/measures.schema.yaml` | superseded | Keep notes F5 and F8 as the record of the reasoning; mark the file superseded |
| Everything else in `requirements.md` §8.1 | unchanged | |

## 7. The measure→HiSim mapping

This is the data structure the translation layer reads. It is the first thing to build (§10).

### 7.1 Requirements on the structure

1. **Effects are of different kinds.** A measure may set a U-value, set an inventory field,
   select a variant option, enable a group, choose the base file, or do nothing.
2. **Some values must be derived.** Material + thickness → U-value; glazing panes → U-value;
   percent of roof → watts; days → kWh.
3. **Experts options need defaults.** 11 options are absent from most requests. Each needs a
   default and a source.
4. **Contributions on one element add up** (§5.3). The structure must hold what each measure
   adds, and resolve once.
5. **Every row must produce a translation-report status** (`requirements.md` R7).
6. **Two vocabularies change underneath.** The catalogue is RenoVisor's file and will be
   revised. HiSim's config field names change with every P4 batch until they freeze at P5
   (`requirements.md` C9). The structure must survive both without a rewrite.

### 7.2 Shape: three tables

```
measure + option values
   │
   ├─ L1  measure_effects.yaml     one entry per measure: what it does, how each option is read
   ├─ L2  envelope_materials.yaml  material → conductivity, default thicknesses
   │      glazing_u_values.yaml    panes → U-value
   ├─ compose per element (§7.3)
   ▼
post-measure HomeInventoryInput     (in the inventory's own units)
   │
   ├─ L3  inventory_bindings.yaml  inventory path → component + config field in the base file
   ▼
config overrides · variant selections · group flags
```

**Rule: L1 and L2 never name a HiSim component or config field.** Measures write the
*inventory*. L3 alone maps inventory paths to HiSim names. A P4 rename then touches one L3 row,
not 33 measure entries. L3 is needed anyway: it is the mapping table `requirements.md` Q3
(recommendation b, "the contract is a façade") calls for, and the base-file parametrisation
uses it with or without measures.

The three tables have different owners and change at different rates: L1 when the catalogue
changes, L2 when the physics data is re-sourced, L3 on every P4 batch.

#### L1 — `measure_effects.yaml`

One entry per catalogue measure. Keyed by a slug; joined to the catalogue on `display_name`
until the catalogue has ids (N1).

```yaml
version: 1
catalogue: mockups/measures.yaml
catalogue_sha256: "…"          # detects a catalogue revision nobody re-checked

measures:

  external_insulation:
    display_name: external insulation
    element: facade
    effect: add_thermal_resistance
    options:
      material:        { kind: lookup, table: envelope_materials }
      thickness_in_mm:
        kind: literal
        unit: mm
        default: 140
        default_source: "SEAI external wall insulation guidance, 2024"
    report: used

  window_replacement:
    display_name: window replacement
    element: window
    effect: set_u_value
    options:
      glazing_panes:   { kind: lookup, table: glazing_u_values }
    report: used

  photovoltaic_system:
    display_name: photovoltaic system
    effect: set_inventory_field
    writes: energy_system_config.photovoltaics.power_in_watt
    options:
      size in percent of roof area: { kind: law, law: pv_power_from_roof_share }
    also: { enable_group: photovoltaics }
    report: approximated

  solar_thermal_system:
    display_name: solar thermal system
    effect: select_variant
    variant: solar_thermal
    options:
      supplies:
        kind: enum_to_option
        map:
          dhw_only:              dhw_only
          space_heating_only:    space_heating
          dhw_and_space_heating: dhw_and_space_heating
    report: used

  change_room_temperature:
    display_name: change room temperature
    effect: set_inventory_field
    writes: building_config.general.set_heating_temperature_in_celsius
    options:
      new room temperature: { kind: literal, unit: degree_celsius }
    report: used
    # L3 maps this one inventory field to both HiSim fields (§4.1)

  install_new_led_lights:
    display_name: install new led lights
    effect: none
    reason: no_appliance_submodel     # a code from the errors.json catalogue (R13.2.2)
    report: ignored
```

#### L2 — physics tables

```yaml
# envelope_materials.yaml
materials:
  eps:
    aliases: [EPS, "EPS Foam"]
    lambda_in_watt_per_m_per_kelvin: 0.038
    source: "EN ISO 10456, tabulated design value"
  mineral_wool:
    aliases: ["Mineral wool", "Mineral Wool", "mineral wool"]
    lambda_in_watt_per_m_per_kelvin: 0.035
    source: "…"
```

The `aliases` list absorbs the catalogue's twelve spellings of nine materials (N2) without
waiting for the frontend to fix them.

`glazing_u_values.yaml`: `glazing_panes` → U-value, per country, from building regulations.

#### L3 — `inventory_bindings.yaml`

```yaml
building_config.envelope_details.roof_u_value_in_watt_per_m2_per_kelvin:
  - { component: building, config: roof_u_value_in_watt_per_m2_per_kelvin }

building_config.general.set_heating_temperature_in_celsius:
  - { component: building,       config: set_heating_temperature_in_celsius }
  - { component: hds_controller, config: set_heating_temperature_for_building_in_celsius }

energy_system_config.photovoltaics.power_in_watt:
  - { component: pv, config: power_in_watt, requires_group: photovoltaics }

energy_system_config.heating_system.heat_distribution_system:
  - component: hds_controller
    config: heating_system
    value_map:                          # enum values are written by member name
      surface_heating:          FLOORHEATING
      low_temperature_radiator: LOW_TEMPERATURE_RADIATOR
      conventional_radiator:    RADIATOR
```

One inventory field may bind to several HiSim fields (the room temperature) — the list form
handles that.

### 7.3 Effect kinds

Closed set, discriminated on `effect`. An unknown kind is an error.

| `effect` | Payload | Result |
|---|---|---|
| `add_thermal_resistance` | element, material, thickness | a ΔR contribution to the element |
| `set_u_value` | element, U-value | the element's new U-value |
| `set_inventory_field` | inventory path, value or law | one inventory write |
| `select_variant` | variant name, option name | `variants.<name>.selected` |
| `enable_group` | group name | `groups.<name>.enabled: true` |
| `select_base_file` | generator | which of the 11 base files |
| `none` | reason code | no change; a translation-report line |

Resolution order within one element: apply all `set_u_value` first (replacement sets the
baseline), then add all `add_thermal_resistance` contributions, then write the element's U-value
once. The intermediate state is `element → (baseline U, list of ΔR)`, not `element → U`. A
structure that stores a final U per element while measures are still being applied reproduces
the bug in §5.3 and shows no symptom.

### 7.4 Alternatives considered

| Shape | Why not |
|---|---|
| A Python function per measure | Conductivities and default thicknesses end up as literals in code. No domain reviewer will read them, and no check can compare them to the catalogue. Formula in code, data in tables. |
| One flat table `(measure, option, value) → change` | Cannot hold different effect kinds, two-stage derivations, or an Experts default with a source. |
| Annotate the base files with the measure that writes each line | Eleven copies of the mapping that drift apart. Keep such annotations as comments; do not make them the source. |
| Add `x-writes` to `measures.yaml`, as `measures.schema.yaml` did | `measures.yaml` is RenoVisor's file and is replaced wholesale on every revision. HiSim's mapping must live in HiSim's file, keyed to theirs. |

### 7.5 Checks (CI)

1. Every measure in `measures.yaml` has exactly one L1 entry, and vice versa.
2. Every option name and every enum value in the catalogue is handled by L1.
3. Every material string in the catalogue resolves to exactly one L2 material via `aliases`.
4. Every Experts option has a `default` and a `default_source`.
5. Every inventory path L1 writes exists in `HomeInventoryInput` (`requirements.md` AC4.3).
6. Every L3 binding resolves in every base file it applies to: the component exists, and its
   config class has the field.
7. Every variant and group L1 names exists in every base file it can be selected with.

Checks 1, 2, 6 and 7 turn "the catalogue changed" and "a component was renamed" into a failing
build instead of silent drift.

## 8. Requirements

`M` = on HiSim. `N` = on the catalogue or the contract; needs the frontend team.

### 8.1 On HiSim (M)

**M1 — U-value derivation is a checked-in table.** `[proposed; §5.1, §7.2 L2]`
(measure, material, thickness) → U-value and glazing panes → U-value are data files with a
source per row. Every measure that offers `thickness_in_mm` has a default thickness. If the
contract later supplies a U-value, it overrides the table.

**M2 — Insulation contributions on one element add up.** `[proposed; §5.3]`
For several measures on one element the resulting U-value is computed from the current
U-value and the added resistance of all of them. Replacement measures (window, door) set the
baseline first. The rule is implemented in one place and tested over every pair the catalogue
allows.

**M3 — Three new `BuildingConfig` fields.** `[proposed; §4.2]`
Air-change rate (or separate infiltration and use rates), external vertical shading factor,
heat-recovery efficiency. Each `Optional`, `None` = TABULA value, so nothing changes when unset.
Unblocks `outside shading`, `shallow air tightness measures`, `diy sealing of air leaks`,
`ventilation system`.

**M4 — Relative sizes are resolved by named sizing laws.** `[proposed; §5.2]`
`size in percent of roof area` and `days to cover` are laws in the P1 sizing kernel with stated
inputs. Both are reported as `approximated` with the law's name.

**M5 — Units are converted before the inventory is written.** `[proposed; §5.2]`
The post-measure `HomeInventoryInput` is in the inventory's own units. No percent or day-count
reaches L3.

**M6 — The mapping is data in the three-table shape of §7.** `[proposed; §7]`
L1 and L2 name no HiSim component or config field. L3 alone does. The seven checks of §7.5 run
in CI.

**M7 — Every Experts option has a default with a source, and omitting it is reported.**
`[proposed; §4]` 11 options. Each default appears in the translation report as `defaulted`.

**M8 — A measure with no model is reported, not dropped.** `[given; R7]`
A package containing one of the nine no-model measures still simulates. The translation report
states that the measure changed nothing, with a reason code.

**M9 — The base simulation is built from the inventory only.** `[proposed; §5.5]`
HiSim does not read stage-0 measures. A variant is the inventory plus stage ≥ 1 measures.

**M10 — The library may write variant selections.** `[proposed; §5.4]`
`requirements.md` R4 lists config overrides, constructor arguments and group flags as the only
permitted writes. Add `variants.<name>.selected`. Amend AC3 (mechanical diff against the base)
to accept it.

**M11 — `change room temperature` writes both set-point fields.** `[proposed; §4.1]`
`BuildingConfig.set_heating_temperature_in_celsius` and
`HeatDistributionControllerConfig.set_heating_temperature_for_building_in_celsius`. This is an
L3 binding with two targets.

**M12 — `hvo_heating` is a `generic_boiler` preset, not a P4 item.** `[proposed; §4.1]`
Add an HVO fuel (emission factor, price) and a preset. Decide what `biomass_heating` maps to.

### 8.2 On the catalogue and the contract (N)

**N1 — Stable ids for measures and enum values.** `[proposed; §7.2]`
`display_name` will be translated and re-worded. `requirements.md` A6.5 needs two clients
describing the same renovation to reach the same `pkg_id`. Add an `id` slug per measure and per
enum value; keep `display_name` as the label.

**N2 — One spelling convention.** `[proposed; §4]`
The catalogue mixes `MechanicalExtract`, `heat_pump`, `Heat pump`, `EPS Foam`. Nine materials
are spelled twelve ways (`EPS` / `EPS Foam`; `Mineral wool` / `Mineral Wool` / `mineral wool`).

**N3 — `options:` null vs. `options: []`.** `[proposed; §4]`
Four measures use the first, six the second. Pick one.

**N4 — State that a measure may be offered which nothing simulates.** `[proposed; §4.2]`
Nine measures have no model. A homeowner comparing a package with `install new led lights` to one
without sees identical results. `requirements.md` A2 and A12 already took the "declare, don't
remove" line for fields and KPIs; say the same here, explicitly.

**N5 — Provide `dependencies.yaml`, and say whether HiSim must also enforce it.** `[proposed; §5.3, §5.4]`
HiSim needs it to know which base-file combinations are reachable and whether to refuse
physically contradictory packages itself.

**N6 — Provide the current `openapi.yaml`.** `[proposed; §3]`
The catalogue refers to fields and values the copy here does not have. Three heating-system
values are new (`hybrid_heat_pump`, `biomass_heating`, `hvo_heating`); the other eight are
snake_case respellings of `hisim.loadtypes.HeatingSystems`. This confirms `requirements.md`
Q3(b): the contract's names are a façade mapped onto HiSim's.

**N7 — Move the cavity width into the inventory.** `[proposed; §5.1]`
`cavity wall insulation`'s `thickness_in_mm` is the cavity width, a survey fact, not a choice.

**N8 — Say what `electric vehicle: number` and `photovoltaic system: size` mean.** `[proposed; §6]`
Post-measure totals (per A6.3), or additions? For a house with one EV and a measure saying
`number: 2`, the two readings differ by a car.

## 9. Open questions

**Q-N1 — One calculation per invocation, or a base plus N variants?**

*Context.* The plan is one base simulation plus one variant per package. `requirements.md` Q10
and R13.5 decided one calculation per container invocation. Weather and LPG inputs are cached on
disk (`hisim/caching`), so the repeated cost per variant is the simulation itself, not the
input loading.

*Options.* (a) The C# service submits base and each variant as separate calculations. Contract
unchanged. (b) A batch mode: one inventory, N packages, N results per invocation. Breaks
one-input-one-result, which the service's caching depends on, and moves the state-reset problem
(C13) inside an invocation.

*Recommendation.* (a). If per-measure attribution is wanted (base + one variant per measure +
the full package), a ten-measure package is twelve simulations; the product should know that
number before it is discovered.

---

**Q-N2 — Who owns the U-value derivation table?**

*Options.* (a) HiSim, as a checked-in table with sources. (b) The contract: `/materials` gains
conductivity and the measure regains a U-value field, as A7 had it. (c) Both: the contract
supplies a U-value when it can, HiSim's table is the fallback.

*Recommendation.* (a) now, (c) as the target. The data is public and small; keeping it in HiSim
removes a dependency on another team. As a data file, (c) costs nothing later.

---

**Q-N3 — How are the variant combinations covered in CI?**

*Context.* 11 files × up to 16 combinations; one runs per request. The drop rule (§5.4, rule 2)
makes a mis-wired combination run instead of fail.

*Options.* (a) Load, validate and resolve every combination; simulate a sampled subset.
(b) Simulate every combination at a short window (`one_week_july` exists for this).
(c) Only the combinations `dependencies.yaml` makes reachable.

*Recommendation.* (a) as the gate, narrowed by (c) once the dependency rules are available.

---

**Q-N4 — Do appliance, lighting and behaviour measures get a model?**

*Context.* `replace white appliances`, `install new led lights`, `optimize behaviour for
self-consumption of PV`, `temperature control system` all need something the LPG profile does
not offer: a separable sub-load or a schedule.

*Options.* (a) Declared and unsimulated, labelled in the report (the A12 precedent).
(b) Scale the household electricity by a factor per measure. (c) Sub-load profiles from the
LPG, if it can produce them — to be checked.

*Recommendation.* (a) for the MVP. (b) changes self-consumption and battery KPIs on an
invented factor. Check (c) before committing further.

## 10. Order of work

0. **Fetch the current `openapi.yaml` and `dependencies.yaml`.** Everything that names an
   inventory field is provisional until then.
1. **L1 and the checks** (§7.2, §7.5). Data plus tests, no simulation. Makes §4.1 executable.
2. **L2 and the composition module** (M1, M2, M5). Pure, unit-tested. Covers 15 measures.
3. **The three `BuildingConfig` fields** (M3). Own PR; golden suites prove nothing changed.
4. **`hvo_heating` preset and the `biomass_heating` decision** (M12). Small.
5. **Base files for the five boiler generators**, without PV, battery, EMS or storages — what
   `field_inventory.md` §6 says is buildable today. Runs the pipeline end to end on a real
   inventory. Variant blocks are added as their components are converted.
6. **The rest waits for P4**: heat pump, electric and district heating (B1), storages (B4), PV
   and battery (B5), car battery, charger, air conditioner, solar thermal (B7).

## 11. Glossary

**Catalogue** — `mockups/measures.yaml`: the closed set of measures a request may name.
**Package** — a chosen subset of the catalogue with chosen option values.
**Stage 0** — the existing building expressed as measures, for the dependency engine. Not read
by HiSim.
**Access level** — `Everyone` or `Experts` on an option. Experts options are absent from most
requests.
**Variant** — the energy-system format's exclusive switch: named options, each with its own
components and wiring, one live.
**Group** — the format's on/off switch for a set of components.
**Effect** — one of the seven things a measure does (§7.3).
**L1 / L2 / L3** — the three mapping tables (§7.2).
