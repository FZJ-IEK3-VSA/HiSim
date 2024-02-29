# -*- coding: utf-8 -*-
# clean

""" Generic Electrolyzer controller with minimal runtime.

CHP is controlled by ...
"""

# Owned
from dataclasses import dataclass
from typing import List
from dataclasses_json import dataclass_json

# Generic/Built-in
from hisim import component as cp
from hisim import log, utils
from hisim.component import ConfigBase
from hisim.components import (generic_hydrogen_storage, controller_l1_example_controller_C4L_2a)
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters

__authors__ = "edited Christof Bernsteiner"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


@dataclass_json
@dataclass
class C4Lelectrolyzerfuelcell1a1bControllerConfig(ConfigBase):
    """CHP Controller Config."""

    #: name of the device
    name: str
    #: priority of the device in hierachy: the higher the number the lower the priority
    source_weight: int
    #: type of CHP: hydrogen or gas (hydrogen than considers also SOC of hydrogen storage)
    use: LoadTypes
    #: minimal state of charge of the hydrogen storage to start operating in percent (only relevant for electrolyzer):
    h2_soc_upper_threshold_electrolyzer: float
    #: minimal state of charge of the hydrogen storage to start operating in percent (only relevant for fuel cell):
    h2_soc_lower_threshold_fuelcell: float
    # SOFC or SOEF activated
    on_off_SOEC : int
    off_on_SOEC : int

    @staticmethod
    def get_default_config_electrolyzerfuelcell() -> "C4Lelectrolyzerfuelcell1a1bControllerConfig":
        """Returns default configuration for the CHP controller."""
        config = C4Lelectrolyzerfuelcell1a1bControllerConfig(
            name="Electrolyzer FuelCell Controller", source_weight=1, use=LoadTypes.GAS,  h2_soc_upper_threshold_electrolyzer=0,h2_soc_lower_threshold_fuelcell = 0 , on_off_SOEC = 0, off_on_SOEC = 0)
        return config




class C4Lelectrolyzerfuelcell1a1bControllerState:
    """Data class that saves the state of the electrolyzer controller."""
    def __init__(
        self,
        RunElectrolyzer: int,
        RunFuelCell: int,
        mode: int,
    ) -> None:
        """Initializes electrolyzer Controller state.

        :param RunElectrolyzer: 0 if turned off, 1 if running.
        :type RunElectrolyzer: int
        """
        self.RunElectrolyzer: int = RunElectrolyzer
        self.RunFuelCell: int = RunFuelCell
        self.mode: int = mode
 
    def clone(self) -> "C4Lelectrolyzerfuelcell1a1bControllerState":
        """Copies the current instance."""
        return C4Lelectrolyzerfuelcell1a1bControllerState(
            RunElectrolyzer=self.RunElectrolyzer,
            RunFuelCell= self.RunFuelCell,
            mode = self.mode,
        )

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    # def activate(self, timestep: int) -> None:
    #     """Activates the heat pump and remembers the time step."""
    #     if self.on_off == 0:
    #         self.activation_time_step = timestep
    #     self.RunElectrolyzer = 1

    # def deactivate(self, timestep: int) -> None:
    #     """Deactivates the heat pump and remembers the time step."""
    #     if self.on_off == 1:
    #         self.deactivation_time_step = timestep
    #     self.RunElectrolyzer = 0


class C4Lelectrolyzerfuelcell1a1bController(cp.Component):
    """
    """

    # Inputs
    HydrogenSOC = "HydrogenSOC"

    # Outputs
    ElectrolyzerControllerOnOffSignal = "ElectrolyzerControllerOnOffSignal"
    FuelCellControllerOnOffSignal = "FuelCellControllerOnOffSignal"
    CHPControllerHeatingModeSignal = "CHPControllerHeatingModeSignal" #Dummy Connection because without the generic_CHP.py is not working

    @utils.measure_execution_time
    def __init__(
        self, my_simulation_parameters: SimulationParameters, config: C4Lelectrolyzerfuelcell1a1bControllerConfig
    ) -> None:
        """For initializing."""
        if not config.__class__.__name__ == C4Lelectrolyzerfuelcell1a1bControllerConfig.__name__:
            raise ValueError("Wrong config class. Got a " + config.__class__.__name__)
        super().__init__(
            name=config.name + "_w" + str(config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )
        # self.minimum_runtime_in_timesteps = int(
        #     config.min_operation_time_in_seconds
        #     / self.my_simulation_parameters.seconds_per_timestep
        # )
        # self.minimum_resting_time_in_timesteps = int(
        #     config.min_idle_time_in_seconds
        #     / self.my_simulation_parameters.seconds_per_timestep
        # )

        self.state: C4Lelectrolyzerfuelcell1a1bControllerState = C4Lelectrolyzerfuelcell1a1bControllerState(0, 0, 0)
        self.previous_state: C4Lelectrolyzerfuelcell1a1bControllerState = self.state.clone()
        self.processed_state: C4Lelectrolyzerfuelcell1a1bControllerState = self.state.clone()

        # Component Outputs
        self.electrolyzer_onoff_signal_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ElectrolyzerControllerOnOffSignal,
            LoadTypes.ON_OFF,
            Units.BINARY,
            output_description="On off signal from Electrolyzer controller.",
        )
        self.fuelcell_onoff_signal_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.FuelCellControllerOnOffSignal,
            LoadTypes.ON_OFF,
            Units.BINARY,
            output_description="On off signal from Fuel Cell controller.",
        )

        self.chp_heatingmode_signal_channel: cp.ComponentOutput = self.add_output( #Dummy, because without the generic_chp.py is not working  
            self.component_name,
            self.CHPControllerHeatingModeSignal,
            LoadTypes.ANY,
            Units.BINARY,
            output_description="Heating mode signal from CHP controller.",
        )

        # Component Inputs
        self.hydrogen_soc_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HydrogenSOC,
            LoadTypes.HYDROGEN,
            Units.PERCENT,
            mandatory=False,
        )

        #Input hinsichtlich 
        self.add_default_connections(self.get_default_connections_from_h2_storage())

    def get_default_connections_from_h2_storage(self):
        """Sets default connections for the hydrogen storage."""
        log.information("setting hydrogen storage default connections in L1 CHP/Fuel Cell Controller")
        connections = []
        h2_storage_classname = generic_hydrogen_storage.GenericHydrogenStorage.get_classname()
        connections.append(
            cp.ComponentConnection(
                C4Lelectrolyzerfuelcell1a1bController.HydrogenSOC,
                h2_storage_classname,
                generic_hydrogen_storage.GenericHydrogenStorage.HydrogenSOC,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restores previous state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """For double checking results."""
        pass

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Core Simulation function."""
        if force_convergence:
            # states are saved after each timestep, outputs after each iteration
            # outputs have to be in line with states, so if convergence is forced outputs are aligned to last known state.
            self.state = self.processed_state.clone()
        else:
            
            #Electrolyzer controller
            if self.hydrogen_soc_channel.source_output is not None:
                hydrogen_soc = stsv.get_input_value(self.hydrogen_soc_channel)
                hydrogen_soc_ok = hydrogen_soc >= self.config.h2_soc_upper_threshold_electrolyzer #Is the hydrogen storage tank already filled above the upper limit?
            
            else:
                hydrogen_soc_ok = False

            if not hydrogen_soc_ok: #If the hydrogen storage tank is not filled above the upper limit, then theoretically, the electrolyzer can be turned on
                calc_electrolyzer = ((timestep >= self.config.on_off_SOEC) and (timestep <= self.config.off_on_SOEC))  #is the season of the year, where the electrolyzer is allowed to run?

                if calc_electrolyzer:
                    self.state.RunElectrolyzer = 0 #electrolyzer is not running"off" because, it is not the season, where the electrolyzer is allowed to run or there is not enough fuel cell in the tank

                else:
                    self.state.RunElectrolyzer = 1 #electrolyzer is running "on mode"
            elif hydrogen_soc_ok:
                self.state.RunElectrolyzer = 0 #electrolyzer is not running"off"


            #Fuel Cell controller
            #First looking if there is enough fuel available to run the fuel cell
            if self.hydrogen_soc_channel.source_output is not None:
                hydrogen_soc = stsv.get_input_value(self.hydrogen_soc_channel)
                hydrogen_soc_ok = hydrogen_soc > self.config.h2_soc_lower_threshold_fuelcell
            else:
                hydrogen_soc_ok = True

            if not hydrogen_soc_ok:
                self.state.RunFuelCell = 0
            else:    
                calc_fuelcell = ((timestep >= self.config.on_off_SOEC) and (timestep <= self.config.off_on_SOEC))
                if calc_fuelcell:
                    self.state.RunFuelCell = 1 #turn on
                    self.state.mode = 2 #Needs the fuel cell: then the global thermal power is calculated
                    stsv.set_output_value(self.chp_heatingmode_signal_channel, self.state.mode)
                else:
                    self.state.RunFuelCell = 0 #turn off
                    self.state.mode = 0  #if no forced running then only consider 



            self.processed_state = self.state.clone()
        
        stsv.set_output_value(self.electrolyzer_onoff_signal_channel,self.state.RunElectrolyzer) 
        stsv.set_output_value(self.fuelcell_onoff_signal_channel,self.state.RunFuelCell) 

    def write_to_report(self) -> List[str]:
        """Writes the information of the current component to the report."""
        return self.config.get_string_dict()
