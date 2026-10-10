# Spec: hydronic coupling in the temperature domain (pure (m, T) ports)

**Date:** 2026-10-01 · **Owner:** Noah Pflugradt · **Status:** decided (owner, 2026-10-01: the frame in §2,
D1–D8 in §10); ready for staged implementation (§9.5). Replaces PR #864. Beads: hisim-4g9.16, hisim-4g9.21,
hisim-4g9.22, hisim-9uoo.12, hisim-9uoo.15, hisim-9uoo.17, hisim-6ehm; renovisorissues #76.

All `file:line` citations are against `origin/main` at fca81ba0. Code on the #864 branch (`fix/storage-energy`)
is cited by name only.

## 1. Problem

Every generator–vessel–consumer chain in HiSim exchanges water temperatures and mass flows, but the vessels do
not integrate them consistently, so the heat a generator books, the heat a vessel holds and the heat a consumer
receives are three different numbers:

- **The vessel mixes masses instead of integrating.** `calculate_mean_water_temperature_in_water_storage`
  (`hisim/components/simple_water_storage.py:372-397`) sets `T_new = (M T0 + Σ m_i dt T_i) / (M + Σ m_i dt)`, the
  temperature of a vessel that swallowed one step of every inflow without anything leaving. At 900 s the inflow
  mass is comparable to the vessel, so the result is far from the physical one (hisim-4g9.16).
- **Energy is booked against the start temperature.** The inflow heat is `m dt c (T_i − T0)`
  (`simple_water_storage.py:991-1006`, DHW `:1834-1846`) and the outlet to every circuit is `T0`
  (`:1010-1012`, DHW `:1872-1873`). A generator thus books heat into a vessel that, by the end of the step, is
  already at its supply temperature and could not have accepted it (the bug class #864 repaired by capping). The
  standby loss is taken at the mixed temperature after the step (`:1147-1160`, DHW `:1956-1969`).
- **Generators are power sources.** The boiler computes a power from a control signal and sets
  `T_out = T_in + ΔT`, `m = P/(c ΔT)` (`generic_boiler.py:663-690`); district heating
  (`generic_district_heating.py:653-685`, DHW `:687-707`) and electric DHW heating
  (`generic_electric_heating.py:542-560`) do the same with `P = P_max ΔT/100`. Their own power and the heat the
  water carries agree only by construction of `m`, and nothing checks what the receiver did with it.
- **The distribution system lags one step.** `HeatDistribution.i_simulate` publishes the previous step's state
  (`heat_distribution_system.py:471-491`) and writes this step's result into the state afterwards (`:498-501`).
  District heating feeding it without a buffer bills `m c ΔT_needed` while the HDS books its own exchange: 30.8
  kWh/yr sent and never received (hisim-9uoo.12).
- **The heat pump's flow and power disagree.** hplib is cached on inputs rounded to 0.1 K
  (`more_advanced_heat_pump_hplib.py:1997-1999`), while the storage integrates the flow at the unrounded return; the
  flow then carries up to ~0.5 % more heat than `P_th` (hisim-4g9.21; the rounding is the likely cause).

PR #864 closed the vessels' balance with an energy back-channel (storages publish the heat they accepted, generators
book it, the HDS requests and is granted heat). That makes watts a second coupling next to the temperatures, and
it does not compose: every series tank, coil, valve or buffer-less loop would need its own accept/grant protocol.

## 2. Decisions already made (owner, 2026-10-01)

1. **No interim fix.** #864's accepted-heat back-channel (`ThermalPowerAcceptedByStorage*`,
   `AcceptedHeat.booked`, HDS `Requested/Granted/Floor`) is not merged; #864 is replaced by this design, keeping
   what §9.3 lists. hisim-4g9.16 was closed against the #864 branch and is re-solved here; hisim-6ehm, also closed
   there, stays open as a validation item (§10, D4).
2. **Everything in the temperature domain, with pure (m, T) ports.** A hydronic circuit carries a mass flow, a
   supply temperature and a return temperature, nothing else. Energy is only ever derived, as
   `m c (T_sup − T_ret) dt` with one water `c` (`PhysicsConfig` water, 4180 J/(kg K),
   `hisim/components/configuration.py:911`). No component sends watts to another. The goal is composability: two
   tanks in series, a buffer charging a DHW tank through a coil, mixing valves, pipes, a generator feeding the
   HDS without a buffer.
3. **The circuit-law extension is not adopted now.** An owner publishing its outlet-versus-inlet curve so the
   node can solve a step in 2-3 iterations is bead hisim-4g9.22 (P4), for when iterations get slow or biased
   (§7 lists the measured limits that would trigger it).

## 3. Port convention

### 3.1 Circuits, owners, nodes

A **circuit** is one closed water loop between exactly two components. It has:

- one **pump owner**, who decides the mass flow `m ≥ 0` (kg/s) for the step;
- two **legs**, supply (hot) and return (cold); each leg's temperature is owned by the component the water
  *leaves*: the generator owns the supply of its charging circuit, the vessel owns the return; the vessel owns
  the supply of the distribution circuit, the HDS owns its return;
- at least one end that is an **integrating node**: a component that holds water and integrates its temperature
  over the step (a mixed vessel, or the HDS pipe water of §4.6). The other end is algebraic: it maps the
  temperature it receives to the one it sends back within the iteration (a generator, the HDS heat exchange).

A circuit's outputs are its mass flow (kg/s, by the pump owner only), its supply temperature and its return
temperature (°C, each by its owner); the other end reads them. Each component names them under its own output
names, which predate this spec: the boiler's hot-water circuit is `WaterOutputMassFlowDhw` and
`WaterOutputTemperatureDhw`, the heat pump's `MassFlowOutputDHW` and `TemperatureOutputDHW`, and the hot-water tank
publishes the return as `StepMeanWaterTemperatureToHeatGeneratorInCelsius` (renamed 2026-10-10, owner; it was
`WaterTemperatureToHeatGenerator`). A uniform `MassFlow<circuit>` naming is not scheduled; the energy-balance stage
names the three outputs of each circuit in its `HydronicPort` instead.

### 3.2 A node publishes the step mean of its temperature

A node's outlet temperature is the mean of its temperature over the step, `T̄ = (1/dt) ∫ T(t) dt`, not its start
or end temperature. The reason is the energy of the circuit: with the inflow temperature constant over the step,
the heat the node exchanges with circuit i is exactly `m_i c (T_in,i − T̄) dt` (§4.2). Publishing `T0` books
against a temperature the vessel has already left (over-books a charging generator; the #864 bug class);
publishing `T_end` under-books it. Only `T̄` makes the triple `(m, T_sup, T_ret)` that the two ends see
energy-exact for the node's own integration, so the generator, the vessel and the meter derive the same number
from the same three values. The node also publishes `T_end` (next step's state) and `T0` (for controllers, §5.5).

### 3.3 Dual-circuit generators

A generator with a space-heating and a hot-water side (boiler, heat pump, district heating) presents two
circuits, `SpaceHeating` and `Dhw` (the space-heating circuit is written out, never abbreviated; owner, 2026-10-04). The idle one has `m = 0`, and its supply temperature equals its return (no
information, no energy). Which one runs is decided once per step (§10, D6).

### 3.4 Signs

`m ≥ 0` always; direction is given by the topology (supply leg from the supply owner). The heat of a circuit,
`Q = m c (T_sup − T_ret)`, is positive when the supply owner heats the receiving side (a generator charging a
vessel, a vessel feeding the HDS). A cooling heat pump has `T_sup < T_ret` and `Q < 0` on the same circuit;
nothing is mirrored and no circuit changes owner.

### 3.5 Energy for KPIs, meters and the energy balance

Because energy is derived, it is derived by one rule in one place. A `HydronicPort` (in `hisim/energy_port.py`,
next to `EnergyPort` on the energy-balance branch `feat/energy-balance`, #870/#871) names the three outputs of a
circuit:

```python
@dataclass(frozen=True)
class HydronicPort:
    mass_flow: str          # output name (kg/s) of the pump owner
    supply_temperature: str # output name (°C) of the supply owner
    return_temperature: str # output name (°C) of the return owner
    role: lt.EnergyRole     # OUT for the supply owner, IN for the receiver
    @staticmethod
    def heat_in_kilowatt_hour(*, mass_flow_in_kg_per_second, supply_temperature_in_celsius,
                              return_temperature_in_celsius, seconds_per_timestep) -> float:
        return hydronics.circuit_heat_kwh(...)  # m c (T_sup - T_ret) dt / 3.6e6, keyword-only
```

The balance check applies `heat_in_kilowatt_hour` to the three columns; both ends declare the same port with opposite
roles, so a circuit balances by construction and a mismatch can only come from inside a component. Components
keep their `ThermalPower*`/`ThermalEnergy*` outputs for KPIs and reports, computed by the same function from their
port values; the electricity and fuel meters read only fuel or electricity outputs (§5). On the
energy-balance branch the generators', storages' and HDS's heat ports thus reduce to one `HydronicPort` each, and
a vessel's balance is `Σ circuits − loss − ΔU = 0` from the values the simulation used.

**The two ends come from the wiring, never from a declared peer.** A `HydronicPort` names no other component and
needs no `peer_input`/`peer_output` pointer: the circuit's mass-flow output is read by exactly the component at
the other end of the loop, so the balance check pairs the OUT and IN ports of a circuit through that connection.
A component therefore never knows what it is coupled to, and one boiler serves any vessel, a second circuit
serves a second vessel, and a vessel feeds another vessel through a circuit of its own. Two rules keep the pairing
unambiguous, and both fail the run (owner, 2026-10-02: always fail hard): a mass-flow output read by more than one
component that declares a `HydronicPort` on it (one flow cannot deliver its heat twice; a split is a valve
component with one circuit per branch), and a circuit whose one end declares a `HydronicPort` while the other,
declared component does not declare the matching one. A circuit whose other end declares no ports at all is
reported as undeclared, as today. The `EnergyPort` peer pointer remains only for carriers that are not water: fuel,
electricity, ambient heat and solar.

## 4. The mixed node step

### 4.1 Equation and exact solution

A fully mixed node of heat capacity `C = M c` with inflows `m_i` at temperatures `T_i` (the supply of its
charging circuits, the return of its distribution circuits, the cold refill of a tap) and a loss `UA` to
`T_amb`:

`C dT/dt = Σ_i m_i c (T_i − T) − UA (T − T_amb)`

Within one iteration the `T_i` are constants (they are other components' published values), so the equation is
linear with constant coefficients. With `G = Σ m_i c + UA`, `T∞ = (Σ m_i c T_i + UA T_amb)/G`, `k = G/C` and
`a = k dt`:

- `T_end = T∞ + (T0 − T∞) e^(−a)`
- `T̄ = T∞ + (T0 − T∞) (1 − e^(−a))/a` (for `a → 0`: `T̄ = T0`, no division)

One closed form per call, no sub-stepping, the same for the buffer, the DHW tank and the HDS pipe water. It lives
in a new module `hisim/hydronics.py` (`MixedNode.step(T0, inflows, UA, T_amb, dt, C) -> (T_end, T_mean, Q_i, loss)`;
not under `hisim/components/`, because `hisim/energy_port.py`, imported by the base `Component`, delegates to it), so
storages stop carrying their own mixing, booking and loss code. Stage A (PR #890) fixed the constants and the open
parameters (owner, 2026-10-04): water `c = 4180 J/(kg K)`, pinned equal to `PhysicsConfig`'s; water density
`992 kg/m³`, the value at 40 °C both storages use today, so stages C and D replace arithmetic without moving a number
(`PhysicsConfig`'s 1000 is aligned in stage C as one deliberate change); the §6 accelerator uses a secant step after
six of the node's own iterates when the estimated contraction lies in [0, 1), under-relaxation by 0.5 on a residual
sign change, and the plain iterate otherwise.

### 4.2 Properties

- **Exact closure.** Integrating gives `C (T_end − T0) = Σ m_i c (T_i − T̄) dt − UA (T̄ − T_amb) dt` identically;
  the node's per-step balance closes to floating-point precision (~1e-9 J), whatever the step length.
- **No overshoot.** `T∞` is a weighted mean of the inflow temperatures and `T_amb`, and `T` relaxes
  monotonically towards it, so a vessel never passes its hottest inflow (or its coldest). #864's acceptance cap
  becomes a property of the integration instead of a rule.
- **Cooling falls out.** A flow colder than the vessel cools it with the same equation and `Q < 0`. On the storage
  side hisim-9uoo.17 needs nothing more; the refusal #864 introduced for a cooling heat pump into a storage can
  go once the heat pump itself has its cooling model and metering (hisim-9uoo.15, §5.2).
- **Standby loss exact.** The loss is `UA (T̄ − T_amb) dt`, taken inside the same integration; it replaces the
  explicit after-step subtraction at `simple_water_storage.py:1147-1160`.

### 4.3 Hot water at the tap (kept from #864)

The tap draw is internal to the DHW tank, not a circuit to another component. #864's thermostatic mixing valve
(`hydronics.mixing_valve_draw`, solved on the step mean by `SimpleDHWStorage.solve_tank_step`, with
`ThermalEnergyUnmetDHWInWattHour`) is kept and expressed on `(m, T)`: the household
asks for `m_d` at `T_warm`; a tank above `T_warm` lets `m_hot = m_d (T_warm − T_cold)/(T − T_cold)` leave and the
same mass of mains water enter at `T_cold`. In the node equation that is one inflow `m_hot` at `T_cold`. Because
`m_hot` depends on the tank temperature, the tank solves the valve on its own step mean with a local scalar
iteration (all inside one `i_simulate`, no simulator iteration): then the heat drawn,
`m_hot c (T̄ − T_cold) dt`, equals the demand exactly while `T̄ > T_warm`. Below `T_warm` all of `m_d` passes
unmixed and the shortfall is `ThermalEnergyUnmetDHWInWattHour`; below `T_cold` nothing is drawn. #864's
`check_water_mass` (a vessel without water is refused at construction) is kept, as
`SimpleDHWStorage.refuse_a_tank_without_water`. The local iteration is the Illinois variant of regula falsi,
`hydronics.solve_bracketed_fixed_point`, bracketed by the range of every temperature the tank mixes
(`hydronics.TemperatureRange`). The tap's heat, `ThermalEnergyConsumptionDHW` and `ThermalPowerConsumptionDHW`, is
booked positive, the heat the tap delivers (owner, 2026-10-10; the energy was booked negative before, and the cost
engine's useful-heat source reads it as heat into the house).

The DHW tank publishes the step mean it computes from its inputs and its saved state, on every pass (owner,
2026-10-10). Until then it published its start temperature `T0` on the first pass of every step and accelerated
later passes from its own iteration history (§6); both are iteration memory inside the component, which component
design principle 5 rules out ("`i_simulate` is a function of the inputs and the saved state"). The first pass now
starts from the previous step's converged values (the simulator's warm start, hisim-4g9.23) and the acceleration
lives in the simulator (§6).

### 4.4 The space-heating buffer

On the fully mixed path `SimpleHotWaterStorage` becomes a node with up to two charging circuits and one
distribution circuit. It publishes `T̄` as the return to each generator and the supply to the HDS, `T_end` as its
state and `T0` for controllers; `PositionHotWaterStorageInSystemSetup` (`simple_water_storage.py:62-72`) stays a
wiring choice.

### 4.5 Stratification path

The non-exchanger branch (`heat_exchanger_is_present=False`, `simple_water_storage.py:179`, mixing factor
`dt/3600` at `:399-416`, outlets at `:1014-1055`) mixes a supply into an outlet by a resolution-dependent factor;
it is not an integration. It is kept as it is for now and tackled by a separate bead (§10, D5). The two coexist
like this: the node rule of §4.1-4.2 and the step-mean outlet of §3.2 apply to the default, fully mixed path
(`heat_exchanger_is_present=True`); a storage configured without the exchanger keeps today's mass mixing,
start-temperature booking and mixing-factor outlets unchanged. It is documented as not energy-consistent and is
outside the balance guarantee of §4.2 and §9.4. It still declares its `HydronicPort`s, so the energy-balance check
(#870) computes its residual and reports the storage as a known non-conserving component instead of hiding it.
No recorded setup uses this path (only `tests/test_simple_hot_water_storage.py:71`).

### 4.6 The distribution system as a flow circuit

`HeatDistribution` is the pump owner of the distribution circuit with its design flow
(`heat_distribution_system.py:419-425`, sized at `:1499-1512`). Given the supply `T_sup` (the buffer's `T̄`) and the
building's demand, it computes its return in the same step with the existing law (`:556-620`):
`T_ret = max(T_sup − Q_dem/(m c), T_room)` when heating, clamped symmetrically when cooling, and no exchange when
`T_sup ≤ T_room`. The one-step lag of `:471-491` goes: the delivered heat is derived from this step's
`(m, T_sup, T_ret)`.

Without a buffer (district heating, or a generator with `NO_STORAGE_*`, `:74-75`), the HDS's own pipe water is
the node: its mass is already computed (`mass_of_water_in_hds`, `:531`, 8.8 m of 16 mm pipe per m² of floor area,
`:526`), and the free-convection decay of `:503-554` is the `m = 0` case of the same node equation with the
building as `T_amb`. The generator then sees the pipe water's `T̄` as its return, which fixes hisim-9uoo.12: the
heat district heating bills is the heat that entered the pipe water, and the heat the building gets is what left
it, with the pipe water's `ΔU` in between (§10, D3).

**Who pumps follows from the circuit (owner, 2026-10-04, assemblies D25).** In stage D the HDS stops being told its
position by `position_hot_water_storage_in_system`: if the component at the other end of its space-heating circuit
publishes `MassFlowSpaceHeating` (a heat pump, a boiler, a substation with a pump), the HDS reads that flow through its
default connections from that class; otherwise (a buffer in front of it, or a substation without a pump, today's
`NO_STORAGE_MASS_FLOW_FIX`) the HDS pumps with its design flow. The enum is removed with the re-record of this stage, so
a heating assembly with or without a buffer (two assemblies, no parameter) needs no site setting to agree with.

## 5. Generators

Every generator reads its node's return temperature and answers, within its limits, with a supply temperature
and (as pump owner) a mass flow. Its heat is `Q = m c (T_sup − T_ret) dt` (§3.5); its fuel or electricity follows
from its own model, which at the converged iterate carries exactly that heat.

**Maximum supply temperature (owner, 2026-10-09).** Every generator that holds a lift (the boiler, the electric
heater's hot-water side, the heat pump's hot-water side) has a maximum supply temperature and throttles like the
district-heating substation (§5.4): when `T_ret + ΔT` would exceed `T_max`, the supply is `max(T_max, T_ret)`, the
flow stays the one the unthrottled law pumps, and the heat is `m c (T_max − T_ret)`. The fuel or electricity of a
throttled step follows from that heat through the generator's own efficiency or COP law; unthrottled steps stay as
D1 and D2 say. Without the limit, a full-power charge decided on `T0` (D4) heated the conserving DHW tank past
90 °C at 3600 s, and at 900 s for a small tank or a large generator (hisim-fxix.9).

**Hot-water supply capped at the controller's set temperature (owner, 2026-10-09).** A generator's hot-water
supply is also capped at its controller's set temperature, the value the controller already aims at:
`T_sup = max(min(T_set, T_max, T̄ + ΔT), T̄)` (never below the return, as §5.4's throttle; `ΔT ≥ 0` for every hot-water
circuit), with the flow as before and the heat `m c (T_sup − T̄)`; the fuel or
electricity follows from that heat (§5.1). The boiler's `T_set` is its controller's 60 °C warm-water aim plus its
10 K hysteresis, 70 °C (`GenericBoilerController.SupplyTemperatureSetForDHWInCelsius`); the electric heater's is its
controller's 60 °C aim plus its 15 K hysteresis, 75 °C (`ElectricHeatingController.SupplyTemperatureSetForDHWInCelsius`);
the heat pump's is its hot-water controller's `t_max`, 60 °C, plus the energy manager's raise while it is active
(`MoreAdvancedHeatPumpHPLibControllerDHW.SupplyTemperatureSetForDHWInCelsius`); `T_max` (80 °C, 80 °C, 75 °C) stays the upper
bound. The rule is one library function, `hydronics.capped_hot_water_supply_temperature_c`, which the three
generators call; their set-temperature inputs are mandatory, and a set temperature above `T_max`, which a charge
could never reach, is refused (owner, 2026-10-10; the inputs were optional before, and an unwired one silently
dropped the cap). The district-heating substation throttles at its set temperature with the same library rule,
`hydronics.throttled_supply_temperature_c`. So a charge ends at its target inside the step rather than a whole
step past it, which made the fuel and electricity depend on the step length (gas fuel +4.4 % at 3600 s against
60 s over a year before the cap). This is an in-step supply cap at the controller's target, not a forecast: the
controller still decides on `T0` (D4, §10). A supply capped at the set temperature brings the tank towards it but
never past it, so the heat pump's hot-water controller ends a charge within 0.5 K of it (§5.5).

### 5.1 Boiler (D1: power control kept)

The boiler keeps its power control and fixed lift. The controller sets, from start-of-step values, the
`control_signal` through `modulate_power` (`generic_boiler.py:1544-1578`), the mode and the lift
`ΔT = T_set − T0` (`:1461-1525`); the boiler turns the signal into its thermal power `P_th` with the existing
efficiency law (`:663-682`, as #864's `combustion_efficiency_at_burner_power`), pumps `m = P_th/(c ΔT)`
(`:686-690`) and publishes `T_sup = T_ret + ΔT`, where `T_ret` is now the node's step mean `T̄`, not `T0`. The
derived heat is then `m c ΔT = P_th` at every converged iterate, so fuel follows from the forward law and no
inverse is needed. The minimum run and idle times (`:1527-1542`) stay. The controller reads the node's `T0`
output, the boiler its `T̄` output; on main both read the same start temperature.

Consequence: a firing boiler holds its lift, so every firing boiler step is the power-limited case of §7: the node
cannot see the boiler's within-step reaction, and the iteration needs about 11 tries at 900 s, just past the
simulator's `force_convergence` limit of 10. The node acceleration of §6 and the iteration histogram test cover
the boiler setups explicitly. The **electric DHW heater** stays the same way: it keeps its `P_max ΔT/100`
regulation (`generic_electric_heating.py:542-560`) and fixed lift, publishing `T_sup = T_ret + ΔT`.

The boiler's maximum supply temperature is `GenericBoilerConfig.maximal_flow_temperature_in_celsius`, 80 °C: the usual
upper flow-temperature setting of domestic gas, oil and solid-fuel boilers, whose safety high-limit cut-outs act
above it, and above the controller's 70 °C hot-water flow aim, at which the hot-water supply is capped (§5).
On a throttled step the burner cycles at the power it was commanded to: the fuel is the throttled heat over the
efficiency at the commanded burner power, `combustion_efficiency_at_burner_power(P_commanded)` (owner,
2026-10-09; this replaced the inverse of the efficiency law). The electric heater's is `ElectricHeatingConfig.maximal_dhw_supply_temperature_in_celsius`,
80 °C, the usual upper thermostat setting of a domestic electric water heater; its electricity is the throttled heat.
A hot-water step whose controller asks for no lift moves no water, so the boiler does not fire on it (owner,
2026-10-09); the same holds for a space-heating step without a lift, which booked the burner's heat at zero flow
before (owner, 2026-10-10). A boiler that does not fire publishes zero fuel power (`TotalFuelConsumption`); before
2026-10-10 it published its commanded burner power while off.

### 5.2 Heat pump (D2: hplib's flow is authoritative)

hplib returns `P_th`, `P_el`, `COP`, `T_out` and `m_dot` for a return temperature (parallel mode,
`more_advanced_heat_pump_hplib.py:1504-1530`). hplib's `m_dot` and `T_out` are authoritative and published as
the circuit's mass flow and supply; the heat pump books `P_th = m c (T_out − T_in)` with `T_in` the node's
unrounded `T̄`, and `P_el = P_th / COP`. This resolves hisim-4g9.21 this way round: the booked heat is what the
flow carries, and it may differ from hplib's calibrated `P_th` (and so from the calibrated SCOP,
`ScopCalibration`, `:236`) by up to ~0.5 %, the excess hisim-4g9.21 measured. The fixed-flow mode (`:1531-1580`,
`m_dot_ref`, `T_out = T_in + P_th/(m c)`) is consistent under the same rule.

The cache keeps today's 0.1 K rounding of its inputs (`:1997-1999`); the rounding is the likely cause of the
0.5 %. Risk: as a function of the return temperature the rounded result is a staircase, so an iteration can hop
between two bins at the 1e-4 tolerance. The iteration histogram test (§6) watches for it; interpolation between
grid points is the remedy if it shows. It showed, and the remedy is applied in stage C (owner, 2026-10-09): the
cache stays keyed on the 0.1 K grid, and the results of the two neighbouring grid points are interpolated
linearly in the return temperature, which is not rounded any more (`T_out`, `m_dot`, `P_th`, `P_el` and `COP`
alike); the source and ambient temperatures stay rounded, since they are constant while a step iterates. Cooling uses the same circuit with `T_sup < T_ret`; its electricity and
the brine pump must reach the meter (hisim-9uoo.15) before #864's cooling refusal is removed (hisim-9uoo.17).

The heat pump's hot-water outlet is limited to
`MoreAdvancedHeatPumpHPLibConfig.maximal_dhw_supply_temperature_in_celsius`, 75 °C (owner, 2026-10-09): hplib has
no outlet limit; 75 °C is the highest flow temperature air/water heat pumps on the market reach (propane units state
70-75 °C), and it lies above the hot-water controller's switch-off point, 60 °C plus the energy manager's 10 K
surplus raise. A throttled step keeps hplib's flow and COP; its electricity is the throttled heat over that COP.
Below that maximum the hot-water outlet is capped at the hot-water controller's set temperature, `t_max` (60 °C)
plus the energy manager's raise while it is active (owner, 2026-10-09; §5): `T_out = max(min(T_set, T_max, hplib's
T_out), T̄)` (hplib's hot-water outlet lies above its inlet `T̄`), the flow stays hplib's, the heat is `m c (T_out − T̄)`
and the electricity that heat over the COP.

### 5.3 Solar thermal

The collector heat depends on the inlet temperature (`solar_thermal_system.py:767-777`); on main the outlet is
`T_in + 2 ΔT_n` at a flow sized for `ΔT_n` (`:784-796`), so the water carried twice the heat the collector booked.
Corrected (owner, 2026-10-09): the outlet stays `T_in + 2 ΔT_n`, because `ΔT_n` is the inlet-to-mean difference the
efficiency curve is evaluated at, and the flow is sized for `2 ΔT_n`: the collector is pump owner at
`m = Q_coll(T̄)/(c · 2 ΔT_n)`, reads the node's `T̄` and publishes `T_sup = T̄ + 2 ΔT_n`, so its water carries exactly
`Q_coll(T̄)`. While the pump stands or the collector has no heat, the circuit moves no water and its supply is its
return. The pump decides on the step mean, an exception to D4 for the solar controller only (owner, 2026-10-09):
the collector publishes the flow `Q_coll(T̄)/(c · 2 ΔT_n)` (`RequiredWaterMassFlowOutput`) whether or not the pump
runs, and the pump runs while that flow reaches the controller's minimum. Deciding on `T0` made the collector's
yield and the tank's hot-water fuel depend on the step length. The controller's former switch-on comparison, the
collector's outlet at `T̄` against `T̄` plus `set_temperature_difference_for_on`, is removed with that field and the
collector's `CollectorTemperatureAtStepMeanInCelsius` output (owner, 2026-10-10): the difference was `2 ΔT_n`
whenever the collector had heat and 0 otherwise, so the comparison only repeated the minimum flow below 20 K and
stopped the pump for good from 20 K on; results are unchanged with the default 10 K. The controller's full-tank stop (the tank above its
60 °C aim, `SolarThermalSystemController.WARM_WATER_AIM_IN_CELSIUS`) stays on `T0`, which the controller reads from
the tank's `WaterTemperatureAtStartOfStepInCelsius`: on `T̄` it has no fixed point when the pump's own heat lifts the
step mean across 60 °C, and
such a step ended at `force_convergence` (owner decision, 2026-10-09: the stop stays on `T0` because on `T̄` it has
no fixed point; the gas-and-solar twin's year has 21507 forced steps at 60 s with the stop on `T̄`, 13 with it on
`T0`, and 1110 against 11 at 900 s). The controller's minimum pump flow is halved to
0.005 kg/s, the same collector heat as before (owner, 2026-10-09).

### 5.4 District heating substation

The substation is a power-limited heat exchanger: `T_sup = T_ret + min(T_set − T_ret, P_connected/(m c))` on
whichever circuit runs, replacing `P = m c ΔT_needed` (`generic_district_heating.py:653-685`) and the
`P_connected ΔT/100` regulation of its DHW side (`:687-707`). It books the derived heat, so its bill is what the
node received (hisim-9uoo.12). On the DHW circuit the pump runs at `m = P_connected / (c · 100 K)`, the flow the
former regulation implied, and `T_set` is the controller's `T0` plus the lift it asks for (owner, 2026-10-09). Its
early return under `force_convergence` (`:452-453`) keeps the balance
consistent, since the node integrates what the frozen supply carries; only its reaction is stale.

### 5.5 Controllers (D4: as on main)

Controllers stay as on main: on/off, mode (SH/DHW/off), set temperatures and the boiler's signal and lift are
decided from the node's start-of-step temperature `T0`, never from `T̄` or `T_end`, so they are constants of the
iteration and freezing them under `force_convergence` changes nothing. There is no temperature forecast for
on/off controllers and no `DhwChargeYield`; #864's `StorageForecast` and `DhwChargeYield` are dropped. DHW
priority stays in `DiverterValve` (`dual_circuit_system.py:108-112`), one circuit per step (D6). Whether
heat-pump buffers run dry and underheat at coarse steps once the vessels conserve energy (hisim-6ehm) is a
validation item (§9.4); if it returns, it is solved then. The heat pump's hot-water controller ends a charge when
`T0 ≥ t_max + raise − 0.5 K` (`MoreAdvancedHeatPumpHPLibControllerDHW.SWITCH_OFF_TOLERANCE_IN_KELVIN`; owner,
2026-10-09): its supply is capped at `t_max + raise` (§5.2), so the tank approaches that target without passing it,
and with the former strict `T0 > t_max + raise` a charge never ended (hot-water mode on 56.9 % of the time at
60 s). The energy manager's switch-on with a surplus (`raise > 0` and the tank below `t_max`) stops at the same
`t_max − 0.5 K` (owner decision, 2026-10-09): at `t_max`, a tank between `t_max − 0.5 K` and
`t_max` would be switched on with the raise and off without it, and the raise follows the heat pump's draw, so it
can come and go within a step (hisim-4g9.28); with the stop at the same point the charge's state cannot toggle with
it (forced steps of the heat-pump twin's year at 60 s: 112312 with the switch-on at `t_max`, 97983 with it at
`t_max − 0.5 K`). One exception: the solar pump decides on the node's `T̄`
(§5.3, owner 2026-10-09). The hot-water supply cap at the controller's set temperature (§5) is not a decision on
`T̄`: the controller's set temperature is a constant, and the controller still switches on `T0`.

## 6. Convergence with pure ports

The simulator iterates every component in order until no output changes by more than 1e-4
(`hisim/component.py:189-195`, `hisim/simulator.py:393-422`); after 10 tries it sets `force_convergence`, which
freezes controllers, and after 100 it aborts (`simulator.py:415-419`). D7: the simulator is not changed.

**Why it converges.** For a node fed by one circuit whose supply follows the return 1:1 (a generator holding its
lift), the derivative of the node's `T̄` with respect to the inflow temperature is
`θ = 1 − (1 − e^(−a))/a`, with `a = Σ m dt / M` (the flow-through ratio of the step; with several circuits, times
the circuit's share of the flow). `θ` is in `(0, 1)`, so the fixed point contracts and does not oscillate; the
iterations needed to reach 1e-4 from an error of order 1 K are about `ln(1e-4)/ln θ`:

| `a` | 0.1 | 0.5 | 1 | 2 | 5 |
|---|---|---|---|---|---|
| `θ` | 0.05 | 0.21 | 0.37 | 0.57 | 0.80 |
| iterations | 3 | 6 | 9 | 16 | 41 |

Under D1 and D2 the boiler, the electric heater and the heat pump all hold their lift, so `θ` governs them; a
substation below its connected load holds its supply temperature (`θ ≈ 0`, 2 iterations). Slow cases are long
steps and small nodes (`a` large): every firing boiler step at 900 s (~11), a heat pump at 3600 s, the HDS pipe
water without a buffer (~24 at 900 s), a small DHW tank under a full boiler. Note, not a plan: the 1e-4 tolerance
is absolute and applies to watt outputs too; a derived power at `m c ≈ 700 W/K` needs its temperatures to
~1.5e-7 K, which adds `ln(1e-3)/ln θ` iterations. A per-output tolerance is considered only if the histogram
shows the watt outputs dominate.

**Acceleration in the simulator (owner, 2026-10-10).** A component declares an output whose value is the image of
a fixed-point map, a node's `T̄`, with `add_output(..., is_accelerated=True)`; the hot-water tank declares its two
step-mean outputs. After the component has run, the simulator replaces the value it computed
(`hisim/fixed_point_acceleration.py`): up to six passes on a step the plain value, after them a secant (Aitken) step
on the output's own fixed-point residual while the secant's contraction estimate lies in [0, 1), and the
under-relaxed value (weight 0.5) when the residual changes sign (an oscillation, which a contraction with `θ > 0`
should not produce, but a circuit whose heat falls steeply with `T̄` can, such as the heat pump's hot-water supply
capped at its controller's set temperature). A new value within 1e-9 of the one the output stood at keeps that
value: a converged iteration can alternate between two neighbouring floats, which a component deciding on the sign
of a balance (the energy manager, hisim-4g9.31) turns into a cycle of its own. Once the simulator forces
convergence it only holds values within that deadband and extrapolates nothing: the controllers are then frozen,
and a secant from a history that spans their switching aims at a fixed point that no longer exists (with the
extrapolation kept under forced convergence the heat-pump twin's year at 3600 s aborted on a 100-pass cycle). The
acceleration changes only how fast the fixed point is reached, never which one; the iteration history belongs to
the simulator, which runs the iteration, so every component stays a function of its inputs and its saved state.
Until 2026-10-10 the tank kept this history itself, published `T0` on the first pass of a step and clamped the
extrapolation to the range of the temperatures it mixes.

Measured on the six twins with a hot-water tank, full year 2021, mean passes per step and forced steps (more than
ten passes), before (the tank's own memory) and after (the simulator's acceleration), both with the simulator's warm
start (hisim-4g9.23):

| twin | 60 s before | 60 s after | 900 s before | 900 s after | 3600 s before | 3600 s after |
|---|---|---|---|---|---|---|
| gas | 4.06, 0 | 3.90, 0 | 4.10, 0 | 3.95, 0 | 4.20, 0 | 4.06, 0 |
| heat pump | 5.06, 30904 | 4.48, 30589 | 5.71, 3937 | 5.36, 3797 | 7.12, 1893 | 6.82, 1915 |
| electric heating | 5.04, 0 | 4.75, 0 | 6.16, 0 | 5.90, 0 | 7.61, 0 | 7.41, 0 |
| district heating | 4.00, 0 | 3.27, 0 | 4.00, 0 | 3.34, 0 | 4.00, 0 | 3.43, 0 |
| gas + solar | 4.10, 14 | 3.92, 14 | 4.20, 13 | 4.04, 10 | 4.34, 7 | 4.21, 7 |
| heat pump + solar | 4.63, 9977 | 4.04, 10309 | 4.99, 1906 | 4.49, 1826 | aborted at step 4426 | 5.06, 646 |

The hot-water KPIs (tap heat, generators' hot-water heat and fuel or electricity, unmet heat) of the gas, electric,
district-heating and gas-and-solar twins agree within 0.2 % (the collector heat of the gas-and-solar twin within
1.3 % at 900 s, its pump decisions being whole-step); the heat-pump twins' within 0.8 %, their forced steps being
the energy manager's cycles (hisim-4g9.28, hisim-4g9.31). Without the warm start (the state of this stage alone) the
heat-pump twins' forced steps at 900 s rise by 6 % (heat pump, 7909 → 8396) and 10 % (heat pump + solar,
1561 → 1725), fall by 11 % at 60 s (heat pump, 97983 → 86860), and the other twins are unchanged or better; the
heat-pump-and-solar year at 3600 s no longer aborts either way.

**Iteration histogram test.** The recorded twins run at 60, 900 and 3600 s and the iterations per step
(`simulator.py:426` returns the count) are asserted: at 900 s no step reaches `force_convergence` (more than 10
tries). The boiler twins (gas, oil, pellets, wood chips, hydrogen, gas + solar thermal), the heat-pump twins (for
the energy manager's on/off loop; the rounding staircase is interpolated since stage C) and the district-heating twin (no buffer) are listed explicitly. The histogram and each
twin's mean are a CI artifact, so drifts are seen.

**Escalation to hisim-4g9.22** if, after stage D (§9.5), a twin still has 900 s steps at `force_convergence`, or
the §7 bias exceeds 1 % of an annual KPI against 60 s; the buffer-less district-heating loop (D3) goes first.

## 7. Known limits of pure ports

Per iteration the node treats each inflow temperature as constant over the step. A generator that holds its
*lift* rather than its supply temperature (a heat pump, the boiler under D1, a saturated substation) would in
reality raise its supply as the return rises within the step; the node does not see that reaction. The iteration
converges to a self-consistent answer (the published `T̄` and the supply computed from it agree), but that
answer is biased against the within-step physics. Measured on a toy loop (300 kg tank at 35 °C, 6 kW draw, heat
pump at 0.16 kg/s with `P_th = 9000 (1 − 0.015x − 0.0004x²)`, `x` the return temperature):

| step | mean temperature bias | heat bias | iterations |
|---|---|---|---|
| 900 s (the RenoVisor resolution, `hisim/renovisor/simulation.py:74`) | +0.06 K | < 0.1 % | 8 |
| 3600 s | +0.62 K | — | 17 |

The earlier draft measured a power-limited boiler at 4 / 11 / 41 iterations at 60 / 900 / 3600 s, and district
heating feeding the HDS without a buffer at 24 iterations and 0.5 K off at 900 s: past the simulator's freeze
after 10 tries, inside its abort at 100. With D1 the boiler figure applies to every firing boiler step; hence
the acceleration and escalation of §6. Circuit laws (hisim-4g9.22) remove bias and iterations at a richer port.

## 8. Composability test cases

Each is a unit test asserting every node's per-step balance (relative 1e-6) and equal circuit energies at both ends:

1. **Two tanks in series.** A generator charges tank A, A's outlet feeds tank B, B's return goes back to A. Each
   tank is a node; the A→B circuit's supply is A's `T̄`, its return B's `T̄`.
2. **Buffer charging a DHW tank through a coil.** The coil is a circuit from the buffer (supply `T̄_buf`) whose
   return is `T̄_dhw + (T̄_buf − T̄_dhw) e^(−UA_coil/(m c))` (an ε-NTU exchanger, algebraic in the step means).
3. **Mixing valves.** The tap valve of §4.3, and a space-heating mixing valve that blends the buffer's supply with
   the HDS return to a set flow temperature: an algebraic component with two inflows and one outflow; its mass
   split is chosen from start-of-step values, its outlet temperature is the mass-weighted mean.
4. **Generator → HDS without a buffer.** District heating directly on the HDS pipe-water node: per step, the heat
   district heating books equals the pipe water's `ΔU` plus the heat delivered to the building.
5. **Cooling into a buffer.** A heat pump with `T_sup < T_ret` lowers the buffer; the storage side books `Q < 0`
   and closes; the heat pump's cooling electricity is asserted to reach the meter once hisim-9uoo.15 is done.

## 9. Migration

### 9.1 Components

| Component | Change |
|---|---|
| `hydronics.py` (new) | `MixedNode.step`, water `c` (`Water`), circuit heat, supply caps, tap valve, bracketed solve; the acceleration is `fixed_point_acceleration.py`, applied by the simulator (§6) |
| `SimpleHotWaterStorage`, `SimpleDHWStorage` | fully mixed path: node step, publish `T̄`, `T_end`, `T0`; drop mass mixing (`:372-397`), start-temperature booking, explicit loss. Stratification path unchanged (D5) |
| `HeatDistribution` | same-step return; pipe-water node without a buffer; drop the lag (`:471-491`) |
| `GenericBoiler` + controller | keep `control_signal`, `modulate_power` and fixed lift; supply from `T̄`; controller reads `T0` |
| `MoreAdvancedHeatPumpHPLib` | `m_dot`/`T_out` authoritative, `P_th` derived, `P_el = P_th/COP`; 0.1 K grid kept, interpolated in the return (stage C); cooling metered |
| `ElectricHeating` | keep power regulation and fixed lift; supply from `T̄` |
| `DistrictHeating`, `SolarThermalSystem` | supply from return within their limits; book derived heat |

### 9.2 Default connections, setups, twins, RenoVisor

Most rewiring is in `get_default_connections_from_*` (storages `simple_water_storage.py:800-878`,
`:1529-1683`; boiler `generic_boiler.py:519-586`; district heating `generic_district_heating.py:346-424`). The 14
setups in `system_setups/` that use a water storage and the energy-system files that reference one (26 of the
files in `energy_systems/`, including the `*.grouped.energy_system.yaml` twins) are re-recorded in the stage that
changes their wiring. The grouped twins are the RenoVisor base files (`hisim/renovisor/translate.py:486-511`), so
the translator follows them; the translation map (`roadmap/renovisor/translation_map.html`) is regenerated with
`python -m hisim.renovisor map` in the same stage.

### 9.3 What happens to #864

| #864 part | Fate |
|---|---|
| `AcceptedHeat.booked`, `ThermalPowerAcceptedByStorage*` inputs and their default connections | dropped |
| HDS `ThermalPowerRequestedFromStorage`, `ThermalPowerGrantedByStorage`, `SupplyTemperatureFloor` | dropped |
| boiler `ThermalPowerSetpoint` feed-forward, `MINIMUM_TEMPERATURE_LIFT_IN_KELVIN`, `fuel_power_for_thermal_power` | dropped (D1: power control kept, fuel from the forward law) |
| `combustion_efficiency_at_burner_power`, the power-band guard | kept |
| `hot_water_draw` (now `hydronics.mixing_valve_draw` and `solve_tank_step`), `ThermalEnergyUnmetDHWInWattHour`, `check_water_mass` (now `refuse_a_tank_without_water`) | kept, on `(m, T)` (§4.3) |
| `StorageForecast`, `DhwChargeYield` and the controllers' forecast inputs | dropped (D4) |
| cooling-into-storage refusal | kept until hisim-9uoo.15 / 9uoo.17 land |
| `tests/test_water_storage_energy_balance.py` | winter-week, tap and boiler-law scenarios kept, assertions rewritten to node closure; forecast and yield tests dropped |
| #864's re-blessed golden references | discarded |

### 9.4 Golden references and validation

#864's goldens are discarded. Under D8 every stage re-blesses the goldens it changes (`golden_references/`,
`scripts/golden_kpis.py`) in its own PR, with a short before/after table of the changed KPIs.

- **Per step, per component:** every fully mixed node's balance closes (relative 1e-6) and both ends of every
  circuit derive the same heat, on every step of every recorded twin at 900 s. Storages on the stratification
  path are excluded and flagged by the balance check (§4.5).
- **Resolution independence:** annual heat delivered, fuel and electricity at 60 / 900 / 3600 s within 1 % of
  each other (extends `tests/test_time_resolution.py`).
- **Fuel plausibility:** pellet/gas fuel ratio for the same house close to the efficiency ratio (~1.1, not the
  ~2.5 measured earlier). The stated-versus-simulated seasonal efficiency of renovisorissues #76 is reported in the
  same run; its part-load efficiency curve (hisim-9uoo.13) is out of scope here.
- **Heat-pump comfort (hisim-6ehm):** underheating hours and minimum buffer temperature of the heat-pump twins at
  900 s against 60 s; a regression reopens the control question then, not before.
- **Comparison runs** main / #864 / new on gas, pellets, heat pump, gas + solar thermal and district heating
  building-sizer twins (full year, 900 s): heat delivered, fuel/electricity, unmet DHW, comfort hours, iteration
  histogram, attached to the last stage's PR.

### 9.5 Staging

Staged PRs straight into main (D8). Every stage that changes RenoVisor results (B, C, D, F) is announced on
renovisorissues before it merges, naming the KPIs that move and by how much. Each stage's scope and "done when":

- **A. Node and port library.** Scope: `hydronics.py` (`MixedNode.step`, water `c`, `HydronicPort` arithmetic,
  acceleration) with unit tests, used by nothing. Done when: closure and no-overshoot property tests pass and no
  golden changes.
- **B. Heat-pump consistency.** Scope: `P_th = m c (T_out − T_in)`, `P_el = P_th/COP` in both modes, rounding
  kept. Done when: the flow heat equals the booked `P_th` on every step of the heat-pump twins, hisim-4g9.21 is
  closed, goldens re-blessed.
- **C. DHW chain.** Scope: `SimpleDHWStorage` as node, tap valve and unmet DHW, the DHW circuits of boiler, heat
  pump, district heating, electric heater and solar thermal, controllers on `T0`. Done when: the DHW tank closes
  per step in every twin, 60 / 900 / 3600 s agree within 1 %, twins and goldens re-recorded.
  *Amended (owner, 2026-10-09):* the 1 % applies to the generators' hot-water heat and to their fuel or
  electricity, at 900 s and at 3600 s against 60 s. The electric heater's 1.08 % at 3600 s (full year 2021) is
  accepted. For the houses with solar thermal the residual comes from the solar pump's whole-step decisions
  (collector yield -7 % / -24 % with a gas boiler and -15 % / -15 % with a heat pump at 900 / 3600 s, the backup's
  gas +1.3 % / +4.3 % and electricity +9.2 % / +43 %, measured before the heat pump's hot-water cap); it moves to
  hisim-fxix.11, due before RenoVisor's solar results are relied on (RenoVisor runs at 900 s). The heat pump's
  hot-water electricity residual (+1.73 % at 900 s, +18.1 % at 3600 s against 60 s, while its hot-water heat agrees
  within 0.4 %) comes from whole-step charge decisions (D4) into hplib's near-COP-1 range above about 63 °C and from
  the energy manager's raise switching inside a step; it moves to hisim-fxix.12 (owner, 2026-10-09). The
  heat-pump-and-solar twin's year at 3600 s aborts on a 2-cycle the pinned 0.5 under-relaxation cannot damp
  (hisim-fxix.13; the owner kept 0.5, 2026-10-09).
- **D. SH chain.** Scope: `SimpleHotWaterStorage` as node, HDS same-step return and pipe-water node without a
  buffer, the SH circuits, the iteration histogram test. Done when: buffer and HDS close per step, district
  heating's bill equals pipe-water `ΔU` plus delivered heat (hisim-9uoo.12 closed), no step at
  `force_convergence` at 900 s in the boiler, heat-pump and district-heating twins, goldens re-blessed.
- **E. Energy balance.** Scope: `HydronicPort` on every circuit in #870/#871's check. Done when: the balance
  report closes for every twin, stratification-path storages flagged, no result changes.
- **F. Cooling.** Scope: cooling into a buffer, heat-pump cooling and brine-pump electricity to the meter, the
  refusal removed. Done when: hisim-9uoo.15 and hisim-9uoo.17 closed, test case 5 of §8 passes.
- **G. Close-out.** Scope: comparison runs, close #864. Done when: the comparison table is on the PR and #864 is
  closed.

C and D touch the same dual-circuit generators: between them a generator's DHW circuit is on pure ports and its
SH circuit is not. If main must never hold that mixed state, C and D go in one PR.

## 10. Owner decisions (2026-10-01)

**D1 — Boiler control: keep the power `control_signal` and fixed lift.** `T_sup = T_ret + ΔT` with
`P_th` from `control_signal` and `modulate_power`, `m = P_th/(c ΔT)`; no flow thermostat. Consequence: every
firing boiler step is the power-limited pure-port case of §7 (≈11 iterations at 900 s, just past the 10-try
`force_convergence` limit), so the node acceleration and the iteration histogram test cover the boiler setups
explicitly (§6, §9.5 D). #864's setpoint feed-forward and inverse fuel law are dropped.

*Amendment (owner, 2026-10-09).* Every fixed-lift generator (boiler, electric heater's DHW side, heat pump's DHW
side) has a maximum supply temperature and throttles to it when `T_ret + ΔT` would exceed it (§5); on a throttled
step the heat is `m c (T_max − T_ret)` and the fuel or electricity follows from it through the generator's own
efficiency or COP law. Unthrottled steps stay as above. *Second amendment (owner, 2026-10-09):* the boiler's and the
electric heater's hot-water supply is also capped at their controller's set temperature (70 °C, 75 °C), with
`T_max` as the upper bound (§5), and a throttled boiler burns at the efficiency of its commanded power, so the
inverse fuel law is not used (§5.1). The heat pump's hot-water outlet is capped the same way at its controller's
`t_max` plus the energy manager's raise, below its 75 °C maximum (§5.2).

**D2 — Heat-pump authority: hplib's `m_dot` and `T_out`.** `P_th = m c (T_out − T_in)`, `P_el = P_th/COP`;
resolves hisim-4g9.21, with the delivered heat differing from the calibrated SCOP value by up to ~0.5 %. The
cache keeps today's 0.1 K rounding (`more_advanced_heat_pump_hplib.py:1997-1999`). Consequence: a staircase in
the return temperature that can make an iteration hop between bins; the histogram test watches for it, and
interpolation is the remedy if it shows. *Amendment (owner, 2026-10-09):* the staircase showed (a heat pump's
hot-water outlet throttled at 75 °C left the storage's step mean without a fixed point), and the interpolation is
applied in stage C (§5.2).

**D3 — Generator → HDS without a buffer: the HDS pipe water is the node** (§4.6). Fixes hisim-9uoo.12 and keeps
the RenoVisor district-heating path as it is. Consequence: ≈24 iterations at 900 s before acceleration; the
first candidate for hisim-4g9.22 if the histogram fails.

**D4 — Feed-forward and DHW priority: drop both.** No temperature forecast for on/off controllers, no
`DhwChargeYield`; controllers stay as on main, deciding on start-of-step values. Consequence: hisim-6ehm stays
open as a validation item; heat-pump comfort at 900 s is compared in the validation runs (§9.4) and, if the
underheating returns, it is solved then. *Amendment (owner, 2026-10-09):* the solar controller decides on the node's
step mean `T̄` and the collector's answer there to switch the pump on (§5.3), the one exception; its full-tank
stop stays on `T0`. The in-step supply cap at the
controller's set temperature (§5) relates to D4 as a limit, not a forecast: the generator stops its supply at the
controller's target inside the step, while the controller still decides on `T0`; the heat pump's hot-water
controller ends a charge within 0.5 K of its set temperature, since a capped supply never passes it (§5.5).

**D5 — Stratification path: keep for now.** `heat_exchanger_is_present=False` and the `dt/3600` mixing stay as they
are; bead hisim-fxix.1 tackles it later. Consequence: the node rule covers only the fully mixed path;
the stratification path is documented as not energy-consistent, outside the balance guarantee, and flagged by the
energy-balance check (#870) rather than hidden (§4.5).

**D6 — Dual circuits: one circuit per step,** SH or DHW as `DiverterValve` decides on `T0`. Consequence: a DHW
charge takes whole steps; within a step every flow is constant, as §4.1 assumes.

**D7 — Simulator: no change.** Node-side acceleration and the iteration histogram test only; the convergence
burden sits in the nodes. A per-output tolerance only if the histogram shows the watt outputs dominate (§6).
*Amendment (owner, 2026-10-10):* the iteration memory moves out of the nodes. The simulator accelerates the outputs
a component declares accelerated (§6) and starts every step from the previous step's converged values (the warm
start, hisim-4g9.23); both are generic and know nothing about a component.

**D8 — Staging: staged PRs straight into main,** each re-blessing the goldens it changes (§9.5). Consequence:
goldens and RenoVisor results move in several steps, and each stage that changes results is announced on
renovisorissues.

## 11. Beads

hisim-4g9.16 (vessels conserve energy; re-solved by §4), hisim-4g9.21 (heat-pump flow vs `P_th`; resolved by D2,
§5.2), hisim-4g9.22 (circuit laws; §6 escalation, D3 first candidate), hisim-9uoo.12 (district heating without a
buffer; §4.6, D3), hisim-9uoo.15 (cooling and brine-pump electricity metering; §5.2), hisim-9uoo.17 (cooling
through a buffer; §4.2), hisim-6ehm (buffer control at coarse steps; open as a validation item, D4, §9.4);
renovisorissues #76 (stated vs simulated boiler efficiency; §9.4); the D5 stratification bead hisim-fxix.1; the epic hisim-fxix with one bead per stage (A hisim-fxix.2 … G hisim-fxix.8).
