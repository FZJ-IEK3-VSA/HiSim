# Spec: every setup declares its kind, every validation rule lives in one rules package and says where it applies, and a rule a kind does not need is waived by name with a reason

Status: draft (proposed 2026-11-06); D1 decided (owner, 2026-11-06), Q1 to Q10 open; nothing is built. Owner: Noah.
Epic: proposed, hisim-setup-kinds-<hash> (P2, `core`), §12. Builds on: `roadmap/device_location_spec.md` (D-h: the
build rule "room heat needs a receiver", PRs #1211 and #1213), `roadmap/complete_energy_sankeys_spec.md` (D1 and
Q-B: a component declares its ports or is exempt with a reason; Q-H: the meter check is enforced only while nothing
is undeclared), `roadmap/fluid_loops_spec.md` (§3.8, the checks in one table), `roadmap/declarative_energy_systems/`
(the EF error catalogue), `system_docs/component_design_principles.md`, `system_docs/coding_style.md` §4 (no
module-level constants, no mutable module state), `system_docs/testing.md`. Code is cited against main at 18fd35b64;
the room-heat rule against PR #1211 as it stood on 2026-11-06.

## 1. Problem

**A rule that is right for a house refuses everything that is not a house.** The device-location rework adds the
build rule "room heat needs a receiver" (D-h of the device-location spec): a `PRODUCTION` port of carrier
`ROOM_HEAT` that no component reads is a transfer with a missing end, and the build refuses it before the first
step. The rule is right for a residential house: a tank in the heated zone, or the residents, heat the house, and a
house that does not take that heat is wired wrong. It is wrong for every system that runs an occupancy without a
building, and HiSim ships several: the demo setup `system_setups/dynamic_components.py` and its twin
`energy_systems/dynamic_components.energy_system.yaml` (weather, residents through `UtspLpgConnector`, PV, two
batteries, two fuel-cell CHPs, an energy management system, an electricity and a hydrogen meter, no building), the
test fixture `tests/energy_systems/two_pv_arrays.energy_system.yaml` (the same shape, two PV arrays), a dispatch
test, an assembly-crossing test, and the isolation runs of the assembly harness that place the occupancy partner
beside an energy manager or a grid meter without the building partner
(`hisim/energy_system/assemblies/testing/isolation.py`; the `Building` partner requires `UTSPConnector`, not the
other way round, `energy_systems/assemblies/test_partners.yaml`). PR #1211 would refuse all of them, so the owner
held it (2026-11-06) until a setup can say what it is and a rule can say where it applies.

**The rule is one of many.** The inventory of §6 lists every check HiSim applies today: 200 distinct rules, of
which about 155 refuse (an exception at load, build or after the run), 8 only log, 23 are CI gates or harness
contracts, and 14 check a recording against its run. Twenty-one of them encode a house (eight a household,
thirteen any heated building), and none of them says so. Examples: a heat
distribution system has mandatory inputs only a `Building` can feed (`HeatDistribution.__init__`,
`hisim/components/heat_distribution_system.py`), the assembly contract `check_room_heated` requires the day-mean
room air of every building within 1 K of its heating set point (`hisim/energy_system/assemblies/testing/checks.py`),
the assembly binding EF-7L requires exactly one provider per carrier (one site, one supply), the sizing vocabulary
is the house's (`SizingContext`, `hisim/config/context.py`: heating load, apartments, residents, roof area), and the
recorder refuses a component whose identity names a building or a unit (EF-R2), so a district cannot be recorded
at all. Every new rule faces the same choice the room-heat rule faces: written for every system, it refuses the
plant and the demo; not written, the house goes unchecked. The owner's request of 2026-11-06: "a lot of the rules
for making sure that a residential house is correct make total sense, but only for residential houses, and
applying them to everything will make HiSim unusable for anything except residential houses."

**Nothing lists the rules, and each one is wired into the module it happened to be written in.** The build rules
run from four places: `Simulator.run_all_timesteps` (`hisim/simulator.py`: `check_cost_declarations`, then
`connect_all_components`, `FluidLoopWalk.find_loops`, `Wiring.refuse_a_broken_wiring`), `EnergySystemExecutor.build`
(`hisim/energy_system/executor.py`: `validate_structure`, `validate_classes`, the sizing bridge, the wiring
checks), the component constructors (`EnergyPort.__post_init__`, `FluidLoopChannel.check_fits`, the config
`__post_init__` methods) and `PostProcessor.run` (`FiniteResultsCheck.require_finite`, `check_energy_balance`).
The fault lists of `Wiring.refuse_a_broken_wiring` live in `hisim/postprocessing/energy_balance/ports.py` (1,259
lines, which also hold the post-run collection), the loop walk in `hisim/fluid_loops.py`, the declarative wiring
checks in `hisim/energy_system/wiring_checks.py`, the document rules in `hisim/energy_system/validation.py`, the
meter check in `energy_balance/meters.py`. Whether a check refuses or logs is decided inside each one: the meter
balance and the unmetered-port check fail the run only while the run has no undeclared component (Q-H), computed
per run in `MeterCheck.__init__`; unlinked ports and undeclared components never fail; evaluation-order cycles
warn. No environment variable or parameter switches any of it. The owner's second requirement of 2026-11-06: the
rules are modular, each in its own unit in its own package, with the hooks in the loader, the simulator and the
post-processing only calling a registry.

Beads involved: hisim-device-location-820d (PR #1211 waits on this spec); the epic of §12 is new.

## 2. Decisions

D1 was taken with the request; the rest of the design is open and is put as questions, each with its options and a
recommendation, in §11. The table lists them all so the register is complete once the owner has answered.

| ID | Decision or question | Owner | Date |
|---|---|---|---|
| D1 | The validation rules are modular and live in one rules package, `hisim/rules/`: one module per rule family, each rule a self-contained unit that declares its id, what it checks, when it runs, whether it is universal and its scope; a registry collects them; the hooks in the loader, the simulator and the post-processing only call the registry; the existing checks move out of their modules into the package | Noah | 2026-11-06 |
| Q1 | Two classes of rule, universal (never waived) and domain (scoped), or one class with every rule waivable | open | – |
| Q2 | The default direction: every rule applies everywhere unless a waiver names (rule, kind, reason), or every rule lists the kinds it applies to | open | – |
| Q3 | One kind per setup, or two axes: the purpose of the setup and the use of each building | open | – |
| Q4 | The names: `purpose: case \| demo` and `use: residential \| commercial`, or the owner's four kinds | open | – |
| Q5 | A kind is a contract the run checks (a residential building has residents and a heater; a demo is never a RenoVisor base), not only a switch | open | – |
| Q6 | Where the declaration lives and that it is mandatory without a default | open | – |
| Q7 | A demo keeps its goldens (today `dynamic_components`, `simple_system_setup_one` and `_two` have five goldens each) or is never blessed | open | – |
| Q8 | What a parse fault is: a document the reader cannot read (EF-00 to EF-03, EF-07) stays in the reader; only a check on a readable document is a rule | open | – |
| Q9 | The per-step physics guards of the components (a tank's converged temperature in [0, 90] °C, a boiler's control signal in [0, 1]) stay in their components; the package holds the rules of the system, not of one device's model | open | – |
| Q10 | `simple_air_conditioner_household_building_sizer`, a residential case without residents: gets an occupancy, declares `demo`, or declares itself unoccupied | open | – |

**D1 — the validation rules are modular and live in one rules package, each rule a self-contained unit with its
id, its check, its stage, its universality and its scope; a registry collects them; the hooks only call the
registry; the existing checks move into the package** (owner, 2026-11-06). Today a rule's text, its trigger and its
enforcement are three decisions taken in whichever module the rule was written in, and nothing lists them (§1); a
rule that must say where it applies needs one place where that is said and one test that reads it. Consequence:
the package of §4.1; the migration of §8, one rule family per pull request after the mechanism; the inventory of
§6 names each rule's target module; every rule gets direct tests (§9).

## 3. Terms

- **Setup**: one simulated system, however it is written: a Python setup (`system_setups/*.py`, its
  `setup_function`), an energy-system file (`*.energy_system.yaml`), a test fixture, or an isolation system the
  assembly harness builds.
- **Kind**: what a setup declares about itself, from which the rules decide where they apply. With Q3 it is a pair:
  the **purpose** of the setup and the **use** of each of its buildings.
- **Purpose**: why the setup exists, one of `SetupPurpose.CASE` (a real case: its results are meant, its
  numbers may be read, quoted, blessed and built on) and `SetupPurpose.DEMO` (a demonstration or a test rig: it
  shows a mechanism, its numbers are not a statement about any house or plant). The file spells the lowercase
  values `case` and `demo`.
- **Building use**: what a building is used for, one of `BuildingUse.RESIDENTIAL` (people live in it: an
  occupancy, apartments, domestic hot water per person) and `BuildingUse.COMMERCIAL` (people work in it: usage
  profiles by zone type, no apartments). A building declares its use; a system without a building has none.
- **Plant**: a setup with no building (`electrolyzer_with_renewables`). It needs no kind of its own: a building
  rule has no building to apply to, and the purpose says whether its numbers are meant.
- **Rule**: one named check on one subject at one stage, with one message per fault. A rule has an **id**
  (`<family>.<name>`, for example `energy_ports.room_heat_needs_receiver`), a **family** (its module), a **stage**,
  a **scope** and, for the document rules, the **code** the message carries (`EF-31`).
- **Stage**: when a rule runs. `RuleStage.LOAD`: on the document, before any class is imported (declarative mode
  only). `RuleStage.BUILD`: on the built, wired components, before the first step, in both modes. `RuleStage.AFTER_RUN`:
  on the result table, in the post-processing, before anything is written.
- **Universal rule**: a rule that states a law of the model or a consistency the code relies on, and that no kind
  may waive: energy is conserved per component and per link, a fluid loop closes and has one pump owner, a port's
  unit is a power or an energy, a meter publishes what it reads, no result is NaN or infinite.
- **Domain rule**: a rule that states what a correct system of some kind looks like: residents need a house,
  room heat needs a receiver, a bought carrier passes a meter, a heated building reaches its set point. A domain
  rule is scoped.
- **Scope**: which subjects a domain rule applies to. `RuleScope.SYSTEM`: the whole setup, by purpose.
  `RuleScope.BUILDING`: each building, by its use (and the purpose).
- **Waiver**: one row of the waiver table, `Waiver(rule_id, purpose, use, reason)`: the named rule does not run for
  that kind, and the reason says why. A waiver names a domain rule; a waiver that names a universal rule fails the
  package's own test.
- **Registry**: the object that collects every rule of the package, answers which rules run at a stage for a
  kind, runs them and raises their faults as one error. It is built on demand, never stored in a module.
- **Fault**: one finding of one rule: the rule id, the subject (a component, an output, a file location) and the
  message. A stage's faults are raised together, as today's `EnergyPortWiringError` and `FluidLoopWiringError`
  list every fault at once.

## 4. Design

### 4.1 The rules package (D1)

```
hisim/rules/
  __init__.py        the package docstring: what a rule is, the stages, the scopes, how a rule is added
  rule.py            Rule (the base class), Fault, RuleStage, RuleScope, RuleViolation (the error)
  kinds.py           SetupPurpose, BuildingUse, SetupKind (the declared pair), KindContract rules (§4.4)
  registry.py        RuleRegistry: collect(), rules_for(stage, kind), run(stage, subject, kind)
  waivers.py         Waiver, Waivers.TABLE: every (rule, purpose, use, reason)
  inventory.py       RuleRecord, Inventory.RECORDS: every rule id with its family, stage, scope and where it lives
  subjects.py        the subject of each stage: LoadedDocument, BuiltSystem, RunResults
  document.py        the structural rules on a loaded document (EF-05, EF-06, EF-08, EF-11, ..., EF-57)
  bindings.py        classes, presets, constructors, config fields (EF-10, EF-13, ..., EF-1B, EF-43)
  sizing.py          the sizing faults the bridge classifies (EF-4A to EF-4X)
  wiring.py          the declarative wiring rules (EF-21 to EF-33, EF-7M, EF-7N, EF-7T, the warnings)
  assemblies.py      the import and port-binding rules (EF-70 to EF-7V, except those above)
  energy_ports.py    the fault lists of Wiring.refuse_a_broken_wiring and room_heat_needs_receiver
  fluid_loops.py     the refusals of FluidLoopWalk.find_loops
  components.py      the simulator's build rules: one kind base, duplicate names, cost declarations
  results.py         after the run: finite results, the component and link balance, the circuit faults
  meters.py          after the run: the meter balance, the unmetered ports
```

**A rule.** A subclass of `Rule` with class-scoped declarations and one method:

```python
class RoomHeatNeedsReceiver(Rule):
    """A PRODUCTION port of ROOM_HEAT that no component reads is a transfer with a missing end."""

    ID = "energy_ports.room_heat_needs_receiver"
    STAGE = RuleStage.BUILD
    SCOPE = RuleScope.SYSTEM
    UNIVERSAL = False

    def check(self, system: BuiltSystem) -> Sequence[Fault]:
        ...
```

`ID` is unique across the package (the registry's test checks it); `STAGE` picks the subject the rule receives;
`SCOPE` says whether the rule sees the whole system once or each building with its use; `UNIVERSAL = True` makes
the rule unwaivable (and `SCOPE` must then be `SYSTEM`). A document rule adds `CODE = "EF-31"`, which its messages
carry as today, so the error catalogue of `roadmap/declarative_energy_systems/` and every test that asserts on a
message stay valid. `check` returns faults and raises nothing; a rule that needs a value it does not have (a column
missing from the table) returns a fault that says so. The class holds no state between calls. Everything is
class-scoped or an `Enum` (`system_docs/coding_style.md` §4); the package writes no file and prints nothing, so the
semgrep gates (`.semgrep/file_writes.yml`, `.semgrep/no_print.yml`) need no entry.

**The subjects.** One frozen object per stage, built by the hook and handed to every rule of that stage:
`LoadedDocument` (the `EnergySystemFile` model and the raw document for locations), `BuiltSystem` (the wired
components in evaluation order, the declared kind, the buildings with their uses, the path resolver), `RunResults`
(the result table, the outputs, the declared ports, the kind). A rule reads the subject and nothing else: no
import of `hisim.components`, no `SimRepository`, so the package stays importable by the schema exporter and the
editor-side checks that import no component today (`hisim/energy_system/validation.py` keeps that property in its
new place, `tests/test_energy_system_layering.py` keeps proving it).

**The registry.** `RuleRegistry.collect()` walks the package's modules (`pkgutil.iter_modules` over `hisim.rules`)
and gathers every `Rule` subclass that declares an `ID`; it returns a registry object the caller keeps for the
calculation. Nothing is cached at module level (`coding_style.md` §4: no mutable module state); the simulator
holds the registry it built, as it holds its components. `registry.run(stage, subject, kind)` picks the rules of
the stage, drops those the waiver table waives for the kind, runs each, and raises one `RuleViolation` listing
every fault with its rule id, or returns the faults a rule declares as log-only (§4.5). The order of rules within
a stage is the order of the inventory, so the message is stable.

**The hooks.** Four call sites, each one line of logic:

| Stage | Hook | Replaces |
|---|---|---|
| `LOAD` | `hisim/energy_system/loader.py:load_energy_system`, after the reader built the model | `validate_structure` |
| `BUILD` (declarative) | `hisim/energy_system/executor.py:EnergySystemExecutor.build`, after `wire_energy_system` | `validate_classes`, the sizing bridge's classification, `WiringChecker`, `check_consuming_outputs`, `check_double_count` |
| `BUILD` (both modes) | `hisim/simulator.py:Simulator.run_all_timesteps`, after `connect_all_components` | `check_cost_declarations`, `FluidLoopWalk.find_loops`, `Wiring.refuse_a_broken_wiring` |
| `AFTER_RUN` | `hisim/postprocessing/postprocessing_main.py:PostProcessor.run`, first thing | `FiniteResultsCheck.require_finite`, the failing half of `EnergyBalanceReport` |

The declarative build rules run before the simulator's, as today; the simulator's run in both modes. The energy
balance *report* (the collection of ports, the Sankey, `balance_report.json`) stays in
`hisim/postprocessing/energy_balance/`: it computes, the rules of `results.py` and `meters.py` judge what it
computed. The component constructors' own refusals (`EnergyPort.__post_init__`, a config's `__post_init__`) are
not rules of the system and stay (Q9).

**The errors.** `RuleViolation(ValueError)` carries the faults. The existing error classes stay as subclasses so
that every `except` and every test keeps working: `EnergySystemFormatError`, `EnergySystemBindingError`,
`EnergySystemSizingError`, `EnergySystemWiringError`, `EnergySystemAssemblyError` (`hisim/energy_system/errors.py`),
`EnergyPortWiringError`, `FluidLoopWiringError`, `EnergyBalanceError`, `NonFiniteResultsError`. A family raises its
own subclass; the message formats do not change in the migration (§8: a migration PR moves code and changes no
message, so the tests that assert on messages prove the move).

### 4.2 Two classes of rule, and the default direction (Q1, Q2)

A rule is universal or domain (§3). Universal rules are the laws the model stands on; a run that breaks one is
wrong whatever it simulates, and no kind may waive it. Domain rules say what a correct system of a kind looks
like. The recommendation of Q2 is that a domain rule applies to every kind unless the waiver table names it: the
rule says nothing about kinds, the table says what is waived and why, and the generic test of §9 fails a waiver
that names a universal rule, a rule that does not exist, or a kind that does not exist. A forgotten entry then
fails loudly (a demo is refused by a rule it should not see, and the fix is one row with a reason) instead of
silently (a rule that lists kinds and forgets the new one runs nowhere for it). The pattern is the energy
balance's: a component declares its ports or is exempt with a reason on its class, and
`tests/test_energy_port_coverage.py` fails the one that does neither (`Exemptions`,
`hisim/postprocessing/energy_balance/ports.py`; `Component.ENERGY_PORTS_EXEMPT_BECAUSE`).

The waiver table, in `hisim/rules/waivers.py`:

```python
class Waivers:
    """Every domain rule a kind does not run, with the reason; a universal rule is never listed."""

    TABLE: ClassVar[Tuple[Waiver, ...]] = (
        Waiver("energy_ports.room_heat_needs_receiver", SetupPurpose.DEMO, None,
               "a demo may run an occupancy without a building; the heat it does not place is reported as unlinked"),
        Waiver("kinds.residential_building_has_residents", SetupPurpose.DEMO, None,
               "a demo building may stand empty"),
        ...
    )
```

A waiver is global per kind, never per setup: a per-setup waiver would be the switch the owner wants to avoid, and
a setup whose kind does not fit a rule declares another kind or fixes the system.

### 4.3 The kind: two axes (Q3, Q4)

**Purpose, per setup.** `case` or `demo`. It decides rigour: a case runs every rule its buildings' uses do not
waive; a demo runs the universal rules and the domain rules the table does not waive for demos. The owner's
`energy_system` kind is not needed: a plant is a case without a building, and the building rules have nothing to
apply to. (The name would also collide with the file suffix `*.energy_system.yaml`; if a third purpose is ever
needed, `plant` is the word.)

**Use, per building.** `residential` or `commercial`, a mandatory field `use` of `BuildingConfig`
(`hisim/components/building/config.py`), set by every preset to `RESIDENTIAL`; the file spells it under the
building's `config`. A building rule runs once per building with that building's use, so a district with a
residential and a commercial building (HiSim's identity model allows it: `ComponentID.building` and `.unit`,
`hisim/config/base.py`; the KPI post-processing groups by `ComponentID.building_label` and aggregates a district
through `DistrictNames.is_district`, `hisim/postprocessing/kpi_computation/compute_kpis.py`; no shipped setup has
two buildings, and the recorder refuses one, EF-R2) gets each building checked against its own use. A use is not
a purpose: a commercial case is as rigorous as a residential one, with other rules.

**What residential and commercial differ in (Q4, item 6 of the review).** HiSim's building is TABULA's: every
`building_code` is a row of the residential typology, and the occupancy is a household profile of the
LoadProfileGenerator. Commercial buildings differ in their sources, less in their wiring: the usage profiles come
by zone type (DIN V 18599-10's hours, people, equipment and lighting per m², instead of a household's persons and
devices), the design heating load is the same DIN EN 12831 steady state (`BuildingInformation.get_max_thermal_building_demand`,
`hisim/components/building/information.py`), domestic hot water is per use, not per person
(`HouseholdWarmWaterDemandConfig`, `hisim/components/configuration.py`, 2000 kWh/a), there are no apartments (the
sizing laws per apartment, `SimpleDHWStorageConfig.VOLUME_LAW` 250 l per apartment, the collector's 4 m² per
apartment, the boiler's 2,500 W per apartment floor, do not apply) and the comfort band is the working hours'. So
the rules that differ by use are the occupancy and demand rules (a residential building has a household occupancy;
a commercial one has a usage profile), the per-apartment sizing rules (residential only), the KPIs per apartment
(residential only) and the comfort rules (set points by use); the wiring, port, loop, meter and balance rules are
the same. Until HiSim has a commercial archetype and a commercial profile source, the rule
`kinds.building_use_has_a_source` (§4.4) refuses `commercial`: the value exists so that the scoping is designed
once, and a declaration nobody can honour fails instead of running a TABULA house with an office label.

### 4.4 A kind is a contract (Q5)

Declaring a kind states what the setup promises, and a rule checks each promise, so a wrong declaration is caught
where it matters. The contract rules live in `hisim/rules/kinds.py`:

| Rule | Promise | Scope |
|---|---|---|
| `kinds.purpose_declared` | every setup declares its purpose; an undeclared one is refused at build (both modes) | universal |
| `kinds.building_use_declared` | every building declares its use | universal |
| `kinds.building_use_has_a_source` | a building's use has an archetype and a profile source in HiSim (today: residential only) | universal |
| `kinds.residential_building_has_residents` | a residential building reads an occupancy's heat and its hot-water draw (today the building only warns, `Building.warn_about_unconnected_internal_heat_gains`, and `simple_air_conditioner_household_building_sizer` runs without one) | building, residential; waived for `demo` |
| `kinds.residential_building_is_heated` | a residential building reads a heat delivery (a heat distribution system or a direct electric heater) or declares that it is unheated | building, residential; waived for `demo` |
| `kinds.residents_have_a_house` | an occupancy's `ROOM_HEAT` outputs are read by a building (the room-heat rule covers this once it exists; listed for the case without it) | system; waived for `demo` |
| `kinds.demo_is_no_base` | a RenoVisor base file (`hisim/renovisor/translate.py` picks a recorded grouped twin by generator) is a `case`; a test partner (`energy_systems/assemblies/test_partners.yaml`) comes from a `case` | universal (checked by the RenoVisor build and the assembly library check, not at run time) |

Whether a demo is blessed into the goldens is Q7.

### 4.5 Enforcement: refuse, or log

A rule declares `ENFORCED = True` (its faults raise) or `False` (its faults are logged and reported, as the balance
reports unlinked ports and undeclared components today, `EnergyBalanceReport.conclude`,
`hisim/postprocessing/energy_balance/export.py`). The per-run switch of Q-H (the meter balance and the unmetered
ports fail only while no component is undeclared) stays a property of those two rules, computed from the subject,
not a kind: an undeclared component makes the meter's reading unverifiable whatever the kind. The acceptance that
turns "unlinked" into a failure (PR 6 of the complete Sankey spec) then flips one attribute.

### 4.6 Where the kind is declared and how it reaches the twin (Q6)

- **Energy-system file**: a new top-level key `purpose: case | demo`, mandatory in a v3 and v4 energy-system file
  (not in an assembly file, whose top level already has `kind: assembly`, `AssemblyFile.KIND`,
  `hisim/energy_system/assemblies/model.py`; hence the key is `purpose`, not `kind`). It joins
  `EnergySystemFile.TOP_LEVEL_KEYS` (`hisim/energy_system/model.py`, `extra="forbid"`), the reader
  (`EnergySystemReader._build`, EF-03 for an unknown key), the emitter's order
  (`hisim/energy_system/emitter.py`), the schema export (`SchemaExporter._top_level`,
  `hisim/energy_system_v3.schema.json`, `tests/test_energy_system_schema.py` requires regeneration in the same
  change) and the executor, which copies it onto the simulator as it copies `description` and `scenario_name`
  (`hisim/energy_system/executor.py`). A file without it is refused at load (EF-03's family: a missing required key).
- **Building use**: the field `use` of `BuildingConfig`, in the file under the building's `config`, written by
  every twin since a preset sets it and the recorder writes what differs from the preset (so a twin on a preset
  shows nothing, a hand-authored file states it or inherits the preset's).
- **Python setup**: `my_sim.declare_purpose(SetupPurpose.CASE)` as the first line of `setup_function`; the
  simulator's `run_all_timesteps` refuses a setup that has not declared (`kinds.purpose_declared`). Not a module
  constant (`coding_style.md` §4) and not a `SimulationParameters` field: the parameter files are a shared library,
  any system runs from any of them (`simulation_parameters/README.md`), and the recorder's design says which system
  a parameter set runs with is nobody's business (`RecordingSession.PARAMETERS_DIRECTORY`,
  `hisim/energy_system/recording/session.py`).
- **The recorder**: `SystemObserver.observe` (`hisim/energy_system/recording/observe.py`) reads the simulator's
  purpose into `RecordedSystem`, `EnergySystemBuilder.build` writes it, and the freshness gate
  (`scripts/record_all_setups.py --check`, `.github/workflows/energy-system-freshness.yml`) re-records every twin
  once with the new key.
- **Fixtures and the harness**: the test fixtures state their purpose in the file; the assembly harness declares
  `demo` for every isolation system it builds (`isolation_document`, `hisim/energy_system/assemblies/testing/isolation.py`),
  since an isolation system is a rig by construction. The composed twins and `uc1.realized` state `case`.
- **RenoVisor**: the translator reads the base's purpose and refuses a `demo` (`kinds.demo_is_no_base`); every
  request is residential, so the use it writes is `residential`, and the capability document says so.

### 4.7 The room-heat rule lands scoped

`energy_ports.room_heat_needs_receiver` (D-h of the device-location spec) is the first rule written directly into
the package: stage `BUILD`, scope `SYSTEM`, domain, enforced, waived for `demo` with the reason "a demo may run an
occupancy without a building; the heat it does not place is reported as unlinked". Consequences for the systems
of §1: `dynamic_components` declares `demo` (its residents' room heat has no reader; the balance lists the two
outputs as unlinked, as today); `two_pv_arrays` declares `demo`; the dispatch test and the assembly-crossing test
declare `demo` or get a building, whichever the test is about; the isolation runs are `demo` by construction
(§4.6). Every case twin has a building and is not affected. PR #1211 is rebased onto the mechanism PR and adds the
rule to `energy_ports.py` instead of a fourth fault list in `Wiring.refuse_a_broken_wiring`.

## 5. What changes and what does not

- **Results**: no number moves. Every migration PR keeps results and messages bit-identical; the goldens
  (`golden_references/`) are not re-blessed by this epic. The kind contracts of §4.4 may refuse a setup that runs
  today (`simple_air_conditioner_household_building_sizer` without an occupancy, §7); the fix is in that setup's PR
  and moves its results, which that PR measures and blesses.
- **Public interfaces**: the file gets a mandatory top-level key `purpose`; `BuildingConfig` gets a mandatory field
  `use` (every preset sets it, so a file on a preset changes nothing); `Simulator.declare_purpose`; the error
  classes keep their names and messages, and gain a base `RuleViolation` with the faults' rule ids. The heads-up
  on renovisorissues (`system_docs/results_and_interfaces.md` §5): the translator writes `purpose: case` into every
  calculation's file and `use: residential` into the building; a request carries neither.
- **Goldens and twins**: every twin is re-recorded once for the `purpose` key (freshness gate); the goldens are
  unchanged (the KPI leaves do not carry the key).
- **Modules that shrink**: `hisim/postprocessing/energy_balance/ports.py` loses the fault lists of `Wiring`,
  `hisim/fluid_loops.py` its refusals (the walk's discovery of loop ids stays, the balance needs it),
  `hisim/energy_system/wiring_checks.py` and `validation.py` go whole, `hisim/simulator.py` loses
  `check_cost_declarations`, `hisim/postprocessing/finite_results.py` goes whole.

## 6. The rule inventory

Every check HiSim applies today, by family. Columns: the proposed id (or today's name where the rule is not moved);
what it checks; where it lives today (file, `Class.method`); when it runs; whether it refuses (E) or only logs (L);
the proposed class; the target module in `hisim/rules/`. The classes: **U** universal, unwaivable; **S** system
rule, every purpose, waivable for `demo` by the table; **B** building rule, residential and commercial, scoped by
use; **R** residential only; **P** a parse fault, stays in the reader (Q8); **K** a device's own physics guard,
stays in the component (Q9); **C** a CI gate or harness contract, listed with the scope it gets. Paths are relative
to the repository root; `es/` is `hisim/energy_system/`, `eb/` is `hisim/postprocessing/energy_balance/`,
`cmp/` is `hisim/components/`.

### 6.1 Reading the document (declarative mode, stage LOAD)

| Rule | Checks | Lives today | When | E/L | Class | Target |
|---|---|---|---|---|---|---|
| EF-00 | the file suffix is `.yaml`/`.yml` | `es/document.py:RawDocument._read_text`; `es/executor.py:SimulationParametersReader.read` | load | E | P | stays |
| EF-01 | `schema_version` present and in (3, 4); a v3 file has no `imports`; an assembly file is v4 | `es/loader.py:EnergySystemReader._check_schema_version`; `es/assemblies/reader.py:AssemblyReader.build` | load | E | P | stays |
| EF-02 | no YAML key written twice | `es/document.py:RawDocument._reject_duplicate_keys` | load | E | P | stays |
| EF-03 | readable YAML, a mapping, no unknown top-level key, some components | `es/document.py:RawDocument._parse`; `es/loader.py:EnergySystemReader._build` | load | E | P (a missing `purpose` joins it) | stays |
| EF-07 | a block has the YAML type expected; a simulation-parameters file, address table or `consuming` block is well-formed | `es/document.py:RawDocument.malformed`; `es/address_table.py`; `es/executor.py:SimulationParametersReader` | load / build | E | P | stays |
| `document.no_absolute_path` (EF-05) | no absolute filesystem path in a value | `es/validation.py:StructuralValidator._check_no_absolute_paths` | load, again at build | E | U | `document.py` |
| `document.reference_syntax` (EF-06) | no wildcard or relative reference | `es/names.py:NameRules._reference_error` | load | E | U | `document.py` |
| `document.valid_name` (EF-08) | a component, group, variant or option name is an identifier | `es/names.py:NameRules.check_identifier`; `es/validation.py:StructuralValidator._check_expanded_names` | load | E | U | `document.py` |
| `document.no_metadata_on_a_plain_run` (EF-09) | a file with `metadata:` (a run record) is only re-run | `es/executor.py:EnergySystemExecutor.check_metadata` | build | E | U | `document.py` |
| `document.one_configuration_origin` (EF-11, EF-12) | an entry names a preset or a constructor, never both, and at least one of preset, constructor, config | `es/validation.py:StructuralValidator._check_configuration_origin` | load | E | U | `document.py` |
| `document.constructor_block` (EF-14) | a constructor block names exactly one constructor | `es/entries.py:EntryReader._constructor` | load | E | U | `document.py` |
| `document.known_entry_keys` (EF-18) | an entry, group, variant or option uses only its closed key set | `es/entries.py:EntryReader.entry`; `es/loader.py:EnergySystemReader._check_block_keys` | load | E | U | `document.py` |
| `document.input_item_shape` (EF-19) | an input item is a defaults, wire or feed item | `es/entries.py:EntryReader._check_item_keys` | load | E | U | `document.py` |
| `document.known_source` (EF-20) | an input item names a component the selected world has | `es/validation.py:StructuralValidator._check_reference_closure` | load | E | U | `document.py` |
| `document.one_input_spelling` (EF-24) | one consumer-source pair is spelled as wires or as feeds, not both; no defaults item repeated | `es/validation.py:StructuralValidator._require_new_spelling` | load | E | U | `document.py` |
| `document.no_duplicate_feed` (EF-25) | one source output fed once into an aggregator (file level) | `es/validation.py:StructuralValidator._check_input_shapes` | load | E | U | `document.py` |
| `document.no_duplicate_wire` (EF-26) | one input wired once (file level) | same | load | E | U | `document.py` |
| `document.known_sizing_source` (EF-40, EF-41) | `sizing_sources` names an existing component and the fact it maps | `es/validation.py:StructuralValidator._check_reference_closure` | load | E | U | `document.py` |
| `document.no_nested_group` (EF-50) | an entry carries no group keys | `es/entries.py:EntryReader.entry` | load | E | U | `document.py` |
| `document.one_group_per_component` (EF-51) | a component is in at most one group | `es/validation.py:StructuralValidator._check_names` | load | E | U | `document.py` |
| `document.unique_name` (EF-52) | component, group, variant and expanded import names do not collide | `es/validation.py:StructuralValidator._check_names`; `es/assemblies/expansion.py:ImportExpander._check_names` | load / build | E | U | `document.py` |
| `document.group_enabled_flag` (EF-53) | a group's `enabled` is a bool | `es/loader.py:EnergySystemReader._build_groups` | load | E | U | `document.py` |
| `document.group_not_empty` (EF-54) | a group lists a component | same | load | E | U | `document.py` |
| `document.known_variant_option` (EF-55) | `selected` names an option | `es/loader.py:EnergySystemReader._build_variants` | load | E | U | `document.py` |
| `document.variant_not_empty` (EF-56) | a variant offers an option | `es/loader.py:EnergySystemReader._build_options` | load | E | U | `document.py` |
| `document.one_variant_per_component` (EF-57) | a component is declared by at most one variant | `es/validation.py:StructuralValidator._check_option_member` | load | E | U | `document.py` |
| `document.not_cut_from_v1` (EF-73) | a construct cut from v1 is refused by name | `es/imports_reader.py:CutConstructs.error` | load | E | U | `document.py` |

### 6.2 Classes, presets, configuration and sizing (declarative mode, stage BUILD)

| Rule | Checks | Lives today | When | E/L | Class | Target |
|---|---|---|---|---|---|---|
| `bindings.class_importable` (EF-10) | the dotted class path imports a HiSim component with an annotated config | `es/classes.py:ClassBinder._import_component_class` | build | E | U | `bindings.py` |
| `bindings.known_preset` (EF-13) | the preset is declared by the config class | `es/bindings.py:ConfigOriginChecker.resolve_preset` | build | E | U | `bindings.py` |
| `bindings.known_constructor` (EF-15) | the constructor is declared by the config class | `es/bindings.py:ConfigOriginChecker.resolve_constructor` | build | E | U | `bindings.py` |
| `bindings.constructor_arguments` (EF-16) | no unknown and no missing required argument | `es/bindings.py:ConfigOriginChecker.check_constructor_arguments` | build | E | U | `bindings.py` |
| `bindings.known_config_field` (EF-17) | every config key is a field; `component_id` is not set in `config` | `es/bindings.py:ConfigOriginChecker.check_config_keys` | build | E | U | `bindings.py` |
| `bindings.decodable_value` (EF-1A) | a value decodes into its field's type | `es/codec.py:ConfigValueCodec.*`; `es/configure.py:EntryConfigurator._realize_origin` | build | E | U | `bindings.py` |
| `bindings.auto_only_on_a_sized_field` (EF-1B) | `AUTO` only on a field with a sizing law | `es/bindings.py:ConfigOriginChecker.check_config_keys` | build | E | U | `bindings.py` |
| `bindings.sizing_fact_is_read` (EF-43) | `sizing_sources` names a fact the class's laws read | `es/bindings.py:ConfigOriginChecker.check_sizing_facts` | build | E | U | `bindings.py` |
| `bindings.path_variable_known` (EF-04) | a `${name}` names a registered path variable | `es/path_resolver.py:PathResolver._substitute` | build | E | U | `bindings.py` |
| `sizing.fact_provided` (EF-4A) | a fact a law reads is provided by somebody | `hisim/config/engine.py`, classified by `es/sizing_bridge.py:KernelFailure.classify` | build | E | U (the vocabulary is the house's, §4.3; the rule is not) | `sizing.py` |
| `sizing.provider_unambiguous` (EF-4B) | several providers need a source line | same | build | E | U | `sizing.py` |
| `sizing.source_is_a_provider` (EF-4C) | a source line names a component that declares the fact | same | build | E | U | `sizing.py` |
| `sizing.fact_not_null` (EF-4D) | the provider gives a value | same | build | E | U | `sizing.py` |
| `sizing.source_shape` (EF-4E) | the mapping shape fits the law's cardinality | same | build | E | U | `sizing.py` |
| `sizing.no_field_cycle` (EF-4F) | sibling `Self(...)` reads form no cycle | same | build | E | U | `sizing.py` |
| `sizing.one_read_shape` (EF-4G) | a fact is read many-fold or once, not both; no repeated provider | same | build | E | U | `sizing.py` |
| `sizing.unique_instance` (EF-4H) | every config has `component_id.name`, no two share it | `hisim/config/engine.py:SizingFactEngine.register` | build | E | U | `sizing.py` |
| `sizing.sized_together` (EF-4J) | a `SizedTogether` group is stated whole or not at all | `hisim/config/sizing.py:SizedTogether.check` | build | E | U | `sizing.py` |
| `sizing.resolved` (EF-4X) | the kernel made progress and no law raised | `es/sizing_bridge.py:KernelFailure.classify` | build | E | U | `sizing.py` |
| `sizing.unconsumed_fact` | a provided fact nobody reads | `es/sizing_bridge.py:unconsumed_warnings` | build | L | S | `sizing.py` |
| `sizing.sizing_source_enabled` (EF-42) | a scalar sizing source is not in a disabled group or unselected option | `es/groups.py:GroupExpander._dangling_scalar_error`; `es/assemblies/addresses.py:_rewrite_sizing` | build | E | U | `sizing.py` |

The sizing kernel's own refusals (`hisim/config/sizing.py`, `laws.py`, `engine.py`, `contributions.py`,
`context.py`: `concrete()` on an unresolved field, `NothingToSizeError`, a `Self()` read before its law, a
`FactContribution` naming a fact the context lacks, a null fact, a deadlock) are what the bridge classifies into
EF-4A to EF-4X; they are the kernel's and stay, the rule is the classification. The config-class rules at class
creation (`ConfigBase.__init_subclass__`: `MAIN_CLASS` present and resolvable; `hisim/config/presets.py`:
`preset_<wire>` naming, `@preset` above `@classmethod`, the builder signature; `_refuse_ambiguous_enum_spellings`)
run when HiSim is imported, concern the code and not a setup, and stay (U, not moved), covered by
`tests/test_config_contracts.py`, `test_presets.py`, `test_config_main_class.py`.

### 6.3 Wiring (declarative mode, stage BUILD)

| Rule | Checks | Lives today | When | E/L | Class | Target |
|---|---|---|---|---|---|---|
| `wiring.output_exists` (EF-21) | a wire, feed or consuming output names an existing output; a written wire never names a derived one; default feeds are unambiguous | `es/wiring_checks.py:WiringChecker._check_ports_exist`, `_check_written_wires_avoid_derived_ports`; `es/feed_resolution.py`; `es/aggregator_ports.py:AggregatorPortChecker.check_ports_were_created` | build | E | U | `wiring.py` |
| `wiring.input_exists` (EF-22) | the same for inputs | same | build | E | U | `wiring.py` |
| `wiring.defaults_declared` (EF-23) | a bare defaults item has default connections declared for the source class | `es/wiring.py:WiringPlanner._expand_default_item` | build | E | U | `wiring.py` |
| `wiring.no_input_fed_twice` (EF-26) | after default expansion, one input has one wire | `es/wiring_checks.py:WiringChecker._check_no_input_is_fed_twice` | build | E | U | `wiring.py` |
| `wiring.feed_unique` (EF-25) | one participant output enters an aggregator once | `es/aggregator_ports.py:AggregatorPortChecker.check_participant_ports_are_unique` | build | E | U | `wiring.py` |
| `wiring.target_is_an_aggregator` (EF-27) | feeds go to a component with dynamic channels | `es/feed_resolution.py:DynamicConnectionResolver._require_channels` | build | E | U | `wiring.py` |
| `wiring.channel_matches` (EF-28) | a feed's tags match exactly one channel | `es/channel_matching.py:ChannelMatcher.match` | build | E | U | `wiring.py` |
| `wiring.dispatch_rule` (EF-29) | a dispatch block where and only where the channel dispatches; no rank on a forbidding channel; allowed dispatch tags | `es/channel_matching.py:ChannelMatcher._check_dispatch_rule`, `_check_dispatch_tags` | build | E | U | `wiring.py` |
| `wiring.known_tag` (EF-2A) | a component-type or flow tag is a known enum member | `es/channels.py:TagDecoder.component_type`, `flow_tags` | build | E | U | `wiring.py` |
| `wiring.dispatch_signal_unique` (EF-2B) | two feeds do not claim one control signal | `es/dispatch_signals.py:DispatchSignalPlanner._refuse_if_claimed` | build | E | U | `wiring.py` |
| `wiring.port_types_agree` (EF-30) | load type and unit agree across a wire or feed (`ANY` matches) | `es/wiring_checks.py:WiringChecker._check_port_types_agree`; `es/channel_matching.py:ChannelMatcher._check_port_types` | build | E | U | `wiring.py` |
| `wiring.mandatory_input_connected` (EF-31) | a mandatory input is fed; `allow_unconnected_mandatory` only logs | `es/wiring_checks.py:WiringChecker._check_mandatory_inputs_are_connected` | build | E (L when allowed) | U (the inputs that make a device need a building are listed in §6.8) | `wiring.py` |
| `wiring.port_name_free` (EF-32) | feed resolution creates no port name that exists | `es/aggregator_ports.py:AggregatorPortChecker.check_port_names_are_free` | build | E | U | `wiring.py` |
| `wiring.constructed` (EF-33) | a component constructor did not raise | `es/wiring.py:ComponentBuilder._construct` | build | E | U | `wiring.py` |
| `wiring.consuming_output_carrier` (EF-7M) | a consuming output's energy port carries the need's carrier | `es/wiring_checks.py:check_consuming_outputs` | build | E | U | `wiring.py` |
| `wiring.meter_feed_count` (EF-7N) | the meter's class declares exactly one feed per consuming output | `es/wiring_checks.py:meter_feed_of` | build | E | U | `wiring.py` |
| `wiring.no_double_count` (EF-7T) | an aggregator does not read another aggregator's output while both observe one flow | `es/wiring_checks.py:check_double_count` | build | E | U | `wiring.py` |
| `wiring.unconsumed_source` | a component feeds nobody | `es/wiring_checks.py:unconsumed_sources` | build | L | S | `wiring.py` |
| `wiring.unmetered_output_of_a_need` | a metered consumer's other outputs of the same carrier are named in the need | `es/wiring_checks.py:unmetered_outputs` | build | L | S | `wiring.py` |

### 6.4 Assemblies and imports (declarative mode, stage BUILD)

| Rule | Checks | Lives today | When | E/L | Class | Target |
|---|---|---|---|---|---|---|
| EF-70 | an assembly or `imports` block is well-formed; the assembly's `kind` is `assembly` | `es/imports_reader.py:ImportsReader.shape_error`; `es/assemblies/reader.py:AssemblyReader.build` | load / build | E | P | stays |
| `assemblies.found` (EF-71) | the assembly is in one library directory | `es/assemblies/resolver.py:AssemblyResolver.resolve` | build | E | U | `assemblies.py` |
| `assemblies.found_once` (EF-72) | not in two | same | build | E | U | `assemblies.py` |
| `assemblies.library_valid` (EF-75) | the assembly file passes the library check (the 45 problems `LibraryChecker.check` lists: name equals path, members load, parameters described with defaults, units and ranges, constraints consistent, variants cover the selector, ports and placeholders name members, a `tests:` contract with bounds, monotone and expect entries) | `es/assemblies/library.py:LibraryChecker.check`, `require_valid` | build; CI `tests/assemblies/test_assembly_library.py` | E | U | `assemblies.py` (the checker moves whole; its problems are one rule with 45 messages) |
| `assemblies.parameter_valid` (EF-76) | an import parameter has the type, value and range its declaration states | `es/assemblies/parameters.py:select` | build | E | U | `assemblies.py` |
| `assemblies.constraint_holds` (EF-77) | `exactly_one_of` / `all_or_none_of` hold | same | build | E | U | `assemblies.py` |
| `assemblies.parameter_unit` (EF-78, EF-79) | a parameter's unit equals its field's; a fed field has a unit | `es/assemblies/library.py:LibraryChecker._unit` | build / CI | E | U | `assemblies.py` |
| `assemblies.port_has_a_partner` (EF-7A) | a required port finds a candidate; a many-fact port a provider | `es/assemblies/binding.py:PortBinder.decide` | build | E | U | `assemblies.py` |
| `assemblies.port_unambiguous` (EF-7B) | several candidates need a verb | same | build | E | U | `assemblies.py` |
| `assemblies.required_port_not_declined` (EF-7C) | no `none:` on a required port | same | build | E | U | `assemblies.py` |
| `assemblies.bound_partner_exists` (EF-7D) | the bind target exists | same | build | E | U | `assemblies.py` |
| `assemblies.optional_port_decided` (EF-7E) | an optional port with candidates carries a verb | same | build | E | U | `assemblies.py` |
| `assemblies.verb_on_an_active_port` (EF-7F) | no verb on a switched-off port | same | build | E | U | `assemblies.py` |
| `assemblies.known_port` (EF-7G) | a verb names an existing port of the right kind | `PortBinder.bind` | build | E | U | `assemblies.py` |
| `assemblies.bound_output_is_read` (EF-7H) | a bound provided output is one the port's wires name | `PortBinder.provided` | build | E | U | `assemblies.py` |
| `assemblies.port_contract` (EF-7J) | a port fits its members (carrier, fact, circuit lowering, observer, controllables); placeholders have counterparts | `PortBinder._carrier`, `_land_fact`, `_lower_circuit`, `_observe`, `_plan_controllables`; `es/assemblies/expansion.py:ImportExpander` | build | E | U | `assemblies.py` |
| `assemblies.circuit_ends_match` (EF-7K) | both ends of a circuit binding name one circuit, written consistently | `PortBinder._joined`, `resolve` | build | E | U | `assemblies.py` |
| `assemblies.one_provider_per_carrier` (EF-7L) | a carrier has exactly one provider; a provider has a bound consumer; a required need a provider | `PortBinder._carrier`, `bind` | build | E | **S** (one site with one supply per carrier: a district or a plant with two grid connections is refused; today no setup has two) | `assemblies.py` |
| `assemblies.order_valid` (EF-7P) | an evaluation `order` names each number once; recorded members follow the record's sequence | `es/assemblies/addresses.py:sequence`; `expansion.py:_check_recorded_sequence` | build | E | U | `assemblies.py` |
| `assemblies.observer_selection` (EF-7S) | an `observes:` selector resolves | `es/assemblies/selection.py:SelectionPlan._select` | build | E | U | `assemblies.py` |
| `assemblies.actuation_selection` (EF-7U) | a controllable selection resolves | `SelectionPlan.__call__` | build | E | U | `assemblies.py` |
| `assemblies.weight_free` (EF-7V) | a derived weight reaches no other kind's base weight | `SelectionPlan._refuse_collision` | build | E | U | `assemblies.py` |

### 6.5 The built system (both modes, stage BUILD, before the first step)

| Rule | Checks | Lives today | When | E/L | Class | Target |
|---|---|---|---|---|---|---|
| `components.one_kind_base` | the class inherits exactly one of the five kind bases | `hisim/component.py:ComponentKinds.of`, from `Simulator.add_component` | build | E | U | `components.py` |
| `components.unique_name` | no second component with one `component_id.key` | `hisim/simulator.py:Simulator.add_component` | build | E | U | `components.py` |
| `components.unique_output_key` | no two outputs with one full name | `hisim/component_wrapper.py:ComponentWrapper._register_output` | build | E | U | `components.py` |
| `components.has_outputs` | a component registered at least one output | `ComponentWrapper.register_component_outputs` | build | E | U | `components.py` |
| `components.name_is_identity` | the constructor name equals `config.component_id.key`; the name is an identifier; the config is a `ConfigBase`; no `AUTO` left; parameters set | `hisim/component.py:Component.__init__` | build (construction) | E | U | stays (constructor contract, Q9) |
| `components.connect_input_target` | `connect_input` names an existing input, once | `hisim/component.py:Component.connect_input` | build | E | U | `components.py` |
| `components.default_connections_wellformed` | a default-connection list is non-empty, from one source class, and the connections asked for exist | `Component.add_default_connections`, `get_default_connections`; `hisim/dynamic_component.py` | build | E | U | `components.py` |
| `components.load_type_agrees` | input and output share a load type (`ANY` matches) | `hisim/component_wrapper.py:ComponentWrapper.connect_inputs` | build | E | U | `components.py` (one rule with EF-30) |
| `components.unit_agrees` | concrete units agree; `Units.ANY` warns | same | build | E / L | U | `components.py` |
| `components.mandatory_input_connected` | a mandatory input has a source (`allow_unconnected_mandatory` warns) | same | build | E / L | U (see §6.8) | `components.py` (one rule with EF-31) |
| `components.auto_connect_possible` | automatic connection finds default connections and one matching source | `Simulator.connect_everything_automatically`, `connect_dynamic_component_automatically` | build | E | U | `components.py` |
| `components.no_duplicate_dynamic_feed` | one source output enters a dynamic component once | `hisim/dynamic_component.py:DynamicComponent.add_component_input_and_connect` | build | E | U | `components.py` (one rule with EF-25) |
| `components.dispatch_port_unique` | an aggregator has no two dispatch ports of one name or one type on one weight | `DynamicComponent._refuse_a_dispatch_port_this_aggregator_already_has` | build | E | U | `components.py` |
| `components.default_connection_tag` | a default connection's tag is carried by its target output | `DynamicComponent.connect_with_dynamic_connections_list` | build | E | U | `components.py` |
| `components.channel_declared` | an aggregator asks only for a channel it declares | `DynamicComponent.get_channel` | build / run | E | U | stays (an internal contract) |
| `components.cost_relevance_declared` | with `COMPUTE_LIFECYCLE_COSTS` or `LIFECYCLE_COST_REPORT`: no class with `cost_relevance` `UNDECLARED`; a `PRICED` class has `get_cost_facts` or an adapter entry | `hisim/simulator.py:Simulator.check_cost_declarations` | build | E | U | `components.py` |
| `components.source_output_registered` | every connected input's source output belongs to an added component | `Simulator.evaluation_plan` | first plan | E | U | `components.py` |
| `components.kind_in_evaluation_order` | every kind is in `EVALUATION_ORDER_OF_KINDS` | `Simulator.evaluation_positions` | first plan | E | U | `components.py` |
| `components.no_physics_cycle` | the L1 device-before-store constraints form no cycle | `hisim/evaluation_order.py:PhysicsOrder.ordered` | first plan | L | S | `components.py` |
| `components.no_l2_cycle` | L2 controllers read one another in no cycle | `L2Order.ordered` | first plan | L | S | `components.py` |
| `components.not_empty` | the run has at least one component and one column | `Simulator.run_all_timesteps`; `process_one_timestep` | build / step | E | U | `components.py` |
| `energy_ports.circuit_pairing` | an end's other end declares the circuit; a circuit has exactly one supplier | `eb/ports.py:Wiring.pairing_fault`, `Wiring.circuits` | build; again after the run | E | U | `energy_ports.py` |
| `energy_ports.one_reader_per_flow` | a circuit's mass flow is read by one circuit-declaring component | `eb/ports.py:Wiring.circuits` | build; after the run | E | U | `energy_ports.py` |
| `energy_ports.no_circuit_on_a_node` | an exempt component or a meter declares no circuit end | `eb/ports.py:Wiring.circuits` | build; after the run | E | U | `energy_ports.py` |
| `energy_ports.receiver_unambiguous` | an output port read through paired inputs by one component; a non-metered `PRODUCTION` port read by one declared component unless an input is tagged | `eb/ports.py:Wiring.ambiguous_receiver_faults` | build | E | U | `energy_ports.py` |
| `energy_ports.input_port_pairs` | an input port pairs with the output port it reads (same carrier, opposite role) | `eb/ports.py:Wiring.unpaired_input_port_faults` | build | E | U | `energy_ports.py` |
| `energy_ports.room_heat_needs_receiver` | a `PRODUCTION` port of `ROOM_HEAT` is read by a component | PR #1211 (`Wiring.refuse_a_broken_wiring`, fourth fault list) | build | E | **S**, waived for `demo` | `energy_ports.py` |
| `energy_ports.loop_tagged_whole` | a port set tags all four loop quantities | `hisim/component.py:FluidLoopPortSet.group` | build | E | U | `fluid_loops.py` |
| `fluid_loops.far_end_tagged` | a tagged flow is read by, or fed from, a tagged channel | `hisim/fluid_loops.py:FluidLoopWalk.untagged_far_end_faults` | build | E | U | `fluid_loops.py` |
| `fluid_loops.flow_read_once` | one flow out is read by one flow in (a split needs a valve) | `FluidLoopWalk.connection_fault` | build | E | U | `fluid_loops.py` |
| `fluid_loops.closes` | a wired port set's flow out is read and its flow in fed | same | build | E | U | `fluid_loops.py` |
| `fluid_loops.wired_whole` | flows and temperatures are connected together, or neither | same | build | E | U | `fluid_loops.py` |
| `fluid_loops.temperature_follows_flow` | a member's temperature in reads the member whose flow it reads | `FluidLoopWalk.loop_faults` | build | E | U | `fluid_loops.py` |
| `fluid_loops.one_carrier_one_fluid` | all members state one carrier and one fluid | same | build | E | U | `fluid_loops.py` |
| `fluid_loops.one_pump_owner` | exactly one member pumps | same | build | E | U | `fluid_loops.py` |
| `kinds.*` | the contract rules of §4.4 | new | build | E | U / S / B | `kinds.py` |

The constructor-time port rules (`EnergyPort.__post_init__`: no grid environment, no environment on a metered
port; `EnergyPort.validate_unit`: a power, an energy or kg/s for a fuel; `check_fits_an_input`;
`MeteredExchange.__post_init__`; `FluidLoop.__post_init__`: the carrier is one of three loop carriers, the fluid a
`LoopFluid`; `FluidLoopChannel.check_fits`; `Component.refuse_a_repeated_fluid_loop_tag`) are declarations of a
component class, checked when the class declares them; they are U and stay (Q9). `FluidLoop.LOOP_CARRIERS` (space
heating, domestic hot water, ambient heat) is the one of them that encodes a house: a process-heat loop is refused
by the declaration; it is noted for the commercial use, not scoped.

### 6.6 After the run (stage AFTER_RUN) and the write guard

| Rule | Checks | Lives today | When | E/L | Class | Target |
|---|---|---|---|---|---|---|
| `results.finite` | no result column holds NaN or inf unless its output declares `may_be_non_finite`; one column per output | `hisim/postprocessing/finite_results.py:FiniteResultsCheck.require_finite` | after the run, first | E | U | `results.py` |
| `results.all_rows` | the table has one row per timestep | `Simulator.prepare_post_processing` | after the run | E | U | `results.py` |
| `results.port_finite` | a declared port is finite in every step | `eb/check.py:BalanceCheck.nonfinite` | after the run | E | U | `results.py` (one rule with `results.finite`) |
| `results.component_balance_closes` | per component, consumption − production − loss − stored change ≤ 0.1 % of throughput over the run and per step (`Tolerance`) | `eb/check.py:BalanceCheck.balance`, `run` | after the run | E | U | `results.py` |
| `results.link_closes` | every `PRODUCTION`→`CONSUMPTION` transfer between declared components is booked by both sides within 0.1 % | `eb/check.py:BalanceCheck.links`; `eb/ports.py:DeclaredPorts.declared_links` | after the run | E | U | `results.py` |
| `results.circuit_faults` | the circuit rules of §6.5 on the run's columns, plus: an open end pumps no water | `eb/ports.py:DeclaredPorts.circuit_faults`, `add_circuits` | after the run | E | U | `results.py` |
| `results.circuit_columns` | a circuit's mass flow is never negative, the timestep positive; the columns a port, circuit or exchange names exist | `eb/ports.py:CircuitColumns.heat_in_kilowatt_hours_per_step`, `DeclaredPorts.add_input_ports`, `read_meters` | after the run | E | U | `results.py` |
| `results.no_port_on_a_node` | an exempt component or a meter declares no port | `eb/ports.py:DeclaredPorts.refuse_a_port_of_a_node_that_moves_nothing` | after the run | E | U | `results.py` |
| `meters.meter_balance` | per meter and carrier, import − export equals the ports it reads, within tolerance; enforced only while nothing is undeclared (Q-H) | `eb/meters.py:MeterBalance.of`, `MeterCheck.failing` | after the run | E (conditional) | U | `meters.py` |
| `meters.metered_port_has_a_meter` | a metered port's peer is a meter or a component; enforced only while nothing is undeclared | `eb/ports.py:DeclaredPorts.unmetered`; `eb/meters.py:MeterCheck.failing_unmetered` | after the run | E (conditional) | **S**, waived for `demo` | `meters.py` |
| `results.port_linked` | a non-idle port has a peer | `eb/ports.py:DeclaredPorts.unlinked` | after the run | L (the acceptance PR makes it E) | S, waived for `demo` | `results.py` |
| `results.component_declared` | a component declares ports or is exempt with a reason | `eb/ports.py:DeclaredPorts.collect` | after the run | L (CI makes it E, §6.9) | U | `results.py` |
| `costs.lifecycle_engine` | the cost engine finished; `CostDataError` always propagates, other errors fail only with `require_lifecycle_costs` | `hisim/postprocessing/postprocessing_main.py:PostProcessor.compute_lifecycle_costs` | post-processing | E / L | U | stays (the engine's own policy) |
| write guard | no write outside the calculation's directories; one result directory per calculation; not under `hisim/inputs` | `hisim/write_guard.py:WriteGuard._hook`, `CalculationDirectories` | every write | E | U | stays (not a rule on a system) |
| simulation window | the step divides a day, the span is whole days in one year, the start is midnight; `weather_year`, `part_load_above_seconds`, `cache_directories` valid | `hisim/simulationparameters.py:SimulationParameters.validated_window` and siblings | load | E | U | stays (parameters, not the system) |
| convergence | a step converges within max(100, 100·s/60) passes; after 10 it is forced | `Simulator.process_one_timestep` | per step | E | U | stays (the loop) |
| post-processing options | report writers need `GENERATE_PDF_REPORT`; KPI JSON needs `COMPUTE_KPIS`; network charts need `MAKE_NETWORK_CHARTS` and `dot` | `PostProcessor.run` and siblings | post-processing | E | U | stays |
| KPI consistency | self-sufficiency, relative demand, self-consumption ≤ 100 %; batteries publish their stored-energy increase; KPI entries have a source, a tag, a unique address | `hisim/postprocessing/kpi_computation/kpi_preparation.py`, `kpi_structure.py`, `kpi_address.py` | post-processing | E | U (the HTW Berlin definitions are a prosumer household's, noted) | stays |
| building-sizer KPI JSON | a building object has a positive conditioned floor area | `PostProcessor.write_kpis_to_json_for_building_sizer` | post-processing | E | B | stays |
| scenario KPIs read `BUI1` | `write_kpis_in_dict` reads the default building label | `PostProcessor.write_kpis_in_dict` | post-processing | E (KeyError) | R-shaped (one house) | stays; a district bead |
| economics | `DeviceEnergySpecs` rows for every electricity publisher; useful-heat sources are `Building.TheoreticalHeatingEnergyDemand` and `SimpleDHWStorage.ThermalEnergyConsumptionDHW`; `EconomicContext` (tenancy, envelope), perspectives (owner-occupier, landlord, tenant), the subsidy context (dwelling units, household size) | `hisim/economics/bridge.py`, `adapter.py`, `perspectives.py`, `subsidies/context.py` | post-processing | E | **R** (the cost engine is a household's) | stays; scoped when the engine runs for a commercial case |

### 6.7 Recording (the twin, not the run)

The recorder's checks (EF-R1 to EF-R14, `hisim/energy_system/recording/`: names are identifiers, no qualified
identity (EF-R2), no absolute path, the preset still exists, the written file loads and builds, a law-omitted
field re-resolves to the run's number, a hand-authored file is not overwritten, a grouping assigns every
difference) check a recording against its run, not a system; they stay. EF-R2 is the one that encodes a single
house ("recording a district system needs the format to carry the qualified identity first") and is the first
blocker of a district twin; noted for the district bead, not scoped.

### 6.8 Component rules that encode a residential house

These live in the components as mandatory inputs, sizing laws, constants or refusals. None of them moves: a
device's model is its own (Q9). They are listed because they are what the kind must know about, and because the
contract rules of §4.4 and the waivers are written against them.

| Rule | What | Lives today | When | Class | Note |
|---|---|---|---|---|---|
| a heat distribution system needs a building | mandatory inputs `TheoreticalThermalBuildingDemand`, `ResidenceTemperatureIndoorAir` only a `Building` feeds | `cmp/heat_distribution_system.py:HeatDistribution.__init__`, `HeatDistributionController.__init__` | build (EF-31) | B | right for any heated building |
| room-temperature controllers need a building | mandatory indoor temperature: `AirConditionerController`, `SimpleAirConditioner`, `L1CHPController`, `IdealizedElectricHeaterController` | `cmp/air_conditioner.py`, `simple_air_conditioner.py`, `generic_chp/controller.py`, `idealized_electric_heater.py` | build (EF-31) | B | same |
| heating-season input | boiler, district heating, electric heating, heat-pump space heating and HDS controllers read `HeatingSeason` from the heating curve, whose laws read the building's load per m² | `cmp/generic_boiler.py`, `generic_district_heating.py`, `generic_electric_heating.py`, `more_advanced_heat_pump_hplib.py`, `heating_curve.py:HEATING_THRESHOLD_LAW` | build | B | a process-heat boiler has no season; a commercial building has one |
| a building without residents warns | `HeatingByResidents` and `HeatingByDevices` optional; 0 W when unconnected | `cmp/building/building.py:Building.warn_about_unconnected_internal_heat_gains` | first step | R | becomes `kinds.residential_building_has_residents` |
| TABULA windows, scaling, apartments | a row with no reference window area is refused; floor area and base area exclusive; apartments ≥ 0 (else floor area / 92.1 m²) | `cmp/building/information.py` | build | R | TABULA is residential |
| the design heating load | (H_tr + H_ve)·(T_in − T_ref), DIN EN 12831 steady state | `cmp/building/information.py:get_max_thermal_building_demand` | build (sizing) | B | the same law for both uses |
| the building provides the facts | heating load, apartments, floor area, roof area, set points; a consumer without a building fails EF-4A | `cmp/building/config.py:SIZING_CONTRIBUTIONS` | build | B (apartments: R) | |
| per-apartment sizing laws | DHW tank 250 l per apartment; collector 4 m² per apartment; boiler floor 2,500 W per apartment | `cmp/simple_water_storage.py:SimpleDHWStorageConfig.VOLUME_LAW`; `solar_thermal_system.py`; `generic_boiler.py` | build | R | commercial laws per use |
| rooftop PV needs a roof area; a buffer needs the generator's power | `cmp/generic_pv_system/config.py:_rooftop_power_in_watt`; `simple_water_storage.py:_buffer_volume_in_liter` | build | B / U | |
| domestic hot water | tap 45 °C, fresh water 10 °C, 2,000 kWh/a per household | `cmp/configuration.py:HouseholdWarmWaterDemandConfig` | per step | R | per use for commercial |
| heating-curve design day | the reference temperature is below the set point; the load fraction ≥ 0 | `cmp/heating_curve.py:HeatingCurveController.__init__` | build / step | B | |
| comfort bands | heating set < cooling set, finite, positive hysteresis | `cmp/air_conditioner.py:AirConditionerController.__init__` | build | B | the band's values by use |
| cooling through the emitter needs floor heating; HDS cooling needs `cooling_enabled` | `cmp/more_advanced_heat_pump_hplib.py`; `heat_distribution_system.py` | build / step | B | |
| a household occupancy | one household per connector (more through the LPG); a predefined profile exists; a car's profile is published by exactly one occupancy; a car runs on electricity or diesel | `cmp/loadprofilegenerator_utsp_connector.py`; `lpg_car_information.py`; `generic_car.py:CarConfig.for_household` | build | R | |
| substation DHW share | in parallel mode the DHW load is below the connected load | `cmp/generic_district_heating.py`; `generic_electric_heating.py` | per step | B | |
| the storage's converged temperature | the mean water temperature in [0, 90] °C after convergence | `cmp/simple_water_storage.py:SimpleWaterStorage.check_converged_mean_water_temperature` (the one non-empty `i_doublecheck`) | per step | K | a domestic tank's cap; a process tank would raise it |
| the loop carriers | `SPACE_HEATING_HEAT`, `DOMESTIC_HOT_WATER_HEAT`, `AMBIENT_HEAT` | `hisim/energy_port.py:FluidLoop.LOOP_CARRIERS` | declaration | K | a process-heat loop needs a carrier |

The remaining component refusals are physics guards (K, universal, stay): the heat pump's SCOP range and W55 ≤
W35, power ≥ 1 W, COP > 0 when running; the boiler's efficiency range and control signal in [0, 1]; the
substations' lift ≥ 0; the battery's finite non-negative parameters and SOC ≤ 1; the CHP, fuel cell, electrolyzer
and hydrogen storage bounds; the transformer's efficiency in (0, 1]; the solar-thermal pump's flows; the PV share
and database lookups; the weather files, columns and ranges; the CSV loader's rows, columns and time labels; the
LPG and car profile resolution; the meters' carriers; the `NaN` fall-through branches of unknown modes
(`hisim/components/*`, §3 of the inventory's source notes).

### 6.9 CI gates and harness contracts

| Gate | Enforces | Lives today | Scope it gets |
|---|---|---|---|
| golden check | every (setup, parameters) pair reproduces its KPI leaves in Python, twin and composed mode | `scripts/golden_check.py`, `golden_kpis.py`, `golden_config.json`; `.github/workflows/golden-check.yml`, `golden-year.yml`; `tests/test_golden_matrix.py` (every setup is in the config) | every purpose (Q7) |
| twin freshness | every setup re-records byte-identically; no skip list; no two parameter files describe one run | `scripts/record_all_setups.py --check`; `.github/workflows/energy-system-freshness.yml`; `tests/test_energy_system_recording_fleet.py` | every purpose; checks the `purpose` key |
| parity | a Python setup equals its twin, wiring and results | `scripts/p3_parity_check.py`; `tests/test_p3_parity.py` | every purpose |
| schema | the committed schema equals a fresh export; every twin validates | `tests/test_energy_system_schema.py` | every purpose |
| port coverage | every component class declares ports or is exempt with a reason stated on its own body; an exempt class declares nothing | `tests/test_energy_port_coverage.py`, `tests/component_sweep.py` (sizes every preset against `ComponentSweep.REFERENCE_HOUSE`, a single-family archetype) | U; the reference house is R-shaped (a commercial sweep when a commercial archetype exists) |
| flag agreement | an electricity postprocessing flag agrees with its energy port; the one-sided list only shrinks | `tests/test_energy_port_flag_agreement.py` | U |
| kind base | every component class has one kind; the evaluation order | `tests/test_component_kinds.py` | U |
| cost declarations | every class declares `cost_relevance`; adapter keys resolve; `AUTO_SUBSTITUTES` are the single-family archetype | `tests/test_economics_adapter_contract.py`, `test_economics_declaration_gate.py`; `tests/renovisor/test_twin_cost_declarations.py` | U; substitutes R-shaped |
| no per-step finite checks | no `isfinite` in hot paths | `tests/test_no_per_step_finite_checks.py` | U |
| explicit-state controllers | no hidden state; the committed state reproduces the outputs | `tests/test_explicit_state_controllers.py` | U |
| fluid-loop fleet | every recorded file builds and gets its loop ids | `tests/test_fluid_loops.py` | U |
| DHW and buffer twins | every file with a tank closes it; both circuit ends agree; the forced-step share is bounded | `tests/test_dhw_chain_twins.py`, `test_space_heating_buffer_twins.py`, `test_part_load_twins.py` | B (files with a tank) |
| sizing scalability | components scale ×5 with the floor area | `tests/test_sizing_energy_systems.py` | R |
| assembly library | EF-75 on every library file | `tests/assemblies/test_assembly_library.py` | U |
| assembly contracts: run, balance, finite, bounds, expect, monotone, member contract | per sample | `tests/assemblies/test_library_contracts.py`; `es/assemblies/testing/checks.py` | U |
| assembly contract: heat delivered | no `HEATING` input left unconnected when a default connection serves it | `checks.py:HeatDelivery.lost`, `check_heat_delivered` | B |
| assembly contract: room heated | every heated building's day-mean air within 1 K (`Building.UNDERHEATING_TOLERANCE_IN_KELVIN`) of its set point | `checks.py:check_room_heated` | B (set point by use) |
| isolation harness | every carrier moved through a metered port gets a meter partner; a port with no partner refuses the build | `es/assemblies/testing/isolation.py`, `partners.py` | the isolation system is `demo` |
| composed-twin gates | every composed file equals its twin outside the neutral swaps | `tests/assemblies/test_twin_gates.py`, `test_composed_twins.py` | every purpose |
| layering lints | nothing outside `hisim/renovisor` imports it; subcomponents import only the kernel; a component imports no file format | `tests/test_renovisor_import_lint.py`, `test_subcomponent_import_lint.py`, `test_energy_system_layering.py` | U; the rules package joins the layering test (imports no component) |
| semgrep | no file write outside the allowlist; no `print` | `.semgrep/file_writes.yml`, `no_print.yml`; `quality.yml` | U |
| stray files | a test dirties nothing outside its result directory | `tests/conftest.py:guard_against_stray_files` | U |
| RenoVisor path verification | every probe path translates and diffs against its base | `python -m hisim.renovisor verify`; `.github/workflows/path-verification.yml` | R (RenoVisor) |

### 6.10 Out of scope, noted: RenoVisor

`hisim/renovisor/request.py` checks a calculation request in two steps: the vendored JSON Schema, then the
semantics (every measure id in the 32-entry catalogue, every option of its measure, an `everyone` option, a TABULA
typology for the country, a TABULA row, no conflicting hot-water volumes), all problems at once as a
`RequestError` (exit 2). `whitelist.py` and `not_implemented_yet.yaml` list what the translator may skip with a
note; anything else unmapped fails (exit 3). `translate.py` picks a recorded grouped twin by renovated generator
and may only write config values, named-constructor arguments and a variant selection; `run.py` requires the
lifecycle costs. All of it is a house and a household: RenoVisor is residential, every file it writes is `case`,
and the only rule of this spec that touches it is `kinds.demo_is_no_base`.

### 6.11 Counts

Rows of the tables above (one row is one rule; the §6.4 library check is one row for its 45 messages, the
`kinds.*` row of §6.5 stands for the seven contract rules of §4.4):

| Family | Rows | Stay (P, K, parameters, engine) | Moved into the package | Of the moved: log-only |
|---|---|---|---|---|
| §6.1 document | 27 | 5 | 22 | – |
| §6.2 bindings and sizing | 21 | – | 21 | 1 |
| §6.3 wiring | 19 | – | 19 | 2 |
| §6.4 assemblies | 22 | 1 | 21 | – |
| §6.5 built system, energy ports, fluid loops, kinds | 36 | 2 | 34 (+6 contract rules) | 2 |
| §6.6 after the run, meters, the rest | 21 | 9 | 12 | 2 |
| §6.7 recorder | 14 | 14 | – | – |
| §6.8 component rules that encode a house | 17 | 17 (R 5, B 10, K 2) | – | – |
| §6.9 CI gates and contracts | 23 | 23 | – | – |
| Total | 200 | 71 | 129 (+6) | 7 |

The residential assumptions: eight rules hold only for a household (the five R rows of §6.8, EF-R2, the cost
engine's household framing with `write_kpis_in_dict`'s `BUI1`, and `ComponentSweep.REFERENCE_HOUSE` with
`AUTO_SUBSTITUTES`), thirteen hold for any heated building (the ten B rows of §6.8, `check_room_heated`,
`check_heat_delivered`, the building-sizer KPI JSON), and two are device limits shaped by a house (the tank's
90 °C, the loop carriers). Of the rules that are moved, two are scoped on day one
(`energy_ports.room_heat_needs_receiver`, `meters.metered_port_has_a_meter`), four are log-only system rules
(`wiring.unconsumed_source`, `wiring.unmetered_output_of_a_need`, `sizing.unconsumed_fact`, `results.port_linked`),
three are log-only universal ones (`components.no_physics_cycle`, `components.no_l2_cycle`,
`results.component_declared`), and one encodes one site (`assemblies.one_provider_per_carrier`); every other
moved rule is universal.

## 7. Which setup gets which kind

| Setup or file | Building | Occupancy | Purpose | Consequence |
|---|---|---|---|---|
| the eleven `household_*_building_sizer` setups, their twins, grouped and composed files; `household_gas_solar_thermal`; `basic_household`, `basic_household_only_heating`; `air_conditioned_house`; `automatic_default_connections`, `default_connections`; `gas_boiler_household` (hand-authored); `uc1.realized` | one, residential | yes | `case` | nothing changes; the contract rules of §4.4 hold already |
| `simple_air_conditioner_household_building_sizer` (+ grouped, probes) | one, residential | **none** (the building warns, 0 W gains) | `case` | `kinds.residential_building_has_residents` refuses it: add the occupancy (results move, blessed in that PR) or declare `demo` (then it leaves RenoVisor's base set; it is not a renovation generator today). Recommendation: add the occupancy |
| `dynamic_components` | none | yes | `demo` | the room-heat rule is waived; the residents' two `ROOM_HEAT` outputs stay unlinked (reported, as today); its five goldens stay (Q7) |
| `electrolyzer_with_renewables` | none | none | `case` (a plant) | no building rule applies; the system rules do; nothing changes |
| `simple_system_setup_one`, `_two` | none | none | `demo` | random numbers and sums; every universal rule still holds |
| `tests/energy_systems/two_pv_arrays` (+ composed) | none | yes | `demo` | the room-heat rule is waived |
| the dispatch test and the assembly-crossing test of #1211's list | none | yes | `demo`, or a building where the test is about the house | decided per test in the PR |
| the isolation systems of the assembly harness | the `Building` partner when a member needs its facts | the `UTSPConnector` partner when a member observes it | `demo` by construction | the harness contracts (`check_room_heated`, `check_heat_delivered`) keep running: they are the rig's own checks, not run-time rules |
| RenoVisor's output file | one, residential | yes | `case` | written by the translator; the base must be `case` |

## 8. Implementation plan

Small stacked PRs; each keeps every result and every message bit-identical unless it says otherwise; each moves
one family and its tests, and adds the direct tests of §9 for the rules it moves. PR 1 is the mechanism; PR 2
carries #1211; the families follow in the order of their coupling.

| PR | Content | Moves | Results |
|---|---|---|---|
| 1 | `hisim/rules/`: `Rule`, `Fault`, `RuleStage`, `RuleScope`, `RuleViolation`, `RuleRegistry.collect`, `Waivers.TABLE` (empty), `Inventory.RECORDS` (every rule id of §6 with its family, stage, scope and where it lives today), the subjects; `SetupPurpose`, `BuildingUse`, the `purpose` key (model, reader, emitter, schema, executor), `BuildingConfig.use`, `Simulator.declare_purpose`, the recorder, every setup's declaration, every twin re-recorded, the fixtures and the harness declaring; `kinds.purpose_declared`, `kinds.building_use_declared`, `kinds.building_use_has_a_source`; the generic tests of §9; the four hooks calling an (almost empty) registry | nothing | none |
| 2 | `energy_ports.py`: the three fault lists of `Wiring.refuse_a_broken_wiring` and `energy_ports.room_heat_needs_receiver` with its `demo` waiver; #1211 rebased onto PR 1 and reduced to the device side | `eb/ports.py:Wiring.*_faults` | none (the rule refuses nothing that runs today once the demos are declared) |
| 3 | `fluid_loops.py`: the refusals of `FluidLoopWalk.find_loops`; the walk keeps discovering loop ids | `hisim/fluid_loops.py` (part) | none |
| 4 | `results.py`, `meters.py`: the finite check, the balance, the links, the circuit faults, the meter balance, the unmetered ports (Q-H kept), the log-only `port_linked` and `component_declared` | `hisim/postprocessing/finite_results.py`; `eb/check.py`, `eb/meters.py` (the judging halves) | none |
| 5 | `components.py`: the simulator's build rules, `check_cost_declarations`, the evaluation-order cycles | `hisim/simulator.py`, `component_wrapper.py`, `dynamic_component.py` (the checks) | none |
| 6 | `wiring.py`: the declarative wiring rules | `es/wiring_checks.py` whole; the checks of `es/aggregator_ports.py`, `channel_matching.py`, `dispatch_signals.py`, `feed_resolution.py` | none |
| 7 | `document.py`: the structural rules | `es/validation.py` whole; the checks of `names.py`, `entries.py`, `loader.py` | none |
| 8 | `bindings.py`, `sizing.py`: the class, preset, config and sizing-classification rules | `es/bindings.py` (checks), `classes.py` (checks), `sizing_bridge.py:KernelFailure` | none |
| 9 | `assemblies.py`: the import, binding and library rules | `es/assemblies/binding.py` (checks), `library.py:LibraryChecker`, `parameters.py` (checks), `selection.py` (checks) | none |
| 10 | `kinds.py`: `residential_building_has_residents`, `residential_building_is_heated`, `demo_is_no_base` (RenoVisor's base selection and the test-partner source); `simple_air_conditioner_household_building_sizer` gets its occupancy (or declares `demo`, Q) | – | that setup's goldens move, measured and blessed in the PR |
| 11 | the CI gates read the scope: `check_room_heated` and `check_heat_delivered` take the building's use; the sweep's reference house is named residential | `es/assemblies/testing/checks.py` | none |

After PR 11, `Inventory.RECORDS` names no "lives today" outside the package for any rule of class U, S, B or R that
is a rule of the system; the records of the P, K and recorder rows stay as records with their place, so the
inventory stays the one list.

## 9. Verification

Generic tests, in `tests/rules/`, each one test for the whole package:

- `test_registry.py`: every `Rule` subclass of the package declares a unique `ID` of the form `<module>.<name>`
  where `<module>` is its module; a `STAGE`, a `SCOPE`; `UNIVERSAL` implies `SCOPE == SYSTEM`; the registry
  collects exactly the classes the inventory records as "in the package"; the order of a stage is the inventory's.
- `test_waivers.py`: every waiver names an existing rule, a non-universal one, an existing purpose and use, and a
  non-empty reason; no waiver twice; no waiver for a `case` with a residential use (a case is never waived; if one
  is ever needed, the table's test is amended with the owner).
- `test_inventory.py`: every rule id of the registry is in the inventory and vice versa; every record names a
  class, a stage and a place; the records of this spec's §6 are the file's (the spec cites the file as its source
  after PR 1).
- `test_direct_tests.py`: every rule id appears in a test under `tests/rules/test_<module>.py` that constructs a
  subject with the fault and one without (the extensive-docstring rule's "every static function has direct
  tests", here per rule).
- `test_declarations.py`: every `system_setups/*.py` declares its purpose (a source scan for `declare_purpose` and
  a run of each setup's build); every `energy_systems/*.energy_system.yaml`, every composed file and every fixture
  under `tests/energy_systems/` carries `purpose`; every building in every file has a `use`.
- The existing tests prove each migration: a PR that moves a family changes no message, so
  `tests/test_energy_system_*.py`, `tests/test_energy_port_*.py`, `tests/test_fluid_loops.py`,
  `tests/test_finite_results.py`, `tests/test_component_kinds.py` and the assembly tests pass unchanged; the
  freshness gate, parity and the goldens stay green (the one re-recording in PR 1 is the `purpose` key).
- The room-heat rule: `tests/rules/test_energy_ports.py` builds a system with an unread `ROOM_HEAT` output and
  sees the fault for `case`, no fault for `demo`, and a fault again for `case` with the waiver table emptied (the
  waiver, not the rule, is what the demo relies on).
- The `base` budget (`system_docs/testing.md` §3): the generic tests import the package and scan files, no
  simulation; the per-rule tests build small subjects, no full run.

## 10. Rejected alternatives

- **One class of rule, everything waivable.** A waiver on the energy balance or the loop closure would let a demo
  publish numbers that break the model; the owner's "fail loudly" rule has no demo exception.
- **Per-rule kind lists (the owner's first form).** A new kind starts with no checks, and a rule that forgets a
  kind runs nowhere for it, both silently; with a waiver table a forgotten entry refuses, which is noticed.
- **Per-setup waivers.** A setup that does not fit its kind declares another kind or is fixed; a switch per file is
  the state the owner wants to leave.
- **One kind per setup (`residential`, `commercial`, `energy_system`, `demo`).** A district mixes uses; a plant
  needs no kind once building rules scope by building; `energy_system` collides with the file suffix; rigour (demo
  or not) and use (what the building is) are different questions, and one word would have to answer both (a
  "residential demo" has no name).
- **A kind in `SimulationParameters`.** The parameter files are a shared library, any system runs from any of
  them; a kind is a property of the system.
- **A kind read from the components (a building present ⇒ a house).** The kind is then a guess that cannot be
  wrong by declaration, so a mis-built system cannot be caught; and a demo with a building would be a case.
- **Keeping the rules where they are and adding a `kinds` attribute to each.** The hooks would stay four, the
  enforcement decisions scattered, nothing would list the rules; D1 rejects it.
- **Moving the per-step physics guards and the constructor-time declarations into the package.** They are a
  device's model (clamp or fail at a limit, `system_docs/modelling_guide.md`) or a class's declaration, not a rule
  of a system; the package holds what a system must obey (Q9).
- **A `metadata:` block for the purpose.** EF-09 reserves `metadata` for generated run records; a plain run refuses it.

## 11. Open questions

- **Q1. Two classes of rule, or one.** (a) Two: universal rules (energy conservation per component and link, a
  closed loop with one pump owner, port units, a meter publishing what it reads, finite results, the document
  being well-formed) are never waived; domain rules are scoped. (b) One: every rule may be waived by the table. The
  consequence of (b) is that a demo can be made to pass with numbers that break the model, and nothing in the
  package says which rules are laws. Recommendation: (a), with `UNIVERSAL = True` on the rule and the waiver test
  refusing a waiver that names one. Bead .1 (`owner-decision`).
- **Q2. The default direction.** (a) Every domain rule applies to every kind unless the waiver table names (rule,
  purpose, use, reason); the test fails a waiver that names nothing or a universal rule. (b) Every rule lists the
  kinds it applies to; a new kind starts with no domain checks until each rule names it. (a) errs toward refusing
  (a demo refused by a rule it should not see is one table row away), (b) toward silence (a rule that forgets a
  kind never runs for it, and no test can tell a forgotten kind from an intended one). Recommendation: (a). Bead .1.
- **Q3. One kind per setup, or two axes.** (a) One kind: `demo`, `residential`, `commercial`, `energy_system`
  (the owner's form). (b) Two axes: the purpose per setup (`case`, `demo`) and the use per building (`residential`,
  `commercial`). With (b) a district with two uses is checked building by building, a plant needs no kind, and a
  demo of a house keeps its building rules waivable separately from a demo of a plant. With (a) each of those is a
  special case of the one word. Recommendation: (b). Bead .1.
- **Q4. The names.** For the purpose: `case` and `demo` (alternatives: `study`/`demo`, `production`/`demo`); for the
  use: `residential` and `commercial` (`non_residential` would match DIN V 18599's wording; `commercial` is the
  owner's). The file key is `purpose`, not `kind` (assembly files have `kind: assembly`). Recommendation: as
  written. Bead .1.
- **Q5. A kind is a contract.** (a) The contract rules of §4.4 run: a residential case must have residents and a
  heater, a commercial use must have a source, a demo is never a RenoVisor base or a test-partner source; a wrong
  declaration is refused. (b) The kind is a switch only; what a setup declares is trusted. Under (b) a case with
  an empty building passes, and a demo can be handed to RenoVisor. Recommendation: (a). Bead .1.
- **Q6. Declaration and enforcement.** The purpose is a mandatory top-level key of every energy-system file and a
  mandatory `declare_purpose` call in every Python setup, refused at build when missing, written into the twin by
  the recorder; the building's use a mandatory config field every preset sets to residential. The alternative, a
  default of `case`, lets a demo run as a case by omission, which is the silent direction. Recommendation: no
  default. Bead .1.
- **Q7. Demos and goldens.** Today `dynamic_components`, `simple_system_setup_one` and `_two` have five goldens
  each, and `tests/test_golden_matrix.py` requires every setup in the golden config. (a) Keep them: a golden is a
  regression gate on the code, not a statement that the numbers are right, and a demo's drift is as much a signal
  as a case's. (b) A demo is never blessed: the matrix test gets a purpose-aware exemption, the three demos leave
  `golden_config.json`. Recommendation: (a); the contract of §4.4 then says "never a RenoVisor base, never a test
  partner", and says nothing about goldens. Bead .1.
- **Q8. What a parse fault is.** EF-00 to EF-03 and EF-07 (the suffix, the schema version, a duplicate key,
  unreadable YAML, a block of the wrong type) and EF-70 are faults the reader meets before there is a document to
  check; the recommendation keeps them in the reader and counts as rules only the checks on a readable document
  (§6.1). The alternative moves the reader's checks too, which would make the reader call the registry on a half-
  read document. Recommendation: stay. Bead .1.
- **Q9. The per-step physics guards and the constructor-time declarations stay in the components.** A tank's
  converged temperature in [0, 90] °C, a boiler's control signal in [0, 1], a port's unit, a loop's carrier: each
  is a device's own model or a class's declaration (§6.5, §6.8), tested with the device. The alternative moves
  them into the package, which would make the package import every component and a device's limit a rule of the
  system. Recommendation: stay; the package holds the rules of the system. Bead .1.
- **Q10. `simple_air_conditioner_household_building_sizer`.** Under Q5 the residential case needs residents.
  (a) Add the occupancy, measure and bless the moved goldens (PR 10). (b) Declare it `demo`. (c) Let
  `kinds.residential_building_has_residents` accept a building that declares `unoccupied: true`. Recommendation:
  (a); the setup is called a household. Bead .1 (the decision) and .11 (the change).

## 12. Beads

Epic (proposed; the owner files the beads): hisim-setup-kinds-<hash>, P2, `core`, "every setup declares its kind,
every validation rule lives in `hisim/rules/` and says where it applies". The PR numbers of §8.

| Bead | Content | PR |
|---|---|---|
| .1 (P2, `owner-decision`) | the answers to Q1 to Q10, recorded as D2 to D11 | – |
| .2 (P2) | the mechanism: the package, the registry, the waiver table, the inventory as data, the subjects, the hooks; `purpose` and `use` declared everywhere; the twins re-recorded; the generic tests | PR 1 |
| .3 (P2) | the energy-port family, with `room_heat_needs_receiver` scoped and its `demo` waiver; #1211 rebased onto it | PR 2 |
| .4 (P3) | the fluid-loop family | PR 3 |
| .5 (P3) | the after-run and meter families | PR 4 |
| .6 (P3) | the simulator's build rules and the cost declarations | PR 5 |
| .7 (P3) | the declarative wiring family | PR 6 |
| .8 (P3) | the document family | PR 7 |
| .9 (P3) | the bindings and sizing families | PR 8 |
| .10 (P3) | the assembly family | PR 9 |
| .11 (P2) | the kind contracts (`residential_building_has_residents`, `_is_heated`, `demo_is_no_base`); the occupancy of `simple_air_conditioner_household_building_sizer` (Q10) | PR 10 |
| .12 (P3) | the CI gates and harness contracts read the scope | PR 11 |
| .13 (P4, `external`) | the heads-up on renovisorissues: `purpose: case` and `use: residential` in every file the translator writes; the capability document says the use | – |
| .14 (P4) | a commercial building: the archetype source, the usage profiles by zone type, the per-use hot water, the comfort band; until then `commercial` is refused by `kinds.building_use_has_a_source` | – |
| .15 (P4) | a district twin: EF-R2, `write_kpis_in_dict`'s `BUI1`, `assemblies.one_provider_per_carrier`; the building rules then run per building | – |
| .16 (P4) | `system_docs/`: the rule of D1 ("a validation rule lives in `hisim/rules/`, declares its stage and scope, and is waived only by the table") written into `coding_style.md` or a new guideline once PR 1 is merged | – |
