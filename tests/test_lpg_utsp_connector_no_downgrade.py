"""The occupancy connector must run the mode it was configured with, or fail saying why.

Until roadmap/pylpg_flakiness.md F1 landed, any exception in the UTSP or local-LPG path made the
connector rewrite its own ``data_acquisition_mode`` and continue with the shipped predefined
profile -- a different household. The run then finished, exited zero and produced a full set of
plausible indicators that no downstream check could distinguish from the requested household's.
These tests pin the replacement behaviour from both ends: the exception has to reach the caller,
and the configuration object has to come back holding exactly what its author put in it.

They are deliberately cheap and marked ``base``. Neither needs a UTSP server, a network or a
working pylpg installation, because both make the profile source fail on purpose; what they check
is the connector's reaction to that failure, which is the part that used to be wrong.
"""

# clean

import os
from typing import Any

import pytest

from hisim import utils
from hisim.components import loadprofilegenerator_utsp_connector as lpg_connector
from hisim.simulationparameters import SimulationParameters

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


class UnreachableProfileSourceError(RuntimeError):
    """Marks the failure these tests inject, so the assertion can prove the original one propagated.

    A generic exception type would let a re-wrapped or a narrowed exception pass the test, which is
    precisely what F1 forbids: the original exception and its traceback must reach the caller
    untouched. A dedicated type makes ``pytest.raises`` check identity rather than plausibility.
    """


def build_connector_config(
    mode: lpg_connector.LpgDataAcquisitionMode, cache_directory: str
) -> lpg_connector.UtspLpgConnectorConfig:
    """Builds a connector configuration in the given acquisition mode, cached in a scratch directory.

    The cache directory is redirected into the test's own temporary path because a hit in the real
    ``hisim/inputs/cache`` would satisfy the request before the profile source is ever consulted,
    and these tests are entirely about what happens when it is consulted and fails.

    Args:
        mode: the acquisition mode to configure; the test then asserts it survives the run.
        cache_directory: a writable scratch directory to use as the connector's cache.

    Returns:
        UtspLpgConnectorConfig: the default configuration with the mode and cache path applied.
    """
    config = lpg_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()
    config.data_acquisition_mode = mode
    config.cache_dir_path = cache_directory
    config.result_dir_path = os.path.join(cache_directory, "results")
    return config


@pytest.mark.base
def test_local_lpg_failure_propagates_instead_of_swapping_the_household(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local-LPG failure raises the original exception and leaves the configured mode alone.

    The LPG executor is replaced with one that always raises, which is what an absent or broken
    pylpg installation looks like from the connector's side. The old code caught that, logged a
    warning, set ``USE_PREDEFINED_PROFILE`` and finished successfully; the new code must let the
    exception through with the mode untouched.
    """

    def raise_instead_of_executing(*_args: Any, **_kwargs: Any) -> Any:
        raise UnreachableProfileSourceError("pylpg is not available in this test")

    monkeypatch.setattr(lpg_connector.lpg_execution, "LPGExecutor", raise_instead_of_executing)
    config = build_connector_config(lpg_connector.LpgDataAcquisitionMode.USE_LOCAL_LPG, str(tmp_path))
    simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)

    with pytest.raises(UnreachableProfileSourceError):
        lpg_connector.UtspLpgConnector(config=config, my_simulation_parameters=simulation_parameters)

    assert config.data_acquisition_mode is lpg_connector.LpgDataAcquisitionMode.USE_LOCAL_LPG


@pytest.mark.base
def test_missing_utsp_credentials_fail_the_run_rather_than_choosing_another_source(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unconfigured UTSP is an error, not a reason to run the local LPG instead.

    ``UTSP_URL`` and ``UTSP_API_KEY`` are made unreadable, which is the state of any checkout
    without a ``.env``. The connector used to answer that by switching to the local LPG and, if
    that failed too, to the predefined profile; both hops changed the household without saying so
    anywhere a check could see.
    """

    def raise_for_any_variable(key: str, default: Any = None) -> str:
        raise UnreachableProfileSourceError(f"no .env in this test, {key} is unset (default {default})")

    monkeypatch.setattr(utils, "get_environment_variable", raise_for_any_variable)
    config = build_connector_config(lpg_connector.LpgDataAcquisitionMode.USE_UTSP, str(tmp_path))
    simulation_parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)

    with pytest.raises(UnreachableProfileSourceError):
        lpg_connector.UtspLpgConnector(config=config, my_simulation_parameters=simulation_parameters)

    assert config.data_acquisition_mode is lpg_connector.LpgDataAcquisitionMode.USE_UTSP


@pytest.mark.base
def test_the_guidance_names_the_configured_mode_and_the_way_out() -> None:
    """The message printed beside the traceback has to be worth reading, so its content is pinned.

    It exists because the failure it accompanies is a configuration failure: the reader needs the
    mode that was asked for, the credentials that mode needs, and the two alternatives. The warning
    that the predefined profile is a *different* household is the clause most easily lost in a
    later edit, and the one whose absence caused the original defect to go unnoticed for so long.
    """
    config = build_connector_config(lpg_connector.LpgDataAcquisitionMode.USE_LOCAL_LPG, os.devnull)
    connector = lpg_connector.UtspLpgConnector.__new__(lpg_connector.UtspLpgConnector)
    connector.utsp_config = config

    guidance = connector.describe_unreachable_profile_source()

    assert "USE_LOCAL_LPG" in guidance
    assert "UTSP_URL" in guidance and "UTSP_API_KEY" in guidance
    assert "DIFFERENT household" in guidance
    assert "not comparable" in guidance
