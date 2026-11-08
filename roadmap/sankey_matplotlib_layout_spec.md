# Spec: every energy-system YAML gets a matplotlib Sankey in the chained style, laid out automatically, with a warnings box and a data panel

Status: decided (owner, 2026-11-08), nothing built. Owner: Noah Pflugradt. Epic: hisim-energy-sankeys-9uoo. Builds on:
`roadmap/complete_energy_sankeys_spec.md` (the graph model, §4; its D4 is amended here), `roadmap/fluid_loops_spec.md`
(the loop columns of the balance report), #1122 (loop specs), the prototype of 2026-11-08
(`agent-tmp/sankey-matplotlib-proto/`, not in the repository: twelve hand layouts of the building-sizer systems in
one style, a trial with temperatures, specific values and plausibility checks, and a second trial with every
warning in one box) and the May 2026 comparison (`agent-tmp/sankey-may2026/`, the "unaccounted loss/gain" arrows).

All code citations are against `origin/main` at b58f31a1a.

## 1. Problem

The energy Sankey of a run is a plotly HTML per view (`hisim/postprocessing/energy_balance/sankey.py`,
`SankeyDrawing`). Plotly places nodes in columns by topology and has no collision handling: in the full-year
`overall` view of `household_heatpump_building_sizer` the battery's loss labels sit on the hot-water tank, the
building's losses overlap `grid export`, and the battery draws a ring from and to the meter (bead
hisim-sankey-layout-48b8). The picture does not say what a reader of a heating system wants to know first: which
chain the heat takes from the meter to the rooms, how big the losses beside it are, what temperatures the loops run
at, and whether the numbers are plausible. The PDF report has no Sankey at all (bead hisim-4hed).

The prototype of 2026-11-08 drew the twelve building-sizer systems as "chained sub-diagram" Sankeys: every component
a trunk with its name inside, the main heat chain on one row from left to right, losses peeling off at the side,
storages above and below, every cross-link a connected band. All twelve reached 0 label overlaps, with 56 to 187
lines of hand layout each, about 10 minutes of agent time per system including the overlap checker. The owner
reviewed them and decided on a computed layout for every energy-system file (§2). The two trials added loop
temperatures, specific values, comfort figures, plausibility markers, one warnings box and a data panel; the second
trial's figure (`figures/trial/household_heatpump_building_sizer_trial.png` of the prototype) is the target picture.

The prototype also showed where `matplotlib.sankey` stands in the way: it places sub-diagrams implicitly through a
chain of path lengths, orders flows along a side by list rules, rotates a connected sub-diagram's frame, allows one
connection per sub-diagram, and has no label handling. Every feature the prototype added (connectors, flattened
stubs, placed trees) worked around the library.

## 2. Decisions

All taken by the owner on 2026-11-08 after reviewing the prototype's round 2 and the two trials.

**D1 — Every energy-system YAML gets a matplotlib Sankey in the chained sub-diagram style, with an automatically
computed layout; an optional per-file pin file may fix individual placements** (owner, 2026-11-08). The picture of
the prototype is the one the owner wants for every system, and twelve hand layouts showed that most of each layout
follows from the graph. Consequence: a layout engine (§6) derives roles, the main chain, the spanning tree,
orientations, sizes, placements, routes and labels from `energy_sankey.json`; a pin file (§6.10) overrides single
choices for showcase figures and is never required. *This amends D4 of `roadmap/complete_energy_sankeys_spec.md`*
("the renderer is plotly, as today; an arrow-style renderer is a later step"): the arrow-style renderer is now this
spec, built as its §10 plans; bead hisim-energy-sankeys-9uoo.19 (the arrow renderer) and hisim-4hed (static images
for the PDF report) close with it. The graph model of that spec (§4) and `energy_sankey.json` stay as they are:
one graph, now two renderers (§4.3, Q1).

**D2 — The renderer is HiSim's own band renderer with absolute positions, not built on `matplotlib.sankey`**
(owner, 2026-11-08). The prototype mostly worked around that library's implicit positions. Consequence: the
renderer draws trunks, arrows, notches and connector bands as its own matplotlib polygons from coordinates the
layout engine computed (§6.11); only the look of `matplotlib.sankey` (trunk, side branches, arrowheads, notches)
is kept.

**D3 — The PV system is its own visible element, drawn from its AC electricity for now** (owner, 2026-11-08). The
sunlight and the module-and-inverter loss would make the PV the widest band of the picture (126.9 MWh of sun
against 26.3 MWh of electricity). Consequence: a component that converts `SOLAR` into `ELECTRICITY` is drawn with
a flat tail that carries the net of its solar input and solar losses, unlabelled; its block shows kWp and kWh/kWp.
The space left of the block is the slot for the sun and the loss arrows once bead hisim-device-location-820d.5
publishes the DC output and the inverter loss; then the cut goes and the diagram starts after the modules. A
component that converts `SOLAR` into heat (the collector) keeps its sunlight and its losses, as the prototype
drew them.

**D4 — Every cross-link is a connected band with rounded corners** (owner, 2026-11-08). Merges (a generator's
second input from the same meter), loops (the battery's discharge back to the meter) and far links (the household's
heat into the building) are drawn as bands routed from stub to stub, never as a pair of labelled stubs.
Consequence: the router of §6.7.

**D5 — On the bands and nodes only values and KPIs** (owner, 2026-11-08): flows, efficiencies (heat pump SPF and
ambient share, boiler efficiency, battery round trip, collector efficiency, substation efficiency), specific values
(kWh/(m²a), kWh/kWp, kWh per person, full cycles), flow-weighted loop temperatures, comfort values. A value with a
warning gets a small numbered marker (⚠n), red for an error, orange for a plausibility warning. No ranges, sources
or sentences on the diagram. Consequence: §7 (metrics) and §8 (warnings).

**D6 — All warnings in one box in the bottom-right corner** (owner, 2026-11-08), sorted by component in the
diagram's order, errors (unbalanced or undeclared component, demand not delivered, above a design limit) before
plausibility warnings within a component. Each entry: number, component, value and range, one or two sentences of
context, and a source with organisation, title, year and table, page or URL, or the visible words "estimate,
unsourced". Consequence: §8.2.

**D7 — A data panel below the diagram** (owner, 2026-11-08): runtime, starts, full-load hours, peak powers,
battery cycles and state of charge, storage temperature ranges, building and comfort figures, and the run's key
results. Consequence: §8.3. The panel takes the detail off the bands; the trial's first version, with everything on
the diagram, had 20 overlaps.

**D8 — Loop temperatures are flow-weighted statistics computed in HiSim's post-processing and exported in the
balance report, not read from a CSV** (owner, 2026-11-08): mean supply and return weighted by the mass flow over
the steps the loop carries heat (not the plain time average, which includes idle steps), the maximum as the 99th
percentile of the flow-weighted supply, and the hours above the emitter's design supply (red when exceeded); for
the hot-water tank the hours below 40 °C and at or above 60 °C. Consequence: §5, the balance report schema moves
from `hisim.energy_balance/5` to 6 (loops) and 7 (device and storage states).

**D9 — Plausibility ranges live in a versioned table in the repository, every range with its source, and a source
enters the table only after a person checked it against the original** (owner, 2026-11-08). The prototype's nine
ranges are drafts: three are bands or estimates, the DIN V 18599-10 value and the Nature Energy citation were not
checked. Consequence: §7.3; an entry without a recorded check is drawn as "estimate, unsourced", and a `base` test
refuses an entry that claims neither.

**D10 — A minimum drawn band width, with a "†" after the value and a caption note for every band drawn wider than
its value** (owner, 2026-11-08). The prototype used 2 px. Consequence: §6.5; the numbers in the labels stay exact,
and a trunk whose inflated bands no longer balance tapers.

**D11 — An unbalanced or undeclared component is drawn closed by an explicit, visibly marked residual, and is an
error entry in the warnings box** (owner, 2026-11-08). The May 2026 comparison drew every gap of the old version as
an "unaccounted loss" or "unaccounted gain" arrow; the district-heating twin's heat distribution declares no energy
ports today and shows a 20 kWh gap. Consequence: §6.3; nothing is invented to close a component, and the picture
never hides a gap.

**D12 — No winter-week Sankey for now** (owner, 2026-11-08). One figure per run, over the run's whole period.

## 3. Terms

- **Drawn node**: a component, meter or undeclared node of the `overall` view (`NodeKind.COMPONENT`, `METER`,
  `UNDECLARED` of `sankey.py`), drawn as one trunk. Boundary, loss, storage and unaccounted nodes are not drawn as
  trunks: their edges become terminal flows of the drawn node they touch.
- **Flow**: one edge of the `overall` view as the drawn node sees it: positive into the node, negative out of it,
  in kWh over the run. A **link** is a flow between two drawn nodes; a **terminal flow** has a boundary, loss,
  storage or unaccounted node at its other end.
- **Trunk**: the polygon of a drawn node: a band of width Σ|drawn inputs| (it tapers to Σ|drawn outputs| at the
  head) along the node's **axis** (one of E, S, W, N: the compass direction the trunk's main flow runs in), with
  the title inside. Its **tail** is the upstream end, its **head** the downstream end, its two **lateral sides**
  the others. A flow attaches to one **side** (tail, head, or a lateral side named by compass direction).
- **Branch**: a terminal flow or a link's stub on a lateral side: a short band perpendicular to the trunk, ending
  in an arrowhead (output) or starting with a notch (input).
- **Tree connection**: the link by which a drawn node hangs from its **parent**, the drawn node its largest input
  comes from. **Cross-link**: every other link. A **tree** is a parent with all its descendants; a node without a
  parent is a **root**.
- **Main chain**: the row of drawn nodes from the heat chain's meter to the building (§6.2), laid on y = 0.
- **Connector**: the band that draws a cross-link, an orthogonal polyline from the source's stub to the target's
  stub with rounded corners (§6.7).
- **Canvas unit (u)**: the layout's length unit; a band's width in u is its drawn kWh divided by `kwh_per_unit`
  (§6.5). Pixels follow from the figure width (26 in at 110 dpi, as the prototype: 2,860 px).
- **Overlap**: two label boxes intersecting, a label box intersecting a band it is not drawn inside on purpose, a
  connector band (without its ends) intersecting another band, or a label clipped by the canvas (the four findings
  of the prototype's `report_overlaps`).
- **Metric**: a derived value of a drawn node or band (§7.1), with its formula. **Range**: an entry of the
  plausibility table (§7.3) attached to a metric by role. **Warning**: a metric outside its range (plausibility),
  or one of the error conditions of §8.1.
- **Pin**: one entry of the pin file (§6.10) that replaces one choice of the layout engine.
- **Flow-weighted mean**: Σ ṁ·T / Σ ṁ over the steps with ṁ > 0, ṁ the loop's mass flow in kg/s, T a temperature
  in °C (c is constant along a loop and cancels).

## 4. Inputs and outputs

### 4.1 What the renderer reads

One data class, `SankeyInputs`, built either from a result directory (the CLI of §4.4) or in memory at the end of
the post-processing; both paths carry the same four things:

| Input | File in the result directory | Used for |
|---|---|---|
| the `overall` view | `energy_sankeys/energy_sankey.json` (`EnergyBalanceReport.sankeys()`) | nodes, flows, carriers, kinds; every number on a band |
| the balance report | `balance_report.json`, schema 7 (§5) | component classes, verdicts, residuals, the loop statistics, the device and storage statistics |
| the KPIs | `all_kpis.json` (`WRITE_KPIS_TO_JSON`; in memory the KPI table `COMPUTE_KPIS` builds) | grid import and export, self-consumption and self-sufficiency rates, unmet hot-water demand, heat pump starts, comfort figures, floor area, TABULA reference need |
| the realized configuration | `realized.energy_system.yaml` (`AnnotatedEmitter.RECORD_FILENAME`, written by `hisim/energy_system/executor.py`) | kWp (`PVSystem.config.power_in_watt`), battery capacity, TABULA code, household profile name, emitter type. Read only through the statistic tags of §5.2 where those exist; the renderer itself reads a configuration field in exactly two places, the PV peak power and the household's profile name, both listed in `SankeyStyle.CONFIG_FIELDS` |

A run started from a Python setup has no realized record; the fields it supplies are then absent, and the values
that need them (kWh/kWp, the profile name) are left out, never estimated.

### 4.2 What the renderer writes

Into `energy_sankeys/` of the result directory, beside the plotly files:

| File | What |
|---|---|
| `overall.png` | the figure, 2,860 px wide, white background |
| `overall.pdf`, `overall.svg` | the same figure as vector graphics (the SVG keeps text as text) |
| `overall.layout.json` | the computed layout: every node's axis, anchor and trunk length, every flow's side, order and stub length, every connector's route points, every label's position, the scale, the canvas, the pins applied. The determinism test compares this file (§9.3), and a person starts a pin file from it |
| `overall.layout_error.json` | only when the layout engine fails (§6.12): the faults, as sentences, and the partial layout |

Only the `overall` view is drawn in v1. The per-carrier views stay plotly HTML (Q1); a per-carrier matplotlib view
is a filter of the same layout and is left for a later bead.

### 4.3 The post-processing option and the plotly HTML

`PostProcessingOptions.PLOT_SANKEY` (value 3) exists, is listed in `hisim/energy_system/parameters_format.py`,
and does nothing today (its only other mention is a comment in `hisim/simulationparameters.py`). Recommendation
(Q2): it gets this meaning. `PLOT_SANKEY` requires `COMPUTE_KPIS` and implies `EXPORT_ENERGY_BALANCE` (the figure
is drawn from the same report and views that option writes, and the files are what the CLI redraws from); a
parameter set with `PLOT_SANKEY` and without `COMPUTE_KPIS` is refused when the parameters are read, with the
sentence that names both options. The figure is drawn at the end of `PostProcessor.export_sankeys`
(`hisim/postprocessing/postprocessing_main.py`), after the balance files are written, and also in the failure path
of `judge_the_results`, where the balance report is written before an `EnergyBalanceError` propagates: a run that
fails its balance gets its picture with the residual arrows (D11) when the option is set.

The plotly HTML stays as it is in v1: `EXPORT_ENERGY_BALANCE` keeps writing the per-view HTML, interactive hover
text included, and nothing of `SankeyDrawing` changes. The matplotlib figure is the picture of the run; the PDF
report shows it (`INCLUDE_IMAGES_IN_PDF_REPORT`, bead hisim-4hed, PR 7). Whether the plotly `overall.html` is
retired later is Q1.

### 4.4 The redraw command

`hisim energy-system sankey <result directory> [--pins FILE] [--out DIRECTORY]` draws the figure from a finished
result directory without simulating again: it builds `SankeyInputs.from_result_directory`, applies the pin file
when given (else the one §6.10 finds), writes the files of §4.2 into `--out` (default: the result directory's
`energy_sankeys/`) and exits 0 on 0 overlaps, 4 when the layout engine reports faults (the files of §4.2 are still
written where it could draw), 2 when the directory lacks an input. A render takes a few seconds, a run 20 to 45 s,
so layout work and pin files are done with this command. It is a developer tool in the sense of
`.semgrep/file_writes.yml`: it writes into the directory it is pointed at, and its module joins the allowlist when
it does not live under `hisim/postprocessing/` (the renderer does: `hisim/postprocessing/energy_balance/figure/`,
§6.13).

## 5. The run statistics in the balance report (D8)

`EnergyBalanceReport` (`hisim/postprocessing/energy_balance/export.py`) has the result table and every loop's
three columns (`LoopColumns`, `ports.py`); the statistics are computed there, once per run, and written under a
new top-level key `statistics` of `balance_report.json`. Two schema steps: 6 adds `statistics.loops`, 7 adds
`statistics.states` and `statistics.site`. Both are additive; every reader of schema 5 (RenoVisor's `result.py`
reads the verdict) keeps working. `REPORT_SCHEMA` moves accordingly, and `tests/test_energy_balance.py` pins the
new keys on a hand-built table.

### 5.1 Loops (schema 6)

One entry per fluid loop the balance resolved (`DeclaredPorts.add_loops`), keyed by the loop's label (`loop
<component>.<mass flow output>`, as the ports list it):

```json
"statistics": {
  "loops": {
    "loop HeatDistributionSystem.WaterMassFlowHDS": {
      "supplier": "SimpleHotWaterStorage", "receiver": "HeatDistributionSystem", "carrier": "space_heating_heat",
      "columns": {"mass_flow": "...", "supply_temperature": "...", "return_temperature": "..."},
      "steps_with_flow": 22305, "hours_with_flow": 5576.25,
      "supply_mean_flow_weighted_in_celsius": 32.33, "return_mean_flow_weighted_in_celsius": 30.47,
      "supply_p99_flow_weighted_in_celsius": 43.07, "supply_mean_time_average_in_celsius": 32.40,
      "design_supply_limit_in_celsius": 35.0, "design_limit_source": "EmitterDesign.for_type(FLOORHEATING)",
      "hours_above_design_supply": 1257.0
    }
  }
}
```

Definitions, each a static function of `LoopStatistics` in a new module `hisim/postprocessing/energy_balance/statistics.py`,
each with direct tests on arrays of a few steps:

- `steps_with_flow`: the steps with ṁ > `FLOW_THRESHOLD_IN_KG_PER_SECOND` (1e-9); `hours_with_flow` =
  steps × seconds per step / 3600.
- `supply_mean_flow_weighted_in_celsius`, `return_mean_flow_weighted_in_celsius`: Σ ṁ·T / Σ ṁ over the steps with
  flow. A loop without a step with flow has `null` in every temperature field and 0 steps.
- `supply_p99_flow_weighted_in_celsius`: the 99th percentile of the supply temperature over the steps with flow,
  each step weighted by its ṁ: sort the supply values, take the cumulative weight divided by the total weight,
  interpolate the temperature at 0.99 (`numpy.interp`, as the prototype's `weighted_percentile`).
- `supply_mean_time_average_in_celsius`: the plain mean of the supply column over all steps, kept so a reader sees
  why the KPI "Mean flow temperature" differs.
- `design_supply_limit_in_celsius` and `hours_above_design_supply`: present when the receiving end declares a
  design supply temperature; the hours with flow in which the supply exceeds it. The heat distribution system
  declares it: `FluidLoop` (`hisim/energy_port.py`) gains an optional `design_supply_temperature_in_celsius`,
  which `HeatDistribution` sets from `EmitterDesign.for_type(config.heating_system).maximal_supply_temperature_in_celsius`
  (`hisim/components/heating_curve.py`); `design_limit_source` names the emitter type. No other loop has a limit in
  v1 (a tank coil has no design supply).

### 5.2 Device and storage states (schema 7): the statistic tag

The renderer must not know which output of which class is "the tank temperature". A component says it about
itself, once, where it declares the output: `Component.add_output(..., statistic=StatisticTag(...))`, beside the
existing `energy_port=` argument. `hisim/loadtypes.py` gains the enum `StatisticRole`, `hisim/energy_port.py` the
frozen data class `StatisticTag(role, rated_in_watt=None, capacity_in_kilowatt_hour=None)`:

| `StatisticRole` | Declared on | Statistics written |
|---|---|---|
| `THERMAL_OUTPUT_POWER` | a heat generator's total thermal output in W, with `rated_in_watt` (the heat pump's `ThermalOutputPowerTotalHeatpump` with `set_thermal_output_power_in_watt`; the boilers', the substation's and the electric heater's output with their rated power) | `runtime_hours` (steps with P > 1 W), `full_load_hours` (Σ P·Δt / rated), `peak_in_watt`, `peak_on_coldest_day_in_watt`, `coldest_day` (date), `coldest_day_mean_outdoor_in_celsius` (needs `OUTDOOR_AIR_TEMPERATURE`) |
| `ELECTRIC_OUTPUT_POWER` | a generator's AC electricity in W with `rated_in_watt` (the PV's `ElectricityOutput` with `power_in_watt`) | `energy_in_kilowatt_hour`, `specific_yield_in_kilowatt_hour_per_kilowatt` (energy / rated in kW), `peak_in_watt` |
| `START_COUNTER` | a cumulative start counter (`CounterOnOff` of the heat pump) | `starts` (the last value minus the first) |
| `STORAGE_TEMPERATURE` | a storage's medium temperature in °C (`SimpleDHWStorage.WaterTemperatureAtEndOfStepInCelsius`, `SimpleHotWaterStorage.WaterMeanTemperatureInStorage`) | `min_in_celsius`, `max_in_celsius`, `hours_below_40_celsius`, `hours_at_or_above_60_celsius` (the two thresholds are `StorageTemperatureThresholds` constants of the statistics module, named for the taps and DVGW W 551; the plausibility table owns their sources) |
| `STATE_OF_CHARGE` | a battery's state of charge in [0, 1] with `capacity_in_kilowatt_hour` (`Battery.StateOfCharge`, `CarBattery`) | `mean_state_of_charge`, `equivalent_full_cycles` (Σ positive SOC increments × capacity / capacity = Σ positive increments; equal to the charged energy of the cells over the capacity) |
| `INDOOR_AIR_TEMPERATURE` | the building's `TemperatureIndoorAir` | `min_in_celsius`, `max_in_celsius` (the comfort K·h figures stay KPIs) |
| `OUTDOOR_AIR_TEMPERATURE` | `Weather.TemperatureOutside` | `coldest_day`, `coldest_day_mean_in_celsius` (daily means over the run's calendar days) |
| `OCCUPANTS` | `UtspLpgConnector.NumberOfResidents` | `mean_occupants` (the time mean) |

Written as `statistics.states[<component>][<output>] = {"role": ..., "rated_in_watt": ..., ...}` and
`statistics.site = {"coldest_day": ..., "coldest_day_mean_outdoor_in_celsius": ...}`. A tag on an output whose
unit does not fit its role (a temperature role on a W output) is refused by `add_output` with a sentence; a run
without a tagged output of a role writes nothing for it. Every statistic is a static function with direct tests.
A tag changes no result and no KPI; the semgrep gate is untouched (the writer is `export.py`).

## 6. The layout engine

Module `hisim/postprocessing/energy_balance/figure/` (§6.13). Input: `SankeyInputs` (§4.1) and the optional pins
(§6.10); output: a `Layout` (what `overall.layout.json` serialises) and the list of faults. Every step is a static
function or a small class with direct tests on hand-built graphs; the engine never reads a class name of a
component for a layout decision (roles come from kinds, carriers and the shape of the links), and it never reads
a file.

### 6.1 The drawn graph

From the `overall` view:

1. **Drawn nodes** are the nodes of kind `component`, `meter` and `undeclared`. Every other node is folded into
   the flows of the drawn node it touches: a loss edge `component → loss node` becomes a terminal output of the
   component named by the loss node's port (the `loss node → outdoors` edge is dropped); a storage edge becomes a
   terminal flow in the direction the view gives (into the component when the run's net released energy, out when
   it stored); a boundary edge a terminal flow to or from the boundary node; a residual edge a terminal flow marked
   `residual` (§6.3).
2. **The solar cut (D3)**: a drawn node with a `SOLAR` input and a production of `ELECTRICITY` has its `SOLAR`
   input and its `SOLAR` losses replaced by one unlabelled terminal input, flagged `cut`, of their difference, so
   the trunk is drawn with a flat tail. A node with a `SOLAR` input and no electricity production keeps them.
3. **Empty flows**: a flow below `SankeyStyle.OMIT_BELOW_KWH` (0.001 kWh, as `SankeyDrawing.EMPTY_FLOW_IN_KILOWATT_HOUR`)
   is left out; a link left out at one end is left out at the other.
4. **Balance of a drawn node**: Σ inputs − Σ outputs over its flows (signed kWh). A node whose imbalance exceeds
   `RESIDUAL_KWH` (0.5 kWh, as the prototype) and that has no residual flow gets a synthetic residual flow (§6.3).
   This is how an undeclared node, whose edges are the declared neighbours' bookings, is closed.

The result is a list of `DrawnNode(name, kind, class_name, flows: list[Flow])`, each `Flow(label, value_kwh,
carrier, kind, other_end, is_terminal, flags)`, in the order the view lists them. Every ordering below is by an
explicit key; set iteration is never relied on (string hashes vary between processes, and §9.3 demands the same
layout in every process).

### 6.2 Roles and the main chain

A **role** per drawn node, from kinds and carriers only, decided in this order (the first that matches):

| Role | Criterion |
|---|---|
| `ELECTRICITY_METER` | kind `meter`, its boundary flows carry `ELECTRICITY` |
| `FUEL_METER` | kind `meter`, any other carrier (gas, oil, pellets, wood chips, hydrogen, district heat) |
| `PV` | consumes `SOLAR`, produces `ELECTRICITY` to a drawn node |
| `SOLAR_COLLECTOR` | consumes `SOLAR`, produces a heat carrier |
| `VEHICLE_STORAGE` | produces `VEHICLE_ELECTRICITY` |
| `VEHICLE` | consumes `VEHICLE_ELECTRICITY` |
| `ELECTRIC_STORAGE` | has a storage flow, consumes and produces `ELECTRICITY` to the same drawn node |
| `HOUSEHOLD` | consumes `ELECTRICITY`, produces `ROOM_HEAT` to a drawn node |
| `COOLER` | consumes `COOLING` from a drawn node, produces `AMBIENT_HEAT` to a boundary |
| `HOT_WATER_TANK` | has a storage flow of `DOMESTIC_HOT_WATER_HEAT`, or produces `DOMESTIC_HOT_WATER_HEAT` to a boundary |
| `BUFFER` | has a storage flow of `SPACE_HEATING_HEAT`, consumes and produces it to drawn nodes |
| `BUILDING` | consumes `SPACE_HEATING_HEAT` or `ROOM_HEAT` from a drawn node and produces no heat carrier to a drawn node except `COOLING` |
| `DISTRIBUTION` | consumes a heat carrier from a drawn node and produces the same carrier to a drawn node, no storage flow (the heat distribution system, declared or undeclared) |
| `GENERATOR` | produces a heat carrier (`SPACE_HEATING_HEAT`, `DOMESTIC_HOT_WATER_HEAT`, `ROOM_HEAT`) to a drawn node and consumes a carrier that is no heat carrier |
| `CONVERTER` | everything else (electrolyzer, fuel cell, CHP, transformer, hydrogen storage) |

A node of kind `undeclared` gets its role from the same criteria on the flows its neighbours book, with
`DISTRIBUTION` as the common case; the fallback is `CONVERTER`.

**Main chain.** Start at the `BUILDING` with the largest heat input (several buildings: the largest; none: see
below). Walk upstream: from the current node, take the largest input link whose carrier is `SPACE_HEATING_HEAT`,
then `ROOM_HEAT`, then any heat carrier, then any carrier; stop at a meter or at a node without an input link. The
nodes visited, reversed, are the chain: `GasMeter → GenericBoiler → SimpleHotWaterStorage → HeatDistributionSystem
→ Building` in the gas twin, `ElectricityMeter → UTSPConnector → Building` in the air-conditioner twin,
`ElectricityMeter → ElectricHeating → Building` in the electric-heating twin. The chain's meter is the **chain
root**; when the chain ends at a node without an input link (a collector feeding the building directly), that node
is the root. Without a `BUILDING` (the electrolyzer and the `dynamic_components` systems), the chain starts at the
meter with the largest throughput and follows the largest output link until a node has none.

### 6.3 Residuals (D11)

A residual flow is drawn as a terminal branch of its node in the **unaccounted style**: grey fill with a hatch,
dashed grey outline, the label in a dashed grey box: "unaccounted loss · 20 kWh" for an output gap (Σ in > Σ out),
"unaccounted gain · 20 kWh" for an input gap. When the node is undeclared, or when the balance report's verdict for
the node is `does_not_close`, the label and the box are red and the label says why in three to five words
("undeclared: no energy ports", "balance does not close"); either is an error entry of the warnings box (§8.1).
The residual flow takes the side the role table gives for losses (§6.4). A node whose residual is within
`RESIDUAL_KWH` draws none.

### 6.4 Orientation rules by role

The table decides, per role, the node's axis, where its tree connection comes in, the side of every other flow
by carrier and kind, and the slot of each child. "Head" and "tail" are relative to the axis; the lateral sides are
compass directions of the page. A flow whose rule names a side already taken by a wider flow of the opposite
direction keeps the side (a side holds any number of flows in order); the order along a side is: tree connection
first, then links by value descending, then terminal flows by value descending, residual last.

| Role | Axis | Tree connection in at | Other inputs | Outputs | Children |
|---|---|---|---|---|---|
| `ELECTRICITY_METER` | E | tail (W), from the `PV` when there is one | grid import: N; storage discharge (from `ELECTRIC_STORAGE`): S; other component production (collector pump? no — a CHP, a fuel cell): S | grid export: N when the meter is the chain root, else head (E); to `GENERATOR`, `CONVERTER` on the chain: head (E), a second flow to the same node stacked below the first; to `HOUSEHOLD`, `ELECTRIC_STORAGE`, `VEHICLE_STORAGE`, `COOLER`, `SOLAR_COLLECTOR` (pump electricity): S | `GENERATOR` on the chain: continuation E; `HOUSEHOLD`: below, rightmost; `ELECTRIC_STORAGE`: below, leftmost; `VEHICLE_STORAGE`: below, between them |
| `FUEL_METER` | E | tail (W): the supply boundary flow | – | to its generator: head (E), a second flow to the same generator stacked below | `GENERATOR`: continuation E |
| `PV` | E | – (root) | the cut: tail (W), flat | to the meter: head (E) | `ELECTRICITY_METER`: continuation E |
| `GENERATOR` | E | tail (W); a second input from the same source: tail, stacked below | `AMBIENT_HEAT`, `SOLAR`: S | `SPACE_HEATING_HEAT` to the chain's next node: head (E); `DOMESTIC_HOT_WATER_HEAT`: N; losses: S | chain successor: continuation E; `HOT_WATER_TANK`: above |
| `BUFFER`, `DISTRIBUTION` | E | tail (W) | a second heat source (collector): S | chain successor: head (E); losses: N; storage change: S; residual: S | chain successor: continuation E |
| `BUILDING` | E | tail (W) | `SOLAR`: N; `ROOM_HEAT` from `HOUSEHOLD`: S; storage change: S | opaque transmission and gains lost: head (E); ventilation: N; windows and doors: S; `COOLING` to a `COOLER`: head (E) | `COOLER`: continuation E |
| `HOT_WATER_TANK` | E | S (from the generator below) | a second heat source (collector): tail (W); storage change (released): N | taps: head (E); losses, storage change (stored): N | – |
| `SOLAR_COLLECTOR` | E | – (root) | sun: tail (W); pump electricity: S | heat: head (E); losses: N | – |
| `HOUSEHOLD` | S | tail (N) | body heat: W | heat to the building (two flows): E; appliance electricity not released: head (S) | – |
| `ELECTRIC_STORAGE` | S | tail (N) | storage change (released): W | discharge (back to the meter): W; inverter loss: E; cell loss: head (S); storage change (stored): W | – |
| `VEHICLE_STORAGE` | S | tail (N) | – | to the vehicle: head (S); losses: E | `VEHICLE`: continuation S |
| `VEHICLE` | E | tail (W), after a corner from the storage's head | – | driving: head (E); losses: S | – |
| `COOLER` | E | tail (W) | electricity: N | to the ambient sink: head (E) | – |
| `CONVERTER` | E | tail (W) | boundary inputs: N | links: head (E); losses: S; storage change: S | each child: continuation E for the largest, below for the others |

Storage changes whose sign flipped between runs (the tank stored heat in the gas twin and released it in the
heat-pump twin) keep their side; only the arrow's direction and the display name change (`{in: "Stored heat
released", out: "Heat stored"}` in the style table).

### 6.5 Scale, sizes and the minimum band

- **Scale.** One `kwh_per_unit` per figure: the smallest value of the series 1, 2, 5 × 10ⁿ for which the widest
  drawn band is at most `WIDEST_BAND_IN_UNITS` (7 u). Every full-year building-sizer system lands at 4,000 kWh/u
  (the PV's 26.3 MWh is the widest band at 6.6 u), as the prototype's family scale; a week-long test run lands at
  100 kWh/u. A pin may fix it.
- **Band width** of a flow: |drawn kWh| / `kwh_per_unit`. **Trunk width**: Σ drawn inputs at the tail, tapering by
  each lateral flow to Σ drawn outputs at the head.
- **Minimum band (D10).** `MIN_BAND_PX` = 2. The layout is computed twice: the first pass with the raw values gives
  the canvas span and so the pixels per unit (2,860 px × 0.98 / span in u); every band below `MIN_BAND_PX /
  px_per_unit × kwh_per_unit` kWh is then drawn at that minimum, flagged `inflated`, and the second pass uses the
  drawn values. If the second pass's span differs from the first by more than 1 % a third pass runs; a fourth is
  never needed, and a difference after the third is a fault. An inflated band's label ends in " †" and the caption
  says "† drawn at the minimum width of 2 px, not to scale".
- **Trunk length** of a node: the largest of (a) the title's width plus `TITLE_PAD` (0.6 u) on both sides, (b) the
  note block's width (§6.8) plus the pads, (c) for each lateral side, `GAP` (0.56 u) × (n + 1) + Σ widths of its n
  flows, (d) `MIN_TRUNK` (3 u). Text widths come from `matplotlib.textpath.TextPath` at the style's font size,
  converted to u through the pixels per unit, so they need no canvas and are deterministic given the font (§9.3).
- **Stub length** of a branch: `STUB` (1.3 u) for a terminal flow, 0.48 u for the stub of a link (the connector
  continues it), grown by the label pass (§6.8). **Connection length** of a tree connection: the width of the label
  drawn inside it plus 2 × `TITLE_PAD`, at least `MIN_CONNECTION` (2 u); a label does not fit inside a band
  narrower than 1.1 line heights and goes above it instead (§6.8).
- **Arrowheads and notches**: head length 0.5 × band width + `SHOULDER` (0.1 u), head half-width = band half-width
  + `SHOULDER`; a notch is the mirror image cut into the receiving edge. Where a connector continues a stub, the
  stub is cut flat (no head, no notch), and where a tree connection runs beside a connector into the same side of
  the child (the generator's two inputs from one meter), the connection's head is cut flat too, so the two bands
  enter side by side with one seam (the prototype's `flatten_parallel_connections`). These constants are the
  prototype's `sankey:` style, in u.

### 6.6 Placing the trees

1. **The main chain** goes on y = 0, the chain root's tail at x = 0, each node's tail at the previous node's head
   plus the connection length, axis E. The `PV`, when it is the root's parent, goes left of the root on the same
   row.
2. **The spanning tree.** Every drawn node not on the chain hangs from the drawn node its largest input link
   comes from (ties: carrier order of §6.2's chain walk, then name). Roots are the nodes without an input link:
   the chain root's tree, the `PV` (already placed), a `SOLAR_COLLECTOR`, a second meter.
3. **Children of a placed node**, in the slots the role table names: a *continuation* child sits on the parent's
   axis after the connection; a *below* child has its tail facing the parent's S side, its anchor `DROP` (2.5 u)
   below the parent's lower edge, at the x of its stub on the parent; an *above* child the same upwards. Several
   children in one slot are ordered as the role table says (leftmost, rightmost, between) and by value descending
   otherwise, and are spread along the parent's axis so their bounding boxes (inflated by `NODE_MARGIN`, 1 u)
   do not intersect: each is shifted outward from the previous by the overlap found, in order. The children's
   subtrees are placed recursively before their siblings are spread, so a subtree moves as a whole.
4. **Further trees** go into the quadrant their root's role names: a `SOLAR_COLLECTOR` above and left of the node
   it feeds (the tank), so its head faces the tank's tail with a Z route; an `ELECTRICITY_METER` that is not the
   chain root (fuel systems) below the chain, its tail at the chain root's x; any other root below the chain, right
   of the trees already placed there. The tree is first put at the nearest position of its quadrant and then slid
   away from the chain in steps of `SLIDE` (0.5 u) until its bounding box, inflated by `TREE_MARGIN` (1.5 u), is
   clear of every placed tree and every connector it needs (§6.7) routes. `MAX_SLIDES` (200) steps without
   success is a fault.
5. **Pins** (§6.10) fix a root's anchor or a node's axis before this step runs; the rest is computed around them.

### 6.7 Routing the cross-links (D4)

For every cross-link, in order of band width descending:

1. The source's stub leaves the side the role table gives (an output side), the target's stub enters its side (an
   input side); both stubs are cut flat (§6.5).
2. **Shape**: *straight* when the two stubs are collinear and face each other; *L* when they are perpendicular and
   the lines through them meet in front of both; *Z* when they are parallel, face opposite directions and are
   offset. Each shape is tested against the obstacle map; the first that is clear is taken.
3. **Grid search** otherwise: A* over a grid of `GRID` (0.25 u) on the canvas inflated by 3 u, orthogonal moves,
   cost = length + `BEND_COST` (3 u) per turn, a cell blocked when the band (its full width plus `CLEARANCE`,
   0.3 u) would intersect a trunk, a branch, a placed connector or a placed label. The route is the cell path
   simplified to its corners.
4. **Corners** are rounded with `CONNECTOR_RADIUS` (0.64 u), reduced to the half of the shortest adjacent segment
   when that is smaller, down to `MIN_RADIUS` (0.24 u); a route that leaves less is a fault, and the engine first
   retries after sliding the target's tree (§6.6, step 4) one more step.
5. **Parallel connectors** (two routes that share a corridor, such as the household's two heat flows to the
   building): ordered so they never cross, the band nearer the trunk turning first; their stubs on a shared side are
   stacked in that order.
6. **A route that fails** after `MAX_ROUTE_ROUNDS` (3) slides of the involved trees is a fault that names the link
   and both ends; the layout continues without that band, and the stubs are drawn with their labels (the
   prototype's stub style), so the picture is still readable while the fault is visible.

Connector polygons: the centre line is sampled at 16 points per corner; the outline is the centre line offset by
± half the width (the prototype's `rounded_centre_line` and `band_outline`); the fill is the source node's colour,
the two sides are drawn as thin dark lines.

### 6.8 Labels and the note block

Every flow has a label of two lines, name and value (`format_kwh`: MWh with one decimal from 10 MWh, whole kWh
from 10 kWh, one decimal below; thousands separators; no sign). A loop's band carries a third line with its
temperatures (§7.1). Every drawn node has a title (bold, inside the trunk) and a **note block** under the title: the
metric lines §7.1 gives its role, each one value with its unit and, when it has a warning, the marker ⚠n. The note
block is reserved before placement: its width counts in the trunk length (§6.5) and its box is an obstacle, so
the label pass never has to move it.

**Candidates** per label, tried in this order:

- terminal branch: beyond the arrow tip (inputs: beyond the notch), anchored away from the arrow (the prototype's
  rule); beside the stub on either side; the same with the name wrapped to two lines (`textwrap` at 18 characters);
- tree connection: inside the band (white, bold) when the band is at least 1.1 line heights wide; else above the
  band at its middle, below it, at its quarter points;
- connector: above, below, left or right of each segment's middle, from the longest segment to the shortest;
- node title and note block: fixed (no candidates); a title wider than the trunk by more than 2 × `TITLE_PAD` is
  wrapped to two lines before the trunk length is computed.

**Scoring** of a candidate: 1,000 per overlap with a placed label, a trunk, a branch or a connector band (a label
inside its own band is exempt from that band), 1,000 when it leaves the canvas, plus its distance from the first
candidate in u. **Order**: labels of the widest bands first, then the rest by width descending. **Repair**: after
one greedy pass, every label that still overlaps is re-placed over all its candidates with the others fixed, twice.
**Growth**: labels that still overlap grow the stub of their flow and the trunk of their node by `GROW` (0.5 u)
and the layout is recomputed from §6.5; `MAX_GROWTH_ROUNDS` (6) rounds without 0 overlaps is a fault that lists
the pairs. The overlap checker is the prototype's `report_overlaps`, made a class `OverlapCheck` with the four
findings of §3 as typed records.

### 6.9 Canvas, panel and caption

The canvas is the bounding box of every polygon and label plus `CANVAS_MARGIN` (1 u). The figure is 26 in wide at
110 dpi; its height follows from the canvas's aspect ratio plus a title line (0.55 in), the panel (§8.3, its height
from its rows) and the caption (0.7 in). The caption reads: the data source ("yearly energy from the run's energy
balance, overall view"), the scale ("band width: 1 u = 4 MWh"), the loop-temperature definition in one line, the
marker legend, the PV note of D3, the balance verdict, the † note when a band is inflated, and a second line with
the run's key results from the KPIs (grid import and export, self-consumption and self-sufficiency rates, heat
delivered to the rooms or heat removed by cooling).

### 6.10 The pin file

An optional YAML file, `energy_systems/<stem>.sankey_pins.yaml` beside the energy-system file (found through the
realized record's source path when the run came from a YAML, else none; the CLI takes `--pins`), schema
`hisim.sankey_pins/1`:

```yaml
schema: hisim.sankey_pins/1
canvas: {kwh_per_unit: 4000}                       # fixes the scale
nodes:
  SolarThermalSystem: {at: [3.5, 10.3]}            # a root's anchor in u; its whole tree moves with it
  Battery: {axis: S}                               # overrides the role's axis
  ElectricityMeter: {trunk_length: 9.6}            # overrides the computed length
flows:
  Building.GainsLostThroughWindowsInWatt: {side: E, stub: 0.48, label: beside_below}
  SolarThermalSystem.ElectricityConsumptionOutput: {turns: ["+2.5", -9.8, 4.6]}   # a connector's corners
labels:
  Battery.StoredEnergyIncreaseInWattHour: {shift: [0.3, -0.5], lines: 2}
```

A pin replaces the engine's choice for that one element in the step where the choice is made; everything else is
computed around it, and the validation of §6.12 applies unchanged (a pin that causes an overlap is a fault like
any other). A pin naming a node or flow the run does not have is a fault. The applied pins are listed in
`overall.layout.json`. The pin file is the escape hatch for the hard cases of §6.14; no energy-system file in the
repository ships one in v1 (a showcase figure may add one later, under the acceptance test).

### 6.11 Rendering

The renderer takes a `Layout` and draws it with matplotlib's `Agg` backend into one `Figure`: trunk polygons
(`PathPatch`, the node's colour, dark outline 0.6 pt), branch and connector polygons, labels (`Text`), the note
blocks, the markers (⚠n in bold, red `#c62828` for errors, orange `#d35400` for plausibility), the residual style
(§6.3), the unmet stub (§8.1), the panel and the caption (§8.3), then saves PNG, PDF and SVG. Fonts: DejaVu Sans,
matplotlib's bundled default, pinned in an `rc_context`, so text widths are the same on every machine. Colours and
titles per component class and display names per output come from one style table,
`hisim/postprocessing/energy_balance/figure/sankey_style.yaml` (the prototype's `_style.yaml`: `kinds`,
`port_names`); a class without an entry takes its role's colour and its instance name; two instances of one class
get the instance name appended to the title. Class-keyed colours and names are presentation, not a layout
dependence; the layout engine reads roles only.

### 6.12 Validation: what fails loudly

The engine collects faults and the renderer writes `overall.layout_error.json` (§4.2) when the list is not empty;
the acceptance tests (§9) fail on any fault. Faults:

- a drawn node without a role (cannot happen by construction; `CONVERTER` is the fallback) or a chain that finds
  no root;
- a flow of the view that no node placed, or placed twice;
- a node whose flows do not balance within `RESIDUAL_KWH` and that has no residual flow (cannot happen after
  §6.1 step 4; checked anyway);
- a tree that found no clear position (`MAX_SLIDES`), a connector that found no route (`MAX_ROUTE_ROUNDS`), a
  corner without room for `MIN_RADIUS`;
- an overlap after `MAX_GROWTH_ROUNDS`, listed pair by pair;
- the minimum-band passes not converging (§6.5);
- a pin naming an unknown element or an unknown key;
- a metric whose inputs the run lacks is no fault: the line is left out (§7.1).

Whether a fault fails the run is Q3; the recommendation is that the run finishes and the fault is a logged error
plus the file, and that the tests fail.

### 6.13 Code layout

```
hisim/postprocessing/energy_balance/
  statistics.py            LoopStatistics, StateStatistics (§5), each statistic a tested static function
  figure/
    __init__.py            draw_overall(inputs, pins) -> Layout; the entry the post-processing and the CLI call
    inputs.py              SankeyInputs.from_result_directory / from_run
    graph.py               the drawn graph (§6.1), roles and the main chain (§6.2), residuals (§6.3)
    layout.py              Layout, the orientation table (§6.4), sizes (§6.5), tree placement (§6.6)
    routing.py             shapes, the grid search, corners (§6.7)
    labels.py              candidates, scoring, OverlapCheck (§6.8)
    metrics.py             the metric formulas (§7.1) and the plausibility evaluation (§7.3)
    warnings.py            the warning entries, their order and numbering (§8)
    render.py              matplotlib drawing (§6.11), the panel and the caption (§8.3)
    pins.py                the pin file (§6.10)
    sankey_style.yaml      colours, titles, display names, the layout constants that are style, the panel rows
  plausibility_ranges.yaml the range table (§7.3)
```

Constants live on the classes they belong to (`system_docs/coding_style.md` §4): the layout constants of §6.5 to
§6.8 on `LayoutConstants`, a frozen data class the style file may override field by field. `hisim/cli.py` gains
the `energy-system sankey` command (§4.4). No module outside `figure/` imports matplotlib for this feature.

### 6.14 Hard cases and what v1 does

| Case | v1 |
|---|---|
| several generators in parallel (hybrid heat pump plus boiler feeding one buffer) | the generator with the larger heat output into the buffer is on the chain; the other hangs from its own meter or, without one, below the chain as a further tree, its heat routed into the buffer's S side by the router. Drawn by the generic rules, not exercised by any energy-system file today; a pin file is the escape hatch |
| very unequal sizes (a 16 kWh pump hairline beside a 21 MWh export band) | the minimum band (D10) and the label candidates; the prototype's robustness test broke labels, not routes, at +50 %, which the label pass now re-solves per run |
| storages whose change flips sign between runs | the side stays, the arrow direction and the display name flip (§6.4) |
| systems without PV (`simple_air_conditioner_household_building_sizer`) | the meter is a root with its grid import at the tail (W); the role table's "from the PV when there is one" falls through |
| cooling-only systems | the chain runs meter → household → building; the cooler is the building's continuation child; the ambient sink is its head |
| a cooling flow of a heat pump into a buffer (hisim-energy-sankeys-9uoo.17) | a negative loop heat turns the edge round: it is drawn as a `COOLING` input of the generator from the buffer, side S; untested until that bead lands |
| systems without a building (`electrolyzer_with_renewables`, `dynamic_components`) | the fallback chain of §6.2 from the largest meter; every node a `CONVERTER` or a meter; acceptance requires 0 faults, not a reviewed picture |
| systems with nothing declared (`simple_system_setup_one`, `_two`: verdict `nothing_declared`) | no figure; `export_sankeys` logs that there is nothing to draw; not a fault |
| several buildings, multi-family or district systems | out of scope for v1: the chain is the largest building's, the other buildings hang as children of their parents by the generic rules; no acceptance test; the pin file is the escape hatch. A rule set for siblings on one row is a later bead |
| the per-carrier views as matplotlib figures | not in v1 (Q1) |

## 7. Metrics and plausibility

### 7.1 The metric definitions

Every metric is a static function of `Metrics` with direct tests; its inputs are the drawn flows of its node (in
kWh, by port name), the statistics of §5 and the KPIs. A metric whose input the run lacks (no kWp without a
realized record; no occupants without the tag) is left out, never estimated. Notation: P(x) is the yearly kWh of
the node's output port x, C(x) of its input x; "heat out" is the sum of the node's heat productions to drawn nodes.

| Metric (unit) | Role | Formula | Shown |
|---|---|---|---|
| seasonal performance factor (–) | `GENERATOR` consuming `ELECTRICITY` and `AMBIENT_HEAT` | heat out / electricity in | note block: "SPF 4.14 · ambient 76 %" |
| ambient share (%) | the same | ambient heat in / heat out × 100 | with the SPF |
| generator efficiency (%) | `GENERATOR` consuming a fuel or `DISTRICT_HEAT`, or `ELECTRICITY` without `AMBIENT_HEAT` | heat out / fuel (or district heat, or electricity) in × 100 | note block; left out when it is 100.0 % by construction (the electric heater) |
| collector efficiency (%) | `SOLAR_COLLECTOR` | heat out / solar in × 100 | note block |
| battery round trip, AC to AC (%) | `ELECTRIC_STORAGE` | discharge to the meter / charge from the meter × 100 (the run's net storage change is ignored; it is below 0.1 % of the charge over a year) | note block |
| equivalent full cycles (1/a) | `ELECTRIC_STORAGE`, `VEHICLE_STORAGE` | `statistics.states[..].equivalent_full_cycles`, scaled to a year by 8,760 h / run hours | note block |
| specific yield (kWh/kWp) | `PV` | `specific_yield_in_kilowatt_hour_per_kilowatt` of the `ELECTRIC_OUTPUT_POWER` tag, scaled to a year | note block, with the kWp |
| specific heat to the rooms (kWh/(m²a)) | `BUILDING` | heat received from the `DISTRIBUTION` or `GENERATOR` / KPI "Conditioned floor area", scaled to a year | note block |
| internal gains (Wh/(m²d)) | `HOUSEHOLD` | (appliance heat + body heat to the building) / floor area / run days × 1,000 | note block |
| hot water per person (kWh/(person·a)) | `HOT_WATER_TANK` | heat to the taps / `mean_occupants`, scaled to a year | note block |
| energy efficiency ratio (–) | `COOLER` | heat removed / electricity in | note block |
| substation efficiency (%) | `GENERATOR` consuming `DISTRICT_HEAT` | as generator efficiency | note block |
| loop temperatures (°C) | every link that is a loop | `supply_mean_flow_weighted` / `return_mean_flow_weighted` · "max" `supply_p99_flow_weighted`; a fourth line "n h above the d °C design supply" when the loop has a limit, red when n > 0 | on the band |
| tank hours (h) | `HOT_WATER_TANK` with a `STORAGE_TEMPERATURE` tag | `hours_below_40_celsius`, `hours_at_or_above_60_celsius` | note block: "64 h < 40 °C · 3,498 h ≥ 60 °C" |
| comfort (°C, K·h) | `BUILDING` | KPIs "Minimum building indoor air temperature reached" and "Degree-hours … more than 1.0 K below its heating set temperature" | note block: "min. air 18.3 °C · 1.3 K·h below" |
| unmet hot water (kWh) | `HOT_WATER_TANK` | KPI "Unmet DHW heat demand" | a red stub (§8.1) |

Scaling to a year uses the run's hours from the balance report (`timesteps × seconds_per_timestep / 3600`), so a
week's figure is labelled per year and reads like the full year's; the caption states the run's period.

### 7.2 Where a number comes from

Every value on the figure traces to one of the four inputs of §4.1; `overall.layout.json` records, per label, the
input and key it came from (`"source": "kpi:Unmet DHW heat demand"`, `"source": "statistics.loops[...]"`), so a
reviewer can check a number without the code.

### 7.3 The plausibility table (D9)

`hisim/postprocessing/energy_balance/plausibility_ranges.yaml`, schema `hisim.plausibility_ranges/1`, with a
`version` date that changes with every edit:

```yaml
schema: hisim.plausibility_ranges/1
version: 2026-11-08
ranges:
  - id: heat_pump_spf_air_source_existing_buildings
    metric: seasonal_performance_factor
    applies_to: {role: GENERATOR, consumes: [electricity, ambient_heat]}
    low: 2.6
    high: 4.9
    reference: 3.4
    unit: "-"
    basis: "the range of the 61 measured air/water systems"
    source:
      organisation: "Fraunhofer ISE"
      title: "WP-QS im Bestand, final report; press release #30"
      year: 2025
      where: "press release #30, 2025; https://www.ise.fraunhofer.de/de/presse-und-medien/presseinformationen/2025/..."
    checked: {by: "<name>", on: 2026-11-xx, against: "the press release and the final report's table of the measured SPFs"}
    estimate: false
  - id: hot_water_per_person
    metric: hot_water_per_person
    applies_to: {role: HOT_WATER_TANK}
    low: 500
    high: 1000
    unit: "kWh/(person a)"
    basis: "typical German per-person values; no exact source found"
    estimate: true
```

Rules, each enforced by a `base` test of the table:

- `applies_to` names a role and optionally the carriers the node consumes; never a class name. At most one range
  applies to one metric of one node; two that both apply are a test failure.
- An entry has either `checked` (who, on which date, against what) and `estimate: false`, or `estimate: true` and
  no `checked`. An estimate is drawn in the warnings box with the words "estimate, unsourced" after the range,
  whatever its `basis` and `source` say. An entry with neither, or with both, is refused.
- `low` < `high`; `reference`, when given, lies between them; `unit` matches the metric's unit in §7.1.
- A `source` has `organisation`, `title`, `year` and `where` (table, page or URL); an estimate may omit it.
- `metric` is one of the ids of §7.1; the test keeps the two lists equal.

The first version of the table ships with PR 3 and holds: the heat pump SPF (Fraunhofer ISE, to be checked), the
battery AC-to-AC round trip derived from HTW Berlin's Stromspeicher-Inspektion 2024 (the derivation of the trial:
AC2BAT × battery × BAT2AC for the worst and the best values with AC2BAT assumed in the BAT2AC range, Bild 21 p. 25
and Bild 24 pp. 26–27; marked `estimate: true` until a person checks the figures), the PV specific yield (PVGIS
5.2 for Aachen, E_y ± 2 SD; Q8: it is site-specific, so it applies only when the run's weather location is Aachen
and is otherwise left out), the building's specific heat (TABULA ±20 %, estimate), the internal gains (DIN V
18599-10, estimate until checked), the hot water per person (estimate), the battery cycles (estimate), the comfort
criterion (own criterion after EN 16798-1 category II, estimate), and the tank thresholds 40 °C (estimate) and
60 °C (DVGW W 551, to be checked). Only a person with the original turns an estimate into a checked range; an
agent never writes `checked`.

## 8. Warnings and the panel

### 8.1 Errors and plausibility warnings

Errors (red), in this order within a component:

1. the component is undeclared (it has a residual by construction);
2. the component's balance does not close (verdict `does_not_close` in the report);
3. demand not delivered: the KPI "Unmet DHW heat demand" > 0 for a `HOT_WATER_TANK`, drawn as a red stub arrow out
   of the tank's N side labelled "not delivered ⚠n" with its kWh (the prototype's `draw_unmet`), an obstacle and a
   label like any other; a future unmet space-heating KPI joins the same rule;
4. a loop ran above its design supply: `hours_above_design_supply` > 0.

Plausibility warnings (orange): a metric of §7.1 outside its applicable range of §7.3. A metric without a range
gets no marker. The heat pump's peak thermal power against its rating is shown in the panel only (Q7).

### 8.2 The warnings box (D6)

Bottom right, 62 % of the figure width, two columns at 8.5 pt, a light orange background with an orange frame,
headed "Warnings (n) — by component, errors (red) before plausibility warnings (orange)". Entries are numbered
1…n in the order: components in the diagram's order (the chain root's tree in depth-first order by placement x,
then the further trees by their anchor x), within a component errors first (§8.1's order) then plausibility
warnings by metric order of §7.1. Each entry: a bold heading "⚠n  <component title> · <value> (<range>)" in the
severity's colour, one or two sentences of context from a template per metric with the run's numbers filled in,
and an italic "Source: <organisation>, <title>, <year>, <where>" or "Source: estimate, unsourced; basis: <basis>".
The marker ⚠n is drawn after the value on the diagram (note block or band label). Entries are wrapped to the
column width and distributed over the two columns by lines; the box's height is computed from the wrapped text,
and the panel takes that height when it is larger than the data rows need. A run without warnings draws no box and
the data panel takes the full width.

### 8.3 The data panel (D7)

Left of the box (38 % of the width, two columns of sections), rows "name … value", one section per present role,
in this order with these rows:

| Section | Rows (left out when the input is absent) |
|---|---|
| per `GENERATOR` with a `THERMAL_OUTPUT_POWER` tag | runtime (h), starts, full-load hours, max. thermal power / rated (kW, and % of rated), max. on the coldest day (date, daily mean outdoor °C) |
| Grid and battery | peak grid import / export (KPIs "Max power from grid", "Max power to grid"), battery capacity, equivalent full cycles, mean state of charge |
| Storages and loops | buffer temperature range, hot-water tank range, unmet hot water, each loop's design supply where it has one |
| Building and comfort | conditioned floor area, TABULA reference need (KPI), lowest / highest indoor air temperature, K·h above 26 °C, K·h below the set point, household profile |
| Run | period and step length, balance verdict, number of components drawn, kWh/u |

The panel is data only: no ranges, no sources (D5, D6).

## 9. Verification

### 9.1 Unit tests (`base`)

Every static function of §5, §6 and §7 has direct tests on hand-built inputs whose numbers can be read off the
test (`system_docs/coding_style.md` §1, `system_docs/testing.md` §4): the flow-weighted mean and percentile on
five-step arrays; each statistic of the tag roles; the drawn graph from a ten-link view (loss folding, the solar
cut, the empty flow, the synthetic residual); every role of §6.2 from a minimal node; the chain of each family
(heat pump, gas, district heat, electric heating, air conditioner, no building); the tree with a tie; the scale
series; the minimum-band passes; the trunk length from a title and two lateral flows; each connector shape and a
grid route around one obstacle; corner radius reduction and the fault; label candidates and the scoring; the
overlap checker on two boxes and a box over a band; every metric with a known answer; the range table's rules on
good and bad entries; the warning order and numbering; the pin file's application and its faults; the caption.

### 9.2 Layout tests on recorded inputs (`base`)

`tests/sankey_inputs/<stem>/` holds the four input files of §4.1 of a full-year 900 s run of each of the twelve
building-sizer systems (about 50 to 150 KB each; from the balance report only the keys the renderer reads, to
keep them small), refreshed by `scripts/refresh_sankey_inputs.py <result dir>…` and stamped with the schema and
the commit. `tests/test_sankey_layout_recorded.py` runs the layout engine (not the PNG) on each and asserts:
0 faults, 0 overlaps; and the **structure** against `tests/sankey_inputs/<stem>/structure.yaml`, which records the
hand layout's chain, every node's parent, axis and slot, and every cross-link's shape class (straight, L, Z,
routed): "the twelve building-sizer systems match the hand layouts' structure (same tree and orientations, not
pixel-equal)". A `base` test checks each recording's schema against `REPORT_SCHEMA`, so a schema bump without a
refresh fails. The layout of one system takes well under two seconds (text extents through `TextPath`, no canvas);
the module fixture loads the inputs once.

### 9.3 Determinism (`base`)

The layout of the heat-pump recording is computed twice in one process and once in a subprocess
(`sys.executable -c`), and the three `Layout` serialisations are byte-identical. The test also perturbs every
flow by +25 % and asserts 0 faults and 0 overlaps, the prototype's robustness case that broke the hand layouts.

### 9.4 End-to-end (`extendedbase`)

`tests/test_sankey_figure_runs.py` runs one week at 900 s of one twin per family — `household_heatpump_building_sizer`,
`household_gas_building_sizer`, `household_district_heating_building_sizer`, `household_electric_heating_building_sizer`,
`simple_air_conditioner_household_building_sizer` — with `PLOT_SANKEY`, `COMPUTE_KPIS` and `WRITE_KPIS_TO_JSON`,
and asserts the files of §4.2 exist, `overall.layout_error.json` does not, `balance_report.json` has schema 7 with
a `statistics.loops` entry per loop of the twin and the expected tagged states, and the redraw command (§4.4)
reproduces `overall.layout.json` from the result directory. One PNG is compared by size and aspect only; no pixel
golden.

### 9.5 The sweep over every energy-system file

Every flat twin under `energy_systems/*.energy_system.yaml` (grouped and composed files are the same systems and
are covered by one grouped file as a smoke case) renders with 0 faults over a full year. Full-year renders of
every system take too long for a pull request; the sweep runs where the full years already run: the full-year
golden workflow (`golden-year.yml`) sets `PLOT_SANKEY` in its parameter set once PR 7 lands, and a layout fault
there fails the job, as a failing balance does. Until then PR 4's description reports the sweep run locally (the
command of §4.4 over the twelve recorded result directories plus the non-building-sizer files for a week).

### 9.6 What the acceptance test does not check

Picture quality beyond 0 overlaps: the owner reviews one figure per family on PR 5 and PR 6 (the figures attached
to the PR descriptions), as D4 of the complete Sankey spec asked for the arrow renderer.

## 10. Pull requests

Stacked, each small, each closing its bead in its own branch (AGENTS.md). Bead ids are the children of
hisim-energy-sankeys-9uoo listed in §12.

| PR | Bead | Scope | Done when |
|---|---|---|---|
| 1 | hisim-sankey-mpl-loop-statistics-3f1a (P1: every later PR reads it) | §5.1: `statistics.py` with `LoopStatistics`, `statistics.loops` in the report, schema 6, `FluidLoop.design_supply_temperature_in_celsius` set by `HeatDistribution` | `tests/test_energy_balance.py` pins the new keys on a hand-built table; the week of `test_energy_balance_storages.py` shows a loop entry with the floor heating's 35 °C limit; no result or KPI changes |
| 2 | hisim-sankey-mpl-state-statistics-7c2e | §5.2: `StatisticRole`, `StatisticTag`, `add_output(statistic=)`, `StateStatistics`, the tags on the heat pump, boilers, substation, electric heater, PV, both batteries, both storages, the building, the weather and the occupancy; schema 7 | every statistic tested directly; the heat-pump twin's week writes every state of the table; a wrong-unit tag is refused |
| 3 | hisim-sankey-mpl-plausibility-table-b4d9 | §7.3 the table with its first entries and the `base` test of its rules; §7.1 `metrics.py` with every formula and the evaluation against the table (no drawing) | every metric and rule tested; the table's estimates are marked; no checked entry written by an agent |
| 4 | hisim-sankey-mpl-band-renderer-5a8c | §6.1 to §6.3, §6.5, §6.11, §6.13: the drawn graph, roles, chain, residuals, the band renderer with absolute positions, the overlap checker, the pin file as the only source of positions (a full pin file for the heat-pump twin, written by hand from the prototype's layout), the files of §4.2, `PLOT_SANKEY` wired (§4.3), the redraw command (§4.4) | the heat-pump twin renders from its pin file with 0 overlaps from its recorded inputs; the `extendedbase` week of §9.4 for that twin; semgrep clean |
| 5 | hisim-sankey-mpl-auto-layout-9e2b | §6.4, §6.6 to §6.8, §6.10 pins as overrides, §6.12, §6.14: the orientation table, tree placement, routing, labels, growth, faults | §9.2 for all twelve recordings with their `structure.yaml`; §9.3; the five weeks of §9.4; the local sweep of §9.5 reported in the PR; the owner reviews one figure per family |
| 6 | hisim-sankey-mpl-warnings-panel-c6f3 | §8: markers, the warnings box, the data panel, the unmet stub, loop temperatures on the bands, the caption's second line | the heat-pump recording reproduces the trial's nine warnings in its order (the ranges of PR 3 give the same findings); 0 overlaps on all twelve with the notes and the panel; the owner reviews the heat-pump figure against the trial's |
| 7 | hisim-sankey-mpl-report-integration-2d7a | the figure in the PDF report (`INCLUDE_IMAGES_IN_PDF_REPORT`), `PLOT_SANKEY` in the full-year golden parameter set (§9.5), `energy_systems/README.md` and `simulation_parameters/README.md` updated, the D4 amendment and status lines written into `roadmap/complete_energy_sankeys_spec.md`, beads hisim-energy-sankeys-9uoo.19 and hisim-4hed closed with a note, Q1's decision applied | the PDF of the heat-pump week shows the figure; `golden-year.yml` renders every twin with 0 faults |

Order: 1 before 2 (schema sequence); 3 and 4 are independent of each other and of 2; 5 needs 4; 6 needs 1, 2, 3
and 5; 7 needs 6. Nothing here changes a simulation result or a KPI; the goldens stay.

## 11. Owner questions

Each with its options, what each means, and a recommendation.

- **Q1. The plotly HTML beside the matplotlib figure.** (a) *Keep both*: `EXPORT_ENERGY_BALANCE` keeps writing
  every view's HTML as today; `PLOT_SANKEY` adds the figure. Nothing is lost, the HTML keeps hover texts and the
  per-carrier views, two renderers to maintain. (b) *Replace*: the matplotlib figure replaces the HTML; one
  renderer, no interactivity, the per-carrier views vanish until a matplotlib filter exists. (c) *Keep the
  per-carrier HTML, drop the plotly `overall.html`*: the figure is the overall picture, the HTML the filters; the
  bead hisim-sankey-layout-48b8 (plotly columns) closes as not needed. Recommendation: (a) for v1, (c) in PR 7 if
  the owner, after reviewing PR 5's figures, no longer opens the plotly overall; hisim-sankey-layout-48b8 then
  closes as superseded.
- **Q2. The post-processing option.** (a) *Give `PLOT_SANKEY` (3) this meaning*: the name says what it does, it is
  already in the parameter format's list, and the option set's wire form rule ("a number is never reused for a
  different option") is kept, since a Sankey plot is what it always meant. (b) *A new option 31*
  (`PLOT_ENERGY_SANKEY_FIGURE`) and `PLOT_SANKEY` stays dead. Recommendation: (a).
- **Q3. A layout fault at the end of a run.** (a) *The run finishes*; the fault is a logged error and
  `overall.layout_error.json`; the tests fail. A picture never costs a result. (b) *The run fails*, as a failing
  balance does; a layout the engine cannot solve is then found at once, but a user's run dies for a drawing.
  Recommendation: (a).
- **Q4. Where the pin file lives.** (a) `energy_systems/<stem>.sankey_pins.yaml` beside the energy-system file,
  found by the file's stem through the realized record; the CLI takes `--pins`. (b) A `sankey_pins:` key inside
  the energy-system YAML's `metadata`: one file, but layout data in the model's input. (c) Pins only through the
  CLI, never in a run. Recommendation: (a).
- **Q5. Views in v1.** (a) `overall` only (this spec). (b) Also one figure per carrier as filters of the same
  layout, which needs a rule for what a filtered trunk looks like. Recommendation: (a); (b) is a later bead.
- **Q6. Formats.** (a) PNG, PDF and SVG, as the prototype (three `savefig` calls, a few seconds). (b) PNG and PDF.
  Recommendation: (a); the SVG keeps text selectable for reviews.
- **Q7. The heat pump's peak against its rating** (153 % in the trial). (a) *Panel only*: the hplib model exceeds
  its A-7/W35 rating at milder outdoor temperatures by design, so a fixed threshold would warn in every run. (b)
  *A plausibility warning* above a threshold the table names (say 130 %). Recommendation: (a) in v1; a range enters
  the table only with a source that says what a heat pump's peak over rating should be.
- **Q8. The PV yield range is site-specific** (PVGIS for Aachen: 909–1,149 kWh/kWp). (a) *Apply it only when the
  run's weather location is Aachen* (read from the realized `Weather` configuration), else no PV range. (b) *A
  small table of PVGIS yields per weather location HiSim ships* (`lt.Locations`), queried once by a person and
  recorded with the query parameters, so every location gets a checked range. (c) Query PVGIS at render time (a
  network call in post-processing; refused by the write guard's spirit and by offline runs). Recommendation: (a)
  now, (b) as a `data` bead once the location table exists.
- **Q9. Who checks the sources** (D9). The table of PR 3 ships with every entry an estimate except those a person
  has checked by then. Recommendation: the owner names who checks the Fraunhofer ISE SPF range, the HTW battery
  figures, DIN V 18599-10 and DVGW W 551, in that order of value (each turned a warning in the trial), and the
  checking person writes the `checked` block in a PR of their own.

## 12. Beads

Epic hisim-energy-sankeys-9uoo. The seven beads below are new (kind `feature`, area `postprocessing`); the two
existing beads close with PR 7.

| Bead | Content | PR |
|---|---|---|
| hisim-sankey-mpl-loop-statistics-3f1a (P1) | §5.1, schema 6 | 1 |
| hisim-sankey-mpl-state-statistics-7c2e (P2) | §5.2, schema 7 | 2 |
| hisim-sankey-mpl-plausibility-table-b4d9 (P2) | §7 | 3 |
| hisim-sankey-mpl-band-renderer-5a8c (P2) | §6.1–§6.3, §6.5, §6.11, §6.13, §4.2–§4.4 | 4 |
| hisim-sankey-mpl-auto-layout-9e2b (P2) | §6.4, §6.6–§6.8, §6.10, §6.12, §6.14, §9.2–§9.3 | 5 |
| hisim-sankey-mpl-warnings-panel-c6f3 (P2) | §8 | 6 |
| hisim-sankey-mpl-report-integration-2d7a (P2) | §4.3's PDF report, §9.5, the amendment of the complete Sankey spec, Q1 | 7 |
| hisim-energy-sankeys-9uoo.19 (P3, existing) | the arrow renderer of D4 — superseded by this spec | closes with 7 |
| hisim-4hed (P3, existing) | static images for the PDF report | closes with 7 |
| hisim-sankey-layout-48b8 (P2, existing) | plotly node columns — kept or superseded by Q1 | – |
