# Spec: every device has an installation location derived from the building, its loss is computed against the temperature of that place, and the loss goes where the place says — into the heated zone as a gain, or out of the house

Status: decided (owner, 2026-11-04), nothing is built. Owner: Noah. Epic: hisim-device-location-820d (P2,
`components`). Builds on: `roadmap/complete_energy_sankeys_spec.md` (Q-F, Q-G, the boundary table of §4.3), the open
Sankey stack #1164–#1177 (the building's ports and tagged inputs, #1166; the stack top `heat-pump-explicit-state`
at 1f30a6c05 holds all of it), `roadmap/fluid_loops_spec.md` (D9: an energy port on an input),
`system_docs/component_design_principles.md`, `system_docs/modelling_guide.md`. Revises Q-F of the complete Sankey
spec ("the tanks' standby losses go outdoors") and the default boundary rule of its §4.3 (every `LOSS` port goes to
"outdoors"). Absorbs bead hisim-af8m. Code is cited against main at 185bbd97f and the stack top at 1f30a6c05.

## 1. Problem

Every device loss vanishes into one boundary node, and the tanks compute it against a room that does not exist.

- **Every loss leaves the house.** On the stack top, every `LOSS` energy port whose wiring names no peer goes to the
  boundary node `lt.EnvironmentNode.OUTDOORS` (`EnergyPort.default_boundary`, `hisim/energy_port.py`). In the
  heat-pump twin `household_heatpump_building_sizer` (full year 2021 at 900 s, stack top, `balance_report.json`),
  "outdoors" receives 124,368 kWh: the PV system's module-and-inverter loss 100,609 (one port,
  `ModuleAndInverterLossInWatt`, carrier `SOLAR`), the building's envelope losses 20,081 (ventilation 5,733, opaque
  10,946, windows and doors 3,355, gains lost through windows 47), the buffer's standby loss 673, the hot-water
  tank's standby loss 275, the battery's inverter loss 800 and cell loss 158, and the appliance electricity the
  LoadProfileGenerator does not release indoors 1,772 (waste water, exhaust air, light; not location-dependent). In
  the gas twin the boiler's combustion loss adds 754. The model therefore assumes every device stands in an unheated
  place: the owner's question of 2026-11-04 was whether that is right, and it is not for every house. In an older
  house the heat generator, the tanks and the battery stand in an unheated cellar; in a newer house without a cellar
  they stand in a utility room inside the heated envelope, where their losses heat the house.
- **The tanks' surroundings are a fixed 20 °C.** `SimpleHotWaterStorage` sets
  `self.ambient_temperature_in_celsius = 20.0` (`hisim/components/simple_water_storage.py`, the buffer's `__init__`,
  commented as a basement) and `SimpleDHWStorage.AMBIENT_TEMPERATURE_IN_CELSIUS = 20.0` ("the room the tank stands
  in"); both compute `UA (T_tank − 20 °C)` and book it as a `LOSS` to outdoors. That is inconsistent with either
  location: a tank in a 20 °C room heats that room, and a tank in a cellar loses against the cellar's
  temperature, which is far below 20 °C in winter.
- **One boundary hides what the losses are.** The owner, reading the Sankey: the single "outdoors" sink mixes the
  sunlight the PV modules do not convert (100,609 kWh, four fifths of the node) with the heat the house loses
  (20,081 kWh) and the devices' losses (about 1,900 kWh), so the band says nothing about where the house's energy
  goes.

Beads involved: hisim-af8m (P3, "Where a hot-water tank's and a buffer's standby losses go is configurable"),
absorbed by this spec; related hisim-6id7 (the largest `b_Transmission` of an element's sub-elements), hisim-opxv
(airing), hisim-boiler-auxiliary-losses-pbda (the boiler's standby loss), the epic hisim-energy-sankeys-9uoo.

## 2. Decisions

The owner took every decision below on 2026-11-04. The letters keep the order of the interview.

| ID | Decision | Owner | Date |
|---|---|---|---|
| D-a | One sized field on the building, the technical-room location, derived from TABULA's `Code_CellarCond`; every device's `installation_location` is a sized field that follows it and a file may pin per device | Noah | 2026-11-04 |
| D-b | In an unheated space the loss is computed against a cellar temperature derived from the floor's b-factor, T_cellar = T_in − b (T_in − T_out), and all of it leaves the house | Noah | 2026-11-04 |
| D-c | In the heated zone the loss is computed against the building's indoor air temperature and enters the building as an internal gain through an explicit input carrying an energy port; the device reads the previous step's air temperature | Noah | 2026-11-04 |
| D-d | First step: the buffer, the hot-water tank and the house battery; the PV inverter, the boiler's casing and the car battery later | Noah | 2026-11-04 |
| D-e | The Sankey's "outdoors" splits into "unconverted sunlight" (`LOSS` of carrier `SOLAR`) and "heat loss to outdoors" (every other `LOSS`), chosen by the carrier | Noah | 2026-11-04 |
| D-f | Spec first, then beads; the measurement is part of the first result-moving PR | Noah | 2026-11-04 |

**D-a — one sized field on the building, the technical-room location, derived from TABULA's `Code_CellarCond`;
every device's `installation_location` is a sized field that follows it, and a file may pin it per device**
(owner, 2026-11-04). The house decides where its technical room is, once, from the one source HiSim has about the
house: the TABULA row. A device follows the house unless a file says otherwise (a battery in the garage, a tank
on the roof). The derivation of §4.2: an unheated cellar (`N`) gives an unheated space; a heated cellar (`C`), a
partly heated cellar (`P`) and no cellar (`-`) give the heated zone; `NI` and `0` are Q1 of §10, with the
recommendation heated zone. Rejected: a construction-year cut-off (HiSim has no source for the year of the
house it simulates; the TABULA class is a year band, the row's cellar code is the direct fact), a plain field
with no derivation (every twin would then state it, and RenoVisor's requests do not carry it), and a field per
device only (ten devices would repeat one fact about the house). RenoVisor overrides the building's field once the
calculation request carries the house's basement condition, a contract change on the renovisorissues side
(bead hisim-device-location-820d.8, `external`). Consequence: the building contributes a new sizing fact,
`technical_room_location`; every device of D-d reads it through a sized field.

**D-b — in an unheated space the loss is computed against a cellar temperature derived from the floor's b-factor,
T_cellar = T_in − b (T_in − T_out), and all of it leaves the house** (owner, 2026-11-04). The b-factor is the one
number HiSim already has about the cellar's temperature (§3), and a cellar that gets a tank's heat is still an
unheated space whose ceiling the building's model already counts, through the floor's `U A b`, as a loss toward
the cellar; the heat the tank adds to the cellar does not come back through that ceiling in the model. Rejected:
returning the share `1 − b` of the loss into the house (half a cellar model: it would need the cellar's own heat
balance to be right, and a cellar node is not in the 5R1C model), and a fixed configurable cellar temperature (a
second number to state and source, and wrong in both summer and winter). T_in is the building's indoor air
temperature at the start of the step, not the set point; §4.3 says why. Consequence: a tank in an unheated cellar
loses more in winter than against today's 20 °C (§7) and the loss stays a `LOSS`.

**D-c — in the heated zone the loss is computed against the building's indoor air temperature and enters the
building as an internal gain through an explicit input that carries an energy port; the device reads the previous
step's air temperature** (owner, 2026-11-04). HiSim has no bus (owner decision Q-J of the complete Sankey spec), so
the heat travels on a wire from the device's output to an input of the building; the input carries the port (D9 of
`roadmap/fluid_loops_spec.md`), so the balance pairs the device's heat with the building's as an ordinary
transfer, with nothing special for losses. The building's air temperature answers to the device's heat within the
step, so the device reads the air temperature the previous step converged to, which the building already
publishes (`TemperatureIndoorAirAtStartOfStepInCelsius`, #1166), and there is no loop within a step. Consequence:
the building gets dynamic inputs of carrier `ROOM_HEAT` (§4.4), and a device in the heated zone publishes its heat
as a `PRODUCTION` port of carrier `ROOM_HEAT` instead of a `LOSS` (§4.5).

**D-d — first step: the buffer (`SimpleHotWaterStorage`), the hot-water tank (`SimpleDHWStorage`) and the house
battery (`advanced_battery_bslib.Battery`); later, each its own bead: the PV system's module loss (roof, outdoors)
split from its inverter loss (technical room), the boiler's casing loss split from its flue loss, and the car
battery (garage, normally outside the envelope)** (owner, 2026-11-04). The tanks are the devices whose loss depends
on the surroundings' temperature; the battery's losses do not (bslib's efficiency curves have no temperature), so
only their destination changes. The PV split needs the DC power from pvlib in the PV series, which changes the
PV cache key (P4, bead .5). The boiler has no casing loss today (hisim-boiler-auxiliary-losses-pbda models it; its
destination then follows the location, bead .6). The car battery's default is outdoors (bead .7). Consequence: three
PRs move results (§6); the rest is backlog.

**D-e — the Sankey's single sink "outdoors" splits into two boundary nodes, "unconverted sunlight" for a `LOSS` port
of carrier `SOLAR` and "heat loss to outdoors" for every other `LOSS` port, chosen by the port's carrier in
`EnergyPort.default_boundary`** (owner, 2026-11-04; the owner's own proposal of the same discussion, "split that
into a sunshine-loss and a heat-loss block"). A generic rule, nothing listed per device: a loss of sunlight is
light that was never converted, every other loss ends as heat outside the house. The alternatives, three sinks and
one sink per carrier, are in §9 with the recommendation to keep the two. The PV band's width is unchanged by this
spec: it follows from Q-D of the complete Sankey spec (the PV system starts at the sun). Consequence: the node
`lt.EnvironmentNode.OUTDOORS` is replaced by two nodes (§4.6); `balance_report.json` and the diagram labels change,
no number does.

**D-f — spec first, then beads; the measurement is part of the first result-moving PR** (owner, 2026-11-04). The
expected effect is estimated in §7 from the numbers of §1; the PR of bead .2 measures it on both sizer twins and
writes the table into §7. Consequence: no measurement bead; the verification of §8 lists what is measured.

## 3. Terms

- **Installation location**: where a device stands, one of three values of the new enum
  `lt.InstallationLocation`: `HEATED_ZONE` (inside the building's thermal envelope, the zone the 5R1C model
  simulates), `UNHEATED_SPACE` (an unheated cellar or utility room outside the envelope but under the house),
  `OUTDOORS` (outside the house: a roof, a garage, a wall-mounted inverter). The values are the lowercase words
  `heated_zone`, `unheated_space`, `outdoors`, which the energy-system files spell.
- **Technical-room location**: the building's field of D-a, `BuildingConfig.technical_room_location`, one of
  `HEATED_ZONE` or `UNHEATED_SPACE` (the building refuses `OUTDOORS` for it: a technical room is under a roof); the
  default of every device's installation location.
- **Thermal envelope**: the surfaces whose transmission the building's model counts (floor, walls, roof, windows,
  doors), between the heated zone and the outside, the ground or an unheated space.
- **b-factor, b**: the temperature adjustment factor of an unconditioned space of EN ISO 13789,
  b = (θ_i − θ_u) / (θ_i − θ_e), the fraction of the inside-to-outside temperature difference that the surface
  toward the unconditioned space sees; dimensionless, 0 to 1. TABULA's `b_Transmission_Floor_1` and `_2` are that
  factor for the floor's sub-elements; the building already multiplies the floor's conductance by the largest of
  them (`BuildingInformation._element_u_value_and_adjustment_factor`,
  `hisim/components/building/information.py`), 0.5 in the German rows with a cellar, and 0.5 by its fixed factor
  when the floor's U-value is configured.
- **Cellar temperature, T_cellar** (°C): T_in − b (T_in − T_out), the b-factor's definition solved for the
  unconditioned space's temperature; T_in the building's indoor air temperature at the start of the step, T_out the
  outside air temperature of the step. With b = 0.5: 6.5 °C at T_in 20 °C and T_out −7 °C; 15 °C at T_out 10 °C;
  24.5 °C at T_in 24 °C and T_out 25 °C.
- **Surrounding temperature** (°C): the temperature a device computes its loss against; what its new input
  `SurroundingTemperatureInCelsius` carries: the indoor air temperature at the start of the step in the heated
  zone, the cellar temperature in an unheated space, the outside temperature outdoors.
- **Standby loss** (W): a tank's heat flow to its surroundings, `UA (T_tank − T_surrounding)`, with `UA` in W/K
  the tank's loss coefficient (2 W/(m² K) over 3.66 m² of a 500 l buffer, 7.3 W/K; 0.36 W/(m² K) for the
  hot-water tank). Negative while the surroundings are warmer than the water.
- **Internal gain** (W): heat released inside the heated zone, which the 5R1C model of ISO 13790 (C.3) puts half on
  the air node and the rest on the surface and mass nodes: the occupants' body heat, the appliances' heat, and
  with this spec a device's loss in the heated zone.
- **Energy port, role, carrier**: `EnergyPort(role, carrier)` on an output or an input (`hisim/energy_port.py`),
  the roles `CONSUMPTION`, `PRODUCTION`, `LOSS`, `STORED_CHANGE` (`lt.EnergyRole`), the carriers of
  `lt.EnergyBalanceCarrier`. A component's balance closes over all its ports; a converter turns one carrier into
  another inside its node (complete Sankey spec §4.5).
- **`ROOM_HEAT`**: the carrier of heat released into or lost from the heated zone (Q-G of the complete Sankey spec):
  the internal gains, the building's envelope losses and its thermal mass.
- **Boundary node**: a node of the balance and the Sankey outside the house (`lt.EnvironmentNode`), where a port
  goes when the wiring names no peer: "sun", "ambient heat source", today "outdoors".
- **Sized field, sizing fact**: a configuration field left `AUTO` and resolved by the sizing engine from a rule
  over facts (`sized_field(rule=...)`, `hisim/config/sizing.py`); a fact is a value a configuration contributes for
  others (`SIZING_CONTRIBUTIONS`, the building's `heating_load_in_watt` is one).
- **Dynamic input**: an input a `DynamicComponent` creates at wiring time for each output it reads, found by what
  the output declares rather than by a fixed name (`hisim/dynamic_component.py`; the electricity meter reads every
  electricity port this way, `ElectricityMeter.default_connections_for`).
- **Twin**: a recorded energy-system file under `energy_systems/`; the **sizer twins** are
  `household_heatpump_building_sizer` and `household_gas_building_sizer`, both on TABULA
  `DE.N.SFH.09.Gen.ReEx.001.002` (1995–2001, usual refurbishment, cellar code `N`).

## 4. Design

### 4.1 The vocabulary

`lt.InstallationLocation(str, enum.Enum)` in `hisim/loadtypes.py`, the registry of enums, with the three members
of §3. One enum for the building's field and the devices' fields, so a file spells one word for both; the building
refuses `OUTDOORS` on its own field when it is built (a `ValueError` naming the field), not by a second enum.

### 4.2 The building's technical-room location and its derivation (D-a)

`BuildingConfig` (`hisim/components/building/config.py`) gets

    technical_room_location: Sizable[InstallationLocation] = sized_field(
        rule=TECHNICAL_ROOM_LOCATION_LAW, value_type=InstallationLocation, fields=("building_code",)
    )

a function law over the building's own `building_code` (the `fields=` form of `sized_field`, as the buffer's
`sizing_option` uses it), which reads the row's `Code_CellarCond` through `BuildingInformation` and maps it by the
table below in a pure, tested `@staticmethod` with a class-scoped mapping. Every preset leaves it `AUTO`; a file may
pin it. The resolved value is contributed as the fact `technical_room_location` in `SIZING_CONTRIBUTIONS`, and
`Size.TECHNICAL_ROOM_LOCATION` is its term (`hisim/config/context.py`).

The derivation. `Code_CellarCond` in `hisim/inputs/housing/data_processed/episcope-tabula.csv` (586 German rows:
`N` 493, `-` 36, `0` 24, `NI` 18, `P` 9, `C` 6; all countries 3,281 rows: `N` 1,724, `-` 857, `0` 183, `C` 126,
`NI` 51, `P` 33; the 307 rows without a code have no `Code_BuildingVariant` either, so no building is without a
code). The meanings come from the repository's own note on TABULA's EU.01 geometry method
(`roadmap/renovisor/implementation/storeys_geometry.md`, citing MOBASY 2021 Annex B.1: the codes `-`/N/P/C and
NI/PI) and from the rows themselves (`f_CellarCond`, the heated fraction of the cellar; `n_Storey_effective`
against `n_Storey_effective_envelope`; the floor sub-elements).

| Code | Meaning | Evidence in the rows | `f_CellarCond` | Technical room |
|---|---|---|---|---|
| `N` | unheated cellar, outside the thermal envelope | the floor to the cellar has b = 0.5; the cellar adds no storey to the envelope | 0 | `UNHEATED_SPACE` |
| `C` | heated (conditioned) cellar | the cellar adds one storey to the heated and to the envelope count; b = 0.5 on the cellar floor | 1 | `HEATED_ZONE` |
| `P` | partly heated cellar | adds 0.5 storey (`DE.N.MFH.03`, `TH.08`, `TH.10`) | 0.5 | `HEATED_ZONE` (the technical room is in the heated part; an owner assumption) |
| `-` | no cellar (`storeys_geometry.md` maps the request's `none` to it) | `A_Floor_1` = 0, the floor is `Floor_2` "Soil" with b = 0.5 (`DE.N.SFH.11`, `.12`, `MFH.11`, `.12`) | 0 | `HEATED_ZONE` |
| `NI` | unheated cellar inside the insulated envelope (inferred: the cellar adds a storey to the envelope count but not to the heated one, the floor to the cellar has b = 0, the cellar's floor to the soil b = 0.5; `DE.N.TH.11`, `.12`); **unverified** against MOBASY Annex B.1 | as stated | 0 | Q1 of §10; recommendation `HEATED_ZONE` |
| `0` | only on the synthetic-average rows (`DE.N.SFH-TH.01-06.Gen.SyAv.*`, `MFH-AB.*`, `DE.TestRegion.*`: `n_Storey` = 0, no geometry), the owner read it as "no cellar"; **unverified** | b = 0.5 on `Floor_1` like an `N` row; the rows average classes that are nearly all `N` | 0 | Q1 of §10; the owner's mapping `HEATED_ZONE` until decided |

A code outside the table refuses the build (fail loudly), so a new TABULA export cannot slip a meaning past the
table. Every German class up to `.10` (to 1994 for single-family houses, 2001 for `SFH.09`) is `N`; `.11` and `.12`
(2002 on) are `-` or `NI`: the derivation gives the "older house with a cellar, newer house without" the owner
described, from the row and not from a year.

### 4.3 The cellar temperature (D-b)

The building, which owns the b-factor and the outside temperature, publishes the cellar temperature; no device
learns the b-factor (a model coefficient of another component crosses no port, `component_design_principles.md`
B 2). New output `TemperatureUnheatedCellarInCelsius` (°C), every step:

    T_cellar = T_air,start − b_floor (T_air,start − T_out)

with `T_air,start` the building's indoor air temperature at the start of the step (its state, the value it
publishes as `TemperatureIndoorAirAtStartOfStepInCelsius`), `T_out` the step's outside temperature (its weather
input) and `b_floor` the floor's transmission adjustment factor as the building already uses it
(`BuildingInformation.element_values()["floor"].adjustment_factor`: the largest `b_Transmission_Floor_*` of the
row, or the fixed 0.5 when the floor's U-value is configured). A b outside [0, 1] refuses the build. The formula is
a pure `@staticmethod` with its own tests. It is published for every building, whatever its technical-room
location; a device reads it only when its location is an unheated space.

Why T_in is the start-of-step air temperature and not the set point. Both are the building's own quantities, so
neither costs a wire. The air temperature follows the house: in the heating season it sits within the thermostat's
band of the set point, where 0.5 K in T_in moves a 50 °C buffer's loss against a 7 °C cellar by 0.6 %; in summer
the house stands at 24–28 °C while the set point says 20 °C, and a cellar under a 26 °C house is not colder than
under a 20 °C one. The start-of-step value is fixed within the step, so the cellar temperature does not change
from pass to pass and no device feeds back into it within the step (`component_design_principles.md` B 5). What
the published air temperature is while the occupants air the house is hisim-opxv's business; until it lands the
building's state carries the value it publishes.

The b-factor is a seasonal mean; using it step by step makes the cellar follow the outside air without the
ground's and the cellar's own inertia. That is the limit of the model and is stated in the output's description;
a cellar node is not in scope (D-b, rejected alternatives).

### 4.4 The building's heat inputs from devices (D-c)

`Building` becomes `class Building(DynamicComponent, PhysicsComponent)`, as the electricity meter is a
`DynamicComponent` and a `MeterComponent`. It overrides `default_connections_for(source)` the way the meter does:
for every output of the source that declares `EnergyPort(PRODUCTION, ROOM_HEAT)` and that none of the building's
named inputs already reads (`HeatingByResidents`, `HeatingByDevices` read the occupancy's two `ROOM_HEAT` ports by
name), one dynamic connection, in W. Each dynamic input carries `EnergyPort(CONSUMPTION, ROOM_HEAT)`, so the
transfer is paired on the wire (D9 of the fluid loops spec); for that `DynamicComponent.add_component_input_and_connect`
gets a keyword-only `energy_port=` it passes to the `ComponentInput`, as `add_input` does. The duplicate-feed
refusal of `add_component_input_and_connect` stays the guard against reading one output twice.

In `i_simulate` the building sums its dynamic inputs into the internal gains beside the occupancy's two, so the
device heat takes the ISO 13790 C.3 split (half to the air node, the rest to the surface and mass nodes, less the
share lost through the windows), as every internal gain does. New output `HeatReleasedByDevicesInWatt` (W), the
sum, for the report; it carries no port (the inputs carry them). The building's balance closes as it does on
#1166: the new consumption enters the same equations the occupancy's gains enter.

Why dynamic inputs found by the port, and not one named input per device or a tag per device class. A named input
per device would make the building list the devices of the system (a buffer input, a tank input, a battery input,
two for a second battery), and every new device would need a new input on the building. A tag of
`lt.InandOutputType` per device would say in a second place what the port already says. The port
`EnergyPort(PRODUCTION, ROOM_HEAT)` is the one statement "this output is heat released into the heated zone", and
the building reads every output that makes it, from any class, as the meter reads every electricity port. In a
declarative file the loader asks `default_connections_for` for the bare items, so a twin needs no edit to wire a
device in the heated zone; a recorded twin shows the resolved inputs.

### 4.5 The device side: the location, the surrounding temperature and the port (D-b, D-c, D-d)

Each device of D-d gets, in its configuration:

    installation_location: Sizable[InstallationLocation] = sized_field(
        rule=Size.TECHNICAL_ROOM_LOCATION, value_type=InstallationLocation
    )

`AUTO` in every preset, so it follows the building's technical-room location; a file pins it to any of the three
values (`installation_location: outdoors` for a battery in the garage). A device with no building in the system
and the field `AUTO` fails the sizing with the fact's name, as every unresolved fact does.

**The surrounding temperature.** The tanks get one new mandatory input `SurroundingTemperatureInCelsius` (°C) and
compute their standby loss against it instead of the fixed 20 °C: `SimpleHotWaterStorage` passes it where
`self.ambient_temperature_in_celsius` goes today (its node step, `t_amb_c=`); `SimpleDHWStorage` moves
`ambient_temperature_in_celsius` out of the frozen `DhwTankProperties` into the step's inputs, so the properties
stay the constants of the tank. The default connection follows the location:

| Location | Source of `SurroundingTemperatureInCelsius` |
|---|---|
| `HEATED_ZONE` | `Building.TemperatureIndoorAirAtStartOfStepInCelsius` |
| `UNHEATED_SPACE` | `Building.TemperatureUnheatedCellarInCelsius` |
| `OUTDOORS` | `Weather.TemperatureOutside` |

The battery declares no such input: bslib's losses do not depend on temperature (D-d).

**The port.** Each loss output keeps its name and its number (the heat leaving the device, W, negative in a step
whose surroundings are warmer) and gets its port from the location, through one shared pure function in
`hisim/energy_port.py` (three callers: both tanks, the battery):

    EnergyPort.for_heat_released(location, carrier)
        HEATED_ZONE    -> EnergyPort(PRODUCTION, ROOM_HEAT)
        UNHEATED_SPACE -> EnergyPort(LOSS, carrier)
        OUTDOORS       -> EnergyPort(LOSS, carrier)

So a tank in the heated zone converts `SPACE_HEATING_HEAT` (or `DOMESTIC_HOT_WATER_HEAT`) into `ROOM_HEAT` inside
its node, which the balance allows (the component's total closes, the per-carrier totals are reported, complete
Sankey spec §4.5), and the building reads the output as §4.4 says. In an unheated space and outdoors the port is
a `LOSS` of the device's own carrier and goes to "heat loss to outdoors" (§4.6) as today, and nothing reads it.
The `LOSS` role is kept for those cases by the same function; no component checks the topology, and the balance
has no case for losses: a `PRODUCTION` port with a reader is a transfer, a `LOSS` port goes to its boundary.

The outputs: `SimpleHotWaterStorage.StandbyHeatLoss` (carrier `SPACE_HEATING_HEAT`),
`SimpleDHWStorage.StandbyHeatLoss` (`DOMESTIC_HOT_WATER_HEAT`), `Battery.InverterLossInWatt` and
`Battery.CellLossInWatt` (`ELECTRICITY`). Their names stay (the KPIs "Standby heat loss of Hot water storage" and
"Standby heat loss of DHW storage" read them, and a name is not renamed for this rule alone, AGENTS.md); each
description states the destination the location gives. The battery's two outputs become two `ROOM_HEAT`
`PRODUCTION` ports in the heated zone, two dynamic inputs on the building.

Rejected for the port: two outputs per device (a `LOSS` and a `ROOM_HEAT` `PRODUCTION`, one always zero and idle,
`PortSeries.is_idle`): one column more per device and a reader that reads zeros; the role from the location is one
statement at build time.

### 4.6 The Sankey's two sinks (D-e)

`lt.EnvironmentNode.OUTDOORS` ("outdoors") is replaced by `UNCONVERTED_SUNLIGHT` ("unconverted sunlight") and
`HEAT_LOSS_TO_OUTDOORS` ("heat loss to outdoors"). `EnergyPort.default_boundary`: a `LOSS` port of carrier `SOLAR`
goes to the first, every other `LOSS` port to the second; a port that names its `environment` goes there as today.
The boundary table of the complete Sankey spec §4.3 is revised accordingly (its `LOSS` column reads "unconverted
sunlight" for `SOLAR`, "heat loss to outdoors" for every other carrier). The docstrings and the tests that assert
"outdoors" (`tests/test_energy_balance.py`) follow. In the heat-pump twin the two sinks receive 100,609 kWh and
23,759 kWh today, and after the tanks and the battery follow the location (§7) the second changes by their share.

### 4.7 RenoVisor (D-a)

The translator (`hisim/renovisor/translate.py`) writes nothing for the field until the calculation request carries
the house's basement condition (the `basement_condition` field planned in `storeys_geometry.md`, bead
hisim-envelope-geometry-4vho.1, `external`): the building's `AUTO` field then gives the TABULA row's answer, which
the mapping report states. Once the request carries it, the translator writes
`Building.config.technical_room_location` from it (`none`, `heated`, `partly_heated` → `heated_zone`; `unheated` →
`unheated_space`) and the mapping report says which source decided (bead hisim-device-location-820d.8). A request
field naming the technical room's location itself is a contract question for the same issue; this spec posts
nothing there.

## 5. What changes and what does not

- **Results.** Every twin with a buffer or a hot-water tank moves once the tanks follow the location (PR 2):
  with cellar code `N` (every German class to `.10`, both sizer twins) the default is an unheated space, the
  surroundings fall from a fixed 20 °C to the cellar temperature (6.5 °C at −7 °C outside), the standby losses
  rise in winter and the generators cover them; a twin that pins `heated_zone`, or a house on a `-`, `C` or `P` row,
  sends its tank losses into the building and heats less. The battery's PR moves only the heated-zone cases. 60 s
  results move too: the surrounding temperature has no step-length branch. The Sankey PR moves no number.
- **Public interfaces.** New: `lt.InstallationLocation`; `BuildingConfig.technical_room_location` and the sizing
  fact `technical_room_location` (`Size.TECHNICAL_ROOM_LOCATION`); the building's outputs
  `TemperatureUnheatedCellarInCelsius` and `HeatReleasedByDevicesInWatt` and its dynamic `ROOM_HEAT` inputs;
  `installation_location` on `SimpleHotWaterStorageConfig`, `SimpleDHWStorageConfig` and `BatteryConfig`; the
  tanks' input `SurroundingTemperatureInCelsius`; `EnergyPort.for_heat_released`; `energy_port=` on
  `DynamicComponent.add_component_input_and_connect`; the boundary nodes `UNCONVERTED_SUNLIGHT` and
  `HEAT_LOSS_TO_OUTDOORS` in place of `OUTDOORS` (a `balance_report.json` and diagram label change). Renamed: nothing.
  Heads-up on renovisorissues (`system_docs/results_and_interfaces.md` §5): every package's tank losses move with
  PR 2, and result.json's standby-loss KPIs with them.
- **Goldens and twins.** Re-blessed through `golden-update.yml` with PR 2 and again with PR 3 (two small PRs
  rather than one, AGENTS.md); every recorded twin with a building is re-recorded with PR 1 (the resolved
  `technical_room_location` is spelled out, as every sized field is) and with PR 2 and 3 (the resolved
  `installation_location`, the new inputs).
- **Unchanged.** The buildings' envelope losses and the appliance electricity not released indoors stay losses to
  outdoors; the PV loss stays one port until bead .5; the boiler's combustion loss stays a flue loss; the occupancy's
  gains and their wiring; the KPI names; the energy-balance check itself (no new case).

## 6. Implementation plan

Pull requests, in this order, one bead each (AGENTS.md); PR 4 is independent of the others and may go at any time.

| PR | Bead | Content | Tests | Results |
|---|---|---|---|---|
| 1 | hisim-device-location-820d.1 (components, P2) | `lt.InstallationLocation`; the building's `technical_room_location`, its law over the TABULA row with the table of §4.2 and the refusal of an unknown code or of `OUTDOORS`; the fact and `Size.TECHNICAL_ROOM_LOCATION`; `TemperatureUnheatedCellarInCelsius` (§4.3); the building as a `DynamicComponent` with `default_connections_for` by the `ROOM_HEAT` `PRODUCTION` port, dynamic inputs carrying ports, `HeatReleasedByDevicesInWatt` (§4.4); `EnergyPort.for_heat_released` (§4.5) | the table of §4.2 row by row (each German class, `N` → unheated, `-`/`C`/`P` → heated, a foreign code refused); the cellar formula at the three worked points of §3 and b outside [0, 1] refused; a test double with a `ROOM_HEAT` `PRODUCTION` output wired by default connection, its heat in the building's gains and both sides booking the same column; the occupancy's ports not read twice; `for_heat_released` for the three locations | no number moves; the new output columns; recorded twins re-recorded for the spelled-out field |
| 2 | hisim-device-location-820d.2 (components, P2; absorbs hisim-af8m) | `installation_location` on both tanks; `SurroundingTemperatureInCelsius` with the default connections of §4.5; the standby loss against it in both node steps; the port by location; the output descriptions; the measurement of §7 | a buffer in an unheated space at 50 °C and a 6.5 °C cellar loses `UA · 43.5` W; the same buffer in the heated zone loses against the start-of-step air temperature and the building books it; a buffer pinned outdoors reads the weather; an `AUTO` location without a building fails naming the fact; both tanks' balances close in all three locations; the sizer twins' balance closes every step | every tank twin moves; goldens re-blessed; §7 filled |
| 3 | hisim-device-location-820d.3 (components, P2) | `installation_location` on the battery; its two loss outputs' ports by location | the battery in the heated zone hands both losses to the building and its balance closes; outdoors it books two losses | the heated-zone twins move; goldens re-blessed |
| 4 | hisim-device-location-820d.4 (postprocessing, P2) | the two sinks of §4.6 in `lt.EnvironmentNode` and `default_boundary`; the Sankey spec's table revised; docstrings and tests | `default_boundary` of a `SOLAR` `LOSS` and of every other `LOSS`; a run's report naming no "outdoors" | labels only |
| later | hisim-device-location-820d.5 (components, P4) | the PV system's module loss (outdoors) and inverter loss (its installation location) as two ports; needs the DC power from pvlib in the PV series, a PV cache key change | | the heated-zone twins move |
| later | hisim-device-location-820d.6 (components, P4; after hisim-boiler-auxiliary-losses-pbda) | the boiler's casing loss follows its installation location; the flue loss stays outdoors | | |
| later | hisim-device-location-820d.7 (components, P4) | the car battery's `installation_location`, default `outdoors` | | none by default |
| later | hisim-device-location-820d.8 (renovisor, P3, `external`; after hisim-envelope-geometry-4vho.1) | the translator writes `technical_room_location` from the request's basement condition (§4.7) | | RenoVisor packages with a stated basement condition |
| decision | hisim-device-location-820d.9 (question, P3, `owner-decision`) | Q1 of §10: the meaning and mapping of `NI` and `0` | | |

## 7. Expected effect and measured results

**Estimate from the stack-top balance reports (§1), full year 2021 at 900 s.** Heat-pump twin: the tanks lose
948 kWh and the battery 958 kWh a year, 1,906 kWh together, against a space-heating consumption of the building of
11,663 kWh.

- *The default on the sizer twins (cellar code `N`, unheated space).* The tanks' surroundings go from 20 °C to the
  cellar temperature: with b = 0.5 the cellar sits at 15 °C at 10 °C outside and 6.5 °C at −7 °C, so a buffer at
  40–50 °C loses 20–45 % more in the heating season and about the same in summer. Expected: the buffer's and the
  tank's standby losses rise by roughly 250–350 kWh a year together, the heat pump's space-heating and hot-water
  heat by the same, its electricity by about 80–100 kWh; the building does not move. The gas twin: about 450 kWh
  of tank losses today, +100–150 kWh, the boiler burns it.
- *A house in the heated zone (a `-`, `C` or `P` row, or `heated_zone` pinned in the file).* The 1,906 kWh enter
  the building as internal gains; the heating season's share (the tanks lose evenly over the year, the battery
  mostly in the PV months) is about 1,100–1,300 kWh, which the 5R1C model turns into 9–11 % less space heating
  (the owner's "about 10 %"); the rest arrives in summer and raises the air temperature, which the air-conditioner
  twin pays as cooling.

**Measured** (to be written by PR 2 and PR 3, both sizer twins, full year at 900 s, and one twin pinned to
`heated_zone` and one to `outdoors`): the standby losses of both tanks, the battery's losses, the building's
space-heating consumption, the heat pump's electricity and SCOP, the gas burnt, the hours above 26 °C and the
cooling demand, the building's balance residual, the number of moved goldens.

| Twin | Location | Tank losses kWh | Battery losses kWh | Space heating kWh | HP electricity kWh / SCOP | Gas kWh | h > 26 °C | Cooling kWh |
|---|---|---|---|---|---|---|---|---|
| – | – | – | – | – | – | – | – | – |

## 8. Verification

- **Pure functions, tested alone**: the cellar-code table of §4.2 (every code, the refusal), the cellar
  temperature at the worked points of §3 and its refusals, `for_heat_released`, the tanks' standby loss against a
  surrounding temperature (positive, negative, equal).
- **Component tests**: the table of §6. A device in the heated zone and the building book the same column (the
  balance's transfer check, one wire); the building's balance closes every step with device inputs; a tank's and the
  battery's balance closes in each of the three locations; the occupancy's ports are not read twice; a device with
  `AUTO` and no building fails naming the fact; a building refusing `OUTDOORS` for its technical room.
- **Energy balance**: `balance_report.json` of both sizer twins closes every step after PR 2 and PR 3; no port
  unlinked; no node "outdoors" after PR 4.
- **Step length**: the same January week at 60, 900 and 3,600 s on the heat-pump twin pinned to `heated_zone`: the
  tanks' losses and the building's heat within 1 % of the 60 s run (`modelling_guide.md` §3).
- **Effect**: the measurement of §7 against its estimate; the summer overheating hours and the cooling demand of
  `simple_air_conditioner_household_building_sizer` pinned to `heated_zone`.
- **Goldens**: every tank twin through the golden gate after each re-bless; the moved KPIs listed in the PR
  description (`results_and_interfaces.md`).
- **RenoVisor**: `tests/renovisor/` base tests unchanged until bead .8; the mapping report states the location's
  source.

## 9. Rejected alternatives

- A construction-year cut-off for the default location: no source for the year of the simulated house; the TABULA
  row's cellar code is the direct fact (D-a).
- A plain location field without derivation, or a field per device only (D-a).
- The share `1 − b` of a cellar device's loss returning into the house: half a cellar model (D-b).
- A fixed configurable cellar temperature: a second sourced number, wrong in both seasons (D-b).
- The set point as T_in of the cellar formula: no wire saved, and a cellar under a 26 °C summer house computed as
  under a 20 °C one (§4.3).
- A bus or a summing component between the devices and the building: HiSim has no bus (Q-J); a `SumBuilder` would
  be a pass-through physics component in every file (D-c).
- One named input per device on the building, or a tag per device class: the building would list the system's
  devices (§4.4).
- Two outputs per device, one always zero (§4.5).
- Three sinks (unconverted sunlight; the devices' and the boiler's conversion losses; the envelope's heat loss):
  readable, but "conversion loss" is not a property of a port, so the rule would need a list of which losses are
  conversions, per device; rejected for the two sinks by carrier (D-e). Recommendation kept if the owner revisits:
  the two sinks, and the per-loss nodes of the graph (complete Sankey spec §4.2, 6) for reading one loss where it
  occurs.
- One sink per carrier ("electricity loss", "natural gas loss", ...): as many sinks as carriers, most of them a
  few hundred kWh, and the envelope's 20 MWh `ROOM_HEAT` beside a 158 kWh "electricity loss" band; rejected (D-e).
- A cellar thermal node (ground coupling, the cellar's own mass): not in the 5R1C model; the b-factor is the
  model's statement about the cellar (§4.3).

## 10. Open questions

- **Q1. The meaning of the cellar codes `NI` and `0`, and their mapping** (bead hisim-device-location-820d.9,
  `owner-decision`, P3). `NI` (18 German rows, `DE.N.TH.11` and `.12`, 2002 on; 51 rows in all) reads from the
  rows as an unheated cellar inside the insulated envelope: it adds a storey to the envelope count and none to the
  heated one, the floor above it has b = 0, its own floor to the soil b = 0.5. `0` (24 German rows) appears only on
  the synthetic-average rows without geometry (`SyAv`, `n_Storey` = 0), with b = 0.5 on the floor like an `N` row,
  averaging classes that are nearly all `N`. Neither meaning could be verified against MOBASY 2021 Annex B.1 (not in
  the repository; a web search found nothing). Options for `NI`: (a) `HEATED_ZONE`: a cellar inside the insulation
  keeps the heat in the envelope, and the building's floor b (the largest of the sub-elements, 0.5 from the soil
  floor) would otherwise give the wrong cellar temperature; (b) `UNHEATED_SPACE`: the cellar is unheated, and
  the loss leaves as for `N`. Options for `0`: (a) `HEATED_ZONE`, the owner's reading "no cellar"; (b)
  `UNHEATED_SPACE`, the population the rows average. Recommendation: (a) for `NI`, and (a) for `0` as the owner
  said, both marked in the table as unverified, to be amended when the document is checked. The rows affected are
  the two newest terraced-house classes and the synthetic averages, none of them a twin.

## 11. Beads

Epic hisim-device-location-820d (P2, `components`).

| Bead | Content | PR |
|---|---|---|
| hisim-device-location-820d.1 (P2, components) | PR 1 of §6: the building's location, derivation, cellar temperature, dynamic `ROOM_HEAT` inputs, the port helper | – |
| hisim-device-location-820d.2 (P2, components) | PR 2: the buffer and the hot-water tank follow the location; the measurement of §7; absorbs hisim-af8m | – |
| hisim-device-location-820d.3 (P2, components) | PR 3: the battery's losses follow the location | – |
| hisim-device-location-820d.4 (P2, postprocessing) | PR 4: the Sankey's two sinks (D-e) | – |
| hisim-device-location-820d.5 (P4, components) | the PV system's module loss and inverter loss as two ports | – |
| hisim-device-location-820d.6 (P4, components) | the boiler's casing loss follows the location, after hisim-boiler-auxiliary-losses-pbda | – |
| hisim-device-location-820d.7 (P4, components) | the car battery's location, default outdoors | – |
| hisim-device-location-820d.8 (P3, renovisor, `external`) | the translator writes the location from the request's basement condition, after hisim-envelope-geometry-4vho.1 | – |
| hisim-device-location-820d.9 (P3, question, `owner-decision`) | Q1 of §10 | – |
| hisim-af8m (P3, closed) | absorbed by .2 | – |
| hisim-6id7 (P2, components, related) | the floor's b per sub-element; the cellar temperature reads whatever b the floor uses | – |
| hisim-opxv (P2, components, related) | airing; decides what the published air temperature is while the windows are open | – |
