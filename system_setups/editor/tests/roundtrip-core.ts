// Round-trip comparison logic shared by the Vitest unit suite
// (tests/roundtrip.test.ts) and the Playwright E2E suite (tests/e2e/roundtrip.spec.ts).
//
// Pure — no filesystem or browser access — so it runs in any Node context. It calls
// the exact functions the web UI calls (importScenario / exportScenario / validateScenario),
// so the comparison faithfully mirrors "Open JSON" followed by "Save JSON".

import { importScenario } from '../src/io/import'
import { exportScenario } from '../src/io/export'
import { validateScenario } from '../src/io/validate'
import type { ComponentDb } from '../src/types'

/** "Open" a scenario JSON string and immediately "Save" it — the editor's open→save path. */
export function roundTrip(text: string, db: ComponentDb): string {
  const r = importScenario(text, db)
  return exportScenario(r.nodes, r.edges, r.scenarioName, r.scenarioDescription)
}

/** Warnings surfaced by import (dropped components, dangling connection endpoints). */
export function importWarnings(text: string, db: ComponentDb): string[] {
  return importScenario(text, db).warnings
}

/** Validation *errors* (not warnings/infos) for an imported scenario. */
export function validationErrorsFor(text: string, db: ComponentDb): string[] {
  const r = importScenario(text, db)
  return validateScenario(r.nodes, r.edges).errors
}

/**
 * Whether a scenario exercises HiSim's *dynamic* component ports (dynamic inputs/outputs).
 *
 * The editor does not yet fully round-trip these: dynamic `outputs` are not persisted, and
 * dynamic-input connection indices are re-synthesised on import (see tests/README.md). For
 * such scenarios only the universal invariants (no dropped components, idempotency) are
 * asserted; full semantic equality is asserted for the dynamic-free scenarios. When the
 * editor learns to preserve dynamic ports, this gate can be removed.
 */
export function usesDynamicPorts(text: string): boolean {
  let j: { components?: unknown[]; connections?: unknown[] }
  try {
    j = JSON.parse(text)
  } catch {
    return false
  }
  const comps = (j.components ?? []) as Array<Record<string, unknown>>
  const hasDynDecl = comps.some(
    (c) =>
      (Array.isArray(c.inputs) && c.inputs.length > 0) ||
      (Array.isArray(c.outputs) && c.outputs.length > 0),
  )
  const conns = (j.connections ?? []) as Array<Record<string, { field_name?: string }>>
  const hasDynConn = conns.some((c) => /^Input_.+_\d+$/.test(c?.target?.field_name ?? ''))
  return hasDynDecl || hasDynConn
}

// ── Canonicalisation ─────────────────────────────────────────────────────────

/** Recursively key-sorted JSON string — a stable, order-insensitive value fingerprint. */
function stable(v: unknown): string {
  return JSON.stringify(sortKeys(v))
}

function sortKeys(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(sortKeys)
  if (v && typeof v === 'object') {
    const obj = v as Record<string, unknown>
    return Object.fromEntries(
      Object.keys(obj)
        .sort()
        .map((k) => [k, sortKeys(obj[k])]),
    )
  }
  return v
}

/* eslint-disable @typescript-eslint/no-explicit-any */
const connKey = (c: any): string =>
  `${c?.source?.component_name}.${c?.source?.field_name} → ` +
  `${c?.target?.component_name}.${c?.target?.field_name}`

const compName = (c: any): string => String(c?.configuration?.name ?? '')

/**
 * Compare two scenario JSON strings for *semantic* equality, ignoring things that are
 * not meaningful content:
 *   - editor-only metadata (`_editor_positions`)
 *   - object key ordering
 *   - number formatting (`1.0` vs `1`, `1e-3` vs `0.001`) — both sides are parsed
 *   - connection ordering
 *
 * Returns a list of human-readable differences. An empty list means the round-trip
 * preserved everything that matters.
 */
export function diffScenario(originalText: string, roundTrippedText: string): string[] {
  const a = JSON.parse(originalText)
  const b = JSON.parse(roundTrippedText)
  const diffs: string[] = []

  // Top-level scalar fields. `multiple_buildings` defaults to false when absent so that
  // an omitted original still matches the exporter, which always writes it explicitly.
  for (const key of ['name', 'description', 'multiple_buildings'] as const) {
    const fallback = key === 'multiple_buildings' ? false : ''
    const av = a[key] ?? fallback
    const bv = b[key] ?? fallback
    if (stable(av) !== stable(bv)) {
      diffs.push(`${key} differs: ${JSON.stringify(av)} → ${JSON.stringify(bv)}`)
    }
  }

  // Components, keyed by configuration.name (component order is not semantically meaningful).
  const aComps = new Map<string, any>((a.components ?? []).map((c: any) => [compName(c), c]))
  const bComps = new Map<string, any>((b.components ?? []).map((c: any) => [compName(c), c]))

  for (const name of aComps.keys()) {
    if (!bComps.has(name)) diffs.push(`component removed by round-trip: "${name}"`)
  }
  for (const name of bComps.keys()) {
    if (!aComps.has(name)) diffs.push(`component added by round-trip: "${name}"`)
  }

  const compFields = [
    'component_full_classname',
    'config_full_classname',
    'configuration',
    'inputs',
    'outputs',
    'connect_automatically',
  ] as const

  for (const [name, ac] of aComps) {
    const bc = bComps.get(name)
    if (!bc) continue
    for (const field of compFields) {
      const av = ac[field]
      const bv = bc[field]
      if (stable(av) !== stable(bv)) {
        const detail =
          Array.isArray(av) || Array.isArray(bv)
            ? ` (original ${Array.isArray(av) ? av.length : '—'} vs round-trip ${
                Array.isArray(bv) ? bv.length : '—'
              })`
            : ''
        diffs.push(`component "${name}": ${field} differs${detail}`)
      }
    }
  }

  // Connections as an unordered set of "src.field → tgt.field" keys.
  const aConns = new Set<string>((a.connections ?? []).map(connKey))
  const bConns = new Set<string>((b.connections ?? []).map(connKey))
  for (const c of aConns) if (!bConns.has(c)) diffs.push(`connection removed by round-trip: ${c}`)
  for (const c of bConns) if (!aConns.has(c)) diffs.push(`connection added by round-trip: ${c}`)

  return diffs
}
/* eslint-enable @typescript-eslint/no-explicit-any */
