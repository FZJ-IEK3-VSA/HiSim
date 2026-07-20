// Tier 1 — fast data round-trip over every shipped scenario JSON.
//
// Exercises the editor's real import/export logic (the same functions the UI calls)
// without a browser. For each scenario it asserts:
//   - universal:   import drops nothing; a second open→save is byte-identical (idempotent)
//   - semantic:    open→save preserves all content, and the import validates without errors
//                  (asserted only for scenarios that do not use dynamic ports — see README)
//
// See tests/e2e/roundtrip.spec.ts for the browser-driven counterpart (Tier 2).

import { describe, it, expect } from 'vitest'
import { listScenarioFiles, readScenario, loadComponentDb } from './scenarios'
import {
  roundTrip,
  diffScenario,
  usesDynamicPorts,
  importWarnings,
  validationErrorsFor,
} from './roundtrip-core'

const db = loadComponentDb()
const files = listScenarioFiles()

describe('scenario round-trip (Open JSON → Save JSON)', () => {
  it('finds scenario files to test', () => {
    expect(files.length).toBeGreaterThan(0)
  })

  describe.each(files)('%s', (file) => {
    const original = readScenario(file)
    const dynamic = usesDynamicPorts(original)

    // ── Universal invariants — must hold for every scenario ──────────────────
    it('imports without dropping components or dangling connections', () => {
      expect(importWarnings(original, db)).toEqual([])
    })

    it('round-trips idempotently (a second open→save changes nothing)', () => {
      const once = roundTrip(original, db)
      const twice = roundTrip(once, db)
      expect(twice).toBe(once)
    })

    // ── Semantic fidelity — dynamic-free scenarios only (see tests/README.md) ─
    const semantic = dynamic ? it.skip : it

    semantic('preserves all content across open → save', () => {
      expect(diffScenario(original, roundTrip(original, db))).toEqual([])
    })

    semantic('imports with no validation errors', () => {
      expect(validationErrorsFor(original, db)).toEqual([])
    })
  })
})
