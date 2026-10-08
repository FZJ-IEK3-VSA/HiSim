"""The replacement reserve of the operating-only view (cost_spec.md §2.3, §4.2).

Under the OPERATING_ONLY installation context a perspective (one named way of looking at the costs) sees no capital
expenditure. So that equipment wear is still charged, the replacements are levelized into a sinking fund: their present
value, spread over years 1..T with the annuity factor, paid as an equal REPLACEMENT_RESERVE amount each year. The
investment calculator collects the replacement flows even when their REPLACEMENT entries are suppressed.
"""

from __future__ import annotations

from typing import List, Tuple

from hisim.economics.parameters import EconomicParameters
from hisim.economics.timeline import CashFlowEntry, CostCategory
from hisim.economics.uncertainty import UncertainValue


class ReserveConstants:
    """Labels of the replacement-reserve flows (§4.2).

    The reserve covers all subjects at once, so its entries are booked under one synthetic subject name. That name
    appears in `cash_flow_timeline.csv`, the per-subject breakdowns and the report charts.
    """

    #: Timeline subject the reserve is booked under (it belongs to no single component).
    RESERVE_SUBJECT = "replacement reserve"


def replacement_reserve_amount(
    replacement_flows: List[Tuple[int, UncertainValue]],
    parameters: EconomicParameters,
) -> UncertainValue:
    """Return the level annual sinking-fund payment that a set of replacement flows implies (§4.2).

    Example: `replacement_reserve_amount([(10, UncertainValue.exact(20000.0))], parameters)`. The staged evaluator
    calls this on its own, without the entries, because a plan's replacements fall in different years than any single
    stage's.

    Args:
        replacement_flows: `(year, amount)` pairs, nominal, escalated to their year and cost-positive. They are summed
            left to right in the given order, because float addition is not associative and the total is published.
        parameters: Economic parameters; supply the discount factor and the annuity factor.

    Returns:
        The equal amount paid in each year 1..T; an exact zero band for an empty list.
    """
    discounted_band = UncertainValue.exact(0.0)
    for repl_year, amount in replacement_flows:
        discounted_band = discounted_band + amount.scale(parameters.discount_factor(repl_year))
    return discounted_band.scale(parameters.annuity_factor())


def build_replacement_reserve_entries(
    replacement_flows: List[Tuple[int, UncertainValue]],
    parameters: EconomicParameters,
    horizon: int,
) -> List[CashFlowEntry]:
    """Return the annual sinking-fund entries that cover the suppressed replacements (§4.2).

    The amount is `a * sum_t(F_t / (1+i)^t)` with `a` the VDI 2067-1 annuity factor over the horizon: every replacement
    is discounted to today and the sum annuitized. The payment is level and nominal, not escalated, because it is
    already the annuity of escalated future prices. Only OPERATING_ONLY uses it; other perspectives charge the
    replacements themselves.

    Args:
        replacement_flows: `(year, amount)` pairs from every subject's `InvestmentSchedule`, in collection order;
            amounts are nominal, escalated to their year and cost-positive, years relative to the investment date.
        parameters: Economic parameters; supply the discount factor and the annuity factor.
        horizon: Observation period T in years; one entry is emitted per year 1..T.

    Returns:
        T identical cost-positive REPLACEMENT_RESERVE entries in euro per year. An empty list still yields T zero
            entries.
    """
    reserve = replacement_reserve_amount(replacement_flows, parameters)
    return [
        CashFlowEntry(
            year=year,
            amount_in_euro=reserve,
            category=CostCategory.REPLACEMENT_RESERVE,
            subject=ReserveConstants.RESERVE_SUBJECT,
        )
        for year in range(1, horizon + 1)
    ]
