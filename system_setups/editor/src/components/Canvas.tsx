import { useCallback } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  useReactFlow,
  type IsValidConnection,
} from '@xyflow/react'
import { useEditorStore, type HiSimNode } from '../store'
import { nodeTypes } from '../nodes'

// Counter for generating unique sequential instance names
let nodeCounter = 0

function CanvasInner() {
  const { screenToFlowPosition } = useReactFlow()

  const componentDb = useEditorStore((s) => s.componentDb)
  const nodes = useEditorStore((s) => s.nodes)
  const edges = useEditorStore((s) => s.edges)
  const onNodesChange = useEditorStore((s) => s.onNodesChange)
  const onEdgesChange = useEditorStore((s) => s.onEdgesChange)
  const addNode = useEditorStore((s) => s.addNode)
  const connect = useEditorStore((s) => s.connect)
  const setSelectedNodeId = useEditorStore((s) => s.setSelectedNodeId)

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

      const newNode: HiSimNode = {
        id: `node-${nodeCounter}-${Date.now()}`,
        type: 'componentCard',
        position,
        data: {
          entry,
          instanceName: `${entry.display_name}_${nodeCounter}`,
          config: { ...entry.default_config },
          collapsed: true,
          connectAutomatically: true,
        },
      }
      addNode(newNode)
    },
    [componentDb, screenToFlowPosition, addNode],
  )

  // ── Connection validation (Phase 4) ───────────────────────────
  const isValidConnection: IsValidConnection = useCallback(
    (connection) => {
      // Allow self-loops to be handled by React Flow (they're implicitly rejected)
      if (connection.source === connection.target) return false

      const allNodes = useEditorStore.getState().nodes
      const src = allNodes.find((n) => n.id === connection.source)
      const tgt = allNodes.find((n) => n.id === connection.target)
      if (!src || !tgt) return false

      const outName = connection.sourceHandle?.replace('output-', '') ?? ''
      const inName = connection.targetHandle?.replace('input-', '') ?? ''

      const outPort = src.data.entry.output_ports.find((p) => p.field_name === outName)
      const inPort = tgt.data.entry.input_ports.find((p) => p.field_name === inName)

      if (!outPort || !inPort) return false

      // Any port accepts any source
      if (inPort.load_type === 'Any' || outPort.load_type === 'Any') return true

      return outPort.load_type === inPort.load_type && outPort.unit === inPort.unit
    },
    [],
  )

  return (
    <div
      className="w-full h-full"
      onDrop={onDrop}
      onDragOver={onDragOver}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={connect}
        isValidConnection={isValidConnection}
        onNodeClick={(_e, node) => setSelectedNodeId(node.id)}
        onPaneClick={() => setSelectedNodeId(null)}
        deleteKeyCode={['Delete', 'Backspace']}
        fitView
        proOptions={{ hideAttribution: false }}
      >
        <Background gap={16} color="#e5e7eb" />
        <Controls />
        <MiniMap nodeStrokeWidth={2} zoomable pannable />
      </ReactFlow>
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
