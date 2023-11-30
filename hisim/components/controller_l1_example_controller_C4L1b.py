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


__authors__ = "Christof Bernsteiner"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "-"
__email__ = "christof.bernsteiner@4wardenergy.at"
__status__ = "development"


@dataclass_json
@dataclass
class SimpleControllerConfig(ConfigBase):
    """Config class."""

    name: str
    szenario: str

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return SimpleController.get_full_classname()

    @classmethod
    def get_default_config(cls) -> Any:
        """Returns default config."""
        
        
        config = SimpleControllerConfig(name="SimpleController",szenario="necessary_to_choose")

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
    BatteryLoadingPowerWish = "BatteryLoadingPower" #W
    

    #Outputs
    ElectricityToOrFromGrid = "ElectricityToOrFromGrid"
    TotalElectricityConsumption = "TotalElectricityConsumption"



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
        self.General_ElectricityConsumptiomInput: ComponentInput = self.add_input(
            self.component_name,
            SimpleController.General_ElectricityConsumptiom,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )

        #Electrolyzer Electricity Consumption 
        self.Electrolyzer_ElectricityConsumptionInput: ComponentInput = self.add_input(
            self.component_name,
            SimpleController.Electrolyzer_ElectricityConsumption,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )

        #H2 Storage Electricity Consumption 
        self.H2Storage_ElectricityConsumptionInput: ComponentInput = self.add_input(
            self.component_name,
            SimpleController.H2Storage_ElectricityConsumption,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )

        #Photovoltaik Delivery
        self.General_PhotovoltaicDeliveryInput: ComponentInput = self.add_input(
            self.component_name,
            SimpleController.General_PhotovoltaicDelivery,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )


        #CHP/Fuel Cell Electricity Delviery 
        self.CHP_ElectricityDeliveryInput: ComponentInput = self.add_input(
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
        self.BatteryLoadingPowerWishOutput: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.BatteryLoadingPowerWish,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            postprocessing_flag=[
                InandOutputType.CHARGE_DISCHARGE,
                ComponentType.BATTERY,
            ],
            output_description=f"here a description for {self.BatteryLoadingPowerWish} will follow.",
        )

               
        self.electricity_to_or_from_gridOutput: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityToOrFromGrid,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityToOrFromGrid} will follow.",
        )

        self.total_electricity_consumptionOutput: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TotalElectricityConsumption,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.TotalElectricityConsumption} will follow.",
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

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        if force_convergence:
            return

        #From differenct Components
        General_ElectricityConsumptiom = stsv.get_input_value(self.General_ElectricityConsumptiomInput)
        Electrolyzer_ElectricityConsumption = stsv.get_input_value(self.Electrolyzer_ElectricityConsumptionInput)
        H2Storage_ElectricityConsumption = stsv.get_input_value(self.H2Storage_ElectricityConsumptionInput)
        General_PhotovoltaicDelivery = stsv.get_input_value(self.General_PhotovoltaicDeliveryInput)
        CHP_ElectricityDelivery = stsv.get_input_value(self.CHP_ElectricityDeliveryInput)

        
        if self.config.szenario == '1a':
            # #From differenct Components
            # General_ElectricityConsumptiom = stsv.get_input_value(self.General_ElectricityConsumptiomInput)
            # Electrolyzer_ElectricityConsumption = stsv.get_input_value(self.Electrolyzer_ElectricityConsumptionInput)
            # H2Storage_ElectricityConsumption = stsv.get_input_value(self.H2Storage_ElectricityConsumptionInput)
            # General_PhotovoltaicDelivery = stsv.get_input_value(self.General_PhotovoltaicDeliveryInput)
            # CHP_ElectricityDelivery = stsv.get_input_value(self.CHP_ElectricityDeliveryInput)
            
            #Production and consumption without Battery
            total_electricity_production = General_PhotovoltaicDelivery + CHP_ElectricityDelivery
            total_electricity_consumption = General_ElectricityConsumptiom + Electrolyzer_ElectricityConsumption + H2Storage_ElectricityConsumption
            electricity_to__or_from_battery_Wish = total_electricity_production - total_electricity_consumption
            
            #Integration of Battery
            stsv.set_output_value(self.BatteryLoadingPowerWishOutput, electricity_to__or_from_battery_Wish)
            #BatteryStateofCharge = stsv.get_input_value(self.BatteryStateofChargeInput)
            BatteryAcBatteryPower = stsv.get_input_value(self.BatteryAcBatteryPowerInput)
            #BatteryDcBatteryPower = stsv.get_input_value(self.BatteryDcBatteryPowerInput)


            electricity_to_or_from_grid = total_electricity_production - total_electricity_consumption - BatteryAcBatteryPower
            stsv.set_output_value(self.electricity_to_or_from_gridOutput, electricity_to_or_from_grid)
            stsv.set_output_value(self.total_electricity_consumptionOutput, total_electricity_consumption)
        
        
        if self.config.szenario == '1b':
            #Abzug aller Stromproduktionen und Verbräuche

            
            
            #Elektrolysebetrieb
            if Electrolyzer_ElectricityConsumption > 0 and CHP_ElectricityDelivery == 0:  ##Sind wir im Electrolysebetrieb? Wenn ja, dann...
                
                Estatus_house = General_PhotovoltaicDelivery - General_ElectricityConsumptiom ##Decke zuerst mit der PV den Strombedarf des Hauses
                electricity_electH2stor_consumption_system = Electrolyzer_ElectricityConsumption + H2Storage_ElectricityConsumption ##Rechne den Gesamtstrombedarf des Elektrolyseur + Wasserstoffspeichers zusammen
        
                if Estatus_house > 0: #Wenn nach Abzug des Hausstrombedarfs von der PV noch ein PV Strom übrig ist.....
                                    
                    status_batteryWish = Estatus_house - electricity_electH2stor_consumption_system #Brauche ich Energie von der Batterie, oder kann ich dieser welche zukommen lassen?          
                    
                    stsv.set_output_value(self.BatteryLoadingPowerWishOutput, status_batteryWish) 
                    BatteryAcBatteryPower = stsv.get_input_value(self.BatteryAcBatteryPowerInput) #Falls ich Energie von der Batterie brauche, wieviel kann mir diese liefern?
                    
                    Estatus_system = Estatus_house - electricity_electH2stor_consumption_system - BatteryAcBatteryPower #Strombedarf vom Netz ergibt sich aus PV Produktion + Batteriezuschuss - Verbrauch Elektrolyseur - Verbrauch Wasserstoffspeicher
                    
                    ##Wenn Batterie Strom zur Deckung liefert, darf kein Strom ins Netz gehen!!!! ÜBERPRÜFEN NOTWENDIG !!!!
                    
                    electricity_to_or_from_grid = Estatus_system

                
                elif Estatus_house <= 0: #Es ist kein PV Strom mehr übrig UND der Hausstrombedarf ist eventuell auch nicht gedeckt
                    
                    status_batteryWish = -electricity_electH2stor_consumption_system #Überprüfen ob Batterie den Stromverbrauch Elektrolyseur + Wasserstoffspeicher decken kann?
                    stsv.set_output_value(self.BatteryLoadingPowerWishOutput, status_batteryWish)
                    BatteryAcBatteryPower = stsv.get_input_value(self.BatteryAcBatteryPowerInput) #Falls ich Energie von der Batterie brauche, wieviel kann mir diese liefern?


                    electricity_to_or_from_grid = Estatus_house - electricity_electH2stor_consumption_system - BatteryAcBatteryPower 
                else:
                    print("ERROR IM ENERGYMANAGEMENT-SYSTEM")
                    breakpoint()
            
            #Brennstoffzellenbetrieb
            if Electrolyzer_ElectricityConsumption == 0 and CHP_ElectricityDelivery >= 0:  ##Sind wir im Electrolysebetrieb? Wenn ja, dann...
                print(timestep)
                Estatus_house = General_PhotovoltaicDelivery - General_ElectricityConsumptiom ##Decke zuerst mit der PV den Strombedarf des Hauses // 
                part_pv_to_grid = 0
                if Estatus_house > 0: #Ist ein Überschussstrom vom PV Strom nach Abzug des Strombederafs des Hauses noch vorhanden?
                    part_pv_to_grid = Estatus_house #WEnn ja, dieser Überschussstrom geht ins Netz....
                    Estatus_house = 0 #Haustrombedarf ist damit auch auf jeden Fall gedeckt....
                
                #Zählen wir den restlichen Hausstrombedarf mit dem Bedarf für den Wasserstoffspeicher zusammen und schauen, wieviel uns die Brennstoffzelle liefert!
                status_batteryWish = Estatus_house - H2Storage_ElectricityConsumption + CHP_ElectricityDelivery
                stsv.set_output_value(self.BatteryLoadingPowerWishOutput, status_batteryWish)
                BatteryAcBatteryPower = stsv.get_input_value(self.BatteryAcBatteryPowerInput) #Falls ich Energie von der Batterie brauche, wieviel kann mir diese liefern?
                # if BatteryAcBatteryPower < 0:
                #     breakpoint()
                Estatus_total = Estatus_house - H2Storage_ElectricityConsumption + CHP_ElectricityDelivery - BatteryAcBatteryPower
                
                if Estatus_total > 0 and part_pv_to_grid <= 0:
                    breakpoint()
                electricity_to_or_from_grid = Estatus_total + part_pv_to_grid

            if Electrolyzer_ElectricityConsumption == 0 and CHP_ElectricityDelivery == 0:
                print(timestep)

            
            stsv.set_output_value(self.electricity_to_or_from_gridOutput, electricity_to_or_from_grid)
    
    
    
    
    def write_to_report(self):
        """Writes the information of the current component to the report."""
        return self.config.get_string_dict()