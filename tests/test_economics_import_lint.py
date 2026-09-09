"""The seam-4 contract, machine-enforced (cost-spec-v2 §2.4, package S4b).

Seam 4's invariant is "presentation never computes". Docstrings and code review cannot hold that
line — the first person who needs one more number reaches for the evaluator, and nothing stops
them. What *can* hold it is the import graph: presentation that cannot reach the engine cannot
recompute an engine figure, whatever it wanted to. This module parses the import statements of
the presentation modules with `ast` (so a local import inside a function counts exactly like a
top-of-file one) and checks them against the surface below.

**The allowed surface for presentation** — `reporting`, `report_plots`, `presentation_style`:

| Module              | Why presentation may import it                                        |
|---------------------|-----------------------------------------------------------------------|
| `results`           | the result object it renders, plus the comparison math on it (W4.4)   |
| `views`             | the view-model — every derived number a report shows (W4.1)           |
| `plausibility`      | the typed findings and their check ids — **types only**, see below    |
| `input_audit`       | the typed resolved-input rows `audit.py` produces (W4.6)              |
| `presentation_style`| the display groups and palette shared by HTML and matplotlib (W4.7)   |
| `exports`           | `build_lifecycle_kpi_entries`, so the KPI table and lifecycle_kpis.json cannot disagree |
| `timeline`          | `CostCategory` / `Actor` — the vocabulary of the result object        |
| `uncertainty`       | `UncertainValue` / `Slot` — the type every money figure has           |
| `carriers`, `parameters`, `provenance` | shared kernel types                                |

Everything else under `hisim.economics` is engine and is forbidden: `evaluator`, `database`,
`calculators.*`, `subsidies`, `tariffs`, `scenarios`, `financing`, `actors`, `perspectives`,
`facts`, `adapter`, `bridge`, `serialization`, `validation`, `audit`. Two of those are worth a
sentence:

* `scenarios` — the report's section 9 renders a `ScenarioCube`, but takes it as an untyped
  parameter and asks it for `equivalent_annual_cost_swings` / `_spreads`. The cube derives its
  own numbers (W4.1); presentation never imports the module that builds one.
* `audit` — the *types* it produces are importable (`input_audit`), the module that fills them
  from the cost database is not.

`plausibility` is allowed for its types but not for running the checks: importing
`run_plausibility_checks` or `PlausibilityConfig` into presentation would mean a report deciding
*whether* a number is plausible rather than rendering the decision, so those names are banned
by name.

**The other direction.** `exports.py` (result serialization) and `audit.py` (the legacy parity
harness — it computes by design, §2.4 W4.6) are *not* presentation and are not linted against
the engine. What they must not do is import `reporting` or `report_plots`: the dependency runs
from presentation to the engine's outputs, never back.

**Error class.** A failure here is never a wrong number — it is the *guard rail* against a class
of wrong numbers. It says the seam-4 boundary was crossed, so a figure a reader sees may from now
on be computed twice (once in the engine, once in a chart) and the two copies may disagree. The
fix is always the same: move the derivation into `views.py` and let presentation read it.
"""

# clean

import ast
import os
from dataclasses import dataclass, field
from typing import Dict, List, Set

import pytest

pytestmark = pytest.mark.base

PACKAGE_DIRECTORY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hisim", "economics"
)
PACKAGE_PREFIX = "hisim.economics"

#: The presentation layer: what renders, and nothing else.
PRESENTATION_MODULES = ("reporting", "report_plots", "presentation_style")

#: Modules the presentation layer may import from `hisim.economics` (see the table above).
ALLOWED_FOR_PRESENTATION: Set[str] = {
    "results",
    "views",
    "plausibility",
    "input_audit",
    "presentation_style",
    "exports",
    "timeline",
    "uncertainty",
    "carriers",
    "parameters",
    "provenance",
}

#: Names that make `plausibility` an engine import rather than a type import.
BANNED_NAMES: Dict[str, Set[str]] = {
    "plausibility": {"run_plausibility_checks", "PlausibilityConfig"},
}

#: Modules that are neither presentation nor allowed to depend on it.
NON_PRESENTATION_MODULES = ("exports", "audit", "input_audit", "views", "results", "plausibility")


def _parse(module_name: str) -> ast.Module:
    """Parses one `hisim/economics/<module>` — a `.py` file or a package — into one syntax tree.

    The lint reads source, never imports it: importing a module would execute it and, worse,
    would make the check depend on what Python happens to have in `sys.modules` rather than on
    what the file says. A package (`reporting/`, `subsidies/` — split per the PR-3 review) is
    parsed as the concatenation of all its files, so the seam rules apply to every submodule
    exactly as they applied to the former single file. The assertion on the path turns a renamed
    or moved module into an immediate, obvious failure instead of a silently empty import list
    that passes every check.
    """
    file_path = os.path.join(PACKAGE_DIRECTORY, f"{module_name}.py")
    package_path = os.path.join(PACKAGE_DIRECTORY, module_name)
    assert os.path.isfile(file_path) or os.path.isdir(package_path), file_path
    if os.path.isfile(file_path):
        with open(file_path, encoding="utf-8") as file:
            return ast.parse(file.read(), filename=file_path)
    merged = ast.Module(body=[], type_ignores=[])
    for name in sorted(os.listdir(package_path)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(package_path, name)
        with open(path, encoding="utf-8") as file:
            merged.body.extend(ast.parse(file.read(), filename=path).body)
    return merged


@dataclass(frozen=True)
class EconomicsImport:
    """One `hisim.economics.<module>` import: which module, which names, which line."""

    module: str
    line: int
    names: Set[str] = field(default_factory=set)


def _economics_imports(module_name: str) -> List[EconomicsImport]:
    """Every `hisim.economics.X` a module imports.

    Walks the whole tree, so a deferred import inside a function is caught exactly like a
    top-of-file one — deferring an import is how a boundary usually gets crossed quietly.
    """
    found: List[EconomicsImport] = []
    for node in ast.walk(_parse(module_name)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(PACKAGE_PREFIX + "."):
                    target = alias.name[len(PACKAGE_PREFIX) + 1:].split(".")[0]
                    found.append(EconomicsImport(module=target, line=node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = {alias.name for alias in node.names}
            if node.module == PACKAGE_PREFIX:
                # `from hisim.economics import views, results` — each name is a submodule.
                for name in names:
                    found.append(EconomicsImport(module=name, line=node.lineno))
            elif node.module.startswith(PACKAGE_PREFIX + "."):
                target = node.module[len(PACKAGE_PREFIX) + 1:].split(".")[0]
                found.append(EconomicsImport(module=target, line=node.lineno, names=names))
    return found


class TestPresentationImportsNoEngine:
    """`reporting`, `report_plots` and `presentation_style` may not reach the engine."""

    @pytest.mark.parametrize("module_name", PRESENTATION_MODULES)
    def test_only_the_allowed_surface_is_imported(self, module_name):
        """Every hisim.economics import of a presentation module is on the allowed list."""
        violations = [
            f"{module_name}.py:{item.line} imports {PACKAGE_PREFIX}.{item.module}"
            for item in _economics_imports(module_name)
            # A package importing its own submodules is not a seam crossing.
            if item.module not in ALLOWED_FOR_PRESENTATION and item.module != module_name
        ]
        assert not violations, (
            "Presentation reached into the engine (cost-spec-v2 §2.4). Take the number from "
            "views.py instead of computing it here:\n" + "\n".join(violations)
        )

    @pytest.mark.parametrize("module_name", PRESENTATION_MODULES)
    def test_no_computation_entry_points_are_imported(self, module_name):
        """Allowed modules still may not hand presentation their *computation* entry points."""
        violations = [
            f"{module_name}.py:{item.line} imports {name} from {item.module}"
            for item in _economics_imports(module_name)
            for name in BANNED_NAMES.get(item.module, set()) & item.names
        ]
        assert not violations, "\n".join(violations)

    def test_report_plots_does_not_import_reporting(self):
        """W4.7: the two renderers share `presentation_style`, not each other."""
        assert "reporting" not in {item.module for item in _economics_imports("report_plots")}

    def test_presentation_style_imports_only_the_category_vocabulary(self):
        """The shared module has to stay importable from either side of the seam."""
        assert {item.module for item in _economics_imports("presentation_style")} == {"timeline"}


class TestEngineDoesNotImportPresentation:
    """The dependency runs one way. `exports` and `audit` are engine-side, not presentation."""

    @pytest.mark.parametrize("module_name", NON_PRESENTATION_MODULES)
    def test_no_engine_side_module_imports_a_renderer(self, module_name):
        """A result serializer or a parity harness that renders is a seam violation."""
        imported = {item.module for item in _economics_imports(module_name)}
        assert not imported & {"reporting", "report_plots"}, (
            f"{module_name}.py imports a renderer; exports serialize and audit verifies — "
            "neither formats (cost-spec-v2 §2.4, W4.6)."
        )

    def test_the_lint_would_notice(self):
        """Sanity: the parser really does see a deferred, engine-side import."""
        # `bridge.py` legitimately imports both sides — it is the orchestrator, not either half.
        bridge = {item.module for item in _economics_imports("bridge")}
        assert "reporting" in bridge and "evaluator" in bridge
