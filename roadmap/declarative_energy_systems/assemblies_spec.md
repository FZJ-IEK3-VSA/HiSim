# Spec: assemblies — composing an energy system from tested fragments

**Status:** design, not implemented. **Date:** 2026-10-02, revised after the owner's review of 2026-10-03 · **Owner:**
Noah Pflugradt. The name ASSEMBLY (file `*.assembly.yaml`) and the frame of §2–§5 were decided in discussion on
2026-10-02; the owner's review of 2026-10-03 settled the points §14.1 lists, and §14.2 keeps one open, with a
recommendation. Related: renovisorissues #83, #85, #77; epic hisim-lt0b; beads hisim-fxix, hisim-9uoo, hisim-epc.28,
hisim-9b0m (§15).

All `file:line` citations are against `origin/main` at 0921f9bd. The hydronic coupling spec is cited from branch
`docs/hydronic-coupling` (PR #878) by section.

## 1. Problem

### 1.1 Structure today comes from recorded twins and two switches

An energy-system file has components, groups (one on/off flag over a set of components) and variants (one selected
option out of several complete worlds) (`hisim/energy_system/model.py:365-407`). Expansion drops a disabled group's
components and every input item pointing at them (`hisim/energy_system/groups.py:11-22`). A group is "emphatically not a
namespace" and groups do not nest (`model.py:286-288`); a variant option holds components and nothing else
(`model.py:310-314`). Names are one global identifier space, and the grammar refuses wildcards and path syntax as "the
vocabulary of a future preprocessor" (`hisim/energy_system/names.py:1-13`, `:31-34`).

The RenoVisor's base files are ten recorded grouped twins, eight chosen by heat generator and two with solar thermal
(`hisim/renovisor/translate.py:510-557`). Its rule R4 allows configuration values, constructor arguments and
variant/group selections only; authoring an `inputs` item, a `sizing_sources` block or a component entry is forbidden
(`translate.py:20-24`), and `DiffRule.check` enforces it on every translation (`translate.py:386-483`). "No
photovoltaics" is therefore a zero-power pin on the array every twin carries (`translate.py:2002-2035`), and an EMS
without a battery cannot be expressed at all (`hisim/renovisor/not_implemented_yet.yaml:77`).

### 1.2 Why that does not scale

- **N arrays, N batteries (renovisorissues #83).** Several PV systems, each with its own size, orientation and
  installation year; a measure replaces a named one or adds one; at least four, batteries the same way (owner).
- **Seven hot-water types (renovisorissues #85)**, of which only "together with the heating system" runs today
  (`not_implemented_yet.yaml:161-170`, `:309-316`): as variants ~7 × 10 = ~70 branches, each recorded and probed.
- **Guarantees reach only the probes** ("a grouped file's guarantees reach exactly as far as its probe list",
  `energy_systems/README.md:209`); a new DHW type with N arrays on ten generators is untested by construction.
- **Hydronic tanks.** Tanks in series, coils and valves become composable (hydronic spec §2, §3.5); each topology as a
  variant of each twin multiplies again.

### 1.3 Where the rigidity really sits

The meter and the EMS are already dynamic components. The electricity meter sums every input on its production channel
(`hisim/components/electricity_meter.py:504`, `:520`); the EMS ranks every controlled input by its source weight and
pairs each with a dispatch output found by component type and the same weight
(`hisim/components/controller_l2_energy_management_system.py:595-645`, the pairing `:617-625`, the dispatch `:790-821`).
A second dispatch port of the same component type and weight is refused (`hisim/dynamic_component.py:236-275`), so two
batteries need two weights. Feed resolution is deterministic: feeds are sorted by weight, participant and output
(`hisim/energy_system/feed_resolution.py:10-14`).

The rigidity is in the files. A recorded twin writes every feed explicitly with its output, tags and weight
(`energy_systems/household_heatpump_building_sizer.grouped.energy_system.yaml:96-171`), and the format has no way to say
"every output tagged PV": a bare name expands only the dynamic component's declared defaults for one named source
(`feed_resolution.py:168-183`). Adding an array means editing the meter and EMS entries of both variant options, which
R4 forbids the translator to do. Sizing has the same shape: a bare fact binds only when exactly one component provides
it (`hisim/config/engine.py:13-25`, `:329-335`); the battery's laws read `pv_peak_power_in_watt`
(`hisim/components/advanced_battery_bslib.py:58`, `:65`), which every array contributes
(`hisim/components/generic_pv_system/config.py:176-186`), so a second array makes the read ambiguous, and the
many-provider aggregation is declared but raises when evaluated (`hisim/config/laws.py:55-66`, `:228-241`).

### 1.4 Positioning

EnergyPlus and TRNSYS describe a system flat, every unit wired by number or name. Modelica is hierarchical with typed
connectors, but demanding to author and weak on economics. Assemblies give HiSim nesting, ports that lower to the
default connections its classes already declare, and a hermetic expansion to a flat file, on top of the balance check
(`hisim/energy_port.py:1-9`) and the economics layer. The asset is the validated library of country-specific assemblies
(an Irish immersion cylinder, a Dutch combi boiler), each tested in isolation and in combination (§10.2).

## 2. Concepts

### 2.1 Assembly

An **assembly** is a fragment of an energy system in a file of its own, `<family>/<name>.assembly.yaml` under
`energy_systems/assemblies/` (e.g. `heating/air_source_heat_pump`, `dhw/heat_pump_water_heater`, `pv/array`,
`storage/battery`, `control/ems_self_consumption`, `supply/gas_connection`). It holds:

- **components** — ordinary entries (`class`, `preset`/`constructor`, `config`, `inputs`, `sizing_sources`) whose names
  are local to the assembly and whose references name only other members;
- **imports** of inner assemblies (§2.5), **parameters** with unit, description and constraints (§2.6), **presets**
  (§2.7);
- **internal variants** selected by a parameter (`selected_by: energy_carrier`), for carriers one component cannot cover
  (§5.3);
- an **interface** — ports through which it needs something from the surrounding system and provides
  something to it (§3). A member never names a component outside its assembly; everything that crosses the boundary
  crosses through a port, in both directions and at every level of nesting.

### 2.2 Import and instances

A system file gains one top-level key, `imports`, between `components` and `groups`. Each import key names a slot; its
value names the assembly, optionally a preset, and either one parameter set or several named **instances**:

```yaml
imports:
  dhw:     {assembly: dhw/storage_water_heater, preset: ie_immersion_120l}
  pv:      {assembly: pv/array, instances: {east: {azimuth_in_degree: 90}, west: {azimuth_in_degree: 270}}}
  battery: {assembly: storage/battery, instances: {main: {capacity_in_kwh: 10}}}
```

Parameters given next to a preset override its values, as `config` overrides a component preset. An import, like a
site entry, carries the binding verbs `bind:`, `optional-bind:` and `none:` (§3.1, §3.3), and `observes:`/`actuates:`
for a reader or a controller (§4.3).

### 2.3 Expansion

Pre-processing **expands** every import into ordinary components of one flat file, before groups are expanded and
anything is validated, configured or built — one more pure stage in front of `expand_groups` in
`EnergySystemExecutor.build` (`hisim/energy_system/executor.py:264-300`, the call `:275`). Like group expansion it
builds a new file and never mutates its input, and it is idempotent (`groups.py:24-28`). It works **innermost first**
(inner imports are expanded and their internal ports bound before the assembly is offered to its importer); for each
import (and instance) it:

1. resolves the assembly (§9.3), applies the preset, checks the parameters (types, units, values, constraints);
2. evaluates every port's `required_when`/`active_when` and selects the internal variants;
3. substitutes the parameters — `{$param: <name>}`, and `{$switch: <selector>, <case>: <value>, …}`, the value of the
   case a parameter or an internal variant selects, its cases covering the selector's values exactly once — and gives
   every member its structured address (§2.4), rewriting internal references;
4. binds every active port (§3.3) and lowers it to existing items — bare-name default inputs, explicit wires, dynamic
   input feeds, `sizing_sources` lines, config values (§3.2) — and resolves the selectors (§4);
5. attaches a source map entry to everything it produced (§9.2) and writes an **import record** (data, like
   `ExpansionRecord`, `groups.py:133-151`): assembly path and content hash, preset, parameters as given and as
   defaulted, internal variants, the members' addresses and order paths, every port's state and partner, every
   derived weight, and the file's final evaluation sequence.

Everything downstream — wiring, sizing, simulator, realized record, energy balance, economics — sees ordinary
components, exactly as nothing downstream knows that a variant existed (`groups.py:30-36`).

**Evaluation order** (decided, owner, 2026-10-03, D23; resolves G14). `order:` is an optional integer on every top-level
component and import; an assembly's members follow their file position or an explicit relative `order:` (needed when a
variant's member sits between plain ones), an import's instances their written order, and an inner import's members nest
again. The flat sequence is the order paths compared element by element as integers (6 < 6.1 < 6.3.1 < 7, 9 < 11);
numbers need not be consecutive, a repeated number at one level is a load error, and a level numbers all its entries or
none. Without `order:` the sequence is today's, so flat files need nothing. The importer positions an assembly as a
whole, never inside it (§2.5). Import and realized record state the final sequence, in which the simulator adds the
components (`executor.py:343-344`): order is reviewed and pinned, since it moves a week's results by 0.1–7 % through
on/off decisions and forced convergence (`roadmap/convergence_order_findings.md`, beads hisim-4g9.23–.28); once a
canonical dependency order makes results order-independent, the numbers become harmless rather than wrong.

### 2.4 Addresses and expanded names

A member's identity is a **structured address**: the import path (import keys from outermost to innermost, each with its
instance key where it has one) and the member name. Decided (owner, 2026-10-03, D5): `ComponentID`
(`hisim/config/base.py:174`, fields `name`, `building`, `unit` at `:209-211`) gains a `path` of those keys, and its
`key` (`:242`), which today joins building, unit and name with `_`, derives the component name from it. The name is the
address's **serialization**: `<import>[-<instance>]-…-<Member>`, e.g. `pv-east-PVSystem`, `heating-HeatPump`,
`dhw-tank-Tank` (decided, owner, 2026-10-03). The prior art is Terraform, whose resources inside modules are addressed
`module.pv["east"].<type>.<name>` and serialized only for display.

- **The hyphen is the separator and nothing else may contain it.** Authored component, import and instance names keep
  the identifier grammar (`hisim/config/names.py:43`, no hyphen), so an expanded name cannot collide with an authored
  one or with another expansion; the grammar of the expanded file admits `-` in component names, which references
  (`source.Output`, `names.py:82-117`) parse unchanged since the separator is `.`. The string is display-only — unique,
  deterministic, valid as a directory name, CSV header and YAML key — and never parsed back, like `ComponentID.key`
  today (`base.py:187-195`), so a later change of separator or order is only a column rename with one golden re-bless.
- It is **stable**: built only from authored keys, never from an index or an order; the RenoVisor keeps one instance key
  per system id across all stages of a plan (§10.4).
- **KPI addresses.** A component KPI is keyed `"<name> (<source component>)"` (`name_of_source_component`,
  `hisim/component.py:658-659`), today only when two sources collide
  (`hisim/postprocessing/kpi_computation/kpi_preparation.py:1868`); the proposed `roadmap/kpi_address_spec.md` makes the
  qualifier unconditional (`KpiAddress.source`). With assemblies the source is the serialized address and `KpiAddress`
  gains the structured `path`, so a reader filters "every KPI of import `pv`" without splitting strings.
- **Results.** Result columns, economics subjects and the per-subject rows of `result.json` (`plan.by_subject[]`,
  `hisim/renovisor/costs.py:88-93`) carry the serialization, `result.json` the structured address beside it. The
  serialized form is a contract with the RenoVisor frontend, settled with them on renovisorissues (§14, D15).
- **Display names and labels.** Beside the address, the KPI JSON and `result.json` carry `display_name`, an English
  default: the member's `display:` template over the resolved parameters (`display: "PV array, {orientation}"`),
  rendered at expansion into `ComponentID.display_name`, else the component's `DisplayConfig.pretty_name`, else the
  member name; the assembly's name is not prepended, it is the separate field `assembly` (decided, owner, 2026-10-04);
  site components use their `DisplayConfig` — and `label`, the
  request's own name for the system, verbatim and optional, which wins over `display_name`. HiSim ships no
  translations: the frontend translates by the stable fields (assembly, member). See `roadmap/kpi_address_spec.md`.

### 2.5 Nesting

**Not in v1 (D26).** 
Decided (owner, 2026-10-03, D7): an assembly may import assemblies from the start. The expansion refuses a cycle and a
depth beyond 4, an implementation safeguard rather than a modelling limit. Naming composes along the path
(`heating-dhw-Tank`). Every port of an inner import is in exactly one state inside its importer, and the expansion
refuses an inner port left in none:

- **internal** — bound inside the assembly to another inner import (`bind:` on the inner import) or to a member (an
  `internal:` entry); invisible from outside;
- **re-exported** — an outer port declared `from: <inner import>.<port>`, bound by the outer importer as its own; when
  the inner import has instances the reference names one, `from: '<inner>[<instance>].<port>'` (quoted inside a flow
  mapping), and a re-export that names no instance of a multi-instance inner import is refused (2026-10-04, from the
  review of #886: aggregating one outer port over several instances had no defined meaning);
- **inactive** or **declined** (`none:`), as at top level (§3.1).

A member of the outer assembly never names an inner member; it reaches an inner import only through that import's ports,
so an inner assembly stays a black box and keeps its isolation test. Worked example — a heat-pump water heater built
from a tank, a small heat pump and a controller:

```yaml
# energy_systems/assemblies/dhw/heat_pump_water_heater.assembly.yaml
kind: assembly
parameters:
  volume_in_liter: {type: float, unit: LITER, default: 200, description: Usable tank volume.}
imports:
  tank:      {assembly: storage/dhw_tank, parameters: {volume_in_liter: {$param: volume_in_liter}}}
  generator: {assembly: generator/dhw_heat_pump, bind: {charge: tank.charge}}     # internal circuit
components:
  Controller: {class: hisim.components.dhw_heat_pump.DhwHeatPumpController, preset: standard}   # † hisim-lenz
interface:
  needs:
    hot_water_demand: {from: tank.hot_water_demand}       # re-exported: the occupancy binds here
    electricity:      {from: generator.electricity}       # re-exported carrier need
  provides:
    heat_pump_electricity: {from: generator.electricity_use}
  internal:                                              # receiver's default connections from the sender's class
    tank_temperature: {bind: [tank.temperature, Controller]}
    on_off:           {bind: [Controller, generator.on_off]}
```

It expands to `dhw-tank-Tank`, `dhw-generator-HeatPump` and `dhw-Controller`.

**Explicit assemblies, no optional inner imports** (decided, owner, 2026-10-03). A combination is its own assembly:
`heating/hybrid_heat_pump` imports `generator/air_source_heat_pump` and `generator/gas_boiler` and adds the hybrid
controller; `heating/heat_pump_with_electric_backup` is another file. Nesting is for reuse, not optionality: an inner
import is never optional (that would be a group inside an assembly, D7). Control inside an assembly is hardcoded by
its author and proven by its isolation test (§9.3); the format encodes no control theory. Smart heating control is a
parameter of the heating assembly, `control: smart | traditional` (a preset or internal variant), not an import.

### 2.6 Parameters: units, documentation, constraints

A parameter declares `type`, a `default` (`AUTO` allowed for a sized field, `none` for no value; the library test
requires one, §9.4), optional `values`, and now `unit`, `description` and, for a numeric parameter, `range: {min, max}`
(decided, owner, 2026-10-03, D24: the range is the
parameter box the assembly is tested over, §9.4; a numeric parameter without one fails the library's tests). A unit is a member name of `lt.Units` (`hisim/loadtypes.py:140`; `WATT`, `LITER`, `CELSIUS`,
`KG_PER_SEC` at `:149`, `:174`, `:188`, `:185`), the vocabulary every I/O declaration uses; the typed quantities of
`hisim/units.py` (`Watt` `:217`, `Liter` `:280`, `Celsius` `:322`) are code-level and map one to one where both exist. A
parameter without a description fails the library's tests. The loader checks a stated unit against every config field
the parameter feeds through `{$param: …}` (`$` is outside the identifier grammar, `hisim/config/names.py:43`, so no
field collides). Fields encode their unit in the name today — in `hisim/components` the suffixes `_in_celsius`,
`_in_watt`, `_in_kwh`, `_in_m2`, `_in_years`, `_in_seconds`, `_in_liter`, `_in_kelvin` appear 1230, 742, 238, 202, 87,
76, 45 and 14 times — but the suffix is documentation, not the source (D16, decided (b)): every config field an
assembly parameter feeds declares its unit, `sized_field(..., unit=lt.Units.LITER)` (`sized_field` has no unit argument
yet, `hisim/config/sizing.py:194-198`) or `field(metadata={"unit": …})`, and a fed field without a declared unit is
refused. A mismatch is a load error, never a conversion. Only a numeric parameter carries a unit (and a `range`), and a
numeric parameter that feeds a nested value or a constructor argument, neither of which declares a unit, is refused.
Declaring the units on the fields the first assemblies feed
(PV azimuth and tilt, the battery's capacity, the EMS offsets, the collector area, the air conditioner's power) is the
sweep of §13 step 1 (gap G10).

**Every parameter does something (decided, owner, 2026-10-04).** The library check refuses a parameter that feeds no
config field, law, preset, variant selector or display template, and an enum value that selects nothing (no variant
option, preset or `$switch` case): a knob a user can set with no effect is a silent failure. A planned option is a
comment naming what brings it back, not a declared value.

**Constraints across parameters** are structured data, not expressions: `constraints: [{exactly_one_of: [power_in_watt,
size_in_percent_of_roof_area]}, {at_most_one_of: […]}, {requires: {tilt_in_degree: [azimuth_in_degree]}}]`, each checked
against the parameter declarations when the assembly is loaded and against the given values per import. **Internal
variants partition their selector:** the `when:` lists must cover every allowed value of the selecting parameter exactly
once; a value covered twice or not at all refuses the assembly itself, so a carrier added to `values` cannot fall
through.

### 2.7 Presets

**Not in v1 (D26).** 
An assembly may carry `presets:`, named parameter sets that mirror component presets (`preset_<name>(cls, name)`):
`dhw/storage_water_heater` offers `ie_immersion_120l` (`energy_carrier: electricity, volume_in_liter: 120`), `dhw/combi`
offers `nl_combiketel`. A preset names only declared parameters and is checked like an import's parameters, constraints
included; the import record keeps the preset name and the resolved values.

## 3. Ports

### 3.1 Requirement states

Every port is in one of three states, decided per import (or instance) before binding:

- **required** — must be bound, or the file is refused at load time;
- **conditionally required** — `required_when` names parameter values; when they hold the port is required, otherwise it
  is **inactive**: never bound, never lowered, and an explicit `bind:` to it is refused. A house with gas and
  electricity wires an electric storage water heater to electricity only, although a gas provider exists;
- **optional with a declared fallback** — decided (owner, 2026-10-03, D8 (i), G13; principle: always fail hard, and
  adding one import never silently changes another): the intent is written where the port lives, with one of three
  verbs, alike on site entries and imports: `bind: {port: partner}`, the partner must exist; `optional-bind: {port:
  partner}`, binds if the partner exists in the fully expanded system, else the port keeps its default (unbound; a
  fallback only if the assembly declares one); `none: [port]`, declines it although a partner exists (its fallback, a
  config patch on named members, applies). With a candidate and no verb it is a load error — "import 'dhw': optional
  port 'ems_modifier' (partner L2GenericEnergyManagementSystem) has 1 candidate `control-EMS`; add `optional-bind:
  {ems_modifier: control}`, `bind:` or `none: [ems_modifier]`". So one site serves houses with and without an EMS
  (`optional-bind: {temperature_modifier: control}` on `Building` and `HeatDistributionController`); the translator
  writes every line.

Rejected (2026-10-02): choosing the carrier by optional connections alone; a missing needed connection would pass
silently, fuel burned and drawn from no provider (the balance accepts a booking toward "nobody the wiring names",
`hisim/energy_port.py:21-25`). `required_when` is structured, not an expression: a mapping from parameter to the list of
values for which the port is required, all keys conjunctive (`required_when: {energy_carrier: [natural_gas, lpg,
heating_oil]}`). `active_when`, same shape, makes an optional or a provided port inactive outside its values. **`optional-bind:` names a
partner the file declares** (an import key or a site entry); the partner may be absent only through a disabled group
or an unselected variant, and a name nothing declares is refused by name (owner, 2026-10-07: otherwise a typo is
indistinguishable from a house without that import). A house without an EMS omits both the `control` import and the
`optional-bind` line. Variants and `active_when` overlap: an author uses a variant when the members differ between the
cases and `active_when` when only a port's state differs (owner, 2026-10-06).

### 3.2 What a port lowers to

Decided (owner, 2026-10-03, D1): there is no connector registry and no connector type. A port names the members it
exposes and lowers to declarations HiSim already has:

- **A need from a partner**, `{into: [Tank], partner: UtspLpgConnector}`, lowers to each member's **default
  connections** from the bound partner's class: the table `add_default_connections` fills per source class
  (`hisim/component.py:338-352`, by convention from `get_default_connections_from_<class>`), copied with the partner's
  instance name by `get_default_connections` (`:487-507`), which raises when the class declares none (`:493-501`).
  Binding succeeds exactly when every `into:` member's class declares default connections from the partner's class; the
  partner is a site entry or a member of another import, found by class, and the lowering writes a bare name, as the
  twins do (dry run, G2). Several partner classes form a small group, `partner: [UtspLpgConnector, MeasuredProfiles]`;
  a new partner (a measured-profile occupancy) is integrated as every new HiSim component is, by its consumers'
  classes declaring default connections from it, and no assembly changes. Set-point and modifier inputs are needs
  alike (`flow_temperature` from the `HeatDistributionController`, `ems_modifier` from the EMS).
- **Explicit wires, the last resort.** Where a pair has no default connections, the port writes the partner's output
  names, `wires: {TemperatureInputPrimary: DailyAverageOutsideTemperatures}`. That leaks names into the assembly and
  is fixed by adding the default connection to the class (the heat pump's two weather wires, dry run §5).
- **A provided output**, `{output: PVSystem.ElectricityOutput}`, names what a partner or the controller binds to
  (`controllable:`, §4.4); observers need no port, they select by tag (§4.1). Unbound, it is unused (D20).
- **A circuit**, `{circuit: dhw, member: Tank}` (or `member: [Collector, Controller]`, each member reading or owning
  one of the circuit's outputs), is one end of one hydronic circuit and lowers to the members' default
  connections for the circuit's three outputs `MassFlowDhw`, `SupplyTemperatureDhw`, `ReturnTemperatureDhw` (§11.1).
  The circuit name is the medium: a brine circuit's outputs are not what a DHW tank declares connections for.
- **A carrier need**, `{carrier: natural_gas, outputs: [heater_fuel]}`, lowers to the provider's meter observing the
  named outputs (§5.1); for electricity, to the check that exactly one grid connection exists, and no wire. A carrier
  is an `lt.EnergyBalanceCarrier` value (`natural_gas`, `electricity`, `heating_oil`, `pellets`, …), the spelling an
  energy port serializes. The provider writes `provides: {connection: {carrier: natural_gas, meter: GasMeter}}`, and
  the consumers' feeds land at the meter's `{$port: connection}` placeholder; an electricity provision names no
  `meter:` (electricity has no link; its meter observes, §4.3).
- **A sizing fact**, `{fact: pv_peak_power_in_watt, many: true, into: [Battery]}`, lowers to a `sizing_sources` line,
  scalar or list (§6); a provided fact, `{fact: pv_peak_power_in_watt, member: PVSystem}`, names the member providing
  it and must be in that member class's `SIZING_CONTRIBUTIONS`.

Every wire the lowering writes passes the existing load-type and unit check: the energy-system wiring refuses a wire
whose ends disagree (`_check_port_types_agree`, `EF-30`, `hisim/energy_system/wiring_checks.py:197-226`) and the
simulator raises on the same mismatch when it connects (`hisim/component_wrapper.py:250-279`; `connect_input`,
`component.py:407-434`, only records the source). Parameter units are D16's (§2.6).

The energy balance is served by two declarations defined outside this spec and only referenced: the `EnergyPort` of
every energy-carrying output (#870/#871: direction and carrier, declared in the class, `hisim/energy_port.py`), and the
hydronic naming convention (`MassFlow<c>`, `SupplyTemperature<c>`, `ReturnTemperature<c>`, ownership per leg), by
which the check recognises a circuit and recomputes `m c ΔT`; once a component's heat crosses a circuit, its heat
`EnergyPort` is replaced by the circuit (hydronic spec §3.5). Both are needed because energy direction is not derivable
from wiring or types (fuel demand is an output that energetically enters the boiler), and because the check must
recompute circuit energy from primary quantities rather than trust a component's booked output (the #864 bug class).

### 3.3 Binding and the load-time checks

Binding runs innermost first (§2.5): an assembly's internal ports are bound when it is expanded, its re-exported ports
at the next level up. At each level the rule mirrors the sizing engine's (`engine.py:13-25`): a required port binds to
the one partner in scope that fits — a component of its partner class, the one other end of its circuit, the one
provider of its carrier — and `bind:` decides every other case. Each of these fails at load time with the source map of
the import (§9.2), naming the instance, the port and every candidate, with a paste-ready `bind:` line:

- a required (or active conditional) port with no partner, or several and no `bind:`; `none:` on a required port;
- a `bind:` whose partner is absent; an optional port with candidates and no verb (§3.1); a verb on an inactive port;
  an inner port neither bound nor re-exported; a `bind:` on an electricity need or an electricity output, which have
  no link end (§4.3): refused, not ignored (owner, 2026-10-03);
- a `bind:` naming a partner whose class a member declares no default connections from, or a circuit of another name;
  a need bound to `<import>.<provided port>` whose member does not read that output (checked from the files when the
  need has `wires:`, else against the default connections the wiring actually made; a bound output nothing reads is a
  silent lie);
- a carrier need without exactly one provider of its carrier (§5.1); a target input actuated twice; a meter observing
  the EMS's grid balance and an output that EMS observes (§4.3); observers are otherwise never counted, so an output
  read by many is legal;
- a contract check, run once per assembly in tests and again after expansion: every port names members that exist;
  each `into:` member's class declares default connections from every class of the port's partner group; every
  output and wire names an existing output or input; every provided fact is in the member class's
  `SIZING_CONTRIBUTIONS`.

An `optional-bind:` never fails: the import record (§2.3) says "bound `control-EMS`" or "not bound: partner absent".

**Where the checks run (decided, owner, 2026-10-04).** The expansion decides from the files alone what it can (every
entry states its `class:`): candidates, the binding verbs, the circuit and carrier topology, fact providers, parameter
units. It writes exactly what a hand-written file writes — bare-name default inputs, wires, feeds — and a provenance
table (import, instance, port, member, partner, file:line per lowered item) into the import record. Then HiSim assembles
the whole system and **the wiring stage is the connection check**: a bare name whose target declares no default
connections from the partner's class, a wire to a missing input or output, a load-type or unit disagreement, an input
fed twice are refused there as today, and a refused item carries its provenance and the port it came from. No separate
pass re-verifies connections before wiring, no class declares its ports a second time (a class-level interface
declaration was tried and rejected: it duplicated every constructor), and no stage edits the expanded file after
construction. The two facts the wiring cannot see — that a fed output's `EnergyPort` carries the need's carrier, and
that a provider's meter feeds exactly the named outputs — are two wiring checks beside the others. A selector
(`observes:`) is a filter on HiSim's automatic dynamic default connections, resolved at wiring against the components
present; circuit completeness is what the energy balance verifies by the naming convention (hydronic stage E). The
candidates and paste-ready `bind:` lines belong to the expansion-time errors, which need no instances.

## 4. Selectors: observe and actuate

### 4.1 Syntax

`observes:` is a list of selectors, allowed on any component entry, any import and any assembly interface; it replaces
the former `select:` item of a dynamic component's `inputs` (bare name, explicit wire and feed stay,
`model.py:106-211`):

```yaml
interface:                                        # supply/electricity_grid: the meter's observer port
  observes: {reading: {into: [ElectricityMeter], default: declared}}
imports:                                          # a system file writes each observer's selection
  hp_tariff_meter: {assembly: supply/meter, observes: [{component_type: [HEAT_PUMP_BUILDING, HEAT_PUMP_DHW]}]}
```

Decided (owner, 2026-10-03, D2): a selector matches the existing runtime tags, `lt.ComponentType` and
`lt.InandOutputType`, by which the meter and the EMS already find their inputs (`tags_search_and_compare`,
`hisim/dynamic_component.py:163-170`, used by `get_dynamic_inputs`, `:833-872`), or an output by name. The candidates
are the outputs the observer's class declares dynamic default connections from (`add_dynamic_default_connections`,
`:793-807`, one `DynamicComponentConnection` with its `source_tags` and weight per output, e.g. the meter's from the
occupancy, `electricity_meter.py:304-326`); `default: declared` is all of them, and a list observes the union of its
matches. The feed's tags and weight are that declaration's (an EMS's ranked weights are derived from its priorities,
§4.4), so no file authors a tag; a selector may override them
with `feed:`. The EMS's grid balance is selected by name, `{output: TotalElectricityToOrFromGrid}`, and lands on the
meter's production channel at 999 (twin :106-109). An observer never matches its own outputs. An import's
`observes:` fills the observer port its assembly declares, replacing its `default`; a controller adds `actuates:`
(§4.3), for the EMS its priority list (§4.4).

### 4.2 Semantics

- **What matches.** Every tagged output of the expanded system, site entries and imports alike; there is no
  published-port vocabulary (D2). An output whose class the observer declares no dynamic default connection from is
  no candidate: a new observed class is integrated by declaring that connection, as a new partner is (§3.2).
- **When.** Statically in the expansion; each match becomes an ordinary `AggregatorFeed` (`model.py:181-209`), the
  dynamic input wiring the meter and the EMS read every time step, so the realized record carries explicit feeds and the
  channel matching (`hisim/energy_system/channel_matching.py:3-15`) runs unchanged. **Order:** import, instance, output
  order as written, then feed resolution's sort (`feed_resolution.py:10-14`); never a dict, file system or hash order.
- **Coexistence.** Explicit feeds stay legal; an output both selected and fed explicitly to one observer is refused, as
  today's `DUPLICATE_FEED` refuses one output read twice by one dynamic component
  (`hisim/energy_system/aggregator_ports.py:39-67`), and so is any selector matching nothing (decided, owner, 2026-10-04: there is no `required:` flag; a selector that
  finds no output is a typo or a stale line, and a file that wants to observe something only if present writes
  nothing, since `declared` already observes everything the class can see). The twins
  keep their explicit feeds (§8); the gate of §13 proves a composed file's expansion writes exactly the twin's feeds.

### 4.3 Observe and actuate

Decided (owner, 2026-10-03, D13): meters and controllers are the same kind of thing with respect to a flow — they read
it — and a controller additionally acts on it. Ports stand in exactly three relations:

1. **Link** — a need and its partner, a consumer's fuel need and its provider, or the two ends of a circuit (§11.1);
   what `bind:` decides (§3.3). **Electricity has no link** (owner, 2026-10-03): its outputs are only observed.
2. **Observe** — a read-only tap on a tagged output, selected as in §4.1, by any number of observers: meters, a
   controller reading production, consumption or a tank temperature, forecasters, loggers. It lowers to the observer's
   dynamic inputs (§4.2), since HiSim's meter and EMS are time-loop components; the balance's peers come from that
   wiring as today (`hisim/postprocessing/energy_balance/ports.py:206-226`).
3. **Actuate** — one controller output into one target input. Decided (owner, 2026-10-03, D21): HiSim's three control
   layers are the architecture. L1 controllers own the loops on physical variables, hardcoded in their device's
   assembly (§2.5); the L2 EMS observes electricity flows and actuates **only** L1 set-point modifiers (the heat
   pump L1s' `SimpleHotWaterStorageTemperatureModifier` and `DHWStorageTemperatureModifier`,
   `more_advanced_heat_pump_hplib.py:2701`, `:3230`; `BuildingTemperatureModifier` of `Building` and
   `HeatDistributionController`, `building.py:332`, `heat_distribution_system.py:1045`) and the battery's
   `LoadingPowerInput` (`advanced_battery_bslib.py:195`; it has no L1). Each target input is actuated exactly once.

**Meter and EMS are observers with their own selections.** HiSim deliberately has no bus and no aggregator role: the
pattern is a dynamic component whose inputs are added as its selectors match. The `ElectricityMeter` observes one set
of electricity outputs and the EMS another, each in its own `observes:`; the sets may coincide or not. The meter
sums its production and consumption channels (`hisim/components/electricity_meter.py:504`, `:520`) and books the
difference as grid exchange. The two twin shapes are two selections of the meter, written on the `grid` import:

```yaml
grid: {assembly: supply/electricity_grid}                                               # no EMS: every flow it declares
grid: {assembly: supply/electricity_grid, observes: [{output: TotalElectricityToOrFromGrid}]}   # EMS: its grid balance
```

Without an EMS the meter computes the exchange from the flows (twin `metered_directly`, `:145-171` of
`household_heatpump_building_sizer.grouped.energy_system.yaml`); with one, `control/ems_self_consumption` observes the
flows and outputs their sum, `TotalElectricityToOrFromGrid`, which the meter reads (`ems_with_battery`, `:105-109`).
Adding a `control` import changes the `grid` import only if its author edits `observes:`; left at the default, the
double-count check of §3.3 fails the file, once the meter declares a dynamic default connection from the EMS (†, dry
run G6). The only "exactly once" rule is actuation (D21). A second meter (`hp_tariff_meter`, §4.1) is one more
observer; a fuel meter observes the fuel outputs alike (§5.1).

**Billing leaves the format.** No meter is "the billing one": a tariff in the economics file (D22) names the observer
whose reading it prices, `tariff: {observer: grid.meter, …}`, and that no kWh is priced under two tariffs is an
economics check against the observers' selections (a heat-pump tariff prices `hp_tariff_meter`, the household tariff
`grid.meter` net of it). The format checks only the link rule of §3.3 and the double count above.

### 4.4 Control: devices declare, the controller decides

Decided (owner, 2026-10-03, D11, D21): a device assembly states only that its provided electricity output is
controllable and through which input; it declares no weight. A battery names its own `LoadingPowerInput` (`controllable:
{target_input: LoadingPowerInput}`), which lowers into the EMS feed's `dispatch.target_input`, as in the twin. A heating
or DHW assembly's controllable input is its L1 controller's modifier, one need **`ems_modifier`** with the EMS as
partner (`controllable: {via: ems_modifier}` on the outputs it covers), never a device input: binding `ems_modifier:
control` lowers to the feed with an empty `dispatch: {}` plus the L1's default connections from the EMS class, the
modifier output for that component type (`controller_l2_energy_management_system.py:747-790`), so the feed and its
modifier are bound together or not at all; for these devices the actuated target is that modifier. An EV's charge
controller is an L1 as well (`ElectricityTargetFromEMS`, the car twin). A **controller assembly** owns the policy:
`control/ems_self_consumption` first, later `control/ems_tariff` and `control/ems_peak_shaving`, each with the EMS as
member, observing the flows and outputting their grid balance (§4.3), and a `priorities` parameter, the ordered list of
selectors it actuates:

```yaml
priorities:                                                   # control/ems_self_consumption, its default
  - {component_type: RESIDENTS}
  - {component_type: [HEAT_PUMP_BUILDING, ELECTRIC_HEATING_SH]}   # space heating
  - {component_type: [HEAT_PUMP_DHW, ELECTRIC_HEATING_DHW]}       # hot water
  - {component_type: SOLAR_THERMAL_SYSTEM}
  - {component_type: BATTERY}                                     # instance order
```

The list orders the outputs the controller ranks: a battery's is required and binds to the one controller; a `via:` one
(heat pump, immersion heater) is controlled only when its import binds `ems_modifier` (§3.1); a rank-only feed the EMS
class declares (residents, solar thermal) is ranked with `dispatch: {}` (dry run G18). A heat pump whose import does not bind
`ems_modifier` beside an EMS is a legal, uncontrolled observation (decided, owner, 2026-10-04, G5): the EMS class declares
rank-only feeds for `HEAT_PUMP_BUILDING` and `HEAT_PUMP_DHW` as it does for residents and solar thermal (step 4, a
result-neutral component change, every EMS twin controls its heat pump), so its consumption stays in the EMS balance
and the priorities list simply does not steer it. The lowering derives the EMS
weights deterministically. Each entry starts from the EMS class default of its component types (residents 1, space
heating 2, hot water 3, solar thermal 4, battery 6: `controller_l2_energy_management_system.py:409`, `:444`, `:466`,
`:503`, `:525`, `:555`, `:581`); the k-th further instance of a type gets `default + k`, and an entry not above every
earlier entry's weight is raised to the next free one. A default-order list with one device each therefore yields
today's weights, so a one-battery file is byte-identical to its twin under the rename map (§13); a second battery gets
7, and a reordered list (a tariff controller putting the battery before hot water) gets weights in list order. A bound
controllable output no entry selects, a weight reaching 999 and two ports of one type at one weight are load errors. The
weights go to the import record; no file authors them. An EMS without a battery is a `control` import with no `battery`
import, which the twins cannot express (`not_implemented_yet.yaml:77`).

## 5. Carriers and providers

### 5.1 Supply assemblies

A **provider** of a carrier is a supply assembly: the connection (the carrier's provider, the economics' connection
subject, §5.2) plus a meter whose `observes:` the importing file writes. `supply/electricity_grid` is the grid
connection and the `ElectricityMeter`, by default observing every electricity output its class declares (§4.3); an
electricity need only checks that exactly one exists (§3.2). `supply/gas_connection` (`GasMeter`), `supply/lpg_tank`,
`supply/oil_tank` (`FuelMeter`) and `supply/district_heating_substation` name their meter on the provision,
`provides: {connection: {carrier: natural_gas, meter: GasMeter}}`, and every bound consumer's fuel output lands at the
meter's `{$port: connection}` placeholder as the feed its class declares for that output (the `GenericBoiler`'s
`EnergyDemandSh` and `EnergyDemandDhw` for the gas meter), whose `EnergyPort` names the carrier: that feed is the fuel
link, and the meter the fuel port's balance peer as today (`hisim/components/generic_boiler.py:450`, `:501`;
`energy_port.py:12-17`).
Control is a `control` import (§4.4). The meter's carrier is pinned from the provider, not copied from "the generator
beside it" through the `energy_carrier` fact (`hisim/components/gas_meter.py:63-68`, `:82`), which a second burner makes
ambiguous; every bound consumer's carrier is checked against the provider's at load time.

LPG is not a carrier yet: `LoadTypes` has `GAS` and `OIL` (`hisim/loadtypes.py:122`, `:129`), the balance carriers have
no LPG (`loadtypes.py:278-285`, `energy_port.py:101-113`), and LPG houses run the gas twin (`translate.py:513`), which
is how renovisorissues #77 bills them a gas standing charge. `supply/lpg_tank` needs that carrier first (§13).

### 5.2 A missing provider fails; the RenoVisor adds it explicitly

An active carrier port without a provider is a load error; the format never adds structure. The translator adds a
provider explicitly when the house or a measure needs it and reports it as one mapping-report line with its economics:
the connection as a subject of its own (connection fee as investment), the carrier's standing charge from the tariff
(`hisim/economics/catalog_entries.py:291`), and for a provider the plan leaves without consumers the removal of the
import and the carrier's grid exit fee (`catalog_entries.py:292`). A provider with no bound consumer is refused.

### 5.3 Carrier-dependent assemblies

Three patterns: (1) **pure parameter** — `GenericBoiler` takes `energy_carrier` (`generic_boiler.py:198`) and its fuel
ports follow it (`EnergyPort.carrier_of_fuel`, `energy_port.py:116-124`, used at `generic_boiler.py:420`); (2)
**parameter-selected internal variants** — different components sharing the rest (immersion heater or burner); (3)
**separate assemblies** where variants share almost nothing (a heat-pump water heater). The storage water heater is
pattern 2 († does not exist yet: the immersion heater is hisim-epc.21):

```yaml
# energy_systems/assemblies/dhw/storage_water_heater.assembly.yaml  (schema_version 3, kind: assembly)
parameters:
  energy_carrier: {type: enum, values: [electricity, natural_gas, lpg, heating_oil], default: electricity,
                   description: What heats the tank.}                # lpg †: no lt.EnergyBalanceCarrier member yet
  volume_in_liter: {type: float, unit: LITER, default: AUTO, description: Usable tank volume.}  # AUTO: class law
presets:
  ie_immersion_120l: {energy_carrier: electricity, volume_in_liter: 120}
components:
  Tank: {class: hisim.components.simple_water_storage.SimpleDHWStorage, preset: standard,
         config: {volume_heating_water_storage_in_liter: {$param: volume_in_liter}}}
variants:
  heater:
    selected_by: energy_carrier                  # the when: lists partition energy_carrier's values (§2.6)
    options:
      immersion:
        when: [electricity]
        components:
          Heater: {class: hisim.components.immersion_heater.ImmersionHeater, preset: standard}   # †
      burner:
        when: [natural_gas, lpg, heating_oil]
        components:
          Heater: {class: hisim.components.generic_boiler.GenericBoiler, preset: condensing_gas,
                   config: {energy_carrier: {$param: energy_carrier}}}   # carrier name -> LoadTypes in the loader
interface:
  needs:
    hot_water_demand: {into: [Tank], partner: [UtspLpgConnector]}   # Tank's default connections from the occupancy
    electricity: {carrier: electricity, outputs: [heater_electricity], required_when: {energy_carrier: [electricity]}}
    fuel: {carrier: {$param: energy_carrier}, outputs: [heater_fuel],
           required_when: {energy_carrier: [natural_gas, lpg, heating_oil]}}
    ems_modifier: {into: [HeaterController], partner: L2GenericEnergyManagementSystem,                      # † L1
                   optional: true, active_when: {energy_carrier: [electricity]}}
  provides:
    heater_electricity: {output: Heater.ElectricityInput, controllable: {via: ems_modifier},                # †
                         active_when: {energy_carrier: [electricity]}}
    heater_fuel: {output: Heater.EnergyDemandDhw, active_when: {energy_carrier: [natural_gas, lpg, heating_oil]}}
```

The heater's L1 thermostat (`HeaterController` †, in the full file) is always there; `heater_electricity` is
inactive for a burner, and the EMS raises the thermostat's set point through `ems_modifier` when the import binds it,
which also makes the heater's feed controlled (§4.4). The full file is
`assemblies_mockup/dhw/storage_water_heater.assembly.yaml` (§12).

**One library check.** The expansion runs the complete library check on every assembly it loads, at run time as in
the library test: a missing name, description, range, default-less parameter an import does not state, test contract
or unit declaration refuses the file wherever it is used (2026-10-04, from the review of #885; there is no weaker
"expansion strength").

**Members per variant option (decided, owner, 2026-10-04).** An option that swaps a member's class names its own
member (`Burner` in the gas option, `Immersion` in the electric one); one name for two classes is refused by the
library check, since a contract or a port written once could not hold for both. A port's `into:` and a contract's
`bounds`/`monotone`/`expect` may name members of any option; at expansion only the members the selected option has
are bound and checked, and a port that names no existing member in the selected option is a contract error, so a port
never binds nothing silently. `describe` prints the members per option.

## 6. Sizing across assemblies

**v1 carries `Sum(Many)` and the fact ports only; exports and the scoped-provider rule are not in v1 (D26).** 
The resolver sees the expanded system: every member is an ordinary config under its expanded name, so the engine's
providers and binding rule apply unchanged (`engine.py:193-225`, `:311-348`). Three cases need the expansion's help:

- **A fact read over all instances.** The battery is sized to all arrays. Its laws become a sum over a many-cardinality
  read, which needs the `Many` aggregation implemented with an explicit `Sum` (`laws.py:228-241` raise today). The
  battery's fact port reads `pv_peak_power_in_watt` with `many: true`; the expansion writes
  `sizing_sources: {pv_peak_power_in_watt: [pv-east-PVSystem, pv-west-PVSystem, …]}` in instance order, which the
  engine reads in written order (`engine.py:387-399`).
- **A fact an import makes ambiguous.** A burner inside a DHW assembly contributes `maximal_thermal_power_in_watt` as
  the space-heating boiler does, and the buffer's volume law reads that fact (`simple_water_storage.py:160-162`). Rule:
  an assembly declares which contributions it **exports** (re-exported through every enclosing assembly like a port);
  for every reader outside it whose read an unexported contribution would make ambiguous, the expansion writes the
  explicit `sizing_sources` line to the reader's previous provider; a read inside an assembly binds to its own member
  first, then to its fact port.
- **Meter fuel constants.** The fuel meter copies heating value and density from "the generator", which derives them
  from carrier and boiler type (`hisim/components/fuel_meter.py:61-69`, `generic_boiler.py:217-259`). Until the meter
  converts per participant, the provider refuses consumers whose constants differ (§14, D10).

## 7. Groups and variants after assemblies

- **Import slots replace add-on groups and assembly choices replace structural variants.** No array is no `pv` import,
  not a zero-power pin; no EMS is no `control` import; the DHW type and the heat generator are the `assembly:` of the
  `dhw` and `heating` imports; `electricity_management` becomes the presence and choice of the `control` import.
- **What remains.** Groups and variants stay in the format, because the grouped twins are generated from grouping
  decisions (`energy_systems/README.md:162-214`) and keep their guarantees. Imports live at the top of a system file or
  of an assembly, never inside a group or a variant option, and an assembly holds no group or variant except its
  parameter-selected internal variants (§2.6), resolved before anything else.
- **Combinations are explicit assemblies** (§2.5): no inner import is optional, so an assembly holds no switch.

## 8. Recording and the twins

Recording cannot see intent ("one run cannot be asked about intent", `energy_systems/README.md:97-101`), so (D3) twins
stay flat, the site and the composed files (site plus the imports reproducing one twin) are written by hand, and a test
compares each composed file's expansion with its twin under the import record's rename map; no recorder change. The
`energy-system-freshness` workflow expands every composed file and example and regenerates the library index, failing on
any byte difference, as `scripts/record_all_setups.py --check` does (`energy_systems/README.md:149-151`).

## 9. Versioning, provenance, source maps and the library

### 9.1 Versioning and provenance

The realized record is written from the expanded file — flat, every preset expanded, every sized value a number, every
feed explicit (`hisim/energy_system/record.py:9-15`) — so a re-run never needs an assembly again; provenance is for the
reader. The `metadata` block (`hisim/energy_system/metadata.py:1-18`) gains `imports:` with, per import and instance at
every depth, the assembly path, the sha256 of its canonical text, the preset, the parameters as resolved, the internal
variant selections, the port states and the members' addresses; `result.json` provenance carries the same hashes.
Decided (D4): content hashes now, pinned versions when a library outside the repository exists.

### 9.2 Source maps

The expansion attaches a **source map entry** to everything it produces — component, input item, feed, `sizing_sources`
line, config value: the import path, the instance, the member, and the assembly file and line it came from (the loader
keeps YAML line numbers for this). The entries live in a side table keyed by structured address and item, not in the
expanded file, so the byte-identity gate of §13 is unaffected; the realized record's metadata carries them. Every
downstream error prints the entry of what it names — class validation, sizing (ambiguous or missing fact), wiring and
feed checks, the energy-balance report, economics (a subject without a catalogue entry) — in the shape
"`dhw-generator-HeatPump` (import dhw → generator, `dhw/heat_pump_water_heater.assembly.yaml:6` →
`generator/dhw_heat_pump.assembly.yaml:9`): …".

### 9.3 The library

- **Resolver.** An import's `assembly:` is resolved along a search path: `energy_systems/assemblies/` first, then
  directories a simulation parameter or `HISIM_ASSEMBLY_PATH` names, so assemblies can later live in a library outside
  the repository. A name found in two places is refused, never shadowed.
- **Describe.** `hisim energy-system describe` (`hisim/cli.py:105`, `:209`, today a class path) also takes an assembly
  path and prints the interface (ports, partner classes, requirement states), the parameters with units,
  descriptions and constraints, the presets and the inner imports.
- **Index, examples, tests.** A library index and each assembly's interface documentation are generated and checked
  for freshness (§8). Each assembly has one example (a minimal system file importing it, which also joins the golden
  configuration so its default result is a golden leaf set) and the test contract of §9.4.

- **Mock assemblies (owner, 2026-10-04).** The format's own tests use a test-only library,
  `tests/assemblies/mock_assemblies/` (family `mock/`, classes `Mock*` in `mock_components.py`), whose paths can never
  collide with the real library's. A mock assembly or class exists only for a shape the real library cannot show — a
  refusal, a deliberately wrong contract, depth-4 nesting, a cycle, a backwards heater — and every test that can run on a
  real assembly moves to the real library when it exists (§13 step 4); the mock library then shrinks to that residue.
  Each mock class says in one line which real class or role it stands in for. The spec's design mockup
  (`roadmap/declarative_energy_systems/assemblies_mockup/`) is a design document, not a test input: the schema test reads
  it only until the first real assemblies land, then the mockup is marked historical.

### 9.4 Testing an assembly

"Tested fragments" means more than one run at the defaults (owner, 2026-10-03). Every assembly carries its **test
contract in its own file**, structured data the generic harness in `hisim/energy_system/assemblies/testing/` executes,
run by `hisim energy-system test-assemblies`; nothing is written per assembly in Python (decided, D24: contract in the
assembly file, not a sibling file or hand-written tests, so the
library test can refuse an assembly without one and `describe` prints it).

**Samples.** The harness derives the parameter samples from the declarations: every preset, every `range` boundary
(`min` and `max` of each numeric parameter, the others at their defaults, so every parameter declares a default),
every `values` entry, every internal variant,
and a seeded **Latin hypercube sample** of the parameter box (owner, 2026-10-03: `scipy.stats.qmc.LatinHypercube`,
scrambled, fixed seed): every numeric parameter is a dimension over its `range`, every `values` parameter and every
internal variant a stratified discrete dimension, and a constraint with alternatives (`exactly_one_of`) splits the box
into one hypercube per branch, so no sample is thrown away for violating a constraint. Sample size and seed are the
harness's, not the assembly's; the size is recorded with the run.

**Isolation run.** For each sample the harness builds a minimal system: the assembly, plus for each port a test
partner, optional ports included, and for a carrier the assembly provides a consumer partner; one simulated day at
900 s per step (an assembly declares no resolution), the energy-balance check and `i_doublecheck` on. The test
partners are data, one `test_partners.yaml` per library: a site entry per partner class, circuit end, carrier,
consumer or fact, each naming the partners it requires. The run fails on an exception,
a NaN or infinity, an open balance (`EnergyBalanceError`) or a violated declaration below.

**Declarations** (`tests:` in the assembly file; structured, no expressions):

```yaml
tests:
  bounds:                                   # over every sample; an output or a KPI stays in a physical band
    - {output: Tank.TemperatureMean, unit: CELSIUS, min: 5, max: 95}
    - {kpi: Seasonal performance factor of SH heat pump, member: HeatPump, min: 1.5, max: 7}
  monotone:                                 # one parameter rises, all else at the sample; the KPI moves one way
    - {parameter: volume_in_liter, kpi: Standby heat losses, member: Tank, direction: increasing}
    - {parameter: scop_en14825_w35, kpi: Electricity consumption, member: HeatPump, direction: decreasing}
  expect:                                   # a preset's result on the test weather lies in a band
    - {preset: ie_typical, kpi: Seasonal performance factor of SH heat pump, member: HeatPump, min: 3.3, max: 4.6}
```

- `bounds` names an output (`member.OutputName`, unit declared and checked) or a KPI (name and member, resolved by the
  finder of `roadmap/kpi_address_spec.md`, never by key string); it holds for every sample. A KPI entry **without**
  `member` names a derived KPI (one the KPI preparation computes per building, `source: null`), resolved by name within
  the isolation system's single building, where it is unambiguous (decided, owner, 2026-10-04). Device KPIs that are
  derived today (PV production, the battery's three) move into their components before assemblies ship several
  instances of them (§10.4, #83), so each instance reports its own and the building sum stays a derived total.
- `monotone` is evaluated on every sample as a base point, moving one parameter across its range in a few steps;
  `direction` is `increasing`, `decreasing` or `constant`, within the gate's numeric tolerance. It is the cheap test
  that catches wrong-sign physics.
- `expect` pins a preset's results to a band, like the goldens do exactly; a band is wider than a golden and states the
  modeller's plausibility judgement.
- **Required (D24):** every parameter has a `default`; every numeric parameter has a `range`; every energy-carrying or
  temperature output of a member has a `bounds` entry; at least one `monotone` entry when the assembly has a numeric
  parameter (an assembly without one, such as a bare supply connection, has nothing to sweep), naming a KPI of one of
  its own members or a derived KPI by name (so a composite assembly made only of inner imports can state one). The
  library test refuses an
  assembly missing any of them, as it
  refuses one without descriptions. A declaration that names a missing member, output, KPI or parameter is a contract
  error at load time.

**Tiers (D24).** The PR gate runs the contract test and the deterministic samples (presets, boundaries, values,
variants) with every declaration, a few one-day runs per assembly, sharded like the week goldens. The Latin hypercube
sample of the box runs as the job `assemblies-nightly` of `golden-year.yml`, beside the full-year goldens and for now
behind the same gate, once per PR commit; a schedule comes with the real library. A failure opens a bead, as a golden
drift does. The example system of every assembly is a golden pair, so its default result is also pinned exactly.
The harness exists from step 1 on fixture assemblies with fake components (§13); it meets real components in step 4.

## 10. The RenoVisor on assemblies

### 10.1 Shape of a translated file

Decided (owner, 2026-10-03, D17): there is **one base file, the site** (`energy_systems/site.energy_system.yaml`), and
the heat generator is an assembly. A translated file is `site + heating import + dhw import + pv instances + battery
instances + control import + supply imports`. The site holds weather, occupancy, building, the heat distribution system
and its controller (D12) and what the hydronic stages leave on the house side; its entries carry `ports:` blocks where
an import binds to them (the `space_heating` circuit, the EMS modifier inputs). The `heating/<generator>` assemblies —
`condensing_gas_boiler`, `oil_boiler`, `pellet_boiler`, `wood_chip_boiler`, `hydrogen_boiler`, `air_source_heat_pump`,
`ground_source_heat_pump`, `district_heating`, `electric_heating`, and `heating/solar_thermal` beside one of them — each
expose an `space_heating` and a `dhw` circuit port and their carrier needs, and hold their buffer vessel (D12). The translator's
rule R4 becomes **"may only pick tested assemblies, their presets and parameters"**: it writes the site's configuration
values as today and an `imports` block (assemblies, presets, instance keys, parameters, binding verbs), nothing else;
`DiffRule` (`translate.py:386-483`) checks the `imports` block against the library.

### 10.2 Coverage

Instead of ~70 recorded branches: (1) each assembly in isolation (§9.3); (2) contract tests (§3.3); (3) a combination
matrix **heating × dhw × supplies × arrays** against the single site — every heating assembly with every DHW assembly it
can serve (each variant) and the supplies they need, 0–4 arrays with 0–2 batteries and each controller on one heating
assembly — one day with the energy-balance check per cell. The capability probes (`hisim/renovisor/verify/`) gain probes
per DHW type and array count.

### 10.3 Hot water (#85)

| Request type | Assembly | Parameters | Prerequisite |
|---|---|---|---|
| `from_space_heating_cylinder` | `dhw/indirect_cylinder` | volume | binds the heating assembly's `dhw` circuit (§11.1) |
| `from_space_heating_combi` | `dhw/combi` | — (preset `nl_combiketel`) | generator DHW side without a vessel |
| `storage_water_heater` | `dhw/storage_water_heater` | `energy_carrier`, volume | immersion heater (hisim-epc.21); LPG carrier |
| `instantaneous_water_heater` | `dhw/instantaneous_water_heater` | `energy_carrier` | a flow heater component |
| `heat_pump_water_heater` | `dhw/heat_pump_water_heater` (nested, §2.5) | volume | DHW heat pump (hisim-lenz) |
| `district_heating` | `dhw/district_heating` | — | substation DHW side; `supply/district_heating_substation` |
| `none` | no `dhw` import | — | the heating's `dhw` port declined; reported: hot water not served |

A `hot_water_system` measure swaps the `dhw` import's assembly and parameters, declines the heating assembly's `dhw`
port where the new type does not use it, adds a provider where the new type needs a carrier the house lacks (§5.2), and
its economics retire the old import's members as replaced subjects and buy the new ones.

### 10.4 Photovoltaics and batteries (#83)

- **Ids and measures.** Each existing system has an id (`^[a-z0-9_]+$`), which is its instance key;
  measures name ids, never indices. A legacy single object normalises to a one-element list with the id `pv_system`
  (`battery`). `replaces: <id>` replaces the named system; on the list shape a measure without `replaces` adds an
  instance under its `system_id`. New problems `measure.replaces_unknown`, `measure.replaces_twice`,
  `measure.system_id_duplicate`; `MEASURE_DUPLICATE` (`hisim/renovisor/request.py:74`) is lifted for these.
- **Mounting surface, not roof** (owner, 2026-10-03: facade PV is coming, so the roof cannot be a hard limit). An array
  states what it is mounted on, `mounted_on: roof | facade` (later a named roof face or facade once the contract has
  `house.building.roof.faces[]`). `size_in_percent` is a share of *that* surface's area, which the Building knows for
  both (`roof.area_in_m2`, `facade.area_in_m2`), and `pv/array`'s fact port names the surface's area fact by
  `mounted_on`. The fill check is per surface: the share-sized arrays on one surface may not exceed 100 % of it, an
  added one beyond that is capped (approximated) and reported; roof and facade do not compete; a stated power
  (`exactly_one_of: [power_in_watt, size_in_percent]`) and an existing array are never capped. No orientation means
  south with the roof shape's tilt for a roof array, the facade's azimuth at 90° for a facade array, both reported.
- **Economics.** Every instance is its own subject bound to its own register entry (`ExistingAsset.subject`,
  `own_register_entry`, `hisim/economics/facts.py:294`, `:561`; salvaged from PR #872): kept, replaced or added; the EMS
  is costed with the `control` import. The Irish grant `IE_SEAI_SOLAR_PV` (TIERED_PER_UNIT on kWp, cap 1800 €) is per
  dwelling: kWp summed per stage, tiers and cap once, split pro rata (`aggregate_over_subjects_in_stage`).
- **Stages.** Instance keys are stable across stages; one mapping-report line per instance leaf
  (`house.pv_systems[2].azimuth`); probes for 1–4 arrays, replace, add and overfill.

## 11. Interplay

### 11.1 Hydronic coupling (hisim-fxix, PR #878)

A circuit port is one end of one circuit (hydronic spec §3.1): its circuit name `<c>` selects the outputs `MassFlow<c>`,
`SupplyTemperature<c>` and `ReturnTemperature<c>`, the naming convention says which end owns which leg and the pump, and
the binding lowers to the members' default connections for them (§3.2). The `HydronicPort` ends are then found from the
wiring (spec §3.5, "the two ends come from the wiring, never from a declared peer"), so an assembly needs nothing beyond
the wires its port produced. A circuit port binds exactly one partner; a split is a valve assembly with one circuit per
branch. The dual-circuit generators (spec §3.3, circuits `SpaceHeating` and `Dhw`) make a heating assembly possible: `space_heating` binds
the site's distribution side (D12), `dhw` binds `dhw/indirect_cylinder` or is declined. **Dependency:** the heating
assemblies require hydronic stages C (DHW chain) and D (SH chain) of spec §9.5; before them generator, buffer and HDS
are coupled through default connections (spec §9.2) and cannot be cut at a circuit. Stages A–B and §13 steps 1–3 proceed
in parallel; each heating assembly is written once, against pure circuits, after C and D (or their joint PR) are on
main.

### 11.2 Energy balance (hisim-9uoo, #870/#871)

Members keep their classes' `EnergyPort`s; the two declarations the balance needs, and why, are §3.2. A fuel port's
peer is the provider's meter observing it (`energy_port.py:12-17`), electricity's whoever observes the output (§4.3).
The check allows a one-sided booking toward a component without ports (`energy_port.py:21-25`), so the format, not the
balance, refuses a missing provider (§5.2).

## 12. Worked examples

The full mockup is `assemblies_mockup/` beside this file: `renovisor_full_house.energy_system.yaml` (one request, every
schema leaf and catalogue measure, † where HiSim lacks the feature), one `*.assembly.yaml` per import with real classes.
`dry_run_heatpump_twin.md` expands the heat-pump composed file with the twin's defaults
by hand as §2.3 describes into `expanded_heatpump_default.energy_system.yaml` and compares it with the twin's
`ems_with_battery` option; its gap list is what this spec owes before §13 step 4. All design, not loadable.

Each example is the `imports` block the translator adds to the site file (§10.1).

(a) An air-source heat pump with four arrays and two batteries:

```yaml
imports:
  heating: {assembly: heating/air_source_heat_pump, optional-bind: {ems_modifier: control}}
  dhw:     {assembly: dhw/indirect_cylinder, bind: {circuit: heating.dhw}}
  supply:  {assembly: supply/electricity_grid, observes: [{output: TotalElectricityToOrFromGrid}]}
  control: {assembly: control/ems_self_consumption}
  pv:
    assembly: pv/array
    instances:
      east:  {azimuth_in_degree: 90,  tilt_in_degree: 35, power_in_watt: 3000}
      west:  {azimuth_in_degree: 270, tilt_in_degree: 35, power_in_watt: 3000}
      south: {azimuth_in_degree: 180, tilt_in_degree: 35, power_in_watt: 5000}
      flat:  {azimuth_in_degree: 180, tilt_in_degree: 10, power_in_watt: 2000}
  battery: {assembly: storage/battery, instances: {garage: {capacity_in_kwh: AUTO}, cellar: {capacity_in_kwh: 5}}}
```

It expands to `pv-east-PVSystem` … `pv-flat-PVSystem`, `battery-garage-Battery` (weight 6; sized to all four
arrays), `battery-cellar-Battery` (7), `heating-HeatPump` (2 and 3) and `control-EMS` with one production feed per
array and the controlled feeds in priority order; `supply-ElectricityMeter` has one input, the EMS's grid balance its
`observes:` line selects, as in the twin (§4.3). Two arrays of equal tilt and azimuth share one cached
series, since the cache key holds neither power nor name (`hisim/components/generic_pv_system/pv_system.py:516-547`).

(b) A gas-heated Irish house (`IE.N.SFH…` archetype) with an electric immersion storage water heater:

```yaml
imports:
  heating: {assembly: heating/condensing_gas_boiler, none: [dhw]}
  supply:  {assembly: supply/electricity_grid}
  gas:     {assembly: supply/gas_connection}
  dhw:     {assembly: dhw/storage_water_heater, preset: ie_immersion_120l, optional-bind: {ems_modifier: control}}
```

`dhw.fuel` is inactive, so the gas connection is never offered to the heater although it exists; `dhw.electricity` is
met by the one grid connection, `supply`, whose meter observes every flow; `ems_modifier`'s `optional-bind:` finds no
`control`, so the heater runs on its L1 thermostat alone, recorded "not bound: partner absent". The boiler's `dhw`
circuit is declined, which sets its controller to run without hot water. Adding a `control` import later binds
`ems_modifier` as the file already says; the file then fails only until `supply` observes the EMS's grid balance (§4.3).

(c) After a `hot_water_system` measure to a heat-pump water heater only the `dhw` line changes, to `{assembly:
dhw/heat_pump_water_heater, parameters: {volume_in_liter: 200}}`. The economics retire every member of the old import
and buy every member of the new one (`dhw-tank-Tank`, `dhw-generator-HeatPump`, `dhw-Controller`); a subject is
keyed by the structured address and the assembly path, so a `dhw-Tank` of two assemblies are two subjects.

(d) An all-electric house (`heating/electric_heating`, `dhw` declined) adding `dhw/instantaneous_water_heater` with
`energy_carrier: natural_gas` is refused without a gas provider ("import 'dhw': port 'fuel' (carrier
natural_gas) is required and no provider of natural_gas exists", with the import's source map); the translator adds
`gas: {assembly: supply/gas_connection}` and reports it with the connection fee and the standing charge.

## 13. Migration and staging

1. **Format, no behaviour change.** Assembly loader and schema; nested `imports`, presets, parameters with units and
   constraints; the port lowering to default connections and the missing class declarations it finds;
   `ComponentID.path`; source maps; partner, circuit and fact ports, import record, load-time checks; `describe`;
   the test contract (`range`, `tests:`), its contract checks and the harness of §9.4 on fixture assemblies with fake
   components. Fixtures only.
2. **Observe and actuate, tag selectors, controller lowering.** `observes:`/`actuates:` items, carrier needs, the link
   rule, the grid-balance selection and the double-count check (§4.3), the priorities.
3. **Sizing.** `Many` with `Sum`; the battery laws over all arrays; fact exports (§6). One array stays byte-identical.
4. **First assemblies.** The site, one heating assembly (`heating/air_source_heat_pump`), `pv/array`, `storage/battery`,
   `control/ems_self_consumption`, `supply/electricity_grid`. Gate: site plus these imports expands to the heat-pump
   twin byte for byte under the rename map and a short, listed set of intended differences (decided, owner, 2026-10-04,
   G7: a many fact port with one provider writes its one-element `sizing_sources` list where the twin writes no line —
   one lowering rule whatever the count, and adding a second array never changes the first one's lines), and a one-day
   run gives identical result columns; each carries its test
   contract (§9.4) and the harness runs them in the PR gate. Waits for hydronic stages C and D (§11.1).
5. **One heating assembly per PR,** each with the equality gate against that generator's twin: condensing gas, oil,
   pellets, wood chips, hydrogen, ground source, district heating, electric heating, then solar thermal, with
   `supply/gas_connection`, `supply/oil_tank`, `dhw/indirect_cylinder`. The RenoVisor switches each generator to the
   site plus its heating import in the PR whose gate passes; its outputs for every existing probe are identical.
6. **New structure.** #83 request contract and N instances; LPG carrier and `supply/lpg_tank` (#77); DHW assemblies as
   their components land (hisim-epc.21, hisim-lenz); #85 contract; further controllers.

**Order in the base files** (§2.3). The composed files set `order:` to reproduce the twin's sequence up to a neutral
swap, buffer before cylinder (`composed_heatpump_default.energy_system.yaml`); the numbering serves every generator
twin, and `heatpump_solar_thermal` alone is reordered and re-recorded once (dry run §9.1).

## 13.1 Lean v1 (owner, 2026-10-06)

The five implementation PRs of step 1a to step 3 (#885–#889) explored the full design and came to about 10,300 lines
of library code, more than HiSim's simulator core. The owner went through every feature and decided what v1 carries;
the exploration PRs stay as the record, and v1 is built as one new branch from `main` that takes the needed and
recommended pieces, with this section as its spec. Every cut feature may return as its own PR when a real assembly needs
it, which is a better test of need than a mockup.

**Kept in v1:** assembly files with components, parameters (unit, description, default, `range`), `exactly_one_of`
constraints, the test contract (§9.4) and the full library check; import once or as named instances; expansion to one
flat file with `ComponentID.path` and hyphenated addresses, site entries first then imports, each in file order; need
ports by partner class with `bind:`, `optional-bind:`, `none:`, `required_when` and `active_when`; wires and provided
outputs; internal variants `selected_by`; circuit ports; carrier needs and providers with the meter feeds; fact ports
with `Sum(Many)`; selectors `observes:` as a filter on the observer's dynamic default connections, `controllable`
(`target_input` / `via`) with the controller class's own weight table (instances in written order); the double-count
check; source maps on every error; the import record with the binding decisions; `display:` templates; `describe`
for assemblies; both JSON schemas; the harness rebuilt on pytest (sampler incl. the Latin hypercube tier, isolation
builder with the partner registry, the bounds/monotone/expect checks; pytest's parametrization, reporting and `xdist`
shards; the nightly tier a marker run by the golden-year workflow; an external library runs `pytest
--assembly-library DIR`).

**Cut from v1 (D26):**

| Feature | Decision | What returns it |
|---|---|---|
| Nesting: inner imports, re-exports, internal ports, depth and cycle rules (D7) | assemblies are flat; shared sub-structure is duplicated | two real assemblies sharing a sub-structure |
| `order:` evaluation paths (D23) | a flat integer `order:` on site entries and imports only, sorted ascending, no nested paths, no per-member order (an import's members stay a block in file order); entries without `order:` follow the ordered ones in file order (revised, owner, 2026-10-07: the twin's sequence interleaves site entries and import members, and the sequence moves results) | — |
| Presets inside assemblies (§2.7) | parameters have defaults, importers set values; `expect` is at defaults | a catalogue of typical devices belongs to the RenoVisor, not the fragment format |
| `$switch`, `$fact`, `$derived` | `$param` is the only placeholder; per-variant values live in the variant options | — |
| Fact exports and the scoped-provider rule, the fuel-constant check (§6) | a scalar read with two providers is refused as ambiguous and the author writes the `sizing_sources` line | — |
| Port-provenance table and restated wiring errors | the wiring's own error plus the source map (component and file:line) | — |
| `at_most_one_of`, `requires` | expressed as an enum parameter selecting a variant | an assembly that needs them |
| `priorities` reordering, weight derivation, `feed:` overrides (§4.4) | the controller ranks what its class ranks, in the class's order; a second instance follows the first | a tariff controller needing another order |
| The standalone harness CLI, JSON report, summary, shard machinery | pytest | — |

The sections above that describe a cut feature remain as the design record and are marked "not in v1" where they
start; §14.1's D7 and D23 are superseded by D26 for v1.

## 14. Decisions

### 14.1 Decided

All by the owner on 2026-10-03.

- **D1 — Interface:** explicit ports in the assembly file, lowered to default connections, tags and units HiSim
  already has; no connector types (§3.2). The contract test ties each port to the default connections its members'
  classes declare. **D14** (connector registry location) is dropped: there is no registry.
- **D2 — Selector scope:** selectors match the existing runtime tags (`ComponentType`, `InandOutputType`) the meter and
  EMS already match by, or an output name, statically (§4.1, §4.2); they see every tagged output, and there is no
  published-port vocabulary. The site is not forced into assemblies.
- **D3 — Recording:** (b): twins stay flat; the site and the composed files are written by hand and tested against their
  generator's twin under the rename map (§8, §13). A one-time split tool bootstraps the first site and assembly files
  from a twin plus an assignment of each component to a member; it is not a maintained layer.
- **D4 — Versioning:** content hashes now; pinned versions (`pv/array@2`) when a library outside the repository exists.
- **D5 — Identity:** a structured address in `ComponentID` (`path` plus member), `-` joining only its serialization,
  display-only and never parsed back (§2.4); `ComponentID.display_name` carries the member's rendered `display:`
  template.
- **D6 — Provider handling:** (a): a missing provider is a load-time error naming import, port and carrier; the
  translator adds the provider explicitly, reports it and costs the connection (fee, standing charge, exit fee when a
  stage leaves it idle); an idle provider is refused (§5.2).
- **D7 — Nesting:** from the start, depth-limited to 4, ports internal or re-exported, binding innermost-first (§2.5).
  Groups and variants stay, imports never inside them; retiring them is revisited when no grouped twin is read.
- **D8 — Ports:** (i) the three verbs `bind:`, `optional-bind:`, `none:`, alike on site entries and imports; unmentioned
  with a candidate is a load error; resolves G13 (§3.1, §3.3). (ii) `required_when`/`active_when` are structured,
  conjunctive mappings `{parameter: [values]}`. (iii) A fallback is a config patch on members, never a structural
  change. (iv) Sizing facts are `fact:` ports (§3.2).
- **D9 — Staging:** (a), the gate being "the assembly's defaults reproduce the twin": per generator one PR adds
  `heating/<generator>` (and its DHW assembly) with the twin's presets and values as defaults. Its expansion, in the
  composed file's `order:` (§2.3), equals the twin under the rename map modulo a reviewed list of intended differences
  (names; pins become `AUTO`; "no PV" as no import; the EMS as an import; a **neutral swap**, two components that read
  nothing from each other exchanged, which leaves every pass bit-identical), the sized values are identical and the
  one-week run equals the **existing golden**: no twin or golden is re-recorded (except `heatpump_solar_thermal`, §13).
  The twins' test artefacts are site values; the equality test stays in the freshness workflow permanently.
- **D10 — Sizing across assemblies:** (a): `Many` with an explicit `Sum`, a `many: true` fact port lowered to a
  `sizing_sources` list in instance order, and fact exports; laws stay in the classes (§6). (b), the summed fact as a
  parameter, was rejected: it moves the law into the file and cannot work when an array's power is itself sized.
  Per-participant fuel constants in the meter follow when a second burner needs them.
- **D11 — Dispatch:** the controller assembly owns the priority list and derives the weights (§4.4).
- **D12 — Site versus heating assembly:** (b): the buffer vessel lives in each heating assembly; the site keeps the HDS
  and its controller and exposes one `space_heating` circuit; `distribution/<type>` waits for a request that changes the HDS.
- **D13 — Metering:** meters are observers, each with its own selection, and billing is the economics file's (§4.3).
- **D16 — Unit check source:** (b): every config field an assembly parameter feeds declares its unit
  (`sized_field`/config `unit=`), and the check compares the parameter's unit with it; a fed field without a declared
  unit is refused. The name suffix is documentation, not the source. A sweep over the fields the assemblies feed
  precedes step 1 (gap G10).
- **D17 — Base files:** one site file and `heating/<generator>` assemblies instead of ten skeletons (§10.1).
- **D18 — Installation year and quote:** reserved import-level fields next to `assembly:`, read by the economics through
  the import record and never by a component; not a parameter every assembly redeclares, not outside the file.
- **D19 — No change to the Building's cardinality:** one named input per kind acting on the zone
  (`building.py:244-331`), each linked once by the Building's default connections from the device class (§3.2). A
  secondary heater, an air conditioner and a ventilation unit each get one named input when their component exists, as
  the CHP did; two of a kind are refused, as the request allows one each. Many-cardinality stays in meter, EMS, sizing
  facts.
- **D20 — Optional provided ports:** (a): a `provides` port may be optional (a solar coil on a DHW tank or a buffer; a
  combi boiler has none): unbound it is unused, bound it is checked like any circuit, and a `bind:` to an import
  without it is a load error naming both assemblies.
- **D21 — Control layers:** L1 controllers are hardcoded in their device's assembly; the L2 EMS observes electricity
  outputs and actuates only L1 set-point modifiers (`ems_modifier`) and the battery's `LoadingPowerInput` (§4.3, §4.4).
  A combination is its own assembly with no optional inner import, and a control choice is a parameter (§2.5, §7).
- **D22 — Where the economics inputs live:** (i) What is about the calculation — `location.country`, `applicant.*`, plan
  start and price-basis year, the subsidy catalogue and the tariff choice — goes into a third file kind,
  `*.economics.yaml`, beside the composed files and recorded with the run (`EconomicContext` and `EconomicParameters`
  made declarative), so one house pairs with several economics cases. A tariff there names the observer whose reading it
  prices (`tariff: {observer: grid.meter, …}`), and the economics refuse a kWh priced under two tariffs, checked against
  the observers' selections (§4.3). (ii) A quote is a reserved field on the instance or envelope element the plan buys,
  `quote: {amount_in_euro: {min, max}, per: m2 | unit | kw | kwh, source}`; a quote on a kept instance is a load-time
  error (#67), `per` is checked against the subject's size unit (#74), and each stage's file carries its quotes.
  `location.country` in both files must agree.
- **D23 — Evaluation order:** declared and nested, duplicates refused, pinned in the records; resolves G14 (§2.3, §13).
- **G8 — A heating assembly without a buffer:** deferred to hydronic stage D, which makes the HDS's
  `position_hot_water_storage_in_system` derivable from the circuit binding instead of set by an import; until then
  the heating assemblies are written with the buffer. An import never patches a site field.
- Also: units, descriptions, constraints and the variant partition check (§2.6); presets (§2.7); source maps (§9.2);
  `describe`, index, examples, isolation tests and the resolver's search path (§9.3).

- **D24 — Tested fragments (owner, 2026-10-03):** every assembly carries its test contract in its own file: a
  `default` on every parameter, `range` on every numeric parameter, `tests.bounds` on every energy-carrying or
  temperature output, at least one `tests.monotone` when the assembly has a numeric parameter, optional `tests.expect` per preset; a generic harness
  (`hisim/energy_system/assemblies/testing/`, `hisim energy-system test-assemblies`) runs each sample for one day at
  900 s beside test partners read from each library's `test_partners.yaml`, and samples presets, boundaries, values
  and variants on every PR and a seeded Latin hypercube sample of the parameter box in the nightly tier, the job
  `assemblies-nightly` of `golden-year.yml` (one hypercube per constraint branch, discrete parameters and variants
  stratified); an assembly without the contract is refused by the library test (§9.4). Rejected: a sibling test file
  (drifts) and hand-written Python tests per assembly
  (no generic harness, no `describe`).

- **D25 — Buffer or no buffer is a different assembly, and the pump owner follows the circuit (owner, 2026-10-04):**
  `heating/air_source_heat_pump` (buffer inside) and `heating/air_source_heat_pump_direct` (the heat pump feeds the
  distribution) are two assemblies; there is no `with_buffer` parameter. The HeatDistribution's
  `position_hot_water_storage_in_system` encodes who owns the mass flow of the space-heating loop (two loops and two
  pumps with a buffer; one loop pumped by the generator without one; a fixed flow when the generator has no pump,
  district heating). That is a property of the circuit, which the hydronic design already states: one pump owner per
  circuit, who publishes `MassFlowSpaceHeating`. From hydronic stage D on the HDS derives it from what the other end
  of its circuit publishes (it reads the flow through its default connections from that class, else it pumps), and the
  enum goes away; no fact, no site setting, nothing written twice. Step 4 waits for stages C and D, so the first real
  assemblies never meet the enum. Rejected: a sizing fact for the position (a second statement of a wiring fact), the
  HDS inside the direct assembly (two shapes of the Building's input, D19), a site value checked against the import.

- **D26 — Lean v1 (owner, 2026-10-06):** see §13.1; the cut list there supersedes D7 and D23 for v1. Revised 2026-10-07
  after the review of this PR: a flat integer `order:` on site entries and imports returns (the twin's sequence
  interleaves them and the sequence moves results); `optional-bind:` must name a partner the file declares.

### 14.2 Open

**D15 — Serialized address for the frontend.** (a) `-` joining as in §2.4. (b) A separate field per path element in
`result.json` and the KPI JSON, with the `-` string only as the column name. Recommendation: **(b)** in `result.json`
and `KpiAddress`, (a) for columns and names; to be settled on renovisorissues before step 4 of §13.

## 15. Related

- renovisorissues **#83** (several PV systems and batteries), **#85** (seven hot-water types), **#77** (LPG houses
  billed a gas standing charge).
- Beads **hisim-fxix** (hydronic coupling), **hisim-9uoo** (energy balance), **hisim-epc.28** (PV adds), **hisim-9b0m**
  (storeys; the roof area bounds the arrays' share, `roadmap/renovisor/implementation/storeys_geometry.md`).
- PRs **#872** (closed; salvageable: own subject and register entry for an added unit, recorder fixes in
  `recording/matrix.py` and `regrouping.py`, commit eb2be815), **#878** (hydronic coupling spec).
- `roadmap/kpi_address_spec.md` (KPI keys, §2.4); `roadmap/declarative_energy_systems/p2_file_format_requirements.md`,
  `grouping_overview.md`.
