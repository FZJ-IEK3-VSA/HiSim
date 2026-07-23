# HiSim Scenario Editor — Specification

## Goal

A browser-based visual editor for creating and editing HiSim `*.scenario.json` files.
The scenario is represented as a node graph: each component is a card with input ports on
the left and output ports on the right; connections are bezier curves between ports. The
editor exports valid scenario JSON that HiSim can run directly.

---

## Databases

All databases are generated or discovered automatically so they stay in sync with the
Python source without manual maintenance.

### 1. Component Registry *(auto-generated from Python source)*

The central database. A Python script (`tools/generate_component_db.py`) imports every
module under `hisim/components/`, finds all `Component` subclasses, instantiates each
with its default config, and records:

| Field | Source |
|---|---|
| `component_full_classname` | `__module__` + `__qualname__` |
| `config_full_classname` | `__init__` type hint of first arg |
| `display_name` | class docstring first line, or class name |
| `category` | module name segment (e.g. `generic_heat_pump` → *Heating*) |
| `default_config` | result of `get_default_*()` classmethod |
| `input_ports` | result of `add_input()` calls collected during `__init__` |
| `output_ports` | result of `add_output()` calls collected during `__init__` |
| `default_connections` | result of `add_default_connections()` |
| `is_dynamic` | `isinstance(comp, DynamicComponent)` |

Each port entry records: `field_name`, `load_type`, `unit`, `mandatory`, `tags`,
`weight`, `postprocessing_flag`, `sankey_flow_direction`, `description`.

The script emits `component_db.json`. It can be run as a pre-build step or CI job, and
re-run whenever components change.

### 2. Enum / LoadType Registry *(auto-generated)*

Generated from `hisim/loadtypes.py` by the same script. Exported as `enum_db.json`:

- `LoadTypes` — drives port color-coding and connection compatibility checks
- `Units` — paired with `LoadTypes` for validation (only compatible unit–loadtype
  combinations allowed)
- `ComponentType` tags and `InandOutputType` tags — for dynamic input matching display
- `BuildingCodes` — dropdown values for the Building component
- `Locations` — dropdown values for Weather and PV

### 3. Simulation Presets *(auto-discovered)*

Discovered by globbing `system_setups/*.simulation.json` at startup. Presented in a
dropdown so the user can pick a pre-defined simulation config or create a custom one.
Fields map 1:1 to the simulation JSON spec.

### 4. Domain Catalogs *(static or semi-static)*

Richer dropdown data for specific config fields. Generated once and updated when source
data changes:

| Catalog | Used by | Source |
|---|---|---|
| Weather datasets | `WeatherConfig.source_path` | Glob `hisim/inputs/weather/**` |
| Household profiles | `UtspLpgConnectorConfig.household` | LPG profile list (name + GUID) |
| Heat pump models | `GenericHeatPumpConfig` | Heat pump data files in `hisim/inputs/` |
| PV modules | `PVSystemConfig.module_name` | pvlib local database or cached JSON |
| PV inverters | `PVSystemConfig.inverter_name` | pvlib local database or cached JSON |

### 5. Post-Processing Options Registry *(auto-generated)*

Enumerated from `hisim/postprocessingoptions.py`. Each entry: string value +
human-readable description. Drives the checkboxes in the simulation settings panel.

---

## UI Layout

```
┌──────────────┬─────────────────────────────────────────┬─────────────────┐
│              │  Toolbar                                │                 │
│  Component   ├─────────────────────────────────────────┤   Inspector     │
│  Palette     │                                         │   Panel         │
│              │                                         │                 │
│  [search]    │         Canvas (node graph)             │  (config form   │
│              │                                         │   for selected  │
│  ▸ Weather   │                                         │   component)    │
│  ▸ Occupancy │                                         │                 │
│  ▸ Heating   │                                         │                 │
│  ▸ PV        │                                         │                 │
│  ▸ Storage   │                                         │                 │
│  ▸ EV        │                                         │                 │
│  ▸ Metering  │                                         │                 │
│  ▸ Control   │                                         │                 │
│              ├─────────────────────────────────────────┤                 │
│              │  Validation status bar                  │                 │
└──────────────┴─────────────────────────────────────────┴─────────────────┘
```

---

## Canvas — Node Graph

### Component card anatomy

```
        ┌─────────────────────────────────┐
        │ MyWeather             [Weather] │  ← header
        ├─────────────────────────────────┤
  ○─────┤ SomeInput    WeatherOutput      ├─────○
        │              TemperatureOutside ├─────○
        ├─────────────────────────────────┤
        │  location: Aachen               │  ← config summary (expanded)
        │  data_source: TRY               │
        └─────────────────────────────────┘
       input ports (left)        output ports (right)
```

- **Header**: editable instance name on the left + muted class label on the right
- **Input ports** on the left edge: colored dot outside + field name label inside
- **Output ports** on the right edge: field name label inside + colored dot outside
- **Port color** determined by `LoadType` (e.g. yellow = Electricity, blue = Heat,
  green = Gas, grey = Other)
- **Config summary** below the ports: a selection of the component's config dataclass
  fields (i.e. the constructor parameters of the `ConfigBase` subclass) shown inline as
  `key: value` pairs when the card is expanded; the full set is editable in the
  inspector panel
- Collapsed card shows only the header and ports; click the header to expand/collapse

### Connections

- Bezier curves between ports, colored by `LoadType` of the connected output
- Incompatible connections (mismatched `LoadType`/`Unit`) are prevented on drop; a
  tooltip explains why
- Click a connection to select it; `Delete` key removes it

### Canvas interactions

| Interaction | Effect |
|---|---|
| Drag component from palette | Add component with default config (ask for the necessary constructor parameters of the `ConfigBase` subclass, if any), ask whether to connect automatically |
| Drag output port → input port | Create connection |
| Click component | Select; open inspector |
| Click canvas background | Deselect |
| Click connection line | Select connection |
| `Delete` / `Backspace` | Delete selected component(s) or connection(s) |
| Right-click component | Context menu: *Delete*, *Duplicate*, *Auto-connect this* |
| Scroll / pinch | Zoom |
| Middle-drag or space-drag | Pan |
| `Ctrl+Z` / `Ctrl+Y` | Undo / redo |
| `Ctrl+A` | Select all |

---

## Toolbar

| Control | Action |
|---|---|
| **New** | Clear canvas, start empty scenario |
| **Open JSON** | Import an existing `*.scenario.json`; reconstruct graph |
| **Save JSON** | Export current graph as `*.scenario.json` |
| **Validate** | Run full validation, show detailed report |
| **Auto-connect all** | Apply `add_default_connections()` logic to all components |
| **Simulation settings** | Open dialog to configure / pick the `*.simulation.json` side |

---

## Inspector Panel

Context-sensitive; shows the selected component's config fields as a form:

- **Text fields**: plain input with placeholder from default value
- **Number fields**: number input with unit hint
- **Boolean fields**: toggle switch
- **Enum fields**: dropdown populated from the appropriate registry (e.g. `BuildingCodes`,
  `Locations`, household profiles)
- **`connect_automatically` toggle**: controls whether the simulator auto-wires defaults
  at runtime
- **Inputs / Outputs tabs**: for dynamic components, shows the port list with add/remove
  controls
- **GUID fields** (for LPG): presented as a dropdown by profile name; GUID stored
  internally

Changes in the inspector are reflected immediately on the canvas card.

---

## Validation

### Real-time (inline)

- Unconnected **mandatory** input ports highlighted in red with a badge
- Incompatible port types highlighted in red on hover/drop attempt
- Duplicate instance names flagged inline

### Full validation (on demand)

Triggered by the Validate button or before export:

1. All mandatory inputs connected
2. All connections use compatible `LoadType` + `Unit` pairs
3. No duplicate `name` fields within the same `building_name`
4. No orphaned connections referencing non-existent ports
5. Required config fields non-null (derived from the component registry's `mandatory`
   metadata)
6. Warnings (not errors) for components with `connect_automatically: false` that have
   unconnected non-mandatory inputs

---

## Auto-connect Logic

Mirrors the Python `add_default_connections()` mechanism:

1. When a component is added and the user selects to auto-connect it (or *Auto-connect* is triggered), look up its
   `default_connections` from the component registry.
2. For each declared default connection, find a component on the canvas that matches by
   class.
3. If exactly one match exists, draw the connection automatically.
4. If zero or multiple matches exist, highlight the port as "needs manual wiring" and
   list the candidates in the inspector.

"Auto-connect this" on a right-click applies only to that component. "Auto-connect all"
in the toolbar applies to every component on the canvas.

---

## Import / Export

### Import (`*.scenario.json`)

1. Parse JSON.
2. For each entry in `components`, look up the class in the component registry to
   retrieve port metadata.
3. Place component cards on the canvas using auto-layout (or restore positions from the
   `_editor_positions` extension field if present).
4. Reconstruct explicit connections from `connections[]`.
5. Reconstruct dynamic inputs from `inputs[]`.
6. Warn about any component class not found in the registry (may be from a newer version
   of HiSim).

### Export (`*.scenario.json`)

1. Validate; block export on errors (show warnings, allow override).
2. Serialize to the exact schema described in `system_setups/README.md`.
3. Strip the `_editor_positions` extension field before writing.
4. Offer to also export / select a simulation JSON alongside the scenario JSON.

---

## Auto-generation Script

`tools/generate_component_db.py`:

1. Walks `hisim/components/` and imports every module.
2. For each `Component` subclass found:
   a. Calls `get_default_*()` to get the default config.
   b. Instantiates the component inside a mock simulator context to capture
      `add_input()` / `add_output()` / `add_default_connections()` calls.
   c. Records all metadata.
3. Walks `hisim/loadtypes.py` and `hisim/postprocessingoptions.py` for enum registries.
4. Writes `component_db.json` and `enum_db.json` to `editor/public/data/`.
5. Exits non-zero if any component fails to introspect, so CI catches regressions.

Run via:

```bash
python tools/generate_component_db.py
```

Can be added to a `pre-build` npm script or a GitHub Actions step triggered by changes
to `hisim/components/` or `hisim/loadtypes.py`.

---

## Technology

| Concern | Candidate |
|---|---|
| Framework | React (or Svelte for smaller bundle) |
| Node graph | React Flow — handles ports, edges, pan/zoom, undo out of the box |
| Forms | React Hook Form + Zod (schema derived from component registry) |
| Styling | Tailwind CSS |
| Build | Vite |
| State | Zustand (lightweight, works well with React Flow) |
| Packaging | Standalone static site; no backend needed — all data is in the generated JSON databases |

---

## Implementation Plan

Each phase is independently testable before the next begins.

### Phase 1 — Data foundation

Write `tools/generate_component_db.py`:

- Walk `hisim/components/`, import every module, collect all `Component` subclasses
- Instantiate each with its default config inside a mock simulator context to capture
  port declarations and default connections
- Walk `hisim/loadtypes.py` and `hisim/postprocessingoptions.py` for enum registries
- Emit `editor/public/data/component_db.json` and `editor/public/data/enum_db.json`
- Exit non-zero on any introspection failure

Verify the output manually against a known component (e.g. `Weather`, `GenericHeatPump`)
before moving on.

### Phase 2 — Frontend scaffold

Initialize the editor project inside `system_setups/editor/`:

- Vite + React + TypeScript project skeleton
- Add React Flow, Tailwind CSS, Zustand
- Three-panel shell: palette (left) · canvas (centre) · inspector (right)
- Toolbar strip across the top, validation status bar across the bottom
- All panels are stubs — no real content yet; confirm layout renders correctly

### Phase 3 — Component palette and canvas nodes

- Load `component_db.json` at startup; group components by `category`
- Render palette: collapsible category groups, search/filter box
- Drag a component from the palette onto the canvas → a card appears with:
  - Header (instance name + class label)
  - Input ports on the left, output ports on the right (colored by `LoadType`)
  - Collapsed config summary (a few key `ConfigBase` fields as `key: value`)
- Click header to expand/collapse the config summary

### Phase 4 — Connections

- Enable port-to-port dragging in React Flow to draw edges
- Color each edge by the `LoadType` of its source output port
- Enforce compatibility on drop: reject connections where `LoadType` or `Unit` do not
  match; show a tooltip explaining the mismatch
- Click an edge to select it; `Delete` removes it

### Phase 5 — Inspector panel

- Clicking a component opens the inspector with a generated form for all `ConfigBase`
  fields:
  - Text, number, boolean controls for primitive fields
  - Dropdowns populated from `enum_db.json` for enum fields (`BuildingCodes`,
    `Locations`, etc.)
  - `connect_automatically` toggle
  - Inputs / Outputs tabs for dynamic components
- Changes in the inspector update the canvas card's config summary in real time

### Phase 6 — Import and export

- **Export**: serialize the current canvas state to a valid `*.scenario.json` (matching
  the schema in `system_setups/README.md`); strip the `_editor_positions` extension
  field
- **Import**: parse an existing `*.scenario.json`, reconstruct all component cards and
  connections, restore positions from `_editor_positions` if present, auto-layout
  otherwise
- Round-trip test: import `basic_household.scenario.json`, export, diff against the
  original

### Phase 7 — Auto-connect

- Implement the `add_default_connections()` logic in the editor using the
  `default_connections` entries in `component_db.json`
- "Auto-connect this" (right-click menu): wire default connections for one component
- "Auto-connect all" (toolbar): apply to every component on the canvas
- Unresolved connections (zero or multiple candidates) highlighted on the port with a
  list of candidates shown in the inspector

### Phase 8 — Validation

- Real-time: highlight unconnected mandatory input ports in red; flag duplicate instance
  names inline
- Full validation (Validate button and pre-export check):
  1. All mandatory inputs connected
  2. All edges have matching `LoadType` + `Unit`
  3. No duplicate `name` fields within the same `building_name`
  4. No orphaned edges referencing non-existent ports
  5. Required config fields non-null
  6. Warnings for non-mandatory unconnected inputs on manually-wired components
- Validation report shown in a modal or the status bar

### Phase 9 — Domain catalogs

Enrich the inspector dropdowns with the richer domain data:

- Weather dataset paths (globbed from `hisim/inputs/`)
- Household profiles (name + GUID pairs for LPG connector)
- Heat pump manufacturer / model pairs
- PV module and inverter lists from pvlib cache

Each catalog is generated by the same Python script (Phase 1) or a companion script and
written to `editor/public/data/`.

### Optional: Phase 10 — Simulation settings

- "Simulation settings" toolbar button opens a dialog
- Preset picker: dropdown of discovered `*.simulation.json` files (auto-discovered at
  startup)
- Full form for all simulation JSON fields (`start_date`, `end_date`,
  `seconds_per_timestep`, `post_processing_options` checkboxes, etc.)
- Export exports both `*.scenario.json` and `*.simulation.json` together

### Phase 11 — Polish

- Undo / redo (React Flow's built-in history or Zustand middleware)
- Keyboard shortcuts (`Ctrl+Z`, `Ctrl+A`, `Delete`, etc.)
- Save / restore canvas node positions via `_editor_positions` extension field in the
  scenario JSON
- Auto-layout algorithm for imported files that have no saved positions


### Phase 12

- Systematically scan for flaws and improve on them
