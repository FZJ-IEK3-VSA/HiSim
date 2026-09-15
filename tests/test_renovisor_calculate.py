"""Tests of the file interface: an input directory in, an output directory out.

What a caller of the container can actually observe is tested here and nothing else -- the files
in the output directory and the exit code -- because that is the whole interface (requirement
R13). The three unsuccessful outcomes are exercised one by one, since telling them apart from
``errors.json`` alone is the point of acceptance criterion AC11.2, and each run is followed by a
working-tree cleanliness check, which is AC11.5.

No simulation runs in this file. The invalid and refused cases fail before one would start, and
the crash case injects a runner that raises instead of simulating, so the whole suite stays a
base-marked one that finishes in seconds.
"""

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from hisim.renovisor import TRANSLATOR_VERSION
from hisim.renovisor import calculate as calculate_module
from hisim.renovisor.calculate import (
    CalculationRunner,
    CalculationStatus,
    ErrorGroup,
    ExitCode,
    InputFiles,
    OutputFiles,
    RequiredOptions,
)
from hisim.renovisor.costs import CostField
from hisim.renovisor.kpis import KpiField
from hisim.renovisor.result import ResultBuilder
from hisim.renovisor.reasons import ReasonCode
from hisim.simulationparameters import SimulationParameters

pytestmark = pytest.mark.base

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
BASE_FILES = REPOSITORY_ROOT / "energy_systems"
EXAMPLE_INVENTORY_PATH = Path(__file__).resolve().parent / "renovisor" / "example_inventory_ie_1988_detached.json"
PARAMETERS_PATH = REPOSITORY_ROOT / "energy_systems" / "one_day_15min.simulation.yaml"


class ExplodingRunner:
    """A simulation runner that raises, so the crash path can be exercised without a simulation.

    Requirement R13.2.1 says a container that dies without writing ``errors.json`` is itself a
    defect, and the only way to test that is to make the simulation fail on purpose.
    """

    #: What it raises, so the test can find the sentence again in ``errors.json``.
    MESSAGE = "the heat pump fell over"

    def run(
        self,
        energy_system_path: Path,
        parameters_path: Path,
        parameters: SimulationParameters,
        record_directory: Path,
    ) -> None:
        """Raise instead of simulating.

        Raises:
            RuntimeError: Always, with :attr:`MESSAGE`.
        """
        del energy_system_path, parameters_path, parameters, record_directory
        raise RuntimeError(self.MESSAGE)


def write_input_directory(
    directory: Path, package: Any, inventory: Optional[Dict[str, Any]] = None
) -> Path:
    """Write one complete input directory and return it.

    Args:
        directory: Where to write; created if missing.
        package: The ``package.json`` document, as it should reach the runner.
        inventory: The inventory document; the committed example when omitted.

    Returns:
        The directory.
    """
    directory.mkdir(parents=True, exist_ok=True)
    document = (
        inventory
        if inventory is not None
        else json.loads(EXAMPLE_INVENTORY_PATH.read_text(encoding="utf-8"))
    )
    (directory / InputFiles.INVENTORY).write_text(json.dumps(document), encoding="utf-8")
    (directory / InputFiles.PACKAGE).write_text(json.dumps(package), encoding="utf-8")
    (directory / InputFiles.PARAMETER_NAMES[0]).write_text(
        PARAMETERS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return directory


def worktree_status() -> str:
    """Return ``git status --porcelain`` of this checkout, for the cleanliness check (AC11.5)."""
    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def errors_of(output: Path) -> Dict[str, Any]:
    """Return the parsed ``errors.json`` of one output directory."""
    document: Dict[str, Any] = json.loads((output / OutputFiles.ERRORS).read_text(encoding="utf-8"))
    return document


def outcome_of(output: Path) -> Dict[str, Any]:
    """Return the parsed ``calculation.json`` of one output directory."""
    document: Dict[str, Any] = json.loads(
        (output / OutputFiles.CALCULATION).read_text(encoding="utf-8")
    )
    return document


def test_a_malformed_package_is_invalid(tmp_path: Path) -> None:
    """A measure the catalogue does not have is the caller's mistake: exit 2, group validation."""
    inputs = write_input_directory(
        tmp_path / "in", {"measures": [{"measure_id": "PAINT_IT_BLUE", "options": {}}]}
    )
    output = tmp_path / "out"

    code = CalculationRunner(inputs, output, base_files_directory=BASE_FILES).run()

    assert code is ExitCode.INVALID
    assert outcome_of(output)["status"] == CalculationStatus.INVALID.value
    errors = errors_of(output)
    assert errors["status"] == CalculationStatus.INVALID.value
    assert errors["errors"][0]["reason"] == ReasonCode.UNKNOWN_MEASURE.value
    assert errors["errors"][0]["group"] == ErrorGroup.VALIDATION.value
    assert "PAINT_IT_BLUE" in errors["errors"][0]["detail"]
    assert "traceback" not in errors


def test_a_missing_input_file_is_invalid(tmp_path: Path) -> None:
    """A caller that forgot a file gets a reason code rather than a stack trace."""
    inputs = tmp_path / "in"
    inputs.mkdir()
    output = tmp_path / "out"

    code = CalculationRunner(inputs, output, base_files_directory=BASE_FILES).run()

    assert code is ExitCode.INVALID
    assert errors_of(output)["errors"][0]["reason"] == ReasonCode.SCHEMA_VIOLATION.value


def test_an_unsimulable_package_is_refused(tmp_path: Path) -> None:
    """Decision Q13: window panes have no material rows yet, so the request is refused, not run."""
    inputs = write_input_directory(
        tmp_path / "in",
        {"measures": [{"measure_id": "WINDOW_REPLACEMENT", "options": {"glazing_panes": 3}}]},
    )
    output = tmp_path / "out"

    code = CalculationRunner(inputs, output, base_files_directory=BASE_FILES).run()

    assert code is ExitCode.REFUSED
    assert outcome_of(output)["status"] == CalculationStatus.REFUSED.value
    errors = errors_of(output)
    assert errors["errors"][0]["reason"] == ReasonCode.MATERIAL_NOT_IN_DATABASE.value
    assert errors["errors"][0]["group"] == ErrorGroup.REFUSAL.value
    assert "traceback" not in errors


def test_a_crashing_simulation_is_failed_and_carries_its_traceback(tmp_path: Path) -> None:
    """The third outcome: the request was accepted and the calculation then broke."""
    inputs = write_input_directory(
        tmp_path / "in",
        {"measures": [{"measure_id": "CHANGE_ROOM_TEMPERATURE", "options": {"new_room_temperature": 21}}]},
    )
    output = tmp_path / "out"

    code = CalculationRunner(
        inputs, output, base_files_directory=BASE_FILES, simulation_runner=ExplodingRunner()
    ).run()

    assert code is ExitCode.FAILED
    assert outcome_of(output)["status"] == CalculationStatus.FAILED.value
    errors = errors_of(output)
    assert errors["errors"][0]["reason"] == ReasonCode.SIMULATION_FAILED.value
    assert errors["errors"][0]["group"] == ErrorGroup.CRASH.value
    assert ExplodingRunner.MESSAGE in errors["errors"][0]["detail"]
    assert "RuntimeError" in errors["traceback"]


def test_the_file_that_would_have_run_is_written_before_the_run(tmp_path: Path) -> None:
    """Requirement R16 in spirit: a crashed calculation still leaves behind what it was running."""
    inputs = write_input_directory(
        tmp_path / "in",
        {"measures": [{"measure_id": "CHANGE_ROOM_TEMPERATURE", "options": {"new_room_temperature": 21}}]},
    )
    output = tmp_path / "out"

    CalculationRunner(
        inputs, output, base_files_directory=BASE_FILES, simulation_runner=ExplodingRunner()
    ).run()

    assert (output / OutputFiles.PARAMETRISED).is_file()
    assert (output / OutputFiles.TRANSLATION_REPORT).is_file()
    report = json.loads((output / OutputFiles.TRANSLATION_REPORT).read_text(encoding="utf-8"))
    assert report["translator_version"] == TRANSLATOR_VERSION
    assert report["base_file"].endswith(".energy_system.yaml")
    assert report["contract"]["commit"]
    assert report["image_digest"] is None


def test_the_image_digest_is_echoed_when_the_caller_passes_one(tmp_path: Path) -> None:
    """Decision Q26: the calculation's version is the image digest, and HiSim only repeats it."""
    inputs = write_input_directory(tmp_path / "in", {"measures": []})
    output = tmp_path / "out"

    CalculationRunner(
        inputs,
        output,
        base_files_directory=BASE_FILES,
        image_digest="sha256:abc",
        simulation_runner=ExplodingRunner(),
    ).run()

    assert outcome_of(output)["image_digest"] == "sha256:abc"
    report = json.loads((output / OutputFiles.TRANSLATION_REPORT).read_text(encoding="utf-8"))
    assert report["image_digest"] == "sha256:abc"


def test_the_outcome_lists_the_files_it_wrote(tmp_path: Path) -> None:
    """``calculation.json`` is what a caller reads to know what to collect."""
    inputs = write_input_directory(tmp_path / "in", {"measures": []})
    output = tmp_path / "out"

    CalculationRunner(
        inputs, output, base_files_directory=BASE_FILES, simulation_runner=ExplodingRunner()
    ).run()

    listed: List[str] = outcome_of(output)["output_files"]
    assert OutputFiles.CALCULATION in listed
    assert OutputFiles.ERRORS in listed
    assert OutputFiles.PARAMETRISED in listed
    assert OutputFiles.TRANSLATION_REPORT in listed


def test_nothing_is_written_outside_the_output_directory(tmp_path: Path) -> None:
    """Acceptance criterion AC11.5: the checkout is as clean after a calculation as before it."""
    before = worktree_status()
    for index, package in enumerate(
        [
            {"measures": [{"measure_id": "PAINT_IT_BLUE", "options": {}}]},
            {"measures": [{"measure_id": "WINDOW_REPLACEMENT", "options": {"glazing_panes": 3}}]},
            {
                "measures": [
                    {"measure_id": "CHANGE_ROOM_TEMPERATURE", "options": {"new_room_temperature": 21}}
                ]
            },
        ]
    ):
        inputs = write_input_directory(tmp_path / f"in{index}", package)
        CalculationRunner(
            inputs,
            tmp_path / f"out{index}",
            base_files_directory=BASE_FILES,
            simulation_runner=ExplodingRunner(),
        ).run()

    assert worktree_status() == before


def test_the_command_line_returns_the_outcomes_exit_code(tmp_path: Path) -> None:
    """The command line is a thin layer, and this is the whole of what it has to get right."""
    from hisim.renovisor.__main__ import RenovisorCommandLine  # noqa: PLC0415  (module under test)

    inputs = write_input_directory(
        tmp_path / "in", {"measures": [{"measure_id": "PAINT_IT_BLUE", "options": {}}]}
    )

    code = RenovisorCommandLine.main(
        ["calculate", str(inputs), str(tmp_path / "out"), "--base-files", str(BASE_FILES)]
    )

    assert code == int(ExitCode.INVALID)


class RecordingRunner:
    """A simulation runner that records the parameters it was handed and simulates nothing.

    The three post-processing options a result payload needs are added by the runner *before* the
    simulation starts, and what the simulation is then given is the only place that decision can
    be observed. Injecting this in place of the real runner makes that observable without a run.
    """

    def __init__(self) -> None:
        """Start with nothing recorded."""
        self.parameters: Optional[SimulationParameters] = None

    def run(
        self,
        energy_system_path: Path,
        parameters_path: Path,
        parameters: SimulationParameters,
        record_directory: Path,
    ) -> None:
        """Record the parameters and return without simulating."""
        del energy_system_path, parameters_path, record_directory
        self.parameters = parameters


def a_heat_pump_package() -> List[Dict[str, Any]]:
    """Return the measure list of a package that changes the heating system and nothing else."""
    return [{"measure_id": "HEATING_SYSTEM", "options": {"type_of_system": "HEAT_PUMP"}}]


def test_the_options_a_result_needs_are_added_and_recorded(tmp_path: Path) -> None:
    """A caller who asked for a RenoVisor result asked for the switches that produce one."""
    inputs = write_input_directory(tmp_path / "in", {"measures": a_heat_pump_package()})
    output = tmp_path / "out"
    runner = RecordingRunner()

    CalculationRunner(
        inputs, output, base_files_directory=BASE_FILES, simulation_runner=runner
    ).run()

    assert runner.parameters is not None
    for option in RequiredOptions.REQUIRED:
        assert option in runner.parameters.post_processing_options
    assert outcome_of(output)["options_added"] == [option.name for option in RequiredOptions.REQUIRED]


def test_the_cost_engine_is_pointed_at_the_dwellings_own_country(tmp_path: Path) -> None:
    """The engine prices against ``_IE`` data files, which it picks from the attached parameters."""
    inputs = write_input_directory(tmp_path / "in", {"measures": a_heat_pump_package()})
    runner = RecordingRunner()

    CalculationRunner(
        inputs, tmp_path / "out", base_files_directory=BASE_FILES, simulation_runner=runner
    ).run()

    assert runner.parameters is not None
    assert runner.parameters.economic_parameters is not None
    assert runner.parameters.economic_parameters.country == "IE"


def test_a_result_payload_is_written_even_when_the_run_produced_no_figures(tmp_path: Path) -> None:
    """A payload with nothing in it still says what is missing and why (decision R8)."""
    inputs = write_input_directory(tmp_path / "in", {"measures": a_heat_pump_package()})
    output = tmp_path / "out"

    code = CalculationRunner(
        inputs, output, base_files_directory=BASE_FILES, simulation_runner=RecordingRunner()
    ).run()

    assert code is ExitCode.FINISHED
    payload: Dict[str, Any] = json.loads((output / OutputFiles.RESULT).read_text(encoding="utf-8"))
    assert payload["base_file"]
    assert payload["weather_basis"]["location"] == "IE"
    assert payload["period"]["fraction_of_year"] == pytest.approx(1 / 365, rel=1e-2)
    missing = {entry["field"] for entry in payload["missing"]}
    assert f"kpis.{KpiField.ENERGY_DEMAND.value}" in missing
    assert f"costs.{CostField.NET_PRESENT_VALUE.value}" in missing
    assert OutputFiles.RESULT in outcome_of(output)["output_files"]


def test_a_failure_while_deriving_the_result_has_its_own_reason_code(tmp_path: Path) -> None:
    """A payload that cannot be derived is a different failure from a simulation that died."""
    inputs = write_input_directory(tmp_path / "in", {"measures": a_heat_pump_package()})
    output = tmp_path / "out"
    runner = RecordingRunner()

    class BrokenBuilder(ResultBuilder):
        """A builder that raises, standing in for any defect in the derivation."""

        def build(self, contract: Any) -> Dict[str, Any]:
            """Raise instead of assembling a payload."""
            raise ValueError("no")

    calculation = CalculationRunner(
        inputs, output, base_files_directory=BASE_FILES, simulation_runner=runner
    )
    original = calculate_module.ResultBuilder
    calculate_module.ResultBuilder = BrokenBuilder  # type: ignore[misc]
    try:
        code = calculation.run()
    finally:
        calculate_module.ResultBuilder = original  # type: ignore[misc]

    assert code is ExitCode.FAILED
    errors = errors_of(output)["errors"]
    assert errors[0]["reason"] == ReasonCode.RESULT_DERIVATION_FAILED.value
    assert errors[0]["group"] == ErrorGroup.CRASH.value
    assert (output / OutputFiles.TRANSLATION_REPORT).is_file(), "the report must survive a failed derivation"
