"""The `staged` subcommand of `python -m hisim.economics`: price a multi-year renovation plan out of finished jobs.

Usage::

    python -m hisim.economics staged --stage <dir>:<from_year>:<label>[:<job_id>] ... [--parameters F]
        [--perspective ID] [--subsidy-catalog DIR] --out <dir>/economics_result.json

Each `--stage` directory holds one finished job: its stored inputs (`economic_inputs.json`, in the directory or its
`results/` subdirectory) and its mapping report (`mapping_report.json`, in the directory or its parent), whose
`economics_stage` record (`staged_record.EconomicsStageRecord`) says which measures the stage carries out and which
cost subjects they created. `StagedCli` reads them, prices the plan with `staged.StagedEvaluator` and writes
`economics_result.json` (`staged_document.StagedDocument`) and its provenance ledger. `__main__` dispatches here.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple

from hisim.calculation_progress import Phase, ProgressWriter
from hisim.economics.calculators.energy import StatedPriceError
from hisim.economics.database import CostDatabase, CostDataError
from hisim.economics.evaluator import UnresolvableSubjectsError
from hisim.economics.exports import ExportFileNames, write_provenance_ledgers
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import Perspective, load_default_bundle
from hisim.economics.serialization import (
    read_inputs,
    read_stored_country,
    read_stored_parameters,
    read_stored_price_basis_year,
)
from hisim.economics.staged import (
    InvestmentOverride,
    Stage,
    StagedEngineError,
    StagedEvaluationError,
    StagedEvaluator,
)
from hisim.economics.staged_document import (
    BandOrderError,
    MeasureWithoutRowError,
    StagedDocument,
    SubsidyReconciliationError,
)
from hisim.economics.staged_parameters import (
    ParameterKeys,
    ParameterProblem,
    ParameterProblemCodes,
    StagedParameters,
)
from hisim.economics.staged_record import (
    EconomicsStageRecord,
    MainSubjectError,
    MainSubjectRule,
    StageCatalogue,
    StageRecordError,
)
from hisim.economics.subsidies import SubsidyCatalog
from hisim.loadtypes import ComponentType


@dataclass(frozen=True)
class StageMapping:
    """What the stages' economics stage records say about the cost subjects, merged over the plan.

    Attributes:
        measure_ids: Cost subject -> the catalogue measure that created it.
        unpriced: The subjects with no price behind them.
        costless: The subjects of a measure that costs nothing to carry out.
        notes: Subject -> the sentence its ``by_subject`` row carries as ``note``.
        catalogue: The translator's measure tables, which every stage states alike.
        replaces: Measure subject -> the reference subjects it replaces, which its ``by_subject``
            row carries as ``replaces_subjects``.
    """

    measure_ids: Dict[str, Optional[str]]
    unpriced: List[str]
    costless: List[str]
    notes: Dict[str, str]
    catalogue: StageCatalogue
    replaces: Dict[str, List[str]] = field(default_factory=dict)


class StagedCli:
    """Everything the `staged` subcommand decides, in one place.

    `staged` prices a renovation plan spread over several years out of the stored `economic_inputs.json` of the jobs
    that simulated each state, and writes `economics_result.json`. It runs no simulation and finishes in under a
    second, so a backend can call it synchronously.

    Exit codes: 0 with the document; 2 with a `problems.json` beside `--out` when the plan is refused (missing input, a
    mapping report without a readable economics stage record, years running backwards, stages priced under different
    conditions or translated with different measure tables, unknown perspective, country without data, refused
    parameter key, unreadable `--parameters` file); 3 with one line on stderr when the engine refuses (an unresolvable
    cost subject, a quote whose main subject cannot be determined). A 2 is fixable by sending a different plan; a 3 is
    not. An `--out` without a directory is refused with exit 2 and a message only, since there is nowhere to write.

    The `--parameters` file is the document's own `parameters` block (`staged_parameters.StagedParameters`), not an
    `EconomicParameters` record. The country and the price basis year are always the stages' own; a value in the file
    is only checked against them.

    Example::

        python -m hisim.economics staged \
            --stage jobs/base:0:baseline --stage jobs/pkg:0:"stage 1":job-7 \
            --parameters economics.json --out results/economics_result.json
    """

    #: How a ``--stage`` argument is spelled, for the help text and for the error messages.
    ARGUMENT_FORM: ClassVar[str] = "<directory>:<from_year>:<label>[:<job_id>]"

    #: The separator between a stage argument's fields. A label containing one is not supported;
    #: the fourth field absorbs nothing, so ``a:0:my:label`` reads ``my`` as the label and
    #: ``label`` as the job id.
    SEPARATOR: ClassVar[str] = ":"

    #: How many fields a stage argument has at least, and at most.
    MINIMUM_FIELDS: ClassVar[int] = 3
    MAXIMUM_FIELDS: ClassVar[int] = 4

    #: The perspective a RenoVisor plan is priced under unless the caller names another: existing
    #: assets in the register, subsidies applied where a catalogue says so, cash financing. The
    #: id `brownfield_owner_subsidized_cash` is an alias of it and not in the shipped bundle.
    #: Defined beside the parameter block that may also name it, so both default alike.
    DEFAULT_PERSPECTIVE: ClassVar[str] = StagedParameters.DEFAULT_PERSPECTIVE_ID

    #: What the problems document is called, beside the requested output.
    PROBLEMS_FILE_NAME: ClassVar[str] = "problems.json"

    #: Exit code for a plan the evaluator refuses.
    PLAN_REFUSED: ClassVar[int] = 2

    @staticmethod
    def out_directory(out: str) -> Optional[str]:
        """Return the absolute directory `--out` names, or None for a bare file name.

        The document, its ledger and a refusal's `problems.json` are written there. A bare file name would mean the
        working directory, which is not a result directory, so it names none.
        """
        if not os.path.dirname(str(out)):
            return None
        return os.path.dirname(os.path.abspath(str(out)))

    #: What standard error says for an ``--out`` without a directory.
    BARE_OUT_MESSAGE: ClassVar[str] = (
        "--out {out!r} names no directory; give the document a directory of its own, e.g. "
        "--out results/{out}, so the result, its ledger and a problems.json have somewhere to go "
        "that is not the working directory."
    )

    #: Exit code for an engine failure — a subject nothing can price, a data file that will not
    #: load. A different code from the plan refusal because the caller cannot fix it by asking a
    #: different question.
    ENGINE_FAILED: ClassVar[int] = 3

    #: The mapping report a stage directory carries; its ``economics_stage`` record
    #: (:class:`EconomicsStageRecord`) is what the command reads from it.
    MAPPING_REPORT_FILE_NAME: ClassVar[str] = "mapping_report.json"

    #: The stored inputs every stage must carry, under the directory or under its results.
    INPUTS_FILE_NAME: ClassVar[str] = "economic_inputs.json"

    #: Where a finished RenoVisor job puts the simulation's own outputs, the stored inputs among
    #: them. A caller names the job directory and the CLI looks one level down, so the argument is
    #: the directory the backend already has rather than a path into it.
    RESULTS_SUBDIRECTORY: ClassVar[str] = "results"

    #: What a stage directory with stored inputs but no mapping report is refused with.
    MISSING_MAPPING_MESSAGE: ClassVar[str] = (
        "--stage #{index} {argument!r}: {directory!r} carries {inputs} but no {report}, in it or "
        "beside it. The report is what says which catalogue measure created which cost subject "
        "and which subjects the request carried no price for; without it every row of the "
        "document would claim a known price, and a measure of unknown cost would be published as "
        "one that costs nothing."
    )

    @classmethod
    def parse_stage(cls, argument: str, index: int) -> Tuple[str, int, str, Optional[str]]:
        """Split one `--stage` argument into its directory, year, label and job id.

        Args:
            argument: The argument as typed.
            index: Its position among the `--stage` flags, for the error message.

        Returns:
            `(directory, from_year, label, job_id)`; job id is None when the argument has three fields.

        Raises:
            StagedEvaluationError: If the argument has the wrong number of fields or its year is not an integer (exit
                2).
        """
        fields = argument.split(cls.SEPARATOR)
        if not cls.MINIMUM_FIELDS <= len(fields) <= cls.MAXIMUM_FIELDS:
            raise StagedEvaluationError(
                f"--stage #{index} {argument!r} is not {cls.ARGUMENT_FORM}: it has "
                f"{len(fields)} colon-separated fields, and a stage has "
                f"{cls.MINIMUM_FIELDS} or {cls.MAXIMUM_FIELDS}."
            )
        directory, raw_year, label = fields[0], fields[1], fields[2]
        try:
            from_year = int(raw_year)
        except ValueError:
            raise StagedEvaluationError(
                f"--stage #{index} {argument!r}: {raw_year!r} is not a year. A stage's second "
                "field is the horizon year it starts in, counted from 0."
            ) from None
        job_id = fields[3] if len(fields) == cls.MAXIMUM_FIELDS else None
        return directory, from_year, label, job_id

    @classmethod
    def read_stage(cls, argument: str, index: int) -> Tuple[Stage, str]:
        """Read one stage's stored inputs from its job directory.

        Args:
            argument: The `--stage` argument.
            index: Its position, for the error messages.

        Returns:
            The stage, and the job directory it was read from (the mapping report and provenance file live there).

        Raises:
            StagedEvaluationError: If neither the directory nor its `results` subdirectory has an
                `economic_inputs.json` (a job that has not finished).
        """
        directory, from_year, label, job_id = cls.parse_stage(argument, index)
        source = cls.inputs_directory(directory)
        if source is None:
            raise StagedEvaluationError(
                f"--stage #{index} {argument!r}: no {cls.INPUTS_FILE_NAME} in {directory!r} or "
                f"its {cls.RESULTS_SUBDIRECTORY!r} subdirectory. Every stage of a plan is a "
                "finished job's output directory."
            )
        try:
            inputs = read_inputs(source)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            raise StagedEvaluationError(
                f"--stage #{index} {argument!r}: {cls.INPUTS_FILE_NAME} in {source!r} does not "
                f"read back ({error})."
            ) from error
        return Stage(
            inputs=inputs,
            from_year=from_year,
            label=label,
            measures=cls.read_stage_measures(directory, index, argument),
            job_id=job_id,
        ), directory

    @classmethod
    def read_stage_measures(cls, directory: str, index: int, argument: str) -> Tuple[str, ...]:
        """Return the catalogue measure ids one stage carries out, from its economics stage record.

        The record lists them in catalogue order, the ones the translation acted on only. A stage without a mapping
        report yields nothing here; `read_mapping` refuses it.

        Args:
            directory: The `--stage` argument's first field.
            index: The stage's position, for the error message.
            argument: The argument as typed, for the error message.

        Returns:
            The measure ids, in catalogue order.

        Raises:
            StagedEvaluationError: If the report exists but does not read back, or carries no readable record.
        """
        record = cls.read_record(directory, index, argument)
        return record.measures if record is not None else ()

    @classmethod
    def read_record(cls, directory: str, index: int, argument: str) -> Optional[EconomicsStageRecord]:
        """Return one stage's economics stage record, read out of its mapping report.

        Args:
            directory: The `--stage` argument's first field.
            index: The stage's position, for the error message.
            argument: The argument as typed, for the error message.

        Returns:
            The record, or None when neither the directory nor its parent has a mapping report.

        Raises:
            StagedEvaluationError: If the report is not valid JSON, or its record is missing, of another schema
                version or malformed (exit 2 with `problems.json`).
        """
        path = cls.mapping_report_path(directory)
        if path is None:
            return None
        try:
            with open(path, encoding="utf-8") as handle:
                report = json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            raise StagedEvaluationError(
                f"--stage #{index} {argument!r}: {cls.MAPPING_REPORT_FILE_NAME} in {directory!r} "
                f"does not read back ({error})."
            ) from error
        try:
            return EconomicsStageRecord.from_report(report)
        except StageRecordError as error:
            raise StagedEvaluationError(
                f"--stage #{index} {argument!r}: {cls.MAPPING_REPORT_FILE_NAME} in {directory!r} cannot be "
                f"priced: {error}"
            ) from error

    @classmethod
    def inputs_directory(cls, directory: str) -> Optional[str]:
        """Return where one stage's stored inputs are: the directory itself, or its `results` subdirectory.

        A RenoVisor job directory keeps the simulation outputs, `economic_inputs.json` among them, in `results/`;
        accepting either lets a backend pass the job directory and a hand-run plan pass a bare inputs directory.

        Args:
            directory: The `--stage` argument's first field.

        Returns:
            The directory holding the inputs, or None when neither candidate does.
        """
        for candidate in (directory, os.path.join(directory, cls.RESULTS_SUBDIRECTORY)):
            if os.path.isfile(os.path.join(candidate, cls.INPUTS_FILE_NAME)):
                return candidate
        return None

    #: Code of the refusal raised when the stages of one plan state different measure tables
    #: (`EconomicsStageRecord.catalogue`): stages translated by different translators.
    STAGE_CATALOGUE_MISMATCH_CODE: ClassVar[str] = "stage.catalogue.mismatch"

    @classmethod
    def read_mapping(cls, directories: List[str], arguments: Optional[List[str]] = None) -> "StageMapping":
        """Return the merged subject-to-measure map, subject flags and measure tables over every stage directory.

        Each stage directory must have the translator's `mapping_report.json`, in itself or its parent, with its
        `economics_stage` record: `subjects` says which catalogue measure created which cost subject,
        `unpriced_subjects` which have no price, `costless_subjects` which stand for a measure that costs nothing,
        `subject_notes` why, and `replaces_subjects` which reference subjects each measure subject replaces. Later
        stages win. A directory without a report is refused: an unpriced subject reaches the engine with an investment
        of zero, and only the flag tells that apart from a real price of nothing.

        Args:
            directories: The stage directories, in stage order.
            arguments: The `--stage` arguments they came from, for the refusal message; the directories when not given.

        Returns:
            The `StageMapping`.

        Raises:
            StagedEvaluationError: Naming the first directory without a report or without a readable record, or the
                first stage whose measure tables differ from the first stage's (exit 2 with `problems.json`).
        """
        spelled = arguments if arguments is not None else directories
        measures: Dict[str, Optional[str]] = {}
        unpriced: List[str] = []
        costless: List[str] = []
        notes: Dict[str, str] = {}
        replaces: Dict[str, List[str]] = {}
        catalogue: Optional[StageCatalogue] = None
        for index, directory in enumerate(directories):
            record = cls.read_record(directory, index, spelled[index])
            if record is None:
                raise StagedEvaluationError(
                    cls.MISSING_MAPPING_MESSAGE.format(
                        index=index,
                        argument=spelled[index],
                        directory=directory,
                        inputs=cls.INPUTS_FILE_NAME,
                        report=cls.MAPPING_REPORT_FILE_NAME,
                    )
                )
            if catalogue is None:
                catalogue = record.catalogue
            elif record.catalogue != catalogue:
                message = (
                    f"stage #{index} ({directory!r}) was translated with other measure tables than stage #0 "
                    f"({directories[0]!r}): their {cls.MAPPING_REPORT_FILE_NAME} records list different catalogue "
                    "measures, costless measures or main-subject rules. One plan is priced under one translator's "
                    "tables; translate every stage with the same image."
                )
                raise StagedEvaluationError(
                    message, [{"path": "stages", "code": cls.STAGE_CATALOGUE_MISMATCH_CODE, "message": message}]
                )
            measures.update(record.subjects)
            for subject in record.unpriced_subjects:
                if subject not in unpriced:
                    unpriced.append(subject)
            for subject in record.costless_subjects:
                if subject not in costless:
                    costless.append(subject)
            notes.update(record.subject_notes)
            replaces.update({subject: list(names) for subject, names in record.replaces_subjects.items()})
        if catalogue is None:
            raise StagedEvaluationError("a plan has at least one stage, and this one has none.")
        return StageMapping(
            measure_ids=measures,
            unpriced=unpriced,
            costless=costless,
            notes=notes,
            catalogue=catalogue,
            replaces=replaces,
        )

    @classmethod
    def mapping_report_path(cls, directory: str) -> Optional[str]:
        """Return the path of one stage's mapping report: in the directory, or in its parent.

        A RenoVisor job writes the report beside its records and the simulation outputs one level down, so naming the
        `results` subdirectory still finds it.

        Args:
            directory: The `--stage` argument's first field.

        Returns:
            The path, or None when neither candidate has one.
        """
        for candidate in (
            os.path.join(directory, cls.MAPPING_REPORT_FILE_NAME),
            os.path.join(os.path.dirname(os.path.abspath(directory)), cls.MAPPING_REPORT_FILE_NAME),
        ):
            if os.path.isfile(candidate):
                return candidate
        return None

    @classmethod
    def perspective(cls, requested: Optional[str]) -> Perspective:
        """Return the shipped-bundle perspective the plan is priced under, by id.

        Args:
            requested: The `--perspective` id, or None for `DEFAULT_PERSPECTIVE`.

        Returns:
            The perspective.

        Raises:
            StagedEvaluationError: If the bundle has no row with that id; the message lists the ids it has.
        """
        wanted = requested or cls.DEFAULT_PERSPECTIVE
        bundle = load_default_bundle()
        for perspective in bundle:
            if perspective.id == wanted:
                return perspective
        raise StagedEvaluationError(
            f"unknown perspective {wanted!r}; the shipped bundle has "
            f"{', '.join(perspective.id for perspective in bundle)}."
        )

    #: Code of the refusal raised when one plan's stages do not agree about the country they were
    #: priced for, within one stage (stored evaluation versus stored inputs) or across stages.
    STAGE_COUNTRY_MISMATCH_CODE: ClassVar[str] = "stage.country.mismatch"

    #: The same, for the price basis year: one plan is one price level.
    STAGE_PRICE_BASIS_YEAR_MISMATCH_CODE: ClassVar[str] = "stage.price_basis_year.mismatch"

    #: How the two facts are named in those refusals, so one message serves both.
    COUNTRY_FACT_NAME: ClassVar[str] = "country"
    PRICE_BASIS_YEAR_FACT_NAME: ClassVar[str] = "price basis year"

    @classmethod
    def stage_fact(
        cls,
        index: int,
        directory: str,
        name: str,
        code: str,
        from_evaluation: Optional[Any],
        from_inputs: Optional[Any],
    ) -> Optional[Any]:
        """Return one stage's value of one fact (country or price basis year) from the two files that may state it.

        A finished job states it in its stored evaluation (`lifecycle_costs.json`) and its stored inputs
        (`economic_inputs.json`); a backend's stage directory may hold only the latter. A directory holding both must
        state the same value in each.

        Args:
            index: The stage's position, for the refusal message.
            directory: The directory the stage's inputs were read from.
            name: What the fact is called in the message, e.g. `"country"`.
            code: The problem code a contradiction is published under.
            from_evaluation: What the stored evaluation says, or None.
            from_inputs: What the stored inputs say, or None.

        Returns:
            The fact, or None when neither file states it.

        Raises:
            StagedEvaluationError: If the two files of one stage state different values.
        """
        if from_evaluation is not None and from_inputs is not None and from_evaluation != from_inputs:
            path = f"stages[{index}]"
            message = (
                f"stage #{index} ({directory!r}): its stored evaluation was priced with {name} "
                f"{from_evaluation!r} and its {cls.INPUTS_FILE_NAME} says {from_inputs!r}; one job "
                f"is one {name}, and which it is cannot be guessed from a directory that says both."
            )
            raise StagedEvaluationError(
                message, [{"path": path, "code": code, "message": message}]
            )
        return from_evaluation if from_evaluation is not None else from_inputs

    @classmethod
    def agreed_across_stages(
        cls, stated: List[Tuple[str, Any]], name: str, code: str
    ) -> Optional[Any]:
        """Return the one value the stages state for a fact, or refuse naming every stage that disagrees.

        Stages priced for different countries or at different price basis years cannot be put on one axis.

        Args:
            stated: `(directory, value)` for every stage that states the fact, in stage order.
            name: What the fact is called in the message.
            code: The problem code a contradiction is published under.

        Returns:
            The value, or None when no stage states it.

        Raises:
            StagedEvaluationError: If two stages state different values.
        """
        if len(set(value for _directory, value in stated)) > 1:
            listed = ", ".join(f"{directory} -> {value}" for directory, value in stated)
            message = (
                f"the stages of this plan disagree about the {name} they were priced with "
                f"({listed}); one plan is one {name}."
            )
            raise StagedEvaluationError(
                message, [{"path": "stages", "code": code, "message": message}]
            )
        return stated[0][1] if stated else None

    @classmethod
    def stored_assumptions(
        cls, directories: List[str]
    ) -> Tuple[Optional[EconomicParameters], Optional[str], Optional[int]]:
        """Return what the stages say the plan is priced under, checking that they agree.

        The stored parameter record (`lifecycle_costs.json`) is the base the `--parameters` file overlays; the country
        and the price basis year are resolved separately from both stored files, because the file may never change
        them.

        Args:
            directories: The stage directories, in stage order.

        Returns:
            `(first stage's stored parameter record or None, the stages' country or None, the stages' price basis year
                or None)`. The record is None when the directories hold no stored evaluation (as a backend writes
                them).

        Raises:
            StagedEvaluationError: If a stage contradicts itself or two stages contradict each other about either fact.
        """
        sources = [cls.inputs_directory(directory) or directory for directory in directories]
        records = [(directory, read_stored_parameters(directory)) for directory in sources]
        countries: List[Tuple[str, Any]] = []
        years: List[Tuple[str, Any]] = []
        for index, (directory, record) in enumerate(records):
            country = cls.stage_fact(
                index,
                directory,
                cls.COUNTRY_FACT_NAME,
                cls.STAGE_COUNTRY_MISMATCH_CODE,
                record.country if record is not None else None,
                read_stored_country(directory),
            )
            if country is not None:
                countries.append((directory, country))
            year = cls.stage_fact(
                index,
                directory,
                cls.PRICE_BASIS_YEAR_FACT_NAME,
                cls.STAGE_PRICE_BASIS_YEAR_MISMATCH_CODE,
                record.price_basis_year if record is not None else None,
                read_stored_price_basis_year(directory),
            )
            if year is not None:
                years.append((directory, year))
        return (
            next((record for _directory, record in records if record is not None), None),
            cls.agreed_across_stages(countries, cls.COUNTRY_FACT_NAME, cls.STAGE_COUNTRY_MISMATCH_CODE),
            cls.agreed_across_stages(
                years, cls.PRICE_BASIS_YEAR_FACT_NAME, cls.STAGE_PRICE_BASIS_YEAR_MISMATCH_CODE
            ),
        )

    @classmethod
    def read_parameters_file(cls, path: str) -> Any:
        """Read the `--parameters` file, refusing a missing or unparsable file like a bad key.

        The refusal is exit 2 with a `problems.json`, the same as for a refused key.

        Args:
            path: The `--parameters` argument.

        Returns:
            The parsed JSON, of any type; `StagedParameters.from_mapping` refuses one that is not an object.

        Raises:
            StagedEvaluationError: If the file is missing or does not parse, carrying one problem row that names it.
        """
        def refuse(message: str) -> StagedEvaluationError:
            """Build the refusal, with the one ``parameters.unreadable`` row it carries."""
            return StagedEvaluationError(
                message,
                [
                    ParameterProblem(
                        path=ParameterKeys.ROOT_PATH,
                        code=ParameterProblemCodes.UNREADABLE.format(path=ParameterKeys.ROOT_PATH),
                        message=message,
                    ).to_json()
                ],
            )

        if not os.path.isfile(path):
            raise refuse(f"--parameters file not found: {path!r}.")
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            raise refuse(f"--parameters file {path!r} does not read back as JSON ({error}).") from error

    #: What the stderr line says when a parameter block is refused; the count is what makes it
    #: worth reading the file the refusal wrote.
    PARAMETERS_REFUSED_MESSAGE: ClassVar[str] = (
        "the economic parameters of this plan were refused: {count} problem(s), each named in "
        "the problems document."
    )

    @classmethod
    def parameters(cls, args: argparse.Namespace, directories: List[str]) -> Tuple[StagedParameters, str]:
        """Return the parsed `--parameters` block and the perspective id the plan is priced under.

        The stages' stored assumptions are the base, the file states what may change
        (`staged_parameters.ParameterKeys`), and `--perspective` is reconciled with the file's `perspective_id`. All
        faults are collected and reported at once.

        Args:
            args: The parsed namespace, for `--parameters` and `--perspective`.
            directories: The stage directories, in stage order.

        Returns:
            `(the parsed parameters, the perspective id)`.

        Raises:
            StagedEvaluationError: If any key is refused (one problem row per key), or the stages disagree about their
                country.
        """
        stored, stored_country, stored_year = cls.stored_assumptions(directories)
        raw: Any = cls.read_parameters_file(args.parameters) if args.parameters else {}
        parsed = StagedParameters.from_mapping(raw, stored, stored_country, stored_year)
        problems: List[ParameterProblem] = list(parsed.problems)
        perspective_id = StagedParameters.reconciled_perspective_id(
            parsed.perspective_id, args.perspective, problems
        )
        if problems:
            raise StagedEvaluationError(
                cls.PARAMETERS_REFUSED_MESSAGE.format(count=len(problems)),
                [problem.to_json() for problem in problems],
            )
        return parsed, perspective_id

    #: What the stderr line says when a quote does not fit the plan's stages.
    QUOTES_REFUSED_MESSAGE: ClassVar[str] = (
        "the reader's quotes of this plan were refused: {count} problem(s), each named in the "
        "problems document."
    )

    @classmethod
    def carried_out_by_stage(cls, stages: List[Stage]) -> List[Tuple[str, ...]]:
        """Return the measures each stage carries out itself: all of stage 0's, and only the new ones later.

        A RenoVisor stage lists every measure of its package, including earlier stages'; only the ones it adds are its
        own and can carry a quote.

        Args:
            stages: The plan, in stage order.

        Returns:
            Per stage, in stage order, the measures it carries out.
        """
        carried: List[Tuple[str, ...]] = []
        for index, stage in enumerate(stages):
            before = set(stages[index - 1].measures) if index > 0 else set()
            carried.append(tuple(measure for measure in stage.measures if measure not in before))
        return carried

    @classmethod
    def investment_overrides(
        cls, parsed: StagedParameters, stages: List[Stage], mapping: "StageMapping"
    ) -> Tuple[InvestmentOverride, ...]:
        """Return the reader's investment quotes, checked against the stages and resolved to the subjects they price.

        Each quote must name a stage of the plan and a catalogue measure that costs something, that the stage carries
        out (`carried_out_by_stage`) and whose main subject the stage pays for rather than carries over; every fault is
        a problem row (exit 2), reported together. The main subject is resolved by the measure's `MainSubjectRule` from
        the stages' measure tables, over the subjects the translator assigned to the measure in that stage.

        Args:
            parsed: The parsed parameter block.
            stages: The plan.
            mapping: The stages' merged subject map and measure tables.

        Returns:
            The resolved quotes, in file order.

        Raises:
            StagedEvaluationError: Carrying one problem row per quote that does not fit the plan.
            MainSubjectError: When a quote's main subject cannot be determined (exit 3).
        """
        quotes = parsed.investment_overrides
        if not quotes:
            return ()
        carried = cls.carried_out_by_stage(stages)
        catalogue = mapping.catalogue
        problems = StagedParameters.check_quotes(
            quotes, carried, catalogue.costless_measure_ids, catalogue.measure_ids
        )
        resolved: List[InvestmentOverride] = []
        for quote in quotes:
            entry_path = f"{StagedParameters.OVERRIDE_ENTRY_PATH}[{quote.position}]"
            if any(problem.path.startswith(entry_path) for problem in problems):
                continue
            subjects: Dict[str, Optional[ComponentType]] = {
                facts.subject: facts.facts.asset_class
                for facts in stages[quote.stage].inputs.cost_facts
                if mapping.measure_ids.get(facts.subject) == quote.measure_id
            }
            if mapping.measure_ids.get(quote.measure_id) == quote.measure_id and quote.measure_id not in subjects:
                subjects[quote.measure_id] = None  # a measure-only subject: no cost facts in any stage
            rule: Optional[MainSubjectRule] = catalogue.main_subjects.get(quote.measure_id)
            if rule is None:
                raise MainSubjectRule.undeclared(quote.measure_id, quote.stage)
            main_subject, others = rule.resolve(quote.measure_id, quote.stage, subjects)
            charged = StagedEvaluator.charged_subjects(stages, quote.stage)
            if subjects.get(main_subject) is not None and main_subject not in charged:
                code_path = f"{StagedParameters.OVERRIDE_ENTRY_PATH}.{ParameterKeys.OVERRIDE_MEASURE_ID}"
                problems.append(
                    ParameterProblem(
                        path=f"{entry_path}.{ParameterKeys.OVERRIDE_MEASURE_ID}",
                        code=ParameterProblemCodes.NOT_IN_STAGE.format(path=code_path),
                        message=f"stage {quote.stage} buys nothing for {quote.measure_id!r}: its subject "
                        f"{main_subject!r} is carried over from the stage before, so there is no purchase a "
                        "quote could price.",
                    )
                )
                continue
            resolved.append(
                InvestmentOverride(
                    stage=quote.stage,
                    measure_id=quote.measure_id,
                    amount_in_euro=quote.amount_in_euro,
                    source=quote.source,
                    main_subject=main_subject,
                    other_subjects=others,
                )
            )
        if problems:
            raise StagedEvaluationError(
                cls.QUOTES_REFUSED_MESSAGE.format(count=len(problems)), [problem.to_json() for problem in problems]
            )
        return tuple(resolved)

    #: The code a refusal about the plan as a whole is published under — a missing input file,
    #: years that run backwards, a stage with no mapping report. A refused *parameter* block
    #: carries its own per-key codes instead (``parameters.<key>.invalid`` and the rest).
    PLAN_PROBLEM_CODE: ClassVar[str] = "STAGED_PLAN_INVALID"

    @classmethod
    def write_problems(cls, out_path: str, error: StagedEvaluationError) -> str:
        """Write the `problems.json` of a refused plan beside the requested output.

        Every exit 2 of `staged` writes this file; a backend reads an exit 2 without it as a broken engine. Per-key
        rows (from a parameter block) are written verbatim; any other refusal becomes one row from its message.

        Args:
            out_path: The `--out` path the caller asked for, which is not written.
            error: The refusal.

        Returns:
            The path of the problems document, for the message on stderr.
        """
        rows: List[Mapping[str, Any]] = list(error.problems) or [
            {"code": cls.PLAN_PROBLEM_CODE, "message": str(error)}
        ]
        directory = os.path.dirname(os.path.abspath(out_path))
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, cls.PROBLEMS_FILE_NAME)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"problems": rows}, handle, indent=2)
            handle.write("\n")
        return path

    @classmethod
    def run(cls, args: argparse.Namespace) -> int:
        """Run `staged`: price a multi-year plan out of finished jobs into `economics_result.json`.

        Reads each `--stage` directory, resolves the assumptions, perspective and optional subsidy catalogue, prices
        the plan with `staged.StagedEvaluator`, validates the document against its schema and writes it, then writes
        the plan's provenance ledger as `cost_provenance.json` in the same directory. A document refused by its schema
        leaves neither file. Prints the progress lines `reading`, `evaluating` and `writing`
        (`hisim.calculation_progress`).

        Args:
            args: The parsed `staged` namespace.

        Returns:
            0 on success, 2 for a refused plan (with a `problems.json` beside `--out`), 3 for an engine failure.
        """
        progress = ProgressWriter()
        progress.enter(Phase.READING)
        try:
            stages_and_directories = [
                cls.read_stage(argument, index) for index, argument in enumerate(args.stage)
            ]
            directories = [directory for _stage, directory in stages_and_directories]
            stages = [stage for stage, _directory in stages_and_directories]
            parsed, perspective_id = cls.parameters(args, directories)
            # The financing and subsidy dimensions the parameter block may state are properties of the
            # perspective, so they are applied to the bundle's row before anything is priced.
            parameters, perspective = parsed.applied_to(cls.perspective(perspective_id))
        except StagedEvaluationError as error:
            path = cls.write_problems(args.out, error)
            print(f"{error} (problems written to {path})", file=sys.stderr)
            return cls.PLAN_REFUSED

        # Which catalogue a plan is priced under: `--subsidy-catalog` wins, then a path the stages'
        # stored record names, then the shipped directory if it has this country's file. The last
        # step serves a stage directory holding only the extract, which names no catalogue.
        catalog_path = getattr(args, "subsidy_catalog", None)
        try:
            catalog = SubsidyCatalog.load_configured(
                parameters.country,
                SubsidyCatalog.configured_or_shipped_path(
                    parameters.country, parameters.subsidy_catalog_path, catalog_path
                ),
            )
            database = CostDatabase(parameters.cost_database_path)
        except CostDataError as error:
            print(str(error), file=sys.stderr)
            return cls.ENGINE_FAILED

        progress.enter(Phase.EVALUATING)
        try:
            mapping = cls.read_mapping(directories, args.stage)
            overrides = cls.investment_overrides(parsed, stages, mapping)
            result = StagedEvaluator(database).evaluate(
                stages,
                parameters,
                perspective,
                catalog,
                plan_start_year=parsed.plan_start_year,
                investment_overrides=overrides,
            )
        except StagedEvaluationError as error:
            path = cls.write_problems(args.out, error)
            print(f"{error} (problems written to {path})", file=sys.stderr)
            return cls.PLAN_REFUSED
        except (
            UnresolvableSubjectsError,
            CostDataError,
            StagedEngineError,
            StatedPriceError,
            MainSubjectError,
        ) as error:
            # A stated price the evaluator's own checks let through and the calculator then refused
            # (`StatedPriceError`) is the engine disagreeing with itself, like a `StagedEngineError`.
            print(str(error), file=sys.stderr)
            return cls.ENGINE_FAILED
        document = StagedDocument(
            result=result,
            parameters=parameters,
            perspective=perspective,
            measure_ids=mapping.measure_ids,
            unpriced_subjects=mapping.unpriced,
            cost_provenance=ExportFileNames.PROVENANCE_FILE_NAME,
            costless_subjects=mapping.costless,
            subject_notes=mapping.notes,
            replaces_subjects=mapping.replaces,
        )
        progress.enter(Phase.WRITING)
        try:
            document.write(Path(args.out))
        except (SubsidyReconciliationError, BandOrderError, MeasureWithoutRowError) as error:
            # All three are ValueErrors, which `main` would report as a mistyped invocation (exit 2).
            # They are engine errors, and write() refuses before the file exists.
            print(str(error), file=sys.stderr)
            return cls.ENGINE_FAILED
        # `write` created the directory; the ledger goes beside the document it explains.
        ledger = result.ledger
        write_provenance_ledgers(
            {perspective.id: ledger} if ledger is not None else {}, os.path.dirname(os.path.abspath(args.out))
        )
        print(f"Wrote {args.out} for {len(stages)} stages under perspective {perspective.id}.")
        return 0

    @classmethod
    def add_parser(cls, subparsers: Any) -> None:
        """Add the `staged` subcommand, its flags and its handler (`run`) to the CLI's subparsers.

        Args:
            subparsers: What `argparse.ArgumentParser.add_subparsers` returned in `__main__.main`.
        """
        staged_parser = subparsers.add_parser(
            "staged", help="price a multi-year plan into economics_result.json (E-spec §6)"
        )
        staged_parser.add_argument(
            "--stage",
            action="append",
            required=True,
            metavar=cls.ARGUMENT_FORM,
            help="one stage of the plan; repeat once per stage, in ascending year order",
        )
        staged_parser.add_argument(
            "--parameters",
            help=(
                "JSON file in the shape of the document's own `parameters` block, every key optional: "
                + ", ".join(ParameterKeys.ACCEPTED)
                + '. Example: {"horizon_years": 20, "interest_rate": 0.03, "perspective_id": '
                + '"brownfield_net", "financing": {"kind": "cash"}, "subsidy_mode": "full"}. The '
                + "country comes from the stages; a `country` here is only checked against theirs. "
                + "The price basis year likewise. `plan_start_year` is the calendar year of the plan's "
                + "year 0: the document's calendar years count from it, and are null without it. "
                + "`energy_prices` states year-1 prices per carrier "
                + "(working price all-in, carbon included). `investment_overrides` states the reader's "
                + 'quotes, [{"stage", "measure_id", "amount_in_euro", "source"}], each replacing the '
                + "year-0 investment of the measure's main subject in that stage, booked exactly as "
                + "stated in the stage's year (never escalated). "
                + "`weather_year`, `subsidy_catalog` and "
                + "`origins` are accepted and ignored."
            ),
        )
        staged_parser.add_argument(
            "--perspective",
            help=(
                f"perspective id the plan is priced under (default {cls.DEFAULT_PERSPECTIVE}); "
                "must agree with a `perspective_id` in --parameters"
            ),
        )
        staged_parser.add_argument(
            "--subsidy-catalog",
            dest="subsidy_catalog",
            help=(
                "subsidy catalog directory; without it the shipped hisim/subsidy_catalog is used when "
                "it holds <COUNTRY>.json, and the plan runs with no catalogue otherwise"
            ),
        )
        staged_parser.add_argument("--out", required=True, help="where economics_result.json goes")
        staged_parser.set_defaults(func=cls.run)
