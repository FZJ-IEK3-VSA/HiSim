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
            "source": {"import": null, "instance": null, "path": [], "member": "Building",
                       "assembly": null, "name": "Building", "display_name": "Building",
                       "label": null}}
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

## Assemblies: importing tested fragments (v1)

An **assembly** is a fragment of an energy system in a file of its own,
`<family>/<name>.assembly.yaml`: components whose names are local to it, parameters (each with a
type, a default, a description, and for a number a `unit` of `lt.Units` and a `range`),
`exactly_one_of` constraints (the defaults state exactly one member; an import that states one member
unstates the others' defaults, which resolve to `none`; a member written as `none` is an explicit
unstatement; one stating two is refused, `EF-77`),
internal variants a parameter selects (`selected_by`, `when:`), an interface of ports, and its test contract (`tests:` with `bounds`, `monotone` and `expect` at the
defaults). A file of `schema_version: 4` imports assemblies under `imports:`, once or as named
instances:

```yaml
imports:
  pv:     {assembly: pv/array, instances: {east: {azimuth_in_degree: 90}, west: {azimuth_in_degree: 270}}}
  heater: {assembly: heating/electric_heater, optional-bind: {ems_modifier: Ems}, installation_year: 2025}
```

Before anything else sees the file, the expansion of imports turns every import into ordinary
components of one flat version-3 file: the members are named by their structured address,
`pv-east-PVSystem` (`ComponentID.path`). It writes only overrides, as the recorder does for a twin: a
parameter substituted into a config field writes no line when it equals the value the member's preset
gives that field (without a preset: the field's default), nor when it is `AUTO` or `none` (G9, D28). The sequence in which the simulator adds them is set by an
optional flat integer `order:` on a site entry or an import, and nowhere else: the entries carrying
one come first, ascending (a number used twice is refused, `EF-7P`), then the others in file order,
site entries before imports; an import is one block, its instances in written order and its members
in the order the assembly writes them. The order is reviewed, because it moves results through
on/off decisions and forced convergence. A port is how anything crosses an assembly's boundary: a
**need** `{into: [Member], partner: Class}` binds to the one component of its partner class in the
file and lowers to a bare name at the member's `{$port: <need>}` placeholder (its default
connections from that class) or, with `wires:`, to explicit wires; a **provided** output `{output:
Member.Output}` is what a need names when it must read one output. A site entry declares its own
needs under `ports:`. Every port with a candidate is decided in the file, with one of three verbs
on the import or the site entry: `bind: {port: partner}` (the partner must exist), `optional-bind:
{port: partner}` (an optional port only: binds if it exists; on a required port it is refused,
`EF-7C`), `none: [port]` (declines an optional port). The partner of
either verb is an import or a component the file declares; it may be absent only because its group
is disabled or its variant option not selected, which leaves an `optional-bind:` unbound with that
reason in the record, and a name the file does not declare is refused (`EF-7D`), so a typo is never
read as a house without the partner. `required_when`/`active_when` switch a port by the parameters.
What cannot be decided — no partner, several and no verb, a verb on an inactive port — is refused
with `EF-7A` … `EF-7J`; the refusals that decide among candidates name them and a line to paste. The realized record of the run
carries the import record (assembly and its sha256, parameters as given and as resolved, variants,
addresses, every port's decision, `installation_year`/`quote`, the final sequence) and the source
map in its metadata, so `--rerun` reproduces it without any assembly; a record whose components no
longer stand in its stated sequence is refused (`EF-7P`).

Assemblies are found in `energy_systems/assemblies/` and then the directories of
`HISIM_ASSEMBLY_PATH`; a name found twice is refused. Every imported assembly passes the library
check first, which lists every problem of a file at once (`EF-75`): documentation, defaults, units
against the config fields they feed (`sized_field(unit=…)` or `field(metadata={"unit": …})`), a
parameter that does nothing, variants that do not partition their selector, ports naming missing
members, the test contract. `hisim energy-system describe <family>/<name>` prints an assembly's
interface, parameters and contract; `hisim energy-system schema` writes `assembly_v4.schema.json`
beside the energy-system schema.

Beyond needs, four kinds of port cross an assembly's boundary (`assemblies_spec.md` §3.2-§6):

- a **circuit end**, `{circuit: dhw, member: Boiler}` (a site entry writes `ports: {dhw: {circuit:
  dhw}}`), binds the one other end of the same circuit in the file — the circuit name is the medium,
  `dhw` or `space_heating`; by convention a `dhw` end's members declare the outputs `MassFlowDhw`,
  `SupplyTemperatureDhw` and `ReturnTemperatureDhw` (the mock members do), while the `space_heating`
  outputs are not declared yet — and lowers, in both directions, to a bare name of every member of the other
  end at every member carrying the port's `{$port: dhw}` placeholder; `bind:` decides several ends, an
  end of another circuit is refused;
- a **carrier need**, `{carrier: natural_gas, outputs: [Boiler.FuelUse]}`, needs the one provider of
  its carrier (an `lt.EnergyBalanceCarrier` value), `provides: {connection: {carrier: natural_gas,
  meter: Meter}}`. For a fuel each named consuming output lands at the meter's `{$port: connection}`
  placeholder as the feed the meter's class declares for it, written explicitly (`from`, tags, weight)
  by the wiring, which reads the constructed meter's declarations; a named output without a declared
  feed is refused (`EF-7N`), an unnamed one is not metered (D30), and each consuming output's energy
  port must carry the carrier (`EF-7M`). Electricity has no link and no meter on the provision: the need only requires the one grid
  connection. Two providers of one carrier, a need without one and a fuel provider nobody consumes from
  are refused;
- a **fact need**, `{fact: pv_peak_power_in_watt, into: [Battery]}`, is written only where the author
  must choose among providers or sum them (`many`); a scalar read with one provider in the file
  crosses the boundary by the engine's bare-fact rule and needs no port, as in the twins (D29). It
  lowers to a `sizing_sources` line naming the one provider in the file — a site entry whose class contributes the fact, or an
  import's provided fact `{fact: pv_peak_power_in_watt, member: PVSystem}`, which must be in that
  class's `SIZING_CONTRIBUTIONS` — and with `many: true` to the list of every provider, site entries
  first, then the imports and instances as written. A law sums such a list with
  `Sum(Many(Size.PV_PEAK_POWER_IN_WATT))`; a many read without a line sums every provider in the
  file, a list into a one-provider law or one provider into a sum is refused (`EF-4E`), and an empty
  or repeating list is `EF-4G`. The battery's class laws are sums and its preset `sized_to_pv` leaves
  both fields `AUTO`, so a battery is sized to every array; over one array the sum is that array's
  value, so every twin's numbers are unchanged;
- an **observer port**, `observes: {reading: {into: [Meter], default: declared}}`, and `observes:` on
  a site entry or an import (replacing its assembly's default): `declared` is every output the
  observer's class declares a dynamic default connection from among the components present —
  HiSim's `connect_automatically` — and a list of selectors (`{component_type: PV}`, `{flow:
  ELECTRICITY_PRODUCTION}`, `{output: TotalElectricityToOrFromGrid}`) filters it. The selection runs in
  the wiring, on the constructed observer, and the realized record writes the selected feeds as
  ordinary feeds, so `--rerun` selects nothing. A derived input or dispatch output name carries the
  source's runtime name with the address separator `-` mapped to `_` (`pv-east-PVSystem` feeds
  `ElectricityOutputFrompv_east_PVSystem`; `NameSyntax.port_name_part`). A controller — an observer whose class ranks a feed
  below weight 999 — ranks at its class's own weights (`DEFAULT_WEIGHTS`), a second device of one type
  at the next weight (a derived weight that reaches another type's weight is refused, `EF-7V`: pin the
  weight on the feed); a provided output names what it actuates, `controllable: {target_input:
  LoadingPowerInput}` or `{via: ems_modifier}`, and is ranked by exactly the one controller it binds.
  A meter reading the energy manager's balance and a flow the manager observes counts it twice and is
  refused, in every file.

| Code | Refused |
|---|---|
| `EF-70` … `EF-72` | an assembly file of the wrong shape, not found, found twice |
| `EF-73` | a construct v1 does not have, by name |
| `EF-75`, `EF-78`, `EF-79` | the library check (every problem at once), units |
| `EF-76`, `EF-77` | an import's parameter, a violated `exactly_one_of` |
| `EF-7A` … `EF-7F` | no partner, several and no verb, `none:` on a required port, an absent `bind:` partner, an undecided optional port, a verb on an inactive port |
| `EF-7G`, `EF-7H`, `EF-7J` | a verb on a port no verb binds, a bound output not read, a port that does not fit its members |
| `EF-7K`, `EF-7L` | a circuit end of another circuit; a carrier without exactly one provider, or an idle fuel provider |
| `EF-7M`, `EF-7N` | (wiring) a consuming output of another carrier; a named consuming output its meter declares no feed for |
| `EF-7S`, `EF-7T`, `EF-7U`, `EF-7V` | (wiring) an observer that cannot select or a selector matching nothing; a flow counted twice; a controllable output not actuated by exactly its one controller; a derived weight reaching another kind's base weight |
| `EF-4E`, `EF-4G` | (sizing) a sources line of the wrong cardinality; an empty or repeating many list, one fact read once and many-fold |

### Testing an assembly

Every assembly carries its test contract in its own file (`assemblies_spec.md` §9.4, D24), and pytest
runs it; nothing is written per assembly in Python:

```yaml
tests:
  bounds:     # an output in its unit, or a KPI of a member (without a member: the derived KPI of that name)
    - {output: PVSystem.ElectricityOutput, unit: WATT, min: 0, max: 20000}
    - {kpi: PV production, member: PVSystem, min: 0}
  monotone:   # one numeric parameter rises, everything else fixed: the KPI moves one way
    - {parameter: power_in_watt, kpi: PV production, member: PVSystem, direction: increasing}
  expect:     # a KPI at the defaults lies in a band
    - {kpi: PV production, member: PVSystem, min: 0, max: 100}
```

`tests/assemblies/test_library_contracts.py` draws the samples from the declarations — the defaults,
the `min` and `max` of every `range`, every allowed value, every internal variant, and a seeded Latin
hypercube per `exactly_one_of` branch (`--samples` points per branch, an upper bound for a branch whose
dimensions are all discrete, where two points drawing the same values are one sample) — and runs each
sample once in isolation: the assembly as one import beside a test partner for every active port whose
binding changes what the assembly computes (optional ports included; a providing port gets none, except
a consumer for a fuel it provides), and beside the provider of every sizing fact its members read from
the site without a fact port (the engine's bare-fact rule, as in the importing system, D29), one day at 900 s, the energy balance and `i_doublecheck` on. One
test per check kind reads that run: the run raised nothing (a failure names the innermost raising
`file.py:line`), the balance closed, every result column is finite, the member contract holds (every
energy or temperature output of a member has a `bounds` entry in its unit, every KPI named is one the
member reports; an energy manager's dispatch outputs, named by what the system gives it to steer, are
exempt), and every applicable `bounds` entry and, at the defaults, every `expect` entry holds; a
bounded column that is not numeric or not finite fails. Every `monotone` entry sweeps its parameter
across its range in four equidistant steps from the samples that admit a sweep, one sweep per distinct
point set, within the golden gate's tolerance; an int parameter takes the nearest integer of each step,
so its sweep yields up to four distinct values across the range. A failure is named by assembly,
sample, check and subject:
`mock/pv_array sample s003 bounds PVSystem.ElectricityOutput [WATT] in [0, 20000]: …`. A run's directory (under pytest's `tmp_path`) holds its `isolation.energy_system.yaml` and
parameters while its sample's checks run, and is deleted with the run once they are done.

```bash
pytest -m assemblies tests/assemblies/test_library_contracts.py            # the deterministic samples (PR gate)
                                                                           # of the mock and the real library
pytest -m nightly tests/assemblies/test_library_contracts.py --samples 16 --seed 20261003
pytest -m nightly -n 4 --dist loadgroup tests/assemblies/test_library_contracts.py   # shards, one run per sample
pytest tests/assemblies/test_library_contracts.py --assembly-library path/to/library # another library
```

Without `--assembly-library` the harness runs the mock library and the real one,
`energy_systems/assemblies/` with its `test_partners.yaml`. `assemblies` is the deterministic tier, run in the PR
gate's own job `pytest (assemblies)` (every test under `tests/assemblies/` carries that marker or `nightly`, and
needs the local LoadProfileGenerator); `nightly` is the hypercube, run by
the `assemblies-nightly` job of `golden-year.yml` (`--samples`, default 16 per branch, and `--seed`,
default 20261003). `--assembly-library DIR` tests another library: its assemblies and the
`test_partners.yaml` beside them, which names the site entry standing in for each partner class,
circuit end, carrier provider or consumer, fact provider, observed component and controller
(`hisim/energy_system/assemblies/testing/partners.py` documents the format; the mock library's file
is the example). A port no partner serves fails its sample's tests, naming the class it needs.

**Not in v1** (owner, 2026-10-06, D26): nesting (inner `imports`, `from:` re-exports, `internal:`
ports), `order:` paths and `order:` on an instance or an assembly member, presets inside
assemblies, `$switch`/`$fact`/`$derived` (`$param` is the one value placeholder),
`at_most_one_of`/`requires`, fact exports and the scoped-provider rule, `priorities`, `actuates` and
`feed:`/`required:` on selectors. Each is refused by name (`EF-73`): assemblies are flat, which the
first real assemblies need, and a cut feature returns as its own change when a real assembly needs
it. The spec is `roadmap/declarative_energy_systems/assemblies_spec.md` (§13.1); the mock library
the tests run on is `tests/assemblies/mock_assemblies/library/`.

**The real library** (§13 steps 4 and 5) is `energy_systems/assemblies/`: `heating/air_source_heat_pump`,
`heating/air_source_heat_pump_space_heating_only` (no DHW: a second assembly, D29),
`heating/gas_condensing_boiler`, `heating/oil_boiler`, `heating/pellet_boiler`, `heating/wood_chip_boiler`,
`heating/hydrogen_boiler` (one assembly per fuel), `heating/district_heating`, `heating/electric_resistive`,
`heating/solar_thermal` (the collectors and their pump controller, charging a cylinder's solar coil),
`dhw/indirect_cylinder`, `pv/array`, `storage/battery`, `control/ems_self_consumption`, `supply/electricity_grid`,
`supply/gas_connection`, `supply/oil_tank`, `supply/pellet_store`, `supply/wood_chip_store`,
`supply/hydrogen_connection` (one supply per fuel; the hydrogen connection's meter is a `GasMeter`, as in its twin) and
`supply/district_heating_connection`. `household_heatpump_building_sizer.composed.energy_system.yaml` is the site of
the heat-pump twin plus six of them (the heat pump with DHW, the cylinder, the array, the battery, the energy manager
and the grid); the composed files of the gas, oil, pellets, wood-chips and hydrogen-boiler twins
(`household_<fuel>_building_sizer.composed.energy_system.yaml`) are the same site plus seven (the fuel's boiler and its
supply in place of the heat pump). `household_district_heating_building_sizer.composed.energy_system.yaml` is the same
site plus seven (the connection feeding the heat distribution directly, without a buffer, and its heat meter; the
distribution's `NO_STORAGE_MASS_FLOW_FIX` position is a site line), and
`household_electric_heating_building_sizer.composed.energy_system.yaml` a site without heat distribution plus six (the
direct electric heater, which the `Building` reads through a need of its own, in place of the heat pump). The two
solar thermal twins have no composed file yet: their cylinder takes the collector on its primary coil and the
generator on its secondary coil, which a circuit end cannot lower to; the composed files, their gates and the reorder
of the heat-pump solar setup wait for hydronic stage C, whose ports on the cylinder's two coils derive the wires
(§13 step 5). The table
`hisim/energy_system/assemblies/twins.py` names every composed file, its twin and the rename of its members to the
twin's names. `tests/assemblies/test_twin_gates.py`, on `tests/assemblies/twin_gate.py`, runs one gate per row: it
expands the composed file, renames its members and asserts the twin outside the listed intended differences (G7, the
battery's one-element `sizing_sources` list, and, where the generator has a buffer, one neutral swap of the
sequence), and runs both for one day: every result column and KPI equal. The golden gate's `composed` mode runs each
composed file for the week and the full year and compares its KPIs, renamed through the same table, with the Python
setup's goldens (`golden_references/README.md`).

## Relation to `system_setups/`

`system_setups/` is untouched and still holds HiSim's Python setups and their JSON twins; those
keep working exactly as before and are not going anywhere yet. This directory is where new,
declarative systems are written, and where the recorded twins of the old ones land.

`gas_boiler_household.energy_system.yaml` has a second life as a design document:
`roadmap/declarative_energy_systems/energy_system_mockup_minimal.yaml` is the normative mockup of
the file format, and the file here is its runnable, canonically written twin. A test asserts that
the two are identical once both are canonicalised, so a change to one has to be made to the other.
