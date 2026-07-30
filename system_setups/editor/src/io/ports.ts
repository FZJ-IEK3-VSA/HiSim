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

/**
 * The port's effective load type for a given configuration.
 *
 * Some components type their ports from config rather than fixing them in code — CSVLoader
 * passes `config.loadtype` / `config.unit` straight to `add_output`, and SumBuilder,
 * CalculateOperation, SimpleStorage and GasMeter do the same. The component database records
 * whichever value the *default* config produced, so reading `port.load_type` directly would
 * compare a scenario's kW against a registry snapshot's W and report a mismatch that does not
 * exist. `tools/generate_component_db.py` marks these ports with `load_type_from_config` /
 * `unit_from_config`, and only when the mapping is the identity, so resolving against the
 * node's config is safe.
 */
export function portLoadType(port: TypedPort, config: Record<string, unknown>): string {
  return resolveFromConfig(port.load_type_from_config, config) ?? port.load_type
}

/** The port's effective unit for a given configuration. See portLoadType. */
export function portUnit(port: TypedPort, config: Record<string, unknown>): string {
  return resolveFromConfig(port.unit_from_config, config) ?? port.unit
}

/**
 * Anything carrying a load type and a unit — a static InputPort/OutputPort, or a
 * DynamicInputPort/DynamicOutputPort. Dynamic ports never carry the `*_from_config` markers
 * (their types come from the scenario itself), so they resolve to their own values.
 */
interface TypedPort {
  load_type: string
  unit: string
  load_type_from_config?: string
  unit_from_config?: string
}

function resolveFromConfig(
  field: string | undefined,
  config: Record<string, unknown>,
): string | undefined {
  if (!field) return undefined
  const value = config[field]
  return value === undefined || value === null ? undefined : String(value)
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
