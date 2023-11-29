# Generic/Built-in
import copy
from hisim import log
from hisim import component as cp
from typing import Any
from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Owned
from hisim.component import (
    Component,
    SingleTimeStepValues,
    ComponentInput,
    ComponentOutput,
    ConfigBase,
)
from hisim import loadtypes as lt
from hisim.loadtypes import InandOutputType, ComponentType
from hisim.simulationparameters import SimulationParameters
from hisim.components import static_electrolyzer, generic_hydrogen_storage, csvloader_electricityconsumption, csvloader_photovoltaic, generic_CHP


__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


@dataclass_json
@dataclass
class SimpleControllerConfig(ConfigBase):
    """Config class."""

    name: str

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return SimpleController.get_full_classname()

    @classmethod
    def get_default_config(cls) -> Any:
        """Returns default config."""
        config = SimpleControllerConfig(name="SimpleController")
        return config


class SimpleController(Component):
  
    #Input
    General_ElectricityConsumptiom = "CSV Profile Electricity Consumption Input"
    Electrolyzer_ElectricityConsumption = "ElectricityConsumptionElectrolyzer" #W
    H2Storage_ElectricityConsumption = "StorageElectricityConsumption" #W (richtig??)
    General_PhotovoltaicDelivery = "CSV Profile Photovoltaic Electricity Delivery Input" #W
    CHP_ElectricityDelivery = "Fuel Cell/CHP Electricity Delivery Input" #W
    
    
    #State of Charge of different storages 
    BatteryStateOfCharge = "Battery State of Charge 0...1" # [0..1]
    BatteryAcBatteryPower = "Ac Battery Power" #W
    BatteryDcBatteryPower = "Dc Battery Power" #W  
    
    #Output to Battery
    BatteryLoadingPower = "BatteryLoadingPower" #W
  




    def __init__(
        self,
        name: str,
        my_simulation_parameters: SimulationParameters,
        config: SimpleControllerConfig,
    ) -> None:
        super().__init__(
            name, my_simulation_parameters=my_simulation_parameters, my_config=config
        )


        #INPUTS
        #csv Input Electricity Consumption
        self.input1: ComponentInput = self.add_input(
            self.component_name,
            SimpleController.General_ElectricityConsumptiom,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )

        #Electrolyzer Electricity Consumption 
        self.input2: ComponentInput = self.add_input(
            self.component_name,
            SimpleController.Electrolyzer_ElectricityConsumption,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )

        #H2 Storage Electricity Consumption 
        self.input3: ComponentInput = self.add_input(
            self.component_name,
            SimpleController.H2Storage_ElectricityConsumption,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )

        #H2 Storage Electricity Consumption 
        self.input4: ComponentInput = self.add_input(
            self.component_name,
            SimpleController.General_PhotovoltaicDelivery,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )


        #H2 Storage Electricity Consumption 
        self.input5: ComponentInput = self.add_input(
            self.component_name,
            SimpleController.CHP_ElectricityDelivery,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )

        #Battery: Loading Input Data with other code logic
        self.BatteryStateofChargeInput: ComponentInput = self.add_input(
            self.component_name,
            SimpleController.BatteryStateOfCharge,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            True,
        )

        #Battery: Loading Input Data with other code logic
        self.BatteryAcBatteryPowerInput: ComponentInput = self.add_input(
            self.component_name,
            SimpleController.BatteryAcBatteryPower,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )

        #Battery: Loading Input Data with other code logic
        self.BatteryDcBatteryPowerInput: ComponentInput = self.add_input(
            self.component_name,
            SimpleController.BatteryDcBatteryPower,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )

        #OUTPUTS
        # Define component outputs
        self.BatteryLoadingPowerOutput: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.BatteryLoadingPower,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            postprocessing_flag=[
                InandOutputType.CHARGE_DISCHARGE,
                ComponentType.BATTERY,
            ],
            output_description=f"here a description for {self.BatteryLoadingPower} will follow.",
        )


 
        self.state = 0
        self.previous_state = self.state

        self.add_default_connections(self.get_default_connections_from_static_electrolyzer())
        self.add_default_connections(self.get_default_connections_from_h2_storage())
        self.add_default_connections(self.get_default_connections_from_general_electricity_consumption()) # Wie verbinden?
        self.add_default_connections(self.get_default_connections_from_general_photovoltaic_delivery()) # Wie verbinden?
        self.add_default_connections(self.get_default_connections_from_chp_electricity_delivery()) # Wie verbinden?


    def get_default_connections_from_static_electrolyzer(self):
        """Sets default connections for the electrolyzer in the energy management system."""
        log.information("setting electrolyzer default connections in Electrolyzer Controller")
        connections = []
        electrolyzer_classname = static_electrolyzer.StaticElectrolyzer.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleController.Electrolyzer_ElectricityConsumption,
                electrolyzer_classname,
                static_electrolyzer.StaticElectrolyzer.ElectricityConsumption,
            )
        )
        return connections
    
    def get_default_connections_from_h2_storage(self):
        """Sets default connections for the hydrogen storage in the energy management system."""
        log.information("setting hydrogen storage default connections in Electrolyzer Controller")
        connections = []
        h2_storage_classname = generic_hydrogen_storage.GenericHydrogenStorage.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleController.H2Storage_ElectricityConsumption,
                h2_storage_classname,
                generic_hydrogen_storage.GenericHydrogenStorage.ElectricityConsumption,
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
                SimpleController.General_ElectricityConsumptiom,
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
                SimpleController.General_PhotovoltaicDelivery,
                general_photovoltaic_delivery_classname,
                csvloader_photovoltaic.CSVLoader_photovoltaic.Output1,
            )
        )
        return connections
    

    def get_default_connections_from_chp_electricity_delivery(self):
        """Sets default connections for the chp electricity delivered  in the energy management system."""
        log.information("setting chp electricity delivered  default connections in Electrolyzer Controller")
        connections = []
        CHP_ElectricityDelivery_classname = generic_CHP.SimpleCHP.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleController.CHP_ElectricityDelivery,
                CHP_ElectricityDelivery_classname,
                generic_CHP.SimpleCHP.ElectricityOutput,
            )
        )
        return connections


    def i_save_state(self) -> None:
        self.previous_state = self.state

    def i_restore_state(self) -> None:
        self.state = self.previous_state

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        if force_convergence:
            return
        
        #Loading Input Data from Battery 
        BatteryStateofCharge = stsv.get_input_value(self.BatteryStateofChargeInput)
        BatteryAcBatteryPowe = stsv.get_input_value(self.BatteryAcBatteryPowerInput)
        BatteryDcBatteryPower = stsv.get_input_value(self.BatteryDcBatteryPowerInput)

        stsv.set_output_value(self.BatteryLoadingPowerOutput, ac_battery_power_in_watt)

        percent = stsv.get_input_value(self.input1)
        
        
        
        if percent < 0.4:
            self.state = 1
        if percent > 0.99:
            self.state = 0
        stsv.set_output_value(self.output1, self.state)
