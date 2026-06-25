import type { Edge } from '@xyflow/react'
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

  const components = nodes.map((node) => ({
    component_full_classname: node.data.entry.component_full_classname,
    configuration: node.data.config,
    config_full_classname: node.data.entry.config_full_classname,
    inputs: [],   // dynamic inputs not yet supported (Phase 9)
    outputs: [],
    connect_automatically: node.data.connectAutomatically,
  }))

  const connections: unknown[] = []
  for (const edge of edges) {
    const srcNode = nodeById.get(edge.source)
    const tgtNode = nodeById.get(edge.target)
    if (!srcNode || !tgtNode) continue

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

  const scenario: ScenarioJson = {
    name: scenarioName,
    description: scenarioDescription,
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
