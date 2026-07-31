import type { Edge } from '@xyflow/react'
import type { HiSimNode } from '../store'
import type { DynamicInputPort, DynamicOutputPort } from '../types'
import { activeInputPorts, portLoadType, portUnit } from './ports'

export interface ValidationResult {
  errors: string[]
  warnings: string[]
  infos: string[]
}

export function validateScenario(nodes: HiSimNode[], edges: Edge[]): ValidationResult {
  const errors: string[] = []
  const warnings: string[] = []
  const infos: string[] = []

  if (nodes.length === 0) {
    warnings.push('Canvas is empty.')
    return { errors, warnings, infos }
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

    // Only ports that this component's configuration actually declares — a conditional port
    // (see io/ports.ts) is not missing when its config switch is off.
    for (const port of activeInputPorts(node.data.entry.input_ports, node.data.config)) {
      const handle = `input-${port.field_name}`
      const connected = connectedHandles.has(handle)
      if (port.mandatory && !connected) {
        errors.push(
          `${node.data.instanceName}: mandatory port "${port.field_name}" is not connected.`,
        )
      } else if (!port.mandatory && !connected) {
        infos.push(
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

    // Dynamic ports carry load_type/unit just like static ones, so they take part in the
    // compatibility check too.
    const outPort =
      srcNode.data.entry.output_ports.find((p) => p.field_name === outName) ??
      (srcNode.data.dynamicOutputs as DynamicOutputPort[] | undefined)?.find(
        (p) => p.field_name === outName,
      )
    const inPort =
      tgtNode.data.entry.input_ports.find((p) => p.field_name === inName) ??
      (tgtNode.data.dynamicInputs as DynamicInputPort[] | undefined)?.find(
        (p) => p.field_name === inName,
      )

    if (!outPort || !inPort) continue

    // Resolve config-derived types against each node's own config, not the registry default.
    const outLoadType = portLoadType(outPort, srcNode.data.config)
    const inLoadType = portLoadType(inPort, tgtNode.data.config)
    const outUnit = portUnit(outPort, srcNode.data.config)
    const inUnit = portUnit(inPort, tgtNode.data.config)
    const edgeLabel =
      `${srcNode.data.instanceName}.${outName} → ${tgtNode.data.instanceName}.${inName}`

    // The two checks are deliberately independent, and at different severities.
    //
    // A **unit** mismatch is the one that can actually break a simulation: HiSim passes the
    // raw float straight down the channel, so a W output feeding a kW input is a silent
    // factor-1000 error. That is a warning. Units.ANY ("-") means "unitless", so it opts out.
    if (outUnit !== inUnit && outUnit !== '-' && inUnit !== '-') {
      warnings.push(`${edgeLabel}: unit mismatch (${outUnit} ≠ ${inUnit}).`)
    }

    // A **load type** difference is a labelling inconsistency, not a wiring fault. Nothing in
    // HiSim reads a port's load type — Component.connect_input matches on field name alone,
    // dynamic components match on tags and source_weight, and the only consumers of
    // ComponentOutput.load_type are a display string and JSON serialisation. Component authors
    // therefore label the same channel differently in good faith (a storage calls its outlet
    // Water; the consumer calls its inlet a Temperature), and the shipped setups are full of
    // it. Reporting these as warnings drowned out the unit check above, so they are infos.
    if (outLoadType !== inLoadType && outLoadType !== 'Any' && inLoadType !== 'Any') {
      infos.push(`${edgeLabel}: load type mismatch (${outLoadType} ≠ ${inLoadType}).`)
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
    const hasStaticOut = srcNode.data.entry.output_ports.some((p) => p.field_name === outName)
    const hasDynOut = (srcNode.data.dynamicOutputs as DynamicOutputPort[] | undefined)?.some(
      (p) => p.field_name === outName,
    )
    if (!hasStaticOut && !hasDynOut) {
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

  // ── 7: connect_automatically that HiSim would refuse ──────────────────────
  //
  // `Simulator.connect_everything_automatically` raises KeyError for a component registered
  // with the flag that either declares no default connections at all, or declares some but
  // finds no matching source in the setup. Both kill the run at prepare_calculation, before
  // the first time step, and neither is visible anywhere else on the canvas — a Weather
  // dropped from the palette looks perfectly wired.
  for (const node of nodes) {
    if (!node.data.connectAutomatically) continue
    const entry = node.data.entry
    const sources = Object.keys(entry.default_connections ?? {})
    const dynamicSources = Object.keys(entry.dynamic_default_connections ?? {})
    const declared = [...sources, ...dynamicSources]

    if (declared.length === 0) {
      errors.push(
        `${node.data.instanceName}: "connect automatically" is on but the component declares ` +
        'no default connections — HiSim raises KeyError. Switch it off.',
      )
      continue
    }
    const matches = (className: string) =>
      nodes.some(
        (n) =>
          n.id !== node.id &&
          (n.data.entry.component_full_classname.endsWith(`.${className}`) ||
            n.data.entry.display_name === className),
      )
    if (!declared.some(matches)) {
      errors.push(
        `${node.data.instanceName}: "connect automatically" is on but none of its default ` +
        `connection sources (${declared.join(', ')}) is on the canvas — HiSim raises KeyError.`,
      )
    }
  }

  // ── 5: Required config fields non-null ────────────────────────────────────
  const isEmpty = (v: unknown) => v === null || v === undefined || v === ''
  for (const node of nodes) {
    for (const field of node.data.entry.config_fields) {
      if (field.name === 'name' || field.is_optional) continue
      // A field whose own default is empty may legitimately stay empty
      // (e.g. UtspLpgConnector.guid defaults to ""), so don't flag it.
      if (isEmpty(field.default)) continue
      if (isEmpty(node.data.config[field.name])) {
        warnings.push(
          `${node.data.instanceName}: required config field "${field.name}" is empty.`,
        )
      }
    }
  }

  return { errors, warnings, infos }
}
