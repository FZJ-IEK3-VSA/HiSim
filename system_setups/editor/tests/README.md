# Scenario editor tests

Two tiers verify that opening a `*.scenario.json` in the editor and saving it again
preserves the scenario — i.e. **save == open when nothing is changed**.

| Tier | Runner | Scope | Command |
|------|--------|-------|---------|
| 1 — data round-trip | Vitest (Node) | all `system_setups/*.scenario.json` | `npm run test` |
| 2 — UI round-trip | Playwright (headless Chromium) | all `system_setups/*.scenario.json` | `npm run test:e2e` |

[`suggest.test.ts`](./suggest.test.ts) and [`dynamicPorts.test.ts`](./dynamicPorts.test.ts)
run alongside tier 1 and cover different questions — see [Component
suggestions](#component-suggestions) and [Dynamic ports](#dynamic-ports) at the end.

Both tiers judge fidelity with the same logic in [`roundtrip-core.ts`](./roundtrip-core.ts):
Tier 1 calls the editor's `importScenario`/`exportScenario` directly (fast, deterministic,
pinpoints logic bugs); Tier 2 drives the real app — clicking **Open JSON**, picking the file
through the browser's file chooser, clicking **Save JSON**, and capturing the download — so it
also covers the React components, the store wiring, and auto-validate-on-open.

## What "preserved" means

A literal byte comparison is *not* used, because a faithful save legitimately differs from the
source text in ways that carry no meaning. The comparison ignores:

- `_editor_positions` — canvas coordinates the editor adds on save;
- object **key ordering**;
- **number formatting** (`1.0` vs `1`, `1e-3` vs `0.001`) — both sides are parsed;
- **connection ordering**.

Everything else — component set, per-component config, inputs/outputs, `connect_automatically`,
the connection set, and `multiple_buildings` — must match.

## Invariants asserted per scenario

All of these run for **every** scenario, including those using dynamic component ports.

- **No dropped content** — import produces no warnings. Import warns both when a component is
  missing from the registry and when a *connection* cannot be resolved to a port, so a
  silently vanishing edge fails the build instead of resurfacing later as a misleading
  "mandatory port is not connected" error.
- **Idempotency** — a second open→save is byte-identical to the first. A failure means the
  round-trip is non-deterministic.
- **Semantic preservation** — open→save loses nothing (see above).
- **No validation errors or warnings** — a warning means a dimensional problem or an empty
  required config field; the shipped scenarios have neither.
- **Only the reviewed load-type mismatches** — recorded in a snapshot, not required to be
  empty (see below).

## Unit mismatch = warning, load-type mismatch = info

The connection compatibility check reports these at two different severities, deliberately.

A **unit** mismatch is a real defect. HiSim passes the raw float straight down the channel, so
a `W` output feeding a `kW` input is a silent factor-1000 error. That is a warning, and the
tests assert there are none.

A **load type** mismatch is a labelling inconsistency, not a wiring fault — so it is an info,
and snapshotted. Nothing in HiSim reads a port's load type: `Component.connect_input` matches
on field name alone, dynamic components match on tags and `source_weight`, and the only
consumers of `ComponentOutput.load_type` are a display string and JSON serialisation. Component
authors therefore label the same channel differently in good faith — a storage calls its outlet
`Water`, the consumer calls its inlet a `Temperature` — and the shipped setups are full of it
(~136 occurrences across 10 recurring type pairs, with the *units* agreeing every time).

Reporting those as warnings drowned out the unit check, which is what actually matters. Keeping
them as a snapshot means the existing ones stay visible and reviewable in
`__snapshots__/roundtrip.test.ts.snap`, while any **new** one shows up as a diff:

```bash
npm run test -- -u     # after an intentional change: refresh, then review the snapshot diff
```

Vitest refuses to create missing snapshots when `CI` is set, so the snapshot file must be
committed — CI cannot silently self-approve a new mismatch.

## Dynamic component ports

Scenarios using HiSim's *dynamic* component ports are held to the same standard as the rest.
The editor reconstructs the runtime field names the way `DynamicComponent` does:

- **inputs** — `add_component_input_and_connect` labels the port
  `Input_{source}_{output}_{len(self.inputs)}`, and `self.inputs` already holds the static
  inputs, so the index starts at the static input count, not at 0;
- **outputs** — `add_component_output` appends `Output{len(self.outputs) + 1}` to the declared
  prefix, so the first scenario-declared output lands one past the registry's output count.

Both are implemented in [`../src/io/import.ts`](../src/io/import.ts) and mirrored on save by
[`../src/io/export.ts`](../src/io/export.ts).

## Component suggestions

[`suggest.test.ts`](./suggest.test.ts) tests the *Suggest components* engine
([`../src/io/suggest.ts`](../src/io/suggest.ts)) against the same shipped scenarios, used
the other way round: **delete one component from a working scenario and the editor should
be able to name what went missing.** That is the situation the feature exists for, and it
is measurable, so the recovery rate is asserted rather than eyeballed.

The two signals are measured separately because they cover disjoint cases:

| Signal | Case it covers | Measured | Asserted |
|---|---|---|---|
| port analysis (`default_connections`, then unit + name matching) | the deletion left an unconnected mandatory input (161 of the 227 removals) | rank 1: **91%**, top 3: **98%** | ≥75% / ≥85% |
| co-occurrence across the shipped scenarios (`usage_db.json`) | the deletion left nothing unconnected — a component nothing declares a need for, such as a battery or a PV system feeding a dynamic port, which port analysis recovers only 12% of the time (the other 66) | rank 1: **67%**, top 3: **70%** | top 3 ≥50% |

Thresholds sit well below the measured rates on purpose: ordinary registry churn shifts
these by a point or two, while a real regression halves them.

The rest of the file asserts the properties that must hold regardless of ranking — a
complete scenario yields nothing to fix, no proposal is a component the canvas already has,
every proposed port pairing names ports that actually exist, and dropping `usage_db.json`
degrades the second signal to silence rather than to guesswork.

## Dynamic ports

[`dynamicPorts.test.ts`](./dynamicPorts.test.ts) covers the ports a `DynamicComponent` grows
at run time. The risk there is **naming**: a dynamic input is labelled
`Input_{source}_{output}_{len(self.inputs)}`, so the order the simulator walks the components
in decides the port names, and a connection naming the wrong index is silently dropped.

Rather than assert that against a hand-written expectation, the tests use a shipped scenario
as ground truth in both directions. `household_gas_building_sizer.scenario.json` spells its
EMS ports out explicitly — names produced by HiSim itself. The test switches the EMS to
`connect_automatically`, deletes those ports, and requires the editor to derive exactly the
ones that were there, with the same load types, units, tags and source weights. The same
scenario pins the output side: `LoadingPowerInputForBattery_Output16` must come back out of
the mined declaration with that exact name.

Also asserted: derived ports are never written back to the file (HiSim recreates them, so
writing them would create each port twice), nothing is derived when the toggle is off, and
the two `connect_automatically` mistakes that make HiSim raise `KeyError` at start-up are
reported as validation errors.

## Prerequisites

`public/data/component_db.json` must be current (the tests import against it):

```bash
npm run generate-db      # = python ../../tools/generate_component_db.py
```

The generator instantiates each component with non-default config switches so that
conditionally declared ports (e.g. MoreAdvancedHeatPumpHPLib's DHW ports, added only under
`with_domestic_hot_water_preparation`) end up in the registry, and it self-checks that every
`default_connections` entry names ports that actually exist — a broken one fails generation
rather than surfacing later as an orphaned-edge error in the browser.

CI regenerates the database before running so the tests never run against a stale registry.

## Running locally

```bash
npm install              # first time (adds vitest + @playwright/test)
npm run test             # Tier 1
npx playwright install chromium   # first time, for Tier 2
npm run test:e2e         # Tier 2 (boots the dev server automatically)
```
