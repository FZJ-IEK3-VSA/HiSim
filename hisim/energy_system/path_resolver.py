"""Resolution of ``${var}`` resource references in energy-system files.

Energy-system files must be portable between machines and operating systems, so they never
contain absolute filesystem paths. Every path-valued config field is instead stored as a
reference of the form ``${variable}/relative/parts`` — always with forward slashes — where
the variable names a registered root directory such as the HiSim inputs tree. This module
provides the registry (:class:`PathVariable` for the well-known names, :class:`PathResolver`
for the lookup) and the two conversions the config save and load hooks use: ``symbolize``
turns a concrete absolute path into a reference, ``resolve`` turns a reference back into an
absolute path using the local machine's directory layout.

References are strictly data. An unknown variable raises rather than silently producing a
path that does not exist, and the structural validator refuses an absolute path outright, so
the only way a location reaches a file is through a variable somebody registered.

Nothing here parses or writes YAML, and nothing imports a component: the module is a leaf
that the configuration stage of the executor uses when it decodes a config block, and that
tests can build over temporary directories.
"""

# clean

from __future__ import annotations

import enum
import os
import re
from typing import Any, Dict, Iterable, Mapping, Optional

from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemFormatError


@enum.unique
class PathVariable(str, enum.Enum):
    """The well-known variable names an energy-system file may reference.

    These are the roots a default :class:`PathResolver` registers, so that a file written
    on one machine resolves on another without editing: the HiSim inputs tree, the UTSP result
    directory, the input cache directory and the process working directory. The enum exists so
    the names are spelled once and referenced symbolically from the component save and load hooks
    instead of being repeated as bare strings.

    Additional roots can be registered on a resolver instance at runtime (a project-specific
    data directory, for instance); this enum only fixes the ones every HiSim installation has.
    """

    INPUTS = "inputs"
    UTSP_RESULTS = "utsp_results"
    CACHE = "cache"
    CWD = "cwd"


class PathResolver:
    """Bidirectional translator between absolute filesystem paths and ``${var}`` references.

    An instance holds a registry mapping variable names to absolute root directories. The two
    public conversions are exact inverses for any path that lies below one of those roots:
    ``symbolize`` picks the longest matching root and emits ``${name}/relative/parts`` with
    forward slashes, ``resolve`` substitutes the root back in and rebuilds a path using the
    local OS separator. Paths that lie below no registered root pass through ``symbolize``
    unchanged, which keeps the conversion total — a component that stores an absolute path
    outside the HiSim tree still serializes, it just serializes non-portably.

    The default registry is built from :data:`hisim.utils.HISIMPATH` plus the current working
    directory, and is captured per instance at construction time so that no module-level mutable
    state exists and tests can build resolvers over temporary directories.
    """

    #: Matches a single ``${name}`` reference anywhere in a stored path string. Variable names
    #: use the same character class as component names, which keeps the two identifier
    #: syntaxes of the format consistent and unambiguous to a human reader.
    REFERENCE_PATTERN = re.compile(r"\$\{([A-Za-z0-9_]+)\}")

    #: Separator used inside stored references. Energy-system files are OS independent, so the
    #: relative part after the variable is always written with forward slashes regardless of
    #: which platform produced the file.
    STORAGE_SEPARATOR = "/"

    def __init__(self, roots: Optional[Mapping[str, str]] = None) -> None:
        """Builds a resolver over the given roots, or over the HiSim defaults when omitted.

        When ``roots`` is ``None`` the registry is populated with the four
        :class:`PathVariable` entries taken from :data:`hisim.utils.HISIMPATH` and
        :func:`os.getcwd`. When it is given, it fully replaces the defaults — this is what
        tests and embedding applications use to point the variables at temporary directories.
        Every root is normalized to an absolute path without a trailing separator so that the
        longest-root comparison in :meth:`symbolize` works on comparable strings.

        Args:
            roots: Optional mapping of variable name to root directory. Names are used
                verbatim in the ``${name}`` references, values may be relative and are
                converted to absolute paths.
        """
        source: Mapping[str, str] = self.get_default_roots() if roots is None else roots
        self.roots: Dict[str, str] = {name: self._normalize(path) for name, path in source.items()}

    @classmethod
    def default(cls) -> "PathResolver":
        """Builds a resolver over this installation's default roots.

        The save direction of the config protocol has no build context — ``to_config_dict``
        takes no arguments — so a config that symbolizes a path needs some way to reach a
        resolver. This classmethod is that way: it constructs a fresh resolver from
        :meth:`get_default_roots` on every call, which keeps the module free of any cached or
        otherwise mutable global state and lets a test that redirects the working directory or
        the HiSim inputs tree take effect immediately.

        The load direction never uses this method; it uses the resolver carried by the build
        context, so that an embedding application can point ``${utsp_results}`` somewhere else
        for one particular run.

        Returns:
            A new resolver over the default roots.
        """
        return cls()

    @classmethod
    def get_default_roots(cls) -> Dict[str, str]:
        """Returns the default variable-to-directory mapping of a HiSim installation.

        The mapping covers every member of :class:`PathVariable`: ``inputs`` and ``cache``
        come from the packaged inputs tree, ``utsp_results`` from the HiSim results directory,
        and ``cwd`` from the process working directory at the moment this method is called.
        It is recomputed on every call rather than cached in a module-level constant, so a
        test that changes the working directory gets a resolver that reflects that change.

        Returns:
            A fresh dict mapping the four well-known variable names to absolute directories.
        """
        from hisim.utils import HISIMPATH  # local: keeps importing this package free of pandas

        return {
            PathVariable.INPUTS.value: str(HISIMPATH["inputs"]),
            PathVariable.UTSP_RESULTS.value: str(HISIMPATH["utsp_results"]),
            PathVariable.CACHE.value: str(HISIMPATH["cache_dir"]),
            PathVariable.CWD.value: os.getcwd(),
        }

    def register(self, name: str, path: str) -> None:
        """Adds or replaces a variable in this resolver's registry.

        Use this to make a run-specific or project-specific directory addressable by
        reference, for instance a directory of measured load profiles that ships outside the
        HiSim package. Registering a name that already exists replaces its root, which is the
        intended way for an embedding application to redirect ``${utsp_results}`` at runtime.

        Args:
            name: Variable name as it appears inside ``${...}`` in the file.
            path: Root directory the variable stands for; converted to an absolute path.
        """
        self.roots[name] = self._normalize(path)

    def resolve(self, stored_path: str) -> str:
        """Expands every ``${var}`` reference in a stored path into a local absolute path.

        Substitution is purely textual, after which the result is rebuilt with the local
        separator so that a reference written on Linux resolves correctly on Windows and vice
        versa. A string containing no reference is returned unchanged apart from that
        separator normalization, which makes the method safe to call on any path-valued config
        field regardless of whether the author symbolized it.

        Args:
            stored_path: Path as stored in the file, e.g. ``${inputs}/weather/foo``.

        Returns:
            The resolved path using this machine's directories and this OS's separator.

        Raises:
            EnergySystemFormatError: ``EF-04`` if the string references an unregistered variable.
        """
        if self.REFERENCE_PATTERN.search(stored_path) is None:
            return self._to_local_separators(stored_path)

        def _substitute(match: "re.Match[str]") -> str:
            name = match.group(1)
            if name not in self.roots:
                raise EnergySystemFormatError(
                    EnergySystemErrorId.UNRESOLVABLE_PATH_VARIABLE,
                    stored_path,
                    f"the path reference '${{{name}}}' names no registered path variable.",
                    alternatives=sorted(self.roots),
                    alternatives_label="path variables",
                    offending_value=name,
                    remedy="Register the variable on the PathResolver before loading this file.",
                )
            return self.roots[name]

        expanded = self.REFERENCE_PATTERN.sub(_substitute, stored_path)
        return self._to_local_separators(expanded)

    def symbolize(self, absolute_path: str) -> str:
        """Turns an absolute path below a registered root into a portable ``${var}`` reference.

        The root with the longest directory prefix wins, so a file inside the input cache
        becomes ``${cache}/...`` rather than ``${inputs}/cache/...`` even though both roots
        match; ties between equally long roots are broken by name so the output is
        deterministic. A path that is already a reference, that is relative, or that lies below
        no registered root is returned unchanged — symbolization is best effort and never
        raises.

        Args:
            absolute_path: A concrete filesystem path as held by a live config object.

        Returns:
            ``${name}`` when the path is exactly a root, ``${name}/relative/parts`` (forward
            slashes) when it lies below one, and the input unchanged otherwise.
        """
        if self.REFERENCE_PATTERN.search(absolute_path) is not None:
            return absolute_path
        if not os.path.isabs(absolute_path):
            return absolute_path

        candidate = self._normalize(absolute_path)
        for name in sorted(self.roots, key=lambda key: (-len(self.roots[key]), key)):
            relative = self._relative_to_root(candidate, self.roots[name])
            if relative is None:
                continue
            if relative == "":
                return "${" + name + "}"
            return "${" + name + "}" + self.STORAGE_SEPARATOR + relative
        return absolute_path

    @classmethod
    def _normalize(cls, path: str) -> str:
        """Converts a path into the canonical absolute form used for registry comparisons.

        Normalization applies :func:`os.path.abspath`, which collapses ``..`` segments, makes
        relative paths absolute against the working directory and strips a trailing separator.
        Comparing only normalized paths is what makes the longest-root selection in
        :meth:`symbolize` reliable rather than dependent on how a caller happened to spell a
        directory.

        Args:
            path: Any path string.

        Returns:
            The normalized absolute form of the path.
        """
        return os.path.abspath(path)

    @classmethod
    def _relative_to_root(cls, candidate: str, root: str) -> Optional[str]:
        """Returns the forward-slash relative part of ``candidate`` below ``root``.

        The comparison is done component-wise rather than by string prefix so that a root
        ``/data/in`` does not appear to contain ``/data/inputs``. An empty string is returned
        when the candidate *is* the root, which the caller turns into a bare ``${name}``
        reference; ``None`` signals that the candidate lies outside the root, including the
        Windows case of two paths on different drives.

        Args:
            candidate: Normalized absolute path to test.
            root: Normalized absolute root directory.

        Returns:
            The relative path with forward slashes, ``""`` for an exact match, or ``None``.
        """
        try:
            relative = os.path.relpath(candidate, root)
        except ValueError:
            return None
        if relative == os.curdir:
            return ""
        if relative == os.pardir or relative.startswith(os.pardir + os.sep):
            return None
        return relative.replace(os.sep, cls.STORAGE_SEPARATOR)

    @classmethod
    def _to_local_separators(cls, path: str) -> str:
        """Rewrites a stored forward-slash path into the local operating system's form.

        Scenario files always use forward slashes, so on Windows the separators have to be
        translated before the path can be handed to the filesystem; on POSIX systems the
        translation is a no-op. :func:`os.path.normpath` additionally collapses the duplicate
        separator that appears when a reference expanding to a root is followed by ``/``.

        Args:
            path: Path string with forward slashes, already free of ``${var}`` references.

        Returns:
            The same location spelled with the local separator.
        """
        return os.path.normpath(path.replace(cls.STORAGE_SEPARATOR, os.sep))


class PathFieldCodec:
    """Shared helpers for configs whose fields hold filesystem locations.

    A config that stores a directory or a file path cannot serialize it verbatim: the absolute
    path of the HiSim inputs tree differs between machines and between operating systems, and an
    energy-system file must be portable. Such configs therefore symbolize their path fields in
    the save hook and resolve them in the load hook using the shared helpers gathered here.

    Keeping the loop over the path fields here rather than repeating it in every config override
    means all of them agree on the details that are easy to get wrong: ``None`` passes through
    untouched, a value that is already a reference is left alone by
    :meth:`PathResolver.symbolize`, and a field the config does not carry is skipped instead of
    being invented.
    """

    @classmethod
    def symbolize(
        cls,
        data: Dict[str, Any],
        field_names: Iterable[str],
        resolver: Optional[PathResolver] = None,
    ) -> Dict[str, Any]:
        """Replaces the named entries of a config dict with portable ``${var}`` references.

        Args:
            data: The config dict produced by the generic save hook; it is modified in place
                and also returned, so an override can write ``return PathFieldCodec.symbolize(
                super().to_config_dict(), self.PATH_FIELDS)``.
            field_names: The config fields that hold filesystem locations.
            resolver: Resolver to symbolize against; :meth:`PathResolver.default` when omitted,
                since the save hook has no build context to take one from.

        Returns:
            The same dict, with every present, non-empty path field turned into a reference.
        """
        active = resolver if resolver is not None else PathResolver.default()
        for field_name in field_names:
            value = data.get(field_name)
            if isinstance(value, str) and value:
                data[field_name] = active.symbolize(value)
        return data

    @classmethod
    def resolve(cls, config: Any, field_names: Iterable[str], resolver: PathResolver) -> Any:
        """Expands the named path fields of a freshly loaded config into local absolute paths.

        The config object is mutated rather than rebuilt, because a config class may carry
        derived state that a second construction would recompute differently, and because the
        load hook has already produced exactly the instance the caller wants.

        Args:
            config: The config instance returned by the generic load hook.
            field_names: The config fields that hold filesystem locations.
            resolver: The build context's resolver.

        Returns:
            The same config instance, with its path fields resolved.

        Raises:
            EnergySystemFormatError: ``EF-04`` if a field references an unregistered variable.
        """
        for field_name in field_names:
            value = getattr(config, field_name, None)
            if isinstance(value, str) and value:
                setattr(config, field_name, resolver.resolve(value))
        return config
