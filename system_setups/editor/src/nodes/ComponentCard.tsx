import { memo, useCallback } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { ComponentNodeData, DynamicInputPort } from '../types'
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
export const ComponentCard = memo(function ComponentCard({ id, data: rawData, selected }: NodeProps) {
  const data = rawData as ComponentNodeData
  const updateNodeData = useEditorStore((s) => s.updateNodeData)
  const { entry, instanceName, config, collapsed, unresolvedPorts = [] } = data
  const dynamicInputs: DynamicInputPort[] = (data.dynamicInputs as DynamicInputPort[] | undefined) ?? []

  // ── Real-time validation ──────────────────────────────────────────────────
  // Return unconnected mandatory port names as a \0-separated string for stable
  // equality comparison (avoids re-renders when the Set contents haven't changed).
  const unconnectedMandatoryStr = useEditorStore((s) => {
    const connected = new Set(
      s.edges.filter((e) => e.target === id).map((e) => e.targetHandle),
    )
    return entry.input_ports
      .filter((p) => p.mandatory && !connected.has(`input-${p.field_name}`))
      .map((p) => p.field_name)
      .join('\0')
  })
  const unconnectedMandatory = unconnectedMandatoryStr
    ? new Set(unconnectedMandatoryStr.split('\0'))
    : new Set<string>()

  const isDuplicateName = useEditorStore((s) => {
    const name = String(config.name ?? instanceName)
    return s.nodes.filter((n) => String(n.data.config.name ?? n.data.instanceName) === name).length > 1
  })

  const hasRealTimeError = unconnectedMandatory.size > 0 || isDuplicateName

  const toggleCollapsed = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation()
      updateNodeData(id, { collapsed: !collapsed })
    },
    [id, collapsed, updateNodeData],
  )

  const staticPortRows = Math.max(entry.input_ports.length, entry.output_ports.length)

  return (
    <div style={{ width: 260 }} className="relative">
      {/* ── Static input handles ──────────────────────────────── */}
      {entry.input_ports.map((port, i) => (
        <Handle
          key={`in-${port.field_name}`}
          type="target"
          position={Position.Left}
          id={`input-${port.field_name}`}
          style={{
            ...HANDLE_STYLE,
            top: HEADER_H + i * PORT_H + PORT_H / 2,
            background: unconnectedMandatory.has(port.field_name)
              ? '#ef4444'
              : getLoadTypeColor(port.load_type),
          }}
        />
      ))}

      {/* ── Static output handles ─────────────────────────────── */}
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

      {/* ── Dynamic input handles (below static rows) ─────────── */}
      {dynamicInputs.map((inp, j) => (
        <Handle
          key={`dyn-${inp.field_name}`}
          type="target"
          position={Position.Left}
          id={`input-${inp.field_name}`}
          style={{
            ...HANDLE_STYLE,
            top: HEADER_H + staticPortRows * PORT_H + j * PORT_H + PORT_H / 2,
            background: getLoadTypeColor(inp.load_type),
          }}
        />
      ))}

      {/* ── Visual card ───────────────────────────────────────── */}
      <div
        className={`bg-white rounded-lg overflow-hidden ${
          selected
            ? 'ring-2 ring-blue-400 shadow-md'
            : hasRealTimeError
            ? 'ring-2 ring-red-400 shadow-sm'
            : 'border border-gray-200 shadow-sm'
        }`}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between gap-2 px-3 h-9 border-b border-gray-100 cursor-pointer hover:bg-gray-50 active:bg-gray-100"
          onClick={toggleCollapsed}
          title="Click to expand / collapse"
        >
          <span className={`font-semibold text-xs truncate ${hasRealTimeError ? 'text-red-600' : ''}`}>
            {instanceName}
          </span>
          {isDuplicateName && (
            <span className="shrink-0 text-[10px] text-red-500 font-medium" title="Duplicate component name">
              dup
            </span>
          )}
          <CategoryBadge label={entry.category} />
        </div>

        {/* Static port rows */}
        {staticPortRows > 0 && (
          <div className={dynamicInputs.length > 0 ? '' : 'border-b border-gray-100'}>
            {Array.from({ length: staticPortRows }).map((_, i) => {
              const inp = entry.input_ports[i]
              const out = entry.output_ports[i]
              const missingMandatory = inp && unconnectedMandatory.has(inp.field_name)
              return (
                <div key={i} className="flex items-center" style={{ height: PORT_H }}>
                  {/* Input label */}
                  <div className="flex-1 flex items-center pl-4 pr-1 min-w-0 gap-1">
                    {inp && (
                      <>
                        <span
                          className="text-[11px] truncate"
                          style={{
                            color: missingMandatory
                              ? '#ef4444'
                              : getLoadTypeColor(inp.load_type) + 'cc',
                          }}
                          title={`${inp.field_name} — ${inp.load_type} [${inp.unit}]${
                            inp.mandatory ? ' *required' : ''
                          }${missingMandatory ? ' — NOT CONNECTED' : ''}`}
                        >
                          {inp.field_name}
                          {inp.mandatory && (
                            <span
                              className={`ml-0.5 ${missingMandatory ? 'text-red-500' : 'text-red-400'}`}
                            >
                              *
                            </span>
                          )}
                        </span>
                        {unresolvedPorts.includes(inp.field_name) && (
                          <span
                            className="shrink-0 text-[10px] text-amber-500"
                            title="Auto-connect: no unique source found"
                          >
                            ⚠
                          </span>
                        )}
                      </>
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

        {/* Dynamic input rows */}
        {dynamicInputs.length > 0 && (
          <div className="border-b border-gray-100">
            {staticPortRows > 0 && (
              <div className="border-t border-dashed border-gray-200" />
            )}
            {dynamicInputs.map((inp) => (
              <div
                key={inp.field_name}
                className="flex items-center"
                style={{ height: PORT_H }}
              >
                <div className="flex-1 flex items-center pl-4 pr-2 min-w-0">
                  <span
                    className="text-[11px] truncate italic"
                    style={{ color: getLoadTypeColor(inp.load_type) + 'cc' }}
                    title={`${inp.field_name} — ${inp.load_type} [${inp.unit}]`}
                  >
                    {inp.source_object_name}: {inp.source_component_output}
                  </span>
                </div>
              </div>
            ))}
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
