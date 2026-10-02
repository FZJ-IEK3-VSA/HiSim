"""A later stage that enlarges what an earlier one bought pays for the increment (hisim-1y0m).

Stage 1 buys a 5 kWp array in year 0, stage 2 has 8 kWp from year 2. The stage-2 evaluation used to
see the array as kept -- the stage-1 purchase is in its register -- so it booked no purchase, and
the charge share (8 - 5) / 8 of nothing was nothing. Owner decision (2026-09-28): the later stage
buys the increment as a new purchase, priced as a new 3 kWp unit at the database's law for that
size, escalated to its year, with its own life and replacements, and a reader's quote for the
stage's measure replaces that price exactly; the 5 kWp bought earlier keeps ageing on its own.

Every figure is hand-computed from the synthetic PV entry written here: EUR 1,000 per kW, linear,
plus a fixed EUR 2,000 installation per purchase, a six-year life, and a 2 % investment escalation
rate over a twelve-year horizon.
"""

import json
from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Tuple

import pytest

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts, ExistingAsset
from hisim.economics.parameters import EconomicParameters
from hisim.economics.serialization import asset_from_json, asset_to_json, facts_from_json, facts_to_json
from hisim.economics.staged import IncrementSubjects, InvestmentOverride, Stage, StagedEvaluator
from hisim.economics.staged_document import StagedDocument
from hisim.economics.subsidies import BenefitKind, OperationalBenefit, PayoutKind, SubsidyCatalog
from hisim.economics.timeline import CostCategory
from hisim.loadtypes import ComponentType, Units

from tests.economics.synthetic_stages import (
    SyntheticPlan,
    always_eligible_scheme,
    brownfield_perspective,
    device_facts,
    entry_signature,
    inventory_register,
    synthetic_catalog,
    write_database,
)

pytestmark = pytest.mark.base

RATE = 0.02
PER_KW = 1000.0
INSTALLATION = 2000.0
LIFE = 6
PV = "PVSystem"
INCREMENT = IncrementSubjects.name(PV, 2)


@pytest.fixture(name="database", scope="module")
def fixture_database(tmp_path_factory) -> CostDatabase:
    """The synthetic database plus one linear PV entry with a fixed installation cost."""
    directory = str(tmp_path_factory.mktemp("increment_database"))
    write_database(directory)
    devices = {
        "entries": [
            {
                "component_type": "PV",
                "valid_from_year": SyntheticPlan.YEAR,
                "specific_investment": {"value": PER_KW, "per_unit": "kW"},
                "scaling_exponent": None,
                "fixed_installation_cost_in_euro": INSTALLATION,
                "planning_cost_in_euro": 0,
                "removal_cost_in_euro": 0,
                "maintenance_rate_per_year": 0.01,
                "fixed_operation_cost_in_euro_per_year": 0,
                "service_life_in_years": LIFE,
                "embodied_co2": {"value": 0.0, "per_unit": "kW"},
                "vat_rate": 0.0,
                "price_basis": "NET",
                "source_ids": ["src_staged_test"],
                "notes": "Synthetic PV price for the increment tests.",
            }
        ]
    }
    with open(f"{directory}/devices_{SyntheticPlan.COUNTRY}.json", "w", encoding="utf-8") as handle:
        json.dump(devices, handle)
    return CostDatabase(directory)


def _parameters(subsidies: bool = False) -> EconomicParameters:
    """Synthetic assumptions with a 2 % investment escalation rate, subsidies off unless asked for."""
    return EconomicParameters(
        observation_period_in_years=SyntheticPlan.HORIZON,
        interest_rate=SyntheticPlan.INTEREST_RATE,
        country=SyntheticPlan.COUNTRY,
        price_basis_year=SyntheticPlan.YEAR,
        co2_price_scenario="none",
        apply_subsidies=subsidies,
        investment_price_escalation_rate=RATE,
    )


def _stage(
    pv_size: float,
    from_year: int,
    label: str,
    pv_investment: Optional[float] = None,
    sold: float = 0.0,
    extra: Sequence[SubjectCostFacts] = (),
) -> Stage:
    """A state with an array of ``pv_size`` kW (none for 0 means no subject).

    Args:
        pv_size: The array's size in kW; 0 for no array.
        from_year: The stage's start year.
        label: The stage's label.
        pv_investment: A whole-array investment override (with a ten-year life), or ``None`` for
            the database's price.
        sold: Electricity the stage sells a year, in kWh.
        extra: Further subjects of the stage.
    """
    cost_facts = list(extra)
    if pv_size:
        facts = (
            ComponentCostFacts(asset_class=ComponentType.PV, size=pv_size, size_unit=Units.KILOWATT)
            if pv_investment is None
            else device_facts(ComponentType.PV, pv_size, pv_investment)
        )
        cost_facts.append(SubjectCostFacts(PV, facts))
    return Stage(
        inputs=EvaluationInputs(
            simulation_year=SyntheticPlan.YEAR,
            simulated_period_fraction=1.0,
            cost_facts=cost_facts,
            billing=[
                BillingDeterminants(
                    carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=4000.0, energy_sold_in_kwh=sold
                )
            ],
            existing_assets=inventory_register(),
            annual_heat_demand_in_kwh=11000.0,
        ),
        from_year=from_year,
        label=label,
        measures=("photovoltaics",) if pv_size else (),
    )


def _plan() -> List[Stage]:
    """Baseline without an array, 5 kWp in year 0, 8 kWp from year 2."""
    return [_stage(0.0, 0, "baseline"), _stage(5.0, 0, "stage 1"), _stage(8.0, 2, "stage 2")]


def _flows(result, subject: str) -> Dict[CostCategory, List[Tuple[int, float]]]:
    """Category -> ``(plan year, best estimate)`` of one subject's investment-class entries."""
    flows: Dict[CostCategory, List[Tuple[int, float]]] = {}
    for entry in result.plan.timeline.entries:
        if entry.subject == subject and entry.category in (
            CostCategory.INVESTMENT,
            CostCategory.PLANNING,
            CostCategory.REPLACEMENT,
            CostCategory.RESIDUAL_VALUE,
        ):
            flows.setdefault(entry.category, []).append((entry.year, entry.amount_in_euro.best_estimate))
    return {category: sorted(values) for category, values in flows.items()}


class TestTheIncrementIsBought:
    """The 3 kWp stage 2 adds is a purchase of its own; the 5 kWp of stage 1 ages on."""

    def test_the_increment_is_priced_as_a_new_unit_of_its_size_in_its_year(self, database):
        """(3 x 1,000 + 2,000) x 1.02^2 = 5,202 in year 2; replaced after six years, in year 8.

        Not a share of the 8 kWp price (3/8 x 10,000 = 3,750): a separate purchase pays the fixed
        installation again. The replacement is 5,000 at year-0 prices escalated to year 8, and the
        residual at year 12 is a third of it (installed 8, life 6: 2 of 6 years left).
        """
        result = StagedEvaluator(database).evaluate(_plan(), _parameters(), brownfield_perspective())
        flows = _flows(result, INCREMENT)
        assert flows[CostCategory.INVESTMENT] == [(2, pytest.approx(5000.0 * 1.02**2))]
        assert flows[CostCategory.REPLACEMENT] == [(8, pytest.approx(5000.0 * 1.02**8))]
        assert flows[CostCategory.RESIDUAL_VALUE] == [(12, pytest.approx(-5000.0 * 1.02**8 / 3.0))]
        assert 5000.0 * 1.02**2 == pytest.approx(5202.0)

    def test_the_earlier_array_keeps_its_own_schedule_and_size(self, database):
        """7,000 in year 0; replaced in year 6 as 5 kWp (7,000 x 1.02^6), not 8 kWp; nothing left at 12."""
        result = StagedEvaluator(database).evaluate(_plan(), _parameters(), brownfield_perspective())
        flows = _flows(result, PV)
        assert flows[CostCategory.INVESTMENT] == [(0, pytest.approx(7000.0))]
        assert flows[CostCategory.REPLACEMENT] == [(6, pytest.approx(7000.0 * 1.02**6))]
        assert CostCategory.RESIDUAL_VALUE not in flows

    def test_the_stage_charges_the_increment_whole_and_carries_the_array(self, database):
        """Stage 2 pays for its increment in full and not for the array it keeps."""
        result = StagedEvaluator(database).evaluate(_plan(), _parameters(), brownfield_perspective())
        assert result.charged_subjects_by_stage[2] == {INCREMENT: 1.0}
        assert StagedEvaluator.charged_subjects(_plan(), 2) == {PV: 1.0}

    def test_a_third_stage_keeps_both_pieces_on_their_own_schedules(self, database):
        """A stage in year 4 with the same 8 kWp buys nothing and moves no replacement."""
        stages = _plan() + [_stage(8.0, 4, "stage 3")]
        result = StagedEvaluator(database).evaluate(stages, _parameters(), brownfield_perspective())
        assert result.charged_subjects_by_stage[3] == {}
        assert _flows(result, INCREMENT)[CostCategory.REPLACEMENT] == [(8, pytest.approx(5000.0 * 1.02**8))]
        assert _flows(result, PV)[CostCategory.REPLACEMENT] == [(6, pytest.approx(7000.0 * 1.02**6))]

    def test_moving_the_enlargement_later_changes_nothing_before_it(self, database):
        """Invariant 3: every flow before year 2 is the same whether stage 2 starts in 2 or in 5."""
        early = StagedEvaluator(database).evaluate(_plan(), _parameters(), brownfield_perspective())
        late_plan = _plan()[:2] + [_stage(8.0, 5, "stage 2")]
        late = StagedEvaluator(database).evaluate(late_plan, _parameters(), brownfield_perspective())
        before = [row for row in entry_signature(early.plan.timeline.entries) if row[0] < 2]
        assert before == [row for row in entry_signature(late.plan.timeline.entries) if row[0] < 2]

    def test_a_plan_without_enlargement_is_split_into_nothing(self, database):
        """Invariant 1 still holds: a one-stage plan equals the plain evaluation entry for entry."""
        stage = _stage(5.0, 0, "only")
        assert StagedEvaluator.split_increments([stage]) == (stage,)
        staged = StagedEvaluator(database).evaluate([stage], _parameters(), brownfield_perspective())
        plain = EconomicEvaluator(database, _parameters(), None, book_anyway_credit=False).evaluate(
            stage.inputs, brownfield_perspective()
        )
        assert entry_signature(staged.plan.timeline.entries) == entry_signature(plain.timeline.entries)


class TestAQuoteForTheEnlargingStage:
    """A reader's quote for stage 2's measure is the increment's price, exactly."""

    QUOTE = 4000.0

    def _result(self, database):
        """The plan priced with a EUR 4,000 quote for stage 2's photovoltaics measure."""
        quote = InvestmentOverride(
            stage=2, measure_id="photovoltaics", amount_in_euro=self.QUOTE, source="installer", main_subject=PV
        )
        return StagedEvaluator(database).evaluate(
            _plan(), _parameters(), brownfield_perspective(), investment_overrides=[quote]
        )

    def test_the_quote_replaces_the_increments_year_2_price_unescalated(self, database):
        """4,000 in year 2 as stated; its replacement is the database's 5,000 x 1.02^8 in year 8."""
        result = self._result(database)
        flows = _flows(result, INCREMENT)
        assert flows[CostCategory.INVESTMENT] == [(2, pytest.approx(self.QUOTE))]
        assert flows[CostCategory.REPLACEMENT] == [(8, pytest.approx(5000.0 * 1.02**8))]
        assert result.investment_overrides[0].main_subject == INCREMENT

    def test_the_array_bought_earlier_is_not_repriced(self, database):
        """Stage 1's 7,000 and its year-6 replacement are what they are without the quote."""
        flows = _flows(self._result(database), PV)
        assert flows[CostCategory.INVESTMENT] == [(0, pytest.approx(7000.0))]
        assert flows[CostCategory.REPLACEMENT] == [(6, pytest.approx(7000.0 * 1.02**6))]


class TestTheDocumentRow:
    """The increment is a by_subject row of its own, under the array's measure, in stage 2."""

    def test_the_increment_row_names_the_measure_and_the_stage(self, database, tmp_path):
        """Its own row: the array's measure, stage 2, 5,202 booked in stage 2, installed in year 0 + 2."""
        result = StagedEvaluator(database).evaluate(_plan(), _parameters(), brownfield_perspective())
        document = StagedDocument(
            result, _parameters(), brownfield_perspective(), measure_ids={PV: "photovoltaics"}
        ).write(tmp_path / "increment.json")
        rows = {row["subject"]: row for row in document["plan"]["by_subject"]}
        increment = rows[INCREMENT]
        assert increment["measure_id"] == "photovoltaics"
        assert increment["stage"] == 2
        assert increment["asset_class"] == ComponentType.PV.value
        assert [entry["stage"] for entry in increment["investment_by_stage"]] == [2]
        assert increment["investment_by_stage"][0]["investment_in_euro"]["best"] == pytest.approx(5202.0)
        assert increment["installation_year"] == SyntheticPlan.YEAR + 2
        assert rows[PV]["stage"] == 1


class TestAnOverridePricedEnlargement:
    """A whole-array override: the base keeps its own price, the increment takes the stage's per-kW price.

    Stage 1 buys 100 kW for EUR 10,000 (EUR 100 per kW), stage 2 has 150 kW for EUR 60,000 (EUR 400
    per kW) from year 2; both overrides state a ten-year life.
    """

    def _result(self, database):
        """The plan: no array, 100 kW at EUR 10,000 in year 0, 150 kW at EUR 60,000 from year 2."""
        stages = [
            _stage(0.0, 0, "baseline"),
            _stage(100.0, 0, "stage 1", pv_investment=10000.0),
            _stage(150.0, 2, "stage 2", pv_investment=60000.0),
        ]
        return StagedEvaluator(database).evaluate(stages, _parameters(), brownfield_perspective())

    def test_the_base_is_replaced_at_the_price_its_own_stage_paid(self, database):
        """10,000 x 1.02^10 = 12,189.94 in year 10, not two thirds of 60,000 x 1.02^10 = 48,759.78."""
        flows = _flows(self._result(database), PV)
        assert flows[CostCategory.INVESTMENT] == [(0, pytest.approx(10000.0))]
        assert flows[CostCategory.REPLACEMENT] == [(10, pytest.approx(10000.0 * 1.02**10))]
        assert 10000.0 * 1.02**10 == pytest.approx(12189.94, abs=0.01)

    def test_the_increment_pays_the_stages_price_per_kw_for_its_50_kw(self, database):
        """50 kW x EUR 400 per kW = 20,000 at year-0 prices, 20,808 in year 2; no replacement in the horizon."""
        flows = _flows(self._result(database), INCREMENT)
        assert flows[CostCategory.INVESTMENT] == [(2, pytest.approx(20000.0 * 1.02**2))]
        assert CostCategory.REPLACEMENT not in flows

    def test_the_pieces_carry_their_own_facts(self, database):
        """Stage 2 carries the base at stage 1's override and the increment at a third of stage 2's."""
        pieces = {facts.subject: facts.facts for facts in self._result(database).stages[2].inputs.cost_facts}
        assert pieces[PV].size == 100.0
        assert pieces[PV].investment_cost_override_in_euro.best_estimate == pytest.approx(10000.0)
        assert pieces[INCREMENT].size == 50.0
        assert pieces[INCREMENT].investment_cost_override_in_euro.best_estimate == pytest.approx(20000.0)


class TestAnArrayEnlargedTwice:
    """5 kWp in year 0, 8 kWp from year 2, 10 kWp from year 4: two increments, each its own purchase."""

    def _plan(self) -> List[Stage]:
        """The plan."""
        return _plan() + [_stage(10.0, 4, "stage 3")]

    def test_each_increment_has_its_size_and_its_share_of_the_energy(self, database):
        """Stage 3 carries 5 + 3 + 2 kW, paid on 5/10, 3/10 and 2/10 of the energy sold."""
        result = StagedEvaluator(database).evaluate(self._plan(), _parameters(), brownfield_perspective())
        pieces = {facts.subject: facts.facts for facts in result.stages[3].inputs.cost_facts}
        second = IncrementSubjects.name(PV, 3)
        assert {subject: facts.size for subject, facts in pieces.items()} == {PV: 5.0, INCREMENT: 3.0, second: 2.0}
        assert pieces[PV].share_of_energy_sold == pytest.approx(0.5)
        assert pieces[INCREMENT].share_of_energy_sold == pytest.approx(0.3)
        assert pieces[second].share_of_energy_sold == pytest.approx(0.2)
        assert pieces[second].own_register_entry and pieces[INCREMENT].own_register_entry
        assert not pieces[PV].own_register_entry
        assert result.charged_subjects_by_stage[3] == {second: 1.0}

    def test_each_increment_is_priced_in_its_own_year(self, database):
        """(3 x 1,000 + 2,000) x 1.02^2 = 5,202 in year 2 and (2 x 1,000 + 2,000) x 1.02^4 in year 4."""
        result = StagedEvaluator(database).evaluate(self._plan(), _parameters(), brownfield_perspective())
        assert _flows(result, INCREMENT)[CostCategory.INVESTMENT] == [(2, pytest.approx(5000.0 * 1.02**2))]
        second = _flows(result, IncrementSubjects.name(PV, 3))
        assert second[CostCategory.INVESTMENT] == [(4, pytest.approx(4000.0 * 1.02**4))]
        assert second[CostCategory.REPLACEMENT] == [(10, pytest.approx(4000.0 * 1.02**10))]
        assert _flows(result, PV)[CostCategory.INVESTMENT] == [(0, pytest.approx(7000.0))]


class TestAnIncrementThatLeaves:
    """A stage that shrinks the array back removes the increment: no residual value is booked for it."""

    def test_a_shrunk_array_books_no_residual_for_the_increment(self, database):
        """8 kWp from year 8, 5 kWp again from year 10: the 3 kWp bought in year 8 earns nothing at 12.

        Kept to the horizon it would be worth a third of its year-8 price there (life 6, 2 years
        left), which is what the plan booked before the increment's departure was respected.
        """
        stages = [
            _stage(0.0, 0, "baseline"),
            _stage(5.0, 0, "stage 1"),
            _stage(8.0, 8, "stage 2"),
            _stage(5.0, 10, "stage 3"),
        ]
        result = StagedEvaluator(database).evaluate(stages, _parameters(), brownfield_perspective())
        increment = IncrementSubjects.name(PV, 2)
        flows = _flows(result, increment)
        assert flows == {CostCategory.INVESTMENT: [(8, pytest.approx(5000.0 * 1.02**8))]}
        kept = StagedEvaluator(database).evaluate(stages[:3], _parameters(), brownfield_perspective())
        assert _flows(kept, increment)[CostCategory.RESIDUAL_VALUE] == [(12, pytest.approx(-5000.0 * 1.02**8 / 3.0))]


class TestPerKwhPaymentsBySizeShare:
    """Owner decision 2026-10-01: each piece is paid per kWh on its size share of the energy sold.

    EUR 0.05 per kWh sold for twenty years, on 1,500 kWh a year in every stage: EUR 75 a year for a
    whole array. 5 kWp in year 0 grown to 8 kWp from year 2: the 5 kWp unit is paid on 5/8 and the
    3 kWp increment on 3/8 once the array is 8 kWp, so the two add up to one payment on the whole.
    """

    RATE_PER_KWH = 0.05
    SOLD = 1500.0
    SCHEME = "SYNTHETIC_PV_PREMIUM"

    def _catalog(self) -> SubsidyCatalog:
        """One operational premium every PV array qualifies for."""
        scheme = always_eligible_scheme(
            self.SCHEME,
            BenefitKind.OPERATIONAL,
            OperationalBenefit(rate_per_kwh=self.RATE_PER_KWH, carrier=EnergyCarrier.ELECTRICITY, duration_years=20),
            PayoutKind.OPERATIONAL,
        )
        return synthetic_catalog([replace(scheme, asset_classes=[ComponentType.PV])])

    def _payments(self, database, stages: List[Stage]) -> Dict[str, Dict[int, float]]:
        """Subject -> plan year -> the premium booked, as a positive amount."""
        result = StagedEvaluator(database).evaluate(
            stages, _parameters(subsidies=True), brownfield_perspective(subsidies=True), self._catalog()
        )
        payments: Dict[str, Dict[int, float]] = {}
        for entry in result.plan.timeline.entries:
            if entry.category is CostCategory.SUBSIDY and entry.subsidy_scheme_id == self.SCHEME:
                payments.setdefault(entry.subject, {})[entry.year] = -entry.amount_in_euro.best_estimate
        return payments

    def test_base_and_increment_are_paid_on_their_shares(self, database):
        """Year 1: 75 for the 5 kWp; years 3..12: 46.875 + 28.125 = 75, one payment on the whole."""
        stages = [
            _stage(0.0, 0, "baseline"),
            _stage(5.0, 0, "stage 1", sold=self.SOLD),
            _stage(8.0, 2, "stage 2", sold=self.SOLD),
        ]
        payments = self._payments(database, stages)
        whole = self.RATE_PER_KWH * self.SOLD
        assert payments[PV][1] == pytest.approx(whole)
        assert payments[PV][2] == pytest.approx(whole * 5 / 8)
        for year in range(3, SyntheticPlan.HORIZON + 1):
            assert payments[PV][year] == pytest.approx(whole * 5 / 8)
            assert payments[INCREMENT][year] == pytest.approx(whole * 3 / 8)
            assert payments[PV][year] + payments[INCREMENT][year] == pytest.approx(whole)
        assert sorted(payments[INCREMENT]) == list(range(3, SyntheticPlan.HORIZON + 1))

    def test_an_unsplit_purchase_is_paid_on_the_whole_energy(self, database):
        """8 kWp bought whole in year 0: EUR 75 in every year 1..12, as before."""
        stages = [_stage(0.0, 0, "baseline"), _stage(8.0, 0, "stage 1", sold=self.SOLD)]
        payments = self._payments(database, stages)
        assert set(payments) == {PV}
        assert payments[PV] == {
            year: pytest.approx(self.RATE_PER_KWH * self.SOLD) for year in range(1, SyntheticPlan.HORIZON + 1)
        }


class TestAQuoteNamingTheEnlargedSubjectAsAFurtherOne:
    """A quote whose further subject is the array a stage enlarges buys the increment at zero."""

    QUOTE = 9000.0

    def test_the_quote_covers_the_increment_and_leaves_the_array_alone(self, database):
        """The roof works quoted at 9,000 in year 2 include the 3 kWp; the 5 kWp is not re-priced."""
        quote = InvestmentOverride(
            stage=2,
            measure_id="roof",
            amount_in_euro=self.QUOTE,
            source="installer",
            main_subject="roof_works",
            other_subjects=(PV,),
        )
        result = StagedEvaluator(database).evaluate(
            _plan(), _parameters(), brownfield_perspective(), investment_overrides=[quote]
        )
        assert result.investment_overrides[0].other_subjects == (INCREMENT,)
        increment = _flows(result, INCREMENT)
        assert sum(amount for year, amount in increment[CostCategory.INVESTMENT] if year == 2) == pytest.approx(0.0)
        assert increment[CostCategory.REPLACEMENT] == [(8, pytest.approx(5000.0 * 1.02**8))]
        roof = _flows(result, "roof_works")
        assert roof[CostCategory.INVESTMENT] == [(2, pytest.approx(self.QUOTE))]
        array = _flows(result, PV)
        assert array[CostCategory.INVESTMENT] == [(0, pytest.approx(7000.0))]
        assert array[CostCategory.REPLACEMENT] == [(6, pytest.approx(7000.0 * 1.02**6))]


class TestSerialization:
    """The cost facts and the register carry the split's fields, and old documents read with defaults."""

    def test_the_facts_round_trip_with_their_register_binding_and_energy_share(self):
        """``own_register_entry`` and ``share_of_energy_sold`` survive; absent, they read False and 1.0."""
        facts = ComponentCostFacts(
            asset_class=ComponentType.PV,
            size=3.0,
            size_unit=Units.KILOWATT,
            own_register_entry=True,
            share_of_energy_sold=0.375,
        )
        again = facts_from_json(facts_to_json(facts))
        assert again.own_register_entry is True
        assert again.share_of_energy_sold == pytest.approx(0.375)
        old = facts_to_json(facts)
        del old["own_register_entry"], old["share_of_energy_sold"]
        legacy = facts_from_json(old)
        assert legacy.own_register_entry is False
        assert legacy.share_of_energy_sold == 1.0

    def test_a_register_entry_round_trips_with_its_subject(self):
        """``ExistingAsset.subject`` survives; absent, it reads ``None``."""
        asset = ExistingAsset(
            asset_class=ComponentType.PV,
            size=3.0,
            size_unit=Units.KILOWATT,
            installation_year=SyntheticPlan.YEAR + 2,
            subject=INCREMENT,
        )
        assert asset_from_json(asset_to_json(asset)).subject == INCREMENT
        old = asset_to_json(asset)
        del old["subject"]
        assert asset_from_json(old).subject is None
