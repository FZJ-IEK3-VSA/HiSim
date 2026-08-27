# P2 — Energy-system file format and executor: implementation specification

**Status:** draft · **Date:** 2026-08-26
**Implements:** `roadmap/declarative_energy_systems/p2_file_format_requirements.md` (revision 2026-08-25, in review) under the epic `roadmap/declarative_energy_systems/epic.md` (E1–E8) · **Plan:** `plan.md` §P2
**Author(s):** assistant (every item `[proposed]` unless tagged) · **Reviewers:** HiSim core team
**Branch / PRs:** `energy_system_files` (P1 kernel already on it); planned PR-1 … PR-6, §10
**Base evidence:** current branch, 2026-08-26 — `hisim/config/{engine,presets,introspection,sizing,report}.py` (500/198/266/487/173), `hisim/json_executor.py` (309), `hisim/hisim_main.py` (575), 25 `system_setups/*.py` with 23 `*.scenario.json` twins; `hisim/scenario_v2/` is **absent** here (a stale `__pycache__` only), so every port is a `git show json_v2:` copy
**Solution-design input:** `json_v2:system_docs/json_scenario_v2_spec.md` §3–§5 and §9 decisions 1–21; `json_v2:roadmap/design_review_questions.md` A1–A4, B5; `json_v2:hisim/scenario_v2/*` (schema 1449, executor 1126, resolution 742, audit 507, channels 426, serialization 372, path_resolver 345, templating 222, parity 500, errors 123, context 100)

**What a reviewer must decide here:**
1. §3.2 / §5.2 — the consumer-side `inputs` item grammar and its collapse of the v2 four shapes to three (field group dropped).
2. §3.3 / §6 — the new package `hisim/energy_system/` and which json_v2 modules port, rewrite, die.
3. §3.4 — the two-level validator (structural vs. class-bound) and, with it, **which mockups actually execute in P2** (§9, D2/D3).
4. §5.5 / §8 — the `EF-` error catalogue and the rule that every message names both ends and the alternatives.
5. §3.6 / §7 — realized-record identity: the metadata carry-forward rule that makes AC-P2.4 byte-exact, and the non-circular identity test (§9 T-9).
6. §13 DQ1 — where named constructors are *declared* (R3.8 assumes a mechanism P1 did not ship).

---

## 1. Summary

P2 turns the three mockups into a loadable format and an executor. The central design idea: **a component entry is the only place anything about that component is written** — its class, its configuration (preset or constructor plus sparse overrides), where its inputs come from, and where its sizing facts come from — so the file has no central connections block and no source-side declaration at all (R2.1, R11). The parser, the static validator, the channel matcher, the dynamic-connection resolver, the `${var}` resolver and the config codec are ported from the `json_v2` spike; the grouped-by-source connection spelling (v2 decision 20), the `sizing_facts` seed block, the entry-level `component_id` and the parity harness are dropped. New in P2: YAML as a first-class input, group expansion with the off rule (R14), the `sizing_sources` → `resolve_all(sources=)` bridge (R4.3), the realized record as commented YAML (R8, Q8), the JSON Schema export and the `hisim config` CLI (R13). The two non-obvious places are §3.4 — validation splits into a class-independent level the mockups pass today and a class-bound level that only unlocks as P4 converts classes — and §3.6, where a byte-exact re-execution guarantee forces a rule about what metadata a record may carry. Two requirements cannot be met as written and are raised in §13: AC-P2.1 (nothing executes end to end on four converted classes) and AC-P2.9/RQ3 (resolution *must* read TABULA).

## 2. Requirements coverage matrix

| Req / AC | Design element (§) | Code location (planned) | Test | Status |
|---|---|---|---|---|
| R1.1 name = key = identity | §3.1, §5.1 | `energy_system/model.py:ComponentEntry` | T-1 | planned |
| R1.2 class may repeat | §3.1 (no class-keyed state) | `model.py` | T-1 | planned |
| R1.3 no `component_id` in a file | §3.5 I-1, EF-18 | `model.py`, `executor.py:_instantiate` | T-1, T-11 | planned |
| R1.4 key order, preset xor constructor | §5.1, EF-11/12/18 | `model.py`, `validation.py` | T-1, T-8, T-11 | planned |
| R2.1 consumer-side only | §3.2 I-2 | `model.py` (no source-side field) | T-2 | planned |
| R2.2 three item shapes | §3.2, §5.2 | `model.py`, `wiring.py`, `channels.py` | T-2, T-3 | planned |
| R2.3 unknown/duplicate/mixed → error | §8 EF-20…EF-30 | `validation.py`, `wiring.py` | T-11 | planned |
| R2.4 unconsumed source = warning | §3.4 step 6, `report.unconsumed` | `executor.py`, `cli.py` | T-12 | planned |
| R3.1 preset + sparse config | §3.3 port of v2 dec. 21 | `executor.py:_load_config` | T-1 | planned |
| R3.2 any field, incl. `AUTO` | §5.1 `AUTO` spelling | `serialization.py`, `hisim/config/sizing.py` | T-1 | planned |
| R3.6 unknown field/preset/type | §8 EF-13/16/17 | `validation.py` | T-11 | planned |
| R3.7 `Sizable[Enum]` codec | §3.7 | `hisim/config/sizing.py:sized_field` | T-13 | planned |
| R3.8 `constructor:` form | §3.3, §5.1, §13 DQ1, D4 | `model.py`, `executor.py`, `hisim/config/introspection.py` | T-8 | planned |
| R4.3 `sizing_sources`, enabled set | §3.4 step 5, §5.3 | `executor.py:_resolve` → `config.engine.resolve_all` | T-4, T-5 | planned |
| R4.4 scalar and list, no `all` | §5.1, §3.4 | `model.py`, `groups.py` | T-4, T-5 | planned |
| R4.8 no wildcards / relative refs | §8 EF-06 | `validation.py` | T-11 | planned |
| R6 simulation params separate | §5.4, §10 | `hisim/hisim_main.py` | T-14 | planned |
| R7 hard errors, no repair | §8 whole catalogue | `errors.py` + all validators | T-11 | planned |
| R8.1 realized record | §3.6, §7 I-7 | `energy_system/record.py` | T-9, T-10 | planned |
| R8.2 audit companion | §3.6 | `record.py:AuditWriter` | T-10 | planned |
| R8.3 YAML-comment provenance (Q8) | §3.6 `[proposed; Q8]` | `record.py` (ruamel, write-only) | T-15 | planned |
| R11 canonical style | §5.1, RQ4 | `record.py:emit`, `loader.py` | T-7 | planned |
| R12 `${var}` paths, no absolutes | §3.3 port | `path_resolver.py`, EF-04/05 | T-11 | planned |
| R13.2 JSON Schema export | §3.8, §5.4 | `schema_export.py` | T-6 | planned |
| R13.3 `describe` / `facts` CLI | §3.8, §5.4 (Q-P2.4 → P2) | `hisim/cli.py` | T-6, T-12 | planned |
| R13.4 messages list alternatives | §8 | `errors.py` templates | T-11 | planned |
| R14.1 group shape, flat, one group | §3.4 step 2, EF-50…54 | `groups.py`, `validation.py` | T-11 | planned |
| R14.2 not namespaces | §3.4 (names global) | `groups.py` | T-11 | planned |
| R14.3 off rule | §3.4 step 2, §7 I-4 | `groups.py:expand` | T-5, T-9 | planned |
| R14.4 identity | §3.6, §9 T-9 | — | T-9 | planned |
| RQ2 `schema_version: 3` | §8 EF-01 | `loader.py` | T-11 | planned |
| RQ3 no I/O in load/validate/resolve | §3.9, §13 DQ2 | `loader.py`, `validation.py` | T-16 | **partial — see §13 DQ2** |
| RQ4 load → dump unchanged | §3.6, §7 I-10 | `record.py`, `loader.py` | T-7 | planned |
| C-P2.1 golden parity | §10 (P3 owns the suites) | — | T-14 | planned |
| C-P2.2 single-zone building | §3.1 (explicit values, no mechanism) | — | — | n/a by design |
| C-P2.3 aggregator semantics inherited | §3.2, §6 (port `channels.py`, `resolution.py`) | `channels.py`, `resolution.py` | T-3 | planned |
| C-P2.4 v1 keeps working | §10 (`json_executor.py` untouched) | `hisim_main.py` third mode | T-14 | planned |
| C-P2.5 groups = orthogonal add-ons | authoring guidance only, not coded | — | — | n/a by design |
| C-P2.6 UTSP `JsonReference` spelling | §10 PR-6 | fixtures + one local-LPG run | T-17 | planned |
| A1 v2 fixtures regenerated | §10 (v2 fixtures never land here) | — | — | n/a |
| A2 `yaml.safe_load` + dup hook | §3.3, §7 I-8 | `loader.py` | T-11 | planned |
| AC-P2.1 mockups load…run | §3.4, §9 T-1 | — | T-1 | **cannot be met as written — D2, §13 DQ3** |
| AC-P2.2 cliff errors | §9 T-4 | — | T-4 | planned |
| AC-P2.3 identity over (mockup, group) | §9 T-9 | — | T-9 | planned |
| AC-P2.4 re-execution | §3.6, §9 T-10 | — | T-10 | planned |
| AC-P2.5 one test per hard error | §9 T-11 | — | T-11 | planned |
| AC-P2.6 schema rejects, describe/facts | §9 T-6 | — | T-6 | planned |
| AC-P2.7 `dump(load(f)) == f` | §9 T-7 | — | T-7 | planned |
| AC-P2.8 abs path / dup key / version 2 | §9 T-11 | — | T-11 | planned |
| AC-P2.9 no I/O on UC3 | §9 T-16 | — | T-16 | **re-scoped — §13 DQ2** |
| AC-P2.10 unconsumed warning | §9 T-12 | — | T-12 | planned |
| AC-P2.11 group violations | §9 T-11 | — | T-11 | planned |
| AC-P2.12 `[*]` / relative rejected | §9 T-11 | — | T-11 | planned |
| AC-P2.13 comments not load-bearing | §9 T-15 | — | T-15 | planned (Q8) |
| AC-P2.14 enum member after load | §9 T-13 | — | T-13 | planned |
| AC-P2.15 constructors in UC2 | §9 T-8 | — | T-8 | **partial — no class declares one yet, §13 DQ1** |
| **D1** `[proposed]` two-level validation: structural (class-independent) vs. class-bound (presets, fields, ports, facts); a file may be structurally validated without importing any component | §3.4 | `validation.py` split | T-1, T-11 | from AC-P2.1 + P4 dependency |
| **D2** `[proposed]` a fourth, **executable** fixture restricted to what exists: presets for the four converted classes, full `config:` blocks (R1.4 "unless `config` is complete") for the rest | §9, §10 PR-3 | `tests/energy_systems/gas_boiler.energy_system.yaml` | T-1, T-10 | from AC-P2.1 |
| **D3** `[proposed]` per-mockup *expected class-bound error set*, asserted by a test, shrinking as P4 converts classes | §9 T-1b | `tests/test_energy_system_mockups.py` | T-1b | from AC-P2.1 |
| **D4** `[proposed]` config classes **declare** their named constructors (`@config_constructor`), and `ConfigDescription` grows `constructors: Tuple[ConstructorInfo, ...]`; without it R3.8's "listing the constructor's parameters" and R13.2's schema export have no data source | §3.3, §5.3, §13 DQ1 | `hisim/config/introspection.py`, `hisim/config/presets.py` | T-8 | from R3.8, R13.2 |
| **D5** `[proposed]` `hisim/cli.py` is created: `setup.py:36` already declares `hisim=hisim.cli:main` and the module does not exist, so the console script is broken today | §5.4 | `hisim/cli.py` | T-6 | from R13.3 |
| **D6** `[proposed]` `PyYAML` and `ruamel.yaml` are added to `requirements.txt`; neither is declared today (PyYAML is present only transitively, ruamel not at all — verified 2026-08-26) | §11 R-1 | `requirements.txt`, `setup.py` | T-18 | from A2, R8.3 |

## 3. Design overview

### 3.1 Concepts

- **Energy-system file** — one YAML document (YAML only, R15), `schema_version: 3`; suffix `*.energy_system.yaml`, directory `energy_systems/` (Q-P2.5c).
- **Component entry** — the key is the instance name and the whole identity (R1.1, R1.3); the value carries `class`, `preset` xor `constructor`, `config`, `inputs`, `sizing_sources` in that order (R1.4).
- **Input item** — one element of a consumer's `inputs`: bare source name, explicit wire, or aggregator feed (§3.2).
- **Group** — `{enabled, components}`: a set with a flag, never a namespace (R14.1/14.2). **Enabled set** — what survives expansion; the *only* set the binding rule sees (R14.3, Q-P2.3).
- **Realized record** — the file re-emitted with presets/constructors expanded, `AUTO` → numbers, `sizing_sources` always written, disabled groups absent (R8.1); its **audit companion** is the machine-readable twin (R8.2) the record's comments render (R8.3).

### 3.2 The `inputs` grammar and its mapping onto the v2 shapes (R2.1, R2.2, C-P2.3)

```
inputs:
  - weather                                     # (a) bare  → target's default connections for Weather
  - input: TemperatureInputPrimary              # (b) wire  → one explicit connect_input
    from: weather.DailyAverageOutsideTemperatures
  - from: battery.AcBatteryPowerUsed            # (c) feed  → aggregator participant
    component_type: BATTERY
    tags: [ELECTRICITY_CONSUMPTION_EMS_CONTROLLED]
    weight: 6
    dispatch: {}
```

Mapping to `json_v2:hisim/scenario_v2/schema.py`: (a) is `BareConnectionEntry` with `to` supplied by the owning key; (b) is `ExplicitWireEntry`, with `from` carrying the source port and `input` the target port (the v2 dotted `to` half moves to its own key, because the target is already known); (c) is `DynamicConnectionEntry` with `to` implied, `from` optionally dotted to name the source output (v2's separate `output` key is folded into the dotted `from`, matching the mockups). **The v2 field-group shape (`fields: [...]`, v2 §3.3c) is dropped** `[proposed]`: consumer-side items are already one wire each, so a group buys nothing but a second spelling. **The grouped-by-source spelling (v2 decision 20) is dropped** — R2.1 retires it outright.

Classification is total, by key presence: `input` ⇒ (b); any of `tags`/`weight`/`dispatch`/`component_type` ⇒ (c); a bare string ⇒ (a); anything else, a dotted bare string included, ⇒ EF-19. Downstream of classification the v2 machinery is unchanged (C-P2.3): most-specific-tag-subset channel matching (`json_v2:hisim/scenario_v2/channels.py:DynamicConnectionChannelMatcher`), `DispatchRule.{FORBIDDEN,OPTIONAL,REQUIRED}`, weight 999 reserved for monitored-only, `(source, output)` unique per aggregator, and v2 §4.6's frozen port names (`{source_output}From{source_id}`, `DispatchTo{source_id}_{target_input}`, `DispatchFor{source_id}_{source_output}`). The **don't-mix rule** (v2 §3.4) is per (consumer, source) pair: at most one bare item, plus *either* explicit wires *or* feeds, never both (R2.2).

### 3.3 What is configured, and how it is read (R3.1, R3.8, R12, A2)

An entry's config has exactly one of three origins, then sparse overrides: `preset: <name>` → `Cls.presets.<name>(key)` (`hisim/config/presets.py:Preset.__call__`); `constructor: {<name>: {<arg>: <value>}}` → `Cls.<name>(key, **args)` (R3.8, D4); or a complete `config:` block (R1.4). Overrides decode through the ported `serialization.py:ScenarioConfigCodec` — enums by member name, `"AUTO"` via the `sized_field` codec, `${var}` via `path_resolver.py`.

`loader.py` accepts `.yaml`/`.yml` only (R15: a `.json` path is EF-00 unsupported format), uses `yaml.safe_load` with a duplicate-key-rejecting constructor; the ported JSON `object_pairs_hook` is dropped.

### 3.4 Executor lifecycle

```
  *.energy_system.yaml  +  *.simulation.yaml (existing *.simulation.json still accepted for the parameters file)
      │
  1 LOAD      yaml.safe_load only, duplicate-key hook, schema_version (RQ2, R15, EF-00/01/02)
      ▼       → EnergySystemFile (pydantic), key order preserved
  2 GROUPS    expand(): drop disabled components, every `inputs` item naming one, every
      │       sizing reference — scalar dangling = EF-42, list shrinks (may reach []) and the
      ▼       shrink is recorded → the ENABLED SET + an ExpansionRecord (R14.3)
  3 VALIDATE-STRUCTURAL   names, group rules, entry keys ⊆ R1.4, preset xor constructor,
      ▼       item shapes, don't-mix, closed reference graph, no `[*]`/relative/absolute (D1)
  4 VALIDATE-CLASS-BOUND  import classes; preset/constructor/field/type (EF-10…18); ports
      ▼       exist; channels match; load types and units agree (D1 — needs the class)
  5 CONFIGURE preset(key) / constructor(key, …) → sparse overrides → configs named by key
      ▼
  6 RESOLVE   config.engine.resolve_all(configs, seed=None, sources=<file's sizing_sources>);
      ▼       P1 E-01…E-08 re-raised as EF-4x naming the entry; report.unconsumed → warn (R2.4)
  7 BUILD     Component.build_from_scenario(config, sim_params, ctx), file order
      ▼
  8 WIRE      bare→defaults, explicit wires, feeds→channels→resolved dynamic connections;
      ▼       final validation (duplicate feed, mandatory inputs), register with Simulator
  9 RECORD    realized.energy_system.yaml (+comments), realized.audit.yaml, component_connections.json
      ▼
 10 SIMULATE  Simulator.run_all_timesteps() → PostProcessor   (unchanged)
```

Steps 3–6 reorder v2, which built components before sizing (`json_v2:hisim/scenario_v2/executor.py:build_components`); sizing now completes before the first component exists, which lets a construction-only run still write a record and keeps R7's "hard error before any component is built" true.

**Boundaries.** `hisim/energy_system/` depends on `hisim.config`, `hisim.component`/`hisim.dynamic_component`, `hisim.simulator`, `hisim.loadtypes`, `hisim.log`, `hisim.utils`; `hisim/config/` gains **no** dependency on it (epic C4). `hisim/json_executor.py` is untouched (C-P2.4); `hisim/hisim_main.py` gains a third mode.

### 3.6 The realized record and the identity guarantees (R8, R14.4, RQ4)

Same format, canonical style (R11), one emitter shared with `dump()` so `dump(load(f)) == f` holds (RQ4, I-10). Rules: presets and constructors expanded to a full `config:` (R8.1, R3.8); every `AUTO` a number; `sizing_sources` written for *every* consumer that read a fact, even where the file left it implicit (R8.1 — this is what makes a record size nothing on re-execution); enabled groups kept, disabled groups and their components absent (R14.3). Provenance comments `[proposed; Q8 (a)]` come from `ruamel.yaml` (`CommentedMap`, end-of-line) rendering `sizing_record` and `preset_provenance()`; the read path never sees ruamel (I-8):

```yaml
# Generated by HiSim 1.4.0 — do not hand-edit; re-run to regenerate
  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    config:                                        # built from preset: condensing_gas
      energy_carrier: GAS
      maximal_thermal_power_in_watt: 8600.0        # sized from building.heating_load_in_watt = 8600.0
      minimal_thermal_power_in_watt: 716.7         # sized from self.maximal_thermal_power_in_watt (× 1/12)
    sizing_sources:
      heating_load_in_watt: building.heating_load_in_watt
```

**Byte-exact re-execution (AC-P2.4)** would otherwise break on two things — `metadata.source_energy_system_file` and the sizing comments. Rules `[proposed]`: (i) `source_energy_system_file` names the **originally authored** file and is carried forward verbatim; (ii) `hisim_version`/`git_commit` are per run and must match within the pair (a version change *is* a genuine difference); (iii) a re-executed record sizes nothing, hence carries no sizing comments, so record₂ == record₁ from the second run on. The test compares run₂'s record with run₁'s.

**Group identity (R14.4, AC-P2.3)** is `realize(X disabled) == realize(X deleted by hand)`. Checking that against `groups.py` would be circular, so the hand-deleted side comes from an independent naive deleter in the test module plus, for one (mockup, group) pair, a checked-in hand-written fixture (§9 T-9).

### 3.7 The `Sizable[Enum]` codec (R3.7)

`hisim/config/sizing.py:_sizable_decoder` already coerces when `sized_field(..., value_type=)` is given, and `HeatDistributionConfig.heating_system` gives it — verified 2026-08-26: `from_dict({"heating_system": "FLOORHEATING"})` yields the member, `is` holds. The live defect is that `value_type` is *optional and silently forgettable* (`json_v2:roadmap/design_review_questions.md` B5). Fix in the kernel, not the file layer: `ConfigBase.__init_subclass__` fills the decoder's type cell from the class's own `Sizable[X]` annotation when `value_type` is absent; a contract test asserts every `Sizable[Enum]` field in the repo decodes to a member (T-13).

### 3.8 Discoverability (R13.2, R13.3, Q-P2.4 → P2)

`schema_export.py` renders `describe_config` into one JSON Schema per `schema_version`, at `hisim/energy_system_v3.schema.json` (the path the mockups' `# yaml-language-server:` line already names). Per-class constraints use the conditional idiom — `allOf: [{if: {properties: {class: {const: "…PVSystem"}}}, then: {properties: {preset: {enum: […]}, config: {properties: {…}, additionalProperties: false}, constructor: {…}, sizing_sources: {propertyNames: {enum: [facts the class reads]}}}}}]` — giving editor completion and rejecting an unknown preset or field in place (AC-P2.6). The file is committed; a CI job re-exports and diffs it (§10).

`hisim/cli.py` (D5) adds `hisim config describe <class>` (fields; presets with pinned/auto and note; sizable fields with law/facts/cardinality/kind; facts provided; constructors with parameters), `hisim config facts <energy_system>` (every fact with its providers, every consumer with its resolved or missing source, unconsumed providers as warnings — the `ResolutionReport` before a run), and `hisim config schema [--out]`.

### 3.9 Performance and I/O (RQ3)

Steps 1–3 touch only the file and the in-memory `${var}` registry (I-6, T-16). Step 4 imports component modules — sanctioned. Step 6 folds `SIZING_CONTRIBUTIONS`, and `hisim/components/building/information.py:_building_sizing_facts` builds a `BuildingInformation`, which reads TABULA (`information.py:274 pd.read_csv(utils.HISIMPATH["housing"])`) — unavoidable, and what epic E5 means by provider-side computation at build time; see §13 DQ2. Weather CSVs, LPG/UTSP and the PV cache are all step 7.

## 4. Alternatives considered

| Design point | Chosen | Rejected — consequence |
|---|---|---|
| Package name | new `hisim/energy_system/` | keep `hisim/scenario_v2/` — the epic retires "scenario" and "v2"; a v3 format in a `scenario_v2` package guarantees stale grep hits and a second rename later. Rejected `[superseded 2026-08-26]` |
| Input item shapes | three (bare / wire / feed) | keep v2's four — the field group is a bundle of wires and, consumer-side, saves nothing |
| Feed's source port | dotted `from: battery.AcBatteryPowerUsed` | v2's separate `output:` key — the mockups write it dotted; one spelling per concept |
| Grouped-by-source connections; sizing after construction | both dropped | keeping either — the first contradicts R2.1 and doubles the validator, the second breaks R7's "error before any component is built" |
| Group expansion point | before validation (step 2) | after resolution — a disabled component's class would still have to import and validate; the off rule would leak into every later stage |
| Realized record format | YAML with ruamel comments, `yaml.safe_load` on the read path | JSON + audit only (Q8 b) — a human checking a run needs two files; comments as data — violates R8.3 |
| Directory | rename to `energy_systems/` in P2 (Q-P2.5c) | rename in P5 — v3 files would be born under a retired name |
| v1 → v3 converter | none (Q-P2.5a) | write one — throw-away code; the P3 recordings *are* the conversion |
| Schema per class | conditional `allOf` over `class` | one file per class — yaml-language-server binds one schema per document |
| Constructor declaration | `@config_constructor` (D4) | `for_*` convention — captures unrelated classmethods; a `CONSTRUCTORS` tuple — a second place to forget |

## 5. Public surface

### 5.1 Wire format

The three mockups are normative. What they do not show, fixed here:

- Top level: `schema_version` (int `3`, required), `name` (str, required), `description` (optional), `components` (mapping, required, empty only if `groups` is not), `groups` (optional), `metadata` (generated files only, tolerated and never interpreted). Any other key: EF-03.
- Entry keys, canonical order: `class`, `preset` | `constructor`, `config`, `inputs`, `sizing_sources`; any other: EF-18. `constructor` is a one-entry mapping `{<name>: {<arg>: <plain value>}}` (two entries: EF-14).
- `sizing_sources`: `{<fact>: "<name>.<fact>"}` or `{<fact>: [ … ]}`; `[]` is legal and explicit (R4.4). `AUTO` is the bare string `AUTO` (`hisim/config/sizing.py:_AutoSize.WIRE_SPELLING`).
- Group: `{enabled: bool, components: non-empty mapping}`, both required. Names: `[A-Za-z_][A-Za-z0-9_]*`, unique across the whole file including grouped entries.

### 5.2 APIs (`hisim.energy_system`)

```python
def load_energy_system(path: str | Path) -> EnergySystemFile          # steps 1+3, no class imports
def expand_groups(file: EnergySystemFile) -> tuple[EnergySystemFile, ExpansionRecord]
def build_simulator_from_energy_system(path, simulation_parameters, path_resolver=None) -> Simulator
def build_components_from_energy_system(path, simulation_parameters, path_resolver=None) -> Sequence[tuple[str, Component]]
def dump_energy_system(file: EnergySystemFile, *, comments: AuditData | None = None) -> str
class EnergySystemExecutor:  configure() / resolve() / build_components() / wire() / register() / build()
```
All raise `EnergySystemError` (one subclass per catalogue group), never warn-and-continue.

### 5.3 Kernel additions this phase needs (D4, R3.7)

```python
@config_constructor                                  # hisim/config/presets.py — new
@classmethod
def for_tabula_code(cls, name: str, *, building_code: str) -> "BuildingConfig": ...

class ConstructorInfo:  name: str; parameters: Tuple[ParameterInfo, ...]; note: str | None   # new
ConfigDescription.constructors: Tuple[ConstructorInfo, ...]                                   # new field
```
Constructor signature convention: first positional parameter is the instance name, supplied by the executor from the entry key; every other parameter is keyword and comes from the file.

### 5.4 CLI and file naming

- `python hisim/hisim_main.py <x.energy_system.yaml> <y.simulation.json>` — third mode in `hisim_main.py:validate_args`, dispatching on the `.energy_system.{yaml,yml,json}` suffix. The two existing modes are untouched (C-P2.4).
- `hisim config describe <fully.qualified.ConfigClass|ComponentClass>` · `hisim config facts <energy_system>` · `hisim config schema [--out PATH]`.
- Directory `energy_systems/` (new, Q-P2.5c); suffix `*.energy_system.yaml`; simulation parameters keep `*.simulation.json` and additionally accept `*.simulation.yaml` (R6).
- Generated per run: `realized.energy_system.yaml`, `realized.audit.yaml`, `component_connections.json`.

### 5.5 Errors

Prefix `EF-` so P1's `E-0x` stay distinguishable. Full catalogue in §8; templates for the ones the requirements call out by name:

```
EF-13  unknown preset 'condensing_gaz' for component 'boiler'
       (hisim.components.generic_boiler.GenericBoilerConfig); known presets:
       condensing_gas, condensing_gas_12kw, oil, oil_12kw, pellets, wood_chips
EF-15  constructor 'for_tabula_code' of 'building' got an unknown argument
       'tabula_code'; parameters: building_code
EF-20  component 'battery' declares an input from 'pv_souht', which is not a
       component of this energy system; did you mean: pv_south?
EF-24  'meter' mixes explicit wires and aggregator feeds from 'ems'; a
       (consumer, source) pair carries one or the other (R2.2)
EF-42  'battery'.sizing_sources.pv_peak_power_in_watt points at 'pv_south',
       which group 'pv' disabled; remove the line or enable the group
EF-4A  (wraps P1 E-02) 'pv_peak_power_in_watt' needed by 'battery' is provided
       by pv_east, pv_south; add to the entry:
         sizing_sources:
           pv_peak_power_in_watt: pv_south.pv_peak_power_in_watt
```

## 6. Internal structure

New package `hisim/energy_system/`; budget ≤ 500 lines per module (P1 §6 amendment).

| Module | Responsibility | Origin | ~lines |
|---|---|---|---|
| `model.py` | pydantic v3 models: file, entry, group, three item shapes, `sizing_sources` | rewrite of `json_v2:schema.py:ScenarioFile` etc. (1449 → split) | 450 |
| `loader.py` | YAML/JSON read, duplicate-key hooks, bool-resolver restriction, `schema_version` gate | new + `json_v2:schema.py:read_scenario_document` | 200 |
| `validation.py` | structural + class-bound validators (D1) | port of `json_v2:schema.py:ScenarioStaticValidator` | 480 |
| `groups.py` | expansion, off rule, `ExpansionRecord` | **new** | 200 |
| `channels.py` | `DynamicConnectionChannel`, `DispatchRule`, matcher | **port as-is** (426) | 426 |
| `resolution.py` | default-feed expansion, `ResolvedDynamicConnection`, port names | port, minus grouped normalization | 500 |
| `wiring.py` | `PlannedWire`, defaults/explicit/feed planning, wiring validation | split out of `json_v2:executor.py` | 450 |
| `executor.py` | lifecycle orchestration, configure, resolve bridge, construct, register | rewrite of `json_v2:executor.py` (1126 → split) | 450 |
| `serialization.py` | config ↔ dict codec, enums by name | **port as-is** (372) | 372 |
| `path_resolver.py` | `${var}` ↔ absolute | **port as-is** (345) | 345 |
| `record.py` | realized record, ruamel comment emitter, audit companion, wire log | rewrite of `json_v2:audit.py` (507) | 480 |
| `schema_export.py` | JSON Schema from `describe_config` | **new** | 250 |
| `errors.py` | `EF-` catalogue, exception classes, message templates | rewrite of `json_v2:errors.py` | 220 |
| `context.py` · `hisim/cli.py` | `BuildContext` (port as-is) · `hisim config describe / facts / schema` (new, D5) | port / new | 100 / 180 |

**Dropped, not ported:** `json_v2:templating.py` (222 — the recorder is P3), `json_v2:parity.py` (500 — the wiring-diff harness is a P3 golden tool), the grouped-by-source normalization in `schema.py`, `ComponentIdentityEntry` (R1.3), the top-level `sizing_facts` block (superseded by `sizing_sources`), the `adjacency`/`preseeded_facts` call into the engine (deleted in P1).

Dependency direction: `errors ← model ← loader ← groups ← validation ← {channels, resolution} ← wiring ← executor ← record`; `serialization`, `path_resolver`, `context`, `schema_export` are leaves.

## 7. Data, state and invariants

`EnergySystemFile` and every model are frozen pydantic models; `expand_groups` returns a new file plus `ExpansionRecord(disabled_groups, dropped_components, dropped_input_items, shrunk_sizing_lists)`, which flows into the audit (R8.2).

- **I-1** No file, model or record carries a `component_id`; the key is the name. *Checked:* `model.py` has no such field; grep in the diff; T-11.
- **I-2** No model field describes an outgoing connection. *Checked:* review of `model.py`; T-2.
- **I-3** `expand_groups` is pure and idempotent: `expand(expand(f)) == expand(f)`. *Checked:* T-5 property.
- **I-4** After expansion no surviving `inputs` item or `sizing_sources` reference names a dropped component. *Checked:* assertion at the end of `groups.expand`; T-5.
- **I-5** `resolve_all` is called with `seed=None` and exactly the enabled configs. *Checked:* grep for `SizingContext.for_building` under `hisim/energy_system/` returns nothing; T-4.
- **I-6** Between `load()` and the end of structural validation the only filesystem read is the file itself. *Checked:* T-16 with a monitored filesystem.
- **I-7** A realized record contains no `AUTO`, no `preset`, no `constructor`, and one `sizing_sources` entry per fact actually read. *Checked:* `record.py:_assert_fully_concrete`; T-10.
- **I-8** `ruamel` is imported in `record.py` and nowhere else, and never inside a load path. *Checked:* grep; T-15.
- **I-9** Derived port names depend only on the resolved feed, never on list order. *Checked:* T-3 with shuffled `inputs`.
- **I-10** `dump(load(f)) == f` for canonical files. *Checked:* T-7.
- **I-11** Every `EF-` message names the offending entry, and where a set of valid values exists, that set. *Checked:* T-11 asserts substrings per ID.
- **I-12** Group expansion happens exactly once, before any class import. *Checked:* call-order review; T-16.

## 8. Error handling

Fail fast at the earliest stage that can know; nothing is repaired, defaulted or skipped. The only sanctioned removals are R14.3's; the only sanctioned warnings are R2.4's unconsumed providers (from `ResolutionReport.unconsumed`) and v2's `optional` default-feed skip when a source port is absent under the participant's config (C-P2.3, v2 §3.3a) — both surface in the audit.

| Stage | IDs |
|---|---|
| 1 load | EF-01 `schema_version` ≠ 3 (names the supported version) · EF-00 unsupported suffix (not `.yaml`/`.yml`) · EF-02 duplicate key (whole document) · EF-03 malformed/unknown top-level key · EF-04 unresolvable `${var}` · EF-05 absolute path · EF-06 `[*]` or a relative reference |
| 2 groups | EF-42 dangling scalar `sizing_sources` after the off rule |
| 3 structural | EF-11 preset **and** constructor · EF-12 neither and `config` incomplete · EF-18 unknown entry key · EF-19 unclassifiable `inputs` item · EF-20 unknown source · EF-24 mixed spelling per pair · EF-25 duplicate feed `(source, output)` · EF-26 duplicate wire into one input · EF-50 nested group · EF-51 component in two groups · EF-52 duplicate name · EF-53 missing/non-bool `enabled` · EF-54 empty group |
| 4 class-bound | EF-10 class not importable · EF-13 unknown preset · EF-14 unknown constructor · EF-15 unknown/missing constructor argument · EF-16 unknown config field · EF-17 undecodable value · EF-21/22 unknown output/input port · EF-23 bare item with no declared defaults · EF-27 feed at a non-aggregator · EF-28 no channel match / tie · EF-29 dispatch vs. `DispatchRule`, dispatch on weight 999 · EF-30 load-type/unit mismatch |
| 6 resolve | EF-40 `sizing_sources` names an unknown component · EF-41 the reference's fact half ≠ the key · EF-43 a fact the class does not read · EF-4A…EF-4H wrap P1 E-01…E-08 with the entry named |
| 8 wire | EF-31 mandatory input unconnected (unless `allow_unconnected_mandatory`) · EF-32 port-name collision |
| 9 record | EF-60 record still contains `AUTO` (internal breach, never user-caused) |

## 9. Testing strategy

Fixtures are the three mockups plus D2's executable fixture; nothing parallel is written.

| ID | Verifies | Kind | Fixture | Failure looks like |
|---|---|---|---|---|
| T-1 | R1.x, R3.1, R3.2, AC-P2.1 *as far as reachable* | integration | D2 executable fixture: load → validate → resolve → build → run → record | a stage raises, or the boiler is not sized from the building |
| T-1b | D3, AC-P2.1 remainder | contract | the three mockups | the *set* of class-bound errors differs from the pinned list (shrinks as P4 lands → update the list in the same PR) |
| T-2 | R2.1, R2.2, I-2 | unit | all mockups, structural only | an item misclassifies; a source-side field exists |
| T-3 | R2.2 feeds, C-P2.3, I-9 | unit + property | UC2 `ems`, UC1 `meter`; `inputs` shuffled 20× | channel mismatch, non-deterministic port names |
| T-4 | R4.3, AC-P2.2, I-5 | unit | UC2 with the battery's `sizing_sources` line deleted; the EMS list deleted; the EMS list `[]` | no EF-4A, or the message lacks the paste-ready block |
| T-5 | R14.3, AC-P2.3 partly, I-3, I-4 | unit + property | every (mockup, group) pair | a dangling reference survives; expansion not idempotent |
| T-6 | R13.2, R13.3, AC-P2.6 | golden | exported schema; `describe` on the four converted classes; `facts` on UC1 | schema accepts an unknown preset/field; `describe` misses a sizable field or a constructor |
| T-7 | RQ4, R11, AC-P2.7, I-10 | round-trip | three mockups + D2 fixture, one canonicalising pass | key order or list style drifts |
| T-8 | R3.8, AC-P2.15, D4 | unit | a test-only config class with two `@config_constructor` methods **plus** UC2 once `BuildingConfig.for_tabula_code` exists | wrong name/arg does not list parameters; the record keeps `constructor:` |
| T-9 | R14.4, AC-P2.3 | **identity** | for every (mockup, group): `realize(disabled)` vs. an independently hand-deleted copy (test-local naive deleter) + one checked-in hand-written fixture for (UC2, `ev`) | one byte differs |
| T-10 | R8.1, R8.2, AC-P2.4, I-7 | **identity / re-execution** | D2 fixture: run₁ → record₁ → run₂ → record₂; also UC2's record must not mention `ev` | record₂ ≠ record₁; audit of run₂ lists a sized field; results differ |
| T-11 | R7, R2.3, R3.6, R14.1/2, RQ2, R12, R4.8, AC-P2.5/8/11/12, I-11 | unit, one per `EF-` id | minimal mutations of UC1/UC2 | an id is unreachable, or the message omits an end or the alternatives |
| T-12 | R2.4, AC-P2.10 | unit | UC1 with the meter's feeds removed | an error instead of a warning; `facts` output silent |
| T-13 | R3.7, AC-P2.14 | contract | every `Sizable[Enum]` field in the repo | `is` comparison fails after `from_dict` |
| T-14 | R6, C-P2.4, C-P2.1 | integration | `hisim_main` with the D2 pair; one existing v1 pair | the v1 path regresses; the modes cross-dispatch |
| T-15 | R8.3, AC-P2.13, I-8 | property | D2 record with every comment stripped | stripped record produces different results; ruamel imported on the load path |
| T-16 | RQ3, AC-P2.9 (re-scoped, §13 DQ2), I-6, I-12 | monitored-filesystem | UC3 through step 3; then step 6 with an allow-list | any read besides the file (steps 1–3); any read outside the allow-list at step 6 |
| T-17 | C-P2.6 | integration | one local-LPG run with `Name`/`Guid`/`StrVal` spelled correctly, before/after evidence | the reference deserialises empty |
| T-18 | D6 | static | `requirements.txt` | `yaml`/`ruamel.yaml` importable but undeclared |

**Not tested here:** many-cardinality *evaluation* (P1's hook raises E-07 — UC2/UC3's EMS lists parse and reach the `sources` mapping, then fail at law evaluation; parking lot); golden parity of recorded setups (P3); consumers (P5); 35-component build performance (measured in T-16, not asserted).

## 10. Migration, compatibility and rollout

**Changes for whom.** Nothing released changes: v1 `*.scenario.json` + `hisim/json_executor.py` keep running (C-P2.4), and `hisim/renovisor/runner.py:run_simulation` keeps calling `hisim_main.main(path_to_module=…)` — dispatch is by suffix, so P5 points it at a `.energy_system.yaml` with no runner change. `scenario-json-freshness.yml` and `scripts/regenerate_scenario_jsons.py` stay in P2 and die in P5 with the 23 v1 JSONs (Q-P2.5a); a **new** freshness job re-exports `hisim/energy_system_v3.schema.json` and fails on drift.

**PR sequence** (each independently reviewable; the plan's §P2 boxes in brackets):

1. **PR-1 model + loader** [box 3] — `errors.py`, `model.py`, `loader.py`, `path_resolver.py`+`serialization.py` (ports); structural validation of the three mockups; T-2, T-7, load-stage half of T-11. No component import anywhere.
2. **PR-2 groups + `inputs` semantics** [boxes 4, 5] — `groups.py`, the `inputs` half of `validation.py`; T-5, T-9's mechanism on structural output, the group and item errors of T-11.
3. **PR-3 executor + wiring** [box 6] — `channels.py`, `resolution.py`, `wiring.py`, `executor.py`, `context.py`, the D2 fixture, the `resolve_all` bridge; T-1, T-1b, T-3, T-4, T-12, T-16. Kernel side-cars, own commits: D4's `@config_constructor` + `ConstructorInfo`; R3.7's annotation-driven codec.
4. **PR-4 realized record + audit** [boxes 7, 8] — `record.py`, ruamel + PyYAML in `requirements.txt` (D6); T-9 complete, T-10, T-15, T-18.
5. **PR-5 schema export + CLI** [box 9] — `schema_export.py`, `hisim/cli.py` (D5), the committed schema and its freshness job; T-6.
6. **PR-6 directory + fixtures** [boxes 10, 11] — create `energy_systems/`, move the D2 fixture there, `hisim_main.py`'s third mode, the UTSP `JsonReference` fix in its own before/after commit (C-P2.6); T-14, T-17. `system_setups/` is **not** renamed here — it still holds the Python setups and their v1 twins; the rename completes in P5 when those go.

**Deleted in P2:** nothing on `main`; the stale untracked `hisim/scenario_v2/__pycache__` goes. **Deleted in P5:** `hisim/json_executor.py`, the 23 `system_setups/*.scenario.json`, `scripts/regenerate_scenario_jsons.py`, `.github/workflows/scenario-json-freshness.yml`. **Docs:** `system_docs/json_scenario_v2_spec.md` is not carried over — the format reference is this spec plus the mockups; `CLAUDE.md`'s "Run a simulation" section gains the third mode.

## 11. Risks and unknowns

- **R-1 `ruamel.yaml` is undeclared and the emitter does not exist.** Q8 states "the emitter exists on `json_v2`"; verified 2026-08-26 — `git grep ruamel json_v2` hits only `roadmap/design_review_questions.md`, and json_v2 contains **no YAML code at all**. Effect: PR-4 writes the comment emitter from scratch (~150 lines) instead of porting it. Mitigation: comments render audit data that exists anyway, so a slip degrades R8.3 to Q8 (b) without touching R8.1.
- **R-2 Channel/dispatch port complexity.** `channels.py` + `resolution.py` = 1168 lines of the spike's subtlest logic (most-specific matching, dispatch rules, derived port names), exercised only by the EMS and the meters. Trigger: T-3 on UC2's 6-feed EMS. Mitigation: port verbatim, adapt only the entry shape, carry over `json_v2:tests/test_scenario_v2_{channels,ems,meter}.py` rewritten onto the v3 shape.
- **R-3 YAML 1.1 type coercion.** PyYAML's SafeLoader turns `NO`, `ON`, `Y` into booleans — enum member names in this codebase are uppercase, so a future `LoadTypes.ON` would silently become `True`. Mitigation: the loader removes the bool resolver for everything but `true|false` (§3.3), and T-11 pins it.
- **R-4 The executable-fixture gap.** Only `GenericBoilerConfig`, `HeatDistributionConfig`, `EMSConfig`, `BuildingConfig` have presets (`grep "presets: ClassVar"` → 4 hits); `HeatDistributionControllerConfig` has facts but none; weather, occupancy, meter and the boiler controller have neither, and no class declares a named constructor. Effect: no mockup executes; D2/D3 are the workaround and AC-P2.1/AC-P2.15 close in P4.
- **R-5 Performance at 35 components.** Load+validate is dict work; resolve is `resolve_all`'s sweeps; build is dominated by TABULA and the weather/LPG loads, unchanged from today. Measured in T-16, asserted nowhere.
- **R-6 Two validation levels drift.** A rule implemented structurally *and* class-bound (duplicate feeds, say) can diverge. Mitigation: each `EF-` id is raised from exactly one place; T-11 has one test per id.
- **R-7 `sizing_sources`-always surprises readers.** A record writes mappings the author never wrote and so diffs noisily against its source. Intended (R8.1) — noted so a reviewer does not "fix" it.

## 12. Code-review guide

**Where to look first.** (1) `groups.py:expand` — the off rule is the only sanctioned deletion in the whole system; check that a dangling *scalar* errors and a list *shrinks*, and that the record of both reaches the audit. (2) `executor.py` step 6 — the bridge into `resolve_all`: enabled set only, `seed=None`, the file's mapping passed through unchanged. (3) `record.py` — that the record is emitted by the same code path as `dump()` and that ruamel never appears on a load path.

**Invariant checklist**
- [ ] I-1 no `component_id` in `model.py`, in any fixture, or in a record
- [ ] I-2 `model.py` has no field expressing an outgoing connection
- [ ] I-3/I-4 `expand` returns a new object, asserts no dangling reference, and is idempotent
- [ ] I-5 `grep -rn "for_building\|seed=" hisim/energy_system/` finds only `seed=None`
- [ ] I-6/I-12 no `open(`/`read_csv`/`import_module` before structural validation returns
- [ ] I-7 `_assert_fully_concrete` runs on every record write, not only in tests
- [ ] I-8 `grep -rn ruamel hisim/` hits `record.py` only
- [ ] I-9 port names built from the resolved feed, sorted by `(weight, source, output)`
- [ ] I-10 one emitter, used by both `dump_energy_system` and the record writer
- [ ] I-11 every `raise` in the diff carries an `EF-` id and names the offending entry

**Requirement checklist** — R2.1: no source-side connection anywhere (T-2) · R2.2: the don't-mix rule is per (consumer, source) pair, not per consumer (T-11 EF-24) · R4.3: uniqueness computed over the **enabled** set, after expansion (T-5) · R7: no `log.warning` + `continue` anywhere in the executor — the v1 pattern must not reappear · R8.1: `sizing_sources` written for every consumer, disabled groups absent (T-10) · R13.4: every message with a closed value set lists it (T-11) · R14.4: the identity test's hand-deleted side does **not** call `groups.expand` (T-9) · RQ2: `schema_version` checked before anything else parses.

**Smells to reject** — any `except … : pass` or default-on-missing in loader/validator (R7) · a fallback preset when `preset` is absent (E1, "never inferred") · `entry.get("component_id")` anywhere (R1.3) · a second YAML emitter or a hand-rolled quoter (Q8 guardrail) · `yaml.load` without `SafeLoader` · a source-keyed connection container (R2.1) · `sizing_facts` or `adjacency` or `preseeded_facts` (deleted in P1) · a class-bound check running in `validation.validate_structural` (D1) · an import of `hisim.energy_system` from `hisim/config/` (epic C4).

**Out of scope for this review** — converting components to presets/laws (P4; the four converted classes are the whole executable surface today) · many-cardinality evaluation (parking lot) · the recorder and golden parity on recorded setups (P3) · RenoVisor/building-sizer/HPC changes (P5) · deleting the v1 executor and its fixtures (P5).

## 13. Open design questions

**DQ1 — Where are named constructors *declared*, and does `ConfigDescription` grow a `constructors` field in P1 or P2?** · blocks §3.3, §5.3, R3.8, R13.2, AC-P2.15, D4
*Context.* R3.8 requires the executor to reject an unknown constructor "listing the constructor's parameters", and R13.2 requires constructor names and parameters in the exported schema. P1 decided constructors are "plain classmethods … not discovered as presets" (`p1_implementation_spec.md` §3.4) and shipped no declaration: `ConfigDescription` (`hisim/config/introspection.py:112`) has `config_class_name, fields, presets, sizable_fields, facts_provided` and nothing else, and no class in the repo declares one — `grep for_tabula_code|for_location|for_household hisim/components/` is empty (2026-08-26). Without a declaration the executor would have to `getattr` an arbitrary name off the config class, which is a code-execution surface driven by a data file.
*Options.* (a) **`@config_constructor` decorator in `hisim/config/presets.py` + `ConstructorInfo` on `ConfigDescription`** — the kernel keeps owning class-side declarations, `describe_config` stays the single introspection source, P2 imports nothing new; costs a small P1 amendment landed as a side-car commit in PR-3. (b) **P2-local registry** — `hisim/energy_system/` scans for a marker attribute; keeps P1 frozen, but splits introspection across two packages and makes `hisim config describe` reach into both. (c) **Name convention `for_*`** — no declaration at all; captures unrelated classmethods and cannot carry a note.
*Recommendation.* (a). It is ~40 lines, keeps epic C4 intact, and is the only option under which the JSON Schema export has one data source.
*Blocks:* T-8 and AC-P2.15 remain partial until at least `BuildingConfig.for_tabula_code` exists (P1 follow-up, P4 B6).

**DQ2 — RQ3/AC-P2.9 forbid filesystem I/O during "load + validate + resolve", but resolution necessarily reads TABULA. Which stage boundary does the requirement mean?** · blocks §3.9, §9 T-16, AC-P2.9
*Context.* `resolve_all` folds `SIZING_CONTRIBUTIONS`; `BuildingConfig`'s contribution is `hisim/components/building/information.py:_building_sizing_facts`, which constructs a `BuildingInformation` and reads `pd.read_csv(utils.HISIMPATH["housing"])` (`information.py:274`). Every mockup contains a Building, so *no* mockup can resolve without that read. Epic E5 says provider-side fact computation "happens at build time", which reads as sanctioning it; RQ3's own sentence ("TABULA/weather reads happen at build time (E5)") appears to intend the same but then names `resolve` inside the no-I/O window.
*Options.* (a) **Re-scope RQ3 to load + group expansion + structural validation** (no class imports, no data files), and assert at resolve only that reads stay inside a named allow-list (the TABULA table). Consequence: AC-P2.9 becomes two assertions; the property that matters — "authoring tools and the schema layer can inspect a file without touching HiSim's data tree" — is preserved and is actually stronger, because it also forbids the class imports. (b) **Keep RQ3 literally and move fact computation to build.** Consequence: sizing would run after construction, contradicting R7 ("hard error before any component is built") and P1's engine design. (c) **Cache the TABULA table at import.** Consequence: hides the read rather than removing it; a first-call read still happens somewhere.
*Recommendation.* (a), as a dated amendment to the requirements document; (b) is a genuine contradiction between RQ3 and R7 and should not be resolved silently in the design.

**DQ3 — AC-P2.1 requires all three mockups to "load, validate, resolve, build and run", which is impossible in P2. Does AC-P2.1 split, or does P2 depend on part of P4?** · blocks §9 T-1/T-1b, D2, D3, plan §P2 gate
*Context.* Four config classes have presets (`GenericBoilerConfig`, `HeatDistributionConfig`, `EMSConfig`, `BuildingConfig`); `HeatDistributionControllerConfig` has facts but no presets; Weather, `UtspLpgConnector`, `ElectricityMeter`, `GenericBoilerController`, the heat pump, PV, battery, `HeatSource`, `L1HeatPumpController`, cars and chargers have neither presets nor constructors. UC1 alone references five classes with no presets; UC2 and UC3 additionally reference preset names that do not exist even on converted classes (`hds` presets `floor_heating`/`radiator`/`radiator_low_temperature` — only `standard` exists). So the class-bound validator will correctly reject all three mockups until P4 lands.
*Options.* (a) **Split AC-P2.1**: "all three mockups load, structurally validate and expand groups" (P2 gate) + "each mockup executes end to end as its classes are converted" (P4 batch gate), with D2's executable fixture carrying P2's end-to-end coverage and D3's pinned error sets tracking the gap. (b) **Pull the missing conversions into P2** — roughly batches B1, B5, B6, B7 of the plan, i.e. most of P4 ahead of its own gates. (c) **Stub presets** for the unconverted classes. Consequence: throw-away presets in wire format, which E8 says are frozen once released.
*Recommendation.* (a). It keeps P2's gate honest and turns the gap into a shrinking, tested list rather than a note.

**DQ4 — Does the `metadata` block belong in the format, or only in generated records?** · blocks §5.1, §3.6, RQ4
*Context.* v2 added `metadata` because a strict schema otherwise rejects the record it just wrote (`json_v2:roadmap/design_review_questions.md` A4). AC-P2.4 needs `source_energy_system_file` stable across re-execution (§3.6 rule (i)). A hand-authored file has no business carrying it.
*Options.* (a) **Tolerated everywhere, written only by generators** — one schema; a hand-written `metadata` is ignored, one place where the file is not fully meaningful. (b) **Two schemas** (authored / realized, the latter a superset) — cleaner semantics, two artifacts to keep in step, and each file kind needs the right `$schema` line, which a record can emit for itself.
*Recommendation.* (b) if the schema export proves cheap in PR-5, else (a); decide at PR-5 review.

## 14. Glossary

**Structural validation** — the class-independent half of §3.4 step 3: shapes, names, references, group rules; needs no component import (D1). **Class-bound validation** — step 4: presets, fields, ports, channels, facts. **Expansion record** — what the off rule removed and shrank, carried into the audit (R14.3). **Executable fixture** — D2's fourth energy-system file, restricted to what P1 converted plus full-`config` entries. **Item shape** — one of the three `inputs` forms (§3.2). **Carry-forward metadata** — `source_energy_system_file`, copied unchanged into every re-execution's record so AC-P2.4 is byte-exact.

## Amendments (owner decisions, 2026-08-26)

- **DQ4** `[decided 2026-08-26]` one JSON Schema per `schema_version` with `metadata` optional; the executor rejects a present `metadata` block on a plain run ("this is a realized record; re-run it explicitly or strip metadata") and accepts it on an explicit re-run.
- **DQ3 / AC-P2.1** `[decided 2026-08-26]` P2 converts UC1's four classes (Weather, UtspLpgConnector, GenericBoilerController, ElectricityMeter) so UC1 executes end to end; UC2/UC3 keep pinned expected-error sets (D3); the fourth executable mockup (D2) is therefore not needed.
- **DQ2 / RQ3** `[decided 2026-08-26]` RQ3 dropped entirely; no I/O test.
- **Package** `[decided 2026-08-26]` `hisim/energy_system/`. **Directories:** new files in top-level `energy_systems/`; `system_setups/` untouched (Python setups stay for months).
- **CLI** `[decided 2026-08-26]` `hisim/cli.py` with nested nouns: `hisim energy-system describe|facts|run`.
- **Provenance comments** `[decided 2026-08-26]` built in PR-4 (new work; PyYAML + ruamel.yaml declared).
- **Constructors** `[decided 2026-08-26]` class side `@constructor` (P1 rework), file side R3.8 as proposed.
- **Format** `[decided 2026-08-26]` YAML only (R15): the JSON loader path and the `.json` suffix are removed from §3/§5/§8; `component_connections.json` stays as the v1-compatible *output* wire log (A4), unaffected.

## Amendments (PR-1, 2026-08-26)

- **§6 modules** the loader split into `document.py` (strict YAML loader, duplicate-key walk, typed accessors) and `emitter.py` (canonical writer, the single emitter PR-4's comment renderer builds on) to keep `loader.py` ≤ 500 lines; `path_resolver.py` ported as-is with its errors folded into `EF-04`.
- **§8 stages** `EF-40` (unknown sizing provider) and `EF-41` (fact half ≠ key) are raised structurally, not at resolve: both are decidable from the document alone.
- **§5.4 `EF-05`** rule fixed: a key equal to or ending in `_path`/`_paths`/`_directory`/`_file`/`_filename` whose value starts with `/`, `\` or a Windows drive, scanned recursively over `config` and constructor arguments.
- **New ids** `EF-07` wrong YAML kind, `EF-08` unusable name. Model is frozen pydantic v2 (`extra="forbid"`); the three `inputs` shapes are a discriminated union.
- **Canonical style** indents block sequences under their key, so generated files diff cleanly against the hand-written mockups; `dump(load(f))` is a fixed point for all three mockups after one pass (AC-P2.7).
- **D6** `PyYAML` added to `requirements.txt` (`setup.py` reads it); `ruamel.yaml` arrives with PR-4.
- **For PR-2:** `groups.py` (`expand_groups` with `ExpansionRecord`, `EF-42` dangling scalar), the class-bound half of validation as a separate entry point, and the `sizing_sources` → `resolve_all(sources=)` bridge (`EF-43`, `EF-4A…4H` wrapping P1's `E-01…E-08`); `metadata` gating (DQ4) and `AUTO` wire decoding belong to the executor (PR-3).

## Amendments (PR-2, 2026-08-26)

- **§6 modules** the class-bound stage is `classes.py` (binding entries to component/config classes) + `bindings.py` (result types and checks decidable from a config class alone) + `codec.py` (config value decoding: enums by name, `AUTO` wire spelling, paths) + `sizing_bridge.py` (`sizing_sources` → `resolve_all(sources=)`, kernel errors wrapped) + `configure.py` (orchestration) + `groups.py`; `classes.py`/`configure.py` are deliberately not re-exported from the package so `import hisim.energy_system` loads no component code.
- **§5.4 ids** EF-15 unknown constructor, EF-16 constructor argument, EF-17 unknown config key (EF-14 stays PR-1's malformed constructor block); new EF-1A undecodable value, EF-1B `AUTO` on a field without a law, EF-4X fallback for a kernel failure that names no single entry (deadlock report).
- **Kernel error classification** is by message substring (`KernelFailure`); every one of E-01…E-08 is provoked through the real kernel in the tests so a reworded message fails loudly instead of degrading to EF-4X. Follow-up: give the kernel errors a machine-readable id.
- **Explicit-wire port checks** (EF-21/22/23/27–32) move to PR-3: HiSim registers ports in `Component.__init__`, so they are not decidable before construction (confirmed against `json_v2:hisim/scenario_v2/executor.py:_check_ports_exist`).
- **R3.7 done here** instead of PR-3: `sized_field` records `value_type`, and `ConfigBase.__init_subclass__` fills a missing one from the `Sizable[SomeEnum]` annotation; a contract test asserts every enum-typed sizable field decodes member names to members and `"AUTO"` to the sentinel.
- **Expected-error pin** (AC-P2.1 amended): pinned per mockup in `tests/test_energy_system_classes.py::ExpectedFailures`; today UC1 fails on 5 entries (all EF-13 unknown preset), UC2 on 15, UC3 on 32 — every one a preset/constructor still to be created by P2's UC1 conversion or P4, none a format defect. The mockups' two wrong class paths (`generic_heat_source.HeatSource` → `simple_heat_source.SimpleHeatSource`, `generic_ev_charger.EVCharger` → `controller_l1_generic_ev_charge.L1Controller`) were corrected.
- **Many-reader `[]`** cannot be exercised end to end (no converted many-reader, EQ2); the bridge is tested to carry `[]` through, EF-4E/EF-4G cover the misuse cases.
- **Open:** `PathFieldCodec.resolve` mutates via `setattr`, which a frozen config with a path field would refuse (none converted yet).

## Amendments (PR-3, 2026-08-26)

- **UC1 needed five conversions**, not four: `HeatDistributionControllerConfig` (`preset_standard`; heating load, reference temperature and the two set temperatures sized; specific load and threshold as laws) joins Weather (`preset_standard`, `for_location`), UtspLpgConnector (`preset_standard`, `for_household`), GenericBoilerController (`preset_modulating`, `preset_on_off`; the pellet/wood-chip factories are field overrides per naming A2) and ElectricityMeter (`preset_standard`). `SizingContext`/`Size` gained `set_heating_temperature_in_celsius` / `set_cooling_temperature_in_celsius`, contributed by the Building.
- **Minimal mockup corrected** so it can be wired: explicit boiler↔HDS water-temperature wires, `position_hot_water_storage_in_system: NO_STORAGE_MASS_FLOW_FIX`, the boiler controller's storage-temperature input, the meter fed by occupancy only (a gas boiler has no electricity output), `boiler_controller: preset: modulating`. UC1 pin is empty; UC2/UC3 pins shrank accordingly.
- **§6 modules** `channels.py`/`channel_matching.py`, `resolution.py`/`feed_resolution.py`/`aggregator_ports.py`, `wiring.py`/`wiring_checks.py`, `executor.py` (+ `SimulationParametersReader`, `*.simulation.yaml` accepted); `dynamic_component.py` gained `CHANNELS`, `resolve_dynamic_connections`, `add_resolved_dynamic_input/output`; `DefaultDynamicConnection` dropped in favour of the existing `dynamic_default_connections` registry.
- **§5.4 ids** EF-09 metadata present on a plain run; EF-21/22/23/25/26/27/28/29/2A/30/31/32 wiring; EF-33 constructor raised (new).
- **Layering follow-up (PR-6 or P4):** `dynamic_component.py`, the EMS and the meter import `hisim.energy_system.{channels,resolution,errors}`; importing any submodule executes `hisim/energy_system/__init__.py`, which loads the file-format stack (pydantic, PyYAML) into every component import. Move the declaration types (`DynamicConnectionChannel`, `DispatchRule`, `ResolvedDynamicConnection`, the tag decoder) into a leaf below the components — `hisim/config/channels.py` — and keep only the matcher in `hisim/energy_system`.
- **Closed 2026-08-27:** `tests/test_energy_system_dispatch.py` drives a dispatch feed with `target_input` end to end (PV + battery + EMS + meter as complete `config` blocks, R1.4 — no P4 conversion needed) and pins the two rejections (R2.5). ~~no test drives a dispatch feed with `target_input` (needs a converted controllable participant — battery, P4)~~; `PathFieldCodec.resolve` mutates via `setattr` (Weather's `source_path` is the first converted path field and is still an absolute path built at construction — symbolizing to `${inputs}` lands with the record writer).
- **PR-4 input:** `build_energy_system(...)` returns `BuiltEnergySystem` (post-expansion model, `ExpansionRecord`, bindings with the chosen builder per entry, configured system with `ResolutionReport` and warnings, planned wires with file and runtime names, resolved feeds with derived port names, the simulator) without running.

## Amendments (PR-4, 2026-08-26)

- **§6 modules** the record layer is five modules, not one, to keep each under the 500-line budget: `metadata.py` (the reproduction block), `record.py` (`realize`, the concreteness assertion, the re-run verification), `audit_records.py` (the typed provenance shapes, read by both consumers), `audit.py` (collecting them from a finished build; the audit file and the wire log), `comments.py` (the annotated writer, the only place `ruamel.yaml` appears).
- **§3.6 byte-exactness restated.** Rule (iii) is right and rule (i) is dropped. A record₁ written from an authored file carries sizing comments; its re-run has nothing to size and therefore writes none, so record₂ ≠ record₁ *as text* while being identical *as data*. The fixed point is record₂ == record₃, which the test asserts byte for byte. `metadata.source_energy_system` is **not** carried forward: each record names the file it was actually run from, and every record comparison drops the two `source_` keys instead (`RunMetadata.without_sources`). `verify_rerun` enforces the promise on every re-run — no field may have been sized, and the comment-free canonical dump of the record produced must equal that of the record given.
- **§5.4 ids** `EF-60` a record that is not fully concrete (a surviving `AUTO`, a surviving `preset`/`constructor`, or a configuration value that is not plain data) and `EF-61` a re-run that did not reproduce its record; both raised through the new `EnergySystemRecordError`, and neither reachable from any authored file.
- **Emitter, two changes so the two writers cannot drift.** `LINE_WIDTH` becomes effectively unlimited — folding a long scalar is one of exactly two places where PyYAML and `ruamel.yaml` disagree about the same document — and `EnergySystemEmitter.must_quote` is the other: it answers the quoting question with PyYAML's own implicit resolver (plus the multi-line rule), and the annotated writer asks it rather than its own library. With those two, the comment-free ruamel output equals `dump_energy_system` byte for byte on all three mockups and on a record, which is a test.
- **Codec, two fixes the record forced.** A complete `config` block now passes through `ConfigValueCodec.to_deserializer_payload` before the configuration class deserializes it, because this format writes enums by member *name* while `dataclasses_json` reads them by *value* and the two differ (`LoadTypes.GAS` is `"Gas"`) — without it a realized record of any entry with such a field would not load. And `ConfigValueCodec.wire_value` widens an `int` held by a `float`-annotated field, without which a record and its own re-execution differ textually (`minimum_runtime_in_seconds: 1800` against `1800.0`) for a whole class of presets.
- **`ConfiguredSystem.origins`** keeps each entry's configuration as its preset or constructor produced it, so the audit states an override's preset default without calling the builder a second time.
- **T-9 on the record** cannot use UC2/UC3 (their classes do not configure), so the identity is checked over an inline two-group file built from the converted classes — the minimal household with its meter in a disabled group and its boiler pair in an enabled one — against a hand-written copy with the group and its component deleted, which never touches `groups.expand`.
- **Golden** `tests/energy_systems/uc1.realized.energy_system.yaml`, the record of UC1 with the version, the commit and the two source paths pinned; rewritten only under `HISIM_REGENERATE_ENERGY_SYSTEM_GOLDENS`, following the building goldens' convention.
- **Open:** the wire log names components by their *runtime* names, which differ from the file's keys as soon as a configuration carries a building or a unit — the v1 shape has no room for both, and the record and the audit carry the file names; `describe_source` writes a path relative to the working directory, so a record's metadata reads differently depending on where the run was started from.

## Amendments (PR-5, 2026-08-26)

- **§6 modules** the exporter is two modules, not one, to stay inside the 500-line budget: `schema_classes.py` (which component classes a file may name at all, and the annotation → JSON-Schema translation) and `schema_export.py` (the assembly of the document, `build_schema`, `export_schema`, `render_schema`, `schema_is_current`). Neither is re-exported from the package, so `import hisim.energy_system` still loads no component code.
- **DQ4 closed as (a)** one schema, `metadata` optional and never interpreted; the two-schema option was not worth the second artifact once the record proved to validate against the authored schema unchanged.
- **`class` is a closed enum over reachable spellings**, not one path per class: a component defined in a sub-package is also re-exported by it (`hisim.components.building.Building` and `…building.building.Building`), both import the same class and both therefore appear, shortest first. The `if` half of each conditional matches on the same list.
- **Empty constraints are `false`**, the schema nothing satisfies, wherever a class declares no preset or no constructor — writing one is then rejected on its own line rather than accepted and refused later.
- **What the schema catches that the class-bound validator does not.** UC2's weather names `for_location: {location: BERLIN}`, which no `LocationEnum` member carries; the class-bound stage checks argument *names* and defers *values* to the configuring stage, while the schema sees both. The mockup pin therefore does not list `weather` and the schema test carries it as a named exception. Conversely the schema reads a file as written, disabled groups included, so a disabled group's entries are compared against the pin's complement rather than against the pin.
- **§5.4 CLI** implemented as the owner amendment states — `hisim energy-system describe|facts|schema|run` — with exit codes 0 / 2 (usage) / 1 (any `EnergySystemError`, message on stderr). `describe` accepts either the component class a file writes or the configuration class itself. `facts` prints providers, consumers and the resolution table without running, and returns 1 with the executor's own message for a file that does not resolve.
- **D6** `jsonschema` added to `requirements.txt` as a test-only dependency (nothing in `hisim/` imports it); `hisim/energy_system_v3.schema.json` added to `setup.py`'s `package_data`.
- **Freshness** is a plain pytest (`schema_is_current`), not a workflow, so CI's existing test job covers it.

## Amendments (PR-6, 2026-08-26)

- **§5.4 third mode** `hisim_main.py` dispatches on the compound suffix `*.energy_system.{yaml,yml}`, so a plain `*.simulation.yaml` first argument stays unclaimed and is reported rather than parsed as a household; the parameter suffixes it accepts are taken from `SimulationParametersReader` so the two cannot disagree. The Python and v1 JSON modes are untouched and pinned by a test.
- **Directory** `energy_systems/` holds `README.md`, `gas_boiler_household.energy_system.yaml` (the canonically written twin of the minimal mockup, identity asserted after one canonicalising pass), `one_day_15min.simulation.yaml` and `2021_minutely.simulation.yaml` (the YAML twin of `system_setups/2021_minutely_plots.simulation.json` without its `OPEN_DIRECTORY_IN_EXPLORER`). The one-day parameters moved out of `tests/energy_systems/` and the tests now read the shipped file, so there is one copy rather than two.
- **Layering follow-up done.** `hisim/config/channels.py` holds `DispatchRule`, `DynamicConnectionChannel`, `ConnectionTag`, `ResolvedDispatch`, `ResolvedDynamicConnection` and `ResolvedDynamicWire`; `hisim.energy_system.channels` keeps `FeedRequest` and `TagDecoder` and re-exports the declarations, and `hisim.energy_system.resolution` becomes a re-export. `hisim.loadtypes` is added to `hisim/config/__init__.py`'s sanctioned exceptions (it imports only the standard library, like `hisim.log`), together with the `TYPE_CHECKING`-only reference to `hisim.component`. Importing `hisim.dynamic_component`, the meter or the EMS now loads neither `hisim.energy_system` nor pydantic nor PyYAML, asserted in a fresh interpreter by `tests/test_energy_system_layering.py`.
- **Error classification changed with the move.** The three raise sites that travelled to the leaf — a channel with no tags, a channel that forbids dispatch and names dispatch tags, `get_channel` on an undeclared key — are defects of a component class that no file can cause, and they now raise `ChannelDeclarationError` instead of `EF-28`/`EF-29`. Both identifiers stay in use, raised by the matcher for the file-caused conditions, where they were already tested.
- **C-P2.6 verified at the config level; no live UTSP call was possible.** The defect is real and still present in the *v1* fixtures: 22 `system_setups/*.scenario.json` spell `household: {name, guid: {str_val}}` while `utspclient`'s dataclass declares `Name`/`Guid`/`StrVal`, and `json_executor.py:172-177` only survives it by pascalizing those four keys by hand. This format needs no such case: `ConfigBlockWriter` writes the class's own spelling, and `tests/test_energy_system_utsp.py` round-trips `UtspLpgConnectorConfig.for_household` with all four references chosen away from their defaults — writer → YAML → codec → `from_dict` — comparing name and Guid field for field, and does the same over the realized record of the shipped household. A live run against a UTSP server would need `UTSP_URL` and `UTSP_API_KEY`; the reference household ships as a predefined profile, so the offline round trip covers what the requirement is about (a reference that deserialises empty), and only the remote request itself is unexercised.

## Amendments (PR-7, review of #592, 2026-08-27)

- **The occupancy constructor now honours the household it is given** (review comment on
  `loadprofilegenerator_utsp_connector.py:173`). `for_household` set
  `name_of_predefined_loadprofile` to the shipped `"CHR01 Couple both at Work"` whatever
  household it was handed, and the predefined mode reads the profile *by that name*, so asking
  for any other household produced the couple's profile without a word. The constructor takes
  `data_acquisition_mode` (default: the predefined profile) and derives the profile name from
  the household through `predefined_profile_of`, which refuses a household with no shipped
  profile and refuses a list of them, naming the shipped households and the two computing modes
  in both messages; a computed household leaves the name empty rather than claiming a profile
  it does not simulate. `predefined_households()` reads the shipped set from
  `utils.HISIMPATH["occupancy"]` intersected with the catalogue, so adding a profile needs no
  second list. `preset_standard` is unchanged in value — CHR01 in the predefined mode — and the
  golden realized record is byte-identical. Four tests in `tests/test_energy_system_utsp.py`
  pin the two refusals, the computed case and the preset; the exported JSON Schema gains the
  new constructor parameter. A builder that raises reaches the author as `EF-17` naming the
  constructor, which is how the refusal surfaces from a file.
- **The naming of the builders stands** (review comment on `:137`, `build_occupancy_from_lpg_household`
  suggested). The `preset_`/`for_`/`from_` prefixes are the decided convention (P1 amendment of
  2026-08-26, Q-P1.9): the prefix marks *what kind of builder* a method is and is stripped for
  the wire name, so the file spells `constructor: {for_household: {...}}`. A descriptive verb in
  the method name would either leak into the wire format or need a second, hand-maintained
  mapping; the class the method sits on supplies the noun the verb would repeat.
