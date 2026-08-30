# P3 — random findings and defects

**Status:** living document · **Opened:** 2026-08-28 · **Last entry:** 2026-08-30
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

### F-17 — recording must happen inside the repository **[reported]**
A recorded file's schema comment is `os.path.relpath(schema, out_dir)`, so recording into `/tmp` changes line
one and makes a freshness check report all 21 setups as changed. The `--check` scratch directory is therefore
created at repo-root depth 1 and removed in a `finally`.

### F-18 — the recording driver cannot be parallelised **[reported]**
R8.3 requires a setup to reuse a parameter file written earlier **in the same run**, which is inherently
sequential. A full fleet recording takes about 15 minutes. Parallelising would need the dedup to move to a
second pass over the whole run.

---

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
