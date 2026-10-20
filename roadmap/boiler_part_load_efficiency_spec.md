# Spec: the boiler's combustion efficiency follows a sourced part-load curve per boiler type

Status: implemented on branch `boiler-part-load-efficiency-curve` (2026-10-20). Owner: Noah Pflugradt. Bead:
hisim-component-physics-4g9.66 (epic hisim-component-physics-4g9). Closes hisim-renovisor-translator-epc.29. Unblocks
part 2 of hisim-ef6u (the boiler burns at the efficiency of its part-load power).

## 1. Problem

`BurnerModulation.efficiency_at` made the combustion efficiency rise linearly with burner power, from
`eff_th_min = 0.60` at the minimal to `eff_th_max = 0.90` at the maximal power, for every boiler type, with no source.
A condensing gas boiler therefore ran its space heating at 0.73 (gas twin, 60 s golden) where its Ecodesign fiche
gives 0.88 to 0.98 on the gross calorific value, and firing part of a step at full power burnt 9 to 19 % less gas than
modulating for the same heat. RenoVisor runs this baseline for every boiler house and wrote a stated seasonal
efficiency onto the two ends of that line (`eff_th_max = value/100`, `eff_th_min = eff_th_max − 0.3`).

## 2. Decisions

| Date | Who | Decision |
|---|---|---|
| 2026-10-15 | owner | P1; built after the open PR stack merged. |
| 2026-10-20 | owner | Q1: one optional `seasonal_efficiency` replaces `eff_th_max` / `eff_th_min`. It is the Ecodesign seasonal space heating efficiency `η_s` of the energy label (813/2013 for gas and oil, 2015/1189 for solid fuel), on the gross calorific value, after the corrections the regulations subtract. When stated, the type's curve is scaled so that `0.85 η_30 + 0.15 η_100` minus those corrections reproduces it; when absent, the curve applies unscaled. |
| 2026-10-20 | owner | Q2 follows from Q1: the stated figure is on the gross calorific value (the label's basis). |
| 2026-10-20 | owner | Curve shape for gas and oil: IWU 2002 Tab. 2 / Tab. 3 by type and decade, eq. 2-1 for the size; the standby loss of eq. 2-2 only where the model has one (it has none). Pellets and wood chips keep the draft's product points. The condensing return-temperature gain stays (BRE S10TP-12). |
| 2026-10-20 | owner | Q3: hydrogen takes the gas condensing row, a stated estimate. |
| 2026-10-20 | owner | Q4: the decade row follows the request's `installation_year` when given. |
| 2026-10-20 | owner | Legal floor as a test: Council Directive 92/42/EEC Art. 5. |
| 2026-10-20 | owner | A: the 92/42 floor test checks only the 1990s rows (and current products), with a tolerance of 0.005: IWU prints two decimals and Art. 5(2) asks for "appropriate tolerances". Source values stay untouched. |
| 2026-10-20 | owner | B: scaling to a stated `η_s` adds back only F(1) = 3 points; F(2) to F(4) are left out because HiSim models none of those losses. The resulting bias is stated (§4.5). |
| 2026-10-20 | owner | C: a boiler with no installation year, or one from 2000 on, takes current product data (Vitodens for condensing gas, the same row on the GCV for hydrogen; a condensing-oil and a low-temperature datasheet sourced the same way). IWU's decade rows apply only to a stated year before 2000. |

## 3. Terms

- **GCV / NCV**: gross and net calorific value (Brennwert Hs / Heizwert Hi). An efficiency on the NCV is the one on
  the GCV times the fuel's GCV/NCV.
- **Combustion efficiency** `η`: heat into the water over fuel power, on the calorific basis the boiler's fuel is
  booked on (§4.1).
- **Load ratio** `β`: burner (fuel) power over maximal burner power. The standards' 30 % point is 30 % of the rated
  heat output; the two ratios differ by `η_30 / η_100`, at most 11 %.
- **Test points**: `η_100` at full load and a 60 °C return (80/60 °C, 813/2013 Annex II Table 1 footnote; the 70 °C
  mean boiler temperature of 92/42 and IWU Tab. 2), `η_30` at 30 % load and a 30 °C return for a condensing boiler
  (same footnote; IWU Tab. 3 states a 30 °C mean boiler temperature, read as the return).
- **Active-mode seasonal efficiency** `η_son = 0.85 η_30 + 0.15 η_100`; the label's `η_s = η_son − ΣF(i)`.

## 4. Design

### 4.1 Calorific basis

HiSim books a boiler's fuel in kWh of burner power and turns them into litres or m³ with
`GenericBoilerConfig.heating_value_in_joule_per_m3`: the higher heating value for a condensing, the lower for a
conventional boiler. Pellets and wood chips have only an NCV in `PhysicsConfig`, so they are booked on the NCV. Each
curve is therefore put on its boiler's booking basis (`CalorificBasis.of_booking`): condensing gas, oil and hydrogen
on the GCV, every other boiler on the NCV.

GCV/NCV ratios (`BoilerEfficiencySources.gross_over_net`):

| Fuel | GCV/NCV | Source |
|---|---|---|
| Natural gas | 39.819 / 35.895 = 1.109 | `PhysicsConfig` |
| Heating oil | 37.682 / 35.358 = 1.066 | `PhysicsConfig` |
| Hydrogen | 12.745 / 10.783 = 1.182 (not used: hydrogen takes the gas curve as it is) | `PhysicsConfig` |
| Wood pellets | 18.89 / 17.52 = 1.078 | TÜV SÜD report 2212100-1, A1 test pellets |
| Wood chips, 15 % water | 17 000 / 15 522 = 1.095 | **stated estimate**: EN ISO 18125 Annex E with q_gr,d = 20 000 J/g, H_d = 6.0 %, O_d + N_d = 43.2 %, M = 15 %; the same inputs give UBA's 15.6 MJ/kg within 0.5 % |

### 4.2 Sources: the test points per type and vintage

Vintage (`BoilerVintage.of_installation_year`): before 1980 → 1970s row, 1980–1989 → 1980s, 1990–1999 → 1990s, no year
or 2000 on → current product. HiSim's conventional boiler is IWU's low-temperature boiler in the 1980s and 1990s rows
and its standard boiler ("Spezialkessel") in the 1970s, the only conventional row of that decade. A condensing boiler
has no 1970s row and takes the 1980s one. Pellets and wood chips ignore the year (IWU's solid-fuel rows are log
boilers).

| Boiler | Vintage | `η_100` | `η_30` | Basis | Source |
|---|---|---|---|---|---|
| Gas condensing (hydrogen likewise) | current | 0.877 | 0.978 | GCV | Viessmann Vitodens 200-W B2HF-11 (10 kW), product fiche to (EU) 811/2013 and 813/2013, document 5593552-01: η4 = 87.7 %, η1 = 97.8 % |
| Gas condensing | 1990s / 1980s | 0.92 / 0.89 | 1.01 / 0.97 | NCV, 20 kW | IWU 2002 Tab. 2 "Gas-Niedertemperatur-/Brennwertkessel" `η_k,70`; Tab. 3 "Gas-Brennwertkessel" `η_30%` at 30 °C |
| Oil condensing | current | 0.910 | 0.975 | GCV | Buderus Logano plus GB125-22 (22 kW, article 7736604146), product fiche to 811/2013 and 813/2013, document 6721825297 (2020/10), https://assets.heizung-billiger.de/documents/7736604146.pdf |
| Oil condensing | 1990s / 1980s | 0.92 / 0.89 | 0.97 / 0.94 | NCV, 20 kW | IWU 2002 Tab. 2 "Öl-Niedertemperaturkessel/Brennwertkessel"; Tab. 3 "Öl-Brennwertkessel" at 30 °C |
| Gas or oil conventional | current | 0.937 | 0.960 | NCV | Buderus Logano G125-21 (oil low-temperature boiler), Planungsunterlage Logano G125 (Ausgabe 08/2004), section 1.4 (DIN V 4701-10 values), https://www.intec-heizung.de/media/pdf/37/11/ff/Buderus-Logano-G125-G125-RLU-G125-WT-Planungsunterlage.pdf. **No Ecodesign fiche exists**: 813/2013 bars non-condensing boilers up to 400 kW from sale for space heating (Viessmann's own Vitoplex 200 datasheet 5796300, 4/2022, says so), so this is the newest household low-temperature data found. Gas uses it too, as IWU gives gas and oil low-temperature boilers equal rows. |
| Gas or oil conventional | 1990s / 1980s | 0.92 / 0.89 | 0.92 / 0.88 | NCV, 20 kW | IWU 2002 Tab. 2 / Tab. 3 "Gas-/Öl-Niedertemperaturkessel" (Tab. 3 at 40 °C) |
| Gas conventional | 1970s | 0.85 | 0.80 | NCV, 20 kW | IWU 2002 Tab. 2 / Tab. 3 "Gas-Spezialkessel" (Tab. 3 at 50 °C) |
| Oil conventional | 1970s | 0.84 | 0.79 | NCV, 20 kW | IWU 2002 Tab. 2 / Tab. 3 "Öl-Spezialkessel" |
| Pellets | all | 0.878 | 0.845 | GCV | Fröling PE1 Pellet 15, "Produktdaten PE1 Pellet" T6390021_de, data to (EU) 2015/1189: ηn, ηp; on the NCV 0.947 / 0.911 |
| Wood chips | all | 0.939 | 0.914 | NCV | Hargassner Eco-HK 20 (6–20 kW), brochure "Hackgut" 2020: "Wirkungsgrad Volllast / Teillast 93,9 / 91,4 %" |

IWU 2002: Diefenbach, Loga, Born, Großklos, Herbert, "Energetische Kenngrößen für Heizungsanlagen im Bestand",
Institut Wohnen und Umwelt, Darmstadt, 1 November 2002, pages 4–5,
https://www.iwu.de/fileadmin/publikationen/energie/werkzeuge/2002_IWU_DiefenbachEtAl_Energetische-Kenngr%C3%B6%C3%9Fen-f%C3%BCr-Heizungsanlagen-im_Bestand.pdf.
Its size law, eq. 2-1: `η = η_20kW − 0.0195 + 0.015 lg P_n` (P_n in kW), applied to both points ("setze η30% statt
ηK,70"). `P_n` is HiSim's `maximal_thermal_power_in_watt`, the maximal burner (fuel) power (hisim-component-physics-4g9.29);
the rated heat output is about 8 % lower, which moves the correction by 0.0005. Product rows do not follow the size law.
Eq. 2-2 (standby loss `q_B,70`) is not used: the boiler model has no standby loss.

### 4.3 The law (`CombustionEfficiencyCurve.efficiency_at`)

With `β` limited to `[0.3, 1]` and the return `T` to `[30, 60]` °C:

```
η(β, T) = η_100 + (η_30 − η_100 − g · 30 K) · (1 − β) / 0.7 + g · (60 °C − T)
```

- `g` = 0.0030 per kelvin on the GCV for a condensing boiler (BRE, SAP 10 Technical Paper S10TP-12 issue 1.2, 2023,
  §4.5: theoretical maximum gross efficiency 97.3 % at a 30 °C and 88.3 % at a 60 °C return), 0 for the others (the
  same paper: "typically 1 % or below" for a non-condensing boiler).
- The curve passes through both test points. Below 30 % load it keeps the 30 % value (no lower test point; cycling
  losses are hisim-3y10). Outside the two test returns the gain stays constant.
- Worked example, current gas condensing at half load and a 40 °C return:
  `0.877 + 0.011 · 0.5 / 0.7 + 0.0030 · 20 = 0.945`.
- The boiler reads its own return temperature, `WaterInputTemperatureSh` or `WaterInputTemperatureDhw` of the circuit
  it fires: no other component's state.

### 4.4 Where it lives

`hisim/components/generic_boiler.py`: `CalorificBasis`, `BoilerVintage` with `InstallationYearBands`,
`EfficiencyTestPoints` (one source row), `CombustionEfficiencyCurve` (the law, the Ecodesign weighting, the scaling),
`BoilerEfficiencySources` (every row as a class constant with its source, the ratios, eq. 2-1, F(1), and
`curve(...)` that picks, sizes, converts and scales). `BurnerModulation` holds the curve; `GenericBoiler.build` builds
it from the config and sizes the pump on `P_max · η_100` (at the 60 °C test return).

### 4.5 The stated seasonal efficiency

`GenericBoilerConfig.seasonal_efficiency` (optional, a fraction): the curve is scaled by
`s = (η_s + 0.03) · k / (0.85 η_30 + 0.15 η_100)`, with `k` the fuel's GCV/NCV for a boiler booked on the NCV and 1
otherwise. The config refuses a value that is not finite or not in (0, 0.97] (`η_son ≤ 1` on the GCV); the build
refuses a scaled curve above 1 anywhere on the GCV.

F(1) = 3 % is the correction for temperature controls (Commission communication 2014/C 207/02, point 4.4(a), for
813/2013; Regulation (EU) 2015/1189, Annex IV, point 4(a)). F(2) auxiliary electricity
(`2.5 (0.15 elmax + 0.85 elmin + 1.3 P_SB) / (0.15 P4 + 0.85 P1)`), F(3) standby loss (`0.5 Pstby / P4`) and F(4)
ignition burner are left out (decision B). **Bias**: the curve's `η_son` is too low by F(2) + F(3) + F(4), so a
stated label figure makes HiSim burn that much more fuel. On product fiches to 811/2013 and 813/2013: Hoval UltraGas
20 (gas condensing, data sheet 7013255, 2020-04-02) F(2) + F(3) = 0.95 + 0.24 = 1.2 points; Buderus GB125-22 (oil
condensing, 6721825297) 2.94 + 0.28 = 3.2 points; Hoval MultiJet 12 (oil condensing, data sheet 7013576, 2019-03-29,
https://cdn.hoval.com/7013576-h1000-0003-datbl-hybris-original.pdf) 3.54 + 0.67 = 4.2 points. Each fiche's η4, η1 and corrections reproduce its stated η_s, which checks
the formulas. The owner expected 0.5 to 1 point; for oil it is 3 to 4.

### 4.6 RenoVisor

- `seasonal_efficiency_in_percent / 100` → `GenericBoiler.config.seasonal_efficiency` (reported `approximated`, the
  note naming the left-out corrections). The contract row (calculation-request.md §3.6) must change before merge:
  draft in `review-evals/renovisor_contract_seasonal_efficiency_draft.md` (outside the repository).
- `house.heating.installation_year` of the existing boiler → `installation_year`. Its report line stays the economics
  stage's, its note says that it also picks the efficiency row. A `heating_system` measure removes the year with the
  generator, so a new boiler is a current one.
- A request stating more than 97 % for a boiler is refused (`range.exceeded`, `SemanticChecks._boiler_seasonal_efficiency`):
  no label `η_s` exceeds it, and a figure like 104 % is on the NCV. Before, 400 % ran as `eff_th_max = 4.0`, a boiler
  that delivered four times its fuel. The capability probe at 400 % on the gas-boiler base is now refused; the
  published bounds (the schema's 1–400) are unchanged, because the heat-pump houses still list any value.

### 4.7 Relation to hisim-component-physics-4g9.29

4g9.29 (the power field is the fuel input, so a boiler sized to the load delivers `η · load`) is not touched and need
not come first: the curve is defined on the load ratio, which is the same whichever power the field holds. When
4g9.29 makes the field the rated heat output, the maximal fuel power becomes `P_rated / η_100` with this curve's
`η_100`, and eq. 2-1's `P_n` becomes exact.

## 5. What changes

- **Results**: every run with a `GenericBoiler` moves (§7). Goldens that move: every pair of
  `household_gas_building_sizer`, `household_oil_building_sizer`, `household_pellets_building_sizer`,
  `household_wood_chips_building_sizer`, `household_hydrogen_boiler_building_sizer`, `household_gas_solar_thermal`,
  `household_gas_solar_thermal_building_sizer` and `gas_boiler_household` (`one_week_60s`, `full_year_60s`), and the
  economics report goldens that read boiler fuel. Every RenoVisor boiler result moves.
- **Interfaces**: `GenericBoilerConfig.eff_th_min` / `eff_th_max` are gone; `seasonal_efficiency` and
  `installation_year` are new (energy-system schema regenerated). `GenericBoiler.combustion_efficiency_at_burner_power`
  is gone (no caller). `BurnerModulation.efficiency_at` and `heat_power_in_watt` take the return temperature.
- **RenoVisor**: §4.6; `HeatGenerator.boilers()` is the one list of boiler generators (`BaseFiles.BOILERS` reads it).

## 6. Verification

- `tests/test_boiler_efficiency_curve.py`: every source row is reproduced at both test points (20 rows); the
  return-temperature gain and its limits; flat below 30 % load; eq. 2-1 for IWU rows only; the year bands; a stated
  `η_s` of 0.75 and 0.90 is reproduced by `0.85 η_30 + 0.15 η_100 − 0.03` on the GCV for every row, the shape kept;
  a scaled curve above 1 (GCV) refused; a fuel without a source refused; the 92/42 floor for the 1990s rows (tolerance
  0.005) and the current products (no tolerance) at 10, 20, 50, 100 and 400 kW.
- `tests/test_generic_boiler.py::test_a_condensing_gas_boiler_burns_at_its_ecodesign_test_points_not_on_the_old_linear_law`
  reads the efficiency off the boiler's published heat and fuel. On main it fails: 0.667 instead of 0.978 at 30 %
  load and a 30 °C return, 0.900 instead of 0.877 at full load and a 60 °C return.
- Tests that pinned the old law are rebased (`test_generic_boiler.py`, `test_energy_balance_generators.py`), the
  field-name examples moved to `seasonal_efficiency` (`test_energy_system_classes.py`, `test_energy_system_configure.py`,
  `test_presets.py`), RenoVisor: `test_translate.py`, `test_request.py`, `test_verify.py`.
- Finding for the owner: below about 8 kW the IWU 1990s gas condensing full-load point falls below the 92/42 floor by
  more than the 0.005 tolerance (at 4 kW: 0.9095 against 0.9160 on the NCV). The test checks 10 kW and up.

## 7. Measured results

Full year 2021, the five boiler twins (`system_setups/household_*_building_sizer.py`), main (`537f3d91`) against
this branch, at 60 s and 900 s. Fuel and heat in kWh of the boiler's booking basis (§4.1), space heating (SH) and hot
water (DHW) together; η is heat over fuel. No twin states a seasonal efficiency or a year, so every boiler is a current
one.

| Twin | Step | Fuel before (kWh) | Fuel after (kWh) | Δ fuel | Heat before | Heat after | η before (SH / DHW) | η after (SH / DHW) |
|---|---|---|---|---|---|---|---|---|
| gas | 60 s | 18,887 | 15,043 | -20.3 % | 14,380 | 14,393 | 0.761 (0.728 / 0.900) | 0.957 (0.977 / 0.896) |
| oil | 60 s | 18,887 | 15,111 | -20.0 % | 14,380 | 14,393 | 0.761 (0.728 / 0.900) | 0.953 (0.957 / 0.937) |
| pellets | 60 s | 16,337 | 15,556 | -4.8 % | 14,702 | 14,726 | 0.900 (0.900 / 0.900) | 0.947 (0.947 / 0.947) |
| wood_chips | 60 s | 16,636 | 15,919 | -4.3 % | 14,924 | 14,943 | 0.897 (0.896 / 0.900) | 0.939 (0.939 / 0.939) |
| hydrogen_boiler | 60 s | 18,887 | 15,043 | -20.3 % | 14,380 | 14,393 | 0.761 (0.728 / 0.900) | 0.957 (0.977 / 0.896) |
| gas | 900 s | 17,385 | 15,163 | -12.8 % | 14,380 | 14,405 | 0.827 (0.808 / 0.900) | 0.950 (0.967 / 0.896) |
| oil | 900 s | 17,385 | 15,326 | -11.8 % | 14,380 | 14,402 | 0.827 (0.808 / 0.900) | 0.940 (0.940 / 0.937) |
| pellets | 900 s | 16,178 | 15,383 | -4.9 % | 14,560 | 14,562 | 0.900 (0.900 / 0.900) | 0.947 (0.947 / 0.947) |
| wood_chips | 900 s | 16,418 | 15,739 | -4.1 % | 14,762 | 14,778 | 0.899 (0.899 / 0.900) | 0.939 (0.939 / 0.939) |
| hydrogen_boiler | 900 s | 17,385 | 15,163 | -12.8 % | 14,380 | 14,405 | 0.827 (0.808 / 0.900) | 0.950 (0.967 / 0.896) |

Share of the fuel and mean efficiency per load band (burner power / maximal power, while firing):

| Twin | Step | Run | <0.3 | 0.3-0.5 | 0.5-0.7 | 0.7-0.9 | 0.9-1 |
|---|---|---|---|---|---|---|---|
| gas | 60 s | before | 16 % @ 0.663 | 42 % @ 0.722 | 19 % @ 0.770 | 1 % @ 0.833 | 22 % @ 0.900 |
| gas | 60 s | after | 32 % @ 0.978 | 36 % @ 0.977 | 4 % @ 0.974 | 1 % @ 0.970 | 27 % @ 0.902 |
| oil | 60 s | before | 16 % @ 0.663 | 42 % @ 0.722 | 19 % @ 0.770 | 1 % @ 0.833 | 22 % @ 0.900 |
| oil | 60 s | after | 32 % @ 0.960 | 37 % @ 0.957 | 5 % @ 0.952 | 1 % @ 0.944 | 26 % @ 0.937 |
| pellets | 60 s | before | 0 % @ 0.600 | – | – | – | 100 % @ 0.900 |
| pellets | 60 s | after | 0 % @ 0.911 | – | – | – | 100 % @ 0.947 |
| wood_chips | 60 s | before | 1 % @ 0.600 | – | – | – | 99 % @ 0.900 |
| wood_chips | 60 s | after | 1 % @ 0.914 | – | – | – | 99 % @ 0.939 |
| hydrogen_boiler | 60 s | before | 16 % @ 0.663 | 42 % @ 0.722 | 19 % @ 0.770 | 1 % @ 0.833 | 22 % @ 0.900 |
| hydrogen_boiler | 60 s | after | 32 % @ 0.978 | 36 % @ 0.977 | 4 % @ 0.974 | 1 % @ 0.970 | 27 % @ 0.902 |
| gas | 900 s | before | 8 % @ 0.667 | 22 % @ 0.720 | 10 % @ 0.775 | 23 % @ 0.885 | 38 % @ 0.899 |
| gas | 900 s | after | 3 % @ 0.977 | 3 % @ 0.976 | 3 % @ 0.973 | 22 % @ 0.904 | 70 % @ 0.961 |
| oil | 900 s | before | 8 % @ 0.667 | 22 % @ 0.720 | 10 % @ 0.775 | 23 % @ 0.885 | 38 % @ 0.899 |
| oil | 900 s | after | 4 % @ 0.960 | 5 % @ 0.957 | 5 % @ 0.950 | 23 % @ 0.938 | 64 % @ 0.937 |
| pellets | 900 s | before | – | – | – | 18 % @ 0.900 | 82 % @ 0.900 |
| pellets | 900 s | after | 0 % @ 0.911 | – | – | 19 % @ 0.947 | 81 % @ 0.947 |
| wood_chips | 900 s | before | 0 % @ 0.600 | – | – | 18 % @ 0.900 | 82 % @ 0.900 |
| wood_chips | 900 s | after | 0 % @ 0.914 | – | – | 18 % @ 0.939 | 81 % @ 0.939 |
| hydrogen_boiler | 900 s | before | 8 % @ 0.667 | 22 % @ 0.720 | 10 % @ 0.775 | 23 % @ 0.885 | 38 % @ 0.899 |
| hydrogen_boiler | 900 s | after | 3 % @ 0.977 | 3 % @ 0.976 | 3 % @ 0.973 | 22 % @ 0.904 | 70 % @ 0.961 |

The 900 s fuel against the 60 s fuel: gas and hydrogen +0.8 % (before −8.0 %), oil +1.4 % (before −8.0 %), pellets
−1.1 % (before −1.0 %), wood chips −1.1 % (before −1.3 %). The step-length gap of the old law (firing a fraction of a
step at full power burnt less fuel than modulating) is gone; oil's 1.4 % is slightly above the modelling guide's 1 %.

Hydrogen equals gas by decision (Q3). Oil and gas were equal before because they shared the law and the defaults; the
oil twin is now a current low-temperature boiler (Logano G125, NCV) and the gas twin a condensing one (GCV). Gas DHW
stays near 0.90: the hot-water charge runs at full load on a 45–60 °C return, close to the full-load test point.

RenoVisor, one request through `python -m hisim.renovisor run` (full year): the mockup house (IE, 300 m²) with no
measures, a condensing gas boiler stating `seasonal_efficiency_in_percent: 90` and `installation_year: 2008`. Before,
the translator wrote `eff_th_max 0.9`, `eff_th_min 0.6`; after, `seasonal_efficiency 0.9`, `installation_year 2008`.

| | Gas (kWh) | Heat (kWh) | η (SH / DHW) | `energy_demand_in_kilowatt_hour_per_year` | CO2 (kg/a) |
|---|---|---|---|---|---|
| before | 54,093 | 43,545 | 0.805 (0.798 / 0.900) | 57,105 | 11,651 |
| after | 48,945 | 43,560 | 0.890 (0.892 / 0.863) | 51,953 | 10,621 |

−9.5 % gas. The stated 90 % now scales the current condensing curve by 0.966 (`η_son` 0.93 on the GCV).

## 8. Beads

| Bead | Content |
|---|---|
| hisim-component-physics-4g9.66 | this spec; closed with the branch |
| hisim-renovisor-translator-epc.29 | the `eff_th_min > eff_th_max` inversion; gone with the single field, closed |
| hisim-ef6u | part 2 (boiler efficiency at the part-load power) is unblocked |
| hisim-component-physics-4g9.29 | the power field is the fuel input; §4.7 |
| hisim-3y10 | cycling losses below 30 % load, on top of this law |
| hisim-hydrogen-boiler-curve-d6hc (new, P4) | hydrogen takes the gas curve as an estimate; replace it when a hydrogen boiler's fiche or test exists |
| hisim-boiler-auxiliary-losses-pbda (new, P3) | the boiler has no auxiliary electricity and no standby loss; a stated `η_s` adds back only F(1), a bias of 1 (gas) to 4 (oil) points of fuel |
| hisim-boiler-floor-small-vgpn (new, P3, owner-decision) | below about 8 kW the IWU 1990s gas condensing full-load point is below the 92/42 floor by more than the tolerance |
