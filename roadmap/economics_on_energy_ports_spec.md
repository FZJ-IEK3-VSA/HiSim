# Spec: the economics reads the energy flows the components declare on their ports; its parallel tables are gone

Status: decided (owner, 2026-11-25), nothing is built; no question is open (§2). Owner: Noah. Epic:
hisim-economics-ports-25a0 (P2, `economics`, under hisim-economics-engine-cyc). Builds on: the energy balance and its
declared ports (`roadmap/complete_energy_sankeys_spec.md`, `roadmap/fluid_loops_spec.md`), the levelized cost of heat
decision of hisim-lcoh-heat-costs-only-9f07 (owner, 2026-11-13: heat costs only) and its first implementation in
#1276 (branch `lcoh-heat-costs-only`, replaced by PR 3 below), and the seams design (`roadmap/hisim_seams_spec.html`,
bead hisim-seam-economics-input-5d07).

Written from the design plan of 2026-11-25 (`/home/noah/hisim/agent-tmp/economics-ports/plan.md`). Every
`file:line` below refers to origin/main `60e409afb`; line numbers drift, the names do not.

## 1. Problem

The cost engine has its own vocabulary of energy flows, and every part of it repeats something a component already
declares with `EnergyPort`, `FluidLoop` or `MeteredExchange`:

- `EnergyFlowRole` and `UsefulHeatKind` (`hisim/economics/carriers.py`), and a second carrier enum `EnergyCarrier`
  beside `hisim.loadtypes.EnergyBalanceCarrier`;
- the class-name tables `DeviceEnergySpecs`, `UsefulHeatSources` and `MeterOutputContracts` (`hisim/economics/adapter.py`),
  which name component classes and their output columns.

The energy balance already turns the declarations into kWh per port, in every run, before the cost engine runs
(`postprocessing_main.py:313` judges the balance, `:463` calls the cost engine), and it fails a run whose balance
does not close. The tables are checked by nothing of the kind: a column with the wrong sign, a cumulative Wh column
beside its W column, a device the table does not list, each passes. Three consequences today:

- **The levelized cost of heat (LCOH) divides by the ideal demand**, `Building.TheoreticalHeatingEnergyDemand`, not by
  the heat the system delivered. In the runs on disk the delivered room heat is 0.9923 to 1.0000 of the ideal (§7.1),
  so the LCOH reads up to 0.8 % low where a heat pump cannot always hold the set point, more where a system is
  undersized.
- **The LCOH's numerator counts the whole household** (hisim-lcoh-heat-costs-only-9f07). #1276 fixed it with hand-written
  lists of 22 heat asset classes, heating electricity roles and heat carriers: a fourth class-name table.
- **The household electricity balance misses consumers.** `DeviceEnergySpecs` books 20 classes with real flows as
  "no terminal" (the EV battery, the electric heater, the air conditioners, the solar-thermal pump, the hydrogen
  chain, the CHP), so self-sufficiency and the chart leave them out.

### 1.1 Inventory

Counts are occurrences (`grep -o`), then files. "Partner-facing" means `economics_result.json` (schema
`hisim/economics/economics_result.schema.json`), its `staged --parameters` input, RenoVisor's `result.json` or the
capability document.

**`EnergyFlowRole`** (carriers.py:37-55): 63 occurrences in 6 files.

| where | count | what it does |
|---|---|---|
| hisim/economics/views.py | 36 | `EnergyBalanceLayout` (2924), `_read_energy_attribution` (3006), `has_energy_balance` (3056), `energy_balance_flows` (3072), `_check_grid_nodes_against_the_meter` (3164): the household electricity chart, self-consumption and self-sufficiency |
| hisim/economics/adapter.py | 13 | the roles of the `DeviceEnergySpecs` rows (446-516) |
| tests/test_economics_views_charts_b.py | 7 | chart tests |
| hisim/economics/carriers.py | 3 | definition; `validate_energy_attribution` (100-131) |
| hisim/economics/results.py, evaluator.py | 1 + 1 | docstrings of the attribution fields (results.py:652, evaluator.py:211) |

The role names are the keys of `energy_attribution_by_subject_in_kwh` in `economic_inputs.json` (serialization.py:375)
and of `annual_energy_attribution_by_subject_in_kwh` in `lifecycle_costs.json` (results.py:880, serialization.py:821).
Both are HiSim exports, not in the partner schema. RenoVisor reads neither. The record they key has 48 occurrences in 15
files: serialization.py 10, evaluator.py 4, calculators/aggregation.py 4 (`annual_energy_attribution`, 91), views.py 3,
results.py 3, bridge.py 3, staged.py 2 (2540), carriers.py 1; tests: test_economics_data_serialization.py 14,
test_economics_engine_actors.py 4, test_economics_sections_b.py 3, test_economics_views_charts_b.py 2,
test_economics_extraction.py 2, test_multiple_pv_arrays.py 1, test_economics_report_goldens.py 1;
energy_systems/README.md 1.

**`DeviceEnergySpecs`** and its helpers: 36 occurrences of the name in 10 files, 48 with the helpers.
`DeviceEnergySpec` (adapter.py:409), `ResolvedDeviceEnergyFlow` (426), `DeviceEnergySpecs` (435-516, 33 class rows:
5 drawn terminals, 6 controllers, 2 templates, 20 "real flows with no terminal"), `resolve_device_energy_flows` (519),
and in bridge.py `_device_energy_flows` (283), `_require_no_unplaced_electricity` (348), the call at 757. Counts:
adapter.py 17, tests/test_economics_extraction.py 12 (binds every key and constant to the real classes), bridge.py 11,
views.py, results.py, evaluator.py 1 each, hisim/setup_rules/inventory.py:1083 1,
roadmap/declarative_energy_systems/p4_component_sweep_requirements.md 2, roadmap/system_kinds_spec.md 1,
.beads/issues.jsonl 1.

**`UsefulHeatSources` and `UsefulHeatKind`**: 32 + 18 occurrences, 50 with the helpers, in 8 files.
`UsefulHeatSource` (adapter.py:547), `ResolvedUsefulHeatSource` (580), `UsefulHeatSources` (589-610, two rows:
`Building.TheoreticalHeatingEnergyDemand`, `SimpleDHWStorage.ThermalEnergyConsumptionDHW`), `resolve_useful_heat_source`
(613); `UsefulHeatKind` (carriers.py:57-68); in bridge.py `UsefulHeatExtraction` (414), `_useful_heat_of_component`
(427), `_useful_heat_by_kind` (480), the call at 822. Counts: adapter.py 19, bridge.py 16,
tests/test_economics_bridge.py 8 (binds the rows), evaluator.py 6 (`annual_heat_demand` 242, `heat_cost_omits_hot_water`
255), serialization.py 3, carriers.py 2, plausibility.py 1 (`useful_heat_without_hot_water`, 55, 454),
.beads/issues.jsonl 2 (hisim-4wlu, hisim-4p86). The fields they fill, `useful_heat_of_simulated_period_in_kwh`,
`..._by_kind_in_kwh` and `heat_cost_omits_hot_water`, are keys of `economic_inputs.json` only; partner-facing only
through the value of `levelized_cost_of_heat_in_euro_per_kwh` (schema lines 380, 395).

**`MeterOutputContracts`, `MeterSpec`, `get_meter_spec`**: adapter.py:25-38, 211-364 (four meter classes by name,
each with a carrier and two or three column constants); bridge.py:600-679; the electricity meter's own
`get_energy_flow_facts` (electricity_meter.py:682-739), which sums the same columns. Counts: adapter.py 17,
tests/test_economics_adapter_contract.py 13, test_economics_extraction.py 8, bridge.py 3, electricity_meter.py 3,
component.py 2, facts.py 1, roadmap/cost-spec-v2.md 3. It duplicates `Component.metered_exchanges()` (component.py:431)
and is the last place that reads a meter's configuration by `getattr` (`_gas_meter_carrier` 211, `_fuel_meter_carrier`
222).

**`EnergyCarrier`** (carriers.py:15-34): 515 occurrences in 77 files; `ELECTRICITY_FEED_IN` 238 in 35 files. Code in
30 files of `hisim/` and `system_setups/`: economics/database.py 25, staged.py 21, renovisor/economics.py 20
(`GeneratorAssets`, 83-142: 17 generator rows to an asset class and a carrier), staged_parameters.py 17 (parses the
partner's `energy_prices` and `escalation.energy`, 1432-1446), calculators/energy.py 17, adapter.py 14, parameters.py 9,
serialization.py 8, carriers.py 8, tariffs.py 5, views.py 4, subsidies/assessment.py 4, facts.py 4, bridge.py 4,
subsidies/catalog.py 3, scenarios.py 3, evaluator.py 3, catalog_entries.py 3, calculators/escalation.py 3, results.py 2,
calculators/subsidy_application.py 2, economics/__init__.py 2, components/tariff_provider.py 2,
components/electricity_meter.py 2, loadtypes.py 1 (docstring), validation.py 1, timeline.py 1, aggregation.py 1,
system_setups/economic_example/ (2 files, 2 each). Tests: 310 occurrences in 41 files (largest:
test_economics_data_and_integration.py 27, test_economics_database.py 27, economics/test_staged.py 37,
economics/test_clean_export_guarantee.py 15, test_economics_data_serialization.py 15, test_economics_engine.py 14,
economics/test_staged_parameters.py 13, tests/worked_example_runners.py 12). Docs: roadmap/cost_spec.md 6,
hisim/economics/README.md:382, roadmap/cost_module_issues.md 1, roadmap/hisim_seams_spec.html 1 (lists `EnergyCarrier`
among the types that move to contracts). Module graph: two baseline entries under hisim-seam-contracts-3f1a
(tests/module_graph_baseline.yaml:57 electricity_meter, :158 tariff_provider import `hisim.economics.carriers`).

Data files keyed by carrier names:

| file | what | migration (PR 6) |
|---|---|---|
| hisim/cost_database/energy_prices_DE.json | 90 rows, 10 `ELECTRICITY_FEED_IN`, 10 `DISTRICT_HEATING` | feed-in rows become electricity with direction `EXPORT`; `DISTRICT_HEATING` becomes `DISTRICT_HEAT` |
| hisim/cost_database/energy_prices_AT.json | 18 rows, 2 + 2 | same |
| hisim/cost_database/energy_prices_IE.json | 17 rows, 1 + 2 | same |
| hisim/cost_database/escalation_defaults_{DE,AT,IE}.json | keys `ELECTRICITY_FEED_IN` (line 4), `DISTRICT_HEATING` (line 9) | the feed-in key goes (never read: a feed-in follows `feed_in` or `ELECTRICITY`); `DISTRICT_HEATING` becomes `DISTRICT_HEAT` |
| hisim/cost_database/plausibility_checks.json:5-16 | price ranges per carrier | `DISTRICT_HEAT` |
| hisim/cost_database/tariffs/DE_DYNAMIC_SYNTHETIC_2024.json | `"carrier": "ELECTRICITY"` | none |
| hisim/subsidy_catalog/{DE,AT,IE}.json | conditions on `building.existing_heating.energy_carrier` (DE.json:150) | none (the fossil names stay) |
| hisim/subsidy_catalog/questions_{DE,AT,IE}.json | answer options and labels incl. `DISTRICT_HEATING` (DE:176-201) | `DISTRICT_HEAT` |

Partner-facing fields that publish carrier names:

| field | file | carrier names in it |
|---|---|---|
| `parameters.energy_prices` (output and `staged --parameters` input) | economics_result.schema.json:105-117 | all nine, `ELECTRICITY_FEED_IN` for the feed-in rate |
| `parameters.escalation.energy` (output and input; `ELECTRICITY_FEED_IN` refused as input) | schema:96-101 | all nine |
| `parameters.origins.escalation.energy`, `parameters.origins.energy_prices` | schema:157-170 | all nine |
| `reference/plan.by_subject[]` rows of `kind: "carrier"` | schema `$defs/evaluation/by_subject` | `subject` is the carrier name; `ELECTRICITY_FEED_IN` is a subject of its own (the revenue, carriers.py:74) |
| `reference/plan.energy_year1[].carrier` | staged_document.py:1396-1425 | carrier names; the feed-in already merged into the `ELECTRICITY` row |
| `totals.levelized_cost_of_heat_in_euro_per_kwh` | schema:380, 395 | no names; its value and meaning change (PR 2, PR 3) |
| `result.json` KPI `source` texts | renovisor/kpis.py:530-544, 683 | free text "carriers: ELECTRICITY at 0.28 kg/kWh" |
| hisim/renovisor/how_to_use.md:266, 372-380 | documentation of the input | `ELECTRICITY_FEED_IN` |

Not affected: the capability document (capabilities.py and measure-capabilities.openapi.yaml name no carrier and no
heat-cost field) and the fields of `result.json` (it carries no LCOH: result.py:248).

## 2. Decisions of 2026-11-25

**D1 — All parallel economics tables go** (owner, 2026-11-25): `EnergyFlowRole`, `DeviceEnergySpecs`,
`UsefulHeatSources` with `UsefulHeatKind`, `MeterOutputContracts` with `MeterSpec`, and the `EnergyCarrier` enum, which
is merged into `EnergyBalanceCarrier`. Every quantity the economics reads of a run comes from the declared ports, the
fluid-loop tags and the metered exchanges. Consequence: `hisim/economics/carriers.py` is deleted at the end of the series
(PR 7), and a component that is not described by its ports cannot be priced; the completeness rule (§3) fails such a
cost run.

**D2 — The levelized cost of heat divides by the heat delivered**, read from the ports, not by the ideal demand
(owner, 2026-11-25). Delivered room heat is what the building's input ports of space-heating heat received; delivered
hot water is what the heat subjects gave to the taps (§4.2). Consequence: the LCOH rises by 0.0 % to 0.8 % in the runs
on disk (§7.1), more for an undersized system; PR 2. `EconomicContext.annual_heat_demand_in_kwh` still overrides.

**D3 — A device with two useful outputs counts as heat by its share of useful output** (owner, 2026-11-25; question Q1
of the plan, option (b)). For a CHP (electricity and heat) and an air conditioner or reversible heat pump (heat and
cooling), per step the heat share is the useful heat out over the heat plus the other useful output, and the device's
own costs and its fuel or electricity count as heat cost by that share. One rule for every multi-output device, read
from the ports, with no price in the allocation. Consequence: a CHP's LCOH is no longer charged for the electricity it
makes, and an air conditioner's for the cooling; PR 3. The arithmetic is the useful-output heat share of §3.

**D4 — The published carrier names follow the merged enum** (owner, 2026-11-25; Q2 of the plan, option (b)): the
feed-in is electricity in the export direction, and district heating is published as `DISTRICT_HEAT`. This changes
partner-facing fields (§1.1, the second table), so it is its own pull request late in the series (PR 6), with a bump
of the staged document's `SCHEMA_VERSION` and a heads-up on renovisorissues before it merges. Consequence: the plan's
PR 6a (rename `EnergyBalanceCarrier.DISTRICT_HEAT` to `DISTRICT_HEATING` to keep the published spelling) is dropped.

**D5 — The per-port energies come from post-processing's energy balance**, computed once, and travel to the economics
as plain data (owner, 2026-11-25; Q3 of the plan, option (a)): first as a field of `EvaluationInputs`, written to
`economic_inputs.json` (PR 1); later as a field of the seams record `EconomicsInput` (hisim-seam-economics-input-5d07).
The economics never derives a loop's heat itself. Consequence: the record type lives in `hisim/contracts`, which both
post-processing and economics may import (`system_docs/module_graph.yaml`); the balance runs before the economics, as it
does today.

**D6 — `GenericHeatPump`'s hot water gets a bead of its own** (owner, 2026-11-25; gap G7): hisim-component-physics-4g9.71.

**D7 — `HeatingMeter` gets no bead of its own** (owner, 2026-11-25; gap G2): PR 5 drops its billing row, as the plan
says.

## 3. Terms

All per component `s` and over the simulated period unless "per step" is said. A port's kWh is its total over the run
as the energy balance computes it (`PortSeries` of `postprocessing/energy_balance/ports.py`; its sign is already
resolved there, `negated` and the supplier's sign of a loop, so no reader repeats it).

- **Heat carrier**: `SPACE_HEATING_HEAT` or `DOMESTIC_HOT_WATER_HEAT`. Not `ROOM_HEAT` (internal gains, a tank's loss
  into the zone), not `AMBIENT_HEAT`, not `COOLING`.
- **Heat subject**: a component that declares a port of role `PRODUCTION` or `STORED_CHANGE` of a heat carrier, or
  tags a fluid loop whose carrier is a heat carrier (either end). Read from the declarations, not from the values: a
  heat pump that never runs is still a heat subject. The loop tag counts because the heat distribution system tags its
  loop in every position but books it only in `PARALLEL` (G1). Over a staged plan, the union of the stages' heat
  subjects, by subject name (G8).
- **Priced carrier**: `EnergyPort.METERED_CARRIERS` (energy_port.py:365): electricity and the seven bought carriers.
- **Net consumption** of carrier `c` by `s`: its `CONSUMPTION` ports of `c` minus its `PRODUCTION` ports of `c`; `s` is a
  consumer of `c` when it is positive. A home battery counts its losses; an EV battery all its charge (it gives
  `VEHICLE_ELECTRICITY`, not electricity); PV, CHP and fuel cell are producers.
- **Carrier heat share** of `c`: the net consumption of `c` by heat subjects over the net consumption of `c` by all
  consumers, per stage (one simulation), applied to every year the stage is active, as #1276 did; a device with two
  useful outputs contributes by its useful-output heat share. A carrier no heat subject consumes has share 0; one only
  heat subjects consume has share 1.
- **Useful-output heat share** of a device with two useful outputs (D3): per step `t`, `s_t = H_t / (H_t + O_t)`, with
  `H_t` the useful heat the device gives out in kWh (its `PRODUCTION` ports of a heat carrier and the heat it supplies
  on its heat loops) and `O_t` its other useful output in kWh (the electricity it produces, or the cooling it moves). Its
  fuel and electricity of step `t` count as heat by `s_t`. Its own cash flows (investment, replacement, maintenance),
  which are not per step, count by the stage's share `Σ H_t / Σ (H_t + O_t)`, the per-step shares weighted by output.
  A step without useful output (standby) counts by the stage's share; a device with no useful output in the whole stage
  counts as heat (share 1), as a heat subject that never ran. These two edge cases are design choices of this spec,
  for PR 3's review to confirm.
- **Completeness**: for every priced carrier with a meter, the meter's published import minus export equals the
  declared net consumption within the balance's tolerance (`MeterBalance` of `postprocessing/energy_balance/meters.py`).
  If not, a consumer is undeclared and no share can be known: the cost run fails with an unresolved subject, as
  `_require_no_unplaced_electricity` (bridge.py:348) does today.
- **Port energies**: the record of PR 1 (§4.1): every declared port of the run with its kWh, every component's
  fluid-loop tags and every meter's exchange with its totals.

## 4. Design

### 4.1 The record (PR 1)

`hisim/contracts/port_energies.py` holds plain frozen dataclasses that import only `hisim.loadtypes` and numpy:

- `PortEnergy`: subject (the component name), port (the channel, or the loop's label for a loop end), role
  (`EnergyRole`), carrier (`EnergyBalanceCarrier`), environment (`EnvironmentNode` or none), peer and peer kind
  (`PeerKind`, which moves from `postprocessing/energy_balance/ports.py` into the contracts), the kWh of the simulated
  period, and the kWh per step in memory only (PR 3's per-step split needs them; the file carries totals).
- `LoopTag`: subject, loop name, carrier and whether the subject supplies the loop, for every loop a component tags,
  also one the balance does not book (G1).
- `MeterEnergy`: meter, carrier, import and export output, the published import and export of the simulated period in
  kWh, and whether the meter agrees with the ports it reads.
- `PortEnergies`: the three tuples.

`EnergyBalanceReport.port_energies()` (`postprocessing/energy_balance/export.py`) builds it from the balance the setup
rules after the run already judged. `PostProcessor.compute_lifecycle_costs` passes it to
`hisim.economics.bridge.compute_lifecycle_costs`, which sets it on `EvaluationInputs.port_energies`;
`economic_inputs.json` carries it as the key `port_energies` (totals; an older file loads with `None`). Carriers, roles,
environments and peer kinds are spelled there by their member names (`ELECTRICITY`, `CONSUMPTION`), the spelling D4
gives every published carrier.

### 4.2 From the tables to port queries (PR 2 to PR 5)

**`DeviceEnergySpecs` and `EnergyFlowRole`** (adapter.py:446-516, carriers.py:37-55):

| today | port query | the ports' channel |
|---|---|---|
| `PV_GENERATION` = PVSystem.ElectricityEnergyOutput (Wh) | `PRODUCTION ELECTRICITY` ports of net producers that declare no `STORED_CHANGE ELECTRICITY` | PVSystem.ElectricityOutput (pv_system.py:252); also CHP.ElectricityOutput (advanced_fuel_cell.py:299), SimpleCHP.ElectricityOutput (chp.py:222), FuelCell.PowerOutput (generic_fuel_cell.py:213), Transformer.ElectricityDeliveredInWatt (transformer_rectifier.py:235), all in today's residual |
| `BATTERY_CHARGE` / `BATTERY_DISCHARGE` = Battery.AcBatteryPowerUsed split by sign | `CONSUMPTION` / `PRODUCTION ELECTRICITY` ports of a component that declares `STORED_CHANGE ELECTRICITY` and produces electricity | Battery.ChargingPower / DischargingPower (`negated`) (advanced_battery_bslib.py:416, 426); no signed channel read |
| `HEAT_PUMP_ELECTRICITY` = MoreAdvancedHeatPumpHPLib.ElectricalInputPowerTotal; GenericHeatPump.ElectricityOutput | `CONSUMPTION ELECTRICITY` ports of heat subjects | hplib: space heating, cooling, brine pump, heating rod, hot water (heat_pump.py:776, 785, 794, 806, 1080); GenericHeatPump.ElectricityOutput (generic_heat_pump.py:435); also ElectricHeating space heating and hot water (generic_electric_heating.py:319, 367), AirConditioner (air_conditioner.py:524), SolarThermalSystem pump (solar_thermal_system.py:668), IdealizedElectricHeater (idealized_electric_heater.py:269) |
| `HOUSEHOLD_ELECTRICITY` = UtspLpgConnector.ElectricalEnergyConsumption (Wh) | `CONSUMPTION ELECTRICITY` ports of consumers that are not heat subjects | UtspLpgConnector.ElectricalPowerConsumption (loadprofilegenerator_utsp_connector.py:598); also CarBattery (advanced_ev_battery_bslib.py:179), Electrolyzer (generic_electrolyzer_h2.py:405), AdvancedElectrolyzer (generic_electrolyzer_and_h2_storage.py:367), HydrogenStorage compression (:797), SimpleAirConditioner (simple_air_conditioner.py:215) |
| `GRID_IMPORT` / `GRID_EXPORT` = ElectricityMeter.ElectricityFromGrid / ToGrid (Wh) | the electricity `MeteredExchange` import / export outputs | ElectricityMeter.ElectricityFromGridInWatt / ToGridInWatt (electricity_meter.py:396); the same energy as the Wh columns |
| empty rows for controllers and templates | nothing: exempt by kind (ports.py:117-141) or no port | – |
| empty rows for real flows (CarBattery, Car, ElectricHeating, AirConditioner, SimpleAirConditioner, IdealizedElectricHeater, SolarThermalSystem, Transformer, the hydrogen chain, CHP, SimpleCHP) | placed by their own ports, as above | – |
| the completeness guard `_require_no_unplaced_electricity` (bridge.py:348) | the completeness rule of §3 | – |
| magnitudes by role, `validate_energy_attribution` (carriers.py:100) | the port's role and `negated`; a negative total is a component that runs its port backwards, which the balance reports | – |

**`UsefulHeatSources` and `UsefulHeatKind`** (adapter.py:589-610, carriers.py:57-68):

| today | port query | channel |
|---|---|---|
| `ROOM_HEATING` = Building.TheoreticalHeatingEnergyDemand (ideal demand, clipped at 0) | **delivered room heat**: `CONSUMPTION SPACE_HEATING_HEAT` ports of components that are not heat subjects | Building.HeatReceivedInWatt and Building.ThermalPowerCHP, input ports (building.py:362, 374); their series is the connected output, for example HeatDistribution.HeatDeliveredInWatt |
| `HOT_WATER` = SimpleDHWStorage.ThermalEnergyConsumptionDHW | **delivered hot water**: `PRODUCTION DOMESTIC_HOT_WATER_HEAT` ports of heat subjects whose peer is no heat subject (today the boundary node "hot water drawn at the taps", `EnvironmentNode.HOT_WATER_USE`) | SimpleDHWStorage.ThermalPowerConsumptionDHW (simple_water_storage.py:2725) |
| `UsefulHeatSource.sign` (INTO_THE_HOUSE / LEAVING_THE_SOURCE) and the per-step sign refusal (bridge.py:462-476) | the port's role and `negated`; a heat port running backwards is the balance's business | – |
| `heat_cost_omits_hot_water` (evaluator.py:255) | delivered room heat > 0 and no delivered hot water in the run | – |
| the two `UsefulHeatKind` keys | the two carriers' names | – |

- Rooms: behind a distribution system the building's input ports read `HeatDeliveredInWatt`; behind an electric heater
  `ThermalOutputShPower` (default connections, building.py:815-866). If a setup wires the building's
  `ThermalPowerDelivered` and not `HeatReceivedInWatt`, the building's balance does not close and the run fails, so the
  query cannot silently miss heat. Several buildings or apartments are summed.
- Hot water: the heat the taps got, the demand minus the unmet part. It is the quantity today's row reads, so hot water
  does not change. Only `SimpleDHWStorage` delivers to the taps today; a new device that does declares the same port, so
  the rule finds it without a new row (which answers hisim-4wlu).
- The `useful_heat_without_hot_water` warning stays, read from the ports.

**#1276's hand-written lists** (`calculators/heat_cost.py` on `lcoh-heat-costs-only`):

| #1276 | port query |
|---|---|
| `HEAT_ASSET_CLASSES` (22 asset classes) | heat subjects (§3) |
| `HEATING_ELECTRICITY_ROLES` and the new role `ELECTRIC_HEATER_ELECTRICITY` | the electricity heat share (§3); the electric heater needs nothing new |
| `ELECTRIC_HEAT_ASSET_CLASSES` (the "share unknown" guard) | the completeness rule; a heat subject's electricity is always declared |
| `HEAT_CARRIERS` (six carriers counted whole) | the heat share of every priced carrier: gas, oil, pellets, wood chips and district heat come out at 1 where only heat subjects burn them; hydrogen splits between a boiler and a fuel cell |
| `SHARED_CARRIER` = electricity | no special case |
| meters, synthetic subjects (financing, reserve, CO2 damage), envelope, feed-in revenue: not heat | unchanged: they declare no heat port; the feed-in revenue is the export direction of electricity and gets no share |

**`MeterOutputContracts`** (adapter.py:273-303):

| today | port query |
|---|---|
| carrier per meter class (`_gas_meter_carrier`, `_fuel_meter_carrier`) | `MeteredExchange.carrier` (gas_meter.py:414, fuel_meter.py:386) |
| bought / sold columns (Wh) | `import_output` / `export_output` |
| `power_constant` for capacity peaks | the electricity exchange's `import_output`, already in W |
| `HeatingMeter` → `DISTRICT_HEATING` | no exchange; its billing row goes (G2, D7) |

**`EnergyCarrier`** → the priced members of `EnergyBalanceCarrier` plus a direction for the feed-in (§4.4).

### 4.3 The gaps and how each is handled

Checked against every case the owner named and every priced class.

| gap | what the ports say | how it is handled |
|---|---|---|
| **G1. Heat distribution system outside `PARALLEL`** | `HeatDeliveredInWatt` / `HeatDrawnForCoolingInWatt` carry a port only `if self.declares_energy_ports` (heat_distribution_system.py:246-248, 351-380); the loop is booked only then (:686) | delivered room heat is right regardless (it is read at the building's input port, whose series is the connected column). Its heat-subject status comes from the loop tag, which the record carries for every loop (`LoopTag`, PR 1); without the tag rule the emitters' costs would leave the LCOH. The real fix stays hisim-pnw7 (P1), which makes the system declare its ports in every position |
| **G2. `HeatingMeter`** | a meter with no `metered_exchanges`, exempt by kind (ports.py:133); used by no setup | cannot be billed from ports. PR 5 drops its billing row; no bead of its own (D7) |
| **G3. Ground or air heat source** (`SimpleHeatSource`, `PRICED`) | a brine loop of `AMBIENT_HEAT` (simple_heat_source.py:349) to the heat pump | not a heat subject by §3, though part of a ground-source heat pump. Moot today: it has no cost facts and no priced setup uses it. When it gets cost facts, the rule extends: a supplier of an `AMBIENT_HEAT` loop whose other member is a heat subject is a heat subject |
| **G4. Heat and cooling in one device** (AirConditioner, reversible hplib, HeatDistribution with cooling, IdealizedElectricHeater) | heat `PRODUCTION` and `COOLING` `CONSUMPTION` per step; the electricity port does not say what it served (AirConditioner has one, air_conditioner.py:524; hplib a separate cooling port, heat_pump.py:785, which nothing marks as cooling) | D3: the device's carrier consumption of step `t` counts for heat by `H_t / (H_t + C_t)`, its own cash flows by the stage's share. No component change. PR 3 |
| **G5. CHP** (SimpleCHP chp.py:204-264, the fuel-cell CHP advanced_fuel_cell.py:299-341) | heat and electricity `PRODUCTION`, fuel `CONSUMPTION` | D3 with the electricity produced as the other useful output: the gas counts as heat by `H_t / (H_t + E_t)`. PR 3 |
| **G6. A consumer of a priced carrier with no port** | none among today's classes | the completeness rule (§3) fails the cost run. PR 3 |
| **G7. `GenericHeatPump` hot water** | declares the inputs `WaterConsumption`, `WaterInput_mass` and `WaterInput_temperature` (generic_heat_pump.py:347-366) but no hot-water port; checked 2026-11-25: its `i_simulate` reads none of them (the hot-water code is commented out, :620-656), and no setup or energy-system file wires them | it heats no water today, so the LCOH misses nothing. Bead hisim-component-physics-4g9.71 (D6): delete the three unread inputs and the commented-out block, or, if the device is to heat water, book it on a `PRODUCTION DOMESTIC_HOT_WATER_HEAT` port to the taps |
| **G8. A subject the stage did not simulate** (a staged plan's reference boiler, removed later) | ports exist only in the stages that simulate it | heat-subject status is the union over the plan's stages, by subject name; removal costs are booked on the new subject (context_resolution.py:421-428), which its own stage classifies. No component fix; the staged evaluator unions the stages' heat subjects. PR 3 |

Checked and fine without a fix:

- **Storage** (buffer, hot-water cylinder): `STORED_CHANGE` of a heat carrier (simple_water_storage.py:1328, 2710) and
  loop ends. Heat subject. Its loss into the zone is `ROOM_HEAT`, not counted.
- **Emitter in `PARALLEL`**: `PRODUCTION SPACE_HEATING_HEAT` to the building. Heat subject.
- **Hybrid** (heat pump and boiler): both heat subjects; gas share 1, electricity share by the heat pump's consumption.
- **District heating**: loop supplier and `CONSUMPTION DISTRICT_HEAT` (generic_district_heating.py:408, 452). Heat
  subject, district heat share 1.
- **Solar thermal**: supplies the `solar_dhw` loop (solar_thermal_system.py:571). Heat subject; its pump electricity
  counts as heat electricity. Today `DeviceEnergySpecs` drops it (adapter.py:502), and #1276 would have too.
- **Building**: consumes heat, produces none. Not a heat subject (the envelope stays out of the LCOH).
- **Occupancy, PV, battery**: `ROOM_HEAT` only. Not heat subjects.
- **Double counting**: the hplib total (`ElectricalInputPowerTotalHeatpump`) carries no port, its circuits do; the
  battery's signed `AcBatteryPowerUsed` carries none, its charge and discharge do; the distribution system's signed
  `ThermalPowerDelivered` carries none, its two directions do. Meters publish exchanges, not ports, so a device's port
  and the meter's exchange are never summed together. A cumulative channel with a port would break its component's
  balance and fail the run.

### 4.4 Cooling

- The LCOH counts heating only. The cooling the building gives up (`HeatRemovedByCoolingInWatt`, `PRODUCTION COOLING`,
  building.py:382) is in neither the numerator nor the denominator, as today (the ideal demand was clipped at zero).
- A cooling-only device (SimpleAirConditioner) is not a heat subject: its cost and its electricity leave the LCOH.
- A device that heats and cools: D3.
- A levelized cost of cooling could later use the same rule with `COOLING` in place of the heat carriers. Not planned.

### 4.5 The carrier merge (PR 6, PR 7)

- One enum, `hisim.loadtypes.EnergyBalanceCarrier`. The priced carriers are `EnergyPort.METERED_CARRIERS`:
  `ELECTRICITY`, `NATURAL_GAS`, `HEATING_OIL`, `PELLETS`, `WOOD_CHIPS`, `HYDROGEN`, `DIESEL`, `DISTRICT_HEAT`.
- The feed-in is electricity in the export direction. A price row is keyed by `(carrier, direction)`, direction
  `IMPORT` or `EXPORT` (a small enum beside the carrier, in `hisim/loadtypes.py`). Only electricity has an `EXPORT` row
  today; `MeteredExchange.export_output` already says which carriers can be exported.
- Data files and published fields spell a carrier by its member name, as energy-system files spell every enum
  (`ELECTRICITY`, not `electricity`). One helper writes and reads the name; a test refuses a lowercase carrier key in
  any economics output.
- `revenue_subject` / `bill_subjects` (carriers.py:74-97) become a property of the export direction.
- `hisim/economics/carriers.py` then holds no vocabulary, and the module goes.
- Data migration (PR 6): the 13 `ELECTRICITY_FEED_IN` rows of `energy_prices_{DE,AT,IE}.json` become
  `"carrier": "ELECTRICITY", "direction": "EXPORT"`, every other row gets `"direction": "IMPORT"` (explicit, so a
  missing key fails); the 14 `DISTRICT_HEATING` rows become `DISTRICT_HEAT`; `escalation_defaults_{DE,AT,IE}.json` lose
  the `ELECTRICITY_FEED_IN` key (the feed-in never escalates by carrier: schema line 99, how_to_use.md:266) and rename
  `DISTRICT_HEATING`; `plausibility_checks.json` and `questions_{DE,AT,IE}.json` rename `DISTRICT_HEATING`;
  `hisim/economics/validation.py` (the data-file check) learns the direction key. Stored `economic_inputs.json` files
  name `ELECTRICITY_FEED_IN` nowhere (the feed-in is `energy_sold_in_kwh` of the electricity record); a stored
  `DISTRICT_HEATING` is read by the name helper as `DISTRICT_HEAT`, so old files still load.
- Published (D4): `energy_prices.ELECTRICITY` gains the export rate in place of `ELECTRICITY_FEED_IN`;
  `escalation.energy` and the origins lose `ELECTRICITY_FEED_IN`; `by_subject` carries one `ELECTRICITY` carrier row
  (bill and revenue together); `DISTRICT_HEATING` becomes `DISTRICT_HEAT` everywhere; `how_to_use.md` and the KPI
  source texts follow. The staged document's `SCHEMA_VERSION` (staged_document.py:430; 12 after PR 3) goes up by one,
  and renovisorissues gets a heads-up before PR 6 merges.

## 5. The pull requests

Stacked, one PR at a time, each green on its own. "Partner" marks a PR that changes partner-facing output. No PR moves
a simulation golden (`golden_references/` holds no lifecycle KPI; checked). Every PR runs the touched test files,
flake8, the three mypy runs, `semgrep --config .semgrep/ --error hisim/` and `tests/test_module_graph.py`. The plan's
PR 6a is dropped (D4); its 6b and 6c are PR 6 and PR 7 here.

**PR 1. The energy ports' kWh reach the cost engine** (hisim-economics-ports-25a0.1). No figure changes.
- Scope: the record of §4.1, built from the energy balance that `postprocessing_main.py:313` already computes,
  handed to `compute_lifecycle_costs` as an argument and written to `economic_inputs.json`.
- Files: new `hisim/contracts/__init__.py` and `hisim/contracts/port_energies.py` (the package is created here, since
  hisim-seam-contracts-3f1a has not created it yet; the contracts may import leaves, physics constants, config and
  parameters, `system_docs/module_graph.yaml`); `PeerKind` moves there from `postprocessing/energy_balance/ports.py`,
  which imports it back; `postprocessing/energy_balance/export.py` (`EnergyBalanceReport.port_energies`),
  `postprocessing_main.py` (passes it), `economics/bridge.py` (`compute_lifecycle_costs` takes it),
  `economics/evaluator.py` (`EvaluationInputs.port_energies`), `economics/serialization.py` (writes the totals; an old
  file loads with `None`).
- Tests: the record of a small hand-checkable run, its round trip through `economic_inputs.json`, the bridge writing
  it, and the economics goldens unchanged.
- Stays green: the old tables still drive every figure.

**PR 2. The levelized cost of heat divides by the heat delivered; `UsefulHeatSources` and `UsefulHeatKind` go**
(hisim-economics-ports-25a0.2). Partner (values only).
- Scope: heat subjects (§3) and delivered heat (§4.2) as queries over the record, in a new
  `hisim/economics/heat_flows.py`. Removes adapter.py:547-632, carriers.py:57-68, bridge.py:414-514; the evaluator's two
  useful-heat fields keep their meaning, keyed by carrier name; `heat_cost_omits_hot_water` reads the ports.
- Tests: `tests/test_economics_bridge.py` (the binding test of the two rows goes, delivered-heat tests come),
  `tests/economics/test_heat_flows.py` (synthetic records: building, taps, several buildings, emitter outside
  `PARALLEL`), `tests/renovisor/test_staged_economics.py`.
- Goldens: `tests/goldens/cost_summary.md` and `lifecycle_report.html` are built from synthetic inputs
  (`tests/worked_example_runners.py`); their heat is kept, so they should not move.
- Partner: `levelized_cost_of_heat_in_euro_per_kwh` rises by 0 to 0.8 % (§7.1). No schema change. Closes hisim-4wlu (the
  hot-water survey is answered by the ports).

**PR 3. The levelized cost of heat counts heat costs only, from the ports** (hisim-lcoh-heat-costs-only-9f07). Replaces
#1276. Partner.
- Scope: `hisim/economics/calculators/heat_cost.py` with #1276's arithmetic (`HeatCostBasis`, the NPV of weighted
  entries) but no lists: heat subjects and per-carrier heat shares from the record, the completeness rule, D3's
  split for G4 and G5, G8's union. `aggregation.py:186-250`, `evaluator.py:1210-1230`, `staged.py` (shares per stage
  and year, heat subjects united over stages), `staged_document.py` (`UnpricedInvestment.kept_totals` asks the
  evaluation for its heat subjects instead of asset classes), `results.py:581` (`HeatCostNaming` → "Levelized cost of
  heat"), `serialization.py` (`heat_cost_npv_in_euro`). `economic_inputs.json` gains, per port of a heat subject, the
  kWh attributed to heat (the per-step split needs the steps, which only the extraction has).
- Tests: #1276's `tests/economics/test_heat_cost.py` rewritten on synthetic port records (heat pump, boiler, hybrid,
  district heating, solar thermal pump, electric heater, air conditioner heating and cooling, CHP, a PV-only plan that
  leaves the figure unchanged); the test files #1276 touched.
- Goldens: `tests/goldens/cost_summary.md`, `lifecycle_report.html` move as in #1276 (0.169 → about 0.156).
- Partner: LCOH values fall by about 10 to 20 % (§7.2); the figure's name in `lifecycle_kpis.json` and the reports;
  `SCHEMA_VERSION` 12 (as #1276). Heads-up on renovisorissues #107 / #108.

**PR 4. The electricity balance chart and self-consumption come from the ports; `EnergyFlowRole` and
`DeviceEnergySpecs` go** (hisim-economics-ports-25a0.3). Not partner-facing.
- Scope: `views.py:2924-3210` builds its nodes from the record: grid import and export from the meter's exchange, one
  node per generator, storage (charge and discharge) and consumer, consumers labelled by subject and grouped heat /
  other. Self-consumption = (own generation − export) / own generation; self-sufficiency = (net consumption − import) /
  net consumption, now with the EV, electric heater, air conditioner and solar pump in the consumption. The
  `losses / unattributed` node keeps only a meter's residual. Removes adapter.py:409-544, bridge.py:283-377,
  carriers.py:37-55 and 100-131, `energy_attribution_by_subject_in_kwh` (evaluator.py:211-215) and
  `annual_energy_attribution_by_subject_in_kwh` (results.py:652-660, staged.py:2540, aggregation.py:91), replaced by
  annual port energies. `setup_rules/inventory.py:1083` text.
- Tests: `tests/test_economics_extraction.py` (the binding test of `DeviceEnergySpecs` goes; a generic test that every
  class with `cost_relevance` `PRICED` or `METER` declares ports or an exchange comes), `test_economics_views_charts_b.py`,
  `test_economics_sections_b.py`, `test_economics_engine_actors.py`, `test_multiple_pv_arrays.py`,
  `test_economics_data_serialization.py`.
- Goldens: `lifecycle_report.html` if the synthetic example draws the chart (it carries attribution:
  test_economics_report_goldens.py).
- `lifecycle_costs.json` changes one field's shape (a HiSim export, not in the partner schema).

**PR 5. Bills come from the meters' declared exchanges; `MeterOutputContracts` goes** (hisim-economics-ports-25a0.4). No
figure changes.
- Scope: `bridge._billing_determinants` (600-679) reads `metered_exchanges()` (carrier, import, export; peaks from the
  electricity import in W). Removes adapter.py:25-38, 211-364, the electricity meter's `get_energy_flow_facts` override
  (electricity_meter.py:682-739). `HeatingMeter` loses its billing row (G2, D7).
- Tests: `tests/test_economics_adapter_contract.py` (its meter half goes), `test_economics_extraction.py`, a bill
  equality test on a short run (same kWh, same peaks).
- Interaction: the base hook `Component.get_energy_flow_facts` (component.py:901) and `EnergyFlowFacts` stay for
  hisim-seam-contracts-3f1a and hisim-seam-economics-input-5d07 to decide.

**PR 6. The published carrier names follow the merged model: the feed-in is electricity's export direction, district
heating is `DISTRICT_HEAT`** (hisim-economics-ports-25a0.5). Partner (D4).
- Scope: price rows keyed by `(carrier, direction)`; `EnergyCarrier.ELECTRICITY_FEED_IN` goes;
  `EnergyCarrier.DISTRICT_HEATING` is renamed `DISTRICT_HEAT`, so every name of `EnergyCarrier` is a name of
  `EnergyBalanceCarrier`; the data migration and the published fields of §4.5; `database.py`, `calculators/energy.py`
  (163-208, 359-401), `parameters.py`, `staged_parameters.py` (1432-1457), `staged.py`, `staged_document.py`
  (`SCHEMA_VERSION`), `catalog_entries.py`, `validation.py`, `economics_result.schema.json`,
  `hisim/renovisor/how_to_use.md`, `renovisor/kpis.py`.
- Tests: the 14 test modules that name `ELECTRICITY_FEED_IN`; `tests/goldens/cost_summary.md` and
  `lifecycle_report.html` where a carrier's label changes.
- Partner: the schema bump and the heads-up on renovisorissues, posted before the PR merges.

**PR 7. `EnergyCarrier` is replaced by `EnergyBalanceCarrier`** (hisim-economics-ports-25a0.6). Mechanical; no figure or
partner change.
- Scope: after PR 6 the names agree, so data files stay as they are. All 30 code files and 41 test files of §1.1;
  `hisim/economics/carriers.py` deleted; `renovisor/economics.py` `GeneratorAssets`; `system_setups/economic_example/`.
- Module graph: electricity_meter.py and tariff_provider.py import the carrier from loadtypes; the two baseline entries
  (module_graph_baseline.yaml:57, :158) are deleted in the same PR, as the ratchet requires. hisim-seam-contracts-3f1a
  is told that `EnergyCarrier` no longer needs to move to the contracts.
- Docs: hisim/economics/README.md:382, roadmap/cost_spec.md (6 places).

Order: 1 → 2 → 3 → 4 → 5 → 6 → 7. PR 4 and PR 5 need only PR 1, but run after PR 3 to avoid conflicts in carriers.py and
bridge.py; PR 6 and PR 7 come last, when fewer `EnergyCarrier` sites are left.

### 5.1 Alignment with hisim-seam-economics-input-5d07

- Today the bridge reads live components: `wrapper.my_component` with `getattr` for facts, flows, meters and the tariff
  contract (bridge.py:575-597, 750-819), and the adapter reads configurations through `hisim.config.concrete`
  (module_graph_baseline.yaml:178). The useful heat and the device flows are read by column name off the live class
  (adapter.py:306-333).
- The seams spec (§5) makes `EconomicsInput` a frozen record in `hisim/contracts`, built by the simulator from the
  `ComponentRunRecord`s, and has the simulator call both engines (§10 Q3). It keeps the energy balance as
  post-processing's result (§10 Q4).
- This series runs before 5d07, as its first slice: PR 1 defines the port record in the contracts and fills it from the
  balance post-processing already computes; PRs 2 to 5 remove three of the bridge's live reads (device columns,
  useful-heat columns, meter configurations). When 5d07 lands, the simulator puts the same record into
  `EconomicsInput` (D5); nothing in the economics changes. Doing it after 5d07 would block the LCOH fix behind seams
  steps 11 and 12 (the run record, then the economics input).

### 5.2 Interactions with hisim-legacy-cost-cutover-7300

- No dependency either way. The legacy path neither prices a heat cost nor reads the economics tables.
- PR 5 leaves the billed kWh bit-identical, so `cost_parity_report.csv` keeps its deltas (the cutover's exit criterion).
- Both touch `bridge.py` (`write_parity_from_stored_inputs`, 1213) and `postprocessing_main.py`: merge conflicts only.
- The cutover removes the components' legacy carrier and price tables in `configuration.py`; after PR 7 nothing else
  names a carrier twice.

### 5.3 Out of scope

- `FactsExtractors` (adapter.py:106-208) is a class-name table too, but of asset classes and sizes. It goes as
  components adopt `get_cost_facts`, under hisim-seam-economics-input-5d07 and hisim-seam-contracts-3f1a.
- The CO2KostAufG split (actors.py:261-275) applies by law to heating fuels only, but today it splits every carbon
  price. PR 3's carrier heat share could feed it later. Not planned.
- #1276's other content (naming, schema 12, `kept_totals`, the report captions) carries over into PR 3.

## 6. What changes and what does not

- Results: no simulation result moves; `golden_references/` holds no lifecycle KPI. The LCOH rises by 0 to 0.8 % in
  PR 2 and falls by about 10 to 20 % in PR 3; self-consumption and self-sufficiency in the HiSim lifecycle report change
  in PR 4 where a run has an EV, an electric heater, an air conditioner or a solar-thermal pump.
- Public interfaces: `economic_inputs.json` gains `port_energies` (PR 1) and loses the attribution and useful-heat-kind
  keys (PR 2, PR 4); `lifecycle_costs.json` changes one field's shape (PR 4); the partner-facing changes are the LCOH's
  value (PR 2, PR 3), its name and `SCHEMA_VERSION` 12 (PR 3), and the carrier names with the next schema version (PR 6),
  each with a heads-up on renovisorissues (`system_docs/results_and_interfaces.md` §5).
- Goldens: `tests/goldens/cost_summary.md` and `lifecycle_report.html` move in PR 3, possibly in PR 4 and PR 6; nothing
  in `golden_references/`.

## 7. Verification and measured results

Every PR proves its rule on synthetic port records with hand-checkable numbers, and that the figures it does not
change stay bit-identical (`tests/test_economics_report_goldens.py`).

### 7.1 Delivered heat against the ideal demand, from runs on disk (2026-11-25)

| run | ideal room heat (kWh) | delivered (kWh) | delivered / ideal | LCOH from the denominator |
|---|---|---|---|---|
| agent-tmp/j27e/runs/baseline_main (IE gas, full year) | 39,094.4 | 39,094.4 (HDS output) | 1.0000 | 0.0 % |
| agent-tmp/boiler-basis/.../after_boil_condensing_gas_full_year | 37,100.9 | 37,100.9 | 1.0000 | 0.0 % |
| agent-tmp/sankey-preview/household_gas_building_sizer | 10,948.6 | 10,945.6 (building port) | 0.9997 | +0.03 % |
| agent-tmp/j27e/runs/mockup_main (heat pump, PV, insulation) | 24,492.8 | 24,485.9 (HDS output) | 0.9997 | +0.03 % |
| agent-tmp/sankey-preview/household_heatpump_building_sizer | 11,750.5 | 11,663.5 (building port) | 0.9926 | +0.75 % |
| agent-tmp/sankey-preview/household_heatpump_car_building_sizer | 11,728.7 | 11,638.5 (building port) | 0.9923 | +0.78 % |

Hot water in the same runs: today's `HOT_WATER` equals the tap port (for example 3,036.6 kWh = 3,044.2 demand − 7.6
unmet), so it does not change. Delivered heat is at most the ideal demand in every run seen, so the LCOH rises: by 0 %
for a boiler and up to about 0.8 % for a heat pump that cannot always hold the set point. It rises more where a system
is undersized and falls where a system overheats the rooms. To measure it on any run: `balance_report.json` →
component `Building`, ports of carrier `space_heating_heat` and role `consumption`, against the KPI "Theoretical heating
demand" in `all_kpis.json`.

### 7.2 Together with the heat-costs-only numerator (estimate)

From the stored `lifecycle_costs.json` of the two j27e runs (brownfield_gross, best estimate; heat subjects and the
electricity share as in §3):

| run | LCOH today | heat costs only | change |
|---|---|---|---|
| j27e baseline (gas boiler) | 0.326 EUR/kWh | 0.287 | −12 % (the appliance electricity leaves) |
| j27e mockup (heat pump, PV, facade) | 0.240 EUR/kWh | 0.196 | −18 % (PV, facade and 35 % of the electricity bill leave; share 5,610 / 8,582 kWh = 0.654) |
| tests/goldens/cost_summary.md as #1276 moved it | 0.169 | 0.156 | −8 % (tenant 0.23 → 0.11) |

The denominator's effect (at most +0.8 %) is small beside the numerator's.

## 8. Beads

Epic hisim-economics-ports-25a0 (P2, `economics`), a child of hisim-economics-engine-cyc.

| Bead | Content | PR |
|---|---|---|
| hisim-economics-ports-25a0.1 (P2, task) | PR 1: the port energies reach the cost engine as a record in `hisim/contracts` | – |
| hisim-economics-ports-25a0.2 (P2, bug) | PR 2: the LCOH divides by the delivered heat; `UsefulHeatSources`, `UsefulHeatKind` go; closes hisim-4wlu | – |
| hisim-lcoh-heat-costs-only-9f07 (P2) | PR 3: the LCOH counts heat costs only, from the ports (replaces #1276) | #1276 (replaced) |
| hisim-economics-ports-25a0.3 (P2, task) | PR 4: the electricity chart and self-sufficiency from the ports; `EnergyFlowRole`, `DeviceEnergySpecs` go | – |
| hisim-economics-ports-25a0.4 (P2, chore) | PR 5: bills from the metered exchanges; `MeterOutputContracts` goes; `HeatingMeter`'s billing row goes | – |
| hisim-economics-ports-25a0.5 (P2, task) | PR 6: the published carrier names follow the merged model (partner, schema bump, heads-up) | – |
| hisim-economics-ports-25a0.6 (P2, chore) | PR 7: `EnergyCarrier` replaced by `EnergyBalanceCarrier`; `carriers.py` deleted | – |
| hisim-4wlu (P3) | the hot-water survey, answered by the ports; closed by PR 2 | – |
| hisim-component-physics-4g9.71 (P3, bug) | G7: `GenericHeatPump` declares hot-water inputs it never reads and no hot-water port | – |
| hisim-pnw7 (P1) | G1: the heat distribution system declares its ports in every position | – |
