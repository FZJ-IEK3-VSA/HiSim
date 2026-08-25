# Energy system config file — requirements

**Status:** SUPERSEDED 2026-08-25 by `roadmap/declarative_energy_systems/epic.md` and its phase documents (`p1_sizing_kernel_requirements.md`, `p2_file_format_requirements.md`), restructured per `roadmap/templates/requirements_template.md`. Kept for the discussion history; R-numbering is preserved in the children.
no mechanism. Every requirement is tagged **[given]** (stated by the owner) or
**[proposed]** (derived by the assistant from the v2 spike, the design-B sizing work
and the existing consumers — to be confirmed, cut or reworded). Design work
(`roadmap/system_tree_mockup.yaml`, `hisim/config/engine.py` scopes) is paused until
this list is agreed.

**Why:** the design discussion spiralled (systems, subsystems, repeated instances,
per-unit facts) before anyone had written down what the file must actually do. This
document is the yardstick every design proposal is checked against.

---

## 1. Purpose

One file describes one simulated household/building system completely enough that
HiSim can build and run it without a Python setup function, and a reader can
understand what is simulated without opening component code.

## 2. Core functional requirements

### R1 — Define the components  [given]
The file names every component instance and its type (class).

- R1.1 [proposed] A component's *name* is chosen by the author and is the identity used
  everywhere else in the file (connections, sizing references) and in results.
- R1.2 [proposed] The same class may appear several times (two boilers, three
  occupancy profiles) with different names and configs.
- R1.3 [decided 2026-08-25] The component's name is the mapping key of its entry;
  presets do not carry a `component_id` (the executor supplies it from the key).
  Names are global even inside groups; repeated structures use a prefix by
  convention (`apt1_hds`, `apt2_hds`) until the R5 template layer exists.

### R2 — Define the connections between components  [given]
The file states which component outputs feed which component inputs.

[decided 2026-08-25, from the mockups] Connections are declared **at the consuming
component**, under `inputs`, never at the source. Reasons: one direction throughout
the file (sizing sources are consumer-side too, R4.3); it mirrors the code
(`connect_input(target, …)` and default connections are target-centric, so R9
recording is a plain dump); aggregators list their own feeds with tags/weights; a
group's incoming wiring sits inside the group; placement is canonical, so generated
and hand-written files agree. Shapes: a bare name = the source's default connections
into this component; `{input, from: source.Output}` = one explicit wire;
`{from, tags, weight, dispatch}` = an aggregator feed. A source nobody consumes is
legal but listed as a warning in the resolution report.

- R2.1 [proposed] The common case ("wire these two the standard way") is one short
  entry; the executor applies the target's declared default connections.
- R2.2 [proposed] Any single wire can be spelled out explicitly (output → input).
- R2.3 [proposed] Aggregators (EMS, electricity meter, building heat sources) with a
  variable number of participants are expressible, including the per-participant data
  they need today (tags, dispatch weight, back-channel).
- R2.4 [proposed] A connection the executor cannot make (unknown port, ambiguous
  default, mixed spelling) is a hard error naming both ends. No silent skipping.

### R3 — Configure the components  [given]
The file sets each component's parameters.

- R3.1 [proposed] A component can be configured by naming a **preset** (a
  class-provided named default) plus sparse overrides, so the common file is short and
  the full parameter dump is never required.
- R3.2 [proposed] Every parameter of every component can nevertheless be set
  explicitly in the file; presets are a shortcut, not a restriction.
- R3.3 [proposed] A parameter may be left to be **sized** ("compute this from the
  system", today `AUTO`) instead of given as a number.
- R3.4 [proposed] Preset names and field names are a stable wire format; renaming one
  is a breaking change and is treated as such.

### R4 — Define which component inherits which facts from which other component  [given]
Sized parameters (R3.3) depend on facts about other components (building heating
load, boiler power band, PV peak power). The file must make it unambiguous **which**
component's fact a sized parameter uses.

**Governing principle (also behind R2.4 and R7):** unambiguous → nothing to write;
anything else → written out; never inferred.

- R4.1 [proposed] Ambiguity is never resolved by guessing: with two candidates the
  executor errors and names them.
- R4.2 [proposed] The unambiguous case (one building, one boiler, one controller)
  requires **no** extra declaration; the author only writes something where the
  wiring alone does not decide.
- R4.3 [decided 2026-08-25] One rule, the same for building facts and for
  sibling-component facts:

  > Every sizing fact a component declares is addressable as `<component-name>.<fact>`.
  > A sized field reads its facts by bare name; a bare name resolves only if exactly one
  > component in the (expanded) file provides it — otherwise the consumer must map it
  > explicitly (`sizing_sources: {<fact>: <component-name>.<fact>}`) and the executor
  > errors listing the candidates. Realized records always write the mapping.

  Consequences, decided with it:
  - Providers never declare anything in the file; a component's facts are its class's
    static contributions and are always addressable qualified. Only consumers write.
  - Conflict is per fact and per consumer, not per component: a boiler and a heat pump
    conflict only on the fact names they share, and only for a consumer that reads
    one. Two boilers nobody sizes from are not an error.
  - The bare-name shortcut is a hand-authoring convenience; anything generated (the
    realized record, template expansions) writes fully qualified sources, so a
    generated file never depends on how many components of a class exist.
  - The explicit mapping is a rename table on the consumer: a law that reads
    `heating_load_in_watt` can be pointed at `building_b.heating_load_in_watt`
    without touching the class (keeps N1/N2).
  - Fact names must be specific enough that unrelated classes do not collide by
    accident (`pv_peak_power_in_watt`, not `power_in_watt`); two classes share a fact
    name only when they are interchangeable providers (boiler / heat pump for
    `maximal_thermal_power_in_watt`). A contract test enforces this.
  - This replaces the GLOBAL / CONNECTED-adjacent / CONNECTED-pool scopes and the
    file-level `sizing_facts` pre-seed of the current `hisim/config/engine.py`.
- R4.4 [decided 2026-08-25, revised same day] A sized field may depend on *several*
  components' facts (the EMS and battery on every PV system, a meter on every heat
  generator). The file expresses this with **references only, no math**: a source
  entry is one qualified reference or an explicit list of them. What to do with several values — sum,
  max, something non-linear — is the **consuming class's** business, declared in its
  law:
  - The class declares the cardinality of each fact it reads (one vs. many). A list
    given to a one-fact law is an error; a single reference to a many-fact law is a
    one-element list. R13 surfaces the cardinality per field.
  - With several providers and no mapping, a many-fact law is **not** silently given
    all of them — that is a guess (a battery belonging to one of two PV systems). The
    author writes the list. No `all` keyword or similar shorthand [decided 2026-08-25]:
    anything not unambiguous is spelled out; the cost of adding one line when a PV is
    added is preferred over a shorthand whose meaning changes with the file's contents.
    The error message for the missing mapping prints the full candidate list ready to
    paste.
  - Mixed providers (boiler + heat pump on `maximal_thermal_power_in_watt`) are simply
    a two-element list for a consumer that wants both.
  - Templates: a wildcard reference (`apartments[*].boiler.…`) expands to the list
    (R5). The runtime never sees wildcards.
  - A consumer needing a combination its law does not implement needs a class change,
    reviewed as code — consistent with N1.
  - Provenance: `SizingRecordEntry.inputs` records every `(qualified fact, value)`
    operand; the engine treats a list source as a node reading each operand.
  - Superseded the same day: a structured expression object in the file
    (`{sum: [...]}`, closed operator algebra). Rejected because it makes the law
    algebra wire format and puts aggregation semantics with the author instead of
    the class.
- R4.6 [decided 2026-08-25] **No privileged root component.** Any component may both
  consume facts and provide them (the Building consumes the weather's design
  temperature and provides the heating load; a generator consumes the load and
  provides its power band). The only structural constraint is acyclicity. Provider-side
  fact computation (TABULA lookup, deriving a design temperature from the weather
  year) happens once at **build time** inside the provider, never at parse time (N4)
  and never inside a consumer's law.
- R4.5 [proposed] The sizing law itself (the formula) is **not** in the file. It lives
  once in the component class; the file only says *which* source and *whether* to
  size. (Decided in design B; the file never contains formulas.)

## 3. Further functional requirements  [all proposed]

### R5 — Repetition
Several structurally identical parts (N apartments with their own boiler and
occupancy) must be writable without copying the block N times, **and** each instance
must be able to differ (its own floor area, its own occupancy profile, one pellet boiler
among gas boilers). A bare count without per-instance data is not sufficient.

[decided 2026-08-25] Repetition is a **preprocessor**, not a runtime scope: a template
expands into a flat file, and the expander rewrites the template's relative sizing
references (`boiler.maximal_thermal_power_in_watt`) into qualified ones
(`eg_links.boiler.maximal_thermal_power_in_watt`). The R4.3 rule therefore operates on
one flat namespace only; the realized record is the expanded file.

### R6 — Simulation parameters are separate
Time range, resolution and post-processing options stay in a separate file
(`*.simulation.json` today), so one system runs under many settings and vice versa.

### R7 — Hard errors, no repair
Every inconsistency (unknown component, unknown field, unknown preset, unprovided
fact, cycle, duplicate key) is a hard error at load time, before anything is built.
The file is never silently corrected, defaulted or partially executed.

### R8 — Re-executability and provenance
A run writes a **realized** copy of its input in the same format in which every preset
is expanded and every sized value is a number; re-running it reproduces the run
bit-for-bit and sizes nothing. Provenance (which preset, which law, which source fact
with which value) is recorded alongside, machine-readable, and rendered as comments
in the realized file. (Decided as A3/A4 on `json_v2`.)

### R9 — Generatable from Python
An existing Python `setup_function` can be recorded into a file of this format
losslessly, so the ~50 existing setups migrate mechanically and the Python and file
paths stay structurally identical rather than parity-tested into agreement.

### R10 — Machine-writable and machine-readable  [proposed; consumers fixed by Q5]
The format is the exchange interface for three real producers/consumers (Q5). What
each needs, verified against the code on 2026-08-25:

- **RenoVisor backend** (`hisim/renovisor/`) and **building sizer**
  (`hisim/building_sizer_utils/interface_configs/`): both work one level *above*
  components — a `ModularHouseholdConfig` (heating-system enum, PV/battery/EV flags
  and sizes, building archetype) — and today pick one of the Python setups by heating
  system (`renovisor/mapping.py::_select_setup`). They therefore need a
  **programmatic way to produce a complete energy-system file from a handful of knobs**
  without hand-listing 15 components and 30 connections. See Q6 for where that layer
  lives.
- **HPC harness** (`hpc_harnes_spec.md` on `json_v2`): payload = energy system + simulation
  parameters as *small JSON over the network*; the worker runs it via
  `run_one.run_single`. The energy system must be **self-contained and small** (no side
  files besides `${inputs}` references, R12), loadable from a string/dict as well as a
  path, and cheap to validate at every job start (N4). The harness itself never
  interprets the content.
- General: regular structure, no comment-only information (R8), no order dependence,
  no duplicate-key ambiguity — everything a program emits round-trips through
  `load → dump` unchanged.

### R11 — Readable
A person can read a complete household file (~15 components) top to bottom and
understand the system; a reviewer can diff two files meaningfully. This is what
motivated presets, grouped connections and YAML on `json_v2`, and the subsystem idea.

[decided 2026-08-25] Canonical style, for generated files too: every component is
self-contained (`class`, `preset`, `config`, `inputs`, `sizing_sources`, in that
order); lists are block lists, one item per line (adding an input is a one-line
diff); no central connections block. Measured on the mockups: 8 components ≈ 70
lines, 19 components ≈ 250 lines, 35 components ≈ 430 lines.

### R12 — Resource references
File paths (weather data, load profiles, caches) are written as symbolic references
(`${inputs}/...`), never as absolute paths, so files are portable across machines.

### R13 — Discoverability of what can be written  [proposed]
An author must be able to find out, without reading component source, for every
component class: (a) its settable fields with types and defaults, (b) its preset names
and what each preset pins or leaves sized, (c) which of its fields are sizable and
which facts their laws read, (d) which facts it provides for others. All four come
from the class alone (N1), so they are generated, never hand-maintained, and surfaced
in at least these places:
- a CLI (`hisim config describe <class>` / `hisim config facts <energy_system>`) that lists
  the above, and for an energy-system file the fact table: every provided fact with its
  provider(s), every consumer with its resolved or missing source;
- a **JSON Schema** export per energy system version with per-class field names, preset
  names as enums and provided fact names, so an editor with a YAML language server
  autocompletes and red-underlines while typing — this is the main salience
  mechanism for hand authors;
- generated reference documentation (Sphinx) from the same introspection;
- error messages that list the valid alternatives (candidate providers, known preset
  names, known fields) rather than only the offending token;
- the realized record's provenance comments (R8), which show after the fact which
  source every sized value used.

### R14 — Component groups with an on/off flag  [decided 2026-08-25]
Components and their connections may be gathered into a named **group** carrying an
`enabled` flag. Groups serve two purposes with one construct: readability (R11 — "the
electricity system", "the DHW chain") and presence toggles (R10 — PV, battery+EMS, EV,
solar thermal on or off) without combinatorial base files.

- **Groups are not scopes.** Component names stay globally unique; R4.3 is unaffected.
  A group is a set with a flag. v1: flat groups only (no nesting, a component in at
  most one group); ungrouped components are always on.
- **One rule for "off":** a disabled group removes its components, every connection
  touching them from either side, and every sizing reference into them. A *scalar*
  reference left dangling is a hard error (a battery sized from a disabled PV); a
  *list* reference shrinks, and the shrink is recorded in the resolution report and
  the realized record's comments. This is the only sanctioned dropping in the
  executor, and it is author-requested by the flag, so R7 holds.
- **Not a replacement for base files.** Orthogonal add-ons are groups; mutually
  exclusive alternatives (the heating system) remain one base file each — an
  "exactly one of these groups" constraint would be invisible in the data, and two
  heating groups on is even a valid hybrid system.
- **No inter-group `requires`.** A component left with an unconnected mandatory input
  because its counterpart's group is off fails at build, naming the disabled group.
  Add dependency machinery only if those errors prove confusing.
- **Disabled content is not part of the realized system** [decided 2026-08-25,
  overriding an earlier draft]. The realized record contains only what ran; a disabled
  group leaves no trace in it (enabled groups stay as grouping). That the group was off
  is recorded in the audit companion (R8), whose job is provenance.
- **Uniqueness is evaluated over the enabled set** [decided 2026-08-25, O3]: a
  disabled provider never forces a `sizing_sources` line. Consistent with the
  identity test — removing providers cannot create ambiguity — and with dropping
  (not erroring on) `inputs` entries that point into a disabled group.
- **Groups cannot express "enabled iff another group is off"** (O2: metering via the
  EMS vs. direct feeds to the meter). Such variants are separate base files; the
  identity test fails loudly on the double-count otherwise.
- **Identity test:** for any file and any group X, `realize(file with X disabled)` must
  equal `realize(file with X and every reference to it deleted by hand)`, bit-for-bit.
  This one property verifies the entire "off" rule — components, touching
  connections, list-source shrink — and is a required test of the implementation.

## 4. Non-functional requirements  [proposed]

- N1 Adding a new component requires **no** change to the file format, the executor
  or any central registry — only the component's own class (config, presets, laws,
  default connections).
- N2 Converting an existing component to the format is local to its module and
  mechanical (this is the "50 components" cost; a design that needs per-component
  special cases in the executor fails).
- N3 The format is versioned; a file states its schema version and an old version is
  rejected or migrated explicitly, never guessed at.
- N4 Loading and validating a file is fast enough to run on every HPC job start
  (thousands of runs) — no heavy lookups (TABULA, weather) at parse time; those
  happen once at build time.

## 5. Explicit non-goals (for this format)

- Not a modeling language: no expressions, no math, no conditionals in the file
  (R4.4, R4.5). The file contains values and references, nothing else. Any computation
  lives in a component class or the building model.
- Not a replacement for Python setups as a *development* tool; Python stays for
  experiments, the file is the production and exchange format.
- Not a results/KPI format; outputs are separate.

## 6. Open questions to settle before designing

- Q1 [answered 2026-08-25] Per-apartment heating physics **is** a design goal, but not
  an immediate one: it first requires upgrading the `Building` component (one thermal
  zone today). Consequence for the format: nothing may be designed that *precludes*
  N apartments with individual heat generators later (R5 template layer, qualified
  fact names, list sources), but the first version needs no per-unit building facts
  and no repeated-subsystem support beyond what R4.3/R4.4 already give.
- Q2 [answered 2026-08-25] Neither: the file only lists *which* facts feed a field
  (one or an explicit list); the consuming class aggregates — see R4.4. Rejected:
  helper components whose only job is to sum (clutter complex setups), string
  expressions (parser, eval), and structured expression objects (algebra becomes wire
  format, semantics move to the author).
- Q3 [answered 2026-08-25] Structure never changes the sizing semantics, which are
  flat (R4.3). Two structural constructs exist: **groups** (R14) — sets with an on/off
  flag, in the runtime format, for readability and presence toggles — and
  **templates** (R5) — a preprocessor for repetition, not needed in v1 (Q1).
- Q4 [answered 2026-08-25] Dynamic connections stay author-visible and explicit as in
  the v2 spike (tags, weight, dispatch per entry). Reducing their verbosity is not a
  goal of this redesign.
- Q5 [answered 2026-08-25] The legacy v1 format does **not** constrain the design —
  this is a redesign. The consumers that must be able to produce and/or consume the
  format are: the **building sizer**, the **HPC harness**, and the **RenoVisor
  backend** (`hisim/renovisor/`). Any other consumer (webtool, template creator) is
  not a design constraint until it exists.
- Q6 [answered 2026-08-25] **No system-level template layer in the format.**
  `ModularHouseholdConfig` was a workaround for the missing energy-system files and is
  replaced by using them directly. Its knobs map onto three things the format already
  has or must have:
  1. *Field overrides* (building code, dwellings, PV azimuth/tilt, weather location,
     LPG households, heat distribution type) → sparse `config:` overrides on named
     components (R3.1). Manual sizes (`norm_heating_load_in_kilowatt`,
     `pv_rooftop_capacity_in_kilowatt`) become `AUTO` or an override.
  2. *Topology choice* (`heating_system`) → one checked-in base energy-system file per
     heating system in `system_setups/`, i.e. what the Python setups become under R9.
  3. *Presence toggles* (`use_battery_and_ems`, EV, solar thermal) → **component
     groups with an `enabled` flag** (R14); the consumer flips a boolean in the data.
     Rejected: combinatorial base files; an overlay/patch feature; a Python editing
     API as the primary mechanism (superseded the same day by R14 — what remains is
     "load, set a field, dump").
  RenoVisor's `mapping.py` shrinks to "pick base file, apply overrides, toggle
  components". Its post-processing/KPI selection moves to the simulation-parameters
  file (R6) — two files, the correct split.

## 6b. Findings from the mockups (2026-08-25)

Files: `roadmap/declarative_energy_systems/energy_system_mockup_minimal.yaml` (gas boiler, 8 components, zero sizing
text), `roadmap/declarative_energy_systems/energy_system_mockup.yaml` (heat pump + 2 PV + battery/EMS + backup heater,
groups, many-facts), `roadmap/declarative_energy_systems/energy_system_mockup_mfh.yaml` (3 apartments, central heat
pump and PV, 35 components). Resolved there: O1 (placement of connections → R2),
O3 (uniqueness scope → R14), O2 (exclusive groups → R14). Still open, none of them
format questions:

- **O5** Single-zone `Building`: per-apartment sizes (HDS floor area, DHW volume, DHW
  heater power) must be written explicitly today — 4 lines per apartment in the MFH
  mockup. Per-unit building facts (Q1) remove them without a format change.
- **O6** The `Building` has one occupancy input; several apartments need an
  aggregating input on it (component work).
- **O7** A central heat pump SH controller with three HDS controllers — a modeling
  question the format must not paper over.
- **O8** The three apartment groups are 95 % identical: the concrete test case for
  the R5 template layer (would roughly halve the 430 lines). Not v1.
- **O4** With consumer-side wiring an unconsumed source is silent → report warning
  (folded into R2).

## 6c. Sizing-fact inventory (2026-08-25)

`roadmap/declarative_energy_systems/sizing_fact_inventory.md` lists every sized field of every component with the
facts it reads and the arithmetic used. Headline findings: 7 primary facts (all from
the Building, one from Weather); ~35 sized fields, ~20 of them plain copies; the only
non-trivial math is one ratio, one max-with-factor, one step table and two catalogue
lookups — all opaque function laws; the deepest dependency chain is 3 levels, acyclic;
**no reader consumes several providers today**. Consequences: the scalar law algebra
on `config_presets` already suffices; the many-reader of R4.4 is specified but should be
implemented with its first real consumer; the fixed-point engine can be a topological
sort with a cycle check.

## 7. Checklist for any design proposal

A proposal is discussed only if it states, per requirement R1–R12 / N1–N4, how it
meets it — and for R4.3 quotes the one-sentence rule.
