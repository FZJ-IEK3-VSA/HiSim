# Outdoor relative humidity of the shipped weather files: where it comes from

Every weather source publishes the outdoor relative humidity, in percent of saturation over water,
0 to 100 (the `RelativeHumidity` output of the Weather component; the defrost model of the air-source
heat pump reads it). The reader of each source takes it from a column of the data file
(`hisim.components.weather.calculation.RelativeHumidity.FILE_COLUMNS`) and refuses a file without it.
This page says where each column comes from, how it was added to the files that did not carry it, and
how that was checked. The humidity of every file comes from the same upstream data, site and period as
its temperature, and every added column was aligned by first reproducing the file's existing
temperature from that data.

Fetched on 2026-10-21. No other column of any file was changed: each edited file differs from its
predecessor only by the one column appended to its column header and to every data row.

## DWD test reference years (`DWD_TRY`)

`test-reference-years_1995-2012_1-location` and `test-reference-years_2015-2045_15-locations`: the `.dat`
files of the Deutscher Wetterdienst carry the relative humidity in their own column `RF` (percent,
1 to 100) and the mixing ratio in `x` (g/kg). Nothing was added. The reader keeps `RF`; `x` is not
published, because no other source carries it.

## NSRDB quarter-hourly files (`NSRDB_15MIN`, `NSRDB_15min/*/*.csv`, 20 files)

- **Source**: NREL National Solar Radiation Database, the Meteosat Prime Meridian data set (PSM v3) of
  2019, the data set these files were exported from: their file names carry its site id
  (`469536_52.37_4.90_2019.csv` is site 469536), and the site's latitude, longitude and elevation in the
  data set equal the file header's.
- **Data**: the variable `relative_humidity` (scale factor 100) of
  `https://nrel-pds-nsrdb.s3.amazonaws.com/meteosat/meteosat_csp_2019.h5` (the public NSRDB bucket of the
  AWS Open Data registry), column = site id. The temperature and wind speed used for the check are
  `air_temperature` and `wind_speed` of `meteosat_pv_2019.h5` beside it.
- **Alignment**: the files are the NSRDB download in local standard time: row `k` is the data set's
  quarter hour `k - 4 * tz` (UTC), with the first `4 * tz` rows taken from the end of the year, where
  `tz` is the header's `Time Zone`. The column was laid onto the rows the same way.
- **Check**: under that alignment the file's `Temperature` and `Wind Speed` equal the data set's in every
  row of every file (to the files' 0.1 rounding).
- **Column**: `Relative Humidity`, percent, two decimals.

## NSRDB hourly files (`NSRDB`, `NSRDB/*/*.dat`, 9 files)

- **Source** (all but Seville, see below): the same NSRDB Meteosat Prime Meridian data set of 2019. The
  headers call the files a typical meteorological year of 2017 to 2019 ("mittleres Jahr", "TMY"), but
  their rows are the data of 2019: every row's temperature and wind speed equal the data set's 2019
  values at hh:30 UTC. The site is the one at the header's coordinates; its elevation in the data set
  equals the header's.

  | File      | Site id |
  |-----------|---------|
  | Athens    | 1291832 |
  | Belgrade  | 1108362 |
  | Cyprus    | 1809009 |
  | Ljubljana | 808557  |
  | Madrid    | 232500  |
  | Milan     | 597263  |
  | Sarajevo  | 1001659 |
  | Vranje    | 1187640 |

- **Alignment**: row `k` (the `k`-th hour of the year) is the data set's quarter hour at `k`:30 UTC.
- **Check**: `Temperature` and `Wind Speed` equal the data set's in all 8760 rows of every file.
- **Column**: `Relative Humidity`, percent, two decimals, appended after `Wind Speed` (or `Pressure`).

### Seville (`NSRDB/Seville/Seville.dat`)

Despite its folder, this file is not NSRDB data (its header says "TRY, mittleres Jahr, 2005-2020" and
names no data base). Its temperature, wind speed and wind direction are the typical meteorological year
of PVGIS 5.2 (European Commission JRC, SARAH-2 with ERA5, 2005 to 2020) at 37.37 N, 5.98 W:

- **Data**: `https://re.jrc.ec.europa.eu/api/v5_2/tmy?lat=37.37&lon=-5.98&outputformat=csv`, column `RH`
  (percent).
- **Alignment**: month by month. The months January, February, March, May, June and October sit on
  the TMY's own rows; April, July and November are the TMY's rows one day later, August, September
  and December one day earlier (the file was evidently assembled with these one-day shifts). Under that
  alignment 8473 of the 8760 rows equal the TMY's temperature to 0.01 K; the 281 rows that differ by
  more than 0.05 K are the hours around the seams between months, which the file interpolated across.
  Those rows take the TMY's humidity of the same hour.
- **Licence**: PVGIS data may be reused with the source acknowledged ("PVGIS (c) European Union,
  2001-2024"); https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/pvgis-data-download_en.

**Licence (both NSRDB sets)**: NSRDB data are published by NREL under the Creative Commons Attribution
3.0 United States licence (the licence the original readme of these files named). Cite: Sengupta, M.,
Y. Xie, A. Lopez, A. Habte, G. Maclaurin, and J. Shelby (2018), "The National Solar Radiation Data Base
(NSRDB)", Renewable and Sustainable Energy Reviews 89, 51-60.

## DWD ten-minute files (`DWD_10MIN`, `dwd_10min_data/*.csv`, 2 files)

- **Source**: Deutscher Wetterdienst, Climate Data Center, ten-minute air temperature product of the
  station observations, the product the files' temperature and pressure come from:
  `https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes/air_temperature/historical/10minutenwerte_TU_<station>_20200101_20251231_hist.zip`
  (station 02110: `..._20200101_20241202_hist.zip`). The humidity is its `RF_10` (relative humidity at
  2 m, percent); times are UTC.
- **Method**: the files were made by the retired `weather_data_import.dwd_10min_request` (moved out of this repository in #596), which
  combines the stations within its search radius by inverse-distance weighting. Its stations and weights
  were recovered from the files themselves: the temperature column equals, in every row, the weighted
  mean of `TT_10` of stations 15000 (Aachen-Orsbach), 03591 (Nideggen-Schmidt) and 02110
  (Heinsberg-Schleiden), weighted by `1 / sqrt(d)` with `d` the distance in degrees from (50.775, 6.083),
  a station without a value left out; and the pressure equals `PP_10` of station 15000. The humidity is
  the same weighted mean of `RF_10`. No row lacks all three stations.
- **Column**: `relative_humidity`, percent, rounded to two decimals.

## DWD quarter-hourly file (`DWD_15MIN`, `dwd_15min_data/dwd_15min_2024_Duesseldorf_6.7686_51.2960.csv`)

- **Source**: the same DWD ten-minute air temperature product, station 01078 (Düsseldorf, the coordinates
  of the file name) and, for its gaps, 01303 (Essen-Bredeney, the next station).
- **Method**, recovered from the file the same way: each station's ten-minute series is interpolated
  linearly onto the quarter hours (a quarter hour between two ten-minute values is their mean); a quarter
  hour next to a gap of 01078 takes the value of 01303. This reproduces the file's temperature and
  pressure in every row. The humidity is `RF_10` treated the same way; 214 quarter hours come from 01303.
- **Column**: `relative_humidity`, percent, two decimals.

**Licence (both DWD sets)**: DWD open data, free to use with the source named ("Deutscher
Wetterdienst"); terms of use: https://www.dwd.de/EN/service/copyright/copyright_artikel.html.

## ERA5 (`ERA5`)

No ERA5 file ships, and the importer that wrote ERA5 exports was retired. The reader expects a
`relative_humidity` column in percent. ERA5 itself publishes the 2 m dew point temperature
(`2m_dewpoint_temperature`) rather than the relative humidity; an export computes the relative humidity
from it and the 2 m temperature, for example with the Magnus formula over water,
`RH = 100 * exp(17.625 * Td / (243.04 + Td)) / exp(17.625 * T / (243.04 + T))` with both temperatures in
degrees Celsius.

## File sizes

| Files                                                    | Before (bytes) | After (bytes) |
|----------------------------------------------------------|---------------:|--------------:|
| `NSRDB_15min/*/*.csv` (20 files)                         | 35 277 007     | 39 352 781    |
| `NSRDB/*/*.dat` (9 files)                                | 3 619 296      | 4 076 354     |
| `dwd_10min_data/dwd_10min_2021-2022_AACHEN_6.08_50.77.csv` | 3 625 859    | 3 934 943     |
| `dwd_10min_data/dwd_10min_2023-2024_Aachen_6.08_50.77.csv` | 3 641 523    | 3 950 419     |
| `dwd_15min_data/dwd_15min_2024_Duesseldorf_6.7686_51.2960.csv` | 1 657 790 | 1 836 838     |

In all, the weather inputs grow by about 5.1 MB (11 %).
