"""Iterative Energy Surplus Controller.

It received the electricity consumption
of all components and the PV production. According to the balance it
sends activation/deactivation siganls to components.
The component with the lowest source weight is activated first.
"""

import dataclasses
import math
from dataclasses import dataclass, field

from types import MappingProxyType
from typing import Any, ClassVar, List, Mapping, Tuple, Optional, cast
from collections import OrderedDict
from dataclasses_json import dataclass_json
import pandas as pd
from hisim import component as cp
from hisim import dynamic_component
from hisim import loadtypes as lt
from hisim import utils
from hisim.component import ComponentInput, ComponentOutput
from hisim.config import ConfigBase, ComponentID, DisplayConfig, preset
from hisim.config.channels import DispatchRule, DynamicConnectionChannel
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiHelperClass, KpiSource, KpiTagEnumClass
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions
from hisim.economics.facts import CostRelevance


@dataclass_json
@dataclass
class EMSConfig(ConfigBase):
    """L1 Controller Config.

    The one surplus-control strategy anything uses is :meth:`preset_optimize_own_consumption`;
    the fields say how far the controller may push a building's and a storage's set temperatures
    to place that surplus.
    """

    MAIN_CLASS = "hisim.components.controller_l2_energy_management_system.L2GenericEnergyManagementSystem"

    component_id: ComponentID
    # control strategy, more or less obsolete because only "optimize_own_consumption" is used at the moment.
    strategy: str = "optimize_own_consumption"
    # limit for peak shaving option, more or less obsolete because only "optimize_own_consumption" is used at the moment.
    limit_to_shave: float = 0
    # increase building set temperatures for heating when PV surplus is available.
    # Must be smaller than difference of set_heating_temperature and set_cooling_temperature.
    # The three offsets are declared in °C, the unit of the modifier outputs they become.
    building_indoor_temperature_offset_value: float = field(default=2, metadata={"unit": lt.Units.CELSIUS})
    # increase in dhw buffer set temperatures when PV surplus is available for heating
    domestic_hot_water_storage_temperature_offset_value: float = field(
        default=10, metadata={"unit": lt.Units.CELSIUS}
    )
    # increase in SimpleHotWaterStorage set temperatures when PV surplus is available for heating
    space_heating_water_storage_temperature_offset_value: float = field(
        default=10, metadata={"unit": lt.Units.CELSIUS}
    )
    #: CO2 footprint of investment in kg. Unset throughout the repository, which is what makes
    #: postprocessing look the device up in the cost database instead.
    device_co2_footprint_in_kg: Optional[float] = None
    #: cost for investment in Euro
    investment_costs_in_euro: Optional[float] = None
    #: lifetime in years
    lifetime_in_years: Optional[float] = None
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: Optional[float] = None
    # subsidies as percentage of investment costs
    subsidy_as_percentage_of_investment_costs: Optional[float] = None

    @preset
    @classmethod
    def preset_optimize_own_consumption(cls, name: str) -> "EMSConfig":
        """The surplus controller that maximises the building's own PV consumption.

        The field defaults are this controller, so the preset adds nothing to them but the
        component's name.

        Args:
            name: The instance name, which becomes the configuration's component identity.

        Returns:
            EMSConfig: The preset configuration.
        """
        return cls(component_id=ComponentID(name=name))


@dataclass(frozen=True)
class EMSState:
    """The energy manager's state from one time step to the next: whether it raised the set temperatures.

    The energy manager raises the set temperatures of the building, the space-heating storage and the hot-water
    storage while electricity is left over after the battery. Close to zero surplus it keeps the decision of the
    step before (:meth:`L2GenericEnergyManagementSystem.raises_set_temperatures`), so that decision is the one
    value the manager carries from step to step. ``i_simulate`` reads it from the saved state and writes the
    decision of the current pass as the end-of-step state; ``i_save_state`` makes the converged pass's decision
    the next step's start.
    """

    #: Whether the set temperatures are raised, in the step this state ends.
    set_temperatures_are_raised: bool = False


class L2GenericEnergyManagementSystem(dynamic_component.DynamicComponent):
    """Surplus electricity controller - time step based.

    Iteratively goes through connected inputs by hierachy of
    source weights of inputs and passes available surplus
    electricity to each device. Needs to be configured with
    dynamic In- and Outputs.

    Recognises production of any component when dynamic input
    is labeled with the flag "CONSUMPTION" and the
    related source weight is set to 999.

    Recognised non controllable consumption of any component
    when dynamic input is labeld with the flag
    "CONSUMPTION_UNCONTROLLED" and the related source weight
    is set to 999.

    For each component, which should receive signals from the
    EMS, the EMS needs to be connected with one dynamic input
    with the tag "ELECTRICITY_REAL" and the source weight of
    the related component. This signal reflects the real
    consumption/production of the device, which is needed to
    update the energy balance in the EMS.
    In addition, the EMS needs to be connected with one dynamic
    output with the tag "ELECTRICITY_TARGET" with the
    source weight of the related component. This signal sends
    information on the available surplus electricity to the
    component, which receives signals from the EMS.

    """

    cost_relevance = CostRelevance.PRICED

    #: The weight each kind of participant the controller ranks is fed at by default: its rank in the
    #: surplus distribution, lowest first (residents, space heating, hot water, solar thermal, the
    #: home battery). The dynamic default connections below take their weights from here, and a
    #: controller assembly's priority list starts from these values (``assemblies_spec.md`` §4.4):
    #: the k-th further participant of one kind gets ``default + k``, refused where that reaches another
    #: kind's weight (D27). 999 (``DynamicConnectionChannel.MONITORED_ONLY_WEIGHT``) is never a rank, it
    #: marks a participant that is only measured.
    DEFAULT_WEIGHTS: ClassVar[Mapping[lt.ComponentType, int]] = MappingProxyType(
        {
            lt.ComponentType.RESIDENTS: 1,
            lt.ComponentType.HEAT_PUMP_BUILDING: 2,
            lt.ComponentType.ELECTRIC_HEATING_SH: 2,
            lt.ComponentType.HEAT_PUMP_DHW: 3,
            lt.ComponentType.ELECTRIC_HEATING_DHW: 3,
            lt.ComponentType.SOLAR_THERMAL_SYSTEM: 4,
            lt.ComponentType.BATTERY: 6,
        }
    )

    #: The half-width of the band around zero surplus within which the energy manager keeps the previous step's
    #: decision to raise the set temperatures, in W (:meth:`raises_set_temperatures`). Wide against float noise:
    #: a battery that balances the house leaves a surplus of about +-5e-12 W, and the simulator accepts a step once
    #: no output moves by more than 1e-4 between passes, so a converged surplus moves by less than about 1e-3 W
    #: even where several ports add up. Negligible in energy: while the surplus lies within the band, at most 0.01 W
    #: goes to or comes from the grid whichever decision is kept, at most 88 Wh in a year at any step length.
    SURPLUS_HYSTERESIS_BAND_IN_WATT: ClassVar[float] = 0.01

    # Inputs
    ElectricityToElectrolyzerUnused = "ElectricityToElectrolyzerUnused"
    ElectricityToBuildingFromDistrict = "ElectricityToBuildingFromDistrict"

    # Outputs
    ElectricityToElectrolyzerTarget = "ElectricityToElectrolyzerTarget"

    TotalElectricityToOrFromGrid = "TotalElectricityToOrFromGrid"
    TotalElectricityConsumption = "TotalElectricityConsumption"
    BuildingIndoorTemperatureModifier = "BuildingIndoorTemperatureModifier"  # connect to HDS controller and Building
    DomesticHotWaterStorageTemperatureModifier = (
        "DomesticHotWaterStorageTemperatureModifier"  # used for L1HeatPumpController  # Todo: change name?
    )
    SpaceHeatingWaterStorageTemperatureModifier = (
        "SpaceHeatingWaterStorageTemperatureModifier"  # used for HeatPumpHplibController
    )
    ElectricityToBuildingFromDistrictEMSOutput = "ElectricityToBuildingFromDistrictEMSOutput"

    PeakShavingStatus = "PeakShavingStatus"

    #: Stable key of the channel carrying electricity a participant produces.
    PRODUCTION_CHANNEL = "production"

    #: Stable key of the channel carrying consumption this controller cannot influence.
    CONSUMPTION_UNCONTROLLED_CHANNEL = "consumption_uncontrolled"

    #: Stable key of the channel carrying consumption this controller dispatches to.
    CONSUMPTION_CONTROLLED_CHANNEL = "consumption_controlled"

    #: Stable key of the channel carrying a battery, which is a controllable consumer the
    #: controller treats specially because it can also give electricity back.
    STORAGE_CHANNEL = "storage"

    #: The flows this energy management system understands. Until this declaration the accepted
    #: tags existed only implicitly, inside the hard-coded tag queries of the ranking code, which
    #: meant a participant whose tags matched no query wired cleanly and was then never read.
    #:
    #: The four channels are nested on purpose: ``storage`` is a strict superset of
    #: ``consumption_controlled``, and most-specific matching is what routes a battery to the
    #: former while an ordinary controllable consumer — a heat pump, whose extra descriptive tag
    #: no channel consumes — falls to the latter. That mirrors the ranking code's existing
    #: "is this participant a battery" branch instead of inventing a new tag value for it.
    #:
    #: Two runtime lookups deliberately read no channel: the ranking code's per-participant
    #: dispatch-output query, and the KPI code asking which participant kind a dispatch output
    #: steers. Both key on one participant's component type beside
    #: :attr:`~hisim.loadtypes.InandOutputType.ELECTRICITY_TARGET`, so they name no fixed tag set
    #: — and both read outputs, for which no channel-key accessor exists. A dispatch-side
    #: accessor would be a design of its own, not a near-match to force onto
    #: :meth:`~hisim.dynamic_component.DynamicComponent.get_channel_inputs`.
    CHANNELS: Tuple[DynamicConnectionChannel, ...] = (
        DynamicConnectionChannel(
            key=PRODUCTION_CHANNEL,
            tags=frozenset({lt.InandOutputType.ELECTRICITY_PRODUCTION}),
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            dispatch=DispatchRule.FORBIDDEN,
        ),
        DynamicConnectionChannel(
            key=CONSUMPTION_UNCONTROLLED_CHANNEL,
            tags=frozenset({lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED}),
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            dispatch=DispatchRule.FORBIDDEN,
        ),
        DynamicConnectionChannel(
            key=CONSUMPTION_CONTROLLED_CHANNEL,
            tags=frozenset({lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED}),
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            dispatch=DispatchRule.REQUIRED,
            dispatch_tags=frozenset({lt.InandOutputType.ELECTRICITY_TARGET}),
        ),
        DynamicConnectionChannel(
            key=STORAGE_CHANNEL,
            tags=frozenset(
                {
                    lt.ComponentType.BATTERY,
                    lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
                }
            ),
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            dispatch=DispatchRule.REQUIRED,
            dispatch_tags=frozenset({lt.InandOutputType.ELECTRICITY_TARGET}),
        ),
    )

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: EMSConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ):
        """Initializes."""
        self.my_component_inputs: List[dynamic_component.DynamicConnectionInput] = []
        self.my_component_outputs: List[dynamic_component.DynamicConnectionOutput] = []
        self.ems_config = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_component_inputs=self.my_component_inputs,
            my_component_outputs=self.my_component_outputs,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.state = EMSState()
        self.previous_state = EMSState()

        self.component_types_sorted: List[lt.ComponentType] = []
        self.inputs_sorted: List[ComponentInput] = []
        self.outputs_sorted: List[ComponentOutput] = []
        self.production_inputs: List[ComponentInput] = []
        self.consumption_uncontrolled_inputs: List[ComponentInput] = []
        self.consumption_ems_controlled_inputs: List[ComponentInput] = []

        self.operating_mode: Any
        self.strategy = self.ems_config.strategy
        self.limit_to_shave = self.ems_config.limit_to_shave
        self.building_indoor_temperature_offset_value = self.ems_config.building_indoor_temperature_offset_value
        self.domestic_hot_water_storage_temperature_offset_value = (
            self.ems_config.domestic_hot_water_storage_temperature_offset_value
        )
        self.space_heating_water_storage_temperature_offset_value = (
            self.ems_config.space_heating_water_storage_temperature_offset_value
        )

        # Inputs
        self.electricity_to_electrolyzer_unused: cp.ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.ElectricityToElectrolyzerUnused,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            mandatory=False,
        )

        self.electricity_to_building_from_district: cp.ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.ElectricityToBuildingFromDistrict,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            mandatory=False,
        )

        # Outputs
        self.total_electricity_to_or_from_grid: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TotalElectricityToOrFromGrid,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            output_description=f"here a description for {self.TotalElectricityToOrFromGrid} will follow.",
        )

        self.total_electricity_consumption_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TotalElectricityConsumption,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            output_description=f"here a description for {self.TotalElectricityConsumption} will follow.",
        )

        self.building_indoor_temperature_modifier: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.BuildingIndoorTemperatureModifier,
            load_type=lt.LoadTypes.TEMPERATURE,
            unit=lt.Units.CELSIUS,
            output_description=f"here a description for {self.BuildingIndoorTemperatureModifier} will follow.",
        )

        self.domestic_hot_water_storage_temperature_modifier: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.DomesticHotWaterStorageTemperatureModifier,
            load_type=lt.LoadTypes.TEMPERATURE,
            unit=lt.Units.CELSIUS,
            output_description=f"here a description for {self.DomesticHotWaterStorageTemperatureModifier} will follow.",
        )

        self.space_heating_water_storage_temperature_modifier: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.SpaceHeatingWaterStorageTemperatureModifier,
            load_type=lt.LoadTypes.TEMPERATURE,
            unit=lt.Units.CELSIUS,
            output_description=f"here a description for {self.SpaceHeatingWaterStorageTemperatureModifier} will follow.",
        )

        self.peak_shaving_status: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.PeakShavingStatus,
            load_type=lt.LoadTypes.ANY,
            unit=lt.Units.ANY,
            output_description=f"here a description for {self.PeakShavingStatus} will follow.",
        )

        self.electricity_to_building_from_district_output: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityToBuildingFromDistrictEMSOutput,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            output_description=f"here a description for {self.ElectricityToBuildingFromDistrictEMSOutput} will follow.",
        )

        # Describing the connections creates no port. Each of these methods returns the feeds one
        # participant class would bring and, where the controller steers that participant, the
        # target output it would need; both are grown later, by
        # connect_with_dynamic_connections_list, and only for a class the system setup built. The
        # methods used to create their target outputs as a side effect of being asked, which gave
        # every controller the ports of every device it could ever meet (F-1).
        self.add_dynamic_default_connections(self.get_default_connections_from_utsp_occupancy())
        self.add_dynamic_default_connections(self.get_default_connections_from_pv_system())
        self.add_dynamic_default_connections(self.get_default_connections_from_more_advanced_heat_pump())
        self.add_dynamic_default_connections(self.get_default_connections_from_advanced_battery())
        self.add_dynamic_default_connections(self.get_default_connections_from_electric_heater())
        self.add_dynamic_default_connections(self.get_default_connections_from_solar_thermal_system())
        # self.add_dynamic_default_connections(self.get_default_connections_from_car_battery())

    def get_default_connections_from_pv_system(
        self,
    ):
        """Get pv system default connections."""

        from hisim.components.generic_pv_system import PVSystem  # pylint: disable=import-outside-toplevel

        dynamic_connections = []
        pv_class_name = PVSystem.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=PVSystem,
                source_class_name=pv_class_name,
                source_component_field_name=PVSystem.ElectricityOutput,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[
                    lt.ComponentType.PV,
                    lt.InandOutputType.ELECTRICITY_PRODUCTION,
                ],
                source_weight=DynamicConnectionChannel.MONITORED_ONLY_WEIGHT,
            )
        )

        return dynamic_connections

    def get_default_connections_from_utsp_occupancy(
        self,
    ):
        """Get utsp occupancy default connections."""

        from hisim.components.loadprofilegenerator_utsp_connector import (  # pylint: disable=import-outside-toplevel
            UtspLpgConnector,
        )

        dynamic_connections = []
        occupancy_class_name = UtspLpgConnector.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=UtspLpgConnector,
                source_class_name=occupancy_class_name,
                source_component_field_name=UtspLpgConnector.ElectricalPowerConsumption,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[lt.ComponentType.RESIDENTS, lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED],
                source_weight=self.DEFAULT_WEIGHTS[lt.ComponentType.RESIDENTS],
                target_output=dynamic_component.DynamicComponentTargetOutput(
                    source_output_name=f"ElectricityToOrFromGridOf{occupancy_class_name}_",
                    source_tags=[
                        lt.ComponentType.RESIDENTS,
                        lt.InandOutputType.ELECTRICITY_TARGET,
                    ],
                    output_description="Target electricity for Occupancy. ",
                ),
            )
        )
        return dynamic_connections

    def get_default_connections_from_more_advanced_heat_pump(
        self,
    ):
        """Get advanced heat pump default connections."""

        from hisim.components.more_advanced_heat_pump_hplib import (  # pylint: disable=import-outside-toplevel
            MoreAdvancedHeatPumpHPLib,
        )

        dynamic_connections = []
        more_advanced_heat_pump_class_name = MoreAdvancedHeatPumpHPLib.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=MoreAdvancedHeatPumpHPLib,
                source_class_name=more_advanced_heat_pump_class_name,
                source_component_field_name=MoreAdvancedHeatPumpHPLib.ElectricalInputPowerSH,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[
                    lt.ComponentType.HEAT_PUMP_BUILDING,
                    lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
                ],
                source_weight=self.DEFAULT_WEIGHTS[lt.ComponentType.HEAT_PUMP_BUILDING],
                target_output=dynamic_component.DynamicComponentTargetOutput(
                    source_output_name=f"ElectricityToOrFromGridOfSH{more_advanced_heat_pump_class_name}_",
                    source_tags=[
                        lt.ComponentType.HEAT_PUMP_BUILDING,
                        lt.InandOutputType.ELECTRICITY_TARGET,
                    ],
                    output_description="Target electricity for Heating Heat Pump. ",
                ),
            )
        )
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=MoreAdvancedHeatPumpHPLib,
                source_class_name=more_advanced_heat_pump_class_name,
                source_component_field_name=MoreAdvancedHeatPumpHPLib.ElectricalInputPowerDHW,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[
                    lt.ComponentType.HEAT_PUMP_DHW,
                    lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
                ],
                source_weight=self.DEFAULT_WEIGHTS[lt.ComponentType.HEAT_PUMP_DHW],
                # The DHW electrical power output only exists when the heat pump
                # has domestic hot water preparation enabled; allow this mandatory
                # input to remain unconnected when DHW is deactivated.
                allow_unconnected_mandatory=True,
                target_output=dynamic_component.DynamicComponentTargetOutput(
                    source_output_name=f"ElectricityToOrFromGridOfDHW{more_advanced_heat_pump_class_name}_",
                    source_tags=[
                        lt.ComponentType.HEAT_PUMP_DHW,
                        lt.InandOutputType.ELECTRICITY_TARGET,
                    ],
                    output_description="Target electricity for Heating Heat Pump. ",
                ),
            )
        )
        return dynamic_connections

    def get_default_connections_from_electric_heater(
        self,
    ):
        """Get electric heater default connections."""

        from hisim.components.generic_electric_heating import ElectricHeating  # pylint: disable=import-outside-toplevel

        dynamic_connections = []
        electric_heater_class_name = ElectricHeating.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=ElectricHeating,
                source_class_name=electric_heater_class_name,
                source_component_field_name=ElectricHeating.ElectricOutputShPower,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[
                    lt.ComponentType.ELECTRIC_HEATING_SH,
                    lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
                ],
                source_weight=self.DEFAULT_WEIGHTS[lt.ComponentType.ELECTRIC_HEATING_SH],
                target_output=dynamic_component.DynamicComponentTargetOutput(
                    source_output_name=f"ElectricityToOrFromGridOfSH{electric_heater_class_name}_",
                    source_tags=[
                        lt.ComponentType.ELECTRIC_HEATING_SH,
                        lt.InandOutputType.ELECTRICITY_TARGET,
                    ],
                    output_description="Target electricity for electric heater space heating.",
                ),
            )
        )
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=ElectricHeating,
                source_class_name=electric_heater_class_name,
                source_component_field_name=ElectricHeating.ElectricOutputDhwPower,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[
                    lt.ComponentType.ELECTRIC_HEATING_DHW,
                    lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
                ],
                source_weight=self.DEFAULT_WEIGHTS[lt.ComponentType.ELECTRIC_HEATING_DHW],
                target_output=dynamic_component.DynamicComponentTargetOutput(
                    source_output_name=f"ElectricityToOrFromGridOfDHW{electric_heater_class_name}_",
                    source_tags=[
                        lt.ComponentType.ELECTRIC_HEATING_DHW,
                        lt.InandOutputType.ELECTRICITY_TARGET,
                    ],
                    output_description="Target electricity for electric heater domestic hot water.",
                ),
            )
        )
        return dynamic_connections

    def get_default_connections_from_advanced_battery(
        self,
    ):
        """Get advanced battery default connections."""

        from hisim.components.advanced_battery_bslib import Battery  # pylint: disable=import-outside-toplevel

        dynamic_connections = []
        advanced_battery_class_name = Battery.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=Battery,
                source_class_name=advanced_battery_class_name,
                source_component_field_name=Battery.AcBatteryPowerUsed,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED],
                source_weight=self.DEFAULT_WEIGHTS[lt.ComponentType.BATTERY],
            )
        )

        return dynamic_connections

    def get_default_connections_from_solar_thermal_system(
        self,
    ):
        """Get solar thermal default connections."""

        from hisim.components.solar_thermal_system import SolarThermalSystem  # pylint: disable=import-outside-toplevel

        dynamic_connections = []
        solar_thermal_class_name = SolarThermalSystem.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=SolarThermalSystem,
                source_class_name=solar_thermal_class_name,
                source_component_field_name=SolarThermalSystem.ElectricityConsumptionOutput,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[
                    lt.ComponentType.SOLAR_THERMAL_SYSTEM,
                    lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
                ],
                source_weight=self.DEFAULT_WEIGHTS[lt.ComponentType.SOLAR_THERMAL_SYSTEM],
                target_output=dynamic_component.DynamicComponentTargetOutput(
                    source_output_name=f"ElectricityToOrFromGridOf{solar_thermal_class_name}_",
                    source_tags=[
                        lt.ComponentType.SOLAR_THERMAL_SYSTEM,
                        lt.InandOutputType.ELECTRICITY_TARGET,
                    ],
                    output_description="Target electricity for solar thermal domestic hot water.",
                ),
            )
        )

        return dynamic_connections

    def sort_source_weights_and_components(
        self,
    ) -> Tuple[
        List[ComponentInput],
        List[lt.ComponentType],
        List[ComponentOutput],
        List[ComponentInput],
        List[ComponentInput],
        List[ComponentInput],
    ]:
        """Sorts dynamic Inputs and Outputs according to source weights."""
        measured = DynamicConnectionChannel.MONITORED_ONLY_WEIGHT
        inputs = [elem for elem in self.my_component_inputs if elem.source_weight != measured]

        source_tags = [elem.source_tags[0] for elem in inputs]
        source_weights = [elem.source_weight for elem in inputs]
        sortindex = sorted(range(len(source_weights)), key=lambda k: source_weights[k])
        source_weights = [source_weights[i] for i in sortindex]

        component_types_sorted = cast(List[lt.ComponentType], [source_tags[i] for i in sortindex])
        inputs_sorted = [getattr(self, inputs[i].source_component_class) for i in sortindex]
        outputs_sorted = []

        for ind, source_weight in enumerate(source_weights):
            # Literal on purpose: a per-participant dispatch-output query, see the CHANNELS docstring.
            outputs = self.get_all_dynamic_outputs(
                tags=[
                    component_types_sorted[ind],
                    lt.InandOutputType.ELECTRICITY_TARGET,
                ],
                weight_counter=source_weight,
            )

            for output in outputs:
                if output is not None:
                    outputs_sorted.append(output)
                else:
                    raise ValueError("Dynamic input is not connected to dynamic output")
        outputs_sorted = list(OrderedDict.fromkeys(outputs_sorted))

        production_inputs = self.get_channel_inputs(self.PRODUCTION_CHANNEL)
        consumption_uncontrolled_inputs = self.get_channel_inputs(self.CONSUMPTION_UNCONTROLLED_CHANNEL)
        consumption_ems_controlled_inputs = self.get_channel_inputs(self.CONSUMPTION_CONTROLLED_CHANNEL)

        return (
            inputs_sorted,
            component_types_sorted,
            outputs_sorted,
            production_inputs,
            consumption_uncontrolled_inputs,
            consumption_ems_controlled_inputs,
        )

    def write_to_report(self):
        """Writes relevant information to report."""
        return self.ems_config.get_string_dict()

    def i_save_state(self) -> None:
        """Save the state the step ends with, the decision of the converged pass, as the next step's start."""
        self.previous_state = dataclasses.replace(self.state)

    def i_restore_state(self) -> None:
        """Restore the state the step started with, so every pass decides from the previous step's decision."""
        self.state = dataclasses.replace(self.previous_state)

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublechecks values."""
        pass

    def control_electricity_component_iterative(
        self,
        available_surplus_electricity_in_watt: float,
        stsv: cp.SingleTimeStepValues,
        current_component_type: lt.ComponentType,
        current_input: cp.ComponentInput,
        current_output: cp.ComponentOutput,
    ) -> float:
        """Calculates available surplus electricity.

        Subtracts the electricity consumption signal of the component from the previous iteration,
        and sends updated signal back.
        This function controls how surplus electricity is distributed and how much of each components'
        electricity need is covered onsite or from grid.
        """
        # get electricity demand from input component and substract from (or add to) available surplus electricity
        electricity_demand_from_current_input_component_in_watt = stsv.get_input_value(component_input=current_input)

        # if available_surplus_electricity > 0: electricity is fed into battery
        # if available_surplus_electricity < 0: electricity is taken from battery
        if current_component_type == lt.ComponentType.BATTERY:
            stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)
            # difference between what is fed into battery and what battery really used
            available_surplus_electricity_in_watt = (
                available_surplus_electricity_in_watt - electricity_demand_from_current_input_component_in_watt
            )

        # these are electricity CONSUMERS
        elif current_component_type in [
            lt.ComponentType.RESIDENTS,
            lt.ComponentType.ELECTROLYZER,
            lt.ComponentType.SMART_DEVICE,
            lt.ComponentType.CAR_BATTERY,
            lt.ComponentType.HEAT_PUMP_DHW,
            lt.ComponentType.HEAT_PUMP,
            lt.ComponentType.HEAT_PUMP_BUILDING,
            lt.ComponentType.ELECTRIC_HEATING_SH,
            lt.ComponentType.ELECTRIC_HEATING_DHW,
            lt.ComponentType.SOLAR_THERMAL_SYSTEM,
        ]:
            # if surplus electricity is available, a part of the component's consumption can be covered onsite
            if available_surplus_electricity_in_watt > 0:
                available_surplus_electricity_in_watt = (
                    available_surplus_electricity_in_watt - electricity_demand_from_current_input_component_in_watt
                )
                # if this value is 0, all electricity demand from this consumer could be covered exactly from surplus energy
                # if this value is positive, all electricity demand from this consumer could be covered from surplus energy and there is even surplus energy left
                # if this value is negative in (0, -electricity_demand): then only parts of the electricity demand from the consumer could be covered from surplus energy
                # if this value is equal to -electrcity demand: no surplus energy was available and electricty demand from the consumer was totally taken from grid
                stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)
            # otherwise all of the component's consumption is taken from grid
            else:
                stsv.set_output_value(
                    output=current_output, value=-electricity_demand_from_current_input_component_in_watt
                )
                available_surplus_electricity_in_watt = (
                    available_surplus_electricity_in_watt - electricity_demand_from_current_input_component_in_watt
                )

        # these are electricity PRODUCERS
        elif current_component_type == lt.ComponentType.CHP:
            available_surplus_electricity_in_watt = (
                available_surplus_electricity_in_watt + electricity_demand_from_current_input_component_in_watt
            )
            stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)

        elif current_component_type == lt.ComponentType.SURPLUS_CONTROLLER_DISTRICT:
            if available_surplus_electricity_in_watt > 0:
                available_surplus_electricity_in_watt = (
                    available_surplus_electricity_in_watt - electricity_demand_from_current_input_component_in_watt
                )
                stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)
            else:
                stsv.set_output_value(
                    output=current_output, value=-electricity_demand_from_current_input_component_in_watt
                )

        return available_surplus_electricity_in_watt

    @staticmethod
    def raises_set_temperatures(*, surplus_after_battery_in_watt: float, raised_in_previous_step: bool) -> bool:
        """Return whether the energy manager raises the set temperatures, from the surplus and its previous decision.

        The surplus after the battery is the electricity left over once every participant, the battery last, has
        taken its share; positive means electricity would go to the grid. The decision has a hysteresis band of
        ``SURPLUS_HYSTERESIS_BAND_IN_WATT`` around zero: above the band the set temperatures are raised, below it
        they are not, and within it, edges included, the decision of the previous step is kept. For example, with
        the band of 0.01 W, a surplus of 5e-12 W keeps the previous decision, a surplus of 0.5 W raises, and a
        surplus of -0.5 W does not.

        Why: when the battery balances the house exactly, the surplus is zero up to float noise, and a decision on
        its sign alone would switch the raise on and off between the passes of one step.

        Args:
            surplus_after_battery_in_watt: The surplus left after the battery, in W.
            raised_in_previous_step: Whether the set temperatures were raised in the previous step.

        Returns:
            Whether the set temperatures are raised in this step.

        Raises:
            ValueError: If the surplus is NaN or infinite.
        """
        if not math.isfinite(surplus_after_battery_in_watt):
            raise ValueError(
                f"The surplus after the battery must be a finite power in W, got {surplus_after_battery_in_watt!r}."
            )
        band_in_watt = L2GenericEnergyManagementSystem.SURPLUS_HYSTERESIS_BAND_IN_WATT
        if surplus_after_battery_in_watt > band_in_watt:
            return True
        if surplus_after_battery_in_watt < -band_in_watt:
            return False
        return raised_in_previous_step

    def modify_set_temperatures_for_components_in_case_of_surplus_electricity(
        self,
        set_temperatures_are_raised: bool,
        stsv: cp.SingleTimeStepValues,
        inputs_sorted: List[ComponentInput],
        component_types_sorted: List[lt.ComponentType],
    ) -> None:
        """Publish the set-temperature raises for space heating and hot water, or zero raises.

        While the raise is on (:meth:`raises_set_temperatures`), the heat pumps heat up the building and the water
        storages, and the surplus electricity is stored as heat. See also SG-ready heat pumps:
        https://de.gridx.ai/wissen/sg-ready.

        The temperature modification outputs go to the heat pumps, the heat distribution system and the building
        component (see network charts).

        Args:
            set_temperatures_are_raised: Whether the set temperatures are raised in this pass.
            stsv: The step values the raises are written to.
            inputs_sorted: The participants' inputs, ranked.
            component_types_sorted: The participants' component types, in the order of ``inputs_sorted``.
        """
        for index in range(len(inputs_sorted)):
            current_component_type = component_types_sorted[index]

            if current_component_type == lt.ComponentType.HEAT_PUMP_BUILDING:
                if set_temperatures_are_raised:
                    stsv.set_output_value(
                        self.building_indoor_temperature_modifier,
                        self.building_indoor_temperature_offset_value,
                    )
                    stsv.set_output_value(
                        self.space_heating_water_storage_temperature_modifier,
                        self.space_heating_water_storage_temperature_offset_value,
                    )
                else:
                    stsv.set_output_value(self.building_indoor_temperature_modifier, 0)
                    stsv.set_output_value(self.space_heating_water_storage_temperature_modifier, 0)

            elif current_component_type in [
                lt.ComponentType.HEAT_PUMP_DHW,
                lt.ComponentType.HEAT_PUMP,
            ]:
                if set_temperatures_are_raised:
                    stsv.set_output_value(
                        self.domestic_hot_water_storage_temperature_modifier,
                        self.domestic_hot_water_storage_temperature_offset_value,
                    )
                else:
                    stsv.set_output_value(self.domestic_hot_water_storage_temperature_modifier, 0)

    def distribute_available_surplus_electricity_iterative(
        self,
        available_surplus_electricity_in_watt: float,
        stsv: cp.SingleTimeStepValues,
        inputs_sorted: List[ComponentInput],
        component_types_sorted: List[lt.ComponentType],
        outputs_sorted: List[ComponentOutput],
    ) -> float:
        """Evaluates available surplus electricity component by component, iteratively, and sends updated signals back."""
        if len(outputs_sorted) < len(inputs_sorted) or len(component_types_sorted) < len(inputs_sorted):
            raise ValueError(
                "Lengths of inputs, component types, and outputs must match."
                f" Got {len(inputs_sorted)}, {len(component_types_sorted)}, and {len(outputs_sorted)}."
                "Make sure all inputs have the same source weight as the corresponding output! "
                "Please check all your default and manual connections. \n"
                f"Inputs: {[input.fullname for input in inputs_sorted]} \n"
                f"Outputs: {[output.full_name for output in outputs_sorted]} \n"
            )

        for index, single_input_sorted in enumerate(inputs_sorted):
            single_component_type_sorted = component_types_sorted[index]
            single_output_sorted = outputs_sorted[index]

            available_surplus_electricity_in_watt = self.control_electricity_component_iterative(
                available_surplus_electricity_in_watt=available_surplus_electricity_in_watt,
                stsv=stsv,
                current_component_type=single_component_type_sorted,
                current_input=single_input_sorted,
                current_output=single_output_sorted,
            )

        return available_surplus_electricity_in_watt

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates iteration of surplus controller."""
        if timestep == 0:
            (
                self.inputs_sorted,
                self.component_types_sorted,
                self.outputs_sorted,
                self.production_inputs,
                self.consumption_uncontrolled_inputs,
                self.consumption_ems_controlled_inputs,
            ) = self.sort_source_weights_and_components()

        district_electricity_unused = stsv.get_input_value(component_input=self.electricity_to_building_from_district)

        stsv.set_output_value(self.electricity_to_building_from_district_output, district_electricity_unused)

        # get total production and consumptions
        production_in_watt = (
            sum([stsv.get_input_value(component_input=elem) for elem in self.production_inputs])
            + district_electricity_unused
        )
        consumption_uncontrolled_in_watt = sum(
            [stsv.get_input_value(component_input=elem) for elem in self.consumption_uncontrolled_inputs]
        )
        consumption_ems_controlled_in_watt = sum(
            [stsv.get_input_value(component_input=elem) for elem in self.consumption_ems_controlled_inputs]
        )

        # Production of Electricity positve sign
        # Consumption of Electricity negative sign
        available_surplus_electricity_in_watt = production_in_watt - consumption_uncontrolled_in_watt
        if self.strategy == "optimize_own_consumption":
            available_surplus_electricity_in_watt = self.distribute_available_surplus_electricity_iterative(
                available_surplus_electricity_in_watt=available_surplus_electricity_in_watt,
                stsv=stsv,
                inputs_sorted=self.inputs_sorted,
                component_types_sorted=self.component_types_sorted,
                outputs_sorted=self.outputs_sorted,
            )
            set_temperatures_are_raised = self.raises_set_temperatures(
                surplus_after_battery_in_watt=available_surplus_electricity_in_watt,
                raised_in_previous_step=self.previous_state.set_temperatures_are_raised,
            )
            self.state = EMSState(set_temperatures_are_raised=set_temperatures_are_raised)
            self.modify_set_temperatures_for_components_in_case_of_surplus_electricity(
                set_temperatures_are_raised=set_temperatures_are_raised,
                stsv=stsv,
                inputs_sorted=self.inputs_sorted,
                component_types_sorted=self.component_types_sorted,
            )

        stsv.set_output_value(self.total_electricity_to_or_from_grid, available_surplus_electricity_in_watt)
        stsv.set_output_value(
            self.total_electricity_consumption_channel,
            consumption_uncontrolled_in_watt + consumption_ems_controlled_in_watt,
        )
        """
        elif self.strategy == "seasonal_storage":
            self.seasonal_storage(delta_demand=delta_demand, stsv=stsv)
        elif self.strategy == "peak_shaving_into_grid":
            self.peak_shaving_into_grid(delta_demand=delta_demand, limit_to_shave=limit_to_shave,stsv=stsv)
        elif self.strategy == "peak_shaving_from_grid":
            self.peak_shaving_from_grid(delta_demand=delta_demand, limit_to_shave=limit_to_shave,stsv=stsv)
        """

        # HEAT #
        # If comftortable temperature of building is to low heat with WarmWaterStorage the building
        # Solution with Control Signal Residence
        # not perfect solution!
        """
        if self.temperature_residence<self.min_comfortable_temperature_residence:
            #heat
            #here has to be added how "strong" HeatingWater Storage can be discharged
            #Working with upper boarder?
        elif self.temperature_residence > self.max_comfortable_temperature_residence:
            #cool
        elif self.temperature_residence>self.min_comfortable_temperature_residence and self.temperature_residence<self.max_comfortable_temperature_residence:
        """

    def dispatch_target_component_type(self, field_name: str) -> Optional[lt.ComponentType]:
        """Reports which kind of participant one of this controller's outputs dispatches to.

        The KPIs below are each the grid share of one participant's dispatch, so they have to be
        able to tell the dispatch targets apart. A target's *name* cannot do that: it is chosen by
        whoever wired the participant in, and the two wiring paths choose differently — a Python
        setup passes a prefix it builds from the source class name, while an energy-system file
        derives ``DispatchTo<instance>_<input>`` from the participant's instance name. Sniffing for
        a class name therefore finds the target on one path and misses it on the other, which made
        the same house report fewer KPIs when it was built from a file than from a setup function.

        What both paths do agree on is the tags the target carries, because those come from the
        channel and from the participant's component type rather than from any name — and they
        are what :meth:`sort_source_weights_and_components` looks these very ports up by when it
        dispatches to them. Reading the tags is what makes these KPIs independent of the wiring
        path.

        Args:
            field_name: Name of the output being classified.

        Returns:
            The component type this output is the electricity target of, or None if the output is
            not one of this controller's electricity targets at all.
        """
        for dynamic_output in self.my_component_outputs:
            if dynamic_output.source_output_field_name != field_name:
                continue
            tags = dynamic_output.source_tags
            if lt.InandOutputType.ELECTRICITY_TARGET not in tags:
                return None
            for tag in tags:
                if isinstance(tag, lt.ComponentType):
                    return tag
            return None
        return None

    def dispatch_target_kpi_source(self, field_name: str) -> KpiSource:
        """The source of the participant one of this controller's dispatch outputs steers.

        The grid-share KPIs are reported on behalf of the participant, so they carry its
        :class:`KpiSource` and not the manager's. The participant is found the way the dispatch
        pairs it (:meth:`sort_source_weights_and_components`): the dispatch output and the input
        that measures the participant share one source weight and one component type. That input
        is connected to the participant's own output, which carries the participant's identity
        and display configuration, so the source is built exactly as the participant builds it.

        Args:
            field_name: The dispatch output's field name.

        Returns:
            The participant's source.

        Raises:
            ValueError: If the output is no dispatch output of this controller, if not exactly one
                input pairs with it, or if that input is not connected to a component's output.
        """
        dispatch_entries = [
            entry for entry in self.my_component_outputs if entry.source_output_field_name == field_name
        ]
        if len(dispatch_entries) != 1:
            raise ValueError(
                f"{self.component_name}: '{field_name}' is not exactly one dispatch output "
                f"({len(dispatch_entries)} found), so the participant it steers cannot be named."
            )
        dispatch = dispatch_entries[0]
        component_types = [tag for tag in dispatch.source_tags if isinstance(tag, lt.ComponentType)]
        paired = [
            entry
            for entry in self.my_component_inputs
            if entry.source_weight == dispatch.source_weight
            and entry.source_tags
            and entry.source_tags[0] in component_types
        ]
        if len(paired) != 1:
            raise ValueError(
                f"{self.component_name}: the dispatch output '{field_name}' (weight "
                f"{dispatch.source_weight}, {component_types}) pairs with {len(paired)} inputs "
                f"({[entry.source_component_class for entry in paired]}); exactly one input measuring "
                "the participant must share its weight and component type, or its KPI cannot name "
                "the participant it is reported for."
            )
        measuring_input: ComponentInput = getattr(self, paired[0].source_component_class)
        participant_output = measuring_input.source_output
        if participant_output is None or participant_output.display_config is None:
            raise ValueError(
                f"{self.component_name}: the input '{measuring_input.field_name}' that pairs with the "
                f"dispatch output '{field_name}' is not connected to a component's output "
                f"(source '{measuring_input.src_object_name}.{measuring_input.src_field_name}'), so "
                "the participant's identity is unknown."
            )
        return KpiSource.for_component(participant_output.component_id, participant_output.display_config)

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list.

        The grid share of each participant's dispatch is reported on behalf of that participant
        and carries its source (:meth:`dispatch_target_kpi_source`); the priorities are the
        manager's own.
        """
        # What each participant draws from the grid, keyed by the component type its electricity
        # target carries — the same key the dispatch itself steers by — and not by the class name
        # one of the two wiring paths happens to spell into the port's name. The value is the KPI's
        # name; the entry is reported on behalf of the participant, whose source is read off the
        # input the dispatch is paired with (dispatch_target_kpi_source).
        kpi_by_dispatch_target = {
            lt.ComponentType.HEAT_PUMP_BUILDING: "Space heating heat pump electricity from grid",
            lt.ComponentType.HEAT_PUMP_DHW: "Domestic hot water heat pump electricity from grid",
            lt.ComponentType.RESIDENTS: "Residents' electricity consumption from grid",
            lt.ComponentType.ELECTRIC_HEATING_SH: "Space heating electric heater electricity from grid",
            lt.ComponentType.ELECTRIC_HEATING_DHW: "Domestic hot water electric heater electricity from grid",
            lt.ComponentType.SOLAR_THERMAL_SYSTEM: "Domestic hot water solar thermal system electricity from grid",
            lt.ComponentType.CAR_BATTERY: "Electric car electricity consumption from grid",
        }

        list_of_kpi_entries: List[KpiEntry] = []
        for index, output in enumerate(all_outputs):
            if output.component_name != self.component_name or output.unit != lt.Units.WATT:
                continue
            dispatch_target = self.dispatch_target_component_type(output.field_name)
            if dispatch_target is None or dispatch_target not in kpi_by_dispatch_target:
                continue
            kpi_name = kpi_by_dispatch_target[dispatch_target]

            # A negative dispatch is what the participant had to take from the grid.
            electricity_from_grid_in_watt_series = postprocessing_results.iloc[:, index].loc[
                postprocessing_results.iloc[:, index] < 0.0
            ]
            electricity_from_grid_in_kilowatt_hour = abs(
                KpiHelperClass.compute_total_energy_from_power_timeseries(
                    power_timeseries_in_watt=electricity_from_grid_in_watt_series,
                    time_resolution_in_seconds=self.my_simulation_parameters.seconds_per_timestep,
                )
            )
            list_of_kpi_entries.append(
                KpiEntry(
                    name=kpi_name,
                    unit="kWh",
                    value=electricity_from_grid_in_kilowatt_hour,
                    tag=KpiTagEnumClass.ENERGY_MANAGEMENT_SYSTEM,
                    description=self.component_name,
                    source=self.dispatch_target_kpi_source(output.field_name),
                )
            )

        # add all source weights to KPIs; these are the manager's own, an input of its own each,
        # so component_kpi_entries stamps the manager's source on them.
        for index, input_sorted in enumerate(self.inputs_sorted):
            list_of_kpi_entries.append(KpiEntry(
                name=f"Priority for {input_sorted.field_name}",
                unit="-",
                value=index,
                tag=KpiTagEnumClass.ENERGY_MANAGEMENT_SYSTEM,
                description=self.component_name,
            ))

        return list_of_kpi_entries

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> cp.OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        # opex energy costs and co2 emissions are covered by electricity meter
        opex_cost_data_class = cp.OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=0,
            total_consumption_in_kwh=0,
            loadtype=lt.LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.ENERGY_MANAGEMENT_SYSTEM,
        )
        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(
        config: EMSConfig, simulation_parameters: SimulationParameters
    ) -> cp.CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        component_type = lt.ComponentType.ENERGY_MANAGEMENT_SYSTEM
        kpi_tag = KpiTagEnumClass.ENERGY_MANAGEMENT_SYSTEM
        unit = lt.Units.ANY
        size_of_energy_system = 1

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
