import { create } from 'zustand'
import { applyNodeChanges, applyEdgeChanges, addEdge as rfAddEdge } from '@xyflow/react'
import type { Node, Edge, NodeChange, EdgeChange, Connection } from '@xyflow/react'
import type { ComponentDb, EnumDb, ComponentNodeData } from '../types'
import { getLoadTypeColor } from '../data/loadTypeColors'

export type HiSimNode = Node<ComponentNodeData>

interface EditorState {
  componentDb: ComponentDb | null
  enumDb: EnumDb | null
  nodes: HiSimNode[]
  edges: Edge[]
  selectedNodeId: string | null
  validationMessages: string[]
}

interface EditorActions {
  loadDatabases: (db: ComponentDb, edb: EnumDb) => void
  setNodes: (nodes: HiSimNode[]) => void
  setEdges: (edges: Edge[]) => void
  onNodesChange: (changes: NodeChange<HiSimNode>[]) => void
  onEdgesChange: (changes: EdgeChange[]) => void
  addNode: (node: HiSimNode) => void
  connect: (connection: Connection) => void
  setSelectedNodeId: (id: string | null) => void
  updateNodeData: (nodeId: string, patch: Partial<ComponentNodeData>) => void
  setValidationMessages: (messages: string[]) => void
  reset: () => void
}

export const useEditorStore = create<EditorState & EditorActions>()((set, get) => ({
  componentDb: null,
  enumDb: null,
  nodes: [],
  edges: [],
  selectedNodeId: null,
  validationMessages: [],

  loadDatabases: (db, edb) => set({ componentDb: db, enumDb: edb }),

  setNodes: (nodes) => set({ nodes }),
  setEdges: (edges) => set({ edges }),

  onNodesChange: (changes) =>
    set((s) => ({ nodes: applyNodeChanges(changes, s.nodes) })),

  onEdgesChange: (changes) =>
    set((s) => ({ edges: applyEdgeChanges(changes, s.edges) })),

  addNode: (node) => set((s) => ({ nodes: [...s.nodes, node] })),

  connect: (connection) => {
    const { nodes, edges } = get()
    const sourceNode = nodes.find((n) => n.id === connection.source)
    const portName = connection.sourceHandle?.replace('output-', '') ?? ''
    const outPort = sourceNode?.data.entry.output_ports.find((p) => p.field_name === portName)
    const loadType = outPort?.load_type ?? 'Any'

    const newEdge: Edge = {
      ...connection,
      id: `e-${Date.now()}`,
      style: { stroke: getLoadTypeColor(loadType), strokeWidth: 2 },
      data: { loadType, unit: outPort?.unit ?? '' },
    }
    set({ edges: rfAddEdge(newEdge, edges) })
  },

  setSelectedNodeId: (id) => set({ selectedNodeId: id }),

  updateNodeData: (nodeId, patch) =>
    set((s) => ({
      nodes: s.nodes.map((n) =>
        n.id === nodeId ? { ...n, data: { ...n.data, ...patch } } : n
      ),
    })),

  setValidationMessages: (messages) => set({ validationMessages: messages }),

  reset: () =>
    set({ nodes: [], edges: [], selectedNodeId: null, validationMessages: [] }),
}))
