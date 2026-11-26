"""The document states the year-1 energy-bill difference as its own band, world by world (renovisorissues #95).

Before hisim-energy-bill-delta-band-e41f the document gave each evaluation's year-1 bill per carrier and no band of
the difference, so the frontend combined two bands. `comparison.energy_bill_year1_delta_in_euro` is the plan's bill
minus the reference's over every carrier, charges minus feed-in revenue, each end pairing the same price world of both.
"""

import json
from typing import Any, Dict, List

import pytest

from hisim.economics.parameters import EconomicParameters
from hisim.economics.staged import StagedEvaluator, StagedResult
from hisim.economics.staged_document import StagedDocument, UnpricedInvestment
from hisim.economics.timeline import CashFlowEntry, CostCategory, SubjectKind
from hisim.economics.uncertainty import UncertainValue

from tests.economics.synthetic_stages import (
    SyntheticPlan,
    baseline_stage,
    brownfield_perspective,
    heat_pump_stage,
    write_database,
)

pytestmark = pytest.mark.base


def _entry(year: int, amount: UncertainValue, category: CostCategory, subject: str) -> CashFlowEntry:
    """One carrier entry of a timeline."""
    return CashFlowEntry(
        year=year, amount_in_euro=amount, category=category, subject=subject, subject_kind=SubjectKind.CARRIER
    )


def _slots(value: UncertainValue) -> List[float]:
    """The three worlds of a band, LOW, central and HIGH."""
    return [value.minimum, value.best_estimate, value.maximum]


class TestOneCarriersBill:
    """`StagedDocument.carrier_bill_year1` reads one carrier's year-1 charges and feed-in revenue."""

    def test_charges_and_the_feed_in_under_electricity(self):
        """Working and standing charges are the cost, the feed-in revenue (booked under its own subject) the revenue."""
        entries = [
            _entry(1, UncertainValue.exact(1000.0), CostCategory.ENERGY_WORKING, "ELECTRICITY"),
            _entry(1, UncertainValue.exact(200.0), CostCategory.ENERGY_STANDING, "ELECTRICITY"),
            _entry(1, UncertainValue.exact(-300.0), CostCategory.FEED_IN_REVENUE, "ELECTRICITY_FEED_IN"),
        ]
        cost, revenue = StagedDocument.carrier_bill_year1(entries, "ELECTRICITY")
        assert (cost.best_estimate, revenue.best_estimate) == (1200.0, -300.0)

    def test_other_years_and_other_carriers_are_not_in_it(self):
        """Year 2 and the gas bill belong to other rows."""
        entries = [
            _entry(2, UncertainValue.exact(1000.0), CostCategory.ENERGY_WORKING, "ELECTRICITY"),
            _entry(1, UncertainValue.exact(800.0), CostCategory.ENERGY_WORKING, "NATURAL_GAS"),
        ]
        cost, revenue = StagedDocument.carrier_bill_year1(entries, "ELECTRICITY")
        assert cost == UncertainValue.exact(0.0) and revenue == UncertainValue.exact(0.0)

    def test_a_non_bill_category_is_not_in_it(self):
        """Maintenance booked under a carrier's name is no charge for energy."""
        entries = [_entry(1, UncertainValue.exact(50.0), CostCategory.MAINTENANCE, "ELECTRICITY")]
        cost, _revenue = StagedDocument.carrier_bill_year1(entries, "ELECTRICITY")
        assert cost == UncertainValue.exact(0.0)


@pytest.fixture(name="database", scope="module")
def fixture_database(tmp_path_factory):
    """The synthetic cost database, written once for the whole module."""
    return write_database(str(tmp_path_factory.mktemp("energy_bill_delta_database")))


@pytest.fixture(name="parameters")
def fixture_parameters() -> EconomicParameters:
    """The assumptions the synthetic plan is priced under."""
    return EconomicParameters(
        observation_period_in_years=SyntheticPlan.HORIZON,
        interest_rate=SyntheticPlan.INTEREST_RATE,
        country=SyntheticPlan.COUNTRY,
        price_basis_year=SyntheticPlan.YEAR,
        co2_price_scenario="none",
        apply_subsidies=False,
    )


def _priced(database, parameters) -> StagedResult:
    """A gas house against a heat pump from year 0 that also feeds electricity into the grid."""
    stages = [baseline_stage(), heat_pump_stage(0, electricity_sold_in_kwh=SyntheticPlan.SOLD_ELECTRICITY_IN_KWH)]
    return StagedEvaluator(database).evaluate(stages, parameters, brownfield_perspective())


def _write(result: StagedResult, parameters, path, unpriced=()) -> Dict[str, Any]:
    """Write the plan's document with the measure map and read it back."""
    StagedDocument(
        result,
        parameters,
        brownfield_perspective(),
        measure_ids={SyntheticPlan.HEAT_PUMP_SUBJECT: "heating_system", SyntheticPlan.ENVELOPE_SUBJECT: "external_insulation"},
        unpriced_subjects=unpriced,
    ).write(path)
    document: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


class TestTheDocument:
    """`comparison.energy_bill_year1_delta_in_euro` on a written document."""

    def test_the_delta_is_the_difference_of_the_rows_world_by_world(self, database, parameters, tmp_path):
        """Each world of the plan minus the same world of the reference, over every row, then sorted."""
        result = _priced(database, parameters)
        document = _write(result, parameters, tmp_path / "delta.json")

        delta = document["comparison"]["energy_bill_year1_delta_in_euro"]
        worlds = [
            plan - reference
            for plan, reference in zip(
                _slots(StagedDocument.energy_bill_year1(result.plan)),
                _slots(StagedDocument.energy_bill_year1(result.reference)),
            )
        ]
        assert delta == {
            "min": pytest.approx(min(worlds)),
            "best": pytest.approx(worlds[1]),
            "max": pytest.approx(max(worlds)),
        }
        for variant in ("plan", "reference"):
            rows = document[variant]["energy_year1"]
            bill = sum(row["cost_in_euro"]["best"] + row["revenue_in_euro"]["best"] for row in rows)
            assert bill == pytest.approx(StagedDocument.energy_bill_year1(getattr(result, variant)).best_estimate)

    def test_a_plan_that_buys_less_and_sells_saves(self, database, parameters, tmp_path):
        """The heat pump house buys less electricity than the gas house and feeds some in: every end is negative.

        The feed-in remuneration is a band, so the plan's bill is one and the delta keeps its width.
        """
        result = _priced(database, parameters)
        document = _write(result, parameters, tmp_path / "saves.json")

        delta = document["comparison"]["energy_bill_year1_delta_in_euro"]
        assert delta["min"] < delta["best"] < delta["max"] < 0

    def test_an_unknown_investment_leaves_it_stated(self, database, parameters, tmp_path):
        """No investment enters the bill, so an unpriced purchase withholds the NPV difference and not this one."""
        result = _priced(database, parameters)
        document = _write(result, parameters, tmp_path / "unpriced.json", unpriced=[SyntheticPlan.HEAT_PUMP_SUBJECT])

        comparison = document["comparison"]
        assert document["unpriced_investment"] is not None
        assert comparison["npv_delta_in_euro"] is None
        delta = StagedDocument.energy_bill_year1(result.plan) - StagedDocument.energy_bill_year1(result.reference)
        assert comparison["energy_bill_year1_delta_in_euro"] == {
            "min": delta.minimum,
            "best": delta.best_estimate,
            "max": delta.maximum,
        }

    def test_the_withholding_rule_declares_it_free_of_the_investment(self):
        """The rule's own list, so a later change to it is a decision someone writes down."""
        assert "energy_bill_year1_delta_in_euro" in UnpricedInvestment.PRICE_FREE_DIFFERENCES
