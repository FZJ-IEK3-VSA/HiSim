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
from typing import Any, Dict, List, Optional

import pytest

from hisim.economics.__main__ import StagedCli, main
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import effective_price_basis_year
from hisim.economics.exports import ExportFileNames
from hisim.economics.parameters import EconomicParameters
from hisim.economics.serialization import SerializationFileNames, write_inputs
from hisim.economics.staged import StagedEvaluationError
from hisim.economics.staged_document import StagedDocument
from hisim.economics.staged_parameters import ParameterKeys

from tests.economics.synthetic_stages import (
    SyntheticPlan,
    baseline_stage,
    envelope_stage,
    heat_pump_stage,
    write_database,
)

pytestmark = pytest.mark.base


#: The country the fixture's stages were priced under. Ireland rather than the synthetic ``XX``
#: on purpose: the defect this file pins is a staged run that took the engine's ``"DE"`` default
#: instead of the country its stages were priced for (shared todo H19), and DE is the country the
#: fixture's database has no prices for.
STAGE_COUNTRY = "IE"


def _stored_parameters(country: str, database: Path) -> EconomicParameters:
    """The assumptions a finished job of the synthetic plan was priced under.

    Args:
        country: The country the job was priced for.
        database: The synthetic cost database directory.

    Returns:
        The record, with the synthetic plan's horizon, interest and price basis year.
    """
    return EconomicParameters(
        observation_period_in_years=SyntheticPlan.HORIZON,
        interest_rate=SyntheticPlan.INTEREST_RATE,
        country=country,
        price_basis_year=SyntheticPlan.YEAR,
        co2_price_scenario="none",
        apply_subsidies=False,
        cost_database_path=str(database),
    )


def _write_stored_parameters(directory: Path, parameters: EconomicParameters) -> None:
    """Write the ``lifecycle_costs.json`` a finished job leaves its assumptions in.

    ``staged`` reads the plan's country, price basis year and data paths back out of this file
    (``serialization.read_stored_parameters``), so a stage directory without one is a stage that
    states nothing about what it was priced under.

    Args:
        directory: The job directory.
        parameters: The assumptions the job was priced under.
    """
    (directory / ExportFileNames.LIFECYCLE_COSTS_FILE_NAME).write_text(
        json.dumps({"brownfield_gross": {"parameters": parameters.to_dict()}}),
        encoding="utf-8",
    )


@pytest.fixture(name="workspace")
def fixture_workspace(tmp_path) -> Path:
    """A directory holding the synthetic database, three job directories and a parameters file.

    The three jobs are the plan of ``synthetic_stages``: the house as it is, an envelope measure
    and a heat pump. Each is written exactly as a finished RenoVisor job leaves it — the stored
    inputs, the mapping report and the ``lifecycle_costs.json`` stating what it was priced under
    — so the subcommand reads them the way it reads a real one.

    The parameters file is in the shape the document publishes and the backend sends: the two
    assumptions a caller may change, and no country, because the country is the stages'.
    """
    database_directory = tmp_path / "cost_database"
    write_database(str(database_directory))
    # The synthetic database prices one carrier for the synthetic country; the same entry under
    # the stages' country is what lets the plan be priced as an Irish one without a shipped price
    # reaching the test.
    (database_directory / f"energy_prices_{STAGE_COUNTRY}.json").write_bytes(
        (database_directory / f"energy_prices_{SyntheticPlan.COUNTRY}.json").read_bytes()
    )
    for name, stage in (
        ("base", baseline_stage()),
        ("envelope", envelope_stage(0)),
        ("heat_pump", heat_pump_stage(4)),
    ):
        directory = tmp_path / name
        directory.mkdir()
        write_inputs(stage.inputs, str(directory))
        _write_stored_parameters(directory, _stored_parameters(STAGE_COUNTRY, database_directory))
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
    (tmp_path / "parameters.json").write_text(
        json.dumps(
            {
                ParameterKeys.HORIZON_YEARS: SyntheticPlan.HORIZON,
                ParameterKeys.INTEREST_RATE: SyntheticPlan.INTEREST_RATE,
            }
        ),
        encoding="utf-8",
    )
    workspace: Path = tmp_path
    return workspace


def _the_year_the_shipped_data_prices_at() -> int:
    """The basis year the engine resolves for the stages' country against the shipped database.

    A backend stage directory carries no cost-database path, so a plan assembled out of such
    directories is priced against the database the image ships — and it has to name a basis year
    that database covers. Reading the year off the database, through the very function the engine
    resolves it with, is what keeps this case honest when the shipped data gains or loses a year;
    writing a number here would be inventing one.

    Returns:
        The resolved basis year for :data:`STAGE_COUNTRY` in the shipped cost database.
    """
    return effective_price_basis_year(
        EconomicParameters(country=STAGE_COUNTRY), CostDatabase(), SyntheticPlan.YEAR
    )


def _as_a_backend_stage(directory: Path, country: Optional[str], price_basis_year: Optional[int]) -> None:
    """Leave in one stage directory only what a backend's worker ships into it.

    ``economics-backend-spec.md`` §3: the worker writes each stage job's ``economic_inputs.json``
    and its ``mapping_report.json`` into ``<JobDir>/stages/<index>/`` and nothing else — no
    ``lifecycle_costs.json``, so no stored evaluation and no stored parameter record. What such a
    stage still states is the two facts ``write_inputs`` puts into the extract: the country it
    was priced for and the resolved price basis year it priced at.

    Args:
        directory: The job directory to rewrite in place.
        country: The country to write into the stored inputs, or None to remove the key — an
            extract written by an engine from before the key existed.
        price_basis_year: The basis year to write, or None to remove that key, for the same
            reason.
    """
    (directory / ExportFileNames.LIFECYCLE_COSTS_FILE_NAME).unlink()
    path = directory / StagedCli.INPUTS_FILE_NAME
    raw = json.loads(path.read_text(encoding="utf-8"))
    for key, value in (
        (SerializationFileNames.COUNTRY_KEY, country),
        (SerializationFileNames.PRICE_BASIS_YEAR_KEY, price_basis_year),
    ):
        if value is None:
            raw.pop(key, None)
        else:
            raw[key] = value
    path.write_text(json.dumps(raw), encoding="utf-8")


def _forget_what_the_stages_were_priced_under(workspace: Path) -> None:
    """Remove every stage's stored evaluation, leaving jobs that state no country.

    The state a backend's worker produces today: it ships each stage's ``economic_inputs.json``
    and its mapping report, not its ``lifecycle_costs.json``. A plan out of such stages has to be
    told its country, and is refused when it is not.

    Args:
        workspace: The fixture directory.
    """
    for name in ("base", "envelope", "heat_pump"):
        (workspace / name / ExportFileNames.LIFECYCLE_COSTS_FILE_NAME).unlink()


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
        _forget_what_the_stages_were_priced_under(workspace)
        elsewhere = workspace / "elsewhere.json"
        elsewhere.write_text(
            json.dumps(
                {ParameterKeys.COUNTRY: "ZZ", ParameterKeys.PRICE_BASIS_YEAR: SyntheticPlan.YEAR}
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

    def test_no_country_anywhere(self, workspace):
        """Stages that state no country and a file that states none: refused, never a default.

        The trap of shared todo H19 in its purest form. The engine record's ``country`` field
        defaults to ``"DE"``, so a plan out of stages that say nothing used to be priced with
        German data — correctly-shaped, plausible and simply the wrong country.
        """
        _forget_what_the_stages_were_priced_under(workspace)
        out = workspace / "economics_result.json"
        code = main(["staged", "--stage", f"{workspace / 'base'}:0:baseline", "--out", str(out)])
        assert code == StagedCli.PLAN_REFUSED
        assert not out.exists()
        problems = self._problems(out)["problems"]
        assert {problem["code"] for problem in problems} == {
            "parameters.country.missing",
            "parameters.price_basis_year.missing",
        }
        assert all("--parameters" in problem["message"] for problem in problems)

    def test_a_parameters_file_that_is_not_there(self, workspace):
        """A missing file is a refusal with a problems document, never a bare exit 2 (B29)."""
        out = workspace / "economics_result.json"
        code = main(
            [
                "staged",
                "--stage",
                f"{workspace / 'base'}:0:baseline",
                "--parameters",
                str(workspace / "nowhere.json"),
                "--out",
                str(out),
            ]
        )
        assert code == StagedCli.PLAN_REFUSED
        assert self._problems(out)["problems"][0]["code"] == "parameters.unreadable"

    def test_a_parameters_file_that_is_not_json(self, workspace):
        """Neither is a file that will not parse: the caller mistyped, the engine is fine."""
        broken = workspace / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        out = workspace / "economics_result.json"
        code = main(
            [
                "staged",
                "--stage",
                f"{workspace / 'base'}:0:baseline",
                "--parameters",
                str(broken),
                "--out",
                str(out),
            ]
        )
        assert code == StagedCli.PLAN_REFUSED
        assert self._problems(out)["problems"][0]["code"] == "parameters.unreadable"


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


class TestTheStageMeasuresComeFromTheReport:
    """``stages[].measures`` is read from each stage's mapping report (hisim-cyc.4).

    The end-to-end test on the mockup pair cannot show the filter at work, because every measure
    of that package is ``used`` or ``approximated`` at measure level -- a value-level substitution
    such as the low-temperature radiator never changes its measure's status. So the rules are
    pinned here on a hand-written report: acted-on statuses only, catalogue order regardless of
    package order, an id the catalogue does not know sorted last, an absent report yielding
    nothing (the mapping read refuses it later, by name), and a report that does not parse
    refused with the stage's position.
    """

    @staticmethod
    def write_report(directory: Path, measures: List[Dict[str, Any]]) -> None:
        """Write a mapping report carrying only the measure half under the writer's key."""
        from hisim.renovisor.report import MappingReport

        (directory / StagedCli.MAPPING_REPORT_FILE_NAME).write_text(
            json.dumps({MappingReport.MEASURES_FIELD: measures}), encoding="utf-8"
        )

    def test_only_acted_on_measures_are_listed_in_catalogue_order(self, tmp_path: Path) -> None:
        """A not-implemented measure changed nothing, so the timeline must not name it."""
        from hisim.renovisor.request import CatalogueTable

        order = list(CatalogueTable.ids())
        self.write_report(
            tmp_path,
            [
                {"id": "photovoltaic_system", "status": "used"},
                {"id": "heating_system", "status": "approximated"},
                {"id": "ventilation_system", "status": "not_implemented_yet"},
                {"id": "external_insulation", "status": "used"},
            ],
        )

        listed = StagedCli.read_stage_measures(str(tmp_path), 1, "stage 1")

        assert set(listed) == {"photovoltaic_system", "heating_system", "external_insulation"}
        assert list(listed) == sorted(listed, key=order.index), "catalogue order, not package order"
        assert order.index("heating_system") < order.index("photovoltaic_system")

    def test_an_unknown_id_sorts_after_the_catalogue(self, tmp_path: Path) -> None:
        """A report from another catalogue revision is kept, behind everything the table knows."""
        self.write_report(
            tmp_path,
            [
                {"id": "zzz_not_in_the_catalogue", "status": "used"},
                {"id": "aaa_not_in_the_catalogue", "status": "used"},
                {"id": "photovoltaic_system", "status": "used"},
            ],
        )

        assert StagedCli.read_stage_measures(str(tmp_path), 1, "stage 1") == (
            "photovoltaic_system",
            "aaa_not_in_the_catalogue",
            "zzz_not_in_the_catalogue",
        )

    def test_a_directory_without_a_report_yields_nothing_here(self, tmp_path: Path) -> None:
        """The refusal lives in the mapping read; this reader only has to tolerate the absence."""
        listed = StagedCli.read_stage_measures(str(tmp_path), 0, "baseline")

        assert isinstance(listed, tuple)
        assert not listed

    def test_a_report_that_does_not_parse_is_refused_with_the_stage_named(self, tmp_path: Path) -> None:
        """Broken JSON is a plan problem naming the stage, not a stack trace."""
        (tmp_path / StagedCli.MAPPING_REPORT_FILE_NAME).write_text("{not json", encoding="utf-8")

        with pytest.raises(StagedEvaluationError) as caught:
            StagedCli.read_stage_measures(str(tmp_path), 2, "stage 2")

        message = str(caught.value)
        assert "--stage #2" in message
        assert "stage 2" in message
        assert StagedCli.MAPPING_REPORT_FILE_NAME in message

    def test_the_reader_takes_the_measures_key_from_the_writer(self) -> None:
        """The same guard the subjects keys have: one spelling for both processes."""
        from hisim.renovisor.report import MappingReport

        assert StagedCli.MEASURES_KEY == MappingReport.MEASURES_FIELD
        assert StagedCli.MEASURES_KEY in MappingReport().to_json()


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


class TestTheParameterBlockTheBackendSends:
    """``--parameters`` is the document's own ``parameters`` block (step 13 §1).

    The subcommand used to read the file as an :class:`EconomicParameters` record, whose field
    names are a different vocabulary from the one the document publishes and the RenoVisor
    contract promises. Of the five keys in ``economics-backend-spec.md`` §2.1's example, exactly
    one — ``interest_rate`` — was accepted; the other four were refused by name (shared todo C5).
    """

    #: The block of `economics-backend-spec.md` §2.1, with the perspective id the shipped bundle
    #: has. It is quoted rather than built so that a change to the contract's example breaks this
    #: case instead of passing quietly.
    BACKEND_EXAMPLE: Dict[str, Any] = {
        "horizon_years": 20,
        "interest_rate": 0.03,
        "perspective_id": "brownfield_net",
        "financing": {"kind": "cash"},
        "subsidy_mode": "full",
    }

    @staticmethod
    def _run(workspace: Path, block: Dict[str, Any], name: str) -> Dict[str, Any]:
        """Price the three-stage plan under one parameter block and read the document back."""
        parameters_path = workspace / f"{name}.parameters.json"
        parameters_path.write_text(json.dumps(block), encoding="utf-8")
        out = workspace / f"{name}.json"
        code = main(
            [
                "staged",
                "--stage",
                f"{workspace / 'base'}:0:baseline",
                "--stage",
                f"{workspace / 'envelope'}:0:stage 1",
                "--stage",
                f"{workspace / 'heat_pump'}:4:stage 2",
                "--parameters",
                str(parameters_path),
                "--out",
                str(out),
            ]
        )
        assert code == 0, (out.parent / StagedCli.PROBLEMS_FILE_NAME).read_text(encoding="utf-8")
        document: Dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
        return document

    def test_the_documented_example_is_accepted_and_priced(self, workspace):
        """Every key of the contract's example reaches the engine as what it means."""
        document = self._run(workspace, self.BACKEND_EXAMPLE, "backend")
        block = document["parameters"]
        assert block["horizon_years"] == 20
        assert block["interest_rate"] == 0.03
        assert block["perspective_id"] == "brownfield_net"
        assert block["financing"] == {"kind": "cash"}
        assert block["subsidy_mode"] == "full"

    def test_the_old_engine_vocabulary_is_no_longer_accepted(self, workspace):
        """Two vocabularies was the defect, so the engine's own field names are refused now."""
        out = workspace / "engine_shape.json"
        engine_shape = workspace / "engine_shape.parameters.json"
        engine_shape.write_text(json.dumps({"observation_period_in_years": 20}), encoding="utf-8")
        code = main(
            [
                "staged",
                "--stage",
                f"{workspace / 'base'}:0:baseline",
                "--parameters",
                str(engine_shape),
                "--out",
                str(out),
            ]
        )
        assert code == StagedCli.PLAN_REFUSED
        problem = json.loads(
            (out.parent / StagedCli.PROBLEMS_FILE_NAME).read_text(encoding="utf-8")
        )["problems"][0]
        assert problem["code"] == "parameters.unknown_key"
        assert ParameterKeys.HORIZON_YEARS in problem["accepted"]

    def test_the_documents_own_block_is_a_legal_input_file(self, workspace):
        """A reader can feed a document's assumptions back in and get the same run."""
        first = self._run(workspace, self.BACKEND_EXAMPLE, "first")
        second = self._run(workspace, first["parameters"], "second")
        assert second["parameters"] == first["parameters"]


class TestTheCountryComesFromTheStages:
    """No default country anywhere in the staged path (step 13 §1.2, shared todo H19)."""

    def test_a_file_without_a_country_prices_the_stages_country(self, workspace):
        """The Irish stages are priced as Irish, and no German lookup happens.

        The fixture's database holds prices for the stages' country and for the synthetic one,
        and for no other. A run that fell back to the engine's ``"DE"`` default would therefore
        be refused for want of German prices rather than reach a document at all.
        """
        out = workspace / "economics_result.json"
        assert (
            main(
                [
                    "staged",
                    "--stage",
                    f"{workspace / 'base'}:0:baseline",
                    "--stage",
                    f"{workspace / 'heat_pump'}:4:stage 2",
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
        assert json.loads(out.read_text(encoding="utf-8"))["parameters"]["country"] == STAGE_COUNTRY

    def test_a_file_naming_another_country_is_refused(self, workspace):
        """A file may repeat the stages' country; it may not change it."""
        elsewhere = workspace / "german.json"
        elsewhere.write_text(json.dumps({ParameterKeys.COUNTRY: "DE"}), encoding="utf-8")
        out = workspace / "economics_result.json"
        code = main(
            [
                "staged",
                "--stage",
                f"{workspace / 'base'}:0:baseline",
                "--parameters",
                str(elsewhere),
                "--out",
                str(out),
            ]
        )
        assert code == StagedCli.PLAN_REFUSED
        assert not out.exists()
        problem = json.loads(
            (out.parent / StagedCli.PROBLEMS_FILE_NAME).read_text(encoding="utf-8")
        )["problems"][0]
        assert problem["code"] == "parameters.country.mismatch"
        assert "DE" in problem["message"] and STAGE_COUNTRY in problem["message"]

    def test_repeating_the_stages_country_is_accepted(self, workspace):
        """Saying the same thing twice is an assertion, not a contradiction."""
        same = workspace / "same.json"
        same.write_text(json.dumps({ParameterKeys.COUNTRY: STAGE_COUNTRY}), encoding="utf-8")
        out = workspace / "economics_result.json"
        assert (
            main(
                [
                    "staged",
                    "--stage",
                    f"{workspace / 'base'}:0:baseline",
                    "--parameters",
                    str(same),
                    "--perspective",
                    "brownfield_gross",
                    "--out",
                    str(out),
                ]
            )
            == 0
        )


class TestEveryOffendingKeyAtOnce:
    """A refused parameter block reports every fault in one ``problems.json`` (step 13 §1.3)."""

    def test_four_faults_are_four_problems_and_no_document(self, workspace):
        """An unknown key, a bad subsidy mode, a bad financing kind and a horizon of zero."""
        bad = workspace / "bad.json"
        bad.write_text(
            json.dumps(
                {
                    "discount_rate": 0.02,
                    "subsidy_mode": "partial",
                    "financing": {"kind": "leasing"},
                    "horizon_years": 0,
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
                str(bad),
                "--out",
                str(out),
            ]
        )
        assert code == StagedCli.PLAN_REFUSED
        assert not out.exists(), "a refused plan writes no document"
        problems = json.loads(
            (out.parent / StagedCli.PROBLEMS_FILE_NAME).read_text(encoding="utf-8")
        )["problems"]
        assert {problem["code"] for problem in problems} == {
            "parameters.unknown_key",
            "parameters.subsidy_mode.invalid",
            "parameters.financing.kind.invalid",
            "parameters.horizon_years.invalid",
        }


class TestThePerspectiveIsNamedOnce:
    """``--perspective`` and ``perspective_id`` are two spellings of one choice (step 13 §1)."""

    @staticmethod
    def _run(workspace: Path, in_file: str, flag: str) -> int:
        """Price the baseline alone with a perspective named in both places."""
        parameters_path = workspace / "perspective.json"
        parameters_path.write_text(
            json.dumps({ParameterKeys.PERSPECTIVE_ID: in_file}), encoding="utf-8"
        )
        return main(
            [
                "staged",
                "--stage",
                f"{workspace / 'base'}:0:baseline",
                "--parameters",
                str(parameters_path),
                "--perspective",
                flag,
                "--out",
                str(workspace / "economics_result.json"),
            ]
        )

    def test_a_disagreement_is_refused(self, workspace):
        """Two sources with two answers have no right answer, so neither silently wins."""
        assert self._run(workspace, "brownfield_gross", "brownfield_net") == StagedCli.PLAN_REFUSED
        problem = json.loads(
            (workspace / StagedCli.PROBLEMS_FILE_NAME).read_text(encoding="utf-8")
        )["problems"][0]
        assert problem["code"] == "parameters.perspective_id.mismatch"

    def test_agreeing_is_fine(self, workspace):
        """Saying the same id twice is what a backend that fills both in does."""
        assert self._run(workspace, "brownfield_gross", "brownfield_gross") == 0


class TestFinancingOverridesThePerspective:
    """``financing`` replaces the bundle perspective's plan, and the document shows the loans."""

    def test_a_loan_reaches_the_documents_financing_block(self, workspace):
        """``brownfield_gross`` buys for cash; the file makes it borrow, and the loans appear."""
        financed = workspace / "financed.json"
        financed.write_text(
            json.dumps(
                {
                    ParameterKeys.PERSPECTIVE_ID: "brownfield_gross",
                    ParameterKeys.FINANCING: {
                        "kind": "loan",
                        "financed_share": 0.5,
                        "nominal_interest_rate": 0.02,
                        "term_in_years": 10,
                    },
                }
            ),
            encoding="utf-8",
        )
        out = workspace / "economics_result.json"
        assert (
            main(
                [
                    "staged",
                    "--stage",
                    f"{workspace / 'base'}:0:baseline",
                    "--stage",
                    f"{workspace / 'envelope'}:0:stage 1",
                    "--stage",
                    f"{workspace / 'heat_pump'}:4:stage 2",
                    "--parameters",
                    str(financed),
                    "--out",
                    str(out),
                ]
            )
            == 0
        ), (workspace / StagedCli.PROBLEMS_FILE_NAME).read_text(encoding="utf-8")
        document = json.loads(out.read_text(encoding="utf-8"))
        loans = document["plan"]["financing"]["loans"]
        assert loans, "a financed plan borrows at least once"
        # One loan per disbursement year, stamped with the stage active in it: the envelope
        # measure's year-0 investment (stage 1) and the heat pump's in year 4 (stage 2). The
        # baseline stage buys nothing in a brownfield frame, so it borrows nothing.
        assert {loan["stage"] for loan in loans} == {1, 2}
        for loan in loans:
            assert loan["rate"] == 0.02
            assert loan["term_years"] == 10
            assert loan["principal_in_euro"]["best"] > 0
        assert document["parameters"]["financing"]["term_in_years"] == 10


class TestTheBackendsStageLayout:
    """A stage directory holding only the two files a backend's worker ships (shared todo H19).

    ``economics-backend-spec.md`` §3 puts each stage job's ``economic_inputs.json`` and
    ``mapping_report.json`` into the economics job's stage directory and nothing else. Such a
    stage carries no stored evaluation, so the country cannot come from one — which is why
    ``write_inputs`` writes it into the extract, where it is a fact of the house rather than an
    assumption anyone may change.
    """

    @staticmethod
    def _run(workspace: Path, block: Dict[str, Any]) -> int:
        """Price the two backend-shaped stages under one parameter block."""
        parameters_path = workspace / "backend.parameters.json"
        parameters_path.write_text(json.dumps(block), encoding="utf-8")
        return main(
            [
                "staged",
                "--stage",
                f"{workspace / 'base'}:0:baseline",
                "--stage",
                f"{workspace / 'heat_pump'}:4:stage 2",
                "--parameters",
                str(parameters_path),
                "--perspective",
                "brownfield_gross",
                "--out",
                str(workspace / "economics_result.json"),
            ]
        )

    def test_the_country_of_the_stored_inputs_prices_the_plan(self, workspace):
        """No stored evaluation, no country in the block, and the plan is still priced as Irish."""
        for name in ("base", "heat_pump"):
            _as_a_backend_stage(workspace / name, STAGE_COUNTRY, _the_year_the_shipped_data_prices_at())
        assert self._run(workspace, {ParameterKeys.HORIZON_YEARS: SyntheticPlan.HORIZON}) == 0, (
            workspace / StagedCli.PROBLEMS_FILE_NAME
        ).read_text(encoding="utf-8")
        document = json.loads((workspace / "economics_result.json").read_text(encoding="utf-8"))
        assert document["parameters"]["country"] == STAGE_COUNTRY

    def test_a_block_naming_another_country_is_still_refused(self, workspace):
        """The extract's country is as binding as a stored evaluation's."""
        for name in ("base", "heat_pump"):
            _as_a_backend_stage(workspace / name, STAGE_COUNTRY, _the_year_the_shipped_data_prices_at())
        assert self._run(workspace, {ParameterKeys.COUNTRY: "DE"}) == StagedCli.PLAN_REFUSED
        problem = json.loads(
            (workspace / StagedCli.PROBLEMS_FILE_NAME).read_text(encoding="utf-8")
        )["problems"][0]
        assert problem["code"] == "parameters.country.mismatch"

    def test_an_extract_from_before_the_key_existed_states_nothing(self, workspace):
        """Jobs run by an older image carry no country, and a plan over them has to be told one."""
        for name in ("base", "heat_pump"):
            _as_a_backend_stage(workspace / name, None, None)
        assert self._run(workspace, {}) == StagedCli.PLAN_REFUSED
        codes = {
            problem["code"]
            for problem in json.loads(
                (workspace / StagedCli.PROBLEMS_FILE_NAME).read_text(encoding="utf-8")
            )["problems"]
        }
        assert codes == {"parameters.country.missing", "parameters.price_basis_year.missing"}

    def test_the_basis_year_of_the_stored_inputs_prices_the_plan(self, workspace):
        """No stored evaluation, no year in the block, and the plan still prices at the stages'.

        The extract carries the *resolved* year the run actually priced at, so a plan assembled
        out of extracts prices at the same price level as the runs behind it — which is the whole
        reason the key is there rather than re-derived from ``simulation_year``.
        """
        year = _the_year_the_shipped_data_prices_at()
        for name in ("base", "heat_pump"):
            _as_a_backend_stage(workspace / name, STAGE_COUNTRY, year)
        assert self._run(workspace, {}) == 0, (
            workspace / StagedCli.PROBLEMS_FILE_NAME
        ).read_text(encoding="utf-8")
        document = json.loads((workspace / "economics_result.json").read_text(encoding="utf-8"))
        assert document["parameters"]["price_basis_year"] == year

    def test_a_block_naming_another_basis_year_is_refused(self, workspace):
        """The stored inputs were priced at theirs and cannot be re-based without re-running."""
        year = _the_year_the_shipped_data_prices_at()
        for name in ("base", "heat_pump"):
            _as_a_backend_stage(workspace / name, STAGE_COUNTRY, year)
        assert (
            self._run(workspace, {ParameterKeys.PRICE_BASIS_YEAR: year + 1})
            == StagedCli.PLAN_REFUSED
        )
        problem = json.loads(
            (workspace / StagedCli.PROBLEMS_FILE_NAME).read_text(encoding="utf-8")
        )["problems"][0]
        assert problem["code"] == "parameters.price_basis_year.mismatch"

    def test_an_old_extract_is_priced_by_naming_the_year_in_the_block(self, workspace):
        """Jobs from before the key existed are priceable, but only by saying at which level."""
        for name in ("base", "heat_pump"):
            _as_a_backend_stage(workspace / name, STAGE_COUNTRY, None)
        assert (
            self._run(
                workspace,
                {ParameterKeys.PRICE_BASIS_YEAR: _the_year_the_shipped_data_prices_at()},
            )
            == 0
        ), (workspace / StagedCli.PROBLEMS_FILE_NAME).read_text(encoding="utf-8")

    def test_a_stage_that_contradicts_itself_about_its_basis_year_is_refused(self, workspace):
        """Its stored evaluation and its stored inputs must state the same price level."""
        path = workspace / "base" / StagedCli.INPUTS_FILE_NAME
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw[SerializationFileNames.PRICE_BASIS_YEAR_KEY] = SyntheticPlan.YEAR + 1
        path.write_text(json.dumps(raw), encoding="utf-8")
        out = workspace / "economics_result.json"
        assert (
            main(["staged", "--stage", f"{workspace / 'base'}:0:baseline", "--out", str(out)])
            == StagedCli.PLAN_REFUSED
        )
        problem = json.loads(
            (out.parent / StagedCli.PROBLEMS_FILE_NAME).read_text(encoding="utf-8")
        )["problems"][0]
        assert problem["code"] == StagedCli.STAGE_PRICE_BASIS_YEAR_MISMATCH_CODE
        assert problem["path"] == "stages[0]"

    def test_two_stages_priced_at_different_basis_years_are_refused(self, workspace):
        """One plan is one price level, whichever file each stage states it in."""
        _as_a_backend_stage(workspace / "heat_pump", STAGE_COUNTRY, SyntheticPlan.YEAR + 1)
        out = workspace / "economics_result.json"
        assert (
            main(
                [
                    "staged",
                    "--stage",
                    f"{workspace / 'base'}:0:baseline",
                    "--stage",
                    f"{workspace / 'heat_pump'}:4:stage 2",
                    "--out",
                    str(out),
                ]
            )
            == StagedCli.PLAN_REFUSED
        )
        problem = json.loads(
            (out.parent / StagedCli.PROBLEMS_FILE_NAME).read_text(encoding="utf-8")
        )["problems"][0]
        assert problem["code"] == StagedCli.STAGE_PRICE_BASIS_YEAR_MISMATCH_CODE
        assert problem["path"] == "stages"

    def test_a_stage_that_contradicts_itself_is_refused(self, workspace):
        """Its stored evaluation and its stored inputs must say the same country."""
        path = workspace / "base" / StagedCli.INPUTS_FILE_NAME
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw[SerializationFileNames.COUNTRY_KEY] = "DE"
        path.write_text(json.dumps(raw), encoding="utf-8")
        out = workspace / "economics_result.json"
        assert (
            main(["staged", "--stage", f"{workspace / 'base'}:0:baseline", "--out", str(out)])
            == StagedCli.PLAN_REFUSED
        )
        problem = json.loads(
            (out.parent / StagedCli.PROBLEMS_FILE_NAME).read_text(encoding="utf-8")
        )["problems"][0]
        assert problem["code"] == StagedCli.STAGE_COUNTRY_MISMATCH_CODE
        assert problem["path"] == "stages[0]"

    def test_two_stages_priced_for_different_countries_are_refused(self, workspace):
        """One plan is one country's price data, whichever file each stage states it in."""
        _as_a_backend_stage(workspace / "heat_pump", "DE", SyntheticPlan.YEAR)
        out = workspace / "economics_result.json"
        assert (
            main(
                [
                    "staged",
                    "--stage",
                    f"{workspace / 'base'}:0:baseline",
                    "--stage",
                    f"{workspace / 'heat_pump'}:4:stage 2",
                    "--out",
                    str(out),
                ]
            )
            == StagedCli.PLAN_REFUSED
        )
        problem = json.loads(
            (out.parent / StagedCli.PROBLEMS_FILE_NAME).read_text(encoding="utf-8")
        )["problems"][0]
        assert problem["code"] == StagedCli.STAGE_COUNTRY_MISMATCH_CODE
        assert problem["path"] == "stages"


class TestTheShippedCatalogueIsTheDefault:
    """A staged plan finds the shipped catalogue without being told where it is (step 11 §3).

    The catalogue is the one input a stage directory does not carry: the extract holds the
    country and the price basis year, both facts of the run, but not a path into the engine's own
    data. So ``staged`` applies the rule the translator applies — the shipped
    ``hisim/subsidy_catalog`` directory when it has ``<COUNTRY>.json`` — instead of pricing with
    no catalogue, which published a plan whose every subsidy row was undetermined while the same
    plan over a full job directory priced the grants.
    """

    @staticmethod
    def _price_for(workspace: Path, country: str) -> Dict[str, Any]:
        """Price the baseline alone with every stage priced for one country, and read it back."""
        database = workspace / "cost_database"
        for name in ("base",):
            _write_stored_parameters(workspace / name, _stored_parameters(country, database))
            path = workspace / name / StagedCli.INPUTS_FILE_NAME
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw[SerializationFileNames.COUNTRY_KEY] = country
            path.write_text(json.dumps(raw), encoding="utf-8")
        out = workspace / f"{country}.json"
        assert (
            main(
                [
                    "staged",
                    "--stage",
                    f"{workspace / 'base'}:0:baseline",
                    "--perspective",
                    "brownfield_gross",
                    "--out",
                    str(out),
                ]
            )
            == 0
        ), (out.parent / StagedCli.PROBLEMS_FILE_NAME).read_text(encoding="utf-8")
        document: Dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
        return document

    def test_a_country_that_ships_one_is_priced_under_it(self, workspace):
        """No ``--subsidy-catalog`` and no path in the stages, and the document names a catalogue."""
        document = self._price_for(workspace, STAGE_COUNTRY)
        assert document["parameters"]["subsidy_catalog"] is not None
        assert document["parameters"]["subsidy_catalog"].startswith(f"{STAGE_COUNTRY}@")

    def test_a_country_that_ships_none_runs_without_one(self, workspace):
        """The synthetic country has no catalogue file, and the plan says so rather than failing.

        ``null`` is a statement a reader can act on: every subsidy row of such a document is
        undetermined, which is a different thing from a row worth nothing.
        """
        document = self._price_for(workspace, SyntheticPlan.COUNTRY)
        assert document["parameters"]["subsidy_catalog"] is None

    def test_the_translator_and_the_engine_answer_the_same_question(self):
        """One rule, two callers: a run and a staged plan over its outputs cannot disagree."""
        from hisim.economics.subsidies import SubsidyCatalog
        from hisim.renovisor.simulation import SubsidyCatalogue

        for country in (STAGE_COUNTRY, SyntheticPlan.COUNTRY):
            from_translator = SubsidyCatalogue.path_for(country)
            from_engine = SubsidyCatalog.shipped_catalog_file(country)
            assert (from_translator is None) == (from_engine is None)
            if from_engine is not None:
                assert str(from_translator) == from_engine
