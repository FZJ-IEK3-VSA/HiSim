"""Tests for the ``simple_weather_data_import`` system setup module.

These tests guard the Single Responsibility Principle refactor (GitLab issue
#881) that moved the ``WeatherDataImport`` construction out of module scope and
into ``setup_function``. Importing the module pulls in the optional
``wetterdienst`` dependency (and, before the refactor, triggered a network fetch
as an import-time side effect), so these tests inspect the module source via
:mod:`ast` instead of importing it. This keeps them fast, offline, and free of
the ``wetterdienst`` dependency while still pinning the structural invariant:
configuration constants live at module scope and the ``WeatherDataImport``
construction lives inside ``setup_function``.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
MODULE_PATH: Path = REPO_ROOT / "system_setups" / "simple_weather_data_import.py"


def _parse_module() -> ast.Module:
    """Parse the ``simple_weather_data_import`` module source into an AST."""
    return ast.parse(MODULE_PATH.read_text(encoding="utf-8"))


def _is_weather_data_import_call(node: ast.AST) -> bool:
    """Return ``True`` if *node* is a ``WeatherDataImport(...)`` call."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "WeatherDataImport"
    )


@pytest.mark.base
def test_simple_weather_data_import_defines_setup_function() -> None:
    """The module must define a ``setup_function`` callable.

    The HiSim framework discovers and calls ``setup_function`` by name (see
    ``hisim_main.initialize_from_python``); a setup module without it cannot be
    run. This pins the convention introduced by the refactor.
    """
    tree = _parse_module()
    function_names = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    assert "setup_function" in function_names, (
        "simple_weather_data_import.py must define a setup_function; "
        "the HiSim framework calls it by name to build the setup."
    )


@pytest.mark.base
def test_simple_weather_data_import_has_no_module_level_construction() -> None:
    """No ``WeatherDataImport(...)`` call may appear at module scope.

    Before the refactor the module instantiated ``WeatherDataImport`` at import
    time, which fetched data from DWD as a side effect of merely importing the
    file. The construction must live inside ``setup_function`` so that importing
    the module is free of side effects.
    """
    tree = _parse_module()
    for node in tree.body:
        # Anything that is not a function definition is module-level code.
        if isinstance(node, ast.FunctionDef):
            continue
        for sub in ast.walk(node):
            assert not _is_weather_data_import_call(sub), (
                "WeatherDataImport(...) is constructed at module scope in "
                "simple_weather_data_import.py; it must be moved inside "
                "setup_function to avoid import-time (network) side effects."
            )


@pytest.mark.base
def test_simple_weather_data_import_setup_function_constructs_and_returns() -> None:
    """``setup_function`` must construct and return a ``WeatherDataImport``.

    The refactor moved the construction into ``setup_function`` and exposes the
    instance via the return value (instead of a module-level global) so former
    users of the global can obtain it from the return value.
    """
    tree = _parse_module()
    setup_func = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "setup_function"
    )
    # The body must contain a WeatherDataImport(...) call.
    assert any(
        _is_weather_data_import_call(sub) for sub in ast.walk(setup_func)
    ), "setup_function must construct a WeatherDataImport instance."

    # The body must end with a return statement (exposes the instance).
    last_stmt = setup_func.body[-1]
    assert isinstance(last_stmt, ast.Return), (
        "setup_function must return the constructed WeatherDataImport so "
        "callers can obtain it without a module-level global."
    )
