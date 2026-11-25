"""A plan that buys an unpriced subject states no payback year, and says why (renovisorissues #87, point 1).

An unpriced subject (an envelope measure without a cost block, a measure HiSim holds no price for) is booked at a
placeholder zero. Before hisim-payback-unpriced-null-58d1 the document stated a payback year over that partial
investment: the backend's Irish 1968 house with unpriced windows paid back in years 1 to 3. By owner decision of
2026-11-13 such a plan states ``null`` in every end of ``comparison.discounted_payback_year``, and
``comparison.discounted_payback_year_null_reason`` names the unpriced subjects; there is no lower bound. A kept envelope
element is unpriced too but belongs to the house as it was, so it withholds nothing.
"""

import json
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence

import pytest

from hisim.economics.evaluator import SubjectCostFacts
from hisim.economics.parameters import EconomicParameters
from hisim.economics.staged import Stage, StagedEvaluator, StagedResult
from hisim.economics.staged_document import StagedDocument, UnpricedPayback
from hisim.loadtypes import ComponentType, Units

from tests.economics.synthetic_stages import (
    SyntheticPlan,
    baseline_stage,
    brownfield_perspective,
    device_facts,
    state_inputs,
    write_database,
)

pytestmark = pytest.mark.base

#: The unpriced envelope measure of the backend's case, which is also its subject.
WINDOWS = "window_replacement"

#: A kept envelope element: unpriced in the reference and the plan alike, with no measure.
KEPT_DOOR = "envelope_door"

#: A heat pump cheap enough that the plan pays back within the synthetic horizon.
HEAT_PUMP_INVESTMENT_IN_EURO = 3000.0


def _row(subject: str, measure_id: Optional[str], unpriced: bool) -> Dict[str, Any]:
    """The three fields of a ``by_subject`` row the rule reads."""
    return {"subject": subject, "measure_id": measure_id, "unpriced": unpriced}


def _unpriced_facts(subject: str, asset_class: ComponentType) -> SubjectCostFacts:
    """A subject booked at a placeholder zero, as the translator books an envelope subject without a price."""
    facts = replace(device_facts(asset_class, 20.0, 0.0), size_unit=Units.SQUARE_METER, price_is_unknown=True)
    return SubjectCostFacts(subject, facts)


def _stage(windows_in_euro: Optional[float], kept_door: bool = False) -> Stage:
    """Stage 1 in year 0: a heat pump replaces the boiler and the windows are replaced.

    Args:
        windows_in_euro: The windows' price; None books them at a placeholder zero, unpriced.
        kept_door: Whether the kept door is a subject of the stage.

    Returns:
        The stage.
    """
    subjects = [(SyntheticPlan.HEAT_PUMP_SUBJECT, ComponentType.HEAT_PUMP, 9.0, HEAT_PUMP_INVESTMENT_IN_EURO)]
    if windows_in_euro is not None:
        subjects.append((WINDOWS, ComponentType.WINDOWS_TRIPLE_GLAZED, 20.0, windows_in_euro))
    inputs = state_inputs(subjects, SyntheticPlan.RENOVATED_ELECTRICITY_IN_KWH)
    if windows_in_euro is None:
        inputs.cost_facts.append(_unpriced_facts(WINDOWS, ComponentType.WINDOWS_TRIPLE_GLAZED))
    if kept_door:
        inputs.cost_facts.append(_unpriced_facts(KEPT_DOOR, ComponentType.WINDOWS_TRIPLE_GLAZED))
    return Stage(inputs=inputs, from_year=0, label="stage 1", measures=("heating_system", WINDOWS), job_id="job-1")


def _baseline(kept_door: bool = False) -> Stage:
    """Stage 0, the house as it is, with or without the kept door as a subject."""
    stage = baseline_stage()
    if kept_door:
        stage.inputs.cost_facts.append(_unpriced_facts(KEPT_DOOR, ComponentType.WINDOWS_TRIPLE_GLAZED))
    return stage


@pytest.fixture(name="database", scope="module")
def fixture_database(tmp_path_factory):
    """The synthetic cost database, written once for the whole module."""
    return write_database(str(tmp_path_factory.mktemp("unpriced_payback_database")))


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


def _write(result: StagedResult, parameters: EconomicParameters, path, unpriced: Sequence[str]) -> Dict[str, Any]:
    """Write the plan's document with the translator's measure map and read it back from disk."""
    StagedDocument(
        result,
        parameters,
        brownfield_perspective(),
        measure_ids={SyntheticPlan.HEAT_PUMP_SUBJECT: "heating_system", WINDOWS: WINDOWS, KEPT_DOOR: None},
        unpriced_subjects=unpriced,
    ).write(path)
    document: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


class TestTheUnpricedSubjects:
    """`UnpricedPayback.subjects` picks the unpriced rows the plan buys."""

    def test_an_unpriced_measure_counts(self):
        """A row with a measure and no price withholds the payback."""
        assert UnpricedPayback.subjects([_row(WINDOWS, WINDOWS, True)], has_measure_map=True) == [WINDOWS]

    def test_a_kept_element_does_not_count(self):
        """An unpriced row without a measure is the house as it was, renewed alike in the reference."""
        assert UnpricedPayback.subjects([_row(KEPT_DOOR, None, True)], has_measure_map=True) == []

    def test_a_priced_row_does_not_count(self):
        """A measure with a price, or one a reader's quote priced (its row says unpriced false)."""
        assert UnpricedPayback.subjects([_row("HeatPump", "heating_system", False)], has_measure_map=True) == []

    def test_without_a_measure_map_every_unpriced_row_counts(self):
        """No map, no telling a measure from a kept element: the payback is withheld rather than guessed."""
        assert UnpricedPayback.subjects([_row(KEPT_DOOR, None, True)], has_measure_map=False) == [KEPT_DOOR]

    def test_the_subjects_are_sorted(self):
        """The reason lists them in a stable order, so two runs write the same bytes."""
        rows = [_row("roof_insulation", "roof_insulation", True), _row(WINDOWS, WINDOWS, True)]
        assert UnpricedPayback.subjects(list(reversed(rows)), has_measure_map=True) == ["roof_insulation", WINDOWS]


class TestTheReason:
    """`UnpricedPayback.reason` is the machine-readable reason, or None."""

    def test_nothing_unpriced_gives_no_reason(self):
        """A plan that buys nothing unpriced states its payback and no reason."""
        assert UnpricedPayback.reason([_row("HeatPump", "heating_system", False)], []) is None

    def test_the_reason_names_the_subjects_and_their_measures(self):
        """Code, subjects, the distinct measures of their rows, and a sentence naming the subjects."""
        rows: List[Dict[str, Any]] = [_row(WINDOWS, WINDOWS, True), _row("HeatPump", "heating_system", False)]
        reason = UnpricedPayback.reason(rows, [WINDOWS])
        assert reason is not None
        assert reason["code"] == UnpricedPayback.CODE == "unpriced_subjects"
        assert reason["unpriced_subjects"] == [WINDOWS]
        assert reason["measure_ids"] == [WINDOWS]
        assert WINDOWS in reason["note"]

    def test_a_subject_without_a_measure_adds_no_measure(self):
        """Without a measure map a kept element can withhold the payback; it names no measure."""
        reason = UnpricedPayback.reason([_row(KEPT_DOOR, None, True)], [KEPT_DOOR])
        assert reason is not None and reason["measure_ids"] == []


class TestTheDocument:
    """The written document of a plan with unpriced windows (the backend's case of #87)."""

    def test_unpriced_windows_state_no_payback_and_say_why(self, database, parameters, tmp_path):
        """The engine pays back within the horizon over the zero; the document states null and names the windows."""
        result = StagedEvaluator(database).evaluate([baseline_stage(), _stage(None)], parameters, brownfield_perspective())
        engine_band = result.comparison.discounted_payback_envelope.to_band()
        assert engine_band["max"] is not None and engine_band["max"] <= 3, engine_band

        document = _write(result, parameters, tmp_path / "unpriced.json", unpriced=[WINDOWS])

        comparison = document["comparison"]
        assert comparison["discounted_payback_year"] == {"min": None, "best": None, "max": None}
        reason = comparison["discounted_payback_year_null_reason"]
        assert reason["code"] == "unpriced_subjects"
        assert reason["unpriced_subjects"] == [WINDOWS]
        assert reason["measure_ids"] == [WINDOWS]
        rows = {row["subject"]: row for row in document["plan"]["by_subject"]}
        assert rows[WINDOWS]["unpriced"] is True

    def test_priced_windows_state_the_payback_by_the_stays_repaid_rule(self, database, parameters, tmp_path):
        """With every subject priced the band is the engine's: the first year each world stays repaid."""
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), _stage(4000.0)], parameters, brownfield_perspective()
        )

        document = _write(result, parameters, tmp_path / "priced.json", unpriced=[])

        comparison = document["comparison"]
        assert comparison["discounted_payback_year_null_reason"] is None
        assert comparison["discounted_payback_year"] == result.comparison.discounted_payback_envelope.to_band()
        assert comparison["discounted_payback_year"]["best"] is not None
        best = comparison["discounted_payback_year"]["best"]
        discounted = [row["discounted_in_euro"]["best"] for row in comparison["cumulative_delta"]]
        assert all(value <= 0 for value in discounted[best:])
        assert best == 0 or discounted[best - 1] > 0

    def test_a_kept_unpriced_element_withholds_nothing(self, database, parameters, tmp_path):
        """The kept door is unpriced in the reference and the plan alike and carries no measure."""
        result = StagedEvaluator(database).evaluate(
            [_baseline(kept_door=True), _stage(4000.0, kept_door=True)], parameters, brownfield_perspective()
        )

        document = _write(result, parameters, tmp_path / "kept.json", unpriced=[KEPT_DOOR])

        comparison = document["comparison"]
        assert comparison["discounted_payback_year_null_reason"] is None
        assert comparison["discounted_payback_year"] == result.comparison.discounted_payback_envelope.to_band()
        rows = {row["subject"]: row for row in document["plan"]["by_subject"]}
        assert rows[KEPT_DOOR]["unpriced"] is True and rows[KEPT_DOOR]["measure_id"] is None
