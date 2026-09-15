"""The operating view's replacement reserve (cost-spec-v2 §2.3).

The §2.3 "replacement reserve" calculator. Under the OPERATING_ONLY installation context the
perspective sees no capital expenditure at all — no year-0 investment, no REPLACEMENT entries.
Charging nothing for the wear of equipment that will have to be replaced would understate the
cost of operating, so the replacements are instead levelized into a **sinking fund**: their
present value at the discount rate, spread over the observation period with the annuity factor,
paid as an equal REPLACEMENT_RESERVE amount in every year 1..T (cost_spec.md §4.2).

Note that the replacement flows this works on are collected by the investment calculator even
when the REPLACEMENT entries themselves are suppressed — that is exactly the OPERATING_ONLY case
this fund exists for.

Realizes: cost_spec.md §4.2 (operating view), §3.4 (discounting and the annuity factor).
"""

from __future__ import annotations

from typing import List, Tuple

from hisim.economics.parameters import EconomicParameters
from hisim.economics.timeline import CashFlowEntry, CostCategory
from hisim.economics.uncertainty import UncertainValue


class ReserveConstants:
    """Labels of the replacement-reserve flows (§4.2).

    The reserve is levelized across *all* subjects at once, so its entries cannot be attributed to
    any one component; they are booked under a synthetic subject name instead. That name is a
    published string — it appears as a row in `cash_flow_timeline.csv`, in the per-subject
    breakdowns and in the report charts — so it lives here as a constant rather than as a literal
    at the emit site.
    """

    #: Timeline subject the reserve is booked under (it belongs to no single component).
    RESERVE_SUBJECT = "replacement reserve"


def build_replacement_reserve_entries(
    replacement_flows: List[Tuple[int, UncertainValue]],
    parameters: EconomicParameters,
    horizon: int,
) -> List[CashFlowEntry]:
    """The annual sinking-fund payment covering the suppressed replacements (§4.2).

    Discounts every replacement to today, annuitizes the sum, and emits that amount in each
    year 1..T. The fold over the flows is left-to-right in collection order, which is the order
    the subjects were processed in — kept because float addition is not associative.

    The closed form is the standard sinking-fund/capital-recovery pair: `a * sum_t(F_t / (1+i)^t)`
    with `a` the VDI 2067-1 annuity factor over the same horizon and interest rate. The result is
    a *level nominal* payment — deliberately not escalated, because it is already the annuity of
    escalated future prices — and it is the German Instandhaltungsrücklage figure a homeowner or
    a WEG should set aside. It exists only under OPERATING_ONLY; every other perspective charges
    the replacements themselves and would double count if it also charged a reserve.

    Args:
        replacement_flows: `(year, amount)` pairs from every subject's `InvestmentSchedule`, in
            collection order. Amounts are nominal, already escalated to their year, cost-positive
            euro bands; the years are relative to the investment date.
        parameters: Economic parameters — supplies the discount factor and the annuity factor.
        horizon: Observation period T in years; one entry is emitted per year 1..T.

    Returns:
        T identical cost-positive REPLACEMENT_RESERVE entries, one per year, in euro per year.
        An empty `replacement_flows` list still yields T zero-valued entries, so the caller only
        invokes this when there is something to reserve for.
    """
    discounted_band = UncertainValue.exact(0.0)
    for repl_year, amount in replacement_flows:
        discounted_band = discounted_band + amount.scale(parameters.discount_factor(repl_year))
    reserve = discounted_band.scale(parameters.annuity_factor())
    return [
        CashFlowEntry(
            year=year,
            amount_in_euro=reserve,
            category=CostCategory.REPLACEMENT_RESERVE,
            subject=ReserveConstants.RESERVE_SUBJECT,
        )
        for year in range(1, horizon + 1)
    ]
