const Separator = () => <div className="w-px h-4 bg-gray-200 mx-1" />

const Button = ({ children }: { children: React.ReactNode }) => (
  <button className="px-2 py-1 rounded text-gray-700 hover:bg-gray-100 active:bg-gray-200 transition-colors">
    {children}
  </button>
)

export default function Toolbar() {
  return (
    <header className="flex items-center gap-0.5 px-2 py-1 border-b border-gray-200 bg-white text-xs shrink-0">
      <Button>New</Button>
      <Button>Open JSON</Button>
      <Button>Save JSON</Button>
      <Separator />
      <Button>Validate</Button>
      <Button>Auto-connect all</Button>
      <Separator />
      <Button>Simulation settings</Button>
    </header>
  )
}
