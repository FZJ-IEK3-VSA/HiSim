# Device location, PR 3: the measurement of §7 for the battery

The battery's rows of §7 of `roadmap/device_location_spec.md` for PR 3 (bead hisim-device-location-820d.3): the house
battery stands where the building's technical room is, and its losses, the inverter's plus the cells', go into the
heated zone or out of the house. PR 2's measurement is in `roadmap/device_location_pr2_measurement.md`; both are to be
merged into the spec's §7 when the branches reach main, and these files are then deleted.

## How it was measured

- The heat-pump sizer twin `household_heatpump_building_sizer` (TABULA `DE.N.SFH.09.Gen.ReEx.001.002`, cellar code
  `N`, so its `AUTO` technical room is an unheated space), full year 2021 at 900 s, LoadProfileGenerator profile from
  the local LPG, post-processing `COMPUTE_KPIS`, `WRITE_KPIS_TO_JSON`, `EXPORT_ENERGY_BALANCE`.
- **Before**: the parent of PR 3, `6721d649b` (PR 2, branch `device-location-tanks`, reworked to D-h): the battery's
  inverter and cell losses are two `LOSS` ports of electricity.
- **After**: PR 3. `AUTO` is the recorded twin as it is: the battery follows the technical room, the unheated cellar,
  and its losses leave the house on `HeatLossInWatt`. *Battery in the heated zone*: only the battery pinned to
  `HEATED_ZONE`, the tanks left `AUTO` (unheated cellar), so the row shows the battery's own effect. *Everything in the
  heated zone*: the battery and both tanks pinned to `HEATED_ZONE`, the case of a house without a cellar, against the
  estimate of §7. No other edit of the file: the recorded twin lists the battery under the building's inputs.
- The columns are those of PR 2's table, each a KPI of `all_kpis.json`: tank losses "Standby heat loss of Hot water
  storage" + "Standby heat loss of DHW storage"; battery losses "Battery losses"; space heating the heat
  distribution system's thermal output (the building's theoretical heating demand in brackets); generator heat the heat
  pump's space-heating plus hot-water output; SCOP that heat over the heat pump's electricity (space heating plus hot
  water); the overheating column "Degree-hours of building indoor air temperature above 26.0 Celsius" in °C·h; the
  twin has no cooling device, so the cooling column is the building's theoretical cooling demand. The battery's heat
  into the heated zone is its `HeatToHeatedZoneInWatt` port total of `balance_report.json`, the energy the building's
  dynamic input `HeatToHeatedZoneInWattFromBattery` books.
- The energy balance closed in every run: 8 of 8 declared components close, no circuit fault, the meter publishes what
  it reads, no port unmetered or unlinked. Links: 6 of 6 in both `AUTO` runs (the building's three inputs of the
  battery's and the tanks' heat are idle and book nothing), 7 of 7 with the battery in the heated zone, 9 of 9 with
  everything there.

## Measured, full year 2021 at 900 s

| Location | Tank losses kWh (buffer + DHW) | Battery losses kWh | Battery heat into the heated zone kWh | Space heating kWh (theoretical demand) | Generator heat kWh | HP electricity kWh (SH + DHW) / SCOP | Grid import kWh | °C·h above 26 °C | Cooling demand kWh | Unmet DHW kWh |
|---|---|---|---|---|---|---|---|---|---|---|
| before, `AUTO` = unheated space | 898.3 + 302.5 = 1,200.8 | 958.8 | – | 11,661.5 (11,754.4) | 15,892.6 | 3,841.1 (2,776.0 + 1,065.1) / 4.137 | 1,830.2 | 251.3 | 2,210.7 | 8.6 |
| after, `AUTO` = unheated space | 898.3 + 302.5 = 1,200.8 | 958.8 | 0 | 11,661.5 (11,754.4) | 15,892.6 | 3,841.1 (2,776.0 + 1,065.1) / 4.137 | 1,830.2 | 251.3 | 2,210.7 | 8.6 |
| after, battery in the heated zone | 900.9 + 302.4 = 1,203.3 | 954.5 | 954.5 | 10,917.3 (10,985.8) | 15,152.6 | 3,688.5 (2,624.9 + 1,063.6) / 4.108 | 1,779.7 | 257.6 | 2,462.0 | 7.0 |
| after, everything in the heated zone | 637.3 + 264.7 = 902.0 | 940.5 | 940.5 | 10,309.0 (10,373.1) | 14,242.3 | 3,504.2 (2,448.2 + 1,055.9) / 4.064 | 1,702.5 | 266.5 | 2,759.8 | 8.4 |

- *`AUTO`, unheated space: nothing moves.* All 116 KPIs of `all_kpis.json` are equal to the last digit before and
  after PR 3. The battery's losses now leave on one port, `HeatLossInWatt` (958.8 kWh, the sum of what the two loss
  ports booked before), and the building reads the battery's `HeatToHeatedZoneInWatt` as zeros, an idle transfer. Only
  the port wiring changed, as the spec's §5 says ("the battery's PR moves only the heated-zone cases").
- *Battery in the heated zone.* Its 954.5 kWh enter the building as internal gains: space heating falls by 744 kWh,
  6.4 %, the heat pump's space-heating electricity by 151 kWh and the grid import by 51 kWh; 78 % of the battery's
  losses replace heating, because in this twin the battery cycles all year (26 MWh of PV) and the house heats about
  5,000 h of it. The rest raises the house in summer: +6.3 °C·h above 26 °C and +251 kWh of theoretical cooling demand.
  The SCOP falls from 4.137 to 4.108, as in PR 2's heated-zone case: less space heating at the same hot water. The
  battery's own losses move by 4 kWh through the heat pump's changed consumption, which the energy manager's dispatch
  follows.
- *Everything in the heated zone.* Space heating falls by 1,352 kWh, 11.6 %, against the estimate of 9–11 % in §7
  (the owner's "about 10 %"); alone, the tanks saved 641 kWh (PR 2's heated-zone row) and the battery 744 kWh, so
  together a little less than the sum, since a warmer house uses less of each gain. The summer share costs
  +15.2 °C·h above 26 °C and +549 kWh of theoretical cooling demand.

## Goldens

Not blessed here (they are blessed on main through `golden-update.yml`). The `AUTO` run shows that no KPI of a twin
whose battery stands in its TABULA row's unheated cellar moves; every recorded twin with a battery stands on such a
row. What may move in the goldens is the battery's result columns: `HeatToHeatedZoneInWatt` and `HeatLossInWatt` are
new, and `InverterLossInWatt` and `CellLossInWatt` keep their numbers. The twins re-recorded by this PR list the
battery under the building's inputs (12 twins with a battery: the eleven building sizers with an energy manager and
`dynamic_components`, whose two batteries, without a building, are pinned outdoors).
