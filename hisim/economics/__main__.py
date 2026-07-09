"""CLI of the lifecycle cost engine (cost_spec.md §3.10, §4.6).

Usage::

    python -m hisim.economics evaluate <results_dir> [--scenarios scenarios.json]
    python -m hisim.economics explain <results_dir> --value "<perspective>/<field-path>"
    python -m hisim.economics validate
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator
from hisim.economics.exports import (
    write_cash_flow_timeline,
    write_component_costs,
    write_lifecycle_costs_json,
    write_provenance_ledger,
)
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import load_default_bundle, select_applicable
from hisim.economics.scenarios import ScenarioSet, evaluate_cube, export_cube_csv, export_cube_json
from hisim.economics.serialization import read_inputs
from hisim.economics.subsidies import SubsidyCatalog
from hisim.economics.validation import validate_all


def _load_parameters(args: argparse.Namespace) -> EconomicParameters:
    if args.parameters and os.path.isfile(args.parameters):
        with open(args.parameters, encoding="utf-8") as file:
            parameters: EconomicParameters = EconomicParameters.from_dict(json.load(file))
            return parameters
    return EconomicParameters()


def _cmd_evaluate(args: argparse.Namespace) -> int:
    inputs = read_inputs(args.results_dir)
    parameters = _load_parameters(args)
    database = CostDatabase(parameters.cost_database_path)
    catalog = None
    if parameters.subsidy_catalog_path or args.subsidy_catalog:
        catalog = SubsidyCatalog.load(parameters.country, args.subsidy_catalog or parameters.subsidy_catalog_path)
    perspectives = select_applicable(load_default_bundle(), has_register=inputs.existing_assets is not None)
    if args.scenarios:
        with open(args.scenarios, encoding="utf-8") as file:
            scenario_set = ScenarioSet.from_json(json.load(file))
        cube = evaluate_cube(inputs, parameters, perspectives, scenario_set, database, catalog)
        export_cube_csv(cube, os.path.join(args.results_dir, "scenario_cube.csv"))
        export_cube_json(cube, os.path.join(args.results_dir, "scenario_cube.json"))
        print(f"Wrote scenario_cube.csv/.json for {sum(len(v) for v in cube.results.values())} cells.")
        return 0
    evaluator = EconomicEvaluator(database, parameters, catalog)
    from hisim.economics.results import EvaluationMatrix

    matrix = EvaluationMatrix()
    for perspective in perspectives:
        matrix.results[perspective.id] = evaluator.evaluate(inputs, perspective)
    write_lifecycle_costs_json(matrix, args.results_dir)
    write_component_costs(matrix, args.results_dir)
    write_cash_flow_timeline(matrix, args.results_dir)
    write_provenance_ledger(matrix, args.results_dir)
    print(f"Re-evaluated {len(matrix.results)} perspectives into {args.results_dir}.")
    return 0


def _cmd_explain(args: argparse.Namespace) -> int:
    inputs = read_inputs(args.results_dir)
    parameters = _load_parameters(args)
    database = CostDatabase(parameters.cost_database_path)
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
    evaluator = EconomicEvaluator(database, parameters)
    result = evaluator.evaluate(inputs, perspectives[0])
    report = result.explain(value_path)
    if args.json:
        print(json.dumps(report.to_json(), indent=2))
    else:
        print(report.render_text())
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    """Writes cost_summary.md, lifecycle_report.html and the PNG charts for stored results."""
    from hisim.economics.report_plots import plot_payback_curve, write_report_plots
    from hisim.economics.reporting import run_plausibility_checks, write_cost_summary, write_lifecycle_report
    from hisim.economics.results import EvaluationMatrix, compare

    inputs = read_inputs(args.results_dir)
    parameters = _load_parameters(args)
    database = CostDatabase(parameters.cost_database_path)
    catalog = None
    if parameters.subsidy_catalog_path:
        catalog = SubsidyCatalog.load(parameters.country, parameters.subsidy_catalog_path)
    perspectives = select_applicable(load_default_bundle(), has_register=inputs.existing_assets is not None)
    evaluator = EconomicEvaluator(database, parameters, catalog)
    matrix = EvaluationMatrix()
    for perspective in perspectives:
        matrix.results[perspective.id] = evaluator.evaluate(inputs, perspective)

    comparison = None
    reference_result = None
    if args.compare:
        reference_inputs = read_inputs(args.compare)
        reference_perspectives = select_applicable(
            load_default_bundle(), has_register=reference_inputs.existing_assets is not None
        )
        reference_matrix = EvaluationMatrix()
        for perspective in reference_perspectives:
            reference_matrix.results[perspective.id] = evaluator.evaluate(reference_inputs, perspective)
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
        from hisim.economics.scenarios import ScenarioSet, evaluate_cube, export_cube_csv, export_cube_json

        with open(args.scenarios, encoding="utf-8") as file:
            scenario_set = ScenarioSet.from_json(json.load(file))
        scenario_cube = evaluate_cube(inputs, parameters, perspectives, scenario_set, database, catalog)
        export_cube_csv(scenario_cube, os.path.join(args.results_dir, "scenario_cube.csv"))
        export_cube_json(scenario_cube, os.path.join(args.results_dir, "scenario_cube.json"))

    checks = run_plausibility_checks(matrix, inputs)
    write_cost_summary(matrix, inputs, checks, args.results_dir, comparison)
    write_lifecycle_report(
        matrix, inputs, database, checks, args.results_dir, comparison, reference_result,
        scenario_cube=scenario_cube,
    )
    write_report_plots(matrix, args.results_dir)
    if comparison is not None and reference_result is not None:
        plot_payback_curve(
            reference_result,
            matrix.results[comparison.perspective_id],
            os.path.join(args.results_dir, "lifecycle_payback_curve.png"),
        )
    bad = sum(1 for check in checks if check.status != "PASS")
    print(
        f"Wrote cost_summary.md, lifecycle_report.html and PNG charts to {args.results_dir} "
        f"({len(checks)} plausibility checks, {bad} flagged)."
    )
    return 0


def _cmd_validate(_args: argparse.Namespace) -> int:
    report = validate_all()
    for warning in report.warnings:
        print(f"WARNING: {warning}")
    for error in report.errors:
        print(f"ERROR: {error}")
    print(f"{len(report.errors)} errors, {len(report.warnings)} warnings.")
    return 0 if report.ok else 1


def main(argv=None) -> int:
    """Entry point."""
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
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
