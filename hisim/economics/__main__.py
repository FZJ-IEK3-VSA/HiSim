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

**The four subcommands and their contracts:**

- ``evaluate <results_dir> [--scenarios F] [--parameters F] [--subsidy-catalog DIR]`` — re-prices
  the stored inputs. Without ``--scenarios`` it evaluates the applicable perspectives and
  *overwrites* the directory's `lifecycle_costs.json`, `component_costs.*`,
  `cash_flow_timeline.csv`, `cost_provenance.json`, `cost_audit.csv` and `cost_audit.json`, then
  prints how many perspectives it wrote. With ``--scenarios`` it instead evaluates the §4.6 cube
  and writes `scenario_cube.csv`/`.json`, printing the number of cells. Exit 0 on success.
- ``explain <results_dir> --value "<perspective>/<field-path>"`` — re-evaluates one perspective and
  traces one result value back through the provenance ledger to the data entries and sources behind
  it (§3.10), as text or, with ``--json``, as the machine-readable report. Exit 2 with a message on
  stderr when the ``--value`` argument has no ``/`` or names an unknown perspective.
- ``report <results_dir> [--compare DIR] [--scenarios F]`` — writes the human-readable outputs
  (`cost_summary.md`, `lifecycle_report.html`, the PNG charts) for stored results, adding a variant
  comparison and/or a scenario section on request. Exit 2 when there is nothing to report or the
  two directories share no perspective.
- ``validate`` — runs `validation.validate_all` over the shipped data files, printing every warning
  and every error followed by a count. **Exit 1 when any error was found, 0 otherwise**; warnings
  never affect the exit code, which is what makes it usable as a CI gate. A failing check means the
  shipped data is internally inconsistent — an unsourced datapoint, a coverage or question-coverage
  hole, a malformed tariff contract — and the run that would have used it is not to be trusted.

**Where the assumptions come from.** ``--parameters`` states them; without the flag every
subcommand reads the parameters the run itself was priced under out of its `lifecycle_costs.json`
(`_load_parameters`). The engine defaults are never a fallback: a directory that carries neither is
an error naming both files, because re-pricing an archived study at default assumptions answers a
question nobody asked. The subsidy catalog those parameters name is loaded by every subcommand,
`explain` included, through `SubsidyCatalog.load_configured` — a named catalog that cannot be
resolved is an error (D25), never a quiet fall-through to the §10.1 legacy flat shim.

Across all commands, an `UnresolvableSubjectsError` — the fail-fast of decision D7 — is caught in
`main` and turned into exit code 2 with the same message the postprocessing bridge logs. There are
no partial cost results and no ``--allow-drops`` escape. Every other `CostDataError` — a cost
database that will not load, a `--parameters` file that is not there (issue #23) — and every
`SubsidyDataError` is caught in the same place and reported the same way, so a data or invocation
problem is a message and an exit code rather than a traceback, and never a silently substituted
default.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional, Tuple

from hisim.economics.database import CostDatabase, CostDataError
from hisim.economics.evaluator import (
    EconomicEvaluator,
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
from hisim.economics.perspectives import load_default_bundle, select_applicable
from hisim.economics.results import EvaluationMatrix
from hisim.economics.scenarios import ScenarioSet, evaluate_cube, export_cube_csv, export_cube_json
from hisim.economics.serialization import read_inputs, read_results, read_stored_parameters
from hisim.economics.subsidies import SubsidyCatalog, SubsidyDataError
from hisim.economics.validation import validate_all


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
    exactly the failure this function exists to prevent — so it fails and names the two files.

    Args:
        args: The parsed CLI namespace, for `--parameters`.
        results_dir: The invocation's result directory, or None for a subcommand that has none.

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


def _cmd_evaluate(args: argparse.Namespace) -> int:
    """``evaluate``: re-price a stored result directory, or sweep it (§4.6).

    Reads `economic_inputs.json`, applies the caller's parameters and catalog, runs the D7
    resolution check and then either evaluates the applicable perspectives — overwriting the
    directory's export set in place, audit included so a later `report` needs no cost database
    (W4.5) — or, with ``--scenarios``, evaluates the scenario cube instead and writes only
    `scenario_cube.csv`/`.json`. The two are exclusive: a scenario run does not refresh the base
    exports.

    This is the command that makes "new interest-rate assumptions" or "an updated subsidy catalog"
    a second-long operation on an archived study rather than a re-simulation.

    Returns:
        0. Failure surfaces as an exception — `UnresolvableSubjectsError` becomes exit 2 in `main`,
        an unreadable directory or malformed scenario file propagates.
    """
    inputs = read_inputs(args.results_dir)
    parameters = _load_parameters(args, args.results_dir)
    database = CostDatabase(parameters.cost_database_path)
    catalog = SubsidyCatalog.load_configured(
        parameters.country, parameters.subsidy_catalog_path, args.subsidy_catalog
    )
    evaluator = EconomicEvaluator(database, parameters, catalog)
    require_resolvable_subjects(inputs, evaluator)
    perspectives = select_applicable(load_default_bundle(), has_register=inputs.existing_assets is not None)
    if args.scenarios:
        with open(args.scenarios, encoding="utf-8") as file:
            scenario_set = ScenarioSet.from_json(json.load(file))
        cube = evaluate_cube(inputs, parameters, perspectives, scenario_set, database, catalog)
        export_cube_csv(cube, os.path.join(args.results_dir, "scenario_cube.csv"))
        export_cube_json(cube, os.path.join(args.results_dir, "scenario_cube.json"))
        print(f"Wrote scenario_cube.csv/.json for {sum(len(v) for v in cube.results.values())} cells.")
        return 0
    matrix = EvaluationMatrix()
    for perspective in perspectives:
        matrix.results[perspective.id] = evaluator.evaluate(inputs, perspective)
    write_lifecycle_costs_json(matrix, args.results_dir)
    write_component_costs(matrix, args.results_dir)
    write_cash_flow_timeline(matrix, args.results_dir)
    write_provenance_ledger(matrix, args.results_dir)
    first = next(iter(matrix.results.values()), None)
    if first is not None:
        # The audit belongs to the stored evaluation: without it a later `report` could not
        # render section 1 without reopening the cost database (W4.5).
        from hisim.economics.audit import build_input_audit, write_cost_audit
        from hisim.economics.report_plots import write_audit_plots

        audit = build_input_audit(inputs, database, parameters, first)
        write_cost_audit(audit, args.results_dir)
        write_input_audit(audit, args.results_dir)
        # V6 travels with the audit tables, not with the report (owner decision Q9).
        write_audit_plots(first, args.results_dir)
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
    queried, and it is built during evaluation. That re-evaluation is the run's, not a generic one:
    the assumptions come from `_load_parameters` (the run's stored parameters unless the caller
    overrides them) and the subsidy catalog they name is loaded, so the explained subsidy values are
    the run's real BEG decisions. Before this the command loaded no catalog at all and every
    subsidy-derived value was explained through the §10.1 flat-shim path, which is not what the run
    did.

    Returns:
        0 on success; 2 with a message on stderr when ``--value`` is malformed (no ``/``) or names a
        perspective that is not applicable to this directory.
    """
    inputs = read_inputs(args.results_dir)
    parameters = _load_parameters(args, args.results_dir)
    database = CostDatabase(parameters.cost_database_path)
    catalog = SubsidyCatalog.load_configured(parameters.country, parameters.subsidy_catalog_path)
    if "/" not in args.value:
        print("--value must have the form '<perspective>/<field-path>'", file=sys.stderr)
        return 2
    perspective_id, value_path = args.value.split("/", 1)
    perspectives = [
        perspective
        for perspective in select_applicable(load_default_bundle(), inputs.existing_assets is not None)
        if perspective.id == perspective_id
    ]
    if not perspectives:
        print(f"Unknown perspective {perspective_id!r}.", file=sys.stderr)
        return 2
    evaluator = EconomicEvaluator(database, parameters, catalog)
    require_resolvable_subjects(inputs, evaluator)
    result = evaluator.evaluate(inputs, perspectives[0])
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

    Used by `report` when a directory holds inputs but no stored evaluation. It does the full
    engine run — database, catalog, resolution check, every applicable perspective — plus the input
    audit, so the caller gets exactly what `read_results` + `read_input_audit` would have returned
    for a directory that had them.

    Args:
        results_dir: Directory holding `economic_inputs.json`.
        args: The parsed CLI namespace, for `--parameters`.

    Returns:
        The evaluated matrix and its input audit (None only when no perspective was evaluated).
    """
    from hisim.economics.audit import build_input_audit
    from hisim.economics.results import EvaluationMatrix

    inputs = read_inputs(results_dir)
    parameters = _load_parameters(args, results_dir)
    database = CostDatabase(parameters.cost_database_path)
    catalog = SubsidyCatalog.load_configured(parameters.country, parameters.subsidy_catalog_path)
    evaluator = EconomicEvaluator(database, parameters, catalog)
    require_resolvable_subjects(inputs, evaluator)
    perspectives = select_applicable(load_default_bundle(), has_register=inputs.existing_assets is not None)
    matrix = EvaluationMatrix()
    for perspective in perspectives:
        matrix.results[perspective.id] = evaluator.evaluate(inputs, perspective)
    first = next(iter(matrix.results.values()), None)
    audit = build_input_audit(inputs, database, parameters, first) if first is not None else None
    return matrix, audit


def _load_or_evaluate(
    results_dir: str, args: argparse.Namespace, label: str
) -> "Tuple[EvaluationMatrix, Optional[InputAuditReport]]":
    """Stored results if the directory has them, otherwise a fresh evaluation (W4.5).

    Reporting is supposed to *render* an evaluation, not perform one — the docstring said so
    long before the code did. A directory written by `evaluate`, by the postprocessing bridge or
    by an earlier `report` carries everything the reports need; only a directory holding nothing
    but `economic_inputs.json` still has to be priced, and then it says so.

    The distinction matters to a reader of the output: rendered stored results show the numbers the
    original run published, while a re-priced directory shows what *today's* data and parameters
    say about the same physical facts. The printed line is the only signal of which happened.

    Args:
        results_dir: Directory to load or evaluate.
        args: The parsed CLI namespace, for `--parameters`.
        label: How to name the directory in the fallback message (the caller passes the path).

    Returns:
        The matrix and the input audit, from storage or from a fresh evaluation.
    """
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
    results and only re-prices when the directory has none (`_load_or_evaluate`), which is what
    keeps reporting a rendering step rather than a second evaluation (W4.5).

    Two optional additions. ``--compare <reference_dir>`` loads a second directory, picks a shared
    perspective (preferring `brownfield_net`, then `greenfield_net`) and adds the variant-comparison
    section — delta waterfall, discounted payback band, warm-rent change — plus the payback PNG.
    ``--scenarios`` evaluates a §4.6 cube for the scenario section; that branch always needs the
    engine, since a cube is a set of fresh evaluations by definition, and it also writes
    `scenario_cube.csv`/`.json`.

    Returns:
        0 on success; 2 with a message on stderr when the directory yields no results, or when the
        two compared directories share no perspective.
    """
    from hisim.economics.plausibility import run_plausibility_checks
    from hisim.economics.report_plots import plot_payback_curve, write_report_plots
    from hisim.economics.reporting import write_cost_summary, write_lifecycle_report
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
        # The scenario cube is a fresh cube of evaluations by definition (§4.6), so this branch
        # needs the engine whether or not stored results exist.
        from hisim.economics.scenarios import ScenarioSet, evaluate_cube, export_cube_csv, export_cube_json

        inputs = read_inputs(args.results_dir)
        parameters = _load_parameters(args, args.results_dir)
        database = CostDatabase(parameters.cost_database_path)
        catalog = SubsidyCatalog.load_configured(parameters.country, parameters.subsidy_catalog_path)
        evaluator = EconomicEvaluator(database, parameters, catalog)
        require_resolvable_subjects(inputs, evaluator)
        perspectives = select_applicable(load_default_bundle(), has_register=inputs.existing_assets is not None)
        with open(args.scenarios, encoding="utf-8") as file:
            scenario_set = ScenarioSet.from_json(json.load(file))
        scenario_cube = evaluate_cube(inputs, parameters, perspectives, scenario_set, database, catalog)
        export_cube_csv(scenario_cube, os.path.join(args.results_dir, "scenario_cube.csv"))
        export_cube_json(scenario_cube, os.path.join(args.results_dir, "scenario_cube.json"))

    plausibility = run_plausibility_checks(matrix)
    write_cost_summary(matrix, plausibility, args.results_dir, comparison)
    write_lifecycle_report(
        matrix, plausibility, args.results_dir, audit, comparison, scenario_cube=scenario_cube,
        reference_result=reference_result,
    )
    # The reference goes to the plot set too: the comparison bridge and the fixed-interest
    # benchmark decompose it and cannot be drawn from the comparison object alone.
    write_report_plots(matrix, args.results_dir, reference_result)
    if comparison is not None and reference_result is not None:
        plot_payback_curve(
            reference_result,
            matrix.results[comparison.perspective_id],
            os.path.join(args.results_dir, "lifecycle_payback_curve.png"),
        )
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
    than in a handler is the D7 contract: `UnresolvableSubjectsError` is caught for *every*
    subcommand and turned into exit code 2 with the error on stderr — the same message the
    postprocessing bridge logs — so no entry point can ever emit a partial cost result.

    Args:
        argv: Argument list, defaulting to `sys.argv[1:]`; passed explicitly by the CLI tests.

    Returns:
        The subcommand's exit code, or 2 for an unresolvable subject.
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
    explain_parser.add_argument("--json", action="store_true")
    explain_parser.set_defaults(func=_cmd_explain)

    report_parser = subparsers.add_parser(
        "report", help="human-readable report + plausibility panel for stored results"
    )
    report_parser.add_argument("results_dir")
    report_parser.add_argument("--compare", help="reference result directory for a variant comparison")
    report_parser.add_argument("--parameters", help="EconomicParameters JSON file")
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
        # `--parameters` file (issue #23), a directory whose stored parameters are gone. Same
        # channel, same exit code: the caller asked for a priced result and is told why there is
        # none instead of getting one built on defaults.
        print(str(err), file=sys.stderr)
        return 2
    except SubsidyDataError as err:
        # A named subsidy catalog that does not resolve or does not parse (D25). It reaches the
        # user as a message rather than a traceback, and never as a result priced by the §10.1
        # flat shim under a catalog the caller believed was active.
        print(str(err), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
