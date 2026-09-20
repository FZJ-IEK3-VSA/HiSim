# RenoVisor translation layer — the challenges, counted against the sources of 2026-09-15

**Status:** working inventory for the first code draft
**Date:** 2026-09-15
**Sources:** `~/renovisor-api-contract` at `eeb49b5` (`openapi.yaml` v0.3.0-draft, `measures.yaml`)
and its `materials` branch at `a79f13d` (`materials.yaml`, 26 materials) · this branch's
`roadmap/renovisor/{requirements,measures_v2_requirements,field_inventory}.md` and
`mockups/` (deleted in step 9; in git history) · HiSim `main` at `4612b899` for everything that
names code
**Purpose:** one list of what has to be solved or decided before and while the translation layer
is written. Requirement ids (R/A/M/N/Q) are the ones in the two requirements documents; this file
adds nothing to them, it orders them by the work and adds what the contract repository and the
materials dump revealed since.

---

## 1. The three sources disagree with each other

| Artifact | Where | State |
|---|---|---|
| `openapi.yaml` | contract repo `main` | v0.3.0-draft of 2026-08-27. Still `HeatPump`/`GasSolarThermal` spelling, `"Conventional Radiator"` strings, flat `Measure`, no `ventilation_system` block, no `worst_window_glazing_panes`, no `hvo_heating`, no `km_per_year`, no `measure_id`. |
| `openapi.yaml` | this branch, `roadmap/renovisor/` | Same file plus two `SUPERSEDED` stubs on `Measure` and `Schedule` (45-line diff). Nothing else. |
| `measures.yaml` | contract repo `main` and verbatim here | Cites contract fields that exist in **neither** copy of `openapi.yaml`: snake_case heating values, `surface_heating`, `worst_window_glazing_panes`, the `ventilation_system.system` vocabulary. It was written against a contract revision that was never made. |
| `measures.schema.yaml` | this branch, mockup | The 14-variant discriminated union. Superseded by the catalogue, but its footer decisions F4 (vehicle ids), F5 (five thermal elements), F6 (ventilation + air-conditioning blocks) are still the agreed answers and have not been written into any `openapi.yaml`. |
| `materials.yaml` | contract repo `materials` branch | Spreadsheet export, not a schema-conforming resource. See §3. |

**Challenge C1 — one `openapi.yaml`, in the contract repo, that the catalogue is consistent with.**
The update has to fold in, in one PR against `climatemedia/renovisor-api-contract`:

- the payload items of `requirements.md` §8.2 that are the backend's to make: A2 (drop or mark the
  8 consumer-less fields), A4 (weather basis in the result), A5 (`vehicles[].km_per_year`), A9
  (`Range` as one slot; rename `normal`), A10 (`self_sufficiency_in_percent` is a number), A12 +
  A15 (mock labels and a calculation version on the result), A13 (remove
  `property_value_increase_in_percent` or find an owner), A14 (boundary and period on
  `energy_demand_kwh` / `emissions_kg_co2`), A16 (refusal reason code + field paths), A21 (the
  nine-to-five element mapping written down);
- the inventory changes the catalogue and the mockup footer presuppose: snake_case values for
  `heating_system.system` (11 values, `hvo_heating`, `biomass_heating`, `hybrid_heat_pump` added,
  the two `*SolarThermal` values removed) and `heat_distribution_system` (`surface_heating`,
  `low_temperature_radiator`, `conventional_radiator`); a `ventilation_system` block and an
  `air_conditioning_system` block under `energy_system_config` (F6); `worst_window_glazing_panes`
  on the building; the cavity width as an inventory fact (N7); `vehicle_id` on vehicles (F4/A24),
  or an explicit statement that measures never name a vehicle;
- a `Measure` schema that matches the catalogue's shape (§2), replacing the flat draft;
- a `Schedule` whose phases reference measure ids (A22);
- a `Material` schema that can carry what `materials.yaml` has (§3).

**Challenge C2 — delete the HiSim copy without losing the tests that need it.**
`measures_v2_requirements.md` §7.5 check 4 ("every inventory path a measure writes exists in
`HomeInventoryInput`") and `requirements.md` AC4.1/AC4.3 validate against the OpenAPI schema at
test time. Once `hisim/renovisor/contract/openapi.yaml` is gone, HiSim needs a way to see the contract
in CI. Options: (a) a pinned copy under `hisim/renovisor/contract/` refreshed by a script that
records the contract commit hash and fails when the two differ; (b) a git submodule; (c) fetch
in CI. (a) is the only one that keeps `pytest` offline and makes "which contract revision does
this HiSim speak" a committed fact. The same choice applies to `measures.yaml` and
`materials.yaml`, which the registry tests read (§7.5 checks 1–3). Decision needed.

**Challenge C3 — the vocabularies cross a naming boundary that both sides keep moving.**
The contract's field set is a façade over HiSim names (Q3, recommendation b, confirmed by N6).
HiSim renames config fields on every P4 batch until P5 freezes them (C9); RenoVisor re-spells
enum values (`Heat pump` / `heat_pump` / `HeatPump` all occur across the three files). Every
value map lives in exactly one place — the bindings table — and a CI check resolves every binding
in every base file (§7.5 check 5). Nothing else in HiSim may know a contract spelling.

## 2. The measure model is not yet a JSON shape

The catalogue is a list of `display_name` + `options[{name, value_type, values}]`. Nothing says
how a *package* names a measure and its chosen option values in a request body. Three things are
missing before `PackageDefinition.measures` can be written into the contract:

**Challenge C4 — ids.** `display_name` is a label that will be translated (N1). Measures need a
slug id, enum values need slug ids, option names need one spelling convention (N2: `thickness_in_mm`
beside `type of system` and `size in percent of roof area`). Proposed: derive slugs
mechanically from the catalogue now (`external_insulation`, `type_of_system`, `heat_pump`) and
write them into `measures.yaml` as `id:` fields so the derivation becomes the committed truth.

**Challenge C5 — the request shape.** Proposed:
`{measure_id, options: {<option_id>: <value>}}`, closed vocabulary, unknown ids rejected (A6.1).
`measure_id` alone identifies a *kind* of measure; a package may contain the same kind twice only
where the catalogue says so (two insulation layers on one wall are two *different* measures,
`external insulation` and `internal dry lining insulation`, so kinds are unique per package — to
be stated). The content hash of a package is then the sorted list of these objects (A6.5, AC4.2).

**Challenge C6 — totals or additions.** `photovoltaic system: size in percent of roof area` and
`electric vehicle: number` on a house that already has PV or a car (N8). A6.3 says post-measure
totals; the catalogue is silent. Decide and write into the option descriptions.

**Challenge C7 — stage-0 measures.** The catalogue lets the existing building be expressed as
measures. HiSim must read only the inventory for the base state (M9); the contract should say a
`PackageDefinition` carries stage ≥ 1 measures only.

**Challenge C8 — `dependencies.yaml` does not exist.** It decides which combinations are
reachable (§5.4 of the v2 document) and whether HiSim must refuse the physically contradictory
ones itself (three floor constructions, five floor measures). Until it exists, HiSim refuses
nothing on those grounds and reports what it composed (N5). Working assumption for the draft:
HiSim trusts the caller and composes.

## 3. The materials dump is data, not yet a database

26 materials, 12 material strings in the catalogue, and they do not meet:

| Catalogue string | In `materials.yaml`? |
|---|---|
| `EPS`, `EPS Foam` | yes — `polystyrene_eps_rigid_board` (and `eps_beads_cavity` for cavities) |
| `XPS` | yes — `extruded_polystyrene_xps` |
| `Mineral wool`, `Mineral Wool`, `mineral wool` | ambiguous — `stone_wool_flexible_insulation_blankets` or `metac_glasswool`; both are mineral wool |
| `glass wool` | yes — `metac_glasswool` (a product name as the material) |
| `wood fiber` | yes — `wood_fiber_rigid_board` |
| `open cell spray foam` | yes — `open_cell_spray_foam` |
| `PIR` | **no** |
| `Thermal Laminate drylining board` | **no** (a product: plasterboard + PIR or EPS) |
| `Liquid Insulation` | **no** (probably one of the three insulating screeds, or spray foam) |

**Challenge C9 — the material vocabulary has to be closed on one side.** Either the catalogue's
`material` options become `asp_id`s from the database (and the three missing materials get
rows), or HiSim keeps an alias table from catalogue strings to `asp_id`s with three entries
that point at a HiSim-owned fallback row. The `/materials` contract schema
(`{id, name, description, category, image_url}`) must at least carry `asp_id` as `id`; whether
it also carries conductivity decides Q-N2 (who owns the U-value derivation).

**Challenge C10 — the numbers need a normalisation layer with provenance.** Found in the dump:
`Thermal conductivity (AVERAGE)` is present for all 26 rows and is the one column that is
consistently numeric. `Heat capacity (AVERAGE)` for XPS is `0.035` (a conductivity, not a heat
capacity); for `eps_beads_cavity` it is the string `120-1401-0`. Density is a string range in
20 rows. Investment costs are `{min, max}` in €/m³ for IE/ES/NL but with `'n'` (no data) and
`'41-80€/m2'` as values. Lifespan is `≥ 50`, `40-75`, `None`. Footnotes with the caveats are
deliberately not exported. A `materials.py` that reads this dump must (a) take only λ, lifespan,
€/m³ min/max, CO₂ per m² and the component vocabulary, (b) parse ranges into `(low, high)`
explicitly, (c) record `source` per row from the `Sources` field, and (d) fail loudly on the
rows it cannot parse instead of defaulting. Proposed: a one-off import script writes a small,
typed, checked-in `insulation_materials.json` in HiSim from the dump, with the dump's commit hash
in its header; the dump itself is not read at runtime.

**Challenge C11 — the database's element vocabulary is a fourth one.** `materials.yaml` uses 14
application areas (`External wall: internal`, `Roof: betw. rafter`, `Basement floor and walls
(outside)`, …). The catalogue has 15 envelope measures, the contract's `condition_assessment`
has 9 elements, `envelope_details` has 5. The registry maps measures onto the 5 (F5); the
materials' 14 areas are only needed to check that a material is *offered* for a measure, which is
the catalogue's job, not HiSim's. HiSim reads λ, lifespan and price and ignores the areas —
stated so nobody adds a fifth mapping.

## 4. Turning options into physics

**Challenge C12 — the U-value derivation (M1, §5.1 of the v2 document).** Five inputs, none of → hisim-4g9.5
which exists in HiSim: λ per material (from §3), a default thickness per measure where the
Experts option is absent (11 defaults with a source, M7), the element's current U-value (TABULA
row or `envelope_details`, precedence A8 undecided), the formula `U_new = 1/(1/U_old + Σ d/λ)`
with a decision on a thermal-bridge surcharge, and a `glazing_panes → U` table for windows
(2, 3 panes) and doors (0, 2, 3) per country. Three measures carry no options at all (`add
internal dry lining`, `basement internal insulation`, `top floor ceiling insulation`) and need a
fixed default build-up each. `cavity wall insulation` has a thickness that is a survey fact (N7)
and no material; until the inventory carries the cavity width, the measure needs a default cavity
and `eps_beads_cavity` as its material.

**Challenge C13 — composition per element (M2).** Insulation adds resistances; windows and doors
replace. Accumulate `element → (baseline U, [ΔR])`, resolve once, write once. The floor's
transmission factor becomes a fixed 0.5 when a floor U-value is configured
(`building/information.py`) — check against a suspended floor over a ventilated crawl space.

**Challenge C14 — relative sizes (M4, M5).** `size in percent of roof area` → watts needs a
usable-roof rule (roof form, orientation share, W/m²); `days to cover` → kWh needs an a-priori
daily demand (which loads, which day). Both are sizing laws with named inputs, both reported
`approximated`, both resolved *before* the inventory is written so the post-measure inventory is
in watts and kWh (A6.4). A11 (stated size beats law) becomes required.

**Challenge C15 — two-field writes and measures with no model.** `change room temperature` → hisim-4g9.3, hisim-4g9.6, hisim-epc.21
writes the building set-point and the HDS controller set-point (M11). Nine measures have no
model and are reported, not dropped (M8); three of them become cheap once `BuildingConfig` gains
optional infiltration, shading and air-change/heat-recovery fields (M3). `hot water system:
Direct electric` needs a component that does not exist.

**Challenge C16 — the eleven heat generators.** Five are `generic_boiler` presets today. → hisim-epc.19, hisim-epc.20
`hvo_heating` is a new fuel + preset (M12). `biomass_heating` needs a decision (alias to pellets
or ask). `heat_pump`, `electric_heating`, `district_heating` wait for P4 B1 presets.
`hybrid_heat_pump` has no component. The `heating system` measure is also the one measure that
changes *which base file runs* (`SelectBaseFile` effect).

## 5. From inventory to a runnable energy-system file

**Challenge C17 — what a base file is, today.** `[decided 2026-09-15, Q18]`
Checked against `origin/main` at `497fa421` (not the morning snapshot): the recorded
`energy_systems/household_*_building_sizer.grouped.energy_system.yaml` files *are* the base
files. Eleven of them exist (gas, oil, pellets, wood chips, hydrogen, heat pump, electric,
district, gas + solar thermal, heat pump + solar thermal, heat pump + car). They carry presets
throughout (`rooftop` PV with a roof-area law, `sized_to_pv` battery, `buffer` and `standard`
storages, `building_derived` heat distribution), so they are auto-sized through the P4 laws, and
each carries an `electricity_management` variant (`ems_with_battery` / `metered_directly`).
Decision: use them as they are. Selection is a lookup over generator × solar thermal × car; any
combination without a file is a refusal with a reason code (solar thermal with oil, pellets,
wood chips, hydrogen, electric or district heating; a car with anything but a heat pump; two or
more cars; a DHW heat pump on a boiler house; HVO and hybrid until a file exists). Changes to
these files that RenoVisor motivates — a PV group, a solar-thermal variant — are energy-system
changes under the golden gate, never a RenoVisor-owned second version.

**Challenge C18 — the switches the recorded files offer.** `[decided 2026-09-15, Q17, Q18]`
Battery: the `electricity_management` variant. Solar thermal and the car: file selection. PV:
`PVSystem` is present in every file with the `rooftop` preset; the catalogue's
`size in percent of roof area` is that law's share input, so the PV measure is an override of
an existing law, and Q16's roof form becomes an extra input to it. Still to read off the files:
how "no PV" is expressed (probably a zero share), and which of the catalogue's three
`supplies` values the recorded solar-thermal wiring corresponds to — the other two are
refusals. CI: every reachable file × variant combination loads, validates and resolves; a
sampled subset is simulated over one day; an "expected feeds per variant" assertion is a
request to the energy-system side, because a dropped feed is silent today.

**Challenge C19 — component names across base files.** `[decided 2026-09-15, Q18]`
The recorded keys are consistent across the fleet for everything but the generator:
`Building`, `Weather`, `UTSPConnector`, `PVSystem`, `HeatDistributionController`,
`HeatDistributionSystem`, `DHWStorage`, `SimpleHotWaterStorage`, `ElectricityMeter`,
`Battery`, `L2EMSElectricityController`. Generators differ per file (`CondensingGasBoiler`,
`MoreAdvancedHeatPumpHPLib`, `ConventionalPelletBoiler`, …). Bindings address the recorded
keys directly: fleet-wide for the common components, per generator for the rest. §7.5 check 5
resolves every binding in every file it applies to.

**Challenge C20 — location and archetype.** `country_code` → weather `for_location` (IE = Dublin
NSRDB 2019, the only Irish dataset). `tabula_building_type` + `construction_year` +
`retrofit_status` → TABULA code; IE has `SFH`/`TH`/`AB` only, `MFH` is a refusal (UC3, AC7);
rows with zero door/window area crash `Building` (C6 of the requirements) and need the v1
workaround until fixed. `hisim/renovisor/tabula_ie.py` already does the age-band lookup and the
workaround and is worth keeping.

**Challenge C21 — occupancy.** `residents_count/type/employment_status` → nearest of 66 LPG
households, reported `approximated` (A3). In a container the profile must come from a local LPG
binary or from predefined profiles; UTSP is not an option offline. `km_per_year` (A5) has no
HiSim consumer yet — the car's distance comes from the travel-route set.

## 6. Returning a result

**Challenge C22 — KPIs.** 109 named KPIs exist in `kpi_preparation.py`; 3 of the contract's 13
fields map to one of them, and each needs its boundary and period stated (A14). The other ten are
constants labelled `mocked` in the result (A12, AC8.1). A `kpis.py` that maps HiSim KPI names to
contract fields is a small table plus the label mechanism — and the KPI *names* are HiSim
internals that P4 may rename, so the same CI resolution check as for bindings applies.

**Challenge C23 — costs.** The economics engine exists and Irish device prices, energy prices
and escalation defaults have landed since the August survey (`devices_IE.json`,
`energy_prices_IE.json`, `escalation_defaults_IE.json`). Still missing: an Irish subsidy
catalogue (`subsidy_catalog/IE.json`; `GrantScheme` names three SEAI selections), an Irish
tariff (`cost_database/tariffs/` has one German file), and any model for
`property_value_increase_in_percent` (A13). `Range` must be one slot (A9, AC9). Material prices
from §3 (€/m³ min/max by country) feed the insulation measures' investment cost and need the
thickness and area to become €.

**Challenge C24 — the translation report and `errors.json`.** Every leaf field of the
configuration exactly once, one of five statuses (R7); every measure once, with the rule that
produced its number or the reason code for no effect. Failures as a structured file with reason
codes from a closed catalogue, distinguishing validation error, refusal and crash (R11, R13.2).
The v1 `MappingReport` class is a usable starting point for the field half.

## 7. Running inside a reused container

**Challenge C25 — reuse safety (C13).** `SingletonSimRepository.reset()` and → hisim-epc.23
`ResultPathProviderSingleton.reset()` exist and nothing on a production path calls them; module
caches (weather, TABULA, pvlib) are unsurveyed. One reset point at the start of a calculation,
and AC11.1's differing-sequence identity test.

**Challenge C26 — write only into the output location (R13.4).** Two defaults write into the → hisim-epc.22, hisim-hi1.8
repository: `ResultPathProviderSingleton` (`<repo>/results/`) and the input cache
(`hisim/inputs/cache/Building_<hash>.cache` is on disk on `main` right now). The cache is
content-keyed and deterministic, so keeping it *across* calculations is fine and even wanted,
but its location must be an explicit, declared directory, not the working tree.

**Challenge C27 — determinism and the version stamp (R10, R14).** No timestamp, job id or
machine name in emitted files; a stamp covering library, base files, component models and cost
database, the image digest being the candidate. The realized record and the parametrised file
written before the first timestep (R16).

## 8. The existing code and where the new code goes

**Challenge C28 — `hisim/renovisor/` is the v1 translator.** Seven modules, six test files,
targeting the v1 camelCase contract and `ModularHouseholdConfig` (still present on `main`,
scheduled for deletion at P5). Its `spec.md` declares result derivation out of scope. Replace,
keeping `tabula_ie.py` and the shape of `MappingReport`; delete the wrapper envelope, the
uploader and the CLI's upload path (the container writes files, nothing POSTs). The v1
`examples/*.json` are the old contract and go with it.

**Challenge C29 — the package layout for maintainability and expandability.** The v2 document's
§7 already fixes the shape: measures never name HiSim fields; bindings never contain physics;
effects are a closed set of frozen dataclasses checked by `assert_never`; every constant carries
a source; five CI checks turn catalogue drift and P4 renames into failing builds. What the first
draft has to add to that shape:

- a **catalogue loader** that reads `measures.yaml` into typed `MeasureSpec`/`OptionSpec` and is
  the *only* reader of the catalogue (so N2/N3 normalisation happens once);
- an **`Options` reader** that validates a request's option values against the spec, applies the
  Experts defaults and emits the `defaulted` report lines;
- the **effects accumulator** (`AddThermalResistance`, `SetUValue`, `SetInventoryField`,
  `SelectVariant`, `EnableGroup`, `SelectBaseFile`, `NoEffect`) and its **resolver**;
- **`materials.py`** reading the typed JSON of C10;
- the **inventory model**: a typed view of `HomeInventoryInput` with path-addressed reads and
  writes so measures and bindings share one path vocabulary, validated against the pinned schema;
- **`bindings.py`** and a **parametriser** that loads a base file, applies overrides, variant
  selections and group flags, and asserts the R4/M10 diff rule against the base;
- **report** and **errors** writers;
- a **`calculate`** entry point: configuration + parameters in, files out, one reset at the top.

## 9. Decision register — interview of 2026-09-15

Every item was decided by the owner in review; `[proposed]` defaults that were overruled are
noted. Ids Q1–Q28 are the interview's; C3 and V1–V3 preceded it.

### Naming and the contract

| Id | Decision |
|---|---|
| C3 | **Everything in HiSim spelling**, meaning the energy-system file's wire vocabulary: enum values are UPPER_SNAKE member names (`FLOORHEATING`, `HEAT_PUMP`), field names are the config field names with units spelled out (`_in_watt`, `_in_kilowatt_hour`, `_in_euro`), plain strings stay plain (`medium`), LPG references keep the LPG's `Name`. The contract's conventions block changes accordingly (field names lowercase, enum values UPPER_SNAKE, money `_in_euro`). Vocabularies HiSim lacks (heat generator, ventilation type, DHW supply, solar-thermal supplies, retrofit status, TABULA type) are defined once in `hisim/renovisor/vocabulary.py` and the contract's enum lists are generated from it. Where a HiSim field name breaks HiSim's own convention (`custom_battery_capacity_generic_in_kilowatt_hour`, `area_m2`, `azimuth`), P4 renames HiSim and the contract adopts the clean name. Material ids are the database's `asp_id`; KPI and cost result fields follow only the unit convention. Cost accepted: every HiSim rename after P5 is a public contract break. |
| Q1 | Measure request shape is **generic**: `{"measure_id": "EXTERNAL_INSULATION", "options": {"material": "<asp_id>", "thickness_in_mm": 140}}`. One contract schema; option sets validated against the catalogue by the backend and by HiSim. Ids: measure ids UPPER_SNAKE, option ids snake_case, option enum values UPPER_SNAKE, materials `asp_id`; written into `measures.yaml` as `id:` fields. |
| Q2 | PV size and vehicle count are **post-measure totals** (A6.3); the report carries old and new so costing prices the difference. |
| Q3 | Contradictory packages: **inventory facts plus an exclusivity table**. `HomeInventoryInput` gains `floor_construction` and `wall_construction`; HiSim refuses a measure that does not fit the building or clashes with another on the same element. Trust-the-caller only for what neither covers. |
| Q4 | A stage-0 measure in a package is a **validation error, fail hard**. The contract states packages carry measures to be applied only; the base state comes from the inventory alone (M9). |
| Q28 | Contract files (`openapi.yaml`, `measures.yaml`, `materials.yaml`) reach HiSim as a **vendored copy for now**, under `hisim/renovisor/contract/`, with the contract commit recorded and a drift check. Where the master version lives is a project discussion still to be had; the installable-package option is on the table. |

### Materials

| Id | Decision |
|---|---|
| Q5 | Materials data enters HiSim through a **one-off import script** producing a checked-in typed `insulation_materials.json` (λ, lifespan, cost, CO₂ per m², sources, dump commit hash), failing on unparseable rows. A clean export is requested from the materials team alongside. |
| Q6 | PIR, thermal laminate drylining board and liquid insulation: **blocked until the database has rows**. Those option values are refused; no HiSim fallback rows. |
| Q7 | "Mineral wool": **left to the catalogue owners** when they write the `asp_id`s. HiSim does not guess. |
| Q8/Q9 | Envelope measures are priced from the **materials database only**, which gains a **total-cost column owned by the materials team** (labour and system parts included). HiSim estimates no envelope costs and does not use its 13 AI-estimate envelope entries for RenoVisor. |
| Q13 | Window and door U-values and prices per pane class are **materials database rows**; `glazing_panes` values become `asp_id`s. Both measures are blocked until the rows exist. |

### Physics

| Id | Decision |
|---|---|
| Q10 | The current U-value: **inventory wins per element**, TABULA otherwise; the report says which. Same rule for the base simulation (settles A8). | → hisim-4g9.5
| Q11 | Missing thickness is **target-driven, no thermal-bridge surcharge**: the thickness that reaches the Irish regulatory target U-value for the element from `U_old` and λ, rounded up to the material's increments; targets as a sourced table; the simplification is stated in the report. |
| Q12 | Battery `days_to_cover` is sized on **household load plus heat pump plus EV**: LPG profile annual sum / 365, plus the TABULA annual heating demand / an assumed seasonal heat-pump efficiency, plus mileage × consumption. Stated in the report as `approximated` with the law's name. |
| Q14 | `HYBRID_HEAT_PUMP` and `HOT_WATER_SYSTEM: DIRECT_ELECTRIC` are **refused** with a reason code; `BIOMASS_HEATING` is **simulated as pellets**, reported `approximated`, with a request to the owners to drop the value. | → hisim-epc.20, hisim-epc.21
| Q15 | The three optional `BuildingConfig` fields (infiltration, shading, air change / heat recovery) are **not built for the MVP**; `OUTSIDE_SHADING`, `SHALLOW_AIR_TIGHTNESS_MEASURES`, `DIY_SEALING_OF_AIR_LEAKS`, `VENTILATION_SYSTEM` stay `NoEffect`, reported. | → hisim-4g9.6
| Q16 | PV sizing reads **roof form and orientation from new inventory fields**, with TABULA roof geometry as the fallback. Contract change. The rule is the existing `rooftop` law with an added input, not a new law. |

### Base files and the container

| Id | Decision |
|---|---|
| Q17 | Variant coverage: **resolve every combination, simulate a sample** over one day; request an expected-feeds assertion in the format. |
| Q18 | Base files are the **recorded grouped files as they stand**; selection is a lookup table; missing combinations are refusals; additions only through the golden gate (details in C17–C19). |
| Q19 | Zero-area TABULA rows: **keep the v1 workaround** (nearest usable age band, reported with the substituted code); the `Building` fix is a separate scheduled item. | → hisim-epc.18, hisim-4g9.1
| Q20 | Occupancy profiles: **local LPG executable in the image plus a warm content-keyed cache**. |
| Q25 | A **cache directory is mapped into the container**; HiSim's simulation parameters accept a **list of cache directories**. The cache is exempt from the output-location cleanliness rule. | → hisim-epc.22
| Q26 | The calculation version is the **image digest only**, passed in by the caller and echoed in the result. |

### Results

| Id | Decision |
|---|---|
| Q21 | Provenance is **per-field objects**: every KPI and cost value is `{"value": …, "provenance": "SIMULATED" | "MOCKED" | "PARTIAL"}`. |
| Q22 | `energy_demand_kwh` is **delivered energy summed over carriers per year**; `emissions_kg_co2` is **operational CO₂ per year**; the package's **embodied CO₂ is a new separate field** fed from the materials table. |
| Q23 | HiSim computes the **full `Costs` block**: envelope investment from the materials totals, device investment from `devices_IE.json` labelled `PARTIAL` until reviewed, everything else from the economics engine. |
| Q24 | Irish grants: **build `subsidy_catalog/IE.json`** for HiSim's subsidy solver, sourced and dated. |

### Code and documents

| Id | Decision |
|---|---|
| Q27 | `hisim/renovisor/` is **revised, not deleted**: keep `tabula_ie.py`, the `MappingReport` class (gains `non_simulation` and per-measure lines), the selection-table shape, the occupancy and weather resolution; remove `measures.py`, `schema.py` (validation moves to the vendored OpenAPI schema), `runner.py`, `uploader.py`, `spec.md`, `how_to_use.md`, `examples/`; rewrite `__main__.py` as one `calculate` command; keep the TABULA tests, rewrite the rest. |
| V1 | The translation process is visualised by a **generated HTML page**, `roadmap/renovisor/translation_map.html`: a flow map over all 33 measures (measure → effect → inventory path → base-file component and field → switch) with status colour and hover highlighting, and a worked-example trace (inventory + package → post-measure inventory diff → parametrised YAML diff → report). Single file, inline SVG/CSS/JS, no external libraries; produced by a CLI verb; a test asserts the committed file is current. |
| V2 | The map's decision overlay reads decision ids from **docstrings on the registry functions**. |
| V3 | The map shows **display names beside the HiSim member names**, so it can accompany the contract PR for the frontend team. |
| — | Work stays on the **renovisor-backend branch**; `main` is merged into it, conflicts under `hisim/` resolved to `main`'s versions, the branch's stale P2 test copies removed. |

## 10. Order of work after the interview

1. Merge `main` into `renovisor-backend`; confirm the energy-system suite is green.
2. Vendored contract copies under `hisim/renovisor/contract/` with the commit recorded and a drift check (Q28).
3. `vocabulary.py`; catalogue loader; `Options` reader with Experts defaults; effects and resolver; the materials import script and its typed table (Q5); the registry with all 33 measures; the five §7.5 checks. No simulation.
4. The map generator and the committed `translation_map.html` (V1–V3).
5. Selection table over the recorded grouped files (Q18); bindings on the recorded keys; parametriser with the R4/M10 diff assertion; one end-to-end run over a day for a gas-heated house with envelope measures; the trace page.
6. The contract PR: enum lists generated from `vocabulary.py`; generic `Measure`; ids in `measures.yaml`; inventory additions (`floor_construction`, `wall_construction`, roof form and orientation, `ventilation_system`, `air_conditioning_system`, `worst_window_glazing_panes`, `km_per_year`); provenance objects on KPIs and costs; `embodied_co2_kg`; the materials `Material` schema with `asp_id`, conductivity and total cost; requests to the owners (`BIOMASS_HEATING`, mineral wool, the three missing materials, window and door rows, total-cost column).

## 11. Findings from the implementation of steps 3–6 (2026-09-15)

Facts the code surfaced that were not visible from the documents. Each names who has to act.

| # | Finding | Action |
|---|---|---|
| F1 | A recorded value on a sized field **pins** it: keeping the recorded `Building.weather_identity` on a Dublin house kept Aachen's solar-gain key. The parametriser removes `weather_identity` and `Weather.location`; the diff rule allows exactly those removals. | Done in step 5. Any future constructor swap must list the recorded keys it invalidates. |
| F2 | The legacy KPI path has emission and price factors for `DE` and `AT` only (`hisim/components/configuration.py`, `EmissionFactorsAndCostsForFuelsConfig`); `simulation_parameters.country = "IE"` crashes `COMPUTE_KPIS`. The lifecycle engine prices against the Irish files via attached `EconomicParameters`, so costs are Irish but `emissions_in_kg_co2_per_year` uses German grid factors and is labelled `PARTIAL` with the country named. | **Data task:** Irish rows for that table, sourced (SEAI/CRU emission and price factors). | → hisim-l07.14
| F3 | `HISIM_IN_DOCKER_CONTAINER` makes `postprocessing_main.py` whitelist post-processing options, and `COMPUTE_LIFECYCLE_COSTS` is not on the list: a container with that variable set produces a `result.json` whose whole `costs` block is `missing`. | **HiSim change** before the container ships. Decided 2026-09-19: the override is removed altogether rather than widened; the simulation file is authoritative in a container too (the allow-list dated from the 2022 UTSP workers and blocked every option added since). |
| F4 | `greenfield_net` applies a flat subsidy shim when no country catalogue exists; on the Irish example it booked 4 626 € of "support" from a German-shaped percentage. The payload reads `greenfield_gross` and publishes a grant only from a real catalogue. | Nothing further; `subsidy_catalog/IE.json` (step 6b, Q24) activates the grant without a code change. |
| F5 | Embodied CO₂ of the example is **negative** (−2 224 kg): the materials dump gives wood fibre −174.8 kg CO₂-eq./m³ with biogenic storage netted in at A1–A3. | **Question to the materials team:** does `embodied_co2_in_kg` want storage netted in or reported separately (the dump has both columns)? | → hisim-l07.10
| F6 | Two recorded files lack components some inventory blocks bind to: district and electric heating have no `SimpleHotWaterStorage`; electric heating has no `HeatDistributionController`. Base-state inventory values for those report `ignored`; a measure needing them is a refusal. | Accepted behaviour (Q18: additions only through the golden gate). |
| F7 | The battery law's household term is the mean over the simulated period, not annual/365, so that the estimate warms the very LPG cache entry the run then uses; identical for a full-year run. | Accepted; the rule string names the period. Revisit if a winter-day run should not size a battery. |
| F8 | The report-line bug: measures writing no line of their own shifted later measures' lines (PV and battery had none). Fixed in step 5, with the test that had hidden it corrected. | Done. |
| F9 | Two LPG catalogue names cannot be read into a household composition (`CHR06 Jak Jobless`, `CHR20 …`) and `CHR52 Student Flatsharing` reads as one adult; excluded or documented rather than guessed. | Decide whether to widen the phrase rules. | → hisim-epc.25
| F10 | `PVSystem` is in every recorded file; "no PV" is a pinned `power_in_watt: 0`, which the parametriser drops as stale when a PV measure writes a roof share. Solar-thermal files wire the collector to DHW only, so `DHW_ONLY` is the one supported supply mode — a fact now, not a PROVISIONAL. | Done; `base_files.py` updated. |

## 12. Course correction of 2026-09-15 (evening): the input contract is rewritten, not aligned

**Owner's clarification.** `openapi.yaml` v0.3 is an older draft written before the energy-system
redesign, against the config object the redesign deletes. It is to be revised and rewritten, not
aligned in place. The v1 translator's implicit contract (`scripts/hisim_spec.md`) had the right
philosophy — *the user's raw answers are the input; HiSim derives U-values, materials, climate and
load profiles* — and the UI has since added fields for requirements that appeared, which v0.3
reflects. Grants, schedule and financing **are** HiSim's: grants go to the subsidy engine,
financing to the economic parameters, and the schedule generates one HiSim calculation per stage
of a multi-year renovation roadmap.

Consequences for the register above:

- The **input HiSim validates becomes its own schema** (`SimulationInput`, name provisional):
  v1's categorical envelope descriptors as primary fields with per-element U-values as expert
  overrides that win when present (v1 rule, consistent with Q10); v0.3's requirement-driven
  additions kept (installation years, water storage, vehicles by drivetrain, condition assessment,
  cooling set-point, heat-capacity class, residents by type and employment); everything in HiSim
  spelling (C3). Base-state U-values come from the same composition machinery the measures use:
  a categorical insulation id is a layer on the TABULA baseline.
- The **alignment PR** on `hisim-alignment` (contract commit `3fdb70d`, unpushed) is
  **superseded**: its simulation-input half informs the rewrite, its in-place edits of
  `HomeInventoryInput` are not the deliverable. Leave the branch unpushed until the rewrite exists;
  `REQUESTS.md` and the enum generation stay valid.
- Steps 3–6 stand: registry, effects, bindings, parametriser, `calculate` and the payload are
  built on paths, and paths change cheaply. New work: a base-state derivation step before the
  measures, the input schema, a staging layer above `calculate`, financing and grants wiring.

| Id | Decision |
|---|---|
| Q29 | **HiSim expands the schedule into stages; the caller runs each stage.** A pure `stages` command writes `stage_k/home_inventory.json` and `package.json` for k = 0..n deterministically (stage k = inventory with the measures of phases 1..k applied); `calculate` stays one calculation, so the C# side schedules and caches per stage (Q10, R13.5 unchanged). |
| Q30 | **A stage's start year changes the economics only.** Weather stays the fixed Irish dataset; prices escalate to the stage year; devices installed in earlier stages carry their installation year. No ageing of the building state. |
| Q31 | **The categorical envelope descriptors are defined jointly with the catalogue owners**, so the existing building and the measures share one material vocabulary. The input schema's structure can be drafted now; its value lists for construction, insulation, window and door types wait for that meeting. v1's Irish ids are the starting proposal to bring to it. |
| — | Grants: `selected_grant_schemes` and the package's `grants` block feed the subsidy engine's eligibility context (needs `subsidy_catalog/IE.json`, step 6b). Financing: `loan_term_years`, `own_contribution_in_euro` become `EconomicParameters` inputs for the monthly-net-cost figures. |

## 13. Decisions of 2026-09-19 on the frontend side's translator spec v2

Reviewed in `review_translator_spec_v2_2026-09-19.md`. Owner's decisions on its six questions:

| Id | Decision |
|---|---|
| D-C | **CHR01 for everyone in the MVP** (the spec's D7/D10 stand; Q20 is deferred, not reversed). Reason: LPG calculation times in early testing. The battery estimate for `days_to_cover` uses the shipped CHR01 profile's own electricity, which the run also uses, so no invented table is needed. `number_of_residents`, `white_appliances`, `electric_vehicles` are `not_implemented_yet` until the LPG binary is in the image; the capability document says so. |
| D-D | **One set of base files: the recorded grouped twins**, as they are today (Q18 stands; the spec's translator-owned copies are rejected). Everything the spec's base files would have added — the `pv` group, `ems_without_battery`, night setback, air conditioner, solar thermal or EV on further generators — is `not_implemented_yet` for the MVP; base-file work starts after the MVP runs. "No PV" is a zero-power pin (verified 2026-09-19: the baseline runs, output 0). |
| D-E | **The spec's rule 6 as written**: a feature on the `not_implemented_yet` whitelist is reported and the calculation runs; anything not on the whitelist fails hard. The register's refusals for `HYBRID_HEAT_PUMP`, direct-electric DHW, solar thermal on other generators, missing material rows become whitelist entries with notes. (Q4 stays: an unknown key or a stage-0 entry is not a feature, it is invalid input.) Whether the UI hides system-selecting `not_implemented_yet` values is the frontend's call; the capability document distinguishes `no_model` from `substituted` so it can. |
| D-A, D-B, D-F | Not yet answered by the owner; the review's recommendations (frontend derives the physics with `envelope_source` provenance; catalogue lowercase on the wire, carried as the HiSim enums' values; the branch's `result.py` is the result spec, with `COMPUTE_LIFECYCLE_COSTS` added to the spec's option set) are the working assumption until said otherwise. |

**Defect found and fixed the same day (`application.py`):** the parametriser selected the
`electricity_management` variant only from a `BATTERY_SYSTEM` measure; a base-state house without a
battery kept the twin's default `ems_with_battery`, whose battery sizes itself from the PV array,
and with a zero-power array the battery library divided by zero. The variant is now derived from the
post-measure inventory's battery block when no measure selects it. Every end-to-end test before had
run a package with a battery measure, which is why the most common request — a baseline without PV
or battery — had never been exercised. Lesson recorded: the probe set of the capability document
(the spec's §9) must include the bare baseline and every block absent.

**F2 revisited (2026-09-19, owner):** rather than leaving `SimulationParameters.country` at `DE` so the
legacy KPI path keeps working — a shim that would be rediscovered in three months as "the country setting
does nothing" — Ireland is registered in the legacy opex/capex tables as a **placeholder with every number
set to −1 000 000 000** (`hisim/components/configuration.py`, `PlaceholderCountryFactors`). A run with
`country = "IE"` completes, and every legacy cost or CO₂ KPI of an Irish run is visibly absurd until sourced
Irish rows replace the block. The payload reads none of those KPIs: emissions come from the lifecycle engine's
Irish factors; energy demand and self-sufficiency do not depend on the tables. Verified: the KPI path ran,
"Costs of grid electricity" = −1.2 × 10¹⁰ EUR for one January day.

**F11 (2026-09-19, found on the merge with main):** HiSim #771 moved the outside design temperature to the → hisim-epc.8
weather (`Weather.for_location` requires `heating_reference_temperature_in_celsius`; the building reads it as a
sizing fact). The translator had been passing TABULA's `Theta_e_Base` for it — which is the heating-degree-day
base of 12 °C for every country, not a design condition (`Theta_e` is the annual mean). The value is now the
reviewed per-country constant `DesignTemperatures.BY_COUNTRY` (IE −3 °C, NL −10 °C, both TO BE REVIEWED), and a
country without one is a translator error rather than Aachen's −7 °C.
