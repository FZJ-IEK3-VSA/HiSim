# Random findings from the config-presets spike

Accidental discoveries, implementation friction and elegance opportunities logged while
building the design-B spike (branch `config_presets`, spec
`system_docs/config_defaults_spec.md` §8b PR 1). Not a to-do list yet — a raw capture to
work through later, one by one.

Legend: **[bug]** latent defect found along the way · **[friction]** implementation issue
worth fixing · **[elegance]** a cleaner design is possible · **[spec]** the spec needs a
correction or addition.

---

- **[spec] One lazy import is unavoidable in the sizing module layout.** The spec's §4.1
  says `hisim/sizing.py` imports `building_config.py` directly, "no lazy imports needed" —
  but `component.py` must import `sizing` (for the `Component.__init__` AUTO check and
  `ConfigBase.resolve`), and `building_config.py` imports `component.py` (ConfigBase), so
  `sizing → building_config` at module level would close the cycle after all. Resolution
  implemented: `sizing.py` imports nothing from hisim at module level;
  `SizingContext.for_building` imports `building_config` locally. Spec §4.1 should be
  corrected when the spike is reviewed.

- **[spec] Per-preset law variation exists in production after all.** The spec's design-B
  weakness note claims "no such variation exists today" — but `GenericBoilerConfig`'s
  scaled factories disagree on the *minimal* power law per fuel: gas/oil/hydrogen use a
  constant 0, pellet and wood chip use 1/12 of the *sized maximal* power. The escape
  hatch the spec sketched (a preset assigning a `SizingLaw` as a field value) is
  therefore implemented in the spike, not hypothetical. Correct the weakness note.

- **[elegance] Cross-field laws.** The pellet/wood-chip minimal power depends on the
  resolved *maximal* power, not on a context fact. The spike expresses it by reusing the
  maximal-power law times 1/12 (recomputing it), which is correct but computes the law
  twice and would drift if someone changed only the field's law. A `Field("...")` term
  referencing sibling fields of the same config would express it directly — consider for
  the sweep if more cross-field laws surface.

- **[friction] `Sizable[SomeEnum]` fields bypass dataclasses_json's enum handling.** The
  field-level decoder that `sized_field` injects *replaces* the library's default decoding,
  so a JSON `"FLOORHEATING"` stays a string instead of becoming the enum member. It works
  today only because config enums are `(str, Enum)` with value == member name, so equality
  comparisons still hold — but `is` comparisons would silently fail. The sweep needs a
  type-aware codec (sized_field capturing the field's concrete type, or the scenario codec
  handling Sizable unions) before converting enum-typed sizable fields broadly.
  `HeatDistributionConfig.heating_system` is the live instance of this.

- **[spec] The dataclass field-ordering constraint fired exactly as predicted.** Converting
  `HeatDistributionConfig` required giving the five trailing capex fields `None` defaults
  (they follow the now-defaulted sized fields). Harmless here — `None` was what every
  factory passed anyway — but the sweep should expect this on every class it converts.

- **[elegance] Setups now build several small SizingContexts instead of one.** The pilot
  conversion constructs an inline context per component (boiler, HDS) because the spike
  does not touch the setups' structure. The sweep should build ONE context per setup
  (`SizingContext.for_building(...)` + `.with_facts(...)` enrichment) and thread it — that
  is the design's actual intent and removes the repetition.

- **[friction] The conversion changes JSON literal spellings, not values.** Regenerating
  all scenario JSONs after the pilot conversion shows exactly two kinds of drift: the
  `config_full_classname` of `BuildingConfig` follows its module move
  (`building` → `building_config`), and int literals became float literals (`0` → `0.0`,
  `20` → `20.0`, `1000` → `1000.0`) where presets pass floats that the old factories
  passed as Python ints. Numerically identical, golden-neutral, but the freshness gate
  demands the regenerated files be committed with the change — the sweep will produce the
  same class of diff on every converted component.

- **[bug] Import-insertion tooling corrupted one setup.** Inserting the SizingContext
  import after "from hisim.simulator import SimulationParameters" broke
  `household_heatpump_solar_thermal_building_sizer.py`, whose import line continued with
  ", Simulator". Caught by the regeneration gate, fixed; noted as a reminder that textual
  import insertion needs full-line anchors.

- **[elegance→adopted] `concrete()` as the read-side idiom for sizable fields.** As the
  spec predicted, mypy sees `Sizable[float]` on component reads even though the central
  init check guarantees concreteness by then. Rather than casting, the spike added
  `sizing.concrete(value)` — a typed pass-through with a runtime assertion — used at the
  four pilot read sites. `Sizable` was also widened to include `SizingLaw` (the per-preset
  escape hatch makes law values legal field content pre-resolution). Both belong in the
  spec's §4.1 when the spike is reviewed.

- **[friction] Forward-referenced type aliases break get_type_hints in foreign modules.**
  `Sizable = Union[T, _AutoSize, "SizingLaw"]` (string forward ref, defined above the
  class) made dataclasses_json's `get_type_hints` fail with NameError in every config
  module that did not itself import SizingLaw. Fixed by defining the alias below the
  class with a real reference. General lesson for shared aliases: never leave a string
  forward reference in an alias that other modules' dataclasses annotate with.

- **[bug] The SingletonSimRepository's sizing keys are dead.** `NUMBEROFAPARTMENTS`,
  `MAXTHERMALBUILDINGDEMAND`, `WATERMASSFLOWRATEOFHEATGENERATOR` and the storage
  set-temperature keys are declared and *read* (`simple_water_storage` silently falls
  back to `None` via `entry_exists`) but written by nobody — their writers vanished with
  the modular-household removals. The cross-component sizing channel has silently rotted;
  every read of these keys always takes the fallback branch today. Short-term: delete the
  dead keys and the fallback reads (they are dead code); long-term: the SizingContext /
  context-contributor design (spec §8.4) replaces the construction-time half of the
  singleton outright. The runtime-forecast half (MPC/PID heat-flux, weather and price
  forecasts) is still live and needs its own redesign decision — probably proper wiring.

---

## Rebuild on the cleaned building package (2026-08-21)

Findings from rebuilding the same machinery as the layered `hisim/config/` package on top
of the building-cleanup branch.

- **[elegance→adopted] The config layer wants to be a package, not two modules.** The
  spike's `hisim/sizing.py` + `hisim/sizing_engine.py` pair had to live *outside*
  `hisim/component.py` for the cycle reason recorded above, but nothing expressed *why*
  they sit where they sit. Moving `ConfigBase`, `ComponentID` and `DisplayConfig` out of
  `component.py` into `hisim/config/base.py` and putting the machinery next to them turns
  the accidental arrangement into a stated rule: `hisim/config/` imports nothing from the
  rest of HiSim, `hisim/component.py` imports it. The cycle argument stops being folklore
  in a docstring and becomes a property of the package boundary. Related: dropping the
  compatibility aliases from `component.py` (169 files updated) is what makes the rule
  checkable by grep rather than by trust.

- **[friction] `SIZING_CONTRIBUTIONS` needed a home on `ConfigBase`.** The engine reads a
  class attribute by name (`getattr(type(config), CONTRIBUTIONS_ATTRIBUTE, ())`), which
  works at runtime but leaves mypy rejecting every `SomeConfig.SIZING_CONTRIBUTIONS = ...`
  assignment on a class that did not pre-declare it — including the test fixtures, which
  are the engine's own oracle. Declaring it once on `ConfigBase` fixes that, but the
  element type has to stay `Any`: naming `FactContribution` would either invert the
  package layering (base importing the engine) or leave a `TYPE_CHECKING`-only forward
  reference in an annotation that `dataclasses_json` evaluates from every subclass's
  module — the *same* trap as the `Sizable` alias one section above, hit from the other
  direction. Third occurrence of the pattern; worth a rule: never put a name in a
  `ConfigBase` annotation that is not importable at runtime from `hisim/config/base.py`.

- **[friction] pylint's `cyclic-import` cannot see the difference between a lazy import
  and a real one.** The spec's §4.1 resolution — `SizingContext.for_building` importing
  the building package inside the method body — is invisible to Python's import system but
  fully visible to pylint's import graph, so prospector reports the cycle anyway. Worse, it
  attributes the message to an arbitrary module *outside* the cycle (a file under
  `hisim/inputs/`), so a `# pylint: disable=cyclic-import` at the actual site does nothing.
  Disabled in `.prospector.yml`, matching `pylintrc-critical-only`, which already disables
  it. If the check is ever wanted back, the honest fix is to move `for_building` into the
  building package and leave `SizingContext` fact-agnostic.

- **[friction] Mechanical call-site rewriting needs a syntax gate, not a diff review.**
  Converting the 72 `get_default_german_single_family_home` call sites with a
  keyword-args-to-field-assignments script silently produced one wrecked file: the setup
  whose last keyword argument ended `...,)` on the same line ran the "read until the
  closing paren" loop past the end of the call and rewrote nine following statements into
  attribute assignments. `compileall` over the touched trees caught it instantly. Any
  future sweep of this shape should run a parse gate per file, not per tree.

- **[elegance] `Catalog` attribute access is inherently `Any`.** `presets.X` cannot be
  typed: a `ClassVar` may not contain a type variable, so `Catalog` cannot be generic over
  the config class it serves. Call sites that immediately return the preset therefore need
  an explicit annotation to satisfy `warn_return_any`. A `__class_getitem__`-based
  `Catalog[SomeConfig]` declared as a plain class attribute (not `ClassVar`) might work
  once the `Component[TConfig]` generics sweep lands; worth revisiting then.
