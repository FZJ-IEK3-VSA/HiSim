import { useEditorStore } from '../store'
import type { ConfigField, EnumDb } from '../types'

// ── Enum value lookup ──────────────────────────────────────────────────────────
function getEnumValues(enumClass: string | null, enumDb: EnumDb): string[] | null {
  if (!enumClass) return null
  const map: Record<string, string[]> = {
    Locations: enumDb.locations,
    BuildingCodes: enumDb.building_codes,
    LoadTypes: enumDb.load_types,
    Units: enumDb.units,
    ComponentType: enumDb.component_types,
    InandOutputType: enumDb.in_and_output_types,
    PostProcessingOptions: enumDb.post_processing_options.map((o) => o.value),
  }
  return map[enumClass] ?? null
}

// ── Individual field renderer ──────────────────────────────────────────────────
function FieldInput({
  field,
  value,
  enumDb,
  onChange,
}: {
  field: ConfigField
  value: unknown
  enumDb: EnumDb
  onChange: (name: string, val: unknown) => void
}) {
  const strVal = value === null || value === undefined ? '' : String(value)
  const enumValues = getEnumValues(field.enum_class, enumDb)
  const isBool = field.type.includes('bool')
  const isNum = field.type.includes('int') || field.type.includes('float')

  const base =
    'w-full px-2 py-1 text-xs border border-gray-200 rounded focus:outline-none focus:ring-1 focus:ring-blue-400 bg-white'

  if (enumValues) {
    return (
      <select
        className={base}
        value={strVal}
        onChange={(e) => onChange(field.name, e.target.value)}
      >
        {field.is_optional && <option value="">— none —</option>}
        {enumValues.map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </select>
    )
  }

  if (isBool) {
    return (
      <button
        type="button"
        role="switch"
        aria-checked={strVal === 'true' || strVal === 'True'}
        className={`relative inline-flex h-4 w-8 shrink-0 rounded-full border transition-colors ${
          strVal === 'true' || strVal === 'True'
            ? 'bg-blue-500 border-blue-500'
            : 'bg-gray-200 border-gray-300'
        }`}
        onClick={() =>
          onChange(
            field.name,
            !(strVal === 'true' || strVal === 'True'),
          )
        }
      >
        <span
          className={`absolute top-0.5 h-3 w-3 rounded-full bg-white shadow transition-transform ${
            strVal === 'true' || strVal === 'True' ? 'translate-x-4' : 'translate-x-0.5'
          }`}
        />
      </button>
    )
  }

  if (isNum) {
    return (
      <input
        type="number"
        className={base}
        value={strVal}
        onChange={(e) => onChange(field.name, e.target.value === '' ? null : Number(e.target.value))}
      />
    )
  }

  return (
    <input
      type="text"
      className={base}
      value={strVal}
      placeholder={field.is_optional ? '(optional)' : ''}
      onChange={(e) => onChange(field.name, e.target.value)}
    />
  )
}

// ── Scenario metadata (shown when nothing is selected) ────────────────────────
function ScenarioMeta() {
  const scenarioName = useEditorStore((s) => s.scenarioName)
  const scenarioDescription = useEditorStore((s) => s.scenarioDescription)
  const setScenarioMeta = useEditorStore((s) => s.setScenarioMeta)

  return (
    <div className="flex-1 overflow-y-auto p-3 space-y-3">
      <p className="text-[11px] text-gray-400">
        Set scenario metadata below, then drag components onto the canvas.
      </p>
      <div>
        <label className="block text-[11px] font-medium text-gray-600 mb-0.5">
          Scenario name
        </label>
        <input
          type="text"
          className="w-full px-2 py-1 text-xs border border-gray-200 rounded focus:outline-none focus:ring-1 focus:ring-blue-400"
          value={scenarioName}
          onChange={(e) => setScenarioMeta(e.target.value, scenarioDescription)}
        />
      </div>
      <div>
        <label className="block text-[11px] font-medium text-gray-600 mb-0.5">
          Description
        </label>
        <textarea
          rows={3}
          className="w-full px-2 py-1 text-xs border border-gray-200 rounded focus:outline-none focus:ring-1 focus:ring-blue-400 resize-none"
          value={scenarioDescription}
          onChange={(e) => setScenarioMeta(scenarioName, e.target.value)}
        />
      </div>
    </div>
  )
}

// ── Inspector panel ────────────────────────────────────────────────────────────
export default function Inspector() {
  const enumDb = useEditorStore((s) => s.enumDb)
  const selectedNodeId = useEditorStore((s) => s.selectedNodeId)
  const nodes = useEditorStore((s) => s.nodes)
  const updateNodeData = useEditorStore((s) => s.updateNodeData)

  const node = selectedNodeId ? nodes.find((n) => n.id === selectedNodeId) : null

  const handleChange = (name: string, val: unknown) => {
    if (!node) return
    const newConfig = { ...node.data.config, [name]: val }
    // Keep instanceName in sync with the config 'name' field
    if (name === 'name') {
      updateNodeData(node.id, { config: newConfig, instanceName: String(val) })
    } else {
      updateNodeData(node.id, { config: newConfig })
    }
  }

  const handleInstanceName = (val: string) => {
    if (!node) return
    updateNodeData(node.id, {
      instanceName: val,
      config: { ...node.data.config, name: val },
    })
  }

  return (
    <aside className="w-64 flex flex-col border-l border-gray-200 bg-white shrink-0 overflow-hidden">
      {/* Header */}
      <div className="px-3 py-2 border-b border-gray-200 shrink-0">
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
          Inspector
        </span>
      </div>

      {!node ? (
        <ScenarioMeta />
      ) : !enumDb ? (
        <div className="flex-1 p-3 text-xs text-gray-400 italic">Loading…</div>
      ) : (
        <div className="flex-1 overflow-y-auto p-3 space-y-3">
          {/* Class info */}
          <div className="text-[11px] text-gray-400">
            <span className="font-medium text-gray-500">{node.data.entry.category}</span>
            {' · '}
            {node.data.entry.display_name}
          </div>

          {/* Instance name */}
          <div>
            <label className="block text-[11px] font-medium text-gray-600 mb-0.5">
              Instance name
            </label>
            <input
              type="text"
              className="w-full px-2 py-1 text-xs border border-gray-200 rounded focus:outline-none focus:ring-1 focus:ring-blue-400"
              value={node.data.instanceName}
              onChange={(e) => handleInstanceName(e.target.value)}
            />
          </div>

          {/* Config fields */}
          {node.data.entry.config_fields
            .filter((f) => f.name !== 'name')
            .map((field) => (
              <div key={field.name}>
                <label className="flex items-center gap-1 text-[11px] font-medium text-gray-600 mb-0.5">
                  {field.name}
                  {!field.is_optional && <span className="text-red-400">*</span>}
                  <span className="ml-auto font-normal text-gray-400">{field.type}</span>
                </label>
                <FieldInput
                  field={field}
                  value={node.data.config[field.name]}
                  enumDb={enumDb}
                  onChange={handleChange}
                />
              </div>
            ))}

          {/* Connect automatically toggle */}
          <div className="flex items-center justify-between pt-1 border-t border-gray-100">
            <span className="text-[11px] text-gray-600">Connect automatically</span>
            <button
              type="button"
              role="switch"
              aria-checked={node.data.connectAutomatically}
              className={`relative inline-flex h-4 w-8 shrink-0 rounded-full border transition-colors ${
                node.data.connectAutomatically
                  ? 'bg-blue-500 border-blue-500'
                  : 'bg-gray-200 border-gray-300'
              }`}
              onClick={() =>
                updateNodeData(node.id, {
                  connectAutomatically: !node.data.connectAutomatically,
                })
              }
            >
              <span
                className={`absolute top-0.5 h-3 w-3 rounded-full bg-white shadow transition-transform ${
                  node.data.connectAutomatically ? 'translate-x-4' : 'translate-x-0.5'
                }`}
              />
            </button>
          </div>

          {/* Port summary (read-only) */}
          {(node.data.entry.input_ports.length > 0 ||
            node.data.entry.output_ports.length > 0) && (
            <div className="pt-1 border-t border-gray-100">
              <p className="text-[11px] font-medium text-gray-500 mb-1">Ports</p>
              {node.data.entry.input_ports.map((p) => (
                <div key={p.field_name} className="flex gap-1 text-[11px] text-gray-500 leading-5">
                  <span className="text-gray-400">→</span>
                  <span className="truncate">{p.field_name}</span>
                  <span className="ml-auto text-gray-400 shrink-0">{p.load_type}</span>
                </div>
              ))}
              {node.data.entry.output_ports.map((p) => (
                <div key={p.field_name} className="flex gap-1 text-[11px] text-gray-500 leading-5">
                  <span className="text-gray-400">←</span>
                  <span className="truncate">{p.field_name}</span>
                  <span className="ml-auto text-gray-400 shrink-0">{p.load_type}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </aside>
  )
}
