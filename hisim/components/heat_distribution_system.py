"""Heat Distribution Module."""

import importlib
from enum import Enum, unique
from typing import Any, ClassVar, List, Optional, Tuple, assert_never
from dataclasses import dataclass
from dataclasses_json import dataclass_json

import pandas as pd
import numpy as np

import hisim.component as cp
from hisim.part_load import PartLoadCommand, StartOfStepPartLoadControl
from hisim.components.building import Building

# HeatDistributionSystemType and EmitterDesign live in heating_curve, which this module imports; their old import
# path through this module stays valid for the setups and tests that use it, but new code imports them from there.
from hisim.components.heating_curve import EmitterDesign, HeatDistributionSystemType, HeatingCurveController
from hisim.simulationparameters import SimulationParameters
from hisim.components.configuration import PhysicsConfig
from hisim import loadtypes as lt
from hisim import utils
from hisim.component import OpexCostDataClass, CapexCostDataClass
from hisim.config import (
    ComponentID,
    ConfigBase,
    DisplayConfig,
    FactContribution,
    Sizable,
    Size,
    SizingContext,
    concrete,
    preset,
    sized_field,
)
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiHelperClass, KpiTagEnumClass
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions
from hisim.economics.facts import CostRelevance


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
    """Configuration of the HeatDistribution class, the water circuit between generator and rooms.

    Every value it needs is a property of the building it serves — the emitter type, the mass
    flow the circuit carries and the floor area it heats — so all three fields are sizable and
    :meth:`preset_building_derived` pins none of them.
    """

    MAIN_CLASS = "hisim.components.heat_distribution_system.HeatDistribution"

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

    @preset
    @classmethod
    def preset_building_derived(cls, name: str) -> "HeatDistributionConfig":
        """The one heat distribution system, sized entirely to the building it serves."""
        return cls(component_id=ComponentID(name=name))


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


class HeatDistribution(cp.PhysicsComponent):
    """Heat Distribution System.

    It simulates the heat exchange between heat generator and building.

    """

    cost_relevance = CostRelevance.PRICED

    # Inputs
    State = "State"
    WaterTemperatureInput = "WaterTemperatureInput"
    TheoreticalThermalBuildingDemand = "TheoreticalThermalBuildingDemand"
    ResidenceTemperatureIndoorAir = "ResidenceTemperatureIndoorAir"
    WaterMassFlowInput = "WaterMassFlowInput"
    #: The fraction of the step the circuit runs, commanded by the heat distribution controller.
    PartLoadRatio = "PartLoadRatio"

    # Outputs
    WaterTemperatureInlet = "WaterTemperatureInlet"
    WaterTemperatureOutput = "WaterTemperatureOutput"
    WaterTemperatureDifference = "WaterTemperatureDifference"
    ThermalPowerDelivered = "ThermalPowerDelivered"
    WaterMassFlowHDS = "WaterMassFlowHDS"
    #: The fraction of the step the circuit ran, reported back to the controller.
    PartLoadRatioRun = "PartLoadRatioRun"

    #: The positions in which the heat distribution system pumps its own design flow, because the component at the
    #: other end of its circuit (a buffer, or a district-heating substation without a pump) moves no water itself.
    POSITIONS_PUMPING_THE_DESIGN_FLOW: ClassVar[Tuple[PositionHotWaterStorageInSystemSetup, ...]] = (
        PositionHotWaterStorageInSystemSetup.PARALLEL,
        PositionHotWaterStorageInSystemSetup.NO_STORAGE_MASS_FLOW_FIX,
    )

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
        #: Whether the circuit pumps its own design flow; otherwise it reads the flow of its heat generator.
        self.pumps_its_design_flow: bool = (
            self.position_hot_water_storage_in_system in self.POSITIONS_PUMPING_THE_DESIGN_FLOW
        )
        self.refuse_a_design_flow_that_moves_no_water()

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
        if not self.pumps_its_design_flow:
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

        self.part_load_command = PartLoadCommand(
            self,
            input_name=self.PartLoadRatio,
            ratio_run_output_name=self.PartLoadRatioRun,
            device_description="the heat distribution system's circuit",
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
        connections.append(
            cp.ComponentConnection(
                HeatDistribution.PartLoadRatio,
                heat_distribution_controller_classname,
                HeatDistributionController.PartLoadRatio,
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

        # the room temperature at the start of the step: a clamp on the same pass's room temperature and the heat
        # delivered into the room can alternate from pass to pass
        connections.append(
            cp.ComponentConnection(
                HeatDistribution.ResidenceTemperatureIndoorAir,
                building_classname,
                Building.TemperatureIndoorAirAtStartOfStepInCelsius,
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
        """Simulate the heat distribution system.

        Where the system pumps its own design flow (a buffer in front of it, or a district-heating substation without
        a pump), the pump stops while the controller's valve is shut: the circuit then moves no water, carries no heat
        and returns its inlet temperature unchanged (:meth:`pump_stopped_by_the_valve`). Before, it went on pumping the
        design flow with the valve shut and returned the inlet cooled by a hundredth of its difference to the room,
        so the buffer booked heat into a circuit that delivered none to the building.

        Behind a buffer (``PARALLEL``) the circuit publishes this step's heat and temperatures: the buffer sends its
        start-of-step temperature as the supply, the circuit returns it less what the building takes, and both ends
        book ``m c (T_sup - T_ret)`` from the same values. Without a buffer the outputs still lag one step. The
        return is clamped at the room temperature the building reports at the start of the step: while heating it does
        not fall below it, while cooling it does not rise above it. A supply on the wrong side of that temperature, for
        example 16 °C while heating a room at 20 °C, returns unchanged and delivers no heat. Behind a buffer the
        circuit runs the fraction of the step its controller commands (``PartLoadRatio``): it publishes that fraction
        of its flow and of its heat, and reports the fraction it ran (``PartLoadRatioRun``).
        """

        # Get inputs ------------------------------------------------------------------------------------------------------------
        state_controller = stsv.get_input_value(self.state_channel)
        theoretical_thermal_building_demand_in_watt = stsv.get_input_value(
            self.theoretical_thermal_building_demand_channel
        )
        residence_temperature_input_in_celsius = stsv.get_input_value(self.residence_temperature_input_channel)

        pump_stands = self.pumps_its_design_flow and self.pump_stopped_by_the_valve(state_controller)
        if pump_stands:
            water_mass_flow_rate_in_kg_per_second = 0.0
        elif self.pumps_its_design_flow:
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

        if pump_stands:
            # the valve is shut and the pump stands: no water moves, no heat is exchanged
            water_temperature_input_in_celsius = stsv.get_input_value(self.water_temperature_input_channel)
            water_temperature_output_in_celsius = water_temperature_input_in_celsius
            thermal_power_delivered_in_watt = 0.0
        elif water_mass_flow_rate_in_kg_per_second == 0:
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

        ratio_run = 1.0 if water_mass_flow_rate_in_kg_per_second > 0 else 0.0
        if (
            self.position_hot_water_storage_in_system == PositionHotWaterStorageInSystemSetup.PARALLEL
            and water_mass_flow_rate_in_kg_per_second > 0
        ):
            # the circuit runs the fraction of the step its controller commands: the averaged flow and its heat
            ratio_run = self.part_load_command.ratio(stsv)
            water_mass_flow_rate_in_kg_per_second *= ratio_run
            thermal_power_delivered_in_watt *= ratio_run
        self.part_load_command.publish_ratio_run(stsv, ratio_run)

        # Set outputs -----------------------------------------------------------------------------------------------------------
        if self.position_hot_water_storage_in_system == PositionHotWaterStorageInSystemSetup.PARALLEL:
            # behind a buffer the circuit's heat is this step's, booked on the buffer's start temperature
            published = HeatDistributionSystemState(
                water_output_temperature_in_celsius=water_temperature_output_in_celsius,
                water_input_temperature_in_celsius=water_temperature_input_in_celsius,
                thermal_power_delivered_in_watt=thermal_power_delivered_in_watt,
            )
        else:
            # without a buffer the outputs still lag one step
            published = self.state
        stsv.set_output_value(self.water_temperature_inlet_channel, published.water_input_temperature_in_celsius)
        stsv.set_output_value(self.water_temperature_outlet_channel, published.water_output_temperature_in_celsius)
        stsv.set_output_value(
            self.water_temperature_difference_channel,
            published.water_input_temperature_in_celsius - published.water_output_temperature_in_celsius,
        )
        stsv.set_output_value(self.thermal_power_delivered_channel, published.thermal_power_delivered_in_watt)
        stsv.set_output_value(
            self.water_mass_flow_channel,
            water_mass_flow_rate_in_kg_per_second,
        )

        # write values to state
        self.state.water_output_temperature_in_celsius = water_temperature_output_in_celsius
        self.state.water_input_temperature_in_celsius = water_temperature_input_in_celsius
        self.state.thermal_power_delivered_in_watt = thermal_power_delivered_in_watt

    @staticmethod
    def pump_stopped_by_the_valve(valve_state: float) -> bool:
        """Return whether the controller's valve state stops the circuit's own pump.

        The heat distribution controller publishes 1 for a valve open for heating, -1 for one open for cooling and
        0 for a shut valve (also outside the heating season). A pump that moves the design flow stands while the
        valve is shut. For example, 0 stops the pump and 1 and -1 keep it running.

        Args:
            valve_state: The controller's ``State`` output.

        Returns:
            True if the valve is shut.
        """
        return valve_state == 0

    def refuse_a_design_flow_that_moves_no_water(self) -> None:
        """Refuse a circuit that pumps its own design flow when that flow is not above 0 kg/s.

        Where the system pumps its own design flow (``PARALLEL`` or ``NO_STORAGE_MASS_FLOW_FIX``), that flow is the
        only water it moves, so a design flow of 0 kg/s would deliver no heat to the building at any valve state. For
        example, a design flow sized from a very small heating load and rounded to two decimals, 0.0049 kg/s, becomes
        0.0 kg/s and is refused here. Where the heat generator moves the water, the design flow is not used and is not
        checked.

        Raises:
            ValueError: If the system pumps its own design flow and the flow is not above 0 kg/s.
        """
        design_flow_in_kg_per_second = self.heating_distribution_system_water_mass_flow_rate_in_kg_per_second
        if self.pumps_its_design_flow and design_flow_in_kg_per_second <= 0:
            raise ValueError(
                f"{self.component_name}: water_mass_flow_rate_in_kg_per_second is {design_flow_in_kg_per_second} "
                f"kg/s, but at position {self.position_hot_water_storage_in_system.value} the heat distribution "
                "system pumps this design flow itself, so it must be above 0 kg/s; a circuit without flow delivers "
                "no heat to the building."
            )

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
        ) = KpiHelperClass.compute_mean_max_min_values(
            list_or_pandas_series=temperature_diff_flow_and_return_in_celsius
        )

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
    """Configuration of the heat-distribution controller: the emitter of the water circuit and the load it is sized for.

    The controller switches the circuit's valve; what it contributes to the components around it at configuration
    time is the emitter type and the water mass flow the circuit carries at the building's heating load. The emitter
    is the author's choice between floor heating and radiators, the heating load a property of the building, so
    :meth:`preset_building_derived` pins nothing.
    """

    MAIN_CLASS = "hisim.components.heat_distribution_system.HeatDistributionController"

    component_id: ComponentID
    #: Which emitter the water circuit feeds; an author choice, contributed onwards as a fact.
    heating_system: HeatDistributionSystemType = HeatDistributionSystemType.FLOORHEATING
    heating_load_of_building_in_watt: Sizable[float] = sized_field(rule=Size.HEATING_LOAD_IN_WATT.rounded(2))

    @staticmethod
    def sizing_facts(config: "HeatDistributionControllerConfig", ctx: SizingContext) -> dict:
        """Return the facts the emitter circuit decides for the components around it: the mass flow and the emitter.

        The water mass flow is the one :class:`HeatDistributionControllerInformation` derives, the flow that carries
        the building's heating load at the emitter's design temperature difference.

        Args:
            config: This controller's configuration, fully resolved.
            ctx: The sizing context; unused, the configuration carries the heating load itself.

        Returns:
            The two facts named in :attr:`SIZING_CONTRIBUTIONS`.
        """
        del ctx
        information = HeatDistributionControllerInformation(config=config)
        return {
            "water_mass_flow_rate_in_kg_per_second": information.water_mass_flow_rate_in_kg_per_second,
            "heat_distribution_system_type": config.heating_system,
        }

    #: Sizing facts this config contributes: the water mass flow it derives via
    #: :class:`HeatDistributionControllerInformation`, and the emitter type.
    SIZING_CONTRIBUTIONS: ClassVar[Tuple[FactContribution, ...]] = (
        FactContribution(
            facts=(
                "water_mass_flow_rate_in_kg_per_second",
                "heat_distribution_system_type",
            ),
            compute=sizing_facts,
        ),
    )

    @preset
    @classmethod
    def preset_building_derived(cls, name: str) -> "HeatDistributionControllerConfig":
        """The one heat distribution controller, derived entirely from the building it serves.

        The heating load is a property of the building, so the preset pins nothing but the emitter type, which is the
        author's choice between floor heating and radiators and is a one-line override in the file.
        """
        return cls(component_id=ComponentID(name=name))


@unique
class HeatDistributionValveMode(str, Enum):
    """The mode of the heat-distribution valve: closed, open for heating, or open for cooling."""

    OFF = "off"
    HEATING = "heating"
    COOLING = "cooling"


@dataclass(frozen=True)
class HeatDistributionControllerState:
    """The heat-distribution controller's state from one step to the next: the mode of its valve.

    ``i_simulate`` reads the mode the last step converged to and writes this pass's mode as the end-of-step state;
    ``i_save_state`` makes the converged pass's mode the next step's start, so every pass of a step starts from the
    same mode.
    """

    valve_mode: HeatDistributionValveMode


class HeatDistributionController(cp.L1ControllerComponent):
    """L1 controller of the heat distribution: it opens the circuit's valve for heating or cooling, or closes it.

    The valve follows the building's theoretical demand (:meth:`next_valve_mode`): a closed valve opens for heating
    on a positive demand and for cooling on a negative one, and an open valve closes when the demand is zero. A
    valve open for heating passes heat only in the heating season, which the heating curve
    (:class:`hisim.components.heating_curve.HeatingCurveController`) decides. The controller publishes ``State``:
    1 for heating, -1 for cooling, and 0 for a closed valve or for a valve open for heating outside the heating season.

    Behind a space-heating buffer, above the part-load threshold, the controller also sets the fraction of the step the
    circuit runs while it heats (``PartLoadRatio``): the largest fraction that keeps the buffer's end temperature at or
    above the room's start-of-step temperature (:meth:`hisim.part_load.StartOfStepPartLoadRule.lowering_ratio`). Above
    the threshold the buffer is a start-of-step node: it is booked on its start temperature and does not see its own
    fall within the step, so a whole step of the building's demand could draw it far below the room. Without a
    buffer, at and below the threshold (where the buffer is an iterated mixed node that sees its own fall), and while
    it cools, the circuit runs the whole step.
    """

    cost_relevance = CostRelevance.FREE_OF_COST

    # Inputs
    TheoreticalThermalBuildingDemand = "TheoreticalThermalBuildingDemand"
    HeatingSeason = "HeatingSeason"
    #: The room's temperature at the start of the step, the floor the buffer may not end below.
    TemperatureIndoorAirAtStartOfStepInCelsius = "TemperatureIndoorAirAtStartOfStepInCelsius"
    #: The buffer's end-of-step temperature, and its end temperature without the distribution circuit.
    WaterTemperatureAtEndOfStepFromWaterStorageInCelsius = "WaterTemperatureAtEndOfStepFromWaterStorageInCelsius"
    WaterTemperatureAtEndOfStepWithoutHeatDistributionFromWaterStorageInCelsius = (
        "WaterTemperatureAtEndOfStepWithoutHeatDistributionFromWaterStorageInCelsius"
    )
    #: The fraction of the step the circuit ran, as the heat distribution system reports it.
    PartLoadRatioRun = "PartLoadRatioRun"

    # Outputs
    State = "State"
    #: The fraction of the step the circuit runs.
    PartLoadRatio = "PartLoadRatio"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: HeatDistributionControllerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct the controller and declare its ports."""
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
        # Inputs
        self.theoretical_thermal_building_demand_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TheoreticalThermalBuildingDemand,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            True,
        )
        self.heating_season_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HeatingSeason,
            lt.LoadTypes.ANY,
            lt.Units.BINARY,
            True,
        )
        self.room_temperature_at_start_of_step_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureIndoorAirAtStartOfStepInCelsius,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            False,
        )
        self.part_load = StartOfStepPartLoadControl(
            self,
            end_temperature_input_name=self.WaterTemperatureAtEndOfStepFromWaterStorageInCelsius,
            end_temperature_without_circuit_input_name=(
                self.WaterTemperatureAtEndOfStepWithoutHeatDistributionFromWaterStorageInCelsius
            ),
            ratio_run_input_name=self.PartLoadRatioRun,
            ratio_output_name=self.PartLoadRatio,
            device_description="the heat distribution system's circuit",
        )
        # Outputs
        self.state_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.State,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description=(
                "The valve: 1 open for heating, -1 open for cooling, 0 closed or open for heating outside the heating "
                "season."
            ),
            aggregation=lt.OutputAggregation.NONE,
        )

        #: The valve's mode at the start of the step, and the mode the latest free pass of the step decided.
        self.state = HeatDistributionControllerState(valve_mode=HeatDistributionValveMode.OFF)
        self.end_of_step_state = self.state

        self.add_default_connections(self.get_default_connections_from_building())
        self.add_default_connections(self.get_default_connections_from_heating_curve())
        self.add_default_connections(self.get_default_connections_from_simple_hot_water_storage())
        self.add_default_connections(self.get_default_connections_from_heat_distribution())

    def get_default_connections_from_building(
        self,
    ):
        """Get building default connections: the theoretical demand and the room's start-of-step temperature."""

        connections = []
        building_classname = Building.get_classname()
        connections.append(
            cp.ComponentConnection(
                HeatDistributionController.TheoreticalThermalBuildingDemand,
                building_classname,
                Building.TheoreticalThermalBuildingDemand,
            )
        )
        connections.append(
            cp.ComponentConnection(
                HeatDistributionController.TemperatureIndoorAirAtStartOfStepInCelsius,
                building_classname,
                Building.TemperatureIndoorAirAtStartOfStepInCelsius,
            )
        )
        return connections

    @staticmethod
    def get_default_connections_from_simple_hot_water_storage() -> List[cp.ComponentConnection]:
        """Return the connections of the buffer's end temperatures the circuit's fraction is set from.

        Returns:
            The two connections.
        """
        # imported here: the storage module imports this one
        storage_module = importlib.import_module("hisim.components.simple_water_storage")
        storage_class = getattr(storage_module, "SimpleHotWaterStorage")
        return [
            cp.ComponentConnection(
                HeatDistributionController.WaterTemperatureAtEndOfStepFromWaterStorageInCelsius,
                storage_class.get_classname(),
                storage_class.WaterTemperatureAtEndOfStepInCelsius,
            ),
            cp.ComponentConnection(
                HeatDistributionController.WaterTemperatureAtEndOfStepWithoutHeatDistributionFromWaterStorageInCelsius,
                storage_class.get_classname(),
                storage_class.WaterTemperatureAtEndOfStepWithoutHeatDistributionInCelsius,
            ),
        ]

    @staticmethod
    def get_default_connections_from_heat_distribution() -> List[cp.ComponentConnection]:
        """Return the connection of the fraction of the step the circuit reports it ran.

        Returns:
            The one connection.
        """
        return [
            cp.ComponentConnection(
                HeatDistributionController.PartLoadRatioRun,
                HeatDistribution.get_classname(),
                HeatDistribution.PartLoadRatioRun,
            )
        ]

    @staticmethod
    def get_default_connections_from_heating_curve() -> List[cp.ComponentConnection]:
        """Return the connection of the heating season from the heating curve.

        Returns:
            The one connection.
        """
        return [
            cp.ComponentConnection(
                HeatDistributionController.HeatingSeason,
                HeatingCurveController.get_classname(),
                HeatingCurveController.HeatingSeason,
            )
        ]

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Make the valve mode the converged pass decided the next step's start."""
        self.state = self.end_of_step_state

    def i_restore_state(self) -> None:
        """Restore nothing: a pass reads only the start-of-step state, which no pass changes.

        The end-of-step state is not reset either. A free pass overwrites it; under forced convergence the controller
        is frozen, so the step ends with the mode of its last free pass, the mode its frozen ``State`` output shows.
        """

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(self) -> List[str]:
        """Write important variables to report."""
        lines = []
        lines.append("Heat Distribution Controller")
        return lines

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Set the valve from the building's demand and the heating season, and publish its state and the fraction.

        Under ``force_convergence`` the state keeps the value of the last free pass, and the fraction is still set by
        the floor rule from that state: the floor is a limit of the water, not a decision that could cycle, and with
        the other controllers frozen the rule settles in one pass. Holding the fraction the circuit last ran instead
        let a forced step drain the buffer far below the room.
        """
        if force_convergence:
            self.publish_fraction(stsv, int(stsv.values[self.state_channel.global_index]))
            return
        theoretical_thermal_building_demand_in_watt = stsv.get_input_value(
            self.theoretical_thermal_building_demand_channel
        )
        is_heating_season = stsv.get_input_value(self.heating_season_channel) > 0.5
        valve_mode = self.next_valve_mode(self.state.valve_mode, theoretical_thermal_building_demand_in_watt)
        self.end_of_step_state = HeatDistributionControllerState(valve_mode=valve_mode)
        state = self.valve_state(valve_mode, is_heating_season=is_heating_season)
        stsv.set_output_value(self.state_channel, state)
        self.publish_fraction(stsv, state)

    def publish_fraction(self, stsv: cp.SingleTimeStepValues, state: int) -> None:
        """Publish the fraction of the step the circuit runs, for the valve state of this pass.

        Heating (1): the largest fraction that keeps the buffer at or above the room's start-of-step temperature.
        Cooling (-1): the whole step, since a cooling circuit raises the buffer. Shut (0): nothing.

        Args:
            stsv: The step's values.
            state: The valve state: 1 heating, -1 cooling, 0 shut.
        """
        if state == -1:
            stsv.set_output_value(self.part_load.ratio_channel, 1.0)
            return
        self.part_load.publish_lowering(
            stsv,
            device_runs=state == 1,
            floor_temperature_in_celsius=stsv.get_input_value(self.room_temperature_at_start_of_step_channel),
        )

    @staticmethod
    def next_valve_mode(
        mode: HeatDistributionValveMode, theoretical_thermal_building_demand_in_watt: float
    ) -> HeatDistributionValveMode:
        """Return the valve's mode for this pass, from its mode and the building's theoretical demand.

        A closed valve opens for heating on a positive demand and for cooling on a negative one; an open valve closes
        when the demand is exactly zero and otherwise stays in its mode. For example, a valve open for heating stays
        open on a demand of 300 W and closes on 0 W.

        Args:
            mode: The valve's mode before this pass.
            theoretical_thermal_building_demand_in_watt: The building's theoretical heating demand, in W; negative
                for a cooling demand.

        Returns:
            The valve's mode.
        """
        match mode:
            case HeatDistributionValveMode.HEATING | HeatDistributionValveMode.COOLING:
                if theoretical_thermal_building_demand_in_watt == 0:
                    return HeatDistributionValveMode.OFF
                return mode
            case HeatDistributionValveMode.OFF:
                if theoretical_thermal_building_demand_in_watt > 0:
                    return HeatDistributionValveMode.HEATING
                if theoretical_thermal_building_demand_in_watt < 0:
                    return HeatDistributionValveMode.COOLING
                return mode
            case _:
                assert_never(mode)

    @staticmethod
    def valve_state(mode: HeatDistributionValveMode, *, is_heating_season: bool) -> int:
        """Return the valve's published state: 1 for heating, -1 for cooling and 0 for no flow.

        A closed valve publishes 0, and so does a valve open for heating outside the heating season, which passes
        nothing.

        Args:
            mode: The valve's mode.
            is_heating_season: Whether the day is in the heating season.

        Returns:
            The state.
        """
        match mode:
            case HeatDistributionValveMode.HEATING:
                return 1 if is_heating_season else 0
            case HeatDistributionValveMode.COOLING:
                return -1
            case HeatDistributionValveMode.OFF:
                return 0
            case _:
                assert_never(mode)

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


class HeatDistributionControllerInformation:
    """The emitter circuit's facts a setup passes to the components around it: the emitter type and the mass flow."""

    def __init__(self, config: HeatDistributionControllerConfig) -> None:
        """Derive the emitter type and the circuit's water mass flow from a resolved configuration.

        Args:
            config: The heat-distribution controller's configuration, its heating load resolved.
        """
        self.hds_controller_config: HeatDistributionControllerConfig = config
        self.heat_distribution_system_type: HeatDistributionSystemType = config.heating_system
        self.water_mass_flow_rate_in_kg_per_second: float = self.water_mass_flow_rate_for(
            heating_load_in_watt=concrete(config.heating_load_of_building_in_watt),
            emitter_design=EmitterDesign.for_type(config.heating_system),
        )

    @staticmethod
    def water_mass_flow_rate_for(*, heating_load_in_watt: float, emitter_design: EmitterDesign) -> float:
        """Return the water mass flow that carries the heating load at the emitter's design temperature lift, in kg/s.

        For example, 7000 W through a floor heating designed for 35/28 °C (7 K) needs 7000 / (4180 x 7) = 0.24 kg/s.

        Args:
            heating_load_in_watt: The building's heating load, in W.
            emitter_design: The design point of the emitter.

        Returns:
            The mass flow, in kg/s.
        """
        specific_heat_capacity_of_water_in_joule_per_kg_per_kelvin = PhysicsConfig.get_properties_for_energy_carrier(
            energy_carrier=lt.LoadTypes.WATER
        ).specific_heat_capacity_in_joule_per_kg_per_kelvin
        return heating_load_in_watt / (
            specific_heat_capacity_of_water_in_joule_per_kg_per_kelvin
            * (
                emitter_design.maximal_supply_temperature_in_celsius
                - emitter_design.maximal_return_temperature_in_celsius
            )
        )
