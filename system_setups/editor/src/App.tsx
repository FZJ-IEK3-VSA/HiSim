import Toolbar from './components/Toolbar'
import Palette from './components/Palette'
import Canvas from './components/Canvas'
import Inspector from './components/Inspector'
import StatusBar from './components/StatusBar'

export default function App() {
  return (
    <div className="h-screen flex overflow-hidden bg-gray-50 text-sm text-gray-800 select-none">
      {/* Left sidebar — component palette */}
      <Palette />

      {/* Centre column — toolbar · canvas · status bar */}
      <div className="flex-1 flex flex-col min-w-0">
        <Toolbar />
        <Canvas />
        <StatusBar />
      </div>

      {/* Right sidebar — inspector */}
      <Inspector />
    </div>
  )
}
