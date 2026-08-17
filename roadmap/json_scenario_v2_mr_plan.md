# JSON Scenario v2 — Merge Request Plan

**Spec:** `system_docs/json_scenario_v2_spec.md`
**Date:** 2026-08-17

Decomposition of the spec's §7 migration phases into individually reviewable, individually
testable MRs. Ground rules that make the split work:

1. **v1 stays untouched until the final deletion MR.** The v2 executor, schema, and
   protocol land *alongside* the existing code; every MR leaves main green and shippable.
2. **The parity harness is the workhorse.** MR B1 introduces a test that runs a Python
   setup and the equivalent v2 JSON and diffs the resolved wiring
   (`component_connections.json`) and, where cheap, simulation results. Every later MR is
   verified by it plus the existing JSON golden parity gate.
3. **Aggregator migrations split into two MRs each:** first a pure behavior-preserving
   refactor (goldens prove nothing changed), then the cutover (goldens regenerate, parity
   gate proves equivalence). Reviewers never face refactor and behavior change in one diff.
4. Sizes: **S** ≲150 lines changed, **M** ≈150–500, **L** >500 (L only where the diff is
   mechanical and the gate does the verification).

---

## Track A — Foundations (mutually independent, no production behavior change)

| MR | Content | Size | Tested by |
|---|---|---|---|
| **A1** | `PathResolver` + `${var}` reference scheme (spec §3.5) | S | pure unit tests (symbolize/resolve roundtrip, OS-independence, unknown-var error) |
| **A2** | v2 pydantic schema + static validator: entry shapes, don't-mix rule, duplicate detection, error catalog items that need no components (spec §3, §5 steps 1+3) | M | pure unit tests on JSON fixtures, one test per hard-error condition |
| **A3** | Protocol hooks with default implementations: `ConfigBase.to_scenario_dict`/`from_scenario_dict`, `Component.build_from_scenario`, `BuildContext` (spec §4.1–4.2) + the config-roundtrip half of the contract test (§4.7 items 1–2) over all components | M | the new parametrized contract test; components it exposes as lossy get small follow-up fix MRs |
| **A4** | `IntentChannel`, `DispatchRule`, channel-matching logic (spec §4.5) | S | pure unit tests (zero/one/ambiguous match, dispatch rules, type checks) |

## Track B — Executor v2 (sequential on A)

| MR | Content | Depends | Size | Tested by |
|---|---|---|---|---|
| **B1** | v2 executor for static-only scenarios: build lifecycle steps 1–4 (no intents), bare-pair expansion via existing default connections, explicit wires, hard errors. **Introduces the parity harness.** | A1–A3 | M/L | parity harness on a hand-written minimal scenario (weather + occupancy + building) vs its Python setup |
| **B2** | `WeatherConfig` + `UtspLpgConnectorConfig` serialization overrides; kill their central special-case blocks in the v2 path | A3 | S | contract test + parity harness |
| **B3** | `Car` + EV charge controller: `build_from_scenario` overrides using `BuildContext` | A3, B1 | S | contract test + a car-scenario parity case |
| **B4** | Intent resolution engine: `resolve_intents` protocol, deterministic ordering, §4.6 naming, dispatch wiring, `Simulator.connect_intent` (execution side) — tested against a **fixture aggregator**, no real component touched | A4, B1 | M | unit tests: shuffled-input determinism, naming, dispatch back-wiring, all intent error conditions |

## Track C — Aggregator migrations (each = refactor MR + cutover MR)

| MR | Content | Depends | Size | Tested by |
|---|---|---|---|---|
| **C1** | EMS: declare `INTENT_CHANNELS`, route existing runtime tag queries through channel keys. **Pure refactor — all goldens unchanged.** | A4 | M | existing goldens (must not move) |
| **C2** | EMS: implement `resolve_intents` + default-intent declarations as data. Additive — old dynamic add-API still works. | B4, C1 | M | unit tests constructing an EMS and feeding intents; §4.7 items 3–4 |
| **C3** | EMS cutover: remove constructor-created dynamic outputs, switch all EMS-touching setups to `connect_intent`, regenerate affected goldens (`OutputN` → semantic names) | C2, D2 | L (mechanical) | parity harness + golden parity gate |
| **C4** | `ElectricityMeter`: channels + `resolve_intents` + cutover (simple, monitored-only — one MR) | B4, C3 pattern | M | same |
| **C5** | `GasMeter` + `FuelMeter` + `HeatingMeter` (same pattern ×3, trivially similar) | C4 | M | same |
| **C6** | `Building` (monitored-only heat-source intents, per spec §9.4) | B4 | M | same |

## Track D — Generation & setup conversion

| MR | Content | Depends | Size | Tested by |
|---|---|---|---|---|
| **D1** | Recording model for static scenarios: `sim.connect_default`, component-entry recording via save protocol, v2 `write_standalone_scenario_json`. Delete nothing yet. | B1 | M | generate-then-execute roundtrip on already-static setups |
| **D2** | Intent recording: `connect_intent` records the intent entry | B4, D1 | S | roundtrip on the B4 fixture aggregator |
| **D3–D5** | Setup conversion in ~3 batches (building_sizer family; household setups; district + remainder): each batch moves setups to the recording API and regenerates its scenario JSONs as v2 | C3–C6, D1–D2 | M each (mechanical) | golden parity gate per batch |
| **D6** | Schema-dump command (`python -m hisim.scenario_schema`): export component classes, config schemas, intent channels for the GUI. Optional, parallel any time after A2+A4. | A2, A4 | S | snapshot test of the dump |

## Track E — Cutover

| MR | Content | Depends | Size | Tested by |
|---|---|---|---|---|
| **E1** | Deletion: v1 generator/executor paths, `remove_automatic_connections` and friends, public dynamic add-API, `connect_automatically`, `humps`, `<<...>>` placeholders, deprecated `source_weight` config fields. Pure red diff. | everything | L (trivial review) | full test suite + parity gate stay green |

## Dependency graph

```
A1 ─┐
A2 ─┼─> B1 ──> B3          C1 ─> C2 ─┐
A3 ─┘    │                            ├─> C3 ─> C4 ─> C5 ─┐
A3 ─────> B2               ┌──────────┘                    ├─> D3–D5 ─> E1
A4 ─────────> B4 ──────────┤          C6 ─────────────────┘
              │            └─> D2 ─┐
B1 ─────────> D1 ──────────────────┴─> D3–D5
A2 + A4 ────> D6   (parallel, optional)
```

Parallelizable at the start: A1–A4 are four independent MRs; B2 can proceed while B1 is in
review; C1 (pure refactor) needs only A4 and can land early. The critical path is
A→B1→B4→C2→C3→D3–D5→E1.

## What "independently tested" means per track

- **A:** pure unit tests; nothing in production calls the new code yet.
- **B:** the parity harness makes correctness observable per MR without touching any
  existing scenario or golden.
- **C:** refactor MRs are proven by *unchanged* goldens; cutover MRs by *regenerated*
  goldens plus the parity harness showing v2 wiring ≡ Python-setup wiring.
- **D:** generate→execute→compare roundtrips, batch by batch.
- **E:** deletion only — if anything still depended on v1, the suite goes red.
