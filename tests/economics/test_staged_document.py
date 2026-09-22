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
    always_eligible_catalog,
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
                "monthly_equivalent_cost_delta_in_euro" if path == ("comparison",) else "monthly_equivalent_cost_in_euro"
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
    awarded: V1 drew a credit that V8 said did not exist. Without a catalogue a plan is now priced
    with ``subsidy_mode: NONE``, and :meth:`StagedDocument.write` refuses any document whose two
    statements disagree.
    """

    #: Share of the eligible cost the synthetic grant pays.
    GRANT_RATE = 0.3

    @staticmethod
    def _stages():
        """The three-stage synthetic plan: baseline, envelope in year 0, heat pump in year 4."""
        return [baseline_stage(), envelope_stage(0), heat_pump_stage(4)]

    @pytest.fixture(name="shim_database", scope="class")
    def fixture_shim_database(self, tmp_path_factory):
        """The synthetic database plus a heat-pump entry carrying a 25 % legacy flat share."""
        return write_database(
            str(tmp_path_factory.mktemp("shim_cost_database")), heat_pump_legacy_flat_subsidy_share=0.25
        )

    def _write(self, database, parameters, catalog, path, catalog_id=None) -> Dict[str, Any]:
        """Price the synthetic plan with subsidies on and write its document."""
        perspective = brownfield_perspective(subsidies=True)
        result = StagedEvaluator(database).evaluate(self._stages(), parameters, perspective, catalog)
        written: Dict[str, Any] = StagedDocument(
            result, parameters, perspective, subsidy_catalog_id=catalog_id
        ).write(path)
        return written

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
            database,
            parameters,
            always_eligible_catalog(self.GRANT_RATE),
            tmp_path / "catalogue.json",
            catalog_id=f"{SyntheticPlan.COUNTRY}@synthetic",
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
        assert all(
            amount == 0.0 for year, amount in subsidies_by_year.items() if year not in (0, 4)
        ), "support booked outside the two stage years"

    def test_a_stack_without_an_awarded_row_is_refused(self, document):
        """The no-catalogue contradiction itself, put back into a document, is refused."""
        from hisim.economics.staged_document import SubsidyReconciliationError

        broken = json.loads(json.dumps(document))
        broken["plan"]["annual"][4]["by_group"][CostGroup.SUBSIDIES.value] = {
            "min": -6015.0,
            "best": -6015.0,
            "max": -6015.0,
        }
        with pytest.raises(SubsidyReconciliationError, match="plan: the Subsidies group"):
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
