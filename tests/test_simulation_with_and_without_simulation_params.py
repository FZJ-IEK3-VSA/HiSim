""" Tests for the basic household system setup with simulation params and without. """
import os
from pathlib import Path
import shutil

import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils
from tests.testing_utils import TestingUtils


BASIC_HOUSEHOLD_PATH = str(Path(__file__).resolve().parent.parent / "system_setups" / "basic_household.py")


@pytest.mark.extendedbase
@utils.measure_execution_time
def test_basic_household_with_simu_params() -> None:
    """Single day."""
    mysimpar = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60 * 60)
    mysimpar.result_directory = TestingUtils.get_result_directory()
    shutil.rmtree(mysimpar.result_directory, ignore_errors=True)
    hisim_main.main(BASIC_HOUSEHOLD_PATH, mysimpar)
    log.information(os.getcwd())


@pytest.mark.extendedbase
@utils.measure_execution_time
def test_basic_household_without_simu_params(monkeypatch: pytest.MonkeyPatch):
    """No simulation params given. HiSim is often called this way."""

    def fast_default_parameters(
        cls: type[SimulationParameters],
        year: int,
        seconds_per_timestep: int,
    ) -> SimulationParameters:
        """Return a short simulation for this test while still exercising the no-params call path."""
        mysimpar = cls.one_day_only(year=year, seconds_per_timestep=max(seconds_per_timestep, 60 * 60))
        mysimpar.result_directory = TestingUtils.get_result_directory()
        shutil.rmtree(mysimpar.result_directory, ignore_errors=True)
        return mysimpar

    monkeypatch.setattr(
        SimulationParameters,
        "full_year_with_only_plots",
        classmethod(fast_default_parameters),
    )
    mysimpar = None
    hisim_main.main(BASIC_HOUSEHOLD_PATH, mysimpar)
    log.information(os.getcwd())
