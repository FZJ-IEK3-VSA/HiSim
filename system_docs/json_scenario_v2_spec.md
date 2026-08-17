# JSON Scenario Architecture v2

**Status:** draft for team review
**Date:** 2026-08-17
**Supersedes:** the current `json_generator.py` / `json_executor.py` design and the
construction-provenance approach on branch `fix_json_scenario_translator` (commit `1a8ebf0e`).

---

## 1. Motivation

The current JSON translation works by running a Python setup and reverse-engineering the
resulting live object graph back into JSON. That direction of truth causes every problem we
have seen so far:

1. **Positional port identity.** Dynamic ports are named by insertion order (`Output17`,
   `Input_Weather_Temperature_5`). Serialization must reproduce exact call order; the
   generator regex-parses these names to recover intent. Removing one default connection
   from the EMS constructor shifted every index and silently broke the export.
2. **The provenance problem.** A `DynamicComponent`'s ports come partly from its own
   constructor and partly from the setup function. The serializer must tell them apart —
   first via a hard-coded index, now (on the fix branch) via `__init_subclass__`-based
   constructor wrapping and a `created_during_construction` flag. Both are symptom patches:
   the flag rewrites every subclass constructor's semantics (an LSP violation), makes
   `add_component_output` mean different things depending on *when* it is called, and leaks
   a serialization concern into the runtime component model.
3. **Central special-casing.** `json_generator.py` and `json_executor.py` are riddled with
   `isinstance(component, DynamicComponent)` branches and per-class blocks (Car, UTSP LPG
   connector, Weather, EV charge controller). `Component` and `DynamicComponent` are not
   substitutable from the translator's point of view.
4. **Heuristic default-connection subtraction.** `remove_automatic_connections()` guesses
   which resolved connections came from defaults so it can omit them and store a global
   `connect_automatically` flag. It matches by name-prefix heuristics
   (`building_name + "_" + name`) and breaks on districts.
5. **Unreadable wiring.** Field-level connections explode a ~15-relationship household into
   ~100 JSON entries nobody can review or hand-edit.
6. **Silent failure.** The executor logs a warning and continues when a connection cannot
   be made, producing half-wired systems whose results look plausible but are wrong.
7. **String hacks.** Absolute paths are smuggled through `<<utils.HISIMPATH[...]>>`
   placeholders, and a blanket `humps.decamelize`/`pascalize` pass mangles nested
   `JsonReference` dicts, requiring per-class repair code.

## 2. Design principles (locked)

1. **Components save and load themselves.** Serialization and construction knowledge lives
   in the component/config classes, not in a central translator. The executor and generator
   treat every component identically — zero `isinstance`, zero class-name comparisons.
2. **Ports are derived, never serialized.** Static ports are created by the constructor from
   the config; aggregator ports are created by dynamic-connection resolution. The scenario JSON contains
   **no `inputs`/`outputs` sections** and no provenance information of any kind.
3. **Connections are a component-level adjacency list.** One unified `connections` list.
   Field-level expansion is always derived: from the target's declared defaults (bare pair),
   from dynamic-connection resolution (aggregators), or from explicit `fields` when wiring manually.
4. **Default XOR manual — never mixed.** For a given (source, target) pair you either use
   the default expansion or wire fields manually. Mixing is a hard error. There is no
   per-field exclude/suppression mechanism.
5. **Dynamic connections become declarative JSON entries.** The term is kept from the
   legacy mechanism, but a dynamic connection is now one data entry that carries both
   directions of an aggregator relationship: the monitored/consumed flow *and* the optional
   dispatch back-channel. Pairing of weights and target ports is correct by construction;
   the imperative add-API is retired.
6. **Resolution is deterministic.** Same JSON → identical port names, identical wiring,
   identical results. Sort rules and naming templates are part of this spec.
7. **Validation is strict.** The executor validates and hard-errors; it never repairs,
   never warns-and-continues.
8. **No blanket case conversion.** JSON keys are exactly the dataclass field names
   (snake_case). `humps` is removed from the translation path entirely.

## 3. Scenario file format

### 3.1 Top level

```json
{
    "schema_version": 2,
    "name": "Basic household",
    "description": "...",
    "components": [ ... ],
    "connections": [ ... ]
}
```

`multiple_buildings` remains in the simulation-parameters JSON, not here.

### 3.2 Component entries

```json
{
    "id": "Battery",
    "class": "hisim.components.advanced_battery_bslib.Battery",
    "config": { ... }
}
```

- `id` is the canonical instance identifier, unique per scenario, and the only name the
  `connections` list may reference. It defaults to `config.name` when omitted by an author,
  but the generator always writes it explicitly. District scenarios disambiguate by id
  (`BUI1_EMS`, `BUI2_EMS`) — no name-prefix guessing anywhere in the code.
- **Ids are opaque.** No code — executor, postprocessing, KPI computation, tooling — may
  parse structure out of an id (no prefix matching, no splitting on `_`). The
  `BUI1_`-style prefix is purely a human convention for choosing unique ids. This rule
  keeps the planned building-membership extension cheap: a future optional
  `"building": "BUI1"` field on the component entry (additive, non-breaking; ids stay
  flat and globally unique, the field is grouping metadata for per-building KPIs and
  result organization). When it lands, `building_name` migrates out of the configs into
  that field, and existing JSONs are back-filled mechanically from the prefix convention.
- `class` is the fully qualified component class. The config class is resolved from the
  constructor's type hints; if that fails, it is a hard error with a message telling the
  component author to override `build_from_scenario` (see §4.2). The separate
  `config_full_classname` field is dropped.
- `config` is produced/consumed by the config's own serialization hooks (§4.1). Keys are
  dataclass field names verbatim.
- The `connect_automatically` flag is **removed**.

### 3.3 Connection entries — four shapes, one list

**(a) Default pair.** The target expands it using its declared default connections for the
source's class (today's `get_default_connections_from_*` knowledge, which stays on the
target component):

```json
{"from": "Weather", "to": "Building"}
```

If the target is an aggregator (§4.4), a bare pair instead expands using the target's
*default dynamic connection* declaration for the source class (today's `dynamic_default_connections`
knowledge, refactored into data). Either way: bare pair = "target, apply your defaults for
this source class". No defaults declared → hard error.

**(b) Explicit single wire.** Dotted names address ports directly:

```json
{"from": "Weather.GlobalHorizontalIrradiance", "to": "PVSystem.Irradiance"}
```

**(c) Explicit field group.** Several manual wires between one pair, kept together:

```json
{"from": "Weather", "to": "PVSystem", "fields": [
    {"from": "GlobalHorizontalIrradiance", "to": "Irradiance"},
    {"from": "Azimuth", "to": "Azimuth"},
    {"from": "ApparentZenith", "to": "ApparentZenith"}
]}
```

A `fields` entry wires exactly those fields and nothing else — no defaults are applied.

**(d) Dynamic connection.** Addressed to an aggregator by component id. Carries tags and weight for the
forward (monitored) direction and optionally a `dispatch` block for the back-channel:

```json
{"from": "PVSystem", "to": "EMS",
 "tags": ["PV", "ELECTRICITY_PRODUCTION"], "weight": 999},

{"from": "Battery", "to": "EMS",
 "output": "AcBatteryPowerOutput",
 "tags": ["BATTERY", "ELECTRICITY_REAL"], "weight": 6,
 "dispatch": {"target_input": "LoadingPowerInput",
              "tags": ["BATTERY", "ELECTRICITY_TARGET"]}}
```

- `output` names the source port feeding the aggregator; it may be omitted when the
  aggregator's default dynamic connection for the source class specifies it.
- A dynamic connection **without** `dispatch` is monitored-only (occupancy consumption, meter feeds).
- A dynamic connection **with** `dispatch` makes the aggregator create a paired dispatch output and
  wire it into the named input of the participant. One entry, both directions, weights
  matched by construction.
- Multiple dynamic connections between the same pair are allowed (e.g. a heat pump registering space
  heating and DHW channels with different weights); exact duplicates are a hard error.
- Tags and weights are enum names / integers referencing `loadtypes.py`; unknown enum
  values are a hard error.

### 3.4 The don't-mix rule

Entries are grouped by ordered (from, to) pair. Each pair must be exclusively one of:

- exactly one **bare** entry (default expansion — static defaults or default dynamic connections,
  depending on the target), or
- one or more **manual** entries (single wires and/or one field group), or
- one or more **explicit dynamic connections**.

A bare entry combined with manual or explicit-dynamic-connection entries for the same pair is a hard
error. Manual wires to an aggregator's *static* ports (e.g. reading the EMS total-grid
output into a meter) are ordinary connections between a different pair or on static fields
and are unaffected by dynamic connections.

### 3.5 Resource references

The `<<utils.HISIMPATH[...]>>` string hack is replaced by `${var}` references resolved
through a `PathResolver` registry (`${inputs}`, `${utsp_results}`, `${cache}`, ...):

```json
"source_path": "${inputs}/weather/dwd_15min/..."
```

Configs that hold filesystem paths symbolize them in their save hook and resolve them in
their load hook using shared helpers; paths are stored with forward slashes and resolved
OS-independently. An unresolvable variable is a hard error.

### 3.6 Example (abridged household)

```json
{
    "schema_version": 2,
    "name": "Basic household",
    "description": "PV + battery + heat pump household with EMS",
    "components": [
        {"id": "Weather",   "class": "hisim.components.weather.Weather", "config": {"...": "..."}},
        {"id": "Occupancy", "class": "hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnector", "config": {"...": "..."}},
        {"id": "Building",  "class": "hisim.components.building.Building", "config": {"...": "..."}},
        {"id": "PVSystem",  "class": "hisim.components.generic_pv_system.PVSystem", "config": {"...": "..."}},
        {"id": "HeatPump",  "class": "hisim.components.advanced_heat_pump_hplib.HeatPumpHplib", "config": {"...": "..."}},
        {"id": "Battery",   "class": "hisim.components.advanced_battery_bslib.Battery", "config": {"...": "..."}},
        {"id": "EMS",       "class": "hisim.components.controller_l2_energy_management_system.L2GenericEnergyManagementSystem", "config": {"...": "..."}},
        {"id": "Meter",     "class": "hisim.components.electricity_meter.ElectricityMeter", "config": {"...": "..."}}
    ],
    "connections": [
        {"from": "Weather",   "to": "Building"},
        {"from": "Occupancy", "to": "Building"},
        {"from": "Weather",   "to": "PVSystem"},
        {"from": "Weather",   "to": "HeatPump"},
        {"from": "Building",  "to": "HeatPump"},

        {"from": "Occupancy", "to": "EMS"},
        {"from": "PVSystem",  "to": "EMS"},
        {"from": "HeatPump",  "to": "EMS",
         "tags": ["HEAT_PUMP", "ELECTRICITY_CONSUMPTION_EMS_CONTROLLED"], "weight": 2,
         "dispatch": {"target_input": "ElectricityTarget"}},
        {"from": "Battery",   "to": "EMS",
         "tags": ["BATTERY", "ELECTRICITY_REAL"], "weight": 6,
         "dispatch": {"target_input": "LoadingPowerInput"}},

        {"from": "EMS.TotalElectricityToOrFromGrid", "to": "Meter.ElectricityToOrFromGrid"}
    ]
}
```

Compare: the same system currently serializes to roughly a hundred field-level entries plus
per-component `inputs`/`outputs` arrays.

A complete, realistic mock-up — the full `household_heatpump_building_sizer` setup (14
components with their real configs) translated to this format — is checked in next to this
spec as `system_docs/household_heatpump_building_sizer.v2-example.json`.

## 4. Component protocol (Python side)

### 4.1 Serialization hooks on `ConfigBase`

```python
class ConfigBase(JSONWizard):
    def to_scenario_dict(self) -> dict:
        """Default: plain to_dict(). Override to symbolize paths, transform fields."""

    @classmethod
    def from_scenario_dict(cls, data: dict, ctx: "BuildContext") -> "ConfigBase":
        """Default: plain from_dict(). Override to resolve paths, transform fields."""
```

Known overrides, moved out of the central translator into the class they belong to:

- `WeatherConfig` — `source_path` symbolize/resolve via `PathResolver`.
- `UtspLpgConnectorConfig` — `result_dir_path` symbolization; its `JsonReference` fields
  serialize naturally because there is no blanket case conversion anymore.
- `L1Controller` (EV charging) — `charging_station_set` handling, same reason.

### 4.2 Construction hook and `BuildContext`

```python
class Component:
    @classmethod
    def build_from_scenario(cls, config: ConfigBase,
                            sim_params: SimulationParameters,
                            ctx: "BuildContext") -> "Component":
        return cls(config=config, my_simulation_parameters=sim_params)  # 95 % case
```

`BuildContext` exposes: `components_by_id` (all previously built components; build order is
file order), `sim_params`, `path_resolver`, and scenario metadata. `generic_car.Car`
overrides the hook to pull `GenericCarInformation` from the UTSP connector in the context —
replacing the hardcoded Car/UTSP block in the executor. A referenced component that does not
exist (e.g. a Car whose name is in no UTSP connector's `cars`) is a hard error raised by the
override itself.

### 4.3 Static default connections

The existing mechanism is kept as the expansion knowledge for bare pairs: the *target*
component declares, per source class, which field wires to make
(`add_default_connections(...)` / `get_default_connections_from_*`). Nothing changes at
runtime; what changes is that the JSON references this knowledge by pair instead of
inlining its output.

### 4.4 Aggregators and dynamic connections

An aggregator is a component that accepts 1..n participants. It implements:

```python
class Component:
    def resolve_dynamic_connections(self, connections: list[ResolvedDynamicConnection]) -> None:
        raise UnsupportedDynamicConnectionError(...)   # base class: hard error

class DynamicComponent(Component):
    def resolve_dynamic_connections(self, connections: list[ResolvedDynamicConnection]) -> None:
        """Create one input per dynamic connection, one dispatch output per dispatch block,
        and wire both directions."""
```

- **Default dynamic connections.** Aggregators declare per-source-class default dynamic connection parameters
  (tags, weight, source output, dispatch spec) as *data* — replacing both the legacy
  `dynamic_default_connections` dict and the EMS constructor's ~15 speculatively pre-created
  outputs, which are removed. A bare pair addressed to an aggregator expands from this
  declaration.
- **Terminology vs. legacy code.** The legacy dataclasses `DynamicConnectionInput`,
  `DynamicConnectionOutput` and `DynamicComponentConnection` stay in the codebase until the
  final deletion MR. The new classes never reuse those names: the parsed JSON shape is
  `DynamicConnectionEntry`, the resolved object handed to an aggregator is
  `ResolvedDynamicConnection`, the channel declaration is `DynamicConnectionChannel`. In a
  diff, any bare legacy name is old code.
- **Determinism.** Dynamic connections are resolved sorted by `(weight, source_id, source_output)`.
- **Port naming.** Names derive solely from the resolved dynamic connection, never from insertion
  order; the exact templates are frozen in §4.6.
- **Accepted tags.** Each aggregator declares the tag vocabulary it accepts as
  dynamic-connection channels (§4.5); dynamic connections are validated against that
  declaration before construction.
- Explicit wires may not reference resolution-created ports (they do not exist at parse
  time and the dispatch block covers the back-channel): hard error.
- The setup-facing mutators `add_component_output`, `add_component_input_and_connect` and
  `add_component_inputs_and_connect` are removed from the public API once migration
  completes; `DynamicComponent`'s tag-based runtime lookups (`get_dynamic_inputs`,
  `get_all_dynamic_outputs`, ...) are untouched.
- The runtime iteration logic of the EMS (`sort_source_weights_and_components` etc.) reads
  from the resolved dynamic connection structure instead of `my_component_inputs` bookkeeping.
- Participant configs currently carrying `source_weight` fields stop being the source of
  weight; the weight lives in the connection entry. Config fields are deprecated and
  removed at the end of migration.

Aggregators in scope: `L2GenericEnergyManagementSystem`, `ElectricityMeter`, `GasMeter`,
`FuelMeter`, `HeatingMeter` (monitored-only, no dispatch), `Building` (dynamic heat
sources). EMS migrates first as proof of the mechanism (§7).

### 4.5 Dynamic-connection channels — the aggregator's declared tag vocabulary

Today the tags an aggregator understands exist only implicitly, in hardcoded
`get_dynamic_inputs(tags=[...])` calls inside its runtime methods. Nothing can validate an
dynamic connection against them: a dynamic connection whose tags match no query wires cleanly and is silently
never read. To close this, every aggregator declares its **dynamic-connection channels** as class-level
data:

```python
@dataclass(frozen=True)
class DynamicConnectionChannel:
    key: str                        # stable identifier, e.g. "production"
    tags: frozenset[lt.InandOutputType]
    load_type: lt.LoadTypes
    unit: lt.Units
    dispatch: DispatchRule          # FORBIDDEN | OPTIONAL | REQUIRED
    dispatch_tags: frozenset[lt.InandOutputType] = frozenset()

class L2GenericEnergyManagementSystem(DynamicComponent):
    CHANNELS = (
        DynamicConnectionChannel("production",               {ELECTRICITY_PRODUCTION},                 ELECTRICITY, WATT, DispatchRule.FORBIDDEN),
        DynamicConnectionChannel("consumption_uncontrolled", {ELECTRICITY_CONSUMPTION_UNCONTROLLED},   ELECTRICITY, WATT, DispatchRule.FORBIDDEN),
        DynamicConnectionChannel("consumption_controlled",   {ELECTRICITY_CONSUMPTION_EMS_CONTROLLED}, ELECTRICITY, WATT, DispatchRule.REQUIRED, {ELECTRICITY_TARGET}),
        DynamicConnectionChannel("storage",                  {ELECTRICITY_REAL},                       ELECTRICITY, WATT, DispatchRule.REQUIRED, {ELECTRICITY_TARGET}),
    )
```

Rules:

- **Matching.** A dynamic connection's `InandOutputType` tags must match **exactly one** channel of
  the target aggregator — zero matches and ambiguous matches are both hard errors.
  `ComponentType` tags (`BATTERY`, `BUILDINGS`, ...) are participant metadata, not channel
  selectors; they remain free-form and are used for filtering in KPI/postprocessing (e.g.
  the electricity meter's per-building district sums).
- **Static validation.** Because the channel declares `load_type`/`unit` and the dispatch
  rule, dynamic connections are fully validatable in the executor's step 3 — before any component port
  exists. A `dispatch` block on a `FORBIDDEN` channel, a missing one on a `REQUIRED`
  channel, or a load-type/unit mismatch all fail at parse time.
- **No drift.** Runtime code queries by channel key (`self.get_channel_inputs("production")`),
  not by raw tag lists — the declaration is the single source of truth for both validation
  and simulation. The contract test (§4.7) additionally asserts that every default-dynamic-connection
  declaration references a declared channel.
- **Why tags, not channel keys, are the wire format.** One could imagine JSON entries
  referencing a channel directly (`"channel": "storage"`) instead of carrying tags. This is
  deliberately rejected. Tags describe the *participant* from its own point of view ("I am
  a battery; this flow is real measured electricity") and are strategy-agnostic; channels
  describe *one aggregator's interpretation* of those tags. Keeping tags as the wire format
  preserves **strategy substitution**: a scenario can swap the EMS `class` for a different
  strategy implementation (peak shaving, price-driven, ...) without touching a single
  connection entry — the new strategy declares its own channels over the same tag
  vocabulary and reinterprets the same participants. Channel matching still validates every
  entry against whichever aggregator is currently the target, so a strategy whose channels
  do not cover a participant's tags fails at parse time rather than silently ignoring it.
- **Machine-readable interface.** `CHANNELS`, together with the config schema and
  default-connection declarations, gives each component a complete machine-readable
  interface description. A schema-dump command (e.g. `python -m hisim.scenario_schema`)
  can export these for tooling — this is the foundation the planned JSON-authoring GUI
  (§9.2) builds its component palette and wiring validation on.

### 4.6 Naming templates (frozen)

These names appear in result files and KPI lookups, so they are part of the spec, not an
implementation detail. All template variables come from the *resolved* dynamic connection (i.e. after
default-dynamic-connection expansion), so a bare pair and an explicit dynamic connection with the same parameters
produce identical names. No template contains an index counter; any collision is a hard
error (§5).

| Name | Template | Example |
|---|---|---|
| Component instance id | author-chosen, `[A-Za-z0-9_]+`, unique per scenario; district convention `{building_id}_{role}` | `Battery`, `BUI1_EMS` |
| Static port | class-declared constant, unchanged by this spec | `ElectricityOutput` |
| Aggregator input (one per dynamic connection) | `{source_output}From{source_id}` | `AcBatteryPowerOutputFromBattery` |
| Dispatch output (one per `dispatch` block) | `DispatchTo{source_id}_{target_input}` | `DispatchToBattery_LoadingPowerInput` |

Uniqueness follows structurally from the duplicate rules: `(source_id, source_output)` is
unique per aggregator (duplicate dynamic connections are rejected), so input names are unique;
`(source_id, target_input)` is unique (a duplicate wire into the same input is rejected),
so dispatch names are unique. Multiple dynamic connections from one source (e.g. a heat pump's space
heating and DHW channels) differ in `source_output` and/or `target_input` and therefore
get distinct names — e.g. `DispatchToHeatPump_ElectricityTargetSH` vs
`DispatchToHeatPump_ElectricityTargetDHW`.

Result-file and display naming keeps the existing `object_name` / `field_name` convention;
only the `field_name` part changes, from `OutputN`-style names to the templates above.

### 4.7 Contract test (replaces all index/provenance heuristics)

One parametrized test over every component in `hisim/components`:

1. Build from default config → `to_scenario_dict` → `from_scenario_dict` →
   `build_from_scenario` → `to_scenario_dict` again.
2. Assert: both dicts identical; port sets (names, load types, units) identical.
3. For aggregators additionally: default-dynamic-connection resolution of a fixed participant set is
   idempotent and order-independent (shuffled input, identical result).
4. For aggregators additionally: every default-dynamic-connection declaration matches exactly one
   declared dynamic-connection channel (§4.5), and every channel is reachable (no dead channel that
   no default or known participant can address — a warning-level check at first).

A new component that breaks the "ports = f(class, config)" invariant or writes a lossy
save hook fails this test by name.

## 5. Executor build lifecycle

1. **Parse & schema-validate** the file (pydantic model, `schema_version` checked).
2. **Instantiate** components in file order via `from_scenario_dict` +
   `build_from_scenario`; `BuildContext` grows as it goes.
3. **Static validation** of the connection list: ids resolve, shapes well-formed,
   don't-mix rule (§3.4), duplicate detection.
4. **Apply** bare-pair defaults and explicit wires.
5. **Resolve dynamic connections**, grouped by target, deterministic order (§4.4).
6. **Final validation**, then register everything with the `Simulator`.

**Hard-error catalog** (executor never warns-and-continues):

- unknown `schema_version` / malformed entry shape
- unknown component id in any entry; unknown port name in an explicit wire
- config class not resolvable and no `build_from_scenario` override
- bare pair whose target declares no defaults (static or dynamic) for the source class
- dynamic connection addressed to a component that does not resolve dynamic connections
- dynamic connection whose tags match zero or more than one declared channel of the target (§4.5)
- `dispatch` block present on a `FORBIDDEN` channel, or absent on a `REQUIRED` channel
- default XOR manual violated for a pair; exact duplicate dynamic connection; duplicate wire
  (same source port → same target input, across all entries and expansions)
- explicit wire referencing a resolution-created port
- unresolvable `${var}` reference; unknown tag/unit/load-type enum value
- load-type or unit mismatch on any wire
- mandatory input still unconnected after step 5 (unless the port was declared with
  `allow_unconnected_mandatory`)
- port-name collision during dynamic-connection resolution

The current `log.warning` + `continue` in `json_executor.py` is deleted.

**Audit artifact:** the existing resolved-connections log (`component_connections.json`)
is written on every run and serves as the expansion "lockfile": if a component's default
set changes in a new HiSim version, the same scenario JSON wires differently — that is
intended (defaults are a dependency like component physics), and the golden parity gate
plus this log make any drift visible and reviewable.

## 6. Generation from Python setups

Generation becomes **recording, not introspection**. The framework records the model at the
moment intent is executed — nothing is reverse-engineered from the object graph:

- `sim.add_component(c)` → component entry via the save protocol (`id` = instance name).
- Applying default connections (`connect_automatically` today, an explicit
  `sim.connect_default(source, target)` after migration) → one bare pair entry.
- `connect_input(...)` → explicit wire entry.
- New call `sim.connect_dynamic(source, aggregator, tags=..., weight=..., output=...,
  dispatch=...)` (or the no-arg default variant) → dynamic-connection entry. This single call replaces
  today's triple of `add_component_inputs_and_connect` + `add_component_output` +
  back-channel `connect_input` in every setup.

`write_standalone_scenario_json` serializes the recorded model. `remove_automatic_connections`,
`get_unique_connections`-based subtraction, and all regex name parsing are deleted. Because
setups end up calling the same declarative API the JSON expresses, generation is pure
serialization and roundtrip fidelity is structural, not tested-into-existence.

## 7. Migration plan

Each phase lands behind the existing JSON golden parity gate; goldens regenerate per phase.
The phase list below is the coarse view; the MR-level decomposition (~18 individually
reviewable and testable MRs with dependency graph) is maintained in
`roadmap/json_scenario_v2_mr_plan.md`.

1. **Foundations.** v2 pydantic schema, `PathResolver`, protocol hooks with default
   implementations on `ConfigBase`/`Component`, `BuildContext`, v2 executor skeleton
   (static components + explicit wires + bare pairs), contract test. v1 executor still
   runs the existing 23 JSONs.
2. **Special-case components.** Weather, UTSP connector, Car, EV charge controller get
   their overrides; the corresponding central blocks and all `humps` usage die.
3. **Aggregators, one per PR, EMS first.** Remove constructor-created dynamic outputs,
   implement `resolve_dynamic_connections` + default-dynamic-connection declarations, update every setup touching
   that component to `connect_dynamic`, regenerate affected goldens. Then the four meters,
   then Building. Dynamic output field names change (`OutputN` → semantic names), so
   result-file goldens regenerate here.
4. **Setup conversion.** Remaining setups moved to the recording API; all 23 scenario
   JSONs regenerated as v2; parity gate green across the matrix.
5. **Deletion (straight cutover, no v1 compatibility window).** v1 generator/executor
   paths, `connect_automatically`,
   `dynamic_default_connections`, the public dynamic add-API, `OutputN` naming, and
   deprecated `source_weight` config fields are removed. The
   `fix_json_scenario_translator` provenance instrumentation is superseded and not merged;
   independent fixes on that branch (README, skip-reason reporting, regenerated JSONs)
   are cherry-picked separately.

## 8. What this design deletes

- `created_during_construction` flag, `__init_subclass__` constructor wrapping,
  construction-depth counters (fix branch)
- hard-coded EMS output index (main)
- all regex parsing of generated port names
- `remove_automatic_connections` / `compare_automatic_connections` / `delete_connections`
- every `isinstance(_, DynamicComponent)` and class-name string comparison in
  `json_generator.py` / `json_executor.py`
- `humps` from the translation path; `<<...>>` placeholder strings
- warn-and-continue connection handling
- per-component `inputs`/`outputs` arrays and `connect_automatically` in the JSON

## 9. Resolved design questions (decided 2026-08-17)

1. **v1 compatibility window: none.** Straight cutover — all scenario JSONs are
   regenerated as v2 in Phase 4 and the v1 format stops loading in Phase 5. No external
   consumer requires v1 files to keep working.
2. **End state of Python setups: kept for now, retired later.** After migration the setups
   call the declarative recording API and remain the authoring path. The long-term plan is
   a GUI that authors scenario JSONs directly; once that exists, Python setups are retired
   and JSON becomes the sole setup format. The recording API is therefore transitional but
   not throwaway — it is what keeps generation lossless until the GUI cutover.
3. **Naming templates: frozen** as specified in §4.6.
4. **`Building` as aggregator: confirmed.** Its dynamic heat-source inputs map onto
   monitored-only dynamic connections (no `dispatch` block); it needs no special treatment beyond the
   standard aggregator protocol.
