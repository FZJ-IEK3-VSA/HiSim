"""No module of HiSim outside ``hisim/renovisor`` imports ``hisim.renovisor``.

RenoVisor is the top layer: a request translator on top of the energy-system format and the
lifecycle cost engine (``hisim.economics``). It may import everything below it; nothing below it
may import it. What the cost engine needs to know from the translator travels as data instead —
the economics stage record inside each stage's mapping report (``hisim.economics.staged_record``).

Two checks hold the rule:

* a static one, which parses every ``hisim/**/*.py`` outside ``hisim/renovisor`` with ``ast`` and
  flags every ``import`` and ``from ... import`` that names ``hisim.renovisor`` or one of its
  modules, wherever it stands (a function-local import counts like a top-of-file one) and however
  it is spelled (a relative import is resolved against the file's package first);
* a dynamic one, which imports the cost engine's command line, the simulation entry point and the
  simulator in a fresh interpreter and checks that no ``hisim.renovisor`` module was loaded, which
  also catches an import through ``importlib``.

Tests, system setups and scripts may use RenoVisor freely; they are not under ``hisim/``.
"""

import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, List, Optional, Tuple

import pytest

pytestmark = pytest.mark.base


@dataclass(frozen=True)
class RenovisorImport:
    """One import of ``hisim.renovisor`` found in a source file.

    Args:
        path: The file, relative to the repository root.
        line: The line of the import statement.
        module: The imported module, resolved to its absolute dotted name.
    """

    path: str
    line: int
    module: str

    def describe(self) -> str:
        """Return the failure line: file, line and module."""
        return f"{self.path}:{self.line} imports {self.module}"


class RenovisorLayering:
    """The rule's constants and the scanner that checks it."""

    #: The repository root, the package tree under it and the package nothing below it may import.
    ROOT: ClassVar[Path] = Path(__file__).resolve().parents[1]
    PACKAGE: ClassVar[str] = "hisim"
    FORBIDDEN: ClassVar[str] = "hisim.renovisor"

    #: The entry points imported in a fresh interpreter: the cost engine's command line, the simulation entry point
    #: and the simulator.
    ENTRY_POINTS: ClassVar[Tuple[str, ...]] = ("hisim.economics.__main__", "hisim.hisim_main", "hisim.simulator")

    #: What a failure says about the rule.
    RULE: ClassVar[str] = (
        "hisim.renovisor sits on top of HiSim: modules under hisim/ outside hisim/renovisor must not import it. "
        "Pass what the lower layer needs as data (e.g. the economics stage record of the mapping report, "
        "hisim/economics/staged_record.py) or move the shared code below both packages."
    )

    @classmethod
    def source_files(cls) -> List[Path]:
        """Return every ``.py`` file under ``hisim/`` outside ``hisim/renovisor/``, sorted."""
        package = cls.ROOT / cls.PACKAGE
        excluded = package / "renovisor"
        return sorted(path for path in package.rglob("*.py") if excluded not in path.parents)

    @classmethod
    def module_package(cls, relative_path: Path) -> List[str]:
        """Return the dotted package of a file, as parts: ``hisim/economics/x.py`` -> ``["hisim", "economics"]``.

        A package's ``__init__.py`` belongs to the package itself, like any other module in it.
        """
        return list(relative_path.with_suffix("").parts[:-1])

    @classmethod
    def imported_modules(cls, node: ast.AST, package: List[str]) -> List[str]:
        """Return the absolute module names one import statement may load.

        ``import a.b`` loads ``a.b``; ``from a import b`` loads ``a`` and possibly the submodule ``a.b``, so both are
        returned. A relative import is resolved against ``package`` first.

        Args:
            node: Any syntax node; only ``Import`` and ``ImportFrom`` yield names.
            package: The dotted package of the file, as parts.

        Returns:
            The candidate module names, or an empty list for any other node.
        """
        if isinstance(node, ast.Import):
            return [alias.name for alias in node.names]
        if not isinstance(node, ast.ImportFrom):
            return []
        base: Optional[str] = node.module
        if node.level:
            parts = package[: len(package) - (node.level - 1)] if node.level > 1 else list(package)
            base = ".".join(parts + ([node.module] if node.module else []))
        if not base:
            return []
        return [base] + [f"{base}.{alias.name}" for alias in node.names]

    @classmethod
    def is_forbidden(cls, module: str) -> bool:
        """Return whether a module name is ``hisim.renovisor`` or one of its modules."""
        return module == cls.FORBIDDEN or module.startswith(cls.FORBIDDEN + ".")

    @classmethod
    def scan(cls, source: str, relative_path: Path) -> List[RenovisorImport]:
        """Return every import of ``hisim.renovisor`` in one source text.

        Args:
            source: The file's text.
            relative_path: The file's path relative to the repository root, for its package and the message.

        Returns:
            One entry per offending statement, naming the first forbidden module it loads.
        """
        package = cls.module_package(relative_path)
        found: List[RenovisorImport] = []
        for node in ast.walk(ast.parse(source, filename=str(relative_path))):
            forbidden = [module for module in cls.imported_modules(node, package) if cls.is_forbidden(module)]
            if forbidden:
                found.append(RenovisorImport(str(relative_path), getattr(node, "lineno", 0), forbidden[0]))
        return sorted(found, key=lambda item: (item.path, item.line))


class TestNothingBelowRenovisorImportsIt:
    """The static check over every source file, and a check that the scanner sees what it must."""

    def test_no_module_outside_renovisor_imports_it(self) -> None:
        """Every file under hisim/ outside hisim/renovisor is free of hisim.renovisor imports."""
        files = RenovisorLayering.source_files()
        assert len(files) > 100, "the walk found almost no source files; is ROOT right?"
        violations = [
            item
            for path in files
            for item in RenovisorLayering.scan(
                path.read_text(encoding="utf-8"), path.relative_to(RenovisorLayering.ROOT)
            )
        ]
        assert not violations, RenovisorLayering.RULE + "\n" + "\n".join(item.describe() for item in violations)

    @pytest.mark.parametrize(
        "statement, module",
        [
            ("import hisim.renovisor", "hisim.renovisor"),
            ("import hisim.renovisor.report as report", "hisim.renovisor.report"),
            ("from hisim.renovisor.report import MappingReport", "hisim.renovisor.report"),
            ("from hisim import renovisor", "hisim.renovisor"),
            ("from ..renovisor.request import CatalogueTable", "hisim.renovisor.request"),
            ("from .. import renovisor", "hisim.renovisor"),
            ("def later():\n    from hisim.renovisor.economics import MainSubjects", "hisim.renovisor.economics"),
        ],
    )
    def test_the_scanner_sees_every_spelling(self, statement: str, module: str) -> None:
        """Absolute, aliased, relative and function-local imports are each flagged, with their module."""
        found = RenovisorLayering.scan(statement + "\n", Path("hisim/economics/example.py"))
        assert [item.module for item in found] == [module]
        assert found[0].describe().startswith("hisim/economics/example.py:")

    @pytest.mark.parametrize(
        "statement",
        [
            "import hisim.economics.staged_record",
            "from hisim.economics import staged",
            "from . import staged_record",
            "import renovisor_like_name",
            "from hisim.renovisor_tools import x",
        ],
    )
    def test_the_scanner_lets_everything_else_through(self, statement: str) -> None:
        """Imports of other packages, including names that only start alike, are not flagged."""
        assert not RenovisorLayering.scan(statement + "\n", Path("hisim/economics/example.py"))


@pytest.mark.parametrize("module", RenovisorLayering.ENTRY_POINTS)
def test_importing_an_entry_point_loads_no_renovisor_module(module: str) -> None:
    """A fresh interpreter importing the entry point has no hisim.renovisor module afterwards.

    Catches what the static check cannot: an import through ``importlib`` or ``__import__``.
    """
    program = f"import {module}, sys; print('\\n'.join(sorted(sys.modules)))"
    finished = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, check=False)
    assert finished.returncode == 0, f"importing {module} failed: {finished.stderr}"
    loaded = [name for name in finished.stdout.split() if RenovisorLayering.is_forbidden(name)]
    assert not loaded, f"importing {module} loaded {loaded}. {RenovisorLayering.RULE}"
