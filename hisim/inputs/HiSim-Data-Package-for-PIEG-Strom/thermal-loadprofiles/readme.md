# Domestic hot water load profiles from 1 to 200 persons

## Sources and License:

- `docs` -> Software: DHWcalc v2.02b

- `data_processed` [![License: CC BY 4.0](https://img.shields.io/badge/License-CC%20BY%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by/4.0/)

## Summary:

The data set contains domestic hat water (dhw) load profiles from 1 up to 200 persons. The profiles where generated with the software DHWcalc (v2.02b) under default settings with a mean water consumption of 40 liters at 45°C per person and day, which reperesents an average consumption.

For the calculation of the load profile in W the cold water temperature (T_coldwater) was assumed to vary between 10 and 15 °C 

T_coldwater = 12.5 + 2.5 * sin(270° + (DOY/365) * 360°)

with DOY = day of year. The yearly energy consumption results in **555 kWh per person and year**.

## Content

* **files**: 200 dhw load profiles with the number representing the number of persons

```
dhw_1.csv
...
...
dhw_200.csv
```

* **columns per file**:

```
datetime [yyyy-MM-dd hh:mm:ss+01:00/02:00]
flow rate at 45 °C [kg/h]
load [W]
```

* **length**: 1 year

* **time increment**: 1min / 15min / 60min

## Important hints:

- all files in `data_processed` were calculated with the skript `process_data.ipynb`
- *A value with, for example, a timestamp 12:00:00 represents the mean value from this timestamp until the following timestamp.*
- *datetime column is in CET / CEST*
