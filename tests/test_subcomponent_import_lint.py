"""A module under ``hisim/subcomponents`` imports only what a subcomponent may depend on.

A subcomponent is a plain class that models part of a device's behaviour; a component builds it and calls it
(``system_docs/component_design_principles.md``, section C). So that any component can use it, a subcomponent must
not depend on the component runtime, another component, the simulator or anything above them. A module under
``hisim/subcomponents/`` may import only:

* the standard library and numpy;
* ``hisim.config``, ``hisim.hydronics``, ``hisim.loadtypes`` and ``hisim.units``, and their modules;
* other modules of ``hisim.subcomponents``.

Two checks hold the rule:

* a static one, which parses every ``hisim/subcomponents/**/*.py`` with ``ast`` and flags every ``import`` and
  ``from ... import`` that names anything else, wherever it stands (a function-local import counts like a
  top-of-file one) and however it is spelled (a relative import is resolved against the file's package first);
* a dynamic one, which imports every subcomponent module in a fresh interpreter and checks that none of the layers
  above was loaded, which also catches an import through ``importlib``.
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
class SubcomponentImport:
    """One import in a subcomponent module of a module a subcomponent may not depend on.

    For example, ``from hisim.component import SingleTimeStepValues`` on line 3 of ``hisim/subcomponents/pumps.py``
    is ``SubcomponentImport("hisim/subcomponents/pumps.py", 3, "hisim.component")``.

    Args:
        path: The file, relative to the repository root.
        line: The line of the import statement.
        module: The imported module, resolved to its absolute dotted name.
    """

    path: str
    line: int
    module: str

    def describe(self) -> str:
        """Return the failure line of this import: the file, the line and the module.

        Returns:
            For example ``"hisim/subcomponents/pumps.py:3 imports hisim.component"``.
        """
        return f"{self.path}:{self.line} imports {self.module}"


class SubcomponentLayering:
    """The rule's constants and the scanner that checks one source text against them.

    The scanner resolves each import statement to the modules it loads and compares each with the allowed set:
    a standard-library module, numpy, or a module at or below one of :attr:`ALLOWED_HISIM_PACKAGES`. For example,
    ``from hisim.config import ConfigBase`` and ``import numpy as np`` pass, ``from hisim import component`` and
    ``import pandas`` are flagged.
    """

    #: The repository root and the package whose modules are checked.
    ROOT: ClassVar[Path] = Path(__file__).resolve().parents[1]
    PACKAGE: ClassVar[str] = "hisim.subcomponents"

    #: The HiSim modules a subcomponent may import, each with every module below it.
    ALLOWED_HISIM_PACKAGES: ClassVar[Tuple[str, ...]] = (
        "hisim.config",
        "hisim.hydronics",
        "hisim.loadtypes",
        "hisim.units",
        "hisim.subcomponents",
    )

    #: The third-party packages a subcomponent may import.
    ALLOWED_THIRD_PARTY: ClassVar[Tuple[str, ...]] = ("numpy",)

    #: The top-level package of HiSim: ``from hisim import x`` loads the module ``hisim.x``.
    ROOT_PACKAGE: ClassVar[str] = "hisim"

    #: The layers above the subcomponents that the dynamic check expects to stay unloaded.
    LAYERS_ABOVE: ClassVar[Tuple[str, ...]] = (
        "hisim.component",
        "hisim.components",
        "hisim.simulator",
        "hisim.postprocessing",
        "hisim.energy_system",
        "hisim.renovisor",
    )

    #: What a failure says about the rule.
    RULE: ClassVar[str] = (
        "A module under hisim/subcomponents may import only the standard library, numpy, hisim.config, "
        "hisim.hydronics, hisim.loadtypes, hisim.units and other subcomponent modules "
        "(system_docs/component_design_principles.md, section C). Pass what the subcomponent needs as plain values."
    )

    @classmethod
    def source_files(cls) -> List[Path]:
        """Return every ``.py`` file under ``hisim/subcomponents/``, sorted.

        Returns:
            The absolute paths of the package's source files, the marker module among them.
        """
        return sorted((cls.ROOT / Path(*cls.PACKAGE.split("."))).rglob("*.py"))

    @classmethod
    def module_name(cls, relative_path: Path) -> str:
        """Return the dotted module name of a source file.

        For example, ``hisim/subcomponents/base.py`` is ``hisim.subcomponents.base``, and a package's
        ``__init__.py`` is the package itself.

        Args:
            relative_path: The file's path relative to the repository root.

        Returns:
            The dotted module name.
        """
        parts = list(relative_path.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)

    @classmethod
    def module_package(cls, relative_path: Path) -> List[str]:
        """Return the dotted package of a file, as parts.

        For example, ``hisim/subcomponents/pumps.py`` gives ``["hisim", "subcomponents"]``. A package's
        ``__init__.py`` belongs to the package itself, like any other module in it, which is how Python resolves
        its relative imports.

        Args:
            relative_path: The file's path relative to the repository root.

        Returns:
            The package's name parts.
        """
        return list(relative_path.with_suffix("").parts[:-1])

    @classmethod
    def imported_modules(cls, node: ast.AST, package: List[str]) -> List[str]:
        """Return the absolute names of the modules one import statement loads.

        ``import a.b`` loads ``a.b``. ``from a import b`` loads ``a``, and for ``from hisim import b`` that is the
        module ``hisim.b``, since the root package holds nothing a subcomponent could use but its modules. A
        relative import is resolved against ``package`` first. For example, ``from .. import component`` in
        ``hisim/subcomponents/pumps.py`` loads ``hisim.component``.

        Args:
            node: Any syntax node; only ``Import`` and ``ImportFrom`` yield names.
            package: The dotted package of the file, as parts.

        Returns:
            The module names, or an empty list for any other node.
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
        if base == cls.ROOT_PACKAGE:
            return [f"{base}.{alias.name}" for alias in node.names]
        return [base]

    @classmethod
    def is_allowed(cls, module: str) -> bool:
        """Return whether a subcomponent may import a module.

        For example, ``math``, ``numpy.typing`` and ``hisim.config.laws`` are allowed; ``pandas``, ``hisim`` and
        ``hisim.component`` are not.

        Args:
            module: The absolute dotted module name.

        Returns:
            True for a standard-library module, an allowed third-party package or an allowed HiSim module.
        """
        top_level = module.split(".")[0]
        if top_level in cls.ALLOWED_THIRD_PARTY:
            return True
        if top_level == cls.ROOT_PACKAGE:
            return any(module == allowed or module.startswith(allowed + ".") for allowed in cls.ALLOWED_HISIM_PACKAGES)
        return top_level in sys.stdlib_module_names

    @classmethod
    def scan(cls, source: str, relative_path: Path) -> List[SubcomponentImport]:
        """Return every import of a module a subcomponent may not depend on, in one source text.

        Args:
            source: The file's text.
            relative_path: The file's path relative to the repository root, for its package and the message.

        Returns:
            One entry per offending statement, naming the first forbidden module it loads, sorted by line.
        """
        package = cls.module_package(relative_path)
        found: List[SubcomponentImport] = []
        for node in ast.walk(ast.parse(source, filename=str(relative_path))):
            forbidden = [module for module in cls.imported_modules(node, package) if not cls.is_allowed(module)]
            if forbidden:
                found.append(SubcomponentImport(str(relative_path), getattr(node, "lineno", 0), forbidden[0]))
        return sorted(found, key=lambda item: (item.path, item.line))


class TestSubcomponentsImportOnlyTheirLayer:
    """The static check over every subcomponent module, and checks that the scanner sees what it must."""

    def test_no_subcomponent_module_imports_a_forbidden_module(self) -> None:
        """Every file under hisim/subcomponents imports only the allowed modules.

        Fails when a subcomponent module imports the component runtime, a component, the simulator or any other
        module outside the allowed set, so the subcomponent could no longer be used by any component.
        """
        files = SubcomponentLayering.source_files()
        assert any(path.name == "base.py" for path in files), "the walk did not find hisim/subcomponents/base.py"
        violations = [
            item
            for path in files
            for item in SubcomponentLayering.scan(path.read_text(encoding="utf-8"), path.relative_to(SubcomponentLayering.ROOT))
        ]
        assert not violations, SubcomponentLayering.RULE + "\n" + "\n".join(item.describe() for item in violations)

    @pytest.mark.parametrize(
        "statement, module",
        [
            ("import hisim.component", "hisim.component"),
            ("from hisim.component import SingleTimeStepValues", "hisim.component"),
            ("from hisim import component", "hisim.component"),
            ("from hisim import hydronics, log", "hisim.log"),
            ("import hisim", "hisim"),
            ("from hisim.components.generic_boiler import BoilerPump", "hisim.components.generic_boiler"),
            ("from .. import simulator", "hisim.simulator"),
            ("from ..energy_system import model", "hisim.energy_system"),
            ("import pandas as pd", "pandas"),
            ("def later():\n    from hisim.postprocessing import kpi_computation", "hisim.postprocessing"),
        ],
    )
    def test_the_scanner_flags_every_spelling(self, statement: str, module: str) -> None:
        """Absolute, relative, aliased and function-local imports of a forbidden module are flagged, with its name.

        Fails when the scanner misses a spelling, so the static check would let a forbidden import through.
        """
        found = SubcomponentLayering.scan(statement + "\n", Path("hisim/subcomponents/example.py"))
        assert [item.module for item in found] == [module]
        assert found[0].describe().startswith("hisim/subcomponents/example.py:")

    @pytest.mark.parametrize(
        "statement",
        [
            "import math",
            "from dataclasses import dataclass",
            "from typing import ClassVar, NamedTuple",
            "from __future__ import annotations",
            "import numpy as np",
            "from hisim import hydronics, loadtypes, units",
            "from hisim.config import ConfigBase",
            "from hisim.config.laws import SizingLaw",
            "from hisim.subcomponents.base import Subcomponent",
            "from . import base",
            "from .base import Subcomponent",
        ],
    )
    def test_the_scanner_lets_the_allowed_modules_through(self, statement: str) -> None:
        """The standard library, numpy, the allowed HiSim modules and sibling subcomponents are not flagged.

        Fails when the scanner refuses an allowed import, so a correct subcomponent module would fail the check.
        """
        assert not SubcomponentLayering.scan(statement + "\n", Path("hisim/subcomponents/example.py"))


def test_importing_every_subcomponent_loads_no_layer_above() -> None:
    """A fresh interpreter importing every subcomponent module has none of the layers above loaded afterwards.

    Catches what the static check cannot: a forbidden module reached through ``importlib`` or ``__import__``, or
    through an allowed module that itself started to import a layer above the subcomponents.
    """
    modules = [
        SubcomponentLayering.module_name(path.relative_to(SubcomponentLayering.ROOT)) for path in SubcomponentLayering.source_files()
    ]
    program = f"import sys\nfor name in {modules!r}:\n    __import__(name)\nprint('\\n'.join(sorted(sys.modules)))"
    finished = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, check=False, cwd=SubcomponentLayering.ROOT)
    assert finished.returncode == 0, f"importing {modules} failed: {finished.stderr}"
    loaded = [
        name
        for name in finished.stdout.split()
        if any(name == layer or name.startswith(layer + ".") for layer in SubcomponentLayering.LAYERS_ABOVE)
    ]
    assert not loaded, f"importing {modules} loaded {loaded}. {SubcomponentLayering.RULE}"
