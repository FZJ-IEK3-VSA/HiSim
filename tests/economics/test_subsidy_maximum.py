"""Every subsidy row states the scheme's cap: ``max_amount_in_euro`` (renovisorissues #54).

The owner's rule (2026-09-26): the most the scheme can pay for the row's subject under the house
and the plan as stated, on every row whatever its status, signed and banded like
``amount_in_euro`` -- a LUMP_SUM its amount, TIERED_PER_UNIT the tier for the stated size, a
share of eligible cost the share times the eligible cost capped by the scheme's eligible-basis
cap, the larger rate where the rate hinges on an open question, and ``null`` only where the
catalogue defines no limit, with the row's note saying so.

The maximum is computed in the subsidy layer (:func:`~hisim.economics.subsidies.scheme_maximum`),
so its arithmetic is checked there, one case per benefit kind; the rows are checked on the
synthetic plan's document.
"""

from dataclasses import replace
from typing import Any, Dict, List

import pytest

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.facts import ComponentCostFacts
from hisim.economics.parameters import EconomicParameters
from hisim.economics.staged import StagedEvaluator
from hisim.economics.staged_document import StagedDocument
from hisim.economics.subsidies import (
    BenefitKind,
    Condition,
    EligibleCostSpec,
    LoanTermsBenefit,
    LumpSumBenefit,
    MeasureForSubsidy,
    OperationalBenefit,
    PayoutKind,
    PerUnitBenefit,
    ReducedVatBenefit,
    SchemeMaximumNotes,
    ShareBenefit,
    SubsidyContext,
    TaxCreditBenefit,
    Tier,
    TieredPerUnitBenefit,
    scheme_maximum,
    solve_cumulation,
)
from hisim.economics.timeline import CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

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

#: The measure's year-0 investment, planning and removal, in euro.
INVESTMENT, PLANNING, REMOVAL = 20000.0, 1000.0, 500.0

#: The heat pump's size, in kilowatts.
SIZE_IN_KW = 3.0


def _measure(energy_sold_in_kwh: float = 0.0) -> MeasureForSubsidy:
    """One heat pump measure, 21,500 EUR in year 0."""
    return MeasureForSubsidy(
        subject="HeatPump",
        facts=ComponentCostFacts(asset_class=ComponentType.HEAT_PUMP, size=SIZE_IN_KW, size_unit=Units.KILOWATT),
        measure_kind="REPLACE",
        cost_by_category={
            CostCategory.INVESTMENT: UncertainValue.exact(INVESTMENT),
            CostCategory.PLANNING: UncertainValue.exact(PLANNING),
            CostCategory.REMOVAL: UncertainValue.exact(REMOVAL),
        },
        annual_energy_sold_in_kwh={EnergyCarrier.ELECTRICITY: energy_sold_in_kwh} if energy_sold_in_kwh else {},
    )


def _scheme(scheme_id: str, kind: BenefitKind, benefit, payout=PayoutKind.UPFRONT_GRANT, **changes):
    """An always-eligible scheme of one benefit kind, with any field changed."""
    return replace(always_eligible_scheme(scheme_id, kind, benefit, payout), **changes)


def _maximum(scheme, measure=None, overall_cap_share=None):
    return scheme_maximum(scheme, measure or _measure(), SubsidyContext(), overall_cap_share)


class TestOneMaximumPerBenefitKind:
    """The arithmetic of each kind, the scheme valued alone."""

    def test_a_lump_sum_is_its_amount(self) -> None:
        """A fixed amount is its own maximum."""
        maximum = _maximum(_scheme("LUMP", BenefitKind.LUMP_SUM, LumpSumBenefit(amount=6500.0)))
        assert maximum.amount_in_euro == UncertainValue.exact(6500.0)
        assert maximum.note is None

    def test_a_lump_sum_above_the_eligible_cost_is_the_eligible_cost(self) -> None:
        """As an award is: a grant never exceeds the cost it funds."""
        maximum = _maximum(
            _scheme("PLANNING_LUMP", BenefitKind.LUMP_SUM, LumpSumBenefit(amount=2000.0),
                    eligible_cost=EligibleCostSpec(categories=[CostCategory.PLANNING]))
        )
        assert maximum.amount_in_euro == UncertainValue.exact(PLANNING)

    def test_a_per_unit_amount_is_for_the_stated_size(self) -> None:
        """The amount per kilowatt times the heat pump's kilowatts."""
        maximum = _maximum(
            _scheme("PER_KW", BenefitKind.PER_UNIT, PerUnitBenefit(amount=500.0, size_unit=Units.KILOWATT))
        )
        assert maximum.amount_in_euro == UncertainValue.exact(500.0 * SIZE_IN_KW)

    def test_a_tiered_amount_is_the_tier_for_the_stated_size(self) -> None:
        """The shape of SEAI's PV grant: 700 per kW up to 2, 200 per kW up to 4, at most 1,800."""
        benefit = TieredPerUnitBenefit(
            tiers=(Tier(up_to=2.0, amount_per_unit=700.0), Tier(up_to=4.0, amount_per_unit=200.0)),
            size_unit=Units.KILOWATT,
            cap_in_euro=1800.0,
        )
        maximum = _maximum(_scheme("TIERED", BenefitKind.TIERED_PER_UNIT, benefit))
        assert maximum.amount_in_euro == UncertainValue.exact(2 * 700.0 + 1 * 200.0)

    def test_a_share_is_the_rate_on_the_eligible_cost(self) -> None:
        """30 % of investment, planning and removal."""
        maximum = _maximum(_scheme("SHARE", BenefitKind.SHARE_OF_ELIGIBLE_COST, ShareBenefit(rate=0.3)))
        assert maximum.amount_in_euro is not None
        assert maximum.amount_in_euro.best_estimate == pytest.approx(0.3 * (INVESTMENT + PLANNING + REMOVAL))

    def test_a_share_is_capped_by_the_eligible_basis_cap(self) -> None:
        """30 % of at most 10,000 EUR of eligible cost."""
        maximum = _maximum(
            _scheme(
                "CAPPED_SHARE",
                BenefitKind.SHARE_OF_ELIGIBLE_COST,
                ShareBenefit(rate=0.3),
                eligible_cost=EligibleCostSpec(cap_per_dwelling_unit_in_euro=[10000.0]),
            )
        )
        assert maximum.amount_in_euro is not None
        assert maximum.amount_in_euro.best_estimate == pytest.approx(3000.0)

    def test_a_tax_credit_is_the_whole_credit(self) -> None:
        """Every instalment, not only the first."""
        maximum = _maximum(
            _scheme(
                "CREDIT", BenefitKind.TAX_CREDIT, TaxCreditBenefit(rate=0.2, years=3), PayoutKind.TAX_CREDIT_SCHEDULE
            )
        )
        assert maximum.amount_in_euro is not None
        assert maximum.amount_in_euro.best_estimate == pytest.approx(0.2 * (INVESTMENT + PLANNING + REMOVAL))

    def test_a_soft_loan_is_its_repayment_grant(self) -> None:
        """A loan pays no grant but the share of it written off."""
        maximum = _maximum(
            _scheme(
                "LOAN",
                BenefitKind.SOFT_LOAN,
                LoanTermsBenefit(interest_rate=0.01, term=10, repayment_grant_rate=0.1),
                PayoutKind.LOAN_TERMS,
            )
        )
        assert maximum.amount_in_euro is not None
        assert maximum.amount_in_euro.best_estimate == pytest.approx(0.1 * (INVESTMENT + PLANNING + REMOVAL))

    def test_a_soft_loan_without_a_repayment_grant_states_no_amount(self) -> None:
        """A loan with no grant element: null, not a zero band, and why (renovisorissues #65)."""
        maximum = _maximum(
            _scheme(
                "LOAN",
                BenefitKind.SOFT_LOAN,
                LoanTermsBenefit(interest_rate=0.03, term=10, repayment_grant_rate=0.0),
                PayoutKind.LOAN_TERMS,
            )
        )
        assert maximum.amount_in_euro is None
        assert maximum.note == SchemeMaximumNotes.SOFT_LOAN

    def test_an_operational_payment_is_rate_times_energy_times_duration(self) -> None:
        """A per-kWh payment over its whole duration."""
        maximum = _maximum(
            _scheme(
                "FEED_IN",
                BenefitKind.OPERATIONAL,
                OperationalBenefit(rate_per_kwh=0.05, carrier=EnergyCarrier.ELECTRICITY, duration_years=10),
                PayoutKind.OPERATIONAL,
            ),
            _measure(energy_sold_in_kwh=2000.0),
        )
        assert maximum.amount_in_euro == UncertainValue.exact(0.05 * 2000.0 * 10)

    def test_a_vat_reduction_states_no_limit_and_says_so(self) -> None:
        """The one kind whose catalogue entry states no amount: null, with the note."""
        maximum = _maximum(
            _scheme("VAT", BenefitKind.REDUCED_VAT, ReducedVatBenefit(vat_rate=0.0), PayoutKind.VAT_REDUCTION)
        )
        assert maximum.amount_in_euro is None
        assert maximum.note == SchemeMaximumNotes.REDUCED_VAT

    def test_the_overall_state_aid_share_bounds_it(self) -> None:
        """As it bounds an award: at most 10 % of the gross cost here."""
        maximum = _maximum(
            _scheme("SHARE", BenefitKind.SHARE_OF_ELIGIBLE_COST, ShareBenefit(rate=0.3)), overall_cap_share=0.1
        )
        assert maximum.amount_in_euro is not None
        assert maximum.amount_in_euro.best_estimate == pytest.approx(0.1 * (INVESTMENT + PLANNING + REMOVAL))


class TestTheDecisionStatesEveryScheme:
    """The solver records a maximum for every scheme it assessed, whatever the verdict."""

    #: A condition on an answer the default context does not give.
    OPEN_QUESTION = Condition(kind="leaf", fieldname="applicant.first_time_buyer", op="==", value=True)

    #: A condition the default context answers with no.
    ANSWERED_NO = Condition(kind="leaf", fieldname="applicant.actor", op="==", value="TENANT")

    def _decide(self, schemes) -> Any:
        return solve_cumulation(
            synthetic_catalog(schemes), _measure(), SubsidyContext(), SyntheticPlan.YEAR, lambda _year: 1.0
        )

    def test_an_awarded_an_ineligible_and_an_undetermined_scheme_each_have_one(self) -> None:
        """One maximum per assessed scheme, whatever its verdict."""
        decision = self._decide(
            [
                _scheme("AWARDED", BenefitKind.LUMP_SUM, LumpSumBenefit(amount=1000.0)),
                _scheme("REFUSED", BenefitKind.LUMP_SUM, LumpSumBenefit(amount=2000.0), eligibility=self.ANSWERED_NO),
                _scheme("OPEN", BenefitKind.LUMP_SUM, LumpSumBenefit(amount=2500.0), eligibility=self.OPEN_QUESTION),
            ]
        )
        assert [award.scheme_id for award in decision.applied] == ["AWARDED"]
        assert [row["scheme_id"] for row in decision.undetermined] == ["OPEN"]
        assert {scheme: maximum.amount_in_euro for scheme, maximum in decision.maximum_by_scheme.items()} == {
            "AWARDED": UncertainValue.exact(1000.0),
            "REFUSED": UncertainValue.exact(2000.0),
            "OPEN": UncertainValue.exact(2500.0),
        }

    def test_a_rate_that_hinges_on_an_open_question_is_stated_at_the_larger_value(self) -> None:
        """A bonus the group's combined-rate cap scales down in combination keeps its full rate.

        Base 30 % and a 20 % bonus in one group capped at 40 %: awarded together each is scaled
        by 0.8, and whether they are together hinges on the bonus's own answer. The row states
        what the scheme can pay: its full rate, the larger of the two.
        """
        base = _scheme(
            "BASE", BenefitKind.SHARE_OF_ELIGIBLE_COST, ShareBenefit(rate=0.3),
            cumulation_group="G", combined_rate_cap=0.4,
        )
        bonus = _scheme(
            "BONUS", BenefitKind.BONUS_SHARE, ShareBenefit(rate=0.2), cumulation_group="G", combined_rate_cap=0.4
        )
        decision = self._decide([base, bonus])
        eligible = INVESTMENT + PLANNING + REMOVAL
        awarded = {award.scheme_id: award.upfront_amount.best_estimate for award in decision.applied}
        assert awarded["BASE"] == pytest.approx(0.3 * 0.8 * eligible)
        maximum = decision.maximum_by_scheme["BASE"].amount_in_euro
        assert maximum is not None and maximum.best_estimate == pytest.approx(0.3 * eligible)


# ------------------------------------------------------------------------------------ the rows


def _parameters() -> EconomicParameters:
    return EconomicParameters(
        observation_period_in_years=SyntheticPlan.HORIZON,
        interest_rate=SyntheticPlan.INTEREST_RATE,
        country=SyntheticPlan.COUNTRY,
        price_basis_year=SyntheticPlan.YEAR,
        co2_price_scenario="none",
    )


def _document(tmp_path, catalog, from_year: int = 0) -> Dict[str, Any]:
    """The synthetic plan's validated document under one catalogue."""
    database = write_database(str(tmp_path / "database"))
    stages = [baseline_stage(), envelope_stage(0), heat_pump_stage(from_year)]
    perspective = brownfield_perspective(subsidies=True)
    result = StagedEvaluator(database).evaluate(stages, _parameters(), perspective, catalog)
    document = StagedDocument(result, _parameters(), perspective).to_json()
    StagedDocument.validate(document)
    StagedDocument.assert_bands_ordered(document)
    return document


def _rows(document: Dict[str, Any], variant: str = "plan") -> List[Dict[str, Any]]:
    return list(document[variant]["subsidies"])


class TestEveryRowStatesItsCap:
    """``max_amount_in_euro`` on every ``subsidies[]`` row, signed and banded like the amount."""

    def test_an_awarded_and_an_undetermined_row_state_their_caps_as_credits(self, tmp_path) -> None:
        """Negative bands, the undetermined row's the scheme's amount."""
        catalog = synthetic_catalog(
            [
                _scheme("SHARE", BenefitKind.SHARE_OF_ELIGIBLE_COST, ShareBenefit(rate=0.3)),
                _scheme(
                    "OPEN",
                    BenefitKind.LUMP_SUM,
                    LumpSumBenefit(amount=2500.0),
                    eligibility=TestTheDecisionStatesEveryScheme.OPEN_QUESTION,
                ),
            ]
        )
        rows = {(row["scheme"], row["stage"]): row for row in _rows(_document(tmp_path, catalog))}
        awarded = rows[("SHARE", 2)]
        assert awarded["status"] == "awarded"
        assert awarded["max_amount_in_euro"] == awarded["amount_in_euro"]
        assert awarded["max_amount_in_euro"]["best"] == pytest.approx(
            -0.3 * SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO
        )
        undetermined = rows[("OPEN", 2)]
        assert undetermined["status"] == "undetermined"
        assert undetermined["amount_in_euro"] is None
        assert undetermined["max_amount_in_euro"] == {"min": -2500.0, "best": -2500.0, "max": -2500.0}
        assert undetermined["note"] is None
        for row in rows.values():
            assert "max_amount_in_euro" in row

    def test_a_later_stages_cap_is_moved_into_its_year_like_its_award(self, tmp_path) -> None:
        """The awarded row's cap equals its amount when nothing combined cut it, stage 2 in year 4."""
        catalog = synthetic_catalog([_scheme("SHARE", BenefitKind.SHARE_OF_ELIGIBLE_COST, ShareBenefit(rate=0.3))])
        document = _document(tmp_path, catalog, from_year=4)
        for row in _rows(document):
            assert row["max_amount_in_euro"]["best"] == pytest.approx(row["amount_in_euro"]["best"])

    def test_a_row_with_no_limit_is_null_and_says_so(self, tmp_path) -> None:
        """A VAT reduction states a rate on the price, no amount."""
        catalog = synthetic_catalog(
            [_scheme("VAT", BenefitKind.REDUCED_VAT, ReducedVatBenefit(vat_rate=0.0), PayoutKind.VAT_REDUCTION)]
        )
        for row in _rows(_document(tmp_path, catalog)):
            assert row["max_amount_in_euro"] is None
            assert SchemeMaximumNotes.REDUCED_VAT in (row["note"] or "")

    def test_an_open_soft_loan_without_a_repayment_grant_is_null_and_says_so(self, tmp_path) -> None:
        """IE_HEULS_LOAN's case: undetermined, no grant element, so no maximum and why (#65)."""
        catalog = synthetic_catalog(
            [
                _scheme(
                    "LOAN",
                    BenefitKind.SOFT_LOAN,
                    LoanTermsBenefit(interest_rate=0.03, term=10, repayment_grant_rate=0.0),
                    PayoutKind.LOAN_TERMS,
                    eligibility=TestTheDecisionStatesEveryScheme.OPEN_QUESTION,
                )
            ]
        )
        rows = _rows(_document(tmp_path, catalog))
        assert rows
        for row in rows:
            assert row["status"] == "undetermined"
            assert row["max_amount_in_euro"] is None
            assert row["max_amount_for_measure_in_euro"] is None
            assert row["note"] == SchemeMaximumNotes.SOFT_LOAN

    def test_a_plan_without_a_catalogue_states_null_and_says_why(self, tmp_path) -> None:
        """No scheme, no limit: the undetermined rows carry null and the note names it."""
        for row in _rows(_document(tmp_path, None)):
            assert row["max_amount_in_euro"] is None
            assert "no maximum" in row["note"]


class TestTheMeasuresMaximum:
    """``max_amount_for_measure_in_euro``: the scheme's maximum over all of a measure's subjects (#65).

    The synthetic ``heating_system`` measure buys a heat pump and a buffer in stage 2; a scheme
    covering both classes pays towards each, one row each, and "up to EUR X" for the measure is
    the sum of the two.
    """

    RATE = 0.3

    def test_every_row_of_the_measure_states_the_sum_of_its_subjects(self, tmp_path) -> None:
        """Two rows, each with its own maximum and the measure's, 30 % of heat pump + buffer."""
        from tests.economics.test_investment_overrides import (  # pylint: disable=import-outside-toplevel
            BUFFER_INVESTMENT_IN_EURO,
            MEASURE_IDS,
            _parameters as override_parameters,
            _stages,
        )

        scheme = _scheme(
            "SHARE",
            BenefitKind.SHARE_OF_ELIGIBLE_COST,
            ShareBenefit(rate=self.RATE),
            asset_classes=[ComponentType.HEAT_PUMP, ComponentType.SPACE_HEATING_STORAGE],
        )
        database = write_database(str(tmp_path / "database"))
        perspective = brownfield_perspective(subsidies=True)
        result = StagedEvaluator(database).evaluate(
            _stages(), override_parameters(), perspective, synthetic_catalog([scheme])
        )
        document = StagedDocument(result, override_parameters(), perspective, measure_ids=MEASURE_IDS).to_json()
        StagedDocument.validate(document)
        rows = [
            row
            for row in document["plan"]["subsidies"]
            if row["scheme"] == "SHARE" and row["measure_id"] == "heating_system"
        ]
        assert len(rows) == 2
        own = sorted(row["max_amount_in_euro"]["best"] for row in rows)
        assert own == pytest.approx(
            sorted([-self.RATE * SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO, -self.RATE * BUFFER_INVESTMENT_IN_EURO])
        )
        for row in rows:
            assert row["max_amount_for_measure_in_euro"] == {
                slot: pytest.approx(sum(other["max_amount_in_euro"][slot] for other in rows))
                for slot in ("min", "best", "max")
            }
        assert rows[0]["max_amount_for_measure_in_euro"]["best"] == pytest.approx(
            -self.RATE * (SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO + BUFFER_INVESTMENT_IN_EURO)
        )
