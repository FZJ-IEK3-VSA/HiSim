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
from hisim.components import (generic_hydrogen_storage,advanced_battery_bslib,csvloader_electricityconsumption, csvloader_photovoltaic)
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
    #: minimal state of charge of the hydrogen storage to start operating in percent (only relevant for electro Surplus_electrical_power_related_to_elecrolzyer_percentagelyzer):
    h2_soc_upper_threshold_electrolyzer: float
    #: minimal state of charge of the hydrogen storage to start operating in percent (only relevant for fuel cell):
    h2_soc_lower_threshold_fuelcell: float
    # SOFC or SOEF timestep when activated (seasonal on or off!)
    on_off_SOEC : int #SOEC = Electrolyzer is turned off  at this timestep (!) of season
    off_on_SOEC : int #SOEC = Electrolyzer is turned on at this timestep (!) of season
    #Electrolyzer consumption & fuel cell power in "Watt"
    p_el_elektrolyzer : int
    fuel_cell_power : int
    #Runtime, Standbytime
    minstandbytime_electrolyzer: int #in seconds
    minruntime_electrolyzer: int #in seconds

    minstandbytime_fuelcell: int #in seconds
    minruntime_fuelcell: int #in seconds

    #Battery depending parameters
    minbatterystateofcharge_electrolyzer_turnon: int #min state of charge battery, to turn on electrolyzer, in %
    minbatterystateofcharge_let_electrolyzer_staysturnedon: int #min state of charg of battery, which is necessary, that electrolyzer stays turned on, in % 

    maxbatterystateofcharge_fuelcell_turnon: int #max state of charge battery, to turn on fuel cell; above this state of charge, fuel cell will not be turned on, in %
    maxbatterystateofcharge_let_fuelcell_staysturnedon: int #max state of charge battery, to let fuel cell stay turned on; above this state of charge, it will be checked if a running fuel cell makes sense (if the battery is too full, it can not pick up surplus electricity of fuel cell)

    #Surpluse Energy power and Energy Amount relation Depending parameters to turn on electrolyzer
    Electrical_power_surplus_related_to_electrolzyer_percentage: int #Faktor in % which represents the ratio  between [PV-HouseConsumption]/Electrolzyer (each in Watt)...--> 100 % means, that the surpluse energy (PV-houseconsumption) is equivalent to electrolyzer and electrolyzer could be turned on if the next steps in decision tree will be positive
    Surplus_electrical_amount_related_to_electrolzyer_in_minimum_standby_time_in_percentage: int #Factor in % which represents the same as Electrical_power_surplus_related_to_electrolzyer_percentage BUT for minimum standby time horizon
    Surplus_electrical_amount_related_to_electrolzyer_in_prediction_horizon_in_percentage: int #Please consider description in input_variablen generation AND at the predictive controller

    # Energy power Demand and Energy Amount Demand relation Depending parameters to turn on electrolyzer
    Electrical_power_demand_related_to_fuelcell_percentage: int #Faktor in % which represents the ratio  between [HouseConsumption-PV]/FuelCell (each in Watt)...--> 100 % means, that the  energy demand (houseconsumption-PV) is equivalent to fuel cell output
    Usebale_electrical_amount_of_fuelcell_related_to_fuelcell_output_in_prediction_horizon_in_percentage:int #Please consider description in input_variablen generation AND at the predictive controller
    Usebale_electrical_amount_of_fuelcell_related_to_fuelcell_output_in_minimum_standby_time_in_percentage: int #Please consider description in input_variablen generation AND at the predictive controller

    @staticmethod
    def get_default_config_electrolyzerfuelcell() -> "C4LelectrolyzerfuelcellpredictiveControllerConfig":
        """Returns default configuration for the CHP controller."""
        config = C4LelectrolyzerfuelcellpredictiveControllerConfig(                                                                                                                                                                                                                                                                                          
            name="Electrolyzer FuelCell Controller", source_weight=1, use=LoadTypes.GAS,  h2_soc_upper_threshold_electrolyzer=0,h2_soc_lower_threshold_fuelcell = 0 , on_off_SOEC = 0, off_on_SOEC = 0, p_el_elektrolyzer = 0, fuel_cell_power = 0,minstandbytime_electrolyzer=0, minruntime_electrolyzer = 0,minstandbytime_fuelcell = 0,minruntime_fuelcell = 0,minbatterystateofcharge_electrolyzer_turnon=0, maxbatterystateofcharge_fuelcell_turnon = 0, Electrical_power_surplus_related_to_electrolzyer_percentage = 0, Surplus_electrical_amount_related_to_electrolzyer_in_prediction_horizon_in_percentage = 0, Usebale_electrical_amount_of_fuelcell_related_to_fuelcell_output_in_prediction_horizon_in_percentage= 0,minbatterystateofcharge_let_electrolyzer_staysturnedon = 0, maxbatterystateofcharge_let_fuelcell_staysturnedon = 0, Surplus_electrical_amount_related_to_electrolzyer_in_minimum_standby_time_in_percentage = 0, Electrical_power_demand_related_to_fuelcell_percentage = 0, Usebale_electrical_amount_of_fuelcell_related_to_fuelcell_output_in_minimum_standby_time_in_percentage = 0)
        return config




class C4LelectrolyzerfuelcellpredictiveControllerState:
    """Data class that saves the state of the electrolyzer-fuelcell controller."""
    def __init__(
        self,
        RunElectrolyzer: int,
        RunFuelCell: int,
        mode: int,
        activation_timestep_electrolyzer: int,
        deactivation_timestep_electrolyzer: int,
        activation_timestep_fuelcell: int,
        deactivation_timestep_fuelcell: int,
    ) -> None:
        """Initializes electrolyzer Controller state.

        :param RunElectrolyzer: 0 if turned off, 1 if running.
        :type RunElectrolyzer: int
        """
        self.RunElectrolyzer: int = RunElectrolyzer
        self.RunFuelCell: int = RunFuelCell
        self.mode: int = mode
        
        self.activation_timestep_electrolyzer: int = activation_timestep_electrolyzer
        self.deactivation_timestep_electrolyzer: int = deactivation_timestep_electrolyzer
        
        self.activation_timestep_fuelcell: int = activation_timestep_fuelcell
        self.deactivation_timestep_fuelcell: int = deactivation_timestep_fuelcell


    def clone(self) -> "C4LelectrolyzerfuelcellpredictiveControllerState":
        """Copies the current instance."""
        return C4LelectrolyzerfuelcellpredictiveControllerState(
            RunElectrolyzer=self.RunElectrolyzer,
            RunFuelCell= self.RunFuelCell,
            mode = self.mode,
            activation_timestep_electrolyzer=self.activation_timestep_electrolyzer,
            deactivation_timestep_electrolyzer=self.deactivation_timestep_electrolyzer,
            
            activation_timestep_fuelcell=self.activation_timestep_fuelcell,
            deactivation_timestep_fuelcell=self.deactivation_timestep_fuelcell,
        )

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass



class C4LelectrolyzerfuelcellpredictiveController(cp.Component):
    """
    """

    # Inputs...state of charge of different storages; Loading Input data with default conneciton logic
    HydrogenSOC = "HydrogenSOC" 
    BatteryStateOfCharge = "BatteryStateOfCharge" #Do I need for loading data with get default connection logic = "Battery State of Charge 0...1" # [0..1] State of charge
    General_ElectricityConsumptiom = "CSV Profile Electricity Consumption Input"  #W
    General_PhotovoltaicDelivery = "CSV Profile Photovoltaic Electricity Delivery Input" #W


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

        self.state: C4LelectrolyzerfuelcellpredictiveControllerState = C4LelectrolyzerfuelcellpredictiveControllerState(0, 0, 0,0,0,0,0)
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
        
        #csv Input Electricity Consumption
        self.General_ElectricityConsumptiomInput: ComponentInput = self.add_input(
            self.component_name,
            self.General_ElectricityConsumptiom,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            True,
        )

        #Photovoltaik Delivery
        self.General_PhotovoltaicDeliveryInput: ComponentInput = self.add_input(
            self.component_name,
            self.General_PhotovoltaicDelivery,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            True,
        )


        #Build input channel 
        self.add_default_connections(self.get_default_connections_from_h2_storage()) #Do I need for loading data with get default connection logic
        self.add_default_connections(self.get_default_connections_from_battery())
        self.add_default_connections(self.get_default_connections_from_general_electricity_consumption())
        self.add_default_connections(self.get_default_connections_from_general_photovoltaic_delivery())

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

    def get_default_connections_from_general_electricity_consumption(self):
        """Sets default connections for the csv file data electricity consumption in the energy management system."""
        log.information("setting csv file data electricity consumption default connections in Electrolyzer Controller")
        connections = []
        general_electricity_consumption_classname = csvloader_electricityconsumption.CSVLoader_electricityconsumption.get_classname()
        connections.append(
            cp.ComponentConnection(
                C4LelectrolyzerfuelcellpredictiveController.General_ElectricityConsumptiom,
                general_electricity_consumption_classname,
                csvloader_electricityconsumption.CSVLoader_electricityconsumption.Output1,
            )
        )
        return connections
    
    def get_default_connections_from_general_photovoltaic_delivery(self):
        """Sets default connections for the csv file data photovoltaic delivery  in the energy management system."""
        log.information("setting csv photovoltaic delivery default connections in Electrolyzer Controller")
        connections = []
        general_photovoltaic_delivery_classname = csvloader_photovoltaic.CSVLoader_photovoltaic.get_classname()
        connections.append(
            cp.ComponentConnection(
                C4LelectrolyzerfuelcellpredictiveController.General_PhotovoltaicDelivery,
                general_photovoltaic_delivery_classname,
                csvloader_photovoltaic.CSVLoader_photovoltaic.Output1,
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
        self.activation_timestep_electrolyzer = timestep

    def deactivate(self, timestep: int) -> None:
        """Activates the controller."""
        self.on_off = 0
        self.deactivation_timestep_electrolyzer = timestep
    
    def turn_off_procedure_electrolyzer(self, timestep, pv_prediction_watt, el_consumption_pred_onlyhouse_watt, BatteryStateOfCharge, General_PhotovoltaicDelivery, General_ElectricityConsumptiom, hydrogen_soc):
        ''' Turn off procedure started because Electrolyzer is already running
        This function manipluates 
            self.state.RunElectrolyzer
            self.state.deactivation_timestep_electrolyzer

        ****    
        1. Question: Is the minimum runtime of the electrolyzer reached? 
        '''
        runtimeelectrolyzer = (timestep-self.state.activation_timestep_electrolyzer)*self.my_simulation_parameters.seconds_per_timestep
        if runtimeelectrolyzer>= self.config.minruntime_electrolyzer:
            '''Yes, minimum runtime of electrolyzer is reached
            
            Is the Minimum state of charge of battery already reached to turn off Electrolyzer?
            '''
            if BatteryStateOfCharge*100 > self.config.minbatterystateofcharge_let_electrolyzer_staysturnedon:
                '''
                Electrolyzer stays turned on because minimum state of charge of battery is not reached
                '''
                self.state.RunElectrolyzer = 1
            else:
                '''
                Minimum state of charge of battery is reached so go further in turn off Electrolyzer procedure

                Is actual less surplus electricity power available? 
                Surplus_energy(ti) = pv(ti) - electricityconsumption_house(ti) (in Watt)
                '''
                if 100*(General_PhotovoltaicDelivery-General_ElectricityConsumptiom)/self.config.p_el_elektrolyzer< self.config.Electrical_power_surplus_related_to_electrolzyer_percentage:    
                    '''
                    Minimum actual electricity power threshold to turn on electrolyzer IS NOT met any more,

                    But is there enoug energy in future (minimum standbytime)  available?
                    
                    
                    Next Steps:
                        1.) Calculation of surplus Energy in Each timestep for the prediction horizon
                            >> Surplus_energy(ti) = pv(ti) - electricityconsumption_house(ti) (in Watt)

                        2.) Then, in each timestep calculation of useable part of surplus energy for electrolyzer:
                        Only surplus energy from 0 up to maximum electrolyzer will be considered (= useable part of surplus energy). 
                        Surplus energy which exceeds electrolyzer consumption will not be considered in decision process
                            >> Useable_Surplus_energy(ti) = from Surplus_energy(ti) consider only values between 0 up to electrolyzer consumption (in Watt)
                        
                        3.) Calculation of the useable energy quantity in Wh via prediction horizon:
                            >> total_useable_surplus_energy_in_prediction_horizon_Wh= sum of each Useable_Surplus_energy(ti)*delta_timestep in Wh

                        4.) Calculation of Electrolyzer Energy Consumption in Wh, if the Electrolyzer would RUN active in the minimum standby time horizon. Until now, the
                        electrolyzer runs stationary (no variation in electricity power demand and hydrogen output)
                            >> total_electrolyzer_energy_consumption_in_prediction_horizon_Wh = electrcitiy_consumption * delta_minimum_standby_time_Electrolyzer in Wh

                        5.) Calculation of total_useable_surplus_energy_in_prediction_horizon_Wh/total_electrolyzer_energy_consumption_in_prediction_horizon_Wh
                            Based on a defined threshold of this ratio, decision to turn off or let it stay turned on electrolyzer
                    '''

                    #Step 0: How many time steps do I have to consider from prediction horizon, because I just want to consider the minimum standby time of the electrolyzer
                    
                    seconds_per_timestep=self.my_simulation_parameters.seconds_per_timestep
                    timesteps_to_consider = self.config.minstandbytime_electrolyzer / seconds_per_timestep
                    timesteps_to_consider = int(round(timesteps_to_consider,0))
                    #Step 1
                    surplus_energy_values_for_each_timestep = []
                    for pv, el_consumption in zip(pv_prediction_watt, el_consumption_pred_onlyhouse_watt):
                        surplus_energy_values_for_each_timestep.append(pv - el_consumption)

                    #Step 2                             
                    useable_surplus_energy_values_for_each_timestep = [max(0, min(value, self.config.p_el_elektrolyzer)) for value in surplus_energy_values_for_each_timestep]
                    useable_surplus_energy_values_for_each_timestep = useable_surplus_energy_values_for_each_timestep[:timesteps_to_consider]
                    
                    #Step3
                    useable_surplus_energy_values_for_each_timestep_in_wh = [value * seconds_per_timestep / 3600 for value in useable_surplus_energy_values_for_each_timestep]  # Umrechnung in Wh
                    total_useable_surplus_energy_in_minstandbytime_Wh = sum(useable_surplus_energy_values_for_each_timestep_in_wh)
                    
                    #Step 4
                    total_electrolyzer_energy_consumption_in_minstandbytime_Wh = self.config.p_el_elektrolyzer*seconds_per_timestep*timesteps_to_consider/3600
                    
                    
                    #Step 5
                    ratio_inpercentage = 0
                    ratio_inpercentage = total_useable_surplus_energy_in_minstandbytime_Wh/total_electrolyzer_energy_consumption_in_minstandbytime_Wh*100
                    
                    if ratio_inpercentage >= self.config.Surplus_electrical_amount_related_to_electrolzyer_in_minimum_standby_time_in_percentage:
                        '''There is enoug useable electricity amount in the prediction horizon, which is min stand by time in this case, to run the electrolyzer, so turn on if there is not to much hydrogen in the tank'''
                        
                        if hydrogen_soc > self.config.h2_soc_upper_threshold_electrolyzer: #Is the hydrogen storage tank already filled above the upper limit?
                            '''Hydrogen tank is already filled up!'''
                            self.state.RunElectrolyzer = 99 #electrolyzer is not running"off" because, there is not enough fuel cell in the tank
                            print('Hydrogen tank is already full')
                            self.state.RunElectrolyzer = 99
                            self.state.deactivation_timestep_electrolyzer = timestep   
                            sys.exit()
                            
                        else:
                            '''
                            Start Electrolyzer and save the timestep when electrolyzer is started!
                            '''
                            self.state.RunElectrolyzer = 1 #electrolyzer is running "on mode"


                    else:
                        #Elektrolyseur wird in den Standby geschalten
                        '''
                        There is NOT enoug useable electricity amount in the prediction horizon/minimum stand by time in this case, 
                        to run the electrolyzer

                        So turn Electrolyzer in standby mode and save deactivation timestep!
                        '''
                        self.state.deactivation_timestep_electrolyzer = timestep  
                        self.state.RunElectrolyzer = 99 

                else:
                    
                    '''
                    Actual, the electricity thershold to turn on electrolyzer or to let it turned on is reacehd
                    So Electrolyzer stays turned on!
                    '''
                    self.state.RunElectrolyzer = 1


        else:
            self.state.RunElectrolyzer = 1
    

    def turn_on_procedure_electrolyzer(self, timestep, pv_prediction_watt, el_consumption_pred_onlyhouse_watt, BatteryStateOfCharge, General_PhotovoltaicDelivery, General_ElectricityConsumptiom, hydrogen_soc):
        # #Turn On Procedure    
        
        '''
        The following turn_off_procedure function manipulates the 
        self.state.RunElectrolyzer and 
        self.state.activation_timestep_electrolyzer

        Nex step:
        Minimum standby time of electrolyzer has already been met ?
        '''
        standbytime = (timestep-self.state.deactivation_timestep_electrolyzer)*self.my_simulation_parameters.seconds_per_timestep
        if standbytime >= self.config.minstandbytime_electrolyzer:
            '''
            Yes:
            is the Minimum standby time of electrolyzer met
            '''
            
            '''
            Next Step:
            Is the Battery state of charge threshold met, which is necessary to reach, to turn on electrolyzer?
            '''

            if BatteryStateOfCharge*100 >= self.config.minbatterystateofcharge_electrolyzer_turnon:
                '''
                Yes, battery state of charge threshold is reached which is necessary, to turn on electrolyzer
                
                Next Step:
                    Is at the actual timestep enough Electricity suprluse energy available, that a prediction of surpluse energy amount makes sense?
                    surpluse energy at timestep = pv electricity - electricity consumption of house (in Watt AND not in kWh)

                    That surplus energy is related to electricity consumption of electrolyzer
                
                '''
                
                if 100*(General_PhotovoltaicDelivery-General_ElectricityConsumptiom)/self.config.p_el_elektrolyzer>= self.config.Electrical_power_surplus_related_to_electrolzyer_percentage:    
                    '''
                    Minimum actual electricity power threshold to turn on electrolyzer has been met
                    ''' 

                    '''
                        Next Step:
                        1.) Calculation of surplus Energy in Each timestep within the prediction horizon 
                            >> Surplus_energy(ti) = pv(ti) - electricityconsumption_house(ti) (in Watt)

                        2.) Then, in each timestep calculation of useable part of surplus energy for electrolyzer:
                        Only surplus energy from 0 up to maximum electrolyzer will be considered (= useable part of surplus energy). 
                        Surplus energy which exceeds electrolyzer consumption will not be considered in decision process
                            >> Useable_Surplus_energy(ti) = from Surplus_energy(ti) consider only values between 0 up to electrolyzer consumption (in Watt)
                        
                        3.) Calculation of the useable energy quantity in Wh via prediction horizon:
                            >> total_useable_surplus_energy_in_prediction_horizon_Wh= sum of each Useable_Surplus_energy(ti)*delta_timestep in Wh

                        4.) Calculation of Electrolyzer Energy Consumption in Wh for the prediciton horizon. Until now, the
                        electrolyzer runs stationary (no variation in electricity power demand and hydrogen output)
                            >> total_electrolyzer_energy_consumption_in_prediction_horizon_Wh = electrcitiy_consumption * delta_time_prediction_horizon in Wh

                        5.) Calculation of total_useable_surplus_energy_in_prediction_horizon_Wh/total_electrolyzer_energy_consumption_in_prediction_horizon_Wh
                            Based on a defined threshold of this ratio, decision to turn on or off electrolyzer
                    '''
                    #Step 1
                    surplus_energy_values_for_each_timestep = []
                    for pv, el_consumption in zip(pv_prediction_watt, el_consumption_pred_onlyhouse_watt):
                        surplus_energy_values_for_each_timestep.append(pv - el_consumption)

                    #Step 2                             
                    useable_surplus_energy_values_for_each_timestep = [max(0, min(value, self.config.p_el_elektrolyzer)) for value in surplus_energy_values_for_each_timestep]
                    
                    #Step3
                    seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
                    useable_surplus_energy_values_for_each_timestep_in_wh = [value * seconds_per_timestep / 3600 for value in useable_surplus_energy_values_for_each_timestep]  # Umrechnung in Wh
                    total_useable_surplus_energy_in_prediction_horizon_Wh = sum(useable_surplus_energy_values_for_each_timestep_in_wh)
                    
                    #Step 4
                    total_electrolyzer_energy_consumption_in_prediction_horizon_Wh = self.config.p_el_elektrolyzer*self.my_simulation_parameters.prediction_horizon/3600 
                    #Step 5
                    ratio_inpercentage = 0
                    ratio_inpercentage = total_useable_surplus_energy_in_prediction_horizon_Wh/total_electrolyzer_energy_consumption_in_prediction_horizon_Wh*100
                    
                    if ratio_inpercentage >= self.config.Surplus_electrical_amount_related_to_electrolzyer_in_prediction_horizon_in_percentage:
                        '''There is enoug useable electricity amount in the prediction horizon, to run the electrolyzer, so turn on if there is not to much hydrogen in the tank'''
                        
                        if hydrogen_soc > self.config.h2_soc_upper_threshold_electrolyzer: #Is the hydrogen storage tank already filled above the upper limit?
                            '''Hydrogen tank is already filled up!'''
                            self.state.RunElectrolyzer = 0 #electrolyzer is not running"off" because, there is enough fuel cell in the tank
                            print('Hydrogen tank is already full')
                            self.state.RunElectrolyzer = 0 
                            sys.exit()
                            
                        else:
                            '''
                            Start Electrolyzer and save the timestep when electrolyzer is started!
                            '''
                            self.state.RunElectrolyzer = 1 #electrolyzer is running "on mode"
                            self.state.activation_timestep_electrolyzer = timestep  


        
        
                    else:
                        #Elektrolyseur läuft weiterhin auf Standby
                        self.state.RunElectrolyzer = 99 


                else:
                    #Elektrolyseur läuft weiterhin auf Standby
                    self.state.RunElectrolyzer = 99 
                    

            else: 
                #Elektrolyseur läuft weiterhin auf Standby
                self.state.RunElectrolyzer = 99
                

        else:
            #Elektrolyseur läuft weiterhin auf Standby
            self.state.RunElectrolyzer = 99

    def turn_off_procedure_fuelcell(self, timestep, pv_prediction_watt, el_consumption_pred_onlyhouse_watt, BatteryStateOfCharge, General_PhotovoltaicDelivery, General_ElectricityConsumptiom, hydrogen_soc):
        ''' Turn off procedure started because Fuel Cell is already running
        This function manipluates 
            self.state.RunFuelCell
            self.state.deactivation_timestep_electrolyzer

        ****    
        1. Question: Is the minimum runtime of the electrolyzer reached? 
        '''
        runtimefuelcell = (timestep-self.state.activation_timestep_fuelcell)*self.my_simulation_parameters.seconds_per_timestep
        if runtimefuelcell>= self.config.minruntime_fuelcell:
            '''Yes, minimum runtime of fuel cell is reached
            
            Is the Minimum state of charge of battery already reached to turn off Electrolyzer?
            '''
            if BatteryStateOfCharge*100 < self.config.maxbatterystateofcharge_let_fuelcell_staysturnedon: # [ ]
                '''
                Fuel Cell stays turned on because battery state of charge threshold to turn off fuel cell is not reached
                '''
                self.state.RunFuelCell = 1
                self.state.mode = 2 #Needs the fuel cell: then the global thermal power is calculated; 0 means that global thermal power fuel cell is NOT calculated, 2 means, that the global thermal power is calculated

            else:
                '''
                Upper battery state of charge threshold is reached so go further in turn off Fuel Cell procedure

                Is actual the ratio betwenn energy demand to fuel cell output less or greater than a factor? 
                
                energy_demand(ti) = electricityconsumption_house(ti) -pv(ti)(in Watt)
                
                '''
                if 100*(General_ElectricityConsumptiom-General_PhotovoltaicDelivery)/self.config.fuel_cell_power > self.config.Electrical_power_demand_related_to_fuelcell_percentage:    
                    '''
                    Actual, the ratio energy demand to fuel cell output is greater than a certain factor --> so the energy demand exceeds a certain threshold which means
                    that the fuel cell keeps running
                    '''
                    self.state.RunFuelCell = 1
                    self.state.mode = 2 #Needs the fuel cell: then the global thermal power is calculated; 0 means that global thermal power fuel cell is NOT calculated, 2 means, that the global thermal power is calculated

                else:
                    '''
                    Minimum actual electricity power demand threshold to let fuel cell turned on IS NOT met any more,

                    But is the  energy demand in future (within minimum standbytime)  high enough, to let fuel cell keep running?
                    
                    
                    Next Steps:
                        1.) Calculation of surplus Energy in Each timestep for the prediction horizon
                            >> Surplus_energy(ti) = pv(ti) - electricityconsumption_house(ti) (in Watt)

                        2.) Then, in each timestep calculation of useable part of surplus energy for electrolyzer:
                        Only surplus energy from 0 up to maximum electrolyzer will be considered (= useable part of surplus energy). 
                        Surplus energy which exceeds electrolyzer consumption will not be considered in decision process
                            >> Useable_Surplus_energy(ti) = from Surplus_energy(ti) consider only values between 0 up to electrolyzer consumption (in Watt)
                        
                        3.) Calculation of the useable energy quantity in Wh via prediction horizon:
                            >> total_useable_surplus_energy_in_prediction_horizon_Wh= sum of each Useable_Surplus_energy(ti)*delta_timestep in Wh

                        4.) Calculation of Electrolyzer Energy Consumption in Wh, if the Electrolyzer would RUN active in the minimum standby time horizon. Until now, the
                        electrolyzer runs stationary (no variation in electricity power demand and hydrogen output)
                            >> total_electrolyzer_energy_consumption_in_prediction_horizon_Wh = electrcitiy_consumption * delta_minimum_standby_time_Electrolyzer in Wh

                        5.) Calculation of total_useable_surplus_energy_in_prediction_horizon_Wh/total_electrolyzer_energy_consumption_in_prediction_horizon_Wh
                            Based on a defined threshold of this ratio, decision to turn off or let it stay turned on electrolyzer
                    '''

                    #Step 0: How many time steps do I have to consider from prediction horizon, because I just want to consider the minimum standby time of the fuel cell
                    
                    seconds_per_timestep=self.my_simulation_parameters.seconds_per_timestep
                    timesteps_to_consider = self.config.minstandbytime_fuelcell / seconds_per_timestep
                    timesteps_to_consider = int(round(timesteps_to_consider,0))
                    #Step 1
                    
                    energy_demand_values_for_each_timestep = []
                    for pv, el_consumption in zip(pv_prediction_watt, el_consumption_pred_onlyhouse_watt):
                        energy_demand_values_for_each_timestep.append(el_consumption-pv)

                    #Step 2 
                    useable_fuelcell_energy_values_for_each_timestep = [max(0, min(value, self.config.fuel_cell_power)) for value in energy_demand_values_for_each_timestep]
                    useable_fuelcell_energy_values_for_each_timestep = useable_fuelcell_energy_values_for_each_timestep[:timesteps_to_consider]
                    
                    #Step3
                    useable_fuelcell_energy_values_for_each_timestep_in_wh = [value * seconds_per_timestep / 3600 for value in useable_fuelcell_energy_values_for_each_timestep]  # Umrechnung in Wh
                    total_useable_fuelcell_energy_amount_in_minimum_standbytime_fuelcell_wh = sum(useable_fuelcell_energy_values_for_each_timestep_in_wh)
                    
                    #Step 4
                    total_fuelcell_energy_output_amount_in_minimum_standbytime_fuelcell_Wh = self.config.fuel_cell_power*seconds_per_timestep*timesteps_to_consider/3600
                    
                    
                    #Step 5
                    ratio_in_percentage_useableenergyfromfuelcell = 0
                    ratio_in_percentage_useableenergyfromfuelcell = total_useable_fuelcell_energy_amount_in_minimum_standbytime_fuelcell_wh/total_fuelcell_energy_output_amount_in_minimum_standbytime_fuelcell_Wh*100
                    
                    if ratio_in_percentage_useableenergyfromfuelcell >= self.config.Usebale_electrical_amount_of_fuelcell_related_to_fuelcell_output_in_minimum_standby_time_in_percentage:
                        '''There is enough electricity demand amount in the minimum standby time of fuel cell, 
                        which means, that the fuel cell should keep running
                        '''
                        self.state.RunFuelCell = 1
                        self.state.mode = 2 #Needs the fuel cell: then the global thermal power is calculated; 0 means that global thermal power fuel cell is NOT calculated, 2 means, that the global thermal power is calculated



                    else:
                        #Fuel Cell wird in Standby geschalten
                        '''
                        There is NOT enough electricity demand amount in the minimum standby time of fuel cell, 
                        which means, that the fuel cell will be turned off
                        Please rember deactivation timestep of fuel cell
                
                        '''
                        self.state.deactivation_timestep_fuelcell = timestep  
                        self.state.RunFuelCell = 99 
                        self.state.mode = 0 #Needs the fuel cell: then the global thermal power is calculated; 0 means that global thermal power fuel cell is NOT calculated, 2 means, that the global thermal power is calculated





        else:
            #Fuel Cell should keep running because minimum runtime of fuel cell is NOT reached

            self.state.RunFuelCell = 1
            self.state.mode = 2 #Needs the fuel cell: then the global thermal power is calculated; 0 means that global thermal power fuel cell is NOT calculated, 2 means, that the global thermal power is calculated

    
    def turn_on_procedure_fuelcell(self, timestep, pv_prediction_watt, el_consumption_pred_onlyhouse_watt, BatteryStateOfCharge, General_PhotovoltaicDelivery, General_ElectricityConsumptiom, hydrogen_soc):
        ##Turn On Procedure    
        
        '''
        The following turn_off_procedure function manipulates the 
        self.state.RunFuelCell and 
        self.state.activation_timestep_fuelcell

        Nex step:
        Minimum standby time of fuel cell has already been met ?
        '''
        standbytime = (timestep-self.state.deactivation_timestep_fuelcell)*self.my_simulation_parameters.seconds_per_timestep
        if standbytime >= self.config.minstandbytime_fuelcell:
            '''
            Yes:
            is the Minimum standby time of fuel cell met
            '''
            
            '''
            Next Step:
            Is the actuall battery state of charge lower a certain threshold? If the threshold is exceeded, then the fuell cell will not be turned on!
            '''

            if BatteryStateOfCharge*100 <= self.config.maxbatterystateofcharge_fuelcell_turnon:
                '''
                Yes, actual battery state of charge is lower than the certrain battery state of charge threshold. The fuel cell can be turned on 
                
                Next Step:
                    Is there any electricity demand at the actual timestep, that a prediction of  energy amount demand makes sense?
                    energy demand at timestep =  electricity consumption of house - pv electricity (in Watt AND NOT in kWh)

                    That energy demand is related to electricity output of fuel cell (ratio!)
                
                '''
                                                                                                                            
                if 100*(General_ElectricityConsumptiom-General_PhotovoltaicDelivery)/self.config.fuel_cell_power>= self.config.Electrical_power_demand_related_to_fuelcell_percentage:    
                    '''
                    Minimum actual electricity demand threshold to turn on fuel cell has been met
                    ''' 

                    '''
                        Next Step:
                        1.) Calculation of surplus Energy in Each timestep within the prediction horizon 
                            >> energy_demand(ti) =  electricityconsumption_house(ti) - pv(ti) (in Watt)

                        2.) Then, in each timestep calculation of useable part of energy for fuel cell:
                        Only fuel cell energy from 0 up to maximum energy demand will be considered (= useable part of fuel cell energy): 
                        Energy demand which is above that what fuel cell can deliver: Will not be considered because full cell can not cover that energy demand
                        
                            >> useable_fuelcell_energy_values_for_each_timestep(ti) = from fuel cell output only consider up to min(energy_demand(ti), fuel cell output) 
                        
                        3.) Calculation of the useable energy quantity of fuel cell in Wh via prediction horizon:
                            >> total_fuelcell_energy_output_in_prediction_horizon_Wh= sum of each useable_fuelcell_energy_values_for_each_timestep(ti)*delta_timestep in Wh

                        4.) Calculation of Electrolyzer Energy Consumption in Wh for the prediciton horizon. Until now, the
                            fuel cell runs stationary (no variation in electricity power output and hydrogen consumption)
                            >> total_fuelcell_energy_output_in_prediction_horizon_Wh = fuelcell output * delta_time_prediction_horizon in Wh

                        5.) Calculation of ratio (in %) of total useable energy from fuel cell /total fuel cell energy output both in in prediction horizon Wh
                            Based on a defined threshold of this ratio, decision to turn on or off electrolyzer
                    '''
                    #Step 1
                    energy_demand_values_for_each_timestep = []
                    for pv, el_consumption in zip(pv_prediction_watt, el_consumption_pred_onlyhouse_watt):
                        energy_demand_values_for_each_timestep.append(el_consumption-pv)

                    #Step 2                             
                    useable_fuelcell_energy_values_for_each_timestep = [max(0, min(value, self.config.fuel_cell_power)) for value in energy_demand_values_for_each_timestep]
                    
                    #Step3
                    seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
                    
                    useable_fuelcell_energy_values_for_each_timestep_in_wh = [value * seconds_per_timestep / 3600 for value in useable_fuelcell_energy_values_for_each_timestep]  # Umrechnung in Wh
                    total_useable_fuelcell_energy_amount_in_prediction_horizon_wh = sum(useable_fuelcell_energy_values_for_each_timestep_in_wh)
                    
                    #Step 4
                    total_fuelcell_energy_output_amount_in_prediction_horizon_Wh = self.config.fuel_cell_power*self.my_simulation_parameters.prediction_horizon/3600 
                    #Step 5
                    ratio_inpercentage_useableenergyfromfuelcell = 0
                    ratio_inpercentage_useableenergyfromfuelcell = total_useable_fuelcell_energy_amount_in_prediction_horizon_wh/total_fuelcell_energy_output_amount_in_prediction_horizon_Wh *100
                    
                    if ratio_inpercentage_useableenergyfromfuelcell >= self.config.Usebale_electrical_amount_of_fuelcell_related_to_fuelcell_output_in_prediction_horizon_in_percentage:
                        '''There is enough electricity demand amount in the prediction horizon, to run the fuel cell, so turn fuel cell on
                        
                        Start Fuel Cell and save the timestep when Fuel cell is started!
                        '''
                        self.state.RunFuelCell = 1 #electrolyzer is running "on mode"
                        self.state.mode = 2 #Needs the fuel cell: then the global thermal power is calculated; 0 means that global thermal power fuel cell is NOT calculated, 2 means, that the global thermal power is calculated
                        self.state.activation_timestep_fuelcell = timestep  


        
        
                    else:
                        #Fuel Cell  läuft weiterhin auf Standby
                        self.state.RunFuelCell = 99
                        self.state.mode = 0 #Needs the fuel cell: then the global thermal power is calculated; 0 means that global thermal power fuel cell is NOT calculated, 2 means, that the global thermal power is calculated


                else:
                    #Fuel Cell  läuft weiterhin auf Standby
                    self.state.RunFuelCell = 99 
                    self.state.mode = 0 #Needs the fuel cell: then the global thermal power is calculated; 0 means that global thermal power fuel cell is NOT calculated, 2 means, that the global thermal power is calculated


            else: 
                #Fuel Cell  läuft weiterhin auf Standby
                self.state.RunFuelCell = 99
                self.state.mode = 0 #Needs the fuel cell: then the global thermal power is calculated; 0 means that global thermal power fuel cell is NOT calculated, 2 means, that the global thermal power is calculated

                

        else:
            #Fuel Cell läuft weiterhin auf Standby
            self.state.RunFuelCell = 99
            self.state.mode = 0 #Needs the fuel cell: then the global thermal power is calculated; 0 means that global thermal power fuel cell is NOT calculated, 2 means, that the global thermal power is calculated




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
            Im Sommerbetrieb (SOEC) wird anhand einer PV-Prognose sowie einer Verbrauchsprognose der 
            voraussichtlich vorhandene Überschussstrom ermittelt. Reicht dieser laut Prognose UND Entscheidungskriterien aus, um den SOEC-Betrieb 
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
            General_PhotovoltaicDelivery = stsv.get_input_value(self.General_PhotovoltaicDeliveryInput)
            General_ElectricityConsumptiom = stsv.get_input_value(self.General_ElectricityConsumptiomInput)

            #Get "real" values of future timestep out of real data within prediciton horizon 
            el_consumption_pred_onlyhouse_watt = self.simulation_repository.get_dynamic_entry(ComponentType.ELECTRIC_CONSUMPTION, source_weight = 999)
            pv_prediction_watt = self.simulation_repository.get_dynamic_entry(ComponentType.PV, source_weight = 999) 
            
            #Controll "length" of values
            if len(pv_prediction_watt) != len(el_consumption_pred_onlyhouse_watt): #Control, whether the prediction lists have the same length!
                print("Fehler: Die Prediction-Listen im predictive controller electrolyzer fuel cell haben unterschiedliche Längen!")
                sys.exit(1)  # Beendet das Skript mit einem Fehlercode 1

            #Decision parameter: Is fuel cell season or electrolyzer season
            electrolyzer_or_fuelcell_mode = ((timestep >= self.config.on_off_SOEC) and (timestep <= self.config.off_on_SOEC))  #is the season of the year, where the electrolyzer is allowed to run?
                    #electrolyzer_or_fuelcell_mode = False --> Electrolyzer Season
                    #electrolyzer_or_fuelcell_mode = True --> Fuel Cell Season
            

            if electrolyzer_or_fuelcell_mode == False: #ELECTROLYZER SEASON!!! 
                self.state.RunFuelCell = 0 #Fuel Cell is completely turned off

                
                if self.state.RunElectrolyzer == 1:
                    '''
                    The following turn_off_procedure function manipulates the self.state.RunElectrolyzer and self.state.deactivation_timestep_electrolyzer
                    '''
                    self.turn_off_procedure_electrolyzer(timestep, pv_prediction_watt, el_consumption_pred_onlyhouse_watt, BatteryStateOfCharge, General_PhotovoltaicDelivery, General_ElectricityConsumptiom, hydrogen_soc)
                    


                elif self.state.RunElectrolyzer == 0 or self.state.RunElectrolyzer == 99:
                    ##Turn On Procedure 
                    '''
                    The following turn_off_procedure function manipulates the 
                    self.state.RunElectrolyzer and 
                    self.state.activation_timestep_electrolyzer
                    '''   
                    self.turn_on_procedure_electrolyzer(timestep, pv_prediction_watt, el_consumption_pred_onlyhouse_watt, BatteryStateOfCharge, General_PhotovoltaicDelivery, General_ElectricityConsumptiom, hydrogen_soc)
                print('Zeitschritt ', timestep, 'Einschalten/Ausschalteb ',self.state.RunElectrolyzer, 'Activation Timestep ', self.state.activation_timestep_electrolyzer, 'Deactivation Timestep  ', self.state.deactivation_timestep_electrolyzer)
            
            #*********************
                            
            
            
            
            
            else:    
                #FUEL CELL SEASON!!!         
                self.state.RunElectrolyzer = 0 # Electrolyzer is completely turned off which means, it is NOT running in standby
                '''
                Predictive Controll Fuel Cell: 
                # [x] Fuel Cell --> implementation of prediciton is not done until now
                '''
                #Forecast needed       
                if hydrogen_soc > self.config.h2_soc_lower_threshold_fuelcell:
                    
                    if self.state.RunFuelCell == 1:
                        self.turn_off_procedure_electrolyzer(timestep, pv_prediction_watt, el_consumption_pred_onlyhouse_watt, BatteryStateOfCharge, General_PhotovoltaicDelivery, General_ElectricityConsumptiom, hydrogen_soc)

                    elif self.state.RunFuelCell == 0 or self.state.RunFuelCell == 99:
                        self.turn_on_procedure_fuelcell(timestep, pv_prediction_watt, el_consumption_pred_onlyhouse_watt, BatteryStateOfCharge, General_PhotovoltaicDelivery, General_ElectricityConsumptiom, hydrogen_soc)




                else:
                    #There is not enough Hydrogen available in storage
                    self.state.RunFuelCell = 0 #Fuel Cell wird abgeschalten!
                    self.state.mode = 0 #Needs the fuel cell: then the global thermal power is calculated


            



            self.processed_state = self.state.clone()
        
        stsv.set_output_value(self.electrolyzer_onoff_signal_channel,self.state.RunElectrolyzer) 
        stsv.set_output_value(self.fuelcell_onoff_signal_channel,self.state.RunFuelCell) 
        stsv.set_output_value(self.chp_heatingmode_signal_channel, self.state.mode)

    def write_to_report(self) -> List[str]:
        """Writes the information of the current component to the report."""
        return self.config.get_string_dict()
    






