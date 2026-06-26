import { useEffect, useState } from 'react'
import Toolbar from './components/Toolbar'
import Palette from './components/Palette'
import Canvas from './components/Canvas'
import Inspector from './components/Inspector'
import StatusBar from './components/StatusBar'
import { useEditorStore } from './store'
import type { CatalogDb, ComponentDb, EnumDb } from './types'

export default function App() {
  const loadDatabases = useEditorStore((s) => s.loadDatabases)
  const loadCatalogDb = useEditorStore((s) => s.loadCatalogDb)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([
      fetch('./data/component_db.json').then((r) => r.json() as Promise<ComponentDb>),
      fetch('./data/enum_db.json').then((r) => r.json() as Promise<EnumDb>),
    ])
      .then(([compDb, eDb]) => loadDatabases(compDb, eDb))
      .catch((e: unknown) => {
        const msg = e instanceof Error ? e.message : String(e)
        setError(
          `Failed to load component databases: ${msg}. ` +
          'Run: python tools/generate_component_db.py',
        )
      })

    fetch('./data/catalog_db.json')
      .then((r) => r.json() as Promise<CatalogDb>)
      .then((db) => loadCatalogDb(db))
      .catch(() => { /* catalog not yet generated — editor still works without it */ })
  }, [loadDatabases, loadCatalogDb])

  if (error) {
    return (
      <div className="h-screen flex items-center justify-center bg-gray-50">
        <div className="max-w-md p-6 bg-white rounded-lg border border-red-200 shadow text-sm">
          <p className="text-red-600 font-semibold mb-2">Database error</p>
          <p className="text-gray-600">{error}</p>
        </div>
      </div>
    )
  }

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
