// Inspector section for a DynamicComponent's variable ports.
//
// Three groups, matching the three ways such a port comes into existence (io/dynamicPorts.ts):
// ports the simulator derives from the component's declared defaults (shown, not editable —
// they follow the *Connect automatically* toggle), ports the scenario declares itself
// (add/remove), and the output channels only ever written by hand, offered from what the
// shipped scenarios do.

import { useEditorStore } from '../store'
import type { HiSimNode } from '../store'
import {
  buildDynamicOutput,
  buildExplicitDynamicInput,
  dynamicInputOptions,
  dynamicOutputOptions,
} from '../io/dynamicPorts'
import type { DynamicInputPort, DynamicOutputPort } from '../types'

export default function DynamicPortsPanel({ node }: { node: HiSimNode }) {
  const nodes = useEditorStore((s) => s.nodes)
  const usageDb = useEditorStore((s) => s.usageDb)
  const addDynamicInput = useEditorStore((s) => s.addDynamicInput)
  const addDynamicOutput = useEditorStore((s) => s.addDynamicOutput)
  const removeDynamicInput = useEditorStore((s) => s.removeDynamicInput)
  const removeDynamicOutput = useEditorStore((s) => s.removeDynamicOutput)

  if (!node.data.entry.is_dynamic) return null

  const inputs = (node.data.dynamicInputs as DynamicInputPort[] | undefined) ?? []
  const outputs = (node.data.dynamicOutputs as DynamicOutputPort[] | undefined) ?? []
  const inputOptions = dynamicInputOptions(node, nodes, usageDb)
  const outputOptions = dynamicOutputOptions(node, nodes, usageDb)
  const auto = node.data.connectAutomatically

  return (
    <div className="pt-1 border-t border-gray-100">
      <p className="text-[11px] font-medium text-gray-500 mb-0.5">Dynamic ports</p>
      <p className="text-[10px] text-gray-400 mb-1.5 leading-snug">
        {auto
          ? 'HiSim creates the ports below at run time, one per matching component on the canvas. Switch “Connect automatically” off to write them into the file instead.'
          : 'This component has no ports until the scenario declares them. Add the ones you need.'}
      </p>

      {/* ── Inputs ─────────────────────────────────────────────────────── */}
      {inputs.length === 0 && inputOptions.length === 0 && (
        <p className="text-[11px] text-gray-400 italic">
          No inputs — add the components this one should measure or control.
        </p>
      )}

      {inputs.map((port) => (
        <div key={port.field_name} className="flex items-center gap-1 text-[11px] leading-5">
          <span className="text-gray-400">→</span>
          <span className="truncate italic" title={port.field_name}>
            {port.source_object_name}: {port.source_component_output}
          </span>
          {port.auto ? (
            <span
              className="ml-auto shrink-0 text-[9px] px-1 rounded bg-blue-50 text-blue-600"
              title="Derived from the component's default connections — HiSim recreates it, so it is not written to the file."
            >
              auto
            </span>
          ) : (
            <button
              className="ml-auto shrink-0 text-gray-300 hover:text-red-500 px-1"
              title="Remove this port and its connection"
              onClick={() => removeDynamicInput(node.id, port.field_name)}
            >
              ✕
            </button>
          )}
        </div>
      ))}

      {!auto && inputOptions.length > 0 && (
        <div className="mt-1">
          <p className="text-[10px] text-gray-400">Available from components on the canvas</p>
          {inputOptions.map((option) => (
            <div
              key={`${option.sourceNodeId}-${option.sourceOutput}`}
              className="flex items-center gap-1 text-[11px] leading-5"
            >
              <span className="text-gray-300">+</span>
              <span className="truncate text-gray-500" title={option.sourceOutput}>
                {option.sourceNodeName}: {option.sourceOutput}
              </span>
              {option.scenarios !== undefined && (
                <span
                  className="shrink-0 text-[9px] px-1 rounded bg-amber-50 text-amber-700"
                  title={
                    'Not declared by this component — mined from the shipped scenarios, where ' +
                    `${option.scenarios} of them wire it by hand. Adding it is a deliberate ` +
                    'choice: an aggregate like the EMS total replaces the individual consumers ' +
                    'rather than adding to them.'
                  }
                >
                  {option.scenarios}×
                </span>
              )}
              <button
                className="ml-auto shrink-0 px-1.5 rounded bg-blue-50 text-blue-700 hover:bg-blue-100"
                title={`Weight ${option.connection.weight} · ${option.connection.tags.join(', ')}`}
                onClick={() =>
                  addDynamicInput(
                    node.id,
                    buildExplicitDynamicInput(node, option),
                    option.sourceNodeId,
                  )
                }
              >
                Add
              </button>
            </div>
          ))}
        </div>
      )}

      {/* ── Outputs ────────────────────────────────────────────────────── */}
      {(outputs.length > 0 || outputOptions.length > 0) && (
        <div className="mt-2">
          <p className="text-[10px] text-gray-400">Output channels</p>
          {outputs.map((port) => (
            <div key={port.field_name} className="flex items-center gap-1 text-[11px] leading-5">
              <span className="text-gray-400">←</span>
              <span className="truncate italic" title={port.field_name}>
                {port.source_output_name}
              </span>
              <button
                className="ml-auto shrink-0 text-gray-300 hover:text-red-500 px-1"
                title="Remove this output and its connection"
                onClick={() => removeDynamicOutput(node.id, port.field_name)}
              >
                ✕
              </button>
            </div>
          ))}

          {/* Most-used first (the generator sorts them), and only a few: an EMS next to a
              battery matches half a dozen mined channels, most of them one-off variants from
              a single old scenario. */}
          {outputOptions.slice(0, 3).map((option) => {
            const target = option.targets[0]
            const { declaration } = option
            return (
              <div
                key={`${declaration.source_output_name}-${declaration.source_weight}`}
                className="flex items-center gap-1 text-[11px] leading-5"
              >
                <span className="text-gray-300">+</span>
                <span
                  className="truncate text-gray-500"
                  title={
                    `${declaration.source_output_name} — weight ${declaration.source_weight}, ` +
                    `tags ${declaration.source_tags.join(', ')}. Used in ` +
                    `${declaration.scenarios} shipped scenario(s).`
                  }
                >
                  {declaration.source_output_name} → {target.nodeName}.{target.inputName}
                </span>
                <button
                  className="ml-auto shrink-0 px-1.5 rounded bg-blue-50 text-blue-700 hover:bg-blue-100"
                  title="Add this output channel and connect it"
                  onClick={() =>
                    addDynamicOutput(node.id, buildDynamicOutput(node, declaration), {
                      nodeId: target.nodeId,
                      inputName: target.inputName,
                    })
                  }
                >
                  Add
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
