# Gap analysis: verification suite for the HiSim building model (step 1)

**Spec:** `roadmap/hisim_verification_agent_spec.md`, §5 (step 1 done against revision 3; the
decisions below are carried into revision 4).
**Code read at:** commit `88c40a22` (branch `verification_spec`), 2026-11-02.
**Checks run with:** conda env `hisim_env`, Python 3.11.15, numpy 1.26.4, pandas 2.1.4, pvlib 0.15.1
(`requirements.txt` pins pandas 3.0.6; the checks below do not depend on pandas).
**Status:** step 1 reviewed. The human's decisions of 2026-11-02 are recorded in §4 and under each
finding; the spec corrections of §5 are applied (spec revision 4). Nothing in the model or the
components was changed. §7 re-checks §1 to §3 against `origin/main` of 2026-11-03 (commit
`d3a1ddc3`, merged as `389ac8c0`): F1 is fixed on `main` since PR #1079, the ideal heater has a
controller, and the test markers follow a new rule (decisions D14, D15 open).

The four things the spec asked to be flagged come first (§1). §2 is the §5 table of the spec, confirmed
or corrected row by row with the proposed change. §3 lists every change to a public interface, §4 the
decisions taken, §5 the corrections applied to the spec, §6 the checks and how to
repeat them.

## 1. Findings that need a decision before step 2

### F1. The thermal-mass state is the mid-step average, not θ_m,t (rule 3a, human only)

`Building.i_simulate` advances the mass node with

```python
self.state.thermal_mass_temperature_in_celsius = thermal_mass_average_bulk_temperature_in_celsius
```

that is with θ_m = (θ_m,t−1 + θ_m,t)/2 of ISO 13790:2008, Annex C, eq. C.9 [verify]. Annex C uses
that average only for the surface and air temperatures of the current step (C.10, C.11) and carries the
end-of-step value θ_m,t (C.4) into the next step. `calc_crank_nicolson` computes θ_m,t and returns it
(`next_thermal_mass_temperature_in_celsius`), but the state takes the average. RC_BuildingSimulator,
the cited source, carries `t_m_next` in its loop. The line has read this way since the first version of
the component (commit `166b6f3d`, 2021-10-05); the 2024 refactor only reformatted it.

Consequence, derived and then measured (§6.1): the stored state moves by half the Crank–Nicolson
increment per step, so for Δt → 0 the scheme converges to the 5R1C network with **2·C_m**. The steady
state is unchanged; every dynamic quantity (free-float temperatures, peak loads, the response to a
setback, the time-step dependence) is that of a building with twice the thermal capacity. Free decay
of the mass node of the default building from 20 °C against 0 °C, computed with the Building's own
`calc_crank_nicolson` (C_m = 2.0·10⁷ J/K, τ = C_m/(H_tr_3 + H_tr_em) = 24.99 h):

| state rule | Δt [s] | 1 h | 3 h | 6 h | 12 h | 24 h |
|---|---:|---:|---:|---:|---:|---:|
| analytic, C_m | – | 19.2156 | 17.7378 | 15.7314 | 12.3739 | 7.6556 |
| analytic, 2·C_m | – | 19.6039 | 18.8349 | 17.7378 | 15.7314 | 12.3739 |
| Annex C (θ_m,t) | 3600 | 19.2155 | 17.7375 | 15.7309 | 12.3731 | 7.6546 |
| Annex C (θ_m,t) | 900 | 19.2155 | 17.7377 | 15.7314 | 12.3738 | 7.6556 |
| Annex C (θ_m,t) | 60 | 19.2156 | 17.7378 | 15.7314 | 12.3739 | 7.6556 |
| HiSim (average) | 3600 | 19.6077 | 18.8461 | 17.7588 | 15.7687 | 12.4327 |
| HiSim (average) | 900 | 19.6048 | 18.8378 | 17.7431 | 15.7408 | 12.3887 |
| HiSim (average) | 60 | 19.6039 | 18.8351 | 17.7381 | 15.7320 | 12.3749 |

Two readings of the table:

- **Time-step consistency of the scheme itself:** with the Annex C rule the Crank–Nicolson step is
  consistent; the 3600 s result differs from the exact solution by < 0.001 K after 24 h. With the
  current rule the three time steps also agree with each other (12.43 / 12.39 / 12.37 K), but with
  the wrong continuous model. So the "C_m / seconds_per_timestep" of spec §3.1 is correct, and the
  sign of inconsistency the spec asked to look for is not in the time stepping but in the state.
- **Effect on the verification:** Case 600 has C_m ≈ 1.9 MJ/K and a mass time constant of a few
  hours; Case 900 about 13 MJ/K. With the current rule HiSim would be verified as a building with
  3.9 and 27 MJ/K. The annual heating and cooling sums react weakly (same steady state), the
  free-float cases (600FF, 900FF, 1 °C bins), the peak loads and the ISO Table 33/34 hourly profiles
  react strongly, and the 600 → 900 differential ranges of Annex A3 measure exactly the quantity that
  is doubled.

Algebraically the current rule is the backward-Euler step of a 2·C_m network: eliminating θ_m,t from
the two lines gives s_t = (2·C_m/Δt · s_t−1 + Φ_mtot) / (2·C_m/Δt + H_tr_3 + H_tr_em). The 3600 s row
confirms it (24 h: 12.433 measured, 12.434 backward Euler with 2·C_m, 12.374 exact 2·C_m). The
equivalence to 2·C_m is exact for Δt → 0 and first-order in Δt otherwise.

This is a change to the 5R1C scheme and therefore forbidden for the agent (rule 3a). Options for the
human: (a) correct the state update to θ_m,t (one line; every golden reference that contains a
building moves and has to be re-blessed through the golden-update workflow; the output
`TemperatureMeanThermalMass` keeps reporting the average) before step 3, or (b) keep it and record it
as a `model` entry of the deviation register ("state carries the mid-step average; dynamics equivalent
to 2·C_m"). Recommendation: (a), because every C_m-related comparison of both suites is otherwise a
comparison of a different model than the one documented. Bead: see §4.

**Decision (human, 2026-11-02): (b) for now, (a) deferred.** No code change; the first full
verification run shows HiSim as it is. Register entry in every suite, category `model`:
"`Building.i_simulate` stores the mid-step average θ_m = (θ_m,t−1 + θ_m,t)/2 as the mass-node state
instead of θ_m,t (ISO 13790:2008, Annex C, equations (C.4) and (C.9) [verify]); dynamics equivalent
to the 5R1C network with 2·C_m (exactly: a backward-Euler step of a 2·C_m network, converging to it
for Δt → 0); steady state unchanged". The input mapping derives C_m strictly per §3.2 and Appendix B;
no compensation (no C_m/2 or similar) anywhere in the verification code. The report places this
entry next to the results it affects most (free float, peak loads, hourly profiles, 9xx − 6xx
differential ranges). Bead hisim-x6c7.1 stays open as `owner-decision` for (a) after the first
full report; it no longer blocks step 2.

### F2. The thermostat is the Building's, not the heater's; spec §3.2.10 option (a) has to move

`IdealizedElectricHeater.i_simulate` copies its input `TheoreticalThermalBuildingDemand` to its output
`ThermalPowerDelivered`; its two configuration fields
`set_heating_temperature_for_building_in_celsius` / `set_cooling_…` are read by nothing. The
thermostat is `Building.calc_theoretical_thermal_building_demand_for_building`, the two-step
procedure of ISO 13790 C.4 (0 and 10 W/m², linear interpolation, eq. C.13 [verify]) on the **air
temperature** with the setpoints `BuildingConfig.set_heating_temperature_in_celsius` /
`set_cooling_temperature_in_celsius` (20/25 by default; also sizing facts). Within a time step the
simulator iterates building and heater to a fixed point; because the network is linear in Φ_HC, the
converged delivered power is the exact power that brings the air node to the setpoint, consistent
with the mass temperature of that step. Consequences for the spec:

- Setpoints 20/27 and 20/20 (685, 695, 985, 995): configuration of the Building, no code change;
  with equal setpoints the C.4 branch `set_heating <= θ_air,0 <= set_cooling` still works.
- Setback 640/940 (10 °C from 23:00 to 07:00, heating only): the existing input
  `Building.BuildingTemperatureModifier` adds an offset to the heating setpoint only, and the
  existing `NightSetbackController` (`hisim/components/night_setback_controller.py`) emits exactly
  such an offset for a window of whole hours (`setback_delta_in_kelvin=-10`, 23 → 7). No code change.
- Unlimited capacity: given; the heater has no limit. A power limit (FR4) is needed by no phase-1
  case; proposal: drop it from FR4 for phase 1.
- Control on the operative temperature (ISO suite): can only be added in the Building's C.4 step,
  interpolating on θ_op = 0.3·θ_air + 0.7·θ_s instead of θ_air (both temperatures are already
  computed for the 0 and 10 W/m² probes). Implementation options: (1) `BuildingConfig` gets
  `thermostat_control_variable: str = "air"` (new optional field; default keeps every result;
  committed JSON schema regenerated; recorded twins unchanged, they record only deviations from the
  preset); (2) a `Building` subclass in `hisim/verification/` overriding the C.4 step; (3) air control
  with a register entry. Recommendation was (1); decision below.
- FR4 and §11 step 2 should no longer name the heater; the heater stays as it is. The dead setpoint
  fields of `IdealizedHeaterConfig` are a separate cleanup (bead, §4), not part of this work.

**Decision (human, 2026-11-02): no change to the Building, the heater or any physics.** Setpoints
20/27 and 20/20 as Building configuration. Setback 640/940 through `NightSetbackController`
(`setback_delta_in_kelvin = -10`, `night_start_time_in_hours = 23`, `night_end_time_in_hours = 7`)
and `BuildingTemperatureModifier`: the window is 23:00 ≤ t < 07:00 and the modifier reaches the
heating setpoint only; the first report shows the recorded controller output and the night-time
air temperature of case 640 as evidence that the setpoint drops to 10 °C. Unlimited capacity
accepted (`max_thermal_building_demand_in_watt` serves KPIs only, it does not cap the demand).
Operative-temperature control: option (3); options (1) and (2) rejected. Register entry `model`:
"Thermostat acts on θ_air (ISO 13790, clause C.4 procedure); EN ISO 52016-1 controls on the
operative temperature (§3.2.10)". FR4 and §11 step 2 no longer name the heater. The dead setpoint
fields of `IdealizedHeaterConfig` are bead hisim-component-physics-4g9.70, not part of this work. Rule for the rest of
the work: a case is `not_modelled` when its defining feature is missing in HiSim (shading in 610,
night ventilation in 650); a mechanism missing for all cases alike is a register entry and the
cases run. No model or control code is added to HiSim to make a case runnable; the input-side
infrastructure of §3 is in scope.

### F3. §3.2.8: F_w is a constant on the total plane irradiance; no angular model exists

`Window.calc_solar_heat_gains` (`hisim/components/building/window.py`) computes
`pvlib.irradiance.get_total_irradiance(tilt, azimuth, zenith, sun azimuth, DNI, GHI, DHI, DNI_extra,
albedo=0.2)` with pvlib's default isotropic sky model, i.e. beam + isotropic sky diffuse + ground
reflection with albedo 0.2 (the EN ISO 52010-1 default, stated in the class), and multiplies the
plane irradiance by `g_gl_n · F_w · F_sh_vert · (1 − F_f) · A`. Every factor is a constant per window;
there is no incidence-angle dependence of the transmittance. `calc_direct_solar_factor` and
`calc_diffuse_solar_factor` in the same class are not called by anything (dead code, no action
proposed here). Therefore:

- The irradiance-weighted annual F_w of spec §3.2.8 is the right quantity for HiSim: it is applied as
  the constant the model has. `WindowGeometry` carries `nonperpendicular_reduction_factor` per
  window, so a per-orientation F_w needs no change to the optics.
- Register entries: `input_mapping` "angular dependence of the SHGC represented by one irradiance-
  weighted constant per orientation (Table 7-12); permitted by 7.2.1.11.3 a) [verify]"; `model` "sky
  diffuse isotropic (pvlib default)". Ground reflectance 0.2 is not a deviation: Standard 140
  specifies it (7.2.1.1.2.5 [verify]).
- ISO suite: the bypass of §7.4 is not possible without new code. The Building has no input for
  plane irradiance or solar gains; the gain series is produced before the run from the weather
  series (GHI, DNI, DHI, sun position) for every window, and the ISO file carries plane irradiance
  only, no DNI/DHI for the optics. `HeatingByResidents` and `HeatingByDevices` are no substitute:
  they enter as internal gains (half to the air node, C.1), solar gains enter the surface and mass
  nodes only (C.2, C.3). Decision: D9.
- Window azimuth convention: south 180°, east 90°, west 270°, tilt 90° (pvlib convention); matches
  the case descriptions.

**Decision (human, 2026-11-02).** ASHRAE suite: F_w = irradiance-weighted annual mean of SHGC(θ)/SHGC_n
from Table 7-12 per orientation, by the method named in §3.2.8 (beam from the angular table, diffuse
from the hemispherical value), computed once in the verification code before any result is looked
at, written to `parameters/<case>.json` and entered through the per-window
`nonperpendicular_reduction_factor` of the explicit window list (D6); the hemispherical ratio
0.670/0.769 = 0.871 is reported beside it. No change to `Window`. The per-window factor exists, but
the TABULA pipeline fills it with one scalar for all windows (`information.py`, column `F_w`), so
per-orientation values need the explicit description. Register entries as above. ISO suite:
deferred (D9). Dead code: no action.

### F4. §3.2.9: opaque solar absorption and sky radiation are not implemented

Solar gains enter the model only as `solar_heat_gain_through_windows` (the produced window series),
split onto the surface and mass nodes by C.1 to C.3. H_tr_em sees the outdoor air temperature; there is
no absorbed-solar term on opaque elements (ISO 13790 11.3.4 [verify]) and no sky-radiation term
(11.3.5 [verify]). α = 0.6 is therefore not used; register entry of category `model` in both suites.
Not added (rule 3a).

**Decision (human, 2026-11-02): accepted as stated.** No opaque solar absorption and no sky
radiation are added. Register entry, category `model`, every suite: "no absorbed solar radiation on
opaque elements (ISO 13790 11.3.4 [verify]) and no sky radiation (11.3.5 [verify]); H_tr_em sees the
outdoor air temperature; α = 0.6 not used". No workaround through the inputs: no sol-air temperature
or similar in place of the outdoor air temperature.

## 2. Capability table (spec §5, confirmed and corrected)

| # | Capability | Where in code (confirmed) | Status (confirmed/corrected) | Proposed change |
|---|---|---|---|---|
| 1 | Explicit building parametrisation without TABULA | `BuildingConfig.building_code: str` (mandatory); `BuildingInformation.__init__` pipeline `get_building_from_tabula → get_building_area_parameters → get_building_heat_transfer_parameters → get_constants → get_max_thermal_building_demand → set_reference_data_from_tabula`; the row is read in every step (A_C_Ref, A_*, U_Actual_*, b_Transmission_*, A_Window_<dir>, F_w, F_f, F_sh_vert, g_gl_n, n_air_use, n_air_infiltration, h_room, delta_U_ThermalBridging, n_Apartment, A_Estim_Door, q_*, c_m, h_Ventilation, h_Transmission, eta_h_gn) | **partial, confirmed.** Overridable from the config: the five element areas and U-values, floor area, apartments, max demand. Everything else comes from the row. Two traps for an explicit building: a TABULA `delta_U_ThermalBridging` of 0 is replaced by 0.1 W/(m²K) (`thermal_bridging_surcharge…`), and a configured floor U-value gets the undocumented adjustment factor 0.5 (`FLOOR_ADJUSTMENT_FACTOR_FOR_CONFIGURED_U_VALUE`); neither may reach the BESTEST cell | **Decided (D6, option C).** `BuildingConfig.building_code` becomes `Optional[str]` and a new optional field `explicit_description: Optional[ExplicitBuildingDescription]` (in the config style of `BuildingConfig`: floor area, volume, opaque elements with area, U, κ, windows as in row 3, infiltration ACH, thermal-bridge conductance, apartments) is added; exactly one of the two must be set. `BuildingInformation.__init__` dispatches to a second pipeline that sets the same attributes (areas, conductances with adjustment factor 1 and the given thermal-bridge conductance, constants of row 2, window data of row 3, apartments, max demand from the conductances; the `tabula_ref_*` attributes `None` and the TABULA KPI omitted; `building_heat_capacity_class` unused). `element_values()` (read by RenoVisor) filled from the explicit values. Public interface, see §3 |
| 2 | EN ISO 13790 parameter derivation (§3.2.3 to 3.2.5) | `BuildingInformation.get_constants`: C_m = f_c(class)·A_f, A_m = f_a(class)·A_f, A_tot = 4.5·A_f; coupling 3.45·A_tot and 9.1·A_m in `Building.get_conductances`; H_tr_em = 1/(1/H_tr_op − 1/H_tr_ms) in `get_thermal_conductance_of_opaque_surfaces…` | **missing, confirmed** (class table and fixed ratios) | **Decided (D6).** Pure functions in a new module `hisim/components/building/explicit.py`: C_m = Σ κ_j A_j, A_m = C_m²/Σ A_j κ_j², A_tot = Σ A_j from κ and areas given per element; the layer-to-κ rule of §3.2.3 (ISO 13786) is applied in the suite's system builder, step 3 (decided 2026-11-02); unit test against Appendix B after the human has verified it. The explicit pipeline of row 1 stores the results in the attributes the Building reads; the coupling conductances and H_tr_em need no change |
| 3 | Window orientation, area, U, g, frame fraction | `Building.get_windows` builds one `WindowGeometry` per TABULA direction (S, E, N, W, horizontal) with the row's single g_gl_n, F_w, F_f, F_sh_vert; H_tr_w = U_window·A_window + U_door·A_door from one area-weighted window U | **partial, confirmed** | `ExplicitBuildingDescription.windows`: list of (azimuth, tilt, area, U, g_n, F_f, F_w, F_sh_vert = 1); `get_windows` takes the list when present; H_tr_w = Σ U_w A_w, door area 0 |
| 4 | Window optics: incidence angle, diffuse and ground-reflected share, F_w | `Window.calc_solar_heat_gains` | **read; see F3**: constant factors on the total plane irradiance, isotropic sky, albedo 0.2 | No code change; F_w per orientation computed by the suite (F3); register entries of F3 |
| 5 | Irradiance per orientation as direct input, bypassing the optics (§7.4) | `solar_gains.produce_solar_gains` takes the six weather series; `Building.fetch_solar_heat_gains_through_windows` requires `Weather.SERIES_ARTIFACT_KEY` and the six published series | **missing, confirmed** | **Not built: the ISO suite is deferred (D9); no new Building input (F3).** For a later phase: a new optional input `Building.ExternalSolarGainThroughWindows` (W, `mandatory=False`); when connected, the Building uses it as the gain series and skips the producer and the weather-key requirement. The ISO climate source (row 10) computes Σ_w I_sol(orientation_w)·A_w·g_n·F_w·(1 − F_f) from the case's windows and feeds it. The node split (row 13) is unchanged, so the bypass touches only the optics, as §7.4 intends. Alternative: a plane-irradiance mode in the producer DTO (changes cache-key material and the cached CSV schema); not recommended |
| 6 | Opaque solar absorption and sky radiation (§3.2.9) | `Building.i_simulate`, `calc_internal_heat_flows_from_internal_gains_and_solar_gains` | **missing, confirmed; see F4** | None (rule 3a); register entry `model` |
| 7 | Ideal heater with setpoints, schedule, power limit, control variable | `IdealizedElectricHeater` (pass-through); thermostat in `Building.calc_theoretical_thermal_building_demand_for_building`; schedule input `Building.BuildingTemperatureModifier` + `NightSetbackController` | **corrected; see F2**: setpoints, 20/20 and the 640/940 setback exist (Building + night-setback component); capacity unlimited; the heater's setpoint fields are dead; control variable missing | **Decided (D2, D7).** Heater unchanged. Control variable: option (3) of F2, air control with a register entry, no Building option. FR4 power limit dropped for phase 1. The suite wires heater ↔ building explicitly (both directions) |
| 8 | Free-floating run | `Building.i_simulate`: `thermal_power_delivered_channel` is optional; `SingleTimeStepValues.get_input_value` returns 0 for an unconnected input; the demand outputs are still computed and ignored; `enable_opening_windows=False` by default (must stay so) | **exists, confirmed** | A suite test asserts delivered power 0 and no heater in the FF systems; `tests/test_building_one_day_snapshot.py` covers free-float phases with a delivered power of 0 W, not the unconnected input |
| 9 | Weather from an EPW file | `weather/calculation.py`: `WeatherDataSourceEnum` {DWD_TRY, NSRDB, NSRDB_15MIN, DWD_10MIN, ERA5, DWD_15MIN}; `WeatherSourceFiles.SUFFIXES`, `YEAR_COLUMNS`; readers index hourly data at hh:30 with `tz="Europe/Berlin"`; `interpolate()` pads the year with a hard-coded Europe/Berlin timestamp and the value 0.0 at the year start, the last value at the year end; `get_coordinates` per source; `weather/config.py`: `LocationEnum`, `WeatherConfig.for_location` requires a `heating_reference_temperature_in_celsius` | **missing, confirmed**; three details matter: (i) the hard-coded Europe/Berlin in `interpolate` and in the readers (Denver is UTC−7, standard time, no DST); (ii) `interpolate` pads every column with 0.0 before the first hour, so 00:00 to 00:30 of 1 January is interpolated towards 0 (also for the temperature); (iii) pressure is kept in hPa (output ×100), EPW gives Pa | `WeatherDataSourceEnum.EPW`; `read_epw_data` (dry-bulb, pressure → hPa, GHI, DNI, DHI, wind speed; index hh:30 like the other hourly readers in the fixed-offset zone of the EPW header, UTC−7 → `Etc/GMT+7`); `interpolate(series, year, tz)` with the zone as a parameter (every cached weather series moves to a new key once, which is the intended behaviour of the producer fingerprint); `SUFFIXES[EPW] = ("",)`, `YEAR_COLUMNS[EPW] = None` (the eight header records are followed by data rows without a column header, and a TMY3 belongs to no calendar year, like a DWD test reference year); `get_coordinates` EPW branch; `LocationEnum.DENVER_INTL_AP = ("Denver Intl AP", "EPW", "Denver_Intl_AP", "725650TYCST.epw", EPW)`. The suite states a design temperature for the sizing fact only (register entry: no physical effect). Register entries: timestamp convention hh:30 and linear interpolation; the 0.0 padding of the first half hour |
| 10 | Weather from the ISO climate spreadsheet (§7.4) | – | **missing, confirmed.** `.xls` needs `xlrd`, which is not installed; `openpyxl` reads only `.xlsx` | **Deferred with the ISO suite (D9).** For a later phase: a data-source component `hisim/verification/iso_climate.py` (not a weather reader: the frame schema `PRODUCED_COLUMNS` is fixed and cached) that outputs `TemperatureOutside` and the external gain series of row 5 from `theta_e;air` and the per-orientation `I_sol`, reading a CSV converted once from the spreadsheet in the private folder (D5: no `xlrd`; conversion documented, hash recorded); the initialisation month cannot be used (row 17) |
| 11 | Sub-hourly weather interpolation | `resample_to_one_minute` (linear; hourly sources through `interpolate` with padding), `aggregate_to_timestep` (mean) | **exists, confirmed**; documented in row 9 | Register entry; no change |
| 12 | Constant internal gains with a fixed value | `Building` inputs `HeatingByResidents`, `HeatingByDevices` (W, optional, warning when unconnected) | **partial, corrected**: `CSVLoader` (`hisim/components/csvloader.py`) serves as the constant source; it takes a file under an `inputs_dir` or a `dataframe` argument, requires at least `timesteps` finite rows, and its unit and multiplier are configurable | **No new component (decided 2026-11-02).** The suite feeds a constant 200 W series through `CSVLoader` (frame built in the run, or a CSV written under the run's cache directory) into `HeatingByDevices`; `HeatingByResidents` stays unconnected (logged warning) |
| 13 | Distribution of gains to the 5R1C nodes | `calc_internal_heat_flows_from_internal_gains_and_solar_gains`: Φ_ia = 0.5·Φ_int; Φ_st = (1 − A_m/A_tot − H_tr_w/(9.1·A_tot))·(0.5·Φ_int + Φ_sol); Φ_m = A_m/A_tot·(0.5·Φ_int + Φ_sol); delivered heating/cooling power enters the air node (C.5, C.10, C.11) | **exists, confirmed**; not configurable | Register entries `model` in both suites (§3.2.7). Note for the report: the delivered power entering the air node matches an all-convective ideal system, which is what ASHRAE 140 specifies [verify] |
| 14 | Infiltration as constant ACH with explicit volume | `_ventilation_conductance_in_watt_per_kelvin`: 0.34 Wh/(m³K)·(n_use + n_inf)·h_room·A_f | **partial, confirmed** | Explicit pipeline: 0.34·n·V with the scaled n of §3.2.6 and V from the description; both written to the parameters output |
| 15 | Operative temperature output | `Building` outputs `TemperatureIndoorAir` **and** `TemperatureInternalSurface` (set every step from the C.10 surface temperature) | **corrected: the surface temperature is an output.** θ_op = 0.3·θ_air + 0.7·θ_s is computable from the two outputs | Compute θ_op in the runner from the two outputs (no interface change; decided with D2) |
| 16 | Time steps 3600/900/60 s: supported? consistent? | `calc_next_thermal_mass_temperature_in_celsius`: C_m / seconds_per_timestep; weather and gains per time step | **supported, confirmed; consistency measured, see F1**: the scheme is consistent, the state rule is not Annex C | Decision F1 |
| 17 | Warm-up period | `SimulationParameters` (start/end date, timesteps); every reader lays exactly one calendar year by position; mid-year windows "are January in disguise" (`roadmap/midyear_start_epic.md`); no pre-run in `Simulator` | **not supported, confirmed**: neither a 13-month run nor a December start is possible | **Decided (D3): two-pass run** per (case, time step): pass 1 the full year from `initial_internal_temperature_in_celsius`, pass 2 the full year from the mass temperature at the end of pass 1 (periodic steady state; cost: twice the run time). Warm-up method and the carried state go to the parameters output; register entry `input_mapping`. The ISO initialisation month, when that suite returns, cannot be used for the same reason |
| 18 | Result directory and write guard for `BESTEST_output/` | `CalculationScope.open(label, run_directory=…)` (used so in `tests/test_dhw_chain_twins.py`), `ResultPathProviderSingleton`, `hisim/write_guard.py` | **exists, confirmed.** On Windows the guard refuses `open of '\\.\nul'` that `numpy.testing` performs when `pvlib` is imported inside the scope (every `hisim_main` run with a building fails on this machine); `HISIM_WRITE_GUARD=collect` lets the run proceed and lists it | Exporter writes `BESTEST_output/` under `ResultPathProviderSingleton.get_result_directory_name()` inside the scope. **Decided (D8):** the runner sets `HISIM_WRITE_GUARD=collect`; the Windows refusal stays bead hisim-repo-cleanup-o6a.36 and is not fixed in this work |
| 19 | Golden-reference integration (FR17) | `golden_references/*.json` per (setup, parameter set), keyed by KPI address; `scripts/golden_check.py`, `golden_config.json`; tolerance rel 1e-9 | **exists for setup KPIs, confirmed**; verification results are not KPIs of a setup | Own files `golden_references/bestest/<suite>__<case>__<timestep>.json` with HiSim values only, compared in `test_verification_suites.py` with the same numeric rule; blessing by the human through a `--bless` option of the CLI; the existing scripts stay untouched |
| 20 | Run time of an annual simulation at 60 s | – | **measured**, §6.2 | Two passes (row 17) double it; all 24 (case, Δt) pairs of the matrix are independent and can run in parallel |
| 21 | Test framework, markers, CI | `pytest.ini` (`--strict-markers`; markers base, extendedbase, buildingtest, …); `tests/conftest.py` fails a module that leaves the working tree dirty; `.github/workflows/` (tests, golden-check, golden-year, energy-system-freshness, quality, …) | **exists, confirmed** | Register marker `verification` in `pytest.ini`; suite tests write only under result directories (stray-file guard); `.env` is loaded by `hisim_main` and the CLI but not by pytest, so the suite's `conftest.py` calls `load_dotenv()` for `HISIM_VERIFICATION_DATA`; GitHub workflows untouched (§9.3) |

Further facts the implementation relies on:

- **Convergence loop** (`Simulator.process_one_timestep`): a step converges when every output moved
  by at most 10⁻⁴ (absolute) against the previous pass; after more than 10 passes the step is
  frozen (`force_convergence`), after 100 it raises. Building ↔ ideal heater is a linear fixed-point
  iteration and converges in a few passes; the runner records the average passes and the number of
  frozen steps per run (progress log "Avg. iterations") and the report prints them, because a frozen
  step delivers a power that is not the demand. Known defects of the loop for other components:
  `roadmap/convergence_order_findings.md`, beads hisim-component-physics-4g9.23 and hisim-component-physics-4g9.28; none involves the
  three components of the verification systems.
- **Case files and schema (FR1):** the repository's validation approach is pydantic v2 (`hisim_main`
  uses `TypeAdapter`, `hisim/energy_system/model.py`) with a committed JSON schema whose freshness a
  test pins; the case schema follows it.
- **Results access for the runner:** `Simulator.results_data_frame` holds every output per time step
  after `run_all_timesteps`; `PostProcessingOptions.EXPORT_TO_CSV` writes one CSV per output into the
  result directory. The runner reads the frame in process.

## 3. Changes to public interfaces (approved 2026-11-02 with D6, option C; the building rows are revised by D16, §8)

| Interface | Change | Who else is affected |
|---|---|---|
| `BuildingConfig` | `building_code: str` → `Optional[str]`; new field `explicit_description` (row 1) | committed JSON schema `hisim/energy_system_v3.schema.json` regenerated (`tests/test_energy_system_schema.py`); recorded twins unchanged (they record deviations from presets only); RenoVisor reads `building_code` of its own request, not this field |
| `Building` | **no change**: the external gain input of row 5 is not built (D9), θ_op is computed in the runner (row 15) | – |
| `BuildingInformation` | second derivation pipeline; `tabula_ref_*` may be `None` | `Building.get_building_kpis_from_building_information` skips the TABULA KPI for an explicit building |
| `WeatherDataSourceEnum`, `LocationEnum`, `WeatherSourceFiles`, `calculation.interpolate` | new member `EPW`, new catalogue entry, time zone parameter (row 9) | every cached weather series gets a new key once (producer fingerprint); the enum is wire format in recorded twins, additive |
| `CSVLoader` | **no change**; used as the constant source (row 12) | – |
| `pytest.ini` | marker `verification` | none |
| `IdealizedElectricHeater`, `NightSetbackController`, `Window`, the 5R1C equations | **no change** | – |

## 4. Decisions for the human

| # | Decision | Recommendation | Decision (human, 2026-11-02) | Bead |
|---|---|---|---|---|
| D1 | F1: correct the mass state to θ_m,t (re-bless goldens) or declare the 2·C_m behaviour as a model deviation | correct, before step 3 | (b) for now, (a) deferred until after the first full report; register entry `model`; no compensation in the input mapping | hisim-x6c7.1 (open, `owner-decision`) |
| D2 | F2: where the operative-temperature control lives | option (1), `BuildingConfig.thermostat_control_variable` | option (3): air control, register entry `model`; no new field, no subclass | hisim-x6c7.2 (closed) |
| D3 | Warm-up: two-pass run, none, or a simulator feature | two-pass | two-pass | hisim-x6c7.3 (closed) |
| D4 | ISO suite: external gain input on the Building (row 5) or a producer mode | external input | not built; superseded by D9 | – |
| D5 | ISO climate: CSV converted once from the `.xls` in the private folder, or `xlrd` as a dependency | CSV | CSV, when the ISO suite returns | – |
| D6 | Shape of the explicit building description (row 1) and where the derivation functions live (row 2) | as proposed, `hisim/components/building/explicit.py` | option C as proposed: `building_code` optional, `explicit_description`, second pipeline in `explicit.py`; option B (more overrides on a TABULA row) rejected because every untouched value would come silently from the row. *Revised 2026-11-03 by D16 (§8): option B, with an origin record the suite checks* | hisim-x6c7.4 |
| D7 | FR4: drop the power limit for phase 1; the heater stays unchanged | drop | drop | hisim-x6c7.4 |
| D8 | Windows write-guard refusal of `\\.\nul` | separate bug; local runs use `HISIM_WRITE_GUARD=collect` meanwhile | workaround: the runner sets `HISIM_WRITE_GUARD=collect`; the bug stays open | hisim-repo-cleanup-o6a.36 |
| D9 | ISO suite (`iso52016`) in phase 1, after F3: the §7.4 bypass needs a new Building input | option 2 of F3 (optional gain input as input infrastructure) | deferred: phase 1 runs the ASHRAE suite only; no new Building input | hisim-x6c7.5 |
| D10 | Spec §3.2.1: printed air-to-air U-values, no thermal bridges, floor to outdoor air without a ground factor, no solar radiation on the floor; printed value over the layer recomputation (Case 900 floor) | as stated | as recommended | hisim-x6c7 (comment) |
| D11 | Spec §3.2.3: κ per element by the ISO 13786 effective-thickness rule, conditions (a) half thickness, (b) up to the first insulation layer, (c) 0.10 m; no class table, no full-layer sum | rule with all three conditions | as recommended. ISO 13790 (FDIS 2007 text, 12.3.1.1 and Table 11) states only the 0.10 m cap and refers to ISO 13786, which is not in the private folder: (a) and (b) stay [verify]. The rule is applied in the suite's system builder (step 3); the explicit description takes κ per element | hisim-x6c7 (comment) |
| D12 | Spec §3.2.4: A_tot as the actual area of all surfaces facing the zone, A_m = C_m²/Σ A_j κ_j² | actual A_tot, A_m by the formula | as recommended (ISO 13790 7.2.2.2 and 12.2.2; FDIS 2007 equation (66)) | hisim-x6c7.6 |
| D13 | Who resolves the [verify] marks against the standards in the private folder | the agent reads them; only clause and table numbers and the suite's own derived values enter the repository | as recommended; ISO 13790 marks resolved against the FDIS 2007 text on 2026-11-02, ASHRAE marks and Appendix B layer data open. Added 2026-11-03: the standards are read in place, no text is extracted or copied into any file, not even temporarily | – |

Beads: epic hisim-x6c7 (the suite); hisim-x6c7.1 stays open as the `owner-decision` for D1 (a) and no
longer blocks step 2; hisim-x6c7.2 and .3 are closed with their decisions; hisim-x6c7.4 (EPW weather, marker)
and hisim-x6c7.6 (explicit building) are the two step-2 PRs with the scope of §3; hisim-x6c7.5 holds the deferred ISO suite; hisim-repo-cleanup-o6a.36 is the write-guard
refusal under the repository-cleanup epic; hisim-component-physics-4g9.70 the dead setpoint fields of
`IdealizedHeaterConfig` under the component-physics epic.

## 5. Corrections applied to the spec (revision 4, 2026-11-02)

- §3.1, second bullet: add that the setpoints are `BuildingConfig` fields and the C.4 step is the
  thermostat; the ideal heater controls nothing.
- §3.1, third bullet and §2.5/§8.4: add F1; the time stepping is consistent, the state rule is not
  Annex C.
- §3.2.10: option (a) would be a Building option, not a heater option (F2); decided: air control
  with a register entry (D2).
- §3.2.8: resolved by F3 (constant F_w on the total plane irradiance, isotropic sky; ground
  reflectance 0.2 is the standard's value, not a deviation).
- §3.2.9: resolved by F4 (not implemented; register entry `model`).
- §4 FR4: schedule through `BuildingTemperatureModifier` + `NightSetbackController`; power limit
  dropped for phase 1; no control variable (D2). FR2, FR6, FR7, FR9 adjusted (second parameter
  source, EPW reader, `CSVLoader`, θ_op in the runner).
- §5 row "Operative temperature output": the surface temperature is an output.
- §5 row "Warm-up": not supported; §3.2.11 now specifies the two-pass run (D3).
- §11 step 2: reduced to the explicit building source with derivation and window list, explicit ACH
  and volume (D6), and the EPW reader with time-zone parameter and Denver entry; heater changes, a
  constant-gain component, an operative-temperature output and the ISO items are removed. Step 3
  runs case 600 of the ASHRAE suite; step 7 is deferred.
- §0 rule 3: the `not_modelled` rule of F2 (defining feature missing: case excluded; mechanism
  missing for all cases: register entry) and "no model or control code to make a case runnable".
- §1, §2.1, §7.4, §9.3, §13, §A: the `iso52016` suite is deferred (D9); the ISO climate path stays
  documented for a later phase.
- §3.1: F1 recorded (the state is the (C.9) average, 2·C_m dynamics, register entry); thermostat
  bullet corrected.
- §8.3: the `model` category lists the entries of F1, F3 (isotropic sky) and F4.
- §10: the Windows write-guard refusal and the `collect` workaround (D8).
- §A: warm-up, §3.2.8 to 3.2.11 and the thermostat item ticked with the date; the §3.2.1, 3.2.3 and
  3.2.4 decisions stay open.
- §7.2: `HISIM_VERIFICATION_DATA` is loaded from `.env` by `hisim_main` and the CLI only; pytest needs
  the suite's own `load_dotenv()`.

## 6. Checks and how to repeat them

### 6.1 Free decay of the mass node (F1)

Script used (run from the repository root with `PYTHONPATH=.` so that this checkout's `hisim` is
imported, not an installed one):

```python
"""Free decay of the thermal-mass node with the Building's own step, two state-update rules."""
import math
from hisim.components import building
from hisim.simulationparameters import SimulationParameters

HOURS, THETA_0 = (1, 3, 6, 12, 24), 20.0

def make_building(seconds_per_timestep):
    params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=seconds_per_timestep)
    config = building.BuildingConfig.preset_german_single_family_home("Building")
    config.weather_identity = "no weather component in this check"
    config.heating_reference_temperature_in_celsius = -7.0
    return building.Building(config=config, my_simulation_parameters=params)

def decay(b, rule, dt):
    state, out, per_hour = THETA_0, {}, 3600 // dt
    for step in range(1, 24 * per_hour + 1):
        average, _, _, _, _, next_theta_m, _, _ = b.calc_crank_nicolson(
            internal_heat_gains_in_watt=0.0, solar_heat_gains_in_watt=0.0,
            outside_temperature_in_celsius=0.0, thermal_mass_temperature_prev_in_celsius=state,
            thermal_power_delivered_in_watt=0.0)
        state = average if rule == "hisim" else next_theta_m
        if step % per_hour == 0 and step // per_hour in HOURS:
            out[step // per_hour] = state
    return out

b = make_building(3600)
c_m = b.my_building_information.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin
tau = c_m / (b.transmission_heat_transfer_coeff_3_in_watt_per_kelvin
             + b.external_part_of_transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin)
print("analytic", [round(THETA_0 * math.exp(-h * 3600 / tau), 4) for h in HOURS])
for rule in ("annex_c", "hisim"):
    for dt in (3600, 900, 60):
        print(rule, dt, [round(v, 4) for v in decay(make_building(dt), rule, dt).values()])
```

Output: the table of F1.

### 6.2 Run time of a full year, weather + building + ideal heater

Setup: `WeatherConfig.preset_aachen`, `BuildingConfig.preset_german_single_family_home`,
`IdealizedHeaterConfig.preset_standard`, wired heater ↔ building; `hisim_main` with a
`*.simulation.json` of a full year 2021 and no post-processing option as the **third** argument
(the second is the module configuration; a parameter file in second position is silently taken
for one); `HISIM_WRITE_GUARD=collect` (row 18). Wall time includes start-up, the weather and
solar-gain producers (cache state noted) and the empty post-processing.

| Δt [s] | time steps | wall time [s] | cache state |
|---:|---:|---:|---|
| 3600 | 8 760 | 5 | weather hit, solar gains miss (producer ran) |
| 900 | 35 040 | 8 | weather hit, solar gains miss (producer ran) |
| 60 | 525 600 | 32 | weather hit, solar gains hit; 115 s in an earlier run with a solar-gains miss |

The progress log of the 60 s run reports 3.1 to 5.0 passes per step (the shorter runs end before the
first progress line). The time loop of the three components costs about 30 s per minutely year;
the solar-gain producer adds about 80 s on a cache miss at 60 s and a few seconds at 3600 s, once
per distinct (window set, weather, time step) triple, then cached (cases with identical windows and
weather share the entry: 600, 640, 680, 685, 900, 940, 980, 985 and 995 differ only in opaque
elements and setpoints). The whole phase-1 matrix, 18 ASHRAE cases × 3 time steps × 2 passes (row 17), is
therefore of the order of an hour on this machine, and the pairs are independent. All three runs
completed the simulation and the post-processing and then exited with code 1 because the collected
stray write of row 18 is reported at the end.

## 7. Re-check after the merge of `origin/main` (2026-11-03)

**Code read at:** commit `d3a1ddc3` (tip of `origin/main`, merged into `verification_spec` as
`389ac8c0`). §1 to §6 describe the code at `88c40a22` (branch point of 2026-10-12) and are left as
written; this section says where `main` has moved since and what follows for the decisions. Files
cited above that `main` changed: `building.py`, `information.py`, `solar_gains.py`,
`weather/weather.py`, `weather/calculation.py`, `csvloader.py`, `idealized_electric_heater.py`,
`night_setback_controller.py`, `component.py`, `simulator.py`, `simulationparameters.py`,
`loadtypes.py`, `write_guard.py`, `pytest.ini`, `tests/conftest.py`, `system_docs/testing.md`,
`energy_system_v3.schema.json`. Unchanged: `window.py`, `weather/config.py`,
`calculation_scope.py`, `hisim_main.py`, `scripts/golden_check.py`.

### 7.1 Findings

| Finding | State on `main` | Consequence |
|---|---|---|
| F1 mass state | **Fixed on `main`** (PR #1079, commit `97ae8430`, bead hisim-b5u7, found there independently): `Building.i_simulate` stores θ_m,t (`next_thermal_mass_temperature_in_celsius`, building.py line 724); the output `TemperatureMeanThermalMass` keeps publishing the step average of (C.9). The goldens were re-blessed on `main`. | D1 is superseded: option (a) happened on `main`. Proposal D15 (§7.4): close hisim-x6c7.1 and drop the F1 `model` register entry from the spec. The rows "HiSim (average)" of the F1 table and the script of §6.1 describe the code before that PR. |
| F2 thermostat | The thermostat is still the Building's C.4 step on θ_air. The ideal heater is no longer a pass-through (PR #924): `IdealizedElectricHeater` (now `cp.PhysicsComponent`, input `ThermalPowerSetInWatt`) delivers what its L1 controller `IdealizedElectricHeaterController` (`IdealizedHeaterControllerConfig.preset_standard`) sets, and the controller copies `TheoreticalThermalBuildingDemand`. The setpoint fields of `IdealizedHeaterConfig` are still dead (hisim-component-physics-4g9.70). The Building's setback input is renamed `BuildingTemperatureModifierInKelvin` (unit K); `NightSetbackController` (now `cp.L2ControllerComponent`) emits it under that name. | D2 and D7 stand: air control, unlimited capacity, no change to heater or controller. The suite wires three components, Building → controller → heater → Building, as `tests/idealized_heater_rig.py` (`controller_between`) does; the delivered power is still exactly the demand. |
| F3 window optics | `window.py` unchanged. `solar_gains.py`: the producer DTO carries `window_start` and reads the `Weather.RUN_WINDOW_*` series (one value per time step of the run) instead of `YEARLY_*`. | Decision unchanged; the suite's full-year runs make the run window the whole year. |
| F4 opaque absorption, sky radiation | unchanged | – |

### 7.2 Capability table (§2): rows that moved

| # | Row | What changed on `main` | Consequence |
|---|---|---|---|
| 1, 2, 3 | explicit building | `information.py`: only bead ids in comments; the TABULA pipeline and both traps are unchanged. `BuildingState` now also carries the indoor air temperature (keyword argument `indoor_air_temperature_in_celsius`); `Building` is a `cp.PhysicsComponent` with the new output `TemperatureIndoorAirAtStartOfStepInCelsius`. `energy_system_v3.schema.json` was regenerated on `main`; `tests/test_energy_system_schema.py` grew. | D6 unchanged; the schema is regenerated after the config change, as planned. |
| 7 | ideal heater | see F2 | three-component wiring in the system builder |
| 8 | free float | `thermal_power_delivered_channel` still optional; `enable_opening_windows` still `False` by default (the opening rule gained a 0.01 K margin, irrelevant when disabled) | unchanged |
| 9 | EPW weather | `Weather` is a `cp.DataSourceComponent`. Every source must supply a relative-humidity column (`calculation.RelativeHumidity`, new output `RelativeHumidity`; an EPW has one). The produced frame (`ProducedFrame`) is indexed by the rows' labels in the source's **standard time** (`timestep_labels`, `WindowOffset.standard_time_labels`); the run's window is looked up in it (`WindowOffset.resolve`) and published as `RUN_WINDOW_*`. The station header is read by labels per format (`StationHeader`), so `get_coordinates` needs an EPW branch. `interpolate(series, year, pad_start_with_first_sample)` still pads with hard-coded `Europe/Berlin` timestamps (calculation.py lines 629 and 631) and the hourly readers still index at hh:30 `Europe/Berlin`; `DURATION_DEPENDENT_SOURCES` is gone (the frame always covers the year). | The plan stands and gains three items: the humidity column, an EPW entry in `StationHeader`/`get_coordinates`, labels in the EPW's fixed standard time (UTC−7, no DST; `Etc/GMT+7` has that standard time). Done in step 2a: `interpolate` pads in the zone of the series' own index, derived from it rather than passed, so the padding cannot disagree with the series; the German sources keep their padding instants. |
| 11 | sub-hourly interpolation | unchanged | unchanged |
| 12 | constant gains via `CSVLoader` | `CSVLoader` is a `cp.DataSourceComponent` with an optional `time_column` (ISO 8601 labels in standard time; finer rows are averaged); without it the file serves only a run starting on 1 January. The `dataframe` argument and `inputs_dir` remain. | unchanged for the suite: full-year runs start on 1 January; the constant 200 W frame needs no time column |
| 15 | θ_op in the runner | `TemperatureIndoorAir` and `TemperatureInternalSurface` unchanged; a start-of-step air output was added | unchanged |
| 16 | time steps, convergence | `Simulator`: `force_convergence` still after 10 passes; the abort limit scales with the step, 100 passes at 60 s, 1500 at 900 s, 6000 at 3600 s (`maximum_passes_for_step_length`). Components are evaluated kind by kind: data sources, L2 controllers, L1 controllers, physics, meters (`EVALUATION_ORDER_OF_KINDS`); every component class must inherit exactly one kind base class. | the runner still records passes and frozen steps |
| 17 | warm-up | `SimulationParameters.validated_window`: a window starts at midnight, covers whole days and **lies within one calendar year**. Mid-year windows work now (weather and CSV loader look the start up by label); "January in disguise" is history. | **D3 stands**: a 13-month run or a December start across the year is still refused; two-pass run unchanged |
| 18 | write guard | `write_guard.py`: a bead id only; `CalculationScope` unchanged | D8 unchanged; the Windows refusal is re-tested on the merged tree before step 3 |
| 21 | markers, CI | **New rule** (PRs #1039, #1044; `system_docs/testing.md` §2, `tests/job_markers.py`, registered by `tests/conftest.py`): every test carries exactly one job marker of the guide's table, otherwise the collection fails. A new job marker goes into the table, `pytest.ini` and `JobMarkers.NAMES` together, and into `tests.yml` only if it gets a job of its own; a marker without a job runs in `pytest (others)`, whose `-m` expression excludes only the markers that have jobs. | **D14 (§7.4)**: "marker `verification` in `pytest.ini`" alone would fail every collection. |
| facts | convergence loop, results access | see row 16; `results_data_frame` and `PostProcessingOptions.EXPORT_TO_CSV` unchanged | – |

### 7.3 Interfaces (§3) after the merge

- `Building`: still no change by this work; `main` renamed the input to `BuildingTemperatureModifierInKelvin`.
- `IdealizedElectricHeater` and its controller: no change; the suite adds the controller to its systems.
- `WeatherDataSourceEnum`, `LocationEnum`, `WeatherSourceFiles`, `interpolate`: as planned, plus the EPW entries in `RelativeHumidity` and `StationHeader`.
- `CSVLoader`: no change.
- `pytest.ini`: per D14, together with `tests/job_markers.py` and `system_docs/testing.md`.

### 7.4 Decisions

| # | Decision | Recommendation | Decision (human) | Bead |
|---|---|---|---|---|
| D14 | Job marker of the suite under the rule of `system_docs/testing.md` §2: (a) a new job marker `verification` in the table, `pytest.ini` and `JobMarkers.NAMES`, run by the catch-all job `pytest (others)` without a `tests.yml` change (like `mpc`), the suite tests skipping when `HISIM_VERIFICATION_DATA` is unset, the unit tests of step 2 carrying `base`; (b) existing markers only, `base` for the unit tests and `extendedbase2` for the suite runs | (a): one marker names the suite, no workflow edit, GitHub CI sees skips only | (a), 2026-11-03; registered in step 2a | hisim-x6c7.4 |
| D15 | F1 is fixed on `main` (#1079): close hisim-x6c7.1 as superseded and drop the F1 register entry from the spec (§3.1, §8.3) | yes | yes, 2026-11-03; bead closed | hisim-x6c7.1 |

### 7.5 Spec corrections applied (revision 6, 2026-11-03)

- §3.1: the F1 bullet states the fix on `main`; the thermostat bullet names the heater's controller.
- §3.2.10, FR4: `BuildingTemperatureModifierInKelvin`; three-component wiring.
- §5 row "test framework", §9.1, §9.2, §11 step 2a: the job-marker rule and D14.
- §8.3: the F1 entry leaves the `model` category (pending D15).
- §A: merge item ticked; D14 and D15 listed before step 2a.

## 8. Revision of D6 (2026-11-03): one parameter path, TABULA with overrides

Raised by the human before step 2b: option C of D6 means two parameter pipelines in
`BuildingInformation` to maintain. The code was re-read at `80e2d0df` (tip of
`verification/step2-epw-weather`) on 2026-11-03.

### 8.1 What the code says

- The Building's 5R1C reads neither the thermal-bridging conductance nor the transmission total:
  H_tr_op is facade + roof + floor (`building.py`,
  `get_thermal_conductance_of_opaque_surfaces_in_watt_per_kelvin`), H_tr_w is window + door
  (`get_thermal_conductance_between_exterior_and_windows_and_door_in_watt_per_kelvin`). The
  surcharge enters only `max_thermal_building_demand_in_watt`, which the config already overrides.
  The 0 → 0.1 W/(m²K) trap of row 1 is therefore harmless for the suite. Note for the register in
  step 3: the 5R1C has no thermal-bridge term at all; both suites set zero, so no deviation.
- The floor trap is real: a configured floor U-value gets the fixed factor 0.5
  (`FLOOR_ELEMENT.fixed_adjustment_factor`); wall and roof get 1.
- The origin record exists: `ElementValues.u_value_origin` and `_door_area_origin` state for each
  U-value and the door area whether it is configured or from the row (`element_values()`, read by
  RenoVisor). Extending that record to every quantity the 5R1C reads is the code's own pattern.
- Every row column that reaches the 5R1C: `A_C_Ref` (scaling), `A_*`, `U_Actual_*`,
  `b_Transmission_*` (through the element descriptors), `A_Window_<direction>`, `F_w`, `F_f`,
  `F_sh_vert`, `g_gl_n`, `n_air_use`, `n_air_infiltration`, `h_room`, the heat-capacity class
  table (C_m, A_m) and Λ_at = 4.5 (A_tot). Overridable today: the five element areas and
  U-values, the floor area, the apartments, the maximum demand. Everything else is not.
- The window optics already take a tilt and an azimuth per window (`WindowGeometry`); only
  `Building.get_windows` fixes them to the five TABULA directions (tilt 90° or 0°; azimuth 180, 90,
  0, 270) and fills the four optics factors with the row's single values for all windows.

### 8.2 Design: overrides on the TABULA row, with an origin record

`BuildingConfig.building_code` stays mandatory; the row is the only pipeline. New optional fields,
each with the rule the config already spells for the areas, "unset means the TABULA row's value"
(the names as implemented in step 2b):

| Field(s) | Replaces | Note |
|---|---|---|
| `windows: Optional[List[ConfiguredWindow]]`; one item per window: `area_in_m2`, `azimuth_angle_in_degrees` (`None` for horizontal), `tilt_angle_in_degrees`, and the four optics factors `glass_solar_transmittance`, `frame_area_fraction_reduction_factor`, `nonperpendicular_reduction_factor`, `external_shading_vertical_reduction_factor`, each optional with the row's value as default | the per-direction `A_Window_*` scaling and the row's single `g_gl_n`, `F_f`, `F_w`, `F_sh_vert`; the window area is the list's sum | one item is one `WindowGeometry`, built by `BuildingInformation` (§8.3); any tilt and azimuth. A list beside `window_area_in_m2` is refused by name |
| `use_air_change_rate_per_hour`, `infiltration_air_change_rate_per_hour` | `n_air_use`, `n_air_infiltration` | the suite sets use 0 and the scaled infiltration of spec §3.2.6 |
| `conditioned_air_volume_in_m3` | `h_room` · A_f | the volume the standards state; H_ve = 0.34 Wh/(m³K) · n · V as today, without the floor area's scaling round trip |
| `floor_transmission_adjustment_factor`, `facade_…`, `roof_…` | the row's largest `b_Transmission_*`, or the fixed factor when the U-value is configured | a configured factor is used as given (1.0 for the floor over outdoor air, D10); the 0.5 stays for configs that do not set it |
| `thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin`, `effective_mass_area_in_m2`, `total_internal_surface_area_in_m2` | f_c(class) · A_f, f_a(class) · A_f, 4.5 · A_f | the suite computes them by spec §3.2.3 and 3.2.4 in the system builder (step 3) and passes the results |

- `BuildingInformation` records the origin of every quantity above and of the existing overrides
  in one mapping, `value_origins()`: a `ValueOrigin` per quantity whose `source` is `configured`,
  `TABULA row` or `derived` (a fixed factor, the class table, a fallback) with the field, columns or
  rule as detail; `window_geometries()` hands the Building its windows. The suite's system builder
  refuses a case whose record names the row anywhere and writes the record to
  `parameters/<case>.json`: the carrier row is visible, and so is that nothing of it is used. For a
  fully configured building only the window's and the door's adjustment factors are `derived`
  (both are always 1).
- The TABULA path is unchanged: every new field defaults to `None`; the goldens stay bit-identical.
- `hisim/components/building/explicit.py` is not created. C_m = Σ κ_j A_j, A_m = C_m²/Σ A_j κ_j²
  and A_tot = Σ A_j belong to the suite's system builder beside the layer rule (step 3), tested
  there against Appendix B.
- Known and accepted: the suite's floor area goes through the scaling round trip
  `A_C_Ref · (48 / A_C_Ref)` and may differ from 48 m² in the last bit (pinned behaviour of
  `get_scaling_factor_according_to_conditioned_living_area`). With the volume override it reaches
  no 5R1C parameter, only the Building's per-m² KPIs; the value used is in the parameters output.
- `BuildingConfig` grows by 11 optional fields, flat like the existing ones; the schema is
  regenerated; recorded twins are unaffected (they record deviations from presets only).

### 8.3 The Building reads the window geometries (part of 2b)

`BuildingInformation` builds the `WindowGeometry` list: from the row's five directions with the
values `Building.get_windows` (building.py lines 1141 to 1158) computes today, bit-identical and in
the same order, so the solar-gains cache key of a TABULA building does not move; or from the
override, one item per window with its own tilt, azimuth and optics. `get_windows` takes that list
and sums the areas; the loop with the fixed angles goes. About 45 lines replaced by 5; no change to
the conductances, the DTO or the outputs. The human's first choice (2026-11-03) was to leave the
Building untouched in 2b and lift a window-list restriction in a step 2c; that was dropped the same
day: a restriction built only to be removed is no design, and F3's F_w per orientation (cases 620
and 920 have an east and a west window whose F_w differ slightly through the weather's
morning/afternoon asymmetry) needs the per-window geometry anyway.

### 8.4 Decisions

| # | Decision | Recommendation | Decision (human) | Bead |
|---|---|---|---|---|
| D16 | Revise D6: (B) one parameter path, overrides on the TABULA row for every value the 5R1C reads, a window list with angles per window, an origin record the suite checks; or keep (C), the second pipeline | (B) with the three conditions of §8.2: the window list in its final shape, the origin record checked by the suite, the TABULA path bit-identical | (B), 2026-11-03, as recommended. Same day: the per-building optics overrides are dropped (the list items carry them), the volume replaces the room height | hisim-x6c7.6 |
| D17 | Step 2c (the Building reads the geometries, §8.3): directly after 2b, or later | directly after 2b, before step 3 | folded into 2b, 2026-11-03: no restriction is built that a later step removes (§8.3) | hisim-x6c7.6 |

### 8.5 Spec corrections applied (revision 7, 2026-11-03)

- §3.1: the parametrisation bullet. §3.2 introduction and rules 3 and 4: rules 1 to 4 live in the
  system builder, HiSim receives the results as overrides; rule 5 stays HiSim's.
- FR2 rewritten; §11 steps 2b and 3; §A: D16 and D17 recorded.

## 9. ASHRAE 140 [verify] marks resolved against the standard (2026-11-03)

Method (D13): the PDF in the private folder was searched in place for the cited clause and table
numbers, printing only the matching line heads; nothing of it was copied into a file. Two
acceptance-range rows of Annex A3 appeared in the local session log of that search by mistake;
they are in no file and nowhere in the repository.

### 9.1 Confirmed (clause and table numbers of ANSI/ASHRAE 140-2023)

Geometry 7.2.1.3; ground reflectance 0.2, 7.2.1.1.2.5; raised floor 7.2.1.5; materials Table 7-2
(600), 7-25 (680), 7-27 (900), 7-31 (980), with the informative summaries Tables 7-3 and 7-28;
infiltration 0.5 ach, 7.2.1.6, the altitude adjustment 7.2.1.6.1 with Table 7-4 and Annex B3.1;
internal gains 7.2.1.7, 60 % radiative and 40 % convective; Table 7-5 with 7.2.1.8.1 (interior
solar absorptance 0.6, infrared emittance 0.9); surface coefficients Tables 7-7 and 7-9 with
7.2.1.9.3 and 7.2.1.10.3; window: Normative Table 7-10 (curtains, blinds, frames, spacers,
mullions: none), Informative Table 7-11, Table 7-12 (SHGC 0.769 at normal incidence), 7.2.1.11.3
a) (a program without angle-dependent inputs skips the section); interior solar distribution
7.2.1.12.2 with Tables 7-13 (600) and 7-15 (620); mechanical system 7.2.1.13, thermostat
7.2.1.13.1 (senses only the air temperature; heat on below 20 °C, cool on above 27 °C); Case 640
in 7.2.2.1.4 (cooling at 27 °C at all hours); Case 650 in 7.2.2.1.5 (1700 standard m³/h); 660
Table 7-17 (7.2.2.1.6); 670 Table 7-21 (7.2.2.1.7); 900 in 7.2.2.2.1; in-depth cases 7.2.3 from
Case 195 (7.2.3.5) to Case 810 (7.2.3.25); outputs Table 7-47 with 7.3.8, the 1 °C bins of 7.3.7;
site Table A1-1; Annex A3: Table A3-14 counts (low mass 21 ranges, 18 required; high mass 19 and
17), Case 650 listed in Table A3-2 only, Case 910 as 900 − 910 in Tables A3-3 and A3-4. Thermal
bridges: the term does not occur in the standard; the constructions are plane layers.

### 9.2 Still marked

The hemispherical SHGC 0.670 of Table 7-12 (not located by search; pyBuildingEnergy's reading),
the exterior solar absorptance of Table 7-5, the window placement of 620, the 20/20 set points of
685, 695, 985 and 995, 7.2.2.3 for the free-float cases, the geometry of the shading cases (not
modelled), and every ISO item (ISO 13786 unavailable, the ISO 52016 suite deferred). The geometry
and layer data of Appendix B were checked by the human on 2026-11-03: all correct.

### 9.3 Finding F5: the set-point ramp of cases 640 and 940

7.2.2.1.4 c: from 07:00 to 08:00 the heating set point rises linearly from 10 °C to 20 °C;
sub-hourly programs follow the ramp step by step (12.5, 15, 17.5 and 20 °C at 900 s), hourly
programs use 20 °C for that hour. Spec §3.2.10 had the set point at 20 °C from 07:00.
`NightSetbackController` has `night_end_time_in_hours` and one `setback_delta_in_kelvin`: a step.
Options: (a) the step at 07:00 as planned, registered as `input_mapping` naming 7.2.2.1.4 c (the
morning peak and the heating of that hour differ from the ramp, the annual heating little);
(b) a ramp end hour in the controller (control code; rule 3 allows it only as a refinement, the
case runs without it); (c) the step at 08:00 (the hourly programs' reading, wrong at 900 s and
60 s). Recommendation: (a) for the first full report, revisited with the report.

### 9.4 Decision

| # | Decision | Recommendation | Decision (human) | Bead |
|---|---|---|---|---|
| D18 | F5: the 07:00 to 08:00 set-point ramp of 640 and 940 | (a) step at 07:00 with a register entry | (a), 2026-11-03, for now; revisited with the first full report | hisim-x6c7 |

### 9.5 Spec corrections applied (revision 8, 2026-11-03)

- §2.1, §2.2, §2.4, §3.1, §3.2 rules 1, 2, 6 to 10 and 11, §7.3, §8.1: the confirmed clause and
  table numbers replace their [verify] marks; the marks of §9.2 stay.
- §3.2.10: the ramp hour of 7.2.2.1.4 c; D18 in §A.
- §3 and §4 of this document are left as written; D6 points here.
