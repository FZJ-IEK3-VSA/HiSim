import { create } from 'zustand'
import { applyNodeChanges, applyEdgeChanges, addEdge as rfAddEdge } from '@xyflow/react'
import type { Node, Edge, NodeChange, EdgeChange, Connection } from '@xyflow/react'
import type {
  CatalogDb,
  ComponentDb,
  DynamicInputPort,
  DynamicOutputPort,
  EnumDb,
  ComponentNodeData,
  UsageDb,
} from '../types'
import { getLoadTypeColor } from '../data/loadTypeColors'
import { autoConnectNode as autoConnectNodeFn } from '../io/autoConnect'
import { syncDynamicPorts as syncDynamicPortsFn } from '../io/dynamicPorts'
import { portLoadType, portUnit } from '../io/ports'
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
  usageDb: UsageDb | null
  nodes: HiSimNode[]
  edges: Edge[]
  selectedNodeId: string | null
  validationMessages: string[]
  validationErrors: string[]
  validationWarnings: string[]
  validationInfos: string[]
  /**
   * Bumped by every change to the *content* of the graph — components, connections, config.
   * Not by moving a card around, which cannot invalidate a validation result.
   */
  graphRevision: number
  /** The `graphRevision` the current validation result describes; null = never validated. */
  validatedRevision: number | null
  scenarioName: string
  scenarioDescription: string
  showAutoConnections: boolean
  /** The "Suggest components" dialog — opened from the toolbar and from the status bar. */
  showSuggestions: boolean
  past: HistoryEntry[]
  future: HistoryEntry[]
}

/** Whether the displayed validation result still describes the graph in front of the user. */
export type ValidationFreshness = 'never' | 'stale' | 'current'

export function validationFreshness(state: EditorState): ValidationFreshness {
  if (state.validatedRevision === null) return 'never'
  return state.validatedRevision === state.graphRevision ? 'current' : 'stale'
}

interface EditorActions {
  loadDatabases: (db: ComponentDb, edb: EnumDb) => void
  loadCatalogDb: (db: CatalogDb) => void
  loadUsageDb: (db: UsageDb) => void
  setNodes: (nodes: HiSimNode[]) => void
  setEdges: (edges: Edge[]) => void
  /** Reposition cards only — leaves the validation result valid (see graphRevision). */
  setNodePositions: (nodes: HiSimNode[]) => void
  onNodesChange: (changes: NodeChange<HiSimNode>[]) => void
  onEdgesChange: (changes: EdgeChange[]) => void
  addNode: (node: HiSimNode) => void
  addNodeAndAutoConnect: (node: HiSimNode) => void
  connectPorts: (
    source: { nodeId: string; output: string },
    target: { nodeId: string; input: string },
  ) => void
  connect: (connection: Connection) => void
  setSelectedNodeId: (id: string | null) => void
  updateNodeData: (nodeId: string, patch: Partial<ComponentNodeData>) => void
  setValidationMessages: (messages: string[]) => void
  runValidation: () => void
  setScenarioMeta: (name: string, description: string) => void
  autoConnectNode: (nodeId: string) => void
  autoConnectAll: () => void
  deleteNode: (nodeId: string) => void
  /**
   * Recompute the dynamic input ports HiSim would create at run time (io/dynamicPorts.ts).
   * Cheap and idempotent — called after anything that could change the answer.
   */
  syncDynamicPorts: () => void
  addDynamicInput: (nodeId: string, port: DynamicInputPort, sourceNodeId: string) => void
  addDynamicOutput: (
    nodeId: string,
    port: DynamicOutputPort,
    target?: { nodeId: string; inputName: string },
  ) => void
  removeDynamicInput: (nodeId: string, fieldName: string) => void
  removeDynamicOutput: (nodeId: string, fieldName: string) => void
  toggleShowAutoConnections: () => void
  setShowSuggestions: (open: boolean) => void
  reset: () => void
  pushHistory: () => void
  undo: () => void
  redo: () => void
  resetHistory: () => void
}

/** React Flow node changes that touch only presentation, never the scenario's content. */
const COSMETIC_NODE_CHANGES = new Set<string>(['select', 'position', 'dimensions'])

/**
 * Build the edge for a connection, resolving its load type and unit from the source port.
 *
 * Static port types may come from config (see io/ports.ts); dynamic ports carry their own.
 */
function buildEdge(nodes: HiSimNode[], connection: Connection): Edge {
  const sourceNode = nodes.find((n) => n.id === connection.source)
  const portName = connection.sourceHandle?.replace('output-', '') ?? ''
  const staticOutPort = sourceNode?.data.entry.output_ports.find(
    (p) => p.field_name === portName,
  )
  const dynOutput = !staticOutPort
    ? (sourceNode?.data.dynamicOutputs as DynamicOutputPort[] | undefined)?.find(
        (p) => p.field_name === portName,
      )
    : undefined

  const config = sourceNode?.data.config ?? {}
  const loadType = staticOutPort
    ? portLoadType(staticOutPort, config)
    : dynOutput?.load_type ?? 'Any'
  const unit = staticOutPort ? portUnit(staticOutPort, config) : dynOutput?.unit ?? ''

  return {
    ...connection,
    id: `e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    style: { stroke: getLoadTypeColor(loadType), strokeWidth: 2 },
    data: { loadType, unit },
  }
}

export const useEditorStore = create<EditorState & EditorActions>()((set, get) => ({
  componentDb: null,
  enumDb: null,
  catalogDb: null,
  usageDb: null,
  nodes: [],
  edges: [],
  selectedNodeId: null,
  validationMessages: [],
  validationErrors: [],
  validationWarnings: [],
  validationInfos: [],
  graphRevision: 0,
  validatedRevision: null,
  scenarioName: 'Untitled scenario',
  scenarioDescription: '',
  showAutoConnections: true,
  showSuggestions: false,
  past: [],
  future: [],

  loadDatabases: (db, edb) => set({ componentDb: db, enumDb: edb }),
  loadCatalogDb: (db) => set({ catalogDb: db }),
  loadUsageDb: (db) => set({ usageDb: db }),

  setNodes: (nodes) => set((s) => ({ nodes, graphRevision: s.graphRevision + 1 })),
  setEdges: (edges) => set((s) => ({ edges, graphRevision: s.graphRevision + 1 })),
  setNodePositions: (nodes) => set({ nodes }),

  onNodesChange: (changes) => {
    const removed = changes.some((c) => c.type === 'remove')
    if (removed) get().pushHistory()
    // React Flow reports selection, measurement and drag alongside add/remove/replace. Only
    // the latter alter the scenario. Listed the other way round on purpose: a change type
    // this code has never heard of counts as content, so the worst a future React Flow can
    // do is make validation look out of date when it isn't.
    set((s) => ({
      nodes: applyNodeChanges(changes, s.nodes),
      graphRevision: changes.some((c) => !COSMETIC_NODE_CHANGES.has(c.type))
        ? s.graphRevision + 1
        : s.graphRevision,
    }))
    // Deleting a component removes the dynamic ports it gave its consumers.
    if (removed) get().syncDynamicPorts()
  },

  onEdgesChange: (changes) => {
    if (changes.some((c) => c.type === 'remove')) get().pushHistory()
    set((s) => ({
      edges: applyEdgeChanges(changes, s.edges),
      graphRevision: changes.some((c) => c.type !== 'select')
        ? s.graphRevision + 1
        : s.graphRevision,
    }))
  },

  addNode: (node) => {
    get().pushHistory()
    set((s) => ({ nodes: [...s.nodes, node], graphRevision: s.graphRevision + 1 }))
    // The new component may be the source a dynamic component grows a port for — or be one.
    get().syncDynamicPorts()
  },

  addNodeAndAutoConnect: (node) => {
    // One history entry for the whole operation, so a single Ctrl+Z undoes the add and its
    // wiring together rather than leaving a stranded component behind.
    get().pushHistory()
    set((s) => ({ nodes: [...s.nodes, node], graphRevision: s.graphRevision + 1 }))
    const { nodes, edges } = get()
    const { newEdges, unresolvedPorts } = autoConnectNodeFn(node, nodes, edges)

    // The new component also completes default connections that existing components were
    // missing a source for, so re-resolve those too — that is the whole point of adding it.
    let accEdges = [...edges, ...newEdges]
    const patches = new Map<string, string[]>([[node.id, unresolvedPorts]])
    for (const other of nodes) {
      if (other.id === node.id) continue
      if (!other.data.connectAutomatically) continue
      const result = autoConnectNodeFn(other, nodes, accEdges)
      accEdges = [...accEdges, ...result.newEdges]
      patches.set(other.id, result.unresolvedPorts)
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
    get().syncDynamicPorts()
  },

  connectPorts: (source, target) => {
    get().pushHistory()
    const { nodes, edges } = get()
    const connection: Connection = {
      source: source.nodeId,
      sourceHandle: `output-${source.output}`,
      target: target.nodeId,
      targetHandle: `input-${target.input}`,
    }
    set((s) => ({
      edges: rfAddEdge(buildEdge(nodes, connection), edges),
      graphRevision: s.graphRevision + 1,
    }))
  },

  connect: (connection) => {
    get().pushHistory()
    const { nodes, edges } = get()
    set((s) => ({
      edges: rfAddEdge(buildEdge(nodes, connection), edges),
      graphRevision: s.graphRevision + 1,
    }))
  },

  setSelectedNodeId: (id) => set({ selectedNodeId: id }),

  updateNodeData: (nodeId, patch) => {
    // `collapsed` and `unresolvedPorts` are presentation state — changing them cannot
    // invalidate a validation result, so they leave graphRevision alone.
    const cosmetic = new Set(['collapsed', 'unresolvedPorts'])
    const structural = Object.keys(patch).some((k) => !cosmetic.has(k))
    set((s) => ({
      nodes: s.nodes.map((n) =>
        n.id === nodeId ? { ...n, data: { ...n.data, ...patch } } : n
      ),
      graphRevision: structural ? s.graphRevision + 1 : s.graphRevision,
    }))
    // The derived dynamic ports depend on the auto-connect toggle, on the component's config
    // (conditional static inputs shift their numbering) and on instance names, which are
    // baked into the port names.
    if ('connectAutomatically' in patch || 'config' in patch || 'instanceName' in patch) {
      get().syncDynamicPorts()
    }
  },

  setValidationMessages: (messages) => set({ validationMessages: messages }),

  runValidation: () => {
    const { nodes, edges, graphRevision } = get()
    const { errors, warnings, infos } = validateScenario(nodes, edges)
    set({
      validationErrors: errors,
      validationWarnings: warnings,
      validationInfos: infos,
      validatedRevision: graphRevision,
    })
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
      graphRevision: s.graphRevision + 1,
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
      graphRevision: s.graphRevision + 1,
    }))
  },

  deleteNode: (nodeId) => {
    get().pushHistory()
    set((s) => ({
      nodes: s.nodes.filter((n) => n.id !== nodeId),
      edges: s.edges.filter((e) => e.source !== nodeId && e.target !== nodeId),
      selectedNodeId: s.selectedNodeId === nodeId ? null : s.selectedNodeId,
      graphRevision: s.graphRevision + 1,
    }))
    get().syncDynamicPorts()
  },

  syncDynamicPorts: () => {
    const { nodes, edges } = get()
    const result = syncDynamicPortsFn(nodes, edges)
    // No history entry: this is derived state, not an edit. Undoing the action that caused it
    // restores a graph these ports are recomputed from anyway.
    if (result.changed) set({ nodes: result.nodes, edges: result.edges })
  },

  addDynamicInput: (nodeId, port, sourceNodeId) => {
    get().pushHistory()
    const { nodes } = get()
    const target = nodes.find((n) => n.id === nodeId)
    if (!target) return
    const existing = (target.data.dynamicInputs as DynamicInputPort[] | undefined) ?? []
    // Explicit ports come first — they are created at build time, before the simulator adds
    // any derived ones, and that ordering is what fixes the port names.
    const explicit = existing.filter((p) => !p.auto)
    const derived = existing.filter((p) => p.auto)

    set((s) => ({
      nodes: s.nodes.map((n) =>
        n.id === nodeId
          ? { ...n, data: { ...n.data, dynamicInputs: [...explicit, port, ...derived] } }
          : n,
      ),
      graphRevision: s.graphRevision + 1,
    }))
    get().connectPorts(
      { nodeId: sourceNodeId, output: port.source_component_output },
      { nodeId, input: port.field_name },
    )
    // Renumbers the derived ports that now sit behind the new explicit one.
    get().syncDynamicPorts()
  },

  addDynamicOutput: (nodeId, port, target) => {
    get().pushHistory()
    set((s) => ({
      nodes: s.nodes.map((n) => {
        if (n.id !== nodeId) return n
        const existing = (n.data.dynamicOutputs as DynamicOutputPort[] | undefined) ?? []
        return { ...n, data: { ...n.data, dynamicOutputs: [...existing, port] } }
      }),
      graphRevision: s.graphRevision + 1,
    }))
    if (target) {
      get().connectPorts(
        { nodeId, output: port.field_name },
        { nodeId: target.nodeId, input: target.inputName },
      )
    }
  },

  removeDynamicInput: (nodeId, fieldName) => {
    get().pushHistory()
    set((s) => ({
      nodes: s.nodes.map((n) => {
        if (n.id !== nodeId) return n
        const kept = ((n.data.dynamicInputs as DynamicInputPort[] | undefined) ?? []).filter(
          (p) => p.field_name !== fieldName,
        )
        return { ...n, data: { ...n.data, dynamicInputs: kept.length > 0 ? kept : undefined } }
      }),
      edges: s.edges.filter(
        (e) => !(e.target === nodeId && e.targetHandle === `input-${fieldName}`),
      ),
      graphRevision: s.graphRevision + 1,
    }))
    get().syncDynamicPorts()
  },

  removeDynamicOutput: (nodeId, fieldName) => {
    get().pushHistory()
    set((s) => ({
      nodes: s.nodes.map((n) => {
        if (n.id !== nodeId) return n
        const kept = ((n.data.dynamicOutputs as DynamicOutputPort[] | undefined) ?? []).filter(
          (p) => p.field_name !== fieldName,
        )
        return { ...n, data: { ...n.data, dynamicOutputs: kept.length > 0 ? kept : undefined } }
      }),
      edges: s.edges.filter(
        (e) => !(e.source === nodeId && e.sourceHandle === `output-${fieldName}`),
      ),
      graphRevision: s.graphRevision + 1,
    }))
  },

  toggleShowAutoConnections: () =>
    set((s) => ({ showAutoConnections: !s.showAutoConnections })),

  setShowSuggestions: (open) => set({ showSuggestions: open }),

  reset: () =>
    set((s) => ({
      nodes: [],
      edges: [],
      selectedNodeId: null,
      validationMessages: [],
      validationErrors: [],
      validationWarnings: [],
      validationInfos: [],
      graphRevision: s.graphRevision + 1,
      validatedRevision: null,
      scenarioName: 'Untitled scenario',
      scenarioDescription: '',
      past: [],
      future: [],
    })),

  pushHistory: () => {
    const { nodes, edges, past } = get()
    set({ past: [...past.slice(-49), { nodes, edges }], future: [] })
  },

  undo: () => {
    const { past, future, nodes, edges, graphRevision } = get()
    if (past.length === 0) return
    const prev = past[past.length - 1]
    set({
      nodes: prev.nodes.map((n) => ({ ...n, selected: false })),
      edges: prev.edges,
      past: past.slice(0, -1),
      future: [{ nodes, edges }, ...future.slice(0, 49)],
      selectedNodeId: null,
      // A restored graph is a different graph as far as validation is concerned — the
      // revision only ever moves forward, so undo marks the result stale rather than
      // pretending an old validation still applies.
      graphRevision: graphRevision + 1,
    })
  },

  redo: () => {
    const { past, future, nodes, edges, graphRevision } = get()
    if (future.length === 0) return
    const next = future[0]
    set({
      nodes: next.nodes.map((n) => ({ ...n, selected: false })),
      edges: next.edges,
      past: [...past.slice(-49), { nodes, edges }],
      future: future.slice(1),
      selectedNodeId: null,
      graphRevision: graphRevision + 1,
    })
  },

  resetHistory: () => set({ past: [], future: [] }),
}))
