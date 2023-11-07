"""Test basic household with default connections."""
import pytest
from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters


@pytest.mark.system_setups
def test_basic_household_with_default_connections():
    """Test basic household with default connections."""
    # if os.path.isdir("../hisim/inputs/cache"):
    #   shutil.rmtree("../hisim/inputs/cache")
    path = "../system_setups/basic_household.py"
    func = "setup_function"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func, mysimpar)
