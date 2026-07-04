"""Configuration for the HiSim MPI HPC harness.

Values come from a JSON config file (``--config``) and/or command-line overrides.
Command-line values take precedence over the config file, which takes precedence
over the defaults below.
"""

import json
import warnings
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Optional, Tuple, Union

# Renamed public fields whose old JSON keys / override kwargs are still
# accepted for backward compatibility (issue #614). Mapping: old name -> new name.
_DEPRECATED_FIELD_ALIASES = {"db": "db_path", "sim_params": "sim_params_path"}


def _normalize_path(
    path: str,
    *,
    cwd: Optional[Path] = None,
    home: Optional[Path] = None,
) -> str:
    """Normalise a path to an absolute, user-expanded, resolved string.

    This is the pure seam behind :meth:`HarnessConfig.finalize`: it makes
    ``Path(...).expanduser().resolve()`` testable without touching
    process-global state (the current working directory and the ``HOME``
    environment variable). With both ``cwd`` and ``home`` left as ``None`` it
    reproduces ``str(Path(path).expanduser().resolve())`` exactly, so
    production behaviour is unchanged. Supplying explicit ``cwd`` and/or
    ``home`` decouples the result from that global state: a leading ``~`` (or
    ``~/...``) is expanded against ``home`` and a relative ``path`` is joined
    onto ``cwd`` before resolving, which lets tests assert on the normalised
    string deterministically without ``monkeypatch.chdir``/``monkeypatch.setenv``.

    Args:
        path: The path string to normalise.
        cwd: Base directory used to resolve relative ``path`` values. Defaults
            to :meth:`Path.cwd` when needed.
        home: Home directory substituted for a leading ``~``. Defaults to
            :meth:`Path.home` when needed.

    Returns:
        The normalised absolute path as a string.

    Note:
        The explicit-argument form expands only a bare leading ``~`` (and
        ``~/...``) against ``home``; the POSIX ``~user`` user-lookup form is
        honoured solely on the default fast path (which delegates to
        :meth:`Path.expanduser`). The harness never configures ``~user``
        paths, so this keeps the seam simple and dependency-free.
    """
    # Fast path: no injection requested, reproduce today's behaviour verbatim.
    if cwd is None and home is None:
        return str(Path(path).expanduser().resolve())
    if cwd is None:
        cwd = Path.cwd()
    if home is None:
        home = Path.home()
    parsed = Path(path)
    parts = parsed.parts
    if parts and parts[0] == "~":
        parsed = home if len(parts) == 1 else home.joinpath(*parts[1:])
    if not parsed.is_absolute():
        parsed = cwd / parsed
    return str(parsed.resolve())


@dataclass
class HarnessConfig:
    """All parameters for a harness ``run``."""

    # --- required (via config file or CLI) ---
    db_path: Optional[str] = None
    """Path to the SQLite task database (on a shared filesystem)."""
    sim_params_path: Optional[str] = None
    """Path to the single ``*.simulation.json`` used for every task in this run."""
    result_root: Optional[str] = None
    """Directory (shared filesystem) under which per-task result folders are created."""

    # --- memory-gated admission ---
    per_sim_mem_gb: float = 10.0
    """Estimated peak memory of one HiSim simulation. Used for slot sizing and the gate."""
    min_headroom_gb: float = 12.0
    """Minimum free memory that must remain available. A new simulation is launched only
    when ``psutil.available >= per_sim_mem_gb + min_headroom_gb``."""
    max_slots: Optional[int] = None
    """Hard cap on concurrent simulations per node. Defaults to
    ``floor((node_total_mem - min_headroom_gb) / per_sim_mem_gb)`` computed per node."""

    # --- failure handling ---
    timeout_s: float = 7200.0
    """Wall-clock timeout per simulation. Overruns are killed and retried."""
    max_attempts: int = 3
    """Maximum attempts per task before it is marked ``dead``."""
    lease_timeout_s: Optional[float] = None
    """A leased task with no report after this long is reclaimed. Defaults to ``2 * timeout_s``."""

    # --- behaviour / tuning ---
    head_runs_jobs: bool = True
    """If true, rank 0 also runs a local simulation pool instead of being a pure dispatcher."""
    sample_interval_s: float = 1.0
    """Loop tick: how often to sample memory, reap subprocesses and launch new ones."""
    backoff_s: float = 5.0
    """How long an agent waits after a NO_WORK_AVAILABLE reply before requesting work again."""

    # --- deprecated attribute aliases (issue #614) ---------------------------
    # The fields above were renamed (db -> db_path, sim_params -> sim_params_path).
    # These read-write properties keep direct attribute access working for any
    # external consumer that has not yet migrated, emitting a DeprecationWarning.

    @property
    def db(self) -> Optional[str]:
        """Deprecated alias for :attr:`db_path`."""
        warnings.warn(
            "HarnessConfig.db is deprecated; use db_path instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.db_path

    @db.setter
    def db(self, value: Optional[str]) -> None:
        warnings.warn(
            "HarnessConfig.db is deprecated; use db_path instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.db_path = value

    @property
    def sim_params(self) -> Optional[str]:
        """Deprecated alias for :attr:`sim_params_path`."""
        warnings.warn(
            "HarnessConfig.sim_params is deprecated; use sim_params_path instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.sim_params_path

    @sim_params.setter
    def sim_params(self, value: Optional[str]) -> None:
        warnings.warn(
            "HarnessConfig.sim_params is deprecated; use sim_params_path instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.sim_params_path = value

    @classmethod
    def from_file(cls, path: str) -> "HarnessConfig":
        """Load a config from a JSON file, rejecting unknown keys.

        Thin I/O wrapper around :meth:`from_dict`: it reads and parses the
        JSON file at ``path`` and delegates all parsing/validation
        (deprecated-key aliasing, both-old-and-new-key conflict check,
        unknown-key rejection) to :meth:`from_dict`, passing ``path`` as the
        ``source`` label used in error/warning messages.

        Args:
            path: Filesystem path to a JSON config file.

        Returns:
            A new :class:`HarnessConfig` populated from the JSON keys.

        Raises:
            ValueError: If the JSON contains keys that are not
                :class:`HarnessConfig` fields, or if both a deprecated key
                (``db``, ``sim_params``) and its renamed form are present.
            json.JSONDecodeError: Propagated from parsing the file when its
                contents are not valid JSON.
            OSError: Propagated from reading the file at ``path``.

        Warns:
            DeprecationWarning: If a deprecated JSON key (``db`` or
                ``sim_params``) is used instead of its renamed form.
        """
        return cls.from_dict(
            json.loads(Path(path).read_text(encoding="utf-8")),
            source=path,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, source: str = "<dict>") -> "HarnessConfig":
        """Build a config from a parsed JSON ``dict``, rejecting unknown keys.

        This is the pure, filesystem-free parsing/validation seam behind
        :meth:`from_file`: it performs the deprecated-key aliasing (with a
        :class:`DeprecationWarning`), the both-old-and-new-key conflict check
        and the unknown-key rejection, then constructs the instance with
        ``cls(**data)``. Because it takes an already-parsed ``dict`` it can be
        unit-tested directly -- no ``tmp_path`` / JSON file required -- which
        is the only reason it exists separately from :meth:`from_file`.

        ``data`` is mutated in place when a deprecated key is remapped to its
        current name (mirroring the historical behaviour of
        :meth:`from_file`). ``source`` is used solely inside error/warning
        messages to identify where ``data`` came from (the file path when
        called via :meth:`from_file`, or any label a caller chooses when
        invoking :meth:`from_dict` directly); it never affects the constructed
        config.

        Args:
            data: A parsed JSON config (the mapping that ``json.loads`` would
                return). Modified in place when a deprecated key is remapped.
            source: Human-readable label for the origin of ``data``, included
                in :class:`ValueError` messages. Defaults to ``"<dict>"``.

        Returns:
            A new :class:`HarnessConfig` populated from ``data``.

        Raises:
            ValueError: If ``data`` contains keys that are not
                :class:`HarnessConfig` fields, or if both a deprecated key
                (``db``, ``sim_params``) and its renamed form are present.

        Warns:
            DeprecationWarning: If a deprecated key (``db`` or ``sim_params``)
                is used instead of its renamed form.
        """
        # Accept the pre-rename JSON keys ("db", "sim_params") for backward
        # compatibility, mapping them to the current field names with a
        # DeprecationWarning so existing config files keep working (issue #614).
        for old_name, new_name in _DEPRECATED_FIELD_ALIASES.items():
            if old_name in data:
                if new_name in data:
                    raise ValueError(
                        f"Harness config {source} sets both '{old_name}' and its "
                        f"renamed form '{new_name}'; remove the deprecated "
                        f"'{old_name}' key."
                    )
                warnings.warn(
                    f"Harness config key '{old_name}' is deprecated; use "
                    f"'{new_name}' instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                data[new_name] = data.pop(old_name)
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Unknown keys in harness config {source}: {sorted(unknown)}")
        return cls(**data)

    def apply_overrides(self, **kwargs: Optional[Union[str, float, int, bool]]) -> "HarnessConfig":
        """Override fields with any non-None keyword values (used for CLI flags).

        Args:
            **kwargs: Field-name/value pairs; any non-None value whose name
                matches a :class:`HarnessConfig` field overwrites the current
                value. Unknown names and ``None`` values are silently ignored.
                The pre-rename names ``db`` and ``sim_params`` are still
                accepted (with a :class:`DeprecationWarning`) and mapped to
                ``db_path`` and ``sim_params_path`` (issue #614).

        Returns:
            ``self``, for chaining.

        Raises:
            ValueError: If both a deprecated name and its renamed form are
                supplied at the same time.
        """
        # Remap deprecated keyword names before applying (issue #614).
        # A deprecated name passed as ``None`` means "no override" and is
        # silently ignored, even if the renamed form is also present.
        for old_name, new_name in _DEPRECATED_FIELD_ALIASES.items():
            if old_name in kwargs:
                value = kwargs.pop(old_name)
                if value is not None:
                    if new_name in kwargs:
                        raise ValueError(
                            f"apply_overrides received both '{old_name}' and its "
                            f"renamed form '{new_name}'; use only '{new_name}'."
                        )
                    warnings.warn(
                        f"apply_overrides keyword '{old_name}' is deprecated; "
                        f"use '{new_name}' instead.",
                        DeprecationWarning,
                        stacklevel=2,
                    )
                    kwargs[new_name] = value
        for key, value in kwargs.items():
            if value is not None and hasattr(self, key):
                setattr(self, key, value)
        return self

    def finalize(
        self,
        *,
        cwd: Optional[Path] = None,
        home: Optional[Path] = None,
    ) -> "HarnessConfig":
        """Fill in derived defaults and validate required fields.

        When ``lease_timeout_s`` is unset it defaults to ``2 * timeout_s``.
        The ``db_path``, ``sim_params_path`` and ``result_root`` path strings are then
        rewritten as absolute, user-expanded, resolved paths so every node
        resolves them identically.

        Path normalisation is delegated to :func:`_normalize_path`. With both
        ``cwd`` and ``home`` left as ``None`` (the production default) the
        result matches ``Path(...).expanduser().resolve()``, i.e. it is taken
        against the process's current working directory and ``HOME``. Passing
        explicit ``cwd`` and/or ``home`` resolves the paths against fixed
        locations instead, which is how tests assert on the normalised strings
        deterministically without ``monkeypatch.chdir``/``monkeypatch.setenv``.

        Args:
            cwd: Optional base directory for resolving relative path strings.
                Defaults to :meth:`Path.cwd`.
            home: Optional home directory substituted for a leading ``~``.
                Defaults to :meth:`Path.home`.

        Returns:
            ``self``, with derived defaults filled in and paths normalised.

        Raises:
            ValueError: If ``db_path``, ``sim_params_path`` or ``result_root`` is not set
                (raised via :meth:`required_paths`).
        """
        # Finalisation is split into two single-purpose steps (issue #698):
        # a pure, filesystem-free default-derivation pass followed by the path
        # normalisation I/O. Splitting them lets the derived-default rule be
        # unit-tested in isolation (see _apply_derived_defaults) without having
        # to supply three real, resolvable paths.
        self._apply_derived_defaults()
        self._resolve_paths(cwd=cwd, home=home)
        return self

    def _apply_derived_defaults(self) -> None:
        """Fill in derived-default fields that depend on other settings.

        Pure: this touches only ``self`` and performs no filesystem access, so
        it can be exercised without the three resolvable paths that
        :meth:`required_paths` / :meth:`_resolve_paths` demand. Currently it
        derives ``lease_timeout_s = 2.0 * timeout_s`` when unset; any future
        derived field belongs here so it stays independently testable.

        Idempotent: a second call leaves an already-set ``lease_timeout_s``
        untouched.
        """
        if self.lease_timeout_s is None:
            self.lease_timeout_s = 2.0 * self.timeout_s

    def _resolve_paths(
        self,
        *,
        cwd: Optional[Path] = None,
        home: Optional[Path] = None,
    ) -> None:
        """Normalise the three required path strings to absolute form in place.

        Thin I/O seam around :func:`_normalize_path`: this is the only place
        :meth:`finalize` (and tests) touch the filesystem for path
        normalisation, keeping that mutation localised. It first validates via
        :meth:`required_paths` (raising ``ValueError`` if any required path is
        unset) and then rewrites ``db_path``, ``sim_params_path`` and
        ``result_root`` to their resolved string form.

        Args:
            cwd: Optional base directory for resolving relative path strings.
                Defaults to :meth:`Path.cwd`.
            home: Optional home directory substituted for a leading ``~``.
                Defaults to :meth:`Path.home`.
        """
        db_path, sim_params_path, result_root_path = self.required_paths()
        # Normalise paths to absolute so every node resolves them identically.
        # _normalize_path keeps this independent of CWD/HOME when cwd/home are
        # injected (e.g. by tests); the defaults reproduce expanduser().resolve().
        self.db_path = _normalize_path(db_path, cwd=cwd, home=home)
        self.sim_params_path = _normalize_path(sim_params_path, cwd=cwd, home=home)
        self.result_root = _normalize_path(result_root_path, cwd=cwd, home=home)

    def required_paths(self) -> Tuple[str, str, str]:
        """Return required path settings after validating that all are configured.

        Returns:
            The ``(db_path, sim_params_path, result_root)`` path strings.

        Raises:
            ValueError: If any of ``db_path``, ``sim_params_path`` or ``result_root`` is
                ``None``.
        """
        missing = [name for name in ("db_path", "sim_params_path", "result_root") if getattr(self, name) is None]
        if missing:
            raise ValueError(
                f"Missing required harness settings: {missing}. "
                "Provide them in the config file or via command-line flags."
            )
        if self.db_path is None or self.sim_params_path is None or self.result_root is None:
            raise ValueError("Harness path settings were not fully configured.")
        return self.db_path, self.sim_params_path, self.result_root
