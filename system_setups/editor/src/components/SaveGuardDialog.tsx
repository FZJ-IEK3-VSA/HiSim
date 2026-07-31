// Pre-export confirmation — shown by "Save JSON" when the validation result does not
// describe the scenario about to be written.
//
// The status bar always shows *a* validation result, but it is a snapshot: edit anything
// afterwards and it silently describes a graph that no longer exists. Exporting on the
// strength of a stale (or never-run) validation is how a broken scenario reaches
// hisim_main.py looking checked. So the export path states plainly which of the three
// situations applies — never validated, out of date, or validated and complaining — and
// makes the user choose.

import { useEffect } from 'react'

export type SaveGuardReason = 'never' | 'stale' | 'findings'

interface Props {
  reason: SaveGuardReason
  errors: string[]
  warnings: string[]
  onValidate: () => void
  onExportAnyway: () => void
  onCancel: () => void
}

export default function SaveGuardDialog({
  reason,
  errors,
  warnings,
  onValidate,
  onExportAnyway,
  onCancel,
}: Props) {
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel() }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onCancel])

  const headline =
    reason === 'never'
      ? 'This scenario has not been validated'
      : reason === 'stale'
      ? 'Validation is out of date'
      : 'Validation found problems'

  const explanation =
    reason === 'never'
      ? 'Nothing has been checked yet — unconnected mandatory inputs, duplicate component names and unit mismatches would all go unnoticed.'
      : reason === 'stale'
      ? 'The scenario changed after the last validation, so the results in the status bar describe an earlier version of it.'
      : 'The last validation ran against the current scenario and reported the following.'

  const findings = reason === 'findings'
  const total = errors.length + warnings.length

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20" onClick={onCancel}>
      <div
        className="w-[520px] max-h-[80vh] flex flex-col bg-white rounded-lg border border-gray-200 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-4 py-3 border-b border-gray-200 shrink-0">
          <p className={`text-sm font-semibold ${errors.length > 0 ? 'text-red-600' : 'text-amber-600'}`}>
            {errors.length > 0 ? '✗' : '⚠'} {headline}
          </p>
          <p className="mt-1 text-xs text-gray-600">{explanation}</p>
        </div>

        {findings && total > 0 && (
          <div className="flex-1 overflow-y-auto px-4 py-2 space-y-0.5 select-text">
            {errors.slice(0, 12).map((msg, i) => (
              <div key={`e${i}`} className="flex gap-1.5 text-xs text-red-600">
                <span className="shrink-0">✗</span>
                <span>{msg}</span>
              </div>
            ))}
            {warnings.slice(0, 12).map((msg, i) => (
              <div key={`w${i}`} className="flex gap-1.5 text-xs text-amber-600">
                <span className="shrink-0">⚠</span>
                <span>{msg}</span>
              </div>
            ))}
            {total > 24 && (
              <p className="text-[11px] text-gray-400">…see the status bar for the full list.</p>
            )}
          </div>
        )}

        <div className="flex items-center gap-2 px-4 py-2.5 border-t border-gray-200 shrink-0">
          <p className="text-[11px] text-gray-400">
            {findings
              ? 'HiSim will still load the file; these are the editor’s own checks.'
              : 'Validating takes a moment and changes nothing.'}
          </p>
          <div className="ml-auto flex items-center gap-2">
            <button
              className="px-3 py-1 text-xs rounded text-gray-600 hover:bg-gray-100"
              onClick={onCancel}
            >
              Cancel
            </button>
            <button
              className="px-3 py-1 text-xs rounded bg-gray-100 text-gray-700 hover:bg-gray-200"
              onClick={onExportAnyway}
            >
              Export anyway
            </button>
            {!findings && (
              <button
                className="px-3 py-1 text-xs rounded bg-blue-600 text-white hover:bg-blue-700"
                onClick={onValidate}
              >
                Validate first
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
