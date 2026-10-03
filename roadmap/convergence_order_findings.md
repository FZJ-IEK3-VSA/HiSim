# HiSim per-step convergence and component order — findings (2026-10-03, verified against origin/main 0921f9bd)

Measurement: reordering the components of household_heatpump_building_sizer.grouped (content identical) changes one
week's KPIs by 0.1-7 % on energies, 10-20 % on small quantities; 60/113 KPIs beyond 1e-9. Both orders deterministic.
First divergence: one heat-pump SH on/off decision resolving the other way (step 44 at 900 s, step 2034 at 60 s).

## Verified defects
1. Every step starts from an ALL-ZERO vector: `stsv` is created once (simulator.py:628) and never reassigned
   (:631-638; the reset line is commented out and its comment says the opposite); process_one_timestep clones the
   incoming vector (:388-389), so pass 1 reads 0 from every output later in file order. Order-dependent start; the
   "storage sends zero" workaround (more_advanced_heat_pump_hplib.py:3357-3362) and the minimum of 3 passes follow.
2. DHW controller i_restore_state (:3333-3338) overwrites its saved state -> restores the previous PASS, not the step start.
3. HeatDistributionController i_restore_state assigns the mode to itself (heat_distribution_system.py:1178-1180): no restore.
4. Heat pump sets self.minimum_thermal_output_power = 0.0 on any pass with on_off==2 (:1649), never restored.
5. EMS i_save_state aliases instead of copying (controller_l2_energy_management_system.py:651-658); harmless today.
6. force_convergence (after 10 tries, simulator.py:415-416): controllers skip their computation but restore_state has
   already reset their internal state -> output of pass 12 with start-of-step memory; the plant (heat pump, storages,
   EMS, battery) keeps computing with those frozen outputs -> controller and machine carry different states on.
   900 s, one week: 32 (original) / 30 (reordered) forced steps = 4.5-4.8 %, ~8 h per week; no step converges with
   8-13 passes, every >7-pass step is a limit cycle ended by the freeze.

## Why no unique fixed point at step 44
Cycle C1: EMS surplus>0 -> SpaceHeatingWaterStorageTemperatureModifier (+10 K switch points, :2967-2985) -> SH on ->
heat pump draws 2655 W -> surplus<0 -> modifier 0 -> SH off -> surplus reappears (battery lags one pass). "Off" is the
only fixed point; the reordered run fell into a period-2 cycle and the freeze kept the "on" phase, inconsistent with its
own recorded inputs (HDC flow 32.369 °C stale from a pass with building modifier 2). Strict sign tests at :765/:782 flip on
float noise once the battery absorbs the surplus. Storage temperatures are already start-of-step (T0) on the
heat-exchanger path (simple_water_storage.py:1009-1012), i.e. hydronic D4 is already true there; the cycles are the EMS
modifiers (C1-C3), EMS<->battery (C4), building<->HDS (C5).

## Options
(a) Jacobi: order-free but 2-3x passes, discrete loops -> period-2, results arbitrary at the freeze. Not recommended.
(b) Discrete decisions on start-of-step values (controllers snapshot inputs in i_doublecheck, decide next step; EMS
    modifier on exogenous + start-of-step surplus (PV - uncontrolled load - battery headroom from SOC) with ±50 W
    hysteresis, excluding the heat pump's own draw): kills C1-C3; one-step lag only where inputs weren't T0 already.
(c) Latching (first pass decides): stopgap only, still order-dependent with the zero start.
(d) Dependency ordering by the simulator/executor (SCCs topological, fixed rank inside): bit-identical across file
    permutations; after (b) the SCC shrinks to {EMS,Battery} and {Building,HDS}.
(e) Damping: changes the path not the fixed point; anti-chatter hysteresis on the EMS sign test is cheap and good.
(f) Joint solver: hisim-4g9.22 escalation only.

## Recommended sequence
1. Bug fixes 2-5; warm start (stsv = resulting_stsv, remove the :3357 hack); under force skip restore_state for frozen
   components so memory matches output. ~1 day. Needs hydronic D7 ("no simulator change") revised.
2. EMS signal + controllers deciding on the i_doublecheck snapshot (b). Changes results in all EMS twins; announce. 2-4 days.
3. Canonical ordering (d). 1-2 days. Turns "equal within 1e-4" into "identical".
4. Hydronic stages C/D as specified.
Tests: permutation test (file order, reversed, seeded shuffle -> identical KPIs); forced-step counter target 0;
pass-purity unit test per component (save, simulate, restore, simulate -> identical outputs and __dict__);
consistency assertion at forced steps.
