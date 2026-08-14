"""One place for the cost-category sets the engine tests membership against (W3.3).

**W3.3 is centralization without unification.** Three category lists used to sit in three
different files and *disagree with each other*; the review could not tell whether the
differences were intentional. They are collected here side by side, still as distinct
constants, with the disagreement spelled out — and, since package 2b, with the verdict on each
difference: all three answer different questions and are correct as they stand; the one real
defect was the *unit* of the subsidy display figure (W3.4, fixed) and the one missing
distinction was sign vs. band mirroring (W3.7, fixed by adding a fourth set).

The disagreement, in one table (rows = category, columns = the three constants)::

                        INVESTMENT_       FINANCING_YEAR0_   BREAKDOWN_INVESTMENT_
                        CATEGORIES        PRINCIPAL_         GROSS_
                        (perspective      CATEGORIES         CATEGORIES
                         filter)          (loan principal)   (display figure)
    INVESTMENT              yes               yes                yes
    PLANNING                yes               yes                yes
    REMOVAL                 yes               yes                yes
    REPLACEMENT             yes               no  (year > 0)     no  (year > 0)
    RESIDUAL_VALUE          yes               no                 no
    SUBSIDY                 yes               yes                no  (*)
    LOAN_*                  yes               no                 no
    ANYWAY_COST_CREDIT      yes               no                 no

They answer three different questions, which is why they may legitimately differ:

1. `INVESTMENT_CATEGORIES` — "which flows disappear when a perspective excludes investment?"
   (OPERATING_ONLY, cost_spec.md §4.2). Broadest: everything investment-*caused*, including
   the loan that financed it and the credits netted against it.
2. `FINANCING_YEAR0_PRINCIPAL_CATEGORIES` — "what does the loan principal get taken from?"
   (§4.4). Year-0 flows only, and SUBSIDY is *included* because the principal is computed on
   the investment **net of** upfront grants (subsidy entries are negative, so summing them in
   subtracts). Replacements are excluded because they land after year 0 and are always paid cash
   or from the §4.2 reserve — the engine has no replacement refinancing (§8 D23).
3. `BREAKDOWN_INVESTMENT_GROSS_CATEGORIES` — "what did buying this subject cost, before
   support?" (§7.4). Same three year-0 categories minus SUBSIDY, precisely because it is the
   *gross* figure; `ComponentCostBreakdown` reports the support separately.

   (*) The unit clash that used to sit here is fixed (W3.4, 2026-08-12): the breakdown now
   carries `subsidies_nominal_in_euro` *and* `subsidies_npv_in_euro`, each named after its unit,
   the latter being `npv_by_category[SUBSIDY]` mirrored to positive.

A fourth question, added by W3.7, is answered by a *fifth* set:

4. `NEGATIVE_SIGN_CATEGORIES` — "which entries are negative-signed?" This is **not**
   `REVENUE_CATEGORIES`, and conflating the two is why sign validation used to be off:
   "revenue-banded" (the optimistic world takes the band *maximum*, §3.9) and "negative-signed"
   are two properties. LOAN_DISBURSEMENT is negative but not revenue-banded — its band tracks
   the investment it finances — and MODERNIZATION_LEVY belongs to neither set because its sign
   depends on the *payer*, not the category: the tenant pays the rent increase, the landlord
   receives it (§6.4). `timeline.expected_sign(entry)` is therefore payer-aware and is the one
   authority on signs; `CashFlowTimeline.add` enforces it.

The four sets `timeline.py` owns (`REVENUE_CATEGORIES`, `NEGATIVE_SIGN_CATEGORIES`,
`INVESTMENT_CATEGORIES`, `SUBSIDY_FLOW_CATEGORIES`) keep their definition *there* and are
re-exported here rather than the other way around: `CostCategory` itself lives in `timeline.py`,
so defining them in this module and aliasing them back would make the kernel import the engine
and create an import cycle. This module is the place to *read* all of them together.

**What this module decides, and who consumes it.** It contains no arithmetic and no euro: it
encodes the *classification* decisions — "does this cost category belong to that question?" — on
which two figures then depend. `calculators/financing_application.compute_year0_net_investment`
reads `FINANCING_YEAR0_PRINCIPAL_CATEGORIES` to size the loan principal, and
`calculators/aggregation.build_breakdowns` reads `BREAKDOWN_INVESTMENT_GROSS_CATEGORIES` to fill
`ComponentCostBreakdown.investment_gross_in_euro`, the per-subject figure the frontend stacked
bars and the §9.5 audit table display. Of the re-exported `CategoryRules` sets,
`NEGATIVE_SIGN_CATEGORIES` is executed by `timeline.expected_sign` on every insertion and
`REVENUE_CATEGORIES` governs band mirroring (§3.9); `INVESTMENT_CATEGORIES` and
`SUBSIDY_FLOW_CATEGORIES` are declarative — the evaluator realizes the OPERATING_ONLY and
subsidy-mode perspectives by never *generating* those flows rather than by filtering them out
afterwards, and the sets document what that amounts to. A reviewer disagreeing with one of these
lists is disagreeing with exactly one published figure, which is the property the split was
built for.

Realizes: cost_spec.md §3.6 (categories), §4.2 (perspective filters), §4.4 (financing basis),
§5.5 (subsidy modes), §7.4 (breakdowns).
"""

from __future__ import annotations

from typing import Tuple

from hisim.economics.timeline import CategoryRules, CostCategory, expected_sign

__all__ = [
    "CategoryRules",
    "EngineCategoryRules",
    "expected_sign",
]


class EngineCategoryRules:
    """The engine-side category sets, read side by side with `CategoryRules` (W3.3).

    Two membership tests that only the *engine* asks, as opposed to the four taxonomy questions
    every timeline consumer asks (`timeline.CategoryRules`). Both are restricted to year 0 and
    both are summed over — one to size a loan, one to display what a subject cost — which is why
    they are tuples of categories rather than a general classification: the sets exist to be
    tested against `entry.category` inside a single `for entry in timeline.entries` loop.

    They are held in a class rather than as loose constants so the W3.3 comparison table in the
    module docstring has one object to point at, and so a future unification (or a deliberate
    decision not to unify) touches one place.
    """

    #: Year-0 categories summed into the net investment the loan principal is a share of (§4.4).
    #: SUBSIDY is deliberately in: its entries are negative, so the sum is *net* of upfront grants.
    FINANCING_YEAR0_PRINCIPAL_CATEGORIES: Tuple[CostCategory, ...] = (
        CostCategory.INVESTMENT,
        CostCategory.PLANNING,
        CostCategory.REMOVAL,
        CostCategory.SUBSIDY,
    )

    #: Year-0 categories summed into `ComponentCostBreakdown.investment_gross_in_euro` (§7.4).
    #: SUBSIDY is deliberately out: this is the figure *before* support.
    BREAKDOWN_INVESTMENT_GROSS_CATEGORIES: Tuple[CostCategory, ...] = (
        CostCategory.INVESTMENT,
        CostCategory.PLANNING,
        CostCategory.REMOVAL,
    )
