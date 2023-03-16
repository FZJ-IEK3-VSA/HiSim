from hisim import component as cp
#import components as cps
#import components
# from hisim.components import generic_heat_pump
# from hisim.components import loadprofilegenerator_connector
# from hisim.components import weather
# from hisim.components import building
# from hisim import loadtypes as lt
import pytest
from hisim.simulationparameters import SimulationParameters
# from hisim.json_generator import JsonConfigurationGenerator
from hisim.json_executor import JsonExecutor
from tests.test_json_generator import ExampleConfig
from hisim import utils
from hisim import hisim_main
# from hisim import log
# import os

@pytest.mark.examples
@utils.measure_execution_time
def test_modular_household_configurations():
    """ Tests the modular households. """
    path = "../examples/modular_example.py"
    func = "modular_household_explicit"
    mysimpar = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60 * 15)
    hisim_main.main(path, func, mysimpar)
