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
from typing import Dict, List, Tuple

import pytest

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts
from hisim.economics.parameters import EconomicParameters
from hisim.economics.staged import IncrementSubjects, InvestmentOverride, Stage, StagedEvaluator
from hisim.economics.staged_document import StagedDocument
from hisim.economics.timeline import CostCategory
from hisim.loadtypes import ComponentType, Units

from tests.economics.synthetic_stages import (
    SyntheticPlan,
    brownfield_perspective,
    entry_signature,
    inventory_register,
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


def _parameters() -> EconomicParameters:
    """Synthetic assumptions with a 2 % investment escalation rate."""
    return EconomicParameters(
        observation_period_in_years=SyntheticPlan.HORIZON,
        interest_rate=SyntheticPlan.INTEREST_RATE,
        country=SyntheticPlan.COUNTRY,
        price_basis_year=SyntheticPlan.YEAR,
        co2_price_scenario="none",
        apply_subsidies=False,
        investment_price_escalation_rate=RATE,
    )


def _stage(pv_size: float, from_year: int, label: str) -> Stage:
    """A state with a database-priced array of ``pv_size`` kW (none for 0 means no subject)."""
    cost_facts = []
    if pv_size:
        cost_facts.append(
            SubjectCostFacts(
                PV, ComponentCostFacts(asset_class=ComponentType.PV, size=pv_size, size_unit=Units.KILOWATT)
            )
        )
    return Stage(
        inputs=EvaluationInputs(
            simulation_year=SyntheticPlan.YEAR,
            simulated_period_fraction=1.0,
            cost_facts=cost_facts,
            billing=[BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=4000.0)],
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
