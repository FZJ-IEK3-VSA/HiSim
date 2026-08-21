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
