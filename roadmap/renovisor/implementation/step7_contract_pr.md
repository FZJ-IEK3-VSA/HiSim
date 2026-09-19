# Step 7 — the contract PR: one `openapi.yaml` the catalogue, the materials database and HiSim agree on

**Superseded** by the adoption of the frontend side's spec (`challenges.md` §13); kept for the reasoning.

**Status:** implementation specification, 2026-09-15
**Target repository:** `~/renovisor-api-contract` (GitHub `climatemedia/renovisor-api-contract`),
branch `hisim-alignment` from `main` (`eeb49b5`). Reviewed by the CODEOWNERS of both teams; HiSim
does not merge it.
**Decisions:** `challenges.md` §9 — C3 (HiSim spelling), Q1 (generic `Measure`), Q2, Q3, Q4, Q6,
Q7, Q9, Q13, Q14, Q16, Q21, Q22, Q23, Q26, Q28; the A-items of `requirements.md` §8.2 the backend
owns; the N-items of `measures_v2_requirements.md` §8.2.
**Sources of truth the PR copies from, never retypes:** `hisim/renovisor/vocabulary.py` (enum
members), `hisim/renovisor/bindings.py` `PENDING_RENAMES` (field renames), `hisim/renovisor/
inventory.py` `PendingContractPaths` (new fields), `hisim/renovisor/catalogue.py` `IdDerivation`
(measure and option ids), `CatalogueSpellings` (values to respell), `roadmap/renovisor/
translation_map.html` (attached to the PR for the frontend team, V3).

Ground rules: work in the contract clone only (`git -C ~/renovisor-api-contract`); create the
branch; do not push (no credentials on this box — the reviewer pushes from another machine); the
repo's own check must pass: `pip install openapi-spec-validator pyyaml` into a scratch venv if the
HiSim venv lacks it, then `python -c "from openapi_spec_validator import validate; import yaml;
validate(yaml.safe_load(open('openapi.yaml')))"`. Keep the file's existing style (2-space YAML,
inline `{ type: … }` where the file already uses it, `x-contract-status` on every schema). Every
changed schema gets a one-line `description` sentence saying what changed and which decision id.

---

## 1. `info.description` and conventions

- Bump `info.version` to `0.4.0-draft`. Add a "Changes since 0.3" list naming each section below.
- Conventions block: field names `snake_case` lowercase with units spelled out (`_in_watt`,
  `_in_kilowatt_hour`, `_in_celsius`, `_in_m2`, `_in_liter`, `_in_degree`, `_in_euro`); **enum
  values are UPPER_SNAKE member names, identical to HiSim's** (C3); money `_in_euro` (fix the
  README's `_eur`). State in one sentence that every enum in this document is generated from
  `hisim.renovisor.vocabulary` or from HiSim's component enums, and that a rename on either side is
  a breaking change (A19).

## 2. `HomeInventoryInput` (provisional payload)

Rename per `PENDING_RENAMES` (contract-owned rows): `conditioned_floor_area_m2` →
`absolute_conditioned_floor_area_in_m2`; both `volume_in_liters` → `volume_in_liter`;
`with_dhw_preparation` → `with_domestic_hot_water_preparation`. Leave the seven P4-owned rows as
they are (HiSim renames toward them) but add `x-hisim-field` on each naming HiSim's current field.

Respell enums (C3): `heating_system.system` → the `HeatGenerator` members (11; the two
`*SolarThermal` values go, `HVO_HEATING`, `BIOMASS_HEATING`, `HYBRID_HEAT_PUMP` come);
`heat_distribution_system` → `FLOORHEATING`, `RADIATOR`, `LOW_TEMPERATURE_RADIATOR`;
`retrofit_status` → `RetrofitStatus`; `tabula_building_type` → `TabulaBuildingType` (with a note
that `MFH` has no Irish archetype and is refused for `IE`, A20); `building_heat_capacity_class`
stays a plain lowercase string (HiSim's own spelling). Turn every "Possible values: …"
description into a real `enum`.

Add fields (each `nullable`, each with a description naming its consumer and decision):
`building_config.general.floor_construction` (`FloorConstruction`), `wall_construction`
(`WallConstruction`), `cavity_width_in_mm` (N7), `roof_form` (`PITCHED`, `FLAT`),
`roof_orientation_in_degree`, `worst_window_glazing_panes` (integer, catalogue reference);
`energy_system_config.photovoltaics.share_of_maximum_pv_potential` (0–1, the rooftop law's input);
`energy_system_config.heating_system.dhw_supply` (`DhwSupply`);
`energy_system_config.ventilation_system` block `{system: VentilationType, installation_year}`
and `air_conditioning_system` block `{power_in_watt, installation_year}` (F6);
`vehicles.electric_vehicles[].km_per_year` and `fossil_vehicles[].km_per_year` (A5);
`vehicles.*[].vehicle_id` (A24) — or, if the team prefers, a sentence that measures never name a
vehicle; propose the id and let review decide.

Tag every field with `x-consumer: simulation | translation | cost_scheduling | none` (A1, A2), using
`Bindings.resolve` kinds: `CONFIG_OVERRIDE`/`CONSTRUCTOR_ARGUMENT` → `simulation`;
`NON_SIMULATION` → `cost_scheduling`, except the fit facts and law inputs (`floor_construction`,
`wall_construction`, `roof_form`, `roof_orientation_in_degree`, `cavity_width_in_mm`) →
`translation`; `NO_CONSUMER` → `none` with the note from the binding. Do not remove the `none`
fields; the team decides (A2).

State the precedence rules in descriptions: `envelope_details` beats the TABULA value per element
when present (Q10, A8); a stated `power_in_watt` pins the size, otherwise the sizing law decides
(A11).

## 3. `Measure`, `PackageDefinition`, `Schedule`

Replace the flat `Measure` by the generic shape (Q1):

```yaml
Measure:
  x-contract-status: provisional
  type: object
  required: [measure_id, options]
  additionalProperties: false        # Q4: no `stage`, nothing else
  properties:
    measure_id: { $ref: '#/components/schemas/MeasureId' }
    options:
      type: object
      description: option_id -> value; the legal option ids and values per measure are in measures.yaml
      additionalProperties: { oneOf: [{type: string}, {type: integer}, {type: boolean}] }
MeasureId: { type: string, enum: [<the 33 ids from IdDerivation, catalogue order>] }
```

`PackageDefinition.measures`: `uniqueItems` on `measure_id` cannot be expressed in JSON Schema;
state it in the description (DUPLICATE_MEASURE). State that packages carry measures to be applied
only; a stage-0 entry is a 422 (Q4). `Schedule.phase_order: [string]` → `phases: [{starts_in,
measure_ids: [MeasureId]}]` (A22).

## 4. `measures.yaml`

Add `id:` to every measure (the 33 derived ids), `id:` to every option (snake_case), and respell
enum values to HiSim members where HiSim has them (`HEATING_INSTALLATION.type_of_system` →
`FLOORHEATING`/`RADIATOR`/`LOW_TEMPERATURE_RADIATOR`; `VENTILATION_SYSTEM.type_of_system` → the
`VentilationType` members; `HEATING_SYSTEM.type_of_system` is already HiSim spelling once
upper-cased; `HOT_WATER_SYSTEM.supply` → `HEAT_PUMP`/`DIRECT_ELECTRIC`; `SOLAR_THERMAL_SYSTEM.
supplies` already). Material options: replace each string by the `asp_id` from `CatalogueSpellings`
where resolved; where UNRESOLVED, keep the string and add a YAML comment `# TODO owners: no
materials.yaml row (Q6) / choose stone or glass wool (Q7)`. `glazing_panes` options: keep, add the
comment `# TODO owners: window/door rows in materials.yaml with U-value and total cost (Q13)`.
Normalise `options:` null → `[]` (N3). Add a header note: ids are the wire format; `display_name`
is the label; stage-0 entries never appear in a `PackageDefinition`.

## 5. `Material` and `/materials`

Widen `Material` to what HiSim reads (Q5, Q9, Q13): `id` = `asp_id`; `name`; `summary`;
`thermal_conductivity_in_watt_per_meter_per_kelvin` (required); `lifespan_in_years {low, high}`;
`investment_cost_in_euro_per_m3 {IE, ES, NL: {low, high}}`; **`total_cost_in_euro_per_m2`** per
country — the new column the materials team owns, labour and system parts included (Q9);
`co2_footprint_in_kg_per_m2`, `co2_storage_in_kg_per_m2`; `end_of_life`; `image_url`; and for
window and door rows `u_value_in_watt_per_m2_per_kelvin` and `glazing_panes` (Q13). Mark the
schema `x-contract-status: provisional`, owner "materials team".

## 6. Result payload

`Kpis` and `Costs` values become provenance objects (Q21): every leaf `{value, provenance:
[SIMULATED, MOCKED, PARTIAL]}` via a `ProvenanceNumber`/`ProvenanceString` schema; fix
`self_sufficiency_in_percent` to a number (A10). Define `energy_demand_in_kilowatt_hour_per_year`
as delivered energy summed over carriers for the simulated year and
`emissions_in_kg_co2_per_year` as operational emissions (Q22, A14); add
`embodied_co2_in_kg` (Q22). `Range`: rename `normal` → `best_estimate` and state the one-slot rule
(A9). Remove `property_value_increase_in_percent` or mark `x-owner: none` (A13; propose removal).
Add to `Package`: `calculation_version {image_digest}` (Q26, A15), `weather_basis {location,
dataset, year}` (A4). `CalculationStatus.reason` → `errors: [{reason: ReasonCode, path, detail}]`
with `ReasonCode` enum generated from `hisim.renovisor.reasons.ReasonCode` (A16, R13.2.2).

## 7. Requests to the owners, as a `REQUESTS.md` beside the PR description

The catalogue owners: drop `BIOMASS_HEATING` in favour of the specific values or define it (Q14);
choose stone or glass wool per measure (Q7); define the three option-less measures (`ADD_INTERNAL_
DRY_LINING`, `BASEMENT_INTERNAL_INSULATION`, `TOP_FLOOR_CEILING_INSULATION`) or remove them; say
whether `AIR_CONDITIONERS` stays offered although no recorded system carries one; supply
`dependencies.yaml` (N5). The materials team: rows for PIR, thermal laminate drylining board,
liquid insulation (Q6); window and door rows per pane class (Q13); the total-cost column (Q9); a
clean export (Q5). Both: where the master copy of the contract lives (Q28).

## 8. PR description (for the reviewer to paste; no session links)

Problem / Done / Result, one paragraph each, then the checklist of sections 1–7, then "Attached:
`translation_map.html` — the generated map of what HiSim does with each measure today". End with
the attribution line the repository conventions require.

## 9. Done means

`openapi.yaml` validates with `openapi_spec_validator`; `measures.yaml` loads and every measure
has an `id` equal to `IdDerivation` of its `display_name`; a HiSim test (add
`tests/test_renovisor_contract_alignment.py`, runnable against the *branch* checkout via an
environment variable pointing at it, skipped otherwise) asserts every enum in the branch's
`openapi.yaml` equals the corresponding HiSim vocabulary and that `PendingContractPaths` is empty
against it; the branch exists locally with one commit; the final report lists the commit hash,
the validator output, and every place where the spec left a choice to review.
