// Tests for the dynamic ports a DynamicComponent grows at run time (src/io/dynamicPorts.ts).
//
// The naming is the delicate part: HiSim labels a dynamic input
// `Input_{source}_{output}_{len(self.inputs)}`, so the *order* the simulator walks the
// components in decides the port names, and a connection naming the wrong index is silently
// dropped. Rather than assert that against a hand-written expectation, these tests use the
// shipped scenarios as ground truth: take a file whose dynamic ports are written out
// explicitly (they were produced by HiSim itself), switch the component to
// `connect_automatically` and delete them, and the editor must derive exactly the ports that
// were there.

import { describe, it, expect } from 'vitest'
import { loadComponentDb, loadUsageDb, readScenario } from './scenarios'
import { importScenario } from '../src/io/import'
import { exportScenario } from '../src/io/export'
import { validateScenario } from '../src/io/validate'
import {
  buildDynamicOutput,
  dynamicInputOptions,
  dynamicOutputOptions,
} from '../src/io/dynamicPorts'
import type { DynamicInputPort } from '../src/types'

const db = loadComponentDb()
const usage = loadUsageDb()
const SCENARIO = 'household_gas_building_sizer.scenario.json'
const EMS = 'L2EMSElectricityController'

/* eslint-disable @typescript-eslint/no-explicit-any */

/** Switch a component to connect_automatically and drop the dynamic inputs it spells out. */
function deferToAutoConnect(text: string, componentName: string): string {
  const scenario = JSON.parse(text)
  for (const comp of scenario.components ?? []) {
    if (comp?.configuration?.name !== componentName) continue
    comp.connect_automatically = true
    comp.inputs = (comp.inputs ?? []).filter((i: any) => !i.dynamic)
  }
  // The connections that referenced those ports go with them.
  scenario.connections = (scenario.connections ?? []).filter(
    (c: any) =>
      !(c?.target?.component_name === componentName && String(c?.target?.field_name).startsWith('Input_')),
  )
  return JSON.stringify(scenario)
}

/** The dynamic input ports of one component after import. */
function dynamicInputsOf(text: string, componentName: string): DynamicInputPort[] {
  const imported = importScenario(text, db)
  const node = imported.nodes.find((n) => n.data.instanceName === componentName)
  return (node?.data.dynamicInputs as DynamicInputPort[] | undefined) ?? []
}

describe('dynamic ports derived from default connections', () => {
  const original = readScenario(SCENARIO)

  it('reproduces the exact ports the scenario spelled out', () => {
    // What the file says, written by HiSim's own numbering.
    const declared = dynamicInputsOf(original, EMS)
    expect(declared.map((p) => p.field_name)).toEqual([
      'Input_UTSPConnector_ElectricalPowerConsumption_2',
      'Input_PVSystem_ElectricityOutput_3',
      'Input_Battery_AcBatteryPowerUsed_4',
    ])
    expect(declared.every((p) => !p.auto)).toBe(true)

    // What the editor derives when the file leaves it to the simulator instead.
    const derived = dynamicInputsOf(deferToAutoConnect(original, EMS), EMS)
    expect(derived.map((p) => p.field_name)).toEqual(declared.map((p) => p.field_name))
    expect(derived.every((p) => p.auto)).toBe(true)
  })

  it('carries the load type, unit, tags and source weight of the declaration', () => {
    const declared = dynamicInputsOf(original, EMS)
    const derived = dynamicInputsOf(deferToAutoConnect(original, EMS), EMS)
    for (const [i, port] of derived.entries()) {
      expect(port.source_object_name).toBe(declared[i].source_object_name)
      expect(port.source_component_output).toBe(declared[i].source_component_output)
      expect(port.load_type).toBe(declared[i].load_type)
      expect(port.unit).toBe(declared[i].unit)
      expect(port.source_weight).toBe(declared[i].source_weight)
      expect(port.source_tags).toEqual(declared[i].source_tags)
    }
  })

  it('connects them, so nothing is left looking unwired', () => {
    const text = deferToAutoConnect(original, EMS)
    const imported = importScenario(text, db)
    const ems = imported.nodes.find((n) => n.data.instanceName === EMS)
    const ports = (ems?.data.dynamicInputs as DynamicInputPort[]) ?? []
    for (const port of ports) {
      const edge = imported.edges.find(
        (e) => e.target === ems?.id && e.targetHandle === `input-${port.field_name}`,
      )
      expect(edge).toBeDefined()
      const source = imported.nodes.find((n) => n.id === edge?.source)
      expect(source?.data.instanceName).toBe(port.source_object_name)
      expect(edge?.sourceHandle).toBe(`output-${port.source_component_output}`)
    }
  })

  it('does not write derived ports back to the file', () => {
    // HiSim recreates them from the defaults; writing them to inputs[] as well would create
    // each port twice, shifting every index after it.
    const text = deferToAutoConnect(original, EMS)
    const imported = importScenario(text, db)
    const saved = JSON.parse(
      exportScenario(imported.nodes, imported.edges, imported.scenarioName, imported.scenarioDescription),
    )
    const ems = saved.components.find((c: any) => c.configuration.name === EMS)
    expect(ems.connect_automatically).toBe(true)
    expect(ems.inputs).toEqual([])
    expect(
      saved.connections.filter((c: any) => String(c?.target?.field_name).startsWith('Input_')
        && c?.target?.component_name === EMS),
    ).toEqual([])
  })

  it('creates nothing when the component is not left to connect itself', () => {
    // connect_automatically off is the whole gate: the scenario then has to declare its ports.
    const scenario = JSON.parse(original)
    for (const comp of scenario.components ?? []) {
      if (comp?.configuration?.name === EMS) comp.inputs = []
    }
    const stripped = JSON.stringify(scenario)
    expect(dynamicInputsOf(stripped, EMS)).toEqual([])
  })

  it('offers the same ports for manual declaration when the toggle is off', () => {
    const scenario = JSON.parse(original)
    for (const comp of scenario.components ?? []) {
      if (comp?.configuration?.name === EMS) comp.inputs = []
    }
    const imported = importScenario(JSON.stringify(scenario), db)
    const ems = imported.nodes.find((n) => n.data.instanceName === EMS)!
    const options = dynamicInputOptions(ems, imported.nodes)
    expect(options.map((o) => `${o.sourceNodeName}.${o.sourceOutput}`)).toEqual([
      'UTSPConnector.ElectricalPowerConsumption',
      'PVSystem.ElectricityOutput',
      'Battery.AcBatteryPowerUsed',
    ])
  })
})

describe('dynamic output channels', () => {
  const original = readScenario(SCENARIO)

  it('offers the EMS output a battery needs, with the port it feeds', () => {
    // Nothing in HiSim declares this channel — every system setup that puts a battery under
    // EMS control writes it by hand — so it is mined from the shipped scenarios instead.
    const scenario = JSON.parse(original)
    for (const comp of scenario.components ?? []) {
      if (comp?.configuration?.name === EMS) comp.outputs = []
    }
    scenario.connections = (scenario.connections ?? []).filter(
      (c: any) => !String(c?.source?.field_name).startsWith('LoadingPowerInputForBattery_'),
    )
    const imported = importScenario(JSON.stringify(scenario), db)
    const ems = imported.nodes.find((n) => n.data.instanceName === EMS)!

    const options = dynamicOutputOptions(ems, imported.nodes, usage)
    const battery = options.find(
      (o) => o.declaration.source_output_name === 'LoadingPowerInputForBattery_',
    )
    expect(battery).toBeDefined()
    expect(battery?.declaration.source_weight).toBe(6)
    expect(battery?.targets[0].inputName).toBe('LoadingPowerInput')
    expect(battery?.targets[0].nodeName).toBe('Battery')

    // …and building it reproduces the port name the shipped scenario has.
    expect(buildDynamicOutput(ems, battery!.declaration).field_name).toBe(
      'LoadingPowerInputForBattery_Output16',
    )
  })

  it('does not offer a channel the scenario already declares', () => {
    const imported = importScenario(original, db)
    const ems = imported.nodes.find((n) => n.data.instanceName === EMS)!
    const options = dynamicOutputOptions(ems, imported.nodes, usage)
    expect(
      options.some((o) => o.declaration.source_output_name === 'LoadingPowerInputForBattery_'),
    ).toBe(false)
  })
})

describe('connect_automatically that HiSim would refuse', () => {
  const original = readScenario(SCENARIO)

  it('reports a component that declares no default connections', () => {
    // Simulator.connect_everything_automatically raises KeyError for these, before the first
    // time step — and nothing else on the canvas looks wrong.
    const scenario = JSON.parse(original)
    for (const comp of scenario.components ?? []) {
      if (comp?.configuration?.name === 'Weather') comp.connect_automatically = true
    }
    const imported = importScenario(JSON.stringify(scenario), db)
    const { errors } = validateScenario(imported.nodes, imported.edges)
    expect(errors.some((e) => e.includes('Weather') && e.includes('KeyError'))).toBe(true)
  })

  it('reports a component whose declared sources are all absent', () => {
    const scenario = JSON.parse(original)
    scenario.components = (scenario.components ?? []).filter(
      (c: any) => !String(c?.component_full_classname).endsWith('.GenericBoiler'),
    )
    scenario.connections = (scenario.connections ?? []).filter(
      (c: any) =>
        c?.source?.component_name !== 'CondensingGasBoiler' &&
        c?.target?.component_name !== 'CondensingGasBoiler',
    )
    for (const comp of scenario.components ?? []) {
      if (comp?.configuration?.name === 'GasMeter') comp.connect_automatically = true
    }
    const imported = importScenario(JSON.stringify(scenario), db)
    const { errors } = validateScenario(imported.nodes, imported.edges)
    expect(errors.some((e) => e.includes('GasMeter') && e.includes('KeyError'))).toBe(true)
  })
})

/* eslint-enable @typescript-eslint/no-explicit-any */
