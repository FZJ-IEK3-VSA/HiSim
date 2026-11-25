# Spec: the hot-water cylinder is a stack of N mixed layers with real flows, explicit and start-of-step, at fine steps

Status: draft (owner decisions D1 to D4 of 2026-11-25 recorded; the open questions of §8 are put to the owner with
this document), nothing built. Owner: Noah Pflugradt. Epic: hisim-hydronics-fxix (bead hisim-hydronics-fxix.1; the
coarse model is hisim-stratified-storage-coarse-0392). Builds on: `roadmap/hydronic_coupling_spec.md` (§4.5, §5.3,
§9.5, D4, D5), `roadmap/hydronic_part_load_spec.md` (decisions 3, 7, 9, 16, 23), `roadmap/two_mode_buffer_spec.md`,
`roadmap/fluid_loops_spec.md` (#1122), the start-of-step node of #1009, and renovisorissues #94.

Code is cited against `origin/economics-ports-record` at 26f691ff1.

## 1. Problem

Two things are wrong with the water storages today, one in the results and one in the code.

**The results: a fully mixed cylinder gives a solar collector too little to do (renovisorissues #94).** The
hot-water cylinder `SimpleDHWStorage` is one fully mixed node (`hydronics.MixedNode`, hydronic stage C, #909): all
of its 250 l have one temperature. The backup (boiler, heat pump, electric heater, substation) charges it to 58 to
70 °C, and the collector's coil then meets a return of 55 to 70 °C whatever it does. In a real bivalent cylinder the
backup coil heats only the upper half, the solar coil sits in the lower third, and the lower third stands at mains
temperature after every draw, so the collector works against 10 to 40 °C. The triage of #94 on `origin/main`
c3d1935e2 (2026-11-10, Irish houses, 900 s, full year, 250 l cylinder, taps drawing 2,854 kWh/a) measured 99 to
171 kWh/(m² a) of collector heat, a 19 to 38 % solar share; a well-designed system gives 300 to 500 kWh/(m² a) and a
4 to 6 m² collector covers 50 to 60 % (the issue's figures). The remaining gap has two causes, written down in the
triage: the mixed cylinder (this spec) and the missing solar priority in the controllers (hisim-bq3h, a control
strategy the owner placed in the controllers on 2026-10-10).

**The code: the buffer's stratification path does not conserve energy (D5 of the hydronic coupling spec).** The
space-heating buffer `SimpleHotWaterStorage` keeps, under `heat_exchanger_is_present=False`, a path that mixes the
inflow masses into the vessel (`calculate_mean_water_temperature_in_water_storage`,
`simple_water_storage.py:713`), takes the outlets as a mix of the inlet and the mean by the factor `dt/3600`
(`calculate_mixing_factor_for_water_temperature_outputs`, `:740`) and subtracts the standby loss afterwards
(`simulate_stratification_path`, `:1836`). The mixing factor makes the outlet depend on the step length; the
inflow is booked on the start temperature while the outlet is a mix of two other temperatures, so the vessel's
balance does not close. Measured by hydronic stage E (bead hisim-hydronics-fxix.1, 2026-10-21): the gas twin with
the buffer's exchanger removed, 10 January 2021 at 60 s, has a residual of 34.3 kWh on 73.9 kWh of throughput
(46.4 %), and the energy-balance check fails the run on 1,439 of 1,440 steps. No recorded energy system uses the
path; only `tests/test_simple_hot_water_storage.py:71` builds it. D5 kept it until a stratified storage replaces it.

**What the replacement must respect.** The owner decided on 2026-11-25 (D1 to D4 below): no iteration and no time
sub-stepping inside a component; the fine model is the explicit start-of-step layer model recorded in bead
hisim-hydronics-fxix.1 on 2026-10-17, valid where the water a circuit moves in one step is small against one layer;
a coarse model for the steps above `SimulationParameters.part_load_above_seconds` is a separate design
(hisim-stratified-storage-coarse-0392). This spec is the fine model.

## 2. Decisions

**D1 — Components never iterate or sub-step in time internally** (owner, 2026-11-25). The sub-stepping that bead
hisim-hydronics-fxix.1 recommended on 2026-10-17 (`n = ceil(m dt / layer mass)` sub-steps inside the tank) is
rejected. Consequence: the layer model is one explicit step per simulation step, from the start-of-step temperatures
of all layers, and it is valid only where the water that leaves a layer in one step is at most the layer's mass (§4.4).
A step length where that does not hold needs the second model of D2.

**D2 — A storage may have a second, optional model for coarse steps, as the part-load pattern does** (owner,
2026-11-25). One model at or below `SimulationParameters.part_load_above_seconds` (600 s by default), an
approximation above it that delivers about the same totals. Consequence: this spec covers only the fine model; the
coarse model is designed under hisim-stratified-storage-coarse-0392, blocked by this one. What a stratified storage
does above the threshold until then is Q1 of §8.

**D3 — Priority between the collector and the backup is control strategy and stays in the controllers** (owner,
2026-10-10, confirmed 2026-11-25). The sensor heights belong to the controllers' reading of the tank. Consequence:
the tank publishes sensor temperatures at configured heights (§4.6) and decides nothing; which sensor a controller
reads is its wiring; the priority logic itself stays with bead hisim-bq3h.

**D4 — Energy closes per step for the tank as a black box** (owner, 2026-11-25). Consequence: every heat the tank
publishes comes from the same layer step; `Σ coils − tap − loss = ΔU` to float precision on every step
(`tests/test_energy_balance_storages.py` and the fluid-loop checks of #1122 keep checking it), and the buoyancy
merge of §4.5 conserves energy exactly.

Earlier decisions this spec rests on: D4 of the hydronic coupling spec (controllers decide on start-of-step
values), its D5 (the buffer's stratification path kept until replaced; replaced here by deletion, Q3), decision 7 of
the part-load spec (the store is a black box that publishes port and sensor quantities only; "any tank model, a
stratified one included, can publish" the controlled temperature at the end of the step), decision 9 of the
part-load spec (collector and backup each set their own ratio; no priority in the mechanism), and the modelling
guide's rule that a new store publishes its start-of-step temperature and both ends book their flows on it
(`system_docs/modelling_guide.md` §1).

## 3. Terms

- **Layer**: one of N equal volumes the tank is cut into along its height, fully mixed within itself, with its own
  temperature. Layer 1 is at the bottom, layer N at the top. Mass `M_k = M/N` (kg), heat capacity `C_k = M_k c`
  (J/K) with `c = hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K` (4180 J/(kg K)).
- **Start-of-step temperature** `T0_k`: layer k's temperature at the start of the step (°C), the tank's state. Every
  value the tank publishes to another component is computed from the `T0_k`, so nothing it publishes moves while a
  step iterates (D4 of the hydronic coupling spec).
- **End temperature** `T_end,k`: layer k's temperature at the end of the step, after the buoyancy merge; the next
  step's `T0_k`.
- **Coil**: one of the tank's charging circuits (the primary coil of the backup, the secondary coil of the
  collector), modelled as today as a water stream that enters the tank at the coil's inlet layer at the generator's
  supply temperature and leaves at its outlet layer at that layer's `T0` (an ideal heat exchanger, as the mixed node
  assumes today; the coil's loop and the tank water do not mix in reality, only their heats).
- **Port height** `h`: where a coil's inlet or outlet, or a sensor pocket, sits, as a fraction of the tank's height
  from the bottom (0 = bottom, 1 = top), dimensionless. Its layer is `k = min(floor(h N) + 1, N)`.
- **Tap draw**: the household's warm-water demand, served through the thermostatic mixing valve of stage C
  (`SimpleDHWStorage.mixing_valve_draw`): `m_hot` of tank water leaves the top layer and the same mass of mains water
  at `T_cold` (10 °C) enters the bottom layer.
- **Through-flow** across a boundary: the net mass flow (kg/s) crossing the boundary between two neighbouring layers,
  from mass conservation of every layer; positive upward.
- **Upwind inflow term**: the heat a stream brings into a layer, `m c (T_from − T0_k) dt`, with `T_from` the
  start-of-step temperature of the layer or the supply the water comes from. The same form as a circuit's heat in
  `hydronics.circuit_power_w`, so no new physics function is needed.
- **Courant number** of a layer: the mass that leaves the layer in one step over the layer's mass,
  `Co_k = (Σ outflows of k) dt / M_k`. The explicit scheme is valid for `Co_k ≤ 1` (§4.4).
- **Buoyancy merge**: after the explicit step, every layer that is warmer than the layer above it is mixed with it
  to their mass-weighted mean, until the profile is monotone (no layer warmer than the one above). The "inversion
  mixing" of the TRNSYS multi-node store (Type 4) and of Duffie and Beckman, Solar Engineering of Thermal Processes,
  §8.4. Not a time sub-step: at most N − 1 merges, each a finite arithmetic mean.
- **Sensor pocket**: a configured height at which the tank publishes its temperature for a controller: its start-of-step
  value (what the controller decides on) and its end value (what its part-load control reads).
- **Threshold**: `SimulationParameters.part_load_above_seconds`, 600 s by default (part-load spec §3).

## 4. Design

### 4.1 Which storages

The hot-water cylinder `SimpleDHWStorage` gets the layer model. It is the storage #94 needs, the one with two coils
at different heights and a draw that displaces the column, and the one whose circuits can run at a Courant number
below 1 at 60 s (§4.4). The space-heating buffer `SimpleHotWaterStorage` does not get it in this spec: its
part-load mode above 600 s rests on the end temperature being exactly linear in each circuit's ratio (part-load
spec, decision 21), which a buoyancy merge breaks; its non-conserving stratification path and the field
`heat_exchanger_is_present` are deleted (Q3), since no recorded setup uses them and D5 only kept them until a
replacement existed. A buffer of layers is a bead of its own if the owner ever wants it.

A cylinder with `number_of_layers = 1` keeps today's model unchanged: the step-mean `MixedNode` with the tap valve
solved on the step mean (Q4). The layer model runs from N = 2 upward. So every recorded energy system, every golden
and every RenoVisor run stays as it is until a file says `number_of_layers: 5`.

### 4.2 Geometry

The tank is the cylinder of `calculate_surface_area_of_storage` (`simple_water_storage.py:826`): radius
`R = (V / 4π)^(1/3)`, height `H = 4R`, cross-section `A = π R²`, side area `2π R H`, two caps of `A`. The 250 l
preset tank has `R = 0.271 m`, `H = 1.084 m`, `A = 0.231 m²`, a surface of 2.307 m², 248 kg of water
(`Water.DENSITY_KG_PER_LITER`, 0.992 kg/l) and `C = 1.037 MJ/K`.

Layer k has the height `H/N`, the mass `M/N`, the side area `2π R H / N`, and the top and bottom layers add one cap
each. Its loss coefficient `UA_k = U · (its surface)`, with `U = heat_transfer_coefficient_in_watt_per_m2_per_kelvin`
(0.36 W/(m² K)); `Σ UA_k` is today's `UA` (0.831 W/K for the preset tank), so the standby loss of a uniform tank is
unchanged. The conduction between layers k and k+1 is `UA_cond = λ_eff A / (H/N)`, with `λ_eff` the effective
vertical conductivity of water plus wall (§4.7, a configured field with a default of 1.0 W/(m K)): 1.06 W/K for
N = 5 and 2.13 W/K for N = 10 on the preset tank.

### 4.3 The step: upwind transport, conduction, loss, then buoyancy

Every quantity of the step is computed from the start-of-step temperatures `T0_1 … T0_N`, the coils' supplies and
flows (inputs), the demand (input) and the surrounding temperature (input).

**The tap valve on the top layer.** The tap draws from layer N, whose supply temperature is `T0_N`, a committed
value, so the valve of stage C has a closed form and the local Illinois iteration of `solve_tank_step` is not
needed for N ≥ 2:

- `T0_N > T_warm` (40 °C): `m_hot = m_d (T_warm − T_cold) / (T0_N − T_cold)`, the heat drawn equals the demand;
- `T_cold < T0_N ≤ T_warm`: `m_hot = m_d`, all of the demand passes unmixed, the shortfall is
  `ThermalEnergyUnmetDHWInWattHour` as today;
- `T0_N ≤ T_cold`: nothing is drawn.

`m_hot` leaves layer N and the same `m_hot` of mains water at `T_cold` enters layer 1: the column moves up by
`m_hot dt / M_k` layers per step.

**Through-flows.** Each circuit moves water from its inlet layer to its outlet layer: the primary coil from `k_in,1`
to `k_out,1`, the secondary coil from `k_in,2` to `k_out,2`, the tap from layer 1 (refill) to layer N (draw). The
through-flow across the boundary between layers k and k+1 is

`F_k = Σ_circuits m_c · s_c(k)`, with `s_c(k) = +1` if the circuit's inlet layer is at or below k and its outlet
layer at or above k+1 (it moves water up across the boundary), `−1` for the reverse, `0` if the boundary is not
between its inlet and outlet.

A coil whose inlet and outlet lie in the same layer moves no water through the tank; its supply mixes into that
layer and its return is that layer's `T0`.

**The explicit layer balance.** For every layer k:

`C_k (T*_k − T0_k) = Σ_s m_s c (T_s − T0_k) dt + UA_cond (T0_k+1 − T0_k) dt + UA_cond (T0_k−1 − T0_k) dt − UA_k (T0_k − T_amb) dt`

where `T*_k` is the temperature before the buoyancy merge, the streams s entering k are the coil supplies whose
inlet layer is k (at the generator's supply temperature), the refill at layer 1 (at `T_cold`), the through-flow
from below if `F_k−1 > 0` (at `T0_k−1`) and the through-flow from above if `F_k < 0` (at `T0_k+1`); the conduction
terms exist only for the neighbours that exist. Streams that leave k do so at `T0_k` and contribute nothing in this
form, because every layer keeps its mass (`Σ in = Σ out`). This is the upwind scheme of bead
hisim-hydronics-fxix.1: layer k books the water from its neighbour exactly as a start-of-step node books a circuit
(`SimpleHotWaterStorage.start_of_step_node_step`, `:1646`).

**Buoyancy merge.** Scan from the bottom: while some layer k is warmer than layer k+1 (`T*_k > T*_k+1`), replace
both, and every neighbour the merged block is still inverted against, by their mass-weighted mean (the arithmetic
mean, since the layers have equal mass). The result `T_end,1 ≤ … ≤ T_end,N` is monotone. This is how a solar coil
at the bottom heats the tank: the warmed bottom layer rises into the layers above it, as the real plume does. The
merge is a finite pass over N layers, at most N − 1 means, and conserves `Σ C_k T_k` exactly.

**Closure.** Summing the layer balances, every through-flow appears once as an inflow and once as an outflow at the
same `T0` and cancels, the conduction terms cancel pairwise, and the merge conserves the sum, so

`Σ_k C_k (T_end,k − T0_k) = Σ_coils m_c c (T_sup,c − T0_k_out,c) dt − m_hot c (T0_N − T_cold) dt − Σ_k UA_k (T0_k − T_amb) dt`

holds identically, for any flows and any N, computed with `math.fsum` as `MixedNode.step` does. The right-hand side
is exactly what the coils' generators book (`m c (T_sup − T_ret)` with `T_ret = T0_k_out`), what the tap books and
what the loss port publishes. D4 is a property of the scheme, not a rule.

A worked step, the preset tank with N = 5 (49.6 kg per layer) at 60 s, layers 20/20/50/55/58 °C from the bottom
(cold below, warm above), the heat pump charging 0.369 kg/s at 63 °C into layer 5, out of layer 3, no draw, no
conduction and no loss: the through-flow is 0.369 kg/s downward across the boundaries 4/5 and 3/4 (22.1 kg per
step, `Co = 0.45`); layer 5 gains `0.369 · 4180 · (63 − 58) · 60 = 462.7 kJ` and ends at 60.23 °C; layer 4 gains
`0.369 · 4180 · (58 − 55) · 60 = 277.6 kJ` and ends at 56.34 °C; layer 3 gains `0.369 · 4180 · (55 − 50) · 60 =
462.7 kJ` and ends at 52.23 °C; layers 1 and 2 stay at 20 °C; the heat pump books `0.369 · 4180 · (63 − 50) · 60 =
1203.0 kJ`, the sum of the three. The profile is monotone, so nothing merges.

### 4.4 Validity: the Courant number, measured against the flows of the twins

The explicit upwind scheme carries, in one step, at most one layer's mass across a boundary: with `Co_k = 1` layer
k ends exactly at its upstream neighbour's start temperature (a plug shift of one layer); with `Co_k > 1` it ends
beyond it, which no mixing can produce. Conduction and loss are explicit too, with the condition
`2 UA_cond dt / C_k ≤ 1`, which is 0.01 at N = 20 and 60 s on the preset tank and never binds in practice.

The flows a cylinder sees, measured on 2026-11-25 on `household_heatpump_solar_thermal_building_sizer`
(15 June 2021 at 60 s, 7.78 kW heat pump, 4 m² collector, 250 l cylinder, Aachen test reference year, predefined
occupancy) and computed from the generators' laws for the others:

| Circuit | Flow, kg/s | Water per 60 s step, kg | Source |
|---|---|---|---|
| heat pump, hot-water charge, 7.78 kW | 0.350 mean, 0.369 peak | 21.0 to 22.1 | measured; hplib's secondary flow is the rated power over about 5 K, `7780 / (4190 · 5) = 0.371` |
| gas boiler, hot-water charge, 8.56 kW (gas + solar twin; `max(7780, 2500) · 1.1`) | 0.102 at a 20 K lift, 0.205 at 10 K, 0.410 at the pump's largest flow (5 K at full power, `minimal_lift_at_maximal_power_in_kelvin`) | 6.1 / 12.3 / 24.6 | `m = P / (c · lift)`, lift `T_set − T0` (coupling spec §5.1) |
| electric heater or district-heating substation, hot water, 7.78 kW | 0.019 (`P / (c · 100 K)`) | 1.1 | `generic_electric_heating.py:196`, `generic_district_heating.py:262` |
| collector, 4 m², measured | 0.0077 mean, 0.0142 peak | 0.46 / 0.85 | measured |
| collector, 4 m², pump bound 40 kg/(m² h) | 0.044 | 2.7 | `SpeedControlledSolarPump` |
| collector, 20 m² (top of the solar assembly's range), pump bound | 0.222 | 13.3 | the same |
| tap, hot water out of the tank at the day's peak draw (10 l/min asked at 40 °C, tank at 55 to 60 °C) | 0.117 | 7.0 | measured |
| tap, the same draw from a 45 °C tank / all of the demand below 40 °C | 0.141 / 0.165 | 8.5 / 9.9 | the valve |

The layer that works hardest is the one a coil's supply enters: its outflow is the coil's flow (plus a through-flow
entering from a neighbour, which only arises when a second circuit enters the same layer). The tap's draw and a
charge in the same step move the column in opposite directions through the upper layers, so they do not add up:
the top layer's outflow during a charge is the charge flow, draw or no draw. Per layer mass of the 250 l cylinder
(49.6 kg at N = 5, 31.0 at N = 8, 24.8 at N = 10, 16.5 at N = 15, 12.4 at N = 20), at 60 s:

| Worst layer outflow, kg per step | Co at N = 5 | N = 8 | N = 10 | N = 15 | N = 20 |
|---|---|---|---|---|---|
| heat-pump charge, 22.1 | 0.45 | 0.71 | 0.89 | 1.34 | 1.78 |
| boiler at its pump's largest flow, 24.6 | 0.50 | 0.79 | 0.99 | 1.49 | 1.98 |
| tap at its peak, 9.9 | 0.20 | 0.32 | 0.40 | 0.60 | 0.80 |
| collector, 20 m², 13.3 | 0.27 | 0.43 | 0.54 | 0.81 | 1.07 |
| electric heater, substation, 4 m² collector: at most 2.7 | 0.05 | 0.09 | 0.11 | 0.16 | 0.22 |

The largest N whose layers hold a step's charge of the twin's heat pump (0.369 kg/s), by step length:

| Step length | Water per step, kg | Largest N with Co ≤ 1 | Co at N = 5 |
|---|---|---|---|
| 60 s | 22.1 | 11 | 0.45 |
| 120 s | 44.3 | 5 | 0.89 |
| 300 s | 110.7 | 2 | 2.2 |
| 600 s (the threshold) | 221.4 | 1 | 4.5 |
| 900 s | 332.1 | none: 1.3 tanks per step | 6.7 |
| 3600 s | 1,328 | none: 5.4 tanks per step | 27 |

For a larger heat pump the flow grows with the rated power: about 0.57 kg/s at 12 kW (34 kg per 60 s step, largest
N = 7), 0.72 kg/s at 15 kW (43 kg, N = 5), 0.95 kg/s at 20 kW (57 kg, N = 4), on the same 250 l cylinder.

So: the fine model as decided holds at 60 s with N up to about 10 for the twins' machines and up to 5 for a heat
pump of 15 kW; at 120 s with N = 5; and not at 300 s or 600 s for any cylinder a heat pump or a boiler charges. The
threshold of D2 (600 s) is not where the fine model stops being valid; the Courant number is. Between 120 s and the
threshold a charged cylinder with N ≥ 2 runs into the condition in its first charge. §8 Q1 and Q2 decide what
happens then; the recommendation is to fail loudly in both cases.

### 4.5 Ports, energy ports and the fluid loops

The tank keeps its channels and their loop tags of hydronic stage E and #1122. Per coil, a `FluidLoop` with
`supplies=False`, `pumps=False`, `may_cool=False`: `temperature_in` (the generator's supply), `flow_in` (its flow),
`temperature_out` (the return) and `flow_out` (the flow passed through unchanged). What changes is what the return
carries:

| Output | N = 1 (today) | N ≥ 2 | Reader |
|---|---|---|---|
| `StepMeanWaterTemperatureToHeatGeneratorInCelsius` | the step mean `T̄` | `T0` of the primary coil's outlet layer, a start-of-step value (the name then lies; Q6) | the backup, as its return; it books `m c (T_sup − T_ret)` |
| `StepMeanWaterTemperatureToSecondaryHeatGeneratorInCelsius` | `T̄` | `T0` of the secondary coil's outlet layer | the collector, as its return |
| `WaterMassFlowRateToHeatGenerator`, `…ToSecondaryHeatGenerator` | the flow in, unchanged | the same | the loop's mass check (#1122) |
| `WaterTemperatureAtStartOfStepInCelsius` | `T0` | the upper sensor pocket's `T0` (§4.6) | the backups' controllers (on/off, hysteresis), the solar controller's full-tank stop today (rewired, §4.6) |
| `WaterTemperatureAtEndOfStepInCelsius` | `T_end` | the upper pocket's `T_end` | the backups' part-load controls |
| `LowerSensorWaterTemperatureAtStartOfStepInCelsius`, `…AtEndOfStepInCelsius` | `T0`, `T_end` (one layer) | the lower pocket's `T0` and `T_end` | the solar controller: the differential switch-on against the collector sensor, the full-tank stop, the pump's part-load target |
| `WaterTemperatureOfLayer<k>AtStartOfStepInCelsius`, k = 1 … N | one output, `T0` | N outputs | plots, tests; no controller |
| `ThermalPowerFromHeatGenerator`, `…FromSecondaryHeatGenerator`, W | `m c (T_sup − T̄)` | `m c (T_sup − T0_k_out)` | the energy balance (the tank's side of each loop), KPIs |
| `ThermalPowerConsumptionDHW`, W, `PRODUCTION` to `HOT_WATER_USE` | `m_hot c (T̄ − T_cold)` | `m_hot c (T0_N − T_cold)` | the balance, the cost engine's useful heat, KPIs |
| `ThermalEnergyUnmetDHWInWattHour` | on `T̄` | on `T0_N` | the KPI "Unmet DHW heat demand" |
| `ThermalEnergyIncreaseInStorage`, Wh, `STORED_CHANGE` | `C (T_end − T0)` | `Σ_k C_k (T_end,k − T0_k)` | the balance |
| `ThermalEnergyInStorage`, Wh | `C T_end` | `Σ_k C_k T_end,k` | KPIs |
| `HeatLossInWatt`, `HeatToHeatedZoneInWatt`, `LOSS` | `UA (T̄ − T_amb)` | `Σ_k UA_k (T0_k − T_amb)` | the balance, the building |
| `StandbyTemperatureLoss`, K | `loss / C` | `loss / C`, on the whole tank | the assembly bounds |

The generator's return is a committed start-of-step value, so the hot-water chain no longer iterates between the
generator and the cylinder (today the step mean takes a few passes, coupling spec §6); the generators' supply caps at
their set temperatures (coupling spec §5) stay as they are and now act on a return that does not move within the
step. The collector reads the lower pocket's `T0` as `TemperatureCollectorInletAtStartOfStepInCelsius` and the
secondary coil's outlet `T0` as its return; with the default heights both are the bottom layer.

**The energy balance and the fluid-loop checks.** The post-run balance (`hisim/postprocessing/energy_balance/check.py`)
books each coil's loop once from the loop's flow, supply and return, by the supplier's sign (fluid loops spec §3.4):
unchanged, and the tank's side equals the generator's side on every pass, since both compute `m c (T_sup − T0_k_out)`
from the same three published values. The tank's own balance `Σ coils − tap − loss = ΔU` closes by §4.3. The mass
check at every member (`flow_out = flow_in`) holds because the tank passes every flow through unchanged; the
heating-only check holds because a coil's supply is never below the return it is capped against; the energy check at
the member (stage 4 of #1122) holds because the booked `ThermalPowerFrom*` is computed from the member's own
published quantities. The Sankey sees the same nodes as today; layers are not nodes.

### 4.6 Sensors: two pockets, wired by the controllers

The tank has two sensor pockets at configured heights, an upper and a lower one (Q7), and publishes each pocket's
start-of-step and end temperature (§4.5). A real bivalent cylinder has its pockets at the heights of its coils, and
the installer puts the backup's sensor in the upper one and the solar controller's sensor in the lower one. Here that
is the wiring: the backups' controllers (`GenericBoilerController`, `MoreAdvancedHeatPumpHPLibControllerDHW`,
`ElectricHeatingController`, the substation's controller) keep their default connections to
`WaterTemperatureAtStartOfStepInCelsius` and `WaterTemperatureAtEndOfStepInCelsius`, which carry the upper pocket;
the solar controller's default connections (`solar_thermal_system.py:1511` and `:1518`) and the collector's inlet
sensor (`:925`) move to the lower pocket's outputs. A file may wire any controller to either pocket.

With the defaults of §4.7 and N = 5, the upper pocket (0.7) is layer 4, the lower pocket (0.1) layer 1. The backup
charges when layer 4 falls below its aim less its hysteresis; the collector's pump switches on when the collector's
sensor reads 7 K above layer 1 and stops when layer 1 starts a step above 80 °C. With N = 1 both pockets are the one
layer, and nothing changes against today. The temperature check (`check_converged_mean_water_temperature`, 0 to
90 °C) applies to the hottest and the coldest layer.

The tank does not know which controller reads which pocket, so D3 holds: a priority rule (bead hisim-bq3h) is a
change to the controllers' set points or an L2 signal, never to the tank.

### 4.7 Configuration, presets, sizing

New fields of `SimpleDHWStorageConfig`, all plain fields (nothing in the surrounding system decides them; Q5 on a
future sizing law for N):

| Field | Default | Meaning and source |
|---|---|---|
| `number_of_layers` | 1 | N; 1 keeps today's model. 5 is the recommended value for a stratified cylinder (§4.4: `Co ≤ 0.5` at 60 s for the twins' machines, 0.87 for a 15 kW heat pump). |
| `primary_coil_inlet_height_as_fraction_of_height` | 1.0 | the backup coil's top |
| `primary_coil_outlet_height_as_fraction_of_height` | 0.5 | the backup coil's bottom: the backup heats the upper half, the ready volume of a bivalent cylinder |
| `secondary_coil_inlet_height_as_fraction_of_height` | 0.25 | the solar coil's top |
| `secondary_coil_outlet_height_as_fraction_of_height` | 0.0 | the solar coil's bottom |
| `upper_sensor_height_as_fraction_of_height` | 0.7 | the backup's pocket, in the ready volume above the backup coil's middle |
| `lower_sensor_height_as_fraction_of_height` | 0.1 | the solar pocket, at the solar coil |
| `effective_vertical_conductivity_in_watt_per_meter_kelvin` | 1.0 | water's 0.6 W/(m K) (VDI Heat Atlas) plus the steel wall's share; store tests after EN 12977-3 give 0.6 to 2 W/(m K) for cylinders with internal coils |

The heights and the conductivity are estimates from the layout of bivalent solar cylinders (the backup coil in the
upper half, the solar coil in the lower third) and from the literature named; the modelling guide §5 asks that an
estimate says so, and Q5 asks the owner to confirm or replace them. The tap's draw at the top and refill at the
bottom are not configurable: every cylinder does that.

Presets: `preset_standard` stays as it is (N = 1). A new `preset_stratified(name)` sets `number_of_layers = 5` and
the defaults above. The refusals at build (`SimpleDHWStorage.build`): N not a positive integer; a height outside
[0, 1]; a coil whose outlet lies above its inlet (a charging coil is fed at its top; a file would state the reverse
only by mistake); the conduction condition of §4.4;
and, until the coarse model lands, N ≥ 2 with a step length above the threshold (Q1).

Twins and assemblies: `energy_systems/assemblies/dhw/indirect_cylinder.assembly.yaml` gets a parameter
`number_of_layers` (default 1, range 1 to 20) with bounds for the new outputs; the need `cylinder_temperature` of
`energy_systems/assemblies/heating/solar_thermal.assembly.yaml` binds the solar controller to the lower pocket's two
outputs; the recorded twins keep N = 1 until the coarse model lands (§5), and a test runs the two solar twins with
N = 5 at 60 s (§6, T10).

### 4.8 Where the code goes

The layer step is a pure function of the start temperatures, the streams, the demand, the surrounding temperature
and the tank's constants: a subcomponent (`system_docs/component_design_principles.md` §C) `StratifiedLayers`, a
frozen dataclass built once from the configuration (N, `M_k`, `C_k`, `UA_k`, `UA_cond`, the port layers, the tap
temperatures, `dt`) with `step(t0_per_layer, coil_streams, demand, t_amb) -> LayerStep` (the end temperatures, the
heat per coil, the tap's draw and unmet share, the loss, `ΔU`), and the pure helpers `through_flows`,
`explicit_layer_balance`, `buoyancy_merge`, `layer_of_height`, each with its own tests. It has one caller, so it
lives beside `StartOfStepNodeStep` in `hisim/components/simple_water_storage.py`, or, since that module is 3,400
lines, in `hisim/components/dhw_cylinder_layers.py`, imported only by the cylinder (`coding_style.md` §1: small
units; a module with one caller is that caller's). `hydronics.py` gains nothing: the upwind term is
`circuit_power_w` and the constants are `Water`.

`SimpleDHWStorage.i_simulate` chooses once, when built, between `solve_tank_step` (N = 1) and the layer step
(N ≥ 2), as the buffer chooses its mode (`two_mode_buffer_spec.md` §4.2); `publish_tank_step` takes the per-coil
heats, the tap's draw and the loss from either and publishes the same outputs. The state is `DhwTankState` with a
tuple of N start temperatures; `i_save_state` and `i_restore_state` copy it whole. The Courant check of Q2 runs in
the layer step, one pass over N layers, and the step raises `LayerOutflowExceedsLayerMassError` naming the layer,
the step, the mass that left and the layer's mass, N and `dt`.

Cost in the hot path: O(N) arithmetic per pass with N ≤ 20, no iteration with the generators; a measurement of the
passes per step on the solar twins at 60 s comes with PR 3 (§5).

### 4.9 Interaction with the part load

At and below the threshold every ratio is exactly 1 (part-load spec, decision 16), so the fine model never sees a
fraction. The controllers' `PartLoadControl` reads the end temperature of the pocket it is wired to; the backups'
controllers read the upper pocket's, the solar controller the lower pocket's. Above the threshold the layer model
does not run (Q1), and the coarse model of hisim-stratified-storage-coarse-0392 decides how a fraction and a pocket's
end temperature relate there. Decision 7 of the part-load spec, "any tank model, a stratified one included, can
publish" the controlled end temperature, is met by the pockets.

## 5. What changes and what does not

- **Results.** PRs 1 to 4 change no simulation result: every recorded energy system keeps N = 1, which runs today's
  code. A cylinder with N ≥ 2 at 60 s is expected to raise the collector's heat (its coil meets the bottom layer,
  near the mains temperature after a draw, instead of the 55 to 70 °C mean), lower the backup's hot-water heat by
  about the same amount, change the standby loss little (a hotter top and a colder bottom), keep the tap's delivered
  heat, and may raise the unmet hot water slightly (the top layer alone serves a draw, and the backup charges the
  upper half only). The direction is stated here; the numbers are measured by T10 and written into §7.
- **Public interfaces.** New outputs on the cylinder (the lower pocket, the layer temperatures), new configuration
  fields, a new preset, a new assembly parameter; the buffer's field `heat_exchanger_is_present` goes (Q3), which
  touches `hisim/energy_system_v3.schema.json` and two tests, no recorded file. The rename of
  `StepMeanWaterTemperatureTo{Heat,SecondaryHeat}GeneratorInCelsius` (Q6) is a column rename in every DHW twin's
  results and gets its own PR with the heads-up of `system_docs/results_and_interfaces.md` §5 on renovisorissues.
- **Goldens and twins.** None re-blessed by PRs 1 to 4, unless the golden gate counts the cylinder's new columns as
  a change, which PR 3 checks. The twins and the RenoVisor requests switch to N = 5 only together with the coarse
  model (RenoVisor runs at 900 s), with re-blessed goldens and a heads-up; until then #94's remaining gap stays, and
  the coarse model is on #94's critical path.

## 6. Verification

Unit tests of the pure functions, `base`, in `tests/test_dhw_cylinder_layers.py`:

- **T1 energy closure per step.** Random profiles, flows, supplies and draws, N in {1, 2, 5, 10, 20}:
  `Σ C_k (T_end,k − T0_k)` equals `Σ coils − tap − loss` within 1e-9 J; also with inverted profiles, so the merge is
  covered.
- **T2 pure transport.** No conduction, no loss: a charge at `Co = 1` shifts the profile by exactly one layer per
  step (plug flow); at `Co = 0.5` the entering layer ends at the mean of its own and the upstream start temperature;
  a coil with inlet and outlet in one layer moves nothing through the tank and mixes into that layer like a
  `MixedNode` with `a → 0`.
- **T3 pure conduction.** Two layers at 60 and 20 °C, no flows: each step moves `UA_cond · 40 K · dt` from the
  warm to the cold layer, and the explicit solution agrees with the exact exponential to first order in `dt`; the
  stability condition is refused at build when violated.
- **T4 a tap draw.** A tank at 20/20/60/60/60 °C: the draw's heat equals the demand, the cold front moves up by
  `m_hot dt / M_k` layers, the hot water let out is `m_d (T_warm − T_cold)/(T0_N − T_cold)`; with the top layer
  below 40 °C the unmet share is `(T_warm − T0_N)/(T_warm − T_cold)` of the demand; below 10 °C nothing is drawn.
- **T5 buoyancy.** A bottom layer warmed above the one above it merges to their mean; a fully inverted profile ends
  uniform at the mass-weighted mean; the sum `Σ C_k T_k` is unchanged by the merge; a monotone profile is untouched.
- **T6 the layer model at N = 1 against the mixed node.** The preset tank at 60 s over 1,440 steps, charged with
  0.369 kg/s at the return plus 5 K and drawn on by the June day's demand profile: the end temperatures agree within
  0.05 K and every heat within 0.1 % with `solve_tank_step`; the difference is the valve on `T0` instead of `T̄` and
  the loss on `T0`, both of order `a` (0.09 for the charge).
- **T7 the Courant refusal.** A flow that empties a layer in one step raises `LayerOutflowExceedsLayerMassError`
  in that step, naming the layer, the masses, N and `dt`.
- **T8 refusals at build.** N not a positive integer, a height outside [0, 1], a coil layout the defaults never
  produce, N ≥ 2 above the threshold (Q1), the conduction condition.

Component tests, `base`, in `tests/test_dhw_storage_node.py` and `tests/test_energy_balance_storages.py`:

- **T9 the balance report.** The cylinder with N = 5 in a day's run: the energy-balance check reports it closed on
  every step, and the loop checks of #1122 pass (mass at every member, heating only, the energy check at the member).
- **T11 the sensor wiring.** The default connections wire the backups' controllers to the upper pocket and the solar
  controller and the collector's inlet sensor to the lower one; a file may wire either; the temperature check covers
  the hottest and the coldest layer.

Twin tests, `system_setups`, in `tests/test_stratified_cylinder_solar_twins.py`:

- **T10 the solar twins' yield before and after.** `household_gas_solar_thermal_building_sizer` and
  `household_heatpump_solar_thermal_building_sizer`, the first week of June 2021 at 60 s, N = 1 against N = 5: the
  collector's heat rises, the backup's hot-water heat falls, the tap's delivered heat agrees within 1 %, the unmet
  hot water does not rise by more than a stated bound, every step closes, and the passes per step are reported.
  The year's figures are measured by hand and written into §7.

Deliberately not tested here: the agreement of coarse steps with 60 s (`modelling_guide.md` §3), which is the coarse
model's test under hisim-stratified-storage-coarse-0392; the solar priority (hisim-bq3h).

## 7. Measured results

2026-11-25, the flows of §4.4 (one June day of the heat pump + solar twin at 60 s). The yields of T10 and the
passes per step are filled in by PR 4.

## 8. Open questions

- **Q1. What does a cylinder with N ≥ 2 do above the threshold until the coarse model lands?** Context: RenoVisor
  runs at 900 s, the goldens run every twin at 60 s and 900 s, and §4.4 shows the fine model cannot run a charge at
  900 s with any N. Options: (a) *refuse at build* when `number_of_layers ≥ 2` and `seconds_per_timestep >
  part_load_above_seconds`, with a message naming the bead hisim-stratified-storage-coarse-0392; a stratified
  cylinder is then a 60 s (and 120 s) model until the coarse model exists, and the recorded twins keep N = 1. (b)
  *Fall back to N = 1* above the threshold: the same file runs stratified at 60 s and mixed at 900 s; results differ
  between the two step lengths by design (the whole point of the layers), which breaks the rule that a coarse run
  agrees with 60 s within 1 % and gives RenoVisor nothing for #94. (c) *Run the fine model wherever every layer's
  Courant number stays below 1*, failing when it does not: at 900 s that is every charge, so it is (a) with a later
  error. Recommendation: (a). Note for the coarse bead: the fine model's validity is a Courant condition, not the
  600 s threshold (§4.4); the coarse design should consider switching on the Courant number rather than on `dt`.
- **Q2. What happens at a fine step when a flow exceeds a layer's mass?** Context: at 60 s the twins' machines stay
  at `Co ≤ 0.5` with N = 5, but a 20 kW heat pump reaches 1.15, and a file may state N = 20. Options: (a) *fail
  loudly* in that step (`LayerOutflowExceedsLayerMassError`, T7), with the remedy in the message: fewer layers, a
  larger cylinder or a shorter step. (b) *Clamp*: cap the water moved across a boundary at one layer per step and
  let the rest pass as a plug through several layers; that is the plug-flow model the owner left to the coarse bead,
  and inside the explicit scheme it books heat the layers did not receive. (c) *Size N from the generators' flows*:
  the generators contribute their largest hot-water flow as a sizing fact and a law chooses the largest N with a
  margin; a later refinement, since the tap's peak is not a fact and the law would read the step length. Recommendation:
  (a) now, (c) as a bead if the assemblies' sweeps hit (a).
- **Q3. Which storages, and the buffer's dead path.** Options: (a) the cylinder only, and delete the buffer's
  stratification path with its field `heat_exchanger_is_present`, its mixing factor and the mass-mixing law (the
  schema, two tests and the buffer's docstrings change; no recorded file states the field); (b) both storages now;
  the buffer's part-load mode above 600 s rests on linear end temperatures, and at 60 s the gas twin's 172 l buffer
  (0.41 kg/s from the boiler's pump, 24.6 kg per step, 34 kg per layer at N = 5) is close to the limit; (c) keep the
  buffer's path once more. Recommendation: (a); a layered buffer is a bead of its own if ever wanted.
- **Q4. Does N = 1 keep today's mixed node?** Options: (a) yes: `solve_tank_step` and the step-mean return stay for
  N = 1, nothing in results or goldens moves, and T6 pins the layer model's agreement with it; (b) the layer model at
  N = 1 replaces the mixed node, one model and a start-of-step return everywhere, at the price of moving every DHW
  golden by the order of `a` (0.1 K in the end temperature, tenths of a percent in the heats, with the heads-up).
  Recommendation: (a) now; (b) may follow when Q6's rename moves the goldens anyway.
- **Q5. The defaults.** N = 5 (recommended: `Co ≤ 0.5` for the twins at 60 s; a 10-layer cylinder fails for heat
  pumps above about 8 kW at 60 s and resolves little more for the backup-versus-solar question) or N = 10; the port
  heights 1.0/0.5 (backup coil), 0.25/0.0 (solar coil), 0.7 and 0.1 (pockets); `λ_eff` = 1.0 W/(m K). These are
  estimates from the layout of bivalent cylinders and the literature named in §4.7; the owner confirms them or names
  a datasheet to take them from.
- **Q6. The return outputs' names.** `StepMeanWaterTemperatureToHeatGeneratorInCelsius` and its secondary twin
  carry a start-of-step value for N ≥ 2. Options: (a) rename both to `WaterTemperatureToHeatGeneratorInCelsius` and
  `WaterTemperatureToSecondaryHeatGeneratorInCelsius` for every N (the buffer's spelling), a column rename in every
  DHW twin's goldens with the heads-up, in PR 5; (b) keep the names and state in the descriptions that the value is
  the step mean for N = 1 and the start-of-step outlet temperature above. Recommendation: (a), since the name would
  state a false thing about the value.
- **Q7. Two fixed pockets or a list.** Options: (a) an upper and a lower pocket, each with its height field and its
  pair of outputs, the existing sensor outputs carrying the upper one so that the backups' default connections stay;
  (b) a configurable list of pockets with indexed outputs, which fits a cylinder with three controllers but gives the
  default connections nothing fixed to read. Recommendation: (a); a third pocket is added when a third reader exists.
- **Q8. The priority of hisim-bq3h.** The triage of #94 recommends raising it to P2 and running it after this spec's
  PR 4, so its effect is measured on a stratified cylinder with the sensors in their pockets. Recommendation: do so.

## 9. Beads

Epic hisim-hydronics-fxix.

| Bead | Content | PR |
|---|---|---|
| hisim-hydronics-fxix.1 (P3) | this spec: the fine layer model of the cylinder, the deletion of the buffer's path, the pockets, the presets and the tests; closes with PR 4 | PR 1 to 4 below |
| hisim-stratified-storage-coarse-0392 (P3, blocked by hisim-hydronics-fxix.1) | the coarse model above the threshold: about the same totals as the fine model; the twins and RenoVisor switch to N = 5 with it | – |
| hisim-bq3h (P3; Q8 asks for P2) | solar priority in the controllers, measured on the stratified cylinder | – |
| hisim-report-states-defaults-41e9 (P3) | the mapping report names the cylinder's volume; unchanged by this spec | – |

The open questions of §8 get `owner-decision` beads only for what stays open after the review of this document
(spec template: an open question is a bead as well as an entry here).

The pull requests, each small and stacked on the one before (AGENTS.md, "Pull requests and merging"):

1. **The buffer's stratification path goes** (Q3): `heat_exchanger_is_present`, the mixing factor and the
   mass-mixing law, their tests and the schema entry; no result changes.
2. **The layer step** as a subcomponent with T1 to T8, not yet wired to the cylinder; no result changes.
3. **The cylinder runs the layer step for N ≥ 2**: the configuration fields, the pockets and the new outputs, the
   refusals at build and the Courant refusal, the state of N temperatures, T6, T9 and T11; N = 1 unchanged; the
   passes per step measured.
4. **The wiring and the twins' measurement**: the solar controller's and the collector's default connections to the
   lower pocket, `preset_stratified`, the assembly parameter and bounds, T10, and the year's figures in §7. Closes
   hisim-hydronics-fxix.1.
5. **The rename of the return outputs** (Q6, if decided), with re-blessed goldens and the heads-up.
