# P4 — random findings and defects

**Status:** living document · **Opened:** 2026-09-01 · **Last entry:** 2026-09-13 (9 findings)
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

That cost five declarative houses a KPI, which is the follow-up this branch also carries. The manager's
`get_component_kpi_entries` identified the dispatch outputs it derives its per-participant grid-consumption
KPIs from by looking for the participant's *class name* inside the port's field name — a spelling only the
legacy wiring produces, and one that in a declarative run had been matching nothing but the constructor's
phantom, which `sort_source_weights_and_components` found by tag and wrote to alongside the file's own
`DispatchFor…` port. With the phantoms gone the substring matched nothing and
`household_district_heating_building_sizer`, `household_heatpump_building_sizer`,
`household_wood_chips_building_sizer`, `household_hydrogen_boiler_building_sizer` and
`household_gas_solar_thermal_building_sizer` each silently dropped a KPI the golden expects. The block now
asks `dispatch_target_component_type` which participant kind a port is the electricity target of, reading
the `ELECTRICITY_TARGET` tag and the component type off the same `my_component_outputs` bookkeeping the
dispatch itself steers by, so both wiring paths report the same KPIs — the seven that were name-matched
(`HEAT_PUMP_BUILDING`, `HEAT_PUMP_DHW`, `RESIDENTS`, `ELECTRIC_HEATING_SH`, `ELECTRIC_HEATING_DHW`,
`SOLAR_THERMAL_SYSTEM`, and the car's `CAR_BATTERY` that already was) collapse into one table keyed by
component type, because every one of them computed the same thing and differed only in what it was called.

Removing the phantoms also removed the only thing adoption ever had to adopt, which is the branch's third
commit. A dispatch block used to ask the aggregator for a *signal* at `(tags, weight)` and take over the
port the constructor had already published for that participant, growing its own only where there was none
(P3, `adopted_dispatch_output`). No aggregator publishes a dynamic output before resolution any more — a
probe over all 36 committed twins finds the dispatch planner's "already published" table empty every time —
so the field, its guard, the name-property branch it overruled, the resolver branch that set it and the
port-collision exemption it needed are all deleted, and `created_dispatch_output_name` collapses into
`dispatch_output_name` because a dispatch block's port is now always the one it creates. What survives is
the *refusal*: a port the aggregator publishes that the runtime's tag-and-weight lookup would answer a
claim with is `EF-2B`, in exactly the containment terms the runtime uses, rather than something to be
absorbed — two ports for one signal is what this whole rule set exists to prevent.

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


### F-7 — `PVSystemConfig.location` is a string nobody reads, and it now sits beside a field it can contradict **[verified]**

`PVSystemConfig.location: str` (`hisim/components/generic_pv_system.py:116`) is passed by 23 setup call
sites (`location=weather_location`) and written into all 27 PV blocks of the committed energy-system YAMLs,
and the PV component never reads it: inside `generic_pv_system.py` it appears seven times — the module
docstring, the dataclass field, a parameter of the two factory methods and their two `location=location`
forwardings, and one docstring line — and in no computation. Its purpose was to make the PV cache key differ
between sites, which worked only as long as every setup copied the same variable into the weather and the PV
— a habit, not an invariant, and the origin of `pylpg_flakiness.md` F7.

F7 has since landed: the PV carries a `weather_identity` field sized from the weather itself
(`generic_pv_system.py:145`), so the key no longer needs `location` for anything. The key still contains it,
because the key is the whole configuration's JSON. What remains is a field that can visibly disagree with its
neighbour: a setup with Seville weather and a PV built with the factories' default `location="Aachen"`
records `location: Aachen` and `weather_identity: Sevilla/...` side by side, and the first is wrong without
consequence.

Retiring it is a P4-sweep item and not a small fix: the field is in the config class, two factory signatures,
23 setup call sites, one test and 27 energy-system YAML blocks, and the conversion of `PVSystemConfig`
(B-batch, `p4_component_sweep_requirements.md` R3 row `PVSystemConfig`) is the point at which those are all
rewritten anyway. Until then it costs nothing but a misleading line in every PV block.

*Logged 2026-09-03 while landing the zenith clamp (#628), re-verified 2026-09-12 against main. Do not fix
piecemeal; fold into the PV conversion.*


### F-8 — the caches did not know which code filled them, so a physics change was invisible to the golden gate **[verified, fixed]**

Found by #628. That branch changes the direct normal irradiance at every low-sun timestep — the zenith
clamp was a chained assignment that pandas' copy-on-write discarded — and on a cold cache it moves 33 KPIs
on `basic_household / one_week_60s` alone. On CI it moved nothing: `golden-check` passed all 24 week pairs,
and the bless run regenerated no reference at all. The change was real; the gate could not see it.

Every weather reader computed the DNI while it processed the source file, and the processed frame — DNI
column included — was what the component wrote to its cache. The key that entry was filed under was
`utils.build_cache_key_string`: the configuration's JSON plus the simulation parameters' unique key, and
nothing else. No part of it named the code that produced the content. CI restores `hisim/inputs/cache` from
the previous run (`.github/actions/hisim-cache`), whose own restore key hashes `hisim/utils.py` and
`hisim/caching/*.py` — the files that decide *where* an entry is filed, not the ones that decide *what is in
it*. So the runner read a DNI column computed by the old code, produced the old numbers, and the check
compared old numbers with old references and agreed. The two caches downstream sat on the same blind spot,
and worse: the PV cached AC power ratios computed from that irradiance, and the building cached the solar
gains through its windows, under keys that mention neither the weather nor the code that computed either.
A weather change could not have reached them even if the weather had recomputed.

**Fixed by phase 1 of the cache service** (`roadmap/cache_service_spec.md` §10), which the owner chose over
the version constant this finding first proposed. Three producers now key on their own code:
`hisim/components/weather/calculation.py` (`weather_series`),
`hisim/components/generic_pv_system/calculation.py` (`pv_series`) and
`hisim/components/building/solar_gains.py` (`building_solar_gains`). Each
key is a fingerprint of the producer module's source and of every `hisim` module in its transitive import
closure, plus the third-party versions in it and the calculation's DTO (§3). The `Weather` publishes its
artifact key, and the two downstream producers take it as key material while the series themselves travel as
payload excluded from the hash — the Merkle composition of §3.1. So an edit to the DNI moves the weather's
digest, and the PV's and the building's with it, by construction and with nothing to remember.

This branch is the first change the chain catches, and it was measured on it: with the cache warmed by the
producer stack and the clamp fix then applied to the same directory, the run logs `Weather series cache
miss`, `PV series cache miss` and `Building solar gains cache miss` — three new digests — and reports the
same 33 divergences a cold cache reports. Before the producers, that second run was a clean pass.

Two legacy caches still key on configuration and simulation parameters alone: `solar_thermal_system.py`
(pvlib solar positions, so a pvlib upgrade is what would be invisible) and `generic_car.py` (an LPG export
resampled by our own `resample_meters_driven`). Both are future producers; the survey in the spec's §12
lists them.

*Logged 2026-09-12 while landing the zenith clamp (#628). The finding is what the producer work was written
for, so it is filed here fixed rather than open.*

### F-9 — a declarative run asked for a scenario JSON dies after the simulation, parsing a port name only the legacy path ever wrote **[verified]**

Found on 2026-09-13 while going through what the post-processing options do to a run started from an
energy-system file. A declarative run whose simulation parameters carry
`WRITE_CONFIGS_FOR_SCENARIO_EVALUATION_TO_JSON` simulates all the way to the end and then raises in
post-processing:

```
$ HISIM_CACHE_DIR=<scratch>/cache hisim energy-system run \
      energy_systems/gas_boiler_household.energy_system.yaml \
      <scratch>/one_day_15min_scenario_eval.simulation.yaml
IFO:Simulation took 0.39s.
IFO:Writing component configurations for scenario evaluation to JSON file.
...
  File "hisim/postprocessing/postprocessing_main.py", line 1114, in write_config_data_for_scenario_evaluation
    write_standalone_scenario_json(ppdt.module_filename, my_sim=my_sim, desc=ppdt.description,
  File "hisim/json_generator.py", line 466, in write_standalone_scenario_json
    add_component_to_scenario(scenario=scenario, config=component.my_component.config, component=component.my_component)
  File "hisim/json_generator.py", line 225, in add_component_to_scenario
    component_entry, ins, outs = convert_component_to_json(config, component)
  File "hisim/json_generator.py", line 150, in convert_component_to_json
    raise ValueError(f"Label does not match expected format: {inp.field_name}")
ValueError: Label does not match expected format: ElectricalPowerConsumptionFromoccupancy
```

The simulation-parameters file is `energy_systems/one_day_15min.simulation.yaml` with the option added to
its `post_processing_options` list beside `EXPORT_TO_CSV`; the unmodified file, one January day at 900 s,
finishes the same system and writes its results. The option is the whole difference, and the exception is
raised after `Simulation took …`, so nothing the timesteps did is implicated.

Two options reach the generator, and neither is the one whose name reads closest to it.
`WRITE_CONFIGS_FOR_SCENARIO_EVALUATION_TO_JSON` calls `write_config_data_for_scenario_evaluation`
(`postprocessing_main.py:476-478`) and `WRITE_COMPONENT_CONFIGS_TO_JSON` calls
`write_component_configurations_to_json` (`:472-474`); both call `write_standalone_simulation_json` and
`write_standalone_scenario_json` out of `hisim/json_generator.py`, and the second option was verified to
fail on the same file with the same message. `PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION` does not touch the
generator at all — `prepare_results_for_scenario_evaluation` writes the resampled result CSVs, and the
option appears inside `write_config_data_for_scenario_evaluation` only to pick the subdirectory the JSONs
go into.

**Mechanism.** For every dynamic input of a `DynamicComponent`, `convert_component_to_json` has to write
down which component the input reads — and it recovers that name out of the port's own name
(`json_generator.py:147-150`):

```python
pattern = rf"^Input_(.*?)_{re.escape(source_component_field_name)}_\d+$"
match = re.match(pattern, inp.field_name)
if not match:
    raise ValueError(f"Label does not match expected format: {inp.field_name}")
```

The format it insists on is the legacy one. `add_component_input_and_connect` names its port
`f"Input_{source_object_name}_{source_component_output}_{num_inputs}"` (`dynamic_component.py:626`), so the
source name is the middle field of a three-field label and the regex reads it back out of the middle. The
declarative path names the same port from a template instead —
`AGGREGATOR_INPUT_TEMPLATE = "{source_output}From{source_name}"` (`hisim/config/channels.py:306`), applied in
`add_resolved_dynamic_input` (`dynamic_component.py:499`) — so the meter's port is called
`ElectricalPowerConsumptionFromoccupancy`, and a pattern anchored on `Input_` matches nothing whatsoever.

`From` is not a spelling the parser knows, and the lowercase `occupancy` is not what defeats it. That name is
lowercase because a component key in an energy-system file is an instance name its author chooses and
`gas_boiler_household.energy_system.yaml` writes `occupancy:`; a CamelCase key fails in exactly the same
place, verified — `basic_household.energy_system.yaml`, whose keys are `UTSPConnector`, `PVSystem`,
`HeatPump`, dies on `ElectricityOutputFromHeatPump`. What the message names is simply the first port of the
first dynamic component the writer reached, so it differs per file and none of the names in it is the cause.

Python-mode runs of the same setup pass, verified rather than inferred: `system_setups/basic_household.py`,
built through `initialize_from_python` with one January day at 900 s and the same option set, runs to
`Finished postprocessing`, and the `scenario.json` it writes carries
`"source_object_name": "PVSystem"`, `"UTSPConnector"` and `"HeatPump"` — three names the regex pulled out of
three `Input_…_…_N` labels. Nothing about the option is broken; it has simply never been handed a label of
any other shape.

The name being parsed for is on the port already, on both paths. `Component.connect_input` records
`src_object_name` on the input it wires (`component.py:427`); the legacy path reaches it through
`add_component_input_and_connect`, and the declarative path through `wiring_checks.py:130-134`, which passes
`src_object_name=wire.source_runtime_name`. Built from the gas-boiler file, the meter's one dynamic port
reports `field_name='ElectricalPowerConsumptionFromoccupancy'`, `src_object_name='occupancy'`,
`src_field_name='ElectricalPowerConsumption'`. The fact the regex reconstructs is sitting beside the name it
reconstructs it from.

**Blast radius.** Every declarative run that asks for either JSON-writing option, whatever the file: the
crash is in the writer, not in any one system's wiring, and it needs only one dynamic component with one
resolved feed — which every energy-system file with an aggregator has. The eleven building-sizer setups set
`PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION` rather than the failing option, so the recorded twins and the
golden runs do not go through this; what it costs is a declarative run configured the way
`tests/test_system_setups_basic_household_with_all_resultfiles.py` configures a Python-mode one. The writer
itself is the legacy JSON mode's own format, and the *output* half of the same function already knows it:
`json_generator.py:107-117` refuses a dynamic output that was not named from a prefix and a weight with a
message saying in as many words that "a run built from an energy-system file is written down by that format,
and the scenario JSON is the legacy path's own file". The input half discovers the same thing by regex
failure, one loop later, with a message that names a port.

*Cost of not finding it: a run that is only ever going to fail is allowed to simulate first, and then fails
with a message about a label format — naming neither the option that asked for the file nor the fact that the
file belongs to the other execution mode.*

**Where it stands.** Not fixed; this entry logs it. Two candidate fixes, and which one is right is a question
about how long the scenario JSON is meant to live, so it is the owner's:

- **Parse nothing.** Read `inp.src_object_name`, which the wiring already set to the source component's real
  name on both paths, instead of matching a regex against the port's name. The writer would stop caring how a
  port is named, and legacy runs would produce the same JSON they produce now, since the label's middle field
  and `src_object_name` are set from the same argument.
- **Retire the label parsing along with the writer's claim on declarative runs.** Refuse the declarative input
  the way `:107-117` already refuses the declarative output, and refuse it before the first timestep rather
  than after the last, so the run fails with an explanation and no wasted simulation.


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

### F-10 — a rounded product renders as if only the fact were rounded **[verified]**

Found on 2026-09-13 while converting the battery (B2). Its inverter law is
`(Size.PV_PEAK_POWER_IN_WATT * 0.5).rounded(2)`, and `hisim energy-system describe` prints it as

```
law: 0.5 * Size.PV_PEAK_POWER_IN_WATT.rounded(2)
```

which reads as `0.5 * round(fact, 2)`. The rounding applies to the product. `_RoundedLaw.describe`
(`hisim/config/laws.py:329`) appends `.rounded(n)` to whatever its inner law renders, and
`_ScaledLaw.describe` (`:284`) renders `factor * inner` without brackets, so any compound inner
law loses its grouping in the suffix form. Nothing before the battery had a compound law under a
rounding: `HEATING_LOAD_IN_WATT.rounded(2)` and the HDS controller's laws round a bare term.

The string is not only CLI output. `describe()` is what a `ConfigSizingError` quotes
(`hisim/config/sizing.py`, the `<- {effective_law.describe()}` in the resolution loop), so a sizing
failure on such a field would name a law that differs from the one that ran.

Fix: bracket a compound inner in `_RoundedLaw.describe` — `(0.5 * Size.PV_PEAK_POWER_IN_WATT).rounded(2)`
— or have `_ScaledLaw` bracket itself whenever it is not the outermost law. One line either way; a
test over the battery's two laws pins the rendering. Not fixed on the B2 branches, which are
conversions; belongs with the next kernel touch.

### F-11 — the P2 mockups still name presets that the P4 decisions renamed, so their pinned errors pass for the wrong reason **[verified, fixed]**

Found on 2026-09-13 after the PV and battery conversions (B2). `roadmap/declarative_energy_systems/energy_system_mockup.yaml`
writes `preset: standard` for `pv_south` and `pv_east` (lines 157, 163) and `preset: sized_to_pv`
for the battery (line 222). D-13 minted the PV preset `rooftop` and D-14 the battery preset
`standard`; neither mockup line was updated when the decisions were taken.

`tests/test_energy_system_classes.py::ExpectedFailures.BY_MOCKUP` pins all three entries as `EF-13`
("the configuration class declares no such preset"). Before B2 they failed with `EF-13` because the
classes had **no presets at all**; after B2 they fail with `EF-13` because the classes have presets
**under different names**. Same code, different cause, and the test cannot tell the two apart — so
UC-P4.4's "the pinned error set shrinks to empty as B2–B5 land" will not happen for these three
entries however many classes convert. The PV entry's comment is also stale: it says
`power_in_watt AUTO <- building.conditioned_floor_area_in_m2`, and the law reads `roof_area_in_m2`.

Fix: rename the three preset lines in the mockup and the comment on line 157, then remove the three
entries from `BY_MOCKUP` and watch the test stay green.

**Fixed 2026-09-14** on the preset-rename branch: the PV lines of all three mockups say `rooftop`, the battery lines already said `sized_to_pv`, both stale `conditioned_floor_area_in_m2` comments name `roof_area_in_m2`, and seven `EF-13` pins left `BY_MOCKUP` (`pv_south`, `pv_east`, `battery`, `pv_central`, `apt1_pv`, `apt2_pv`, and the MFH `battery`) with the equality assert green. The pins that stay are all unconverted classes. Six test fixtures in `tests/test_energy_system_loader.py` and `tests/test_energy_system_validation.py` still write `preset: standard` on a `PVSystem` — not class-validated, so green, but the same shape. The mockup lives under `roadmap/`, which the
B2 conversion briefs kept out of scope; a doc-only commit at the top of the B2 stack is the natural
place, or the first B3 PR.

### F-12 — an optional sized field cannot receive a `None` fact **[verified]**

Found on 2026-09-13 converting the fuel meter (B2). `FuelMeterConfig.heating_value_of_fuel_in_kwh_per_liter`
and `fuel_density_in_kg_per_m3` are `Sizable[Optional[float]]` with `sized_field(optional=True)` and copy laws
over the boiler's facts. `GenericBoilerConfig.fuel_constants` returns `None` for both when the carrier is
district heating, and the boiler's contribution passes that `None` on. The engine then refuses:
`engine.py:_bind_one` raises `"'<fact>' provided as null by '<provider>' (feature off)"`, and in Python mode a
`SizingContext` field left `None` makes `_FactTerm.evaluate` raise `"the SizingContext carries no <fact>"`.

`optional=True` today means only that a `None` **written on the field** counts as resolved. It does not let a
law legitimately produce nothing. So the district-heating setup pins the two `None`s itself instead of
resolving them, and the moment `generic_district_heating` is converted and contributes the facts, its fuel
meter's laws will hit the refusal — even though every declaration involved says `None` is a legal value.

Fix, before the district-heating conversion (B4): an optional sized field whose fact is `None` resolves to
`None`. One rule in `_bind_one` (and the Python-mode `evaluate`), keyed on the field's `optional` flag, so a
non-optional field keeps refusing a null fact as it does today.

### F-13 — `dataclasses.replace` drops a preset's provenance, and the twin loses its `preset:` line **[verified]**

Found on 2026-09-13 converting the fuel meter (B2). A preset builder stamps the returned instance with
`ConfigBuilder.PROVENANCE_ATTRIBUTE`, which is what the recorder reads to write `preset: standard` and only
the fields that differ. `dataclasses.replace` builds a new instance and copies fields, not attributes: the
stamp is gone, and the recorder writes the whole block as literals with no `preset:` line — a silent
regression of exactly what the conversions exist to produce. `resolve_config` carries the stamp across;
`replace` does not.

Plain attribute assignment on the preset instance before `.resolve(...)` keeps the stamp, and that is the
idiom every converted setup now uses (the PV's `azimuth`/`tilt`, the fuel meter's two constants). The
survey's conversion pattern should say so, or a `replace`-shaped helper should carry the stamp; until one
of the two exists this is an easy way to lose a preset from a twin without any test noticing, since the
twin still loads and runs identically.

### F-14 — the recorder had the provenance to write `AUTO` and wrote the number instead, so no twin could be reused **[verified, decided]**

Found on 2026-09-14 while reviewing the battery conversion (#740). Every recorded twin pins the
values its laws computed — `power_in_watt: 22272.28`, `custom_battery_capacity_generic_in_kilowatt_hour: 22.27`
— so a twin describes one archetype's numbers and re-sizes nothing when reused for another
building. P3 chose that shape (R2.4) because "whether a value was sized or hand-copied is not
recoverable from a run" (`p3_recording_requirements.md` §5e). That stopped being true with P4:
`resolve_config` attaches a `sizing_record` to every resolved configuration, per field, and the
recorder reads that object's neighbour (`preset_provenance`) but not the record itself;
`recording/configs.py:unresolved` then states outright that a sized field "always lands in the
deviation block". The P3 glossary meanwhile promised the recorded sizer file as "the P5 consumer
input", which a pinned file cannot be.

Decided as A-P3.1 (`p3_recording_requirements.md` §11): the recorder leaves a
law-computed field unwritten when its facts have a declared provider in the recorded system, so the
preset's `AUTO` stands; assigned fields stay concrete, a pinned law field says why; a twin whose
left-to-the-preset fields do not resolve to the run's values fails the recording. Implemented on `twins_resize`.
