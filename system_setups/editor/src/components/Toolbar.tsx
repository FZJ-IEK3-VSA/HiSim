import { useEditorStore } from '../store'
import { importScenario } from '../io/import'
import { exportScenario, triggerDownload } from '../io/export'

const Separator = () => <div className="w-px h-4 bg-gray-200 mx-1" />

const Button = ({
  children,
  onClick,
  disabled,
  title,
}: {
  children: React.ReactNode
  onClick?: () => void
  disabled?: boolean
  title?: string
}) => (
  <button
    className="px-2 py-1 rounded text-gray-700 hover:bg-gray-100 active:bg-gray-200 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
    onClick={onClick}
    disabled={disabled}
    title={title}
  >
    {children}
  </button>
)

export default function Toolbar() {
  const reset = useEditorStore((s) => s.reset)
  const setNodes = useEditorStore((s) => s.setNodes)
  const setEdges = useEditorStore((s) => s.setEdges)
  const setScenarioMeta = useEditorStore((s) => s.setScenarioMeta)
  const setSelectedNodeId = useEditorStore((s) => s.setSelectedNodeId)
  const setValidationMessages = useEditorStore((s) => s.setValidationMessages)
  const autoConnectAll = useEditorStore((s) => s.autoConnectAll)
  const runValidation = useEditorStore((s) => s.runValidation)
  const showAutoConnections = useEditorStore((s) => s.showAutoConnections)
  const toggleShowAutoConnections = useEditorStore((s) => s.toggleShowAutoConnections)

  const handleOpenJson = () => {
    const componentDb = useEditorStore.getState().componentDb
    if (!componentDb) return

    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.json'
    input.onchange = () => {
      const file = input.files?.[0]
      if (!file) return
      const reader = new FileReader()
      reader.onload = (e) => {
        const text = e.target?.result as string
        const result = importScenario(text, componentDb)
        setNodes(result.nodes)
        setEdges(result.edges)
        setScenarioMeta(result.scenarioName, result.scenarioDescription)
        setSelectedNodeId(null)
        setValidationMessages(
          result.warnings.length > 0
            ? [`Import: ${result.warnings.length} warning(s) — ${result.warnings[0]}`]
            : [],
        )
      }
      reader.readAsText(file)
    }
    input.click()
  }

  const handleSaveJson = () => {
    const { nodes, edges, scenarioName, scenarioDescription } = useEditorStore.getState()
    const json = exportScenario(nodes, edges, scenarioName, scenarioDescription)
    const filename = scenarioName.toLowerCase().replace(/[^a-z0-9]+/g, '_') + '.scenario.json'
    triggerDownload(json, filename)
  }

  return (
    <header className="flex items-center gap-0.5 px-2 py-1 border-b border-gray-200 bg-white text-xs shrink-0">
      <Button onClick={reset}>New</Button>
      <Button onClick={handleOpenJson}>Open JSON</Button>
      <Button onClick={handleSaveJson}>Save JSON</Button>
      <Separator />
      <Button onClick={runValidation}>Validate</Button>
      <Button onClick={autoConnectAll}>Auto-connect all</Button>
      <button
        onClick={toggleShowAutoConnections}
        title={showAutoConnections ? 'Hide auto-connected edges (dashed)' : 'Show auto-connected edges (dashed)'}
        className={`px-2 py-1 rounded text-xs transition-colors ${
          showAutoConnections
            ? 'bg-blue-50 text-blue-700 hover:bg-blue-100'
            : 'text-gray-400 hover:bg-gray-100'
        }`}
      >
        {showAutoConnections ? 'Auto-edges ●' : 'Auto-edges ○'}
      </button>
      <Separator />
      <Button disabled title="Phase 10">Simulation settings</Button>
    </header>
  )
}
