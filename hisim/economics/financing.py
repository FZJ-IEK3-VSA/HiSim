"""Annuity loan model, optionally subsidized (cost_spec.md §4.4).

A loan barely changes the net present cost but changes the liquidity profile completely, replacing one year-0 outflow
with years of payments (§4.3); that monthly figure is what the `owner_monthly` perspective shows. This module holds the
plan description and the pure schedule mathematics. The principal basis, repayment grants and timeline entries are
decided in `calculators/financing_application.py`; soft-loan overrides come from the subsidy package.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import List, Optional, Tuple

from hisim.economics.uncertainty import UncertainValue


class LoanType(str, enum.Enum):
    """Repayment shape of a loan.

    `ANNUITY` (German Annuitätendarlehen) pays a constant total whose interest share shrinks over the term; it is the
    default. `INTEREST_ONLY_WITH_BULLET` (endfälliges Darlehen) pays interest only and repays the whole principal at
    the end. Both produce LOAN_INTEREST and LOAN_PRINCIPAL entries, so only `loan_flows` branches on the type.
    """

    ANNUITY = "ANNUITY"
    INTEREST_ONLY_WITH_BULLET = "INTEREST_ONLY_WITH_BULLET"


@dataclass
class FinancingPlan:
    """How the year-0 investment is financed, attached to a `Perspective` (None there means a cash purchase).

    It is a plan, not a schedule: how much is borrowed, at what rate, over how long and in what shape; `loan_flows`
    turns it into cash flows. Only the year-0 investment is financed; replacements inside the horizon are paid in cash
    or from the §4.2 reserve.

    Attributes:
        financed_share: Share of the year-0 investment net of upfront grants that is borrowed, in [0, 1].
        nominal_interest_rate: Annual loan rate as a fraction; zero is allowed.
        term_in_years: Loan term in years, at least 1.
        type: The repayment shape.
        subsidized_by_scheme_id: A SOFT_LOAN subsidy scheme (§5.3) that may override rate and term, or None.
        repayment_grant_share: Repayment grant (Tilgungszuschuss) as a share of the principal, set by a SOFT_LOAN
            scheme.
    """

    financed_share: float = 1.0  # of net investment after upfront subsidies
    nominal_interest_rate: float = 0.04
    term_in_years: int = 20
    type: LoanType = LoanType.ANNUITY
    # A subsidized-loan scheme (§5.3 SoftLoan) can override rate/term and add a repayment grant.
    subsidized_by_scheme_id: Optional[str] = None
    # Repayment grant (Tilgungszuschuss) share of the principal, set by a SOFT_LOAN scheme.
    repayment_grant_share: float = 0.0

    def __post_init__(self) -> None:
        """Check the share and the term.

        The interest rate is not constrained, since zero-interest subsidized loans exist.

        Raises:
            ValueError: If `financed_share` is outside [0, 1] or `term_in_years` is below 1.
        """
        if not 0.0 <= self.financed_share <= 1.0:
            raise ValueError("financed_share must be within [0, 1].")
        if self.term_in_years < 1:
            raise ValueError("term_in_years must be >= 1.")


def loan_flows(
    plan: FinancingPlan,
    principal: UncertainValue,
) -> Tuple[UncertainValue, List[Tuple[int, UncertainValue, UncertainValue]]]:
    """Return the disbursement and the yearly interest and repayment of a loan on `principal`.

    INTEREST_ONLY_WITH_BULLET pays `principal * rate` every year and the whole principal in the last year. ANNUITY pays
    a constant `principal * i(1+i)^n / ((1+i)^n - 1)`, split into interest on the outstanding debt and repayment; at a
    zero rate the principal is spread evenly. Everything is computed per band slot (minimum, best estimate, maximum):
    the loan follows the investment band, so the expensive world also has the large loan. Signs, categories and horizon
    truncation are left to `calculators/financing_application.py`.

    Args:
        plan: The financing plan, already resolved against any soft-loan override.
        principal: The amount borrowed, as a band.

    Returns:
        The disbursement (equal to the principal, positive) and `(year, interest, principal_repayment)` triples for
            years
        1..term.
    """
    schedule: List[Tuple[int, UncertainValue, UncertainValue]] = []
    rate = plan.nominal_interest_rate
    term = plan.term_in_years
    if plan.type == LoanType.INTEREST_ONLY_WITH_BULLET:
        for year in range(1, term + 1):
            interest = principal.scale(rate)
            repayment = principal if year == term else UncertainValue.exact(0.0)
            schedule.append((year, interest, repayment))
        return principal, schedule

    # Annuity loan: constant annuity, split into interest and principal per year.
    if rate == 0.0:
        annuity = principal.scale(1.0 / term)
        for year in range(1, term + 1):
            schedule.append((year, UncertainValue.exact(0.0), annuity))
        return principal, schedule

    annuity_factor = rate * (1.0 + rate) ** term / ((1.0 + rate) ** term - 1.0)
    annuity = principal.scale(annuity_factor)
    remaining = principal
    for year in range(1, term + 1):
        interest = remaining.scale(rate)
        repayment = annuity - interest
        # Guard against slot-wise rounding pushing the last repayment past the remaining debt.
        if year == term:
            repayment = remaining
        remaining = remaining - repayment
        schedule.append((year, interest, repayment))
    return principal, schedule
