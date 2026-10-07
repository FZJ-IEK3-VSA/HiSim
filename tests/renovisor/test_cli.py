"""T-CLI: the six commands, the four exit codes, and what each of them leaves on disk.

The command line is the whole interface the backend's worker has, so every way a calculation can
end has to be a file and a number rather than a traceback. Exit 0 writes everything of §2.2, exit
2 writes ``problems.json`` and nothing else, exit 3 writes ``translator_error.json``, and exit 5
writes whatever HiSim managed before it raised. The last line on standard error for 3 and 5 is
one line, because the backend shows it as the job's error message.

The simulation is injected for every test here: a runner that does nothing proves the output
collection, and one that raises proves exit 5, neither of which needs a day of weather.
"""

import copy
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from hisim.calculation_progress import ProgressLine, ProgressWriter
from hisim.economics.staged_record import EconomicsStageRecord
from hisim.renovisor.__main__ import RenovisorCommandLine
from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.run import Calculation, ExitCode, LifecycleCosts, Outputs
from hisim.renovisor.simulation import SimulationParameters
from hisim.simulator import ProgressEvent, SimulationProgress

BASE_FILES = Path(__file__).resolve().parents[2] / "energy_systems"


class SilentRunner:
    """A simulation that does nothing, so the output collection can be tested in milliseconds."""

    def run(self, _energy_system_path: Path, parameters: SimulationParameters, record_directory: Path) -> None:
        """Write the records and the cost engine's export HiSim would write, and run no timestep."""
        for name in Outputs.RECORDS:
            (record_directory / name).write_text("# a stand-in for HiSim's own record\n", encoding="utf-8")
        (Path(parameters.result_directory) / LifecycleCosts.FILE_NAME).write_text("{}\n", encoding="utf-8")


class CostlessRunner:
    """A simulation that finishes without the cost engine having written anything."""

    def run(self, _energy_system_path: Path, _parameters: SimulationParameters, record_directory: Path) -> None:
        """Write the records only."""
        for name in Outputs.RECORDS:
            (record_directory / name).write_text("# a stand-in for HiSim's own record\n", encoding="utf-8")


class FailingCostEngineRunner:
    """A simulation whose postprocessing runs the real lifecycle-cost step against an engine that raises."""

    #: The #66 error, as the engine raised it.
    ENGINE_ERROR: str = (
        "Timeline entry violates the §3.9 sign convention: LOAN_DISBURSEMENT entry for 'financing' in "
        "year 0 (payer system) should be non-positive but is (-4800.0, -0.0, 3900.0)"
    )

    def run(self, _energy_system_path: Path, parameters: SimulationParameters, _record_directory: Path) -> None:
        """Hand the calculation's own parameters to ``PostProcessor.compute_lifecycle_costs``."""
        from unittest import mock

        from hisim.postprocessing.postprocessing_main import PostProcessor

        ppdt = SimpleNamespace(
            wrapped_components=[],
            all_outputs=[],
            results=None,
            simulation_parameters=parameters,
            post_processing_options=list(parameters.post_processing_options),
        )
        with mock.patch(
            "hisim.economics.bridge.compute_lifecycle_costs", side_effect=ValueError(self.ENGINE_ERROR)
        ):
            PostProcessor().compute_lifecycle_costs(ppdt)  # type: ignore[arg-type]


class CrashingRunner:
    """A simulation that raises, which is what exit 5 is for."""

    def run(self, energy_system_path: Path, parameters: SimulationParameters, record_directory: Path) -> None:
        """Raise the way HiSim raises when a storage temperature runs away."""
        raise ValueError("The water temperature in the water storage is with 90.4 C way too high or too low.")


def write_request(path: Path, **changes: Any) -> Path:
    """Write the vendored mockup, with the given top-level keys replaced, and return the path."""
    document: Dict[str, Any] = copy.deepcopy(ContractFiles.request_mockup())
    document.update(changes)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def calculation(request: Path, out: Path, runner: Any = None) -> Calculation:
    """Return a calculation over the committed tables with an injected simulation runner."""
    return Calculation(
        request_path=request,
        output_directory=out,
        base_files_directory=BASE_FILES,
        simulation_runner=runner or SilentRunner(),
    )


@pytest.mark.base
class TestExitZero:
    """A run that finished writes everything §2.2 names and nothing outside ``--out``."""

    def test_translate_writes_the_file_and_the_report_and_stops(self, tmp_path: Path) -> None:
        """``translate`` is what the verification harness and every probe use."""
        request = write_request(tmp_path / "request.json", measures=[])
        out = tmp_path / "out"

        assert calculation(request, out).translate_only() == ExitCode.FINISHED

        written = {path.name for path in out.iterdir()}
        assert Outputs.MAPPING_REPORT in written
        assert any(name.startswith("renovisor_") for name in written)
        assert Outputs.RESULT not in written

    def test_run_writes_the_records_and_the_manifest(self, tmp_path: Path) -> None:
        """The manifest says which options ran and which image, so a result can be traced."""
        request = write_request(tmp_path / "request.json", measures=[])
        out = tmp_path / "out"

        assert calculation(request, out).run() == ExitCode.FINISHED

        written = {path.name for path in out.iterdir()}
        assert set(Outputs.RECORDS) <= written
        manifest = json.loads((out / Outputs.CALCULATION).read_text(encoding="utf-8"))
        assert manifest["status"] == "finished"
        assert "COMPUTE_LIFECYCLE_COSTS" in manifest["options_added"]
        assert manifest["period"] == "full_year"

    def test_the_mapping_report_is_readable_json_with_the_contracts_shape(self, tmp_path: Path) -> None:
        """A person has to be able to read it without a tool, and a machine without a schema."""
        request = write_request(tmp_path / "request.json", measures=[])
        out = tmp_path / "out"
        calculation(request, out).translate_only()

        report = json.loads((out / Outputs.MAPPING_REPORT).read_text(encoding="utf-8"))

        assert set(report) == {
            "translator",
            "base_file",
            "energy_system_file",
            "fields",
            "measures",
            # The economics half (step 10 §4.2): which measure created which cost subject, and
            # which of those subjects the request carried no price for.
            "subjects",
            "unpriced_subjects",
            # A measure without a priced subject still gets its row (renovisorissues #58): the
            # subjects that cost nothing, and why a row is unpriced or costs nothing.
            "costless_subjects",
            "subject_notes",
            # The reference subjects each measure subject replaces (hisim-ryw1).
            "replaces_subjects",
            # The same economics keys, the stage's measures and the measure tables, as the plain
            # record ``python -m hisim.economics staged`` reads (``EconomicsStageRecord``).
            "economics_stage",
        }
        assert report["fields"] and all("status" in line for line in report["fields"])
        # The record reads back with the engine's own reader, so a run's directory is a stage.
        record = EconomicsStageRecord.from_report(report)
        assert not record.measures
        assert record.subjects == report["subjects"]


class LoopReportingRunner(SilentRunner):
    """A simulation that reports a one-day, hourly time loop to the calculation's progress writer."""

    def __init__(self, progress: ProgressWriter) -> None:
        """Keep the writer the calculation was given, as the real runner keeps its callback."""
        self._progress = progress

    def run(self, energy_system_path: Path, parameters: SimulationParameters, record_directory: Path) -> None:
        """Report the loop's start, one periodic message and its end, then write what HiSim writes."""
        for event, done in ((ProgressEvent.LOOP_START, 0), (ProgressEvent.PERIODIC, 12), (ProgressEvent.LOOP_END, 24)):
            self._progress.on_simulation_progress(
                SimulationProgress(
                    event=event, timesteps_done=done, timesteps=24, seconds_per_timestep=3600, eta_seconds=None
                )
            )
        super().run(energy_system_path, parameters, record_directory)


def progress_lines(text: str) -> List[Dict[str, Any]]:
    """Return the progress lines among standard output's lines, parsed."""
    return [fields for fields in map(ProgressLine.parse, text.splitlines()) if fields is not None]


@pytest.mark.base
class TestProgress:
    """``run`` reports its five phases in order on standard output (progress-spec §1)."""

    def test_run_writes_the_five_phases_in_order(self, tmp_path: Path, capsys) -> None:
        """reading, preparing, simulating, evaluating, writing; days from simulating on."""
        request = write_request(tmp_path / "request.json", measures=[])
        out = tmp_path / "out"
        progress = ProgressWriter()
        run = Calculation(
            request_path=request,
            output_directory=out,
            base_files_directory=BASE_FILES,
            simulation_runner=LoopReportingRunner(progress),
            progress=progress,
        )

        assert run.run() == ExitCode.FINISHED

        lines = progress_lines(capsys.readouterr().out)
        phases = [line["phase"] for line in lines]
        assert list(dict.fromkeys(phases)) == ["reading", "preparing", "simulating", "evaluating", "writing"]
        assert "simulated_days" not in lines[0] and "simulated_days" not in lines[1]
        days = [line["simulated_days"] for line in lines[2:]]
        assert days == sorted(days) and days[-1] == 1.0
        manifest = json.loads((out / Outputs.CALCULATION).read_text(encoding="utf-8"))
        assert manifest["simulated_days"] == 1.0

    def test_a_runner_that_reports_no_loop_still_gets_the_phases_in_order(self, tmp_path: Path, capsys) -> None:
        """No days are invented: the manifest says none were simulated."""
        request = write_request(tmp_path / "request.json", measures=[])
        out = tmp_path / "out"

        assert calculation(request, out).run() == ExitCode.FINISHED

        phases = [line["phase"] for line in progress_lines(capsys.readouterr().out)]
        assert phases == ["reading", "preparing", "evaluating", "writing"]
        manifest = json.loads((out / Outputs.CALCULATION).read_text(encoding="utf-8"))
        assert manifest["simulated_days"] is None

    def test_a_run_with_standard_output_closed_still_finishes(self, tmp_path: Path) -> None:
        """Progress is informational: a line that cannot be written is dropped (spec §6)."""
        request = write_request(tmp_path / "request.json", measures=[])
        closed = io.StringIO()
        closed.close()
        progress = ProgressWriter(stream=closed)
        run = Calculation(
            request_path=request,
            output_directory=tmp_path / "out",
            base_files_directory=BASE_FILES,
            simulation_runner=LoopReportingRunner(progress),
            progress=progress,
        )
        assert run.run() == ExitCode.FINISHED

    def test_translate_writes_no_progress(self, tmp_path: Path, capsys) -> None:
        """Only ``run`` and ``staged`` report progress."""
        request = write_request(tmp_path / "request.json", measures=[])
        assert calculation(request, tmp_path / "out").translate_only() == ExitCode.FINISHED
        assert not progress_lines(capsys.readouterr().out)


@pytest.mark.base
class TestExitTwo:
    """An invalid request writes ``problems.json`` and nothing else."""

    def test_a_bad_value_is_exit_two_with_the_problems_file(self, tmp_path: Path) -> None:
        """Every fault at once, by path and code, which is what a frontend acts on."""
        document = copy.deepcopy(ContractFiles.request_mockup())
        document["house"]["heating"]["type_of_system"] = "coal_fired_dragon"
        request = tmp_path / "request.json"
        request.write_text(json.dumps(document), encoding="utf-8")
        out = tmp_path / "out"

        assert calculation(request, out).run() == ExitCode.REQUEST_INVALID

        assert {path.name for path in out.iterdir()} == {Outputs.PROBLEMS}
        problems = json.loads((out / Outputs.PROBLEMS).read_text(encoding="utf-8"))["problems"]
        assert problems[0]["path"] == "house.heating.type_of_system"
        assert problems[0]["code"] == "enum.unknown"

    @pytest.mark.parametrize(
        "applicant, path", [(None, "applicant"), ({"household_size": 2}, "applicant.main_residence")]
    )
    def test_a_request_that_does_not_say_main_residence_is_exit_two(
        self, tmp_path: Path, applicant: Any, path: str
    ) -> None:
        """hisim-snt9: no subsidy is priced on an assumed answer; the request is refused by name."""
        document = copy.deepcopy(ContractFiles.request_mockup())
        if applicant is None:
            del document["applicant"]
        else:
            document["applicant"] = applicant
        request = tmp_path / "request.json"
        request.write_text(json.dumps(document), encoding="utf-8")
        out = tmp_path / "out"

        assert calculation(request, out).run() == ExitCode.REQUEST_INVALID

        problems = json.loads((out / Outputs.PROBLEMS).read_text(encoding="utf-8"))["problems"]
        assert [(problem["path"], problem["code"]) for problem in problems] == [(path, "required.missing")]

    def test_a_file_that_is_not_a_request_is_exit_two_as_well(self, tmp_path: Path) -> None:
        """An unreadable file is the same kind of answer as an unreadable field."""
        request = tmp_path / "request.json"
        request.write_text("{ this is not json", encoding="utf-8")
        out = tmp_path / "out"

        assert calculation(request, out).run() == ExitCode.REQUEST_INVALID
        assert (out / Outputs.PROBLEMS).is_file()

    def test_validate_agrees_with_run_and_writes_nothing(self, tmp_path: Path, capsys: Any) -> None:
        """``run`` validates as its first step, so the two can never disagree."""
        document = copy.deepcopy(ContractFiles.request_mockup())
        document["location"]["country"] = "ES"
        request = tmp_path / "request.json"
        request.write_text(json.dumps(document), encoding="utf-8")

        code = RenovisorCommandLine.main(["validate", str(request)])

        assert code == int(ExitCode.REQUEST_INVALID)
        printed = json.loads(capsys.readouterr().out)
        assert printed["problems"][0]["code"] == "location.country.unsupported"

    def test_validate_says_nothing_is_wrong_with_the_mockup(self, tmp_path: Path, capsys: Any) -> None:
        """The one worked example both sides point at has to pass its own validator."""
        request = write_request(tmp_path / "request.json")

        code = RenovisorCommandLine.main(["validate", str(request)])

        assert code == int(ExitCode.FINISHED)
        assert json.loads(capsys.readouterr().out) == {"problems": []}


@pytest.mark.base
class TestExitThree:
    """A translator error is a bug in this package, and it says so in one line."""

    def test_an_unlisted_unmapped_item_is_exit_three(self, tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
        """With an empty list, everything the translator does not map fails its own build."""
        from hisim.renovisor import run as run_module
        from hisim.renovisor.whitelist import Whitelist

        monkeypatch.setattr(run_module, "Whitelist", type("Empty", (), {"load": staticmethod(lambda: Whitelist([]))}))
        request = write_request(tmp_path / "request.json", measures=[])
        out = tmp_path / "out"

        assert calculation(request, out).run() == ExitCode.TRANSLATOR_ERROR

        body = json.loads((out / Outputs.TRANSLATOR_ERROR).read_text(encoding="utf-8"))
        assert set(body) == {"message", "detail"}
        stderr = capsys.readouterr().err.strip().splitlines()
        assert len(stderr) == 1


@pytest.mark.base
class TestExitFive:
    """A simulation that raised leaves what HiSim wrote and one line on standard error."""

    def test_a_crashing_simulation_is_exit_five(self, tmp_path: Path, capsys: Any) -> None:
        """The file and the report are already on disk, which is when they are worth the most."""
        request = write_request(tmp_path / "request.json", measures=[])
        out = tmp_path / "out"

        assert calculation(request, out, CrashingRunner()).run() == ExitCode.SIMULATION_ERROR

        written = {path.name for path in out.iterdir()}
        assert Outputs.MAPPING_REPORT in written
        assert Outputs.RESULT not in written
        stderr = capsys.readouterr().err.strip().splitlines()
        assert len(stderr) == 1
        assert "water temperature" in stderr[0]

    def test_a_failing_cost_engine_is_exit_five_naming_its_error(self, tmp_path: Path, capsys: Any) -> None:
        """No money is a failed calculation, and the one line says why (renovisorissues #66).

        The runner hands the calculation's own parameters to the real postprocessing step, so
        this proves both halves: the RenoVisor run requires the lifecycle costs, and a required
        engine that raises fails the run with its error named (owner decision 2026-09-27).
        """
        request = write_request(tmp_path / "request.json", measures=[])
        out = tmp_path / "out"

        assert calculation(request, out, FailingCostEngineRunner()).run() == ExitCode.SIMULATION_ERROR

        assert Outputs.RESULT not in {path.name for path in out.iterdir()}
        stderr = capsys.readouterr().err.strip().splitlines()
        assert len(stderr) == 1
        assert "the lifecycle cost engine failed: ValueError: " in stderr[0]
        assert FailingCostEngineRunner.ENGINE_ERROR in stderr[0]

    def test_a_run_without_a_cost_export_is_exit_five(self, tmp_path: Path, capsys: Any) -> None:
        """A simulation that never reached the engine is no finished calculation either."""
        request = write_request(tmp_path / "request.json", measures=[])
        out = tmp_path / "out"

        assert calculation(request, out, CostlessRunner()).run() == ExitCode.SIMULATION_ERROR

        assert Outputs.RESULT not in {path.name for path in out.iterdir()}
        stderr = capsys.readouterr().err.strip().splitlines()
        assert len(stderr) == 1
        assert LifecycleCosts.FILE_NAME in stderr[0]


@pytest.mark.base
class TestAPlainSimulationKeepsItsLeniency:
    """Outside RenoVisor a failing cost engine stays a log line: the legacy outputs are unaffected."""

    def test_an_unrequired_engine_failure_does_not_raise(self) -> None:
        """Same postprocessing step, parameters that did not require the costs: no exception."""
        from unittest import mock

        from hisim.postprocessing.postprocessing_main import PostProcessor

        parameters = SimulationParameters.full_year(year=2021, seconds_per_timestep=900)
        assert parameters.lifecycle_costs_required is False
        ppdt = SimpleNamespace(
            wrapped_components=[], all_outputs=[], results=None, simulation_parameters=parameters,
            post_processing_options=[],
        )
        with mock.patch("hisim.economics.bridge.compute_lifecycle_costs", side_effect=ValueError("boom")):
            PostProcessor().compute_lifecycle_costs(ppdt)  # type: ignore[arg-type]


@pytest.mark.base
class TestTheCommandLineItself:
    """Six commands, one subcommand deep, and no ``--variant``."""

    def test_the_parser_declares_the_commands_the_backend_calls(self) -> None:
        """A command that quietly disappeared would be found by the backend, not by a test."""
        listed = re.search(r"\{([a-z,]+)\}", RenovisorCommandLine.parser().format_help())

        assert listed is not None
        assert set(listed.group(1).split(",")) == {
            "run",
            "translate",
            "validate",
            "capabilities",
            "map",
            "verify",
        }

    def test_run_takes_a_period_and_nothing_takes_a_variant(self) -> None:
        """The baseline is a request with an empty package, so there is no switch for it."""
        parser = RenovisorCommandLine.parser()
        arguments = parser.parse_args(["run", "r.json", "--out", "o", "--period", "one_day_15min"])

        assert arguments.period == "one_day_15min"
        assert not hasattr(arguments, "variant")
