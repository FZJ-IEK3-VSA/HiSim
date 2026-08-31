# P2 — Energy-system file format and executor — requirements

**Status:** in review · **Date:** 2026-08-31 (R15.9–R15.11 added while implementing R15; 08-28: R15 exclusive variants added, Q-P2.2 re-answered, C-P2.5 rewritten — an amendment to the merged P2; 08-27: R2.5 dispatch rule; 08-26: Q8, Q-P2.4–Q-P2.6 decided; AC-P2.1 amended; RQ3 dropped)
**Author(s):** Noah Pflugradt (owner; `[given]`) · assistant (`[proposed]`, mockups)
**Reviewers:** HiSim core team
**Parent:** `roadmap/declarative_energy_systems/epic.md` (E1–E8 apply by reference) · **Plan:** `roadmap/declarative_energy_systems/plan.md` §P2 · **Depends on:** P1 accepted
**Related:** `system_docs/json_scenario_v2_spec.md` and `roadmap/design_review_questions.md` on branch `json_v2` (solution-design input; decisions A1–A4, 11, 20, 21 carried over where not contradicted here); `hisim/scenario_v2/` (schema, executor, resolution, audit, templating)
**Mockups (the contract):** `roadmap/declarative_energy_systems/energy_system_mockup_minimal.yaml` · `roadmap/declarative_energy_systems/energy_system_mockup.yaml` · `roadmap/declarative_energy_systems/energy_system_mockup_mfh.yaml`

**Tags:** feature, behavior-change, migration, compatibility
**Keywords:** energy-system file, YAML, schema_version, inputs, preset, sizing_sources, groups, realized record, audit, provenance, JSON Schema, path resolver

**What a reviewer must decide here:** (1) consumer-side `inputs` replacing grouped-by-source connections — R2.1; (2) groups with the "off" rule and the identity test — R14, and exclusive variants with the same two rules — R15 (amendment, 2026-08-28); (3) YAML-comment provenance kept from `json_v2` A4 — Q8; (4) Q-P2.4 (describe/facts CLI in P2), Q-P2.5 (retiring the v1 JSON files and renaming `system_setups/`) and the proposed `constructor:` form (Q-P2.6, R3.8); metering variants are a variant, not base files (Q-P2.2, re-answered 2026-08-28).

---

## 1. Abstract

The `json_v2` spike defined an energy system format with entry-level presets, grouped connections and a re-executable realized record, and built its parser and executor. Three things changed in the epic's review: sizing sources became consumer-side references (E2/E3), connections move to the consuming component for the same reason, and component groups with an on/off flag replace both the subsystem idea and combinatorial base files. P2 specifies the resulting format — spelled out completely in three mockups — and the executor that loads, validates, resolves (through the P1 kernel), builds and records it. RenoVisor, the building sizer and the HPC harness are the consumers (P5).

## 3. Executive Summary

Format v3: a `components` mapping (name → `class`, `preset`, `config`, `inputs`, `sizing_sources`), optional `groups` (name → `enabled`, `components`), `schema_version`, symbolic paths. Every inconsistency is a hard error at load. A run writes a realized record (presets expanded, `AUTO` → numbers, disabled groups absent) that re-executes bit-for-bit, plus an audit companion. Affected: `hisim/scenario_v2/*` (evolves to v3), `hisim/json_executor.py` (retired in P5), `hisim/hisim_main.py`. Cost of inaction: P1's binding rule has no wire form, P3–P5 cannot start, and consumers keep `ModularHouseholdConfig`.

## 4. Context and Current Situation

**Current behavior.**
- **main:** `hisim/json_executor.py` runs full-dump `*.scenario.json`; `_get_default_config` matches method names and fails on classes with 0 or ≥2 `*default*` methods; no preset awareness; no realized record; connections as ~100 field-level entries per household.
- **branch `json_v2`** (`hisim/scenario_v2/`, verified 2026-08-25): pydantic schema with four connection shapes and the grouped-by-source spelling (decision 20); entry-level `preset` + sparse `config` (decision 21); duplicate-key-detecting JSON loader; `${var}` `PathResolver`; realized record + audit + wire log (A4); YAML canonical for generated files with provenance comments (A3/A4 extension); templating. Fixtures: `tests/scenario_v2_*.scenario.json` (14 components).
- **Counted:** grouped spelling ≈ 30 source blocks for 14 components; the three mockups measure the v3 style at 8 / 19 / 35 components ≈ 70 / 250 / 430 lines. RenoVisor and the building sizer expose 4 + ~20 knobs that map to overrides, base-file choice and 3 presence toggles (Q6).

**Stakeholders.** Existing: RenoVisor, building sizer, HPC harness (epic §4); the golden suites; the P3 recorder as a *producer*. Hypothetical: webtool, template creator.

**Required behavior:** R1, R2, R6–R8, R11–R14, RQ2–RQ3. **Kind of change:** new format version replacing v1 and the v2 spike; behavior change for every file-based run. **Assumption A1:** v2 fixtures are regenerated, not migrated (the spike never shipped).

## 5. Goals and Non-Goals

**Goals** — the three mockups load, resolve, build, run and re-execute · zero sizing text where unambiguous · toggles without combinatorial files · everything an author can write is discoverable in the editor.
**Non-Goals** — dynamic-connection verbosity (Q4) · a template layer (E4, parking lot) · recording from Python (P3) · consumer changes (P5) · results formats.

## 6. Use Cases and Mockups

| | Mockup | Decides / demonstrates |
|---|---|---|
| UC1 | `energy_system_mockup_minimal.yaml` | canonical component block; bare `inputs`; aggregator feed; no `sizing_sources`, no `groups` |
| UC2 | `energy_system_mockup.yaml` | explicit wire `{input, from}`; scalar and list `sizing_sources`; four groups incl. one disabled; the cliff (a second provider forces three existing consumers to name theirs); open point O2, resolved by R15 — the variant itself enters the mockup with P2.1, since a mockup must load |
| UC3 | `energy_system_mockup_mfh.yaml` | 35 components; name-prefix convention; explicit per-apartment values (C2) |
| UC4 | footer of UC2 | RenoVisor flow: base file per heating system, `building.config.building_code`, `groups.<x>.enabled`, `variants.electricity_management.selected` |
| UC5 | footers | realized record content and re-execution |

Changes to the format are made as diffs on the mockups first, then reflected here.

## 8. Requirements

### R1 — Components `[given]`
- R1.1 `[proposed]` `components` is a mapping; the key is the author-chosen, file-unique name and the identity in `inputs`, `sizing_sources`, results and the audit.
- R1.2 `[proposed]` Any class may appear several times.
- R1.3 `[decided 2026-08-25]` Entries carry no `component_id`; the executor supplies it from the key (P1 R3.5). Names are global inside groups; repeated structures use a prefix by convention.
- R1.4 `[proposed]` Entry keys, in canonical order: `class`, `preset` **or** `constructor`, `config`, `inputs`, `sizing_sources`; exactly one of `preset`/`constructor` is present unless `config` is complete.

### R2 — Connections `[given]`
- R2.1 `[decided 2026-08-25; mockups]` Declared at the **consuming component** under `inputs`; nothing at the source. Grouped-by-source spelling (v2 decision 20) is retired.
- R2.2 `[proposed; v2 §3.3]` Item shapes: bare source name → the target's default connections for that source class; `{input, from: source.Output}` → one explicit wire; `{from, component_type?, tags, weight, dispatch?}` → aggregator feed (semantics of v2 §3.3d unchanged, incl. weight 999, `dispatch` back-channel, `(source, output)` uniqueness). The don't-mix rule of v2 §3.4 applies per (consumer, source) pair.
- R2.3 `[proposed]` Unknown source/port, no defaults for a bare item, duplicate feed, mixed spelling → hard error naming consumer and source.
- R2.4 `[proposed]` A component that feeds nothing is legal and reported as a warning.
- R2.5 `[proposed 2026-08-27; from the implementation]` **Dispatch back-channel.** A participant the aggregator controls is written as a feed with a `dispatch` block on the *aggregator's* side; `dispatch: {target_input: <Input>}` makes the executor create the aggregator's derived output (`DispatchTo<source>_<Input>`) **and** wire it into the participant's input; `dispatch: {}` creates a recorded signal nobody reads. Nothing is written at the participant: an explicit `{input, from}` wire may never name a derived port (its name is not in the file the author reads). A channel with `DispatchRule.REQUIRED` rejects a feed without `dispatch`; a bare item can therefore never stand for a controlled participant, because class-side default feeds carry no dispatch target — every controlled participant is spelled out (E1). A participant on a channel whose rule is `FORBIDDEN` (production, uncontrolled consumption) carries the monitored-only weight 999; a real rank there is rejected. The mockups were corrected accordingly on 2026-08-27 (the battery's wire moved from the battery to the EMS feed; uncontrolled feeds to 999). **Known mismatch, left for P4 B2:** the EMS's class-side default feed for the occupancy still declares weight 1 (`controller_l2_energy_management_system.py`, `get_default_connections_from_utsp_occupancy`), so a bare `- occupancy` item under an EMS is rejected with EF-29 until that declaration is aligned — a legacy-path behaviour check is needed before changing it, because the legacy ranking loop sees the weight.

### R3 — Configuration `[given]`
- R3.1 `[proposed; v2 decision 21]` `preset` names a class preset; `config` holds sparse overrides only.
- R3.2 `[proposed]` Any field may be set in `config`, including `AUTO` to re-open a field a preset pinned.
- R3.6 `[proposed]` Unknown field, unknown preset, wrong type → hard error listing the valid names.
- R3.8 `[proposed; from P1 Q-P1.6, shown in the mockups]` **Constructor form.** For classes parameterised by an open identifier space (Weather by location, Building by TABULA code, occupancy by LPG household) an entry may carry `constructor: {<name>: {<arg>: <value>, …}}` instead of `preset`, naming a classmethod decorated `@constructor` (its wire name is the full method name, `for_…`/`from_…`; P1 amendment of 2026-08-26) with plain-value arguments (no expressions, E3). `config` overrides apply after the constructor exactly as after a preset. Unknown constructor, unknown or missing argument → hard error listing the constructor's parameters. The realized record expands a constructor entry to a full `config` like a preset, and the audit records the constructor and its arguments.
- R3.7 `[proposed; random_findings]` Enum-typed sizable fields (`Sizable[SomeEnum]`, e.g. `HeatDistributionConfig.heating_system`) decode from a file to the enum *member*, not to its string; the `sized_field` codec must not bypass the enum handling of the serialisation layer. (Today `"FLOORHEATING"` stays a `str`; equality holds only because config enums are `(str, Enum)`, `is` comparisons would silently fail.)

### R4 — Sizing sources `[given]`
- R4.3 `[decided 2026-08-25]` `sizing_sources: {fact: "<name>.<fact>" | ["<name>.<fact>", …]}` on the consumer, required exactly when the bare fact has ≠ 1 provider among the **enabled** components (P1 R4.3 with `configs` = enabled set). Error text prints the paste-ready mapping.
- R4.4 `[decided 2026-08-25]` A list is the only way to feed a many-reader; no `all`; `[]` is explicit "none". No expressions anywhere in the file (E3).
- R4.8 `[proposed]` Wildcards and relative references are not part of v3 (reserved for a future preprocessor, E4).

### R6 — Simulation parameters separate `[proposed]`
Time range, resolution, post-processing stay in the simulation-parameters file (`*.simulation.yaml`; the existing `*.simulation.json` files keep working for the Python setups until they are migrated); an energy-system file contains none of them.

### R7 — Hard errors, no repair `[proposed]`
Unknown component/field/preset/fact, unprovided fact, ambiguity, cycle, duplicate key, wrong `schema_version`, unresolvable `${var}` → hard error before any component is built. The only sanctioned dropping is R14's.

### R8 — Realized record and provenance `[proposed; v2 A3/A4]`
- R8.1 `[proposed; v2 A4]` A run writes `realized.energy_system.yaml`: same format, presets expanded to full `config`, every sized value a number, `sizing_sources` always written, enabled groups kept, disabled groups **absent**. Re-running it sizes nothing and reproduces the run bit-for-bit.
- R8.2 `[proposed; v2 A4]` An audit companion (machine-readable) records per component the preset built from, per sized field the law, the `(qualified source, value)` inputs and the result, the disabled groups, and the resolved aggregator feeds.
- R8.3 `[proposed; v2 A4 extension]` Provenance rendered as YAML comments in the realized record, never load-bearing, only in generated files — **if Q8 confirms**.

### R11 — Readable, canonical style `[decided 2026-08-25]`
Self-contained component blocks in the R1.4 key order; block lists, one item per line; no central connections block; the generator emits exactly this style so hand-written and generated files diff cleanly.

### R15 — YAML is the only format `[decided 2026-08-26, owner]`
Energy-system files are YAML (`*.energy_system.yaml`), hand-written and generated alike; JSON is **not** an accepted input format and `*.json` is not an accepted suffix, even though YAML parsers happen to accept JSON syntax. One loader, one canonical style (R11), one duplicate-key rule, one fixture format. The JSON artifacts that remain are the exported JSON *Schema* (the schema language's own format, R13.2) and the retired v1 `*.scenario.json` files, which v3 never reads. The HPC harness ships the YAML text inside its own job payload; RenoVisor writes YAML. Supersedes the inherited `json_v2` decision A3 ("JSON stays loadable").

### R12 — Resource references `[proposed; v2 §3.5]`
Paths are `${var}/…` through the `PathResolver`; absolute paths in a file are an error at load.

### R13 — Discoverability `[proposed]`
- R13.2 `[proposed]` A JSON Schema per `schema_version` is exported from the P1 introspection data: per class the field names/types, preset names as enums, provided fact names; a `# yaml-language-server: $schema=` line at the top of a file gives editor completion and validation.
- R13.3 `[proposed]` CLI: `hisim config describe <class>` (fields, presets and what each pins or leaves sized, sizable fields with facts and cardinality, facts provided) and `hisim config facts <energy_system>` (every provided fact with its providers, every consumer with its resolved or missing source — the resolution report before a run).
- R13.4 `[proposed]` Error messages list the valid alternatives (candidates, known presets, known fields).

### R14 — Groups `[decided 2026-08-25]`
- R14.1 `[decided 2026-08-25]` `groups: {name: {enabled: bool, components: {…}}}`; a component is in at most one group; ungrouped components are always on; flat (no nesting).
- R14.2 `[decided 2026-08-25]` Groups are not namespaces (R1.3).
- R14.3 `[decided 2026-08-25]` **Off rule:** a disabled group's components, every `inputs` item pointing at them (wherever written) and every `sizing_sources` reference to them are removed before resolution. A scalar reference left without target → error; a list shrinks (possibly to `[]`) and the shrink is recorded (R8.2). Uniqueness (R4.3) is evaluated over the enabled set.
- R14.4 `[decided 2026-08-25]` **Identity:** for any file and group X, `realize(X disabled) == realize(X and all references to it deleted by hand)` byte for byte.

### R15 — Exclusive variants `[decided 2026-08-28; re-answers Q-P2.2]`
- R15.1 `variants: {name: {selected: <option>, options: {<option>: {components: {…}}}}}`; exactly one option of a variant is live. Flat: an option holds components only — no nested groups, no nested variants.
- R15.2 **Alternative worlds, not overrides.** Every option is complete in itself: a component the variant touches is written out in full in each option that has it, and never at the top level as well. No merging, no partial override, no fallback definition. One component name may repeat across the options of one variant precisely because only one of them ever exists — which is what lets two options wire the same component differently (the meter fed by the EMS total, or fed directly).
- R15.3 **Selection rule:** unselected options are removed exactly as a disabled group is (R14.3) — their components, every `inputs` item pointing at them and every `sizing_sources` reference to them go, with the same scalar-error/list-shrink treatment and the same record (R8.2). The selected option's components then join the top level, so nothing downstream of expansion sees a variant at all.
- R15.4 **Identity:** for any file, variant X and option O, `realize(X.selected = O) == realize(the file with O's components written at the top level and the whole X block deleted)` byte for byte.
- R15.5 Rejected at load, each message naming what it found: `selected` not among the options (the message lists them); an empty `options` mapping; one component name in two different variants; one component name in both the top level and a variant option; a variant name colliding with a group or a component.
- R15.6 Groups and variants stay two constructs with one sentence each — a group is an independent on/off add-on, a variant an exclusive choice. Neither is expressed through the other, and there is no relation between them: no `requires`, no `enabled: not X`. Exclusivity lives in the shape of the file; the loader never solves a constraint (E1).
- R15.7 A realized record carries no `variants` key (the selection is resolved); the audit companion (R8.2) records each variant's selection and the components it contributed.
- R15.8 `hisim energy-system facts <file>` reports groups and variants together as the consumer knob surface: a boolean per group, an option name per variant (P5, UC4).
- R15.9 `[added 2026-08-31, from implementing R15]` **Two namespaces, deliberately.** *Validation* works over the **declared** set — every entry of every option, selected or not — so an entry in a losing option must still be well formed. *Resolution* works over the **selected** set. The distinction is forced: with `direct_metering` selected, the four bare `- ems` items name a component that exists only in the losing option, and a selection-aware namespace at load time would **reject** them where R15.3 requires them to be **dropped**. Where one name carries an entry in two options, both are validated.
- R15.10 `[added 2026-08-31, from implementing R15]` **Where the selected components land.** R15.4's byte-for-byte identity needs a position: the selected option's components are appended after the ungrouped components, variants in document order, each option's components in their own order, and the expanded file carries `variants: {}`. Without a stated convention the identity claim is not testable.
- R15.11 `[open 2026-08-31]` A variant with exactly one option is currently accepted — it decides nothing, and R15.1 says "exactly one is live", not "at least two exist", while the empty-`options` refusal text says "at least two". Either R15.1 gains the minimum or the refusal text drops the phrase; the implementation follows R15.1 as written.

The shape, written out on the case that asked for it — a house with an EMS and a battery, or a bare meter:

```yaml
variants:
  electricity_management:
    selected: ems_and_battery
    options:

      ems_and_battery:
        components:
          battery:   { class: …, preset: sized_to_pv, sizing_sources: {…} }
          ems:       { class: …, preset: optimize_own_consumption, inputs: [ … ] }
          meter:
            class: hisim.components.electricity_meter.ElectricityMeter
            preset: standard
            inputs:                      # one feed: the EMS has already netted everything
              - from: ems.TotalElectricityToOrFromGrid
                tags: [ELECTRICITY_PRODUCTION]
                weight: 999

      direct_metering:
        components:
          meter:
            class: hisim.components.electricity_meter.ElectricityMeter
            preset: standard
            inputs:                      # no EMS to net them: every participant feeds the meter
              - { from: pv_south,  tags: [ELECTRICITY_PRODUCTION],                weight: 999 }
              - { from: occupancy, tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLED],  weight: 999 }
              - { from: heat_pump, tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLED],  weight: 999 }
```

`meter` is written out in both options because the two worlds wire it differently, and repeating the name is
legal precisely because only one option ever exists (R15.2). With `direct_metering` selected, the `- ems`
items on the building, the heat-distribution controller and the two heat-pump controllers are dropped by
R15.3, exactly as R14.3 drops references into a disabled group.

**In the mockups since 2026-08-31 (P2.1).** `energy_system_mockup.yaml` is an executable fixture — AC-P2.1
requires every mockup to load and validate — so it gained this block in the same change that taught the loader
to read it (plan §P2.1). Its `battery_and_ems` group and its top-level `meter` are now the
`electricity_management` variant with the two options above; the example here is the abbreviated form of what
the mockup carries.

### Quality
- RQ2 `[proposed]` `schema_version: 3` is mandatory; other values are rejected with a message naming the supported version.
- RQ3 `[dropped 2026-08-26, owner]` ~~Load + validate + resolve performs no filesystem I/O~~ — resolution folds the Building's contributions, which read the TABULA catalogue; the guarantee cannot hold and validation speed is not a hard requirement. AC-P2.9 is withdrawn.
- RQ4 `[proposed]` A file survives `load → dump` unchanged (key order, list style), so programs can edit it (P5).

## 9. Constraints, Invariants and Assumptions

- C-P2.1 `[given]` Golden parity for concrete inputs (E7).
- C-P2.2 `[proposed]` Single-zone Building (epic C2): UC3's per-apartment values stay explicit; the format needs no change when per-unit facts arrive.
- C-P2.3 `[proposed]` Aggregator semantics (tags, weights, dispatch, derived port names v2 §4.6) are inherited unchanged from `json_v2`.
- C-P2.4 `[proposed]` The v1 executor keeps working until P5 removes it; v3 does not read v1 or v2 files.
- C-P2.5 `[re-decided 2026-08-28]` Groups express orthogonal add-ons only; mutually exclusive alternatives are **variants** (R15), and there is still no inter-group or inter-variant `requires`. One split stays at the level of whole files rather than becoming a variant: the **heating system**, where the generator, its controllers, its storages and their wiring differ throughout — one base file per heating system (P3 R6, P5). Inside such a file, exclusive sub-choices — metering with vs. without an EMS, battery wiring — are variants. Which of the two a given alternative deserves stays authoring guidance, enforced by review.
- A1 `[proposed]` v2 fixtures are regenerated in v3 style rather than migrated.
- C-P2.6 `[proposed; agenda C10]` The v2 fixtures spell the UTSP connector's nested `JsonReference`s in snake_case (`household: {name, guid: {str_val}}`) while the utspclient dataclass fields are `Name`/`Guid`/`StrVal`; they silently deserialise to empty references, invisible only because the fixtures run on the predefined profile. The v3 fixtures must spell them correctly and one local-LPG run must verify it (own commit, before/after evidence).
- A2 `[proposed]` `yaml.safe_load` with a duplicate-key hook is an acceptable loader; `ruamel.yaml` write-only for comments (v2 A4) if Q8 confirms.

## 10. Acceptance Criteria

| ID | Criterion | Verifies |
|---|---|---|
| AC-P2.1 `[amended 2026-08-26]` | All three mockups load, validate, expand groups and resolve against the enabled set; **UC1 additionally builds and runs end to end** — P2 converts the four classes UC1 needs (Weather, UtspLpgConnector, GenericBoilerController, ElectricityMeter) to presets/constructors. UC2/UC3 carry a pinned set of expected unknown-preset errors that shrinks as P4 converts classes; their end-to-end run is a P4 acceptance criterion. | R1, R2, R3, R6, R12, RQ2 |
| AC-P2.2 | UC1 contains no `sizing_sources`; deleting the battery's line in UC2 fails with the quoted candidate error; deleting the EMS list fails likewise; `[]` on the EMS resolves. | R4.3, R4.4, R7 |
| AC-P2.3 | Identity test over every (mockup, group) pair passes byte for byte; with `backup_heater` disabled in UC2 the three forced `sizing_sources` may be removed without error. | R14.3, R14.4, R4.3 |
| AC-P2.4 | Re-running each realized record yields an identical realized record and identical results; its audit shows nothing sized; the `ev` group is absent from UC2's record. | R8.1, R8.2, C-P2.1 |
| AC-P2.5 | One test per hard-error condition in R7 and R2.3/R3.6, each asserting the message names the offending entry and (where applicable) the valid alternatives. | R7, R13.4 |
| AC-P2.6 | The exported JSON Schema rejects an unknown preset and an unknown field for every converted class; `describe` and `facts` produce the listed content for the mockups. | R13.2, R13.3 |
| AC-P2.7 | `dump(load(file)) == file` for the three mockups (after one canonicalising pass). | RQ4, R11 |
| AC-P2.8 | A file with an absolute path, a duplicate key, or `schema_version: 2` is rejected at load; a `*.json` file is rejected as an unsupported format. | R7, R12, RQ2, R15 |
| AC-P2.9 | *withdrawn 2026-08-26 with RQ3* | — |
| AC-P2.10 | A component with no consumers produces a warning line in the resolution report, not an error. | R2.4 |
| AC-P2.11 | A nested group, a component listed in two groups, and a grouped component whose name collides with an ungrouped one are each rejected at load with the names involved. | R14.1, R14.2 |
| AC-P2.12 | A reference containing `[*]` or a relative path in `inputs` or `sizing_sources` is rejected at load. | R4.8 |
| AC-P2.15 | UC2's `weather`, `occupancy` and `building` build through their constructors; a wrong constructor name or argument fails at load listing the valid parameters; the realized record shows them as full `config` blocks; `dump(load())` preserves the constructor form for hand-written files. | R3.8, R1.4 |
| AC-P2.14 | A file setting an enum-typed sizable field yields the enum member after load (`is` comparison), for every such field in the converted classes. | R3.7 |
| AC-P2.16 | A PV + battery + EMS + meter file whose battery feed carries `dispatch: {target_input: LoadingPowerInput}` builds, wires `ems.DispatchTobattery_LoadingPowerInput -> battery.LoadingPowerInput`, runs one day and writes results; the same file with `dispatch: {}` is rejected for the unfed mandatory input, and with an explicit wire onto the derived port for naming a derived port. | R2.5, R2.2 |
| AC-P2.17 | The identity test covers every (mockup, variant, option) triple byte for byte. P2.1 moves UC2's `battery_and_ems` group and its meter into an `electricity_management` variant and pins both directions: with `direct_metering` selected no EMS feed survives on the meter and the four `- ems` items are gone, with `ems_and_battery` selected no direct feed does. | R15.4, R15.3 |
| AC-P2.18 | Each R15.5 rejection is raised at load naming the offending names; an unknown `selected` lists the options the variant has. | R15.5 |
| AC-P2.19 | Once P2.1 has moved it, UC2's realized record has no `variants` key and its `meter` sits at the top level and its audit names the selection and the components it brought; re-running the record reproduces the run (R8.1) with the variant already resolved. | R15.7, R8.1 |
| AC-P2.13 | If Q8 is confirmed: every sized value in a realized record carries a provenance comment; stripping all comments and re-running yields the identical result (comments are not load-bearing). | R8.3 |

## 11. Open Questions and Decisions

**Answered**

| ID | Question | Blocks | Status |
|---|---|---|---|
| Q-P2.1 | Where does a wire *into* a grouped component from an ungrouped source live? | — | `[answered 2026-08-25]` at the consumer, always (R2.1) |
| Q-P2.2 | Metering with vs. without EMS (UC2 O2): separate base files or a group? | C-P2.5, UC4, R15 | `[answered 2026-08-25]` separate base files; a group cannot express "enabled iff another is off" · `[re-answered 2026-08-28]` a **variant** (R15): exclusivity is carried by the shape of the file, not by a constraint the loader must solve. The base-file split survives for the heating system alone |
| Q-P2.3 | Uniqueness over the whole file or the enabled set (UC2 O3)? | R4.3, R14.3 | `[answered 2026-08-25]` enabled set |

`[proposed]` items not listed below (R1.1–1.2, R1.4, R2.2–2.4, R3.1–3.2, R3.6, R4.8, R6, R7, R8.1–8.2, R12, R13.2–13.4, RQ2–RQ4, C-P2.2–2.4) are confirmed by silence at review. EQ1/EQ2 in the epic also affect this document.

**Open**

**Q8 — Does the realized record carry provenance as YAML comments next to each value, in addition to the machine-readable audit companion?** · blocks R8.3, A2, AC-P2.13
*Context.* Decided on `json_v2` on 2026-08-20 (owner, `roadmap/design_review_questions.md` A4 extension): the realized record is YAML and each sized value gets an end-of-line comment such as `water_mass_flow_rate_in_kg_per_second: 0.27   # sized from HeatDistributionController`; comments are rendered from the audit data, never parsed back, only in generated files; the emitter is `ruamel.yaml` as a write-only dependency (the load path stays `yaml.safe_load`). The emitter exists on `json_v2`. What changed since: sizing sources are now explicit references, so the comment would read `# sized from pv_south.pv_peak_power_in_watt = 4200.0`, and disabled groups leave no trace in the record (R14), so "group X was off" can only live in the audit companion.
*Options.* (a) **Keep** — one extra dependency (`ruamel.yaml`), the emitter ported; a human reading a realized record sees where every number came from without opening the audit file; re-running a record with comments stripped must give the same result (AC-P2.13). (b) **Drop** — the audit companion is the only provenance; realized records stay plain YAML writable with `yaml.safe_dump`; readers cross-reference two files.
*Recommendation.* (a), unchanged from the `json_v2` decision — the main audience of a realized record is a person checking a run, and the cost is a write-only dependency that already exists on the branch.
*Decision.* `[decided 2026-08-26, owner]` **(a)** keep — built as new work in P2 (PR-4): nothing exists to port (the `json_v2` branch has no YAML code; its only free text is the top-level `description`, kept as a field). PyYAML and ruamel.yaml (write-only) become declared dependencies.

**Q-P2.4 — Do the `describe <class>` and `facts <energy_system>` CLI commands belong to P2 or to the P4 sweep?** · blocks R13.3, AC-P2.6, plan §P2/§P4
*Context.* P1 delivers the introspection *data* (fields, presets, sizable fields with facts and cardinality, facts provided — R13.1). The CLI on top is roughly 100 lines. It is useful in two places: for authors of energy-system files (P2's audience) and as the per-batch review tool in P4 ("does `describe` show the right presets and facts for every converted class?" — plan B8).
*Options.* (a) **P2** — available when the sweep starts, so each P4 batch is reviewed with it; P2 grows by the CLI and its tests. (b) **P4** — P2 stays format-only; the first sweep batches are reviewed by reading code; the CLI arrives mid-sweep.
*Recommendation.* (a); the sweep is the larger review burden and the tool is small.
*Decision.* `[decided 2026-08-26, owner]` P2, as **nested nouns**: `hisim energy-system describe <class>`, `hisim energy-system facts <file>`, `hisim energy-system run <file> <simulation>`; `hisim/cli.py` is created to repair the dangling `hisim=hisim.cli:main` entry point, leaving room for other command families (`hisim renovisor …`).

**Q-P2.5 — What happens to the ~50 v1 `*.scenario.json` files and the `system_setups/` directory name?** · blocks C-P2.4, RQ2, plan §P3/§P5
*Context.* `system_setups/` holds ~50 Python setups and their v1 JSON twins, regenerated by CI (`.github/workflows/scenario-json-freshness.yml`). v3 does not read v1 or v2 files (C-P2.4). P3 records every Python setup into a v3 `*.energy_system.yaml`; after that the v1 JSONs describe the same systems twice. The directory name says "setup" and the file suffix says "scenario", both terms this epic retires.
*Options.* (a) **Retire in P5** — v1 JSONs and their freshness workflow are deleted once every setup has a recorded v3 file and the golden suites pass on those; `system_setups/` is renamed `energy_systems/` at the same time; no converter is written. (b) **Convert** — write a v1 → v3 converter so the JSONs migrate; keeps two representations alive until the Python setups are removed too. (c) **Rename directory now, in P2** — v3 fixtures land in `energy_systems/` from the start; v1 files stay where they are until P5.
*Recommendation.* (c) for the directory (so no v3 file is ever created under the old name) and (a) for the files: the recorded v3 files *are* the conversion, a converter would be throw-away code.
*Decision.* `[decided 2026-08-26, owner]` **Neither rename nor retire now.** `system_setups/` keeps the Python setups (and their v1 JSON twins) for the coming months; new v3 files live in a new top-level directory **`energy_systems/`** (`*.energy_system.yaml`). Retirement of `system_setups/` and the v1 freshness workflow is a later decision (after P3/P5), no v1→v3 converter.

**Q-P2.6 — How does a file instantiate a class whose defaults are an identifier lookup (Weather by location, Building by TABULA code, occupancy by LPG household) when the identifier is not one of the presets?** · blocks R1.4, R3.8
*Proposed answer* `[proposed 2026-08-25, after P1 Q-P1.6 was decided]`: an explicit **`constructor:` entry** naming one of the class's declared constructors with plain-value arguments, mutually exclusive with `preset`, followed by `config` overrides — R3.8; shown for all three classes in `energy_system_mockup.yaml` (weather `for_location`, occupancy `for_household`, building `for_tabula_code`) and for the building and three occupancies in `energy_system_mockup_mfh.yaml`. Rejected: a complete hand-written `config` (long, and the constructor logic would have to be re-typed by the author); an implicit canonical preset when `preset` is omitted (an inference, E1); constructor arguments as `config` overrides on `standard` with dependents re-derived by law (works only after Q-P1.4 and hides that a constructor ran).
*Decision.* `[decided 2026-08-26, owner]` R3.8 accepted as proposed: `constructor: {<for_name>: {<arg>: <value>}}`, exactly one of `preset`/`constructor`, class side = `@constructor` classmethods (decided in P1 the same day).

## 12. Glossary

See the epic. P2-specific: **aggregator feed** — an `inputs` item with tags/weight into an EMS or meter (v2 "dynamic connection"); **base file** — a checked-in energy system per heating system that consumers override; **canonical style** — R11.
