"""Basic financing model: annuity loan, optionally subsidized (cost_spec.md §4.4).

Financing exists in the engine for one question the NPV cannot answer: "what does this cost me per
month?". A loan barely changes the net present cost — only the spread between the loan rate and the
discount rate does — but it changes the *liquidity* profile completely (§4.3), replacing a single
year-0 outflow with two decades of manageable payments. That is the figure a homeowner decides on,
so the shipped `owner_monthly` perspective carries a financing plan while the others do not.

This module owns the plan description and the pure schedule mathematics, nothing else. What the
loan principal is computed from (year-0 investment *net* of upfront grants), when a repayment grant
is booked and how the resulting flows become timeline entries is decided by
`calculators/financing_application.py`; which soft-loan scheme may override the rate and term is
decided by `subsidies.py`. Keeping `loan_flows` free of that context is what makes it
straightforwardly checkable against a textbook amortization table.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import List, Optional, Tuple

from hisim.economics.uncertainty import UncertainValue


class LoanType(str, enum.Enum):
    """Supported loan types.

    The two repayment shapes that matter for a household retrofit. `ANNUITY` is the ordinary German
    Annuitätendarlehen: a constant total payment whose interest share shrinks and principal share
    grows over the term — the default and by far the common case. `INTEREST_ONLY_WITH_BULLET`
    (endfälliges Darlehen) pays interest only and repays the whole principal at the end; it is kept
    because some subsidized and bridge financings work that way, and because it is the extreme case
    against which the annuity schedule's interest total can be sanity-checked.

    Both produce the same LOAN_INTEREST / LOAN_PRINCIPAL categories, so nothing downstream branches
    on the type; only `loan_flows` does.
    """

    ANNUITY = "ANNUITY"
    INTEREST_ONLY_WITH_BULLET = "INTEREST_ONLY_WITH_BULLET"


@dataclass
class FinancingPlan:
    """Financing of the year-0 investment (needed for 'monthly cost for the owners').

    The declarative description of how the investment is paid for, attached to a `Perspective`
    (`None` there means a cash purchase). It is deliberately a *plan*, not a schedule: it says how
    much is borrowed, at what rate, over how long and in what shape, and `loan_flows` turns that
    into cash flows. Loading it from `perspectives_default.json` or from a request therefore needs
    no code, and a scenario axis can sweep the interest rate like any other assumption.

    Two fields connect it to the rest of the engine. `subsidized_by_scheme_id` names a SOFT_LOAN
    scheme (§5.3) that may override the rate and term and add a `repayment_grant_share`
    (Tilgungszuschuss) — the override is applied by `calculators/financing_application.py` before
    any flow is emitted. `financed_share` is a share of the year-0 investment **net of upfront
    grants**, which is why the subsidy category is part of the principal basis (see
    `calculators/categories.py`).

    The plan finances the year-0 investment and nothing else: replacements falling inside the
    horizon are always paid from cash or from the §4.2 reserve. A `refinance_replacements` flag
    used to sit here promising otherwise while no code read it; refinancing replacements is a
    possible future feature and would need a schedule of its own (§8 D23).
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
        """Validation of the two fields whose misuse would silently distort the schedule.

        A share outside [0, 1] would either invent money or finance more than the investment, and a
        term below one year has no annuity. The interest rate is intentionally unconstrained:
        zero-interest subsidized loans exist and are handled by an explicit branch in `loan_flows`.

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
    """Computes loan cash flows for a financed principal.

    Returns ``(disbursement, [(year, interest, principal_repayment), ...])`` with years 1..term.
    All figures are slot-wise on the principal band (the loan follows the slot's investment).

    The pure closed-form half of §4.4: given a plan and a principal it produces the full
    amortization table and nothing else — no timeline entries, no category or sign decisions, no
    horizon truncation, all of which belong to `calculators/financing_application.py`. Two
    schedules are implemented, matching `LoanType`:

    * **INTEREST_ONLY_WITH_BULLET** — `principal x rate` every year, the entire principal repaid in
      the final year.
    * **ANNUITY** — a constant annuity `principal x i(1+i)^n/((1+i)^n - 1)` split each year into
      interest on the outstanding debt and the remainder as principal repayment, so the interest
      share falls as the debt does. At a zero rate the annuity formula is 0/0, so a separate branch
      spreads the principal evenly, which is the correct limit.

    Slot semantics: the loan follows the *investment* band rather than being mirrored like a
    revenue. The world in which the heat pump is expensive is the world in which the loan is large,
    its interest high and its disbursement large — which is exactly why LOAN_DISBURSEMENT is a
    negative-signed but **not** band-mirrored category (see `timeline.CategoryRules`). The returned
    disbursement is positive here; the caller negates it when booking the entry.

    Args:
        plan: The financing plan, already resolved against any soft-loan override.
        principal: The amount actually borrowed, i.e. `financed_share` of the year-0 net
            investment, as a band.

    Returns:
        The disbursement (equal to the principal, positive) and the schedule as
        `(year, interest, principal_repayment)` triples for years 1..term, all as bands.
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
