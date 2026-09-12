# obsolete/

Code that HiSim no longer uses, kept rather than deleted.

A module lands here when nothing in `system_setups/`, `tests/` or `hisim/` builds it any more and the
component sweep (`roadmap/declarative_energy_systems/p4_component_sweep_requirements.md`, decisions
D-1 … D-32) has decided not to convert it. Moving beats deleting: the physics and the parameter values took
work to get right, and a future reader looking for "did HiSim ever model this?" should find an answer rather
than a gap in the history.

**Nothing here is maintained.** These files are excluded from the quality gates, are not imported by
anything, and are not expected to run against the current interfaces. Treat them as a reference, not as a
library — if one of them becomes useful again, it comes back through `hisim/` with tests, not by being
imported from here. Each file is archived exactly as it stood on the day it was retired, its import paths
included, so nothing here is importable in place: a file that comes back is re-homed, not imported from here.

This directory is a staging area. Its contents move on to the separate obsolete repository, which is why a
file may sit here for a while before disappearing from this one.

## What is here, and why

| Moved | Decision | Reason |
|---|---|---|
| `components/advanced_heat_pump_hplib.py` | D-1 | Zero setup instantiations. Its sibling `MoreAdvancedHeatPumpHPLibConfig` does the same job in plain floats and is what the setups use, while this one is the only class whose sizable fields are `Quantity`-typed — which the sizing kernel cannot express. Converting it would have meant either lowering its types or teaching the kernel `Quantity`, for a class with no consumer. |
| `components/controller_l1_heatpump.py` | D-2 | Zero call sites anywhere — the last of the plan's three D13 "zombies" still in the tree, and the only one that still imported cleanly. Converting it would have minted three preset names (`space_heating`/`buffer`/`dhw`) into the wire vocabulary for a controller no setup, test or component builds. It has no test file, so nothing moves beside it. |
| `components/advanced_fuel_cell_controller.py` | D-8 | Zero call sites, and unrunnable if it had any: `ExtendedControllerSimulation.control_*` reads `ExtendedControllerConfig` as bare class attributes, and that class is a `@dataclass` with no field defaults, so the first control call raises `AttributeError`. Its test passes only because it exercises `_sensor_index` and `__init__` and never a control method. |
| `components/configuration_fuel_cell_controller.py` | D-8 | `ExtendedControllerConfig`, `GasControllerConfig` and `CHPControllerConfig`, moved out of `hisim/components/configuration.py`, which was a shared file and keeps everything else. The retired controller above was their only user in the repository. |
| `components/controller_mpc.py` | D-16 | Zero setup uses, and needs `casadi`, no longer installed. A model-predictive prototype whose config carries 27 of its 49 fields as lists — 13 runtime result buffers and 12 forecast inputs, none of which belongs in a file, plus two COP/EER coefficient vectors — so converting it would have forced them into the file format. With `controller_pid.py` it was one of the two readers of the six construction-time 5R1C singleton keys the Building writes, and it was the only consumer of the 24 h price forecast the tariff provider publishes. |
| `components/controller_pid.py` | D-16 | Zero setup uses. The repository's only PID prototype, and the second of the two readers of the Building's 5R1C singleton keys. |
| `components/generic_windturbine.py` | D-16 | Zero setup uses. The KPI vocabulary keeps its `Windturbine production` entries — those are keyed on `ComponentType.WINDTURBINE` in `loadtypes.py`, not on this module — so no recorded result changes. |
| `components/generic_price_signal.py` | D-16 | Zero setup uses, and superseded: `hisim/components/tariff_provider.py` publishes the same two `SingletonSimRepository` forecast keys from the same `TariffContract` the billing engine reads. The parallel phase the two were written for is over. |
| `components/simple_hot_water_storage_controller.py` | D-16 | `SimpleHotWaterStorageController` and its config, moved out of `hisim/components/simple_water_storage.py`, which keeps the three live storage classes. Zero call sites of any kind. It carried one of the two reads of the writer-less `WATERMASSFLOWRATEOFHEATGENERATOR` singleton key; the other stays behind with `SimpleHotWaterStorage`. |
| `components/configuration_legacy.py` | D-16 | `WarmWaterStorageConfig`, `LoadConfig`, `ElectricityDemandConfig` and `PVConfig`, moved out of `hisim/components/configuration.py`. Each verified to have zero live references; `WarmWaterStorageConfig` is named only inside a commented-out block of `generic_heat_pump.py`. Their neighbour `HouseholdWarmWaterDemandConfig` (live) stays; `HydrogenStorageConfig` and `AdvElectrolyzerConfig`, left for D-25/D-29, have since followed into `configuration_hydrogen.py` below. |
| `components/generic_electrolyzer.py` | D-29 | The plainer of the two of HiSim's three electrolyzer models that duplicated each other. The third, `generic_electrolyzer_h2.Electrolyzer`, reads the manufacturer table, is built by the live `electrolyzer_with_renewables` setup and is untouched. No setup built either of the duplicating pair, so the question was which of the two to keep, and the owner kept the richer: `generic_electrolyzer_and_h2_storage.AdvancedElectrolyzer` models waste energy and a part-load band this one does not. Its test moves with it. D-28 asked how to give `GenericElectrolyzerConfig` a default at all — its `get_default_config` takes a mandatory `p_el` — and this move made that question moot. |
| `components/generic_hydrogen_storage.py` | D-29 | The storage half of the same pair, and it imports the electrolyzer above for one of its two default connections. The surviving `HydrogenStorage` in `generic_electrolyzer_and_h2_storage.py` is the richer model the owner kept; its units are Nl/h and kWh/kg where this one's were kg, kg/s and Wh/kg, so nothing here is a drop-in replacement in either direction. `tests/test_h2storage.py` moves with it — it built this class, not the survivor. |
| `components/controller_l1_electrolyzer.py` | D-29 | `L1GenericElectrolyzerController`, written for the electrolyzer above and pointed at the storage above. The follow-up decision was that it moves rather than being re-pointed at the survivor. Its test is the electrolyzer test that moved with the component; `tests/test_controller_l1_generic_electrolyzer.py` stays behind despite its name, because it builds the live `controller_l1_electrolyzer_h2.ElectrolyzerController`. |
| `components/configuration_hydrogen.py` | D-29 | `HydrogenStorageConfig` and `AdvElectrolyzerConfig`, moved out of `hisim/components/configuration.py`, which keeps everything else — the two D-16 left behind as belonging here. Both verified to have zero references; `AdvElectrolyzerConfig` was the dead third copy of the electrolyzer configuration, disagreeing with the live one by a missing underscore (`max_power = 2_4000`). |
| `components/generic_smart_device.py` | D-30 | Defective as well as dead: `SmartDevice.__init__` calls `build()` unconditionally, which reads `utils.HISIMPATH["utsp_reports"]` — a key `HISIMPATH` does not have (it has `utsp_results`, `utsp_example_results`, `utsp_example_reports` and `report`) — so every construction raises `KeyError: 'utsp_reports'`. Zero setups, zero call sites, and no test file, so nothing moves beside it. Even with the path fixed, the naming supplement's proposed `standard` preset would have shipped `identifier="Identifier"`, which matches no device in the LPG flexibility report, so the preset would have built an appliance with an empty profile. A D13 dead-code member the plan did not list. The KPI vocabulary keeps its `ComponentType.SMART_DEVICE` member in `loadtypes.py` and its `KpiTagEnumClass.SMART_DEVICE` tag, and the energy management system keeps the consumer branch that names the former; `SingletonDictKeyEnum.SMARTDEVICESINCLUDED` stays too — nothing in the repository ever wrote or read it, this module included, so it is D-32's business, not this move's. |
| `components/generic_rsoc.py` | D-25 | The reversible solid-oxide cell, unbuildable since it was written. `RsocConfig.read_config` and `from_rsoc_name` — its only builders — open `hisim/inputs/rSOC_manufacturer_config.json`, which is not in this repository and, as far as its history shows, never was; both raise `FileNotFoundError`. The four tests passed only by handing the class a dict they fabricated themselves. |
| `components/controller_l1_rsoc.py` | D-25 | `RsocControllerConfig`, the L1 controller of the cell above, with the same single builder reading the same absent file. Seven tests, all on an injected dict. |
| `components/controller_l2_rsoc_battery_system.py` | D-25 | `RsocBatteryControllerConfig` and its `RsocBatteryOperationMode` enum. Same absent file, same total block: ten tests, all on an injected dict. Its `dataclasses_json` field aliases — seven power fields that serialize as `nom_load_soec_in_kW` while their Python names are `…_in_kw` — are the repository's only ones, and they leave with it. |
| `inputs/rSOC_efficiency_curve_data.json` | D-25 | Travels with its only reader. `generic_rsoc.py` was the sole module that opened this efficiency curve; with the component gone, nothing in `hisim/` reads it, so it moves rather than sitting in `hisim/inputs/` as a table for a component that is no longer there. |
| `components/fuel_cell_manufacturer_table.py` | D-25 | Not a component: the six functions of the fuel-cell manufacturer-table path, cut out of three live modules and archived verbatim. `FuelCellConfig.read_config`/`config_fuel_cell` (`generic_fuel_cell.py`), `FuelCellControllerConfig.read_config`/`control_fuel_cell` (`controller_l1_fuel_cell.py`) and `XTPControllerConfig.read_config`/`control_fuel_cell` (`controller_l2_xtp_fuel_cell_ems.py`) all opened `hisim/inputs/fuel_cell_manufacturer_config.json`, which does not exist and never did. The three modules stay in `hisim/`: the first two keep their hand-typed PEM defaults, which build. The third does not — `XTPControllerConfig` now has **no default builder at all** until the conversion batch gives it a preset, and a caller must name every field. |

Two things D-16 deleted rather than moved are not in the table, because a factory is code and not a class:
`RandomNumbersConfig.get_default_config` and `PVSystem.get_default_config`, both with zero callers. One
consequence is worth naming. `hisim/json_executor.py` builds a component whose scenario entry carries no
`configuration` block by invoking the config class's single `get_default_*` classmethod, so the JSON
default-construction path for the live `RandomNumbers` component is closed until the P4 conversion mints its
`standard` preset; both in-repo scenarios pass explicit configuration blocks, so nothing in the tree relies
on it today. A third joined them on **2026-09-12**, on review of the D-7 PR:
`SolarThermalSystemConfig.get_default_solar_thermal_system_manually_calculated_capex`, also with zero callers,
took the private `_compute_device_co2_footprint` with it — the two carried the only in-repo record of the
€797/m² collector price and of the emission-factor breakdown behind it, and the sources both figures cited
now live in git history alone; the D-7 note in
`roadmap/declarative_energy_systems/p4_component_sweep_requirements.md` §11 says so.

One live component gave something up rather than receiving it. `controller_l1_chp.L1CHPController` declared an
optional default connection from `GenericHydrogenStorage`, resolved by module name at runtime; D-29 dropped it
instead of re-pointing it, and no replacement connection to the surviving `HydrogenStorage` was added. The
controller keeps its optional `HydrogenSOC` input — a setup that wants a fuel cell to watch a hydrogen store
wires it explicitly.

`tests/` holds the tests that came with them, so their history stays next to the code they tested, and
`inputs/` the data files whose only reader moved.
