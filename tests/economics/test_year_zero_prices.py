"""Year-0 prices are escalated from the price basis year to ``plan_start_year`` (renovisorissues #62).

Owner decision (cmf, 2026-09-27): a plan's year 0 is ``plan_start_year`` for dates and ageing
(#841), and from now on for money too. Prices are read at the price basis year and every amount is
escalated to ``plan_start_year`` with the rate it already escalates with in later years -- the
investment rate for purchases, the general rate for maintenance, the carrier rates for energy --
except a reader's quote and a fixed-amount grant. A plan starting before its price basis year is
de-escalated by the same law. Without a start year, or with one equal to the price basis year,
every figure is bit-identical to before.

The synthetic plan of ``test_investment_overrides.py`` is priced at 2024; a start year of 2025 is
one year of escalation.
"""

from dataclasses import replace
from typing import Any, Dict, Optional

import pytest

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.evaluator import EconomicEvaluator, YearZeroPriceLevel
from hisim.economics.financing import FinancingPlan
from hisim.economics.parameters import EconomicParameters
from hisim.economics.staged import StagedEvaluator, StagedResult
from hisim.economics.staged_document import StagedDocument
from hisim.economics.staged_parameters import StagedParameters
from hisim.economics.subsidies import BenefitKind, LumpSumBenefit, PayoutKind
from hisim.economics.timeline import CashFlowEntry, CostCategory
from hisim.economics.uncertainty import UncertainValue

from tests.economics.synthetic_stages import (
    SyntheticPlan,
    always_eligible_catalog,
    always_eligible_scheme,
    brownfield_perspective,
    entry_signature,
    synthetic_catalog,
    write_database,
)
from tests.economics.test_investment_overrides import (
    HEATING_QUOTE,
    QUOTE,
    _entries,
    _stages,
)

pytestmark = pytest.mark.base

#: The investment escalation rate of every case.
RATE = 0.02

#: The general escalation rate of every case, deliberately different from the investment rate.
GENERAL = 0.03

#: The price basis year of the synthetic database.
BASIS = SyntheticPlan.YEAR

#: A lump sum every synthetic measure qualifies for, in euro.
LUMP_SUM = 2000.0


@pytest.fixture(name="database", scope="module")
def fixture_database(tmp_path_factory):
    """The synthetic cost database."""
    return write_database(str(tmp_path_factory.mktemp("year_zero_database")))


def _parameters() -> EconomicParameters:
    """The synthetic assumptions with a 2 % investment and a 3 % general escalation rate."""
    return EconomicParameters(
        observation_period_in_years=SyntheticPlan.HORIZON,
        interest_rate=SyntheticPlan.INTEREST_RATE,
        country=SyntheticPlan.COUNTRY,
        price_basis_year=BASIS,
        co2_price_scenario="none",
        investment_price_escalation_rate=RATE,
        general_price_escalation_rate=GENERAL,
    )


def _price(database, start: Optional[int], catalog=None, overrides=(), perspective=None) -> StagedResult:
    """Price the synthetic plan starting in ``start`` (None: no start year)."""
    return StagedEvaluator(database).evaluate(
        _stages(),
        _parameters(),
        perspective or brownfield_perspective(subsidies=catalog is not None),
        catalog,
        plan_start_year=start,
        investment_overrides=overrides,
    )


def _lump_sum_catalog():
    """One lump sum of EUR 2,000 every synthetic measure qualifies for."""
    return synthetic_catalog(
        [
            always_eligible_scheme(
                "LUMP", BenefitKind.LUMP_SUM, LumpSumBenefit(amount=LUMP_SUM), PayoutKind.UPFRONT_GRANT
            )
        ]
    )


def _one(result: StagedResult, subject: str, category: CostCategory, year: int) -> float:
    """The best estimate of one subject's one entry of one category in one plan year."""
    (amount,) = [value for at, value in _entries(result, subject, category) if at == year]
    return float(amount)


def _document(result: StagedResult) -> Dict[str, Any]:
    """The validated document of a priced plan."""
    document = StagedDocument(result, _parameters(), brownfield_perspective(subsidies=True)).to_json()
    StagedDocument.validate(document)
    return document


class TestAStartYearEqualToTheBasisYear:
    """start = basis: every entry bit-identical to a plan without a start year."""

    @pytest.mark.parametrize("catalog", [None, always_eligible_catalog(0.3)], ids=["cash", "grant"])
    def test_every_entry_is_bit_identical(self, database, catalog) -> None:
        """Reference, every stage and the plan, entry for entry; the totals too."""
        plain, dated = _price(database, None, catalog), _price(database, BASIS, catalog)
        for before, after in [(plain.reference, dated.reference), (plain.plan, dated.plan)] + list(
            zip(plain.per_stage, dated.per_stage)
        ):
            assert entry_signature(after.timeline.entries) == entry_signature(before.timeline.entries)
            assert after.total_npv_in_euro == before.total_npv_in_euro

    def test_the_echo_states_no_escalation(self, database) -> None:
        """``origins.price_level`` is absent."""
        assert "price_level" not in _document(_price(database, BASIS))["parameters"]["origins"]


class TestAStartYearOneYearAfterTheBasisYear:
    """start = basis + 1 under a 2 % investment rate."""

    @pytest.fixture(name="pair", scope="class")
    def fixture_pair(self, database):
        """The plan priced without a start year and starting one year after the basis year."""
        return _price(database, None), _price(database, BASIS + 1)

    def test_a_year_0_database_purchase_is_one_year_of_the_investment_rate_dearer(self, pair) -> None:
        """18,000 x 1.02 in year 0."""
        _plain, later = pair
        assert _one(later, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.INVESTMENT, 0) == pytest.approx(
            SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO * (1.0 + RATE)
        )

    def test_a_replacement_in_year_y_is_escalated_y_plus_one_years(self, pair) -> None:
        """The heat pump (life 10) is re-bought in year 10 at 18,000 x 1.02**11."""
        plain, later = pair
        year = int(SyntheticPlan.LIFETIME_IN_YEARS)
        assert _one(plain, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.REPLACEMENT, year) == pytest.approx(
            SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO * (1.0 + RATE) ** year
        )
        assert _one(later, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.REPLACEMENT, year) == pytest.approx(
            SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO * (1.0 + RATE) ** (year + 1)
        )

    def test_an_energy_price_in_year_1_carries_one_more_year_of_its_rate(self, database, pair) -> None:
        """Year 1's electricity bill is the basis year's times (1 + the carrier rate)."""
        plain, later = pair
        rate = EconomicEvaluator(database, _parameters()).carrier_escalation_rate(EnergyCarrier.ELECTRICITY)
        subject = EnergyCarrier.ELECTRICITY.value
        for year in (1, 5):
            assert _one(later, subject, CostCategory.ENERGY_WORKING, year) == pytest.approx(
                _one(plain, subject, CostCategory.ENERGY_WORKING, year) * (1.0 + rate)
            )

    def test_maintenance_carries_one_more_year_of_the_general_rate(self, pair) -> None:
        """Maintenance escalates with the general rate, so its extra year is 3 %, not 2 %."""
        plain, later = pair
        subject = SyntheticPlan.HEAT_PUMP_SUBJECT
        assert _one(later, subject, CostCategory.MAINTENANCE, 1) == pytest.approx(
            _one(plain, subject, CostCategory.MAINTENANCE, 1) * (1.0 + GENERAL)
        )

    def test_the_reference_is_in_the_same_money(self, database, pair) -> None:
        """The comparison compares like with like: the reference's bill is escalated too."""
        plain, later = pair
        rate = EconomicEvaluator(database, _parameters()).carrier_escalation_rate(EnergyCarrier.ELECTRICITY)
        before = [e for e in plain.reference.timeline.entries if e.category is CostCategory.ENERGY_WORKING]
        after = [e for e in later.reference.timeline.entries if e.category is CostCategory.ENERGY_WORKING]
        assert before
        assert [e.amount_in_euro.best_estimate for e in after] == pytest.approx(
            [e.amount_in_euro.best_estimate * (1.0 + rate) for e in before]
        )

    def test_the_echo_states_both_years(self, pair) -> None:
        """``origins.price_level`` names the year prices were read at and the year of the money."""
        _plain, later = pair
        origins = _document(later)["parameters"]["origins"]
        assert origins["price_level"] == {"from_year": BASIS, "to_year": BASIS + 1}


class TestTheExemptions:
    """A quote and a lump sum are booked as stated; a share-of-cost grant follows its cost."""

    def test_a_quote_in_year_0_is_unchanged(self, database) -> None:
        """11,800 as stated, and the buffer bought within it at zero."""
        later = _price(database, BASIS + 1, overrides=[HEATING_QUOTE])
        assert _entries(later, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.INVESTMENT)[0] == (0, QUOTE)

    def test_a_quoted_subjects_replacement_is_still_escalated(self, database) -> None:
        """The re-purchase is a database price: (y + 1) years of the investment rate."""
        later = _price(database, BASIS + 1, overrides=[HEATING_QUOTE])
        year = int(SyntheticPlan.LIFETIME_IN_YEARS)
        assert _one(later, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.REPLACEMENT, year) == pytest.approx(
            SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO * (1.0 + RATE) ** (year + 1)
        )

    def test_a_lump_sum_in_year_0_is_unchanged(self, database) -> None:
        """EUR 2,000, and its row's maximum too."""
        later = _price(database, BASIS + 1, catalog=_lump_sum_catalog())
        assert _entries(later, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.SUBSIDY) == [(0, -LUMP_SUM)]
        (row,) = [
            row
            for row in _document(later)["plan"]["subsidies"]
            if row["stage"] == 2 and row["status"] == "awarded"
        ]
        assert row["max_amount_in_euro"]["best"] == pytest.approx(-LUMP_SUM)

    def test_a_lump_sum_above_the_cost_is_clamped_to_the_escalated_cost(self, database) -> None:
        """EUR 20,000 on an 18,000 heat pump a year later: 18,360, not the basis year's 18,000 (#65).

        The lump sum is nominal and never escalated, but the cost it is clamped to is the cost in
        the year it is paid in. Clamping it at the price basis year and booking it unescalated left
        a credit below the year-0 cost it was capped by (IE_SEAI_EXTERNAL_WALL_DETACHED: 7,000
        beside a year-0 cost of 7,140). The row's maximum is the same figure.
        """
        big = 20000.0
        catalog = synthetic_catalog(
            [always_eligible_scheme("BIG", BenefitKind.LUMP_SUM, LumpSumBenefit(amount=big), PayoutKind.UPFRONT_GRANT)]
        )
        later = _price(database, BASIS + 1, catalog=catalog)
        cost = sum(
            amount
            for category in (CostCategory.INVESTMENT, CostCategory.PLANNING, CostCategory.REMOVAL)
            for year, amount in _entries(later, SyntheticPlan.HEAT_PUMP_SUBJECT, category)
            if year == 0
        )
        assert cost < big
        assert cost == pytest.approx(SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO * (1.0 + RATE))
        assert _entries(later, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.SUBSIDY) == [(0, pytest.approx(-cost))]
        (row,) = [
            row
            for row in _document(later)["plan"]["subsidies"]
            if row["stage"] == 2 and row["status"] == "awarded"
        ]
        assert row["max_amount_in_euro"]["best"] == pytest.approx(-cost)

    def test_a_share_of_cost_grant_follows_its_escalated_cost(self, database) -> None:
        """30 % of 18,000 x 1.02, and its row's maximum with it."""
        later = _price(database, BASIS + 1, catalog=always_eligible_catalog(0.3))
        expected = -0.3 * SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO * (1.0 + RATE)
        assert _entries(later, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.SUBSIDY) == [
            (0, pytest.approx(expected))
        ]
        rows = [
            row
            for row in _document(later)["plan"]["subsidies"]
            if row["stage"] == 2 and row["status"] == "awarded"
        ]
        assert rows
        for row in rows:
            assert row["max_amount_in_euro"]["best"] == pytest.approx(row["amount_in_euro"]["best"])

    def test_a_share_of_a_quote_stays_a_share_of_the_quote(self, database) -> None:
        """30 % of 11,800 as stated."""
        later = _price(database, BASIS + 1, catalog=always_eligible_catalog(0.3), overrides=[HEATING_QUOTE])
        assert _entries(later, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.SUBSIDY) == [
            (0, pytest.approx(-0.3 * QUOTE))
        ]


class TestTheLoanFollows:
    """The loan is taken out on the escalated year-0 net investment, a quote as stated."""

    FINANCED_SHARE = 0.8

    def _financed(self):
        """The brownfield perspective with 80 % financed over six years."""
        return replace(
            brownfield_perspective(), financing=FinancingPlan(financed_share=self.FINANCED_SHARE, term_in_years=6)
        )

    @staticmethod
    def _principal(result: StagedResult) -> float:
        """Everything disbursed in year 0, as a positive best estimate."""
        disbursed = _entries(result, "financing", CostCategory.LOAN_DISBURSEMENT)
        return -float(sum(amount for at, amount in disbursed if at == 0))

    def test_the_loan_finances_the_escalated_prices_and_the_quote_as_stated(self, database) -> None:
        """Principal ratio: database prices one year dearer, the quoted heat pump not."""
        plain = _price(database, None, perspective=self._financed())
        later = _price(database, BASIS + 1, perspective=self._financed())
        assert self._principal(later) == pytest.approx(self._principal(plain) * (1.0 + RATE))
        quoted_plain = _price(database, None, perspective=self._financed(), overrides=[HEATING_QUOTE])
        quoted_later = _price(database, BASIS + 1, perspective=self._financed(), overrides=[HEATING_QUOTE])
        unquoted_share = self._principal(quoted_plain) - self.FINANCED_SHARE * QUOTE
        assert self._principal(quoted_later) == pytest.approx(
            self.FINANCED_SHARE * QUOTE + unquoted_share * (1.0 + RATE)
        )


class TestAStartYearBeforeTheBasisYear:
    """start = basis - 1: the same law de-escalates."""

    def test_a_year_0_purchase_is_one_year_of_the_investment_rate_cheaper(self, database) -> None:
        """18,000 / 1.02, and the echo says from 2024 to 2023."""
        earlier = _price(database, BASIS - 1)
        assert _one(earlier, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.INVESTMENT, 0) == pytest.approx(
            SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO / (1.0 + RATE)
        )
        assert _document(earlier)["parameters"]["origins"]["price_level"] == {"from_year": BASIS, "to_year": BASIS - 1}


class TestTheWorkingPrice:
    """The flexibility correction is shifted at the spread rate, the volume effect at the carrier rate."""

    def test_both_halves_carry_their_own_rate(self, database) -> None:
        """``V c**(t-1) - F s**(t-1)`` becomes ``V c**(t-1+d) - F s**(t-1+d)``."""
        volume, flexibility, carrier, spread, year = 1000.0, 100.0, 0.04, 0.01, 3
        level = YearZeroPriceLevel(years=2, parameters=_parameters(), database=database, price_basis_year=BASIS)
        level.carriers[EnergyCarrier.ELECTRICITY.value] = (carrier, spread, flexibility)
        booked = volume * (1 + carrier) ** (year - 1) - flexibility * (1 + spread) ** (year - 1)
        entry = CashFlowEntry(
            year=year,
            amount_in_euro=UncertainValue.exact(booked),
            category=CostCategory.ENERGY_WORKING,
            subject=EnergyCarrier.ELECTRICITY.value,
        )
        shifted = level.entry(entry).amount_in_euro.best_estimate
        assert shifted == pytest.approx(volume * (1 + carrier) ** (year + 1) - flexibility * (1 + spread) ** (year + 1))

    def test_a_category_built_after_the_shift_is_refused(self, database) -> None:
        """The loan is never shifted entry by entry: it is taken out on shifted figures."""
        level = YearZeroPriceLevel(years=1, parameters=_parameters(), database=database, price_basis_year=BASIS)
        entry = CashFlowEntry(
            year=1, amount_in_euro=UncertainValue.exact(1.0), category=CostCategory.LOAN_INTEREST, subject="financing"
        )
        with pytest.raises(ValueError, match="LOAN_INTEREST"):
            level.entry(entry)


class TestTheEchoHelper:
    """``origins.price_level`` is written exactly when the two years differ."""

    @pytest.mark.parametrize(
        "basis, start, expected",
        [
            (2026, None, None),
            (2026, 2026, None),
            (None, 2027, None),
            (2026, 2027, {"from_year": 2026, "to_year": 2027}),
            (2026, 2025, {"from_year": 2026, "to_year": 2025}),
        ],
    )
    def test_the_rule(self, basis, start, expected) -> None:
        """Both years or nothing."""
        assert StagedParameters.price_level(basis, start) == expected
