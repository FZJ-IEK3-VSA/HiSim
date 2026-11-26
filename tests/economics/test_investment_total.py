"""The document refuses a plan whose year-0 investment is not the sum of its year-0 stages' split (renovisorissues #78).

The frontend draws both `plan.totals.investment_year0_in_euro` and the `investment_by_stage` entries of the `by_subject`
rows; in production they once read 141,919 against 84,262 EUR. `StagedDocument.validate` now checks the identity slot by
slot (hisim-investment-total-invariant-a62b), and a total withheld over an unpriced purchase is null exactly when the
plan buys one.
"""

import copy
import json
from typing import Any, Dict

import pytest

from hisim.economics.parameters import EconomicParameters
from hisim.economics.staged import StagedEvaluator
from hisim.economics.staged_document import InvestmentTotalError, StagedDocument

from tests.economics.synthetic_stages import (
    SyntheticPlan,
    baseline_stage,
    brownfield_perspective,
    envelope_stage,
    heat_pump_stage,
    write_database,
)

pytestmark = pytest.mark.base

#: The measure map of the synthetic plan.
MEASURE_IDS = {SyntheticPlan.ENVELOPE_SUBJECT: "external_insulation", SyntheticPlan.HEAT_PUMP_SUBJECT: "heating_system"}


@pytest.fixture(name="database", scope="module")
def fixture_database(tmp_path_factory):
    """The synthetic cost database, written once for the whole module."""
    return write_database(str(tmp_path_factory.mktemp("investment_total_database")))


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


def _document(database, parameters, tmp_path, unpriced=()) -> Dict[str, Any]:
    """The written document of the envelope in year 0 and the heat pump in year 4, read back from disk."""
    result = StagedEvaluator(database).evaluate(
        [baseline_stage(), envelope_stage(0), heat_pump_stage(4)], parameters, brownfield_perspective()
    )
    path = tmp_path / StagedDocument.FILE_NAME
    StagedDocument(
        result, parameters, brownfield_perspective(), measure_ids=MEASURE_IDS, unpriced_subjects=unpriced
    ).write(path)
    document: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def _split(document: Dict[str, Any], subject: str, stage: int) -> Dict[str, Any]:
    """The `investment_by_stage` entry of one plan row and one stage."""
    row = next(row for row in document["plan"]["by_subject"] if row["subject"] == subject)
    split: Dict[str, Any] = next(entry for entry in row["investment_by_stage"] if entry["stage"] == stage)
    return split


class TestTheIdentity:
    """`StagedDocument.assert_investment_total_matches_stages` on a consistent document and on tampered ones."""

    def test_a_written_document_holds_it(self, database, parameters, tmp_path):
        """The total is the envelope's year-0 purchase; the heat pump of year 4 is in neither side."""
        document = _document(database, parameters, tmp_path)
        StagedDocument.assert_investment_total_matches_stages(document)
        total = document["plan"]["totals"]["investment_year0_in_euro"]
        assert total["best"] == pytest.approx(_split(document, SyntheticPlan.ENVELOPE_SUBJECT, 1)["investment_in_euro"]["best"])

    def test_a_tampered_total_is_refused_by_validate(self, database, parameters, tmp_path):
        """A total one euro off its parts fails validation, naming the slot and both figures."""
        broken = copy.deepcopy(_document(database, parameters, tmp_path))
        broken["plan"]["totals"]["investment_year0_in_euro"]["max"] += 1.0
        with pytest.raises(InvestmentTotalError, match="slot 'max'"):
            StagedDocument.validate(broken)

    def test_a_tampered_year_zero_split_is_refused(self, database, parameters, tmp_path):
        """The parts are checked as much as the total: a row's year-0 entry moved alone breaks the sum."""
        broken = copy.deepcopy(_document(database, parameters, tmp_path))
        for key in ("min", "best", "max"):
            _split(broken, SyntheticPlan.ENVELOPE_SUBJECT, 1)["investment_in_euro"][key] += 500.0
        with pytest.raises(InvestmentTotalError):
            StagedDocument.assert_investment_total_matches_stages(broken)

    def test_a_later_stage_is_not_a_part(self, database, parameters, tmp_path):
        """The heat pump bought in year 4 is not year-0 investment: changing its split changes nothing checked here."""
        document = copy.deepcopy(_document(database, parameters, tmp_path))
        for key in ("min", "best", "max"):
            _split(document, SyntheticPlan.HEAT_PUMP_SUBJECT, 2)["investment_in_euro"][key] += 500.0
        StagedDocument.assert_investment_total_matches_stages(document)


class TestAWithheldTotal:
    """A null total is the investment withheld over an unpriced purchase, and only that."""

    def test_an_unpriced_purchase_writes_a_null_total(self, database, parameters, tmp_path):
        """The envelope without a price: the total is null and the document validates."""
        document = _document(database, parameters, tmp_path, unpriced=[SyntheticPlan.ENVELOPE_SUBJECT])
        assert document["plan"]["totals"]["investment_year0_in_euro"] is None
        StagedDocument.validate(document)

    def test_a_null_total_without_an_unpriced_purchase_is_refused(self, database, parameters, tmp_path):
        """A null with every subject priced would hide a known investment."""
        broken = copy.deepcopy(_document(database, parameters, tmp_path))
        broken["plan"]["totals"]["investment_year0_in_euro"] = None
        with pytest.raises(InvestmentTotalError, match="null"):
            StagedDocument.assert_investment_total_matches_stages(broken)

    def test_a_number_beside_an_unpriced_purchase_is_refused(self, database, parameters, tmp_path):
        """A stated total over a placeholder zero is the figure the withholding exists to prevent."""
        broken = copy.deepcopy(_document(database, parameters, tmp_path, unpriced=[SyntheticPlan.ENVELOPE_SUBJECT]))
        broken["plan"]["totals"]["investment_year0_in_euro"] = {"min": 0.0, "best": 0.0, "max": 0.0}
        with pytest.raises(InvestmentTotalError, match="unknown"):
            StagedDocument.assert_investment_total_matches_stages(broken)
