# Config presets — team review agenda (phase 2 of the landing plan)

**Purpose:** settle every design question that shapes the per-class conversion work,
*before* the first repetition. Phases 3–8 of `roadmap/config_presets_plan.md` are gated
on this review: every B item below multiplies by ~60 remaining config classes if
decided late, and preset names become wire format the moment the v2 executor accepts
preset references.
**Date prepared:** 2026-08-23. Questions are written out in full so the meeting needs
no other document. Question ids continue the spike-era numbering
(`roadmap/design_review_questions.md` on branch `json_v2`), so earlier notes stay
citable; everything already decided there is only summarized at the end.

**Review material:**
- PR #582 — the `hisim/config/` layering base (mechanical move + import sweep).
- PR #586 — the design-B foundation: sizing machinery (`laws.py`, `context.py`,
  `sizing.py`, `presets.py`, `contributions.py`, `engine.py`, `report.py`), the central
  AUTO check, the building's fact contributions, and three pilot conversions
  (boiler, heat distribution, EMS) with their legacy factories deleted.
- Exhibit A below — a real `ResolutionReport` of the heat pump building-sizer chain,
  as evidence of what the machinery records.
- Background only: the template/realized/audit artifact triple on `json_v2`
  (already reviewed for the A-decisions).

**Recording rule:** each question ends with a **Decision:** line. The session that
implements a decision fills the line in and updates the plan in the same commit.

---

## Exhibit A — what a sizing run looks like now

The heat pump building-sizer chain (building → HDS controller → HDS), resolved
seedlessly by the fact engine; `engine.report.render()` verbatim:

```
sweep 1: resolved L2EMSElectricityController, Building, HeatDistributionController
  contributed heating_load_in_watt=7780.752200000001 by 'Building' (GLOBAL)
  contributed number_of_apartments=1 by 'Building' (GLOBAL)
  contributed conditioned_floor_area_in_m2=121.2 by 'Building' (GLOBAL)
  contributed heating_reference_temperature_in_celsius=-7.0 by 'Building' (GLOBAL)
  contributed water_mass_flow_rate_in_kg_per_second=0.2659176349965824 by 'HeatDistributionController' (GLOBAL)
  contributed heat_distribution_system_type=FLOORHEATING by 'HeatDistributionController' (GLOBAL)
  waiting: 'HeatDistributionSystem' on ['heat_distribution_system_type', 'water_mass_flow_rate_in_kg_per_second', 'conditioned_floor_area_in_m2']
sweep 2: resolved HeatDistributionSystem
lookup: 'HeatDistributionSystem' read heat_distribution_system_type=FLOORHEATING from 'HeatDistributionController' [GLOBAL]
lookup: 'HeatDistributionSystem' read water_mass_flow_rate_in_kg_per_second=0.2659176349965824 from 'HeatDistributionController' [GLOBAL]
lookup: 'HeatDistributionSystem' read conditioned_floor_area_in_m2=121.2 from 'Building' [GLOBAL]
```

The per-field `sizing_record` additionally shows raw input → law → value
(e.g. `0.2659…` read, `.rounded(2)`, `0.27` written). Sized values are verified
byte-identical to the pre-preset factories (config-dump diff against the old commit,
plus the golden gates).

---

## B. Machinery/API questions (settle before anything repeats per class)

### B6. May a sizing law read another field of the same config? (the PV share case)

A sizing law reads only the `SizingContext` — facts about the *surrounding system*.
But `PVSystemConfig.power_in_watt`'s law needs `share_of_maximum_pv_potential`, a field
of the *same config*. The spike (on `json_v2`) works around it with a per-preset law
builder (`scaled_power_law(share, module, db)`) that setups assign as the field value,
while the class-level law assumes share 1.0.

**The sharp edge this leaves:** a template that overrides
`share_of_maximum_pv_potential: 0.5` but leaves `power_in_watt: "AUTO"` silently sizes
at share 1.0 — field and law are not coupled, and nothing warns. This is the one known
trap in the format as it stands.

**Options:**

- **(a1) Restricted own-field reads — plain (non-sizable) siblings only.**
  `sized_field(..., reads_own=("share_of_maximum_pv_potential",))`; the engine feeds
  the sibling values to the law. No cycles by construction (a plain field is never
  law-computed, so nothing that reads it can loop back), no intra-config ordering
  (plain fields hold their final values before resolution starts), and naming a
  *sizable* field in `reads_own` is rejected at import. Fully covers the PV case —
  the trap disappears, because an overridden share is simply what the law reads.
  Does **not** cover B7.
- **(a2) Full own-field reads via the existing fixed point, at field granularity.**
  The engine's ordering was never a topological sort — it is fixed-point iteration
  (resolve whatever has its inputs available, sweep again, diagnose no-progress).
  It stops at the config boundary only because `resolve_config` is atomic per config.
  Lowering the unit of progress from config to *field* extends the same loop inward:
  a field resolves in whatever sweep its inputs (context facts + sibling fields,
  sizable or not) are concrete; an intra-config cycle is rejectable at import time
  (reads are declared statically), stronger than the runtime deadlock diagnosis the
  cross-component case gets. Costs, contained in `sizing.py`/`engine.py`: per-field
  partial-resolution state, a readiness rule for contributions, a `Field("…")` term
  in the law language. Payoff: **solves B6 and B7 with one uniform mechanism** — the
  pellet minimum becomes `1/12 * Field("maximal_thermal_power_in_watt")`.
- **(b) Promote the share to a `SizingContext` fact**, pre-seeded per file. No new
  mechanism, but the share is a component property, not a system fact — and with two
  PV systems in one scenario a global fact breaks down entirely.

**Recommendation on file:** (a2) — the original complexity objection assumed a new
ordering layer, but the engine already *is* the ordering layer; (a2) refines its grain.
(a1) remains the cheap fallback if the rework is judged not worth two use cases.

**Decision:** _open_

### B7. Cross-field laws — the pellet 1/12 case (follows B6)

The pellet/wood-chip boilers' *minimum* power is a twelfth of their *sized maximum* —
a dependency on the resolved value of a sizable sibling. As built today
(`generic_boiler.py`), the preset assigns `law(1/12 * MAXIMAL_POWER_LAW)` as the field
value: the same law object is referenced, so changing the shared `ClassVar` law updates
both fields, and the residual risk (rebinding one law without its companion) is a
review-catchable mistake, not a silent runtime one.

**Decision follows B6:** under (a2) this becomes
`1/12 * Field("maximal_thermal_power_in_watt")` — single-source, no recomputation;
under (a1)/(b), law composition stays the blessed idiom and gets one normative
sentence in the machinery docs.

**Decision:** _open (bound to B6)_

### B8. Typed preset access — `Catalog` and mypy

`Catalog.__getattr__` returns `Any`, so every helper returning a preset needs a manual
annotation and preset-name typos get no static check. The sweep would replicate that
~70×. One obstacle found during the rebuild (`roadmap/random_findings.md`): the natural
spelling `presets: ClassVar[Catalog["GenericBoilerConfig"]]` is rejected — a `ClassVar`
may not contain a type variable — so plain generics don't just drop in. Candidate
mechanisms for phase 3: a `__class_getitem__`-based `Catalog[SomeConfig]` declared as a
plain class attribute (not `ClassVar`), or a descriptor/protocol spelling. To decide
*here* is only: **must preset access be statically typed before the sweep** (and typos
caught by mypy), with the mechanism left to phase 3?

**Recommendation on file:** yes — decide the requirement now, prototype the mechanism
first thing in phase 3, and only then convert further classes.

**Decision:** _open_

### B9. Which factories become presets at all — the Weather lesson

Not every default factory should become a preset. `WeatherConfig`'s factory is a
*lookup* over several dozen `LocationEnum` members — an open identifier space, not a
variant set; converting it exhaustively would mint dozens of meaningless presets. The
spike's pattern: presets only for the cases the repo actually uses (`aachen`,
`seville`), plus a plain constructor for the general case (`for_location(...)`, named
so no default-discovery heuristic picks it up). Same logic applies to TABULA building
codes and LPG household definitions.

**To ratify as the normative rule:** *identifier-parameterized lookups keep a named
constructor; presets are named variant sets.*

**Decision:** _open_

### B10. Author-declared source notes on config values

The machinery already records everything it *knows*: which preset built an entry, which
law sized a field from which facts, what was overridden. What it cannot know is why a
hardcoded number is what it is — where the PV rule's rooftop share comes from, which
standard a buffer rule follows, which datasheet an efficiency curve was read from. If
such notes should reach the realized record and audit artifact, they must be data:
e.g. `sized_field(..., note="VDI 4645")` or a per-preset note.

**The question:** do we want the convention at all, and in which shape (per-field,
per-preset, both)? It is small machinery but a repo-wide authoring convention — the
sweep would apply it ~70 times, and half-maintained citations are worse than none
(a wrong "VDI 4645" misleads with authority).

**Recommendation on file:** decide the shape now, make it strictly optional, populate
only where a source is actually known — never invent citations to fill the field.

**Decision:** _open_

### B11 (new). Scope rule for contributed sizing facts — the HDS controller case

Surfaced by the Exhibit-A audit: `HeatDistributionControllerConfig` contributes its two
facts (`water_mass_flow_rate…`, `heat_distribution_system_type`) with **GLOBAL** scope,
while the boiler contributes its power band with **CONNECTED** scope. Consequence:
two heat-distribution loops in one scenario would hard-error on the double global
contribution instead of resolving per loop — loud, not wrong, and defensible while a
building has exactly one loop; but two boilers *are* already supported by the
CONNECTED mechanism, so the asymmetry is a modeling decision, currently implicit.

**To decide:** the normative scope rule the sweep applies uniformly — proposal:
*a fact describing the scope itself (building physics, the single heat-distribution
loop) is GLOBAL; a fact describing one device of possibly several (a generator's power
band) is CONNECTED* — and whether the HDS controller's facts stay GLOBAL under that
rule (i.e. one loop per building is a modeling commitment) or flip to CONNECTED now,
before the sweep multiplies the pattern.

**Decision:** _open_

---

## C. Physics/value decisions (each changes results — own commit, never bundled)

### C10. The UTSP `JsonReference` key-spelling bug

The hand-authored v2 scenario fixtures spell the LPG connector's nested references in
snake_case (`"household": {"name": …, "guid": {"str_val": …}}`), but the utspclient
dataclass fields are `Name`/`Guid`/`StrVal`. dataclasses_json matches nothing, so they
silently deserialize to empty references — invisible only because the fixtures run on
the predefined profile; under `USE_LOCAL_LPG` household selection would be broken.

**To decide:** fix the key spelling in the fixtures and verify one local-LPG run —
as its own commit with before/after evidence (it changes what the files build).

**Decision:** _open_

### C11. Buffer storage sized from building load vs generator power

The legacy gas/oil/pellet/wood-chip setups size the buffer storage from the *building
heating load* although the parameter is named after the *generator power* — a different
number once the boiler is sized (≈1.1 × max(load, DHW)). The presets preserve this
byte-identically; the heat pump chain genuinely reads the generator's contributed power
(the two coincide there, because the heat pump's law is a pass-through of the load).

**To decide:** bless the status quo explicitly, or schedule combustion buffers sized to
actual boiler power as a deliberate physics change with result diffs.

**Decision:** _open_

### C12. `heating_system` sizes to a constant, not to the building

`HeatDistributionControllerConfig.heating_system`'s law is the constant `FLOORHEATING`:
`"AUTO"` there means "the usual choice", not "derived from the building" — TABULA
carries no heat-distribution-type information, so building-driven inference would be
invented physics.

**To decide:** bless that semantics of AUTO on this field (plus one documenting
sentence), or commission a building-age→radiator heuristic — a result-changing decision
for old buildings.

**Decision:** _open_

---

## D. Process decisions

### D13. The 14 unbuildable components: delete or archive

14 components cannot be built from their own default configs; none is used by any
system setup. Three are zombies whose default connections import modules deleted long
ago (`controller_l1_building_heating`, `controller_l1_heatpump`,
`controller_l1_generic_runtime`); six are defective (`generic_battery` ×2,
`generic_ev_charger` ×4: missing mandatory output descriptions, or defaults naming
nonexistent database entries); five are legitimately data-dependent and keep their
skips.

**To decide:** delete outright, or move to `obsolete/` (the `SmartController`
precedent). Executing this in phase 3 shrinks the sweep — nobody converts a class that
is about to be deleted.

**Decision:** _open_

### Q5. The preset naming convention (names are wire format)

Once the v2 executor accepts `"preset": "<name>"`, preset names are API: a rename is a
breaking change with a migration note. The legacy surface being replaced had ~64
distinct factory-name spellings; the four converted classes currently ship:

| Class | Presets (canonical first) |
|---|---|
| `GenericBoilerConfig` | `condensing_gas`, `condensing_gas_12kw`, `oil`, `oil_12kw`, `pellets`, `wood_chips`, `hydrogen` |
| `HeatDistributionConfig` | `standard` |
| `EMSConfig` | `optimize_own_consumption` |
| `BuildingConfig` | `german_single_family_home` |

**Proposed convention, to accept or amend** (then audit the four classes against it):

1. snake_case; the name names the *variant*, never the class ("what kind", not "what
   it is": `condensing_gas`, not `gas_boiler_config`).
2. A bare variant name is the **sizable template** (AUTO where sizing applies); a
   rating suffix (`_12kw`) marks a **concrete catalog device** — nothing else carries
   numbers in its name.
3. The canonical (first) preset is the variant the repo's setups actually default to.
4. Single-word grammatical number follows the substance (`pellets`, `oil`), no forced
   singular/plural rule.
5. `standard` is reserved for classes with exactly one defensible preset; the moment a
   second appears, both get real variant names and `standard` is retired (rename while
   still free — before the executor ships preset references).

**Decision:** _open_

---

## Already decided (context, not agenda)

- **Design B revised** (field-declared laws, `AUTO`, presets) over designs A/C;
  JSON contract (b): preset reference + sparse overrides; generator emits templates,
  realized record on the result side. (2026-08-19)
- **A1–A4** (entry-level presets; grouped connections; YAML canonical for generated
  files; audit layout + provenance comments) and **B5** (enum-typed sizable fields pass
  `value_type=`). (2026-08-20)
- **D14 merge/carving order** — superseded by `roadmap/config_presets_plan.md`
  (2026-08-23, user): decisions strictly before conversions; sweep in four
  domain-sized batches; v2 MRs after.
- **`hisim.log` layering exception** and the observability layer (ResolutionReport,
  sizing logs, law-input capture). (2026-08-23, user)
- **Docstring convention:** self-contained, no spec references; test docstrings state
  the failure mode they catch. (2026-08-23, user)
