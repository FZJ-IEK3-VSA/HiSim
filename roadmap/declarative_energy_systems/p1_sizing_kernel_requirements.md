# P1 — Sizing kernel: presets, AUTO fields and the fact binding rule — requirements

**Status:** accepted (all questions decided 2026-08-25/26; implemented on `config_presets`, PR #586) · **Date:** 2026-08-25
**Author(s):** Noah Pflugradt (owner; `[given]`) · assistant (`[proposed]`, survey)
**Reviewers:** HiSim core team
**Parent:** `roadmap/declarative_energy_systems/epic.md` (principles E1–E8 apply by reference and are not repeated) · **Plan:** `roadmap/declarative_energy_systems/plan.md` §P1
**Related:** `system_docs/config_defaults_spec.md` (design B — solution-design input); branch `config_presets` (`hisim/config/`, 18 commits); `roadmap/declarative_energy_systems/sizing_fact_inventory.md`

**Tags:** refactoring, behavior-change, technical-debt
**Keywords:** sizing law, AUTO, sized_field, Catalog, preset, fact, provider, sizing_sources, cardinality, fact engine

**What a reviewer must decide here:** (1) the binding rule as a Python API is the P1 deliverable, with no file format involved — R4.3; (2) scalar cardinality only, many-cardinality left as a declared hook — R4.4; (3) presets carry no component name — R3.5; (4) nothing — Q-P1.2–Q-P1.8 were decided 2026-08-25, Q-P1.9 on 2026-08-26; EQ1/EQ2 are decided in the epic. P1 is decision-complete.

---

## 1. Abstract

The `config_presets` branch gives component configs named presets (`Catalog`), sizable fields (`AUTO` + a law declared once per field) and an engine that resolves cross-component facts. Review found the engine's fact scoping — a global pool, a connected-adjacent lookup and a flat-pool fallback — too implicit: with two components of one class in a house, which one a consumer sizes from is debatable. P1 replaces that scoping by one explicit rule (epic E2) implemented as a Python API, keeps everything else on the branch, and stays scalar because no reader consumes several providers today. Files are P2's concern.

## 3. Executive Summary

Keep `sized_field`/`AUTO`/`resolve_config`/`Catalog`/`report`; delete `FactScope`, adjacency and the file-level pre-seed; add qualified fact addressing (`<name>.<fact>`), an explicit `sources` mapping per consumer, uniqueness evaluated over the given set, and a contract test on fact names. Presets stop carrying a component name. Introspection data for later tooling is exposed as functions. Affected: `hisim/config/{engine,contributions,context,laws,presets}.py`, their tests, the three pilots' `SIZING_CONTRIBUTIONS` lines. Cost of inaction: P2 would have to spell the old three-way scope into the file format, freezing the reviewed defect as wire format.

## 4. Context and Current Situation

**Current behavior (branch `config_presets`, verified 2026-08-25).**

| Module | Lines | State |
|---|---|---|
| `hisim/config/sizing.py` | 308 | `AUTO` singleton, `sized_field`, `resolve_config`, `sizing_record`, `Component.__init__` guard — keep |
| `hisim/config/laws.py` | 294 | fact term, `*k`, `at_least`, `at_most`, `rounded`, function law with mandatory `reads=` — keep |
| `hisim/config/presets.py` | 109 | `Catalog`, provenance stamp — keep; every preset sets `component_id=ComponentID(name=…)` (~20 lambdas in 3 pilots) — change |
| `hisim/config/contributions.py` | 77 | `FactContribution(facts, compute, scope=FactScope.GLOBAL/CONNECTED)` — scope goes |
| `hisim/config/context.py` | 114 | `SizingContext` frozen dataclass of 9 optional scalars; `Size` terms 1:1 — keep, scalar |
| `hisim/config/engine.py` | 470 | registration, validation, fixed point — keep; `_look_up_fact` hybrid (pre-seed → adjacent → pool → global), `_declared_connected_providers`, `preseeded_facts` — replace |
| `hisim/config/report.py` | 197 | `ResolutionReport`, `LookupMode` (PRESEED / CONNECTED_ADJACENT / CONNECTED_POOL / GLOBAL) — modes shrink |
| tests | 330 + 456 | `test_sizing.py` survives; `test_sizing_engine.py` tests the scope modes — rewritten |
| pilots | — | `generic_boiler.py` (`GenericBoilerConfig` only), `heat_distribution_system.py` (`HeatDistributionConfig` only), EMS; 1 `FactScope.` usage in components |

**Counted (inventory §1–§4):** 7 primary facts; ~35 sized fields, ~20 copies, 6 `×k`, 3 roundings, 5 function laws; deepest chain 3; **0 many-readers**; 1 fact with several provider classes (`maximal_thermal_power_in_watt`); `at_least/at_most` unused.

**Stakeholders.** Consumers of the P1 API: the P2 executor (`resolve_all` with `sources`), Python setups via the same call, the P4 sweep (every converted class). No external consumer touches `hisim/config` directly.

**Required behavior:** R3–R4, RQ1 below. **Kind of change:** behavior change inside an unmerged branch; no released behavior affected. **Assumption A1:** the pilots' resolved values are unchanged by the new binding (each has one provider).

## 5. Goals and Non-Goals

**Goals** — one binding rule (E2) as code · scalar kernel that the inventory proves sufficient · presets independent of instance names · introspection data for P2/P4 tooling · pilots unchanged in outcome.
**Non-Goals** — file syntax (P2) · many-cardinality implementation (hook only) · converting further components (P4) · the `describe` CLI itself (P2)

## 6. Use Cases and Examples

UC-P1.1 **One provider, nothing to say.** `resolve_all([building, boiler, boiler_controller])` — the boiler's `maximal_thermal_power_in_watt` law reads `heating_load_in_watt`; one Building provides it; the controller reads the boiler's band; one boiler. No `sources` argument. Matches UC1 of the epic.

UC-P1.2 **Two providers, explicit.** `resolve_all([…, pv_south, pv_east, battery])` → `ConfigSizingError`: *"`pv_peak_power_in_watt` needed by `battery` is provided by pv_east, pv_south; pass sources={'battery': {'pv_peak_power_in_watt': 'pv_south.pv_peak_power_in_watt'}}"*. With that mapping it resolves to `pv_south`'s value. Matches UC2.

UC-P1.3 **Per-fact conflict.** A boiler and a heat pump both provide `maximal_thermal_power_in_watt`; a DHW controller that reads it must map; the HDS, which reads `water_mass_flow_rate_in_kg_per_second` (one provider), writes nothing.

UC-P1.4 **Provenance.** After UC-P1.2, `battery.sizing_record` contains `(field, law, [("pv_south.pv_peak_power_in_watt", 4200.0)], value)`.

## 8. Requirements

### R3 — Presets `[given]`
- R3.1 `[proposed; json_v2 decision 21]` A config class exposes named presets (`Catalog`); a preset is a complete config in which sizable fields may be `AUTO` or carry a per-preset law.
- R3.3 `[proposed; config_presets]` A field declared with `sized_field(rule=…)` may hold `AUTO`; a config carrying `AUTO` anywhere is rejected at component construction, naming field and law.
- R3.4 `[proposed]` Preset names and fact names are wire format (E8).
- R3.5 `[decided 2026-08-25]` **Presets carry no component name.** The instance name is supplied at construction (by the executor from the file key, by a setup explicitly); a preset is the same object whatever the instance is called.

### R4 — Fact binding `[given]`
- R4.1 `[proposed]` Ambiguity is never resolved by guessing; the error names every candidate.
- R4.2 `[proposed]` The unambiguous case needs no declaration.
- R4.3 `[decided 2026-08-25]` **Binding rule as API.** `resolve_all(configs, sources=None)` where `sources: {consumer_name: {fact: "<provider>.<fact>" | ["<provider>.<fact>", …]}}`. A law's bare fact resolves iff exactly one config in `configs` declares that fact; otherwise the consumer's `sources` entry decides; missing entry with ≥2 providers → `ConfigSizingError` listing the candidates in a form that can be pasted; entry naming a non-provider → error. Providers declare facts on the class only (`SIZING_CONTRIBUTIONS` without scope). The set over which uniqueness is evaluated is exactly `configs` (P2 passes the enabled set).
- R4.4 `[decided 2026-08-25; hook only in P1]` Laws declare cardinality per fact read (one / many). P1 implements **one**; `many` is declared in the algebra (term + tuple + order-independent aggregates on the class side) but raises `NotImplementedError` until its first consumer (plan parking lot). A law reading both one and many of a fact is a contract-test failure.
- R4.5 `[proposed; design B]` The law lives in the class; `sources` only redirects inputs, never computes.
- R4.6 `[decided 2026-08-25]` Any config may both read and provide facts; cycles are hard errors naming the members; fact computation on the provider side (TABULA etc.) runs once at resolution, never inside a consumer law.
- R4.7 `[proposed]` Every resolution records, per sized field: law description, `(qualified source, value)` per fact read — including sibling reads as `(self.<field>, value)` (Q-P1.4) —, result — the `sizing_record`; the engine's `ResolutionReport` records per lookup which providers were candidates and which was chosen.

### R13 — Introspection data `[proposed]`
- R13.1 `[proposed]` For any config class, functions return: settable fields with types and defaults; preset names and, per preset, which sizable fields are pinned vs. `AUTO`; sizable fields with the facts (and cardinality) their laws read; facts provided. Pure data, no CLI (P2), no I/O.

### Quality
- RQ1 `[proposed]` Converting a class touches only its module (E6): no registry, no engine change.
- RQ2 `[proposed]` The kernel imports nothing from HiSim except `hisim.log` (C4).
- RQ3 `[proposed]` A contract test over all classes with `SIZING_CONTRIBUTIONS` fails when two non-interchangeable classes declare the same fact name; interchangeable providers are listed explicitly in the test.

## 9. Constraints, Invariants and Assumptions

- C-P1.1 `[given]` Pilots' resolved numbers unchanged (golden fixtures on the branch).
- C-P1.2 `[proposed]` `SizingContext` stays a typed frozen dataclass with the closed `Size` vocabulary (typo safety, mypy); many-values, when they come, live in a side table — decided in design, not here.
- C-P1.3 `[decided 2026-08-25]` No new law operator is needed; `at_least/at_most` stay (Q-P1.2).
- A1 `[proposed]` The `json_v2` conversions of the heat pump chain port onto the new kernel without law changes (they read one provider each).

## 10. Acceptance Criteria

| ID | Criterion | Verifies |
|---|---|---|
| AC-P1.1 | UC-P1.1 resolves with `sources=None`; the three pilots' tests pass with identical numbers. | R4.2, R4.3, C-P1.1 |
| AC-P1.15 | Pellet preset with `maximal_thermal_power_in_watt` pinned to 12000 and the minimum `AUTO` resolves the minimum to 1000 (reads the pinned sibling, not the re-evaluated law); a PV preset with `share_of_maximum_pv_potential: 0.5` overridden and `power_in_watt: AUTO` sizes at share 0.5; a config whose two sized fields read each other fails the contract test naming both. | Q-P1.4, R4.7 |
| AC-P1.2 | UC-P1.2 without mapping raises listing `pv_east, pv_south` and the paste-ready mapping; with mapping resolves to the named provider; a mapping to a non-provider raises. | R4.1, R4.3 |
| AC-P1.3 | UC-P1.3: only the reader of the conflicting fact needs a mapping. | R4.3 |
| AC-P1.4 | `sizing_record` and `ResolutionReport` contain qualified sources and values (UC-P1.4). | R4.7 |
| AC-P1.5 | A Weather-like provider → Building → generator chain of depth 4 resolves; a two-node cycle raises naming both. | R4.6 |
| AC-P1.6 | A many-reading law is accepted by the algebra and raises `NotImplementedError` on evaluation; a law reading one and many of a fact fails the contract test. | R4.4 |
| AC-P1.7 | Constructing a component from a preset requires an instance name and no preset source contains `component_id`. | R3.5 |
| AC-P1.8 | Introspection returns the four lists for every pilot class without importing components at module level. | R13.1, RQ2 |
| AC-P1.9 | Contract test detects a deliberately added colliding fact name. | RQ3 |
| AC-P1.10 | `grep FactScope\|preseeded_facts\|adjacency hisim/` is empty. | R4.3 |
| AC-P1.11 | Constructing a component whose config still holds `AUTO` raises naming every such field and its law. | R3.3 |
| AC-P1.12 | Every pilot class's `presets` is enumerable; a sizable preset reports its `AUTO` fields, a concrete one none; a wire-format fixture pins the preset and fact names of the pilots and fails on rename. | R3.1, R3.4 |
| AC-P1.13 | A `sources` value that is not a `<name>.<fact>` reference (a number, an expression string) is rejected at the API boundary. | R4.5 |
| AC-P1.14 | A test-only config class with presets, a law and a contribution resolves through `resolve_all` with no change to `hisim/config`. | RQ1 |

## 11. Open Questions and Decisions

`[proposed]` items not listed here (R3.1, R3.3, R3.4, R4.1, R4.2, R4.5, R4.7, R13.1, RQ1–RQ3, C-P1.2) are confirmed by silence at review. Q-P1.1 of the previous draft (how a preset acquires its instance name) was solution design and moved to the plan's P1 design step. EQ1/EQ2 in the epic also affect this document.

**Q-P1.2 — Keep or delete the unused clamp operators `at_least` / `at_most` in the law algebra?** · blocks C-P1.3
*Context.* `hisim/config/laws.py` (294 lines on `config_presets`) offers five operators on a law: fact term, `* k`, `.at_least(min)`, `.at_most(max)`, `.rounded(digits)`, plus opaque function laws. The inventory (§4) shows what the ~35 sized fields use: ~20 fact terms, 6 `* k`, 3 `.rounded`, 5 function laws, **0** clamps. The two clamp operators are ~30 lines including tests and their `describe()` output.
*Options.* (a) **Delete** — smaller public algebra, nothing untested ships; re-adding later is a 30-line PR when a law needs a floor (e.g. a minimum storage volume). (b) **Keep** — no work now, trivially tested, avoids a future PR; costs two operators in every `describe` listing and in R13 documentation that no class uses.
*Recommendation.* (a); the kernel should ship exactly what the inventory justifies, so that "the algebra is sufficient" stays a checkable claim.
*Decision.* `[decided 2026-08-25, owner]` **Keep** `at_least`/`at_most`.

**Q-P1.3 — When a provider's fact value is `None` (its feature is switched off), does it still count as a provider for the ambiguity rule?** · blocks R4.3, AC-P1.2
*Context.* A class declares the facts it provides statically (`SIZING_CONTRIBUTIONS`), but a value may legitimately be `None` at resolution time: on `config_presets` the heat pump contributes its DHW power only when `with_domestic_hot_water_preparation` is true, otherwise `None` (engine docstring: "values … may legitimately be `None` when a feature is off"). Suppose a house has a heat pump (DHW off) and a DHW electric heater, and a DHW controller reads `maximal_thermal_power_in_watt`: two classes *declare* it, one *computes* `None`.
*Options.* (a) **Declared providers count** — two providers → the controller must write `sizing_sources`, even though only one has a value. Consequence: predictable — toggling a feature flag never changes which component a consumer binds to; one more line in such files; matches E1 ("never inferred"). (b) **`None` providers drop out** — the controller silently binds to the heater. Consequence: fewer lines, but flipping `with_domestic_hot_water_preparation` re-binds a *different* component's size without any edit to it — the kind of surprise the review objected to; also makes resolution order-sensitive unless `None` is known before anything is computed.
*Recommendation.* (a); providership is decided from declarations, as the current engine already does, and a consumer that genuinely wants "whichever is on" is the many-cardinality case (EQ2), not a binding rule.
*Decision.* `[decided 2026-08-25, owner]` **(a)** — a provider whose value is `None` still counts; declarations decide providership.

**Q-P1.4 — May a law read another field of the *same* config (a sibling field), and if so does it read the sibling's final value or re-evaluate the sibling's law?** · blocks R4.3, R4.5, R4.7, AC-P1.1, AC-P1.4
*Context.* Two shapes exist. **Plain sibling:** `PVSystemConfig.power_in_watt`'s law needs `share_of_maximum_pv_potential`, an ordinary (non-sizable) field of the same config; the spike works around it with a per-preset law builder `scaled_power_law(share, …)` while the class law assumes share 1.0 — so a file that overrides `share_of_maximum_pv_potential: 0.5` and leaves `power_in_watt: AUTO` silently sizes at share 1.0 (branch agenda B6: "the one known trap in the format"). **Sizable sibling:** several sized fields depend on a sibling in the same config, not on another component: the pellet/wood-chip boiler's minimal power is 1/12 of its own maximal power; the battery's inverter power is 0.5 × its own capacity; the inventory's future list adds the heat pump's minimum power and nominal mass flow from its own thermal power, the CHP's electrical from its thermal power, the electrolyzer's H₂ rate from its own power band (`sizing_fact_inventory.md` §2, §6). The pilot on `config_presets` expresses the boiler case as `minimal_thermal_power_in_watt = 1/12 * GenericBoilerConfig.MAXIMAL_POWER_LAW` — it **re-evaluates the maximal-power law** from the external facts rather than reading the field. That is only correct while both fields are `AUTO`: an author who pins `maximal_thermal_power_in_watt: 12000` in `config` and leaves the minimum `AUTO` gets a minimum computed from the *unpinned* law, i.e. inconsistent with the value actually used — silently. Today `resolve_config` evaluates all `AUTO` fields of a config in declaration order against the external context only; there is no way for a law to see a sibling's value, and no ordering guarantee is documented.
*Options.* (a0) **Plain siblings only** (agenda B6 a1) — `sized_field(..., reads_own=("share_of_maximum_pv_potential",))`; no ordering or cycle issues because plain fields hold final values before resolution; naming a sizable field is rejected at import. Consequence: removes the PV trap, does not cover the pellet case, which keeps law re-evaluation (b). (a) **Sibling term, final value, sizable or plain** (agenda B6 a2: field-granular fixed point) — the algebra gains a term for "this config's field X" (`Self.MAXIMAL_THERMAL_POWER_IN_WATT`); the resolver orders a config's fields by their intra-config dependencies (a cycle inside one config is a contract-test error) and a sibling term reads the field's *final* value, pinned or sized. Consequence: the pinned-max case gives min = 1000 as expected; the record shows `inputs = [("self.maximal_thermal_power_in_watt", 12000)]`; per-preset laws can still be used; ~40 lines in `laws.py`/`sizing.py`. (b) **Keep law re-evaluation** — document that a per-preset law must not depend on a sibling that a user may pin; add a contract test that flags presets whose per-preset law re-evaluates a sibling law. Consequence: no new term, but the pinned-max inconsistency remains expressible and the restriction is hard to check mechanically (a function law may call anything). (c) **Forbid intra-config dependencies** — every sized field depends on external facts only; the boiler's minimum becomes its own function law of the same facts. Consequence: simplest kernel; the inconsistency of (b) remains for pinned siblings, and the future candidates in §6 (eight of them) cannot be expressed as intended.
*Recommendation.* (a); it is the only option in which pinning one field and sizing its dependant stays consistent, which is exactly the "preset + sparse override" pattern R3.1 promotes; (a0) is the cheap fallback if (a) is judged too much for two present use cases.
*Decision.* `[decided 2026-08-25, owner]` **(a)** — laws may read sibling fields of the same config, plain or sizable, and read the sibling's **final** value (pinned or sized); intra-config dependencies are ordered by the resolver, an intra-config cycle is a contract-test error. Rationale: avoids duplicating calculations across fields (the pellet minimum re-evaluating the maximum law is exactly that duplication). Follow-ups in this document: R4.7 records `self.<field>` inputs; AC-P1.1 gains the pinned-sibling case; P2 Q-P2.6's rejected option (c) becomes viable but the `constructor:` form stays the proposed answer.

**Q-P1.5 — Must preset access be statically typed (a preset-name typo caught by mypy) before the sweep?** · blocks R3.1, RQ1, AC-P1.12
*Context.* `Catalog.__getattr__` returns `Any` (branch `config_presets`, `hisim/config/presets.py`), so `GenericBoilerConfig.presets.condensing_gaz` passes mypy and fails at run time, and every helper returning a preset needs a manual annotation. The sweep will write ~70 `presets` attributes and hundreds of accesses in setups and tests. The obvious spelling `presets: ClassVar[Catalog["GenericBoilerConfig"]]` is rejected by mypy (a `ClassVar` may not contain a type variable — `random_findings.md`); candidate mechanisms exist (`__class_getitem__`, descriptor) but are solution design.
*Options.* (a) **Required before the sweep** — a requirement "an unknown preset name is a mypy error" plus AC; P1 prototypes the mechanism first; the sweep writes typed access from the start. Consequence: P1 grows by a small typing prototype; no retrofit over 70 classes. (b) **Not required** — access stays `Any`; typos surface at run time (the file loader catches them for files, R3.6 in P2, but not for Python setups/tests). Consequence: cheaper now; retrofitting later touches every class.
*Recommendation.* (a); the cost is paid once, the alternative 70 times.
*Decision.* `[decided 2026-08-25, owner]` **(a)** — static typing of preset access is required before the sweep; an unknown preset name must be a mypy error. Mechanism is P1 solution design. **Mechanism: decorated classmethods (2026-08-26)** — `@preset` / `@constructor` on classmethods of the config class itself, typed as identity decorators, replacing the `Presets`/`Preset` namespace; a preset method is named `preset_<wire_name>` and a constructor `for_…`/`from_…`, and that prefix is what makes a call site (`GenericBoilerConfig.preset_condensing_gas("Boiler")`) self-explanatory. See the implementation spec's "Amendments (decorators, 2026-08-26)".

**Q-P1.6 — Which default factories become presets, and which stay constructors?** · blocks R3.1, R3.4, plan P4
*Context.* `WeatherConfig`'s factory is a lookup over several dozen `LocationEnum` members; TABULA building codes (hundreds) and LPG household definitions (dozens) are the same shape: an open identifier space, not a set of variants. Converting them exhaustively would mint dozens of meaningless preset names that then become wire format (E8). The spike's pattern: presets only for the cases the repo uses (`aachen`, `seville`), plus a plain named constructor for the general case (`for_location(...)`), named so no default-discovery heuristic picks it up.
*Options.* (a) **Normative rule:** *identifier-parameterised lookups keep a named constructor; presets are named variant sets.* Consequence: a class may have both; the sweep applies one rule; files instantiate lookup classes via a small complete `config` (P2 Q-P2.6). (b) **Everything is a preset** — one mechanism, but hundreds of names and no way to express "a location not in the list" without editing the class.
*Recommendation.* (a).
*Decision.* `[decided 2026-08-25, owner]` **(a), refined:** lookup-parameterised classes (Weather, Building/TABULA, LPG occupancy) expose exactly **one** preset, named `standard`, plus a configurable constructor for the open identifier space. Consistent with naming rule 5 (Q-P1.9): these classes have exactly one defensible preset. How a file calls the constructor: P2 Q-P2.6 and `energy_system_mockup.yaml`. `[2026-08-26]` The Building now has that constructor: `BuildingConfig.for_tabula_code(name, building_code, …)`, with `preset_standard` delegating to it (field-for-field identical to the previous `standard`); it is the first user of the `@constructor` mechanism.

**Q-P1.7 — Do config values carry author-declared source notes (e.g. `note="VDI 4645"`), and in which shape?** · blocks R4.7, P2 R8.2
*Context.* The record already captures everything the machinery knows (preset built from, law, facts read with values). It cannot know *why* a hard-coded constant is what it is: the PV rule's 0.6 rooftop factor, the 50 l/kW buffer rule, an efficiency curve's datasheet. If such notes should reach the audit companion they must be data on the field or preset. The sweep would apply the convention ~70 times; a wrong citation misleads with authority (agenda B10).
*Options.* (a) **Optional `note=` on `sized_field` and on presets**, populated only where a source is actually known, rendered into the audit companion and the realized-record comments. Consequence: small machinery, a repo-wide but optional convention. (b) **None** — sources live in docstrings only; the audit stays purely mechanical. Consequence: nothing to maintain; provenance of constants stays in code.
*Recommendation.* (a), strictly optional; never a placeholder to fill.
*Decision.* `[decided 2026-08-25, owner]` **(a)** — optional `note=` on sized fields and presets; populated only where a source is known; rendered into the audit companion (P2 R8.2).

**Q-P1.8 — May a sizing law be a constant, i.e. may `AUTO` mean "the author's usual choice" rather than "derived from the system"?** · blocks R3.3, R4.5, R13.1
*Context.* `HeatDistributionControllerConfig.heating_system`'s law is the constant `FLOORHEATING`: `AUTO` there means "the usual choice", because TABULA carries no heat-distribution-type information and a building-age → radiator heuristic would be invented physics (agenda C12). The same shape appears wherever a preset wants a sensible default that is not a number. Readers of a file expect `AUTO` to mean "computed from facts"; the `describe` output (R13.1) would list such a field as sizable although it reads no fact.
*Options.* (a) **Allowed, but marked** — constant laws are legal; introspection and the realized-record comment say `author default (constant law)`, so nobody mistakes it for a derivation. Consequence: presets stay uniform; the semantics are visible. (b) **Forbidden** — a field with a constant law is simply a field with a default value, not sizable; `AUTO` always reads ≥ 1 fact. Consequence: cleaner meaning of `AUTO`; `heating_system` becomes a plain default and a preset that wants to vary it sets the value.
*Recommendation.* (b); "AUTO" should promise a derivation, and a default value already expresses "the usual choice".
*Decision.* `[decided 2026-08-25, owner]` **(a)** — a law may be a constant, marked as `author default (constant law)` in introspection and realized-record comments. **Refinement for the motivating case:** `HeatDistributionControllerConfig.heating_system` must *not* stay a constant under `AUTO`; its law shall depend on the building's construction year and renovation level (new Building facts, e.g. `construction_year`, `renovation_level` from the TABULA code) — a physics decision executed in P4 batch B3 with result diffs; until then the field is a plain default, not `AUTO`.

**Q-P1.9 — Which naming convention do preset names follow (they become wire format, E8)?** · blocks R3.4, plan P4, AC-P1.12
*Context.* The legacy surface has 103 factory classmethods in 79 distinct spellings across 88 config classes; 17 classes have no factory at all (`roadmap/declarative_energy_systems/preset_naming_supplement.md`, survey 2026-08-25, one proposed preset list per class). The four converted classes ship `condensing_gas`, `condensing_gas_12kw`, `oil`, `oil_12kw`, `pellets`, `wood_chips`, `hydrogen` (boiler), `standard` (HDS), `optimize_own_consumption` (EMS), `german_single_family_home` (building). Proposed convention (branch agenda Q5): (1) snake_case, the name names the *variant*, never the class; (2) a bare variant name is the sizable template, a rating suffix (`_12kw`) marks a concrete catalogue device, nothing else carries numbers; (3) the canonical (first) preset is what the repo's setups default to; (4) grammatical number follows the substance; (5) `standard` is reserved for classes with exactly one defensible preset and is retired the moment a second appears.
Applying the five rules to all 88 classes (supplement §"Verdict", §"Rule conflicts", 10 conflicts) shows two collisions: **rules 2 and 5 are mutually exclusive for rating-only classes** (`BatteryConfig`, both hplib heat pumps, `SimpleHotWaterStorageConfig`, `SolarThermalSystemConfig`: one variant + one catalogue device → rule 5 demands `standard`, then forbids it once `standard_8kw` exists); and **~10 classes have factories differing only by a boolean flag** (`with_domestic_hot_water_preparation`, `parallel_space_heating_and_dhw_option`, `secondary_mode`), which rule 1 would turn into `gas_with_dhw`/`gas_space_heating_only` pairs — doubling the wire vocabulary for something a file sets in one `config:` line. Further findings: the 12 `get_default_*`/`get_scaled_*` twins are one variant in two states (sized/unsized), not two presets; the shipped `german_single_family_home` violates Q-P1.6 (Building is a lookup class → `standard`); `PVSystem.get_default_config` is a third, divergent factory living on the component class; ten more classes are lookup-shaped beyond Weather/Building/UTSP (manufacturer JSONs of the H₂ chain, windpowerlib turbine types, device tables, LPG charging-station sets, `CSVLoaderConfig`) and get constructors; the nine legacy plain dataclasses in `configuration.py` are proposed for deletion, not conversion.
*Options.* (a) **Accept the five rules plus the supplement's amendments A1–A3:** A1 a rating-suffixed preset does not count as a second preset for rule 5 (`standard` + `standard_8kw` is legal); A2 a boolean feature flag is never part of a preset name — presets vary by fuel, source medium, technology and control law only; A3 where the class implies a technology, name the variant after it instead of `standard` (`air_water`, `flat_plate`). Covers 86 of 88 classes without an ad-hoc decision; the remaining two (`CSVLoaderConfig`, `SetTemperatureConfig`) are constructor-only. (b) **Five rules unamended** — the four rating-only classes and the flag pairs need per-class decisions during the sweep (≈14 ad-hoc names). (c) **No convention** — each author chooses; renames later are breaking (EQ1).
*Recommendation.* (a); then audit the four converted classes against it before the sweep (rename `german_single_family_home` → `standard`; decide the canonical PV preset explicitly — conflict 5).
*Decision.* `[decided 2026-08-26, owner]` **(a)** — the five rules plus the supplement's amendments A1 (a rating suffix never triggers rule 5), A2 (boolean flags are never in a preset name), A3 (name after the technology instead of `standard` where the class implies one). Wire names are what the rules govern; the code-side method carries the mandatory `preset_` prefix (constructors `for_`/`from_`) so a call site is self-explanatory. Enforced by `tests/test_config_contracts.py`. Names stay provisional until P5 (EQ1).

## 12. Glossary

See the epic. P1-specific: **`sources`** — the Python-side form of `sizing_sources`; **`SIZING_CONTRIBUTIONS`** — the class-side declaration of facts provided.
