import type { InputPort, OutputPort } from '../types'

/**
 * Whether a port actually exists for a given component configuration.
 *
 * The component database is a *superset*: `tools/generate_component_db.py` re-instantiates
 * each component with non-default config switches so that conditionally declared ports (e.g.
 * MoreAdvancedHeatPumpHPLib's DHW ports, only added under
 * `if self.with_domestic_hot_water_preparation:`) are present in the registry. Those ports
 * carry `conditional_on`, naming the switch that reveals them:
 *
 *   - `"with_domestic_hot_water_preparation"` — a boolean flag, active when the config has it true
 *   - `"fuel=Electricity"`                    — an enum field, active when the config equals that value
 *
 * A port may in truth be revealed by more than one setting while `conditional_on` records only
 * the first one found, so this predicate is used **only to suppress** checks (unconnected
 * mandatory inputs), never to raise new ones — an imperfect match then costs a missing hint
 * rather than a bogus error.
 */
export function isPortActive(
  port: InputPort | OutputPort,
  config: Record<string, unknown>,
): boolean {
  const condition = port.conditional_on
  if (!condition) return true

  const eq = condition.indexOf('=')
  if (eq === -1) return config[condition] === true

  const field = condition.slice(0, eq)
  const expected = condition.slice(eq + 1)
  return String(config[field]) === expected
}

/** Input ports that exist for this configuration. */
export function activeInputPorts(
  ports: InputPort[],
  config: Record<string, unknown>,
): InputPort[] {
  return ports.filter((p) => isPortActive(p, config))
}

/** Output ports that exist for this configuration. */
export function activeOutputPorts(
  ports: OutputPort[],
  config: Record<string, unknown>,
): OutputPort[] {
  return ports.filter((p) => isPortActive(p, config))
}
