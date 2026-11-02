"""Non-finite values are caught once after the run, not by checks that run on every step or pass.

The owner decided on 2026-10-17 that a run fails after its time loop if a result column holds NaN or an infinite
value (``hisim/postprocessing/finite_results.py``), and that the simulation's hot path carries no per-call
finiteness checks: they ran tens of millions of times a year and caught nothing the post-run check does not. This
module keeps that decision from eroding. It scans the modules whose code runs on every pass (the components, the
hydronics and part-load arithmetic, the simulator) and refuses a call of ``isfinite`` or ``isnan`` outside the
functions that run once per run, and outside a short list of documented exceptions.
"""

import ast
import pathlib
from typing import ClassVar, FrozenSet, List, Tuple

import pytest


class HotPathScan:
    """The modules that run on every pass, the functions allowed to check finiteness there, and the scan itself."""

    #: The repository root, two levels above this file.
    REPOSITORY_ROOT: ClassVar[pathlib.Path] = pathlib.Path(__file__).resolve().parent.parent

    #: The modules and folders whose code runs on every pass of a step, relative to the repository root.
    HOT_PATHS: ClassVar[Tuple[str, ...]] = (
        "hisim/components",
        "hisim/hydronics.py",
        "hisim/part_load.py",
        "hisim/simulator.py",
        "hisim/component.py",
        "hisim/dynamic_component.py",
        "hisim/component_wrapper.py",
    )

    #: Names of functions that run once per run (building and checking a component), where a check is allowed.
    BUILD_TIME_NAMES: ClassVar[FrozenSet[str]] = frozenset(
        {"__init__", "__post_init__", "build", "sized_for", "i_prepare_simulation"}
    )

    #: Name prefixes of functions that run once per run.
    BUILD_TIME_PREFIXES: ClassVar[Tuple[str, ...]] = ("refuse_", "require_", "check_")

    #: Documented exceptions, as (path relative to the repository root, function name), each with its reason.
    #: - The PV and solar-thermal irradiance functions replace NaN by 0 for a dark sky: logic, not validation.
    #: - The window's solar gain runs in the solar-gains cache producer, once per step when the cache is built and
    #:   never per pass, and names the weather gap that caused a NaN.
    EXCEPTIONS: ClassVar[FrozenSet[Tuple[str, str]]] = frozenset(
        {
            ("hisim/components/generic_pv_system/calculation.py", "simulate_sandia"),
            ("hisim/components/generic_pv_system/calculation.py", "simulate_cec"),
            ("hisim/components/solar_thermal_system.py", "plane_of_array_irradiance_w_m2"),
            ("hisim/components/building/window.py", "calc_solar_heat_gains"),
        }
    )

    #: The calls this scan looks for, by attribute or function name.
    CHECK_NAMES: ClassVar[FrozenSet[str]] = frozenset({"isfinite", "isnan"})

    @classmethod
    def files(cls) -> List[pathlib.Path]:
        """Return every Python file of the hot path.

        Returns:
            The files, sorted.
        """
        found: List[pathlib.Path] = []
        for entry in cls.HOT_PATHS:
            path = cls.REPOSITORY_ROOT / entry
            found.extend(sorted(path.rglob("*.py")) if path.is_dir() else [path])
        return found

    @classmethod
    def checks(cls, path: pathlib.Path) -> List[Tuple[str, int]]:
        """Return each finiteness check in a file with the innermost function that contains it.

        For example, ``math.isfinite(x)`` inside ``Battery.refuse_an_impossible_size`` gives
        ``("refuse_an_impossible_size", <line>)``; a check at module level gives ``("<module>", <line>)``.

        Args:
            path: The Python file.

        Returns:
            (function name, line) per check.
        """
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found: List[Tuple[str, int]] = []

        def visit(node: ast.AST, function_name: str) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function_name = node.name
            if isinstance(node, ast.Call):
                called = node.func
                name = called.attr if isinstance(called, ast.Attribute) else getattr(called, "id", "")
                if name in cls.CHECK_NAMES:
                    found.append((function_name, node.lineno))
            for child in ast.iter_child_nodes(node):
                visit(child, function_name)

        visit(tree, "<module>")
        return found

    @classmethod
    def is_build_time(cls, function_name: str) -> bool:
        """Return whether a function name marks code that runs once per run.

        Args:
            function_name: The function's name.

        Returns:
            Whether a finiteness check may sit there.
        """
        return function_name in cls.BUILD_TIME_NAMES or function_name.startswith(cls.BUILD_TIME_PREFIXES)


@pytest.mark.base
def test_the_hot_path_checks_finiteness_only_at_build_time_or_in_documented_exceptions() -> None:
    """Catches a per-step or per-pass ``isfinite`` / ``isnan`` check returning to the simulation's hot path.

    A new check in a function that runs every pass fails here; move it to where the component is built, or rely on
    the post-run check of the result columns, which names the column and its first bad step.
    """
    offending = []
    for path in HotPathScan.files():
        relative = path.relative_to(HotPathScan.REPOSITORY_ROOT).as_posix()
        for function_name, line in HotPathScan.checks(path):
            if HotPathScan.is_build_time(function_name) or (relative, function_name) in HotPathScan.EXCEPTIONS:
                continue
            offending.append(f"{relative}:{line} in {function_name}")
    assert not offending, (
        "Finiteness checks in code that runs on every step or pass (owner decision 2026-10-17: NaN and infinite "
        "values are caught once after the run by hisim/postprocessing/finite_results.py):\n" + "\n".join(offending)
    )


@pytest.mark.base
def test_every_documented_exception_still_holds_a_finiteness_check() -> None:
    """Catches an exception that no longer checks anything, so the list does not grow stale."""
    stale = []
    for relative, function_name in sorted(HotPathScan.EXCEPTIONS):
        names = {name for name, _ in HotPathScan.checks(HotPathScan.REPOSITORY_ROOT / relative)}
        if function_name not in names:
            stale.append(f"{relative} {function_name}")
    assert not stale, "Remove these from HotPathScan.EXCEPTIONS, they check nothing any more:\n" + "\n".join(stale)


@pytest.mark.base
def test_no_function_is_a_finite_value_helper() -> None:
    """Catches a ``_finite``-style helper, the per-call validation pattern that hydronics carried until 2026-10-17."""
    helpers = []
    for path in sorted((HotPathScan.REPOSITORY_ROOT / "hisim").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_finite"):
                helpers.append(f"{path.relative_to(HotPathScan.REPOSITORY_ROOT).as_posix()}:{node.lineno} {node.name}")
    assert not helpers, "Per-call finite-value helpers are not wanted (post-run check instead):\n" + "\n".join(helpers)
