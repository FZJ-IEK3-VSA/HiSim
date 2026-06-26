import type { Edge } from '@xyflow/react'
import type { DynamicInputPort } from '../types'
import type { HiSimNode } from '../store'

export interface ScenarioJson {
  name: string
  description: string
  multiple_buildings: boolean
  components: unknown[]
  connections: unknown[]
}

export function exportScenario(
  nodes: HiSimNode[],
  edges: Edge[],
  scenarioName: string,
  scenarioDescription: string,
): string {
  // Build a lookup from node id → instance name (= config.name)
  const nodeById = new Map(nodes.map((n) => [n.id, n]))

  const components = nodes.map((node) => {
    const dynInputs = (node.data.dynamicInputs as DynamicInputPort[] | undefined) ?? []
    return {
      component_full_classname: node.data.entry.component_full_classname,
      configuration: node.data.config,
      config_full_classname: node.data.entry.config_full_classname,
      inputs: dynInputs.map((inp) => ({
        dynamic: true,
        source_component_output: inp.source_component_output,
        source_object_name: inp.source_object_name,
        source_load_type: inp.load_type,
        source_unit: inp.unit,
        source_tags: inp.source_tags,
        source_weight: inp.source_weight,
      })),
      outputs: [],
      connect_automatically: node.data.connectAutomatically,
    }
  })

  const connections: unknown[] = []
  for (const edge of edges) {
    const srcNode = nodeById.get(edge.source)
    const tgtNode = nodeById.get(edge.target)
    if (!srcNode || !tgtNode) continue

    // Omit auto-connect edges whose target has connect_automatically:true —
    // HiSim recreates these at runtime, so writing them is redundant.
    if (edge.data?.autoConnected && tgtNode.data.connectAutomatically) continue

    const srcField = edge.sourceHandle?.replace(/^output-/, '')
    const tgtField = edge.targetHandle?.replace(/^input-/, '')
    if (!srcField || !tgtField) continue

    connections.push({
      source: {
        component_name: String(srcNode.data.config.name ?? srcNode.data.instanceName),
        field_name: srcField,
      },
      target: {
        component_name: String(tgtNode.data.config.name ?? tgtNode.data.instanceName),
        field_name: tgtField,
      },
    })
  }

  // Save canvas positions so the editor can restore them on re-import.
  const _editor_positions: Record<string, { x: number; y: number }> = {}
  for (const node of nodes) {
    const name = String(node.data.config.name ?? node.data.instanceName)
    _editor_positions[name] = { x: Math.round(node.position.x), y: Math.round(node.position.y) }
  }

  const scenario = {
    name: scenarioName,
    description: scenarioDescription,
    _editor_positions,
    multiple_buildings: false,
    components,
    connections,
  }

  return JSON.stringify(scenario, null, 4)
}

export function triggerDownload(json: string, filename: string): void {
  const blob = new Blob([json], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
