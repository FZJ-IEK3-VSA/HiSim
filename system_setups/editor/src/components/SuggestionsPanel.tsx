// "Suggest components" — the dialog behind the toolbar button.
//
// Answers "what is this scenario still missing?" in three sections, strongest evidence first:
// components that would satisfy an unconnected input, connections between components already
// on the canvas, and components that the shipped scenarios say normally come along. See
// io/suggest.ts for how each is derived.

import { useEffect, useMemo, useState } from 'react'
import { useEditorStore } from '../store'
import { createComponentNode, placeUpstreamOf } from '../io/createNode'
import { suggestComponents, type ComponentSuggestion, type Suggestions } from '../io/suggest'
import type { ComponentEntry } from '../types'

interface Props {
  onClose: () => void
}

/** Class name without its module path — what HiSim's `default_connections` actually name. */
const shortName = (entry: ComponentEntry) =>
  entry.component_full_classname.split('.').pop() ?? entry.display_name

export default function SuggestionsPanel({ onClose }: Props) {
  const nodes = useEditorStore((s) => s.nodes)
  const edges = useEditorStore((s) => s.edges)
  const componentDb = useEditorStore((s) => s.componentDb)
  const usageDb = useEditorStore((s) => s.usageDb)
  const catalogDb = useEditorStore((s) => s.catalogDb)
  const addNodeAndAutoConnect = useEditorStore((s) => s.addNodeAndAutoConnect)
  const connectPorts = useEditorStore((s) => s.connectPorts)
  const runValidation = useEditorStore((s) => s.runValidation)

  // Names of what was added or wired during this session of the dialog, so the user can see
  // what they already acted on without the list reshuffling under the cursor.
  const [done, setDone] = useState<string[]>([])

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const suggestions: Suggestions | null = useMemo(
    () => (componentDb ? suggestComponents(nodes, edges, componentDb, usageDb) : null),
    [nodes, edges, componentDb, usageDb],
  )

  const addComponent = (suggestion: ComponentSuggestion) => {
    const downstream = suggestion.fixes.map((f) => f.targetNodeId)
    const current = useEditorStore.getState().nodes
    const node = createComponentNode(
      suggestion.entry,
      placeUpstreamOf(current, downstream),
      catalogDb,
      current,
    )
    addNodeAndAutoConnect(node)
    runValidation()
    setDone((d) => [...d, suggestion.entry.component_full_classname])
  }

  if (!suggestions) return null

  const { missing, companions, wiring, unexplained } = suggestions
  const openWiring = wiring.filter((w) => !done.includes(wireKey(w)))
  const nothingToSay =
    missing.length === 0 &&
    companions.length === 0 &&
    openWiring.length === 0 &&
    unexplained.length === 0

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/20"
      onClick={onClose}
    >
      <div
        className="w-[640px] max-h-[80vh] flex flex-col bg-white rounded-lg border border-gray-200 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-gray-200 shrink-0">
          <span className="text-sm font-semibold text-gray-800">Suggest components</span>
          <span className="text-[11px] text-gray-400">
            derived from declared default connections, port types, and the shipped scenarios
          </span>
          <button
            className="ml-auto text-gray-400 hover:text-gray-700 px-1"
            onClick={onClose}
            title="Close (Esc)"
          >
            ✕
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4 select-text">
          {nothingToSay && (
            <p className="text-xs text-gray-500 py-4 text-center">
              Nothing to suggest — every input port that something is known to feed is
              already connected.
            </p>
          )}

          {/* ── Missing components ─────────────────────────────────────── */}
          {missing.length > 0 && (
            <Section
              title="Would fill an unconnected input"
              hint="✗ = mandatory (a validation error) · ℹ = optional. Ranked by how many inputs each one fills; a connection the component declares itself outranks a type-and-name match."
            >
              {missing.map((s) => (
                <SuggestionRow
                  key={s.entry.component_full_classname}
                  suggestion={s}
                  added={done.includes(s.entry.component_full_classname)}
                  onAdd={() => addComponent(s)}
                >
                  <ul className="mt-1 space-y-0.5">
                    {s.fixes.slice(0, 6).map((f, i) => (
                      <li key={i} className="text-[11px] text-gray-500 flex items-center gap-1">
                        <span className={f.mandatory ? 'text-red-500' : 'text-gray-300'}>
                          {f.mandatory ? '✗' : 'ℹ'}
                        </span>
                        <span className="font-mono">
                          {shortName(s.entry)}.{f.sourceOutput}
                        </span>
                        <span className="text-gray-300">→</span>
                        <span className="font-mono">
                          {f.targetNodeName}.{f.targetInput}
                        </span>
                        {!f.declared && <span className="text-gray-400 italic">(guess)</span>}
                      </li>
                    ))}
                    {s.fixes.length > 6 && (
                      <li className="text-[11px] text-gray-400">
                        …and {s.fixes.length - 6} more
                      </li>
                    )}
                  </ul>
                </SuggestionRow>
              ))}
            </Section>
          )}

          {/* ── Wiring between existing components ─────────────────────── */}
          {openWiring.length > 0 && (
            <Section
              title="Already on the canvas — not wired up"
              hint="These sources exist; only the connection is missing. Auto-connect all does the declared ones in one go."
            >
              <div className="space-y-1">
                {openWiring.slice(0, 12).map((w) => (
                  <div
                    key={wireKey(w)}
                    className="flex items-center gap-2 text-[11px] px-2 py-1 rounded border border-gray-100"
                  >
                    <span className={w.mandatory ? 'text-red-500' : 'text-gray-300'}>
                      {w.mandatory ? '✗' : 'ℹ'}
                    </span>
                    <span className="font-mono truncate">
                      {w.sourceNodeName}.{w.sourceOutput}
                    </span>
                    <span className="text-gray-300">→</span>
                    <span className="font-mono truncate">
                      {w.targetNodeName}.{w.targetInput}
                    </span>
                    {!w.declared && <span className="text-gray-400 italic">(guess)</span>}
                    <button
                      className="ml-auto shrink-0 px-2 py-0.5 rounded bg-blue-50 text-blue-700 hover:bg-blue-100"
                      onClick={() => {
                        connectPorts(
                          { nodeId: w.sourceNodeId, output: w.sourceOutput },
                          { nodeId: w.targetNodeId, input: w.targetInput },
                        )
                        runValidation()
                        setDone((d) => [...d, wireKey(w)])
                      }}
                    >
                      Connect
                    </button>
                  </div>
                ))}
                {openWiring.length > 12 && (
                  <p className="text-[11px] text-gray-400">
                    …and {openWiring.length - 12} more.
                  </p>
                )}
              </div>
            </Section>
          )}

          {/* ── Usage-based companions ─────────────────────────────────── */}
          {companions.length > 0 && (
            <Section
              title={
                nodes.length === 0
                  ? 'Common starting points'
                  : 'Commonly used together with what you have'
              }
              hint="Counted across the scenarios shipped in system_setups/. Nothing on the canvas requires these — they are what comparable scenarios contain."
            >
              {companions.map((s) => (
                <SuggestionRow
                  key={s.entry.component_full_classname}
                  suggestion={s}
                  added={done.includes(s.entry.component_full_classname)}
                  onAdd={() => addComponent(s)}
                >
                  <p className="mt-1 text-[11px] text-gray-500">
                    {s.companions.length > 0
                      ? s.companions
                          .map((c) => `${c.together} of the ${c.outOf} with ${c.componentName}`)
                          .join(' · ')
                      : `in ${Math.round((s.popularity ?? 0) * 100)}% of shipped scenarios`}
                  </p>
                </SuggestionRow>
              ))}
            </Section>
          )}

          {/* ── Unexplained mandatory inputs ───────────────────────────── */}
          {unexplained.length > 0 && (
            <Section
              title="No candidate found"
              hint="Mandatory inputs nothing in the registry plausibly feeds — wire these by hand."
            >
              <ul className="space-y-0.5">
                {unexplained.slice(0, 10).map((u, i) => (
                  <li key={i} className="text-[11px] text-gray-500 font-mono">
                    {u.nodeName}.{u.input}
                  </li>
                ))}
                {unexplained.length > 10 && (
                  <li className="text-[11px] text-gray-400">
                    …and {unexplained.length - 10} more.
                  </li>
                )}
              </ul>
            </Section>
          )}
        </div>

        <div className="flex items-center gap-2 px-4 py-2 border-t border-gray-200 shrink-0">
          <p className="text-[11px] text-gray-400">
            Added components arrive with their default configuration — check it in the
            Inspector.
          </p>
          <button
            className="ml-auto px-3 py-1 text-xs rounded bg-gray-100 text-gray-700 hover:bg-gray-200"
            onClick={onClose}
          >
            Close
          </button>
        </div>
      </div>
    </div>
  )
}

const wireKey = (w: {
  sourceNodeId: string
  sourceOutput: string
  targetNodeId: string
  targetInput: string
}) => `${w.sourceNodeId}.${w.sourceOutput}→${w.targetNodeId}.${w.targetInput}`

function Section({
  title,
  hint,
  children,
}: {
  title: string
  hint: string
  children: React.ReactNode
}) {
  return (
    <section>
      <h3 className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">
        {title}
      </h3>
      <p className="text-[11px] text-gray-400 mb-1.5">{hint}</p>
      <div className="space-y-1.5">{children}</div>
    </section>
  )
}

function SuggestionRow({
  suggestion,
  added,
  onAdd,
  children,
}: {
  suggestion: ComponentSuggestion
  added: boolean
  onAdd: () => void
  children: React.ReactNode
}) {
  const { entry } = suggestion
  return (
    <div className="border border-gray-200 rounded px-2.5 py-1.5">
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-gray-800 truncate" title={entry.component_full_classname}>
          {shortName(entry)}
        </span>
        <span className="text-[10px] text-gray-400 shrink-0">{entry.category}</span>
        {entry.is_dynamic && (
          <span className="shrink-0 text-[10px] bg-amber-100 text-amber-600 px-1 rounded">dyn</span>
        )}
        <button
          className={`ml-auto shrink-0 px-2 py-0.5 text-xs rounded transition-colors ${
            added
              ? 'bg-green-50 text-green-700'
              : 'bg-blue-50 text-blue-700 hover:bg-blue-100'
          }`}
          onClick={onAdd}
          title={added ? 'Added — click again to add another instance' : 'Add to canvas and auto-connect'}
        >
          {added ? '✓ Added' : '+ Add'}
        </button>
      </div>
      <p className="text-[11px] text-gray-500 truncate" title={entry.display_name}>
        {entry.display_name}
      </p>
      {children}
    </div>
  )
}
