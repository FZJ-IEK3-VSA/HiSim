# Step 8 — reconciling the branch with the frontend side's translator spec v2

**Status:** implementation specification, 2026-09-19
**Governing documents, in order of authority:**
1. The owner's decisions in `roadmap/renovisor/challenges.md` §13 (2026-09-19) and, where §13 is
   silent, §9 and §12.
2. `~/contract-proposals/translator-implementation-spec.md` and `calculation-request.md` — the
   frontend side's spec ("the F-spec" below), read together with `calculation-request.schema.json`,
   `calculation-request.mockup-1.yaml`, `measure-capabilities.md` and
   `measure-capabilities.openapi.yaml`. Copies of these five files are vendored by this step (§1).
3. `roadmap/renovisor/review_translator_spec_v2_2026-09-19.md` — the review; where it and the
   F-spec differ, the decisions in §13 say which wins.
**Builds on:** steps 3–6 on `renovisor-backend` (HEAD `c02bc801`). This step reshapes them into the
F-spec's request, outputs and vocabulary; it does not rebuild them.

The decisions this step implements, in one line each:
- **D-A** the request carries U-values and material *properties*; HiSim derives no physics from
  ids and reads no catalogue except TABULA and the frozen catalogue table for validation.
- **D-B** the catalogue's lowercase snake_case values are the wire format and become the *values*
  of HiSim's vocabulary enums; energy-system files keep writing member names.
- **D-C** the MVP runs every household as `CHR01_Couple_both_at_Work` with
  `USE_PREDEFINED_PROFILE`; the LPG path stays in the code behind a flag.
- **D-D** the recorded grouped twins in `energy_systems/` are the only base files; what they lack
  is `not_implemented_yet`.
- **D-E** the F-spec's rule 6: a feature on `not_implemented_yet.yaml` is a note and the run
  proceeds; anything the translator cannot map and has not listed fails the translator's build
  (exit 3), never the request; invalid input (unknown key, unknown value, stage-0, out of range,
  unplaceable TABULA, unsupported country) is exit 2 with `problems.json`.
- **D-F** `result.py` stays the result payload; `COMPUTE_LIFECYCLE_COSTS` is added to the
  post-processing options.

Ground rules are those of step 3 §0 (worktree `/home/noah/hisim/HiSim-renovisor`, interpreter,
`PYTHONPATH`, no commits, class-scope constants, plain complete docstrings, flake8 + mypy clean,
`@pytest.mark.base` unless stated). One addition: **every enum value the request may carry is a
string the catalogue or the F-spec spells; invent none.**

---

## 1. Vendored contract: refresh and extend (`hisim/renovisor/contract/`)

- Refresh `measures.yaml` and `materials.yaml` from the contract repo's `origin/main` at
  `5181aa5` (the 2026-09-17 revision: `id:` written out, lowercase, `options: []`, 32 measures,
  the new `hot_water_tank_and_pipe_insulation`, `add internal dry lining` gone). `openapi.yaml`
  stays vendored but its `PINNED.yaml` entry gains `authoritative: false` and a note that it is
  superseded by the request schema. Extend `refresh.py` with a second source kind, a local file,
  and vendor `calculation-request.schema.json`, `calculation-request.mockup-1.yaml`,
  `measure-capabilities.openapi.yaml` from `~/contract-proposals/` with `source: contract-proposals
  2026-09-19` and their SHA-256 in `PINNED.yaml`. `ContractFiles` gains `request_schema()`,
  `request_mockup()`, `capabilities_schema()`.
- The `test_renovisor_contract.py` hash test covers the new files unchanged.

## 2. Vocabulary (`vocabulary.py`) — D-B

Every enum keeps UPPER_SNAKE member names and takes the **catalogue's string as its value**:

```python
class HeatGenerator(str, Enum):
    AIR_SOURCE_HEAT_PUMP = "air_source_heat_pump"
    ...                                   # the 16 values of measure heating_system.type_of_system
    SOLID_FUEL_HEATING = "solid_fuel_heating"   # inventory-only, F-spec §3.6
class HeatDistributionType(str, Enum):    # request values; .hisim_member -> HeatDistributionSystemType
    SURFACE_HEATING = "surface_heating"; LOW_TEMPERATURE_RADIATOR = "low_temperature_radiator"; CONVENTIONAL_RADIATOR = "conventional_radiator"
class HotWaterSupply(str, Enum):          together_with_heating_system | separate_heat_pump | separate_direct_electric
class VentilationType(str, Enum):         natural | window_trickle_vent | mechanical_extract | demand_controlled_extract | mechanical_ventilation_with_heat_recovery
class AirTightness(str, Enum):            as_built | diy_sealed | professionally_sealed
class TemperatureControl(str, Enum):      traditional_thermostats | smart_heating_control_system
class WhiteAppliances(str, Enum):         existing | new_efficient
class SolarThermalSupplies(str, Enum):    dhw_only | dhw_and_space_heating
class CollectorType(str, Enum):           flat_plate | evacuated_tube
class BuildingType(str, Enum):            detached_sfh | semi_detached_sfh | terraced_sfh | bungalow | apartment | other
class RoofShape(str, Enum):               pitched | flat
class FrameMaterial(str, Enum):           wood | plastic | metal | composite
class Country(str, Enum):                 IE | ES | NL
class Provenance(str, Enum):              unchanged
class ReportStatus(str, Enum):            used | approximated | defaulted | not_implemented_yet   (moves here from report.py)
```

A test asserts every enum value equals the corresponding value list in the vendored
`measures.yaml` or the request schema (`enum` arrays), so the two cannot drift. Remove the
enums the new request has no field for (`DwellingType`, `RetrofitStatus`, `TabulaBuildingType`,
`FloorConstruction`, `WallConstruction`, `PvOrientation`, `Drivetrain`, `SecondaryHeating`,
`DhwSupply`); `ThermalElement` stays (`roof, facade, floor, window, door`).

## 3. Request model and validation (`request.py`) — replaces `inventory.py`, `catalogue.py`, `options.py`

1. **Structural validation with the schema itself**, not a mirror: `jsonschema` Draft 2020-12
   against the vendored `calculation-request.schema.json`, collecting **all** errors
   (`iter_errors`), each mapped to `{path, code, message, accepted?}` with the F-spec §3.1 codes
   (`key.unknown` for `additionalProperties`, `type.invalid`, `enum.unknown` with `accepted`,
   `range.exceeded` for `minimum/maximum`, `required.missing`, `oneof.violated`,
   `schema_version.unsupported`). `path` is dotted with `[i]` for list indices. Using the schema
   directly is what the F-spec's T-SCHEMA exists to prove for a mirror; here there is nothing to
   prove, and the test becomes: the vendored mockup validates, and a generated corpus of one-key
   mutations of the mockup is rejected with the expected code and path.
2. **Typed access after validation**: frozen dataclasses `Request`, `House`, `Building`,
   `Element` (`u_value_in_watt_per_m2_per_kelvin`, `area_in_m2`, `added_insulation: Tuple[Layer, ...]`,
   plus the element-specific fields), `Heating`, `HotWater`, `PvSystem`, `Battery`,
   `SolarThermal`, `ElectricVehicles`, `Measure(id, options)`, built from the validated dict.
   Absent optional blocks are `None`. The `material` option is a frozen `Material` with the
   schema's field names verbatim (`asp_id`, `thermal_conductivity_w_mk`, `heat_capacity_j_kgk`,
   `density_kg_m3`, `co2_footprint_a1_a3_c3_c4_kg_m2`, `lifespan_years`).
3. **The frozen catalogue table** (`CatalogueTable`, class attributes): the 32 ids of the
   2026-09-17 revision with each option's name, `access_level`, `value_type` and `values`. A
   test (T-CAT) asserts it equals the vendored `measures.yaml`. Semantic checks per F-spec §3.2:
   `measure.unknown`, `measure.duplicate`, `measure.option.unknown`, `measure.option.missing`
   (an `everyone` option absent), `measure.option.value.unknown` (with `accepted`),
   `added_insulation.not_allowed`, `location.country.unsupported` (no `.N.` TABULA typology;
   today `ES`), `hot_water.conflicting_volumes`, `tabula.unresolvable`, `range.exceeded` for
   `electric_vehicles.number > 2` and `change_room_temperature` outside 12–28. The `material`
   option's `asp_id` is provenance and is **not** checked against `materials.yaml` (D-A); the
   conductivity must be a positive number (`type.invalid` otherwise).
4. `RequestError(problems)` → `problems.json` and exit 2 (F-spec §3.3). Keep `ReasonCode` only for
   the crash group and for `result.py`; the request codes are the F-spec's strings.

## 4. Apply (`apply.py` = today's `registry.py` + `effects.py` + `application.py`, reshaped)

Keep the registry-of-functions and the closed effect set; change what they read and write:

- Functions are keyed by the **catalogue id** (`external_insulation`, …, 32 of them).
- `AddThermalResistance` carries the request's `Material` (conductivity from the request, D-A)
  and the layer's `placement` (F-spec §4.2 table; provenance only). The composer is unchanged:
  `U_new = 1/(1/U + d/λ)`, layers stack in list order, the note carries the arithmetic with the
  numbers (F-spec §4.3). `U_existing` is the request's element U-value (required by the schema),
  never TABULA's.
- **Layer default thicknesses** per measure (F-spec §4.2: 100/60/100(≤150)/100/80/100/100/150/
  120/150/300/200 mm) and the **fixed materials** for the three option-less measures
  (`cavity_wall_insulation` → EPS beads λ 0.0355 per the review's note that the dump gives
  0.0355; `basement_internal_insulation`, `top_floor_ceiling_insulation` → stone wool λ 0.035)
  live in `constants.py`, each with a `source` comment; every use is a `defaulted` or
  `approximated` line. `Q11`'s target-driven default and `regulatory_u_values_IE.json` are
  removed (D-A: the frontend knows the current U and can send a thickness).
- **Openings**: `window_replacement` writes `glazing_panes`, `frame_material`,
  `low_emissivity_coating` and the U-value (expert `u_value_in_watt_per_m2_per_kelvin` when
  given, else `NEW_WINDOW_U[(panes, coating)]` = {(2,False):1.4, (2,True):1.1, (3,False):0.8,
  (3,True):0.7}); `door_replacement` likewise with `NEW_DOOR_U` = {0:1.4, 2:1.8, 3:1.2}. Both
  tables in `constants.py`, `approximated`, "to be reviewed" in the comment.
- `hot_water_tank_and_pipe_insulation` → `hot_water.tank_and_pipe_insulated = true` →
  `DHWStorage.config.heat_transfer_coefficient_in_watt_per_m2_per_kelvin: 0.18`
  (`TANK_INSULATED_HEAT_TRANSFER`, half the class default), `approximated`.
- `heating_system` sets `heating.type_of_system` and **removes** `flow_temperature_in_celsius`,
  `seasonal_efficiency_in_percent`, `secondary`, `cooking_range` with a report line each.
- `photovoltaic_system` replaces `pv_system` by `{size_in_percent_of_roof_area}` keeping
  `azimuth`/`tilt`; `battery_system` replaces `battery` by `{days_to_cover}`; `electric_vehicle`
  writes `electric_vehicles.number`; `change_room_temperature` writes
  `building.set_heating_temperature_in_celsius`; `temperature_control_system`,
  `hot_water_system`, `heating_installation`, `air_conditioners`, `solar_thermal_system`,
  `ventilation_system`, `shallow_air_tightness_measures`, `replace_white_appliances`,
  `optimize_behaviour_for_self_consumption_of_pv`, `diy_sealing_of_air_leaks`,
  `thermocover_for_the_windows`, `outside_shading` write exactly the fields of F-spec §4.2.
- `installation_year` (experts, five measures) is recorded and has no target.
- **No refusals from apply any more** (D-E). Every former `Refusal` becomes either a whitelist
  match (§6) or, for invalid input, a `problems.json` code (§3). `ExclusivityTable` and
  `FitToBuilding` are deleted: the F-spec has no construction facts to check against and
  contradictory layers simply stack (F-spec §4.2 says so).

## 5. Translate (`translate.py` = today's `parametriser.py` + `bindings.py` + `base_files.py`)

- **Base files are the twins** (D-D): `BaseFiles.BY_GENERATOR` maps the 17 `HeatGenerator` values
  to the nine `energy_systems/household_*_building_sizer.grouped.energy_system.yaml` files per
  F-spec §5.2 (gas ← `condensing_gas_heating`, `conventional_gas_heating`, `*_lpg_heating`;
  oil ← `conventional_oil_heating`, `condensing_oil_heating`, `hvo_heating`; pellets ←
  `pellet_heating`, `biomass_heating`, `solid_fuel_heating`; wood chips; hydrogen; heat pump ←
  `air_source_heat_pump`, `ground_source_heat_pump`, `hybrid_heat_pump`; electric; district),
  plus the solar-thermal twins for gas and heat pump when `solar_thermal_system` is present, and
  the car twin never (EV is `not_implemented_yet`, D-C). `conventional_gas_heating` and
  `condensing_oil_heating` set `boiler_type` on the generator (`CONVENTIONAL` / `CONDENSING`).
- **Bindings** become the F-spec's "Target" column: request path → (component key, field or
  constructor argument), with the recorded keys, no value maps. `heat_distribution.type_of_system`
  → `HeatDistributionController.config.heating_system: <HeatDistributionType.hisim_member.name>`
  (the enum carries the mapping, D-B). `pv_system.size_in_percent_of_roof_area / 100` →
  `PVSystem.config.share_of_maximum_pv_potential`; `pv_system.power_in_watt` → `power_in_watt`;
  **absent `pv_system` → `power_in_watt: 0`** (D-D; verified to run) with a `defaulted` line
  saying the array is pinned to zero because the twin has no PV group. `battery` present →
  `electricity_management: ems_with_battery` with capacity and inverter pinned
  (`custom_pv_inverter_power_generic_in_watt = capacity × 500`), absent → `metered_directly`
  (the fix of `c02bc801` carried over). `occupancy.pv_self_consumption_optimised` →
  `not_implemented_yet` (no `ems_without_battery` option in the twins).
- **Constructor swaps** as today: `Building.for_tabula_code{building_code,
  absolute_conditioned_floor_area_in_m2, number_of_apartments: 1,
  heating_reference_temperature_in_celsius}` — the reference temperature from the TABULA row's
  `Theta_e_Base` column for the selected code (review §6; no per-country constant), reported
  `defaulted` with the value; `Weather.for_location{location: <country>}`;
  `UTSPConnector.for_household{household: CHR01 Couple both at Work (JsonReference with Name
  and Guid.StrVal), data_acquisition_mode: USE_PREDEFINED_PROFILE}` always (D-C), behind
  `OccupancyMode.PREDEFINED_CHR01` in `constants.py`; the matcher of `occupancy.py` stays for
  `OccupancyMode.LOCAL_LPG`, unused, with a docstring saying so. Remove the recorded
  `weather_identity`, `Weather.config.location`, `source_path`, `data_source` as today.
- Everything not written stays as the twin has it (AUTO through presets); the translator never
  computes a heating load.
- **TABULA** (`tabula.py` from `tabula_ie.py`): index `(country, type) → bands` for every `.N.`
  country; `BuildingType` → SFH/TH/AB per F-spec §3.3; band by year with clamping; **variant
  always `001`**; usable-row rule as today, except that when the request supplies both
  `door.area_in_m2` and `window.area_in_m2` every row is usable; `tabula_building_code` in the
  request skips the derivation. Read the CSV with `sep=";"`, `decimal=","`, `encoding="cp1252"`
  as `building/information.py` does.
- **Self-check**: `dump_energy_system` then `load_energy_system` on the text; the diff rule of
  step 5 stays and is extended to allow `boiler_type` and the zero PV pin.
- The output file is `renovisor_<hash>.energy_system.yaml`, `<hash>` = first 16 hex of SHA-256
  over the canonical request (sorted keys, compact separators, UTF-8); `name: renovisor_<hash>`.

## 6. The whitelist (`not_implemented_yet.yaml`, `whitelist.py`) — D-E

The file's format and semantics are F-spec §4.4 (`path` with optional `except`/`when`, or
`measure: id[.option[=value]]`, `note`, optional `tracked`). Initial content: the F-spec's §6.4
list, **plus** the entries D-D adds because the twins lack the groups:
`house.temperature_control.type_of_system` except `[traditional_thermostats]` ("No night-setback
group in the recorded base files yet"), `house.air_conditioning` ("No air-conditioner group in the
recorded base files yet"), `house.occupancy.pv_self_consumption_optimised` ("No EMS-without-battery
option in the recorded base files yet"), `measure: temperature_control_system`, `measure:
air_conditioners`, `measure: optimize_behaviour_for_self_consumption_of_pv`, and the former
refusals: `measure: hot_water_system.supply=separate_direct_electric`, `measure:
hot_water_system.supply=separate_heat_pump` with `when: heating.type_of_system != air_source_heat_pump`,
`measure: solar_thermal_system` with `when: heating.type_of_system not in [conventional_gas_heating,
condensing_gas_heating, air_source_heat_pump]`.

Mechanics: `apply` and `translate` emit `NotImplemented(item, value)` where they find no target;
`Whitelist.match(item, renovated_house)` returns the entry or `None`; `None` →
`TranslatorError` (exit 3, `translator_error.json`, one-line stderr reason). Every `not_implemented_yet`
report line carries the entry's `note` verbatim. `when` is evaluated on the renovated house.

## 7. Report (`report.py`)

Statuses `used | approximated | defaulted | not_implemented_yet`; shape of F-spec §6:
`translator {version, commit, request_schema_version, hisim_commit}`, `base_file`,
`energy_system_file`, `fields[] {path, status, target?, value?, note}`, `measures[] {id, status,
options[] {name, status, note?}, targets[]}`. Invariants (tests): every leaf present in the request
appears exactly once in `fields`; every default applied appears once as `defaulted` with `value`;
every measure once with every option it carried; `not_implemented_yet` lines only from whitelist
entries. File name `mapping_report.json`.

## 8. Run, simulation parameters, outputs (`run.py`, `simulation.py`, `__main__.py`)

```
python -m hisim.renovisor run       <request.{json,yaml}> --out DIR [--period full_year|one_day_15min|one_week_15min]
python -m hisim.renovisor translate <request> --out DIR
python -m hisim.renovisor validate  <request>
python -m hisim.renovisor capabilities --out FILE [--measures <measures.yaml>]
```

Exit codes 0 / 2 (`problems.json`) / 3 (`translator_error.json`) / 5 (HiSim refused the file or
the simulation raised; the `EF-…` id or the traceback's last line on stderr). `SimulationParameters`
built in-process per F-spec §2.3 (**year 2019**, 900 s, `logging_level: 3`,
`result_directory = --out/results`), with post-processing options `COMPUTE_KPIS`,
`WRITE_KPIS_TO_JSON`, `COMPUTE_LIFECYCLE_COSTS` (D-F) — **not** `COMPUTE_OPEX/CAPEX`, and **not**
`country` on the parameters (it crashes `COMPUTE_KPIS` for `IE`; attach `EconomicParameters(country)`
as `calculate.py` does today and keep that docstring). Outputs in `--out`:
`renovisor_<hash>.energy_system.yaml`, `mapping_report.json`, `realized.energy_system.yaml`,
`realized.audit.yaml`, `component_connections.json`, `results/all_kpis.json`, `result.json`
(step 6, unchanged shape), `calculation.json` (kept as the success manifest with
`options_added`, `image_digest`). `ResultPathProviderSingleton.reset()` first; nothing written
outside `--out` (test with `git status --porcelain`).

`result.py`/`kpis.py`/`costs.py`/`provenance.py` stay; they read the same `all_kpis.json` and
lifecycle files. The envelope material cost now uses the request's material object: it has no
price, so `investment_breakdown.envelope_material` is **absent** and listed under `missing` with
"the request carries no material price (D-A); the materials team's total-cost column is the source"
— until the request schema gains it. `embodied_co2_in_kg` uses `co2_footprint_a1_a3_c3_c4_kg_m2 ×
element area` from the request's material (per m², the field the request has).

## 9. Capabilities (`capabilities.py`) and the map (`map.py`)

`python -m hisim.renovisor capabilities --out FILE` writes the document of
`measure-capabilities.openapi.yaml` (read its `components.schemas` for the exact shape): `engine`,
`engine_version`, `translator {version, commit, hisim_commit, request_schema_version: 1,
catalogue_revision, generated_at, probes}`, `measures[] {measure_id, status, options[] {name,
status, accepted_values | values[] {value, status, note}, minimum, maximum}, hisim_targets[],
note}`, `fields[]` likewise for the inventory. `generated_at` is the one non-deterministic field;
`--generated-at` overrides it for tests. Probe set (as data, `Probe` dataclasses): the anchor
(vendored mockup with `measures: []`), **the bare baseline with every optional block absent**,
one probe per optional block present alone, one per measure on, per enum value of each option,
per boundary of each integer option's `values` (or 1 and a large value for free integers), per
inventory enum value and range boundary. `validate + apply + translate` each, no simulation;
status per item = worst observed. `T-NIY`: every `not_implemented_yet` line has a whitelist entry,
every entry is hit by a probe, no entry's probes report `used`/`approximated`.
`T-CAP`: the document's measure-level tally equals F-spec §4.2 adjusted for D-D (compute it and
report it; expected roughly 14 `used`, 9 `approximated`, 9 `not_implemented_yet`).

`map.py` reads the same probe results and renders as today; add a distinct `substituted` marker
(a whitelist entry whose note says "modelled as …") beside `no_model`, and emit that distinction
as `substitution: true|false` on the capability document's entries (review D-E). Regenerate
`translation_map.html`; the trace example becomes the vendored mockup.

## 10. Deletions and renames

Delete: `inventory.py`, `catalogue.py`, `options.py`, `materials.py`, `materials_import.py`,
`data/insulation_materials.json`, `data/regulatory_u_values_IE.json`, `base_files.py` (folded
into `translate.py`), `bindings.py` (folded), `parametriser.py` (folded), `application.py`
(folded into `apply.py`), `laws.py` (the battery law becomes: `days_to_cover × daily kWh of the
CHR01 predefined profile`, computed once from the shipped profile through
`UtspLpgConnector.electricity_consumption_of` with `USE_PREDEFINED_PROFILE`, cached on the class;
the vehicle and heat-pump terms of Q12 are dropped for the MVP and the note says so), the
`mockups/request_roundtrip/` directory (the mockup is now the vendored one; replace the README
with one paragraph pointing at it), `tests/test_renovisor_contract_alignment.py` (the openapi
alignment is moot), and the tests of the deleted modules. Rename `calculate.py` → `run.py`,
`translation_report.json` → `mapping_report.json`, `parametrised.energy_system.yaml` →
`renovisor_<hash>.energy_system.yaml`, `errors.json` → `problems.json` (exit 2) /
`translator_error.json` (exit 3); `ReasonCode` shrinks to the crash group plus `result.py`'s.
Update `CLAUDE.md`'s RenoVisor paragraph, `hisim/renovisor/__init__.py`'s docstring, and add
`hisim/renovisor/how_to_use.md` (one page: the four commands, the files in and out, the exit codes,
the whitelist rule). `TRANSLATOR_VERSION = "2.0.0"`.

## 11. Tests (`@pytest.mark.base` unless stated) — the F-spec's list, adapted

T-SCHEMA (mockup validates; mutation corpus rejected with code and path) · T-VAL (every code
reachable, all at once, `validate` and `run` agree) · T-CAT · T-APPLY (deep-diff per measure row;
list order; two layers stack; defaults reported; original unmutated; `hot_water_system.supply=
separate_heat_pump` depends on the renovated generator) · T-ENV · T-TABULA (band boundaries IE
SFH/TH/AB and DE SFH; clamping; usable-row rule with and without areas: `IE 1975 SFH` → band 04 or
06 without areas, 05 with; `tabula_building_code` passthrough; ES → `location.country.unsupported`;
17 countries index) · T-XLATE (for every generator value the file loads; the generator entry is
§5's; every target receives its value, asserted on the model; the report covers every leaf once;
absent PV pins zero; absent battery selects `metered_directly`) · T-NIY · T-CAP · T-DET (identical
bytes twice; key order does not change the file name) · T-CLI (0/2/3/5 each reachable, each writing
what §8 says, one-line stderr on 3 and 5) · T-RUN `@pytest.mark.system_setups` (the vendored
mockup baseline and package, `one_day_15min`: exit 0, all outputs, package heating demand <
baseline in `all_kpis.json`; and the bare baseline with every block absent) · T-RESULT (the
step-6 payload assertions on the mockup run, adjusted for the absent envelope price).

## 12. Done means

All `tests/test_renovisor_*.py` green; flake8 and mypy clean on `hisim/renovisor` and `hisim/`;
`python -m hisim.renovisor capabilities --out …` produces a document that validates against the
vendored `measure-capabilities.openapi.yaml` schema; `translation_map.html` regenerated; the
final report lists: the capability tally, every constant in `constants.py` with its source
comment, every whitelist entry with the probe that hits it, the wall time of the mockup run, and
every place where the F-spec, the review and §13 disagreed and which one you followed.

## 13. Addendum of 2026-09-19 (after review of the generated capability document)

Three changes, decided by the owner after reading the document.

**A. Field-level note aggregation is wrong.** For a `fields[]` entry whose status is the worst
over several probes, the top-level `note` may come from a *different* probe than the one that set
the status (`house.solar_thermal_system.supplies` is `not_implemented_yet` because of
`dhw_and_space_heating`, but its note is the `dhw_only` sentence). Fix: the entry's `note` is the
note of the probe that produced the worst status; when several probes tie at the worst status
with different notes, join them with `" | "` in probe order. Same rule for `options[]` (the
`heating_system.type_of_system` option is `used` yet carries a `not_implemented_yet` value's note;
an option's note is the note of its own worst *option-level* observation, or absent when the option
itself is `used` and only values are listed). Add a test that builds a two-probe field with
different statuses and asserts the note follows the status.

**B. A `results` section.** The document gains `results: {kpis: [...], costs: [...]}`, one entry
per field `result.py` can emit, generated from `KpiSources`/`CostSources` (the same tables, not a
second list): `{"field": "kpis.energy_demand_in_kilowatt_hour_per_year", "provenance": "SIMULATED" |
"PARTIAL" | "MOCKED", "source": "<the source string result.py writes>", "conditions": ["PARTIAL when
the period is shorter than a year"]}`, and for fields that can be absent `{"field": …,
"provenance": "absent", "reason": "<the missing reason result.py writes>", "when": "<condition>"}`
(embodied CO₂ without element areas; envelope material price always until the request carries one;
grant until `subsidy_catalog/IE.json`; payback needs the base run; property value has no model).
The vendored `measure-capabilities.openapi.yaml` has no `additionalProperties: false`, so the
document still validates; additionally write the `results` schema as a HiSim-side proposal file
`hisim/renovisor/contract/measure-capabilities.results-extension.yaml` (an OpenAPI schema fragment
for the frontend team to merge; not pinned, HiSim-authored) and validate `results` against it too.
Render the section in `map.py` (the trace tab's result pane already lists these; reuse).

**C. `low_temperature_radiator` is `not_implemented_yet`.** The cost engine's adapter returns no
cost facts for that emitter on purpose and the evaluation aborts (exit 5). Decision: whitelist it
as a *substitution*: entries `measure: heating_installation.type_of_system=low_temperature_radiator`
and `path: house.heat_distribution.type_of_system` with `except: [surface_heating,
conventional_radiator]`, note "No cost row for a low-temperature radiator in the cost database yet;
modelled as surface heating (floor heating), the heat pump's other low-temperature emitter."
`translate.py` writes `FLOORHEATING` for it, so a heat-pump package with that emitter runs and is
priced; the mapping report line and the capability document carry the note; `substitution: true`.
Verify with the vendored mockup **verbatim** (it asks for `low_temperature_radiator`): `run
--period one_day_15min` exits 0 and `result.json` is written; make that the end-to-end test instead
of the `surface_heating` copy, and keep `TestTheLowTemperatureRadiatorBlocker` pointing at the
cost adapter so the entry is removed the day a cost row exists (T-NIY will then also demand it).

Regenerate the capability document fixture, the map, and the tally (expect the
`heating_installation` measure to become `approximated` at measure level or stay `supported` with
one `not_implemented_yet` value — follow the status rules of the F-spec §4.2 and report which).
