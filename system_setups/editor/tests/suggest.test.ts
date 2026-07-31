// Tests for the "Suggest components" engine (src/io/suggest.ts).
//
// The shipped scenarios double as ground truth: delete one component from a working
// scenario and the editor should be able to name what went missing. That is exactly the
// situation the feature exists for — a half-built scenario with open validation errors —
// and it is measurable, so the recovery rate is asserted rather than eyeballed.

import { describe, it, expect } from 'vitest'
import { listScenarioFiles, readScenario, loadComponentDb, loadUsageDb } from './scenarios'
import { importScenario } from '../src/io/import'
import { validateScenario } from '../src/io/validate'
import { suggestComponents, type Suggestions } from '../src/io/suggest'
import type { ComponentEntry } from '../src/types'

const db = loadComponentDb()
const usage = loadUsageDb()
const files = listScenarioFiles()

const shortName = (entry: ComponentEntry) => entry.component_full_classname.split('.').pop()

/** Strip one component (by its `configuration.name`) and every connection touching it. */
function withoutComponent(text: string, name: string): string {
  /* eslint-disable @typescript-eslint/no-explicit-any */
  const scenario = JSON.parse(text)
  scenario.components = (scenario.components ?? []).filter(
    (c: any) => c?.configuration?.name !== name,
  )
  scenario.connections = (scenario.connections ?? []).filter(
    (c: any) => c?.source?.component_name !== name && c?.target?.component_name !== name,
  )
  /* eslint-enable @typescript-eslint/no-explicit-any */
  return JSON.stringify(scenario)
}

function suggestFor(text: string): Suggestions {
  const imported = importScenario(text, db)
  return suggestComponents(imported.nodes, imported.edges, db, usage)
}

/** Unconnected mandatory inputs — what the editor reports as errors. */
function errorCount(text: string): number {
  const imported = importScenario(text, db)
  return validateScenario(imported.nodes, imported.edges).errors.length
}

describe('suggestions for a complete scenario', () => {
  describe.each(files)('%s', (file) => {
    const text = readScenario(file)

    // Every shipped scenario validates without errors (tests/roundtrip.test.ts asserts
    // this), so nothing mandatory is left open and nothing may be proposed to fix one.
    it('proposes nothing to fix a mandatory input', () => {
      const { missing, unexplained } = suggestFor(text)
      expect(unexplained).toEqual([])
      expect(missing.flatMap((m) => m.fixes).filter((f) => f.mandatory)).toEqual([])
    })

    it('never proposes adding a component the canvas already has', () => {
      const imported = importScenario(text, db)
      const present = new Set(imported.nodes.map((n) => n.data.entry.component_full_classname))
      const { missing, companions } = suggestComponents(imported.nodes, imported.edges, db, usage)
      for (const s of [...missing, ...companions]) {
        expect(present.has(s.entry.component_full_classname)).toBe(false)
      }
    })
  })
})

describe('suggestions for an incomplete scenario', () => {
  const basic = readScenario('basic_household.scenario.json')

  it('names the component that was deleted, ranked first', () => {
    const { missing } = suggestFor(withoutComponent(basic, 'Weather'))
    expect(missing.length).toBeGreaterThan(0)
    expect(shortName(missing[0].entry)).toBe('Weather')
  })

  it('says which ports the proposal would connect', () => {
    const { missing } = suggestFor(withoutComponent(basic, 'Weather'))
    const fixes = missing[0].fixes
    expect(fixes.length).toBeGreaterThan(0)
    // Every proposed fix names a real port pairing taken from default_connections.
    for (const fix of fixes) {
      expect(fix.declared).toBe(true)
      expect(missing[0].entry.output_ports.some((p) => p.field_name === fix.sourceOutput)).toBe(true)
    }
    expect(fixes.some((f) => f.mandatory)).toBe(true)
    expect(fixes.map((f) => f.targetNodeName)).toContain('PVSystem')
  })

  it('proposes wiring, not a new component, when the source is already on the canvas', () => {
    const imported = importScenario(basic, db)
    // Same components, no connections at all — every declared default connection now has
    // its source sitting right there unwired.
    const { missing, wiring } = suggestComponents(imported.nodes, [], db, usage)
    expect(wiring.length).toBeGreaterThan(0)
    const present = new Set(imported.nodes.map((n) => n.data.entry.component_full_classname))
    for (const s of missing) {
      expect(present.has(s.entry.component_full_classname)).toBe(false)
    }
    // The wiring it proposes is real: both endpoints exist and are named the way the
    // canvas handles are.
    for (const w of wiring) {
      const source = imported.nodes.find((n) => n.id === w.sourceNodeId)
      const target = imported.nodes.find((n) => n.id === w.targetNodeId)
      expect(source).toBeDefined()
      expect(target).toBeDefined()
      expect(source?.data.entry.output_ports.some((p) => p.field_name === w.sourceOutput)).toBe(true)
      expect(target?.data.entry.input_ports.some((p) => p.field_name === w.targetInput)).toBe(true)
    }
  })

  it('falls back to usage statistics for a component nothing depends on', () => {
    // Nothing in basic_household declares a dependency on the PV system — it feeds the
    // ElectricityMeter through a *dynamic* port, which that scenario defines itself rather
    // than deriving from the component. Deleting it therefore leaves the graph structurally
    // intact and port analysis has nothing to go on. The shipped scenarios do: every one of
    // them containing a PVSystem also contains a Weather, and vice versa.
    const text = withoutComponent(basic, 'PVSystem')
    expect(errorCount(text)).toBe(errorCount(basic))

    const { missing, companions } = suggestFor(text)
    expect(missing.map((m) => shortName(m.entry))).not.toContain('PVSystem')
    expect(shortName(companions[0].entry)).toBe('PVSystem')
  })

  it('still names a sink when something declares an optional connection to it', () => {
    // The counter-example to the test above, and the reason the two signals are not merged:
    // deleting the ElectricityMeter raises no error either (the port that wants it is
    // optional), but GenericHeatPumpController *declares* ElectricityMeter as the source
    // for its ElectricityInput — so port analysis names it, and the usage-based section
    // does not repeat it.
    const text = withoutComponent(basic, 'ElectricityMeter')
    expect(errorCount(text)).toBe(errorCount(basic))

    const { missing, companions } = suggestFor(text)
    const meter = missing.find((m) => shortName(m.entry) === 'ElectricityMeter')
    expect(meter).toBeDefined()
    expect(meter?.fixes.some((f) => f.declared && !f.mandatory)).toBe(true)
    expect(companions.map((c) => shortName(c.entry))).not.toContain('ElectricityMeter')
  })

  it('backs each usage-based proposal with the scenarios it counted', () => {
    const { companions } = suggestFor(withoutComponent(basic, 'PVSystem'))
    expect(companions.length).toBeGreaterThan(0)
    for (const suggestion of companions) {
      expect(suggestion.companions.length).toBeGreaterThan(0)
      for (const evidence of suggestion.companions) {
        expect(evidence.together).toBeLessThanOrEqual(evidence.outOf)
        expect(evidence.outOf).toBeLessThanOrEqual(usage.scenario_count)
      }
    }
  })
})

describe('suggestions for an empty canvas', () => {
  it('offers the components most scenarios start from', () => {
    const { missing, companions } = suggestComponents([], [], db, usage)
    expect(missing).toEqual([])
    expect(companions.length).toBeGreaterThan(0)
    // Weather and the occupancy profile are in almost every shipped scenario.
    expect(companions.map((c) => shortName(c.entry))).toContain('Weather')
  })

  it('degrades to nothing at all rather than guessing without usage data', () => {
    const { missing, companions } = suggestComponents([], [], db, null)
    expect(missing).toEqual([])
    expect(companions).toEqual([])
  })
})

// ── Recovery rate over every shipped scenario ────────────────────────────────
//
// Delete each component in turn and see whether the engine names it. The two signals are
// measured separately because they cover disjoint cases: port analysis only sees a
// component something declares a dependency on, usage statistics only see what commonly
// travels together. Thresholds sit well below the measured rates (port analysis, rank 1:
// 91% / top 3: 98%; co-occurrence on the cases port analysis cannot see, top 3: 70%) so
// ordinary registry churn does not fail the build — a real regression halves these
// numbers, it does not shave a point off them.
describe('recovery rate (leave-one-out over the shipped scenarios)', () => {
  interface Outcome {
    /** Rank of the deleted component in the proposal list, or null if absent. */
    portRank: number | null
    companionRank: number | null
    /** Whether deleting it left an unconnected mandatory input for port analysis to see. */
    detectable: boolean
  }

  const outcomes: Outcome[] = []

  for (const file of files) {
    const text = readScenario(file)
    const baseErrors = errorCount(text)
    /* eslint-disable @typescript-eslint/no-explicit-any */
    const originals: any[] = JSON.parse(text).components ?? []
    for (const original of originals) {
      const name = String(original?.configuration?.name ?? '')
      const removedClass = String(original?.component_full_classname ?? '')
      if (name === '') continue
      if (!db.components.some((c) => c.component_full_classname === removedClass)) continue

      const reduced = withoutComponent(text, name)
      const remaining: any[] = JSON.parse(reduced).components ?? []
      if (remaining.length === 0) continue
      // A second instance of the same class still on the canvas makes "is it missing?"
      // meaningless.
      if (remaining.some((c) => c?.component_full_classname === removedClass)) continue

      // One import, used for both the validation and the suggestion, so the measurement
      // sees exactly the graph the editor would show.
      const imported = importScenario(reduced, db)
      const { missing, companions } = suggestComponents(
        imported.nodes,
        imported.edges,
        db,
        usage,
      )
      const rankIn = (list: { entry: ComponentEntry }[]) => {
        const i = list.findIndex((s) => s.entry.component_full_classname === removedClass)
        return i === -1 ? null : i + 1
      }
      outcomes.push({
        portRank: rankIn(missing),
        companionRank: rankIn(companions),
        detectable: validateScenario(imported.nodes, imported.edges).errors.length > baseErrors,
      })
    }
    /* eslint-enable @typescript-eslint/no-explicit-any */
  }

  const share = (subset: Outcome[], predicate: (o: Outcome) => boolean) =>
    subset.length === 0 ? 0 : subset.filter(predicate).length / subset.length

  it('has something to measure', () => {
    expect(outcomes.length).toBeGreaterThan(100)
  })

  it('recovers deletions that leave an open mandatory input', () => {
    const detectable = outcomes.filter((o) => o.detectable)
    expect(detectable.length).toBeGreaterThan(50)
    expect(share(detectable, (o) => o.portRank === 1)).toBeGreaterThanOrEqual(0.75)
    expect(share(detectable, (o) => o.portRank !== null && o.portRank <= 3)).toBeGreaterThanOrEqual(0.85)
  })

  it('recovers silent deletions from usage statistics', () => {
    // Deleting a sink leaves the graph structurally intact, so only co-occurrence can see
    // it. Measured against the shipped scenarios *including* the one under test, which the
    // usage database was built from — the browser is in the same position, since the user's
    // scenario is not in that database either way.
    const silent = outcomes.filter((o) => !o.detectable)
    expect(silent.length).toBeGreaterThan(20)
    expect(share(silent, (o) => o.companionRank !== null && o.companionRank <= 3)).toBeGreaterThanOrEqual(0.5)
  })
})
