# P4 — random findings and defects

**Status:** living document · **Opened:** 2026-09-01 · **Last entry:** 2026-09-01 (2 findings)
**Context:** things that surfaced while working through
`roadmap/declarative_energy_systems/p4_component_sweep_requirements.md` — the component sweep, decisions
D-1 … D-32 — and were **not** what the work set out to do. Kept separately so the requirements stay about
the design, and so nothing found on the way is lost when a branch merges.

Numbering is local to this document, as it is in `roadmap/p3_random_findings.md`. Entries marked
**[verified]** were re-checked independently; **[reported]** rest on a single observation.

---

## 1. Defects in already-merged code

### F-1 — the energy manager declares target outputs for devices the setup does not have, and names them by list position **[verified]**

Found by D-1. Retiring `advanced_heat_pump_hplib` — a class **no system setup instantiates** — broke
`golden-json-check` and `scenario-json-freshness` across twelve committed scenario JSONs. Two separate
defects compose to produce that blast radius.

**Outputs are created eagerly; inputs are not.** `add_dynamic_default_connections`
(`hisim/dynamic_component.py:438`) only records a connection list in a dict keyed by source class name — it
creates nothing, and the matching inputs are materialised later, in `connect_everything_automatically`, only
for source components that are actually present. That half is correct. But six `add_component_output` calls
live *inside* the `get_default_connections_from_*` methods themselves, and
`controller_l2_energy_management_system.py:357-362` calls all six methods unconditionally in `__init__`.
Asking a method to *describe* its connections therefore has the side effect of *creating* the target output,
whether or not the device exists.

Measured on `household_district_heating_building_sizer`, whose energy manager carries fourteen outputs:

| # | Output | Device in the setup? |
|---|---|---|
| 1–7 | the manager's own static outputs | — |
| 8 | `ElectricityToOrFromGridOfUtspLpgConnector_` | yes |
| 9 | `…OfSHMoreAdvancedHeatPumpHPLib_` | **no** |
| 10 | `…OfDHWMoreAdvancedHeatPumpHPLib_` | **no** |
| 11 | `…OfSHElectricHeating_` | **no** |
| 12 | `…OfDHWElectricHeating_` | **no** |
| 13 | `…OfSolarThermalSystem_` | **no** |
| 14 | `LoadingPowerInputForBattery_` | yes — created by the setup itself, not by a default connection |

A district-heated house carries five target outputs for a heat pump, an electric heater and a solar
collector it does not have. They are wired to nothing. They do not reach the KPIs — no such name appears in
`golden_references/household_district_heating_building_sizer__one_week_60s.json` — so the cost is
structural rather than numerical.

The behaviour is not even uniform: `get_default_connections_from_pv_system` and `..._from_advanced_battery`
create no output at all, which is why the battery's output has to be created by hand in every setup that
wants one (`household_district_heating_building_sizer.py:440`).

**The name encodes a position in a list.** `hisim/dynamic_component.py:147-149`:

```python
num_inputs = len(self.outputs)
# label = f"{source_weight}"
label = f"Output{num_inputs + 1}"
```

A port's identity is therefore a function of how many unrelated ports were declared before it — and, because
of the first defect, that count is a property of the energy-manager *class* rather than of the system being
modelled. Deleting one dead method renamed every port after it: `LoadingPowerInputForBattery_Output15`
became `_Output14`, and eleven other outputs moved with it. The committed scenario JSONs name ports
literally, so twelve went stale at once. The commented-out `# label = f"{source_weight}"` on the line above
is someone having considered naming ports by their meaning and not doing it.

*Cost of not finding it: retiring dead code has a blast radius unrelated to the dead code, and the person
doing it has no way to predict it. `main` already carries `4dcd4079 "Stop the scenario-JSON converter
hard-coding the EMS output count, regenerate"`, so this has bitten before from the other direction.*

**Where it stands.** Not fixed. D-1 (#604) regenerates the twelve JSONs and stops there, which is the right
scope for "retire a dead module". Two independent fixes are wanted, and they interact:

- **Name ports by meaning.** `LoadingPowerInputForBattery_Output14` already carries its meaning in the
  prefix; the `_OutputN` suffix adds nothing but fragility. Naming by `source_weight` and tag — what the
  manager actually dispatches on — makes names stable under unrelated edits and ends this class of
  breakage. One fleet-wide regeneration.
- **Create the output where the input is created.** Moving `add_component_output` out of the description
  methods and into `connect_everything_automatically` means a target output exists only when its device
  does, and deletes the five phantom ports.

Both rename things, so they want to land together or in that order, never against each other. P3's
recordings inherit these names too, so the sequencing matters to the declarative stack as well.

---

### F-3 — `PVSystemConfig.location` is a string nobody reads, and once F7 lands it can contradict the field beside it **[verified]**

`PVSystemConfig.location: str = "Aachen"` is passed by 26 setup call sites (`location=weather_location`) and
serialised into every scenario JSON, and the PV component never reads it: inside `generic_pv_system.py` it
appears as the dataclass field, a parameter of the two factory methods and their two `location=location`
forwardings, one docstring line and the default configuration's `location="Aachen"`, and in no computation.
Its purpose was to make the PV cache key differ between sites, which worked only as long as every setup
copied the same variable into the weather and the PV -- a habit, not an invariant, and the origin of
`pylpg_flakiness.md` F7.

F7 (#625, open at the time of writing) gives the PV a `weather_identity` field sized from the weather itself,
so the key no longer needs `location` for anything. The key still contains it, because the key is the whole
configuration's JSON. What remains is a field that can then visibly disagree with its neighbour: a setup with
Seville weather and a PV built with the default `location="Aachen"` records `location: Aachen` and
`weather_identity: Sevilla/...` side by side, and the first is wrong without consequence.

Retiring it is a P4-sweep item and not a small fix: the field is in the config class, two factory
signatures, 26 setup call sites and 19 committed scenario JSONs, and the conversion of `PVSystemConfig`
(B-batch, `p4_component_sweep_requirements.md` R3 row `PVSystemConfig`) is the point at which those are all
rewritten anyway. Until then it costs nothing but a misleading line in every PV block.

*Logged 2026-09-03 while landing the zenith clamp (#628), with F7 (#625) still open. Do not fix piecemeal;
fold into the PV conversion.*

## 2. Recurrences of findings logged elsewhere

### F-2 — P3's F-2 recurred, in exactly the shape it was logged in **[verified]**

`roadmap/p3_random_findings.md` F-2 records that `scripts/regenerate_scenario_jsons.py` regenerates against
the *installed* package rather than the worktree, and "fails by producing plausible output, on a script whose
whole job is to keep generated files honest". It is still open.

It happened again on 2026-09-01, while fixing F-1 above. The first regeneration on the D-1 branch reported
`DONE: 22 OK, 0 FAILED` and produced **zero drift** — a false all-clear on twelve files that were genuinely
stale. The subprocesses had imported `hisim` from the editable install in the primary checkout. Re-running
with `PYTHONPATH=<worktree>` produced the real twelve-file diff.

Worth recording that the trap is worktree-specific and silent, and that neighbouring tools do not share it:
`python -m pytest` from a worktree is fine because the working directory goes on `sys.path`, and
`scripts/golden_check.py` is fine because it inserts its own repo root derived from `__file__` — which is
why the golden check reproduced the failure correctly while the regenerator quietly did not.

*A second occurrence on a different branch, three days apart, on the same script. The fix is one line of
`env` in the subprocess call.*
