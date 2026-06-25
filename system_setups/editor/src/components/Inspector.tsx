export default function Inspector() {
  return (
    <aside className="w-64 flex flex-col border-l border-gray-200 bg-white shrink-0 overflow-hidden">
      <div className="px-3 py-2 border-b border-gray-200 shrink-0">
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
          Inspector
        </span>
      </div>

      {/* Config form — placeholder */}
      <div className="flex-1 p-3 text-xs text-gray-400 italic flex items-center justify-center">
        Select a component to inspect.
      </div>
    </aside>
  )
}
