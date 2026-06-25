import type { Edge } from '@xyflow/react'
import type { HiSimNode } from '../store'
import { getLoadTypeColor } from '../data/loadTypeColors'

// Module-level counter — never resets, so IDs are unique across all calls in a session
let _edgeSeq = 0

export interface AutoConnectResult {
  newEdges: Edge[]
  /** Input port names for which no unique source could be found. */
  unresolvedPorts: string[]
}

/**
 * Attempt to auto-connect all default_connections declared by `targetNode`.
 *
 * default_connections in the DB is a dict: { SourceClassName: [{target_input_name, source_output_name}] }
 *
 * For each (sourceClassName, conn) pair:
 *  - If the target input port already has an edge → skip (already wired).
 *  - If exactly one canvas node matches sourceClassName → create edge.
 *  - If zero or 2+ candidates → add to unresolvedPorts.
 *
 * @param targetNode   The node whose default_connections we resolve.
 * @param allNodes     All nodes currently on the canvas.
 * @param existingEdges  All edges currently in the graph (used to skip already-wired ports).
 */
export function autoConnectNode(
  targetNode: HiSimNode,
  allNodes: HiSimNode[],
  existingEdges: Edge[],
): AutoConnectResult {
  const newEdges: Edge[] = []
  const unresolvedPorts: string[] = []

  // default_connections: { "SourceClassName": [{target_input_name, source_output_name}] }
  const defaultConns = targetNode.data.entry.default_connections

  for (const [sourceClassName, conns] of Object.entries(defaultConns)) {
    for (const conn of conns) {
      const targetHandle = `input-${conn.target_input_name}`

      // Skip if this input port already has a connection
      const alreadyWired = existingEdges.some(
        (e) => e.target === targetNode.id && e.targetHandle === targetHandle,
      )
      if (alreadyWired) continue

      // Find candidates: nodes whose full classname ends with `.{sourceClassName}`
      const candidates = allNodes.filter(
        (n) =>
          n.id !== targetNode.id &&
          (n.data.entry.component_full_classname.endsWith(`.${sourceClassName}`) ||
            n.data.entry.display_name === sourceClassName),
      )

      if (candidates.length === 1) {
        const src = candidates[0]
        const sourceHandle = `output-${conn.source_output_name}`

        // Skip if this exact edge already exists (e.g. from a previous auto-connect call)
        const edgeExists = existingEdges.some(
          (e) =>
            e.source === src.id &&
            e.sourceHandle === sourceHandle &&
            e.target === targetNode.id &&
            e.targetHandle === targetHandle,
        )
        if (edgeExists) continue

        const outPort = src.data.entry.output_ports.find(
          (p) => p.field_name === conn.source_output_name,
        )
        const loadType = outPort?.load_type ?? 'Any'

        newEdges.push({
          id: `ac-${++_edgeSeq}`,
          source: src.id,
          target: targetNode.id,
          sourceHandle,
          targetHandle,
          style: {
            stroke: getLoadTypeColor(loadType),
            strokeWidth: 2,
            strokeDasharray: '5,4',
          },
          data: { loadType, unit: outPort?.unit ?? '', autoConnected: true },
        })
      } else {
        unresolvedPorts.push(conn.target_input_name)
      }
    }
  }

  return { newEdges, unresolvedPorts }
}
