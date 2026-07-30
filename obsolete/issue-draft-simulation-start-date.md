# Simulations silently start on 1 January regardless of `start_date`

## Summary

`SimulationParameters.start_date` only contributes its **year** and, via `end_date - start_date`,
the **duration**. The month and day are discarded, and every profile-reading component indexes its
data from 1 January. A simulation declared to run 1 March – 31 May therefore consumes 1 January –
31 March weather and occupancy, while the results are labelled with March–May timestamps.

Nothing warns.

## Evidence

`SimulationParameters.__init__` keeps only these two derivations:

```python
self.duration = end_date - start_date
self.year: int = int(start_date.year)
```

`timesteps` is computed from `duration`. Grepping `hisim/` for readers of `start_date` returns
eight sites, and all but three use it purely to obtain the duration or the year.

The components then anchor themselves at 1 January:

| component | behaviour |
| --------- | --------- |
| [`weather.py`](../hisim/components/weather.py) | builds a full-year series from 1 January and reads it with `self.temperature_list[timestep]` (line 624 ff.), so `timestep 0` is always 1 January |
| [`loadprofilegenerator_utsp_connector.py`](../hisim/components/loadprofilegenerator_utsp_connector.py) | `compute_lpg_start_and_end_date` (line 81) asks the local LPG for 1 January onwards; `load_result_files_and_transform_to_lists` reads the first `steps_desired_in_minutes` rows and labels them from 1 January (line 1749) |
| [`generic_car.py`](../hisim/components/generic_car.py) | `Car.build` labels the driving profile from 1 January (line 569) |

## Reproduction

```python
import datetime
from hisim.components import weather
from hisim.simulationparameters import SimulationParameters
from hisim.sim_repository import SimRepository

def build(sp):
    w = weather.Weather(
        config=weather.WeatherConfig.get_default(location_entry=weather.LocationEnum.AACHEN),
        my_simulation_parameters=sp,
    )
    w.set_sim_repo(SimRepository())
    w.i_prepare_simulation()
    return w

full = SimulationParameters.full_year(year=2021, seconds_per_timestep=900)
march = SimulationParameters.three_months_only(year=2021, seconds_per_timestep=900)  # start_date = 1 March
w_full, w_march = build(full), build(march)

offset = (datetime.date(2021, 3, 1) - datetime.date(2021, 1, 1)).days * 96
print(sum(w_march.temperature_list[:96]) / 96)          # first 24 h of the "March" run
print(sum(w_full.temperature_list[:96]) / 96)           # first 24 h of 1 January
print(sum(w_full.temperature_list[offset:offset+96]) / 96)  # first 24 h of the real 1 March
```

Observed, Aachen 2021:

| first 24 h of the run | mean outside temperature |
| --------------------- | -----------------------: |
| 1 January run         |                −0.614 °C |
| **1 March run**       |            **−0.614 °C** |
| true 1 March          |                +8.392 °C |

The March run is identical to the January run and 9 K away from March.

## Three places disagree with the 1-January convention

The anchoring is at least self-consistent across weather and occupancy. These three are not, which
makes the effect harder to spot and produces results that contradict each other:

1. **[`loadprofilegenerator_utsp_connector.py:387`](../hisim/components/loadprofilegenerator_utsp_connector.py#L387)**
   — the UTSP path is the only code that requests the *real* window:
   ```python
   start_date = self.my_simulation_parameters.start_date.strftime("%Y-%m-%d")
   ```
   So the same scenario yields seasonally correct occupancy under `USE_UTSP` and January occupancy
   under `USE_LOCAL_LPG` / `USE_PREDEFINED_PROFILE`. The UTSP result is then relabelled as starting
   1 January (line 1749) before `utils.convert_lpg_data_to_utc` applies the daylight-saving
   corrections, so the DST fix-ups are applied to the wrong dates as well.

2. **[`solar_thermal_system.py:652`](../hisim/components/solar_thermal_system.py#L652)** — builds its
   time index from the real `start_date`, so it believes it is March while the weather component is
   feeding it January irradiance.

3. **[`simulator.py:356-361`](../hisim/simulator.py#L356-L361)** — labels the results DataFrame with
   the real `start_date`:
   ```python
   df_index = pd.date_range(
       start=self._simulation_parameters.start_date,
       end=self._simulation_parameters.end_date,
       freq=f"{self._simulation_parameters.seconds_per_timestep}s",
   )[:-1]
   ```
   Every plot, exported CSV and PDF report therefore claims a season the underlying data is not
   from.

## How reachable is this

Two shipped presets start mid-year:

* [`SimulationParameters.three_months_only()`](../hisim/simulationparameters.py#L168) — starts **1 March**
* [`SimulationParameters.three_months_with_plots_only()`](../hisim/simulationparameters.py#L177) — starts **1 June**

and `three_months` is a selectable duration in the HPC harness
([`hisim_setup_runner.py:32`](../scripts/hpc_harness/runners/hisim_setup_runner.py#L32)). Any run
using them is affected. `.simulation.json` files also carry a free-form `start_date`, so the same
applies to any JSON-mode run that does not start on 1 January.

Runs that start on 1 January — which is all of the shipped scenarios — are correct.

## Impact

* Seasonal studies are silently wrong: a summer run is simulated with winter weather and winter
  occupancy, which inverts heating demand, PV yield and heat-pump COP.
* `USE_UTSP` and `USE_LOCAL_LPG` are not comparable for non-1-January runs.
* Output timestamps do not describe the data they carry, so the error is invisible in plots and
  reports — the axes look right.

## Possible fixes

1. **Fail loudly (small).** Reject, or at minimum warn on, a `start_date` that is not 1 January of
   its year. This does not make mid-year runs work, but it stops silently producing January physics
   under a March label. `three_months_only` and `three_months_with_plots_only` would need to be
   redefined or removed, and the HPC harness `three_months` option with them.

2. **Honour the start date (the real fix).** Offset every profile read by the start day-of-year —
   weather, the LPG profiles, the car profiles — label the frames with the actual dates, and drop
   the divergent handling in the UTSP path so all three acquisition modes agree. This changes
   results for any non-1-January run and touches several components, so it wants its own test
   coverage: a run starting 1 March should produce the same values as time steps 5664 ff. of the
   corresponding full-year run.

Option 1 is worth doing immediately regardless of whether option 2 is scheduled.

## Not related to the date-format fix

This is independent of the `%d-%m-%Y` / `MM-dd-yyyy` defect (issue #534, first item). That fix
changed only the *format* of the strings handed to the LPG binary; the 1-January anchoring and the
duration arithmetic are unchanged by it.
