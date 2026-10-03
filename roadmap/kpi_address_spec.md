# Spec: stable KPI addresses and a way to find them

**Date:** 2026-09-08, revised 2026-10-03 · **Owner:** Noah Pflugradt · **Status:** proposed; step 0 of the
assemblies staging (`roadmap/declarative_energy_systems/assemblies_spec.md` §13, decisions D5/D15/D18), before its
step 1 and independent of the hydronic stages (hisim-fxix). Bead **hisim-b3b.1, P1** (the gating step).

## Problem

A component KPI is addressed, at every layer, by a string key that depends on which other
components happen to live in the same building:

| Layer | Address today |
|---|---|
| Collection (`KpiPreparation.kpi_collection_dict_unsorted`) | `building -> key -> entry dict` |
| Tag-sorted collection, `all_kpis.json`, webtool JSON | `building -> tag -> key -> entry dict` |
| Report table (`return_table_for_report`) | one row per `building`, `key` |
| Golden references (`scripts/golden_kpis.py::flatten`) | `building.tag.key` |

Since #653 the `key` is the bare entry name while exactly one component of the building emits it,
and `"<name> (<source component>)"` as soon as a second one does (`keyed_component_entries`,
`kpi_preparation.py:1868`). So a key is not a property of the KPI: a second car renames the first
car's "Distance driven" for every consumer, and a reader of `all_kpis.json` cannot know a key's shape
without knowing the whole setup. Consumers rebuild key strings by hand: 29 lookups in ten test files,
one in `tests/test_fuel_meter.py` that already had to change. Assemblies sharpen this: a member's name
serializes a structured address (`pv__east__PVSystem`, assemblies spec §2.4), and "every KPI of import
`pv`" would mean splitting that string, which the spec forbids (`__` is derived, never parsed back).

## Goal

1. Every KPI has one address that is a function of the KPI itself: its building, its tag, its
   name and, for component KPIs, its structured source. Nothing about the neighbours enters it.
2. Callers never build or split a key string. A finder enumerates and resolves entries by their
   fields, in process and on a loaded `all_kpis.json` alike.
3. KPIs, the economics' per-subject cost rows and the RenoVisor request ids join on one field set.

## The source of a component KPI

`KpiEntry` (`hisim/postprocessing/kpi_computation/kpi_structure.py:60`) gains
`source: Optional[KpiSource]`, serialized by its `to_dict` next to the existing fields:

| Field (JSON) | Meaning | Site | Member |
|---|---|---|---|
| `import` | the import key | `null` | `pv` |
| `instance` | the instance key = the request's system id; `null` if the import has none | `null` | `east` |
| `member` | the name inside its assembly, or the plain component name | `Building` | `PVSystem` |
| `assembly` | library path of the innermost owning assembly; informative, not identity | `null` | `pv/array` |
| `name` | runtime name = serialized address `<import>[__<instance>]__…__<member>` | `Building` | `pv__east__PVSystem` |

- `import` is a Python keyword, so the dataclass field is `import_key`, pinned to the JSON name
  `import` with `dataclasses_json` field metadata, as `KpiEntry` already pins its own spelling (`:63-65`).
- `Component.component_kpi_entries` (`hisim/component.py:630`, stamping at `:658-659`) fills
  `source` from the component's `ComponentID` (its `path` and member, assemblies D5) wherever an
  entry has none; an entry that reports on behalf of another component (the EMS,
  `controller_l2_energy_management_system.py:990`) sets that component's source.
- Derived KPIs (General tag, meter-derived cost totals, district totals) keep `source: null`.
- **`name_of_source_component` is kept for one release** as the same string as `source.name`,
  marked deprecated in the docstring and the contract note: `nameOfSourceComponent` is part of the
  webtool JSON wire format (`kpi_structure.py:63-65`), so its readers get one release offering both.

## Address scheme

- **Component KPIs** are always keyed `"<name> (<source.name>)"`, whether or not anything collides.
  The leaf key stays a string, so `jsondata[building][tag][key]` and the goldens' flat
  `building.tag.key` keep working, and a key never changes when a neighbour is added.
- **Derived KPIs** have no source and stay keyed by bare name; they are singletons per building.
- A key's shape is fixed by its producer, never by who else is present. `keyed_component_entries`
  keeps refusing an entry without a source and a duplicate key; both now mean a component defect.
- Consumers **filter on `source.*`**; nobody splits strings. The tag level and the JSON nesting stay
  (a nesting level per source was rejected: it changes the shape for every consumer).

## Worked example: `all_kpis.json`

Two PV arrays (import `pv`, instances `east`/`west`), a heat pump in import `heating`, a site
`Building`, one derived General KPI. Entries abridged to `value`, `unit` and `source`; every component
entry also keeps `nameOfSourceComponent` = `source.name`:

```json
{"BUI1": {
  "General": {
    "Self-consumption rate": {"value": 41.2, "unit": "%", "source": null}},
  "PV": {
    "Electricity production (pv__east__PVSystem)": {"value": 3120.5, "unit": "kWh",
      "source": {"import": "pv", "instance": "east", "member": "PVSystem",
                 "assembly": "pv/array", "name": "pv__east__PVSystem"}},
    "Electricity production (pv__west__PVSystem)": {"value": 2875.0, "unit": "kWh",
      "source": {"import": "pv", "instance": "west", "member": "PVSystem",
                 "assembly": "pv/array", "name": "pv__west__PVSystem"}}},
  "Heat Pump For Space Heating": {
    "Seasonal performance factor (heating__HeatPump)": {"value": 3.4, "unit": "-",
      "source": {"import": "heating", "instance": null, "member": "HeatPump",
                 "assembly": "heating/air_source_heat_pump", "name": "heating__HeatPump"}}},
  "Building": {
    "Heating load (Building)": {"value": 7.9, "unit": "kW",
      "source": {"import": null, "instance": null, "member": "Building",
                 "assembly": null, "name": "Building"}}}}}
```

The golden form of the first array is `BUI1.PV.Electricity production (pv__east__PVSystem)`; adding a
third array adds a key and renames none.

## Finder

A small module `hisim/postprocessing/kpi_computation/kpi_address.py`, importing only
`kpi_structure`, so scripts and tests can use it on a loaded JSON without the simulator:

```python
@dataclass(frozen=True)
class KpiAddress:
    building: str
    tag: str                          # the KpiTagEnumClass value as written in the JSON
    name: str                         # the entry's own name, never qualified
    source: Optional[KpiSource] = None  # None for derived KPIs

    @property
    def key(self) -> str: ...         # "<name> (<source.name>)" or "<name>"
    @property
    def dotted(self) -> str: ...      # "<building>.<tag>.<key>", the golden form

class KpiFinder:
    def __init__(self, sorted_collection: Mapping[str, Mapping[str, Mapping[str, dict]]]): ...
    def addresses(self, *, building=None, tag=None, name=None, source=None,
                  import_key=None, instance=None, member=None, assembly=None) -> List[KpiAddress]
    def entries(self, **same) -> List[Tuple[KpiAddress, dict]]
    def one(self, **same) -> Tuple[KpiAddress, dict]   # exactly one match or ValueError naming the candidates
    def value(self, **same) -> float                   # one(...) and its "value"
```

- Every filter is optional and exact; `source` matches `source.name`. "Every KPI of import pv" is
  `addresses(import_key="pv")`, "the array with instance east" `entries(import_key="pv", instance="east")`.
  Omitting all filters enumerates the whole collection.
- The finder reads `source` from the entry dict, never from the key; a JSON written before this
  spec (no `source`) is read with `source.name` from `nameOfSourceComponent` and the rest `null`.
- `one` raises with the list of matching addresses when zero or several match, so a test that
  meant "the car's distance" in a two-car setup fails by name rather than by KeyError.
- `KpiPreparation` and `KpiGenerator` expose a `finder` on the sorted collection; the writer is unchanged.
- Optional CLI: `hisim kpis list <dir or all_kpis.json> [--building|--tag|--name|--import|--instance]`.

## The same source on the cost rows

The economics' per-subject rows of `result.json`, `plan.by_subject[]` (`hisim/renovisor/costs.py:88-93`),
carry the same `source` object beside their `subject` string, so the frontend joins KPIs, cost rows and
request ids (`source.instance`) on one field set. This is a contract change: it is settled with the
frontend as a spec MR on renovisorissues (assemblies D15, recommendation (b)) before the first composed
energy-system file ships. Until then a flat file's rows carry `source` with `import`/`instance` `null`.

## Migration

1. `KpiSource` and `KpiEntry.source`, stamped by `component_kpi_entries` (the EMS sets its target's);
   `name_of_source_component` stays, deprecated, equal to `source.name`.
2. `keyed_component_entries` qualifies every component entry by `source.name`; the "exactly one
   emitter keeps the bare name" branch goes, its tests move with it. `read_opex_and_capex_costs_from_results`
   matches on name and tag and is unaffected (confirm with the existing two-meter test).
3. `kpi_address.py` with the finder's `import_key`/`instance`/`member`/`assembly` filters; replace
   the 29 hand-built lookups in tests with `finder.value(...)`; `tests/test_fuel_meter.py` loses its
   f-string key.
4. Re-bless all golden references once through the `golden-update` workflow, for keys only (every
   component KPI key changes; no value does). Done before the hydronic stages re-bless values, so the
   two diffs never mix. The manifest's config hash moves with it.
5. `plan.by_subject[]` rows gain `source`; the renovisorissues spec MR for `result.json` and the KPI
   JSON is opened with this PR and merged before the first composed file ships.
6. Record the key format and `source` in the webtool JSON contract note in `kpi_structure.py` and in
   the `energy_systems/README.md` KPI section, with one before/after example.
7. Remove the "volatile keys" remark from `roadmap/p3_cleanup_todos.md` once merged. → hisim-b3b.1

## Acceptance

- Adding a second instance of any component to any setup changes no existing key of the first
  instance; the golden diff shows only added keys, and the re-bless diff changes no value.
- Every component entry in every golden's `all_kpis.json` carries a `source` whose `name` equals its
  `nameOfSourceComponent` and the qualifier of its key; every derived entry carries `source: null`.
- No production or test code outside `kpi_address.py` and `keyed_component_entries` builds or splits
  a key string; `grep -rn '(\{' tests hisim --include=*.py | grep -i kpi` finds nothing.
- `KpiFinder(...).addresses()` on any golden's source `all_kpis.json` lists every leaf;
  `one(name=..., source=...)` resolves every component KPI without a collision error, and on a
  composed two-array file `addresses(import_key="pv")` returns both arrays' KPIs and nothing else.
- Every `plan.by_subject[]` row of a RenoVisor `result.json` carries `source`, and the frontend spec MR
  is merged on renovisorissues.
- The base suite and the one-week golden gate are green; the full-year goldens are re-blessed in
  the same PR.

## Out of scope

- Renaming or restructuring derived (General, district) KPIs.
- Changing the tag vocabulary or the JSON nesting.
- Removing `name_of_source_component` (the release after this one).
- (Replaced:) stable identifiers for the source. The source is now the structured address: a renamed
  site component still renames its KPI keys, which is correct for a name; an assembly member's identity
  is its address, which the RenoVisor keeps stable per system id across a plan's stages (assemblies §2.4).
