#!/usr/bin/env python3
"""Flag attribute accesses on ``ConfigBase`` dataclasses that name a non-existent field.

Why this exists (and why mypy cannot do it)
-------------------------------------------
:class:`hisim.component.ConfigBase` inherits from ``dataclass_wizard.JSONWizard``.
``dataclass_wizard`` ships no ``py.typed`` marker, so under ``ignore_missing_imports``
mypy resolves ``JSONWizard`` to ``Any``. A class with an ``Any`` base is treated as
having *arbitrary* attributes, so mypy silently accepts ``config.lifetime`` even when
the dataclass only declares ``lifetime_in_years``. Enabling the ``attr-defined`` error
code does not help: the blindness is structural, not a matter of configuration.

Config field renames (``lifetime`` -> ``lifetime_in_years``, ``co2_footprint`` ->
``device_co2_footprint_in_kg``, ``cost`` -> ``investment_costs_in_euro``) have therefore
been able to update the dataclass *definitions* while leaving *readers* pointing at
names that no longer exist -- an ``AttributeError`` that only fires when that code path
is executed (typically in post-processing, long after the simulation ran).

What it checks
--------------
Every ``ConfigBase`` subclass is resolved to its full field set (own fields plus those
of all its ancestors). Any attribute access on a value known to be of that type is then
checked against that set. A value is known to be a config when it is:

  * a function/method parameter annotated with a config class
    (e.g. ``def get_cost_capex(config: HeatPumpHplibConfig, ...)``);
  * a local annotated or assigned from such a parameter;
  * ``self.<name>``, where ``__init__`` assigned a config-annotated parameter to it
    (the ``self.config = config`` / ``self.evconfig = config`` pattern).

To stay free of false positives the checker skips any config class with a base it cannot
resolve in the source tree, and it allows the dynamic ``JSONWizard`` serialisation API.

Gate: BLOCKING in CI (see ``.github/workflows/quality.yml``). ``obsolete/`` is excluded
by default, matching ``ignore-paths`` in ``pylintrc-critical-only``.

Examples
--------
    python scripts/check_config_attrs.py                    # hisim, system_setups, tests
    python scripts/check_config_attrs.py obsolete           # audit the obsolete tree too
    python scripts/check_config_attrs.py --root . hisim
"""
from __future__ import annotations

import argparse
import ast
import difflib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Attributes that legitimately come from the dynamic JSONWizard base.
JSONWIZARD_API = {
    "from_dict",
    "to_dict",
    "from_json",
    "to_json",
    "from_list",
    "list_to_json",
    "__dict__",
    "__class__",
    "__dataclass_fields__",
    "__annotations__",
    "__doc__",
}

# Bases that are known to be dynamic or trivial; inheriting one of them does not make a
# config class unresolvable.
DYNAMIC_BASES = {"JSONWizard", "ConfigBase", "object", "ABC", "Generic"}

# Annotation wrappers to unwrap when looking for the underlying class name.
TYPING_WRAPPERS = {"Optional", "List", "Sequence", "Iterable", "Union"}

SKIP_DIRS = {".git", ".mypy_cache", "__pycache__", "build", "dist", "node_modules", ".venv"}

DEFAULT_PATHS = ("hisim", "system_setups", "tests")


@dataclass
class ClassInfo:
    """A class definition as seen in the source tree."""

    name: str
    bases: List[str]
    members: Set[str]


@dataclass
class Finding:
    """A single attribute access that does not resolve to a config field."""

    path: Path
    lineno: int
    var: str
    attr: str
    config_class: str
    known_fields: Set[str] = field(repr=False, default_factory=set)

    def render(self, root: Path) -> str:
        """Format as a compiler-style 'file:line: message', with a spelling hint."""
        try:
            location = self.path.relative_to(root)
        except ValueError:
            location = self.path
        suggestions = difflib.get_close_matches(self.attr, self.known_fields, n=2, cutoff=0.6)
        hint = f" (did you mean: {', '.join(suggestions)}?)" if suggestions else ""
        return (
            f"{location}:{self.lineno}: {self.var}.{self.attr} -- "
            f"'{self.config_class}' has no field '{self.attr}'{hint}"
        )


def bare_name(node: Optional[ast.expr]) -> Optional[str]:
    """Reduce an expression to its bare class name (``cp.ConfigBase`` -> ``ConfigBase``)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return bare_name(node.value)
    return None


def annotation_name(node: Optional[ast.expr]) -> Optional[str]:
    """Reduce an annotation to a bare class name, unwrapping quotes and ``Optional[X]``."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return None
    if isinstance(node, ast.Subscript):
        outer = bare_name(node.value)
        if outer in TYPING_WRAPPERS:
            inner = node.slice
            if isinstance(inner, ast.Tuple) and inner.elts:
                return annotation_name(inner.elts[0])
            return annotation_name(inner)
        return outer
    return bare_name(node)


def class_members(node: ast.ClassDef) -> Set[str]:
    """Every name a class body defines: fields, class attrs, methods, and self-assignments."""
    members: Set[str] = set()
    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            members.add(stmt.target.id)
        elif isinstance(stmt, ast.Assign):
            members.update(t.id for t in stmt.targets if isinstance(t, ast.Name))
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            members.add(stmt.name)
            # Attributes materialised on self inside methods, e.g. __post_init__.
            for sub in ast.walk(stmt):
                if (
                    isinstance(sub, ast.Attribute)
                    and isinstance(sub.ctx, ast.Store)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == "self"
                ):
                    members.add(sub.attr)
    return members


def python_files(root: Path) -> List[Path]:
    """Every .py file under root, skipping caches and virtualenvs."""
    return [f for f in root.rglob("*.py") if not SKIP_DIRS.intersection(f.parts)]


def parse_tree(path: Path) -> Optional[ast.Module]:
    """Parse a file, returning None if it cannot be read or parsed."""
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None


def build_registry(trees: Dict[Path, ast.Module]) -> Dict[str, List[ClassInfo]]:
    """Index every class in the tree by name. Names can collide across modules, so a name
    maps to a list and lookups take the union of all definitions -- conservative, i.e. it
    can only suppress findings, never invent them."""
    registry: Dict[str, List[ClassInfo]] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = [b for b in (bare_name(b) for b in node.bases) if b]
                registry.setdefault(node.name, []).append(
                    ClassInfo(node.name, bases, class_members(node))
                )
    return registry


def ancestors(name: str, registry: Dict[str, List[ClassInfo]]) -> Tuple[Set[str], bool]:
    """All ancestor names of a class, plus whether any base could not be resolved."""
    seen: Set[str] = set()
    unresolved = False

    def walk(current: str) -> None:
        nonlocal unresolved
        if current in seen:
            return
        seen.add(current)
        for info in registry.get(current, []):
            for base in info.bases:
                if base in registry:
                    walk(base)
                else:
                    seen.add(base)
                    if base not in DYNAMIC_BASES:
                        unresolved = True

    walk(name)
    return seen, unresolved


def config_classes(registry: Dict[str, List[ClassInfo]]) -> Dict[str, Set[str]]:
    """Map each ConfigBase subclass to every field name it accepts."""
    result: Dict[str, Set[str]] = {}
    for name in registry:
        lineage, unresolved = ancestors(name, registry)
        # A class with a base we cannot see might inherit fields we cannot see, so skip it.
        if "ConfigBase" not in lineage or name == "ConfigBase" or unresolved:
            continue
        fields = set(JSONWIZARD_API)
        for ancestor in lineage:
            for info in registry.get(ancestor, []):
                fields |= info.members
        result[name] = fields
    return result


def config_params(fn: ast.AST, configs: Dict[str, Set[str]]) -> Dict[str, str]:
    """Parameters of a function whose annotation names a config class."""
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return {}
    args = fn.args
    all_args = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
    return {
        a.arg: annotation_name(a.annotation)  # type: ignore[misc]
        for a in all_args
        if annotation_name(a.annotation) in configs
    }


def self_config_attrs(cls: ast.ClassDef, configs: Dict[str, Set[str]]) -> Dict[str, str]:
    """Map ``self.<name>`` to a config class for every config parameter __init__ stores."""
    stored: Dict[str, str] = {}
    for fn in cls.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name != "__init__":
            continue
        params = config_params(fn, configs)
        for sub in ast.walk(fn):
            if not isinstance(sub, ast.Assign) or not isinstance(sub.value, ast.Name):
                continue
            config_class = params.get(sub.value.id)
            if not config_class:
                continue
            for target in sub.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    stored[target.attr] = config_class
    return stored


def check_function(
    fn: ast.AST,
    path: Path,
    configs: Dict[str, Set[str]],
    self_attrs: Dict[str, str],
    findings: List[Finding],
) -> None:
    """Check every config attribute access inside one function body."""
    local = config_params(fn, configs)
    for sub in ast.walk(fn):
        # A local annotated as a config:  my_config: HeatPumpHplibConfig = ...
        if isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
            annotated = annotation_name(sub.annotation)
            if annotated in configs:
                local[sub.target.id] = annotated
        # A local aliasing a known config:  cfg = config
        elif isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Name):
            source = local.get(sub.value.id)
            if source:
                local.update({t.id: source for t in sub.targets if isinstance(t, ast.Name)})

    for sub in ast.walk(fn):
        if not isinstance(sub, ast.Attribute):
            continue
        value = sub.value
        config_class = variable = None
        if isinstance(value, ast.Name) and value.id in local:
            config_class, variable = local[value.id], value.id
        elif (
            isinstance(value, ast.Attribute)
            and isinstance(value.value, ast.Name)
            and value.value.id == "self"
            and value.attr in self_attrs
        ):
            config_class, variable = self_attrs[value.attr], f"self.{value.attr}"
        if config_class and sub.attr not in configs[config_class]:
            findings.append(
                Finding(path, sub.lineno, str(variable), sub.attr, config_class, configs[config_class])
            )


def check_tree(
    path: Path, tree: ast.Module, configs: Dict[str, Set[str]], findings: List[Finding]
) -> None:
    """Check one module: its classes' methods, then its module-level functions."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            self_attrs = self_config_attrs(node, configs)
            for fn in node.body:
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    check_function(fn, path, configs, self_attrs, findings)
    for fn in tree.body:
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            check_function(fn, path, configs, {}, findings)


def main() -> int:
    """Scan the requested paths and report every bad config attribute access."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "paths",
        nargs="*",
        default=list(DEFAULT_PATHS),
        help=f"paths to report on, relative to --root (default: {', '.join(DEFAULT_PATHS)})",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="repository root; all of it is parsed to resolve base classes (default: repo root)",
    )
    args = parser.parse_args()

    root: Path = args.root.resolve()
    # The whole repo is parsed so that base classes always resolve, but only the
    # requested paths are reported on.
    trees = {f: t for f in python_files(root) if (t := parse_tree(f)) is not None}
    configs = config_classes(build_registry(trees))

    targets = [(root / p).resolve() for p in args.paths]
    findings: List[Finding] = []
    for path, tree in trees.items():
        if any(path.is_relative_to(t) for t in targets):
            check_tree(path, tree, configs, findings)

    findings.sort(key=lambda f: (str(f.path), f.lineno, f.attr))
    for finding in findings:
        print(finding.render(root))

    scanned = ", ".join(args.paths)
    if findings:
        affected = len({f.path for f in findings})
        print(
            f"\n{len(findings)} bad config attribute access(es) in {affected} file(s) "
            f"[{scanned}]. Each one raises AttributeError when its code path runs.",
            file=sys.stderr,
        )
        return 1
    print(f"OK: no bad config attribute accesses in {scanned} ({len(configs)} config classes checked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
