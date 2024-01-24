"""Building module.

This module simulates the thermal behaviour of a building using reference data from the EPISCOPE/TABULA project and
a resistor-capacitor (RC) model from the RC_BuildingSimulator project and the ISO 13790 norm.

The module contains the following classes:
    1. class BuildingState - constructs the building state.
    2. class BuildingControllerState - constructs the building controller state.
    3. class BuildingConfig - json dataclass, configurates the building class.
    4. class BuildingControllerConfig -  json dataclass, configurates the building controller class.
    5. class Building -  main class, involves building properties taken from EPISCOPE/TABULA project database* and
       calculations from RC_BuildingSimulator project**. Inputs of the building class are the heating device, occupancy and weather.
       Outputs are for example temperature, stored energy and solar gains through windows.
    6. class Window - taken from the RC simulator project, calculates for example solar gains through windows.
    7. class BuildingInformation - gets important building parameters and properties

*EPISCOPE/TABULA project:
     Ths project involves a collection of multiple typologies of residences from 12 European countries, listing among others,
     heat coefficient, area, volumes, light transmissibility, house heat capacity for various residence construction elements.
     These typologies are categorized by year of construction, residence type and degree of refurbishment.
     For information, please access site: https://episcope.eu/building-typology/webtool/

**RC_BuildingSimulator project:
    The functions cited in this module are at some degree based on the RC_BuildingSimulator project:
    [rc_buildingsimulator-jayathissa]:
    [1] Jayathissa, Prageeth, et al. "Optimising building net energy demand with dynamic BIPV shading." Applied Energy 202 (2017): 726-735.
    The implementation of the RC_BuildingSimulator project can be found under the following repository:
    https://github.com/architecture-building-systems/RC_BuildingSimulator

    The RC model (5R1C model) is based on the EN ISO 13790 standard and explains thermal physics with the help of an electrical circuit analogy.
    Principal components of this model are the heat fluxes Phi which are analogous to the electrical current I,
    the thermal conductances H which are the inverse of the thermal resistance and analogous to the electrical conductance G (=1/R),
    the thermal capacitance C analogous to the electrical capacitance C and
    the temperatures T which are analogous to the electrical voltage V.

*** Another paper using the 5R1C model from EN ISO 13790:
    [2] I. Horvat et al. "Dynamic method for calculating energy need in HVAC systems." Transactions of Famena 40 (2016): 47-62.

"""
# clean

# Generic/Built-in
import importlib
from typing import List, Any, Optional, Tuple
from functools import lru_cache
import math
from dataclasses import dataclass
from dataclasses_json import dataclass_json
import pvlib
import pandas as pd

from hisim import utils
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim.components.loadprofilegenerator_utsp_connector import UtspLpgConnector
from hisim.simulationparameters import SimulationParameters
from hisim.components.weather import Weather
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum
from obsolete import loadprofilegenerator_connector

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Dr. Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


@dataclass_json
@dataclass
class BuildingConfig(cp.ConfigBase):

    """Configuration of the Building class."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return Building.get_full_classname()

    name: str
    heating_reference_temperature_in_celsius: float
    building_code: str
    building_heat_capacity_class: str
    initial_internal_temperature_in_celsius: float
    absolute_conditioned_floor_area_in_m2: Optional[float]
    total_base_area_in_m2: Optional[float]
    number_of_apartments: Optional[float]
    predictive: bool
    set_heating_temperature_in_celsius: float
    set_cooling_temperature_in_celsius: float
    enable_opening_windows: bool

    @classmethod
    def get_default_german_single_family_home(
        cls,
        set_heating_temperature_in_celsius: float = 19.0,
        set_cooling_temperature_in_celsius: float = 24.0,
        heating_reference_temperature_in_celsius: float = -7.0,
    ) -> Any:
        """Get a default Building."""
        config = BuildingConfig(
            name="Building",
            building_code="DE.N.SFH.05.Gen.ReEx.001.002",
            building_heat_capacity_class="medium",
            initial_internal_temperature_in_celsius=23.0,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
            absolute_conditioned_floor_area_in_m2=121.2,
            total_base_area_in_m2=None,
            number_of_apartments=None,
            predictive=False,
            set_heating_temperature_in_celsius=set_heating_temperature_in_celsius,
            set_cooling_temperature_in_celsius=set_cooling_temperature_in_celsius,
            enable_opening_windows=False,
        )
        return config


class BuildingState:

    """BuildingState class."""

    def __init__(
        self,
        thermal_mass_temperature_in_celsius: float,
        thermal_capacitance_in_joule_per_kelvin: float,
    ):
        """Construct all the neccessary attributes for the BuildingState object."""
        # this is labeled as t_m in the paper [1] (** Check header)
        self.thermal_mass_temperature_in_celsius: float = thermal_mass_temperature_in_celsius

        # this is labeled as c_m in the paper [1] (** Check header)
        self.thermal_capacitance_in_joule_per_kelvin: float = thermal_capacitance_in_joule_per_kelvin

    def calc_stored_thermal_power_in_watt(
        self,
    ) -> float:
        """Calculate the thermal power stored by the thermal mass per second."""
        return (self.thermal_mass_temperature_in_celsius * self.thermal_capacitance_in_joule_per_kelvin) / 3600

    def self_copy(
        self,
    ):
        """Copy the Building State."""
        return BuildingState(
            self.thermal_mass_temperature_in_celsius,
            self.thermal_capacitance_in_joule_per_kelvin,
        )


# class Building(dynamic_component.DynamicComponent):
class Building(cp.Component):

    """Building class.

    This class calculates the thermal behaviour of the building based on the RC Simulator (** paper [1]) and the EN ISO 13790 norm (see header).
    The corresponding functions and variables are also described in the paper [2].
    Also it provides multiple typologies of residences based on the EPISCOPE/TABULA project database (* Check header).

    Parameters
    ----------
    building_code :str
        Code reference to a specific residence typology list in EPISCOPE/TABULA database
    building_heat_capacity_class: str
        Heat capacity of residence defined using one of the following terms:
            - very light
            - light
            - medium
            - heavy
            - very heavy
    initial_internal_temperature_in_celsius : float
        Initial internal temperature of residence in Celsius
    sim_params : Simulator
        Simulator object used to carry the simulation using this class

    """

    # Inputs -> heating device
    ThermalPowerDelivered = "ThermalPowerDelivered"
    ThermalPowerCHP = "ThermalPowerCHP"

    # Inputs -> occupancy
    HeatingByResidents = "HeatingByResidents"
    HeatingByDevices = "HeatingByDevices"

    # Inputs -> weather
    Altitude = "Altitude"
    Azimuth = "Azimuth"
    ApparentZenith = "ApparentZenith"
    DirectNormalIrradiance = "DirectNormalIrradiance"
    DirectNormalIrradianceExtra = "DirectNormalIrradianceExtra"
    DiffuseHorizontalIrradiance = "DiffuseHorizontalIrradiance"
    GlobalHorizontalIrradiance = "GlobalHorizontalIrradiance"
    TemperatureOutside = "TemperatureOutside"

    # Inputs -> energy management system
    BuildingTemperatureModifier = "BuildingTemperatureModifier"

    # Outputs
    TemperatureMeanThermalMass = "TemperatureMeanThermalMass"
    TemperatureInternalSurface = "TemperatureInternalSurface"
    TemperatureIndoorAir = "TemperatureIndoorAir"
    TotalEnergyToResidence = "TotalEnergyToResidence"
    SolarGainThroughWindows = "SolarGainThroughWindows"
    HeatLoss = "HeatLoss"
    TheoreticalThermalBuildingDemand = "TheoreticalThermalBuildingDemand"
    HeatFluxWallNode = "HeatFluxWallNode"
    HeatFluxThermalMassNode = "HeatFluxThermalMassNode"
    OpenWindow = "OpenWindow"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: BuildingConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
    ):
        """Construct all the neccessary attributes."""
        self.buildingconfig = config

        super().__init__(
            name=self.buildingconfig.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        # =================================================================================================================================
        # Initialization of variables

        self.set_heating_temperature_in_celsius = self.buildingconfig.set_heating_temperature_in_celsius
        self.set_cooling_temperature_in_celsius = self.buildingconfig.set_cooling_temperature_in_celsius
        self.window_open: int = 0

        (
            self.is_in_cache,
            self.cache_file_path,
        ) = utils.get_cache_file(
            self.component_name,
            self.buildingconfig,
            self.my_simulation_parameters,
        )

        self.cache: List[float]
        self.solar_heat_gain_through_windows: List[float]

        self.my_building_information = BuildingInformation(config=self.buildingconfig)
        self.build()

        self.state: BuildingState = BuildingState(
            thermal_mass_temperature_in_celsius=config.initial_internal_temperature_in_celsius,
            thermal_capacitance_in_joule_per_kelvin=self.my_building_information.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin,
        )
        self.previous_state = self.state.self_copy()

        # =================================================================================================================================
        # Input channels

        self.thermal_power_delivered_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ThermalPowerDelivered,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            False,
        )
        self.thermal_power_chp_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ThermalPowerCHP,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            False,
        )
        self.altitude_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.Altitude,
            lt.LoadTypes.ANY,
            lt.Units.DEGREES,
            True,
        )
        self.azimuth_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.Azimuth,
            lt.LoadTypes.ANY,
            lt.Units.DEGREES,
            True,
        )
        self.apparent_zenith_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ApparentZenith,
            lt.LoadTypes.ANY,
            lt.Units.DEGREES,
            True,
        )
        self.direct_normal_irradiance_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.DirectNormalIrradiance,
            lt.LoadTypes.IRRADIANCE,
            lt.Units.WATT_PER_SQUARE_METER,
            True,
        )
        self.direct_normal_irradiance_extra_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.DirectNormalIrradianceExtra,
            lt.LoadTypes.IRRADIANCE,
            lt.Units.WATT_PER_SQUARE_METER,
            True,
        )
        self.direct_horizontal_irradiance_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.DiffuseHorizontalIrradiance,
            lt.LoadTypes.IRRADIANCE,
            lt.Units.WATT_PER_SQUARE_METER,
            True,
        )
        self.global_horizontal_irradiance_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.GlobalHorizontalIrradiance,
            lt.LoadTypes.IRRADIANCE,
            lt.Units.WATT_PER_SQUARE_METER,
            True,
        )

        self.temperature_outside_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureOutside,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )

        self.occupancy_heat_gain_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HeatingByResidents,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            True,
        )

        self.device_heat_gain_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HeatingByDevices,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            True,
        )

        self.building_temperature_modifier_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.BuildingTemperatureModifier,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            mandatory=False,
        )

        # Output channels
        self.thermal_mass_temperature_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.TemperatureMeanThermalMass,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.TemperatureMeanThermalMass} will follow.",
        )
        self.internal_surface_temperature_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.TemperatureInternalSurface,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.TemperatureInternalSurface} will follow.",
        )
        self.indoor_air_temperature_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.TemperatureIndoorAir,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.TemperatureIndoorAir} will follow.",
        )
        self.total_power_to_residence_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.TotalEnergyToResidence,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.TotalEnergyToResidence} will follow.",
        )
        self.solar_gain_through_windows_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.SolarGainThroughWindows,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.SolarGainThroughWindows} will follow.",
        )

        self.heat_loss_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.HeatLoss,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.HeatLoss} will follow.",
        )
        self.theoretical_thermal_building_demand_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.TheoreticalThermalBuildingDemand,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.TheoreticalThermalBuildingDemand} will follow.",
        )
        self.heat_flow_rate_to_thermal_mass_node_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.HeatFluxThermalMassNode,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.HeatFluxThermalMassNode} will follow.",
        )
        self.heat_flow_rates_to_internal_surface_node_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.HeatFluxWallNode,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.HeatFluxWallNode} will follow.",
        )
        self.open_window_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.OpenWindow,
            lt.LoadTypes.ON_OFF,
            lt.Units.TIMESTEPS,
            output_description=f"here a description for {self.OpenWindow} will follow.",
        )

        # =================================================================================================================================
        # Add and get default connections

        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(self.get_default_connections_from_utsp_occupancy())
        self.add_default_connections(self.get_default_connections_from_hds())
        self.add_default_connections(self.get_default_connections_from_outdated_occupancy())

    def get_default_connections_from_weather(
        self,
    ):
        """Get weather default connnections."""

        connections = []
        weather_classname = Weather.get_classname()
        connections.append(
            cp.ComponentConnection(
                Building.Altitude,
                weather_classname,
                Weather.Altitude,
            )
        )
        connections.append(
            cp.ComponentConnection(
                Building.Azimuth,
                weather_classname,
                Weather.Azimuth,
            )
        )
        connections.append(
            cp.ComponentConnection(
                Building.ApparentZenith,
                weather_classname,
                Weather.ApparentZenith,
            )
        )
        connections.append(
            cp.ComponentConnection(
                Building.DirectNormalIrradiance,
                weather_classname,
                Weather.DirectNormalIrradiance,
            )
        )
        connections.append(
            cp.ComponentConnection(
                Building.DirectNormalIrradianceExtra,
                weather_classname,
                Weather.DirectNormalIrradianceExtra,
            )
        )
        connections.append(
            cp.ComponentConnection(
                Building.DiffuseHorizontalIrradiance,
                weather_classname,
                Weather.DiffuseHorizontalIrradiance,
            )
        )
        connections.append(
            cp.ComponentConnection(
                Building.GlobalHorizontalIrradiance,
                weather_classname,
                Weather.GlobalHorizontalIrradiance,
            )
        )
        connections.append(
            cp.ComponentConnection(
                Building.TemperatureOutside,
                weather_classname,
                Weather.TemperatureOutside,
            )
        )
        return connections

    def get_default_connections_from_utsp_occupancy(
        self,
    ):
        """Get UTSP default connections."""

        connections = []
        utsp_classname = UtspLpgConnector.get_classname()
        connections.append(
            cp.ComponentConnection(
                Building.HeatingByResidents,
                utsp_classname,
                UtspLpgConnector.HeatingByResidents,
            )
        )
        connections.append(
            cp.ComponentConnection(
                Building.HeatingByDevices,
                utsp_classname,
                UtspLpgConnector.HeatingByDevices,
            )
        )
        return connections

    def get_default_connections_from_outdated_occupancy(
        self,
    ):
        """Get occupancy default connections."""

        connections = []
        occupancy_classname = loadprofilegenerator_connector.Occupancy.get_classname()
        connections.append(
            cp.ComponentConnection(
                Building.HeatingByResidents,
                occupancy_classname,
                loadprofilegenerator_connector.Occupancy.HeatingByResidents,
            )
        )
        connections.append(
            cp.ComponentConnection(
                Building.HeatingByDevices,
                occupancy_classname,
                loadprofilegenerator_connector.Occupancy.HeatingByDevices,
            )
        )
        return connections

    def get_default_connections_from_hds(
        self,
    ):
        """Get heat distribution default connections."""

        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.heat_distribution_system"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "HeatDistribution")
        connections = []
        hds_classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                Building.ThermalPowerDelivered,
                hds_classname,
                component_class.ThermalPowerDelivered,
            )
        )
        return connections

    # =================================================================================================================================
    # Simulation of the building class

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the thermal behaviour of the building."""

        # Gets inputs
        if hasattr(self, "solar_gain_through_windows") is False:
            azimuth = stsv.get_input_value(self.azimuth_channel)
            direct_normal_irradiance = stsv.get_input_value(self.direct_normal_irradiance_channel)
            direct_horizontal_irradiance = stsv.get_input_value(self.direct_horizontal_irradiance_channel)
            global_horizontal_irradiance = stsv.get_input_value(self.global_horizontal_irradiance_channel)
            direct_normal_irradiance_extra = stsv.get_input_value(self.direct_normal_irradiance_extra_channel)
            apparent_zenith = stsv.get_input_value(self.apparent_zenith_channel)

        internal_heat_gains_through_occupancy_in_watt = stsv.get_input_value(self.occupancy_heat_gain_channel)

        internal_heat_gains_through_devices_in_watt = 0.0  # stsv.get_input_value(
        #     self.device_heat_gain_channel
        # )

        temperature_outside_in_celsius = stsv.get_input_value(self.temperature_outside_channel)

        building_temperature_modifier = stsv.get_input_value(self.building_temperature_modifier_channel)

        thermal_power_delivered_in_watt = 0.0
        if self.thermal_power_delivered_channel.source_output is not None:
            thermal_power_delivered_in_watt = thermal_power_delivered_in_watt + stsv.get_input_value(
                self.thermal_power_delivered_channel
            )
        if self.thermal_power_chp_channel.source_output is not None:
            thermal_power_delivered_in_watt = thermal_power_delivered_in_watt + stsv.get_input_value(
                self.thermal_power_chp_channel
            )

        previous_thermal_mass_temperature_in_celsius = self.state.thermal_mass_temperature_in_celsius

        # Performs calculations
        if hasattr(self, "solar_gain_through_windows") is False:
            solar_heat_gain_through_windows = self.get_solar_heat_gain_through_windows(
                azimuth=azimuth,
                direct_normal_irradiance=direct_normal_irradiance,
                direct_horizontal_irradiance=direct_horizontal_irradiance,
                global_horizontal_irradiance=global_horizontal_irradiance,
                direct_normal_irradiance_extra=direct_normal_irradiance_extra,
                apparent_zenith=apparent_zenith,
            )
        else:
            solar_heat_gain_through_windows = self.solar_heat_gain_through_windows[timestep]

        (
            thermal_mass_average_bulk_temperature_in_celsius,
            heat_loss_in_watt,
            internal_surface_temperature_in_celsius,
            indoor_air_temperature_in_celsius,
            heat_flux_thermal_mass_in_watt,
            heat_flux_internal_room_surface_in_watt,
            next_thermal_mass_temperature_in_celsius,
            heat_flux_indoor_air_in_watt,
        ) = self.calc_crank_nicolson(
            thermal_power_delivered_in_watt=thermal_power_delivered_in_watt,
            internal_heat_gains_in_watt=internal_heat_gains_through_occupancy_in_watt
            + internal_heat_gains_through_devices_in_watt,
            solar_heat_gains_in_watt=solar_heat_gain_through_windows,
            outside_temperature_in_celsius=temperature_outside_in_celsius,
            thermal_mass_temperature_prev_in_celsius=previous_thermal_mass_temperature_in_celsius,
        )
        self.state.thermal_mass_temperature_in_celsius = thermal_mass_average_bulk_temperature_in_celsius

        # if indoor temperature is too high make complete air exchange by opening the windows until outdoor temperature or set_heating_temperature + 1°C is reached
        if (
            self.buildingconfig.enable_opening_windows is True
            and self.set_heating_temperature_in_celsius + 1.0
            < self.set_cooling_temperature_in_celsius
            < indoor_air_temperature_in_celsius
            and temperature_outside_in_celsius < indoor_air_temperature_in_celsius
        ):
            indoor_air_temperature_in_celsius = max(
                self.set_heating_temperature_in_celsius + 1.0,
                temperature_outside_in_celsius,
            )
            self.window_open = 1
        else:
            self.window_open = 0

        # increase set_heating_temperature when connected to EnergyManagementSystem and surplus electricity available
        set_heating_temperature_modified_in_celsius = (
            self.set_heating_temperature_in_celsius + building_temperature_modifier
        )

        theoretical_thermal_building_demand_in_watt = self.calc_theoretical_thermal_building_demand_for_building(
            set_heating_temperature_in_celsius=set_heating_temperature_modified_in_celsius,
            set_cooling_temperature_in_celsius=self.set_cooling_temperature_in_celsius,
            previous_thermal_mass_temperature_in_celsius=previous_thermal_mass_temperature_in_celsius,
            outside_temperature_in_celsius=temperature_outside_in_celsius,
            next_thermal_mass_temperature_in_celsius=next_thermal_mass_temperature_in_celsius,
            heat_flux_indoor_air_in_watt=heat_flux_indoor_air_in_watt,
            heat_flux_internal_room_surface_in_watt=heat_flux_internal_room_surface_in_watt,
        )
        # Returns outputs
        stsv.set_output_value(
            self.thermal_mass_temperature_channel,
            thermal_mass_average_bulk_temperature_in_celsius,
        )
        stsv.set_output_value(
            self.internal_surface_temperature_channel,
            internal_surface_temperature_in_celsius,
        )

        stsv.set_output_value(
            self.indoor_air_temperature_channel,
            indoor_air_temperature_in_celsius,
        )

        # phi_loss is already given in W, time correction factor applied to thermal transmittance h_tr
        stsv.set_output_value(self.total_power_to_residence_channel, heat_loss_in_watt)

        stsv.set_output_value(self.solar_gain_through_windows_channel, solar_heat_gain_through_windows)

        stsv.set_output_value(
            self.heat_loss_channel,
            heat_loss_in_watt,
        )

        stsv.set_output_value(
            self.theoretical_thermal_building_demand_channel,
            theoretical_thermal_building_demand_in_watt,
        )

        stsv.set_output_value(
            self.heat_flow_rate_to_thermal_mass_node_channel,
            heat_flux_thermal_mass_in_watt,
        )
        stsv.set_output_value(
            self.heat_flow_rates_to_internal_surface_node_channel,
            heat_flux_internal_room_surface_in_watt,
        )
        stsv.set_output_value(
            self.open_window_channel,
            self.window_open,
        )

        # Saves solar gains cache
        if not self.is_in_cache:
            self.cache[timestep] = solar_heat_gain_through_windows
            if timestep + 1 == self.my_simulation_parameters.timesteps:
                database = pd.DataFrame(
                    self.cache,
                    columns=["solar_gain_through_windows"],
                )
                database.to_csv(
                    self.cache_file_path,
                    sep=",",
                    decimal=".",
                    index=False,
                )

    # =================================================================================================================================

    def i_save_state(
        self,
    ) -> None:
        """Save the current state."""
        self.previous_state = self.state.self_copy()

    def i_prepare_simulation(
        self,
    ) -> None:
        """Prepare the simulation."""
        if self.buildingconfig.predictive:
            # get weather forecast to compute forecasted solar gains
            # ambient_temperature_forecast = SingletonSimRepository().get_entry(
            #     key=SingletonDictKeyEnum.Weather_TemperatureOutside_yearly_forecast
            # )
            # altitude_forecast = SingletonSimRepository().get_entry(
            #     key=SingletonDictKeyEnum.Weather_Altitude_yearly_forecast
            # )
            azimuth_forecast = SingletonSimRepository().get_entry(key=SingletonDictKeyEnum.WEATHERAZIMUTHYEARLYFORECAST)
            apparent_zenith_forecast = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.WEATHERAPPARENTZENITHYEARLYFORECAST
            )
            direct_horizontal_irradiance_forecast = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.WEATHERDIFFUSEHORIZONTALIRRADIANCEYEARLYFORECAST
            )
            direct_normal_irradiance_forecast = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.WEATHERDIRECTNORMALIRRADIANCEYEARLYFORECAST
            )
            direct_normal_irradiance_extra_forecast = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.WEATHERDIRECTNORMALIRRADIANCEEXTRAYEARLYFORECAST
            )
            global_horizontal_irradiance_forecast = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.WEATHERGLOBALHORIZONTALIRRADIANCEYEARLYFORECAST
            )

            solar_gains_forecast = []
            for i in range(self.my_simulation_parameters.timesteps):
                solar_gains_forecast_yearly = self.get_solar_heat_gain_through_windows(
                    azimuth=azimuth_forecast[i],
                    direct_normal_irradiance=direct_normal_irradiance_forecast[i],
                    direct_horizontal_irradiance=direct_horizontal_irradiance_forecast[i],
                    global_horizontal_irradiance=global_horizontal_irradiance_forecast[i],
                    direct_normal_irradiance_extra=direct_normal_irradiance_extra_forecast[i],
                    apparent_zenith=apparent_zenith_forecast[i],
                )

                solar_gains_forecast.append(solar_gains_forecast_yearly)

            # get internal gains forecast
            internal_gains_forecast = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.HEATINGBYRESIDENTSYEARLYFORECAST
            )

            # compute the forecast of phi_ia phi_st and phi_m
            phi_m_forecast: list = []
            phi_st_forecast: list = []
            phi_ia_forecast: list = []
            for i in range(self.my_simulation_parameters.timesteps):
                (
                    _,
                    phi_ia_yearly,
                    phi_st_yearly,
                    phi_m_yearly,
                ) = self.calc_heat_flow(
                    internal_gains_forecast[i],
                    solar_gains_forecast[i],
                )
                phi_m_forecast.append(phi_m_yearly)
                phi_st_forecast.append(phi_st_yearly)
                phi_ia_forecast.append(phi_ia_yearly)

            # disturbance forecast for model predictive control
            SingletonSimRepository().set_entry(
                key=SingletonDictKeyEnum.HEATFLUXTHERMALMASSNODEFORECAST,
                entry=phi_m_forecast,
            )
            SingletonSimRepository().set_entry(
                key=SingletonDictKeyEnum.HEATFLUXSURFACENODEFORECAST,
                entry=phi_st_forecast,
            )
            SingletonSimRepository().set_entry(
                key=SingletonDictKeyEnum.HEATFLUXINDOORAIRNODEFORECAST,
                entry=phi_ia_forecast,
            )

    def i_restore_state(
        self,
    ) -> None:
        """Restore the previous state."""
        self.state = self.previous_state.self_copy()

    def i_doublecheck(
        self,
        timestep: int,
        stsv: cp.SingleTimeStepValues,
    ) -> None:
        """Doublecheck."""
        pass

    def build(
        self,
    ):
        """Build function.

        The function sets important constants and parameters for the calculations.
        It imports the building dataset from TABULA and gets phys params and thermal conductances etc.
        """

        self.seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
        self.timesteps = self.my_simulation_parameters.timesteps

        # Gets conductances
        (
            self.transmission_heat_transfer_coeff_windows_and_door_in_watt_per_kelvin,
            self.internal_part_of_transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin,
            self.transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin,
            self.external_part_of_transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin,
            self.heat_transfer_coeff_indoor_air_and_internal_surface_in_watt_per_kelvin,
            self.thermal_conductance_by_ventilation_in_watt_per_kelvin,
        ) = self.get_conductances()

        # send building parameters 5r1c to PID controller and to the MPC controller to generate an equivalent state space model
        # state space represntation is used for tuning of the pid and as a prediction model in the model predictive controller
        SingletonSimRepository().set_entry(
            key=SingletonDictKeyEnum.THERMALTRANSMISSIONCOEFFICIENTGLAZING,
            entry=self.transmission_heat_transfer_coeff_windows_and_door_in_watt_per_kelvin,
        )
        SingletonSimRepository().set_entry(
            key=SingletonDictKeyEnum.THERMALTRANSMISSIONSURFACEINDOORAIR,
            entry=self.heat_transfer_coeff_indoor_air_and_internal_surface_in_watt_per_kelvin,
        )
        SingletonSimRepository().set_entry(
            key=SingletonDictKeyEnum.THERMALTRANSMISSIONCOEFFICIENTOPAQUEEM,
            entry=self.external_part_of_transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin,
        )
        SingletonSimRepository().set_entry(
            key=SingletonDictKeyEnum.THERMALTRANSMISSIONCOEFFICIENTOPAQUEMS,
            entry=self.internal_part_of_transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin,
        )
        SingletonSimRepository().set_entry(
            key=SingletonDictKeyEnum.THERMALTRANSMISSIONCOEFFICIENTVENTILLATION,
            entry=self.thermal_conductance_by_ventilation_in_watt_per_kelvin,
        )
        SingletonSimRepository().set_entry(
            key=SingletonDictKeyEnum.THERMALCAPACITYENVELOPE,
            entry=self.my_building_information.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin,
        )

        # Get windows
        self.windows, self.total_scaled_windows_area = self.get_windows()

    def get_windows(
        self,
    ):
        """Retrieve data about windows sizes.

        :return:
        """

        windows = []
        total_windows_area = 0.0
        south_angle = 180

        windows_azimuth_angles = {
            "South": south_angle,
            "East": south_angle - 90,
            "North": south_angle - 180,
            "West": south_angle + 90,
            "Horizontal": None,
        }

        reduction_factor_for_non_perpedicular_radiation = self.my_building_information.buildingdata["F_w"].values[0]
        reduction_factor_for_frame_area_fraction_of_window = self.my_building_information.buildingdata["F_f"].values[0]
        reduction_factor_for_external_vertical_shading = self.my_building_information.buildingdata["F_sh_vert"].values[
            0
        ]
        total_solar_energy_transmittance_for_perpedicular_radiation = self.my_building_information.buildingdata[
            "g_gl_n"
        ].values[0]

        for index, windows_direction in enumerate(self.my_building_information.windows_directions):
            if windows_direction == "Horizontal":
                window_tilt_angle = 0
            else:
                window_tilt_angle = 90

            windows.append(
                Window(
                    window_tilt_angle=window_tilt_angle,
                    window_azimuth_angle=windows_azimuth_angles[windows_direction],
                    area=self.my_building_information.scaled_window_areas_in_m2[index],
                    frame_area_fraction_reduction_factor=reduction_factor_for_frame_area_fraction_of_window,
                    glass_solar_transmittance=total_solar_energy_transmittance_for_perpedicular_radiation,
                    nonperpendicular_reduction_factor=reduction_factor_for_non_perpedicular_radiation,
                    external_shading_vertical_reduction_factor=reduction_factor_for_external_vertical_shading,
                )
            )

            total_windows_area += self.my_building_information.scaled_window_areas_in_m2[index]
        # if nothing exists, initialize the empty arrays for caching, else read stuff
        if not self.is_in_cache:  # cache_filepath is None or  (not os.path.isfile(cache_filepath)):
            self.cache = [0] * self.my_simulation_parameters.timesteps
        else:
            self.solar_heat_gain_through_windows = pd.read_csv(
                self.cache_file_path,
                sep=",",
                decimal=".",
            )["solar_gain_through_windows"].tolist()

        return windows, total_windows_area

    # =====================================================================================================================================

    def __str__(
        self,
    ):
        """Return lines from report as string format."""
        entire = str()
        lines = self.write_to_report()
        for (
            index,
            line,
        ) in enumerate(lines):
            if index == 0:
                entire = line
            else:
                entire = f"{entire}\n{line}"
        return entire

    def write_to_report(
        self,
    ):
        """Write important variables to report."""
        lines = []

        lines.append(f"Max Thermal Demand [W]: {self.my_building_information.max_thermal_building_demand_in_watt}")
        lines.append("-------------------------------------------------------------------------------------------")
        lines.append("Building Thermal Conductances:")
        lines.append("--------------------------------------------")
        lines.append(
            f"Transmission for Windows and Doors, based on ISO 13790 (H_tr_w) [W/K]: "
            f"{self.transmission_heat_transfer_coeff_windows_and_door_in_watt_per_kelvin:.2f}"
        )
        lines.append(
            f"External Part of Transmission for Opaque Surfaces, based on ISO 13790 (H_tr_em) [W/K]: "
            f"{self.external_part_of_transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin:.2f}"
        )
        lines.append(
            f"Internal Part of Transmission for Opaque Surfaces, based on ISO 13790 (H_tr_ms) [W/K]: "
            f"{self.internal_part_of_transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin:.2f}"
        )
        lines.append(
            f"Transmission between Indoor Air and Internal Surface, based on ISO 13790 (H_tr_is) [W/K]: "
            f"{self.heat_transfer_coeff_indoor_air_and_internal_surface_in_watt_per_kelvin:.2f}"
        )

        lines.append(
            f"Thermal Conductance by Ventilation, based on TABULA (H_ve) [W/K]: "
            f"{self.my_building_information.heat_transfer_coeff_by_ventilation_reference_in_watt_per_kelvin:.2f}"
        )

        lines.append("-------------------------------------------------------------------------------------------")
        lines.append("Building Construction:")
        lines.append("--------------------------------------------")
        lines.append(f"Number of Apartments: {self.my_building_information.number_of_apartments}")
        lines.append(f"Number of Storeys: {self.my_building_information.number_of_storeys}")
        lines.append(
            f"Conditioned Floor Area (A_f) [m2]: {self.my_building_information.scaled_conditioned_floor_area_in_m2:.2f}"
        )
        lines.append(
            f"Effective Mass Area (A_m), based on ISO 13790 [m2]: {self.my_building_information.effective_mass_area_in_m2:.2f}"
        )
        lines.append(
            f"Total Internal Surface Area, based on ISO 13790 (A_t) [m2]: {self.my_building_information.total_internal_surface_area_in_m2:.2f}"
        )

        lines.append(f"Total Window Area [m2]: {self.total_scaled_windows_area:.2f}")

        lines.append("-------------------------------------------------------------------------------------------")
        lines.append("Building Thermal Capacitances:")
        lines.append("--------------------------------------------")
        lines.append(
            f"Floor Related Thermal Capacitance of Thermal Mass, based on ISO 13790 [Wh/m2.K]: "
            f"{(self.my_building_information.thermal_capacity_of_building_thermal_mass_in_watthour_per_m2_per_kelvin):.2f}"
        )
        lines.append(
            f"Floor Related Thermal Capacitance of Thermal Mass, based on TABULA [Wh/m2.K]: "
            f"{(self.my_building_information.thermal_capacity_of_building_thermal_mass_reference_in_watthour_per_m2_per_kelvin):.2f}"
        )
        lines.append("-------------------------------------------------------------------------------------------")
        lines.append("Building Heat Transfers:")
        lines.append("--------------------------------------------")
        lines.append(
            f"Annual Floor Related Total Heat Loss, based on TABULA (Q_ht) [kWh/m2.a]: "
            f"{self.my_building_information.total_heat_transfer_reference_in_kilowatthour_per_m2_per_year:.2f}"
        )
        lines.append(
            f"Annual Floor Related Internal Heat Gain, based on TABULA (Q_int) [kWh/m2.a]: "
            f"{self.my_building_information.internal_heat_sources_reference_in_kilowatthour_per_m2_per_year:.2f}"
        )
        lines.append(
            f"Annual Floor Related Solar Heat Gain, based on TABULA (Q_sol) [kWh/m2.a]: "
            f"{self.my_building_information.solar_heat_load_during_heating_seasons_reference_in_kilowatthour_per_m2_per_year:.2f}"
        )
        lines.append(
            f"Annual Floor Related Heating Demand, based on TABULA (Q_h_nd) [kWh/m2.a]: "
            f"{self.my_building_information.energy_need_for_heating_reference_in_kilowatthour_per_m2_per_year:.2f}"
        )
        return self.buildingconfig.get_string_dict() + lines

    # =====================================================================================================================================
    # Calculation of the heat transfer coefficients or thermal conductances.
    # (**/*** Check header)

    @property
    def transmission_heat_transfer_coeff_1_in_watt_per_kelvin(
        self,
    ):
        """Definition to simplify calc_phi_m_tot. Long form for H_tr_1.

        # (C.6) in [C.3 ISO 13790]
        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        return 1.0 / (
            1.0 / self.thermal_conductance_by_ventilation_in_watt_per_kelvin
            + 1.0 / self.heat_transfer_coeff_indoor_air_and_internal_surface_in_watt_per_kelvin
        )

    @property
    def transmission_heat_transfer_coeff_2_in_watt_per_kelvin(
        self,
    ):
        """Definition to simplify calc_phi_m_tot. Long form for H_tr_2.

        # (C.7) in [C.3 ISO 13790]
        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        return (
            self.transmission_heat_transfer_coeff_1_in_watt_per_kelvin
            + self.transmission_heat_transfer_coeff_windows_and_door_in_watt_per_kelvin
        )

    @property
    def transmission_heat_transfer_coeff_3_in_watt_per_kelvin(
        self,
    ):
        """Definition to simplify calc_phi_m_tot. Long form for H_tr_3.

        # (C.8) in [C.3 ISO 13790]
        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        return 1.0 / (
            1.0 / self.transmission_heat_transfer_coeff_2_in_watt_per_kelvin
            + 1.0 / self.internal_part_of_transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin
        )

    def get_thermal_conductance_between_exterior_and_windows_and_door_in_watt_per_kelvin(
        self,
    ):
        """Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)."""
        # Long from for H_tr_w: Conductance between exterior temperature and surface temperature
        # Objects: Doors, windows, curtain walls and windowed walls ISO 7.2.2.2 (here Window 1, Window 2 and Door 1)

        transmission_heat_transfer_coeff_windows_and_door_in_watt_per_kelvin = 0.0

        # here instead of reading H_Transmission from buildingdata it will be calculated manually using
        # input values U_Actual, A_ and b_Transmission also given by TABULA buildingdata
        for index, w_i in enumerate(self.my_building_information.windows_and_door):
            # with with H_Tr = U * A * b_tr [W/K], here b_tr is not given in TABULA data, so it is chosen 1.0
            h_tr_i = (
                self.my_building_information.buildingdata["U_Actual_" + w_i].values[0]
                * self.my_building_information.scaled_windows_and_door_envelope_areas_in_m2[index]
                * 1.0
            )
            transmission_heat_transfer_coeff_windows_and_door_in_watt_per_kelvin += float(h_tr_i)

        return transmission_heat_transfer_coeff_windows_and_door_in_watt_per_kelvin

    def get_thermal_conductance_thermal_mass_and_internal_surface_in_watt_per_kelvin(
        self,
        heat_transfer_coeff_thermal_mass_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin: float,
    ) -> float:
        """Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)."""
        # Long from for H_tr_ms, this is the same as internal pasrt of transmission heat transfer coefficient for opaque elements
        internal_part_of_transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin = float(
            self.my_building_information.effective_mass_area_in_m2
            * heat_transfer_coeff_thermal_mass_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin
        )

        return internal_part_of_transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin

    def get_thermal_conductance_of_opaque_surfaces_in_watt_per_kelvin(
        self,
        internal_part_of_transmission_coeff_opaque_elements_in_watt_per_kelvin: float,
    ) -> Tuple[float, float]:
        """Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)."""
        # Long from for H_tr_op: H_tr_op = 1/ (1/H_tr_ms + 1/H_tr_em) with
        # H_tr_ms: Conductance of opaque surfaces to interior [W/K] and H_tr_em: Conductance of opaque surfaces to exterior [W/K]
        # here opaque surfaces are Roof 1, Roof 2, Wall 1, Wall 2, Wall 3, Floor 1, Floor 2
        transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin = 0.0
        # here modification for scalability: instead of reading H_Transmission from buildingdata it will be calculated manually using
        # input values U_Actual, A_Calc and b_Transmission also given by TABULA buildingdata
        for index, o_w in enumerate(self.my_building_information.opaque_walls):
            # with with H_Tr = U * A * b_tr [W/K]
            h_tr_i = (
                self.my_building_information.buildingdata["U_Actual_" + o_w].values[0]
                * self.my_building_information.scaled_opaque_surfaces_envelope_area_in_m2[index]
                * self.my_building_information.buildingdata["b_Transmission_" + o_w].values[0]
            )
            transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin += float(h_tr_i)
        if (
            transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin != 0
            and internal_part_of_transmission_coeff_opaque_elements_in_watt_per_kelvin != 0
        ):
            external_part_of_transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin = 1 / (
                (1 / transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin)
                - (1 / internal_part_of_transmission_coeff_opaque_elements_in_watt_per_kelvin)
            )

        return (
            transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin,
            external_part_of_transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin,
        )

    def get_thermal_conductance_indoor_air_and_internal_surface_in_watt_per_kelvin(
        self,
        heat_transfer_coeff_indoor_air_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin: float,
    ) -> float:
        """Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)."""
        # Long from for H_tr_is: Conductance between air temperature and surface temperature
        heat_transfer_coeff_indoor_air_and_internal_surface_in_watt_per_kelvin = float(
            self.my_building_information.total_internal_surface_area_in_m2
            * heat_transfer_coeff_indoor_air_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin
        )

        return heat_transfer_coeff_indoor_air_and_internal_surface_in_watt_per_kelvin

    def get_thermal_conductance_ventilation_in_watt_per_kelvin(
        self,
    ) -> float:
        """Based on the EPISCOPE TABULA (* Check header)."""
        # Long from for H_ve_adj: Ventilation
        # Determine the ventilation conductance

        heat_capacity_of_air_per_volume_in_watt_hour_per_m3_per_kelvin = 0.34
        thermal_conductance_by_ventilation_in_watt_per_kelvin = float(
            heat_capacity_of_air_per_volume_in_watt_hour_per_m3_per_kelvin
            * float(
                self.my_building_information.buildingdata["n_air_use"].iloc[0]
                + self.my_building_information.buildingdata["n_air_infiltration"].iloc[0]
            )
            * self.my_building_information.scaled_conditioned_floor_area_in_m2
            * float(self.my_building_information.buildingdata["h_room"].iloc[0])
        )

        return thermal_conductance_by_ventilation_in_watt_per_kelvin

    def get_conductances(
        self,
    ) -> Tuple[float, float, float, float, float, float]:
        """Get the thermal conductances based on the norm EN ISO 13970.

        :key
        """
        # labeled as H_w in the paper [2] (*** Check header), before h_tr_w
        transmission_coeff_windows_and_door_in_watt_per_kelvin = (
            self.get_thermal_conductance_between_exterior_and_windows_and_door_in_watt_per_kelvin()
        )
        # labeled as H_tr_ms in paper [2] (*** Check header)
        internal_part_of_transmission_coeff_opaque_elements_in_watt_per_kelvin = self.get_thermal_conductance_thermal_mass_and_internal_surface_in_watt_per_kelvin(
            heat_transfer_coeff_thermal_mass_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin=(
                self.my_building_information.heat_transfer_coeff_thermal_mass_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin
            )
        )
        # external part of transmission heat transfer coeff opaque elements labeled as H_tr_em in paper [2] (*** Check header)
        (
            transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin,
            external_part_of_transmission_coeff_opaque_elements_in_watt_per_kelvin,
        ) = self.get_thermal_conductance_of_opaque_surfaces_in_watt_per_kelvin(
            internal_part_of_transmission_coeff_opaque_elements_in_watt_per_kelvin=internal_part_of_transmission_coeff_opaque_elements_in_watt_per_kelvin
        )
        # labeled as H_tr_is in paper [2] (** Check header)
        heat_transfer_coeff_indoor_air_and_internal_surface_in_watt_per_kelvin = self.get_thermal_conductance_indoor_air_and_internal_surface_in_watt_per_kelvin(
            heat_transfer_coeff_indoor_air_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin=(
                self.my_building_information.heat_transfer_coeff_indoor_air_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin
            )
        )
        thermal_conductance_by_ventilation_in_watt_per_kelvin = (
            self.get_thermal_conductance_ventilation_in_watt_per_kelvin()
        )

        return (
            transmission_coeff_windows_and_door_in_watt_per_kelvin,
            internal_part_of_transmission_coeff_opaque_elements_in_watt_per_kelvin,
            transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin,
            external_part_of_transmission_coeff_opaque_elements_in_watt_per_kelvin,
            heat_transfer_coeff_indoor_air_and_internal_surface_in_watt_per_kelvin,
            thermal_conductance_by_ventilation_in_watt_per_kelvin,
        )

    # =====================================================================================================================================

    def get_solar_heat_gain_through_windows(
        self,
        azimuth,
        direct_normal_irradiance,
        direct_horizontal_irradiance,
        global_horizontal_irradiance,
        direct_normal_irradiance_extra,
        apparent_zenith,
    ):
        """Calculate the thermal solar gain passed to the building through the windows.

        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        solar_heat_gains = 0.0

        if direct_normal_irradiance != 0 or direct_horizontal_irradiance != 0 or global_horizontal_irradiance != 0:
            for window in self.windows:
                solar_heat_gain = window.calc_solar_heat_gains(
                    sun_azimuth=azimuth,
                    direct_normal_irradiance=direct_normal_irradiance,
                    direct_horizontal_irradiance=direct_horizontal_irradiance,
                    global_horizontal_irradiance=global_horizontal_irradiance,
                    direct_normal_irradiance_extra=direct_normal_irradiance_extra,
                    apparent_zenith=apparent_zenith,
                    window_tilt_angle=window.window_tilt_angle,
                    window_azimuth_angle=window.window_azimuth_angle,
                    reduction_factor_with_area=window.reduction_factor_with_area,
                )
                solar_heat_gains += solar_heat_gain
        return solar_heat_gains

    # =====================================================================================================================================
    # Calculation of the heat flows from internal and solar heat sources.
    # (**/*** Check header)

    def calc_heat_flow(
        self,
        # this is labeled as Phi_int in paper [1] (** Check header)
        internal_heat_gains_in_watt,
        # this is labeled as Phi_sol in paper [1] (** Check header)
        solar_heat_gains_in_watt,
    ):
        """Calculate the heat flow from the solar gains, heating/cooling system, and internal gains into the building.

        The input of the building is split into the air node, surface node, and thermal mass node based on
        on the following equations

        #C.1 - C.3 in [C.3 ISO 13790]

        Note that this equation has diverged slightly from the standard
        as the heating/cooling node can enter any node depending on the
        emission system selected
        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """

        # Calculates the heat flows to various points of the building based on the breakdown in section C.2, formulas C.1-C.3

        # Heat flow to the air node in W, before labeled Phi_ia
        heat_flux_indoor_air_in_watt = 0.5 * internal_heat_gains_in_watt

        # Heat flow to the surface node in W, before labeled Phi_st
        heat_flux_internal_room_surface_in_watt = (
            1
            - (
                self.my_building_information.effective_mass_area_in_m2
                / self.my_building_information.total_internal_surface_area_in_m2
            )
            - (
                self.transmission_heat_transfer_coeff_windows_and_door_in_watt_per_kelvin
                / (
                    self.my_building_information.heat_transfer_coeff_thermal_mass_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin
                    * self.my_building_information.total_internal_surface_area_in_m2
                )
            )
        ) * (0.5 * internal_heat_gains_in_watt + solar_heat_gains_in_watt)

        # Heat flow to the thermal mass node in W, before labeled Phi_m
        heat_flux_thermal_mass_in_watt = (
            self.my_building_information.effective_mass_area_in_m2
            / self.my_building_information.total_internal_surface_area_in_m2
        ) * (0.5 * internal_heat_gains_in_watt + solar_heat_gains_in_watt)

        # Heat loss in W, before labeled Phi_loss
        heat_loss_in_watt = (
            self.transmission_heat_transfer_coeff_windows_and_door_in_watt_per_kelvin
            / (
                self.my_building_information.heat_transfer_coeff_thermal_mass_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin
                * self.my_building_information.total_internal_surface_area_in_m2
            )
        ) * (0.5 * internal_heat_gains_in_watt + solar_heat_gains_in_watt)

        return (
            heat_flux_indoor_air_in_watt,
            heat_flux_internal_room_surface_in_watt,
            heat_flux_thermal_mass_in_watt,
            heat_loss_in_watt,
        )

    # =====================================================================================================================================
    # Determination of different temperatures T_air, T_s, T_m,t and T_m and global heat transfer Phi_m_tot which are used in crank-nicolson method.
    # (**/*** Check header)

    def calc_next_thermal_mass_temperature_in_celsius(
        self,
        previous_thermal_mass_temperature_in_celsius: float,
        equivalent_heat_flux_in_watt: float,
    ) -> float:
        """Primary Equation, calculates the temperature of the next time step: T_m,t.

        # (C.4) in [C.3 ISO 13790]
        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        next_thermal_mass_temperature_in_celsius = float(
            (
                previous_thermal_mass_temperature_in_celsius
                * (
                    (
                        self.my_building_information.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin
                        / self.seconds_per_timestep
                    )
                    - 0.5
                    * (
                        self.transmission_heat_transfer_coeff_3_in_watt_per_kelvin
                        + self.external_part_of_transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin
                    )
                )
            )
            + equivalent_heat_flux_in_watt
        ) / float(
            (
                self.my_building_information.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin
                / self.seconds_per_timestep
            )
            + 0.5
            * (
                self.transmission_heat_transfer_coeff_3_in_watt_per_kelvin
                + self.external_part_of_transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin
            )
        )

        return next_thermal_mass_temperature_in_celsius

    def calc_equivalent_heat_flux_in_watt(
        self,
        temperature_outside_in_celsius: float,
        thermal_power_delivered_in_watt: float,
        heat_flux_thermal_mass_in_watt: float,
        heat_flux_internal_room_surface_in_watt: float,
        heat_flux_indoor_air_in_watt: float,
    ) -> float:
        """Calculate a global heat transfer: Phi_m_tot.

        This is a definition used to simplify equation calc_t_m_next so it's not so long to write out
        # (C.5) in [C.3 ISO 13790]
        # h_ve = h_ve_adj and t_supply = t_out [9.3.2 ISO 13790]
        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        # ASSUMPTION: Supply air comes straight from the outside air
        # here Phi_HC,nd is not heating or cooling demand but thermal power delivered
        t_supply = temperature_outside_in_celsius

        equivalent_heat_flux_in_watt = float(
            heat_flux_thermal_mass_in_watt
            + self.external_part_of_transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin
            * temperature_outside_in_celsius
            + self.transmission_heat_transfer_coeff_3_in_watt_per_kelvin
            * (
                heat_flux_internal_room_surface_in_watt
                + self.transmission_heat_transfer_coeff_windows_and_door_in_watt_per_kelvin
                * temperature_outside_in_celsius
                + self.transmission_heat_transfer_coeff_1_in_watt_per_kelvin
                * (
                    (
                        (heat_flux_indoor_air_in_watt + thermal_power_delivered_in_watt)
                        / self.thermal_conductance_by_ventilation_in_watt_per_kelvin
                    )
                    + t_supply
                )
            )
            / self.transmission_heat_transfer_coeff_2_in_watt_per_kelvin
        )

        return equivalent_heat_flux_in_watt

    def calc_thermal_mass_averag_bulk_temperature_in_celsius_used_for_calculations(
        self,
        previous_thermal_mass_temperature_in_celsius: float,
        next_thermal_mass_temperature_in_celsius: float,
    ) -> float:
        """Temperature used for the calculations, average between newly calculated and previous bulk temperature: T_m.

        # (C.9) in [C.3 ISO 13790]
        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        return (previous_thermal_mass_temperature_in_celsius + next_thermal_mass_temperature_in_celsius) / 2

    def calc_temperature_of_internal_room_surfaces_in_celsius(
        self,
        temperature_outside_in_celsius: float,
        thermal_mass_temperature_in_celsius: float,
        thermal_power_delivered_in_watt: float,
        heat_flux_internal_room_surface_in_watt: float,
        heat_flux_indoor_air_in_watt: float,
    ) -> float:
        """Calculate the temperature of the inside room surfaces: T_s.

        # (C.10) in [C.3 ISO 13790]
        # h_ve = h_ve_adj and t_supply = t_out [9.3.2 ISO 13790]
        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        # ASSUMPTION: Supply air comes straight from the outside air
        # here Phi_HC,nd is not heating or cooling demand but thermal power delivered
        t_supply = temperature_outside_in_celsius

        return float(
            self.internal_part_of_transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin
            * thermal_mass_temperature_in_celsius
            + heat_flux_internal_room_surface_in_watt
            + self.transmission_heat_transfer_coeff_windows_and_door_in_watt_per_kelvin * temperature_outside_in_celsius
            + self.transmission_heat_transfer_coeff_1_in_watt_per_kelvin
            * (
                t_supply
                + (heat_flux_indoor_air_in_watt + thermal_power_delivered_in_watt)
                / self.thermal_conductance_by_ventilation_in_watt_per_kelvin
            )
        ) / float(
            self.internal_part_of_transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin
            + self.transmission_heat_transfer_coeff_windows_and_door_in_watt_per_kelvin
            + self.transmission_heat_transfer_coeff_1_in_watt_per_kelvin
        )

    def calc_temperature_of_the_inside_air_in_celsius(
        self,
        temperature_outside_in_celsius: float,
        temperature_internal_room_surfaces_in_celsius: float,
        thermal_power_delivered_in_watt: float,
        heat_flux_indoor_air_in_watt: float,
    ) -> float:
        """Calculate the temperature of the air node: T_air.

        # (C.11) in [C.3 ISO 13790]
        # h_ve = h_ve_adj and t_supply = t_out [9.3.2 ISO 13790]
        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        # ASSUMPTION: Supply air comes straight from the outside air
        # here Phi_HC,nd is not heating or cooling demand but thermal power delivered
        t_supply = temperature_outside_in_celsius

        return (
            self.heat_transfer_coeff_indoor_air_and_internal_surface_in_watt_per_kelvin
            * temperature_internal_room_surfaces_in_celsius
            + self.thermal_conductance_by_ventilation_in_watt_per_kelvin * t_supply
            + thermal_power_delivered_in_watt
            + heat_flux_indoor_air_in_watt
        ) / (
            self.heat_transfer_coeff_indoor_air_and_internal_surface_in_watt_per_kelvin
            + self.thermal_conductance_by_ventilation_in_watt_per_kelvin
        )

    def calc_crank_nicolson(
        self,
        internal_heat_gains_in_watt: float,
        solar_heat_gains_in_watt: float,
        outside_temperature_in_celsius: float,
        thermal_mass_temperature_prev_in_celsius: float,
        thermal_power_delivered_in_watt: float,
    ) -> Tuple[float, float, float, float, float, float, float, float]:
        """Determine node temperatures and computes derivation to determine the new node temperatures.

        Used in: has_demand(), solve_energy(), calc_energy_demand()
        # section C.3 in [C.3 ISO 13790]
        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        Alternatively, described in paper [2].
        """

        # Updates flows
        (
            heat_flux_indoor_air_in_watt,
            heat_flux_internal_room_surface_in_watt,
            heat_flux_thermal_mass_in_watt,
            heat_loss_in_watt,
        ) = self.calc_heat_flow(
            internal_heat_gains_in_watt,
            solar_heat_gains_in_watt,
        )

        # Updates total flow
        equivalent_heat_flux_in_watt = self.calc_equivalent_heat_flux_in_watt(
            outside_temperature_in_celsius,
            thermal_power_delivered_in_watt,
            heat_flux_indoor_air_in_watt=heat_flux_indoor_air_in_watt,
            heat_flux_internal_room_surface_in_watt=heat_flux_internal_room_surface_in_watt,
            heat_flux_thermal_mass_in_watt=heat_flux_thermal_mass_in_watt,
        )

        # calculates the new bulk temperature POINT from the old one # CHECKED Requires t_m_prev
        next_thermal_mass_temperature_in_celsius = self.calc_next_thermal_mass_temperature_in_celsius(
            thermal_mass_temperature_prev_in_celsius,
            equivalent_heat_flux_in_watt=equivalent_heat_flux_in_watt,
        )

        # calculates the AVERAGE bulk temperature used for the remaining
        thermal_mass_average_bulk_temperature_in_celsius = (
            self.calc_thermal_mass_averag_bulk_temperature_in_celsius_used_for_calculations(
                previous_thermal_mass_temperature_in_celsius=thermal_mass_temperature_prev_in_celsius,
                next_thermal_mass_temperature_in_celsius=next_thermal_mass_temperature_in_celsius,
            )
        )

        # keep these calculations if later you are interested in the indoor surface or air temperature
        # Updates internal surface temperature (t_s)
        internal_room_surface_temperature_in_celsius = self.calc_temperature_of_internal_room_surfaces_in_celsius(
            outside_temperature_in_celsius,
            thermal_mass_average_bulk_temperature_in_celsius,
            thermal_power_delivered_in_watt,
            heat_flux_indoor_air_in_watt=heat_flux_indoor_air_in_watt,
            heat_flux_internal_room_surface_in_watt=heat_flux_internal_room_surface_in_watt,
        )

        # Updates indoor air temperature (t_air)
        indoor_air_temperature_in_celsius = self.calc_temperature_of_the_inside_air_in_celsius(
            outside_temperature_in_celsius,
            internal_room_surface_temperature_in_celsius,
            thermal_power_delivered_in_watt,
            heat_flux_indoor_air_in_watt=heat_flux_indoor_air_in_watt,
        )

        return (
            thermal_mass_average_bulk_temperature_in_celsius,
            heat_loss_in_watt,
            internal_room_surface_temperature_in_celsius,
            indoor_air_temperature_in_celsius,
            heat_flux_thermal_mass_in_watt,
            heat_flux_internal_room_surface_in_watt,
            next_thermal_mass_temperature_in_celsius,
            heat_flux_indoor_air_in_watt,
        )

    # =====================================================================================================================================
    # Calculate theroretical thermal building demand according to ISO 13790 C.4

    def calc_theoretical_thermal_building_demand_for_building(
        self,
        set_heating_temperature_in_celsius: float,
        set_cooling_temperature_in_celsius: float,
        previous_thermal_mass_temperature_in_celsius: float,
        outside_temperature_in_celsius: float,
        next_thermal_mass_temperature_in_celsius: float,
        heat_flux_internal_room_surface_in_watt: float,
        heat_flux_indoor_air_in_watt: float,
    ) -> Any:
        """Calculate theoretical thermal building demand to attain a certain set temperature according to ISO 13790 (C.4)."""

        # step1, calculate air temperature when thermal power delivered is zero
        indoor_air_temperature_zero_in_celsius = self.calc_indoor_air_temperature_zero_step_one(
            previous_thermal_mass_temperature_in_celsius=previous_thermal_mass_temperature_in_celsius,
            outside_temperature_in_celsius=outside_temperature_in_celsius,
            next_thermal_mass_temperature_in_celsius=next_thermal_mass_temperature_in_celsius,
            heat_flux_indoor_air_in_watt=heat_flux_indoor_air_in_watt,
            heat_flux_internal_room_surface_in_watt=heat_flux_internal_room_surface_in_watt,
        )

        # conditions for air_temperature_zero
        if (
            set_heating_temperature_in_celsius
            <= indoor_air_temperature_zero_in_celsius
            <= set_cooling_temperature_in_celsius
        ):
            # step1 finsihed, no heating or cooling needed
            theoretical_thermal_building_demand_in_watt = 0

        elif (
            indoor_air_temperature_zero_in_celsius > set_cooling_temperature_in_celsius
            or indoor_air_temperature_zero_in_celsius < set_heating_temperature_in_celsius
        ):
            # step2, heating or cooling is needed, calculate air temperature when therma power delivered is 10 W/m2
            (
                indoor_air_temperature_ten_in_celsius,
                ten_thermal_power_delivered_in_watt,
            ) = self.calc_indoor_air_temperature_ten_step_two(
                previous_thermal_mass_temperature_in_celsius=previous_thermal_mass_temperature_in_celsius,
                outside_temperature_in_celsius=outside_temperature_in_celsius,
                next_thermal_mass_temperature_in_celsius=next_thermal_mass_temperature_in_celsius,
                heat_flux_indoor_air_in_watt=heat_flux_indoor_air_in_watt,
                heat_flux_internal_room_surface_in_watt=heat_flux_internal_room_surface_in_watt,
            )
            # set air temperature
            if indoor_air_temperature_zero_in_celsius > set_cooling_temperature_in_celsius:
                indoor_air_temperature_set_in_celsius = set_cooling_temperature_in_celsius
            elif indoor_air_temperature_zero_in_celsius < set_heating_temperature_in_celsius:
                indoor_air_temperature_set_in_celsius = set_heating_temperature_in_celsius

            theoretical_thermal_building_demand_in_watt = (
                self.calc_theoretical_thermal_building_demand_when_heating_or_cooling_needed_step_two(
                    ten_thermal_power_delivered_in_watt=ten_thermal_power_delivered_in_watt,
                    indoor_air_temperature_zero_in_celsius=indoor_air_temperature_zero_in_celsius,
                    indoor_air_temperature_ten_in_celsius=indoor_air_temperature_ten_in_celsius,
                    indoor_air_temperature_set_in_celsius=indoor_air_temperature_set_in_celsius,
                )
            )
        else:
            raise ValueError(
                f"Value error for theoretical building demand. Indoor_air_temp_zero has uncompatible value {indoor_air_temperature_zero_in_celsius} C."
            )

        return theoretical_thermal_building_demand_in_watt

    def calc_indoor_air_temperature_zero_step_one(
        self,
        previous_thermal_mass_temperature_in_celsius: float,
        outside_temperature_in_celsius: float,
        next_thermal_mass_temperature_in_celsius: float,
        heat_flux_internal_room_surface_in_watt: float,
        heat_flux_indoor_air_in_watt: float,
    ) -> Any:
        """Calculate indoor air temperature for zero thermal power delivered (Phi_HC_nd) according to ISO 13790 (C.4.2)."""

        # step1: check if heating or cooling is needed
        zero_thermal_power_delivered_in_watt = 0

        # calculate temperatures (C.9 - C.11)
        thermal_mass_average_bulk_temperature_in_celsius = (
            self.calc_thermal_mass_averag_bulk_temperature_in_celsius_used_for_calculations(
                previous_thermal_mass_temperature_in_celsius=previous_thermal_mass_temperature_in_celsius,
                next_thermal_mass_temperature_in_celsius=next_thermal_mass_temperature_in_celsius,
            )
        )

        internal_room_surface_temperature_in_celsius = self.calc_temperature_of_internal_room_surfaces_in_celsius(
            temperature_outside_in_celsius=outside_temperature_in_celsius,
            thermal_mass_temperature_in_celsius=thermal_mass_average_bulk_temperature_in_celsius,
            thermal_power_delivered_in_watt=zero_thermal_power_delivered_in_watt,
            heat_flux_indoor_air_in_watt=heat_flux_indoor_air_in_watt,
            heat_flux_internal_room_surface_in_watt=heat_flux_internal_room_surface_in_watt,
        )

        # indoor air temperature named zero
        indoor_air_temperature_zero_in_celsius = self.calc_temperature_of_the_inside_air_in_celsius(
            temperature_outside_in_celsius=outside_temperature_in_celsius,
            temperature_internal_room_surfaces_in_celsius=internal_room_surface_temperature_in_celsius,
            thermal_power_delivered_in_watt=zero_thermal_power_delivered_in_watt,
            heat_flux_indoor_air_in_watt=heat_flux_indoor_air_in_watt,
        )
        return indoor_air_temperature_zero_in_celsius

    def calc_indoor_air_temperature_ten_step_two(
        self,
        previous_thermal_mass_temperature_in_celsius: float,
        outside_temperature_in_celsius: float,
        next_thermal_mass_temperature_in_celsius: float,
        heat_flux_internal_room_surface_in_watt: float,
        heat_flux_indoor_air_in_watt: float,
    ) -> Any:
        """Calculate indoor air temperature for thermal power delivered (Phi_HC_nd) of 10 W/m2 according to ISO 13790 (C.4.2)."""
        heating_power_in_watt_per_m2 = 10
        ten_thermal_power_delivered_in_watt = (
            heating_power_in_watt_per_m2 * self.my_building_information.scaled_conditioned_floor_area_in_m2
        )

        # calculate temperatures (C.9 - C.11)
        thermal_mass_average_bulk_temperature_in_celsius = (
            self.calc_thermal_mass_averag_bulk_temperature_in_celsius_used_for_calculations(
                previous_thermal_mass_temperature_in_celsius=previous_thermal_mass_temperature_in_celsius,
                next_thermal_mass_temperature_in_celsius=next_thermal_mass_temperature_in_celsius,
            )
        )

        internal_room_surface_temperature_in_celsius = self.calc_temperature_of_internal_room_surfaces_in_celsius(
            temperature_outside_in_celsius=outside_temperature_in_celsius,
            thermal_mass_temperature_in_celsius=thermal_mass_average_bulk_temperature_in_celsius,
            thermal_power_delivered_in_watt=ten_thermal_power_delivered_in_watt,
            heat_flux_indoor_air_in_watt=heat_flux_indoor_air_in_watt,
            heat_flux_internal_room_surface_in_watt=heat_flux_internal_room_surface_in_watt,
        )

        # indoor air temperature named zero
        indoor_air_temperature_ten_in_celsius = self.calc_temperature_of_the_inside_air_in_celsius(
            temperature_outside_in_celsius=outside_temperature_in_celsius,
            temperature_internal_room_surfaces_in_celsius=internal_room_surface_temperature_in_celsius,
            thermal_power_delivered_in_watt=ten_thermal_power_delivered_in_watt,
            heat_flux_indoor_air_in_watt=heat_flux_indoor_air_in_watt,
        )

        return (
            indoor_air_temperature_ten_in_celsius,
            ten_thermal_power_delivered_in_watt,
        )

    def calc_theoretical_thermal_building_demand_when_heating_or_cooling_needed_step_two(
        self,
        ten_thermal_power_delivered_in_watt: float,
        indoor_air_temperature_set_in_celsius: float,
        indoor_air_temperature_zero_in_celsius: float,
        indoor_air_temperature_ten_in_celsius: float,
    ) -> Any:
        """Calculate theoretical thermal building demand to attain a certain set temperature according to ISO 13790 (C.4.2, Eq. C.13)."""

        theoretical_thermal_building_demand_in_watt = (
            ten_thermal_power_delivered_in_watt
            * (indoor_air_temperature_set_in_celsius - indoor_air_temperature_zero_in_celsius)
            / (indoor_air_temperature_ten_in_celsius - indoor_air_temperature_zero_in_celsius)
        )

        return theoretical_thermal_building_demand_in_watt


# =====================================================================================================================================
class Window:

    """Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)."""

    def __init__(
        self,
        window_azimuth_angle=None,
        window_tilt_angle=None,
        area=None,
        glass_solar_transmittance=None,
        frame_area_fraction_reduction_factor=None,
        external_shading_vertical_reduction_factor=None,
        nonperpendicular_reduction_factor=None,
    ):
        """Construct all the neccessary attributes."""
        self.warning_message_already_shown = False
        # Angles
        self.window_tilt_angle = window_tilt_angle
        self.window_azimuth_angle = window_azimuth_angle
        self.window_tilt_angle_rad: float = 0

        # Area
        self.area = area

        # Transmittance
        self.glass_solar_transmittance = glass_solar_transmittance
        # Incident Solar Radiation
        self.incident_solar: int

        # Reduction factors
        self.nonperpendicular_reduction_factor = nonperpendicular_reduction_factor
        self.external_shading_vertical_reduction_factor = external_shading_vertical_reduction_factor
        self.frame_area_fraction_reduction_factor = frame_area_fraction_reduction_factor

        self.reduction_factor = (
            glass_solar_transmittance
            * nonperpendicular_reduction_factor
            * external_shading_vertical_reduction_factor
            * (1 - frame_area_fraction_reduction_factor)
        )

        self.reduction_factor_with_area = self.reduction_factor * self.area

    def calc_direct_solar_factor(
        self,
        sun_altitude,
        sun_azimuth,
        apparent_zenith,
    ):
        """Calculate the cosine of the angle of incidence on the window.

        Commented equations, that provide a direct calculation, were derived in:

        Proportion of the radiation incident on the window (cos of the incident ray)
        ref:Quaschning, Volker, and Rolf Hanitsch. "Shade calculations in photovoltaic systems."
        ISES Solar World Conference, Harare. 1995.

        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        sun_altitude_rad = math.radians(sun_altitude)

        aoi = pvlib.irradiance.aoi(
            self.window_tilt_angle,
            self.window_azimuth_angle,
            apparent_zenith,
            sun_azimuth,
        )

        direct_factor = math.cos(aoi) / (math.sin(sun_altitude_rad))

        return direct_factor

    def calc_diffuse_solar_factor(
        self,
    ):
        """Calculate the proportion of diffuse radiation.

        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        self.window_tilt_angle_rad = math.radians(self.window_tilt_angle)
        # Proportion of incident light on the window surface
        return (1 + math.cos(self.window_tilt_angle_rad)) / 2

    # Calculate solar heat gain through windows.
    # (** Check header)
    @lru_cache(maxsize=16)
    def calc_solar_heat_gains(
        self,
        sun_azimuth,
        direct_normal_irradiance,
        direct_horizontal_irradiance,
        global_horizontal_irradiance,
        direct_normal_irradiance_extra,
        apparent_zenith,
        window_tilt_angle,
        window_azimuth_angle,
        reduction_factor_with_area,
    ):
        """Calculate the Solar Gains in the building zone through the set Window.

        :param sun_altitude: Altitude Angle of the Sun in Degrees
        :type sun_altitude: float
        :param sun_azimuth: Azimuth angle of the sun in degrees
        :type sun_azimuth: float
        :param normal_direct_radiation: Normal Direct Radiation from weather file
        :type normal_direct_radiation: float
        :param horizontal_diffuse_radiation: Horizontal Diffuse Radiation from weather file
        :type horizontal_diffuse_radiation: float
        :return: self.incident_solar, Incident Solar Radiation on window
        :return: self.solar_gains - Solar gains in building after transmitting through the window
        :rtype: float
        """
        if window_azimuth_angle is None:
            window_azimuth_angle = 0
            if self.warning_message_already_shown is False:
                log.warning("window azimuth angle was set to 0 south because no value was set.")
                self.warning_message_already_shown = True

        poa_irrad = pvlib.irradiance.get_total_irradiance(
            window_tilt_angle,
            window_azimuth_angle,
            apparent_zenith,
            sun_azimuth,
            direct_normal_irradiance,
            global_horizontal_irradiance,
            direct_horizontal_irradiance,
            direct_normal_irradiance_extra,
        )

        if math.isnan(poa_irrad["poa_direct"]):
            return 0

        return poa_irrad["poa_direct"] * reduction_factor_with_area


@dataclass_json
@dataclass
class BuildingInformation:

    """Class for collecting important building parameters to pass to other components.

    The class reads the building config and collects all the important parameters of the buidling.

    """

    def __init__(self, config: BuildingConfig):
        """Initialize the class."""

        self.buildingconfig = config

        self.windows_directions: List[str]
        self.windows_and_door: List[str]
        self.opaque_walls: List[str]

        self.get_building()
        self.build()

        # get set temperatures for building
        self.set_heating_temperature_for_building_in_celsius = self.buildingconfig.set_heating_temperature_in_celsius
        self.set_cooling_temperature_for_building_in_celsius = self.buildingconfig.set_cooling_temperature_in_celsius
        self.heating_reference_temperature_in_celsius = self.buildingconfig.heating_reference_temperature_in_celsius

    def get_building(
        self,
    ):
        """Get the building code from a TABULA building."""
        d_f = pd.read_csv(
            utils.HISIMPATH["housing"],
            decimal=",",
            sep=";",
            encoding="cp1252",
            low_memory=False,
        )

        # Gets parameters from chosen building
        self.buildingdata = d_f.loc[d_f["Code_BuildingVariant"] == self.buildingconfig.building_code]
        self.buildingcode = self.buildingconfig.building_code
        self.building_heat_capacity_class = self.buildingconfig.building_heat_capacity_class

    def build(self):
        """Set important parameters."""

        # CONSTANTS
        # Heat transfer coefficient between nodes "m" and "s" (12.2.2 E64 P79); labeled as h_ms in paper [2] (*** Check header)
        self.heat_transfer_coeff_thermal_mass_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin = 9.1
        # Dimensionless ratio between surfaces and the useful surfaces (7.2.2.2 E9 P36); labeled as A_at in paper [2] (*** Check header); before lambda_at
        self.ratio_between_internal_surface_area_and_floor_area = 4.5
        # Heat transfer coefficient between nodes "air" and "s" (7.2.2.2 E9 P35); labeled as h_is in paper [2] (*** Check header)
        self.heat_transfer_coeff_indoor_air_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin = 3.45

        self.building_heat_capacity_class_f_a = {
            "very light": 2.5,
            "light": 2.5,
            "medium": 2.5,
            "heavy": 3.0,
            "very heavy": 3.5,
        }

        self.building_heat_capacity_class_f_c_in_joule_per_m2_per_kelvin = {
            "very light": 8e4,
            "light": 1.1e5,
            "medium": 1.65e5,
            "heavy": 2.6e5,
            "very heavy": 3.7e5,
        }

        self.ven_method = "EPISCOPE"
        # Get physical parameters
        (
            scaling_factor,
            self.scaled_windows_and_door_envelope_areas_in_m2,
            self.scaled_opaque_surfaces_envelope_area_in_m2,
            self.scaled_conditioned_floor_area_in_m2,
            self.scaled_window_areas_in_m2,
            self.scaled_rooftop_area_in_m2,
            self.room_height_in_m,
            self.number_of_storeys,
            self.buildingdata,
        ) = self.get_physical_param(buildingdata=self.buildingdata)

        # Reference properties from TABULA, but not used in the model (scaling factor added in case floor area is different to tabula floor area A_C_ref)
        (
            self.solar_heat_load_during_heating_seasons_reference_in_kilowatthour_per_m2_per_year,
            self.internal_heat_sources_reference_in_kilowatthour_per_m2_per_year,
            self.total_heat_transfer_reference_in_kilowatthour_per_m2_per_year,
            self.energy_need_for_heating_reference_in_kilowatthour_per_m2_per_year,
            self.thermal_capacity_of_building_thermal_mass_reference_in_watthour_per_m2_per_kelvin,
            self.heat_transfer_coeff_by_ventilation_reference_in_watt_per_kelvin,
            heat_transfer_coeff_by_transmission_reference_in_watt_per_m2_per_kelvin,
            heat_transfer_coeff_by_ventilation_reference_in_watt_per_m2_per_kelvin,
        ) = self.get_some_reference_data_from_tabula(
            buildingdata=self.buildingdata,
            scaled_conditioned_floor_area_in_m2=self.scaled_conditioned_floor_area_in_m2,
        )

        # Room Capacitance [J/K] (TABULA: Internal heat capacity) Ref: ISO standard 12.3.1.2
        # labeled as C_m in the paper [1] (** Check header), before c_m
        self.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin = (
            self.building_heat_capacity_class_f_c_in_joule_per_m2_per_kelvin[self.building_heat_capacity_class]
            * self.scaled_conditioned_floor_area_in_m2
        )
        # Room Capacitance [Wh/m2K] (TABULA: Internal heat capacity) Ref: ISO standard 12.3.1.2
        self.thermal_capacity_of_building_thermal_mass_in_watthour_per_m2_per_kelvin = (
            self.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin
            / (3.6e3 * self.scaled_conditioned_floor_area_in_m2)
        )
        # before labeled as a_m
        self.effective_mass_area_in_m2 = (
            self.scaled_conditioned_floor_area_in_m2
            * self.building_heat_capacity_class_f_a[self.building_heat_capacity_class]
        )
        # before labeled as a_t
        self.total_internal_surface_area_in_m2 = (
            self.scaled_conditioned_floor_area_in_m2 * self.ratio_between_internal_surface_area_and_floor_area
        )

        # Get number of apartments
        self.number_of_apartments = int(
            self.get_number_of_apartments(
                conditioned_floor_area_in_m2=self.scaled_conditioned_floor_area_in_m2,
                scaling_factor=scaling_factor,
                buildingdata=self.buildingdata,
            )
        )

        # Get heating load of building
        self.max_thermal_building_demand_in_watt = self.calc_max_thermal_building_demand(
            heating_reference_temperature_in_celsius=self.buildingconfig.heating_reference_temperature_in_celsius,
            initial_temperature_in_celsius=self.buildingconfig.initial_internal_temperature_in_celsius,
            scaled_conditioned_floor_area_in_m2=self.scaled_conditioned_floor_area_in_m2,
            heat_transfer_coeff_by_transmission_in_watt_per_m2_per_kelvin=heat_transfer_coeff_by_transmission_reference_in_watt_per_m2_per_kelvin,
            heat_transfer_coeff_by_ventilation_in_watt_per_m2_per_kelvin=heat_transfer_coeff_by_ventilation_reference_in_watt_per_m2_per_kelvin,
        )

    def get_physical_param(self, buildingdata: Any) -> Tuple[float, List, List, float, List, float, float, float, Any]:
        """Get the physical parameters from the building data."""

        # Reference area [m^2] (TABULA: Reference floor area A_C_Ref )Ref: ISO standard 7.2.2.2
        conditioned_floor_area_in_m2_reference = float((buildingdata["A_C_Ref"].values[0]))

        room_height_in_m = float(buildingdata["h_room"].values[0])

        rooftop_area_in_m2_reference = float(buildingdata["A_Roof_1"].values[0]) + float(
            buildingdata["A_Roof_2"].values[0]
        )

        number_of_storeys = float(buildingdata["n_Storey"].values[0])

        # Get scaled areas
        (
            scaling_factor,
            scaled_windows_and_door_envelope_areas_in_m2,
            scaled_opaque_surfaces_envelope_area_in_m2,
            scaled_conditioned_floor_area_in_m2,
            scaled_window_areas_in_m2,
            scaled_rooftop_area_in_m2,
            buildingdata,
        ) = self.scaling_over_conditioned_floor_area(
            conditioned_floor_area_in_m2=conditioned_floor_area_in_m2_reference,
            rooftop_area_in_m2=rooftop_area_in_m2_reference,
            number_of_storeys=number_of_storeys,
            room_height_in_m=room_height_in_m,
            buildingdata=self.buildingdata,
        )

        return (
            scaling_factor,
            scaled_windows_and_door_envelope_areas_in_m2,
            scaled_opaque_surfaces_envelope_area_in_m2,
            scaled_conditioned_floor_area_in_m2,
            scaled_window_areas_in_m2,
            scaled_rooftop_area_in_m2,
            room_height_in_m,
            number_of_storeys,
            buildingdata,
        )

    # =====================================================================================================================================
    # Calculation of maximal thermal building heat demand according to TABULA (* Check header).
    def calc_max_thermal_building_demand(
        self,
        initial_temperature_in_celsius: float,
        heating_reference_temperature_in_celsius: float,
        scaled_conditioned_floor_area_in_m2: float,
        heat_transfer_coeff_by_ventilation_in_watt_per_m2_per_kelvin: float,
        heat_transfer_coeff_by_transmission_in_watt_per_m2_per_kelvin: float,
    ) -> Any:
        """Calculate maximal thermal building demand using TABULA data."""

        # with with dQ/dt = h * (T2-T1) * A -> [W]
        max_thermal_building_demand_in_watt = (
            (
                heat_transfer_coeff_by_transmission_in_watt_per_m2_per_kelvin
                + heat_transfer_coeff_by_ventilation_in_watt_per_m2_per_kelvin
            )
            * (initial_temperature_in_celsius - heating_reference_temperature_in_celsius)
            * scaled_conditioned_floor_area_in_m2
        )
        return max_thermal_building_demand_in_watt

    def get_number_of_apartments(
        self,
        conditioned_floor_area_in_m2: float,
        scaling_factor: float,
        buildingdata: Any,
    ) -> float:
        """Get number of apartments.

        Either from config or from tabula or through approximation with data from
        https://www.umweltbundesamt.de/daten/private-haushalte-konsum/wohnen/wohnflaeche#zahl-der-wohnungen-gestiegen.
        """

        if self.buildingconfig.number_of_apartments is not None:
            number_of_apartments_origin = self.buildingconfig.number_of_apartments

            if number_of_apartments_origin == 0:
                # check table from the link for the year 2021
                average_living_area_per_apartment_in_2021_in_m2 = 92.1
                number_of_apartments = conditioned_floor_area_in_m2 / average_living_area_per_apartment_in_2021_in_m2
            elif number_of_apartments_origin > 0:
                number_of_apartments = number_of_apartments_origin

            else:
                raise ValueError("Number of apartments can not be negative.")

        elif self.buildingconfig.number_of_apartments is None:
            number_of_apartments_origin = float(buildingdata["n_Apartment"].values[0])

            # if no value given or if the area given in the config is bigger than the tabula ref area
            if number_of_apartments_origin == 0 or scaling_factor != 1:
                # check table from the link for the year 2021
                average_living_area_per_apartment_in_2021_in_m2 = 92.1
                number_of_apartments = conditioned_floor_area_in_m2 / average_living_area_per_apartment_in_2021_in_m2
            elif number_of_apartments_origin > 0:
                number_of_apartments = number_of_apartments_origin

            else:
                raise ValueError("Number of apartments can not be negative.")

        return number_of_apartments

    def scaling_over_conditioned_floor_area(
        self,
        conditioned_floor_area_in_m2: float,
        rooftop_area_in_m2: float,
        number_of_storeys: float,
        room_height_in_m: float,
        buildingdata: Any,
    ) -> Tuple[float, List, List, float, List, float, Any]:
        """Calculate scaling factors for the building.

        Either the absolute conditioned floor area or the total base area should be given.
        The conditioned floor area, the envelope surface areas or window areas are scaled with a scaling factor.
        """

        # scaling envelope areas of windows and door
        self.windows_and_door = [
            "Window_1",
            "Window_2",
            "Door_1",
        ]
        # scaling envelope areas of opaque surfaces
        self.opaque_walls = [
            "Wall_1",
            "Wall_2",
            "Wall_3",
            "Roof_1",
            "Roof_2",
            "Floor_1",
            "Floor_2",
        ]
        scaled_windows_and_door_envelope_areas_in_m2 = []
        scaled_opaque_surfaces_envelope_area_in_m2 = []

        if (
            self.buildingconfig.absolute_conditioned_floor_area_in_m2 is not None
            and self.buildingconfig.total_base_area_in_m2 is not None
        ):
            raise ValueError("Only one variable can be used, the other one must be None.")

        if self.buildingconfig.absolute_conditioned_floor_area_in_m2 is not None:
            # this is for preventing that the conditioned_floor_area is 0 (some buildings in TABULA have conditioned_floor_area (A_C_Ref) = 0)
            if conditioned_floor_area_in_m2 == 0:
                scaled_conditioned_floor_area_in_m2 = self.buildingconfig.absolute_conditioned_floor_area_in_m2
                factor_of_absolute_floor_area_to_tabula_floor_area = 1.0
                buildingdata["A_C_Ref"] = scaled_conditioned_floor_area_in_m2
            # scaling conditioned floor area
            else:
                factor_of_absolute_floor_area_to_tabula_floor_area = (
                    self.buildingconfig.absolute_conditioned_floor_area_in_m2 / conditioned_floor_area_in_m2
                )
                scaled_conditioned_floor_area_in_m2 = (
                    conditioned_floor_area_in_m2 * factor_of_absolute_floor_area_to_tabula_floor_area
                )
            scaling_factor = factor_of_absolute_floor_area_to_tabula_floor_area

        elif self.buildingconfig.total_base_area_in_m2 is not None:
            # this is for preventing that the conditioned_floor_area is 0
            if conditioned_floor_area_in_m2 == 0:
                scaled_conditioned_floor_area_in_m2 = self.buildingconfig.total_base_area_in_m2
                factor_of_total_base_area_to_tabula_floor_area = 1.0
                buildingdata["A_C_Ref"] = scaled_conditioned_floor_area_in_m2
            # scaling conditioned floor area
            else:
                factor_of_total_base_area_to_tabula_floor_area = (
                    self.buildingconfig.total_base_area_in_m2 / conditioned_floor_area_in_m2
                )
                scaled_conditioned_floor_area_in_m2 = (
                    conditioned_floor_area_in_m2 * factor_of_total_base_area_to_tabula_floor_area
                )
            scaling_factor = factor_of_total_base_area_to_tabula_floor_area

        # if no value for building size is provided in config, use reference value from Tabula or 500 m^2.
        else:
            if conditioned_floor_area_in_m2 == 0:
                scaled_conditioned_floor_area_in_m2 = 500.0
                buildingdata["A_C_Ref"] = scaled_conditioned_floor_area_in_m2
                log.warning(
                    "There is no reference given for absolute conditioned floor area in m^2, so a default of 500 m^2 is used."
                )
            else:
                scaled_conditioned_floor_area_in_m2 = conditioned_floor_area_in_m2

            scaling_factor = 1.0

        for w_i in self.windows_and_door:
            scaled_windows_and_door_envelope_areas_in_m2.append(
                float(buildingdata["A_" + w_i].values[0]) * scaling_factor
            )

        for o_w in self.opaque_walls:
            scaled_opaque_surfaces_envelope_area_in_m2.append(
                float(buildingdata["A_" + o_w].values[0]) * scaling_factor
            )

        # scaling window areas over wall area
        self.windows_directions = [
            "South",
            "East",
            "North",
            "West",
            "Horizontal",
        ]

        # assumption: building is a cuboid with square floor area (area_of_one_wall = wall_length * wall_height, with wall_length = sqrt(floor_area))
        total_wall_area_in_m2_tabula = 4 * math.sqrt(conditioned_floor_area_in_m2) * room_height_in_m

        scaled_total_wall_area_in_m2 = 4 * math.sqrt(scaled_conditioned_floor_area_in_m2) * room_height_in_m

        scaled_window_areas_in_m2 = []
        for windows_direction in self.windows_directions:
            window_area_in_m2 = float(buildingdata["A_Window_" + windows_direction].iloc[0])

            if scaling_factor != 1.0:
                factor_window_area_to_wall_area_tabula = window_area_in_m2 / total_wall_area_in_m2_tabula
                scaled_window_areas_in_m2.append(scaled_total_wall_area_in_m2 * factor_window_area_to_wall_area_tabula)
            else:
                scaled_window_areas_in_m2.append(window_area_in_m2)

        # scale rooftop area with the same factor as conditioned floor area
        scaled_rooftop_area_in_m2 = self.scale_rooftop_area(
            rooftop_area_from_tabula_in_m2=rooftop_area_in_m2,
            total_base_area_in_m2_from_config=self.buildingconfig.total_base_area_in_m2,
            absolute_floor_area_in_m2_from_config=self.buildingconfig.absolute_conditioned_floor_area_in_m2,
            scaling_factor=scaling_factor,
            number_of_storeys=number_of_storeys,
        )

        return (
            scaling_factor,
            scaled_windows_and_door_envelope_areas_in_m2,
            scaled_opaque_surfaces_envelope_area_in_m2,
            scaled_conditioned_floor_area_in_m2,
            scaled_window_areas_in_m2,
            scaled_rooftop_area_in_m2,
            buildingdata,
        )

    def scale_rooftop_area(
        self,
        rooftop_area_from_tabula_in_m2: float,
        total_base_area_in_m2_from_config: Optional[float],
        absolute_floor_area_in_m2_from_config: Optional[float],
        scaling_factor: float,
        number_of_storeys: float,
    ) -> float:
        """Scale rooftop area of building according to floor area and number of storeys."""

        if total_base_area_in_m2_from_config is not None:
            # rooftop area scales linearly with base area
            scaling_factor_for_rooftop = scaling_factor
        elif absolute_floor_area_in_m2_from_config is not None:
            # rooftop area scales linearly with floor area but divided by number of storeys
            scaling_factor_for_rooftop = scaling_factor / number_of_storeys

        else:
            # both total base area and absolute floor area from config are None
            # in this case the floor area from tabula or 500m2 default is taken
            # rooftop area scales linearly with floor area but divided by number of storeys
            scaling_factor_for_rooftop = scaling_factor / number_of_storeys

        return rooftop_area_from_tabula_in_m2 * scaling_factor_for_rooftop

    def get_some_reference_data_from_tabula(
        self, buildingdata: Any, scaled_conditioned_floor_area_in_m2: float
    ) -> Tuple[float, float, float, float, float, float, float, float]:
        """Get some reference parameter from Tabula."""

        # Floor area related heat load during heating season
        # reference taken from TABULA (* Check header) Q_sol [kWh/m2.a], before q_sol_ref (or solar heat sources?)
        solar_heat_load_during_heating_seasons_reference_in_kilowatthour_per_m2_per_year = float(
            (buildingdata["q_sol"].values[0])
        )
        # Floor area related internal heat sources during heating season
        # reference taken from TABULA (* Check header) as Q_int [kWh/m2.a], before q_int_ref
        internal_heat_sources_reference_in_kilowatthour_per_m2_per_year = float(buildingdata["q_int"].values[0])
        # Floor area related annual losses
        # reference taken from TABULA (* Check header) as Q_ht [kWh/m2.a], before q_ht_ref
        total_heat_transfer_reference_in_kilowatthour_per_m2_per_year = float(buildingdata["q_ht"].values[0])
        # Energy need for heating
        # reference taken from TABULA (* Check header) as Q_H_nd [kWh/m2.a], before q_h_nd_ref
        energy_need_for_heating_reference_in_kilowatthour_per_m2_per_year = float(buildingdata["q_h_nd"].values[0])
        # Internal heat capacity per m2 reference area [Wh/(m^2.K)] (TABULA: Internal heat capacity)
        thermal_capacity_of_building_thermal_mass_reference_in_watthour_per_m2_per_kelvin = float(
            buildingdata["c_m"].values[0]
        )

        # Heat transfer coefficient by ventilation in watt per m2 per kelvin
        heat_transfer_coeff_by_ventilation_reference_in_watt_per_m2_per_kelvin = float(
            buildingdata["h_Ventilation"].values[0]
        )
        if heat_transfer_coeff_by_ventilation_reference_in_watt_per_m2_per_kelvin is None:
            raise ValueError("h_Ventilation was none.")
        # Heat transfer coefficient by ventilation in watt per kelvin
        heat_transfer_coeff_by_ventilation_reference_in_watt_per_kelvin = (
            float(buildingdata["h_Ventilation"].values[0]) * scaled_conditioned_floor_area_in_m2
        )

        # Heat transfer coefficient by transmission in watt per m2 per kelvin
        heat_transfer_coeff_by_transmission_reference_in_watt_per_m2_per_kelvin = float(
            buildingdata["h_Transmission"].values[0]
        )
        if heat_transfer_coeff_by_transmission_reference_in_watt_per_m2_per_kelvin is None:
            raise ValueError("h_Transmission was none.")

        return (
            solar_heat_load_during_heating_seasons_reference_in_kilowatthour_per_m2_per_year,
            internal_heat_sources_reference_in_kilowatthour_per_m2_per_year,
            total_heat_transfer_reference_in_kilowatthour_per_m2_per_year,
            energy_need_for_heating_reference_in_kilowatthour_per_m2_per_year,
            thermal_capacity_of_building_thermal_mass_reference_in_watthour_per_m2_per_kelvin,
            heat_transfer_coeff_by_ventilation_reference_in_watt_per_kelvin,
            heat_transfer_coeff_by_transmission_reference_in_watt_per_m2_per_kelvin,
            heat_transfer_coeff_by_ventilation_reference_in_watt_per_m2_per_kelvin,
        )
