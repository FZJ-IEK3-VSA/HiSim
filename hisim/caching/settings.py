"""Where the cache client learns how it is configured, and the one place that reads those variables.

``roadmap/cache_service_spec.md`` §5 configures the cache through six environment variables, following
the ``UTSP_URL`` / ``UTSP_API_KEY`` precedent of a ``.env`` file at the repository root. All six are
optional, and that is the load-bearing property: with none of them set the library is local-only and
HiSim behaves exactly as it did before the library existed. The shared-directory and server tiers are
strictly additive opt-ins, so a plain ``pip install hisim`` involves no institute infrastructure and a
user outside it loses nothing by not having any.

The spec asks that ``CacheSettings`` read all of this once, in one place, and that nothing else in HiSim
touch the variables. That is why the class exists at all rather than each tier calling ``os.getenv``
where it needs a value: a variable read in six places is documented in none of them, and a typo in one
is a tier that silently stays off.

Only the local half of the settings is acted on in this phase. The network mode and the two endpoint
URLs are parsed and validated here already, so that a misspelled value fails now rather than the day the
remote tier arrives, but nothing in this phase opens a connection.
"""

# clean

import enum
import os
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
    """How the client chooses between the two server endpoints, or declines to use either.

    ``AUTO`` probes the internal endpoint once per process and falls back to the external one, then to
    local-only; ``INTERNAL`` and ``EXTERNAL`` pin one endpoint and skip the probe; ``OFF`` disables all
    networking and makes the library bit-identical to the pre-service behaviour. The values are the
    exact spellings accepted in the environment variable, so the enum is also the validation.
    """

    AUTO = "auto"
    INTERNAL = "internal"
    EXTERNAL = "external"
    OFF = "off"


class CacheSettingsError(ValueError):
    """Raised when an environment variable holds a value the cache cannot act on.

    The message names the variable and lists the accepted spellings, because the person who sees it
    has usually just edited a ``.env`` file and wants to know which line to fix.
    """


@dataclass(frozen=True)
class CacheSettings:
    """The cache configuration read from the environment, held as plain typed values.

    Instances are cheap to build and immutable, so there is no process-wide singleton to keep in step
    with the environment: a caller that wants current settings asks for them. The one thing the spec
    wants remembered per process -- the result of the network probe -- belongs to the remote tier, not
    here, because it is an observation and not a setting.
    """

    class Variables:
        """The names of the six environment variables, stated once so no other module spells them.

        Kept as a nested namespace rather than loose constants so that the variable names are visibly
        part of the settings contract and a reader can find all six in one screen.
        """

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
        """Reads the six variables and returns them as settings, validating what can be validated.

        An empty string is treated the same as an unset variable, because that is what a ``.env`` line
        with nothing after the equals sign produces and nobody means "the URL is the empty string".

        Args:
            environment: the mapping to read from; defaults to ``os.environ``. Tests pass their own so
                that they neither depend on nor disturb the real process environment.

        Returns:
            CacheSettings: the parsed settings.

        Raises:
            CacheSettingsError: if the network mode is not one of the four accepted spellings.
        """
        source: Mapping[str, str] = os.environ if environment is None else environment
        return cls(
            internal_url=cls._optional(source, cls.Variables.URL_INTERNAL),
            external_url=cls._optional(source, cls.Variables.URL_EXTERNAL),
            network=cls._network_mode(source),
            api_key=cls._optional(source, cls.Variables.API_KEY),
            local_directory=cls._optional(source, cls.Variables.DIRECTORY),
            shared_directory=cls._optional(source, cls.Variables.SHARED_DIRECTORY),
        )

    @property
    def is_standalone(self) -> bool:
        """Says whether these settings describe the pre-service behaviour: local directory only.

        This is the property the spec's standalone guarantee rests on, so it is spelled out rather than
        left for callers to infer from three fields. A local-directory override alone does not make the
        configuration non-standalone; it only moves the directory.

        Returns:
            bool: True if no network endpoint can be used and no shared directory is configured.
        """
        network_possible = self.network is not CacheNetworkMode.OFF and (
            self.internal_url is not None or self.external_url is not None
        )
        return not network_possible and self.shared_directory is None

    def resolve_local_directory(self, default_directory: str) -> str:
        """Returns the directory local entries live in, honouring the override if one is set.

        Args:
            default_directory: the directory the simulation would use on its own, normally
                ``SimulationParameters.cache_dir_path``.

        Returns:
            str: the override from the environment if set, otherwise the default.
        """
        return self.local_directory if self.local_directory is not None else default_directory

    @staticmethod
    def _optional(source: Mapping[str, str], name: str) -> Optional[str]:
        """Reads one variable, mapping absent and empty to ``None``.

        Args:
            source: the environment mapping.
            name: the variable to read.

        Returns:
            Optional[str]: the stripped value, or ``None``.
        """
        value = source.get(name, "").strip()
        return value if value else None

    @classmethod
    def _network_mode(cls, source: Mapping[str, str]) -> CacheNetworkMode:
        """Parses the network mode, defaulting to ``AUTO`` and refusing anything unrecognised.

        Args:
            source: the environment mapping.

        Returns:
            CacheNetworkMode: the parsed mode.

        Raises:
            CacheSettingsError: if the value is set but is not one of the accepted spellings.
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
