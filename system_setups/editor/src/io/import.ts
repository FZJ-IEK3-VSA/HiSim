import type { Edge } from '@xyflow/react'
import type { ComponentDb, ComponentEntry } from '../types'
import type { HiSimNode } from '../store'
import { getLoadTypeColor } from '../data/loadTypeColors'

export interface ImportResult {
  nodes: HiSimNode[]
  edges: Edge[]
  scenarioName: string
  scenarioDescription: string
  warnings: string[]
}

// Card layout constants — must match ComponentCard.tsx
const HEADER_H = 37
const PORT_ROW_H = 28
const CARD_GAP = 24    // vertical gap between cards in the same column
const COLS = 3
const COL_W = 320      // card width (260) + horizontal gap (60)
const ORIGIN_X = 60
const ORIGIN_Y = 60

/** Estimated rendered height of a collapsed card (header + port rows). */
function cardHeight(entry: ComponentEntry): number {
  const portRows = Math.max(entry.input_ports.length, entry.output_ports.length)
  return HEADER_H + portRows * PORT_ROW_H
}

/**
 * Compute non-overlapping positions for a list of entries.
 * Each column advances its Y cursor by the actual estimated card height,
 * so tall cards (many ports) don't overlap the next card below them.
 */
function computePositions(entries: ComponentEntry[]): { x: number; y: number }[] {
  const colY = Array<number>(COLS).fill(ORIGIN_Y)
  return entries.map((entry, i) => {
    const col = i % COLS
    const pos = { x: col * COL_W + ORIGIN_X, y: colY[col] }
    colY[col] += cardHeight(entry) + CARD_GAP
    return pos
  })
}

export function importScenario(text: string, componentDb: ComponentDb): ImportResult {
  const warnings: string[] = []
  let json: Record<string, unknown>

  try {
    json = JSON.parse(text) as Record<string, unknown>
  } catch {
    return {
      nodes: [],
      edges: [],
      scenarioName: 'Untitled scenario',
      scenarioDescription: '',
      warnings: ['Invalid JSON file.'],
    }
  }

  const entryMap = new Map(componentDb.components.map((c) => [c.component_full_classname, c]))

  // Saved positions from a previous editor session (Phase 11 writes these)
  const savedPositions = (json._editor_positions ?? {}) as Record<string, { x: number; y: number }>

  const rawComponents = (json.components as Record<string, unknown>[]) ?? []
  let nodeSeq = Date.now()

  // ── Pass 1: collect valid (comp, entry) pairs and warn about unknowns ────────
  const valid: Array<{ comp: Record<string, unknown>; entry: ComponentEntry }> = []
  for (const comp of rawComponents) {
    const classname = comp.component_full_classname as string
    const entry = entryMap.get(classname)
    if (!entry) {
      warnings.push(`Not in registry (skipped): ${classname}`)
      continue
    }
    valid.push({ comp, entry })
  }

  // ── Pass 2: compute height-aware auto-layout positions for all valid cards ──
  const autoPositions = computePositions(valid.map((v) => v.entry))

  // ── Pass 3: create nodes, preferring saved positions over auto-layout ────────
  const nodes: HiSimNode[] = valid.map(({ comp, entry }, i) => {
    const config = (comp.configuration ?? {}) as Record<string, unknown>
    const instanceName = String(config.name ?? entry.display_name)
    const position = savedPositions[instanceName] ?? autoPositions[i]

    return {
      id: `n-${nodeSeq++}`,
      type: 'componentCard',
      position,
      data: {
        entry,
        instanceName,
        config,
        collapsed: true,
        connectAutomatically: Boolean(comp.connect_automatically ?? false),
      },
    }
  })

  // Build instance-name → node map for edge creation
  const nodeByName = new Map(nodes.map((n) => [n.data.instanceName, n]))

  const rawConnections = (json.connections as Record<string, unknown>[]) ?? []
  const edges: Edge[] = []

  for (const conn of rawConnections) {
    const src = conn.source as { component_name: string; field_name: string }
    const tgt = conn.target as { component_name: string; field_name: string }

    const srcNode = nodeByName.get(src.component_name)
    const tgtNode = nodeByName.get(tgt.component_name)

    if (!srcNode) {
      warnings.push(`Connection: unknown source "${src.component_name}" (skipped)`)
      continue
    }
    if (!tgtNode) {
      warnings.push(`Connection: unknown target "${tgt.component_name}" (skipped)`)
      continue
    }

    const outPort = srcNode.data.entry.output_ports.find((p) => p.field_name === src.field_name)
    if (!outPort) {
      // Likely a dynamic port — skip silently to avoid noise for files with many dynamic inputs
      continue
    }

    const inPort = tgtNode.data.entry.input_ports.find((p) => p.field_name === tgt.field_name)
    if (!inPort && !tgtNode.data.entry.is_dynamic) {
      continue
    }

    edges.push({
      id: `e-${nodeSeq++}`,
      source: srcNode.id,
      target: tgtNode.id,
      sourceHandle: `output-${src.field_name}`,
      targetHandle: `input-${tgt.field_name}`,
      style: { stroke: getLoadTypeColor(outPort.load_type), strokeWidth: 2 },
      data: { loadType: outPort.load_type, unit: outPort.unit },
    })
  }

  return {
    nodes,
    edges,
    scenarioName: String(json.name ?? 'Untitled scenario'),
    scenarioDescription: String(json.description ?? ''),
    warnings,
  }
}
