# Import packages from standard library or the environment e.g. pandas, numpy etc.
from copy import deepcopy
from dataclasses import dataclass
from bslib import bslib as bsl

# Import modules from HiSim
from hisim.component import Component, ComponentInput, ComponentOutput, SingleTimeStepValues
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters
from typing import Optional
__authors__ = "Tjarko Tjaden, Hauke Hoops, Kai Rösken"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = "..."
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Tjarko Tjaden"
__email__ = "tjarko.tjaden@hs-emden-leer.de"
__status__ = "development"

class Battery(Component):
    """
    Simulate state of charge and realized power of a ac coupled battery
    storage system with the bslib library. Relevant simulation parameters
    are loaded within the init for a specific or generic battery type.
    """

    # Inputs
    LoadingPowerInput = "LoadingPowerInput"     # W

    # Outputs
    AcBatteryPower = "AcBatteryPower"           # W
    DcBatteryPower = "DcBatteryPower"           # W
    StateOfCharge = "StateOfCharge"             # [0..1]

    def __init__(self, my_simulation_parameters: SimulationParameters,
                system_id: str,
                p_inv_custom: float = 0,
                e_bat_custom: float = 0):
        """
        Loads the parameters of the specified battery storage.

        Parameters
        ----------
        system_id : str
            Name (system_id) of the battery storage from bslib database.
        p_inv_custom : numeric, default 0
            AC power of battery inverter. Only for system_ids of type "Generic". [kW]
        e_bat_custom : numeric, default 0
            Useable battery capacity. Only for system_ids of type "Generic". [kWh]
        """

        super().__init__(name="Battery", my_simulation_parameters=my_simulation_parameters)

        self.system_id = system_id

        self.p_inv_custom = p_inv_custom

        self.e_bat_custom = e_bat_custom

        # Component has states
        self.state = BatteryState()
        self.previous_state = deepcopy(self.state)

        # Load battery object with parameters from bslib database
        self.BAT = bsl.ACBatMod(system_id=self.system_id,
                                p_inv_custom=self.p_inv_custom,
                                e_bat_custom=self.e_bat_custom)

        # Define component inputs
        self.p_set: ComponentInput = self.add_input(object_name=self.ComponentName,
                                                   field_name=self.LoadingPowerInput,
                                                   load_type=LoadTypes.Electricity,
                                                   unit=Units.Watt,
                                                   mandatory=True)

        # Define component outputs
        self.p_bs: ComponentOutput = self.add_output(object_name=self.ComponentName,
                                                     field_name=self.AcBatteryPower,
                                                     load_type=LoadTypes.Electricity,
                                                     unit=Units.Watt)
        
        self.p_bat: ComponentOutput = self.add_output(object_name=self.ComponentName,
                                                     field_name=self.DcBatteryPower,
                                                     load_type=LoadTypes.Electricity,
                                                     unit=Units.Watt)

        self.soc: ComponentOutput = self.add_output(object_name=self.ComponentName,
                                                     field_name=self.StateOfCharge,
                                                     load_type=LoadTypes.Any,
                                                     unit=Units.Any)

    def i_save_state(self):
        self.previous_state = deepcopy(self.state)

    def i_restore_state(self):
        self.state = deepcopy(self.previous_state)

    def i_doublecheck(self, timestep: int,  stsv: SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues,  force_convergence: bool):
        
        # Parameters
        dt = self.my_simulation_parameters.seconds_per_timestep
        
        # Load input values
        p_set = stsv.get_input_value(self.p_set)
        soc = self.state.soc

        # Simulate on timestep
        results = self.BAT.simulate(p_load=p_set,
                                    soc=soc,
                                    dt=dt)
        p_bs = results[0]
        p_bat = results[1]
        soc = results[2]

        # write values for output time series
        stsv.set_output_value(self.p_bs, p_bs)
        stsv.set_output_value(self.p_bat, p_bat)
        stsv.set_output_value(self.soc, soc)

        # write values to state
        self.state.soc = soc

@dataclass
class BatteryState:
    soc: float = 0

class BatteryController(Component):
    ElectricityInput = "ElectricityInput"
    State = "State"

    def __init__(self, my_simulation_parameters: SimulationParameters ):
        super().__init__(name="BatteryController",my_simulation_parameters=my_simulation_parameters)

        self.inputC : ComponentInput = self.add_input(object_name=self.ComponentName,
                                                      field_name=self.ElectricityInput,
                                                      load_type=LoadTypes.Electricity,
                                                      unit=Units.Watt,
                                                      mandatory=True)

        self.stateC : ComponentOutput = self.add_output(object_name=self.ComponentName,
                                                        field_name=self.State,
                                                        load_type=LoadTypes.Any,
                                                        unit=Units.Any)

    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues,  force_convergence: bool):
        load = stsv.get_input_value(self.inputC)

        if load < 0.0:
            state = 1.0
        elif load > 0.0:
            state = - 1.0
        else:
            state = 0.0

        stsv.set_output_value(self.stateC, state)










