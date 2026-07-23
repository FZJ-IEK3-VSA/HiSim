export interface InputPort {
  field_name: string
  load_type: string
  unit: string
  mandatory: boolean
  tags: string[]
}

export interface OutputPort {
  field_name: string
  load_type: string
  unit: string
  postprocessing_flag: boolean
  sankey_flow_direction: string | null
  output_description: string
}

/** One entry within a default_connections value list (source_class_name is the dict key). */
export interface DefaultConnectionEntry {
  target_input_name: string
  source_output_name: string
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
}

export interface ComponentDb {
  generated_at: string
  components: ComponentEntry[]
  failures: Array<{ classname: string; error: string }>
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
  /** Input port names that could not be auto-connected (zero or multiple candidates). */
  unresolvedPorts?: string[]
}
