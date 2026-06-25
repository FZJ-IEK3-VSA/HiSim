import { memo, useCallback } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { ComponentNodeData } from '../types'
import { getLoadTypeColor } from '../data/loadTypeColors'
import { useEditorStore } from '../store'

// Card layout constants — handle top positions must match these exactly
const HEADER_H = 37    // header (36px content) + 1px bottom border
const PORT_H = 28      // height of each port row in pixels

const HANDLE_STYLE = {
  width: 10,
  height: 10,
  border: '2px solid white',
} as const

function CategoryBadge({ label }: { label: string }) {
  return (
    <span className="shrink-0 text-[10px] font-medium bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded leading-none">
      {label}
    </span>
  )
}

// NodeProps must use the base Record type for compatibility with React Flow's NodeTypes registry.
// We cast data to ComponentNodeData safely — only ComponentCard nodes carry this shape.
export const ComponentCard = memo(function ComponentCard({ id, data: rawData, selected }: NodeProps) {
  const data = rawData as ComponentNodeData
  const updateNodeData = useEditorStore((s) => s.updateNodeData)
  const { entry, instanceName, config, collapsed } = data

  const toggleCollapsed = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation()
      updateNodeData(id, { collapsed: !collapsed })
    },
    [id, collapsed, updateNodeData],
  )

  const portRows = Math.max(entry.input_ports.length, entry.output_ports.length)

  return (
    // Outer div is the node's bounding box; handles are positioned relative to it.
    // overflow: visible so handles render outside the white card border.
    <div style={{ width: 260 }} className="relative">
      {/* ── Input handles ─────────────────────────────────────── */}
      {entry.input_ports.map((port, i) => (
        <Handle
          key={`in-${port.field_name}`}
          type="target"
          position={Position.Left}
          id={`input-${port.field_name}`}
          style={{
            ...HANDLE_STYLE,
            top: HEADER_H + i * PORT_H + PORT_H / 2,
            background: getLoadTypeColor(port.load_type),
          }}
        />
      ))}

      {/* ── Output handles ────────────────────────────────────── */}
      {entry.output_ports.map((port, i) => (
        <Handle
          key={`out-${port.field_name}`}
          type="source"
          position={Position.Right}
          id={`output-${port.field_name}`}
          style={{
            ...HANDLE_STYLE,
            top: HEADER_H + i * PORT_H + PORT_H / 2,
            background: getLoadTypeColor(port.load_type),
          }}
        />
      ))}

      {/* ── Visual card ───────────────────────────────────────── */}
      <div
        className={`bg-white rounded-lg overflow-hidden ${
          selected
            ? 'ring-2 ring-blue-400 shadow-md'
            : 'border border-gray-200 shadow-sm'
        }`}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between gap-2 px-3 h-9 border-b border-gray-100 cursor-pointer hover:bg-gray-50 active:bg-gray-100"
          onClick={toggleCollapsed}
          title="Click to expand / collapse"
        >
          <span className="font-semibold text-xs truncate">{instanceName}</span>
          <CategoryBadge label={entry.category} />
        </div>

        {/* Port rows */}
        {portRows > 0 && (
          <div className="border-b border-gray-100">
            {Array.from({ length: portRows }).map((_, i) => {
              const inp = entry.input_ports[i]
              const out = entry.output_ports[i]
              return (
                <div key={i} className="flex items-center" style={{ height: PORT_H }}>
                  {/* Input label */}
                  <div className="flex-1 flex items-center pl-4 pr-1 min-w-0">
                    {inp && (
                      <span
                        className="text-[11px] text-gray-600 truncate"
                        style={{ color: getLoadTypeColor(inp.load_type) + 'cc' }}
                        title={`${inp.field_name} — ${inp.load_type} [${inp.unit}]${inp.mandatory ? ' *required' : ''}`}
                      >
                        {inp.field_name}
                        {inp.mandatory && <span className="text-red-400 ml-0.5">*</span>}
                      </span>
                    )}
                  </div>

                  {/* Centre divider */}
                  <div className="w-px h-3 bg-gray-100 shrink-0" />

                  {/* Output label */}
                  <div className="flex-1 flex items-center justify-end pl-1 pr-4 min-w-0">
                    {out && (
                      <span
                        className="text-[11px] truncate"
                        style={{ color: getLoadTypeColor(out.load_type) + 'cc' }}
                        title={`${out.field_name} — ${out.load_type} [${out.unit}]`}
                      >
                        {out.field_name}
                      </span>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}

        {/* Config summary — shown when NOT collapsed */}
        {!collapsed && entry.config_fields.length > 0 && (
          <div className="px-3 py-2 space-y-0.5">
            {entry.config_fields
              .filter((f) => f.name !== 'name')
              .slice(0, 6)
              .map((field) => {
                const val = config[field.name]
                const display = val === null || val === undefined ? '—' : String(val)
                return (
                  <div key={field.name} className="flex gap-1 text-[11px] leading-5 min-w-0">
                    <span className="text-gray-400 shrink-0">{field.name}:</span>
                    <span className="text-gray-700 truncate">{display}</span>
                  </div>
                )
              })}
          </div>
        )}
      </div>
    </div>
  )
})
