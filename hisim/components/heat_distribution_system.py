"""Heat Distribution Module."""

# clean
import importlib
from enum import Enum, unique
from typing import Any, ClassVar, List, Optional, Tuple
from dataclasses import dataclass
from dataclasses_json import dataclass_json

import pandas as pd
import numpy as np

import hisim.component as cp
from hisim.components.building import Building
from hisim.components.weather import Weather
from hisim.simulationparameters import SimulationParameters
from hisim.components.configuration import PhysicsConfig
from hisim import loadtypes as lt
from hisim import utils
from hisim.component import OpexCostDataClass, CapexCostDataClass
from hisim.config import (
    Catalog,
    ComponentID,
    ConfigBase,
    DisplayConfig,
    FactContribution,
    Sizable,
    Size,
    SizingContext,
    concrete,
    sized_field,
)
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiHelperClass, KpiTagEnumClass
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions

__authors__ = "Katharina Rieck, Noah Pflugradt"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Katharina Rieck"
__email__ = "k.rieck@fz-juelich.de"
__status__ = ""


@unique
class HeatDistributionSystemType(str, Enum):
    """Set Heating System Types.

    Every member carries its own name as its value so that a serialized
    configuration names the heating system explicitly instead of encoding it as
    an integer. Only member identity is meaningful; no ordinal is used anywhere.
    """

    RADIATOR = "RADIATOR"
    FLOORHEATING = "FLOORHEATING"
    LOW_TEMPERATURE_RADIATOR = "LOW_TEMPERATURE_RADIATOR"


@unique
class PositionHotWaterStorageInSystemSetup(str, Enum):
    """Set Postion of Hot Water Storage in system setup.

    Every member carries its own name as its value so that a serialized
    configuration spells the storage position out instead of encoding it as an
    integer. Only member identity is meaningful; no ordinal is used anywhere.

    PARALLEL:
    Hot Water Storage is parallel to heatpump and hds, mass flow of heatpump and heat distribution system are independent of each other.
    Heatpump mass flow is calculated in hp model, hds mass flow is calculated in hds model.

    SERIES:
    Hot Water Storage in series to hp/hds, mass flow of hds is an input and connected to hp, hot water storage is between output of hds and input of hp

    NO_STORAGE:
    No Hot Water Storage in system setup for space heating
    """

    PARALLEL = "PARALLEL"
    SERIES = "SERIES"
    NO_STORAGE_MASS_FLOW_FROM_HEAT_GENERATOR = "NO_STORAGE_MASS_FLOW_FROM_HEAT_GENERATOR"
    NO_STORAGE_MASS_FLOW_FIX = "NO_STORAGE_MASS_FLOW_FIX"


@dataclass_json
@dataclass
class HeatDistributionConfig(ConfigBase):
    """Configuration of the HeatingWaterStorage class."""

    @classmethod
    def get_main_classname(cls) -> str:
        """Return the full class name of the base class."""
        return HeatDistribution.get_full_classname()  # type: ignore[no-any-return]

    #: Named default presets. The heat distribution system has no defensible nominal —
    #: every essential field is sized to the building it serves — so the standard preset
    #: is a pure sizable template: all three sized fields default to AUTO and the config
    #: cannot reach a component unresolved (config_defaults_spec.md design B).
    PRESET_COMPONENT_ID: ClassVar[ComponentID] = ComponentID(name="HeatDistributionSystem")

    component_id: ComponentID
    heating_system: Sizable[HeatDistributionSystemType] = sized_field(
        rule=Size.HEAT_DISTRIBUTION_SYSTEM_TYPE, value_type=HeatDistributionSystemType
    )
    water_mass_flow_rate_in_kg_per_second: Sizable[float] = sized_field(
        rule=Size.WATER_MASS_FLOW_RATE_IN_KG_PER_SECOND.rounded(2)
    )
    absolute_conditioned_floor_area_in_m2: Sizable[float] = sized_field(rule=Size.CONDITIONED_FLOOR_AREA_IN_M2)
    position_hot_water_storage_in_system: PositionHotWaterStorageInSystemSetup = (
        PositionHotWaterStorageInSystemSetup.PARALLEL
    )
    #: CO2 footprint of investment in kg; None means postprocessing looks it up.
    device_co2_footprint_in_kg: Optional[float] = None
    #: cost for investment in Euro; None means postprocessing looks it up.
    investment_costs_in_euro: Optional[float] = None
    #: lifetime in years; None means postprocessing looks it up.
    lifetime_in_years: Optional[float] = None
    # maintenance cost in euro per year; None means postprocessing looks it up.
    maintenance_costs_in_euro_per_year: Optional[float] = None
    # subsidies as percentage of investment costs; None means postprocessing looks it up.
    subsidy_as_percentage_of_investment_costs: Optional[float] = None

    presets: ClassVar[Catalog] = Catalog(
        standard=lambda: HeatDistributionConfig(component_id=HeatDistributionConfig.PRESET_COMPONENT_ID),
    )


@dataclass
class HeatDistributionSystemState:
    """HeatDistributionSystemState class."""

    water_output_temperature_in_celsius: float = 25.0
    water_input_temperature_in_celsius: float = 25.0
    thermal_power_delivered_in_watt: float = 0.0

    def self_copy(self) -> "HeatDistributionSystemState":
        """Copy the Heat Distribution State."""
        return HeatDistributionSystemState(
            self.water_output_temperature_in_celsius,
            self.water_input_temperature_in_celsius,
            self.thermal_power_delivered_in_watt,
        )


class HeatDistribution(cp.Component):
    """Heat Distribution System.

    It simulates the heat exchange between heat generator and building.

    """

    # Inputs
    State = "State"
    WaterTemperatureInput = "WaterTemperatureInput"
    TheoreticalThermalBuildingDemand = "TheoreticalThermalBuildingDemand"
    ResidenceTemperatureIndoorAir = "ResidenceTemperatureIndoorAir"
    WaterMassFlowInput = "WaterMassFlowInput"

    # Outputs
    WaterTemperatureInlet = "WaterTemperatureInlet"
    WaterTemperatureOutput = "WaterTemperatureOutput"
    WaterTemperatureDifference = "WaterTemperatureDifference"
    ThermalPowerDelivered = "ThermalPowerDelivered"
    WaterMassFlowHDS = "WaterMassFlowHDS"

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__(
        self,
        config: HeatDistributionConfig,
        my_simulation_parameters: SimulationParameters,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.heat_distribution_system_config = config

        self.thermal_power_delivered_in_watt: float = 0.0
        self.water_temperature_output_in_celsius: float = 21
        self.water_input_temperature_in_celsius: float = 21

        self.heating_distribution_system_water_mass_flow_rate_in_kg_per_second: float = concrete(
            self.heat_distribution_system_config.water_mass_flow_rate_in_kg_per_second
        )

        self.absolute_conditioned_floor_area_in_m2: float = concrete(
            self.heat_distribution_system_config.absolute_conditioned_floor_area_in_m2
        )

        self.position_hot_water_storage_in_system: PositionHotWaterStorageInSystemSetup = (
            self.heat_distribution_system_config.position_hot_water_storage_in_system
        )

        self.build()

        self.state: HeatDistributionSystemState = HeatDistributionSystemState(
            water_output_temperature_in_celsius=21.0,
            water_input_temperature_in_celsius=21.0,
            thermal_power_delivered_in_watt=0,
        )
        self.previous_state: HeatDistributionSystemState = self.state.self_copy()

        # Inputs
        self.state_channel: cp.ComponentInput = self.add_input(
            self.component_name, self.State, lt.LoadTypes.ANY, lt.Units.ANY, True
        )

        self.theoretical_thermal_building_demand_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TheoreticalThermalBuildingDemand,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            True,
        )

        self.water_temperature_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureInput,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )

        self.residence_temperature_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ResidenceTemperatureIndoorAir,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )

        self.water_mass_flow_rate_hp_in_kg_per_second_channel: Optional[cp.ComponentInput] = None
        if self.position_hot_water_storage_in_system in (
            PositionHotWaterStorageInSystemSetup.SERIES,
            PositionHotWaterStorageInSystemSetup.NO_STORAGE_MASS_FLOW_FROM_HEAT_GENERATOR,
        ):
            # just important for heating system without parallel bufferstorage
            self.water_mass_flow_rate_hp_in_kg_per_second_channel = self.add_input(
                self.component_name,
                self.WaterMassFlowInput,
                lt.LoadTypes.WARM_WATER,
                lt.Units.KG_PER_SEC,
                True,
            )

        # Outputs
        self.water_temperature_inlet_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.WaterTemperatureInlet,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.WaterTemperatureInlet} will follow.",
        )
        self.water_temperature_outlet_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.WaterTemperatureOutput,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.WaterTemperatureOutput} will follow.",
        )
        self.water_temperature_difference_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.WaterTemperatureDifference,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.KELVIN,
            output_description=f"here a description for {self.WaterTemperatureDifference} will follow.",
        )
        self.thermal_power_delivered_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPowerDelivered,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.ThermalPowerDelivered} will follow.",
        )
        self.water_mass_flow_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.WaterMassFlowHDS,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            output_description=f"here a description for {self.WaterMassFlowHDS} will follow.",
        )

        self.add_default_connections(self.get_default_connections_from_heat_distribution_controller())
        self.add_default_connections(self.get_default_connections_from_building())
        self.add_default_connections(self.get_default_connections_from_district_heating())
        if self.position_hot_water_storage_in_system == PositionHotWaterStorageInSystemSetup.PARALLEL:
            self.add_default_connections(self.get_default_connections_from_simple_hot_water_storage())

    def get_default_connections_from_heat_distribution_controller(
        self,
    ) -> List[cp.ComponentConnection]:
        """Get heat distribution controller default connections."""

        connections = []
        heat_distribution_controller_classname = HeatDistributionController.get_classname()
        connections.append(
            cp.ComponentConnection(
                HeatDistribution.State,
                heat_distribution_controller_classname,
                HeatDistributionController.State,
            )
        )
        return connections

    def get_default_connections_from_building(
        self,
    ) -> List[cp.ComponentConnection]:
        """Get building default connections."""

        connections = []
        building_classname = Building.get_classname()
        connections.append(
            cp.ComponentConnection(
                HeatDistribution.TheoreticalThermalBuildingDemand,
                building_classname,
                Building.TheoreticalThermalBuildingDemand,
            )
        )

        connections.append(
            cp.ComponentConnection(
                HeatDistribution.ResidenceTemperatureIndoorAir,
                building_classname,
                Building.TemperatureIndoorAir,
            )
        )
        return connections

    def get_default_connections_from_simple_hot_water_storage(
        self,
    ) -> List[cp.ComponentConnection]:
        """Get simple hot water storage default connections."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.simple_water_storage"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "SimpleHotWaterStorage")
        connections = []
        hws_classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                HeatDistribution.WaterTemperatureInput,
                hws_classname,
                component_class.WaterTemperatureToHeatDistribution,
            )
        )
        return connections

    def get_default_connections_from_district_heating(
        self,
    ) -> List[cp.ComponentConnection]:
        """Get distrct heating default connections."""
        # use importlib for importing the other component in order to avoid circular-import errors
        # for district heating as heating source no
        component_module_name = "hisim.components.generic_district_heating"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "DistrictHeating")

        connections = []
        classname = component_class.get_classname()
        connections.append(
            cp.ComponentConnection(
                HeatDistribution.WaterTemperatureInput,
                classname,
                component_class.WaterOutputShTemperature,
            )
        )
        return connections

    def build(
        self,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius: float = (
            PhysicsConfig.get_properties_for_energy_carrier(
                energy_carrier=lt.LoadTypes.WATER
            ).specific_heat_capacity_in_joule_per_kg_per_kelvin
        )
        self.density_of_water_in_kg_per_m3: float = PhysicsConfig.get_properties_for_energy_carrier(
            energy_carrier=lt.LoadTypes.WATER
        ).density_in_kg_per_m3

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_state = self.state.self_copy()
        # pass

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.state = self.previous_state.self_copy()
        # pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(self) -> List[str]:
        """Write important variables to report."""
        return self.heat_distribution_system_config.get_string_dict()

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the heat distribution system."""

        # Get inputs ------------------------------------------------------------------------------------------------------------
        state_controller = stsv.get_input_value(self.state_channel)
        theoretical_thermal_building_demand_in_watt = stsv.get_input_value(
            self.theoretical_thermal_building_demand_channel
        )
        residence_temperature_input_in_celsius = stsv.get_input_value(self.residence_temperature_input_channel)

        if self.position_hot_water_storage_in_system in (
            PositionHotWaterStorageInSystemSetup.PARALLEL,
            PositionHotWaterStorageInSystemSetup.NO_STORAGE_MASS_FLOW_FIX,
        ):
            water_mass_flow_rate_in_kg_per_second = (
                self.heating_distribution_system_water_mass_flow_rate_in_kg_per_second
            )
        else:
            # important for heating system without buffer storage
            if self.water_mass_flow_rate_hp_in_kg_per_second_channel is None:
                raise ValueError(
                    "water_mass_flow_rate_hp_in_kg_per_second_channel is not configured "
                    "for the current hot water storage position."
                )
            water_mass_flow_rate_in_kg_per_second = stsv.get_input_value(
                self.water_mass_flow_rate_hp_in_kg_per_second_channel
            )

        if water_mass_flow_rate_in_kg_per_second == 0:
            # important for heating system without buffer storage
            (
                water_temperature_input_in_celsius,
                water_temperature_output_in_celsius,
                thermal_power_delivered_in_watt,
            ) = self.determine_water_temperature_input_output_effective_thermal_power_without_mass_flow(
                residence_temperature_in_celsius=residence_temperature_input_in_celsius,
            )
        else:
            water_temperature_input_in_celsius = stsv.get_input_value(self.water_temperature_input_channel)

            if state_controller in (1, -1):
                (
                    water_temperature_output_in_celsius,
                    thermal_power_delivered_in_watt,
                ) = self.determine_water_temperature_output_after_heat_exchange_with_building_and_effective_thermal_power(
                    water_temperature_input_in_celsius=water_temperature_input_in_celsius,
                    water_mass_flow_in_kg_per_second=water_mass_flow_rate_in_kg_per_second,
                    theoretical_thermal_buiding_demand_in_watt=theoretical_thermal_building_demand_in_watt,
                    residence_temperature_in_celsius=residence_temperature_input_in_celsius,
                )

            elif state_controller == 0:
                thermal_power_delivered_in_watt = 0.0
                # keep temperature almost as is, as no heating/cooling occurs,
                # but introduce small change of temperature to account for heat loss and gain
                # between heat distribution system and building
                water_temperature_output_in_celsius = water_temperature_input_in_celsius + 0.01 * (
                    residence_temperature_input_in_celsius - water_temperature_input_in_celsius
                )
            else:
                raise ValueError("unknown hds controller mode")

        # Set outputs -----------------------------------------------------------------------------------------------------------
        stsv.set_output_value(
            self.water_temperature_inlet_channel,
            self.state.water_input_temperature_in_celsius,
            # water_temperature_input_in_celsius
        )
        stsv.set_output_value(
            self.water_temperature_outlet_channel,
            self.state.water_output_temperature_in_celsius,
            # water_temperature_output_in_celsius,
        )
        stsv.set_output_value(
            self.water_temperature_difference_channel,
            #    water_temperature_input_in_celsius-water_temperature_output_in_celsius
            self.state.water_input_temperature_in_celsius - self.state.water_output_temperature_in_celsius,
        )

        stsv.set_output_value(
            self.thermal_power_delivered_channel,
            self.state.thermal_power_delivered_in_watt,
            # thermal_power_delivered_in_watt,
        )
        stsv.set_output_value(
            self.water_mass_flow_channel,
            water_mass_flow_rate_in_kg_per_second,
        )

        # write values to state
        self.state.water_output_temperature_in_celsius = water_temperature_output_in_celsius
        self.state.water_input_temperature_in_celsius = water_temperature_input_in_celsius
        self.state.thermal_power_delivered_in_watt = thermal_power_delivered_in_watt

    def determine_water_temperature_input_output_effective_thermal_power_without_mass_flow(
        self,
        residence_temperature_in_celsius: float,
    ) -> Any:
        """Calculate cooled or heated water temperature due to free convection after heat exchange between heat distribution system and building without mass flow."""

        # source1: https://www.mdpi.com/1996-1073/16/15/5850
        # source2: https://www.researchgate.net/publication/305659004_Modelling_and_Simulation_of_Underfloor_Heating_System_Supplied_from_Heat_Pump
        # source3: https://www.sciencedirect.com/science/article/pii/S0378778816312749?via%3Dihub
        # assumption1: https://www.heizsparer.de/heizung/heizkorper/fussbodenheizung/fussbodenheizung-planen-heizkreise-berechnen
        # assumption2: heat transfer direction just to the room --> other direction is adiabatic
        # assumption3: still air and no floor covering considered

        height_of_screed = 40 / 1000  # in m
        thermal_conductivity_screed = 1.4  # in W/(m^2K)
        heat_transfer_coefficient_screed_to_air = 5.8  # assumption3
        inner_pipe_diameter = 16 / 1000  # in m  # Task 44 IEA Annex 38
        outer_pipe_diameter = (16 + 2) / 1000  # in m  # Task 44 IEA Annex 38

        heat_resistance_coefficient_hds_pipe_to_air = (
            height_of_screed / thermal_conductivity_screed + 1 / heat_transfer_coefficient_screed_to_air
        )

        length_of_hds_pipe = 8.8 * self.absolute_conditioned_floor_area_in_m2  # in m -> assumption1
        inner_volume_of_hds = (np.pi / 4) * ((inner_pipe_diameter) ** 2) * length_of_hds_pipe  # in m^3

        outer_surface_of_hds_pipe = np.pi * outer_pipe_diameter * length_of_hds_pipe  # in m^2

        mass_of_water_in_hds = inner_volume_of_hds * self.density_of_water_in_kg_per_m3

        time_constant_hds = (
            mass_of_water_in_hds * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
        ) * (heat_resistance_coefficient_hds_pipe_to_air / outer_surface_of_hds_pipe)

        water_temperature_input_in_celsius = residence_temperature_in_celsius + (
            (self.state.water_input_temperature_in_celsius - residence_temperature_in_celsius)
            * np.exp(-(self.my_simulation_parameters.seconds_per_timestep) / time_constant_hds)
        )
        water_temperature_output_in_celsius = residence_temperature_in_celsius + (
            (self.state.water_output_temperature_in_celsius - residence_temperature_in_celsius)
            * np.exp(-(self.my_simulation_parameters.seconds_per_timestep) / time_constant_hds)
        )

        thermal_power_delivered_effective_in_watt = self.state.thermal_power_delivered_in_watt * np.exp(
            -(self.my_simulation_parameters.seconds_per_timestep) / time_constant_hds
        )

        return (
            water_temperature_input_in_celsius,
            water_temperature_output_in_celsius,
            thermal_power_delivered_effective_in_watt,
        )

    def determine_water_temperature_output_after_heat_exchange_with_building_and_effective_thermal_power(
        self,
        water_mass_flow_in_kg_per_second: float,
        water_temperature_input_in_celsius: float,
        theoretical_thermal_buiding_demand_in_watt: float,
        residence_temperature_in_celsius: float,
    ) -> Any:
        """Calculate cooled or heated water temperature after heat exchange between heat distribution system and building."""
        # Tout = Tin -  Q/(c * m)
        water_temperature_output_in_celsius = (
            water_temperature_input_in_celsius
            - theoretical_thermal_buiding_demand_in_watt
            / (
                water_mass_flow_in_kg_per_second
                * self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
            )
        )

        if theoretical_thermal_buiding_demand_in_watt > 0:
            # water in hds must be warmer than the building in order to exchange heat
            if water_temperature_input_in_celsius > residence_temperature_in_celsius:
                # prevent that water output temperature in hds gets colder than residence temperature in building when heating
                water_temperature_output_in_celsius = max(
                    water_temperature_output_in_celsius,
                    residence_temperature_in_celsius,
                )
                thermal_power_delivered_effective_in_watt = (
                    self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
                    * water_mass_flow_in_kg_per_second
                    * (water_temperature_input_in_celsius - water_temperature_output_in_celsius)
                )
            else:
                # water in hds is not warmer than the building, therefore heat exchange is not possible
                water_temperature_output_in_celsius = water_temperature_input_in_celsius
                thermal_power_delivered_effective_in_watt = 0

        elif theoretical_thermal_buiding_demand_in_watt < 0:
            # water in hds must be cooler than the building in order to cool building down
            if water_temperature_input_in_celsius < residence_temperature_in_celsius:
                # prevent that water output temperature in hds gets hotter than residence temperature in building when cooling
                water_temperature_output_in_celsius = min(
                    water_temperature_output_in_celsius,
                    residence_temperature_in_celsius,
                )
                thermal_power_delivered_effective_in_watt = (
                    self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
                    * water_mass_flow_in_kg_per_second
                    * (water_temperature_input_in_celsius - water_temperature_output_in_celsius)
                )
            else:
                # water in hds is not colder than building and therefore cooling is not possible
                water_temperature_output_in_celsius = water_temperature_input_in_celsius
                thermal_power_delivered_effective_in_watt = 0

        # in case no heating or cooling needed, water output is equal to water input
        elif theoretical_thermal_buiding_demand_in_watt == 0:
            water_temperature_output_in_celsius = water_temperature_input_in_celsius
            thermal_power_delivered_effective_in_watt = 0
        else:
            raise ValueError(
                f"Theoretical thermal demand has unacceptable value here {theoretical_thermal_buiding_demand_in_watt}."
            )
        return (
            water_temperature_output_in_celsius,
            thermal_power_delivered_effective_in_watt,
        )

    @staticmethod
    def get_cost_capex(
        config: HeatDistributionConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime."""
        # consider costs of changing heat distribution system to floor heating
        if config.heating_system == HeatDistributionSystemType.FLOORHEATING:
            component_type = lt.ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING
        elif config.heating_system == HeatDistributionSystemType.RADIATOR:
            component_type = lt.ComponentType.HEAT_DISTRIBUTION_SYSTEM_RADIATOR
        elif config.heating_system == HeatDistributionSystemType.LOW_TEMPERATURE_RADIATOR:
            component_type = lt.ComponentType.HEAT_DISTRIBUTION_SYSTEM_LOW_TEMPERATURE_RADIATOR
        else:
            raise ValueError(
                f"Unknown heating_system type {config.heating_system!r} in HeatDistributionConfig; "
                f"expected one of {list(HeatDistributionSystemType)}."
            )

        kpi_tag = KpiTagEnumClass.HEAT_DISTRIBUTION_SYSTEM
        unit = lt.Units.SQUARE_METER
        size_of_energy_system = concrete(config.absolute_conditioned_floor_area_in_m2)

        capex_cost_data_class = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
            simulation_parameters=simulation_parameters,
            component_type=component_type,
            unit=unit,
            size_of_energy_system=size_of_energy_system,
            config=config,
            kpi_tag=kpi_tag,
        )

        config = CapexComputationHelperFunctions.overwrite_config_values_with_new_capex_values(
            config=config, capex_cost_data_class=capex_cost_data_class
        )
        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List[cp.ComponentOutput],
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        # pylint: disable=unused-argument
        """Calculate OPEX costs, consisting of maintenance costs for Heat Distribution System."""
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=0,
            total_consumption_in_kwh=0,
            loadtype=lt.LoadTypes.ANY,
            kpi_tag=KpiTagEnumClass.HEAT_DISTRIBUTION_SYSTEM,
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List[cp.ComponentOutput],
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""

        thermal_output_energy_in_kilowatt_hour: Optional[float] = None
        mean_flow_temperature_in_celsius: Optional[float] = None
        mean_return_temperature_in_celsius: Optional[float] = None
        mean_temperature_difference_between_flow_and_return_in_celsius: Optional[float] = None
        min_flow_temperature_in_celsius: Optional[float] = None
        min_return_temperature_in_celsius: Optional[float] = None
        min_temperature_difference_between_flow_and_return_in_celsius: Optional[float] = None
        max_flow_temperature_in_celsius: Optional[float] = None
        max_return_temperature_in_celsius: Optional[float] = None
        max_temperature_difference_between_flow_and_return_in_celsius: Optional[float] = None
        flow_temperature_list_in_celsius: pd.Series = pd.Series([])
        return_temperature_list_in_celsius: pd.Series = pd.Series([])

        list_of_kpi_entries: List[KpiEntry] = []
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if (
                    output.field_name == self.ThermalPowerDelivered
                    and output.load_type == lt.LoadTypes.HEATING
                    and output.unit == lt.Units.WATT
                ):
                    # take only output values for heating
                    thermal_output_power_values_in_watt = postprocessing_results.iloc[:, index].loc[
                        postprocessing_results.iloc[:, index] > 0.0
                    ]
                    # get energy from power
                    thermal_output_energy_in_kilowatt_hour = KpiHelperClass.compute_total_energy_from_power_timeseries(
                        power_timeseries_in_watt=thermal_output_power_values_in_watt,
                        time_resolution_in_seconds=self.my_simulation_parameters.seconds_per_timestep,
                    )
                    thermal_output_energy_hds_entry = KpiEntry(
                        name="Thermal output energy of heat distribution system",
                        unit="kWh",
                        value=thermal_output_energy_in_kilowatt_hour,
                        tag=KpiTagEnumClass.HEAT_DISTRIBUTION_SYSTEM,
                        description=self.component_name,
                    )
                    list_of_kpi_entries.append(thermal_output_energy_hds_entry)

                elif output.field_name == self.WaterTemperatureInlet:
                    flow_temperature_list_in_celsius = postprocessing_results.iloc[:, index]
                elif output.field_name == self.WaterTemperatureOutput:
                    return_temperature_list_in_celsius = postprocessing_results.iloc[:, index]

        # get mean, max and min values of flow and return temperatures
        temperature_diff_flow_and_return_in_celsius = (
            flow_temperature_list_in_celsius - return_temperature_list_in_celsius
        )
        (
            mean_temperature_difference_between_flow_and_return_in_celsius,
            max_temperature_difference_between_flow_and_return_in_celsius,
            min_temperature_difference_between_flow_and_return_in_celsius,
        ) = KpiHelperClass.compute_mean_max_min_values(list_or_pandas_series=temperature_diff_flow_and_return_in_celsius)

        (
            mean_flow_temperature_in_celsius,
            max_flow_temperature_in_celsius,
            min_flow_temperature_in_celsius,
        ) = KpiHelperClass.compute_mean_max_min_values(list_or_pandas_series=flow_temperature_list_in_celsius)

        (
            mean_return_temperature_in_celsius,
            max_return_temperature_in_celsius,
            min_return_temperature_in_celsius,
        ) = KpiHelperClass.compute_mean_max_min_values(list_or_pandas_series=return_temperature_list_in_celsius)

        # make kpi entries and append to list
        mean_flow_temperature_hds_entry = KpiEntry(
            name="Mean flow temperature of heat distribution system",
            unit="°C",
            value=mean_flow_temperature_in_celsius,
            tag=KpiTagEnumClass.HEAT_DISTRIBUTION_SYSTEM,
            description=self.component_name,
        )
        list_of_kpi_entries.append(mean_flow_temperature_hds_entry)

        mean_return_temperature_hds_entry = KpiEntry(
            name="Mean return temperature of heat distribution system",
            unit="°C",
            value=mean_return_temperature_in_celsius,
            tag=KpiTagEnumClass.HEAT_DISTRIBUTION_SYSTEM,
            description=self.component_name,
        )
        list_of_kpi_entries.append(mean_return_temperature_hds_entry)

        mean_temperature_difference_hds_entry = KpiEntry(
            name="Mean temperature difference of heat distribution system",
            unit="°C",
            value=mean_temperature_difference_between_flow_and_return_in_celsius,
            tag=KpiTagEnumClass.HEAT_DISTRIBUTION_SYSTEM,
            description=self.component_name,
        )
        list_of_kpi_entries.append(mean_temperature_difference_hds_entry)

        max_flow_temperature_hds_entry = KpiEntry(
            name="Max flow temperature of heat distribution system",
            unit="°C",
            value=max_flow_temperature_in_celsius,
            tag=KpiTagEnumClass.HEAT_DISTRIBUTION_SYSTEM,
            description=self.component_name,
        )
        list_of_kpi_entries.append(max_flow_temperature_hds_entry)

        max_return_temperature_hds_entry = KpiEntry(
            name="Max return temperature of heat distribution system",
            unit="°C",
            value=max_return_temperature_in_celsius,
            tag=KpiTagEnumClass.HEAT_DISTRIBUTION_SYSTEM,
            description=self.component_name,
        )
        list_of_kpi_entries.append(max_return_temperature_hds_entry)

        max_temperature_difference_hds_entry = KpiEntry(
            name="Max temperature difference of heat distribution system",
            unit="°C",
            value=max_temperature_difference_between_flow_and_return_in_celsius,
            tag=KpiTagEnumClass.HEAT_DISTRIBUTION_SYSTEM,
            description=self.component_name,
        )
        list_of_kpi_entries.append(max_temperature_difference_hds_entry)

        min_flow_temperature_hds_entry = KpiEntry(
            name="Min flow temperature of heat distribution system",
            unit="°C",
            value=min_flow_temperature_in_celsius,
            tag=KpiTagEnumClass.HEAT_DISTRIBUTION_SYSTEM,
            description=self.component_name,
        )
        list_of_kpi_entries.append(min_flow_temperature_hds_entry)

        min_return_temperature_hds_entry = KpiEntry(
            name="Min return temperature of heat distribution system",
            unit="°C",
            value=min_return_temperature_in_celsius,
            tag=KpiTagEnumClass.HEAT_DISTRIBUTION_SYSTEM,
            description=self.component_name,
        )
        list_of_kpi_entries.append(min_return_temperature_hds_entry)

        min_temperature_difference_hds_entry = KpiEntry(
            name="Min temperature difference of heat distribution system",
            unit="°C",
            value=min_temperature_difference_between_flow_and_return_in_celsius,
            tag=KpiTagEnumClass.HEAT_DISTRIBUTION_SYSTEM,
            description=self.component_name,
        )
        list_of_kpi_entries.append(min_temperature_difference_hds_entry)

        return list_of_kpi_entries


@dataclass_json
@dataclass
class HeatDistributionControllerConfig(ConfigBase):
    """HeatDistribution Controller Config Class."""

    #: Sizing facts this config contributes (spec §8.4): the water mass flow it derives
    #: via HeatDistributionControllerInformation and the heat distribution system type —
    #: the two sibling facts the HeatDistributionConfig preset resolves from. Assigned
    #: below the class, next to the compute function.
    SIZING_CONTRIBUTIONS: ClassVar[Tuple[FactContribution, ...]] = ()

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return HeatDistributionController.get_full_classname()

    component_id: ComponentID
    heating_system: HeatDistributionSystemType
    set_heating_threshold_outside_temperature_in_celsius: float
    heating_reference_temperature_in_celsius: float
    set_heating_temperature_for_building_in_celsius: float
    set_cooling_temperature_for_building_in_celsius: float
    heating_load_of_building_in_watt: float
    specific_heating_load_of_building_in_watt_per_m2: Optional[float]

    @classmethod
    def get_default_heat_distribution_controller_config(
        cls,
        heating_load_of_building_in_watt: float,
        set_heating_temperature_for_building_in_celsius: float,
        set_cooling_temperature_for_building_in_celsius: float,
        set_heating_threshold_outside_temperature_in_celsius: float = 16.0,
        heating_reference_temperature_in_celsius: float = -7.0,
        heating_system: HeatDistributionSystemType = HeatDistributionSystemType.FLOORHEATING,
        component_id: Optional[ComponentID] = None,
    ) -> "HeatDistributionControllerConfig":
        """Gets a default HeatDistribution Controller."""

        if component_id is None:
            component_id = ComponentID(name="HeatDistributionController")
        return HeatDistributionControllerConfig(
            component_id=component_id,
            heating_system=heating_system,
            set_heating_threshold_outside_temperature_in_celsius=set_heating_threshold_outside_temperature_in_celsius,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
            set_heating_temperature_for_building_in_celsius=set_heating_temperature_for_building_in_celsius,
            set_cooling_temperature_for_building_in_celsius=set_cooling_temperature_for_building_in_celsius,
            heating_load_of_building_in_watt=round(heating_load_of_building_in_watt, 2),
            specific_heating_load_of_building_in_watt_per_m2=None,
        )

    @staticmethod
    def set_heating_threshold_temperature_based_on_building_efficiency(
        specific_heating_load_of_building_in_watt_per_m2: float,
        set_heating_threshold_outside_temperature_in_celsius: float = 16.0,
    ):
        """Set heating threshold outside temperature based on building efficiency."""
        # avoid that inefficient building cool out in summer time (in the mornings and evenings)
        if 50 < specific_heating_load_of_building_in_watt_per_m2 <= 80.0:
            set_heating_threshold_outside_temperature_in_celsius = 18.0
        elif 80 < specific_heating_load_of_building_in_watt_per_m2:
            set_heating_threshold_outside_temperature_in_celsius = 20.0
        else:
            pass
        return set_heating_threshold_outside_temperature_in_celsius

    @classmethod
    def get_config_based_on_building_efficiency(
        cls,
        heating_load_of_building_in_watt: float,
        set_heating_temperature_for_building_in_celsius: float,
        set_cooling_temperature_for_building_in_celsius: float,
        specific_heating_load_of_building_in_watt_per_m2: float,
        set_heating_threshold_outside_temperature_in_celsius: float = 16.0,
        heating_reference_temperature_in_celsius: float = -7.0,
        heating_system: HeatDistributionSystemType = HeatDistributionSystemType.FLOORHEATING,
        component_id: Optional[ComponentID] = None,
    ) -> "HeatDistributionControllerConfig":
        """Gets a default HeatDistribution Controller."""
        # avoid that inefficient building cool out in summer time (in the mornings and evenings)
        if component_id is None:
            component_id = ComponentID(name="HeatDistributionController")
        set_heating_threshold_outside_temperature_in_celsius = HeatDistributionControllerConfig.set_heating_threshold_temperature_based_on_building_efficiency(
            specific_heating_load_of_building_in_watt_per_m2=specific_heating_load_of_building_in_watt_per_m2,
            set_heating_threshold_outside_temperature_in_celsius=set_heating_threshold_outside_temperature_in_celsius,
        )

        return HeatDistributionControllerConfig(
            component_id=component_id,
            heating_system=heating_system,
            set_heating_threshold_outside_temperature_in_celsius=set_heating_threshold_outside_temperature_in_celsius,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
            set_heating_temperature_for_building_in_celsius=set_heating_temperature_for_building_in_celsius,
            set_cooling_temperature_for_building_in_celsius=set_cooling_temperature_for_building_in_celsius,
            heating_load_of_building_in_watt=round(heating_load_of_building_in_watt, 2),
            specific_heating_load_of_building_in_watt_per_m2=specific_heating_load_of_building_in_watt_per_m2,
        )


class HeatDistributionController(cp.Component):
    """Heat Distribution Controller.

    It takes data from the building, weather and water storage and sends signal to the heat distribution for
    activation or deactivation.

    """

    # Inputs
    TheoreticalThermalBuildingDemand = "TheoreticalThermalBuildingDemand"
    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"
    # Inputs -> energy management system
    BuildingTemperatureModifier = "BuildingTemperatureModifier"

    # Outputs
    State = "State"
    HeatingFlowTemperature = "HeatingFlowTemperature"
    HeatingReturnTemperature = "HeatingReturnTemperature"
    HeatingTemperatureDifference = "HeatingTemperatureDifference"

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: HeatDistributionControllerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.hsd_controller_config = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.state_controller: int = 0
        self.building_temperature_modifier: float = 0
        my_heat_distribution_controller_information = HeatDistributionControllerInformation(
            config=self.hsd_controller_config
        )

        self.build(
            set_heating_threshold_temperature_in_celsius=my_heat_distribution_controller_information.set_heating_threshold_temperature_in_celsius,
            heating_reference_temperature_in_celsius=my_heat_distribution_controller_information.heating_reference_temperature_in_celsius,
            heat_distribution_system_type=my_heat_distribution_controller_information.heat_distribution_system_type,
            max_flow_temperature_in_celsius=my_heat_distribution_controller_information.max_flow_temperature_in_celsius,
            max_return_temperature_in_celsius=my_heat_distribution_controller_information.max_return_temperature_in_celsius,
            min_flow_temperature_in_celsius=my_heat_distribution_controller_information.min_flow_temperature_in_celsius,
            min_return_temperature_in_celsius=my_heat_distribution_controller_information.min_return_temperature_in_celsius,
            factor_of_oversizing_of_heat_distribution_system=my_heat_distribution_controller_information.factor_of_oversizing_of_heat_distribution_system,
            exponent_factor_of_heating_distribution_system=my_heat_distribution_controller_information.exponent_factor_of_heating_distribution_system,
            set_room_temperature_for_building_in_celsius=my_heat_distribution_controller_information.set_room_temperature_for_building_in_celsius,
        )

        # Inputs
        self.theoretical_thermal_building_demand_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TheoreticalThermalBuildingDemand,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            True,
        )
        self.daily_avg_outside_temperature_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.DailyAverageOutsideTemperature,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.building_temperature_modifier_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.BuildingTemperatureModifier,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            mandatory=False,
        )
        # Outputs
        self.state_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.State,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description=f"here a description for {self.State} will follow.",
        )
        self.heating_flow_temperature_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.HeatingFlowTemperature,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.HeatingFlowTemperature} will follow.",
        )
        self.heating_return_temperature_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.HeatingReturnTemperature,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.HeatingReturnTemperature} will follow.",
        )
        self.heating_temperature_difference_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.HeatingTemperatureDifference,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.HeatingTemperatureDifference} will follow.",
        )

        self.controller_heat_distribution_mode: str = "off"
        self.previous_controller_heat_distribution_mode: str = "off"

        self.add_default_connections(self.get_default_connections_from_building())
        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(self.get_default_connections_from_energy_management_system())

    def get_default_connections_from_weather(
        self,
    ):
        """Get weather default connections."""

        connections = []
        weather_classname = Weather.get_classname()
        connections.append(
            cp.ComponentConnection(
                HeatDistributionController.DailyAverageOutsideTemperature,
                weather_classname,
                Weather.DailyAverageOutsideTemperatures,
            )
        )
        return connections

    def get_default_connections_from_building(
        self,
    ):
        """Get building default connections."""

        connections = []
        building_classname = Building.get_classname()
        connections.append(
            cp.ComponentConnection(
                HeatDistributionController.TheoreticalThermalBuildingDemand,
                building_classname,
                Building.TheoreticalThermalBuildingDemand,
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
                HeatDistributionController.BuildingTemperatureModifier,
                ems_classname,
                component_class.BuildingIndoorTemperatureModifier,
            )
        )
        return connections

    def build(
        self,
        set_heating_threshold_temperature_in_celsius: float,
        heating_reference_temperature_in_celsius: float,
        heat_distribution_system_type: HeatDistributionSystemType,
        max_flow_temperature_in_celsius: float,
        min_flow_temperature_in_celsius: float,
        max_return_temperature_in_celsius: float,
        min_return_temperature_in_celsius: float,
        factor_of_oversizing_of_heat_distribution_system: float,
        exponent_factor_of_heating_distribution_system: float,
        set_room_temperature_for_building_in_celsius: float,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        # Configuration
        self.set_heating_threshold_temperature_in_celsius = set_heating_threshold_temperature_in_celsius
        self.heating_reference_temperature_in_celsius = heating_reference_temperature_in_celsius
        self.heat_distribution_system_type = heat_distribution_system_type

        self.max_flow_temperature_in_celsius = max_flow_temperature_in_celsius
        self.min_flow_temperature_in_celsius = min_flow_temperature_in_celsius
        self.max_return_temperature_in_celsius = max_return_temperature_in_celsius
        self.min_return_temperature_in_celsius = min_return_temperature_in_celsius
        self.factor_of_oversizing_of_heat_distribution_system = factor_of_oversizing_of_heat_distribution_system
        self.exponent_factor_of_heating_distribution_system = exponent_factor_of_heating_distribution_system
        self.set_room_temperature_for_building_in_celsius = set_room_temperature_for_building_in_celsius

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_controller_heat_distribution_mode = self.controller_heat_distribution_mode

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.controller_heat_distribution_mode = self.controller_heat_distribution_mode

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(self) -> List[str]:
        """Write important variables to report."""
        lines = []
        lines.append("Heat Distribution Controller")
        return lines

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the heat distribution controller."""
        if force_convergence:
            pass
        else:
            # Retrieves inputs
            theoretical_thermal_building_demand_in_watt = stsv.get_input_value(
                self.theoretical_thermal_building_demand_channel
            )
            daily_avg_outside_temperature_in_celsius = stsv.get_input_value(
                self.daily_avg_outside_temperature_input_channel
            )
            self.building_temperature_modifier = stsv.get_input_value(self.building_temperature_modifier_channel)

            list_of_heating_distribution_system_flow_and_return_temperatures = (
                self.calc_heat_distribution_flow_and_return_temperatures(
                    daily_avg_outside_temperature_in_celsius=daily_avg_outside_temperature_in_celsius
                )
            )

            self.conditions_for_opening_or_shutting_heat_distribution(
                theoretical_thermal_building_demand_in_watt=theoretical_thermal_building_demand_in_watt,
            )

            # turning heat distributon system off when the average daily outside temperature is above a certain threshold
            summer_heating_mode = self.summer_heating_condition(
                daily_average_outside_temperature_in_celsius=daily_avg_outside_temperature_in_celsius,
                set_heating_threshold_temperature_in_celsius=self.hsd_controller_config.set_heating_threshold_outside_temperature_in_celsius,
            )

            if self.controller_heat_distribution_mode == "heating":
                if summer_heating_mode == "on":
                    self.state_controller = 1
                elif summer_heating_mode == "off":
                    self.state_controller = 0

            elif self.controller_heat_distribution_mode == "cooling":
                self.state_controller = -1

            elif self.controller_heat_distribution_mode == "off":
                self.state_controller = 0

            else:
                raise ValueError("unknown hds controller mode or summer mode or dew point protection mode.")

            stsv.set_output_value(self.state_channel, self.state_controller)
            stsv.set_output_value(
                self.heating_flow_temperature_channel,
                list_of_heating_distribution_system_flow_and_return_temperatures[0],
            )
            stsv.set_output_value(
                self.heating_return_temperature_channel,
                list_of_heating_distribution_system_flow_and_return_temperatures[1],
            )

            stsv.set_output_value(
                self.heating_temperature_difference_channel,
                list_of_heating_distribution_system_flow_and_return_temperatures[0]
                - list_of_heating_distribution_system_flow_and_return_temperatures[1],
            )

    def conditions_for_opening_or_shutting_heat_distribution(
        self,
        theoretical_thermal_building_demand_in_watt: float,
    ) -> None:
        """Set conditions for the valve in heat distribution."""

        if self.controller_heat_distribution_mode in ("cooling", "heating"):
            # no heat exchange with building if theres no demand
            if theoretical_thermal_building_demand_in_watt == 0:
                self.controller_heat_distribution_mode = "off"
                return
        elif self.controller_heat_distribution_mode == "off":
            # if heating or cooling is needed for building
            if theoretical_thermal_building_demand_in_watt > 0:
                self.controller_heat_distribution_mode = "heating"
            elif theoretical_thermal_building_demand_in_watt < 0:
                self.controller_heat_distribution_mode = "cooling"
                return

        else:
            raise ValueError("unknown hds controller mode.")

    def summer_heating_condition(
        self,
        daily_average_outside_temperature_in_celsius: float,
        set_heating_threshold_temperature_in_celsius: float,
    ) -> str:
        """Set conditions for the valve in heat distribution."""

        if daily_average_outside_temperature_in_celsius > set_heating_threshold_temperature_in_celsius:
            heating_mode = "off"

        elif daily_average_outside_temperature_in_celsius < set_heating_threshold_temperature_in_celsius:
            heating_mode = "on"

        else:
            raise ValueError(
                f"daily average temperature {daily_average_outside_temperature_in_celsius}°C"
                f"or heating threshold temperature {set_heating_threshold_temperature_in_celsius}°C is invalid."
            )

        return heating_mode

    def calc_heat_distribution_flow_and_return_temperatures(
        self, daily_avg_outside_temperature_in_celsius: float
    ) -> List[float]:
        """Calculate the heat distribution flow and return temperature as a function of the moving average daily mean outside temperature.

        Calculations are based on DIN/TS 18599-12: 2021-04, p.170, Eq. 127,128

        Returns
        -------
        list with heating flow and heating return temperature

        """
        # increase set_heating_temperature when connected to EnergyManagementSystem and surplus electricity available.
        # only used for heating case
        set_room_temperature_for_building_modified_in_celsius = (
            self.set_room_temperature_for_building_in_celsius + self.building_temperature_modifier
        )
        min_flow_temperature_modified_in_celsius = (
            self.min_flow_temperature_in_celsius + self.building_temperature_modifier
        )
        min_return_temperature_modified_in_celsius = (
            self.min_return_temperature_in_celsius + self.building_temperature_modifier
        )

        # cooling case, daily avg temperature is higher than set indoor temperature.
        # flow and return temperatures can not be lower than set indoor temperature (because number would be complex)
        if self.set_room_temperature_for_building_in_celsius < daily_avg_outside_temperature_in_celsius:
            # prevent that flow and return temperatures get colder than 19 °C because this could cause condensation of the indoor air on the heating system
            # https://suissetec.ch/files/PDFs/Merkblaetter/Heizung/Deutsch/2021_11_MB_Kuehlung_mit_Fussbodenheizung_DE_Web.pdf

            flow_temperature_in_celsius = max(self.min_flow_temperature_in_celsius, 19.0)
            return_temperature_in_celsius = max(self.min_return_temperature_in_celsius, 19.0)

        else:
            # heating case, daily avg outside temperature is lower than indoor temperature
            flow_temperature_in_celsius = float(
                min_flow_temperature_modified_in_celsius
                + (
                    (1 / self.factor_of_oversizing_of_heat_distribution_system)
                    * (
                        (
                            set_room_temperature_for_building_modified_in_celsius
                            - daily_avg_outside_temperature_in_celsius
                        )
                        / (
                            set_room_temperature_for_building_modified_in_celsius
                            - self.heating_reference_temperature_in_celsius
                        )
                    )
                )
                ** (1 / self.exponent_factor_of_heating_distribution_system)
                * (self.max_flow_temperature_in_celsius - min_flow_temperature_modified_in_celsius)
            )
            return_temperature_in_celsius = float(
                min_return_temperature_modified_in_celsius
                + (
                    (1 / self.factor_of_oversizing_of_heat_distribution_system)
                    * (
                        (
                            set_room_temperature_for_building_modified_in_celsius
                            - daily_avg_outside_temperature_in_celsius
                        )
                        / (
                            set_room_temperature_for_building_modified_in_celsius
                            - self.heating_reference_temperature_in_celsius
                        )
                    )
                )
                ** (1 / self.exponent_factor_of_heating_distribution_system)
                * (self.max_return_temperature_in_celsius - min_return_temperature_modified_in_celsius)
            )

        list_of_heating_flow_and_return_temperature_in_celsius = [
            flow_temperature_in_celsius,
            return_temperature_in_celsius,
        ]

        return list_of_heating_flow_and_return_temperature_in_celsius

    def get_cost_opex(
        self,
        all_outputs: List[cp.ComponentOutput],
        postprocessing_results: pd.DataFrame,
    ) -> cp.OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        opex_cost_data_class = cp.OpexCostDataClass.get_default_opex_cost_data_class()
        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(
        config: HeatDistributionControllerConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        capex_cost_data_class = CapexCostDataClass.get_default_capex_cost_data_class()
        return capex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List[cp.ComponentOutput],
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []


@dataclass_json
@dataclass
class HeatDistributionControllerInformation:
    """Class for collecting important heat distribution parameters to pass to other components."""

    def __init__(self, config: HeatDistributionControllerConfig) -> None:
        """Initialize the class."""

        self.hds_controller_config: HeatDistributionControllerConfig = config

        self.build(
            set_heating_threshold_temperature_in_celsius=self.hds_controller_config.set_heating_threshold_outside_temperature_in_celsius,
            heating_reference_temperature_in_celsius=self.hds_controller_config.heating_reference_temperature_in_celsius,
            heat_distribution_system_type=self.hds_controller_config.heating_system,
            set_heating_temperature_for_building_in_celsius=self.hds_controller_config.set_heating_temperature_for_building_in_celsius,
            set_cooling_temperature_for_building_in_celsius=self.hds_controller_config.set_cooling_temperature_for_building_in_celsius,
        )
        self.prepare_heating_distribution_temperature_calculation(
            set_room_temperature_for_building_in_celsius=self.hds_controller_config.set_heating_temperature_for_building_in_celsius,
            factor_of_oversizing_of_heat_distribution_system=1.0,
        )

        self.water_mass_flow_rate_in_kg_per_second: float = self.calculate_heating_distribution_system_water_mass_flow_rate(
            max_thermal_building_demand_in_watt=self.hds_controller_config.heating_load_of_building_in_watt
        )

    def build(
        self,
        set_heating_threshold_temperature_in_celsius: float,
        heating_reference_temperature_in_celsius: float,
        heat_distribution_system_type: HeatDistributionSystemType,
        set_heating_temperature_for_building_in_celsius: float,
        set_cooling_temperature_for_building_in_celsius: float,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        # Configuration
        self.set_heating_threshold_temperature_in_celsius: float = set_heating_threshold_temperature_in_celsius
        self.heating_reference_temperature_in_celsius: float = heating_reference_temperature_in_celsius
        self.heat_distribution_system_type: HeatDistributionSystemType = heat_distribution_system_type

        self.set_heating_temperature_for_building_in_celsius: float = set_heating_temperature_for_building_in_celsius
        self.set_cooling_temperature_for_building_in_celsius: float = set_cooling_temperature_for_building_in_celsius

    def prepare_heating_distribution_temperature_calculation(
        self,
        set_room_temperature_for_building_in_celsius: float = 20.0,
        factor_of_oversizing_of_heat_distribution_system: float = 1.0,
    ) -> None:
        """Function to set several input parameters for functions regarding the heating system.

        This function is taken from the HeatingSystem class of hplib and slightly adapted here.
        """

        self.set_room_temperature_for_building_in_celsius: float = set_room_temperature_for_building_in_celsius
        if self.heat_distribution_system_type == HeatDistributionSystemType.FLOORHEATING:
            list_of_maximum_flow_and_return_temperatures_in_celsius = [35, 28]
            exponent_factor_of_heating_distribution_system = 1.1

        elif self.heat_distribution_system_type == HeatDistributionSystemType.RADIATOR:
            list_of_maximum_flow_and_return_temperatures_in_celsius = [70, 55]
            exponent_factor_of_heating_distribution_system = 1.3

        elif self.heat_distribution_system_type == HeatDistributionSystemType.LOW_TEMPERATURE_RADIATOR:
            list_of_maximum_flow_and_return_temperatures_in_celsius = [55, 45]
            exponent_factor_of_heating_distribution_system = 1.2
        else:
            raise ValueError(
                "Heating System Type not defined here. Check your heat distribution controller config or your Heating System Type class."
            )

        self.max_flow_temperature_in_celsius: float = list_of_maximum_flow_and_return_temperatures_in_celsius[0]
        self.min_flow_temperature_in_celsius: float = set_room_temperature_for_building_in_celsius
        self.max_return_temperature_in_celsius: float = list_of_maximum_flow_and_return_temperatures_in_celsius[1]
        self.min_return_temperature_in_celsius: float = set_room_temperature_for_building_in_celsius
        self.factor_of_oversizing_of_heat_distribution_system: float = factor_of_oversizing_of_heat_distribution_system
        self.exponent_factor_of_heating_distribution_system: float = exponent_factor_of_heating_distribution_system

        self.temperature_difference_between_flow_and_return_in_celsius: float = (
            self.max_flow_temperature_in_celsius - self.max_return_temperature_in_celsius
        )

    def calculate_heating_distribution_system_water_mass_flow_rate(
        self,
        max_thermal_building_demand_in_watt: float,
    ) -> float:
        """Calculate water mass flow between heating distribution system and hot water storage."""
        specific_heat_capacity_of_water_in_joule_per_kg_per_celsius = PhysicsConfig.get_properties_for_energy_carrier(
            energy_carrier=lt.LoadTypes.WATER
        ).specific_heat_capacity_in_joule_per_kg_per_kelvin

        heating_distribution_system_water_mass_flow_in_kg_per_second = max_thermal_building_demand_in_watt / (
            specific_heat_capacity_of_water_in_joule_per_kg_per_celsius
            * self.temperature_difference_between_flow_and_return_in_celsius
        )
        return heating_distribution_system_water_mass_flow_in_kg_per_second


def _hds_controller_sizing_facts(
    config: HeatDistributionControllerConfig, ctx: SizingContext
) -> dict:
    """Computes the heat-distribution sizing facts from the controller config (spec §8.4).

    Uses the same HeatDistributionControllerInformation derivation the setups call today,
    so engine-resolved values are identical to the hand-threaded ones. The context is
    unused: the controller config already carries its building-derived parameters.
    """
    del ctx
    information = HeatDistributionControllerInformation(config=config)
    return {
        "water_mass_flow_rate_in_kg_per_second": information.water_mass_flow_rate_in_kg_per_second,
        "heat_distribution_system_type": config.heating_system,
    }


HeatDistributionControllerConfig.SIZING_CONTRIBUTIONS = (
    FactContribution(
        facts=("water_mass_flow_rate_in_kg_per_second", "heat_distribution_system_type"),
        compute=_hds_controller_sizing_facts,
    ),
)
