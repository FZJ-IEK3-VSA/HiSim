# The space-heating buffer as a two-mode buffer: an iterated buffer up to 600 s, stage D's start-of-step buffer above it

Implementation specification for the owner's decision of 2026-10-17. It records what is built, why each regime
is chosen, how a step is computed in each, the one port interface both share, the changes against the open stage D
stack and against main, what to expect, how it is verified, and the pull-request order. It is a plan: every number
is marked **measured** (from the files under `/home/noah/hisim/loop-design/` and the raw runs they name) or
**expected** (derived here, not yet run). Earlier decisions: `roadmap/hydronic_coupling_spec.md` (§4 the mixed
node, §6 convergence, §9.5 staging, §12 stage D as decided) and `roadmap/hydronic_part_load_spec.md` (decisions
1–22, §9 thresholds).

**Terms.** *Buffer*: `SimpleHotWaterStorage` with its heat exchanger, the space-heating store between the
generator and the heat distribution system in every recorded twin. *Generator*: boiler, heat pump, CHP or
district-heating substation charging the buffer; the solar collector is the *secondary generator*. *Distribution*:
`HeatDistribution`, the circuit that takes heat from the buffer to the building's emitters. *Step*: one time step of
length `dt_s`. *Pass*: one evaluation of every component within a step; the simulator repeats passes until no output
moves by more than 1e-4 (`hisim/simulator.py:424–504` on main). *Forced pass*: a pass after the 11th, with
`force_convergence` set, in which controllers hold their decision (`simulator.py:488–489`). *T0*: the buffer's
temperature at the start of the step, in °C. *T̄* (`T_mean`): the mean of its temperature over the step, in °C.
*T_end*: its temperature at the end of the step, the next step's T0. *Booked heat*: what the generator's circuit
carries, `m c (T_sup − T_ret)`. *Delivered heat*: the distribution's `ThermalPowerDelivered` to the building.
*Turnover `a`*: `Σ(m c) dt / C`, how many times the step's circuit flows would exchange the buffer's water, with
`C = M c` the buffer's heat capacity. *θ*: the contraction of the iterated node per pass, `1 − (1 − e^(−a))/a`.

**Measurement labels** (the files under `/home/noah/hisim/loop-design/` use them, so the spec keeps them where it
cites a measurement): S = start-of-step buffer as measured on stageD-9-close-out; M2 = iterated buffer (draft
`38f755ee` + #1048 + window margin); M4, M5 = iterated buffer with fractions above 600 s (dropped); Mw = M + window
margin; main+T = main with a scaled forcing threshold (dropped).

---

## 1. The decision, and the trade-off against the alternatives

### 1.1 Decision (owner, 2026-10-17)

The buffer runs one of two models, chosen once per run from the step length against
`SimulationParameters.part_load_above_seconds` (default 600 s, `hisim/simulationparameters.py:288` on main):

- **Steps at or below the threshold (iterated buffer).** The buffer is one `hydronics.MixedNode`, iterated with its
  circuits within the step. It publishes its step mean T̄ to the generators as their return and to the distribution as
  its supply, and T0 to the controllers. The distribution books this step's heat (same-step return). Every circuit runs
  whole steps; no fractions. This is variant "M2" of the measurements: draft commit `38f755ee` on
  `origin/sh-buffer-mixed-node`, plus the energy manager's raise hold under `force_convergence` (PR #1048) and the
  window margin (commit `351e4854`, PR #1010).
- **Steps above the threshold (start-of-step buffer).** Stage D's start-of-step buffer: `hydronics.StartOfStepNode` (PR
  #1009), the buffer as that node (#1011), the end temperatures without each circuit and the exact part-load rules
  (#1013), both ends of the buffer running fractions of the step for the boiler and district-heating chain (#1014) and
  for the heat pump (#1012), the close-out (#1035). Branches `stageD-3-start-node` … `stageD-9-close-out`.

Both models publish the same ports (§4), so generators, controllers and the distribution do not depend on which
one runs.

Why a two-mode buffer and not one model: the iterated buffer converges only while `a` is small (§2) and is the more
faithful model while it does, because it sees the buffer move within the step; the start-of-step buffer converges at any
step length but needs the part-load fractions to stay on target, and the fractions have their own cost and residual
error. 600 s is where the iterated buffer's cost is still bounded (measured maximum 35 passes of a 1000-pass budget) and
the start-of-step buffer's whole-step errors have not yet begun.

### 1.2 Trade-off table

Deviation is against the same variant's 60 s run unless stated. "HP" is `household_heatpump_building_sizer`
(386 kg buffer), "gas" is `household_gas_building_sizer` (170 kg). Loop times are the time-step loop alone, in
seconds; the 60 s times were measured with two runs sharing the machine ("contended"), the 900 s times of the
grouped RenoVisor twins one at a time ("uncontended", `sh_buffer_m5_2026-10-17.md` Part B).

| | main (as merged, `341e6b9e`) | start-of-step buffer everywhere (stage D stack top, `e26cea8a`) | iterated buffer everywhere (M2) | **Two-mode buffer (iterated ≤ 600 s, start-of-step above)** |
|---|---|---|---|---|
| Buffer closes per step | No: the mixing law destroys 0.6 % (gas) to 3.9 % (pellets) of the booked heat even at 60 s, 18.7 % (HP) to 24.6 % (pellets) at 900 s (hisim-4g9.16, **measured**) | Yes, residual < 1e-11 kWh/a (**measured**) | Yes (**measured**) | Yes, both paths (**measured** each) |
| Delivered heat at 900 s | HP −0.50 %, gas −0.18 % (against main's own 60 s, which is itself wrong) | HP −0.70 %, gas −0.13 % | HP −0.79 %, gas −0.06 % | = start-of-step: HP −0.70 %, gas −0.13 % against the start-of-step buffer's 60 s; against the two-mode buffer's 60 s reference (M2) **expected** HP ≈ −0.71 %, gas ≈ −0.15 % (§6.1) |
| Booked heat at 900 s | HP +18.6 %, gas +5.1 %, pellets +25.8 % | HP −1.07 %, gas +0.02 % | HP −0.26 %, gas −0.04 % | = start-of-step |
| SH electricity (HP) / fuel (gas) at 900 s | +16.3 % / +4.0 % | −3.54 % / −9.31 % | +3.18 % / −6.67 % (M2 overcharges: year-mean buffer 32.7 °C against 31.7 at 60 s, seasonal COP 4.42 against 4.57) | = start-of-step: −3.54 % / −9.31 %; against M2's 60 s **expected** HP ≈ −4.5 % (M2's 60 s uses +0.96 % more SH electricity than the start-of-step buffer's 60 s, §6.1) |
| Degree-hours at 900 s (60 s in brackets) | 1.7 (7.8) | 23.0 (16.3) | 25.9 (11.2) | 23.0 (11.2) |
| Delivered / SH electricity at 3600 s (HP) | −1.09 % / +34.4 % | −1.99 % / −5.91 % | aborts at the 100-pass limit; with a 1000-pass limit −0.96 % / **+17.45 %** (`M2k1000`, max 121 passes) | = start-of-step |
| Convergence, 60 s | finishes; HP 5346 forced steps, max 19 passes | finishes; HP 5174 forced, max 19 | finishes with #1048; HP max 21, gas max 11 (**measured**); without #1048 the HP twin aborts on 22 May (two-point cycle through the energy manager) | = M2 |
| Convergence, 600 s | finishes, max 19–21 | finishes, HP max 29, gas max 12 | finishes, HP max 31, gas max 35, 49–70 % of steps forced | = M2 |
| Convergence, 3600 s | finishes, HP max 85 | finishes, HP max 84, gas 26 | aborts (θ = 0.82–0.91 per pass, §2) | = start-of-step |
| Loop time 60 s, gas / HP (contended) | 146 / 272 s | 176 / 300 s | 227 / 382 s | 227 / 382 s (= M2) |
| Loop time 900 s, gas / HP (uncontended, grouped twins) | 9.0 / 20.8 s | 14.4 / 28.7 s (1.60× / 1.38× main) | ≈ 55 / 55 s (sizer twins, contended; M4 uncontended 61.9 / 58.0 s) | 14.4 / 28.7 s (= start-of-step) |
| Maintenance | one buffer path with a known leak | one node, two rules, three controllers with part-load parts | one node, no fractions; cannot run 3600 s | two node paths in one component (about 60 lines more than the start-of-step buffer), one shared publishing routine; the rules of the start-of-step buffer unchanged |
| Lock-in | — | 60 s reference is the start-of-step buffer's; the fractions' control target is the 60 s cycle's end temperature | 60 s reference is M2's | 60 s reference is M2's (one bless); the threshold is already a parameter |

What the two-mode buffer buys over the start-of-step buffer everywhere, **measured** at the steps it changes: at 300 and
600 s the heat-pump twin delivers −0.21 / −0.53 % instead of −0.32 / −0.62 %, uses −0.83 / −1.32 % SH electricity
instead of −0.77 / −2.12 %, and has 16.0 / 21.3 degree-hours instead of 19.7 / 28.0 (`sh_buffer_m2_2026-10-17.md`). At
60 s it costs about 30 % more loop time (gas 227 against 176 s, heat pump 382 against 300 s, contended) and buys the
same-step coupling: the degree-hours fall from 16.3 to 11.2 in the heat-pump twin. What it does not buy: anything at
900 s or above, where the start-of-step buffer runs unchanged.

### 1.3 Dropped, and why

- **M4/M5's part-load fractions on the mixed node** (`sh_buffer_m4_2026-10-17.md`, `sh_buffer_m5_2026-10-17.md`). They
  converge at every step length and remove M2's overcharge (HP SH electricity −3.4 % at 900 s instead of +3.2 %), but
  cost 2.8–6.9× main's loop time at 900 s (M4) against the start-of-step buffer's 1.4–1.6×, with 64–70 % of steps
  forced; the fraction is frozen at pass 10 before it settles (gas at 3600 s: median miss 6 K, buffer up to 66 °C); M5's
  continuous rule needed new full-load port quantities (`ThermalPowerSpaceHeatingAtFullLoadInWatt`,
  `MaximalThermalPowerInWatt`, `HeatCapacityInJoulePerKelvin`), and still left gas at 3600 s 27 % of fraction steps off
  target. The start-of-step buffer reaches the same accuracy at a fifth of the cost.
- **The generators' full-load-heat outputs** (M5): only the dropped rule needed them.
- **The scaled forcing threshold** `max(10, ceil(dt / 60 s))` ("main+T"): no effect at or below 600 s (it stays 10
  there), and above 600 s the start-of-step buffer runs, whose fractions settle in one pass. It changed main's KPIs by ≤
  0.13 % and is at most a separate small PR.
- **Convergence accelerations** (`convergence_acceleration_2026-10-17.md`): per-unit tolerances rejected by the owner;
  the extrapolated warm start gave no gain and moved results; the inner iteration was 2.7–3.1× slower; an
  operating-point lookup was measured and not worth it.
- **Progress-based aborts**: each costs a check per pass, where the pass is already the unit of cost (≈ 84 µs on main
  and the iterated buffer alike).

### 1.4 Already merged and relied on (checked 2026-10-17 with `gh pr view`)

| PR | State | What the two-mode buffer relies on |
|---|---|---|
| #1007 | merged | the distribution's pump stands while its valve is shut |
| #1008 | merged | the building publishes `TemperatureIndoorAirAtStartOfStepInCelsius`; the distribution's room clamp reads it in both modes |
| #1050 | merged | pass limit `max(100, ceil(100 · dt / 60 s))`: 100 at 60 s, 500 at 300 s, 1000 at 600 s (`simulator.py:183, 323–324`) |
| #1048 | **open**, base main | the energy manager holds its set-temperature raise under `force_convergence`. **Hard prerequisite of the iterated buffer**: without it the heat-pump twin at 60 s aborts on 22 May (`sh_buffer_models_2026-10-17.md`, cause 2) |
| #1051 | **open**, base main | non-finite check once after the run instead of per call; a speed-up of every pass (finite checks were ~11 % of M5's profile). Not a prerequisite |
| #1009 … #1035 | **open**, stacked on main | the start-of-step buffer, including the window margin #1010 that the iterated buffer needs for the gas twin at 300 / 600 s (cause 1) |

---

## 2. Why each regime: the convergence analysis

### 2.1 The mixed node contracts by θ per pass

With the iterated buffer the generator reads the buffer's T̄ and supplies `T̄ + lift`; the distribution reads T̄ and
returns `T̄ − drop`; the buffer reads both and computes a new T̄ (`MixedNode.step`, `hisim/hydronics.py:632–703` on
main):

```python
# hisim/hydronics.py:688-697 (origin/main), inside MixedNode.step
t_inf = numerator / total_conductance          # the weighted mean of inflow and ambient temperatures
a = total_conductance * dt / capacity          # the relaxation exponent a = (Σ m_i c + UA) dt / C
phi = relaxation_mean_factor(a)                # (1 - e^(-a)) / a
t_mean = t_inf + (t0 - t_inf) * phi
t_end = t0 + (t_inf - t0) * (a * phi)          # = t_inf + (t0 - t_inf) e^(-a)
```

`t_inf` is almost exactly the T̄ the circuits read (plus a constant: the lift and drop weighted by the flows), so
the new T̄ depends on the old one with the factor `1 − φ(a) = θ`. One pass shrinks the error by θ; the passes to
go from an initial error `e0` to the tolerance `tol` are

`passes ≈ ln(e0 / tol) / ln(1 / θ)`, with `θ = 1 − (1 − e^(−a)) / a`, `a = Σ(m c) dt / C`.

For `e0 = 1 K` and `tol = 1e-4 K`, `ln(e0 / tol) = 9.2`. Watt outputs make it worse: the buffer's
`ThermalPowerFromHeatGenerator` is `m c (T_sup − T̄)`, about 1250 W/K times the temperature error, and the
simulator's 1e-4 is absolute, so a watt column needs the temperature to about 1e-7 K, three more decades
(`ln(1e-7) / ln(1e-4) ≈ 1.75× the passes`).

### 2.2 `a` and θ per twin and step length (measured medians over steps with generator flow)

From `sh_buffer_models_2026-10-17.md`; passes predicted for 1 K → 1e-4 K, and measured as mean / maximum per step
over the year (M2 where it ran a year; gas at 300 / 600 / 1800 s is Mw = M + window margin, which equals M2 for the
gas twin).

| Twin (buffer mass) | 60 s | 300 s | 600 s | 900 s | 1800 s | 3600 s |
|---|---|---|---|---|---|---|
| gas (170 kg): `a` / θ | 0.19 / 0.09 | 0.98 / 0.36 | 1.97 / 0.56 | 2.91 / 0.68 | 5.84 / 0.83 | ≈ 11.7 / 0.91 |
| predicted passes | 3.8 | 9.0 | 15.9 | 23.9 | 49 | 98 |
| **measured** mean / max | 6.2 / 11 | 10.4 / 21 | 16.0 / 35 | 23.3 / 51 | 38.9 / 99 | **abort** (100-pass limit; contraction 0.907 per pass measured) |
| heat pump (386 kg): `a` / θ | 0.09 / 0.04 | 0.44 / 0.19 | 0.86 / 0.33 | 1.36 / 0.45 | 2.87 / 0.67 | ≈ 5.5 / 0.82 |
| predicted passes | 2.9 | 5.5 | 8.3 | 11.5 | 23 | 46 |
| **measured** mean / max | 5.7 / 21 | 8.1 / 26 | 10.5 / 31 | 12.8 / 37 | 20.0 / 59 | **abort** at 100; 35.7 / 121 with a 1000-pass limit |
| heat pump 70 % (270 kg): `a` / θ | 0.10 / 0.05 | 0.54 / 0.23 | 1.04 / 0.38 | 1.56 / 0.49 | 3.17 / 0.70 | ≈ 6.3 / 0.84 |
| **measured** mean / max | 6.0 / 21 | 8.7 / 25 | 11.7 / 31 | 14.8 / 39 | 24.3 / 67 | **abort** at 100 |

The means follow the prediction within a few passes (the mean includes the many idle steps and the steps the
hot-water cylinder dominates); the maxima are 2–3× the prediction, the watt columns' extra decades.

### 2.3 Why 600 s is safe for the iterated buffer

- **Bounded cost, measured.** At 600 s the largest step needed 35 passes (gas) and 31 (heat pumps); the budget
  since #1050 is 1000 passes at 600 s (`Simulator.maximum_passes_for_step_length`, `simulator.py:323–324`). At 60 s
  the maxima are 11 and 21 against a budget of 100. The mean passes at 600 s are 16.0 / 10.5 / 11.7, which is
  1.4–3.5× the start-of-step buffer's 4.6 / 5.3 / 5.4; this is the loop-time ratio of §1.2.
- **The abort causes are gone with #1048 and #1010, measured.** The four abort causes of `sh_buffer_models` were
  the window flip (fixed by the 0.01 K margin, #1010), the energy manager's raise cycle closed through T̄ (fixed by
  the raise hold, #1048), the building at its set temperature and the heat pump against the energy manager's
  dispatch (both also energy-manager cycles at the PV-surplus boundary: "Mwin" without #1048 aborts on the same
  steps as M, M2 does not). M2 finished every year from 60 to 1800 s in all three twins.
- **The step-mean bias is small, computed (§3.1).** In the owner's example system the node's T̄ sits 0.006 K above
  the exact time mean at 60 s and 0.54 K at 600 s; at 900 s it is 1.1 K and at 1800 s 3.2 K. It is the price of
  publishing one return temperature for the whole step to a generator that would, in reality, track the buffer
  moment by moment. At or below 600 s it is below the controllers' hysteresis (±5 K) and the heat pump's COP
  resolution.
- **Whole steps have not yet begun to overcharge.** M2's SH electricity against its 60 s is −0.83 % at 300 s and
  −1.32 % at 600 s, then **+3.18 %** at 900 s and **+14.06 %** at 1800 s (heat pump): a controller that decides on
  T0 lets the heat pump run the whole step past its target once a step carries several kelvin. At 600 s one whole
  step moves the HP twin's buffer by at most about 3 K (7.8 kW · 600 s / 1.61 MJ/K).

### 2.4 Why the start-of-step buffer above

- θ ≥ 0.68 at 900 s in the gas twin and ≥ 0.82 at 3600 s everywhere: 24–130 predicted passes, with the watt decades 2–3×
  that. Measured: M2 aborts at 3600 s at the 100-pass limit in all three twins from the slow, monotone contraction alone
  (factor 0.854 / 0.876 / 0.907 per pass; held regimes are not involved: "M3" aborts on the same steps). With a
  1000-pass budget it converges (max 121 passes) but is wrong: +17.45 % SH electricity.
- The start-of-step buffer's node publishes nothing that moves within the step (`StartOfStepNode.step`,
  `hisim/hydronics.py:807 ff.` on `origin/stageD-3-start-node`): "Nothing the node publishes changes while a step
  iterates, so the circuits do not iterate against the node". Its fractions solve a line and land in one pass (§3.2).
  Measured at 900 s: 5.0 / 5.9 mean passes, loop 14.4 / 28.7 s uncontended, heat within 1 % of 60 s.
- RenoVisor runs at 900 s (`a` = 2.9 / 1.4 / 1.6 for the three twins), so RenoVisor runs the start-of-step buffer in
  every twin, as the stage D measurements and its 900 s-vs-60 s assertion (#1035) already cover.

---

## 3. Calculation flows, step by step

The owner's example system throughout: a 200 kg buffer, `c = 4180 J/(kg K)`, so `C = 836 000 J/K`; `T0 = 35 °C`;
a heat pump with 0.3 kg/s holding a 10 K lift (it supplies the return it reads plus 10 K); floor heating with
0.5 kg/s holding a 4 K drop (it returns the supply it reads minus 4 K); `UA = 2 W/K` to a 20 °C room. The circuit
conductances are `0.3 · 4180 = 1254 W/K` and `0.5 · 4180 = 2090 W/K`; `G = 1254 + 2090 + 2 = 3346 W/K`. The net
power into the buffer while both hold their differences is `1254 · 10 − 2090 · 4 = 4180 W` less the standby loss.
All numbers below are **computed with the real functions** (`MixedNode.step` and `relaxation_mean_factor` from
`hisim/hydronics.py` on main, `StartOfStepNode.step` from `origin/stageD-3-start-node`,
`StartOfStepPartLoadRule` from `origin/stageD-9-close-out`), not simulated.

For this system: `a = 3346 · dt / 836 000`:

| `dt_s` | 60 | 300 | 600 | 900 | 1800 | 3600 |
|---|---|---|---|---|---|---|
| `a` | 0.240 | 1.20 | 2.40 | 3.60 | 7.20 | 14.4 |
| θ | 0.111 | 0.418 | 0.621 | 0.730 | 0.861 | 0.931 |
| passes 1 K → 1e-4 K | 4.2 | 10.6 | 19.4 | 29.3 | 61.7 | 128 |

### 3.1 The iterated buffer at a short step: pass by pass

**Who reads and publishes what in one pass.** The simulator evaluates the components in its order
(`evaluation_plan`); each reads the step's current values, which in the first pass are the previous step's
converged values (`simulator.py:438–440`: "the first pass reads the previous step's value from every output
computed later in the order").

1. *Controllers* (boiler's, heat pump's space-heating, CHP's) read the buffer's T0 port, which does not move
   within the step, and decide on/off against their hysteresis (as on main, D4 of the coupling spec). The
   distribution controller reads the building's demand and sets the valve.
2. *Generator* reads its return port (T̄ in this mode), supplies `T̄ + lift` at its flow, books
   `m c (T_sup − T̄)`, electricity `heat / COP(T_sup)` for a heat pump, fuel from the burner law for a boiler.
3. *Distribution* reads its supply port (T̄), takes the building's demand at its flow, returns `T̄ − drop` clamped
   at the room's start-of-step temperature, and books `m c (T̄ − T_ret)` **in this step** (the same-step booking
   of `38f755ee`, `heat_distribution_system.py` hunk at line 476: "behind a buffer the circuit's heat is this
   step's, from the supply the buffer publishes in this pass").
4. *Buffer* reads the two supplies and flows and the distribution's return and computes `MixedNode.step` from T0;
   publishes T̄ on the return and supply ports, T0 on the sensor port, T_end, the circuit heats and the loss
   (`simulate_mixed_node`, `38f755ee`, `simple_water_storage.py` hunk at line 1215).
5. *Building* reads the distribution's heat of this step.
6. The simulator compares every output with the previous pass; if any moved by more than 1e-4, another pass.

**60 s (`a = 0.240`, θ = 0.111), starting from the guess T̄ = T0 = 35 °C:**

| pass | T̄ read | generator supplies | distribution returns | node T̄ | node T_end | Q_gen, kJ | Q_dist, kJ | loss, kJ |
|---|---|---|---|---|---|---|---|---|
| 1 | 35.00000 | 45.00000 | 31.00000 | 35.13769 | 35.26478 | 742.04 | −518.87 | 1.817 |
| 2 | 35.13769 | 45.13769 | 31.13769 | 35.15296 | 35.29416 | 751.25 | −503.52 | 1.818 |
| 3 | 35.15296 | 45.15296 | 31.15296 | 35.15466 | 35.29742 | 752.27 | −501.81 | 1.819 |
| 4 | 35.15466 | 45.15466 | 31.15466 | 35.15484 | 35.29778 | 752.39 | −501.62 | 1.819 |
| 5 | 35.15484 | 45.15484 | 31.15484 | 35.15486 | 35.29782 | 752.40 | −501.60 | 1.819 |

The error shrinks by 0.1108, 0.1097 per pass (θ = 0.111 predicted); five passes reach 1e-4 K. At the fixed
point the generator's heat is `1254 · 10 · 60 = 752.4 kJ` and the distribution's `−2090 · 4 · 60 = −501.6 kJ`
exactly: a circuit holding its difference books the same heat whatever T̄ is, and T̄ only decides at which
temperature the heat pump's COP is evaluated and which supply the floor heating sees. The balance:
`752.40 − 501.60 − 1.82 = 248.98 kJ = 836 000 · (35.29782 − 35.0)` closes to the last digit.

**600 s (`a = 2.40`, θ = 0.621), same start:**

| pass | T̄ read | node T̄ | node T_end | Q_gen, kJ | Q_dist, kJ |
|---|---|---|---|---|---|
| 1 | 35.00000 | 35.77060 | 36.12793 | 6944.2 | −5982.3 |
| 2 | 35.77060 | 36.24909 | 36.82830 | 7164.0 | −5616.0 |
| 3 | 36.24909 | 36.54619 | 37.26319 | 7300.5 | −5388.6 |
| 6 | 36.84523 | 36.91636 | 37.80501 | 7470.5 | −5105.2 |
| 19 | 37.03250 | 37.03264 | 37.97520 | 7523.9 | −5016.2 |
| 20 | 37.03264 | 37.03273 | 37.97533 | 7523.9 | −5016.1 |

Contraction 0.6209, 0.6209, 0.6208 per pass (θ = 0.621); 20 passes to 1e-4 K, which is why the simulator's forcing after
10 passes is routine with the iterated buffer at 600 s (49–70 % of steps forced, measured) and harmless: the controllers
hold, the physics keeps contracting.

**The step-mean bias.** A real heat pump holding a 10 K lift would track the buffer's rising temperature moment by
moment, so the buffer would warm along a straight line (constant net power 4180 W less the loss) and its time mean
would be `(T0 + T_end) / 2` to within the loss. The node instead takes the inflow temperatures as constant over
the step (`T̄ + 10`, `T̄ − 4`) and relaxes exponentially towards `T∞ ≈ T̄ + 1.25 K`, front-loading the rise.
T_end agrees (the net power is the same), T̄ does not:

| `dt_s` | node T̄ | exact time mean | bias of T̄ | node T_end | exact T_end |
|---|---|---|---|---|---|
| 60 | 35.1549 | 35.1489 | +0.006 K | 35.2978 | 35.2978 |
| 300 | 35.8898 | 35.7444 | +0.145 K | 36.4886 | 36.4887 |
| 600 | 37.0329 | 36.4885 | +0.544 K | 37.9756 | 37.9763 |
| 900 | 38.3472 | 37.2322 | +1.115 K | 39.4605 | 39.4617 |
| 1800 | 42.6733 | 39.4613 | +3.212 K | 43.9024 | 43.9100 |

The bias grows with `a²` while `a` is small and is the temperature at which the iterated buffer evaluates the heat
pump's outlet (COP) and the floor heating's supply. At 60 s it is nothing; at 600 s half a kelvin, about 1 % of a heat
pump's COP; at 1800 s three kelvin. This, together with whole steps past the controller's target, is M2's measured
overcharge from 900 s on (§2.3), and the second reason the threshold is not above 600 s.

### 3.2 The start-of-step buffer at 900 s: the start-of-step booking, the raising fraction, the floor fraction

**Who reads and publishes what.** The controllers read T0 (with the start-of-step buffer the return and supply ports
carry T0 too). The generator supplies `T0 + lift` and books `m c lift` on the ratio its controller commands; the
distribution returns `T0 − drop` and books `m c drop` on its ratio; the buffer computes `StartOfStepNode.step` and
publishes T0 on the circuit ports, T_end, and T_end without each circuit; the two part-load controls read T_end,
T_end-without and the ratio run and publish the next ratio. Nothing the buffer publishes depends on what it reads except
T_end and the T_end-without values, and the rules read those linearly, so the ratios land in one pass.

```python
# hisim/hydronics.py (origin/stageD-3-start-node), StartOfStepNode.step, the arithmetic
heat_in = tuple(circuit_heat_j(mass_flow_kg_per_s=inflow.mass_flow_kg_per_s,
                               t_supply_c=inflow.temperature_c, t_return_c=t0, dt_s=dt)
                for inflow in arguments.inflows)                      # m_i c (T_i - T0) dt
loss = ua * (t0 - t_amb) * dt                                         # UA (T0 - T_amb) dt
net = sum(heat_in) - loss - drawn
t_end = t0 + net / capacity
```

```python
# hisim/components/simple_water_storage.py:1117-1119 (origin/stageD-9-close-out), simulate_start_of_step_node
for channel, heat_in_joule in zip(
    self.water_temperature_at_end_of_step_without_circuit_channels, node.heat_in_j_per_inflow
):
    stsv.set_output_value(channel, node.t_end_c - heat_in_joule / self.heat_capacity_in_joule_per_kelvin)
```

**Pass 1, both ratios 1 (what the previous step left, or the first guess):** the heat pump brings
`1254 · 10 · 900 = 11.286 MJ`, the floor takes `2090 · 4 · 900 = 7.524 MJ`, the loss is `2 · 15 · 900 = 27 kJ`.
`T_end = 35 + (11.286 − 7.524 − 0.027) MJ / 836 kJ/K = 39.4677 °C`.
`T_end without the heat pump = 39.4677 − 11.286 MJ / C = 25.9677 °C`;
`T_end without the floor = 39.4677 + 7.524 MJ / C = 48.4677 °C`.

**The heat pump's raising fraction.** Its controller's target is the heating curve's supply temperature, say
38 °C (plus the energy manager's raise, 0 here); the aim is the target plus half the 0.05 K band, 38.025 °C
(`PartLoadRule.aim_temperature_in_celsius`, `part_load.py:91–104`). The rule (`part_load.py:462–508`):

```python
# hisim/part_load.py:495-507 (origin/stageD-9-close-out), StartOfStepPartLoadRule.raising_ratio
if end_temperature_without_circuit_in_celsius >= target_temperature_in_celsius:
    return 0.0                                   # the store ends above target on its own
deviation_in_kelvin = end_temperature_in_celsius - target_temperature_in_celsius
if 0.0 <= deviation_in_kelvin <= PartLoadRule.TARGET_BAND_IN_KELVIN:
    return ratio_run                             # inside the band: keep
rise_in_kelvin = end_temperature_in_celsius - end_temperature_without_circuit_in_celsius
if ratio_run == 0.0 or rise_in_kelvin <= 0.0:
    return 1.0
needed_in_kelvin = aim - end_temperature_without_circuit_in_celsius
return min(1.0, max(0.0, ratio_run * needed_in_kelvin / rise_in_kelvin))
```

`r = 1 · (38.025 − 25.9677) / (39.4677 − 25.9677) = 12.057 / 13.5 = 0.8931`.

**The floor's lowering fraction** (`part_load.py:510–551`): the floor is the room's start-of-step temperature,
20 °C. `T_end without the floor = 48.4677 > 20`, the fall is `48.4677 − 39.4677 = 9.0 K`, the allowed fall
`48.4677 − 20 = 28.47 K`: `r = min(1, 1 · 28.47 / 9.0) = 1`. The floor heating runs the whole step.

**Pass 2, ratios 0.8931 and 1:** the heat pump runs `0.8931 · 0.3 = 0.2679 kg/s` averaged flow at its full-load
outlet 45 °C (`part_loaded_space_heating`, `more_advanced_heat_pump_hplib.py:1429–1465` on stageD-9: "the
averaged flow, the full-load flow times the ratio, at the full-load outlet temperature; the thermal and electrical
power follow from that flow at the step's COP"), so `Q_gen = 0.8931 · 11.286 = 10.080 MJ`.
`T_end = 35 + (10.080 − 7.524 − 0.027) / 0.836 = 38.0250 °C`: the aim, to four decimals.
`T_end without the heat pump` is still 25.9677 (it does not depend on the heat pump's ratio), `T_end without the
floor` is 47.0250. Both rules return their inputs unchanged (inside the band; whole step allowed). Converged in
two passes. The heat pump's electricity for the step is `10.080 MJ / COP(45 °C outlet)`, and the brine pump draws
0.8931 of its power.

**Why the fraction is needed at all (3600 s, whole step):** `T_end = 35 + (45.144 − 30.096 − 0.108) / 0.836 =
52.871 °C`, 15 K past the target; `T_end without the heat pump = −1.129 °C`, `without the floor = 88.871 °C` (this
is why #1013 bounds the "without" outputs at −300 to 300 °C). The rule gives `r = (38.025 + 1.129) / (52.871 +
1.129) = 0.7251`: the heat pump runs 2610 s of the hour at 0.2175 kg/s averaged, and the buffer ends at the aim.

**The floor at work (a cold buffer, 900 s):** `T0 = 22 °C`, no generator, the floor takes `7.524 MJ` →
`T_end = 22 − 9.0 = 12.996 °C`, `T_end without the floor = 21.996 °C` (the loss alone). Floor 20 °C: `r =
(21.996 − 20) / (21.996 − 12.996) = 0.2217`: the floor heating runs 200 s of the step and the buffer ends at
20.0 °C, not 13. The distribution controller applies this in forced passes too
(`heat_distribution_system.py:1185–1203` on stageD-9: "the floor is a limit of the water, not a decision that
could cycle").

### 3.3 At the threshold, and how a run picks its mode

**The choice is made once, when the buffer is built**, from `my_simulation_parameters.runs_part_load()`
(`simulationparameters.py:322–329` on main: "a 900 s run with the default 600 s threshold runs part load; a 60 s
run never does, nor a 600 s run, since the rule applies strictly above the threshold"). The buffer stores the
answer and dispatches in `i_simulate`; the part-load controls already key on the same method
(`StartOfStepPartLoadControl.__init__`, `part_load.py:615`: `self.runs_part_load =
controller.my_simulation_parameters.runs_part_load()`), so above the threshold they set fractions and at or below
it exactly 1 (`runs_fractions`, `part_load.py:660–662`).

**What a 600 s step looks like against a 601 s step** (the example system, `T0 = 35 °C`, target 38 °C; both
columns computed with the real functions):

| | 600 s, iterated | 601 s, start-of-step |
|---|---|---|
| return the heat pump reads | 37.033 °C (T̄) | 35.0 °C (T0) |
| heat pump outlet | 47.03 °C | 45.0 °C |
| ratio | 1 (whole step; no fractions with the iterated buffer) | raising rule: whole step would end at 37.9834 °C, below the 38 °C target, so `r = min(1, (38.025 − 28.9684) / (37.9834 − 28.9684)) = 1` |
| booked heat | 7523.9 kJ (= 1254 · 10 · 600) | 7536.5 kJ (= 1254 · 10 · 601) |
| distribution's heat | −5016.1 kJ | −5024.4 kJ |
| buffer T_end | 37.9753 °C | 37.9834 °C |
| passes to converge | 20 | 1 (nothing the buffer publishes moves) |

The heats and the end temperature agree to the step's extra second; the two columns differ by 2 K in the return
the heat pump reads (its outlet, so its COP) and in the pass count. With a higher net power the start-of-step column
would also run a fraction where the iterated column runs the whole step (the 900 s case of §3.2). This is the "small,
documented jump at the threshold itself, a step length nobody runs" already accepted in the part-load spec (§2 of
`hydronic_part_load_spec.md`, after decision 22), now between two buffer models instead of between whole and
fractional steps of one.

**Steps above 600 s with a threshold at or above the step** (for example 900 s with `part_load_above_seconds = 900`)
would run the iterated buffer above its range. #1035's `check_whole_steps_are_safe` (`simple_water_storage.py:979–1013`
on stageD-9) refuses exactly this combination today; the two-mode buffer keeps the refusal with a reworded message
(§5.1). At 3600 s with #1050's 6000-pass budget the iterated buffer would converge (measured: 121 passes maximum) and be
wrong (+17.45 % SH electricity), so the refusal is about correctness, not only cost.

---

## 4. The shared port interface and the selection mechanism

### 4.1 Outputs of `SimpleHotWaterStorage` with its heat exchanger, in both modes

| Output | iterated (≤ threshold) | start-of-step (> threshold) | Readers | Status |
|---|---|---|---|---|
| `WaterTemperatureToHeatGenerator` | T̄, the step mean | T0 | the generator (boiler `generic_boiler.py:1080`, heat pump `more_advanced_heat_pump_hplib.py:1014`, and any other generator wired to a buffer), as the return of its space-heating circuit; it books `m c (T_sup − T_read)` | exists on main; value changes per mode |
| `WaterTemperatureToSecondaryHeatGenerator` | T̄ | T0 | the solar collector (`solar_thermal_system.py`) as its return | exists on main |
| `WaterTemperatureToHeatDistribution` | T̄ | T0 | `HeatDistribution` as its supply; it returns supply − drop, clamped at the room's start-of-step temperature, and books `m c (T_read − T_ret)` in this step | exists on main |
| **`WaterTemperatureAtStartOfStepInCelsius`** | T0 | T0 | the generator controllers' sensor: `GenericBoilerController`, `MoreAdvancedHeatPumpHPLibControllerSpaceHeating`, `L1CHPController`; they decide on/off, mode and target against it with their hysteresis | **new in the two-mode buffer** (from `38f755ee`); the start-of-step buffer's controllers read `WaterTemperatureToHeatGenerator`, which happens to be T0 there; the two-mode buffer gives the sensor its own port so the controllers read T0 in both modes |
| `WaterMeanTemperatureInStorage` | T0 | T0 | KPIs, plots; no controller after the rewiring | exists on main; both drafts publish T0 on it (the name predates the node; it is the step's state variable) |
| `WaterTemperatureAtEndOfStepInCelsius` | T_end from `MixedNode.step` | T_end from `StartOfStepNode.step` | the part-load controls (boiler's, heat pump's, distribution's), which act on it only above the threshold | exists on stageD-6 and in `38f755ee` |
| `WaterTemperatureAtEndOfStepWithout{HeatGenerator, SecondaryHeatGenerator, HeatDistribution}InCelsius` | the end temperature of a second `MixedNode.step` from T0 without that inflow (exact for the mixed node; **not** linear in a ratio) — see open question Q2 for the cheaper alternative | `T_end − Q_i / C`, exactly linear in the circuit's ratio | the same part-load controls, above the threshold only | exists on stageD-6; the heating assemblies bound them at −300 to 300 °C |
| `ThermalPowerFromHeatGenerator`, `ThermalPowerFromSecondaryHeatGenerator`, W | `Q_i / dt` with `Q_i = m_i c (T_i − T̄) dt` | `Q_i / dt` with `Q_i = m_i c (T_i − T0) dt` | the energy balance (the buffer's side of the generator's port), KPIs | exist on main; in both modes they equal what the generator books, at the fixed point with the iterated buffer and in every pass with the start-of-step buffer |
| `ThermalPowerConsumptionHeatDistribution`, W | `−Q_dist / dt` | same | the energy balance, KPIs | exists on main |
| `ThermalEnergyFromHeatGenerator`, `…FromSecondaryHeatGenerator`, `…InputHeatDistributionSystem`, `ThermalEnergyIncreaseInStorage`, `ThermalEnergyInStorage`, Wh | from the same node step | same | KPIs | exist on main |
| `StandByHeatLoss`, W | `UA (T̄ − T_amb)` | `UA (T0 − T_amb)` | KPIs, the energy balance | exists on main |

What the readers must and must not assume:

- A **generator** reads its return port and books the heat its water carries. It never asks which model runs. Its
  part-load command (`PartLoadCommand`, `part_load.py:357–434`) reads a ratio that is 1 with the iterated buffer and the
  controller's fraction with the start-of-step buffer, and it reports the ratio it ran. Unchanged from stageD-7/8.
- A **controller** reads the sensor port (T0) and decides as on main. Its `StartOfStepPartLoadControl` reads T_end and
  T_end-without and publishes 1 with the iterated buffer (`runs_fractions()` is False) and the rule's ratio with the
  start-of-step buffer. Unchanged from stageD-7/8 except the sensor's source output.
- The **distribution** reads its supply port and the room's start-of-step temperature, books in the same step in both
  modes (identical code in `38f755ee` and stageD-5), and runs its controller's ratio (1 with the iterated buffer).
- The **buffer** is the only component that knows the mode.

Both ends of every circuit agree: with the start-of-step buffer in every pass (both compute from T0), with the iterated
buffer at the fixed point (the generator's `m c (T_sup − T̄)` and the buffer's `m c (T_sup − T̄)` use the same T̄ once
it has converged; the closure test of §7.1 checks this on the converged step).

### 4.2 Selection mechanism

Once per run, in the buffer's constructor (pseudocode; the real names follow `38f755ee` and stageD-9):

```python
# pseudocode for SimpleHotWaterStorage.__init__ (two-mode buffer)
self.runs_start_of_step_node: bool = my_simulation_parameters.runs_part_load()   # dt > part_load_above_seconds
if self.heat_exchanger_is_present:
    self.check_mixed_node_step_is_safe(                      # #1035's refusal, reworded (§5.1)
        buffer_name=self.component_name,
        seconds_per_timestep=my_simulation_parameters.seconds_per_timestep,
        part_load_above_seconds=my_simulation_parameters.part_load_above_seconds,
    )

# pseudocode for SimpleHotWaterStorage.i_simulate
if not self.heat_exchanger_is_present:
    self.simulate_stratification_path(timestep, stsv, force_convergence)   # unchanged, no recorded file uses it
elif self.runs_start_of_step_node:
    self.simulate_start_of_step_node(stsv)      # start-of-step buffer, stageD-5/6 code unchanged
else:
    self.simulate_mixed_node(stsv)              # iterated buffer, 38f755ee code
```

No other component learns the mode. The part-load controls already read `runs_part_load()` for themselves.

**Recorded twins and energy-system files.** The recorded files name, per component, the components it takes its default
connections from (the stageD-7/8 re-recordings added `- HeatDistributionSystem`, `- SimpleHotWaterStorage` and `-
MoreAdvancedHeatPumpHPLib` to the controllers' input lists), not the output names. The two-mode buffer's only wiring
change is the three controllers' sensor input moving from one buffer output to another within an existing connection to
the buffer, so **expected: no recorded twin changes**; `tests/renovisor/test_map.py` and the recording tests confirm it
(§7). The `part_load_above_seconds` parameter is already part of the simulation parameters and of the result keys when
it is not the default (`parameters_format.py:229, 253`: `partload<seconds>`).

---

## 5. Component-by-component changes

Relative to the stage D stack top (`origin/stageD-9-close-out`), which is what the two-mode buffer is built on; where
main differs from the stack top, the difference is named.

### 5.1 Buffer, `hisim/components/simple_water_storage.py`

Against stageD-9 (which has `simulate_start_of_step_node` at 1080–1150, the four end-temperature outputs at
642–653, `LONGEST_WHOLE_STEP_IN_SECONDS = 600.0` at 623 and `check_whole_steps_are_safe` at 979–1013):

1. **Add `simulate_mixed_node`** from `38f755ee` (hunk at its line 1215). Its core, as in the draft:

```python
# 38f755ee, hisim/components/simple_water_storage.py, simulate_mixed_node (excerpt)
node = hydronics.MixedNode.step(
    t0_c=start_temperature_in_celsius,
    inflows=inflows,
    ua_w_per_k=self.heat_transfer_coefficient_in_watt_per_m2_per_kelvin * self.storage_surface_in_m2,
    t_amb_c=self.ambient_temperature_in_celsius,
    dt_s=dt_in_seconds,
    heat_capacity_j_per_k=self.heat_capacity_in_joule_per_kelvin,
)
heat_primary_in_joule, heat_secondary_in_joule, heat_from_distribution_in_joule = node.heat_in_j_per_inflow
if self.position_hot_water_storage_in_system == PositionHotWaterStorageInSystemSetup.PARALLEL_TO_HEAT_SOURCE:
    stsv.set_output_value(self.water_temperature_heat_distribution_system_output_channel, node.t_mean_c)
stsv.set_output_value(self.water_temperature_heat_generator_output_channel, node.t_mean_c)
stsv.set_output_value(self.water_temperature_secondary_heat_generator_output_channel, node.t_mean_c)
stsv.set_output_value(self.water_temperature_mean_channel, start_temperature_in_celsius)
stsv.set_output_value(self.water_temperature_at_start_of_step_channel, start_temperature_in_celsius)
stsv.set_output_value(self.water_temperature_at_end_of_step_channel, node.t_end_c)
```

   The inflows come from stageD-9's `circuit_inflows(stsv)` (the draft built them inline). The two paths share
   one publishing routine for the heats, energies, loss and state (the twelve `set_output_value` calls after the
   temperatures are identical in `38f755ee` and stageD-9 apart from the temperature published on the circuit
   ports), so the second path is about 25 lines, not 60:

```python
# pseudocode: the shared tail of both paths
def publish_node_step(self, stsv, *, start_c, circuit_temperature_c, t_end_c, heats_j, loss_j, t_end_without_c):
    for channel in (self.water_temperature_heat_generator_output_channel,
                    self.water_temperature_secondary_heat_generator_output_channel,
                    self.water_temperature_heat_distribution_system_output_channel):
        stsv.set_output_value(channel, circuit_temperature_c)  # T_mean (iterated buffer), T0 (start-of-step buffer)
    stsv.set_output_value(self.water_temperature_mean_channel, start_c)
    stsv.set_output_value(self.water_temperature_at_start_of_step_channel, start_c)
    stsv.set_output_value(self.water_temperature_at_end_of_step_channel, t_end_c)
    for channel, value in zip(self.water_temperature_at_end_of_step_without_circuit_channels, t_end_without_c):
        stsv.set_output_value(channel, value)
    ... the heats, energies, loss and state exactly as stageD-9 lines 1121-1150 ...
```

2. **Add the output `WaterTemperatureAtStartOfStepInCelsius`** (from `38f755ee`), published as T0 in both paths and as
   the end temperature on the stratification path (its convention for the stage D outputs).
3. **The "without" outputs with the iterated buffer** (open question Q2): the recommended form is three further
   `MixedNode.step` calls from T0 with that inflow's flow set to 0, publishing each `t_end_c`. With the start-of-step
   buffer unchanged.
4. **Rename `LONGEST_WHOLE_STEP_IN_SECONDS` to `LONGEST_MIXED_NODE_STEP_IN_SECONDS`** (value 600.0) and reword
   `check_whole_steps_are_safe` to `check_mixed_node_step_is_safe`; the arithmetic stays (`seconds_per_timestep <= 600
   or seconds_per_timestep > part_load_above_seconds` passes). The message says: the buffer iterates as a mixed node up
   to the threshold; above 600 s its contraction per pass is θ ≥ 0.6 and whole steps overcharge it (measured +3.2 % SH
   electricity at 900 s, +17.5 % at 3600 s), so lower the threshold below the step or run steps of at most 600 s.
   `StartOfStepBufferWholeStepError` keeps its name (EF-33 in the energy-system reports) or is renamed with it; owner's
   call, Q3.
5. **Docstrings**: the class docstring says which model runs when and what each port means in each (the table of §4.1 in
   prose).

Against main: main has neither path (its `i_simulate` is the mixing law with the leak hisim-4g9.16), none of the
five end-temperature outputs, no `heat_capacity_in_joule_per_kelvin`, no `circuit_inflows`.

### 5.2 Heat distribution system, `heat_distribution_system.py`

No change against stageD-9. Its same-step booking behind a buffer (stageD-5, identical to `38f755ee`'s hunk at line
476), its room clamp on `Building.TemperatureIndoorAirAtStartOfStepInCelsius` (#1008), its ratio run
(`heat_distribution_system.py:506–515`) and its controller's floor rule (1185–1223) all work with the iterated buffer at
a ratio of 1:

```python
# hisim/components/heat_distribution_system.py:506-515 (origin/stageD-9-close-out)
ratio_run = 1.0 if water_mass_flow_rate_in_kg_per_second > 0 else 0.0
if (self.position_hot_water_storage_in_system == PositionHotWaterStorageInSystemSetup.PARALLEL
        and water_mass_flow_rate_in_kg_per_second > 0):
    ratio_run = self.part_load_command.ratio(stsv)       # 1 at and below the threshold
    water_mass_flow_rate_in_kg_per_second *= ratio_run
    thermal_power_delivered_in_watt *= ratio_run
self.part_load_command.publish_ratio_run(stsv, ratio_run)
```

Against main: main publishes the previous step's heat and temperatures (`self.state.*`, the one-step lag of
hisim-fxix.14) and has no part-load command.

### 5.3 Heat pump, `more_advanced_heat_pump_hplib.py`

No change against stageD-9 in the heat pump itself: it reads its return port (T̄ or T0), runs
`part_loaded_space_heating` with the commanded ratio (1 with the iterated buffer returns the full-load step unchanged,
`more_advanced_heat_pump_hplib.py:1449–1450`: `if part_load_ratio >= 1.0: return full_load`), books `circuit_power_w(m,
T_out, T_return)` (line 1172) and electricity from the COP.

**Controller** `MoreAdvancedHeatPumpHPLibControllerSpaceHeating`: one line, the default connection of
`WaterTemperatureInput` moves from `SimpleHotWaterStorage.WaterTemperatureToHeatGenerator` to
`SimpleHotWaterStorage.WaterTemperatureAtStartOfStepInCelsius` (the `38f755ee` hunk at line 2820). Its
`publish_raising` (stageD-8) and `publish_held` stay; with the iterated buffer they publish 1 and hold nothing.

Why the sensor must be T0 with the iterated buffer: a controller deciding on T̄ reads a value that moves with its own
decision within the step (the heat pump on → T̄ up → the controller off), the two-point cycle the coupling spec's §6
warns of and `sh_buffer_models` measured as causes 2–4 through the energy manager. Draft `38f755ee` already moved the
three controllers to T0 for this reason.

### 5.4 Boiler, `generic_boiler.py`

No change in `GenericBoiler`: it reads its return port, fires its circuits (`commanded_firing`, 1296–1310 on
stageD-9), and `part_loaded_firing` returns the full-load firing at ratio 1.

**Controller** `GenericBoilerController`: the sensor's default connection moves to
`WaterTemperatureAtStartOfStepInCelsius` (`38f755ee` hunk at line 2091). Its space-heating fraction, steady charge
and comfort remainder (`generic_boiler.py:2355–2415` on stageD-9) are gated by `part.runs_fractions()` and so are
inert with the iterated buffer:

```python
# hisim/components/generic_boiler.py:2389-2396 (origin/stageD-9-close-out)
ratio = part.publish_raising(stsv, device_runs=True, target_temperature_in_celsius=supply_temperature_set_in_celsius)
charges_a_buffer_at_its_target = temperature_delta_in_kelvin <= 0.0
if part.runs_fractions() and ratio > 0.0 and charges_a_buffer_at_its_target:   # start-of-step buffer only
    stsv.set_output_value(self.control_signal_to_generic_boiler_channel, 1.0)
    stsv.set_output_value(self.temperature_delta_channel, self.ASKED_LIFT_OF_A_CHARGE_ABOVE_THE_TARGET_IN_KELVIN)
```

### 5.5 District heating and electric heating, `generic_district_heating.py`, `generic_electric_heating.py`

No change. No recorded twin puts a space-heating buffer behind them (`sh_buffer_m5` Part B: "District heating and
electric heating have no space-heating buffer on any branch"; their loop times are at main's in every variant). Their
default connections read `StepMeanWaterTemperatureToHeatGeneratorInCelsius` of the hot-water cylinder
(`SimpleDHWStorage`, `simple_water_storage.py:1418` on main), stage C's node, which the two-mode buffer does not touch.
If a setup ever puts them behind a buffer, they read its return port like the boiler and need nothing else.

### 5.6 CHP controller, `generic_chp/controller.py`

One line: `HotWaterStorageTemperature` moves to `WaterTemperatureAtStartOfStepInCelsius` (`38f755ee` hunk at
line 424).

### 5.7 Solar collector as secondary generator, `solar_thermal_system.py`

No change. It reads `WaterTemperatureToSecondaryHeatGenerator` as its return (T̄ with the iterated buffer, T0 with the
start-of-step buffer). Above the threshold its collector part load uses the memoryless `PartLoadRule` of decision 11
against the buffer, which takes T0 as the end temperature at ratio 0 and can alternate under a draw (the hisim-fxix.17
case); that is stage D's state today and unchanged here (§6.2).

### 5.8 `hisim/part_load.py`

No code change. `StartOfStepPartLoadControl.runs_fractions()` (`part_load.py:660–662`) is the gate in every
controller; `publish_held` (729–739) publishes nothing at or below the threshold "as the controller's other
outputs do". The module docstring gains one sentence: below the threshold the buffer is an iterated mixed node and
every ratio is 1.

### 5.9 `hisim/simulator.py`

No change. #1050's budget (100 passes per 60 s of step, never below 100) covers the iterated buffer's measured maxima
(35 of 1000 at 600 s, 21 of 100 at 60 s) and the start-of-step buffer's (84 of 6000 at 3600 s). Forcing after 10 passes
stays (`simulator.py:488`).

### 5.10 Energy manager and building

#1048 (raise hold) and #1010 (window margin) as they are; both are mode-independent and both are prerequisites of
the iterated buffer finishing a year (§1.4).

### 5.11 Goldens and docs

- `golden_references/<twin>__full_year_60s.json` for the twins with a buffer move once (§6.1): one bless on the PR that
  adds the mixed-node path.
- `roadmap/hydronic_coupling_spec.md` §12 gets a pointer to this file; `roadmap/hydronic_part_load_spec.md` gets
  decision 23 (the two-mode buffer); the modelling guide's sentence on the buffer says "iterated up to 600 s,
  start-of-step above".

---

## 6. Expected performance after implementation

### 6.1 Accuracy per regime

**At 60 s (M2 is the reference).** Measured, M2 against S at 60 s, heat-pump twin: delivered +0.01 %, booked +0.04 %, SH
electricity +0.96 %, DHW electricity −0.02 %, degree-hours 11.2 against 16.3. Gas: the iterated and the start-of-step
buffer agree within 0.02 % on heat and fuel; degree-hours 8.8 against 9.5. So the 60 s goldens of the twins with a
buffer move by these amounts, once.

**At 300 and 600 s (iterated buffer).** Measured, against M2's own 60 s: heat pump delivered −0.21 / −0.53 %, booked
−0.19 / −0.35 %, SH electricity −0.83 / −1.32 %, degree-hours 16.0 / 21.3. Heat pump at 70 %: −0.52 / −0.56 %, −0.69 /
−0.31 %, −1.38 / +1.47 %, 202.8 / 154.8 (60 s: 184.4). Gas (Mw): −0.04 / +0.01 %, −0.04 / +0.02 %, −0.08 / −0.37 %, 15.8
/ 11.2 (60 s: 8.8).

**At 900 s (start-of-step buffer; RenoVisor).** Measured against the start-of-step buffer's own 60 s: heat pump
delivered −0.70 %, booked −1.07 %, SH electricity −3.54 %, degree-hours 23.0; gas −0.13 %, +0.02 %, fuel −9.31 %, 36.8;
pellets −0.09 %, −1.31 %, −1.32 %, 0.4; heat pump + solar thermal −0.66 %, −0.70 %, −2.79 %, 15.5 (§12 of the coupling
spec). **Expected** against the two-mode buffer's 60 s reference (M2): shift each by M2's 60 s offset to the
start-of-step buffer's 60 s, that is delivered −0.71 % and SH electricity ≈ −4.5 % for the heat pump, −0.15 % and −9.3 %
for gas. The #1035 assertion (delivered within 1 %) holds with margin; it is re-measured, not assumed (§7.1).

**At 1800 and 3600 s (start-of-step buffer).** Measured: heat pump delivered −1.23 / −1.99 %, SH electricity −4.63 /
−5.91 %, degree-hours 73.1 / 109.3; gas −0.18 / −1.01 %, fuel −18.76 / −19.36 %, 38.8 / 192.8.

### 6.2 What remains wrong in both regimes

- **The heat pump's SH electricity about 3.5 % low at 900 s** is the control target, not the buffer: at 60 s the
  controller cycles the buffer between supply −5 K and +5 K, and the fraction stops each charge at the supply
  temperature, so the heat-weighted charging temperature is about 1.4 K lower at 900 s and the COP higher
  (`sh_buffer_m4`, "Why heat-pump SH electricity is about 3 % low"). M4, M5 and S share it (−3.4 / −3.7 / −3.5 %). Bead
  hisim-fxix.27 (P1). Untested lever: a target nearer the 60 s cycle's mean charging temperature.
- **Gas fuel −9.3 % at 900 s and −19.4 % at 3600 s** with the heat within 1 %: the boiler's efficiency law rises with
  burner power, and a fraction of the step at full power burns less fuel than modulating low all step. Bead hisim-4g9.66
  (P1). M2 at 900 s shows −6.7 % for the same reason (it fires in 58 % of steps at 2202 W mean against 77 % at 1662 W at
  60 s).
- **Comfort at 3600 s** (hisim-6ehm): 109–193 degree-hours against 11–16 at 60 s.
- **hisim-fxix.1** (open: "A stratified water storage of N mixed layers replaces the dt/3600 mixing factor of
  `heat_exchanger_is_present=False`") stays: the two-mode buffer leaves the stratification path as it is, and no
  recorded file uses it. **hisim-pnw7** (open, P3: "Hydronic chain without a buffer: the pipe water as a start-of-step
  node, D25's flow ownership and district heating's bill") stays: chains without a buffer (district heating,
  `gas_boiler_household`) are outside the two-mode buffer, which concerns the buffer only; their pipe-water node is
  decided there. **hisim-fxix.17** (open: "The memoryless part-load rule alternates where the draw takes more than the
  rise the step still needs") remains for the collector's circuit on the buffer with the start-of-step buffer (§5.7).
- **Forced steps.** M2 forces 49–70 % of the steps at 600 s (information only, owner rule); at 60 s it forces fewer than
  main (heat pump 3921 against 5346 under M5's 60 s run, which equals M2 there).
- **The step-mean bias of the iterated buffer** (§3.1), 0.5 K at 600 s in the example system; accepted with the
  threshold.

### 6.3 Runtime per regime

| Regime | Measured today | Expected after implementation |
|---|---|---|
| 60 s, gas / heat pump, loop | main 146 / 272 s; S 176 / 300 s; M2 227 / 382 s (all contended) | = M2: 227 / 382 s contended; roughly +30 % on S and +55 / +40 % on main. #1051 (finite checks once after the run) would take a part of this back: finite checks were ~11 % of M5's cumulative profile; not measured on M2 |
| 300 / 600 s, heat pump, loop | M2 105 / 66 s; S 65 / 36 s (contended) | = M2 |
| 900 s, gas / heat pump / pellets, loop, uncontended grouped twins | main 9.0 / 20.8 / 8.6 s; S 14.4 / 28.7 / 13.9 s | = S; total wall 1.29–1.39× main (about 5–6 s of import, build and post-processing on top) |
| 1800 / 3600 s, heat pump, loop | S 16.7 / 10.4 s | = S |
| per pass | ≈ 84 µs on main and the iterated buffer alike, 77–82 % in components' `i_simulate`, `is_close_enough` ~3 % | unchanged; the cost is the pass count |

CI: the golden gate's full-year 60 s runs of the four twins with a buffer are the only jobs that get slower, by the
M2-against-S difference (expected +50 to +80 s loop each, contended figures); every 900 s job runs the start-of-step
buffer unchanged.

---

## 7. Verification plan

### 7.1 Tests

| Test | What it checks | Where | Marker, cost |
|---|---|---|---|
| closure per step, both paths | `Σ Q_i − loss = C (T_end − T0)` to 1e-6 of the step's largest term, on every 900 s step of a January day in the 29 files with a buffer (exists, stageD-5) **and** on every 60 s step of the same day in the four twins (new, iterated buffer) | `tests/test_space_heating_buffer_twins.py` | `extendedbase2`; the 60 s day adds 1440 steps × ≈ 6 passes × 84 µs ≈ 0.7 s loop per twin plus the build |
| both ends agree | the generator's booked power equals the buffer's `ThermalPowerFromHeatGenerator`, the distribution's delivered power equals `−ThermalPowerConsumptionHeatDistribution`, on the converged step: with the start-of-step buffer every pass, with the iterated buffer at the fixed point (tolerance 1e-4 · m c, the simulator's own) | same file | same |
| pass-by-pass unit test of the iterated buffer | the example of §3.1: five passes at 60 s to 1e-4 K, contraction 0.111 ± 0.01; 20 passes at 600 s, contraction 0.621 ± 0.01; the fixed-point heats 752.40 / −501.60 kJ; T_end 35.29782 °C | new `tests/test_space_heating_buffer_mixed_node.py` | `base`, < 1 s |
| the threshold switch | a buffer built at 600 s publishes T̄ on the circuit ports and T0 on the sensor; at 601 s it publishes T0 on all; `runs_part_load()` decides; with `part_load_above_seconds = 300`, a 600 s buffer runs the start-of-step buffer | same new file | `base` |
| the refusal | 900 s with threshold 900 or 1800 refused; 900 and 3600 s with the default accepted; 60 and 600 s with any threshold from 0 to 3600 accepted; never a buffer without its exchanger (exists, #1035; messages re-asserted) | `tests/test_space_heating_buffer_whole_step_refusal.py` | `base`, ≈ 1 s |
| part-load controls inert with the iterated buffer | at 600 s every `PartLoadRatio*` output is exactly 1 while the device runs and 0 while it is off, in forced passes too | `tests/test_start_of_step_part_load.py`, `tests/test_heat_pump_space_heating_part_load.py` (extend) | `base` |
| convergence without abort, iterated buffer | the four twins finish the full year 2021 at 60 s (the golden gate's own runs are this test), and at 300 and 600 s one January week and one July week each with the largest pass count recorded and asserted below 50 (measured maxima 26–35) | new `tests/test_space_heating_time_resolution.py` cases, or the nightly tier | `system_setups`; a week at 300 s ≈ 2000 steps |
| convergence without abort, start-of-step buffer | the gas and heat-pump twins at 900, 1800 and 3600 s finish a January week; maxima recorded (exists in spirit in #1035's 900 s runs; 3600 s added) | same | `system_setups` |
| 900 s against 60 s | #1035's assertion unchanged (delivered within 1 % of the committed `full_year_60s` golden), **re-measured against the M2 goldens**; the recorded (not asserted) booked heat, fuel and electricity updated to the new reference | `tests/test_space_heating_time_resolution.py` | `system_setups`, 18–41 s per twin (its docstring) |
| recorded files unchanged | the recording tests and `tests/renovisor/test_map.py` pass without re-recording (expected, §4.2); if a file does change, it is re-recorded in the same PR | existing | `base` |

### 7.2 Goldens

One bless of `golden_references/<twin>__full_year_60s.json` for `household_gas_building_sizer`,
`household_pellets_building_sizer`, `household_heatpump_building_sizer`,
`household_heatpump_solar_thermal_building_sizer` and the other buffer twins (oil, wood chips, hydrogen, gas +
solar thermal, heat pump + car), on the PR that adds the mixed-node path, via the golden-update workflow. Expected
movement: heat within 0.05 %, heat-pump SH electricity +0.96 %, degree-hours down (16.3 → 11.2 in the heat-pump
twin). The 900 s goldens, if any, do not move.

### 7.3 Runtime budget for CI

- `base` additions: under 5 s in total.
- `extendedbase2`: the 60 s January day in four twins, about 30 s including builds.
- `system_setups`: the week runs at 300 / 600 / 900 / 1800 / 3600 s, about 2–3 minutes in total; #1035's four 900 s
  years unchanged (18–41 s each).
- Golden gate: +50 to +80 s per buffer twin-year at 60 s (expected from the contended M2-against-S difference).

---

## 8. Pull-request plan

Order, each small, stacked (AGENTS.md: a new PR targets the stack top):

0. **Prerequisites merge first.** #1048 (energy manager's raise hold; the iterated buffer aborts without it) and then
   the stage D stack in order, #1009 → #1010 → #1011 → #1013 → #1014 → #1012 → #1035, rebasing only the bottom onto main
   after each merge. #1051 is independent and may merge any time.
1. **T1 — the sensor port.** `SimpleHotWaterStorage` publishes `WaterTemperatureAtStartOfStepInCelsius` (T0 on the
   start-of-step path, the end temperature on the stratification path); the three controllers' default connection for
   their sensor moves to it. No result change with the start-of-step buffer (the value is T0 either way); recorded files
   expected unchanged. About 40 lines plus a `base` test. Base: `stageD-9-close-out` (or, if the owner prefers, folded
   into #1013, the ports PR — open question Q4).
2. **T2 — the mixed-node path.** `simulate_mixed_node` (from `38f755ee`), the shared publishing routine, the dispatch on
   `runs_part_load()`, the "without" outputs with the iterated buffer (per Q2), the refusal renamed and reworded, the
   `base` tests of §7.1 (pass by pass, threshold switch, inert controls) and the 60 s closure day. **Results change at ≤
   600 s**; the golden bless is its own commit on this PR. Base: T1.
3. **T3 — the resolution tests and docs.** The week runs at 300–3600 s with recorded maxima, #1035's assertion
   re-measured against the M2 goldens with its recorded numbers updated, the pointer in the coupling spec §12, decision
   23 in the part-load spec, the guide sentence, `part_load.py`'s module docstring. Base: T2.

What changes in the open stage D PRs: nothing in code. #1035's docstrings speak of "whole steps" where the two-mode
buffer runs the mixed node; T2 rewrites them, so #1035 need not be reopened. If Q4 is answered "in #1013", #1013 gains
T1's 40 lines and is re-reviewed once.

---

## 9. Open questions for the owner

Each with options and a recommendation; none needs the code to decide.

**Q1. Which port do the generator controllers read?** The controllers need the buffer temperature at the start of the
step, a value no pass moves, so that their on/off decision cannot cycle with the iterated buffer. (a) *A new output
`WaterTemperatureAtStartOfStepInCelsius`*, T0 in both modes, named for what it is; the three controllers' default
connection moves to it (draft `38f755ee` did this). (b) *Reuse `WaterMeanTemperatureInStorage`*, which both drafts
already fill with T0; no new output, but the name says "mean" and a reader cannot tell from it that the value is the
start-of-step state. (c) *Leave the controllers on `WaterTemperatureToHeatGenerator`* as the start-of-step buffer has
them; with the iterated buffer they would then decide on T̄, which moves within the step. **Recommendation: (a).** One
output, one meaning, and the reason is visible at the controller.

**Q2. What do the three "end temperature without a circuit" outputs carry with the iterated buffer?** Above the
threshold the part-load rules read them; at or below it nobody acts on them, but the ports exist in both modes. (a)
*Exact for the mixed node*: three more `MixedNode.step` calls per pass, each from T0 without one inflow; the port means
the same thing in both modes ("the end temperature had this circuit brought nothing"). Expected cost: the node step is
about 5 % of a pass (`MixedNode.step` was 21 % of M5's cumulative time with four calls per pass), so three more calls
add roughly 10–15 % per pass at ≤ 600 s — expected, to be measured on T2. (b) *Publish T_end* on all three with the
iterated buffer, the stratification path's convention; free, but the port's meaning depends on the mode.
**Recommendation: (a), with (b) as the fallback if the measured cost on T2 exceeds 10 % of the 60 s loop time.** The
interface is the point of the two-mode buffer; a port whose meaning changes with the mode is a hidden mode switch.

**Q3. Keep #1035's refusal of a step above 600 s whose threshold is at or above it?** With the two-mode buffer such a
run would iterate the mixed node above its range: at 900 s it converges (gas max 51 passes) but overcharges (+3.2 % SH
electricity, heat pump), at 3600 s it needs up to 121 passes of #1050's 6000 and is +17.5 % off. (a) *Keep the refusal*,
renamed (`check_mixed_node_step_is_safe`, `LONGEST_MIXED_NODE_STEP_IN_SECONDS`), message reworded; measurement runs of
the iterated buffer above 600 s happen on scratch branches as they did. (b) *Allow it with a warning*, for comparisons
from a committed tree. **Recommendation: (a).** The wrong result is the reason, not the cost; the owner decided the
refusal on 2026-10-16 for the same combination.

**Q4. Where does the sensor port land?** (a) *In #1013* (the ports PR, open): it is a port of the same family and
changes no result of the start-of-step buffer; #1013 is re-reviewed once. (b) *As T1 on top of #1035*: the reviewed
stack stays as it is; one more small PR. **Recommendation: (b)**, unless #1013 is still unreviewed when the two-mode
buffer starts.

**Q5. The 60 s reference moves to M2.** The heat-pump twin's 60 s golden then uses +0.96 % more SH electricity and has
11.2 instead of 16.3 degree-hours; every coarse-step deviation is re-stated against it (expected: 900 s delivered
−0.71 %, SH electricity ≈ −4.5 %). (a) *Accept and bless once* on T2. (b) *Keep the start-of-step buffer's 60 s as
the reference* and run the start-of-step buffer at 60 s too — that is "start-of-step buffer everywhere", not the
two-mode buffer. **Recommendation: (a).** The iterated buffer at 60 s is the more faithful model (same-step coupling, no
whole-step overshoot, closure), which is why it is the reference.

**Q6. Threshold default.** (a) *600 s* (the owner's decision): at 600 s the iterated buffer's maximum is 35 passes of
1000, mean 16.0 / 10.5 / 11.7; the iterated buffer beats the start-of-step buffer on heat and comfort there (heat pump
−0.53 % against −0.62 % delivered, 21.3 against 28.0 degree-hours), and the step-mean bias is 0.5 K in the example. (b)
*300 s* (`a ≈ 1`, the agent's earlier suggestion): cheaper at 600 s (start-of-step buffer: 36 s against 66 s loop, heat
pump) but the start-of-step buffer at 600 s is −2.12 % SH electricity against M2's −1.32 %. No recorded workflow runs
300 or 600 s. **Recommendation: (a)**, as decided; the parameter exists for anyone who needs (b).

**Q7. Forced steps with the iterated buffer at 300–600 s** are 49–70 % of all steps (start-of-step buffer: 1–10 %),
because 10 passes are fewer than θ needs and the controllers are then held while the physics finishes. (a) *Record, do
not assert* (the owner's rule of 2026-10-17: forced steps are information only). (b) *Assert a ceiling* at 600 s.
**Recommendation: (a).**

**Q8. Merge order of #1048.** The iterated buffer needs it; the stage D stack does not. (a) *#1048 first*, then rebase
#1009 onto main and merge the stack, then T1–T3. (b) *Stack first*, #1048 after, T1–T3 after both. **Recommendation:
(a)**, because #1048 is a small PR already measured on main and every later measurement of the iterated buffer wants it
in the tree.

**Q9. The collector's circuit on the buffer with the start-of-step buffer.** Above the threshold the solar collector's
part load on the buffer uses the memoryless rule of decision 11, whose alternation under a draw is hisim-fxix.17's case;
with the iterated buffer it runs whole steps. The two-mode buffer does not change this. (a) *Leave it*, recorded under
hisim-fxix.17, and measure the heat pump + solar thermal twin at 900 s on T3 (stage D measured −0.66 % delivered there).
(b) *Give the collector the exact rule* (`raising_ratio` against
`WaterTemperatureAtEndOfStepWithoutSecondaryHeatGeneratorInCelsius`, which the buffer already publishes) in a PR of its
own. **Recommendation: (a) now, (b) as the next small PR after T3** if the 900 s measurement of that twin shows
alternation.
