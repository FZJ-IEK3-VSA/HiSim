# P1 — Sizing kernel: implementation specification

**Status:** draft · **Date:** 2026-08-25
**Implements:** `roadmap/declarative_energy_systems/p1_sizing_kernel_requirements.md` (revision 2026-08-25; Q-P1.2–Q-P1.8 decided, Q-P1.9 open) under the epic `roadmap/declarative_energy_systems/epic.md` (E1–E8)
**Author(s):** assistant (all items `[proposed]` unless tagged) · **Reviewers:** HiSim core team
**Branch / PRs:** `config_presets` (rebased on PR #582 `config_base_move`); planned: PR-A kernel rework, PR-B typed presets, PR-C pilots + tests (see §10)
**Base evidence:** branch `config_presets` as of 2026-08-25 — `hisim/config/{laws,context,sizing,presets,contributions,engine,report}.py` (294/114/308/109/77/470/197 lines), `tests/test_sizing.py` (330), `tests/test_sizing_engine.py` (456), pilots in `generic_boiler.py`, `heat_distribution_system.py`, `controller_l2_energy_management_system.py`

**What a reviewer must decide here:**
1. §3.2 / §5.1 — the binding rule as `resolve_all(configs, sources=…)` with providership from *declarations over the given set* (R4.3, Q-P1.3).
2. §3.3 / §5.2 — sibling reads via a `Self.<FIELD>` law term evaluated against the config under resolution, with per-config field ordering (Q-P1.4).
3. §3.4 / §5.3 — typed presets as descriptor attributes on a per-class `Presets` subclass, builders taking the instance name (Q-P1.5, R3.5). Alternatives in §4.
4. §3.5 — many-cardinality as a declared-but-unimplemented term (`Many(...)` → `NotImplementedError`) (R4.4, EQ2).
5. §10 — three PRs in the order kernel → typing → pilots, or one.

---

## 1. Summary

P1 keeps the design-B kernel on `config_presets` — `AUTO`, `sized_field`, the law algebra, `Catalog` provenance, the fixed-point engine and its `ResolutionReport` — and replaces exactly one thing: how a consumer's fact is bound to a provider. The three-way scope (`FactScope.GLOBAL`/`CONNECTED`, adjacency, pre-seed) is deleted; in its place every provider declared in the resolved set is addressable as `<name>.<fact>`, a bare fact resolves iff exactly one such provider exists, and an explicit `sources` mapping decides otherwise (R4.3, E2). Two additions ride along because the decided questions demand them: laws may read sibling fields of their own config through a `Self` term with intra-config ordering (Q-P1.4), and preset access becomes statically typed with the instance name supplied at build time instead of inside the preset (Q-P1.5, R3.5). Many-cardinality is declared in the algebra and left unimplemented (EQ2). An introspection module exposes what P2's schema export, CLI and the P4 sweep need (R13.1). The risky parts are the typing mechanism (mypy must reject a typo without a plugin — §11 R-1) and the ordering change inside `resolve_config` (§3.3).

## 2. Requirements coverage matrix

| Req / AC | Design element | Code location (planned) | Test | Status |
|---|---|---|---|---|
| R3.1 presets | §3.4 `Presets` subclass, `Preset` descriptor | `hisim/config/presets.py` | T-9 | planned |
| R3.3 AUTO guard | unchanged `Component.__init__` check | `hisim/component.py`, `hisim/config/sizing.py:describe_auto_fields` | T-10 | exists |
| R3.4 wire-format names | §5.3 names; contract test pins pilot names | `tests/test_config_contracts.py` | T-9 | planned |
| R3.5 no name in preset | §3.4 builder signature `(name) -> ConfigT` | `presets.py`, 3 pilots | T-9 | planned |
| R4.1 no guessing | §3.2 lookup table, E-02/E-03 | `engine.py:_look_up_fact` | T-2, T-3 | planned |
| R4.2 nothing to write | §3.2 unique-provider path | `engine.py` | T-1 | planned |
| R4.3 binding rule | §3.2, §5.1 `resolve_all(configs, sources)` | `engine.py` | T-1–T-4, T-11 | planned |
| R4.4 cardinality hook | §3.5 `Many` term, E-07 | `laws.py`, `context.py` | T-6 | planned |
| R4.5 law in class | §5.1 `sources` value validation E-05 | `engine.py` | T-4 | planned |
| R4.6 no root, acyclic | unchanged registration/cycle check; §3.2 | `engine.py:validate` | T-5 | exists (re-tested) |
| R4.7 provenance incl. `self.` | §7 `SizingRecordEntry.inputs`, `FactLookupRecord` | `sizing.py`, `report.py` | T-7 | planned |
| R13.1 introspection | §3.6 `introspection.py` | `hisim/config/introspection.py` | T-8 | planned |
| RQ1 locality | no registry; class attributes only | — | T-12 | planned |
| RQ2 layering | unchanged import rule | `hisim/config/__init__.py` docstring; import-linter check | T-13 | exists |
| RQ3 contract test | §9 T-11 | `tests/test_config_contracts.py` | T-11 | planned |
| C-P1.1 pilots unchanged | §10 golden fixtures | pilot tests | T-1 | planned |
| C-P1.2 typed context | §3.5: scalars stay dataclass fields | `context.py` | T-6 | planned |
| Q-P1.2 keep clamps | no change | `laws.py` | existing | exists |
| Q-P1.3 `None` counts | §3.2 providership from declarations | `engine.py` | T-3 | planned |
| Q-P1.4 sibling reads | §3.3 `Self` term, field ordering | `laws.py`, `sizing.py` | T-14 | planned |
| Q-P1.6 lookup classes | §3.4: `standard` + constructors are plain classmethods; no kernel change | pilots (Building) | T-9 | planned |
| Q-P1.7 notes | §5.2 `sized_field(note=)`, `Preset(note=)` | `sizing.py`, `presets.py` | T-8 | planned |
| Q-P1.8 constant laws marked | §3.6 `SizableFieldInfo.kind = CONSTANT` | `introspection.py` | T-8 | planned |
| AC-P1.1 | T-1 · AC-P1.2 T-2 · AC-P1.3 T-3 · AC-P1.4 T-7 · AC-P1.5 T-5 · AC-P1.6 T-6 · AC-P1.7 T-9 · AC-P1.8 T-8 · AC-P1.9 T-11 · AC-P1.10 T-13 · AC-P1.11 T-10 · AC-P1.12 T-9 · AC-P1.13 T-4 · AC-P1.14 T-12 · AC-P1.15 T-14 | | | planned |
| **D1** `[proposed]` derived: a config under resolution must expose which of its sizable fields are already concrete (pinned) so sibling terms can read them | §3.3 | `sizing.py` | T-14 | — from Q-P1.4 |
| **D2** `[proposed]` derived: `resolve_all` needs each config's instance name before resolution (for `<name>.<fact>`), so configs enter the engine already named | §3.2, §5.1 | `engine.py` | T-1 | — from R4.3 + R3.5 |

## 3. Design overview

### 3.1 Concepts

- **Fact** — a `SizingContext` field name; provided by a config class through `SIZING_CONTRIBUTIONS`, read by a law through a `Size.<FACT>` term. Unchanged.
- **Provider** — a config *instance* in the resolved set whose class declares a fact. Identified by its instance name (`config.component_id.name`).
- **Qualified fact** — `"<name>.<fact>"`, the only way to point at a particular provider.
- **Sources mapping** — `sources: {consumer_name: {fact: qualified | [qualified, …]}}`; the Python form of the file's `sizing_sources`.
- **Sibling term** — `Self.<FIELD>`: a law input that is another field of the same config, read at its final value.
- **Preset** — a named builder `(name: str) -> ConfigT` declared as a typed attribute of the class's `Presets` subclass; still fresh-instance, still provenance-stamped.

```
configs (named) ─► register: needs = laws.facts_read ∪ contributions.reads
                              offers = SIZING_CONTRIBUTIONS.facts  (declared, per instance)
                ─► validate: every need providable by ≥1 offer or seed; no cycle
                ─► sweep until fixed point:
                     for each unresolved config:
                        for each needed fact: bind (§3.2) → value or wait
                        resolve_config(config, ctx):  order fields by Self-deps (§3.3),
                                                      evaluate laws, record inputs
                        fold contributions → provider pool[name][fact]
                ─► results (input order) + ResolutionReport
```

### 3.2 The binding rule (R4.3, Q-P1.3, E2) `[decided requirement; proposed mechanism]`

Per consumer *c* and needed fact *f*, with `P(f)` = names of configs in the set whose class **declares** *f* (values irrelevant — a `None` value still counts, Q-P1.3) and `S = sources.get(c, {}).get(f)`:

| reader | `S` | `|P(f)|` | result |
|---|---|---|---|
| one | absent | 0 | seed value if the seed context carries *f*, else **E-01** unprovided |
| one | absent | 1 | that provider's value (wait if not yet computed) |
| one | absent | ≥ 2 | **E-02** ambiguous, lists `sorted(P(f))` and the paste-ready mapping |
| one | `"p.f"` | any | provider `p`, which must be in `P(f)` else **E-03**; value `None` → **E-04** null provider |
| one | a list | any | **E-05** cardinality mismatch |
| many | absent | 1 | one-tuple (hook; raises **E-07** at evaluation until implemented) |
| many | absent | 0 or ≥ 2 | **E-02** variant: "write `sources[c][f] = [...]` (`[]` for none)" |
| many | list / `[]` | any | tuple in written order (hook) |

The seed context (`SizingContext.for_building(...)` in Python setups) is a provider named `<seed>` and participates in `P(f)` like any declared provider, so a setup that seeds the building facts *and* passes a Building config gets E-02, not a silent preference. Providership is computed once at registration from declarations; values arrive during sweeps. Readiness: a consumer resolves in the first sweep in which every bound provider has folded its contributions; no progress → the existing deadlock error with the report.

**Deleted:** `FactScope`, `contribution.scope`, `SizingFactEngine.__init__(adjacency=, preseeded_facts=)`, `_declared_connected_providers`, `_connected_facts` split, `LookupMode.{PRESEED, CONNECTED_ADJACENT, CONNECTED_POOL, GLOBAL}`, `OverrideRecord`. **Added:** `LookupMode.{UNIQUE, EXPLICIT, SEED}`, `_providers: Dict[fact, Set[name]]`, `_pool: Dict[name, Dict[fact, value]]`.

### 3.3 Sibling reads and intra-config ordering (Q-P1.4, D1)

`Self.<FIELD>` is a `_SelfTerm(field_name)` law node. `SizingLaw.evaluate(ctx, own)` gains a second argument: a read-only view of the config under resolution exposing final values. `resolve_config`:

1. Collect sizable fields; for each `AUTO` field determine its law (class law, or per-preset law if the field value *is* a `SizingLaw`).
2. Build the intra-config dependency graph from `law.fields_read()`; a field that reads a non-`AUTO` sibling depends on nothing (the value is final); a field that reads an `AUTO` sibling depends on it. Cycle → **E-06** (also caught statically by T-11 over class laws; per-preset laws are checked at `Presets` construction).
3. Evaluate in topological order; after each field, the `own` view exposes the new value. `SizingRecordEntry.inputs` gets `("self.<field>", value)` entries.
4. `facts_read()` (external) is unchanged and still drives the engine's needs; `fields_read()` is new and local.

The pellet preset becomes `minimal_thermal_power_in_watt = Self.MAXIMAL_THERMAL_POWER_IN_WATT * (1 / 12)`; the PV class law reads `Self.SHARE_OF_MAXIMUM_PV_POTENTIAL` (a plain field), which closes the B6 trap without the per-preset law builder.

### 3.4 Typed presets without names (Q-P1.5, R3.5, Q-P1.6)

```python
class GenericBoilerPresets(Presets["GenericBoilerConfig"]):
    condensing_gas = Preset(lambda name: GenericBoilerConfig(component_id=ComponentID(name=name), energy_carrier=lt.LoadTypes.GAS, boiler_type=BoilerType.CONDENSING))
    condensing_gas_12kw = Preset(lambda name: ..., note="nominal catalogue device")
    ...

@dataclass
class GenericBoilerConfig(ConfigBase):
    presets: ClassVar[Type[GenericBoilerPresets]] = GenericBoilerPresets
```

- `Preset[ConfigT]` is a descriptor; `GenericBoilerConfig.presets.condensing_gas` is typed `Preset[GenericBoilerConfig]` and **a misspelled attribute is a mypy `attr-defined` error** — no plugin, no `__getattr__`. Calling it, `presets.condensing_gas("boiler")`, builds a fresh, provenance-stamped instance named `boiler`.
- `Presets.__init_subclass__` collects the descriptors in declaration order → `names()`, `canonical`, iteration, and the note per preset (Q-P1.7). The reserved-name check moves there.
- Lookup classes (Q-P1.6) need nothing from the kernel: `standard = Preset(lambda name: WeatherConfig.for_location(name, LocationEnum.AACHEN))` plus the classmethod constructor; the constructor is *not* a preset and is not discovered as one (RQ3 contract test enumerates `Presets` subclasses only).
- `Catalog` is deleted; `preset_provenance()` stays (stamped by `Preset.__call__`).
- For Q-P1.9 the pilots keep their current names until the decision lands, except `BuildingConfig.presets.german_single_family_home` → `standard` (decided via Q-P1.6).

### 3.5 Cardinality hook (R4.4, EQ2)

`Many(Size.PV_PEAK_POWER_IN_WATT)` is a `_ManyTerm` whose `facts_read()` returns `(fact, Cardinality.MANY)`; `evaluate` raises **E-07** `NotImplementedError("many-cardinality is declared but not implemented; see plan parking lot")`. The engine's binding table above already distinguishes one/many so that the error is E-02's many-variant, not a wrong scalar binding. `SizingContext` stays a frozen dataclass of typed scalars (C-P1.2); the future tuple side-table is not added now.

### 3.6 Introspection (R13.1, Q-P1.8)

`hisim/config/introspection.py` — pure functions over a config class, no I/O, no component imports:

```python
describe_config(cls) -> ConfigDescription(
    fields: Tuple[FieldInfo, ...],            # name, type, default, sizable: bool
    presets: Tuple[PresetInfo, ...],          # name, canonical: bool, pinned: Tuple[str,...], auto: Tuple[str,...], note
    sizable_fields: Tuple[SizableFieldInfo, ...],  # name, law: str, facts_read: Tuple[(fact, Cardinality)], fields_read, kind: LAW | CONSTANT, note
    facts_provided: Tuple[str, ...])
```

`kind = CONSTANT` marks constant laws ("author default (constant law)", Q-P1.8). P2 renders this into the JSON Schema, the CLI and the realized-record comments.

## 4. Alternatives considered

| Design point | Chosen | Rejected — consequence |
|---|---|---|
| Binding rule scope | providership over the given set, declarations only | keep adjacency hybrid — the reviewed defect (epic, superseded); drop `None` providers — re-binds on a feature flag (Q-P1.3 b, decided against) |
| Seed context | a provider named `<seed>` in `P(f)` | seed silently wins — an inference (E1) |
| Sibling reads | `Self` term + per-config topological order | law re-evaluation (status quo) — wrong when the sibling is pinned; plain-siblings-only (`reads_own=`) — leaves the pellet case on re-evaluation (Q-P1.4 a0) |
| `evaluate` signature | `evaluate(ctx, own)` | merge `own` into `ctx` as pseudo-facts — pollutes the fact vocabulary and the report; make `Self` read a thread-local — hidden state |
| Typed presets | descriptor attributes on a `Presets` subclass | `Catalog[ConfigT]` generic with `__getattr__` — typed return but typos pass; `get(name: Literal[...])` — Literal must be maintained by hand per class; mypy plugin — tooling burden |
| Instance name | builder argument `(name) -> ConfigT` | post-construction injection (`replace(component_id=…)`) — presets would still need a placeholder name; making `component_id` optional — every component reads it |
| Many hook | declared term raising E-07 | not declared at all — laws for the MFH case could not even be written; full implementation — EQ2 |
| Introspection | separate module | on `ConfigBase` methods — bloats the base every config inherits |

## 5. Public surface

### 5.1 APIs (`hisim.config`)

```python
def resolve_all(configs: Sequence[ConfigBase], seed: SizingContext | None = None,
                sources: Mapping[str, Mapping[str, str | Sequence[str]]] | None = None) -> List[ConfigBase]
```
Contract: input order preserved; every config must carry a unique `component_id.name` (E-08 otherwise); raises `ConfigSizingError` for E-01…E-06, `SizingError` for E-08. **Changed:** `adjacency`, `preseeded_facts` removed. `SizingFactEngine` keeps `report`, `resolution_order`.

```python
class SizingLaw:  evaluate(self, ctx: SizingContext, own: OwnFields) -> Any   # changed: own
                  facts_read(self) -> Tuple[Tuple[str, Cardinality], ...]     # changed: cardinality
                  fields_read(self) -> Tuple[str, ...]                        # new
class Self:       <FIELD>: _SelfTerm                                          # new, generated per config? no — see §13 DQ1
def Many(term: _FactTerm) -> _ManyTerm                                        # new
def sized_field(*, rule, value_type=None, note: str | None = None, **kw)      # changed: note
class Presets(Generic[ConfigT]):  names() / canonical / __iter__ / notes()    # new (replaces Catalog)
class Preset(Generic[ConfigT]):   __call__(name: str) -> ConfigT; note        # new
def preset_provenance(config) -> str | None                                   # unchanged
def describe_config(cls) -> ConfigDescription                                 # new
```

### 5.2 Declarations on config classes

`SIZING_CONTRIBUTIONS: ClassVar[Tuple[FactContribution, ...]]` — `FactContribution(facts, compute, reads)`; **`scope` removed**. `presets: ClassVar[Type[<Cls>Presets]]`. Sizable fields: `sized_field(rule=…, note=…)`.

### 5.3 Names introduced

`Self`, `Many`, `Cardinality`, `Presets`, `Preset`, `describe_config`, `ConfigDescription`, `FieldInfo`, `PresetInfo`, `SizableFieldInfo`, `LookupMode.{UNIQUE, EXPLICIT, SEED}`. Fact names unchanged (9). Preset names unchanged pending Q-P1.9, except `german_single_family_home → standard`.

### 5.4 Error catalogue

| ID | Condition | Exception | Message template |
|---|---|---|---|
| E-01 | fact needed, nobody declares it, seed lacks it | `ConfigSizingError` | `'{fact}' needed by '{consumer}' is provided by nobody; providers of other facts: …` |
| E-02 | ≥ 2 declared providers, no mapping | `ConfigSizingError` | `'{fact}' needed by '{consumer}' is provided by {a, b}; pass sources={'{consumer}': {'{fact}': '<one of a.{fact}, b.{fact}>'}}` (many: `[…]`, `[]` for none) |
| E-03 | mapping names a non-provider | `ConfigSizingError` | `sources['{consumer}']['{fact}'] = '{p}.{fact}' but '{p}' does not declare '{fact}' (declares: …)` |
| E-04 | bound provider's value is `None` | `ConfigSizingError` | `'{fact}' provided as null by '{p}' (feature off); '{consumer}' cannot size from it` |
| E-05 | mapping shape ≠ law cardinality, or value not a `<name>.<fact>` string | `ConfigSizingError` | `sources['{consumer}']['{fact}']: expected {one|list} reference(s) of the form '<name>.<fact>', got {…}` |
| E-06 | intra-config cycle | `ConfigSizingError` (contract test: `AssertionError`) | `{Cls}: fields {a} and {b} read each other via Self` |
| E-07 | many-term evaluated | `NotImplementedError` | as §3.5 |
| E-08 | duplicate or missing instance name | `SizingError` | `two configs named '{name}' ({ClsA}, {ClsB})` / `{Cls} has no component_id.name` |
| (kept) | deadlock / cycle across configs | `ConfigSizingError` | existing message + `report.render()` |

## 6. Internal structure

| Unit | Responsibility | Change |
|---|---|---|
| `laws.py` | algebra; `_SelfTerm`, `_ManyTerm`, `Cardinality`; `fields_read` | +~90 lines (≤ 400) |
| `context.py` | `SizingContext`, `Size`; no change except docstring | 0 |
| `sizing.py` | `resolve_config` with intra-config ordering and `own` view (`OwnFields`: reads pinned values and resolved-so-far); `sized_field(note=)`; `describe_auto_fields` | +~60 |
| `presets.py` | `Presets`, `Preset`; delete `Catalog` | rewrite, ~120 |
| `contributions.py` | delete `FactScope`, `scope` | −20 |
| `engine.py` | delete scopes/adjacency/pre-seed; `_providers`, `_pool`, binding table; E-01…E-05, E-08 | ≈ −120 net; target ≤ 350 |
| `report.py` | `LookupMode` trimmed; drop `OverrideRecord`; `FactLookupRecord.candidates` kept | −30 |
| `introspection.py` | `describe_config` and its dataclasses | new, ~150 |
| `__init__.py` | exports; layering docstring unchanged | small |
| pilots | drop `scope=`; `Catalog` → `Presets`; builders take `name`; pellet law → `Self` | mechanical |

Dependency direction unchanged: `laws ← context ← sizing ← engine`; `presets` and `introspection` are leaves except for `laws`/`sizing`; nothing imports `hisim.components` (RQ2).

## 7. Data, state and invariants

- `_providers: Dict[str, Set[str]]` (fact → provider names) — built at registration, immutable afterwards.
- `_pool: Dict[str, Dict[str, Any]]` (provider name → fact → value) — grows as contributions fold.
- `OwnFields` — read-only view over a config: `pinned` (non-`AUTO` sizable fields + plain fields) and `resolved` (filled during `resolve_config`).

Invariants:

- **I-1** Providership never depends on resolution order or on values: `_providers` is complete before the first sweep and never mutated. *Checked:* T-3 (same result with shuffled `configs`).
- **I-2** A bare fact binds iff `|P(f)| == 1` (seed included); otherwise an explicit mapping or E-02. *Checked:* T-1, T-2, T-11 property.
- **I-3** The mapping never computes: values are qualified references only. *Checked:* E-05, T-4.
- **I-4** `resolve_config` output is independent of field declaration order; only `Self` dependencies order evaluation. *Checked:* T-14 with two field orders.
- **I-5** Every `SizingRecordEntry.inputs` entry is `(qualified fact | "self.<field>", value)` and the recorded values equal what the law received. *Checked:* T-7.
- **I-6** A preset builder is pure in everything but `name`: `presets.x("a") == replace(presets.x("b"), component_id=…)`. *Checked:* T-9.
- **I-7** `hisim/config/*` imports only stdlib, `dataclasses_json`, `hisim.log`. *Checked:* T-13.
- **I-8** No class declares the same fact name as another unless listed in `INTERCHANGEABLE_PROVIDERS` in the contract test. *Checked:* T-11.

## 8. Error handling

Fail fast at the earliest phase that can know: E-08 at registration; E-01, E-06 at validation (before any value is computed); E-02, E-03, E-05 at first binding; E-04 when the null value is read; E-07 at law evaluation. Nothing is silently skipped, defaulted or preferred — the only non-error condition the requirements sanction is a *warning* for a provider nobody reads (P2 R2.4; the kernel records it in `report.unconsumed`, P2 prints it). The deadlock diagnosis keeps rendering the full report.

## 9. Testing strategy

| ID | Verifies | Kind | Fixture | Failure looks like |
|---|---|---|---|---|
| T-1 | AC-P1.1, R4.2, C-P1.1, I-2 | golden/unit | the three pilots' existing configs and expected numbers | a pilot value changes or a mapping is demanded |
| T-2 | AC-P1.2, R4.1, R4.3 | unit | building + `pv_south` + `pv_east` + battery | no E-02, wrong candidate list, mapping not honoured |
| T-3 | AC-P1.3, Q-P1.3, I-1 | unit + property | boiler + heat pump (DHW off → `None`) + DHW controller + HDS; 20 random permutations | HDS asked to map; result differs across permutations |
| T-4 | AC-P1.13, R4.5, I-3 | unit | mapping with `4200.0`, `"sum(a,b)"`, list for a scalar law | no E-05 |
| T-5 | AC-P1.5, R4.6 | unit | synthetic Weather-like provider → Building → generator → controller (depth 4); two-node cycle | cycle not named |
| T-6 | AC-P1.6, R4.4 | unit | law with `Many(...)`; law reading `X` and `Many(X)` | no E-07; contract test passes the double read |
| T-7 | AC-P1.4, R4.7, I-5 | unit | T-2 with mapping; T-14 pellet | inputs lack qualified source or `self.` entry |
| T-8 | AC-P1.8, R13.1, Q-P1.7/8 | unit | pilot classes | missing constant-law kind, note, cardinality |
| T-9 | AC-P1.7, AC-P1.12, R3.5, I-6 | unit + mypy | pilots; a `.py` snippet with a misspelled preset run through `mypy --strict` in-test | typo passes mypy; builder needs no name |
| T-10 | AC-P1.11, R3.3 | unit | preset with `AUTO` passed to a component | no error, or error without field/law names |
| T-11 | AC-P1.9, RQ3, I-8, E-06 | contract (all classes with `SIZING_CONTRIBUTIONS` / `Presets`) | repo scan | colliding fact name or Self-cycle undetected |
| T-12 | AC-P1.14, RQ1 | unit | a test-only config module with presets/law/contribution | any edit to `hisim/config` needed |
| T-13 | AC-P1.10, RQ2, I-7 | static | `grep`/import-linter | forbidden import or leftover symbol |
| T-14 | AC-P1.15, Q-P1.4, I-4, E-06 | unit | pellet preset pinned max 12000; PV share 0.5; two fields reading each other; same config with fields declared in two orders | min ≠ 1000; share ignored; no cycle error; order-dependent result |

Not tested here: file syntax (P2), many-cardinality behaviour (parking lot), performance (RQ3 at P2 with the 35-component mockup).

## 10. Migration, compatibility and rollout

Nothing here is released; the branch is unmerged. Landing order, each independently reviewable:

1. **PR-A kernel** — `engine.py`, `contributions.py`, `report.py`, `laws.py` (`Self`, `Many`), `sizing.py` ordering; pilots adjusted minimally (`scope=` dropped, pellet law → `Self`); `tests/test_sizing_engine.py` rewritten (T-1–T-7, T-14). Golden: pilot numbers unchanged.
2. **PR-B typed presets + introspection** — `presets.py` rewrite, pilots to `Presets`/`(name)` builders, `german_single_family_home → standard`, `introspection.py`; T-8–T-13. Requires a mypy prototype green *before* the pilots are converted (R-1).
3. **PR-C plan bookkeeping** — `roadmap/declarative_energy_systems/plan.md` §P1 checked off; branch plan/agenda marked superseded; `system_docs/config_defaults_spec.md` §on scopes replaced by a pointer to the requirements.

Deleted: `FactScope`, `Catalog`, `OverrideRecord`, `adjacency`/`preseeded_facts` parameters, `LookupMode` old members, the `sizing_facts` wording in docstrings. Regenerated: pilot scenario JSON fixtures if literal spellings change (expected int→float drift only, `random_findings`).

## 11. Risks and unknowns

- **R-1 Typing mechanism** `[resolved 2026-08-25]`. Prototyped (60 lines, `Preset(Generic[ConfigT])` with `__set_name__`/`__call__`, `Presets(Generic[ConfigT])` collecting attributes in `__init_subclass__`, `presets: ClassVar[Type["BoilerPresets"]]` assigned after the subclass) and checked with the repo's `mypy.ini` (mypy 2.3.0): a misspelled preset is `error: "type[BoilerPresets]" has no attribute "condensing_gaz" [attr-defined]`, a wrong builder argument is `[arg-type]` with the expected `str`, a correct call is typed as the config class; names, canonical, notes and provenance work at run time. No plugin, no stubs. The `Literal` fallback is not needed.
- **R-2 Field ordering regressions.** `resolve_config` moves from declaration order to dependency order; a per-preset law that today relies on declaration order silently changes. Trigger: pilot golden diff. Mitigation: T-1 + I-4 test with reversed declaration order.
- **R-3 `own` view leaks unresolved values.** A law reading an `AUTO` sibling before it is resolved must be impossible by construction (ordering), not by check. Mitigation: `OwnFields.__getattr__` raises on an unresolved `AUTO` field (defensive, covered by T-14).
- **R-4 Seed as provider surprises setups** that both seed and pass a Building. Trigger: E-02 in a converted setup. Mitigation: the message names `<seed>`; the plan's P3 recorder never seeds.
- **R-5 Q-P1.9 lands after PR-B** — names renamed twice. Mitigation: PR-B only renames what Q-P1.6 already decided.

## 12. Code-review guide

**Where to look first.** (1) `engine.py:_look_up_fact` — the binding table §3.2 line by line; (2) `sizing.py:resolve_config` — the ordering and the `own` view (R-3); (3) `presets.py` — descriptor typing and that no builder constructs a `ComponentID` from a literal.

**Invariant checklist**
- [ ] I-1 `_providers` built in `register`, never written elsewhere (grep assignments)
- [ ] I-2 the `|P(f)| == 1` branch is the only bare-name success path; `<seed>` is in `P(f)`
- [ ] I-3 `sources` values are validated as `<name>.<fact>` strings before any lookup (E-05)
- [ ] I-4 topological sort over `fields_read()`; no reliance on `dataclasses.fields` order
- [ ] I-5 `SizingRecordEntry.inputs` filled from the same values passed to `evaluate`
- [ ] I-6 every preset builder uses its `name` argument and nothing else varies
- [ ] I-7 imports in `hisim/config/*` — stdlib, `dataclasses_json`, `hisim.log` only
- [ ] I-8 `INTERCHANGEABLE_PROVIDERS` in the contract test lists exactly `maximal_thermal_power_in_watt`, `minimal_thermal_power_in_watt` (boiler / heat pump / electric / district heating)

**Requirement checklist** — R4.3: E-02 raised with both candidates and a paste-ready mapping (T-2) · Q-P1.3: a `None`-valued provider still forces the mapping (T-3) · Q-P1.4: pinned max 12000 → min 1000 (T-14) · R3.5: `grep "ComponentID(name=\"" hisim/components/{generic_boiler,heat_distribution_system,controller_l2_energy_management_system}.py` finds nothing inside preset builders · R4.4: `Many` evaluates to E-07 and nothing else (T-6) · R13.1: `describe_config` on all three pilots without importing them at module level of `hisim.config` (T-8) · AC-P1.10: `grep -rn "FactScope\|preseeded_facts\|adjacency" hisim/` empty.

**Smells to reject** — any `entry_exists`/`get(..., default)` on a fact pool (silent fallback, R7) · any `if len(providers) > 1: pick …` (guessing, E1) · a `try/except` around `evaluate` that substitutes a value · `component_id=ComponentID(name="…")` inside a `Preset` builder · `from hisim.components` anywhere under `hisim/config/` · a new `SizingContext` field without a `Size` term (test enforces) · a preset named with a number outside a rating suffix (pending Q-P1.9).

**Out of scope for this review** — file syntax and `sizing_sources` parsing (P2); many-cardinality evaluation (parking lot); converting classes beyond the three pilots (P4); the `describe` CLI (P2).

## 13. Open design questions

**DQ1 — How is the `Self` vocabulary spelled per class: generated terms (`Self.MAXIMAL_THERMAL_POWER_IN_WATT`) or a string-keyed factory (`Self("maximal_thermal_power_in_watt")`)?** · blocks §3.3, §5.1
*Context.* `Size.<FACT>` terms are a closed, hand-maintained registry over the 9 `SizingContext` fields with a one-term-per-field test. Sibling fields are per class (dozens of names across 88 classes), so a central registry is impossible; the term must be created per class. A typo in a string key is a runtime error at class import (the field does not exist); a generated attribute would be a mypy error but needs code generation or a metaclass over dataclass fields.
*Options.* (a) `Self("field_name")` — 5 lines, validated at import against `dataclasses.fields(cls)` when the law is bound to a field; typos fail at import, not at mypy. (b) A `Self` object built per class by `__init_subclass__` exposing one attribute per field — mypy-visible only with a plugin or generated stubs. (c) Reference the field object: `Self(GenericBoilerConfig.maximal_thermal_power_in_watt)` — not possible, dataclass fields are not descriptors.
*Recommendation.* (a); import-time validation is one test run away and the alternative needs tooling P1 does not have.

**DQ2 — Does `evaluate` receive `own` as a second positional argument or does `SizingContext` grow an `own` attribute set per resolution?** · blocks §3.3, §5.1, C-P1.2
*Context.* `SizingContext` is a frozen dataclass shared across all consumers of a sweep; `own` is per config. Threading it through `evaluate(ctx, own)` changes the law protocol (all `_Law` subclasses, function laws receive it as a second lambda argument — existing pilot lambdas take one argument). A `dataclasses.replace(ctx, own=view)` per config keeps the one-argument lambdas but puts per-config state into a shared-looking object.
*Options.* (a) `evaluate(ctx, own)`; function laws declared with `reads=` keep `lambda ctx: …` and get `own` only if they declare `fields=(…)` (the resolver inspects the declaration, not the signature). (b) `ctx.own` per config via `replace` — no protocol change; a law reading `ctx.own.x` on a context that has none fails at runtime.
*Recommendation.* (a); the declaration-driven variant keeps existing lambdas untouched and makes sibling reads visible in `describe_config`.

## Amendments (PR-A, 2026-08-25)

Recorded while implementing §10 item 1; the code follows these, not the original text.

- **§13 DQ1** `[decided 2026-08-25]` `Self("field_name")` is validated on the first `resolve_config` of the class (against its dataclass fields) and by the contract test over all classes, not at import time: a `dataclasses.field()` wrapper cannot see its owning class, and an import-time check would need a `ConfigBase` metaclass hook this phase does not budget.
- **§13 DQ2** `[decided 2026-08-25]` `evaluate(ctx, own)`; a function law receives `own` only when declared with `fields=(...)`. `sized_field(...)` accepts `fields=` as well, so an inline function law can read a sibling.
- **§5.3** additional public names: `SizedFieldMetadata` (class-scoped metadata keys `LAW`, `NOTE`, `FIELDS`), `field_notes()`, `OwnFields`/`OwnFieldsView`, `SourceReference`, `ResolutionReport.unconsumed`.
- **§5.4 E-05** also covers a reference whose fact part is not the fact being bound (`sources[c][f] = "Boiler.some_other_fact"`).
- **§8** `ResolutionReport.unconsumed: List[(producer, fact)]` lists provided facts nobody read, including `<seed>` facts; P2 decides whether to warn on seed facts.
- **§6 budget** `engine.py` ≤ 500 lines (was ≤ 350): the binding table plus the error catalogue with full docstrings does not fit below without a `binding.py` split, which §6 does not sanction.
- **§9 T-2 / T-14 fixtures** use `maximal_thermal_power_in_watt` with providers named `pv_east`/`pv_south` and a test-only config with a plain sibling field, because `pv_peak_power_in_watt` and the PV conversion arrive with P4.
- **Open (for PR-B/P2):** a *contribution* whose `reads` hits a two-provider fact raises E-02 with the contributing config as consumer — correct by the rule, untested (no pilot has `reads`); a many-binding never reaches the E-04 null check because E-07 fires first — define when many is implemented.
