import os

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters
import shutil
def test_basic_household():
    if os.path.isdir("../hisim/inputs/cache"):
        shutil.rmtree("../hisim/inputs/cache")
    path = "../examples/basic_household.py"
    func = "basic_household_explicit"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func,mysimpar )
    print(os.getcwd())

def test_basic_household_boiler():
    path = "../examples/basic_household_boiler.py"
    func = "basic_household_boiler_explicit"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func, mysimpar)

def test_basic_household_districtheating():
    path = "../examples/basic_household_Districtheating.py"
    func = "basic_household_Districtheating_explicit"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func, mysimpar)

def test_basic_household_oilheater():
    path = "../examples/basic_household_Oilheater.py"
    func = "basic_household_Oilheater_explicit"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func, mysimpar)

def test_first_example():
    path = "../examples/examples.py"
    func = "first_example"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func, mysimpar)

def test_second_example():
    path = "../examples/examples.py"
    func = "second_example"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
    hisim_main.main(path, func, mysimpar)
