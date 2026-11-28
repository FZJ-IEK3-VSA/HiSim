# Spec: An explicit building computes with the unchanged Building: BuildingInformation takes a BuildingDescription, and TABULA is one source of it

Status: draft, design agreed (owner, 2026-11-26); nothing built. Owner: Nicolai Gölz. Epic:
hisim-building-description-eylt. Builds on: `roadmap/hisim_verification_agent_spec.md` (branch `verification_spec`,
revision 6: FR2; D16 of revision 14 on branch `verification/step6-suite`), branch `verification/step2-building-overrides`
(PR #1253, superseded by this spec), PR #954 (`building.expanded.yaml`), the EPC tool (#897).

## 1. Problem

Every building HiSim computes is a TABULA/EPISCOPE row. `BuildingInformation` (`hisim/components/building/information.py`)
reads the row, scales it to the conditioned floor area, weights U-values, estimates the door, builds the windows per
direction and takes C_m, A_m and A_tot from the heat-capacity class table; the flat overrides of `BuildingConfig` (areas,
U-values, floor area, apartments) replace single values of that chain. A building that is not a TABULA row, first the
test cases of the building-model verification suite, later any measured or hand-described house, cannot be stated.

The verification suite's first answer (FR2 of its spec, revision 6, decided 2026-11-02) was a second parameter source,
`explicit_description` in `hisim/components/building/explicit.py`, with κ per element. Its second answer (D16 of
revision 14 on branch `verification/step6-suite`, 2026-11-03, built on branch `verification/step2-building-overrides`)
was eleven more optional overrides on a carrier row plus an origin record that
proves afterwards that nothing of the row was used: about twenty "configured or row" branches in `BuildingInformation`,
a test box that is formally a German single-family house of 1958, and bookkeeping that manages the problem instead of
removing it. Neither is kept. This spec is the configuration mechanism alone; the suite adapts to it in its own epic
(hisim-x6c7, branch `verification_spec`), where its spec gets a decision that replaces D16 and a new FR2. The
deviations from FR2 of revision 6 are deliberate: the field is `description` in `description.py`, not `explicit_description` in
`explicit.py`, and the description states C_m, A_m and A_tot in final form; the κ rule of the suite stays in the suite.

Not in scope: a layer-based description from which HiSim derives U-values and capacities (a possible third source
later), partial overrides of the new quantities on a TABULA building (D3), any change to the 5R1C physics.

## 2. Decisions

**D1 — The description is an inline block of the configuration** (owner, 2026-11-26). A file by path would let the
realized twin and the file drift apart; a sizable field would need the engine to carry a dataclass as a value type.
Consequence: `BuildingConfig.description: Optional[BuildingDescription]`, written in full into every realized twin.

**D2 — A described building pins `technical_room_location`; the law refuses instead of falling back** (owner,
2026-11-26). The law derives the room from the row's cellar code, and a described building has no row. Consequence:
`for_description` takes the location without a default; a file that leaves it `AUTO` is refused naming the two values.

**D3 — No partial overrides of the new quantities on a TABULA building in YAML** (owner, 2026-11-26). Every such override
would be one more "configured or row" branch. Consequence: in Python,
`dataclasses.replace(BuildingDescription.from_tabula(config), ...)` is the way; a key of the description written flat
beside `building_code` is an unknown key and refused (R4).

**D4 — `building_heat_capacity_class` becomes `Optional`: mandatory and one of its five names with a code, refused with a
description** (owner, 2026-11-26). The class only feeds the TABULA path's C_m and A_m; a stated class beside explicit
C_m and A_m would be ignored silently, and a misspelt class dies today with a bare `KeyError` in `get_constants`.
Consequence: R2 and R3. `for_tabula_code` keeps its argument default `"medium"`, a documented archetype default. No
committed file needs a change: the presets state the class, the building-sizer probes set the code on the sizer's
`ArchetypeConfig` whose setups copy the class only when stated, and the three tests that build `BuildingConfig`
directly state it.

**D5 — `source` is a mandatory text of the description** (owner, 2026-11-26). Every parameter has a source
(`system_docs/modelling_guide.md` §5); a described building names it once. Consequence: a non-empty string, refused
when empty.

**D6 — Exclusivity refuses, nothing is ignored** (owner, 2026-11-26). A field that has no effect in the chosen path is
a wrong configuration. Consequence: R1 and R2; "set" means "not `None`".

**D7 — Stage 2 refuses a building unless 0 < H_tr,op < H_tr,ms, for both paths** (owner, 2026-11-26). Today
`Building.get_thermal_conductance_of_opaque_surfaces_in_watt_per_kelvin` guards only against zero: H_tr,op > H_tr,ms
yields a negative H_tr,em and the simulation runs on with a wrong network, and H_tr,op = 0 leaves the variable
unassigned and crashes by accident. Consequence: R7; a TABULA code that violates the condition changes its golden
entry from numbers to a refusal, which Q2 decides on.

**D8 — The ranges of §4.3 are the validation; every violation names the field and the value** (owner, 2026-11-26).
Consequence: `__post_init__` of the three classes; one test per rule.

**D9 — Two stacked pull requests, the first without new input and with the golden's numbers unchanged** (owner,
2026-11-26). The refactor's claim "no behaviour change" is verifiable on its own. Consequence: beads
hisim-building-description-eylt.1 and .3; after the squash merge of PR 1 the second branch is rebased onto `main`
before its PR is retargeted.

**D10 — Window and door carry no adjustment factor, and the window element's area is stated beside the window list**
(owner, 2026-11-26). HiSim uses b = 1 for both (their descriptors carry no `b_Transmission_*` column), and the TABULA
path multiplies the window conductance by the scaled total area while the solar gains take the areas per direction;
summing the list instead could move the last bit. Consequence: `ElementDescription` for window and door,
`OpaqueElementDescription` for floor, wall and roof, and the cross-field rule of §4.3.

## 3. Terms

- **Description**: a `BuildingDescription`, every quantity stage 2 needs, in final form, with units in the field names.
- **Stage 1**: the TABULA interpretation, `BuildingDescription.from_tabula(config)`, or the description as written.
- **Stage 2**: the physics `BuildingInformation` computes from a description, shared by both paths.
- **TABULA path / described path**: a configuration with `building_code` / with `description`.
- **Element**: floor, wall, roof, window, door; area A in m², U-value in W/(m²K), adjustment factor b (ISO 13790,
  dimensionless, 1 for a surface to outdoor air).
- **A_f**: conditioned floor area in m². **V**: conditioned air volume in m³. **C_m** in J/K, **A_m** and **A_tot**
  in m²: the ISO 13790 thermal capacity, effective mass area and total internal surface area.
- **H_tr,op**: opaque transmission conductance in W/K; **H_tr,ms** = h_ms · A_m with h_ms = 9.1 W/(m²K);
  **H_tr,em** = 1 / (1/H_tr,op − 1/H_tr,ms); **H_ve**: ventilation conductance in W/K.

## 4. Design

### 4.1 Two stages, one switch

```
BuildingConfig ──building_code──▶ stage 1: BuildingDescription.from_tabula(config)  ─┐
                                   (row lookup, scaling, U weighting, b rule, door     │
                                    estimate, windows per direction, class table)     ├──▶ stage 2: physics
BuildingConfig ──description────▶ the description as written ────────────────────────┘    (U·A·b, H_ve, sums,
                                                                                          constants, windows,
                                                                                          maximum demand)
```

- Stage 1 of the TABULA path is today's pipeline (`get_building_from_tabula`, `get_building_area_parameters`,
  `get_building_heat_transfer_parameters`, the class table of `get_constants`, `get_number_of_apartments`), behind one
  function that returns a `BuildingDescription`. The flat overrides act here and only here. The rule "a configured
  U-value fixes b" with the floor's 0.5, the door estimate for `A_Door_1 = 0`, the thermal-bridging surcharge 0 read as
  0.1, the window rescaling over the reference window area and the apartment count derived from the row and truncated
  by `int()` stay here and reach no described building.
- Stage 2 reads the description and the behaviour fields of the configuration (set temperatures, initial temperature,
  the maximum-demand override, the heating reference temperature); nothing else.
- The switch is one line in `BuildingInformation.__init__`: the configuration's description, or stage 1. The constructor
  signature `BuildingInformation(config)` stays, so the five constructions in the package (`building.py`, `config.py`
  `sizing_facts`, `config/context.py`, `renovisor/layers.py`, `renovisor/tabula.py`) and the tests are untouched.
- `BuildingInformation.description` is a public attribute, the one object `building.expanded.yaml` and the verification
  suite read.

### 4.2 The description

Module `hisim/components/building/description.py`, three frozen `dataclass_json` dataclasses with
`undefined=Undefined.RAISE`. Every field is mandatory; none has a default. Names follow the attributes
`BuildingInformation` exposes, with two deliberate exceptions listed in the mapping below.

`ElementDescription` (window, door):

| Field | Unit | Accepted |
|---|---|---|
| `area_in_m2` | m² | ≥ 0 (a building without a door states 0) |
| `u_value_in_watt_per_m2_per_kelvin` | W/(m²K) | ≥ 0; the TABULA catalogue carries 0 beside a positive area (§7), so 0 is accepted on both paths |

`OpaqueElementDescription` (floor, wall, roof) = `ElementDescription` plus:

| Field | Unit | Accepted |
|---|---|---|
| `adjustment_factor` | – | in [0, 1] |

`WindowDescription`, one per window, the seven numbers of `solar_gains.WindowGeometry`:

| Field | Unit | Accepted and meaning |
|---|---|---|
| `area_in_m2` | m² | ≥ 0 |
| `tilt_angle_in_degrees` | ° | in [0, 90]; 0 horizontal, 90 vertical |
| `azimuth_angle_in_degrees` | ° | `null` if and only if the tilt is 0; else in [0, 360), 180 being south |
| `frame_area_fraction_reduction_factor` | – | in [0, 1]; the share of the area taken by the frame (TABULA `F_f`); the optics multiply by 1 − this value, so 0 is frameless and 0.3 leaves 70 % glass |
| `glass_solar_transmittance` | – | in [0, 1]; `g_gl_n`, perpendicular |
| `nonperpendicular_reduction_factor` | – | in [0, 1]; `F_w` |
| `external_shading_vertical_reduction_factor` | – | in [0, 1]; `F_sh_vert`, 1 means no shading |

`BuildingDescription`:

| Field | Unit | Accepted |
|---|---|---|
| `source` | text | non-empty: where the numbers come from, one statement for the whole description |
| `conditioned_floor_area_in_m2` | m² | > 0; A_f: the sizing fact `conditioned_floor_area_in_m2`, the specific heating load, the KPIs; it does not enter the 5R1C network |
| `floor`, `wall`, `roof` | – | `OpaqueElementDescription` |
| `window`, `door` | – | `ElementDescription`; `window.area_in_m2` is the area the window conductance multiplies |
| `windows` | – | tuple of `WindowDescription`, may be empty; the solar-gains producer's input |
| `use_air_change_rate_per_hour` | 1/h | ≥ 0 |
| `infiltration_air_change_rate_per_hour` | 1/h | ≥ 0 |
| `conditioned_air_volume_in_m3` | m³ | > 0 |
| `thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin` | J/K | > 0; C_m |
| `effective_mass_area_in_m2` | m² | > 0; A_m |
| `total_internal_surface_area_in_m2` | m² | > 0; A_tot |
| `number_of_apartments` | – | integer ≥ 1 (today the TABULA path truncates with `int()`; a described 1.5 would silently become 1, so a float is refused) |
| `thermal_bridging_surcharge_in_watt_per_m2_per_kelvin` | W/(m²K) | ≥ 0; enters the maximum demand only, as today |

Mapping to `BuildingInformation` (both paths set these attributes):

| Description | `BuildingInformation` |
|---|---|
| `conditioned_floor_area_in_m2` | `scaled_conditioned_floor_area_in_m2` ("scaled" is the TABULA path's history) |
| `wall.*` | `facade_area_in_m2`, `facade_u_value_in_watt_per_m2_per_kelvin`, `heat_conductance_facade_in_watt_per_kelvin`; `element_values()["wall"]` already uses this key, as does `building.expanded.yaml` |
| `floor.*`, `roof.*`, `door.*` | `<element>_area_in_m2`, `<element>_u_value_in_watt_per_m2_per_kelvin`, `heat_conductance_<element>_in_watt_per_kelvin` |
| `window.*`, `windows` | `window_area_in_m2`, `window_u_value_in_watt_per_m2_per_kelvin`, `heat_conductance_window_in_watt_per_kelvin`; `window_geometries()` |
| `use_air_change_rate_per_hour`, `infiltration_air_change_rate_per_hour`, `conditioned_air_volume_in_m3` | `use_air_change_rate_per_hour`, `air_infiltration_rate_per_hour`, `conditioned_air_volume_in_m3` (new) |
| C_m, A_m, A_tot, apartments, surcharge | the attributes of the same name |

### 4.3 Validation

In `__post_init__` of each class, refusing with the field name and the value:

- A number is an `int` or a `float` and finite; `type(value) is bool`, a string and `null` are refused, nothing is
  coerced. `number_of_apartments` must be an `int`.
- The ranges of §4.2. `azimuth_angle_in_degrees` is `null` exactly when the tilt is 0.
- The same validation runs on what stage 1 returns for a TABULA row, so a row outside the ranges is refused like a
  written description; the sweep of PR 1 reports every such code (§7, Q2).
- Cross-field: the sum of `windows[*].area_in_m2` equals `window.area_in_m2` within a relative 1e-9, and exactly 0
  when `window.area_in_m2` is 0.
- Unknown keys are refused by `Undefined.RAISE`: the energy-system codec rebuilds a nested dataclass through
  `from_dict` (`hisim/energy_system/codec.py`, `ConfigValueCodec._rebuild_nested_dataclass`), and dataclasses_json
  drops unknown keys unless the class says otherwise. The general rule for every nested config block is bead
  hisim-declarative-systems-b3b.36; this spec applies it to its own classes.

### 4.4 Stage 2

Exactly today's expressions in today's order of operations, for both paths:

- conductance per element U · A · b, left-associated; b = 1 for window and door;
- H_tr,w = window + door; H_tr,op = wall + roof + floor as `Building` sums them; the total transmission conductance
  is door + window + facade + floor + roof + thermal bridging, summed in that order; the thermal-bridging conductance is
  the surcharge times (window + floor + door + facade + roof areas, in that order);
- H_ve = 0.34 Wh/(m³K) · (n_use + n_inf) · V (Q1 for the TABULA path);
- h_ms = 9.1 and h_is = 3.45 W/(m²K) as class constants; A_m, A_tot and C_m as given; the report's
  `thermal_capacity_of_building_thermal_mass_in_watthour_per_m2_per_kelvin` = C_m / (3600 · A_f) as today;
- `element_values()` for the five elements on both paths: on the described path `u_value_origin` is `"description"`,
  `adjustment_factor` the description's b and `adjustment_factor_from_tabula_row` false; `Building.__init__` reads the
  floor's factor for the cellar temperature, `__str__` reads the door lines;
- the window geometries, one per entry of `windows`;
- maximum demand = (total transmission + H_ve) · (initial internal temperature − heating reference temperature),
  unless `max_thermal_building_demand_in_watt` is configured;
- refusal R7 unless 0 < H_tr,op < H_tr,ms, with both numbers in the message (D7).

What only the TABULA path sets stays set by its stage 1: `buildingdata_ref`, `windows_directions`,
`scaled_window_areas_in_m2`, `window_scaling_factor`, `tabula_ref_*`; a described building has none of them.

### 4.5 Configuration

| Field | Today | With this spec |
|---|---|---|
| `building_code` | `str`, mandatory | `Optional[str] = None` |
| `description` | – | `Optional[BuildingDescription] = None` |
| `building_heat_capacity_class` | `str = "medium"` | `Optional[str] = None` (D4) |
| the ten area/U overrides, `absolute_conditioned_floor_area_in_m2`, `total_base_area_in_m2`, `number_of_apartments` | `Optional`, act on the row | unchanged, allowed with `building_code` only |
| `max_thermal_building_demand_in_watt`, the set temperatures, `initial_internal_temperature_in_celsius`, `enable_opening_windows`, the cost fields, the three sizable fields | – | unchanged, both paths |

Constructors: `for_tabula_code` keeps its signature. New:

```python
@constructor(note="a building stated in full by its own description; no TABULA row")
@classmethod
def for_description(
    cls,
    name: str,
    description: BuildingDescription,
    technical_room_location: lt.InstallationLocation,
    initial_internal_temperature_in_celsius: float = 22.0,
    set_heating_temperature_in_celsius: float = 20.0,
    set_cooling_temperature_in_celsius: float = 25.0,
    enable_opening_windows: bool = False,
) -> "BuildingConfig"
```

The three defaults are today's field defaults; the location has none (D2). Wire format (D1), numbers illustrative:

```yaml
building:
  class: hisim.components.building.Building
  config:
    building_code: null
    building_heat_capacity_class: null
    description:
      source: "<where the numbers come from>"
      conditioned_floor_area_in_m2: 48.0
      floor: {area_in_m2: 48.0, u_value_in_watt_per_m2_per_kelvin: 0.04, adjustment_factor: 1.0}
      wall:  {area_in_m2: 63.6, u_value_in_watt_per_m2_per_kelvin: 0.5, adjustment_factor: 1.0}
      roof:  {area_in_m2: 48.0, u_value_in_watt_per_m2_per_kelvin: 0.3, adjustment_factor: 1.0}
      window: {area_in_m2: 12.0, u_value_in_watt_per_m2_per_kelvin: 2.1}
      door:   {area_in_m2: 0.0, u_value_in_watt_per_m2_per_kelvin: 1.0}
      windows:
        - {area_in_m2: 6.0, tilt_angle_in_degrees: 90, azimuth_angle_in_degrees: 180,
           frame_area_fraction_reduction_factor: 0.0, glass_solar_transmittance: 0.7,
           nonperpendicular_reduction_factor: 0.9, external_shading_vertical_reduction_factor: 1.0}
        - {area_in_m2: 6.0, tilt_angle_in_degrees: 90, azimuth_angle_in_degrees: 180,
           frame_area_fraction_reduction_factor: 0.0, glass_solar_transmittance: 0.7,
           nonperpendicular_reduction_factor: 0.9, external_shading_vertical_reduction_factor: 1.0}
      use_air_change_rate_per_hour: 0.0
      infiltration_air_change_rate_per_hour: 0.5
      conditioned_air_volume_in_m3: 129.6
      thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin: 2.0e6
      effective_mass_area_in_m2: 100.0
      total_internal_surface_area_in_m2: 220.0
      number_of_apartments: 1
      thermal_bridging_surcharge_in_watt_per_m2_per_kelvin: 0.0
    technical_room_location: heated_zone
```

### 4.6 Refusals

Every rule names the field and the offending value and, where a closed set exists, lists it. "Set" means "not `None`".

- **R1** `building_code` and `description` both set, or neither: refused in `__post_init__`.
- **R2** Beside a `description`, any of these set: the ten area/U overrides, `absolute_conditioned_floor_area_in_m2`,
  `total_base_area_in_m2`, `number_of_apartments`, `building_heat_capacity_class`.
- **R3** With a `building_code`, `building_heat_capacity_class` `null` or outside the five names of the class table,
  the message listing them; and a `number_of_apartments` override that is not integral, which today the row's
  `int()` truncates silently.
- **R4** A key of the description written flat in `config:` beside `building_code`. The setup rule
  `known-config-field` (`hisim/setup_rules/bindings.py`, EF-17) refuses every key of a `config:` block that names no
  field, with or without a preset; it does not look inside a nested block, which is what R5 covers.
- **R5** Inside `description`: a missing field, an unknown key, `null`, `"AUTO"`, a `bool` or a string where a number
  belongs, a non-finite number, a value outside its range, a float for the apartments, the window-area mismatch, an
  azimuth on a horizontal window or none on a tilted one.
- **R6** `technical_room_location` left `AUTO` on a described building: the law refuses, naming `heated_zone` and
  `unheated_space`; it never falls back.
- **R7** H_tr,op ≤ 0 or H_tr,op ≥ H_tr,ms in stage 2, both paths (D7).
- **R8** RenoVisor's `layers.py` already refuses a configuration without a `building_code`; unchanged.

### 4.7 Consumers at the edges

| Consumer | TABULA path | Described path |
|---|---|---|
| sizing facts (`BuildingConfig.sizing_facts`: heating load, apartments, A_f, roof area) | as today | from the description; everything sized from them follows |
| cellar temperature (`Building.__init__`, the floor's b) | as today | the description's floor b |
| `tabula_ref_*` KPI (`Building`, specific heating demand per year) | written | not written |
| `Building` report (`__str__`) | from the description | from the description |
| `technical_room_location` law | the row's cellar code | R6 |
| RenoVisor (`layers.py`, `tabula.py`), building sizer (`ArchetypeConfig`) | unchanged | not reached (R8) |
| `building.expanded.yaml` (#954) | writes the description, plus `hisim_building_code` | writes the description, `hisim_building_code: null`; bead hisim-declarative-systems-b3b.38 |
| verification suite `system_builder` | – | builds a `BuildingDescription`; the verification epic's own work (hisim-x6c7), outside this spec |

## 5. What changes and what does not

- **Results**: none intended. The TABULA path computes the same numbers, with three possible exceptions that the
  catalogue sweep of PR 1 measures: the last bit of H_ve where the association changes (Q1), codes that D7 now
  refuses (Q2; three today, §7), and codes the ranges of §4.2 refuse (none expected after the pre-measurement of §7). The 5R1C physics, h_ms and h_is, the window geometries of a TABULA building and with them the
  solar-gains cache key do not change.
- **Public interfaces**: `building_code` and `building_heat_capacity_class` become `Optional` with the refusals of
  §4.6; `description` is a new nested block; `for_description` a second constructor; `description.py` a new module;
  `BuildingInformation.description`, `conditioned_air_volume_in_m3` new attributes; the schema is regenerated with
  the nested classes rendered (bead hisim-declarative-systems-b3b.37); the `tabula_ref` KPI is absent for a described
  building. The eleven override fields, `ConfiguredWindow`, `ValueOrigin` and `value_origins()` of branch
  `verification/step2-building-overrides` never reach `main`.
- **Goldens and twins**: the characterization golden (`tests/goldens/building_information.json`) is regenerated in
  PR 1 as a metadata change, because `description` is a new public attribute the harness cannot encode today
  (`tests/building_golden_support.py` is extended to encode dataclasses); its numbers stay, except what Q1 and Q2
  allow, and the PR lists every moved value. The realized twins gain the line `description: null` and are
  re-recorded in PR 2. The KPI goldens stay untouched unless Q1 moves a sized value.

## 6. Verification

- PR 1: the roundtrip test, for every TABULA code of the golden: stage 1 → stage 2 equals every attribute of the old
  pipeline exactly; the characterization golden with the metadata regeneration and the sweep report (Q1, Q2); the
  window-geometry and cache-key tests taken over from branch `verification/step2-building-overrides`; a test per
  branch of R7.
- PR 2: a hand-written description reaching the conductances, capacities, windows and maximum demand exactly; one
  test per refusal R1 to R6, including an unknown key inside `description` and inside a window, a float for the
  apartments, a `bool` for a number; the schema round trip of a description once the export renders nested classes;
  `hisim energy-system describe` of the Building naming both constructors; the twins' one-line diff.

## 7. Measured results

Pre-measurement on the characterization golden (`tests/goldens/building_information.json` at `main` f8e81b69,
2026-11-26), on the attributes the golden pins, not on a run of the new code:

- D7: three codes have H_tr,op = 0 despite positive areas and U-values, so their row's b factors are 0:
  `AT.N.AB.05.Gen.ReEx.001.001`, `.002` and `.003`. They cannot build a `Building` today (the unassigned variable of
  §4.6 R7). No code has H_tr,op ≥ H_tr,ms. 124 codes are pinned as raising already.
- Ranges: U = 0 beside a positive area occurs in the default variants 88 times for the door (e.g. the placeholder
  codes `##.N.SFH.01…`, with a conductance of 0), 9 for the window, 7 for the roof, 3 for the wall, 2 for the floor;
  hence U ≥ 0 in §4.2. No area, capacity, surface, apartment count, ventilation conductance or surcharge is outside
  its range.

The sweep of PR 1 reports here, on the new code: the codes R7 refuses, the codes the ranges refuse, and the codes
whose `heat_conductance_ventilation_in_watt_per_kelvin` or `max_thermal_building_demand_in_watt` moved, with the
largest relative difference.

## 8. Open questions

The owner of this spec takes Q1 to Q3 (owner, 2026-11-26). Their beads carry no `owner-decision` label, which
AGENTS.md reserves for the cmf queue.

- **Q1. Ventilation on the TABULA path.** Today H_ve = 0.34 · n · h_room · A_f, left-associated; stage 2 computes
  0.34 · n · V with V = h_room · A_f from stage 1, which can differ in the last bit and, through the maximum demand,
  move the sizing fact `heating_load_in_watt` and every power sized from it, where a catalogue lookup can flip
  discretely. Options: (a) accept, regenerate the golden for exactly the moved attributes, list codes and the largest
  relative difference, run the full test suite with the twins and the KPI goldens and treat any discrete flip as a
  visible failure to decide on; (b) keep the old association on the TABULA path by handing h_room and A_f over beside
  V, two formulas for one quantity; (c) accept and re-record the affected twins. Recommendation: (a). Bead
  hisim-building-description-eylt.4; decided after the sweep.
- **Q2. TABULA codes D7 and the ranges refuse.** Three codes today (§7). Options: (a) keep refusing, the honest
  result for a network that was silently wrong; (b) exempt the TABULA path; (c) refuse and open a bead per affected
  code. Recommendation: (a), with (c) if the sweep finds more. Bead hisim-building-description-eylt.5; decided after
  the sweep.
- **Q3. A second reviewer for §4.5 before PR 2.** The field semantics are wire format forever and the config and
  sizing design has its own author. Options: (a) a named second reviewer; (b) the owner alone. Recommendation: (a).
  Bead hisim-building-description-eylt.6; decided before PR 2 opens, which depends on it.

## 9. Beads

Epic hisim-building-description-eylt.

| Bead | Content | PR |
|---|---|---|
| hisim-building-description-eylt.1 (P2) | PR 1: the two stages, `description.py`, stage 2, the window geometries, R7, the golden's metadata regeneration, the sweeps | – |
| hisim-building-description-eylt.3 (P2) | PR 2: the configuration of §4.5, R1 to R6, `for_description`, schema, twins, tests; closes PR #1253 as superseded | – |
| hisim-building-description-eylt.4 (P2, the owner's question) | Q1 | – |
| hisim-building-description-eylt.5 (P2, the owner's question) | Q2 | – |
| hisim-building-description-eylt.6 (P2, the owner's question) | Q3; PR 2 depends on it | – |
| hisim-declarative-systems-b3b.36 (P3) | every nested config block refuses unknown keys (the general rule behind R5) | – |
| hisim-declarative-systems-b3b.37 (P2) | the schema export renders nested dataclasses; PR 2 depends on it | – |
| hisim-declarative-systems-b3b.38 (P3) | `building.expanded.yaml` writes the description (after #954); depends on PR 2 | – |

hisim-building-description-eylt.2 was a duplicate of .1 and is deleted; the number stays unused.

The verification suite's adaptation (no carrier row, `check_origins` gone, its spec's D16 and FR2 replaced) is a bead
of the verification epic hisim-x6c7, created on branch `verification_spec`.
