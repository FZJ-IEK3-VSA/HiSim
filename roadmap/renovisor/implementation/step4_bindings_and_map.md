# Step 4 — the bindings table and the translation map

**Status:** implementation specification, 2026-09-15
**Builds on:** step 3 (`step3_pure_layers.md`): `vocabulary`, `catalogue`, `options`, `effects`,
`reasons`, `report`, `materials`, `envelope`, `inventory`, `base_files`, `registry`, `application`.
**Decisions:** `challenges.md` §9 — C3 (HiSim spelling; a binding names a component, not a field
mapping), Q18 (recorded grouped files are the base files; recorded component keys), V1–V3 (the
generated HTML map, decision ids from registry docstrings, display names beside HiSim ids).
**Out of scope:** the parametriser (writing overrides into a file), law resolution, the trace page,
the `calculate` entry point — step 5.

Ground rules are those of step 3 §0 (worktree, interpreter, no commits, class-scope constants,
plain complete docstrings, flake8 + mypy clean, `@pytest.mark.base`).

---

## 1. `bindings.py` — which base-file component owns which inventory block

Under C3 a contract field name equals the config field name, so a binding says only *where* an
inventory value lands, never how it is renamed. The table is block-level with explicit exceptions.

```python
class BindingKind(str, Enum):
    CONFIG_OVERRIDE      # inventory leaf -> components.<key>.config.<same leaf name>
    CONSTRUCTOR_ARGUMENT # inventory leaf(s) -> components.<key>.constructor.<constructor>.<argument>
    NON_SIMULATION       # legitimate input, no physics consumer (cost / scheduling / grants)
    NO_CONSUMER          # nothing consumes it; reported `ignored`

@dataclass(frozen=True)
class BlockBinding:
    inventory_block: str        # dotted prefix, e.g. "building_config.envelope_details"
    component_key: str          # recorded key, e.g. "Building"; or GeneratorKey.SELECTED for the per-file generator
    requires_variant: Optional[Tuple[str, str]] = None   # e.g. ("electricity_management", "ems_with_battery")

@dataclass(frozen=True)
class LeafBinding:
    inventory_path: str
    kind: BindingKind
    targets: Tuple[Target, ...]   # Target(component_key, field_or_argument, constructor: Optional[str])
    note: str                     # why this leaf is an exception to its block rule

class Bindings:
    BLOCKS: ClassVar[Tuple[BlockBinding, ...]] = (
        BlockBinding("building_config.envelope_details", "Building"),
        BlockBinding("building_config.general", "Building"),
        BlockBinding("energy_system_config.photovoltaics", "PVSystem"),
        BlockBinding("energy_system_config.battery_storage", "Battery", ("electricity_management", "ems_with_battery")),
        BlockBinding("energy_system_config.water_storage.hot_water_storage", "SimpleHotWaterStorage"),
        BlockBinding("energy_system_config.water_storage.domestic_hot_water_storage", "DHWStorage"),
        BlockBinding("energy_system_config.solar_thermal_system", "SolarThermalSystem"),
        BlockBinding("energy_system_config.heating_system", GeneratorKey.SELECTED),
        BlockBinding("occupancy_config", "UTSPConnector"),
        BlockBinding("location", "Weather"),
    )
    LEAVES: ClassVar[Tuple[LeafBinding, ...]] = (
        # constructor arguments
        LeafBinding("building_config.general.tabula_building_type", CONSTRUCTOR_ARGUMENT,
                    (Target("Building", "building_code", "for_tabula_code"),), "part of the TABULA code"),
        LeafBinding("building_config.general.construction_year", CONSTRUCTOR_ARGUMENT, (same target,), "TABULA age band"),
        LeafBinding("building_config.general.retrofit_status", CONSTRUCTOR_ARGUMENT, (same target,), "TABULA variant .001/.002/.003"),
        LeafBinding("building_config.general.conditioned_floor_area_m2", CONSTRUCTOR_ARGUMENT,
                    (Target("Building", "absolute_conditioned_floor_area_in_m2", "for_tabula_code"),),
                    "contract name predates C3; the contract PR renames it"),
        LeafBinding("location.country_code", CONSTRUCTOR_ARGUMENT, (Target("Weather", "location", "for_location"),
                    Target("PVSystem", "location", None)), "weather station and the PV location field"),
        LeafBinding("occupancy_config.residents_count", CONSTRUCTOR_ARGUMENT, (Target("UTSPConnector", "household", "for_household"),),
                    "with residents_type and employment: nearest of the LPG catalogue households (A3)"),
        LeafBinding("occupancy_config.residents_type", ...same...), LeafBinding("occupancy_config.residents_employment_status", ...same...),
        LeafBinding("occupancy_config.travel_route_set", CONSTRUCTOR_ARGUMENT, (Target("UTSPConnector", "travel_route_set", "for_household"),), ""),
        # two-target override
        LeafBinding("building_config.general.set_heating_temperature_in_celsius", CONFIG_OVERRIDE,
                    (Target("Building", "set_heating_temperature_in_celsius", None),
                     Target("HeatDistributionController", "set_heating_temperature_for_building_in_celsius", None)), "M11"),
        LeafBinding("building_config.general.set_cooling_temperature_in_celsius", CONFIG_OVERRIDE,
                    (Target("Building", ..., None), Target("HeatDistributionController", "set_cooling_temperature_for_building_in_celsius", None)), "M11"),
        # heat distribution lives on the controller, not the generator
        LeafBinding("energy_system_config.heating_system.heat_distribution_system", CONFIG_OVERRIDE,
                    (Target("HeatDistributionController", "heating_system", None),), "controller field"),
        LeafBinding("energy_system_config.heating_system.system", CONSTRUCTOR_ARGUMENT, (), "selects the base file (base_files.py), no field"),
        LeafBinding("energy_system_config.heating_system.dhw_supply", CONSTRUCTOR_ARGUMENT, (), "selects the base file"),
        LeafBinding("energy_system_config.heating_system.heatpump_flow_temperature_in_celsius", CONFIG_OVERRIDE,
                    (Target("MoreAdvancedHeatPumpHPLib", "flow_temperature_in_celsius", None),), "heat-pump files only"),
        # non-simulation and no-consumer leaves (field_inventory.md rows 2, 3, 30, 34, 36, 40, 41, 43-45, 51-52, 55-56, 58, 60-79)
        LeafBinding("location.region", NO_CONSUMER, (), "one weather station per country"),
        LeafBinding("location.eircode_or_postcode", NO_CONSUMER, (), ""),
        LeafBinding("*.installation_year", NON_SIMULATION, (), "age-based condition, cost layer"),
        LeafBinding("energy_system_config.photovoltaics.remaining_performance_in_percent", NO_CONSUMER, (), "no degradation model"),
        LeafBinding("energy_system_config.battery_storage.power_in_watt", NO_CONSUMER, (), "only the inverter power exists"),
        LeafBinding("energy_system_config.battery_storage.state_of_health_in_percent", NO_CONSUMER, (), "no fade model"),
        LeafBinding("energy_system_config.vehicles", NON_SIMULATION, (), "vehicles select the base file (Q18); per-vehicle fields are step 5"),
        LeafBinding("condition_assessment", NON_SIMULATION, (), "scheduling layer"),
        LeafBinding("selected_grant_schemes", NON_SIMULATION, (), "subsidy layer (Q24)"),
    )
    def resolve(cls, inventory_path: str) -> LeafBinding   # leaf rule wins over block rule; a path under no rule raises BindingError
```

`GeneratorKey.SELECTED` is a sentinel resolved per base file: the component whose class module is
one of the generator modules (`generic_boiler`, `more_advanced_heat_pump_hplib`,
`generic_electric_heating`, `generic_district_heating`); exactly one such top-level component
must exist per file, asserted by the test. Wildcard leaf `*.installation_year` matches any block.

Contract field names that do not yet follow C3 (`conditioned_floor_area_m2`, `capacity_in_kwh`,
`collector_area_in_m2`, `azimuth_in_degree`, `tilt_in_degree`, `with_dhw_preparation`,
`power_in_watt` on the heating system) get an explicit `LeafBinding` with the current HiSim field
name and the note "renamed by the contract PR / by P4"; `PendingRenames` (class attribute) lists
them so step 6 can empty it. **No value maps anywhere** (C3).

### The check (§7.5 check 5)

`tests/test_renovisor_bindings.py`: for every file in `BaseFiles.BY_KEY`, load it with
`hisim.energy_system.loader.load_energy_system`, and for every `Target`:
- the component key exists at top level, or inside the variant option `requires_variant` names
  (variants' options carry their own `components`);
- `hisim.energy_system.classes.ClassBinder.config_class_of(...)` yields the config class, and
  `hisim.config.introspection.describe_config(config_class)` lists the target field (for
  `CONFIG_OVERRIDE`) or a constructor of that name with that parameter (for `CONSTRUCTOR_ARGUMENT`);
- for `GeneratorKey.SELECTED`, exactly one generator component per file.
A second test walks every leaf of the `HomeInventoryInput` schema (from `ContractFiles.openapi()`)
plus `PendingContractPaths` and asserts `Bindings.resolve` answers for each: every contract field
has a stated fate (R7, A1). Both tests are `@pytest.mark.base`; loading eleven YAML files and
importing their classes takes seconds, not minutes — if it is slower, cache the loaded files per
session.

## 2. `map.py` — the generated translation map (V1–V3)

`python -m hisim.renovisor.map [--out roadmap/renovisor/translation_map.html]` writes one
self-contained HTML file. No external scripts, styles or fonts; inline CSS and a small inline
script for highlighting. Deterministic: no timestamps; the header shows `TRANSLATOR_VERSION`, the
contract commit and hash from `PINNED.yaml`, and the count of measures per status.

### Data collection (`MapData.collect()`)

For every measure of the catalogue: for every combination of enum option values (product over
enum options; integer options take the spec's listed values or one representative value, Experts
defaults applied), run the registry function with a representative inventory
(`tests/renovisor/example_inventory_ie_1988_detached.json` from step 3) and collect the effects and
report lines **without** resolving (no TABULA, no composition). Then classify each (measure,
value combination):

| Status | When | Colour role |
|---|---|---|
| `SIMULATED` | every effect is a write, switch or law request that step 5 can apply | green |
| `LAW_PENDING` | contains a `LawRequest` | teal |
| `BLOCKED_ON_DATA` | a `Refusal` with `MATERIAL_NOT_IN_DATABASE` or `UNDEFINED_MEASURE_BUILDUP` | amber |
| `REFUSED` | any other `Refusal` (`UNSUPPORTED_SYSTEM`, `NO_BASE_FILE_FOR_COMBINATION`, `TOO_MANY_VEHICLES`) | red |
| `NO_EFFECT` | only `NoEffect` | grey |

Each effect is joined onward: `SetInventoryField` / composed U-value → inventory path →
`Bindings.resolve(path)` → targets (component key, field); `SelectVariant`/`EnableGroup`/
`SelectBaseFile` → the switch column with the file name from `BaseFiles`; `NoEffect`/`Refusal` →
the reason code and its description. The decision ids come from the registry function's docstring
`Decisions:` line (V2); the display names for measures, options and values from the catalogue specs
(V3), shown beside the ids in a lighter weight.

### Rendering

- **Header:** title, version and contract line, the legend, status counts, and a decision filter:
  a row of toggle chips, one per decision id found in any docstring; toggling one dims every row
  whose docstring does not carry it.
- **Map tab (default):** one section per catalogue category, in catalogue order. Inside, one row
  per measure with sub-rows per value combination when their status or effects differ (collapse
  identical ones and show the value list). Columns: measure (id, display name) · options (id,
  value, display value, `defaulted` marker with source on hover) · effect (type and payload in one
  line: `+R FACADE polystyrene_eps_rigid_board 140 mm`, `U WINDOW …`, `set …path… = …`,
  `variant electricity_management=ems_with_battery`, `base file household_heatpump_…`,
  `no effect: NO_APPLIANCE_SUBMODEL`, `refuse: MATERIAL_NOT_IN_DATABASE`) · inventory path ·
  HiSim target (`Building.roof_u_value_in_watt_per_m2_per_kelvin`, `constructor for_tabula_code`,
  or the file name) · status. Hovering a row highlights every cell of the same measure across
  sub-rows and dims the rest; clicking pins the highlight.
  (Note 2026-09-24: contract PR #10 renamed the effect example's row `polystyrene_eps_rigid_board` to
  `eps_rigid_board`; the translator and the mockup use the new id.)
- **Elements tab:** matrix measures × `ThermalElement` marking which measures add resistance or
  set a U-value on which element, with the exclusivity groups outlined so contradictory pairs are
  visible.
- **Switches tab:** matrix measures × {base file, `electricity_management`, groups} showing which
  measures select what, and the `BaseFiles.BY_KEY` table with its refusal gaps.
- **Trace tab:** a placeholder paragraph "available after step 5 (parametriser)".
- Works at 1200 px and wider; horizontal scroll inside the map table only; system font stack;
  colours legible in light and dark schemes via `prefers-color-scheme`.

### Freshness test

`tests/test_renovisor_map.py`: `MapData.collect()` runs and classifies all 33 measures; the
rendered HTML equals the committed `roadmap/renovisor/translation_map.html` byte for byte (so a
registry, catalogue or bindings change without regenerating the map fails the build, the same
discipline as the energy-system JSON schema); the HTML contains no `http://` or `https://` script or
link references.

## 3. Done means

Both new tests and every step-3 test green with the step-3 commands; flake8 and mypy clean; the
committed `translation_map.html` regenerated; the final report lists: every base file that lacks a
component some block binds to (expected: `SolarThermalSystem` only in the two solar-thermal files,
`Battery` only inside the `ems_with_battery` option), every `PendingRenames` entry, and the status
count per measure for the reviewer to sanity-check against `measures_v2_requirements.md` §4.1
(2 work today, 15 need the U-value derivation, 7 wait, 9 no model — the counts will differ now
that materials are blocked; report what they are).
