from hisim import component as cp
#import components as cps
#import components
# from hisim.components import generic_heat_pump
# from hisim.components import loadprofilegenerator_connector
# from hisim.components import weather
# from hisim.components import building
# from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
# from hisim.json_generator import JsonConfigurationGenerator
import pytest
from hisim.json_executor import JsonExecutor
from tests.test_json_generator import ExampleConfig
from hisim import utils
from hisim import hisim_main
# from hisim import log
# import os


@pytest.mark.base
@utils.measure_execution_time
def test_json_executor():
    ex = ExampleConfig()
    ex.make_example_config()

    je = JsonExecutor("cfg.json")
    je.execute_all()
