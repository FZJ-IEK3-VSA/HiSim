"""What goes into a cache key, and how it is derived from a producer and its inputs without anyone declaring it.

``roadmap/cache_service_spec.md`` §3 extends the key from ``sha256(config_json + simulation_key)`` to

    sha256( artifact_kind : code_fingerprint : third_party_fingerprint : dto_json )

so that a key describes the *inputs* of a calculation rather than the *owner* of its result. The
first part names the producer. The second changes whenever code that can influence the calculation
changes and stays put when only component plumbing does. The third pins the third-party packages
the calculation runs on, by version. The last is the canonical JSON of the calculation's own inputs,
the DTO of §3.1. Two calculations with the same key are the same calculation, on the same code, on
the same libraries, from the same inputs -- which is what lets a cache be shared between a laptop,
a cluster and a container without anyone worrying about whose entry they are reading.

The two fingerprints are **fully automatic**: nothing is declared, they are read off the producer
module's import statements. That is only sound because producer modules live under the layering rule
:class:`ProducerLayering` enforces -- no dynamic imports, which an AST walk cannot see, and no imports
of the component or simulator machinery, which would drag most of the package into the closure and turn
the fingerprint into a commit hash. The rule and the fingerprint are two uses of the same
:class:`ImportClosure`, so they cannot disagree about what a producer depends on.

The legacy key scheme is not here. ``hisim.utils.build_cache_key_string`` keeps producing it for
components that have not yet been given a producer, and the two coexist until the last extraction lands.
This module imports the standard library only, so that it stays importable from anywhere in HiSim
without pulling in the simulation.
"""

# clean

import ast
import dataclasses
import enum
import hashlib
import importlib.metadata
import importlib.util
import json
import pathlib
import re
import sys
from types import ModuleType
from typing import Any, ClassVar, Dict, FrozenSet, Iterator, List, Mapping, Optional, Set, Tuple

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


class CacheKeyError(ValueError):
    """Raised when a cache key cannot be built from what was given.

    Every message names the thing that was wrong -- the artifact kind, the DTO field, the module -- so
    the producer author can fix it without reading this module.
    """


class ProducerLayeringError(CacheKeyError):
    """Raised when a producer module imports something the producer layer bans.

    A producer is meant to be a pure calculation callable without a ``Simulator``. Importing the
    component machinery is how it stops being one, and it is also what would make its fingerprint
    swallow the package, so the two concerns share one refusal.
    """


class KeyMaterial:
    """The marker that says whether a DTO field is part of the key or merely travels with it.

    Spec §3.1: a producer that needs an upstream artifact gets its *key* as a plain string field and
    the loaded payload in a second field that is excluded from hashing. Both are ordinary dataclass
    fields; the only difference is this marker in the field's metadata, which :class:`CanonicalJson`
    reads. A payload field is declared exactly as the spec writes it::

        weather_artifact_key: str
        weather_frame: pd.DataFrame = dataclasses.field(default=None, metadata=KeyMaterial.PAYLOAD)

    The invariant that every payload field is paired with a key field identifying it is the producer
    author's to keep; nothing here can check that the pairing is right, only that a payload stays out
    of the key.
    """

    #: The metadata key under which a field says whether it is key material.
    FLAG: ClassVar[str] = "key_material"

    #: The metadata mapping that declares a payload field. Pass it as ``metadata=`` to
    #: :func:`dataclasses.field`; unmarked fields are key material by default.
    PAYLOAD: ClassVar[Mapping[str, bool]] = {"key_material": False}

    @classmethod
    def is_key_material(cls, field: "dataclasses.Field[Any]") -> bool:
        """Says whether a field participates in the key. Unmarked fields do, by default.

        Args:
            field: the dataclass field to inspect.

        Returns:
            bool: False only for fields declared with :attr:`PAYLOAD` in their metadata.
        """
        return bool(field.metadata.get(cls.FLAG, True))


class CanonicalJson:
    """Turns a DTO into the one JSON string that stands for it, so equal inputs give equal keys.

    The rules are the spec's: sorted keys, enums by value, and payload fields left out. Two things are
    added because they were needed to make real DTOs serialisable at all: nested dataclasses are
    rendered recursively under the same rules, and paths are rendered as strings. Anything else that
    ``json`` cannot render is refused by name, because silently stringifying an unknown object would
    let two different inputs share a key.
    """

    #: The ``json.dumps`` separators, spelled out so that the canonical form is fixed rather than
    #: whatever the library's default happens to be.
    SEPARATORS: ClassVar[Tuple[str, str]] = (",", ":")

    @classmethod
    def dumps(cls, dto: Any) -> str:
        """Renders a DTO canonically.

        Args:
            dto: a dataclass instance -- the single parameter of a producer function.

        Returns:
            str: the canonical JSON.

        Raises:
            CacheKeyError: if the DTO is not a dataclass instance, or a field holds a value that has
                no canonical rendering.
        """
        if not dataclasses.is_dataclass(dto) or isinstance(dto, type):
            raise CacheKeyError(
                f"A calculation DTO must be a dataclass instance; got {type(dto).__name__}. "
                "Spec §3.1: every producer takes exactly one frozen dataclass."
            )
        return json.dumps(cls._render_dataclass(dto, ""), sort_keys=True, separators=cls.SEPARATORS)

    @classmethod
    def _render_dataclass(cls, dto: Any, path: str) -> Dict[str, Any]:
        """Renders one dataclass instance, skipping payload fields.

        Args:
            dto: the instance.
            path: the dotted path to it, for error messages.

        Returns:
            Dict[str, Any]: the rendered mapping.
        """
        rendered: Dict[str, Any] = {}
        for field in dataclasses.fields(dto):
            if not KeyMaterial.is_key_material(field):
                continue
            rendered[field.name] = cls._render(getattr(dto, field.name), f"{path}.{field.name}".lstrip("."))
        return rendered

    @classmethod
    def _render(cls, value: Any, path: str) -> Any:
        """Renders one value under the canonical rules.

        Args:
            value: the value.
            path: the dotted path to it, for error messages.

        Returns:
            Any: something ``json.dumps`` can render deterministically.

        Raises:
            CacheKeyError: for a value with no canonical rendering.
        """
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, enum.Enum):
            return value.value
        if isinstance(value, pathlib.PurePath):
            return str(value)
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return cls._render_dataclass(value, path)
        if isinstance(value, (list, tuple)):
            return [cls._render(item, f"{path}[{index}]") for index, item in enumerate(value)]
        if isinstance(value, Mapping):
            return {str(key): cls._render(item, f"{path}[{key!r}]") for key, item in value.items()}
        raise CacheKeyError(
            f"DTO field {path!r} holds a {type(value).__name__}, which has no canonical JSON form. "
            "Spec §3.1: DTO fields are primitives, enums, lists, mappings, nested DTOs and artifact keys; "
            "a payload that only travels with the key is declared with metadata=KeyMaterial.PAYLOAD."
        )


@dataclasses.dataclass(frozen=True)
class ImportClosure:
    """Everything a module imports, transitively within one package, found by reading source, not running it.

    This is the object both fingerprints and the layering rule are computed from. It is built by
    parsing each module's ``import`` statements and following the ones that stay inside the root
    package; imports of anything else are recorded by their top-level name and not followed, because
    a third-party package is identified by its version, not its source.

    Modules are *located* with :func:`importlib.util.find_spec`, which imports parent packages to find
    a child but never executes the child itself. That is as close to a purely static walk as Python
    allows, and it is why the root package's ``__init__`` must stay cheap.
    """

    #: The package whose modules are followed, e.g. ``"hisim"``.
    root_package: str

    #: Every module of the root package in the closure, the starting module included, sorted.
    package_modules: Tuple[str, ...]

    #: The file each package module was read from, keyed by module name.
    module_files: Mapping[str, str]

    #: Top-level names of every import that leaves the root package, standard library excluded.
    third_party_top_levels: Tuple[str, ...]

    #: Descriptions of every dynamic-import call site found, empty for a well-behaved producer.
    dynamic_import_sites: Tuple[str, ...]

    class Calls:
        """The dynamic-import spellings the walk looks for, which a static analysis cannot see through."""

        NAMES: ClassVar[FrozenSet[str]] = frozenset({"__import__", "import_module"})

    @classmethod
    def of(cls, module: ModuleType, root_package: Optional[str] = None) -> "ImportClosure":
        """Computes the closure of a module.

        Args:
            module: the starting module; it must have been loaded from a file.
            root_package: the package whose modules are followed. Defaults to the top-level package of
                ``module`` itself, which is ``hisim`` for every real producer; tests pass their own.

        Returns:
            ImportClosure: the closure.

        Raises:
            CacheKeyError: if the module has no source file to read.
        """
        name = module.__name__
        root = root_package if root_package is not None else name.split(".")[0]
        module_files: Dict[str, str] = {}
        third_party: Set[str] = set()
        dynamic: List[str] = []
        pending = [name]
        while pending:
            current = pending.pop()
            if current in module_files:
                continue
            source_path = cls._source_file(current)
            module_files[current] = source_path
            tree = ast.parse(pathlib.Path(source_path).read_text(encoding="utf-8"), filename=source_path)
            for imported in cls._imported_names(tree, current):
                if imported == root or imported.startswith(root + "."):
                    pending.append(cls._module_or_owner(imported))
                else:
                    top_level = imported.split(".")[0]
                    if top_level not in sys.stdlib_module_names:
                        third_party.add(top_level)
            dynamic.extend(cls._dynamic_import_sites(tree, current))
        return cls(
            root_package=root,
            package_modules=tuple(sorted(module_files)),
            module_files=dict(sorted(module_files.items())),
            third_party_top_levels=tuple(sorted(third_party)),
            dynamic_import_sites=tuple(dynamic),
        )

    @staticmethod
    def _source_file(module_name: str) -> str:
        """Locates a module's source file without executing the module.

        Args:
            module_name: the fully qualified name.

        Returns:
            str: the path of its ``.py`` file.

        Raises:
            CacheKeyError: if the module cannot be found or has no source file.
        """
        try:
            spec = importlib.util.find_spec(module_name)
        except (ImportError, ValueError) as error:
            raise CacheKeyError(f"Cannot locate module {module_name!r} to fingerprint it: {error}") from error
        if spec is None or not spec.origin or not spec.origin.endswith(".py"):
            raise CacheKeyError(
                f"Module {module_name!r} has no Python source file to fingerprint; a producer and everything "
                "it imports from its own package must be ordinary .py modules."
            )
        return spec.origin

    @classmethod
    def _module_or_owner(cls, imported: str) -> str:
        """Resolves ``from pkg import name`` where ``name`` may be a submodule or an attribute.

        Args:
            imported: the fully qualified name as the import statement spells it.

        Returns:
            str: ``imported`` if it is a module, otherwise the package it is an attribute of.
        """
        try:
            if importlib.util.find_spec(imported) is not None:
                return imported
        except (ImportError, ValueError):
            pass
        return imported.rsplit(".", 1)[0] if "." in imported else imported

    @staticmethod
    def _imported_names(tree: ast.AST, module_name: str) -> Iterator[str]:
        """Yields the fully qualified name of every import statement in a module.

        Relative imports are resolved against the importing module's package, so a producer may use
        ``from . import helpers`` and still be followed.

        Args:
            tree: the parsed module.
            module_name: the importing module, for resolving relative imports.

        Yields:
            str: one fully qualified name per imported module or attribute.
        """
        package_parts = module_name.split(".")[:-1]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    yield alias.name
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base_parts = package_parts[: len(package_parts) - (node.level - 1)] if node.level > 1 else package_parts
                    base = ".".join(base_parts)
                    base = f"{base}.{node.module}" if node.module else base
                else:
                    base = node.module or ""
                for alias in node.names:
                    yield f"{base}.{alias.name}" if base else alias.name

    @classmethod
    def _dynamic_import_sites(cls, tree: ast.AST, module_name: str) -> Iterator[str]:
        """Yields a description of every call that imports by name at run time.

        Args:
            tree: the parsed module.
            module_name: the module, for the description.

        Yields:
            str: ``module:line`` for each ``__import__`` or ``import_module`` call.
        """
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            called = callee.id if isinstance(callee, ast.Name) else callee.attr if isinstance(callee, ast.Attribute) else ""
            if called in cls.Calls.NAMES:
                yield f"{module_name}:{node.lineno}"


class ProducerLayering:
    """The rule a producer module and its closure must obey, and the check that enforces it.

    A producer is a pure calculation: callable without a ``Simulator``, importing numpy, pandas,
    pvlib and config dataclasses, never the component base class, the simulator or a repository. The
    rule has a cache cost as well as a design reason -- every module in the closure is fingerprinted, so
    importing a frequently edited module invalidates all of a producer's artifacts on every edit to it.
    Dynamic imports are banned outright because the closure walk cannot see through them, and a
    dependency it cannot see is a stale entry it cannot prevent.
    """

    #: Module prefixes a producer's closure may not contain.
    FORBIDDEN_PREFIXES: ClassVar[Tuple[str, ...]] = (
        "hisim.component",
        "hisim.dynamic_component",
        "hisim.simulator",
        "hisim.sim_repository",
        "hisim.sim_repository_singleton",
        "hisim.postprocessing",
    )

    @classmethod
    def violations(cls, closure: ImportClosure) -> Tuple[str, ...]:
        """Lists every way a closure breaks the rule, empty for a compliant producer.

        Args:
            closure: the producer's import closure.

        Returns:
            Tuple[str, ...]: one sentence per violation.
        """
        found: List[str] = []
        for module_name in closure.package_modules:
            if any(module_name == prefix or module_name.startswith(prefix + ".") for prefix in cls.FORBIDDEN_PREFIXES):
                found.append(f"imports {module_name}, which is component or simulator machinery")
        for site in closure.dynamic_import_sites:
            found.append(f"imports dynamically at {site}, which the closure walk cannot follow")
        return tuple(found)

    @classmethod
    def check(cls, closure: ImportClosure) -> None:
        """Raises if the closure breaks the rule.

        Args:
            closure: the producer's import closure.

        Raises:
            ProducerLayeringError: naming every violation at once, so they are fixed in one pass.
        """
        violations = cls.violations(closure)
        if violations:
            listed = "\n  - ".join(violations)
            raise ProducerLayeringError(
                f"The producer {closure.package_modules[0] if closure.package_modules else '?'} breaks the producer "
                f"layering rule:\n  - {listed}\nA producer is a pure calculation; pass plain values through its DTO instead."
            )


class Fingerprints:
    """The two automatic fingerprints of spec §3, computed from an :class:`ImportClosure`.

    Both are deterministic functions of the closure alone. Neither includes the repository commit,
    deliberately: a commit changes on every edit anywhere, and keying on it would make every cache
    entry disposable on every push. The commit travels as metadata beside the entry instead.
    """

    #: What the standard library contributes to the third-party fingerprint: the interpreter's
    #: major.minor, because that is what decides its behaviour, and nothing finer.
    PYTHON_LABEL: ClassVar[str] = "python"

    @staticmethod
    def code(closure: ImportClosure) -> str:
        """Hashes the source of every package module in the closure.

        Args:
            closure: the producer's import closure.

        Returns:
            str: a sha256 hex digest over the sorted (module name, source) pairs.
        """
        digest = hashlib.sha256()
        for module_name in closure.package_modules:
            digest.update(module_name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(pathlib.Path(closure.module_files[module_name]).read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    @classmethod
    def third_party(cls, closure: ImportClosure) -> str:
        """Lists ``name==version`` for every third-party distribution the closure imports.

        Deliberately not a hash of the whole installed environment: the cluster, the CI container and
        the RenoVisor image never have identical dependency sets, and hashing all of them would reduce
        cross-environment cache hits to zero. Only what the calculation imports counts.

        Args:
            closure: the producer's import closure.

        Returns:
            str: the sorted, semicolon-separated pins, led by the interpreter version.
        """
        distributions = importlib.metadata.packages_distributions()
        pins: Set[str] = {f"{cls.PYTHON_LABEL}=={sys.version_info.major}.{sys.version_info.minor}"}
        for top_level in closure.third_party_top_levels:
            for distribution_name in distributions.get(top_level, [top_level]):
                try:
                    pins.add(f"{distribution_name}=={importlib.metadata.version(distribution_name)}")
                except importlib.metadata.PackageNotFoundError:
                    pins.add(f"{distribution_name}==unknown")
        return ";".join(sorted(pins))


@dataclasses.dataclass(frozen=True)
class CacheKey:
    """A cache key under the spec §3 scheme: four parts, one digest.

    The parts are kept rather than only the digest because the metadata file beside each entry
    records the raw material, and a person reading it should be able to see which producer, which
    code, which libraries and which inputs an entry came from.
    """

    #: A short name for the producer, e.g. ``"pv_series"``. Also the filename prefix.
    artifact_kind: str

    #: :meth:`Fingerprints.code` of the producer's closure.
    code_fingerprint: str

    #: :meth:`Fingerprints.third_party` of the producer's closure.
    third_party_fingerprint: str

    #: :meth:`CanonicalJson.dumps` of the calculation's DTO.
    dto_json: str

    #: What an artifact kind may look like: a filename and URL segment with no surprises.
    KIND_PATTERN: ClassVar["re.Pattern[str]"] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

    #: The separator between the four parts of the material.
    SEPARATOR: ClassVar[str] = ":"

    def __post_init__(self) -> None:
        """Refuses an artifact kind that could not serve as a path segment.

        Raises:
            CacheKeyError: if the kind is empty or contains characters outside the pattern.
        """
        if not self.KIND_PATTERN.match(self.artifact_kind):
            raise CacheKeyError(
                f"Artifact kind {self.artifact_kind!r} is not usable as a filename and URL segment; use letters, "
                "digits, underscores and hyphens, starting with a letter or digit."
            )

    @classmethod
    def for_producer(cls, artifact_kind: str, producer_module: ModuleType, dto: Any) -> "CacheKey":
        """Builds the key for one calculation, checking the producer's layering on the way.

        Args:
            artifact_kind: the producer's short name.
            producer_module: the module holding the producer function and its DTO class.
            dto: the calculation's inputs.

        Returns:
            CacheKey: the key.

        Raises:
            ProducerLayeringError: if the producer module imports what a producer may not.
            CacheKeyError: if the DTO or the kind cannot be rendered.
        """
        closure = ImportClosure.of(producer_module)
        ProducerLayering.check(closure)
        return cls(
            artifact_kind=artifact_kind,
            code_fingerprint=Fingerprints.code(closure),
            third_party_fingerprint=Fingerprints.third_party(closure),
            dto_json=CanonicalJson.dumps(dto),
        )

    @property
    def material(self) -> str:
        """The string the digest is taken from, and what the entry's metadata file records verbatim.

        Returns:
            str: the four parts joined by :attr:`SEPARATOR`.
        """
        return self.SEPARATOR.join(
            (self.artifact_kind, self.code_fingerprint, self.third_party_fingerprint, self.dto_json)
        )

    @property
    def digest(self) -> str:
        """The sha256 hex digest of :attr:`material`; the ``{sha}`` of the remote key and the local filename.

        Returns:
            str: the digest.
        """
        return hashlib.sha256(self.material.encode("utf-8")).hexdigest()
