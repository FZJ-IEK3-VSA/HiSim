import type { Edge } from '@xyflow/react'
import type { ComponentDb, ComponentEntry, DynamicInputPort } from '../types'
import type { HiSimNode } from '../store'
import { getLoadTypeColor } from '../data/loadTypeColors'
import { autoConnectNode as autoConnectNodeFn } from './autoConnect'
import { autoLayout } from './layout'

export interface ImportResult {
  nodes: HiSimNode[]
  edges: Edge[]
  scenarioName: string
  scenarioDescription: string
  warnings: string[]
}

/** Parse a component's raw `inputs[]` array into typed DynamicInputPort objects. */
function parseDynamicInputs(comp: Record<string, unknown>): DynamicInputPort[] {
  const rawInputs = (comp.inputs as Array<Record<string, unknown>>) ?? []
  return rawInputs
    .filter((inp) => inp.dynamic === true)
    .map((inp, i) => ({
      field_name: `Input_${inp.source_object_name}_${inp.source_component_output}_${i}`,
      load_type: String(inp.source_load_type ?? 'Any'),
      unit: String(inp.source_unit ?? '-'),
      source_object_name: String(inp.source_object_name ?? ''),
      source_component_output: String(inp.source_component_output ?? ''),
      source_tags: (inp.source_tags as string[]) ?? [],
      source_weight: Number(inp.source_weight ?? 0),
    }))
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

  // ── Pass 1: collect valid (comp, entry, dynamicInputs) triples ────────────
  const valid: Array<{
    comp: Record<string, unknown>
    entry: ComponentEntry
    dynamicInputs: DynamicInputPort[]
  }> = []

  for (const comp of rawComponents) {
    const classname = comp.component_full_classname as string
    const entry = entryMap.get(classname)
    if (!entry) {
      warnings.push(`Not in registry (skipped): ${classname}`)
      continue
    }
    valid.push({ comp, entry, dynamicInputs: parseDynamicInputs(comp) })
  }

  // ── Pass 2: create nodes (positions resolved after edges are built) ──────
  const nodes: HiSimNode[] = valid.map(({ comp, entry, dynamicInputs }) => {
    const config = (comp.configuration ?? {}) as Record<string, unknown>
    const instanceName = String(config.name ?? entry.display_name)
    return {
      id: `n-${nodeSeq++}`,
      type: 'componentCard',
      position: { x: 0, y: 0 },  // placeholder — overridden in the layout pass below
      data: {
        entry,
        instanceName,
        config,
        collapsed: true,
        connectAutomatically: Boolean(comp.connect_automatically ?? false),
        dynamicInputs: dynamicInputs.length > 0 ? dynamicInputs : undefined,
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

    // Source must always be a static output port
    const outPort = srcNode.data.entry.output_ports.find((p) => p.field_name === src.field_name)
    if (!outPort) {
      // Dynamic-component outputs are not in the registry — skip silently
      continue
    }

    // Target may be a static input port OR a dynamic input port
    const staticInPort = tgtNode.data.entry.input_ports.find((p) => p.field_name === tgt.field_name)
    const dynInput = !staticInPort
      ? tgtNode.data.dynamicInputs?.find((p) => p.field_name === tgt.field_name)
      : undefined

    if (!staticInPort && !dynInput) {
      // Unknown target port — skip silently
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

  // ── Pass 4: auto-connect nodes with connect_automatically: true ───────────
  let accEdges = [...edges]
  for (const node of nodes) {
    if (!node.data.connectAutomatically) continue
    const { newEdges } = autoConnectNodeFn(node, nodes, accEdges)
    accEdges = [...accEdges, ...newEdges]
  }

  // ── Pass 5: assign positions ──────────────────────────────────────────────
  // Use DAG auto-layout as the base; override with any saved positions from
  // a prior editor session (_editor_positions field written by export.ts).
  const laidOut = autoLayout(nodes, accEdges)
  const finalNodes = laidOut.map((n) => {
    const saved = savedPositions[n.data.instanceName]
    return saved ? { ...n, position: saved } : n
  })

  return {
    nodes: finalNodes,
    edges: accEdges,
    scenarioName: String(json.name ?? 'Untitled scenario'),
    scenarioDescription: String(json.description ?? ''),
    warnings,
  }
}
