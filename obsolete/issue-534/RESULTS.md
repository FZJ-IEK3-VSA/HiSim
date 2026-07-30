# Car double-counting study — setup and results

## Goal

Determine where a car's electricity demand enters a HiSim household, and whether the shipped
`household_heatpump_car_building_sizer` scenario counts the same car twice.

Mobility is split across two models that do not know about each other:

* The **LoadProfileGenerator** owns the trips. `transportation_device_set` decides what the
  household drives (LPG's "Car" devices are electric), and `charging_station_set` decides which
  LPG load type the refuelling is booked to. `Charging At Home with 11 kW` — the default — books
  it to `Electricity`, which is exactly the profile `UtspLpgConnector` publishes as
  `ElectricalPowerConsumption`.
* The **HiSim `Car` / `CarBattery` / `L1Controller` chain** converts LPG's driven metres into
  electricity via `consumption_per_km` and charges the battery through the EMS. It never tells
  the LPG which drivetrain it assumes.

If both are active, the same kilometres are paid for twice.

## Setup

A 2×2 over the two independent axes, all four derived from the shipped scenario by
[`make_scenarios.py`](make_scenarios.py):

|                                  | LPG charging inside the household profile | LPG charging routed elsewhere |
| -------------------------------- | ----------------------------------------- | ----------------------------- |
| **HiSim car components present** | `s4_lpg_car_and_car_element` (as shipped)  | `s3_car_element_only`         |
| **no HiSim car components**      | `s2_lpg_car_only`                          | `s1_no_car`                   |

* **S1** — LPG transportation off (all three mobility fields `null`). No trips at all.
* **S2** — shipped LPG mobility; HiSim car components removed. The car exists only in the LPG profile.
* **S3** — LPG mobility kept, but `charging_station_set` = `Charging At Home with 03.7 kW, output
  results to Car Electricity`, which books the charging to the separate `Electricity for Car
  Charging` load type HiSim never reads. Driving distances and car locations survive; the charging
  energy leaves the household profile. HiSim models the car. **Physically consistent.**
* **S4** — the shipped scenario, unchanged.

Building, weather, PV, heat pump, storages, house battery, EMS and electricity meter are identical
across all four. Removing the HiSim car components also required renumbering the EMS's dynamic
port labels, which carry a running index.

**Simulation period** 1 Jan – 2 Feb 2021 (32 days), 15 min, Aachen, CHR01 Couple both at Work,
643.9 km driven. Post-processing: line/carpet/monthly-bar plots, CSV export, PDF report, OPEX,
CAPEX and KPIs.

Not a calendar month on purpose — see the date defect below.

## Results

| kWh over 32 days | S1 no car | S2 LPG car only | S3 car element only | S4 both *(shipped)* |
| ---------------------------- | --------: | --------------: | ------------------: | ------------------: |
| LPG residents' electricity   |    260.95 |      **410.11** |              279.03 |          **410.11** |
| HiSim car charging → EMS     |         – |               – |           **99.60** |          **101.07** |
| HiSim car driving demand     |         – |               – |               96.59 |               96.59 |
| heat pump                    |    985.64 |          982.71 |             1001.22 |              982.46 |
| PV production                |    961.57 |          961.57 |              961.57 |              961.57 |
| **EMS total consumption**    |   1327.03 |         1467.49 |             1453.78 |         **1564.89** |
| **grid import**              |    582.79 |          688.99 |              681.11 |          **791.35** |
| grid export                  |    217.33 |          183.07 |              188.91 |              188.04 |

Consistency checks, all passing:

* PV production is identical in all four runs.
* S2 and S4 have a bit-identical LPG profile (410.11 kWh) — same connector config.
* S3 and S4 drive an identical 643,914 m, so re-routing the charging load type did not perturb
  the trips.
* HiSim's own `Total electricity consumption` KPI reproduces the EMS row exactly.

## Conclusion

**The double count is confirmed, and it is large.**

The clean measurement of the LPG-side charging is **S2 − S3 = 131.08 kWh** — same trips, same
occupancy, only the load type differs. That is 20.4 kWh/100 km, matching LPG's `Car 2` device
spec of 20 kWh/100 km.

S3 − S1 = 18.08 kWh is the *behavioural* effect of enabling trips at all (different appliance use
when people are out), not charging. This is why S1 alone is the wrong baseline for the comparison.

On top of the 131.08 kWh, S4's HiSim car chain charges another **101.07 kWh** (96.59 kWh driving
demand at 0.15 kWh/km, plus battery losses).

So in the shipped scenario the same 643.9 km is charged
**131.08 + 101.07 = 232.15 kWh → 36.1 kWh/100 km**, roughly 2.4× a real EV.

**Grid import: 791.35 kWh vs 681.11 kWh, +110.24 kWh (+16.2 %)** against the consistent S3.

Either single-car configuration is defensible and the two agree closely — S2 = 688.99 kWh,
S3 = 681.11 kWh grid import, 1.2 % apart. **S3 is the one to prefer**, because HiSim then owns the
charging, so surplus-PV charging and battery state of charge are modelled explicitly instead of
arriving as a fixed LPG profile.

## Two defects surfaced along the way

1. **Fixed.** [`json_executor.py:162`](../../hisim/json_executor.py#L162) called `humps.pascalize()`
   unconditionally on the three LPG mobility fields. `humps.pascalize(None)` returns `''`, not
   `None`, and an empty string cannot decode into a `JsonReference` — so `"travel_route_set": null`
   crashed with `AttributeError: 'str' object has no attribute 'items'`. S1 was impossible to
   express in JSON mode until this was guarded. Same defect class as the double count itself: the
   configuration that avoids the problem was unreachable.

2. **Not fixed, worked around.**
   [`loadprofilegenerator_utsp_connector.py:1125`](../../hisim/components/loadprofilegenerator_utsp_connector.py#L1125)
   formats the LPG start/end dates as `%d-%m-%Y`, but the LPG binary parses them with
   InvariantCulture as `MM-dd-yyyy`. A 31-day run ends 1 Feb → written `01-02-2021` → read as
   2 January, so the LPG returns a single day of profile. A 30-day run ends 31 Jan → `31-01-2021`
   → `Could not convert string to DateTime`. Hence the 32-day period, ending 2 Feb → `02-02-2021`,
   which is unambiguous. Full-year runs are unaffected because they end on 1 January.

## Reproducing

```bash
export PYTHONPATH=/path/to/HiSim     # the editable install may point at a different checkout
cd system_setups/car_double_count_study
python make_scenarios.py
for s in s1_no_car s2_lpg_car_only s3_car_element_only s4_lpg_car_and_car_element; do
    python ../../hisim/hisim_main.py $s.scenario.json one_month_plots_report.simulation.json
done
python compare_results.py
```
