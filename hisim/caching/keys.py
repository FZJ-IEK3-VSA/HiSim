"""Cache keys under the cache-service scheme: what goes into one and how it is derived.

``roadmap/cache_service_spec.md`` §3 defines the key as

    sha256( artifact_kind : code_fingerprint : third_party_fingerprint : dto_json )

``artifact_kind`` names the producer (for example ``pv_series``). ``code_fingerprint`` is a hash of the
producer module's source and of every HiSim module it imports, so a change to code that can affect the
result changes the key. ``third_party_fingerprint`` pins the third-party packages the producer imports,
by version. ``dto_json`` is the canonical JSON of the calculation's inputs (the DTO). Two calculations with
the same key ran the same code on the same libraries with the same inputs, which is what allows one
cache to be shared between a laptop, a cluster and a container.

The fingerprints need no declarations: they are read from the module's ``import`` statements
(:class:`ImportClosure`). That works only if producer modules follow :class:`ProducerLayering` -- no
dynamic imports, and no imports of the component or simulator machinery, which would pull most of the
package into the closure.

No component uses this scheme yet. ``hisim.utils.build_cache_key_string`` produces the legacy key until
each producer is extracted. This module imports the standard library only.
"""

# clean

import ast
import dataclasses
import enum
import hashlib
import importlib.metadata
import importlib.util
import json
import math
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
    """Raised when a cache key cannot be built. The message names the offending kind, field or module."""


class ProducerLayeringError(CacheKeyError):
    """Raised when a producer module imports something the producer layer forbids.

    See :class:`ProducerLayering` for the rule and the reason.
    """


class KeyMaterial:
    """Marks a DTO field as a payload that travels with the key but is not part of it.

    A producer that needs an upstream artifact takes its key as a string field and the loaded data as a
    second field excluded from hashing (spec §3.1). Declare the second field like this::

        weather_artifact_key: str
        weather_frame: pd.DataFrame = dataclasses.field(default=None, metadata=KeyMaterial.PAYLOAD)

    :class:`CanonicalJson` skips fields marked this way. Pairing every payload field with a key field is
    the author's responsibility.
    """

    #: The metadata key under which a field says whether it is key material.
    FLAG: ClassVar[str] = "key_material"

    #: The metadata mapping that declares a payload field. Pass it as ``metadata=`` to
    #: :func:`dataclasses.field`; unmarked fields are key material by default.
    PAYLOAD: ClassVar[Mapping[str, bool]] = {"key_material": False}

    @classmethod
    def is_key_material(cls, field: "dataclasses.Field[Any]") -> bool:
        """Return True unless the field is marked as payload.

        Args:
            field: the dataclass field.

        Returns:
            bool: False only for fields declared with :attr:`PAYLOAD`.
        """
        return bool(field.metadata.get(cls.FLAG, True))


class CanonicalJson:
    """Renders a DTO as the one JSON string that represents it, so equal inputs give equal keys.

    Rules: keys sorted, enums by value, payload fields omitted, nested dataclasses rendered recursively,
    paths as strings. Any other value is refused with the field's name, because stringifying an unknown
    object could give two different inputs the same key. For the same reason mapping keys must be
    strings (coercing them would collapse ``1`` and ``"1"`` into one key) and floats must be finite
    (every NaN would render as the same ``NaN``, which is not JSON either).
    """

    #: The ``json.dumps`` separators, spelled out so that the canonical form is fixed rather than
    #: whatever the library's default happens to be.
    SEPARATORS: ClassVar[Tuple[str, str]] = (",", ":")

    @classmethod
    def dumps(cls, dto: Any) -> str:
        """Render a DTO canonically.

        Args:
            dto: a dataclass instance, the single argument of a producer function.

        Returns:
            str: the canonical JSON.

        Raises:
            CacheKeyError: if ``dto`` is not a dataclass instance, or a field holds a value with no canonical form.
        """
        if not dataclasses.is_dataclass(dto) or isinstance(dto, type):
            raise CacheKeyError(
                f"A calculation DTO must be a dataclass instance; got {type(dto).__name__}. "
                "Spec §3.1: every producer takes exactly one frozen dataclass."
            )
        return json.dumps(cls._render_dataclass(dto, ""), sort_keys=True, separators=cls.SEPARATORS)

    @classmethod
    def _render_dataclass(cls, dto: Any, path: str) -> Dict[str, Any]:
        """Render one dataclass instance, skipping payload fields.

        Args:
            dto: the instance.
            path: dotted path to it, for error messages.

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
        """Render one value under the canonical rules.

        Args:
            value: the value.
            path: dotted path to it, for error messages.

        Returns:
            Any: a value ``json.dumps`` renders deterministically.

        Raises:
            CacheKeyError: for a value with no canonical form.
        """
        if isinstance(value, float) and not math.isfinite(value):
            raise CacheKeyError(
                f"DTO field {path!r} holds the non-finite float {value!r}, which has no canonical JSON form: "
                "every NaN would render identically and give different inputs the same key."
            )
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
            for key in value:
                if not isinstance(key, str):
                    raise CacheKeyError(
                        f"DTO field {path!r} holds a mapping with the non-string key {key!r}; coercing it to "
                        "a string would let two different inputs (say the int 1 and the string '1') share "
                        "one cache key, so mapping keys must be strings."
                    )
            return {key: cls._render(item, f"{path}[{key!r}]") for key, item in value.items()}
        raise CacheKeyError(
            f"DTO field {path!r} holds a {type(value).__name__}, which has no canonical JSON form. "
            "Spec §3.1: DTO fields are primitives, enums, lists, mappings, nested DTOs and artifact keys; "
            "a payload that only travels with the key is declared with metadata=KeyMaterial.PAYLOAD."
        )


@dataclasses.dataclass(frozen=True)
class ImportClosure:
    """Everything a module imports, followed transitively within one package, found by reading source.

    Both fingerprints and the layering check are computed from this. Modules of the root package are
    followed and their files recorded; imports of anything else are recorded by top-level name only,
    since a third-party package is identified by its version, not its source. Modules are located with
    :func:`importlib.util.find_spec`, which imports parent packages but not the module itself.
    """

    #: The package whose modules are followed, e.g. ``"hisim"``.
    root_package: str

    #: The module the walk started from -- the producer itself, named in error messages.
    entry_module: str

    #: Every module of the root package in the closure, the starting module included, sorted.
    package_modules: Tuple[str, ...]

    #: The file each package module was read from, keyed by module name.
    module_files: Mapping[str, str]

    #: Top-level names of every import that leaves the root package, standard library excluded.
    third_party_top_levels: Tuple[str, ...]

    #: Descriptions of every dynamic-import call site found, empty for a well-behaved producer.
    dynamic_import_sites: Tuple[str, ...]

    class Calls:
        """Function names that import a module at run time. A static walk cannot see through them."""

        NAMES: ClassVar[FrozenSet[str]] = frozenset({"__import__", "import_module"})

    @classmethod
    def of(cls, module: ModuleType, root_package: Optional[str] = None) -> "ImportClosure":
        """Compute the closure of a module.

        Args:
            module: the starting module; it must have been loaded from a ``.py`` file.
            root_package: the package whose modules are followed. Defaults to the module's top-level package
                (``hisim`` for every real producer); tests pass their own.

        Returns:
            ImportClosure: the closure.

        Raises:
            CacheKeyError: if a module in the closure has no source file.
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
            is_package = pathlib.Path(source_path).name == "__init__.py"
            for imported in cls._imported_names(tree, current, is_package):
                if imported == root or imported.startswith(root + "."):
                    pending.append(cls._module_or_owner(imported))
                else:
                    top_level = imported.split(".")[0]
                    if top_level not in sys.stdlib_module_names:
                        third_party.add(top_level)
            dynamic.extend(cls._dynamic_import_sites(tree, current))
        return cls(
            root_package=root,
            entry_module=name,
            package_modules=tuple(sorted(module_files)),
            module_files=dict(sorted(module_files.items())),
            third_party_top_levels=tuple(sorted(third_party)),
            dynamic_import_sites=tuple(dynamic),
        )

    @staticmethod
    def _source_file(module_name: str) -> str:
        """Locate a module's source file without executing the module.

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
        """Resolve ``from pkg import name``, where ``name`` may be a submodule or an attribute.

        Args:
            imported: the fully qualified name as written in the import.

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
    def _imported_names(tree: ast.AST, module_name: str, is_package: bool) -> Iterator[str]:
        """Yield the fully qualified name of every import in a module, relative imports resolved.

        Args:
            tree: the parsed module.
            module_name: the importing module, used to resolve relative imports.
            is_package: whether the module is a package's ``__init__``. A relative import in a package
                resolves against the package itself; in a plain module it resolves against the parent
                package. Without this distinction, ``from . import sub`` in the root ``__init__`` would
                lose the root prefix and misfile ``sub`` as a third-party name.

        Yields:
            str: one name per imported module or attribute.
        """
        package_parts = module_name.split(".") if is_package else module_name.split(".")[:-1]
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
        """Yield ``module:line`` for every ``__import__`` or ``import_module`` call.

        A bare name matches either spelling (``import_module`` may arrive via ``from importlib
        import import_module``). The attribute form counts only on the ``importlib`` module itself,
        so an unrelated object that happens to have an ``import_module`` method is not a violation.

        Args:
            tree: the parsed module.
            module_name: the module name, for the description.

        Yields:
            str: one entry per call site.
        """
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            if isinstance(callee, ast.Name):
                called = callee.id
            elif (
                isinstance(callee, ast.Attribute)
                and isinstance(callee.value, ast.Name)
                and callee.value.id == "importlib"
            ):
                called = callee.attr
            else:
                called = ""
            if called in cls.Calls.NAMES:
                yield f"{module_name}:{node.lineno}"


class ProducerLayering:
    """The import rule for producer modules, and the check that enforces it.

    A producer is a pure calculation. It may import numpy, pandas, pvlib and config dataclasses; it may
    not import the component base class, the simulator or a repository, and it may not import dynamically.
    Two reasons: a producer must stay callable without a ``Simulator``, and every module in its closure is
    fingerprinted, so importing a frequently edited module would invalidate all of its cache entries on
    every edit. Dynamic imports are forbidden because the closure walk cannot see them.
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
        """Return one sentence per rule violation; empty for a compliant producer.

        Args:
            closure: the producer's import closure.

        Returns:
            Tuple[str, ...]: the violations.
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
        """Raise if the closure violates the rule.

        Args:
            closure: the producer's import closure.

        Raises:
            ProducerLayeringError: listing every violation at once.
        """
        violations = cls.violations(closure)
        if violations:
            listed = "\n  - ".join(violations)
            raise ProducerLayeringError(
                f"The producer {closure.entry_module} breaks the producer "
                f"layering rule:\n  - {listed}\nA producer is a pure calculation; pass plain values through its DTO instead."
            )


class Fingerprints:
    """The two automatic fingerprints of spec §3, computed from an :class:`ImportClosure`.

    Neither includes the repository commit: a commit changes on every edit anywhere and would make every
    entry disposable on every push. The commit is stored as metadata beside the entry instead.
    """

    #: What the standard library contributes to the third-party fingerprint: the interpreter's
    #: major.minor, because that is what decides its behaviour, and nothing finer.
    PYTHON_LABEL: ClassVar[str] = "python"

    @staticmethod
    def code(closure: ImportClosure) -> str:
        """Hash the source of every package module in the closure.

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
        """List ``name==version`` for every third-party distribution in the closure, plus the Python version.

        Only what the producer imports is pinned, not the whole environment: the cluster, the CI container and
        the RenoVisor image never have identical dependency sets, and pinning everything would prevent any
        cross-environment cache hit.

        Args:
            closure: the producer's import closure.

        Returns:
            str: the sorted pins, separated by semicolons.

        Raises:
            CacheKeyError: if a dependency's version cannot be resolved. A pin like ``name==unknown``
                would let two environments running different code for that package share cache
                entries, which is the failure the fingerprint exists to prevent.
        """
        distributions = importlib.metadata.packages_distributions()
        pins: Set[str] = {f"{cls.PYTHON_LABEL}=={sys.version_info.major}.{sys.version_info.minor}"}
        for top_level in closure.third_party_top_levels:
            for distribution_name in distributions.get(top_level, [top_level]):
                try:
                    pins.add(f"{distribution_name}=={importlib.metadata.version(distribution_name)}")
                except importlib.metadata.PackageNotFoundError as error:
                    raise CacheKeyError(
                        f"The producer {closure.entry_module} imports {top_level!r}, but no version can be "
                        f"resolved for the distribution {distribution_name!r}, so the third-party fingerprint "
                        "cannot vouch for the code that runs. Install the package with metadata, or drop the "
                        "import from the producer."
                    ) from error
        return ";".join(sorted(pins))


@dataclasses.dataclass(frozen=True)
class CacheKey:
    """A cache key under spec §3: four parts and their digest.

    The parts are kept, not only the digest, because the ``.meta`` file beside an entry records the raw
    material and a reader should be able to see which producer, code, libraries and inputs it came from.
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
        """Refuse an artifact kind that cannot serve as a filename and URL segment.

        Raises:
            CacheKeyError: if the kind is empty or contains other characters than letters, digits, ``_`` and ``-``.
        """
        if not self.KIND_PATTERN.match(self.artifact_kind):
            raise CacheKeyError(
                f"Artifact kind {self.artifact_kind!r} is not usable as a filename and URL segment; use letters, "
                "digits, underscores and hyphens, starting with a letter or digit."
            )

    @classmethod
    def for_producer(cls, artifact_kind: str, producer_module: ModuleType, dto: Any) -> "CacheKey":
        """Build the key for one calculation, checking the producer's layering first.

        Args:
            artifact_kind: the producer's short name, for example ``pv_series``.
            producer_module: the module holding the producer function and its DTO class.
            dto: the calculation's inputs.

        Returns:
            CacheKey: the key.

        Raises:
            ProducerLayeringError: if the producer module violates the layering rule.
            CacheKeyError: if the kind or the DTO cannot be rendered.
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
        """The string the digest is computed from; also what the entry's ``.meta`` file records.

        Returns:
            str: the four parts joined by :attr:`SEPARATOR`.
        """
        return self.SEPARATOR.join(
            (self.artifact_kind, self.code_fingerprint, self.third_party_fingerprint, self.dto_json)
        )

    @property
    def digest(self) -> str:
        """The sha256 hex digest of :attr:`material`: the ``{sha}`` in the remote key and the local filename.

        The expression is the same as :meth:`hisim.caching.local.CacheEntryMetadata.hash_of` and must
        stay the same, or a new-scheme key would not match the filename the local tier derives for it.
        It is spelled out here rather than imported because this module deliberately imports the
        standard library only.

        Returns:
            str: the digest.
        """
        return hashlib.sha256(self.material.encode("utf-8")).hexdigest()
