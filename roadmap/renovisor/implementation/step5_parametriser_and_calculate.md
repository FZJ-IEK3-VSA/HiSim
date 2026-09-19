# Step 5 — the parametriser, law resolution, the `calculate` entry point and the trace page

**Status:** implementation specification, 2026-09-15
**Builds on:** step 3 (`application.py` → `ApplicationResult`), step 4 (`bindings.py`, `map.py`).
**Decisions:** `challenges.md` §9 — Q12 (battery law inputs), Q18 (recorded grouped files as base
files; refusals for missing combinations), Q19 (TABULA workaround), Q20/Q25 (cache directory),
Q26 (image digest echoed), R4/M10 (what a parametrised file may differ in), R7 (report), R11/R13.2
(`errors.json`, three distinguishable outcomes), R16 (records written), V1 (trace page).
**Out of scope:** the result payload (`kpis`, `costs`, provenance objects) — step 6; the contract
PR — step 7; a list of cache directories on `SimulationParameters` — step 7b (Q25 follow-up).

Ground rules are those of step 3 §0 (worktree, interpreter, no commits, class-scope constants,
plain complete docstrings, flake8 + mypy clean). New unit tests are `@pytest.mark.base`; the one
end-to-end test is `@pytest.mark.system_setups`.

---

## 1. `occupancy.py` — nearest LPG household (A3)

`HouseholdMatcher.match(inventory) -> HouseholdMatch(household: JsonReference, travel_route_set:
Optional[JsonReference], note: str)`. Reads `occupancy_config.residents_count`, `residents_type`
and `residents_employment_status`, picks the nearest entry of
`utspclient.helpers.lpgdata.Households` (list the catalogue at import, do not hard-code 66 names):
exact match on adult/child counts and employment pattern when the catalogue has one, else the
nearest by adult count, then by child count, then by employment; the note states what matched and
what was relaxed. `travel_route_set` maps the contract's string to `utspclient.helpers.lpgdata.
TravelRouteSets` by name. The report line is `approximated` with the note as `rule`. Unit-test the
matcher on five compositions including one with no exact catalogue entry.

## 2. `laws.py` — resolving `LawRequest`s (Q12)

```python
class DemandEstimator(Protocol):
    def household_electricity_in_kwh_per_day(self) -> float
    def heat_pump_electricity_in_kwh_per_day(self) -> float
    def vehicle_electricity_in_kwh_per_day(self) -> float

class LawResolver:
    def resolve(self, law: LawRequest, inventory: Inventory, estimator: DemandEstimator) -> LawResult(value, rule: str)
```

`BATTERY_FROM_DAYS_TO_COVER`: `days × (household + heat_pump + vehicle)` in kWh; the heat-pump
term is zero when the selected generator is not a heat pump. The `rule` string names the three
inputs and their values so the report shows the arithmetic.

`PreRunDemandEstimator(inventory, base_file_key, household_match, ...)`:
- household: load the matched LPG profile through `UtspLpgConnector`'s own loading path with the
  configured cache directory (the run needs the same profile, so the cache is warmed, not
  duplicated) and sum electricity over the year / 365. If that loading path cannot be invoked
  without a full component construction, expose the smallest seam in the connector that returns
  the annual electricity of a household reference and use it; document the seam.
- heat pump: TABULA row `q_h_nd` (kWh/m²a, from `episcope-tabula.csv` for the selected building
  code) × conditioned floor area / `SEASONAL_PERFORMANCE_FACTOR` / 365, with
  `SEASONAL_PERFORMANCE_FACTOR = 3.0` as a class constant marked
  `PROVISIONAL — assumed seasonal performance of an air/water heat pump; replace by the hplib
  model's own figure once the run has happened`.
- vehicle: `km_per_year × consumption_in_kwh_per_km / 365` per electric vehicle when both are
  present; `km_per_year` is a pending contract path (A5) — when absent the term is 0 and the rule
  says so.

`PV_SHARE_OF_ROOF` is **not** a law here: the share is written as a config override and the
recorded `rooftop` preset's law computes the power (step 3, Q18).

## 3. `parametriser.py` — from `ApplicationResult` to a parametrised energy-system file

```python
class Parametriser:
    def __init__(self, base_files_directory: Path, bindings: Bindings = Bindings, tabula: ..., matcher: HouseholdMatcher, laws: LawResolver)
    def parametrise(self, result: ApplicationResult, estimator: DemandEstimator) -> ParametrisedSystem
@dataclass(frozen=True)
class ParametrisedSystem:
    model: EnergySystemFile          # the edited pydantic model
    base_file_name: str
    yaml_text: str                   # dump_energy_system(model), deterministic
    edits: Tuple[Edit, ...]          # every change with its inventory path or measure id, for the report and the trace
```

Load the base file with `hisim.energy_system.loader.load_energy_system`. Then, in this order:

1. **Variant selections and groups** from `result.variant_selections` / `enabled_groups`
   (`variants[name].selected`, `groups[name].enabled`). Unknown variant or option name →
   `RefusalError(NO_BASE_FILE_FOR_COMBINATION)`.
2. **Constructor swaps** (R4 permits constructor arguments; a recorded preset is replaced by the
   constructor call, and the recorded `config:` keys the constructor now supplies are removed):
   - `Building`: `preset: german_single_family_home` → `constructor: for_tabula_code` with
     `building_code` from `tabula_ie.select_building_code` (type, year, retrofit status; the
     zero-area workaround reported `approximated` with the substituted code, Q19),
     `absolute_conditioned_floor_area_in_m2`, `number_of_apartments`,
     `building_heat_capacity_class`; **remove** the recorded `weather_identity` (a sized value
     computed from the weather; the law recomputes it for Dublin) — verify in the configure step
     that a config value on a sized field pins it, and document the finding.
   - `Weather`: recorded raw config → `constructor: for_location` with `location: IE`
     (`LocationEnum` member name for the country code; any other code →
     `RefusalError(NO_TABULA_ARCHETYPE)`-style refusal with a new reason `NO_WEATHER_FOR_COUNTRY`
     added to `ReasonCode`); remove `source_path` and `data_source`.
   - `PVSystem.config.location` → `IE` (a plain field).
   - `UTSPConnector`: `preset: couple_both_at_work` → `constructor: for_household` with the
     `HouseholdMatch`; keep `data_acquisition_mode: USE_LOCAL_LPG` (Q20).
3. **Config overrides** for every inventory leaf that `Bindings.resolve` maps to
   `CONFIG_OVERRIDE` and that is present (non-null) in the post-measure inventory, including the
   composed U-values and the `PendingRenames` (write the HiSim name). A leaf whose target
   component is absent from the selected file: if the leaf was **written by a measure** →
   `Refusal(NO_BASE_FILE_FOR_COMBINATION)` (a requested renovation the file cannot carry, fail hard);
   if it is **base-state inventory** → report `ignored` with the note "base file <name> has no
   <component>" (the electric-heating file has no `HeatDistributionController`, the district and
   electric files no `SimpleHotWaterStorage`; step-4 report §4). A `requires_variant` block whose
   variant is not selected is skipped with the same `ignored` note.
4. **Laws**: resolve every `result.pending_laws` entry with `LawResolver`, write the value as a
   config override through the bindings, report `approximated` with the rule.
5. **Dump** with `dump_energy_system`; set `model.name` to `renovisor <base file stem>` and
   `model.description` to one deterministic sentence (no hashes, no timestamps; R10).

**The diff rule (AC3 + M10), asserted by `ParametrisedSystem.assert_only_permitted_edits(base)`
and tested:** compared to the loaded base model, a parametrised model may differ only in
`components.*.config` values, `components.*.preset` → `constructor` swaps for the three named
components (with the listed key removals), `components.PVSystem.config.location`,
`variants.*.selected`, `groups.*.enabled`, `name` and `description`. Any other difference
(inputs, sizing_sources, a new or removed component, a new variant) raises `ParametriserError`.

## 4. `calculate.py` and `__main__.py` — files in, files out (R13)

```
python -m hisim.renovisor calculate INPUT_DIR OUTPUT_DIR [--cache-dir DIR] [--base-files DIR]
```

`INPUT_DIR` holds `home_inventory.json` (a `HomeInventoryInput`), `package.json`
(`{"measures": [...]}`, the Q1 shape) and `simulation.yaml` or `simulation.json` (read by
`hisim.energy_system.executor.SimulationParametersReader`, A18). `OUTPUT_DIR` receives everything
the calculation writes and nothing is written anywhere else (R13.4): the simulation's
`result_directory` is `OUTPUT_DIR/results`, `SimulationParameters.cache_dir_path` is `--cache-dir`
when given (Q25; default HiSim's) and `ResultPathProviderSingleton.reset()` is called first.

Outputs:

| File | When | Content |
|---|---|---|
| `parametrised.energy_system.yaml` | after parametrisation, before the run | the file that ran |
| `realized.energy_system.yaml`, `realized.audit.yaml`, `component_connections.json` | via `write_records` before the first timestep | R16 |
| `translation_report.json` | always when parametrisation happened | `{translator_version, contract: {commit, sha256 per file}, image_digest, base_file, fields: [...], measures: [...]}`; `image_digest` echoes the environment variable `RENOVISOR_IMAGE_DIGEST` (Q26), `null` when unset |
| `calculation.json` | always | `{"status": "finished" \| "refused" \| "failed" \| "invalid", "translator_version", "image_digest", "output_files": [...]}` |
| `errors.json` | on every non-finished outcome | `{"status", "errors": [{"reason": <ReasonCode>, "group": validation\|refusal\|crash, "path", "detail"}], "traceback": <str, crash only>}` |
| `results/…` | the simulation's own output | whatever the parameters file asked for |

Exit codes as an Enum: `FINISHED = 0`, `INVALID = 2` (ValidationError), `REFUSED = 3`
(RefusalError), `FAILED = 4` (any other exception; traceback in `errors.json`). A crash **after**
`errors.json` would be unrecoverable, so write `calculation.json` and `errors.json` in a
`finally`-style path that itself cannot raise on the happy path. `CalculationRunner` is the class;
`__main__.main` is a thin argparse layer over it; keep the module docstring's interface
description in step with this table.

## 5. Trace page (V1, second view) in `map.py`

`MapData.collect_trace(example)` runs `PackageApplication.apply` and `Parametriser.parametrise`
(no simulation; laws use a `StaticDemandEstimator` with three stated numbers only for the trace
example, labelled as such on the page) on a committed example pair:
`tests/renovisor/example_inventory_ie_1988_detached.json` (exists) plus a new
`tests/renovisor/example_package_gas_to_heat_pump.json` with `EXTERNAL_INSULATION(EPS)`,
`ROLLED_OUT_ATTIC_INSULATION(WOOD_FIBER)`, `HEATING_INSTALLATION(SURFACE_HEATING)`,
`CHANGE_ROOM_TEMPERATURE(21)`, `HEATING_SYSTEM(HEAT_PUMP)`, `PHOTOVOLTAIC_SYSTEM(40)`,
`BATTERY_SYSTEM(2)`. The Trace tab shows three panes: the inventory diff (pre → post, changed
leaves only, each linked to its measure), the YAML diff (base file → parametrised, unified diff
with each hunk annotated with the inventory path or measure id from `edits`), and the report
table. Deterministic, offline (TABULA CSV and the base file only). The freshness test of step 4
covers it because it is part of the same HTML.

## 6. Tests

- `test_renovisor_occupancy.py`, `test_renovisor_laws.py` (`StaticDemandEstimator`;
  arithmetic; heat-pump term zero for a boiler), `test_renovisor_parametriser.py` (constructor
  swaps; every `CONFIG_OVERRIDE` leaf present lands on its target; the electric-heating file
  reports `ignored` for the HDS controller and refuses a `HEATING_INSTALLATION` measure; the
  diff rule rejects a forged `inputs` change; dump is deterministic across two runs),
  `test_renovisor_calculate.py` (a temp input dir → `calculation.json`/`errors.json` for each of
  invalid, refused, and a forced crash via an injected runner; nothing written outside the output
  dir, asserted by listing the worktree with `git status --porcelain` before and after, AC11.5).
- `test_renovisor_end_to_end.py` `@pytest.mark.system_setups`: the trace example through
  `calculate` with `one_day_15min.simulation.yaml`; asserts exit 0, the five output files, the
  realized record loads, the report has one line per measure. Before writing it, check that the
  Irish NSRDB weather file `LocationEnum.IE` points at exists in `hisim/inputs/` of the worktree;
  if it does not, the test skips with that reason and the final report says so — do not switch
  the location to make it pass.

## 7. Done means

All `tests/test_renovisor_*.py` green (the end-to-end one may skip only for the missing-weather
reason); flake8 and mypy clean; `translation_map.html` regenerated with the Trace tab filled;
the final report lists: the pinning finding for `weather_identity`, the seam used for the household
electricity estimate, whether the end-to-end run happened and its wall time, and every PROVISIONAL
constant.
