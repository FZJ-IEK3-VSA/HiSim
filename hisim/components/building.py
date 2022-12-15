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
    7. class BuildingController - calculates real heating demand and how much building is supposed to be heated up.

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
from typing import (
    List,
    Any
)
from functools import (
    lru_cache,
)
import math
from dataclasses import (
    dataclass,
)
from dataclasses_json import (
    dataclass_json,
)
import pvlib
import pandas as pd
import numpy as np

from hisim import (
    dynamic_component,
    utils,
)
from hisim import (
    component as cp,
)
from hisim import (
    loadtypes as lt,
)
from hisim import (
    log,
)
# from hisim.components.configuration import (
#     PhysicsConfig,
# )
# from hisim.components.configuration import (
#     LoadConfig,
# )
from hisim.components.loadprofilegenerator_utsp_connector import (
    UtspLpgConnector,
)
from hisim.simulationparameters import (
    SimulationParameters,
)
from hisim.components.weather import (
    Weather,
)
from hisim.components.loadprofilegenerator_connector import (
    Occupancy,
)


__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Dr. Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


@lru_cache(maxsize=16)
def calc_solar_heat_gains(
    sun_azimuth,
    direct_normal_irradiance,
    direct_horizontal_irradiance,
    global_horizontal_irradiance,
    direct_normal_irradiance_extra,
    apparent_zenith,
    altitude_tilt,
    azimuth_tilt,
    reduction_factor_with_area,
):
    """Calculates the Solar Gains in the building zone through the set Window.

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
    poa_irrad = pvlib.irradiance.get_total_irradiance(
        altitude_tilt,
        azimuth_tilt,
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


class BuildingState:

    """BuildingState class."""

    def __init__(
        self,
        thermal_mass_temperature_in_celsius: float,
        thermal_capacitance_in_joule_per_kelvin: float,
    ):
        """Constructs all the neccessary attributes for the BuildingState object."""
        # this is labeled as t_m in the paper [1] (** Check header)
        self.thermal_mass_temperature_in_celsius: float = (
            thermal_mass_temperature_in_celsius
        )
        # this is labeled as c_m in the paper [1] (** Check header)
        self.thermal_capacitance_in_joule_per_kelvin: float = (
            thermal_capacitance_in_joule_per_kelvin
        )

    def calc_stored_thermal_power_in_watt(
        self,
    ) -> float:
        """Calculates the thermal energy stored by the thermal mass per second (thermal power)."""
        return (
            self.thermal_mass_temperature_in_celsius
            * self.thermal_capacitance_in_joule_per_kelvin
        ) / 3600

    def self_copy(
        self,
    ):
        """Copies the Building State."""
        return BuildingState(
            self.thermal_mass_temperature_in_celsius,
            self.thermal_capacitance_in_joule_per_kelvin,
        )


class BuildingControllerState:

    """BuildingControllerState class."""

    def __init__(
        self,
        temperature_building_target_in_celsius: float,
        level_of_utilization: float,
    ):
        """Constructs all the neccessary attributes for the BuildingControllerState object."""
        self.temperature_building_target_in_celsius: float = (
            temperature_building_target_in_celsius
        )
        self.level_of_utilization: float = level_of_utilization

    def clone(self):
        """Copies the BuildingControllerState."""
        return BuildingControllerState(
            temperature_building_target_in_celsius=self.temperature_building_target_in_celsius,
            level_of_utilization=self.level_of_utilization,
        )


@dataclass_json
@dataclass
class BuildingConfig(cp.ConfigBase):

    """Configuration of the Building class."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return Building.get_full_classname()

    name: str
    heating_reference_temperature_in_celsius: float
    building_code: str
    building_heat_capacity_class: str
    initial_internal_temperature_in_celsius: float

    @classmethod
    def get_default_german_single_family_home(
        cls,
    ) -> Any:
        """Gets a default Building."""
        config = BuildingConfig(
            name="Building_1",
            building_code="DE.N.SFH.05.Gen.ReEx.001.002",
            building_heat_capacity_class="medium",
            initial_internal_temperature_in_celsius=23,
            heating_reference_temperature_in_celsius=-14,
        )
        return config


@dataclass_json
@dataclass
class BuildingControllerConfig:

    """Configuration of the Building Controller class."""

    minimal_building_temperature_in_celsius: float
    stop_heating_building_temperature_in_celsius: float


class Building(dynamic_component.DynamicComponent):

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
    # either thermal energy delivered from heat pump
    ThermalEnergyDelivered = "ThermalEnergyDelivered"
    # or mass input and temperature input delivered from Thermal Energy Storage (TES)
    MassInput = "MassInput"
    TemperatureInput = "TemperatureInput"

    # Inputs -> occupancy
    HeatingByResidents = "HeatingByResidents"

    # Inputs -> weather
    Altitude = "Altitude"
    Azimuth = "Azimuth"
    ApparentZenith = "ApparentZenith"
    DirectNormalIrradiance = "DirectNormalIrradiance"
    DirectNormalIrradianceExtra = "DirectNormalIrradianceExtra"
    DiffuseHorizontalIrradiance = "DiffuseHorizontalIrradiance"
    GlobalHorizontalIrradiance = "GlobalHorizontalIrradiance"
    TemperatureOutside = "TemperatureOutside"

    # Outputs
    TemperatureMean = "Residence Temperature"
    TemperatureAir = "TemperatureAir"
    TotalEnergyToResidence = "TotalEnergyToResidence"
    SolarGainThroughWindows = "SolarGainThroughWindows"
    StoredEnergyVariation = "StoredEnergyVariation"
    InternalLoss = "InternalLoss"
    OldStoredEnergy = "OldStoredEnergy"
    CurrentStoredEnergy = "CurrentStoredEnergy"
    MassOutput = "MassOutput"
    TemperatureOutput = "TemperatureOutput"
    ReferenceMaxHeatBuildingDemand = "ReferenceMaxHeatBuildingDemand"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: BuildingConfig,
    ):
        """Constructs all the neccessary attributes."""
        self.buildingconfig = config
        # dynamic
        self.my_component_inputs: List[dynamic_component.DynamicConnectionInput] = []
        self.my_component_outputs: List[dynamic_component.DynamicConnectionOutput] = []
        super().__init__(
            my_component_inputs=self.my_component_inputs,
            my_component_outputs=self.my_component_outputs,
            name=self.buildingconfig.name,
            my_simulation_parameters=my_simulation_parameters,
        )

        # =================================================================================================================================
        # Initialization of variables

        (self.is_in_cache, self.cache_file_path,) = utils.get_cache_file(
            self.component_name,
            self.buildingconfig,
            self.my_simulation_parameters,
        )
        # labeled as C_m in the paper [1] (** Check header), before c_m
        self.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin: float = 0
        self.thermal_capacity_of_building_thermal_mass_reference_in_joule_per_kelvin: float = 0
        # labeled as H_w in the paper [2] (*** Check header), before h_tr_w
        self.transmission_heat_transfer_coefficient_for_windows_and_door_in_watt_per_kelvin: float
        self.transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin: float
        # labeled as H_tr_em in paper [2] (*** Check header)
        self.external_part_of_transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin: float = 0
        # labeled as H_tr_ms in paper [2] (*** Check header)
        self.internal_part_of_transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin: float = 0
        # labeled as h_ms in paper [2] (*** Check header)
        self.heat_transfer_coefficient_between_thermal_mass_and_internal_surface_with_fixed_value_in_watt_per_m2_per_kelvin: float = 0
        # labeled as H_tr_is in paper [2] (** Check header)
        self.heat_transfer_coefficient_between_indoor_air_and_internal_surface_in_watt_per_kelvin: float = 0
        # labeled as h_is in paper [2] (** Check header)
        self.heat_transfer_coefficient_between_indoor_air_and_internal_surface_with_fixed_value_in_watt_per_m2_per_kelvin: float = 0
        # labeled as H_ve in paper [2] (*** Check header), before h_ve_adj
        self.thermal_conductance_by_ventilation_in_watt_per_kelvin: float = 0
        self.thermal_conductance_by_ventilation_reference_in_watt_per_kelvin: float = 0
        # before labeled as a_f
        self.conditioned_floor_area_in_m2: float = 0
        # before labeled as a_m
        self.effective_mass_area_in_m2: float = 0
        # before labeled as a_t
        self.total_internal_surface_area_in_m2: float = 0
        self.room_volume_in_m3: float = 0
        # reference taken from TABULA (* Check header) as Q_ht [kWh/m2.a], before q_ht_ref
        self.total_heat_transfer_reference_in_kilowatthour_per_m2_per_year: float = 0
        # reference taken from TABULA (* Check header) as Q_int [kWh/m2.a], before q_int_ref
        self.internal_heat_sources_reference_in_kilowatthour_per_m2_per_year: float = 0
        # reference taken from TABULA (* Check header) Q_sol [kWh/m2.a], before q_sol_ref (or solar heat sources?)
        self.solar_heat_load_during_heating_seasons_reference_in_kilowatthour_per_m2_per_year: float = 0
        # reference taken from TABULA (* Check header) as Q_H_ind [kWh/m2.a], before q_h_nd_ref
        self.energy_need_for_heating_reference_in_kilowatthour_per_m2_per_year: float = 0
        self.test_new_temperature_in_celsius: float
        self.buildingdata: Any
        self.buildingcode: str
        self.windows: List[Window]
        self.windows_area: float
        self.cache: List[float]
        self.solar_heat_gain_through_windows: List[float]
        # labeled as Phi_ia in paper [1] (** Check header)
        self.heat_flux_indoor_air_in_watt: float
        # labeled as Phi_st in the paper [1] (** Check header)
        self.heat_flux_internal_room_surface_in_watt: float
        # labeled as Phi_m in the paper [1] (** Check header)
        self.heat_flux_thermal_mass_in_watt: float
        self.heat_loss_in_watt: float
        # labeled as Phi_m_tot in the paper [1] (** Check header)
        self.equivalent_heat_flux_in_watt: float
        self.next_thermal_mass_temperature_in_celsius: float

        self.get_building()
        self.max_thermal_building_demand_in_watt = self.calc_max_thermal_building_demand(
            heating_reference_temperature_in_celsius=config.heating_reference_temperature_in_celsius,
            initial_temperature_in_celsius=config.initial_internal_temperature_in_celsius,
        )
        self.build()

        self.state: BuildingState = BuildingState(
            thermal_mass_temperature_in_celsius=config.initial_internal_temperature_in_celsius,
            thermal_capacitance_in_joule_per_kelvin=self.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin,
        )
        self.previous_state = self.state.self_copy()

        # =================================================================================================================================
        # Input and Output channels

        self.thermal_power_delivered_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ThermalEnergyDelivered,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            False,
        )
        self.mass_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.MassInput,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            False,
        )
        self.temperature_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureInput,
            lt.LoadTypes.WARM_WATER,
            lt.Units.CELSIUS,
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

        self.thermal_mass_temperature_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.TemperatureMean,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
        )
        self.total_power_to_residence_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.TotalEnergyToResidence,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
        )
        self.solar_gain_through_windows_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.SolarGainThroughWindows,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
        )
        self.var_max_thermal_building_demand_channel: cp.ComponentOutput = (
            self.add_output(
                self.component_name,
                self.ReferenceMaxHeatBuildingDemand,
                lt.LoadTypes.HEATING,
                lt.Units.WATT,
            )
        )

        # =================================================================================================================================
        # Add and get default connections

        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(self.get_default_connections_from_occupancy())
        self.add_default_connections(self.get_default_connections_from_utsp())

    def get_default_connections_from_weather(
        self,
    ):
        """Get weather default connnections."""
        log.information("setting weather default connections")
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

    def get_default_connections_from_occupancy(
        self,
    ):
        """Get occupancy default connections."""
        log.information("setting occupancy default connections")
        connections = []
        occupancy_classname = Occupancy.get_classname()
        connections.append(
            cp.ComponentConnection(
                Building.HeatingByResidents,
                occupancy_classname,
                Occupancy.HeatingByResidents,
            )
        )
        return connections

    def get_default_connections_from_utsp(
        self,
    ):
        """Gets UTSP default connections."""
        log.information("setting utsp default connections")
        connections = []
        utsp_classname = UtspLpgConnector.get_classname()
        connections.append(
            cp.ComponentConnection(
                Building.HeatingByResidents,
                utsp_classname,
                UtspLpgConnector.HeatingByResidents,
            )
        )
        return connections

    # =================================================================================================================================
    # Simulation of the building class

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulates the thermal behaviour of the building."""

        # Gets inputs
        if hasattr(self, "solar_gain_through_windows") is False:
            # altitude = stsv.get_input_value(self.altitude_channel)
            azimuth = stsv.get_input_value(self.azimuth_channel)
            direct_normal_irradiance = stsv.get_input_value(self.direct_normal_irradiance_channel)
            direct_horizontal_irradiance = stsv.get_input_value(self.direct_horizontal_irradiance_channel)
            global_horizontal_irradiance = stsv.get_input_value(self.global_horizontal_irradiance_channel)
            direct_normal_irradiance_extra = stsv.get_input_value(self.direct_normal_irradiance_extra_channel)
            apparent_zenith = stsv.get_input_value(self.apparent_zenith_channel)

        occupancy_heat_gain_in_watt = stsv.get_input_value(self.occupancy_heat_gain_channel)
        temperature_outside_in_celsius = stsv.get_input_value(self.temperature_outside_channel)
        thermal_power_delivered_in_watt = stsv.get_input_value(self.thermal_power_delivered_channel)

        # # With Thermal Energy Storage (TES) [In Development]
        # if self.mass_input_channel.source_output is not None:
        #     if force_convergence:
        #         return

        #     thermal_power_delivered_in_watt = stsv.get_input_value(self.thermal_power_delivered_channel)
        #     mass_input_in_kilogram_per_second = stsv.get_input_value(self.mass_input_channel)

        #     temperature_input_in_celsius = stsv.get_input_value(self.temperature_input_channel)

        #     if thermal_power_delivered_in_watt > 0 and (
        #         mass_input_in_kilogram_per_second == 0
        #         and temperature_input_in_celsius == 0
        #     ):
        #         """first iteration --> random numbers"""
        #         temperature_input_in_celsius = 40.456
        #         mass_input_in_kilogram_per_second = 0.0123

        #     if thermal_power_delivered_in_watt > 0:

        #         massflows_possible_in_kilogram_per_second = (
        #             LoadConfig.possible_massflows_load
        #         )
        #         mass_flow_level = 0
        #         # K = W / (J/kgK * kg/s); delta T in Kelvin = delta T in Celsius; heat capacity J/kgK = J/kg°C
        #         temperature_delta_heat_in_kelvin = thermal_power_delivered_in_watt / (
        #             PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
        #             * massflows_possible_in_kilogram_per_second[mass_flow_level]
        #         )
        #         while temperature_delta_heat_in_kelvin > LoadConfig.delta_T:
        #             mass_flow_level += 1
        #             temperature_delta_heat_in_kelvin = thermal_power_delivered_in_watt / (
        #                 PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
        #                 * massflows_possible_in_kilogram_per_second[mass_flow_level]
        #             )

        #         mass_input_load_in_kilogram_per_timestep = (
        #             massflows_possible_in_kilogram_per_second[mass_flow_level]
        #             * self.seconds_per_timestep
        #         )

        #         energy_demand_in_joule_per_timestep = (
        #             thermal_power_delivered_in_watt * self.seconds_per_timestep
        #         )
        #         enthalpy_slice_in_joule_per_timestep = (
        #             mass_input_load_in_kilogram_per_timestep
        #             * temperature_input_in_celsius
        #             * PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
        #         )
        #         enthalpy_new_in_joule_per_timestep = (
        #             enthalpy_slice_in_joule_per_timestep
        #             - energy_demand_in_joule_per_timestep
        #         )
        #         temperature_new_in_celsius = enthalpy_new_in_joule_per_timestep / (
        #             mass_input_load_in_kilogram_per_timestep
        #             * PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
        #         )

        #     else:
        #         # no water is flowing
        #         temperature_new_in_celsius = temperature_input_in_celsius
        #         mass_input_load_in_kilogram_per_timestep = 0

        #     self.test_new_temperature_in_celsius = temperature_new_in_celsius

        # # Only with HeatPump
        # elif self.thermal_power_delivered_channel.source_output is not None:
        #     thermal_power_delivered_in_watt = stsv.get_input_value(self.thermal_power_delivered_channel)
        # else:
        #     thermal_power_delivered_in_watt = sum(
        #         self.get_dynamic_inputs(
        #             stsv=stsv, tags=[lt.InandOutputType.HEAT_TO_BUILDING]
        #         )
        #     )

        previous_thermal_mass_temperature_in_celsius = (
            self.state.thermal_mass_temperature_in_celsius
        )

        # Performs calculations
        if hasattr(self, "solar_gain_through_windows") is False:
            # @JG I guess you wanted to transfer W to Wh
            solar_heat_gain_through_windows = self.get_solar_heat_gain_through_windows(
                # altitude=altitude,
                azimuth=azimuth,
                direct_normal_irradiance=direct_normal_irradiance,
                direct_horizontal_irradiance=direct_horizontal_irradiance,
                global_horizontal_irradiance=global_horizontal_irradiance,
                direct_normal_irradiance_extra=direct_normal_irradiance_extra,
                apparent_zenith=apparent_zenith,
            )
        else:
            solar_heat_gain_through_windows = self.solar_heat_gain_through_windows[
                timestep
            ]

        (
            thermal_mass_average_bulk_temperature_in_celsius,
            heat_loss_in_watt,
        ) = self.calc_crank_nicolson(
            thermal_power_delivered_in_watt=thermal_power_delivered_in_watt,
            internal_heat_gains_in_watt=occupancy_heat_gain_in_watt,
            solar_heat_gains_in_watt=solar_heat_gain_through_windows,
            outside_temperature_in_celsius=temperature_outside_in_celsius,
            thermal_mass_temperature_prev_in_celsius=previous_thermal_mass_temperature_in_celsius,
        )
        self.state.thermal_mass_temperature_in_celsius = thermal_mass_average_bulk_temperature_in_celsius

        # Returns outputs

        stsv.set_output_value(self.thermal_mass_temperature_channel, thermal_mass_average_bulk_temperature_in_celsius)
        # stsv.set_output_value(self.t_airC, t_air)
        # phi_loss is already given in W, time correction factor applied to thermal transmittance h_tr
        stsv.set_output_value(self.total_power_to_residence_channel, heat_loss_in_watt)
        stsv.set_output_value(self.solar_gain_through_windows_channel, solar_heat_gain_through_windows)
        stsv.set_output_value(self.var_max_thermal_building_demand_channel, self.max_thermal_building_demand_in_watt)

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
        """Saves the current state."""
        self.previous_state = self.state.self_copy()

    def i_prepare_simulation(
        self,
    ) -> None:
        """Prepares the simulation."""
        pass

    def i_restore_state(
        self,
    ) -> None:
        """Restores the previous state."""
        self.state = self.previous_state.self_copy()

    def i_doublecheck(
        self,
        timestep: int,
        stsv: cp.SingleTimeStepValues,
    ) -> None:
        """Doublechecks."""
        pass

    def build(
        self,
    ):
        """Build function.

        The function sets important constants and parameters for the calculations.
        It imports the building dataset from TABULA and gets physical parameters and thermal conductances.
        """

        self.seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
        self.timesteps = self.my_simulation_parameters.timesteps
        self.parameters = [
            self.building_heat_capacity_class,
            self.buildingcode,
        ]

        # CONSTANTS
        # Heat transfer coefficient between nodes "m" and "s" (12.2.2 E64 P79); labeled as h_ms in paper [2] (*** Check header)
        self.heat_transfer_coefficient_between_thermal_mass_and_internal_surface_with_fixed_value_in_watt_per_m2_per_kelvin = 9.1
        # Dimensionless ratio between surfaces and the useful surfaces (7.2.2.2 E9 P36); labeled as A_at in paper [2] (*** Check header); before lambda_at
        self.ratio_between_internal_surface_area_and_floor_area = 4.5
        # Heat transfer coefficient between nodes "air" and "s" (7.2.2.2 E9 P35); labeled as h_is in paper [2] (*** Check header)
        self.heat_transfer_coefficient_between_indoor_air_and_internal_surface_with_fixed_value_in_watt_per_m2_per_kelvin = 3.45

        self.building_heat_capacity_class_f_a = {
            "very light": 2.5,
            "light": 2.5,
            "medium": 2.5,
            "heavy": 3.0,
            "very heavy": 3.5,
        }
        self.building_heat_capacity_class_f_c = {
            "very light": 8e4,
            "light": 1.1e5,
            "medium": 1.65e5,
            "heavy": 2.6e5,
            "very heavy": 3.7e5,
        }

        self.ven_method = "EPISCOPE"

        # Get physical parameters
        self.get_physical_param()

        # Gets conductances
        self.get_conductances()

    def calc_max_thermal_building_demand(
        self,
        initial_temperature_in_celsius: float,
        heating_reference_temperature_in_celsius: float,
    ) -> Any:
        """Calculate maximal thermal building demand using TABULA data."""

        vals1_in_watt_per_m2_per_kelvin = float(self.buildingdata["h_Transmission"].values[0])

        if vals1_in_watt_per_m2_per_kelvin is None:
            raise ValueError("h_Transmission was none.")
        vals2_in_watt_per_m2_per_kelvin = float(self.buildingdata["h_Ventilation"].values[0])
        conditioned_floor_area_in_m2 = float(self.buildingdata["A_C_Ref"].values[0])

        # dQ/dt = h * (T2-T1) * A -> [W]
        max_thermal_building_demand_in_watt = (
            (vals1_in_watt_per_m2_per_kelvin + vals2_in_watt_per_m2_per_kelvin)
            * (
                initial_temperature_in_celsius
                - heating_reference_temperature_in_celsius
            )
            * conditioned_floor_area_in_m2
        )
        return max_thermal_building_demand_in_watt

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
        """Writes important variables to a report."""
        lines = []
        lines.append(f"Name: {self.component_name}")
        lines.append(f"Code: {self.buildingcode}")

        lines.append("")
        lines.append("Conductances:")
        lines.append(
            f"H_tr_w [W/K]: {self.transmission_heat_transfer_coefficient_for_windows_and_door_in_watt_per_kelvin:4.2f}"
        )
        lines.append(
            f"H_tr_em [W/K]: {self.external_part_of_transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin:4.2f}"
        )
        lines.append(
            f"H_tr_is [W/K]: {self.heat_transfer_coefficient_between_indoor_air_and_internal_surface_in_watt_per_kelvin:4.2f}"
        )
        lines.append(
            f"H_tr_ms [W/K]: {self.internal_part_of_transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin:4.2f}"
        )
        lines.append(
            f"H_ve_adj [W/K]: {self.thermal_conductance_by_ventilation_in_watt_per_kelvin:4.2f}"
        )
        lines.append(
            f"H_Ventilation [kWh/a]: {self.thermal_conductance_by_ventilation_reference_in_watt_per_kelvin}"
        )

        lines.append(" ")
        lines.append("Areas:")
        lines.append(f"A_f [m^2]: {self.conditioned_floor_area_in_m2:4.1f}")
        lines.append(f"A_m [m^2]: {self.effective_mass_area_in_m2:4.1f}")
        lines.append(f"A_t [m^2]: {self.total_internal_surface_area_in_m2:4.1f}")
        lines.append(f"Room volume [m^3]: {self.room_volume_in_m3}")

        lines.append(" ")
        lines.append("Capacitance:")
        lines.append(
            f"Capacitance [Wh/m^2.K]: {(self.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin * 3600 / self.conditioned_floor_area_in_m2):4.2f}"
        )
        lines.append(
            f"Capacitance [Wh/K]: {(self.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin * 3600):4.2f}"
        )
        lines.append(
            f"Capacitance [J/K]: {self.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin:4.2f}"
        )

        lines.append(
            f"Capacitance Ref [Wh/m^2.K]: {self.thermal_capacity_of_building_thermal_mass_reference_in_joule_per_kelvin:4.2f}"
        )
        lines.append(
            f"Capacitance Ref [Wh/K]: {(self.thermal_capacity_of_building_thermal_mass_reference_in_joule_per_kelvin * self.conditioned_floor_area_in_m2):4.2f}"
        )
        lines.append(
            f"Capacitance Ref [J/K]: {(self.thermal_capacity_of_building_thermal_mass_reference_in_joule_per_kelvin * self.conditioned_floor_area_in_m2 / 3600):4.2f}"
        )

        lines.append(" ")
        lines.append("Heat Transfers:")
        lines.append(
            f"Annual heating losses Q_ht [kWh/m^2.a]: {self.total_heat_transfer_reference_in_kilowatthour_per_m2_per_year}"
        )
        lines.append(
            f"Annual heating losses Q_ht [kWh/a]: {self.total_heat_transfer_reference_in_kilowatthour_per_m2_per_year * self.conditioned_floor_area_in_m2}"
        )
        lines.append(
            f"Q_int [kWh/m^2.a]: {self.internal_heat_sources_reference_in_kilowatthour_per_m2_per_year}"
        )
        lines.append(
            f"Q_int [kWh/a]: {self.internal_heat_sources_reference_in_kilowatthour_per_m2_per_year * self.conditioned_floor_area_in_m2}"
        )
        lines.append(
            f"Q_sol [kWh/m^2.a]: {self.solar_heat_load_during_heating_seasons_reference_in_kilowatthour_per_m2_per_year}"
        )
        lines.append(
            f"Q_sol [kWh/a]: {self.solar_heat_load_during_heating_seasons_reference_in_kilowatthour_per_m2_per_year * self.conditioned_floor_area_in_m2}"
        )
        lines.append("=============== REFERENCE ===============")
        lines.append(
            f"Balance Heating Demand Reference [kWh/m^2.a]: {self.energy_need_for_heating_reference_in_kilowatthour_per_m2_per_year}"
        )
        lines.append(
            f"Balance Heating Demand Reference [kWh/a]: {self.energy_need_for_heating_reference_in_kilowatthour_per_m2_per_year * self.conditioned_floor_area_in_m2}"
        )
        return lines

    # =====================================================================================================================================
    # Calculation of the heat transfer coefficients or thermal conductances.
    # (**/*** Check header)

    @property
    def transmission_heat_transfer_coeffcient_1_in_watt_per_kelvin(
        self,
    ):
        """Definition to simplify calc_phi_m_tot. Long form for H_tr_1.

        # (C.6) in [C.3 ISO 13790]
        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        return 1.0 / (
            1.0 / self.thermal_conductance_by_ventilation_in_watt_per_kelvin
            + 1.0
            / self.heat_transfer_coefficient_between_indoor_air_and_internal_surface_in_watt_per_kelvin
        )

    @property
    def transmission_heat_transfer_coefficient_2_in_watt_per_kelvin(
        self,
    ):
        """Definition to simplify calc_phi_m_tot. Long form for H_tr_2.

        # (C.7) in [C.3 ISO 13790]
        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        return (
            self.transmission_heat_transfer_coeffcient_1_in_watt_per_kelvin
            + self.transmission_heat_transfer_coefficient_for_windows_and_door_in_watt_per_kelvin
        )

    @property
    def transmission_heat_transfer_coefficient_3_in_watt_per_kelvin(
        self,
    ):
        """Definition to simplify calc_phi_m_tot. Long form for H_tr_3.

        # (C.8) in [C.3 ISO 13790]
        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        return 1.0 / (
            1.0 / self.transmission_heat_transfer_coefficient_2_in_watt_per_kelvin
            + 1.0
            / self.internal_part_of_transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin
        )

    def get_thermal_conductance_between_exterior_and_windows_and_door_in_watt_per_kelvin(
        self,
    ):
        """Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)."""
        # Long from for H_tr_w: Conductance between exterior temperature and surface temperature
        # Objects: Doors, windows, curtain walls and windowed walls ISO 7.2.2.2
        w_s = [
            "Window_1",
            "Window_2",
            "Door_1",
        ]

        self.transmission_heat_transfer_coefficient_for_windows_and_door_in_watt_per_kelvin = 0.0
        for w_i in w_s:
            self.transmission_heat_transfer_coefficient_for_windows_and_door_in_watt_per_kelvin += float(
                self.buildingdata["H_Transmission_" + w_i].values[0]
            )

    def get_thermal_conductance_between_thermal_mass_and_internal_surface_in_watt_per_kelvin(
        self,
    ):
        """Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)."""
        # Long from for H_tr_ms, this is the same as internal pasrt of transmission heat transfer coefficient for opaque elements
        self.internal_part_of_transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin = (
            self.effective_mass_area_in_m2
            * self.heat_transfer_coefficient_between_thermal_mass_and_internal_surface_with_fixed_value_in_watt_per_m2_per_kelvin
        )

    def get_thermal_conductance_of_opaque_surfaces_in_watt_per_kelvin(
        self,
    ):
        """Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)."""
        # Long from for H_tr_op: H_tr_op = 1/ (1/H_tr_ms + 1/H_tr_em) with
        # H_tr_ms: Conductance of opaque surfaces to interior [W/K] and H_tr_em: Conductance of opaque surfaces to exterior [W/K]
        opaque_walls = [
            "Wall_1",
            "Wall_2",
            "Wall_3",
            "Roof_1",
            "Roof_2",
            "Floor_1",
            "Floor_2",
        ]

        # Version 1
        # self.h_tr_em = 0.0
        # for ow in opaque_walls:
        #    self.h_tr_em += float(self.buildingdata["H_Transmission_" + ow].values[0])

        # Version 2
        self.transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin = 0.0
        for o_w in opaque_walls:
            self.transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin += float(
                self.buildingdata["H_Transmission_" + o_w].values[0]
            )

        self.external_part_of_transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin = 1 / (
            (
                1
                / self.transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin
            )
            - (
                1
                / self.internal_part_of_transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin
            )
        )

    def get_thermal_conductance_between_indoor_air_and_internal_surface_in_watt_per_kelvin(
        self,
    ):
        """Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)."""
        # Long from for H_tr_is: Conductance between air temperature and surface temperature
        self.heat_transfer_coefficient_between_indoor_air_and_internal_surface_in_watt_per_kelvin = (
            self.total_internal_surface_area_in_m2
            * self.heat_transfer_coefficient_between_indoor_air_and_internal_surface_with_fixed_value_in_watt_per_m2_per_kelvin
        )

    def get_thermal_conductance_ventilation_in_watt_per_kelvin(
        self,
    ):
        """Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)."""
        # Long from for H_ve_adj: Ventilation
        if self.ven_method == "RC_BuildingSimulator":
            """Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)."""
            air_changes_per_hour_through_ventilation = 1.5
            air_changes_per_hour_through_infiltration = 0.5
            ventilation_efficiency = 0.6
            # Determine the ventilation conductance
            total_air_changes_per_hour = (
                air_changes_per_hour_through_infiltration
                + air_changes_per_hour_through_ventilation
            )
            # temperature adjustment factor taking ventilation and infiltration
            # [ISO: E -27]
            b_ek = (
                1
                - (
                    air_changes_per_hour_through_ventilation
                    / total_air_changes_per_hour
                )
                * ventilation_efficiency
            )
            self.thermal_conductance_by_ventilation_in_watt_per_kelvin = float(
                1200
                * b_ek
                * self.room_volume_in_m3
                * (total_air_changes_per_hour / 3600)
            )  # Conductance through ventilation [W/M]
        elif self.ven_method == "EPISCOPE":
            # cp = 0.00028378 * 1E3  # [Wh/m3K]
            c_p = 0.34
            self.thermal_conductance_by_ventilation_in_watt_per_kelvin = (
                c_p
                * float(
                    self.buildingdata["n_air_use"]
                    + self.buildingdata["n_air_infiltration"]
                )
                * self.conditioned_floor_area_in_m2
                * float(self.buildingdata["h_room"])
            )

    def get_conductances(
        self,
    ):
        """Get the thermal conductances based on the norm EN ISO 13970.

        :key
        """
        self.get_thermal_conductance_between_exterior_and_windows_and_door_in_watt_per_kelvin()
        self.get_thermal_conductance_between_thermal_mass_and_internal_surface_in_watt_per_kelvin()
        self.get_thermal_conductance_of_opaque_surfaces_in_watt_per_kelvin()
        self.get_thermal_conductance_between_indoor_air_and_internal_surface_in_watt_per_kelvin()
        self.get_thermal_conductance_ventilation_in_watt_per_kelvin()

    # =====================================================================================================================================
    # Get building parameters.
    # (* Check header)

    def get_physical_param(
        self,
    ):
        """Get the physical parameters from the building data."""
        # Windows area
        self.get_windows()

        # Reference area [m^2] (TABULA: Reference floor area )Ref: ISO standard 7.2.2.2
        self.conditioned_floor_area_in_m2 = float(
            self.buildingdata["A_C_Ref"].values[0]
        )
        # total_internal_area = buildingdata["A_Estim_Floor"][1]

        self.effective_mass_area_in_m2 = (
            self.conditioned_floor_area_in_m2
            * self.building_heat_capacity_class_f_a[self.building_heat_capacity_class]
        )
        self.total_internal_surface_area_in_m2 = (
            self.conditioned_floor_area_in_m2
            * self.ratio_between_internal_surface_area_and_floor_area
        )

        # Room Capacitance [J/K] (TABULA: Internal heat capacity) Ref: ISO standard 12.3.1.2
        self.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin = (
            self.building_heat_capacity_class_f_c[self.building_heat_capacity_class]
            * self.conditioned_floor_area_in_m2
        )

        # Building volume (TABULA: Conditioned building volume)
        self.room_volume_in_m3 = float(self.buildingdata["V_C"].values[0])

        # Reference properties from TABULA, but not used in the model
        # Floor area related heat load during heating season
        self.solar_heat_load_during_heating_seasons_reference_in_kilowatthour_per_m2_per_year = float(
            (self.buildingdata["q_sol"].values[0])
        )
        # Floor area related internal heat sources during heating season
        self.internal_heat_sources_reference_in_kilowatthour_per_m2_per_year = float(
            self.buildingdata["q_int"].values[0]
        )
        # Floor area related annual losses
        self.total_heat_transfer_reference_in_kilowatthour_per_m2_per_year = float(
            self.buildingdata["q_ht"].values[0]
        )
        # Energy need for heating
        self.energy_need_for_heating_reference_in_kilowatthour_per_m2_per_year = float(
            self.buildingdata["q_h_nd"].values[0]
        )
        # Internal heat capacity per m2 reference area [Wh/(m^2.K)] (TABULA: Internal heat capacity)
        self.thermal_capacity_of_building_thermal_mass_reference_in_joule_per_kelvin = (
            float(self.buildingdata["c_m"].values[0])
        )

        # Heat transfer coefficient by ventilation
        self.thermal_conductance_by_ventilation_reference_in_watt_per_kelvin = (
            float(self.buildingdata["h_Ventilation"].values[0])
            * self.conditioned_floor_area_in_m2
        )

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

    def get_windows(
        self,
    ):
        """Retrieves data about windows sizes.

        :return:
        """

        self.windows = []
        self.windows_area = 0.0
        south_angle = 180

        windows_angles = {
            "South": south_angle,
            "East": south_angle - 90,
            "North": south_angle - 180,
            "West": south_angle + 90,
        }

        windows_directions = [
            "South",
            "East",
            "North",
            "West",
        ]
        f_w = self.buildingdata["F_w"].values[0]
        f_f = self.buildingdata["F_f"].values[0]
        f_sh_vertical = self.buildingdata["F_sh_vert"].values[0]
        g_gln = self.buildingdata["g_gl_n"].values[0]
        for windows_direction in windows_directions:
            area = float(self.buildingdata["A_Window_" + windows_direction])
            if area != 0.0:
                self.windows.append(
                    Window(
                        azimuth_tilt=windows_angles[windows_direction],
                        area=area,
                        frame_area_fraction_reduction_factor=f_f,
                        glass_solar_transmittance=g_gln,
                        nonperpendicular_reduction_factor=f_w,
                        external_shading_vertical_reduction_factor=f_sh_vertical,
                    )
                )
                self.windows_area += area
        # if nothing exists, initialize the empty arrays for caching, else read stuff
        if (
            not self.is_in_cache
        ):  # cache_filepath is None or  (not os.path.isfile(cache_filepath)):
            self.cache = [0] * self.my_simulation_parameters.timesteps
        else:
            self.solar_heat_gain_through_windows = pd.read_csv(
                self.cache_file_path,
                sep=",",
                decimal=".",
            )["solar_gain_through_windows"].tolist()

    # =====================================================================================================================================
    # Calculate solar heat gain through windows.
    # (** Check header)

    # @cached(cache=LRUCache(maxsize=16))
    def get_solar_heat_gain_through_windows(
        self,
        azimuth,
        direct_normal_irradiance,
        direct_horizontal_irradiance,
        global_horizontal_irradiance,
        direct_normal_irradiance_extra,
        apparent_zenith,
    ):  # altitude,
        """Calculates the thermal solar gain passed to the building through the windows.

        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        solar_heat_gains = 0.0

        if (
            direct_normal_irradiance != 0
            or direct_horizontal_irradiance != 0
            or global_horizontal_irradiance != 0
        ):

            for window in self.windows:
                solar_heat_gain = calc_solar_heat_gains(
                    sun_azimuth=azimuth,
                    direct_normal_irradiance=direct_normal_irradiance,
                    direct_horizontal_irradiance=direct_horizontal_irradiance,
                    global_horizontal_irradiance=global_horizontal_irradiance,
                    direct_normal_irradiance_extra=direct_normal_irradiance_extra,
                    apparent_zenith=apparent_zenith,
                    altitude_tilt=window.altitude_tilt,
                    azimuth_tilt=window.azimuth_tilt,
                    reduction_factor_with_area=window.reduction_factor_with_area,
                )
                solar_heat_gains += solar_heat_gain
        return solar_heat_gains

    # =====================================================================================================================================
    # Calculation of the heat flows from internal and solar heat sources
    # (**/*** Check header)

    def calc_heat_flow(
        self,
        # this is labeled as Phi_int in paper [1] (** Check header)
        internal_heat_gains_in_watt,
        # this is labeled as Phi_sol in paper [1] (** Check header)
        solar_heat_gains_in_watt,
        # power_demand_in_watt,
    ):
        """Calculates the heat flow from the solar gains, heating/cooling system, and internal gains into the building.

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
        # self.heat_flux_indoor_air_in_watt = (
        #     0.5 * internal_heat_gains_in_watt + power_demand_in_watt
        # )
        self.heat_flux_indoor_air_in_watt = 0.5 * internal_heat_gains_in_watt
        # Heat flow to the surface node in W, before labeled Phi_st
        self.heat_flux_internal_room_surface_in_watt = (
            1
            - (self.effective_mass_area_in_m2 / self.total_internal_surface_area_in_m2)
            - (
                self.transmission_heat_transfer_coefficient_for_windows_and_door_in_watt_per_kelvin
                / (9.1 * self.total_internal_surface_area_in_m2)
            )
        ) * (0.5 * internal_heat_gains_in_watt + solar_heat_gains_in_watt)

        # Heat flow to the thermal mass node in W, before labeled Phi_m
        self.heat_flux_thermal_mass_in_watt = (
            self.effective_mass_area_in_m2 / self.total_internal_surface_area_in_m2
        ) * (0.5 * internal_heat_gains_in_watt + solar_heat_gains_in_watt)

        # Heat loss in W, before labeled Phi_loss
        self.heat_loss_in_watt = (
            self.transmission_heat_transfer_coefficient_for_windows_and_door_in_watt_per_kelvin
            / (9.1 * self.total_internal_surface_area_in_m2)
        ) * (0.5 * internal_heat_gains_in_watt + solar_heat_gains_in_watt)
        return self.heat_loss_in_watt

    # =====================================================================================================================================
    # Determination of different temperatures T_air, T_s, T_m,t and T_m and global heat transfer Phi_m_tot which are used in crank-nicolson method
    # (**/*** Check header)

    def calc_next_thermal_mass_temperature_in_celsius(
        self,
        previous_thermal_mass_temperature_in_celsius,
    ):
        """Primary Equation, calculates the temperature of the next time step: T_m,t.

        # (C.4) in [C.3 ISO 13790]
        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        self.next_thermal_mass_temperature_in_celsius = (
            (
                previous_thermal_mass_temperature_in_celsius
                * (
                    (
                        self.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin
                        / self.seconds_per_timestep
                    )
                    - 0.5
                    * (
                        self.transmission_heat_transfer_coefficient_3_in_watt_per_kelvin
                        + self.external_part_of_transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin
                    )
                )
            )
            + self.equivalent_heat_flux_in_watt
        ) / (
            (
                self.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin
                / self.seconds_per_timestep
            )
            + 0.5
            * (
                self.transmission_heat_transfer_coefficient_3_in_watt_per_kelvin
                + self.external_part_of_transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin
            )
        )

    def calc_equivalent_heat_flux_in_watt(self, temperature_outside_in_celsius, thermal_power_delivered_in_watt):
        """Calculates a global heat transfer: Phi_m_tot.

        This is a definition used to simplify equation calc_t_m_next so it's not so long to write out
        # (C.5) in [C.3 ISO 13790]
        # h_ve = h_ve_adj and t_supply = t_out [9.3.2 ISO 13790]
        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        # ASSUMPTION: Supply air comes straight from the outside air
        # here Phi_HC,nd is not heating or cooling demand but thermal power delivered
        t_supply = temperature_outside_in_celsius

        self.equivalent_heat_flux_in_watt = (
            self.heat_flux_thermal_mass_in_watt
            + self.external_part_of_transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin
            * temperature_outside_in_celsius
            + self.transmission_heat_transfer_coefficient_3_in_watt_per_kelvin
            * (
                self.heat_flux_internal_room_surface_in_watt
                + self.transmission_heat_transfer_coefficient_for_windows_and_door_in_watt_per_kelvin
                * temperature_outside_in_celsius
                + self.transmission_heat_transfer_coeffcient_1_in_watt_per_kelvin
                * (
                    (
                        (self.heat_flux_indoor_air_in_watt + thermal_power_delivered_in_watt)
                        / self.thermal_conductance_by_ventilation_in_watt_per_kelvin
                    )
                    + t_supply
                )
            )
            / self.transmission_heat_transfer_coefficient_2_in_watt_per_kelvin
        )

    def calc_thermal_mass_averag_bulk_temperature_in_celsius_used_for_calculations(
        self,
        previous_thermal_mass_temperature_in_celsius,
    ):
        """Temperature used for the calculations, average between newly calculated and previous bulk temperature: T_m.

        # (C.9) in [C.3 ISO 13790]
        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        return (
            previous_thermal_mass_temperature_in_celsius
            + self.next_thermal_mass_temperature_in_celsius
        ) / 2

    def calc_temperature_of_internal_room_surfaces_in_celsius(
        self,
        temperature_outside_in_celsius,
        thermal_mass_temperature_in_celsius,
        thermal_power_delivered_in_watt
    ):
        """Calculate the temperature of the inside room surfaces: T_s.

        # (C.10) in [C.3 ISO 13790]
        # h_ve = h_ve_adj and t_supply = t_out [9.3.2 ISO 13790]
        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        # ASSUMPTION: Supply air comes straight from the outside air
        # here Phi_HC,nd is not heating or cooling demand but thermal power delivered
        t_supply = temperature_outside_in_celsius

        return (
            self.heat_transfer_coefficient_between_thermal_mass_and_internal_surface_with_fixed_value_in_watt_per_m2_per_kelvin
            * thermal_mass_temperature_in_celsius
            + self.heat_flux_internal_room_surface_in_watt
            + self.transmission_heat_transfer_coefficient_for_windows_and_door_in_watt_per_kelvin
            * temperature_outside_in_celsius
            + self.transmission_heat_transfer_coeffcient_1_in_watt_per_kelvin
            * (
                t_supply
                + (self.heat_flux_indoor_air_in_watt + thermal_power_delivered_in_watt)
                / self.thermal_conductance_by_ventilation_in_watt_per_kelvin
            )
        ) / (
            self.heat_transfer_coefficient_between_thermal_mass_and_internal_surface_with_fixed_value_in_watt_per_m2_per_kelvin
            + self.transmission_heat_transfer_coefficient_for_windows_and_door_in_watt_per_kelvin
            + self.transmission_heat_transfer_coeffcient_1_in_watt_per_kelvin
        )

    def calc_temperature_of_the_inside_air_in_celsius(
        self,
        temperature_outside_in_celsius,
        temperature_internal_room_surfaces_in_celsius,
        thermal_power_delivered_in_watt
    ):
        """Calculate the temperature of the air node: T_air.

        # (C.11) in [C.3 ISO 13790]
        # h_ve = h_ve_adj and t_supply = t_out [9.3.2 ISO 13790]
        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        # ASSUMPTION: Supply air comes straight from the outside air
        # here Phi_HC,nd is not heating or cooling demand but thermal power delivered
        t_supply = temperature_outside_in_celsius

        return (
            self.heat_transfer_coefficient_between_indoor_air_and_internal_surface_with_fixed_value_in_watt_per_m2_per_kelvin
            * temperature_internal_room_surfaces_in_celsius
            + self.thermal_conductance_by_ventilation_in_watt_per_kelvin * t_supply
            + thermal_power_delivered_in_watt
            + self.heat_flux_indoor_air_in_watt
        ) / (
            self.heat_transfer_coefficient_between_indoor_air_and_internal_surface_with_fixed_value_in_watt_per_m2_per_kelvin
            + self.thermal_conductance_by_ventilation_in_watt_per_kelvin
        )

    def calc_crank_nicolson(
        self,
        internal_heat_gains_in_watt,
        solar_heat_gains_in_watt,
        outside_temperature_in_celsius,
        thermal_mass_temperature_prev_in_celsius,
        thermal_power_delivered_in_watt
    ):
        """Determines node temperatures and computes derivation to determine the new node temperatures.

        Used in: has_demand(), solve_energy(), calc_energy_demand()
        # section C.3 in [C.3 ISO 13790]
        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        Alternatively, described in paper [2].
        """

        # Updates flows
        heat_loss_in_watt = self.calc_heat_flow(
            internal_heat_gains_in_watt,
            solar_heat_gains_in_watt,
        )

        # Updates total flow
        self.calc_equivalent_heat_flux_in_watt(outside_temperature_in_celsius, thermal_power_delivered_in_watt)

        # calculates the new bulk temperature POINT from the old one # CHECKED Requires t_m_prev
        self.calc_next_thermal_mass_temperature_in_celsius(
            thermal_mass_temperature_prev_in_celsius
        )

        # calculates the AVERAGE bulk temperature used for the remaining
        thermal_mass_average_bulk_temperature_in_celsius = (
            self.calc_thermal_mass_averag_bulk_temperature_in_celsius_used_for_calculations(
                thermal_mass_temperature_prev_in_celsius
            )
        )

        # # Updates internal surface temperature (t_s)
        # internal_room_surface_temperature_in_celsius = (
        #     self.calc_temperature_of_internal_room_surfaces_in_celsius(
        #         outside_temperature_in_celsius,
        #         thermal_mass_average_bulk_temperature_in_celsius,
        #     )
        # )

        # # Updates indoor air temperature (t_air)
        # indoor_air_temperature_in_celsius = (
        #     self.calc_temperature_of_the_inside_air_in_celsius(
        #         outside_temperature_in_celsius,
        #         internal_room_surface_temperature_in_celsius,
        #     )
        # )

        return (
            thermal_mass_average_bulk_temperature_in_celsius,
            heat_loss_in_watt,
        )
        # return t_m, t_air, t_s, indoor_air_temperature_in_celsius,internal_room_surface_temperature_in_celsius,


class Window:

    """Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)."""

    def __init__(
        self,
        azimuth_tilt=None,
        altitude_tilt=90,
        area=None,
        glass_solar_transmittance=0.6,
        frame_area_fraction_reduction_factor=0.3,
        external_shading_vertical_reduction_factor=1.0,
        nonperpendicular_reduction_factor=0.9,
    ):
        """Constructs all the neccessary attributes."""
        # Angles
        self.altitude_tilt = altitude_tilt
        self.azimuth_tilt = azimuth_tilt
        self.altitude_tilt_rad = math.radians(altitude_tilt)
        self.azimuth_tilt_rad = math.radians(azimuth_tilt)

        # Area
        self.area = area

        # Transmittance
        self.glass_solar_transmittance = glass_solar_transmittance

        # Incident Solar Radiation
        self.incident_solar: int

        # Reduction factors
        self.nonperpendicular_reduction_factor = nonperpendicular_reduction_factor
        self.external_shading_vertical_reduction_factor = (
            external_shading_vertical_reduction_factor
        )
        self.frame_area_fraction_reduction_factor = frame_area_fraction_reduction_factor

        self.reduction_factor = (
            glass_solar_transmittance
            * nonperpendicular_reduction_factor
            * external_shading_vertical_reduction_factor
            * (1 - frame_area_fraction_reduction_factor)
        )

        self.reduction_factor_with_area = self.reduction_factor * area

    # @cached(cache=LRUCache(maxsize=5))
    # @lru_cache
    def calc_solar_gains(
        self,
        sun_azimuth,
        direct_normal_irradiance,
        direct_horizontal_irradiance,
        global_horizontal_irradiance,
        direct_normal_irradiance_extra,
        apparent_zenith,
    ):
        """Calculates the Solar Gains in the building zone through the set Window.

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
        albedo = 0.4
        # automatic pd time series in future pvlib version
        # calculate airmass
        airmass = pvlib.atmosphere.get_relative_airmass(apparent_zenith)
        # use perez model to calculate the plane of array diffuse sky radiation
        poa_sky_diffuse = pvlib.irradiance.perez(
            self.altitude_tilt,
            self.azimuth_tilt,
            direct_horizontal_irradiance,
            np.float64(direct_normal_irradiance),
            direct_normal_irradiance_extra,
            apparent_zenith,
            sun_azimuth,
            airmass,
        )
        # calculate ground diffuse with specified albedo
        poa_ground_diffuse = pvlib.irradiance.get_ground_diffuse(
            self.altitude_tilt,
            global_horizontal_irradiance,
            albedo=albedo,
        )
        # calculate angle of incidence
        aoi = pvlib.irradiance.aoi(
            self.altitude_tilt,
            self.azimuth_tilt,
            apparent_zenith,
            sun_azimuth,
        )
        # calculate plane of array irradiance
        poa_irrad = pvlib.irradiance.poa_components(
            aoi,
            np.float64(direct_normal_irradiance),
            poa_sky_diffuse,
            poa_ground_diffuse,
        )

        if math.isnan(poa_irrad["poa_direct"]):
            self.incident_solar = 0
        else:
            self.incident_solar = (poa_irrad["poa_direct"]) * self.area

        solar_gains = (
            self.incident_solar
            * self.glass_solar_transmittance
            * self.nonperpendicular_reduction_factor
            * self.external_shading_vertical_reduction_factor
            * (1 - self.frame_area_fraction_reduction_factor)
        )
        return solar_gains

    def calc_direct_solar_factor(
        self,
        sun_altitude,
        sun_azimuth,
        apparent_zenith,
    ):
        """Calculates the cosine of the angle of incidence on the window.

        Commented equations, that provide a direct calculation, were derived in:

        Proportion of the radiation incident on the window (cos of the incident ray)
        ref:Quaschning, Volker, and Rolf Hanitsch. "Shade calculations in photovoltaic systems."
        ISES Solar World Conference, Harare. 1995.

        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        sun_altitude_rad = math.radians(sun_altitude)

        aoi = pvlib.irradiance.aoi(
            self.altitude_tilt,
            self.azimuth_tilt,
            apparent_zenith,
            sun_azimuth,
        )

        direct_factor = math.cos(aoi) / (math.sin(sun_altitude_rad))

        return direct_factor

    def calc_diffuse_solar_factor(
        self,
    ):
        """Calculates the proportion of diffuse radiation.

        Based on the RC_BuildingSimulator project @[rc_buildingsimulator-jayathissa] (** Check header)
        """
        # Proportion of incident light on the window surface
        return (1 + math.cos(self.altitude_tilt_rad)) / 2


class BuildingController(cp.Component):

    """BuildingController class.

    It calculates on base of the maximal Building
    Thermal Demand and the difference of the actual Building Tempreature
    to the Target/Minimal Building Tempreature how much the building is suppose
    to be heated up. This Output is called "RealBuildingHeatDemand".

    Parameters
    ----------
    sim_params : Simulator
        Simulator object used to carry the simulation using this class

    """

    # Inputs
    ReferenceMaxHeatBuildingDemand = "ReferenceMaxHeatBuildingDemand"
    ResidenceTemperature = "ResidenceTemperature"
    # Outputs
    RealHeatBuildingDemand = "RealHeatBuildingDemand"
    LevelOfUtilization = "LevelOfUtilization"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: BuildingControllerConfig,
    ):
        """Constructs all the neccessary attributes of the Building Controller object."""
        super().__init__(
            name="BuildingController",
            my_simulation_parameters=my_simulation_parameters,
        )
        self.minimal_building_temperature_in_celsius = (
            config.minimal_building_temperature_in_celsius
        )
        self.stop_heating_building_temperature_in_celsius = (
            config.stop_heating_building_temperature_in_celsius
        )
        self.state = BuildingControllerState(
            temperature_building_target_in_celsius=config.minimal_building_temperature_in_celsius,
            level_of_utilization=0,
        )
        self.previous_state = self.state.clone()

        # =================================================================================================================================
        # Inputs and Output channels

        self.ref_max_thermal_build_demand_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ReferenceMaxHeatBuildingDemand,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            True,
        )
        self.residence_temperature_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ResidenceTemperature,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.real_heat_building_demand_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.RealHeatBuildingDemand,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
        )
        self.level_of_utilization_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.LevelOfUtilization,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
        )
        # =================================================================================================================================

    @staticmethod
    def get_default_config():
        """Gets a default configuration of the building controller."""
        config = BuildingControllerConfig(
            minimal_building_temperature_in_celsius=20,
            stop_heating_building_temperature_in_celsius=21,
        )
        return config

    def build(self):
        """Build load profile for entire simulation duration."""
        pass

    def write_to_report(
        self,
    ):
        """Writes a report."""
        pass

    def i_save_state(
        self,
    ):
        """Saves the current state."""
        self.previous_state = self.state.clone()

    def i_restore_state(
        self,
    ):
        """Restores previous state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(
        self,
        timestep: int,
        stsv: cp.SingleTimeStepValues,
    ) -> None:
        """Doublechecks."""
        pass

    def i_prepare_simulation(
        self,
    ) -> None:
        """Prepares the simulation."""
        pass

    def i_simulate(
        self,
        timestep: int,
        stsv: cp.SingleTimeStepValues,
        force_convergence: bool,
    ) -> None:
        """Simulates the building controller."""
        building_temperature_in_celsius = stsv.get_input_value(
            self.residence_temperature_channel
        )
        minimal_building_temperature_in_celsius = (
            self.minimal_building_temperature_in_celsius
        )
        delta_temp_for_level_of_utilization = 0.4

        # Building is warm enough
        if building_temperature_in_celsius > minimal_building_temperature_in_celsius:
            level_of_utilization: float = 0
        # Building get heated up, when temperature is underneath target temperature
        elif (
            building_temperature_in_celsius
            < minimal_building_temperature_in_celsius
            - delta_temp_for_level_of_utilization
        ):
            level_of_utilization = 1
        else:
            level_of_utilization = (
                minimal_building_temperature_in_celsius
                - building_temperature_in_celsius
            )

        real_heat_building_demand_in_watt = (
            self.state.level_of_utilization
            * stsv.get_input_value(self.ref_max_thermal_build_demand_channel)
        )
        self.state.level_of_utilization = level_of_utilization
        stsv.set_output_value(self.level_of_utilization_channel, self.state.level_of_utilization)
        stsv.set_output_value(self.real_heat_building_demand_channel, real_heat_building_demand_in_watt)
