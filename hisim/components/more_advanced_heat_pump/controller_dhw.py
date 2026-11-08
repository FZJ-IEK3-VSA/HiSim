"""The domestic-hot-water L1 controller of the hplib heat pump.

:class:`MoreAdvancedHeatPumpHPLibControllerDHW` switches the heat pump's hot-water side on and off on the hot-water
storage's temperature and sends the supply temperature for the hot-water charge; it also applies the hot-water
priority limit against the space-heating controller's signal. Its configuration
(:class:`MoreAdvancedHeatPumpHPLibControllerDHWConfig`), its states (:class:`HeatPumpDhwState`) and the state it keeps
between steps (:class:`HeatPumpDhwControllerState`) live beside it.
"""

import importlib
import math
from enum import Enum, unique
from dataclasses import dataclass, field
from typing import List, NamedTuple

import pandas as pd
from dataclasses_json import dataclass_json

# Import modules from HiSim
from hisim.component import (
    L1ControllerComponent,
    ComponentInput,
    ComponentOutput,
    SingleTimeStepValues,
    ComponentConnection,
    OpexCostDataClass,
    CapexCostDataClass,
)
from hisim.config import (
    ComponentID,
    ConfigBase,
    DisplayConfig,
    preset,
)
from hisim.explicit_state_controller import ExplicitStateController
from hisim.components.dual_circuit_system import HotWaterPriorityLimit, HotWaterPriorityTimer
from hisim.loadtypes import (
    OutputAggregation,
    LoadTypes,
    Units,
)

from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry
from hisim.economics.facts import CostRelevance
from hisim.components.more_advanced_heat_pump.controller_space_heating import (
    MoreAdvancedHeatPumpHPLibControllerSpaceHeating,
)


# implement a HPLib controller l1 for dhw storage (tww)
@dataclass_json
@dataclass
class MoreAdvancedHeatPumpHPLibControllerDHWConfig(ConfigBase):
    """Configuration of the hplib heat pump's domestic-hot-water controller.

    The hysteresis in front of the machine's hot-water side: it switches the machine on
    when the DHW vessel has cooled to :attr:`t_min_dhw_storage_in_celsius` and off again
    when it has come within :attr:`charge_end_below_target_in_kelvin` of
    :attr:`t_max_dhw_storage_in_celsius` (58 °C by default). The named default is
    :meth:`preset_standard`, the 40/60 °C band the fleet runs::

        MoreAdvancedHeatPumpHPLibControllerDHWConfig.preset_standard("HeatPumpControllerDHW")

    Nothing here depends on the building or on the machine beside it, which is why no
    field is sizable and the preset takes nothing but the instance name.
    """

    MAIN_CLASS = "hisim.components.more_advanced_heat_pump.controller_dhw.MoreAdvancedHeatPumpHPLibControllerDHW"

    component_id: ComponentID
    #: lower set temperature of DHW Storage, given in °C
    t_min_dhw_storage_in_celsius: float = 40.0
    #: upper set temperature of DHW Storage, given in °C
    t_max_dhw_storage_in_celsius: float = 60.0
    #: How far below its target, ``t_max`` plus the energy manager's raise, a hot-water charge ends, in K: the charge
    #: ends once the tank's start-of-step temperature is at or above the target less this. The heat pump's hot-water
    #: supply is capped at the target, so the tank approaches it without reaching it and its last kelvins come slowly:
    #: on household_heatpump_building_sizer (full year 2021, 900 s) a charge that ended 0.5 K below the target ran 75
    #: minutes in the median and up to 195, its last steps at 3.0, 1.5 and 0.9 kW, while the space-heating buffer
    #: emptied. A real cylinder is charged through a coil that needs its flow some kelvins above the water to pass a
    #: useful power, so a charge whose flow is capped at the target stops a few kelvins short of it. 2 K is a stated
    #: default, not a manufacturer's value: the owner's decision of 2026-10-21 (hisim-room-air-dhw-dip-a5by), where it
    #: cut the quarter-hours with a room-air drop above 1 K during hot water from 160 to 101. The surplus switch-on
    #: stops at the same distance below ``t_max`` (:meth:`MoreAdvancedHeatPumpHPLibControllerDHW.next_state`).
    charge_end_below_target_in_kelvin: float = field(default=2.0, metadata={"unit": Units.KELVIN})
    #: How long a hot-water charge has priority over space heating before it is interrupted for
    #: :attr:`space_heating_interval_after_hot_water_priority_in_seconds`, in s; 0 means absolute priority
    #: (:class:`~hisim.components.dual_circuit_system.HotWaterPriorityLimit`, which names the sources). Heat-pump
    #: controllers ship 45 to 60 min. The default is 15 min, the owner's decision of 2026-10-21
    #: (hisim-room-air-dhw-dip-a5by): the space-heating buffer empties after 20 to 25 min of priority, and the room
    #: air, which has no heat capacity of its own in the building model, falls with it. On
    #: household_heatpump_building_sizer (full year 2021, 900 s, charge end 2 K) the quarter-hours with a room-air
    #: drop above 0.5 K during hot water were 164 with absolute priority, 144 with 60 / 30 min, 115 with 45 / 30 min
    #: and 42 with 15 / 15 min.
    maximum_hot_water_priority_time_in_seconds: float = field(default=900.0, metadata={"unit": Units.SECONDS})
    #: How long space heating has the heat pump after an interrupted hot-water charge before the charge resumes, in
    #: s. The default is 15 min, as long as the maximum priority time (the Siemens Albatros2 rule).
    space_heating_interval_after_hot_water_priority_in_seconds: float = field(
        default=900.0, metadata={"unit": Units.SECONDS}
    )

    def __post_init__(self) -> None:
        """Refuse a charge end that is not above 0 K or not below the band, naming the field and the value.

        For example, the default 2 K with the 40/60 °C band is accepted; 0 K is refused, since the capped supply never
        brings the tank to its target and a charge would not end, and 20 K is refused, since the charge would end at
        or below the 40 °C switch-on point and start and end on the same reading.

        The hot-water priority times are checked by
        :meth:`~hisim.components.dual_circuit_system.HotWaterPriorityLimit.require_valid_times`.

        Raises:
            ValueError: If ``charge_end_below_target_in_kelvin`` is not a finite number above 0 K and below
                ``t_max_dhw_storage_in_celsius - t_min_dhw_storage_in_celsius``, or the hot-water priority times are
                invalid.
        """
        band_in_kelvin = self.t_max_dhw_storage_in_celsius - self.t_min_dhw_storage_in_celsius
        end_in_kelvin = self.charge_end_below_target_in_kelvin
        if not math.isfinite(end_in_kelvin) or not 0.0 < end_in_kelvin < band_in_kelvin:
            raise ValueError(
                f"The heat pump's hot-water controller needs charge_end_below_target_in_kelvin above 0 K and below "
                f"its {band_in_kelvin} K band (t_max_dhw_storage_in_celsius - t_min_dhw_storage_in_celsius), got "
                f"{end_in_kelvin!r} K: at 0 K the capped supply never brings the tank to the end of the charge, and "
                "at the band or above the charge ends where it starts."
            )
        HotWaterPriorityLimit.require_valid_times(
            maximum_priority_time_in_seconds=self.maximum_hot_water_priority_time_in_seconds,
            space_heating_interval_in_seconds=self.space_heating_interval_after_hot_water_priority_in_seconds,
            controller_description="The heat pump's hot-water controller",
        )

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MoreAdvancedHeatPumpHPLibControllerDHWConfig":
        """The fleet's hot-water controller, reheating the vessel from 40 °C until 2 K below its 60 °C set temperature.

        The field defaults are that controller: the 40/60 °C band, which keeps the machine from cycling on every tap,
        and a charge that ends 2 K below the 60 °C set temperature (:attr:`charge_end_below_target_in_kelvin`).

        Args:
            name: Instance name of the controller in the simulation.

        Returns:
            The configuration, fully concrete -- the class has no sizable field.
        """
        return cls(component_id=ComponentID(name=name))


@unique
class HeatPumpDhwState(int, Enum):

    """The hot-water states of the hplib heat pump's hot-water controller, with the signal values it publishes.

    The heat pump reads the value as its hot-water switch: 2 asks for a hot-water charge, 0 for none.
    """

    #: No hot-water charge.
    OFF = 0
    #: A hot-water charge.
    ON = 2


class HeatPumpDhwControllerState(NamedTuple):
    """The whole memory of the heat pump's hot-water controller: its charge, the charge it commands, and its timer.

    :class:`MoreAdvancedHeatPumpHPLibControllerDHW` keeps nothing else between passes or steps
    (:class:`hisim.explicit_state_controller.ExplicitStateController`). For example, a charge that has had priority for
    its maximum time while space heating asks for heat holds ``charge=HeatPumpDhwState.ON`` and
    ``commanded_charge=HeatPumpDhwState.OFF``: it yields the step to space heating and resumes after the interval. It is a
    NamedTuple, so a pass builds a new state instead of changing the old one.
    """

    #: The controller's own hysteresis decision, which stays ON through a priority interruption.
    charge: HeatPumpDhwState
    #: The hot-water signal the controller commands the heat pump: ``charge``, or OFF on a step it yields to space heating.
    commanded_charge: HeatPumpDhwState
    #: Where a charge stands against its maximum priority time (:class:`HotWaterPriorityLimit`).
    hot_water_priority_timer: HotWaterPriorityTimer
    #: The hot-water supply temperature the controller aims at, ``t_max`` plus the energy manager's raise, in °C.
    supply_temperature_set_for_dhw_in_celsius: float
    #: The hysteresis decision without the energy manager's raise, which the next step starts from: whether the
    #: controller would charge the tank on its own.
    charge_without_raise: HeatPumpDhwState


class MoreAdvancedHeatPumpHPLibControllerDHW(ExplicitStateController[HeatPumpDhwControllerState], L1ControllerComponent):
    """Heat Pump Controller for DHW.

    It takes data from DHW Storage --> generic hot water storage modular
    sends signal to the heat pump for activation or deactivation.

    The controller keeps its whole memory in one :class:`HeatPumpDhwControllerState` and leaves saving, restoring and
    forcing to :class:`hisim.explicit_state_controller.ExplicitStateController`: every unforced pass decides from the
    state at the start of the step (:meth:`decide`), and a forced pass republishes the state of the last unforced pass,
    which is also the state the step commits. Before, each pass decided from the pass before it, so a decision of any
    pass latched for the rest of the step (hisim-component-physics-4g9.24).

    The controller also keeps the charge it would run without the energy manager's raise and publishes whether the
    commanded charge is that own need (``ChargesWithoutRaise``), so the energy manager counts only such a charge's draw
    as the heat pump's own need.
    """

    cost_relevance = CostRelevance.FREE_OF_COST

    # Inputs
    WaterTemperatureInputFromDHWStorage = "WaterTemperatureInputFromDHWStorage"
    DHWStorageTemperatureModifier = "DHWStorageTemperatureModifier"
    #: The space-heating controller's signal (1 heating), which tells whether space heating asks for the heat pump
    #: while a hot-water charge has priority (:class:`~hisim.components.dual_circuit_system.HotWaterPriorityLimit`).
    SpaceHeatingStateFromController = "SpaceHeatingStateFromController"

    # Outputs
    State_dhw = "StateDHW"
    #: The hot-water supply temperature the controller aims at: ``t_max`` plus the energy manager's raise.
    SupplyTemperatureSetForDHWInCelsius = "SupplyTemperatureSetForDHWInCelsius"
    #: 1 while the heat pump charges the tank on a charge the controller would run without the raise, else 0.
    ChargesWithoutRaise = "ChargesWithoutRaise"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: MoreAdvancedHeatPumpHPLibControllerDHWConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.heatpump_controller_dhw_config = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.config: MoreAdvancedHeatPumpHPLibControllerDHWConfig = config

        self.water_temperature_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureInputFromDHWStorage,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        self.storage_temperature_modifier_channel: ComponentInput = self.add_input(
            self.component_name,
            self.DHWStorageTemperatureModifier,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            mandatory=False,
        )

        # without a space-heating controller nothing asks for space heating, and a charge is never interrupted
        self.space_heating_state_channel: ComponentInput = self.add_input(
            self.component_name,
            self.SpaceHeatingStateFromController,
            LoadTypes.ANY,
            Units.ANY,
            mandatory=False,
        )

        self.state_dhw_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.State_dhw,
            LoadTypes.ANY,
            Units.ANY,
            output_description=(
                "The hot-water mode the controller commands the heat pump: 2 charges the hot-water tank, 0 does not."
            ),
            aggregation=OutputAggregation.NONE,
        )

        self.supply_temperature_set_for_dhw_in_celsius_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.SupplyTemperatureSetForDHWInCelsius,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            output_description=(
                "The hot-water supply temperature the controller aims at, t_max plus the energy manager's raise "
                "(60 °C by default). The heat pump's hot-water supply stops at it, and a charge ends once the tank "
                "is within the controller's charge end (2 K by default) of it."
            ),
        )

        self.charges_without_raise_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ChargesWithoutRaise,
            LoadTypes.ANY,
            Units.BINARY,
            output_description=(
                "1 while the controller commands a hot-water charge that it would run without the energy manager's "
                "raise, else 0: its own hysteresis at the unraised set temperature, on the tank's start-of-step "
                "temperature. A charge only the raise starts is 0. The energy manager reads it to count the heat "
                "pump's hot-water draw as its own need."
            ),
        )

        self.add_default_connections(self.get_default_connections_from_simple_dhw_storage())
        self.add_default_connections(self.get_default_connections_from_energy_management_system())
        self.add_default_connections(self.get_default_connections_from_space_heating_controller())

    @staticmethod
    def get_default_connections_from_space_heating_controller() -> List[ComponentConnection]:
        """Return the connection of the space-heating controller's signal, which the priority limit reads.

        Returns:
            The connection of ``SpaceHeatingStateFromController`` to the space-heating controller's ``State_SH``.
        """
        return [
            ComponentConnection(
                MoreAdvancedHeatPumpHPLibControllerDHW.SpaceHeatingStateFromController,
                MoreAdvancedHeatPumpHPLibControllerSpaceHeating.get_classname(),
                MoreAdvancedHeatPumpHPLibControllerSpaceHeating.State_SH,
            )
        ]

    def get_default_connections_from_simple_dhw_storage(
        self,
    ):
        """Get simple dhw water storage default connections."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.simple_water_storage"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "SimpleDHWStorage")
        connections = []
        dhw_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                MoreAdvancedHeatPumpHPLibControllerDHW.WaterTemperatureInputFromDHWStorage,
                dhw_classname,
                # the tank's start-of-step temperature T0: the controller decides on a value the step's
                # iteration does not move
                component_class.WaterTemperatureAtStartOfStepInCelsius,
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
            ComponentConnection(
                MoreAdvancedHeatPumpHPLibControllerDHW.DHWStorageTemperatureModifier,
                ems_classname,
                component_class.DomesticHotWaterStorageTemperatureModifier,
            )
        )
        return connections

    def initial_state(self) -> HeatPumpDhwControllerState:
        """Return the state before the first step: no charge, an idle priority timer and ``t_max`` as the set temperature.

        Returns:
            The initial state.
        """
        return HeatPumpDhwControllerState(
            charge=HeatPumpDhwState.OFF,
            commanded_charge=HeatPumpDhwState.OFF,
            hot_water_priority_timer=HotWaterPriorityTimer(),
            supply_temperature_set_for_dhw_in_celsius=self.config.t_max_dhw_storage_in_celsius,
            charge_without_raise=HeatPumpDhwState.OFF,
        )

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(self) -> List[str]:
        """Write important variables to report."""
        return self.heatpump_controller_dhw_config.get_string_dict()

    @staticmethod
    def next_state(
        *,
        state: "HeatPumpDhwState",
        storage_temperature_in_celsius: float,
        raise_in_kelvin: float,
        minimum_temperature_in_celsius: float,
        maximum_temperature_in_celsius: float,
        charge_end_below_target_in_kelvin: float,
    ) -> "HeatPumpDhwState":
        """Return the hot-water state after one decision on the tank's start-of-step temperature.

        The transitions, applied in this order, the last that applies winning:

        | condition | next state |
        |---|---|
        | the tank is below ``minimum_temperature_in_celsius`` | ON |
        | the tank is at or above ``maximum + raise - charge_end_below_target_in_kelvin`` | OFF |
        | a raise above 0 and the tank below ``maximum - charge_end_below_target_in_kelvin`` | ON |
        | none of these | the state as it was |

        The raise is the energy manager's increase of the set temperature while it has surplus electricity. For
        example, with the 40/60 °C band, the default 2 K charge end and no raise, a tank at 50 °C keeps its state, one
        at 39 °C switches on and one at 58 °C switches off; with a 10 K raise, a tank at 57 °C switches on and charges
        until 68 °C.

        Why the surplus switch-on stops at the charge end below ``maximum`` and not at ``maximum``: a tank between the
        two would be switched on by the raise and off without it, and the energy manager's raise, which follows the
        heat pump's draw, can come and go within a step, so the charge would toggle with it.

        Args:
            state: The state before the decision.
            storage_temperature_in_celsius: The tank's start-of-step temperature, in °C.
            raise_in_kelvin: The energy manager's raise of the set temperature, in K, 0 or more.
            minimum_temperature_in_celsius: The tank temperature below which a charge starts, in °C.
            maximum_temperature_in_celsius: The set temperature without a raise, in °C.
            charge_end_below_target_in_kelvin: How far below the target, ``maximum + raise``, a charge ends, in K
                (:attr:`MoreAdvancedHeatPumpHPLibControllerDHWConfig.charge_end_below_target_in_kelvin`).

        Returns:
            The state after the decision.
        """
        next_state = state
        if storage_temperature_in_celsius < minimum_temperature_in_celsius:
            next_state = HeatPumpDhwState.ON
        if storage_temperature_in_celsius >= (
            maximum_temperature_in_celsius + raise_in_kelvin - charge_end_below_target_in_kelvin
        ):
            # the tank has come as close to its target, to which the heat pump's supply is capped, as a charge goes
            next_state = HeatPumpDhwState.OFF
        if raise_in_kelvin > 0 and storage_temperature_in_celsius < (
            maximum_temperature_in_celsius - charge_end_below_target_in_kelvin
        ):
            # on with surplus electricity, below where the unraised charge ends, so a raise that comes and goes
            # within a step cannot switch the charge on and off
            next_state = HeatPumpDhwState.ON
        return next_state

    def decide(
        self, *, timestep: int, stsv: SingleTimeStepValues, state: HeatPumpDhwControllerState
    ) -> HeatPumpDhwControllerState:
        """Decide whether the heat pump charges the hot-water tank, and the set temperature of the charge.

        The decision (:meth:`next_state`) is taken on the tank's start-of-step temperature, which does not change
        while the step iterates, from the charge at the start of the step. A charge that has had priority for its
        maximum time while the space-heating controller asks for heat yields its steps to space heating for the
        configured interval (:class:`~hisim.components.dual_circuit_system.HotWaterPriorityLimit`): the controller
        commands OFF on them and keeps its own charge ON, so the charge resumes after the interval and ends at its
        target. For example, a tank at 39 °C with the 40/60 °C band and no raise starts a charge at 60 °C.

        A first pass of the run reads the tank's zeroed output, 0 °C, and asks for a charge; the next pass decides
        again from the start of the step on the tank's real temperature, so that request does not last.

        The same hysteresis decides a second time with no raise, from the charge without the raise the step started
        with: whether the charge is the controller's own need (:attr:`ChargesWithoutRaise`).

        Args:
            timestep: The step.
            stsv: The step's values, to read the inputs from.
            state: The state at the start of the step.

        Returns:
            The new state.
        """
        raise_in_kelvin = stsv.get_input_value(self.storage_temperature_modifier_channel)
        storage_temperature_in_celsius = stsv.get_input_value(self.water_temperature_input_channel)
        charge = self.next_state(
            state=state.charge,
            storage_temperature_in_celsius=storage_temperature_in_celsius,
            raise_in_kelvin=raise_in_kelvin,
            minimum_temperature_in_celsius=self.config.t_min_dhw_storage_in_celsius,
            maximum_temperature_in_celsius=self.config.t_max_dhw_storage_in_celsius,
            charge_end_below_target_in_kelvin=self.config.charge_end_below_target_in_kelvin,
        )
        charge_without_raise = self.next_state(
            state=state.charge_without_raise,
            storage_temperature_in_celsius=storage_temperature_in_celsius,
            raise_in_kelvin=0.0,
            minimum_temperature_in_celsius=self.config.t_min_dhw_storage_in_celsius,
            maximum_temperature_in_celsius=self.config.t_max_dhw_storage_in_celsius,
            charge_end_below_target_in_kelvin=self.config.charge_end_below_target_in_kelvin,
        )
        charges, timer = HotWaterPriorityLimit.decide(
            timer=state.hot_water_priority_timer,
            charge_requested=charge == HeatPumpDhwState.ON,
            space_heating_requested=stsv.get_input_value(self.space_heating_state_channel) == 1,
            maximum_priority_time_in_seconds=self.config.maximum_hot_water_priority_time_in_seconds,
            space_heating_interval_in_seconds=self.config.space_heating_interval_after_hot_water_priority_in_seconds,
            seconds_per_timestep=self.my_simulation_parameters.seconds_per_timestep,
        )
        return HeatPumpDhwControllerState(
            charge=charge,
            commanded_charge=charge if charges else HeatPumpDhwState.OFF,
            hot_water_priority_timer=timer,
            supply_temperature_set_for_dhw_in_celsius=self.config.t_max_dhw_storage_in_celsius + raise_in_kelvin,
            charge_without_raise=charge_without_raise,
        )

    def publish(self, *, stsv: SingleTimeStepValues, state: HeatPumpDhwControllerState) -> None:
        """Write the outputs that belong to ``state``: the commanded charge, the set temperature and the own charge.

        Args:
            stsv: The step's values, to write the outputs to.
            state: The state whose outputs are written.
        """
        stsv.set_output_value(self.state_dhw_channel, state.commanded_charge.value)
        stsv.set_output_value(
            self.supply_temperature_set_for_dhw_in_celsius_channel, state.supply_temperature_set_for_dhw_in_celsius
        )
        charges_on_its_own = (
            state.commanded_charge == HeatPumpDhwState.ON and state.charge_without_raise == HeatPumpDhwState.ON
        )
        stsv.set_output_value(self.charges_without_raise_channel, 1.0 if charges_on_its_own else 0.0)

    @staticmethod
    def get_cost_capex(
        config: MoreAdvancedHeatPumpHPLibControllerDHWConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        capex_cost_data_class = CapexCostDataClass.get_default_capex_cost_data_class()
        return capex_cost_data_class

    def get_cost_opex(
        self, all_outputs: List, postprocessing_results: pd.DataFrame
    ) -> OpexCostDataClass:  # pylint: disable=unused-argument
        """Calculate OPEX costs, consisting of maintenance costs for Heat Distribution System."""
        opex_cost_data_class = OpexCostDataClass.get_default_opex_cost_data_class()

        return opex_cost_data_class

    def get_component_kpi_entries(self, all_outputs: List, postprocessing_results: pd.DataFrame) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []
