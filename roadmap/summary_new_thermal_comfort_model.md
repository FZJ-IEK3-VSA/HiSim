# Adaptive thermal comfort KPI for HiSim – summary

Oct 19, 2026 · @Katharina

HiSim will evaluate thermal comfort with the ASHRAE 55 adaptive model in summer and fixed 20–25 °C limits in winter, using operative temperature and counting only hours when residents are at home. This tab summarises the current state, the new approach and the reasoning; the requirements are in the tab Requirement spec.

## Current state

HiSim evaluates comfort with fixed limits on indoor air temperature, over all hours and for every weather file and climate.

| KPI | Definition | Used by |
| --- | --- | --- |
| Deviation below heating setpoint | Degree-hours of air temperature below 20 °C | Building sizer |
| Deviation above cooling setpoint | Degree-hours of air temperature above 25 °C | Building sizer |
| Underheating degree-hours | Degree-hours of air temperature more than 1 K below the heating setpoint | RenoVisor comfort grade (heating) |
| Overheating degree-hours | Degree-hours of air temperature above 26 °C (DIN 4108-2, region B) | RenoVisor comfort grades (cooling, summer heat protection) |
| Min / max indoor air temperature | Lowest and highest air temperature of the run | Building sizer |

Heating and cooling are controlled on indoor air temperature (ISO 13790 C.4.2), with default setpoints of 20 °C heating and 25 °C cooling.

Limitations: the summer limit ignores the climate and recent weather; air temperature ignores cold or warm walls; empty hours count as discomfort.

## New approach

Agreed on Oct 22, 2026. Comfort limits follow recent outdoor weather in summer and stay fixed in winter; control of heating and cooling is unchanged.

| Element | Choice |
| --- | --- |
| Standard | ASHRAE 55-2023, Section 5.4 (adaptive model) |
| Prevailing mean outdoor temperature T\_pma | Arithmetic mean of the daily mean outdoor temperatures of the previous 7 days |
| Neutral temperature | T\_neutral = 0.31 · T\_pma + 17.8 °C |
| Summer (T\_pma ≥ 10 °C) | Comfort band T\_neutral ± 3.5 K (80 % acceptability) |
| Winter (T\_pma < 10 °C) | Fixed band 20–25 °C |
| Temperature evaluated | Operative: T\_op = 0.3 · T\_air + 0.7 · T\_s (ISO 13790, eq. C.12) |
| Hours counted | Only hours with at least one resident at home (NumberOfResidents > 0) |
| KPIs | Degree-hours below the band, degree-hours above the band, min and max operative temperature |
| Control | Unchanged: heating and cooling still target indoor air temperature |

Example band values: at T\_pma = 10 °C the summer band is 17.4–24.4 °C; at T\_pma = 20 °C it is 20.5–27.5 °C.

## Explanation

**Adaptive model.** Field studies (de Dear & Brager; Nicol & Humphreys) showed that people accept warmer indoor temperatures after warm weather, through clothing, window opening and expectation. ASHRAE 55 turns this into a comfort temperature that rises with recent outdoor temperature. The static PMV model (ISO 7730) instead gives fixed limits per season.

**Prevailing mean outdoor temperature (T\_pma).** The average of the daily mean outdoor temperatures of the previous days, not the current outdoor temperature. People adapt over days, so a single hot afternoon does not shift the limit. ASHRAE allows 7–30 days; 7 days reacts faster to heat waves.

**Neutral temperature (T\_neutral).** The indoor temperature at which the average person feels neither warm nor cool. It is the centre of the comfort band, not a limit.

**Comfort band.** ASHRAE 55 defines 80 % acceptability as T\_neutral ± 3.5 K and 90 % as ± 2.5 K. We use 80 %, the usual choice for typical buildings.

**Winter limits.** The adaptive model is only valid for T\_pma between 10 and 33.5 °C. Below 10 °C, which covers much of the German heating season, fixed limits of 20–25 °C apply. 20 °C is the usual lower limit for heated homes (EN 16798-1, Category II).

**Operative temperature.** Comfort standards define limits on operative temperature, which combines air and radiant (wall) temperature. In the 5R1C model ISO 13790 approximates it as 0.3 · T\_air + 0.7 · T\_s, where T\_s already mixes air and mean radiant temperature. In unrenovated buildings with cold walls, T\_op is below T\_air in winter; since control stays on air temperature, this shows up as underheating and is reported as a real comfort deficit.

**Occupied hours.** Discomfort only matters when someone is at home. EN 16798-1 and CIBSE TM59 likewise evaluate occupied hours.

**Assumption.** ASHRAE 55 formally limits the adaptive model to spaces without mechanical cooling and with occupant control. Applying it to buildings with heating or cooling is common in research and is stated as an assumption.

## References

Standards were checked by the team (ASHRAE 55-2023 Section 5.4, ISO 13790 eq. C.12); the journal papers are cited from memory and need checking before publication.

- ANSI/ASHRAE Standard 55-2023, Thermal Environmental Conditions for Human Occupancy, Section 5.4 (adaptive model)
- ISO 13790:2008, Energy performance of buildings – Calculation of energy use for space heating and cooling, Annex C (5R1C model, eq. C.12)
- EN 16798-1:2019, Energy performance of buildings – Indoor environmental input parameters
- ISO 7730:2005, Ergonomics of the thermal environment (PMV/PPD)
- DIN 4108-2, Thermal protection and energy economy in buildings – Part 2 (summer overheating limits)
- CIBSE TM59 (2017), Design methodology for the assessment of overheating risk in homes
- de Dear R., Brager G. (1998), Developing an adaptive model of thermal comfort and preference, ASHRAE Transactions
- de Dear R., Brager G. (2002), Thermal comfort in naturally ventilated buildings, Energy and Buildings
- Nicol F., Humphreys M. (2002), Adaptive thermal comfort and sustainable thermal standards for buildings, Energy and Buildings
- Nicol F., Humphreys M. (2010), Derivation of the adaptive equations for thermal comfort in free-running buildings in European standard EN15251, Building and Environment
