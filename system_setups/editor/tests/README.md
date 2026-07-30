# Scenario editor tests

Two tiers verify that opening a `*.scenario.json` in the editor and saving it again
preserves the scenario — i.e. **save == open when nothing is changed**.

| Tier | Runner | Scope | Command |
|------|--------|-------|---------|
| 1 — data round-trip | Vitest (Node) | all `system_setups/*.scenario.json` | `npm run test` |
| 2 — UI round-trip | Playwright (headless Chromium) | all `system_setups/*.scenario.json` | `npm run test:e2e` |

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
- **No validation errors** — the imported scenario has zero validation errors.
- **Only the reviewed validation warnings** — warnings are snapshotted, not required to be
  empty (see below).

## Why warnings are snapshotted rather than asserted empty

Validation reports load-type and unit mismatches on connections as **warnings**. Many are
legitimate: HiSim's `Component.connect_input` matches inputs to outputs purely by field name
and never enforces load-type equality, and shipped setups genuinely mix related load types
(`Water` → `Temperature` on a water-storage port, `Volume` → `WarmWater` on a mass-flow port).
Requiring zero warnings would mean either rewriting HiSim's load-type labels — which feed KPI
and post-processing grouping — or deleting a check that does catch real mis-wiring.

So the warning list per scenario is captured in a snapshot. The existing warnings stay visible
and reviewable in `__snapshots__/roundtrip.test.ts.snap`, while any **new** warning fails the
build.

```bash
npm run test -- -u     # after an intentional change: refresh, then review the snapshot diff
```

Vitest refuses to create missing snapshots when `CI` is set, so the snapshot file must be
committed — CI cannot silently self-approve a new warning.

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
