import json
import os
import ast
import inspect
import sys

currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
sys.path.insert(0, parentdir)

import cfg_automator

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

def smarthome_setup(my_sim):
    my_smart_home = cfg_automator.SmartHome()
    my_smart_home.build(my_sim)

if __name__ == '__main__':
    list_for_PV_size = [1, 5, 10, 20, 50, 100]*1000
    for index_b, pv_power in enumerate(list_for_PV_size):
           my_cfg = cfg_automator.ConfigurationGenerator()
           my_cfg.pvs["power"] = pv_power
           my_cfg.dump()
           command_line = "python hisim.py smarthome smarthome_setup"
           os.system(command_line)
