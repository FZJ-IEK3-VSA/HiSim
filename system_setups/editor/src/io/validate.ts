import type { Edge } from '@xyflow/react'
import type { HiSimNode } from '../store'
import type { DynamicInputPort } from '../types'

export interface ValidationResult {
  errors: string[]
  warnings: string[]
}

export function validateScenario(nodes: HiSimNode[], edges: Edge[]): ValidationResult {
  const errors: string[] = []
  const warnings: string[] = []

  if (nodes.length === 0) {
    warnings.push('Canvas is empty.')
    return { errors, warnings }
  }

  const nodeById = new Map(nodes.map((n) => [n.id, n]))

  // Index incoming edges by target node id for fast lookup
  const edgesByTarget = new Map<string, Edge[]>()
  for (const edge of edges) {
    const list = edgesByTarget.get(edge.target) ?? []
    list.push(edge)
    edgesByTarget.set(edge.target, list)
  }

  // ── 1 & 6: Mandatory / optional unconnected inputs ────────────────────────
  for (const node of nodes) {
    const incoming = edgesByTarget.get(node.id) ?? []
    const connectedHandles = new Set(incoming.map((e) => e.targetHandle))

    for (const port of node.data.entry.input_ports) {
      const handle = `input-${port.field_name}`
      const connected = connectedHandles.has(handle)
      if (port.mandatory && !connected) {
        errors.push(
          `${node.data.instanceName}: mandatory port "${port.field_name}" is not connected.`,
        )
      } else if (!port.mandatory && !connected) {
        warnings.push(
          `${node.data.instanceName}: optional port "${port.field_name}" is not connected.`,
        )
      }
    }
  }

  // ── 2: Edge type / unit compatibility ─────────────────────────────────────
  for (const edge of edges) {
    const srcNode = nodeById.get(edge.source)
    const tgtNode = nodeById.get(edge.target)
    if (!srcNode || !tgtNode) continue

    const outName = edge.sourceHandle?.replace(/^output-/, '') ?? ''
    const inName = edge.targetHandle?.replace(/^input-/, '') ?? ''
    const outPort = srcNode.data.entry.output_ports.find((p) => p.field_name === outName)
    const inPort = tgtNode.data.entry.input_ports.find((p) => p.field_name === inName)

    if (!outPort || !inPort) continue
    if (outPort.load_type === 'Any' || inPort.load_type === 'Any') continue

    if (outPort.load_type !== inPort.load_type) {
      errors.push(
        `${srcNode.data.instanceName}.${outName} → ${tgtNode.data.instanceName}.${inName}: ` +
          `load type mismatch (${outPort.load_type} ≠ ${inPort.load_type}).`,
      )
    } else if (outPort.unit !== inPort.unit) {
      warnings.push(
        `${srcNode.data.instanceName}.${outName} → ${tgtNode.data.instanceName}.${inName}: ` +
          `unit mismatch (${outPort.unit} ≠ ${inPort.unit}).`,
      )
    }
  }

  // ── 3: Duplicate component names ──────────────────────────────────────────
  const nameCounts = new Map<string, number>()
  for (const node of nodes) {
    const name = String(node.data.config.name ?? node.data.instanceName)
    nameCounts.set(name, (nameCounts.get(name) ?? 0) + 1)
  }
  for (const [name, count] of nameCounts) {
    if (count > 1) {
      errors.push(`Duplicate component name: "${name}" (appears ${count} times).`)
    }
  }

  // ── 4: Orphaned edges ─────────────────────────────────────────────────────
  for (const edge of edges) {
    const srcNode = nodeById.get(edge.source)
    const tgtNode = nodeById.get(edge.target)
    if (!srcNode || !tgtNode) {
      errors.push('Orphaned edge: one or both connected nodes no longer exist.')
      continue
    }
    const outName = edge.sourceHandle?.replace(/^output-/, '') ?? ''
    if (!srcNode.data.entry.output_ports.some((p) => p.field_name === outName)) {
      errors.push(
        `Orphaned edge: "${srcNode.data.instanceName}.${outName}" is not a known output port.`,
      )
    }
    const inName = edge.targetHandle?.replace(/^input-/, '') ?? ''
    const hasStaticIn = tgtNode.data.entry.input_ports.some((p) => p.field_name === inName)
    const hasDynIn = (tgtNode.data.dynamicInputs as DynamicInputPort[] | undefined)?.some(
      (p) => p.field_name === inName,
    )
    if (!hasStaticIn && !hasDynIn) {
      errors.push(
        `Orphaned edge: "${tgtNode.data.instanceName}.${inName}" is not a known input port.`,
      )
    }
  }

  // ── 5: Required config fields non-null ────────────────────────────────────
  for (const node of nodes) {
    for (const field of node.data.entry.config_fields) {
      if (field.name === 'name' || field.is_optional) continue
      const val = node.data.config[field.name]
      if (val === null || val === undefined || val === '') {
        warnings.push(
          `${node.data.instanceName}: required config field "${field.name}" is empty.`,
        )
      }
    }
  }

  return { errors, warnings }
}
