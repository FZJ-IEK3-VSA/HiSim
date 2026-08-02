// Enum-typed config fields keep their JSON type (src/io/configEnums.ts).
//
// The failure this guards against is silent in the editor and fatal in HiSim: an enum whose
// members are numbers, written to the scenario as text. `BoilerType.CONDENSING` is `2`, and
// `BoilerType("2")` raises on load — after the user has already saved the file and started
// the simulation.

import { describe, it, expect } from 'vitest'
import { listScenarioFiles, readScenario, loadComponentDb, loadEnumDb } from './scenarios'
import { importScenario } from '../src/io/import'
import { validateScenario } from '../src/io/validate'
import { enumOptionsFor, isExactEnumValue, typedEnumValue } from '../src/io/configEnums'
import type { HiSimNode } from '../src/store'

const db = loadComponentDb()
const enumDb = loadEnumDb()

/** Every (component, config field) pair whose type is an enum. */
const enumFields = db.components.flatMap((entry) =>
  entry.config_fields
    .filter((f) => f.enum_class)
    .map((field) => ({ component: entry.component_full_classname, entry, field })),
)

describe('enum-typed config fields', () => {
  it('finds enum fields to check', () => {
    expect(enumFields.length).toBeGreaterThan(10)
  })

  // Without this the field degrades to a free-text box, which is how a number-valued enum
  // ends up in the scenario as a string in the first place.
  it('all resolve to a dropdown, not a free-text input', () => {
    const unresolved = enumFields
      .filter(({ field }) => enumOptionsFor(field.enum_class, enumDb) === null)
      .map(({ component, field }) => `${component}.${field.name} (${field.enum_class})`)
    expect(unresolved).toEqual([])
  })

  it('offer the registry default with its own JSON type', () => {
    const mistyped: string[] = []
    for (const { component, field } of enumFields) {
      const options = enumOptionsFor(field.enum_class, enumDb)
      if (!options || field.default === null || field.default === '') continue
      if (!isExactEnumValue(field.default, options)) {
        mistyped.push(
          `${component}.${field.name}: default ${JSON.stringify(field.default)} ` +
          `(${typeof field.default}) is not an exact ${field.enum_class} member`,
        )
      }
    }
    expect(mistyped).toEqual([])
  })

  it('turn a select\'s string back into the typed member value', () => {
    // BoilerType is the reported case: members are numbers, and a <select> can only hand
    // back "2".
    const options = enumOptionsFor('BoilerType', enumDb)
    expect(options).not.toBeNull()
    const value = typedEnumValue('2', options!)
    expect(value).toBe(2)
    expect(typeof value).toBe('number')

    // A string-valued enum stays a string.
    const loadTypes = enumOptionsFor('LoadTypes', enumDb)!
    expect(typeof typedEnumValue('Gas', loadTypes)).toBe('string')

    // The empty option (an optional field set to nothing) clears the value.
    expect(typedEnumValue('', options!)).toBeNull()
  })

  it('label a number-valued member by name and a string-valued one by value', () => {
    const boiler = enumOptionsFor('BoilerType', enumDb)!
    expect(boiler.map((o) => o.label)).toContain('CONDENSING (2)')

    const loadTypes = enumOptionsFor('LoadTypes', enumDb)!
    expect(loadTypes.map((o) => o.label)).toContain('Gas')
  })
})

describe('validation of enum-typed config values', () => {
  const scenarios = listScenarioFiles()

  it.each(scenarios)('%s holds only exact enum members', (file) => {
    const imported = importScenario(readScenario(file), db)
    // The difference the enum registry makes, which is exactly this check's output —
    // unrelated warnings stay the business of tests/roundtrip.test.ts.
    const withEnums = validateScenario(imported.nodes, imported.edges, enumDb).warnings
    const without = validateScenario(imported.nodes, imported.edges).warnings
    expect(withEnums.filter((w) => !without.includes(w))).toEqual([])
  })

  it('reports a number-valued enum stored as text', () => {
    const boiler = db.components.find((c) =>
      c.config_fields.some((f) => f.enum_class === 'BoilerType'),
    )
    expect(boiler).toBeDefined()

    // Exactly what the broken free-text input used to produce.
    const node: HiSimNode = {
      id: 'n1',
      type: 'componentCard',
      position: { x: 0, y: 0 },
      data: {
        entry: boiler!,
        instanceName: 'Boiler',
        config: { ...boiler!.default_config, boiler_type: '2' },
        collapsed: true,
        connectAutomatically: false,
      },
    }
    const { warnings } = validateScenario([node], [], enumDb)
    const complaint = warnings.filter((w) => w.includes('boiler_type'))
    expect(complaint).toHaveLength(1)
    expect(complaint[0]).toContain('"2"')
    expect(complaint[0]).toContain('BoilerType expects 2')

    // ...and says nothing once the value carries the right type.
    const fixed: HiSimNode = {
      ...node,
      data: { ...node.data, config: { ...node.data.config, boiler_type: 2 } },
    }
    expect(validateScenario([fixed], [], enumDb).warnings.filter((w) => w.includes('boiler_type')))
      .toEqual([])
  })
})
