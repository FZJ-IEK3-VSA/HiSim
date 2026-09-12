# P4 — random findings and defects

**Status:** living document · **Opened:** 2026-09-01 · **Last entry:** 2026-09-12 (6 findings)
**Context:** things that surfaced while working through
`roadmap/declarative_energy_systems/p4_component_sweep_requirements.md` — the component sweep, decisions
D-1 … D-32 — and were **not** what the work set out to do. Kept separately so the requirements stay about
the design, and so nothing found on the way is lost when a branch merges.

Numbering is local to this document, as it is in `roadmap/p3_random_findings.md`. Entries marked
**[verified]** were re-checked independently; **[reported]** rest on a single observation.

---

## 1. Defects in already-merged code

### F-1 — the energy manager declares target outputs for devices the setup does not have, and names them by list position **[verified, fixed]**

**Fixed 2026-09-12**: both halves, in one change. The six `get_default_connections_from_*` methods are
pure again — they describe, and create nothing. What each of them used to create on the side, the target
output of the participant it describes, now travels with the connection that describes it, as a
`DynamicComponentTargetOutput` on `DynamicComponentConnection` carrying the three things the port does not
share with its own feed: the name prefix, the dispatch tags and the description. Load type, unit, weight and
source class come off the connection, because a target that disagreed with its feed about any of them could
never be paired with it. The port is materialised in `connect_with_dynamic_connections_list` — beside the
input, in the same loop, for a source component the run actually has. That is later than components are
registered, so the ports grown while wiring are registered then:
`ComponentWrapper.register_outputs_grown_while_wiring`, called by `Simulator.prepare_calculation` right
after the automatic connection, before `run_all_timesteps` sizes its values vector. A port that already
existed at registration is not reconsidered, so one deliberately skipped there — its class is not in this
run — stays skipped.

The naming rule is now the prefix plus the source weight: `LoadingPowerInputForBattery_6`,
`ElectricityToOrFromGridOfSHMoreAdvancedHeatPumpHPLib_2`, `ElectricityTarget1` … `ElectricityTarget4` for
the four targets `dynamic_components` names alike and tells apart by weight. The prefix already says what
the port is and the weight is what the manager dispatches on, so the name is a function of the port and of
nothing else; a second port of the same name is refused with a `ValueError` naming both halves, because two
ports of one name would be one port to every tag-and-weight lookup. The commented-out
`# label = f"{source_weight}"` is gone, having finally been done.

`household_district_heating_building_sizer`'s energy manager declares **9 ports where it declared 14**: the
five phantoms are gone and the nine that remain are seven static ones, the occupancy target and the
battery target the setup adds by hand. `household_heatpump_building_sizer` goes from 14 to 11 and keeps both
heat pump targets, which is the other half of the same rule. The registered result columns do not move with
them: `ComponentWrapper.register_component_outputs` was already dropping any output whose source class is
absent from the run, which is why the phantoms never reached a result file or a KPI — and why this change is
measurably neutral. All twelve recorded twins are byte-identical under
`scripts/record_all_setups.py --check`; the eleven grouped twins among them re-record to no change; the
one-week goldens of `household_heatpump_building_sizer` (a house whose manager grows two targets while
wiring) and of `dynamic_components` (four hand-made targets) both report GOLDEN CHECK OK. The declarative
path never called the legacy wiring, so it simply loses the constructor's phantoms: a declarative
`household_heatpump_building_sizer` run now publishes four dispatch columns, `DispatchFor…`/`DispatchTo…`,
and no `_OutputN` name anywhere.

Two places that spelled the counter were respelled with it. `scripts/p3_parity_renamings.py` — the
legacy→declarative table — keeps its seven EMS rows with their new legacy names and a comment block that
tells the counter's story in the past tense; `tests/test_p3_parity.py`'s canary asserts the new spellings
against a live build, and its static cross-check against the committed scenario files now covers the
aggregator inputs alone, since the dispatch names moved and those files are being retired with the JSON path
rather than regenerated. `hisim/json_generator.py` no longer reads a port's number out of its name or counts
the manager's constructor-built outputs: it asks the declarations which target ports the executor will grow
for itself, and takes a prefix by cutting the weight off the end.

The committed `system_setups/*.scenario.json` still spell the old names and were deliberately not
regenerated (#708 deletes them); `scenario-json-freshness` and `golden-json-check` are expected red on this
branch until it rebases past that deletion. Everything from here on is the finding as it was recorded,
including the two fixes it asked for, which are the two that landed.

Found by D-1. Retiring `advanced_heat_pump_hplib` — a class **no system setup instantiates** — broke
`golden-json-check` and `scenario-json-freshness` across twelve committed scenario JSONs. Two separate
defects compose to produce that blast radius.

*(Both of those gates, and the scenario JSONs they guarded, were retired on 2026-09-12; the finding's
text is kept as written, because the defects it describes are about the energy manager, not the gates.)*

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

**Fixed 2026-09-11**: the summer branch deactivates against `t_max_dhw_in_celsius`, so the water is heated
to the top of its own band. `tests/test_generic_chp.py` holds the CHP on at 50 °C in July and off above
60 °C; the paragraphs below are the finding as it was recorded.

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


### F-6 — four boilers that prepare domestic hot water are sized as if they only heated the rooms **[verified, fixed]**

Found by the D-9 review of #700: of the five boiler sizers whose recorded power that change was expected to
move, only three did. Chasing the silent two turned up four setups that resolve their boiler with a
`SizingContext` carrying `heating_load_in_watt` and nothing else:

- `system_setups/household_oil_building_sizer.py:337`
- `system_setups/household_pellets_building_sizer.py:311`
- `system_setups/household_wood_chips_building_sizer.py:325`
- `system_setups/household_gas_solar_thermal.py:147`

`GenericBoilerConfig.scale_thermal_power` (`hisim/components/generic_boiler.py:117-136`) takes the larger of
the space-heating load and the domestic hot water demand — 2.5 kW per apartment — and adds ten percent when
the boiler serves both at once. Both terms after the first read `number_of_apartments_in_building`, which
`MAXIMAL_POWER_LAW` takes from the context. A context without `number_of_apartments` makes the DHW term
zero, so the maximum is the space-heating load alone and the `dhw > 0 and sh > 0` guard on the uplift never
opens. Each of the four controllers is nevertheless built with `with_domestic_hot_water_preparation=True`,
and each boiler is wired to a `DHWStorage`: the water is heated by a machine sized as though it were not.
`system_setups/household_gas_building_sizer.py` and `household_gas_solar_thermal_building_sizer.py` pass
both facts, which is what makes the four read as omissions rather than as a choice. Every one of the four
already computes `number_of_apartments` from `arche_type_config_.number_of_dwellings_per_building` a couple
of hundred lines above, for the building config.

*Cost of not finding it: the sizing law is one expression, and half of it was unreachable in four of the six
setups that call it — not because the law was wrong, but because the caller never handed it the second fact.*

**Where it stands.** Decided 2026-09-11: fix, in its own PR with a golden bless, after D-9. Fixed. The four
setups now pass `number_of_apartments=number_of_apartments` into the boiler's context, exactly as the gas
sizer does; `generic_boiler.py` is untouched. All four buildings are single-dwelling, so the DHW term is
2.5 kW against a 7.78 kW heating load and the change is the 1.1× uplift alone:

| setup | boiler max thermal power | buffer storage volume |
|---|---|---|
| `household_oil_building_sizer` | 7780.75 W → 8558.83 W | 155.62 l → 171.18 l |
| `household_pellets_building_sizer` | 7780.75 W → 8558.83 W | 311.23 l → 342.35 l |
| `household_wood_chips_building_sizer` | 7780.75 W → 8558.83 W | 389.04 l → 427.94 l |
| `household_gas_solar_thermal` | 7780.75 W → 8558.83 W | 155.62 l → 171.18 l |

The buffer moves because D-9 (#700) sizes `SimpleHotWaterStorage` from the generator power; the pellet and
wood-chip boilers also carry a minimum power, 648.40 W → 713.24 W, a fixed twelfth of the maximum. Nothing else in the recorded twins changed. On `household_oil_building_sizer`'s one-week golden the
KPI effect is: boiler CAPEX +10.0 % (6050 € → 6655 €), boiler maintenance +10.0 %, total costs for the
period +2.2 % (146.69 € → 149.88 €), oil consumption +1.85 % (795.4 kWh → 810.1 kWh), total CO₂ +1.7 %, and
thermal energy delivered +0.3 % — the larger boiler and the larger buffer together shift the run pattern, and
the week ends having burnt slightly more. Seven golden references go stale and need a bless:
`household_oil_building_sizer`, `household_pellets_building_sizer` and `household_wood_chips_building_sizer`
(one-week and full-year each) and `household_gas_solar_thermal` (one-week).


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
