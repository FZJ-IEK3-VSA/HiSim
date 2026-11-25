# Spec: the stratified hot-water cylinder is a second component, interchangeable with the mixed one: N explicit start-of-step layers with real flows, valid where one step moves at most one layer

Status: draft, decided (owner decisions D1 to D11 of 2026-11-25 recorded; §8 lists what is still open), nothing
built. Owner: Noah Pflugradt. Epic: hisim-hydronics-fxix (bead hisim-hydronics-fxix.1; the coarse model is
hisim-stratified-storage-coarse-0392; the rename is hisim-dhw-return-outputs-rename-653d; the sourcing of the
defaults is hisim-stratified-cylinder-defaults-1eff). Builds on: `roadmap/hydronic_coupling_spec.md` (§4.5, §5.3,
§9.5, D4, D5), `roadmap/hydronic_part_load_spec.md` (decisions 3, 7, 9, 16, 23), `roadmap/two_mode_buffer_spec.md`,
`roadmap/fluid_loops_spec.md` (#1122), the start-of-step node of #1009, the seams spec (`roadmap/hisim_seams_spec.html`,
`system_docs/module_graph.yaml`), and renovisorissues #94.

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

**What the replacement must respect.** The owner decided on 2026-11-25 (D1 to D11 below): no iteration and no time
sub-stepping inside a component; the fine model is the explicit start-of-step layer model recorded in bead
hisim-hydronics-fxix.1 on 2026-10-17, valid where the water a circuit moves in one step is at most one layer; the
stratified cylinder is a second component beside the mixed one, interchangeable with it, as TRNSYS offers a fully
mixed tank type and a stratified tank type; the mixed cylinder stays as it is; a coarse model for the steps where the
fine one is not valid is a separate design (hisim-stratified-storage-coarse-0392). This spec is the fine model and
the new component.

## 2. Decisions

**D1 — Components never iterate or sub-step in time internally** (owner, 2026-11-25). The sub-stepping that bead
hisim-hydronics-fxix.1 recommended on 2026-10-17 (`n = ceil(m dt / layer mass)` sub-steps inside the tank) is
rejected. Consequence: the layer model is one explicit step per simulation step, from the start-of-step temperatures
of all layers, and it is valid only where the water that leaves a layer in one step is at most the layer's mass (§4.4).
A step length where that does not hold needs the second model of D2.

**D2 — A storage may have a second, optional model for coarse steps, as the part-load pattern does** (owner,
2026-11-25). One model at or below `SimulationParameters.part_load_above_seconds` (600 s by default), an
approximation above it that delivers about the same totals. Consequence: this spec covers only the fine model; the
coarse model is designed under hisim-stratified-storage-coarse-0392, blocked by this one. Until it exists, a run the
fine model cannot serve uses the mixed cylinder (D6).

**D3 — Priority between the collector and the backup is control strategy and stays in the controllers** (owner,
2026-10-10, confirmed 2026-11-25). The sensor heights belong to the controllers' reading of the tank. Consequence:
the tank publishes sensor temperatures at configured heights (§4.6) and decides nothing; which sensor a controller
reads is its wiring; the priority logic itself stays with bead hisim-bq3h.

**D4 — Energy closes per step for the tank as a black box** (owner, 2026-11-25). Consequence: every heat the tank
publishes comes from the same layer step; `Σ coils − tap − loss = ΔU` to float precision on every step
(`tests/test_energy_balance_storages.py` and the fluid-loop checks of #1122 keep checking it), and the buoyancy
merge of §4.3 conserves energy exactly.

**D5 — Two separate components, interchangeable, as TRNSYS's mixed and stratified tank types** (owner, 2026-11-25).
`SimpleDHWStorage` stays exactly as it is: one mixed node, no `number_of_layers`, its results and goldens unchanged.
A new component class `StratifiedDHWStorage` is the stratified cylinder. Both expose the same interface: the same
input and output names, load types, units, energy ports and loop tags, the same default connections in both
directions, so a system file or an assembly switches by changing the class name, and everything wired to the cylinder
works with either (§4.1). Consequence: one generic test pins the shared interface (T12); the two classes share no
model code, only the pure pieces both need (§4.8); every partner that names `SimpleDHWStorage` in a default
connection names `StratifiedDHWStorage` too (§4.8). The old Q4 (N = 1 keeps the mixed node) is moot: the stratified
class requires N ≥ 2 (§4.7).

**D6 — The fine model's validity is checked at run time, not at build** (owner, 2026-11-25). The stratified cylinder
checks every step's flows against the layer mass and fails the run at the first step in which a flow moves more than
one layer (§4.4). The error names the port, the flow, the layer mass, the step length, the largest N that would hold
and bead hisim-stratified-storage-coarse-0392. There is no check against the step length at build. Consequence: a
user picks the mixed cylinder for a run the stratified one cannot serve; RenoVisor at 900 s keeps the mixed one until
the coarse model exists; the recorded twins keep the mixed one (§5).

**D7 — The cylinder now; the buffer's dead path goes** (owner, 2026-11-25; the old Q3). The space-heating buffer's
`heat_exchanger_is_present=False` path, its mixing factor and its mass-mixing law are deleted in the first PR. A
stratified buffer, if ever wanted, is its own component later, on the pattern of D5; no bead for it now (§8).

**D8 — The defaults are accepted as presets marked as estimates** (owner, 2026-11-25; the old Q5). Five layers,
the backup coil from 1.0 to 0.5 of the height, the solar coil from 0.25 to 0.0, the pockets at 0.7 and 0.1,
`λ_eff = 1.0 W/(m K)`. Consequence: the preset's docstring and the field comments say they are estimates; bead
hisim-stratified-cylinder-defaults-1eff sources them (manufacturer data sheets, VDI 6002, EN 12977 or similar)
before RenoVisor uses the stratified cylinder.

**D9 — The return outputs are renamed in their own PR with a heads-up** (owner, 2026-11-25; the old Q6).
`StepMeanWaterTemperatureToHeatGeneratorInCelsius` and `StepMeanWaterTemperatureToSecondaryHeatGeneratorInCelsius`
become `WaterTemperatureToHeatGeneratorInCelsius` and `WaterTemperatureToSecondaryHeatGeneratorInCelsius` on both
cylinder classes, with the recorded twins re-recorded, the goldens re-blessed and the heads-up of
`system_docs/results_and_interfaces.md` §5 on renovisorissues. Until that PR the stratified class carries the old
names, whose descriptions say the value is the start-of-step outlet temperature. Bead
hisim-dhw-return-outputs-rename-653d, PR 6 of §9.

**D10 — hisim-bq3h (solar priority) is P2, scheduled after the stratified cylinder** (owner, 2026-11-25; the old
Q8). Its effect is measured on the stratified cylinder with the sensors in their pockets. Recorded on the bead.

**D11 — Two fixed sensor pockets, an upper and a lower one** (owner, 2026-11-25; the old Q7). Each has a height
field and a pair of outputs; the existing sensor outputs carry the upper pocket, so the backups' default connections
stay (§4.6). A third pocket is added when a third reader exists.

Earlier decisions this spec rests on: D4 of the hydronic coupling spec (controllers decide on start-of-step
values), its D5 (the buffer's stratification path kept until replaced; replaced here by deletion, D7), decision 7 of
the part-load spec (the store is a black box that publishes port and sensor quantities only; "any tank model, a
stratified one included, can publish" the controlled temperature at the end of the step), decision 9 of the
part-load spec (collector and backup each set their own ratio; no priority in the mechanism), and the modelling
guide's rule that a new store publishes its start-of-step temperature and both ends book their flows on it
(`system_docs/modelling_guide.md` §1).

## 3. Terms

- **The mixed cylinder**: `SimpleDHWStorage`, today's component, one fully mixed node with the tap valve solved on
  the step mean. Unchanged by this spec.
- **The stratified cylinder**: `StratifiedDHWStorage`, the new component of this spec, N layers.
- **The shared interface**: everything another component or a file can read from or send to a cylinder: its input
  and output names with their load types, units, energy ports, loop tags and postprocessing flags, its default
  connections in both directions, and the configuration fields both classes have (§4.1).
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
  (`SimpleDHWStorage.mixing_valve_draw`, moved to `hydronics` by §4.8): `m_hot` of tank water leaves the top layer
  and the same mass of mains water at `T_cold` (10 °C) enters the bottom layer.
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
- **Threshold**: `SimulationParameters.part_load_above_seconds`, 600 s by default (part-load spec §3). Not where
  the fine model stops being valid (§4.4); the Courant number is.

## 4. Design

### 4.1 Two components, one interface

`SimpleDHWStorage` keeps its code and its behaviour. `StratifiedDHWStorage` is a second `PhysicsComponent`, built
from its own configuration class `StratifiedDHWStorageConfig`, in its own folder (§4.8). A file states which it wants
by the class path; nothing else in the file changes:

```yaml
components:
  DHWStorage:
    class: hisim.components.stratified_dhw_storage.storage.StratifiedDHWStorage   # was hisim.components.simple_water_storage.SimpleDHWStorage
    preset: standard
```

**What is identical, and pinned by T12.** For both classes, read from an instance built from `preset_standard`:

- every input: its name, load type, unit, whether it is mandatory, whether it may stay unconnected
  (`allow_unconnected_mandatory`, the primary coil's two inputs), and its loop tag (`fluid_loop`: which loop, which
  channel);
- every output of the mixed class: its name, load type, unit, energy port (role and carrier), loop tag,
  postprocessing flags and display setting; the stratified class publishes every one of them with the same tags;
- the two fluid loops: name, carrier, fluid, `supplies=False`, `pumps=False`, `may_cool=False`;
- the default connections the cylinder declares from its partners (`get_default_connections_from_*`: the heat pump,
  the occupancy, the boiler, the collector, the substation, the electric heater): the same partner classes, the same
  input and output names;
- the default connections every partner declares from the cylinder: a partner that declares connections from
  `SimpleDHWStorage` declares the same list, output for output, from `StratifiedDHWStorage` (§4.8);
- `cost_relevance` (`PRICED`), the cost facts the economics adapter reads by class name (§4.8), the useful-heat
  source (`ThermalEnergyConsumptionDHW`);
- the configuration fields both have: `volume_heating_water_storage_in_liter` with the same sizing law
  (`VOLUME_LAW`, `SIZING_CONTRIBUTIONS` unchanged), `installation_location` with the same rule,
  `heat_transfer_coefficient_in_watt_per_m2_per_kelvin`, the cost fields; both offer `preset_standard(name)`, so a
  file keeps `preset: standard` when it switches.

**What the stratified class adds.** Only outputs that no default connection of any component names: the N layer
temperatures `WaterTemperatureOfLayer<k>AtStartOfStepInCelsius` (for plots and tests). T12 allows an output on the
stratified class that the mixed one lacks only if it matches that pattern; everything else must agree.

**The lower pocket is on both classes.** The two lower-pocket outputs (`LowerSensorWaterTemperatureAtStartOfStepInCelsius`,
`LowerSensorWaterTemperatureAtEndOfStepInCelsius`, §4.6) are part of the shared interface: the mixed class publishes
them too, equal to its single `T0` and `T_end`. Decided here, since the owner left it to the spec: the alternative,
publishing them on the stratified class only, would make the solar controller's default connection and the
`cylinder_temperature` need of the solar assembly differ by class, and a file could no longer switch by the class name
alone. The cost is two new result columns on every run with a mixed cylinder, whose values repeat two existing
columns; no KPI moves, and the PR says so (`results_and_interfaces.md` §1).

**Why two classes and not one with a switch.** One class with `number_of_layers` would carry two models, two state
shapes and a mode choice in every method, and a reader of a file could not tell from the class which model runs. Two
classes say it in the class name, as TRNSYS's Type 4 (stratified) and Type 38 (mixed) do; the interface test keeps
them interchangeable. A configuration field that selects a model is the pattern the two-mode buffer uses for the
part-load threshold (`two_mode_buffer_spec.md` §4.2), where the choice is the step length's, not the author's.

### 4.2 Geometry

The tank is the cylinder of `calculate_surface_area_of_storage` (`simple_water_storage.py:826`): radius
`R = (V / 4π)^(1/3)`, height `H = 4R`, cross-section `A = π R²`, side area `2π R H`, two caps of `A`. The 250 l
preset tank has `R = 0.271 m`, `H = 1.084 m`, `A = 0.231 m²`, a surface of 2.307 m², 248 kg of water
(`Water.DENSITY_KG_PER_LITER`, 0.992 kg/l) and `C = 1.037 MJ/K`.

Layer k has the height `H/N`, the mass `M/N`, the side area `2π R H / N`, and the top and bottom layers add one cap
each. Its loss coefficient `UA_k = U · (its surface)`, with `U = heat_transfer_coefficient_in_watt_per_m2_per_kelvin`
(0.36 W/(m² K)); `Σ UA_k` is the mixed cylinder's `UA` (0.831 W/K for the preset tank), so the standby loss of a
uniform tank is the same in both classes (T6). The conduction between layers k and k+1 is `UA_cond = λ_eff A / (H/N)`,
with `λ_eff` the effective vertical conductivity of water plus wall (§4.7, a configured field with a default of
1.0 W/(m K)): 1.06 W/K for N = 5 and 2.13 W/K for N = 10 on the preset tank.

### 4.3 The step: upwind transport, conduction, loss, then buoyancy

Every quantity of the step is computed from the start-of-step temperatures `T0_1 … T0_N`, the coils' supplies and
flows (inputs), the demand (input) and the surrounding temperature (input).

**The tap valve on the top layer.** The tap draws from layer N, whose supply temperature is `T0_N`, a committed
value, so the valve of stage C has a closed form and no local iteration (the mixed class's `solve_tank_step` solves
the valve on the step mean, which the stratified class does not have):

- `T0_N > T_warm` (40 °C): `m_hot = m_d (T_warm − T_cold) / (T0_N − T_cold)`, the heat drawn equals the demand;
- `T_cold < T0_N ≤ T_warm`: `m_hot = m_d`, all of the demand passes unmixed, the shortfall is
  `ThermalEnergyUnmetDHWInWattHour` as in the mixed class;
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

### 4.4 Validity: the Courant number, checked at run time

The explicit upwind scheme carries, in one step, at most one layer's mass across a boundary: with `Co_k = 1` layer
k ends exactly at its upstream neighbour's start temperature (a plug shift of one layer); with `Co_k > 1` it ends
beyond it, which no mixing can produce. Conduction and loss are explicit too, with the condition
`2 UA_cond dt / C_k ≤ 1`, which is 0.01 at N = 20 and 60 s on the preset tank and never binds in practice; it is
refused at build, since it depends on the configuration and the step length only.

**The refusal (D6).** In every pass of every step, after the flows are read and before anything is published, the
layer step computes each layer's outflow and fails the run at the first layer whose outflow exceeds its mass. There
is no check at build against the step length: the flows are the generators' and the tap's, known only in the run.
The error, `LayerFlowExceedsLayerMassError` (a `hydronics.HydronicsError`), names

- the port whose flow did it (the primary coil, the secondary coil or the tap) and its flow in kg/s;
- the water that flow moves in one step, in kg, against the layer's mass in kg (and N and the tank's mass);
- the step length in seconds and the step's index;
- the largest N that would hold that flow at this step length, `floor(M / (m dt))`, or "none" below 2;
- the remedies: fewer layers, a shorter step, or the mixed cylinder `SimpleDHWStorage` for this step length; and the
  bead hisim-stratified-storage-coarse-0392, where the coarse model that lifts the limit is designed.

Example: `StratifiedDHWStorage 'DHWStorage': the primary coil moves 0.369 kg/s, 332.1 kg in a 900 s step, through
layers of 49.6 kg (N = 5, 248 kg). The explicit layer model holds only where one step moves at most one layer; no
N >= 2 holds this flow at 900 s. Use fewer layers, a shorter step, or SimpleDHWStorage at this step length; the
coarse model for long steps is bead hisim-stratified-storage-coarse-0392.`

The check is one pass over N layers in the hot path, no allocation; a flow that moves exactly one layer passes.

**The flows the twins move**, measured on 2026-11-25 on `household_heatpump_solar_thermal_building_sizer`
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

So: the stratified cylinder as decided holds at 60 s with N up to about 10 for the twins' machines and up to 5 for a
heat pump of 15 kW; at 120 s with N = 5; and not at 300 s or above for any cylinder a heat pump or a boiler charges.
A file that states the stratified class at 900 s fails in the first charge, with the message above. The coarse
bead inherits this: its switch should consider the Courant number rather than `dt`.

### 4.5 Ports, energy ports and the fluid loops

Both classes declare the channels and loop tags of hydronic stage E and #1122. Per coil, a `FluidLoop` with
`supplies=False`, `pumps=False`, `may_cool=False`: `temperature_in` (the generator's supply), `flow_in` (its flow),
`temperature_out` (the return) and `flow_out` (the flow passed through unchanged). What differs is what the value
carries:

| Output | The mixed cylinder | The stratified cylinder | Reader |
|---|---|---|---|
| `StepMeanWaterTemperatureToHeatGeneratorInCelsius` (renamed by D9) | the step mean `T̄` | `T0` of the primary coil's outlet layer, a start-of-step value | the backup, as its return; it books `m c (T_sup − T_ret)` |
| `StepMeanWaterTemperatureToSecondaryHeatGeneratorInCelsius` (renamed by D9) | `T̄` | `T0` of the secondary coil's outlet layer | the collector, as its return |
| `WaterMassFlowRateToHeatGenerator`, `…ToSecondaryHeatGenerator` | the flow in, unchanged | the same | the loop's mass check (#1122) |
| `WaterTemperatureFromHeatGenerator`, `…FromSecondaryHeatGenerator` (outputs) | the supply, echoed | the same | plots |
| `WaterTemperatureAtStartOfStepInCelsius` | `T0` | the upper sensor pocket's `T0` (§4.6) | the backups' controllers (on/off, hysteresis) |
| `WaterTemperatureAtEndOfStepInCelsius` | `T_end` | the upper pocket's `T_end` | the backups' part-load controls |
| `LowerSensorWaterTemperatureAtStartOfStepInCelsius`, `…AtEndOfStepInCelsius` (new, both classes) | `T0`, `T_end` | the lower pocket's `T0` and `T_end` | the solar controller: the differential switch-on against the collector sensor, the full-tank stop, the pump's part-load target; the collector's inlet sensor |
| `WaterTemperatureOfLayer<k>AtStartOfStepInCelsius`, k = 1 … N (stratified only) | – | N outputs | plots, tests; no controller |
| `ThermalPowerFromHeatGenerator`, `…FromSecondaryHeatGenerator`, W | `m c (T_sup − T̄)` | `m c (T_sup − T0_k_out)` | the energy balance (the tank's side of each loop), KPIs |
| `ThermalPowerConsumptionDHW`, W, `PRODUCTION` to `HOT_WATER_USE` | `m_hot c (T̄ − T_cold)` | `m_hot c (T0_N − T_cold)` | the balance, the cost engine's useful heat, KPIs |
| `ThermalEnergyUnmetDHWInWattHour` | on `T̄` | on `T0_N` | the KPI "Unmet DHW heat demand" |
| `ThermalEnergyIncreaseInStorage`, Wh, `STORED_CHANGE` | `C (T_end − T0)` | `Σ_k C_k (T_end,k − T0_k)` | the balance |
| `ThermalEnergyInStorage`, Wh | `C T_end` | `Σ_k C_k T_end,k` | KPIs |
| `HeatLossInWatt`, `HeatToHeatedZoneInWatt`, `LOSS` | `UA (T̄ − T_amb)` | `Σ_k UA_k (T0_k − T_amb)` | the balance, the building |
| `StandbyTemperatureLoss`, K | `loss / C` | `loss / C`, on the whole tank | the assembly bounds |
| `WaterMassFlowRateOfDHW`, `HotWaterMassFlowRateFromStorageInKgPerSecond` | the demand, `m_hot` | the same | plots, KPIs |

In the stratified cylinder the generator's return is a committed start-of-step value, so the hot-water chain no
longer iterates between the generator and the cylinder (with the mixed one the step mean takes a few passes, coupling
spec §6); the generators' supply caps at their set temperatures (coupling spec §5) stay as they are and act on a
return that does not move within the step. The collector reads the lower pocket's `T0` as
`TemperatureCollectorInletAtStartOfStepInCelsius` and the secondary coil's outlet `T0` as its return; with the
default heights both are the bottom layer.

**The energy balance and the fluid-loop checks.** The post-run balance (`hisim/postprocessing/energy_balance/check.py`)
books each coil's loop once from the loop's flow, supply and return, by the supplier's sign (fluid loops spec §3.4):
unchanged, and the tank's side equals the generator's side on every pass, since both compute `m c (T_sup − T0_k_out)`
from the same three published values. The tank's own balance `Σ coils − tap − loss = ΔU` closes by §4.3. The mass
check at every member (`flow_out = flow_in`) holds because the tank passes every flow through unchanged; the
heating-only check holds because a coil's supply is never below the return it is capped against; the energy check at
the member (stage 4 of #1122) holds because the booked `ThermalPowerFrom*` is computed from the member's own
published quantities. The Sankey sees the same nodes as with the mixed cylinder; layers are not nodes.

### 4.6 Sensors: two pockets, wired by the controllers

The stratified cylinder has two sensor pockets at configured heights, an upper and a lower one (D11), and publishes
each pocket's start-of-step and end temperature (§4.5). A real bivalent cylinder has its pockets at the heights of
its coils, and the installer puts the backup's sensor in the upper one and the solar controller's sensor in the lower
one. Here that is the wiring: the backups' controllers (`GenericBoilerController`,
`MoreAdvancedHeatPumpHPLibControllerDHW`, `ElectricHeatingController`, the substation's controller) keep their
default connections to `WaterTemperatureAtStartOfStepInCelsius` and `WaterTemperatureAtEndOfStepInCelsius`, which
carry the upper pocket; the solar controller's default connections (`solar_thermal_system.py:1511` and `:1518`) and
the collector's inlet sensor (`:925`) move to the lower pocket's outputs, on both classes. On the mixed cylinder the
lower pocket repeats the single temperature, so the solar twins' runs with the mixed class do not change. A file may
wire any controller to either pocket.

With the defaults of §4.7 and N = 5, the upper pocket (0.7) is layer 4, the lower pocket (0.1) layer 1. The backup
charges when layer 4 falls below its aim less its hysteresis; the collector's pump switches on when the collector's
sensor reads 7 K above layer 1 and stops when layer 1 starts a step above 80 °C. The temperature check
(`check_converged_mean_water_temperature`, 0 to 90 °C) applies to the hottest and the coldest layer.

The tank does not know which controller reads which pocket, so D3 holds: a priority rule (bead hisim-bq3h) is a
change to the controllers' set points or an L2 signal, never to the tank.

### 4.7 Configuration, presets, sizing

`StratifiedDHWStorageConfig` (`MAIN_CLASS = "hisim.components.stratified_dhw_storage.storage.StratifiedDHWStorage"`)
has the fields of `SimpleDHWStorageConfig` (§4.1, with the same sizing laws) and these plain fields, which nothing in
the surrounding system decides:

| Field | Default | Meaning and source |
|---|---|---|
| `number_of_layers` | 5 | N, at least 2. 5 is the recommended value (§4.4: `Co ≤ 0.5` at 60 s for the twins' machines, 0.87 for a 15 kW heat pump). |
| `primary_coil_inlet_height_as_fraction_of_height` | 1.0 | the backup coil's top |
| `primary_coil_outlet_height_as_fraction_of_height` | 0.5 | the backup coil's bottom: the backup heats the upper half, the ready volume of a bivalent cylinder |
| `secondary_coil_inlet_height_as_fraction_of_height` | 0.25 | the solar coil's top |
| `secondary_coil_outlet_height_as_fraction_of_height` | 0.0 | the solar coil's bottom |
| `upper_sensor_height_as_fraction_of_height` | 0.7 | the backup's pocket, in the ready volume above the backup coil's middle |
| `lower_sensor_height_as_fraction_of_height` | 0.1 | the solar pocket, at the solar coil |
| `effective_vertical_conductivity_in_watt_per_meter_kelvin` | 1.0 | water's 0.6 W/(m K) (VDI Heat Atlas) plus the steel wall's share; store tests after EN 12977-3 give 0.6 to 2 W/(m K) for cylinders with internal coils |

The heights and the conductivity are estimates from the layout of bivalent solar cylinders (the backup coil in the
upper half, the solar coil in the lower third) and from the literature named; the preset's docstring and the field
comments say so (modelling guide §5), and bead hisim-stratified-cylinder-defaults-1eff replaces them by sourced
values (D8). The tap's draw at the top and refill at the bottom are not configurable: every cylinder does that.

Presets: `preset_standard(name)` on the stratified class is the bivalent cylinder above, with the volume and the
location left `AUTO` as the mixed class's `preset_standard` leaves them, so a file switches classes and keeps
`preset: standard`. `SimpleDHWStorageConfig.preset_standard` is untouched.

The refusals at build (`StratifiedDHWStorage.build`): N below 2 or not an integer (a cylinder of one layer is the
mixed class's job, and a layered tank of one layer would be a third model, the mixed node with a start-of-step
valve, that nobody asked for); a height outside [0, 1]; a coil whose outlet lies above its inlet (a charging coil is
fed at its top; a file would state the reverse only by mistake); the conduction condition of §4.4. Nothing about the
step length (D6).

Twins and assemblies: the recorded twins and the assemblies keep the mixed class (§5). The `partner:` lists of the
needs that name `SimpleDHWStorage` (`dhw_temperature` of the five heating assemblies, `cylinder_temperature` of
`heating/solar_thermal`, `cylinder_heat` and the circuit ends of `test_partners.yaml`) list both classes, so a system
that states the stratified class binds; the `cylinder_temperature` need binds the solar controller to the lower
pocket's two outputs. A stratified assembly (`dhw/stratified_cylinder`) is not written until the coarse model exists,
since the assembly tests run at 900 s (§8, Q1).

### 4.8 Where the code goes

**The component.** A folder, one per component (seams spec, `module_graph.yaml` group `components`):
`hisim/components/stratified_dhw_storage/` with `__init__.py`, `storage.py` (`StratifiedDHWStorageConfig`,
`StratifiedDHWStorage`, `LayerFlowExceedsLayerMassError`) and `layers.py`. The class inherits `SimpleWaterStorage`
from `hisim.components.simple_water_storage`, as the mixed cylinder does: the surroundings inputs, the loss outputs
and the geometry are every water storage's and already have two callers there. The graph allows the import (a module
may import its own group); it is an inheritance import, not a wiring one, so hisim-default-connection-names-497c does
not remove it. No other code is shared with the mixed class: the coil channels, the loops, the tap outputs and the
default connections are declared again in `storage.py`, and T12 keeps them equal. A common base class for those
declarations was considered and rejected: it would need a home in `simple_water_storage.py` (shared code under
`hisim.components.shared` may not import the component model), it would make the mixed class a dependency of the
stratified one for its port list, and it would not stop an override from drifting, which T12 does anyway.

**The layer step.** `layers.py` holds the pure layer step, a subcomponent in the sense of
`component_design_principles.md` §C with one caller, so it lives in its caller's folder and not in
`hisim/subcomponents`: a frozen dataclass `StratifiedLayers` built once from the configuration (N, `M_k`, `C_k`,
`UA_k`, `UA_cond`, the port layers, the tap temperatures, `dt`) with
`step(t0_per_layer, coil_streams, demand, t_amb) -> LayerStep` (the end temperatures, the heat per coil, the tap's
draw and unmet share, the loss, `ΔU`), and the pure helpers `through_flows`, `explicit_layer_balance`,
`buoyancy_merge`, `layer_of_height`, each with its own tests (T1 to T5, T7). The Courant check of D6 runs in
`step`, before the balance. The state is `StratifiedTankState` with a tuple of N start temperatures; `i_save_state`
and `i_restore_state` copy it whole.

**The tap valve.** `ValveDraw`, `mixing_valve_draw`, `ValveTemperatureOrderError` and `NegativeDemandError` move
from `SimpleDHWStorage` to `hisim/hydronics.py` (a leaf; the valve's errors already subclass
`hydronics.HydronicsError`), where both classes call them. This is the one code change to the mixed class besides the
two lower-pocket outputs; its behaviour does not change. `hydronics.py` gains nothing else: the upwind term is
`circuit_power_w` and the constants are `Water`.

**Default connections.** The simulator keys a target's default connections by the source's class name
(`Component.default_connections[source_classname]`). Ten methods on six partners name `SimpleDHWStorage` today:
`GenericBoiler` and its DHW controller (`generic_boiler.py:1854`, `:3147`), `MoreAdvancedHeatPumpHPLib` and its DHW
controller (`heat_pump.py:1301`, `controller_dhw.py:319`), `SolarThermalSystem` and its controller
(`solar_thermal_system.py:913`, `:1506`), `DistrictHeating` and its controller (`generic_district_heating.py:528`,
`:1166`), `ElectricHeating` and its controller (`generic_electric_heating.py:441`, `:1018`). Until
hisim-default-connection-names-497c names partners by string, each of them gets the entry for the new class: the
existing method takes the storage class name as a parameter and is registered twice, once per class, with the same
output names; the import of the stratified module that this needs is one more wiring import for 497c to remove. The
stratified class declares its own `get_default_connections_from_*` for the same six partners, with the same lists as
the mixed class. T12 checks both directions.

**The rest of the system.** `hisim energy-system describe hisim.components.stratified_dhw_storage.storage.StratifiedDHWStorage`
works from the dotted path, as for every class; `hisim energy-system schema` finds the class by walking the package
(`schema_classes.py`); the energy-system loader imports the class path a file states (`classes.py`); the recorder
writes the class of the instance a Python setup built, so a setup that builds `StratifiedDHWStorage` records it.
The economics adapter's cost facts (`adapter.py:163`, by class name) and useful-heat source (`:607`) get the entry
`"StratifiedDHWStorage"` with the same facts, until hisim-economics-ports-25a0 removes those tables. RenoVisor's
equipment register (`renovisor/economics.py:393`) keeps the mixed class until RenoVisor switches (bead
hisim-stratified-cylinder-defaults-1eff names it).

Cost in the hot path: O(N) arithmetic per pass with N ≤ 20, no iteration with the generators; a measurement of the
passes per step on the solar twins at 60 s with each class comes with PR 5 (§9).

### 4.9 Interaction with the part load

At and below the threshold every ratio is exactly 1 (part-load spec, decision 16), so a stratified cylinder at 60 s
or 120 s never sees a fraction. The controllers' `PartLoadControl` reads the end temperature of the pocket it is
wired to; the backups' controllers read the upper pocket's, the solar controller the lower pocket's. Above the
threshold the stratified cylinder fails in its first charge (§4.4), so the part-load rules and the layer model never
meet until the coarse model of hisim-stratified-storage-coarse-0392 decides how a fraction and a pocket's end
temperature relate. Decision 7 of the part-load spec, "any tank model, a stratified one included, can publish" the
controlled end temperature, is met by the pockets.

## 5. What changes and what does not

- **Results.** No recorded energy system, golden or RenoVisor run changes: every one of them keeps
  `SimpleDHWStorage`, whose model is untouched. The two lower-pocket columns appear on every run with a mixed
  cylinder, each repeating an existing column; no KPI moves (PR 3 says so in its description). A run that states the
  stratified class at 60 s is expected to raise the collector's heat (its coil meets the bottom layer, near the mains
  temperature after a draw, instead of the 55 to 70 °C mean), lower the backup's hot-water heat by about the same
  amount, change the standby loss little (a hotter top and a colder bottom), keep the tap's delivered heat, and may
  raise the unmet hot water slightly (the top layer alone serves a draw, and the backup charges the upper half only).
  The direction is stated here; the numbers are measured by T10 and written into §7.
- **Public interfaces.** A new component class with its configuration and preset; the two lower-pocket outputs on
  both classes; the layer outputs on the stratified one; the `partner:` lists of the assemblies; the buffer's field
  `heat_exchanger_is_present` goes (D7), which touches `hisim/energy_system_v3.schema.json` and two tests, no recorded
  file. The rename of the return outputs (D9) is a column rename in every DHW twin's results and gets its own PR with
  the heads-up of `results_and_interfaces.md` §5.
- **Goldens and twins.** None re-blessed by PRs 1 to 5. PR 6 (the rename) re-records the twins and re-blesses the
  goldens for the renamed columns, no value changes. The twins and the RenoVisor requests switch to the stratified
  class only together with the coarse model (RenoVisor runs at 900 s, the goldens at 60 s and 900 s), with re-blessed
  goldens and a heads-up; until then #94's remaining gap stays, and the coarse model is on #94's critical path.

## 6. Verification

Unit tests of the pure functions, `base`, in `tests/test_stratified_dhw_layers.py`:

- **T1 energy closure per step.** Random profiles, flows, supplies and draws, N in {2, 5, 10, 20}:
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
- **T6 the same standby loss.** A uniform tank with no flows and no draw loses, per step, the same heat in
  `StratifiedLayers.step` as `MixedNode.step` within the order of `a` (the mixed node's loss is on the step mean,
  the layers' on `T0`), and exactly the same `Σ UA_k` is built from the same volume and U.
- **T7 the Courant refusal at run time.** A flow that moves more than one layer in one step raises
  `LayerFlowExceedsLayerMassError` in that step, naming the port, the flow, the layer mass, the step length, the
  largest N that would hold, and the bead; a flow of exactly one layer passes; the check runs in the pure step, so
  the test needs no simulator.
- **T8 refusals at build.** N below 2 or not an integer, a height outside [0, 1], a coil whose outlet lies above its
  inlet, the conduction condition; and no refusal for a step length of 900 s at build (D6).

Component tests, `base`, in `tests/test_stratified_dhw_storage.py` and `tests/test_energy_balance_storages.py`:

- **T9 the balance report.** The stratified cylinder with N = 5 in a day's run at 60 s: the energy-balance check
  reports it closed on every step, and the loop checks of #1122 pass (mass at every member, heating only, the energy
  check at the member).
- **T11 the sensor wiring.** The default connections wire the backups' controllers to the upper pocket and the solar
  controller and the collector's inlet sensor to the lower one, on both classes; a file may wire either; the
  temperature check covers the hottest and the coldest layer.
- **T12 the shared interface** (generic, `tests/test_dhw_cylinder_interface.py`). Builds `SimpleDHWStorage` and
  `StratifiedDHWStorage` from `preset_standard` through `tests/component_sweep.py` and checks §4.1 item by item: the
  inputs (name, load type, unit, mandatory, `allow_unconnected_mandatory`, loop tag); the outputs of the mixed class
  all present on the stratified one with the same load type, unit, energy port, loop tag, postprocessing flags and
  display; every extra output of the stratified class matches `WaterTemperatureOfLayer<k>AtStartOfStepInCelsius`;
  the two loops' declarations; the cylinder's own default-connection lists per partner class; for every component
  class the sweep builds, its default connections keyed by `SimpleDHWStorage` and by `StratifiedDHWStorage` are the
  same list, output for output; `cost_relevance`; the adapter's cost facts and useful-heat row for both class names;
  the shared configuration fields (name, type, default, sizing rule) and that both classes offer `standard`. One
  test, no allowlist: the two classes cannot drift without failing it.
- **T13 the run-time refusal in a run.** The stratified cylinder in the heat pump + solar twin at 900 s fails in
  the first charge with `LayerFlowExceedsLayerMassError`, and the message names the mixed class as a remedy
  (`system_setups`, one hour of simulated time, so the test is cheap).

Twin tests, `system_setups`, in `tests/test_stratified_cylinder_solar_twins.py`:

- **T10 interchangeability and the solar twins' yield.** `household_gas_solar_thermal_building_sizer` and
  `household_heatpump_solar_thermal_building_sizer`, the first week of June 2021 at 60 s, run from their recorded
  energy-system files once as recorded and once with the cylinder's class path replaced by the stratified class and
  nothing else changed. Both runs finish; with the stratified class the collector's heat rises, the backup's hot-water
  heat falls, the tap's delivered heat agrees within 1 %, the unmet hot water does not rise by more than a stated
  bound, every step closes, the loop checks pass, and the passes per step are reported for both classes. The year's
  figures are measured by hand and written into §7.

Deliberately not tested here: the agreement of coarse steps with 60 s (`modelling_guide.md` §3), which is the coarse
model's test under hisim-stratified-storage-coarse-0392; the solar priority (hisim-bq3h).

## 7. Measured results

2026-11-25, the flows of §4.4 (one June day of the heat pump + solar twin at 60 s). The yields of T10 and the
passes per step are filled in by PR 5.

## 8. Decisions of 2026-11-25, and what is still open

The eight open questions of the draft of 2026-11-25 were decided by the owner the same day and are D5 to D11 of
§2: Q1 (above the threshold) and Q2 (a flow exceeding a layer) are D6, one run-time refusal; Q3 (which storages, the
buffer's dead path) is D7; Q4 (N = 1 keeps the mixed node) is moot under D5, the stratified class requires N ≥ 2;
Q5 (the defaults) is D8; Q6 (the rename) is D9; Q7 (two pockets) is D11; Q8 (hisim-bq3h to P2) is D10. Decided by
this spec under D5: the lower pocket is on both classes (§4.1); the two classes share the valve and the
`SimpleWaterStorage` base and nothing else (§4.8); `preset_standard` on both (§4.7).

Still open, each with a recommendation:

- **Q1. Does the stratified cylinder get a recorded energy-system file of its own before the coarse model exists?**
  Context: every recorded twin is a golden pair at 60 s and 900 s (`results_and_interfaces.md` §1), and the
  stratified class fails at 900 s by design (D6), so a recorded stratified twin cannot join the gate. Options: (a) no
  recorded file; T10 runs the two solar twins with the class swapped, and a user who wants the stratified cylinder
  copies a twin and changes the class path; (b) a recorded file `household_heatpump_solar_thermal_stratified`
  without a golden pair, as the example to copy, whose 900 s run fails with the message of §4.4. Recommendation: (a)
  now, since a file in `energy_systems/` that fails at the step length RenoVisor uses invites the wrong copy; (b)
  with the coarse model, when the file runs at both step lengths.
- **Q2. A stratified buffer: a bead now or none?** The owner asked. Recommendation: none now. D7 records that a
  stratified buffer would be its own component on the pattern of D5; nobody asks for one (the buffer's dead path has
  no user), and the part-load mode of the buffer above 600 s rests on linear end temperatures (part-load spec,
  decision 21), which a buoyancy merge breaks, so a stratified buffer needs the coarse model first. A bead is filed
  when a use exists, under hisim-hydronics-fxix.

## 9. Beads and pull requests

Epic hisim-hydronics-fxix.

| Bead | Content | PR |
|---|---|---|
| hisim-hydronics-fxix.1 (P3) | this spec: the buffer's path deleted, the layer step, `StratifiedDHWStorage` with the interface test and the default connections, the controllers' pocket wiring and the twins' measurement; closes with PR 5 | PR 1 to 5 |
| hisim-dhw-return-outputs-rename-653d (P3, new, D9) | the rename of the two return outputs on both classes, the twins re-recorded, the goldens re-blessed, the heads-up on renovisorissues | PR 6 |
| hisim-stratified-cylinder-defaults-1eff (P3, new, D8) | sources the layer count, the coil and pocket heights and `λ_eff` from manufacturer data sheets, VDI 6002, EN 12977 or similar, before RenoVisor uses the stratified cylinder; names RenoVisor's equipment register | – |
| hisim-stratified-storage-coarse-0392 (P3, blocked by hisim-hydronics-fxix.1) | the coarse model: about the same totals as the fine model where the fine one is not valid; the twins and RenoVisor switch to the stratified class with it | – |
| hisim-bq3h (P2 since D10, after the stratified cylinder) | solar priority in the controllers, measured on the stratified cylinder | – |
| hisim-report-states-defaults-41e9 (P3) | the mapping report names the cylinder's volume; unchanged by this spec | – |

The open questions of §8 get `owner-decision` beads only for what stays open after the review of this document
(spec template: an open question is a bead as well as an entry here).

The pull requests, each small and stacked on the one before (AGENTS.md, "Pull requests and merging"):

1. **The buffer's stratification path goes** (D7): `heat_exchanger_is_present`, the mixing factor and the
   mass-mixing law, their tests and the schema entry; no result changes.
2. **The layer step** as a pure subcomponent in `hisim/components/stratified_dhw_storage/layers.py`, with T1 to T8,
   not yet a component; the tap valve moves to `hydronics.py` and the mixed class calls it there; no result changes.
3. **`StratifiedDHWStorage`**: the configuration and `preset_standard`, the component in `storage.py` with the
   state of N temperatures, the two pockets, the layer outputs, the refusals at build, the lower-pocket outputs on
   both classes, the default connections in both directions (the ten partner methods registered for both classes),
   the economics adapter's entries, the `partner:` lists of the assemblies, T9, T12 and T13. The two new columns on
   the mixed class are named in the description; no KPI moves.
4. **The controllers' pocket wiring**: the solar controller's and the collector's default connections to the lower
   pocket on both classes, the `cylinder_temperature` need of the solar assembly, T11; no result changes (on the
   mixed class the lower pocket repeats the single temperature).
5. **The twins' measurement at 60 s with both classes**: T10, the passes per step with each class, the year's
   figures in §7. Closes hisim-hydronics-fxix.1.
6. **The rename of the return outputs** (D9, hisim-dhw-return-outputs-rename-653d): both classes, every reader
   inside HiSim, the assemblies' bounds, the twins re-recorded, the goldens re-blessed, the heads-up on
   renovisorissues per `results_and_interfaces.md` §5.
