"""Independent domain calculators of the lifecycle cost engine (cost-spec-v2 §2.3, seam 3).

`evaluator.py` used to be one 1,200-line class holding every financial mechanism. This
subpackage holds the mechanisms one module per concern, so each can be reviewed (and later
tested) in isolation; `evaluator.EconomicEvaluator` keeps the frozen public surface and
composes the calculators.

Each module names the §2.3 table row it implements and the cost_spec.md sections it realizes.
The split of this package is a *pure refactor*: every calculator reproduces the arithmetic of
its former inline site bit-for-bit, including the escalation-exponent conventions and the order
in which entries reach the timeline (`CashFlowTimeline.npv` accumulates in entry order, so entry
order is part of the observable behavior).

**Reviewing one formula at a time.** The point of the split is that a domain expert can verify a
single mechanism without reading the orchestrator. One module, one review concern:

===========================  =========================================================
module                       the concern it isolates
===========================  =========================================================
`annualization.py`           scaling a partially simulated year up to a full one (W3.5)
`escalation.py`              the compound factor `(1+r)**n` and the two rate-fallback
                             chains (W3.1)
`categories.py`              which `CostCategory` set answers which membership question
                             (W3.3) — no arithmetic of its own
`context_resolution.py`      new vs. kept vs. replaced asset, sunk cost, anyway-cost
                             credit (§4.1, spec Q7)
`investment.py`              year-0 capex, the replacement schedule, residual value
                             (§3.6 rules 1-3, VDI 2067-1)
`maintenance.py`             maintenance rate x investment and fixed O&M (§3.6 rule 4)
`energy.py`                  the year-1 bill and its projection over the horizon
                             (§3.6 rule 5, §8.5)
`co2.py`                     the four CO2 figures that must never be summed (§3.8)
`subsidy_application.py`     awards -> SUBSIDY cash flows, and the legacy flat shim (§5)
`financing_application.py`   what gets financed and how the loan flows are laid out
                             (§4.4)
`reserve.py`                 the OPERATING_ONLY sinking fund (§4.2)
`aggregation.py`             discounting, NPV/EAC, pivots, per-subject breakdowns
                             (§3.7, §4.3, §7.4)
===========================  =========================================================

Sign convention throughout the package: amounts are **cost-positive** and revenue-negative
(§3.6). Every entry a calculator puts on the timeline is in *nominal* euros of the year it falls
in, undiscounted — `aggregation` discounts the finished timeline. (Two calculators discount
internally for a different purpose: `reserve` levelizes future replacements into a present-value
annuity, and `subsidy_application` lets the cumulation solver compare differently timed payouts.
Neither writes a discounted amount onto the timeline.) Year indices are relative to the
investment date — year 0 is the investment year, recurring flows run over years 1..T under the
end-of-year convention.
"""
