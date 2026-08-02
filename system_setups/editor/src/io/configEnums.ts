// Enum-valued config fields: which values a field may take, and in what JSON type.
//
// The type is the whole point. A `<select>` hands back `event.target.value`, which is always
// a string, and HiSim's configs are deserialised straight into their dataclasses — so a
// number-valued enum written as text fails at load: `BoilerType("2")` raises where
// `BoilerType(2)` is fine. Every value the editor writes therefore comes from the generated
// registry with its type intact, never from the DOM.

import type { EnumDb } from '../types'

export type EnumValue = string | number | boolean

export interface EnumOption {
  /** What the dropdown shows. */
  label: string
  /** What goes into the config — the member's own value, typed. */
  value: EnumValue
}

/**
 * The options for a config field typed with `enumClass`, or null if it is not an enum the
 * databases know (the field then stays a plain input).
 */
export function enumOptionsFor(
  enumClass: string | null | undefined,
  enumDb: EnumDb,
): EnumOption[] | null {
  if (!enumClass) return null

  const members = enumDb.config_enums?.[enumClass]
  if (members && members.length > 0) {
    return members.map((m) => ({
      // For a string-valued enum the value *is* the readable label (LoadTypes.GAS = "Gas").
      // For anything else it is opaque, so the member name carries the meaning and the raw
      // value is shown alongside it, because that is what lands in the JSON.
      label: typeof m.value === 'string' ? m.value : `${m.name} (${String(m.value)})`,
      value: m.value,
    }))
  }

  // Enums from loadtypes.py that no config field happens to be typed with — kept so a field
  // referring to one still gets a dropdown. All of these are string-valued.
  const fromEnumLists: Record<string, string[] | undefined> = {
    Locations: enumDb.locations,
    BuildingCodes: enumDb.building_codes,
    LoadTypes: enumDb.load_types,
    Units: enumDb.units,
    ComponentType: enumDb.component_types,
    InandOutputType: enumDb.in_and_output_types,
    PostProcessingOptions: enumDb.post_processing_options.map((o) => o.value),
  }
  const values = fromEnumLists[enumClass]
  return values ? values.map((v) => ({ label: v, value: v })) : null
}

/**
 * Turn a `<select>`'s string back into the member value it stands for.
 *
 * Options are keyed by `String(value)` in the DOM, so the reverse lookup is exact. An
 * unmatched string is passed through rather than dropped — it can only come from a stored
 * value that is not a member of the enum, which validation reports rather than silently
 * rewriting.
 */
export function typedEnumValue(raw: string, options: EnumOption[]): EnumValue | null {
  if (raw === '') return null
  const match = options.find((o) => String(o.value) === raw)
  return match ? match.value : raw
}

/** How a member value is identified in the DOM (`<option value>`), and matched back. */
export const optionKey = (value: EnumValue): string => String(value)

/**
 * Whether `value` is exactly one of the enum's members — same value *and* same type.
 *
 * `"2"` and `2` are the same option to a `<select>` and different things to Python, which is
 * the failure this distinguishes.
 */
export function isExactEnumValue(value: unknown, options: EnumOption[]): boolean {
  return options.some((o) => o.value === value)
}
