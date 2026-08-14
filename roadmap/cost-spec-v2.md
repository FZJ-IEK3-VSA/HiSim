# Cost Module v2 — Reviewability & Verification Spec

Status: **draft for review, rev. 2** (2026-08-12) — nothing in here is implemented yet.
Rev. 2 incorporates a detailed review of the plan against the code; claims about
the current state now carry file/line evidence, and a fix-or-freeze appendix (§7)
lists the defects found during that review.

Scope: restructuring `hisim/economics` for independent review/testing, plus a
human-checkable worked-example library. This spec does not change any pricing
methodology; it changes how the existing calculations are organized and verified.

Naming note: the repo root's `cost_spec.md` (self-titled "v2") is the
*methodology* spec; this document is the *reviewability* spec layered on top of
it. Its §10 migration constraints (strictly parallel implementation, no legacy
edits) continue to govern; everything below happens inside the new module and
is releasable independently of the legacy cutover.

## 1. Motivation

The cost module will be the foundation of all financial analysis. An undetected
error invalidates every research result built on top of it. The current module
(~8,900 lines across 26 files) is correct-by-construction in intent but is
reviewable only as a whole: a reviewer must understand simulation extraction,
cost data, financial math, and report generation at once to sign off on any of it.

Goals, in priority order:

1. **Localized review** — a domain expert can verify one concern (e.g. loan math,
   or database entries against sources) without reading the rest.
2. **Human-checkable references** — expected results that a person can re-derive
   with a pocket calculator or Excel, independent of the Python implementation.
3. **Error-class isolation** — a test failure names the layer that broke
   (extraction vs. data vs. formula vs. presentation), not just "NPV is wrong".
4. **Tamper-evidence** — expected values cannot silently drift to match a buggy
   implementation.

## 2. Package restructuring: four seams

The module is split along four seams that follow the existing data flow. Each
seam has a serializable contract, an independent test strategy, and isolates a
distinct error class.

The code-level starting position is better than a 8,900-line module suggests:
an import audit shows `hisim/economics` has **zero reach-back into simulation
objects** — no `hisim.component`, no `SimRepository`, no postprocessing logic;
only enum/dataclass imports of `hisim.loadtypes` and `kpi_structure`. And the
evaluator's external API surface is tiny: outside `evaluator.py`, only
`evaluate`, `evaluate_matrix` and `resolve_check` are called (`bridge.py`,
`scenarios.py`, `__main__.py`, tests). Internals are free to reshape.

### 2.1 Seam 1 — Simulation extraction ↔ economic inputs

**Cut between** `bridge.py`/`adapter.py` (read HiSim outputs and component
configs) and everything downstream. **Contract:** `EvaluationInputs` ↔
`economic_inputs.json` (`serialization.py`).

The contract *mostly* exists: `EvaluationInputs` (`evaluator.py:66-86`) is plain
data (no component objects, no callables), serialization round-trips it, and
the "pure function of a JSON file" path is already exercised by tests
(round-trip with equal NPV, three CLI tests, the report CLI). What does **not**
hold yet is faithfulness — today `economic_inputs.json` is not a pure
simulation extract. Work items, all preconditions for the seam's error-class
story ("wrong physical quantities in" vs. "priced them wrong"):

- **W1.1 — stop post-filtering inputs against price data.** *(done; amended by
  D7, §8.)* `compute_lifecycle_costs` used to drop `SubjectCostFacts` whose
  subject appeared in a `resolve_check` problem string (substring match)
  *before* writing the JSON, so extraction output depended on cost-database
  state. Now the faithful extract is written first and unconditionally, and the
  resolution check runs strictly downstream of the file. Per **D7** that check
  no longer drops anything: `require_resolvable_subjects`
  (`evaluator.py`) raises `UnresolvableSubjectsError` — a `CostDataError` —
  listing every blocked subject with its database reason, in the bridge and in
  the CLI alike. There is no partial cost result and no `--allow-drops` escape;
  non-blocking problems (an override without `override_source`) keep warning.
  Blockers without a subject (a billed carrier with no price entry) are
  included in the same error: they already aborted evaluation deep inside the
  database lookup, now they abort it early and typed.
- **W1.2 — move price-basis-year policy downstream.**
  `_pick_price_basis_year` (`bridge.py:69-87`) loads the cost database inside
  the extraction layer and sets an economic parameter that is *not recorded in
  the JSON*; the CLI defaults differently (`evaluator.py:154-156`), so the same
  file prices differently per entry point. The chosen basis year is either
  computed downstream or serialized.
- **W1.3 — close the serialization hole** (§7, B1): `SubsidyBuildingContext.
  existing_heating` is silently dropped (`serialization.py:163-211`), although
  the shipped DE catalog conditions on it (`DE.json:140,145` — BEG speed
  bonus). Re-pricing from JSON currently degrades those schemes.
- **W1.4 — decide the tariff-contract representation.** Contracts are stored by
  id only and re-resolved from the shipped tariffs directory
  (`serialization.py:226-246`); in-memory contracts cannot be restored, and
  `read_inputs` cannot take a custom tariffs path. Either embed contracts in
  the JSON or plumb `tariffs_base_path` through — the file must be
  self-contained or explicitly versioned against catalog state.
- **W1.5 — unit-test the extraction side.** Inverted from what one would
  expect, extraction is currently the *least*-tested layer: no unit test
  constructs fake wrapped components for `build_evaluation_inputs` or
  `adapter`; they are covered only transitively by one `extendedbase`
  simulation test. Add fixture-based tests (fake components + in-memory
  results frame → expected `EvaluationInputs`).

Test strategy per side, once faithful:

- Upstream test: compare the written `economic_inputs.json` against the
  simulation results (note: the bridge reads the in-memory results DataFrame,
  not the CSVs — the test should too) — pure extraction correctness, no
  economics knowledge needed.
- Downstream test: everything after this seam is a pure function of a JSON file.
  Small hand-written input files (one heat pump, 10,000 kWh, no subsidies) with
  exact expected results; fast, deterministic, no simulation run.
- Error class: "wrong physical quantities in" vs. "priced them wrong".
- **This seam is implemented first** — it makes the entire financial core
  testable without HiSim and is the precondition for cheap testing of the rest.

Known quirks to document at the contract (not necessarily change):
`BillingDeterminants.energy_bought_in_kwh` used to hold liters or tons after
`MeterSpec.quantity_conversion`, so the field name lied about its unit — **fixed
under D26**: the conversion moved to the price side, the field is kilowatt-hours
for every carrier, and `MeterSpec` has no `quantity_conversion` any more. What
remains is `_peaks_from_power_series` (`bridge.py:109-127`), which bakes a
15-minute billing grid into extraction, so a different capacity-tariff regime
requires re-running the simulation.

### 2.2 Seam 2 — Cost data ↔ calculation engine

**Cut between** the data layer (`database.py`, catalog/data halves of
`subsidies.py` and `tariffs.py`, `validation.py`, `provenance.py`) and the
evaluator.

**The contract must be built, not enforced** — rev. 1 claimed resolved entries
"carrying `UncertainValue` + provenance" already exist; they don't. Provenance
is a side-channel: entries hold string source-ids, `ProvenanceLedger` hands out
int ids threaded separately (`database.py:521-551`), the subsidy loader
resolves sources but never stores them, and the evaluator fabricates fake ids
(`inline:subsidy scheme …`, `evaluator.py:659`). Work items:

**Status (2026-08-12): every W2.x item is done.** What remains of seam 2 is the
*physical* split of §2.5 — moving `database.py`, the catalog halves of
`subsidies.py`/`tariffs.py` and `validation.py` into `data/`, and the evaluator,
calculators, subsidy application and billing engine into `engine/`. Both modules
that straddle the cut now carry a section banner naming which half each
definition lands in (`subsidies.py` since W2.3, `tariffs.py` since the tariff
straggler commit e52f065a), and the last data→engine dependency inversions are
gone: no engine code fabricates provenance, and no data module imports the price
catalog to construct contracts. The move is deferred to the stack carving so
importers churn once (open question 1).

- **W2.1 — resolved entries. Done, pragmatically** (commit d37167df). Scope
  honestly stated: the value bands were *not* moved into a new wrapper — they
  stay on `DeviceEntry` / `EnergyPriceEntry`, and tariff contracts and subsidy
  schemes got no wrapper at all (their provenance is solved by W2.4 instead).
  What was built is the half that was actually broken: resolution and provenance
  recording became one step. `CostDatabase.resolve_device_entry(...)` /
  `resolve_energy_price(...)` take the ledger plus the fields the caller will
  price from, record one `DATABASE_ENTRY` record per field *at resolution time*,
  and return `ResolvedDeviceEntry` / `ResolvedPriceEntry` (entry + field → ledger
  id); asking for an unrequested field raises. `provenance_for_device` /
  `provenance_for_price` have no callers outside the database any more, so a
  calculator can no longer look an entry up and forget its provenance — the old
  failure mode was silent (right number, empty ledger). The raw
  `get_device_entry` / `get_energy_price` stay for verification and presentation
  (`resolve_check`, `validation`, `audit`, the report's input-audit table), which
  read entries without pricing and would otherwise invent records for a dry run.
  Two pricing sites keep the raw lookup, each commented: the replaced asset's
  removal cost and its like-for-like credit record nothing today, and recording
  them would change ledger content instead of preserving it — the one remaining
  gap, together with the feed-in rate of a default contract. Parity was checked
  by dumping an evaluation's ledger before/after: NPV, EAC, the record set and
  every entry's provenance content are identical; ids renumber only where a
  subject mixes overrides with database fields (database records now precede the
  override records).
- **W2.2 — typed benefits. Done** (commit d0f6ffe4). One frozen payload per
  kind — `ShareBenefit(rate)` (SHARE_OF_ELIGIBLE_COST + BONUS_SHARE),
  `LumpSumBenefit(amount)`, `PerUnitBenefit(amount)`,
  `TaxCreditBenefit(rate, years, annual_shares)`, `ReducedVatBenefit(vat_rate)`,
  `LoanTermsBenefit(interest_rate, term, repayment_grant_rate)`,
  `OperationalBenefit(rate_per_kwh, carrier, duration_years)` — parsed by
  `parse_benefit` at catalog load, which rejects missing, unknown and
  unparsable keys naming the scheme id and the key. The catalog JSON format is
  unchanged. `SubsidyScheme.__post_init__` keeps kind and payload in sync, so
  in-memory schemes are typed too. The divergent valuation in
  `required_questions` is gone: `Benefit.value_estimate(gross, size)` is the
  one simplified valuation, on the same typed fields the solver reads. Two
  checks moved from solve time to load time (`annual_shares` sum to 1, and
  their count matches `years`). B7 (REDUCED_VAT has no consumer) stays open:
  typed and documented as unwired, neither wired nor deleted.
- **W2.3 — split `Condition` into data and evaluator. Done** (commit 2cffad12).
  `Condition` is a frozen, behavior-free AST; `parse_condition` (data side)
  builds it, `evaluate_condition` and `referenced_fields` (engine side)
  consume it, with a section banner separating the two halves of the module.
  The field vocabulary is now *derived*: `_enumerate_context_fields()` walks
  `CONTEXT_ROOTS` (the context dataclasses), one nested dataclass level and
  their properties, so `KNOWN_CONTEXT_FIELDS` cannot fall behind a new context
  field (18 → 23 names, a superset — no shipped condition changed meaning).
  The two derived-field special cases collapsed into `DERIVED_CONTEXT_FIELDS`
  + `question_targets()`, and `scheme_context_fields(scheme)` is the single
  definition of a scheme's context dependencies (conditions plus what the
  eligible-cost spec implies), used by both the question derivation and
  `validation.py`. Kept in one module deliberately: the §2.5 package split
  moves both halves at once, and splitting now would churn every importer
  twice or create an AST ↔ context import cycle.
- **W2.4 — subsidy provenance end-to-end. Done** (commits db688848, 3ec6e415).
  (a) `SubsidyCatalog.load` keeps the source resolution it used to discard
  (`SubsidyCatalog.sources`) and mints the provenance itself
  (`provenance_for_scheme`): a catalog-loaded scheme records `DATABASE_ENTRY`
  with its real registry ids, carrying its `legal_basis`/`url` — scheme fields,
  not registry entries — in `detail`. In-memory schemes (tests, worked examples)
  record the new `ParameterOrigin.IN_MEMORY_DEFINITION`, which carries no source
  ids *by design* instead of faking one. Catalog files must now cite sources (a
  load error, like device entries and tariff contracts). A result's
  `source_resolver` merges both registries — cost database *and* subsidy catalog,
  whose id spaces are disjoint — so subsidy ids resolve at a report leaf, and the
  report's "sources used" table lists every registry entry the ledger cites.
  (b) The last shipped `inline:` id is gone: the synthetic dynamic contract cites
  the new `src_tariff_synthetic_fixture_2024`, labelled as the test fixture it is
  (authored, not retrieved; replace before publishing), and an inline id in a
  tariff *catalog file* is now a validation **error**. The convention survives
  only for contracts built in memory, which no registry can back; the `tariffs.py`
  module docstring states that split. `validate_all()`: 0 errors, 0 warnings.
- **W2.5 — validate what is currently unvalidated. Done** (commit b6d865be).
  `validate_tariff_contracts` walks `cost_database/tariffs/*.json` (§7 B9):
  every contract parses through the §8.2 schema, its id matches its file name
  (contracts are resolved by file name), a DYNAMIC contract's `spot_series`
  CSV exists, and non-inline source ids resolve against the cost-database
  registry. Inline sources (`inline:<citation>`, which the shipped dynamic
  contract used and `results.py` renders) were accepted with a warning; W2.4b
  (commit 3ec6e415) retired them — in a catalog file they are now an error, and
  the shipped contract cites a registry entry. New catalog-level errors: duplicate scheme ids, `excludes`
  naming a non-existent scheme, `annual_shares` summing to 1, and cumulation
  groups whose members disagree on `combined_rate_cap` (the solver applies the
  group minimum to all members). Note `cumulation_group` is a group *label*,
  not a scheme id, so there is nothing to resolve it against; cap coherence is
  its catalog-level invariant. Benefit key typing is enforced at load by W2.2.
  Shipped data passes: `validate_all()` reported 0 errors and 1 warning (the
  inline tariff source) and reports **0 errors, 0 warnings** since W2.4b.
- **W2.6 — `legacy_flat_subsidy_share`. Done** (commit 8696c966), by marking
  rather than moving. cost_spec.md §10.1 keeps the flat shim alive until Phase 4
  has a catalog for every shipped country (Ireland has none), so the capability
  stays; a separate data file for one float per entry — with its own loader,
  validation, overlay and provenance plumbing, to be dismantled again at Phase 4
  — would cost more than it clarifies. Instead: the field sits in a marked
  `§10.1 migration shim` block in `database.py` and a §3.2a section of the
  package README saying it is not device data, who reads it and when it dies; it
  left the scenario-overlay surface (sweeping a subsidy level is a subsidy axis —
  catalog plus subsidy mode — and an overlayable shim reads as a supported knob),
  so overlaying it is an error; and the shim path emits its own record with the
  new `ParameterOrigin.LEGACY_MIGRATION_SHIM`, citing no source unless the data
  file declares `field_sources` for the share — the shipped entries' `source_ids`
  are capex market surveys that say nothing about a 30 % subsidy.

Tariffs, by contrast, were already ~80 % cut and are now fully placed (commit
e52f065a): all escalation and multi-year projection live in the evaluator,
`apply_tariff` is a pure year-1 billing engine, and the three functions that
straddled the line are settled. `default_from_price_entry` moved to its only
caller, `calculators/energy.contract_from_price_entry` — building a contract out
of a §3.5 price entry is engine behavior, and it had made the tariff *data*
module import the price catalog. `validate_billing_interval` stays on the data
side (a contract-data pre-check against a simulation parameter) and
`marginal_purchase_price_components` stays with the contract dataclass, both
under an explicit section banner. The duplication is gone too: the simulation
side used to reimplement FLAT/ToU/DYNAMIC selection
(`components/tariff_provider._purchase_price`), so a controller's price and its
bill were two implementations of one rule; both now call
`tariffs.marginal_purchase_price_in_euro_per_kwh` (with `time_of_use_band_for`
and `energy_price_in_euro_per_kwh` beneath it), the billing engine's semantics
being authoritative, and `spot_factor` is applied in exactly one place.
`tests/test_tariff_provider.py` pins per-kind agreement between the simulated
price series and the bill.

- Data side: a review problem, not a math problem — entries verified against
  cited sources; `validate` keeps doing structural CI checks.
- Engine side: math tests run against tiny synthetic databases with round
  numbers, so they never break when real prices are updated.
- Error class: "wrong number in the database" vs. "wrong formula".

### 2.3 Seam 3 — Engine internals: independent domain calculators

`evaluator.py` (1,093 lines) is the concentration of risk. **Split it into pure
calculators** that each emit cash-flow entries. **Contract:** `CashFlowTimeline`
**plus typed non-cash outputs** — the timeline alone is not sufficient: four
results legitimately bypass it today (`sunk_cost`, the CO2 mass accounting
`co2_result`, the modernization-levy `basis_parts`, and the subsidy
`decisions` record). Each calculator's signature declares which of these it
produces; none are smuggled through shared mutable accumulators as they are
now (`co2_result` is mutated by two different phases, `ledger` by three,
`decisions` written by subsidies and read by financing).

Calculators (extended from rev. 1 — the first five existed in the plan; the
rest are real code in `evaluator.py` that the plan missed):

| Calculator | Concern | Today | Reference check |
|---|---|---|---|
| investment schedule | capex, replacements, residual value | `build_timeline:379-513` + `_resolve_device` | hand-computable schedules |
| context resolution | existing assets, kept/new/replacement, `is_new_investment`, sunk cost + anyway-cost credit (§4.1, Q7 coupled cost) | `_resolve_device:274-307`, `build_timeline:416-464` | small brownfield scenarios with hand-derived credits |
| maintenance & fixed O&M | maintenance rate × investment, fixed opex, escalation | `build_timeline:515-531` (see §7 B2) | rate × capex × escalation by hand |
| financing | loan/annuity flows (`financing.py`, already pure) | `_apply_financing:885-947` | closed-form annuity formulas, `PMT` |
| energy bills | tariff application, annualization, escalation | `_add_energy_flows:722-883` | year-1 bill from consumption × price |
| subsidy application | eligibility + amounts + cumulation | `_add_subsidies` + `subsidies.py` engine half | "subsidy ≤ eligible cost" property + catalog examples |
| CO2 accounting | embodied, operational, macro damage entries, CO2-price bill component (four kinds that must never be summed, `results.py:60-82`) | scattered: `build_timeline:411/488/564-581`, `_add_energy_flows:867-883` | mass balance by hand |
| replacement reserve | operating-view sinking fund (a discounting computation currently inside timeline construction) | `build_timeline:548-562` | annuity closed form |
| discounting/aggregation | timeline → NPV/EAC, pivots, LCOH | `evaluate:978-1006`, `_build_breakdowns`, plus `timeline.py:136-162` (already pure) | NPV at 0% = plain sum; annuity closed forms |

Cross-cutting cleanups that make the calculators independently reviewable:

- **W3.1 — escalation as one shared helper.** Escalation is applied at ~9
  sites with 5 different rates (replacements, residual, anyway credit,
  maintenance, energy working/standing, spread, grid fee, feed-in). One
  audited `escalate(amount, rate, year)` + the two rate-fallback chains.
- **W3.2 — financing takes typed inputs.** Today it is a timeline *rewriter*:
  it derives the principal by re-scanning already-emitted year-0 entries with
  a hardcoded category set (`evaluator.py:901-912`), reads subsidy `decisions`
  for LOAN_TERMS overrides, and emits a further SUBSIDY entry (repayment
  grant) *after* the subsidy calculator ran. New signature: financing receives
  a typed "year-0 net investment" figure and the loan-terms award; ordering
  dependencies become arguments, not conventions.
- **W3.3 — one category-semantics map.** Three disagreeing category-set lists
  exist (`timeline.py:43-69`, `_apply_financing:903-908`,
  `_build_breakdowns:1055-1065`). One module-level definition, used by all.
- **W3.4 — make the subsidy total complete and unit-consistent. Done**
  (commit c81262ea). The defects were: `_add_subsidies` emitted OPERATIONAL
  payouts as SUBSIDY timeline entries **without accumulating them into the
  returned total**, `_apply_financing` emitted the repayment-grant SUBSIDY entry
  *after* that total was closed, and tax-credit schedules entered the total
  discounted while upfront grants entered nominal — so the modernization-levy
  basis deducted less support than the landlord receives (real money: the tenant
  was over-levied). Decision: the basis deducts the **nominal sum of all SUBSIDY
  timeline entries** (§559 BGB deducts subsidies *received*). The figure is now
  derived from the finished timeline by
  `calculators/subsidy_application.nominal_support_from_entries`, after
  financing has run — complete by construction and recoverable from the
  timeline. The per-subject display figure was split into
  `subsidies_nominal_in_euro` and `subsidies_npv_in_euro` so every reported
  support number names its unit. Both §5.1 conservation xfails are green.
- **W3.5 — annualization in one place.** Partial-year annualization is
  duplicated with two different zero-guards (`_add_energy_flows:735-765` vs.
  `_add_subsidies:635-639`).
- **W3.6 — allocation is a producer, not a filter.** `DE2024Ruleset.allocate`
  *mints* new MODERNIZATION_LEVY entries (`actors.py:213,222`); the
  orchestrator's composition model must account for allocation adding money
  movements, and the zero-sum invariant (`actors.py:246`) is its test.
- **W3.7 — entry-level sign validation. Done** (commit 745ddfd7). The blocker
  was that `REVENUE_CATEGORIES` conflated "band is mirrored for slot assembly"
  with "entry is negative". Split into `REVENUE_CATEGORIES` (mirroring) and
  `NEGATIVE_SIGN_CATEGORIES` (= the former plus LOAN_DISBURSEMENT), with a
  payer-aware `expected_sign(entry)` for MODERNIZATION_LEVY, whose sign depends
  on which leg of the transfer pair it is. `CashFlowTimeline.add`/`extend` now
  validate; synthetic test timelines opt out with `CashFlowTimeline(
  validate=False)`.

The remaining evaluator becomes an orchestrator that composes timelines and
typed side-outputs, tested via conservation invariants (§5.1), staying nearly
logic-free. Perspective flags (`include_investment`, `macro`, `subsidy_mode`)
remain inputs to several calculators — "pure" means deterministic in inputs,
not perspective-free.

- Error class: each financial mechanism verified in isolation instead of only
  end-to-end.

### 2.4 Seam 4 — Results ↔ presentation

**Cut between** `results.py`/`LifecycleCostResult` and the presentation layer.
**Enforceable invariant: presentation never computes.** No arithmetic beyond
formatting; every displayed number must already exist in the result object.

Rev. 1 called this "largely organizational enforcement of boundaries that
already half-exist". The code review shows it is the largest construction job
of the four seams; scoping it honestly:

- **W4.1 — build the result view-model. Done** (commits 43968df3, 2df60f3b).
  `hisim/economics/views.py` holds the ~15 formerly on-the-fly derivations as
  pure functions of `LifecycleCostResult` returning plain data, each naming the
  presentation site it replaces: cumulative discounted cost per slot,
  the nominal year × category matrix, loan interest/principal series, the
  (year, subject, category) detail rows with year subtotals, the payer ×
  category NPV pivot, per-carrier year-1 bills with annualized quantity and
  effective price, per-subject year-0 build-up and net outflow, subsidy share
  of gross, cumulative operational CO2, per-category and
  per-subject-per-category EAC, total subsidies received. Display grouping
  stays presentational per the stated exception, but the sums come from
  `fold_categories`/`fold_category_matrix`, into which presentation passes its
  own DISPLAY_GROUPS mapping. Scenario swings/min-max/spread live on
  `ScenarioCube` and the payback curve on `VariantComparison` (both W4.3/W4.4,
  commit 2ea33610). Two business rules that were hiding in chart code got a
  documented home: the `min(subsidy, gross)` clamp (formerly duplicated in
  `reporting.py:1227` = `report_plots.py:93-96`, closing §7 B8's first half)
  and "feed-in is shown in the electricity bill but never enters the effective
  price". `exports.py` stopped minting its per-category EAC, per-entry discount
  factors and support total; the written files are byte-identical.
  Every view is pinned in `tests/test_economics_views.py` against a
  brute-force recomputation on a banded, financed, subsidised, feed-in,
  multi-subject, actor-allocated fixture. Presentation consumes them since
  W4.7a (commit f6c20c82); three further views were added there for figures the
  inventory had missed (`investment_net_of_subsidies`, `payer_npv_total`,
  `award_total_amount`).
- **W4.2 — plausibility checks move engine-side. Done** (commits 111a0c65,
  fa834f6d). `hisim/economics/plausibility.py` produces a typed
  `PlausibilityReport` of `PlausibilityFinding`s — stable `check_id`, float
  `value` + `unit`, the `bounds` a range check required, a `context` dict for
  the other numbers a check compared — with no formatting anywhere in it;
  `reporting.py` keeps only `_render_finding` and the reader hints.
  Its precondition landed first: `LifecycleCostResult` now carries
  `annual_energy_quantities_by_carrier` (annualized through
  `calculators/annualization.py`), `reference_areas` and
  `simulated_period_fraction`, all serialized additively into
  `lifecycle_costs.json`, so the checks no longer need `EvaluationInputs`.
  Parity is pinned by `PANEL_BEFORE_W42` (the eleven rendered rows captured
  from the pre-move implementation) and was additionally verified by diffing
  the full `cost_summary.md`/`lifecycle_report.html` of the fixture
  before/after — byte-identical.
- **W4.3 — one discount formula. Done** (commit 2ea33610).
  `timeline.discount_factor(interest_rate, year)` is the single implementation;
  `CashFlowTimeline.npv`/`npv_by`, `EconomicParameters.discount_factor`,
  `results.compare` and `exports.py` all route through it. The last
  hand-written copies, in presentation (`reporting.py:691`,
  `report_plots.py:216-229`), went with W4.7a: grepping `reporting.py` and
  `report_plots.py` for `(1 +` / `(1.0 +` now finds nothing.
- **W4.4 — `results.py` is engine, not DTO. Done for the comparison math**
  (commit 2ea33610). `cumulative_discounted_savings` produces the payback curve
  per slot through the canonical discount helper, `VariantComparison` carries
  it (`cumulative_discounted_savings_in_euro`, serialized additively) and
  `discounted_payback_year` derives the number *from* the curve, so the drawn
  curve and the printed year cannot disagree. Warm-rent neutrality annuitizes
  rather than discounts and was left as it was, with a comment saying so.
  §7 B4, the `explain` scoping bug, was left to S4b because fixing it changes
  `explain` output; **fixed** there (commit fb729175):
  `CashFlowTimeline.scoped_to(payer)` is now the single definition of
  perspective scoping, used by the aggregation calculator, by
  `scoped_timeline()` and by `_entries_for_path`.
- **W4.5 — the report CLI reads stored results. Done** (commit af6dba6c).
  `serialization.py` gained the inverse of the export set —
  `result_from_json` / `matrix_from_json` / `read_results` — rebuilding a full
  `EvaluationMatrix` from `lifecycle_costs.json`, `cash_flow_timeline.csv` and
  `cost_provenance.json`. Two additive extensions made the reload faithful:
  `scope_payer` and `simulation_year` in `lifecycle_costs.json`, and a
  `subject_kind` column appended to `cash_flow_timeline.csv`. The one field a
  reload cannot reproduce, `source_resolver`, is a database/catalog artifact and
  is not needed: the input audit is persisted as `cost_audit.json` and carries
  its own resolved §3.10 sources. `report` loads and renders when a directory
  has stored results — no evaluator, no database, no catalog, `--compare` the
  same per directory — and falls back to re-evaluation with a printed notice
  otherwise. `--scenarios` still evaluates, because a scenario cube *is* a fresh
  set of evaluations (§4.6). Pinned by a round-trip test asserting the rendering
  from reloaded files is byte-identical to the rendering from memory.
- **W4.6 — reclassify `audit.py`. Done** (commit a597e632). `audit.py` sits on
  the verification side of the lint and keeps computing, by design.
  `build_input_audit` resolves every declared fact **once** into a typed
  `InputAuditReport` (`input_audit.py`: `ResolvedInputRow` with an `ORIGIN_*`
  kind, override source, entry key, source ids, bands, flags, plus the resolved
  §3.10 sources); `write_cost_audit` renders it to CSV, byte-identically, and
  the report's section 1 renders the same rows. The types live in their own
  module so presentation can import them without importing the database and the
  evaluator. `SourceRegistry.referenced_ids()` replaced the private-set access.
  The duplication had a real consequence: the report dropped an override's unit
  price whenever the asset class had no database entry, which the CSV did not —
  now fixed and pinned.
  **The gross-investment proposal is rejected, with measurement.**
  `write_parity_report` keeps recomputing the legacy formula: the breakdown's
  `investment_gross_in_euro` adds year-0 PLANNING and REMOVAL and is taken on
  the *scoped* timeline, while the legacy CSV column it diffs against is the
  bare device investment. They agree on shipped data only because
  `planning_cost_in_euro` and `removal_cost_in_euro` are 0 in every data file,
  so adopting it would have turned every row into a false discrepancy the day
  someone priced planning. Recomputing the legacy formula is the harness's
  purpose; the docstring says so.
  With section 1 rendering typed rows, `reporting.py` needs neither
  `EvaluationInputs` nor `CostDatabase`: `LifecycleCostResult.simulation_year`
  (additive) carries the last thing it read off the inputs.
- **W4.7 — untangle `report_plots` ← `reporting`. Done** (commit f6c20c82).
  `presentation_style.py` holds `DISPLAY_GROUPS`, the palette and `group_of`
  and imports nothing but `timeline.CostCategory`; both renderers take them
  from there and `report_plots` no longer imports `reporting` at all. Its
  duplicated payback discounting, group sums and `min(subsidy, gross)` clamp
  are gone the same way. `CATEGORY_TO_GROUP` is total over `CostCategory`, so
  `fold_categories`' deliberate no-gaps rule cannot turn a newly added category
  into a report crash.
  §7 B8's second half closed with it: `_waterfall_svg` is handed its net rather
  than summing the steps, so the comparison waterfall's net bar reads
  `npv_delta_in_euro`.

Allowed exception, stated once: mapping the 16 cost categories onto ~8 display
groups is a presentation concept; the *grouping* may live in presentation, but
group **sums** are provided by the view-model.

**Seam 4 is complete.** Package S4a (commits 111a0c65, fa834f6d, 2ea33610,
43968df3, 2df60f3b) built the engine-side halves — W4.1–W4.4 — without changing a
single number. Package S4b (commits 91b8abf9, f6c20c82, a597e632, af6dba6c,
fb729175, 42a247cb) performed the switch-over, and also without changing one:
the golden files landed **before** the first line of presentation was touched
(91b8abf9) and are byte-identical through every commit since. The predicted
float noise stayed below the display rounding, as expected — moving the detail
table's discounted column from `amount / (1+i)**year` to
`amount * discount_factor(...)` (observed maximum deviation 2.2e-16 relative)
changed no rendered digit anywhere in the report.

**The enforced contract**, as `tests/test_economics_import_lint.py` states and
checks it (with `ast`, so a deferred import inside a function counts like a
top-of-file one):

* Presentation is `reporting.py`, `report_plots.py`, `presentation_style.py`.
  From `hisim.economics` they may import only `results`, `views`,
  `plausibility` (its *types*: `run_plausibility_checks` and
  `PlausibilityConfig` are banned by name), `input_audit`,
  `presentation_style`, `exports` (`build_lifecycle_kpi_entries`, so the KPI
  table and `lifecycle_kpis.json` cannot disagree), `timeline`, `uncertainty`,
  `carriers`, `parameters`, `provenance`. Everything else — `evaluator`,
  `database`, `calculators.*`, `subsidies`, `tariffs`, `scenarios`, `financing`,
  `actors`, `perspectives`, `facts`, `adapter`, `bridge`, `serialization`,
  `validation`, `audit` — is out of reach. `scenarios` is absent rather than
  allowed because section 9 takes its `ScenarioCube` as an untyped parameter and
  asks it for `equivalent_annual_cost_swings` / `_spreads`.
* `report_plots` does not import `reporting`; `presentation_style` imports
  `timeline` and nothing else.
* The dependency runs one way: `exports` (serialization), `audit` (the parity
  harness, which computes by design), `input_audit`, `views`, `results` and
  `plausibility` must not import a renderer.
* What is left of arithmetic in presentation: SVG geometry, axis scaling and
  `_fmt`'s rounding. Grepping the two renderers for `(1 +` / `(1.0 +` finds
  nothing; every remaining `sum(` computes a stack height or a span.

**Deferrals from S4a, settled.**

* *`year_zero_build_up`, full vs. scoped timeline* — **settled: scoped**, like
  every other view. The same section's table is built from
  `component_breakdowns`, which the engine derives from the scoped timeline, so
  under an actor scope the waterfall and the table beneath it disagreed: a
  tenant perspective drew the landlord's full investment above an empty table.
  Measured on the S4b fixture, the two readings differ for exactly that case
  (a tenant scope has no year-0 build-up, having no year-0 flows) and are
  identical for every other perspective, landlord included — so the goldens did
  not move. `payer_category_npv_pivot` remains the one deliberate exception: it
  is the view *of* the split and must see every payer.
* *`total_subsidies_received` — award-based meaning* — **resolved by D7's
  sibling D2 (§8): the KPI is now the timeline-based nominal figure.** It used
  to sum the catalog's award amounts (`SubsidyDecision.applied[*]
  .upfront_amount`) and therefore omitted every euro that reaches the timeline
  without an upfront award — scheduled payouts, repayment grants, operational
  support and the §10.1 flat shim. It now calls `nominal_support_from_entries`
  on the perspective's scoped timeline, the same basis the §6.4 levy deducts
  (W3.4). Report goldens re-baselined deliberately with that commit.

- Test: `tests/test_economics_report_goldens.py` (golden `cost_summary.md` and
  `lifecycle_report.html` from a deterministic in-memory fixture — the only
  normalization is today's ISO date, replaced literally, so the §3.10
  `retrieved` dates stay in the golden; plus the stored-results round trip) and
  `tests/test_economics_import_lint.py` (the contract above).
- Error class: a reporting bug can mislead a reader but can never corrupt
  research numbers.

### 2.5 Target package layout

```
hisim/economics/
    uncertainty.py timeline.py provenance.py carriers.py
    facts.py parameters.py                       # shared kernel (facts/parameters are
                                                 # part of the seam-1 contract surface)
    inputs/        # bridge, adapter, serialization            (seam 1, upstream)
    data/          # database, catalogs (subsidy + tariff data halves), validation
    engine/        # calculators/, evaluator (orchestrator), financing,
                   # subsidies (application), tariffs (billing), actors,
                   # perspectives, results (+ view-model), plausibility, scenarios
    presentation/  # reporting, report_plots, presentation_style
                   # (exports is result serialization, not presentation — W4.6)
    verification/  # audit (legacy parity harness — computes by design);
                   # input_audit holds its typed rows, importable from both sides
    __main__.py    # CLI: thin orchestration over inputs/engine/presentation
```

Placement notes (modules rev. 1 left unplaced): `actors.py` and
`perspectives.py` are engine (allocation produces entries; perspectives are
engine configuration). `facts.py` and `parameters.py` are kernel — they are
the vocabulary of the seam-1 contract. `financing.py` is already a pure
engine calculator. `scenarios.py` is a *driver above* the evaluator (it
builds a fresh `EconomicEvaluator` per scenario, `scenarios.py:239`), so it
depends only on the frozen contract below.

**Frozen contracts** through the restructuring:
`EconomicEvaluator(database, parameters, subsidy_catalog)` with
`evaluate(inputs, perspective)`, `evaluate_matrix(...)`, `resolve_check(...)`
— the only evaluator surface used outside the file — and
`EvaluationInputs` ↔ `economic_inputs.json`.

Migration order: seam 1 (with W1.1–W1.5) → seam 3 → seams 2 and 4. Seams 2
and 4 are **not** merely organizational (see W2.x, W4.x) but they depend on
the calculator boundaries from seam 3 to know where the moved logic lands.

## 3. Worked-example library

A library of hand-computed reference results, authored in Excel (the tool
humans enjoy and domain experts trust), converted to diffable YAML that the
tests read. The Excel formulas are a genuinely **independent second
implementation**: if Python and the spreadsheet agree, two different toolchains
produced the same arithmetic.

**Ordering dependency:** examples are grouped by calculator (seam 3). Until
the seam-3 split lands, only `financing/` (already a pure function) and
`end_to_end/` (through `EvaluationInputs`) examples have stable entry points;
author those first, the rest follow the split.

### 3.1 Repository layout

```
tests/worked_examples/
    financing/loan_10y_3pct.xlsx          # source of truth, authored by humans
    financing/loan_10y_3pct.yaml          # GENERATED, diffable, read by pytest
    tariffs/...
    subsidies/...
    end_to_end/...                        # few full EvaluationInputs cases
tools/convert_worked_examples.py          # manual run + CI drift check
```

**One example = one workbook = one YAML file** (decided, was open question):
Excel's *Create Names from Selection* produces **workbook-scoped** names, so a
sheet-per-example workbook would collide `principal` across examples, and
sheet-scoping names manually in the Name Manager is exactly the fiddly step
authors will get wrong. One `.xlsx` per example also isolates binary churn per
example in git history. A shared `_template.xlsx` is copied to start a new one.

### 3.2 Workbook authoring conventions

Fixed template layout, validated by the converter:

- **Metadata block:** `name`, `spec_section`, `computed_by`, `description`
  (the description states the derivation in words).
- **Inputs table** and **Expected table**, each using the label convention:
  identifier in column A, value in column B, optional display text in column C.
  Expected rows carry an explicit **tolerance column** (never guessed).
- **Labels are valid identifiers** matching `[a-z][a-z0-9_]*`, following the
  codebase's unit-suffix convention (`principal_in_euro`, `interest_rate`).
  YAML keys, Excel labels, and Python field names line up 1:1, so a reviewer
  traces one quantity through all three layers by name.
- **Named cells:** authors select label+value columns and use Excel's
  *Create Names from Selection* (Ctrl+Shift+F3), then write formulas directly
  in terms of names: `=principal*interest_rate/(1-(1+interest_rate)^-duration_years)`.
  Readable in Excel itself, before any conversion.
- **Round numbers deliberately** (20,000 €, 3%, 10 years, 0.30 €/kWh): a
  reviewer must be able to re-derive most steps in ten minutes, or the example
  provides no human check.
- **Assert intermediates, not just finals:** expected rows include intermediate
  quantities (annuity, year-1 interest, remaining debt at year 5, …) so a
  failure names the step that broke.
- **Uncertainty policy (new):** worked examples use **degenerate bands only**
  (every input an exact value; min = best_estimate = max). The engine's LOW/BEST_ESTIMATE/HIGH
  slot mechanics — `as_revenue` band mirroring, the envelope semantics of
  `UncertainValue.__sub__`, slot-wise caps — are verified by the property
  tests of §5.1, *not* by worked examples. If a banded example ever becomes
  necessary, the template grows explicit `_min`/`_best_estimate`/`_max` label triples;
  until then the converter rejects band syntax.
- **Sign convention (new):** the template states the engine convention (cost
  positive, revenue/subsidy negative) next to the Expected table; the
  converter checks the sign of expected values in known revenue-type rows.

### 3.3 Converter (`tools/convert_worked_examples.py`)

Reads each workbook with `openpyxl` twice — `data_only=False` (formula text)
and `data_only=True` (cached values) — and emits deterministic YAML (stable key
order, fixed float formatting, values rounded consistently with the declared
tolerance).

**Formula extraction and rewriting:**

- Formulas written with defined names pass through as-is.
- Raw references (`=B2*B3` from click-and-point authoring) are rewritten to
  names using the coordinate→name map, via `openpyxl.formula.Tokenizer`
  (never regex), so `PMT(B3,B4,B2)` → `PMT(interest_rate, duration_years, principal)`.
- Ranges rewrite by endpoints: `SUM(B5:B14)` → `SUM(cash_flow_year_1:cash_flow_year_10)`.
- Excel surface syntax is kept (`^`, `PMT`, `NPV`): the derivation is for human
  verification, and Excel's finance functions are well-documented reference
  semantics in their own right.

**Validation rules (conversion fails on violation):**

1. Every cell referenced by any formula has a name — no magic intermediate
   cells; every quantity in the derivation chain is named and visible.
2. No duplicate names within a workbook (names are workbook-scoped; one
   workbook per example makes this the natural unit).
3. No volatile functions (`TODAY()`, `RAND()`, `NOW()`) — they break
   reproducibility of cached values.
4. No external workbook references.
5. Every expected cell contains a formula, or an explicit `note` explaining
   where the constant comes from (e.g. a literature value) — a bare pasted
   constant stands out.
6. Labels must be valid identifiers (§3.2); tolerance cells must be present.
7. Ranges must consist of individually labeled rows (or a labeled table, once
   the template grows one for long timelines).
8. No uncertainty-band syntax (degenerate-bands policy, §3.2).

**Arithmetic cross-check (stale-cache defense):** for pure-arithmetic formulas
(`+ - * / ^ ( )` over names — not the Excel function library), the converter
re-evaluates the formula in Python against the named values and fails on
mismatch with the cached value. This catches "edited an input without letting
Excel recalculate" at conversion time. Cells using `PMT`/`NPV`/etc. skip this
check and rely on the cached value — which is fine: they are exactly the cells
whose Excel implementation is wanted as the independent reference.

**Dependencies (clarified):** `openpyxl` is currently a *runtime* dependency
(requirements.txt); this work moves it to a dev/test extra. PyYAML is used
transitively today and must be declared explicitly as a dev/test dependency.
`numpy_financial` (§5.3) is a **new** dev/test dependency.

### 3.4 YAML format

```yaml
# GENERATED from loan_10y_3pct.xlsx — edit the xlsx, then run tools/convert_worked_examples.py
name: loan_10y_3pct_round_numbers
spec_section: "4.4"
computed_by: "N. Pflugradt, by hand + Excel PMT, 2026-08-05"
description: >
  20,000 EUR loan, 10 years, 3% interest, annuity repayment.
inputs:
  principal_in_euro: 20000
  interest_rate: 0.03
  duration_in_years: 10
expected:
  annuity_in_euro:
    value: 2344.61
    abs_tol: 0.01
    derivation: "principal_in_euro * interest_rate / (1 - (1 + interest_rate)^-duration_in_years)"
  interest_year_1_in_euro:
    value: 600.00
    abs_tol: 0.01
    derivation: "principal_in_euro * interest_rate"
  remaining_debt_year_5_in_euro: {value: 10741.36, abs_tol: 0.01, derivation: "..."}
  total_interest_in_euro: {value: 3446.10, abs_tol: 0.01, derivation: "..."}
```

A PR diff therefore shows *which* expected value changed **and** *how it was
computed* — the derivation audit trail materializes automatically.

### 3.5 Test collector

One parametrized pytest collects every YAML, maps the example's group to the
corresponding calculator entry point (seam 3) or to `EvaluationInputs`
end-to-end evaluation (seam 1), runs it with the declared inputs, and compares
each expected value within its tolerance. Marker: `worked_examples` (fast,
part of `base`). The collector also validates each example's review
attestation from the YAML alone (§3.8) and warns (later: fails) on unreviewed
or stale examples.

### 3.6 CI drift check

CI re-runs the converter and **fails if the committed YAML differs from the
xlsx output**. This closes both silent failure modes:

- xlsx edited, YAML not regenerated → drift check fails;
- YAML hand-edited to make a test pass without touching the xlsx → drift check
  fails.

Conversion remains a manual authoring step; CI only proves the YAML is exactly
what the xlsx says.

### 3.7 Process rules (not enforceable by tooling)

- An expected value may **not** change in the same PR as engine-code changes
  unless the YAML's description states the re-derivation. (Protects the
  independence of the reference — the classic golden-test failure mode is
  pasting the implementation's output back into the fixture.)
- The formula-extraction makes a pasted constant visible (constant where a
  formula should be, caught by validation rule 5), but the last line of defense
  is review discipline.
- Every discrepancy in §7 gets a fix-or-freeze decision **before** the first
  worked example touching that code path is authored — otherwise the example
  fossilizes the bug.

### 3.8 Review attestations (in the workbook, content-addressed)

Every worked example records whether *this revision* has been human-reviewed.
Design principles: the attestation lives **in the workbook itself** — the
reviewer must have the sheet open in Excel to attest — and it is bound to
content by a fingerprint, never a boolean anyone must remember to reset.

- **Content fingerprint** := shortened SHA-256 (12 hex chars, displayed as
  `XXXX-XXXX-XXXX`) of the generated YAML **minus its `review:` block**. All
  semantic content (inputs, formulas, expected values, tolerances) is inside
  the hash; the attestation itself is not — this breaks the self-reference.
- The workbook metadata block (§3.2) gains three optional labeled rows:
  `reviewed_by`, `review_date`, `reviewed_fingerprint`. All three set or all
  empty (converter validation rule).
- **Attestation flow:** the converter prints the current fingerprint for every
  unreviewed example; the reviewer opens the workbook (recalculated, in front
  of them), performs the §3.2 re-derivation pass, types name + date + the
  fingerprint into the metadata block, saves, re-runs the converter. The YAML
  gains a `review:` block; CI's drift check (§3.6) pins it to the sheet.
- **Auto-reset is pure logic:** any semantic change to the workbook changes
  the YAML, so the stored fingerprint no longer matches and the example is
  unreviewed *by definition*. Nothing is cleared; the converter warns
  "review by &lt;name&gt; is stale (fingerprint mismatch)" until re-reviewed.
  Cosmetic xlsx edits (formatting, layout) don't reach the YAML and leave the
  attestation valid. A pasted typo behaves like staleness: mismatch, warning.
- **The collector (§3.5) validates from the YAML alone** — recompute
  hash(YAML minus `review:`), compare to the embedded fingerprint — no
  registry file, no xlsx access in tests. Unreviewed/stale examples emit a
  pytest warning while `tests/worked_examples/enforcement.yaml` says `warn`;
  flipping it to `error` after the first review round is a one-line data
  change.
- **No tool ever writes an xlsx** (openpyxl destroys cached formula values on
  save); attesting is always a human action inside Excel.
- `reviewed_by` complements `computed_by`: machine-drafted examples
  (`computed_by` naming a tool) start unreviewed by construction and gain
  fixture-trust only through a human attestation. Attestations are
  diff-visible despite the binary xlsx, because they appear in the generated
  YAML.
- Limitation, accepted: pasting the fingerprint without truly re-deriving
  defeats the mechanism — same trust boundary as §3.7; the forcing function
  (file open, explicit paste, attributable name in the PR diff) is the
  strongest available short of proctoring.

## 4. Deprecated alternative: hand-written YAML

Authoring YAML by hand was considered and rejected: not an enjoyable human
task, no formula audit trail, and domain experts who should review the
economics work in Excel, not YAML. Excel-only fixtures (tests parsing xlsx
directly) were also rejected: binary blobs are invisible in code review, and
`openpyxl` reads stale cached values without a drift check.

## 5. Complementary verification (what point examples can't catch)

Worked examples are point checks; they miss errors that cancel or sit off the
tested path.

### 5.1 Invariant / property tests

- **Money conservation** (the killer invariant for a cost model): every euro in
  the aggregate NPV traces to exactly one cash-flow entry; totals equal the sum
  of calculator outputs. **Status 2026-08-12: green.** The two conservation
  tests were written first as strict xfails, and gated the seam-3 cleanups as
  intended: they flipped to passing with W3.4 (commit c81262ea), which made the
  subsidy total complete and put every reported support figure in a named unit.
  W3.3 turned out not to break the invariant — the three category lists answer
  three different questions and are correct as they stand.
- Subsidy ≤ eligible cost.
- NPV at 0% interest = plain sum of the timeline.
- Doubling consumption doubles the variable part of the bill.
- Zero-sum actor allocation: sum of payer NPVs = system NPV — per slot for
  retag-only rulesets, with band-envelope containment where a ruleset mints a
  transfer pair (B11, envelope semantics; `actors.assert_zero_sum`). A property
  test over random timelines, in both regimes.
- **Uncertainty-band properties** (carries the load the degenerate-band worked
  examples deliberately don't): slot ordering preserved through every
  operation, `as_revenue` mirroring, envelope semantics of subtraction,
  slot-wise cap clamping. **Status 2026-08-12: green.** The last strict xfail
  (B12, the overall subsidy cap applied per slot) flipped with commit e9f3c11a;
  the §5.1 suite now has no xfails.
- These hold for all inputs — run them on randomized/property-based inputs,
  not only curated ones.

### 5.2 Externally published reference examples

The strongest fixtures, because nobody in the team computed them: worked
examples from the annuity-method standards the spec follows (VDI 2067 /
EN 15459 annex examples), finance-textbook loan tables, official subsidy
program illustrative cases (BAFA/KfW). Stored in the same worked-example
format with `computed_by` citing the source. One passing standard-annex
example is worth ten homemade sheets.

### 5.3 Independent implementation cross-check

For closed-form parts, tests compare against `numpy_financial`
(`pmt`, `npv`) on randomized inputs — a third implementation for free.
(New dev/test dependency.)

## 6. Open questions for review

1. **Package layout depth** (§2.5): sub-packages as proposed, or flat module
   with enforced import-lint layers only? The import audit shrinks this
   question: only `bridge.py`, `scenarios.py`, `__main__.py` and the tests
   import the evaluator, so path churn is minimal. Recommendation: import-lint
   contracts from day 1 (cheap, reversible), physical sub-packages once seam 3
   has settled the calculator boundaries.
2. **Where the seam-2 split of `subsidies.py` lands** — *resolved 2026-08-12*:
   W2.2/W2.3/W2.5 landed as one package of separate commits (d0f6ffe4,
   2cffad12, b6d865be) inside `subsidies.py`, with a section banner marking the
   data/engine cut and the two B-items on that path (B5, B12) fixed alongside
   (16a69812, e9f3c11a). The *physical* move into `data/` and `engine/` is left
   to the package split so importers churn once. W2.1/W2.4/W2.6 followed on the
  same terms (d37167df, db688848, 3ec6e415, 8696c966, e52f065a): seam 2 is
  behaviorally complete, only the physical `data/` vs `engine/` move is left.
3. **Long-timeline examples** (§3.2): individual row labels vs. a labeled
   two-column table convention — decide when the first 40-year example appears.
4. **Tolerance policy**: default `abs_tol` per unit (0.01 € for money, 1e-6 for
   rates), overridable per cell, or always explicit as currently specified?
5. **Which existing tests migrate** into the worked-example format vs. stay as
   plain pytest. (The VDI-2067 hand examples in `test_economics_engine.py` are
   the natural first migrations.)
6. **Tariff contracts in `economic_inputs.json`** (W1.4): embed full contracts
   vs. reference-by-id + explicit catalog version stamp?
7. **Fix-or-freeze decisions for §7** — each needs an owner and a call before
   worked examples freeze the affected path.
8. **What "total subsidies received" means** (carried out of seam 4, §2.4):
   **decided (D2, §8) and implemented** — the KPI is the nominal support on the
   perspective's scoped timeline (`nominal_support_from_entries`), unified with
   the §6.4 levy basis, no longer the sum of the catalog's upfront award
   amounts. `views.total_subsidies_received` documents the scoping.

(Resolved since rev. 1: Excel template distribution — one workbook per
example, §3.1.)

## 7. Appendix: known discrepancies (decide fix-or-freeze before freezing references)

Found while reviewing this plan against the code (2026-08-12). Worked examples
and golden files pin behavior; each item below must be explicitly fixed, or
frozen-as-intended with a note, before the reference covering it is authored.

| # | Where | Defect | Impact | Proposed |
|---|---|---|---|---|
| B1 | `serialization.py:163-211` | `SubsidyBuildingContext.existing_heating` not serialized; DE catalog conditions on it (`DE.json:140,145`) | Re-pricing from JSON loses BEG speed bonus → wrong subsidies | **Fix now** (independent of refactor; W1.3) |
| B2 | `evaluator.py:519-527` | Maintenance + fixed opex summed into one entry; category chosen by heuristic (MAINTENANCE if maintenance > 0) | Mislabels fixed opex; DE2024 splits MAINTENANCE landlord/tenant but FIXED_OPERATION is tenant-only → **real money moves between actors** | **Fixed** (commit 375f69d1): `calculators/maintenance.py` emits up to two separately categorized entries per year. No shipped number moved — only VENTILATION_SYSTEM carries both cost kinds and no test priced it — so the fix is pinned by new tests (`TestMaintenanceAndFixedOperation`) instead of a re-baseline |
| B3 | `subsidies.py:749-750` vs `evaluator.py:915-922` | Solver values repayment grant on gross measure cost; financing applies it to the principal | Solver overstates soft loans whenever `financed_share < 1` (masked: shipped KfW rate is 0.0) | Fix in W3.2 |
| B4 | `results.py:165-180` vs `evaluator.py:986-991` | `explain()` filters the full timeline; `total_npv` uses the payer-scoped one | `explain("total_npv_in_euro")` returns entries the KPI doesn't contain | **Fixed** (commit fb729175, package S4b — see §2.4). Scoping now has one implementation, `CashFlowTimeline.scoped_to(payer)`: `calculators/aggregation` derives every scoped KPI through it, `LifecycleCostResult.scoped_timeline` returns it, and `_entries_for_path` filters it. `total_npv_in_euro`, `equivalent_annual_cost_in_euro`, `monthly_cost_year1_in_euro`, `npv_by_category[…]` and `npv_by_component[…]` are all scope-filtered figures and now explain themselves from the scoped timeline; `npv_by_payer[actor]` is the one pivot deliberately taken over *all* payers (it exists to show the other side of the split) and stays on the full timeline, where filtering by that payer yields the same entries either way. The change is visible only for actor-scoped perspectives — a tenant-scope `explain("total_npv_in_euro")` used to list the landlord's INVESTMENT entries and no longer does. No existing test asserted the old output, so nothing was re-baselined; two new tests in `TestActorAllocation` pin the scoped and the pivot case, the first also checking that the listed entries sum back to the value they explain |
| B5 | `evaluator.py:651` → `calculators/subsidy_application.py:149` | Perspective `subsidy_mode` filtered awards *after* the cumulation solve, no re-solve | ONLY/EXCLUDE perspectives can get a non-optimal remainder combination | **Decided 2026-08-12: fixed, filter before the solve** (commit 16a69812). `solve_cumulation`, `assess_schemes` and `required_questions` take an optional `admits(scheme_id)` predicate (a plain predicate, not a `SubsidyMode`, so the subsidy module keeps no dependency on the perspective types); `build_subsidy_flows` passes `subsidy_mode.admits` and no longer post-filters. Eligibility, cumulation, the undetermined bound and the question set now see the same admitted candidate set. Measured effect on the shipped DE catalog: an EXCLUDE perspective dropping the four BEG heat-pump schemes used to receive **0 EUR** (the solver picked the BEG stack, which excludes §35c, and the post-filter deleted it) and now receives the §35c tax credit, 20 % of the eligible cost. No existing test asserted the old behavior, so nothing was re-baselined. |
| B6 | `subsidies.py:795` vs `:824` | Main cumulation loop enumerates 2^n subsets unguarded; only the optimistic-bound loop caps at n ≤ 16 | Pathological catalog → hang | Fix now (add the same guard) |
| B7 | `subsidies.py:691-698` | `REDUCED_VAT` / `PayoutKind.VAT_REDUCTION` awards have no consumer anywhere | Dead feature or missing wiring | **Still open** — W2.2 (commit d0f6ffe4) typed the payload as `ReducedVatBenefit(vat_rate)` and documented it as unwired at both ends (the dataclass and the award branch), deliberately neither wiring nor deleting it. Decide: wire into `_eligible_cost_basis` VAT netting, or delete kind |
| B8 | `reporting.py:1227`, `report_plots.py:93-96`, `:746` | Business rules in chart code: silent `min(subsidy, gross)` clamp duplicated twice; comparison waterfall computes its net bar instead of reading `npv_delta_in_euro` | Displayed numbers can diverge from result object | **Fixed**, in two halves. The clamp moved into `views.subsidy_share_of_gross` with W4.1 (commit 43968df3) and both chart helpers now read it; the waterfall's net bar was closed with W4.7a (commit f6c20c82) — `_waterfall_svg` is handed its net instead of summing its steps, so the comparison waterfall prints `npv_delta_in_euro` and the investment waterfall prints `YearZeroBuildUp.net_outflow_in_euro`. No rendered digit changed (the dropped sub-cent subject deltas were below the display rounding) |
| B9 | `validation.py:146-158` | `validate_all` never validates `cost_database/tariffs/*.json` | Malformed tariff data ships silently | **Fixed** (commit b6d865be, W2.5): `validate_tariff_contracts` parses every contract, checks id-vs-file-name, the `spot_series` reference and source resolution. Tightened by W2.4b (commit 3ec6e415): an `inline:` source id in a catalog file is an **error**, not a warning, and the shipped dynamic contract cites `src_tariff_synthetic_fixture_2024` — a registry entry labelled as the synthetic test fixture it is. `validate_all()` is now clean of warnings as well as errors |
| B10 | `bridge.py:266-283` | Inputs post-filtered against cost DB (substring match) and price basis year chosen at extraction, unrecorded | `economic_inputs.json` unfaithful; CLI vs. postprocessing price differently | Fix in W1.1/W1.2 — **done** (commits 7ea9509d, 78698826) |
| B11 | `actors.py:213-226` | Modernization-levy pair: tenant pays `annual_levy`, landlord receives `annual_levy.as_revenue()` — mirroring makes the pair sum to (min−max, 0, max−min) per slot, so §6.5 zero-sum holds only in BEST_ESTIMATE. (The existing engine zero-sum test is tautological: it compares payer NPVs against the *post*-allocation timeline.) | **Not fixable by a sign flip**: the band-ordering invariant forces the mirror — a slot-coherent transfer entry would have min > max. §3.9 already defines "minimum = best case" for signed flows. | **Decided 2026-08-12: envelope semantics — implemented** (commit aadda132). `assert_zero_sum` asserts exact equality in BEST_ESTIMATE always, and containment otherwise (`payers.minimum ≤ system.minimum`, `system.maximum ≤ payers.maximum`: minting may only *widen* the payer band); `require_per_slot_equality=True` restores the strong form for retag-only rulesets and degenerate levy bases. The invariant test is no longer an xfail and pins the widening to exactly the discounted mirror gap. |
| B12 | `subsidies.py:719-728` | EU state-aid overall cap scaled all upfront awards by the BEST_ESTIMATE-slot ratio only | With banded cost bases, LOW/HIGH-world support can exceed the same world's eligible cost (or even gross cost); latent while shipped data is degenerate | **Fixed** (commit e9f3c11a): the ratio is computed per slot (`_overall_cap_ratios`) and applied per award (`_scaled_to_cap`). Per-slot ratios are not monotone across the slots (the cap grows with gross cost, the support with the eligible bases, a statutory lump sum not at all), so a plain slot-wise product can order an award's band the wrong way round; each award is therefore capped from the HIGH slot downwards — a slot takes the smaller of its own capped value and the slot above. That gives `min <= best_estimate <= max` by construction, per-slot support ≤ per-slot cap, and award ≤ its own basis, at the price of a slot occasionally leaving support unused (the conservative direction). Degenerate bands — every shipped catalog, `overall_cap_share` being null in DE and AT, and every worked example — are bit-for-bit unchanged. The §5.1 property test is no longer an xfail and now also asserts the stronger "total ≤ gross × cap_share" per slot; the suite carries **no xfails**. |

## 8. Decision log (owner interview, 2026-08-12)

Every consequential call of the implementation phase, decided or ratified
explicitly by N. Pflugradt. "Ratified" = made during implementation on stated
rationale, confirmed unchanged; "Decided" = chosen from alternatives here.

| # | Topic | Decision |
|---|---|---|
| D1 | B7 `REDUCED_VAT` benefit kind | **Leave typed, unwired**; wiring is a methodology question deferred until a real scheme needs it. |
| D2 | `total_subsidies_received` KPI | **Decided: switch to the timeline-based nominal figure** (consistent with the W3.4 levy basis; includes flat-shim, operational and repayment-grant support). **Done** (post-interview fixes, commit `62a29877`): the view calls `nominal_support_from_entries` on `result.scoped_timeline()`, so a SYSTEM-scope perspective reports every SUBSIDY entry and an actor scope reports that actor's. Report goldens re-baselined deliberately, four KPI rows only: brownfield_net / financed_net / landlord 10,400.00 [8,320 \| 13,520] → 14,600.00 [11,400 \| 18,840] (the §35c tax credit's three instalments, 1,470 + 1,470 + 1,260, which carry `upfront_amount == 0`), and the tenant row disappears (a tenant receives none of the landlord's support). `cost_summary.md` is unchanged. |
| D3 | Modernization-levy basis unit | Ratified **nominal** (§559 BGB deducts subsidies as received, undiscounted). |
| D4 | B5 subsidy-mode filtering | Ratified **pre-solve** (optimizer sees only admitted schemes; non-admitted schemes absent from rejected/undetermined lists). |
| D5 | B12 overall-cap design | Ratified the **per-slot, HIGH-downward conservative clamp** (cap never overrun; a slot may leave admissible support unused). |
| D6 | B2 category split | Ratified: fixed operation apportions to tenants in full (BetrKV), maintenance splits — the old lumping was wrong. |
| D7 | Unresolvable-subject policy | **Decided: FAIL FAST everywhere** — overrides the implemented drop-and-continue. An unresolvable cost subject is a hard error in bridge and CLI alike; `economic_inputs.json` is still written first (faithfulness unchanged). **Done** (post-interview fixes, commit `364c2315`): `drop_unresolvable_subjects` is gone, replaced by `require_resolvable_subjects` raising `UnresolvableSubjectsError(CostDataError)`; the CLI exits 2 with that message, the bridge lets it propagate into postprocessing's existing try/except (legacy outputs unaffected). No silent-drop path remains. See §2.1 W1.1. |
| D8 | Default tariff contracts on reload | Ratified: recorded in the file, **regenerated** from price entries on reload (keeps scenario price overlays effective); explicit contracts round-trip byte-identical. |
| D9 | §3.8 enforcement flip | **Keep `warn` indefinitely** — advisory until external/domain-expert reviews are in, not only the owner's attestations. |
| D10 | Sequencing | Post-interview fixes package first, then carve the PR stack; attestations run in parallel at the owner's pace. |
| D11 | Publishing | Everything prepared **locally** (branches, PR descriptions as text files); the owner pushes and opens PRs personally. |
| D12 | Price-basis-year policy | Ratified: simulation year, else earliest covered year **with warning**; explicit parameter always wins. |
| D13 | Timeline sign validation | Ratified: **on by default** at `CashFlowTimeline.add`, payer-aware for the levy, explicit `validate=False` escape for synthetic test timelines. |
| D14 | PostProcessingOptions values | Ratified: renumbered 28/29 with main's scheme — values are positional, options are referenced by name. |
| D15 | Worked-example file layout | Ratified: **one .xlsx per example** (workbook-scoped names; per-example git churn). |
| D16 | Uncertainty bands in worked examples | Ratified degenerate-only **for now**; **planned extension**: `_min`/`_best_estimate`/`_max` label triples in the template for a few hand-checkable banded examples once the format has proven itself (converter rule 8 to be relaxed then). |
| D17 | End-to-end example pricing | Ratified: synthetic empty country `XX` — no shipped value can leak into hand-checkable numbers. |
| D18 | Report goldens | Ratified: normalize **only** the literal run date; every figure, coordinate and tooltip exact; PNGs not goldened. |
| D19 | Shim / parity formula / explain | Ratified as implemented: legacy flat-subsidy shim marked-not-moved and removed from the overlay surface; parity audit keeps the bare-device-investment formula; `explain` lists scoped entries for scoped figures (B4). |
| D20 | B11 zero-sum semantics | Decided earlier the same day (see §7 B11): **envelope semantics**, implemented. **Extension, ratified 2026-08-14 (review issue 10):** the same semantics govern the *cumulation solve*, not only the transfer pair. `solve_cumulation` (`subsidies.py`, `slot_getters`) values a candidate combination in the LOW slot at each award's band **maximum** and in the HIGH slot at its **minimum**, because §3.9 defines LOW as the best case for the applicant; the winner reported per slot in `other_slot_optimal_combination` is therefore the combination that is optimal *against the envelope*, evaluated with the most favourable value each award can take, and not the combination that would win in a coherent world where the whole cost basis is simultaneously low. Those are different questions and can have different answers: a scheme whose cap binds only at a high basis can be envelope-optimal in the LOW slot while a coherent low-cost world would have preferred another set. This is the deliberate reading, not a defect — the envelope is what §3.9 publishes everywhere else, and reporting per-slot winners on any other basis would make the slot columns of one report mean two different things. The residual limitation is the one B11 already names (slot-wise combination is not world-coherent); it is stated here so a reviewer meets it in the solver as well as in the levy pair. |
| D21 | `database.py` module split | **Decided: split into three modules** — `catalog_entries.py` (the row dataclasses, their resolved wrappers and the parsing helpers, with `CostDataError` at the bottom of the import order), `sources.py` (the `sources.json` registry) and `database.py` (`CostDatabase` alone). Rationale: at 804 lines the data layer was the least reviewable PR of the stack, and the file did three separable jobs. Pure code motion; `database.py` re-exports every moved public name via `__all__`, so no importing module changed. |
| D22 | No module-level constants in `hisim/economics` | **Decided as a lasting convention** (owner preference): the package holds no module-level constants and no mutable module state. Every constant is a class attribute — on the class it belongs to where one exists (`CostDatabase.DEFAULT_PATH`, `DeviceEntry.PER_UNIT_TO_SIZE_UNIT`, `SourceRegistry.SOURCE_KINDS`), otherwise on a namespace class naming the group (`ExportFileNames`, `CategoryRules`, `CheckIds`, `PresentationStyle`). Mutable module globals are banned outright: the warn-once set behind the price-basis-year policy became a class attribute. Values, orderings and file names are unchanged throughout — the goldens and worked examples pin that. Type aliases and `__all__` are not constants and stay. |

| D23 | `FinancingPlan.refinance_replacements` and `ApplicantActor.from_actor` | **Decided: delete both** (review issue 21). The bool was parsed out of a perspective file, serialized and never read — it looked like a switch for financing replacements while every replacement was in fact paid cash or from the §4.2 reserve, which is the more misleading of the two failure modes. Replacement refinancing stays a **possible future feature**: it needs a schedule of its own (a second loan per replacement year, or a revolving facility) plus a decision on how the §4.2 reserve and a refinanced replacement coexist, and should be specified before any field re-appears. `from_actor` was an unused constructor mapping `timeline.Actor` onto applicant roles; the applicant is stated directly on `ApplicantProfile`, and `WEG` has no `Actor` counterpart, so the mapping was never total in the other direction either. A stale perspective file naming the removed field now fails loudly at `FinancingPlan(**raw)` (D7) instead of being silently ignored. |
| D24 | Package A — engine correctness | **Decided: fix all nine** (review issues 1, 4, 5, 6, 7, 9, 20, 21, interview 2026-08-14). (1) The year-T residual value is emitted only for an installation the timeline actually charged — `is_new_investment` or an in-horizon replacement — so a kept brownfield asset outliving the horizon no longer credits revenue against a cost that was never booked (DIN EN 15459-1: residual value of investments made *within* the calculation period). (4) `operational_co2_by_carrier_in_kg` accumulates instead of assigning, so two meters on one carrier agree with the by-year series. (5) `ProvenanceLedger.from_json` rebuilds the interning index, so `record()` on a rehydrated ledger still deduplicates instead of renumbering ids that exported files reference. (6) The LOAN_TERMS award substitution uses `is not None` for rate, term **and** repayment grant: a stated 0.0 % soft-loan rate survives, and an award that says nothing about a Tilgungszuschuss no longer erases the plan's (`SubsidyAward.loan_repayment_grant_share` becomes `Optional[float] = None`). (7) Monthly peak blocks are sized by ceil division, so a partial-year run keeps the peak of its trailing intervals; at most twelve blocks, the last one short. (9) `robustness_summary` rejects an empty scenario list with a `ScenarioDataError` naming itself, instead of a bare `min()` failure; the slot-aware dominance flag stays defined on the equivalent annual cost. (20) **Not applicable as filed**: the inline category sets `evaluator.py:379/432` reported are gone since the seam-3 slim-down — the module contains no `CostCategory` reference at all and realizes both perspective rules by never generating the flows, which is what `CategoryRules.INVESTMENT_CATEGORIES` / `SUBSIDY_FLOW_CATEGORIES` document declaratively (see `calculators/categories.py`). No code change; the finding is closed as stale. (21) `FeedIn.spot_factor` now scales the integrated spot term of SPOT_REFERENCED feed-in revenue (the markup is unaffected); the two dead names are deleted under D23. |
| D25 | Package B — fail-fast & diagnostics | **Decided: tighten all eight** (review issues 2, 3, 18, 22, 23, 24, 25a-c, interview 2026-08-14). (2) A component whose class *is* in `FactsExtractors.BY_CLASS_NAME` but whose extractor returns nothing is no longer dropped while counting as PRICED: it becomes an `UnresolvedSubject` on `EvaluationInputs`, is written into `economic_inputs.json` like every other extract, and blocks the D7 resolution check with a reason naming the component and its class. (3) An unmapped or missing `fuel_loadtype` raises `CostDataError` naming the meter instead of billing the fuel at heating-oil prices; the bridge catches it per component so the config error lands on the same D7 path rather than in a swallowed warning. (18) `Component.get_energy_flow_facts` is wired as the meter path — component hook first, `adapter.get_meter_spec` table second, mirroring the §9.1 precedence for cost facts; the two determinants the hook cannot express (capacity peaks, the native billing quantity) still come from the `MeterSpec`, so adopting the hook cannot silently change a fuel meter's billing unit *(the billing-unit half of this is superseded by D26: there is no quantity conversion any more, only peaks)*. (22) An INELIGIBLE scheme names the condition leaf it failed and the answer that failed it ("condition not met: building.construction_year <= 2004 (actual: 2010)"); an `all` names every failing leaf, an `any` reports as "none of: …", a `not` says the child does hold. Catalog schemas unchanged; no golden pinned the old fixed string. (23) A `--parameters` path that does not exist exits 2 with the path in the message instead of silently pricing with the defaults — an omitted flag still means defaults; every other `CostDataError` now reaches the same handler. (24) An unknown carrier in an energy-price file raises a located `CostDataError` (file, entry, known carriers) like the device loader's errors, not a bare `ValueError`. (25a) `load_spot_series` raises on a malformed line with its line number — a skipped hour shifts every later price — while blank lines and a non-numeric first line (the header) stay tolerated. (25b) The negative-flexibility clamp stays for the §8.5 arithmetic, but the raw value now travels on `LifecycleCostResult.raw_flexibility_value_by_carrier` and surfaces as a WARN finding (`CheckIds.CHECK_FLEXIBILITY_VALUE`); the calculator still imports no presentation. (25c) A registered existing asset whose class left the database raises unless the register declares `replacement_cost_override_in_euro` — that override keeps the documented `FALLBACK_SERVICE_LIFE_IN_YEARS`, which is now reachable only on that path. |
| D26 | Uniform EUR/kWh pricing basis | **Decided: convert the price, never the quantity** (review issue 11, interview 2026-08-14). `energy_bought_in_kwh` now holds kilowatt-hours for every carrier — the adapter's kWh→tons/liters conversion is gone, so the field name, the `EUR/kWh` labels of the report and the plausibility panel, and the number underneath them finally agree. The data files originally kept their native quotes (EUR/t for pellets and wood chips, EUR/l for oil and diesel); the PR-2 review (2026-08-18) found the `*_per_kwh` key holding a per-liter number too confusing, so the shipped files now store true EUR/kWh — converted with the engine's own `energy_content_of` resolution, the as-published quote and heating value recorded in each row's `notes` (reviewability preserved there instead of in the value itself). `CostDatabase.get_energy_price` still divides the working price *and* the emission factor by the carrier's lower heating value at resolution time for any user-supplied file that quotes natively, taking that value from `PhysicsConfig` alone so a bill can never disagree with the physics that produced its kWh, and records the division in the provenance detail ("300 EUR/t ÷ 5000 kWh/t = 0.06 EUR/kWh (LHV: PhysicsConfig PELLETS)") so a published native quote survives into the audit trail. Applying the same divisor to price and factor while the quantity gains it back leaves every product unchanged: pellet and wood-chip bills and emissions are bit-for-bit what they were, and the shipped plausibility bands were converted with the same heating values so no band changed what it admits. The only figure that can move is heating oil, whose liters used to come from the fuel meter's configurable `heating_value_of_fuel_in_kwh_per_liter` (default 9.82) rather than from `PhysicsConfig` (9.8217): a meter left on the default bills 0.017 % less than before, and that config field now serves the component's own legacy OPEX report only. |
| D27 | §559e heating modernization levy | **Decided: implement now, data-driven, with nested caps** (review issue 15, interview 2026-08-14). The dangling `heating_levy_rate_per_year` finally has a consumer: a heating modernization measure is levied at **10 %/year** of its subsidy-reduced basis instead of §559's 8 %, and the rent increase attributable to those measures is capped at **0.50 EUR/m²/month** (`heating_cap_in_euro_per_m2_per_month`). Three sub-decisions. *(a) Classification is data.* `modernization_levy.heating_measure_component_types` in `allocation_DE_2024.json` lists the `ComponentType` names that earn the §559e rate — the §71 GEG 65-%-renewable heat-supply options that exist in `devices_DE.json` (`HEAT_PUMP`, `DISTRICT_HEATING`, `SOLAR_THERMAL_SYSTEM`, `PELLET_HEATER`, `WOOD_CHIP_HEATER`) plus the `THERMAL_ENERGY_STORAGE` installed with them. Fossil `GAS_HEATER`/`OIL_HEATER`, the conditionally compliant `HYDROGEN_HEATER`/`ELECTRIC_HEATER`, the heat emitters and the whole envelope stay at 8 % — the conservative direction, since claiming §559e for a measure that does not qualify overstates a landlord's recoverable rent. An unknown name in the list is a located `CostDataError` (D7), not a silently ignored row. *(b) Caps nest.* The §559e leg is capped first at 0.50 EUR/m²/month; the **total** of both paragraphs then has to fit the existing §559 Abs. 3a cap regime including its low-rent tier. When the total cap binds, the **non-heating leg is reduced first**: it is the larger rate base's natural residual claim, the §559e leg is already individually capped at a small figure, and reducing the heating leg first would leave a landlord with a *lower* total increase for doing the heating measure than for skipping it — the opposite of the incentive §559e exists to create. The heating leg only yields when it alone exceeds the total cap, which the shipped parameters (0.50 < 2.00 < 3.00) never produce. *(c) One booked pair, split derived.* The evaluator now hands the allocation a **per-measure** levy basis (`ModernizationLevyBasis.by_subject`, checked against the aggregate at context assembly), the ruleset classifies it and runs *one* deduction rule (§559 Abs. 2 = §559e Abs. 2) over both pools, and `compute_modernization_levy` returns both legs — but the timeline still books their **sum** as a single tenant/landlord transfer pair. Reason: the total cap couples the legs, so with a banded basis a single leg can carry a slot-reordered band while their sum cannot; booking the legs separately would publish two entries whose slot-wise sum is not the rent increase the tenant pays (the same representability limit as B11). Reports keep their paragraph-agnostic wording. *Consequences:* the zero floor of the basis now applies per paragraph pool rather than to one aggregate, so a package whose heating measure is over-subsidized no longer nets that surplus against its envelope measures; the report goldens' landlord/tenant rows move accordingly (worked example `end_to_end/heating_levy_559e_mixed_package`). |
| D28 | Package E — honest reporting surfaces, tooling and API hygiene | **Decided: fix all eleven** (review issues 8, 12, 13, 14, 16, 17, 19, 26, 27, 28, 29, interview 2026-08-14). (8) `TariffProvider.CapacityChargeMarginal` emits the contract's capacity price only on a timestep whose draw is at or above the running billing-period peak and 0.0 below it, which is what its own output description promised and what a peak-shaving controller needs; the comparison is made against the peak before the timestep is folded in, so the convergence rollback keeps it stable. (12) `cost_audit.csv` gains a **Price basis** column in front of the three unit-price columns (`EUR/<size unit> (database)` vs `EUR absolute (override)`): the column held two different quantities under one label, and reading an override as a per-unit price understates a subject by orders of magnitude. (13) An `ORIGIN_UNRESOLVED` row is labelled `unresolved - not priced` instead of falling through to the bare string `database entry`, which reported an unpriceable component as if the database had priced it. (14) One `BILL_CATEGORIES`: the definition moves to the kernel (`timeline.CategoryRules`) and `plausibility.PlausibilityCategories` / `views.ViewCategories` bind that object, so the panel's effective-price check and report section 4 cannot drift into checking a price the report does not show. (16) The subsidy cards, the awards table and the markdown bullets de-duplicate by **decision content** (measure plus every scheme's outcome, amount, payout, binding caps and open questions) instead of by measure name, and each distinct decision is annotated with the perspectives that reached it; perspectives that differ in subsidy mode now both appear, where before the first one seen won and the rest vanished. The report goldens move by exactly those annotations and the new table column — no figure changes. (17) `plot_investment_waterfall`'s docstring said the subsidy share is "hatched off"; the code draws two colour-coded stacked segments and always did. Wording only. (19) `write_actor_kpis_into` becomes `actor_kpi_entries` and is **wired** into `build_lifecycle_kpi_entries`, hence into both `lifecycle_kpis.json` and the report's KPI table: the §6.5 actor split reached no KPI file at all. Purely additive names; the landlord/tenant KPIs still sum to the system NPV per slot, which is asserted on the written file. The HTML golden gains those rows for its two actor-scoped perspectives. (26) The worked-example converter's arithmetic cross-check parenthesizes Excel's unary minus, which binds *tighter* than `^` where Python's `**` binds tighter than unary minus: `=-A1^2` was transliterated into a Python expression of the opposite reading and accused a perfectly fresh workbook of being stale. Excel's left-associative `^` chain remains unhandled and keeps failing the check conservatively, which is documented rather than silently wrong. (27) `validation.py` cited `tests/test_economics_data_files.py`, which does not exist; it now points at `test_economics_data_and_integration.py`. (28) Two weak tests pinned: the billing-interval check asserts `CostDataError` and its message instead of bare `Exception`, and `test_break_even_finds_crossing` asserts that a crossing was found, that it lies inside the search range and that the cost difference changes sign across it — it was previously satisfied by "no crossing in range". (29) `Simulator` gains a public read-only `simulation_parameters` property and both `system_setups/economic_example/` setups use it, dropping their `# noqa: SLF001` — reference material is copied, and reaching into a private was the habit it was teaching. |

The two open work items of this log are **done**, as the package "post-interview
fixes": **D7** (fail-fast; commit `364c2315`) and **D2** (KPI switch, with the
one sanctioned report-golden re-baseline; commit `62a29877`). D16 is a planned later
extension, not scheduled.
