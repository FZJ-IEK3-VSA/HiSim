from copy import deepcopy
import datetime
from typing import Any, Dict
from dataclasses import dataclass
from dataclasses_json import dataclass_json
import pandas as pd

from hisim.component import Component, ComponentConnection, ComponentInput, ComponentOutput, SingleTimeStepValues, DisplayConfig
from hisim import loadtypes
from hisim.components.configuration import PhysicsConfig
from hisim.components.simple_water_storage import SimpleDHWStorage
from hisim.components.weather import Weather
from hisim.simulationparameters import SimulationParameters
from hisim.component import ConfigBase
from oemof.thermal.solar_thermal_collector import flat_plate_precalc

__authors__ = "Kristina Dabrock"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Kristina Dabrock"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Kristina Dabrock"
__email__ = "k.dabrock@fz-juelich.de"
__status__ = "development"


@dataclass_json
@dataclass
class SolarThermalSystemConfig(ConfigBase):
    """Configuration of the SolarThermalSystem component."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return SolarThermalSystem.get_full_classname()

    building_name: str
    name: str
    coordinates: Dict[str, float]

    # Module configuration
    azimuth: float
    tilt: float
    area_m2: float # m2
    eta_0: float
    a_1_w_m2_k: float # W/(m2*K)
    a_2_w_m2_k: float # W/(m2*K2)
    delta_temperature_n_k = 10 # K 

    # Whether the system is used to support space heating in addition
    # to water heating
    heating_support: bool

    # Weight of component, defines hierachy in control. The default is 1.
    source_weight: int

    @classmethod
    def get_default_solar_thermal_system(
        cls,
        building_name: str = "BUI1",
    ) -> Any:
        """Gets a default SolarThermalSystem."""
        return SolarThermalSystemConfig(
            building_name=building_name,
            coordinates={"latitude": 50.78,
                         "longitude": 6.08},
            name="SolarThermalSystem",
            azimuth=180.,
            tilt=30.,
            area_m2=1.5, #m2
            # These values are taken from the Excel sheet that can be downloaded from
            # http://www.estif.org/solarkeymarknew/the-solar-keymark-scheme-rules/21-certification-bodies/certified-products/58-collector-performance-parameters
            # Values were determined by changing eta_0, a_1, and a_2 so that the curve
            # fits with the typical flat plat curve
            eta_0 = 0.78,
            a_1_w_m2_k = 3.2, # W/(m2*K)
            a_2_w_m2_k = 0.015, # W/(m2*K2)
            heating_support=False,
            source_weight=1
        )


class SolarThermalSystem(Component):
    """Solar thermal system.

    This class represents a solar thermal system that can be used
    for warm water and space heating.
    """

    # Inputs
    TemperatureOutsideDegC = "TemperatureOutsideDegC"
    DiffuseHorizontalIrradianceWM2 = "DiffuseHorizontalIrradianceWM2"
    GlobalHorizontalIrradianceWM2 = "GlobalHorizontalIrradianceWM2"
    Azimuth = "Azimuth"
    ApparentZenith = "ApparentZenith"
    TemperatureCollectorInletDegC = "TemperatureCollectorInletDegC"

    # Outputs
    ThermalPowerOutput = "ThermalPowerOutput"
    ThermalEnergyOutput = "ThermalEnergyOutput"
    WaterMassFlowOutput = "WaterMassFlowOutput"
    WaterTemperatureOutput = "WaterTemperatureOutput"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: SolarThermalSystemConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Constructs all the neccessary attributes."""
        self.componentnameconfig = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        # If a component requires states, this can be implemented here.
        self.state = SolarThermalSystemState()
        self.previous_state = deepcopy(self.state)
        # Initialized variables
        self.factor = 1.0

        # Add inputs
        self.t_out_channel: ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureOutsideDegC,
            loadtypes.LoadTypes.TEMPERATURE,
            loadtypes.Units.CELSIUS,
            True,
        )

        self.dhi_channel: ComponentInput = self.add_input(
            self.component_name,
            self.DiffuseHorizontalIrradianceWM2,
            loadtypes.LoadTypes.IRRADIANCE,
            loadtypes.Units.WATT_PER_SQUARE_METER,
            True,
        )

        self.ghi_channel: ComponentInput = self.add_input(
            self.component_name,
            self.GlobalHorizontalIrradianceWM2,
            loadtypes.LoadTypes.IRRADIANCE,
            loadtypes.Units.WATT_PER_SQUARE_METER,
            True,
        )

        self.azimuth_channel: ComponentInput = self.add_input(
            self.component_name, self.Azimuth, loadtypes.LoadTypes.ANY, loadtypes.Units.DEGREES, True
        )

        self.apparent_zenith_channel: ComponentInput = self.add_input(
            self.component_name,
            self.ApparentZenith,
            loadtypes.LoadTypes.ANY,
            loadtypes.Units.DEGREES,
            True,
        )

        self.water_temperature_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureCollectorInletDegC,
            loadtypes.LoadTypes.TEMPERATURE,
            loadtypes.Units.CELSIUS,
            True,
        )

        # Add outputs
        self.thermal_power_w_output_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalPowerOutput,
            load_type=loadtypes.LoadTypes.HEATING,
            unit=loadtypes.Units.WATT,
            postprocessing_flag=[loadtypes.InandOutputType.WATER_HEATING], # TODO is this needed?
            output_description="Thermal power output [W]",
        )

        self.thermal_energy_wh_output_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalEnergyOutput,
            load_type=loadtypes.LoadTypes.HEATING,
            unit=loadtypes.Units.WATT_HOUR,
            output_description="Thermal energy output [Wh]",
            postprocessing_flag=[loadtypes.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )

        self.water_mass_flow_kg_s_output_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.WaterMassFlowOutput,
            load_type=loadtypes.LoadTypes.WATER,
            unit=loadtypes.Units.KG_PER_SEC,
            output_description="Mass flow of heat transfer liquid [kg/s]",
        )
        self.water_temperature_deg_c_output_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.WaterTemperatureOutput,
            load_type=loadtypes.LoadTypes.WATER,
            unit=loadtypes.Units.CELSIUS,
            output_description="Output temperature of heat transfer liquid [°C]",
        )

        # Only if system is used to support heating in addition to warm water preparation
        # self.thermal_power_delicered_channel: ComponentOutput = self.add_output(
        #     object_name=self.component_name,
        #     field_name=self.ThermalPowerDelivered,
        #     load_type=loadtypes.LoadTypes.HEATING,
        #     unit=loadtypes.Units.WATT,
        #     postprocessing_flag=[loadtypes.InandOutputType.WATER_HEATING], # TODO is this needed?
        #     output_description="Thermal Power Delivered",
        # )
        self.add_default_connections(self.get_default_connections_from_simple_hot_water_storage())
        self.add_default_connections(self.get_default_connections_from_weather())


    def get_default_connections_from_simple_hot_water_storage(self,):
        """Get simple_water_storage default connections."""

        connections = []
        storage_classname = SimpleDHWStorage.get_classname()
        connections.append(
            ComponentConnection(
                SolarThermalSystem.TemperatureCollectorInletDegC,
                storage_classname,
                SimpleDHWStorage.WaterTemperatureToHeatGenerator,
            )
        )
        return connections
    
    def get_default_connections_from_weather(self):
        """Get default connections from weather."""

        connections = []
        weather_classname = Weather.get_classname()
        connections.append(
            ComponentConnection(
                SolarThermalSystem.TemperatureOutsideDegC,
                weather_classname,
                Weather.TemperatureOutside,
            )
        )
        connections.append(
            ComponentConnection(
                SolarThermalSystem.GlobalHorizontalIrradianceWM2,
                weather_classname,
                Weather.GlobalHorizontalIrradiance,
            )
        )
        connections.append(
            ComponentConnection(
                SolarThermalSystem.DiffuseHorizontalIrradianceWM2,
                weather_classname,
                Weather.DiffuseHorizontalIrradiance,
            )
        )
        connections.append(ComponentConnection(SolarThermalSystem.Azimuth, weather_classname, Weather.Azimuth))
        connections.append(ComponentConnection(SolarThermalSystem.ApparentZenith, weather_classname, Weather.ApparentZenith))
        return connections

    def i_save_state(self) -> None:
        """Saves the current state."""
        self.previous_state = deepcopy(self.state)

    def i_restore_state(self) -> None:
        """Restores previous state."""
        self.state = deepcopy(self.previous_state)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates the component."""
        # define local variables
        global_horizontal_irradiance_w_m2 = stsv.get_input_value(self.ghi_channel)
        diffuse_horizontal_irradiance_w_m2 = stsv.get_input_value(self.dhi_channel)
        outside_temperature_deg_c = stsv.get_input_value(self.t_out_channel)
        temperature_collector_inlet_deg_c = stsv.get_input_value(self.t_out_channel)

        # calculate collectors heat
        # Some more info on equation: http://www.estif.org/solarkeymarknew/the-solar-keymark-scheme-rules/21-certification-bodies/certified-products/58-collector-performance-parameters
        time_ind = self.my_simulation_parameters.start_date + datetime.timedelta(0, self.my_simulation_parameters.seconds_per_timestep * timestep)
        
        precalc_data = flat_plate_precalc(
            lat=self.config.coordinates["latitude"],
            long=self.config.coordinates["longitude"],
            collector_tilt=self.config.tilt,
            collector_azimuth=self.config.azimuth,
            eta_0=self.config.eta_0, # optical efficiency of the collector
            a_1=self.config.a_1_w_m2_k, # thermal loss parameter 1
            a_2=self.config.a_2_w_m2_k, # thermal loss parameter 2
            temp_collector_inlet=temperature_collector_inlet_deg_c, # collectors inlet temperature
            delta_temp_n=self.config.delta_temperature_n_k, # temperature difference between collector inlet and mean temperature 
            irradiance_global=pd.Series(global_horizontal_irradiance_w_m2, index=[time_ind]),
            irradiance_diffuse=pd.Series(diffuse_horizontal_irradiance_w_m2, index=[time_ind]),
            temp_amb=pd.Series(outside_temperature_deg_c, index=[time_ind]),
        )

        # TODO include transformer (heat losses in pipes, required electricity etc)

        # write values for output time series
        thermal_power_output_w = precalc_data["collectors_heat"].iloc[0] * self.config.area_m2
        thermal_energy_output_wh = (
            thermal_power_output_w * self.my_simulation_parameters.seconds_per_timestep / 3.6e3
        )

        water_temperature_output_deg_c = self.config.delta_temperature_n_k + stsv.get_input_value(
            self.water_temperature_input_channel
        )
        mass_flow_output_kg_s = thermal_power_output_w / (
            PhysicsConfig.get_properties_for_energy_carrier(
            energy_carrier=loadtypes.LoadTypes.WATER
        ).specific_heat_capacity_in_joule_per_kg_per_kelvin * self.config.delta_temperature_n_k
        )

        stsv.set_output_value(self.thermal_power_w_output_channel, thermal_power_output_w)
        stsv.set_output_value(self.thermal_energy_wh_output_channel, thermal_energy_output_wh)
        stsv.set_output_value(
            self.water_temperature_deg_c_output_channel, water_temperature_output_deg_c,
        )
        stsv.set_output_value(self.water_mass_flow_kg_s_output_channel, mass_flow_output_kg_s)


@dataclass
class SolarThermalSystemState:
    """The data class saves the state of the simulation results.

    Parameters
    ----------
    output_with_state : int
    Stores the state of the output_with_state value from
    :py:class:`~hisim.component.ComponentName`.

    """

    output_with_state: float = 0
