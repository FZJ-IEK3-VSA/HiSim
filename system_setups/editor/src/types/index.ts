export interface InputPort {
  field_name: string
  load_type: string
  unit: string
  mandatory: boolean
  tags: string[]
  /**
   * Set when the port only exists for a non-default config setting (e.g.
   * MoreAdvancedHeatPumpHPLib's DHW ports under `with_domestic_hot_water_preparation`).
   * Names the config field — `"flag"` for a boolean, `"field=MEMBER"` for an enum.
   */
  conditional_on?: string
  /**
   * Set when the port's load type / unit is taken straight from a config field rather than
   * fixed in code (e.g. CSVLoader passes `config.loadtype` / `config.unit` to `add_output`).
   * Names the field; the effective type is the node's current config value.
   */
  load_type_from_config?: string
  unit_from_config?: string
}

export interface OutputPort {
  field_name: string
  load_type: string
  unit: string
  postprocessing_flag: boolean
  sankey_flow_direction: string | null
  output_description: string
  /** See InputPort.conditional_on. */
  conditional_on?: string
  /** See InputPort.load_type_from_config / unit_from_config. */
  load_type_from_config?: string
  unit_from_config?: string
}

/** One entry within a default_connections value list (source_class_name is the dict key). */
export interface DefaultConnectionEntry {
  target_input_name: string
  source_output_name: string
}

/**
 * One input port a DynamicComponent grows for a given source class.
 *
 * A dynamic component declares no such port up front; `Simulator.prepare_calculation` creates
 * it — with this exact metadata — for every component registered with
 * `connect_automatically=True` whose class appears in the target's
 * `dynamic_default_connections`. There is no `target_input_name` because the port does not
 * exist yet: its name is *derived* when it is created (see io/dynamicPorts.ts).
 */
export interface DynamicDefaultConnection {
  source_component_field_name: string
  source_load_type: string
  source_unit: string
  source_tags: string[]
  source_weight: number
}

export interface ConfigField {
  name: string
  type: string
  is_optional: boolean
  enum_class: string | null
  default: unknown
}

export interface ComponentEntry {
  component_full_classname: string
  config_full_classname: string
  display_name: string
  category: string
  is_dynamic: boolean
  default_config: Record<string, unknown>
  config_fields: ConfigField[]
  input_ports: InputPort[]
  output_ports: OutputPort[]
  // { "SourceClassName": [{target_input_name, source_output_name}, ...] }
  default_connections: Record<string, DefaultConnectionEntry[]>
  /** { "SourceClassName": [...] } — empty unless `is_dynamic`. */
  dynamic_default_connections: Record<string, DynamicDefaultConnection[]>
  /** Config switches that had to be flipped to reveal `conditional_on` ports. */
  conditional_flags: string[]
}

/** A default_connections declaration naming a port that does not exist. */
export interface DefaultConnectionIssue {
  component: string
  source_class: string
  target_input_name: string
  source_output_name: string
  problem: string
  known: boolean
}

export interface ComponentDb {
  generated_at: string
  components: ComponentEntry[]
  failures: Array<{ classname: string; error: string }>
  default_connection_issues?: DefaultConnectionIssue[]
}

export interface EnumDb {
  load_types: string[]
  units: string[]
  component_types: string[]
  in_and_output_types: string[]
  building_codes: string[]
  locations: string[]
  post_processing_options: Array<{ name: string; value: string }>
  generated_at: string
}

/**
 * A dynamic input port synthesised from a component's `inputs[]` array in the scenario JSON.
 * Dynamic components (e.g. ElectricityMeter) collect inputs at runtime rather than declaring
 * them statically; the field_name follows the pattern Input_{source_object}_{output}_{index}.
 */
export interface DynamicInputPort {
  field_name: string             // e.g. "Input_PVSystem_ElectricityOutput_0"
  load_type: string
  unit: string
  source_object_name: string     // instance name of the source component
  source_component_output: string
  source_tags: string[]
  source_weight: number
  /**
   * True when the port was derived from `dynamic_default_connections` rather than declared in
   * the scenario file. HiSim recreates these itself at `prepare_calculation`, so they are
   * *not* written to `inputs[]` on export — writing them would create the port twice.
   */
  auto?: boolean
}

/**
 * A dynamic output port synthesised from a component's `outputs[]` array in the scenario JSON.
 *
 * HiSim's DynamicComponent.add_component_output appends `Output{n}` to the declared prefix,
 * where n counts every output the component already has — so the scenario JSON stores only
 * the prefix (`source_output_name`) while connections reference the full name. The editor
 * reconstructs `field_name` the same way; see synthesiseDynamicOutputName in io/import.ts.
 */
export interface DynamicOutputPort {
  field_name: string             // e.g. "LoadingPowerInputForBattery_Output16"
  source_output_name: string     // the stored prefix, e.g. "LoadingPowerInputForBattery_"
  load_type: string
  unit: string
  source_tags: string[]
  source_weight: number
  output_description: string
  source_component_class: string | null
}

// ── Usage statistics ─────────────────────────────────────────────────────────

/** How often one component class appears in the shipped scenarios, and alongside what. */
export interface UsageEntry {
  /** Number of shipped scenarios containing this component class. */
  scenarios: number
  /** Other component class → number of those scenarios that also contain it. */
  companions: Record<string, number>
}

/**
 * Mined from `system_setups/*.scenario.json` by tools/generate_component_db.py.
 *
 * The component registry can only say what a component declares about *itself*, which is
 * blind to a whole class of omission: leaving out a consumer (a meter, a battery) breaks
 * nothing structurally — no port is left unconnected, the system is just smaller. This
 * table is the only evidence of which components belong together, and it drives the
 * "commonly used together" half of io/suggest.ts.
 */
/**
 * A dynamic *output* port that the shipped scenarios declare on a component, and what it feeds.
 *
 * The counterpart to `DynamicDefaultConnection`, and the one thing introspection cannot
 * provide: an output only exists where a setup explicitly calls `add_component_output`. The
 * EMS's `LoadingPowerInputForBattery_` channel is written by hand in every system setup that
 * puts a battery under EMS control, and nothing in HiSim declares that it ought to exist — so
 * the scenarios are mined for it instead.
 */
export interface DynamicOutputDeclaration {
  source_output_name: string     // the prefix; HiSim appends Output{n}
  source_tags: string[]
  source_load_type: string
  source_unit: string
  source_weight: number
  output_description: string
  source_component_class: string | null
  /** Where this output was connected, in the scenarios that declare it. */
  feeds: Array<{ component_class: string; target_input_name: string }>
  /** Number of shipped scenarios declaring it. */
  scenarios: number
}

export interface UsageDb {
  generated_at: string
  scenario_count: number
  components: Record<string, UsageEntry>
  /** component classname → dynamic output ports the shipped scenarios declare on it. */
  dynamic_outputs: Record<string, DynamicOutputDeclaration[]>
}

// ── Domain catalogs ──────────────────────────────────────────────────────────

export interface WeatherDataset {
  label: string
  path: string
}

export interface HeatPumpModel {
  manufacturer: string
  name: string
}

export interface CatalogDb {
  generated_at: string
  weather_datasets: WeatherDataset[]
  heat_pump_models: HeatPumpModel[]
  /** module_database enum value (as string) → list of module names */
  pv_modules: Record<string, string[]>
  /** inverter_database enum value (as string) → list of inverter names */
  pv_inverters: Record<string, string[]>
  predefined_load_profiles: string[]
  /** config_full_classname → { field_name: override_value } — applied on new node creation */
  config_overrides: Record<string, Record<string, unknown>>
}

/** Data stored on each React Flow node. Must extend Record<string, unknown> for RF types. */
export interface ComponentNodeData extends Record<string, unknown> {
  entry: ComponentEntry
  instanceName: string
  config: Record<string, unknown>
  collapsed: boolean
  connectAutomatically: boolean
  /** Parsed from the scenario JSON's inputs[] — only present on dynamic components. */
  dynamicInputs?: DynamicInputPort[]
  /** Parsed from the scenario JSON's outputs[] — only present on dynamic components. */
  dynamicOutputs?: DynamicOutputPort[]
  /** Input port names that could not be auto-connected (zero or multiple candidates). */
  unresolvedPorts?: string[]
}
