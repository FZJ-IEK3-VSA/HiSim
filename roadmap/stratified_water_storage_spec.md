# Spec: the stratified hot-water cylinder is a second component, interchangeable with the mixed one: N explicit layers with real flows, sub-stepped inside the simulation step by the combined rule

Status: decided (owner decisions D1 to D11 of 2026-11-25, the two questions of §8 of 2026-11-26, and the decisions of
2026-11-28 after the sub-stepping prototype: D1, D2, D6 and D8 revised, D12 to D15 added), nothing built. Owner: Noah
Pflugradt. Epic: hisim-hydronics-fxix (bead hisim-hydronics-fxix.1; the rename is hisim-dhw-return-outputs-rename-653d;
the sourcing of the defaults is hisim-stratified-cylinder-defaults-1eff; port heights at fractional positions within a
layer are hisim-stratified-fractional-heights-9b4e; the charge-end signal is hisim-stratified-charge-end-signal-5e39,
on PR #1299's branch; the coarse model hisim-stratified-storage-coarse-0392 is closed, superseded by D1). Builds on:
`roadmap/hydronic_coupling_spec.md` (§4.5, §5.3, §9.5, D4, D5), `roadmap/hydronic_part_load_spec.md` (decisions 3, 7,
9, 16, 23), `roadmap/two_mode_buffer_spec.md`, `roadmap/fluid_loops_spec.md` (#1122), the start-of-step node of #1009,
the seams spec (`roadmap/hisim_seams_spec.html`, `system_docs/module_graph.yaml`), and renovisorissues #94.

Code is cited against `origin/economics-ports-record` at 26f691ff1. The prototype's measurements (§7) come from the
untracked prototype in the worktrees `/home/noah/hisim/agent-tmp/wt-strat-substep` (detached at 230636791) and
`wt-strat-substep2` (detached at f7405fae0, the heat-pump stack with the hot-water part-load fraction of PR #1299);
the evidence files are named in §7.

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
strategy the owner placed in the controllers on 2026-10-10). The prototype of §7 measured the cylinder's part on the
six #94 cases: the stratified cylinder raises the collector's heat by 61 to 110 % and the solar share from 19 to 38 %
to 41 to 62 % (N = 5), and to 45 to 67 % at N = 20; the rest is the weather file (D15), not the cylinder.

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

**What the replacement must respect.** The owner decided on 2026-11-25 (D1 to D11 below) that the stratified
cylinder is a second component beside the mixed one, interchangeable with it, as TRNSYS offers a fully mixed tank
type and a stratified tank type; that the mixed cylinder stays as it is; and that the model is the explicit
start-of-step layer model recorded in bead hisim-hydronics-fxix.1 on 2026-10-17. That decision first held the model
to one explicit step per simulation step, valid where the water a circuit moves in one step is at most one layer,
with a separate coarse model for the other steps. A prototype evaluated over full years on the two solar twins and
the six #94 cases (§7) showed that the layer model sub-stepped inside the simulation step holds the 60 s run's totals
at 900 s and 3600 s, provided the sub-step count cannot jump between the passes of a step. On 2026-11-28 the owner
revised D1: the stratified cylinder sub-steps its own water transport by the combined rule (§4.4), as the one decided
exception to the rule that components do not sub-step; the coarse model is not needed (D2), the run-time refusal
goes (D6), and N = 10 is the default (D8). This spec is that model and the new component.

## 2. Decisions

**D1 — Components never iterate or sub-step in time internally, with one decided exception: the stratified cylinder
sub-steps its own water transport** (owner, 2026-11-25; revised 2026-11-28 after the prototype of §7). The general
rule stands for every other component: no generator, controller, building or store iterates or sub-steps inside
`i_simulate`, and the simulator's passes are the only iteration. The exception is for the storage's internal
transport only. Within one simulation step of length `dt`, the layer step of §4.3 runs `n` sub-steps of `dt/n`
with the pass's inputs (the coils' supplies and flows, the demand, the surrounding temperature) held constant, and
publishes the sums over the sub-steps (heats, loss, unmet) and the means (the returns). The combined rule sets `n`
in every pass (§4.4):

- `n = 1` in a pass where no water moves (every coil flow and the tap's demand are zero);
- otherwise `n = max(ceil(dt / 60 s), ceil(max_k Co_k), n_max)`, with `Co_k` the layer Courant numbers of the pass
  over the whole step and `n_max` the running maximum of `n` over the earlier passes of the same step; `n_max` is
  reset to 1 at the start of each step (in `i_save_state`) and is not rolled back by `i_restore_state`.

Why each term, from the prototype (full years on the two solar twins, §7.3). The sub-stepping that bead
hisim-hydronics-fxix.1 recommended on 2026-10-17, `n = ceil(Co)` alone, makes the tank's answer jump wherever `n`
changes; every generator sets its flow from that answer, so where a step's fixed point lies inside a jump the passes
alternate forever: 4 of 4 coarse runs failed (gas 900 s at step 2435 with `n` 3↔2, gas 3600 s at step 414 with
2↔3, heat pump 900 s at step 995 with 5↔6, heat pump 3600 s at step 587 with 1↔2). The 60 s floor sets the accuracy:
sub-steps of at most 60 s hold every tank-side total within 1 % at 900 s and 1.4 % at 3600 s of the 60 s run
(N = 5), whereas the smallest stable count (the upward-only rule: `n = ceil(Co)` that may only grow within a step)
is 1 on 78 to 96 % of the steps and books the collector 1.2 to 2.6 % too much heat, because the collector then reads
the bottom layer's start-of-step temperature for the whole step, the lag of the single-step model. The Courant term
sets stability only. The running maximum guards against a jump where even 60 s sub-steps move more than one layer,
which happens at 60 s steps too: at N = 20 the twin's heat-pump charge (hplib's flow, up to 0.59 kg/s) moves up to
2.9 layers in 60 s, at N = 10 about 1.4, so there `n` follows the flow again and could alternate (sub-steps of 300 s,
whose floor of 3 lay below the charge's Courant number of 5, locked up the heat-pump twin at 900 s at the same step
995). With the memory `n` cannot fall within a step, so after finitely many passes the tank is a continuous function
of its inputs and the passes converge. Measured: the combined rule converged on 26 of 26 full-year runs, matches
fixed 60 s sub-steps within 0.23 percentage points on every total, and costs about a third less of the extra loop
time.

Consequence: one model at every step length, so the coarse model of D2 is not needed and the refusal of D6 goes; the
published returns are means over the sub-steps (§4.5); within a sub-step the model is the explicit
start-of-sub-step layer model, unchanged.

**D2 — No second model for coarse steps** (owner, 2026-11-25: "a storage may have a second, optional model for
coarse steps, as the part-load pattern does"; superseded 2026-11-28). The sub-stepped layer model serves every step
length (D1), so the coarse model is not designed. Bead hisim-stratified-storage-coarse-0392 is closed with this
spec, its close reason naming D1 and §7. The two-mode pattern of the space-heating buffer
(`roadmap/two_mode_buffer_spec.md`) is untouched.

**D3 — Priority between the collector and the backup is control strategy and stays in the controllers** (owner,
2026-10-10, confirmed 2026-11-25). The sensor heights belong to the controllers' reading of the tank. Consequence:
the tank publishes sensor temperatures at configured heights (§4.6) and decides nothing; which sensor a controller
reads is its wiring; the priority logic itself stays with bead hisim-bq3h (D14).

**D4 — Energy closes per step for the tank as a black box** (owner, 2026-11-25). Consequence: every heat the tank
publishes comes from the same sub-steps; `Σ coils − tap − loss = ΔU` to float precision on every sub-step and,
summed, on every step (`tests/test_energy_balance_storages.py` and the fluid-loop checks of #1122 keep checking
it), and the buoyancy merge of §4.3 conserves energy exactly. Measured in the prototype: the residual is below
1e-10 Wh per step over every full-year run (§7).

**D5 — Two separate components, interchangeable, as TRNSYS's mixed and stratified tank types** (owner, 2026-11-25).
`SimpleDHWStorage` stays exactly as it is: one mixed node, no `number_of_layers`, its results and goldens unchanged.
A new component class `StratifiedDHWStorage` is the stratified cylinder. Both expose the same interface: the same
input and output names, load types, units, energy ports and loop tags, the same default connections in both
directions, so a system file or an assembly switches by changing the class name, and everything wired to the cylinder
works with either (§4.1). Consequence: one generic test pins the shared interface (T12); the two classes share no
model code, only the pure pieces both need (§4.8); every partner that names `SimpleDHWStorage` in a default
connection names `StratifiedDHWStorage` too (§4.8). The old Q4 (N = 1 keeps the mixed node) is moot: the stratified
class requires N ≥ 2 (§4.7).

**D6 — No refusal at run time; refusals at build for configuration errors only** (owner, 2026-11-25: "the fine
model's validity is checked at run time"; revised 2026-11-28). The combined rule holds any flow (a flow that moves
many layers per step is a larger `n`: at most 174 sub-steps in a 3600 s step at N = 20 in the prototype), so the
run-time refusal `LayerFlowExceedsLayerMassError` and the check of every step's flows against the layer mass are
dropped. What the build refuses (§4.7): N below 2 or not an integer, a height outside [0, 1], a coil whose outlet lies
above its inlet, and the conduction stability condition `2 UA_cond dt / C_k ≤ 1` evaluated at the full step length,
since a pass without flow runs one sub-step of the whole step (N = 40 at 3600 s gives 2.4 and is refused; N = 20
gives 0.59, N = 10 gives 0.15). The evidence supports no other refusal: no flow failed under the combined rule in 26
full-year runs. Consequence: the stratified cylinder runs at 60 s, 900 s and 3600 s; the recorded solar twins and
RenoVisor's solar houses use it (D12).

**D7 — The cylinder now; the buffer's dead path goes** (owner, 2026-11-25; the old Q3). The space-heating buffer's
`heat_exchanger_is_present=False` path, its mixing factor and its mass-mixing law are deleted in the first PR. A
stratified buffer is its own component later, on the pattern of D5: bead hisim-stratified-buffer-eb26 (P2, §8 Q2).

**D8 — N = 10 layers; the other defaults are accepted as presets marked as estimates** (owner, 2026-11-25; N
revised 2026-11-28; the old Q5). Ten layers, the backup coil from 1.0 to 0.5 of the height, the solar coil from
0.25 to 0.0, the pockets at 0.7 and 0.1, `λ_eff = 1.0 W/(m K)`. Measured layer convergence (fixed 60 s sub-steps,
900 s, full year, the RenoVisor case hp4 on Dublin and the heat-pump solar twin on Aachen, against N = 40, §7.4):
collector heat −9.5 / −7.1 % at N = 5, −3.7 / −3.0 % at N = 10, −1.0 / −0.8 % at N = 20; backup final energy has
converged from N = 10 (hp4, +0.6 %) and N = 20 (twin, +0.7 %) on; N = 40 itself lies about 0.5 % below the
extrapolated limit. Cost over N = 5: the loop takes +11 / +10 % at N = 10 and +43 / +30 % at N = 20, +83 to +118 %
at N = 40 (§7.5). The owner chose N = 10 as the balance of the two: the collector about 3 to 4 % below N = 40 at
a quarter to a third of N = 20's extra cost. Part of the N dependence is geometry, not resolution: the coil and pocket
heights snap to layer boundaries, which move with N; bead hisim-stratified-fractional-heights-9b4e (P3) places them
at fractional positions within a layer, after which fewer layers may do. Measured but not decided (hp4, N = 5, one
change each, §7.2): the backup coil heating the upper 40 % instead of 50 % gives +4.6 % collector heat, a 300 l
cylinder +3.1 %. Consequence: the preset's docstring and the field comments say the heights and the conductivity are
estimates; bead hisim-stratified-cylinder-defaults-1eff sources them (manufacturer data sheets, VDI 6002, EN 12977 or
similar).

**D9 — The return outputs are renamed in their own PR with a heads-up** (owner, 2026-11-25; the old Q6).
`StepMeanWaterTemperatureToHeatGeneratorInCelsius` and `StepMeanWaterTemperatureToSecondaryHeatGeneratorInCelsius`
become `WaterTemperatureToHeatGeneratorInCelsius` and `WaterTemperatureToSecondaryHeatGeneratorInCelsius` on both
cylinder classes, with the recorded twins re-recorded, the goldens re-blessed and the heads-up of
`system_docs/results_and_interfaces.md` §5 on renovisorissues. Since D1's revision the value is again a mean on both
classes: the mixed class's exact mean of its one node over the step, the stratified class's mean over the sub-steps
of the outlet layer's start-of-sub-step temperature, which is the outlet layer's start-of-step temperature when one
sub-step serves the step (§4.5). The rename stays decided, for the buffer's spelling and because the new name states
no time basis, which differs between the classes; each class's output description says what its value is. Until
that PR the stratified class carries the old names, whose descriptions say the value is the mean over the sub-steps.
Bead hisim-dhw-return-outputs-rename-653d, PR 6 of §9.

**D10 — hisim-bq3h (solar priority) is P2, scheduled after the stratified cylinder** (owner, 2026-11-25; the old
Q8; confirmed 2026-11-28, D14). Its effect is measured on the stratified cylinder with the sensors in their pockets.
Recorded on the bead.

**D11 — Two fixed sensor pockets, an upper and a lower one** (owner, 2026-11-25; the old Q7). Each has a height
field and a pair of outputs; the existing sensor outputs carry the upper pocket, so the backups' default connections
stay (§4.6). A third pocket is added when a third reader exists.

**D12 — Only houses with solar thermal get the stratified bivalent cylinder** (owner, 2026-11-28). A solar-thermal
retrofit normally includes a new bivalent cylinder, so its effect belongs to the measure; every other house keeps
the mixed cylinder. Consequence: the three recorded solar twins (`household_gas_solar_thermal`,
`household_gas_solar_thermal_building_sizer`, `household_heatpump_solar_thermal_building_sizer`) state the
stratified class, re-recorded with re-blessed goldens and a heads-up (§5); RenoVisor's two solar base files
(`BaseFiles.WITH_SOLAR_THERMAL`, `hisim/renovisor/translate.py:531`) are those twins, so a house with a collector is
simulated with the bivalent cylinder and a house without one with the mixed cylinder; the mapping report's entry
for the solar-thermal measure says that the measure includes the bivalent cylinder; RenoVisor's equipment register
(`renovisor/economics.py:393`) gets the row for `StratifiedDHWStorage` with the facts of the mixed one. A stratified
cylinder without a collector is not a baseline: the #94 case gas0 with the stratified class halves the standby loss
(125 against 253 kWh/a) because the backup heats only the upper half, which is a different appliance, not a better
model of the same one (§7.2). The twins and assemblies without a collector keep the mixed class.

**D13 — The charge-end signal is accepted for now** (owner, 2026-11-28). The heat pump's hot-water part-load
fraction (PR #1299, bead hisim-heat-pump-model-rework-fpqz.9) aims the charge at the upper pocket's end temperature.
In the stratified cylinder that temperature jumps to the supply once the charge front passes the pocket, so the
rule overshoots the charge end: the heat pump's hot-water electricity is +3.4 % at 900 s on the Aachen twin (N = 5)
and +8.6 % on the #94 case hp4, for heat within 0.3 %; at N = 20 it is +7.5 % at 900 s and +24 % at 3600 s on the
twin (§7.3). The tank's totals hold, so this is a controller matter: bead hisim-stratified-charge-end-signal-5e39
(P2), on PR #1299's branch, not this one, looks for another signal (the charge front, the tank's energy). The gas
boiler's rule, which stops the charge inside the step by its ratio, is within 1.3 % of 60 s on gas final energy.

**D14 — hisim-bq3h stays P2, and its gain is bounded** (owner, 2026-11-28; D10 confirmed). Measured on hp4 with the
stratified cylinder (§7.2): the backup heat a summer morning puts in before the collector's first run is 97 kWh/a,
the cylinder reaches the neighbourhood of the 80 °C stop on 5 summer days, and the collector is idle at 75 °C or
more for 142 of the 458 kWh of sun it misses. A priority rule would mostly delay the backup, so its gain is bounded at
about 3 share points on hp4. At 10 m² the stop binds on 31 summer days, which is oversizing and priority cannot use
either.

**D15 — RenoVisor keeps the NSRDB 2019 weather for now; its Dublin file is about 28 % low in irradiance** (owner,
2026-11-28). The file holds 713 kWh/(m² a) of global horizontal irradiance against 974 measured at Dublin Airport
(Met Éireann, long-term) and 990 to 1006 from PVGIS-SARAH3 for 2019 and for 2005 to 2023; the deficit is in every
month and worst in November and December (37 to 44 % of SARAH3); 2019 itself was a normal year. Bead
hisim-weather-files-rework-7c1d (P3, on main) reworks all weather files; hisim-17fs (P2) fixes the NSRDB time zone.
Consequence: the #94 yields of §7.2 are read with that caveat: the yield target (300 to 500 kWh/(m² a)) is reached
at 4 m², the share target (50 to 60 % at 4 to 6 m²) is not reachable on this file, and the same 4 m² system on the
Aachen test reference year (1,193 kWh/(m² a) on the collector plane) reaches 480 kWh/(m² a) and 59 %.

Earlier decisions this spec rests on: D4 of the hydronic coupling spec (controllers decide on start-of-step
values), its D5 (the buffer's stratification path kept until replaced; replaced here by deletion, D7), decision 7 of
the part-load spec (the store is a black box that publishes port and sensor quantities only; "any tank model, a
stratified one included, can publish" the controlled temperature at the end of the step), decision 9 of the
part-load spec (collector and backup each set their own ratio; no priority in the mechanism), and the modelling
guide's rule that a new store publishes the temperatures both ends book their flows on (`system_docs/modelling_guide.md`
§1): here the sub-step mean, which both ends read from the same output.

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
- **Simulation step**: the simulator's step of length `dt` (`SimulationParameters.seconds_per_timestep`), iterated
  in passes until every component's outputs converge.
- **Sub-step**: one of `n` equal parts of a simulation step, of length `dt/n`, over which the layer step of §4.3 runs
  once; the pass's inputs are held constant over all `n` sub-steps.
- **Start-of-step temperature** `T0_k`: layer k's temperature at the start of the simulation step (°C), the tank's
  state; the start-of-sub-step temperature of the first sub-step. The sensor pockets' start values and the layer
  outputs are `T0_k`, so what a controller decides on does not move while a step iterates (D4 of the hydronic
  coupling spec).
- **End temperature** `T_end,k`: layer k's temperature at the end of the simulation step, after the last sub-step's
  buoyancy merge; the next step's `T0_k`.
- **Coil**: one of the tank's charging circuits (the primary coil of the backup, the secondary coil of the
  collector), modelled as today as a water stream that enters the tank at the coil's inlet layer at the generator's
  supply temperature and leaves at its outlet layer at that layer's start-of-sub-step temperature (an ideal heat
  exchanger, as the mixed node assumes today; the coil's loop and the tank water do not mix in reality, only their
  heats).
- **Port height** `h`: where a coil's inlet or outlet, or a sensor pocket, sits, as a fraction of the tank's height
  from the bottom (0 = bottom, 1 = top), dimensionless. Its layer is `k = min(floor(h N) + 1, N)`.
- **Tap draw**: the household's warm-water demand, served through the thermostatic mixing valve of stage C
  (`SimpleDHWStorage.mixing_valve_draw`, moved to `hydronics` by §4.8): `m_hot` of tank water leaves the top layer
  and the same mass of mains water at `T_cold` (10 °C) enters the bottom layer.
- **Through-flow** across a boundary: the net mass flow (kg/s) crossing the boundary between two neighbouring layers,
  from mass conservation of every layer; positive upward.
- **Upwind inflow term**: the heat a stream brings into a layer, `m c (T_from − T_k) dt/n`, with `T_from` the
  start-of-sub-step temperature of the layer or the supply the water comes from. The same form as a circuit's heat in
  `hydronics.circuit_power_w`, so no new physics function is needed.
- **Courant number** of a layer over the step: the mass that leaves the layer in one simulation step over the layer's
  mass, `Co_k = (Σ outflows of k) dt / M_k`. The explicit scheme is valid for one layer's mass per sub-step, so a
  sub-step needs `Co_k / n ≤ 1` (§4.4).
- **Combined rule**: the rule of D1 that sets `n` in every pass: 1 without flow, otherwise the largest of the 60 s
  floor `ceil(dt / 60 s)`, the Courant term `ceil(max_k Co_k)` and the step's running maximum.
- **Running maximum** `n_max`: the largest `n` any earlier pass of the same simulation step used; 1 at the start of
  every step; it survives `i_restore_state` (§4.8).
- **Flow-free pass**: a pass in which every coil flow and the tap's demand are zero, so `Co_k = 0` for every layer and
  `n = 1`; only conduction and loss act.
- **Buoyancy merge**: after each explicit sub-step, every layer that is warmer than the layer above it is mixed with
  it to their mass-weighted mean, until the profile is monotone (no layer warmer than the one above). The "inversion
  mixing" of the TRNSYS multi-node store (Type 4) and of Duffie and Beckman, Solar Engineering of Thermal Processes,
  §8.4. Not a time sub-step: at most N − 1 merges, each a finite arithmetic mean.
- **Sensor pocket**: a configured height at which the tank publishes its temperature for a controller: its start-of-step
  value (what the controller decides on) and its end value (what its part-load control reads).
- **Threshold**: `SimulationParameters.part_load_above_seconds`, 600 s by default (part-load spec §3). The
  controllers' part-load rules read it; the tank does not (§4.9).

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
temperatures `WaterTemperatureOfLayer<k>AtStartOfStepInCelsius` (for plots and tests) and the step's sub-step count
`NumberOfSubsteps` (dimensionless, for the acceptance measurements of §6 and the plots). T12 allows an output on the
stratified class that the mixed one lacks only if it matches one of these two patterns; everything else must agree.

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

Layer k has the height `H/N`, the mass `M/N` (24.8 kg at N = 10), the side area `2π R H / N`, and the top and bottom
layers add one cap each. Its loss coefficient `UA_k = U · (its surface)`, with `U =
heat_transfer_coefficient_in_watt_per_m2_per_kelvin` (0.36 W/(m² K)); `Σ UA_k` is the mixed cylinder's `UA`
(0.831 W/K for the preset tank), so the standby loss of a uniform tank is the same in both classes (T6). The
conduction between layers k and k+1 is `UA_cond = λ_eff A / (H/N)`, with `λ_eff` the effective vertical
conductivity of water plus wall (§4.7, a configured field with a default of 1.0 W/(m K)): 1.06 W/K for N = 5,
2.13 W/K for N = 10 and 4.26 W/K for N = 20 on the preset tank.

### 4.3 The sub-step: upwind transport, conduction, loss, then buoyancy; the step: n sub-steps

Every quantity of a sub-step is computed from the temperatures at its start, `T_1 … T_N` (the `T0_k` for the first
sub-step), the coils' supplies and flows (inputs), the demand (input) and the surrounding temperature (input), all
held for the whole simulation step. The sub-step length is `τ = dt/n`, with `n` from §4.4.

**The tap valve on the top layer.** The tap draws from layer N, whose supply temperature is `T_N` at the start of
the sub-step, a committed value, so the valve of stage C has a closed form and no local iteration (the mixed class's
`solve_tank_step` solves the valve on the step mean, which the stratified class does not have):

- `T_N > T_warm` (40 °C): `m_hot = m_d (T_warm − T_cold) / (T_N − T_cold)`, the heat drawn equals the demand;
- `T_cold < T_N ≤ T_warm`: `m_hot = m_d`, all of the demand passes unmixed, the shortfall is
  `ThermalEnergyUnmetDHWInWattHour` as in the mixed class;
- `T_N ≤ T_cold`: nothing is drawn.

`m_hot` leaves layer N and the same `m_hot` of mains water at `T_cold` enters layer 1: the column moves up by
`m_hot τ / M_k` layers per sub-step. The draw is solved again in every sub-step on that sub-step's top layer, so a
draw that empties the top layer within a long step serves the later sub-steps from the layer that has moved up.

**Through-flows.** Each circuit moves water from its inlet layer to its outlet layer: the primary coil from `k_in,1`
to `k_out,1`, the secondary coil from `k_in,2` to `k_out,2`, the tap from layer 1 (refill) to layer N (draw). The
through-flow across the boundary between layers k and k+1 is

`F_k = Σ_circuits m_c · s_c(k)`, with `s_c(k) = +1` if the circuit's inlet layer is at or below k and its outlet
layer at or above k+1 (it moves water up across the boundary), `−1` for the reverse, `0` if the boundary is not
between its inlet and outlet.

A coil whose inlet and outlet lie in the same layer moves no water through the tank; its supply mixes into that
layer and its return is that layer's temperature.

**The explicit layer balance.** For every layer k, over one sub-step:

`C_k (T*_k − T_k) = Σ_s m_s c (T_s − T_k) τ + UA_cond (T_k+1 − T_k) τ + UA_cond (T_k−1 − T_k) τ − UA_k (T_k − T_amb) τ`

where `T*_k` is the temperature before the buoyancy merge, the streams s entering k are the coil supplies whose
inlet layer is k (at the generator's supply temperature), the refill at layer 1 (at `T_cold`), the through-flow
from below if `F_k−1 > 0` (at `T_k−1`) and the through-flow from above if `F_k < 0` (at `T_k+1`); the conduction
terms exist only for the neighbours that exist. Streams that leave k do so at `T_k` and contribute nothing in this
form, because every layer keeps its mass (`Σ in = Σ out`). This is the upwind scheme of bead
hisim-hydronics-fxix.1: layer k books the water from its neighbour exactly as a start-of-step node books a circuit
(`SimpleHotWaterStorage.start_of_step_node_step`, `:1646`).

**Buoyancy merge.** Scan from the bottom: while some layer k is warmer than layer k+1 (`T*_k > T*_k+1`), replace
both, and every neighbour the merged block is still inverted against, by their mass-weighted mean (the arithmetic
mean, since the layers have equal mass). The result is monotone and is the next sub-step's start profile. This is
how a solar coil at the bottom heats the tank: the warmed bottom layer rises into the layers above it, as the real
plume does. The merge is a finite pass over N layers, at most N − 1 means, and conserves `Σ C_k T_k` exactly.

**Closure of a sub-step.** Summing the layer balances, every through-flow appears once as an inflow and once as an
outflow at the same temperature and cancels, the conduction terms cancel pairwise, and the merge conserves the sum, so

`Σ_k C_k (T_after,k − T_k) = Σ_coils m_c c (T_sup,c − T_k_out,c) τ − m_hot c (T_N − T_cold) τ − Σ_k UA_k (T_k − T_amb) τ`

holds identically, for any flows and any N, computed with `math.fsum` as `MixedNode.step` does.

**The step.** The `n` sub-steps run in sequence, each from the profile the one before left. The tank publishes, for
the step:

- the end temperatures `T_end,k`, the last sub-step's merged profile;
- per coil, the return `T̄_ret,c = (1/n) Σ_i T_k_out,c,i`, the mean over the sub-steps of the outlet layer's
  start-of-sub-step temperature, and the heat `Σ_i m_c c (T_sup,c − T_k_out,c,i) τ = m_c c (T_sup,c − T̄_ret,c) dt`,
  so the heat a generator books from the published flow, supply and return is exactly the tank's;
- the tap's heat, `m_hot` and unmet share as the sums over the sub-steps (the published `m_hot` is the mean flow);
- the loss as the sum over the sub-steps, `ΔU = Σ_k C_k (T_end,k − T0_k)`.

Summed over the sub-steps the closure above is the step's: `Σ coils − tap − loss = ΔU` identically. The right-hand
side is exactly what the coils' generators book, what the tap books and what the loss port publishes. D4 is a
property of the scheme, not a rule. With `n = 1` the step is the single explicit step of the 2026-11-25 spec, bit
for bit (the prototype tested this identity).

**A worked step, one sub-step.** The preset tank with N = 5 (49.6 kg per layer) at 60 s, layers 20/20/50/55/58 °C
from the bottom (cold below, warm above), the heat pump charging 0.369 kg/s at 63 °C into layer 5, out of layer 3,
no draw, no conduction and no loss. The through-flow is 0.369 kg/s downward across the boundaries 4/5 and 3/4
(22.1 kg per step, `Co = 0.45`, so `n = 1` by §4.4); layer 5 gains `0.369 · 4180 · (63 − 58) · 60 = 462.7 kJ` and
ends at 60.23 °C; layer 4 gains `0.369 · 4180 · (58 − 55) · 60 = 277.6 kJ` and ends at 56.34 °C; layer 3 gains
`0.369 · 4180 · (55 − 50) · 60 = 462.7 kJ` and ends at 52.23 °C; layers 1 and 2 stay at 20 °C; the published return
is 50 °C, layer 3's start temperature, and the heat pump books `0.369 · 4180 · (63 − 50) · 60 = 1203.0 kJ`, the sum
of the three. The profile is monotone, so nothing merges.

**The same step at 900 s.** The same inputs over a 900 s step move 332 kg, `Co = 6.7` on layers 3 to 5, so the rule
gives `n = max(15, 7, 1) = 15` sub-steps of 60 s. The first sub-step is the step above. After 15 the layers are
20/20/62.94/62.99/63.00 °C: the charge front has passed layer 3 and the upper three layers stand at the supply. The
published return is the mean of layer 3's fifteen start temperatures, 59.13 °C; the heat pump books
`0.369 · 4180 · (63 − 59.13) · 900 = 5375 kJ`, and `ΔU = 49.6 · 4180 · (12.94 + 7.99 + 5.00) = 5375 kJ`. One explicit
step over 900 s would have moved 6.7 layers through a 5-layer tank, which no profile represents; a step mean of a
single mixed node would have booked the charge against a return rising towards 63 °C over the whole tank.

### 4.4 The sub-steps: the combined rule

The explicit upwind scheme carries, in one sub-step, at most one layer's mass across a boundary: with one layer
mass per sub-step the layer ends exactly at its upstream neighbour's start temperature (a plug shift of one layer);
with more it ends beyond it, which no mixing can produce. So every sub-step needs `Co_k / n ≤ 1` for every layer,
`n ≥ ceil(max_k Co_k)`. Conduction and loss are explicit too, with the condition `2 UA_cond τ / C_k ≤ 1`; it is
checked at build for `τ = dt`, the flow-free pass (D6, §4.7).

**The rule (D1).** In every pass, after the flows are read and before the first sub-step:

```
if every coil flow == 0 and the tap's demand == 0:
    n = 1                                          # a flow-free pass: conduction and loss only
else:
    n = max(ceil(dt / 60 s), ceil(max_k Co_k), n_max)
n_max = max(n_max, n)                              # the step's running maximum
```

`Co_k` is computed from the pass's flows over the whole step, `(Σ outflows of k) dt / M_k`, with the tap's draw
bounded by its demand (`m_d`, the largest `m_hot` the valve can take). The tap's demand, not its momentary draw,
decides "flow": a draw of 0 with a demand above 0 needs the top layer at mains temperature. `Co_k` is exactly 0
if and only if every coil flow is at most 0 and the demand is 0, since the layer outflows are sums of positive flows.
The 60 s of the floor is a class constant of the layer step (`StratifiedLayers.LONGEST_SUBSTEP_IN_SECONDS`), not a
configuration field: a different length is a different model (300 s misses the totals and can lock up, §7.3), and
nothing in the surrounding system decides it.

**Why the three terms** (the measurements are §7.3). The Courant term alone is the rule bead hisim-hydronics-fxix.1
proposed, and it does not converge: `n` is an integer function of the flows, so the tank's answer jumps at whole
Courant numbers, and the generator, which sets its flow from the tank's return, lands on the jump (the boiler's
`PartLoadRule` steers its flow right onto it). The floor makes `n` independent of the flows wherever
`ceil(Co) ≤ ceil(dt / 60 s)`, which is every step of the gas twin and the gas #94 cases (largest Courant number 13.1
at 900 s and 52.1 at 3600 s at N = 20) and 97 to 99 % of the heat pump's steps; it also sets the accuracy, since a
coil's return then follows its outlet layer within the step at the 60 s resolution the reference run has. The
running maximum covers the steps where the heat-pump charge exceeds the floor (1 to 3 % of the steps at N = 20, where
`n` reached 44 at 900 s and 174 at 3600 s): there `n` follows the flow, but as it cannot fall within a step, it stops
changing after finitely many passes, and from then on the tank is a continuous function of its inputs within the
step and cannot alternate. The memory is needed at 60 s steps too, where the floor is 1: at N = 10 the heat pump's
largest hot-water flow (0.59 kg/s) moves 1.4 layers in 60 s, at N = 20 2.9.

**What the rule gives up.** The converged answer of a step may depend on the pass history: a transient pass, for
example the warm start with the previous step's flows, can lock in a larger `n` than the converged flows need. This
is deterministic and only adds resolution; it held `n` above the floor and the Courant term of the last pass on 1.4
to 3.7 % of the heat-pump steps at N = 20 and on none of the gas steps, and the totals differ from fixed 60 s
sub-steps by at most 0.23 percentage points. A flow-free pass takes `n = 1` whatever `n_max` is and does not read
it; the next flowing pass resumes at `n_max`. Jumping between 1 and `n_max` as a flow crosses zero changes the answer
only by the refinement of conduction and loss, and every one of the 809 to 1,529 steps per run whose passes switched
between flowing and flow-free converged with no more passes than under fixed sub-steps.

**Forced passes.** The tank ignores `force_convergence`: on a forced pass the controllers hold their decisions, and
the tank keeps `n_max` like on any other pass. The forced steps of the twins (726 / 343 at 900 / 3600 s on the
heat-pump twin, 213 / 611 on the gas twin, upward-only rule, N = 5) equal those with fixed sub-steps within 5 %: the
forcing comes from the controllers, not from `n`.

**The flows the twins move**, measured on 2026-11-25 on `household_heatpump_solar_thermal_building_sizer`
(15 June 2021 at 60 s, 7.78 kW heat pump, 4 m² collector, 250 l cylinder, Aachen test reference year, predefined
occupancy) and computed from the generators' laws for the others:

| Circuit | Flow, kg/s | Water per 60 s, kg | Source |
|---|---|---|---|
| heat pump, hot-water charge, 7.78 kW | 0.350 mean, 0.369 peak | 21.0 to 22.1 | measured; hplib's secondary flow is the rated power over about 5 K, `7780 / (4190 · 5) = 0.371` |
| heat pump, hot-water charge, the largest hplib flow over the year | 0.59 | 35.4 | measured over the full year (REPORT.md §1) |
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
(49.6 kg at N = 5, 24.8 at N = 10, 12.4 at N = 20), the Courant number per 60 s:

| Worst layer outflow, kg per 60 s | Co at N = 5 | N = 10 | N = 20 |
|---|---|---|---|
| heat-pump charge, 7.78 kW, 22.1 | 0.45 | 0.89 | 1.78 |
| heat-pump charge, largest hplib flow, 35.4 | 0.71 | 1.43 | 2.85 |
| boiler at its pump's largest flow, 24.6 | 0.50 | 0.99 | 1.98 |
| tap at its peak, 9.9 | 0.20 | 0.40 | 0.80 |
| collector, 20 m², 13.3 | 0.27 | 0.54 | 1.07 |
| electric heater, substation, 4 m² collector: at most 2.7 | 0.05 | 0.11 | 0.22 |

So at N = 10 and 60 s steps every flow but the heat pump's largest gives `n = 1`, and the heat pump's largest gives
`n = 2` on those passes. The sub-step count for the twin's heat-pump charge at N = 10, by step length:

| Step length | Floor `ceil(dt / 60 s)` | Co at 0.369 kg/s | Co at 0.59 kg/s | n at 0.369 / 0.59 kg/s |
|---|---|---|---|---|
| 60 s | 1 | 0.89 | 1.43 | 1 / 2 |
| 900 s | 15 | 13.4 | 21.4 | 15 / 22 |
| 3600 s | 60 | 53.6 | 85.6 | 60 / 86 |

Measured at N = 20 over full years (§7.6): a flow-free pass is 45 to 81 % of all steps, so the mean `n` is 3.7 to
7.3 at 900 s and 34 at 3600 s; the Courant term raised `n` above the floor on 1.1 to 3.0 % of the heat-pump steps
and never on a gas step.

### 4.5 Ports, energy ports and the fluid loops

Both classes declare the channels and loop tags of hydronic stage E and #1122. Per coil, a `FluidLoop` with
`supplies=False`, `pumps=False`, `may_cool=False`: `temperature_in` (the generator's supply), `flow_in` (its flow),
`temperature_out` (the return) and `flow_out` (the flow passed through unchanged). What differs is what the value
carries:

| Output | The mixed cylinder | The stratified cylinder | Reader |
|---|---|---|---|
| `StepMeanWaterTemperatureToHeatGeneratorInCelsius` (renamed by D9) | the step mean `T̄` of the one node | `T̄_ret,1`, the mean over the sub-steps of the primary coil's outlet layer | the backup, as its return; it books `m c (T_sup − T_ret)` |
| `StepMeanWaterTemperatureToSecondaryHeatGeneratorInCelsius` (renamed by D9) | `T̄` | `T̄_ret,2`, the same for the secondary coil's outlet layer | the collector, as its return |
| `WaterMassFlowRateToHeatGenerator`, `…ToSecondaryHeatGenerator` | the flow in, unchanged | the same | the loop's mass check (#1122) |
| `WaterTemperatureFromHeatGenerator`, `…FromSecondaryHeatGenerator` (outputs) | the supply, echoed | the same | plots |
| `WaterTemperatureAtStartOfStepInCelsius` | `T0` | the upper sensor pocket's `T0` (§4.6) | the backups' controllers (on/off, hysteresis) |
| `WaterTemperatureAtEndOfStepInCelsius` | `T_end` | the upper pocket's `T_end` | the backups' part-load controls |
| `LowerSensorWaterTemperatureAtStartOfStepInCelsius`, `…AtEndOfStepInCelsius` (new, both classes) | `T0`, `T_end` | the lower pocket's `T0` and `T_end` | the solar controller: the differential switch-on against the collector sensor, the full-tank stop, the pump's part-load target; the collector's inlet sensor |
| `WaterTemperatureOfLayer<k>AtStartOfStepInCelsius`, k = 1 … N (stratified only) | – | N outputs, `T0_k` | plots, tests; no controller |
| `NumberOfSubsteps` (stratified only) | – | `n` of the pass | the acceptance measurements (§6), plots; no controller |
| `ThermalPowerFromHeatGenerator`, `…FromSecondaryHeatGenerator`, W | `m c (T_sup − T̄)` | `m c (T_sup − T̄_ret,c)`, the sub-steps' heats over `dt` | the energy balance (the tank's side of each loop), KPIs |
| `ThermalPowerConsumptionDHW`, W, `PRODUCTION` to `HOT_WATER_USE` | `m_hot c (T̄ − T_cold)` | the sum over the sub-steps of `m_hot,i c (T_N,i − T_cold) τ`, over `dt` | the balance, the cost engine's useful heat, KPIs |
| `ThermalEnergyUnmetDHWInWattHour` | on `T̄` | the sum over the sub-steps | the KPI "Unmet DHW heat demand" |
| `ThermalEnergyIncreaseInStorage`, Wh, `STORED_CHANGE` | `C (T_end − T0)` | `Σ_k C_k (T_end,k − T0_k)` | the balance |
| `ThermalEnergyInStorage`, Wh | `C T_end` | `Σ_k C_k T_end,k` | KPIs |
| `HeatLossInWatt`, `HeatToHeatedZoneInWatt`, `LOSS` | `UA (T̄ − T_amb)` | the sum over the sub-steps of `Σ_k UA_k (T_k,i − T_amb) τ`, over `dt` | the balance, the building |
| `StandbyTemperatureLoss`, K | `loss / C` | `loss / C`, on the whole tank | the assembly bounds |
| `WaterMassFlowRateOfDHW`, `HotWaterMassFlowRateFromStorageInKgPerSecond` | the demand, `m_hot` | the demand, the mean `m_hot` over the sub-steps | plots, KPIs |

The returns the generators read are means over the sub-steps, not start-of-step values, so the generator and the
tank go back and forth over a few passes within a step, as they do with the mixed cylinder (coupling spec §6): the
generator sets its flow and supply from the return, the tank's return moves with the sub-steps' profile, until both
agree. Measured over full years at N = 20 (§7.6): 5.9 / 8.0 passes per step at 900 / 3600 s on the heat-pump twin
against 5.7 / 9.1 with the mixed cylinder, 5.5 / 7.9 against 5.2 / 7.4 on the gas twin. The generators' supply caps
at their set temperatures (coupling spec §5) stay as they are. What does not move within a step is what the
controllers decide on: the pockets' and the layers' start-of-step values are the committed `T0_k`.

The collector reads the lower pocket's `T0` as `TemperatureCollectorInletAtStartOfStepInCelsius` and the secondary
coil's sub-step mean as its return; with the default heights the coil's outlet is layer 1 and the lower pocket
layer 2 at N = 10 (§4.6).

**The energy balance and the fluid-loop checks.** The post-run balance (`hisim/postprocessing/energy_balance/check.py`)
books each coil's loop once from the loop's flow, supply and return, by the supplier's sign (fluid loops spec §3.4):
unchanged, and the tank's side equals the generator's side on every pass, since both compute
`m c (T_sup − T̄_ret,c)` from the same three published values, and the tank's `T̄_ret,c` is defined so that this
product is the sub-steps' heat (§4.3). The tank's own balance `Σ coils − tap − loss = ΔU` closes by §4.3. The mass
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

With the defaults of §4.7 and N = 10 (`k = min(floor(h N) + 1, N)`): the backup coil enters layer 10 and leaves
layer 6, the solar coil enters layer 3 and leaves layer 1, the upper pocket (0.7) is layer 8 and the lower pocket
(0.1) is layer 2, the layer above the solar coil's outlet. The backup charges when layer 8 falls below its aim less
its hysteresis; the collector's pump switches on when the collector's sensor reads 7 K above layer 2 and stops when
layer 2 starts a step above 80 °C. A height on a layer boundary belongs to the layer above it; bead
hisim-stratified-fractional-heights-9b4e places ports at fractional positions, so that the pocket at 0.1 reads the
temperature at 0.1 of the height and not of a whole layer. The temperature check
(`check_converged_mean_water_temperature`, 0 to 90 °C) applies to the hottest and the coldest layer.

The tank does not know which controller reads which pocket, so D3 holds: a priority rule (bead hisim-bq3h) is a
change to the controllers' set points or an L2 signal, never to the tank.

### 4.7 Configuration, presets, sizing

`StratifiedDHWStorageConfig` (`MAIN_CLASS = "hisim.components.stratified_dhw_storage.storage.StratifiedDHWStorage"`)
has the fields of `SimpleDHWStorageConfig` (§4.1, with the same sizing laws) and these plain fields, which nothing in
the surrounding system decides:

| Field | Default | Meaning and source |
|---|---|---|
| `number_of_layers` | 10 | N, at least 2. The owner's choice of 2026-11-28 (D8): collector heat 3 to 4 % below N = 40 at about a tenth more loop time than N = 5, where N = 20 is within 1 % at a third to half more (§7.4, §7.5). |
| `primary_coil_inlet_height_as_fraction_of_height` | 1.0 | the backup coil's top |
| `primary_coil_outlet_height_as_fraction_of_height` | 0.5 | the backup coil's bottom: the backup heats the upper half, the ready volume of a bivalent cylinder (0.7, the upper 40 %, gave +4.6 % collector heat on hp4: measured, not decided, D8) |
| `secondary_coil_inlet_height_as_fraction_of_height` | 0.25 | the solar coil's top |
| `secondary_coil_outlet_height_as_fraction_of_height` | 0.0 | the solar coil's bottom |
| `upper_sensor_height_as_fraction_of_height` | 0.7 | the backup's pocket, in the ready volume above the backup coil's middle |
| `lower_sensor_height_as_fraction_of_height` | 0.1 | the solar pocket, at the solar coil |
| `effective_vertical_conductivity_in_watt_per_meter_kelvin` | 1.0 | water's 0.6 W/(m K) (VDI Heat Atlas) plus the steel wall's share; store tests after EN 12977-3 give 0.6 to 2 W/(m K) for cylinders with internal coils |

There is no field for the sub-stepping: the combined rule and its 60 s floor are the model (§4.4), and a file cannot
switch them off or change the length. The heights and the conductivity are estimates from the layout of bivalent
solar cylinders (the backup coil in the upper half, the solar coil in the lower third) and from the literature named;
the preset's docstring and the field comments say so (modelling guide §5), and bead
hisim-stratified-cylinder-defaults-1eff replaces them by sourced values (D8). The tap's draw at the top and refill at
the bottom are not configurable: every cylinder does that.

Presets: `preset_standard(name)` on the stratified class is the bivalent cylinder above, with the volume and the
location left `AUTO` as the mixed class's `preset_standard` leaves them, so a file switches classes and keeps
`preset: standard`. `SimpleDHWStorageConfig.preset_standard` is untouched.

The refusals at build (`StratifiedDHWStorage.build`): N below 2 or not an integer (a cylinder of one layer is the
mixed class's job, and a layered tank of one layer would be a third model, the mixed node with a start-of-step
valve, that nobody asked for); a height outside [0, 1]; a coil whose outlet lies above its inlet (a charging coil is
fed at its top; a file would state the reverse only by mistake); the conduction condition `2 UA_cond dt / C_k ≤ 1`
at the step length (§4.4; it binds only at N = 40 and 3600 s on the preset tank, where it is 2.4). Nothing about the
flows (D6).

Twins and assemblies: the three recorded solar twins state the stratified class (D12, §5); every other recorded twin
and the assemblies keep the mixed class. The `partner:` lists of the needs that name `SimpleDHWStorage`
(`dhw_temperature` of the five heating assemblies, `cylinder_temperature` of `heating/solar_thermal`, `cylinder_heat`
and the circuit ends of `test_partners.yaml`) list both classes, so a system that states the stratified class binds;
the `cylinder_temperature` need binds the solar controller to the lower pocket's two outputs. A stratified assembly
(`dhw/stratified_cylinder`) is written when a system composed from assemblies needs it; the solar twins are recorded
files, not compositions, and need none.

### 4.8 Where the code goes

**The component.** A folder, one per component (seams spec, `module_graph.yaml` group `components`):
`hisim/components/stratified_dhw_storage/` with `__init__.py`, `storage.py` (`StratifiedDHWStorageConfig`,
`StratifiedDHWStorage`) and `layers.py`. The class inherits `SimpleWaterStorage` from
`hisim.components.simple_water_storage`, as the mixed cylinder does: the surroundings inputs, the loss outputs and
the geometry are every water storage's and already have two callers there. The graph allows the import (a module
may import its own group); it is an inheritance import, not a wiring one, so hisim-default-connection-names-497c does
not remove it. No other code is shared with the mixed class: the coil channels, the loops, the tap outputs and the
default connections are declared again in `storage.py`, and T12 keeps them equal. A common base class for those
declarations was considered and rejected: it would need a home in `simple_water_storage.py` (shared code under
`hisim.components.shared` may not import the component model), it would make the mixed class a dependency of the
stratified one for its port list, and it would not stop an override from drifting, which T12 does anyway.

**The layer step.** `layers.py` holds the pure layer step, a subcomponent in the sense of
`component_design_principles.md` §C with one caller, so it lives in its caller's folder and not in
`hisim/subcomponents`: a frozen dataclass `StratifiedLayers` built once from the configuration (N, `M_k`, `C_k`,
`UA_k`, `UA_cond`, the port layers, the tap temperatures, `dt`), with the class constant
`LONGEST_SUBSTEP_IN_SECONDS = 60.0`, and
`step(t0_per_layer, coil_streams, demand, t_amb, largest_earlier_number_of_substeps) -> LayerStep` (the end
temperatures, the heat and the sub-step-mean return per coil, the tap's draw, heat and unmet share, the loss, `ΔU`,
the number of sub-steps `n` used and the largest Courant number), and the pure helpers `number_of_substeps` (the
combined rule), `through_flows`, `explicit_layer_balance`, `buoyancy_merge`, `layer_of_height`, each with its own
tests (T1 to T5, T7). The stateless `step` takes the running maximum as an argument and returns the `n` it used; it
holds no state of its own.

**The state and the running maximum.** The state is `StratifiedTankState` with a tuple of N start temperatures;
`i_save_state` and `i_restore_state` copy it whole. The running maximum `n_max` is an attribute of the component
beside the state, not in it: the simulator calls `i_save_state` once per step before the first pass and
`i_restore_state` then `i_simulate` once per pass (`simulator.py`, `process_one_timestep`), so `i_save_state` is
the one "new step" hook. `i_save_state` calls the parent and resets `n_max` to 1; `i_restore_state` copies back the
state only, so `n_max` survives every pass of the step; `i_simulate` passes `n_max` to the layer step and raises it
to the `n` the step used. This is explicit state with a documented lifetime, as `system_docs/component_design_principles.md`
asks of a controller's memory, and T7 drives it through the three calls as the simulator does.

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
equipment register (`renovisor/economics.py:393`) gets the row for `StratifiedDHWStorage` (`hot_water_system`,
dated by heating, as the mixed one), and the mapping report's entry for the solar-thermal measure says the measure
includes the bivalent cylinder (D12).

**Cost in the hot path.** O(N · n) arithmetic per pass, no allocation in the sub-step loop. Measured at N = 20 over
full years (§7.5): with the combined rule the loop takes 1.5 to 1.7 times the mixed cylinder's at 900 s and 2.4 to
4.0 times at 3600 s; the cylinder's own share of the loop is 33 to 45 % at 900 s. A flow-free pass still pays the
per-call work of reading the inputs and publishing about 30 outputs and N layer channels; the flowing passes, at 15
or more sub-steps, carry most of the cost. At N = 10 the cost is estimated at 1.25 to 1.35 times the mixed cylinder's
loop at 900 s (§7.5), to be measured by PR 5. A cheaper flowing step would come from vectorising the layer step over
N, not from the rule.

### 4.9 Interaction with the part load

At and below the threshold every ratio is exactly 1 (part-load spec, decision 16), so a stratified cylinder at 60 s
or 600 s never sees a fraction. Above it the controllers' part-load rules meet the layer model through the pockets:
each `PartLoadControl` reads the end temperature of the pocket it is wired to, the backups' the upper pocket's, the
solar controller the lower pocket's, and the tank's end temperatures are the profile after `n` sub-steps. Decision 7
of the part-load spec, "any tank model, a stratified one included, can publish" the controlled end temperature, is
met by the pockets. What a fraction does to the tank is what it does to the generator's flow: the boiler's rule
reduces the flow at the full-load supply (`hydronics.py:265-298`), the heat pump's (PR #1299) reduces the flow at the
capped outlet, and the tank sees a smaller flow for the whole step, held over its sub-steps.

Measured (§7.3): the boiler's rule, which stops the charge inside the step by its ratio, puts the gas twin within
1.3 % of 60 s on gas final energy at 900 s and 0.5 % at 3600 s. The heat pump's rule aims at the upper pocket's end
temperature, which jumps to the supply once the charge front passes the pocket (the 900 s worked step of §4.3 ends
its upper pocket at 62.99 °C, past a 58 °C charge end), so its secant cannot land on the charge end and the
electricity overshoots (D13, bead hisim-stratified-charge-end-signal-5e39). The tank publishes what a rule on
another signal would need: the pockets' end temperatures, the layers' start temperatures and the stored energy.

## 5. What changes and what does not

- **Results.** The three recorded solar twins switch to the stratified class (D12), so their goldens at 60 s and
  900 s move: the collector's heat rises by 56 to 86 % (its coil meets the bottom layer, near the mains temperature
  after a draw, instead of the 55 to 70 °C mean), the backup's hot-water heat falls by about the same amount, the
  standby loss falls (the backup keeps only the upper half hot), the tap's delivered heat stays, and the unmet hot
  water changes little (§7.1). Every other recorded energy system, golden and RenoVisor run without a collector keeps
  `SimpleDHWStorage`, whose model is untouched. The two lower-pocket columns appear on every run with a mixed
  cylinder, each repeating an existing column; no KPI moves (PR 3 says so in its description). RenoVisor's houses
  with solar thermal get the bivalent cylinder through the switched base files: on the six #94 cases the collector's
  yield rises from 99 to 171 to 182 to 353 kWh/(m² a) and the solar share from 19 to 38 % to 45 to 67 % (§7.2), with
  the weather caveat of D15.
- **Public interfaces.** A new component class with its configuration and preset; the two lower-pocket outputs on
  both classes; the layer and sub-step outputs on the stratified one; the `partner:` lists of the assemblies; the
  mapping report's sentence on the solar measure; the buffer's field `heat_exchanger_is_present` goes (D7), which
  touches `hisim/energy_system_v3.schema.json` and two tests, no recorded file. The rename of the return outputs (D9)
  is a column rename in every DHW twin's results and gets its own PR with the heads-up of
  `results_and_interfaces.md` §5.
- **Goldens and twins.** None re-blessed by PRs 1 to 4. PR 5 re-records the three solar twins with the stratified
  class, re-blesses their goldens at 60 s and 900 s, switches RenoVisor's solar houses through them, and posts the
  heads-up on renovisorissues (the #94 thread) with the measured yields. PR 6 (the rename) re-records the twins and
  re-blesses the goldens for the renamed columns, no value changes.

## 6. Verification

Unit tests of the pure functions, `base`, in `tests/test_stratified_dhw_layers.py`:

- **T1 energy closure per step.** Random profiles, flows, supplies and draws, N in {2, 5, 10, 20}, at 60, 900 and
  3600 s, a third of the cases flow-free: `Σ C_k (T_end,k − T0_k)` equals `Σ coils − tap − loss` within 1e-9 J,
  with the coil heats equal to `m c (T_sup − T̄_ret) dt` from the published returns; also with inverted profiles,
  so the merge is covered; `n ≥ max(floor, n_max, ceil(Co))` whenever water flows.
- **T2 pure transport.** No conduction, no loss: a charge of one layer mass per sub-step shifts the profile by
  exactly one layer per sub-step (plug flow); at half a layer the entering layer ends at the mean of its own and the
  upstream start temperature; the 900 s worked step of §4.3 gives `n = 15`, the layers 20/20/62.94/62.99/63.00 °C,
  the return 59.13 °C and 5375 kJ; a coil with inlet and outlet in one layer moves nothing through the tank and
  mixes into that layer like a `MixedNode` with `a → 0`.
- **T3 pure conduction.** Two layers at 60 and 20 °C, no flows: each sub-step moves `UA_cond · 40 K · τ` from the
  warm to the cold layer, and the explicit solution agrees with the exact exponential to first order in `τ`; the
  stability condition at the step length is refused at build when violated.
- **T4 a tap draw.** A tank at 20/20/60/60/60 °C: the draw's heat equals the demand, the cold front moves up by
  `m_hot dt / M_k` layers, the hot water let out is `m_d (T_warm − T_cold)/(T_N − T_cold)`; with the top layer
  below 40 °C the unmet share is `(T_warm − T_N)/(T_warm − T_cold)` of the demand; below 10 °C nothing is drawn; a
  draw that empties the top layer within a 3600 s step serves the later sub-steps from the layer that moved up, and
  the unmet share is the sum over the sub-steps.
- **T5 buoyancy.** A bottom layer warmed above the one above it merges to their mean; a fully inverted profile ends
  uniform at the mass-weighted mean; the sum `Σ C_k T_k` is unchanged by the merge; a monotone profile is untouched.
- **T6 the same standby loss.** A uniform tank with no flows and no draw loses, per step, the same heat in
  `StratifiedLayers.step` as `MixedNode.step` within the order of `a` (the mixed node's loss is on the step mean,
  the layers' on the sub-steps' start temperatures), and exactly the same `Σ UA_k` is built from the same volume and U.
- **T7 the combined rule.** In the pure step: `n = 1` with no flow at 900 and 3600 s, even with an earlier maximum
  of 40 (closed balance, no tap heat); the floor of 15 at 900 s for every flow up to `Co = 15`, including a
  1e-6 kg/s trickle and a tap draw alone, and 60 at 3600 s; the guard above the floor (1.0 kg/s at 900 s at N = 20
  is `Co = 18.1`, so `n = 19`; an earlier 25 holds; an earlier 19 holds against a smaller flow); `n = 1` at 60 s
  below one layer per step and 2 above it; at `n = 1` the result equals the single explicit step bit for bit. With
  a component driver that calls `i_save_state`, `i_restore_state` and `i_simulate` as the simulator does: the passes
  give 19 → 19 → 1 (flow-free) → 19, the maximum stays 19 across the flow-free pass and across `i_restore_state`
  while the layers are rolled back, after `i_save_state` the next step starts at the floor, and the published
  balance closes on a raised pass and on a flow-free pass.
- **T8 refusals at build.** N below 2 or not an integer, a height outside [0, 1], a coil whose outlet lies above its
  inlet, the conduction condition at the step length (N = 40 at 3600 s refused, N = 20 accepted); and no refusal for
  any flow or for a step length of 900 or 3600 s (D6).

Component tests, `base`, in `tests/test_stratified_dhw_storage.py` and `tests/test_energy_balance_storages.py`:

- **T9 the balance report.** The stratified cylinder with N = 10 in a day's run at 60 s and at 900 s: the
  energy-balance check reports it closed on every step, and the loop checks of #1122 pass (mass at every member,
  heating only, the energy check at the member, which compares the generator's booked heat with the tank's from the
  published sub-step-mean return).
- **T11 the sensor wiring.** The default connections wire the backups' controllers to the upper pocket and the solar
  controller and the collector's inlet sensor to the lower one, on both classes; a file may wire either; the
  temperature check covers the hottest and the coldest layer.
- **T12 the shared interface** (generic, `tests/test_dhw_cylinder_interface.py`). Builds `SimpleDHWStorage` and
  `StratifiedDHWStorage` from `preset_standard` through `tests/component_sweep.py` and checks §4.1 item by item: the
  inputs (name, load type, unit, mandatory, `allow_unconnected_mandatory`, loop tag); the outputs of the mixed class
  all present on the stratified one with the same load type, unit, energy port, loop tag, postprocessing flags and
  display; every extra output of the stratified class matches `WaterTemperatureOfLayer<k>AtStartOfStepInCelsius` or
  is `NumberOfSubsteps`; the two loops' declarations; the cylinder's own default-connection lists per partner class;
  for every component class the sweep builds, its default connections keyed by `SimpleDHWStorage` and by
  `StratifiedDHWStorage` are the same list, output for output; `cost_relevance`; the adapter's cost facts and
  useful-heat row for both class names; the shared configuration fields (name, type, default, sizing rule) and that
  both classes offer `standard`. One test, no allowlist: the two classes cannot drift without failing it.
- **T13 a coarse step in a run.** The heat-pump solar twin with the stratified class, one week of January at 900 s
  (`system_setups`): the run finishes, every step closes, `NumberOfSubsteps` is 1 on every flow-free step and at
  least 15 on every step with a charge or a draw, and no step hits the pass limit.

Twin tests, `system_setups`, in `tests/test_stratified_cylinder_solar_twins.py`:

- **T10 interchangeability and the solar twins' yield.** `household_gas_solar_thermal_building_sizer` and
  `household_heatpump_solar_thermal_building_sizer`, the first week of June 2021 at 60 s and at 900 s, run from their
  recorded energy-system files once with the stratified class as recorded and once with the cylinder's class path
  replaced by the mixed class and nothing else changed. All runs finish; with the stratified class the collector's
  heat rises, the backup's hot-water heat falls, the tap's delivered heat agrees within 1 %, the unmet hot water
  does not rise by more than a stated bound, every step closes, the loop checks pass, and the passes per step are
  reported for both classes.

**Acceptance measurements of the implementing PR (PR 5).** Measured by hand over full years, the way the prototype
measured them (§7), reported in the PR and written into §7; they are the step-length checks of the modelling guide
(§3) for this component, too slow for CI:

- **A1 convergence.** The combined rule at N = 10 converges on both solar twins at 60, 900 and 3600 s (no pass-limit
  abort), and the forced steps are those of the controllers (within a few percent of the mixed cylinder's).
- **A2 tank-side totals.** Collector heat, backup heat, tap heat and standby loss at 900 s within about 1 % and at
  3600 s within about 1.4 % of the 60 s run, both twins; the balance residual at most 1e-10 Wh per step. The
  prototype's measured miss at N = 20 is recorded here: on the heat-pump twin the backup heat is +1.13 % at 900 s and
  +2.62 % at 3600 s (collector −0.56 / −1.29 %), with fixed 60 s sub-steps giving the same (+1.25 / +2.63 %) and
  N = 5 holding +0.3 / −0.06 % at 900 s and +0.26 % at 3600 s, so the cause is the fine layers resolving the
  collector's return from the bottom 5 % while the collector's supply and flow are held over the whole step, not the
  rule; the gas twin holds at both lengths. The PR remeasures at N = 10; if the heat-pump twin misses 1 % there, the
  PR states the miss and its cause as here, and the owner decides whether the bound or N moves.
- **A3 the #94 cases.** The six cases of §7.2 at 900 s with N = 10: yield in kWh/(m² a) and solar share, against the
  N = 5 and N = 20 rows of §7.2 (N = 10 is expected between them: hp4 gave 343 kWh/(m² a) and 45.8 % with fixed
  60 s sub-steps).
- **A4 the n distribution.** Per run: the mean `n`, the share of steps at `n = 1`, the largest `n`, the steps where
  the Courant term raised `n` above the floor and where the running maximum did, and the sub-steps per pass; against
  §7.6.
- **A5 cost.** The loop time and the cylinder's share against the mixed cylinder on both twins at 900 and 3600 s,
  against the estimate of §7.5.

Deliberately not tested here: the solar priority (hisim-bq3h, D14); the charge-end signal (D13, its bead).

## 7. Measured results

All measurements are the prototype's of 2026-11-28, full years, no post-processing. Evidence, with the system
files, scripts, per-run columns and pass counts beside each:

- E1 `/home/noah/hisim/agent-tmp/stratified-substep/REPORT.md`: the prototype, the Courant rule's failure, fixed 60 s
  sub-steps against the mixed cylinder on the twins, N = 5, code 230636791 (without the heat pump's hot-water
  part-load fraction; the `plr_` runs there have it);
- E2 `/home/noah/hisim/agent-tmp/stratified-substep/upward/RESULTS.md`: the upward-only rule, 300 s sub-steps, the
  layer convergence, N = 5 to 40;
- E3 `/home/noah/hisim/agent-tmp/stratified-substep/combined/RESULTS.md` and `tables.md`: the combined rule at N = 20
  on both twins and the six #94 cases, the n distribution, the cost, code f7405fae0 (with the part-load fraction);
- E4 `/home/noah/hisim/agent-tmp/stratified-solar-94/RESULTS.md`: the six #94 cases at N = 5, mixed against
  stratified, the weather limit, the sensitivity tests;
- E5 `/home/noah/hisim/agent-tmp/stratified-substep/timing/`: the first cylinder timings (heat-pump twin, fixed 60 s
  sub-steps against mixed);
- E6 `/home/noah/hisim/agent-tmp/hp-dhw-part-load/RESULTS.md`: the heat pump's hot-water part-load fraction
  (PR #1299) and the charge-end signal;
- E7 `/home/noah/hisim/agent-tmp/renovisor-weather-year/RESULTS.md`: the Dublin weather file.

Definitions: **solar share** = collector heat / (collector heat + backup heat); **yield** = collector heat per m² of
collector; **backup final** = the heat pump's hot-water electricity plus the heating rod in hot-water mode, or the
boiler's hot-water gas; **collector inlet** = the collector's return weighted by its heat; **tank-side totals** =
collector heat, backup heat, tap heat, standby loss.

### 7.1 Mixed against stratified, the two solar twins (Aachen test reference year 2021, 4 m², 250 l; kWh/a)

| Twin | Cylinder | N | Step, rule | Collector | Yield, kWh/(m² a) | Backup heat | Backup final | Standby | Share | Collector inlet, °C | Source |
|---|---|---|---|---|---|---|---|---|---|---|---|
| heat pump | mixed | – | 60 s | 1232.5 | 308 | 2091.0 | 686.9 | 289.5 | 37.1 % | about 60 | E1 |
| heat pump | stratified | 5 | 60 s (`n = 1` throughout) | 1926.5 | 482 | 1350.0 | 430.3 | 238.1 | 58.8 % | 41.2 | E1 |
| heat pump | stratified | 20 | 60 s, combined | 2062.4 | 516 | 1201.7 | 384.2 | 228.3 | 63.2 % | 35.6 | E3 |
| gas | mixed | – | 60 s | 1077.4 | 269 | 2294.0 | 2560.3 | 324.8 | 32.0 % | about 60 | E1 |
| gas | stratified | 5 | 60 s | 1855.6 | 464 | 1445.0 | 1565.3 | 259.5 | 56.2 % | 42.8 | E1 |
| gas | stratified | 20 | 60 s, combined | 2005.1 | 501 | 1282.3 | 1384.7 | 247.9 | 61.0 % | 36.7 | E3 |

The #94 gap on the twins, mixed against stratified at 60 s: collector heat −36 % (heat pump) and −42 % (gas); the
collector's heat-weighted return is 60 °C in the mixed tank against 41 to 43 °C in the stratified one; backup heat
+55 to +59 %, backup final energy +60 to +64 %, standby loss +22 to +25 %. The tap's heat agrees within 0.3 %; the
unmet hot water is 2.4 kWh/a stratified against 12.5 mixed on the heat-pump twin and 0 on both for gas. The N = 5
and N = 20 rows of the heat-pump twin differ in code as well (the part-load fraction), which moves the backup final
energy, not the collector's heat.

### 7.2 The six #94 cases (RenoVisor's translation, Dublin NSRDB 2019, 900 s, 250 l; kWh/a)

The N = 5 rows ran fixed 60 s sub-steps (E4), the N = 20 rows the combined rule (E3); the mixed rows are RenoVisor's
translated files unchanged, and reproduce the triage of 2026-11-10 (gas exactly, heat pump within 1 %).

| Case | Collector, m² | Cylinder | Collector | Yield, kWh/(m² a) | Share | Backup heat | Backup final | Standby | Collector inlet, °C |
|---|---|---|---|---|---|---|---|---|---|
| hp4 | 4 | mixed | 676.9 | 169 | 22.0 % | 2399.3 | 784.4 | 236.8 | 57.7 |
| hp4 | 4 | stratified N = 5 | 1289.3 | 322 | 42.8 % | 1720.7 | 570.1 | 160.1 | 34.1 |
| hp4 | 4 | stratified N = 10 (sensitivity, fixed 60 s) | 1370.6 | 343 | 45.8 % | 1621.6 | 552.3 | 143.5 | 30.6 |
| hp4 | 4 | stratified N = 20 | 1412.7 | 353 | 47.2 % | 1583.3 | 548.0 | 146.5 | 29.1 |
| hp10 | 10 | mixed | 1179.9 | 118 | 38.1 % | 1916.0 | 633.7 | 253.5 | 60.5 |
| hp10 | 10 | stratified N = 5 | 1904.8 | 190 | 62.3 % | 1152.5 | 389.8 | 208.3 | 44.0 |
| hp10 | 10 | stratified N = 20 | 2045.9 | 205 | 67.0 % | 1006.0 | 351.9 | 200.5 | 38.9 |
| gas0 | – | mixed | – | – | – | 3108.8 | 3680.5 | 253.4 | – |
| gas0 | – | stratified N = 5 | – | – | – | 2975.2 | 3522.3 | 125.4 | – |
| gas0 | – | stratified N = 20 | – | – | – | 2951.5 | 3494.3 | 102.9 | – |
| gas4 | 4 | mixed | 586.5 | 147 | 18.8 % | 2535.7 | 3002.1 | 266.5 | 61.9 |
| gas4 | 4 | stratified N = 5 | 1232.8 | 308 | 40.7 % | 1800.2 | 2131.3 | 181.9 | 35.7 |
| gas4 | 4 | stratified N = 20 | 1364.4 | 341 | 45.2 % | 1652.0 | 1955.8 | 166.1 | 30.3 |
| gas6 | 6 | mixed | 766.5 | 128 | 24.5 % | 2360.6 | 2794.7 | 271.0 | 62.8 |
| gas6 | 6 | stratified N = 5 | 1499.7 | 250 | 49.2 % | 1550.4 | 1835.6 | 198.7 | 40.1 |
| gas6 | 6 | stratified N = 20 | 1651.5 | 275 | 54.4 % | 1385.0 | 1639.7 | 185.7 | 34.7 |
| gas10 | 10 | mixed | 988.7 | 99 | 31.5 % | 2147.5 | 2542.4 | 280.1 | 64.6 |
| gas10 | 10 | stratified N = 5 | 1820.8 | 182 | 59.2 % | 1255.4 | 1486.3 | 223.8 | 45.3 |
| gas10 | 10 | stratified N = 20 | 1973.4 | 197 | 64.4 % | 1091.0 | 1291.7 | 212.7 | 39.7 |

What the table shows (E3, E4):

- The stratified cylinder lowers the collector's heat-weighted return by 17 to 26 K and raises its heat by 61 to
  110 % at N = 5, and by a further 7 to 11 % at N = 20; the backup's final energy falls 27 to 42 % in the solar cases.
  The tap's heat is 2,842 to 2,854 kWh/a in every run; the unmet hot water is at most 1.9 kWh/a stratified against
  12.4 mixed (hp4). The cylinder's balance closes to at most 5e-11 Wh per step.
- **Against #94's targets**: at 4 m² the yield is 308 to 353 kWh/(m² a), inside 300 to 500, and the share is 41 to
  47 %, below 50 to 60 %; 6 m² (gas) gives 250 to 275 kWh/(m² a) and 49 to 54 %; 10 m² reaches 59 to 67 % at 182 to
  205 kWh/(m² a), because the summer surplus is lost (the cylinder stops at 80 °C on 31 summer days). The share
  target is not reachable on this weather file: the Dublin file holds 713 kWh/(m² a) of global horizontal irradiance
  and 782 on the collector plane, about 28 % below the long-term 974 to 1006 (D15, E7); the same 4 m² system on the
  Aachen year (1,193 kWh/(m² a) on the plane) reaches 480 kWh/(m² a) and 59 % (§7.1). The collector's efficiency is
  the same on both, 40 to 41 %, so the model is not what limits the share.
- **gas0** (no collector) with the stratified class is not a baseline (D12): the backup heats only the upper half,
  which halves the standby loss; the system without solar is the mixed gas0.
- **Step length** (hp4, N = 5, 60 s against 900 s over the 364 days the 15-minute file allows): collector heat
  −0.3 % at 900 s, the same share; the heat pump's hot-water electricity +8.6 % (D13).
- **Sensitivity** (hp4, N = 5, 900 s, one change each; measured, not decided): backup coil heating the upper 40 %
  (`primary_coil_outlet_height_as_fraction_of_height: 0.7`) +4.6 % collector heat, 45.1 % share; 300 l +3.1 %,
  43.9 %; 10 layers +6.3 %, 45.8 %.
- **Solar priority** (D14): over May to August the backup puts in 97 kWh before the collector's first run of the
  day and 290 kWh after it; the lower pocket reaches 77 °C or more on 5 days; of the 458 kWh of sun on the idle
  collector, 142 kWh fall while the lower pocket is at 75 °C or more. The bound of about 3 share points is an
  inference from these totals, not a run with a priority rule.

### 7.3 The sub-step rules (full years on the two solar twins; % against stratified 60 s)

| Rule | N | Converges | Collector, heat pump 900 / 3600 s | Collector, gas 900 / 3600 s | Backup heat, heat pump / gas, 900 s | Backup final, heat pump / gas, 900 s | Cylinder time of the loop, heat pump 900 s | Source |
|---|---|---|---|---|---|---|---|---|
| `n = ceil(Co)` | 5 | no: 4 of 4 coarse runs alternate (gas 900 s step 2435 `n` 3↔2, gas 3600 s step 414 2↔3, heat pump 900 s step 995 5↔6, heat pump 3600 s step 587 1↔2) | – | – | – | – | – | E1 |
| fixed 60 s sub-steps | 5 | yes, 4 of 4 | −0.3 / −0.1 % | +0.3 / +0.1 % | +0.5 / −0.5 % | +5.4 / −1.1 % | 14.8 s of 60.1 s (mixed: 3.9 of 47.2) | E1, E2 |
| upward-only (`n = ceil(Co)`, never falling within a step) | 5 | yes, 6 of 6 | +1.2 / +1.9 % | +2.1 / +2.6 % | −1.6 / −2.9 % | +3.2 / −4.1 % | 5.9 s of 49.1 s | E2 |
| fixed 300 s sub-steps | 5 | heat pump 900 s: no (pass limit at step 995); the other 3 yes | – / −0.0 % | +1.1 / +0.5 % | – / −1.6 % (gas) | – / −2.5 % (gas) | – | E2 |
| combined (the decision) | 20 | yes, 26 of 26 (twins at 60, 900, 3600 s; six #94 cases at 900 s) | −0.56 / −1.29 % | +0.10 / −0.71 % | +1.13 / −0.33 % | +7.47 / −1.30 % | 22.8 s of 69.6 s (fixed 60 s at N = 20: 33.4 of 81.5; mixed: 3.9 of 47.6) | E3 |

The combined rule's references are its own 60 s runs at N = 20, where `n` was 2 or 3 on 2.1 % of the heat-pump
twin's steps and the running maximum acted on 233 steps (0.04 %); its totals equal fixed 60 s sub-steps' within
0.23 percentage points on every quantity of both twins and every #94 case (collector within 0.18 %, share within
0.09 points). The mechanism behind upward-only's miss: the collector's heat-weighted return is 39.8 / 39.1 °C
(heat pump, 900 / 3600 s) against 41.1 / 41.0 °C with fixed sub-steps and 41.2 °C at 60 s, so the collector runs
more hours and books more heat. The backup final energy of the heat pump at 900 and 3600 s (+5.4 % before the
part-load fraction, +3.4 % with it at N = 5; +7.5 % at N = 20; +20 to +24 % at 3600 s) is the COP: the charge runs
whole steps at the 60 °C cap past the 58 °C charge end, the charge-end signal of D13; the heat is within 0.5 %. The
combined rule's twin results at 900 and 3600 s against 60 s, N = 20 (E3), the baseline of A2:

| Twin | Step | Collector | Backup heat | Backup final | Tap | Standby | Collector inlet, °C | Residual, Wh/step |
|---|---|---|---|---|---|---|---|---|
| heat pump | 60 s | 2062.4 | 1201.7 | 384.2 | 3040.5 | 228.3 | 35.59 | 1.0e-11 |
| heat pump | 900 s | 2050.8 (−0.56 %) | 1215.2 (+1.13 %) | 412.9 (+7.47 %) | 3041.0 (+0.02 %) | 229.6 (+0.56 %) | 35.76 | 4.8e-11 |
| heat pump | 3600 s | 2035.7 (−1.29 %) | 1233.1 (+2.62 %) | 476.7 (+24.07 %) | 3041.9 (+0.04 %) | 231.5 (+1.38 %) | 36.10 | 7.9e-11 |
| gas | 60 s | 2005.1 | 1282.3 | 1384.7 | 3044.2 | 247.9 | 36.72 | 1.1e-11 |
| gas | 900 s | 2007.1 (+0.10 %) | 1278.1 (−0.33 %) | 1366.7 (−1.30 %) | 3044.2 (−0.00 %) | 245.6 (−0.94 %) | 36.70 | 5.1e-11 |
| gas | 3600 s | 1991.0 (−0.71 %) | 1297.1 (+1.15 %) | 1391.2 (+0.47 %) | 3044.0 (−0.01 %) | 248.6 (+0.27 %) | 37.06 | 8.1e-11 |

### 7.4 Layer convergence (fixed 60 s sub-steps, 900 s, full year; % against N = 40; E2)

| Case | N | Collector | Backup heat | Backup final | Standby | Share | Collector inlet, °C | Loop, s | Cylinder, s (share) |
|---|---|---|---|---|---|---|---|---|---|
| hp4 (Dublin) | 5 | 1289.3 (−9.5 %) | 1720.7 (+9.3 %) | 570.1 (+3.8 %) | 160.1 | 42.8 % | 34.1 | 61 | 16.5 (27 %) |
| hp4 | 10 | 1370.6 (−3.7 %) | 1621.6 (+3.0 %) | 552.3 (+0.6 %) | 143.5 | 45.8 % | 30.6 | 68 | 23.6 (34 %) |
| hp4 | 20 | 1410.2 (−1.0 %) | 1586.3 (+0.7 %) | 549.0 (−0.0 %) | 146.9 | 47.1 % | 29.1 | 87 | 40.5 (47 %) |
| hp4 | 40 | 1424.0 | 1574.6 | 549.2 | 148.9 | 47.5 % | 28.6 | 133 | 85.1 (64 %) |
| heat-pump twin (Aachen) | 5 | 1921.1 (−7.1 %) | 1357.2 (+13.0 %) | 453.4 (+9.3 %) | 240.4 | 58.6 % | 41.1 | 60 | 14.8 (25 %) |
| heat-pump twin | 10 | 2006.8 (−3.0 %) | 1254.8 (+4.5 %) | 424.7 (+2.4 %) | 226.4 | 61.5 % | 37.2 | 66 | 20.5 (31 %) |
| heat-pump twin | 20 | 2050.7 (−0.8 %) | 1216.7 (+1.3 %) | 417.6 (+0.7 %) | 230.7 | 62.8 % | 35.8 | 78 | 32.1 (41 %) |
| heat-pump twin | 40 | 2068.2 | 1201.3 | 414.7 | 232.0 | 63.3 % | 35.0 | 110 | 63.0 (57 %) |

The 50 l bottom layer of N = 5 mixes the collector's return (inlet 34.1 °C against 28.6 at N = 40 on hp4). The
20 → 40 differences (1.0 %, 0.8 %) are a third of the 10 → 20 ones; a geometric extrapolation puts the limit about
0.5 % above N = 40, so N = 10 is about 4 % and N = 20 about 1.5 % below it. Part of the N dependence is the port
heights snapping to layer boundaries (D8, bead hisim-stratified-fractional-heights-9b4e).

### 7.5 Cost (full years, wall seconds of the simulation loop; the cylinder's `i_simulate` including the layer step; E3)

| Case | Step | Mixed, loop s | Fixed 60 s, N = 20, loop s (cylinder) | Combined, N = 20, loop s (cylinder) | Combined over mixed | Combined saves of fixed's extra |
|---|---|---|---|---|---|---|
| heat-pump twin | 900 s | 47.6 | 81.5 (33.4) | 69.6 (22.8) | 1.46 | 35 % |
| heat-pump twin | 3600 s | 18.4 | 55.2 (38.7) | 43.3 (27.2) | 2.35 | 32 % |
| gas twin | 900 s | 24.3 | 52.7 (28.0) | 41.5 (16.9) | 1.71 | 39 % |
| gas twin | 3600 s | 8.2 | 44.0 (34.9) | 32.8 (24.2) | 4.00 | 31 % |
| hp4 | 900 s | 45.3 | 86.2 (40.7) | 72.7 (28.2) | 1.60 | 33 % |
| hp10 | 900 s | 44.4 | 82.9 (37.9) | 66.2 (22.8) | 1.49 | 43 % |
| gas0 | 900 s | 14.4 | 39.3 (26.1) | 22.7 (10.3) | 1.58 | 67 % |
| gas4 | 900 s | 20.3 | 48.3 (27.8) | 35.0 (15.2) | 1.72 | 48 % |
| gas6 | 900 s | 20.5 | 48.0 (27.5) | 34.5 (14.5) | 1.68 | 49 % |
| gas10 | 900 s | 20.3 | 47.7 (27.5) | 33.1 (13.7) | 1.63 | 53 % |

At N = 20 and 900 s the combined rule's loop is 1.5 to 1.7 times the mixed cylinder's; at 3600 s 2.4 (heat pump) and
4.0 (gas) times. For N = 10 no combined-rule run exists. Estimated from the layer runs with fixed 60 s sub-steps at
900 s (§7.4: N = 10 took 66 s against 60 s at N = 5 and 47.2 s mixed on the heat-pump twin; 68 against 61 and 45.3 s
on hp4) and the third of the extra time the combined rule saves: about 1.25 to 1.35 times the mixed cylinder's loop
at 900 s, that is +10 to 11 % over N = 5 and a quarter to a third of N = 20's extra. PR 5 measures it (A5). At 60 s the
cylinder is 14 to 20 % of the loop at N = 20 (the two references ran side by side, so indicative only).

### 7.6 The n distribution (combined rule, N = 20, the converged pass of each step; E3)

| Run | Floor | Largest Co | Mean n | Steps at n = 1 | Largest n | Steps raised above the floor by the Courant term | by the running maximum | Sub-steps per pass | Passes per step, mean / max (mixed) |
|---|---|---|---|---|---|---|---|---|---|
| heat-pump twin, 60 s | 1 | 2.9 | 1.02 | 97.9 % | 3 | 11071 (2.11 %) | 233 (0.04 %) | 1.03 | 5.36 / 18 |
| heat-pump twin, 900 s | 15 | 34.9 | 7.26 | 57.1 % | 44 | 689 (1.97 %) | 766 (2.19 %) | 9.49 | 5.88 / 52 (5.71 / 28) |
| heat-pump twin, 3600 s | 60 | 112.6 | 34.5 | 45.8 % | 174 | 262 (2.99 %) | 328 (3.74 %) | 41.6 | 8.01 / 156 (9.05 / 87) |
| gas twin, 60 s | 1 | 1.2 | 1.00 | 100.0 % | 2 | 47 (0.01 %) | 0 | 1.00 | 5.78 / 10 |
| gas twin, 900 s | 15 | 13.1 | 6.94 | 57.5 % | 15 | 0 | 0 | 7.60 | 5.46 / 25 (5.23 / 20) |
| gas twin, 3600 s | 60 | 52.1 | 33.7 | 44.6 % | 60 | 0 | 0 | 37.2 | 7.85 / 30 (7.37 / 25) |
| hp4, 900 s | 15 | 54.7 | 6.90 | 62.4 % | 59 | 589 (1.68 %) | 775 (2.21 %) | 11.0 | 6.65 / 92 (6.44 / 45) |
| hp10, 900 s | 15 | 54.7 | 5.74 | 69.0 % | 59 | 380 (1.08 %) | 492 (1.40 %) | 8.64 | 6.44 / 105 (6.34 / 45) |
| gas0, gas4, gas6, gas10, 900 s | 15 | 10.0 | 3.67 to 6.34 | 61.9 to 80.9 % | 15 | 0 | 0 | 4.24 to 6.74 | 5.43 to 5.55 / 21 to 22 |

Every step at `n = 1` was a flow-free step, so the rule never ran one sub-step while water moved. The guard acts only
with the heat pump; gas never exceeds the floor. The largest `n` under the combined rule (44 to 174) exceeds fixed
sub-steps' (35 to 113), which may let `n` fall back to `ceil(Co)` within a step; that is where the convergence
guarantee comes from, at about 0.1 point on the totals.

## 8. Decisions of 2026-11-25, 2026-11-26 and 2026-11-28

The eight open questions of the draft of 2026-11-25 were decided by the owner the same day and are D5 to D11 of
§2: Q1 (above the threshold) and Q2 (a flow exceeding a layer) were D6, one run-time refusal, revised on 2026-11-28;
Q3 (which storages, the buffer's dead path) is D7; Q4 (N = 1 keeps the mixed node) is moot under D5, the stratified
class requires N ≥ 2; Q5 (the defaults) is D8; Q6 (the rename) is D9; Q7 (two pockets) is D11; Q8 (hisim-bq3h to P2)
is D10. Decided by this spec under D5: the lower pocket is on both classes (§4.1); the two classes share the valve and
the `SimpleWaterStorage` base and nothing else (§4.8); `preset_standard` on both (§4.7).

Decided by the owner on 2026-11-26, the two questions that review left open:

- **Q1. No recorded energy-system file for the stratified cylinder before the coarse model exists.** Superseded on
  2026-11-28: the stratified class runs at 60 s and 900 s (D1, D6), so the three recorded solar twins state it (D12)
  and join the golden gate at both lengths with PR 5; no separate stratified file is recorded.
- **Q2. A stratified buffer gets a bead now, P2** (hisim-stratified-buffer-eb26, under hisim-hydronics-fxix, blocked by
  hisim-hydronics-fxix.1). It is its own class on the pattern of D5, interchangeable with the mixed buffer, and needs a
  short spec first: the buffer's part-load mode above 600 s rests on linear end temperatures (part-load spec, decision
  21), which a buoyancy merge breaks. Its spec decides whether it sub-steps by the same combined rule; nothing here
  decides it.

Decided by the owner on 2026-11-28, after the sub-stepping prototype (the decisions 1 to 10 of
`/home/noah/hisim/agent-tmp/stratified-substep/DECISIONS.md`):

- **The spec waited for the full evaluation** of sub-stepping (decision 1), which ran in three stages: fixed 60 s
  sub-steps against the Courant rule (E1), the upward-only rule, 300 s sub-steps and the layer convergence (E2), and
  the combined rule at N = 20 on both twins and the six #94 cases (E3).
- **The sub-step rule** (decisions 2, 9, 10) is the combined rule of D1: the Courant rule does not converge, fixed
  60 s sub-steps hold the totals but can jump where a flow exceeds the floor, upward-only converges but misses the
  totals, and the combined rule has fixed sub-steps' accuracy with upward-only's guarantee at a third less of the
  extra cost. D1 is revised; D2 and D6 are superseded; bead hisim-stratified-storage-coarse-0392 is closed.
- **The charge-end signal** (decision 3) is accepted for now: D13, bead hisim-stratified-charge-end-signal-5e39
  (P2) on PR #1299's branch.
- **Which houses** (decision 4): only houses with solar thermal get the stratified bivalent cylinder, and the mapping
  report says the solar measure includes it: D12.
- **Layers** (decisions 5 and 10): the convergence was measured at N = 5, 10, 20 and 40 (§7.4); the owner fixed N = 10
  as the default, below the "smallest N within about 1 % of the finest" the measurement plan named (that is N = 20),
  for the cost: D8.
- **Solar priority** (decision 6): hisim-bq3h stays P2 although its gain is bounded at about 3 share points on hp4:
  D14.
- **Weather year** (decision 7): RenoVisor keeps NSRDB 2019 for now; beads hisim-weather-files-rework-7c1d (P3) and
  hisim-17fs (P2), both merged to main with PR #1301: D15.
- **fpqz.9** (decision 8) closed in PR #1299 for 900 s with the mixed cylinder; its follow-ups are fpqz.10 (the
  3600 s operating point, P3) and the charge-end signal bead. Not this spec's.

Decided by this spec on 2026-11-28, as the owner's decisions leave it to the text: the 60 s floor is a class constant
and not a configuration field (§4.4); a flow-free pass runs `n = 1` and does not read the running maximum (§4.4, as
the prototype did); the conduction condition is checked at build for the full step length (D6); `NumberOfSubsteps` is
an output of the stratified class (§4.1); D9's rename stays with its descriptions changed (D9); the equipment
register's row for the stratified class repeats the mixed one's facts (§4.8); the solar twins need no stratified
assembly (§4.7).

## 9. Beads and pull requests

Epic hisim-hydronics-fxix.

| Bead | Content | PR |
|---|---|---|
| hisim-hydronics-fxix.1 (P3) | this spec: the buffer's path deleted, the sub-stepped layer step, `StratifiedDHWStorage` with the interface test and the default connections, the controllers' pocket wiring, the solar houses' switch and the acceptance measurements; closes with PR 5 | PR 1 to 5 |
| hisim-dhw-return-outputs-rename-653d (P3, D9) | the rename of the two return outputs on both classes, with the descriptions of §4.5, the twins re-recorded, the goldens re-blessed, the heads-up on renovisorissues | PR 6 |
| hisim-stratified-cylinder-defaults-1eff (P3, D8) | sources the coil and pocket heights and `λ_eff` from manufacturer data sheets, VDI 6002, EN 12977 or similar, and re-checks N = 10 against the sourced heights; names RenoVisor's equipment register | – |
| hisim-stratified-fractional-heights-9b4e (P3, new, D8) | coil inlets and outlets and sensor pockets at fractional positions within a layer, so that the ports stop snapping to layer boundaries that move with N; then remeasure the layer convergence of §7.4, which may allow fewer layers | – |
| hisim-stratified-storage-coarse-0392 (closed 2026-11-28) | the coarse model: superseded by D1's combined rule, with the evidence of §7 | – |
| hisim-stratified-charge-end-signal-5e39 (P2, D13; on PR #1299's branch) | the heat pump's hot-water part-load rule on a signal that does not jump when the charge front passes the upper pocket | – |
| hisim-bq3h (P2 since D10, confirmed D14, after the stratified cylinder) | solar priority in the controllers, measured on the stratified cylinder; its gain bounded at about 3 share points on hp4 | – |
| hisim-weather-files-rework-7c1d (P3, D15, on main) and hisim-17fs (P2) | the weather files reworked; the NSRDB time zone | – |
| hisim-stratified-buffer-eb26 (P2, §8 Q2; blocked by hisim-hydronics-fxix.1) | the stratified space-heating buffer as a separate class interchangeable with the mixed one, a short spec first | – |
| hisim-report-states-defaults-41e9 (P3) | the mapping report names the cylinder's volume; unchanged by this spec | – |

No question of §8 stays open, so no `owner-decision` bead is filed.

The pull requests, each small and stacked on the one before (AGENTS.md, "Pull requests and merging"):

1. **The buffer's stratification path goes** (D7): `heat_exchanger_is_present`, the mixing factor and the
   mass-mixing law, their tests and the schema entry; no result changes.
2. **The sub-stepped layer step** as a pure subcomponent in `hisim/components/stratified_dhw_storage/layers.py`:
   the sub-step, the combined rule, with T1 to T8, not yet a component; the tap valve moves to `hydronics.py` and the
   mixed class calls it there; no result changes.
3. **`StratifiedDHWStorage`**: the configuration and `preset_standard` with N = 10, the component in `storage.py`
   with the state of N temperatures and the running maximum beside it, the two pockets, the layer and sub-step
   outputs, the refusals at build, the lower-pocket outputs on both classes, the default connections in both
   directions (the ten partner methods registered for both classes), the economics adapter's entries, the `partner:`
   lists of the assemblies, T9, T12 and T13. The two new columns on the mixed class are named in the description; no
   KPI moves.
4. **The controllers' pocket wiring**: the solar controller's and the collector's default connections to the lower
   pocket on both classes, the `cylinder_temperature` need of the solar assembly, T11; no result changes (on the
   mixed class the lower pocket repeats the single temperature).
5. **The solar houses switch** (D12): the three recorded solar twins state the stratified class and are re-recorded,
   their goldens re-blessed at 60 s and 900 s; RenoVisor's solar houses follow through the base files, the mapping
   report's entry for the solar measure says it includes the bivalent cylinder, the equipment register gets the row;
   T10; the acceptance measurements A1 to A5 at N = 10 over full years, written into §7; the heads-up on
   renovisorissues (the #94 thread) with the measured yields and the weather caveat of D15. Closes
   hisim-hydronics-fxix.1.
6. **The rename of the return outputs** (D9, hisim-dhw-return-outputs-rename-653d): both classes, every reader
   inside HiSim, the assemblies' bounds, the twins re-recorded, the goldens re-blessed, the heads-up on
   renovisorissues per `results_and_interfaces.md` §5.
