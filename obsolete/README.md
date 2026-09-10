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
imported from here.

This directory is a staging area. Its contents move on to the separate obsolete repository, which is why a
file may sit here for a while before disappearing from this one.

## What is here, and why

| Moved | Decision | Reason |
|---|---|---|
| `components/advanced_heat_pump_hplib.py` | D-1 | Zero setup instantiations. Its sibling `MoreAdvancedHeatPumpHPLibConfig` does the same job in plain floats and is what the setups use, while this one is the only class whose sizable fields are `Quantity`-typed — which the sizing kernel cannot express. Converting it would have meant either lowering its types or teaching the kernel `Quantity`, for a class with no consumer. |
| `components/controller_l1_heatpump.py` | D-2 | Zero call sites anywhere — the last of the plan's three D13 "zombies" still in the tree, and the only one that still imported cleanly. Converting it would have minted three preset names (`space_heating`/`buffer`/`dhw`) into the wire vocabulary for a controller no setup, test or component builds. It has no test file, so nothing moves beside it. |
| `components/advanced_fuel_cell_controller.py` | D-8 | Zero call sites, and unrunnable if it had any: `ExtendedControllerSimulation.control_*` reads `ExtendedControllerConfig` as bare class attributes, and that class is a `@dataclass` with no field defaults, so the first control call raises `AttributeError`. Its test passes only because it exercises `_sensor_index` and `__init__` and never a control method. |
| `components/configuration_fuel_cell_controller.py` | D-8 | `ExtendedControllerConfig`, `GasControllerConfig` and `CHPControllerConfig`, moved out of `hisim/components/configuration.py`, which was a shared file and keeps everything else. The retired controller above was their only user in the repository. |
| `components/controller_mpc.py` | D-16 | Zero setup uses. A model-predictive prototype whose config carries 31 runtime result buffers among its 49 fields, so converting it would have forced those buffers into the file format. With `controller_pid.py` it was one of the two readers of the six construction-time 5R1C singleton keys the Building writes, and it was the only consumer of the 24 h price forecast the tariff provider publishes. |
| `components/controller_pid.py` | D-16 | Zero setup uses. The repository's only PID prototype, and the second of the two readers of the Building's 5R1C singleton keys. |
| `components/generic_windturbine.py` | D-16 | Zero setup uses. The KPI vocabulary keeps its `Windturbine production` entries — those are keyed on `ComponentType.WINDTURBINE` in `loadtypes.py`, not on this module — so no recorded result changes. |
| `components/generic_price_signal.py` | D-16 | Zero setup uses, and superseded: `hisim/components/tariff_provider.py` publishes the same two `SingletonSimRepository` forecast keys from the same `TariffContract` the billing engine reads. The parallel phase the two were written for is over. |
| `components/simple_hot_water_storage_controller.py` | D-16 | `SimpleHotWaterStorageController` and its config, moved out of `hisim/components/simple_water_storage.py`, which keeps the three live storage classes. Zero call sites of any kind. It carried one of the two reads of the writer-less `WATERMASSFLOWRATEOFHEATGENERATOR` singleton key; the other stays behind with `SimpleHotWaterStorage`. |
| `components/configuration_legacy.py` | D-16 | `WarmWaterStorageConfig`, `LoadConfig`, `ElectricityDemandConfig` and `PVConfig`, moved out of `hisim/components/configuration.py`. Each verified to have zero live references; `WarmWaterStorageConfig` is named only inside a commented-out block of `generic_heat_pump.py`. Their neighbours `HouseholdWarmWaterDemandConfig` (live), `HydrogenStorageConfig` and `AdvElectrolyzerConfig` (D-25/D-29) stay. |

`tests/` holds the tests that came with them, so their history stays next to the code they tested.
