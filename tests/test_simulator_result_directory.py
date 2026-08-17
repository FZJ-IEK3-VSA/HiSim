"""Tests for how :meth:`hisim.simulator.Simulator.prepare_simulation_directory` picks a directory.

The result path lives in a process-wide singleton, which is fine for the usual one-run-per-process
invocation and was not fine for anything that runs two simulations in one process — a reference
setup followed by a variant, as the economic examples do. The second run found the first run's
directory still configured in the singleton, adopted it as "set manually", and overwrote the first
run's artifacts; the comparison that followed then compared the variant against itself.

**Error class.** A failure here loses *results*, silently and after the expensive part is done, so
it is worse than a crash. The two directions below are the whole rule: a directory a caller chose
deliberately is still honoured, and a directory a previous simulator derived for itself is not.
"""

# clean

import os

import pytest

from hisim.result_path_provider import ResultPathProviderSingleton, RunMode
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator

pytestmark = pytest.mark.base


@pytest.fixture(name="clean_path_provider")
def fixture_clean_path_provider():
    """Gives each test a fresh path-provider singleton and leaves none behind.

    The singleton is exactly the state under test, so a test that inherited it from an earlier one
    would be testing the wrong thing, and one that leaked it would break whatever runs next.
    """
    ResultPathProviderSingleton.reset()
    yield
    ResultPathProviderSingleton.reset()


def _simulator(directory: str, module_filename: str) -> Simulator:
    """A simulator for one setup module, with no result directory of its own yet.

    Nothing is imported and no component is added: `prepare_simulation_directory` only reads the
    module name, the module directory and the parameters' (empty) result directory, which is the
    whole input of the decision under test.
    """
    return Simulator(
        module_directory=directory,
        module_filename=module_filename,
        my_simulation_parameters=SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600),
    )


class TestResultDirectorySelection:
    """Which directory a run ends up in, across two runs in one process."""

    def test_a_second_run_does_not_inherit_the_first_runs_directory(self, tmp_path, clean_path_provider):
        """The defect: gas reference then heat-pump variant, both writing into the gas directory."""
        reference = _simulator(str(tmp_path), "household_gas_building_sizer")
        reference.prepare_simulation_directory()
        first_directory = reference.simulation_parameters.result_directory

        variant = _simulator(str(tmp_path), "household_heatpump_building_sizer")
        variant.prepare_simulation_directory()
        second_directory = variant.simulation_parameters.result_directory

        assert "household_gas_building_sizer" in os.path.basename(first_directory)
        assert "household_heatpump_building_sizer" in os.path.basename(second_directory)
        assert first_directory != second_directory
        assert os.path.isdir(first_directory) and os.path.isdir(second_directory)

    def test_a_deliberately_configured_directory_is_still_honoured(self, tmp_path, clean_path_provider):
        """The other direction: a test, the HPC harness or RenoVisor choosing the path still wins."""
        ResultPathProviderSingleton().configure(
            run_mode=RunMode.TEST, test_name="chosen_by_the_caller", base_path=str(tmp_path)
        )
        chosen = ResultPathProviderSingleton().get_result_directory_name()

        simulator = _simulator(str(tmp_path), "household_gas_building_sizer")
        simulator.prepare_simulation_directory()

        assert simulator.simulation_parameters.result_directory == chosen

    def test_a_preset_result_directory_is_never_touched(self, tmp_path, clean_path_provider):
        """Parameters that already name a directory bypass the provider entirely, as before."""
        parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600)
        parameters.result_directory = str(tmp_path / "explicit")
        simulator = Simulator(
            module_directory=str(tmp_path),
            module_filename="household_gas_building_sizer",
            my_simulation_parameters=parameters,
        )
        simulator.prepare_simulation_directory()

        assert simulator.simulation_parameters.result_directory == str(tmp_path / "explicit")
