"""Tests for the household_1d advanced heat-pump setup (DHW HP, no hot-water storage).

Runs a one-day simulation of the
`household_1d_more_advanced_hp_dhw_hp_no_hot_water_storage` system setup through
`hisim_main.main` and asserts that the simulator populates a result directory,
writes a `finished.flag` marker, and emits at least one CSV result file.
"""
# clean
import pytest

from hisim import utils
from tests._helpers import run_setup_and_assert_artifacts


# @pytest.mark.system_setups
@pytest.mark.utsp
@utils.measure_execution_time
def test_basic_household() -> None:
    """Run a one-day simulation of the household_1d_more_advanced_hp_dhw_hp_no_hot_water_storage setup.

    The setup under test (path, ``SimulationParameters``, post-processing options)
    is orchestrated by :func:`tests._helpers.run_setup_and_assert_artifacts`, which
    builds a one-day profile with ``MAKE_NETWORK_CHARTS`` and ``EXPORT_TO_CSV``,
    runs the setup through ``hisim_main.main``, and verifies the
    ``result_directory`` / ``finished.flag`` / ``*.csv`` completion contract.
    """
    run_setup_and_assert_artifacts(
        "../system_setups/household_1d_more_advanced_hp_dhw_hp_no_hot_water_storage.py"
    )
