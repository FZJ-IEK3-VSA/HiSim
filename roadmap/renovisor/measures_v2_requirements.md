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
code (§7.2, the materials table). If the contract later carries a U-value directly, it overrides the table.

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
the measure registry (§7.2) instead of a footnote.

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

This is the code the translation layer runs to turn a package into inventory changes and then
into base-file overrides. It is plain Python, in three layers. The only YAML it reads is
RenoVisor's own catalogue, and the only data files it owns are physics constants. An earlier
draft of this section proposed the mapping itself as YAML tables with an interpreter; §7.4 says
why that was dropped.

### 7.1 What it has to do

1. **Apply six kinds of effect.** Set a U-value, set an inventory field, select a variant
   option, enable a group, choose the base file, or do nothing.
2. **Derive some values.** Material + thickness → U-value; glazing panes → U-value; percent of
   roof → watts; days → kWh.
3. **Default the Experts options.** 11 options are absent from most requests; each needs a
   default with a source.
4. **Add up contributions on one element** (§5.3): hold what each measure adds, resolve once.
5. **Report every measure** (`requirements.md` R7): `used`, `approximated`, `defaulted`,
   `ignored`, with the rule that produced the number.
6. **Survive two moving vocabularies.** RenoVisor revises the catalogue; HiSim renames config
   fields on every P4 batch until they freeze at P5 (`requirements.md` C9).

### 7.2 Shape: three layers

```
package (measure ids + option values)
   │
   ├─ measures.py    one function per catalogue measure → effects on the INVENTORY
   ├─ materials.py   conductivities, default thicknesses, glazing U-values (data, with sources)
   ├─ compose        accumulate per element, resolve once            (§7.3)
   ▼
post-measure HomeInventoryInput          in the inventory's own units
   │
   ├─ bindings.py    inventory path → (component, config field, value map) in the base file
   ▼
config overrides · variant selections · group flags
```

**The rule that matters: the measure layer never names a HiSim component or config field.**
Measures write the *inventory*; `bindings.py` alone maps inventory paths to HiSim names. A P4
rename then changes one binding, not 33 measure functions. The bindings table is needed
anyway — it is the façade `requirements.md` Q3 (recommendation b) asks for, and base-file
parametrisation uses it with or without measures.

**`measures.py`** — a registry keyed by the catalogue's measure id (its `display_name` until
N1 gives it a real id), one small function each:

```python
class MeasureRegistry:
    """Maps every catalogue measure to the function that applies it."""

    @staticmethod
    def external_insulation(options: Options, out: Effects) -> None:
        material = Materials.by_alias(options.enum("material"))
        thickness_mm = options.integer("thickness_in_mm", default=140,
                                       source="SEAI external wall insulation guidance, 2024")
        out.add_thermal_resistance(Element.FACADE, material, thickness_mm)

    @staticmethod
    def window_replacement(options: Options, out: Effects) -> None:
        panes = options.integer("glazing_panes")
        out.set_u_value(Element.WINDOW, Glazing.u_value_for_panes(panes))

    @staticmethod
    def photovoltaic_system(options: Options, out: Effects) -> None:
        share = options.integer("size in percent of roof area") / 100
        out.set_inventory_field("energy_system_config.photovoltaics.power_in_watt",
                                law=Laws.PV_POWER_FROM_ROOF_SHARE, argument=share,
                                report=Report.APPROXIMATED)
        out.enable_group("photovoltaics")

    @staticmethod
    def solar_thermal_system(options: Options, out: Effects) -> None:
        out.select_variant("solar_thermal", options.enum("supplies"))

    @staticmethod
    def install_new_led_lights(options: Options, out: Effects) -> None:
        out.no_effect(reason=Reason.NO_APPLIANCE_SUBMODEL)

    BY_ID: ClassVar[Mapping[str, Callable[[Options, Effects], None]]] = {
        "external insulation": external_insulation,
        "window replacement": window_replacement,
        # ... one entry per catalogue measure, 33 in all
    }
```

`Options` reads the request's option values, applies the Experts defaults, and records a
`defaulted` report line for each default used (M7). `Effects` is the accumulator of §7.3.

**`materials.py`** — the physics constants, each with a source. Whether this is a Python table
or a small JSON/CSV file is a matter of repository style, not principle; HiSim keeps its other
curated tables as data files (`cost_database/`, `subsidy_catalog/`, the TABULA CSV), so a data
file is the consistent choice. An `aliases` list per material absorbs the catalogue's twelve
spellings of nine materials (N2).

**`bindings.py`** — inventory path → one or more HiSim targets:

```python
class Bindings:
    """Maps inventory fields to the base-file fields they set."""

    BY_PATH: ClassVar[Mapping[str, Tuple[Binding, ...]]] = {
        "building_config.envelope_details.roof_u_value_in_watt_per_m2_per_kelvin": (
            Binding(component="building", field="roof_u_value_in_watt_per_m2_per_kelvin"),
        ),
        "building_config.general.set_heating_temperature_in_celsius": (
            Binding(component="building", field="set_heating_temperature_in_celsius"),
            Binding(component="hds_controller", field="set_heating_temperature_for_building_in_celsius"),
        ),
        "energy_system_config.photovoltaics.power_in_watt": (
            Binding(component="pv", field="power_in_watt", requires_group="photovoltaics"),
        ),
        "energy_system_config.heating_system.heat_distribution_system": (
            Binding(component="hds_controller", field="heating_system",
                    value_map={"surface_heating": "FLOORHEATING",
                               "low_temperature_radiator": "LOW_TEMPERATURE_RADIATOR",
                               "conventional_radiator": "RADIATOR"}),   # enums by member name
        ),
    }
```

One inventory field may bind to several HiSim fields; the room temperature needs two (§4.1).

### 7.3 Effects and composition

The effect types are a closed set of small frozen dataclasses. mypy checks that a measure
function only produces these; an `assert_never` in the resolver checks that every type is
handled.

| Effect | Payload | Resolves to |
|---|---|---|
| `AddThermalResistance` | element, material, thickness | a ΔR contribution to the element |
| `SetUValue` | element, U-value | the element's new baseline U-value |
| `SetInventoryField` | path, value or (law, argument) | one inventory write |
| `SelectVariant` | variant, option | `variants.<name>.selected` |
| `EnableGroup` | group | `groups.<name>.enabled: true` |
| `SelectBaseFile` | generator | which of the 11 base files |
| `NoEffect` | reason code | no change; a translation-report line |

The accumulator holds `element → (baseline U, list of ΔR)` until every measure has run. Then,
per element: apply the `SetUValue` baseline if any, add every ΔR, write the U-value once. This
is the fix for §5.3. A structure that stores a final U per element while measures are still
being applied loses layers silently.

### 7.4 Why plain Python and not a YAML mapping with an interpreter

An earlier draft proposed the measure→effect mapping as YAML (`effect: add_thermal_resistance`,
`kind: lookup | law | literal`, `value_map`, …) read by an interpreter. It was dropped:

| Argument for YAML | Why it does not hold here |
|---|---|
| Readable by a domain expert who does not read Python | The reviewers are HiSim developers. A dict of conductivities reads the same in either language. |
| A P4 rename touches one row instead of 33 | True, but that comes from the layering (§7.2), which Python has too. |
| CI checks catch catalogue drift and renames | The checks are needed either way (§7.5). Python gets several of them from mypy at edit time; the YAML version re-implements type checking as runtime validation. |
| Data with a source per row | A dataclass field `source: str` is the same. |
| A language-neutral file the C# service could read | Nothing in the contract asks for it; the translation report already says per request what was simulated. A JSON export of the registry can be generated if that ever changes. |

What it would have cost: a small DSL and its interpreter, documented and tested, that has to
grow the moment a measure needs something the vocabulary lacks — which is how the previous
`Measure` model broke when the catalogue changed. `law: pv_power_from_roof_share` would have
been a string naming a Python function, invisible to mypy. One function per measure handles
the odd case in three lines.

### 7.5 Checks

mypy covers the effect types and the registry's signatures. Tests add what mypy cannot see:

1. **Bijection with the catalogue.** Every `display_name` in `mockups/measures.yaml` is a key
   in `MeasureRegistry.BY_ID`, and vice versa. A catalogue revision that adds a measure fails
   the build.
2. **Every option and enum value is handled.** For each measure, every option name and every
   enum value the catalogue lists is accepted by the function (call it with each value; an
   unknown value must raise). This catches `hvo_heating` appearing in the generator list.
3. **Every material spelling resolves** to exactly one material via the alias table.
4. **Every inventory path the measures write exists in `HomeInventoryInput`**
   (`requirements.md` AC4.3) — checked against the OpenAPI schema.
5. **Every binding resolves in every base file it applies to**: the component exists and its
   config class has the field; every variant and group named exists. This is what catches a P4
   rename.

Checks 1, 2 and 5 turn "the catalogue changed" and "a component was renamed" into a failing
build instead of silent drift.


## 8. Requirements

`M` = on HiSim. `N` = on the catalogue or the contract; needs the frontend team.

### 8.1 On HiSim (M)

**M1 — U-value derivation is a checked-in table.** `[proposed; §5.1, §7.2]`
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
reaches the bindings.

**M6 — The mapping is plain Python in the three-layer shape of §7.** `[proposed; §7]`
A registry of one function per catalogue measure writes the inventory; a bindings table maps
inventory paths to HiSim fields; neither the registry nor the materials table names a HiSim
component or config field. The five tests of §7.5 run in CI.

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
binding with two targets.

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
1. **The measure registry and its tests** (§7.2, §7.5). No simulation. Makes §4.1 executable:
   every measure either produces effects or a `NoEffect` with a reason.
2. **The materials table and the composition step** (M1, M2, M5). Pure, unit-tested. Covers 15
   measures.
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
**Effect** — one of the seven effect types a measure produces (§7.3).
**Registry / bindings** — the two code layers of the mapping (§7.2): measure functions that write the
inventory, and the table that maps inventory paths to base-file fields.
