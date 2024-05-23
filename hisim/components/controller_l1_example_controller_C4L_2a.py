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
from hisim.components import C4L_electrolyzer
from hisim.postprocessing.Cell4Life_ControllExcelSheet import errormessage

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
    # SOFC or SOEF timestep when activated (seasonal on or off!)
    on_off_SOEC : int #SOEC = Electrolyzer is turned off  at this timestep (!) of season
    off_on_SOEC : int #SOEC = Electrolyzer is turned on at this timestep (!) of season

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return SimpleController.get_full_classname()

    @classmethod
    def get_default_config(cls) -> Any:
        """Returns default config."""
        
        
        config = SimpleControllerConfig(name="SimpleController",szenario="necessary_to_choose", on_off_SOEC = 0, off_on_SOEC = 0)

        return config

class SimpleControllerState:
    """Data class that saves the state of the electrolyzer-fuelcell controller."""
    def __init__(
        self,
        # CHP_ElectricityDelivery: float,
        # H2Storage_ElectricityConsumption: float,
        # CHP_ElectricityStandbyEnergy: float,
        # electricity_from_Battery_to_house: float,
        # electricity_from_Battery_to_CHP_inStandby: float,
        # part_pv_to_grid: float,
        electrolyzer_or_fuelcell_mode: bool,
        electricity_to_or_from_grid: float,
        electricity_from_CHP_to_house:float,
        electricity_from_Battery_to_house: float,
        part_pv_to_grid:float,
        electricity_from_Battery_to_CHP_inStandby:float,
        electricity_fromCHP_to_battery:float,
        electricity_fromCHP_to_grid:float,


    ) -> None:
        """Initializes electrolyzer Controller state.

        :param CHP_ElectricityDelivery: 0 
        :type CHP_ElectricityDelivery: int
        """
        # self.CHP_ElectricityDelivery: float = CHP_ElectricityDelivery
        # self.H2Storage_ElectricityConsumption: float = H2Storage_ElectricityConsumption
        # self.CHP_ElectricityStandbyEnergy: float = CHP_ElectricityStandbyEnergy
        # self.electricity_from_Battery_to_house: float = electricity_from_Battery_to_house
        # self.electricity_from_Battery_to_CHP_inStandby: float = electricity_from_Battery_to_CHP_inStandby
        # self.part_pv_to_grid: float = part_pv_to_grid
        
        self.electrolyzer_or_fuelcell_mode: bool = electrolyzer_or_fuelcell_mode
        
        self.electricity_to_or_from_grid: float = electricity_to_or_from_grid
        self.electricity_from_CHP_to_house:float = electricity_from_CHP_to_house
        self.electricity_from_Battery_to_house: float = electricity_from_Battery_to_house
        self.part_pv_to_grid:float = part_pv_to_grid
        self.electricity_from_Battery_to_CHP_inStandby:float = electricity_from_Battery_to_CHP_inStandby
                    
        self.electricity_fromCHP_to_battery:float = electricity_fromCHP_to_battery,
        self.electricity_fromCHP_to_grid:float = electricity_fromCHP_to_grid,
    
    def clone(self) -> "SimpleControllerState":
        """Copies the current instance."""
        return SimpleControllerState(
            # CHP_ElectricityDelivery=self.CHP_ElectricityDelivery,
            # H2Storage_ElectricityConsumption = self.H2Storage_ElectricityConsumption,
            # CHP_ElectricityStandbyEnergy = self.CHP_ElectricityStandbyEnergy,
            # electricity_from_Battery_to_house = self.electricity_from_Battery_to_house,
            electrolyzer_or_fuelcell_mode = self.electrolyzer_or_fuelcell_mode,
            # electricity_from_Battery_to_CHP_inStandby = self.electricity_from_Battery_to_CHP_inStandby,
            # part_pv_to_grid = self.part_pv_to_grid, 
            
            electricity_to_or_from_grid = self.electricity_to_or_from_grid,
            electricity_from_CHP_to_house = self.electricity_from_CHP_to_house,
            electricity_from_Battery_to_house = self.electricity_from_Battery_to_house,
            part_pv_to_grid = self.part_pv_to_grid,
            electricity_from_Battery_to_CHP_inStandby = self.electricity_from_Battery_to_CHP_inStandby,


            electricity_fromCHP_to_battery = self.electricity_fromCHP_to_battery,
            electricity_fromCHP_to_grid = self.electricity_fromCHP_to_grid,
        )

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

class SimpleController(Component):
    
    

    #Input
    General_ElectricityConsumptiom = "CSV Profile Electricity Consumption Input" 
    Electrolyzer_ElectricityConsumption = "ElectricityConsumptionElectrolyzer" #W
    H2Storage_ElectricityConsumption = "StorageElectricityConsumption" #W (richtig??)
    General_PhotovoltaicDelivery = "CSV Profile Photovoltaic Electricity Delivery Input" #W
    CHP_ElectricityDelivery = "Fuel Cell/CHP Electricity Delivery Input" #W
    FuelCellElectricityInputStandby = "Fuel Cell/CHP Electricity Standby Consumption Input" #W
    
    #Battery Inputs
    BatteryStateOfCharge = "Battery State of Charge 0...1" # [0..1] State of charge
    BatteryAcBatteryPower = "Ac Battery Power" #W
    BatteryDcBatteryPower = "Dc Battery Power" #W  
    
    #Output to Battery
    BatteryLoadingPowerWish = "BatteryLoadingPowerWish" #W
    

    #Outputs
    ElectricityToOrFromGrid = "ElectricityToOrFromGrid"
    TotalElectricityConsumption = "TotalElectricityConsumption"
    ElectricityfromCHPtoHouse = "QuantitiyShare_electricity_from_CHP_to_house" #in W ---> the amount of energy which is delivered from the fuel cell/chp to the house
    electricity_fromPVtogrid = "QuantitiyShare_PV_to_grid" #just for // FOR DEBUGGING"
    ElectricityfromBatterytoHouse = "QuantitiyShare_electricity_from_Battery_to_house" #in W ---> the amount of energy which is delivered from the fuel cell/chp to the house
    ElectricityfromBatterytoCHPinStandby = "QuantitiyShare_electricity_from_Battery_to_CHPinStandby" #in W ---> the amount of energy which is delivered from the battery to the CHP/Fuel Cell in Standbymode

    electricity_fromCHP_to_battery = "QuantitiyShare_CHP_to_battery" #in W
    electricity_fromCHP_to_grid = "QuantitiyShare_CHP_to_grid"  #in W

    def __init__(
        self,
        name: str,
        my_simulation_parameters: SimulationParameters,
        config: SimpleControllerConfig,
    ) -> None:
        super().__init__(
            name, my_simulation_parameters=my_simulation_parameters, my_config=config
        )

        self.state: SimpleControllerState = SimpleControllerState(-1, 9999999,9999999,9999999,9999999,9999999,9999999,9999999)
        self.previous_state: SimpleControllerState = self.state.clone()
        self.processed_state: SimpleControllerState = self.state.clone()

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

        #Fuel Cell Standby Electricity Consumption 
        self.FuelCellElectricityInputStandbyInput: ComponentInput = self.add_input(
            self.component_name,
            SimpleController.FuelCellElectricityInputStandby,
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

        self.electricity_from_CHP_to_houseOutput: cp.ComponentOutput = self.add_output(
            object_name= self.component_name,
            field_name= self.ElectricityfromCHPtoHouse,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityfromCHPtoHouse} will follow.",
        )

        self.electricity_from_Battery_to_houseOutput: cp.ComponentOutput = self.add_output(
            object_name= self.component_name,
            field_name= self.ElectricityfromBatterytoHouse,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityfromBatterytoHouse} will follow.",
        )

        self.electricity_from_PV_to_gridOutput: cp.ComponentOutput = self.add_output(
            object_name= self.component_name,
            field_name= self.electricity_fromPVtogrid,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.electricity_fromPVtogrid} will follow.",
        )

        self.electricity_frombattery_to_CHP_inStandby: cp.ComponentOutput = self.add_output(
            object_name= self.component_name,
            field_name= self.ElectricityfromBatterytoCHPinStandby,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityfromBatterytoCHPinStandby} will follow.",
        )

        
        self.electricity_from_electricity_from_CHP_to_batteryOutput: cp.ComponentOutput = self.add_output(
            object_name= self.component_name,
            field_name= self.electricity_fromCHP_to_battery,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.electricity_fromCHP_to_battery} will follow.",
        )


        self.electricity_from_electricity_from_CHP_to_gridOutput: cp.ComponentOutput = self.add_output(
            object_name= self.component_name,
            field_name= self.electricity_fromCHP_to_grid,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.electricity_fromCHP_to_grid} will follow.",
        )



        self.add_default_connections(self.get_default_connections_from_C4L_electrolyzer())
        self.add_default_connections(self.get_default_connections_from_static_electrolyzer())
        self.add_default_connections(self.get_default_connections_from_h2_storage())
        self.add_default_connections(self.get_default_connections_from_general_electricity_consumption()) 
        self.add_default_connections(self.get_default_connections_from_general_photovoltaic_delivery()) 
        self.add_default_connections(self.get_default_connections_from_chp_electricity_delivery()) 
        #self.add_default_connections(self.get_default_connections_from_chp_electricity_Standby_consumption()) 


    def get_default_connections_from_C4L_electrolyzer(self):
        """Sets default connections for the electrolyzer in the energy management system."""
        log.information("setting electrolyzer default connections in Electrolyzer Controller")
        connections = []
        electrolyzer_classname = C4L_electrolyzer.C4LElectrolyzer.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleController.Electrolyzer_ElectricityConsumption,
                electrolyzer_classname,
                C4L_electrolyzer.C4LElectrolyzer.ElectricityConsumption,
            )
        )
        return connections

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
    
    # def get_default_connections_from_chp_electricity_Standby_consumption(self):
    #     """Sets default connections for the chp electricity delivered  in the energy management system."""
    #     log.information("setting FUEL CELL standby energy consumption  default connections")
    #     connections = []
    #     FuelCellElectricityInputStandby_classname = generic_CHP.SimpleCHP.get_classname()
    #     connections.append(
    #         cp.ComponentConnection(
    #             SimpleController.FuelCellElectricityInputStandby,
    #             FuelCellElectricityInputStandby_classname,
    #             generic_CHP.SimpleCHP.FuelCellElectricityInputStandby,
    #         )
    #     )
    #     return connections
    

            

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
        def electricity_from_CHP_Battery_to_houseFct(CHP_ElectricityDelivery, BatteryAcBatteryPower, electricity_to_or_from_grid, H2Storage_ElectricityConsumption, CHP_ElectricityStandbyEnergy, Estatus_CHP_ElectricityStandbyEnergy, timestep):
            #Wenn CHP liefert Electricity (CHP_ElectricityDelivery>0), dann braucht diese keine Standby Energie (CHP_ElectricityStandbyEnergy=0)
            ##CHP läuft --> wie schaut da die Stromaufteilung aus?
            if CHP_ElectricityDelivery>0 and BatteryAcBatteryPower >= 0 and electricity_to_or_from_grid >= 0: #CHP Is running; SURPLOS ELECTRICITY from Fuel Cell/CHP is stored in battery and/or fed in grid
                
                self.state.electricity_from_CHP_to_house = CHP_ElectricityDelivery - H2Storage_ElectricityConsumption - electricity_to_or_from_grid - BatteryAcBatteryPower 
                self.state.electricity_from_Battery_to_house = 0
                self.state.electricity_from_Battery_to_CHP_inStandby = 0
                
                if self.state.electricity_from_CHP_to_house < 0:
                    self.state.electricity_from_CHP_to_house = 0

                self.state.electricity_from_CHP_to_battery = BatteryAcBatteryPower #only chp is loading battery in this simulation case
                self.state.electricity_from_CHP_to_grid = CHP_ElectricityDelivery - self.state.electricity_from_CHP_to_house - self.state.electricity_from_CHP_to_battery - H2Storage_ElectricityConsumption
                
                
                errormessage([timestep, "Fall1: #CHP Is running; SURPLOS ELECTRICITY from Fuel Cell/CHP is stored in battery and/or fed in grid"])
                
            elif CHP_ElectricityDelivery>0 and BatteryAcBatteryPower <= 0 and electricity_to_or_from_grid <= 0: #CHP is running; Grid and/or Battery is delivering some electricity! 
                self.state.electricity_from_CHP_to_house = CHP_ElectricityDelivery - H2Storage_ElectricityConsumption
                self.state.electricity_from_Battery_to_house = -BatteryAcBatteryPower
                self.state.electricity_from_Battery_to_CHP_inStandby = 0
                
                self.state.electricity_from_CHP_to_battery = 0
                self.state.electricity_from_CHP_to_grid = 0

                errormessage([timestep, "Fall2: #CHP is running; Grid and/or Battery is delivering some electricity! "])


            
            elif CHP_ElectricityDelivery>0 and BatteryAcBatteryPower > 0 and electricity_to_or_from_grid < 0: #Fall 3: is only possible, if battery was unloaded too much --> forces system to get energy!
                errormessage([timestep,"Fall 3: Can only happen, if battery was overcharged/undercharged --> battery is loaded by chp"])
                self.state.electricity_from_CHP_to_house = CHP_ElectricityDelivery - H2Storage_ElectricityConsumption - BatteryAcBatteryPower
                self.state.electricity_from_Battery_to_CHP_inStandby = 0
                self.state.electricity_from_Battery_to_house = 0
                
                self.state.electricity_from_CHP_to_battery = BatteryAcBatteryPower
                self.state.electricity_from_CHP_to_grid = CHP_ElectricityDelivery - self.state.electricity_from_CHP_to_house - self.state.electricity_from_CHP_to_battery


            elif CHP_ElectricityDelivery>0 and BatteryAcBatteryPower < 0 and electricity_to_or_from_grid > 0: #WORST CASE: battery is delivering but some electricity goes into grid -> can only happen if h2 storage consumes more energy than CHP is delivering
                errormessage([timestep,"Worstcase 2: is only possible if h2 storage consumes more energy than CHP is delivering --> possible?"])
                self.state.electricity_from_Battery_to_house = -BatteryAcBatteryPower
                self.state.electricity_from_CHP_to_house = CHP_ElectricityDelivery - H2Storage_ElectricityConsumption - electricity_to_or_from_grid
                self.state.electricity_from_Battery_to_CHP_inStandby = 0

                self.state.electricity_from_CHP_to_battery = 0
                self.state.electricity_from_CHP_to_grid = 0

            
            elif CHP_ElectricityDelivery > 0 and CHP_ElectricityStandbyEnergy > 0:
                #Wenn CHP liefert Electricity (CHP_ElectricityDelivery>0), dann braucht diese keine Standby Energie (CHP_ElectricityStandbyEnergy=0)
                errormessage([timestep,"WORSTCASE: Fuel Cell delivers Electricity but is on Standby!!!!"])




            ###CHP ist im Standby --> Wie schaut da die Stromaufteilung aus?
            elif CHP_ElectricityDelivery == 0 and CHP_ElectricityStandbyEnergy > 0 and BatteryAcBatteryPower >= 0 and electricity_to_or_from_grid >= 0: #Surplus Energy from PV is fed to CHP Standby and Battery and grid
                self.state.electricity_from_CHP_to_house = 0 #CHP Is on Standby --> thats why it is not delivering energy to anywhere
                self.state.electricity_from_Battery_to_CHP_inStandby = 0
                self.state.electricity_from_Battery_to_house = 0
                
                self.state.electricity_from_CHP_to_battery = 0
                self.state.electricity_from_CHP_to_grid = 0

                errormessage([timestep, "CHP on standby: Battery and/or grid gets energy"])
            elif CHP_ElectricityDelivery == 0 and CHP_ElectricityStandbyEnergy > 0 and BatteryAcBatteryPower <= 0 and electricity_to_or_from_grid <= 0: #Grid and/or Battery is delivering some electricity! 
                self.state.electricity_from_CHP_to_house = 0 #CHP Is on Standby --> thats why it is not delivering energy to anywhere
                self.state.electricity_from_Battery_to_CHP_inStandby = min(-1*Estatus_CHP_ElectricityStandbyEnergy, -1*BatteryAcBatteryPower)
                self.state.electricity_from_Battery_to_house = (-BatteryAcBatteryPower - self.state.electricity_from_Battery_to_CHP_inStandby)

                self.state.electricity_from_CHP_to_battery = 0
                self.state.electricity_from_CHP_to_grid = 0
                errormessage([timestep, "CHP on standby: Battery and/or grid delivers energy"])

            elif CHP_ElectricityDelivery == 0 and CHP_ElectricityStandbyEnergy > 0 and BatteryAcBatteryPower >= 0 and electricity_to_or_from_grid <= 0: #
                self.state.electricity_from_CHP_to_house = 0 #CHP Is on Standby --> thats why it is not delivering energy to anywhere
                self.state.electricity_from_Battery_to_CHP_inStandby = 0
                self.state.electricity_from_Battery_to_house = 0

                self.state.electricity_from_CHP_to_battery = 0
                self.state.electricity_from_CHP_to_grid = 0
                errormessage([timestep, "Worstcase CHP1: CHP on standby: Battery gets energy/grid delivery energy"])
            
            elif CHP_ElectricityDelivery == 0 and CHP_ElectricityStandbyEnergy > 0 and BatteryAcBatteryPower <= 0 and electricity_to_or_from_grid >= 0: #
                self.state.electricity_from_CHP_to_house = 0 #CHP Is on Standby --> thats why it is not delivering energy to anywhere
                self.state.electricity_from_Battery_to_CHP_inStandby = 0
                self.state.electricity_from_Battery_to_house = 0

                self.state.electricity_from_CHP_to_battery = 0
                self.state.electricity_from_CHP_to_grid = 0
                errormessage([timestep, "Worstcase CHP2: CHP on standby: Battery delivers energy/grid gets energy"])


            else: 
                errormessage([timestep, "No Energy from CHP and Battery to House"])
                self.state.electricity_from_CHP_to_house = 0
                self.state.electricity_from_Battery_to_house = 0
                self.state.electricity_from_Battery_to_CHP_inStandby = 0
                self.state.electricity_from_CHP_to_battery = 0
                self.state.electricity_from_CHP_to_grid = 0

            if self.state.electricity_from_CHP_to_house < 0: #in that case, all electricity from CHP/Fuell cell is going to grid, battery or h2storage
                self.state.electricity_from_CHP_to_house=0
                electricity_from_CHP_to_battery = BatteryAcBatteryPower
                electricity_from_CHP_to_grid = CHP_ElectricityDelivery - self.state.electricity_from_CHP_to_battery - H2Storage_ElectricityConsumption
                errormessage([timestep, "CHP to House wurde Null gesetzt"])
            
            return self.state.electricity_from_CHP_to_house, self.state.electricity_from_Battery_to_house, self.state.electricity_from_Battery_to_CHP_inStandby, self.state.electricity_from_CHP_to_battery,self.state.electricity_from_CHP_to_grid 
        
        '''
        if force_convergence:
            
            # if self.state.electrolyzer_or_fuelcell_mode == False:
            #     pass
            # else:
            #     self.state = self.processed_state.clone() 
            #     BatteryAcBatteryPower = stsv.get_input_value(self.BatteryAcBatteryPowerInput) #Batterie reagiert einen Schritt zeitversetzt! Daher wird hier nochmals nachgefragt, was zuletzt von Batterie angefragt wurde!
            
            #     Estatus_total = self.state.Estatus_house + self.state.Estatus_CHP_ElectricityStandbyEnergy - self.state.H2Storage_ElectricityConsumption + self.state.CHP_ElectricityDelivery - BatteryAcBatteryPower
                        
            #     electricity_to_or_from_grid = Estatus_total +  self.state.part_pv_to_grid
            #     electricity_from_CHP_to_house,self.state.electricity_from_Battery_to_house,electricity_from_Battery_to_CHP_inStandby= electricity_from_CHP_Battery_to_houseFct(self.state.CHP_ElectricityDelivery, BatteryAcBatteryPower, electricity_to_or_from_grid, self.state.H2Storage_ElectricityConsumption,self.state.CHP_ElectricityStandbyEnergy,self.state.Estatus_CHP_ElectricityStandbyEnergy,timestep)


        
        
        else:
        '''    
        if True:
            #From differenct Components
            General_ElectricityConsumptiom = stsv.get_input_value(self.General_ElectricityConsumptiomInput)
            Electrolyzer_ElectricityConsumption = stsv.get_input_value(self.Electrolyzer_ElectricityConsumptionInput)
            H2Storage_ElectricityConsumption = stsv.get_input_value(self.H2Storage_ElectricityConsumptionInput)
            General_PhotovoltaicDelivery = stsv.get_input_value(self.General_PhotovoltaicDeliveryInput)
            CHP_ElectricityDelivery = stsv.get_input_value(self.CHP_ElectricityDeliveryInput)
            CHP_ElectricityStandbyEnergy = stsv.get_input_value(self.FuelCellElectricityInputStandbyInput)
        


            
            if self.config.szenario == '2a': # [X] Logik des Energieflusses gehört für Szenario 2 angepasst --> Angepasst!!! PV beladet sowohl Haus als auch Fuel Cell im Standby!!! Reststrom von PV geht ins Netz--> Nur Fuel Cell beladet Battier; Batterie steht dem HAus zur Verfügung!
                '''
                PV beladet sowohl Haus als auch Fuel Cell wenn diese Standby!!! 
                Reststrom von PV geht ins Netz.
                Nur Fuel Cell beladet Battier; Batterie steht dem Haus und allen anderen Verbräuchern aber zur Verfügung!
                '''
                
                #Abzug aller Stromproduktionen und Verbräuche
                

                #Decision parameter: Is fuel cell season or electrolyzer season --> Decision is the same like in Electrolyzer and fuel cell controller!!! 
                self.state.electrolyzer_or_fuelcell_mode = ((timestep >= self.config.on_off_SOEC) and (timestep <= self.config.off_on_SOEC))  #is the season of the year, where the electrolyzer is allowed to run?
                        #electrolyzer_or_fuelcell_mode = False --> Electrolyzer Season
                        #electrolyzer_or_fuelcell_mode = True --> Fuel Cell Season
                
                #Elektrolysebetrieb # [x] Ich muss abhängig von der Saison diesmal entscheiden --> Szenarion 1b war nämlich Elektrolyseur immer eingeschaltet
                
                
                if self.state.electrolyzer_or_fuelcell_mode == False: ##Sind wir im Electrolysebetrieb? Wenn ja, dann...
            
                    self.state.electricity_from_Battery_to_house = 0 # Batterie beliefert ausschließlich Elektrolyseur auf einer direkt Leitung (nicht über allgemeine Netz)
                    self.state.electricity_from_CHP_to_house     = 0 #Da CHP/Fuel Cell nicht läuft, kann kein Strom von CHP zu den Wohnungen geliefert werden! 
                    #[ ] gehört noch angepasst CHP to battery; CHP to grid
                    self.state.electricity_from_CHP_to_battery = 0 #Da CHP/Fuel Cell nicht läuft, kann kein Strom von CHP geliefert werden! 
                    self.state.electricity_from_CHP_to_grid = 0 #Da CHP/Fuel Cell nicht läuft, kann kein Strom von CHP geliefert werden! 


                    Estatus_house = General_PhotovoltaicDelivery - General_ElectricityConsumptiom ##Decke zuerst mit der PV den Strombedarf des Hauses
                    electricity_electH2stor_consumption_system = Electrolyzer_ElectricityConsumption + H2Storage_ElectricityConsumption ##Rechne den Gesamtstrombedarf des Elektrolyseur + Wasserstoffspeichers zusammen
            
                    if Estatus_house > 0: #Wenn nach Abzug des Hausstrombedarfs von der PV noch ein PV Strom übrig ist.....
                                        
                        status_batteryWish = Estatus_house - electricity_electH2stor_consumption_system #Brauche ich Energie von der Batterie, oder kann ich dieser welche zukommen lassen?          
                        


                        stsv.set_output_value(self.BatteryLoadingPowerWishOutput, status_batteryWish) 
                        BatteryAcBatteryPower = stsv.get_input_value(self.BatteryAcBatteryPowerInput) #Falls ich Energie von der Batterie brauche, wieviel kann mir diese liefern?
                        
                        Estatus_system = Estatus_house - electricity_electH2stor_consumption_system - BatteryAcBatteryPower #Strombedarf vom Netz ergibt sich aus PV Produktion + Batteriezuschuss - Verbrauch Elektrolyseur - Verbrauch Wasserstoffspeicher
                        
                        self.state.electricity_to_or_from_grid = Estatus_system

                    
                    elif Estatus_house <= 0: #Es ist kein PV Strom mehr übrig UND der Hausstrombedarf ist eventuell auch nicht gedeckt
                        
                        status_batteryWish = -electricity_electH2stor_consumption_system #Überprüfen ob Batterie den Stromverbrauch Elektrolyseur + Wasserstoffspeicher decken kann?
                        stsv.set_output_value(self.BatteryLoadingPowerWishOutput, status_batteryWish)
                        BatteryAcBatteryPower = stsv.get_input_value(self.BatteryAcBatteryPowerInput) #Falls ich Energie von der Batterie brauche, wieviel kann mir diese liefern?


                        self.state.electricity_to_or_from_grid = Estatus_house - electricity_electH2stor_consumption_system - BatteryAcBatteryPower 
                    else:
                        print("ERROR IM ENERGYMANAGEMENT-SYSTEM")
                        breakpoint()
                    #self.state.part_pv_to_grid = self.state.electricity_to_or_from_grid
                    
                    if self.state.electricity_to_or_from_grid > 0: 
                        self.state.part_pv_to_grid = self.state.electricity_to_or_from_grid
                    else:
                        self.state.part_pv_to_grid= 0.0
                    
                    self.state.electricity_from_Battery_to_CHP_inStandby = 0.0
                

                # [x] Ich muss BRennstoffzellenbetrieb in Abhängigkeit von der Saison feststellen --> im Szenario 1b war dies durch die Energieflüsse schon gegeben!
                else:  #Fuel Cell season is --> Fuel Cell is on!


                    ##Sind wir im Brennstoffzellenebetrieb oder ist gar nichts im Betrieb? -->  Batterie deckt auch Haus Bedarf, wird aber nur von Überschussstrom der CHP aufgeladen!
                    #print(timestep)
                    Estatus_house = 0.0
                    Estatus_CHP_ElectricityStandbyEnergy = 0.0
                    
                    Estatus_house = General_PhotovoltaicDelivery - General_ElectricityConsumptiom ##Decke zuerst mit der PV den Strombedarf des Hauses 

                    self.state.part_pv_to_grid = 0.0
                    if Estatus_house > 0: #Ist ein Überschussstrom vom PV Strom nach Abzug des Strombederafs des Hauses noch vorhanden?
                        self.state.part_pv_to_grid = Estatus_house #WEnn ja, dieser PV-Überschussstrom geht ins Netz....
                        Estatus_house = 0 #Haustrombedarf ist damit auch auf jeden Fall gedeckt....

                    Estatus_CHP_ElectricityStandbyEnergy = self.state.part_pv_to_grid - CHP_ElectricityStandbyEnergy

                    if self.state.part_pv_to_grid > CHP_ElectricityStandbyEnergy:  #Ist ein Überschussstrom vom PV Strom nach Abzug des Strombederafs des Hauses UND des Standby Energieverbrauchs Fuel Cells noch vorhanden?
                        #PV liefert mehr Strom als CHP verbrauchen kann!
                        self.state.part_pv_to_grid = Estatus_CHP_ElectricityStandbyEnergy #WEnn ja, dieser PV-Überschussstrom geht ins Netz....
                        Estatus_CHP_ElectricityStandbyEnergy = 0 #Standby Energiebedarf ist damit auch auf jeden Fall gedeckt....
                    
                    elif self.state.part_pv_to_grid >  0 and self.state.part_pv_to_grid < CHP_ElectricityStandbyEnergy: #PV deckt nur Teil des CHP Stormbedarfs ab--> daher muss man den Stromanteil von PV ins Netz Null setzen!
                        #PV liefert Überschuss aber weniger als CHP braucht --> daher kein Überschussstrom von PV ins Netz

                        self.state.part_pv_to_grid = 0  

                    #Zählen wir den restlichen Hausstrombedarf mit dem Bedarf für den Wasserstoffspeicher zusammen und schauen, wieviel uns die Brennstoffzelle liefert oder im Standby verbraucht!
                    status_batteryWish = Estatus_house + Estatus_CHP_ElectricityStandbyEnergy - H2Storage_ElectricityConsumption + CHP_ElectricityDelivery
                    stsv.set_output_value(self.BatteryLoadingPowerWishOutput, status_batteryWish)
                    BatteryAcBatteryPower = stsv.get_input_value(self.BatteryAcBatteryPowerInput) #Falls ich Energie von der Batterie brauche, wieviel kann mir diese liefern?

                    
                    Estatus_total = Estatus_house + Estatus_CHP_ElectricityStandbyEnergy - H2Storage_ElectricityConsumption + CHP_ElectricityDelivery - BatteryAcBatteryPower


                    self.state.electricity_to_or_from_grid = Estatus_total +  self.state.part_pv_to_grid
                    
                    


                    self.state.electricity_from_CHP_to_house,self.state.electricity_from_Battery_to_house,self.state.electricity_from_Battery_to_CHP_inStandby, self.state.electricity_from_CHP_to_battery, self.state.electricity_from_CHP_to_grid = electricity_from_CHP_Battery_to_houseFct(CHP_ElectricityDelivery, BatteryAcBatteryPower,self.state.electricity_to_or_from_grid, H2Storage_ElectricityConsumption,CHP_ElectricityStandbyEnergy,Estatus_CHP_ElectricityStandbyEnergy,timestep)

                self.processed_state = self.state.clone()
        
        BatteryAcBatteryPower = stsv.get_input_value(self.BatteryAcBatteryPowerInput) #Falls ich Energie von der Batterie brauche, wieviel kann mir diese liefern?

        # if timestep == 7549:
        #      print(timestep)
        #      print('Battery:', BatteryAcBatteryPower, '  battery wish: ', status_batteryWish)
        #      print('To or from grid',self.state.electricity_to_or_from_grid,'  fRom chp to house ', self.state.electricity_from_CHP_to_house,'  from battery to house ',self.state.electricity_from_Battery_to_house, '  part pv to grid',self.state.part_pv_to_grid, '  from battery to chp in standby',self.state.electricity_from_Battery_to_CHP_inStandby )
    
        
        stsv.set_output_value(self.electricity_to_or_from_gridOutput, self.state.electricity_to_or_from_grid)
        stsv.set_output_value(self.electricity_from_CHP_to_houseOutput, self.state.electricity_from_CHP_to_house)
        stsv.set_output_value(self.electricity_from_Battery_to_houseOutput, self.state.electricity_from_Battery_to_house)
        stsv.set_output_value(self.electricity_from_PV_to_gridOutput, self.state.part_pv_to_grid)
        stsv.set_output_value(self.electricity_frombattery_to_CHP_inStandby,self.state.electricity_from_Battery_to_CHP_inStandby)
        
        stsv.set_output_value(self.electricity_from_electricity_from_CHP_to_batteryOutput,   self.state.electricity_from_CHP_to_battery)
        stsv.set_output_value(self.electricity_from_electricity_from_CHP_to_gridOutput,   self.state.electricity_from_CHP_to_grid)

    
    
    
    def write_to_report(self):
        """Writes the information of the current component to the report."""
        return self.config.get_string_dict()
    
