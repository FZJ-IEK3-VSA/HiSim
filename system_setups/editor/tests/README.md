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

- **No dropped components** — import produces no warnings (catches registry drift / obsolete
  or renamed components).
- **Idempotency** — a second open→save is byte-identical to the first. Universal; a failure
  means the round-trip is non-deterministic.
- **Semantic preservation** — open→save loses nothing (see above).
- **No validation errors** — the imported scenario has zero validation errors.

## Known gap: dynamic component ports

The last two invariants are asserted only for scenarios that do **not** use HiSim's *dynamic*
component ports. The editor does not yet fully round-trip these:

- dynamic **`outputs`** (e.g. on `L2GenericEnergyManagementSystem`) are not persisted on save;
- dynamic-**input** connection indices are re-synthesised on import, so connections into
  `Input_<source>_<output>_<n>` ports do not match and are dropped.

`usesDynamicPorts()` detects such scenarios (non-empty `inputs`/`outputs`, or connections into
`Input_*_<n>` ports) and the semantic assertions are skipped for them, while the universal
invariants still apply. **When the editor is taught to preserve dynamic ports, remove that gate
in [`roundtrip-core.ts`](./roundtrip-core.ts) so these scenarios are held to full fidelity too.**

## Prerequisites

`public/data/component_db.json` must be current (the tests import against it):

```bash
npm run generate-db      # = python ../../tools/generate_component_db.py
```

CI regenerates it before running so the tests never run against a stale registry.

## Running locally

```bash
npm install              # first time (adds vitest + @playwright/test)
npm run test             # Tier 1
npx playwright install chromium   # first time, for Tier 2
npm run test:e2e         # Tier 2 (boots the dev server automatically)
```
