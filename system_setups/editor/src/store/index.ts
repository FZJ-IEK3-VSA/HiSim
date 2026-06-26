import { create } from 'zustand'
import { applyNodeChanges, applyEdgeChanges, addEdge as rfAddEdge } from '@xyflow/react'
import type { Node, Edge, NodeChange, EdgeChange, Connection } from '@xyflow/react'
import type { CatalogDb, ComponentDb, EnumDb, ComponentNodeData } from '../types'
import { getLoadTypeColor } from '../data/loadTypeColors'
import { autoConnectNode as autoConnectNodeFn } from '../io/autoConnect'
import { validateScenario } from '../io/validate'

export type HiSimNode = Node<ComponentNodeData>

interface HistoryEntry {
  nodes: HiSimNode[]
  edges: Edge[]
}

interface EditorState {
  componentDb: ComponentDb | null
  enumDb: EnumDb | null
  catalogDb: CatalogDb | null
  nodes: HiSimNode[]
  edges: Edge[]
  selectedNodeId: string | null
  validationMessages: string[]
  validationErrors: string[]
  validationWarnings: string[]
  scenarioName: string
  scenarioDescription: string
  showAutoConnections: boolean
  past: HistoryEntry[]
  future: HistoryEntry[]
}

interface EditorActions {
  loadDatabases: (db: ComponentDb, edb: EnumDb) => void
  loadCatalogDb: (db: CatalogDb) => void
  setNodes: (nodes: HiSimNode[]) => void
  setEdges: (edges: Edge[]) => void
  onNodesChange: (changes: NodeChange<HiSimNode>[]) => void
  onEdgesChange: (changes: EdgeChange[]) => void
  addNode: (node: HiSimNode) => void
  connect: (connection: Connection) => void
  setSelectedNodeId: (id: string | null) => void
  updateNodeData: (nodeId: string, patch: Partial<ComponentNodeData>) => void
  setValidationMessages: (messages: string[]) => void
  runValidation: () => void
  setScenarioMeta: (name: string, description: string) => void
  autoConnectNode: (nodeId: string) => void
  autoConnectAll: () => void
  deleteNode: (nodeId: string) => void
  toggleShowAutoConnections: () => void
  reset: () => void
  pushHistory: () => void
  undo: () => void
  redo: () => void
  resetHistory: () => void
}

export const useEditorStore = create<EditorState & EditorActions>()((set, get) => ({
  componentDb: null,
  enumDb: null,
  catalogDb: null,
  nodes: [],
  edges: [],
  selectedNodeId: null,
  validationMessages: [],
  validationErrors: [],
  validationWarnings: [],
  scenarioName: 'Untitled scenario',
  scenarioDescription: '',
  showAutoConnections: true,
  past: [],
  future: [],

  loadDatabases: (db, edb) => set({ componentDb: db, enumDb: edb }),
  loadCatalogDb: (db) => set({ catalogDb: db }),

  setNodes: (nodes) => set({ nodes }),
  setEdges: (edges) => set({ edges }),

  onNodesChange: (changes) => {
    if (changes.some((c) => c.type === 'remove')) get().pushHistory()
    set((s) => ({ nodes: applyNodeChanges(changes, s.nodes) }))
  },

  onEdgesChange: (changes) => {
    if (changes.some((c) => c.type === 'remove')) get().pushHistory()
    set((s) => ({ edges: applyEdgeChanges(changes, s.edges) }))
  },

  addNode: (node) => {
    get().pushHistory()
    set((s) => ({ nodes: [...s.nodes, node] }))
  },

  connect: (connection) => {
    get().pushHistory()
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

  runValidation: () => {
    const { nodes, edges } = get()
    const { errors, warnings } = validateScenario(nodes, edges)
    set({ validationErrors: errors, validationWarnings: warnings })
  },

  setScenarioMeta: (name, description) => set({ scenarioName: name, scenarioDescription: description }),

  autoConnectNode: (nodeId) => {
    get().pushHistory()
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
    get().pushHistory()
    const { nodes } = get()
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

  deleteNode: (nodeId) => {
    get().pushHistory()
    set((s) => ({
      nodes: s.nodes.filter((n) => n.id !== nodeId),
      edges: s.edges.filter((e) => e.source !== nodeId && e.target !== nodeId),
      selectedNodeId: s.selectedNodeId === nodeId ? null : s.selectedNodeId,
    }))
  },

  toggleShowAutoConnections: () =>
    set((s) => ({ showAutoConnections: !s.showAutoConnections })),

  reset: () =>
    set({
      nodes: [],
      edges: [],
      selectedNodeId: null,
      validationMessages: [],
      validationErrors: [],
      validationWarnings: [],
      scenarioName: 'Untitled scenario',
      scenarioDescription: '',
      past: [],
      future: [],
    }),

  pushHistory: () => {
    const { nodes, edges, past } = get()
    set({ past: [...past.slice(-49), { nodes, edges }], future: [] })
  },

  undo: () => {
    const { past, future, nodes, edges } = get()
    if (past.length === 0) return
    const prev = past[past.length - 1]
    set({
      nodes: prev.nodes.map((n) => ({ ...n, selected: false })),
      edges: prev.edges,
      past: past.slice(0, -1),
      future: [{ nodes, edges }, ...future.slice(0, 49)],
      selectedNodeId: null,
    })
  },

  redo: () => {
    const { past, future, nodes, edges } = get()
    if (future.length === 0) return
    const next = future[0]
    set({
      nodes: next.nodes.map((n) => ({ ...n, selected: false })),
      edges: next.edges,
      past: [...past.slice(-49), { nodes, edges }],
      future: future.slice(1),
      selectedNodeId: null,
    })
  },

  resetHistory: () => set({ past: [], future: [] }),
}))
