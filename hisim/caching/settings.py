"""Cache settings: the five environment variables the cache client reads, read in one place.

The variables are described in ``roadmap/cache_service_spec.md`` §5 and follow the ``UTSP_URL`` /
``UTSP_API_KEY`` precedent of a ``.env`` file in the repository root. All of them are optional. With none
set, HiSim uses only its default cache directory and behaves exactly as it did before the cache client
existed; the server tier is an opt-in on top of that.

``HISIM_CACHE_DIRECTORIES`` is the one variable that names cache directories, for every run: plain
HiSim in both modes, the energy-system run, RenoVisor and the economics CLI (owner decision of
2026-11-25). It is an ordered list, read in order and written to the first writable entry; one entry
is a single cache directory. The two variables it replaced, ``HISIM_CACHE_DIR`` and
``HISIM_CACHE_SHARED_DIR``, are refused when set, so a stale ``.env`` line fails the run instead of
being ignored.

Only the directory settings are used in this phase. The network mode and the endpoint URLs are already
parsed and validated so that a typo fails now, but no connection is opened yet.
"""

import enum
import os
import urllib.parse
from dataclasses import dataclass
from typing import ClassVar, List, Mapping, Optional, Sequence, Tuple

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


class CacheNetworkMode(enum.Enum):
    """Which server endpoint the client may use.

    ``AUTO`` probes the internal endpoint once and falls back to the external one, then to local-only.
    ``INTERNAL`` and ``EXTERNAL`` pin one endpoint. ``OFF`` disables all network access. The enum values
    are the exact spellings accepted in ``HISIM_CACHE_NETWORK``. No probing happens in this phase: the
    mode is parsed and validated, but no connection is opened until the network tier ships.
    """

    AUTO = "auto"
    INTERNAL = "internal"
    EXTERNAL = "external"
    OFF = "off"


class CacheSettingsError(ValueError):
    """Raised when an environment variable holds a value the cache client cannot use.

    The message names the variable and says what is accepted instead, so the ``.env`` line to fix is clear.
    """


@dataclass(frozen=True)
class CacheSettings:
    """The cache configuration, read from the environment into plain typed fields.

    Create one with :meth:`from_environment`. Instances are immutable and cheap, so there is no
    process-wide singleton; code that wants the current settings asks for them.
    """

    class Variables:
        """The names of the five environment variables and of the two retired ones. No other module spells them."""

        URL_INTERNAL: ClassVar[str] = "HISIM_CACHE_URL_INTERNAL"
        URL_EXTERNAL: ClassVar[str] = "HISIM_CACHE_URL_EXTERNAL"
        NETWORK: ClassVar[str] = "HISIM_CACHE_NETWORK"
        API_KEY: ClassVar[str] = "HISIM_CACHE_API_KEY"
        DIRECTORIES: ClassVar[str] = "HISIM_CACHE_DIRECTORIES"

        #: The single cache directory that used to replace the whole directory list; one entry of
        #: ``DIRECTORIES`` means the same now. Refused when set.
        RETIRED_DIRECTORY: ClassVar[str] = "HISIM_CACHE_DIR"

        #: The shared-directory tier of spec §5.1, dropped before it was built. Refused when set.
        RETIRED_SHARED_DIRECTORY: ClassVar[str] = "HISIM_CACHE_SHARED_DIR"

        #: Both retired variables, in the order they are checked.
        RETIRED: ClassVar[Tuple[str, ...]] = (RETIRED_DIRECTORY, RETIRED_SHARED_DIRECTORY)

    #: The institute-network endpoint, tried first under ``AUTO``; ``None`` when unset.
    internal_url: Optional[str]

    #: The public endpoint, the fallback under ``AUTO``; ``None`` when unset.
    external_url: Optional[str]

    #: Which endpoint to use, if any. Defaults to ``AUTO`` when the variable is absent.
    network: CacheNetworkMode

    #: The write token. Reading may not need one; writing to the server always does.
    api_key: Optional[str]

    #: The cache directories of ``HISIM_CACHE_DIRECTORIES``, in read order; empty when the variable is unset.
    directories: Tuple[str, ...]

    @classmethod
    def from_environment(cls, environment: Optional[Mapping[str, str]] = None) -> "CacheSettings":
        """Read the five variables and return them as settings, refusing the two retired ones.

        An empty string counts as unset, because that is what a ``.env`` line with nothing after the
        equals sign produces. For example, ``{"HISIM_CACHE_DIRECTORIES": "/seed:/volume"}`` gives settings
        whose ``directories`` are ``("/seed", "/volume")`` and whose network mode is ``AUTO``.

        Args:
            environment: the mapping to read; ``os.environ`` when omitted. Tests pass their own.

        Returns:
            CacheSettings: the parsed settings.

        Raises:
            CacheSettingsError: if ``HISIM_CACHE_DIR`` or ``HISIM_CACHE_SHARED_DIR`` is set, if
                ``HISIM_CACHE_DIRECTORIES`` names no directory or a relative one, if ``HISIM_CACHE_NETWORK``
                is not one of the four accepted values, if a set endpoint URL is not a URL, or if a pinned
                ``internal``/``external`` mode has no URL to pin to.
        """
        source: Mapping[str, str] = os.environ if environment is None else environment
        cls.refuse_retired_variables(source)
        settings = cls(
            internal_url=cls._url(source, cls.Variables.URL_INTERNAL),
            external_url=cls._url(source, cls.Variables.URL_EXTERNAL),
            network=cls._network_mode(source),
            api_key=cls._optional(source, cls.Variables.API_KEY),
            directories=cls.parse_directories(cls._optional(source, cls.Variables.DIRECTORIES)),
        )
        cls._check_mode_has_its_url(settings)
        return settings

    @property
    def is_standalone(self) -> bool:
        """Whether only the local cache directory is in use.

        True when no server endpoint can be reached (mode ``OFF``, or no URL set). The cache directories
        do not change this; they only say where the local cache is.

        Returns:
            bool: True for local-only operation.
        """
        network_possible = self.network is not CacheNetworkMode.OFF and (
            self.internal_url is not None or self.external_url is not None
        )
        return not network_possible

    def resolve_directories(self, explicit_directories: Sequence[str], default_directory: str) -> List[str]:
        """Return the cache directories a run reads, in order: the explicit list, else the variable's, else the default.

        Example: with ``HISIM_CACHE_DIRECTORIES=/seed:/volume``, a run whose parameters list no directories
        gets ``["/seed", "/volume"]``; one whose parameters list ``["/mine"]`` gets ``["/mine"]``; with the
        variable unset, a run without a list gets ``[default_directory]``.

        The precedence is "stated in code wins": a list the parameters were given (in a setup, a
        simulation file, or RenoVisor's ``--cache-dir``) is a decision about this run and outranks the
        environment, which describes the machine. The default directory, ``cache_dir_path``, is what the
        variable replaces, as ``HISIM_CACHE_DIR`` replaced it before.

        Args:
            explicit_directories: ``SimulationParameters.cache_directories``; empty when none were given.
            default_directory: ``SimulationParameters.cache_dir_path``.

        Returns:
            List[str]: at least one directory, in read order; new entries go to the first writable one.
        """
        if explicit_directories:
            return list(explicit_directories)
        if self.directories:
            return list(self.directories)
        return [default_directory]

    @classmethod
    def refuse_retired_variables(cls, source: Mapping[str, str]) -> None:
        """Refuse a run whose environment still sets ``HISIM_CACHE_DIR`` or ``HISIM_CACHE_SHARED_DIR``.

        Both were replaced by ``HISIM_CACHE_DIRECTORIES`` on 2026-11-25. Ignoring a stale setting would
        quietly move the cache back to the default directory, so it fails instead, naming the variable and
        its replacement. An empty value counts as unset, like every other variable here.

        Args:
            source: the environment mapping.

        Raises:
            CacheSettingsError: if a retired variable holds a value.
        """
        for name in cls.Variables.RETIRED:
            value = cls._optional(source, name)
            if value is not None:
                raise CacheSettingsError(
                    f"{name} is set to {value!r}, but it is no longer read: the cache directories are named by "
                    f"{cls.Variables.DIRECTORIES} alone, an ordered list separated by {os.pathsep!r}, read in order "
                    f"and written to the first writable entry. Unset {name} and set {cls.Variables.DIRECTORIES} "
                    "instead; one entry is a single cache directory."
                )

    @classmethod
    def parse_directories(cls, value: Optional[str]) -> Tuple[str, ...]:
        """Split the value of ``HISIM_CACHE_DIRECTORIES`` into absolute directories, in their order.

        Example: ``"/seed:/volume"`` gives ``("/seed", "/volume")`` on POSIX, where :data:`os.pathsep` is
        ``:``; empty entries from doubled or trailing separators are skipped. ``None`` gives ``()``.

        Args:
            value: the stripped value, or ``None`` when the variable is unset or empty.

        Returns:
            Tuple[str, ...]: the directories; empty when the variable is unset.

        Raises:
            CacheSettingsError: if the value names no directory (only separators), or an entry is relative.
                A relative entry would name a different directory for every working directory, so two
                workers sharing a cache would not agree on where it is.
        """
        if value is None:
            return ()
        directories = tuple(entry for entry in value.split(os.pathsep) if entry)
        if not directories:
            raise CacheSettingsError(
                f"{cls.Variables.DIRECTORIES} is {value!r}, which names no directory; unset it to use the default "
                "cache directory, or list at least one absolute path"
            )
        relative = [entry for entry in directories if not os.path.isabs(entry)]
        if relative:
            raise CacheSettingsError(
                f"{cls.Variables.DIRECTORIES} holds relative path(s) {relative!r}; every cache directory has to be "
                "absolute, so two workers agree on where the cache is whatever their working directory is"
            )
        return directories

    @classmethod
    def _url(cls, source: Mapping[str, str], name: str) -> Optional[str]:
        """Read one endpoint variable and refuse a value that is not a URL.

        The network tier that would use the URL ships in a later phase, but a typo must fail now, at
        configuration time, instead of surfacing as a confusing connection error months later.

        Args:
            source: the environment mapping.
            name: the variable name.

        Returns:
            Optional[str]: the URL, or ``None`` when unset.

        Raises:
            CacheSettingsError: if the value has no scheme or no host, or the scheme is not http/https.
        """
        value = cls._optional(source, name)
        if value is None:
            return None
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise CacheSettingsError(
                f"{name} is set to {value!r}, which is not an endpoint URL; expected something like "
                "'https://cache.example.org'."
            )
        return value

    @staticmethod
    def _check_mode_has_its_url(settings: "CacheSettings") -> None:
        """Refuse a pinned network mode whose endpoint URL is missing.

        ``internal`` or ``external`` without the matching URL cannot ever connect; reporting it as
        standalone would hide the misconfiguration until the network phase.

        Args:
            settings: the parsed settings.

        Raises:
            CacheSettingsError: if the pinned mode's URL is unset.
        """
        pins = {
            CacheNetworkMode.INTERNAL: (settings.internal_url, CacheSettings.Variables.URL_INTERNAL),
            CacheNetworkMode.EXTERNAL: (settings.external_url, CacheSettings.Variables.URL_EXTERNAL),
        }
        if settings.network in pins:
            url, variable = pins[settings.network]
            if url is None:
                raise CacheSettingsError(
                    f"{CacheSettings.Variables.NETWORK} pins the {settings.network.value} endpoint, but "
                    f"{variable} is not set, so there is nothing to connect to; set the URL or drop the pin."
                )

    @staticmethod
    def _optional(source: Mapping[str, str], name: str) -> Optional[str]:
        """Read one variable; absent and empty both become ``None``.

        Args:
            source: the environment mapping.
            name: the variable name.

        Returns:
            Optional[str]: the stripped value, or ``None``.
        """
        value = source.get(name, "").strip()
        return value if value else None

    @classmethod
    def _network_mode(cls, source: Mapping[str, str]) -> CacheNetworkMode:
        """Parse ``HISIM_CACHE_NETWORK``; unset means ``AUTO``.

        Args:
            source: the environment mapping.

        Returns:
            CacheNetworkMode: the mode.

        Raises:
            CacheSettingsError: if the value is set but not one of the accepted spellings.
        """
        raw = cls._optional(source, cls.Variables.NETWORK)
        if raw is None:
            return CacheNetworkMode.AUTO
        try:
            return CacheNetworkMode(raw.lower())
        except ValueError as error:
            accepted = ", ".join(mode.value for mode in CacheNetworkMode)
            raise CacheSettingsError(
                f"{cls.Variables.NETWORK} is set to {raw!r}, which is not a cache network mode; "
                f"accepted values are: {accepted}."
            ) from error
