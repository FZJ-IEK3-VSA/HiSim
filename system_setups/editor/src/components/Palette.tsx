export default function Palette() {
  return (
    <aside className="w-52 flex flex-col border-r border-gray-200 bg-white shrink-0 overflow-hidden">
      {/* Search */}
      <div className="p-2 border-b border-gray-200 shrink-0">
        <input
          type="search"
          placeholder="Search components…"
          className="w-full px-2 py-1 text-xs border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-400"
        />
      </div>

      {/* Category list — placeholder */}
      <div className="flex-1 overflow-y-auto p-1 text-xs text-gray-400 italic flex items-center justify-center">
        Component palette — Phase 3
      </div>
    </aside>
  )
}
