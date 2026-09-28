"""A later stage's tax-credit instalments and per-kWh payments, placed in the plan by hand.

hisim-staged-tax-credit-placement-nvz7. A stage's own evaluation books a tax credit's instalments
and an operational payment in its own years 1..N. The splice used to treat them as operating
flows -- kept only in plan years whose active stage was the paying one, at the stage's own year
index -- so a stage starting in year 4 lost the instalments of its own years 1..3, and a stage-0
credit was cut off when the next stage started. The owner principle (2026-09-27): a tax credit is
a share of the cost the stage books, so it follows that cost and is dated from the stage's start,
every instalment kept; an operational payment is a fixed nominal rate per kWh, dated from the
stage's start, never escalated, and kept while the installation that earns it is in the house.

Every expectation is hand-computed from the synthetic plan's declared figures: the heat pump's
EUR 18,000 and the insulation's EUR 24,000 at year-0 prices, a 2 % investment escalation rate, a
20 % credit over three even instalments, a EUR 0.05/kWh payment on 1,500 kWh sold for five years.
"""

from dataclasses import replace
from typing import List, Tuple

import pytest

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.parameters import EconomicParameters
from hisim.economics.staged import Stage, StagedEvaluator
from hisim.economics.subsidies import (
    BenefitKind,
    OperationalBenefit,
    PayoutKind,
    SubsidyCatalog,
    TaxCreditBenefit,
)
from hisim.economics.timeline import CostCategory

from tests.economics.synthetic_stages import (
    SyntheticPlan,
    always_eligible_scheme,
    baseline_stage,
    brownfield_perspective,
    envelope_stage,
    heat_pump_stage,
    synthetic_catalog,
    write_database,
)

pytestmark = pytest.mark.base

#: The investment escalation rate of every plan here.
RATE = 0.02

#: The tax credit's share of the eligible cost, and its instalments.
CREDIT_RATE = 0.2
CREDIT_YEARS = 3

#: The operational payment: rate, energy sold per year, duration.
PAYMENT_RATE = 0.05
SOLD = SyntheticPlan.SOLD_ELECTRICITY_IN_KWH
PAYMENT_YEARS = 5

TAX_CREDIT_SCHEME = "SYNTHETIC_TAX_CREDIT"
OPERATIONAL_SCHEME = "SYNTHETIC_OPERATIONAL"


@pytest.fixture(name="database", scope="module")
def fixture_database(tmp_path_factory):
    """The synthetic cost database, written once for the module."""
    return write_database(str(tmp_path_factory.mktemp("later_subsidies_database")))


def _parameters() -> EconomicParameters:
    """The synthetic assumptions with a 2 % investment escalation rate and subsidies on."""
    return EconomicParameters(
        observation_period_in_years=SyntheticPlan.HORIZON,
        interest_rate=SyntheticPlan.INTEREST_RATE,
        country=SyntheticPlan.COUNTRY,
        price_basis_year=SyntheticPlan.YEAR,
        co2_price_scenario="none",
        investment_price_escalation_rate=RATE,
    )


def _tax_credit_catalog() -> SubsidyCatalog:
    """A 20 % tax credit over three even instalments on the heat pump and the insulation."""
    return synthetic_catalog(
        [
            always_eligible_scheme(
                TAX_CREDIT_SCHEME,
                BenefitKind.TAX_CREDIT,
                TaxCreditBenefit(rate=CREDIT_RATE, years=CREDIT_YEARS),
                PayoutKind.TAX_CREDIT_SCHEDULE,
            )
        ]
    )


def _operational_catalog() -> SubsidyCatalog:
    """EUR 0.05 per kWh of electricity sold, for five years, on the heat pump and the insulation."""
    return synthetic_catalog(
        [
            always_eligible_scheme(
                OPERATIONAL_SCHEME,
                BenefitKind.OPERATIONAL,
                OperationalBenefit(
                    rate_per_kwh=PAYMENT_RATE, carrier=EnergyCarrier.ELECTRICITY, duration_years=PAYMENT_YEARS
                ),
                PayoutKind.OPERATIONAL,
            )
        ]
    )


def _subsidies(database, stages: List[Stage], catalog: SubsidyCatalog, subject: str) -> List[Tuple[int, float]]:
    """``(plan year, best estimate)`` of every SUBSIDY entry of one subject on the plan's timeline."""
    result = StagedEvaluator(database).evaluate(
        stages, _parameters(), brownfield_perspective(subsidies=True), catalog
    )
    return sorted(
        (entry.year, entry.amount_in_euro.best_estimate)
        for entry in result.plan.timeline.entries
        if entry.category is CostCategory.SUBSIDY and entry.subject == subject
    )


class TestTaxCreditInstalments:
    """A credit is a share of the cost the stage books, dated from the stage, every instalment kept."""

    def test_a_stage_starting_in_year_4_books_its_instalments_in_years_5_to_7(self, database):
        """20 % of EUR 18,000 x 1.02^4, in three instalments of EUR 1,298.92, in plan years 5, 6, 7.

        Before the fix all three were dropped: the stage's own years 1..3 lie before it starts.
        The credit is valued on the cost at the stage's price level once -- 1.02^4, not 1.02^8.
        """
        instalment = CREDIT_RATE * SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO * (1 + RATE) ** 4 / CREDIT_YEARS
        booked = _subsidies(
            database, [baseline_stage(), heat_pump_stage(4)], _tax_credit_catalog(), SyntheticPlan.HEAT_PUMP_SUBJECT
        )
        assert [year for year, _ in booked] == [5, 6, 7]
        for _, amount in booked:
            assert amount == pytest.approx(-instalment, abs=1e-9)
        assert instalment == pytest.approx(1298.918592, abs=1e-6)

    def test_a_year_0_credit_runs_on_into_the_next_stage(self, database):
        """The insulation's EUR 1,600 instalments stay in years 1, 2 and 3 though the heat pump starts in 2.

        Before the fix years 2 and 3 were dropped, because the heat-pump stage is active then. The
        heat pump's own credit, 20 % of EUR 18,000 x 1.02^2 over three, lands in years 3, 4, 5.
        """
        stages = [baseline_stage(), envelope_stage(0), heat_pump_stage(2)]
        envelope = _subsidies(database, stages, _tax_credit_catalog(), SyntheticPlan.ENVELOPE_SUBJECT)
        assert envelope == [(1, pytest.approx(-1600.0)), (2, pytest.approx(-1600.0)), (3, pytest.approx(-1600.0))]
        heat_pump = _subsidies(database, stages, _tax_credit_catalog(), SyntheticPlan.HEAT_PUMP_SUBJECT)
        instalment = CREDIT_RATE * SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO * (1 + RATE) ** 2 / CREDIT_YEARS
        assert heat_pump == [(year, pytest.approx(-instalment)) for year in (3, 4, 5)]

    def test_an_instalment_past_the_horizon_is_dropped(self, database):
        """A stage in year 11 of a 12-year horizon keeps its year-12 instalment and drops 13 and 14."""
        booked = _subsidies(
            database, [baseline_stage(), heat_pump_stage(11)], _tax_credit_catalog(), SyntheticPlan.HEAT_PUMP_SUBJECT
        )
        assert [year for year, _ in booked] == [12]


class TestOperationalPayments:
    """A per-kWh payment is nominal, dated from the stage, and runs while its installation does."""

    def test_a_later_stage_is_paid_from_its_own_start_unescalated(self, database):
        """EUR 0.05 x 1,500 kWh = EUR 75 a year in plan years 4..8 for a heat pump bought in year 3."""
        booked = _subsidies(
            database,
            [baseline_stage(), heat_pump_stage(3, electricity_sold_in_kwh=SOLD)],
            _operational_catalog(),
            SyntheticPlan.HEAT_PUMP_SUBJECT,
        )
        assert booked == [(year, pytest.approx(-PAYMENT_RATE * SOLD)) for year in range(4, 9)]

    def test_a_stage_that_keeps_the_installation_does_not_stop_the_payment(self, database):
        """A third stage in year 6 keeps the heat pump: years 7 and 8 are still paid, once each."""
        keeping = replace(heat_pump_stage(6, electricity_sold_in_kwh=SOLD), label="stage 3")
        booked = _subsidies(
            database,
            [baseline_stage(), heat_pump_stage(3, electricity_sold_in_kwh=SOLD), keeping],
            _operational_catalog(),
            SyntheticPlan.HEAT_PUMP_SUBJECT,
        )
        assert booked == [(year, pytest.approx(-PAYMENT_RATE * SOLD)) for year in range(4, 9)]

    def test_a_stage_that_removes_the_installation_ends_the_payment(self, database):
        """A third stage in year 6 without the heat pump: only years 4 and 5 are paid."""
        removing = replace(envelope_stage(6), label="stage 3")
        booked = _subsidies(
            database,
            [baseline_stage(), heat_pump_stage(3, electricity_sold_in_kwh=SOLD), removing],
            _operational_catalog(),
            SyntheticPlan.HEAT_PUMP_SUBJECT,
        )
        assert booked == [(year, pytest.approx(-PAYMENT_RATE * SOLD)) for year in (4, 5)]
