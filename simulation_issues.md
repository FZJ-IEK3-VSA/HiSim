# HiSim simulation — suggested fixes and improvements

Collected 2026-07-05 while building the RenoVisor translator (`hisim/renovisor/`). Each item is
self-contained so it can be reviewed, implemented and tested one by one. Items 1–3 are bugs
observed or directly adjacent to observed behavior; the rest are fidelity improvements that
would let the translator map more of the RenoVisor contract (everything the translator cannot
express today shows up as `ignored`/`approximated` in its mapping report, so progress is
directly measurable).

---

## 1. `ZeroDivisionError` in `Building` for TABULA rows without a door — **observed crash**

**Where:** `hisim/components/building.py`, `BuildingInformation.set_door_heat_transfer_parameter`
(around line 2843).

**What happens:** the door U-value is computed as

```python
self.door_u_value_in_watt_per_m2_per_kelvin = (door_u_value_in_watt_per_m2_per_kelvin * area_door_1) / (
    area_door_1
)
```

`(u * a) / a` is mathematically just `u` — unless `a == 0`, in which case it raises
`ZeroDivisionError`. Many TABULA rows list no separate door (`A_Door_1 = 0`): **22 of the 124
Irish rows** (all of `IE.N.SFH.05.*`, `IE.N.TH.04.*`, `IE.N.TH.06.*`, `IE.N.AB.08–10.*`) and
**146 German rows**. Any simulation using such a building code crashes during
`BuildingInformation.__init__`, i.e. before the first timestep.

**Observed with:** `IE.N.SFH.05.Gen.ReEx.001.001` (a 1968 Irish detached house — a completely
ordinary request).

**Suggested fix:** when `area_door_1 == 0` the building simply has no separate door entry in
TABULA; its door contributes no heat loss:

```python
self.door_u_value_in_watt_per_m2_per_kelvin = (
    door_u_value_in_watt_per_m2_per_kelvin if area_door_1 > 0 else 0.0
)
```

`heat_conductance_door_in_watt_per_kelvin` (a few lines below) then evaluates to 0 as well
because the scaled door area is 0 — consistent.

**Testing:** unit test that constructs `BuildingInformation` with
`building_code="IE.N.SFH.05.Gen.ReEx.001.001"` and asserts it initializes with
`door_u_value == 0`; plus a regression test with a normal German code asserting the U-value is
unchanged by the fix (for `a > 0` the expression is algebraically identical).

**Follow-up once fixed:** remove the workaround in
`hisim/renovisor/tabula_ie.py::_is_usable_by_building_component`, which currently excludes
those rows from selection and silently shifts affected requests to a neighbouring age band
(e.g. a 1968 house is simulated with the 1950–1966 archetype). Re-enable the excluded bands and
update the expectations in `tests/test_renovisor_mapping.py` back to the exact bands
(`IE.N.SFH.05.*`, `IE.N.AB.10.*`).

## 2. Same division pattern for windows — latent crash

**Where:** `hisim/components/building.py`, `set_window_heat_transfer_parameter` (around
line 2820).

**What happens:** the window U-value is the area-weighted average
`(u1*a1 + u2*a2) / (a1 + a2)`. A row with `A_Window_1 == A_Window_2 == 0` crashes identically
to issue 1. No such row is currently selected by the translator, but the pattern is the same
and the data source is the same spreadsheet — worth guarding while touching issue 1.

**Suggested fix:** guard the division; `0.0` when the total window area is 0.

**Testing:** cover via a synthetic `buildingdata_ref` row (both areas zero) or fold into the
issue-1 test module.

## 3. `set_thermal_bridging_parameter` mutates shared reference data

**Where:** `hisim/components/building.py`, `set_thermal_bridging_parameter` (around line 2863).

**What happens:**

```python
if self.buildingdata_ref["delta_U_ThermalBridging"].values[0] == 0:
    self.buildingdata_ref["delta_U_ThermalBridging"] = 0.1
```

This writes the default back into `buildingdata_ref`, which is a slice of the loaded TABULA
dataframe. Depending on how the dataframe is cached/shared, this either mutates global state
that outlives the component (affecting later `BuildingInformation` instances in the same
process, e.g. multi-building districts or batch runs in one interpreter) or triggers pandas'
`SettingWithCopyWarning` undefined behavior. The intent is clearly a local default, not a data
edit.

**Suggested fix:** keep the default in a local variable instead of writing to the dataframe:

```python
delta_u_thermalbridging = float(self.buildingdata_ref["delta_U_ThermalBridging"].values[0]) or 0.1
```

**Testing:** construct two `BuildingInformation` instances for a code whose
`delta_U_ThermalBridging` is 0 and assert the underlying dataframe still contains 0 afterwards.

## 4. Hard-coded cluster paths in the building-sizer setups

**Where:** all `system_setups/household_*_building_sizer.py`, e.g.
`cache_dir_path_utsp = "/benchtop/2024-k-rieck-hisim/lpg-utsp-cache"` and
`cache_dir_path_simuparams = "/benchtop/2024-k-rieck-hisim/hisim_inputs_cache/"`.

**What happens:** on any machine that is not the FZJ cluster these paths silently do not exist
and caching is disabled — every run recomputes LPG profiles and weather inputs, which
dominates runtime for short simulations (the RenoVisor use case runs one simulation per
request, so cold caches hurt every single request).

**Suggested fix:** read the cache directories from environment variables (e.g.
`HISIM_UTSP_CACHE_DIR`, `HISIM_INPUTS_CACHE_DIR`) or from `SimulationParameters`, with the
cluster paths as documented defaults. One shared helper instead of a copy in each of the ~11
setups.

**Testing:** setup-level test that sets the env var to a temp dir and asserts the occupancy
config picks it up; run one of the fast marker tests twice and assert the second run hits the
cache.

## 5. Battery and heat-pump sizes cannot be specified

**Where:** `hisim/building_sizer_utils/interface_configs/system_config.py`
(`EnergySystemConfig`) and the building-sizer setups.

**What happens:** `EnergySystemConfig` only has `use_battery_and_ems: bool`; the battery is
always auto-sized. The heat pump is likewise always auto-sized from the building's heating
load. RenoVisor users pick concrete sizes (battery 5/10/15 kWh, heat pump 6/8/10/12 kW) — the
translator currently marks these as `approximated` and lets the setup size them.

**Suggested fix:** add optional `battery_capacity_in_kilowatt_hours: Optional[float] = None`
and `heat_pump_thermal_power_in_kilowatt: Optional[float] = None` to `EnergySystemConfig`
(`None` = keep auto-sizing, fully backward compatible). In the setups, pass an explicit value
through to `advanced_battery_bslib` / `more_advanced_heat_pump_hplib` configs when present.

**Testing:** extend the setup tests with a config carrying explicit sizes and assert the
component configs received them; keep one auto-sizing test as regression. Translator side:
`hisim/renovisor/mapping.py` can then map `battery.kWh` and the `heat_pump` measure's `kW`
as `used` instead of `approximated` (update `tests/test_renovisor_mapping.py`).

## 6. Heating setpoint is hard-coded; RenoVisor's `targetTempC` cannot be mapped

**Where:** building-sizer setups, e.g. `household_heatpump_building_sizer.py` lines ~122–124:
`building_set_heating_temperature_in_celsius = 20.0` (and 25.0 for cooling).

**What happens:** the user-chosen living-room setpoint (12–28 °C in the RenoVisor UI, default
21) is ignored; every simulation heats to 20 °C. Setpoint strongly affects heating demand
(rule of thumb ~6 %/K), so this is one of the biggest fidelity gaps for "your house today"
baselines.

**Suggested fix:** add `set_heating_temperature_in_celsius: Optional[float] = None` to
`ArcheTypeConfig`; setups use it when present, falling back to the current constants.

**Testing:** setup test asserting the building config receives the configured setpoint; an
integration assertion that a higher setpoint yields higher annual heating demand. Translator:
map `homeInputs.targetTempC` → new field, flip its report status to `used`.

## 7. Per-element U-value overrides exist in `BuildingConfig` but are unreachable

**Where:** `hisim/components/building.py` (`BuildingConfig` has
`window_u_value_in_watt_per_m2_per_kelvin`, `door_u_value_...` overrides) vs.
`ArcheTypeConfig`, which has no envelope fields, and the setups, which never set them.

**What happens:** RenoVisor sends detailed envelope data (per-element construction/insulation
ids and expert U-value overrides). The translator can only express "how renovated is it
overall" via the TABULA refurbishment variant (.001/.002/.003) — a very coarse three-step
scale. Insulation measures (e.g. "20 cm roof insulation") only bump that variant.

**Suggested fix (incremental):**
1. Add optional per-element U-value overrides (roof, wall, floor, window, door) to
   `ArcheTypeConfig` and pass them into `BuildingConfig` in the setups — the building component
   already supports window/door overrides; extend it to roof/wall/floor.
2. Later: a derivation table (construction + insulation + added mm of material → U-value) so
   measures can be translated into concrete post-retrofit U-values.

**Testing:** building-component test comparing heating demand with/without an override;
setup test that the override reaches `BuildingConfig`. Translator: map `*.uValueWPerM2K` and
insulation-measure params to the new fields.

## 8. Ventilation and airtightness are not configurable

**Where:** `hisim/components/building.py` (infiltration/ventilation handling; the setups only
set `enable_opening_windows = True`).

**What happens:** RenoVisor's `air_sealing` measure (target n50 air-change rate) and
`ventilation` measure (MVHR with 75–92 % heat recovery) have no counterpart. Sealing + MVHR is
the standard deep-retrofit combination, so packages containing it are currently
under-represented (only via the TABULA variant bump).

**Suggested fix:** expose an infiltration rate (n50 or air changes per hour) on
`BuildingConfig`/`ArcheTypeConfig`, and add a mechanical-ventilation-with-heat-recovery option
(effective ventilation heat loss reduced by the recovery efficiency).

**Testing:** component-level: heating demand decreases monotonically with lower n50 and with
higher heat-recovery efficiency; energy balance stays closed (`i_doublecheck`).

## 9. Boiler seasonal efficiency and flow temperature are not configurable

**Where:** gas/oil/pellet/wood setups (fixed component defaults);
`heat_distribution_system.py` flow-temperature handling.

**What happens:** RenoVisor's expert fields `heating.seasonalEfficiencyPct` (e.g. an old oil
boiler at 78 %) and `heating.flowTempC` are dropped. Baseline fuel consumption for old boilers
is therefore optimistic, which understates the savings of switching away from them.

**Suggested fix:** optional efficiency override in `EnergySystemConfig` wired to the
boiler component configs; optional design flow temperature wired to the heat-distribution
controller (also improves heat-pump COP realism, issue 5).

**Testing:** setup test that a lower efficiency raises annual fuel energy proportionally.

## 10. Solar thermal only exists paired with gas or heat pump, and is not sizeable

**Where:** setup selection matrix (`household_gas_solar_thermal_building_sizer.py`,
`household_heatpump_solar_thermal_building_sizer.py` are the only solar-thermal setups).

**What happens:** an oil- or electric-heated house with (or adding) solar thermal cannot be
simulated with solar thermal — the translator drops it with a report note. Collector area and
storage volume are also fixed by the setup rather than configurable
(`solarThermal.collectorAreaM2`, `storageLiters` are ignored).

**Suggested fix:** either add solar-thermal variants for the remaining heating systems or —
cleaner long-term — make solar thermal an optional flag in `EnergySystemConfig` handled inside
each setup. Add optional collector area / storage volume fields.

**Testing:** marker test per new combination; assert DHW energy from the boiler decreases when
solar thermal is enabled.

## 11. Electric vehicles / home charging are only available in one special setup

**Where:** `household_heatpump_car_building_sizer.py` is the only building-sizer setup with a
car; none of the others model vehicles.

**What happens:** RenoVisor models all vehicles (fuel cost/CO₂ of the status quo, EV charging
load interacting with PV/battery/tariff). The translator ignores the whole `vehicles` array.
An EV can shift several MWh/a onto the household meter, so PV/battery sizing conclusions are
skewed for EV owners.

**Suggested fix:** add an optional `electric_vehicles` list (annual km, kWh/km) to
`EnergySystemConfig` and instantiate the existing car/car-battery components in the setups when
present, following the pattern of `household_heatpump_car_building_sizer.py`.

**Testing:** setup test asserting total electricity consumption grows by ~`km * kWh/km`;
check EMS still converges (marker test).

## 12. East/west PV cannot be represented

**Where:** `ArcheTypeConfig` has a single `pv_azimuth`; the setups build one PV array.

**What happens:** RenoVisor offers `east_west` roof orientation, common for terraced houses.
The translator approximates it as a single east-facing array (azimuth 90°), which
underestimates evening generation and distorts self-consumption.

**Suggested fix:** allow a list of (azimuth, share) tuples in `ArcheTypeConfig` — or a simple
`pv_split_east_west: bool` — and build two `PVSystem` instances with half the capacity each.

**Testing:** compare annual generation profiles: the east/west split should flatten the midday
peak vs. a south array of equal kWp.

## 13. Weather resolution vs. simulation year is implicit

**Where:** `hisim/components/weather.py` `LocationEnum` (NSRDB 2019 files for IE/GB/…,
DWD/TRY for German locations); setups default to `year=2021`.

**What happens:** the weather file's year and the `SimulationParameters` year are independent;
simulating year X with weather of year Y silently misaligns weekdays and (for leap years) day
counts. The RenoVisor translator hard-codes 2019 for this reason, but nothing in HiSim checks
or documents the constraint.

**Suggested fix:** `Weather.i_prepare_simulation` should log a warning (or raise, behind a
flag) when the requested simulation year does not match the data year of the selected weather
file; expose the data year on `LocationEnum` entries instead of parsing it from filenames.

**Testing:** unit test that a mismatched year triggers the warning; no behavior change
otherwise.

## 14. `weather_try_region` and other German-only defaults in `ArcheTypeConfig`

**Where:** `hisim/building_sizer_utils/interface_configs/archetype_config.py` (defaults:
`weather_try_region: int = 6`, Aachen coordinates, German postal code, DE building code).

**What happens:** non-German archetypes inherit German defaults for every field the caller
does not set; nothing warns about the mismatch (e.g. a TRY region has no meaning for Dublin).
The RenoVisor translator overrides what it can, but any new field added with a German default
silently degrades non-DE runs.

**Suggested fix:** make the country-specific fields `Optional[...] = None` and derive them
from `weather_location`/`building_code` when unset; log which defaults were applied.

**Testing:** construct an IE archetype without optional fields and assert no German TRY region
is used downstream.

## 15. DHW storage temperature runs away at hourly resolution — **observed crash**

**Where:** DHW storage / DHW heat source control loop used by the building-sizer setups
(`simple_water_storage.py` and the DHW heat-source controller).

**What happens:** running `household_gas_building_sizer.py` (via the RenoVisor translator,
config `IE.N.SFH.04.Gen.ReEx.001.001`, full year 2019) at `seconds_per_timestep=3600` aborts
with:

```
The water temperature in the DHW water storage is with 98.32°C way too high or too low.
```

The same configuration proceeds normally at the default 900 s. This points to the DHW charging
control overshooting when one timestep spans a whole hour (a full hour of heating power is
committed before the controller sees the new temperature) — i.e. the components are stable at
15 min but not at 1 h, and nothing validates the timestep range a setup supports.

**Why it matters:** coarse resolutions are the natural lever for fast batch/screening runs
(e.g. RenoVisor previews, building-sizer iterations). Currently they fail late — after
component setup and possibly hours into a batch — rather than being either supported or
rejected upfront.

**Suggested fix (either or both):**
1. Make the DHW storage/heat-source controller timestep-aware: scale the hysteresis band or
   limit charging power so one timestep cannot overshoot the target band
   (`P_max ≈ storage_capacity * ΔT_band / dt`).
2. Have setups declare a supported timestep range and fail fast in
   `SimulationParameters` validation with a clear message instead of mid-simulation.

**Testing:** regression test running the gas building-sizer setup for a winter week at 900 s,
1800 s and 3600 s asserting the DHW temperature stays within bounds; if option 2 is chosen,
assert the clear early error instead.

---

### Suggested order

| Priority | Items | Rationale |
|---|---|---|
| 1 (bugfix) | 1, 2, 3, 15 | Crashes/state corruption; item 1 currently forces the translator to substitute wrong age bands for common Irish houses. |
| 2 (quick wins) | 6, 5, 9 | Small config extensions with large fidelity gains for baselines and retrofit comparisons. |
| 3 (medium) | 4, 12, 13, 14 | Robustness/portability; no contract changes. |
| 4 (larger) | 7, 8, 10, 11 | New physics/config surface; each removes a whole `ignored` block from the translator's mapping report. |
