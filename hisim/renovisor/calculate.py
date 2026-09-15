"""One calculation: an input directory in, an output directory out, and nothing else.

This is the whole interface a container invocation has (requirement R13). A caller writes three
files into a directory, starts the container, and reads what it finds in the output directory --
it imports no Python, parses no log and passes no arguments beyond the two paths::

    python -m hisim.renovisor calculate ./in ./out --cache-dir /var/cache/hisim

    in/                                out/
      home_inventory.json                parametrised.energy_system.yaml
      package.json                       realized.energy_system.yaml
      simulation.yaml                    realized.audit.yaml
                                         component_connections.json
                                         translation_report.json
                                         calculation.json
                                         errors.json        (only when something went wrong)
                                         results/           the simulation's own output

Three properties of this module are the point of it rather than details of it.

**Three outcomes are told apart from files alone** (requirement R11, R13.2). A malformed request
is ``invalid``, a well-formed one this HiSim cannot simulate is ``refused``, and a calculation that
broke after being accepted is ``failed``; each writes ``errors.json`` with a reason code from the
published catalogue, the offending path, and -- for a crash alone -- a traceback. The exit code
says the same thing for a caller that cannot read a file.

**Nothing is written outside the output directory** (requirement R13.4). The simulation's result
directory is set to ``OUTPUT_DIR/results`` before anything is built, the result-path singleton is
reset so that a previous calculation in the same process cannot decide where this one writes, and
the profile and weather caches are the one deliberate exception (decision Q25): they are shared
state a container mounts, not output.

**The last two files cannot be lost.** A calculation that dies without saying so is
indistinguishable from one that hung, so ``calculation.json`` and ``errors.json`` are written on
every path out of :meth:`CalculationRunner.run`, including the one taken when the writing of the
results themselves failed.
"""

import json
import os
import traceback
from dataclasses import dataclass
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Protocol, Sequence, Tuple

from hisim.energy_system.executor import (
    SimulationParametersReader,
    build_energy_system,
    write_records,
)
from hisim.renovisor import TRANSLATOR_VERSION
from hisim.renovisor.application import ApplicationResult, PackageApplication
from hisim.renovisor.catalogue import Catalogue
from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.inventory import Inventory
from hisim.renovisor.laws import PreRunDemandEstimator
from hisim.renovisor.materials import InsulationMaterials
from hisim.renovisor.occupancy import HouseholdMatcher
from hisim.renovisor.parametriser import ParametrisedSystem, Parametriser
from hisim.renovisor.reasons import ReasonCode, RefusalError, ValidationError
from hisim.renovisor.registry import MeasureRegistry
from hisim.result_path_provider import ResultPathProviderSingleton
from hisim.simulationparameters import SimulationParameters


class CalculationStatus(str, Enum):
    """What became of one calculation, as ``calculation.json`` spells it.

    The four values are the contract's own calculation states. They are lower case because that is
    what the contract carries on the wire for this particular field, unlike every vocabulary
    decision C3 governs.
    """

    FINISHED = "finished"
    INVALID = "invalid"
    REFUSED = "refused"
    FAILED = "failed"


class ExitCode(IntEnum):
    """The process exit code each outcome produces.

    A caller that reads files never needs these; a shell script that only has ``$?`` does, and the
    two must not be able to disagree, which is why :class:`CalculationOutcome` pairs them.
    """

    FINISHED = 0
    INVALID = 2
    REFUSED = 3
    FAILED = 4


@dataclass(frozen=True)
class CalculationOutcome:
    """One outcome: its status, its exit code, and the errors that belong to it.

    Args:
        status: What became of the calculation.
        exit_code: The process exit code that says the same thing.
        errors: The structured errors, already JSON-ready; empty for a finished calculation.
        traceback_text: The Python traceback, for a crash alone.
    """

    status: CalculationStatus
    exit_code: ExitCode
    errors: Tuple[Dict[str, Any], ...] = ()
    traceback_text: Optional[str] = None

    @classmethod
    def finished(cls) -> "CalculationOutcome":
        """Return the outcome of a calculation that ran to the end."""
        return cls(status=CalculationStatus.FINISHED, exit_code=ExitCode.FINISHED)


class ErrorGroup(str, Enum):
    """Which kind of thing one entry of ``errors.json`` is, for a caller routing it.

    A validation error goes back to whoever built the request, a refusal goes back to whoever
    chose the measures, and a crash goes to whoever runs the service. That is three different
    inboxes, which is why the distinction is a field and not a matter of reading the message.
    """

    VALIDATION = "validation"
    REFUSAL = "refusal"
    CRASH = "crash"


class InputFiles:
    """What a calculation reads out of its input directory, and what each file has to be.

    The three are separate files rather than one because they have three different owners and
    three different lifetimes: the dwelling is surveyed once, the package is what the user is
    trying out, and the parameters are how the service chose to run today.
    """

    #: The ``HomeInventoryInput`` document describing the dwelling.
    INVENTORY: ClassVar[str] = "home_inventory.json"

    #: The renovation package, ``{"measures": [{"measure_id": …, "options": {…}}, …]}``.
    PACKAGE: ClassVar[str] = "package.json"

    #: The key the package's measure list sits under (decision Q1).
    MEASURES_KEY: ClassVar[str] = "measures"

    #: The simulation-parameters file, in either spelling the executor reads. The first of these
    #: that exists is the one used, so a caller may ship whichever it has.
    PARAMETER_NAMES: ClassVar[Tuple[str, ...]] = ("simulation.yaml", "simulation.yml", "simulation.json")

    @classmethod
    def parameters_path(cls, directory: Path) -> Path:
        """Return the simulation-parameters file of an input directory.

        Args:
            directory: The input directory.

        Returns:
            The first of :attr:`PARAMETER_NAMES` that exists.

        Raises:
            ValidationError: ``SCHEMA_VIOLATION`` when none of them does, naming all three.
        """
        for name in cls.PARAMETER_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
        raise ValidationError(
            ReasonCode.SCHEMA_VIOLATION,
            str(directory),
            f"the input directory holds none of {', '.join(cls.PARAMETER_NAMES)}",
        )

    @classmethod
    def read_json(cls, path: Path) -> Any:
        """Read one JSON input file.

        Args:
            path: Where it should be.

        Returns:
            The parsed document.

        Raises:
            ValidationError: ``SCHEMA_VIOLATION`` when the file is missing or is not JSON.
        """
        if not path.is_file():
            raise ValidationError(ReasonCode.SCHEMA_VIOLATION, str(path), "the file does not exist")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValidationError(
                ReasonCode.SCHEMA_VIOLATION, str(path), f"the file is not valid JSON: {error}"
            ) from error

    @classmethod
    def measures(cls, package: Any, path: Path) -> Sequence[Any]:
        """Return the measure list of a package document.

        Args:
            package: The parsed ``package.json``.
            path: Its path, for the error message.

        Returns:
            The measure list, empty when the package carries none.

        Raises:
            ValidationError: ``SCHEMA_VIOLATION`` when the document is not an object or its
                ``measures`` key does not hold a list.
        """
        if not isinstance(package, dict):
            raise ValidationError(
                ReasonCode.SCHEMA_VIOLATION,
                str(path),
                f"a package is an object with a '{cls.MEASURES_KEY}' list",
            )
        measures = package.get(cls.MEASURES_KEY, [])
        if not isinstance(measures, list):
            raise ValidationError(
                ReasonCode.SCHEMA_VIOLATION,
                f"package.{cls.MEASURES_KEY}",
                f"'{cls.MEASURES_KEY}' holds a list of measures, not a {type(measures).__name__}",
            )
        return measures


class OutputFiles:
    """What a calculation writes into its output directory, and nowhere else.

    The names are fixed rather than derived, because a caller that has to guess a filename cannot
    be a shell script, and requirement R13's whole point is that it can be.
    """

    #: The energy-system file that ran, written before the run so that a crashed run still leaves
    #: behind what it was running.
    PARAMETRISED: ClassVar[str] = "parametrised.energy_system.yaml"

    #: The per-field and per-measure account of the translation (requirement R7).
    TRANSLATION_REPORT: ClassVar[str] = "translation_report.json"

    #: The outcome, always written.
    CALCULATION: ClassVar[str] = "calculation.json"

    #: The structured failure, written on every outcome that is not ``finished``.
    ERRORS: ClassVar[str] = "errors.json"

    #: Where the simulation writes its own output.
    RESULTS_DIRECTORY: ClassVar[str] = "results"

    #: The three files ``write_records`` produces (requirement R16), named here so that the
    #: outcome can list them without importing the writer.
    RECORDS: ClassVar[Tuple[str, ...]] = (
        "realized.energy_system.yaml",
        "realized.audit.yaml",
        "component_connections.json",
    )

    @classmethod
    def expected(cls) -> Tuple[str, ...]:
        """Return every artifact a finished calculation produces, in a fixed order."""
        return (cls.PARAMETRISED,) + cls.RECORDS + (cls.TRANSLATION_REPORT, cls.CALCULATION)


class SimulationRunner(Protocol):
    """How a parametrised file becomes a finished simulation.

    It is a protocol with one method so that a test can inject a runner that crashes, or one that
    does nothing, without the calculation having to know the difference. Everything about the run
    that is a decision -- where the records go, where the results go -- is decided by the caller
    and handed in, so the runner itself decides nothing.
    """

    def run(
        self,
        energy_system_path: Path,
        parameters_path: Path,
        parameters: SimulationParameters,
        record_directory: Path,
    ) -> None:
        """Build the system, write its records, and run every timestep."""


class EnergySystemSimulationRunner:
    """The real runner: build, record, simulate, in that order.

    The order is requirement R16's: the realized record and its audit are written *before* the
    first timestep, so that a run which dies halfway still leaves a complete description of the
    system it was running behind -- which is when such a description is worth the most.
    """

    def run(
        self,
        energy_system_path: Path,
        parameters_path: Path,
        parameters: SimulationParameters,
        record_directory: Path,
    ) -> None:
        """Run one parametrised energy-system file.

        Args:
            energy_system_path: The file that was parametrised.
            parameters_path: The parameters file, which the record names so that the pair that
                reproduces the run is written down.
            parameters: The parameters themselves, their result directory already set.
            record_directory: Where the three records go; the output directory, not the results
                directory, so that a caller finds them beside the file that produced them.

        Raises:
            EnergySystemError: For any condition of the energy-system error catalogue.
        """
        built = build_energy_system(
            energy_system_path, parameters, simulation_parameters_path=str(parameters_path)
        )
        write_records(built, str(record_directory))
        built.simulator.run_all_timesteps()


class TranslationReportDocument:
    """The translation report as it is written to disk (requirement R7).

    It carries two things beside the report lines: which rules produced it -- the translator's own
    version and the contract revision every field name came from -- and which image ran it, so
    that a stored result can be traced back to the code that made it without anything having to be
    remembered elsewhere (decision Q26, requirement R14).
    """

    #: The key the per-field lines sit under.
    FIELDS_KEY: ClassVar[str] = "fields"

    #: The key the per-measure lines sit under.
    MEASURES_KEY: ClassVar[str] = "measures"

    #: The report path prefix that marks a line as being about a measure rather than a field.
    MEASURE_PATH_PREFIX: ClassVar[str] = "package.measures["

    @classmethod
    def build(
        cls, result: ApplicationResult, parametrised: ParametrisedSystem, image_digest: Optional[str]
    ) -> Dict[str, Any]:
        """Return the report document.

        Args:
            result: What applying the package produced; its report is the source of the lines.
            parametrised: The parametrised system, for the base file it came from.
            image_digest: The container image digest the caller passed in, or ``None``.

        Returns:
            The JSON-ready document.
        """
        lines = result.report.to_list()
        return {
            "translator_version": TRANSLATOR_VERSION,
            "contract": cls.contract(),
            "image_digest": image_digest,
            "base_file": parametrised.base_file_name,
            cls.FIELDS_KEY: [
                line for line in lines if not line["path"].startswith(cls.MEASURE_PATH_PREFIX)
            ],
            cls.MEASURES_KEY: [
                line for line in lines if line["path"].startswith(cls.MEASURE_PATH_PREFIX)
            ],
        }

    @classmethod
    def contract(cls) -> Dict[str, Any]:
        """Return which contract revision the vendored copy holds, with a hash per file.

        Returns:
            ``{"commit": …, "files": {"openapi.yaml": {"sha256": …}, …}}``. The commit is the one
            ``openapi.yaml`` was taken from, because that is the file the inventory schema lives
            in; the other two carry their own hashes beside it.
        """
        pinned = ContractFiles.pinned()["files"]
        return {
            "commit": str(pinned[ContractFiles.OPENAPI_FILENAME]["commit"]),
            "files": {
                name: {"commit": str(entry["commit"]), "sha256": str(entry["sha256"])}
                for name, entry in sorted(pinned.items())
            },
        }


class CalculationRunner:
    """Runs one calculation from an input directory into an output directory.

    Everything the calculation needs is either in the input directory or is one of the two
    optional locations a container maps in, so the class takes no configuration beyond those.
    :meth:`run` never raises: every way a calculation can end is a file in the output directory
    and an exit code, which is what makes the caller a shell script rather than a Python program.

    Args:
        input_directory: Holds the inventory, the package and the simulation parameters.
        output_directory: Everything the calculation writes goes here; it is created if missing.
        cache_directory: Where the occupancy and weather caches live. The simulation parameters'
            own default when omitted. It is the one location outside the output directory a
            calculation writes to, and it is shared state rather than output (decision Q25).
        base_files_directory: Where the recorded energy-system files live; the repository's own
            ``energy_systems/`` when omitted.
        image_digest: The container image digest to echo into the report and the outcome; the
            ``RENOVISOR_IMAGE_DIGEST`` environment variable when omitted, and ``None`` when that is
            unset too (decision Q26).
        simulation_runner: How the parametrised file is run; the real one unless a test injects
            another.
    """

    #: The environment variable the image digest is read from when the caller passes none.
    IMAGE_DIGEST_VARIABLE: ClassVar[str] = "RENOVISOR_IMAGE_DIGEST"

    #: Where the recorded base files live, relative to the repository root.
    DEFAULT_BASE_FILES: ClassVar[str] = "energy_systems"

    #: The repository root, two directories above this package.
    REPOSITORY_ROOT: ClassVar[Path] = Path(__file__).resolve().parents[2]

    def __init__(
        self,
        input_directory: Path,
        output_directory: Path,
        cache_directory: Optional[Path] = None,
        base_files_directory: Optional[Path] = None,
        image_digest: Optional[str] = None,
        simulation_runner: Optional[SimulationRunner] = None,
    ) -> None:
        """Store the locations; nothing is read and nothing is created until :meth:`run`."""
        self._input = Path(input_directory)
        self._output = Path(output_directory)
        self._cache = Path(cache_directory) if cache_directory is not None else None
        self._base_files = (
            Path(base_files_directory)
            if base_files_directory is not None
            else self.REPOSITORY_ROOT / self.DEFAULT_BASE_FILES
        )
        self._image_digest = (
            image_digest if image_digest is not None else os.environ.get(self.IMAGE_DIGEST_VARIABLE)
        )
        self._runner: SimulationRunner = simulation_runner or EnergySystemSimulationRunner()
        self._written: List[str] = []

    def run(self) -> ExitCode:
        """Run the calculation and write everything it produced.

        Returns:
            The exit code of the outcome. Nothing propagates out of here: a crash in the
            simulation is a ``failed`` outcome with a traceback in ``errors.json``, and a crash in
            the writing of that outcome is the only thing that could still escape, which is why
            the writing itself touches nothing that can fail on the happy path.
        """
        self._output.mkdir(parents=True, exist_ok=True)
        try:
            self._calculate()
            outcome = CalculationOutcome.finished()
        except ValidationError as error:
            outcome = CalculationOutcome(
                status=CalculationStatus.INVALID,
                exit_code=ExitCode.INVALID,
                errors=(dict(error.to_dict(), group=ErrorGroup.VALIDATION.value),),
            )
        except RefusalError as error:
            outcome = CalculationOutcome(
                status=CalculationStatus.REFUSED,
                exit_code=ExitCode.REFUSED,
                errors=tuple(
                    dict(item, group=ErrorGroup.REFUSAL.value) for item in error.to_list()
                ),
            )
        except BaseException as error:  # pylint: disable=broad-except  # R13.2.1: no silent death
            outcome = CalculationOutcome(
                status=CalculationStatus.FAILED,
                exit_code=ExitCode.FAILED,
                errors=(
                    {
                        "reason": ReasonCode.SIMULATION_FAILED.value,
                        "description": ReasonCode.SIMULATION_FAILED.describe(),
                        "group": ErrorGroup.CRASH.value,
                        "path": str(self._input),
                        "detail": f"{type(error).__name__}: {error}",
                    },
                ),
                traceback_text="".join(
                    traceback.format_exception(type(error), error, error.__traceback__)
                ),
            )
        self._write_outcome(outcome)
        return outcome.exit_code

    def _calculate(self) -> None:
        """Do the work: read, apply, parametrise, record, simulate.

        Raises:
            ValidationError: When the request is malformed.
            RefusalError: When it is well formed and cannot be simulated.
            Exception: Whatever the simulation raises, which :meth:`run` turns into ``failed``.
        """
        inventory_document = InputFiles.read_json(self._input / InputFiles.INVENTORY)
        package_path = self._input / InputFiles.PACKAGE
        package = InputFiles.measures(InputFiles.read_json(package_path), package_path)
        parameters_path = InputFiles.parameters_path(self._input)

        inventory = Inventory.from_dict(inventory_document)
        inventory.validate()
        application = PackageApplication(Catalogue.load(), MeasureRegistry(), InsulationMaterials.load())
        result = application.apply(inventory, package)

        parameters = self._parameters(parameters_path)
        estimator = PreRunDemandEstimator(
            inventory=result.inventory,
            base_file_key=result.base_file_key,
            household_match=HouseholdMatcher().match(result.inventory),
            simulation_parameters=parameters,
            cache_directory=self._cache,
        )
        parametrised = Parametriser(self._base_files).parametrise(result, estimator)

        energy_system_path = self._output / OutputFiles.PARAMETRISED
        energy_system_path.write_text(parametrised.yaml_text, encoding="utf-8")
        self._written.append(OutputFiles.PARAMETRISED)
        self._write_json(
            OutputFiles.TRANSLATION_REPORT,
            TranslationReportDocument.build(result, parametrised, self._image_digest),
        )
        self._runner.run(energy_system_path, parameters_path, parameters, self._output)
        self._written.extend(OutputFiles.RECORDS)
        self._written.append(f"{OutputFiles.RESULTS_DIRECTORY}/")

    def _parameters(self, parameters_path: Path) -> SimulationParameters:
        """Read the simulation parameters and point everything they decide at this calculation.

        The result-path singleton is reset first: it remembers the last run configured in this
        process, and a container that serves several calculations in a row must not let the first
        one decide where the second writes (requirement R13.1).

        Args:
            parameters_path: The parameters file of the input directory.

        Returns:
            The parameters, with the result directory inside the output directory and the cache
            directory the caller mapped in.

        Raises:
            EnergySystemFormatError: When the parameters file cannot be read.
        """
        ResultPathProviderSingleton.reset()
        parameters = SimulationParametersReader.read(parameters_path)
        parameters.result_directory = str(self._output / OutputFiles.RESULTS_DIRECTORY)
        os.makedirs(parameters.result_directory, exist_ok=True)
        if self._cache is not None:
            self._cache.mkdir(parents=True, exist_ok=True)
            parameters.cache_dir_path = str(self._cache)
        return parameters

    def _write_outcome(self, outcome: CalculationOutcome) -> None:
        """Write ``calculation.json`` and, for every unsuccessful outcome, ``errors.json``.

        Args:
            outcome: What became of the calculation.
        """
        if outcome.status is not CalculationStatus.FINISHED:
            errors: Dict[str, Any] = {
                "status": outcome.status.value,
                "errors": list(outcome.errors),
            }
            if outcome.traceback_text is not None:
                errors["traceback"] = outcome.traceback_text
            self._write_json(OutputFiles.ERRORS, errors)
        self._write_json(
            OutputFiles.CALCULATION,
            {
                "status": outcome.status.value,
                "translator_version": TRANSLATOR_VERSION,
                "image_digest": self._image_digest,
                "output_files": sorted(set(self._written) | {OutputFiles.CALCULATION}),
            },
        )

    def _write_json(self, name: str, document: Any) -> None:
        """Write one JSON artifact into the output directory and record that it exists.

        Formatting is fixed rather than compact so that a person can read the outcome and the
        report without a tool, and so that two runs of the same request produce the same bytes
        (requirement R10).

        Args:
            name: The artifact's fixed file name.
            document: The JSON-ready document.
        """
        path = self._output / name
        path.write_text(
            json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        if name not in self._written:
            self._written.append(name)


def calculate(
    input_directory: Path,
    output_directory: Path,
    cache_directory: Optional[Path] = None,
    base_files_directory: Optional[Path] = None,
) -> ExitCode:
    """Run one calculation with the committed tables, in one call.

    The shorthand the command line uses, and the one a test reaches for when it does not need to
    inject anything.

    Args:
        input_directory: Holds the inventory, the package and the simulation parameters.
        output_directory: Everything the calculation writes goes here.
        cache_directory: Where the occupancy and weather caches live, when a container maps one in.
        base_files_directory: Where the recorded energy-system files live.

    Returns:
        The exit code of the outcome.
    """
    return CalculationRunner(
        input_directory=input_directory,
        output_directory=output_directory,
        cache_directory=cache_directory,
        base_files_directory=base_files_directory,
    ).run()
