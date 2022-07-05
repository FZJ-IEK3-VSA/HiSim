# Electrical load profiles 

**Sources and Licenses**:

All sources are located in `data_raw`

- Electricity consumption of 28 German companies in 15-min resolution. <https://doi.org/10.5445/IR/1000098027> [![License: CC BY 4.0](https://img.shields.io/badge/License-CC%20BY%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by/4.0/)
- SCiBER: A new public data set of municipal building consumption. <https://doi.org/10.1145/3208903.3210281> [![License: CC BY 4.0](https://img.shields.io/badge/License-CC%20BY%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by/4.0/)
- Felduntersuchung zu Behavioral Energy Efficiency Potentialen von privaten Haushalten. <https://doi.org/10.5281/zenodo.3855575> [![License: CC BY 4.0](https://img.shields.io/badge/License-CC%20BY%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by/4.0/)
- Tjaden, Tjarko. (2021). Electrical load profile from a tool manufacturer [Data set]. Zenodo. <https://doi.org/10.5281/zenodo.4683455> [![License: CC BY 4.0](https://img.shields.io/badge/License-CC%20BY%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by/4.0/)
- Tjaden, Tjarko. (2021). Electrical load profile from an eletrocplating company [Data set]. Zenodo. <https://doi.org/10.5281/zenodo.4683479> [![License: CC BY 4.0](https://img.shields.io/badge/License-CC%20BY%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by/4.0/)
- Standard Load Profiles (SLP): https://www.bdew.de/energie/standardlastprofile-strom/ 

**Summary**:

Normalized electrical load profiles for several buildings. Based on the raw data, listed above, normalized (1000 kWh/a) reference profiles were created.

To determine reference profiles, several simulations with different pv generator sizes and with/without battery storage were conducted. The profile(s) with the lowest mean deviation in degree of self-sufficiency compared to the median results were chosen as reference profile.

For details have a look into the `process_data.ipynb` notebook.

**Content**:

See Table 6 in VDI 4657 Sheet 3:

| Abbreviation | Example | bandwidth of application <br /> in MWh/a |
| :--- | :--- | :--- |
| **Households** |
LP_W_W | Single flat | 0,5 - 2|
LP_W_EFH | Single family house | 2 - 8|
LP_W_MFH_ | Multi family house. <br /> small (k), medium (m), big (g)| k: 5- 15<br /> m: 15 - 45<br /> g: 45 - 100 |
*_WP025, *_WP050, *_WP075 | Addition for all household profiles starting with “LP_W”, where the electrical load profile then has a proportion of the electricity consumption for a heat pump of 25%, 50% or 75% | raised by energy consumption of heat pump |
**Municipal buildings** |
LP_Ö_Schule_ | Schools.<br /> small (k), medium (m), big (g)  | k: 20 - 100 <br /> m: 100 -300 <br /> g: 300 - 800 |
LP_Ö_Büro_ | Administration (weekdays) | k: 25 - 100 <br /> m: 100 - 300 |
**Commercial buildings** |
LP_G0 to G6 and <br /> LP_L0 to L2 | Standard load profiles | no limitation |
LP_G_MV | Manufacturing industry / metal processing | 250 - 1000 |
LP_G_G | Manufacturing / Electroplating | 250 - 1000 |
LP_G_MH | furniture shop | 175 - 725 |

