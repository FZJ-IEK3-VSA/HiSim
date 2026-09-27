# Weather year separate from the calendar year (renovisorissues #57, step 2)

Status: **decided by cmf on 2026-09-27**. Base: origin/main @ faa88f3b.

## Today
`SimulationParameters.__init__` (hisim/simulationparameters.py) sets `self.year = start_date.year`, which serves as both the calendar and the weather year.

`Weather.build_calculation_inputs` hands `year=C` to `WeatherSeriesInputs`. Every reader in `hisim/components/weather/calculation.py` stamps the file's rows onto C **by position** (`pd.date_range(f"{year}-01-01 …", periods=N)`):

| source | reader | anchor | data year in file | shipped |
|---|---|---|---|---|
| DWD_TRY | read_dwd_try_data | Berlin 00:30, 8760 h | none (MM/DD/HH, MEZ) | Aachen TRY, 15 regions |
| NSRDB | read_nsrdb_data | Berlin 00:30, 8760 h | Year 2019 (nominal: a typical year of 2017–2019) | 9 cities |
| NSRDB_15MIN | read_nsrdb_15min_data | UTC 00:00, 35040 | Year 2019 | 20 countries (RenoVisor) |
| DWD_10MIN | read_dwd_10min_data | UTC 00:00, 52560 | year 2021 / 2023 | Aachen (two files, each named for two years, each holding one) |
| DWD_15MIN | read_dwd_15min_data | UTC 00:00, run duration | year 2024 (leap) | Düsseldorf |
| ERA5 | read_era5_data | UTC 00:00, 8760 | year column | none shipped |

Nothing reads the data year. Sun position (`produce_weather_series`: pvlib `get_solarposition`, `get_extra_radiation`) is computed on the C-stamped index, i.e. on the calendar, and PV, the building and solar thermal read it. When C is a leap year, dates after 28 Feb drift by position and `interpolate` pads 31 Dec flat.

## Decisions (cmf, 2026-09-27)
1. **The parameter is `SimulationParameters.weather_year: Optional[int] = None`.** It covers the whole run and is also a `*.simulation.json/.yaml` key, which the loaders already accept through `SimulationParameters(**data)`. `self.year` stays C. Valid range: int, 1900–2100.
2. **Unset = today, byte-identical:** positional stamping, no check, no remap. A mismatch between the file's declared year and C is logged at INFO (not a WARNING). It is not noted in the realized record: the record is written before the weather is prepared, and it has no run-level notes. A file whose year cannot be read is, unset, a year unknown, never a failure. This closes hisim-9g2.1.
3. **When set:**
   - The file's rows of year W are selected. The run is refused if W is not in the file (the message names the years that are), and refused on a source with no data year (TRY: pick the file instead).
   - The direct normal irradiance is derived on the W-stamped frame.
   - The rows are then remapped W→C **by same date**, in the source's own fixed-offset clock (UTC, or MEZ = UTC+1 for the Berlin-anchored readers), never Berlin wall time.
   - A 365-day W onto a leap C: 29 Feb is a copy of 28 Feb. A leap W onto a 365-day C: 29 Feb is dropped. W = C is the identity.
   - `interpolate(…, C)` and the resampling are unchanged. The sun stays on C.
4. **Future climates are weather files** (a LocationEnum entry or `WeatherConfig.for_data_file`), never a weather_year value. Revisit a `climate_scenario` parameter when scenario files ship.
5. **Cache keys and records:**
   - `get_unique_key` gets `###weather=W` and `get_unique_key_as_list` a line, **only when set**.
   - `ParameterNormalisation.normalise` and `ParameterFileWriter` write `weather_year` only when set, and `ParameterFileName` gets a `_weatherW` suffix only when set.
   - `WeatherSeriesInputs` gets `weather_year: Optional[int]`.
   - The LPG, car and solar-thermal caches key on W when set.
   - Unset keys and records stay byte-identical.
6. **Economics (PR D):**
   - `EvaluationInputs.weather_year: Optional[int]` is serialized in economic_inputs.json (read with `.get`), filled from the repository value `weather_year_effective` that the weather component publishes: W if set; otherwise the file's single declared year; otherwise None.
   - The staged document echoes it when the stages carry it, else `simulation_year` as today (no change while unset).
   - The staged "same year across stages" check also covers weather_year.
7. **RenoVisor (PR E):** `renovisor/simulation.py` `SimulationSetup.YEAR` splits into `CALENDAR_YEAR = 2019` and `WEATHER_YEAR = 2019`. The numbers are identical; the records gain a `weather_year` line. There is no request key. Later we ask RenoVisor whether they want a calendar year (e.g. = plan_start_year) and a weather choice (typical / extreme / 2045).
8. **Order:** A and B now; C after hisim-9g2.4 (reuse its "resolve a date against the delivered frame" helper and its reader anchor tests; 9g2.4's leap refusal stays for the unset case only); then D and E.

## PRs
- **A, the parameter:**
  - `weather_year` in SimulationParameters, with validation, keys and records only when set.
  - If set, the weather component refuses by name until PR C lands.
  - Tests: .simulation.yaml round trip with and without the key; `get_unique_key()` pinned identical when unset; the realized record is identical when unset.
- **B, declared data years:**
  - `WeatherSourceFiles.data_years(source, path) -> Optional[FrozenSet[int]]`, reading the year column, None for TRY, cached per content hash.
  - `Weather.i_prepare_simulation` publishes `weather_year_effective` and logs the INFO mismatch note, plus a note in the record.
  - Tests: the data_years of every shipped file (NSRDB_15MIN {2019}, DWD_10MIN {2021}, DWD_15MIN {2024}, TRY None); the note is emitted; the series are unchanged. Closes hisim-9g2.1.
- **C, select and remap:**
  - Readers filter by year when W is set; `remap_to_calendar(raw, W, C, clock)`; the refusals.
  - Tests: W = C frame-equal to unset; 365→leap (29 Feb = 28 Feb, 31 Dec real); leap→365; no duplicate or missing hour around the DST dates of W and C; sun series equal for W ≠ C; cold and warm cache equal.
- **D, the economics echo:** as in decision 6.
- **E, RenoVisor internal 2019/2019:** as in decision 7.
- No golden moves while weather_year is unset.

## Side findings (to be filed as beads)
- `read_nsrdb_data` labels UTC NSRDB data as Europe/Berlin, probably a 1 h shift.
- `calculation.interpolate` pads 31 Dec 23:00 of the previous year with 0.0 in every column, a temperature of 0 °C included.
- `read_dwd_10min_data`: the file is named 2021-2022 but holds 365 days.
- `Weather.calc_sun_position` (weather.py ~458) looks unused.
