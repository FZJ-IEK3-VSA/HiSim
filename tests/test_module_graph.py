"""Every import under ``hisim/`` follows the module graph, except today's violations listed in the baseline.

The module graph, ``system_docs/module_graph.yaml``, sorts every module into a group and says which groups each group
may import; ``tests/module_graph_baseline.yaml`` lists today's violations, each with the bead that will remove it
(``system_docs/module_graph.md``). :class:`TestTheRepository` is the check itself: a violation not in the baseline
fails, and so does a baseline entry that no longer matches. The other classes test the parts of
``scripts/module_graph.py`` directly on small synthetic sources and graphs.

``TYPE_CHECKING`` imports count: a type-only import still ties the importer's code to the other group's types, and
moving those types is the same work as for a run-time import. They are listed in a section of their own, because
they load nothing at run time and so cause no import cycle.
"""
from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath
from typing import Dict, FrozenSet, List

import pytest

from scripts.module_graph import (
    ImportCollector,
    ImportKind,
    ImportStatement,
    ModuleGraph,
    ModuleGraphCheck,
    ModuleGraphCli,
    ModuleGraphError,
    ModuleGraphRatchet,
)
from scripts.ratchet import BaselineEntry, BaselineFile, EntryKey, Finding, FindingKind

pytestmark = pytest.mark.base


class Synthetic:
    """A small package and graph for the unit tests: ``pkg.low`` (group low) below ``pkg.high`` (group high)."""

    MODULES: FrozenSet[str] = frozenset(
        {"pkg", "pkg.low", "pkg.low.a", "pkg.low.b", "pkg.low.special", "pkg.low.special.x", "pkg.high", "pkg.high.c", "pkg.high.loader", "pkg.side"}
    )

    @staticmethod
    def document() -> Dict:
        """Return the graph file of the synthetic package as parsed YAML."""
        return {
            "package": "pkg",
            "package_root_group": "low",
            "excluded": [],
            "forbid_private_names_across_groups": True,
            "groups": {
                "low": {"modules": ["pkg.low"], "may_import": []},
                "special": {"modules": ["pkg.low.special"], "may_import": ["low"]},
                "high": {"modules": ["pkg.high"], "may_import": ["low", "special"]},
                "side": {"modules": ["pkg.side"], "may_import": []},
            },
            "registries": {"pkg.high.loader": {"may_load": ["side"], "reason": "loads plug-ins by name"}},
        }

    @staticmethod
    def graph() -> ModuleGraph:
        """Return the graph of :meth:`document`."""
        return ModuleGraph.from_document(Synthetic.document())

    @staticmethod
    def collect(source: str, importer: str = "pkg.high.c", is_package: bool = False) -> List[ImportStatement]:
        """Return the imports of a source text as the module ``importer`` of the synthetic package."""
        return ImportCollector.collect(source, importer, is_package, Synthetic.MODULES)

    @staticmethod
    def statement(importer: str, imported: str, names: tuple, kind: ImportKind = ImportKind.STATIC) -> ImportStatement:
        """Return an import statement on line 1."""
        return ImportStatement(importer, imported, names, kind, 1)


class TestModuleNames:
    """``ImportCollector.module_name``, ``resolve_relative`` and ``defining_module``."""

    def test_module_name_of_a_file_and_a_package(self) -> None:
        """A wrong module name would put a file into the wrong group."""
        assert ImportCollector.module_name(PurePosixPath("hisim/log.py")) == ("hisim.log", False)
        assert ImportCollector.module_name(PurePosixPath("hisim/components/weather/__init__.py")) == ("hisim.components.weather", True)
        with pytest.raises(ValueError, match="not a Python source"):
            ImportCollector.module_name(PurePosixPath("hisim/data.json"))

    def test_relative_imports_resolve_against_the_package(self) -> None:
        """A relative import resolved one level off would check the wrong edge."""
        assert ImportCollector.resolve_relative("errors", 1, "hisim.energy_system.model", False) == "hisim.energy_system.errors"
        assert ImportCollector.resolve_relative(None, 2, "hisim.energy_system.model", False) == "hisim"
        assert ImportCollector.resolve_relative("x", 1, "hisim.energy_system", True) == "hisim.energy_system.x"
        assert ImportCollector.resolve_relative("hisim.log", 0, "hisim.a", False) == "hisim.log"
        with pytest.raises(ValueError, match="climbs above"):
            ImportCollector.resolve_relative("x", 3, "hisim.a", False)

    def test_defining_module_is_the_longest_known_prefix(self) -> None:
        """An import of a class must count as an import of the module that defines it; outside imports are ignored."""
        assert ImportCollector.defining_module("pkg.low.a.Thing", Synthetic.MODULES) == "pkg.low.a"
        assert ImportCollector.defining_module("pkg.low", Synthetic.MODULES) == "pkg.low"
        assert ImportCollector.defining_module("numpy.linalg", Synthetic.MODULES) is None


class TestCollector:
    """``ImportCollector.collect`` sees every spelling of an import."""

    def test_static_import_and_from_import(self) -> None:
        """Both statement forms must be seen, with the names a ``from`` import takes."""
        statements = Synthetic.collect("import pkg.low.a\nfrom pkg.low.b import X, Y\nimport numpy\n")
        assert [(s.imported, s.names, s.kind) for s in statements] == [
            ("pkg.low.a", ("*",), ImportKind.STATIC),
            ("pkg.low.b", ("X", "Y"), ImportKind.STATIC),
        ]

    def test_from_a_package_import_a_submodule(self) -> None:
        """``from pkg.low import a`` imports the module ``pkg.low.a``, not a name of ``pkg.low``."""
        statements = Synthetic.collect("from pkg.low import a, helper\n")
        assert [(s.imported, s.names) for s in statements] == [("pkg.low.a", ("*",)), ("pkg.low", ("helper",))]

    def test_function_local_import_is_lazy(self) -> None:
        """A lazy import is still an import; hiding one in a function must not dodge the check."""
        statements = Synthetic.collect("def f():\n    from pkg.low.a import X\n")
        assert [(s.imported, s.kind, s.line) for s in statements] == [("pkg.low.a", ImportKind.LAZY, 2)]

    def test_type_checking_imports_are_their_own_kind(self) -> None:
        """TYPE_CHECKING imports count, listed apart; the else branch is an ordinary import."""
        source = "import typing\nif typing.TYPE_CHECKING:\n    from pkg.low.a import X\nelse:\n    from pkg.low.b import Y\n"
        statements = Synthetic.collect(source)
        assert [(s.imported, s.kind) for s in statements] == [("pkg.low.a", ImportKind.TYPE_CHECKING), ("pkg.low.b", ImportKind.STATIC)]

    def test_relative_import(self) -> None:
        """A relative import must resolve to the absolute module before its group is looked up."""
        statements = Synthetic.collect("from ..low.a import X\nfrom . import loader\n")
        assert [(s.imported, s.names) for s in statements] == [("pkg.low.a", ("X",)), ("pkg.high.loader", ("*",))]

    def test_importlib_with_a_literal_or_a_bound_name(self) -> None:
        """The importlib workaround for a circular import must be seen as the import it is."""
        source = (
            "import importlib\n"
            "def f():\n"
            "    importlib.import_module('pkg.low.a')\n"
            "    name = 'pkg.low.b'\n"
            "    importlib.import_module(name=name)\n"
            "    __import__('pkg.side')\n"
        )
        statements = Synthetic.collect(source)
        assert [(s.imported, s.kind) for s in statements] == [
            ("pkg.low.a", ImportKind.IMPORTLIB),
            ("pkg.low.b", ImportKind.IMPORTLIB),
            ("pkg.side", ImportKind.IMPORTLIB),
        ]

    def test_importlib_with_an_unreadable_target_is_dynamic(self) -> None:
        """An import whose target cannot be read must still be seen, so it can be confined to a registry."""
        statements = Synthetic.collect("import importlib\ndef f(path):\n    return importlib.import_module(path)\n")
        assert [(s.imported, s.kind) for s in statements] == [(ImportCollector.DYNAMIC_TARGET, ImportKind.DYNAMIC)]

    def test_a_module_name_passed_to_a_call_is_a_string_import(self) -> None:
        """A helper that imports by name hides an import; its literal argument names the module."""
        source = "def f():\n    load('pkg.low.a', 'X')\n    log('pkg')\n    log('pkg.unknown')\n"
        statements = Synthetic.collect(source)
        assert [(s.imported, s.kind) for s in statements] == [("pkg.low.a", ImportKind.STRING)]

    def test_a_module_importing_itself_is_left_out(self) -> None:
        """A package ``__init__`` naming itself is no edge."""
        statements = Synthetic.collect("from pkg.high import c\nimport pkg.high\n", importer="pkg.high", is_package=True)
        assert [s.imported for s in statements] == ["pkg.high.c"]


class TestGraph:
    """``ModuleGraph``: loading, group lookup and order."""

    def test_longest_prefix_wins(self) -> None:
        """A nested group (shared code inside components) must win over its enclosing package."""
        graph = Synthetic.graph()
        assert graph.group_of("pkg.low.special.x") == "special"
        assert graph.group_of("pkg.low.special") == "special"
        assert graph.group_of("pkg.low.a") == "low"
        assert graph.group_of("pkg") == "low"

    def test_an_unknown_module_fails_loudly_with_the_remedy(self) -> None:
        """A module outside every group would otherwise escape the check."""
        with pytest.raises(ModuleGraphError, match="belongs to no group; add it"):
            Synthetic.graph().group_of("pkg.lowly")

    def test_topological_order_and_cycle(self) -> None:
        """The allowed graph must be acyclic, or the seams would allow a loop."""
        assert ModuleGraph.topological_order({"a": frozenset({"b"}), "b": frozenset()}) == ["b", "a"]
        with pytest.raises(ModuleGraphError, match="cycle"):
            ModuleGraph.topological_order({"a": frozenset({"b"}), "b": frozenset({"a"})})

    @pytest.mark.parametrize(
        "change, message",
        [
            (lambda d: d["groups"]["side"].update(modules=["pkg.low"]), "listed in both"),
            (lambda d: d["groups"]["side"].update(may_import=["nowhere"]), "unknown groups"),
            (lambda d: d["groups"]["side"].update(may_import=["side"]), "lists itself"),
            (lambda d: d["groups"]["low"].update(may_import=["high"]), "cycle"),
            (lambda d: d.pop("excluded"), "lacks the keys"),
            (lambda d: d["registries"]["pkg.high.loader"].pop("reason"), "needs a reason"),
            (lambda d: d.update(package_root_group="nowhere"), "is not a group"),
            (lambda d: d["groups"]["side"].update(color="red"), "unknown keys"),
        ],
    )
    def test_inconsistent_graphs_are_refused(self, change, message: str) -> None:
        """A graph file with a mistake must fail when read, not check against a wrong graph."""
        document = Synthetic.document()
        change(document)
        with pytest.raises(ModuleGraphError, match=message):
            ModuleGraph.from_document(document)


class TestViolations:
    """``ModuleGraphRatchet.violations``: which imports break the graph."""

    @staticmethod
    def keys(*statements: ImportStatement) -> Dict[EntryKey, int]:
        """Return the violations of the statements under the synthetic graph."""
        return dict(ModuleGraphRatchet.violations(statements, Synthetic.graph()))

    def test_allowed_and_same_group_imports_pass(self) -> None:
        """Allowed edges and imports inside a group are never violations."""
        assert not self.keys(Synthetic.statement("pkg.high.c", "pkg.low.a", ("X",)), Synthetic.statement("pkg.low.a", "pkg.low.b", ("_y",)))

    def test_a_forbidden_edge_counts_once_per_name(self) -> None:
        """Each name of a forbidden import is its own entry, so adding a name to it is a new violation."""
        statements = [Synthetic.statement("pkg.low.a", "pkg.high.c", ("X", "Y")), Synthetic.statement("pkg.low.b", "pkg.high.c", ("X",))]
        assert self.keys(*statements) == {
            EntryKey("imports", ("pkg.low.a", "pkg.high.c", "X")): 1,
            EntryKey("imports", ("pkg.low.a", "pkg.high.c", "Y")): 1,
            EntryKey("imports", ("pkg.low.b", "pkg.high.c", "X")): 1,
        }

    def test_private_names_of_another_group_are_violations_but_dunders_are_not(self) -> None:
        """An allowed edge does not allow reaching into another group's private names."""
        statement = Synthetic.statement("pkg.high.c", "pkg.low.a", ("_hidden", "__version__", "Public"))
        assert self.keys(statement) == {EntryKey("imports", ("pkg.high.c", "pkg.low.a", "_hidden")): 1}

    def test_type_checking_violations_have_their_own_section(self) -> None:
        """Type-only imports count, but apart from run-time ones."""
        statement = Synthetic.statement("pkg.low.a", "pkg.high.c", ("X",), ImportKind.TYPE_CHECKING)
        assert self.keys(statement) == {EntryKey("type_checking_imports", ("pkg.low.a", "pkg.high.c", "X")): 1}

    def test_imports_by_string_are_violations_outside_a_registry(self) -> None:
        """An importlib workaround is a violation even along an allowed edge or inside a group."""
        statements = [
            Synthetic.statement("pkg.high.c", "pkg.low.a", ("*",), ImportKind.IMPORTLIB),
            Synthetic.statement("pkg.high.c", "pkg.high.loader", ("*",), ImportKind.STRING),
            Synthetic.statement("pkg.high.c", ImportCollector.DYNAMIC_TARGET, ("*",), ImportKind.DYNAMIC),
        ]
        assert set(self.keys(*statements)) == {
            EntryKey("string_imports", ("pkg.high.c", "pkg.low.a", "*")),
            EntryKey("string_imports", ("pkg.high.c", "pkg.high.loader", "*")),
            EntryKey("string_imports", ("pkg.high.c", ImportCollector.DYNAMIC_TARGET, "*")),
        }

    def test_a_registry_may_load_its_groups_and_import_dynamically(self) -> None:
        """A registry's purpose is loading by name: within its may_load and its own edges, that is no violation."""
        statements = [
            Synthetic.statement("pkg.high.loader", "pkg.side", ("*",), ImportKind.IMPORTLIB),
            Synthetic.statement("pkg.high.loader", "pkg.low.a", ("*",), ImportKind.STRING),
            Synthetic.statement("pkg.high.loader", ImportCollector.DYNAMIC_TARGET, ("*",), ImportKind.DYNAMIC),
        ]
        assert not self.keys(*statements)

    def test_a_registry_may_not_import_other_groups_by_statement(self) -> None:
        """may_load exempts imports by string only; a plain import of the same group is still checked."""
        statement = Synthetic.statement("pkg.high.loader", "pkg.side", ("X",))
        assert self.keys(statement) == {EntryKey("imports", ("pkg.high.loader", "pkg.side", "X")): 1}

    def test_section_of_and_is_private(self) -> None:
        """The section and the private-name rule are what the baseline is keyed by."""
        assert ModuleGraphRatchet.section_of(ImportKind.LAZY) == "imports"
        assert ModuleGraphRatchet.section_of(ImportKind.TYPE_CHECKING) == "type_checking_imports"
        assert ModuleGraphRatchet.section_of(ImportKind.DYNAMIC) == "string_imports"
        assert ModuleGraphRatchet.is_private("_x") and not ModuleGraphRatchet.is_private("__all__") and not ModuleGraphRatchet.is_private("x")

    def test_messages_name_the_edge_and_the_remedies(self) -> None:
        """A failure that does not say what to do costs every contributor a search."""
        graph = Synthetic.graph()
        key = EntryKey("imports", ("pkg.low.a", "pkg.high.c", "X"))
        new = ModuleGraphRatchet.describe(Finding(FindingKind.NEW, key, 1, 0, ""), graph, {key: [7]})
        assert "pkg.low.a (lines 7) imports X from pkg.high.c" in new
        assert "low -> high" in new and "Remedy 1: fix the import" in new and "owner's approval" in new
        assert '- {importer: "pkg.low.a", imported: "pkg.high.c", name: "X", count: 1, bead: "<bead id>"}' in new
        stale = ModuleGraphRatchet.describe(Finding(FindingKind.GONE, key, 0, 1, "b-1"), graph, {})
        assert stale.startswith("STALE") and "delete it" in stale
        lower = ModuleGraphRatchet.describe(Finding(FindingKind.LOWER, key, 1, 2, "b-1"), graph, {})
        assert "lower the count to 1" in lower
        grew = ModuleGraphRatchet.describe(Finding(FindingKind.GREW, key, 3, 2, "b-1"), graph, {})
        assert grew.startswith("GREW") and "never raised" in grew
        assert ModuleGraphRatchet.edge_label(key, graph) == "low -> high"


@pytest.fixture(name="repository_check", scope="module")
def fixture_repository_check() -> ModuleGraphCheck:
    """The check over this repository, collected once for the tests that read it (about a second)."""
    return ModuleGraphCheck.for_repository(ModuleGraphCheck.repository_root())


class TestTheRepository:
    """The check on this repository: the graph, every import under hisim/, the baseline and the beads."""

    def test_every_import_follows_the_graph_or_the_baseline(self, repository_check: ModuleGraphCheck) -> None:
        """A new forbidden import, a stale baseline entry, an unknown bead or an unsorted baseline fails here."""
        problems = repository_check.problems()
        assert not problems, "\n\n".join(problems)

    def test_the_report_lists_every_entry_per_bead_and_per_edge(self, repository_check: ModuleGraphCheck) -> None:
        """A report whose totals miss entries would understate what is left to fix."""
        report = repository_check.report()
        total = f"| **total** | **{len(repository_check.baseline)}** | **{sum(entry.count for entry in repository_check.baseline.values())}** |"
        assert "### Per bead" in report and "### Per edge" in report
        assert report.count(total) == 3

    def test_the_graph_names_every_top_level_module(self) -> None:
        """Every module of hisim/ must belong to a group; an unknown one would fail the whole check with no edge."""
        root = ModuleGraphCheck.repository_root()
        graph = ModuleGraph.load(root / ModuleGraphCheck.GRAPH_PATH)
        for path in sorted((root / "hisim").glob("*.py")):
            assert graph.group_of(ImportCollector.module_name(PurePosixPath(path.relative_to(root).as_posix()))[0])

    def test_prune_only_deletes_or_lowers(self, tmp_path: Path) -> None:
        """Prune must drop a stale entry and lower a count, and must not add the violations the baseline lacks."""
        root = ModuleGraphCheck.repository_root()
        baseline_path = tmp_path / "baseline.yaml"
        shutil.copy(root / ModuleGraphCheck.BASELINE_PATH, baseline_path)
        entries = BaselineFile.read(baseline_path, ModuleGraphRatchet.LAYOUT)
        dropped = sorted(entries)[0]
        stale = EntryKey("imports", ("hisim.utils", "hisim.simulator", "Gone"))
        raised = sorted(entries)[1]
        changed = {key: entry for key, entry in entries.items() if key != dropped}
        changed[stale] = BaselineEntry(stale, 1, entries[raised].bead)
        changed[raised] = BaselineEntry(raised, entries[raised].count + 2, entries[raised].bead)
        BaselineFile.write(baseline_path, changed, ModuleGraphRatchet.LAYOUT)
        assert ModuleGraphCli.main(["prune", "--root", str(root), "--baseline", str(baseline_path)]) == 0
        pruned = BaselineFile.read(baseline_path, ModuleGraphRatchet.LAYOUT)
        assert dropped not in pruned and stale not in pruned
        assert pruned[raised].count == entries[raised].count
        assert pruned == {key: entry for key, entry in entries.items() if key != dropped}
