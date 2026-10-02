# Spec: assemblies — composing an energy system from tested fragments

**Status:** design, first approach; not implemented. **Date:** 2026-10-02 · **Owner:** Noah Pflugradt. The name
ASSEMBLY (file `*.assembly.yaml`) and the frame of §2–§5 were decided in discussion on 2026-10-02 (owner: "needs some
more refinement, but it's a good first approach"). Everything §14 lists is open and is written as a decision with a
recommendation, not as a promise. Related: renovisorissues #83, #85, #77; epic hisim-lt0b; beads hisim-fxix, hisim-9uoo, hisim-epc.28,
hisim-9b0m (§15).

All `file:line` citations are against `origin/main` at 0921f9bd. The hydronic coupling spec is cited from branch
`docs/hydronic-coupling` (PR #878) by section.

## 1. Problem

### 1.1 Structure today comes from recorded twins and two switches

An energy-system file has components, groups (one on/off flag over a set of components) and variants (one selected
option out of several complete worlds) (`hisim/energy_system/model.py:365-407`). Expansion drops a disabled group's
components and every input item pointing at them (`hisim/energy_system/groups.py:11-22`). A group is "emphatically not
a namespace" and groups do not nest (`model.py:286-288`); a variant option holds components and nothing else
(`model.py:310-314`). Names are one global identifier space, and the grammar refuses wildcards and path syntax as "the
vocabulary of a future preprocessor" (`hisim/energy_system/names.py:1-13`, `:31-34`).

The RenoVisor's base files are ten recorded grouped twins, eight chosen by heat generator and two with solar thermal
(`hisim/renovisor/translate.py:510-557`). Its rule R4 allows configuration values, constructor arguments and
variant/group selections only; authoring an `inputs` item, a `sizing_sources` block or a component entry is forbidden
(`translate.py:20-24`), and `DiffRule.check` enforces it on every translation (`translate.py:386-483`). "No
photovoltaics" is therefore a zero-power pin on the array every twin carries (`translate.py:2002-2035`), and an EMS
without a battery cannot be expressed at all (`hisim/renovisor/not_implemented_yet.yaml:77`).

### 1.2 Why that does not scale

- **N arrays, N batteries (renovisorissues #83).** A house may have several PV systems, each with its own size,
  orientation and installation year, and a measure replaces a named one or adds one; HiSim must support at least four.
  Owner: batteries the same way. As fixed slots this grows every one of the ten twins by four arrays and their wiring,
  and the slots that are not used are zero-power pins.
- **Seven hot-water types (renovisorissues #85).** `from_space_heating_combi`, `from_space_heating_cylinder`,
  `storage_water_heater[electricity|natural_gas|lpg|oil]`, `instantaneous_water_heater[carrier]`,
  `heat_pump_water_heater`, `district_heating`, `none`; a `hot_water_system` measure replaces one with another. Today
  only "together with the heating system" runs (`not_implemented_yet.yaml:161-170`, `:309-316`). As variants that is
  ~7 × 10 = ~70 branches, each recorded and probed.
- **Guarantees reach only the probes.** A grouped file is proven against its probe columns and no further; the README
  says so ("a grouped file's guarantees reach exactly as far as its probe list", `energy_systems/README.md:209`).
  Combining a new DHW type with N arrays and a battery on ten generators is untested by construction.
- **Hydronic tanks.** The hydronic redesign makes tanks in series, coils and valves composable (hydronic spec §2,
  §3.5); each such topology as a variant of each twin multiplies again.

### 1.3 Where the rigidity really sits

The aggregators are already dynamic. The electricity meter sums every input on its production channel
(`hisim/components/electricity_meter.py:504`, `:520`); the EMS ranks every controlled input by its source weight and
pairs each with a dispatch output found by component type and the same weight
(`hisim/components/controller_l2_energy_management_system.py:595-645`, the pairing `:617-625`, the dispatch
`:790-821`). A second dispatch port of the same component type and weight is refused
(`hisim/dynamic_component.py:236-275`), so two batteries need two weights. Feed resolution is deterministic: feeds are
sorted by weight, participant and output (`hisim/energy_system/feed_resolution.py:10-14`).

The rigidity is in the files. A recorded twin writes every feed explicitly with its output, tags and weight
(`energy_systems/household_heatpump_building_sizer.grouped.energy_system.yaml:96-171`), and the format has no way to
say "every output tagged PV": a bare name expands only the aggregator's declared defaults for one named source
(`feed_resolution.py:168-183`). Adding an array means editing the meter and EMS entries of both variant options, which
R4 forbids the translator to do. Sizing has the same shape: a bare fact binds only when exactly one component provides
it (`hisim/config/engine.py:13-25`, `:329-335`); the battery's laws read `pv_peak_power_in_watt`
(`hisim/components/advanced_battery_bslib.py:58`, `:65`), which every array contributes
(`hisim/components/generic_pv_system/config.py:176-186`), so a second array makes the read ambiguous, and the
many-provider aggregation is declared but raises when evaluated (`hisim/config/laws.py:55-66`, `:228-241`).

## 2. Concepts

### 2.1 Assembly

An **assembly** is a fragment of an energy system in a file of its own, `<family>/<name>.assembly.yaml` under
`energy_systems/assemblies/` (e.g. `dhw/heat_pump_water_heater`, `pv/array`, `storage/battery`,
`supply/gas_connection`). It holds:

- **components** — ordinary entries (`class`, `preset`/`constructor`, `config`, `inputs`, `sizing_sources`) whose
  names are local to the assembly and whose references name only other members;
- **parameters** — typed, named values the importing file sets (`volume_in_liter`, `energy_carrier`), each with a
  type, an optional default (`AUTO` allowed for a sized field) and an optional allowed-value list; a member's config
  takes one with the reserved form `{$param: volume_in_liter}` (`$` is outside the identifier grammar,
  `hisim/config/names.py:43`, so no config field can collide with it);
- **internal variants** selected by a parameter (`selected_by: energy_carrier`), for carriers one component cannot
  cover (§5.3);
- an **interface** — the ports through which it needs something from the surrounding system and provides something to
  it (§3). A member never names a component outside its assembly; everything that crosses the boundary crosses through
  a port.

An assembly does not import other assemblies in this first approach (§14, D7).

### 2.2 Import and instances

A system file gains one top-level key, `imports`, between `components` and `groups`. Each import key names a slot; its
value names the assembly and either one parameter set or several named **instances**:

```yaml
imports:
  dhw:     {assembly: dhw/heat_pump_water_heater, parameters: {volume_in_liter: 200}}
  pv:      {assembly: pv/array, instances: {east: {azimuth_in_degree: 90}, west: {azimuth_in_degree: 270}}}
  battery: {assembly: storage/battery, instances: {main: {capacity_in_kwh: 10}}}
```

An import may carry `bind:` to name the partner of a port explicitly where the default rule (§3.3) would be ambiguous,
and `none` for an optional port it declines.

### 2.3 Expansion

Pre-processing **expands** every import into ordinary components of one flat file, before groups are expanded and
before anything is validated, configured or built — one more pure stage in front of `expand_groups` in
`EnergySystemExecutor.build` (`hisim/energy_system/executor.py:264-300`, the call `:275`). Like group expansion it
builds a new file and never mutates its input, and it is idempotent (`groups.py:24-28`). For each import (and
instance) it:

1. reads the assembly, checks the parameters (types, allowed values, required, no unknown keys);
2. evaluates every port's `required_when` and selects the internal variants;
3. substitutes the parameters and renames every member to its expanded name (§2.4), rewriting the members' internal
   references;
4. binds every active port (§3.3) and lowers it to existing items — bare-name default inputs, explicit wires,
   aggregator feeds, `sizing_sources` lines, config values (§3.2);
5. resolves the tag selectors of every aggregator against the published ports (§4);
6. writes an **import record** (data, like `ExpansionRecord`, `groups.py:133-151`): per import the assembly path and
   content hash, the parameters as given and as defaulted, the selected internal variants, the members' expanded
   names, every port with its state (bound to whom / inactive / fallback applied) and every derived weight.

Everything downstream — wiring, sizing, simulator, realized record, energy balance, economics — sees ordinary
components, exactly as nothing downstream knows that a variant existed (`groups.py:30-36`).

### 2.4 Expanded names

Proposal: `<import>__<instance>__<Member>` for an import with instances and `<import>__<Member>` for one without, e.g.
`pv__east__PVSystem`, `battery__main__Battery`, `dhw__Tank`.

- It stays inside the identifier grammar (`hisim/config/names.py:43`), so `NameRules`, references (`source.Output`,
  `names.py:82-117`), result columns, CSV headers and the economics subjects need no change. A bracket form such as
  `pv[east].PVSystem` would put the reference separator `.` inside a name and need a grammar change across every
  consumer of names.
- The double underscore is never parsed back, like `ComponentID.key` (`hisim/config/base.py:187-195`); the parts
  are read from the import record. `__` is refused in authored component, import and instance names, and an instance
  name may not start or end with `_`, so expanded names cannot collide.
- It is **stable**: it is built only from three authored keys, never from an index or an order, so the same file names
  the same components in every run, and the RenoVisor keeps one instance key per system id across all stages of a plan
  (§10.4). The expanded name is the component name in results, the economics subject, and what a `replaces` measure
  names through its system id.

## 3. The interface and the port model

### 3.1 Requirement states

Every port is in one of three states, decided per import (or instance) before binding:

- **required** — must be bound, or the file is refused at load time;
- **conditionally required** — `required_when` names parameter values; when they hold the port is required, otherwise
  it is **inactive**: never bound, never lowered, and an explicit `bind:` to it is refused. A house with gas and
  electricity wires an electric storage water heater to electricity only, although a gas provider exists;
- **optional with a declared fallback** — bound when exactly one partner exists (or `bind:` names one), otherwise the
  declared fallback applies: a config patch on named members, recorded in the import record. EMS control of an
  immersion heater: bound, the heater charges on PV surplus; `none` or no EMS, it runs on its own thermostat.

Rejected (discussion 2026-10-02): choosing the carrier by optional connections alone. Both providers usually exist,
and a needed connection that is missing would pass silently: fuel burned and not metered, and the balance check
accepts a one-sided booking toward "nobody the wiring names" (`hisim/energy_port.py:21-25`).

`required_when` is structured, not an expression: a mapping from parameter to the list of values for which the port is
required, all keys conjunctive (`required_when: {energy_carrier: [natural_gas, lpg, oil]}`). `active_when`, same
shape, makes an optional or a published port inactive outside its values.

### 3.2 What a port carries

| Kind | Need side | Provide side | Lowered to |
|---|---|---|---|
| `partner` | a data partner by class (weather, occupancy, building, a controller) and the members it feeds | any component of that class | a bare-name default-inputs item (or explicit wires) on each named member |
| `published` | — | one output with its carrier, flow (production / consumption / storage), component type and, if controllable, base weight and dispatch target | an aggregator feed on whichever aggregator selects it (§4) |
| `carrier` | an energy carrier (`lt.EnergyBalanceCarrier`, `hisim/loadtypes.py:278-285`) and the published outputs that draw it | a provider of that carrier (§5) | the provider's selector picks the outputs up; the binding is the check that exactly one provider does |
| `circuit` | one end of a hydronic circuit | the other end | explicit wires of mass flow, supply and return temperature (§11.1) |
| `signal` | a control input | a control output | one explicit wire |
| `fact` | a sizing fact with cardinality one or many | a member that contributes it | a `sizing_sources` line, scalar or list (§6) |

A port adds no runtime concept: default connections (`hisim/component.py:338`, `:487`), declared default feeds
(`feed_resolution.py:112-130`) and `SIZING_CONTRIBUTIONS` remain the implementation; the port says which of them cross
the assembly boundary and with whom.

### 3.3 Binding and the load-time checks

The binding rule mirrors the sizing engine's (`engine.py:13-25`): a port binds to the one partner in the expanded
system that matches it; `bind:` decides every other case. Each of these fails at load time, naming the import, the
instance, the port and every candidate, with a paste-ready `bind:` line:

- a required (or conditionally required and active) port with no partner;
- a port with several partners and no `bind:`;
- `bind:` on an inactive port, or `none` on a required one;
- a `bind:` naming a component of the wrong class, carrier or circuit kind;
- a `published` carrier output that no aggregator selects (unmetered), or that two select (metered twice; today's
  `DUPLICATE_FEED`, `hisim/energy_system/aggregator_ports.py:39-67`, catches only the second);
- a contract check, run once per assembly in tests and again in the class-validation stage after expansion: every
  `partner` class has a declared default connection into the named member, every published output exists on the member's class with the declared tags, every
  provided fact is in the member class's `SIZING_CONTRIBUTIONS`.

## 4. Tag-selector inputs for aggregators

### 4.1 Syntax

An aggregator's `inputs` gains a fourth item shape next to bare name, explicit wire and feed (`model.py:106-211`):

```yaml
inputs:
  - select: {carrier: electricity, flow: production}          # every array, every CHP
    feed: {tags: [ELECTRICITY_PRODUCTION], weight: 999}
  - select: {carrier: electricity, flow: [consumption, storage], controllable: true}
    feed: {tags: [ELECTRICITY_CONSUMPTION_EMS_CONTROLLED], weight: from_port, dispatch: from_port}
  - select: {carrier: electricity, flow: consumption, controllable: false}
    feed: {tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLED], weight: 999}
```

`select` matches published ports by carrier, flow, component type and controllability; `feed` says what the aggregator
makes of each match, defaulting to the port's own tags. `weight: from_port` takes the port's base weight and derives
the per-instance one (§4.3); `dispatch: from_port` takes its dispatch target.

### 4.2 Semantics

- **What matches.** Published ports only — ports in an assembly's `provides`, or in a `provides` block a top-level
  entry may carry (§14, D2). Runtime output tags (`postprocessing_flag`) are not matched: they exist only after
  construction and carry descriptive tags no aggregator should follow (the battery's output is tagged
  `CHARGE_DISCHARGE`/`BATTERY`, `hisim/components/advanced_battery_bslib.py:207`, while its EMS feed is
  `ELECTRICITY_CONSUMPTION_EMS_CONTROLLED`). The wiring stage checks every published port against the output it names
  (exists, unit, load type) and fails on a mismatch.
- **When.** In the expansion, statically; each match becomes an ordinary `AggregatorFeed` (`model.py:181-209`). The
  expanded file and the realized record therefore carry explicit feeds, and the channel matching
  (`hisim/energy_system/channel_matching.py:3-15`) and every existing check run unchanged.
- **Order.** Matches are emitted in import order, then instance order, then port order, as written; the feed
  resolution sorts again by weight, participant and output (`feed_resolution.py:10-14`). Order never depends on a
  dict, a file system or a hash.
- **Coexistence.** Explicit feeds stay legal. A port matched by a selector and also fed explicitly to the same
  aggregator is refused (one source of truth per port), as is a selector matching nothing when it is declared
  `required: true` (an EMS with nothing to control).
- **Migration.** The recorded twins keep their explicit feeds; recording is untouched (§8). Composed files carry
  selectors, and the gate of §13 proves that their expansion writes exactly the twin's feeds. No authored file is
  rewritten automatically.

### 4.3 Dispatch weights per instance

The EMS pairs inputs and dispatch outputs by component type and weight and processes them in weight order
(`controller_l2_energy_management_system.py:606-625`); two ports of one type at one weight are refused
(`dynamic_component.py:236-275`). A controllable port therefore declares a **base weight** (battery 6, heat pump space
heating 2 and hot water 3, as the class defaults declare them, `controller_l2_energy_management_system.py:444`,
`:466`, `:555`), and the selector derives the weight of the k-th instance of that component type in import/instance
order as `base + k` (k = 0, 1, …). The first instance keeps today's weight, so a one-battery file expands
byte-identically to today's twin under the rename map (§13). An instance may pin `priority:` to reorder; a derived weight that reaches 999 or
collides with another port of the same component type is refused at load time.

## 5. Carriers and providers

### 5.1 Supply assemblies

A **provider** of a carrier is a supply assembly whose bus member selects the carrier's published ports:
`supply/electricity_grid` (meter, and the EMS when its parameter `energy_management` is set), `supply/gas_connection`
(`GasMeter`), `supply/lpg_tank`, `supply/oil_tank` (`FuelMeter`), `supply/district_heating_substation`. A consumer's
`carrier` port binds to the unique provider of that carrier — the same component the energy-balance fuel port finds as
its peer, because the meter reads the consumer's fuel output (`hisim/components/generic_boiler.py:450`, `:501`;
`energy_port.py:12-17`). Making the EMS a parameter of the electricity supply rather than a member of the battery also
expresses the EMS without a battery that the twins lack (`not_implemented_yet.yaml:77`); the battery assembly requires
the `ems` signal port.

The provider states its carrier; the meter's carrier is pinned from that parameter. Today the gas meter copies its
carrier from "the generator beside it" through the `energy_carrier` fact (`hisim/components/gas_meter.py:63-68`,
`:82`), which a second burner on the same meter (a gas storage water heater beside a gas boiler) makes an ambiguous
read. With providers, the carrier of every bound consumer is checked against the provider's at load time instead.

LPG is not a carrier yet: `LoadTypes` has `GAS` and `OIL` (`hisim/loadtypes.py:122`, `:129`), the balance carriers
have no LPG (`loadtypes.py:278-285`, `energy_port.py:101-113`), and LPG houses run the gas twin (`translate.py:513`),
which is how renovisorissues #77 bills them a gas standing charge. `supply/lpg_tank` needs that carrier first (§13, PR
6).

### 5.2 A missing provider fails; the RenoVisor adds it explicitly

The format never adds structure implicitly: an active `carrier` port without a provider is a load error. The RenoVisor
translator adds a provider assembly explicitly when the house or a measure needs it (a heat-pump house adding a gas
instantaneous heater) and reports it as one mapping-report line, with its economics: the connection as a subject of
its own (connection fee as investment), the carrier's standing charge from the tariff
(`hisim/economics/catalog_entries.py:291`), and for a provider that the plan leaves without consumers the removal of
the import and the carrier's grid exit fee (`catalog_entries.py:292`). A provider with no bound consumer is refused by
the format (an idle gas connection is a decision the translator states, not a leftover).

### 5.3 Carrier-dependent assemblies

Three patterns, chosen per assembly:

1. **Pure parameter** — one component covers every carrier. `GenericBoiler` takes `energy_carrier`
   (`generic_boiler.py:198`), and its fuel ports follow it (`EnergyPort.carrier_of_fuel`, `energy_port.py:116-124`,
   used at `generic_boiler.py:420`).
2. **Parameter-selected internal variants** — the carriers need different components that share the rest (tank,
   controller, ports): an immersion heater versus a burner.
3. **Separate assemblies** — only where variants share almost nothing (a heat-pump water heater is not a storage water
   heater with another carrier).

The storage water heater is pattern 2 (classes marked † do not exist yet: the immersion heater is hisim-epc.21; the
tank and the burner's DHW-only use follow the hydronic spec):

```yaml
# energy_systems/assemblies/dhw/storage_water_heater.assembly.yaml
schema_version: 3
kind: assembly
name: Storage water heater
parameters:
  energy_carrier: {type: enum, values: [electricity, natural_gas, lpg, oil]}
  volume_in_liter: {type: float, default: AUTO}            # AUTO: the class law, from the apartments (§6)
components:
  Tank:
    class: hisim.components.simple_water_storage.SimpleDHWStorage
    preset: standard
    config: {volume_heating_water_storage_in_liter: {$param: volume_in_liter}}
variants:
  heater:
    selected_by: energy_carrier
    options:
      immersion:
        when: [electricity]
        components:
          Heater: {class: hisim.components.immersion_heater.ImmersionHeater, preset: standard}   # †
      burner:
        when: [natural_gas, lpg, oil]
        components:
          Heater:
            class: hisim.components.generic_boiler.GenericBoiler
            preset: condensing_gas
            config: {energy_carrier: {$param: energy_carrier}}   # carrier name -> LoadTypes in the loader
interface:
  needs:
    occupancy: {kind: partner, class: hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnector,
                into: [Tank]}
    electricity: {kind: carrier, carrier: electricity, outputs: [heater_electricity],
                  required_when: {energy_carrier: [electricity]}}
    fuel: {kind: carrier, carrier: {$param: energy_carrier}, outputs: [heater_fuel],
           required_when: {energy_carrier: [natural_gas, lpg, oil]}}
    ems: {kind: signal, into: Heater.SetPointInput, from_type: ELECTRIC_HEATING_DHW,          # † input name
          active_when: {energy_carrier: [electricity]}, optional: {fallback: {Heater: {control: THERMOSTAT}}}}
  provides:
    heater_electricity: {kind: published, output: Heater.ElectricityInput, carrier: electricity,
                         flow: consumption, component_type: ELECTRIC_HEATING_DHW,
                         controllable: {base_weight: 3, dispatch: {target_input: SetPointInput}},
                         active_when: {energy_carrier: [electricity]}}
    heater_fuel: {kind: published, output: Heater.EnergyDemandDhw, carrier: {$param: energy_carrier},
                  flow: consumption, active_when: {energy_carrier: [natural_gas, lpg, oil]}}
```

The `ems` port shows both refinements at once: inactive for a burner, optional with a thermostat fallback for an
immersion heater.

## 6. Sizing across assemblies

The resolver sees the expanded system: every member is an ordinary config under its expanded name, so the engine's
providers and binding rule apply unchanged (`engine.py:193-225`, `:311-348`). Three cases need the expansion's help:

- **A fact read over all instances.** The battery is sized to all arrays. Its laws become a sum over a
  many-cardinality read, which needs the `Many` aggregation implemented with an explicit `Sum` (`laws.py:228-241`
  raise today). The battery's `fact` port declares `pv_peak_power_in_watt` with cardinality many and "every provider";
  the expansion writes the list `sizing_sources: {pv_peak_power_in_watt: [pv__east__PVSystem, pv__west__PVSystem, …]}`
  in instance order, which the engine already reads in written order (`engine.py:387-399`).
- **A fact an import makes ambiguous.** The DHW tank's volume law reads `number_of_apartments`
  (`hisim/components/simple_water_storage.py:252`) and binds to the building, unambiguously. A burner inside a DHW
  assembly, however, contributes `maximal_thermal_power_in_watt` as the space-heating boiler does, and the buffer's
  volume law reads that fact (`simple_water_storage.py:160-162`): the import would make a skeleton read ambiguous.
  Rule: an assembly declares which contributions it **exports**; for every reader outside it whose read an unexported
  contribution would make ambiguous, the expansion writes the explicit `sizing_sources` line to the reader's previous
  provider; a read inside an assembly binds to its own member first, then to its `fact` port. Ambiguity left after
  that is the engine's ordinary error with its paste-ready snippet.
- **Meter fuel constants.** The fuel meter copies heating value and density from "the generator", which derives them
  from carrier and boiler type (`hisim/components/fuel_meter.py:61-69`, `generic_boiler.py:217-259`). Two oil burners
  of different boiler types on one meter have no single value; until the meter converts per participant, the provider
  refuses consumers whose constants differ (§14, D10).

## 7. Groups and variants after assemblies

- **An import slot replaces an add-on group.** No array is no `pv` import (or an empty `instances:`), not a zero-power
  pin; no battery is no `battery` import.
- **An assembly choice replaces a structural variant.** The DHW type is the `assembly:` of the `dhw` import;
  `electricity_management` becomes the `energy_management` parameter of `supply/electricity_grid`.
- **What remains.** Groups and variants stay in the format, because the grouped twins are generated from grouping
  decisions (`energy_systems/README.md:162-214`) and keep their guarantees. Imports are top-level only: no import
  inside a group or a variant option, and no group or variant inside an assembly except the parameter-selected
  internal variants of §5.3, which are resolved before anything else and leave no trace.

## 8. Recording and the twins

Recording observes one constructed system, and "one run cannot be asked about intent"
(`energy_systems/README.md:97-101`); "this battery is an instance of `storage/battery`" is intent. Options:

- **A — assemblies as a decision layer over recordings.** A committed `<setup>.assemblies.yaml` assigns recorded
  components to assembly members, like `grouping.yaml` assigns rows; the second pass proves the composition against
  the twin.
- **B — twins stay flat; assemblies are a new authored layer.** Twins, probes and grouped files stay as they are.
  Skeletons and the composed base files (skeleton plus the imports reproducing the twin) are written by hand; a test
  expands each composed file and compares it with its twin under the rename map of the import record.

Recommendation: **B**. It needs no change to recording, the equality test is the same check as the migration gate, and
A can follow if hand-maintaining composed files proves costly. Determinism: the expansion is pure and its output is
canonical through the format's own emitter; the `energy-system-freshness` workflow gains a step that expands every
composed file and every assembly's isolation harness and fails on any byte difference, as
`scripts/record_all_setups.py --check` does for twins (`energy_systems/README.md:149-151`).

## 9. Versioning and provenance

Assemblies live in the repository and are imported by path. The realized record is written from the expanded file —
flat, every preset expanded, every sized value a number, every feed explicit (`hisim/energy_system/record.py:9-15`) —
so a re-run never needs an assembly again; provenance is for the reader. The `metadata` block
(`hisim/energy_system/metadata.py:1-18`) gains `imports:` with, per import and instance, the assembly path, the sha256
of its canonical text, the parameters as resolved, the internal variant selections, the port states and the members'
expanded names. The RenoVisor's `result.json` provenance carries the same hashes. Whether an import may pin a version
is §14, D4.

## 10. The RenoVisor on assemblies

### 10.1 Shape of a translated file

`skeleton(generator) + dhw import by type + pv instances + battery instances + supply imports`. A skeleton is an
ordinary energy-system file per heat generator (`energy_systems/skeletons/<generator>.energy_system.yaml`): weather,
occupancy, building, generator and controller, distribution and buffer — a twin minus PV, battery, EMS, meters and DHW
— whose participants carry `provides` blocks (§14, D2). The translator's rule changes from R4's "may only switch" to
**"may only pick tested assemblies and set their parameters"**: it writes the skeleton's configuration values as today
and an `imports` block (assembly, instance keys, parameters, `bind:`), nothing else. Every component, input item and
sizing line still comes from a reviewed skeleton or assembly. `DiffRule` (`translate.py:386-483`) is rewritten
accordingly: the skeleton compared as today, the `imports` block checked against the list of tested assemblies.

### 10.2 Coverage

Instead of ~70 recorded branches: (1) each assembly tested in isolation in a harness that provides its required ports;
(2) interface contract tests (§3.3, last bullet) for every assembly; (3) a combination matrix, each assembly (each
parameter-selected variant) against each skeleton, a one-day run with the energy-balance check, plus 1–4 arrays and
1–2 batteries on one skeleton. The capability probes (`hisim/renovisor/verify/`) gain probes per DHW type and per
array count; `not_implemented_yet.yaml` keeps listing what is not built.

### 10.3 Hot water (#85)

| Request type | Assembly | Parameters | Prerequisite |
|---|---|---|---|
| `from_space_heating_cylinder` | `dhw/indirect_cylinder` | volume | circuit port to the generator's DHW circuit (§11.1); today's structure |
| `from_space_heating_combi` | `dhw/combi` | — | generator DHW side without a vessel |
| `storage_water_heater` | `dhw/storage_water_heater` | `energy_carrier`, volume | immersion heater (hisim-epc.21); LPG carrier |
| `instantaneous_water_heater` | `dhw/instantaneous_water_heater` | `energy_carrier` | a flow heater component |
| `heat_pump_water_heater` | `dhw/heat_pump_water_heater` | volume | DHW heat pump (hisim-lenz) |
| `district_heating` | `dhw/district_heating` | — | substation DHW side; `supply/district_heating_substation` |
| `none` | no `dhw` import | — | reported: the occupancy's hot-water demand is not served |

A `hot_water_system` measure swaps the `dhw` import's assembly and parameters, adds a provider where the new type
needs a carrier the house lacks (§5.2), and its economics retire the old import's members as replaced subjects and buy
the new ones.

### 10.4 Photovoltaics and batteries (#83)

Kept from the superseded fixed-slot draft, mapped onto instances:

- **Ids and measures.** Each existing system has an id (`^[a-z0-9_]+$`, without `__`), which is its instance key;
  measures name ids, never indices. A legacy single object normalises to a one-element list with the id `pv_system`
  (`battery`). `replaces: <id>` replaces the named system (same key, new parameters); on the list shape a measure
  without `replaces` adds an instance under its `system_id`; the legacy object shape keeps today's replace. New
  problems `measure.replaces_unknown`, `measure.replaces_twice`, `measure.system_id_duplicate`; `MEASURE_DUPLICATE`
  (`hisim/renovisor/request.py:74`) is lifted for these.
- **Roof.** A share is a share of the whole roof; the sum over arrays is capped at 100 %, an added share-sized array
  is capped (approximated) and reported, a stated power and existing arrays never; roof faces
  (`house.building.roof.faces[]`) later. An added array without orientation is south with the roof shape's tilt,
  defaulted and reported, never cloned.
- **Economics.** Every instance is its own subject bound to its own register entry (`ExistingAsset.subject`,
  `own_register_entry`, `hisim/economics/facts.py:294`, `:561`; salvaged from PR #872): kept, replaced (the named one
  retired, `ReplacedSubjects` by bound subject) or added; the EMS is costed with the first battery. The Irish grant
  `IE_SEAI_SOLAR_PV` (TIERED_PER_UNIT on kWp, cap 1800 €) is per dwelling: kWp summed per stage, tiers and cap once,
  split pro rata by size (`aggregate_over_subjects_in_stage`); undetermined for an array added to a house that already
  has PV.
- **Stages and reporting.** Instance keys are stable across stages (append, never reorder); a plan binding one id to
  two instances is refused; `investment_overrides` gain `system_id`. One mapping-report line per instance leaf
  (`house.pv_systems[2].azimuth`); the capability walker descends into array items; probes for 1–4 arrays, replace,
  add and overfill; `not_implemented_yet` entries until the contract settles.

## 11. Interplay

### 11.1 Hydronic coupling (hisim-fxix, PR #878)

A `circuit` port is one end of one circuit (hydronic spec §3.1): its declaration names which member owns the pump, the
supply and the return, and the binding lowers to the explicit wires of `MassFlow<c>`, `SupplyTemperature<c>` and
`ReturnTemperature<c>`. The `HydronicPort` ends are then found from the wiring (spec §3.5, "the two ends come from the
wiring, never from a declared peer"), so an assembly needs nothing beyond the wires its port produced. The spec's
ambiguity rules hold one level up: a circuit port binds exactly one partner, and a split is a valve assembly with one
circuit per branch. The skeleton's dual-circuit generator (spec §3.3) provides a `dhw` circuit port;
`dhw/indirect_cylinder` requires it, `dhw/storage_water_heater` has none. The hydronic stages that rewire default
connections (spec §9.2) land before the DHW assemblies, so the first DHW assemblies are written against the new
circuits rather than migrated twice.

### 11.2 Energy balance (hisim-9uoo, #870/#871)

Members keep their classes' `EnergyPort`s; a carrier set by parameter reaches the port through the config
(`generic_boiler.py:420`). Providers are the fuel ports' peers, because the binding creates the meter's read of the
fuel output (`energy_port.py:12-17`). The check allows a one-sided booking toward a component without ports
(`energy_port.py:21-25`), so the format, not the balance, must refuse a missing provider (§5.2).

## 12. Worked examples

Each example shows the `imports` block the translator adds to a skeleton file (§10.1); the skeleton's components are
as today minus PV, battery, EMS, meters and DHW.

(a) Four arrays and two batteries on the heat-pump skeleton:

```yaml
imports:
  supply:  {assembly: supply/electricity_grid, parameters: {energy_management: optimize_own_consumption}}
  dhw:     {assembly: dhw/indirect_cylinder}
  pv:
    assembly: pv/array
    instances:
      east:  {azimuth_in_degree: 90,  tilt_in_degree: 35, power_in_watt: 3000}
      west:  {azimuth_in_degree: 270, tilt_in_degree: 35, power_in_watt: 3000}
      south: {azimuth_in_degree: 180, tilt_in_degree: 35, power_in_watt: 5000}
      flat:  {azimuth_in_degree: 180, tilt_in_degree: 10, power_in_watt: 2000}
  battery:
    assembly: storage/battery
    instances:
      garage: {capacity_in_kwh: AUTO}  # sized to all four arrays: 13 kWh
      cellar: {capacity_in_kwh: 5}
```

It expands to `pv__east__PVSystem` … `pv__flat__PVSystem`, `battery__garage__Battery` (EMS weight 6),
`battery__cellar__Battery` (weight 7), and `supply__EMS` with one production feed per array, two controlled battery
feeds, and the heat pump's two and the occupancy's controlled feeds from the skeleton's published ports. Two arrays of
equal tilt and azimuth would share one cached series, since the cache key holds neither power nor name
(`hisim/components/generic_pv_system/pv_system.py:516-547`).

(b) A gas-heated Irish house (condensing-gas skeleton, `IE.N.SFH…` archetype) with an electric immersion storage water
heater:

```yaml
imports:
  supply: {assembly: supply/electricity_grid}            # energy_management: none
  gas:    {assembly: supply/gas_connection}
  dhw:    {assembly: dhw/storage_water_heater, parameters: {energy_carrier: electricity, volume_in_liter: 120}}
```

`dhw.fuel` is inactive, so the gas connection is never offered to the heater although it exists; `dhw.electricity`
binds to `supply`; `dhw.ems` finds no EMS and the thermostat fallback applies, recorded in the import record. The
skeleton's boiler controller runs without hot water (`with_domestic_hot_water_preparation: false`, a config value the
translator already may write).

(c) The same house after a `hot_water_system` measure to a heat-pump water heater: only `dhw: {assembly:
dhw/heat_pump_water_heater, parameters: {volume_in_liter: 200}}` changes. The economics retire every member of the old
`dhw` import and buy every member of the new one; a subject is keyed by the expanded name and the assembly path, so
`dhw__Tank` of two assemblies are two subjects.

(d) An all-electric house (electric-heating skeleton) adding a gas instantaneous heater:

```yaml
imports:
  supply: {assembly: supply/electricity_grid}
  gas:    {assembly: supply/gas_connection}      # added by the translator, reported with its costs
  dhw:    {assembly: dhw/instantaneous_water_heater, parameters: {energy_carrier: natural_gas}}
```

Without `gas` the file is refused ("import 'dhw': port 'fuel' (natural_gas) is required and no provider of natural_gas
exists"). The translator adds it and reports why, with the connection fee and the standing charge.

## 13. Migration and staging

1. **Format, no behaviour change.** Assembly model, loader and schema; `imports`; expansion with parameters, internal
   variants, names, ports `partner`/`signal`/`fact`, import record, load-time checks; `__` refused in authored names.
   Tested on fixtures; no shipped file uses it.
2. **Tag selectors and published ports.** `select` items, `published` and `carrier` ports, derived weights, the
   unmetered and double-metered checks.
3. **Sizing.** `Many` with `Sum`; the battery laws over all arrays; fact exports (§6). The one-array case stays
   byte-identical.
4. **First assemblies.** `pv/array`, `storage/battery`, `supply/electricity_grid`, the heat-pump skeleton. Gate: the
   composed heat-pump file expands to the twin byte for byte under the import record's rename map, and a one-day run
   gives identical result columns under that map.
5. **The remaining base files, one per PR.** Gas, oil, pellets, wood chips, hydrogen, electric, district heating, the
   two solar-thermal ones, with `supply/gas_connection`, `supply/oil_tank`, `dhw/indirect_cylinder` (after the
   hydronic stage that rewires it). The RenoVisor switches each generator to its composed file in the PR whose gate
   passes; its outputs for every existing probe are identical.
6. **New structure.** #83 request contract and N instances; LPG carrier and `supply/lpg_tank` (#77); DHW assemblies as
   their components land (hisim-epc.21, hisim-lenz); #85 contract.

## 14. Open decisions

**D1 — Interface language.** (a) Implicit: an assembly's interface is whatever its members' default connections and
`SIZING_CONTRIBUTIONS` name outside it. (b) Explicit ports in the assembly file, lowered to the existing mechanisms
(§3). (c) Ports declared on the Python classes. (a) cannot express conditional or optional requirement and binds
silently; (c) puts system knowledge into classes and changes every class. Recommendation: **(b)**, with the contract
test tying each port to the class declarations.

**D2 — Tag-selector scope.** (a) Runtime output tags of every component, resolved after construction. (b) Published
ports of imports only. (c) Published ports of imports and of top-level entries that carry a `provides` block. (a)
matches descriptive tags and puts the resolution after construction, so the expanded file says nothing; (b) forces
every participant into an assembly before any selector can see it. Recommendation: **(c)**, resolved statically in the
expansion.

**D3 — Recording.** (a) A decision file over recordings, like grouping; (b) twins flat, skeletons and composed files
authored, equality test (§8). (a) needs a recorder pass and a workbook per setup and still cannot see intent; (b)
needs no recorder change but composed files are maintained by hand. Recommendation: **(b)**.

**D4 — Versioning.** (a) Import by path; the realized record carries content hashes. (b) Pinned versions
(`pv/array@2`) with a `version` field in each assembly and old versions kept. (c) Both. (b) keeps old files
reproducible from source but makes the repository carry every version; the realized record is self-contained anyway.
Recommendation: **(a)**; a changed assembly is reviewed with its combination matrix.

**D5 — Instance naming.** (a) `<import>__<instance>__<Member>`; (b) `pv[east].PVSystem` with a grammar change; (c) new
`ComponentID` fields (`assembly`, `instance`) with the key derived from them. (b) collides with the reference
separator; (c) is cleaner but reaches postprocessing. Recommendation: **(a)** now, (c) when `ComponentID` next
changes.

**D6 — Provider handling.** (a) A missing provider fails; the translator adds providers explicitly and reports them
(§5.2). (b) The expansion adds a default provider. (c) Providers implicit per carrier. (b) and (c) add structure
nobody wrote and hide #77-type billing errors. Recommendation: **(a)**, with an idle provider refused.

**D7 — Groups and variants.** (a) Keep both, imports top-level only, no nesting (§7). (b) Retire groups and variants
after migration. (c) Allow imports inside variants. Recommendation: **(a)**; revisit (b) once the RenoVisor no longer
reads grouped twins. Assemblies importing assemblies stay out of the first approach.

**D8 — Port refinement points.** (i) Optional ports bind automatically to a unique partner, or only when `bind:` names
one; automatic keeps files short, explicit makes adding an EMS change no other import's behaviour silently.
(ii) `required_when`/`active_when` as a structured mapping, or an expression grammar; the mapping is checkable against
the parameter types, an expression is more expressive. (iii) Fallback as a config patch on members, or as an internal
variant; the patch is smaller, the variant can change classes. (iv) Sizing facts as a port kind, or outside the
interface. Recommendation: automatic binding recorded in the import record, the structured mapping, the config patch,
and `fact` ports.

**D9 — Staging.** (a) The six PRs of §13, the RenoVisor per base file behind the equality gate. (b) A big-bang switch
of all ten. (c) Assemblies for new structure only, twins kept for the RenoVisor. (c) leaves two systems side by side;
(b) loses the per-generator proof. Recommendation: **(a)**.

**D10 — Sizing across assemblies.** (a) `Many`/`Sum` plus fact exports (§6). (b) Every cross-assembly fact passed as a
parameter by the importing file. (c) Sized in the translator. (b) and (c) move laws out of the classes.
Recommendation: **(a)**; the meter's per-participant fuel constants follow when a second burner on one meter is
needed.

**D11 — Dispatch weights.** (a) `base + k` per component type in instance order, `priority:` to override. (b) A global
priority list in the supply assembly. (c) Weights as a pair (class, rank) in the EMS. (c) changes the EMS and every
weight; (b) needs editing per instance. Recommendation: **(a)**.

## 15. Related

- renovisorissues **#83** (several PV systems and batteries), **#85** (seven hot-water types), **#77** (LPG houses
  billed a gas standing charge).
- Beads **hisim-fxix** (hydronic coupling), **hisim-9uoo** (energy balance), **hisim-epc.28** (PV adds),
  **hisim-9b0m** (storeys; the roof area bounds the arrays' share,
  `roadmap/renovisor/implementation/storeys_geometry.md`).
- PRs **#872** (closed; salvageable: own subject and register entry for an added unit, recorder fixes in
  `recording/matrix.py` and `regrouping.py`, commit eb2be815), **#878** (hydronic coupling spec).
- `roadmap/declarative_energy_systems/p2_file_format_requirements.md`, `grouping_overview.md`.
