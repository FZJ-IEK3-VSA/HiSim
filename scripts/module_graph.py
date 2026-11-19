#!/usr/bin/env python3
"""Check every import under ``hisim/`` against the module graph, with a ratchet baseline of today's violations.

The module graph (``system_docs/module_graph.yaml``) sorts every module of ``hisim`` into a group by the longest
dotted prefix that matches it, and lists for each group the groups it may import. This script collects every import
of every module -- module-level, function-local, ``TYPE_CHECKING``-only, ``importlib.import_module`` /
``__import__`` with a readable target, and a module name passed as a string to a call -- and reports each one the
graph forbids. Today's violations are listed in ``tests/module_graph_baseline.yaml``, each with the bead that will
remove it; the generic ratchet of ``scripts/ratchet.py`` fails on a violation the baseline does not hold and on a
baseline entry that no longer matches.

Usage::

    python scripts/module_graph.py check            # the check of tests/test_module_graph.py, human-readable
    python scripts/module_graph.py report           # Markdown: baseline per bead and per edge, for a CI job summary
    python scripts/module_graph.py prune            # delete or lower stale baseline entries; never adds one
"""
from __future__ import annotations

import argparse
import ast
import dataclasses
import enum
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import ClassVar, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

import yaml

# The script runs as `python scripts/module_graph.py` (sys.path[0] is scripts/) and is imported by the tests as
# `scripts.module_graph` (the repository root is on sys.path); the ratchet core is found either way.
try:
    from scripts.ratchet import BaselineEntry, BaselineFile, BaselineLayout, BeadIds, EntryKey, Finding, FindingKind, Ratchet
except ImportError:  # pragma: no cover - only when run as a plain script
    from ratchet import (  # type: ignore[no-redef, import-not-found]  # the same module under its script-mode name
        BaselineEntry, BaselineFile, BaselineLayout, BeadIds, EntryKey, Finding, FindingKind, Ratchet,
    )


class ImportKind(str, enum.Enum):
    """How an import statement loads its module; decides the baseline section a violation is listed in."""

    #: ``import x`` / ``from x import y`` at module or class level.
    STATIC = "static"
    #: The same inside a function: loaded when the function first runs.
    LAZY = "lazy"
    #: The same under ``if TYPE_CHECKING:``: read by the type checker only, never loaded at run time.
    TYPE_CHECKING = "type_checking"
    #: ``importlib.import_module`` or ``__import__`` with a target readable from the source (a literal, or a name
    #: bound to a literal in the same scope).
    IMPORTLIB = "importlib"
    #: The exact dotted name of a module passed as a string to some other call, such as a helper that imports it.
    STRING = "string"
    #: ``importlib.import_module`` or ``__import__`` whose target cannot be read from the source.
    DYNAMIC = "dynamic"

    @property
    def is_by_string(self) -> bool:
        """Whether the module is named by a string at run time rather than by an import statement."""
        return self in (ImportKind.IMPORTLIB, ImportKind.STRING, ImportKind.DYNAMIC)


@dataclasses.dataclass(frozen=True)
class ImportStatement:
    """One import of one module by another, as found in the importer's source.

    For example, ``from hisim.economics.facts import CostRelevance, EnergyFlowFacts`` on line 12 of
    ``hisim/components/generic_boiler.py`` is
    ``ImportStatement("hisim.components.generic_boiler", "hisim.economics.facts", ("CostRelevance", "EnergyFlowFacts"),
    ImportKind.STATIC, 12)``. ``names`` is ``("*",)`` when the statement imports the module itself
    (``import hisim.log``, ``from hisim import log``, ``importlib.import_module("hisim.log")``).
    """

    importer: str
    imported: str
    names: Tuple[str, ...]
    kind: ImportKind
    line: int


class ImportCollector:
    """Collects the imports of Python source text, resolved to the modules of one package.

    Only imports of the package's own modules are collected (``hisim``); a dotted target is reduced to the module
    that defines it, so ``from hisim.config import ConfigBase`` imports the module ``hisim.config`` with the name
    ``ConfigBase``, while ``from hisim import log`` imports the module ``hisim.log`` itself.
    """

    #: The name under which an import whose target cannot be read is listed.
    DYNAMIC_TARGET: ClassVar[str] = "<dynamic>"
    #: The imported "name" of a statement that imports a whole module.
    WHOLE_MODULE: ClassVar[str] = "*"
    #: The spellings of a call that imports a module named by a string.
    IMPORT_CALLS: ClassVar[FrozenSet[str]] = frozenset({"importlib.import_module", "import_module", "__import__"})

    @staticmethod
    def module_name(relative_path: PurePosixPath) -> Tuple[str, bool]:
        """Return the dotted module name of a source file and whether it is a package's ``__init__``.

        For example ``hisim/components/weather/__init__.py`` gives ``("hisim.components.weather", True)`` and
        ``hisim/log.py`` gives ``("hisim.log", False)``.

        Args:
            relative_path: The file's path relative to the directory that holds the package.

        Returns:
            The module name and whether the file is an ``__init__.py``.

        Raises:
            ValueError: If the path is not a ``.py`` file.
        """
        if relative_path.suffix != ".py":
            raise ValueError(f"'{relative_path}' is not a Python source file.")
        parts = list(relative_path.with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts = parts[:-1]
        return ".".join(parts), is_package

    @staticmethod
    def resolve_relative(target: Optional[str], level: int, importer: str, importer_is_package: bool) -> str:
        """Return the absolute dotted name of a possibly relative import target.

        For example, in ``hisim/energy_system/model.py`` (``importer="hisim.energy_system.model"``) the statement
        ``from .errors import X`` (``target="errors", level=1``) resolves to ``hisim.energy_system.errors``, and
        ``from .. import log`` (``target=None, level=2``) to ``hisim``. In a package's ``__init__`` the package
        itself is the starting point.

        Args:
            target: The dotted name after the dots, or None for ``from . import x``.
            level: The number of leading dots; 0 for an absolute import.
            importer: The dotted name of the importing module.
            importer_is_package: Whether the importer is a package's ``__init__``.

        Returns:
            The absolute dotted name.

        Raises:
            ValueError: If the dots climb above the top-level package.
        """
        if level == 0:
            return target or ""
        parts = importer.split(".")
        if not importer_is_package:
            parts = parts[:-1]
        if level - 1 >= len(parts):
            raise ValueError(f"the relative import of level {level} in '{importer}' climbs above the top-level package.")
        parts = parts[: len(parts) - (level - 1)]
        return ".".join(parts + ([target] if target else []))

    @staticmethod
    def defining_module(dotted: str, known_modules: FrozenSet[str]) -> Optional[str]:
        """Return the longest prefix of a dotted name that is a known module, or None if there is none.

        For example ``hisim.config.ConfigBase`` gives ``hisim.config``, ``hisim.log`` gives ``hisim.log`` and
        ``numpy.linalg`` gives None.
        """
        name = dotted
        while name:
            if name in known_modules:
                return name
            name = name.rpartition(".")[0]
        return None

    @staticmethod
    def collect(source: str, importer: str, importer_is_package: bool, known_modules: FrozenSet[str]) -> List[ImportStatement]:
        """Return every import of a known module in one module's source, in source order.

        A statement that imports several names from one module gives one :class:`ImportStatement` with those names;
        ``from pkg import a, b`` where ``a`` and ``b`` are modules gives one statement per module. A module's import
        of itself is left out.

        Args:
            source: The module's source text.
            importer: The module's dotted name.
            importer_is_package: Whether the module is a package's ``__init__``.
            known_modules: Every module of the package, by dotted name.

        Returns:
            The imports.

        Raises:
            SyntaxError: If the source does not parse.
        """
        walk = _ImportWalk(importer, importer_is_package, known_modules)
        walk.walk(ast.parse(source))
        return [statement for statement in walk.statements if statement.imported != importer]

    @staticmethod
    def collect_package(repository_root: Path, package: str, excluded: Sequence[str]) -> List[ImportStatement]:
        """Return the imports of every module of a package under a repository root, file by file in sorted order.

        Args:
            repository_root: The directory that holds the package directory.
            package: The top-level package, such as ``hisim``.
            excluded: Dotted prefixes whose modules are neither scanned nor importable targets (``hisim.inputs``).

        Returns:
            The imports of all scanned modules.
        """
        sources: Dict[str, Tuple[Path, bool]] = {}
        for path in sorted((repository_root / package).rglob("*.py")):
            module, is_package = ImportCollector.module_name(PurePosixPath(path.relative_to(repository_root).as_posix()))
            if not any(module == prefix or module.startswith(prefix + ".") for prefix in excluded):
                sources[module] = (path, is_package)
        known_modules = frozenset(sources)
        statements: List[ImportStatement] = []
        for module, (path, is_package) in sources.items():
            statements += ImportCollector.collect(path.read_text(encoding="utf-8"), module, is_package, known_modules)
        return statements


class _ImportWalk:
    """The ``ast`` walk behind :meth:`ImportCollector.collect`; tracks whether it is inside a function or ``TYPE_CHECKING``.

    A plain recursive walk with one dispatch, rather than an ``ast.NodeVisitor``, because the scope state (function
    depth, ``TYPE_CHECKING`` depth, the literals bound per scope) must be entered and left around each node's children.
    """

    def __init__(self, importer: str, importer_is_package: bool, known_modules: FrozenSet[str]) -> None:
        """Start a walk over the module ``importer``."""
        self.importer = importer
        self.importer_is_package = importer_is_package
        self.known_modules = known_modules
        self.statements: List[ImportStatement] = []
        self.function_depth = 0
        self.type_checking_depth = 0
        # One mapping per open scope (module, then each enclosing function): name -> the string literal bound to it.
        self.literal_scopes: List[Dict[str, str]] = [{}]

    def walk(self, node: ast.AST) -> None:
        """Record the imports in ``node`` and everything below it."""
        if isinstance(node, ast.Import):
            self._import(node)
        elif isinstance(node, ast.ImportFrom):
            self._import_from(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            self.function_depth += 1
            self.literal_scopes.append({})
            self._walk_children(node)
            self.literal_scopes.pop()
            self.function_depth -= 1
        elif isinstance(node, ast.If) and ast.unparse(node.test).endswith("TYPE_CHECKING"):
            self.type_checking_depth += 1
            for child in node.body:
                self.walk(child)
            self.type_checking_depth -= 1
            for child in node.orelse:
                self.walk(child)
        else:
            if isinstance(node, ast.Assign):
                self._bind(node)
            elif isinstance(node, ast.Call):
                self._call(node)
            self._walk_children(node)

    def _walk_children(self, node: ast.AST) -> None:
        """Walk every direct child of a node."""
        for child in ast.iter_child_nodes(node):
            self.walk(child)

    def _kind(self) -> ImportKind:
        """Return the kind of an import statement at the current position of the walk."""
        if self.type_checking_depth:
            return ImportKind.TYPE_CHECKING
        return ImportKind.LAZY if self.function_depth else ImportKind.STATIC

    def _add(self, imported: str, names: Tuple[str, ...], kind: ImportKind, line: int) -> None:
        """Record one import of a module."""
        self.statements.append(ImportStatement(self.importer, imported, names, kind, line))

    def _import(self, node: ast.Import) -> None:
        """Record ``import a.b.c``: an import of the module ``a.b.c`` (or the known module that defines it)."""
        for alias in node.names:
            module = ImportCollector.defining_module(alias.name, self.known_modules)
            if module is not None:
                self._add(module, (ImportCollector.WHOLE_MODULE,), self._kind(), node.lineno)

    def _import_from(self, node: ast.ImportFrom) -> None:
        """Record ``from x import a, b``: a name that is a submodule imports that module, any other name module ``x``."""
        base = ImportCollector.resolve_relative(node.module, node.level, self.importer, self.importer_is_package)
        base_module = ImportCollector.defining_module(base, self.known_modules)
        names_per_module: Dict[str, List[str]] = {}
        for alias in node.names:
            submodule = f"{base}.{alias.name}"
            if submodule in self.known_modules:
                names_per_module.setdefault(submodule, []).append(ImportCollector.WHOLE_MODULE)
            elif base_module is not None:
                names_per_module.setdefault(base_module, []).append(alias.name)
        for module, names in names_per_module.items():
            self._add(module, tuple(dict.fromkeys(names)), self._kind(), node.lineno)

    def _bind(self, node: ast.Assign) -> None:
        """Remember ``name = "literal"`` in the current scope, for an ``import_module(name)`` below it."""
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                self.literal_scopes[-1][node.targets[0].id] = node.value.value
            else:
                self.literal_scopes[-1].pop(node.targets[0].id, None)

    def _literal(self, node: ast.expr) -> Optional[str]:
        """Return the string a call argument names: a literal, or a name bound to one in an open scope."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            for scope in reversed(self.literal_scopes):
                if node.id in scope:
                    return scope[node.id]
        return None

    def _call(self, node: ast.Call) -> None:
        """Record an import by string: ``import_module``/``__import__``, or a module's exact name passed to any call."""
        if ast.unparse(node.func) in ImportCollector.IMPORT_CALLS:
            arguments = list(node.args[:1]) + [keyword.value for keyword in node.keywords if keyword.arg == "name"]
            target = self._literal(arguments[0]) if arguments else None
            if target is None:
                self._add(ImportCollector.DYNAMIC_TARGET, (ImportCollector.WHOLE_MODULE,), ImportKind.DYNAMIC, node.lineno)
            else:
                module = ImportCollector.defining_module(target, self.known_modules)
                if module is not None:
                    self._add(module, (ImportCollector.WHOLE_MODULE,), ImportKind.IMPORTLIB, node.lineno)
            return
        # A dotted name only: the bare package name is far more often a distribution or program name ("hisim").
        for argument in list(node.args) + [keyword.value for keyword in node.keywords]:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str) and "." in argument.value and argument.value in self.known_modules:
                self._add(argument.value, (ImportCollector.WHOLE_MODULE,), ImportKind.STRING, node.lineno)


class ModuleGraphError(ValueError):
    """The module graph file is inconsistent, or a module belongs to no group."""


@dataclasses.dataclass(frozen=True)
class Registry:
    """A module that loads modules by a name known only at run time, such as a class path from a file.

    Its dynamic imports are not violations, and its imports by string of the groups in ``may_load`` are not either,
    even where the module's group may not import them by a statement.
    """

    module: str
    may_load: FrozenSet[str]
    reason: str


@dataclasses.dataclass(frozen=True)
class ModuleGraph:
    """The module groups, the allowed edges between them and the registries, as read from ``module_graph.yaml``.

    Terms: a *group* is a set of modules given by dotted prefixes (``hisim.economics`` holds every module below it);
    an *edge* ``a -> b`` says that a module of group ``a`` may import a module of group ``b``. Imports inside a group
    are always allowed.
    """

    package: str
    package_root_group: str
    excluded: Tuple[str, ...]
    prefixes: Mapping[str, str]
    may_import: Mapping[str, FrozenSet[str]]
    registries: Mapping[str, Registry]
    forbid_private_names_across_groups: bool

    #: The top-level keys of the graph file.
    DOCUMENT_KEYS: ClassVar[FrozenSet[str]] = frozenset(
        {"package", "package_root_group", "excluded", "forbid_private_names_across_groups", "groups", "registries"}
    )

    @staticmethod
    def from_document(document: Mapping) -> "ModuleGraph":
        """Return the graph of a parsed ``module_graph.yaml``, after checking that it is consistent.

        Args:
            document: The file as ``yaml.safe_load`` returns it.

        Returns:
            The graph.

        Raises:
            ModuleGraphError: If a key is missing or unknown, a prefix belongs to two groups, an edge or a registry
                names an unknown group, a group lists itself, or the allowed edges form a cycle.
        """
        missing = sorted(ModuleGraph.DOCUMENT_KEYS - set(document))
        unknown = sorted(set(document) - ModuleGraph.DOCUMENT_KEYS)
        if missing or unknown:
            raise ModuleGraphError(f"the module graph lacks the keys {missing} or has the unknown keys {unknown}.")
        groups: Mapping[str, Mapping] = document["groups"]
        prefixes: Dict[str, str] = {}
        may_import: Dict[str, FrozenSet[str]] = {}
        for group, spec in groups.items():
            if set(spec) - {"description", "modules", "may_import"}:
                raise ModuleGraphError(f"the group '{group}' has the unknown keys {sorted(set(spec) - {'description', 'modules', 'may_import'})}.")
            for prefix in spec.get("modules") or []:
                if prefix in prefixes:
                    raise ModuleGraphError(f"the prefix '{prefix}' is listed in both '{prefixes[prefix]}' and '{group}'; list it once.")
                prefixes[prefix] = group
            may_import[group] = frozenset(spec.get("may_import") or [])
        for group, targets in may_import.items():
            if group in targets:
                raise ModuleGraphError(f"the group '{group}' lists itself in may_import; a group may always import itself, so remove it.")
            unknown_targets = sorted(targets - set(groups))
            if unknown_targets:
                raise ModuleGraphError(f"the group '{group}' may import the unknown groups {unknown_targets}.")
        if document["package_root_group"] not in groups:
            raise ModuleGraphError(f"package_root_group '{document['package_root_group']}' is not a group.")
        registries: Dict[str, Registry] = {}
        for module, spec in (document["registries"] or {}).items():
            may_load = frozenset(spec.get("may_load") or [])
            if sorted(may_load - set(groups)) or not spec.get("reason"):
                raise ModuleGraphError(f"the registry '{module}' needs a reason and may load only known groups, not {sorted(may_load - set(groups))}.")
            registries[module] = Registry(module, may_load, str(spec["reason"]))
        ModuleGraph.topological_order(may_import)
        return ModuleGraph(
            package=str(document["package"]),
            package_root_group=str(document["package_root_group"]),
            excluded=tuple(document["excluded"] or ()),
            prefixes=prefixes,
            may_import=may_import,
            registries=registries,
            forbid_private_names_across_groups=bool(document["forbid_private_names_across_groups"]),
        )

    @staticmethod
    def load(path: Path) -> "ModuleGraph":
        """Return the graph of the YAML file at ``path``; see :meth:`from_document` for what it refuses."""
        return ModuleGraph.from_document(yaml.safe_load(path.read_text(encoding="utf-8")))

    @staticmethod
    def topological_order(may_import: Mapping[str, FrozenSet[str]]) -> List[str]:
        """Return the groups bottom first, each after every group it may import; ties in name order.

        For example ``{"a": {"b"}, "b": set()}`` gives ``["b", "a"]``.

        Raises:
            ModuleGraphError: If the allowed edges form a cycle; the message names the groups on it.
        """
        remaining = {group: set(targets) for group, targets in may_import.items()}
        order: List[str] = []
        while remaining:
            ready = sorted(group for group, targets in remaining.items() if not targets - set(order))
            if not ready:
                raise ModuleGraphError(f"the allowed edges form a cycle among {sorted(remaining)}; the graph must be acyclic.")
            order += ready
            for group in ready:
                del remaining[group]
        return order

    def group_of(self, module: str) -> str:
        """Return the group of a module: the group of the longest prefix that is the module or a package above it.

        For example, with the prefixes ``hisim.components`` (components) and ``hisim.components.shared`` (shared
        code), ``hisim.components.shared.modes`` is in shared code and ``hisim.components.boiler`` in components.
        The package's own ``__init__`` (``hisim``) is in ``package_root_group``.

        Raises:
            ModuleGraphError: If no prefix matches; the message says where to add one.
        """
        if module == self.package:
            return self.package_root_group
        best = ""
        for prefix in self.prefixes:
            if (module == prefix or module.startswith(prefix + ".")) and len(prefix) > len(best):
                best = prefix
        if not best:
            raise ModuleGraphError(
                f"the module '{module}' belongs to no group; add it, or a package above it, to the 'modules' of a group in system_docs/module_graph.yaml."
            )
        return self.prefixes[best]

    def is_allowed(self, importer_group: str, imported_group: str) -> bool:
        """Return whether a module of one group may import a module of the other by a statement."""
        return importer_group == imported_group or imported_group in self.may_import[importer_group]


class ModuleGraphRatchet:
    """The import-graph side of the ratchet: which imports are violations, and how a finding is explained."""

    #: The baseline sections: violations by a statement; by a statement under TYPE_CHECKING; by a string.
    IMPORTS: ClassVar[str] = "imports"
    TYPE_CHECKING_IMPORTS: ClassVar[str] = "type_checking_imports"
    STRING_IMPORTS: ClassVar[str] = "string_imports"

    LAYOUT: ClassVar[BaselineLayout] = BaselineLayout(
        sections=(IMPORTS, TYPE_CHECKING_IMPORTS, STRING_IMPORTS),
        key_fields=("importer", "imported", "name"),
        header=(
            "Today's imports that system_docs/module_graph.yaml forbids, each with the bead that will remove it.",
            "Checked by tests/test_module_graph.py: a violation not listed here fails, and so does an entry that",
            "no longer matches or whose count went down. Delete or lower entries with `python scripts/module_graph.py prune`.",
            "An entry is added only with a bead id and the owner's approval in review.",
            "count: the number of import statements of that name. name: the imported name, or * for the module itself.",
            "imports: import statements (module level or in a function); type_checking_imports: under TYPE_CHECKING only;",
            "string_imports: importlib.import_module, __import__ or a module name passed to a call, outside a registry.",
        ),
    )

    @staticmethod
    def section_of(kind: ImportKind) -> str:
        """Return the baseline section a violation of an import of this kind is listed in."""
        if kind == ImportKind.TYPE_CHECKING:
            return ModuleGraphRatchet.TYPE_CHECKING_IMPORTS
        return ModuleGraphRatchet.STRING_IMPORTS if kind.is_by_string else ModuleGraphRatchet.IMPORTS

    @staticmethod
    def is_private(name: str) -> bool:
        """Return whether an imported name is private to its module: a leading underscore, not a dunder."""
        return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))

    @staticmethod
    def violations(statements: Iterable[ImportStatement], graph: ModuleGraph) -> Counter:
        """Return the number of violating statements per baseline key.

        A statement violates the graph when it crosses an edge the graph does not allow, imports a private name of
        another group (if the graph forbids that), or imports a module by a string outside a registry. A registry
        may import by string the groups its ``may_load`` lists, and its unreadable dynamic imports are allowed.

        Args:
            statements: The imports of the package.
            graph: The module graph.

        Returns:
            A ``Counter`` of :class:`EntryKey` to the number of statements; one statement that imports two names
            counts once under each name's key.
        """
        counts: Counter = Counter()
        for statement in statements:
            registry = graph.registries.get(statement.importer)
            if statement.kind == ImportKind.DYNAMIC:
                if registry is None:
                    counts[EntryKey(ModuleGraphRatchet.STRING_IMPORTS, (statement.importer, statement.imported, ImportCollector.WHOLE_MODULE))] += 1
                continue
            importer_group = graph.group_of(statement.importer)
            imported_group = graph.group_of(statement.imported)
            section = ModuleGraphRatchet.section_of(statement.kind)
            if statement.kind.is_by_string:
                edge_ok = registry is not None and (graph.is_allowed(importer_group, imported_group) or imported_group in registry.may_load)
            else:
                edge_ok = graph.is_allowed(importer_group, imported_group)
            for name in statement.names:
                private = graph.forbid_private_names_across_groups and importer_group != imported_group and ModuleGraphRatchet.is_private(name)
                if not edge_ok or private:
                    counts[EntryKey(section, (statement.importer, statement.imported, name))] += 1
        return counts

    @staticmethod
    def edge_label(key: EntryKey, graph: ModuleGraph) -> str:
        """Return the group edge of a violation, such as ``postprocessing -> components``, for messages and reports."""
        importer, imported, _ = key.fields
        imported_group = "<dynamic>" if imported == ImportCollector.DYNAMIC_TARGET else graph.group_of(imported)
        return f"{graph.group_of(importer)} -> {imported_group}"

    @staticmethod
    def reason(key: EntryKey, graph: ModuleGraph) -> str:
        """Return why the import of a key is a violation, in one clause."""
        importer, imported, name = key.fields
        if imported == ImportCollector.DYNAMIC_TARGET:
            return "imports a module whose name cannot be read from the source, outside a registry of the module graph"
        importer_group, imported_group = graph.group_of(importer), graph.group_of(imported)
        if key.section == ModuleGraphRatchet.STRING_IMPORTS and graph.is_allowed(importer_group, imported_group):
            return "imports a module by a string outside a registry (coding_style.md, code rule 7: a plain import, or fix the cycle it dodges)"
        if graph.is_allowed(importer_group, imported_group):
            return f"imports the private name '{name}' of another group"
        return f"crosses the edge {importer_group} -> {imported_group}, which system_docs/module_graph.yaml does not allow"

    @staticmethod
    def describe(finding: Finding, graph: ModuleGraph, lines: Mapping[EntryKey, List[int]]) -> str:
        """Return the failure message of one finding: what was found, why it is one, and the remedies."""
        importer, imported, name = finding.key.fields
        where = ", ".join(str(line) for line in lines.get(finding.key, [])) or "-"
        entry = BaselineFile.render(
            {finding.key: BaselineEntry(finding.key, max(finding.measured_count, 1), "<bead id>")}, dataclasses.replace(ModuleGraphRatchet.LAYOUT, header=())
        ).splitlines()
        entry_line = next(line for line in entry if line.startswith("- "))
        if finding.kind == FindingKind.NEW:
            return (
                f"NEW violation [{finding.key.section}]: {importer} (lines {where}) imports {name} from {imported}: it "
                f"{ModuleGraphRatchet.reason(finding.key, graph)}.\n"
                f"  Remedy 1: fix the import (move the code to a group this group may import, or pass the value in).\n"
                f"  Remedy 2: add under '{finding.key.section}:' in tests/module_graph_baseline.yaml, with the bead that will remove it "
                f"(needs the owner's approval in review):\n    {entry_line}"
            )
        if finding.kind == FindingKind.GREW:
            return (
                f"GREW [{finding.key.section}]: {importer} (lines {where}) imports {name} from {imported} in {finding.measured_count} statements, "
                f"but the baseline allows {finding.baseline_count} (bead {finding.bead}); it {ModuleGraphRatchet.reason(finding.key, graph)}.\n"
                f"  Remedy: remove the new statement; a baseline count is never raised without the owner's approval in review."
            )
        if finding.kind == FindingKind.GONE:
            return (
                f"STALE [{finding.key.section}]: the baseline entry {importer} -> {imported} ({name}), bead {finding.bead}, no longer matches any import.\n"
                f"  Remedy: delete it (python scripts/module_graph.py prune), so the fix cannot regress silently."
            )
        return (
            f"STALE [{finding.key.section}]: {importer} imports {name} from {imported} in {finding.measured_count} statements, the baseline "
            f"allows {finding.baseline_count} (bead {finding.bead}).\n"
            f"  Remedy: lower the count to {finding.measured_count} (python scripts/module_graph.py prune)."
        )


@dataclasses.dataclass(frozen=True)
class ModuleGraphCheck:
    """One run of the check over a repository: the graph, the measurement, the baseline and the beads.

    Build it with :meth:`for_repository`; :meth:`problems` gives every failure message, empty when the check passes.
    """

    graph: ModuleGraph
    statements: Tuple[ImportStatement, ...]
    measured: Mapping[EntryKey, int]
    baseline: Mapping[EntryKey, BaselineEntry]
    baseline_text: str
    bead_ids: FrozenSet[str]

    #: The files of the check, relative to the repository root.
    GRAPH_PATH: ClassVar[str] = "system_docs/module_graph.yaml"
    BASELINE_PATH: ClassVar[str] = "tests/module_graph_baseline.yaml"
    BEADS_PATH: ClassVar[str] = ".beads/issues.jsonl"

    @staticmethod
    def repository_root() -> Path:
        """Return the repository root, found from this file, so the check works from any working directory."""
        return Path(__file__).resolve().parent.parent

    @staticmethod
    def for_repository(root: Path, baseline_path: Optional[Path] = None) -> "ModuleGraphCheck":
        """Read the graph, the baseline and the beads of a repository and collect its imports.

        Args:
            root: The repository root.
            baseline_path: The baseline to compare with; the committed one when None.
        """
        graph = ModuleGraph.load(root / ModuleGraphCheck.GRAPH_PATH)
        statements = tuple(ImportCollector.collect_package(root, graph.package, graph.excluded))
        baseline_text = (baseline_path or root / ModuleGraphCheck.BASELINE_PATH).read_text(encoding="utf-8")
        return ModuleGraphCheck(
            graph=graph,
            statements=statements,
            measured=ModuleGraphRatchet.violations(statements, graph),
            baseline=BaselineFile.parse(baseline_text, ModuleGraphRatchet.LAYOUT),
            baseline_text=baseline_text,
            bead_ids=BeadIds.read(root / ModuleGraphCheck.BEADS_PATH),
        )

    def lines(self) -> Dict[EntryKey, List[int]]:
        """Return the source lines of the statements behind each measured key, for the failure messages."""
        result: Dict[EntryKey, List[int]] = {}
        single = ModuleGraphRatchet.violations
        for statement in self.statements:
            for key in single([statement], self.graph):
                result.setdefault(key, []).append(statement.line)
        return result

    def problems(self) -> List[str]:
        """Return every failure message of the check; an empty list when the code and the baseline agree."""
        problems: List[str] = []
        unknown = Ratchet.unknown_beads(self.baseline, self.bead_ids)
        if unknown:
            problems.append(f"the baseline names the beads {unknown}, which are not in {self.BEADS_PATH}; name an existing bead or create it.")
        if BaselineFile.render(self.baseline, ModuleGraphRatchet.LAYOUT) != self.baseline_text:
            problems.append(f"{self.BASELINE_PATH} is not in its canonical form (sorted, one entry per line); rewrite it with python scripts/module_graph.py prune.")
        lines = self.lines()
        problems += [ModuleGraphRatchet.describe(finding, self.graph, lines) for finding in Ratchet.compare(self.measured, self.baseline)]
        return problems

    def report(self) -> str:
        """Return the Markdown report of the baseline per bead and per edge, with the state of the check."""
        entries = list(self.baseline.values())
        findings = Ratchet.compare(self.measured, self.baseline)
        regressions = sum(1 for finding in findings if finding.kind.is_regression)
        parts = [
            "## Module graph ratchet",
            "",
            f"{len(entries)} baseline entries carrying {sum(entry.count for entry in entries)} occurrences (one per imported name and statement); "
            f"{regressions} new or grown violations, {len(findings) - regressions} stale entries.",
            "",
            Ratchet.markdown_table("Per bead", "bead", Ratchet.summary(entries, lambda entry: entry.bead)),
            Ratchet.markdown_table("Per edge", "edge", Ratchet.summary(entries, lambda entry: ModuleGraphRatchet.edge_label(entry.key, self.graph))),
            Ratchet.markdown_table("Per section", "section", Ratchet.summary(entries, lambda entry: entry.key.section)),
        ]
        return "\n".join(parts)


class ModuleGraphCli:
    """The command line: ``check``, ``report`` and ``prune``."""

    @staticmethod
    def main(argv: Optional[Sequence[str]] = None) -> int:
        """Run one command and return its exit code: 0 passed or done, 1 the check found problems."""
        parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument("command", choices=["check", "report", "prune"])
        parser.add_argument("--root", type=Path, default=ModuleGraphCheck.repository_root(), help="the repository root (default: this checkout)")
        parser.add_argument("--baseline", type=Path, default=None, help=f"the baseline file (default: <root>/{ModuleGraphCheck.BASELINE_PATH})")
        arguments = parser.parse_args(argv)
        baseline_path: Path = arguments.baseline or arguments.root / ModuleGraphCheck.BASELINE_PATH
        check = ModuleGraphCheck.for_repository(arguments.root, baseline_path)
        if arguments.command == "report":
            sys.stdout.write(check.report())
            return 0
        if arguments.command == "prune":
            pruned = Ratchet.pruned(check.baseline, check.measured)
            BaselineFile.write(baseline_path, pruned, ModuleGraphRatchet.LAYOUT)
            removed = len(check.baseline) - len(pruned)
            lowered = sum(1 for key, entry in pruned.items() if entry.count < check.baseline[key].count)
            sys.stdout.write(f"{baseline_path}: {removed} entries deleted, {lowered} lowered, {len(pruned)} left.\n")
            return 0
        problems = check.problems()
        for problem in problems:
            sys.stdout.write(problem + "\n\n")
        sys.stdout.write(f"module graph: {len(problems)} problems, {len(check.baseline)} baseline entries, {len(check.statements)} imports checked.\n")
        return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(ModuleGraphCli.main())
