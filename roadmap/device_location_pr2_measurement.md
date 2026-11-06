# Device location, PR 2: the measurement of §7

The filled table of §7 of `roadmap/device_location_spec.md` for PR 2 (bead hisim-device-location-820d.2): the buffer
and the hot-water tank follow the building's technical room and lose their standby heat against the temperature of
that place. To be merged into the spec's §7 when the branch reaches main; this file is then deleted.

## How it was measured

- Both sizer twins, `household_heatpump_building_sizer` and `household_gas_building_sizer` (TABULA
  `DE.N.SFH.09.Gen.ReEx.001.002`, cellar code `N`, floor b = 0.5), full year 2021 at 900 s, LoadProfileGenerator
  profile from the local LPG, post-processing `COMPUTE_KPIS`, `WRITE_KPIS_TO_JSON`.
- **Before**: the parent of PR 2, `2a1410252` (PR 1, branch `device-location-building`): both tanks lose against a
  fixed 20 °C and book the loss as a `LOSS`.
- **After**: PR 2. `AUTO` is the recorded twin as it is: the tanks follow the technical room, here an unheated space,
  and lose against `Building.TemperatureUnheatedCellarInCelsius`. *Pinned heated zone*: both tanks pinned to
  `HEATED_ZONE`, and the building's inputs list both tanks, so it grows the inputs of their heat (see "Note" below).
  *Pinned outdoors*: both tanks pinned to `OUTDOORS`, their surrounding temperature read from
  `Weather.TemperatureOutside`.
- Every figure is a KPI of `all_kpis.json`. Tank losses: "Standby heat loss of Hot water storage" + "Standby heat loss
  of DHW storage". Space heating: the heat distribution system's thermal output, the heat the building receives
  (the building's theoretical heating demand in brackets). Generator heat: the heat pump's space-heating plus
  hot-water output, or the boiler's total thermal energy. SCOP: that heat over the heat pump's electricity
  (space heating plus hot water). The battery is untouched by PR 2 (PR 3). The twins have no cooling device, so the
  cooling column is the building's theoretical cooling demand, and the overheating column is the KPI
  "Degree-hours of building indoor air temperature above 26.0 Celsius", in °C·h, not hours.
- The energy balance closed in every run: every declared component closes, every link closes, no circuit fault, no
  unlinked port (the heat-pump heated-zone run: 8 of 8 links, the two tank-to-building links included; the gas
  heated-zone run: 7 of 7).

## Measured, full year 2021 at 900 s

| Twin | Location | Tank losses kWh (buffer + DHW) | Battery losses kWh | Space heating kWh (theoretical demand) | Generator heat kWh | HP electricity kWh / SCOP | Gas kWh | °C·h above 26 °C | Cooling demand kWh | Unmet DHW kWh |
|---|---|---|---|---|---|---|---|---|---|---|
| HP | before (20 °C) | 673.2 + 274.6 = 947.8 | 958 | 11,663 (11,751) | 15,644 | 3,780 (2,725 SH + 1,055 DHW) / 4.139 | – | 251.5 | 2,212 | 7.6 |
| HP | after, `AUTO` = unheated space | 898.3 + 302.5 = 1,200.8 | 959 | 11,662 (11,754) | 15,893 | 3,841 (2,776 SH + 1,065 DHW) / 4.137 | – | 251.3 | 2,211 | 8.6 |
| HP | after, pinned heated zone | 634.1 + 265.1 = 899.2 | 941 | 11,021 (11,092) | 14,952 | 3,649 (2,593 SH + 1,055 DHW) / 4.098 | – | 261.1 | 2,464 | 7.4 |
| HP | after, pinned outdoors | 1,158.3 + 339.8 = 1,498.1 | 961 | 11,655 (11,746) | 16,183 | 3,908 (2,829 SH + 1,078 DHW) / 4.141 | – | 251.4 | 2,211 | 8.2 |
| Gas | before (20 °C) | 173.4 + 264.8 = 438.2 | 752 | 10,946 (10,949) | 14,429 | – | 15,183 | 247.6 | 2,174 | 0.0 |
| Gas | after, `AUTO` = unheated space | 310.2 + 294.2 = 604.4 | 752 | 10,946 (10,948) | 14,591 | – | 15,353 | 247.6 | 2,174 | 0.0 |
| Gas | after, pinned heated zone | 160.2 + 258.3 = 418.5 | 752 | 10,631 (10,632) | 14,090 | – | 14,833 | 250.6 | 2,272 | 0.0 |
| Gas | after, pinned outdoors | 464.3 + 330.0 = 794.3 | 752 | 10,946 (10,948) | 14,780 | – | 15,553 | 247.6 | 2,174 | 0.0 |

Against the estimate of §7:

- *Default, unheated space.* The tanks lose 253 kWh more in the heat-pump twin (estimate 250–350) and 166 kWh more in
  the gas twin (estimate 100–150); the building does not move (space heating −1 kWh, 0 kWh). The heat pump makes
  249 kWh more heat for 61 kWh more electricity (estimate 80–100), its SCOP unchanged; the boiler burns 170 kWh more
  gas.
- *Pinned heated zone.* The tanks lose a little less than at 20 °C (899 against 948 kWh; the house is warmer than
  20 °C most of the year), and the loss enters the building: space heating falls by 642 kWh, 5.5 %, in the heat-pump
  twin and by 315 kWh, 2.9 %, in the gas twin. The estimate of 9–11 % counted the battery's 958 kWh as well, which
  PR 3 adds. The summer share raises the house: +9.6 °C·h above 26 °C and +252 kWh of theoretical cooling demand in
  the heat-pump twin. The heat pump's SCOP falls from 4.137 to 4.098, its space-heating electricity by 183 kWh; the
  boiler burns 520 kWh less gas than in the default case.
- *Pinned outdoors.* The tanks lose 1,498 kWh (heat pump) and 794 kWh (gas), 58 % and 81 % more than at 20 °C.
- The battery's losses move in the heat-pump runs only through the heat pump's changed consumption (by up to
  17 kWh, in the heated zone); they follow the location with PR 3.

## Goldens

Not blessed here (they are blessed on main through `golden-update.yml`). Every recorded twin with a tank moves: 14
twins (`basic_household_only_heating`, `automatic_default_connections`, `household_gas_solar_thermal` and the eleven
building-sizer twins with a tank, district heating and electric heating included through their hot-water tank), whose
golden files number 70 of the 116 under `golden_references/`. The twins without a tank do not move.

## Confirmation after the rework of D-h

PR 2 was reworked to owner decision D-h (2026-11-06): each tank has three fixed temperature inputs and two fixed
released-heat outputs, `HeatToHeatedZoneInWatt` and `HeatLossInWatt`, its `StandbyHeatLoss` is gone, and every twin
lists both tanks under the building. The physics is unchanged, so the numbers above must not move. Re-run on the
reworked branch, heat-pump twin, full year 2021 at 900 s, local LPG, `COMPUTE_KPIS`, `WRITE_KPIS_TO_JSON`,
`EXPORT_ENERGY_BALANCE`:

| Location | Tank losses kWh (buffer + DHW) | Battery losses kWh | Space heating kWh (theoretical demand) | HP electricity kWh (SH + DHW) | °C·h above 26 °C | Cooling demand kWh | Unmet DHW kWh |
|---|---|---|---|---|---|---|---|
| `AUTO` = unheated space | 898.3 + 302.5 | 958.8 | 11,661.5 (11,754.4) | 2,776.0 + 1,065.1 | 251.3 | 2,210.7 | 8.6 |
| pinned heated zone | 634.1 + 265.1 | 940.7 | 11,021.1 (11,092.4) | 2,593.3 + 1,055.4 | 261.1 | 2,464.0 | 7.4 |

Every figure equals its row of the table above to the digits shown there. The heated-zone run now needs no edit
beyond pinning the two tanks: the recorded twin lists both under the building, and the run's balance closes with
8 of 8 links and no unlinked port; in the `AUTO` run the building reads zeros from both tanks, an idle transfer, and
the balance shows 6 of 6 links (the two idle ones book nothing) and no unlinked port.

## Note: a tank in the heated zone in a file (resolved by D-h)

The first build of PR 2 wired a tank's surrounding temperature and the port of its loss by its location, so a file
was right only for the house it was recorded on: a tank whose `AUTO` location resolved to the heated zone in a file
whose building did not list it published room heat that nothing read, and listing it was refused (EF-23) when the
tank stood elsewhere. D-h removed the dependence: the three temperature inputs and the two released-heat outputs are
the same in every location, the building lists every tank as a bare item in every twin, and a run refuses room heat
that no component reads before its first step.
