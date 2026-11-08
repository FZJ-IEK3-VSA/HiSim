# Spec: every energy-system YAML gets a matplotlib Sankey in the chained style, laid out automatically, with a warnings box and a data panel

Status: decided (owner, 2026-11-08; the open questions of the first draft decided the same day, D13 to D21; D2
corrected the same day: the diagram is drawn with `matplotlib.sankey`), nothing merged. A first try of PRs 4 and 5
exists on branch `sankey-mpl-auto-layout` (worktree `agent-tmp/wt-sankey-auto`, results in
`agent-tmp/sankey-auto-try/README.md`): it laid out 41 of the 42 drawable energy-system files with 0 faults and 0
overlaps and matched the twelve hand layouts' chain, parents and orientations, but with its own rectangular band
renderer, so the bends, notches and arrowheads of the hand figures were gone; its graph, role, chain, tree,
orientation, routing and label modules carry over, its output switches to `matplotlib.sankey` parameters (§10), and
its twenty corrections are worked into §3, §6 and §7. Owner: Noah Pflugradt. Epic: hisim-energy-sankeys-9uoo.
Builds on: `roadmap/complete_energy_sankeys_spec.md` (the graph model, §4; its D4 is amended here),
`roadmap/fluid_loops_spec.md` (the loop columns of the balance report), #1122 (loop specs), the prototype of
2026-11-08 (`agent-tmp/sankey-matplotlib-proto/`, not in the repository: twelve hand layouts of the building-sizer
systems in one style drawn with `matplotlib.sankey` by `draw_sankey.py`, a trial with temperatures, specific values
and plausibility checks, and a second trial with every warning in one box) and the May 2026 comparison
(`agent-tmp/sankey-may2026/`, the "unaccounted loss/gain" arrows).

All code citations are against `origin/main` at 5fbd2c7ee; `matplotlib.sankey` citations against matplotlib 3.10.9.

## 1. Problem

The energy Sankey of a run is a plotly HTML per view (`hisim/postprocessing/energy_balance/sankey.py`,
`SankeyDrawing`). Plotly places nodes in columns by topology and has no collision handling: in the full-year
`overall` view of `household_heatpump_building_sizer` the battery's loss labels sit on the hot-water tank, the
building's losses overlap `grid export`, and the battery draws a ring from and to the meter (bead
hisim-sankey-layout-48b8). The picture does not say what a reader of a heating system wants to know first: which
chain the heat takes from the meter to the rooms, how big the losses beside it are, what temperatures the loops run
at, and whether the numbers are plausible. The PDF report has no Sankey at all (bead hisim-4hed).

The prototype of 2026-11-08 drew the twelve building-sizer systems as "chained sub-diagram" Sankeys with
`matplotlib.sankey`: every component a sub-diagram whose trunk carries its name, the main heat chain on one row
from left to right, losses peeling off at the side in quarter-circle bends, inputs ending in notches and outputs in
arrowheads, storages above and below, every cross-link a connected band. All twelve reached 0 label overlaps, with
56 to 187 lines of hand layout each, about 10 minutes of agent time per system including the overlap checker. The
owner reviewed them and decided on a computed layout for every energy-system file (§2). The two trials added loop
temperatures, specific values, comfort figures, plausibility markers, one warnings box and a data panel; the
second trial's figure (`figures/trial/household_heatpump_building_sizer_trial.png` of the prototype) is the target
picture.

The prototype also listed where `matplotlib.sankey` has to be worked around: it places sub-diagrams implicitly
through path lengths, orders flows along a side by list rules, sets a connected sub-diagram's frame from the
connection, allows one connection per sub-diagram, centres its labels on the arrow tips and has no collision
handling (§6.11). The first try of the engine replaced the library by its own rectangular renderer and lost the
look of the hand figures ("all the arrow roundings are gone", owner); the engine of this spec keeps the library and
computes its parameters (D2).

## 2. Decisions

All taken by the owner on 2026-11-08 after reviewing the prototype's round 2, the two trials and the first try of
the engine.

**D1 — Every energy-system YAML gets a matplotlib Sankey in the chained sub-diagram style, with an automatically
computed layout; an optional per-file pin file may fix individual placements** (owner, 2026-11-08). The picture of
the prototype is the one the owner wants for every system, and twelve hand layouts showed that most of each layout
follows from the graph. Consequence: a layout engine (§6) derives roles, the main chain, the spanning tree,
orientations, sizes, placements, routes and labels from `energy_sankey.json`; a pin file (§6.10) overrides single
choices for showcase figures and is never required. *This amends D4 of `roadmap/complete_energy_sankeys_spec.md`*
("the renderer is plotly, as today; an arrow-style renderer is a later step"): the arrow-style renderer is now this
spec, built as its §10 plans; bead hisim-energy-sankeys-9uoo.19 (the arrow renderer) and hisim-4hed (static images
for the PDF report) close with it. The graph model of that spec (§4) and `energy_sankey.json` stay as they are:
one graph, now two renderers (D13).

**D2 — The diagram is drawn with `matplotlib.sankey`; the layout engine computes the library's parameters**
(owner, 2026-11-08). The first draft of this spec proposed HiSim's own band renderer with absolute positions; that
was the agent's recommendation, not a decision, and is withdrawn: the first try built on it drew trunks as
rectangles with right-angled branches and lost the quarter-circle bends, the input notches, the output arrowheads
and the pointed trunk ends of the hand figures. Consequence: the engine's output is what the prototype's
`layouts/*.yaml` held (§6.5): per sub-diagram its prior and connection, each flow's orientation (−1, 0, 1) and path
length, the trunk length, the label offsets, the `at` position of a further tree, the colour; the library draws
the bands, the engine places by an iterative search over those parameters (§6.6), joins the cross-links with the
prototype's connectors (§6.7) and places the labels itself (§6.8). The look of the twelve hand figures is the visual
reference (§9.6).

**D3 — The PV system is its own visible element, drawn from its AC electricity for now** (owner, 2026-11-08). The
sunlight and the module-and-inverter loss would make the PV the widest band of the picture (126.9 MWh of sun
against 26.3 MWh of electricity). Consequence: a component that converts `SOLAR` into an `ELECTRICITY` link is
drawn with a flat tail that carries the net of its solar input and solar losses, unlabelled; its block shows kWp
and kWh/kWp. The space left of the block is the slot for the sun and the loss arrows once bead
hisim-device-location-820d.5 publishes the DC output and the inverter loss; then the cut goes and the diagram
starts after the modules. A component that converts `SOLAR` into heat (the collector) keeps its sunlight and its
losses, as the prototype drew them.

**D4 — Every cross-link is a connected band with rounded corners** (owner, 2026-11-08). Merges (a generator's
second input from the same meter), loops (the battery's discharge back to the meter) and far links (the household's
heat into the building) are drawn as bands routed from stub to stub, never as a pair of labelled stubs.
Consequence: the connectors of §6.7.

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
and a sub-diagram whose inflated bands no longer balance is drawn with the library's smoothed taper.

**D11 — An unbalanced or undeclared component is drawn closed by an explicit, visibly marked residual, and is an
error entry in the warnings box** (owner, 2026-11-08). The May 2026 comparison drew every gap of the old version as
an "unaccounted loss" or "unaccounted gain" arrow; the district-heating twin's heat distribution declares no energy
ports today and shows a 20 kWh gap. Consequence: §6.3; nothing is invented to close a component, and the picture
never hides a gap.

**D12 — No winter-week Sankey for now** (owner, 2026-11-08). One figure per run, over the run's whole period.

The open questions of the first draft (§11), decided by the owner on 2026-11-08:

**D13 — Both renderers stay in v1; whether the plotly `overall.html` goes is decided in PR 7** (owner,
2026-11-08). `EXPORT_ENERGY_BALANCE` keeps writing every view's plotly HTML, hover texts and per-carrier views
included; `PLOT_SANKEY` adds the matplotlib figure. Consequence: nothing of `SankeyDrawing` changes; PR 7 asks the
owner, after the figures of PRs 5 and 6, whether the plotly `overall.html` is still opened, and closes
hisim-sankey-layout-48b8 as superseded if it goes.

**D14 — `PostProcessingOptions.PLOT_SANKEY` (value 3) gets this meaning** (owner, 2026-11-08). The option exists,
is listed in the parameter format and does nothing today; a Sankey plot is what it always meant, so the wire-form
rule of the option set is kept. Consequence: §4.3.

**D15 — A layout fault never fails the run** (owner, 2026-11-08). The run finishes, the fault is a logged error
and `energy_sankeys/overall.layout_error.json`, and the tests of §9 fail on it. A picture never costs a result.

**D16 — The pin file is `energy_systems/<stem>.sankey_pins.yaml` beside the energy-system file, and the redraw
command takes `--pins`** (owner, 2026-11-08). Consequence: §6.10.

**D17 — Only the `overall` view is drawn in v1** (owner, 2026-11-08). A per-carrier matplotlib view is a later
bead.

**D18 — PNG, PDF and SVG** (owner, 2026-11-08). Three `savefig` calls; the SVG keeps text selectable for reviews.

**D19 — The heat pump's peak thermal power against its rating is a data-panel row, not a warning** (owner,
2026-11-08). The hplib model exceeds its rating at milder outdoor temperatures by design; a range enters the table
only with a source that says what a heat pump's peak over rating should be.

**D20 — The PV plausibility check is a performance-ratio check on the run's own irradiation, not a yield against
PVGIS** (owner, 2026-11-08): "the PV yield plausibility should be based on the solar radiation at the location from
the weather and some standard efficiency, not an arbitrary PVGIS value." Consequence: the metric
`pv_performance_ratio` of §7.1, the specific AC yield in kWh/kWp divided by the irradiation on the module plane in
kWh/m² (the PV's `SolarPowerOnModulesInWatt` energy over its module area, §5.2), compared with a sourced range for
modern systems (§7.3, an estimate of 0.75–0.88 until checked under D9). The specific yield stays a value on the PV
block; only the ratio is judged. Location-independent, no PVGIS table, no network call.

**D21 — An agent prepares every source, the owner marks it checked** (owner, 2026-11-08). For each range the agent
fetches the original, quotes the exact passage with table and page into the PR description; the owner reviews the
quotes and writes the `checked` block in that PR; an agent never marks anything checked. Order of value: the
Fraunhofer ISE SPF range, the HTW battery efficiencies, the DIN V 18599-10 internal gains, DVGW W 551, the PV
performance-ratio range.

**D22 — No committed recordings and no committed pin files** (owner, 2026-11-08, after the three tries). The
twelve recorded building-sizer inputs and the twelve converted prototype pin files are not committed. Consequence:
the unit and rule tests build small synthetic systems in test code (`tests/sankey_test_systems.py`: a boiler fed
twice by its meter, a battery loop, a storage without a discharge, a system without a building, two generators
into one buffer, a solar collector with its pump, an undeclared component with a gap, a car), determinism and
robustness run on them, and the figure of every real twin is laid out in the golden jobs CI already runs (§9.5). The
pin-file feature stays (D16); the visual check against the hand figures happens once, in PR 4b's review, with the
comparison figures rendered from local pin files and attached to the PR description.

**D23 — The PDF report is retired; nothing goes into it** (owner, 2026-11-08; bead hisim-retire-pdf-report-be23,
#1245). The figure is the run's PNG/PDF/SVG beside the plotly HTML; §4.3's PDF-report sentence and PR 7's report
item fall away.

**D24 — A second link to the same node stays a second arrow** (owner, 2026-11-08, after the third try). A meter
that feeds its boiler twice (space heating and hot water) is drawn with two ports and two arrows, the second clearly
an arrow of its own (§6.4, §6.7), never merged into one band. The air-conditioner twin's household keeps the chain
rule (on the main row) for now.

**D25 — Three small PRs for the renderer and the layout** (owner, 2026-11-08). PR 4 is cut into 4a (the drawn
graph and what builds on it) and 4b (the renderer); PR 5 is the automatic layout; each is green on its own (§10).

## 3. Terms

- **Drawn node**: a component, meter or undeclared node of the `overall` view (`NodeKind.COMPONENT`, `METER`,
  `UNDECLARED` of `sankey.py`), drawn as one **sub-diagram** of `matplotlib.sankey` (one `Sankey.add` call).
  Boundary, loss, storage and unaccounted nodes are not drawn as sub-diagrams: their edges become terminal flows
  of the drawn node they touch.
- **Flow**: one edge of the `overall` view as the drawn node sees it: positive into the node, negative out of it,
  in kWh over the run; one entry of the sub-diagram's `flows` list. A **link** is a flow between two drawn nodes;
  a **terminal flow** has a boundary, loss, storage or unaccounted node at its other end.
- **Trunk**: the straight middle part of a sub-diagram, of length `trunklength` along the node's **axis** (one of
  E, S, W, N: the compass direction the trunk's main flow runs in on the page), with the title inside. Its **tail**
  is the upstream end, its **head** the downstream end, its two **lateral sides** the others. A flow attaches to
  one **side** (tail, head, or a lateral side named by compass direction), which the engine translates into the
  library's **orientation** (0 along the axis, 1 and −1 to the frame's two sides, §6.4).
- **Branch**: a flow on a lateral side: the library draws it leaving or joining the trunk in a quarter-circle bend
  of radius `radius`, then straight for its **path length**, ending in an arrowhead (output) or starting in a
  notch (input). A flow on the tail or head runs straight for its path length.
- **Tree connection**: the link by which a drawn node hangs from its **parent**, the drawn node its largest input
  comes from; the library's `prior`/`connect` pair, which joins the child's connecting flow to the parent's at
  their tips and sets the child's frame. **Cross-link**: every other link. A **tree** is a parent with all its
  descendants; a node without a parent is a **root**. A root other than the first is placed by an `at` offset.
- **Main chain**: the row of drawn nodes from the heat chain's meter to the building (§6.2), laid on y = 0.
- **Connector**: the band that draws a cross-link, an orthogonal polyline from the source's stub to the target's
  stub with rounded corners (§6.7); the prototype's `draw_connectors`.
- **Canvas unit (u)**: the axes' data unit; a band's width in u is its drawn kWh divided by `kwh_per_unit`
  (the library's `scale` is `1 / kwh_per_unit`). Pixels follow from a fixed 30 px/u (§6.5).
- **Overlap**: two label boxes intersecting, a label box intersecting a band it is not drawn inside on purpose, a
  connector band (without its ends) intersecting another band, two bands of different sub-diagrams intersecting,
  or a label clipped by the canvas (the four findings of the prototype's `report_overlaps` plus the band/band case
  between sub-diagrams, which the placement search of §6.6 needs).
- **Metric**: a derived value of a drawn node or band (§7.1), with its formula. **Range**: an entry of the
  plausibility table (§7.3) attached to a metric by role. **Warning**: a metric outside its range (plausibility),
  or one of the error conditions of §8.1.
- **Pin**: one entry of the pin file (§6.10) that replaces one parameter the layout engine computed.
- **Flow-weighted mean**: Σ ṁ·T / Σ ṁ over the steps with ṁ > 0, ṁ the loop's mass flow in kg/s, T a temperature
  in °C (c is constant along a loop and cancels).

## 4. Inputs and outputs

### 4.1 What the renderer reads

One data class, `SankeyInputs`, built either from a result directory (the CLI of §4.4) or in memory at the end of
the post-processing; both paths carry the same four things:

| Input | File in the result directory | Used for |
|---|---|---|
| the `overall` view | `energy_sankeys/energy_sankey.json` (`EnergyBalanceReport.sankeys()`) | nodes, flows, carriers, kinds; every number on a band; the PV's solar input for the performance ratio |
| the balance report | `balance_report.json`, schema 7 (§5) | component classes, verdicts, residuals, the loop statistics, the device and storage statistics |
| the KPIs | `all_kpis.json` (`WRITE_KPIS_TO_JSON`; in memory the KPI table `COMPUTE_KPIS` builds) | grid import and export, self-consumption and self-sufficiency rates, unmet hot-water demand, heat pump starts, comfort figures, floor area, TABULA reference need |
| the realized configuration | `realized.energy_system.yaml` (`AnnotatedEmitter.RECORD_FILENAME`, written by `hisim/energy_system/executor.py`) | the household's profile name and the TABULA code for the panel; everything else a component states through its statistic tag (§5.2). The two fields the renderer reads itself are listed in `SankeyStyle.CONFIG_FIELDS` |

A run started from a Python setup has no realized record; the fields it supplies are then absent, and the values
that need them are left out, never estimated.

### 4.2 What the renderer writes

Into `energy_sankeys/` of the result directory, beside the plotly files:

| File | What |
|---|---|
| `overall.png` | the figure at 30 px per canvas unit (§6.5), so its width follows the canvas: about 2,400 to 2,900 px for a full-year building-sizer system, 1,300 to 2,400 px for a week; white background |
| `overall.pdf`, `overall.svg` | the same figure as vector graphics (the SVG keeps text as text); D18 |
| `overall.layout.json` | the computed layout: the `matplotlib.sankey` parameters of every sub-diagram (§6.5) in the order added, every connector's route points, every label's position and its data source (§7.2), the scale, the canvas, the pins applied, the rounds the search took. The determinism test compares this file (§9.3), and a person starts a pin file from it |
| `overall.layout_error.json` | only when the layout has a fault (§6.12): `{"faults": [...], "overlaps": [...]}`, each fault a sentence starting with its kind (`overlap: 2 overlaps left`, `route: no clear route for link ...`, `layout: ...`, `error: the figure could not be drawn: ...`), each overlap left as `<kind> <first> <> <second>`; the layout as far as it got is in `overall.layout.json`. A clean redraw deletes a stale one. The golden check reads this file (§9.5) |

Only the `overall` view is drawn in v1 (D17). The per-carrier views stay plotly HTML (D13); a per-carrier
matplotlib view is a filter of the same layout and is left for a later bead.

### 4.3 The post-processing option and the plotly HTML

`PostProcessingOptions.PLOT_SANKEY` (value 3) exists, is listed in `hisim/energy_system/parameters_format.py`,
and does nothing today (its only other mention is a comment in `hisim/simulationparameters.py`). It gets this
meaning (D14). `PLOT_SANKEY` requires `COMPUTE_KPIS` and implies `EXPORT_ENERGY_BALANCE` (the figure is drawn from
the same report and views that option writes, and the files are what the CLI redraws from); a parameter set with
`PLOT_SANKEY` and without `COMPUTE_KPIS` is refused when the parameters are read, with the sentence that names
both options. The figure is drawn at the end of `PostProcessor.export_sankeys`
(`hisim/postprocessing/postprocessing_main.py`), after the balance files are written, and also in the failure path
of `judge_the_results`, where the balance report is written before an `EnergyBalanceError` propagates: a run that
fails its balance gets its picture with the residual arrows (D11) when the option is set.

As built in PR 4b: `PostProcessor.writes_energy_balance` is the one test of "the balance files are written"
(`EXPORT_ENERGY_BALANCE` or `PLOT_SANKEY`), used by `export_sankeys` and by the failure path of `judge_the_results`;
`PostProcessor.plot_sankey` then calls `FigureFiles.draw_run(result_directory)`, which never raises (D15): a layout
fault, a missing input and an exception inside the renderer each become a sentence in the returned list and in
`overall.layout_error.json`, and every sentence is logged as an error. The refusal is
`SimulationParametersReader.refuse_sankey_without_kpis` (`EF-07`, "PLOT_SANKEY draws the Sankey figure from the run's
KPIs, so it requires COMPUTE_KPIS; add COMPUTE_KPIS or drop PLOT_SANKEY."); it guards every parameter file, the
JSON files of the Python setups included (all shipped files that name `PLOT_SANKEY` also name `COMPUTE_KPIS`), but
not a Python setup that appends options in code. Until the automatic layout lands (PR 5), a run with `PLOT_SANKEY`
and no pin file writes the one fault `layout: no pin file given, and this version has no automatic layout yet`;
that is why the golden week parameter sets get the option only in PR 5 (§9.5).

The plotly HTML stays as it is in v1 (D13): `EXPORT_ENERGY_BALANCE` keeps writing the per-view HTML, interactive
hover text included, and nothing of `SankeyDrawing` changes. The matplotlib figure is the picture of the run, its
PNG/PDF/SVG beside the plotly HTML; the PDF report is retired and shows nothing of it (D23). Whether the plotly
`overall.html` is retired is decided in PR 7 (D13).

### 4.4 The redraw command

`hisim energy-system sankey <result directory> [--pins FILE] [--out DIRECTORY] [--graph]` draws the figure from a
finished result directory without simulating again: it builds `SankeyInputs.from_result_directory`, applies the pin
file when given (no pin file is looked up, §6.10), writes the files of §4.2 into `--out` (default: the result directory's
`energy_sankeys/`) and exits 0 on 0 overlaps, 4 when the layout engine reports faults (the files of §4.2 are still
written where it could draw), 2 when the directory lacks an input. A render takes a few seconds, a run 20 to 45 s,
so layout work and pin files are done with this command (the first try built it in `hisim/cli.py`). `--graph`
prints the drawn graph instead (chain, every node with role, parent and flows, the residuals), exit 0 or 2; the
exit code 4 is `ExitCodes.LAYOUT_FAULTS` in `hisim/cli_exit.py`. It writes into
the directory it is pointed at; the renderer lives under `hisim/postprocessing/`, which `.semgrep/file_writes.yml`
already sanctions.

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

Definitions, each a static function of `LoopStatistics` in a new module
`hisim/postprocessing/energy_balance/statistics.py`, each with direct tests on arrays of a few steps:

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
  which `HeatDistribution` sets from
  `EmitterDesign.for_type(config.heating_system).maximal_supply_temperature_in_celsius`
  (`hisim/components/heating_curve.py`); `design_limit_source` names the emitter type. No other loop has a limit in
  v1 (a tank coil has no design supply).

### 5.2 Device and storage states (schema 7): the statistic tag

The renderer must not know which output of which class is "the tank temperature". A component says it about
itself, once, where it declares the output: `Component.add_output(..., statistic=StatisticTag(...))`, beside the
existing `energy_port=` argument. `hisim/loadtypes.py` gains the enum `StatisticRole`, `hisim/energy_port.py` the
frozen data class `StatisticTag(role, rated_in_watt=None, capacity_in_kilowatt_hour=None,
module_area_in_square_metre=None)`:

| `StatisticRole` | Declared on | Statistics written |
|---|---|---|
| `THERMAL_OUTPUT_POWER` | a heat generator's total thermal output in W, with `rated_in_watt` (the heat pump's `ThermalOutputPowerTotalHeatpump` with `set_thermal_output_power_in_watt`; the boilers', the substation's and the electric heater's output with their rated power) | `runtime_hours` (steps with P > 1 W), `full_load_hours` (Σ P·Δt / rated), `peak_in_watt`, `peak_on_coldest_day_in_watt`, `coldest_day` (date), `coldest_day_mean_outdoor_in_celsius` (needs `OUTDOOR_AIR_TEMPERATURE`) |
| `ELECTRIC_OUTPUT_POWER` | a generator's AC electricity in W with `rated_in_watt` (the PV's `ElectricityOutput` with `power_in_watt`) and, for a solar generator, `module_area_in_square_metre` (the array's module area the PV already computes for `SolarPowerOnModulesInWatt`: peak power over the module's peak power, times the module area) | `energy_in_kilowatt_hour`, `specific_yield_in_kilowatt_hour_per_kilowatt` (energy / rated in kW), `peak_in_watt`, and with a module area `irradiation_on_modules_in_kilowatt_hour_per_square_metre` (the component's `SOLAR` consumption over the run / module area) and `performance_ratio` (§7.1, D20) |
| `START_COUNTER` | a cumulative start counter (`CounterOnOff` of the heat pump) | `starts` (the last value minus the first) |
| `STORAGE_TEMPERATURE` | a storage's medium temperature in °C (`SimpleDHWStorage.WaterTemperatureAtEndOfStepInCelsius`, `SimpleHotWaterStorage.WaterMeanTemperatureInStorage`) | `min_in_celsius`, `max_in_celsius`, `hours_below_40_celsius`, `hours_at_or_above_60_celsius` (the two thresholds are `StorageTemperatureThresholds` constants of the statistics module, named for the taps and DVGW W 551; the plausibility table owns their sources) |
| `STATE_OF_CHARGE` | a battery's state of charge in [0, 1] with `capacity_in_kilowatt_hour` (`Battery.StateOfCharge`, `CarBattery`) | `mean_state_of_charge`, `equivalent_full_cycles` (Σ positive SOC increments: the charged energy of the cells over the capacity) |
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
(§6.10); output: a `Layout` (the `matplotlib.sankey` parameters §6.5 lists, which `overall.layout.json`
serialises) and the list of faults. The graph half (§6.1 to §6.4) is pure: static functions on hand-built graphs,
no class name of a component read for a layout decision (roles come from kinds, carriers and the shape of the
links), no file read. The placement half (§6.6 to §6.8) builds the `Sankey` on an `Agg` figure, since the library
places implicitly and the engine has to read back what it drew.

### 6.1 The drawn graph

From the `overall` view:

1. **Drawn nodes** are the nodes of kind `component`, `meter` and `undeclared`. Every other node is folded into
   the flows of the drawn node it touches: a loss edge `component → loss node` becomes a terminal output of the
   component named by the loss node's port (the `loss node → outdoors` edge is dropped); a storage edge becomes a
   terminal flow in the direction the view gives (into the component when the run's net released energy, out when
   it stored); a boundary edge a terminal flow to or from the boundary node; a residual edge a terminal flow marked
   `residual` (§6.3).
2. **The solar cut (D3)**: a drawn node with a `SOLAR` input and an `ELECTRICITY` link to a drawn node has its
   `SOLAR` input and its `SOLAR` losses replaced by one unlabelled terminal input, flagged `cut`, of their
   difference, so the trunk is drawn with a flat tail (the prototype's `flatten_input_notch` on that input). A node
   with a `SOLAR` input and no electricity link keeps them (the collector: its pump loss is an electricity loss,
   not a link, and does not make it a PV).
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
| `SOLAR_COLLECTOR` | consumes `SOLAR`, produces a heat carrier to a drawn node (the building also takes sun and loses room heat, but to loss nodes, so it does not match) |
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
| `CONVERTER` | everything else (electrolyzer, fuel cell, CHP, transformer, hydrogen storage, a battery without a discharge link in the run's period) |

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

**Sources go upstream.** A drawn node without an input link that feeds a meter (the PV; the electrolyzer system's
wind transformer, 180 MWh in a week) is the first sub-diagram of the meter's tree and the meter hangs from it as a
continuation (the prototype: `PVSystem` first, `ElectricityMeter` with `prior: PVSystem`); the meter's boundary
import then moves to a lateral side (§6.4). Hung below the meter, such a node's band would make a U-turn.

### 6.3 Residuals (D11)

A residual flow is drawn as a terminal branch of its node in the **unaccounted style**: grey fill with a hatch,
dashed grey outline, the label in a dashed grey box: "unaccounted loss · 20 kWh" for an output gap (Σ in > Σ out),
"unaccounted gain · 20 kWh" for an input gap. When the node is undeclared, or when the balance report's verdict for
the node is `does_not_close`, the label and the box are red and the label says why in three to five words
("undeclared: no energy ports", "balance does not close"); either is an error entry of the warnings box (§8.1).
The library draws one fill per sub-diagram, so the residual branch gets its own style by a second `PathPatch` over
the branch's outline (the prototype's `mark_errors`). The residual flow takes the side the role table gives for
losses (§6.4). A node whose residual is within `RESIDUAL_KWH` draws none.

### 6.4 Orientation rules by role

The table decides, per role, the node's axis, the side its tree connection comes in at, the side of each class of
flow, and the slot of each child. It is data (one row per role, six flow classes: link in, terminal in, link out,
boundary out, loss, storage change) plus the context rules below it; the first try implemented it as `RoleSides`
rows in `orientation.py`, which carry over. "Head" and "tail" are relative to the axis; the lateral sides are
compass directions of the page.

| Role | Axis | Tree connection in at | Links in | Terminal in (boundary) | Links out | Boundary out | Losses | Storage change |
|---|---|---|---|---|---|---|---|---|
| `ELECTRICITY_METER` | E | tail (W), from the source upstream (`PV`, a transformer) when there is one; else no tree connection and grid import takes the tail | storage discharge: S; other production (a CHP, a fuel cell): S | grid import: N (tail when nothing is upstream) | to the chain's next node (`GENERATOR`, `CONVERTER`, `HOUSEHOLD` on the chain): head (E), a second flow to the same node stacked below the first; to `HOUSEHOLD`, `ELECTRIC_STORAGE`, `VEHICLE_STORAGE`: S; to `SOLAR_COLLECTOR` (pump) and `COOLER`: N (both sit above or after the chain; from the south the band would U-turn) | grid export: N when the meter is the chain root, else head (E) | – | – |
| `FUEL_METER` | E | – (root): the supply boundary flow takes the tail (W) | – | tail (W) | to its generator: head (E), a second flow to the same generator stacked below | – | – | – |
| `PV` | E | – (root): the cut takes the tail (W), flat | – | tail (W) | to the meter: head (E) | – | (cut) | – |
| `GENERATOR` | E | tail (W); a second input from the same source: tail, stacked below | – | `AMBIENT_HEAT`, `SOLAR`: S | `SPACE_HEATING_HEAT` to the chain's next node: head (E); `DOMESTIC_HOT_WATER_HEAT`: N | – | S | S |
| `BUFFER`, `DISTRIBUTION` | E | tail (W) | a second heat source (collector): S | – | chain successor: head (E) | – | N; residual: S | S |
| `BUILDING` | E | tail (W) | `ROOM_HEAT` from `HOUSEHOLD`: S | `SOLAR`: N | `COOLING` to a `COOLER`: head (E) | – | by rank: largest head (E), second N, third and further S | S |
| `HOT_WATER_TANK` | E | S (from the generator below) | a second heat source (collector): tail (W) | – | – | taps: head (E); not delivered (§8.1): N | N | N (released: an input from N; stored: an output to N) |
| `SOLAR_COLLECTOR` | E | – (root): sun takes the tail (W) | pump electricity: S | tail (W) | heat: head (E) | – | N | – |
| `HOUSEHOLD` | E | N (lateral), from the meter above; on the chain (air-conditioner twin): tail (W) | – | body heat: S | heat to the building (two flows): head (E) | – | appliance electricity not released: S | – |
| `ELECTRIC_STORAGE` | S | tail (N) | – | – | discharge (back to the meter's S side): W | – | by rank: largest E, the rest head (S) | W |
| `VEHICLE_STORAGE` | S | tail (N) | – | – | to the vehicle: head (S) | – | E | W |
| `VEHICLE` | E | N (lateral), from the storage above | – | – | – | driving: head (E) | S | – |
| `COOLER` | E | tail (W) | electricity: N | – | – | to the ambient sink: head (E) | S | – |
| `CONVERTER` | E | tail (W) | S | N | head (E) | head (E) | S | S |

Context rules, applied after the table:

1. **A chain node enters at its tail**, whatever its role's tree-connection side: the household on the chain of the
   air-conditioner twin takes the meter's band at its tail (W), not at its north side, else the whole chain turns
   vertical.
2. **A connected sub-diagram's frame comes from its connection**, never from `rotation` (the library ignores
   `rotation` for a connected sub-diagram, `Sankey.add`). The engine therefore chooses the child's connecting
   orientation from the axis the table wants and the direction the parent's flow leaves in: with `d` the page
   direction of the parent's connecting output (its axis for a head output, the side's compass direction for a
   lateral one) and `up(axis)` the page direction of the child's frame "up" side (E → N, S → E, W → S, N → W), the
   child's connecting input has orientation 0 when its axis equals `d` (a continuation), 1 when `up(axis)` is
   opposite to `d` (the band enters the frame's top), −1 when `up(axis)` equals `d` (it enters the frame's
   bottom); an axis opposite to `d` is impossible and a fault. Examples from the hand layouts: the battery below
   the meter (`d` = S, axis S → 0), the household below the meter (`d` = S, axis E, up = N → 1), the tank above
   the heat pump (`d` = N, axis E → −1), the car below its battery (`d` = S, axis E → 1: horizontal, fed from the
   top, driving east, as the hand layout).
3. **Losses are ranked by value, not by name**: the roles above say "by rank" where the losses share a carrier
   (the building's room-heat losses, the battery's electricity losses); largest head, second north, third and
   further south for the building; largest east, the rest head for the battery. This reproduces all twelve hand
   layouts; a rule by port name would read class knowledge the engine must not have.
4. **Slot rank overrides value order**: on the meter's south side the `ELECTRIC_STORAGE` is leftmost, the
   `VEHICLE_STORAGE` next, the `HOUSEHOLD` rightmost, whatever their values (in the electric-heating twin's January
   week the battery's charge exceeds the household's electricity; by value the battery would take the household's
   place and block its bands to the building). Everywhere else links are stacked by value descending.
5. **Storage changes whose sign flipped between runs** (the tank stored heat in the gas twin and released it in the
   heat-pump twin) keep their side; only the arrow's direction and the display name change (`{in: "Stored heat
   released", out: "Heat stored"}` in the style table).
6. **Children**: a `GENERATOR`, `CONVERTER` or `HOUSEHOLD` that is the chain's next node is a continuation child
   (E); a `HOT_WATER_TANK` hangs above its generator; `HOUSEHOLD`, `ELECTRIC_STORAGE` and `VEHICLE_STORAGE` hang
   below the meter in the slot rank of rule 4; a `VEHICLE` hangs below its storage; a `COOLER` continues the
   building (E); a `CONVERTER`'s children continue it for the largest and hang below for the others.

**Sides into orientations and list order.** A side becomes the library's orientation through the frame: for a
sub-diagram with axis `a`, a flow on the tail or head has orientation 0; a flow on the page side `up(a)` has
orientation 1 and on the opposite side −1 (the battery with axis S: its discharge to the west is −1, its inverter
loss to the east 1, as the hand layout). The library then stacks the flows along the trunk by the position in the
`flows` list (`Sankey.add`, matplotlib 3.10.9), which the engine must produce from the wanted order:

- frame-top inputs (orientation 1) stack from the trunk's tail corner outward toward the tail in list order, the
  first in the list nearest the trunk; frame-top outputs (orientation 1) stack from the head corner outward toward
  the head in list order, the first nearest the trunk;
- frame-bottom inputs (orientation −1) stack from the tail corner outward in *reversed* list order, the last in
  the list nearest the trunk; frame-bottom outputs (orientation −1) from the head corner outward in reversed order,
  the last nearest the trunk;
- tail inputs (orientation 0) stack across the tail from the frame's bottom upward in reversed list order (the last
  in the list lowest); head outputs (orientation 0) across the head from the frame's top downward in list order
  (the first highest);
- a flow whose |value| is below the library's `tolerance` is not drawn and cannot connect; the minimum band (§6.5)
  keeps every drawn flow above it.

So the engine orders each side as the rules above want (tree connection first, then links in slot rank, then by
value descending, then terminal flows by value descending, residual last, "first" meaning nearest the trunk for
a lateral side and the frame's top for a head or tail stack) and emits the `flows` list by interleaving the sides
according to these four rules; a direct test per side checks that the emitted list puts the wanted flow nearest
the trunk (by reading the drawn tips back).

### 6.5 Scale, sizes and the parameters

- **Pixels per unit are fixed**: `PX_PER_UNIT` = 30 (the prototype's 29.7). Text and bands then keep their
  proportions in every figure, the minimum band is known in kWh before the layout starts, and the figure's size
  follows its canvas (§6.9).
- **Scale.** One `kwh_per_unit` per figure: the smallest value of the series 1, 2, 4, 5 × 10ⁿ for which the widest
  drawn band is at most `WIDEST_BAND_IN_UNITS` (7 u). The 4 is in the series so that every full-year
  building-sizer system lands at 4,000 kWh/u (the PV's 26.3 MWh is the widest band at 6.6 u), the prototype's
  family scale; a week-long test run lands at 100 kWh/u. A pin may fix it. The library's `scale` is
  `1 / kwh_per_unit`, its `unit` None and `format` "" (no library labels).
- **Band width** of a flow: |drawn kWh| / `kwh_per_unit`.
- **Minimum band (D10).** `MIN_BAND_PX` = 2, so `min_band_kwh = MIN_BAND_PX / PX_PER_UNIT × kwh_per_unit`
  (267 kWh at 4,000 kWh/u). Every band below it is drawn at that minimum (`drawn_kwh = copysign(max(|value|,
  min_band_kwh), value)`, at both ends of a link, so a connection still matches within the library's tolerance)
  and flagged `inflated`; its label ends in " †" and the caption says "† drawn at the minimum width of 2 px, not to
  scale". A sub-diagram whose inflated flows no longer sum to zero is drawn with the library's smoothed taper (it
  logs the imbalance at info level and draws on). The library's `tolerance` is 1e-6 u.
- **The library's style constants**, on `LayoutConstants`, the prototype's `canvas.sankey`: `gap` 0.56 u (the
  spacing between stacked lateral flows and the inset of the first from the trunk corner), `radius` 0.24 u (the
  inner radius of a branch's quarter-circle bend; the outer radius is `radius` plus the band width, so a hairline
  keeps a visible bend), `shoulder` 0.096 u (the arrowhead's overhang beyond the band), `offset` 0.192 u (the
  library's label offset, unused since labels are placed by the engine), `head_angle` 110° (the arrowhead's and
  notch's opening). These give the look of the twelve hand figures and are not tuned per system.
- **Trunk length** (`trunklength`) of a sub-diagram: the largest of (a) the title's width plus `TITLE_PAD`
  (0.6 u) on both sides, (b) the note block's width (§6.8) plus the pads, (c) `MIN_TRUNK` (3 u). To (a) and (b) add
  the library's `gap` at both trunk ends and the notch depth of a tail input (half its width × the pitch): the title
  sits between the first lateral flows' corners, not between the trunk's ends, and without these the title of a
  short trunk touches the prior's arrow (finding of the second try). The lateral
  stacks sit beyond the trunk's corners and need no trunk length of their own. Text widths come from
  `matplotlib.textpath.TextPath` at the style's font size, converted to u through `PX_PER_UNIT`, so they are
  deterministic given the font (§9.3).
- **Path lengths** (`pathlengths`, one per flow): `STUB` (1.3 u) for a terminal branch, 0.48 u for the stub of a
  cross-link (the connector continues it), 0.8 u for a tail input that is a tree connection or a boundary supply
  (the prototype's value), and for a tree connection's output at the parent the **connection length**: the width
  of its label plus 2 × `TITLE_PAD`, at least `MIN_CONNECTION` (2 u), whether the label is drawn inside the band (a
  band at least 1.1 line heights wide) or beside it (a narrower band; else the heat pump's electricity label
  collides with both trunks). The placement search (§6.6) grows path lengths from these starts.
- **The parameters per sub-diagram**, what `Layout` holds and `overall.layout.json` writes, are exactly the keys of
  a prototype layout entry: `component`, `prior` and `connect` (the link label; `None` for a root), `at` for a root
  that is not the first, `trunklength`, `title_offset`, `color`, and per flow `link` (or `residual`/`cut`),
  `orientation`, `pathlength`, the label's candidate and shift; plus per connector its `turns` and label placement
  (§6.7). The order of the sub-diagrams is the order they are added: a parent before its children (the library
  requires `prior` to exist), the chain's tree first, then the further trees.

### 6.6 Placing by search

The library places every connected sub-diagram implicitly: the child's connecting tip is put on the parent's, so a
child's position is the sum of the path lengths and band widths of everything before it on its tree, and an
unconnected root sits at the origin until moved. Placement is therefore an iterative search over the parameters of
§6.5, with the drawn result read back after each round:

1. **Initial parameters** from §6.4 and §6.5: the sub-diagram list in tree order (the chain's tree, then the further
   trees), the `flows` lists in the emitted side order, the orientations, the initial trunk and path lengths, the
   `at` of each further tree at the nearest position of its quadrant: a `SOLAR_COLLECTOR` above and left of the node
   it feeds (the tank), so its head faces the tank's tail with a Z route; an `ELECTRICITY_METER` that is not the
   chain root (fuel systems) below the chain, its tail at the chain root's x; any other root below the chain, right
   of the trees already placed there. A pin (§6.10) replaces any of these before the first round.
2. **Build**: `Sankey(ax, scale, unit=None, format="", tolerance=1e-6, gap, radius, shoulder, offset, head_angle)`,
   one `add` per sub-diagram with `patchlabel=""` and empty labels, `finish()`, then the further trees moved by
   their `at` (the prototype's `place_trees`: the whole tree's patch vertices, tips and text anchors shifted so the
   anchor sub-diagram's centre lands on `at`), the stacked second input's seam flattened
   (`flatten_parallel_connections`), the connectors routed (§6.7), the labels placed (§6.8), and the axes limits
   set to the canvas (the library's `finish()` fits the axes to its own extents, which must not win).
3. **Check**: `OverlapCheck` on the drawn patches' paths, the connector outlines and the label boxes, reporting the
   findings of §3; 0 findings ends the search.
4. **Repair**, one deterministic move per finding, applied in this order within a round, then back to step 2:
   - a label on a label or a band: the label's next candidate (§6.8), no rebuild needed;
   - a label with no clear candidate on a lateral branch: the branch's path length grows by `GROW` (0.5 u); on a
     tree connection: the connection length grows by `GROW` (the child's whole subtree moves away);
   - a band of one subtree on a band of another subtree that hangs from the same parent side: the deeper child in
     slot rank gets its connection length raised until its subtree clears the other's extent along the axis plus
     `NODE_MARGIN` (1 u) — siblings are staggered in depth, as the hand layouts staggered the household (5.6 u),
     the battery (9.6 u) and the car battery (17.5 u) under the meter;
   - a band of a further tree on anything, or a connector of it that found no route: the tree's `at` slides away
     from the chain by `SLIDE` (0.5 u) along its quadrant's direction;
   - a title wider than its trunk: the trunk length grows to fit (the title is wrapped to two lines first when it is
     wider than 2 × `MAX_TITLE_WIDTH`, 6 u).
   The flow order along a side is not a search move: it is fixed by §6.4 (the prototype's hand orderings all
   follow those rules).
5. **Bound**: `MAX_ROUNDS` (12) rounds; a round that changes no parameter and still has findings, or the twelfth
   round, ends the search with the findings as faults (§6.12). The first try needed 2 to 5 rounds per system with
   a comparable loop.

The search is deterministic: the findings are sorted by (kind, first element name, second element name), the moves
by that order, and no step draws a random number.

### 6.7 Connectors (D4)

Every cross-link is drawn as the prototype's connector (`draw_sankey.py`: `flatten_output_head`,
`flatten_input_notch`, `route_points`, `rounded_centre_line`, `band_outline`, `draw_connectors`), in order of band
width descending:

1. **Stubs.** The link is a flow at both ends with path length 0.48 u. The source's stub is an output on the side
   the role table gives, the target's an input on its side. The source stub's arrowhead is cut flat (the head's tip
   and shoulders moved onto the head's base line) and the target stub's notch filled (its inner point moved onto the
   line of its outer corners), so the connector continues both stubs without a seam; the connector reaches
   `connector_overlap` (0.096 u) into both stubs so its fill covers the cut ends. A tree connection that runs beside
   a connector into the same side of the child (a generator's two inputs from one meter) is flattened the same way,
   and the child's notch moved back by the shoulder, so the two bands enter side by side with one straight seam:
   this is the prototype's look and stays for a pinned connector without `parallel`. A connector marked `parallel`
   (D24; the automatic layout marks every second link into a child of its source, a pin file says
   `parallel: true`) keeps the child's notch, stops at its outer edge and enters it with an arrowhead of its own
   (`SankeyFigure.arrowhead`), so it reads as a second arrow. The route of a connector is checked against its own
   source's patch only beyond the stub's arrowhead (`RouteRequest.reach`): the head is cut flat after the route is
   found, so checking it would refuse every route (finding of the second try).
2. **Shape**: *straight* when the two stubs are collinear and face each other; *L* when they are perpendicular and
   the lines through them meet in front of both; *Z* when they are parallel, face opposite directions and are
   offset. Each shape is tested against the obstacle map; the first that is clear is taken. `route_points` turns a
   list of `turns` (where each segment ends, absolute or relative) into orthogonal corner points, refusing a route
   whose first segment does not leave the source stub along its direction or whose last does not arrive along the
   target's.
3. **Grid search** otherwise: A* over a grid of `GRID` (0.25 u) on the canvas inflated by 3 u, orthogonal moves,
   cost = length + `BEND_COST` (3 u) per turn, a cell blocked when the band (its full width plus `CLEARANCE`,
   0.3 u) would intersect a drawn patch, a placed connector or a placed label. The route is the cell path
   simplified to its corners, written as `turns`.
4. **Corners** are rounded with `CONNECTOR_RADIUS` (0.64 u), reduced to half of the shortest adjacent segment when
   that is smaller, down to `MIN_RADIUS` (0.24 u); a grid route of a narrow band may go down to half its width plus
   0.05 u, at least half a grid cell (a hairline's inner edge folds only below half its width, and a corridor of one
   grid cell has no 0.48 u to turn in; one-cell jogs are removed first, `Router.remove_jogs`); a route that leaves
   less is a fault, and the search first retries
   after sliding the target's tree (§6.6 step 4) one more step. The centre line is sampled at 16 points per corner,
   the outline is the centre line offset by ± half the width, drawn as a `PathPatch` in the source sub-diagram's
   colour with the two sides as thin dark lines (0.6 pt).
5. **Parallel connectors** (two routes that share a corridor, such as the household's two heat flows to the
   building) never cross when both ends stack the bands in the same order: the L shapes then nest by themselves,
   and no ordering step is needed beyond the stacking order of §6.4. The first try confirmed this on all twelve
   systems.
6. **A route that fails** after `MAX_ROUTE_ROUNDS` (3) slides of the involved trees is a fault that names the link
   and both ends; the layout continues without that band, and the stubs keep their arrowhead and notch and get
   their labels (the prototype's stub style), so the picture is still readable while the fault is visible.
7. **Routes and labels** (open item of the first try): a label cannot move a route, and in the first try routes did
   not avoid labels; in `dynamic_components` six hairline labels of two tiny batteries end on a discharge route
   that loops around the second battery, 3 overlaps that the search cannot repair. The obstacle map of step 3
   therefore includes the labels placed so far, and a connector is re-routed in the round after a label of another
   node was placed on it; until that lands, that file is the one known failure (§6.14).

### 6.8 Labels and the note block

The library's labels are disabled (`labels=[""] * n`, the `text` of each sub-diagram hidden); the engine places its
own. Every flow has a label of two lines, name and value (`format_kwh`: MWh with one decimal from 10 MWh, whole kWh
from 10 kWh, one decimal below; thousands separators; no sign). A loop's band carries a third line with its
temperatures (§7.1). Every sub-diagram has a title (bold, at the trunk's centre, the library's `text` position,
plus `title_offset`; 11 pt) and a **note block** under the title (11 pt regular, one text block with the title;
flow labels are 9 pt; the hand figures used 9 pt for the metric lines, a presentation choice the layout does not
depend on): the metric lines §7.1 gives its role, each one value with
its unit and, when it has a warning, the marker ⚠n. The note block is reserved before placement: its width counts
in the trunk length (§6.5) and its box is an obstacle, so the search never has to move it.

**Candidates** per label, tried in this order:

- terminal branch: beyond the arrow tip, on the side facing away from the trunk (the prototype's rule: the label
  anchored so it never covers the arrow; an input's label moved out by its notch depth, since the library's tip of
  an input is the notch's inner point); beside the stub on either side; the same with the name wrapped to two lines
  (`textwrap` at 18 characters);
- tree connection: inside the band (white, bold) when the band is at least 1.1 line heights wide; else above the
  band at its middle, below it, at its quarter points;
- connector: above, below, left or right of each segment's middle, from the longest segment to the shortest;
- title and note block: fixed (no candidates); a title wider than `MAX_TITLE_WIDTH` is wrapped to two lines before
  the trunk length is computed.

**Scoring** of a candidate: 1,000 per overlap with a placed label, a drawn patch or a connector band (a label inside
its own band is exempt from that band), 1,000 when it leaves the canvas, plus its distance from the first
candidate in u. **Order**: labels of the widest bands first, then the rest by width descending. **Repair**: after
one greedy pass, every label that still overlaps is re-placed over all its candidates with the others fixed, twice;
what remains goes to the search's repair moves (§6.6 step 4). The overlap checker is the prototype's
`report_overlaps`, made a class `OverlapCheck` with the five findings of §3 as typed records.

### 6.9 Canvas, panel and caption

The canvas is the bounding box of every patch, connector and label plus `CANVAS_MARGIN` (1 u), set as the axes
limits after `finish()` (the prototype's `canvas.xlim/ylim`, now computed). The figure's width is the canvas's
width at `PX_PER_UNIT` (30 px/u, 110 dpi); its height is the canvas's height at the same scale plus a title line
(0.55 in), the panel (§8.3, its height from its rows) and the caption (0.7 in). The caption reads: the data source
("yearly energy from the run's energy balance, overall view"), the scale ("band width: 1 u = 4 MWh"), the
loop-temperature definition in one line, the marker legend, the PV note of D3, the balance verdict, the † note when
a band is inflated, and a second line with the run's key results from the KPIs (grid import and export,
self-consumption and self-sufficiency rates, heat delivered to the rooms or heat removed by cooling).

### 6.10 The pin file

An optional YAML file, `energy_systems/<stem>.sankey_pins.yaml` beside the energy-system file (D16; the CLI takes
`--pins`), schema `hisim.sankey_pins/1`. The post-processing looks none up: the realized record carries no source
path (finding of PR 4b), so a run cannot find the file beside its energy-system file; since no file ships one in v1,
pins are applied through the redraw command only. PR 4b reads *complete* pin files (every sub-diagram with its
flows, every connector with its turns), drawn in one round without repair moves; a partial pin over the computed
layout needs the automatic layout and comes with PR 5. Its keys are the prototype's layout keys (`layouts/<stem>.yaml`), reduced to what differs
from the computed layout:

```yaml
schema: hisim.sankey_pins/1
canvas: {kwh_per_unit: 4000}                       # fixes the scale
diagrams:
  - component: SolarThermalSystem
    at: [3.5, 10.3]                                # the trunk centre of this member in u; its whole tree moves with it
    trunklength: 3.0
    title_offset: [-0.15, 0.0]
    flows:
      - {link: SolarThermalSystem.SolarPumpHeatLoss, orientation: 1, pathlength: 0.6, label: [0.2, -0.3], ha: left}
  - component: Battery
    flows:
      - {link: Battery.StoredEnergyIncreaseInWattHour, pathlength: 0.6, lines: 2}
connectors:
  - {link: SolarThermalSystem.ElectricityConsumptionOutput, turns: ["+2.5", -9.8, 4.6], label: {segment: 2, side: right, at: 0.85}}
```

`at` may sit on any member of a tree, not only its root (the prototype pins the electricity meter, whose PV hangs
upstream of it): the tree is moved so that member's trunk centre lands there. A connector may carry
`parallel: true` (§6.7 step 1). A converted pin file keeps the prototype's lengths in u and draws at the fixed 30 px/u
(§6.5); the prototype drew each figure at its own pixel scale (near 30 px/u for eleven of the twelve, 63 px/u for the
air conditioner, whose texts are therefore twice as large against its bands and collide: 10 overlaps). A pin file
is valid at 30 px/u only; an old hand layout drawn at another scale needs its label shifts redone. In PR 4b's local
check the other eleven draw with 0 to 3 overlaps, each a title touching a neighbouring inside label or branch label,
because the title and its metric lines are one 11 pt block where the hand figures set the metric lines in 9 pt
(§6.8).

A pin replaces the engine's value for that one parameter before the first round of §6.6; everything else is
computed around it, and the validation of §6.12 applies unchanged (a pin that causes an overlap is a fault like
any other; the search may grow a pinned path length no further). A pin naming a component, flow or connector the
run does not have, or an orientation the frame rule of §6.4 forbids, is a fault. The applied pins are listed in
`overall.layout.json`, which holds every parameter a complete pin file needs except the connectors' turns (it
records their route points) and writes a cut or residual flow under its link key; turning it into a pin file is a
small converter left for after PR 5. The pin file is the escape hatch for the hard cases of §6.14; no energy-system file
in the repository ships one in v1.

### 6.11 Rendering, and the library's quirks

The renderer takes a `Layout`, builds the `Sankey` as §6.6 step 2 describes on an `Agg` figure with equal aspect,
draws the connectors, the residual styles (§6.3), the labels, the note blocks, the markers (⚠n in bold, red
`#c62828` for errors, orange `#d35400` for plausibility), the unmet stub (§8.1), the panel and the caption (§8.3),
then saves PNG, PDF and SVG (D18). Fonts: DejaVu Sans, matplotlib's bundled default, pinned in an `rc_context`, so
text widths are the same on every machine. Colours and titles come from one style table,
`hisim/postprocessing/energy_balance/figure/sankey_style.yaml` (the prototype's `_style.yaml`), keyed by component
class (`kinds`), with a title and a colour per role as the fallback (meters have no class in the balance report, and
a composed file names its meter `grid-ElectricityMeter`, so a key by instance name breaks); a class without an
entry and a role without one take the instance name. Two instances of one class get the instance name appended to
the title. Display names of flows are keyed `<class>.<port>` (`port_names`), never by the instance's port label,
with `SankeyDrawing.words` as the fallback; a storage change has an `{in, out}` pair. Class-keyed colours and names
are presentation, not a layout dependence; the layout engine reads roles only.

The library's behaviour the engine works around, each recorded by the prototype (its README and `draw_sankey.py`):

| Quirk (`matplotlib.sankey`, 3.10.9) | What the engine does |
|---|---|
| `finish()` fits the axes to the sub-diagrams' extents and resets the limits | the canvas limits are set after `finish()` (§6.9) |
| labels are centred on the arrow tips, with no collision handling | labels disabled; the engine's own labels and candidates (§6.8) |
| a connected sub-diagram ignores `rotation`: its frame comes from the connection | the axis is chosen through the connecting orientation (§6.4 rule 2); `rotation` is used for roots only |
| flows are stacked along a side by list position, in the four orders of §6.4 | the `flows` list is emitted from the wanted side orders |
| one connection per sub-diagram (`prior`/`connect`) | every other link is a connector (§6.7) |
| one fill per sub-diagram | a connector takes its source's colour; a residual branch gets a second patch (§6.3) |
| a flow below `tolerance` is not drawn and cannot connect; connected flows must match within `tolerance` | the minimum band is applied at both ends of a link (§6.5) |
| a sub-diagram whose flows do not sum to zero is drawn with a smoothed taper and an info log | accepted for inflated bands; a real imbalance is a residual flow (§6.3) |
| an unconnected sub-diagram is placed at the origin | moved by `at` after `finish()` (§6.6 step 2) |
| an input's tip is the notch's inner point | input labels moved out by the notch depth; a connector fills the notch (§6.7) |
| tail inputs and head outputs are justified: every one is drawn as long as the longest of its stack, whatever its path length (lateral flows keep theirs) | a stub on the tail or head is read back from the drawn tip, not from its path length; a connector's label line runs back along both stubs (`FigureTexts.with_stubs`), and a second link is never a stub under the tree connection (D24) |

### 6.12 Validation: what fails loudly

The engine collects faults and the renderer writes `overall.layout_error.json` (§4.2) when the list is not empty;
the acceptance tests (§9) fail on any fault. Faults:

- a drawn node without a role (cannot happen by construction; `CONVERTER` is the fallback) or a chain that finds
  no root;
- a flow of the view that no sub-diagram placed, or placed twice; a connection the library refuses (its `ValueError`
  on a mismatch or a flow below tolerance, caught and reported with the link);
- a node whose flows do not balance within `RESIDUAL_KWH` and that has no residual flow (cannot happen after
  §6.1 step 4; checked anyway);
- a child whose wanted axis is opposite to its parent's flow direction (§6.4 rule 2);
- findings left after `MAX_ROUNDS` of the search (§6.6), listed pair by pair; a connector without a route after
  `MAX_ROUTE_ROUNDS`; a corner without room for `MIN_RADIUS`;
- a pin naming an unknown element, an unknown key or a forbidden orientation;
- a metric whose inputs the run lacks is no fault: the line is left out (§7.1).

A fault never fails the run (D15): the run finishes, the fault is a logged error plus the file, and the tests
fail.

### 6.13 Code layout

```
hisim/postprocessing/energy_balance/
  statistics.py            LoopStatistics, StateStatistics (§5), each statistic a tested static function
  figure/
    __init__.py            draw_overall(inputs, pins) -> Layout; the entry the post-processing and the CLI call
    inputs.py              SankeyInputs.from_result_directory / from_run
    graph.py               the drawn graph (§6.1), roles and the main chain (§6.2), residuals (§6.3), the trees
    orientation.py         the role table and the context rules (§6.4), sides into orientations and list order
    parameters.py          Layout: the matplotlib.sankey parameters per sub-diagram and connector (§6.5), its JSON
    search.py              the placement search (§6.6): build, check, repair, bound
    connectors.py          stubs, shapes, the grid search, corners, the connector patches (§6.7)
    labels.py              text sizes from TextPath, candidates, scoring, OverlapCheck (§6.8)
    metrics.py             the metric formulas (§7.1) and the plausibility evaluation (§7.3)
    warnings.py            the warning entries, their order and numbering (§8)
    render.py              the Sankey build, finish, place_trees, the panel and the caption, PNG/PDF/SVG (§6.11)
    pins.py                the pin file (§6.10)
    sankey_style.yaml      colours, titles, display names, the panel rows
  plausibility_ranges.yaml the range table (§7.3)
```

Constants live on the classes they belong to (`system_docs/coding_style.md` §4): the layout constants of §6.5 to
§6.8 on `LayoutConstants`, a frozen data class the style file may override field by field. `hisim/cli.py` gains
the `energy-system sankey` command (§4.4). No module outside `figure/` imports `matplotlib.sankey` for this
feature.

### 6.14 Hard cases and what v1 does

| Case | v1 |
|---|---|
| several generators in parallel (hybrid heat pump plus boiler feeding one buffer) | the generator with the larger heat output into the buffer is on the chain; the other hangs from its own meter or, without one, below the chain as a further tree, its heat routed into the buffer's S side by a connector. Drawn by the generic rules, not exercised by any energy-system file today; a pin file is the escape hatch |
| very unequal sizes (a 16 kWh pump hairline beside a 21 MWh export band) | the minimum band (D10) and the label candidates; the prototype's robustness test broke labels, not routes, at +50 %, which the search now re-solves per run |
| storages whose change flips sign between runs | the side stays, the arrow direction and the display name flip (§6.4 rule 5) |
| systems without PV (`simple_air_conditioner_household_building_sizer`) | the meter is a root with its grid import at the tail (W); the role table's "from the source upstream when there is one" falls through |
| cooling-only systems | the chain runs meter → household → building; the cooler is the building's continuation child; the ambient sink is its head. In a January week the cooler has no flow and drops out (empty flows) |
| a cooling flow of a heat pump into a buffer (hisim-energy-sankeys-9uoo.17) | a negative loop heat turns the edge round: it is drawn as a `COOLING` input of the generator from the buffer, side S; untested until that bead lands |
| systems without a building (`electrolyzer_with_renewables`, `dynamic_components`) | the fallback chain of §6.2 from the largest meter; every node a `CONVERTER` or a meter; acceptance requires 0 faults, not a reviewed picture. The first try laid `electrolyzer_with_renewables` out cleanly (meter → electrolyzer → hydrogen meter, the wind transformer upstream of the meter); `dynamic_components` kept 3 label overlaps (§6.7 rule 7) and is the one open item of PR 5 |
| a storage without a discharge link in the run's period | it is not an `ELECTRIC_STORAGE` by §6.2 and falls to `CONVERTER`; the picture is still correct, the role only steers placement |
| systems with nothing declared (`simple_system_setup_one`, `_two`: verdict `nothing_declared`) | no figure; `export_sankeys` logs that there is nothing to draw; not a fault |
| several buildings, multi-family or district systems | out of scope for v1: the chain is the largest building's, the other buildings hang as children of their parents by the generic rules; no acceptance test; the pin file is the escape hatch. A rule set for siblings on one row is a later bead |
| the per-carrier views as matplotlib figures | not in v1 (D17) |

## 7. Metrics and plausibility

### 7.1 The metric definitions

Every metric is a static function of `Metrics` with direct tests; its inputs are the drawn flows of its node (in
kWh, by port name), the statistics of §5 and the KPIs. A metric whose input the run lacks (no kWp without a tag; no
occupants without the tag) is left out, never estimated. Notation: P(x) is the yearly kWh of the node's output port
x, C(x) of its input x; "heat out" is the sum of the node's heat productions to drawn nodes.

| Metric (unit) | Role | Formula | Shown |
|---|---|---|---|
| seasonal performance factor (–) | `GENERATOR` consuming `ELECTRICITY` and `AMBIENT_HEAT` | heat out / electricity in | note block: "SPF 4.14 · ambient 76 %" |
| ambient share (%) | the same | ambient heat in / heat out × 100 | with the SPF |
| generator efficiency (%) | `GENERATOR` consuming a fuel or `DISTRICT_HEAT`, or `ELECTRICITY` without `AMBIENT_HEAT` | heat out / fuel (or district heat, or electricity) in × 100 | note block; left out when it is 100.0 % by construction (the electric heater) |
| collector efficiency (%) | `SOLAR_COLLECTOR` | heat out / solar in × 100 | note block |
| battery round trip, AC to AC (%) | `ELECTRIC_STORAGE` | discharge to the meter / (charge from the meter + stored energy released − energy stored) × 100: the storage change counts, since over a January week it is not small (without it the first try read 194 %) | note block |
| equivalent full cycles (1/a) | `ELECTRIC_STORAGE`, `VEHICLE_STORAGE` | `statistics.states[..].equivalent_full_cycles` (see the scaling rule below) | note block |
| specific yield (kWh/kWp) | `PV` | `specific_yield_in_kilowatt_hour_per_kilowatt` of the `ELECTRIC_OUTPUT_POWER` tag | note block, with the kWp; a value, never judged (D20) |
| PV performance ratio (–) | `PV` | specific AC yield (kWh/kWp) / irradiation on the module plane (kWh/m², the PV's `SOLAR` consumption over the run divided by `module_area_in_square_metre` of its tag, §5.2), the standard test irradiance of 1 kW/m² making the ratio dimensionless: PR = E_AC · 1 kW/m² · A / (P_rated · E_sun). Period-independent, location-independent (D20) | judged against the table (§7.3); in the note block only when it has a warning |
| specific heat to the rooms (kWh/(m²a)) | `BUILDING` | heat received from the `DISTRIBUTION` or `GENERATOR` / KPI "Conditioned floor area" | note block |
| internal gains (Wh/(m²d)) | `HOUSEHOLD` | (appliance heat + body heat to the building) / floor area / run days × 1,000 | note block |
| hot water per person (kWh/(person·a)) | `HOT_WATER_TANK` | heat to the taps / `mean_occupants` | note block |
| energy efficiency ratio (–) | `COOLER` | heat removed / electricity in | note block |
| substation efficiency (%) | `GENERATOR` consuming `DISTRICT_HEAT` | as generator efficiency | note block |
| loop temperatures (°C) | every link that is a loop | `supply_mean_flow_weighted` / `return_mean_flow_weighted` · "max" `supply_p99_flow_weighted`; a fourth line "n h above the d °C design supply" when the loop has a limit, red when n > 0 | on the band |
| tank hours (h) | `HOT_WATER_TANK` with a `STORAGE_TEMPERATURE` tag | `hours_below_40_celsius`, `hours_at_or_above_60_celsius` | note block: "64 h < 40 °C · 3,498 h ≥ 60 °C" |
| comfort (°C, K·h) | `BUILDING` | KPIs "Minimum building indoor air temperature reached" and "Degree-hours … more than 1.0 K below its heating set temperature" | note block: "min. air 18.3 °C · 1.3 K·h below" |
| unmet hot water (kWh) | `HOT_WATER_TANK` | KPI "Unmet DHW heat demand" | a red stub (§8.1) |

**No extrapolation.** A specific value per year (kWh/kWp, kWh/(m²a), kWh/(person·a), cycles per year) is given
per year only when the run covers a year (`timesteps × seconds_per_timestep` ≥ 8,760 h within one step);
otherwise the value is stated for the run's period and its unit says so ("kWh/kWp over the week"), and no range
of §7.3 applies to it. The first try scaled a January week to a year and read 193 kWh/kWp and 233 kWh/(m²a), which
misleads. Ratios (SPF, efficiencies, round trip, performance ratio) are period-independent and are judged in every
run.

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
  - id: pv_performance_ratio_modern_systems
    metric: pv_performance_ratio
    applies_to: {role: PV}
    low: 0.75
    high: 0.88
    unit: "-"
    basis: "typical performance ratios of recent grid-connected residential systems; no source prepared yet"
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
and Bild 24 pp. 26–27; marked `estimate: true` until a person checks the figures), the PV performance ratio (D20:
the entry above, an estimate until a source for the performance ratio of recent residential systems is prepared
and checked; the context sentence names the run's irradiation on the module plane and its specific yield), the
building's specific heat (TABULA ±20 %, estimate), the internal gains (DIN V 18599-10, estimate until checked), the
hot water per person (estimate), the battery cycles (estimate), the comfort criterion (own criterion after
EN 16798-1 category II, estimate), and the tank thresholds 40 °C (estimate) and 60 °C (DVGW W 551, to be checked).
No PVGIS value and no per-location table enter the repository.

**How a range gets checked (D21).** For each entry, in the order Fraunhofer ISE SPF, HTW battery efficiencies,
DIN V 18599-10 internal gains, DVGW W 551, PV performance ratio: an agent fetches the original, quotes the exact
passage with table and page into the PR description beside the entry's `low`, `high` and `basis`; the owner reads
the quotes against the entry and writes the `checked` block (`by`, `on`, `against`) in that PR. An agent never
writes `checked`; the `base` test of the table cannot tell who wrote it, so the rule is a review rule, stated here
and in the table's header comment.

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
gets no marker. The heat pump's peak thermal power against its rating is shown in the panel only (D19).

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
| per `GENERATOR` with a `THERMAL_OUTPUT_POWER` tag | runtime (h), starts, full-load hours, max. thermal power / rated (kW, and % of rated; D19), max. on the coldest day (date, daily mean outdoor °C) |
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
(heat pump, gas, district heat, electric heating, air conditioner, no building); the tree with a tie; the
connecting orientation from axis and parent direction for every case of §6.4 rule 2, including the forbidden one;
each of the four side orders of §6.4 read back from the tips of a two-flow sub-diagram the library drew; the scale
series; the minimum band at both ends of a link; the trunk length from a title and a note block; each connector
shape and a grid route around one obstacle; the flattened head and notch; corner radius reduction and the fault;
label candidates and the scoring; the overlap checker on two boxes, a box over a band and two bands; one repair
move of each kind of §6.6 step 4 on a two-node layout; every metric with a known answer; the range table's rules on
good and bad entries; the warning order and numbering; the pin file's application and its faults; the caption.

### 9.2 Layout tests on synthetic systems (`base`)

No recorded run is committed (D22). `tests/sankey_test_systems.py` builds the `SankeyInputs` of small hand-made views,
one per case a rule needs: the heat-pump house (PV, meter, heat pump, buffer, building, household, a battery loop),
a boiler fed twice by its meter with an electricity meter apart (a further tree), a storage without a discharge, a
system without a building (a wind transformer upstream of the meter), two generators into one buffer, a solar
collector with its pump, an undeclared component with a gap, a car. The graph tests (PR 4a) check every case's roles,
chain, trees and residuals; the renderer tests (PR 4b) draw two of them, the heat-pump house and the boiler fed twice
(its second fuel link a `parallel` connector), from hand-written pin files with 0 faults and 0 overlaps; the layout tests
(PR 5) lay each out automatically and assert 0 faults and 0 overlaps. Each case lays out in well under a second.

### 9.3 Determinism (`base`)

The automatic layout of the synthetic heat-pump house is computed twice in one process and once in a subprocess
(`sys.executable -c`), and the three `overall.layout.json` serialisations are byte-identical. The test also perturbs
every flow by +25 % and asserts 0 faults and 0 overlaps.

### 9.4 End-to-end (`extendedbase`)

Covered by the golden jobs (§9.5), which run every twin with `PLOT_SANKEY`; no separate end-to-end test.

### 9.5 Every twin's figure in the golden jobs

The golden check (`.github/workflows/golden-check.yml`, `scripts/golden_check.py`) runs every twin and setup of
`scripts/golden_config.json` in the python, yaml and composed modes. Its week parameter sets (`one_week_60s`,
`july_week_60s`, `one_week_900s`) carry `PLOT_SANKEY` (from PR 5, when the automatic layout exists), so every twin's
figure is laid out in every pull request. A run that writes `energy_sankeys/overall.layout_error.json` is a pair of
status `layout_fault` in the golden report, with each fault as a line, and fails the job as a failing balance or a
KPI divergence does; the run itself finishes (D15), and the KPIs are compared as usual.

As built in PR 4b (`scripts/runner.py`, `scripts/golden_check.py`): `run_one` reads the file into
`RunResult.layout_faults` (`read_layout_faults`, one line per fault and per overlap left), which travels through the
child's result file; `_evaluate` gives a pair whose KPIs match and whose figure has faults the status `layout_fault`,
which fails the gate also for a nondeterministic parameter set (its KPI deviations alone stay advisory); a pair that
diverges stays `fail` and carries its layout faults beside the deviations. `report.json` holds them as
`layout_faults` per pair, `report.txt` lists them under the pair as `- [sankey] <fault>`, the summary line counts
"N with Sankey layout faults", and the console prints a `[SANKEY LAYOUT FAULT]` block per pair, as it prints
`[KPI DIVERGENCE]`.

### 9.6 The visual acceptance

0 overlaps is necessary, not sufficient. The twelve hand figures of the prototype (`figures/*.png`) are the visual
reference for the look: quarter-circle bends where a branch leaves or joins a trunk, notches on inputs, arrowheads
on outputs, pointed trunk ends on the tree connections, connectors that continue their stubs without a seam, and
hairlines that keep a visible bend. In PR 4b's review the twelve full-year runs are drawn once from pin files
converted locally from the prototype layouts and put beside the hand figures (the PNGs attached to the PR
description; nothing committed, D22); the owner reviews one figure per family again on PR 5 and the heat-pump figure
against the trial's on PR 6.

## 10. Pull requests

Stacked, each small, each closing its bead in its own branch (AGENTS.md). Bead ids are the children of
hisim-energy-sankeys-9uoo listed in §12. PRs 4a, 4b and 5 are cut from branch `sankey-mpl-auto-layout` (three tries:
an own band renderer, then `matplotlib.sankey` with the search, then the second link as its own arrow; D25), each a
branch on top of the previous and green on its own. Bead hisim-sankey-mpl-band-renderer-5a8c covers 4a and 4b (open in
4a, closed in 4b).

| PR | Bead | Scope | Done when |
|---|---|---|---|
| 1 | hisim-sankey-mpl-loop-statistics-3f1a (P1: every later PR reads it) | §5.1: `statistics.py` with `LoopStatistics`, `statistics.loops` in the report, schema 6, `FluidLoop.design_supply_temperature_in_celsius` set by `HeatDistribution` | `tests/test_energy_balance.py` pins the new keys on a hand-built table; the week of `test_energy_balance_storages.py` shows a loop entry with the floor heating's 35 °C limit; no result or KPI changes |
| 2 | hisim-sankey-mpl-state-statistics-7c2e | §5.2: `StatisticRole`, `StatisticTag` (with the module area), `add_output(statistic=)`, `StateStatistics`, the tags on the heat pump, boilers, substation, electric heater, PV, both batteries, both storages, the building, the weather and the occupancy; schema 7 | every statistic tested directly; the heat-pump twin's week writes every state of the table, the PV's performance ratio included; a wrong-unit tag is refused |
| 3 | hisim-sankey-mpl-plausibility-table-b4d9 | §7.3 the table with its first entries and the `base` test of its rules; §7.1 `metrics.py` with every formula (the first try's module, with the round trip and the no-extrapolation rule corrected and the performance ratio added) and the evaluation against the table (no drawing); the D21 workflow started for the Fraunhofer ISE range | every metric and rule tested; the table's estimates are marked; no `checked` written by an agent |
| 4a | hisim-sankey-mpl-band-renderer-5a8c (stays open) | §4.1, §6.1 to §6.3, §7.1's metrics of the first try: `inputs.py`, the drawn graph, roles, main chain, trees and residuals (`graph.py`), `metrics.py`, the style table with role fallbacks (`style.py`, `sankey_style.yaml`), the command frame `hisim energy-system sankey <result dir>` (prints the drawn graph); `tests/sankey_test_systems.py` | every static function tested directly on synthetic systems (§9.2); the roles, chains and trees of every synthetic case |
| 4b | hisim-sankey-mpl-band-renderer-5a8c (closed) | §4.2 to §4.4, §6.5's parameters as data, §6.7, §6.9 to §6.11: the `matplotlib.sankey` drawing from pin parameters (`parameters.py` data, `drawing.py`), the connectors (`connectors.py`), the labels and the overlap check (`labels.py`, `texts.py`), the files of §4.2 (`render.py`), complete pin files (`pins.py`), `PLOT_SANKEY` (D14) and its fault handling (D15), the redraw command with `--pins`; the golden check reads `overall.layout_error.json` (status `layout_fault`) | the synthetic systems draw from hand-written pin parameters with 0 overlaps; the twelve hand layouts drawn from local pin files beside the hand figures in the PR description (§9.6); semgrep clean |
| 5 | hisim-sankey-mpl-auto-layout-9e2b | §6.4, §6.6, §6.8, §6.12, §6.14: the orientation table with the frame rule and the side orders, the second link as its own arrow (D24), the library parameters (`ParameterBuilder`), the repair search, label spots and labels as routing obstacles (§6.7 rule 7), the automatic `draw_overall`; `PLOT_SANKEY` in the golden check's week parameter sets (§9.5) | §9.2 and §9.3 on the synthetic systems; every twin laid out with 0 faults and 0 overlaps in the golden jobs (§9.5); the owner reviews one figure per family |
| 6 | hisim-sankey-mpl-warnings-panel-c6f3 | §8: markers, the warnings box, the data panel, the unmet stub, loop temperatures on the bands, the caption's second line | the heat-pump recording reproduces the trial's nine warnings in its order (the PV's entry now the performance ratio); 0 overlaps on all twelve with the notes and the panel; the owner reviews the heat-pump figure against the trial's |
| 7 | hisim-sankey-mpl-report-integration-2d7a | `PLOT_SANKEY` in the full-year golden parameter set (§9.5), no PDF report (D23), `energy_systems/README.md` and `simulation_parameters/README.md` updated, the D4 amendment and status lines written into `roadmap/complete_energy_sankeys_spec.md`, beads hisim-energy-sankeys-9uoo.19 and hisim-4hed closed with a note, the owner's decision on the plotly `overall.html` (D13) taken and applied | `golden-year.yml` renders every twin with 0 faults |

Order: 1 before 2 (schema sequence); 3 and 4a are independent of each other and of 2; 4b needs 4a; 5 needs 4b; 6
needs 1, 2, 3 and 5; 7 needs 6. Nothing here changes a simulation result or a KPI; the goldens stay.

## 11. Owner questions

All decided on 2026-11-08 and recorded as D13 to D21: the plotly HTML stays beside the figure, PR 7 decides on
its `overall.html` (D13); `PLOT_SANKEY` (3) gets this meaning (D14); a layout fault never fails the run (D15); the
pin file lives beside the energy-system file and the CLI takes `--pins` (D16); `overall` only (D17); PNG, PDF and
SVG (D18); the heat pump's peak over rating is a panel row (D19); the PV check is a performance ratio on the run's
own irradiation (D20); an agent prepares each source, the owner marks it checked, in the stated order (D21). No
question is open.

## 12. Beads

Epic hisim-energy-sankeys-9uoo. The seven beads below are new (kind `feature`, area `postprocessing`); the two
existing beads close with PR 7.

| Bead | Content | PR |
|---|---|---|
| hisim-sankey-mpl-loop-statistics-3f1a (P1) | §5.1, schema 6 | 1 |
| hisim-sankey-mpl-state-statistics-7c2e (P2) | §5.2, schema 7 | 2 |
| hisim-sankey-mpl-plausibility-table-b4d9 (P2) | §7 | 3 |
| hisim-sankey-mpl-band-renderer-5a8c (P2) | §6.1–§6.3, §6.5, §6.7, §6.11, §6.13, §4.2–§4.4; the renderer on `matplotlib.sankey` (D2) | 4 |
| hisim-sankey-mpl-auto-layout-9e2b (P2) | §6.4, §6.6, §6.8, §6.10, §6.12, §6.14, §9.2–§9.3 | 5 |
| hisim-sankey-mpl-warnings-panel-c6f3 (P2) | §8 | 6 |
| hisim-sankey-mpl-report-integration-2d7a (P2) | §4.3's PDF report, §9.5, the amendment of the complete Sankey spec, D13 | 7 |
| hisim-energy-sankeys-9uoo.19 (P3, existing) | the arrow renderer of D4 — superseded by this spec | closes with 7 |
| hisim-4hed (P3, existing) | static images for the PDF report | closes with 7 |
| hisim-sankey-layout-48b8 (P2, existing) | plotly node columns — kept or superseded by the PR 7 decision of D13 | – |
