import { useCallback, useState } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  useReactFlow,
  type IsValidConnection,
  type Node,
} from '@xyflow/react'
import { useEditorStore, type HiSimNode } from '../store'
import { portUnit } from '../io/ports'
import type { DynamicInputPort, DynamicOutputPort } from '../types'
import { nodeTypes } from '../nodes'
import { HiSimEdge } from '../edges/HiSimEdge'
import ContextMenu from './ContextMenu'

const edgeTypes = { default: HiSimEdge }

// Counter for generating unique sequential instance names
let nodeCounter = 0

function CanvasInner() {
  const { screenToFlowPosition } = useReactFlow()

  const componentDb = useEditorStore((s) => s.componentDb)
  const catalogDb = useEditorStore((s) => s.catalogDb)
  const nodes = useEditorStore((s) => s.nodes)
  const edges = useEditorStore((s) => s.edges)
  const showAutoConnections = useEditorStore((s) => s.showAutoConnections)
  const onNodesChange = useEditorStore((s) => s.onNodesChange)
  const onEdgesChange = useEditorStore((s) => s.onEdgesChange)
  const addNode = useEditorStore((s) => s.addNode)
  const connect = useEditorStore((s) => s.connect)
  const setSelectedNodeId = useEditorStore((s) => s.setSelectedNodeId)
  const pushHistory = useEditorStore((s) => s.pushHistory)

  const [contextMenu, setContextMenu] = useState<{
    x: number
    y: number
    nodeId: string
  } | null>(null)

  const closeContextMenu = useCallback(() => setContextMenu(null), [])

  // ── Drag-and-drop from palette ─────────────────────────────────
  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
  }, [])

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      const classname = e.dataTransfer.getData('application/hisim-component')
      if (!classname || !componentDb) return

      const entry = componentDb.components.find(
        (c) => c.component_full_classname === classname,
      )
      if (!entry) return

      nodeCounter += 1
      const position = screenToFlowPosition({ x: e.clientX, y: e.clientY })

      const configOverrides = catalogDb?.config_overrides?.[entry.config_full_classname] ?? {}
      const newNode: HiSimNode = {
        id: `node-${nodeCounter}-${Date.now()}`,
        type: 'componentCard',
        position,
        data: {
          entry,
          instanceName: `${entry.display_name}_${nodeCounter}`,
          config: { ...entry.default_config, ...configOverrides },
          collapsed: true,
          connectAutomatically: true,
        },
      }
      addNode(newNode)
    },
    [componentDb, catalogDb, screenToFlowPosition, addNode],
  )

  // ── Connection validation ──────────────────────────────────────
  const isValidConnection: IsValidConnection = useCallback(
    (connection) => {
      if (connection.source === connection.target) return false

      const allNodes = useEditorStore.getState().nodes
      const src = allNodes.find((n) => n.id === connection.source)
      const tgt = allNodes.find((n) => n.id === connection.target)
      if (!src || !tgt) return false

      const outName = connection.sourceHandle?.replace('output-', '') ?? ''
      const inName = connection.targetHandle?.replace('input-', '') ?? ''

      // Check static output port first, then dynamic output port. Static ports may take their
      // type from config (see io/ports.ts), so resolve against the node's own config —
      // otherwise a CSV loader set to kW could not be wired to a kW input. Dynamic ports
      // already carry the scenario's own types.
      const staticOutPort = src.data.entry.output_ports.find((p) => p.field_name === outName)
      const dynOutput = !staticOutPort
        ? (src.data.dynamicOutputs as DynamicOutputPort[] | undefined)?.find(
            (p) => p.field_name === outName,
          )
        : undefined
      if (!staticOutPort && !dynOutput) return false

      // Check static input port first, then dynamic input port
      const staticInPort = tgt.data.entry.input_ports.find((p) => p.field_name === inName)
      const dynInput = !staticInPort
        ? (tgt.data.dynamicInputs as DynamicInputPort[] | undefined)?.find(
            (p) => p.field_name === inName,
          )
        : undefined
      if (!staticInPort && !dynInput) return false

      const outUnit = staticOutPort ? portUnit(staticOutPort, src.data.config) : dynOutput!.unit
      const inUnit = staticInPort ? portUnit(staticInPort, tgt.data.config) : dynInput!.unit

      // Block only on a unit mismatch — that is the dimensional error (W into a kW input).
      // A load-type difference is a labelling inconsistency that HiSim ignores and the shipped
      // setups are full of (see io/validate.ts), so refusing those edges would stop the user
      // drawing connections that exist in the setups they just opened. Validation reports them
      // as infos instead. "-" is Units.ANY, i.e. unitless, so it matches anything.
      if (outUnit === '-' || inUnit === '-') return true
      return outUnit === inUnit
    },
    [],
  )

  // ── Right-click context menu ───────────────────────────────────
  const onNodeContextMenu = useCallback(
    (e: React.MouseEvent, node: Node) => {
      e.preventDefault()
      setSelectedNodeId(node.id)
      setContextMenu({ x: e.clientX, y: e.clientY, nodeId: node.id })
    },
    [setSelectedNodeId],
  )

  return (
    <div
      className="w-full h-full"
      onDrop={onDrop}
      onDragOver={onDragOver}
      onContextMenu={(e) => e.preventDefault()}
    >
      <ReactFlow
        nodes={nodes}
        edges={showAutoConnections ? edges : edges.filter((e) => !e.data?.autoConnected)}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={connect}
        isValidConnection={isValidConnection}
        onNodeDragStart={() => { pushHistory() }}
        onNodeClick={(_e, node) => { setSelectedNodeId(node.id); closeContextMenu() }}
        onNodeContextMenu={onNodeContextMenu}
        onPaneClick={() => { setSelectedNodeId(null); closeContextMenu() }}
        deleteKeyCode={['Delete', 'Backspace']}
        fitView
        proOptions={{ hideAttribution: false }}
      >
        <Background gap={16} color="#e5e7eb" />
        <Controls />
        <MiniMap nodeStrokeWidth={2} zoomable pannable />
      </ReactFlow>

      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          nodeId={contextMenu.nodeId}
          onClose={closeContextMenu}
        />
      )}
    </div>
  )
}

export default function Canvas() {
  return (
    <div className="flex-1 min-h-0">
      <ReactFlowProvider>
        <CanvasInner />
      </ReactFlowProvider>
    </div>
  )
}
