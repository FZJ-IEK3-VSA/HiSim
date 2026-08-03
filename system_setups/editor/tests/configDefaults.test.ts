// Config fields the component registry has no default for (src/io/validate.ts, check 5b).
//
// `tools/generate_component_db.py` has to construct every config to read its ports, and where
// a `get_default_*` factory demands a value it invents one — `0.0`, `null` — purely to get an
// instance. That placeholder then looks exactly like a default in the editor. It is not: the
// Python system setups compute those figures (HeatDistribution's floor area comes off the
// building) and a scenario that keeps the placeholder runs on a zero nothing checks, until the
// heat distribution system divides by its own zero pipe surface mid-simulation.
//
// So the database marks them, and this pins down which fields carry the mark: too many and the
// editor nags for values HiSim would happily derive, too few and the zero gets through again.

import { describe, it, expect } from 'vitest'
import { listScenarioFiles, readScenario, loadComponentDb } from './scenarios'
import { importScenario } from '../src/io/import'
import { validateScenario } from '../src/io/validate'
import type { ComponentEntry } from '../src/types'
import type { HiSimNode } from '../src/store'

const db = loadComponentDb()
const files = listScenarioFiles()

const entry = (shortName: string): ComponentEntry => {
  const found = db.components.find(
    (c) => c.component_full_classname.split('.').pop() === shortName,
  )
  if (!found) throw new Error(`${shortName} is not in the component database`)
  return found
}

const flagged = (shortName: string, flag: 'must_be_set' | 'auto_derived') =>
  entry(shortName)
    .config_fields.filter((f) => f[flag])
    .map((f) => f.name)
    .sort()

describe('fields the user has to supply', () => {
  it('are the ones with no default and no derivation', () => {
    // The heat distribution system: the floor area and the system type describe the building
    // and the installation, so only their owner can state them.
    expect(flagged('HeatDistribution', 'must_be_set')).toEqual([
      'absolute_conditioned_floor_area_in_m2',
      'heating_system',
    ])
    expect(flagged('HeatDistributionController', 'must_be_set')).toEqual([
      'set_cooling_temperature_for_building_in_celsius',
      'set_heating_temperature_for_building_in_celsius',
    ])
  })

  it('exclude figures the component derives from the building', () => {
    // Nobody knows their circuit's design mass flow rate or their building's design heating
    // load offhand — HiSim computes both in i_prepare_simulation and logs how.
    expect(flagged('HeatDistribution', 'auto_derived')).toEqual([
      'water_mass_flow_rate_in_kg_per_second',
    ])
    expect(flagged('HeatDistributionController', 'auto_derived')).toEqual([
      'heating_load_of_building_in_watt',
    ])
    expect(flagged('HeatStorageController', 'auto_derived')).toEqual([
      'heating_load_of_building_in_watt',
    ])
    for (const component of ['HeatDistribution', 'HeatDistributionController', 'HeatStorageController']) {
      expect(flagged(component, 'must_be_set')).not.toContain('heating_load_of_building_in_watt')
      expect(flagged(component, 'must_be_set')).not.toContain('water_mass_flow_rate_in_kg_per_second')
    }
  })

  it('exclude fields where zero is a real setting', () => {
    // A fully modulating boiler has no lower power limit, and five shipped scenarios set
    // exactly zero. Its *maximum* is a different matter.
    expect(flagged('GenericBoilerController', 'must_be_set')).toEqual([
      'maximal_thermal_power_in_watt',
    ])
  })

  it('never include the fields the editor writes itself', () => {
    for (const component of db.components) {
      const names = component.config_fields.filter((f) => f.must_be_set).map((f) => f.name)
      expect(names).not.toContain('name')
      expect(names).not.toContain('building_name')
    }
  })
})

describe('validation of unset fields', () => {
  it.each(files)('%s supplies every field that has no default', (file) => {
    const imported = importScenario(readScenario(file), db)
    const { errors } = validateScenario(imported.nodes, imported.edges)
    expect(errors.filter((e) => e.includes('has no default'))).toEqual([])
  })

  it('reports a placeholder left in place', () => {
    const hds = entry('HeatDistribution')
    const node: HiSimNode = {
      id: 'n1',
      type: 'componentCard',
      position: { x: 0, y: 0 },
      data: {
        entry: hds,
        instanceName: 'HeatDistributionSystem',
        // Exactly what dragging the component onto the canvas produces.
        config: { ...hds.default_config },
        collapsed: true,
        connectAutomatically: false,
      },
    }
    const { errors } = validateScenario([node], [])
    const unset = errors.filter((e) => e.includes('has no default'))

    expect(unset).toHaveLength(2)
    expect(unset.join(' ')).toContain('absolute_conditioned_floor_area_in_m2')
    expect(unset.join(' ')).toContain('heating_system')
    // The mass flow rate is zero here too, and deliberately not complained about.
    expect(unset.join(' ')).not.toContain('water_mass_flow_rate_in_kg_per_second')
  })

  it('stops complaining once the values are supplied', () => {
    const hds = entry('HeatDistribution')
    const node: HiSimNode = {
      id: 'n1',
      type: 'componentCard',
      position: { x: 0, y: 0 },
      data: {
        entry: hds,
        instanceName: 'HeatDistributionSystem',
        config: {
          ...hds.default_config,
          absolute_conditioned_floor_area_in_m2: 121.2,
          heating_system: 2,
        },
        collapsed: true,
        connectAutomatically: false,
      },
    }
    const { errors } = validateScenario([node], [])
    expect(errors.filter((e) => e.includes('has no default'))).toEqual([])
  })
})
