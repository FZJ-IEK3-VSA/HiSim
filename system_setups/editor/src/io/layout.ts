import type { Edge } from '@xyflow/react'
import type { HiSimNode } from '../store'
import type { ComponentEntry } from '../types'

const CARD_WIDTH = 260
const H_GAP = 300
const HEADER_H = 37
const PORT_ROW_H = 28
const CARD_GAP = 40
const ORIGIN_X = 60
const ORIGIN_Y = 60

function cardHeight(entry: ComponentEntry, dynamicCount = 0): number {
  const staticRows = Math.max(entry.input_ports.length, entry.output_ports.length)
  return HEADER_H + (staticRows + dynamicCount) * PORT_ROW_H
}

/**
 * Assign left-to-right positions using longest-path layering (Kahn's algorithm).
 * Source nodes (no incoming edges) sit in column 0; each downstream layer is
 * one column further right. Cyclic nodes are placed in column 0.
 */
export function autoLayout(nodes: HiSimNode[], edges: Edge[]): HiSimNode[] {
  if (nodes.length === 0) return nodes

  const nodeIds = new Set(nodes.map((n) => n.id))

  const outEdges = new Map<string, string[]>()
  const inDegree = new Map<string, number>()
  for (const n of nodes) { outEdges.set(n.id, []); inDegree.set(n.id, 0) }
  for (const e of edges) {
    if (!nodeIds.has(e.source) || !nodeIds.has(e.target)) continue
    if (e.source === e.target) continue
    outEdges.get(e.source)!.push(e.target)
    inDegree.set(e.target, (inDegree.get(e.target) ?? 0) + 1)
  }

  // Longest-path layering via Kahn's algorithm.
  // A node is only enqueued once all its predecessors have been assigned a layer,
  // so the queue drains in finite time even when the graph contains cycles
  // (cyclic nodes never reach inDegree 0 and are placed in layer 0 afterwards).
  const layer = new Map<string, number>()
  const remaining = new Map(inDegree)  // working copy we decrement
  const queue: string[] = []
  for (const n of nodes) {
    if ((remaining.get(n.id) ?? 0) === 0) { layer.set(n.id, 0); queue.push(n.id) }
  }
  while (queue.length > 0) {
    const current = queue.shift()!
    const cl = layer.get(current) ?? 0
    for (const next of (outEdges.get(current) ?? [])) {
      // Track the longest path to 'next' seen so far
      if (!layer.has(next) || layer.get(next)! < cl + 1) layer.set(next, cl + 1)
      const deg = (remaining.get(next) ?? 1) - 1
      remaining.set(next, deg)
      if (deg === 0) queue.push(next)  // all predecessors done — safe to finalize
    }
  }
  // Nodes in cycles were never dequeued; fall back to layer 0
  for (const n of nodes) if (!layer.has(n.id)) layer.set(n.id, 0)

  // Group nodes into layers (preserve input order within each layer)
  const maxLayer = Math.max(...[...layer.values()])
  const layerNodes = new Map<number, HiSimNode[]>()
  for (let l = 0; l <= maxLayer; l++) layerNodes.set(l, [])
  for (const n of nodes) layerNodes.get(layer.get(n.id)!)!.push(n)

  // Assign positions
  const positioned = new Map<string, { x: number; y: number }>()
  for (let l = 0; l <= maxLayer; l++) {
    const col = layerNodes.get(l) ?? []
    const x = ORIGIN_X + l * (CARD_WIDTH + H_GAP)
    let y = ORIGIN_Y
    for (const n of col) {
      positioned.set(n.id, { x, y })
      const dynCount = (n.data.dynamicInputs as unknown[] | undefined)?.length ?? 0
      y += cardHeight(n.data.entry, dynCount) + CARD_GAP
    }
  }

  return nodes.map((n) => ({ ...n, position: positioned.get(n.id) ?? n.position }))
}
