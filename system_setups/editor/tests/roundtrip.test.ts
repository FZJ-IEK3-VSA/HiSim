// Tier 1 — fast data round-trip over every shipped scenario JSON.
//
// Exercises the editor's real import/export logic (the same functions the UI calls)
// without a browser. For each scenario it asserts:
//   - import drops nothing (no components skipped, no connections discarded)
//   - a second open→save is byte-identical (idempotent)
//   - open→save preserves all content
//   - the import validates without errors or warnings
//   - its load-type mismatches match the reviewed snapshot
//
// All assertions run for every scenario, including those using dynamic component ports.
//
// See tests/e2e/roundtrip.spec.ts for the browser-driven counterpart (Tier 2).

import { describe, it, expect } from 'vitest'
import { listScenarioFiles, readScenario, loadComponentDb } from './scenarios'
import {
  roundTrip,
  diffScenario,
  importWarnings,
  loadTypeMismatchesFor,
  validationErrorsFor,
  validationWarningsFor,
} from './roundtrip-core'

const db = loadComponentDb()
const files = listScenarioFiles()

describe('scenario round-trip (Open JSON → Save JSON)', () => {
  it('finds scenario files to test', () => {
    expect(files.length).toBeGreaterThan(0)
  })

  describe.each(files)('%s', (file) => {
    const original = readScenario(file)

    it('imports without dropping components or connections', () => {
      expect(importWarnings(original, db)).toEqual([])
    })

    it('round-trips idempotently (a second open→save changes nothing)', () => {
      const once = roundTrip(original, db)
      const twice = roundTrip(once, db)
      expect(twice).toBe(once)
    })

    it('preserves all content across open → save', () => {
      expect(diffScenario(original, roundTrip(original, db))).toEqual([])
    })

    it('imports with no validation errors', () => {
      expect(validationErrorsFor(original, db)).toEqual([])
    })

    // A warning now means a dimensional problem (unit mismatch) or an empty required config
    // field — both are real defects, so this is asserted empty rather than snapshotted.
    it('imports with no validation warnings', () => {
      expect(validationWarningsFor(original, db)).toEqual([])
    })

    // Load-type mismatches are labelling inconsistencies in HiSim's components, not faults in
    // the scenario or the editor, so they are recorded instead. Run `npm run test -- -u` after
    // an intentional change and review the diff — a new entry means new odd wiring.
    it('produces only the reviewed load-type mismatches', () => {
      expect(loadTypeMismatchesFor(original, db)).toMatchSnapshot()
    })
  })
})
