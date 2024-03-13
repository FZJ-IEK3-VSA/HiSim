# -*- coding: utf-8 -*-
# clean

""" Generic Electrolyzer controller with minimal runtime.

CHP is controlled by ...
"""

# Owned
from dataclasses import dataclass
from typing import List
from dataclasses_json import dataclass_json
import sys
import math
# Generic/Built-in
from hisim import component as cp
from hisim import log, utils
from hisim.component import ConfigBase
from hisim.components import (generic_hydrogen_storage,advanced_battery_bslib)
from hisim.loadtypes import LoadTypes, Units, ComponentType
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
class C4LelectrolyzerfuelcellpredictiveControllerConfig(ConfigBase):
    """CHP & Electrolyzer Controller Config."""

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
    # SOFC or SOEF timestep when activated (seasonal on or off!)
    on_off_SOEC : int #SOEC = Electrolyzer is turned off  at this timestep (!) of season
    off_on_SOEC : int #SOEC = Electrolyzer is turned on at this timestep (!) of season
    #Electrolyzer consumption & fuel cell power in "Watt"
    p_el_elektrolyzer : int
    fuel_cell_power : int
    

    @staticmethod
    def get_default_config_electrolyzerfuelcell() -> "C4LelectrolyzerfuelcellpredictiveControllerConfig":
        """Returns default configuration for the CHP controller."""
        config = C4LelectrolyzerfuelcellpredictiveControllerConfig(
            name="Electrolyzer FuelCell Controller", source_weight=1, use=LoadTypes.GAS,  h2_soc_upper_threshold_electrolyzer=0,h2_soc_lower_threshold_fuelcell = 0 , on_off_SOEC = 0, off_on_SOEC = 0, p_el_elektrolyzer = 0, fuel_cell_power = 0)
        return config




class C4LelectrolyzerfuelcellpredictiveControllerState:
    """Data class that saves the state of the electrolyzer-fuelcell controller."""
    def __init__(
        self,
        RunElectrolyzer: int,
        RunFuelCell: int,
        mode: int,
        activation_time_step_electrolyzer: int,
    ) -> None:
        """Initializes electrolyzer Controller state.

        :param RunElectrolyzer: 0 if turned off, 1 if running.
        :type RunElectrolyzer: int
        """
        self.RunElectrolyzer: int = RunElectrolyzer
        self.RunFuelCell: int = RunFuelCell
        self.mode: int = mode
        self.activation_time_step_electrolyzer: int = activation_time_step_electrolyzer

    def clone(self) -> "C4LelectrolyzerfuelcellpredictiveControllerState":
        """Copies the current instance."""
        return C4LelectrolyzerfuelcellpredictiveControllerState(
            RunElectrolyzer=self.RunElectrolyzer,
            RunFuelCell= self.RunFuelCell,
            mode = self.mode,
            activation_time_step_electrolyzer=self.activation_time_step_electrolyzer,
        )

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass



class C4LelectrolyzerfuelcellpredictiveController(cp.Component):
    """
    """

    # Inputs...state of charge of different storages; Loading Input data with default conneciton logic
    HydrogenSOC = "HydrogenSOC" 
    BatteryStateOfCharge = "BatteryStateOfCharge" #Do I need for loading data with get default connection logic


    # Outputs
    ElectrolyzerControllerOnOffSignal = "ElectrolyzerControllerOnOffSignal"
    FuelCellControllerOnOffSignal = "FuelCellControllerOnOffSignal"
    CHPControllerHeatingModeSignal = "CHPControllerHeatingModeSignal" # [ ] Dummy Connection because without the generic_CHP.py is not working

    @utils.measure_execution_time
    def __init__(
        self, my_simulation_parameters: SimulationParameters, config: C4LelectrolyzerfuelcellpredictiveControllerConfig
    ) -> None:
        """For initializing."""
        if not config.__class__.__name__ == C4LelectrolyzerfuelcellpredictiveControllerConfig.__name__:
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

        self.state: C4LelectrolyzerfuelcellpredictiveControllerState = C4LelectrolyzerfuelcellpredictiveControllerState(0, 0, 0,0)
        self.previous_state: C4LelectrolyzerfuelcellpredictiveControllerState = self.state.clone()
        self.processed_state: C4LelectrolyzerfuelcellpredictiveControllerState = self.state.clone()

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
        
        self.batterystateofcharge_channel: cp.ComponentInput = self.add_input( #Do I need for loading data with get default connection logic
            self.component_name,
            self.BatteryStateOfCharge,
            LoadTypes.ANY,
            Units.ANY,
            True,
        )


        #Build input channel 
        self.add_default_connections(self.get_default_connections_from_h2_storage()) #Do I need for loading data with get default connection logic
        self.add_default_connections(self.get_default_connections_from_battery())


    #get default connections brauche ich, wenn ich im Example nur mehr mit dem Befehl "connect_only_predefined_connections" die Verbindung aufbauen möchte | also nicht mit dem Befehl im Example "connect_input"
    def get_default_connections_from_h2_storage(self):
        """Sets default connections for the hydrogen storage."""
        log.information("setting hydrogen storage default connections in electrolyzer-fuel Cell Controller")
        connections = []
        h2_storage_classname = generic_hydrogen_storage.GenericHydrogenStorage.get_classname()
        connections.append(
            cp.ComponentConnection(
                C4LelectrolyzerfuelcellpredictiveController.HydrogenSOC,
                h2_storage_classname,
                generic_hydrogen_storage.GenericHydrogenStorage.HydrogenSOC,
            )
        )
        return connections
    
    def get_default_connections_from_battery(self): #Do I need for loading data with get default connection logic
        """Sets default connections for the battery storage."""
        log.information("setting battery default connections in electrolyzer-fuel Cell Controller")
        connections = []
        battery_classname = advanced_battery_bslib.Battery.get_classname()
        connections.append(
            cp.ComponentConnection(
                C4LelectrolyzerfuelcellpredictiveController.BatteryStateOfCharge,
                battery_classname,
                advanced_battery_bslib.Battery.StateOfCharge,
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

    def activate(self, timestep: int) -> None:
        """Activates the controller."""
        self.on_off = 1
        self.activation_time_step_electrolyzer = timestep

    def deactivate(self, timestep: int) -> None:
        """Activates the controller."""
        self.on_off = 0
        self.deactivation_time_step = timestep

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Core Simulation function."""
        if force_convergence:
            # states are saved after each timestep, outputs after each iteration
            # outputs have to be in line with states, so if convergence is forced outputs are aligned to last known state.
            self.state = self.processed_state.clone()
        else:
            '''
            From Deliverable 2.1: 

            Das Szenario 2 ist ähnlich dem ersten Szenario aufgebaut, jedoch erfolgt der Betrieb nicht starr (24/7), 
            sondern variabel auf Basis von Erzeugungs- und Verbrauchsprognosen. Das Ziel der Langzeitspeicherung und 
            die damit einhergehende Unterscheidung in Sommer- und Winterbetrieb bleibt bestehen.
            Im Sommerbetrieb (SOEC) wird in der Früh anhand einer PV-Prognose sowie einer Verbrauchsprognose der 
            voraussichtlich vorhandene Überschussstrom ermittelt. Reicht dieser laut Prognose aus, um den SOEC-Betrieb 
            über die Mindestlaufzeit hinaus zu gewährleisten, wird die Einschaltentscheidung für diesen Tag getroffen. 
            Zuerst wird die Batterie mit Überschussstrom geladen (vollständig oder auf eine gewisse Mindestladung), 
            danach wird der SOEC-Betrieb gestartet und die SOEC soweit möglich direkt (ohne Batterie) mit Überschussstrom versorgt. 
            Reicht der Überschuss nicht für den Betrieb der SOEC aus wird der Rest aus der Batterie bezogen. 
            Die SOEC wird so lange betrieben, wie Überschussstrrom zur Verfügung steht bzw. bis die Batterie leer ist. 
            Ist die Mindestlaufzeit noch nicht erreicht und steht trotzdem kein Überschussstrom, weder direkt noch 
            aus der Batterie zur Verfügung, wird auf Netzstrom zurückgegriffen. Aus wirtschaftlicher Sicht soll dieser 
            Umstand jedoch minimiert werden. Es ist anzunehmen, dass in diesem Szenario sowohl die Batterie als auch 
            der Druckgasspeicher kleiner als in Szenario 1 dimensioniert werden können. Durch den intelligenten Betrieb 
            wird eine Steigerung der Wirtschaftlichkeit erhofft.

            Im Winter wird der SOFC-Betrieb aktiviert. Dieser erfolgt analog zum Szenario 1 mit dem einzigen Unterschied, 
            dass der Betrieb nicht starr (24/7) erfolgt, sondern nur dann, wenn laut Prognosen ein Strombedarf besteht.
            
            '''

            #Get Input Values from other components
            BatteryStateOfCharge = stsv.get_input_value(self.batterystateofcharge_channel) #Do I need for loading data with get default connection logic
            hydrogen_soc = stsv.get_input_value(self.hydrogen_soc_channel)


            #Get "real" values of future timestep out of real data within prediciton horizon 
            el_consumption_pred_onlyhouse = self.simulation_repository.get_dynamic_entry(ComponentType.ELECTRIC_CONSUMPTION, source_weight = 999)
            pv_prediction = self.simulation_repository.get_dynamic_entry(ComponentType.PV, source_weight = 999) 
            
            #Controll "length" of values
            if len(pv_prediction) != len(el_consumption_pred_onlyhouse): #Control, whether the prediction lists have the same length!
                print("Fehler: Die Prediction-Listen im predictive controller electrolyzer fuel cell haben unterschiedliche Längen!")
                sys.exit(1)  # Beendet das Skript mit einem Fehlercode 1

            #Decision parameter: Is fuel cell season or electrolyzer season
            electrolyzer_or_fuelcell_mode = ((timestep >= self.config.on_off_SOEC) and (timestep <= self.config.off_on_SOEC))  #is the season of the year, where the electrolyzer is allowed to run?
                    #electrolyzer_or_fuelcell_mode = False --> Electrolyzer Season
                    #electrolyzer_or_fuelcell_mode = True --> Fuel Cell Season
            
            prediction_timesteps = [6]

            if electrolyzer_or_fuelcell_mode == False: #ELECTROLYZER SEASON!!! 
                self.state.RunFuelCell = 0

                #Checking if a forecast needs to be generated; if the current time step is not suitable for prediction, please utilize the existing decisions.
                if math.fmod(timestep,24) not in prediction_timesteps:
                    #No prediction is done: Use the former states
                    print("Stop")
                    print(timestep)
                    print(self.state.activation_time_step_electrolyzer)
                    print(self.state.RunElectrolyzer)
                    print("Nothing any more")

                else:
                    #Forecast needed     


                    if hydrogen_soc >= self.config.h2_soc_upper_threshold_electrolyzer: #Is the hydrogen storage tank already filled above the upper limit?
                        self.state.RunElectrolyzer = 0 #electrolyzer is not running"off" because, there is not enough fuel cell in the tank

                    
                    else: # Hydrogen Storage is not full
                        '''Predictive control electrolyzer
                        1. step: Is there enough electricity from PV available to run electrolyzer?
                        Decision is made on following calculation:


                            No: Electrolyzer stays turned of
                            Yes: go to 2. step
                        2. step: is the battery storage full enough to run electrolyzer?
                            Yes: Run electrolyzer
                            No: Electrolyzer stays turned of 

                        
                        '''
                        pv_prediction_sum = 0
                        el_consumption_pred_onlyhouse_sum = 0
                        for arr_pv, arr_el in zip(pv_prediction, el_consumption_pred_onlyhouse):
                            pv_prediction_sum += arr_pv[0]
                            el_consumption_pred_onlyhouse_sum += arr_el[0]

                        pv_prediction_mean = pv_prediction_sum/len(pv_prediction)
                        el_consumption_pred_onlyhouse_mean = el_consumption_pred_onlyhouse_sum/len(el_consumption_pred_onlyhouse)
                        
                        
                        if  (pv_prediction_mean - el_consumption_pred_onlyhouse_mean) >= 1.1 * self.config.p_el_elektrolyzer:
                                
                            if BatteryStateOfCharge*100 > 80: #Looking, whether a battery state is reached where it is allowed that electrolyzer runs
                                #Battery is charged enough
                                self.state.RunElectrolyzer = 1 #electrolyzer is running "on mode"
                                #Save time step to be able to check minimum runtime
                                self.state.activation_time_step_electrolyzer = timestep

                                #!!!Dieser Zustand muss abgespeichert werden!!!
                            
                            else:
                                #Battery is not charged enough
                                self.state.RunElectrolyzer = 0 #electrolyzer is running "off mode"                    
                        else:
                                
                            self.state.RunElectrolyzer = 0 #electrolyzer is not running"off" because there is not enough surplus energy predicted within the prediction horizon


            else:    
                #FUEL CELL SEASON!!!         
                self.state.RunElectrolyzer = 0 #electrolyzer is not running"off" because, it is not the season, where the electrolyzer is allowed to run or there is not enough fuel cell in the tank
                '''
                Predictive Controll Fuel Cell: 
                
                # [ ] Fuel Cell --> implementation of prediciton is not done until now
                '''
                
                #Checking if a forecast needs to be generated; if the current time step is not suitable for prediction, please utilize the existing decisions.
                if math.fmod(timestep,24) not in prediction_timesteps:
                    #No prediction is done: Use the former states
                    pass
                
                else:

                        
                    #Forecast needed       
                    if hydrogen_soc > self.config.h2_soc_lower_threshold_fuelcell:
                        self.state.RunFuelCell = 1 #turn on
                        self.state.mode = 2 #Needs the fuel cell: then the global thermal power is calculated
                        stsv.set_output_value(self.chp_heatingmode_signal_channel, self.state.mode)

                    else:
                        #There is not enough Hydrogen available in storage
                        self.state.RunFuelCell = 0
                        self.state.mode = 0 #Needs the fuel cell: then the global thermal power is calculated


            



            self.processed_state = self.state.clone()
        
        stsv.set_output_value(self.electrolyzer_onoff_signal_channel,self.state.RunElectrolyzer) 
        stsv.set_output_value(self.fuelcell_onoff_signal_channel,self.state.RunFuelCell) 
        stsv.set_output_value(self.chp_heatingmode_signal_channel, self.state.mode)

    def write_to_report(self) -> List[str]:
        """Writes the information of the current component to the report."""
        return self.config.get_string_dict()
