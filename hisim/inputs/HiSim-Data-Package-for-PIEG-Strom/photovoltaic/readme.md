# Normalized photovoltaic profiles for different orientations based on test reference weather data on a 1min timescale

## Sources and License:

Software: `pvlib-python` [![License: CC BY 4.0](https://img.shields.io/badge/License-BSD%20Clause%203-lightgrey.svg)](https://creativecommons.org/licenses/by/4.0/)

Weather: `../weather/data_processed`[![License: CC BY 4.0](https://img.shields.io/badge/License-CC%20BY%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by/4.0/)

Results: `data_processed`[![License: CC BY 4.0](https://img.shields.io/badge/License-CC%20BY%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by/4.0/)


## Summary:

The data set contains normalized photovoltaic profiles (W/kWp) with a temporal resolution of 1min for three different orientations based on the updated test reference years (TRY) of the German Weather Service (DWD).

- 1 kWp south, 35° tilt
- 0.5 kWp east + 0.5 kWp west, 35° tilt
- 0.25 kWp east-west + 0.75 kWp south, 35° tilt

In order to use the data in simulations with a temporal resolution of 15min or 60min, the data set was down-sampled.

Final datasets are located in -> `data_processed`

Summary of all specific yields (**702 to 1199 kWh/kWp**) are located in `data_processed/photovoltaic_summary`.

## Photovoltaic system

- pvwatts-model
- module
	- type: glass-polymer
	- mounting: open-rack
	- temperature  coefficient: -0,3 %/K
- inverter: 1 kVA / kWp

## Content

* **files**: 270 photovoltaic profiles

```
15 test reference regions
x 3 reference conditions (average year, extreme summer, extreme winter)
x 2 reference projections (year 2015 and year 2045)
x 3 orientations
```

* **columns per file**:

```
datetime [yyyy-MM-dd hh:mm:ss+01:00/02:00]
south [W/kWp]
east-west [W/kWp]
025-east-west_075-south [W/kWp]
```

* **length**: 1 year

* **time increment**: 1min / 15min / 60min

## Important hints:

- all files in `data_processed` were calculated with the skript `process_data.ipynb`
- *A value with, for example, a timestamp 12:00:00 represents the mean value from this timestamp until the following timestamp.*
- *datetime column is in CET / CEST*
