"""T-10, T-12: the calculator's seams to the repository, checked statically.

T-10 parses every module under ``epc/`` and resolves its relative imports: nothing imports HiSim or
the prototype, nothing but the standard library, PyYAML and the package itself is imported (pytest
in the tests), the steps import in the direction of design §3.5, and no module names the archetype
database HiSim builds from (R1.2, R6.1, R8.7, AC6). T-12 checks that ``pytest.ini`` collects these
tests and registers the validation marker (R1.3, C3).
"""

from __future__ import annotations

import ast
import configparser
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest

from epc.tests import support

pytestmark = pytest.mark.base

#: The package folder.
PACKAGE = support.REPOSITORY_ROOT / "epc"

#: Top-level modules no module of the calculator may import.
FORBIDDEN = ("hisim", "oenorm_hwb_calculator")

#: Third-party top-level modules the calculator may import; the tests may import pytest as well.
THIRD_PARTY = ("yaml",)

#: The archetype database's name, assembled so that this module does not contain it.
ARCHETYPE_DATABASE = "".join(("TAB", "ULA"))

#: The shell's own modules under ``engines/``; every other name there is an engine package.
SHELL_ENGINE_MODULES = ("base", "registry")

#: Unit of the package → the other units it may import (design §3.5); ``engines.<cc>`` is any engine.
#: An engine (``engines.<cc>``) imports the model and its own files only.
ALLOWED: Dict[str, Set[str]] = {
    "epc": set(),
    "model": set(),
    "inputs": {"model"},
    "reports": {"model"},
    "engines": {"model", "engines.<cc>"},
    "batch": {"epc", "model", "inputs", "engines", "engines.<cc>", "reports"},
    "__main__": {"epc", "model", "inputs", "engines", "engines.<cc>", "reports", "batch"},
}


def _modules() -> List[Path]:
    """Every module of the package, tests included."""
    return sorted(PACKAGE.rglob("*.py"))


def _module_name(path: Path) -> str:
    """The dotted name of a module file."""
    parts = list(path.relative_to(support.REPOSITORY_ROOT).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _imports(source: str, package: List[str]) -> List[str]:
    """Every module a source imports, its relative imports resolved against its package."""
    names: List[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            module = ".".join(base + ([node.module] if node.module else []))
            names += [module] if node.module else [f"{module}.{alias.name}" for alias in node.names]
    return names


def _unit(module: str) -> str:
    """The unit of design §3.5 a module of the package belongs to, such as ``model`` or ``engines.at``."""
    parts = module.split(".")[1:]
    if not parts:
        return "epc"
    if parts[0] == "engines" and len(parts) > 1 and parts[1] not in SHELL_ENGINE_MODULES:
        return f"engines.{parts[1]}"
    return parts[0]


def _refused(module: str, imported: str) -> bool:
    """Whether a non-test module of the package may not import a module."""
    top = imported.split(".")[0]
    if top in FORBIDDEN:
        return True
    if top != "epc":
        return top not in sys.stdlib_module_names and top not in THIRD_PARTY
    source, target = _unit(module), _unit(imported)
    if source == target:
        return False
    if source.startswith("engines."):
        return target != "model"
    allowed = ALLOWED[source]
    return target not in allowed and not (target.startswith("engines.") and "engines.<cc>" in allowed)


def _findings() -> List[Tuple[str, str]]:
    """(module, offending import) over the package; the tests may import every unit and pytest."""
    findings: List[Tuple[str, str]] = []
    for path in _modules():
        module = _module_name(path)
        package = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
        for imported in _imports(path.read_text(encoding="utf-8"), package):
            top = imported.split(".")[0]
            if module.startswith("epc.tests"):
                refused = top in FORBIDDEN or (
                    top not in ("epc", "pytest") + THIRD_PARTY and top not in sys.stdlib_module_names
                )
            else:
                refused = _refused(module, imported)
            if refused:
                findings.append((module, imported))
    return findings


def test_the_package_imports_nothing_of_hisim_and_follows_its_layers() -> None:
    """No HiSim, no prototype, only stdlib and PyYAML, and the step directions of design §3.5 (T-10)."""
    assert _modules(), "no module found"
    assert not _findings()


def test_the_checker_resolves_relative_imports_and_refuses_what_it_should() -> None:
    """A relative import is seen under its package name, so an escape through it cannot hide."""
    source = "\n".join(
        (
            "from ..model import building",
            "from . import record",
            "from hisim import log",
            "import oenorm_hwb_calculator.x",
        )
    )
    imported = _imports(source, ["epc", "inputs"])
    assert imported == ["epc.model", "epc.inputs.record", "hisim", "oenorm_hwb_calculator.x"]
    assert _refused("epc.inputs.record", "hisim")
    assert _refused("epc.inputs.record", "oenorm_hwb_calculator.x")
    assert _refused("epc.inputs.record", "pandas")
    assert _refused("epc.inputs.record", "epc.reports.table")
    assert _refused("epc.model.result", "epc.inputs.record")
    assert _refused("epc.engines.at.method", "epc.engines.ie.data_tables")
    assert _refused("epc.engines.at.method", "epc.inputs.record")
    assert not _refused("epc.engines.at.method", "epc.model.result")
    assert not _refused("epc.engines.registry", "epc.engines.at.method")
    assert _unit("epc.engines.at") == _unit("epc.engines.at.method") == "engines.at"
    assert _unit("epc.engines.registry") == "engines"
    assert not _refused("epc.engines.at.data_tables", "epc.engines.at")
    assert not _refused("epc.batch", "epc.reports.table")
    assert not _refused("epc.inputs.record", "yaml")


def test_no_module_names_the_archetype_database() -> None:
    """The calculator knows nothing of where HiSim got its building (R6.1, D24); fixtures are not modules."""
    assert not [path for path in _modules() if ARCHETYPE_DATABASE in path.read_text(encoding="utf-8")]


def test_pytest_ini_collects_the_tests_and_registers_the_marker() -> None:
    """``epc/tests`` is a test path, ``epc_norm_data`` a marker, and collection succeeds (T-12)."""
    ini = configparser.ConfigParser(interpolation=None)
    ini.read(support.REPOSITORY_ROOT / "pytest.ini", encoding="utf-8")
    assert "epc/tests" in ini["pytest"]["testpaths"].split()
    markers = [line.split(":")[0].strip() for line in ini["pytest"]["markers"].splitlines() if line.strip()]
    assert "epc_norm_data" in markers
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "epc/tests"],
        cwd=support.REPOSITORY_ROOT,
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "epc/tests/test_reading.py::" in completed.stdout
