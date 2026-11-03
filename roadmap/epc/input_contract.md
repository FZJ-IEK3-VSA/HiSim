# Input contract: the expanded building record

**Status:** draft (in refinement with the requester)
**Date:** 2026-10-13
**Version of the record:** `building-expanded/1`
**Producer:** HiSim, `hisim/energy_system/building_record.py`, called by `write_records()` of every energy-system run (issue #949; requirements D30)
**Consumer:** `epc/inputs/record.py` (design §3.3)
**Related:** `roadmap/epc/requirements.md` R2, R6, D24; `roadmap/epc/design.md` §3.3, §5.2, §7.2

---

## 1. Purpose

HiSim's realized record states a building's configuration; the building HiSim actually computes with
lives only inside `BuildingInformation`. The calculator must compute the certificate for exactly that
building and must know nothing of how HiSim arrived at it. HiSim therefore writes a
second file beside the record, `building.expanded.yaml`, with the building as HiSim used it, read
off `BuildingInformation` attribute by attribute, never recomputed, with HiSim's own origin strings.
The calculator reads that file and nothing else of HiSim. Part 2 defines the site block inside it,
part 3 the country.

## 2. Part 1 — `building.expanded.yaml`

### 2.1 Location and production

- Written by every run of an energy-system file into its result directory, beside
  `realized.energy_system.yaml` and like it before the first time step, from the `BuildingInformation`
  of each `Building` component of the built system: one entry per `Building`, `buildings: []` for a
  system without one.
- A missing or non-finite value refuses before the run starts; HiSim never writes a partial file.
- The file is plain YAML; the reader checks the fields of §2.3 by the consumer rules of §2.4, no
  schema file.

### 2.2 Principle

Every value is the one the `Building` computes with, read off the object as it is: one element per
class with HiSim's area, U-value and adjustment factor, HiSim's window areas per direction, HiSim's
counts and classes. Nothing is averaged, split, scaled or looked up by the writer; the only
derivations are the building type, read from the typology segment of HiSim's building code
(`null` for a segment it does not list), and the `configured` flags and `configured_fields`, read from the
configuration.
`null` appears only where HiSim holds no value.

### 2.3 Fields

`schema` and `producer` stand once at file level; every other path is relative to one entry of the
file's `buildings` list. "Source" names the `BuildingInformation` attribute or the
configuration field the tool reads.

| Path | Type, unit | Source | Used by |
|---|---|---|---|
| `schema` (file level) | string | constant `building-expanded/1` | reader |
| `producer` (file level) | string | `HiSim <version>` | provenance |
| `name` | string | the record entry's name | report |
| `hisim_building_code` | string | `buildingconfig.building_code`, opaque | provenance only |
| `building_type` | string or `null` | `detached` · `terraced` · `multi_family` · `apartment_block` from the typology segment of HiSim's building code (SFH, TH, MFH, AB); `null` for any other segment | AT (terraced houses; `null` → by dwelling units, an assumption), IE |
| `conditioned_floor_area_in_m2` | float, m² | `scaled_conditioned_floor_area_in_m2` | all engines (their reference area follows from it by their norm) |
| `gross_volume_in_m3` | float or `null`, m³ | a gross volume HiSim states, when it states one (P11); `null` today | AT, IE |
| `heat_capacity_class` | string | `building_heat_capacity_class` (HiSim's five classes) | all engines, mapped |
| `dwelling_units.value` | int | `number_of_apartments` (HiSim's `int`) | AT usage |
| `dwelling_units.configured` | bool | `buildingconfig.number_of_apartments is not None` | provenance |
| `elements.<e>.area_in_m2` | float, m² | `<e>_area_in_m2` (`facade_area_in_m2` for the wall), `<e>` ∈ floor, wall, roof, window, door | all |
| `elements.<e>.u_value_in_watt_per_m2_per_kelvin` | float, W/(m²K) | `element_values[<e>].u_value_in_watt_per_m2_per_kelvin` | all |
| `elements.<e>.u_value_origin` | string | `element_values[<e>].u_value_origin`, verbatim | provenance |
| `elements.<e>.adjustment_factor` | float | `element_values[<e>].adjustment_factor` (1, 0.5 or 0) | situation rule (design §7.2) |
| `windows_by_direction.<d>` | float, m² | `scaled_window_areas_in_m2`, a list in the order of `WINDOWS_DIRECTIONS` (South, East, North, West, Horizontal), written as a mapping by those names; `<d>` ∈ south, east, north, west, horizontal | all |
| `glazing.g_value` | float | `total_solar_energy_transmittance_for_perpedicular_radiation` | all |
| `glazing.frame_factor` | float | `reduction_factor_for_frame_area_fraction_of_window` | IE, DE |
| `glazing.non_perpendicular_factor` | float | `reduction_factor_for_non_perpedicular_radiation` | DE |
| `glazing.external_shading_factor` | float | `reduction_factor_for_external_vertical_shading` | IE, DE |
| `thermal_bridging_surcharge_in_watt_per_m2_per_kelvin` | float | `thermal_bridging_surcharge_in_watt_per_m2_per_kelvin` | information |
| `site.climate_region` | string or `null` | the run's weather configuration (`climate_region`, added to HiSim by the requester) | AT |
| `site.elevation_in_m` | float or `null` | the run's weather configuration (`elevation_in_m`) | AT |
| `configured_fields` | list of strings | `BuildingConfig` override fields that are not `null` | provenance |

Floats as YAML floats without rounding. Additive fields keep version 1; a removed or renamed field
or a changed meaning is version 2, announced here first.

### 2.4 Consumer rules

1. The calculator reads `building.expanded.yaml` only; it never opens the realized record's
   `Building` configuration itself and holds no knowledge of the database HiSim builds from.
2. A missing file or another major version refuses, naming the file (E-02, E-04).
3. Unknown fields are ignored; a field of §2.3 that is missing or not finite refuses by name (E-05).
4. Each element's situation follows from `adjustment_factor` and the element class by design §7.2;
   a factor other than 1, 0.5 or 0, and 0.5 on a window or door, refuses (E-06).
5. A `windows_by_direction` block with every area 0 while the window element has an area refuses
   (E-08); `dwelling_units.value` 0 refuses (E-07).
6. The origin strings, `configured_fields` and `hisim_building_code` pass into the result's
   `provenance` unchanged and uninterpreted.

### 2.5 Tests on both sides

- **Producer (HiSim, `tests/test_building_expanded_record.py`):** the block equals a freshly built
  `BuildingInformation` attribute by attribute for the default building, an Austrian one with a
  configured floor area and an overridden U-value, and one with factor 0.5 on the floor; every number
  is a plain float; `write_records()` writes the file beside the record.
- **Consumer (T-3):** fixture files written by HiSim, unedited, and committed under `epc/tests/fixtures/`
  are read into building descriptions that equal them exactly (I-5).

### 2.6 Example (illustrative values; the U-values are placeholders)

```yaml
# building.expanded.yaml — Generated by HiSim 1.2.4 — do not hand-edit
schema: building-expanded/1
producer: HiSim 1.2.4
buildings:
  - name: Building
    hisim_building_code: AT.N.SFH.04.Gen.ReEx.001.001
    building_type: detached
    conditioned_floor_area_in_m2: 115.0
    gross_volume_in_m3: null
    heat_capacity_class: medium
    dwelling_units: {value: 1, configured: false}
    elements:
      wall:   {area_in_m2: 179.94, u_value_in_watt_per_m2_per_kelvin: 0.25, u_value_origin: configured, adjustment_factor: 1.0}
      roof:   {area_in_m2: 143.75, u_value_in_watt_per_m2_per_kelvin: 0.80, u_value_origin: "area-weighted", adjustment_factor: 1.0}
      floor:  {area_in_m2: 143.75, u_value_in_watt_per_m2_per_kelvin: 1.00, u_value_origin: "area-weighted", adjustment_factor: 0.5}
      window: {area_in_m2: 21.61,  u_value_in_watt_per_m2_per_kelvin: 2.50, u_value_origin: "area-weighted", adjustment_factor: 1.0}
      door:   {area_in_m2: 2.1,    u_value_in_watt_per_m2_per_kelvin: 2.00, u_value_origin: "row value", adjustment_factor: 1.0}
    windows_by_direction: {south: 3.35, east: 5.03, north: 6.09, west: 7.14, horizontal: 0.0}
    glazing: {g_value: 0.65, frame_factor: 0.7, non_perpendicular_factor: 0.9, external_shading_factor: 0.6}
    thermal_bridging_surcharge_in_watt_per_m2_per_kelvin: 0.1
    site: {climate_region: S/SO, elevation_in_m: 356}
    configured_fields: [facade_u_value_in_watt_per_m2_per_kelvin]
```

## 3. Part 2 — the site block

The site is part of the building's block. HiSim's writer copies it from the run's weather
configuration, where the requester adds the fields to HiSim outside this work (requirements D23); a
run at another site is another realized record. The calculator has no site file and no site
argument.

| Field | Engine | Type | Admissible values |
|---|---|---|---|
| `site.climate_region` | AT | string | `W`, `NF`, `N`, `ZA`, `SB`, `S/SO`, `N/SO` (ÖNORM B 8110-5:2024 Bild 1) |
| `site.elevation_in_m` | AT | number | finite, ≥ 0 |
| a climate zone | ES | string | joins the block when the Spanish engine is specified (additive, version 1); the CTE DB-HE zone |
| — | IE, NL, DE | | none read: one national or reference climate (DEAP 4.2.7 is location-independent; the others confirmed when specified) |

An engine reads the fields its method needs; a field it needs that is `null`, or a value outside
the admissible set, refuses naming the field and the set (E-09, E-10); fields it does not read are
ignored, so an Irish record may carry an empty block.

## 4. Part 3 — the country

The country is an explicit argument (`--country AT`), ISO 3166-1 alpha-2. Neither the realized
record nor the expanded record carries a country field; the calculator does not infer it.
