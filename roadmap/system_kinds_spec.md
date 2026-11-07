# Spec: every setup declares its kind, every validation rule lives in one rules package, and a rule a kind does not need is waived by name with a reason

Status: decided (owner, 2026-11-06: D1 with the request, D2 to D11 the answers to the questions of the draft, D12
to D15 from the comparison with ruff, pylint, ESLint and clippy);
PR 1 (the mechanism, bead .1) and PR 2 (the energy-port family, bead .2) are built; no question is open. Owner: Noah. Epic: proposed, hisim-setup-kinds-<hash> (P2, `core`), §12.
PR: #1216 (spec only). Builds on: `roadmap/device_location_spec.md` (D-h: the build rule "room heat needs a
receiver", PRs #1211 and #1213), `roadmap/complete_energy_sankeys_spec.md` (D1 and Q-B: a component declares its
ports or is exempt with a reason; Q-H: the meter check is enforced only while nothing is undeclared),
`roadmap/fluid_loops_spec.md` (§3.8, the checks in one table), `roadmap/declarative_energy_systems/` (the EF error
catalogue), `system_docs/component_design_principles.md`, `system_docs/coding_style.md` §4 (no module-level
constants, no mutable module state), `system_docs/testing.md`. Code is cited against main at 18fd35b64; the
room-heat rule against PR #1211 as it stood on 2026-11-06.

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

**The rule is one of many.** The inventory of §6 lists every check HiSim applies today: 199 distinct rules, of
which about 155 refuse (an exception at load, build or after the run), 8 only log, 23 are CI gates or harness
contracts, and 14 check a recording against its run. Twenty-one of them encode a house (eight a household,
thirteen any heated building), and none of them says so. Examples: a heat distribution system has mandatory
inputs only a `Building` can feed (`HeatDistribution.__init__`, `hisim/components/heat_distribution_system.py`),
the assembly contract `check_room_heated` requires the day-mean room air of every building within 1 K of its
heating set point (`hisim/energy_system/assemblies/testing/checks.py`), the assembly binding EF-7L requires exactly
one provider per carrier (one site, one supply), the sizing vocabulary is the house's (`SizingContext`,
`hisim/config/context.py`: heating load, apartments, residents, roof area), and the recorder refuses a component
whose identity names a building or a unit (EF-R2), so a district cannot be recorded at all. Every new rule faces
the same choice the room-heat rule faces: written for every system, it refuses the plant and the demo; not
written, the house goes unchecked. The owner's request of 2026-11-06: "a lot of the rules for making sure that a
residential house is correct make total sense, but only for residential houses, and applying them to everything
will make HiSim unusable for anything except residential houses."

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

D1 was taken with the request; D2 to D11 answer the ten questions of the draft of 2026-11-06 (the owner's answers of
the same day). Where the owner chose against the draft's recommendation, the recommended option is in §10 with the
reason it lost.

| ID | Decision | Owner | Date |
|---|---|---|---|
| D1 | The validation rules are modular and live in one rules package, `hisim/setup_rules/`: one module per rule family, each rule a self-contained unit that declares its id, what it checks and when it runs; a registry collects them; the hooks in the loader, the simulator and the post-processing only call the registry; the existing checks move out of their modules into the package | Noah | 2026-11-06 |
| D2 | Every rule can be waived per kind through its own `WAIVED_FOR`, the physics and consistency rules included; no class of rules is unwaivable | Noah | 2026-11-06 |
| D3 | Every rule applies to every kind unless it is waived for that kind with a reason; amended the same day: the waiver lives on the rule, not in a central table | Noah | 2026-11-06 |
| D4 | A setup declares exactly one kind; there is no second axis per building | Noah | 2026-11-06 |
| D5 | The kinds are `demo`, `residential`, `commercial` and `plant`, the enum `SetupKind`; the file key is `setup_kind` | Noah | 2026-11-06 |
| D6 | A kind is only a switch that selects waivers; nothing checks that a kind fits its setup | Noah | 2026-11-06 |
| D7 | The declaration is mandatory and has no default: a top-level `setup_kind` key in every energy-system file, written by the recorder into every twin; `Simulator.declare_setup_kind(...)` in every Python setup; a run without a declaration is refused before its first step; not a simulation parameter | Noah | 2026-11-06 |
| D8 | A demo keeps its goldens | Noah | 2026-11-06 |
| D9 | A parse fault (EF-00 to EF-03, EF-07, EF-70) stays in the reader; only a check on a readable document is a rule | Noah | 2026-11-06 |
| D10 | The components' per-step physics guards and constructor-time declarations stay in the components | Noah | 2026-11-06 |
| D11 | `simple_air_conditioner_household_building_sizer` gets residents; its results move and are blessed in that PR | Noah | 2026-11-06 |
| D12 | One class per rule: a `SetupRule` subclass with its class-scoped fields, its docstring and its `check` method | Noah | 2026-11-06 |
| D13 | Every rule has a stable code and a readable kebab-case name; a code is never reused and a retired code stays reserved; the EF codes stay; every docstring follows one template, which `hisim setup-rules show` prints and every refusal points to | Noah | 2026-11-06 |
| D14 | A rule is pure and reports every fault; the registry runs every applicable rule of a stage and raises one violation listing every fault, ordered by code; a rule that needs another to have passed declares it in `REQUIRES` and is skipped, visibly, when that one found faults | Noah | 2026-11-06 |
| D15 | A fault carries its location as fields, component, port and the file's key path, beside the message; the violation and the CLI can emit JSON | Noah | 2026-11-06 |

**D1 — the validation rules are modular and live in one rules package, each rule a self-contained unit with its
id, its check and its stage; a registry collects them; the hooks only call the registry; the existing checks move
into the package** (owner, 2026-11-06). Today a rule's text, its trigger and its enforcement are three decisions
taken in whichever module the rule was written in, and nothing lists them (§1); a rule that must say where it
applies needs one place where that is said and one test that reads it. Consequence: the package of §4.1; the
migration of §8, one rule family per pull request after the mechanism; the inventory of §6 names each rule's target
module; every rule gets direct tests (§9).

*Amendment (owner, 2026-11-06):* the base class is named `SetupRule`, not `Rule`, and with it `SetupRuleRegistry`, `SetupRuleViolation`, the package `hisim/setup_rules/` and the command `hisim setup-rules`: a bare `Rule` would clash with later rule concepts (sizing laws, cost rules, RenoVisor's request checks), and the name pairs with `SetupKind`, the declaration whose waivers a setup rule reads.

**D2 — every rule can be waived per kind, the physics and consistency rules included; no rule is unwaivable**
(owner, 2026-11-06; against the draft's recommendation of two classes, §10). One mechanism for every rule, and the
rule's `WAIVED_FOR` is the whole truth about which kinds do not run it: a reader needs no second list of
exceptions to the exceptions. Consequence: the `SetupRule` base class carries no `UNIVERSAL` flag and the base's own
check of a waiver asks only for an existing kind and a non-empty reason, nothing more (§4.1, §4.2, §9); the
inventory keeps a descriptive column that says whether a rule is a law of the model or a domain rule (§6), which
enforces nothing and tells a reviewer what a waiver would cost.

**D3 — every rule applies to every kind unless the waiver table names (rule, kind, reason)** (owner, 2026-11-06).
A forgotten waiver refuses a setup, which is noticed and fixed with one row; a forgotten kind on a per-rule list
runs nowhere for that kind, which nothing notices. The pattern is the energy balance's: a component declares its
ports or is exempt with a reason on its class, and `tests/test_energy_port_coverage.py` fails the one that does
neither (`Exemptions`, `hisim/postprocessing/energy_balance/ports.py`; `Component.ENERGY_PORTS_EXEMPT_BECAUSE`).
Consequence: a rule applies until a waiver with a reason says otherwise (§4.2).

*Amendment (owner, 2026-11-06):* the waivers live on the individual rules, not in a central table. The draft
wrote one `Waivers.TABLE` in `hisim/setup_rules/waivers.py` listing every (rule, kind, reason); such a table duplicates
the rules it names and drifts from them, and everything about a rule lives in its own module, in line with D1. So
each rule declares its own waivers in a class-scoped `WAIVED_FOR` mapping of kind to reason (§4.1), and the overview
of every waiver per kind is generated from the registry, never written by hand (§4.2).

**D4 — a setup declares exactly one kind; there is no second axis per building** (owner, 2026-11-06; against the
draft's two axes, §10). One word per setup is what a reader of a file and a writer of a setup can state and check;
a per-building use would be a field on the building, a scope per rule and a second table, for a district with
mixed uses that HiSim cannot record today (EF-R2) and no setup has. Consequence: a rule sees the whole system and
the one kind; a district that mixes a residential and a commercial building is a known limitation, to be designed
when a district twin exists (bead .13 of §12).

**D5 — the kinds are `demo`, `residential`, `commercial` and `plant`, the enum `SetupKind`; the file key is
`setup_kind`** (owner, 2026-11-06). `demo` is a demonstration or a test rig; `residential` a house people live in;
`commercial` a building people work in; `plant` a system without a building (`electrolyzer_with_renewables`). The
file key is not `kind`, which assembly files use for `kind: assembly` (`AssemblyFile.KIND`,
`hisim/energy_system/assemblies/model.py`); the draft's `purpose` loses because the word names only one of the four
values' meanings (a demo is a purpose, a residential house is not) and `setup_kind` says what the key holds.
Consequence: `SetupKind` in `hisim/setup_rules/kinds.py`, the lowercase values the file spells (§4.3).

**D6 — a kind is only a switch that selects waivers; nothing checks that a kind fits its setup** (owner,
2026-11-06; against the draft's contract rules, §10). What a setup declares is trusted: a residential setup without
residents, a commercial setup on a TABULA house, a demo handed to RenoVisor are the author's responsibility, not a
rule's. Consequence: no contract rules; `hisim/setup_rules/kinds.py` holds the enum and nothing else; the only enforcement
around the declaration is that it exists (D7).

**D7 — the declaration is mandatory and has no default: `setup_kind` at the top level of every energy-system file,
written by the recorder into every twin; `Simulator.declare_setup_kind(...)` in every Python setup; a run without a
declaration is refused before its first step; not a simulation parameter** (owner, 2026-11-06). A default would
let a demo run as a residential case by omission, the silent direction D3 avoids. The parameter files are a shared
library, any system runs from any of them (`simulation_parameters/README.md`), and the recorder's design says which
system a parameter set runs with is nobody's business (`RecordingSession.PARAMETERS_DIRECTORY`,
`hisim/energy_system/recording/session.py`); a kind is a property of the system. Consequence: §4.4; every twin is
re-recorded once; every setup gets one line.

**D8 — a demo keeps its goldens** (owner, 2026-11-06). A golden is a regression gate on the code, not a statement
that the numbers are right; a demo's drift is as much a signal as a house's. Consequence: `golden_config.json` and
`tests/test_golden_matrix.py` are untouched; `dynamic_components`, `simple_system_setup_one` and `_two` keep their
five goldens each.

**D9 — a parse fault stays in the reader; only a check on a readable document is a rule** (owner, 2026-11-06).
EF-00 to EF-03, EF-07 and EF-70 (the suffix, the schema version, a duplicate key, unreadable YAML, a block of the
wrong type, a malformed assembly block) are met before there is a document to check; moving them would make the
reader call the registry on a half-read document. Consequence: the P rows of §6 stay where they are.

**D10 — the components' per-step physics guards and constructor-time declarations stay in the components** (owner,
2026-11-06). A tank's converged temperature in [0, 90] °C, a boiler's control signal in [0, 1], a port's unit, a
loop's carrier: each is a device's own model (clamp or fail at a limit, `system_docs/modelling_guide.md`) or a
class's declaration, tested with the device; the package holds what a system must obey. Consequence: the K rows of
§6.5 and §6.8 stay; the package imports no component.

**D11 — `simple_air_conditioner_household_building_sizer` gets residents; its results move and are blessed in that
PR** (owner, 2026-11-06). The setup is called a household and runs a `Building` with no occupancy (0 W internal
gains and a warning, `Building.warn_about_unconnected_internal_heat_gains`); with D6 no rule would refuse it, and
the owner chose to make it what its name says. Consequence: bead .10, its own PR with the measured goldens and the
re-recorded twin, grouped and probe files.

**D12 — one class per rule: a `SetupRule` subclass with its class-scoped fields, its docstring and its `check`
method** (owner, 2026-11-06; from comparing the design with ruff, pylint, ESLint and clippy). The class form costs
15 to 20 lines of frame per rule where a decorated function would cost 5 to 8, and the owner prefers it: a class has
room for rule-specific helpers as methods, and `__init_subclass__` checks the declarations when the class is
created, which a decorator can do only at registration. Consequence: the shape of §4.1 stands; a rule's helpers are
its methods, never module functions.

**D13 — every rule has a stable code and a readable kebab-case name; a code is never reused and a retired code
stays reserved; the EF codes stay; every docstring follows one template, which `hisim setup-rules show` prints
and every refusal points to** (owner, 2026-11-06; ruff's `F401` / `unused-import` pair). A code is what a message,
a test and a bead cite and must never change meaning; a name is what a reader remembers. Consequence: `CODE` and
`NAME` on every rule, the scheme of §4.1 (a letter prefix per family and a number; the moved file checks keep their
`EF-nn`), `SetupRuleRegistry.RETIRED_CODES` for codes whose rule is gone, the docstring template "What it checks /
Why / Example of a fault / How to fix", and the pointer `see: hisim setup-rules show <code>` at the end of every
refusal.

**D14 — a rule is pure and reports every fault; the registry runs every applicable rule of a stage and raises one
violation listing every fault, ordered by code; a rule that needs another to have passed declares it in
`REQUIRES` and is skipped, visibly, when that one found faults** (owner, 2026-11-06). A run that stops at the first
fault costs one run per fault; a linter reports all of them. A rule that walks loops on a broken wiring would
report nonsense, so the dependency is written down and the registry skips the rule and says so, instead of an
implicit order that nobody can read. Consequence: `check` never raises; `REQUIRES: ClassVar[FrozenSet[str]]` of
codes; `SetupRuleViolation` lists faults and skips; the checks that stop at the first fault today are
restructured in their family's PR (§4.1, "What must be restructured").

**D15 — a fault carries its location as fields, component, port and the file's key path, beside the message;
the violation and the CLI can emit JSON** (owner, 2026-11-06). A message a person reads and a location a tool reads
are two things; CI and RenoVisor's `problems.json` need the second. Line numbers are left for later: the reader may
not keep positions (`RawDocument`, `hisim/energy_system/document.py`). Consequence: the `Fault` fields of §4.1,
`SetupRuleViolation.to_dict()`, `hisim setup-rules check --format json`.

## 3. Terms

- **Setup**: one simulated system, however it is written: a Python setup (`system_setups/*.py`, its
  `setup_function`), an energy-system file (`*.energy_system.yaml`), a test fixture, or an isolation system the
  assembly harness builds.
- **Kind**: the one word a setup declares about itself, from which each rule's `WAIVED_FOR` decides whether that
  rule runs. One of the four values of `SetupKind` (D5): `DEMO` (a demonstration or a test rig: it shows a mechanism;
  its numbers are not a statement about any house or plant), `RESIDENTIAL` (a building people live in: a household
  occupancy, apartments, domestic hot water per person), `COMMERCIAL` (a building people work in: usage profiles
  by zone type, no apartments), `PLANT` (a system without a building: a generator, a storage, a converter and the
  meters around them). The file spells the lowercase values `demo`, `residential`, `commercial`, `plant`.
- **Rule** (a setup rule, class `SetupRule`): one named check on one subject at one stage, with one message per
  fault. A rule has a **code** (`EP-06`, `EF-31`: a letter prefix per family and a number, stable for ever, D13), a
  **name** (kebab-case, `room-heat-needs-receiver`, readable and unique), a **family** (its module), a **stage**,
  and what it **requires** (the codes of rules that must have found nothing for its own result to mean anything,
  D14). The inventory of §6 writes a rule as `<family>.<snake_name>`; the name is the kebab-case form of the second
  part.
- **Retired code**: the code of a rule that was removed; it is listed in `SetupRuleRegistry.RETIRED_CODES` and never
  given to another rule, as `EF-R8` is reserved today (`hisim/energy_system/recording/`).
- **Stage**: when a rule runs. `RuleStage.LOAD`: on the document, before any class is imported (declarative mode
  only). `RuleStage.BUILD`: on the built, wired components, before the first step, in both modes.
  `RuleStage.AFTER_RUN`: on the result table, in the post-processing, before anything is written.
- **Law rule** (descriptive, §6): a rule that states a law of the model or a consistency the code relies on:
  energy is conserved per component and per link, a fluid loop closes and has one pump owner, a port's unit is a
  power or an energy, a meter publishes what it reads, no result is NaN or infinite. Under D2 a kind may waive it
  like any other rule; the word tells a reviewer what the waiver gives up.
- **Domain rule** (descriptive, §6): a rule that states what a correct system of some kind looks like: room heat
  needs a receiver, a bought carrier passes a meter, a heated building reaches its set point.
- **Waiver**: one entry of a rule's `WAIVED_FOR` mapping, a kind and the reason the rule does not run for that
  kind. The `SetupRule` base refuses, when the class is created, a key that is not a `SetupKind` and a reason that is
  empty, so a malformed waiver fails at import.
- **Registry**: the object that collects every rule of the package, answers which rules run at a stage for a
  kind, runs them and raises their faults as one error. It is built on demand, never stored in a module.
- **Fault**: one finding of one rule, a frozen record with the rule's code and name, the message, and the
  location as fields (D15): `component` (the runtime name of the component, or the entry name in a file),
  `port` (the input or output name) and `key_path` (the keys and indices from the file's top level to the value,
  `("components", "Battery1", "inputs", 0)`), each `None` or empty when it does not apply. A stage's faults are
  raised together, as today's `EnergyPortWiringError` and `FluidLoopWiringError` list every fault at once (D14).
- **Skip**: a rule the registry did not run because a rule it requires found faults (D14); reported beside the
  faults with the codes it waited for, never silent.

## 4. Design

### 4.1 The rules package (D1)

```
hisim/setup_rules/
  __init__.py        the package docstring: what a rule is, the stages, how a rule and its waivers are declared
  rule.py            SetupRule (the base class), Fault, RuleStage, SetupRuleViolation (the error)
  kinds.py           SetupKind (D5); nothing else
  registry.py        SetupRuleRegistry: collect(), rules_for(stage, kind), run(stage, subject, kind)
  inventory.py       RuleRecord, Inventory.RECORDS: every rule id with its family, stage, nature and where it lives
  subjects.py        the subject of each stage: LoadedDocument, BuiltSystem, RunResults
  document.py        the structural rules on a loaded document (EF-05, EF-06, EF-08, EF-11, ..., EF-57)
  bindings.py        classes, presets, constructors, config fields (EF-10, EF-13, ..., EF-1B, EF-43)
  sizing.py          the sizing faults the bridge classifies (EF-4A to EF-4X)
  wiring.py          the declarative wiring rules (EF-21 to EF-33, EF-7M, EF-7N, EF-7T, the warnings)
  assemblies.py      the import and port-binding rules (EF-70 to EF-7V, except those above)
  energy_ports.py    the fault lists of Wiring.refuse_a_broken_wiring and room_heat_needs_receiver
  fluid_loops.py     the refusals of FluidLoopWalk.find_loops
  components.py      the simulator's build rules: one kind base, duplicate names, cost declarations
  results.py         after the run: finite results, the component and link balance, the loop faults
  meters.py          after the run: the meter balance, the unmetered ports
```

**A rule.** A subclass of `SetupRule` with class-scoped declarations and one method:

```python
class RoomHeatNeedsReceiver(SetupRule):
    """What it checks: every PRODUCTION port of carrier ROOM_HEAT is read by some component.

    Why: room heat is a transfer into the heated zone, not a loss; an output nobody reads is a
    transfer with a missing end, and the heat would vanish from the balance.

    Example of a fault: ``buffer.HeatToHeatedZoneInWatt declares PRODUCTION of ROOM_HEAT and no
    component reads it``.

    How to fix: list the device under the building's inputs, or set its installation location to
    an unheated space so that the heat leaves as a loss.
    """

    CODE = "EP-06"
    NAME = "room-heat-needs-receiver"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    REQUIRES = frozenset({"EP-05"})  # an input port must pair with the output it reads first
    WAIVED_FOR = MappingProxyType({
        SetupKind.DEMO: "a demo may run an occupancy without a building; the heat it does not place is reported as unlinked",
        SetupKind.PLANT: "a plant has no heated zone; heat a device releases there has no receiver by construction",
    })

    def check(self, system: BuiltSystem) -> Sequence[Fault]:
        ...
```

`CODE` and `NAME` are each unique across the package (D13; the registry's test checks it, and `__init_subclass__`
refuses a code not of the form `<PREFIX>-<nn>`, a name that is not kebab-case, and a code in `RETIRED_CODES`);
`STAGE` picks the subject the rule receives; `ENFORCED` says whether its faults raise or are logged (§4.5);
`REQUIRES` names the codes the rule depends on (D14; `__init_subclass__` cannot see other rules, so the registry
checks at `collect()` that every required code exists, belongs to the same or an earlier stage, and forms no
cycle). A moved file check keeps its `CODE = "EF-31"`, which its messages carry as today, so the error catalogue
of `roadmap/declarative_energy_systems/` and every test that asserts on a message stay valid. `WAIVED_FOR: ClassVar[Mapping[SetupKind, str]]` names the kinds that do not run
the rule, each with its reason (D3 as amended); the base's default is an empty `MappingProxyType({})`, read-only
like every class constant of the package (`coding_style.md` §4), and a rule that waives nothing writes nothing.
`SetupRule.__init_subclass__` checks the mapping when the class is created: a key that is not a `SetupKind` and a
reason that is empty or blank raise a `TypeError` naming the rule, so a malformed waiver fails at import, before
any run, and no test needs to look for it. `check` is pure (D14): it returns every fault it finds and raises
nothing; a rule that needs a value it does not have (a column missing from the table) returns a fault that says
so, and a rule that would read what an earlier rule guards names that rule in `REQUIRES` instead of checking it
again. The class holds no state between calls. Everything is class-scoped or an `Enum`
(`system_docs/coding_style.md` §4); the package writes no file and prints nothing, so the semgrep gates
(`.semgrep/file_writes.yml`, `.semgrep/no_print.yml`) need no entry.

**Codes and names (D13).** The scheme, one letter prefix per family and a two-digit number in the inventory's
order, allocated by the family's PR and never changed afterwards:

| Family (module) | Prefix | Day-one allocation |
|---|---|---|
| `document`, `bindings`, `sizing`, `wiring`, `assemblies` | `EF-` | the codes the checks carry today (§6.1 to §6.4); a moved warning that has none gets the next free code of its block: `EF-34` unconsumed source, `EF-35` unmetered output of a need, `EF-4K` unconsumed fact |
| `energy_ports` | `EP-` | `EP-01` loop-pairing … `EP-05` input-port-pairs, `EP-06` room-heat-needs-receiver (the rows of §6.5 in order) |
| `fluid_loops` | `FL-` | `FL-01` loop-tagged-whole … `FL-08` one-pump-owner |
| `components` | `CB-` | `CB-01` one-kind-base … in the order of §6.5 |
| `results` | `AR-` | `AR-01` finite … in the order of §6.6 |
| `meters` | `MT-` | `MT-01` meter-balance, `MT-02` metered-port-has-a-meter |

A removed rule's code goes into `SetupRuleRegistry.RETIRED_CODES: ClassVar[FrozenSet[str]]` with a one-line comment
saying what it was and in which PR it went; `__init_subclass__` refuses a live rule with a retired code, and the
test of §9 checks that no two rules share a code or a name and that the retired set and the live codes are
disjoint. Reviewers check that the set only grows, as they check that the EF catalogue never renumbers. The
docstring of every rule follows one template, four headings in this order: "What it checks", "Why", "Example of
a fault", "How to fix" (the example of §4.1); `hisim setup-rules show <code|name>` prints it with the
declarations, and every message of a refusal ends with `see: hisim setup-rules show <code>`, so the reader of a
failed run has the fix one command away. The test of §9 checks the four headings in every rule's docstring.

**Every fault, one violation (D14).** `registry.run(stage, subject, kind)` runs every applicable rule of the
stage, in code order, and collects; it raises one `SetupRuleViolation` per stage with every fault of every
enforced rule, ordered by code, then by component and port, and with every skip. A rule whose `REQUIRES` names a
code that found faults in this run, or that was itself skipped, is skipped and listed as `Skip(code, because_of=
(codes,))`; nothing else orders rules, and a rule that runs sees the subject as it is. The faults of the rules
that are not enforced are logged, one line each, with the same fields. *What must be restructured*: the checks
that collect every fault already are `Wiring.refuse_a_broken_wiring`, `FluidLoopWalk.find_loops`,
`LibraryChecker.check` (EF-75), `BalanceCheck`, `MeterCheck` and `FiniteResultsCheck`. The checks that raise at
the first fault today, and whose family PR turns each `raise` into an appended fault, are the structural
validator (`StructuralValidator`, `hisim/energy_system/validation.py`, one `EF-nn` error per raise), the class and
config binder (`ClassBinder`, `ConfigOriginChecker`), the sizing bridge (the first kernel failure), the wiring
checker and the feed, channel and dispatch resolvers (`WiringChecker`, `DynamicConnectionResolver`,
`ChannelMatcher`, `DispatchSignalPlanner`), the assembly port binder (`PortBinder`, `hisim/energy_system/assemblies/binding.py`),
and the simulator's `add_component` and `ComponentWrapper.connect_inputs`. Each message keeps its text, so a test
that asserts on one message still matches; a test that asserts that *only* one fault is reported when two exist
is corrected in that PR.

**A fault's fields (D15).** `Fault` is a frozen dataclass: `code` and `name` of the rule, `stage`, `message` (the
text as today, with the `see:` pointer), `component` (the runtime name, or the entry name in a file; `None` for a
fault of the whole system), `port` (the input or output name; `None`), `key_path` (a tuple of keys and indices
from the file's top level, empty when the subject is not a file; filled by the document, bindings, sizing, wiring
and assembly rules from the location the EF messages print today), `file` (the file's path; `None` in Python
mode). `SetupRuleViolation.to_dict()` gives `{"stage": "build", "kind": "demo", "faults": [{"code", "name",
"stage", "message", "component", "port", "key_path", "file"}, ...], "skipped": [{"code", "because_of": [...]}]}`, which
`hisim setup-rules check --format json` prints and which RenoVisor's `run` may write into `problems.json` in place
of a bare traceback (its exit code 5 case; out of scope here, the field names are what it would read). Line and
column are an extension: the reader keeps no positions, and adding them means a position-keeping reader for
`RawDocument` (a later bead), after which `Fault` gains `line` and `column` without changing anything else.

**The subjects.** One frozen object per stage, built by the hook and handed to every rule of that stage:
`LoadedDocument` (the `EnergySystemFile` model and the raw document for locations), `BuiltSystem` (the wired
components in evaluation order, the declared kind, the path resolver), `RunResults` (the result table, the
outputs, the declared ports, the kind). A rule reads the subject and nothing else: no import of
`hisim.components`, no `SimRepository`, so the package stays importable by the schema exporter and the editor-side
checks that import no component today (`hisim/energy_system/validation.py` keeps that property in its new place,
`tests/test_energy_system_layering.py` keeps proving it).

**The registry.** `SetupRuleRegistry.collect()` walks the package's modules (`pkgutil.iter_modules` over `hisim.setup_rules`)
and gathers every `SetupRule` subclass that declares a `CODE`; it returns a registry object the caller keeps for the
calculation. Nothing is cached at module level (`coding_style.md` §4: no mutable module state); the simulator
holds the registry it built, as it holds its components. `registry.run(stage, subject, kind)` refuses a subject
whose kind is not declared (D7), picks the rules of the stage, drops those whose `WAIVED_FOR` names the kind,
runs each, and raises one `SetupRuleViolation` listing every fault of the enforced rules and every skip, logging
the faults of the others (§4.5, D14). The order of rules within a stage is the order of their codes, so the
message is stable.

**The hooks.** Four call sites, each one line of logic:

| Stage | Hook | Replaces |
|---|---|---|
| `LOAD` | `hisim/energy_system/loader.py:load_energy_system`, after the reader built the model | `validate_structure` |
| `BUILD` (declarative) | `hisim/energy_system/executor.py:EnergySystemExecutor.build`, after `wire_energy_system` | `validate_classes`, the sizing bridge's classification, `WiringChecker`, `check_consuming_outputs`, `check_double_count` |
| `BUILD` (both modes) | `hisim/simulator.py:Simulator.run_all_timesteps`, after `connect_all_components` | `check_cost_declarations`, `FluidLoopWalk.find_loops`, `Wiring.refuse_a_broken_wiring` |
| `AFTER_RUN` | `hisim/postprocessing/postprocessing_main.py:PostProcessor.run`, first thing | `FiniteResultsCheck.require_finite`, the failing half of `EnergyBalanceReport` |

The reader reads `setup_kind` with the rest of the model (D7), so the document rules of the `LOAD` stage see the
kind as every other stage does.
The declarative build rules run before the simulator's, as today; the simulator's run in both modes. The energy
balance *report* (the collection of ports, the Sankey, `balance_report.json`) stays in
`hisim/postprocessing/energy_balance/`: it computes, the rules of `results.py` and `meters.py` judge what it
computed. The component constructors' own refusals (`EnergyPort.__post_init__`, a config's `__post_init__`) are
not rules of the system and stay (D10).

**The errors.** `SetupRuleViolation(ValueError)` carries the faults. The existing error classes stay as subclasses so
that every `except` and every test keeps working: `EnergySystemFormatError`, `EnergySystemBindingError`,
`EnergySystemSizingError`, `EnergySystemWiringError`, `EnergySystemAssemblyError` (`hisim/energy_system/errors.py`),
`EnergyPortWiringError`, `FluidLoopWiringError`, `EnergyBalanceError`, `NonFiniteResultsError`. A family raises its
own subclass; the message texts do not change in the migration (§8: a migration PR moves code and changes no
message, so the tests that assert on messages prove the move), except that each gains the `see: hisim setup-rules
show <code>` pointer at its end (D13) and a stage may now list several (D14).

### 4.2 Every rule applies unless waived; a rule waives itself (D2, D3)

A rule applies to every kind until its own `WAIVED_FOR` names a kind with a reason (D3 as amended). There is no
central list: what a kind does not run is read off the rules, one `WAIVED_FOR` each, and nothing else has to be
kept in step with them. A forgotten waiver fails loudly (a demo is refused by a rule it should not see, and the fix
is one entry with a reason on that rule) instead of silently. Under D2 any rule may carry a waiver, the balance and
the loop rules included: the inventory's nature column (§6) says which rules are laws of the model, so that a
reviewer of a waiver on one of them knows the run then publishes numbers the model does not vouch for, but nothing
in the code refuses such an entry.

**What a waiver looks like.** On the rule, as in the example of §4.1: `WAIVED_FOR = MappingProxyType({SetupKind.DEMO:
"<reason>"})`. The reason is a sentence a reader of the overview understands without the rule's code. A waiver is
global per kind, never per setup: a per-setup waiver would be the switch the owner wants to avoid, and a setup
whose kind does not fit a rule declares another kind or fixes the system.

**The day-one waivers**, three entries on two rules, nothing else:

| Rule | Kind | Reason |
|---|---|---|
| `energy_ports.room_heat_needs_receiver` | `demo` | a demo may run an occupancy without a building; the heat it does not place is reported as unlinked |
| `energy_ports.room_heat_needs_receiver` | `plant` | a plant has no heated zone; heat a device releases there has no receiver by construction |
| `meters.metered_port_has_a_meter` | `demo` | a demo may leave a bought carrier unmetered; the balance reports the port as unmetered |

**The overview is generated, never written.** The one place that shows every waiver per kind is the registry,
through a CLI command: `hisim setup-rules list` prints every rule with its code, name, family, stage, nature,
whether it is enforced, what it requires and the kinds that waive it with their reasons, in code order;
`hisim setup-rules waivers [--kind <kind>]` prints the waivers grouped by kind; `hisim setup-rules show <code|name>`
prints one rule's docstring (the four headings of D13) and declarations; `hisim setup-rules check
<file.energy_system.yaml> [--stage load|build] [--format text|json]` runs the `LOAD` and `BUILD` rules on a file
without simulating (building it as `hisim energy-system facts` does) and prints the faults and skips, as text or
as the JSON of D15, exiting non-zero on an enforced fault.
The commands live beside `hisim energy-system describe` (`hisim/cli.py`, `hisim/cli_render.py`) and read nothing
but `SetupRuleRegistry.collect()`, so they cannot disagree with the code. No overview page is committed: a committed
page would be the central table under another name, and the inventory of §6 is a design record of this spec's
date, not a page the code keeps in step. The test of §9 runs `hisim setup-rules list` on the registry and checks that
every rule and every waiver appears once with its reason, which is the same check a committed page would need,
without the page.

### 4.3 One kind per setup (D4, D5)

A setup declares one of four kinds (§3). What each one is for:

- **`demo`**: a demonstration or a test rig. The pattern of the setups without a building and with an
  occupancy (`dynamic_components`, `two_pv_arrays`), the random-number examples, and every isolation system the
  assembly harness builds. A demo keeps its goldens (D8).
- **`residential`**: a house people live in; every household twin, every RenoVisor file. Every rule applies.
- **`commercial`**: a building people work in. HiSim's building is TABULA's (every `building_code` a row of the
  residential typology) and its occupancy is a household profile of the LoadProfileGenerator, so a commercial
  setup today runs a TABULA house with an office label; under D6 nothing refuses that. What a commercial source
  would change, for the later extension (bead .12): the usage profiles come by zone type (DIN V 18599-10's hours,
  people, equipment and lighting per m², instead of a household's persons and devices), the design heating load is
  the same DIN EN 12831 steady state (`BuildingInformation.get_max_thermal_building_demand`,
  `hisim/components/building/information.py`), domestic hot water is per use, not per person
  (`HouseholdWarmWaterDemandConfig`, `hisim/components/configuration.py`, 2,000 kWh/a), there are no apartments (the
  per-apartment sizing laws, `SimpleDHWStorageConfig.VOLUME_LAW` 250 l per apartment, the collector's 4 m² per
  apartment, the boiler's 2,500 W per apartment floor, do not apply) and the comfort band is the working hours'.
  The rules that would then differ by kind are the occupancy and demand rules, the per-apartment sizing rules, the
  KPIs per apartment and the comfort rules; the wiring, port, loop, meter and balance rules are the same for both.
  Day one, `commercial` has no waiver: it runs every rule a residential setup runs.
- **`plant`**: a system without a building: `electrolyzer_with_renewables` (a CSV load, a transformer, an
  electrolyzer and its controller, two meters). The building rules of §6.8 have no subject in a plant and never
  fire; the one rule a plant waives is the room-heat rule (§4.6).

**A district with mixed uses** (a residential and a commercial building in one run) has one kind under D4 and is
a known limitation: HiSim's identity model allows several buildings (`ComponentID.building` and `.unit`,
`hisim/config/base.py`; the KPI post-processing groups by `ComponentID.building_label` and aggregates a district
through `DistrictNames.is_district`, `hisim/postprocessing/kpi_computation/compute_kpis.py`), but no shipped setup
has two buildings and the recorder refuses one (EF-R2). A per-building scope is designed when a district twin
exists (bead .13).

### 4.4 The declaration is mandatory (D6, D7)

A kind is a switch and nothing checks that it fits (D6); what is checked is that it exists (D7). The registry
refuses to run a stage for a subject without a kind, before the first step in both modes, with one message
("this setup declares no kind: write `setup_kind: residential | commercial | plant | demo` into the file, or call
`my_sim.declare_setup_kind(SetupKind.RESIDENTIAL)` in the setup function"). It is the registry's precondition,
not a rule: a rule is what the kind decides to run, so it cannot be the thing that finds the kind missing.

- **Energy-system file**: a new mandatory top-level key `setup_kind` in a v3 and v4 energy-system file (not in an
  assembly file). It joins `EnergySystemFile.TOP_LEVEL_KEYS` (`hisim/energy_system/model.py`, `extra="forbid"`),
  the reader (`EnergySystemReader._build`, EF-03 for an unknown key and now for the missing one), the emitter's
  order (`hisim/energy_system/emitter.py`), the schema export (`SchemaExporter._top_level`,
  `hisim/energy_system_v3.schema.json`, `tests/test_energy_system_schema.py` requires regeneration in the same
  change) and the executor, which copies it onto the simulator as it copies `description` and `scenario_name`
  (`hisim/energy_system/executor.py`). A hand-authored file (`gas_boiler_household`, the composed files,
  `uc1.realized`, the fixtures) states it; a recorded twin gets it from the recorder.
- **Python setup**: `my_sim.declare_setup_kind(SetupKind.RESIDENTIAL)` as the first line of `setup_function`. Not a
  module constant (`coding_style.md` §4) and not a `SimulationParameters` field (D7).
- **The recorder**: `SystemObserver.observe` (`hisim/energy_system/recording/observe.py`) reads the simulator's
  kind into `RecordedSystem`, `EnergySystemBuilder.build` writes it, and the freshness gate
  (`scripts/record_all_setups.py --check`, `.github/workflows/energy-system-freshness.yml`) re-records every twin
  once with the new key.
- **Fixtures and the harness**: the test fixtures state their kind in the file; the assembly harness declares
  `demo` for every isolation system it builds (`isolation_document`,
  `hisim/energy_system/assemblies/testing/isolation.py`), since an isolation system is a rig by construction; its
  own contracts (`check_room_heated`, `check_heat_delivered`) keep running, they are the harness's, not the
  registry's. The composed twins and `uc1.realized` state `residential`.
- **RenoVisor**: the translator writes `setup_kind: residential` into every file it builds (its bases are the
  residential grouped twins, which carry it already; `hisim/renovisor/translate.py` may only write config values,
  constructor arguments and a variant selection, so the base's key passes through unchanged).

### 4.5 Enforcement: refuse, or log

A rule declares `ENFORCED = True` (its faults raise) or `False` (its faults are logged and reported, as the balance
reports unlinked ports and undeclared components today, `EnergyBalanceReport.conclude`,
`hisim/postprocessing/energy_balance/export.py`). The per-run switch of Q-H (the meter balance and the unmetered
ports fail only while no component is undeclared) stays a property of those two rules, computed from the subject,
not a kind: an undeclared component makes the meter's reading unverifiable whatever the kind. The acceptance that
turns "unlinked" into a failure (PR 6 of the complete Sankey spec) then flips one attribute.

### 4.6 The room-heat rule lands scoped

`energy_ports.room_heat_needs_receiver` (D-h of the device-location spec) is the first rule written directly into
the package: stage `BUILD`, enforced, and it declares two waivers on itself in its `WAIVED_FOR` (§4.1):

- **`demo`**: "a demo may run an occupancy without a building; the heat it does not place is reported as
  unlinked". The occupancy's two `ROOM_HEAT` outputs (`HeatingByResidents`, `HeatingByDevices`) are `PRODUCTION`
  ports with no boundary default, so the balance lists them as unlinked after the run, as it does today, and the
  run finishes.
- **`plant`**: "a plant has no heated zone; heat a device releases there has no receiver by construction". A plant
  has no building, so no device's `installation_location` can resolve to a heated zone through the building's
  technical-room fact (D-a of the device-location spec); a file that pins a device to `heated_zone` anyway
  publishes `HeatToHeatedZoneInWatt` that nothing reads, which the waiver lets through as an unlinked port instead
  of a refused build. The plant in the repository, `electrolyzer_with_renewables`, has no located device, so the
  waiver is for the file that does.

Consequences for the systems of §1: `dynamic_components` declares `demo`; `two_pv_arrays` declares `demo`; the
dispatch test and the assembly-crossing test declare `demo` or get a building, whichever the test is about; the
isolation runs are `demo` by construction (§4.4). Every residential twin has a building and is not affected; a
`commercial` setup is not waived (a commercial building takes room heat as a house does). PR #1211 is rebased onto
the mechanism PR and adds the rule to `energy_ports.py` with its two waivers on the class, instead of a fourth fault list in
`Wiring.refuse_a_broken_wiring`.

## 5. What changes and what does not

- **Results**: no number moves in the mechanism and migration PRs: each keeps results and messages bit-identical;
  the goldens (`golden_references/`) are not re-blessed by them. One PR moves results: D11's residents for
  `simple_air_conditioner_household_building_sizer` (bead .10), measured and blessed in that PR.
- **Public interfaces**: the file gets a mandatory top-level key `setup_kind` (`demo | residential | commercial |
  plant`); `Simulator.declare_setup_kind(SetupKind)`; the error classes keep their names and messages, and gain a
  base `SetupRuleViolation` with the faults' rule ids. The heads-up on renovisorissues
  (`system_docs/results_and_interfaces.md` §5): every file the translator writes carries `setup_kind: residential`;
  a request carries nothing new.
- **Goldens and twins**: every twin is re-recorded once for the `setup_kind` key (freshness gate); the goldens are
  unchanged (the KPI leaves do not carry the key), except the one setup of D11.
- **Modules that shrink**: `hisim/postprocessing/energy_balance/ports.py` loses the fault lists of `Wiring`,
  `hisim/fluid_loops.py` its refusals (the walk's discovery of loop ids stays, the balance needs it),
  `hisim/energy_system/wiring_checks.py` and `validation.py` go whole, `hisim/simulator.py` loses
  `check_cost_declarations`, `hisim/postprocessing/finite_results.py` goes whole.

## 6. The rule inventory

Every check HiSim applies today, by family. Columns: the proposed id (or today's name where the rule is not moved);
what it checks; where it lives today (file, `Class.method`); when it runs; whether it refuses (E) or only logs (L);
its nature; the target module in `hisim/setup_rules/`. The nature is descriptive (D2: any rule may be waived; the column
says what a waiver gives up): **L** a law of the model or a consistency the code relies on; **D** a domain rule
about the whole system; **B** a domain rule about a building (no subject in a plant); **R** a residential
assumption; **P** a parse fault, stays in the reader (D9); **K** a device's own physics guard, stays in the
component (D10); **C** a CI gate or harness contract. "Waived for" names the kinds in the rule's `WAIVED_FOR` on day
one (§4.2); a row without one applies to every kind. The id column writes `<family>.<snake_name>`: the rule's
`NAME` is the kebab-case form of the name part, and its `CODE` is the EF code in brackets where the row shows
one, else allocated by the family's PR in the row order of the scheme of §4.1 (D13). Paths are relative to the
repository root; `es/` is `hisim/energy_system/`, `eb/` is
`hisim/postprocessing/energy_balance/`, `cmp/` is `hisim/components/`. Since PR 1 the records live as data in
`hisim/setup_rules/inventory.py` (`Inventory.RECORDS`, one `RuleRecord` per row below, the paths written out), which
is their source from then on: a family PR updates the records it moves there, not these tables.

### 6.1 Reading the document (declarative mode, stage LOAD)

| Rule | Checks | Lives today | When | E/L | Class | Target |
|---|---|---|---|---|---|---|
| EF-00 | the file suffix is `.yaml`/`.yml` | `es/document.py:RawDocument._read_text`; `es/executor.py:SimulationParametersReader.read` | load | E | P | stays |
| EF-01 | `schema_version` present and in (3, 4); a v3 file has no `imports`; an assembly file is v4 | `es/loader.py:EnergySystemReader._check_schema_version`; `es/assemblies/reader.py:AssemblyReader.build` | load | E | P | stays |
| EF-02 | no YAML key written twice | `es/document.py:RawDocument._reject_duplicate_keys` | load | E | P | stays |
| EF-03 | readable YAML, a mapping, no unknown top-level key, some components | `es/document.py:RawDocument._parse`; `es/loader.py:EnergySystemReader._build` | load | E | P (a missing `setup_kind` joins it) | stays |
| EF-07 | a block has the YAML type expected; a simulation-parameters file, address table or `consuming` block is well-formed | `es/document.py:RawDocument.malformed`; `es/address_table.py`; `es/executor.py:SimulationParametersReader` | load / build | E | P | stays |
| `document.no_absolute_path` (EF-05) | no absolute filesystem path in a value | `es/validation.py:StructuralValidator._check_no_absolute_paths` | load, again at build | E | L | `document.py` |
| `document.reference_syntax` (EF-06) | no wildcard or relative reference | `es/names.py:NameRules._reference_error` | load | E | L | `document.py` |
| `document.valid_name` (EF-08) | a component, group, variant or option name is an identifier | `es/names.py:NameRules.check_identifier`; `es/validation.py:StructuralValidator._check_expanded_names` | load | E | L | `document.py` |
| `document.no_metadata_on_a_plain_run` (EF-09) | a file with `metadata:` (a run record) is only re-run | `es/executor.py:EnergySystemExecutor.check_metadata` | build | E | L | `document.py` |
| `document.one_configuration_origin` (EF-11, EF-12) | an entry names a preset or a constructor, never both, and at least one of preset, constructor, config | `es/validation.py:StructuralValidator._check_configuration_origin` | load | E | L | `document.py` |
| `document.constructor_block` (EF-14) | a constructor block names exactly one constructor | `es/entries.py:EntryReader._constructor` | load | E | L | `document.py` |
| `document.known_entry_keys` (EF-18) | an entry, group, variant or option uses only its closed key set | `es/entries.py:EntryReader.entry`; `es/loader.py:EnergySystemReader._check_block_keys` | load | E | L | `document.py` |
| `document.input_item_shape` (EF-19) | an input item is a defaults, wire or feed item | `es/entries.py:EntryReader._check_item_keys` | load | E | L | `document.py` |
| `document.known_source` (EF-20) | an input item names a component the selected world has | `es/validation.py:StructuralValidator._check_reference_closure` | load | E | L | `document.py` |
| `document.one_input_spelling` (EF-24) | one consumer-source pair is spelled as wires or as feeds, not both; no defaults item repeated | `es/validation.py:StructuralValidator._require_new_spelling` | load | E | L | `document.py` |
| `document.no_duplicate_feed` (EF-25) | one source output fed once into an aggregator (file level) | `es/validation.py:StructuralValidator._check_input_shapes` | load | E | L | `document.py` |
| `document.no_duplicate_wire` (EF-26) | one input wired once (file level) | same | load | E | L | `document.py` |
| `document.known_sizing_source` (EF-40, EF-41) | `sizing_sources` names an existing component and the fact it maps | `es/validation.py:StructuralValidator._check_reference_closure` | load | E | L | `document.py` |
| `document.no_nested_group` (EF-50) | an entry carries no group keys | `es/entries.py:EntryReader.entry` | load | E | L | `document.py` |
| `document.one_group_per_component` (EF-51) | a component is in at most one group | `es/validation.py:StructuralValidator._check_names` | load | E | L | `document.py` |
| `document.unique_name` (EF-52) | component, group, variant and expanded import names do not collide | `es/validation.py:StructuralValidator._check_names`; `es/assemblies/expansion.py:ImportExpander._check_names` | load / build | E | L | `document.py` |
| `document.group_enabled_flag` (EF-53) | a group's `enabled` is a bool | `es/loader.py:EnergySystemReader._build_groups` | load | E | L | `document.py` |
| `document.group_not_empty` (EF-54) | a group lists a component | same | load | E | L | `document.py` |
| `document.known_variant_option` (EF-55) | `selected` names an option | `es/loader.py:EnergySystemReader._build_variants` | load | E | L | `document.py` |
| `document.variant_not_empty` (EF-56) | a variant offers an option | `es/loader.py:EnergySystemReader._build_options` | load | E | L | `document.py` |
| `document.one_variant_per_component` (EF-57) | a component is declared by at most one variant | `es/validation.py:StructuralValidator._check_option_member` | load | E | L | `document.py` |
| `document.not_cut_from_v1` (EF-73) | a construct cut from v1 is refused by name | `es/imports_reader.py:CutConstructs.error` | load | E | L | `document.py` |

### 6.2 Classes, presets, configuration and sizing (declarative mode, stage BUILD)

| Rule | Checks | Lives today | When | E/L | Class | Target |
|---|---|---|---|---|---|---|
| `bindings.class_importable` (EF-10) | the dotted class path imports a HiSim component with an annotated config | `es/classes.py:ClassBinder._import_component_class` | build | E | L | `bindings.py` |
| `bindings.known_preset` (EF-13) | the preset is declared by the config class | `es/bindings.py:ConfigOriginChecker.resolve_preset` | build | E | L | `bindings.py` |
| `bindings.known_constructor` (EF-15) | the constructor is declared by the config class | `es/bindings.py:ConfigOriginChecker.resolve_constructor` | build | E | L | `bindings.py` |
| `bindings.constructor_arguments` (EF-16) | no unknown and no missing required argument | `es/bindings.py:ConfigOriginChecker.check_constructor_arguments` | build | E | L | `bindings.py` |
| `bindings.known_config_field` (EF-17) | every config key is a field; `component_id` is not set in `config` | `es/bindings.py:ConfigOriginChecker.check_config_keys` | build | E | L | `bindings.py` |
| `bindings.decodable_value` (EF-1A) | a value decodes into its field's type | `es/codec.py:ConfigValueCodec.*`; `es/configure.py:EntryConfigurator._realize_origin` | build | E | L | `bindings.py` |
| `bindings.auto_only_on_a_sized_field` (EF-1B) | `AUTO` only on a field with a sizing law | `es/bindings.py:ConfigOriginChecker.check_config_keys` | build | E | L | `bindings.py` |
| `bindings.sizing_fact_is_read` (EF-43) | `sizing_sources` names a fact the class's laws read | `es/bindings.py:ConfigOriginChecker.check_sizing_facts` | build | E | L | `bindings.py` |
| `bindings.path_variable_known` (EF-04) | a `${name}` names a registered path variable | `es/path_resolver.py:PathResolver._substitute` | build | E | L | `bindings.py` |
| `sizing.fact_provided` (EF-4A) | a fact a law reads is provided by somebody | `hisim/config/engine.py`, classified by `es/sizing_bridge.py:KernelFailure.classify` | build | E | L (the vocabulary is the house's, §4.3; the rule is not) | `sizing.py` |
| `sizing.provider_unambiguous` (EF-4B) | several providers need a source line | same | build | E | L | `sizing.py` |
| `sizing.source_is_a_provider` (EF-4C) | a source line names a component that declares the fact | same | build | E | L | `sizing.py` |
| `sizing.fact_not_null` (EF-4D) | the provider gives a value | same | build | E | L | `sizing.py` |
| `sizing.source_shape` (EF-4E) | the mapping shape fits the law's cardinality | same | build | E | L | `sizing.py` |
| `sizing.no_field_cycle` (EF-4F) | sibling `Self(...)` reads form no cycle | same | build | E | L | `sizing.py` |
| `sizing.one_read_shape` (EF-4G) | a fact is read many-fold or once, not both; no repeated provider | same | build | E | L | `sizing.py` |
| `sizing.unique_instance` (EF-4H) | every config has `component_id.name`, no two share it | `hisim/config/engine.py:SizingFactEngine.register` | build | E | L | `sizing.py` |
| `sizing.sized_together` (EF-4J) | a `SizedTogether` group is stated whole or not at all | `hisim/config/sizing.py:SizedTogether.check` | build | E | L | `sizing.py` |
| `sizing.resolved` (EF-4X) | the kernel made progress and no law raised | `es/sizing_bridge.py:KernelFailure.classify` | build | E | L | `sizing.py` |
| `sizing.unconsumed_fact` | a provided fact nobody reads | `es/sizing_bridge.py:unconsumed_warnings` | build | L | D | `sizing.py` |
| `sizing.sizing_source_enabled` (EF-42) | a scalar sizing source is not in a disabled group or unselected option | `es/groups.py:GroupExpander._dangling_scalar_error`; `es/assemblies/addresses.py:_rewrite_sizing` | build | E | L | `sizing.py` |

The sizing kernel's own refusals (`hisim/config/sizing.py`, `laws.py`, `engine.py`, `contributions.py`,
`context.py`: `concrete()` on an unresolved field, `NothingToSizeError`, a `Self()` read before its law, a
`FactContribution` naming a fact the context lacks, a null fact, a deadlock) are what the bridge classifies into
EF-4A to EF-4X; they are the kernel's and stay, the rule is the classification. The config-class rules at class
creation (`ConfigBase.__init_subclass__`: `MAIN_CLASS` present and resolvable; `hisim/config/presets.py`:
`preset_<wire>` naming, `@preset` above `@classmethod`, the builder signature; `_refuse_ambiguous_enum_spellings`)
run when HiSim is imported, concern the code and not a setup, and stay (L, not moved), covered by
`tests/test_config_contracts.py`, `test_presets.py`, `test_config_main_class.py`.

### 6.3 Wiring (declarative mode, stage BUILD)

| Rule | Checks | Lives today | When | E/L | Class | Target |
|---|---|---|---|---|---|---|
| `wiring.output_exists` (EF-21) | a wire, feed or consuming output names an existing output; a written wire never names a derived one; default feeds are unambiguous | `es/wiring_checks.py:WiringChecker._check_ports_exist`, `_check_written_wires_avoid_derived_ports`; `es/feed_resolution.py`; `es/aggregator_ports.py:AggregatorPortChecker.check_ports_were_created` | build | E | L | `wiring.py` |
| `wiring.input_exists` (EF-22) | the same for inputs | same | build | E | L | `wiring.py` |
| `wiring.defaults_declared` (EF-23) | a bare defaults item has default connections declared for the source class | `es/wiring.py:WiringPlanner._expand_default_item` | build | E | L | `wiring.py` |
| `wiring.no_input_fed_twice` (EF-26) | after default expansion, one input has one wire | `es/wiring_checks.py:WiringChecker._check_no_input_is_fed_twice` | build | E | L | `wiring.py` |
| `wiring.feed_unique` (EF-25) | one participant output enters an aggregator once | `es/aggregator_ports.py:AggregatorPortChecker.check_participant_ports_are_unique` | build | E | L | `wiring.py` |
| `wiring.target_is_an_aggregator` (EF-27) | feeds go to a component with dynamic channels | `es/feed_resolution.py:DynamicConnectionResolver._require_channels` | build | E | L | `wiring.py` |
| `wiring.channel_matches` (EF-28) | a feed's tags match exactly one channel | `es/channel_matching.py:ChannelMatcher.match` | build | E | L | `wiring.py` |
| `wiring.dispatch_rule` (EF-29) | a dispatch block where and only where the channel dispatches; no rank on a forbidding channel; allowed dispatch tags | `es/channel_matching.py:ChannelMatcher._check_dispatch_rule`, `_check_dispatch_tags` | build | E | L | `wiring.py` |
| `wiring.known_tag` (EF-2A) | a component-type or flow tag is a known enum member | `es/channels.py:TagDecoder.component_type`, `flow_tags` | build | E | L | `wiring.py` |
| `wiring.dispatch_signal_unique` (EF-2B) | two feeds do not claim one control signal | `es/dispatch_signals.py:DispatchSignalPlanner._refuse_if_claimed` | build | E | L | `wiring.py` |
| `wiring.port_types_agree` (EF-30) | load type and unit agree across a wire or feed (`ANY` matches) | `es/wiring_checks.py:WiringChecker._check_port_types_agree`; `es/channel_matching.py:ChannelMatcher._check_port_types` | build | E | L | `wiring.py` |
| `wiring.mandatory_input_connected` (EF-31) | a mandatory input is fed; `allow_unconnected_mandatory` only logs | `es/wiring_checks.py:WiringChecker._check_mandatory_inputs_are_connected` | build | E (L when allowed) | L (the inputs that make a device need a building are listed in §6.8) | `wiring.py` |
| `wiring.port_name_free` (EF-32) | feed resolution creates no port name that exists | `es/aggregator_ports.py:AggregatorPortChecker.check_port_names_are_free` | build | E | L | `wiring.py` |
| `wiring.constructed` (EF-33) | a component constructor did not raise | `es/wiring.py:ComponentBuilder._construct` | build | E | L | `wiring.py` |
| `wiring.consuming_output_carrier` (EF-7M) | a consuming output's energy port carries the need's carrier | `es/wiring_checks.py:check_consuming_outputs` | build | E | L | `wiring.py` |
| `wiring.meter_feed_count` (EF-7N) | the meter's class declares exactly one feed per consuming output | `es/wiring_checks.py:meter_feed_of` | build | E | L | `wiring.py` |
| `wiring.no_double_count` (EF-7T) | an aggregator does not read another aggregator's output while both observe one flow | `es/wiring_checks.py:check_double_count` | build | E | L | `wiring.py` |
| `wiring.unconsumed_source` | a component feeds nobody | `es/wiring_checks.py:unconsumed_sources` | build | L | D | `wiring.py` |
| `wiring.unmetered_output_of_a_need` | a metered consumer's other outputs of the same carrier are named in the need | `es/wiring_checks.py:unmetered_outputs` | build | L | D | `wiring.py` |

### 6.4 Assemblies and imports (declarative mode, stage BUILD)

| Rule | Checks | Lives today | When | E/L | Class | Target |
|---|---|---|---|---|---|---|
| EF-70 | an assembly or `imports` block is well-formed; the assembly's `kind` is `assembly` | `es/imports_reader.py:ImportsReader.shape_error`; `es/assemblies/reader.py:AssemblyReader.build` | load / build | E | P | stays |
| `assemblies.found` (EF-71) | the assembly is in one library directory | `es/assemblies/resolver.py:AssemblyResolver.resolve` | build | E | L | `assemblies.py` |
| `assemblies.found_once` (EF-72) | not in two | same | build | E | L | `assemblies.py` |
| `assemblies.library_valid` (EF-75) | the assembly file passes the library check (the 45 problems `LibraryChecker.check` lists: name equals path, members load, parameters described with defaults, units and ranges, constraints consistent, variants cover the selector, ports and placeholders name members, a `tests:` contract with bounds, monotone and expect entries) | `es/assemblies/library.py:LibraryChecker.check`, `require_valid` | build; CI `tests/assemblies/test_assembly_library.py` | E | L | `assemblies.py` (the checker moves whole; its problems are one rule with 45 messages) |
| `assemblies.parameter_valid` (EF-76) | an import parameter has the type, value and range its declaration states | `es/assemblies/parameters.py:select` | build | E | L | `assemblies.py` |
| `assemblies.constraint_holds` (EF-77) | `exactly_one_of` / `all_or_none_of` hold | same | build | E | L | `assemblies.py` |
| `assemblies.parameter_unit` (EF-78, EF-79) | a parameter's unit equals its field's; a fed field has a unit | `es/assemblies/library.py:LibraryChecker._unit` | build / CI | E | L | `assemblies.py` |
| `assemblies.port_has_a_partner` (EF-7A) | a required port finds a candidate; a many-fact port a provider | `es/assemblies/binding.py:PortBinder.decide` | build | E | L | `assemblies.py` |
| `assemblies.port_unambiguous` (EF-7B) | several candidates need a verb | same | build | E | L | `assemblies.py` |
| `assemblies.required_port_not_declined` (EF-7C) | no `none:` on a required port | same | build | E | L | `assemblies.py` |
| `assemblies.bound_partner_exists` (EF-7D) | the bind target exists | same | build | E | L | `assemblies.py` |
| `assemblies.optional_port_decided` (EF-7E) | an optional port with candidates carries a verb | same | build | E | L | `assemblies.py` |
| `assemblies.verb_on_an_active_port` (EF-7F) | no verb on a switched-off port | same | build | E | L | `assemblies.py` |
| `assemblies.known_port` (EF-7G) | a verb names an existing port of the right kind | `PortBinder.bind` | build | E | L | `assemblies.py` |
| `assemblies.bound_output_is_read` (EF-7H) | a bound provided output is one the port's wires name | `PortBinder.provided` | build | E | L | `assemblies.py` |
| `assemblies.port_contract` (EF-7J) | a port fits its members (carrier, fact, circuit lowering, observer, controllables); placeholders have counterparts | `PortBinder._carrier`, `_land_fact`, `_lower_circuit`, `_observe`, `_plan_controllables`; `es/assemblies/expansion.py:ImportExpander` | build | E | L | `assemblies.py` |
| `assemblies.circuit_ends_match` (EF-7K) | both ends of a circuit binding name one circuit, written consistently | `PortBinder._joined`, `resolve` | build | E | L | `assemblies.py` |
| `assemblies.one_provider_per_carrier` (EF-7L) | a carrier has exactly one provider; a provider has a bound consumer; a required need a provider | `PortBinder._carrier`, `bind` | build | E | **D** (one site with one supply per carrier: a district or a plant with two grid connections is refused; today no setup has two) | `assemblies.py` |
| `assemblies.order_valid` (EF-7P) | an evaluation `order` names each number once; recorded members follow the record's sequence | `es/assemblies/addresses.py:sequence`; `expansion.py:_check_recorded_sequence` | build | E | L | `assemblies.py` |
| `assemblies.observer_selection` (EF-7S) | an `observes:` selector resolves | `es/assemblies/selection.py:SelectionPlan._select` | build | E | L | `assemblies.py` |
| `assemblies.actuation_selection` (EF-7U) | a controllable selection resolves | `SelectionPlan.__call__` | build | E | L | `assemblies.py` |
| `assemblies.weight_free` (EF-7V) | a derived weight reaches no other kind's base weight | `SelectionPlan._refuse_collision` | build | E | L | `assemblies.py` |

### 6.5 The built system (both modes, stage BUILD, before the first step)

| Rule | Checks | Lives today | When | E/L | Class | Target |
|---|---|---|---|---|---|---|
| `components.one_kind_base` | the class inherits exactly one of the five kind bases | `hisim/component.py:ComponentKinds.of`, from `Simulator.add_component` | build | E | L | `components.py` |
| `components.unique_name` | no second component with one `component_id.key` | `hisim/simulator.py:Simulator.add_component` | build | E | L | `components.py` |
| `components.unique_output_key` | no two outputs with one full name | `hisim/component_wrapper.py:ComponentWrapper._register_output` | build | E | L | `components.py` |
| `components.has_outputs` | a component registered at least one output | `ComponentWrapper.register_component_outputs` | build | E | L | `components.py` |
| `components.name_is_identity` | the constructor name equals `config.component_id.key`; the name is an identifier; the config is a `ConfigBase`; no `AUTO` left; parameters set | `hisim/component.py:Component.__init__` | build (construction) | E | L | stays (a constructor contract, D10) |
| `components.connect_input_target` | `connect_input` names an existing input, once | `hisim/component.py:Component.connect_input` | build | E | L | `components.py` |
| `components.default_connections_wellformed` | a default-connection list is non-empty, from one source class, and the connections asked for exist | `Component.add_default_connections`, `get_default_connections`; `hisim/dynamic_component.py` | build | E | L | `components.py` |
| `components.load_type_agrees` | input and output share a load type (`ANY` matches) | `hisim/component_wrapper.py:ComponentWrapper.connect_inputs` | build | E | L | `components.py` (one rule with EF-30) |
| `components.unit_agrees` | concrete units agree; `Units.ANY` warns | same | build | E / L | L | `components.py` |
| `components.mandatory_input_connected` | a mandatory input has a source (`allow_unconnected_mandatory` warns) | same | build | E / L | L (see §6.8) | `components.py` (one rule with EF-31) |
| `components.auto_connect_possible` | automatic connection finds default connections and one matching source | `Simulator.connect_everything_automatically`, `connect_dynamic_component_automatically` | build | E | L | `components.py` |
| `components.no_duplicate_dynamic_feed` | one source output enters a dynamic component once | `hisim/dynamic_component.py:DynamicComponent.add_component_input_and_connect` | build | E | L | `components.py` (one rule with EF-25) |
| `components.dispatch_port_unique` | an aggregator has no two dispatch ports of one name or one type on one weight | `DynamicComponent._refuse_a_dispatch_port_this_aggregator_already_has` | build | E | L | `components.py` |
| `components.default_connection_tag` | a default connection's tag is carried by its target output | `DynamicComponent.connect_with_dynamic_connections_list` | build | E | L | `components.py` |
| `components.channel_declared` | an aggregator asks only for a channel it declares | `DynamicComponent.get_channel` | build / run | E | L | stays (an internal contract) |
| `components.cost_relevance_declared` | with `COMPUTE_LIFECYCLE_COSTS` or `LIFECYCLE_COST_REPORT`: no class with `cost_relevance` `UNDECLARED`; a `PRICED` class has `get_cost_facts` or an adapter entry | `hisim/simulator.py:Simulator.check_cost_declarations` | build | E | L | `components.py` |
| `components.source_output_registered` | every connected input's source output belongs to an added component | `Simulator.evaluation_plan` | first plan | E | L | `components.py` |
| `components.kind_in_evaluation_order` | every kind is in `EVALUATION_ORDER_OF_KINDS` | `Simulator.evaluation_positions` | first plan | E | L | `components.py` |
| `components.no_physics_cycle` | the L1 device-before-store constraints form no cycle | `hisim/evaluation_order.py:PhysicsOrder.ordered` | first plan | L | D | `components.py` |
| `components.no_l2_cycle` | L2 controllers read one another in no cycle | `L2Order.ordered` | first plan | L | D | `components.py` |
| `components.not_empty` | the run has at least one component and one column | `Simulator.run_all_timesteps`; `process_one_timestep` | build / step | E | L | `components.py` |
| `energy_ports.loop_pairing` | an end's other end declares the loop; a loop has exactly one supplier | `eb/ports.py:Wiring.pairing_fault`, `Wiring.loops` | build; again after the run | E | L | `energy_ports.py` |
| `energy_ports.one_reader_per_flow` | a loop's mass flow is read by one loop-declaring component | `eb/ports.py:Wiring.loops` | build; after the run | E | L | `energy_ports.py` |
| `energy_ports.no_loop_on_a_node` | an exempt component or a meter declares no loop end | `eb/ports.py:Wiring.loops` | build; after the run | E | L | `energy_ports.py` |
| `energy_ports.receiver_unambiguous` | an output port read through paired inputs by one component; a non-metered `PRODUCTION` port read by one declared component unless an input is tagged | `eb/ports.py:Wiring.ambiguous_receiver_faults` | build | E | L | `energy_ports.py` |
| `energy_ports.input_port_pairs` | an input port pairs with the output port it reads (same carrier, opposite role) | `eb/ports.py:Wiring.unpaired_input_port_faults` | build | E | L | `energy_ports.py` |
| `energy_ports.room_heat_needs_receiver` | a `PRODUCTION` port of `ROOM_HEAT` is read by a component | PR #1211 (`Wiring.refuse_a_broken_wiring`, fourth fault list) | build | E | **D**, waived for `demo` and `plant` | `energy_ports.py` |
| `energy_ports.loop_tagged_whole` | a port set tags all four loop quantities | `hisim/component.py:FluidLoopPortSet.group` | build | E | L | `fluid_loops.py` |
| `fluid_loops.far_end_tagged` | a tagged flow is read by, or fed from, a tagged channel | `hisim/fluid_loops.py:FluidLoopWalk.untagged_far_end_faults` | build | E | L | `fluid_loops.py` |
| `fluid_loops.flow_read_once` | one flow out is read by one flow in (a split needs a valve) | `FluidLoopWalk.connection_fault` | build | E | L | `fluid_loops.py` |
| `fluid_loops.closes` | a wired port set's flow out is read and its flow in fed | same | build | E | L | `fluid_loops.py` |
| `fluid_loops.wired_whole` | flows and temperatures are connected together, or neither | same | build | E | L | `fluid_loops.py` |
| `fluid_loops.temperature_follows_flow` | a member's temperature in reads the member whose flow it reads | `FluidLoopWalk.loop_faults` | build | E | L | `fluid_loops.py` |
| `fluid_loops.one_carrier_one_fluid` | all members state one carrier and one fluid | same | build | E | L | `fluid_loops.py` |
| `fluid_loops.one_pump_owner` | exactly one member pumps | same | build | E | L | `fluid_loops.py` |

The constructor-time port rules (`EnergyPort.__post_init__`: no grid environment, no environment on a metered
port; `EnergyPort.validate_unit`: a power, an energy or kg/s for a fuel; `check_fits_an_input`;
`MeteredExchange.__post_init__`; `FluidLoop.__post_init__`: the carrier is one of three loop carriers, the fluid a
`LoopFluid`; `FluidLoopChannel.check_fits`; `Component.refuse_a_repeated_fluid_loop_tag`) are declarations of a
component class, checked when the class declares them; they are laws of a class's declaration and stay (D10). `FluidLoop.LOOP_CARRIERS` (space
heating, domestic hot water, ambient heat) is the one of them that encodes a house: a process-heat loop is refused
by the declaration; it is noted for the commercial use, not scoped.

### 6.6 After the run (stage AFTER_RUN) and the write guard

| Rule | Checks | Lives today | When | E/L | Class | Target |
|---|---|---|---|---|---|---|
| `results.finite` | no result column holds NaN or inf unless its output declares `may_be_non_finite`; one column per output | `hisim/postprocessing/finite_results.py:FiniteResultsCheck.require_finite` | after the run, first | E | L | `results.py` |
| `results.all_rows` | the table has one row per timestep | `Simulator.prepare_post_processing` | after the run | E | L | `results.py` |
| `results.port_finite` | a declared port is finite in every step | `eb/check.py:BalanceCheck.nonfinite` | after the run | E | L | `results.py` (one rule with `results.finite`) |
| `results.component_balance_closes` | per component, consumption − production − loss − stored change ≤ 0.1 % of throughput over the run and per step (`Tolerance`) | `eb/check.py:BalanceCheck.balance`, `run` | after the run | E | L | `results.py` |
| `results.link_closes` | every `PRODUCTION`→`CONSUMPTION` transfer between declared components is booked by both sides within 0.1 % | `eb/check.py:BalanceCheck.links`; `eb/ports.py:DeclaredPorts.declared_links` | after the run | E | L | `results.py` |
| `results.loop_faults` | the loop rules of §6.5 on the run's columns, plus: an open end pumps no water | `eb/ports.py:DeclaredPorts.loop_faults`, `add_loops` | after the run | E | L | `results.py` |
| `results.loop_columns` | a loop's mass flow is never negative, the timestep positive; the columns a port, loop or exchange names exist | `eb/ports.py:LoopColumns.heat_in_kilowatt_hours_per_step`, `DeclaredPorts.add_input_ports`, `read_meters` | after the run | E | L | `results.py` |
| `results.no_port_on_a_node` | an exempt component or a meter declares no port | `eb/ports.py:DeclaredPorts.refuse_a_port_of_a_node_that_moves_nothing` | after the run | E | L | `results.py` |
| `meters.meter_balance` | per meter and carrier, import − export equals the ports it reads, within tolerance; enforced only while nothing is undeclared (Q-H) | `eb/meters.py:MeterBalance.of`, `MeterCheck.failing` | after the run | E (conditional) | L | `meters.py` |
| `meters.metered_port_has_a_meter` | a metered port's peer is a meter or a component; enforced only while nothing is undeclared | `eb/ports.py:DeclaredPorts.unmetered`; `eb/meters.py:MeterCheck.failing_unmetered` | after the run | E (conditional) | **D**, waived for `demo` | `meters.py` |
| `results.port_linked` | a non-idle port has a peer | `eb/ports.py:DeclaredPorts.unlinked` | after the run | L (the acceptance PR makes it E) | D, waived for `demo` | `results.py` |
| `results.component_declared` | a component declares ports or is exempt with a reason | `eb/ports.py:DeclaredPorts.collect` | after the run | L (CI makes it E, §6.9) | L | `results.py` |
| `costs.lifecycle_engine` | the cost engine finished; `CostDataError` always propagates, other errors fail only with `require_lifecycle_costs` | `hisim/postprocessing/postprocessing_main.py:PostProcessor.compute_lifecycle_costs` | post-processing | E / L | L | stays (the engine's own policy) |
| write guard | no write outside the calculation's directories; one result directory per calculation; not under `hisim/inputs` | `hisim/write_guard.py:WriteGuard._hook`, `CalculationDirectories` | every write | E | L | stays (not a rule on a system) |
| simulation window | the step divides a day, the span is whole days in one year, the start is midnight; `weather_year`, `part_load_above_seconds`, `cache_directories` valid | `hisim/simulationparameters.py:SimulationParameters.validated_window` and siblings | load | E | L | stays (parameters, not the system) |
| convergence | a step converges within max(100, 100·s/60) passes; after 10 it is forced | `Simulator.process_one_timestep` | per step | E | L | stays (the loop) |
| post-processing options | report writers need `GENERATE_PDF_REPORT`; KPI JSON needs `COMPUTE_KPIS`; network charts need `MAKE_NETWORK_CHARTS` and `dot` | `PostProcessor.run` and siblings | post-processing | E | L | stays |
| KPI consistency | self-sufficiency, relative demand, self-consumption ≤ 100 %; batteries publish their stored-energy increase; KPI entries have a source, a tag, a unique address | `hisim/postprocessing/kpi_computation/kpi_preparation.py`, `kpi_structure.py`, `kpi_address.py` | post-processing | E | L (the HTW Berlin definitions are a prosumer household's, noted) | stays |
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
device's model is its own (D10). They are listed because they are what the kind must know about, and because the
waivers of §4.2 are written against them.

| Rule | What | Lives today | When | Class | Note |
|---|---|---|---|---|---|
| a heat distribution system needs a building | mandatory inputs `TheoreticalThermalBuildingDemand`, `ResidenceTemperatureIndoorAir` only a `Building` feeds | `cmp/heat_distribution_system.py:HeatDistribution.__init__`, `HeatDistributionController.__init__` | build (EF-31) | B | right for any heated building |
| room-temperature controllers need a building | mandatory indoor temperature: `AirConditionerController`, `SimpleAirConditioner`, `L1CHPController`, `IdealizedElectricHeaterController` | `cmp/air_conditioner.py`, `simple_air_conditioner.py`, `generic_chp/controller.py`, `idealized_electric_heater.py` | build (EF-31) | B | same |
| heating-season input | boiler, district heating, electric heating, heat-pump space heating and HDS controllers read `HeatingSeason` from the heating curve, whose laws read the building's load per m² | `cmp/generic_boiler.py`, `generic_district_heating.py`, `generic_electric_heating.py`, `more_advanced_heat_pump_hplib.py`, `heating_curve.py:HEATING_THRESHOLD_LAW` | build | B | a process-heat boiler has no season; a commercial building has one |
| a building without residents warns | `HeatingByResidents` and `HeatingByDevices` optional; 0 W when unconnected | `cmp/building/building.py:Building.warn_about_unconnected_internal_heat_gains` | first step | R | D11 adds residents to the one setup that lacks them |
| TABULA windows, scaling, apartments | a row with no reference window area is refused; floor area and base area exclusive; apartments ≥ 0 (else floor area / 92.1 m²) | `cmp/building/information.py` | build | R | TABULA is residential |
| the design heating load | (H_tr + H_ve)·(T_in − T_ref), DIN EN 12831 steady state | `cmp/building/information.py:get_max_thermal_building_demand` | build (sizing) | B | the same law for both uses |
| the building provides the facts | heating load, apartments, floor area, roof area, set points; a consumer without a building fails EF-4A | `cmp/building/config.py:SIZING_CONTRIBUTIONS` | build | B (apartments: R) | |
| per-apartment sizing laws | DHW tank 250 l per apartment; collector 4 m² per apartment; boiler floor 2,500 W per apartment | `cmp/simple_water_storage.py:SimpleDHWStorageConfig.VOLUME_LAW`; `solar_thermal_system.py`; `generic_boiler.py` | build | R | commercial laws per use |
| rooftop PV needs a roof area; a buffer needs the generator's power | `cmp/generic_pv_system/config.py:_rooftop_power_in_watt`; `simple_water_storage.py:_buffer_volume_in_liter` | build | B / L | |
| domestic hot water | tap 45 °C, fresh water 10 °C, 2,000 kWh/a per household | `cmp/configuration.py:HouseholdWarmWaterDemandConfig` | per step | R | per use for commercial |
| heating-curve design day | the reference temperature is below the set point; the load fraction ≥ 0 | `cmp/heating_curve.py:HeatingCurveController.__init__` | build / step | B | |
| comfort bands | heating set < cooling set, finite, positive hysteresis | `cmp/air_conditioner.py:AirConditionerController.__init__` | build | B | the band's values by use |
| cooling through the emitter needs floor heating; HDS cooling needs `cooling_enabled` | `cmp/more_advanced_heat_pump_hplib.py`; `heat_distribution_system.py` | build / step | B | |
| a household occupancy | one household per connector (more through the LPG); a predefined profile exists; a car's profile is published by exactly one occupancy; a car runs on electricity or diesel | `cmp/loadprofilegenerator_utsp_connector.py`; `lpg_car_information.py`; `generic_car.py:CarConfig.for_household` | build | R | |
| substation DHW share | in parallel mode the DHW load is below the connected load | `cmp/generic_district_heating.py`; `generic_electric_heating.py` | per step | B | |
| the storage's converged temperature | the mean water temperature in [0, 90] °C after convergence | `cmp/simple_water_storage.py:SimpleWaterStorage.check_converged_mean_water_temperature` (the one non-empty `i_doublecheck`) | per step | K | a domestic tank's cap; a process tank would raise it |
| the loop carriers | `SPACE_HEATING_HEAT`, `DOMESTIC_HOT_WATER_HEAT`, `AMBIENT_HEAT` | `hisim/energy_port.py:FluidLoop.LOOP_CARRIERS` | declaration | K | a process-heat loop needs a carrier |

The remaining component refusals are physics guards (K, stay): the heat pump's SCOP range and W55 ≤
W35, power ≥ 1 W, COP > 0 when running; the boiler's efficiency range and control signal in [0, 1]; the
substations' lift ≥ 0; the battery's finite non-negative parameters and SOC ≤ 1; the CHP, fuel cell, electrolyzer
and hydrogen storage bounds; the transformer's efficiency in (0, 1]; the solar-thermal pump's flows; the PV share
and database lookups; the weather files, columns and ranges; the CSV loader's rows, columns and time labels; the
LPG and car profile resolution; the meters' carriers; the `NaN` fall-through branches of unknown modes
(`hisim/components/*`, §3 of the inventory's source notes).

### 6.9 CI gates and harness contracts

| Gate | Enforces | Lives today | Scope it gets |
|---|---|---|---|
| golden check | every (setup, parameters) pair reproduces its KPI leaves in Python, twin and composed mode | `scripts/golden_check.py`, `golden_kpis.py`, `golden_config.json`; `.github/workflows/golden-check.yml`, `golden-year.yml`; `tests/test_golden_matrix.py` (every setup is in the config) | every kind (D8) |
| twin freshness | every setup re-records byte-identically; no skip list; no two parameter files describe one run | `scripts/record_all_setups.py --check`; `.github/workflows/energy-system-freshness.yml`; `tests/test_energy_system_recording_fleet.py` | every kind; checks the `setup_kind` key |
| parity | a Python setup equals its twin, wiring and results | `scripts/p3_parity_check.py`; `tests/test_p3_parity.py` | every kind |
| schema | the committed schema equals a fresh export; every twin validates | `tests/test_energy_system_schema.py` | every kind |
| port coverage | every component class declares ports or is exempt with a reason stated on its own body; an exempt class declares nothing | `tests/test_energy_port_coverage.py`, `tests/component_sweep.py` (sizes every preset against `ComponentSweep.REFERENCE_HOUSE`, a single-family archetype) | L; the reference house is R-shaped (a commercial sweep when a commercial archetype exists) |
| flag agreement | an electricity postprocessing flag agrees with its energy port; the one-sided list only shrinks | `tests/test_energy_port_flag_agreement.py` | L |
| kind base | every component class has one kind; the evaluation order | `tests/test_component_kinds.py` | L |
| cost declarations | every class declares `cost_relevance`; adapter keys resolve; `AUTO_SUBSTITUTES` are the single-family archetype | `tests/test_economics_adapter_contract.py`, `test_economics_declaration_gate.py`; `tests/renovisor/test_twin_cost_declarations.py` | L; substitutes R-shaped |
| no per-step finite checks | no `isfinite` in hot paths | `tests/test_no_per_step_finite_checks.py` | L |
| explicit-state controllers | no hidden state; the committed state reproduces the outputs | `tests/test_explicit_state_controllers.py` | L |
| fluid-loop fleet | every recorded file builds and gets its loop ids | `tests/test_fluid_loops.py` | L |
| DHW and buffer twins | every file with a tank closes it; both loop ends agree; the forced-step share is bounded | `tests/test_dhw_chain_twins.py`, `test_space_heating_buffer_twins.py`, `test_part_load_twins.py` | B (files with a tank) |
| sizing scalability | components scale ×5 with the floor area | `tests/test_sizing_energy_systems.py` | R |
| assembly library | EF-75 on every library file | `tests/assemblies/test_assembly_library.py` | L |
| assembly contracts: run, balance, finite, bounds, expect, monotone, member contract | per sample | `tests/assemblies/test_library_contracts.py`; `es/assemblies/testing/checks.py` | L |
| assembly contract: heat delivered | no `HEATING` input left unconnected when a default connection serves it | `checks.py:HeatDelivery.lost`, `check_heat_delivered` | B |
| assembly contract: room heated | every heated building's day-mean air within 1 K (`Building.UNDERHEATING_TOLERANCE_IN_KELVIN`) of its set point | `checks.py:check_room_heated` | B (a heated building) |
| isolation harness | every carrier moved through a metered port gets a meter partner; a port with no partner refuses the build | `es/assemblies/testing/isolation.py`, `partners.py` | the isolation system is `demo` |
| composed-twin gates | every composed file equals its twin outside the neutral swaps | `tests/assemblies/test_twin_gates.py`, `test_composed_twins.py` | every kind |
| layering lints | nothing outside `hisim/renovisor` imports it; subcomponents import only the kernel; a component imports no file format | `tests/test_renovisor_import_lint.py`, `test_subcomponent_import_lint.py`, `test_energy_system_layering.py` | L; the rules package joins the layering test (imports no component) |
| semgrep | no file write outside the allowlist; no `print` | `.semgrep/file_writes.yml`, `no_print.yml`; `quality.yml` | L |
| stray files | a test dirties nothing outside its result directory | `tests/conftest.py:guard_against_stray_files` | L |
| RenoVisor path verification | every probe path translates and diffs against its base | `python -m hisim.renovisor verify`; `.github/workflows/path-verification.yml` | R (RenoVisor) |

### 6.10 Out of scope, noted: RenoVisor

`hisim/renovisor/request.py` checks a calculation request in two steps: the vendored JSON Schema, then the
semantics (every measure id in the 32-entry catalogue, every option of its measure, an `everyone` option, a TABULA
typology for the country, a TABULA row, no conflicting hot-water volumes), all problems at once as a
`RequestError` (exit 2). `whitelist.py` and `not_implemented_yet.yaml` list what the translator may skip with a
note; anything else unmapped fails (exit 3). `translate.py` picks a recorded grouped twin by renovated generator
and may only write config values, named-constructor arguments and a variant selection; `run.py` requires the
lifecycle costs. All of it is a house and a household: RenoVisor is residential, every file it writes is `residential`,
and the translator writes `setup_kind: residential` into every file it builds.

### 6.11 Counts

Rows of the tables above (one row is one rule; the §6.4 library check is one row for its 45 messages):

| Family | Rows | Stay (P, K, parameters, engine) | Moved into the package | Of the moved: log-only |
|---|---|---|---|---|
| §6.1 document | 27 | 5 | 22 | – |
| §6.2 bindings and sizing | 21 | – | 21 | 1 |
| §6.3 wiring | 19 | – | 19 | 2 |
| §6.4 assemblies | 22 | 1 | 21 | – |
| §6.5 built system, energy ports, fluid loops | 35 | 2 | 33 | 2 |
| §6.6 after the run, meters, the rest | 21 | 9 | 12 | 2 |
| §6.7 recorder | 14 | 14 | – | – |
| §6.8 component rules that encode a house | 17 | 17 (R 5, B 10, K 2) | – | – |
| §6.9 CI gates and contracts | 23 | 23 | – | – |
| Total | 199 | 71 | 128 | 7 |

The residential assumptions: eight rules hold only for a household (the five R rows of §6.8, EF-R2, the cost
engine's household framing with `write_kpis_in_dict`'s `BUI1`, and `ComponentSweep.REFERENCE_HOUSE` with
`AUTO_SUBSTITUTES`), thirteen hold for any heated building (the ten B rows of §6.8, `check_room_heated`,
`check_heat_delivered`, the building-sizer KPI JSON), and two are device limits shaped by a house (the tank's
90 °C, the loop carriers). None of them moves (D10) and none is waived on day one: a plant has no building for the
B rows to fire on, a demo with a building is checked like a house, and a commercial setup runs a TABULA house (§4.3).

Of the rules that are moved, two are waived on day one (`energy_ports.room_heat_needs_receiver` for `demo` and
`plant`, `meters.metered_port_has_a_meter` for `demo`), four are log-only domain rules (`wiring.unconsumed_source`,
`wiring.unmetered_output_of_a_need`, `sizing.unconsumed_fact`, `results.port_linked`), three are log-only laws
(`components.no_physics_cycle`, `components.no_l2_cycle`, `results.component_declared`), and one encodes one site
(`assemblies.one_provider_per_carrier`); every other moved rule is a law of the model or a consistency of the code,
and applies to every kind until a waiver with a reason says otherwise.

## 7. Which setup gets which kind

| Setup or file | Building | Occupancy | Kind | Consequence |
|---|---|---|---|---|
| the eleven `household_*_building_sizer` setups, their twins, grouped and composed files; `household_gas_solar_thermal`; `basic_household`, `basic_household_only_heating`; `air_conditioned_house`; `automatic_default_connections`, `default_connections`; `gas_boiler_household` (hand-authored); `uc1.realized` | one | yes | `residential` | nothing changes; every rule applies, as today |
| `simple_air_conditioner_household_building_sizer` (+ grouped, probes) | one | **none** today | `residential` | D11: gets residents (its results move, blessed in that PR); no rule refuses it either way (D6) |
| `dynamic_components` | none | yes | `demo` | the room-heat rule is waived; the residents' two `ROOM_HEAT` outputs stay unlinked (reported, as today); its five goldens stay (D8) |
| `electrolyzer_with_renewables` | none | none | `plant` | no building rule has a subject; the room-heat rule is waived (no located device today); nothing changes |
| `simple_system_setup_one`, `_two` | none | none | `demo` | random numbers and sums; every other rule still applies |
| `tests/energy_systems/two_pv_arrays` (+ composed) | none | yes | `demo` | the room-heat rule is waived |
| the dispatch test and the assembly-crossing test of #1211's list | none | yes | `demo`, or a building where the test is about the house | decided per test in the PR |
| the isolation systems of the assembly harness | the `Building` partner when a member needs its facts | the `UTSPConnector` partner when a member observes it | `demo` by construction | the harness contracts (`check_room_heated`, `check_heat_delivered`) keep running: they are the rig's own checks, not run-time rules |
| RenoVisor's output file | one | yes | `residential` | inherited from the base twin; the translator never writes the key |

No setup is `commercial` today.

## 8. Implementation plan

Small stacked PRs; each keeps every result and every message text unchanged unless it says otherwise (the
`see:` pointer of D13 is appended, and a stage may list several faults, D14); each moves one family and its tests,
allocates the family's codes (§4.1), writes each rule's docstring to the template, turns the family's
first-fault raises into appended faults where §4.1 lists them, fills the `Fault` fields its subject allows, and
adds the direct tests of §9 for the rules it moves. PR 1 is the mechanism; PR 2
carries #1211; the families follow in the order of their coupling; PR 10 is the one result-moving PR (D11).

| PR | Content | Moves | Results |
|---|---|---|---|
| 1 | `hisim/setup_rules/`: `SetupRule` (`CODE`, `NAME`, `STAGE`, `ENFORCED`, `REQUIRES`, `WAIVED_FOR`, the docstring template, the `__init_subclass__` checks of D3, D13), `Fault` with the fields of D15, `Skip`, `RuleStage`, `SetupRuleViolation` (every fault and skip per stage, `to_dict()`), `SetupRuleRegistry.collect` (the `REQUIRES` graph checked), `run` (code order, skips), `RETIRED_CODES` (empty), the `hisim setup-rules` commands `list`, `waivers`, `show`, `check --format json`, `Inventory.RECORDS` (every rule id of §6 with its family, stage, nature and where it lives today), the subjects; `SetupKind`, the `setup_kind` key (model, reader, emitter, schema, executor), `Simulator.declare_setup_kind`, the registry's refusal of an undeclared kind, the recorder, every setup's declaration, every twin re-recorded, the fixtures and the harness declaring; the generic tests of §9; the four hooks calling an (empty) registry | nothing | none |
| 2 | `energy_ports.py`: the three fault lists of `Wiring.refuse_a_broken_wiring` and `energy_ports.room_heat_needs_receiver` with its `demo` and `plant` waivers; #1211 rebased onto PR 1 and reduced to the device side | `eb/ports.py:Wiring.*_faults` | none (the rule refuses nothing that runs today once the demos and the plant are declared) |
| 3 | `fluid_loops.py`: the refusals of `FluidLoopWalk.find_loops`; the walk keeps discovering loop ids | `hisim/fluid_loops.py` (part) | none |
| 4 | `results.py`, `meters.py`: the finite check, the balance, the links, the loop faults, the meter balance, the unmetered ports (Q-H kept; the `demo` waiver of `meters.metered_port_has_a_meter`), the log-only `port_linked` and `component_declared` | `hisim/postprocessing/finite_results.py`; `eb/check.py`, `eb/meters.py` (the judging halves) | none |
| 5 | `components.py`: the simulator's build rules, `check_cost_declarations`, the evaluation-order cycles | `hisim/simulator.py`, `component_wrapper.py`, `dynamic_component.py` (the checks) | none |
| 6 | `wiring.py`: the declarative wiring rules | `es/wiring_checks.py` whole; the checks of `es/aggregator_ports.py`, `channel_matching.py`, `dispatch_signals.py`, `feed_resolution.py` | none |
| 7 | `document.py`: the structural rules | `es/validation.py` whole; the checks of `names.py`, `entries.py`, `loader.py` | none |
| 8 | `bindings.py`, `sizing.py`: the class, preset, config and sizing-classification rules | `es/bindings.py` (checks), `classes.py` (checks), `sizing_bridge.py:KernelFailure` | none |
| 9 | `assemblies.py`: the import, binding and library rules | `es/assemblies/binding.py` (checks), `library.py:LibraryChecker`, `parameters.py` (checks), `selection.py` (checks) | none |
| 10 | D11: `simple_air_conditioner_household_building_sizer` gets its occupancy (`UtspLpgConnector`, the building reads its heat gains and its hot-water draw where the setup has a tank); the twin, grouped and probe files re-recorded; the goldens measured and blessed | – | that setup's goldens move |

After PR 9, `Inventory.RECORDS` names no "lives today" outside the package for any rule of nature L, D, B or R that
is a rule of the system; the records of the P, K and recorder rows stay as records with their place, so the
inventory stays the one list.

## 9. Verification

Generic tests, in `tests/rules/`, each one test for the whole package:

- `test_registry.py`: every `SetupRule` subclass of the package declares a `CODE` of its family's prefix, a
  kebab-case `NAME`, a `STAGE` and `ENFORCED`; no two rules share a code or a name; no live code is in
  `RETIRED_CODES`; every `REQUIRES` entry names a live code of the same or an earlier stage and the graph has no
  cycle; the registry collects exactly the classes the inventory records as "in the package"; a stage runs in code
  order; the registry refuses a subject without a kind with the message of §4.4.
- `test_docstrings.py`: every rule's docstring carries the four headings of D13 in order, each followed by text.
- `test_aggregation.py`: two rules of one stage that both find faults give one `SetupRuleViolation` with both
  faults in code order; a rule whose `REQUIRES` names a rule that found faults is listed as skipped with that code
  and is not run; a rule with faults that is not enforced logs and does not raise; every refusal message ends with
  `see: hisim setup-rules show <code>`.
- `test_fault.py`: `Fault` is frozen; `SetupRuleViolation.to_dict()` holds the fields of D15 and round-trips through
  `json`; a document rule fills `key_path` and `file`, a build rule `component` and `port`.
- `test_rule_base.py`: the direct tests of `SetupRule.__init_subclass__`: a subclass whose `WAIVED_FOR` has a key that is
  not a `SetupKind`, or a reason that is empty or blank, fails at class creation with a `TypeError` naming the
  rule; a subclass without `WAIVED_FOR` inherits the empty read-only mapping and cannot mutate it; a well-formed
  mapping is kept as declared. Nothing else is checked about a waiver (D2).
- `test_overview.py`: `hisim setup-rules list` and `hisim setup-rules waivers` print every rule of the registry once
  with its code, name, stage, nature, enforcement, requirements and every `WAIVED_FOR` entry with its reason, in
  code order; `hisim setup-rules show <code>` and `show <name>` print the same rule's docstring and declarations;
  `hisim setup-rules check` on a fixture with two faults prints both as text and as the JSON of D15 and exits
  non-zero; the output is compared with what the registry holds, not with a committed page (§4.2).
- `test_inventory.py`: every rule id of the registry is in the inventory and vice versa; every record names a
  nature, a stage and a place; the records of this spec's §6 are the file's (the spec cites the file as its source
  after PR 1).
- `test_direct_tests.py`: every rule id appears in a test under `tests/rules/test_<module>.py` that constructs a
  subject with the fault and one without (the extensive-docstring rule's "every static function has direct
  tests", here per rule).
- `test_declarations.py`: every `system_setups/*.py` declares its kind (a source scan for `declare_setup_kind`
  and a build of each setup); every `energy_systems/*.energy_system.yaml`, every composed file and every fixture
  under `tests/energy_systems/` carries `setup_kind`.
- The existing tests prove each migration: a PR that moves a family changes no message, so
  `tests/test_energy_system_*.py`, `tests/test_energy_port_*.py`, `tests/test_fluid_loops.py`,
  `tests/test_finite_results.py`, `tests/test_component_kinds.py` and the assembly tests pass unchanged; the
  freshness gate, parity and the goldens stay green (the one re-recording in PR 1 is the `setup_kind` key).
- The room-heat rule: `tests/rules/test_energy_ports.py` builds a system with an unread `ROOM_HEAT` output and
  sees the fault for `residential` and `commercial`, no fault for `demo` and `plant`, and a fault again for `demo`
  with the rule's `WAIVED_FOR` patched empty (the waiver, not the rule, is what the demo relies on).
- D11: the PR of bead .10 measures the KPIs of `simple_air_conditioner_household_building_sizer` before and after
  the residents (the five golden windows) and writes the table into its description; the golden update runs through
  `golden-update.yml` on current main.
- The `base` budget (`system_docs/testing.md` §3): the generic tests import the package and scan files, no
  simulation; the per-rule tests build small subjects, no full run.

## 10. Rejected alternatives

The draft's recommendations the owner decided against (2026-11-06), with the reason each lost, then the
alternatives both rejected.

- **Two classes of rule, with the laws of the model unwaivable** (the draft's Q1; D2 decided against). The draft
  argued a waiver on the energy balance would let a demo publish numbers that break the model. The owner chose one
  mechanism for every rule: the table is then the whole truth about what a kind does not run, and a reviewer reads
  the nature column before accepting a waiver on a law.
- **Two axes, a purpose per setup and a use per building** (the draft's Q3; D4 decided against). The draft argued a
  district mixes uses and a plant needs no kind once building rules scope by building. The owner chose one word per
  setup: simpler to declare, to read and to test; the mixed district is a later extension (bead .13).
- **The file key `purpose` and the values `case` and `demo`** (the draft's Q4; D5 decided against). The owner's four
  kinds name what the setup is; `setup_kind` says what the key holds and avoids the assembly files' `kind`.
- **A kind is a contract the run checks** (the draft's Q5; D6 decided against). The draft proposed rules that a
  residential setup has residents and a heater, that `commercial` is refused until a source exists, that a demo is
  never a RenoVisor base or a test-partner source. The owner chose a switch: the declaration is trusted, and the
  one setup the contract would have caught is fixed by hand (D11).
- **Per-rule kind lists (the owner's first form).** A new kind starts with no checks, and a rule that forgets a
  kind runs nowhere for it, both silently; with apply-unless-waived a forgotten waiver refuses, which is noticed (D3).
- **A central waiver table** (`Waivers.TABLE` in `hisim/setup_rules/waivers.py`, the draft's form; the owner's amendment of
  D3, 2026-11-06). A table that names rules duplicates them and drifts from them, and everything about a rule
  lives in its own module (D1); the waivers are on the rules, and the overview is generated (§4.2).
- **A committed overview page of the waivers, kept in step by a test.** It would be the central table under another
  name; the generated `hisim setup-rules` output is the overview, and the test checks the generator against the registry.
- **Per-setup waivers.** A setup that does not fit its kind declares another kind or is fixed; a switch per file is
  the state the owner wants to leave.
- **Ruff-style rules: a function with a decorator** (D12). Five to eight lines of frame instead of fifteen to
  twenty, but no place for a rule's helpers but module functions, and the declarations can be checked only at
  registration, not when the class is created; the owner prefers the class.
- **Per-rule example pairs enforced by a generic test** (ruff's fixture style: for every rule a file that fails
  and a file that passes, run by one test). Left out by the owner: at 500 to 7,500 lines of fixtures it costs more
  than it finds here; the direct tests per rule (§9) build their subjects in code.
- **Per-rule options** (a threshold or a list a user sets per rule), **inline per-file disables** (`noqa`
  comments in an energy-system file), **severity ladders** beyond refuse or log, **plugin entry points** for
  rules outside the package, **autofix**, and **layered configuration files** (a project file overriding a user
  file overriding the defaults). Each is a linter's answer to many projects with many tastes; HiSim has one
  package, one owner and four kinds, and every one of them would be a switch the waivers were designed to
  replace. Not adopted.
- **A dead-waiver report** (a rule whose waiver names a kind no setup declares, or that would never fire for that
  kind). Not adopted now; it could be revisited once the registry and the setups' declarations exist, as a line
  of `hisim setup-rules waivers`.
- **A kind in `SimulationParameters`.** The parameter files are a shared library, any system runs from any of
  them; a kind is a property of the system (D7).
- **A default kind.** A demo would run as a house by omission, the silent direction D3 avoids (D7).
- **A kind read from the components (a building present ⇒ a house).** The kind is then a guess the author never
  states, and a demo with a building would be a house.
- **Keeping the rules where they are and adding a `kinds` attribute to each.** The hooks would stay four, the
  enforcement decisions scattered, nothing would list the rules; D1 rejects it.
- **Moving the per-step physics guards and the constructor-time declarations into the package.** They are a
  device's model or a class's declaration, not a rule of a system; the package would import every component (D10).
- **Moving the reader's parse faults into the package.** The reader would call the registry on a half-read
  document (D9).
- **A `metadata:` block for the kind.** EF-09 reserves `metadata` for generated run records; a plain run refuses it.
- **A demo never blessed into the goldens.** `tests/test_golden_matrix.py` requires every setup in the golden
  config, and a golden is a regression gate, not a correctness claim (D8).
- **Declaring `simple_air_conditioner_household_building_sizer` a `demo`, or an `unoccupied` flag on the
  building.** The setup is called a household; it gets residents (D11).

## 11. Open questions

None. Every question of the draft of 2026-11-06 is decided (D2 to D11), and the comparison with the linters is
decided (D12 to D15).

## 12. Beads

Epic hisim-setup-kinds-dd45 (children .1 to .14 as below; device-location bead hisim-device-location-820d.1 is blocked by .2), P2, `core`, "every setup declares its kind,
every validation rule lives in `hisim/setup_rules/`, and a rule a kind does not need is waived by name with a reason". The
PR numbers of §8.

| Bead | Content | PR |
|---|---|---|
| .1 (P2) | the mechanism: the package, the registry, `SetupRule` with its code, name, template docstring, `REQUIRES` and `WAIVED_FOR`, the `Fault` fields and the JSON of the violation, every fault per stage with skips, the generated `hisim setup-rules` overview and `check`, the inventory as data, the subjects, the hooks; `SetupKind`, `setup_kind` and `declare_setup_kind` declared everywhere; the twins re-recorded; the generic tests | PR 1 |
| .2 (P2) | the energy-port family, with `room_heat_needs_receiver` and its `demo` and `plant` waivers; #1211 rebased onto it | PR 2 |
| .3 (P3) | the fluid-loop family | PR 3 |
| .4 (P3) | the after-run and meter families, with the `demo` waiver of `meters.metered_port_has_a_meter` | PR 4 |
| .5 (P3) | the simulator's build rules and the cost declarations | PR 5 |
| .6 (P3) | the declarative wiring family | PR 6 |
| .7 (P3) | the document family | PR 7 |
| .8 (P3) | the bindings and sizing families | PR 8 |
| .9 (P3) | the assembly family | PR 9 |
| .10 (P2) | D11: residents for `simple_air_conditioner_household_building_sizer`; results measured and blessed | PR 10 |
| .11 (P4, `external`) | the heads-up on renovisorissues: every file the translator writes carries `setup_kind: residential` | – |
| .12 (P4) | a commercial source: the archetype, the usage profiles by zone type, the per-use hot water, the comfort band; then the waivers and rules that differ for `commercial` (§4.3) | – |
| .13 (P4) | a district twin: EF-R2, `write_kpis_in_dict`'s `BUI1`, `assemblies.one_provider_per_carrier`; a per-building scope for a district with mixed uses (D4's known limitation) | – |
| .14 (P4) | `system_docs/`: the rule of D1 ("a validation rule lives in `hisim/setup_rules/`, declares its stage, and is waived only by the table") written into `coding_style.md` or a new guideline once PR 1 is merged | – |
