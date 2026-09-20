"""``python -m hisim.economics staged``: the exit contract a backend branches on.

The subcommand is called by a backend that has just finished a handful of simulation jobs and
wants one document out of them. What it therefore has to get right, and what these cases pin, is
less the arithmetic — ``test_staged.py`` owns that — than the contract around it: exit 0 with a
document that validates, exit 2 with a ``problems.json`` for a plan the caller can fix, exit 3
for an engine failure they cannot, and the four-field ``--stage`` argument the backend builds.

The stages are written to disk as real job directories (``economic_inputs.json`` beside an
optional ``mapping_report.json``), because reading those directories is half of what the
subcommand does.
"""

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from hisim.economics.__main__ import StagedCli, main
from hisim.economics.parameters import EconomicParameters
from hisim.economics.serialization import write_inputs
from hisim.economics.staged import StagedEvaluationError
from hisim.economics.staged_document import StagedDocument

from tests.economics.synthetic_stages import (
    SyntheticPlan,
    baseline_stage,
    envelope_stage,
    heat_pump_stage,
    write_database,
)

pytestmark = pytest.mark.base


@pytest.fixture(name="workspace")
def fixture_workspace(tmp_path) -> Path:
    """A directory holding the synthetic database, three job directories and a parameters file.

    The three jobs are the plan of ``synthetic_stages``: the house as it is, an envelope measure
    and a heat pump. Each is written exactly as a finished RenoVisor job leaves it, so the
    subcommand reads them the way it reads a real one.
    """
    database_directory = tmp_path / "cost_database"
    write_database(str(database_directory))
    for name, stage in (
        ("base", baseline_stage()),
        ("envelope", envelope_stage(0)),
        ("heat_pump", heat_pump_stage(4)),
    ):
        directory = tmp_path / name
        directory.mkdir()
        write_inputs(stage.inputs, str(directory))
    # Every finished job writes a mapping report; the baseline's says that nothing in it came
    # from a measure and that nothing in it is unpriced, which is a statement and not an absence.
    (tmp_path / "base" / "mapping_report.json").write_text(
        json.dumps({"subjects": {}, "unpriced_subjects": []}),
        encoding="utf-8",
    )
    (tmp_path / "envelope" / "mapping_report.json").write_text(
        json.dumps(
            {
                "subjects": {SyntheticPlan.ENVELOPE_SUBJECT: "external_insulation"},
                "unpriced_subjects": [SyntheticPlan.ENVELOPE_SUBJECT],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "heat_pump" / "mapping_report.json").write_text(
        json.dumps({"subjects": {SyntheticPlan.HEAT_PUMP_SUBJECT: "heating_system"}}),
        encoding="utf-8",
    )
    parameters = EconomicParameters(
        observation_period_in_years=SyntheticPlan.HORIZON,
        interest_rate=SyntheticPlan.INTEREST_RATE,
        country=SyntheticPlan.COUNTRY,
        price_basis_year=SyntheticPlan.YEAR,
        co2_price_scenario="none",
        apply_subsidies=False,
        cost_database_path=str(database_directory),
    )
    (tmp_path / "parameters.json").write_text(
        json.dumps({key: value for key, value in parameters.to_dict().items() if not isinstance(value, dict)}),
        encoding="utf-8",
    )
    workspace: Path = tmp_path
    return workspace


def _arguments(workspace: Path, out: Path) -> List[str]:
    """The ordinary three-stage invocation, as the backend would build it."""
    return [
        "staged",
        "--stage",
        f"{workspace / 'base'}:0:baseline",
        "--stage",
        f"{workspace / 'envelope'}:0:stage 1:job-envelope",
        "--stage",
        f"{workspace / 'heat_pump'}:4:stage 2:job-heat-pump",
        "--parameters",
        str(workspace / "parameters.json"),
        "--perspective",
        "brownfield_gross",
        "--out",
        str(out),
    ]


class TestTheStageArgument:
    """The ``<directory>:<from_year>:<label>[:<job_id>]`` form, parsed once and stated once."""

    def test_three_fields_leave_the_job_id_unset(self):
        """A caller who has no job id says nothing, and the document carries a null."""
        assert StagedCli.parse_stage("jobs/base:0:baseline", 0) == ("jobs/base", 0, "baseline", None)

    def test_four_fields_carry_the_job_id(self):
        """The backend's fourth field is what traces a stage back to the run behind it."""
        assert StagedCli.parse_stage("jobs/pkg:3:stage 2:job-7", 1) == ("jobs/pkg", 3, "stage 2", "job-7")

    def test_too_few_fields_are_refused_by_name(self):
        """A mistyped plan names the argument and the form it should have had."""
        with pytest.raises(StagedEvaluationError, match="colon-separated fields"):
            StagedCli.parse_stage("jobs/base:0", 0)

    def test_a_year_that_is_not_a_year_is_refused(self):
        """The second field is a horizon year, and a word there is the caller's mistake."""
        with pytest.raises(StagedEvaluationError, match="is not a year"):
            StagedCli.parse_stage("jobs/base:soon:baseline", 0)


class TestTheHappyPath:
    """Exit 0, a validated document, and the two things the mapping report contributes to it."""

    def test_it_writes_a_valid_document(self, workspace, capsys):
        """The whole subcommand end to end on three written job directories."""
        out = workspace / "economics_result.json"
        assert main(_arguments(workspace, out)) == 0
        document = json.loads(out.read_text(encoding="utf-8"))
        StagedDocument.validate(document)
        assert [stage["label"] for stage in document["stages"]] == ["baseline", "stage 1", "stage 2"]
        assert document["stages"][2]["job_id"] == "job-heat-pump"
        assert "economics_result.json" in capsys.readouterr().out

    def test_the_mapping_reports_stamp_the_measure_ids(self, workspace):
        """``subjects`` from every stage directory, later stages winning over earlier ones."""
        out = workspace / "economics_result.json"
        assert main(_arguments(workspace, out)) == 0
        rows = {
            row["subject"]: row
            for row in json.loads(out.read_text(encoding="utf-8"))["plan"]["by_subject"]
        }
        assert rows[SyntheticPlan.HEAT_PUMP_SUBJECT]["measure_id"] == "heating_system"
        assert rows[SyntheticPlan.ENVELOPE_SUBJECT]["measure_id"] == "external_insulation"

    def test_an_unpriced_subject_from_the_mapping_is_flagged(self, workspace):
        """The translator's ``unpriced_subjects`` reaches the document rather than being lost."""
        out = workspace / "economics_result.json"
        assert main(_arguments(workspace, out)) == 0
        rows = {
            row["subject"]: row
            for row in json.loads(out.read_text(encoding="utf-8"))["plan"]["by_subject"]
        }
        assert rows[SyntheticPlan.ENVELOPE_SUBJECT]["unpriced"] is True

    def test_a_single_stage_plan_is_a_legal_plan(self, workspace):
        """One stage is the degenerate plan, and the document still has a reference and a plan."""
        out = workspace / "single.json"
        assert (
            main(
                [
                    "staged",
                    "--stage",
                    f"{workspace / 'base'}:0:baseline",
                    "--parameters",
                    str(workspace / "parameters.json"),
                    "--perspective",
                    "brownfield_gross",
                    "--out",
                    str(out),
                ]
            )
            == 0
        )
        document = json.loads(out.read_text(encoding="utf-8"))
        assert document["comparison"]["npv_delta_in_euro"]["best"] == pytest.approx(0.0, abs=0.01)


class TestTheRefusals:
    """Exit 2 with a ``problems.json``: everything the caller can fix by sending another plan."""

    @staticmethod
    def _problems(out: Path) -> Dict[str, Any]:
        """The problems document the refusal wrote beside the requested output."""
        document: Dict[str, Any] = json.loads(
            (out.parent / StagedCli.PROBLEMS_FILE_NAME).read_text(encoding="utf-8")
        )
        return document

    def test_a_missing_input_file(self, workspace):
        """A stage naming a job that has not finished is a plan problem, not an engine one."""
        out = workspace / "economics_result.json"
        code = main(
            [
                "staged",
                "--stage",
                f"{workspace / 'nowhere'}:0:baseline",
                "--parameters",
                str(workspace / "parameters.json"),
                "--out",
                str(out),
            ]
        )
        assert code == StagedCli.PLAN_REFUSED
        assert not out.exists()
        assert "economic_inputs.json" in self._problems(out)["problems"][0]["message"]

    def test_years_that_run_backwards(self, workspace):
        """The evaluator's own refusal reaches the caller as exit 2 and a problems file."""
        out = workspace / "economics_result.json"
        code = main(
            [
                "staged",
                "--stage",
                f"{workspace / 'base'}:0:baseline",
                "--stage",
                f"{workspace / 'envelope'}:5:stage 1",
                "--stage",
                f"{workspace / 'heat_pump'}:2:stage 2",
                "--parameters",
                str(workspace / "parameters.json"),
                "--perspective",
                "brownfield_gross",
                "--out",
                str(out),
            ]
        )
        assert code == StagedCli.PLAN_REFUSED
        assert "must not run backwards" in self._problems(out)["problems"][0]["message"]

    def test_an_unknown_perspective(self, workspace):
        """A perspective id the bundle has no row for is named, with the ids it does have."""
        out = workspace / "economics_result.json"
        code = main(
            [
                "staged",
                "--stage",
                f"{workspace / 'base'}:0:baseline",
                "--parameters",
                str(workspace / "parameters.json"),
                "--perspective",
                "brownfield_owner_subsidized_cash",
                "--out",
                str(out),
            ]
        )
        assert code == StagedCli.PLAN_REFUSED
        message = self._problems(out)["problems"][0]["message"]
        assert "unknown perspective" in message and "brownfield_net" in message

    def test_a_country_with_no_price_data(self, workspace):
        """A plan for a country the database cannot price is refused by name, not defaulted."""
        elsewhere = workspace / "elsewhere.json"
        elsewhere.write_text(
            json.dumps(
                {
                    "observation_period_in_years": SyntheticPlan.HORIZON,
                    "country": "ZZ",
                    "price_basis_year": SyntheticPlan.YEAR,
                    "co2_price_scenario": "none",
                    "cost_database_path": str(workspace / "cost_database"),
                }
            ),
            encoding="utf-8",
        )
        out = workspace / "economics_result.json"
        code = main(
            [
                "staged",
                "--stage",
                f"{workspace / 'base'}:0:baseline",
                "--parameters",
                str(elsewhere),
                "--perspective",
                "brownfield_gross",
                "--out",
                str(out),
            ]
        )
        assert code == StagedCli.PLAN_REFUSED
        assert "no energy price data" in self._problems(out)["problems"][0]["message"]

    def test_no_parameters_anywhere(self, workspace):
        """Pricing with the engine defaults would answer a different question, so it is refused."""
        out = workspace / "economics_result.json"
        code = main(
            ["staged", "--stage", f"{workspace / 'base'}:0:baseline", "--out", str(out)]
        )
        assert code == StagedCli.PLAN_REFUSED
        assert "--parameters" in self._problems(out)["problems"][0]["message"]


class TestTheMappingReportIsRequired:
    """A stage directory with stored inputs but no mapping report is a plan problem (step 12 §3.5).

    The report is the only place that says which cost subjects the request carried no price for.
    An unpriced subject reaches the engine with an investment of zero, so without the report every
    row of the document claims a known price and the plan's total silently understates a measure
    of unknown cost — with exit 0 and nothing anywhere to say so. Refusing is the same choice the
    evaluator makes for every other unanswerable plan: exit 2 and a ``problems.json`` naming the
    directory.
    """

    def test_a_stage_without_a_report_is_refused_by_name(self, workspace: Path) -> None:
        """The refusal names the directory, the file it looked for and why it matters."""
        (workspace / "envelope" / StagedCli.MAPPING_REPORT_FILE_NAME).unlink()
        out = workspace / "economics_result.json"

        assert (
            main(
                [
                    "staged",
                    "--stage",
                    f"{workspace / 'base'}:0:baseline",
                    "--stage",
                    f"{workspace / 'envelope'}:0:stage 1",
                    "--parameters",
                    str(workspace / "parameters.json"),
                    "--out",
                    str(out),
                ]
            )
            == StagedCli.PLAN_REFUSED
        )
        assert not out.exists(), "a refused plan writes no document"
        problems = json.loads((workspace / "problems.json").read_text(encoding="utf-8"))
        message = problems["problems"][0]["message"]
        assert str(workspace / "envelope") in message
        assert StagedCli.MAPPING_REPORT_FILE_NAME in message

    def test_the_report_may_stand_beside_the_results_directory(self, workspace: Path) -> None:
        """A caller naming ``<job>/results`` outright still finds the job's own report."""
        results = workspace / "envelope" / "results"
        results.mkdir()
        (results / "economic_inputs.json").write_bytes(
            (workspace / "envelope" / "economic_inputs.json").read_bytes()
        )

        assert StagedCli.mapping_report_path(str(results)) == str(
            workspace / "envelope" / StagedCli.MAPPING_REPORT_FILE_NAME
        )

    def test_the_reader_takes_the_field_names_from_the_writer(self) -> None:
        """Two processes, one spelling: a rename in the report drops no stamp silently."""
        from hisim.renovisor.report import MappingReport

        assert StagedCli.SUBJECTS_KEY == MappingReport.SUBJECTS_FIELD
        assert StagedCli.UNPRICED_KEY == MappingReport.UNPRICED_SUBJECTS_FIELD
        written = MappingReport().to_json()
        assert StagedCli.SUBJECTS_KEY in written
        assert StagedCli.UNPRICED_KEY in written


class TestTheCatalogueIsNamedInTheDocument:
    """``parameters.subsidy_catalog`` identifies a catalogue, not just a country (step 12 §4)."""

    def test_a_priced_plan_names_the_country_and_the_snapshot_date(self) -> None:
        """Ireland's schemes change every few months; a stored document says which it priced.

        The field used to be the bare country code, which is enough to switch the subsidies view
        on and not enough to reload the catalogue the figures came from.
        """
        from hisim.economics.subsidies import SubsidyCatalog

        # The shipped directory by its absolute path: CI runs pytest from `tests/`, so a path
        # relative to the working directory would name a directory that is not there.
        catalog = SubsidyCatalog.load("IE", SubsidyCatalog.DEFAULT_PATH)

        assert catalog.snapshot_date is not None
        assert StagedCli.catalog_id(catalog, "IE") == f"IE@{catalog.snapshot_date}"

    def test_a_plan_priced_without_a_catalogue_names_none(self) -> None:
        """No catalogue is a different statement from an undated one, and stays ``null``."""
        assert StagedCli.catalog_id(None, "IE") is None
