"""The space-heating L1 controller of the hplib heat pump.

:class:`MoreAdvancedHeatPumpHPLibControllerSpaceHeating` switches the heat pump's space-heating side on and off on the
buffer's start-of-step water temperature against the supply temperature the heating curve asks for, heats only in the
heating season, and commands the part-load ratio a space-heating charge runs. Its configuration
(:class:`MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig`), its operating modes
(:class:`HeatPumpSpaceHeatingMode`) and the state it keeps between steps (:class:`HeatPumpSpaceHeatingControllerState`)
live beside it. The heat pump it commands is :mod:`hisim.components.more_advanced_heat_pump.heat_pump`.
"""

import importlib
from enum import Enum, unique
from dataclasses import dataclass
from typing import ClassVar, List, NamedTuple, Optional, Tuple

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
    Sizable,
    Size,
    concrete,
    preset,
    sized_field,
)
from hisim.components import weather, simple_water_storage, heating_curve
from hisim.explicit_state_controller import ExplicitStateController
from hisim.part_load import StartOfStepPartLoadControl
from hisim.components.heating_curve import HeatDistributionSystemType
from hisim.loadtypes import (
    OutputAggregation,
    LoadTypes,
    Units,
)

from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry
from hisim.economics.facts import CostRelevance


@dataclass_json
@dataclass
class MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig(ConfigBase):
    """Configuration of the hplib heat pump's space-heating controller.

    The on/off logic in front of the machine's space-heating side: it compares the buffer
    vessel's water temperature with the supply temperature the heating curve asks for, and it
    heats only in the heating season the heating curve publishes. The named default is
    :meth:`preset_standard`; the two fields that belong to the building and its emitter
    circuit rather than to the machine -- :attr:`heat_distribution_system_type` and
    :attr:`set_heating_temperature_for_building_in_celsius` -- are sizable, so the preset
    leaves them ``AUTO`` and ``.resolve(ctx)`` copies them from the facts the building and
    the heat distribution controller contribute::

        MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig.preset_standard(
            "MoreAdvancedHeatPumpHPLibControllerSH"
        ).resolve(
            SizingContext(
                set_heating_temperature_in_celsius=20.0,
                heat_distribution_system_type=HeatDistributionSystemType.FLOORHEATING,
            )
        )

    Copying rather than restating is the point: the building decides the room temperature it
    is heated to and the emitter circuit decides which emitter it feeds, and a generator
    controller that disagreed with either would heat water too cold for the room.
    """

    MAIN_CLASS = "hisim.components.more_advanced_heat_pump.controller_space_heating.MoreAdvancedHeatPumpHPLibControllerSpaceHeating"

    component_id: ComponentID
    #: Which control law runs: 1 is the plain on/off switch, 2 adds a cooling state and is
    #: only admissible for floor heating. The controller refuses any other value, and mode 2
    #: with another emitter, when it is built.
    mode: int = 1
    #: Daily average outside temperature below which the machine does not cool in ``mode``
    #: 2. ``None`` means cooling is available at any outside temperature.
    set_cooling_threshold_outside_temperature_in_celsius: Optional[float] = 20.0
    #: How far the water temperature may rise above the flow temperature the distribution
    #: system asks for before the machine switches off, in kelvin.
    upper_temperature_offset_for_state_conditions_in_celsius: float = 5.0
    #: How far it may fall below that flow temperature before the machine switches on, in
    #: kelvin. Together with the upper offset this is the hysteresis band.
    lower_temperature_offset_for_state_conditions_in_celsius: float = 5.0
    #: Which emitter the circuit this controller heats into feeds, copied from the emitter
    #: circuit. Sizable: left ``AUTO`` it is the heat distribution controller's own emitter
    #: type. The component reads it to decide whether ``mode`` 2 is admissible at all.
    heat_distribution_system_type: Sizable[HeatDistributionSystemType] = sized_field(
        rule=Size.HEAT_DISTRIBUTION_SYSTEM_TYPE, value_type=HeatDistributionSystemType
    )
    #: The room temperature the building is heated to, copied from the building. Sizable: left
    #: ``AUTO`` it is the building's own setpoint. It floors the hysteresis band (hisim-q1rm).
    set_heating_temperature_for_building_in_celsius: Sizable[float] = sized_field(
        rule=Size.SET_HEATING_TEMPERATURE_IN_CELSIUS
    )

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig":
        """The one space-heating controller the fleet runs, taking its limits from the emitter circuit.

        The field defaults are that controller: the plain on/off law, a five-kelvin
        hysteresis band either side of the requested flow temperature, and no cooling below
        20 °C outside. What the preset does not fix is the emitter type and the room setpoint,
        which stay ``AUTO`` so that they are copied from the heat distribution controller and the
        building instead of repeating their choices here. The heating season comes from the
        heating curve.

        Args:
            name: Instance name of the controller in the simulation.

        Returns:
            The configuration, with its two sizable fields still ``AUTO``.
        """
        return cls(component_id=ComponentID(name=name))


@unique
class HeatPumpSpaceHeatingMode(int, Enum):
    """The modes of the hplib heat pump's space-heating controller, with the ``State_SH`` values it publishes.

    The heat pump reads the value as its space-heating switch: 1 heats, -1 cools (control mode 2 with floor heating
    only), 0 does neither.
    """

    #: Cooling through the floor.
    COOLING = -1
    #: Neither heating nor cooling.
    OFF = 0
    #: Heating.
    HEATING = 1


class HeatPumpSpaceHeatingControllerState(NamedTuple):
    """The whole memory of the heat pump's space-heating controller: its mode and the part-load outputs it publishes.

    :class:`MoreAdvancedHeatPumpHPLibControllerSpaceHeating` keeps nothing else between passes or steps
    (:class:`hisim.explicit_state_controller.ExplicitStateController`). For example, a controller that switched heating
    on below a 37.5 °C target and asks for two thirds of a 900 s step holds ``mode=HeatPumpSpaceHeatingMode.HEATING``,
    ``requested_part_load_ratio_sh=0.67`` and ``target_temperature_sh_in_celsius=37.5``. It is a NamedTuple, so a pass
    builds a new state instead of changing the old one.
    """

    #: Heating, cooling or off: the hysteresis decision, which the next step starts from.
    mode: HeatPumpSpaceHeatingMode
    #: The fraction of the step the space-heating charge is to run, from 0 to 1.
    requested_part_load_ratio_sh: float
    #: The buffer temperature the space-heating charge aims at, in °C.
    target_temperature_sh_in_celsius: float


class MoreAdvancedHeatPumpHPLibControllerSpaceHeating(
    ExplicitStateController[HeatPumpSpaceHeatingControllerState], L1ControllerComponent
):
    """Heat Pump Controller for Space Heating.

    It takes data from other
    components and sends signal to the heat pump for
    activation or deactivation.
    On/off Switch with respect to water temperature from storage.

    Parameters
    ----------
    t_air_heating: float
        Minimum comfortable temperature for residents
    t_air_cooling: float
        Maximum comfortable temperature for residents
    offset: float
        Temperature offset to compensate the hysteresis
        correction for the building temperature change
    mode : int
        Mode index for operation type for this heat pump

    The controller also owns how much of a step a space-heating charge runs
    (:class:`hisim.part_load.StartOfStepPartLoadControl`): it switches heating on and off on the buffer's
    start-of-step temperature as before, and commands the requested part-load ratio ``RequestedPartLoadRatioSh`` with
    it. At and below the part-load threshold the requested ratio is 1 whenever the heat pump heats. Above it, with the
    buffer wired, the requested ratio is :meth:`hisim.part_load.StartOfStepPartLoadRule.raising_ratio`: the fraction that ends the buffer at its
    target (``TargetTemperatureShInCelsius``), the heating curve's supply temperature raised by the energy manager's
    modifier and by half the upper offset of the hysteresis band (:meth:`space_heating_target_temperature_in_celsius`),
    read from the buffer's end temperature and its end temperature without the heat pump. While the controller heats, a
    buffer at or above its target that its draw would take below it is charged as well, for the fraction that ends it at
    the target. The controller stays in heating while it requests a ratio of 0; the heat pump counts such a step as off
    in its published state and counters, not in its minimum times (:meth:`MoreAdvancedHeatPumpHPLib.published_mode`).
    Under ``force_convergence``, above the threshold with the buffer wired, the requested-ratio port carries the actual
    ratio the heat pump reports it ran with (``ActualPartLoadRatioSh``); otherwise it keeps the value of the last
    unforced pass
    (:meth:`hisim.part_load.StartOfStepPartLoadControl.publish_held`).

    The controller keeps its whole memory in one :class:`HeatPumpSpaceHeatingControllerState` and leaves saving,
    restoring and forcing to :class:`hisim.explicit_state_controller.ExplicitStateController`: :meth:`decide` returns
    the state of a pass, :meth:`publish` writes its outputs, and a forced pass republishes the state of the last unforced
    pass (:meth:`publish_forced`), which is also the state the step commits. So a forced step whose last unforced pass
    switched heating off commits off, the mode the heat pump ran, not the mode the step started in
    (hisim-component-physics-4g9.28).
    """

    cost_relevance = CostRelevance.FREE_OF_COST

    # Inputs
    WaterTemperatureInput = "WaterTemperatureInput"
    #: The buffer's temperature at the end of the step, which the requested part-load ratio is set against.
    WaterTemperatureAtEndOfStepFromWaterStorageInCelsius = "WaterTemperatureAtEndOfStepFromWaterStorageInCelsius"
    #: The buffer's temperature at the end of the step without the heat pump's circuit.
    WaterTemperatureAtEndOfStepWithoutHeatPumpFromWaterStorageInCelsius = (
        "WaterTemperatureAtEndOfStepWithoutHeatPumpFromWaterStorageInCelsius"
    )
    #: The actual part-load ratio: the fraction of the step the heat pump's space-heating charge ran, as the heat pump
    #: reports it.
    ActualPartLoadRatioSh = "ActualPartLoadRatioSh"
    SpaceHeatingSupplyTemperatureSetInCelsius = "SpaceHeatingSupplyTemperatureSetInCelsius"
    HeatingSeason = "HeatingSeason"

    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"

    SimpleHotWaterStorageTemperatureModifier = "SimpleHotWaterStorageTemperatureModifier"

    # Outputs
    State_SH = "State_SH"
    #: The requested part-load ratio: the fraction of the step the space-heating charge is to run.
    RequestedPartLoadRatioSh = "RequestedPartLoadRatioSh"
    #: The buffer temperature the space-heating charge aims at.
    TargetTemperatureShInCelsius = "TargetTemperatureShInCelsius"

    #: The share of the band's upper offset that the space-heating target lies above the supply temperature,
    #: dimensionless (:meth:`space_heating_target_temperature_in_celsius`). Owner decision of 2026-10-19
    #: (hisim-hydronics-fxix.27): half the upper offset, so a charge above the part-load threshold ends the buffer where the
    #: on/off controller holds it on average at 60 s, and the heat pump charges at the COP of the 60 s run.
    SHARE_OF_UPPER_OFFSET_IN_TARGET: ClassVar[float] = 0.5

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.heatpump_controller_config = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.heat_distribution_system_type = self.heatpump_controller_config.heat_distribution_system_type
        self.set_heating_temperature_for_building_in_celsius: float = float(
            concrete(self.heatpump_controller_config.set_heating_temperature_for_building_in_celsius)
        )
        self.build(
            mode=self.heatpump_controller_config.mode,
            upper_temperature_offset_for_state_conditions_in_celsius=self.heatpump_controller_config.upper_temperature_offset_for_state_conditions_in_celsius,
            lower_temperature_offset_for_state_conditions_in_celsius=self.heatpump_controller_config.lower_temperature_offset_for_state_conditions_in_celsius,
        )

        self.water_temperature_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureInput,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        self.supply_temperature_set_channel: ComponentInput = self.add_input(
            self.component_name,
            self.SpaceHeatingSupplyTemperatureSetInCelsius,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )
        self.heating_season_channel: ComponentInput = self.add_input(
            self.component_name,
            self.HeatingSeason,
            LoadTypes.ANY,
            Units.BINARY,
            True,
        )
        self.daily_avg_outside_temperature_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.DailyAverageOutsideTemperature,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        self.simple_hot_water_storage_temperature_modifier_channel: ComponentInput = self.add_input(
            self.component_name,
            self.SimpleHotWaterStorageTemperatureModifier,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            mandatory=False,
        )

        self.state_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.State_SH,
            LoadTypes.ANY,
            Units.ANY,
            output_description=f"here a description for {self.State_SH} will follow.",
            aggregation=OutputAggregation.NONE,
        )

        self.space_heating_part_load = StartOfStepPartLoadControl(
            self,
            end_temperature_input_name=self.WaterTemperatureAtEndOfStepFromWaterStorageInCelsius,
            end_temperature_without_circuit_input_name=(
                self.WaterTemperatureAtEndOfStepWithoutHeatPumpFromWaterStorageInCelsius
            ),
            actual_ratio_input_name=self.ActualPartLoadRatioSh,
            requested_ratio_output_name=self.RequestedPartLoadRatioSh,
            device_description="the heat pump's space-heating charge",
            target_output_name=self.TargetTemperatureShInCelsius,
        )

        self.add_default_connections(self.get_default_connections_from_heating_curve())
        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(self.get_default_connections_from_simple_hot_water_storage())
        self.add_default_connections(self.get_default_connections_from_energy_management_system())
        self.add_default_connections(self.get_default_connections_from_more_advanced_heat_pump())

    @staticmethod
    def get_default_connections_from_heating_curve() -> List[ComponentConnection]:
        """Return the connections of the supply temperature set point and the heating season from the heating curve.

        Returns:
            The two connections.
        """
        curve_classname = heating_curve.HeatingCurveController.get_classname()
        return [
            ComponentConnection(
                MoreAdvancedHeatPumpHPLibControllerSpaceHeating.SpaceHeatingSupplyTemperatureSetInCelsius,
                curve_classname,
                heating_curve.HeatingCurveController.SpaceHeatingSupplyTemperatureSetInCelsius,
            ),
            ComponentConnection(
                MoreAdvancedHeatPumpHPLibControllerSpaceHeating.HeatingSeason,
                curve_classname,
                heating_curve.HeatingCurveController.HeatingSeason,
            ),
        ]

    def get_default_connections_from_weather(
        self,
    ):
        """Get default connections."""
        connections = []
        weather_classname = weather.Weather.get_classname()
        connections.append(
            ComponentConnection(
                MoreAdvancedHeatPumpHPLibControllerSpaceHeating.DailyAverageOutsideTemperature,
                weather_classname,
                weather.Weather.DailyAverageOutsideTemperatures,
            )
        )
        return connections

    def get_default_connections_from_simple_hot_water_storage(
        self,
    ):
        """Get simple hot water storage default connections."""
        connections = []
        hws_classname = simple_water_storage.SimpleHotWaterStorage.get_classname()
        # the sensor is the buffer's start-of-step temperature, which no pass of the step moves, so the on/off
        # decision cannot cycle with the temperatures the buffer publishes to its circuits
        connections.append(
            ComponentConnection(
                MoreAdvancedHeatPumpHPLibControllerSpaceHeating.WaterTemperatureInput,
                hws_classname,
                simple_water_storage.SimpleHotWaterStorage.WaterTemperatureAtStartOfStepInCelsius,
            )
        )
        controller = MoreAdvancedHeatPumpHPLibControllerSpaceHeating
        buffer = simple_water_storage.SimpleHotWaterStorage
        connections.append(
            ComponentConnection(
                controller.WaterTemperatureAtEndOfStepFromWaterStorageInCelsius,
                hws_classname,
                buffer.WaterTemperatureAtEndOfStepInCelsius,
            )
        )
        connections.append(
            ComponentConnection(
                controller.WaterTemperatureAtEndOfStepWithoutHeatPumpFromWaterStorageInCelsius,
                hws_classname,
                buffer.WaterTemperatureAtEndOfStepWithoutHeatGeneratorInCelsius,
            )
        )
        return connections

    @staticmethod
    def get_default_connections_from_more_advanced_heat_pump() -> List[ComponentConnection]:
        """Return the connection of the actual ratio the heat pump's space-heating charge ran, from the heat pump.

        Returns:
            The one connection of ``ActualPartLoadRatioSh``.
        """
        # The heat pump's module imports this controller for its own default connections, so this import waits for the call.
        from hisim.components.more_advanced_heat_pump.heat_pump import (  # pylint: disable=import-outside-toplevel
            MoreAdvancedHeatPumpHPLib,
        )

        return [
            ComponentConnection(
                MoreAdvancedHeatPumpHPLibControllerSpaceHeating.ActualPartLoadRatioSh,
                MoreAdvancedHeatPumpHPLib.get_classname(),
                MoreAdvancedHeatPumpHPLib.ActualPartLoadRatioSh,
            )
        ]

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
                MoreAdvancedHeatPumpHPLibControllerSpaceHeating.SimpleHotWaterStorageTemperatureModifier,
                ems_classname,
                component_class.SpaceHeatingWaterStorageTemperatureModifier,
            )
        )
        return connections

    def build(
        self,
        mode: float,
        upper_temperature_offset_for_state_conditions_in_celsius: float,
        lower_temperature_offset_for_state_conditions_in_celsius: float,
    ) -> None:
        """Set the control law and the hysteresis band; the controller starts switched off (:meth:`initial_state`).

        The control law is refused here, when the controller is built, rather than in its first simulated step:
        mode 1 (on/off) is accepted for every emitter, mode 2 (heating, cooling, off) only for floor heating,
        because only a floor can be cooled through. For example, mode 2 with radiators, or a mode of 3, fails the
        construction with the field that is wrong instead of failing the run at its first step.

        Args:
            mode: The control law, 1 or 2.
            upper_temperature_offset_for_state_conditions_in_celsius: How far the water may rise above the requested
                flow temperature before the machine switches off, in K.
            lower_temperature_offset_for_state_conditions_in_celsius: How far it may fall below it before the machine
                switches on, in K.

        Raises:
            ValueError: If ``mode`` is neither 1 nor 2, or is 2 while the emitter is not floor heating.
        """
        if mode not in (1, 2):
            raise ValueError(
                f"{self.component_name}: the space-heating controller's mode must be 1 (on/off) or 2 (heating, "
                f"cooling, off), got {mode!r}."
            )
        if mode == 2 and self.heat_distribution_system_type != HeatDistributionSystemType.FLOORHEATING:
            raise ValueError(
                f"{self.component_name}: the space-heating controller's mode 2 cools through the emitter, which only "
                f"floor heating can do, but the emitter is {self.heat_distribution_system_type!r}."
            )
        # Configuration
        self.mode = mode
        self.upper_temperature_offset_for_state_conditions_in_celsius = (
            upper_temperature_offset_for_state_conditions_in_celsius
        )
        self.lower_temperature_offset_for_state_conditions_in_celsius = (
            lower_temperature_offset_for_state_conditions_in_celsius
        )

    def initial_state(self) -> HeatPumpSpaceHeatingControllerState:
        """Return the state before the first step: off, no part-load ratio and no target.

        Returns:
            The initial state.
        """
        return HeatPumpSpaceHeatingControllerState(
            mode=HeatPumpSpaceHeatingMode.OFF, requested_part_load_ratio_sh=0.0, target_temperature_sh_in_celsius=0.0
        )

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(self) -> List[str]:
        """Write important variables to report."""
        return self.heatpump_controller_config.get_string_dict()

    def decide(
        self, *, timestep: int, stsv: SingleTimeStepValues, state: HeatPumpSpaceHeatingControllerState
    ) -> HeatPumpSpaceHeatingControllerState:
        """Decide heating, cooling or off on the buffer's start temperature, and the requested space-heating ratio.

        For example, a controller that is off, with the buffer at 29 °C below the 30 °C switch-on point of a 35 °C
        supply temperature, returns heating with the ratio that ends the buffer at its 37.5 °C target. Control mode 1
        switches between heating and off (:meth:`conditions_on_off`), mode 2 also cools
        (:meth:`conditions_heating_cooling_off`). The decision is returned as a new state and published by
        :meth:`publish`; a forced pass publishes the state of the last unforced pass (:meth:`publish_forced`).

        Args:
            timestep: The step.
            stsv: The step's values, to read the inputs from.
            state: The state at the start of the step.

        Returns:
            The new state.

        Raises:
            ValueError: If the configured control mode is neither 1 nor 2.
        """
        water_temperature_input_in_celsius = stsv.get_input_value(self.water_temperature_input_channel)
        heating_flow_temperature_from_heat_distribution_system = stsv.get_input_value(self.supply_temperature_set_channel)
        storage_temperature_modifier = stsv.get_input_value(self.simple_hot_water_storage_temperature_modifier_channel)
        # the heat pump heats only in the heating season the heating curve publishes
        summer_heating_mode = "on" if stsv.get_input_value(self.heating_season_channel) > 0.5 else "off"

        # mode 1 is on/off controller
        if self.mode == 1:
            mode = self.conditions_on_off(
                state.mode,
                water_temperature_input_in_celsius=water_temperature_input_in_celsius,
                set_heating_flow_temperature_in_celsius=heating_flow_temperature_from_heat_distribution_system,
                summer_heating_mode=summer_heating_mode,
                storage_temperature_modifier=storage_temperature_modifier,
                upper_temperature_offset_for_state_conditions_in_celsius=self.upper_temperature_offset_for_state_conditions_in_celsius,
                lower_temperature_offset_for_state_conditions_in_celsius=self.lower_temperature_offset_for_state_conditions_in_celsius,
            )
        # mode 2 is the regulated controller (heating, cooling, off); build() admits it only for floor heating
        elif self.mode == 2:
            # turning heat pump cooling mode off when the average daily outside temperature is below a certain threshold
            summer_cooling_mode = self.summer_cooling_condition(
                daily_average_outside_temperature_in_celsius=stsv.get_input_value(
                    self.daily_avg_outside_temperature_input_channel
                ),
                set_cooling_threshold_temperature_in_celsius=self.heatpump_controller_config.set_cooling_threshold_outside_temperature_in_celsius,
            )
            mode = self.conditions_heating_cooling_off(
                state.mode,
                water_temperature_input_in_celsius=water_temperature_input_in_celsius,
                set_heating_flow_temperature_in_celsius=heating_flow_temperature_from_heat_distribution_system,
                summer_heating_mode=summer_heating_mode,
                summer_cooling_mode=summer_cooling_mode,
                storage_temperature_modifier=storage_temperature_modifier,
                upper_temperature_offset_for_state_conditions_in_celsius=self.upper_temperature_offset_for_state_conditions_in_celsius,
                lower_temperature_offset_for_state_conditions_in_celsius=self.lower_temperature_offset_for_state_conditions_in_celsius,
            )
        else:
            raise ValueError(f"The space-heating controller's mode must be 1 or 2, got {self.mode!r}.")

        target_temperature_in_celsius = self.space_heating_target_temperature_in_celsius(
            supply_temperature_set_in_celsius=heating_flow_temperature_from_heat_distribution_system,
            storage_temperature_modifier_in_kelvin=storage_temperature_modifier,
            upper_temperature_offset_in_kelvin=self.upper_temperature_offset_for_state_conditions_in_celsius,
        )
        return HeatPumpSpaceHeatingControllerState(
            mode=mode,
            requested_part_load_ratio_sh=self.space_heating_part_load.next_requested_raising_ratio(
                stsv,
                device_runs=mode == HeatPumpSpaceHeatingMode.HEATING,
                target_temperature_in_celsius=target_temperature_in_celsius,
            ),
            target_temperature_sh_in_celsius=target_temperature_in_celsius,
        )

    def publish(self, *, stsv: SingleTimeStepValues, state: HeatPumpSpaceHeatingControllerState) -> None:
        """Write the outputs that belong to ``state``: the mode as ``State_SH``, the requested ratio and its target.

        Args:
            stsv: The step's values, to write the outputs to.
            state: The state whose outputs are written.
        """
        stsv.set_output_value(self.state_channel, state.mode.value)
        stsv.set_output_value(self.space_heating_part_load.requested_ratio_channel, state.requested_part_load_ratio_sh)
        if self.space_heating_part_load.target_channel is not None:
            stsv.set_output_value(self.space_heating_part_load.target_channel, state.target_temperature_sh_in_celsius)

    def publish_forced(self, *, stsv: SingleTimeStepValues, state: HeatPumpSpaceHeatingControllerState) -> None:
        """Write the outputs of a forced pass: those of ``state``, except the requested ratio above the threshold.

        Above the threshold with the buffer wired the requested ratio becomes the actual ratio the heat pump reports it
        ran with, so the charge stops changing; the state keeps the ratio its pass decided. At and below the threshold,
        or without the buffer, the ratio is the state's, like every other output.

        Args:
            stsv: The step's values.
            state: The state of the last unforced pass.
        """
        self.publish(stsv=stsv, state=state)
        self.space_heating_part_load.publish_held(stsv)

    @classmethod
    def space_heating_target_temperature_in_celsius(
        cls,
        *,
        supply_temperature_set_in_celsius: float,
        storage_temperature_modifier_in_kelvin: float,
        upper_temperature_offset_in_kelvin: float,
    ) -> float:
        """Return the buffer temperature a space-heating charge ends the step at above the part-load threshold, in °C.

        It is the heating curve's supply temperature, raised by the energy manager's modifier and by half the upper
        offset of the controller's hysteresis band (:attr:`SHARE_OF_UPPER_OFFSET_IN_TARGET`). Example: a 35 °C supply
        temperature, a 5 K raise and the default 5 K upper offset give a 42.5 °C target; without a raise 37.5 °C.

        Why half the upper offset (owner decision of 2026-10-19, hisim-hydronics-fxix.27): at 60 s the on/off controller lets the
        buffer swing up to its switch-off point, the supply temperature plus the upper offset, so the heat pump charges
        at a hotter outlet and a lower COP than a charge that stops at the supply temperature. A target at the supply
        temperature made the heat pump's space-heating electricity of the heat-pump twin 4.3 % lower at 900 s than at
        60 s; at half the upper offset it lies within 1 % (measured 2026-10-19). A target at the switch-off point itself
        is worse, because the buffer then coasts down to the switch-on point for whole steps. The offset comes from the
        controller's own band, so a configured band moves the target with it. The raise also lifts the buffer the
        energy manager uses to store surplus electricity as heat.

        Args:
            supply_temperature_set_in_celsius: The heating curve's supply temperature, in °C.
            storage_temperature_modifier_in_kelvin: The energy manager's raise of the buffer, in K; 0 without one.
            upper_temperature_offset_in_kelvin: How far the buffer may rise above the supply temperature before the
                on/off controller switches heating off, in K: the configuration's
                ``upper_temperature_offset_for_state_conditions_in_celsius``.

        Returns:
            The target, in °C.
        """
        return (
            supply_temperature_set_in_celsius
            + storage_temperature_modifier_in_kelvin
            + cls.SHARE_OF_UPPER_OFFSET_IN_TARGET * upper_temperature_offset_in_kelvin
        )

    def conditions_on_off(
        self,
        mode: HeatPumpSpaceHeatingMode,
        *,
        water_temperature_input_in_celsius: float,
        set_heating_flow_temperature_in_celsius: float,
        summer_heating_mode: str,
        storage_temperature_modifier: float,
        upper_temperature_offset_for_state_conditions_in_celsius: float,
        lower_temperature_offset_for_state_conditions_in_celsius: float,
    ) -> HeatPumpSpaceHeatingMode:
        """Return the mode of control mode 1 (on/off) after one decision on the buffer's start temperature.

        Heating switches off above the switch-off point or outside the heating season; off switches to heating below
        the switch-on point in the heating season (:meth:`heating_switch_temperatures_in_celsius`); inside the band the
        mode stays. For example, with a 35 °C flow temperature and 5 K offsets, heating at 39 °C stays heating and
        heating at 41 °C switches off.

        Args:
            mode: The mode at the start of the step.
            water_temperature_input_in_celsius: The buffer's start-of-step temperature, in °C.
            set_heating_flow_temperature_in_celsius: The flow temperature the circuit asks for, in °C.
            summer_heating_mode: "on" in the heating season, "off" outside it.
            storage_temperature_modifier: The energy manager's raise of the band, in K.
            upper_temperature_offset_for_state_conditions_in_celsius: The upper half of the band, in K.
            lower_temperature_offset_for_state_conditions_in_celsius: The lower half of the band, in K.

        Returns:
            The mode after the decision.

        Raises:
            ValueError: If ``mode`` is cooling, which control mode 1 never decides.
        """
        switch_on_in_celsius, switch_off_in_celsius = self.heating_switch_temperatures_in_celsius(
            set_heating_flow_temperature_in_celsius,
            upper_temperature_offset_for_state_conditions_in_celsius,
            lower_temperature_offset_for_state_conditions_in_celsius,
            storage_temperature_modifier,
        )
        if mode == HeatPumpSpaceHeatingMode.HEATING:
            heating_ends = water_temperature_input_in_celsius > switch_off_in_celsius or summer_heating_mode == "off"
            return HeatPumpSpaceHeatingMode.OFF if heating_ends else mode
        if mode == HeatPumpSpaceHeatingMode.OFF:
            heating_starts = self.heating_is_due(water_temperature_input_in_celsius, switch_on_in_celsius, summer_heating_mode)
            return HeatPumpSpaceHeatingMode.HEATING if heating_starts else mode
        raise ValueError(f"{self.component_name}: control mode 1 has no mode {mode!r}.")

    def heating_switch_temperatures_in_celsius(
        self,
        set_heating_flow_temperature_in_celsius: float,
        upper_temperature_offset_for_state_conditions_in_celsius: float,
        lower_temperature_offset_for_state_conditions_in_celsius: float,
        storage_temperature_modifier: float,
    ) -> Tuple[float, float]:
        """Return the water temperatures at which the machine starts and stops heating.

        The hysteresis band puts the switch-on point ``lower_temperature_offset`` below the flow
        temperature the emitter circuit asks for, and the switch-off point ``upper_temperature_offset``
        above it. A floor-heating curve asks for barely more than the room setpoint in mild
        weather (about 23 °C at 21 °C), which put the switch-on point below the room temperature:
        the buffer, sitting at room temperature, never reached it, and the machine stayed off
        from spring to autumn while the room cooled to 18 °C (hisim-q1rm). The switch-on point is
        therefore never below the building's heating setpoint, since water colder than that
        cannot heat the room.

        The switch-off point is floored the same way, at least ``upper_temperature_offset`` above
        the switch-on point: a flow request below ``room setpoint - upper_temperature_offset``
        would otherwise put it below the switch-on point, and the machine would toggle every
        step. The invariant is ``switch-off - switch-on >= upper_temperature_offset``, the
        storage modifier included, since the modifier raises both points alike. Wherever the
        flow temperature is at or above the room setpoint -- which the heat distribution
        controller's heating curve guarantees, its lowest flow being the room setpoint -- the
        switch-off point is exactly ``flow + upper_temperature_offset + modifier``, as it always was.

        Args:
            set_heating_flow_temperature_in_celsius: The flow temperature the circuit asks for.
            upper_temperature_offset_for_state_conditions_in_celsius: The upper half of the band.
            lower_temperature_offset_for_state_conditions_in_celsius: The lower half of the band.
            storage_temperature_modifier: The energy management system's raise of the target.

        Returns:
            The switch-on and the switch-off temperature in °C.
        """
        switch_on_without_modifier_in_celsius = max(
            set_heating_flow_temperature_in_celsius - lower_temperature_offset_for_state_conditions_in_celsius,
            self.set_heating_temperature_for_building_in_celsius,
        )
        switch_off_without_modifier_in_celsius = (
            max(set_heating_flow_temperature_in_celsius, switch_on_without_modifier_in_celsius)
            + upper_temperature_offset_for_state_conditions_in_celsius
        )
        return (
            switch_on_without_modifier_in_celsius + storage_temperature_modifier,
            switch_off_without_modifier_in_celsius + storage_temperature_modifier,
        )

    @staticmethod
    def heating_is_due(
        water_temperature_input_in_celsius: float,
        switch_on_temperature_in_celsius: float,
        summer_heating_mode: str,
    ) -> bool:
        """Return whether an idle machine starts heating: the water is below the switch-on point in the heating season.

        For example, water at 29 °C below a 30 °C switch-on point starts heating in the heating season ("on") and not
        outside it ("off").

        Args:
            water_temperature_input_in_celsius: The buffer's start-of-step temperature, in °C.
            switch_on_temperature_in_celsius: The switch-on point, in °C.
            summer_heating_mode: "on" in the heating season, "off" outside it.

        Returns:
            Whether the machine starts heating.
        """
        return water_temperature_input_in_celsius < switch_on_temperature_in_celsius and summer_heating_mode == "on"

    def conditions_heating_cooling_off(
        self,
        mode: HeatPumpSpaceHeatingMode,
        *,
        water_temperature_input_in_celsius: float,
        set_heating_flow_temperature_in_celsius: float,
        summer_heating_mode: str,
        summer_cooling_mode: str,
        storage_temperature_modifier: float,
        upper_temperature_offset_for_state_conditions_in_celsius: float,
        lower_temperature_offset_for_state_conditions_in_celsius: float,
    ) -> HeatPumpSpaceHeatingMode:
        """Return the mode of control mode 2 (heating, cooling, off) after one decision on the buffer's start temperature.

        Heating switches off at or above the switch-off point or outside the heating season; cooling switches off at or
        below the flow temperature less the lower offset, or when it is too cold for cooling; off starts heating below
        the switch-on point in the heating season (:meth:`heating_is_due`), else cooling above the flow temperature plus
        the upper offset when it is warm enough. For example, off at 41 °C with a 35 °C flow temperature, 5 K offsets
        and the cooling season on switches to cooling.

        Args:
            mode: The mode at the start of the step.
            water_temperature_input_in_celsius: The buffer's start-of-step temperature, in °C.
            set_heating_flow_temperature_in_celsius: The flow temperature the circuit asks for, in °C; also the cooling
                set temperature.
            summer_heating_mode: "on" in the heating season, "off" outside it.
            summer_cooling_mode: "on" when it is warm enough for cooling (:meth:`summer_cooling_condition`).
            storage_temperature_modifier: The energy manager's raise of the heating band, in K.
            upper_temperature_offset_for_state_conditions_in_celsius: The upper half of the band, in K.
            lower_temperature_offset_for_state_conditions_in_celsius: The lower half of the band, in K.

        Returns:
            The mode after the decision.
        """
        # Todo: storage temperature modifier is only working for heating so far. Implement for cooling similar
        cooling_set_temperature = set_heating_flow_temperature_in_celsius
        # Todo: Check if storage_temperature_modifier is neccessary for the switch-off point
        switch_on_in_celsius, switch_off_in_celsius = self.heating_switch_temperatures_in_celsius(
            set_heating_flow_temperature_in_celsius,
            upper_temperature_offset_for_state_conditions_in_celsius,
            lower_temperature_offset_for_state_conditions_in_celsius,
            storage_temperature_modifier,
        )
        if mode == HeatPumpSpaceHeatingMode.HEATING:
            heating_ends = water_temperature_input_in_celsius >= switch_off_in_celsius or summer_heating_mode == "off"
            return HeatPumpSpaceHeatingMode.OFF if heating_ends else mode
        if mode == HeatPumpSpaceHeatingMode.COOLING:
            cooling_ends = (
                water_temperature_input_in_celsius
                <= cooling_set_temperature - lower_temperature_offset_for_state_conditions_in_celsius
                or summer_cooling_mode == "off"
            )
            return HeatPumpSpaceHeatingMode.OFF if cooling_ends else mode
        if self.heating_is_due(water_temperature_input_in_celsius, switch_on_in_celsius, summer_heating_mode):
            return HeatPumpSpaceHeatingMode.HEATING
        # heat pump is only turned on for cooling if the water temperature is above a certain flow temperature
        # and if the avg daily outside temperature is warm enough (summer cooling mode on)
        cooling_is_due = (
            water_temperature_input_in_celsius
            > (cooling_set_temperature + upper_temperature_offset_for_state_conditions_in_celsius)
            and summer_cooling_mode == "on"
        )
        return HeatPumpSpaceHeatingMode.COOLING if cooling_is_due else mode

    def summer_cooling_condition(
        self,
        daily_average_outside_temperature_in_celsius: float,
        set_cooling_threshold_temperature_in_celsius: Optional[float],
    ) -> str:
        """Set conditions for the heat pump."""

        # if no cooling threshold is set, cooling is always possible no matter what daily outside temperature
        if set_cooling_threshold_temperature_in_celsius is None:
            cooling_mode = "on"

        # it is hot enough for cooling
        elif daily_average_outside_temperature_in_celsius > set_cooling_threshold_temperature_in_celsius:
            cooling_mode = "on"

        # it is too cold for cooling
        elif daily_average_outside_temperature_in_celsius < set_cooling_threshold_temperature_in_celsius:
            cooling_mode = "off"

        else:
            raise ValueError(
                f"daily average temperature {daily_average_outside_temperature_in_celsius}°C"
                f"or cooling threshold temperature {set_cooling_threshold_temperature_in_celsius}°C is not acceptable."
            )

        return cooling_mode

    @staticmethod
    def get_cost_capex(
        config: MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig, simulation_parameters: SimulationParameters
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
