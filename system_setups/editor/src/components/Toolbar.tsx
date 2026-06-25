import { useEditorStore } from '../store'

const Separator = () => <div className="w-px h-4 bg-gray-200 mx-1" />

const Button = ({
  children,
  onClick,
  disabled,
}: {
  children: React.ReactNode
  onClick?: () => void
  disabled?: boolean
}) => (
  <button
    className="px-2 py-1 rounded text-gray-700 hover:bg-gray-100 active:bg-gray-200 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
    onClick={onClick}
    disabled={disabled}
  >
    {children}
  </button>
)

export default function Toolbar() {
  const reset = useEditorStore((s) => s.reset)

  return (
    <header className="flex items-center gap-0.5 px-2 py-1 border-b border-gray-200 bg-white text-xs shrink-0">
      <Button onClick={reset}>New</Button>
      <Button disabled title="Phase 6">Open JSON</Button>
      <Button disabled title="Phase 6">Save JSON</Button>
      <Separator />
      <Button disabled title="Phase 8">Validate</Button>
      <Button disabled title="Phase 7">Auto-connect all</Button>
      <Separator />
      <Button disabled title="Phase 10">Simulation settings</Button>
    </header>
  )
}
