import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type OnNodesChange,
  type OnEdgesChange,
} from '@xyflow/react'
import { useEditorStore } from '../store'

export default function Canvas() {
  const storeNodes = useEditorStore((s) => s.nodes)
  const storeEdges = useEditorStore((s) => s.edges)
  const setStoreNodes = useEditorStore((s) => s.setNodes)
  const setStoreEdges = useEditorStore((s) => s.setEdges)
  const setSelectedNodeId = useEditorStore((s) => s.setSelectedNodeId)

  const [nodes, , onNodesChange] = useNodesState(storeNodes)
  const [edges, , onEdgesChange] = useEdgesState(storeEdges)

  const handleNodesChange: OnNodesChange = (changes) => {
    onNodesChange(changes)
    setStoreNodes(nodes)
  }

  const handleEdgesChange: OnEdgesChange = (changes) => {
    onEdgesChange(changes)
    setStoreEdges(edges)
  }

  return (
    <div className="flex-1">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onNodeClick={(_event, node) => setSelectedNodeId(node.id)}
        onPaneClick={() => setSelectedNodeId(null)}
        fitView
        deleteKeyCode="Delete"
      >
        <Background gap={16} color="#e5e7eb" />
        <Controls />
        <MiniMap nodeStrokeWidth={2} zoomable pannable />
      </ReactFlow>
    </div>
  )
}
