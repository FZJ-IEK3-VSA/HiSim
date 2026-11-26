"""Tests for :mod:`hisim.caching.settings`.

The first test pins the standalone guarantee of spec §5: with no variables set, HiSim is local-only.
The others pin how each variable is read, how the cache directories of a run are resolved, and the
refusal of the two retired variables (owner decision of 2026-11-25).
"""

import os

import pytest

from hisim.caching import CacheNetworkMode, CacheSettings, CacheSettingsError

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


@pytest.mark.base
def test_no_variables_means_standalone_and_auto() -> None:
    """The standalone guarantee: an empty environment is local-only, with the network mode at its default.

    Catches: a default that switches a tier on for a user who set nothing.
    """
    settings = CacheSettings.from_environment({})

    assert settings.is_standalone
    assert settings.network is CacheNetworkMode.AUTO
    assert settings.internal_url is None
    assert settings.external_url is None
    assert settings.api_key is None
    assert not settings.directories


@pytest.mark.base
def test_every_variable_is_read_from_its_documented_name() -> None:
    """Each of the five variables lands in its field, and nothing else in the environment is read.

    Catches: a field wired to the wrong variable, which would silently ignore a user's setting.
    """
    settings = CacheSettings.from_environment(
        {
            CacheSettings.Variables.URL_INTERNAL: "https://10.0.0.1/hisim-cache",
            CacheSettings.Variables.URL_EXTERNAL: "https://cache.example.org/hisim-cache",
            CacheSettings.Variables.NETWORK: "external",
            CacheSettings.Variables.API_KEY: "token",
            CacheSettings.Variables.DIRECTORIES: os.pathsep.join(["/somewhere/seed", "/somewhere/volume"]),
            "UNRELATED": "ignored",
        }
    )

    assert settings.internal_url == "https://10.0.0.1/hisim-cache"
    assert settings.external_url == "https://cache.example.org/hisim-cache"
    assert settings.network is CacheNetworkMode.EXTERNAL
    assert settings.api_key == "token"
    assert settings.directories == ("/somewhere/seed", "/somewhere/volume")
    assert not settings.is_standalone


@pytest.mark.base
def test_an_empty_value_is_the_same_as_an_unset_one() -> None:
    """A ``.env`` line with nothing after the equals sign does not mean "the URL is the empty string".

    Catches: an empty URL being treated as a configured endpoint and probed.
    """
    settings = CacheSettings.from_environment(
        {
            CacheSettings.Variables.URL_INTERNAL: "  ",
            CacheSettings.Variables.DIRECTORIES: "",
            CacheSettings.Variables.RETIRED_DIRECTORY: "",
            CacheSettings.Variables.RETIRED_SHARED_DIRECTORY: " ",
        }
    )

    assert settings.internal_url is None
    assert not settings.directories
    assert settings.is_standalone


@pytest.mark.base
@pytest.mark.parametrize("spelling", ["off", "OFF", "Off"])
def test_the_network_mode_is_case_insensitive(spelling: str) -> None:
    """``off`` is accepted however it is capitalised, because people do.

    Catches: a mode that is only recognised in one case, which a user would read as the setting not
    working.
    """
    settings = CacheSettings.from_environment({CacheSettings.Variables.NETWORK: spelling})

    assert settings.network is CacheNetworkMode.OFF


@pytest.mark.base
def test_an_unknown_network_mode_is_refused_naming_the_variable_and_the_options() -> None:
    """A typo in the mode fails at once, with the fix in the message.

    Catches: a misspelled mode silently falling back to a default, so a user who wrote ``offline``
    to disable the network has it enabled.
    """
    with pytest.raises(CacheSettingsError) as raised:
        CacheSettings.from_environment({CacheSettings.Variables.NETWORK: "offline"})

    message = str(raised.value)
    assert CacheSettings.Variables.NETWORK in message
    assert "offline" in message
    for mode in CacheNetworkMode:
        assert mode.value in message


@pytest.mark.base
@pytest.mark.parametrize("value", ["htp//10.0.0.1/not a real url", "not a url at all", "ftp://cache.example.org"])
def test_a_malformed_endpoint_url_is_refused_naming_the_variable(value: str) -> None:
    """A typo in an endpoint URL fails at configuration time, not in the network phase months later.

    Catches: a misspelled URL being stored silently, surfacing as a confusing connection error only
    once the network tier ships.
    """
    with pytest.raises(CacheSettingsError) as raised:
        CacheSettings.from_environment({CacheSettings.Variables.URL_INTERNAL: value})

    message = str(raised.value)
    assert CacheSettings.Variables.URL_INTERNAL in message
    assert value in message


@pytest.mark.base
@pytest.mark.parametrize(
    ("mode", "variable"),
    [("internal", CacheSettings.Variables.URL_INTERNAL), ("external", CacheSettings.Variables.URL_EXTERNAL)],
)
def test_a_pinned_mode_without_its_url_is_refused(mode: str, variable: str) -> None:
    """``internal`` or ``external`` with no matching URL cannot ever connect, so it fails now.

    Catches: the misconfiguration being masked as standalone operation, hiding it until the network
    phase.
    """
    with pytest.raises(CacheSettingsError) as raised:
        CacheSettings.from_environment({CacheSettings.Variables.NETWORK: mode})

    message = str(raised.value)
    assert variable in message
    assert mode in message


@pytest.mark.base
def test_network_off_with_urls_set_is_still_standalone() -> None:
    """Setting the mode to ``off`` wins over any endpoint that is configured.

    This is the reversibility promise of spec §10: ``HISIM_CACHE_NETWORK=off`` returns to phase-0
    behaviour at any time, whatever else is set.

    Catches: an endpoint variable overriding the explicit off switch.
    """
    settings = CacheSettings.from_environment(
        {
            CacheSettings.Variables.URL_INTERNAL: "https://10.0.0.1/hisim-cache",
            CacheSettings.Variables.NETWORK: "off",
        }
    )

    assert settings.is_standalone


@pytest.mark.base
def test_cache_directories_alone_keep_the_settings_standalone() -> None:
    """The cache directories only say where the local cache is; they switch no other tier on.

    Catches: a directory list being mistaken for infrastructure, as the dropped shared tier was.
    """
    settings = CacheSettings.from_environment({CacheSettings.Variables.DIRECTORIES: "/nas/hisim"})

    assert settings.is_standalone


@pytest.mark.base
@pytest.mark.parametrize(
    "name", [CacheSettings.Variables.RETIRED_DIRECTORY, CacheSettings.Variables.RETIRED_SHARED_DIRECTORY]
)
def test_a_retired_variable_is_refused_naming_it_and_its_replacement(name: str) -> None:
    """``HISIM_CACHE_DIR`` and ``HISIM_CACHE_SHARED_DIR`` fail the settings, whatever else is set.

    Both through :meth:`CacheSettings.refuse_retired_variables` directly and through
    :meth:`CacheSettings.from_environment`, which every run calls before it builds anything.

    Catches: a stale ``.env`` line being ignored, which would quietly move the cache back to the default
    directory, and a message that does not say what to set instead.
    """
    environment = {name: "/old/cache", CacheSettings.Variables.DIRECTORIES: "/new/cache"}

    for read in (CacheSettings.refuse_retired_variables, CacheSettings.from_environment):
        with pytest.raises(CacheSettingsError) as refused:
            read(environment)
        message = str(refused.value)
        assert name in message
        assert f"set {CacheSettings.Variables.DIRECTORIES} instead" in message
    CacheSettings.refuse_retired_variables({CacheSettings.Variables.DIRECTORIES: "/new/cache"})


@pytest.mark.base
class TestParseDirectories:
    """:meth:`CacheSettings.parse_directories`: the value of ``HISIM_CACHE_DIRECTORIES`` as a tuple."""

    def test_unset_is_no_directory(self) -> None:
        """``None`` -- the variable unset or empty -- gives no directory, so the default applies."""
        assert not CacheSettings.parse_directories(None)

    def test_one_entry_is_one_directory(self) -> None:
        """One entry is a single cache directory, what ``HISIM_CACHE_DIR`` used to mean."""
        assert CacheSettings.parse_directories("/data/cache") == ("/data/cache",)

    def test_the_order_is_kept_and_empty_entries_are_skipped(self) -> None:
        """Doubled and trailing separators leave no empty directory behind; the order is the read order."""
        value = os.pathsep.join(["/seed", "", "/volume", ""])

        assert CacheSettings.parse_directories(value) == ("/seed", "/volume")

    @pytest.mark.parametrize("value", [os.pathsep, os.pathsep * 3])
    def test_only_separators_are_refused_by_name(self, value: str) -> None:
        """A value that names no directory is a mistake, not a request for the default."""
        with pytest.raises(CacheSettingsError, match=f"{CacheSettings.Variables.DIRECTORIES}.*names no directory"):
            CacheSettings.parse_directories(value)

    def test_a_relative_entry_is_refused_by_name(self) -> None:
        """A relative entry would name another directory for every working directory."""
        with pytest.raises(CacheSettingsError, match=r"relative path\(s\) \['relative/second'\]"):
            CacheSettings.parse_directories(os.pathsep.join(["/abs/first", "relative/second"]))


@pytest.mark.base
class TestResolveDirectories:
    """:meth:`CacheSettings.resolve_directories`: the explicit list, else the variable's, else the default."""

    def test_neither_list_gives_the_default_directory(self) -> None:
        """No explicit list and the variable unset: the single default directory, as before the lists existed."""
        settings = CacheSettings.from_environment({})

        assert settings.resolve_directories([], "/default") == ["/default"]

    def test_the_variable_replaces_the_default_directory(self) -> None:
        """The variable's list, in its order, takes the place of the default directory."""
        settings = CacheSettings.from_environment({CacheSettings.Variables.DIRECTORIES: os.pathsep.join(["/b", "/a"])})

        assert settings.resolve_directories([], "/default") == ["/b", "/a"]

    def test_an_explicit_list_outranks_the_variable(self) -> None:
        """A list stated in code or a simulation file is a decision about this run; the environment is not read."""
        settings = CacheSettings.from_environment({CacheSettings.Variables.DIRECTORIES: "/environment"})

        assert settings.resolve_directories(["/explicit", "/second"], "/default") == ["/explicit", "/second"]
