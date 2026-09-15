"""Simple implementation of combined heat and power plant (CHP).

This can be either a natural gas driven turbine producing both electricity and heat,
or also a fuel cell. In this implementation the CHP does not modulate: it is either
on or off. When it runs, it outputs a constant thermal and electrical power signal
and needs a constant input of hydrogen or natural gas.
"""

from dataclasses import dataclass
from typing import ClassVar, List

from dataclasses_json import dataclass_json
from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components.generic_chp import controller
from hisim.simulationparameters import SimulationParameters
from hisim.config import (
    ComponentID,
    ConfigBase,
    DisplayConfig,
    Self,
    Sizable,
    SizingLaw,
    concrete,
    preset,
    sized_field,
)
from hisim.economics.facts import CostRelevance


@dataclass_json
@dataclass
class CHPConfig(ConfigBase):
    """Configuration of the non-modulating combined heat and power plant.

    The machine is described by one number the author states -- the thermal power it
    delivers while it runs -- and two that follow from it: the electricity it produces
    beside that heat, and the fuel it burns to make both. The two follow because a CHP is
    quoted by its overall efficiency and by the share of that output which is electrical,
    so both derive from the thermal power by a fixed ratio once the machine's technology
    is chosen. Which ratios apply is what the two presets differ in::

        CHPConfig.preset_hydrogen("CHP").resolve(SizingContext())

    builds a fuel cell of ``p_th`` thermal watt, ``(0.48 / 0.43) * p_th`` electrical watt
    and ``(1 / 0.43) * p_th`` watt of hydrogen input, while ``preset_gas`` builds a
    gas-driven turbine at the gas figures below. The two derived fields are sizable, so a
    configuration has to be resolved before a component can be built from it -- an author
    who pins a different ``p_th`` gets the electricity and the fuel recomputed from it
    rather than left at the preset's numbers.
    """

    MAIN_CLASS = "hisim.components.generic_chp.chp.SimpleCHP"

    #: Share of the fuel energy a gas-driven CHP delivers as heat and electricity together.
    GAS_OVERALL_EFFICIENCY: ClassVar[float] = 0.5
    #: Share of the fuel energy a gas-driven CHP delivers as electricity alone.
    GAS_ELECTRICAL_SHARE: ClassVar[float] = 0.33
    #: Share of the fuel energy a hydrogen fuel cell delivers as heat and electricity together.
    HYDROGEN_OVERALL_EFFICIENCY: ClassVar[float] = 0.43
    #: Share of the fuel energy a hydrogen fuel cell delivers as electricity alone.
    HYDROGEN_ELECTRICAL_SHARE: ClassVar[float] = 0.48

    #: Electrical power of a gas-driven CHP: the thermal power times the ratio of the two
    #: gas efficiencies, which is what "0.33 of the fuel as electricity where 0.5 of it is
    #: useful at all" comes to once the fuel input is eliminated.
    GAS_ELECTRIC_POWER_LAW: ClassVar[SizingLaw] = Self("p_th") * (GAS_ELECTRICAL_SHARE / GAS_OVERALL_EFFICIENCY)
    #: Fuel input of a gas-driven CHP: the thermal power divided by the overall efficiency.
    GAS_FUEL_POWER_LAW: ClassVar[SizingLaw] = Self("p_th") * (1 / GAS_OVERALL_EFFICIENCY)
    #: Electrical power of a hydrogen fuel cell, the hydrogen counterpart of the gas law above.
    HYDROGEN_ELECTRIC_POWER_LAW: ClassVar[SizingLaw] = Self("p_th") * (
        HYDROGEN_ELECTRICAL_SHARE / HYDROGEN_OVERALL_EFFICIENCY
    )
    #: Fuel input of a hydrogen fuel cell, the hydrogen counterpart of the gas law above.
    HYDROGEN_FUEL_POWER_LAW: ClassVar[SizingLaw] = Self("p_th") * (1 / HYDROGEN_OVERALL_EFFICIENCY)

    component_id: ComponentID
    #: Type of CHP: a gas-driven turbine or a hydrogen fuel cell. It decides the unit and the
    #: load type of the fuel channel, so it is what a preset states rather than a default.
    fuel_type: lt.LoadTypes
    #: Thermal power of the CHP in Watt, while it runs. Nominally 500 W, which is not a
    #: catalogue rating but the only size anything in this repository runs the machine at;
    #: an author who has a machine in mind states its rating instead, and the electrical
    #: power and the fuel input follow from whatever is stated here.
    p_th: float = 500.0
    #: Priority of the component in the hierarchy: the higher the number, the lower the priority.
    source_weight: int = 1
    #: Electrical power of the CHP in Watt, while it runs, derived from the thermal power.
    #: The field carries the gas law; :meth:`preset_hydrogen` replaces it with the hydrogen one.
    p_el: Sizable[float] = sized_field(rule=GAS_ELECTRIC_POWER_LAW)
    #: Demanded power of the fuel input in Watt, while it runs, derived from the thermal power.
    #: The field carries the gas law; :meth:`preset_hydrogen` replaces it with the hydrogen one.
    p_fuel: Sizable[float] = sized_field(rule=GAS_FUEL_POWER_LAW)

    @preset
    @classmethod
    def preset_gas(cls, name: str) -> "CHPConfig":
        """Natural-gas driven CHP, half of whose fuel is useful and a third of it electricity."""
        return cls(
            component_id=ComponentID(name=name),
            fuel_type=lt.LoadTypes.GAS,
        )

    @preset
    @classmethod
    def preset_hydrogen(cls, name: str) -> "CHPConfig":
        """Green-hydrogen fuel cell, whose electrical share is the larger half of its output."""
        return cls(
            component_id=ComponentID(name=name),
            fuel_type=lt.LoadTypes.GREEN_HYDROGEN,
            p_el=cls.HYDROGEN_ELECTRIC_POWER_LAW,
            p_fuel=cls.HYDROGEN_FUEL_POWER_LAW,
        )


class GenericCHPState:
    """Generic chp state class saves the state of the CHP."""

    def __init__(self, state: int) -> None:
        """Initialize the class."""
        self.state: int = state

    def clone(self) -> "GenericCHPState":
        """Clones the state."""
        return GenericCHPState(state=self.state)


class SimpleCHP(cp.Component):
    """Simulates CHP operation with constant electical and thermal power as well as constant fuel consumption.

    Components to connect to:
    (1) CHP or fuel cell controller (hisim.components.generic_chp.controller)
    """

    cost_relevance = CostRelevance.PRICED

    # Inputs
    CHPControllerOnOffSignal: ClassVar[str] = "CHPControllerOnOffSignal"
    CHPControllerHeatingModeSignal: ClassVar[str] = "CHPControllerHeatingModeSignal"

    # Outputs
    ThermalPowerOutputBuilding: ClassVar[str] = "ThermalPowerOutputBuilding"
    ThermalPowerOutputDHW: ClassVar[str] = "ThermalPowerOutputDHW"
    ElectricityOutput: ClassVar[str] = "ElectricityOutput"
    FuelDelivered: ClassVar[str] = "FuelDelivered"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: CHPConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Initializes the class."""
        self.my_simulation_parameters: SimulationParameters = my_simulation_parameters
        self.config: CHPConfig = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        fuel_power_in_watt = concrete(config.p_fuel)
        if self.config.fuel_type == lt.LoadTypes.GREEN_HYDROGEN:
            self.p_fuel: float = fuel_power_in_watt / (3.6e3 * 3.939e4)  # converted to kg / s
        else:
            self.p_fuel = fuel_power_in_watt * my_simulation_parameters.seconds_per_timestep / 3.6e3  # to Wh

        self.state: GenericCHPState = GenericCHPState(state=0)
        self.previous_state: GenericCHPState = self.state.clone()

        # Inputs
        self.chp_onoff_signal_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.CHPControllerOnOffSignal,
            lt.LoadTypes.ON_OFF,
            lt.Units.BINARY,
            mandatory=True,
        )

        self.chp_heatingmode_signal_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.CHPControllerHeatingModeSignal,
            lt.LoadTypes.ANY,
            lt.Units.BINARY,
            mandatory=True,
        )

        # Component outputs
        self.thermal_power_output_building_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalPowerOutputBuilding,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT,
            postprocessing_flag=[lt.InandOutputType.THERMAL_PRODUCTION],
            output_description="Thermal Power output from CHP to building or buffer in Watt.",
        )
        self.thermal_power_output_dhw_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalPowerOutputDHW,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT,
            postprocessing_flag=[lt.InandOutputType.THERMAL_PRODUCTION],
            output_description="Thermal Power output from CHP to drain hot water storage in Watt.",
        )
        self.electricity_output_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityOutput,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            postprocessing_flag=[
                lt.InandOutputType.ELECTRICITY_PRODUCTION,
                lt.ComponentType.FUEL_CELL,
            ],
            output_description="Electrical Power output of CHP in Watt.",
        )
        if self.config.fuel_type == lt.LoadTypes.GREEN_HYDROGEN:
            self.fuel_consumption_channel: cp.ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.FuelDelivered,
                load_type=lt.LoadTypes.GREEN_HYDROGEN,
                unit=lt.Units.KG_PER_SEC,
                postprocessing_flag=[lt.LoadTypes.GREEN_HYDROGEN],
                output_description="Hydrogen consumption of CHP in kg / s.",
            )
        elif self.config.fuel_type == lt.LoadTypes.GAS:
            self.fuel_consumption_channel = self.add_output(
                object_name=self.component_name,
                field_name=self.FuelDelivered,
                load_type=lt.LoadTypes.GAS,
                unit=lt.Units.WATT_HOUR,
                postprocessing_flag=[
                    lt.InandOutputType.FUEL_CONSUMPTION,
                    lt.LoadTypes.GAS,
                ],
                output_description="Gas consumption of CHP in Wh.",
            )
        self.add_default_connections(self.get_default_connections_from_chp_controller())

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates the component."""
        # Inputs
        self.state.state = int(stsv.get_input_value(self.chp_onoff_signal_channel))
        heating_mode = stsv.get_input_value(self.chp_heatingmode_signal_channel)

        # Outputs
        if heating_mode == 0:
            stsv.set_output_value(
                self.thermal_power_output_dhw_channel,
                self.state.state * self.config.p_th,
            )
            stsv.set_output_value(self.thermal_power_output_building_channel, 0)
        elif heating_mode == 1:
            stsv.set_output_value(self.thermal_power_output_dhw_channel, 0)
            stsv.set_output_value(
                self.thermal_power_output_building_channel,
                self.state.state * self.config.p_th,
            )

        stsv.set_output_value(self.electricity_output_channel, self.state.state * concrete(self.config.p_el))
        stsv.set_output_value(self.fuel_consumption_channel, self.state.state * self.p_fuel)

    def get_default_connections_from_chp_controller(
        self,
    ) -> List[cp.ComponentConnection]:
        """Sets default connections of the controller in the Fuel Cell / CHP."""

        controller_classname = controller.L1CHPController.get_classname()
        return [
            cp.ComponentConnection(
                SimpleCHP.CHPControllerOnOffSignal,
                controller_classname,
                controller.L1CHPController.CHPControllerOnOffSignal,
            ),
            cp.ComponentConnection(
                SimpleCHP.CHPControllerHeatingModeSignal,
                controller_classname,
                controller.L1CHPController.CHPControllerHeatingModeSignal,
            ),
        ]

    def write_to_report(self) -> List[str]:
        """Writes the information of the current component to the report."""
        # Despite its name, ConfigBase.get_string_dict() returns a List[str] of
        # formatted "key: value" entries (see ConfigBase), not a dict.
        return self.config.get_string_dict()
