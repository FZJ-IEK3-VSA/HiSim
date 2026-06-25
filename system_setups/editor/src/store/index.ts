import { create } from 'zustand'
import type { Node, Edge } from '@xyflow/react'

interface EditorState {
  nodes: Node[]
  edges: Edge[]
  selectedNodeId: string | null
  validationMessages: string[]
}

interface EditorActions {
  setNodes: (nodes: Node[]) => void
  setEdges: (edges: Edge[]) => void
  setSelectedNodeId: (id: string | null) => void
  setValidationMessages: (messages: string[]) => void
}

export const useEditorStore = create<EditorState & EditorActions>()((set) => ({
  nodes: [],
  edges: [],
  selectedNodeId: null,
  validationMessages: [],

  setNodes: (nodes) => set({ nodes }),
  setEdges: (edges) => set({ edges }),
  setSelectedNodeId: (id) => set({ selectedNodeId: id }),
  setValidationMessages: (messages) => set({ validationMessages: messages }),
}))
