"""Building thermal-model component.

Part of the ``hisim.components.building`` package split (see the package ``__init__``
for the layout and the physics references: EPISCOPE/TABULA, RC_BuildingSimulator,
EN ISO 13790). Holds the ``Building`` component together with its ``BuildingState``,
moved verbatim from the former single-module ``building.py``.
"""

# clean

import importlib
from typing import Any, List, Optional, Tuple

import pandas as pd

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log, utils
from hisim.components.building.config import BuildingConfig
from hisim.components.building.information import BuildingInformation
from hisim.components.building.window import Window
from hisim.components.loadprofilegenerator_utsp_connector import UtspLpgConnector
from hisim.components.weather import Weather
from hisim.loadtypes import OutputPostprocessingRules
from hisim.sim_repository_singleton import SingletonDictKeyEnum, SingletonSimRepository
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass, KpiHelperClass
from hisim.config import DisplayConfig


class BuildingState:
    """BuildingState class."""

    def __init__(
        self,
        thermal_mass_temperature_in_celsius: float,
        thermal_capacitance_in_joule_per_kelvin: float,
    ) -> None:
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
    ) -> "BuildingState":
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
    TotalThermalPowerToResidence = "TotalThermalPowerToResidence"
    SolarGainThroughWindows = "SolarGainThroughWindows"
    InternalHeatGainsFromOccupancy = "InternalHeatGainsFromOccupancy"
    TheoreticalThermalBuildingDemand = "TheoreticalThermalBuildingDemand"
    TheoreticalThermalEnergyBuildingDemand = "TheoreticalThermalEnergyBuildingDemand"
    TheoreticalHeatingDemand = "TheoreticalHeatingDemand"
    TheoreticalHeatingEnergyDemand = "TheoreticalHeatingEnergyDemand"
    TheoreticalCoolingDemand = "TheoreticalCoolingDemand"
    TheoreticalCoolingEnergyDemand = "TheoreticalCoolingEnergyDemand"
    HeatFluxToInternalSurface = "HeatFluxToInternalSurface"
    HeatFluxToThermalMass = "HeatFluxToThermalMass"
    TotalThermalMassHeatFlux = "TotalThermalMassHeatFlux"
    OpenWindow = "OpenWindow"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: BuildingConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.buildingconfig = config

        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
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
            self.config.component_id.name,
            self.buildingconfig,
            self.my_simulation_parameters,
        )

        self.cache: List[float]
        self.solar_heat_gain_through_windows: List[float]

        self.my_building_information = BuildingInformation(
            config=self.buildingconfig,
        )

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
            mandatory=False,
        )

        self.device_heat_gain_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HeatingByDevices,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            mandatory=False,
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
        self.total_thermal_power_to_residence_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.TotalThermalPowerToResidence,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.TotalThermalPowerToResidence} will follow.",
        )
        self.solar_gain_through_windows_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.SolarGainThroughWindows,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.SolarGainThroughWindows} will follow.",
            postprocessing_flag=[OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.internal_heat_gains_from_residents_and_devices_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.InternalHeatGainsFromOccupancy,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.InternalHeatGainsFromOccupancy} will follow.",
            postprocessing_flag=[OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.theoretical_thermal_building_demand_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.TheoreticalThermalBuildingDemand,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description="Theoretical thermal power demand of building.",
        )
        self.theoretical_thermal_energy_building_demand_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.TheoreticalThermalEnergyBuildingDemand,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description="Theoretical thermal energy demand of building (Heizwärme-/Kühlbedarf).",
        )
        self.theoretical_heating_demand_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.TheoreticalHeatingDemand,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description="Theoretical heating demand of the building.",
        )
        self.theoretical_heating_energy_demand_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.TheoreticalHeatingEnergyDemand,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description="Theoretical heating energy demand of the building (Heizwärmebedarf).",
        )
        self.theoretical_cooling_demand_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.TheoreticalCoolingDemand,
            lt.LoadTypes.COOLING,
            lt.Units.WATT,
            output_description="Theoretical cooling demand of the building.",
        )
        self.theoretical_cooling_energy_demand_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.TheoreticalCoolingEnergyDemand,
            lt.LoadTypes.COOLING,
            lt.Units.WATT_HOUR,
            output_description="Theoretical cooling demand of the building (Kühlbedarf).",
        )
        self.heat_flow_rate_to_thermal_mass_node_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.HeatFluxToThermalMass,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.HeatFluxToThermalMass} will follow.",
        )
        self.heat_flow_rates_to_internal_surface_node_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.HeatFluxToInternalSurface,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.HeatFluxToInternalSurface} will follow.",
        )
        self.total_heat_flow_rates_thermal_mass_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.TotalThermalMassHeatFlux,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.TotalThermalMassHeatFlux} will follow.",
            postprocessing_flag=[OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.open_window_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.OpenWindow,
            lt.LoadTypes.ANY,
            lt.Units.TIMESTEPS,
            output_description=f"here a description for {self.OpenWindow} will follow.",
        )

        # =================================================================================================================================
        # Add and get default connections

        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(self.get_default_connections_from_utsp_occupancy())
        self.add_default_connections(self.get_default_connections_from_hds())
        self.add_default_connections(self.get_default_connections_from_electric_heater())
        self.add_default_connections(self.get_default_connections_from_energy_management_system())

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

    def get_default_connections_from_electric_heater(
        self,
    ):
        """Get electric heating default connections."""

        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.generic_electric_heating"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "ElectricHeating")
        connections = []
        hds_classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                Building.ThermalPowerDelivered,
                hds_classname,
                component_class.ThermalOutputShPower,
            )
        )
        return connections

    def get_default_connections_from_energy_management_system(
        self,
    ):
        """Get energy management system default connections."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.controller_l2_energy_management_system"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "L2GenericEnergyManagementSystem")
        connections = []
        ems_classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                Building.BuildingTemperatureModifier,
                ems_classname,
                component_class.BuildingIndoorTemperatureModifier,
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

        internal_heat_gains_through_devices_in_watt = stsv.get_input_value(self.device_heat_gain_channel)

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
            solar_heat_gain_through_windows_in_watt = self.get_solar_heat_gain_through_windows(
                azimuth=azimuth,
                direct_normal_irradiance=direct_normal_irradiance,
                direct_horizontal_irradiance=direct_horizontal_irradiance,
                global_horizontal_irradiance=global_horizontal_irradiance,
                direct_normal_irradiance_extra=direct_normal_irradiance_extra,
                apparent_zenith=apparent_zenith,
            )
        else:
            solar_heat_gain_through_windows_in_watt = self.solar_heat_gain_through_windows[timestep]

        # calc total thermal power to building from all heat sources

        total_thermal_power_to_residence_in_watt = (
            internal_heat_gains_through_occupancy_in_watt
            + internal_heat_gains_through_devices_in_watt
            + solar_heat_gain_through_windows_in_watt
            + thermal_power_delivered_in_watt
        )

        # calc temperatures and heat flow rates with crank nicolson method from ISO 13790
        (
            thermal_mass_average_bulk_temperature_in_celsius,
            # heat_loss_in_watt,
            internal_surface_temperature_in_celsius,
            indoor_air_temperature_in_celsius,
            internal_heat_flux_to_thermal_mass_in_watt,
            internal_heat_flux_to_internal_room_surface_in_watt,
            next_thermal_mass_temperature_in_celsius,
            internal_heat_flux_to_indoor_air_in_watt,
            total_thermal_mass_heat_flux_in_watt,
        ) = self.calc_crank_nicolson(
            thermal_power_delivered_in_watt=thermal_power_delivered_in_watt,
            internal_heat_gains_in_watt=internal_heat_gains_through_occupancy_in_watt
            + internal_heat_gains_through_devices_in_watt,
            solar_heat_gains_in_watt=solar_heat_gain_through_windows_in_watt,
            outside_temperature_in_celsius=temperature_outside_in_celsius,
            thermal_mass_temperature_prev_in_celsius=previous_thermal_mass_temperature_in_celsius,
        )
        self.state.thermal_mass_temperature_in_celsius = thermal_mass_average_bulk_temperature_in_celsius

        # if indoor temperature is too high make complete air exchange by opening the windows until outdoor temperature or initial temperature is reached
        if (
            self.buildingconfig.enable_opening_windows is True
            and self.buildingconfig.initial_internal_temperature_in_celsius
            < self.set_cooling_temperature_in_celsius
            < indoor_air_temperature_in_celsius
            and temperature_outside_in_celsius < indoor_air_temperature_in_celsius
        ):
            indoor_air_temperature_in_celsius = max(
                self.buildingconfig.initial_internal_temperature_in_celsius,
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
            heat_flux_indoor_air_in_watt=internal_heat_flux_to_indoor_air_in_watt,
            heat_flux_internal_room_surface_in_watt=internal_heat_flux_to_internal_room_surface_in_watt,
        )
        theoretical_thermal_energy_building_demand_in_watt_hour = (
            theoretical_thermal_building_demand_in_watt * self.my_simulation_parameters.seconds_per_timestep / 3.6e3
        )

        # Split into heating and cooling demand to avoid averaging out values when aggregating
        theoretical_heating_demand_in_watt = (
            theoretical_thermal_building_demand_in_watt if theoretical_thermal_building_demand_in_watt > 0 else 0
        )
        theoretical_heating_energy_demand_in_watt_hour = (
            theoretical_heating_demand_in_watt * self.my_simulation_parameters.seconds_per_timestep / 3.6e3
        )
        theoretical_cooling_demand_in_watt = (
            theoretical_thermal_building_demand_in_watt if theoretical_thermal_building_demand_in_watt < 0 else 0
        )
        theoretical_cooling_energy_demand_in_watt_hour = (
            theoretical_cooling_demand_in_watt * self.my_simulation_parameters.seconds_per_timestep / 3.6e3
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

        stsv.set_output_value(self.total_thermal_power_to_residence_channel, total_thermal_power_to_residence_in_watt)

        stsv.set_output_value(self.solar_gain_through_windows_channel, solar_heat_gain_through_windows_in_watt)
        stsv.set_output_value(
            self.internal_heat_gains_from_residents_and_devices_channel,
            internal_heat_gains_through_occupancy_in_watt + internal_heat_gains_through_devices_in_watt,
        )

        stsv.set_output_value(
            self.theoretical_thermal_building_demand_channel,
            theoretical_thermal_building_demand_in_watt,
        )

        stsv.set_output_value(
            self.theoretical_heating_demand_channel,
            theoretical_heating_demand_in_watt,
        )

        stsv.set_output_value(
            self.theoretical_cooling_demand_channel,
            theoretical_cooling_demand_in_watt,
        )

        stsv.set_output_value(
            self.theoretical_thermal_energy_building_demand_channel,
            theoretical_thermal_energy_building_demand_in_watt_hour,
        )

        stsv.set_output_value(
            self.theoretical_heating_energy_demand_channel,
            theoretical_heating_energy_demand_in_watt_hour,
        )

        stsv.set_output_value(
            self.theoretical_cooling_energy_demand_channel,
            theoretical_cooling_energy_demand_in_watt_hour,
        )

        stsv.set_output_value(
            self.heat_flow_rate_to_thermal_mass_node_channel,
            internal_heat_flux_to_thermal_mass_in_watt,
        )

        stsv.set_output_value(
            self.heat_flow_rates_to_internal_surface_node_channel,
            internal_heat_flux_to_internal_room_surface_in_watt,
        )
        stsv.set_output_value(self.total_heat_flow_rates_thermal_mass_channel, total_thermal_mass_heat_flux_in_watt)
        stsv.set_output_value(
            self.open_window_channel,
            self.window_open,
        )

        # Saves solar gains cache
        if not self.is_in_cache:
            self.cache[timestep] = solar_heat_gain_through_windows_in_watt
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
        # Warn when internal-heat-gain inputs are not connected.  These inputs
        # are optional so that setups without an occupancy component (e.g. the
        # simple air-conditioner household) can run, but silently defaulting the
        # heat gains to 0 W changes the thermal balance.  A warning makes the
        # omission visible without failing the simulation.
        if self.occupancy_heat_gain_channel.source_output is None:
            log.warning(
                f"Building '{self.component_name}': the 'HeatingByResidents' input is not "
                "connected. Internal heat gains from occupants default to 0 W."
            )
        if self.device_heat_gain_channel.source_output is None:
            log.warning(
                f"Building '{self.component_name}': the 'HeatingByDevices' input is not "
                "connected. Internal heat gains from devices default to 0 W."
            )
        if self.buildingconfig.predictive:
            # get weather forecast to compute forecasted solar gains

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
                    # _,
                    phi_ia_yearly,
                    phi_st_yearly,
                    phi_m_yearly,
                ) = self.calc_internal_heat_flows_from_internal_gains_and_solar_gains(
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

        # Get building params
        (
            self.floor_area_in_m2,
            self.facade_area_in_m2,
            self.roof_area_in_m2,
            self.window_area_in_m2,
            self.door_area_in_m2,
            self.floor_u_value_in_watt_per_m2_per_kelvin,
            self.facade_u_value_in_watt_per_m2_per_kelvin,
            self.roof_u_value_in_watt_per_m2_per_kelvin,
            self.window_u_value_in_watt_per_m2_per_kelvin,
            self.door_u_value_in_watt_per_m2_per_kelvin,
        ) = self.get_building_params()

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

        reduction_factor_for_non_perpedicular_radiation = (
            self.my_building_information.reduction_factor_for_non_perpedicular_radiation
        )
        reduction_factor_for_frame_area_fraction_of_window = (
            self.my_building_information.reduction_factor_for_frame_area_fraction_of_window
        )
        reduction_factor_for_external_vertical_shading = (
            self.my_building_information.reduction_factor_for_external_vertical_shading
        )
        total_solar_energy_transmittance_for_perpedicular_radiation = (
            self.my_building_information.total_solar_energy_transmittance_for_perpedicular_radiation
        )

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

        lines.append("-------------------------------------------------------------------------------------------")
        lines.append("Building Construction:")
        lines.append("--------------------------------------------")
        lines.append(f"Number of Apartments: {self.my_building_information.number_of_apartments}")
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
        return self.buildingconfig.get_string_dict() + lines

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> cp.OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        if (
                self.config.maintenance_costs_in_euro_per_year in [None, 0.0] or
                self.config.investment_costs_in_euro in [None, 0.0]
        ):
            opex_cost_data_class = cp.OpexCostDataClass.get_default_opex_cost_data_class()
        else:
            opex_cost_data_class = cp.OpexCostDataClass(
                opex_energy_cost_in_euro=0,
                opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
                co2_footprint_in_kg=0,
                total_consumption_in_kwh=0,
                loadtype=lt.LoadTypes.ANY,
                kpi_tag=KpiTagEnumClass.BUILDING,
            )
        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(
        config: BuildingConfig, simulation_parameters: SimulationParameters
    ) -> cp.CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        if (
                config.lifetime_in_years in [None, 0.0] or
                config.investment_costs_in_euro in [None, 0.0] or
                config.device_co2_footprint_in_kg in [None, 0.0]
        ):
            capex_cost_data_class = cp.CapexCostDataClass.get_default_capex_cost_data_class()
        else:
            assert config.lifetime_in_years is not None
            assert config.investment_costs_in_euro is not None
            assert config.device_co2_footprint_in_kg is not None
            seconds_per_year = 365 * 24 * 60 * 60
            capex_per_simulated_period = ((config.investment_costs_in_euro / config.lifetime_in_years) *
                                          (simulation_parameters.duration.total_seconds() / seconds_per_year)
                                          )
            device_co2_footprint_per_simulated_period = ((config.device_co2_footprint_in_kg / config.lifetime_in_years) *
                                                         (simulation_parameters.duration.total_seconds() /
                                                          seconds_per_year)
                                                         )
            capex_cost_data_class = cp.CapexCostDataClass(
                capex_investment_cost_in_euro=config.investment_costs_in_euro,
                device_co2_footprint_in_kg=config.device_co2_footprint_in_kg,
                lifetime_in_years=config.lifetime_in_years,
                capex_investment_cost_for_simulated_period_in_euro=capex_per_simulated_period,
                device_co2_footprint_for_simulated_period_in_kg=device_co2_footprint_per_simulated_period,
            )
        return capex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""

        list_of_kpi_entries: List[KpiEntry] = []
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                list_of_kpi_entries = self.get_building_kpis_from_outputs(
                    output=output,
                    index=index,
                    postprocessing_results=postprocessing_results,
                    list_of_kpi_entries=list_of_kpi_entries,
                )
                list_of_kpi_entries = self.get_building_kpis_from_building_information(
                    list_of_kpi_entries=list_of_kpi_entries
                )
                list_of_kpi_entries = self.get_building_temperature_deviation_from_set_temperatures(
                    output=output,
                    index=index,
                    postprocessing_results=postprocessing_results,
                    list_of_kpi_entries=list_of_kpi_entries,
                )

        return list_of_kpi_entries

    def get_building_kpis_from_building_information(self, list_of_kpi_entries: List[KpiEntry]) -> List[KpiEntry]:
        """Check building kpi values.

        Check for all timesteps and count the
        time when the temperature is outside of the building set temperatures
        in order to verify if energy system provides enough heating and cooling.
        """
        # get heating load and heating ref temperature
        heating_load_in_watt = self.my_building_information.max_thermal_building_demand_in_watt
        # get building area
        scaled_conditioned_floor_area_in_m2 = self.my_building_information.scaled_conditioned_floor_area_in_m2
        # get rooftop area
        scaled_rooftop_area_in_m2 = self.my_building_information.roof_area_in_m2
        # get specific heating load
        specific_heating_load_in_watt_per_m2 = heating_load_in_watt / scaled_conditioned_floor_area_in_m2
        # get tabula ref value for heating demand per year
        specific_heating_demand_per_year_tabula = self.my_building_information.tabula_ref_energy_need_for_heating_reference_in_kilowatthour_per_m2_per_year

        # make kpi entries and append to list
        heating_load_in_watt_entry = KpiEntry(
            name="Building heating load",
            unit="W",
            value=heating_load_in_watt,
            tag=KpiTagEnumClass.BUILDING,
            description=self.component_name,
        )
        list_of_kpi_entries.append(heating_load_in_watt_entry)

        scaled_conditioned_floor_area_in_m2_entry = KpiEntry(
            name="Conditioned floor area",
            unit="m2",
            value=scaled_conditioned_floor_area_in_m2,
            tag=KpiTagEnumClass.BUILDING,
            description=self.component_name,
        )
        list_of_kpi_entries.append(scaled_conditioned_floor_area_in_m2_entry)

        scaled_rooftop_area_in_m2_entry = KpiEntry(
            name="Rooftop area",
            unit="m2",
            value=scaled_rooftop_area_in_m2,
            tag=KpiTagEnumClass.BUILDING,
            description=self.component_name,
        )
        list_of_kpi_entries.append(scaled_rooftop_area_in_m2_entry)

        specific_heating_load_in_watt_per_m2_entry = KpiEntry(
            name="Specific heating load",
            unit="W/m2",
            value=specific_heating_load_in_watt_per_m2,
            tag=KpiTagEnumClass.BUILDING,
            description=self.component_name,
        )
        list_of_kpi_entries.append(specific_heating_load_in_watt_per_m2_entry)

        specific_heating_demand_ref_in_watt_per_m2_entry = KpiEntry(
            name="Tabula reference energy need for heating",
            unit="kWh/m2",
            value=specific_heating_demand_per_year_tabula,
            tag=KpiTagEnumClass.BUILDING,
            description=self.component_name,
        )
        list_of_kpi_entries.append(specific_heating_demand_ref_in_watt_per_m2_entry)
        return list_of_kpi_entries

    def get_building_temperature_deviation_from_set_temperatures(
        self, output: Any, index: int, postprocessing_results: pd.DataFrame, list_of_kpi_entries: List[KpiEntry]
    ) -> List[KpiEntry]:
        """Check building temperatures.

        Check for all timesteps and count the
        time when the temperature is outside of the building set temperatures
        in order to verify if energy system provides enough heating and cooling.
        """

        temperature_difference_of_building_being_below_heating_set_temperature = 0
        temperature_difference_of_building_being_below_cooling_set_temperature = 0
        temperature_hours_of_building_being_below_heating_set_temperature = None
        temperature_hours_of_building_being_above_cooling_set_temperature = None
        min_temperature_reached_in_celsius = None
        max_temperature_reached_in_celsius = None
        if output.field_name == self.TemperatureIndoorAir:
            indoor_temperatures_in_celsius = postprocessing_results.iloc[:, index]
            for temperature in indoor_temperatures_in_celsius:
                if temperature < self.set_heating_temperature_in_celsius:
                    temperature_difference_heating = self.set_heating_temperature_in_celsius - temperature

                    temperature_difference_of_building_being_below_heating_set_temperature = (
                        temperature_difference_of_building_being_below_heating_set_temperature
                        + temperature_difference_heating
                    )
                elif temperature > self.set_cooling_temperature_in_celsius:
                    temperature_difference_cooling = temperature - self.set_cooling_temperature_in_celsius
                    temperature_difference_of_building_being_below_cooling_set_temperature = (
                        temperature_difference_of_building_being_below_cooling_set_temperature
                        + temperature_difference_cooling
                    )

            temperature_hours_of_building_being_below_heating_set_temperature = (
                temperature_difference_of_building_being_below_heating_set_temperature
                * self.seconds_per_timestep
                / 3600
            )

            temperature_hours_of_building_being_above_cooling_set_temperature = (
                temperature_difference_of_building_being_below_cooling_set_temperature
                * self.seconds_per_timestep
                / 3600
            )

            # get also max and min indoor air temperature
            min_temperature_reached_in_celsius = float(min(indoor_temperatures_in_celsius.values))
            max_temperature_reached_in_celsius = float(max(indoor_temperatures_in_celsius.values))

            # make kpi entries and append to list
            temperature_hours_of_building_below_heating_set_temperature_entry = KpiEntry(
                name=f"Temperature deviation of building indoor air temperature being below set temperature {self.set_heating_temperature_in_celsius} Celsius",
                unit="°C*h",
                value=temperature_hours_of_building_being_below_heating_set_temperature,
                tag=KpiTagEnumClass.BUILDING,
                description=self.component_name,
            )
            list_of_kpi_entries.append(temperature_hours_of_building_below_heating_set_temperature_entry)
            temperature_hours_of_building_above_cooling_set_temperature_entry = KpiEntry(
                name=f"Temperature deviation of building indoor air temperature being above set temperature {self.set_cooling_temperature_in_celsius} Celsius",
                unit="°C*h",
                value=temperature_hours_of_building_being_above_cooling_set_temperature,
                tag=KpiTagEnumClass.BUILDING,
                description=self.component_name,
            )
            list_of_kpi_entries.append(temperature_hours_of_building_above_cooling_set_temperature_entry)
            min_temperature_reached_in_celsius_entry = KpiEntry(
                name="Minimum building indoor air temperature reached",
                unit="°C",
                value=min_temperature_reached_in_celsius,
                tag=KpiTagEnumClass.BUILDING,
                description=self.component_name,
            )
            list_of_kpi_entries.append(min_temperature_reached_in_celsius_entry)
            max_temperature_reached_in_celsius_entry = KpiEntry(
                name="Maximum building indoor air temperature reached",
                unit="°C",
                value=max_temperature_reached_in_celsius,
                tag=KpiTagEnumClass.BUILDING,
                description=self.component_name,
            )
            list_of_kpi_entries.append(max_temperature_reached_in_celsius_entry)
        return list_of_kpi_entries

    def get_building_kpis_from_outputs(
        self, output: Any, index: int, postprocessing_results: pd.DataFrame, list_of_kpi_entries: List[KpiEntry]
    ) -> List[KpiEntry]:
        """Get KPIs for building outputs."""
        energy_gains_from_solar_in_kilowatt_hour: Optional[float] = None
        energy_gains_from_internal_in_kilowatt_hour: Optional[float] = None
        heating_demand_in_kilowatt_hour: Optional[float] = None
        cooling_demand_in_kilowatt_hour: Optional[float] = None

        if output.field_name == self.TheoreticalThermalBuildingDemand:
            thermal_demand_values = postprocessing_results.iloc[:, index]
            heating_demand_in_kilowatt_hour = KpiHelperClass.compute_total_energy_from_power_timeseries(
                thermal_demand_values[thermal_demand_values > 0], time_resolution_in_seconds=self.seconds_per_timestep
            )
            cooling_demand_in_kilowatt_hour = KpiHelperClass.compute_total_energy_from_power_timeseries(
                thermal_demand_values[thermal_demand_values < 0], time_resolution_in_seconds=self.seconds_per_timestep
            )

            heating_demand_entry = KpiEntry(
                name="Theoretical heating demand",
                unit="kWh",
                value=heating_demand_in_kilowatt_hour,
                tag=KpiTagEnumClass.BUILDING,
                description=self.component_name,
            )
            list_of_kpi_entries.append(heating_demand_entry)

            cooling_demand_entry = KpiEntry(
                name="Theoretical cooling demand",
                unit="kWh",
                value=cooling_demand_in_kilowatt_hour,
                tag=KpiTagEnumClass.BUILDING,
                description=self.component_name,
            )
            list_of_kpi_entries.append(cooling_demand_entry)

        elif output.field_name == self.SolarGainThroughWindows:
            solar_gains_values_in_watt = postprocessing_results.iloc[:, index]
            # get energy from power
            energy_gains_from_solar_in_kilowatt_hour = KpiHelperClass.compute_total_energy_from_power_timeseries(
                power_timeseries_in_watt=solar_gains_values_in_watt, time_resolution_in_seconds=self.seconds_per_timestep
            )
            energy_gains_from_solar_entry = KpiEntry(
                name="Solar energy gains",
                unit="kWh",
                value=energy_gains_from_solar_in_kilowatt_hour,
                tag=KpiTagEnumClass.BUILDING,
                description=self.component_name,
            )
            list_of_kpi_entries.append(energy_gains_from_solar_entry)

        elif output.field_name == self.InternalHeatGainsFromOccupancy:
            internal_gains_values_in_watt = postprocessing_results.iloc[:, index]
            # get energy from power
            energy_gains_from_internal_in_kilowatt_hour = KpiHelperClass.compute_total_energy_from_power_timeseries(
                power_timeseries_in_watt=internal_gains_values_in_watt, time_resolution_in_seconds=self.seconds_per_timestep
            )
            energy_gains_from_internal_entry = KpiEntry(
                name="Internal energy gains",
                unit="kWh",
                value=energy_gains_from_internal_in_kilowatt_hour,
                tag=KpiTagEnumClass.BUILDING,
                description=self.component_name,
            )
            list_of_kpi_entries.append(energy_gains_from_internal_entry)

        return list_of_kpi_entries

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
        transmission_heat_transfer_coeff_windows_and_door_in_watt_per_kelvin = (
            self.my_building_information.heat_conductance_window_in_watt_per_kelvin
            + self.my_building_information.heat_conductance_door_in_watt_per_kelvin
        )

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
        # here modification for scalability: instead of reading H_Transmission from buildingdata it will be calculated manually using
        # input values U_Actual, A_Calc and b_Transmission also given by TABULA buildingdata

        transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin = (
            self.my_building_information.heat_conductance_facade_in_watt_per_kelvin
            + self.my_building_information.heat_conductance_roof_in_watt_per_kelvin
            + self.my_building_information.heat_conductance_floor_in_watt_per_kelvin
        )

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
            self.my_building_information.heat_conductance_ventilation_in_watt_per_kelvin
        )

        return (
            transmission_coeff_windows_and_door_in_watt_per_kelvin,
            internal_part_of_transmission_coeff_opaque_elements_in_watt_per_kelvin,
            transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin,
            external_part_of_transmission_coeff_opaque_elements_in_watt_per_kelvin,
            heat_transfer_coeff_indoor_air_and_internal_surface_in_watt_per_kelvin,
            thermal_conductance_by_ventilation_in_watt_per_kelvin,
        )

    def get_building_params(
        self,
    ) -> Tuple[
        float,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
    ]:
        """Get the building params from building information class."""
        floor_area_in_m2 = self.my_building_information.floor_area_in_m2
        facade_area_in_m2 = self.my_building_information.facade_area_in_m2
        roof_area_in_m2 = self.my_building_information.roof_area_in_m2
        window_area_in_m2 = self.my_building_information.window_area_in_m2
        door_area_in_m2 = self.my_building_information.door_area_in_m2
        floor_u_value_in_watt_per_m2_per_kelvin = self.my_building_information.floor_u_value_in_watt_per_m2_per_kelvin
        facade_u_value_in_watt_per_m2_per_kelvin = self.my_building_information.facade_u_value_in_watt_per_m2_per_kelvin
        roof_u_value_in_watt_per_m2_per_kelvin = self.my_building_information.roof_u_value_in_watt_per_m2_per_kelvin
        window_u_value_in_watt_per_m2_per_kelvin = self.my_building_information.window_u_value_in_watt_per_m2_per_kelvin
        door_u_value_in_watt_per_m2_per_kelvin = self.my_building_information.door_u_value_in_watt_per_m2_per_kelvin

        return (
            floor_area_in_m2,
            floor_u_value_in_watt_per_m2_per_kelvin,
            facade_area_in_m2,
            facade_u_value_in_watt_per_m2_per_kelvin,
            roof_area_in_m2,
            roof_u_value_in_watt_per_m2_per_kelvin,
            window_area_in_m2,
            window_u_value_in_watt_per_m2_per_kelvin,
            door_area_in_m2,
            door_u_value_in_watt_per_m2_per_kelvin,
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

    def calc_internal_heat_flows_from_internal_gains_and_solar_gains(
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

        # # Heat loss in W, before labeled Phi_loss
        # heat_loss_in_watt = (
        #     self.transmission_heat_transfer_coeff_windows_and_door_in_watt_per_kelvin
        #     / (
        #         self.my_building_information.heat_transfer_coeff_thermal_mass_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin
        #         * self.my_building_information.total_internal_surface_area_in_m2
        #     )
        # ) * (0.5 * internal_heat_gains_in_watt + solar_heat_gains_in_watt)

        return (
            heat_flux_indoor_air_in_watt,
            heat_flux_internal_room_surface_in_watt,
            heat_flux_thermal_mass_in_watt,
            # heat_loss_in_watt,
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

    def calc_total_thermal_mass_heat_flux_in_watt(
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
    ) -> Tuple[float, float, float, float, float, float, float, float]:  # , float]:
        """Determine node temperatures and computes derivation to determine the new node temperatures.

        Used in: has_demand(), solve_energy(), calc_energy_demand()
        # section C.3 in [C.3 ISO 13790]
        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        Alternatively, described in paper [2].
        """

        # Updates internal flows from internal and solar gains
        (
            heat_flux_to_indoor_air_in_watt,
            heat_flux_to_internal_room_surface_in_watt,
            heat_flux_to_thermal_mass_in_watt,
            # heat_loss_in_watt,
        ) = self.calc_internal_heat_flows_from_internal_gains_and_solar_gains(
            internal_heat_gains_in_watt,
            solar_heat_gains_in_watt,
        )

        # Updates total flow, this was denoted phi_m_tot before
        total_thermal_mass_heat_flux_in_watt = self.calc_total_thermal_mass_heat_flux_in_watt(
            outside_temperature_in_celsius,
            thermal_power_delivered_in_watt,
            heat_flux_indoor_air_in_watt=heat_flux_to_indoor_air_in_watt,
            heat_flux_internal_room_surface_in_watt=heat_flux_to_internal_room_surface_in_watt,
            heat_flux_thermal_mass_in_watt=heat_flux_to_thermal_mass_in_watt,
        )

        # calculates the new bulk temperature POINT from the old one # CHECKED Requires t_m_prev
        next_thermal_mass_temperature_in_celsius = self.calc_next_thermal_mass_temperature_in_celsius(
            thermal_mass_temperature_prev_in_celsius,
            equivalent_heat_flux_in_watt=total_thermal_mass_heat_flux_in_watt,
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
            heat_flux_indoor_air_in_watt=heat_flux_to_indoor_air_in_watt,
            heat_flux_internal_room_surface_in_watt=heat_flux_to_internal_room_surface_in_watt,
        )

        # Updates indoor air temperature (t_air)
        indoor_air_temperature_in_celsius = self.calc_temperature_of_the_inside_air_in_celsius(
            outside_temperature_in_celsius,
            internal_room_surface_temperature_in_celsius,
            thermal_power_delivered_in_watt,
            heat_flux_indoor_air_in_watt=heat_flux_to_indoor_air_in_watt,
        )

        return (
            thermal_mass_average_bulk_temperature_in_celsius,
            # heat_loss_in_watt,
            internal_room_surface_temperature_in_celsius,
            indoor_air_temperature_in_celsius,
            heat_flux_to_thermal_mass_in_watt,
            heat_flux_to_internal_room_surface_in_watt,
            next_thermal_mass_temperature_in_celsius,
            heat_flux_to_indoor_air_in_watt,
            total_thermal_mass_heat_flux_in_watt,
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
