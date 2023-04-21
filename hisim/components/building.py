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
from typing import List, Any, Optional
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

    @classmethod
    def get_default_german_single_family_home(
        cls,
    ) -> Any:
        """Get a default Building."""
        config = BuildingConfig(
            name="Building_1",
            building_code="DE.N.SFH.05.Gen.ReEx.001.002",
            building_heat_capacity_class="medium",
            initial_internal_temperature_in_celsius=23,
            heating_reference_temperature_in_celsius=-14,
            absolute_conditioned_floor_area_in_m2=121.2,
            total_base_area_in_m2=None,
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
        """Calculate the thermal power stored by the thermal mass per second."""
        return (
            self.thermal_mass_temperature_in_celsius
            * self.thermal_capacitance_in_joule_per_kelvin
        ) / 3600

    def self_copy(
        self,
    ):
        """Copy the Building State."""
        return BuildingState(
            self.thermal_mass_temperature_in_celsius,
            self.thermal_capacitance_in_joule_per_kelvin,
        )


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
    ThermalPowerDelivered = "ThermalPowerDelivered"
    ThermalPowerCHP = "ThermalPowerCHP"
    SetHeatingTemperature = "SetHeatingTemperature"
    SetCoolingTemperature = "SetCoolingTemperature"

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
    TemperatureMeanThermalMass = "TemperatureMeanThermalMass"
    TemperatureInternalSurface = "TemperatureInternalSurface"
    TemperatureIndoorAir = "TemperatureIndoorAir"
    TotalEnergyToResidence = "TotalEnergyToResidence"
    SolarGainThroughWindows = "SolarGainThroughWindows"
    ReferenceMaxHeatBuildingDemand = "ReferenceMaxHeatBuildingDemand"
    HeatLoss = "HeatLoss"
    TheoreticalThermalBuildingDemand = "TheoreticalThermalBuildingDemand"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: BuildingConfig,
    ):
        """Construct all the neccessary attributes."""
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

        self.set_heating_temperature_in_celsius: float = 20
        self.set_cooling_temperature_in_celsius: float = 23

        (self.is_in_cache, self.cache_file_path,) = utils.get_cache_file(
            self.component_name,
            self.buildingconfig,
            self.my_simulation_parameters,
        )
        # labeled as C_m in the paper [1] (** Check header), before c_m
        self.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin: float = 0
        self.thermal_capacity_of_building_thermal_mass_reference_in_watthour_per_m2_per_kelvin: float = (
            0
        )
        # labeled as H_w in the paper [2] (*** Check header), before h_tr_w
        self.transmission_heat_transfer_coefficient_for_windows_and_door_in_watt_per_kelvin: float
        self.transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin: float
        # labeled as H_tr_em in paper [2] (*** Check header)
        self.external_part_of_transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin: float = (
            0
        )
        # labeled as H_tr_ms in paper [2] (*** Check header)
        self.internal_part_of_transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin: float = (
            0
        )
        # labeled as h_ms in paper [2] (*** Check header)
        self.heat_transfer_coefficient_between_thermal_mass_and_internal_surface_with_fixed_value_in_watt_per_m2_per_kelvin: float = (
            0
        )
        # labeled as H_tr_is in paper [2] (** Check header)
        self.heat_transfer_coefficient_between_indoor_air_and_internal_surface_in_watt_per_kelvin: float = (
            0
        )
        # labeled as h_is in paper [2] (** Check header)
        self.heat_transfer_coefficient_between_indoor_air_and_internal_surface_with_fixed_value_in_watt_per_m2_per_kelvin: float = (
            0
        )
        # labeled as H_ve in paper [2] (*** Check header), before h_ve_adj
        self.thermal_conductance_by_ventilation_in_watt_per_kelvin: float = 0
        self.heat_transfer_coefficient_by_ventilation_reference_in_watt_per_kelvin: float = (
            0
        )
        self.buildingdata: Any
        self.buildingcode: str

        # before labeled as a_f
        self.conditioned_floor_area_in_m2: float = 1.0
        self.scaled_conditioned_floor_area_in_m2: float = 0
        self.scaling_factor: float = 1.0
        # before labeled as a_m
        self.effective_mass_area_in_m2: float = 0
        # before labeled as a_t
        self.total_internal_surface_area_in_m2: float = 0
        self.room_height_in_m2: float = 0

        self.windows: List[Window]
        self.windows_directions: List[str]
        self.total_windows_area: float
        self.scaled_window_areas_in_m2: List[float]

        self.windows_and_door: List[str]
        self.scaled_windows_and_door_envelope_areas_in_m2: List[float]

        self.opaque_walls: List[str]
        self.scaled_opaque_surfaces_envelope_area_in_m2: List[float]

        self.cache: List[float]
        self.solar_heat_gain_through_windows: List[float]
        # labeled as Phi_ia in paper [1] (** Check header)
        self.heat_flux_indoor_air_in_watt: float
        # labeled as Phi_st in the paper [1] (** Check header)
        self.heat_flux_internal_room_surface_in_watt: float
        # labeled as Phi_m in the paper [1] (** Check header)
        self.heat_flux_thermal_mass_in_watt: float = 0
        self.heat_loss_in_watt: float
        # labeled as Phi_m_tot in the paper [1] (** Check header)
        self.equivalent_heat_flux_in_watt: float
        self.next_thermal_mass_temperature_in_celsius: float
        self.internal_heat_gains_through_occupancy_in_watt: float = 0

        # reference taken from TABULA (* Check header) as Q_ht [kWh/m2.a], before q_ht_ref
        self.total_heat_transfer_reference_in_kilowatthour_per_m2_per_year: float = 0
        # reference taken from TABULA (* Check header) as Q_int [kWh/m2.a], before q_int_ref
        self.internal_heat_sources_reference_in_kilowatthour_per_m2_per_year: float = 0
        # reference taken from TABULA (* Check header) Q_sol [kWh/m2.a], before q_sol_ref (or solar heat sources?)
        self.solar_heat_load_during_heating_seasons_reference_in_kilowatthour_per_m2_per_year: float = (
            0
        )
        # reference taken from TABULA (* Check header) as Q_H_nd [kWh/m2.a], before q_h_nd_ref
        self.energy_need_for_heating_reference_in_kilowatthour_per_m2_per_year: float = (
            0
        )

        self.get_building()
        self.build()
        self.get_physical_param()
        self.max_thermal_building_demand_in_watt = self.calc_max_thermal_building_demand(
            heating_reference_temperature_in_celsius=config.heating_reference_temperature_in_celsius,
            initial_temperature_in_celsius=config.initial_internal_temperature_in_celsius,
        )

        self.state: BuildingState = BuildingState(
            thermal_mass_temperature_in_celsius=config.initial_internal_temperature_in_celsius,
            thermal_capacitance_in_joule_per_kelvin=self.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin,
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

        self.set_heating_temperature_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.SetHeatingTemperature,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            False,
        )
        self.set_cooling_temperature_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.SetCoolingTemperature,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            False,
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
        self.var_max_thermal_building_demand_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ReferenceMaxHeatBuildingDemand,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.ReferenceMaxHeatBuildingDemand} will follow.",
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
        """Get UTSP default connections."""
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
        """Simulate the thermal behaviour of the building."""

        # Gets inputs
        if hasattr(self, "solar_gain_through_windows") is False:
            azimuth = stsv.get_input_value(self.azimuth_channel)
            direct_normal_irradiance = stsv.get_input_value(
                self.direct_normal_irradiance_channel
            )
            direct_horizontal_irradiance = stsv.get_input_value(
                self.direct_horizontal_irradiance_channel
            )
            global_horizontal_irradiance = stsv.get_input_value(
                self.global_horizontal_irradiance_channel
            )
            direct_normal_irradiance_extra = stsv.get_input_value(
                self.direct_normal_irradiance_extra_channel
            )
            apparent_zenith = stsv.get_input_value(self.apparent_zenith_channel)

        self.internal_heat_gains_through_occupancy_in_watt = stsv.get_input_value(
            self.occupancy_heat_gain_channel
        )

        temperature_outside_in_celsius = stsv.get_input_value(
            self.temperature_outside_channel
        )

        self.set_heating_temperature_in_celsius = stsv.get_input_value(
            self.set_heating_temperature_channel
        )
        self.set_cooling_temperature_in_celsius = stsv.get_input_value(
            self.set_cooling_temperature_channel
        )

        thermal_power_delivered_in_watt = 0.0
        if self.thermal_power_delivered_channel.source_output is not None:
            thermal_power_delivered_in_watt = (
                thermal_power_delivered_in_watt
                + stsv.get_input_value(self.thermal_power_delivered_channel)
            )
        if self.thermal_power_chp_channel.source_output is not None:
            thermal_power_delivered_in_watt = (
                thermal_power_delivered_in_watt
                + stsv.get_input_value(self.thermal_power_chp_channel)
            )

        previous_thermal_mass_temperature_in_celsius = (
            self.state.thermal_mass_temperature_in_celsius
        )

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
            solar_heat_gain_through_windows = self.solar_heat_gain_through_windows[
                timestep
            ]

        (
            thermal_mass_average_bulk_temperature_in_celsius,
            heat_loss_in_watt,
            internal_surface_temperature_in_celsius,
            indoor_air_temperature_in_celsius,
        ) = self.calc_crank_nicolson(
            thermal_power_delivered_in_watt=thermal_power_delivered_in_watt,
            internal_heat_gains_in_watt=self.internal_heat_gains_through_occupancy_in_watt,
            solar_heat_gains_in_watt=solar_heat_gain_through_windows,
            outside_temperature_in_celsius=temperature_outside_in_celsius,
            thermal_mass_temperature_prev_in_celsius=previous_thermal_mass_temperature_in_celsius,
        )
        self.state.thermal_mass_temperature_in_celsius = (
            thermal_mass_average_bulk_temperature_in_celsius
        )

        theoretical_thermal_building_demand_in_watt = self.calc_theoretical_thermal_building_demand_for_building(
            set_heating_temperature_in_celsius=self.set_heating_temperature_in_celsius,
            set_cooling_temperature_in_celsius=self.set_cooling_temperature_in_celsius,
            previous_thermal_mass_temperature_in_celsius=previous_thermal_mass_temperature_in_celsius,
            outside_temperature_in_celsius=temperature_outside_in_celsius,
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
        stsv.set_output_value(
            self.solar_gain_through_windows_channel, solar_heat_gain_through_windows
        )
        stsv.set_output_value(
            self.var_max_thermal_building_demand_channel,
            self.max_thermal_building_demand_in_watt,
        )

        stsv.set_output_value(
            self.heat_loss_channel,
            self.heat_loss_in_watt,
        )

        stsv.set_output_value(
            self.theoretical_thermal_building_demand_channel,
            theoretical_thermal_building_demand_in_watt,
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
        # log.information("building timestep " + str(timestep))
        # log.information("building thermal power input " + str(thermal_power_delivered_in_watt))
        # log.information("building real indoor air temperature " + str(indoor_air_temperature_in_celsius))
        # log.information("buiding theoretical demand " + str(theoretical_thermal_building_demand_in_watt))

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
        pass

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
        self.heat_transfer_coefficient_between_thermal_mass_and_internal_surface_with_fixed_value_in_watt_per_m2_per_kelvin = (
            9.1
        )
        # Dimensionless ratio between surfaces and the useful surfaces (7.2.2.2 E9 P36); labeled as A_at in paper [2] (*** Check header); before lambda_at
        self.ratio_between_internal_surface_area_and_floor_area = 4.5
        # Heat transfer coefficient between nodes "air" and "s" (7.2.2.2 E9 P35); labeled as h_is in paper [2] (*** Check header)
        self.heat_transfer_coefficient_between_indoor_air_and_internal_surface_with_fixed_value_in_watt_per_m2_per_kelvin = (
            3.45
        )

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
        self.get_physical_param()
        # Gets conductances
        self.get_conductances()

    def get_physical_param(
        self,
    ):
        """Get the physical parameters from the building data."""

        # Reference area [m^2] (TABULA: Reference floor area )Ref: ISO standard 7.2.2.2
        self.conditioned_floor_area_in_m2 = float(
            self.buildingdata["A_C_Ref"].values[0]
        )

        self.room_height_in_m2 = float(self.buildingdata["h_room"].values[0])

        # Get scaled areas
        self.scaling_over_conditioned_floor_area()
        # Get windows
        self.get_windows()

        # Room Capacitance [J/K] (TABULA: Internal heat capacity) Ref: ISO standard 12.3.1.2
        self.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin = (
            self.building_heat_capacity_class_f_c_in_joule_per_m2_per_kelvin[self.building_heat_capacity_class]
            * self.scaled_conditioned_floor_area_in_m2
        )

        self.effective_mass_area_in_m2 = (
            self.scaled_conditioned_floor_area_in_m2
            * self.building_heat_capacity_class_f_a[self.building_heat_capacity_class]
        )
        self.total_internal_surface_area_in_m2 = (
            self.scaled_conditioned_floor_area_in_m2
            * self.ratio_between_internal_surface_area_and_floor_area
        )
        # Reference properties from TABULA, but not used in the model (scaling factor added in case floor area is different to tabula floor area A_C_ref)
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
        self.thermal_capacity_of_building_thermal_mass_reference_in_watthour_per_m2_per_kelvin = float(
            self.buildingdata["c_m"].values[0]
        )

        # Heat transfer coefficient by ventilation
        self.heat_transfer_coefficient_by_ventilation_reference_in_watt_per_kelvin = (
            float(self.buildingdata["h_Ventilation"].values[0])
            * self.scaled_conditioned_floor_area_in_m2
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
        self.buildingdata = d_f.loc[
            d_f["Code_BuildingVariant"] == self.buildingconfig.building_code
        ]
        self.buildingcode = self.buildingconfig.building_code
        self.building_heat_capacity_class = (
            self.buildingconfig.building_heat_capacity_class
        )

    def get_windows(
        self,
    ):
        """Retrieve data about windows sizes.

        :return:
        """

        self.windows = []
        self.total_windows_area = 0.0
        south_angle = 180

        windows_azimuth_angles = {
            "South": south_angle,
            "East": south_angle - 90,
            "North": south_angle - 180,
            "West": south_angle + 90,
            "Horizontal": None,
        }

        reduction_factor_for_non_perpedicular_radiation = self.buildingdata[
            "F_w"
        ].values[0]
        reduction_factor_for_frame_area_fraction_of_window = self.buildingdata[
            "F_f"
        ].values[0]
        reduction_factor_for_external_vertical_shading = self.buildingdata[
            "F_sh_vert"
        ].values[0]
        total_solar_energy_transmittance_for_perpedicular_radiation = self.buildingdata[
            "g_gl_n"
        ].values[0]

        for index, windows_direction in enumerate(self.windows_directions):
            window_area = float(self.buildingdata["A_Window_" + windows_direction])
            if window_area != 0.0:
                if windows_direction == "Horizontal":
                    window_tilt_angle = 0
                else:
                    window_tilt_angle = 90

                self.windows.append(
                    Window(
                        window_tilt_angle=window_tilt_angle,
                        window_azimuth_angle=windows_azimuth_angles[windows_direction],
                        area=self.scaled_window_areas_in_m2[index],
                        frame_area_fraction_reduction_factor=reduction_factor_for_frame_area_fraction_of_window,
                        glass_solar_transmittance=total_solar_energy_transmittance_for_perpedicular_radiation,
                        nonperpendicular_reduction_factor=reduction_factor_for_non_perpedicular_radiation,
                        external_shading_vertical_reduction_factor=reduction_factor_for_external_vertical_shading,
                    )
                )

                self.total_windows_area += window_area
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

    def scaling_over_conditioned_floor_area(self):
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
        self.scaled_windows_and_door_envelope_areas_in_m2 = []
        self.scaled_opaque_surfaces_envelope_area_in_m2 = []

        if (
            self.buildingconfig.absolute_conditioned_floor_area_in_m2 is not None
            and self.buildingconfig.total_base_area_in_m2 is not None
        ):
            raise ValueError(
                "Only one variable can be used, the other one must be None."
            )

        if self.buildingconfig.absolute_conditioned_floor_area_in_m2 is not None:

            # this is for preventing that the conditioned_floor_area is 0 (some buildings in TABULA have conditioned_floor_area (A_C_Ref) = 0)
            if self.conditioned_floor_area_in_m2 == 0:
                self.scaled_conditioned_floor_area_in_m2 = (
                    self.buildingconfig.absolute_conditioned_floor_area_in_m2
                )
                factor_of_absolute_floor_area_to_tabula_floor_area = 1.0
                self.buildingdata["A_C_Ref"] = self.scaled_conditioned_floor_area_in_m2
            # scaling conditioned floor area
            else:
                factor_of_absolute_floor_area_to_tabula_floor_area = (
                    self.buildingconfig.absolute_conditioned_floor_area_in_m2
                    / self.conditioned_floor_area_in_m2
                )
                self.scaled_conditioned_floor_area_in_m2 = (
                    self.conditioned_floor_area_in_m2
                    * factor_of_absolute_floor_area_to_tabula_floor_area
                )
            self.scaling_factor = factor_of_absolute_floor_area_to_tabula_floor_area

        elif self.buildingconfig.total_base_area_in_m2 is not None:
            # this is for preventing that the conditioned_floor_area is 0
            if self.conditioned_floor_area_in_m2 == 0:
                self.scaled_conditioned_floor_area_in_m2 = (
                    self.buildingconfig.total_base_area_in_m2
                )
                factor_of_total_base_area_to_tabula_floor_area = 1.0
                self.buildingdata["A_C_Ref"] = self.scaled_conditioned_floor_area_in_m2
            # scaling conditioned floor area
            else:
                factor_of_total_base_area_to_tabula_floor_area = (
                    self.buildingconfig.total_base_area_in_m2
                    / self.conditioned_floor_area_in_m2
                )
                self.scaled_conditioned_floor_area_in_m2 = (
                    self.conditioned_floor_area_in_m2
                    * factor_of_total_base_area_to_tabula_floor_area
                )
            self.scaling_factor = factor_of_total_base_area_to_tabula_floor_area

        # if no value for building size is provided in config, use reference value from Tabula or 500 m^2.
        else:
            if self.conditioned_floor_area_in_m2 == 0:
                self.scaled_conditioned_floor_area_in_m2 = 500
                self.buildingdata["A_C_Ref"] = self.scaled_conditioned_floor_area_in_m2
                log.warning(
                    "There is no reference given for absolute conditioned floor area in m^2, so a default of 500 m^2 is used."
                )
            else:
                self.scaled_conditioned_floor_area_in_m2 = (
                    self.conditioned_floor_area_in_m2
                )

            self.scaling_factor = 1

        for w_i in self.windows_and_door:
            self.scaled_windows_and_door_envelope_areas_in_m2.append(
                self.buildingdata["A_" + w_i].values[0] * self.scaling_factor
            )

        for o_w in self.opaque_walls:
            self.scaled_opaque_surfaces_envelope_area_in_m2.append(
                self.buildingdata["A_" + o_w].values[0] * self.scaling_factor
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
        # then the total_wall_area = 4 * area_of_one_wall
        if (
            self.conditioned_floor_area_in_m2 == 0
            and self.buildingconfig.total_base_area_in_m2 is not None
        ):
            total_wall_area_in_m2 = (
                4
                * math.sqrt(self.buildingconfig.total_base_area_in_m2)
                * self.room_height_in_m2
            )
        elif (
            self.conditioned_floor_area_in_m2 == 0
            and self.buildingconfig.absolute_conditioned_floor_area_in_m2 is not None
        ):
            total_wall_area_in_m2 = (
                4
                * math.sqrt(self.buildingconfig.absolute_conditioned_floor_area_in_m2)
                * self.room_height_in_m2
            )
        else:
            total_wall_area_in_m2 = (
                4
                * math.sqrt(self.conditioned_floor_area_in_m2)
                * self.room_height_in_m2
            )
        self.scaled_window_areas_in_m2 = []
        for windows_direction in self.windows_directions:
            window_area_in_m2 = float(
                self.buildingdata["A_Window_" + windows_direction]
            )
            factor_window_area_to_wall_area_tabula = (
                window_area_in_m2 / total_wall_area_in_m2
            )
            self.scaled_window_areas_in_m2.append(
                self.scaled_conditioned_floor_area_in_m2
                * factor_window_area_to_wall_area_tabula
            )

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

        lines.append(
            f"Max Thermal Demand [W]: {self.max_thermal_building_demand_in_watt}"
        )
        lines.append(
            "-------------------------------------------------------------------------------------------"
        )
        lines.append("Building Thermal Conductances:")
        lines.append("--------------------------------------------")
        lines.append(
            f"Transmission for Windows and Doors, based on ISO 13790 (H_tr_w) [W/K]: "
            f"{self.transmission_heat_transfer_coefficient_for_windows_and_door_in_watt_per_kelvin:.2f}"
        )
        lines.append(
            f"External Part of Transmission for Opaque Surfaces, based on ISO 13790 (H_tr_em) [W/K]: "
            f"{self.external_part_of_transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin:.2f}"
        )
        lines.append(
            f"Internal Part of Transmission for Opaque Surfaces, based on ISO 13790 (H_tr_ms) [W/K]: "
            f"{self.internal_part_of_transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin:.2f}"
        )
        lines.append(
            f"Transmission between Indoor Air and Internal Surface, based on ISO 13790 (H_tr_is) [W/K]: "
            f"{self.heat_transfer_coefficient_between_indoor_air_and_internal_surface_in_watt_per_kelvin:.2f}"
        )

        lines.append(
            f"Thermal Conductance by Ventilation, based on TABULA (H_ve) [W/K]: "
            f"{self.heat_transfer_coefficient_by_ventilation_reference_in_watt_per_kelvin:.2f}"
        )

        lines.append(
            "-------------------------------------------------------------------------------------------"
        )
        lines.append("Building Areas:")
        lines.append("--------------------------------------------")
        lines.append(
            f"Conditioned Floor Area (A_f) [m2]: {self.scaled_conditioned_floor_area_in_m2:.2f}"
        )
        lines.append(
            f"Effective Mass Area (A_m), based on ISO 13790 [m2]: {self.effective_mass_area_in_m2:.2f}"
        )
        lines.append(
            f"Total Internal Surface Area, based on ISO 13790 (A_t) [m2]: {self.total_internal_surface_area_in_m2:.2f}"
        )

        lines.append(
            "-------------------------------------------------------------------------------------------"
        )
        lines.append("Building Thermal Capacitances:")
        lines.append("--------------------------------------------")
        lines.append(
            f"Floor Related Thermal Capacitance of Thermal Mass, based on ISO 13790 [Wh/m2.K]: "
            f"{(self.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin / (3.6e3 *self.scaled_conditioned_floor_area_in_m2)):.2f}"
        )
        lines.append(
            f"Floor Related Thermal Capacitance of Thermal Mass, based on TABULA [Wh/m2.K]: "
            f"{(self.thermal_capacity_of_building_thermal_mass_reference_in_watthour_per_m2_per_kelvin):.2f}"
        )
        lines.append(
            "-------------------------------------------------------------------------------------------"
        )
        lines.append("Building Heat Transfers:")
        lines.append("--------------------------------------------")
        lines.append(
            f"Annual Floor Related Total Heat Loss, based on TABULA (Q_ht) [kWh/m2.a]: "
            f"{self.total_heat_transfer_reference_in_kilowatthour_per_m2_per_year:.2f}"
        )
        lines.append(
            f"Annual Floor Related Internal Heat Gain, based on TABULA (Q_int) [kWh/m2.a]: "
            f"{self.internal_heat_sources_reference_in_kilowatthour_per_m2_per_year:.2f}"
        )
        lines.append(
            f"Annual Floor Related Solar Heat Gain, based on TABULA (Q_sol) [kWh/m2.a]: "
            f"{self.solar_heat_load_during_heating_seasons_reference_in_kilowatthour_per_m2_per_year:.2f}"
        )
        lines.append(
            f"Annual Floor Related Heating Demand, based on TABULA (Q_h_nd) [kWh/m2.a]: "
            f"{self.energy_need_for_heating_reference_in_kilowatthour_per_m2_per_year:.2f}"
        )
        return self.buildingconfig.get_string_dict() + lines

    # =====================================================================================================================================
    # Calculation of the heat transfer coefficients or thermal conductances.
    # (**/*** Check header)

    @property
    def transmission_heat_transfer_coefficient_1_in_watt_per_kelvin(
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
            self.transmission_heat_transfer_coefficient_1_in_watt_per_kelvin
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
        # Objects: Doors, windows, curtain walls and windowed walls ISO 7.2.2.2 (here Window 1, Window 2 and Door 1)

        self.transmission_heat_transfer_coefficient_for_windows_and_door_in_watt_per_kelvin = (
            0.0
        )

        # here instead of reading H_Transmission from buildingdata it will be calculated manually using
        # input values U_Actual, A_ and b_Transmission also given by TABULA buildingdata
        for index, w_i in enumerate(self.windows_and_door):
            # with with H_Tr = U * A * b_tr [W/K], here b_tr is not given in TABULA data, so it is chosen 1.0
            h_tr_i = (
                self.buildingdata["U_Actual_" + w_i].values[0]
                * self.scaled_windows_and_door_envelope_areas_in_m2[index]
                * 1.0
            )
            self.transmission_heat_transfer_coefficient_for_windows_and_door_in_watt_per_kelvin += float(
                h_tr_i
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
        # here opaque surfaces are Roof 1, Roof 2, Wall 1, Wall 2, Wall 3, Floor 1, Floor 2
        self.transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin = (
            0.0
        )
        # here modification for scalability: instead of reading H_Transmission from buildingdata it will be calculated manually using
        # input values U_Actual, A_Calc and b_Transmission also given by TABULA buildingdata
        for index, o_w in enumerate(self.opaque_walls):
            # with with H_Tr = U * A * b_tr [W/K]
            h_tr_i = (
                self.buildingdata["U_Actual_" + o_w].values[0]
                * self.scaled_opaque_surfaces_envelope_area_in_m2[index]
                * self.buildingdata["b_Transmission_" + o_w].values[0]
            )
            self.transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin += float(
                h_tr_i
            )
        if (
            self.transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin
            != 0
            and self.internal_part_of_transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin
            != 0
        ):
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
        """Based on the EPISCOPE TABULA (* Check header)."""
        # Long from for H_ve_adj: Ventilation
        # Determine the ventilation conductance

        heat_capacity_of_air_per_volume_in_watt_hour_per_m3_per_kelvin = 0.34
        self.thermal_conductance_by_ventilation_in_watt_per_kelvin = (
            heat_capacity_of_air_per_volume_in_watt_hour_per_m3_per_kelvin
            * float(
                self.buildingdata["n_air_use"] + self.buildingdata["n_air_infiltration"]
            )
            * self.scaled_conditioned_floor_area_in_m2
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

        if (
            direct_normal_irradiance != 0
            or direct_horizontal_irradiance != 0
            or global_horizontal_irradiance != 0
        ):

            for window in self.windows:
                solar_heat_gain = Window.calc_solar_heat_gains(
                    self,
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

        self.heat_flux_indoor_air_in_watt = 0.5 * internal_heat_gains_in_watt
        # Heat flow to the surface node in W, before labeled Phi_st

        self.heat_flux_internal_room_surface_in_watt = (
            1
            - (self.effective_mass_area_in_m2 / self.total_internal_surface_area_in_m2)
            - (
                self.transmission_heat_transfer_coefficient_for_windows_and_door_in_watt_per_kelvin
                / (
                    self.heat_transfer_coefficient_between_thermal_mass_and_internal_surface_with_fixed_value_in_watt_per_m2_per_kelvin
                    * self.total_internal_surface_area_in_m2
                )
            )
        ) * (0.5 * internal_heat_gains_in_watt + solar_heat_gains_in_watt)

        # Heat flow to the thermal mass node in W, before labeled Phi_m
        self.heat_flux_thermal_mass_in_watt = (
            self.effective_mass_area_in_m2 / self.total_internal_surface_area_in_m2
        ) * (0.5 * internal_heat_gains_in_watt + solar_heat_gains_in_watt)

        # Heat loss in W, before labeled Phi_loss
        self.heat_loss_in_watt = (
            self.transmission_heat_transfer_coefficient_for_windows_and_door_in_watt_per_kelvin
            / (
                self.heat_transfer_coefficient_between_thermal_mass_and_internal_surface_with_fixed_value_in_watt_per_m2_per_kelvin
                * self.total_internal_surface_area_in_m2
            )
        ) * (0.5 * internal_heat_gains_in_watt + solar_heat_gains_in_watt)
        return self.heat_loss_in_watt

    # =====================================================================================================================================
    # Determination of different temperatures T_air, T_s, T_m,t and T_m and global heat transfer Phi_m_tot which are used in crank-nicolson method.
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

    def calc_equivalent_heat_flux_in_watt(
        self, temperature_outside_in_celsius, thermal_power_delivered_in_watt
    ):
        """Calculate a global heat transfer: Phi_m_tot.

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
                + self.transmission_heat_transfer_coefficient_1_in_watt_per_kelvin
                * (
                    (
                        (
                            self.heat_flux_indoor_air_in_watt
                            + thermal_power_delivered_in_watt
                        )
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
        thermal_power_delivered_in_watt,
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
            self.internal_part_of_transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin
            * thermal_mass_temperature_in_celsius
            + self.heat_flux_internal_room_surface_in_watt
            + self.transmission_heat_transfer_coefficient_for_windows_and_door_in_watt_per_kelvin
            * temperature_outside_in_celsius
            + self.transmission_heat_transfer_coefficient_1_in_watt_per_kelvin
            * (
                t_supply
                + (self.heat_flux_indoor_air_in_watt + thermal_power_delivered_in_watt)
                / self.thermal_conductance_by_ventilation_in_watt_per_kelvin
            )
        ) / (
            self.internal_part_of_transmission_heat_transfer_coefficient_for_opaque_elements_in_watt_per_kelvin
            + self.transmission_heat_transfer_coefficient_for_windows_and_door_in_watt_per_kelvin
            + self.transmission_heat_transfer_coefficient_1_in_watt_per_kelvin
        )

    def calc_temperature_of_the_inside_air_in_celsius(
        self,
        temperature_outside_in_celsius,
        temperature_internal_room_surfaces_in_celsius,
        thermal_power_delivered_in_watt,
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
            self.heat_transfer_coefficient_between_indoor_air_and_internal_surface_in_watt_per_kelvin
            * temperature_internal_room_surfaces_in_celsius
            + self.thermal_conductance_by_ventilation_in_watt_per_kelvin * t_supply
            + thermal_power_delivered_in_watt
            + self.heat_flux_indoor_air_in_watt
        ) / (
            self.heat_transfer_coefficient_between_indoor_air_and_internal_surface_in_watt_per_kelvin
            + self.thermal_conductance_by_ventilation_in_watt_per_kelvin
        )

    def calc_crank_nicolson(
        self,
        internal_heat_gains_in_watt,
        solar_heat_gains_in_watt,
        outside_temperature_in_celsius,
        thermal_mass_temperature_prev_in_celsius,
        thermal_power_delivered_in_watt,
    ):
        """Determine node temperatures and computes derivation to determine the new node temperatures.

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
        self.calc_equivalent_heat_flux_in_watt(
            outside_temperature_in_celsius, thermal_power_delivered_in_watt
        )

        # calculates the new bulk temperature POINT from the old one # CHECKED Requires t_m_prev
        self.calc_next_thermal_mass_temperature_in_celsius(
            thermal_mass_temperature_prev_in_celsius
        )

        # calculates the AVERAGE bulk temperature used for the remaining
        thermal_mass_average_bulk_temperature_in_celsius = self.calc_thermal_mass_averag_bulk_temperature_in_celsius_used_for_calculations(
            thermal_mass_temperature_prev_in_celsius
        )

        # keep these calculations if later you are interested in the indoor surface or air temperature
        # Updates internal surface temperature (t_s)
        internal_room_surface_temperature_in_celsius = (
            self.calc_temperature_of_internal_room_surfaces_in_celsius(
                outside_temperature_in_celsius,
                thermal_mass_average_bulk_temperature_in_celsius,
                thermal_power_delivered_in_watt,
            )
        )

        # Updates indoor air temperature (t_air)
        indoor_air_temperature_in_celsius = (
            self.calc_temperature_of_the_inside_air_in_celsius(
                outside_temperature_in_celsius,
                internal_room_surface_temperature_in_celsius,
                thermal_power_delivered_in_watt,
            )
        )

        return (
            thermal_mass_average_bulk_temperature_in_celsius,
            heat_loss_in_watt,
            internal_room_surface_temperature_in_celsius,
            indoor_air_temperature_in_celsius,
        )

    # =====================================================================================================================================
    # Calculation of maximal thermal building heat demand according to TABULA (* Check header).
    def calc_max_thermal_building_demand(
        self,
        initial_temperature_in_celsius: float,
        heating_reference_temperature_in_celsius: float,
    ) -> Any:
        """Calculate maximal thermal building demand using TABULA data."""

        self.vals1_in_watt_per_m2_per_kelvin = float(
            self.buildingdata["h_Transmission"].values[0]
        )

        if self.vals1_in_watt_per_m2_per_kelvin is None:
            raise ValueError("h_Transmission was none.")
        self.vals2_in_watt_per_m2_per_kelvin = float(
            self.buildingdata["h_Ventilation"].values[0]
        )

        # with with dQ/dt = h * (T2-T1) * A -> [W]
        max_thermal_building_demand_in_watt = (
            (
                self.vals1_in_watt_per_m2_per_kelvin
                + self.vals2_in_watt_per_m2_per_kelvin
            )
            * (
                initial_temperature_in_celsius
                - heating_reference_temperature_in_celsius
            )
            * self.scaled_conditioned_floor_area_in_m2
        )
        return max_thermal_building_demand_in_watt

    # =====================================================================================================================================
    # Calculate theroretical thermal building demand according to ISO 13790 C.4

    def calc_theoretical_thermal_building_demand_for_building(
        self,
        set_heating_temperature_in_celsius: float,
        set_cooling_temperature_in_celsius: float,
        previous_thermal_mass_temperature_in_celsius: float,
        outside_temperature_in_celsius: float,
    ) -> Any:
        """Calculate theoretical thermal building demand to attain a certain set temperature according to ISO 13790 (C.4)."""

        # step1, calculate air temperature when thermal power delivered is zero
        indoor_air_temperature_zero_in_celsius = self.calc_indoor_air_temperature_zero_step_one(
            previous_thermal_mass_temperature_in_celsius=previous_thermal_mass_temperature_in_celsius,
            outside_temperature_in_celsius=outside_temperature_in_celsius,
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
            or indoor_air_temperature_zero_in_celsius
            < set_heating_temperature_in_celsius
        ):
            # step2, heating or cooling is needed, calculate air temperature when therma power delivered is 10 W/m2
            (
                indoor_air_temperature_ten_in_celsius,
                ten_thermal_power_delivered_in_watt,
            ) = self.calc_indoor_air_temperature_ten_step_two(
                previous_thermal_mass_temperature_in_celsius=previous_thermal_mass_temperature_in_celsius,
                outside_temperature_in_celsius=outside_temperature_in_celsius,
            )
            # set air temperature
            if (
                indoor_air_temperature_zero_in_celsius
                > set_cooling_temperature_in_celsius
            ):
                indoor_air_temperature_set_in_celsius = (
                    set_cooling_temperature_in_celsius
                )
            elif (
                indoor_air_temperature_zero_in_celsius
                < set_heating_temperature_in_celsius
            ):
                indoor_air_temperature_set_in_celsius = (
                    set_heating_temperature_in_celsius
                )

            theoretical_thermal_building_demand_in_watt = self.calc_theoretical_thermal_building_demand_when_heating_or_cooling_needed_step_two(
                ten_thermal_power_delivered_in_watt=ten_thermal_power_delivered_in_watt,
                indoor_air_temperature_zero_in_celsius=indoor_air_temperature_zero_in_celsius,
                indoor_air_temperature_ten_in_celsius=indoor_air_temperature_ten_in_celsius,
                indoor_air_temperature_set_in_celsius=indoor_air_temperature_set_in_celsius,
            )
        else:
            raise ValueError("value error for theoretical building demand")

        return theoretical_thermal_building_demand_in_watt

    def calc_indoor_air_temperature_zero_step_one(
        self,
        previous_thermal_mass_temperature_in_celsius: float,
        outside_temperature_in_celsius: float,
    ) -> Any:
        """Calculate indoor air temperature for zero thermal power delivered (Phi_HC_nd) according to ISO 13790 (C.4.2)."""

        # step1: check if heating or cooling is needed
        zero_thermal_power_delivered_in_watt = 0

        # calculate temperatures (C.9 - C.11)
        thermal_mass_average_bulk_temperature_in_celsius = self.calc_thermal_mass_averag_bulk_temperature_in_celsius_used_for_calculations(
            previous_thermal_mass_temperature_in_celsius=previous_thermal_mass_temperature_in_celsius
        )

        internal_room_surface_temperature_in_celsius = self.calc_temperature_of_internal_room_surfaces_in_celsius(
            temperature_outside_in_celsius=outside_temperature_in_celsius,
            thermal_mass_temperature_in_celsius=thermal_mass_average_bulk_temperature_in_celsius,
            thermal_power_delivered_in_watt=zero_thermal_power_delivered_in_watt,
        )

        # indoor air temperature named zero
        indoor_air_temperature_zero_in_celsius = self.calc_temperature_of_the_inside_air_in_celsius(
            temperature_outside_in_celsius=outside_temperature_in_celsius,
            temperature_internal_room_surfaces_in_celsius=internal_room_surface_temperature_in_celsius,
            thermal_power_delivered_in_watt=zero_thermal_power_delivered_in_watt,
        )
        return indoor_air_temperature_zero_in_celsius

    def calc_indoor_air_temperature_ten_step_two(
        self,
        previous_thermal_mass_temperature_in_celsius: float,
        outside_temperature_in_celsius: float,
    ) -> Any:
        """Calculate indoor air temperature for thermal power delivered (Phi_HC_nd) of 10 W/m2 according to ISO 13790 (C.4.2)."""
        heating_power_in_watt_per_m2 = 10
        ten_thermal_power_delivered_in_watt = (
            heating_power_in_watt_per_m2 * self.scaled_conditioned_floor_area_in_m2
        )

        # calculate temperatures (C.9 - C.11)
        thermal_mass_average_bulk_temperature_in_celsius = self.calc_thermal_mass_averag_bulk_temperature_in_celsius_used_for_calculations(
            previous_thermal_mass_temperature_in_celsius=previous_thermal_mass_temperature_in_celsius
        )

        internal_room_surface_temperature_in_celsius = self.calc_temperature_of_internal_room_surfaces_in_celsius(
            temperature_outside_in_celsius=outside_temperature_in_celsius,
            thermal_mass_temperature_in_celsius=thermal_mass_average_bulk_temperature_in_celsius,
            thermal_power_delivered_in_watt=ten_thermal_power_delivered_in_watt,
        )

        # indoor air temperature named zero
        indoor_air_temperature_ten_in_celsius = self.calc_temperature_of_the_inside_air_in_celsius(
            temperature_outside_in_celsius=outside_temperature_in_celsius,
            temperature_internal_room_surfaces_in_celsius=internal_room_surface_temperature_in_celsius,
            thermal_power_delivered_in_watt=ten_thermal_power_delivered_in_watt,
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
            * (
                indoor_air_temperature_set_in_celsius
                - indoor_air_temperature_zero_in_celsius
            )
            / (
                indoor_air_temperature_ten_in_celsius
                - indoor_air_temperature_zero_in_celsius
            )
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
            log.warning(
                "window azimuth angle was set to 0 south because no value was set."
            )
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
