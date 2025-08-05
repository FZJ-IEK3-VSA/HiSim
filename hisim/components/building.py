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
import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, List, Optional, Tuple

import pandas as pd
import pvlib
from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log, utils
from hisim.components.loadprofilegenerator_utsp_connector import UtspLpgConnector
from hisim.components.weather import Weather
from hisim.loadtypes import OutputPostprocessingRules
from hisim.sim_repository_singleton import SingletonDictKeyEnum, SingletonSimRepository
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass, KpiHelperClass

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

    building_name: str
    name: str
    heating_reference_temperature_in_celsius: float
    building_code: str
    building_heat_capacity_class: str
    initial_internal_temperature_in_celsius: float
    absolute_conditioned_floor_area_in_m2: Optional[float]
    total_base_area_in_m2: Optional[float]
    number_of_apartments: Optional[float]
    max_thermal_building_demand_in_watt: Optional[float]
    floor_u_value_in_watt_per_m2_per_kelvin: Optional[float]
    floor_area_in_m2: Optional[float]
    facade_u_value_in_watt_per_m2_per_kelvin: Optional[float]
    facade_area_in_m2: Optional[float]
    roof_u_value_in_watt_per_m2_per_kelvin: Optional[float]
    roof_area_in_m2: Optional[float]
    window_u_value_in_watt_per_m2_per_kelvin: Optional[float]
    window_area_in_m2: Optional[float]
    door_u_value_in_watt_per_m2_per_kelvin: Optional[float]
    door_area_in_m2: Optional[float]
    predictive: bool
    set_heating_temperature_in_celsius: float
    set_cooling_temperature_in_celsius: float
    enable_opening_windows: bool
    #: CO2 footprint of investment in kg
    device_co2_footprint_in_kg:  Optional[float]
    #: cost for investment in Euro
    investment_costs_in_euro:  Optional[float]
    #: lifetime in years
    lifetime_in_years:  Optional[float]
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year:  Optional[float]
    # subsidies as percentage of investment costs
    subsidy_as_percentage_of_investment_costs: Optional[float]

    @classmethod
    def get_default_german_single_family_home(
        cls,
        set_heating_temperature_in_celsius: float = 20.0,
        set_cooling_temperature_in_celsius: float = 25.0,
        heating_reference_temperature_in_celsius: float = -7.0,
        max_thermal_building_demand_in_watt: Optional[float] = None,
        floor_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None,
        floor_area_in_m2: Optional[float] = None,
        facade_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None,
        facade_area_in_m2: Optional[float] = None,
        roof_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None,
        roof_area_in_m2: Optional[float] = None,
        window_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None,
        window_area_in_m2: Optional[float] = None,
        door_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None,
        door_area_in_m2: Optional[float] = None,
        building_name: str = "BUI1",
    ) -> Any:
        """Get a default Building."""
        config = BuildingConfig(
            building_name=building_name,
            name="Building",
            building_code="DE.N.SFH.05.Gen.ReEx.001.002",
            building_heat_capacity_class="medium",
            initial_internal_temperature_in_celsius=22.0,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
            absolute_conditioned_floor_area_in_m2=121.2,
            max_thermal_building_demand_in_watt=max_thermal_building_demand_in_watt,
            floor_u_value_in_watt_per_m2_per_kelvin=floor_u_value_in_watt_per_m2_per_kelvin,
            floor_area_in_m2=floor_area_in_m2,
            facade_u_value_in_watt_per_m2_per_kelvin=facade_u_value_in_watt_per_m2_per_kelvin,
            facade_area_in_m2=facade_area_in_m2,
            roof_u_value_in_watt_per_m2_per_kelvin=roof_u_value_in_watt_per_m2_per_kelvin,
            roof_area_in_m2=roof_area_in_m2,
            window_u_value_in_watt_per_m2_per_kelvin=window_u_value_in_watt_per_m2_per_kelvin,
            window_area_in_m2=window_area_in_m2,
            door_u_value_in_watt_per_m2_per_kelvin=door_u_value_in_watt_per_m2_per_kelvin,
            door_area_in_m2=door_area_in_m2,
            total_base_area_in_m2=None,
            number_of_apartments=None,
            predictive=False,
            set_heating_temperature_in_celsius=set_heating_temperature_in_celsius,
            set_cooling_temperature_in_celsius=set_cooling_temperature_in_celsius,
            enable_opening_windows=False,
            device_co2_footprint_in_kg=None,  # todo: check value
            investment_costs_in_euro=None,   # todo: check value
            maintenance_costs_in_euro_per_year=None,  # noqa: E501 # todo: check value
            subsidy_as_percentage_of_investment_costs=None,
            lifetime_in_years=None,  # todo: check value
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
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
    ):
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
            self.config.name,
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
            lt.LoadTypes.ON_OFF,
            lt.Units.TIMESTEPS,
            output_description=f"here a description for {self.OpenWindow} will follow.",
        )

        # =================================================================================================================================
        # Add and get default connections

        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(self.get_default_connections_from_utsp_occupancy())
        self.add_default_connections(self.get_default_connections_from_hds())
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
                thermal_demand_values[thermal_demand_values > 0], timeresolution=self.seconds_per_timestep
            )
            cooling_demand_in_kilowatt_hour = KpiHelperClass.compute_total_energy_from_power_timeseries(
                thermal_demand_values[thermal_demand_values < 0], timeresolution=self.seconds_per_timestep
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
                power_timeseries_in_watt=solar_gains_values_in_watt, timeresolution=self.seconds_per_timestep
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
                power_timeseries_in_watt=internal_gains_values_in_watt, timeresolution=self.seconds_per_timestep
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

    def __init__(
        self,
        config: BuildingConfig,
    ):
        """Initialize the class."""

        self.window_scaling_factor: float
        self.heat_transfer_coeff_thermal_mass_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin: float
        self.ratio_between_internal_surface_area_and_floor_area: float
        self.heat_transfer_coeff_indoor_air_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin: float
        self.building_heat_capacity_class_f_a: dict
        self.building_heat_capacity_class_f_c_in_joule_per_m2_per_kelvin: dict
        self.conditioned_floor_area_in_m2_tabula_ref: float
        self.scaled_conditioned_floor_area_in_m2: float
        self.scaling_factor_according_to_conditioned_living_area: float
        self.building_total_area_in_m2: float
        self.total_heat_conductance_transmission: float
        self.total_heat_conductance_ventilation: float
        self.floor_area_in_m2: float
        self.facade_area_in_m2: float
        self.roof_area_in_m2: float
        self.window_area_in_m2: float
        self.scaled_window_areas_in_m2: list
        self.max_thermal_building_demand_in_watt: float
        self.door_area_in_m2: float
        self.floor_u_value_in_watt_per_m2_per_kelvin: float
        self.floor_adjustment_factor_from_tabula: float
        self.heat_conductance_floor_in_watt_per_kelvin: float
        self.facade_u_value_in_watt_per_m2_per_kelvin: float
        self.facade_adjustment_factor_from_tabula: float
        self.roof_adjustment_factor_from_tabula: float
        self.door_adjustment_factor_from_tabula: float
        self.window_adjustment_factor_from_tabula: float
        self.roof_u_value_in_watt_per_m2_per_kelvin: float
        self.window_u_value_in_watt_per_m2_per_kelvin: float
        self.door_u_value_in_watt_per_m2_per_kelvin: float
        self.heat_conductance_facade_in_watt_per_kelvin: float
        self.heat_conductance_roof_in_watt_per_kelvin: float
        self.heat_conductance_window_in_watt_per_kelvin: float
        self.heat_conductance_door_in_watt_per_kelvin: float
        self.heat_conductance_thermal_bridging_in_watt_per_kelvin: float
        self.heat_conductance_ventilation_in_watt_per_kelvin: float
        self.buildingconfig = config

        # get set temperatures for building
        self.set_heating_temperature_for_building_in_celsius = self.buildingconfig.set_heating_temperature_in_celsius
        self.set_cooling_temperature_for_building_in_celsius = self.buildingconfig.set_cooling_temperature_in_celsius
        self.heating_reference_temperature_in_celsius = self.buildingconfig.heating_reference_temperature_in_celsius

        self.building_heat_capacity_class = self.buildingconfig.building_heat_capacity_class

        self.windows_directions: List[str]

        self.get_building_from_tabula()

        self.get_building_area_parameters()

        self.get_building_heat_transfer_parameters()

        self.get_constants()

        self.get_max_thermal_building_demand()

        (
            self.tabula_ref_solar_heat_load_during_heating_seasons_reference_in_kilowatthour_per_m2_per_year,
            self.tabula_ref_internal_heat_sources_reference_in_kilowatthour_per_m2_per_year,
            self.tabula_ref_total_heat_transfer_reference_in_kilowatthour_per_m2_per_year,
            self.tabula_ref_transmission_heat_losses_ref_in_kilowatthour_per_m2_per_year,
            self.tabula_ref_ventilation_heat_losses_ref_in_kilowatthour_per_m2_per_year,
            self.tabula_ref_energy_need_for_heating_reference_in_kilowatthour_per_m2_per_year,
            self.tabula_ref_thermal_capacity_of_building_thermal_mass_reference_in_watthour_per_m2_per_kelvin,
            self.tabula_ref_heat_transfer_coeff_by_ventilation_reference_in_watt_per_kelvin,
            self.tabula_ref_heat_transfer_coeff_by_transmission_ref_in_watt_per_m2_per_kelvin,
            self.tabula_ref_heat_transfer_coeff_by_ventilation_ref_in_watt_per_m2_per_kelvin,
            self.tabula_ref_gain_utilisation_factor_reference,
        ) = self.get_some_reference_data_from_tabula(
            buildingdata=self.buildingdata_ref,
            scaled_conditioned_floor_area_in_m2=self.scaled_conditioned_floor_area_in_m2,)

    def get_building_from_tabula(
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
        self.buildingdata_ref = d_f.loc[d_f["Code_BuildingVariant"] == self.buildingconfig.building_code]
        self.buildingcode = self.buildingconfig.building_code

    def get_constants(
        self,
    ):
        """Get the constants."""
        self.set_constants()

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

        self.reduction_factor_for_non_perpedicular_radiation = self.buildingdata_ref["F_w"].values[0]
        self.reduction_factor_for_frame_area_fraction_of_window = self.buildingdata_ref["F_f"].values[0]
        self.reduction_factor_for_external_vertical_shading = self.buildingdata_ref["F_sh_vert"].values[0]
        self.total_solar_energy_transmittance_for_perpedicular_radiation = self.buildingdata_ref["g_gl_n"].values[0]

        # Get number of apartments
        self.number_of_apartments = int(
            self.get_number_of_apartments(
                conditioned_floor_area_in_m2=self.scaled_conditioned_floor_area_in_m2,
                scaling_factor=self.scaling_factor_according_to_conditioned_living_area,
                buildingdata=self.buildingdata_ref,
            )
        )

    def set_constants(self):
        """Set important constants."""

        # Heat transfer coefficient between nodes "m" and "s" (12.2.2 E64 P79); labeled as h_ms in paper [2] (*** Check header)
        self.heat_transfer_coeff_thermal_mass_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin = 9.1
        # Dimensionless ratio between surfaces and the useful surfaces (7.2.2.2 E9 P36); labeled as A_at in paper [2] (*** Check header); before lambda_at
        self.ratio_between_internal_surface_area_and_floor_area = 4.5
        # Heat transfer coefficient between nodes "air" and "s" (7.2.2.2 E9 P35); labeled as h_is in paper [2] (*** Check header)
        self.heat_transfer_coeff_indoor_air_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin = 3.45

        # heat capacity class values in ISO 13790, table 12, p.69/70
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

    def get_building_area_parameters(self):
        """Get the building parameter."""
        # Reference area [m^2] (TABULA: Reference floor area A_C_Ref )Ref: ISO standard 7.2.2.2
        self.conditioned_floor_area_in_m2_tabula_ref = float((self.buildingdata_ref["A_C_Ref"].values[0]))

        (self.scaling_factor_according_to_conditioned_living_area, self.scaled_conditioned_floor_area_in_m2) = (
            self.get_scaling_factor_according_to_conditioned_living_area(
                conditioned_floor_area_in_m2_tabula_ref=self.conditioned_floor_area_in_m2_tabula_ref
            )
        )

        self.set_floor_area_parameter()

        # if self.facade_u_value_in_watt_per_m2_per_kelvin is not None or self.facade_area_in_m2 is not None:
        self.set_wall_area_parameter()

        # if self.roof_u_value_in_watt_per_m2_per_kelvin is not None or self.roof_area_in_m2 is not None:
        self.set_roof_area_parameter()

        # if self.window_u_value_in_watt_per_m2_per_kelvin is not None or self.window_area_in_m2 is not None:
        self.set_window_area_parameter()

        # if self.door_u_value_in_watt_per_m2_per_kelvin is not None or self.door_area_in_m2 is not None:
        self.set_door_area_parameter()

        self.building_total_area_in_m2 = (
            self.window_area_in_m2
            + self.floor_area_in_m2
            + self.door_area_in_m2
            + self.facade_area_in_m2
            + self.roof_area_in_m2
        )

    def get_building_heat_transfer_parameters(self):
        """Get the building heat transfer parameter."""

        self.set_floor_heat_transfer_parameter()

        # if self.facade_u_value_in_watt_per_m2_per_kelvin is not None or self.facade_area_in_m2 is not None:
        self.set_wall_heat_transfer_parameter()

        # if self.roof_u_value_in_watt_per_m2_per_kelvin is not None or self.roof_area_in_m2 is not None:
        self.set_roof_heat_transfer_parameter()

        # if self.window_u_value_in_watt_per_m2_per_kelvin is not None or self.window_area_in_m2 is not None:
        self.set_window_heat_transfer_parameter()

        # if self.door_u_value_in_watt_per_m2_per_kelvin is not None or self.door_area_in_m2 is not None:
        self.set_door_heat_transfer_parameter()

        self.set_thermal_bridging_parameter()

        self.set_ventilation_heat_transfer_parameter()

        self.total_heat_conductance_transmission = (
            self.heat_conductance_door_in_watt_per_kelvin
            + self.heat_conductance_window_in_watt_per_kelvin
            + self.heat_conductance_facade_in_watt_per_kelvin
            + self.heat_conductance_floor_in_watt_per_kelvin
            + self.heat_conductance_roof_in_watt_per_kelvin
            + self.heat_conductance_thermal_bridging_in_watt_per_kelvin
        )

        self.total_heat_conductance_ventilation = self.heat_conductance_ventilation_in_watt_per_kelvin

    def get_scaling_factor_according_to_conditioned_living_area(
        self, conditioned_floor_area_in_m2_tabula_ref: float
    ) -> Tuple[float, float]:
        """Calculate scaling factors for the building.

        Either the absolute conditioned floor area or the total base area should be given.
        The conditioned floor area, the envelope surface areas or window areas are scaled with a scaling factor.
        """

        if (
            self.buildingconfig.absolute_conditioned_floor_area_in_m2 is not None
            and self.buildingconfig.total_base_area_in_m2 is not None
        ):
            raise ValueError("Only one variable can be used, the other one must be None.")

            # scaling conditioned floor area
        if self.buildingconfig.absolute_conditioned_floor_area_in_m2 is not None:
            # this is for preventing that the conditioned_floor_area is 0 (some buildings in TABULA have conditioned_floor_area (A_C_Ref) = 0)
            if conditioned_floor_area_in_m2_tabula_ref == 0:
                scaled_conditioned_floor_area_in_m2 = self.buildingconfig.absolute_conditioned_floor_area_in_m2
                factor_of_absolute_floor_area_to_tabula_floor_area = 1.0
                self.buildingdata_ref["A_C_Ref"] = scaled_conditioned_floor_area_in_m2
            # scaling conditioned floor area
            else:
                factor_of_absolute_floor_area_to_tabula_floor_area = (
                    self.buildingconfig.absolute_conditioned_floor_area_in_m2 / conditioned_floor_area_in_m2_tabula_ref
                )
                scaled_conditioned_floor_area_in_m2 = (
                    conditioned_floor_area_in_m2_tabula_ref * factor_of_absolute_floor_area_to_tabula_floor_area
                )
            scaling_factor_according_to_conditioned_living_area = factor_of_absolute_floor_area_to_tabula_floor_area

        elif self.buildingconfig.total_base_area_in_m2 is not None:
            # this is for preventing that the conditioned_floor_area is 0
            if conditioned_floor_area_in_m2_tabula_ref == 0:
                scaled_conditioned_floor_area_in_m2 = self.buildingconfig.total_base_area_in_m2
                factor_of_total_base_area_to_tabula_floor_area = 1.0
                self.buildingdata_ref["A_C_Ref"] = scaled_conditioned_floor_area_in_m2
            # scaling conditioned floor area
            else:
                factor_of_total_base_area_to_tabula_floor_area = (
                    self.buildingconfig.total_base_area_in_m2 / conditioned_floor_area_in_m2_tabula_ref
                )
                scaled_conditioned_floor_area_in_m2 = (
                    conditioned_floor_area_in_m2_tabula_ref * factor_of_total_base_area_to_tabula_floor_area
                )
            scaling_factor_according_to_conditioned_living_area = factor_of_total_base_area_to_tabula_floor_area

            # if no value for building size is provided in config, use reference value from Tabula or 500 m^2.
        else:
            if conditioned_floor_area_in_m2_tabula_ref == 0:
                scaled_conditioned_floor_area_in_m2 = 500.0
                self.buildingdata_ref["A_C_Ref"] = scaled_conditioned_floor_area_in_m2
                log.warning(
                    "There is no reference given for absolute conditioned floor area in m^2, so a default of 500 m^2 is used."
                )
            else:
                scaled_conditioned_floor_area_in_m2 = conditioned_floor_area_in_m2_tabula_ref

            scaling_factor_according_to_conditioned_living_area = 1.0

        return scaling_factor_according_to_conditioned_living_area, scaled_conditioned_floor_area_in_m2

    def set_floor_area_parameter(
        self,
    ):
        """Manipulate building data of roof."""

        if self.buildingconfig.floor_area_in_m2 is None:
            area_floor_1 = float(self.buildingdata_ref["A_Floor_1"].values[0])
            area_floor_2 = float(self.buildingdata_ref["A_Floor_2"].values[0])
            self.floor_area_in_m2 = (
                area_floor_1 + area_floor_2
            ) * self.scaling_factor_according_to_conditioned_living_area
        else:
            self.floor_area_in_m2 = self.buildingconfig.floor_area_in_m2

    def set_wall_area_parameter(self):
        """Manipulate building data of walls."""
        if self.buildingconfig.facade_area_in_m2 is None:
            area_wall_1 = float(self.buildingdata_ref["A_Wall_1"].values[0])
            area_wall_2 = float(self.buildingdata_ref["A_Wall_2"].values[0])
            area_wall_3 = float(self.buildingdata_ref["A_Wall_3"].values[0])
            self.facade_area_in_m2 = (
                area_wall_1 + area_wall_2 + area_wall_3
            ) * self.scaling_factor_according_to_conditioned_living_area

        else:
            self.facade_area_in_m2 = self.buildingconfig.facade_area_in_m2

    def set_roof_area_parameter(
        self,
    ):
        """Manipulate building data of roof."""
        if self.buildingconfig.roof_area_in_m2 is None:
            area_roof_1 = float(self.buildingdata_ref["A_Roof_1"].values[0])
            area_roof_2 = float(self.buildingdata_ref["A_Roof_2"].values[0])
            self.roof_area_in_m2 = (
                area_roof_1 + area_roof_2
            ) * self.scaling_factor_according_to_conditioned_living_area
        else:
            self.roof_area_in_m2 = self.buildingconfig.roof_area_in_m2

    def set_window_area_parameter(
        self,
    ):
        """Manipulate building data of windows."""
        area_window_1_ref = float(self.buildingdata_ref["A_Window_1"].values[0])
        area_window_2_ref = float(self.buildingdata_ref["A_Window_2"].values[0])
        if self.buildingconfig.window_area_in_m2 is None:
            self.window_area_in_m2 = (
                area_window_1_ref + area_window_2_ref
            ) * self.scaling_factor_according_to_conditioned_living_area
        else:
            self.window_area_in_m2 = self.buildingconfig.window_area_in_m2

        self.window_scaling_factor = self.window_area_in_m2 / (area_window_1_ref + area_window_2_ref)

        # scaling window areas over wall area
        self.windows_directions = [
            "South",
            "East",
            "North",
            "West",
            "Horizontal",
        ]

        self.scaled_window_areas_in_m2 = []
        for windows_direction in self.windows_directions:
            window_area_of_direction_in_m2 = float(self.buildingdata_ref["A_Window_" + windows_direction].iloc[0])

            self.scaled_window_areas_in_m2.append(window_area_of_direction_in_m2 * self.window_scaling_factor)

    def set_door_area_parameter(
        self,
    ):
        """Manipulate building data of door."""
        if self.buildingconfig.door_area_in_m2 is None:
            area_door_1 = float(self.buildingdata_ref["A_Door_1"].values[0])
            self.door_area_in_m2 = area_door_1 * self.scaling_factor_according_to_conditioned_living_area
        else:
            self.door_area_in_m2 = self.buildingconfig.door_area_in_m2

    def set_floor_heat_transfer_parameter(
        self,
    ):
        """Manipulate building data of floor."""
        if self.buildingconfig.floor_u_value_in_watt_per_m2_per_kelvin is None:

            floor_u_value_in_watt_per_m2_per_kelvin_1 = float(self.buildingdata_ref["U_Actual_Floor_1"].values[0])
            floor_u_value_in_watt_per_m2_per_kelvin_2 = float(self.buildingdata_ref["U_Actual_Floor_2"].values[0])

            area_floor_1 = float(self.buildingdata_ref["A_Floor_1"].values[0])
            area_floor_2 = float(self.buildingdata_ref["A_Floor_2"].values[0])

            self.floor_u_value_in_watt_per_m2_per_kelvin = (
                (floor_u_value_in_watt_per_m2_per_kelvin_1 * area_floor_1)
                + (floor_u_value_in_watt_per_m2_per_kelvin_2 * area_floor_2)
            ) / (area_floor_1 + area_floor_2)

            b_floor_1 = float(self.buildingdata_ref["b_Transmission_Floor_1"].values[0])
            b_floor_2 = float(self.buildingdata_ref["b_Transmission_Floor_2"].values[0])

            self.floor_adjustment_factor_from_tabula = max([b_floor_1, b_floor_2])

        else:
            self.floor_u_value_in_watt_per_m2_per_kelvin = self.buildingconfig.floor_u_value_in_watt_per_m2_per_kelvin

            self.floor_adjustment_factor_from_tabula = 0.5

        self.heat_conductance_floor_in_watt_per_kelvin = (
            self.floor_u_value_in_watt_per_m2_per_kelvin
            * self.floor_area_in_m2
            * self.floor_adjustment_factor_from_tabula
        )

    def set_wall_heat_transfer_parameter(
        self,
    ):
        """Manipulate building data of wall."""
        if self.buildingconfig.facade_u_value_in_watt_per_m2_per_kelvin is None:

            facade_u_value_in_watt_per_m2_per_kelvin_1 = float(self.buildingdata_ref["U_Actual_Wall_1"].values[0])
            facade_u_value_in_watt_per_m2_per_kelvin_2 = float(self.buildingdata_ref["U_Actual_Wall_2"].values[0])
            facade_u_value_in_watt_per_m2_per_kelvin_3 = float(self.buildingdata_ref["U_Actual_Wall_3"].values[0])

            area_wall_1 = float(self.buildingdata_ref["A_Wall_1"].values[0])
            area_wall_2 = float(self.buildingdata_ref["A_Wall_2"].values[0])
            area_wall_3 = float(self.buildingdata_ref["A_Wall_3"].values[0])

            self.facade_u_value_in_watt_per_m2_per_kelvin = (
                (facade_u_value_in_watt_per_m2_per_kelvin_1 * area_wall_1)
                + (facade_u_value_in_watt_per_m2_per_kelvin_2 * area_wall_2)
                + (facade_u_value_in_watt_per_m2_per_kelvin_3 * area_wall_3)
            ) / (area_wall_1 + area_wall_2 + area_wall_3)

            b_wall_1 = float(self.buildingdata_ref["b_Transmission_Wall_1"].values[0])
            b_wall_2 = float(self.buildingdata_ref["b_Transmission_Wall_2"].values[0])
            b_wall_3 = float(self.buildingdata_ref["b_Transmission_Wall_3"].values[0])

            self.facade_adjustment_factor_from_tabula = max([b_wall_1, b_wall_2, b_wall_3])

        else:
            self.facade_u_value_in_watt_per_m2_per_kelvin = self.buildingconfig.facade_u_value_in_watt_per_m2_per_kelvin

            self.facade_adjustment_factor_from_tabula = 1

        self.heat_conductance_facade_in_watt_per_kelvin = (
            self.facade_u_value_in_watt_per_m2_per_kelvin
            * self.facade_area_in_m2
            * self.facade_adjustment_factor_from_tabula
        )

    def set_roof_heat_transfer_parameter(
        self,
    ):
        """Manipulate building data of heat transfer."""
        if self.buildingconfig.roof_u_value_in_watt_per_m2_per_kelvin is None:
            roof_u_value_in_watt_per_m2_per_kelvin_1 = float(self.buildingdata_ref["U_Actual_Roof_1"].values[0])
            roof_u_value_in_watt_per_m2_per_kelvin_2 = float(self.buildingdata_ref["U_Actual_Roof_2"].values[0])

            area_roof_1 = float(self.buildingdata_ref["A_Roof_1"].values[0])
            area_roof_2 = float(self.buildingdata_ref["A_Roof_2"].values[0])

            self.roof_u_value_in_watt_per_m2_per_kelvin = (
                (roof_u_value_in_watt_per_m2_per_kelvin_1 * area_roof_1)
                + (roof_u_value_in_watt_per_m2_per_kelvin_2 * area_roof_2)
            ) / (area_roof_1 + area_roof_2)

            b_roof_1 = float(self.buildingdata_ref["b_Transmission_Roof_1"].values[0])
            b_roof_2 = float(self.buildingdata_ref["b_Transmission_Roof_2"].values[0])

            self.roof_adjustment_factor_from_tabula = max([b_roof_1, b_roof_2])

        else:
            self.roof_u_value_in_watt_per_m2_per_kelvin = self.buildingconfig.roof_u_value_in_watt_per_m2_per_kelvin

            self.roof_adjustment_factor_from_tabula = 1

        self.heat_conductance_roof_in_watt_per_kelvin = (
            self.roof_u_value_in_watt_per_m2_per_kelvin * self.roof_area_in_m2 * self.roof_adjustment_factor_from_tabula
        )

    def set_window_heat_transfer_parameter(
        self,
    ):
        """Manipulate building data of heat transfer."""
        if self.buildingconfig.window_u_value_in_watt_per_m2_per_kelvin is None:
            window_u_value_in_watt_per_m2_per_kelvin_1 = float(self.buildingdata_ref["U_Actual_Window_1"].values[0])
            window_u_value_in_watt_per_m2_per_kelvin_2 = float(self.buildingdata_ref["U_Actual_Window_2"].values[0])

            area_window_1 = float(self.buildingdata_ref["A_Window_1"].values[0])
            area_window_2 = float(self.buildingdata_ref["A_Window_2"].values[0])

            self.window_u_value_in_watt_per_m2_per_kelvin = (
                (window_u_value_in_watt_per_m2_per_kelvin_1 * area_window_1)
                + (window_u_value_in_watt_per_m2_per_kelvin_2 * area_window_2)
            ) / (area_window_1 + area_window_2)
        else:
            self.window_u_value_in_watt_per_m2_per_kelvin = self.buildingconfig.window_u_value_in_watt_per_m2_per_kelvin

        self.window_adjustment_factor_from_tabula = 1

        self.heat_conductance_window_in_watt_per_kelvin = (
            self.window_u_value_in_watt_per_m2_per_kelvin
            * self.window_area_in_m2
            * self.window_adjustment_factor_from_tabula
        )

    def set_door_heat_transfer_parameter(
        self,
    ):
        """Manipulate building data of heat transfer."""
        if self.buildingconfig.door_u_value_in_watt_per_m2_per_kelvin is None:
            area_door_1 = float(self.buildingdata_ref["A_Door_1"].values[0])
            door_u_value_in_watt_per_m2_per_kelvin = float(self.buildingdata_ref["U_Actual_Door_1"].values[0])

            self.door_u_value_in_watt_per_m2_per_kelvin = (door_u_value_in_watt_per_m2_per_kelvin * area_door_1) / (
                area_door_1
            )
        else:
            self.door_u_value_in_watt_per_m2_per_kelvin = self.buildingconfig.door_u_value_in_watt_per_m2_per_kelvin

        self.door_adjustment_factor_from_tabula = 1

        self.heat_conductance_door_in_watt_per_kelvin = (
            self.door_u_value_in_watt_per_m2_per_kelvin * self.door_area_in_m2 * self.door_adjustment_factor_from_tabula
        )

    def set_thermal_bridging_parameter(
        self,
    ):
        """Manipulate building data of heat transfer."""
        if self.buildingdata_ref["delta_U_ThermalBridging"].values[0] == 0:
            self.buildingdata_ref["delta_U_ThermalBridging"] = 0.1

        delta_u_thermalbridging = float(self.buildingdata_ref["delta_U_ThermalBridging"].values[0])

        self.heat_conductance_thermal_bridging_in_watt_per_kelvin = (
            delta_u_thermalbridging * self.building_total_area_in_m2
        )

    def set_ventilation_heat_transfer_parameter(self):
        """Manipulate building data of heat transfer."""
        heat_capacity_of_air_per_volume_in_watt_hour_per_m3_per_kelvin = 0.34

        self.heat_conductance_ventilation_in_watt_per_kelvin = (
            heat_capacity_of_air_per_volume_in_watt_hour_per_m3_per_kelvin
            * (
                float(self.buildingdata_ref["n_air_use"].values[0])
                + float(self.buildingdata_ref["n_air_infiltration"].values[0])
            )
            * float(self.buildingdata_ref["h_room"].values[0])
            * self.scaled_conditioned_floor_area_in_m2
        )

    def get_max_thermal_building_demand(self):
        """Calculate max thermal demand."""
        if self.buildingconfig.max_thermal_building_demand_in_watt is None:

            self.max_thermal_building_demand_in_watt = (
                self.total_heat_conductance_transmission + self.total_heat_conductance_ventilation
            ) * (
                self.buildingconfig.initial_internal_temperature_in_celsius
                - self.buildingconfig.heating_reference_temperature_in_celsius
            )
        else:
            self.max_thermal_building_demand_in_watt = self.buildingconfig.max_thermal_building_demand_in_watt

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

    def get_some_reference_data_from_tabula(
        self, buildingdata: Any, scaled_conditioned_floor_area_in_m2: float
    ) -> Tuple[float, float, float, float, float, float, float, float, float, float, float]:
        """Get some reference parameter from Tabula."""

        # Floor area related heat load during heating season
        # reference taken from TABULA (* Check header) Q_sol [kWh/m2.a], before q_sol_ref (or solar heat sources?)
        tabula_ref_solar_heat_load_during_heating_seasons_reference_in_kilowatthour_per_m2_per_year = float(
            (buildingdata["q_sol"].values[0])
        )
        # Floor area related internal heat sources during heating season
        # reference taken from TABULA (* Check header) as Q_int [kWh/m2.a], before q_int_ref
        tabula_ref_internal_heat_sources_reference_in_kilowatthour_per_m2_per_year = float(buildingdata["q_int"].values[0])
        # Floor area related annual losses
        # reference taken from TABULA (* Check header) as Q_ht [kWh/m2.a], before q_ht_ref
        tabula_ref_total_heat_transfer_reference_in_kilowatthour_per_m2_per_year = float(buildingdata["q_ht"].values[0])
        # transmission heat losses
        tabula_ref_transmission_heat_losses_ref_in_kilowatthour_per_m2_per_year = float(buildingdata["q_ht_tr"].values[0])
        # ventilation heat losses
        tabula_ref_ventilation_heat_losses_ref_in_kilowatthour_per_m2_per_year = float(buildingdata["q_ht_ve"].values[0])
        # Energy need for heating
        # reference taken from TABULA (* Check header) as Q_H_nd [kWh/m2.a], before q_h_nd_ref
        tabula_ref_energy_need_for_heating_reference_in_kilowatthour_per_m2_per_year = float(buildingdata["q_h_nd"].values[0])
        # Internal heat capacity per m2 reference area [Wh/(m^2.K)] (TABULA: Internal heat capacity)
        tabula_ref_thermal_capacity_of_building_thermal_mass_reference_in_watthour_per_m2_per_kelvin = float(
            buildingdata["c_m"].values[0]
        )
        # gain utilisation factor eta_h_gn
        tabula_ref_gain_utilisation_factor_reference = float(buildingdata["eta_h_gn"].values[0])

        # Heat transfer coefficient by ventilation in watt per m2 per kelvin
        tabula_ref_heat_transfer_coeff_by_ventilation_reference_in_watt_per_m2_per_kelvin = float(
            buildingdata["h_Ventilation"].values[0]
        )
        if tabula_ref_heat_transfer_coeff_by_ventilation_reference_in_watt_per_m2_per_kelvin is None:
            raise ValueError("h_Ventilation was none.")
        # Heat transfer coefficient by ventilation in watt per kelvin
        tabula_ref_heat_transfer_coeff_by_ventilation_reference_in_watt_per_kelvin = (
            float(buildingdata["h_Ventilation"].values[0]) * scaled_conditioned_floor_area_in_m2
        )

        # Heat transfer coefficient by transmission in watt per m2 per kelvin
        tabula_ref_heat_transfer_coeff_by_transmission_reference_in_watt_per_m2_per_kelvin = float(
            buildingdata["h_Transmission"].values[0]
        )
        if tabula_ref_heat_transfer_coeff_by_transmission_reference_in_watt_per_m2_per_kelvin is None:
            raise ValueError("h_Transmission was none.")

        return (
            tabula_ref_solar_heat_load_during_heating_seasons_reference_in_kilowatthour_per_m2_per_year,
            tabula_ref_internal_heat_sources_reference_in_kilowatthour_per_m2_per_year,
            tabula_ref_total_heat_transfer_reference_in_kilowatthour_per_m2_per_year,
            tabula_ref_transmission_heat_losses_ref_in_kilowatthour_per_m2_per_year,
            tabula_ref_ventilation_heat_losses_ref_in_kilowatthour_per_m2_per_year,
            tabula_ref_energy_need_for_heating_reference_in_kilowatthour_per_m2_per_year,
            tabula_ref_thermal_capacity_of_building_thermal_mass_reference_in_watthour_per_m2_per_kelvin,
            tabula_ref_heat_transfer_coeff_by_ventilation_reference_in_watt_per_kelvin,
            tabula_ref_heat_transfer_coeff_by_transmission_reference_in_watt_per_m2_per_kelvin,
            tabula_ref_heat_transfer_coeff_by_ventilation_reference_in_watt_per_m2_per_kelvin,
            tabula_ref_gain_utilisation_factor_reference,
        )
