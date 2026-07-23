import { useEffect, useRef } from 'react'
import { useEditorStore } from '../store'

interface ContextMenuProps {
  x: number
  y: number
  nodeId: string
  onClose: () => void
}

export default function ContextMenu({ x, y, nodeId, onClose }: ContextMenuProps) {
  const autoConnectNode = useEditorStore((s) => s.autoConnectNode)
  const deleteNode = useEditorStore((s) => s.deleteNode)
  const menuRef = useRef<HTMLDivElement>(null)

  // Close on click outside or Escape
  useEffect(() => {
    const onPointerDown = (e: PointerEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [onClose])

  const Item = ({
    label,
    onClick,
    danger,
  }: {
    label: string
    onClick: () => void
    danger?: boolean
  }) => (
    <button
      className={`w-full text-left px-3 py-1.5 text-xs hover:bg-gray-50 ${
        danger ? 'text-red-600 hover:bg-red-50' : 'text-gray-700'
      }`}
      onClick={() => { onClick(); onClose() }}
    >
      {label}
    </button>
  )

  return (
    <div
      ref={menuRef}
      className="fixed z-50 min-w-[160px] bg-white border border-gray-200 rounded-lg shadow-lg py-1 text-xs"
      style={{ left: x, top: y }}
    >
      <Item
        label="Auto-connect this"
        onClick={() => autoConnectNode(nodeId)}
      />
      <div className="my-1 border-t border-gray-100" />
      <Item
        label="Delete"
        danger
        onClick={() => deleteNode(nodeId)}
      />
    </div>
  )
}
