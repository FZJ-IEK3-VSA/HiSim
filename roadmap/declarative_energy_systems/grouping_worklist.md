# Grouping the fleet: the worklist

An inventory of every configuration-dependent branch and condition in the 22 recorded setups
(read 2026-09-06, on the grouping branch), taken as the plan for grouping the whole fleet the way
`household_heatpump_building_sizer` was grouped. The generated state of the work lives in
[grouping_overview.md](grouping_overview.md); this file says what is left, what it costs, and what
has to be fixed before a grouping can be honest. Line numbers are from the inventory date and will
drift; the named constructs will not.

**State, 2026-09-07.** The owner decided to group inside P3 rather than defer to P5, and all
thirteen configuration-driven setups are grouped: the exemplar, the seven skeleton sizers, the two
solar-thermal sizers, the car sizer and the two setups that have a configuration axis but no fork
(`simple_air_conditioner_household_building_sizer` and the hidden thirteenth,
`household_gas_solar_thermal`). Every probe column of every one of them reproduces its flat
recording byte for byte. The nine single-configuration setups are still flat, by decision rather
than by omission: they read no module configuration, so a probe list for them would hold one column
and a grouped file would be the twin with a header line added.

## The map

Eleven of the twelve config-driven setups are one file with the heat generator swapped ("the sizer
skeleton"): the same module-config read with a silent-fallback probe, the same archetype-driven
sizing cascade, one structural fork (`use_battery_and_ems` — the energy manager and the battery
against a directly fed meter), and, until fixed, the same silent zero-share coupling. The twelfth,
`simple_air_conditioner_household_building_sizer`, reads no energy-system field and has no fork.
`household_gas_solar_thermal` is a hidden thirteenth config consumer: it demands an energy-system
config, ignores all four of its fields, and reads one archetype value
(`number_of_dwellings_per_building`).

| Setup | sizer? | structural forks | non-config hazards | zero-share coupling | grouping |
|---|---|---|---|---|---|
| `simple_system_setup_one` / `_two` | no | 0 | 0 | n.a. | flat |
| `default_connections` | no | 0 | 0 | n.a. | flat |
| `basic_household` | no | 0 | 0 | n.a. | flat |
| `basic_household_only_heating` | no | 0 | 0 | n.a. | flat |
| `dynamic_components` | no | 0 | 0 | n.a. | flat |
| `electrolyzer_with_renewables` | no | 0 | 1 (bundled CSV) | n.a. | flat |
| `automatic_default_connections` | no | 1 (hplib-Group wire-or-raise) | 1 (hplib DB content) | n.a. | flat |
| `air_conditioned_house` | no | 0 | 1 (reference-temp CSV; duplicated Seville) | n.a. | flat |
| `household_gas_solar_thermal` | hidden | 0 | 1 (module-config probe) | no | **grouped** |
| `simple_air_conditioner_household_building_sizer` | yes | 0 | 1 (module-config probe) | n.a. | **grouped** |
| `household_gas_building_sizer` | yes | 1 (EMS/battery) | 4 | fixed | **grouped** |
| `household_oil_building_sizer` | yes | 1 | 4 | fixed | **grouped** |
| `household_pellets_building_sizer` | yes | 1 | 4 | fixed | **grouped** |
| `household_wood_chips_building_sizer` | yes | 1 | 4 | fixed | **grouped** |
| `household_hydrogen_boiler_building_sizer` | yes | 1 | 4 | fixed | **grouped** |
| `household_district_heating_building_sizer` | yes | 1 | 4 | fixed | **grouped** |
| `household_electric_heating_building_sizer` | yes | 1 (no HDS axis at all) | 4 | fixed | **grouped** |
| `household_gas_solar_thermal_building_sizer` | yes | 1 | 4 | fixed | **grouped** |
| `household_heatpump_solar_thermal_building_sizer` | yes | 2 (+ hplib-Group) | 5 | fixed | **grouped** |
| `household_heatpump_car_building_sizer` | yes | 3 (+ hplib-Group, surplus charging) | 6 | fixed | **grouped** |
| `household_heatpump_building_sizer` | yes | 2 (+ hplib-Group) | 5 | fixed | **grouped** (the exemplar) |

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
  One behavior for all eleven. `read_in_configs` is string-only by decision (review round of
  2026-09-06: it refuses a non-string with a `TypeError`), so this repair has to settle what the
  attribute is allowed to hold rather than widening the reader to accept both.
- [ ] **Four sizers ignore `weather_filepath`/`weather_datasource`**
  (gas_solar_thermal_building_sizer, heatpump_car, heatpump_solar_thermal, hydrogen): the same
  archetype config yields a different weather source across the fleet. Either read them or refuse
  them.
- [x] **`household_heatpump_car_building_sizer`, EMS off** — the premise was wrong, and the
  grouping is what showed it (2026-09-07). The recorded `metered_directly` option has the
  electricity meter taking `L1EVChargeControl_1.BatteryChargingPowerToEMS` as an
  `ELECTRICITY_CONSUMPTION_UNCONTROLLED` input at weight 999, so the EV load is in the balance in
  both worlds: the energy manager takes it as a controlled consumption when there is one and the
  meter takes it directly when there is not. The meter's `connect_automatically=True` on the EMS-off
  path is what wires it. Both wirings are written out in full in the grouped file's two options, so
  a reader can hold them side by side instead of re-deriving them; nothing about that world needed
  deciding before the grouping could state it.
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

Where that lands, after 2026-09-07: the two heat-pump setups grouped on that day
(`household_heatpump_solar_thermal_building_sizer`, `household_heatpump_car_building_sizer`) name
the hplib-Group fork in their probe lists' headers, together with the car setup's
`car_surplus_charging` literal and its dataset-determined car count, and each header says that
every column of the list stands on one side of the branch it names. The exemplar's own probe list
predates that habit and still says nothing about the fork; adding the paragraph to it would change
no recording and no proof, and is the one loose end of the pass.

## The grouping order

1. - [x] **The eight boiler-family sizers as one job** (gas, oil, pellets, wood_chips, hydrogen,
   district_heating, gas_solar_thermal, electric_heating): the identical skeleton means one probe
   list pattern and one judgement pattern, adjusted per file only for the known divergences —
   electric heating has no heat-distribution axis, district heating alone emits the two plot
   options, four ignore the weather-source fields. Done 2026-09-07, and the skeleton held: seven of
   the eight produced the exemplar's table verbatim with the generator's two rows renamed, and each
   is one `electricity_management` variant over the meter, the battery and the manager plus four to
   seven overrides. The two named divergences showed up exactly where the inventory said they
   would — electric heating has four columns instead of five and no emitter rows at all, district
   heating has no buffer store and its controller is uniform across every column — and the plot
   options turned out to be a post-processing matter that the component table never sees.
2. - [x] **`household_heatpump_solar_thermal_building_sizer`**: two structural forks and a
   nonstandard skeleton (hand-wired controllers added after the EMS branch, its own component
   order). Done 2026-09-07. The nonstandard order cost nothing — the recorder observes it and the
   realizer lays the dissolved components out in the recording's order — and the solar thermal pair
   is uniform in all five columns, because the collector's area is four square metres per dwelling
   and no probe moves the dwelling count. The hplib-Group fork is named in the probe list's header
   rather than probed, because no configuration reaches it.
3. - [x] **`household_heatpump_car_building_sizer`**, last and hardest: three interacting forks,
   the `car_surplus_charging` literal that is at once a knob and a fork, and a component count
   that is a property of the occupancy dataset rather than of any configuration — the grouped
   format has no spelling for that today. Done 2026-09-07, and it was the least eventful of the
   three: only one of the three forks is reachable from a module configuration, so the table is the
   exemplar's. `car_surplus_charging` is a literal in the setup body and the hplib Group comes from
   the shipped database, so both are named in the probe list's header as branches every column
   stands on the same side of. The car count is a dataset property and all five columns share one
   occupancy, so the count is constant and the format never has to spell it; that gap is still real
   for a consumer who wants two cars from a file.
4. - [x] **The trivial setups** (`simple_system_setup_one`/`_two`, `default_connections`,
   `basic_household`, `basic_household_only_heating`, `dynamic_components`,
   `electrolyzer_with_renewables`, `simple_air_conditioner_household_building_sizer`,
   `household_gas_solar_thermal`): single configuration, no forks — group whenever convenient;
   their overview sections already state the whole inventory. Split in two on 2026-09-07. The two
   that read a module configuration are grouped: each has a configuration axis even though it has
   no fork, and a two-column probe list on that axis makes "this setup has no structure" a
   byte-for-byte result instead of an assumption. `simple_air_conditioner_household_building_sizer`
   probes the building code, whose cascade stops at the building because the air conditioner and
   its controller take their own class defaults; `household_gas_solar_thermal` probes the dwelling
   count, the one field it reads out of the configuration it demands, and the cascade stops at the
   domestic hot water storage. Both grouped files declare no variant group and carry one override,
   and the overview page has prose for exactly that shape. The other six read no module
   configuration at all, so their probe list would hold one column and their grouped file would be
   the twin with a header line added; they stay flat.
5. - [ ] **`automatic_default_connections` and `air_conditioned_house`**: small, but each carries
   one environment coupling (hplib database; reference-temperature CSV plus the duplicated
   location) to pin first. Still open, and untouched by the 2026-09-07 pass: neither reads a module
   configuration, so neither has an axis a probe list could stand on until the coupling above it is
   pinned.

One axis is shared by all 22 and belongs to no single setup: every `setup_function` invents its
own parameters when `my_simulation_parameters is None`, so the no-parameters-file path is a
configuration of its own. The recorded twins all pin an explicit parameters file, which is the
right answer; the axis is named here so nobody mistakes the twins for covering it.
