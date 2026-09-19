"""``economics_result.json``: that it validates, that its stacks add up, and that it says ids.

The document is a contract with a frontend that cannot check it, so the checks that matter are
the ones a reader of a chart would otherwise have to trust: the written file matches the shipped
schema, every breakdown sums to the total it belongs to per uncertainty slot, the eight groups
are in one fixed order in both evaluations, nothing in the document is a display label, and every
subject the mapping report named carries its ``measure_id``.

The plan under test is the synthetic one of ``tests/economics/synthetic_stages.py``: a baseline
gas boiler, an envelope measure in year 0 and a heat pump in year 4, priced against a country
whose only price is one the fixture wrote.
"""

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from hisim.economics.parameters import EconomicParameters
from hisim.economics.staged import StagedEvaluator
from hisim.economics.staged_document import CostGroup, CostGroups, StagedDocument
from hisim.economics.timeline import CostCategory

from tests.economics.synthetic_stages import (
    SyntheticPlan,
    baseline_stage,
    brownfield_perspective,
    envelope_stage,
    heat_pump_stage,
    write_database,
)

pytestmark = pytest.mark.base


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


@pytest.fixture(name="document")
def fixture_document(tmp_path, parameters, database) -> Dict[str, Any]:
    """The written document of the synthetic three-stage plan, read back from disk.

    Read back rather than taken from :meth:`StagedDocument.to_json` on purpose: every assertion
    below is about the file a backend serves, so anything JSON cannot carry has to fail here.
    """
    perspective = brownfield_perspective()
    result = StagedEvaluator(database).evaluate(
        [baseline_stage(), envelope_stage(0), heat_pump_stage(4)], parameters, perspective
    )
    path = tmp_path / StagedDocument.FILE_NAME
    StagedDocument(
        result,
        parameters,
        perspective,
        measure_ids={
            SyntheticPlan.ENVELOPE_SUBJECT: "external_insulation",
            SyntheticPlan.HEAT_PUMP_SUBJECT: "heating_system",
        },
        unpriced_subjects={SyntheticPlan.ENVELOPE_SUBJECT},
    ).write(path)
    document: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


@pytest.fixture(name="database", scope="module")
def fixture_database(tmp_path_factory):
    """The synthetic cost database, written once for the whole module."""
    return write_database(str(tmp_path_factory.mktemp("document_cost_database")))


class TestTheSchema:
    """The written file validates, and the schema is strict enough for that to mean something."""

    def test_the_written_document_validates(self, document):
        """The file a backend serves matches ``economics_result.schema.json``."""
        StagedDocument.validate(document)

    def test_the_schema_is_shipped_beside_the_module(self):
        """A packaged HiSim carries the schema, or nothing downstream can check a document."""
        assert StagedDocument.schema_path().is_file()
        assert StagedDocument.schema()["$schema"].endswith("2020-12/schema")

    def test_a_document_missing_a_stack_is_rejected(self, document):
        """The schema would catch a group quietly disappearing from an evaluation."""
        import jsonschema

        broken = json.loads(json.dumps(document))
        broken["plan"]["by_group"].pop(CostGroup.ENERGY.value)
        with pytest.raises(jsonschema.ValidationError):
            StagedDocument.validate(broken)

    def test_a_band_missing_a_slot_is_rejected(self, document):
        """A collapsed band — the one thing the E-spec forbids everywhere — does not validate."""
        import jsonschema

        broken = json.loads(json.dumps(document))
        broken["plan"]["totals"]["npv_in_euro"] = 1234.0
        with pytest.raises(jsonschema.ValidationError):
            StagedDocument.validate(broken)


class TestTheStacksAddUp:
    """E-spec §0: every breakdown sums to its total per slot, to the cent, on the written file.

    Four stacks are checked, because the four charts that read them (V1, V2, V3, V4) each show a
    total beside its parts: the group stack of the totals, the group stack of every year, the
    per-subject NPVs, and the cumulative series against the annual one.
    """

    SLOTS = ("min", "best", "max")

    @pytest.mark.parametrize("variant", ["reference", "plan"])
    def test_the_group_stack_sums_to_the_npv(self, document, variant):
        """Chart V2 stacks the eight groups under the headline NPV."""
        evaluation = document[variant]
        for slot in self.SLOTS:
            stack = sum(band[slot] for band in evaluation["by_group"].values())
            assert stack == pytest.approx(evaluation["totals"]["npv_in_euro"][slot], abs=0.01)

    @pytest.mark.parametrize("variant", ["reference", "plan"])
    def test_the_category_pivot_sums_to_the_npv(self, document, variant):
        """The engine's own categories are the finer cut of the same total."""
        evaluation = document[variant]
        for slot in self.SLOTS:
            stack = sum(band[slot] for band in evaluation["by_category"].values())
            assert stack == pytest.approx(evaluation["totals"]["npv_in_euro"][slot], abs=0.01)

    @pytest.mark.parametrize("variant", ["reference", "plan"])
    def test_the_subject_rows_sum_to_the_npv(self, document, variant):
        """Chart V4 filters the same rows by stage, so they must be the whole of the NPV."""
        evaluation = document[variant]
        for slot in self.SLOTS:
            stack = sum(row["npv_in_euro"][slot] for row in evaluation["by_subject"])
            assert stack == pytest.approx(evaluation["totals"]["npv_in_euro"][slot], abs=0.01)

    @pytest.mark.parametrize("variant", ["reference", "plan"])
    def test_every_year_stack_sums_to_that_years_total(self, document, variant):
        """Chart V1 draws one column per year with a band on the column total."""
        for row in document[variant]["annual"]:
            for slot in self.SLOTS:
                stack = sum(band[slot] for band in row["by_group"].values())
                assert stack == pytest.approx(row["total_nominal_in_euro"][slot], abs=0.01), row["year"]

    @pytest.mark.parametrize("variant", ["reference", "plan"])
    def test_the_cumulative_series_is_the_running_annual_one(self, document, variant):
        """Chart V3's curve and chart V1's columns are two views of one series."""
        evaluation = document[variant]
        running = 0.0
        for annual, cumulative in zip(evaluation["annual"], evaluation["cumulative"]):
            running += annual["total_nominal_in_euro"]["best"]
            assert cumulative["nominal_in_euro"]["best"] == pytest.approx(running, abs=0.01)


class TestTheDocumentShape:
    """What the document says, beyond the arithmetic: order, ids, stages and unpriced subjects."""

    def test_the_groups_are_the_eight_in_a_fixed_order(self, document):
        """The order is stated once and both evaluations use it, or the colours would shift."""
        expected = [group.value for group in CostGroup]
        assert document["groups"] == expected
        assert list(document["plan"]["by_group"]) == expected
        assert list(document["reference"]["by_group"]) == expected

    def test_every_cost_category_has_a_group(self):
        """A category added to the engine without a stack breaks the sums, so it breaks at import."""
        for category in CostCategory:
            assert isinstance(CostGroups.of(category), CostGroup)

    def test_measure_ids_are_stamped_from_the_mapping(self, document):
        """Chart V4 needs the measure that created each subject; the baseline's is null."""
        rows = {row["subject"]: row for row in document["plan"]["by_subject"]}
        assert rows[SyntheticPlan.HEAT_PUMP_SUBJECT]["measure_id"] == "heating_system"
        assert rows[SyntheticPlan.ENVELOPE_SUBJECT]["measure_id"] == "external_insulation"
        assert rows[SyntheticPlan.BOILER_SUBJECT]["measure_id"] is None

    def test_every_subject_carries_the_stage_that_bought_it(self, document):
        """The waterfall of chart V4 is a filter on this field and on nothing else."""
        rows = {row["subject"]: row for row in document["plan"]["by_subject"]}
        assert rows[SyntheticPlan.ENVELOPE_SUBJECT]["stage"] == 1
        assert rows[SyntheticPlan.HEAT_PUMP_SUBJECT]["stage"] == 2

    def test_an_unpriced_subject_says_so_rather_than_disappearing(self, document):
        """A measure with no price behind it is in the document, flagged (step 10 §1)."""
        rows = {row["subject"]: row for row in document["plan"]["by_subject"]}
        assert rows[SyntheticPlan.ENVELOPE_SUBJECT]["unpriced"] is True
        assert rows[SyntheticPlan.BOILER_SUBJECT]["unpriced"] is False

    def test_the_document_carries_ids_and_no_display_labels(self, document):
        """Ids only: a subject, an asset class and a carrier are named as the engine names them."""
        for row in document["plan"]["by_subject"]:
            assert row["subject"] in {
                SyntheticPlan.BOILER_SUBJECT,
                SyntheticPlan.ENVELOPE_SUBJECT,
                SyntheticPlan.HEAT_PUMP_SUBJECT,
                "ELECTRICITY",
            }
            assert row["asset_class"] in (None, "GasHeater", "WallExternalInsulation", "HeatPump")

    def test_the_stage_rows_carry_year_job_and_measures(self, document):
        """A stage can be traced back to the job that produced it and the measures it added."""
        stages = document["stages"]
        assert [stage["from_year"] for stage in stages] == [0, 0, 4]
        assert stages[2]["job_id"] == "job-heat-pump"
        assert stages[2]["measures"] == ["heating_system"]

    def test_every_year_knows_its_calendar_year_and_its_stage(self, document):
        """Relative years plus one anchor: nothing downstream adds the simulation year itself."""
        for row in document["plan"]["annual"]:
            assert row["calendar_year"] == SyntheticPlan.YEAR + row["year"]
        stages = {row["year"]: row["stage"] for row in document["plan"]["annual"]}
        assert stages[0] == 1
        assert stages[4] == 2

    def test_the_stage_start_of_a_later_stage_is_an_event(self, document):
        """Chart V9's timeline reads the markers rather than re-deriving them from the stages."""
        events = {
            row["year"]: [event["kind"] for event in row["events"]] for row in document["plan"]["annual"]
        }
        assert "stage_start" in events[4]
        assert "investment" in events[4]

    def test_no_catalogue_means_undetermined_and_never_zero(self, document):
        """Step 10 §1: with no catalogue every subsidy row is a question, not a grant of nothing."""
        rows = document["plan"]["subsidies"]
        assert rows, "a plan that buys something must say what it does not know about support"
        for row in rows:
            assert row["status"] == "undetermined"
            assert row["amount_in_euro"] is None
            assert SyntheticPlan.COUNTRY in row["note"]

    def test_a_cash_plan_carries_no_financing_block(self, document):
        """``financing`` is null for a cash purchase rather than an empty list of loans."""
        assert document["plan"]["financing"] is None

    def test_the_comparison_is_plan_minus_reference(self, document):
        """The sign convention of the delta, checked against the two totals it is taken from."""
        delta = document["comparison"]["npv_delta_in_euro"]["best"]
        plan = document["plan"]["totals"]["npv_in_euro"]["best"]
        reference = document["reference"]["totals"]["npv_in_euro"]["best"]
        assert delta == pytest.approx(plan - reference, abs=0.01)

    def test_the_engine_block_names_the_code_that_produced_it(self, document):
        """A stored document says which commit and which specification revision made it."""
        assert document["engine"]["economics_version"] == StagedDocument.ECONOMICS_VERSION
        assert "hisim_commit" in document["engine"]


class TestFinancing:
    """A plan with a loan publishes one loan per stage that borrowed, with its debt service."""

    def test_a_financed_plan_publishes_its_loans(self, database, parameters, tmp_path):
        """The loan's principal is positive, its schedule is as long as the horizon."""
        from hisim.economics.financing import FinancingPlan
        from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode

        perspective = Perspective(
            id="owner_monthly",
            installation_context=InstallationContext.BROWNFIELD,
            subsidy_mode=SubsidyMode.none(),
            financing=FinancingPlan(term_in_years=10),
        )
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), heat_pump_stage(3)], parameters, perspective
        )
        path = tmp_path / "financed.json"
        written = StagedDocument(result, parameters, perspective).write(path)
        financing = written["plan"]["financing"]
        assert financing is not None
        assert financing["loans"], "a financed plan borrows at least once"
        for loan in financing["loans"]:
            assert loan["principal_in_euro"]["best"] > 0
            assert len(loan["debt_service_by_year_in_euro"]) == parameters.observation_period_in_years + 1
        assert Path(path).is_file()
