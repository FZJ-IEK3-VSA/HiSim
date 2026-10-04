# Energy systems

This directory holds **energy-system files**: declarative descriptions of one simulated household,
written as YAML, with no Python involved. A file states what each component is, how it is
configured, where it takes its inputs from and — only where that would otherwise be ambiguous —
which component it takes each of its sizing facts from. Everything else, including every value a
preset pins and every value the system computes for itself, follows from those declarations.

## What is here

| File | What it is |
|---|---|
| `gas_boiler_household.energy_system.yaml` | A single-family house with a condensing gas boiler, floor heating and grid electricity. The reference system of this directory, and the only hand-written one. |
| `<setup>.energy_system.yaml` | The recorded twin of `system_setups/<setup>.py`, one per setup. Generated; see "Recording a Python setup" below. |
| `<setup>.probes.yaml` | The module configurations `<setup>` is recorded under — the *probe list*. Authored; the first entry is always the class defaults. See "Groups and variants" below. |
| `<setup>.grouping.yaml` | What a person decided each difference between those configurations means. Authored through a workbook and committed in this form. |
| `<setup>.grouped.energy_system.yaml` | The twin again, with the differences that are structure expressed as groups and variants. Generated from the two files above. |

Two kinds of file live side by side and are never mixed:

- `*.energy_system.yaml` says what the household **is**. It carries no period, no resolution and no
  post-processing, so the same household can be run over a day and over a year without a second
  copy of it.
- `*.simulation.yaml` says what to **do** with it: the period, the time-step length, the logging
  level and which post-processing to run. Those live one directory up, in `simulation_parameters/`,
  because a parameter set belongs to no particular household — see the README there. A
  `*.simulation.json` of the same shape is read as well, which is what the Python setups in
  `system_setups/` already ship.

## Running one

Either of these runs the same thing:

```bash
hisim energy-system run energy_systems/gas_boiler_household.energy_system.yaml \
    simulation_parameters/one_day_15min_export.simulation.yaml

python hisim/hisim_main.py energy_systems/gas_boiler_household.energy_system.yaml \
    simulation_parameters/one_day_15min_export.simulation.yaml
```

The run writes its results, and beside them four files that describe what was actually run:
`realized.energy_system.yaml` — the file again with every preset expanded and every computed value
written out, annotated with where each number came from; `realized.audit.yaml` — the same
provenance as plain data; `component_connections.json` — the flat log of every connection that
was made; and `realized.simulation.yaml` — the parameter set the run was given, without the
settings that describe the machine rather than the run. All four are written before the first
timestep, so a run that dies halfway still describes itself.

The first and the last are the two arguments of the command that produced them, so a result
directory re-runs from its own contents, and `--rerun` checks that it reproduces field by field:

```bash
hisim energy-system run results/.../realized.energy_system.yaml \
    results/.../realized.simulation.yaml --rerun
```

### Reading a run's KPIs by address

A run with `COMPUTE_KPIS` and `WRITE_KPIS_TO_JSON` writes `all_kpis.json`, nested building, then
tag, then key, then entry (`roadmap/kpi_address_spec.md`). A KPI a component reports is always
keyed `"<name> (<source.name>)"`, whether or not another component reports the same name, and its
entry carries `source`, the component's structured address; a derived KPI (the General tag, cost
and district totals) keeps its bare name and says `"source": null`. So a key never changes when a
neighbour is added. Before and after, for the Building component of a house:

```text
before: "Conditioned floor area": {"name": "Conditioned floor area", "value": 140.0, ...,
            "nameOfSourceComponent": "Building"}
after:  "Conditioned floor area (Building)": {"name": "Conditioned floor area", "value": 140.0, ...,
            "nameOfSourceComponent": "Building",
            "source": {"import": null, "instance": null, "member": "Building", "assembly": null,
                       "name": "Building", "display_name": "Building", "label": null}}
```

`...` stands for the entry's other fields (`unit`, `description`, `tag`, `valueMin`, `valueMax`).
`nameOfSourceComponent` is deprecated and kept for one release as the same string as
`source.name`. Never build or split a key: filter on the fields instead, with
`hisim.postprocessing.kpi_computation.kpi_address.KpiFinder` (`KpiFinder(json.load(f)).value(name=...,
source=...)`) or on the command line, which prints the dotted address (`building.tag.key`, the
golden references' form), its value and its unit per KPI:

```bash
hisim kpis list results/<run directory> --tag "Electricity Meter"
# BUI1.Electricity Meter.Total energy from grid (ElectricityMeter) = 3412.7 kWh
```

The filters are `--building`, `--tag`, `--name`, `--source` (the source's runtime name), `--import`
and `--instance`.

## Finding out what to write

```bash
hisim energy-system describe hisim.components.generic_boiler.GenericBoiler
hisim energy-system facts energy_systems/gas_boiler_household.energy_system.yaml
```

`describe` prints one class in full: its fields, its named presets and what each of them leaves to
be sized, its named constructors and their parameters, how each sizable field is computed and from
which facts, and which facts it contributes to the rest of a system. `facts` prints, for a whole
file and without running it, the file's knobs — a flag per group and a selected option per variant,
which is everything a consumer of a checked-in system edits — and then every fact somebody
provides, every fact somebody reads, and which provider each read resolved to.

A constructor's arguments are written and decoded exactly like a `config` value: an enum by its
member name or its value, a nested object as a mapping of its own fields, several of them as a
list, and a wrong one refused by name, listing the accepted members or types.

Editors get the same knowledge from `hisim/energy_system_v3.schema.json`, which every file in this
directory binds to with its first line. The schema is generated from the same declarations
`describe` reads — regenerate it with `hisim energy-system schema` whenever a component gains a
preset, a constructor or a field.

## Recording a Python setup

Every `<setup>.energy_system.yaml` here is generated. It was produced by running the Python setup
and writing down what it built — one setup at a time:

```bash
hisim energy-system record system_setups/basic_household.py \
    simulation_parameters/one_day_15min_export.simulation.yaml
```

or the whole fleet at once, which is what regenerates this directory:

```bash
python scripts/record_all_setups.py
```

The recorder observes the prepared, connected system the setup built — it never parses the setup's
source and never runs the timesteps — so a twin states what the setup actually constructed rather
than what its code appears to say. A class that carries a preset is written as that preset plus
whatever the setup changed, and the file names no sizing sources, no groups and no variants: those
are judgements about intent, and one run cannot be asked about intent.

A twin is an **authored** energy-system file, not a transcript of one run. Where a sizing law
computed a value and the recorded system itself contains a component that contributes the facts
that law reads, the twin **writes no line for that field at all** — the preset's own `AUTO` already
answers it:

```yaml
  PVSystem:
    class: hisim.components.generic_pv_system.PVSystem
    preset: rooftop
```

`preset: rooftop` is what says the array is sized from the roof, so a `power_in_watt:` line
restating that default in order to annotate it would add nothing a loader or a reader needs. The
entry re-sizes: point the same file at a different building and the array follows it. That is what
makes a recorded sizer usable as somebody else's base file rather than as one archetype's frozen
numbers.

The number that run produced is not lost, it just lives where it belongs: in the **realized
record** every run writes beside its results (`realized.energy_system.yaml`), which states every
field concretely with the law that computed it and the component that answered each fact in the
margin. `hisim energy-system facts <file>` prints the same thing for a file without running it.

A line that *does* state a number states it for one of two reasons, and says which. Either the
setup assigned the value itself, in which case it is an override like any other and carries no
comment; or a law computed it from a fact that nothing in this system provides yet — the class that
would contribute it has not been converted — and the line reads
`# pinned: no provider of <fact> in this system yet`. Those lines disappear by themselves on the
re-record that follows that class's conversion, so the twins get shorter as the conversion
proceeds. One rarer pin names its own reason: a fact several components in the system declare, so
the file would be ambiguous and a twin writes no `sizing_sources`.

A field whose *preset* pinned a law of its own — the pellet boiler's minimal power, a twelfth of
its maximum instead of the class default of zero — needs no pin. Leaving the line out is exactly
what keeps that law: the preset builds the field holding its own rule and the resolver evaluates
it, whereas a written-out `AUTO` would replace it with the class rule.

Before the command returns, the file it wrote is loaded back through the executor, built and
prepared, so a twin that does not work is reported as a failed recording rather than left behind.
The same step checks every field the twin left to a preset: the resolved value is compared with the
value the Python run produced, and a difference **fails the recording**, naming the setup, the
component, the field, both numbers and the law. A mismatch means the laws and the declared
contributions do not reproduce the context the setup built by hand, which is a finding about the
setup or the component — the recorder never pins the number to make it go away.

Recording is deterministic: the same setup and the same parameters produce the same bytes on any
machine. A twin is therefore regenerated rather than edited — change the setup, re-record, and read
the diff. `python scripts/record_all_setups.py --check` re-records every setup into a throwaway
directory and fails on any difference, which is what the `energy-system-freshness` workflow runs on
every pull request, so a setup cannot change without its twin.

There is no skip list. A setup that cannot be recorded is a defect in the setup or a gap in the
format, and the driver names every one it could not record and exits non-zero. Every setup in the
repository records today, and the rule has already done its work twice rather than never:
`household_gas_solar_thermal` could not be recorded until its doubled meter feed — wired to the
occupancy once explicitly and once through the meter's own declared default — was fixed in the
setup, and `household_heatpump_car_building_sizer` could not be recorded until the `Car`
occupancy-data spelling the declarative path can supply existed. Both were repaired rather than
skipped, which is the point of having no list.

## Groups and variants: probe, decide, apply

A recording observes one run, so it can only describe the branch that ran. The twelve
building-sizer setups driven by a `ModularHouseholdConfig` have branches — a battery that is there
or is not, a rooftop share somebody picks — and which of those differences is *structure* is a
judgement no diff can make. So
there is a second pass with a person in the middle of it, and three files per setup.

```bash
# 1. record the setup under every configuration of its probe list and prefill the workbook
hisim energy-system grouping probe system_setups/household_heatpump_building_sizer.py \
    energy_systems/household_heatpump_building_sizer.probes.yaml

# 2. a person fills in the two right-hand columns of the workbook, then:
hisim energy-system grouping import energy_systems/household_heatpump_building_sizer.grouping.xlsx

# 3. build the grouped file and prove it against every probe
hisim energy-system record system_setups/household_heatpump_building_sizer.py \
    simulation_parameters/one_day_15min_export.simulation.yaml \
    --grouping energy_systems/household_heatpump_building_sizer.grouping.yaml

# 4. re-render the page that says what all of this came to
hisim energy-system grouping overview
```

The workbook has two sheets. `components` has one row per component in the union of the probe runs
and one cell per probe holding `—` (not there), `=` (there, says the same) or `≠` (there, says
something else). The third state is the point: an electricity meter is present in every
configuration and *wired* differently in one of them, which a presence matrix cannot see and which
is exactly what a group cannot express and a variant can. `configurations` has one row per probe,
the module-configuration fields that produced it, and the switch positions that column stands for.

A row that is `=` everywhere needs nothing and stays empty. Every other row must carry one of
three answers: `group:<name>`, `variant:<name>` or `variant:<name>/<option>`, or `override` — the
difference is a value a consumer sets rather than a question of membership. The tool never decides
a row's meaning itself — it only writes down what it observed and carries a previously committed
decision forward — and a `≠` row left empty is refused by name, twice: once by the importer
against the cells, and once by the second pass against the recordings.

Every probe column is then an assertion the second pass checks: put the grouped file's switches
where that column stands, resolve it, and the result equals that column's flat recording byte for
byte. The baseline column is what makes the grouped file provably the committed twin with structure
added. `override` differences are not in the file at all — the file states the baseline's value and
the pass lists each of them as a *consumer knob*, so a column's verdict says exactly how much the
file itself determined.

Two limits are printed rather than implied. The knob list is what the file does not decide. And a
grouped file's guarantees reach exactly as far as its probe list: a list that toggles each fork on
its own has tested no two of them together, so the report names every combination nobody exercised.

The workbook is a scratch artefact and is git-ignored; the probe list and the grouping decision are
committed and reviewed. Re-probing a setup carries the committed decision back into the workbook, so
a setup that grew a component is asked about that row and no other.

The fourth step answers the question the four files per setup do not: which setups have structure
yet, what that structure is, and why each difference was called what it was called. `hisim
energy-system grouping overview` sweeps every committed `*.grouping.yaml`, reads the grouped files
they produced and the recorded twin of every setup, and writes
[`roadmap/declarative_energy_systems/grouping_overview.md`](../roadmap/declarative_energy_systems/grouping_overview.md).
It covers the whole recorded fleet rather than the grouped part of it: a fleet table with one row
per setup, linking to that setup's own section, then a section per setup — for a grouped one a
diagram of where the grouped file puts each component, every judgement note in full and what each
probe column stands for; for a setup that has only a twin, its inventory and the plain statement
that this is all the page can say about it yet. The page is generated and committed, like the twins
themselves: a test re-renders it and compares it byte for byte, so it is re-run whenever a decision,
a grouped file or a twin changes rather than edited by hand.

## Assemblies: composing a system from tested fragments

Schema version 4 of the format adds **assemblies** (`roadmap/declarative_energy_systems/assemblies_spec.md`,
on PR #881): fragments of an energy system in files of their own, `<family>/<name>.assembly.yaml`,
which a file imports instead of writing their components out. These are steps 1 to 3 of that spec
(beads hisim-lt0b.1, hisim-lt0b.2, hisim-lt0b.8, hisim-lt0b.3 and hisim-lt0b.4): the format, the
loader, the expansion, the checks, the test harness, observe and actuate, and sizing across
assemblies, proven on the mock assemblies under `tests/assemblies/mock_assemblies/`. No real assembly ships yet, so
`energy_systems/assemblies/` does not exist.

```yaml
schema_version: 4
components:                       # the site, as today, plus order:, ports: and the verbs
  Weather: {order: 1, class: ..., preset: ...}
imports:
  pv:  {order: 2, assembly: pv/array, instances: {east: {azimuth_in_degree: 90}, west: {azimuth_in_degree: 270}}}
  dhw: {order: 3, assembly: dhw/storage_water_heater, preset: large, optional-bind: {ems_modifier: Ems}}
```

An assembly file (`kind: assembly`) holds **members** — ordinary component entries whose names are
local to it, with an optional relative `order:` and an English `display:` template — **parameters**
with `type`, `unit` (a member name of `lt.Units`), `description`, `default`, `values` and, for a
number, `range`; **constraints** (`exactly_one_of`, `at_most_one_of`, `requires`); **presets**;
**inner imports** (nesting up to four deep); **internal variants** selected by a parameter, whose
`when:` lists partition its values; an **interface** of ports; and its **test contract**, `tests:`
with `bounds`, `monotone` and `expect` (§9.4). A value an assembly parameter supplies is written
`{$param: <name>}` anywhere a value goes; a numeric parameter may only feed a config field that
declares its unit — `sized_field(..., unit=lt.Units.WATT)` or `field(metadata={"unit": lt.Units.WATT})`
— and the units must agree; a mismatch is a load error, never a conversion.

**Expansion.** `hisim energy-system run` expands every import into ordinary components before
anything else happens, innermost first, and everything downstream sees a flat version-3 file. A
member is named by its structured address (`ComponentID.path`), serialized with `-`:
`pv-east-PVSystem`, `dhw-tank-Tank`. Only the expansion produces such names; an authored name with a
hyphen is refused. A file without imports comes back byte for byte. The evaluation sequence follows
the `order:` paths (`6 < 6.1 < 6.3.1 < 7`); without `order:` it is the site's components in written
order, then the imports.

**Ports.** A need `{into: [Member], partner: Class}` lowers to the member's default connections
from the bound partner's class — a bare name where the member's `{$port: <port>}` placeholder
stands — or to the explicit `wires:` it names. It binds to the one component in scope of its partner
class; anything else is decided by a verb, alike on site entries and on imports: `bind: {port:
partner}` (the partner must exist), `optional-bind: {port: partner}` (binds if it exists, else stays
unbound, and the record says so) and `none: [port]` (declines an optional port). `required_when` and
`active_when` make a port depend on the parameters. Inside an assembly an inner import's port is
bound by a verb on the inner import, by an `internal:` entry, or re-exported with `from:
<inner>.<port>`. Binding a component needs no declaration beyond what its class already does in
its constructor: the expansion decides from the files alone (every entry states its class) which
component is a candidate and refuses what they decide — no partner, several and no verb, `none:` on a
required port, an absent `bind:` partner, a verb on an inactive port; each of these refusals names
the import, the instance, the port and every candidate, prints the source map of the import, and
ends in a paste-ready `bind:` line. The expansion writes the same items a hand-written file writes
(bare names, wires), and the wiring stage checks every connection on the constructed components,
as it does for any file: whether the member declares default connections from the partner's class,
whether a wired input or output or a provided output exists, whether load types and units agree.
A refused item names the port it came from — the import, the instance, the port, the partner, the
member and the files and lines, from the port-provenance table the import record keeps — and a
missing default connection or port is reported as `EF-7H` or `EF-7J`.

**Circuits.** `{circuit: dhw, member: Cylinder}` (or `member: [A, B]`) is one end of one hydronic
circuit; the name is the medium (`dhw`, `sh`, `brine`, `solar_dhw`). A required end binds the one
other end of the same circuit in scope — another import's circuit port or a site entry's
(`ports: {sh: {circuit: sh}}` on the entry itself) — and `bind: {circuit: heating.dhw}` decides
several; an end of another circuit is refused (`EF-7N`). The binding lowers from the files alone:
every member of one end that carries the `{$port: <port>}` placeholder takes a bare name of each
member of the other end. The wiring expands those bare names through the readers' default
connections and checks them like any connection, so the hydronic naming convention — each of
`MassFlow<C>`, `SupplyTemperature<C>` and `ReturnTemperature<C>` (`<C>` the name in camel case,
`Dhw`, `SolarDhw`) an output of exactly one member of the two ends and an input, of the same load
type and unit, of a member of the other end — holds or the build is refused: a reader without
default connections from a member of the other end (`EF-7H`, naming the circuit port), an output
owned twice (`EF-26`), a load type or unit that differs (`EF-30`), a reader left without its circuit
inputs (`EF-31`). A member of an end that owns none of the outputs the other end reads is therefore
refused, not skipped. A circuit port under `provides:` is optional (D20). The record lists every circuit with
both ends and their members.

**Carriers.** A supply assembly provides a carrier, `provides: {connection: {carrier: natural_gas,
meter: Meter}}`, the meter carrying a `{$port: connection}` placeholder; a site entry provides one
with `ports: {gas: {carrier: natural_gas}}` and is its own meter. A carrier is written as an
`lt.EnergyBalanceCarrier` value (`natural_gas`, `heating_oil`, `electricity`, …), or a
`{$param: …}`/`{$switch: …}` resolving to one. A need, `{carrier: natural_gas, outputs:
[Boiler.FuelUse]}` (an output may also be named by a provided port of the assembly), binds the one
provider of its carrier in scope, `bind:` decides several, and no provider is refused with the
import that would add it ("add the import of `supply/gas_connection`"; the format never adds one,
§5.2). For a fuel the provider's meter observes the consuming outputs: the meter takes a bare name
of each consuming member, which the wiring expands through the default feeds the meter's constructor
declares from the consumer's class (`add_dynamic_default_connections`, with their tags and weight).
The wiring checks, beside the connections it makes, that every named output exists, that its
`EnergyPort` carries the need's carrier (`EF-7Q`), and that the meter's default feeds from the
consumer's class are exactly the named outputs (`EF-7H`, `EF-7J`), each refusal naming the need.
Electricity has no link: a need writes nothing, takes no verb, and checks that exactly one
electricity provider exists; its outputs' carriers are verified alike. A fuel provider no need is
bound to is refused.

**Facts.** A fact need, `{fact: pv_peak_power_in_watt, into: [Battery]}`, lowers to a
`sizing_sources` line on each member naming the provider: a site entry or member whose class
contributes the fact, or an import's provided fact, `provides: {peak_power: {fact:
pv_peak_power_in_watt, member: PVSystem}}`, which must be in the member class's
`SIZING_CONTRIBUTIONS`. Two providers and no `bind:` is the sizing engine's ambiguity (`EF-4B`),
raised at load time with the candidates and a paste-ready line.

**Sizing across assemblies** (§6, D10, hisim-lt0b.4). A law may read a fact from *every* provider and
sum it: `Sum(Many(Size.PV_PEAK_POWER_IN_WATT))`, which scales, clamps and rounds like any law. `Sum`
is the one aggregation; `Many` outside a `Sum`, a function law reading `Many`, and `Max`/`Min` are
refused when the class is defined. The engine binds a many read to the list its `sizing_sources`
names — `pv_peak_power_in_watt: [pv-east-PVSystem.pv_peak_power_in_watt, …]` — or, with no line, to
the one provider when there is exactly one; a list naming nobody (a group switched off may shrink one
to `[]`) or one provider twice is `EF-4G`, never a zero or a double count. The sum of one provider is
that provider's value, integers sum as integers and anything else exactly rounded (`math.fsum`), so
the order of the list never moves the number. The sizing record, the audit and the resolution report
name every provider with its value; the realized record writes the list. The real battery's class
laws sum every array (`BatteryConfig.CAPACITY_LAW`, `INVERTER_POWER_LAW`); its preset `sized_to_pv`
keeps the one-array laws the recorded twins were written with, so every twin sizes, records and runs
exactly as before, and writing the two fields `AUTO` selects the sums.

- *A fact read over all instances.* `{fact: pv_peak_power_in_watt, many: true, into: [Battery]}`
  binds every provider of the fact in scope — the level's own members as written, then every
  import's provided fact, import by import and instance by instance as written — and lowers to the
  list. The field's law must sum it: a list into a one-provider law, or one provider into a sum, is
  `EF-7X`, naming the field and its law (a pinned field reads nothing and is not checked). A many
  port takes no verb and cannot be optional; with no provider it is `EF-7R`.
- *Exports.* A member's contribution is internal to its assembly unless the assembly exports it:
  `provides: {<port>: {fact: …, member: …}}` is the export, re-exported through every enclosing
  assembly with `from: <inner>.<port>` like any port (an `export:` key is refused). A bare read binds
  within its own assembly first, and never to another assembly's internal contribution: wherever the
  engine's bare rule would bind elsewhere or be ambiguous, the expansion writes the explicit line to
  the provider the reader's scope gives — a site buffer stays on the site boiler beside a DHW
  assembly's unexported burner; a buffer inside a heating assembly takes that assembly's generator —
  and records it (`metadata.imports.scoped_sizing_sources`, with the source map's note). An exported
  contribution joins the provider set like a site entry's. Several providers in the reader's scope stay
  the ambiguity `EF-4B`; a read only internal contributions answer is `EF-7Y`.
- *Fuel constants.* A fuel provider's meter converts with one carrier, heating value and density,
  which it copies from its consumer. Until it converts per participant, the consumers of one
  provider must state the same three (D10): each consumer's fuel contribution — for the boiler
  `GenericBoilerConfig.fuel_constants` from carrier and boiler type, a contribution of its own since it
  needs no sizing — is read before sizing, and a mismatch, or a consumer stating none, is `EF-7Z`
  ("provider gas.connection (meter gas-Meter) of natural_gas serves space-Burner (GAS, …) and
  water-Burner (GAS, …)").

**Values.** Besides `{$param: <name>}`, a value may be `{$switch: <selector>, <case>: <value>, …}`:
the case the selector chooses, where the selector is a parameter (cases: its values) or an internal
variant (cases: its options), and the cases must cover every value exactly once. `{$fact: …}` stays
refused (`EF-7L`): a value that follows a fact is a sized field whose law reads it, declared in its
class and fed by a fact port, so `$fact` would either repeat that or put a law into the file (D10 b);
the mockup's uses (`household_name`, a constructor argument, and the charging station, from facts no
class contributes) need those declarations first.

**Display names.** A member's `display:` template, rendered over the resolved parameters
(`"PV array, {facing}, azimuth {azimuth_in_degree}"`), becomes its `ComponentID.display_name` and the
`display_name` of every KPI it reports; without a template the component's `DisplayConfig.pretty_name`
and then its member name are used.

**Observe and actuate** (§4, hisim-lt0b.3). HiSim has no bus: a meter and an energy manager are
dynamic components whose inputs are added as their selectors match. An **observer** is an assembly's
observer port, `interface: {observes: {reading: {into: [Meter], default: declared}}}`, whose feeds land
where its member writes `{$observes: reading}`, or a site entry with `observes: [...]` and
`{$observes: observes}` in its inputs; an import's `observes:` replaces the port's default (a list for
the assembly's one observer port, or `{<port>: [...]}`). The candidates are the outputs of every
component of the expanded system that the constructed observer declares a dynamic default
connection from — what its constructor adds with `add_dynamic_default_connections`, as the real
meter and energy manager do (an observer declaring none cannot observe, `EF-7H`) — never its own.
`observes: declared` is HiSim's `connect_automatically` for those dynamic default connections, and
a selector filters them, so the selection runs in the wiring stage, where the components exist:
the selected feeds are planned and checked like written ones, and a refusal of one names the
observer port (its `observe` row of the port-provenance table) — an observed output the participant
lacks (`EF-7J`), a channel that does not accept it (`EF-28`), an output fed twice (`EF-25`), a
dispatch of another quantity than its target input takes (`EF-30`), a target input actuated twice or
also wired (`EF-26`). The built file and its realized record carry the selected feeds as written
feeds, so a re-run selects nothing.
`declared` takes them all; a selector matches by `component_type` (an `lt.ComponentType` name or a
list), `flow` (an `lt.InandOutputType` name or a list) or `output` (a name) — the keys of one
selector hold together, a list observes the union — with an optional `feed: {component_type, tags,
weight}` override and `required: true`. Each match becomes an ordinary aggregator feed with the
declaration's tags and weight, in the order site entries, then imports, instances and members as
written, then the outputs as the observer declares them; the build's feed resolution sorts them again.
Refused: a `required` selector or a whole selection matching nothing (an idle observer), a ranked feed
on an observer that is no controller (`EF-7S`), and one output selected and fed explicitly to one
observer (`EF-25`).

The **grid** is `supply/electricity_grid`: the electricity provider (no link, no meter on the port)
and a meter observing. Its two shapes are two selections on the `grid` import: left at `declared`,
the meter observes every flow it declares (the twins' `metered_directly`); beside an energy
manager it observes the manager's grid balance by name, `observes: [{output:
TotalElectricityToOrFromGrid}]`, on its production channel at 999 (`ems_with_battery`; the real
`ElectricityMeter` declares that feed from `L2GenericEnergyManagementSystem`). A component reading
another observer's output and an output that observer reads — the meter left at `declared` beside the
manager — is the **double count**, which the wiring refuses over all planned feeds, naming the
observer port (`EF-7T`).

**Actuate.** A device states only that a provided electricity output is controllable:
`controllable: {target_input: LoadingPowerInput}` (a battery, which has no L1; it binds the one
controller and is refused without one unless `optional: true`), or `controllable: {via: ems_modifier}`
(an L1 modifier need partnered with the manager's class: binding it lowers to the L1's default
connections from the manager, the modifier; unbound, the output is not controlled). A **controller**
assembly (`control/ems_self_consumption`) declares `actuates: {priorities: {$param: priorities}}`, its
priority list, an ordered list of selectors. Its observer port ranks every feed the manager declares at
a weight other than 999: residents and solar thermal rank-only with `dispatch: {}`, a `via` output with
`dispatch: {}`, a battery with `dispatch: {target_input: LoadingPowerInput}`. **Weights are derived,
never written**: each entry starts from the default of its component types — the weight the manager's
constructor declares, for the real manager `L2GenericEnergyManagementSystem.DEFAULT_WEIGHTS` (residents 1,
space heating 2, hot water 3, solar thermal 4, battery 6) — the k-th further instance of a type gets
`default + k`, and an entry not above every earlier entry's weights is raised to the next free one.
One device each in the default order gives the class weights; a second battery gets 7; a list putting
the battery before space heating gives 1, 6, 7. Refused (`EF-7V`): an entry selecting a measured
output or one an earlier entry ranks, an entry with `feed:`, a ranked output no entry selects, a
weight reaching 999, two ports of one type at one weight, a weight written on a controller's
selection. Refused (`EF-7U`): a controllable output no controller ranks or two do, a `via` output
ranked by a manager it is not bound to, `actuates:` on an import. The wiring refuses the rest as it
refuses any connection, naming the controller's port: a `controllable` naming an input the manager
cannot actuate — not an input of the device (`EF-7J`), on a channel that forbids a dispatch
(`EF-29`), or of another quantity than the manager dispatches (`EF-30`; it actuates only L1
modifiers and inputs of the quantity it dispatches, D21) — and a target input actuated and also
wired (`EF-26`). The import record lists every
observer with its selection, its feeds (tags, weight, dispatch, the ports they grow, the selector),
a controller's priorities with the weight each entry gave, and every actuation.

**Derived port names** (hisim-lt0b.11). An observer grows one input per feed,
`<output>From<participant>`, and a dispatch output, `DispatchTo<participant>_<input>` or
`DispatchFor<participant>_<output>`; the participant enters by one function,
`NameSyntax.port_name_part`, `_` for `-` (`ElectricityOutputFrompv_east_PVSystem`), which the
imperative `Input_<participant>_<output>_<n>` labels use too. Two participants of one observer whose
names map to one port name (`pv-east-PVSystem` beside a site entry `pv_east_PVSystem`) are refused
by the wiring (its `EF-32`, restated as `EF-7W` with the observer port); the record shows every port
name.

**Finding assemblies.** An `assembly:` path resolves under `energy_systems/assemblies/`, then under
every directory `HISIM_ASSEMBLY_PATH` names (`os.pathsep`-separated); a path found twice is refused.
The realized record's metadata carries the **import record** — per import and instance the assembly
path and the sha256 of its file, the preset, the parameters as given and as resolved, the variants,
the members' addresses, display names and order paths, every port's state and partner, the binding
decisions, the port-provenance table and the evaluation sequence — and the **source map** of every
produced item, which every downstream error naming a produced component prints. A realized record
re-runs without any assembly; a refusal on the re-run names the port from the record's
port-provenance table as on the first run.

**Inspecting.** `hisim energy-system describe <family>/<name>` (or a path to a `*.assembly.yaml`)
prints an assembly's interface as its file declares it, with partner classes and requirement states
(the partners' default connections are verified when a system is built, not here), its parameters with
units, ranges, defaults and values, its constraints, presets, members, variants, inner imports and
test contract; `describe` of a component class shows the unit every field declares. `hisim
energy-system schema` writes `hisim/assembly_v4.schema.json` beside the energy-system schema (whose
config fields carry their declared unit as `x-unit`); it states the library contract, so validating a
draft lists what it still owes.

**Testing an assembly.** Every assembly carries its test contract in its own file (§9.4, D24): a
`range` on every numeric parameter — the box it is tested over — and under `tests:` a `bounds`
entry for every energy-carrying or temperature output of every member (`{output: Tank.HeatLoss,
unit: WATT, min: 0, max: 200}`; a KPI is bounded as `{kpi: PV production, member: PVSystem, min:
0}`), at least one `monotone` entry while it has a numeric parameter (`{parameter: volume_in_liter,
kpi: Standby heat losses, member: Tank, direction: increasing}`, or `decreasing`, `constant`; a bare
connection has nothing to sweep), and optionally `expect` bands per preset.
The contract test has two halves. The library check reads the file and refuses an assembly without
a `tests:` block, a range or a `monotone`, or with a declaration naming a member, parameter or preset
that does not exist. A member written in several variant options is checked once per option, against
that option's class, and a problem names the option (hisim-lt0b.13); a `{$param: …}` carrier must be
a carrier for every value it takes while its port is active. The member contract needs the
constructed members (no class declares its outputs or KPIs a second time): the harness runs the base
samples first and checks, on their members, that every energy-carrying or temperature output — a
power or energy unit, a temperature load type or a °C/K unit (`MemberContract`,
`hisim/energy_system/assemblies/testing/contract.py`) — has a `bounds` entry, that a bounds entry's
unit is its output's, and that every named KPI is one the member's `get_component_kpi_entries`
reports. A violation is a contract failure named by member and output (or KPI), and no declaration
of the assembly is evaluated. The generic harness,
`hisim/energy_system/assemblies/testing/`, executes the contract; nothing is written per assembly in
Python.

- *Samples.* The defaults, every preset, the `min` and `max` of every range (the others at their
  defaults), every allowed value of an enum or a boolean, and every option of every internal variant.
  The nightly tier adds a seeded Latin hypercube sample of the box (`scipy.stats.qmc.LatinHypercube`,
  scrambled): every number a dimension over its range, every enum, boolean and variant selector a
  stratified discrete dimension. A constraint splits the box into branches — `exactly_one_of: [a, b]`
  into "a stated" and "b stated", `at_most_one_of` adds "neither", `requires` drops its infeasible
  corner — and every branch draws its own hypercube, so no sample is thrown away. A boundary that
  states an alternative moves the sample into that branch. Size and seed are the harness's, and the
  report records them.
- *Isolation run.* Each sample runs one simulated day (15 min steps) in a minimal system: the assembly
  as the import `subject`, plus a test partner for every port whose binding changes what it computes
  — a need's partner class, a circuit's other end, a carrier's provider (or, for a carrier it
  provides, a consumer; none for electricity), a fact's provider, something an observer port's class
  observes (`serves: {observed_by: <class>}`), and the controller a `controllable: {target_input: …}`
  output binds (`serves: {controls: <class>}`). The partners come from the `test_partners.yaml` of the
  library directory, site entries written as a file writes them, or — for a controller, whose
  priorities are a parameter — an import (`import: {assembly: control/ems_self_consumption}`); a port
  without one is refused, naming the class. Every run is checked for an exception, a NaN or infinity, an open energy balance
  (`EnergyBalanceError`), and every `bounds` entry (each timestep of the output's column; a KPI through
  the KPI finder by name, import and member). `expect` is checked on its preset's run; `monotone` moves
  its parameter across its range in 4 steps from every sample, everything else fixed, and the KPI must
  move that way within the golden gate's tolerance. Every failure is collected, the JSON report and a
  one-screen summary written, and the run then fails listing all of them.
- *Tiers.* The PR tier — the contract test and the deterministic samples — runs as
  `tests/assemblies/test_harness_pr_tier.py` on the mock library. The nightly tier, with the
  hypercube, runs beside the full-year goldens (job `assemblies-nightly` in `golden-year.yml`) and
  uploads its report.

```bash
python -m hisim.cli energy-system test-assemblies [--tier pr|nightly] [--library DIR]... \
    [--samples N] [--seed S] [--steps K] [--out DIR] [--shard i/n]
```

`--library` names the library directories (the search path when none is given), `--shard i/n` takes
every n-th assembly of the sorted library from the i-th, and `--out` receives the runs, one directory
per sample with its `isolation.energy_system.yaml`, plus `assembly_test_report.json` and
`assembly_test_summary.txt` (a fresh temporary directory when omitted). `--samples` and `--seed` set
the nightly hypercube (16 per branch, seed 20261003) and are refused with `--tier pr`. Exit 0 when
every check held, 1 when one failed, 2 for a command the harness cannot run as asked.
`tests/assemblies/mock_assemblies/wrong/` holds an assembly whose contract is deliberately wrong, which
the harness must fail by name.

**Errors.** The `EF-7x` band of `hisim/energy_system/errors.py`: `EF-70` … `EF-75` reading, resolving
and library-checking an assembly; `EF-76`/`EF-77` parameters and constraints; `EF-78`/`EF-79` a unit
mismatch and a fed field without a unit; `EF-7A` a required port without a partner, `EF-7B` several
candidates and no verb, `EF-7C` a required port declined, `EF-7D` a bound partner absent, `EF-7E` an
optional port with a candidate and no verb, `EF-7F` a verb on an inactive port, `EF-7G` an inner port
neither bound nor re-exported, `EF-7H` a partner the member's class declares no default connections
(or, for a meter, no default feed) from, `EF-7J` the contract check, `EF-7K` the evaluation order,
`EF-7L` a construct a later step lowers, `EF-7M` a verb naming no port; `EF-7N` two circuit ends
that do not fit (another medium, an output owned twice or by neither end, read by no member of the
other end); `EF-7P` a carrier need without exactly one provider, a verb on an electricity need, and
a fuel provider no need is bound to; `EF-7Q` a carrier that is not the need's (a provider's, or a
consuming output's energy carrier); `EF-7R` a fact port bound to, or finding, no provider of its
fact; `EF-7S` an observer's selection (a required selector or a whole selection matching nothing, a
ranked feed on an observer that is no controller, `observes:` on an import without an observer port,
a verb on an observer port); `EF-7T` the double count; `EF-7U` an actuation; `EF-7V` a controller's
priorities and weights; `EF-7W` two participants of one observer with one derived port name; `EF-7X`
a fact port whose cardinality is not the law's; `EF-7Y` a read only an internal contribution answers;
`EF-7Z` a fuel provider whose consumers need different fuel constants. A scalar fact with two
providers is the sizing engine's `EF-4B`, a many read's list naming nobody or one provider twice its
`EF-4G`; an output selected and fed explicitly to one observer is `EF-25`.

## Simulation-parameters files are shared, never duplicated

A recording does not write a parameters file of its own. It compares the parameters the setup
ended up with against every `*.simulation.yaml` here, references the one that says the same thing,
and only writes a new file when nothing matches — including nothing written earlier in the same
run, so two setups needing identical parameters share one file. The comparison is semantic: the
period, the resolution, the post-processing options as a set, the logging level, the country and
the year. Machine-specific fields take no part in it and are never written, `cache_dir_path` above
all, which eleven setups point at a cluster directory behind an existence probe.

A file written this way is named for its content — the horizon, the resolution and what its option
set is for, as in `one_week_minutely_kpis.simulation.yaml` — and never for the setup that first
needed it, because it is shared from the moment a second setup matches it. The freshness job also
asserts that no two files here describe the same run, so a duplicate cannot be added by hand.

## Relation to `system_setups/`

`system_setups/` is untouched and still holds HiSim's Python setups and their JSON twins; those
keep working exactly as before and are not going anywhere yet. This directory is where new,
declarative systems are written, and where the recorded twins of the old ones land.

`gas_boiler_household.energy_system.yaml` has a second life as a design document:
`roadmap/declarative_energy_systems/energy_system_mockup_minimal.yaml` is the normative mockup of
the file format, and the file here is its runnable, canonically written twin. A test asserts that
the two are identical once both are canonicalised, so a change to one has to be made to the other.
