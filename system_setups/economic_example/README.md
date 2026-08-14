# Economic examples: the lifecycle cost engine end to end

Worked, reproducible examples of the lifecycle cost engine (`hisim/economics/`, see its
README and `cost_spec.md`). Two scripts:

| Script | What it shows |
|---|---|
| `economic_example_heatpump.py` | **Germany**: heat-pump + envelope retrofit of a 1978 single-family house — all perspectives, BEG subsidy engine, three envelope measures, scenario tornado. |
| `economic_example_ireland_gas_to_heatpump.py` | **Ireland**: switching an assumed 1995 bungalow from its functional gas boiler to a heat pump — two simulations (keep-gas reference vs. heat-pump variant), Irish cost data and CO2 path, full variant comparison (delta waterfall, payback band). |

## Run them

```bash
python system_setups/economic_example/economic_example_heatpump.py
python system_setups/economic_example/economic_example_ireland_gas_to_heatpump.py
```

~1 minute per simulation with cached inputs (the Ireland example runs two). Results land in
`system_setups/results/<setup>/default_config/<run>/` — open **`lifecycle_report.html`**
first, then `cost_summary.md`. For the Ireland example the comparison lives in the
*variant's* (heat pump) result directory, section 8.

## What the example models

- **The house**: built 1978, 150 m² owner-occupied single-family home, functional gas boiler
  from 2011, windows from 1993, uninsulated facade/top ceiling from 1988 (the existing-asset
  register).
- **The retrofit**: the simulated heat pump + PV + battery system replaces the gas boiler,
  plus three envelope measures — 140 m² external wall insulation (U 0.18), 90 m² top-ceiling
  insulation (U 0.13), 28 m² triple-glazed windows (U 0.90) — priced with banded AI-estimate
  costs from the 2026 database entries.
- **Subsidies (Germany)**: the BEG EM heat-pump schemes (30 % base + 20 % speed bonus for the
  functional fossil boiler + 30 % income bonus at 38 k€ income + 5 % efficiency bonus for the
  R290 unit, capped at 70 % of 30 k€ eligible cost) and the envelope schemes (15 % + 5 % iSFP
  bonus), with the §35c tax credit correctly excluded. The report's subsidy cards show the
  full solver audit trail.
- **All perspectives** (cost_spec.md §7.1): brownfield gross/net, operating (with replacement
  reserve), owner_monthly (financed), landlord, tenant (DE_2024 allocation with modernization
  levy), macroeconomic. Section 6b of the report shows who pays what.
- **Scenario analysis** (§4.6): interest 1 %/5 %, flat vs. high electricity escalation, a 30 %
  cheaper heat pump via data overlay, and the high CO2-price path — rendered as a tornado in
  section 9 and exported to `scenario_cube.csv`/`.json`.

## The two clocks

`WEATHER_YEAR = 2021` is the simulated physics (weather, load profiles);
`PRICE_BASIS_YEAR = 2026` is the economic "today" — it selects the banded cost data, the
valid subsidy schemes, the CO2-path anchor and the asset ages. Change the price basis to 2035
to see the learning-curve data instead.

## Reproducibility

Everything the evaluation used is stored next to the results: `economic_inputs.json`
(re-price offline with `python -m hisim.economics evaluate <dir> --scenarios ...`),
`cost_provenance.json` (trace any number with `python -m hisim.economics explain <dir>
--value "brownfield_net/total_npv_in_euro"`), and `cost_audit.csv` (one row per priced
subject with origins and sources).
