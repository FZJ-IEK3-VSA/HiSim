# P3 — random findings and defects

**Status:** living document · **Opened:** 2026-08-28 · **Last entry:** 2026-08-31 (34 findings)
**Context:** things that surfaced while implementing `roadmap/declarative_energy_systems/p3_implementation_spec.md`
and were **not** what the work set out to do. Kept separately so the requirements and the spec stay about the
design, and so nothing found on the way is lost when the branch merges.

Each entry says what was found, how it was found, what it costs, and where it stands. Entries marked
**[verified]** were re-checked independently of the agent that reported them; **[reported]** entries rest on
the implementing agent's evidence alone.

---

## 1. Defects in already-merged code

### F-1 — `hisim/energy_system/audit.py` crashes after a successful simulation **[reported, fixed]**
An entry that overrides a field its preset leaves sizable makes `AuditBuilder.overrides` hand the `AUTO`
sentinel to the plain-data writer, which refuses it. The exception lands in `write_records`, i.e. **after**
the simulation has finished and its results are already computed, so the run dies at the very end. This is in
P2 code merged as #592 and would have hit every `hisim energy-system run` of a file with such an override.
Found by the first recordings, which produce exactly that shape. Fixed while porting the recorder.
*Cost of not finding it: the declarative path's headline command aborts at the finish line.*

### F-2 — `scripts/regenerate_scenario_jsons.py` regenerates against the **installed** package **[reported]**
It invokes `hisim/hisim_convert_to_json.py` as a script, so `sys.path[0]` is `hisim/` and the editable install
in the primary checkout wins over the worktree the script is running in. The first regeneration of the v1
twins silently **reverted** the component renames that had just been made, and looked like a successful run.
Needs `PYTHONPATH=<repo>` or an invocation via `python -m`. **Still open** — the recorder's own driver uses
`python -m hisim.cli` and does not have the bug, so nothing in P3 depends on the fix.
*This one is nastier than it looks: it fails by producing plausible output, on a script whose whole job is to
keep generated files honest.*

### F-3 — `@constructor` never stamps provenance **[reported]**
Only `@preset` writes the provenance attribute (`hisim/config/presets.py:_declare`), and the stamp carries a
builder name with no arguments. A recorder can therefore never emit a `constructor:` entry, and P3's R2.2 and
spec §3.4 are preset-only in practice. **Open** — belongs to P1's kernel, not to P3. Recording a constructor
entry needs the stamp to carry the arguments it was called with.

---

## 2. Defects in system setups

### F-4 — `household_gas_solar_thermal` counts occupancy electricity twice **[verified]**
The setup feeds the electricity meter from `UTSPConnector.ElectricalPowerConsumption` explicitly (`:233`)
*and* registers the meter with `connect_automatically=True`, which applies the meter's own default connection
for the same source and output. `hisim/simulator.py:636` does not de-duplicate, so the sum really is taken
twice. **This is the mechanism behind** the long-standing failure
`ValueError: The relative elecricity demand should not be over 100 %. Electricity from grid 21.72 kWh, total
electricity consumption 10.9 kWh`, which had been parked as an unexplained KPI-layer bug.
Found because the recorder refused the file (EF-25, one input fed twice) — the declarative path could not
express a wiring that the imperative path accepted silently. Owner decision 2026-08-30: fix the setup **and**
make the simulator refuse duplicate feeds. The setup is not golden-gated, so no reference is re-blessed.
*A format strict enough to reject nonsense found a bug that years of running the code did not.*

### F-5 — fifteen component names were not identifiers, not four **[verified]**
The setup-fleet survey found four. Turning the rule on exposed eleven more, every one a component's
**class-level default identity** (`"Example Component"`, `"CHP Controller"`, `"Smart Device"`,
`"rSOC and Battery Controller"`, …) — names nobody has to type to get, which is exactly why a survey of what
setups *write* could not see them. None appears in `golden_references/`, in any `.scenario.json` or in
`energy_systems/`; verified independently. All renamed.
*Method note: surveying call sites finds what authors write, never what defaults supply.*

### F-6 — fifty-seven port names were not identifiers **[verified]**
The same rule applied to `ComponentInput`/`ComponentOutput` field names exposed 57 across 17 modules
(`sumbuilder`, `example_storage`, `advanced_fuel_cell_controller` ×12,
`generic_electrolyzer_and_h2_storage` ×17, `loadprofilegenerator_utsp_connector` ×6, …). Checked before
renaming: **none of the 54 distinct strings occurs in any golden reference**, so no KPI key moved. Port names
do reach result-CSV column names, so this was the load-bearing check.

---

## 3. Gaps in the format and the component model

### F-7 — only two components in the codebase declare `CHANNELS` **[verified]**
`electricity_meter.py` and `controller_l2_energy_management_system.py`. Every other aggregator had no
declarative spelling for its feeds, so **six of the eight golden setups** (gas, oil, pellets, wood chips,
hydrogen boiler, district heating) could not be recorded at all. P3's R3.1 and AC-P3.2 were therefore
unreachable, and the requirements did not notice: the work is specified in
`p4_component_sweep_requirements.md` **R2.2**, one phase too late. Pulled forward into P3 with the six golden
checks as proof of behaviour-neutrality.
*Sequencing lesson: a P4 gate silently gated P3. Worth re-reading P4's other gates (R2.x) for the same shape.*

### F-8 — the channel model could not express a fuel meter **[verified]**
`DynamicConnectionChannel` carried one concrete load type, and `ChannelMatcher` compared it against the
participant's port. But `GenericBoiler.EnergyDemandSh` is typed by *fuel carrier*
(OIL / PELLETS / WOOD_CHIPS / GAS / GREEN_HYDROGEN), and district heating feeds one tag with both HEATING and
WARM_WATER inside a single household. Two channels with identical tags tie and are rejected (EF-28), so F-7
was not solvable as specified. Resolved by letting a channel declare `LoadTypes.ANY` / `Units.ANY` — sentinels
that already existed (`loadtypes.py:111,147`) and that `wiring_checks.py` already honoured between two ports,
so channel matching merely became consistent with port matching. The wildcard is on the load type only; the
unit stays concrete and the **tags** still discriminate, so no ambiguity is introduced and EF-28 is untouched.

### F-9 — `Car` cannot be built from configuration alone **[reported]**
`Car.__init__` requires `data_dict_with_car_information`, computed from the occupancy **instance** at setup
time, while the declarative path constructs from `(parameters, config)` only. `household_heatpump_car_building_sizer`
is therefore unrecordable (EF-33). Owner decision 2026-08-30: leave unrecorded and name it as a **P4** gap —
it needs a constructor taking the occupancy-derived values as facts, which is class-conversion work.

### F-10 — a preset carrying its own sizing law broke the sparse diff **[reported, fixed]**
The recorder diffs a config against a **fresh, unresolved** build of its preset. `preset_pellets` and
`preset_wood_chips` hold a `_ScaledLaw` in that state, which the config-block writer rightly refuses (EF-60),
so pellets and wood chips stayed unrecordable even after F-7. Fixed by rendering law-valued baseline fields
as the `AUTO` sentinel — the baseline is only ever compared, never written.

---

## 4. Errors in P3's own documents

Recorded so the spec is corrected rather than quietly worked around.

| # | Where | What was wrong |
|---|---|---|
| F-11 | spec §7 I-2 | Says to check "no AUTO / sizing_sources / groups / variants" by reusing `record.assert_fully_concrete` — but that function also rejects `preset:`, which R2.2 and AC-P3.5 **require** a recording to carry. Split into a reusable `assert_no_sentinels`. |
| F-12 | spec §9 T-7 | `dump(load(f)) == f` cannot hold literally while R2.4 also mandates a header line and a generated-by line. It holds of the body; the boundary is now explicit. |
| F-13 | spec §3.3 | "Four illegal names" — see F-5 and F-6; the true counts are 15 components and 57 ports. |
| F-14 | spec §3.5 | "Pick ONE choke point" for name enforcement is impossible: `add_input`/`add_output` are bypassed by every dynamic component, so both `ComponentInput.__init__` and `ComponentOutput.__init__` enforce. |
| F-15 | requirements R3.1 | Unreachable as written until F-7 is fixed; the dependency on P4 R2.2 was not stated. |

---

## 5. Tooling that cannot be trusted as-is

### F-16 — `golden_validate.py --scan-all` is not deterministic **[reported]**
In a full 21-setup scan, `automatic_default_connections` and `household_district_heating_building_sizer` were
reported **non-deterministic** (12 and 16 differing KPIs), yet both PASS when re-run in isolation with
`--setup`. Cross-run LPG/cache interference inside one long scan session, not a property of the setups.
**Consequence:** `--scan-all` is unusable as a gate, and the eligibility table in
`declarative_energy_systems/p3_setup_inventory.md` §4f — which came from one such scan — should be read as
indicative, not authoritative. Per-setup runs are the reliable form. **Open.**

### F-21 — the golden gate cannot compare a KPI that is numerically zero **[verified]**
A full-fleet `golden_check.py` run fails one pair of sixteen:
`household_electric_heating_building_sizer / one_week_60s`, on the KPI
*"Temperature deviation … below set temperature 20.0 Celsius"* — `ref = 1.216772342142273e-09`,
`got = 1.2167721052946946e-09`. The absolute difference is **2.4e-16**, but the *relative* difference is
**1.95e-7**, which exceeds `rel_tol = 1e-9` by a factor of ~195. The gate's report prints `rel diff=0.000%`,
which rounds the number that decides the verdict out of view.

The cause is the tolerance regime, not the code: `golden_ref_spec.md` §7 fixes `rel_tol = 1e-9, abs_tol = 0`
on the reasoning that it "absorbs sub-ULP platform noise". That holds for KPIs of ordinary magnitude and
fails exactly for KPIs that are **physically zero** — here a temperature deviation of 1.2e-9 °C·h — where a
relative comparison has nothing to be relative to and one last-bit difference is an infinite relative error.

The pair **passes in isolation** on the implementation branch *and* on a branch containing no code changes
at all, and fails only inside a full-fleet run, so it is order- or cache-dependent and unrelated to P3.
Nothing was re-blessed. **Open**, and worth fixing properly: an `abs_tol` floor (even 1e-12) would make
near-zero KPIs comparable, and is a smaller change than it sounds because no KPI of real magnitude is
affected by it.
*Read together with F-16, the pattern is the same: this fleet's tooling is reliable per-setup and flaky
in bulk.*

### F-22 — a hand-built test port was the last illegal name **[verified]**
`tests/test_example_component.py:38` constructed a fake `ComponentOutput` with
`field_name="thermal energy delivered"`. The identifier rule reaches every `ComponentOutput`, including ones
a test builds to stand in for a source, so this failed under the `system_setups` marker — which neither the
`base` nor the `extendedbase` gate covers, so it went unnoticed for two rounds of verification. Fixed.
*Process note: `-m base` and `-m extendedbase` are not the whole suite. `system_setups`, `buildingtest`,
`jsonconfig`, `utsp` and `postprocessingoptions` exist too, and CI runs them.*
The two production strings reading `"Total thermal energy delivered"` name a `KpiEntry`, not a port, and are
correctly untouched — prose belongs in a KPI label.

### F-23 — `/tmp` is a shared 7.4 GB tmpfs and worktrees fill it **[verified]**
A verification run was aborted mid-flight by the disk quota, corrupting a cache file; six merged worktrees
from earlier work were holding 3.2 GB. Removing them took usage from 71 % to 23 %. Worth knowing when a
long run fails for no visible reason: check `df -h /tmp` before believing the error.

### F-17 — recording must happen inside the repository **[reported]**
A recorded file's schema comment is `os.path.relpath(schema, out_dir)`, so recording into `/tmp` changes line
one and makes a freshness check report all 21 setups as changed. The `--check` scratch directory is therefore
created at repo-root depth 1 and removed in a `finally`.

### F-18 — the recording driver cannot be parallelised **[reported]**
R8.3 requires a setup to reuse a parameter file written earlier **in the same run**, which is inherently
sequential. A full fleet recording takes about 15 minutes. Parallelising would need the dedup to move to a
second pass over the whole run.

---

---

## 7. What the parity rig found when it first ran

The rig (R11) compares a setup's Python run against its recorded twin, in one process, at exact equality.
Its first full matrix was **14 of 40 triples passing**. After the fixes below it is **40 of 40**. Everything
in this section was found by that first run and by nothing else.

### F-24 — the executor paired dispatch signals positionally, and the battery stopped charging **[verified]**
Sixteen triples — every EMS building sizer. `sort_source_weights_and_components` finds a participant's
control signal with `get_all_dynamic_outputs(tags, weight)` and **zips the result against the sorted inputs
positionally**. `L2GenericEnergyManagementSystem.__init__` creates one control output per participant *class*
it declares a default feed for, and `ComponentWrapper.register_component_outputs` prunes the ones whose class
is absent — but `add_resolved_dispatch_output` set `source_component_class=None`, so a file-created dispatch
output was **never** pruned. Two outputs answering one search made the list one entry too long, and the
battery's input at weight 6 was paired with the heat-pump-DHW output. Names cannot catch this: they differ.
Measured on `household_heatpump_building_sizer/january` before the fix: battery charging **32.09 → 0.0 kWh**,
discharging 24.06 → 0.0, grid cost 59.83 → 62.09 €, **62 of 108 KPIs moved**.
Fixed by making a dispatch block ask the aggregator for a *signal* at `(tags, weight)` rather than for a
port: it adopts the port the aggregator already publishes and grows a derived one only when there is none.
New guard `EF-2B` (ambiguous dispatch signal), following the `EF-25` precedent.

**A diagnosis that was wrong, recorded because the wrong version is instructive.** The first reading blamed
the recorder for writing `dispatch: {}` on a feed whose dispatch output nothing reads, and concluded the
committed twins were wrong. Implementing that fix made 11 of 21 setups **unrecordable** with `EF-29`: the
`consumption_controlled` channel declares `dispatch=DispatchRule.REQUIRED`, so a feed on it *must* carry a
dispatch block. The twins were faithful all along; the defect was entirely on the replay side, and no twin
needed re-recording.

### F-25 — a latent twin of F-24, deliberately left alone **[reported]**
`L2GenericEnergyManagementSystem.__init__` already creates **two** `HEAT_PUMP_BUILDING` + `ELECTRICITY_TARGET`
weight-2 outputs, one for `HeatPumpHplib` and one for `MoreAdvancedHeatPumpHPLib`. It is harmless today only
because pruning removes whichever class is absent. A symmetric guard on the imperative path would fail every
EMS setup at construction, so it was left visible rather than hidden. **Open.**

### F-26 — an entry's own `config:` block was applied twice **[verified]**
Six triples, the solar-thermal setups. `configure.py::_realize_origin` deserializes a complete block with the
class's `from_dict`, correctly rebuilding nested dataclasses; `_apply_overrides` then re-applies **the same
block** field by field through `ConfigValueCodec.decode`, which passes a nested mapping through untouched.
`config.coordinates` ended up a plain `dict`. This is merged P2 code and it breaks **any hand-written file**
with a full `config:` block containing a nested dataclass, not only recordings. The crash surfaces in
`i_simulate` (`flat_plate_precalc` reads `config.coordinates.latitude_in_degrees` per timestep), not in
`i_prepare_simulation`. Fixed by making the two paths exclusive.

### F-27 — verifying that a recorded file *builds* does not prove it *runs* **[verified]**
The recorder's EF-R5 check stopped at `build_energy_system`, so F-26 sailed through it. The check now also
calls `prepare_calculation()`, which costs nothing measurable (fleet `--check`: **36.5 s** with, **37.0 s**
without, since the recording run has already filled the caches). Honest caveat, stated in the commit: this
extension would **not** have caught F-26, which needs a timestep to fail. Closing that class of defect means
simulating.

### F-28 — KPI keys carry aggregator port names **[verified]**
After F-24 was fixed, every EMS household still failed on four KPI keys **whose values were identical**:
`Priority for Input_Battery_AcBatteryPowerUsed_6` against `Priority for AcBatteryPowerUsedFromBattery`. The
EMS builds `KpiEntry(name=f"Priority for {input_sorted.field_name}")`, so a port name is embedded in a KPI
key. The rig now translates KPI keys through the same declared table, matching whole words only and refusing
a table that maps one legacy name onto two declarative ones.
*This is a second argument for renaming the legacy aggregator ports in P4/P5: as long as they differ, the
difference leaks into KPI keys, not just result columns.*

### F-29 — a simulation parameter leaked into a component config **[reported]**
`RandomNumbers.timesteps` is recorded literally — `96`, the length of the one-day 15-minute window it was
recorded under — so the twin only runs at that horizon and raises `IndexError` at any other. This is exactly
what AC-P3.12 forbids, showing up at the *value* level rather than as a parameter key. Worse, `RandomNumbers`
seeds `random.Random()` with **no seed**, so the two runs draw different numbers by construction and those
two setups could never reach exact parity. **Both fixed 2026-08-31** (owner decision): the seed is now a
configuration field, because a system description has to be able to say what a run will do, and the count
comes from the simulation parameters, where the length of a run belongs. The injected-generator argument
stays for tests. Both setups now pass in both windows, taking the matrix to **40 of 40**.
*The count was the more interesting of the two: every setup wrote `timesteps=my_simulation_parameters.timesteps`
into a component config by hand, so the leak was authored, not accidental, and completely invisible while the
only description of the system was the Python that built it.*

### F-33 — a setup was changed without regenerating its v1 twin **[verified]**
Removing the duplicate occupancy feed from `household_gas_solar_thermal.py` (F-4) changed the setup but left
22 lines describing the now-nonexistent connection in `household_gas_solar_thermal.scenario.json`.
`scenario-json-freshness` would have failed on it in CI; it surfaced here only because an unrelated
regeneration ran. Regenerated. *The gates exist for exactly this: a setup and its twins move together, and a
human will forget.*

### F-34 — a failed recording leaves an unbuildable file in the tree **[verified]**
`record_all_setups.py` writes the file and then verifies it builds, and on failure leaves it "in place for
inspection" — so after a failed run an untracked `energy_systems/<stem>.energy_system.yaml` sits there that
must never be committed. Sensible for debugging, hazardous next to `git add -A`. Should either be written to
a scratch path or named so `.gitignore` catches it. **Open.**

### F-30 — correction to F-8's rationale **[verified]**
R11.5's second window was argued partly from the air conditioner's `ZeroDivisionError`, on the reading that a
January window gives a cooling system nothing to divide by. The rig shows that setup divides by zero **in
July as well**, so the window is not the cause of that particular crash. The second window keeps its
justification — a January-only fleet measures cooling and solar-thermal setups at their annual minimum, and
running both windows is what proved this — but the air conditioner is a KPI-layer defect, not a seasonal one.

### F-31 — the near-zero golden KPI reproduces differently on different branches **[verified]**
F-21's failing pair passed in isolation on the documents branch and fails in isolation on the implementation
branch, bit-identically at `1.2167721052946946e-09`. Independently proven unrelated to any change here: with
the only two golden-path files reverted (`dynamic_component.py`, `config/channels.py` — everything else
touched lives in `hisim/energy_system/`, which the Python path never executes) the failure is unchanged.
It is one ULP against `abs_tol = 0`, and which way it falls depends on cache state, not on code. Reinforces
F-21: the fix is an absolute floor, not a re-bless.

### F-32 — two runs must not share a cache directory **[reported]**
Giving both sides of a parity comparison one cache directory let the second run read the first run's PV
output back out of a CSV. That masks a real configuration difference *and*, because the round trip is not
bit-exact, invents a fake one. Each side now gets its own empty cache, and both run the same post-processing
option set regardless of what the setup appended.
*Generalises beyond the rig: any A/B comparison of two HiSim runs on one machine has this hazard.*

## 6. Process

### F-19 — the mockups are executable fixtures, not documents **[verified]**
`energy_system_mockup.yaml` is parametrised over by `tests/test_energy_system_*.py`, and AC-P2.1 requires
every mockup to load and validate. Adding an R15 `variants:` block to it — on a branch containing **no code
changes at all** — produced 11 failures plus a collection error, because the loader rejects unknown top-level
keys. Fixed by moving the syntax into R15 as a worked example; P2.1 puts it in the mockup in the same change
that teaches the loader to read it.
*Rule worth keeping: a mockup may only gain syntax the loader already accepts.*

### F-20 — a strict reader is a bug detector **[observation]**
F-4 is the clearest case, but the pattern repeats through this list: the double feed, the illegal names, the
missing channels and the un-buildable `Car` were all invisible while the only description of a system was the
Python that built it. Every one of them surfaced the moment something had to write that system down and read
it back. Worth remembering when weighing the cost of the remaining conversions.
