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

export interface DefaultConnection {
  target_input_name: string
  source_class_name: string
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
  default_connections: DefaultConnection[]
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

/** Data stored on each React Flow node. Must extend Record<string, unknown> for RF types. */
export interface ComponentNodeData extends Record<string, unknown> {
  entry: ComponentEntry
  instanceName: string
  config: Record<string, unknown>
  collapsed: boolean
  connectAutomatically: boolean
}
