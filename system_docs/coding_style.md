# Coding style

How HiSim code is designed and written, and how its docstrings, commit messages and pull request descriptions are written. It applies to every
change, by people and by agents alike. Reviews check it. The rules on what a component may do are in
`component_design_principles.md`, and the rules on result files and caches are in AGENTS.md.

## 1. Design: testable, clean and simple

- **Business logic goes into pure functions.** A calculation -- a physical law, a control rule, a cost formula, a
  unit conversion -- lives in a function whose result depends only on its arguments: a `@staticmethod` on the
  class it belongs to, or a function in a library module such as `hisim/hydronics.py`. It reads no `self` state,
  no `stsv`, no file and no `SimRepository`, and it changes nothing. A component's `i_simulate` only gathers its
  inputs and state, calls these functions and publishes their results. Each such function is tested in a unit
  test, edge cases and invalid input included, without building a simulation.

  ```python
  def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
      """Read the inflows, step the node and publish its temperatures."""
      inflows = self.inflows_from_inputs(stsv)
      end_temperature_in_celsius = self.mixed_node_end_temperature_in_celsius(
          self.state.temperature_in_celsius, inflows, self.my_simulation_parameters.seconds_per_timestep
      )
      stsv.set_output_value(self.end_temperature_channel, end_temperature_in_celsius)
  ```

- **Clean Code.** A function does one thing, at one level of abstraction, and is short enough to read at once.
  Names reveal intent. No flag argument that switches between two behaviours: write two functions. A function
  whose name says it returns something does not also change state. Early returns instead of deep nesting.
- **SOLID**, as far as it is reasonable:
  - *Single responsibility*: a class has one reason to change. A component simulates one device; its config
    holds and checks its parameters; KPI and cost logic sit apart from the physics.
  - *Open/closed*: new behaviour is added (a preset, a component, an Enum member with its entry in a mapping),
    not patched into `if`/`elif` chains over types in other modules.
  - *Liskov substitution*: a subclass keeps every promise of its base class, for example the component lifecycle.
  - *Interface segregation*: a component declares only the inputs it reads, and a function takes only the
    arguments it needs, not a whole config object.
  - *Dependency inversion*: depend on ports, roles and small protocols, not on concrete classes of other modules
    (`component_design_principles.md`).
- **YAGNI (you aren't gonna need it).** Build what a current use case needs. No option, parameter, hook or
  generality for an imagined future case. Unused code -- a dead branch, an unread config field, a commented-out
  block -- is deleted, not kept "in case".
- **DRY (don't repeat yourself).** Every piece of knowledge has one place: a physical constant, a law, a set
  point, a conversion. Before writing a function, search for one with the same purpose (the purpose sentence
  below makes that possible) and generalise it rather than adding a second. Two pieces of code that look alike
  but stand for different knowledge are not duplicates.
- **Every function states its purpose in its first docstring sentence**, precisely enough that two functions
  with the same or a similar purpose can be found by comparing these sentences -- by a reviewer, or by an
  automated LLM check across the repository. The sentence says what the function computes or does, from what,
  and in which unit; not how and not where it is used.
  - Good: "Return the heat a water flow carries relative to a reference temperature, in W."
  - Not enough: "Helper for the storage." or "Compute the value."

## 2. Code rules

Where a rule has a CI check, the check is a **ratchet**: a committed baseline of today's violations that may only
shrink. A new violation fails the build; a fixed one leaves the baseline in the same pull request. The existing
violations are fixed bead by bead, never in one big rewrite.

1. **Small units.** A function has at most about 50 lines (hard limit 100), a file at most about 1000 lines (hard
   limit 1500), and a function takes at most 6 arguments; more arguments become a parameter object (a dataclass).
   A 468-line `i_simulate` can neither be unit-tested nor reviewed. CI: pylint size checks with a baseline
   (hisim-o6a.12).
2. **Leave it better (the boy-scout rule).** Whoever changes a function leaves it cleaner than they found it:
   shorter, better named, tested. Every metric in this section only improves.
3. **Typed, explicit data.** Structured data is a `@dataclass`, frozen where possible: no `dict[str, Any]` passed
   between functions, no positional tuples with meaning by index. No new `Any`. Every `# type: ignore` and
   `pylint: disable` names its reason on the same line. CI: escape-hatch ratchet (hisim-o6a.13), stricter mypy
   step by step (hisim-o6a.14).
4. **Explicit state, immutable configuration.** A configuration is frozen once the component is built; nothing
   changes it in `i_simulate`. The state that changes from step to step is one state dataclass per component,
   and `i_save_state` / `i_restore_state` copy that whole object (`dataclasses.replace`) instead of copying
   attributes by hand (the memento pattern, §3).
5. **Keyword-only arguments where values can be confused.** A function that takes two or more parameters of the
   same kind (temperatures, powers, mass flows) makes them keyword-only, so a swapped supply and return fails at
   the call instead of giving a quietly wrong result:
   `def circuit_power_in_watt(*, mass_flow_in_kg_per_second: float, supply_temperature_in_celsius: float,
   return_temperature_in_celsius: float) -> float`.
6. **Fail fast, no silent fallbacks.** Inputs are checked at the boundary: in the config's `__post_init__`, where
   a file is read. Invariants are asserted. No default value hides an error (a 0 that means "not published
   yet"), and no `except Exception` or bare `except:` keeps a calculation running. An error message says what
   was wrong, with which value, and what was expected. CI: part of hisim-o6a.13.
7. **Explicit control flow.** No `importlib.import_module` or `getattr` by string to dodge a circular import: a
   circular import is a design error and is fixed. No attributes created at run time, no monkeypatching in
   production code. The allowed import directions between packages are declared and checked. CI: import-linter
   (hisim-o6a.15).
8. **Tests first for bugs, coverage for new code.** A bug fix starts with a test that fails without the fix. New
   and changed lines are covered by tests (about 90 %). Tests are deterministic and independent of each other and
   of their order. Pure functions (§1) are tested in fast unit tests; system tests check only the wiring. CI:
   diff coverage (hisim-o6a.16).
9. **No magic numbers.** Every literal with a physical or technical meaning is a named class constant with its
   unit and its source (§4): not `0.992`, `60.0` or `0.01` in the middle of a formula.
10. **Composition over inheritance.** A class hierarchy is flat: `Component`, then the concrete class. No mixins
    that carry state. Shared behaviour sits in a helper that is passed in or called, not in a base class.
11. **Duplicates are searched for.** A regular job compares the first docstring sentences of all functions (§1)
    and files the candidates for merging as beads (hisim-o6a.17).

## 3. Design patterns

Use a pattern only when it solves a problem the code has now (YAGNI), in its Python form: a function or a
`Protocol` as a strategy, a dataclass as a value object, not a Java-style class hierarchy. Name the pattern in
the class docstring ("Strategy for the heating curve: ..."), so readers and automated checks recognise it.

### Patterns to use

| Pattern | Where in HiSim |
|---|---|
| **Template method** | The component lifecycle: the simulator calls `i_prepare_simulation`, `i_simulate`, `i_save_state`, `i_restore_state` and `i_doublecheck`; a component fills in these hooks and nothing else. |
| **Mediator** | The simulator and the `SingleTimeStepValues` connect the components. A component never calls another component; it exchanges values only through its ports. |
| **Memento** | `i_save_state` / `i_restore_state` save and restore one state dataclass as a whole (§2, rule 4). |
| **State machine** | A controller's modes (off, space heating, hot water), hysteresis and minimum run and rest times: an explicit state Enum, a transition table and a pure function that returns the next state ("Testing state machines" below). |
| **Strategy** | An interchangeable law -- heating curve, COP model, emitter law, control rule -- is chosen once, at construction, and called through one interface, instead of `if`/`elif` over an Enum inside `i_simulate`. |
| **Factory method and registry** | The `@preset` class methods, `MAIN_CLASS`, building a component from an energy-system file. No `if type == ...` chains to construct objects. |
| **Value object** | Immutable dataclasses for values that belong together (`Inflow`, `CalculationRequest`), and the quantities of `hisim/units.py`. |
| **Adapter** | An external library (hplib, bslib, pvlib, pylpg) sits behind a thin adapter class. The component knows only the adapter, which can be replaced, or faked in a test. |
| **Proxy for caching** | A cache wraps a pure computation (`hisim.caching`); the caching is not woven into the component's logic. |
| **Dependency injection** | Collaborators -- a cache, a data source, the result path, a clock -- are passed into the constructor, not fetched from a global singleton. |
| **Null object** | An optional collaborator (no energy manager, no buffer) is an object that does nothing, instead of `None` checks spread over the code. |

### Anti-patterns a review rejects

| Anti-pattern | What it looks like | Example in HiSim (2026-10-10) |
|---|---|---|
| **God class** | One class or module does many jobs | `more_advanced_heat_pump_hplib.py`, 3419 lines: physics, two controllers, the hplib cache and the SCOP calibration; a 607-line `__init__` |
| **Singleton, global state** | State reachable from everywhere | `SingletonMeta`, `ResultPathProviderSingleton`; the `SimRepository` used as a channel between components |
| **Type switch** | `if`/`elif`/`isinstance` over types or Enum values, repeated in several modules | `PositionHotWaterStorageInSystemSetup`, three Enums that must agree, compared in 7 places |
| **Shotgun surgery** | One change forces edits in many places | A new generator means editing the storage tank, the energy manager and the meter, whose default connections name it by class |
| **Feature envy** | Code that computes with another class's data | The occupancy component's KPI recomputes the tank's hot-water heat with the tank's constants |
| **Temporal coupling** | Methods that only work if called in a particular order | The building postpones its weather setup to the first `i_simulate`, because most setups add it before the weather |
| **Speculative generality** | Options, hooks and layers without a current user | (YAGNI, §1) |
| **Hidden control flow** | Imports and calls by string | `importlib.import_module` in 8 component modules |

### Testing state machines

A controller is a state machine, and it is written so that every state and every transition can be tested:

- **The machine is data plus a pure function.** The states are an `Enum`; the transitions are a table (current
  state, condition -> next state); `next_state(state, inputs, timers) -> state` is a pure function. The component
  only feeds it its inputs and stores the result in its state dataclass.
- **Exhaustive by construction.** Code that branches over the states uses `match` with `typing.assert_never` in
  the default case, so mypy reports a state nobody handles.
- **Every transition has a test, generated from the table.** A parametrised test runs every row of the table,
  plus the boundary values of each condition (exactly at the hysteresis edge, exactly at the minimum run time).
  A coverage check records the transitions the tests took and fails if a transition of the table was never
  taken, or if a transition was taken that the table does not list.
- **Invariants over random sequences.** A property-based test (Hypothesis, `RuleBasedStateMachine`) drives the
  machine with random sequences of inputs and step lengths and checks the invariants after every step: a
  minimum run or rest time is never cut short, the device is never in two modes, an off signal is obeyed
  once the minimum run time is over.
- **Iteration safety, the same test for every component.** Save the state, call `i_simulate` several times with
  different inputs, restore, call it with input X; the result must equal a fresh save followed by one call with
  X. This one generic test finds every `i_restore_state` that restores nothing and every state written in
  `i_simulate`.
- **The diagram comes from the table.** The state diagram in the docstring or documentation is generated from
  the transition table, so the picture reviewers check is the code that runs.

## 4. Constants and module state

- **Constants live on the class they belong to.** A constant that belongs to no single class goes into a small
  namespace class with a docstring. A module holds functions and classes, not values.
- **A set of strings is an `Enum`** (usually `class X(str, Enum)`), never a group of string constants or bare
  literals compared across the code.
- **No mutable module state**: no module-level caches, registries, counters or flags. State that must be shared
  lives in an object that is passed along; simulation-wide data such as prices may live in the `SimRepository`,
  which never replaces the wiring between components.

Example:

```python
# Not like this
DEFAULT_SET_TEMPERATURE_IN_CELSIUS = 60.0
_profile_cache: dict = {}


# Like this
class DhwController(Component):
    """..."""

    #: The hot-water set temperature when the configuration names none, in °C.
    DEFAULT_SET_TEMPERATURE_IN_CELSIUS = 60.0
```

Why: a class constant is found with its class, documented next to it and reviewed with it. Mutable module state
is hidden state: it survives between calculations in one process and between tests.

## 5. Names

- **Every name of a physical quantity carries its unit** (AGENTS.md, "Guidelines for coding"):
  `thermal_power_in_watt`, `temperature_in_celsius`. A dimensionless quantity says so (`part_load_ratio`).
- **A function or method is named for what it does or returns** (`child_environment`, `heat_to_target_in_joule`),
  not for when it is called or who calls it (`announced_in`, `handle_step`).
- **A boolean reads as a statement**: `is_converged`, `runs_part_load`, `has_buffer`.
- **No abbreviations a newcomer must guess** (`t0_c`, `hw_kg_s`, `ctrl`). Established terms of the field stay:
  `cop`, `dhw`, `pv`, `ua`.
- **Field, output and connection names are referenced through the class constants** that declare them, never
  repeated as string literals (AGENTS.md).

## 6. Docstrings

Every module, class and function has a docstring, including private helpers and tests. A docstring is written
for a reader who does not know the surrounding code. It must be complete, but not long.

The order:

1. **What** it is or does, in one plain sentence.
2. **Terms**: define every term of art the first time it appears.
3. **Example**: a short usage example or a worked number, where it helps.
4. **Why**: one or two short sentences, if the reason is not obvious.
5. **Args / Returns / Raises**: plain facts, one consequence each.

Together that is at least two to three sentences for every class and function: enough to state its full
purpose and how it fits in.

Rules:

- **Self-contained.** Never cite a spec or roadmap document ("see spec §8.4"). Specs are design records that get
  retired; the docstring stays with the code. Write the reason out instead.
- **Plain, not essayistic.** No inverted sentences, no rhetoric ("for exactly one reason", "right up to the point
  where"), no chains of consequences in one clause.
- **Units** of every physical argument and return value are stated, even when the name carries them.
- **A test's docstring names the failure it catches**: what would be wrong in the code if this test failed.

Example:

```python
def mixed_node_end_temperature_in_celsius(
    start_temperature_in_celsius: float, inflows: list[Inflow], duration_in_seconds: float
) -> float:
    """Return the temperature of a fully mixed water volume at the end of a step.

    A mixed node is a volume of water with one uniform temperature: every inflow mixes into it at once, and the
    same mass flows out at the node's temperature. For example, 200 kg at 50 °C receiving 0.05 kg/s at 70 °C for
    900 s ends at 54.0 °C.

    The step is integrated exactly, so the result does not depend on the step length.

    Args:
        start_temperature_in_celsius: The node's temperature at the start of the step, in °C.
        inflows: The flows entering the node, each with its mass flow in kg/s and temperature in °C.
        duration_in_seconds: The length of the step, in s.

    Returns:
        The node's temperature at the end of the step, in °C.

    Raises:
        ValueError: If the duration is not positive.
    """
```

Before pushing, read each new docstring once as a newcomer would.

## 7. Comments

- A comment says **why**, not what: the reason for a non-obvious choice, a unit conversion, a physical
  assumption, a reference value.
- Match the comment density of the surrounding code.
- No commented-out code, and no todo comments: a todo is a bead (`br create`).

## 8. Types, formatting and checks

- **Type hints** on every function argument and return value. The code passes the three CI mypy runs
  (`hisim`, `system_setups`, `tests`).
- **Formatting**: black, line length 170 (`pyproject.toml`; pylint uses the same in `.pylintrc`).
- **Before pushing**, on the changed files: flake8, pylint with `pylintrc-critical-only` (the CI gate), the three
  mypy runs and `semgrep --config .semgrep/ --error hisim/`.
- **All output goes through `hisim.log`** (`log.information`, `log.warning`, `log.error`, ...). `hisim/log.py` is
  the only module in `hisim/` that calls `print`; the CI enforces it (`.semgrep/no_print.yml`, whose temporary
  allowlist bead hisim-o6a.11 empties).
- **Exceptions**: raise a specific type, never catch everything (§2, rule 6).

## 9. Commit messages

- **The title is one sentence that states the result**, not the activity: "The hot-water cylinder is a node and
  every DHW circuit books the heat its water carries", not "Refactor storage". The PR number is appended at the
  squash merge.
- **The body is prose**: what changed and why, and what it does to simulation results.
- **Trailer**: only `Co-Authored-By:` for an agent's commit. **Never a session link** (`Claude-Session:` or any
  other link to an agent session).
- A pushed commit is never amended; a review fix is a new commit (AGENTS.md, "Pull requests and merging").

## 10. Pull request descriptions

Written by hand, never generated (`gh pr create --fill` turns a stale base into a description of unrelated
commits). Three sections:

- **Problem**: what was wrong or missing, with a concrete example or number.
- **What was done**: the changes, grouped by topic, with the decisions taken and by whom.
- **Result**: how it was verified (tests, golden check, measured numbers), whether results change and by how
  much (KPI deltas), and anything a reviewer must know before merging (stack position, a re-bless, a follow-up
  bead).

The title follows the commit-title rule of §9. No session links.
