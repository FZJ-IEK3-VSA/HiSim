"""One calculation request in, one directory of results out, and one exit code.

``run`` is what the backend's worker calls. It reads a request, validates it, applies the
package, translates the renovated house into an energy-system file, runs that file in process
and assembles the result payload beside it. Every way the calculation can end is a file in the
output directory and an exit code, which is what lets the caller be a shell script rather than
a Python program::

    python -m hisim.renovisor run request.yaml --out jobs/abc --period one_day_15min

| Exit | Meaning                                                | Written                          |
| ---- | ------------------------------------------------------ | -------------------------------- |
| 0    | success                                                 | everything in :class:`Outputs`   |
| 2    | the request is not a valid request                      | ``problems.json`` and nothing else |
| 3    | the translator could not map something nobody listed    | ``translator_error.json``        |
| 5    | HiSim refused the file, or the simulation raised        | whatever HiSim wrote             |

The last line on standard error for exit 3 and 5 is one line, because the backend shows it as
the job's error message.

Nothing is written outside ``--out`` but the cache directory a container maps in, which is
shared state rather than output (decision Q25) and is the one documented exception.
"""

import json
import os
import sys
import traceback
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Protocol, Tuple

import yaml

from hisim.energy_system.executor import build_energy_system, write_records
from hisim.renovisor import TRANSLATOR_VERSION
from hisim.renovisor.apply import apply
from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.report import ReportError
from hisim.renovisor.request import Request, RequestError
from hisim.renovisor.result import ResultBuilder
from hisim.renovisor.simulation import EconomicSetup, Period, SimulationSetup
from hisim.renovisor.translate import TranslatedSystem, Translator
from hisim.renovisor.whitelist import TranslatorError, Whitelist
from hisim.simulationparameters import SimulationParameters
from hisim.result_path_provider import ResultPathProviderSingleton


class ExitCode(IntEnum):
    """What a calculation's exit code means, as §2.1 of the frontend side's specification fixes it.

    ``FINISHED`` is a run that produced every output. ``REQUEST_INVALID`` is a request that is
    not a request, with ``problems.json`` naming every fault. ``TRANSLATOR_ERROR`` is a bug in
    this package, which a released translator cannot reach because T-NIY runs the whole probe
    set. ``SIMULATION_ERROR`` is HiSim refusing the file or the simulation raising.
    """

    FINISHED = 0
    REQUEST_INVALID = 2
    TRANSLATOR_ERROR = 3
    SIMULATION_ERROR = 5


class Outputs:
    """The names of everything a calculation writes into its output directory."""

    #: Written by ``translate``.
    MAPPING_REPORT: ClassVar[str] = "mapping_report.json"

    #: Written by HiSim's ``write_records``, before the first timestep.
    RECORDS: ClassVar[Tuple[str, ...]] = (
        "realized.energy_system.yaml",
        "realized.audit.yaml",
        "component_connections.json",
        "realized.simulation.yaml",
    )

    #: Written after the simulation.
    RESULT: ClassVar[str] = "result.json"

    #: The success manifest: which options were used and which image ran.
    CALCULATION: ClassVar[str] = "calculation.json"

    #: The two failure documents, one per exit code that has one.
    PROBLEMS: ClassVar[str] = "problems.json"
    TRANSLATOR_ERROR: ClassVar[str] = "translator_error.json"

    #: The subdirectory the simulation's own outputs go into.
    RESULTS_DIRECTORY: ClassVar[str] = "results"


class RequestFile:
    """Reads one request from a JSON or a YAML file.

    YAML is a superset of JSON, so one reader serves both and the extension only decides the
    error message. The mockup is YAML with comments; the backend sends JSON.
    """

    @classmethod
    def read(cls, path: Path) -> Any:
        """Return the parsed request document.

        Args:
            path: The request file.

        Returns:
            The document as nested dictionaries and lists.

        Raises:
            RequestError: When the file does not exist or does not parse, reported as one
                problem so that the caller sees the same shape of answer as for a bad field.
        """
        from hisim.renovisor.request import Problem, ProblemCode

        try:
            with path.open(encoding="utf-8") as handle:
                return yaml.safe_load(handle)
        except (OSError, yaml.YAMLError) as error:
            raise RequestError(
                [
                    Problem(
                        path=str(path),
                        code=ProblemCode.TYPE_INVALID,
                        message=f"the request file cannot be read as JSON or YAML: {error}",
                    )
                ],
                structural=True,
            ) from error


class SimulationRunner(Protocol):
    """How a translated file becomes a finished simulation.

    A protocol with one method so that a test can inject a runner that crashes, or one that does
    nothing, without the calculation having to know the difference.
    """

    def run(
        self,
        energy_system_path: Path,
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
        parameters: SimulationParameters,
        record_directory: Path,
    ) -> None:
        """Run one translated energy-system file.

        Args:
            energy_system_path: The file the translator wrote.
            parameters: The parameters, their result directory already set.
            record_directory: Where the three records go; the output directory, so that a
                caller finds them beside the file that produced them.

        Raises:
            EnergySystemError: For any condition of the energy-system error catalogue.
        """
        built = build_energy_system(energy_system_path, parameters)
        write_records(built, str(record_directory))
        built.simulator.run_all_timesteps()


@dataclass(frozen=True)
class Outcome:
    """How one calculation ended.

    Args:
        exit_code: What the process returns.
        document: The failure document to write, or ``None`` for a success.
        document_name: Which file it is written to.
        message: The one line printed on standard error for exit 3 and 5.
    """

    exit_code: ExitCode
    document: Optional[Dict[str, Any]] = None
    document_name: Optional[str] = None
    message: Optional[str] = None


class Calculation:
    """Runs one request from a file into an output directory.

    :meth:`run` never raises: every way a calculation can end is a file and an exit code.
    :meth:`translate_only` stops after the file and the report, which is what the ``translate``
    command and every probe of the capability document use.

    Args:
        request_path: The request file, JSON or YAML.
        output_directory: Everything the calculation writes goes here; it is created if missing.
        period: How long the run covers.
        cache_directory: Where the occupancy and weather caches live, when a container maps one
            in. The simulation parameters' own default when omitted.
        base_files_directory: Where the recorded twins live; the repository's ``energy_systems/``
            when omitted.
        image_digest: The container image digest echoed into the manifest and the payload; the
            ``RENOVISOR_IMAGE_DIGEST`` environment variable when omitted (decision Q26).
        simulation_runner: How the translated file is run; the real one unless a test injects
            another.
        subsidy_catalogue_directory: Where the country subsidy catalogues live; the shipped
            directory when omitted.
    """

    #: The environment variable the image digest is read from when the caller passes none.
    IMAGE_DIGEST_VARIABLE: ClassVar[str] = "RENOVISOR_IMAGE_DIGEST"

    #: Where the recorded base files live, relative to the repository root.
    DEFAULT_BASE_FILES: ClassVar[str] = "energy_systems"

    #: The repository root, two directories above this package.
    REPOSITORY_ROOT: ClassVar[Path] = Path(__file__).resolve().parents[2]

    def __init__(
        self,
        request_path: Path,
        output_directory: Path,
        period: Period = Period.FULL_YEAR,
        cache_directory: Optional[Path] = None,
        base_files_directory: Optional[Path] = None,
        image_digest: Optional[str] = None,
        simulation_runner: Optional[SimulationRunner] = None,
        subsidy_catalogue_directory: Optional[Path] = None,
    ) -> None:
        """Store the locations; nothing is read and nothing is created until :meth:`run`."""
        self._request_path = Path(request_path)
        self._output = Path(output_directory)
        self._period = period
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
        self._catalogue_directory = (
            Path(subsidy_catalogue_directory) if subsidy_catalogue_directory is not None else None
        )
        self._written: List[str] = []

    def translate_only(self) -> ExitCode:
        """Validate, apply and translate, writing the file and the report but running nothing."""
        return self._guarded(self._translate_and_write)

    def run(self) -> ExitCode:
        """Do everything: validate, apply, translate, simulate and assemble the payload."""
        return self._guarded(self._calculate)

    def validate(self) -> ExitCode:
        """Validate only, printing the problems as JSON on standard output.

        Returns:
            ``0`` when the request is valid, ``2`` when it is not. Nothing is written to disk:
            ``validate`` is the command a person runs on their own machine, and the ``run``
            command validates again as its first step, so the two cannot disagree.
        """
        try:
            Request.parse(RequestFile.read(self._request_path))
        except RequestError as error:
            print(json.dumps(error.to_json(), indent=2, ensure_ascii=False))
            return ExitCode.REQUEST_INVALID
        print(json.dumps({"problems": []}, indent=2))
        return ExitCode.FINISHED

    def _guarded(self, work: Any) -> ExitCode:
        """Run one stage, turning every way it can fail into a file and an exit code."""
        self._output.mkdir(parents=True, exist_ok=True)
        try:
            work()
        except RequestError as error:
            self._write_json(Outputs.PROBLEMS, error.to_json())
            return ExitCode.REQUEST_INVALID
        except (TranslatorError, ReportError) as error:
            outcome = self._translator_error(error)
            self._write_json(Outputs.TRANSLATOR_ERROR, outcome)
            print(outcome["message"], file=sys.stderr)
            return ExitCode.TRANSLATOR_ERROR
        except BaseException as error:  # pylint: disable=broad-except  # no silent death
            print(self._last_line(error), file=sys.stderr)
            return ExitCode.SIMULATION_ERROR
        return ExitCode.FINISHED

    @classmethod
    def _translator_error(cls, error: BaseException) -> Dict[str, str]:
        """Return the ``translator_error.json`` document of one translator error."""
        if isinstance(error, TranslatorError):
            return error.to_json()
        return {"message": str(error), "detail": type(error).__name__}

    @classmethod
    def _last_line(cls, error: BaseException) -> str:
        """Return the one line standard error carries for a failed simulation."""
        formatted = traceback.format_exception(type(error), error, error.__traceback__)
        return formatted[-1].strip() if formatted else f"{type(error).__name__}: {error}"

    def _translate(self) -> Tuple[Request, Any, TranslatedSystem]:
        """Validate, apply and translate, without writing anything."""
        document = RequestFile.read(self._request_path)
        request = Request.parse(document)
        whitelist = Whitelist.load()
        applied = apply(request.document["house"], request.measures, whitelist)
        translated = Translator(self._base_files, whitelist).translate(request, applied)
        return request, applied, translated

    def _translate_and_write(self) -> Tuple[Request, Any, TranslatedSystem]:
        """Translate and write the energy-system file and the mapping report."""
        request, applied, translated = self._translate()
        path = self._output / translated.file_name
        path.write_text(translated.yaml_text, encoding="utf-8")
        self._written.append(translated.file_name)
        self._write_json(Outputs.MAPPING_REPORT, translated.report.to_json())
        return request, applied, translated

    def _calculate(self) -> None:
        """Translate, run and assemble, in that order."""
        ResultPathProviderSingleton.reset()
        request, applied, translated = self._translate_and_write()
        parameters = SimulationSetup.parameters(
            self._period, self._output, self._cache, country=request.country.value
        )
        catalogue = EconomicSetup.attach(parameters, request.country.value, self._catalogue_directory)
        # What the engine cannot learn from the simulation: what was already in the building, what
        # the envelope measures cost and who is applying for support (step 10 §4). Attached beside
        # the economic parameters and before the run, because the postprocessing bridge merges it
        # into `economic_inputs.json` as the simulation finishes.
        if translated.economic_context is not None:
            parameters.set_economic_context(translated.economic_context)
        energy_system_path = self._output / translated.file_name
        self._runner.run(energy_system_path, parameters, self._output)
        self._written.extend(Outputs.RECORDS)
        self._written.append(f"{Outputs.RESULTS_DIRECTORY}/")
        document = ResultBuilder(
            request=request,
            applied=applied,
            translated=translated,
            output_directory=self._output,
            simulation_parameters=parameters,
            image_digest=self._image_digest,
            subsidy_catalogue_path=catalogue,
        ).build(self._contract())
        self._write_json(Outputs.RESULT, document)
        self._write_json(
            Outputs.CALCULATION,
            {
                "status": "finished",
                "translator_version": TRANSLATOR_VERSION,
                "image_digest": self._image_digest,
                "period": self._period.value,
                "options_added": SimulationSetup.option_names(),
                # The cache directories the run read and wrote, in priority order (hisim-epc.22).
                # The realized parameter record deliberately omits them -- it describes the run,
                # not the machine -- so this manifest is the one place a result directory says
                # where its cache entries came from.
                "cache_directories": list(parameters.cache_locations().directories),
                "output_files": sorted(set(self._written) | {Outputs.CALCULATION}),
            },
        )

    @classmethod
    def _contract(cls) -> Dict[str, Any]:
        """Return the contract revision block, so a stored result names the vocabulary it speaks."""
        pin = ContractFiles.pinned()
        files = pin.get("files", {})
        measures = files.get(ContractFiles.MEASURES_FILENAME, {})
        return {
            "commit": measures.get("commit"),
            "files": {
                name: entry.get("sha256")
                for name, entry in sorted(files.items())
            },
        }

    def _write_json(self, name: str, document: Any) -> None:
        """Write one JSON artifact into the output directory and record that it exists.

        Formatting is fixed rather than compact so that a person can read the report without a
        tool, and so that two runs of the same request produce the same bytes.
        """
        path = self._output / name
        path.write_text(
            json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        if name not in self._written:
            self._written.append(name)
