// Creation of a fresh component node — shared by the palette drag-drop (components/Canvas.tsx)
// and the "Suggest components" panel, so a component added either way is identical.

import type { HiSimNode } from '../store'
import type { CatalogDb, ComponentEntry } from '../types'

const CARD_WIDTH = 260
const CARD_H_GAP = 300
/** Rough card height used only for collision avoidance when placing a suggested node. */
const CARD_SLOT_H = 180

let nodeSeq = 0

/**
 * The instance name a new node should get: the config's own default name, made unique.
 *
 * Both `instanceName` (the card header) and `config.name` (what connections refer to in the
 * JSON) are set from this, because the Inspector keeps the two in sync and export reads
 * `config.name`. Uniquifying matters: dropping a second Building would otherwise land on the
 * canvas already carrying a duplicate-name validation error.
 *
 * Most components name their default config after themselves (`Building`, `PVSystem`), but a
 * handful just say `"default"` — Weather among them. That is a placeholder rather than a
 * name, so the class name is used instead, which is also what the shipped scenarios call
 * these components.
 */
export function uniqueInstanceName(entry: ComponentEntry, existing: HiSimNode[]): string {
  const fallback = entry.component_full_classname.split('.').pop() ?? 'Component'
  const defaultName = String(entry.default_config.name ?? '').trim()
  const isPlaceholder = defaultName === '' || defaultName.toLowerCase() === 'default'
  const base = isPlaceholder ? fallback : defaultName

  const taken = new Set(
    existing.map((n) => String(n.data.config.name ?? n.data.instanceName)),
  )
  if (!taken.has(base)) return base
  for (let i = 2; ; i++) {
    const candidate = `${base}_${i}`
    if (!taken.has(candidate)) return candidate
  }
}

/**
 * Whether `connect_automatically` may be switched on for this component at all.
 *
 * Not a preference — a requirement. `Simulator.connect_everything_automatically` raises
 * `KeyError("Automatic connection does not work for X because no default connections were
 * found")` for any component registered with the flag that declares none, so a Weather or an
 * occupancy profile (pure sources, no inputs to wire) must have it off or the run dies before
 * the first time step.
 */
export function supportsAutoConnect(entry: ComponentEntry): boolean {
  return (
    Object.keys(entry.default_connections ?? {}).length > 0 ||
    Object.keys(entry.dynamic_default_connections ?? {}).length > 0
  )
}

export function createComponentNode(
  entry: ComponentEntry,
  position: { x: number; y: number },
  catalogDb: CatalogDb | null,
  existing: HiSimNode[],
): HiSimNode {
  const overrides = catalogDb?.config_overrides?.[entry.config_full_classname] ?? {}
  const name = uniqueInstanceName(entry, existing)

  nodeSeq += 1
  return {
    id: `node-${nodeSeq}-${Date.now()}`,
    type: 'componentCard',
    position,
    data: {
      entry,
      instanceName: name,
      config: { ...entry.default_config, ...overrides, name },
      collapsed: true,
      connectAutomatically: supportsAutoConnect(entry),
    },
  }
}

/**
 * Where to drop a component the user did not place by hand.
 *
 * Upstream of whatever it feeds (one column to the left, data flows left-to-right as in
 * io/layout.ts), or left of the whole graph when it feeds nothing in particular. The slot is
 * nudged downwards until it is clear of existing cards, so a suggested component never lands
 * hidden underneath another one.
 */
export function placeUpstreamOf(
  nodes: HiSimNode[],
  downstreamIds: string[],
): { x: number; y: number } {
  if (nodes.length === 0) return { x: 60, y: 60 }

  const anchors = nodes.filter((n) => downstreamIds.includes(n.id))
  const reference = anchors.length > 0 ? anchors : nodes
  const x = Math.min(...reference.map((n) => n.position.x)) - (CARD_WIDTH + CARD_H_GAP)
  const y = reference.reduce((sum, n) => sum + n.position.y, 0) / reference.length

  return nudgeClear(nodes, { x, y })
}

function nudgeClear(
  nodes: HiSimNode[],
  start: { x: number; y: number },
): { x: number; y: number } {
  const pos = { ...start }
  // Bounded so a dense canvas cannot spin here; after 40 tries the overlap is acceptable.
  for (let i = 0; i < 40; i++) {
    const clash = nodes.some(
      (n) =>
        Math.abs(n.position.x - pos.x) < CARD_WIDTH &&
        Math.abs(n.position.y - pos.y) < CARD_SLOT_H,
    )
    if (!clash) break
    pos.y += CARD_SLOT_H
  }
  return pos
}
