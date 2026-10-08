"""Lay the loan flows of a perspective's financing plan onto the timeline (cost_spec.md §2.3, §4.4).

The loan mathematics is in `financing.loan_flows`. This module computes what is financed (the year-0 investment net of
upfront grants), applies a soft-loan scheme's terms to the plan (§5.3), and emits a LOAN_DISBURSEMENT offsetting the
year-0 outflow, the optional repayment grant, and the interest and principal schedule truncated at the horizon.

Known discrepancy: the subsidy solver values a repayment grant on the gross measure cost, while this module applies
`repayment_grant_share` to the loan principal, so the two differ whenever `financed_share < 1`. The shipped KfW
repayment grant rate is 0.0, so no shipped run is affected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from hisim.economics.calculators.categories import EngineCategoryRules
from hisim.economics.financing import FinancingPlan, loan_flows
from hisim.economics.subsidies import PayoutKind, SubsidyDecision
from hisim.economics.timeline import CashFlowEntry, CashFlowTimeline, CostCategory
from hisim.economics.uncertainty import UncertainValue


class FinancingConstants:
    """Labels of the financing flows (§4.4).

    A loan finances the investment as a whole, so its flows are booked under one synthetic subject. The name appears in
    `cash_flow_timeline.csv`, the per-subject breakdowns and the report's amortization chart.
    """

    #: Timeline subject the loan flows are booked under (they belong to no single component).
    FINANCING_SUBJECT = "financing"


@dataclass(frozen=True)
class Year0NetInvestment:
    """The year-0 investment net of upfront support: what a loan share is taken of (§4.4).

    A cost-positive euro band in year-0 money, undiscounted. Only upfront subsidies are netted, because only they
    reduce what the borrower has to raise on the day. Produced by :func:`compute_year0_net_investment` and consumed by
    :func:`build_financing_flows`; the separate type keeps it from being confused with the gross investment or the levy
    basis.
    """

    amount: UncertainValue

    @property
    def is_financeable(self) -> bool:
        """Whether anything is left to finance after upfront support, tested on the band maximum.

        A band has three slots (minimum, best estimate, maximum), one per price world. Testing the maximum makes "is
        there a loan" one decision for all three slots and finances whenever any world has something left.
        """
        return self.amount.maximum > 0

    @property
    def financed_basis(self) -> UncertainValue:
        """The amount a loan can be taken out on: the net investment, clamped to zero per slot.

        Example: upfront grants of EUR 11,500 against EUR 7,600 of investment in the cheap world give a net of EUR
        -3,900, so that slot's basis is 0. This happens because grants are revenue-mirrored, so the cheap slot pairs
        the largest grant with the cheapest investment. Clamping each slot keeps the band ordered and leaves
        non-negative slots unchanged.

        Returns:
            A cost-positive band whose three slots are each ``max(0, net)``.
        """
        return UncertainValue(
            best_estimate=self._non_negative(self.amount.best_estimate),
            minimum=self._non_negative(self.amount.minimum),
            maximum=self._non_negative(self.amount.maximum),
        )

    @staticmethod
    def _non_negative(value: float) -> float:
        """Return ``value`` itself when it is not negative, and zero when it is."""
        return value if value >= 0.0 else 0.0


def compute_year0_net_investment(timeline: CashFlowTimeline) -> Year0NetInvestment:
    """Sum the year-0 investment categories net of upfront subsidies (§4.4).

    SUBSIDY entries are negative, so including them makes the figure net (see `calculators/categories.py`). The sum
    runs in entry order. Reading it off the timeline makes the principal match what the timeline charges at year 0,
    removal and planning costs included. `evaluator.build_timeline` calls this after every subject is costed.

    Args:
        timeline: The timeline so far; only year-0 entries in `FINANCING_YEAR0_PRINCIPAL_CATEGORIES` are read.

    Returns:
        The net year-0 outflow as a cost-positive euro band, undiscounted; zero or negative when upfront grants cover
            the
        investment.
    """
    total = UncertainValue.exact(0.0)
    for entry in timeline.entries:
        if entry.year == 0 and entry.category in EngineCategoryRules.FINANCING_YEAR0_PRINCIPAL_CATEGORIES:
            total = total + entry.amount_in_euro
    return Year0NetInvestment(amount=total)


def resolve_loan_plan(plan: FinancingPlan, decisions: List[SubsidyDecision]) -> FinancingPlan:
    """Apply a soft-loan scheme's LOAN_TERMS award to the financing plan (§5.3).

    A soft loan (KfW-style) is a subsidy whose benefit is better terms: a lower rate, a longer term, and possibly a
    repayment grant (Tilgungszuschuss) that writes off a share of the principal. Only an award whose scheme is the
    plan's `subsidized_by_scheme_id` applies; when several match, the last one wins.

    Args:
        plan: The perspective's financing plan.
        decisions: The perspective's subsidy decisions in subject order; only applied LOAN_TERMS awards are read.

    Returns:
        The plan itself when no award matches, otherwise a new plan with the award's rate, term and repayment-grant
            share.
        Each field the award states (is not None) overrides the plan's, so a stated 0.0 % rate is kept; an unset field
        keeps the plan's value.
    """
    loan_plan = plan
    for decision in decisions:
        for award in decision.applied:
            if award.payout_kind == PayoutKind.LOAN_TERMS and award.scheme_id == plan.subsidized_by_scheme_id:
                loan_plan = FinancingPlan(
                    financed_share=plan.financed_share,
                    nominal_interest_rate=(
                        award.loan_interest_rate
                        if award.loan_interest_rate is not None
                        else plan.nominal_interest_rate
                    ),
                    term_in_years=(
                        award.loan_term_in_years if award.loan_term_in_years is not None else plan.term_in_years
                    ),
                    type=plan.type,
                    subsidized_by_scheme_id=plan.subsidized_by_scheme_id,
                    repayment_grant_share=(
                        award.loan_repayment_grant_share
                        if award.loan_repayment_grant_share is not None
                        else plan.repayment_grant_share
                    ),
                )
    return loan_plan


def build_financing_flows(
    loan_plan: FinancingPlan,
    year0_net: Year0NetInvestment,
    horizon: int,
) -> List[CashFlowEntry]:
    """Return the loan flows that replace a share of the year-0 outflow (§4.4).

    Nothing is emitted when nothing is left to finance. A slot in which upfront grants exceed the investment finances
    nothing (:attr:`Year0NetInvestment.financed_basis`). The schedule stops at the horizon, so debt from a longer term
    is left outstanding. Financing barely changes the NPV but reshapes the nominal annual series, which answers "can I
    afford this" (§4.3).

    Args:
        loan_plan: The plan after :func:`resolve_loan_plan`.
        year0_net: The year-0 investment net of upfront subsidies.
        horizon: Observation period T in years; schedule rows beyond it are dropped.

    Returns:
        A negative LOAN_DISBURSEMENT at year 0, optionally a negative SUBSIDY entry for the repayment grant, and
        cost-positive LOAN_INTEREST and LOAN_PRINCIPAL entries per year, each only when non-zero; nominal euros of
            their
        year, undiscounted.
    """
    if not year0_net.is_financeable:
        return []
    entries: List[CashFlowEntry] = []
    principal = year0_net.financed_basis.scale(loan_plan.financed_share)
    disbursement, schedule = loan_flows(loan_plan, principal)
    entries.append(
        CashFlowEntry(
            year=0,
            amount_in_euro=disbursement.as_revenue(),
            category=CostCategory.LOAN_DISBURSEMENT,
            subject=FinancingConstants.FINANCING_SUBJECT,
        )
    )
    if loan_plan.repayment_grant_share > 0:
        # The subsidy solver valued this grant on the gross measure cost, not on the principal.
        grant = principal.scale(loan_plan.repayment_grant_share)
        entries.append(
            CashFlowEntry(
                year=0,
                amount_in_euro=grant.as_revenue(),
                category=CostCategory.SUBSIDY,
                subject=FinancingConstants.FINANCING_SUBJECT,
                subsidy_scheme_id=loan_plan.subsidized_by_scheme_id,
            )
        )
    for year, interest, repayment in schedule:
        if year > horizon:
            break
        if interest.maximum:
            entries.append(
                CashFlowEntry(
                    year=year,
                    amount_in_euro=interest,
                    category=CostCategory.LOAN_INTEREST,
                    subject=FinancingConstants.FINANCING_SUBJECT,
                )
            )
        if repayment.maximum:
            entries.append(
                CashFlowEntry(
                    year=year,
                    amount_in_euro=repayment,
                    category=CostCategory.LOAN_PRINCIPAL,
                    subject=FinancingConstants.FINANCING_SUBJECT,
                )
            )
    return entries
