"""Command line of the lifecycle cost engine (cost_spec.md §3.10, §4.6).

Usage::

    python -m hisim.economics evaluate <results_dir> [--scenarios F] [--parameters F] [--subsidy-catalog DIR]
    python -m hisim.economics explain <results_dir> --value "<perspective>/<field-path>" [--json]
    python -m hisim.economics report <results_dir> [--compare DIR] [--scenarios F]
    python -m hisim.economics staged --stage <dir>:<from_year>:<label>[:<job_id>] ... --out <dir>/<file>
    python -m hisim.economics validate

The evaluator is a pure function of `economic_inputs.json`, so a result directory can be re-priced (`evaluate`), traced
(`explain`) or reported on (`report`) without the simulation. `staged` prices a multi-year renovation plan out of
finished jobs (`staged_cli.StagedCli`, to which `main` dispatches); `validate` runs the §9.6 data-file checks.
`evaluate`, `explain` and `report` share one setup (`_build_context`); without `--parameters` they use the parameters
stored in the run's `lifecycle_costs.json`, never the engine defaults, and `--subsidy-catalog` overrides the catalog
path the parameters name.

Errors: an `UnresolvableSubjectsError`, any other `CostDataError`, an unreadable path, malformed JSON and a rejected
value become exit code 2 with a one-line message on stderr; any other exception propagates as a traceback.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Tuple

from hisim.calculation_scope import CalculationScope
from hisim.economics.database import CostDatabase, CostDataError
from hisim.economics.evaluator import (
    EconomicEvaluator,
    EvaluationInputs,
    UnresolvableSubjectsError,
    require_resolvable_subjects,
)
from hisim.economics.exports import (
    write_cash_flow_timeline,
    write_component_costs,
    write_lifecycle_costs_json,
    write_provenance_ledger,
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
from hisim.economics.serialization import read_inputs, read_results, read_stored_parameters
from hisim.economics.staged_cli import StagedCli
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

    StagedCli.add_parser(subparsers)

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
