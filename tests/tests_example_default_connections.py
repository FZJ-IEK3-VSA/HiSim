import pytest
from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters


@pytest.mark.examples
def test_basic_household_with_default_connections():
    #if os.path.isdir("../hisim/inputs/cache"):
     #   shutil.rmtree("../hisim/inputs/cache")
    path = "../examples/basic_household.py"
    func = "basic_household_with_default_connections"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func,mysimpar )
