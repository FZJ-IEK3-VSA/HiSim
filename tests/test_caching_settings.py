"""Tests for :mod:`hisim.caching.settings`.

The first test pins the standalone guarantee of spec §5: with no variables set, HiSim is local-only.
The others pin how each variable is read and the one validation the settings perform.
"""

# clean

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
    assert settings.local_directory is None
    assert settings.shared_directory is None


@pytest.mark.base
def test_every_variable_is_read_from_its_documented_name() -> None:
    """Each of the six variables lands in its field, and nothing else in the environment is read.

    Catches: a field wired to the wrong variable, which would silently ignore a user's setting.
    """
    settings = CacheSettings.from_environment(
        {
            CacheSettings.Variables.URL_INTERNAL: "https://10.0.0.1/hisim-cache",
            CacheSettings.Variables.URL_EXTERNAL: "https://cache.example.org/hisim-cache",
            CacheSettings.Variables.NETWORK: "external",
            CacheSettings.Variables.API_KEY: "token",
            CacheSettings.Variables.DIRECTORY: "/somewhere/local",
            CacheSettings.Variables.SHARED_DIRECTORY: "/somewhere/shared",
            "UNRELATED": "ignored",
        }
    )

    assert settings.internal_url == "https://10.0.0.1/hisim-cache"
    assert settings.external_url == "https://cache.example.org/hisim-cache"
    assert settings.network is CacheNetworkMode.EXTERNAL
    assert settings.api_key == "token"
    assert settings.local_directory == "/somewhere/local"
    assert settings.shared_directory == "/somewhere/shared"
    assert not settings.is_standalone


@pytest.mark.base
def test_an_empty_value_is_the_same_as_an_unset_one() -> None:
    """A ``.env`` line with nothing after the equals sign does not mean "the URL is the empty string".

    Catches: an empty URL being treated as a configured endpoint and probed.
    """
    settings = CacheSettings.from_environment(
        {CacheSettings.Variables.URL_INTERNAL: "  ", CacheSettings.Variables.DIRECTORY: ""}
    )

    assert settings.internal_url is None
    assert settings.local_directory is None
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
def test_a_shared_directory_alone_makes_the_settings_non_standalone() -> None:
    """The shared-directory tier counts as infrastructure even with no network configured.

    Catches: ``is_standalone`` only looking at the network and missing the third tier.
    """
    settings = CacheSettings.from_environment({CacheSettings.Variables.SHARED_DIRECTORY: "/nas/hisim"})

    assert not settings.is_standalone


@pytest.mark.base
def test_the_local_directory_override_is_honoured_and_absent_means_the_default() -> None:
    """``HISIM_CACHE_DIR`` replaces the simulation's directory; without it the default is returned unchanged.

    Catches: the override being ignored, or an unset override returning ``None`` instead of the default.
    """
    without = CacheSettings.from_environment({})
    with_override = CacheSettings.from_environment({CacheSettings.Variables.DIRECTORY: "/override"})

    assert without.resolve_local_directory("/default") == "/default"
    assert with_override.resolve_local_directory("/default") == "/override"
