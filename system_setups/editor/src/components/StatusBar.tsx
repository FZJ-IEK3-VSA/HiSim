import { useEditorStore } from '../store'

export default function StatusBar() {
  const messages = useEditorStore((s) => s.validationMessages)

  return (
    <footer className="px-3 py-1 border-t border-gray-200 bg-white shrink-0 text-xs text-gray-400 flex items-center gap-2">
      {messages.length === 0 ? (
        <span>Ready</span>
      ) : (
        <>
          <span className="text-red-500">⚠ {messages.length} issue{messages.length !== 1 ? 's' : ''}</span>
          <span className="truncate">{messages[0]}</span>
        </>
      )}
    </footer>
  )
}
