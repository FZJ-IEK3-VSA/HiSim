import { create } from 'zustand'
import { applyNodeChanges, applyEdgeChanges, addEdge as rfAddEdge } from '@xyflow/react'
import type { Node, Edge, NodeChange, EdgeChange, Connection } from '@xyflow/react'
import type { ComponentDb, EnumDb, ComponentNodeData } from '../types'
import { getLoadTypeColor } from '../data/loadTypeColors'
import { autoConnectNode as autoConnectNodeFn } from '../io/autoConnect'

export type HiSimNode = Node<ComponentNodeData>

interface EditorState {
  componentDb: ComponentDb | null
  enumDb: EnumDb | null
  nodes: HiSimNode[]
  edges: Edge[]
  selectedNodeId: string | null
  validationMessages: string[]
  scenarioName: string
  scenarioDescription: string
  showAutoConnections: boolean
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
  setScenarioMeta: (name: string, description: string) => void
  autoConnectNode: (nodeId: string) => void
  autoConnectAll: () => void
  deleteNode: (nodeId: string) => void
  toggleShowAutoConnections: () => void
  reset: () => void
}

export const useEditorStore = create<EditorState & EditorActions>()((set, get) => ({
  componentDb: null,
  enumDb: null,
  nodes: [],
  edges: [],
  selectedNodeId: null,
  validationMessages: [],
  scenarioName: 'Untitled scenario',
  scenarioDescription: '',
  showAutoConnections: true,

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

  setScenarioMeta: (name, description) => set({ scenarioName: name, scenarioDescription: description }),

  autoConnectNode: (nodeId) => {
    const { nodes, edges } = get()
    const target = nodes.find((n) => n.id === nodeId)
    if (!target) return
    const { newEdges, unresolvedPorts } = autoConnectNodeFn(target, nodes, edges)
    set((s) => ({
      edges: [...s.edges, ...newEdges],
      nodes: s.nodes.map((n) =>
        n.id === nodeId ? { ...n, data: { ...n.data, unresolvedPorts } } : n,
      ),
    }))
  },

  autoConnectAll: () => {
    const { nodes } = get()
    // Accumulate edges so each successive node sees the edges just created for prior nodes
    let accEdges = [...get().edges]
    const patches = new Map<string, string[]>()

    for (const node of nodes) {
      const { newEdges, unresolvedPorts } = autoConnectNodeFn(node, nodes, accEdges)
      accEdges = [...accEdges, ...newEdges]
      patches.set(node.id, unresolvedPorts)
    }

    set((s) => ({
      edges: accEdges,
      nodes: s.nodes.map((n) => {
        const unresolved = patches.get(n.id)
        return unresolved !== undefined
          ? { ...n, data: { ...n.data, unresolvedPorts: unresolved } }
          : n
      }),
    }))
  },

  deleteNode: (nodeId) =>
    set((s) => ({
      nodes: s.nodes.filter((n) => n.id !== nodeId),
      edges: s.edges.filter((e) => e.source !== nodeId && e.target !== nodeId),
      selectedNodeId: s.selectedNodeId === nodeId ? null : s.selectedNodeId,
    })),

  toggleShowAutoConnections: () =>
    set((s) => ({ showAutoConnections: !s.showAutoConnections })),

  reset: () =>
    set({
      nodes: [],
      edges: [],
      selectedNodeId: null,
      validationMessages: [],
      scenarioName: 'Untitled scenario',
      scenarioDescription: '',
    }),
}))
