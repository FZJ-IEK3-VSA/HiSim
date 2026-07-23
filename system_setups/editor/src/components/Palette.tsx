import { useState, useMemo } from 'react'
import { useEditorStore } from '../store'
import type { ComponentEntry } from '../types'

function PaletteItem({ entry }: { entry: ComponentEntry }) {
  const onDragStart = (e: React.DragEvent) => {
    e.dataTransfer.setData('application/hisim-component', entry.component_full_classname)
    e.dataTransfer.effectAllowed = 'copy'
  }

  return (
    <div
      draggable
      onDragStart={onDragStart}
      className="flex items-center gap-2 px-2 py-1 rounded text-xs text-gray-700 hover:bg-blue-50 hover:text-blue-700 cursor-grab active:cursor-grabbing select-none"
      title={entry.component_full_classname}
    >
      <span className="truncate">{entry.display_name}</span>
      {entry.is_dynamic && (
        <span className="shrink-0 text-[10px] bg-amber-100 text-amber-600 px-1 rounded">dyn</span>
      )}
    </div>
  )
}

function CategoryGroup({
  category,
  entries,
}: {
  category: string
  entries: ComponentEntry[]
}) {
  const [open, setOpen] = useState(true)

  return (
    <div>
      <button
        className="w-full flex items-center gap-1 px-2 py-1 text-[11px] font-semibold text-gray-500 uppercase tracking-wide hover:bg-gray-50"
        onClick={() => setOpen((o) => !o)}
      >
        <span className={`transition-transform ${open ? 'rotate-90' : ''}`}>▶</span>
        {category}
        <span className="ml-auto font-normal normal-case text-gray-400">{entries.length}</span>
      </button>

      {open && (
        <div className="pb-1">
          {entries.map((e) => (
            <PaletteItem key={e.component_full_classname} entry={e} />
          ))}
        </div>
      )}
    </div>
  )
}

export default function Palette() {
  const componentDb = useEditorStore((s) => s.componentDb)
  const [query, setQuery] = useState('')

  const grouped = useMemo(() => {
    if (!componentDb) return []
    const q = query.toLowerCase()
    const filtered = componentDb.components.filter(
      (c) =>
        !q ||
        c.display_name.toLowerCase().includes(q) ||
        c.category.toLowerCase().includes(q),
    )
    const map = new Map<string, ComponentEntry[]>()
    for (const c of filtered) {
      const list = map.get(c.category) ?? []
      list.push(c)
      map.set(c.category, list)
    }
    return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b))
  }, [componentDb, query])

  return (
    <aside className="w-52 flex flex-col border-r border-gray-200 bg-white shrink-0 overflow-hidden">
      {/* Search */}
      <div className="p-2 border-b border-gray-200 shrink-0">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search components…"
          className="w-full px-2 py-1 text-xs border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-400"
        />
      </div>

      {/* Category list */}
      <div className="flex-1 overflow-y-auto py-1">
        {!componentDb ? (
          <p className="px-2 py-3 text-xs text-gray-400 italic">Loading…</p>
        ) : grouped.length === 0 ? (
          <p className="px-2 py-3 text-xs text-gray-400 italic">No results.</p>
        ) : (
          grouped.map(([cat, entries]) => (
            <CategoryGroup key={cat} category={cat} entries={entries} />
          ))
        )}
      </div>
    </aside>
  )
}
