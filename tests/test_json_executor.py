from hisim import component as cp
#import components as cps
#import components
from hisim.components import generic_heat_pump
from hisim.components import loadprofilegenerator_connector
from hisim.components import weather
from hisim.components import building
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim.json_generator import JsonConfigurationGenerator
from hisim.json_executor import JsonExecutor
from  . test_json_generator import ExampleConfig

def test_json_generator():
    ex = ExampleConfig()
    ex.make_example_config()

    je = JsonExecutor("cfg.json")
    je.execute_all()
