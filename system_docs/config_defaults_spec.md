# Config defaults and sizing — three candidate designs

**Status:** draft for team discussion — nothing implemented. Two decisions already made
(2026-08-19): design B was revised for readability (laws at the field, `AUTO` values —
see the §4 revision note), and the JSON preset-reference contract §8.1(b) is wanted in
the final product regardless of design choice.
**Date:** 2026-08-19
**Context:** sweep 3 of `roadmap/json_cleanup.md`; feeds the JSON scenario v2 migration
(`system_docs/json_scenario_v2_spec.md`), whose executor currently discovers default
configs by regex (`^get_.*default`, case-insensitive) plus required-parameter-count
ranking — a heuristic this redesign must delete.

---

## 1. Problem statement

Components need **multiple, named default configurations** (a condensing gas boiler, an
oil boiler, a pellet boiler — all on `GenericBoilerConfig`) and **automatic sizing** of
those defaults to the surrounding system (building heating load, occupancy DHW demand).
Today this is expressed as 104 `get_*default*` classmethods with 64 distinct naming
spellings, many duplicated into `get_default_X` / `get_scaled_X` twins.

Rejected up front: one `get_default_config(name: str)` dispatcher — it produces long
if-chains and the string name cannot be autocompleted or type-checked while writing a
setup.

### Requirements

R1. **Named variants.** A config class can declare several named defaults; referencing
    one autocompletes and a typo fails at authoring time, not at runtime.
R2. **Sizing.** Defaults can be scaled to the surrounding system. Sizing is integrated,
    not a parallel mechanism — the current default/scaled twin factories must collapse.
R3. **Sizing is usually wanted, but not always.** A manually configured scenario that
    mirrors a real installation must be able to take a named default *unsized* and set
    the real device values by hand. Whether a config was auto-sized must be visible
    afterwards (audit/provenance).
R4. **Machine-enumerable.** The v2 executor, the contract test, and the planned GUI can
    enumerate all named defaults of a class and construct each one, with no regex, no
    ranking, no per-class special cases. Exactly one *canonical* default per class.
R5. **Systematic test variation.** Tests can iterate `all variants × {unsized, sized}`
    per component, skipping invalid combinations by declaration rather than try/except.
R6. **Fresh instances.** Every access yields a new object — setups mutate configs freely.
R7. **Low ceremony.** The mechanism must stay pleasant across 100+ config classes; adding
    one more named default should be roughly one line of data, not five synchronized
    declarations.
R8. **Computed values allowed.** Some defaults derive values (scaling laws, temperature
    tables by heat-distribution type); the design must not force everything to be a
    constant.

### Facts that shaped the designs (AST survey of all 104 factories)

- `component_id` appears in 100 factories and legacy `name` in 21 — identity, not
  configuration. The v2 executor injects identity anyway; no design below makes it part
  of a named default.
- Stripping identity, **every required parameter** across all factories is a *sizing
  fact*: `heating_load_of_building_in_watt`, `number_of_apartments_in_building`,
  reference temperature, the boiler controller's min/max power (derived from the sized
  boiler), CHP thermal power, `heat_distribution_system_type`.
- The remaining optional parameters are **plain config fields** pre-set to non-default
  values (`with_domestic_hot_water_preparation`, heating thresholds, PV module names).
  Setups already mutate such fields after the factory call; that spelling autocompletes
  and is guarded by `scripts/check_config_attrs.py`. **No design below adds a second way
  to set fields** — an `**overrides` kwarg was considered and rejected as an untyped
  duplicate of `config.field = value`.

### Shared ingredient: `SizingContext`

All three designs consume the same context object (decided earlier, recorded in
`roadmap/json_cleanup.md`): a small **frozen, scope-resolved snapshot** of the facts a
component may size against.

```python
@dataclass(frozen=True)
class SizingContext:
    heating_load_in_watt: Optional[float] = None
    heating_reference_temperature_in_celsius: Optional[float] = None
    number_of_apartments: Optional[int] = None
    conditioned_floor_area_in_m2: Optional[float] = None
    water_mass_flow_rate_in_kg_per_second: Optional[float] = None
    heat_distribution_system_type: Optional[HeatDistributionSystemType] = None
    number_of_residents: Optional[int] = None   # occupancy-derived, for DHW

    @classmethod
    def for_building(cls, building_config: BuildingConfig,
                     occupancy_config: Optional[UtspLpgConnectorConfig] = None) -> "SizingContext": ...
    # future, when buildings declare units/apartments:
    # @classmethod
    # def for_unit(cls, building_config, unit: str) -> "SizingContext": ...
```

Why not pass `BuildingConfig` directly: the needed facts are *computed* from it
(`BuildingInformation` runs the TABULA lookup — file I/O that must not hide inside every
factory call), some facts are not building facts at all (DHW demand from occupancy, the
controller's power band from the sized boiler), taking `BuildingConfig` in ~40 config
classes couples every component module to the heavy Building component, and future
units-in-buildings need a *scope-resolved* view (`for_unit`) so that no builder ever
learns to slice apartments itself.

### Why the previously drafted design was set aside

The first draft (nested `Preset` enum + `from_preset(preset, *, sizing)` + per-preset
`SizingRule` table) satisfies R1–R5 but fails R7: every class carries **five parallel
structures** that must stay consistent (enum, sizing-rule table, fields-read list,
builder methods, entry point), the builders still spell out the full field list, the
nominal/scaled branch inside each builder is the if-chain in disguise, and the enum
member is opaque — no jump-to-definition from `Preset.CONDENSING_GAS` to what it sets.

---

## 2. Running examples

All three designs are shown on the same three components so they can be compared line by
line. Field lists are the real ones (capex fields abbreviated to `...capex_none...`,
meaning the five cost fields set to `None` for the postprocessing lookup).

- **`GenericBoilerConfig`** — the variant-rich case: five fuels, each currently with a
  default/scaled twin factory (10 methods). Nominal condensing-gas device: 12 kW max,
  1 kW min; scaled: `scale_thermal_power(heating_load, n_apartments)` and min = 0.
- **`HeatDistributionConfig`** — the sizing-mandatory case: its only factory *requires*
  water mass flow rate, floor area and heating-system type. There is no defensible
  nominal heat distribution system; inventing one would silently mis-size setups.
- **`EMSConfig`** — the nothing-to-size case: strategy string, peak-shaving limit, three
  temperature offsets.

---

## 3. Design A — presets are config *instances*; sizing is a method

**Paradigm:** a named default is most naturally a **literal instance** — the dataclass is
already the declaration syntax. Scaling is an ordinary transformation `config → config`.
The only shared machinery is a ~30-line `Catalog` helper whose attribute access calls the
stored zero-arg factory and hands back a **fresh instance** (R6), lazily (no I/O at
import).

### 3.1 The shared helper

```python
class Catalog(Generic[TConfig]):
    """Attribute-style access to named default configs. Each access builds a NEW instance.

    catalog = Catalog(condensing_gas=lambda: GenericBoilerConfig(...), oil=lambda: ...)
    catalog.condensing_gas   -> fresh GenericBoilerConfig
    catalog.canonical        -> alias for the first entry
    iter(catalog)            -> (name, builder) pairs, for tests / executor / GUI
    """
```

A sentinel value marks fields that have no defensible nominal:

```python
REQUIRES_SIZING: Final = _RequiresSizing()   # falsy, reprs as "<REQUIRES_SIZING>"
```

`Component.__init__` performs the one central check: constructing a component whose
config still carries `REQUIRES_SIZING` anywhere is a hard error naming the fields. This
catches "forgot to size" at the right moment **regardless of which code path built the
config** — factory, JSON, or manual construction.

### 3.2 `GenericBoilerConfig` (variants + optional sizing)

```python
@dataclass_json
@dataclass
class GenericBoilerConfig(ConfigBase):
    boiler_type: BoilerType
    energy_carrier: lt.LoadTypes
    temperature_delta_in_celsius: float
    minimal_thermal_power_in_watt: float
    maximal_thermal_power_in_watt: float
    eff_th_min: float
    eff_th_max: float
    ...capex_none...

    def sized(self, ctx: SizingContext) -> "GenericBoilerConfig":
        """Returns a copy scaled to the context (today's get_scaled_* logic, once)."""
        return dataclasses.replace(
            self,
            maximal_thermal_power_in_watt=scale_thermal_power(
                ctx.heating_load_in_watt, ctx.number_of_apartments),
            minimal_thermal_power_in_watt=0.0,
        )

    presets: ClassVar[Catalog["GenericBoilerConfig"]] = Catalog(
        condensing_gas=lambda: GenericBoilerConfig(
            component_id=ComponentID("CondensingGasBoiler"),
            boiler_type=BoilerType.CONDENSING, energy_carrier=lt.LoadTypes.GAS,
            temperature_delta_in_celsius=20,
            minimal_thermal_power_in_watt=1_000, maximal_thermal_power_in_watt=12_000,
            eff_th_min=0.60, eff_th_max=0.90, ...capex_none...),
        oil=lambda: GenericBoilerConfig(
            component_id=ComponentID("OilBoiler"),
            boiler_type=BoilerType.CONVENTIONAL, energy_carrier=lt.LoadTypes.OIL,
            temperature_delta_in_celsius=20,
            minimal_thermal_power_in_watt=1_000, maximal_thermal_power_in_watt=15_000,
            eff_th_min=0.60, eff_th_max=0.90, ...capex_none...),
        pellets=lambda: GenericBoilerConfig(...),
        wood_chips=lambda: GenericBoilerConfig(...),
        hydrogen=lambda: GenericBoilerConfig(...),
    )
```

Usage:

```python
ctx = SizingContext.for_building(my_building_config)

# auto-sized household:
my_boiler_config = GenericBoilerConfig.presets.oil.sized(ctx)

# quick test, nominal catalog device:
my_boiler_config = GenericBoilerConfig.presets.condensing_gas

# real installation, deliberately NOT auto-sized (R3):
my_boiler_config = GenericBoilerConfig.presets.oil
my_boiler_config.maximal_thermal_power_in_watt = 18_000.0   # the plate value
```

Ten factory methods become five one-expression preset entries plus one `sized()` method.

### 3.3 `HeatDistributionConfig` (sizing mandatory)

```python
@dataclass_json
@dataclass
class HeatDistributionConfig(cp.ConfigBase):
    heating_system: HeatDistributionSystemType
    water_mass_flow_rate_in_kg_per_second: float
    absolute_conditioned_floor_area_in_m2: float
    position_hot_water_storage_in_system: PositionHotWaterStorageInSystemSetup
    ...capex_none...

    def sized(self, ctx: SizingContext) -> "HeatDistributionConfig":
        return dataclasses.replace(
            self,
            heating_system=ctx.heat_distribution_system_type,
            water_mass_flow_rate_in_kg_per_second=round(ctx.water_mass_flow_rate_in_kg_per_second, 2),
            absolute_conditioned_floor_area_in_m2=ctx.conditioned_floor_area_in_m2,
        )

    presets: ClassVar[Catalog["HeatDistributionConfig"]] = Catalog(
        standard=lambda: HeatDistributionConfig(
            component_id=ComponentID("HeatDistributionSystem"),
            heating_system=REQUIRES_SIZING,
            water_mass_flow_rate_in_kg_per_second=REQUIRES_SIZING,
            absolute_conditioned_floor_area_in_m2=REQUIRES_SIZING,
            position_hot_water_storage_in_system=PositionHotWaterStorageInSystemSetup.PARALLEL,
            ...capex_none...),
        series_storage=lambda: dataclasses.replace(
            HeatDistributionConfig.presets.standard,
            position_hot_water_storage_in_system=PositionHotWaterStorageInSystemSetup.SERIES),
    )
```

```python
my_hds_config = HeatDistributionConfig.presets.standard.sized(ctx)      # normal path

my_hds_config = HeatDistributionConfig.presets.standard                 # forgot to size...
my_hds = HeatDistribution(config=my_hds_config, my_simulation_parameters=...)
# ConfigSizingError: HeatDistributionConfig for 'HeatDistributionSystem' still requires
# sizing in fields: heating_system, water_mass_flow_rate_in_kg_per_second,
# absolute_conditioned_floor_area_in_m2. Call .sized(ctx) or set them explicitly.
```

Note the error also permits the manual escape (R3): setting the three fields by hand is
just as valid as calling `sized()`.

### 3.4 `EMSConfig` (nothing to size)

```python
@dataclass_json
@dataclass
class EMSConfig(cp.ConfigBase):
    strategy: str
    limit_to_shave: float
    building_indoor_temperature_offset_value: float
    domestic_hot_water_storage_temperature_offset_value: float
    space_heating_water_storage_temperature_offset_value: float
    ...capex_none...

    # no sized() override: ConfigBase.sized(ctx) raises NothingToSizeError, so a setup
    # that believes it sized the EMS fails loudly instead of no-opping.

    presets: ClassVar[Catalog["EMSConfig"]] = Catalog(
        optimize_own_consumption=lambda: EMSConfig(
            component_id=ComponentID("L2EMSElectricityController"),
            strategy="optimize_own_consumption", limit_to_shave=0,
            building_indoor_temperature_offset_value=2,
            domestic_hot_water_storage_temperature_offset_value=10,
            space_heating_water_storage_temperature_offset_value=10, ...capex_none...),
    )
```

The base-class default of `sized()` is the one design decision inside A: *raise*
(chosen here — mirrors "IGNORED errors, no silent no-ops") vs. *return an unchanged
copy* (more convenient for "size everything" loops; the loop can catch
`NothingToSizeError` instead).

### 3.5 How A meets the machinery requirements

- **R4:** canonical = `cls.presets.canonical`; enumeration = `iter(cls.presets)`.
  The v2 executor's regex + ranking and the contract test's `DEFAULT_CONFIG_ARGUMENTS`
  table are deleted.
- **R5:** the contract test iterates `presets × {as-is, .sized(fixture_ctx)}`; the
  invalid cells declare themselves (`NothingToSizeError` for EMS-likes; `REQUIRES_SIZING`
  sentinels surviving the unsized cell are *expected* there and asserted).
- **R3 provenance:** `sized()` sets a non-serialized `was_sized_from: Optional[str]`
  breadcrumb; the component report prints preset name + sized/nominal/manual per
  component.

**Strengths:** least mechanism of all three (one helper + one method + one sentinel);
preset definitions are constructor calls, so jump-to-definition and field autocomplete
work everywhere; adding a variant is one entry (R7).
**Weaknesses:** `sized()` is per-class, so variants needing *different* scaling laws
would need a branch inside it (no such case exists today); the sentinel is less
self-describing than a declared rule; sizing logic stays imperative Python (invisible to
JSON/GUI — contrast design B).

---

## 4. Design B (revised 2026-08-19) — field-declared sizing laws, `AUTO` values

> Revision note: the first draft of this design put the sizing rule *into the field
> value* of each preset (`maximal_thermal_power_in_watt=SizingRule(source=...,
> transform="...")`). That was rejected for readability: preset declarations stopped
> looking like `name = value`, and every preset repeated a law that is really
> **per-class physics knowledge**. The revision moves the law to the field declaration
> and reduces field values to exactly two kinds: a concrete value, or `AUTO`.

**Paradigm:** stop asking factories to produce finished numbers. Each sizable field
declares its sizing law **once, where the field is declared**; a preset (or a scenario
file, or a hand-written setup) only ever says *concrete value* or *`AUTO`*. One shared
resolver turns `AUTO` into numbers when the context is known. The
nominal/scaled/required distinction dissolves into *which fields of a preset say `AUTO`*.

### 4.1 The three pieces

```python
AUTO: Final = _AutoSize()          # the single sentinel; reprs as "AUTO"
Sizable = Union[T, _AutoSize]      # alias, so annotations read Sizable[float]

def sized_field(*, rule: SizingLaw, default: Any = AUTO, **field_kwargs) -> Any:
    """dataclasses.field carrying the field's sizing law in its metadata."""

class ConfigBase:
    def resolve(self, ctx: SizingContext) -> Self:
        """Returns a copy in which every AUTO field is computed by its declared law.
        A law needing a context fact that is absent -> hard error naming field & fact."""
    def auto_fields(self) -> tuple[str, ...]: ...
```

Sizing laws are written as **arithmetic expressions over `Size.*` terms** (an expression
tree via operator overloading — it reads like the formula and serializes for the audit
trail), or as a plain function of the context where the law is genuinely computational:

```python
rule=1.1 * Size.HEATING_LOAD                      # linear law, formula-like
rule=(Size.HEATING_LOAD * 1.1).at_least(5_000)    # with bounds
rule=Size.HDS_TYPE                                 # pass-through of a context fact
rule=lambda ctx: scale_thermal_power(ctx.heating_load_in_watt, ctx.number_of_apartments)
```

A law may also be a plain **constant** (`rule=0.0` — "the sized value is 0.0"), which
several real scaled factories need and which reads better than a zero-factor expression.

`Component.__init__` rejects configs that still carry `AUTO` anywhere — the same single
central check as design A's sentinel, but **self-describing**: the error prints each
field's declared law. This catches "forgot to size" regardless of whether the config
came from a preset, a JSON file or manual construction.

Implementation decisions (2026-08-19):

- **`resolve()` semantics.** Idempotent no-op when the class declares sizable fields but
  none currently says `AUTO` — already-sized and manually-set configs are legitimate
  resolve targets, so "size everything in this setup" loops are safe to write. It raises
  `NothingToSizeError` only when the class declares no `sized_field` at all (the EMS
  case): passing a sizing context to a component that can never use one is a setup bug.
- **`AUTO` is a distinct sentinel object; `sized_field` injects its wire codec**
  (decided 2026-08-19, revising an earlier str-subclass idea: `Sizable[str]` becomes a
  first-class case once sizing selects real devices out of lists, so the sentinel must
  never *be* a string in Python). `_AutoSize` is a copy-stable singleton
  (`__deepcopy__` returns `self`, so `dataclasses.replace`/`deepcopy` never break the
  `value is AUTO` identity check), and `sized_field` merges a dataclasses_json
  field encoder/decoder pair (`AUTO ↔ "AUTO"`) into the same metadata dict that carries
  the law — one declaration, no author-visible machinery, same pattern the repo already
  uses for `KpiEntry.tag`. Residual and accepted: the *wire* spelling `"AUTO"` remains
  in-band, so a JSON file cannot express a device literally named "AUTO"; in Python the
  collision does not exist. The one codec touch-point (learned in the spike): the injected field decoder
  *replaces* dataclasses_json's own handling, so enum-typed sizable fields must pass
  their concrete type — ``sized_field(rule=..., value_type=SomeEnum)`` — or a JSON
  member name would stay a string; `ScenarioConfigCodec` additionally passes `"AUTO"`
  through untouched on such fields instead of decoding it as a member name.
- **Error quality is split by law kind.** Expression laws know which context facts they
  read, so a missing fact errors as "field X needs heating_load_in_watt, absent from the
  SizingContext". Function laws cannot be introspected; they get a field-level error
  wrapping whatever the function raised. Writing the law as an expression is therefore
  mildly preferred where both work.
- **Field ordering.** `sized_field` carries a default (`AUTO`), and Python forbids
  non-default dataclass fields after defaulted ones — so in every migrated config the
  `Sizable` fields (and everything else defaulted) sit below the non-default fields.
  Mechanical, but every class conversion has to respect it.
- **Provenance.** `resolve()` sets a `sizing_record` on the returned copy via plain
  attribute assignment — deliberately *not* a dataclass field, so `to_dict`, equality
  and `dataclasses.replace` all ignore it with no exclusion machinery. It is a tuple of
  frozen `(field, law_description, facts_read, value)` entries; the audit-artifact
  writer (§8.3) reads it with `getattr(config, "sizing_record", None)`.
- **Module layout.** All sizing machinery — `AUTO`, `Sizable`, `sized_field`, the
  `Size.*` expression terms, `SizingContext`, the resolver — lives in one new module,
  `hisim/sizing.py`. The `Size.*` term vocabulary is derived from `SizingContext`'s
  dataclass fields (one registry, so the two cannot drift). To avoid the import cycle
  (`SizingContext.for_building` needs the building physics, `building.py` needs
  `component.py`), `BuildingConfig` and `BuildingInformation` move out of the oversized
  `building.py` into a new `hisim/components/building_config.py` (with `building.py`
  re-exporting them for compatibility). One lazy import remains (corrected from the
  first draft of this note): `component.py` imports `sizing` for the central check and
  `ConfigBase.resolve`, and `building_config.py` imports `component.py`, so `sizing.py`
  imports nothing from hisim at module level and `SizingContext.for_building` imports
  `building_config` locally.

  > **Module layout as built (2026-08-21).** The layout above was refined when the
  > machinery was implemented on top of the cleaned building package. The machinery does
  > not live in two top-level modules but in one **layered package, `hisim/config/`**:
  > `base.py` (`ComponentID`, `ConfigBase`, `DisplayConfig`, *moved out of*
  > `hisim/component.py` — no compatibility alias is left behind, every call site imports
  > from `hisim.config`), `presets.py` (`Catalog` and the preset-provenance stamp),
  > `sizing.py` (everything this note lists) and `engine.py` (§8.4). The package imports
  > nothing from the rest of HiSim at module level; `hisim/component.py` imports *it*,
  > which is what makes the direction of the dependency unambiguous instead of merely
  > acyclic. The `BuildingConfig`/`BuildingInformation` split happened separately, as the
  > `hisim/components/building/` package (`config.py`, `information.py`, `window.py`,
  > `building.py`), so the lazy import in `SizingContext.for_building` now reaches
  > `hisim.components.building.information` — the substance of the correction above is
  > unchanged, only the module names moved.
- **Read-side idiom and the widened union (from the spike).** Component runtime code
  reads sized fields as `Sizable[T]` even though the central check guarantees
  concreteness by then; `sizing.concrete(value)` is the typed, runtime-asserted
  accessor used at such read sites instead of a bare cast. `Sizable` itself is
  `T | _AutoSize | SizingLaw`, because the per-preset escape hatch makes law values
  legal field content before resolution.

### 4.2 `GenericBoilerConfig`

```python
@dataclass_json
@dataclass
class GenericBoilerConfig(ConfigBase):
    boiler_type: BoilerType
    energy_carrier: lt.LoadTypes
    temperature_delta_in_celsius: float
    minimal_thermal_power_in_watt: Sizable[float] = sized_field(rule=0.0 * Size.HEATING_LOAD)
    maximal_thermal_power_in_watt: Sizable[float] = sized_field(
        rule=lambda ctx: scale_thermal_power(ctx.heating_load_in_watt, ctx.number_of_apartments))
    eff_th_min: float = 0.60
    eff_th_max: float = 0.90
    ...capex_none...

    presets: ClassVar[Catalog["GenericBoilerConfig"]] = Catalog(
        # sizable template: the power fields say AUTO — pure name = value
        condensing_gas=lambda: GenericBoilerConfig(
            component_id=ComponentID("CondensingGasBoiler"),
            boiler_type=BoilerType.CONDENSING, energy_carrier=lt.LoadTypes.GAS,
            temperature_delta_in_celsius=20,
            minimal_thermal_power_in_watt=AUTO,
            maximal_thermal_power_in_watt=AUTO,
            eff_th_min=0.60, eff_th_max=0.90, ...capex_none...),
        # concrete catalog device: nothing to resolve
        condensing_gas_12kw=lambda: GenericBoilerConfig(
            component_id=ComponentID("CondensingGasBoiler"),
            boiler_type=BoilerType.CONDENSING, energy_carrier=lt.LoadTypes.GAS,
            temperature_delta_in_celsius=20,
            minimal_thermal_power_in_watt=1_000.0, maximal_thermal_power_in_watt=12_000.0,
            eff_th_min=0.60, eff_th_max=0.90, ...capex_none...),
        oil=..., pellets=..., wood_chips=..., hydrogen=...,
    )
```

```python
my_boiler_config = GenericBoilerConfig.presets.condensing_gas.resolve(ctx)   # sized
my_boiler_config = GenericBoilerConfig.presets.condensing_gas_12kw           # nominal
# real installation:
my_boiler_config = GenericBoilerConfig.presets.condensing_gas_12kw
my_boiler_config.maximal_thermal_power_in_watt = 18_000.0
```

The law appears exactly once, on the line where the field is declared — today the same
law hides inside the `get_scaled_*` method body, invisible at the field. Every *use*
site (presets, setups, JSON) stays `name = value`.

### 4.3 `HeatDistributionConfig`

The "required" case needs no sentinel of its own and no rule table — it is simply a
preset whose essential fields all say `AUTO`, so it *cannot* reach a component unsized:

```python
@dataclass_json
@dataclass
class HeatDistributionConfig(cp.ConfigBase):
    heating_system: Sizable[HeatDistributionSystemType] = sized_field(rule=Size.HDS_TYPE)
    water_mass_flow_rate_in_kg_per_second: Sizable[float] = sized_field(
        rule=Size.WATER_MASS_FLOW.rounded(2))
    absolute_conditioned_floor_area_in_m2: Sizable[float] = sized_field(rule=Size.FLOOR_AREA)
    position_hot_water_storage_in_system: PositionHotWaterStorageInSystemSetup = \
        PositionHotWaterStorageInSystemSetup.PARALLEL
    ...capex_none...

    presets = Catalog(
        standard=lambda: HeatDistributionConfig(
            component_id=ComponentID("HeatDistributionSystem")),   # all sizables default to AUTO
        series_storage=lambda: HeatDistributionConfig(
            component_id=ComponentID("HeatDistributionSystem"),
            position_hot_water_storage_in_system=PositionHotWaterStorageInSystemSetup.SERIES),
    )

HeatDistribution(config=HeatDistributionConfig.presets.standard, ...)
# ConfigSizingError: 3 AUTO fields on HeatDistributionConfig ('HeatDistributionSystem'):
#   heating_system                          <- Size.HDS_TYPE
#   water_mass_flow_rate_in_kg_per_second   <- Size.WATER_MASS_FLOW.rounded(2)
#   absolute_conditioned_floor_area_in_m2   <- Size.FLOOR_AREA
# Call .resolve(ctx) or assign concrete values.
```

Note how far the ceremony dropped: because `sized_field` defaults to `AUTO`, the
"standard" preset is nothing but an identity — the class declaration *is* the preset.

### 4.4 `EMSConfig`

No field is declared `sized_field`, so nothing can say `AUTO`; `resolve(ctx)` on it
raises `NothingToSizeError` (consistent with the other designs: a setup that believes it
sized the EMS fails loudly instead of no-opping).

### 4.5 The deep win: sizing stays serializable — and now readable

Because `AUTO` is a value, it survives `to_dict()`, and the JSON needs **no rule
mini-language at all** — the laws live in Python at the field declarations:

```json
"config_preset": "condensing_gas",
"config": {"maximal_thermal_power_in_watt": "AUTO",
           "eff_th_max": 0.92}
```

A v2 scenario file with `"AUTO"` fields is a **building-independent template** that the
executor resolves against whatever building the file declares — unifying the
building-sizer's core idea with the scenario format, reviewable by anyone. "Auto-size
this" in the GUI is a checkbox that writes the string `AUTO`. The audit trail is
inherent: the resolver records `field ← law(context inputs) = value` per resolved field,
and the audit artifact always contains the fully resolved config.

Generator policy (revised, see §8.1): the template creator emits `"AUTO"` for fields
whose ``sizing_record`` shows an unmodified law-resolved value; the fully realized dump
moves to the result side (§8.3) as the audit and re-creation record.

**Strengths:** most scalable and most v2-spirited (declarative where it matters,
serializable, deterministic, hard-error validation); "required" is per-field and
self-describing; presets are pure `name = value` data; laws are visible at the field
declaration instead of buried in factory bodies; template scenarios unlock a real
product capability; the field union is the trivial `Sizable[T] = T | _AutoSize`
(one well-known sentinel, the same order of nuisance as `Optional`) instead of an
open-ended rule type.
**Weaknesses:** per-preset law variation is not the default — all presets of a class
share one law per field, and a preset needing a different one assigns a `SizingLaw` as
the field value (the spike found this is *not* hypothetical: the pellet and wood-chip
boilers' minimal power is a twelfth of the sized maximum while gas/oil use zero, so the
escape hatch is implemented, not sketched); laws whose input is a
*sibling component* rather than the building (the boiler controller's power band) still
need that fact lifted onto `SizingContext`; the `Sizable` union must not leak past
construction (the central `Component.__init__` check enforces it, but mypy sees the
union until then); this is a v2-spec-level decision, not just a factory sweep.

---

## 5. Design C — defaults are a *device catalog*; scaling is *selection*

**Paradigm:** ask what a "default config" *is*, physically. Almost always **a real
device**: a 12 kW condensing boiler, a pvlib module, a bslib battery model, an hplib heat
pump. The codebase already half-lives this way — batteries and heat pumps are picked by
model name from shipped databases, and `EmissionFactorsAndCostsForDevicesConfig` is a
per-device cost/emission database. The linearly-scaled fictional 13.7 kW boiler is the
outlier; a real building gets the **next-larger real device**. So: move default data out
of the config classes into `hisim/catalog/`, with provenance, and make scaling a query.

### 5.1 Catalog entries

```python
@dataclass(frozen=True)
class CatalogEntry(Generic[TConfig]):
    key: str                       # stable reference, e.g. "boiler.condensing_gas_12kw"
    build: Callable[[], TConfig]   # fresh instance per call (R6)
    source: str                    # provenance: literature / manufacturer / dataset
    year: int
    tags: frozenset[str] = frozenset()

    def config(self) -> TConfig: ...
```

### 5.2 `hisim/catalog/boilers.py`

```python
CONDENSING_GAS_12KW = CatalogEntry(
    key="boiler.condensing_gas_12kw",
    build=lambda: GenericBoilerConfig(
        component_id=ComponentID("CondensingGasBoiler"),
        boiler_type=BoilerType.CONDENSING, energy_carrier=lt.LoadTypes.GAS,
        temperature_delta_in_celsius=20,
        minimal_thermal_power_in_watt=1_000, maximal_thermal_power_in_watt=12_000,
        eff_th_min=0.60, eff_th_max=0.90, ...capex_none...),
    source="BDEW Heizkostenvergleich 2021, table 4", year=2021,
    tags={"gas", "condensing"})

CONDENSING_GAS_20KW = CatalogEntry(..., maximal_thermal_power_in_watt=20_000, ...)
CONDENSING_GAS_35KW = CatalogEntry(...)
OIL_18KW = CatalogEntry(...)
# one module per component family; the whole default dataset reviewable in one place

def select(*, energy_carrier: lt.LoadTypes,
           min_power_watt: float,
           condensing: Optional[bool] = None) -> CatalogEntry[GenericBoilerConfig]:
    """Smallest catalog device covering min_power_watt; hard error listing the nearest
    candidates when nothing covers it (no silent extrapolation)."""
```

```python
ctx = SizingContext.for_building(my_building_config)

my_boiler_config = catalog.boilers.select(
    energy_carrier=lt.LoadTypes.OIL, min_power_watt=ctx.heating_load_in_watt).config()

my_boiler_config = catalog.boilers.CONDENSING_GAS_12KW.config()      # named default
# real installation: pick the actual device class, adjust the plate value if needed
```

Note the semantic shift: the household gets a real 15 kW oil boiler for a 13.7 kW load —
**discrete, physically honest sizing** with the device's real efficiency curve, instead
of a fictional interpolated device.

### 5.3 The non-device corner: `HeatDistributionConfig` and `EMSConfig`

This is design C's honest weakness. A heat distribution system is *sized to order*, not
picked from a catalog, and the EMS is pure software parameters. They would live in a
`catalog/settings.py` corner as parameter presets — at which point C needs a second
mechanism that looks like a thin version of design A or B for exactly these classes:

```python
# catalog/settings.py — parameter presets, not devices; provenance still applies
EMS_OPTIMIZE_OWN_CONSUMPTION = CatalogEntry(
    key="ems.optimize_own_consumption",
    build=lambda: EMSConfig(..., strategy="optimize_own_consumption", ...),
    source="HiSim project convention", year=2022)

# HDS: 'select' computes rather than looks up — a builder in catalog clothing
def select_heat_distribution(ctx: SizingContext) -> CatalogEntry[HeatDistributionConfig]: ...
```

**Strengths:** one reviewable, provenance-carrying home for all default *data* (a domain
scientist can audit "where does eff 0.60 come from?" without reading component code);
natural merge with the existing cost database and the GUI palette; selection over real
devices is often the more defensible science.
**Weaknesses:** does not cover non-device components without a bolted-on preset corner;
continuous scaling is sometimes genuinely wanted (storage volumes, building envelope);
organizational inversion — config classes stop owning their defaults, changing where
component authors work; largest data-curation effort (real device lists per family).

---

## 6. Comparison against the requirements

| | A: preset instances + `sized()` | B (revised): field laws + `AUTO` | C: device catalog + `select()` |
|---|---|---|---|
| R1 named variants, autocomplete | ✅ `presets.oil` | ✅ same `Catalog` helper | ✅ `catalog.boilers.OIL_18KW` |
| R2 integrated sizing | ✅ one method, twins collapse | ✅ dissolves the distinction entirely | ✅ but *discrete* (semantic change) |
| R3 unsized-by-choice + audit | ✅ sentinel + breadcrumb | ✅ per-field, self-describing | ✅ named entry is inherently unsized |
| R4 enumerable, canonical | ✅ | ✅ | ✅ + provenance metadata |
| R5 systematic test variation | ✅ presets × {as-is, sized} | ✅ presets × {as-is, resolved} | ⚠️ devices enumerable; `select` needs sampled contexts |
| R6 fresh instances | ✅ Catalog copies | ✅ | ✅ |
| R7 ceremony per class | **best** (helper + 1 method) | close second (laws at fields, presets pure `name = value`, `AUTO` defaults shrink presets) | good for devices, second mechanism for the rest |
| R8 computed values | ✅ plain Python in `sized()` | ✅ law = expression or plain function of ctx | ✅ inside `select`/builders |
| readability at use sites | ✅ | ✅ `name = value` / `name = AUTO` | ✅ |
| serializable sizing (GUI, templates) | ❌ imperative | **✅ `"AUTO"` — no rule language in files** | ⚠️ selection query could be serialized |
| conceptual blast radius | smallest | large (v2 spec decision) | medium (data curation, repo layout) |

## 7. Composition path (the designs are not mutually exclusive)

They layer naturally, which suggests a staged decision rather than a winner-takes-all:

1. **A now** — deletes the discovery regex, the twin factories and the ranking with the
   least mechanism; everything after builds on its `Catalog` shape.
2. **B later, per field, as a v2 spec decision** — the fields that want serializable
   sizing (template scenarios, GUI auto-size) become `sized_field`s accepting `AUTO`;
   `sized()` methods become thin wrappers or retire per class.
3. **C as the long-term home for the data** — preset *entries* migrate from config
   modules into the catalog when provenance and the cost database merge; the `Catalog`
   attribute on the config class can simply point into it, so call sites never change.

## 8. Interaction with the JSON scenario files

Today (and in the v2 draft) a scenario entry carries the **full config dump** — every
field, concrete. The presets open a second option: **referencing a named default** and
storing only the differences. These are two different contracts, and the choice is
independent of which design wins, though each design colors it differently.

### 8.1 The two contracts

**(a) Full dump (status quo).** The file is self-contained: bit-for-bit reproducible
regardless of HiSim version, no hidden inputs, diffs show every effective value. Presets
are then an *authoring-side* convenience only — the generator records what the preset
produced, and the executor never sees preset names. Cost: 400-line files nobody can
review semantically ("is this boiler just the oil preset, or was something tweaked?").

**(b) Preset reference + sparse overrides.** The entry names the preset and lists only
deviations:

```json
{
    "component_id": {"name": "Boiler"},
    "class": "hisim.components.generic_boiler.GenericBoiler",
    "config_preset": "oil",
    "config": {"maximal_thermal_power_in_watt": 18000.0}
}
```

Executor semantics: build the named preset, apply the `config` block as field overrides,
then (design-dependent) size. Unknown preset name or unknown override field → hard error,
consistent with the v2 error philosophy. The file becomes reviewable *intent* ("the oil
preset, but 18 kW") instead of a value dump.

The cost of (b) is **version coupling**: the same file wires different numbers when a
HiSim release changes the preset data. This is exactly the situation the v2 spec already
accepted for default *connections* (§5 audit artifact: "defaults are a dependency like
component physics") — and the same remedy applies: the executor always writes the **fully
resolved config dump into the audit artifact**, and the golden parity gate makes any
preset-data drift visible and reviewable. Reproducibility concerns are then answered by
"pin the HiSim version + keep the audit artifact", not by bloating the input file.

**Decided 2026-08-19: contract (b) is wanted in the final product.** The executor
accepts **both** — `config` alone (full dump, migration-compatible), or `config_preset`
plus sparse `config`. Direct consequence: **preset names are wire format** — they must be
chosen with the care of API names in this sweep, and renaming one later is a breaking
change requiring a migration note.

**Revised 2026-08-19 (after the spike): the generator emits templates.** The first
version of this decision kept the generator on full dumps; the intended workflow inverts
that:

```
python setup ──template creator──▶ scenario JSON with "AUTO"/preset refs   (shared file)
                                        │ executor resolves at build
                                        ▼
                                    simulation
                                        │ audit dump (§8.3)
                                        ▼
                          fully realized JSON (audit & re-creation record)
```

The *input* file states intent (presets, `"AUTO"`, sparse overrides — reviewable,
building-independent); the *output* record states facts (every value concrete —
reproducible regardless of preset drift or HiSim version). Nothing in between is
ambiguous, and the two files answer different questions by design. The
resolved-full-dump role moves entirely to the result side (§8.3); committed scenario
JSONs become templates once the template creator exists.

The template creator is cheap because of the ``sizing_record`` provenance (§4.1): a
resolved config remembers which fields a law computed and to which value, so the
generator **de-resolves at dump time** — a field whose current value still equals its
recorded value is written as ``"AUTO"``; a field the author overwrote after resolving
keeps its concrete number (exactly the right semantics for the manually-adjusted
real-installation case). The v2 recording API (D-track) achieves the same even more
directly, by recording ``presets.X.resolve(ctx)`` as intent.

### 8.2 What each design adds on top

- **Design A:** exactly the above — `config_preset` is the `Catalog` entry name. Sizing
  is *not* expressible in the file (it happened in Python before generation), so a JSON
  scenario is always concrete; the sized/nominal provenance lands in the audit artifact
  only.
- **Design B (revised):** the file format gets strictly richer at zero readability cost,
  because a sparse entry can keep a field declarative with the single token `"AUTO"` —
  the law itself stays in Python at the field declaration:

  ```json
  "config_preset": "condensing_gas",
  "config": {"maximal_thermal_power_in_watt": "AUTO",
             "eff_th_max": 0.92}
  ```

  The executor resolves `AUTO` against the scenario's building at build time — scenario
  files become **building-independent templates**, and "auto-size" is a property of the
  file, not of the Python setup that once generated it. This is the only design where
  the JSON answers R2/R3 by itself. The template creator emits `"AUTO"`; the fully
  realized record is the result-side audit dump (§8.1 revised, §8.3).
- **Design C:** the reference is a **catalog key** (`"config_ref": "boiler.oil_18kw"`),
  which is stronger than a preset name: it carries provenance (source, year) and is
  shared with the cost database, so the webtool can show "this scenario uses the BDEW
  2021 oil boiler". Discrete `select` queries could also be serialized
  (`{"select": {"energy_carrier": "OIL", "min_power": "HEATING_LOAD"}}`), which
  converges with design B's rules from the other direction.

### 8.3 Result-side dump: the resolved scenario as a reproduction artifact

The counterpart to accepting templates on the input side (decided in §8.1): at the end
of simulator setup, the framework writes every component's *effective* config — after
preset construction, after `resolve(ctx)`, after any manual field writes — as a **full
contract-(a) scenario JSON into the results directory**. Half the machinery exists
(`write_json_for_initialized_simulator` dumps a scenario from a live simulator today),
and under revised design B the dump is fully realized by construction:
`Component.__init__` guarantees no `AUTO` survives into a running simulation, so the
components' state *is* the resolved scenario.

This gives the format a clean symmetry — **template in, record out**: the input may say
`"config_preset": "condensing_gas"` with `"maximal_thermal_power_in_watt": "AUTO"`; the
results directory contains the concrete file with `18432.0`, re-executable as-is and
immune to preset-data drift, HiSim version changes or a different building, because
nothing in it is referential anymore. It also captures manual post-preset field writes
(the real-installation case of R3), which no input-side artifact can.

Boundaries, so this is not oversold:

- **The config dump alone does not reproduce a run.** Reproduction is the tuple:
  resolved scenario JSON + the `.simulation.json` + the HiSim version + input-data
  state. (The v2 spike measured 3.7e-9 result deviations between cold and warm
  weather/LPG caches — see `roadmap/json_v2_spike_findings.md` §1.2.) The dump embeds
  the HiSim version and git commit in its metadata so the tuple is self-documenting.
- **Paths are symbolized** (`${inputs}/...`, v2 spec §3.5); otherwise the file only
  reproduces on the machine that wrote it.
- **Provenance stays out of the dump.** Annotating fields with `← law(inputs)` would
  break re-executability; the resolved file remains a plain, valid scenario. The
  per-field why comes from the `sizing_record` attribute that `resolve()` leaves on the
  config (§4.1) and goes into the audit artifact written next to the dump.

### 8.4 Executor-side sizing: building the context from the file (design sketch)

Resolving a template requires the executor to construct the :class:`SizingContext` from
the scenario itself. Building-derived facts are straightforward — the file contains the
building entry, so ``SizingContext.for_building(...)`` right after the Building is
constructed. The hard case, surfaced by the spike, is **sibling-derived facts** (open
question 3): the heat distribution system's water mass flow comes from the *heat
distribution controller's* information object, not from the building.

**The incumbent mechanism and why it is not kept.** Cross-component sizing is solved
today by the ``SingletonSimRepository`` (``hisim/sim_repository_singleton.py``): a global
singleton mapping ``SingletonDictKeyEnum`` keys to untyped values, written at
construction time by whoever constructs first and read by whoever constructs later.
Auditing it (2026-08-19) shows the pattern has already failed silently in production:
the sizing keys — ``NUMBEROFAPARTMENTS``, ``MAXTHERMALBUILDINGDEMAND``,
``WATERMASSFLOWRATEOFHEATGENERATOR``, the storage set-temperatures — are **dead**:
declared, read (``simple_water_storage`` falls back to ``None`` via ``entry_exists``),
but written by nobody since their writers were obsoleted. Every property of the design
invites exactly that: untyped ``Any`` values, an open-ended flat key enum, implicit
construction-order coupling, silent ``entry_exists`` fallbacks instead of errors, and
global mutable state that bleeds between tests. The context-contributor mechanism below
is the typed, hard-erroring successor for the **construction-time sizing half** of the
repository's job; the singleton's *runtime* half — per-timestep forecast exchange for
the predictive controllers (heat-flux, weather, price forecasts) — is a separate concern
and explicitly out of scope here (it deserves its own redesign decision later; proper
component wiring is the likely answer).

Mechanism (revised 2026-08-19, after review): **declared facts, resolved to a fixed
point over the configs — before any component is constructed.** Cross-component sizing
dependencies nest deeply (building → HDS controller → HDS; building → boiler → boiler
controller → storage), so the resolution is a small dependency engine over *config
objects only* — components are never needed to compute sizing facts (the building facts
come from ``BuildingInformation(config)``, the water mass flow from the HDS controller's
information class, both config-level computations). Three phases:

1. **Registration.** Every config in the file declares its sizing *inputs* (expression
   laws name their ``facts_read`` automatically; for function laws the ``reads=[...]``
   declaration is **mandatory**, enforced at declaration time — an undeclared function
   law makes the graph a guess, which would silently degrade the precise phase-2 errors
   back into "no progress" mysteries) and its *outputs* — the facts it contributes, via
   ``contribute_sizing_facts(config, ctx)``. **Output names are static per config
   class** (decided 2026-08-19): the *values* are computed later and may legitimately be
   ``None`` when a feature is off (a boiler without DHW contributes its DHW power fact
   as null), but the *set* of fact names never depends on resolution — otherwise the
   graph validation would validate fiction. A consumer reading a null fact is a hard
   error attributed as "provided as null by X", distinct from "provided by nobody".
2. **Validation of the graph.** Two hard errors, both detectable up front and named
   precisely: a fact nobody provides ("no config contributes 'water_mass_flow_rate...',
   needed by HeatDistributionConfig.water_mass_flow_rate..."), and a dependency cycle
   (naming its members). No silent fallbacks — the exact opposite of the singleton's
   ``entry_exists``-else-None pattern.
3. **Resolution to the fixed point.** Iterate over the unresolved configs, resolving
   every one whose declared inputs are all available and folding its contributions into
   the context, until none remain (the same shape as the simulator's own convergence
   loop; a topological order over the phase-1 graph is the deterministic equivalent and
   is what the audit record reports as resolution order). Only then are components
   constructed, from fully concrete configs — the central ``Component.__init__`` check
   stays the last line of defense.

Consequences: sizing no longer depends on file order at all (file order remains
load-bearing only for ``build_from_scenario`` construction, v2 spec §4.2); deep chains
need no manual ordering by the author; and the engine is equally usable by the executor
(templates), by Python setups (one call: resolve-all against a setup-built context) and
by the contract test.

**Fact scoping (decided 2026-08-19, refined 2026-08-20): sibling facts resolve along
the connection graph, with the flat pool as fallback.** "The boiler controller's power
band comes from *the boiler it is connected to*" — the scenario's ``connections`` list
already contains the disambiguation, and the parsed connections are available before
construction, exactly when the engine runs. A consumer's sibling fact is looked up as a
**hybrid**: direct adjacency first (this is what keeps two boilers in one scenario
unambiguous), and when *no direct neighbor declares* the fact, the lookup falls back to
the flat-pool rule that governs the no-adjacency mode — exactly one declared provider
in the whole pool wins, two or more are a hard error naming the providers and the
consumer. The with-adjacency behavior is thus a strict refinement of the
without-adjacency behavior rather than a different rule; the refinement exists because
real templates need it: the battery sizes from the PV peak power, yet battery and PV
are two wiring hops apart (both connect only to the EMS), so a purely adjacency-scoped
lookup would starve. Providership is decided from the *declared* ``CONNECTED``
contributions, never from whichever values happen to be computed when the consumer is
visited, so the outcome — including every ambiguity error — is independent of
resolution order. Scope-global facts (building physics from ``BuildingConfig``) stay in
the flat per-scope pool. Anything unclear **fails hard**: a fact nobody provides, a
fact with two remaining candidate sources (two declared providers, or a declared
provider next to a seeded global value), or a fact contributed identically into the
flat pool twice — each names the components involved. (This is what makes two batteries
or two CHPs in one scenario — which ``dynamic_components.py`` has today — safe from day
one.)

Two smaller rules, decided with the above: Python setups converge on the **same
engine** — build all configs unresolved, one ``resolve_all(configs, seed_ctx)`` call,
then construct — so the JSON-template path and the setup path are structurally
identical rather than parity-tested into agreement (per-config ``.resolve`` remains for
tests and one-off scripts). And **cycles stay hard errors**: if genuinely iterative
sizing ever appears (storage ↔ boiler with loss feedback), the answer is an explicit
iterative law owning its convergence, not loosening the engine into value iteration.
As an extension, a template may pre-seed facts (``"sizing_facts": {...}`` at file
level); pre-seeded facts win over contributions, loudly, in the audit record.

This keeps sizing laws reading only the context (no sibling-component lookups inside
laws) and turns question 3's "who populates sibling facts" into a declared, testable
per-config hook. Compared to the singleton it replaces: facts are typed dataclass fields
instead of ``Any`` behind enum keys, contribution is declared per config class instead
of a hidden ``set_entry`` in a constructor, a missing fact is a hard error naming field
and fact instead of a silent fallback, a double contribution is a hard error instead of
last-writer-wins, and there is no global state — the context is threaded, so tests
cannot bleed into each other.

### 8.5 Consequence for the v2 migration plan

Nothing in the current v2 phases blocks on this: phase 4 regenerates the 23 scenario
JSONs as full dumps either way. Preset references would enter as a *later, additive*
schema extension (a `config_preset`/`config_ref` key the executor understands), with its
own spec decision — worth deciding *direction* now so the preset names chosen in this
sweep are stable enough to become wire format.

## 8b. Implementation plan (decided 2026-08-19): two PRs

Design B (revised) is being implemented, in exactly two PRs against main:

1. **Spike PR** — the machinery and proof: `hisim/sizing.py` (`AUTO`, `Sizable`,
   `sized_field`, `Size.*` expression terms, `SizingContext`, `resolve`), the `Catalog`
   preset helper, the central `Component.__init__` check, the
   `BuildingConfig`/`BuildingInformation` split into
   `hisim/components/building_config.py`, and the three worked-example components
   (`GenericBoilerConfig`, `HeatDistributionConfig`, `EMSConfig`) fully converted as
   pilots, with the old factories of those three deleted and their call sites moved.
   This PR also carries this spec file onto main and **settles the preset naming
   convention** (question 5) — preset names are wire format per §8.1, so the names
   chosen here are API.

   > **As built (2026-08-21): two stacked PRs instead of one.** The mechanical half
   > became its own PR — the `hisim/config/` package with `base.py` and the repo-wide
   > clean-import sweep, provable by the untouched golden suites alone — and the design-B
   > half (machinery, building payload, three pilots, this spec) stacks on top of it. The
   > building split is not part of either: it landed earlier as the
   > `hisim/components/building/` package cleanup. Content is as described above.
2. **Sweep PR** — the remaining ~70 config classes converted in one go: presets replace
   the ~100 remaining factories, setups and tests move to `presets.X` / `resolve(ctx)`,
   the `Component[TConfig]` generics ride along (per the sweep-6 deferral in
   `roadmap/json_cleanup.md`), and the mypy escape hatches fall. Numerically neutral by
   requirement: presets must reproduce the old factory values exactly, enforced by the
   golden gates and scenario-JSON freshness.

## 9. Questions for the team

1. ~~Which paradigm~~ **Decided 2026-08-19: design B revised** (see §8b for the
   implementation plan). Design A remains recorded as the fallback that was not taken.
2. Design A policy (only if A): does base `sized()` raise (`NothingToSizeError`, no
   silent no-ops) or return an unchanged copy (friendlier for size-everything loops)?
3. Design B: sizing laws read only `SizingContext`. Facts from *sibling components*
   must be lifted onto the context — §8.4 proposes the answer (context contributors
   riding the executor's file-order construction); confirm or amend that sketch.
4. Design C, if chosen: is discrete device selection acceptable as the *default* sizing
   semantic (results will change vs. today's linear scaling — golden regeneration), or
   selection only where real databases exist?
5. Independent of design: do we normalize the preset *names* (today's 64 spellings) in
   the same sweep or separately? Note this question gained weight with the §8.1
   decision — preset names are wire format, so they should be settled once.
6. ~~JSON contract~~ **Decided 2026-08-19 (§8.1):** contract (b) — preset reference +
   sparse overrides — is part of the final product; the executor accepts both contracts,
   the generator stays on full dumps until explicitly flipped, and preset names are
   wire format.
