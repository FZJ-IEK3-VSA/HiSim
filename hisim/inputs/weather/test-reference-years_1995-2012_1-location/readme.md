# Test Reference Years (TRY) for 15 typical regions in germany with special regards on realisitc radiation data on a 1min timescale

**Sources**:
- Raw data from DWD: https://kunden.dwd.de/obt/ -> `1_raw-data`
- Synthetic 1min radiation data: http://pvmodelling.org/ -> `2_synthetic-radiation`

**Summary**:

The data set contains the updated test reference years (TRY) of the German Weather Service (DWD). By subdividing into 15 TRY regions, each postcode area can be assigned a representative weather data set. It should be emphasized that in addition to a mean, current test reference year for a region, there is also a year with extreme summer and extreme winter weather. To take climate change into account, there is then a time series for the year 2045 for each test reference year based on the IPCC climate models. This means that a total of 90 weather data sets are available with a one-hour time resolution.

In order to use the data in simulations with a temporal resolution of 1min or 15min, the data set was extended by linear interpolation. While this approach is justifiable for air pressure and temperature, for example, it does not depict high fluctuations in solar radiation. Therefore, based on the one-minute open data measurement data set of the Baseline Surface Radiation Network, with an algorithm by Hofmann et. al. the time series of global radiation are newly generated for all test reference years. Another algorithm by Hofmann et. al. was used to calculate the corresponding diffuse radiation times series.

Final datasets are located in -> `3_processed-data`

**Test reference stations / regions**

No. | lon | lat | station | region
--- | --- | --- | --- | ---
1 | 53.5591 | 8.5872 | Aachen | West

![regions & stations](./0_docs/TRY-Regions.png)

**Content**
* **files**: 90 test reference years (TRY)
```
15 test reference regions
x 3 reference conditions (average year, extreme summer, extreme winter)
x 2 reference projections (year 2015 and year 2045)
```
* **columns per file**:
```
datetime [yyyy-MM-dd hh:mm:ss+01:00/02:00]
temperature [degC]
pressure [hPa]
wind direction [deg]
wind speed [m/s]
cloud coverage [1/8]
humidity [%]
direct irradiance [W/m^2]
diffuse irradiance [W/m^2]
synthetic global irradiance [W/m^2]
synthetic diffuse irradiance [W/m^2]
clear sky irradiance [W/m^2]
```
* **length**: 1 year
* **time increment**: 60s / 900s / 3600s

**Important hints**:
- all files in `3_processed-data` were calculated with the skript `process-data.py`
- *A value with, for example, a timestamp 12:00:00 represents the mean value from this timestamp until the following timestamp.*
- *datetime column is in CET / CEST*
