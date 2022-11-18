
# Import packages from standard library or the environment e.g. pandas, numpy etc.
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from typing import List, Any
# Import modules from HiSim
from hisim import component as cp
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim.components.building import Building
from hisim.components import controller_l1_heatpump
from hisim import log
__authors__ = "Johanna Ganglbauer - johanna.ganglbauer@4wardenergy.at"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

@dataclass_json
@dataclass
class HeatSourceConfig:
    """
    HeatSource Config
    """
    name: str
    source_weight: int
    fuel : lt.LoadTypes
    power_th : float
    efficiency : float

    def __init__(self,
                 name: str,
                 source_weight: int,
                 fuel: lt.LoadTypes,
                 power_th: float,
                 efficiency: float) -> None:
        self.name = name
        self.source_weight = source_weight
        self.fuel = fuel
        self.power_th = power_th
        self.efficiency = efficiency
        
    @staticmethod
    def get_default_config_heating() -> Any:
        config = HeatSourceConfig(name='HeatingHeatSource', source_weight=1, fuel=lt.LoadTypes.DISTRICTHEATING, 
                                  power_th = 6200, efficiency=1.0) 
        return config
    
    @staticmethod
    def get_default_config_waterheating() -> Any:
        config = HeatSourceConfig(name='DHWHeatSource', source_weight=1, fuel=lt.LoadTypes.DISTRICTHEATING,
                                  power_th = 3000, efficiency=1.0 ) 
        return config
            
class HeatSourceState:
    """
    This data class saves the state of the CHP.
    """

    def __init__( self, state : int = 0 ):
        self.state = state
        
    def clone( self ):
        return HeatSourceState( state = self.state )

class HeatSource( cp.Component ):
    """
    District heating implementation. District Heating transmitts heat with given efficiency.
    District heating is controlled with a HeatSourceTargetPercentage control oscillating within the comfort temperature band.
    """
    
    # Inputs
    L1HeatSourceTargetPercentage = "L1HeatSourceTargetPercentage"

    # Outputs
    ThermalPowerDelivered = "ThermalPowerDelivered"
    FuelDelivered = "FuelDelivered"

    def __init__(self, my_simulation_parameters: SimulationParameters, config: HeatSourceConfig) -> None:
        """
        Parameters
        ----------
        max_power : int
            Maximum power of district heating.
        efficiency : float
            Efficiency of heat transfer
        """
        
        super( ).__init__(config.name + '_w' + str(config.source_weight), my_simulation_parameters = my_simulation_parameters)
        
        #introduce parameters of district heating
        self.name = config.name
        self.source_weight = config.source_weight
        self.fuel = config.fuel
        self.power_th = config.power_th
        self.efficiency = config.efficiency
        self.state = HeatSourceState( )
        self.previous_state = HeatSourceState( )

        # Inputs - Mandatories
        self.l1_heatsource_taget_percentage: cp.ComponentInput = self.add_input(
            self.component_name, self.L1HeatSourceTargetPercentage, lt.LoadTypes.ANY, lt.Units.PERCENT, mandatory=True)

        # Outputs 
        self.ThermalPowerDeliveredC : cp.ComponentOutput = self.add_output( 
            object_name=self.component_name, field_name=self.ThermalPowerDelivered, load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT, postprocessing_flag=[lt.InandOutputType.THERMAL_PRODUCTION])
        self.FuelDeliveredC: cp.ComponentOutput = self.add_output(
            object_name=self.component_name, field_name=self.FuelDelivered, load_type=self.fuel,
            unit=lt.Units.ANY, postprocessing_flag=[lt.InandOutputType.FUEL_CONSUMPTION])
        
        if config.fuel == lt.LoadTypes.OIL:
            self.FuelDeliveredC.unit = lt.Units.LITER        
        else:
            self.FuelDeliveredC.unit = lt.Units.WATT_HOUR
        
        self.add_default_connections(controller_l1_heatpump.L1HeatPumpController, self.get_l1_controller_default_connections())
        
    def get_l1_controller_default_connections(self) -> List[cp.ComponentConnection]:
        log.information("setting l1 default connections in Generic Heat Source")
        connections = [ ]
        controller_classname = controller_l1_heatpump.L1HeatPumpController.get_classname()
        connections.append(cp.ComponentConnection(HeatSource.L1HeatSourceTargetPercentage, controller_classname,
                                                  controller_l1_heatpump.L1HeatPumpController.HeatControllerTargetPercentage))
        return connections

    def write_to_report( self ) -> List[str]:
        """
        Returns
        -------
        lines : list of strings
            Text to enter report.
        """
        
        lines = []
        lines.append( "Name: {}".format( self.name + str( self.source_weight ) ) )
        lines.append( "Fuel: {}".format( self.fuel ) )
        lines.append( "Power: {:4.0f} kW".format( ( self.power_th ) * 1E-3 ) )
        lines.append( 'Efficiency : {:4.0f} %'.format( ( self.efficiency ) * 100 ) )
        return lines
    
    def i_prepare_simulation(self) -> None:
        """ Prepares the simulation. """
        pass
    
    def i_save_state(self) -> None:
        self.previous_state = self.state.clone( )

    def i_restore_state(self)  -> None:
        self.state = self.previous_state.clone( )

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues)  -> None:
        pass
    
    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,  force_convergence: bool)  -> None:
        """
        Performs the simulation of the district heating model.
        """ 
        
        # Inputs
        target_percentage = stsv.get_input_value(self.l1_heatsource_taget_percentage)

        # calculate modulation
        if target_percentage > 0:
            power_modifier = target_percentage
        if target_percentage == 0:
            power_modifier = 0
        if target_percentage < 0:
            power_modifier = 0
        if power_modifier > 1:
            power_modifier = 1

        stsv.set_output_value(self.ThermalPowerDeliveredC, self.power_th * power_modifier * self.efficiency)

        if self.fuel == lt.LoadTypes.OIL:
            #conversion from Wh oil to liter oil
            stsv.set_output_value(self.FuelDeliveredC, power_modifier * self.power_th * 1.0526315789474e-4 * self.my_simulation_parameters.seconds_per_timestep / 3.6e3)
        else:
            stsv.set_output_value(self.FuelDeliveredC, power_modifier * self.power_th * self.my_simulation_parameters.seconds_per_timestep / 3.6e3)