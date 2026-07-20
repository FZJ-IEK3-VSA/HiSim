import { useState } from 'react'
import { useEditorStore } from '../store'

export default function StatusBar() {
  const importMessages = useEditorStore((s) => s.validationMessages)
  const errors = useEditorStore((s) => s.validationErrors)
  const warnings = useEditorStore((s) => s.validationWarnings)
  const infos = useEditorStore((s) => s.validationInfos)
  const [expanded, setExpanded] = useState(false)
  const [showInfos, setShowInfos] = useState(true)

  const hasValidation = errors.length > 0 || warnings.length > 0 || infos.length > 0
  const hasImport = importMessages.length > 0
  const hasAnything = hasValidation || hasImport

  const summary = hasValidation
    ? [
        errors.length > 0 ? `${errors.length} error${errors.length !== 1 ? 's' : ''}` : '',
        warnings.length > 0 ? `${warnings.length} warning${warnings.length !== 1 ? 's' : ''}` : '',
        infos.length > 0 ? `${infos.length} info` : '',
      ]
        .filter(Boolean)
        .join(', ')
    : hasImport
    ? importMessages.length === 1
      ? `Import: ${importMessages[0]}`
      : `Import: ${importMessages.length} warnings`
    : 'Ready'

  // Highest-severity level present drives the summary icon/color.
  const level = errors.length > 0 ? 'error' : warnings.length > 0 ? 'warning' : 'info'
  const summaryIcon = level === 'error' ? '✗' : level === 'warning' ? '⚠' : 'ℹ'
  const summaryColor =
    level === 'error'
      ? 'text-red-500 font-medium'
      : level === 'warning'
      ? 'text-amber-500 font-medium'
      : 'text-sky-500 font-medium'

  return (
    <div className="border-t border-gray-200 bg-white shrink-0">
      {/* Expanded list */}
      {expanded && hasAnything && (
        <div className="max-h-40 overflow-y-auto border-b border-gray-100 px-3 py-2 space-y-0.5 select-text">
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
            <div key={`m${i}`} className="flex gap-1.5 text-xs text-amber-600">
              <span className="shrink-0">⚠</span>
              <span>{msg}</span>
            </div>
          ))}
          {infos.length > 0 && (
            <>
              <button
                type="button"
                onClick={() => setShowInfos((v) => !v)}
                className="text-[11px] text-gray-400 hover:text-gray-600 pt-0.5"
              >
                {showInfos ? '▾' : '▸'} {infos.length} info{' '}
                {showInfos ? '(hide)' : '(show)'}
              </button>
              {showInfos &&
                infos.map((msg, i) => (
                  <div key={`i${i}`} className="flex gap-1.5 text-xs text-sky-600">
                    <span className="shrink-0">ℹ</span>
                    <span>{msg}</span>
                  </div>
                ))}
            </>
          )}
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
            <span className={summaryColor}>
              {summaryIcon} {summary}
            </span>
            <span className="text-gray-300">·</span>
            <span className="text-gray-400">{expanded ? '▲ collapse' : '▼ expand'}</span>
          </>
        )}
      </footer>
    </div>
  )
}
