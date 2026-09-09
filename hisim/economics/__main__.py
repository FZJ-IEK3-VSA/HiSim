"""CLI of the lifecycle cost engine (cost_spec.md §3.10, §4.6).

Usage::

    python -m hisim.economics evaluate <results_dir> [--scenarios scenarios.json]
    python -m hisim.economics explain <results_dir> --value "<perspective>/<field-path>"
    python -m hisim.economics validate

**Why a CLI exists at all.** Three of these four commands are only possible because the evaluator
is a pure function of `economic_inputs.json` (the seam-1 contract, §4.6): a result directory can be
re-priced, explained or reported on years after the simulation ran, without HiSim's simulation
stack, the original system setup or the weather data. The fourth, `validate`, is the data-file CI
of §9.6 made runnable by hand after a price or catalog edit. None of them can run a simulation, and
none of them touches legacy cost outputs.

**One setup block, three commands.** `evaluate`, `explain` and `report` all need the same
assembly — stored inputs, the caller's `--parameters`, the cost database, the subsidy catalog, the
D7 resolution check, the applicable perspectives — and it lives exactly once, in
`_build_context`. That matters for more than tidiness: while each command assembled its own, they
drifted, and a directory could be explained under different assumptions than the ones it was
evaluated with. `--parameters` and `--subsidy-catalog` are therefore accepted by all three and mean
the same thing in each, the flag taking precedence over the path stored in the parameters file.

**The four subcommands and their contracts:**

- ``evaluate <results_dir> [--scenarios F] [--parameters F] [--subsidy-catalog DIR]`` — re-prices
  the stored inputs. Without ``--scenarios`` it evaluates the applicable perspectives and
  *overwrites* the directory's `lifecycle_costs.json`, `component_costs.*`,
  `cash_flow_timeline.csv`, `cost_provenance.json`, `cost_audit.csv` and `cost_audit.json`, then
  prints how many perspectives it wrote. With ``--scenarios`` it instead evaluates the §4.6 cube
  and writes `scenario_cube.csv`/`.json`, printing the number of cells. Exit 0 on success.
- ``explain <results_dir> --value "<perspective>/<field-path>" [--parameters F]
  [--subsidy-catalog DIR]`` — re-evaluates one perspective and traces one result value back
  through the provenance ledger to the data entries and sources behind it (§3.10), as text or,
  with ``--json``, as the machine-readable report. Exit 2 with a message on stderr when the
  ``--value`` argument has no ``/`` or names an unknown perspective.
- ``report <results_dir> [--compare DIR] [--scenarios F] [--parameters F]
  [--subsidy-catalog DIR]`` — writes the human-readable outputs (`cost_summary.md`,
  `lifecycle_report.html`, the PNG charts) for stored results, adding a variant comparison and/or
  a scenario section on request. Given a re-pricing flag it re-evaluates instead of rendering the
  stored numbers, and says so on stdout. Exit 2 when there is nothing to report or the two
  directories share no perspective.
- ``validate`` — runs `validation.validate_all` over the shipped data files, printing every warning
  and every error followed by a count. **Exit 1 when any error was found, 0 otherwise**; warnings
  never affect the exit code, which is what makes it usable as a CI gate. A failing check means the
  shipped data is internally inconsistent — an unsourced datapoint, a coverage or question-coverage
  hole, a malformed tariff contract — and the run that would have used it is not to be trusted.

**Where the assumptions come from.** ``--parameters`` states them; without the flag every
subcommand reads the parameters the run itself was priced under out of its `lifecycle_costs.json`
(`_load_parameters`). The engine defaults are never a fallback: a directory with neither the flag
nor a `lifecycle_costs.json` carrying its parameters is an error naming that file and the flag that
supplies them instead, because re-pricing an archived study at default assumptions answers a
question nobody asked. The subsidy catalog those parameters name is loaded for every subcommand,
`explain` included, through `SubsidyCatalog.load_configured` — a named catalog that cannot be
resolved is an error (D25), never a quiet fall-through to the §10.1 legacy flat shim.

Across all commands, an `UnresolvableSubjectsError` — the fail-fast of decision D7 — is caught in
`main` and turned into exit code 2 with the same message the postprocessing bridge logs. There are
no partial cost results and no ``--allow-drops`` escape. Every other `CostDataError` — a cost
database that will not load, a `--parameters` file that is not there (issue #23) — is caught in
the same place, and so is the ordinary shape of a bad invocation: an unreadable path, malformed
JSON, a rejected parameter value. A data or invocation problem is therefore a one-line message and
an exit code rather than a traceback, and never a silently substituted default. Anything else — a
genuine programming error — still propagates, because a traceback is the right report for it.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

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
from hisim.economics.subsidies import SubsidyCatalog
from hisim.economics.validation import validate_all


class CliFileNames:
    """Names of the files the CLI itself writes, as opposed to the export modules.

    Only the scenario cube is written here — every other output is named by `exports`,
    `input_audit` or the reporting layer — but it is written by two subcommands, so the two names
    live in one place rather than as literals in both.
    """

    SCENARIO_CUBE_CSV = "scenario_cube.csv"
    SCENARIO_CUBE_JSON = "scenario_cube.json"


class AuditLayerProbe:
    """Whether the module `evaluate` writes its audit files with is importable.

    `evaluate` writes `cost_audit.csv`/`.json` alongside the numeric exports, using
    `hisim.economics.audit`, and it used to reach that lazy import *after* four export files had
    already been written. An installation without the module — or a stack state in which it had
    not been merged yet, which is how this was found — therefore left a half-written directory
    behind that a later `report` would happily render as complete. The probe answers the same
    question before anything is written, so the subcommand refuses instead of half-succeeding.
    """

    #: Module that must be importable for `evaluate` to write a complete export set.
    MODULE_NAME = "hisim.economics.audit"

    @classmethod
    def require(cls) -> None:
        """Raises unless the audit layer is importable.

        Uses `importlib.util.find_spec`, so the check costs a path lookup and does not import
        anything — the lazy imports at the use sites stay where they are.

        Raises:
            CostDataError: If the module is absent. `main` turns it into exit code 2 with the
                message on stderr, before any output file exists.
        """
        if importlib.util.find_spec(cls.MODULE_NAME) is not None:
            return
        raise CostDataError(
            f"{cls.MODULE_NAME} is not importable, so `evaluate` cannot write the input audit that "
            "belongs to a stored evaluation (W4.5) and would leave a half-written export set "
            "behind. Nothing was written."
        )


@dataclass(frozen=True)
class EvaluationContext:
    """Everything one re-pricing invocation needs, assembled once (§4.6).

    `evaluate`, `explain` and `report` all begin the same way — read the stored inputs, load the
    caller's assumptions and the data files behind them, refuse the run if any subject cannot be
    priced (D7), work out which perspectives apply — and that block used to be pasted into each of
    them. The copies had already drifted: one honored `--subsidy-catalog`, one read only the
    parameters file's path, and `explain` loaded no catalog at all and therefore traced different
    numbers than the run had published. Holding the assembled state in one record is what makes
    the three commands agree by construction.

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
    """The economic assumptions for this invocation: `--parameters`, or the run's own.

    Shared by every subcommand so they all price identically, with a two-step resolution:

    1. ``--parameters <file>`` — the caller states the assumptions, which is what re-pricing an
       archived study under *new* assumptions means (§4.6).
    2. otherwise the assumptions the run itself was priced under, read back from its
       `lifecycle_costs.json` (`serialization.read_stored_parameters`).

    Step 2 is the fix for a defect that made `explain` unusable on a real run: without
    ``--parameters`` every subcommand priced with `EconomicParameters()`, so a run evaluated at
    price basis year 2026 with a subsidy catalog was re-evaluated at the default basis year with
    none — the explained numbers were not the run's, and on data valid from 2026 the invocation
    died on the D7 resolution check instead. The parameters travel with the artifacts; the CLI now
    reads them.

    Neither source is allowed to fall back to the engine defaults. A directory holding only
    `economic_inputs.json` has no stored assumptions, and pricing it silently at the defaults is
    exactly the failure this function exists to prevent — so it fails, naming the file it looked
    in and the flag that would supply them. *Passing* a path that does not exist fails for the same
    reason (issue #23).

    Args:
        args: The parsed CLI namespace, for `--parameters`.
        results_dir: The invocation's result directory. Every caller is a subcommand that has one
            (`_build_context` passes its `results_dir`); the parameter is optional only so the
            signature reads the same as the resolution it performs.

    Returns:
        The caller's parameters, or the ones stored with the run.

    Raises:
        CostDataError: If `--parameters` names a path that is not a readable file, or no path was
            given and the directory carries no stored parameters. `main` turns both into exit code
            2 with the message on stderr.
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
    """The one setup block behind `evaluate`, `explain` and `report` (§4.6).

    Reads the directory's stored inputs, resolves the assumptions and the two data sources, binds
    an evaluator to them, runs the D7 resolution check and selects the applicable perspectives —
    in that order, because the resolution check has to see the same database and catalog the
    evaluation will use.

    The catalog precedence is the one policy decided here: `--subsidy-catalog` wins over
    `parameters.subsidy_catalog_path`, so an archived assumption set can be re-priced against a
    different catalog without editing it, and a run configured through the parameters file still
    loads its catalog when no flag is given.

    Args:
        results_dir: Directory holding `economic_inputs.json`.
        args: The parsed CLI namespace; `--parameters` and `--subsidy-catalog` are read from it.

    Returns:
        The assembled context, ready for `_evaluate_perspectives` or a single `evaluate` call.

    Raises:
        UnresolvableSubjectsError: If any cost subject cannot be priced (D7) — no partial results.
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
    """Evaluates every applicable perspective of the context into one matrix (§7.1).

    The evaluate-all loop, in one place for the same reason as `_build_context`: `evaluate` and the
    `report` fallback path must produce identical matrices for identical inputs, and a matrix
    missing a perspective is not visible in any output.

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
    """Evaluates the §4.6 scenario cube and writes `scenario_cube.csv`/`.json`.

    Shared by `evaluate --scenarios` and `report --scenarios`, which differ only in what they do
    with the returned cube: the first prints a cell count, the second renders a report section
    from it. A cube is a set of fresh evaluations by definition, so this path always runs the
    engine whether or not the directory holds stored results.

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
    """``evaluate``: re-price a stored result directory, or sweep it (§4.6).

    Reads `economic_inputs.json`, applies the caller's parameters and catalog, runs the D7
    resolution check and then either evaluates the applicable perspectives — overwriting the
    directory's export set in place, audit included so a later `report` needs no cost database
    (W4.5) — or, with ``--scenarios``, evaluates the scenario cube instead and writes only
    `scenario_cube.csv`/`.json`. The two are exclusive: a scenario run does not refresh the base
    exports.

    The audit-layer probe runs first, before any file is opened for writing, so a stack state in
    which `hisim.economics.audit` is not merged yet fails with a message instead of leaving a
    partly refreshed export set behind.

    This is the command that makes "new interest-rate assumptions" or "an updated subsidy catalog"
    a second-long operation on an archived study rather than a re-simulation.

    Returns:
        0. Failure surfaces as an exception — `UnresolvableSubjectsError` and every other
        `CostDataError` become exit 2 in `main`, and so does a malformed scenario file.
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
        # The audit belongs to the stored evaluation: without it a later `report` could not
        # render section 1 without reopening the cost database (W4.5). The probe above has
        # already established that this import will succeed.
        from hisim.economics.audit import (
            build_input_audit,
            write_cost_audit,
        )

        audit = build_input_audit(context.inputs, context.database, context.parameters, first)
        write_cost_audit(audit, args.results_dir)
        write_input_audit(audit, args.results_dir)
    print(f"Re-evaluated {len(matrix.results)} perspectives into {args.results_dir}.")
    return 0


def _cmd_explain(args: argparse.Namespace) -> int:
    """``explain``: trace one result value back to the data entries and sources behind it (§3.10).

    Takes ``--value "<perspective>/<field-path>"``, re-evaluates that single perspective from the
    stored inputs and asks the result to explain the named field: which parameters entered it, where
    each came from (database entry with its `valid_from_year`, config override with its
    `override_source`, scenario overlay, engine default, legacy shim) and which registry sources
    back them. This is the on-demand counterpart of the eager `cost_audit.csv`, and the answer to
    "is this number defensible" for any single number.

    It re-evaluates rather than reading stored results because the provenance ledger is what is being
    queried, and it is built during evaluation. It builds its context exactly like `evaluate`,
    subsidy catalog included: while it did not, a catalog-configured run was explained through the
    flat shim path, so the trace described numbers the run had never published.

    Returns:
        0 on success; 2 with a message on stderr when ``--value`` is malformed (no ``/``) or names a
        perspective that is not applicable to this directory.
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
    """The fallback path: re-price a directory's `economic_inputs.json` from scratch.

    Used by `report` when a directory holds inputs but no stored evaluation, and when a re-pricing
    flag makes the stored evaluation the wrong thing to render. It does the full engine run —
    database, catalog, resolution check, every applicable perspective — plus the input audit, so
    the caller gets exactly what `read_results` + `read_input_audit` would have returned for a
    directory that had them.

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
    """The flags on this invocation that change the assumptions a result is priced under.

    `report` renders stored results by default, which is what keeps it a rendering step (W4.5). A
    caller who passes `--parameters` or `--subsidy-catalog` is asking for something else, and those
    flags used to be accepted and then ignored: the report came out under the *original*
    assumptions, and with `--scenarios` it mixed a freshly evaluated cube into a stored base — an
    internally inconsistent document with nothing in it to say so.

    Args:
        args: The parsed CLI namespace.

    Returns:
        The names of the re-pricing flags that were given, in flag order; empty when none were.
    """
    given = (
        (getattr(args, "parameters", None), "--parameters"),
        (getattr(args, "subsidy_catalog", None), "--subsidy-catalog"),
    )
    return [name for value, name in given if value]


def _load_or_evaluate(
    results_dir: str, args: argparse.Namespace, label: str
) -> "Tuple[EvaluationMatrix, Optional[InputAuditReport]]":
    """Stored results if the directory has them and nothing re-prices them, else a fresh run (W4.5).

    Reporting is supposed to *render* an evaluation, not perform one — the docstring said so
    long before the code did. A directory written by `evaluate`, by the postprocessing bridge or
    by an earlier `report` carries everything the reports need, so it is rendered as it stands.

    Two things send this to the engine instead: a directory holding nothing but
    `economic_inputs.json`, and a re-pricing flag (`_repricing_flags`), which is a request to
    render *these* assumptions rather than the stored ones.

    The first of those only gets as far as the engine *with* `--parameters`. An inputs-only
    directory carries no stored assumptions to price under, and the engine defaults are never a
    fallback (`_load_parameters`), so without the flag the re-evaluation it announces fails
    immediately with the message naming both ways to supply them.

    The distinction matters to a reader of the output: rendered stored results show the numbers the
    original run published, while a re-priced directory shows what today's data and the given
    parameters say about the same physical facts. The printed line is the only signal of which
    happened, so both re-pricing paths print one.

    Args:
        results_dir: Directory to load or evaluate.
        args: The parsed CLI namespace, for `--parameters` and `--subsidy-catalog`.
        label: How to name the directory in the printed message (the caller passes the path).

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
    """Writes cost_summary.md, lifecycle_report.html and the PNG charts for stored results.

    ``report`` is the human-facing command: it renders the plausibility panel and the report
    sections that follow the money along the calculation chain, from the input audit through the
    year-0 investment build-up to the perspective and per-component views. It prefers *stored*
    results and re-prices only when the directory has none or when a re-pricing flag asks it to
    (`_load_or_evaluate`), which is what keeps reporting a rendering step rather than a second
    evaluation (W4.5).

    Two optional additions. ``--compare <reference_dir>`` loads a second directory, picks a shared
    perspective (preferring `brownfield_net`, then `greenfield_net`) and adds the variant-comparison
    section — delta waterfall, discounted payback band, warm-rent change — plus the payback PNG;
    it goes through the same load-or-evaluate path, so both sides of a comparison are always priced
    under the same assumptions. ``--scenarios`` evaluates a §4.6 cube for the scenario section;
    that branch always needs the engine, since a cube is a set of fresh evaluations by definition,
    and it also writes `scenario_cube.csv`/`.json`.

    Returns:
        0 on success; 2 with a message on stderr when the directory yields no results, or when the
        two compared directories share no perspective.
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
    )
    # One function owns the PNG set: handing it the comparison's reference makes it write the
    # payback curve as part of that set, rather than the CLI writing a fifth file beside it under
    # a name only it knew. `write_report_plots` picks the variant side by perspective id, which is
    # the same result this call site used to look up.
    write_report_plots(matrix, args.results_dir, reference_result)
    print(
        f"Wrote cost_summary.md, lifecycle_report.html and PNG charts to {args.results_dir} "
        f"({len(plausibility)} plausibility checks, {len(plausibility.flagged())} flagged)."
    )
    return 0


def _cmd_validate(_args: argparse.Namespace) -> int:
    """``validate``: run the §9.6 data-file CI checks over the shipped data, as a CI gate.

    Takes no arguments and always checks the shipped `hisim/cost_database/` and
    `hisim/subsidy_catalog/`. Prints every warning, then every error, then a count line; the same
    checks run in CI, so this is what to execute after editing a price, adding a source or writing a
    subsidy scheme, before opening the PR.

    Returns:
        0 when there are no errors (warnings alone do not fail it), 1 otherwise. A non-zero exit
        means the shipped data is internally inconsistent — an unsourced datapoint, a coverage or
        question-coverage hole, a malformed tariff contract, an exclusion naming a scheme that does
        not exist — and any run using it would be untrustworthy rather than merely imperfect.
    """
    report = validate_all()
    for warning in report.warnings:
        print(f"WARNING: {warning}")
    for error in report.errors:
        print(f"ERROR: {error}")
    print(f"{len(report.errors)} errors, {len(report.warnings)} warnings.")
    return 0 if report.ok else 1


def main(argv=None) -> int:
    """Entry point.

    Builds the four subparsers (each documented in the module docstring), dispatches to the matching
    `_cmd_*` handler and returns its exit code. The one piece of behaviour that lives here rather
    than in a handler is the error contract. `UnresolvableSubjectsError` is caught for *every*
    subcommand and turned into exit code 2 with the error on stderr — the same message the
    postprocessing bridge logs — so no entry point can ever emit a partial cost result. Every other
    `CostDataError` and the ordinary shapes of a bad invocation (`OSError` for an unreadable path,
    `json.JSONDecodeError` for a malformed file, `ValueError` for a rejected value) are reported the
    same way, because the module docstring promises a message rather than a traceback for a data or
    invocation problem. Any other exception type propagates: that is a defect, and a traceback is
    the right report for it.

    Args:
        argv: Argument list, defaulting to `sys.argv[1:]`; passed explicitly by the CLI tests.

    Returns:
        The subcommand's exit code, or 2 for an unresolvable subject or a data/invocation problem.
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

    validate_parser = subparsers.add_parser("validate", help="data-file CI checks (§9.6)")
    validate_parser.set_defaults(func=_cmd_validate)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except UnresolvableSubjectsError as err:
        # D7 (cost-spec-v2 §8): evaluate/explain/report all refuse to produce partial cost
        # results; the same message the bridge logs goes to stderr with a non-zero exit code.
        print(str(err), file=sys.stderr)
        return 2
    except CostDataError as err:
        # Everything else the data layer refuses to do — an unloadable database, a missing
        # `--parameters` file (issue #23). Same channel, same exit code: the caller asked for a
        # priced result and is told why there is none instead of getting one built on defaults.
        print(str(err), file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError, ValueError) as err:
        # The ordinary shapes of a bad invocation: a directory that is not there, a JSON file that
        # will not parse, a parameter value the record rejects. A traceback would be the wrong
        # report for any of them — the caller mistyped something, they did not hit a bug.
        print(f"{type(err).__name__}: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
