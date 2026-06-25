import { useState } from 'react'
import { useEditorStore } from '../store'

export default function StatusBar() {
  const importMessages = useEditorStore((s) => s.validationMessages)
  const errors = useEditorStore((s) => s.validationErrors)
  const warnings = useEditorStore((s) => s.validationWarnings)
  const [expanded, setExpanded] = useState(false)

  const hasValidation = errors.length > 0 || warnings.length > 0
  const hasImport = importMessages.length > 0
  const hasAnything = hasValidation || hasImport

  const summary = hasValidation
    ? [
        errors.length > 0 ? `${errors.length} error${errors.length !== 1 ? 's' : ''}` : '',
        warnings.length > 0 ? `${warnings.length} warning${warnings.length !== 1 ? 's' : ''}` : '',
      ]
        .filter(Boolean)
        .join(', ')
    : hasImport
    ? importMessages[0]
    : 'Ready'

  return (
    <div className="border-t border-gray-200 bg-white shrink-0">
      {/* Expanded list */}
      {expanded && hasAnything && (
        <div className="max-h-40 overflow-y-auto border-b border-gray-100 px-3 py-2 space-y-0.5">
          {errors.map((msg, i) => (
            <div key={`e${i}`} className="flex gap-1.5 text-xs text-red-600">
              <span className="shrink-0">✗</span>
              <span>{msg}</span>
            </div>
          ))}
          {warnings.map((msg, i) => (
            <div key={`w${i}`} className="flex gap-1.5 text-xs text-amber-600">
              <span className="shrink-0">⚠</span>
              <span>{msg}</span>
            </div>
          ))}
          {!hasValidation && importMessages.map((msg, i) => (
            <div key={`m${i}`} className="flex gap-1.5 text-xs text-gray-500">
              <span className="shrink-0">ℹ</span>
              <span>{msg}</span>
            </div>
          ))}
        </div>
      )}

      {/* Summary bar */}
      <footer
        className={`px-3 py-1 text-xs flex items-center gap-2 ${
          hasAnything ? 'cursor-pointer hover:bg-gray-50' : ''
        }`}
        onClick={() => hasAnything && setExpanded((v) => !v)}
        title={hasAnything ? (expanded ? 'Click to collapse' : 'Click to expand') : undefined}
      >
        {!hasAnything ? (
          <span className="text-gray-400">Ready</span>
        ) : (
          <>
            <span
              className={
                errors.length > 0
                  ? 'text-red-500 font-medium'
                  : warnings.length > 0
                  ? 'text-amber-500 font-medium'
                  : 'text-gray-500'
              }
            >
              {errors.length > 0 ? '✗' : '⚠'} {summary}
            </span>
            <span className="text-gray-300">·</span>
            <span className="text-gray-400">{expanded ? '▲ collapse' : '▼ expand'}</span>
          </>
        )}
      </footer>
    </div>
  )
}
