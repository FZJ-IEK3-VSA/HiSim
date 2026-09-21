"""CLI of the lifecycle cost engine (cost_spec.md §3.10, §4.6).

Usage::

    python -m hisim.economics evaluate <results_dir> [--scenarios scenarios.json]
    python -m hisim.economics explain <results_dir> --value "<perspective>/<field-path>"
    python -m hisim.economics staged --stage <dir>:<from_year>:<label>[:<job_id>] ... --out <file>
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
- ``staged --stage <dir>:<from_year>:<label>[:<job_id>] ... [--parameters F] [--perspective ID]
  [--subsidy-catalog DIR] --out <file>`` — prices a renovation plan spread over several years out
  of finished jobs' stored inputs into `economics_result.json` (E-spec §3, §6). Its
  ``--parameters`` file is **not** an `EconomicParameters` record: it is the document's own
  `parameters` block, so a reader can feed a document's assumptions back in unchanged. Every key
  is optional — `horizon_years`, `interest_rate`, `country`, `price_basis_year`, `perspective_id`,
  `subsidy_mode` (`full`/`none`), `financing` (`{"kind": "cash"}` or `{"kind": "loan", …}`),
  `escalation`, and the two that are accepted and ignored, `simulation_year` and
  `subsidy_catalog`. **The country and the price basis year are the stages'**: both are written
  into every stage's `economic_inputs.json` as facts of the run, a value in the file is only
  checked against them, and stages that state neither over a file that states neither is a refusal
  rather than a silent `"DE"` or a basis year re-derived from the simulation year. Everything the
  block does not name stays what the stages were priced under. The subsidy catalogue is resolved
  the way the RenoVisor translator resolves it (step 11 §3): `--subsidy-catalog`, else a path the
  stages' stored record names, else the shipped `hisim/subsidy_catalog` directory when it holds
  `<COUNTRY>.json`, else none. Exit 0 with the document, 2 with a `problems.json` naming every
  offending key at once, 3 for an engine failure.
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
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Mapping, Optional, Tuple

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
from hisim.economics.serialization import (
    read_inputs,
    read_results,
    read_stored_country,
    read_stored_parameters,
    read_stored_price_basis_year,
)
from hisim.economics.staged import Stage, StagedEvaluationError, StagedEvaluator
from hisim.economics.staged_document import StagedDocument
from hisim.economics.staged_parameters import (
    ParameterKeys,
    ParameterProblem,
    ParameterProblemCodes,
    StagedParameters,
)
from hisim.renovisor.report import MappingReport
from hisim.renovisor.request import CatalogueTable
from hisim.economics.subsidies import SubsidyCatalog
from hisim.economics.validation import validate_all

if TYPE_CHECKING:  # The renderers are imported lazily, per subcommand; this is their return type.
    from hisim.economics.report_plots import PlotsWritten


class CliFileNames:
    """Names of the files the CLI itself writes, as opposed to the export modules.

    Only the scenario cube is written here — every other output is named by `exports`,
    `input_audit` or the reporting layer — but it is written by two subcommands, so the two names
    live in one place rather than as literals in both.
    """

    SCENARIO_CUBE_CSV = "scenario_cube.csv"
    SCENARIO_CUBE_JSON = "scenario_cube.json"


class AuditLayerProbe:
    """Whether everything `evaluate` writes its audit files with is importable.

    `evaluate` writes `cost_audit.csv`/`.json` alongside the numeric exports, using
    `hisim.economics.audit`, and it used to reach that lazy import *after* four export files had
    already been written. An installation without the module — or a stack state in which it had
    not been merged yet, which is how this was found — therefore left a half-written directory
    behind that a later `report` would happily render as complete. The probe answers the same
    question before anything is written, so the subcommand refuses instead of half-succeeding.

    The audit's ledger heatmap is part of that set (owner decision Q9), so the renderer and
    matplotlib under it are probed too: matplotlib is a dependency of the plain cost path and not
    only of the report path, and an environment without it must fail the same way — by name,
    before the first file — rather than four exports in. `bridge._require_plot_layer` makes the
    same check for the postprocessing path.
    """

    #: Modules that must be importable for `evaluate` to write a complete export set: the audit
    #: layer, the renderer of the audit's own figure, and the library that figure is drawn with.
    MODULE_NAMES = ("hisim.economics.audit", "matplotlib", "hisim.economics.report_plots")

    @classmethod
    def require(cls) -> None:
        """Raises unless every module the audit outputs need is importable.

        Uses `importlib.util.find_spec`, so the check costs a path lookup and does not import
        anything — the lazy imports at the use sites stay where they are.

        Raises:
            CostDataError: If any of them is absent. `main` turns it into exit code 2 with the
                message on stderr, before any output file exists.
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
    directory's export set in place, audit tables and their ledger heatmap PNG included, so a
    later `report` needs no cost database (W4.5) — or, with ``--scenarios``, evaluates the
    scenario cube instead and writes only `scenario_cube.csv`/`.json`. The two are exclusive: a
    scenario run does not refresh the base exports.

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
        from hisim.economics.report_plots import write_audit_plots

        audit = build_input_audit(context.inputs, context.database, context.parameters, first)
        write_cost_audit(audit, args.results_dir)
        write_input_audit(audit, args.results_dir)
        # V6 travels with the audit tables, not with the report (owner decision Q9): the ledger
        # heatmap answers the audit's question, so it is refreshed exactly when the audit is.
        # The renderer hands back what it did not draw rather than logging it; the CLI's way of
        # reporting is to print, so it prints.
        _print_plot_skips(write_audit_plots(first, args.results_dir))
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
        reference_result=reference_result,
    )
    # One function owns the PNG set: handing it the comparison's reference makes it write the
    # payback curve as part of that set, rather than the CLI writing a fifth file beside it under
    # a name only it knew. The comparison goes with it — this one carries the two directories as
    # its reference and variant ids, and recomputing it inside the renderer would relabel it with
    # the defaults.
    _print_plot_skips(write_report_plots(matrix, args.results_dir, reference_result, comparison))
    print(
        f"Wrote cost_summary.md, lifecycle_report.html and PNG charts to {args.results_dir} "
        f"({len(plausibility)} plausibility checks, {len(plausibility.flagged())} flagged)."
    )
    return 0


def _print_plot_skips(plots: "PlotsWritten") -> None:
    """Prints one line per chart the renderers did not draw, and nothing when they drew them all.

    The PNG writers return their skips instead of logging them, precisely so each caller can
    report in its own way: the postprocessing bridge logs them and leaves a file beside the
    images, and the CLI — whose whole output is stdout — prints them under the command that
    caused them. A figure missing without a stated reason is indistinguishable from a renderer
    that crashed and was swallowed, which is the failure this exists to prevent.

    Args:
        plots: The `report_plots.PlotsWritten` a writer returned.
    """
    for line in plots.lines():
        print(f"Chart not drawn: {line}")


class StagedCli:
    """Everything the ``staged`` subcommand decides, in one place (E-spec §6, step 10 §5).

    The subcommand prices a renovation plan spread over several years out of the stored
    ``economic_inputs.json`` of the jobs that simulated each of its states, and writes
    ``economics_result.json``. It runs no simulation, reads no network and finishes in well under
    a second for three stages, which is what lets a backend call it synchronously once the jobs it
    names have finished.

    Its exit contract is the one the backend branches on, and it is deliberately narrower than the
    other subcommands': **0** with the document, **2** with a ``problems.json`` beside it when the
    *plan* is refused (a missing input file, years that run backwards, stages priced under
    different conditions, an unknown perspective, a country with no data, any refused parameter
    key), and **3** with one line on standard error when the *engine* refuses (an unresolvable
    cost subject, D7). The difference matters because a 2 is something the caller can fix by
    sending a different plan and a 3 is not. There is no exit 2 without the file: an unreadable
    ``--parameters`` file is a refusal like any other rather than a traceback (shared todo B29).

    Its ``--parameters`` file is the document's own ``parameters`` block
    (:class:`~hisim.economics.staged_parameters.StagedParameters`), not an
    :class:`~hisim.economics.parameters.EconomicParameters` record: one vocabulary for what goes
    in and what comes out. Neither the country nor the price basis year is ever defaulted or
    re-derived — both are the ones the stages were priced with, read from their stored evaluation
    or from their stored inputs, and a value in the file is only checked against them.

    Example::

        python -m hisim.economics staged \
            --stage jobs/base:0:baseline --stage jobs/pkg:0:"stage 1":job-7 \
            --parameters economics.json --out economics_result.json
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
    #: E-spec calls it `brownfield_owner_subsidized_cash`; that id is an alias of this one and is
    #: not in the shipped bundle (step 10 §1). Defined once, beside the parameter block that may
    #: also name it, so the flag and the file cannot default differently.
    DEFAULT_PERSPECTIVE: ClassVar[str] = StagedParameters.DEFAULT_PERSPECTIVE_ID

    #: What the problems document is called, beside the requested output.
    PROBLEMS_FILE_NAME: ClassVar[str] = "problems.json"

    #: Exit code for a plan the evaluator refuses.
    PLAN_REFUSED: ClassVar[int] = 2

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

    #: Its two keys, taken from the class that writes the file so the two sides of the process
    #: seam cannot drift: a rename in ``MappingReport.to_json`` would otherwise silently drop
    #: every measure stamp and every unpriced flag from the document.
    SUBJECTS_KEY: ClassVar[str] = MappingReport.SUBJECTS_FIELD

    #: Its key holding the subjects the translator could not price.
    UNPRICED_KEY: ClassVar[str] = MappingReport.UNPRICED_SUBJECTS_FIELD

    #: Its key holding the measure lines, taken from the writer for the same reason.
    MEASURES_KEY: ClassVar[str] = MappingReport.MEASURES_FIELD

    #: The measure statuses a stage's ``measures`` list carries. A measure whose line reads
    #: ``not_implemented_yet`` is accepted and acted on by nothing, so naming it would tell the
    #: stage timeline the stage changed something it did not; ``defaulted`` is not a measure
    #: status at all.
    STAGE_MEASURE_STATUSES: ClassVar[Tuple[str, ...]] = ("used", "approximated")

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
        """Split one ``--stage`` argument into its directory, year, label and job id.

        Args:
            argument: The argument as typed.
            index: Its position among the ``--stage`` flags, so an error names which one.

        Returns:
            ``(directory, from_year, label, job_id)``; the job id is None when the argument has
            only three fields.

        Raises:
            StagedEvaluationError: If the argument has the wrong number of fields or its year is
                not an integer. Both are exit 2: the caller typed the plan.
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
        """Read one stage's stored inputs out of its job directory.

        Args:
            argument: The ``--stage`` argument.
            index: Its position, for the error messages.

        Returns:
            The stage and the job directory it was read from, the latter because the mapping
            report and the provenance file live beside the run rather than beside the inputs.

        Raises:
            StagedEvaluationError: If neither the directory nor its ``results`` subdirectory
                carries an ``economic_inputs.json``, which is a plan naming a job that has not
                finished rather than an engine fault.
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
        """The catalogue measure ids one stage acts on, from its mapping report.

        The stage timeline a frontend draws from the document needs to say which measures a
        stage carried out, and the only place that names them is the stage's own mapping report:
        its ``measures[]`` entries, filtered to the ids the translation actually acted on --
        ``used`` or ``approximated``, never ``not_implemented_yet`` -- and ordered by the
        catalogue, so two plans of the same measures read the same regardless of the order the
        requests happened to list them in. A stage without a report yields nothing here; the
        mapping read below refuses it, so a report-less stage never reaches the document.

        Args:
            directory: The ``--stage`` argument's first field.
            index: The stage's position, for the error message.
            argument: The argument as typed, for the error message.

        Returns:
            The measure ids, in catalogue order.

        Raises:
            StagedEvaluationError: When the report that is there does not read back as JSON.
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
        """Where one stage's stored inputs are: the directory itself, or its ``results``.

        A RenoVisor job directory holds the records, the mapping report and the payload, and puts
        the simulation's own outputs -- ``economic_inputs.json`` among them -- one level down in
        ``results/``. Accepting either is what lets the backend pass the job directory it already
        has instead of a path into it, while a bare directory holding only the stored inputs still
        works for a hand-run plan.

        Args:
            directory: The ``--stage`` argument's first field.

        Returns:
            The directory holding the inputs, or ``None`` when neither candidate does.
        """
        for candidate in (directory, os.path.join(directory, cls.RESULTS_SUBDIRECTORY)):
            if os.path.isfile(os.path.join(candidate, cls.INPUTS_FILE_NAME)):
                return candidate
        return None

    @classmethod
    def read_mapping(
        cls, directories: List[str], arguments: Optional[List[str]] = None
    ) -> Tuple[Dict[str, Optional[str]], List[str]]:
        """The subject-to-measure map and the unpriced subjects, over every stage directory.

        Every stage directory must carry the translator's ``mapping_report.json``, in itself or
        beside it: its ``subjects`` map says which catalogue measure created which cost subject
        and its ``unpriced_subjects`` list says which of them the request carried no price for.
        Later stages win over earlier ones, because a subject a later stage re-declares is the
        later stage's.

        A directory without one is refused rather than read as "nothing is unpriced". An unpriced
        subject reaches the engine with an investment of zero
        (``hisim/renovisor/economics.py``), and the flag is the only thing that distinguishes that
        zero from a price of nothing; defaulting it to ``False`` publishes a complete-looking
        total that understates the plan, which a reader of the document cannot detect.

        Args:
            directories: The stage directories, in stage order.
            arguments: The ``--stage`` arguments they came from, for the refusal message; the
                directories themselves when the caller does not pass them.

        Returns:
            ``(measure ids by subject, unpriced subjects)``.

        Raises:
            StagedEvaluationError: Naming the first directory with no report, which the CLI turns
                into exit 2 with a ``problems.json``.
        """
        spelled = arguments if arguments is not None else directories
        measures: Dict[str, Optional[str]] = {}
        unpriced: List[str] = []
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
        return measures, unpriced

    @classmethod
    def mapping_report_path(cls, directory: str) -> Optional[str]:
        """Where one stage's mapping report is: in the directory, or in its parent.

        A RenoVisor job writes the report beside its records and the simulation's own outputs one
        level down, so a caller who named the ``results`` subdirectory outright still finds it.

        Args:
            directory: The ``--stage`` argument's first field.

        Returns:
            The path, or ``None`` when neither candidate carries one.
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
        """The perspective the plan is priced under, by id, from the shipped bundle.

        Args:
            requested: The ``--perspective`` id, or None for :attr:`DEFAULT_PERSPECTIVE`.

        Returns:
            The perspective.

        Raises:
            StagedEvaluationError: If the bundle has no row with that id, listing the ids it has.
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

    #: Code of the refusal raised when one plan's stages do not agree about the country they
    #: were priced for — within one stage (its stored evaluation and its stored inputs say
    #: different things) or across them. A plan is one country's price data, and which country it
    #: is, is the stages' statement, so a contradiction in it has no answer the CLI could pick.
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
        """One stage's statement of one fact, out of the two files that may carry it.

        A finished job states the country it was priced for and the price basis year it priced at
        twice: in the parameters of its stored evaluation (``lifecycle_costs.json``) and in its
        stored inputs (``economic_inputs.json``, which carries both since step 13). Which of them
        a stage directory holds depends on who assembled it — a backend's worker ships the
        extract and the mapping report and nothing else — so both are read, and a directory
        carrying both has to say the same thing in each.

        Args:
            index: The stage's position, so a refusal names which one.
            directory: The directory the stage's inputs were read from.
            name: What the fact is called in the message, e.g. ``"country"``.
            code: The problem code a contradiction is published under.
            from_evaluation: What the stored evaluation says, or None.
            from_inputs: What the stored inputs say, or None.

        Returns:
            The fact, or None when neither file states it (an extract written before the keys
            existed, and no stored evaluation beside it).

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
        """The one value the stages state, or a refusal naming every stage that disagrees.

        A plan whose stages were priced for different countries has no single set of price data
        behind it, and one priced at different basis years has no single price level; pricing
        either anyway would put figures from two worlds on one axis.

        Args:
            stated: ``(directory, value)`` for every stage that states the fact, in stage order.
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
        """What the stages themselves say the plan is priced under, and that they say one thing.

        Every stage was simulated and priced by its own job, and a job leaves two statements of
        what it was priced under: the full parameter record in its ``lifecycle_costs.json``, and —
        because a backend's stage directory holds only the extract and the mapping report — the
        country and the resolved price basis year in its ``economic_inputs.json``. The record is
        the base the ``--parameters`` file overlays; those two are resolved separately, because
        they are the values a stage always states and the ones the file may never change.

        Args:
            directories: The stage directories, in stage order.

        Returns:
            ``(the first stage's stored parameter record or None, the stages' country or None,
            the stages' price basis year or None)``. The record is None for stage directories
            holding no stored evaluation, which is what a backend's worker writes; the two facts
            then still come out of the stored inputs.

        Raises:
            StagedEvaluationError: If one stage contradicts itself or two stages contradict each
                other about either fact.
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
        """Read the ``--parameters`` file, as a refusal rather than as a traceback.

        A file that is not there or is not JSON is the caller's mistake in exactly the way a bad
        key is, so it leaves the same artifact: exit 2 with a ``problems.json``. Letting the
        ``OSError`` or ``JSONDecodeError`` escape produced an exit 2 with no file at all, which a
        backend can only report as a broken engine (shared todo B29).

        Args:
            path: The ``--parameters`` argument.

        Returns:
            The parsed document, of whatever JSON type it happens to be;
            :meth:`StagedParameters.from_mapping` refuses one that is not an object.

        Raises:
            StagedEvaluationError: If the file is missing or does not parse, carrying the one
                problem row that names it.
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
        """The parsed ``--parameters`` block and the perspective id the plan is priced under.

        The whole input contract of the subcommand in one call: the stages' stored assumptions are
        the base, the ``--parameters`` file states what may be changed
        (:class:`~hisim.economics.staged_parameters.ParameterKeys`), and ``--perspective`` is
        reconciled with the file's ``perspective_id``. Every fault is collected and reported at
        once, because fixing a five-key block one round-trip per key is not a conversation anyone
        should have with a batch job.

        Args:
            args: The parsed namespace, for ``--parameters`` and ``--perspective``.
            directories: The stage directories, in stage order.

        Returns:
            ``(the parsed parameters, the perspective id)``.

        Raises:
            StagedEvaluationError: If any key is refused, carrying one problem row per offending
                key, or if the stages disagree about their country.
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

    #: How a catalogue is named in the document: the country it applies to and the date the
    #: catalogue was taken from the programmes' own pages, which is the pair that identifies one
    #: version of one country's support landscape. The bare country would not: Ireland's schemes
    #: change every few months and a stored document has to say which of them it priced.
    CATALOG_ID_FORMAT: ClassVar[str] = "{country}@{snapshot}"

    #: What stands in the date's place when the catalogue states no snapshot date.
    UNDATED_CATALOG: ClassVar[str] = "undated"

    @classmethod
    def catalog_id(cls, catalog: Optional[SubsidyCatalog], country: str) -> Optional[str]:
        """How the document names the subsidy catalogue a plan was priced under.

        Args:
            catalog: The catalogue in force, or ``None`` when the plan ran with none, in which
                case every subsidy row of the document is undetermined.
            country: The country the plan was priced for.

        Returns:
            ``"IE@2026-09-19"``-style id, or ``None`` for a plan priced with no catalogue.
        """
        if catalog is None:
            return None
        return cls.CATALOG_ID_FORMAT.format(
            country=country, snapshot=catalog.snapshot_date or cls.UNDATED_CATALOG
        )

    #: The code a refusal about the plan as a whole is published under — a missing input file,
    #: years that run backwards, a stage with no mapping report. A refused *parameter* block
    #: carries its own per-key codes instead (``parameters.<key>.invalid`` and the rest).
    PLAN_PROBLEM_CODE: ClassVar[str] = "STAGED_PLAN_INVALID"

    @classmethod
    def write_problems(cls, out_path: str, error: StagedEvaluationError) -> str:
        """Write the ``problems.json`` a refused plan produces, beside the requested output.

        Every exit 2 of this subcommand writes this file, which is what a backend branches on: an
        exit 2 without it is a broken engine rather than a refused request (shared todo B29). A
        refusal that carries per-key rows — a parameter block — publishes them verbatim; every
        other refusal is one row worded from the message.

        Args:
            out_path: The ``--out`` path the caller asked for, which is not written.
            error: The refusal, as the evaluator or this class raised it.

        Returns:
            Where the problems document was written, so the message on stderr can name it.
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
    """``staged``: price a multi-year plan out of finished jobs into ``economics_result.json``.

    The whole subcommand, and the only place its three exit codes are decided. It reads each
    ``--stage`` argument's directory, resolves the assumptions, the perspective and the optional
    subsidy catalogue, prices the plan with :class:`~hisim.economics.staged.StagedEvaluator` and
    writes the document of E-spec §3, validated against its schema before the first byte lands.

    Returns:
        0 on success, 2 for a refused plan (with a ``problems.json`` beside ``--out``), 3 for an
        engine failure.
    """
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

    # Which catalogue a plan is priced under (step 11 §3, item 12): `--subsidy-catalog` wins, then
    # a path the stages' stored record names, and failing both the shipped directory when it has
    # this country's file. The last step is what a stage directory holding only the extract needs:
    # the extract carries no catalogue path and is not meant to, and without the default such a
    # plan published every subsidy as undetermined while the same plan over full job directories
    # priced the grants.
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

    try:
        result = StagedEvaluator(database).evaluate(stages, parameters, perspective, catalog)
    except StagedEvaluationError as error:
        path = StagedCli.write_problems(args.out, error)
        print(f"{error} (problems written to {path})", file=sys.stderr)
        return StagedCli.PLAN_REFUSED
    except (UnresolvableSubjectsError, CostDataError) as error:
        print(str(error), file=sys.stderr)
        return StagedCli.ENGINE_FAILED

    try:
        measures, unpriced = StagedCli.read_mapping(directories, args.stage)
    except StagedEvaluationError as error:
        path = StagedCli.write_problems(args.out, error)
        print(f"{error} (problems written to {path})", file=sys.stderr)
        return StagedCli.PLAN_REFUSED
    document = StagedDocument(
        result=result,
        parameters=parameters,
        perspective=perspective,
        measure_ids=measures,
        unpriced_subjects=unpriced,
        subsidy_catalog_id=StagedCli.catalog_id(catalog, parameters.country),
    )
    document.write(Path(args.out))
    print(f"Wrote {args.out} for {len(stages)} stages under perspective {perspective.id}.")
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
            + "The price basis year likewise. `simulation_year` and `subsidy_catalog` are "
            + "accepted and ignored."
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
