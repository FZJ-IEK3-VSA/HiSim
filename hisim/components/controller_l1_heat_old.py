"""Controller l1 heat old module."""

# clean

# Generic/Built-in
from typing import Any
from dataclasses import dataclass
from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import utils


@dataclass_json
@dataclass
class ControllerHeatConfig(cp.ConfigBase):
    """Configuration of the Controller Heat class."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return ControllerHeat.get_full_classname()

    building_name: str
    name: str
    temperature_storage_target_warm_water: float
    temperature_storage_target_heating_water: float
    temperature_storage_target_hysteresis_ww: float
    temperature_storage_target_hysteresis_hw: float

    @classmethod
    def get_default_controller_heat_l1(
        cls,
        building_name: str = "BUI1",
    ) -> Any:
        """Get a default Building."""
        config = ControllerHeatConfig(
            building_name=building_name,
            name="ControllerHeatL1",
            temperature_storage_target_warm_water=50,
            temperature_storage_target_heating_water=35,
            temperature_storage_target_hysteresis_ww=45,
            temperature_storage_target_hysteresis_hw=35,
        )
        return config


class ControllerState:
    """Controller state.

    Save State if Heater Components were supposed to run
    in last timestep (Control_Signal).
    Saves timestep of hysteresis of storages and  the
    changing target temperature of storages. The
    target temperature in states changes when target
    temperature of storage is reached.
    """

    def __init__(
        self,
        control_signal_gas_heater: float,
        control_signal_chp: float,
        control_signal_heat_pump: float,
        temperature_storage_target_ww_c: float,
        temperature_storage_target_hw_c: float,
        timestep_of_hysteresis_ww: int,
        timestep_of_hysteresis_hw: int,
    ) -> None:
        """Initialize the class."""
        self.control_signal_gas_heater: float = control_signal_gas_heater
        self.control_signal_chp: float = control_signal_chp
        self.control_signal_heat_pump: float = control_signal_heat_pump
        self.temperature_storage_target_ww_c: float = temperature_storage_target_ww_c
        self.temperature_storage_target_hw_c: float = temperature_storage_target_hw_c
        self.timestep_of_hysteresis_ww: int = timestep_of_hysteresis_ww
        self.timestep_of_hysteresis_hw: int = timestep_of_hysteresis_hw

    def clone(self) -> "ControllerState":
        """Clones the state."""
        return ControllerState(
            control_signal_gas_heater=self.control_signal_gas_heater,
            control_signal_chp=self.control_signal_chp,
            control_signal_heat_pump=self.control_signal_heat_pump,
            temperature_storage_target_ww_c=self.temperature_storage_target_ww_c,
            temperature_storage_target_hw_c=self.temperature_storage_target_hw_c,
            timestep_of_hysteresis_ww=self.timestep_of_hysteresis_ww,
            timestep_of_hysteresis_hw=self.timestep_of_hysteresis_hw,
        )


class ControllerHeat(cp.Component):
    """Controller heat.

    Controlls energy flows for heat demand.
    Heat Demand will be controlled by the storage temperature.
    For this storage provides heat for a load profile or for the
    building. As well Heat Demand can be simulated without storage.
    """

    # Inputs
    StorageTemperatureHeatingWater = "StorageTemperatureHeatingWater"
    StorageTemperatureWarmWater = "StorageTemperatureWarmWater"
    ResidenceTemperature = "ResidenceTemperature"
    ThermalDemandBuilding = "ThermalDemandBuilding"

    # Outputs
    ControlSignalGasHeater = "ControlSignalGasHeater"
    ControlSignalChp = "ControlSignalChp"
    ControlSignalHeatPump = "ControlSignalHeatPump"
    ControlSignalChooseStorage = "ControlSignalChooseStorage"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ControllerHeatConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
    ) -> None:
        """Initialize the class."""
        self.controller_heat_config = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.mode: Any
        self.temperature_storage_target_warm_water = self.controller_heat_config.temperature_storage_target_warm_water
        self.temperature_storage_target_heating_water = (
            self.controller_heat_config.temperature_storage_target_heating_water
        )
        self.temperature_storage_target_hysteresis_hw = (
            self.controller_heat_config.temperature_storage_target_hysteresis_hw
        )
        self.temperature_storage_target_hysteresis_ww = (
            self.controller_heat_config.temperature_storage_target_hysteresis_ww
        )

        self.state = ControllerState(
            control_signal_heat_pump=0,
            control_signal_gas_heater=0,
            control_signal_chp=0,
            temperature_storage_target_ww_c=self.temperature_storage_target_warm_water,
            temperature_storage_target_hw_c=self.temperature_storage_target_heating_water,
            timestep_of_hysteresis_ww=0,
            timestep_of_hysteresis_hw=0,
        )
        self.previous_state = self.state.clone()

        # Inputs
        self.temperature_storage_warm_water_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.StorageTemperatureWarmWater,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            False,
        )
        self.temperature_storage_heating_water_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.StorageTemperatureHeatingWater,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            False,
        )
        self.temperature_residence_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ResidenceTemperature,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            False,
        )

        # Outputs
        self.control_signal_gas_heater_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ControlSignalGasHeater,
            load_type=lt.LoadTypes.ANY,
            unit=lt.Units.PERCENT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ControlSignalGasHeater} will follow.",
        )
        self.control_signal_chp_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ControlSignalChp,
            load_type=lt.LoadTypes.ANY,
            unit=lt.Units.PERCENT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ControlSignalChp} will follow.",
        )
        self.control_signal_heat_pump_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ControlSignalHeatPump,
            load_type=lt.LoadTypes.ANY,
            unit=lt.Units.PERCENT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ControlSignalHeatPump} will follow.",
        )
        self.control_signal_choose_storage_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ControlSignalChooseStorage,
            load_type=lt.LoadTypes.ANY,
            unit=lt.Units.ANY,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ControlSignalChooseStorage} will follow.",
        )

    def build(self, mode: Any) -> None:
        """Build function."""
        self.mode = mode

    def write_to_report(self):
        """Write to report."""
        return self.controller_heat_config.get_string_dict()

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

    # Simulates and defines the control signals to heat up storages
    # work as a 2-point Ruler with Hysteresis
    def simulate_storage(
        self,
        delta_temperature: float,
        stsv: cp.SingleTimeStepValues,
        timestep: int,
        temperature_storage: float,
        temperature_storage_target: float,
        temperature_storage_target_hysteresis: float,
        temperature_storage_target_c: float,
        timestep_of_hysteresis: int,
    ) -> Any:
        """Simulates the storage."""
        control_signal_chp: float = 0
        control_signal_gas_heater: float = 0
        control_signal_heat_pump: float = 0

        max_temperature_limit = 5
        if temperature_storage > 0:
            if delta_temperature > max_temperature_limit:
                control_signal_heat_pump = 1
                control_signal_chp = 1
                control_signal_gas_heater = 1
                temperature_storage_target_c = temperature_storage_target

            elif 0 < delta_temperature <= max_temperature_limit:
                control_signal_heat_pump = 1
                control_signal_chp = 1
                control_signal_gas_heater = 1

                if self.state.control_signal_chp < control_signal_chp:
                    control_signal_chp = 1
                elif self.state.control_signal_gas_heater < control_signal_gas_heater:
                    control_signal_gas_heater = 1
                temperature_storage_target_c = temperature_storage_target

                # Storage warm enough. Try to turn off Heaters
            elif delta_temperature <= 0:
                if temperature_storage_target_c == temperature_storage_target and timestep_of_hysteresis != timestep:
                    temperature_storage_target_c = temperature_storage_target_hysteresis
                    timestep_of_hysteresis = timestep
                elif temperature_storage_target_c != temperature_storage_target and timestep_of_hysteresis != timestep:
                    control_signal_heat_pump = 0
                    control_signal_gas_heater = 0
                    control_signal_chp = 0

        self.state.control_signal_gas_heater = control_signal_gas_heater
        self.state.control_signal_chp = control_signal_chp
        self.state.control_signal_heat_pump = control_signal_heat_pump
        stsv.set_output_value(self.control_signal_heat_pump_channel, control_signal_heat_pump)
        stsv.set_output_value(self.control_signal_gas_heater_channel, control_signal_gas_heater)
        stsv.set_output_value(self.control_signal_chp_channel, control_signal_chp)

        return temperature_storage_target_c, timestep_of_hysteresis

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates the component."""
        if force_convergence:
            return
        # Heat
        # Logic of regulating HeatDemand:
        # First heat up WarmWaterStorage->more important, than heat up HeatingWater
        # But only one Storage can be heated up in a TimeStep!
        # Simulate WarmWater
        delta_temperature_ww = self.state.temperature_storage_target_ww_c - stsv.get_input_value(
            self.temperature_storage_warm_water_channel
        )
        delta_temperature_hw = self.state.temperature_storage_target_hw_c - stsv.get_input_value(
            self.temperature_storage_heating_water_channel
        )
        if (
            stsv.get_input_value(self.temperature_storage_warm_water_channel) == 0
            and stsv.get_input_value(self.temperature_storage_heating_water_channel) != 0
        ):
            control_signal_choose_storage = 2
        elif (
            stsv.get_input_value(self.temperature_storage_warm_water_channel) != 0
            and stsv.get_input_value(self.temperature_storage_heating_water_channel) == 0
        ):
            control_signal_choose_storage = 1
        else:
            # Choose which Storage should be heated up
            if delta_temperature_ww >= 0 and delta_temperature_hw >= 0:
                if delta_temperature_hw <= delta_temperature_ww:
                    control_signal_choose_storage = 1
                else:
                    control_signal_choose_storage = 2
            elif delta_temperature_ww < 0 and delta_temperature_hw < 0:
                if delta_temperature_hw <= delta_temperature_ww:
                    control_signal_choose_storage = 1
                else:
                    control_signal_choose_storage = 2
            elif delta_temperature_ww <= 0 <= delta_temperature_hw:
                control_signal_choose_storage = 2
            elif delta_temperature_hw <= 0 <= delta_temperature_ww:
                control_signal_choose_storage = 1

        # Heats up storage
        if control_signal_choose_storage == 1:
            (
                self.state.temperature_storage_target_ww_c,
                self.state.timestep_of_hysteresis_ww,
            ) = self.simulate_storage(
                stsv=stsv,
                delta_temperature=delta_temperature_ww,
                timestep=timestep,
                temperature_storage=stsv.get_input_value(self.temperature_storage_warm_water_channel),
                temperature_storage_target=self.temperature_storage_target_warm_water,
                temperature_storage_target_hysteresis=self.temperature_storage_target_hysteresis_ww,
                temperature_storage_target_c=self.state.temperature_storage_target_ww_c,
                timestep_of_hysteresis=self.state.timestep_of_hysteresis_ww,
            )
        elif control_signal_choose_storage == 2:
            delta_temperature_hw = self.state.temperature_storage_target_hw_c - stsv.get_input_value(
                self.temperature_storage_heating_water_channel
            )
            (
                self.state.temperature_storage_target_hw_c,
                self.state.timestep_of_hysteresis_hw,
            ) = self.simulate_storage(
                stsv=stsv,
                delta_temperature=delta_temperature_hw,
                timestep=timestep,
                temperature_storage=stsv.get_input_value(self.temperature_storage_heating_water_channel),
                temperature_storage_target=self.temperature_storage_target_heating_water,
                temperature_storage_target_hysteresis=self.temperature_storage_target_hysteresis_hw,
                temperature_storage_target_c=self.state.temperature_storage_target_hw_c,
                timestep_of_hysteresis=self.state.timestep_of_hysteresis_hw,
            )

        stsv.set_output_value(self.control_signal_choose_storage_channel, control_signal_choose_storage)
