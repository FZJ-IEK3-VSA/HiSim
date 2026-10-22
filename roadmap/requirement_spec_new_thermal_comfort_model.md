# Requirement spec: adaptive thermal comfort KPI in HiSim

## 1. Purpose and scope

HiSim shall report thermal comfort KPIs based on the ASHRAE 55 adaptive model, so that heating and cooling comfort can be compared across buildings, renovation variants and climates. Background and reasoning: see the Summary tab.

In scope: a new operative temperature time series and new comfort KPIs computed after the simulation. Out of scope: changes to heating/cooling control, energy demand or any other results.

## 2. Definitions and parameters

| Term | Definition | Value |
| --- | --- | --- |
| T\_air | Indoor air temperature of the building model | °C, per timestep |
| T\_s | Internal surface (star) node temperature of the 5R1C model | °C, per timestep |
| T\_op | Operative temperature: 0.3 · T\_air + 0.7 · T\_s (ISO 13790, eq. C.12) | °C, per timestep |
| Daily mean outdoor temperature | Arithmetic mean of the outdoor air temperature over all timesteps of a calendar day | °C, per day |
| T\_pma | Prevailing mean outdoor temperature: arithmetic mean of the daily means of the 7 days before the current day (current day excluded) | °C, per day |
| T\_neutral | 0.31 · T\_pma + 17.8 | °C, per day |
| Adaptive band | T\_neutral − 3.5 K to T\_neutral + 3.5 K (ASHRAE 55, 80 % acceptability) | applies when 10 °C ≤ T\_pma |
| Fixed band | 20 °C to 25 °C | applies when T\_pma < 10 °C |
| Validity cap | T\_pma values above 33.5 °C are treated as 33.5 °C | ASHRAE 55 upper validity limit |
| Occupied timestep | Timestep with NumberOfResidents > 0 (sum over all households) | from the load profile (LPG/UTSP) |
| Degree-hours (K·h) | Sum over occupied timesteps of the temperature distance outside the band, times the timestep length in hours | °C·h |

## 3. Functional requirements

| ID | Requirement |
| --- | --- |
| FR-1 | The building component shall output T\_op for every timestep, next to T\_air and T\_s. |
| FR-2 | For each calendar day, the comfort band shall be determined from T\_pma: the adaptive band if T\_pma ≥ 10 °C, otherwise the fixed band; T\_pma above 33.5 °C is capped. The band is constant within a day. |
| FR-3 | For the first 7 days of a run, T\_pma shall use the last days of the simulated year (wrap-around); for runs shorter than 8 days, the mean of all available daily means. |
| FR-4 | Only occupied timesteps shall count in the comfort KPIs. |
| FR-5 | HiSim shall report degree-hours of T\_op below the lower band limit (underheating), in °C·h. |
| FR-6 | HiSim shall report degree-hours of T\_op above the upper band limit (overheating), in °C·h. |
| FR-7 | HiSim shall report the minimum and maximum T\_op over occupied timesteps, in °C. |
| FR-8 | HiSim shall report the number of occupied hours used, in h. |
| FR-9 | Degree-hours shall scale with the timestep length, so the result is the same for 1-min, 15-min and 1-h timesteps of the same temperature curve. |
| FR-10 | The KPIs shall be reported per building, together with the other building KPIs, under fixed names that do not depend on configuration values. |
| FR-11 | All parameters (0.3 / 0.7 weights, 7 days, 0.31, 17.8, 3.5 K, 10 °C, 33.5 °C, 20 °C, 25 °C) shall be defined once as named constants. |
| FR-12 | If no occupancy data exist, every timestep shall count as occupied and a warning shall be logged. If no timestep is occupied, degree-hours and occupied hours are 0 and min / max are undefined (NaN). |

## 4. Constraints

| ID | Constraint |
| --- | --- |
| C-1 | Heating and cooling control and the theoretical demand calculation remain based on indoor air temperature. |
| C-2 | Energy, cost and emission results of existing setups must not change. |
| C-3 | Existing comfort KPIs remain available until their readers (RenoVisor comfort grades, building sizer) are switched; see open points. |
| C-4 | The existing daily average outdoor temperature series of the weather component is a simulation input and must not be changed or reused for T\_pma. |

## 5. Acceptance criteria

| Check | Input | Expected |
| --- | --- | --- |
| Operative temperature | T\_air = 20 °C, T\_s = 18 °C | T\_op = 18.6 °C |
| Prevailing mean | Daily means 10, 11, …, 16 °C on days 1–7 | T\_pma on day 8 = 13.0 °C, independent of day 8's own temperature |
| Adaptive band | T\_pma = 13 °C | 18.33–25.33 °C |
| Switch point | T\_pma = 10.0 °C / 9.9 °C | 17.4–24.4 °C / 20–25 °C |
| Cap | T\_pma = 35 °C | Same band as T\_pma = 33.5 °C |
| Occupancy | Hourly T\_op 26.33 and 27.33 °C, upper limit 25.33 °C, residents 1 then 0 | Overheating = 1.0 °C·h |
| Timestep | Same temperatures in 15-min steps | One quarter of the hourly degree-hours |
| No occupancy | NumberOfResidents = 0 throughout | 0 °C·h, 0 h, min / max NaN |
| Full-year run | Default single-family home | The new KPIs are reported; existing energy, cost and emission KPIs unchanged |

## 6. Open points

- [ ] Replace the existing comfort KPIs? The RenoVisor comfort grades (calibrated on air-temperature degree-hours > 1 K below the heating setpoint and > 26 °C) and the building sizer read them by name. Switching them to the new KPIs needs new grade limits.
- [ ] Confirm the journal references in the Summary tab before publication.
