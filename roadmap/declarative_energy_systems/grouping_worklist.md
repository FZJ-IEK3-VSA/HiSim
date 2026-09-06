# Grouping the fleet: the worklist

An inventory of every configuration-dependent branch and condition in the 22 recorded setups
(read 2026-09-06, on the grouping branch), taken as the plan for grouping the whole fleet the way
`household_heatpump_building_sizer` was grouped. The generated state of the work lives in
[grouping_overview.md](grouping_overview.md); this file says what is left, what it costs, and what
has to be fixed before a grouping can be honest. Line numbers are from the inventory date and will
drift; the named constructs will not.

## The map

Eleven of the twelve config-driven setups are one file with the heat generator swapped ("the sizer
skeleton"): the same module-config read with a silent-fallback probe, the same archetype-driven
sizing cascade, one structural fork (`use_battery_and_ems` — the energy manager and the battery
against a directly fed meter), and, until fixed, the same silent zero-share coupling. The twelfth,
`simple_air_conditioner_household_building_sizer`, reads no energy-system field and has no fork.
`household_gas_solar_thermal` is a hidden thirteenth config consumer: it demands an energy-system
config, ignores all four of its fields, and reads one archetype value
(`number_of_dwellings_per_building`).

| Setup | sizer? | structural forks | non-config hazards | zero-share coupling |
|---|---|---|---|---|
| `simple_system_setup_one` / `_two` | no | 0 | 0 | n.a. |
| `default_connections` | no | 0 | 0 | n.a. |
| `basic_household` | no | 0 | 0 | n.a. |
| `basic_household_only_heating` | no | 0 | 0 | n.a. |
| `dynamic_components` | no | 0 | 0 | n.a. |
| `electrolyzer_with_renewables` | no | 0 | 1 (bundled CSV) | n.a. |
| `automatic_default_connections` | no | 1 (hplib-Group wire-or-raise) | 1 (hplib DB content) | n.a. |
| `air_conditioned_house` | no | 0 | 1 (reference-temp CSV; duplicated Seville) | n.a. |
| `household_gas_solar_thermal` | hidden | 0 | 1 (module-config probe) | no |
| `simple_air_conditioner_household_building_sizer` | yes | 0 | 1 (module-config probe) | n.a. |
| `household_gas_building_sizer` | yes | 1 (EMS/battery) | 4 | fixed |
| `household_oil_building_sizer` | yes | 1 | 4 | fixed |
| `household_pellets_building_sizer` | yes | 1 | 4 | fixed |
| `household_wood_chips_building_sizer` | yes | 1 | 4 | fixed |
| `household_hydrogen_boiler_building_sizer` | yes | 1 | 4 | fixed |
| `household_district_heating_building_sizer` | yes | 1 | 4 | fixed |
| `household_electric_heating_building_sizer` | yes | 1 (no HDS axis at all) | 4 | fixed |
| `household_gas_solar_thermal_building_sizer` | yes | 1 | 4 | fixed |
| `household_heatpump_solar_thermal_building_sizer` | yes | 2 (+ hplib-Group) | 5 | fixed |
| `household_heatpump_car_building_sizer` | yes | 3 (+ hplib-Group, surplus charging) | 6 | fixed |
| `household_heatpump_building_sizer` | yes | 2 (+ hplib-Group) | 5 | fixed — **grouped** |

## Fixes owed before or during the grouping

- [x] **The zero-share refusal, fleet-wide** (found 2026-09-06). The silent coupling
  `if share_of_maximum_pv_potential != 0 and use_battery_and_ems:` — a zero share switching off
  the battery and the energy manager as a side effect — existed verbatim in all eleven large
  sizers. Fixed for the heat pump first, then replicated across the other ten with one
  parametrized refusal test; done on this branch.
- [x] **The module-config probe swallows every read error** (fixed 2026-09-06). `read_in_configs`
  now answers `None` only for "no config given" (`None`, `""`, whitespace) and raises `ValueError`
  naming the source and the reason for a missing or unreadable file, invalid JSON, a payload that
  does not decode, and a config declaring neither module; a config handed over as a dict is read
  rather than refused. The thirteen setups' fallback warning no longer claims a failed read.
- [x] **`hasattr(Households, name)` drops a typo'd LPG household silently** (fixed 2026-09-06).
  The whole resolution moved to `ArcheTypeConfig.resolve_lpg_households`, beside the
  `lpg_households` field it reads; all eleven sizers call it, and an unknown name is refused with
  the typo, the registry that holds the legal names and the closest known spellings.
- [ ] **Nine sizers mutate `my_sim.my_module_config` to a dict on the fallback path**; the two
  heat-pump sizers do not, so downstream readers see a different type depending on the sibling.
  One behavior for all eleven.
- [ ] **Four sizers ignore `weather_filepath`/`weather_datasource`**
  (gas_solar_thermal_building_sizer, heatpump_car, heatpump_solar_thermal, hydrogen): the same
  archetype config yields a different weather source across the fleet. Either read them or refuse
  them.
- [ ] **`household_heatpump_car_building_sizer`, EMS off**: the cars are built but nothing feeds
  their charging power to the electricity meter, so EV load leaves the balance entirely. Decide
  whether that world is legal before its grouping states it.
- [ ] **`air_conditioned_house` duplicates its location** (a `"Seville"` string for the PV beside
  `LocationEnum.SEVILLE` for the weather); one edit without the other silently desynchronizes the
  two. One spelling, one place.

- [ ] **`ModularHouseholdConfig.get_hash()` is not round-trip stable** (found 2026-09-06 while
  testing the reader). `pv_azimuth: float = 180` and `pv_tilt: float = 30` hold ints in memory and
  come back as floats from any JSON round trip, so a config built from defaults and the same config
  read from its own file hash differently — and the building sizer hashes configs. Normalise in
  `get_hash()` (or make the defaults floats) and pin the round trip.

## Environment couplings a recording has to pin, not fix

The two `/benchtop/2024-k-rieck-hisim/` existence probes (inputs cache and LPG cache) and the
local-LPG requirement decide input provenance per machine; the hplib-Group fork branches on the
heat-pump database rather than on configuration (wire `TemperatureInputPrimary` to weather for
groups 1 and 4, `KeyError` otherwise — also in the non-sizer `automatic_default_connections`).
None of these is a grouping decision, but each is a reason a recording or a probe run can differ
between machines, so the grouping of an affected setup names them rather than discovering them.

## The grouping order

1. - [ ] **The eight boiler-family sizers as one job** (gas, oil, pellets, wood_chips, hydrogen,
   district_heating, gas_solar_thermal, electric_heating): the identical skeleton means one probe
   list pattern and one judgement pattern, adjusted per file only for the known divergences —
   electric heating has no heat-distribution axis, district heating alone emits the two plot
   options, four ignore the weather-source fields.
2. - [ ] **`household_heatpump_solar_thermal_building_sizer`**: two structural forks and a
   nonstandard skeleton (hand-wired controllers added after the EMS branch, its own component
   order).
3. - [ ] **`household_heatpump_car_building_sizer`**, last and hardest: three interacting forks,
   the `car_surplus_charging` literal that is at once a knob and a fork, and a component count
   that is a property of the occupancy dataset rather than of any configuration — the grouped
   format has no spelling for that today.
4. - [ ] **The nine trivial setups** (`simple_system_setup_one`/`_two`, `default_connections`,
   `basic_household`, `basic_household_only_heating`, `dynamic_components`,
   `electrolyzer_with_renewables`, `simple_air_conditioner_household_building_sizer`,
   `household_gas_solar_thermal`): single configuration, no forks — group whenever convenient;
   their overview sections already state the whole inventory.
5. - [ ] **`automatic_default_connections` and `air_conditioned_house`**: small, but each carries
   one environment coupling (hplib database; reference-temperature CSV plus the duplicated
   location) to pin first.

One axis is shared by all 22 and belongs to no single setup: every `setup_function` invents its
own parameters when `my_simulation_parameters is None`, so the no-parameters-file path is a
configuration of its own. The recorded twins all pin an explicit parameters file, which is the
right answer; the axis is named here so nobody mistakes the twins for covering it.
