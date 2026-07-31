import type { Edge } from '@xyflow/react'
import type {
  ComponentDb,
  ComponentEntry,
  DynamicInputPort,
  DynamicOutputPort,
} from '../types'
import type { HiSimNode } from '../store'
import { getLoadTypeColor } from '../data/loadTypeColors'
import { autoConnectNode as autoConnectNodeFn } from './autoConnect'
import { syncDynamicPorts } from './dynamicPorts'
import { autoLayout } from './layout'
import { activeInputPorts, activeOutputPorts, portLoadType, portUnit } from './ports'

export interface ImportResult {
  nodes: HiSimNode[]
  edges: Edge[]
  scenarioName: string
  scenarioDescription: string
  warnings: string[]
}

/**
 * Rebuild the field name HiSim gives a dynamic input.
 *
 * `DynamicComponent.add_component_input_and_connect` labels the port
 * `Input_{source}_{output}_{len(self.inputs)}` — and `self.inputs` already holds the
 * component's *static* inputs, so the index starts at the static input count, not at 0.
 * (That off-by-N is why connections into e.g. the EMS's `Input_..._2` … `Input_..._6` ports
 * used to be dropped on import.)
 */
function synthesiseDynamicInputName(
  inp: Record<string, unknown>,
  index: number,
): string {
  return `Input_${inp.source_object_name}_${inp.source_component_output}_${index}`
}

/**
 * Rebuild the field name HiSim gives a dynamic output.
 *
 * `DynamicComponent.add_component_output` appends `Output{len(self.outputs) + 1}` to the
 * declared prefix, counting the component's existing outputs — so the first scenario-declared
 * output lands one past the registry's output count.
 */
function synthesiseDynamicOutputName(prefix: string, index: number): string {
  return `${prefix}Output${index}`
}

/** Parse a component's raw `inputs[]` array into typed DynamicInputPort objects. */
function parseDynamicInputs(
  comp: Record<string, unknown>,
  entry: ComponentEntry,
  config: Record<string, unknown>,
): DynamicInputPort[] {
  const rawInputs = (comp.inputs as Array<Record<string, unknown>>) ?? []
  const base = activeInputPorts(entry.input_ports, config).length
  return rawInputs
    .filter((inp) => inp.dynamic === true)
    .map((inp, i) => ({
      field_name: synthesiseDynamicInputName(inp, base + i),
      load_type: String(inp.source_load_type ?? 'Any'),
      unit: String(inp.source_unit ?? '-'),
      source_object_name: String(inp.source_object_name ?? ''),
      source_component_output: String(inp.source_component_output ?? ''),
      source_tags: (inp.source_tags as string[]) ?? [],
      source_weight: Number(inp.source_weight ?? 0),
    }))
}

/** Parse a component's raw `outputs[]` array into typed DynamicOutputPort objects. */
function parseDynamicOutputs(
  comp: Record<string, unknown>,
  entry: ComponentEntry,
  config: Record<string, unknown>,
): DynamicOutputPort[] {
  const rawOutputs = (comp.outputs as Array<Record<string, unknown>>) ?? []
  const base = activeOutputPorts(entry.output_ports, config).length
  return rawOutputs
    .filter((out) => out.dynamic === true)
    .map((out, i) => {
      const prefix = String(out.source_output_name ?? '')
      return {
        field_name: synthesiseDynamicOutputName(prefix, base + i + 1),
        source_output_name: prefix,
        load_type: String(out.source_load_type ?? 'Any'),
        unit: String(out.source_unit ?? '-'),
        source_tags: (out.source_tags as string[]) ?? [],
        source_weight: Number(out.source_weight ?? 0),
        output_description: String(out.output_description ?? ''),
        source_component_class:
          out.source_component_class === undefined || out.source_component_class === null
            ? null
            : String(out.source_component_class),
      }
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

  // ── Pass 1: collect valid (comp, entry, dynamic port) tuples ──────────────
  const valid: Array<{
    comp: Record<string, unknown>
    entry: ComponentEntry
    config: Record<string, unknown>
    dynamicInputs: DynamicInputPort[]
    dynamicOutputs: DynamicOutputPort[]
  }> = []

  for (const comp of rawComponents) {
    const classname = comp.component_full_classname as string
    const entry = entryMap.get(classname)
    if (!entry) {
      warnings.push(`Not in registry (skipped): ${classname}`)
      continue
    }
    const config = (comp.configuration ?? {}) as Record<string, unknown>
    valid.push({
      comp,
      entry,
      config,
      dynamicInputs: parseDynamicInputs(comp, entry, config),
      dynamicOutputs: parseDynamicOutputs(comp, entry, config),
    })
  }

  // ── Pass 2: create nodes (positions resolved after edges are built) ──────
  const nodes: HiSimNode[] = valid.map(({ comp, entry, config, dynamicInputs, dynamicOutputs }) => {
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
        dynamicOutputs: dynamicOutputs.length > 0 ? dynamicOutputs : undefined,
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

    // Source may be a static output port OR a dynamic output port declared by the scenario
    const staticOutPort = srcNode.data.entry.output_ports.find(
      (p) => p.field_name === src.field_name,
    )
    const dynOutput = !staticOutPort
      ? srcNode.data.dynamicOutputs?.find((p) => p.field_name === src.field_name)
      : undefined
    const outPort = staticOutPort ?? dynOutput

    if (!outPort) {
      // Dropping a connection loses scenario content, so it must be reported — a silent
      // skip here makes the edge vanish and then resurfaces as a misleading downstream
      // error ("mandatory port not connected") on a scenario that is perfectly valid.
      warnings.push(
        `Connection dropped: "${src.component_name}.${src.field_name}" is not a known output port.`,
      )
      continue
    }

    // Target may be a static input port OR a dynamic input port
    const staticInPort = tgtNode.data.entry.input_ports.find((p) => p.field_name === tgt.field_name)
    const dynInput = !staticInPort
      ? tgtNode.data.dynamicInputs?.find((p) => p.field_name === tgt.field_name)
      : undefined

    if (!staticInPort && !dynInput) {
      warnings.push(
        `Connection dropped: "${tgt.component_name}.${tgt.field_name}" is not a known input port.`,
      )
      continue
    }

    const loadType = staticOutPort
      ? portLoadType(staticOutPort, srcNode.data.config)
      : outPort.load_type
    const unit = staticOutPort ? portUnit(staticOutPort, srcNode.data.config) : outPort.unit

    edges.push({
      id: `e-${nodeSeq++}`,
      source: srcNode.id,
      target: tgtNode.id,
      sourceHandle: `output-${src.field_name}`,
      targetHandle: `input-${tgt.field_name}`,
      style: { stroke: getLoadTypeColor(loadType), strokeWidth: 2 },
      data: { loadType, unit },
    })
  }

  // ── Pass 4: auto-connect nodes with connect_automatically: true ───────────
  let accEdges = [...edges]
  for (const node of nodes) {
    if (!node.data.connectAutomatically) continue
    const { newEdges } = autoConnectNodeFn(node, nodes, accEdges)
    accEdges = [...accEdges, ...newEdges]
  }

  // ── Pass 4b: dynamic ports the simulator creates at run time ──────────────
  // A dynamic component carrying connect_automatically grows one input per matching source
  // component when HiSim starts (io/dynamicPorts.ts). The file does not list them — that is
  // the point of the flag — so without this the canvas would show an EMS with no ports at all
  // and no way to see, or wire to, what the simulation will actually have.
  const synced = syncDynamicPorts(nodes, accEdges)
  const nodesWithDynamicPorts = synced.nodes
  accEdges = synced.edges

  // ── Pass 5: assign positions ──────────────────────────────────────────────
  // Use DAG auto-layout as the base; override with any saved positions from
  // a prior editor session (_editor_positions field written by export.ts).
  const laidOut = autoLayout(nodesWithDynamicPorts, accEdges)
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
