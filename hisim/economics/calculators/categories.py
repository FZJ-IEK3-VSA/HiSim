"""The cost-category sets the engine tests membership against, gathered in one place (cost_spec.md §3.6).

A cost category is the kind of a timeline entry (INVESTMENT, SUBSIDY, LOAN_INTEREST, ...). Different questions need
different sets, so the sets legitimately differ:

    category              INVESTMENT_   FINANCING_YEAR0_    BREAKDOWN_INVESTMENT_
                          CATEGORIES    PRINCIPAL_          GROSS_
    INVESTMENT              yes           yes                 yes
    PLANNING                yes           yes                 yes
    REMOVAL                 yes           yes                 yes
    REPLACEMENT             yes           no  (year > 0)      no  (year > 0)
    RESIDUAL_VALUE          yes           no                  no
    SUBSIDY                 yes           yes                 no
    LOAN_*                  yes           no                  no
    ANYWAY_COST_CREDIT      yes           no                  no

- `INVESTMENT_CATEGORIES`: what disappears when a perspective excludes investment (OPERATING_ONLY, §4.2).
- `FINANCING_YEAR0_PRINCIPAL_CATEGORIES`: what the loan principal is a share of (§4.4); includes SUBSIDY because the
  principal is net of upfront grants.
- `BREAKDOWN_INVESTMENT_GROSS_CATEGORIES`: what buying a subject cost before support (§7.4).
- `NEGATIVE_SIGN_CATEGORIES`: which entries are negative-signed. This differs from `REVENUE_CATEGORIES` (entries whose
  optimistic band slot is the maximum, §3.9): LOAN_DISBURSEMENT is negative but follows the investment band, and
  MODERNIZATION_LEVY's sign depends on the payer (§6.4), which `timeline.expected_sign` handles.

The `CategoryRules` sets are defined in `timeline.py`, next to `CostCategory`, and re-exported here to avoid an import
cycle.
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
    """The two category sets only the engine asks about, both restricted to year 0.

    `FINANCING_YEAR0_PRINCIPAL_CATEGORIES` sizes a loan and `BREAKDOWN_INVESTMENT_GROSS_CATEGORIES` fills a subject's
    gross investment figure; the timeline-wide sets are in `timeline.CategoryRules`.
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
