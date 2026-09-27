"""A loan finances max(0, net investment) in every slot (renovisorissues #66).

The frontend's Irish detached house (external wall insulation at EUR 50-70/m² on 120 m², solid
ground floor insulation at EUR 20-50/m² on 80 m², the other two measures unpriced) failed its
lifecycle cost evaluation: the SEAI wall grant (EUR 8,000) and floor grant (EUR 3,500) are capped
at the eligible cost per slot and then mirrored as revenues (§3.9), so the LOW slot pairs the
largest grants with the cheapest investment. The year-0 net investment was the band
``(-3900, 0, 4800)``, the loan was taken out on it as it stood, and its disbursement
``(-4800, -0, 3900)`` broke the sign convention. The loan now finances nothing in the slot where
the grants exceed the investment; a loan on a positive net investment is bit-identical to before.
"""

from types import SimpleNamespace
from typing import List

import pytest

from hisim.economics.calculators.financing_application import (
    Year0NetInvestment,
    build_financing_flows,
    compute_year0_net_investment,
)
from hisim.economics.financing import FinancingPlan, loan_flows
from hisim.economics.staged import StagedEvaluator, _StageCharges
from hisim.economics.timeline import CashFlowEntry, CashFlowTimeline, CostCategory
from hisim.economics.uncertainty import UncertainValue

pytestmark = pytest.mark.base

HORIZON = 30


def _year_zero_of_issue_66() -> List[CashFlowEntry]:
    """The year-0 investment and grants of the frontend's calculation, slot by slot.

    Wall: EUR 50/60/70 per m² on 120 m² against a EUR 8,000 grant capped at the cost. Floor:
    EUR 20/35/50 per m² on 80 m² against a EUR 3,500 grant capped at the cost. The window and the
    warm roof carry no price and contribute exact zeros.
    """

    def grant(investment: UncertainValue, amount: float) -> UncertainValue:
        return investment.clamp_upper(UncertainValue.exact(amount)).as_revenue()

    wall = UncertainValue(best_estimate=7200.0, minimum=6000.0, maximum=8400.0)
    floor = UncertainValue(best_estimate=2800.0, minimum=1600.0, maximum=4000.0)
    return [
        CashFlowEntry(year=0, amount_in_euro=wall, category=CostCategory.INVESTMENT, subject="wall"),
        CashFlowEntry(
            year=0,
            amount_in_euro=grant(wall, 8000.0),
            category=CostCategory.SUBSIDY,
            subject="wall",
            subsidy_scheme_id="IE_SEAI_EXTERNAL_WALL_DETACHED",
        ),
        CashFlowEntry(year=0, amount_in_euro=UncertainValue.ZERO, category=CostCategory.INVESTMENT, subject="roof"),
        CashFlowEntry(year=0, amount_in_euro=floor, category=CostCategory.INVESTMENT, subject="floor"),
        CashFlowEntry(
            year=0,
            amount_in_euro=grant(floor, 3500.0),
            category=CostCategory.SUBSIDY,
            subject="floor",
            subsidy_scheme_id="IE_SEAI_OSS_FLOOR",
        ),
        CashFlowEntry(
            year=0, amount_in_euro=UncertainValue.ZERO, category=CostCategory.INVESTMENT, subject="window"
        ),
    ]


def _assert_sign_clean_and_ordered(entries: List[CashFlowEntry]) -> None:
    """Every loan flow passes the §3.9 sign check and keeps its band ordered."""
    timeline = CashFlowTimeline()
    for entry in entries:
        timeline.add(entry)  # raises on a sign violation
        band = entry.amount_in_euro
        assert band.minimum <= band.best_estimate <= band.maximum


class TestGrantsExceedingTheInvestmentInOneSlot:
    """The #66 reproduction, through the engine's own year-0 fold."""

    def test_the_net_investment_is_the_issues_band(self) -> None:
        """The fold reproduces the figures the frontend saw, so the test is the bug."""
        timeline = CashFlowTimeline()
        for entry in _year_zero_of_issue_66():
            timeline.add(entry)
        net = compute_year0_net_investment(timeline).amount
        assert (net.minimum, net.best_estimate, net.maximum) == (-3900.0, 0.0, 4800.0)

    def test_the_loan_is_zero_where_the_grants_exceed_the_investment(self) -> None:
        """No exception, nothing borrowed in LOW (or BEST, where the net is zero), the HIGH net in full."""
        timeline = CashFlowTimeline()
        for entry in _year_zero_of_issue_66():
            timeline.add(entry)
        flows = build_financing_flows(FinancingPlan(), compute_year0_net_investment(timeline), HORIZON)

        _assert_sign_clean_and_ordered(flows)
        disbursement = next(flow for flow in flows if flow.category is CostCategory.LOAN_DISBURSEMENT)
        assert disbursement.amount_in_euro.minimum == -4800.0
        assert disbursement.amount_in_euro.best_estimate == 0.0
        assert disbursement.amount_in_euro.maximum == 0.0
        for flow in flows:
            if flow.category is not CostCategory.LOAN_DISBURSEMENT:
                assert flow.amount_in_euro.minimum == 0.0
                assert flow.amount_in_euro.best_estimate == 0.0
                assert flow.amount_in_euro.maximum > 0.0

    def test_the_financed_basis_is_the_net_clamped_per_slot(self) -> None:
        """``max(0, x)`` per slot; a non-negative slot keeps its value, a signed zero included."""
        net = Year0NetInvestment(UncertainValue(best_estimate=-0.0, minimum=-3900.0, maximum=4800.0))
        basis = net.financed_basis
        assert (basis.minimum, basis.best_estimate, basis.maximum) == (0.0, 0.0, 4800.0)


class TestAnOrdinaryLoanIsUnchanged:
    """A positive net investment is financed exactly as before the fix."""

    def test_flows_are_bit_identical_to_the_schedule_on_the_raw_net(self) -> None:
        """Disbursement and schedule equal ``loan_flows`` on the unclamped principal, float for float."""
        net = UncertainValue(best_estimate=12345.678, minimum=9876.54321, maximum=15000.000001)
        plan = FinancingPlan(financed_share=0.8, nominal_interest_rate=0.037, term_in_years=15)

        flows = build_financing_flows(plan, Year0NetInvestment(net), HORIZON)

        disbursement, schedule = loan_flows(plan, net.scale(plan.financed_share))
        assert flows[0].amount_in_euro == disbursement.as_revenue()
        expected = []
        for year, interest, repayment in schedule:
            expected.append((year, CostCategory.LOAN_INTEREST, interest))
            expected.append((year, CostCategory.LOAN_PRINCIPAL, repayment))
        assert [(flow.year, flow.category, flow.amount_in_euro) for flow in flows[1:]] == expected


class TestTheStagedLoan:
    """The staged splice re-takes a stage's loan from its booked year-0 flows by the same rule."""

    def test_a_stage_whose_grants_exceed_its_investment_in_one_slot(self) -> None:
        """The #66 year 0 as a stage starting in year 3: no exception, zero loan in LOW, dated from year 3."""
        result = SimpleNamespace(
            timeline=SimpleNamespace(entries=_year_zero_of_issue_66()),
            subsidy_decisions=[],
        )
        charges = _StageCharges(charged={}, carried_over=set(), rates={}, default_rate=0.0)

        flows = StagedEvaluator._stage_loan(  # pylint: disable=protected-access
            result, 3, charges, FinancingPlan(), HORIZON  # type: ignore[arg-type]
        )

        _assert_sign_clean_and_ordered(flows)
        disbursement = next(flow for flow in flows if flow.category is CostCategory.LOAN_DISBURSEMENT)
        assert disbursement.year == 3
        assert (disbursement.amount_in_euro.minimum, disbursement.amount_in_euro.maximum) == (-4800.0, 0.0)
