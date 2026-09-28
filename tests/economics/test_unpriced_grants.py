"""A fixed-amount grant on an unpriced subject: no cap, no award, a question (renovisorissues #77).

An envelope measure without a ``cost`` block is booked at a placeholder zero investment and
flagged unpriced: its price is unknown, not zero. A fixed-amount grant (LUMP_SUM, PER_UNIT,
TIERED_PER_UNIT) is clamped to the eligible cost, so against that zero it used to read "up to
EUR 0" -- production's "rafter insulation grant, up to EUR 0". Owner decision of 2026-09-28: the
scheme's maximum is ``null`` with a note saying why, and a scheme the answers would award is not
awarded (nothing is booked) but left ``undetermined`` on the measure's price. A reader's quote
prices the purchase, and the scheme is then decided as for any priced measure.
"""

from dataclasses import replace
from typing import Any, Dict, List

import pytest

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.facts import ComponentCostFacts
from hisim.economics.parameters import EconomicParameters
from hisim.economics.serialization import facts_from_json, facts_to_json
from hisim.economics.staged import Stage, StagedEvaluator
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
    SchemeMaximumNotes,
    ShareBenefit,
    SubsidyContext,
    TaxCreditBenefit,
    Tier,
    TieredPerUnitBenefit,
    UnpricedMeasures,
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
    synthetic_catalog,
    write_database,
)

pytestmark = pytest.mark.base

#: The envelope measure's area, in square metres.
AREA_IN_M2 = 120.0

#: The fixed amount of the grants below, in euro.
AMOUNT = 3000.0

#: Every eligible-cost category the Irish fixed-amount grants count.
CATEGORIES = [CostCategory.INVESTMENT, CostCategory.PLANNING, CostCategory.REMOVAL]


def _facts(unpriced: bool = True, quote: float = 0.0) -> ComponentCostFacts:
    """An envelope subject's facts: a placeholder zero when unpriced, as the translator writes them."""
    facts = ComponentCostFacts(
        asset_class=ComponentType.WALL_EXTERNAL_INSULATION,
        size=AREA_IN_M2,
        size_unit=Units.SQUARE_METER,
        investment_cost_override_in_euro=UncertainValue.exact(0.0),
        installation_cost_override_in_euro=UncertainValue.exact(0.0),
        override_source="no cost block on the measure",
        price_is_unknown=unpriced,
    )
    if quote:
        facts = replace(facts, purchase_cost_override_in_euro=UncertainValue.exact(quote))
    return facts


def _measure(unpriced: bool = True, cost: float = 0.0, quote: float = 0.0) -> MeasureForSubsidy:
    """The envelope measure the subsidy engine sees, costing ``cost`` in year 0."""
    return MeasureForSubsidy(
        subject="external_insulation",
        facts=_facts(unpriced, quote),
        measure_kind="INSTALL",
        cost_by_category={
            CostCategory.INVESTMENT: UncertainValue.exact(cost),
            CostCategory.PLANNING: UncertainValue.exact(0.0),
            CostCategory.REMOVAL: UncertainValue.exact(0.0),
        },
    )


def _scheme(scheme_id: str, kind: BenefitKind, benefit, **changes):
    """An always-eligible scheme counting the cost categories a fixed Irish grant counts."""
    scheme = always_eligible_scheme(scheme_id, kind, benefit, PayoutKind.UPFRONT_GRANT)
    return replace(scheme, eligible_cost=EligibleCostSpec(categories=list(CATEGORIES)), **changes)


FIXED_AMOUNTS = [
    ("LUMP", BenefitKind.LUMP_SUM, LumpSumBenefit(amount=AMOUNT)),
    ("PER_M2", BenefitKind.PER_UNIT, PerUnitBenefit(amount=25.0, size_unit=Units.SQUARE_METER)),
    (
        "TIERED",
        BenefitKind.TIERED_PER_UNIT,
        TieredPerUnitBenefit(tiers=(Tier(up_to=None, amount_per_unit=25.0),), size_unit=Units.SQUARE_METER),
    ),
]


class TestTheMaximum:
    """``scheme_maximum`` of a fixed amount on an unpriced measure is ``None``, with the reason."""

    @pytest.mark.parametrize("scheme_id, kind, benefit", FIXED_AMOUNTS)
    def test_a_fixed_amount_on_an_unpriced_measure_states_no_cap(self, scheme_id, kind, benefit) -> None:
        """Not "up to EUR 0": the cap is the cost nobody stated."""
        maximum = scheme_maximum(_scheme(scheme_id, kind, benefit), _measure(), SubsidyContext(), None)
        assert maximum.amount_in_euro is None
        assert maximum.note == SchemeMaximumNotes.UNPRICED

    def test_a_priced_measure_keeps_its_clamped_amount(self) -> None:
        """A measure with a price is clamped as before: a grant never exceeds the cost it funds."""
        scheme = _scheme("LUMP", BenefitKind.LUMP_SUM, LumpSumBenefit(amount=AMOUNT))
        maximum = scheme_maximum(scheme, _measure(unpriced=False, cost=2000.0), SubsidyContext(), None)
        assert maximum.amount_in_euro == UncertainValue.exact(2000.0)

    def test_a_quote_prices_the_measure(self) -> None:
        """A reader's quote is a stated price: the fixed amount is capped by it as usual."""
        scheme = _scheme("LUMP", BenefitKind.LUMP_SUM, LumpSumBenefit(amount=AMOUNT))
        maximum = scheme_maximum(scheme, _measure(cost=16000.0, quote=16000.0), SubsidyContext(), None)
        assert maximum.amount_in_euro == UncertainValue.exact(AMOUNT)

    def test_an_unconditional_fixed_amount_is_its_amount(self) -> None:
        """A scheme counting no cost category is not capped at the cost, so the price does not matter."""
        scheme = replace(
            _scheme("LUMP", BenefitKind.LUMP_SUM, LumpSumBenefit(amount=AMOUNT)),
            eligible_cost=EligibleCostSpec(categories=[]),
        )
        maximum = scheme_maximum(scheme, _measure(), SubsidyContext(), None)
        assert maximum.amount_in_euro == UncertainValue.exact(AMOUNT)

    @pytest.mark.parametrize(
        "scheme_id, kind, benefit, payout",
        [
            ("SHARE", BenefitKind.SHARE_OF_ELIGIBLE_COST, ShareBenefit(rate=0.5), PayoutKind.UPFRONT_GRANT),
            ("BONUS", BenefitKind.BONUS_SHARE, ShareBenefit(rate=0.1), PayoutKind.UPFRONT_GRANT),
            ("CREDIT", BenefitKind.TAX_CREDIT, TaxCreditBenefit(rate=0.2, years=3), PayoutKind.TAX_CREDIT_SCHEDULE),
        ],
    )
    def test_a_share_of_an_unknown_cost_states_no_cap(self, scheme_id, kind, benefit, payout) -> None:
        """A share of the cost is as unknown as the cost (owner decision of 2026-09-28)."""
        scheme = replace(
            always_eligible_scheme(scheme_id, kind, benefit, payout),
            eligible_cost=EligibleCostSpec(categories=list(CATEGORIES)),
        )
        maximum = scheme_maximum(scheme, _measure(), SubsidyContext(), None)
        assert maximum.amount_in_euro is None
        assert maximum.note == SchemeMaximumNotes.UNPRICED

    def test_a_soft_loan_and_an_operational_payment_keep_their_rules(self) -> None:
        """A loan without a repayment grant states its own note; a per-kWh payment its amount."""
        loan = _scheme(
            "LOAN", BenefitKind.SOFT_LOAN, LoanTermsBenefit(interest_rate=0.03, term=10, repayment_grant_rate=0.0)
        )
        assert scheme_maximum(loan, _measure(), SubsidyContext(), None).note == SchemeMaximumNotes.SOFT_LOAN
        feed_in = _scheme(
            "FEED_IN",
            BenefitKind.OPERATIONAL,
            OperationalBenefit(rate_per_kwh=0.05, carrier=EnergyCarrier.ELECTRICITY, duration_years=10),
        )
        assert scheme_maximum(feed_in, _measure(), SubsidyContext(), None).amount_in_euro == UncertainValue.exact(0.0)


class TestTheDecision:
    """The solver leaves such a scheme undetermined on the price instead of awarding a zero."""

    OPEN_QUESTION = Condition(kind="leaf", fieldname="applicant.first_time_buyer", op="==", value=True)
    ANSWERED_NO = Condition(kind="leaf", fieldname="applicant.actor", op="==", value="TENANT")

    def _decide(self, schemes, measure):
        """The solver's decision over ``schemes`` for one measure."""
        catalog = synthetic_catalog(schemes)
        return solve_cumulation(catalog, measure, SubsidyContext(), SyntheticPlan.YEAR, lambda _: 1.0)

    def test_an_eligible_fixed_amount_is_not_awarded_but_asks_for_the_price(self) -> None:
        """Undetermined, the price as its open question, nothing applied."""
        decision = self._decide([_scheme("LUMP", BenefitKind.LUMP_SUM, LumpSumBenefit(amount=AMOUNT))], _measure())
        assert decision.applied == []
        assert [row["scheme_id"] for row in decision.undetermined] == ["LUMP"]
        assert decision.undetermined[0]["missing_fields"] == [UnpricedMeasures.PRICE_QUESTION]

    def test_an_open_fixed_amount_also_asks_for_the_price(self) -> None:
        """Its own question first, then the price."""
        scheme = _scheme("LUMP", BenefitKind.LUMP_SUM, LumpSumBenefit(amount=AMOUNT), eligibility=self.OPEN_QUESTION)
        decision = self._decide([scheme], _measure())
        assert decision.undetermined[0]["missing_fields"] == [
            "applicant.first_time_buyer",
            UnpricedMeasures.PRICE_QUESTION,
        ]

    def test_a_refused_fixed_amount_stays_refused(self) -> None:
        """No price could change a verdict the answers already gave."""
        scheme = _scheme("LUMP", BenefitKind.LUMP_SUM, LumpSumBenefit(amount=AMOUNT), eligibility=self.ANSWERED_NO)
        decision = self._decide([scheme], _measure())
        assert [row["scheme_id"] for row in decision.rejected] == ["LUMP"]
        assert not decision.undetermined

    def test_a_quoted_measure_is_awarded_as_usual(self) -> None:
        """The quote prices the purchase, so the fixed amount is decided and paid."""
        decision = self._decide(
            [_scheme("LUMP", BenefitKind.LUMP_SUM, LumpSumBenefit(amount=AMOUNT))],
            _measure(cost=16000.0, quote=16000.0),
        )
        assert [(award.scheme_id, award.upfront_amount) for award in decision.applied] == [
            ("LUMP", UncertainValue.exact(AMOUNT))
        ]


class TestTheFlagTravels:
    """``price_is_unknown`` survives ``economic_inputs.json``, which the staged evaluator reads."""

    @pytest.mark.parametrize("unpriced", [True, False])
    def test_the_flag_round_trips(self, unpriced) -> None:
        """Written and read back unchanged; a file written before the flag reads as priced."""
        assert facts_from_json(facts_to_json(_facts(unpriced))).price_is_unknown is unpriced
        raw = facts_to_json(_facts(True))
        raw.pop("price_is_unknown")
        assert facts_from_json(raw).price_is_unknown is False


def _unpriced_envelope_stage() -> Stage:
    """The synthetic envelope stage with its measure unpriced, as the translator writes one."""
    stage = envelope_stage(0)
    cost_facts = [
        replace(
            subject_facts,
            facts=replace(
                subject_facts.facts,
                investment_cost_override_in_euro=UncertainValue.exact(0.0),
                price_is_unknown=True,
            ),
        )
        if subject_facts.subject == SyntheticPlan.ENVELOPE_SUBJECT
        else subject_facts
        for subject_facts in stage.inputs.cost_facts
    ]
    return replace(stage, inputs=replace(stage.inputs, cost_facts=cost_facts))


def _document(tmp_path, schemes) -> Dict[str, Any]:
    """The written baseline + unpriced envelope plan under a catalogue of ``schemes``."""
    parameters = EconomicParameters(
        observation_period_in_years=SyntheticPlan.HORIZON,
        interest_rate=SyntheticPlan.INTEREST_RATE,
        country=SyntheticPlan.COUNTRY,
        price_basis_year=SyntheticPlan.YEAR,
        co2_price_scenario="none",
    )
    perspective = brownfield_perspective(subsidies=True)
    result = StagedEvaluator(write_database(str(tmp_path / "database"))).evaluate(
        [baseline_stage(), _unpriced_envelope_stage()], parameters, perspective, synthetic_catalog(schemes)
    )
    path = tmp_path / StagedDocument.FILE_NAME
    return StagedDocument(
        result,
        parameters,
        perspective,
        measure_ids={SyntheticPlan.ENVELOPE_SUBJECT: "external_insulation"},
        unpriced_subjects={SyntheticPlan.ENVELOPE_SUBJECT},
    ).write(path)


class TestTheRow:
    """The document's row: undetermined, no amount, no cap, the note and the question."""

    def test_the_row_states_no_cap_and_asks_for_the_price(self, tmp_path) -> None:
        """The production case (#77 point 3), and the document still reconciles and validates."""
        document = _document(tmp_path, [_scheme("LUMP", BenefitKind.LUMP_SUM, LumpSumBenefit(amount=AMOUNT))])
        rows: List[Dict[str, Any]] = [row for row in document["plan"]["subsidies"] if row["scheme"] == "LUMP"]
        assert len(rows) == 1
        row = rows[0]
        assert row["status"] == "undetermined"
        assert row["amount_in_euro"] is None and row["amount_by_year_in_euro"] is None
        assert row["max_amount_in_euro"] is None
        assert row["max_amount_for_measure_in_euro"] is None
        assert row["note"] == SchemeMaximumNotes.UNPRICED
        assert row["open_questions"] == [UnpricedMeasures.PRICE_QUESTION]

    def test_nothing_is_booked_for_it(self, tmp_path) -> None:
        """The Subsidies stack of every year is zero: an undecided grant carries no money."""
        document = _document(tmp_path, [_scheme("LUMP", BenefitKind.LUMP_SUM, LumpSumBenefit(amount=AMOUNT))])
        for year in document["plan"]["annual"]:
            assert year["by_group"]["Subsidies"] == {"min": 0.0, "best": 0.0, "max": 0.0}


class TestWarmerHomesOnAnUnpricedMeasure:
    """The Irish 100 % scheme on an envelope measure without a price (owner decision of 2026-09-28)."""

    def test_it_is_undetermined_on_the_price_with_no_cap(self) -> None:
        """Not "up to EUR 0" and not an award of zero: a question, and nothing booked."""
        from hisim.economics.subsidies import (  # pylint: disable=import-outside-toplevel
            ApplicantProfile,
            DwellingType,
            SubsidyBuildingContext,
            SubsidyCatalog,
        )

        context = SubsidyContext(
            applicant=ApplicantProfile(receives_means_tested_benefit=True),
            building=SubsidyBuildingContext(construction_year=1990, dwelling_type=DwellingType.DETACHED),
        )
        decision = solve_cumulation(SubsidyCatalog.load("IE"), _measure(), context, 2026, lambda _: 1.0)
        assert "IE_SEAI_WARMER_HOMES" not in {award.scheme_id for award in decision.applied}
        open_rows = {row["scheme_id"]: row for row in decision.undetermined}
        assert UnpricedMeasures.PRICE_QUESTION in open_rows["IE_SEAI_WARMER_HOMES"]["missing_fields"]
        maximum = decision.maximum_by_scheme["IE_SEAI_WARMER_HOMES"]
        assert maximum.amount_in_euro is None and maximum.note == SchemeMaximumNotes.UNPRICED
