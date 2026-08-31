# P3 — Recording and setup migration: implementation specification

**Status:** draft, all design questions decided · **Date:** 2026-08-28 (DQ1–DQ5 answered by the owner the same day; §13)
**Implements:** `roadmap/declarative_energy_systems/p3_recording_requirements.md` (revision 2026-08-28, all questions decided) under the epic `epic.md` (E1–E8) · **Plan:** `plan.md` §P3
**Author(s):** assistant (every item `[proposed]` unless tagged) · **Reviewers:** HiSim core team
**Branch / PRs:** new branch off `main` (P2 merged as #592); planned PR-1 … PR-7, §10
**Base evidence:** `main` plus the merged P2, verified 2026-08-28 — `hisim/energy_system/` (28 modules), `hisim/config/presets.py` (`preset_provenance`, `presets_of`, `constructors_of`), `hisim/component.py` (`src_object_name`/`src_field_name` at :131, `get_default_connections` at :420), `hisim/json_generator.py` (519, the v1 recorder), `scripts/regenerate_scenario_jsons.py`, `scripts/golden_*.py`, 24 setups of which 3 are removed by R5.2
**Solution-design input:** `json_v2:hisim/scenario_v2/templating.py` (222) and `json_v2:hisim/scenario_v2/parity.py` (500) — both deliberately **not** ported in P2 ("the recorder is P3", "the wiring-diff harness is a P3 golden tool", P2 spec §6) · `hisim/json_generator.py` for the bare-`inputs` decision and parameter filtering

**Decided by the owner, 2026-08-28** (§13): the four illegal component names are renamed at the source **and** `Component.__init__` enforces the identifier rule afterwards (DQ2); the parity rig compares every result column through a hand-authored renaming table (DQ4, DQ5 — R11.3 and C-P3.2 amended accordingly); `component_id` is guarded by I-4/EF-R2 in P3 and answered in P5 (DQ3); recording lives in a subpackage (DQ1). Ordering decided with them: **P2.1 lands before P3 starts**, the two toy setups are renamed rather than deleted, and the KPI-layer repair list stays a separate workstream.

**What a reviewer must still check here:**
1. §3.4 — what ports from `templating.py`: the preset-provenance half, **not** the `AUTO` de-resolution half, which R2.4 forbids in a recorded file.
2. §3.5 — the three-way `inputs` decision and its reuse of `json_generator.compare_automatic_connections`.
3. §3.7 — parameter-file deduplication by normalised content, and the exclusion of `cache_dir_path` from both the comparison and the written file.
4. §3.6 / §7 — the determinism rules R4 turns from aesthetics into requirements, and the six invariants.

---

## 1. Summary

P3 is a recorder, a grouping pass and a temporary parity rig. The recorder is the only genuinely new machinery: everything in `hisim/energy_system/record.py` runs *file → built system → realized file*, and P3 needs the opposite direction, *live simulator → `EnergySystemFile`*. Around that one new pass, most of the work is porting: `json_v2:parity.py` supplies the wiring snapshot, the port-renaming table and the result comparison; `json_v2:templating.py` supplies the sparse preset diff; `json_generator.py` supplies the rule for when a wire may be written as a bare `inputs` item and how simulation parameters are filtered. The two places where care is needed are §3.3, where four runtime component names cannot be written into a v3 file at all, and §3.6, where R4's "byte-identical across machines" turns float formatting and mapping order into a requirement rather than an aesthetic. The grouping pass (§3.8) is the only part that depends on unshipped work — it emits `variants`, which is P2.1.

## 2. Requirements coverage matrix

| Req / AC | Design element (§) | Code location (planned) | Test | Status |
|---|---|---|---|---|
| R1.1 one command, file loads and builds | §3.2, §5.1 | `recording/session.py` | T-1 | planned |
| R1.2 own process per setup | §3.2 | `scripts/record_all_setups.py` | T-14 | planned |
| R1.3 `record` CLI, `--out` defaults to `energy_systems/` | §5.1 | `hisim/cli.py` | T-13 | planned |
| R2.1 every component once, registration order | §3.2, §3.3 | `recording/builder.py` | T-2 | planned |
| R2.2 preset + sparse diff, else full block | §3.4 | `recording/configs.py` | T-3, T-4 | planned |
| R2.3 bare / explicit / feed `inputs` | §3.5 | `recording/inputs.py` | T-5, T-6 | planned |
| R2.4 no `AUTO`, no `sizing_sources`, no `groups` | §3.4 | `recording/configs.py`, `builder.py` | T-3 | planned |
| R2.5 canonical style | §3.6 | reuses `energy_system/emitter.py` | T-7 | planned |
| R3.1 golden KPI parity | §3.9 | `scripts/golden_check.py` (unchanged) + `golden-yaml-check.yml` | T-15 | planned |
| R3.2 record re-executes | §3.6 | reuses `record.verify_rerun` | T-8 | planned |
| R3.3 wire set equality | §3.9 | `energy_system/parity.py` (port) | T-9 | planned |
| R4 freshness, deterministic | §3.6 | `scripts/record_all_setups.py --check`, `energy-system-freshness.yml` | T-10, T-14 | planned |
| R5.1–R5.4 every setup, no skip list | §3.2, §10 PR-1 | removal commit; `record_all_setups.py` has no exclusions | T-14 | planned |
| R6 base file per heating system | §3.2 | driver's default probe | T-14 | planned |
| R7 flat data-dependent counts | §3.3 | `builder.py` (names are observed, never generated) | T-2 | planned |
| R8.1–R8.6 parameters emitted only when new, deduplicated | §3.7 | `recording/parameters.py` | T-11, T-12 | planned |
| R9 files in `energy_systems/` | §5.3 | `hisim/cli.py`, driver | T-13 | planned |
| R10.1–R10.7 grouping pass | §3.8 | `recording/grouping.py`, `workbook.py` | T-16 … T-19 | planned, needs P2.1 |
| R11.1–R11.8 parity rig | §3.9 | `scripts/p3_parity_check.py`, `.github/workflows/p3-parity.yml` | T-20, T-21 | planned |
| C-P3.2 port names differ | §3.9 | `parity.PortRenaming` | T-9 | **upgraded**, DQ5 |
| C-P3.6 nothing inferred | §3.8 | `grouping.py` refuses an unassigned `≠` row | T-17 | planned |
| C-P3.7 probe list bounds the claim | §3.8 | grouping report | T-19 | planned |
| AC-P3.1 … AC-P3.24 | §9 | — | T-1 … T-21 | planned |

## 3. Design overview

### 3.1 Concepts

**Observation** — reading a live `Simulator` after `prepare_calculation()` and `connect_all_components()` into plain data. **Building** — turning that data into an `EnergySystemFile`. **Emission** — the P2 writer. The three stay separate because only the first touches HiSim's runtime objects and only the last touches YAML; the middle one is pure and is where every test lives.

The recorder never parses Python. It observes what a setup built, which is why group membership, the original preset of an unconverted class and the question of whether a number was sized or typed cannot be recovered (requirements §4) — and why the grouping pass exists as a separate, human-driven step rather than as recorder cleverness.

### 3.2 The recording pass (R1, R2.1, R5)

```
record(setup_module, parameters, out_dir)
  ├── load the module, call setup_function(sim, parameters)     # constructors run: A1
  ├── sim.prepare_calculation(); sim.connect_all_components()   # wires resolve
  ├── observe(sim)      -> RecordedSystem                       # recording/observe.py
  ├── build(recorded)   -> EnergySystemFile                     # recording/builder.py
  ├── validate + build through the executor                     # R1.1: the file must work
  └── emit(file, out_dir)                                       # energy_system/emitter.py
```

`RecordedSystem` is a frozen dataclass: components in registration order, each with runtime name, class path, live config object, the wrapper's `connect_automatically` flag, and — for a `DynamicComponent` — its `my_component_inputs` / `my_component_outputs`. Plus the `WiringSnapshot` (§3.9) and the effective `SimulationParameters`.

Each setup is recorded in its own subprocess (R1.2), as `scripts/regenerate_scenario_jsons.py` does today, because setups mutate module state, singletons and `HISIM_LOCAL_LPG_CALC_INDEX`.

**No skip list** (R5.4). The driver records every `system_setups/*.py` and fails naming any that cannot be recorded. PR-1 removes the three that cannot (R5.2) so this rule can hold from the first run.

### 3.3 Names, keys and identity (R2.1, R7) `[decision needed: DQ2]`

v3 keys must match `NameRules.IDENTIFIER_PATTERN` = `^[A-Za-z_][A-Za-z0-9_]*$`. Of the 51 distinct runtime component names observed across the fleet, **four do not**:

| Name | Setup | |
|---|---|---|
| `Random numbers 100-200` | `simple_system_setup_one`, `_two` | spaces, hyphen |
| `Random numbers 10-20` | `simple_system_setup_one`, `_two` | spaces, hyphen |
| `Example Transformer default` | `simple_system_setup_two` | spaces |
| `Standard transformer and rectifier unit` | `electrolyzer_with_renewables` | spaces |

None of those setups is removed by R5.2, so the recorder meets them. `[decided 2026-08-28]` **They are renamed at the source** (PR-2), because the runtime component name is also the result-column prefix: sanitizing inside the recorder would make the recorded run's CSV columns differ from the Python run's, which is precisely what the parity rig compares (§3.9). Renaming keeps key, runtime name and column identical, and that is the invariant everything else rests on (I-3).

What PR-2 touches, measured: five name literals across `simple_system_setup_one.py`, `_two.py` and `electrolyzer_with_renewables.py`; their three `.scenario.json` twins, regenerated by `scripts/regenerate_scenario_jsons.py` and gated by `scenario-json-freshness.yml`; and `hisim/components/example_transformer.py:55`, where `ComponentID(name="Example Transformer default")` is the component's **own class-level default**, plus two docstring mentions. No golden reference is affected — none of the three setups is in `golden_config.json`.

That class-level default is the argument for the second half of the decision: the recorder refuses a name it cannot write (EF-R1), but a defaulted identity is one nobody has to type, so a recorder-side guard would never see it until something recorded that component. `[decided 2026-08-28]` `Component.__init__` therefore enforces `NameRules.IDENTIFIER_PATTERN` as well, in its own commit after PR-2, so an illegal name cannot be introduced at all.

`component_id` is not written (P2 R1.3); the executor rebuilds it from the key. `ComponentID.key` joins `building`, `unit` and `name` with underscores and is never parsed back, so the rebuilt identity has `building=None` and a name equal to the old key. **In P3 this is exact** — no setup in `system_setups/` sets a building name, verified 2026-08-28 — but `electricity_meter.py:201` branches on `DistrictNames.is_district(config.component_id.building)`, and `kpi_preparation.py`, `postprocessing_main.py` and the opex/capex calculation read `building_label`. A district file recorded this way would change behaviour silently. Recorded as **I-4** and **DQ3**; the recorder asserts `building is None and unit is None` for every component and refuses otherwise, so the hazard cannot arrive unnoticed.

### 3.4 Configs: preset plus sparse diff, or a literal block (R2.2, R2.4)

Ported from `json_v2:templating.py`, **halved**. The spike had two jobs: de-resolving sized values back to `"AUTO"`, and emitting `preset:` plus a sparse override block. R2.4 forbids `AUTO` in a recorded file, so only the second half ports.

```
preset_provenance(config)  ->  None            -> full block via ConfigBlockWriter.block()
                           ->  "<builder name>" -> preset: <name>
                                                   config: diff(config, fresh_build(name))
```

`fresh_build` calls the `ConfigBuilder` from `presets_of(type(config))` / `constructors_of(...)` with the same arguments the provenance stamp records; the diff is field-wise on the encoded form, so a field equal after encoding produces no line and an entry whose diff is empty drops the `config` key entirely. Rejected, per E1: guessing a preset by value-matching a config against every preset of its class.

Both branches encode through the existing `ConfigBlockWriter` (`record.py:82`), so `${var}` paths, enums-by-name and the `component_id` omission are inherited rather than reimplemented.

### 3.5 Wires become consumer-side `inputs` (R2.3)

Three shapes, decided per source in this order:

1. **Bare `- source`** — the set of wires from that source to this component equals exactly the target's default connections for that source. The comparison is `Component.get_default_connections(source)` against the observed wires, which is `json_generator.compare_automatic_connections` (`:327`) transposed to the v3 spelling; the wrapper's `connect_automatically` flag alone is not sufficient, since a setup may add explicit wires on top.
2. **Aggregator feed** — the target is a `DynamicComponent` and the wire arrives through `my_component_inputs`; written `{from, tags, weight}`, with `component_type` when the entry carries one and `dispatch: {…}` when a matching dispatch output exists in `my_component_outputs`. The derived port names (`{source_output}From{source}`, `DispatchTo{source}_{input}`) are recomputed and matched, never written.
3. **Explicit `{input, from}`** — everything else.

Nothing is ever written at the source (P2 R2.1).

### 3.6 Determinism (R4, R2.5, R3.2)

R4 demands byte-identical output across machines, which makes three things requirements:

- **Mapping order** is registration order for components and declaration order for fields; no `set` iteration reaches the output.
- **Floats** are written with `repr`, which round-trips exactly in Python 3, never with a format string. A recorded value must satisfy `float(text) == value`.
- **Paths** are re-symbolised to `${…}` through the existing `PathResolver`, covering the cases the v1 recorder already handles: weather `source_path`, UTSP result paths and `Car.household_name`. An absolute path surviving into the output is an error (EF-R3), not a warning, because it would break the freshness check on the next machine.

Re-execution of the recording is checked with the existing `record.verify_rerun`, so R3.2 costs no new code.

### 3.7 Simulation parameters: emit only what is new (R8)

```
effective = normalise(sim.simulation_parameters)
match = first f in energy_systems/*.simulation.yaml (then: files written earlier this run)
        with normalise(f) == effective
match ? reference it, write nothing : write one new file
```

`normalise` keeps start, end, `seconds_per_timestep`, the post-processing options as a **sorted set**, `logging_level`, `country` and `year`. It drops `cache_dir_path` — 11 setups set it behind an `os.path.exists` probe for the cluster, and keeping it would both defeat the sharing and break R4's cross-machine identity. The dropped field is also absent from what is written. `json_generator.get_filtered_simulation_parameters` (`:215`) is the precedent for the filtering, not for the deduplication, which is new.

A new file is named for its content — horizon, resolution, the purpose of its option set — never for the setup that first needed it (R8.5). The freshness job asserts no two files in `energy_systems/` normalise equal (R8.6), so a duplicate cannot be added by hand later either.

### 3.8 The grouping pass (R10) `[depends on P2.1]`

Two passes with a human in the middle.

```
probe list (authored) ─► record each probe flat ─► diff vs baseline ─► workbook (2 sheets)
                                                                          │ a person assigns
                                                                          ▼
grouped file ◄── second record pass ◄── grouping import ◄── <stem>.grouping.yaml
```

The `components` sheet carries one row per component in the union of all probes, with one column per probe holding `—` (absent), `=` (present, identical) or `≠` (present, config or wiring differs), plus the person's `assignment` and `note`. The three-state cell is the load-bearing part: a component that is present in every probe but wired differently — the meter with and without an EMS — is invisible to a presence matrix, and it is exactly the case that forces P2.1's variants. The `configurations` sheet maps each probe column to the group flags and variant selections it stands for.

`assignment` is one of empty, `group:<name>`, `variant:<name>/<option>`, or `override`. `override` exists so that a continuously varying value — the PV share is the standing example — is not mistaken for structure; without it the tool would push every knob into a group. Nothing is ever assigned automatically (C-P3.6), and a `≠` row with no assignment is an error naming the row.

The workbook is a scratch artefact; `grouping import` normalises it to `<stem>.grouping.yaml`, which is what is committed and reviewed (R10.4). `openpyxl` is already a dependency. Every probe column is then an assertion: `realize(grouped, selections(C)) == flat recording of C`, byte for byte, with the baseline column checking the grouped file against the R6 base file — so the pass is fully verified from recordings that already exist (R10.6).

### 3.9 The parity rig (R11) `[upgrade to R11.3: DQ5]`

Port `json_v2:parity.py` nearly whole into `hisim/energy_system/parity.py`:

| From the spike | Serves |
|---|---|
| `WiringSnapshot.from_simulator` / `.from_components` | R3.3, R11.3 first comparison — and it is the observation half of §3.2 |
| `PortRenaming.apply_to` / `.apply_to_results` | C-P3.2 |
| `WiringDiff.describe` | the rig's failure output |
| `WiringParityHarness.compare_simulators` | R3.3 |
| `ResultComparison.between` | R11.3 second comparison |

**The upgrade** `[decided 2026-08-28; R11.3 and C-P3.2 amended]`. The requirements first treated the aggregator port-naming divergence as a wall: compare only the columns whose names exist on both paths. `PortRenaming` is a solved translation of exactly that divergence — v1's `Input_<source>_<field>_<n>` against the declarative derived names — and `apply_to_results` rewrites the columns of a result frame through the same table that rewrites a wiring snapshot. So the rig compares **every** column, anything the table does not list must still match literally, and a translation landing on a column the frame already carries raises rather than overwriting. This does not disturb Q-P3.2: the permanent golden gate stays on KPIs; only the temporary rig gets stricter. The table is **hand-authored per aggregator** (DQ4) and reviewed with the rig, so "these two names mean the same wire" stays a claim someone made rather than an assumption the tooling reached.

The comparison runs at **exact equality**, not `rel_tol = 1e-9`: both runs happen in one container, where determinism is byte-exact (`golden_ref_spec.md` §7), and the tolerance in the permanent gate exists only to absorb cross-machine drift (R11.2).

## 4. Alternatives considered

| Alternative | Why not |
|---|---|
| Translate Python setups to YAML by parsing them | The setups compute; a parser would have to interpret `read_in_configs`, loops and `parameters["Group"]` branches. Observation gets the answer by running the code that already knows it. |
| Record before `connect_all_components()` | Default connections would be unresolved, so R2.3's bare/explicit decision could not be made. |
| Infer groups by clustering the probe diffs | The PV-share case proves a difference can be a value rather than a structure; the tool cannot tell which, and guessing wrong writes a knob into the file shape (C-P3.6). |
| Sanitize illegal component names in the recorder | Breaks the identity of runtime name and result column, which the parity rig compares (§3.3). |
| Add the 13 unoracled setups to `golden_config.json` | Seven of them crash in the KPI layer; blessing needs repairs first, and a reference locks in behaviour that P4 will deliberately change. The rig needs no references at all. |
| Emit one `<stem>.simulation.yaml` per setup | 21 near-identical files; R8's deduplication produces a handful instead. |

## 5. Public surface

### 5.1 CLI

```
hisim energy-system record <setup.py> <simulation.yaml|json> [--out DIR] [--grouping FILE]
hisim energy-system grouping probe  <setup.py> <probes.yaml> [--out workbook.xlsx]
hisim energy-system grouping import <workbook.xlsx> [--out <stem>.grouping.yaml]
```

`--out` defaults to `energy_systems/` (R1.3, Q-P3.4).

### 5.2 APIs (`hisim.energy_system.recording`)

```python
observe(simulator: Simulator) -> RecordedSystem
build(recorded: RecordedSystem) -> EnergySystemFile
record_setup(module: Path, parameters: SimulationParameters, out: Path) -> RecordingResult
normalise_parameters(parameters: SimulationParameters) -> Mapping[str, Any]
probe(module: Path, probes: Sequence[ProbeConfiguration]) -> ProbeMatrix
apply_grouping(flat: EnergySystemFile, grouping: Grouping) -> EnergySystemFile
```

### 5.3 File naming

`energy_systems/<setup stem>.energy_system.yaml` · `energy_systems/<content name>.simulation.yaml` · `energy_systems/<setup stem>.grouping.yaml`. Workbooks are not committed.

### 5.4 Errors (`EF-R` range, message names both ends and the alternatives)

| Code | Raised when |
|---|---|
| EF-R1 | a component name is not a v3 identifier — names the component, the rule and the setup |
| EF-R2 | a component carries a `building` or `unit` identity (I-4) |
| EF-R3 | an absolute path survived re-symbolisation — names the field and the path |
| EF-R4 | a preset's provenance names a builder its class no longer has |
| EF-R5 | the recorded file fails to load, validate or build |
| EF-R6 | a `≠` row carries no assignment — names the row and the probe columns that differ |
| EF-R7 | `selected` in the `configurations` sheet names an option no assignment created |
| EF-R8 | two parameter files normalise equal |

## 6. Internal structure

`[decided 2026-08-28: DQ1]` New subpackage `hisim/energy_system/recording/`, with `parity.py` staying flat because the rig and the tests use it independently of the recorder; ≤ 500 lines per module.

| Module | Responsibility | Origin | ~lines |
|---|---|---|---|
| `observe.py` | `Simulator` → `RecordedSystem`; dynamic feeds and dispatch outputs | new, uses `parity.WiringSnapshot` | 250 |
| `builder.py` | `RecordedSystem` → `EnergySystemFile` | **new** | 350 |
| `names.py` | key validation, the identifier rule, EF-R1/EF-R2 | new | 90 |
| `configs.py` | preset provenance, fresh build, sparse diff | port of `json_v2:templating.py` minus `AUTO` | 200 |
| `inputs.py` | wires → bare / feed / explicit items | new + `json_generator.py:304-485` logic | 300 |
| `parameters.py` | normalisation, matching, deduplication (R8) | new | 180 |
| `grouping.py` | probe matrix, assignments, second pass (R10) | **new**, needs P2.1 | 400 |
| `workbook.py` | xlsx write/read via `openpyxl` | new | 220 |
| `hisim/energy_system/parity.py` | snapshot, renaming, diff, result comparison | **port as-is** (500) | 500 |
| `scripts/record_all_setups.py` | per-setup subprocess driver, `--check` for freshness | new + `regenerate_scenario_jsons.py` | 200 |
| `scripts/p3_parity_check.py` | one triple: both paths, three comparisons | new | 250 |

Dependency direction: `names ← observe ← builder ← {configs, inputs, parameters} ← grouping`; `parity` is a leaf shared with the rig.

## 7. Data, state and invariants

- **I-1** The recorder never writes at the source: no model field it produces describes an outgoing connection. *Checked:* T-5.
- **I-2** A recorded file contains no `AUTO`, no `sizing_sources`, no `groups`, no `variants`. *Checked:* T-3, reusing `record.assert_fully_concrete`.
- **I-3** Runtime component name, v3 key and result-column prefix are the same string, for every component. *Checked:* T-2 and the rig's column comparison.
- **I-4** Every recorded component has `component_id.building is None and .unit is None`; otherwise EF-R2. *Checked:* T-2. See DQ3.
- **I-5** `record(record(setup))` is byte-identical, and so is a recording taken on another machine. *Checked:* T-10.
- **I-6** `observe` is read-only: no attribute of any component, wrapper or simulator is modified. *Checked:* review plus T-1 comparing a KPI run before and after observation.

## 8. Error handling

Every failure is an exception carrying the setup, the component and the rule; nothing is skipped and nothing degrades to a warning. The driver collects failures across setups and reports them together, so one broken setup does not hide the next — but it exits non-zero, because R5.4 makes an unrecordable setup a defect rather than an exception.

## 9. Testing strategy

| ID | Test |
|---|---|
| T-1 | `observe` on a built simulator changes nothing; a KPI run before and after is identical |
| T-2 | components, order, names and identities of a recorded file equal the Python run's |
| T-3 | a recorded file has no `AUTO`/`sizing_sources`/`groups`/`variants`; converted classes appear as `preset:` + sparse block, unconverted as full blocks (AC-P3.5) |
| T-4 | a preset whose fresh build equals the live config emits no `config` key at all |
| T-5 | bare `inputs` only where the wire set equals the defaults; removing one default wire turns the item explicit on re-record (AC-P3.6) |
| T-6 | an aggregator feed round-trips tags, weight, `component_type` and `dispatch`, with the derived port names recomputed |
| T-7 | `dump(load(f)) == f` for every recorded file (AC-P3.7) |
| T-8 | the recording's own realized record re-executes byte-identically (AC-P3.3) |
| T-9 | wire sets equal between the two paths, through `PortRenaming` |
| T-10 | recording twice is byte-identical; a one-field change to a setup produces exactly the expected diff (AC-P3.4) |
| T-11 | parameters matching a shipped file emit nothing; one changed option emits exactly one file (AC-P3.21) |
| T-12 | two setups with identical new parameters share one file; two differing only in `cache_dir_path` also share one (AC-P3.22, AC-P3.23) |
| T-13 | the CLI writes into `energy_systems/` by default |
| T-14 | the driver records every setup with no skip list and fails naming any it cannot (AC-P3.1, AC-P3.11) |
| T-15 | `golden_check.py` passes for all 8 golden setups run from recorded files (AC-P3.2) |
| T-16 | the probe matrix marks absent / identical / differing correctly against a known fork |
| T-17 | a `≠` row without an assignment is rejected naming the row (AC-P3.14) |
| T-18 | workbook → yaml → workbook is stable; the yaml is committed, the workbook is not (AC-P3.15) |
| T-19 | every probe column equals its flat recording byte for byte; the report names untested combinations (AC-P3.13, AC-P3.16) |
| T-20 | a deliberately altered recorded file fails its triple, naming what moved (AC-P3.18) |
| T-21 | the seven KPI-broken setups return a structural verdict, not an error (AC-P3.19) |

## 10. Migration, compatibility and rollout

`[ordering decided 2026-08-28]` **P2.1 lands before P3 starts.** Only PR-6 needs variants, but the format is cheaper to settle while three mockups and one real file exist than after twenty-one recorded files do, and it answers the RenoVisor requirement in code rather than on paper.

| PR | Content | Depends on |
|---|---|---|
| **P2.1** | Exclusive variants: model, loader, schema, selection in the expander, the five rejections, identity test per option, `facts` | — |
| PR-1 | **Removal** (R5.2): 3 setups, 2 v1 twins, 2 tests, the freshness `--exclude` | — |
| PR-2 | **Names** (§3.3): rename the 5 literals in 3 setups and the `ExampleTransformer` class default; regenerate 3 twins. Second commit: `Component.__init__` enforces the identifier rule | — |
| PR-3 | Port `parity.py`; `observe` + `build` + `record` CLI; 3 setups recorded as fixtures | PR-2 |
| PR-4 | All setups recorded; parameter deduplication (R8); freshness workflow | PR-3 |
| PR-5 | `one_week_july`; the parity rig with its renaming tables and workflow (R11) | PR-4 |
| PR-6 | Grouping pass (R10) | PR-4, P2.1 |
| PR-7 | Teardown: delete the rig, its config and its scripts (R11.8, AC-P3.20) | PR-6 |

PR-1 and PR-2 are the only ones that change a setup, a component or a result, and each is its own commit with its diff shown. PR-2's result change is confined to the column prefixes of three setups, none of them golden-gated. The two toy setups are renamed rather than deleted `[decided 2026-08-28]`: they stay as the documented example of legacy Python mode, and permanently outside the numeric oracle.

Not part of P3 `[decided 2026-08-28]`: the KPI-layer repair list — four components with no KPI method and three bugs, including `household_gas_solar_thermal` reporting more grid import than total consumption. The rig covers those setups structurally (R11.4), so P3 needs none of it, and each setup joins the numeric oracle as it is fixed.

## 11. Risks and unknowns

- **The grouping pass is blocked on P2.1.** PR-6 cannot land before variants exist. Everything before it can.
- **`PortRenaming`'s table is hand-authored per aggregator.** Deriving it from the resolver's naming templates would be better but couples the recorder to the legacy add-API's insertion order; DQ4.
- **Probe runs cost construction, not simulation**, but construction loads weather and LPG profiles. 21 setups × n probes is the grouping pass's real cost; caching decides whether it is minutes or an hour.
- **Seven setups crash in KPI computation.** The rig's structural comparison covers them (R11.4), but they contribute no numeric evidence until the repair list is worked.
- **The spike is six weeks old.** `parity.py` and `templating.py` were written against the v2 schema and `hisim.sizing`; the ports must be re-read against P1's `hisim.config`, not copied.

## 12. Code-review guide

Read in this order: §3.3 (names — the one place a reviewer's decision changes the code), `recording/builder.py` against §3.2, `recording/inputs.py` against §3.5 and `json_generator.compare_automatic_connections`, then the ports (`configs.py` against `json_v2:templating.py`, `parity.py` against its original) checking what was **dropped** rather than what was kept. Finally §3.7's normalisation, where the omissions matter more than the inclusions.

## 13. Design questions — all answered 2026-08-28

**DQ1 — subpackage or flat modules?** `[answered]` Subpackage `hisim/energy_system/recording/`; `parity.py` stays flat, since the rig and the tests use it independently of the recorder.

**DQ2 — enforce the identifier rule in `Component.__init__`?** `[answered]` Yes, in its own commit after PR-2. The deciding evidence is that one of the four illegal names is a component's own class default (`example_transformer.py:55`), which a recorder-side guard would not see until something recorded that component.

**DQ3 — `component_id` and the lossy key.** `[answered]` Guard in P3, decide in P5. The recorder refuses any component carrying a `building` or `unit` (I-4, EF-R2), so P3 is provably exact and P5 must answer the question before district files exist rather than discovering it in a meter branch.

**DQ4 — where does the `PortRenaming` table come from?** `[answered]` Hand-authored per aggregator and reviewed with the rig. Deriving it from the resolver's templates would couple the recorder to the legacy add-API's insertion order, which is what the migration is leaving behind, and the rig is temporary.

**DQ5 — compare all result columns through the table?** `[answered]` Yes; R11.3 and C-P3.2 are amended. Every column is compared, anything the table does not list must match literally, and a translation landing on an existing column raises. Q-P3.2's answer for the permanent gate is untouched.

## 14. Glossary

See the requirements §12. Spec-specific: **observation** — reading a live simulator into plain data; **probe matrix** — the three-state table of component × configuration; **triple** — one (setup, configuration, window) unit of the parity rig.
