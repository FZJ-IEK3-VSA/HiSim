# This module contains a simplified buildings component that follows the 1R1C approach.
# This means simplest possible modeling, one thermal zone with a thermal capacity and
# only one conducting connection to the outside.

# ! author Felix

# Imports

# Generic/Built-in
import importlib
from typing import List
from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Owned
from HiSim.hisim.components.loadprofilegenerator_utsp_connector import UtspLpgConnector
from HiSim.hisim.components.weather import Weather
from hisim import component as cp 
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters


@dataclass_json
@dataclass
class Building1R1CConfig(cp.ConfigBase):
    """Configuration of the 1R1C building component.
    
    Attributes:
        building_name (str): If simulating multiple buildings, differentiate them with this. Default: BUI1
        name (str): 
    """

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return Building1R1C.get_full_classname()

    building_name: str = "BUI1"
    name: str = "Building1R1C"
    u_value: float = 0.5
    area: float = 100.0
    thermal_capacity: float = 100000.0
    initial_internal_temperature: float = 20.0





class Building1R1C(cp.Component):
    """This component models a building with one thermal zone and one thermal connection to the outside (1R1C approach).
    
    It has a single thermal capacity and a single heat transfer coefficient to the outside.\\
    It can receive thermal power from a number of different sources and uses the ambient
    temperature as a boundary condition for the heat loss to the outside.\\
    The model further includes solar gain through windows.

    The outputs are the theoretical thermal demand, the internal temperature and the solar gain
    through windows (if solar radiation inputs are used).

    The model was built to be similar to the Building component. However, it lacks the inputs
    ThermalPowerCHP and BuildingTemperatureModifier (from an EMS). It also lacks a lot of outputs
    f.ex. due to those outputs being related to multiple zones (which this model doesn't have).

    ### Inputs:
        Only one input is necessary, strictly speaking: TemperatureOutside, which denotes ambient
        temperature. Everything else is optional.<br>

        There are three inputs for thermal power that is supplied internally: ThermalPowerInput,
        ThermalPowerInput, and HeatingByDevices. These three exist for compatibility purposes with
        the other HiSim components.
        - ThermalPowerInput is supposed to be connected to
            heat_distribution_system.HeatDistribution.ThermalPowerDelivered
        - HeatingByResidents is supposed to be connected to
            loadprofilegenerator_utsp_connector.UtspLpgConnector.HeatingByResidents
        - HeatingByDevices is supposed to be connected to
            loadprofilegenerator_utsp_connector.UtspLpgConnector.HeatingByDevices
        But really, it all goes into the same sum anyways, so you can use these however you please.
        <br>

        There are seven additional inputs for the calculation of solar gains through windows. All
        of these are supposed to be connected to the weather.Weather components' outputs of the
        same name. These are: Altitude, Azimuth, ApparentZenith, DirectNormalIrradiance, 
        DirectNormalIrradianceExtra, DiffuseHorizontalIrradiance, and GlobalHorizontalIrradiance.
        If you ignore these, solar gains are simply not included in the simulation.
    
    ### Outputs:
        This component outputs
        - TheoreticalThermalBuildingDemand, a theoretical demand that is supposed to connect to
            both the heat_distribution_system.HeatDistribution and 
            heat_distribution_system.HeatDistributionController inputs of the same name.
        - InternalTemperature, the current temperature of the one internal zone. It is supposed to
            connect to heat_distribution_system.HeatDistribution -> ResidenceTemperatureIndoorAir.
        - SolarGainThroughWindows, the calculated heat gain through windows.

    ### Creating an instance of Building1R1C:
        - documentation to be added ...
    """

    """ TODO: List (this is my todo list for this component)
    - Decide on Inputs and Outputs
    - Write Inputs and outputs to __init__
    - write i_simulate
    """

    # --------------------------------------------------------------------------------------------
    # ----- member variables ---------------------------------------------------------------------
    # --------------------------------------------------------------------------------------------

    # Inputs: Ambient temperature
    TemperatureOutside = "TemperatureOutside"

    # Inputs -> Heating
    ThermalPowerDelivered = "ThermalPowerDelivered"
    HeatingByResidents = "HeatingByResidents"
    HeatingByDevices = "HeatingByDevices"

    # Inputs -> Solar influences
    Altitude = "Altitude" # also called elevation, optional
    Azimuth = "Azimuth"
    ApparentZenith = "ApparentZenith"
    DirectNormalIrradiance = "DirectNormalIrradiance"
    DirectNormalIrradianceExtra = "DirectNormalIrradianceExtra"
    DiffuseHorizontalIrradiance = "DiffuseHorizontalIrradiance"
    GlobalHorizontalIrradiance = "GlobalHorizontalIrradiance"

    # Outputs
    TheoreticalThermalBuildingDemand = "TheoreticalThermalBuildingDemand"
    InternalTemperature = "InternalTemperature" # TemperatureIndoorAir
    SolarGainThroughWindows = "SolarGainThroughWindows"


    # other member variables
    my_config: Building1R1CConfig
    my_simulation_parameters: SimulationParameters
    state: float  # the state of the building, namely: current internal temperature
    previous_state: float # whatever the state was before the current time step
    input_channels: dict[str, cp.ComponentInput] = {}
    output_channels: dict[str, cp.ComponentOutput] = {}
    # ... other stuff from the superclass ...


    # --------------------------------------------------------------------------------------------
    # ----- init and init supporters -------------------------------------------------------------
    # --------------------------------------------------------------------------------------------

    def __init__(
        self, 
        my_simulation_parameters: SimulationParameters,
        my_config: Building1R1CConfig,
        my_display_config=cp.DisplayConfig()
    ) -> None:
        """I'm very sorry, but you're gonna have to provide a config. Luckily, the config class
        offers quite a bunch of standard configs, so that should be easy enough. Check there!"""
        # set basic attributes
        self.my_config = my_config
        self.my_simulation_parameters = my_simulation_parameters
        self.component_name = self.get_component_name()
        # call super init
        super().__init__(name=self.component_name, 
                         my_simulation_parameters=my_simulation_parameters,
                         my_config=my_config,
                         my_display_config=my_display_config)
        # set state
        self.state = my_config.initial_internal_temperature
        self.previous_state = self.state
        # add inputs and outputs and connect default connections
        self.add_inputs_and_outputs()
        self.add_default_connections(self.get_all_default_connections())


    def add_inputs_and_outputs(self):
        """Function to encapsulate the adding of input and output channels."""
        # List of all the inputs, loadtypes, units, and whether they are mandatory
        # These are exactly the parameters that self.add_input() needs.
        inputs = [
            ["TemperatureOutside", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, True],
            ["ThermalPowerDelivered", lt.LoadTypes.HEATING, lt.Units.WATT, False],
            ["HeatingByResidents", lt.LoadTypes.HEATING, lt.Units.WATT, False],
            ["HeatingByDevices", lt.LoadTypes.HEATING, lt.Units.WATT, False],
            ["Altitude", lt.LoadTypes.ANY, lt.Units.DEGREES, False],
            ["Azimuth", lt.LoadTypes.ANY, lt.Units.DEGREES, False],
            ["ApparentZenith", lt.LoadTypes.ANY, lt.Units.DEGREES, False],
            ["DirectNormalIrradiance", lt.LoadTypes.IRRADIANCE, lt.Units.WATT_PER_SQUARE_METER, False],
            ["DirectNormalIrradianceExtra", lt.LoadTypes.IRRADIANCE, lt.Units.WATT_PER_SQUARE_METER, False],
            ["DiffuseHorizontalIrradiance", lt.LoadTypes.IRRADIANCE, lt.Units.WATT_PER_SQUARE_METER, False],
            ["GlobalHorizontalIrradiance", lt.LoadTypes.IRRADIANCE, lt.Units.WATT_PER_SQUARE_METER, False],
        ]
        # The same for the outputs except we don't need the mandatory flag
        outputs = [
            ["TheoreticalThermalBuildingDemand", lt.LoadTypes.HEATING, lt.Units.WATT,],
            ["InternalTemperature", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS],
            ["SolarGainThroughWindows", lt.LoadTypes.HEATING, lt.Units.WATT,],
        ]
        # add the inputs and outputs
        for i in inputs:
            self.input_channels[i[0]] = self.add_input(self.component_name, i[0], i[1], i[2], i[3])
        for o in outputs:
            self.output_channels[o[0]] = self.add_output(self.component_name, o[0], o[1], o[2])


    def get_all_default_connections(self):
        """Get weather default connnections."""
        # get classes and classnames - if necessary, use importlib to avoid circular import errors
        weather_classname = Weather.get_classname()
        utsp_classname = UtspLpgConnector.get_classname()
        hds_module = importlib.import_module("hisim.components.heat_distribution_system")
        hds_class = getattr(hds_module, "HeatDistribution")
        hds_classname = hds_class.get_classname()
        # define all connections target input name, source classname, source output name
        connections = [
            [Building1R1C.Altitude, weather_classname, Weather.Altitude],
            [Building1R1C.Azimuth, weather_classname, Weather.Azimuth],
            [Building1R1C.ApparentZenith, Weather.ApparentZenith],
            [Building1R1C.DirectNormalIrradiance, weather_classname, Weather.DirectNormalIrradiance],
            [Building1R1C.DirectNormalIrradianceExtra, weather_classname, Weather.DirectNormalIrradianceExtra],
            [Building1R1C.DiffuseHorizontalIrradiance, weather_classname, Weather.DiffuseHorizontalIrradiance],
            [Building1R1C.GlobalHorizontalIrradiance, weather_classname, Weather.GlobalHorizontalIrradiance],
            [Building1R1C.TemperatureOutside, weather_classname, Weather.TemperatureOutside],
            [Building1R1C.HeatingByResidents, utsp_classname, UtspLpgConnector.HeatingByResidents],
            [Building1R1C.HeatingByDevices, utsp_classname, UtspLpgConnector.HeatingByDevices],
            [Building1R1C.ThermalPowerDelivered, hds_classname, hds_class.ThermalPowerDelivered]
        ]
        return [cp.ComponentConnection(c[0], c[1], c[2]) for c in connections]



    # --------------------------------------------------------------------------------------------
    # ----- "i-functions" ------------------------------------------------------------------------
    # --------------------------------------------------------------------------------------------

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_state = self.state

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.state = self.previous_state

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass


    # Todo
    def write_to_report(self) -> List[str]:
        """Write to report."""
        lines = []
        lines.append(f"Building 1R1C model: {self.config.name}")
        lines.append(f"U-value: {self.my_config.u_value}")
        lines.append(f"Area: {self.my_config.area}")
        lines.append(f"Thermal Capacity: {self.my_config.thermal_capacity}")
        return lines


    # Todo
    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates the component."""
        # preparation
        thermal_sources = [self.ThermalPowerDelivered, self.HeatingByResidents, self.HeatingByDevices]
        solar_sources = [self.Altitude, self.Azimuth, self.ApparentZenith, 
                         self.DirectNormalIrradiance, self.DirectNormalIrradianceExtra,
                         self.DiffuseHorizontalIrradiance, self.GlobalHorizontalIrradiance]
        
        # get inputs and cache state. WARNING: the shorthand get_input_value() function relies on
        # stsv being cached to self.stsv - but this is otherwise highly obscure, therefore it gets
        # deleted immediately after use. DO NOT use this at ANY other point in this class!
        self.stsv: cp.SingleTimeStepValues = stsv
        temperature_outside = self.get_input_value(self.TemperatureOutside)
        thermal_power_inputs = {s: self.get_input_value(s) for s in thermal_sources}
        solar_inputs = {s: self.get_input_value(s) for s in solar_sources}
        del self.stsv # immediately delete the stsv cache again
        previous_internal_temperature = self.state

        # do the calculations # Todo: Write those functions
        theoretical_heat_demand = self.calc_theoretical_heat_demand()
        solar_power_input = self.calc_solar_power_input(**solar_inputs)
        thermal_power_input = sum(thermal_power_inputs.values()) + solar_power_input
        new_internal_temperature = self.calc_new_internal_temperature(thermal_power_input, previous_internal_temperature, temperature_outside)

        # set outputs and new state
        self.state = new_internal_temperature
        stsv.set_output_value(self.output_channels[self.TheoreticalThermalBuildingDemand], theoretical_heat_demand)
        stsv.set_output_value(self.output_channels[self.InternalTemperature], new_internal_temperature)
        stsv.set_output_value(self.output_channels[self.SolarGainThroughWindows], solar_power_input)

    # --------------------------------------------------------------------------------------------
    # ----- helpers ------------------------------------------------------------------------------
    # --------------------------------------------------------------------------------------------

    def get_input_value(self, channel_name: str) -> float:
        """Shorthand method to get an input value from the stsv based on the name.

        WARNING: This relies on stsv being cached! ONLY use this in the very beginning of the
        i_simulate() function, where this is the case!
        """
        return self.stsv.get_input_value(self.input_channels[channel_name])


    def calc_theoretical_heat_demand(self) -> float:
        """Calculates the theoretical heat demand of the building."""
        raise NotImplementedError


    def calc_solar_power_input(self, **kwargs) -> float:
        """Calculates the solar heat gain through windows."""
        raise NotImplementedError


    def calc_new_internal_temperature(self, thermal_power_input, previous_internal_temperature, temperature_outside) -> float:
        """Calculates the new internal temperature."""
        # Todo check this - this is not ready, don't assume it is either!
        temperature_gain_input = thermal_power_input / self.my_config.thermal_capacity
        heat_loss_ambient = self.my_config.u_value * self.my_config.area * (previous_internal_temperature - temperature_outside) 
        temperature_loss_ambient = heat_loss_ambient / self.my_config.thermal_capacity
        new_internal_temperature = previous_internal_temperature + temperature_gain_input - temperature_loss_ambient
        return new_internal_temperature
