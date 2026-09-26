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

from hisim.economics.carriers import revenue_subject
from hisim.economics.parameters import EconomicParameters
from hisim.economics.staged import StagedEvaluator
from hisim.economics.staged_document import CostGroup, CostGroups, StagedDocument
from hisim.economics.subsidies import PayoutKind
from hisim.economics.timeline import CostCategory
from hisim.economics.views import carrier_year_one_bills
from hisim.loadtypes import ComponentType

from tests.economics.synthetic_stages import (
    SyntheticPlan,
    always_eligible_catalog,
    baseline_stage,
    brownfield_perspective,
    envelope_stage,
    grant_and_soft_loan_catalog,
    heat_pump_stage,
    state_inputs,
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
        """A packaged HiSim carries the schema, or nothing downstream can check a document.

        The ``$schema`` dialect string is deliberately not asserted: it is an upstream meta
        version and pinning it fails on a dialect upgrade that has no bearing on the document.
        What matters is that the file is there and describes the document's own top level.
        """
        assert StagedDocument.schema_path().is_file()
        schema = StagedDocument.schema()
        assert set(schema["required"]) <= set(schema["properties"])
        assert "plan" in schema["properties"] and "reference" in schema["properties"]

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

    def test_an_awarded_row_without_an_amount_is_rejected(self, document):
        """Null is the amount of a refused or undecided scheme only; an award always states a band."""
        import jsonschema

        broken = json.loads(json.dumps(document))
        broken["plan"]["subsidies"] = [
            {
                "scheme": "SOME_LOAN",
                "measure_id": None,
                "stage": 1,
                "status": "awarded",
                "amount_in_euro": None,
                "amount_by_year_in_euro": None,
                "binding_cap": None,
                "open_questions": [],
                "note": None,
            }
        ]
        with pytest.raises(jsonschema.ValidationError):
            StagedDocument.validate(broken)
        zero = {"min": 0.0, "best": 0.0, "max": 0.0}
        broken["plan"]["subsidies"][0].update(amount_in_euro=zero, amount_by_year_in_euro=[])
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

    @pytest.mark.parametrize("variant", ["reference", "plan"])
    def test_the_headline_monthly_figure_is_the_annuity_over_twelve(self, document, variant):
        """hisim-cyc.6: ``monthly_equivalent_cost_in_euro`` is EAC / 12 in every slot.

        Twelve is written as a literal on purpose: comparing with the constant the document used
        would pass whatever that constant said.
        """
        totals = document[variant]["totals"]
        for slot in ("min", "best", "max"):
            assert totals["monthly_equivalent_cost_in_euro"][slot] == pytest.approx(
                totals["equivalent_annual_cost_in_euro"][slot] / 12.0
            )

    @pytest.mark.parametrize("variant", ["reference", "plan"])
    def test_the_headline_monthly_figure_is_the_npv_as_a_level_payment(self, document, variant):
        """The monthly figure is the NPV times a hand-derived annuity factor, over twelve.

        The test above compares the figure with the document's own EAC, so a wrong annuity would
        pass it on both sides. Here the factor is worked out by hand at the synthetic plan's
        parameters, 3 % over 12 years, with the VDI 2067-1 capital recovery factor
        ``a = i (1 + i)^T / ((1 + i)^T - 1)``:

        - ``1.03^12 = 1.4257608868``
        - ``a = 0.03 x 1.4257608868 / 0.4257608868 = 0.1004620855``

        and the monthly figure is ``NPV x 0.1004620855 / 12``. The NPV is the document's own: it
        is a sum of discounted flows that involves no annuity, so the product checks the annuity
        and the months, and nothing else.
        """
        assert (SyntheticPlan.INTEREST_RATE, SyntheticPlan.HORIZON) == (0.03, 12)
        annuity_factor = 0.1004620855
        totals = document[variant]["totals"]
        for slot in ("min", "best", "max"):
            assert totals["monthly_equivalent_cost_in_euro"][slot] == pytest.approx(
                totals["npv_in_euro"][slot] * annuity_factor / 12.0, rel=1e-9
            )

    def test_the_headline_monthly_figure_is_not_the_first_years_cash(self, document):
        """The two monthly figures differ on the synthetic plan, which pays its envelope in year 0.

        Year 1 carries no investment there but the annuity spreads year 0's purchase over the
        horizon, so equality would mean the new key was wired to the old figure.
        """
        totals = document["plan"]["totals"]
        assert totals["monthly_equivalent_cost_in_euro"]["best"] != pytest.approx(
            totals["monthly_cost_year1_in_euro"]["best"]
        )

    def test_the_monthly_delta_is_the_annuity_delta_over_twelve(self, document):
        """``comparison.monthly_equivalent_cost_delta_in_euro`` is the EAC delta per month."""
        comparison = document["comparison"]
        for slot in ("min", "best", "max"):
            assert comparison["monthly_equivalent_cost_delta_in_euro"][slot] == pytest.approx(
                comparison["equivalent_annual_cost_delta_in_euro"][slot] / 12.0
            )

    def test_the_schema_requires_the_headline_monthly_figure(self, document):
        """A document without it does not validate, so a consumer can rely on its presence."""
        import jsonschema

        for path in (("plan", "totals"), ("comparison",)):
            broken = json.loads(json.dumps(document))
            block = broken
            for key in path:
                block = block[key]
            block.pop(
                "monthly_equivalent_cost_delta_in_euro"
                if path == ("comparison",)
                else "monthly_equivalent_cost_in_euro"
            )
            with pytest.raises(jsonschema.ValidationError):
                StagedDocument.validate(broken)

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
        """A stored document says which commit and which specification revision made it.

        The version is compared to a fixed literal rather than to the constant it was written
        from: comparing it to ``StagedDocument.ECONOMICS_VERSION`` passes whatever that constant
        says, so a wrong one would ship. ``cost-spec-v2`` is the specification revision this
        engine implements, and changing it is a decision that belongs in a diff of this line.
        """
        assert document["engine"]["economics_version"] == "cost-spec-v2"
        commit = document["engine"]["hisim_commit"]
        assert commit is None or isinstance(commit, str) and commit.strip() == commit

    def test_the_document_states_schema_version_two(self, document):
        """Version 2: awarded subsidy rows state their amount by year, and the monthly headline.

        A literal for the same reason as the economics version above. Version 2 is the format
        with hisim-cyc.5's awarded-row rules and hisim-cyc.6's required monthly keys, and a
        document of that shape stating 1 would tell a consumer it could skip both.
        """
        import jsonschema

        assert document["schema_version"] == 2
        StagedDocument.validate(document)
        with pytest.raises(jsonschema.ValidationError):
            StagedDocument.validate({**document, "schema_version": 1})


class TestTheParametersBlock:
    """``parameters`` states the assumptions *and* is a legal ``--parameters`` file (step 13 §1.4).

    Input and output were two vocabularies until step 13: the block echoed ``horizon_years`` and
    ``perspective_id`` while the command read the engine's own field names, so the block a reader
    copied out of a document was rejected key by key when they handed it back. It is one table of
    keys now, which is why ``subsidy_mode`` and ``financing`` — the two inputs the block did not
    echo — are in it.
    """

    def test_it_echoes_the_subsidy_mode_the_plan_ran_under(self, document):
        """The synthetic perspective admits no scheme, and the document says so."""
        assert document["parameters"]["subsidy_mode"] == "none"

    def test_it_echoes_the_financing_the_plan_ran_under(self, document):
        """A cash purchase is a statement, not an absent key."""
        assert document["parameters"]["financing"] == {"kind": "cash"}

    def test_every_key_of_it_is_a_key_the_command_accepts(self, document):
        """Which is what makes a document's assumptions a runnable input file."""
        from hisim.economics.staged_parameters import ParameterKeys

        assert set(document["parameters"]) == set(ParameterKeys.ACCEPTED)


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

    def test_each_loans_debt_service_is_the_loan_that_borrowed_it(self, database, parameters, tmp_path):
        """Two loans repaid at once are told apart by the stage that took them out (step 12 §2.2).

        The envelope stage borrows in year 0 on a ten-year term and the heat-pump stage in year 3
        on another, so years 4..10 carry instalments of both. Attributing a payment to the stage
        *active* in its year — the rule the operating flows are spliced by — moves the first
        loan's years 3..10 onto the second loan, and the block then shows a ten-year loan repaid
        in two years. Each schedule must instead run the loan's own term from its own year: 1..10
        for the first and 4..12 for the second, the latter cut off by the twelve-year horizon.
        """
        from hisim.economics.financing import FinancingPlan
        from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode

        perspective = Perspective(
            id="owner_monthly",
            installation_context=InstallationContext.BROWNFIELD,
            subsidy_mode=SubsidyMode.none(),
            financing=FinancingPlan(term_in_years=10),
        )
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), envelope_stage(0), heat_pump_stage(3)], parameters, perspective
        )
        written = StagedDocument(result, parameters, perspective).write(tmp_path / "two_loans.json")
        loans = written["plan"]["financing"]["loans"]
        paying_years = [
            [
                year
                for year, band in enumerate(loan["debt_service_by_year_in_euro"])
                if abs(band["best"]) > 1e-9
            ]
            for loan in loans
        ]
        assert paying_years == [list(range(1, 11)), list(range(4, 13))]


class TestBandOrder:
    """Every amount the document writes reads ``min <= best <= max`` (step 12 §2.3)."""

    def test_the_cumulative_difference_stays_ordered_when_the_reference_band_is_wider(
        self, database, parameters, tmp_path
    ):
        """A per-slot sign flip must swap the ends, or the chart draws the range backwards.

        ``comparison.cumulative_discounted_savings_in_euro`` holds three independent per-slot
        curves rather than an envelope, so the low-world saving can exceed the high-world one when
        the reference's cost band is wider than the plan's — a volatile gas bill against a mostly
        fixed heat-pump investment. The document reports plan minus reference, which is the
        negation of that, and negating slot by slot would leave ``min = 100`` above ``max = -700``
        for savings of ``low = -100``, ``best = 250``, ``high = 700``. The ends are therefore taken
        by value: ``min = -700``, ``best = -250``, ``max = 100``.
        """
        perspective = brownfield_perspective()
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), heat_pump_stage(3)], parameters, perspective
        )
        horizon = parameters.observation_period_in_years
        result.comparison.cumulative_discounted_savings_in_euro = {
            "low": [-100.0] * (horizon + 1),
            "best_estimate": [250.0] * (horizon + 1),
            "high": [700.0] * (horizon + 1),
        }
        written = StagedDocument(result, parameters, perspective).write(tmp_path / "wide.json")
        for row in written["comparison"]["cumulative_delta"]:
            assert row["discounted_in_euro"] == {"min": -700.0, "best": -250.0, "max": 100.0}

    def test_an_unordered_band_is_refused_before_the_file_is_written(self, tmp_path):
        """The check the schema cannot express, stated as a refusal rather than as a chart bug."""
        from hisim.economics.staged_document import BandOrderError

        with pytest.raises(BandOrderError, match="min <= best <= max"):
            StagedDocument.assert_bands_ordered(
                {"plan": {"totals": {"npv_in_euro": {"min": 5.0, "best": 1.0, "max": 2.0}}}}
            )
        assert not list(Path(tmp_path).iterdir())


class TestSupportIsStatedOnce:
    """The ``Subsidies`` stack and the ``subsidies[]`` rows state the same support (hisim-cyc.5).

    A plan priced with subsidies on and no catalogue used to run the engine's §10.1 flat shim,
    which booked a grant into the year stacks and the NPV while the rows said nothing had been
    awarded: V1 drew a credit that V8 said did not exist. The shim is retired, a plan without a
    catalogue is priced with ``subsidy_mode: NONE``, and :meth:`StagedDocument.write` refuses any
    document whose two statements disagree in any year.
    """

    #: Share of the eligible cost the synthetic grant pays.
    GRANT_RATE = 0.3

    @staticmethod
    def _stages():
        """The three-stage synthetic plan: baseline, envelope in year 0, heat pump in year 4."""
        return [baseline_stage(), envelope_stage(0), heat_pump_stage(4)]

    @pytest.fixture(name="shim_database", scope="class")
    def fixture_shim_database(self, tmp_path_factory):
        """The synthetic database plus a heat-pump entry carrying a 25 % legacy flat share.

        The retired shim's share, still in the data: nothing may read it for a price.
        """
        return write_database(
            str(tmp_path_factory.mktemp("shim_cost_database")), heat_pump_legacy_flat_subsidy_share=0.25
        )

    def _write(self, database, parameters, catalog, path, stages=None, perspective=None) -> Dict[str, Any]:
        """Price a plan (the synthetic three stages by default) with subsidies on and write it."""
        perspective = perspective or brownfield_perspective(subsidies=True)
        result = StagedEvaluator(database).evaluate(stages or self._stages(), parameters, perspective, catalog)
        written: Dict[str, Any] = StagedDocument(result, parameters, perspective).write(path)
        return written

    @staticmethod
    def _soft_loan_perspective():
        """Brownfield, subsidies on, the investment financed by the synthetic soft loan."""
        from hisim.economics.financing import FinancingPlan
        from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode

        return Perspective(
            id="brownfield_net_soft_loan",
            installation_context=InstallationContext.BROWNFIELD,
            subsidy_mode=SubsidyMode.full(),
            financing=FinancingPlan(term_in_years=20, subsidized_by_scheme_id=SyntheticPlan.SOFT_LOAN_SCHEME),
        )

    def test_without_a_catalogue_no_year_books_support(self, shim_database, parameters, tmp_path):
        """No catalogue: every year's Subsidies stack is zero and the NPV carries no grant."""
        document = self._write(shim_database, parameters, None, tmp_path / "no_catalogue.json")

        for variant in ("reference", "plan"):
            for year in document[variant]["annual"]:
                assert year["by_group"][CostGroup.SUBSIDIES.value] == {"min": 0.0, "best": 0.0, "max": 0.0}
                assert all(event["kind"] != "subsidy" for event in year["events"])
            assert document[variant]["by_group"][CostGroup.SUBSIDIES.value] == {
                "min": 0.0,
                "best": 0.0,
                "max": 0.0,
            }
            assert all(row["status"] == "undetermined" for row in document[variant]["subsidies"])

    def test_without_a_catalogue_the_plan_equals_one_priced_with_subsidies_off(
        self, shim_database, parameters
    ):
        """What "priced with subsidy_mode NONE" means, stated as an equality of NPVs."""
        stages = self._stages()
        with_mode_on = StagedEvaluator(shim_database).evaluate(
            stages, parameters, brownfield_perspective(subsidies=True)
        )
        with_mode_off = StagedEvaluator(shim_database).evaluate(
            stages, parameters, brownfield_perspective(subsidies=False)
        )
        assert with_mode_on.plan.total_npv_in_euro == with_mode_off.plan.total_npv_in_euro

    def test_without_a_catalogue_the_parameters_block_says_none(self, shim_database, parameters, tmp_path):
        """The echo is a statement about the run, even when the caller passed a FULL perspective."""
        document = self._write(shim_database, parameters, None, tmp_path / "echo.json")
        assert document["parameters"]["subsidy_mode"] == "none"
        assert document["parameters"]["subsidy_catalog"] is None

    def test_with_a_catalogue_each_grant_is_booked_in_its_stage_year(self, database, parameters, tmp_path):
        """The awarded rows are the stack: the envelope's grant in year 0, the heat pump's in year 4.

        One scheme funds both measures, so each row must carry its own subject's grant rather than
        the scheme's total over the plan.
        """
        document = self._write(
            database, parameters, always_eligible_catalog(self.GRANT_RATE), tmp_path / "catalogue.json"
        )
        plan = document["plan"]
        awarded = {row["stage"]: row for row in plan["subsidies"] if row["status"] == "awarded"}
        assert set(awarded) == {1, 2}
        assert {row["scheme"] for row in awarded.values()} == {SyntheticPlan.GRANT_SCHEME}
        subsidies_by_year = {
            year["year"]: year["by_group"][CostGroup.SUBSIDIES.value]["best"] for year in plan["annual"]
        }
        assert subsidies_by_year[0] == pytest.approx(awarded[1]["amount_in_euro"]["best"], abs=0.01)
        assert subsidies_by_year[4] == pytest.approx(awarded[2]["amount_in_euro"]["best"], abs=0.01)
        assert awarded[1]["amount_in_euro"]["best"] == pytest.approx(
            -self.GRANT_RATE * SyntheticPlan.ENVELOPE_INVESTMENT_IN_EURO, abs=0.01
        )
        # The heat pump is bought in year 4, so its price — and the grant, a share of it — is
        # escalated four years at the investment rate (the synthetic database has no per-class
        # rate, so the parameter's general one applies): -rate * price * (1 + escalation) ** 4.
        escalation = (1.0 + parameters.investment_price_escalation_rate) ** 4
        assert awarded[2]["amount_in_euro"]["best"] == pytest.approx(
            -self.GRANT_RATE * SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO * escalation, abs=0.01
        )
        assert [booking["year"] for booking in awarded[1]["amount_by_year_in_euro"]] == [0]
        assert [booking["year"] for booking in awarded[2]["amount_by_year_in_euro"]] == [4]
        assert all(
            amount == 0.0 for year, amount in subsidies_by_year.items() if year not in (0, 4)
        ), "support booked outside the two stage years"

    def test_the_catalogue_named_is_the_one_the_result_was_priced_with(self, database, parameters, tmp_path):
        """The document reads the catalogue id off the result; a caller cannot supply another."""
        catalog = always_eligible_catalog(self.GRANT_RATE)
        document = self._write(database, parameters, catalog, tmp_path / "named.json")
        assert document["parameters"]["subsidy_catalog"] == StagedEvaluator.catalog_id(
            catalog, SyntheticPlan.COUNTRY
        )
        assert document["parameters"]["subsidy_mode"] == "full"

    def test_a_grant_moved_to_another_year_is_refused(self, database, parameters, tmp_path):
        """The per-year statement: the heat pump's year-4 grant moved into year 0, total conserved.

        The horizon total and every event still agree, so only the year-by-year comparison with the
        rows' ``amount_by_year_in_euro`` can see it.
        """
        from hisim.economics.staged_document import SubsidyReconciliationError

        document = self._write(
            database, parameters, always_eligible_catalog(self.GRANT_RATE), tmp_path / "moved.json"
        )
        broken = json.loads(json.dumps(document))
        annual = broken["plan"]["annual"]
        moved = annual[4]["by_group"][CostGroup.SUBSIDIES.value]
        for key in ("min", "best", "max"):
            annual[0]["by_group"][CostGroup.SUBSIDIES.value][key] += moved[key]
        annual[4]["by_group"][CostGroup.SUBSIDIES.value] = {"min": 0.0, "best": 0.0, "max": 0.0}
        with pytest.raises(SubsidyReconciliationError, match="plan: in year 0 the Subsidies group"):
            StagedDocument.assert_subsidies_reconciled(broken)

    def test_write_refuses_a_disagreeing_document_and_writes_no_file(self, database, parameters, tmp_path):
        """The check runs inside :meth:`StagedDocument.write`, before the first byte is written."""
        from hisim.economics.staged_document import SubsidyReconciliationError

        class TamperedDocument(StagedDocument):
            """A document whose year-4 stack books a grant no row awards."""

            def to_json(self) -> Dict[str, Any]:
                document = super().to_json()
                document["plan"]["annual"][4]["by_group"][CostGroup.SUBSIDIES.value] = {
                    "min": -6015.0,
                    "best": -6015.0,
                    "max": -6015.0,
                }
                return document

        perspective = brownfield_perspective(subsidies=True)
        result = StagedEvaluator(database).evaluate(self._stages(), parameters, perspective)
        path = tmp_path / "tampered" / StagedDocument.FILE_NAME
        with pytest.raises(SubsidyReconciliationError, match="in year 4"):
            TamperedDocument(result, parameters, perspective).write(path)
        assert not path.exists()
        assert not path.parent.exists()

    def test_a_soft_loans_repayment_grant_is_stated_on_its_loan_row(self, database, parameters, tmp_path):
        """The loan's repayment grant is booked under ``financing``; its LOAN_TERMS row states it.

        Each stage takes out its own loan, so the envelope stage's loan row states the grant of
        the loan taken in year 0 and the heat-pump stage's the one taken in year 4.
        """
        catalog = grant_and_soft_loan_catalog(self.GRANT_RATE, repayment_grant_rate=0.2)
        document = self._write(
            database, parameters, catalog, tmp_path / "soft_loan.json", perspective=self._soft_loan_perspective()
        )
        StagedDocument.assert_subsidies_reconciled(document)
        plan = document["plan"]
        loans = {
            row["stage"]: row
            for row in plan["subsidies"]
            if row["status"] == "awarded" and row["scheme"] == SyntheticPlan.SOFT_LOAN_SCHEME
        }
        assert set(loans) == {1, 2}
        for stage, year in ((1, 0), (2, 4)):
            assert loans[stage]["amount_in_euro"]["best"] < 0.0, "the repayment grant is support"
            assert [booking["year"] for booking in loans[stage]["amount_by_year_in_euro"]] == [year]
            assert loans[stage]["note"] == StagedDocument.REPAYMENT_GRANT_NOTE
        grants = {
            row["stage"]: row["amount_in_euro"]["best"]
            for row in plan["subsidies"]
            if row["status"] == "awarded" and row["scheme"] == SyntheticPlan.GRANT_SCHEME
        }
        stacks = {year["year"]: year["by_group"][CostGroup.SUBSIDIES.value]["best"] for year in plan["annual"]}
        assert stacks[0] == pytest.approx(grants[1] + loans[1]["amount_in_euro"]["best"], abs=0.01)
        assert stacks[4] == pytest.approx(grants[2] + loans[2]["amount_in_euro"]["best"], abs=0.01)

    def test_a_soft_loan_awarded_for_two_measures_is_stated_on_the_first_row(
        self, database, parameters, tmp_path
    ):
        """One stage, two measures, one loan: the first loan row states the grant, the second zero."""
        from hisim.economics.staged import Stage

        package = Stage(
            inputs=state_inputs(
                [
                    (
                        SyntheticPlan.ENVELOPE_SUBJECT,
                        ComponentType.WALL_EXTERNAL_INSULATION,
                        120.0,
                        SyntheticPlan.ENVELOPE_INVESTMENT_IN_EURO,
                    ),
                    (
                        SyntheticPlan.HEAT_PUMP_SUBJECT,
                        ComponentType.HEAT_PUMP,
                        9.0,
                        SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO,
                    ),
                ],
                SyntheticPlan.RENOVATED_ELECTRICITY_IN_KWH,
            ),
            from_year=0,
            label="package",
            measures=("external_insulation", "heating_system"),
        )
        catalog = grant_and_soft_loan_catalog(self.GRANT_RATE, repayment_grant_rate=0.2)
        document = self._write(
            database,
            parameters,
            catalog,
            tmp_path / "one_loan.json",
            stages=[baseline_stage(), package],
            perspective=self._soft_loan_perspective(),
        )
        loans = [
            row
            for row in document["plan"]["subsidies"]
            if row["status"] == "awarded" and row["scheme"] == SyntheticPlan.SOFT_LOAN_SCHEME
        ]
        assert len(loans) == 2
        first, second = loans
        assert first["amount_in_euro"]["best"] < 0.0
        assert first["note"] == StagedDocument.REPAYMENT_GRANT_NOTE
        assert second["amount_in_euro"] == {"min": 0.0, "best": 0.0, "max": 0.0}
        assert second["amount_by_year_in_euro"] == []
        assert second["note"] == StagedDocument.LOAN_STATED_ONCE_NOTE

    def test_loan_terms_that_book_no_grant_state_a_zero_and_say_why(self, database, parameters, tmp_path):
        """An awarded non-cash benefit is a zero band with a note, never the null of a question.

        Under a cash perspective the soft loan is still awarded — the solver values its repayment
        grant — but no loan is taken out, so nothing is booked for it.
        """
        catalog = grant_and_soft_loan_catalog(self.GRANT_RATE, repayment_grant_rate=0.2)
        document = self._write(database, parameters, catalog, tmp_path / "terms.json")
        loans = [
            row
            for row in document["plan"]["subsidies"]
            if row["status"] == "awarded" and row["scheme"] == SyntheticPlan.SOFT_LOAN_SCHEME
        ]
        assert loans
        for row in loans:
            assert row["amount_in_euro"] == {"min": 0.0, "best": 0.0, "max": 0.0}
            assert row["amount_by_year_in_euro"] == []
            assert row["note"] == StagedDocument.NON_CASH_NOTES[PayoutKind.LOAN_TERMS]

    def test_a_reduced_vat_award_states_a_zero_and_says_why(self):
        """The same rule for the other benefit that books no cash of its own."""
        from hisim.economics.subsidies import SubsidyAward, SubsidyDecision

        decision = SubsidyDecision(
            measure_subject=SyntheticPlan.HEAT_PUMP_SUBJECT,
            applied=[SubsidyAward(scheme_id="VAT", payout_kind=PayoutKind.VAT_REDUCTION, reduced_vat_rate=0.0)],
        )
        rows = StagedDocument._decision_rows(  # pylint: disable=protected-access
            decision, 1, "heating_system", {}, set()
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["status"] == "awarded"
        assert row["amount_in_euro"] == {"min": 0.0, "best": 0.0, "max": 0.0}
        assert row["note"] == StagedDocument.NON_CASH_NOTES[PayoutKind.VAT_REDUCTION]

    def test_a_stack_without_an_awarded_row_is_refused(self, document):
        """The no-catalogue contradiction itself, put back into a document, is refused."""
        from hisim.economics.staged_document import SubsidyReconciliationError

        broken = json.loads(json.dumps(document))
        broken["plan"]["annual"][4]["by_group"][CostGroup.SUBSIDIES.value] = {
            "min": -6015.0,
            "best": -6015.0,
            "max": -6015.0,
        }
        with pytest.raises(SubsidyReconciliationError, match="plan: in year 4 the Subsidies group"):
            StagedDocument.assert_subsidies_reconciled(broken)

    def test_an_event_from_a_scheme_nobody_awarded_is_refused(self, document):
        """A year marked with a grant the table does not know is refused, whatever its amount."""
        from hisim.economics.staged_document import SubsidyReconciliationError

        broken = json.loads(json.dumps(document))
        broken["reference"]["annual"][0]["events"].append({"kind": "subsidy", "scheme": "LEGACY_FLAT"})
        with pytest.raises(SubsidyReconciliationError, match="LEGACY_FLAT"):
            StagedDocument.assert_subsidies_reconciled(broken)


class TestTheEmissionsAreOnePhysicalFact:
    """Operational CO2 is the same series under every perspective of one plan.

    ``hisim/renovisor/kpis.py`` reads the lifecycle CO2 of whichever perspective it finds first
    when its preferred one is absent, which is only safe if the figure does not depend on the
    perspective. It does not, and this pins the reason: operational emissions are accumulated from
    the energy flows (``calculators/co2.py`` through ``accumulate_operational_emissions``) before
    any payer scoping or investment-context filtering happens, so a perspective can change who
    pays for a kilowatt-hour but not how much carbon burning it released.
    """

    def test_the_by_year_series_is_identical_across_perspectives(self, database, parameters):
        """Three perspectives that account for capital differently, one emissions series."""
        from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode

        stages = [baseline_stage(), envelope_stage(0), heat_pump_stage(4)]
        perspectives = [
            Perspective(
                id=name,
                installation_context=context,
                subsidy_mode=SubsidyMode.none(),
            )
            for name, context in (
                ("greenfield_gross", InstallationContext.GREENFIELD),
                ("brownfield_gross", InstallationContext.BROWNFIELD),
                ("operating", InstallationContext.OPERATING_ONLY),
            )
        ]
        series = [
            StagedEvaluator(database)
            .evaluate(stages, parameters, perspective)
            .plan.lifecycle_co2_result.operational_co2_by_year_in_kg
            for perspective in perspectives
        ]

        assert series[0] == series[1] == series[2]
        assert any(mass > 0 for mass in series[0]), "a house that burns nothing would prove nothing"


class TestTheFeedInRevenueReachesTheElectricityRow:
    """``plan.energy_year1``'s electricity row states the revenue for the kWh it says were sold.

    The engine books feed-in revenue under the ``ELECTRICITY_FEED_IN`` subject, not under
    ``ELECTRICITY``; the row used to gather only the carrier's own subject and so reported sold
    kilowatt hours earning nothing (renovisorissues #47). The plan here has the heat pump from
    year 0 feed :attr:`SyntheticPlan.SOLD_ELECTRICITY_IN_KWH` into the grid, so year 1 sells.
    """

    @pytest.fixture(name="selling", scope="class")
    def fixture_selling(self, tmp_path_factory):
        """The selling plan's staged result and its written document, read back from disk."""
        directory = tmp_path_factory.mktemp("selling_plan")
        database = write_database(str(directory / "database"))
        parameters = EconomicParameters(
            observation_period_in_years=SyntheticPlan.HORIZON,
            interest_rate=SyntheticPlan.INTEREST_RATE,
            country=SyntheticPlan.COUNTRY,
            price_basis_year=SyntheticPlan.YEAR,
            co2_price_scenario="none",
            apply_subsidies=False,
        )
        perspective = brownfield_perspective()
        stages = [
            baseline_stage(),
            heat_pump_stage(0, electricity_sold_in_kwh=SyntheticPlan.SOLD_ELECTRICITY_IN_KWH),
        ]
        result = StagedEvaluator(database).evaluate(stages, parameters, perspective)
        path = directory / StagedDocument.FILE_NAME
        StagedDocument(result, parameters, perspective).write(path)
        return result, json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _electricity_row(document: Dict[str, Any]) -> Dict[str, Any]:
        """The plan's year-1 electricity row."""
        rows = [row for row in document["plan"]["energy_year1"] if row["carrier"] == "ELECTRICITY"]
        assert len(rows) == 1
        row: Dict[str, Any] = rows[0]
        return row

    def test_the_row_sells_what_the_stage_fed_in(self, selling):
        """The precondition: the row reports the sold kilowatt hours at all."""
        _, document = selling
        assert self._electricity_row(document)["sold_in_kwh"] == pytest.approx(
            SyntheticPlan.SOLD_ELECTRICITY_IN_KWH
        )

    def test_the_revenue_is_the_sold_kwh_at_the_feed_in_rate_and_negative(self, selling):
        """Best is minus sold times the best rate; min and max come from the rate band, mirrored."""
        _, document = selling
        revenue = self._electricity_row(document)["revenue_in_euro"]
        sold = SyntheticPlan.SOLD_ELECTRICITY_IN_KWH
        low_rate, best_rate, high_rate = SyntheticPlan.FEED_IN_RATE_IN_EURO_PER_KWH

        assert revenue["best"] == pytest.approx(-sold * best_rate)
        assert revenue["min"] == pytest.approx(-sold * high_rate)
        assert revenue["max"] == pytest.approx(-sold * low_rate)
        assert revenue["min"] <= revenue["best"] <= revenue["max"] < 0

    def test_the_revenue_equals_the_year_one_timeline_entries(self, selling):
        """The row states exactly what the timeline booked as year-1 feed-in revenue."""
        result, document = selling
        booked = [
            entry.amount_in_euro
            for entry in result.plan.scoped_timeline().entries
            if entry.year == 1 and entry.category == CostCategory.FEED_IN_REVENUE
        ]
        assert booked, "the plan must book feed-in revenue, or the row has nothing to agree with"
        assert {
            entry.subject
            for entry in result.plan.scoped_timeline().entries
            if entry.year == 1 and entry.category == CostCategory.FEED_IN_REVENUE
        } == {revenue_subject("ELECTRICITY")}
        revenue = self._electricity_row(document)["revenue_in_euro"]

        assert revenue["best"] == pytest.approx(sum(amount.best_estimate for amount in booked))
        assert revenue["min"] == pytest.approx(sum(amount.minimum for amount in booked))
        assert revenue["max"] == pytest.approx(sum(amount.maximum for amount in booked))

    def test_the_revenue_agrees_with_the_year_one_bill_view(self, selling):
        """The document and `views.carrier_year_one_bills` read the same subjects of one bill."""
        result, document = selling
        bill = carrier_year_one_bills(result.plan)["ELECTRICITY"]
        row = self._electricity_row(document)

        assert row["revenue_in_euro"]["best"] == pytest.approx(
            bill.by_category_in_euro[CostCategory.FEED_IN_REVENUE]
        )
        assert row["cost_in_euro"]["best"] == pytest.approx(bill.total_excluding_feed_in_in_euro)

    def test_the_revenue_does_not_leak_into_the_cost_or_the_price(self, selling):
        """The cost and the effective price are the purchase alone: a credit is not a kWh's price."""
        _, document = selling
        row = self._electricity_row(document)
        bought_cost = SyntheticPlan.RENOVATED_ELECTRICITY_IN_KWH * SyntheticPlan.ELECTRICITY_PRICE_IN_EURO_PER_KWH

        assert row["cost_in_euro"]["best"] == pytest.approx(bought_cost)
        assert row["effective_price_in_euro_per_kwh"]["best"] == pytest.approx(
            SyntheticPlan.ELECTRICITY_PRICE_IN_EURO_PER_KWH
        )

    def test_the_selling_document_validates(self, selling):
        """A row with revenue still matches the shipped schema."""
        _, document = selling
        StagedDocument.validate(document)
