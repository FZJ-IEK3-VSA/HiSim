# Car double-counting study

Four scenarios that isolate where a car's electricity demand enters a HiSim household, and
show that the shipped `household_heatpump_car_building_sizer` configuration counts the same
car twice.

## The question

Mobility is split across two models that do not know about each other:

* The **LoadProfileGenerator** owns the trips. `travel_route_set` decides how far the household
  drives, `transportation_device_set` decides *what* it drives (LPG's "Car" devices are electric,
  its "Gasoline Car" devices are not), and `charging_station_set` decides which LPG load type the
  refuelling is booked to.
* The **HiSim `Car` component** only converts LPG's driven metres into fuel or electricity via
  `consumption_per_km`. It never tells the LPG which drivetrain it assumes.

`UtspLpgConnector` reads `Results/SumProfiles.HH1.Electricity.csv` and publishes it as
`ElectricalPowerConsumption`. `Charging At Home with 11 kW` — the default — books its charging
to exactly that load type, so the residents' profile already contains the EV's home charging
before HiSim's `Car` / `CarBattery` / `L1Controller` chain charges the same car again.

## The 2x2

|                                  | LPG charging inside the household profile | LPG charging routed elsewhere |
| -------------------------------- | ----------------------------------------- | ----------------------------- |
| **HiSim car components present** | `s4_lpg_car_and_car_element` (as shipped)  | `s3_car_element_only`         |
| **no HiSim car components**      | `s2_lpg_car_only`                          | `s1_no_car`                   |

The two axes are set independently:

* **S1** switches LPG transportation off (all three mobility fields `null`). No trips at all.
* **S2** keeps the shipped LPG mobility, but removes the HiSim car components. The car exists
  only inside the LPG profile.
* **S3** keeps the HiSim car components and keeps LPG transportation, but sets
  `charging_station_set` to `Charging At Home with 03.7 kW, output results to Car Electricity`,
  which books the charging to the separate `Electricity for Car Charging` load type. HiSim never
  reads that file, so the driving distances and car locations survive while the charging energy
  leaves the household profile. **This is the physically consistent configuration.**
* **S4** is the shipped scenario, unchanged.

Everything else — building, weather, PV, heat pump, storages, house battery, EMS, electricity
meter — is identical across all four.

## Running

```bash
# repo root must be on PYTHONPATH: the editable install may point at a different checkout
export PYTHONPATH=/path/to/HiSim
cd system_setups/car_double_count_study
python make_scenarios.py            # regenerate the four scenarios from the shipped one
for s in s1_no_car s2_lpg_car_only s3_car_element_only s4_lpg_car_and_car_element; do
    python ../../hisim/hisim_main.py $s.scenario.json one_month_plots_report.simulation.json
done
python compare_results.py           # side-by-side electricity balance
```

Results land in `results/<scenario>_<timestamp>/`, with line plots, carpet plots, monthly bar
charts, the exported CSV, `all_kpis.json` and the generated PDF report.

## Why the period is 1 Jan – 2 Feb and not a calendar month

`UtspLpgConnector` formats the LPG start/end dates as `%d-%m-%Y`
([loadprofilegenerator_utsp_connector.py:1125](../../hisim/components/loadprofilegenerator_utsp_connector.py#L1125)),
but the LPG binary parses them with InvariantCulture as `MM-dd-yyyy`. A 31-day run ends on
1 Feb, written `01-02-2021`, which the LPG reads as **2 January** — it would then simulate a
single day and HiSim would run off the end of the profile. A 30-day run ends on 31 Jan, written
`31-01-2021`, which throws `Could not convert string to DateTime`. 32 days ends on 2 Feb,
written `02-02-2021`, which is unambiguous. Full-year runs are unaffected because they end on
1 January.
