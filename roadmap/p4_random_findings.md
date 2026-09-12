# P4 — random findings and defects

**Status:** living document · **Opened:** 2026-09-01 · **Last entry:** 2026-09-11 (5 findings)
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

### F-3 — the CHP controller's summer branch switches off against the heating maximum, not the DHW maximum **[verified]**

Found by the review of #683 (D-4), outside that PR's diff: the PR changes the factories' values, not
`calculate_state`. `hisim/components/controller_l1_chp.py:485-497`, the branch whose own comment says "only
consider water heating in summer":

```python
if t_dhw < self.config.t_min_dhw_in_celsius:
    self.state.activate(timestep)
    return
if t_dhw > self.config.t_max_heating_in_celsius:
    self.state.deactivate(timestep)   # ← t_max_dhw_in_celsius is meant
    return
```

The activation reads the DHW band's lower bound, as it should; the deactivation compares the same DHW
temperature against `t_max_heating_in_celsius`, the upper bound of the **heating** band. The winter branch two
lines below reads each band for its own temperature (`t_building > t_max_heating`, `t_dhw > t_max_dhw`), which
is what makes the summer line read as a slip rather than a choice.

What it costs depends on the factory. With either buffer factory the heating maximum is 40 °C against a DHW
maximum of 60 °C; without a buffer it is **20.5 °C**, below every `t_min_dhw_in_celsius` in the file. So in
summer the CHP is switched off as soon as the DHW store passes 40 °C — or, for the two bufferless configs, at
essentially every timestep the minimum-runtime guard does not already hold it on. The 60 °C the same config
asks for is never reached in summer.

*Cost of not finding it: the four factories' DHW maxima are the values D-4 spent a decision on, and in summer
three of the four are unreachable. A conversion that froze them into the wire format would have frozen numbers
the code cannot honour.*

**Where it stands.** Not fixed, and not D-4's to fix. Own PR with a test that drives `calculate_state` at a
summer timestep with `t_dhw` between the heating maximum and the DHW maximum and shows the controller
deactivating where it should stay on. No setup builds `L1CHPController`, so it is a physics change (R5) with
no recorded result behind it; it can land before or after the conversion.

### F-4 — the `hisim` console script ignores the repository's `.env` **[verified, fixed]**

`hisim/hisim_main.py` imports `load_dotenv` (`:12`) and calls it at import time (`:33`), so
`python hisim/hisim_main.py …` picks up `UTSP_URL` and `UTSP_API_KEY` from the repository's `.env` — the file
`CLAUDE.md` documents as the place to put them. `hisim/cli.py`, the entry point behind the installed `hisim`
console script (`cli.main`, `:285`), neither imports `dotenv` nor calls it anywhere.

Every command reached through the console script therefore runs with whatever the ambient environment happens
to hold, `hisim energy-system run` included. The two documented ways to run the same setup — the script and
the module — do not see the same configuration, and the failure is a UTSP request without credentials rather
than a message naming the missing variable.

*Cost of not finding it: the documented environment file works for one of the two documented entry points, and
the one it fails for is the one the install instructions produce.*

**Where it stands.** Fixed. `cli.main` now calls `load_dotenv()` before it parses anything, matching
`hisim_main.py`; the call sits in `main` rather than at import, because importing a library should not read files.
`hisim_main.py`'s import-time call stays where it is: `scripts/hpc_harness/run_one.py`, `scripts/p3_parity_runs.py`
and the economic-example setups import `initialize_from_json` / `initialize_from_python` without ever going through
its `main`, so moving it would take the file away from them. Both calls resolve to the same file — python-dotenv
walks up from the directory of the module that calls it, which is `hisim/` for both. A test in `tests/test_cli.py`
plants a sentinel `.env` and asserts the console script has read it.

### F-5 — a component copied from `example_template.py` raises before its first timestep **[verified, fixed]**

`Component.i_prepare_simulation` (`hisim/component.py:350`) raises
`NotImplementedError("Simulation preparation is missing for …")`, and `Simulator.run_all_timesteps` calls it
for every registered component through `prepare_calculation` (`hisim/simulator.py:441,212` →
`component_wrapper.py:167`) before the loop starts. `hisim/components/example_template.py` implements
`i_save_state`, `i_restore_state`, `i_doublecheck` and `i_simulate` (`:142-190`) and **not**
`i_prepare_simulation`, and `ComponentName` inherits from `Component`, not from `StatelessComponent` (whose
no-op override is at `component.py:734`). A component written from the template — the file `CLAUDE.md` tells
every new author to copy — therefore fails inside a Simulator, at the first thing the Simulator does.

`hisim/components/example_component.py` has the same gap, verified the same way; its 11 tests call `i_simulate`
directly and never build a Simulator, which is why neither file's omission is caught.

*Cost of not finding it: the template's whole job is that copying it produces something that runs, and the one
lifecycle method it omits is the one that raises rather than doing nothing.*

**Where it stands.** Fixed. Both `example_template.ComponentName` and `example_component.ExampleComponent`
implement `i_prepare_simulation` as a documented no-op: the template's docstring says what a real component
does in that hook (open a data file, precompute a profile, read a fact out of the simulation repository) and
that a component with nothing to prepare still has to define it, because the base class refuses to run
without it; the example component's says the same, and that its `build()` already ran at construction — no
behaviour moved. The template's numbered steps now list all five lifecycle methods with a line each, so the
file names the hook before the reader reaches the class.

Each test file gained the test that was missing: it builds the component, adds it to a `Simulator` and runs
`run_all_timesteps()` over one day at hourly resolution with no post-processing option set. Both fail with
`NotImplementedError` on the code as it stood — which is the whole finding, since every other test in those
files calls `i_simulate` directly. (The template declares its input mandatory, so its test wires the
`ExampleComponent`'s `ElectricityOutput` to it; the Simulator refuses an unconnected mandatory input.)


---

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
