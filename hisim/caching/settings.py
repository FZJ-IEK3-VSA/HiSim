"""Cache settings: the six environment variables the cache client reads, read in one place.

The variables are described in ``roadmap/cache_service_spec.md`` §5 and follow the ``UTSP_URL`` /
``UTSP_API_KEY`` precedent of a ``.env`` file in the repository root. All of them are optional. With none
set, HiSim uses only its local cache directory and behaves exactly as it did before the cache client
existed; the shared-directory and server tiers are opt-ins on top of that.

Only the local settings are used in this phase. The network mode and the endpoint URLs are already
parsed and validated so that a typo fails now, but no connection is opened yet.
"""

# clean

import enum
import os
import urllib.parse
from dataclasses import dataclass
from typing import ClassVar, Mapping, Optional

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

    The message names the variable and lists the accepted values, so the ``.env`` line to fix is clear.
    """


@dataclass(frozen=True)
class CacheSettings:
    """The cache configuration, read from the environment into plain typed fields.

    Create one with :meth:`from_environment`. Instances are immutable and cheap, so there is no
    process-wide singleton; code that wants the current settings asks for them.
    """

    class Variables:
        """The names of the six environment variables. No other module spells them."""

        URL_INTERNAL: ClassVar[str] = "HISIM_CACHE_URL_INTERNAL"
        URL_EXTERNAL: ClassVar[str] = "HISIM_CACHE_URL_EXTERNAL"
        NETWORK: ClassVar[str] = "HISIM_CACHE_NETWORK"
        API_KEY: ClassVar[str] = "HISIM_CACHE_API_KEY"
        DIRECTORY: ClassVar[str] = "HISIM_CACHE_DIR"
        SHARED_DIRECTORY: ClassVar[str] = "HISIM_CACHE_SHARED_DIR"

    #: The institute-network endpoint, tried first under ``AUTO``; ``None`` when unset.
    internal_url: Optional[str]

    #: The public endpoint, the fallback under ``AUTO``; ``None`` when unset.
    external_url: Optional[str]

    #: Which endpoint to use, if any. Defaults to ``AUTO`` when the variable is absent.
    network: CacheNetworkMode

    #: The write token. Reading may not need one; writing to the server always does.
    api_key: Optional[str]

    #: An override for the local cache directory; ``None`` means the simulation's own default.
    local_directory: Optional[str]

    #: The shared-filesystem tier of spec §5.1; ``None`` means the tier is off.
    shared_directory: Optional[str]

    @classmethod
    def from_environment(cls, environment: Optional[Mapping[str, str]] = None) -> "CacheSettings":
        """Read the six variables and return them as settings.

        An empty string counts as unset, because that is what a ``.env`` line with nothing after the
        equals sign produces.

        Args:
            environment: the mapping to read; ``os.environ`` when omitted. Tests pass their own.

        Returns:
            CacheSettings: the parsed settings.

        Raises:
            CacheSettingsError: if ``HISIM_CACHE_NETWORK`` is not one of the four accepted values, if a
                set endpoint URL is not a URL, or if a pinned ``internal``/``external`` mode has no URL
                to pin to.
        """
        source: Mapping[str, str] = os.environ if environment is None else environment
        settings = cls(
            internal_url=cls._url(source, cls.Variables.URL_INTERNAL),
            external_url=cls._url(source, cls.Variables.URL_EXTERNAL),
            network=cls._network_mode(source),
            api_key=cls._optional(source, cls.Variables.API_KEY),
            local_directory=cls._optional(source, cls.Variables.DIRECTORY),
            shared_directory=cls._optional(source, cls.Variables.SHARED_DIRECTORY),
        )
        cls._check_mode_has_its_url(settings)
        return settings

    @property
    def is_standalone(self) -> bool:
        """Whether only the local cache directory is in use.

        True when no server endpoint can be reached (mode ``OFF``, or no URL set) and no shared directory is
        configured. A local-directory override alone does not change this; it only moves the directory.

        Returns:
            bool: True for local-only operation.
        """
        network_possible = self.network is not CacheNetworkMode.OFF and (
            self.internal_url is not None or self.external_url is not None
        )
        return not network_possible and self.shared_directory is None

    def resolve_local_directory(self, default_directory: str) -> str:
        """Return the local cache directory: the ``HISIM_CACHE_DIR`` override if set, else the default.

        Args:
            default_directory: normally ``SimulationParameters.cache_dir_path``.

        Returns:
            str: the directory to use.
        """
        return self.local_directory if self.local_directory is not None else default_directory

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
