# Lifecycle cost summary

Simulation year 2026, country DE, horizon 20 a, interest 3.0%, price basis 2026. Monetary values as `avg [min | max]` (cost_spec.md §3.9).

## Plausibility checks

| Status | Check | Value | Expected |
|---|---|---|---|
| OK | subjects sum to total (brownfield_gross) | delta 0.00 EUR | 0 |
| OK | subjects sum to total (brownfield_net) | delta 0.00 EUR | 0 |
| OK | subjects sum to total (financed_net) | delta 0.00 EUR | 0 |
| OK | subjects sum to total (landlord) | delta 0.00 EUR | 0 |
| OK | subjects sum to total (tenant) | delta 0.00 EUR | 0 |
| OK | residual value <= purchases (brownfield_gross) | 16,230 vs 50,423 EUR | residual below discounted purchases |
| OK | residual value <= purchases (brownfield_net) | 16,230 vs 50,423 EUR | residual below discounted purchases |
| OK | subsidies <= eligible basis (brownfield_net) | 14,366 vs 37,000 EUR | support below its cost basis |
| OK | residual value <= purchases (financed_net) | 16,230 vs 50,423 EUR | residual below discounted purchases |
| OK | subsidies <= eligible basis (financed_net) | 14,366 vs 37,000 EUR | support below its cost basis |
| OK | residual value <= purchases (landlord) | 16,230 vs 50,423 EUR | residual below discounted purchases |
| OK | subsidies <= eligible basis (landlord) | 14,366 vs 37,000 EUR | support below its cost basis |
| OK | effective ELECTRICITY price (year 1) | 0.351 EUR/kWh | 0.1 - 0.6 EUR/kWh |
| OK | equivalent annual cost per m2 (brownfield_gross) | 16.940 EUR/m2a | 5 - 80 EUR/m2a |
| OK | levelized cost of heat | 0.169 EUR/kWh | 0.05 - 0.5 EUR/kWh |
| OK | maintenance / investment NPV ratio (brownfield_gross) | 0.099  | 0.02 - 0.8  |
| WARN(!) | uncertainty band width max/min (brownfield_gross) | 13.822 x | 1 - 3.5 x |

- **uncertainty band width max/min (brownfield_gross)**: over 20 years; very wide bands usually mean a band typo in the data

## Perspectives

| Perspective | NPV | Equivalent annual cost | Monthly (year 1) | LCOH |
|---|---|---|---|---|
| brownfield_gross | 37,803 [5,596 | 77,346] EUR | 2,541 [376 | 5,199] EUR/a | -728 [-1,023 | -464] EUR/mo | 0.17 [0.03 | 0.35] EUR/kWh |
| brownfield_net | 23,437 [-12,948 | 66,118] EUR | 1,575 [-870 | 4,444] EUR/a | -850 [-1,178 | -553] EUR/mo | 0.11 [-0.06 | 0.30] EUR/kWh |
| financed_net | 23,918 [-27,323 | 81,463] EUR | 1,608 [-1,837 | 5,476] EUR/a | -712 [-1,102 | -351] EUR/mo | 0.11 [-0.12 | 0.37] EUR/kWh |
| landlord | -27,168 [-77,682 | 19,287] EUR | -1,826 [-5,221 | 1,296] EUR/a | -1,102 [-1,515 | -775] EUR/mo | -0.12 [-0.35 | 0.09] EUR/kWh |
| tenant | 50,606 [31,187 | 80,378] EUR | 3,401 [2,096 | 5,403] EUR/a | 252 [149 | 410] EUR/mo | 0.23 [0.14 | 0.36] EUR/kWh |

## Cost structure (brownfield_gross)

| Display group | NPV |
|---|---|
| Investment & financing | 37,000 [28,200 | 47,400] EUR |
| Feed-in revenue | -2,790 [-3,161 | -2,232] EUR |
| Residual value & anyway credit | -47,163 [-60,388 | -35,775] EUR |
| Replacements | 13,423 [10,738 | 17,450] EUR |
| Energy | 32,333 [27,937 | 38,928] EUR |
| Maintenance & operation | 4,999 [2,269 | 11,576] EUR |

## Per subject (brownfield_gross)

| Subject | NPV | Year-0 investment | Subsidies |
|---|---|---|---|
| HeatPump | 12,092 [-2,182 | 30,449] EUR | 16,000 [12,800 | 20,800] EUR | -0.00 EUR |
| Envelope.Windows | -3,833 [-16,998 | 10,201] EUR | 21,000 [15,400 | 26,600] EUR | -0.00 EUR |
| ELECTRICITY | 32,333 [27,937 | 38,928] EUR | 0.00 EUR | -0.00 EUR |
| ELECTRICITY_FEED_IN | -2,790 [-3,161 | -2,232] EUR | 0.00 EUR | -0.00 EUR |

## Subsidy decisions

- **HeatPump** (all perspectives with subsidy decisions): applied DE_BEG_EM_HP_BASE_2024 (4,800 [3,840 | 6,240] EUR), DE_BEG_EM_HP_INCOME_2024 (4,800 [3,840 | 6,240] EUR), DE_BEG_EM_HP_EFFICIENCY_2024 (800 [640 | 1,040] EUR)
  - undetermined DE_BEG_EM_HP_SPEED_2024 (missing: building.existing_heating.energy_carrier, building.existing_heating.is_functional)
  - answering the open questions could unlock up to 800 EUR
- **Envelope.Windows** (all perspectives with subsidy decisions): applied none
  - undetermined DE_BEG_EM_ENVELOPE_2024 (missing: measure.technical_attributes.u_value)
  - undetermined DE_BEG_EM_ENVELOPE_ISFP_2024 (missing: building.has_isfp)
  - answering the open questions could unlock up to 234 EUR

## Variant comparison (brownfield_net)

- NPV delta (variant - reference): -45,064 [-50,684 | -39,632] EUR
- Equivalent annual cost delta: -3,029 [-3,407 | -2,664] EUR/a
- Discounted payback [a]: best 1, expected 2, worst 4 (None = never within horizon)

| Subject | NPV delta |
|---|---|
| ELECTRICITY | -55,591 [-66,014 | -48,642] EUR |
| Envelope.Windows | 0.00 EUR |
| ELECTRICITY_FEED_IN | 0.00 EUR |
| HeatPump | 10,527 [-2,042 | 26,382] EUR |

_Generated <DATE> by hisim.economics; trace any value with `python -m hisim.economics explain`._
