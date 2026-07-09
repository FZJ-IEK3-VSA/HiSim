"""Basic financing model: annuity loan, optionally subsidized (cost_spec.md §4.4)."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import List, Optional, Tuple

from hisim.economics.uncertainty import UncertainValue


class LoanType(str, enum.Enum):
    """Supported loan types."""

    ANNUITY = "ANNUITY"
    INTEREST_ONLY_WITH_BULLET = "INTEREST_ONLY_WITH_BULLET"


@dataclass
class FinancingPlan:
    """Financing of the year-0 investment (needed for 'monthly cost for the owners')."""

    financed_share: float = 1.0  # of net investment after upfront subsidies
    nominal_interest_rate: float = 0.04
    term_in_years: int = 20
    type: LoanType = LoanType.ANNUITY
    # A subsidized-loan scheme (§5.3 SoftLoan) can override rate/term and add a repayment grant.
    subsidized_by_scheme_id: Optional[str] = None
    # Replacements within the horizon are paid cash / from the reserve by default.
    refinance_replacements: bool = False
    # Repayment grant (Tilgungszuschuss) share of the principal, set by a SOFT_LOAN scheme.
    repayment_grant_share: float = 0.0

    def __post_init__(self) -> None:
        """Validation."""
        if not 0.0 <= self.financed_share <= 1.0:
            raise ValueError("financed_share must be within [0, 1].")
        if self.term_in_years < 1:
            raise ValueError("term_in_years must be >= 1.")


def loan_flows(
    plan: FinancingPlan,
    principal: UncertainValue,
) -> Tuple[UncertainValue, List[Tuple[int, UncertainValue, UncertainValue]]]:
    """Computes loan cash flows for a financed principal.

    Returns ``(disbursement, [(year, interest, principal_repayment), ...])`` with years 1..term.
    All figures are slot-wise on the principal band (the loan follows the slot's investment).
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
