# Spec: stable KPI addresses and a way to find them

**Date:** 2026-09-08 · **Owner:** Noah Pflugradt · **Status:** proposed, its own PR after
`kpi_multi_instance` (#653) is on main.

## Problem

A component KPI is addressed, at every layer, by a string key that depends on which other
components happen to live in the same building:

| Layer | Address today |
|---|---|
| Collection (`KpiPreparation.kpi_collection_dict_unsorted`) | `building -> key -> entry dict` |
| Tag-sorted collection, `all_kpis.json`, webtool JSON | `building -> tag -> key -> entry dict` |
| Report table (`return_table_for_report`) | one row per `building`, `key` |
| Golden references (`scripts/golden_kpis.py::flatten`) | `building.tag.key` |

Since #653 the `key` is the bare entry name while exactly one component of the building emits
it, and `"<name> (<source component>)"` as soon as a second one does. So a key is not a property
of the KPI: adding a second car to a setup renames the first car's "Distance driven" for every
consumer, the fuel meter's CO2 entry is qualified only because an oil boiler shares its name, and
a reader of `all_kpis.json` cannot know a key's shape without knowing the whole setup. Every
consumer that wants one component's entry rebuilds the key string by hand; there are 29 such
lookups in ten test files and one in `tests/test_fuel_meter.py` that already had to change.

## Goal

1. Every KPI has one address that is a function of the KPI itself: its building, its tag, its
   name and, for component KPIs, its source component. Nothing about the neighbours enters it.
2. Callers never build a key string. A finder enumerates and resolves entries by their fields,
   in process and on a loaded `all_kpis.json` alike.

## Address scheme

- **Component KPIs** (entries a `Component.get_component_kpi_entries` produced) are always keyed
  `"<name> (<source component>)"`, whether or not anything collides. The source component is the
  entry's `name_of_source_component`, which the base class stamps on every entry (#653).
- **Derived KPIs** (entries `KpiPreparation` computes itself: the General tag, the meter-derived
  cost totals, district totals) have no source component and stay keyed by bare name. They are
  singletons per building by construction.
- The rule that decides a key's shape is therefore "who produced it", which is fixed at the
  producer, never "who else is present". `keyed_component_entries` keeps refusing an entry
  without a source and a duplicate key; both now mean a component defect, never a setup shape.
- The tag level and the dotted golden form `building.tag.key` stay as they are, so the nesting of
  `all_kpis.json` and the webtool JSON does not change; only the leaf keys of component entries
  gain their qualifier. (Adding a nesting level per source component was considered and
  rejected: it changes the JSON shape for every consumer, while the qualified string only changes
  the leaf key and keeps `jsondata[building][tag][key]` working.)

## Finder

A small module `hisim/postprocessing/kpi_computation/kpi_address.py`, importing only
`kpi_structure`, so scripts and tests can use it on a loaded JSON without the simulator:

```python
@dataclass(frozen=True)
class KpiAddress:
    building: str
    tag: str                      # the KpiTagEnumClass value as written in the JSON
    name: str                     # the entry's own name, never qualified
    source: Optional[str] = None  # name_of_source_component; None for derived KPIs

    @property
    def key(self) -> str: ...     # "<name> (<source>)" or "<name>"
    @property
    def dotted(self) -> str: ...  # "<building>.<tag>.<key>", the golden form

class KpiFinder:
    def __init__(self, sorted_collection: Mapping[str, Mapping[str, Mapping[str, dict]]]): ...
    def addresses(self, *, building=None, tag=None, name=None, source=None) -> List[KpiAddress]
    def entries(self, **same) -> List[Tuple[KpiAddress, dict]]
    def one(self, **same) -> Tuple[KpiAddress, dict]   # exactly one match or ValueError naming the candidates
    def value(self, **same) -> float                   # one(...) and its "value"
```

- Every filter is optional and exact; omitting all of them enumerates the whole collection, which
  is the "way to find keys" a reader needs when they do not know what a setup reports.
- `one` raises with the list of matching addresses when zero or several match, so a test that
  meant "the car's distance" in a two-car setup fails by name rather than by KeyError.
- `KpiPreparation` and `KpiGenerator` expose a `finder` built on the sorted collection, and the
  post-processing entry that writes `all_kpis.json` is unchanged; the finder reads the same dict.
- A CLI is optional and cheap once the finder exists:
  `hisim kpis list <results dir or all_kpis.json> [--building B] [--tag T] [--name N]`, printing
  one dotted address and value per line.

## Migration

1. `keyed_component_entries`: qualify every component entry; drop the "exactly one emitter keeps
   the bare name" branch. Its tests move with it.
2. `read_opex_and_capex_costs_from_results` already matches on the entry's own name and tag, so it
   is unaffected; confirm with the existing two-meter test.
3. Replace the 29 hand-built lookups in tests with `finder.value(...)`; `tests/test_fuel_meter.py`
   loses its f-string key.
4. Re-bless all golden references through the `golden-update` workflow (every component KPI key
   in every golden changes; the values do not). The manifest's config hash moves with it.
5. Record the key format in the webtool JSON contract note in `kpi_structure.py` and in the
   `energy_systems/README.md` KPI section, with one before/after example.
6. Remove the "volatile keys" remark from `roadmap/p3_cleanup_todos.md` once merged.

## Acceptance

- Adding a second instance of any component to any setup changes no existing key of the first
  instance; the golden diff shows only added keys.
- No production or test code outside `kpi_address.py` and `keyed_component_entries` builds a key
  string; `grep -rn '(\{' tests hisim --include=*.py | grep -i kpi` finds nothing.
- `KpiFinder(...).addresses()` on any golden's source `all_kpis.json` lists every leaf, and
  `one(name=..., source=...)` resolves every component KPI in the fleet without a collision error.
- The base suite and the one-week golden gate are green; the full-year goldens are re-blessed in
  the same PR.

## Out of scope

- Renaming or restructuring derived (General, district) KPIs.
- Changing the tag vocabulary or the JSON nesting.
- Stable identifiers for the *source* itself: the qualifier is the runtime `component_name`, and a
  setup that renames a component renames its KPI keys. That is the correct behaviour for a name.
