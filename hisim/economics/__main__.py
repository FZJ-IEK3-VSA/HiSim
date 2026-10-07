"""Command line of the lifecycle cost engine (cost_spec.md §3.10, §4.6).

Usage::

    python -m hisim.economics evaluate <results_dir> [--scenarios F] [--parameters F] [--subsidy-catalog DIR]
    python -m hisim.economics explain <results_dir> --value "<perspective>/<field-path>" [--json]
    python -m hisim.economics report <results_dir> [--compare DIR] [--scenarios F]
    python -m hisim.economics staged --stage <dir>:<from_year>:<label>[:<job_id>] ... --out <dir>/<file>
    python -m hisim.economics validate

The evaluator is a pure function of `economic_inputs.json`, so a result directory can be re-priced (`evaluate`), traced
(`explain`) or reported on (`report`) without the simulation. `staged` prices a multi-year renovation plan out of
finished jobs (see `StagedCli`); `validate` runs the §9.6 data-file checks. `evaluate`, `explain` and `report` share
one setup (`_build_context`); without `--parameters` they use the parameters stored in the run's
`lifecycle_costs.json`, never the engine defaults, and `--subsidy-catalog` overrides the catalog path the parameters
name.

Errors: an `UnresolvableSubjectsError`, any other `CostDataError`, an unreadable path, malformed JSON and a rejected
value become exit code 2 with a one-line message on stderr; any other exception propagates as a traceback.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

from hisim.calculation_scope import CalculationScope
from hisim.economics.calculators.energy import StatedPriceError
from hisim.economics.database import CostDatabase, CostDataError
from hisim.economics.evaluator import (
    EconomicEvaluator,
    EvaluationInputs,
    UnresolvableSubjectsError,
    require_resolvable_subjects,
)
from hisim.economics.exports import (
    ExportFileNames,
    write_cash_flow_timeline,
    write_component_costs,
    write_lifecycle_costs_json,
    write_provenance_ledger,
    write_provenance_ledgers,
)
from hisim.economics.input_audit import InputAuditReport, read_input_audit, write_input_audit
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import Perspective, load_default_bundle, select_applicable
from hisim.economics.results import EvaluationMatrix
from hisim.economics.scenarios import (
    ScenarioCube,
    ScenarioSet,
    evaluate_cube,
    export_cube_csv,
    export_cube_json,
)
from hisim.economics.serialization import (
    read_inputs,
    read_results,
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
from hisim.loadtypes import ComponentType
from hisim.renovisor.economics import (
    EconomicContextBuilder,
    MainSubjectError,
    MainSubjects,
    MeasureSubjects,
    ReplacedSubjects,
)
from hisim.renovisor.progress import Phase, ProgressWriter
from hisim.renovisor.report import MappingReport
from hisim.renovisor.request import CatalogueTable
from hisim.economics.subsidies import SubsidyCatalog
from hisim.economics.validation import validate_all

if TYPE_CHECKING:  # The renderers are imported lazily, per subcommand; this is their return type.
    from hisim.economics.report_plots import PlotsWritten


class CliFileNames:
    """Names of the files the CLI itself writes; only the scenario cube, which two subcommands write."""

    SCENARIO_CUBE_CSV = "scenario_cube.csv"
    SCENARIO_CUBE_JSON = "scenario_cube.json"


class AuditLayerProbe:
    """Checks that every module `evaluate` needs for its audit files is importable, before anything is written.

    `evaluate` writes `cost_audit.csv`/`.json` and the ledger heatmap PNG beside the numeric exports, so
    `hisim.economics.audit`, the renderer and matplotlib are probed first; a missing one refuses the command by name
    instead of leaving a half-written directory. `bridge._require_plot_layer` does the same for postprocessing.
    """

    #: Modules that must be importable for `evaluate` to write a complete export set: the audit
    #: layer, the renderer of the audit's own figure, and the library that figure is drawn with.
    MODULE_NAMES = ("hisim.economics.audit", "matplotlib", "hisim.economics.report_plots")

    @classmethod
    def require(cls) -> None:
        """Raise unless every module the audit outputs need is importable.

        Uses `importlib.util.find_spec`, so nothing is imported.

        Raises:
            CostDataError: If any of them is absent; `main` turns it into exit code 2.
        """
        missing = [name for name in cls.MODULE_NAMES if importlib.util.find_spec(name) is None]
        if not missing:
            return
        raise CostDataError(
            f"{', '.join(missing)} is not importable, so `evaluate` cannot write the audit that "
            "belongs to a stored evaluation (W4.5) — its tables or its ledger heatmap — and would "
            "leave a half-written export set behind. Nothing was written."
        )


@dataclass(frozen=True)
class EvaluationContext:
    """Everything one re-pricing invocation of `evaluate`, `explain` or `report` needs, assembled once (§4.6).

    One record built by `_build_context`, so the three commands price under the same assumptions and catalog.

    Attributes:
        inputs: The stored physical facts of one simulated variant (`economic_inputs.json`).
        parameters: The assumptions, from `--parameters` or from the stored run itself.
        database: The cost database the parameters point at.
        catalog: The subsidy catalog, or None when neither the flag nor the parameters name one.
        evaluator: The engine bound to database, parameters and catalog.
        perspectives: The applicable rows of the default bundle, in bundle order.
    """

    inputs: EvaluationInputs
    parameters: EconomicParameters
    database: CostDatabase
    catalog: Optional[SubsidyCatalog]
    evaluator: EconomicEvaluator
    perspectives: List[Perspective]


def _load_parameters(args: argparse.Namespace, results_dir: Optional[str] = None) -> EconomicParameters:
    """Return the economic assumptions for this invocation: the `--parameters` file, else the run's own.

    Without the flag, the parameters the run was priced under are read from its `lifecycle_costs.json`
    (`serialization.read_stored_parameters`). The engine defaults are never used, since re-pricing an archived study at
    default assumptions answers a question nobody asked.

    Args:
        args: The parsed CLI namespace, for `--parameters`.
        results_dir: The invocation's result directory.

    Returns:
        The caller's parameters, or the ones stored with the run.

    Raises:
        CostDataError: If `--parameters` is not a readable file, or no path was given and the directory has no stored
            parameters.
    """
    if args.parameters:
        if not os.path.isfile(args.parameters):
            raise CostDataError(
                f"--parameters file not found: {args.parameters!r}. Omit the flag to price with "
                "the parameters stored with the run; the defaults are never used as a fallback "
                "for a path that was given explicitly."
            )
        with open(args.parameters, encoding="utf-8") as file:
            parameters: EconomicParameters = EconomicParameters.from_dict(json.load(file))
        return parameters
    stored = read_stored_parameters(results_dir) if results_dir else None
    if stored is not None:
        return stored
    raise CostDataError(
        f"No economic parameters for {results_dir!r}: the directory has no lifecycle_costs.json "
        "carrying the parameters the run was priced under, and pricing it with the engine "
        "defaults would silently answer a different question. Pass --parameters <file>, or run "
        "`evaluate --parameters <file>` on the directory first."
    )


def _build_context(results_dir: str, args: argparse.Namespace) -> EvaluationContext:
    """Assemble the `EvaluationContext` behind `evaluate`, `explain` and `report` (§4.6).

    Reads the stored inputs, resolves the assumptions, database and catalog, binds an evaluator, runs the resolution
    check against that same database and catalog, and selects the applicable perspectives. `--subsidy-catalog` wins
    over `parameters.subsidy_catalog_path`.

    Args:
        results_dir: Directory holding `economic_inputs.json`.
        args: The parsed CLI namespace; `--parameters` and `--subsidy-catalog` are read.

    Returns:
        The assembled context.

    Raises:
        UnresolvableSubjectsError: If any cost subject cannot be priced.
        CostDataError: If the parameters file, the database or the catalog cannot be loaded.
    """
    inputs = read_inputs(results_dir)
    parameters = _load_parameters(args, results_dir)
    database = CostDatabase(parameters.cost_database_path)
    catalog = SubsidyCatalog.load_configured(
        parameters.country, parameters.subsidy_catalog_path, getattr(args, "subsidy_catalog", None)
    )
    evaluator = EconomicEvaluator(database, parameters, catalog)
    require_resolvable_subjects(inputs, evaluator)
    return EvaluationContext(
        inputs=inputs,
        parameters=parameters,
        database=database,
        catalog=catalog,
        evaluator=evaluator,
        perspectives=select_applicable(load_default_bundle(), has_register=inputs.existing_assets is not None),
    )


def _evaluate_perspectives(context: EvaluationContext) -> EvaluationMatrix:
    """Evaluate every applicable perspective of the context into one matrix (§7.1).

    Shared by `evaluate` and `report`, so both produce the same matrix for the same inputs.

    Args:
        context: The assembled evaluation context.

    Returns:
        The matrix, keyed by perspective id in bundle order.
    """
    matrix = EvaluationMatrix()
    for perspective in context.perspectives:
        matrix.results[perspective.id] = context.evaluator.evaluate(context.inputs, perspective)
    return matrix


def _write_scenario_cube(context: EvaluationContext, scenarios_path: str, results_dir: str) -> ScenarioCube:
    """Evaluate the §4.6 scenario cube and write `scenario_cube.csv`/`.json`.

    Shared by `evaluate --scenarios` and `report --scenarios`. A cube is always freshly evaluated.

    Args:
        context: The assembled evaluation context.
        scenarios_path: The scenario-set JSON file to read.
        results_dir: Directory the two cube files are written to.

    Returns:
        The evaluated cube.
    """
    with open(scenarios_path, encoding="utf-8") as file:
        scenario_set = ScenarioSet.from_json(json.load(file))
    cube = evaluate_cube(
        context.inputs, context.parameters, context.perspectives, scenario_set, context.database, context.catalog
    )
    export_cube_csv(cube, os.path.join(results_dir, CliFileNames.SCENARIO_CUBE_CSV))
    export_cube_json(cube, os.path.join(results_dir, CliFileNames.SCENARIO_CUBE_JSON))
    return cube


def _cmd_evaluate(args: argparse.Namespace) -> int:
    """Run `evaluate`: re-price a stored result directory, or evaluate a scenario cube over it (§4.6).

    Without `--scenarios`, evaluates the applicable perspectives and overwrites the directory's `lifecycle_costs.json`,
    `component_costs.*`, `cash_flow_timeline.csv`, `cost_provenance.json`, `cost_audit.csv`/`.json` and the ledger
    heatmap, so a later `report` needs no cost database; prints the number of perspectives. With `--scenarios`, writes
    only `scenario_cube.csv`/`.json` and prints the number of cells. The audit-layer probe runs before any file is
    written.

    Returns:
        0; failures are exceptions that `main` turns into exit code 2.
    """
    AuditLayerProbe.require()
    context = _build_context(args.results_dir, args)
    if args.scenarios:
        cube = _write_scenario_cube(context, args.scenarios, args.results_dir)
        print(f"Wrote scenario_cube.csv/.json for {sum(len(v) for v in cube.results.values())} cells.")
        return 0
    matrix = _evaluate_perspectives(context)
    write_lifecycle_costs_json(matrix, args.results_dir)
    write_component_costs(matrix, args.results_dir)
    write_cash_flow_timeline(matrix, args.results_dir)
    write_provenance_ledger(matrix, args.results_dir)
    first = next(iter(matrix.results.values()), None)
    if first is not None:
        # The audit belongs to the stored evaluation, so a later `report` can render section 1
        # without reopening the cost database. The probe above ensured this import succeeds.
        from hisim.economics.audit import (
            build_input_audit,
            write_cost_audit,
        )
        from hisim.economics.report_plots import write_audit_plots

        audit = build_input_audit(context.inputs, context.database, context.parameters, first)
        write_cost_audit(audit, args.results_dir)
        write_input_audit(audit, args.results_dir)
        # The ledger heatmap travels with the audit tables, so it is refreshed exactly when the
        # audit is. The renderer returns what it did not draw; the CLI prints it.
        _print_plot_skips(write_audit_plots(first, args.results_dir))
    print(f"Re-evaluated {len(matrix.results)} perspectives into {args.results_dir}.")
    return 0


def _cmd_explain(args: argparse.Namespace) -> int:
    """Run `explain`: trace one result value back to the data entries and sources behind it (§3.10).

    Takes `--value "<perspective>/<field-path>"`, re-evaluates that perspective (the provenance ledger is built during
    evaluation) and prints which parameters entered the value and where each came from: database entry, config
    override, scenario overlay or engine default, with their sources. `--json` prints the machine-readable report. The
    context is built exactly as for `evaluate`.

    Returns:
        0 on success; 2 with a message on stderr when `--value` has no `/` or names a perspective not applicable to
            this directory.
    """
    if "/" not in args.value:
        print("--value must have the form '<perspective>/<field-path>'", file=sys.stderr)
        return 2
    perspective_id, value_path = args.value.split("/", 1)
    context = _build_context(args.results_dir, args)
    matching = [perspective for perspective in context.perspectives if perspective.id == perspective_id]
    if not matching:
        print(f"Unknown perspective {perspective_id!r}.", file=sys.stderr)
        return 2
    result = context.evaluator.evaluate(context.inputs, matching[0])
    report = result.explain(value_path)
    if args.json:
        print(json.dumps(report.to_json(), indent=2))
    else:
        print(report.render_text())
    return 0


def _evaluate_directory(
    results_dir: str, args: argparse.Namespace
) -> "Tuple[EvaluationMatrix, Optional[InputAuditReport]]":
    """Re-price a directory's `economic_inputs.json` from scratch, for `report`.

    Runs the full engine and the input audit, so the caller gets what `read_results` and `read_input_audit` return for
    a directory that has stored results.

    Args:
        results_dir: Directory holding `economic_inputs.json`.
        args: The parsed CLI namespace, for `--parameters` and `--subsidy-catalog`.

    Returns:
        The evaluated matrix and its input audit (None only when no perspective was evaluated).

    Raises:
        CostDataError: If the audit layer is not importable, or the data will not load.
    """
    AuditLayerProbe.require()
    # Imported here rather than at module level so that `AuditLayerProbe` can report a missing
    # audit layer as a message instead of an import traceback during startup.
    from hisim.economics.audit import build_input_audit

    context = _build_context(results_dir, args)
    matrix = _evaluate_perspectives(context)
    first = next(iter(matrix.results.values()), None)
    audit = (
        build_input_audit(context.inputs, context.database, context.parameters, first) if first is not None else None
    )
    return matrix, audit


def _repricing_flags(args: argparse.Namespace) -> List[str]:
    """Return the flags on this invocation that change the assumptions a result is priced under.

    `report` renders stored results unless one of these (`--parameters`, `--subsidy-catalog`) is given, in which case
    it re-evaluates.

    Args:
        args: The parsed CLI namespace.

    Returns:
        The names of the given re-pricing flags, in flag order; empty when none were given.
    """
    given = (
        (getattr(args, "parameters", None), "--parameters"),
        (getattr(args, "subsidy_catalog", None), "--subsidy-catalog"),
    )
    return [name for value, name in given if value]


def _load_or_evaluate(
    results_dir: str, args: argparse.Namespace, label: str
) -> "Tuple[EvaluationMatrix, Optional[InputAuditReport]]":
    """Return the directory's stored results, or a fresh evaluation if it has none or a flag re-prices them.

    A directory written by `evaluate`, the postprocessing bridge or an earlier `report` is rendered as it stands. It is
    re-evaluated when it holds only `economic_inputs.json` (which then needs `--parameters`, since there are no stored
    assumptions) or when a re-pricing flag (`_repricing_flags`) is given; both cases print a line saying so.

    Args:
        results_dir: Directory to load or evaluate.
        args: The parsed CLI namespace, for `--parameters` and `--subsidy-catalog`.
        label: How to name the directory in the printed message.

    Returns:
        The matrix and the input audit, from storage or from a fresh evaluation.
    """
    flags = _repricing_flags(args)
    if flags:
        print(
            f"{' and '.join(flags)} given: re-evaluating {label} under those assumptions instead "
            "of rendering its stored results."
        )
        return _evaluate_directory(results_dir, args)
    stored = read_results(results_dir)
    if stored is not None and stored.results:
        return stored, read_input_audit(results_dir)
    print(f"No stored results in {label}: re-evaluating from economic_inputs.json.")
    return _evaluate_directory(results_dir, args)


def _cmd_report(args: argparse.Namespace) -> int:
    """Run `report`: write cost_summary.md, lifecycle_report.html and the PNG charts for stored results.

    Renders stored results and re-prices only via `_load_or_evaluate`. `--compare <reference_dir>` loads a second
    directory the same way, picks a shared perspective (`brownfield_net`, then `greenfield_net`) and adds the variant
    comparison (delta waterfall, discounted payback band, warm-rent change) and the payback PNG. `--scenarios`
    evaluates a §4.6 cube for the scenario section and writes `scenario_cube.csv`/`.json`.

    Returns:
        0 on success; 2 with a message on stderr when the directory yields no results or the two compared directories
            share no perspective.
    """
    from hisim.economics.plausibility import run_plausibility_checks

    from hisim.economics.report_plots import write_report_plots
    from hisim.economics.reporting import (
        write_cost_summary,
        write_lifecycle_report,
    )
    from hisim.economics.results import compare

    matrix, audit = _load_or_evaluate(args.results_dir, args, args.results_dir)
    if not matrix.results:
        print(f"No results to report in {args.results_dir}.", file=sys.stderr)
        return 2

    comparison = None
    reference_result = None
    if args.compare:
        reference_matrix, _reference_audit = _load_or_evaluate(args.compare, args, args.compare)
        shared = [pid for pid in matrix.results if pid in reference_matrix.results]
        if not shared:
            print("No shared perspective between the two result directories.", file=sys.stderr)
            return 2
        chosen = next((pid for pid in ("brownfield_net", "greenfield_net") if pid in shared), shared[0])
        reference_result = reference_matrix.results[chosen]
        comparison = compare(
            reference_result, matrix.results[chosen], reference_id=args.compare, variant_id=args.results_dir
        )

    scenario_cube = None
    if getattr(args, "scenarios", None):
        scenario_cube = _write_scenario_cube(
            _build_context(args.results_dir, args), args.scenarios, args.results_dir
        )

    plausibility = run_plausibility_checks(matrix)
    write_cost_summary(matrix, plausibility, args.results_dir, comparison)
    write_lifecycle_report(
        matrix, plausibility, args.results_dir, audit, comparison, scenario_cube=scenario_cube,
        reference_result=reference_result,
    )
    # One function owns the PNG set: given the comparison's reference, it writes the payback
    # curve as part of that set. The comparison is passed in because it carries the two
    # directories as reference and variant ids, which recomputing it would lose.
    _print_plot_skips(write_report_plots(matrix, args.results_dir, reference_result, comparison))
    print(
        f"Wrote cost_summary.md, lifecycle_report.html and PNG charts to {args.results_dir} "
        f"({len(plausibility)} plausibility checks, {len(plausibility.flagged())} flagged)."
    )
    return 0


def _print_plot_skips(plots: "PlotsWritten") -> None:
    """Print one line per chart the renderers did not draw; nothing when all were drawn.

    Args:
        plots: The `report_plots.PlotsWritten` a writer returned.
    """
    for line in plots.lines():
        print(f"Chart not drawn: {line}")


@dataclass(frozen=True)
class StageMapping:
    """What the stages' mapping reports say about the cost subjects, merged over the plan.

    Attributes:
        measure_ids: Cost subject -> the catalogue measure that created it.
        unpriced: The subjects with no price behind them.
        costless: The subjects of a measure that costs nothing to carry out.
        notes: Subject -> the sentence its ``by_subject`` row carries as ``note``.
        replaces: Measure subject -> the reference subjects it replaces, which its ``by_subject``
            row carries as ``replaces_subjects``.
    """

    measure_ids: Dict[str, Optional[str]]
    unpriced: List[str]
    costless: List[str]
    notes: Dict[str, str]
    replaces: Dict[str, List[str]] = field(default_factory=dict)


class StagedCli:
    """Everything the `staged` subcommand decides, in one place.

    `staged` prices a renovation plan spread over several years out of the stored `economic_inputs.json` of the jobs
    that simulated each state, and writes `economics_result.json`. It runs no simulation and finishes in under a
    second, so a backend can call it synchronously.

    Exit codes: 0 with the document; 2 with a `problems.json` beside `--out` when the plan is refused (missing input,
    years running backwards, stages priced under different conditions, unknown perspective, country without data,
    refused parameter key, unreadable `--parameters` file); 3 with one line on stderr when the engine refuses (an
    unresolvable cost subject). A 2 is fixable by sending a different plan; a 3 is not. An `--out` without a directory
    is refused with exit 2 and a message only, since there is nowhere to write.

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

    #: The mapping report a stage directory may carry, for the subject -> measure map.
    MAPPING_REPORT_FILE_NAME: ClassVar[str] = "mapping_report.json"

    #: The stored inputs every stage must carry, under the directory or under its results.
    INPUTS_FILE_NAME: ClassVar[str] = "economic_inputs.json"

    #: Where a finished RenoVisor job puts the simulation's own outputs, the stored inputs among
    #: them. A caller names the job directory and the CLI looks one level down, so the argument is
    #: the directory the backend already has rather than a path into it.
    RESULTS_SUBDIRECTORY: ClassVar[str] = "results"

    #: Its two keys, taken from the class that writes the file (``MappingReport.to_json``) so a
    #: rename on that side cannot silently drop every measure stamp and unpriced flag.
    SUBJECTS_KEY: ClassVar[str] = MappingReport.SUBJECTS_FIELD

    #: Its key holding the subjects the translator could not price.
    UNPRICED_KEY: ClassVar[str] = MappingReport.UNPRICED_SUBJECTS_FIELD

    #: Its key holding the subjects of measures that cost nothing to carry out.
    COSTLESS_KEY: ClassVar[str] = MappingReport.COSTLESS_SUBJECTS_FIELD

    #: Its key holding the sentence a subject's row carries as its ``note``.
    NOTES_KEY: ClassVar[str] = MappingReport.SUBJECT_NOTES_FIELD

    #: Its key naming the reference subjects each measure subject replaces.
    REPLACES_KEY: ClassVar[str] = MappingReport.REPLACES_SUBJECTS_FIELD

    #: Its key holding the measure lines, taken from the writer for the same reason.
    MEASURES_KEY: ClassVar[str] = MappingReport.MEASURES_FIELD

    #: The measure statuses a stage's ``measures`` list carries: the ones the translation acts on
    #: (:attr:`MappingReport.ACTED_ON_STATUSES`, as the words the report spells them with). A
    #: measure whose line reads ``not_implemented_yet`` is acted on by nothing, so naming it would
    #: tell the stage timeline the stage changed something it did not.
    STAGE_MEASURE_STATUSES: ClassVar[Tuple[str, ...]] = tuple(
        status.value for status in MappingReport.ACTED_ON_STATUSES
    )

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
        """Return the catalogue measure ids one stage acts on, from its mapping report.

        Takes the report's `measures[]` entries the translation acted on (`used` or `approximated`, never
        `not_implemented_yet`), in catalogue order so the same measures always read the same. A stage without a report
        yields nothing here; `read_mapping` refuses it.

        Args:
            directory: The `--stage` argument's first field.
            index: The stage's position, for the error message.
            argument: The argument as typed, for the error message.

        Returns:
            The measure ids, in catalogue order.

        Raises:
            StagedEvaluationError: If the report exists but is not valid JSON.
        """
        path = cls.mapping_report_path(directory)
        if path is None:
            return ()
        try:
            with open(path, encoding="utf-8") as handle:
                report = json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            raise StagedEvaluationError(
                f"--stage #{index} {argument!r}: {cls.MAPPING_REPORT_FILE_NAME} in {directory!r} "
                f"does not read back ({error})."
            ) from error
        entries = report.get(cls.MEASURES_KEY)
        acted_on = [
            str(entry.get("id"))
            for entry in entries or ()
            if isinstance(entry, dict) and entry.get("status") in cls.STAGE_MEASURE_STATUSES
        ]
        order = {measure_id: position for position, measure_id in enumerate(CatalogueTable.ids())}
        return tuple(sorted(acted_on, key=lambda measure_id: (order.get(measure_id, len(order)), measure_id)))

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

    @classmethod
    def read_mapping(
        cls,
        directories: List[str],
        arguments: Optional[List[str]] = None,
        stages: Optional[Sequence[Stage]] = None,
    ) -> "StageMapping":
        """Return the merged subject-to-measure map and subject flags over every stage directory.

        Each stage directory must have the translator's `mapping_report.json`, in itself or its parent: `subjects` says
        which catalogue measure created which cost subject, `unpriced_subjects` which have no price,
        `costless_subjects` which stand for a measure that costs nothing, `subject_notes` why, and `replaces_subjects`
        which reference subjects each measure subject replaces. Keys missing from an older report read as empty;
        missing `replaces_subjects` is derived from the stored inputs when `stages` are given
        (`_replaced_from_inputs`). Later stages win. A directory without a report is refused: an unpriced subject
        reaches the engine with an investment of zero, and only the flag tells that apart from a real price of nothing.

        Args:
            directories: The stage directories, in stage order.
            arguments: The `--stage` arguments they came from, for the refusal message; the directories when not given.
            stages: The stages read from the same directories, for deriving `replaces_subjects`; without them an older
                report contributes none.

        Returns:
            The `StageMapping`.

        Raises:
            StagedEvaluationError: Naming the first directory without a report (exit 2 with `problems.json`).
        """
        spelled = arguments if arguments is not None else directories
        measures: Dict[str, Optional[str]] = {}
        unpriced: List[str] = []
        costless: List[str] = []
        notes: Dict[str, str] = {}
        replaces: Dict[str, List[str]] = {}
        for index, directory in enumerate(directories):
            path = cls.mapping_report_path(directory)
            if path is None:
                raise StagedEvaluationError(
                    cls.MISSING_MAPPING_MESSAGE.format(
                        index=index,
                        argument=spelled[index],
                        directory=directory,
                        inputs=cls.INPUTS_FILE_NAME,
                        report=cls.MAPPING_REPORT_FILE_NAME,
                    )
                )
            with open(path, encoding="utf-8") as handle:
                report = json.load(handle)
            subjects = report.get(cls.SUBJECTS_KEY)
            if isinstance(subjects, dict):
                measures.update(subjects)
            for subject in report.get(cls.UNPRICED_KEY) or []:
                if subject not in unpriced:
                    unpriced.append(subject)
            for subject in report.get(cls.COSTLESS_KEY) or []:
                if subject not in costless:
                    costless.append(subject)
            stated_notes = report.get(cls.NOTES_KEY)
            if isinstance(stated_notes, dict):
                notes.update(stated_notes)
            if cls.COSTLESS_KEY not in report or cls.NOTES_KEY not in report:
                cls._declared_from_translator(report, measures, unpriced, costless, notes)
            stated_replaces = report.get(cls.REPLACES_KEY)
            if isinstance(stated_replaces, dict):
                replaces.update({subject: list(names) for subject, names in stated_replaces.items()})
            elif stages is not None and index < len(stages):
                replaces.update(cls._replaced_from_inputs(stages[index], stages[0], measures))
        return StageMapping(
            measure_ids=measures, unpriced=unpriced, costless=costless, notes=notes, replaces=replaces
        )

    @staticmethod
    def _replaced_from_inputs(
        stage: Stage, reference: Stage, measures: Mapping[str, Optional[str]]
    ) -> Dict[str, List[str]]:
        """Derive the `replaces_subjects` for a mapping report that lacks the field.

        Applies the translator's rule (`renovisor.economics.ReplacedSubjects`) to the stage's stored register (with its
        `replaced_by_asset_classes`), the stage's measure subjects and the reference stage's subjects. A reference
        without envelope subjects names no envelope element.

        Args:
            stage: The stage whose report lacks the field.
            reference: `stages[0]`, the do-nothing reference.
            measures: The subject-to-measure map merged so far, this stage's report included.

        Returns:
            Measure subject -> the reference subjects it replaces.
        """
        return ReplacedSubjects.derive(
            {
                facts.subject: facts.facts.asset_class
                for facts in stage.inputs.cost_facts
                if measures.get(facts.subject)
            },
            stage.inputs.existing_assets,
            {facts.subject: facts.facts.asset_class for facts in reference.inputs.cost_facts},
        )

    @classmethod
    def _declared_from_translator(
        cls,
        report: Mapping[str, Any],
        measures: Dict[str, Optional[str]],
        unpriced: List[str],
        costless: List[str],
        notes: Dict[str, str],
    ) -> None:
        """Fill in what an older mapping report without `costless_subjects` and `subject_notes` does not say.

        Such a report also has no subject for a measure the engine prices at nothing (e.g. changing the set point),
        which the document would refuse. The translator's own declarations
        (`renovisor.economics.MeasureSubjects.declare`) stand in: for every declared measure the stage acts on, the
        measure-named subject, its unpriced or costless flag and its note. An unpriced subject without a note gets the
        translator's `EconomicContextBuilder.UNPRICED_NOTE`. A measure that is neither declared nor has a subject stays
        without a row.

        Args:
            report: One stage's mapping report, as read.
            measures: The subject-to-measure map being merged; extended in place.
            unpriced: The unpriced subjects being merged; extended in place.
            costless: The costless subjects being merged; extended in place.
            notes: The notes being merged; extended in place.
        """
        acted_on = [
            str(entry.get("id"))
            for entry in report.get(cls.MEASURES_KEY) or ()
            if isinstance(entry, dict) and entry.get("status") in cls.STAGE_MEASURE_STATUSES
        ]
        MeasureSubjects.declare(acted_on, measures, unpriced=unpriced, costless=costless, notes=notes)
        for subject in report.get(cls.UNPRICED_KEY) or []:
            notes.setdefault(subject, EconomicContextBuilder.UNPRICED_NOTE)

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
        a problem row (exit 2), reported together. The main subject is resolved by `renovisor.economics.MainSubjects`
        over the subjects the translator assigned to the measure in that stage.

        Args:
            parsed: The parsed parameter block.
            stages: The plan.
            mapping: The stages' merged subject map.

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
        problems = StagedParameters.check_quotes(
            quotes, carried, tuple(MeasureSubjects.COSTLESS), CatalogueTable.ids()
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
            main_subject, others = MainSubjects.resolve(quote.measure_id, quote.stage, subjects)
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


def _cmd_staged(args: argparse.Namespace) -> int:
    """Run `staged`: price a multi-year plan out of finished jobs into `economics_result.json`.

    Reads each `--stage` directory, resolves the assumptions, perspective and optional subsidy catalogue, prices the
    plan with `staged.StagedEvaluator`, validates the document against its schema and writes it, then writes the plan's
    provenance ledger as `cost_provenance.json` in the same directory. A document refused by its schema leaves neither
    file. Prints the progress lines `reading`, `evaluating` and `writing` (`hisim.renovisor.progress`).

    Returns:
        0 on success, 2 for a refused plan (with a `problems.json` beside `--out`), 3 for an engine failure.
    """
    progress = ProgressWriter()
    progress.enter(Phase.READING)
    try:
        stages_and_directories = [
            StagedCli.read_stage(argument, index) for index, argument in enumerate(args.stage)
        ]
        directories = [directory for _stage, directory in stages_and_directories]
        stages = [stage for stage, _directory in stages_and_directories]
        parsed, perspective_id = StagedCli.parameters(args, directories)
        # The financing and subsidy dimensions the parameter block may state are properties of the
        # perspective, so they are applied to the bundle's row before anything is priced.
        parameters, perspective = parsed.applied_to(StagedCli.perspective(perspective_id))
    except StagedEvaluationError as error:
        path = StagedCli.write_problems(args.out, error)
        print(f"{error} (problems written to {path})", file=sys.stderr)
        return StagedCli.PLAN_REFUSED

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
        return StagedCli.ENGINE_FAILED

    progress.enter(Phase.EVALUATING)
    try:
        mapping = StagedCli.read_mapping(directories, args.stage, stages)
        overrides = StagedCli.investment_overrides(parsed, stages, mapping)
        result = StagedEvaluator(database).evaluate(
            stages,
            parameters,
            perspective,
            catalog,
            plan_start_year=parsed.plan_start_year,
            investment_overrides=overrides,
        )
    except StagedEvaluationError as error:
        path = StagedCli.write_problems(args.out, error)
        print(f"{error} (problems written to {path})", file=sys.stderr)
        return StagedCli.PLAN_REFUSED
    except (UnresolvableSubjectsError, CostDataError, StagedEngineError, StatedPriceError, MainSubjectError) as error:
        # A stated price the evaluator's own checks let through and the calculator then refused
        # (`StatedPriceError`) is the engine disagreeing with itself, like a `StagedEngineError`.
        print(str(error), file=sys.stderr)
        return StagedCli.ENGINE_FAILED
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
        return StagedCli.ENGINE_FAILED
    # `write` created the directory; the ledger goes beside the document it explains.
    ledger = result.ledger
    write_provenance_ledgers(
        {perspective.id: ledger} if ledger is not None else {}, os.path.dirname(os.path.abspath(args.out))
    )
    print(f"Wrote {args.out} for {len(stages)} stages under perspective {perspective.id}.")
    return 0


def _cmd_validate(_args: argparse.Namespace) -> int:
    """Run `validate`: the §9.6 data-file checks over the shipped `hisim/cost_database/` and `hisim/subsidy_catalog/`.

    Prints every warning, every error and a count line. The same checks run in CI; run this after editing a price, a
    source or a subsidy scheme. An error means the shipped data is inconsistent (e.g. an unsourced datapoint, a
    coverage hole, a malformed tariff contract, an exclusion naming an unknown scheme).

    Returns:
        0 when there are no errors (warnings do not count), 1 otherwise.
    """
    report = validate_all()
    for warning in report.warnings:
        print(f"WARNING: {warning}")
    for error in report.errors:
        print(f"ERROR: {error}")
    print(f"{len(report.errors)} errors, {len(report.warnings)} warnings.")
    return 0 if report.ok else 1


def _output_directory(args: argparse.Namespace) -> Optional[str]:
    """Return the directory a subcommand writes into, or None when it writes nowhere.

    `staged` writes beside `--out` (a bare file name names no directory); the other commands write into their result
    directory. A missing result directory is not created here; the command reports it.
    """
    if args.command == "staged":
        return StagedCli.out_directory(args.out)
    results_dir = getattr(args, "results_dir", None)
    if results_dir and os.path.isdir(results_dir):
        return str(results_dir)
    return None


def main(argv=None) -> int:
    """Parse the arguments, run the matching `_cmd_*` handler and return its exit code.

    `UnresolvableSubjectsError`, every other `CostDataError`, `OSError`, `json.JSONDecodeError` and `ValueError` are
    caught for every subcommand and reported on stderr with exit code 2, so a data or invocation problem is a message,
    not a traceback, and no partial cost result is emitted. Other exceptions propagate.

    Args:
        argv: Argument list, defaulting to `sys.argv[1:]`.

    Returns:
        The subcommand's exit code, or 2 for an unresolvable subject or a data or invocation problem.
    """
    parser = argparse.ArgumentParser(prog="python -m hisim.economics")
    subparsers = parser.add_subparsers(dest="command", required=True)

    evaluate_parser = subparsers.add_parser("evaluate", help="re-price stored results (§4.6)")
    evaluate_parser.add_argument("results_dir")
    evaluate_parser.add_argument("--scenarios", help="scenario-set JSON file")
    evaluate_parser.add_argument("--parameters", help="EconomicParameters JSON file")
    evaluate_parser.add_argument("--subsidy-catalog", dest="subsidy_catalog", help="subsidy catalog directory")
    evaluate_parser.set_defaults(func=_cmd_evaluate)

    explain_parser = subparsers.add_parser("explain", help="trace a result value to its sources (§3.10)")
    explain_parser.add_argument("results_dir")
    explain_parser.add_argument("--value", required=True, help="e.g. brownfield_net/equivalent_annual_cost_in_euro")
    explain_parser.add_argument("--parameters", help="EconomicParameters JSON file")
    explain_parser.add_argument("--subsidy-catalog", dest="subsidy_catalog", help="subsidy catalog directory")
    explain_parser.add_argument("--json", action="store_true")
    explain_parser.set_defaults(func=_cmd_explain)

    report_parser = subparsers.add_parser(
        "report", help="human-readable report + plausibility panel for stored results"
    )
    report_parser.add_argument("results_dir")
    report_parser.add_argument("--compare", help="reference result directory for a variant comparison")
    report_parser.add_argument("--parameters", help="EconomicParameters JSON file (re-prices instead of rendering)")
    report_parser.add_argument(
        "--subsidy-catalog",
        dest="subsidy_catalog",
        help="subsidy catalog directory (re-prices instead of rendering)",
    )
    report_parser.add_argument("--scenarios", help="scenario-set JSON file for the report's scenario section")
    report_parser.set_defaults(func=_cmd_report)

    staged_parser = subparsers.add_parser(
        "staged", help="price a multi-year plan into economics_result.json (E-spec §6)"
    )
    staged_parser.add_argument(
        "--stage",
        action="append",
        required=True,
        metavar=StagedCli.ARGUMENT_FORM,
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
            f"perspective id the plan is priced under (default {StagedCli.DEFAULT_PERSPECTIVE}); "
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
    staged_parser.set_defaults(func=_cmd_staged)

    validate_parser = subparsers.add_parser("validate", help="data-file CI checks (§9.6)")
    validate_parser.set_defaults(func=_cmd_validate)

    args = parser.parse_args(argv)
    if args.command == "staged" and StagedCli.out_directory(args.out) is None:
        # Refused before the calculation opens, and without a problems.json: the refusal is exactly
        # that there is no directory to write one to (StagedCli.out_directory).
        print(StagedCli.BARE_OUT_MESSAGE.format(out=args.out), file=sys.stderr)
        return StagedCli.PLAN_REFUSED
    try:
        # One calculation, as a simulation run is one (hisim.calculation_scope): whatever the
        # command writes has to land in the directory it was pointed at -- the result directory it
        # re-prices, or the directory of the staged plan's --out -- or in the cache directories.
        with CalculationScope.open(label=f"hisim.economics {args.command}", run_directory=_output_directory(args)):
            return int(args.func(args))
    except UnresolvableSubjectsError as err:
        # An unresolvable subject: no partial cost results; the message the bridge logs goes to
        # stderr with exit code 2.
        print(str(err), file=sys.stderr)
        return 2
    except CostDataError as err:
        # Everything else the data layer refuses (an unloadable database, a missing
        # `--parameters` file): same channel, same exit code, never a result built on defaults.
        print(str(err), file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError, ValueError) as err:
        # The ordinary shapes of a bad invocation: a missing directory, a JSON file that will not
        # parse, a rejected parameter value. The caller mistyped something; no traceback.
        print(f"{type(err).__name__}: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
