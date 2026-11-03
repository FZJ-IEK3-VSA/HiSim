# Provenance: `725650TYCST.epw`

Denver International Airport TMY3 weather file of the ANSI/ASHRAE Standard 140 Section 7
test cases, used by the HiSim building-model verification suite.

## Origin

- Software: "Building Energy Simulation Test - Generation Simulation and Reporting"
  (BESTEST-GSR), Copyright (c) 2026, Alliance for Energy Innovation, LLC.
- Repository: <https://github.com/NatLabRockies/BESTEST-GSR>, file
  `shared_resources/725650TYCST.epw`, branch `main`, head commit `80c3f2e5` (2026-06-09);
  the file was last changed in commit `4c616ae3` (2022-05-12). Downloaded 2026-11-02.
- Underlying data, from the file header: NREL TMY Data Set (2008), WMO 725650, period of
  record 1973-2005; station 39.83 N, 104.65 W, UTC-7, 1650 m.

## Integrity

- Committed unmodified: 1 611 228 bytes, 8 760 data rows, CRLF line endings
  (`.gitattributes` keeps `*.epw` free of line-ending normalisation).
- Git blob SHA-1 `2261f3445e7e7ecbc22160ab7bd00695978db923`, equal to the blob of the
  repository file; SHA-256 `1d0402144460a26265555a18a9cdfe4f0f7d9b4f57d6194847af7959b518571f`.
- Compared hour by hour with the normative `725650TY.CSV` of the ASHRAE 140-2023
  accompanying files on 2026-11-02: all 8 760 rows agree in date and hour and in dry-bulb
  and dew-point temperature, relative humidity, pressure, ETR, ETRN, GHI, DNI, DHI, wind
  direction and speed, total and opaque cloud cover.

## Licence

Not covered by HiSim's MIT licence. Redistributed under the BESTEST-GSR licence,
reproduced verbatim in `LICENSE.md` in this folder (blob SHA-1
`69accd1dfc2459acf66b5000b2f2aa11e5d67d4e`, equal to the repository file).
