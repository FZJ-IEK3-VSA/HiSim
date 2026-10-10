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

## 2. Constants and module state

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

## 3. Names

- **Every name of a physical quantity carries its unit** (AGENTS.md, "Guidelines for coding"):
  `thermal_power_in_watt`, `temperature_in_celsius`. A dimensionless quantity says so (`part_load_ratio`).
- **A function or method is named for what it does or returns** (`child_environment`, `heat_to_target_in_joule`),
  not for when it is called or who calls it (`announced_in`, `handle_step`).
- **A boolean reads as a statement**: `is_converged`, `runs_part_load`, `has_buffer`.
- **No abbreviations a newcomer must guess** (`t0_c`, `hw_kg_s`, `ctrl`). Established terms of the field stay:
  `cop`, `dhw`, `pv`, `ua`.
- **Field, output and connection names are referenced through the class constants** that declare them, never
  repeated as string literals (AGENTS.md).

## 4. Docstrings

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

## 5. Comments

- A comment says **why**, not what: the reason for a non-obvious choice, a unit conversion, a physical
  assumption, a reference value.
- Match the comment density of the surrounding code.
- No commented-out code, and no todo comments: a todo is a bead (`br create`).

## 6. Types, formatting and checks

- **Type hints** on every function argument and return value. The code passes the three CI mypy runs
  (`hisim`, `system_setups`, `tests`).
- **Formatting**: black, line length 170 (`pyproject.toml`; pylint uses the same in `.pylintrc`).
- **Before pushing**, on the changed files: flake8, pylint with `pylintrc-critical-only` (the CI gate), the three
  mypy runs and `semgrep --config .semgrep/ --error hisim/`.
- **All output goes through `hisim.log`** (`log.information`, `log.warning`, `log.error`, ...). `hisim/log.py` is
  the only module in `hisim/` that calls `print`; the CI enforces it (`.semgrep/no_print.yml`, whose temporary
  allowlist bead hisim-o6a.11 empties).
- **Exceptions**: raise a specific type with a message that says what was wrong and with which value. Never catch
  `Exception` or use a bare `except:` to keep a calculation running.

## 7. Commit messages

- **The title is one sentence that states the result**, not the activity: "The hot-water cylinder is a node and
  every DHW circuit books the heat its water carries", not "Refactor storage". The PR number is appended at the
  squash merge.
- **The body is prose**: what changed and why, and what it does to simulation results.
- **Trailer**: only `Co-Authored-By:` for an agent's commit. **Never a session link** (`Claude-Session:` or any
  other link to an agent session).
- A pushed commit is never amended; a review fix is a new commit (AGENTS.md, "Pull requests and merging").

## 8. Pull request descriptions

Written by hand, never generated (`gh pr create --fill` turns a stale base into a description of unrelated
commits). Three sections:

- **Problem**: what was wrong or missing, with a concrete example or number.
- **What was done**: the changes, grouped by topic, with the decisions taken and by whom.
- **Result**: how it was verified (tests, golden check, measured numbers), whether results change and by how
  much (KPI deltas), and anything a reviewer must know before merging (stack position, a re-bless, a follow-up
  bead).

The title follows the commit-title rule of §7. No session links.
