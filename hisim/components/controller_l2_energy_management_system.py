"""Iterative Energy Surplus Controller.

It received the electricity consumption
of all components and the PV production. According to the balance it
sends activation/deactivation siganls to components.
The component with the lowest source weight is activated first.
"""

import dataclasses
from dataclasses import dataclass, field

from types import MappingProxyType
from typing import Any, ClassVar, Dict, List, Mapping, NamedTuple, Tuple, Optional, cast
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
    # Raise of the building's heating set temperature while PV surplus is available, a temperature difference in K:
    # the value of the output BuildingTemperatureModifierInKelvin, also in K, while the raise is on.
    # Must be smaller than difference of set_heating_temperature and set_cooling_temperature.
    # The two storage offsets below are declared in °C, the unit of the storage modifier outputs they become.
    building_indoor_temperature_offset_in_kelvin: float = field(default=2, metadata={"unit": lt.Units.KELVIN})
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
    storage while generated electricity is left over after the battery has charged. With a space-heating heat pump
    the raise is PV mode (:meth:`L2GenericEnergyManagementSystem.runs_pv_mode`), which switches on and off at two
    thresholds; without one, close to zero surplus the raise keeps the decision of the step before
    (:meth:`L2GenericEnergyManagementSystem.raises_set_temperatures`). Either way that decision is the one value the
    manager carries from step to step.

    The manager keeps two of them. ``previous_state`` is the start of the step: a pass reads only it, and no pass
    changes it. ``state`` is the end of the step: every unforced pass overwrites it with its decision, a forced pass
    leaves it alone, and ``i_restore_state`` does not reset it. ``i_save_state`` makes it the next step's start. So the
    step ends with the decision of its last unforced pass, which is the decision the held outputs of a forced pass
    show. For example, if the last unforced pass raises the set temperatures, the modifiers stay raised through
    the forced passes and the next step starts from a raise, whatever surplus the forced passes see.
    """

    #: Whether the set temperatures are raised, in the step this state ends.
    set_temperatures_are_raised: bool = False


@dataclass(frozen=True)
class BatteryChargingLimitInputs:
    """The two inputs that carry one battery's charging limits for the current step to the energy manager.

    The manager pairs them with the battery's own input by the name of the component they come from.
    """

    #: The largest power the battery can take in the step, in W.
    maximum_charging_power_in_watt: ComponentInput
    #: The smallest power the battery charges with, in W.
    minimum_charging_power_in_watt: ComponentInput


@dataclass(frozen=True)
class SurplusAfterParticipants:
    """What is left of the electricity balance after every ranked participant has taken its share, in W.

    Positive means electricity goes to the grid. The two values differ in how a battery is counted. The grid balance
    books its realized power; the decision surplus counts it only as a sink: its charging share in a surplus, and
    nothing in a deficit.
    """

    #: The balance with every participant's realized power, the battery's included; the manager publishes it.
    grid_balance_in_watt: float
    #: The balance with each battery counted only as a sink: it takes from a surplus what its charging limits for the
    #: step allow, and it covers no deficit. Without a space-heating heat pump the manager decides the raise on it.
    surplus_after_battery_in_watt: float
    #: The raise's budget: the surplus after the battery, with the heat pump's own need in place of its draw
    #: (:meth:`L2GenericEnergyManagementSystem.raise_budget_after_participant_in_watt`). With a space-heating heat
    #: pump the manager decides PV mode on it; without one it is not read.
    raise_budget_in_watt: float


class RaiseDecision(NamedTuple):
    """The energy manager's raise decision of one pass: whether it raises, and the heat pump's power target, in W."""

    #: Whether the set temperatures are raised (PV mode, with a space-heating heat pump).
    set_temperatures_are_raised: bool
    #: The space-heating heat pump's electrical power target, in W; 0 outside PV mode and without such a heat pump.
    power_target_in_watt: float


@dataclass(frozen=True)
class HeatPumpInformationInputs:
    """The inputs that tell the energy manager what its space-heating heat pump can draw and would draw on its own.

    The heat pump publishes its full-load and minimum-load space-heating electricity, its space-heating controller
    the own need's electricity, and its hot-water controller, where the heat pump makes hot water, whether its charge
    is its own need. The manager finds them on its channels at the first step
    (:meth:`L2GenericEnergyManagementSystem.pair_heat_pump_information_inputs`).
    """

    #: The heat pump's space-heating electricity at full load, in W.
    full_load_electrical_power_in_watt: ComponentInput
    #: The heat pump's space-heating electricity at its minimum load, in W.
    minimum_load_electrical_power_in_watt: ComponentInput
    #: The space-heating electricity the controller would run without the raise, in W.
    own_need_electrical_power_in_watt: ComponentInput
    #: 1 while the hot-water charge is the hot-water controller's own need; None without a hot-water participant.
    hot_water_charge_is_own_need: Optional[ComponentInput]
    #: The heat pump's hot-water draw, the ranked hot-water participant's input; None without one.
    hot_water_draw_in_watt: Optional[ComponentInput]


class L2GenericEnergyManagementSystem(dynamic_component.DynamicComponent, cp.L2ControllerComponent):
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

    #: The margin of PV mode's two thresholds around the budget the heat pump's minimum load needs, in W
    #: (:meth:`runs_pv_mode`). Owner decision of 2026-11-07 (hisim-ems-raise-within-step-er89), from the measurement of
    #: that day: PV mode switches on at the minimum load's extra draw plus 100 W and off below it minus 100 W.
    PV_MODE_MARGIN_IN_WATT: ClassVar[float] = 100.0

    # Inputs
    ElectricityToElectrolyzerUnused = "ElectricityToElectrolyzerUnused"
    ElectricityToBuildingFromDistrict = "ElectricityToBuildingFromDistrict"

    # Outputs
    ElectricityToElectrolyzerTarget = "ElectricityToElectrolyzerTarget"

    TotalElectricityToOrFromGrid = "TotalElectricityToOrFromGrid"
    TotalElectricityConsumption = "TotalElectricityConsumption"
    BuildingTemperatureModifierInKelvin = "BuildingTemperatureModifierInKelvin"  # read by Building, heating curve
    DomesticHotWaterStorageTemperatureModifier = (
        "DomesticHotWaterStorageTemperatureModifier"  # used for L1HeatPumpController  # Todo: change name?
    )
    SpaceHeatingWaterStorageTemperatureModifier = (
        "SpaceHeatingWaterStorageTemperatureModifier"  # used for HeatPumpHplibController
    )
    ElectricityToBuildingFromDistrictEMSOutput = "ElectricityToBuildingFromDistrictEMSOutput"
    #: The electrical power target for the space-heating heat pump in PV mode, 0 outside it; read by its controller.
    HeatPumpElectricalPowerTargetInWatt = "HeatPumpElectricalPowerTargetInWatt"

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

    #: Stable key of the channel carrying the largest power a battery can take in the current step.
    STORAGE_MAXIMUM_CHARGING_POWER_CHANNEL = "storage_maximum_charging_power"

    #: Stable key of the channel carrying the smallest power a battery charges with.
    STORAGE_MINIMUM_CHARGING_POWER_CHANNEL = "storage_minimum_charging_power"

    #: Stable key of the channel carrying the space-heating heat pump's electricity at full load.
    HEAT_PUMP_FULL_LOAD_CHANNEL = "heat_pump_full_load_electricity"

    #: Stable key of the channel carrying the space-heating heat pump's electricity at its minimum load.
    HEAT_PUMP_MINIMUM_LOAD_CHANNEL = "heat_pump_minimum_load_electricity"

    #: Stable key of the channel carrying the space-heating electricity the heat pump's controller would run without
    #: the raise.
    HEAT_PUMP_OWN_NEED_CHANNEL = "heat_pump_own_need_electricity"

    #: Stable key of the channel carrying whether the heat pump's hot-water charge is its controller's own need.
    HOT_WATER_OWN_CHARGE_CHANNEL = "hot_water_own_need_charge"

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
        DynamicConnectionChannel(
            key=STORAGE_MAXIMUM_CHARGING_POWER_CHANNEL,
            tags=frozenset({lt.ComponentType.BATTERY, lt.InandOutputType.MAXIMUM_CHARGING_POWER}),
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            dispatch=DispatchRule.FORBIDDEN,
        ),
        DynamicConnectionChannel(
            key=STORAGE_MINIMUM_CHARGING_POWER_CHANNEL,
            tags=frozenset({lt.ComponentType.BATTERY, lt.InandOutputType.MINIMUM_CHARGING_POWER}),
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            dispatch=DispatchRule.FORBIDDEN,
        ),
        # What the space-heating heat pump can draw and would draw on its own: measured signals, no participants.
        DynamicConnectionChannel(
            key=HEAT_PUMP_FULL_LOAD_CHANNEL,
            tags=frozenset({lt.ComponentType.HEAT_PUMP_BUILDING, lt.InandOutputType.FULL_LOAD_ELECTRICITY}),
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            dispatch=DispatchRule.FORBIDDEN,
        ),
        DynamicConnectionChannel(
            key=HEAT_PUMP_MINIMUM_LOAD_CHANNEL,
            tags=frozenset({lt.ComponentType.HEAT_PUMP_BUILDING, lt.InandOutputType.MINIMUM_LOAD_ELECTRICITY}),
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            dispatch=DispatchRule.FORBIDDEN,
        ),
        DynamicConnectionChannel(
            key=HEAT_PUMP_OWN_NEED_CHANNEL,
            tags=frozenset({lt.ComponentType.HEAT_PUMP_BUILDING, lt.InandOutputType.OWN_NEED_ELECTRICITY}),
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            dispatch=DispatchRule.FORBIDDEN,
        ),
        DynamicConnectionChannel(
            key=HOT_WATER_OWN_CHARGE_CHANNEL,
            tags=frozenset({lt.ComponentType.HEAT_PUMP_DHW, lt.InandOutputType.OWN_NEED_CHARGE}),
            load_type=lt.LoadTypes.ANY,
            unit=lt.Units.BINARY,
            dispatch=DispatchRule.FORBIDDEN,
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
        #: Each battery's charging-limit inputs, by the name of the battery component; paired at the first step.
        self.battery_charging_limit_inputs: Dict[str, BatteryChargingLimitInputs] = {}
        #: The space-heating heat pump's information inputs, paired at the first step; None without such a heat pump,
        #: and the raise is then decided on the surplus after the battery alone.
        self.heat_pump_information: Optional[HeatPumpInformationInputs] = None

        self.component_types_sorted: List[lt.ComponentType] = []
        self.inputs_sorted: List[ComponentInput] = []
        self.outputs_sorted: List[ComponentOutput] = []
        self.production_inputs: List[ComponentInput] = []
        self.consumption_uncontrolled_inputs: List[ComponentInput] = []
        self.consumption_ems_controlled_inputs: List[ComponentInput] = []

        self.operating_mode: Any
        self.strategy = self.ems_config.strategy
        self.limit_to_shave = self.ems_config.limit_to_shave
        self.building_indoor_temperature_offset_in_kelvin = self.ems_config.building_indoor_temperature_offset_in_kelvin
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
            field_name=self.BuildingTemperatureModifierInKelvin,
            load_type=lt.LoadTypes.TEMPERATURE,
            unit=lt.Units.KELVIN,
            output_description=(
                "The raise of the building's heating set temperature, a temperature difference in K. It equals "
                "the configured building offset (building_indoor_temperature_offset_in_kelvin) while the PV "
                "surplus after the battery raises the set temperatures and a heat pump heating the building "
                "(HEAT_PUMP_BUILDING) is among the ranked participants, else 0. The building and the heating curve "
                "add it to the room set temperature."
            ),
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

        self.heat_pump_electrical_power_target: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.HeatPumpElectricalPowerTargetInWatt,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            output_description=(
                "The electrical power the space-heating heat pump is to draw in PV mode, in W, 0 outside it: the "
                "heat pump's own need plus the raise's budget, kept between its minimum-load and full-load "
                "electricity (pv_mode_power_target_in_watt). The heat pump's space-heating controller turns it into "
                "a part-load ratio. A signal, not a draw."
            ),
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
        self.add_dynamic_default_connections(self.get_default_connections_from_heat_pump_controller_space_heating())
        self.add_dynamic_default_connections(self.get_default_connections_from_heat_pump_controller_dhw())
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

        from hisim.components.more_advanced_heat_pump.heat_pump import (  # pylint: disable=import-outside-toplevel
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
        # What the heat pump can draw for space heating, measured only: PV mode's thresholds and target read them.
        for field_name, information_tag in (
            (MoreAdvancedHeatPumpHPLib.ElectricalPowerAtFullLoadSpaceHeatingInWatt, lt.InandOutputType.FULL_LOAD_ELECTRICITY),
            (
                MoreAdvancedHeatPumpHPLib.ElectricalPowerAtMinimumLoadSpaceHeatingInWatt,
                lt.InandOutputType.MINIMUM_LOAD_ELECTRICITY,
            ),
        ):
            dynamic_connections.append(
                dynamic_component.DynamicComponentConnection(
                    source_component_class=MoreAdvancedHeatPumpHPLib,
                    source_class_name=more_advanced_heat_pump_class_name,
                    source_component_field_name=field_name,
                    source_load_type=lt.LoadTypes.ELECTRICITY,
                    source_unit=lt.Units.WATT,
                    source_tags=[lt.ComponentType.HEAT_PUMP_BUILDING, information_tag],
                    source_weight=DynamicConnectionChannel.MONITORED_ONLY_WEIGHT,
                )
            )
        return dynamic_connections

    def get_default_connections_from_heat_pump_controller_space_heating(self) -> List[dynamic_component.DynamicComponentConnection]:
        """Return the connection of the own need's electricity from the heat pump's space-heating controller.

        Measured only: the raise's budget counts the own need in place of the heat pump's draw
        (:meth:`raise_budget_after_participant_in_watt`).

        Returns:
            The one connection of ``OwnNeedElectricalPowerInWatt``.
        """
        from hisim.components.more_advanced_heat_pump.controller_space_heating import (  # pylint: disable=import-outside-toplevel
            MoreAdvancedHeatPumpHPLibControllerSpaceHeating,
        )

        return [
            dynamic_component.DynamicComponentConnection(
                source_component_class=MoreAdvancedHeatPumpHPLibControllerSpaceHeating,
                source_class_name=MoreAdvancedHeatPumpHPLibControllerSpaceHeating.get_classname(),
                source_component_field_name=MoreAdvancedHeatPumpHPLibControllerSpaceHeating.OwnNeedElectricalPowerInWatt,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[lt.ComponentType.HEAT_PUMP_BUILDING, lt.InandOutputType.OWN_NEED_ELECTRICITY],
                source_weight=DynamicConnectionChannel.MONITORED_ONLY_WEIGHT,
            )
        ]

    def get_default_connections_from_heat_pump_controller_dhw(self) -> List[dynamic_component.DynamicComponentConnection]:
        """Return the connection of whether the hot-water charge is the own need, from the heat pump's hot-water controller.

        Measured only: while the charge is the own need, the heat pump's hot-water draw is the own need
        (:meth:`heat_pump_own_need_in_watt`).

        Returns:
            The one connection of ``ChargesWithoutRaise``.
        """
        from hisim.components.more_advanced_heat_pump.controller_dhw import (  # pylint: disable=import-outside-toplevel
            MoreAdvancedHeatPumpHPLibControllerDHW,
        )

        return [
            dynamic_component.DynamicComponentConnection(
                source_component_class=MoreAdvancedHeatPumpHPLibControllerDHW,
                source_class_name=MoreAdvancedHeatPumpHPLibControllerDHW.get_classname(),
                source_component_field_name=MoreAdvancedHeatPumpHPLibControllerDHW.ChargesWithoutRaise,
                source_load_type=lt.LoadTypes.ANY,
                source_unit=lt.Units.BINARY,
                source_tags=[lt.ComponentType.HEAT_PUMP_DHW, lt.InandOutputType.OWN_NEED_CHARGE],
                source_weight=DynamicConnectionChannel.MONITORED_ONLY_WEIGHT,
            )
        ]

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
        # The battery's charging limits are measured only; the controller pairs them with the battery by its name.
        for field_name, limit_tag in (
            (Battery.MaximumChargingPowerInWatt, lt.InandOutputType.MAXIMUM_CHARGING_POWER),
            (Battery.MinimumChargingPowerInWatt, lt.InandOutputType.MINIMUM_CHARGING_POWER),
        ):
            dynamic_connections.append(
                dynamic_component.DynamicComponentConnection(
                    source_component_class=Battery,
                    source_class_name=advanced_battery_class_name,
                    source_component_field_name=field_name,
                    source_load_type=lt.LoadTypes.ELECTRICITY,
                    source_unit=lt.Units.WATT,
                    source_tags=[lt.ComponentType.BATTERY, limit_tag],
                    source_weight=DynamicConnectionChannel.MONITORED_ONLY_WEIGHT,
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
        """Make the decision the step ends with, that of its last unforced pass, the next step's start."""
        self.previous_state = dataclasses.replace(self.state)

    def i_restore_state(self) -> None:
        """Restore nothing: a pass reads only the start-of-step state, which no pass changes.

        The end-of-step state is not reset either. An unforced pass overwrites it; under forced convergence the
        manager holds its decision, so the step ends with the decision of its last unforced pass, the one its held
        set-temperature modifiers show (:class:`EMSState`).
        """

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

        The signal a participant gets depends on what it does with it. The house battery decides its power from
        the signal, and so does an electric car at or above its set point; below it the car charges at full
        station power whatever it is offered. Both get the surplus offered to them, before their own share is
        subtracted. Every other consumer gets what is left after its own consumption, negative for the part it
        takes from the grid; that signal only books the consumer's grid share. For example, with 3,000 W of
        surplus left, a car above its set point is offered 3,000 W, charges at 3,000 W and leaves 0 W; a heat
        pump after it that draws 2,000 W is then sent 0 - 2,000 = -2,000 W, the 2,000 W it takes from the grid,
        and leaves -2,000 W.

        Why the car gets the offer: at or above its set point its charge controller charges with the signal. Sent
        the surplus left after its own charging, it would answer every pass with that remainder, the next pass
        would send it the surplus minus that remainder, and its charging would alternate between the passes of a
        step.

        Args:
            available_surplus_electricity_in_watt: The surplus left before this participant, in W.
            stsv: The step values the participant's consumption is read from and its signal written to.
            current_component_type: The participant's component type.
            current_input: The input measuring the participant's consumption.
            current_output: The participant's signal output.

        Returns:
            The surplus left after this participant, in W.
        """
        # get electricity demand from input component and substract from (or add to) available surplus electricity
        electricity_demand_from_current_input_component_in_watt = stsv.get_input_value(component_input=current_input)

        # The house battery, and the electric car at or above its set point, decide their power from the signal,
        # so both get the surplus offered to them (negative: the deficit the battery may cover); what they really
        # draw is subtracted. Below its set point the car charges at full station power whatever it is offered.
        if current_component_type in (lt.ComponentType.BATTERY, lt.ComponentType.CAR_BATTERY):
            stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)
            available_surplus_electricity_in_watt = (
                available_surplus_electricity_in_watt - electricity_demand_from_current_input_component_in_watt
            )

        # these are electricity CONSUMERS
        elif current_component_type in [
            lt.ComponentType.RESIDENTS,
            lt.ComponentType.ELECTROLYZER,
            lt.ComponentType.SMART_DEVICE,
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

        This is the decision without a space-heating heat pump, whose own need the manager could not tell from the
        raise's effect; with one the raise is PV mode (:meth:`runs_pv_mode`). The surplus after the battery is the
        electricity left over once every participant has taken its share, a battery counted only as a sink: it takes
        from a surplus what its charging limits for the step allow, and it covers no deficit
        (:meth:`battery_charging_share_in_watt`). Positive means generated electricity (PV in
        the household twins) would go to the grid. So only generated electricity keeps a raise: when keeping it needs
        the battery to discharge, the surplus is negative and the raise ends.

        The transitions, from the decision of the previous step:

        * surplus above ``SURPLUS_HYSTERESIS_BAND_IN_WATT``: raise;
        * surplus below minus the band: do not raise;
        * surplus within the band, edges included: keep the previous decision.

        For example, with the band of 0.01 W, a surplus of 5e-12 W keeps the previous decision, a surplus of 0.5 W
        raises, and a surplus of -0.5 W does not.

        Why: a battery that takes the whole surplus leaves zero up to float noise, and a decision on the sign alone
        would switch the raise on and off between the passes of one step.

        Args:
            surplus_after_battery_in_watt: The surplus left after every participant, the battery counted only as a
                sink, in W.
            raised_in_previous_step: Whether the set temperatures were raised in the previous step.

        Returns:
            Whether the set temperatures are raised in this pass.
        """
        band_in_watt = L2GenericEnergyManagementSystem.SURPLUS_HYSTERESIS_BAND_IN_WATT
        if surplus_after_battery_in_watt > band_in_watt:
            return True
        if surplus_after_battery_in_watt < -band_in_watt:
            return False
        return raised_in_previous_step

    @staticmethod
    def battery_charging_share_in_watt(
        *, surplus_in_watt: float, maximum_charging_power_in_watt: float, minimum_charging_power_in_watt: float
    ) -> float:
        """Return the power a battery takes from the surplus offered to it, from its charging limits for the step, in W.

        The battery takes the surplus up to its maximum charging power, and nothing when that share is at or below its
        minimum charging power. A deficit (a surplus of zero or less) gives no share: the battery counts only as a
        sink. For example, with limits of 5,000 W and 200 W, a surplus of 1,000 W gives 1,000 W, 8,000 W give
        5,000 W, 150 W give 0 W, and -300 W give 0 W.

        Why: the share follows from the limits the battery publishes from its state at the step's start, not from
        its answer to an earlier pass's offer, so the decision of a pass does not depend on the pass before.

        Args:
            surplus_in_watt: The surplus offered to the battery, in W; positive means electricity left over.
            maximum_charging_power_in_watt: The largest power the battery can take in the step, in W.
            minimum_charging_power_in_watt: The smallest power the battery charges with, in W.

        Returns:
            The power the battery takes, in W; zero or positive.

        Raises:
            ValueError: If a charging limit is negative.
        """
        if maximum_charging_power_in_watt < 0 or minimum_charging_power_in_watt < 0:
            raise ValueError(
                "A battery's charging limits must not be negative, got a maximum of "
                f"{maximum_charging_power_in_watt} W and a minimum of {minimum_charging_power_in_watt} W."
            )
        if surplus_in_watt <= 0:
            return 0.0
        share_in_watt = min(surplus_in_watt, maximum_charging_power_in_watt)
        return share_in_watt if share_in_watt > minimum_charging_power_in_watt else 0.0

    @staticmethod
    def raise_budget_after_participant_in_watt(
        *,
        budget_in_watt: float,
        component_type: lt.ComponentType,
        realized_change_in_watt: float,
        heat_pump_own_need_in_watt: float,
    ) -> float:
        """Return the raise's budget after one ranked participant that is not a battery, in W.

        The budget is the surplus after the battery with the space-heating heat pump's own need in place of its draw:
        what the raise may use once the heat pump's own need and the battery are served (battery first, owner decision
        of 2026-11-07). It follows the surplus through the ranking:

        | participant | budget after it |
        |---|---|
        | the space-heating heat pump | the budget less the heat pump's own need |
        | the heat pump's hot water | the budget, unchanged: its own part is in the own need |
        | any other | the budget plus its realized change (negative for a consumer) |

        A battery takes its charging share of the budget (:meth:`battery_charging_share_in_watt`). For example, a budget
        of 3,000 W before a heat pump that draws 2,500 W in PV mode with an own need of 500 W leaves 2,500 W; the
        surplus itself is left at 500 W.

        Why the own need: the heat pump's draw includes what the raise makes it draw, so a budget with the draw in it
        falls when the raise comes on and rises when it goes off, and a decision on it flips between the passes of a
        step. The own need does not contain the raise.

        Args:
            budget_in_watt: The budget before the participant, in W.
            component_type: The participant's component type.
            realized_change_in_watt: The change of the surplus this participant's realized power makes, in W.
            heat_pump_own_need_in_watt: The heat pump's own need (:meth:`heat_pump_own_need_in_watt`), in W.

        Returns:
            The budget after the participant, in W.
        """
        if component_type == lt.ComponentType.HEAT_PUMP_BUILDING:
            return budget_in_watt - heat_pump_own_need_in_watt
        if component_type == lt.ComponentType.HEAT_PUMP_DHW:
            return budget_in_watt
        return budget_in_watt + realized_change_in_watt

    @staticmethod
    def heat_pump_own_need_in_watt(
        *, space_heating_own_need_in_watt: float, hot_water_draw_in_watt: float, hot_water_charge_is_own_need: bool
    ) -> float:
        """Return the electricity the heat pump draws without the raise, its own need, in W.

        While the hot-water charge is the hot-water controller's own need, the heat pump draws for it and space heating
        waits, so the own need is the hot-water draw. Otherwise it is the space-heating controller's own need; a charge
        only the raise starts is not own need. For example, a hot-water draw of 2,000 W and a space-heating own need of
        800 W give 2,000 W on an own charge and 800 W on a charge the raise started.

        Args:
            space_heating_own_need_in_watt: The space-heating electricity the controller would run without the raise,
                in W.
            hot_water_draw_in_watt: The heat pump's hot-water draw in this pass, in W.
            hot_water_charge_is_own_need: Whether the hot-water controller would run its charge without the raise.

        Returns:
            The own need, in W, zero or positive.

        Raises:
            ValueError: If one of the two powers is negative.
        """
        if space_heating_own_need_in_watt < 0.0 or hot_water_draw_in_watt < 0.0:
            raise ValueError(
                "The heat pump's own need and hot-water draw must not be negative, got "
                f"{space_heating_own_need_in_watt} W and {hot_water_draw_in_watt} W."
            )
        return hot_water_draw_in_watt if hot_water_charge_is_own_need else space_heating_own_need_in_watt

    @staticmethod
    def runs_pv_mode(
        *,
        raise_budget_in_watt: float,
        own_need_in_watt: float,
        minimum_load_electrical_power_in_watt: float,
        pv_mode_in_previous_step: bool,
    ) -> bool:
        """Return whether the energy manager runs PV mode, the raise of the set temperatures, from its budget.

        PV mode needs the heat pump to run at least at its minimum load. Beyond its own need, that takes
        ``extra = max(minimum load - own need, 0)``. PV mode switches on when the budget reaches ``extra`` plus
        :attr:`PV_MODE_MARGIN_IN_WATT` and stays on while it is at least ``extra`` minus the margin, but never on less
        than :attr:`SURPLUS_HYSTERESIS_BAND_IN_WATT`, so PV mode never runs on grid electricity or on the battery. The
        memory is the previous step's decision. For example, with a 600 W minimum load and no own need, PV mode
        switches on at a budget of 700 W and off below 500 W; with an own need of 800 W, on at 100 W and off below
        0.01 W.

        Why the budget, not the surplus: the budget does not contain the raise's own extra draw
        (:meth:`raise_budget_after_participant_in_watt`), so within a step the decision does not move with its own
        effect; the band keeps it from switching on every crossing between steps.

        Args:
            raise_budget_in_watt: The raise's budget, in W.
            own_need_in_watt: The heat pump's own need, in W (:meth:`heat_pump_own_need_in_watt`).
            minimum_load_electrical_power_in_watt: The heat pump's electricity at its minimum load, in W.
            pv_mode_in_previous_step: Whether PV mode was on in the previous step.

        Returns:
            Whether PV mode is on in this pass.
        """
        extra_in_watt = max(minimum_load_electrical_power_in_watt - own_need_in_watt, 0.0)
        margin_in_watt: float = L2GenericEnergyManagementSystem.PV_MODE_MARGIN_IN_WATT
        threshold_in_watt = extra_in_watt + margin_in_watt
        if pv_mode_in_previous_step:
            least_in_watt: float = L2GenericEnergyManagementSystem.SURPLUS_HYSTERESIS_BAND_IN_WATT
            threshold_in_watt = max(extra_in_watt - margin_in_watt, least_in_watt)
        return bool(raise_budget_in_watt >= threshold_in_watt)

    @staticmethod
    def pv_mode_power_target_in_watt(
        *,
        own_need_in_watt: float,
        raise_budget_in_watt: float,
        minimum_load_electrical_power_in_watt: float,
        full_load_electrical_power_in_watt: float,
    ) -> float:
        """Return the space-heating heat pump's electrical power target in PV mode, in W.

        The heat pump is to draw its own need plus the raise's budget, but at least its minimum load and at most its
        full load. For example, an own need of 300 W, a budget of 900 W, a 450 W minimum and 1,500 W at full load
        give 1,200 W; a budget of 2,000 W gives 1,500 W; no own need and a budget of 300 W give 450 W.

        Args:
            own_need_in_watt: The heat pump's own need, in W.
            raise_budget_in_watt: The raise's budget, in W.
            minimum_load_electrical_power_in_watt: The heat pump's electricity at its minimum load, in W.
            full_load_electrical_power_in_watt: The heat pump's electricity at full load, in W.

        Returns:
            The target, in W, between the minimum and the full load.

        Raises:
            ValueError: If the minimum load is negative or above the full load.
        """
        if not 0.0 <= minimum_load_electrical_power_in_watt <= full_load_electrical_power_in_watt:
            raise ValueError(
                "The heat pump's minimum-load electricity must lie between 0 W and its full-load electricity, got "
                f"{minimum_load_electrical_power_in_watt} W and {full_load_electrical_power_in_watt} W."
            )
        return min(max(own_need_in_watt + raise_budget_in_watt, minimum_load_electrical_power_in_watt), full_load_electrical_power_in_watt)

    def pair_heat_pump_information_inputs(
        self, inputs_sorted: List[ComponentInput], component_types_sorted: List[lt.ComponentType]
    ) -> Optional[HeatPumpInformationInputs]:
        """Return the space-heating heat pump's information inputs, or None when no such heat pump is ranked.

        A ranked space-heating heat pump (``HEAT_PUMP_BUILDING``) needs its full-load and minimum-load electricity and
        its controller's own need, one input each; a ranked hot-water heat pump beside it needs the hot-water
        controller's own-charge signal. The manager then decides the raise as PV mode (:meth:`runs_pv_mode`).

        Args:
            inputs_sorted: The participants' inputs, ranked.
            component_types_sorted: The participants' component types, in the order of ``inputs_sorted``.

        Returns:
            The information inputs, or None without a ranked space-heating heat pump.

        Raises:
            ValueError: If more than one space-heating or hot-water heat pump is ranked, if an information input of a
                ranked heat pump is missing or wired twice, or if an own-charge signal comes without a hot-water heat
                pump.
        """
        ranked = {
            component_type: [item for item, item_type in zip(inputs_sorted, component_types_sorted) if item_type == component_type]
            for component_type in (lt.ComponentType.HEAT_PUMP_BUILDING, lt.ComponentType.HEAT_PUMP_DHW)
        }
        space_heating = ranked[lt.ComponentType.HEAT_PUMP_BUILDING]
        hot_water = ranked[lt.ComponentType.HEAT_PUMP_DHW]
        own_charge = self.get_channel_inputs(self.HOT_WATER_OWN_CHARGE_CHANNEL)
        if not space_heating:
            return None
        found = {
            key: self.get_channel_inputs(key)
            for key in (self.HEAT_PUMP_FULL_LOAD_CHANNEL, self.HEAT_PUMP_MINIMUM_LOAD_CHANNEL, self.HEAT_PUMP_OWN_NEED_CHANNEL)
        }
        expected_own_charge_count = 1 if hot_water else 0
        missing = [key for key, items in found.items() if len(items) != 1]
        if len(space_heating) > 1 or len(hot_water) > 1 or missing or len(own_charge) != expected_own_charge_count:
            raise ValueError(
                f"{self.component_name}: PV mode needs one ranked space-heating heat pump ({len(space_heating)} found), "
                f"at most one hot-water heat pump ({len(hot_water)} found), exactly one input on each of the channels "
                f"{list(found)} (missing or doubled: {missing}) and {expected_own_charge_count} on "
                f"'{self.HOT_WATER_OWN_CHARGE_CHANNEL}' ({len(own_charge)} found). Wire the heat pump's "
                "ElectricalPowerAtFullLoadSpaceHeatingInWatt and ElectricalPowerAtMinimumLoadSpaceHeatingInWatt, its "
                "space-heating controller's OwnNeedElectricalPowerInWatt and, with hot water, its hot-water controller's "
                "ChargesWithoutRaise to the energy manager: it decides the raise from them."
            )
        return HeatPumpInformationInputs(
            full_load_electrical_power_in_watt=found[self.HEAT_PUMP_FULL_LOAD_CHANNEL][0],
            minimum_load_electrical_power_in_watt=found[self.HEAT_PUMP_MINIMUM_LOAD_CHANNEL][0],
            own_need_electrical_power_in_watt=found[self.HEAT_PUMP_OWN_NEED_CHANNEL][0],
            hot_water_charge_is_own_need=own_charge[0] if own_charge else None,
            hot_water_draw_in_watt=hot_water[0] if hot_water else None,
        )

    def read_heat_pump_own_need_in_watt(self, stsv: cp.SingleTimeStepValues) -> float:
        """Return the heat pump's own need as read in this pass, in W; 0 without a space-heating heat pump.

        Args:
            stsv: The step's values.

        Returns:
            The own need (:meth:`heat_pump_own_need_in_watt`), in W.
        """
        information = self.heat_pump_information
        if information is None:
            return 0.0
        hot_water_draw_in_watt = 0.0
        hot_water_charge_is_own_need = False
        if information.hot_water_draw_in_watt is not None and information.hot_water_charge_is_own_need is not None:
            hot_water_draw_in_watt = stsv.get_input_value(information.hot_water_draw_in_watt)
            hot_water_charge_is_own_need = stsv.get_input_value(information.hot_water_charge_is_own_need) > 0.5
        return self.heat_pump_own_need_in_watt(
            space_heating_own_need_in_watt=stsv.get_input_value(information.own_need_electrical_power_in_watt),
            hot_water_draw_in_watt=hot_water_draw_in_watt,
            hot_water_charge_is_own_need=hot_water_charge_is_own_need,
        )

    def decide_raise(
        self, stsv: cp.SingleTimeStepValues, surplus_after_participants: SurplusAfterParticipants, own_need_in_watt: float
    ) -> RaiseDecision:
        """Return the raise decision of an unforced pass, and the heat pump's power target.

        With a space-heating heat pump the raise is PV mode, decided on the raise's budget (:meth:`runs_pv_mode`), and
        the target is :meth:`pv_mode_power_target_in_watt` while it is on. Without one the raise is decided on the
        surplus after the battery (:meth:`raises_set_temperatures`), and there is no target.

        Args:
            stsv: The step's values, to read the heat pump's loads from.
            surplus_after_participants: What is left of the surplus after the ranked participants.
            own_need_in_watt: The heat pump's own need, in W.

        Returns:
            The decision and the target.
        """
        raised_in_previous_step = self.previous_state.set_temperatures_are_raised
        information = self.heat_pump_information
        if information is None:
            raised = self.raises_set_temperatures(
                surplus_after_battery_in_watt=surplus_after_participants.surplus_after_battery_in_watt,
                raised_in_previous_step=raised_in_previous_step,
            )
            return RaiseDecision(set_temperatures_are_raised=raised, power_target_in_watt=0.0)
        minimum_load_in_watt = stsv.get_input_value(information.minimum_load_electrical_power_in_watt)
        pv_mode = self.runs_pv_mode(
            raise_budget_in_watt=surplus_after_participants.raise_budget_in_watt,
            own_need_in_watt=own_need_in_watt,
            minimum_load_electrical_power_in_watt=minimum_load_in_watt,
            pv_mode_in_previous_step=raised_in_previous_step,
        )
        if not pv_mode:
            return RaiseDecision(set_temperatures_are_raised=False, power_target_in_watt=0.0)
        target_in_watt = self.pv_mode_power_target_in_watt(
            own_need_in_watt=own_need_in_watt,
            raise_budget_in_watt=surplus_after_participants.raise_budget_in_watt,
            minimum_load_electrical_power_in_watt=minimum_load_in_watt,
            full_load_electrical_power_in_watt=stsv.get_input_value(information.full_load_electrical_power_in_watt),
        )
        return RaiseDecision(set_temperatures_are_raised=True, power_target_in_watt=target_in_watt)

    def pair_battery_charging_limit_inputs(
        self, inputs_sorted: List[ComponentInput], component_types_sorted: List[lt.ComponentType]
    ) -> Dict[str, BatteryChargingLimitInputs]:
        """Return each ranked battery's charging-limit inputs, by the name of the battery component.

        Every battery the manager ranks publishes its maximum and minimum charging power for the step, and the
        manager reads them on two channels. An input is paired with a battery when it comes from the same component.

        Args:
            inputs_sorted: The participants' inputs, ranked.
            component_types_sorted: The participants' component types, in the order of ``inputs_sorted``.

        Returns:
            The limit inputs of every ranked battery, by the battery's component name.

        Raises:
            ValueError: If a ranked battery's maximum or minimum charging power is not wired to the manager, or wired
                twice.
        """
        limit_inputs_by_channel = {
            key: self.get_channel_inputs(key)
            for key in (self.STORAGE_MAXIMUM_CHARGING_POWER_CHANNEL, self.STORAGE_MINIMUM_CHARGING_POWER_CHANNEL)
        }
        paired: Dict[str, BatteryChargingLimitInputs] = {}
        for participant_input, component_type in zip(inputs_sorted, component_types_sorted):
            if component_type != lt.ComponentType.BATTERY:
                continue
            battery_name = participant_input.src_object_name
            found = {
                key: [item for item in inputs if item.src_object_name == battery_name]
                for key, inputs in limit_inputs_by_channel.items()
            }
            missing = [key for key, items in found.items() if len(items) != 1]
            if missing or battery_name is None:
                raise ValueError(
                    f"{self.component_name}: the battery '{battery_name}' needs exactly one input on each of the channels "
                    f"{missing}, got {[len(found[key]) for key in missing]}. Wire its "
                    "MaximumChargingPowerInWatt and MinimumChargingPowerInWatt to the energy manager: the manager "
                    "computes the battery's share of a surplus from them."
                )
            paired[battery_name] = BatteryChargingLimitInputs(
                maximum_charging_power_in_watt=found[self.STORAGE_MAXIMUM_CHARGING_POWER_CHANNEL][0],
                minimum_charging_power_in_watt=found[self.STORAGE_MINIMUM_CHARGING_POWER_CHANNEL][0],
            )
        return paired

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
                        self.building_indoor_temperature_offset_in_kelvin,
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
        heat_pump_own_need_in_watt: float = 0.0,
    ) -> SurplusAfterParticipants:
        """Hand the surplus to the participants by rank, send each its signal and return what is left.

        Every participant takes its share in the order of its weight (:meth:`control_electricity_component_iterative`).
        Beside the grid balance, which books every participant's realized power, the surplus the raise is decided on
        counts each battery by the share its charging limits allow (:meth:`battery_charging_share_in_watt`), and the
        raise's budget counts the space-heating heat pump by its own need as well
        (:meth:`raise_budget_after_participant_in_watt`).

        Args:
            available_surplus_electricity_in_watt: The production minus the uncontrolled consumption, in W.
            stsv: The step values the signals are written to.
            inputs_sorted: The participants' inputs, ranked.
            component_types_sorted: The participants' component types, in the order of ``inputs_sorted``.
            outputs_sorted: The participants' signal outputs, in the order of ``inputs_sorted``.
            heat_pump_own_need_in_watt: The heat pump's own need (:meth:`heat_pump_own_need_in_watt`), in W; 0
                without a space-heating heat pump.

        Returns:
            The grid balance, the surplus after the battery and the raise's budget.

        Raises:
            ValueError: If there are fewer outputs or component types than inputs.
        """
        if len(outputs_sorted) < len(inputs_sorted) or len(component_types_sorted) < len(inputs_sorted):
            raise ValueError(
                "Lengths of inputs, component types, and outputs must match."
                f" Got {len(inputs_sorted)}, {len(component_types_sorted)}, and {len(outputs_sorted)}."
                "Make sure all inputs have the same source weight as the corresponding output! "
                "Please check all your default and manual connections. \n"
                f"Inputs: {[input.fullname for input in inputs_sorted]} \n"
                f"Outputs: {[output.full_name for output in outputs_sorted]} \n"
            )

        surplus_after_battery_in_watt = available_surplus_electricity_in_watt
        raise_budget_in_watt = available_surplus_electricity_in_watt
        for index, single_input_sorted in enumerate(inputs_sorted):
            single_component_type_sorted = component_types_sorted[index]
            single_output_sorted = outputs_sorted[index]

            surplus_before_participant_in_watt = available_surplus_electricity_in_watt
            available_surplus_electricity_in_watt = self.control_electricity_component_iterative(
                available_surplus_electricity_in_watt=available_surplus_electricity_in_watt,
                stsv=stsv,
                current_component_type=single_component_type_sorted,
                current_input=single_input_sorted,
                current_output=single_output_sorted,
            )
            if single_component_type_sorted == lt.ComponentType.BATTERY:
                limit_inputs = self.battery_charging_limit_inputs[cast(str, single_input_sorted.src_object_name)]
                maximum_charging_power_in_watt = stsv.get_input_value(limit_inputs.maximum_charging_power_in_watt)
                minimum_charging_power_in_watt = stsv.get_input_value(limit_inputs.minimum_charging_power_in_watt)
                surplus_after_battery_in_watt -= self.battery_charging_share_in_watt(
                    surplus_in_watt=surplus_after_battery_in_watt,
                    maximum_charging_power_in_watt=maximum_charging_power_in_watt,
                    minimum_charging_power_in_watt=minimum_charging_power_in_watt,
                )
                raise_budget_in_watt -= self.battery_charging_share_in_watt(
                    surplus_in_watt=raise_budget_in_watt,
                    maximum_charging_power_in_watt=maximum_charging_power_in_watt,
                    minimum_charging_power_in_watt=minimum_charging_power_in_watt,
                )
            else:
                realized_change_in_watt = available_surplus_electricity_in_watt - surplus_before_participant_in_watt
                surplus_after_battery_in_watt += realized_change_in_watt
                raise_budget_in_watt = self.raise_budget_after_participant_in_watt(
                    budget_in_watt=raise_budget_in_watt,
                    component_type=single_component_type_sorted,
                    realized_change_in_watt=realized_change_in_watt,
                    heat_pump_own_need_in_watt=heat_pump_own_need_in_watt,
                )

        return SurplusAfterParticipants(
            grid_balance_in_watt=available_surplus_electricity_in_watt,
            surplus_after_battery_in_watt=surplus_after_battery_in_watt,
            raise_budget_in_watt=raise_budget_in_watt,
        )

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Distribute the surplus, publish the grid balance, and decide the set-temperature raise.

        Every pass distributes the surplus from the realized values: the offers to the battery and the car, the grid
        balance, the total consumption, the district pass-through and the consumers' grid shares. The set-temperature
        raise, the manager's one on/off decision, is taken only in an unforced pass (:meth:`decide_raise`): under
        ``force_convergence`` its three modifiers and the heat pump's power target keep the values of the last unforced
        pass, and the end-of-step state keeps that pass's decision (:class:`EMSState`). For example, if the last unforced pass raises with 300 W of surplus and the first
        forced pass sees -2,000 W because the raised heat pump draws more, the modifiers stay raised while the grid
        balance shows the -2,000 W and the battery is offered the surplus of this pass.

        The offers are not held: the battery's power follows the surplus continuously within its limits, so it has no
        edge to flip across, and holding its offer would let it charge from the grid on a surplus that is gone. Its
        own decisions belong in a battery controller of its own.

        Why: the raise moves the surplus it is decided on. Where no raise decision is consistent with its own
        effect, re-deciding on every pass flips the raise until the simulator gives up.

        Args:
            timestep: The index of the step.
            stsv: The step values the inputs are read from and the outputs written to.
            force_convergence: Whether the simulator forces the step to converge, so the manager holds its decisions.
        """
        if timestep == 0:
            (
                self.inputs_sorted,
                self.component_types_sorted,
                self.outputs_sorted,
                self.production_inputs,
                self.consumption_uncontrolled_inputs,
                self.consumption_ems_controlled_inputs,
            ) = self.sort_source_weights_and_components()
            self.battery_charging_limit_inputs = self.pair_battery_charging_limit_inputs(
                self.inputs_sorted, self.component_types_sorted
            )
            self.heat_pump_information = self.pair_heat_pump_information_inputs(
                self.inputs_sorted, self.component_types_sorted
            )

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
            own_need_in_watt = self.read_heat_pump_own_need_in_watt(stsv)
            surplus_after_participants = self.distribute_available_surplus_electricity_iterative(
                available_surplus_electricity_in_watt=available_surplus_electricity_in_watt,
                stsv=stsv,
                inputs_sorted=self.inputs_sorted,
                component_types_sorted=self.component_types_sorted,
                outputs_sorted=self.outputs_sorted,
                heat_pump_own_need_in_watt=own_need_in_watt,
            )
            available_surplus_electricity_in_watt = surplus_after_participants.grid_balance_in_watt
            if not force_convergence:
                decision = self.decide_raise(stsv, surplus_after_participants, own_need_in_watt)
                self.state = EMSState(set_temperatures_are_raised=decision.set_temperatures_are_raised)
                self.modify_set_temperatures_for_components_in_case_of_surplus_electricity(
                    set_temperatures_are_raised=decision.set_temperatures_are_raised,
                    stsv=stsv,
                    inputs_sorted=self.inputs_sorted,
                    component_types_sorted=self.component_types_sorted,
                )
                stsv.set_output_value(self.heat_pump_electrical_power_target, decision.power_target_in_watt)

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

    def dispatch_measuring_input(self, field_name: str) -> ComponentInput:
        """Return the input that measures the participant one of this controller's dispatch outputs steers.

        The participant is found the way the dispatch pairs it (:meth:`sort_source_weights_and_components`): the
        dispatch output and the input that measures the participant share one source weight and one component type.
        For example, the dispatch output ``DispatchToL1EVChargeControl_1_ElectricityTargetFromEMS`` pairs with the
        input fed by ``L1EVChargeControl_1.BatteryChargingPowerToEMS``, the car's realized charging power.

        Args:
            field_name: The dispatch output's field name.

        Returns:
            The measuring input, connected to the participant's own output.

        Raises:
            ValueError: If the output is no dispatch output of this controller, if not exactly one input pairs with
                it, or if that input is not connected to a component's output.
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
        return measuring_input

    def dispatch_target_kpi_source(self, field_name: str) -> KpiSource:
        """The source of the participant one of this controller's dispatch outputs steers.

        The grid-share KPIs are reported on behalf of the participant, so they carry its
        :class:`KpiSource` and not the manager's. The participant is the one its measuring input
        (:meth:`dispatch_measuring_input`) is connected to; that input's source output carries the
        participant's identity and display configuration, so the source is built exactly as the
        participant builds it.

        Args:
            field_name: The dispatch output's field name.

        Returns:
            The participant's source.

        Raises:
            ValueError: As :meth:`dispatch_measuring_input`.
        """
        participant_output = cast(ComponentOutput, self.dispatch_measuring_input(field_name).source_output)
        return KpiSource.for_component(
            participant_output.component_id, cast(DisplayConfig, participant_output.display_config)
        )

    @staticmethod
    def offered_participant_grid_draw_in_watt(
        surplus_offered_in_watt: pd.Series, realized_draw_in_watt: pd.Series
    ) -> pd.Series:
        """Return what a participant that is offered the surplus takes from the grid, per step, in W.

        A participant offered the surplus (the electric car) covers its draw from the offer as far as the offer
        reaches, and takes the rest from the grid. A negative offer is a deficit and covers nothing. For example,
        an offer of 3,000 W and a draw of 11,000 W give 8,000 W from the grid; an offer of 5,000 W and a draw of
        4,000 W give 0 W; an offer of -2,000 W and a draw of 11,000 W give 11,000 W.

        Why: the car's dispatch signal is the surplus offered to it, not what is left after it, so its negative part
        is no longer its grid share, as it is for the other consumers.

        Args:
            surplus_offered_in_watt: The surplus offered to the participant in every step, in W.
            realized_draw_in_watt: The participant's realized draw in every step, in W, zero or positive.

        Returns:
            The participant's draw from the grid in every step, in W, zero or positive.

        Raises:
            ValueError: If the two series differ in length.
        """
        if len(surplus_offered_in_watt) != len(realized_draw_in_watt):
            raise ValueError(
                f"The offer ({len(surplus_offered_in_watt)} steps) and the realized draw "
                f"({len(realized_draw_in_watt)} steps) must cover the same steps."
            )
        offer_in_watt = surplus_offered_in_watt.to_numpy(dtype=float).clip(min=0.0)
        draw_in_watt = realized_draw_in_watt.to_numpy(dtype=float)
        return pd.Series((draw_in_watt - offer_in_watt).clip(min=0.0), index=surplus_offered_in_watt.index)

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

            if dispatch_target == lt.ComponentType.CAR_BATTERY:
                # The car is offered the surplus; it takes from the grid what its draw exceeds the offer by.
                # The draw's column is its global index, its position in all_outputs; an output that was never
                # registered there (global index -1) would read another column without a word.
                draw_output = cast(ComponentOutput, self.dispatch_measuring_input(output.field_name).source_output)
                if not (
                    0 <= draw_output.global_index < len(all_outputs)
                    and all_outputs[draw_output.global_index] is draw_output
                ):
                    raise ValueError(
                        f"The car's draw {draw_output.full_name} is not at its global index "
                        f"{draw_output.global_index} in the outputs, so its result column is unknown."
                    )
                electricity_from_grid_in_watt_series = self.offered_participant_grid_draw_in_watt(
                    surplus_offered_in_watt=postprocessing_results.iloc[:, index],
                    realized_draw_in_watt=postprocessing_results.iloc[:, draw_output.global_index],
                )
            else:
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
