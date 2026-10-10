"""Rigs for the part-load tests: simulation parameters, components on fake inputs, and a tank iterated with the rule.

A rig points every input a test drives at a fake output and steps the component on its own, the way
``tests/test_dhw_storage_node.py`` steps the hot-water tank, so a test sets exactly the values the component reads.
"""

from typing import Any, Dict, List, Optional, Tuple

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components import (
    generic_boiler,
    generic_electric_heating,
    more_advanced_heat_pump_hplib,
    simple_water_storage,
)
from hisim.components.more_advanced_heat_pump_hplib import PositionHotWaterStorageInSystemSetup
from hisim.components.dual_circuit_system import HeatingMode
from hisim.config import ComponentID, SizingContext
from hisim.part_load import PartLoadRule
from hisim.simulationparameters import SimulationParameters


class Parameters:
    """Simulation parameters for the threshold tests."""

    @staticmethod
    def one_day(seconds_per_timestep: int = 3600, **extra: Any) -> SimulationParameters:
        """Return one day of 2021 at a resolution, with the given extra constructor arguments.

        Args:
            seconds_per_timestep: The step length, s.
            **extra: Further arguments of :class:`SimulationParameters`, such as ``part_load_above_seconds``.

        Returns:
            The simulation parameters.
        """
        return SimulationParameters(
            start_date=SimulationParameters.one_day_only(2021, seconds_per_timestep).start_date,
            end_date=SimulationParameters.one_day_only(2021, seconds_per_timestep).end_date,
            seconds_per_timestep=seconds_per_timestep,
            **extra,
        )


class Rig:
    """A component whose inputs read fake outputs, and the step values they all write into."""

    @staticmethod
    def wire(component: Any, channels: List[Tuple[Any, Any, Any]]) -> Tuple[Any, List[Any]]:
        """Point every listed input of ``component`` at a fake output; return the step values and the fakes.

        The fakes take the first indices of the step values and the component's outputs the following ones, in the
        order the component declared them, as the simulator registers them.

        Args:
            component: The component under test.
            channels: Its inputs, each with the load type and unit of the fake output that feeds it.

        Returns:
            The step values sized for the fakes and the component, and the fakes in the order of ``channels``.
        """
        fakes: List[Any] = []
        for number, (channel, load_type, unit) in enumerate(channels):
            fake = cp.ComponentOutput(
                f"Fake{number}", f"Fake{number}", load_type, unit, component_id=ComponentID(f"Fake{number}")
            )
            channel.source_output = fake
            fakes.append(fake)
        for index, output in enumerate([*fakes, *component.outputs]):
            output.global_index = index
        stsv = cp.SingleTimeStepValues(len(fakes) + len(component.outputs))
        return stsv, fakes

    @staticmethod
    def output(stsv: Any, channel: Any) -> float:
        """Return the value a component set on one of its output channels in ``stsv``."""
        return float(stsv.values[channel.global_index])


class TankRig:
    """A 250 l hot-water tank on fake inputs: the draw and one charging circuit's supply and flow."""

    @staticmethod
    def build(seconds_per_timestep: int, start_temperature_in_celsius: float) -> Tuple[Any, Any, List[Any]]:
        """Return the tank, its step values and its fakes: consumption (l per step), supply (°C), flow (kg/s).

        Args:
            seconds_per_timestep: The step length, s.
            start_temperature_in_celsius: The tank's temperature at the start of the first step, °C.

        Returns:
            The tank, the step values and the three fakes.
        """
        parameters = SimulationParameters.one_day_only(2021, seconds_per_timestep)
        config = simple_water_storage.SimpleDHWStorageConfig(
            component_id=ComponentID(name="DHWStorage"), volume_heating_water_storage_in_liter=250.0
        )
        tank = simple_water_storage.SimpleDHWStorage(my_simulation_parameters=parameters, config=config)
        stsv, fakes = Rig.wire(
            tank,
            [
                (tank.water_consumption_channel, lt.LoadTypes.WARM_WATER, lt.Units.LITER),
                (tank.water_temperature_heat_generator_input_channel, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
                (tank.water_mass_flow_rate_heat_generator_input_channel, lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC),
            ],
        )
        tank.state = simple_water_storage.DhwTankState(temperature_at_start_of_step_in_celsius=start_temperature_in_celsius)
        return tank, stsv, fakes

    @classmethod
    def ruled_step(
        cls,
        *,
        seconds_per_timestep: int,
        start_temperature_in_celsius: float,
        target_temperature_in_celsius: float,
        draw_in_liter_per_step: float,
        full_load_mass_flow_in_kg_per_second: float,
        supply_temperature_in_celsius: float,
        passes: int,
    ) -> Tuple[List[float], float]:
        """Iterate one tank step with :class:`PartLoadRule` commanding a heater's fraction, as the simulator would.

        Each pass restores the tank to the step start, applies the rule to the ratio the heater ran in the previous
        pass and the tank's end temperature for it, and steps the tank with the averaged flow at the new ratio:
        controller, device and tank in that order. The first pass starts from the whole step, as a heater that ran
        nothing before does under the rule. Nothing but the ratio run and the end temperature carries from one pass to
        the next, as in the simulator.

        Args:
            seconds_per_timestep: The step length, in s.
            start_temperature_in_celsius: The tank's start temperature, in °C.
            target_temperature_in_celsius: The controller's target, in °C.
            draw_in_liter_per_step: The household's warm-water demand over the step, in l.
            full_load_mass_flow_in_kg_per_second: The heater's flow at full load, in kg/s.
            supply_temperature_in_celsius: The heater's supply temperature, in °C.
            passes: How many passes to iterate.

        Returns:
            The ratio of every pass and the tank's end temperature for the last one, in °C.
        """
        tank, stsv, fakes = cls.build(seconds_per_timestep, start_temperature_in_celsius)
        tank.i_save_state()
        stsv.set_output_value(fakes[0], draw_in_liter_per_step)
        stsv.set_output_value(fakes[1], supply_temperature_in_celsius)
        end_temperature_in_celsius = start_temperature_in_celsius
        ratio_run = 0.0
        ratios: List[float] = []
        for _ in range(passes):
            tank.i_restore_state()
            ratio_run = PartLoadRule.next_part_load_ratio(
                ratio_run=ratio_run,
                start_temperature_in_celsius=start_temperature_in_celsius,
                end_temperature_in_celsius=end_temperature_in_celsius,
                target_temperature_in_celsius=target_temperature_in_celsius,
            )
            ratios.append(ratio_run)
            stsv.set_output_value(fakes[2], ratio_run * full_load_mass_flow_in_kg_per_second)
            tank.i_simulate(0, stsv, False)
            end_temperature_in_celsius = Rig.output(stsv, tank.water_temperature_at_end_of_step_in_celsius_channel)
        return ratios, end_temperature_in_celsius


class BoilerRig:
    """A condensing gas boiler on fake inputs, charging the hot-water tank at the ratio its controller commands."""

    #: The boiler's thermal power band, W.
    MAXIMAL_POWER_W: float = 20000.0

    @classmethod
    def build(cls, seconds_per_timestep: int, **extra: Any) -> Tuple[Any, Any, Dict[str, Any]]:
        """Return the boiler, its step values and its fakes by input name.

        Args:
            seconds_per_timestep: The step length, s.
            **extra: Further simulation-parameter arguments.

        Returns:
            The boiler, the step values and the fakes keyed by a short name.
        """
        config = generic_boiler.GenericBoilerConfig.preset_condensing_gas("Boiler").resolve(
            SizingContext(heating_load_in_watt=cls.MAXIMAL_POWER_W / 1.1, number_of_apartments=1)
        )
        parameters = Parameters.one_day(seconds_per_timestep, **extra)
        boiler = generic_boiler.GenericBoiler(my_simulation_parameters=parameters, config=config)
        names = {
            "signal": (boiler.control_signal_channel, lt.LoadTypes.ANY, lt.Units.PERCENT),
            "mode": (boiler.operating_mode_channel, lt.LoadTypes.ANY, lt.Units.ANY),
            "lift_k": (boiler.temperature_delta_channel, lt.LoadTypes.TEMPERATURE, lt.Units.KELVIN),
            "set_c": (
                boiler.supply_temperature_set_for_dhw_in_celsius_channel,
                lt.LoadTypes.TEMPERATURE,
                lt.Units.CELSIUS,
            ),
            "ratio": (boiler.dhw_part_load_command.ratio_channel, lt.LoadTypes.ANY, lt.Units.FRACTION),
            "return_sh_c": (boiler.water_input_temperature_sh_channel, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
            "return_dhw_c": (boiler.water_input_temperature_dhw_channel, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
        }
        stsv, fakes = Rig.wire(boiler, list(names.values()))
        return boiler, stsv, dict(zip(names, fakes))

    @classmethod
    def charge(
        cls, seconds_per_timestep: int, ratio: float, mode: HeatingMode = HeatingMode.DOMESTIC_HOT_WATER
    ) -> Dict[str, float]:
        """Return the booked outputs of one step at full signal from a 55 °C tank mean, 15 K lift and 70 °C cap.

        Args:
            seconds_per_timestep: The step length, s.
            ratio: The part-load ratio the controller commands.
            mode: The operating mode the controller commands.

        Returns:
            The flow (kg/s), supply (°C), heat (W), fuel (W) and fuel energy (Wh) of the hot-water circuit, the
            ratio the boiler reports it ran with, and the space-heating heat (W).
        """
        boiler, stsv, fakes = cls.build(seconds_per_timestep)
        for name, value in (
            ("signal", 1.0),
            ("mode", mode.value),
            ("lift_k", 15.0),
            ("set_c", 70.0),
            ("ratio", ratio),
            ("return_sh_c", 40.0),
            ("return_dhw_c", 55.0),
        ):
            stsv.set_output_value(fakes[name], value)
        boiler.i_simulate(0, stsv, False)
        return {
            "flow_kg_per_s": Rig.output(stsv, boiler.water_output_mass_flow_dhw_channel),
            "supply_c": Rig.output(stsv, boiler.water_output_temperature_dhw_channel),
            "heat_w": Rig.output(stsv, boiler.thermal_output_power_dhw_channel),
            "fuel_w": Rig.output(stsv, boiler.total_fuel_input_power_channel),
            "fuel_wh": Rig.output(stsv, boiler.energy_demand_dhw_channel),
            "ratio_run": Rig.output(stsv, boiler.dhw_part_load_command.ratio_run_channel),
            "space_heating_heat_w": Rig.output(stsv, boiler.thermal_output_power_sh_channel),
        }


class BoilerControllerRig:
    """A modulating boiler controller with hot water on fake inputs."""

    @staticmethod
    def build(seconds_per_timestep: int, **extra: Any) -> Tuple[Any, Any, Dict[str, Any]]:
        """Return the controller, its step values and its fakes by input name.

        Args:
            seconds_per_timestep: The step length, s.
            **extra: Further simulation-parameter arguments.

        Returns:
            The controller, the step values and the fakes keyed by a short name.
        """
        config = generic_boiler.GenericBoilerControllerConfig.preset_modulating("BoilerController").resolve(
            SizingContext(maximal_thermal_power_in_watt=20000, minimal_thermal_power_in_watt=2000)
        )
        config.with_domestic_hot_water_preparation = True
        controller = generic_boiler.GenericBoilerController(
            my_simulation_parameters=Parameters.one_day(seconds_per_timestep, **extra), config=config
        )
        names = {
            "buffer_c": (
                controller.water_temperature_space_heating_input_channel,
                lt.LoadTypes.TEMPERATURE,
                lt.Units.CELSIUS,
            ),
            "tank_start_c": (controller.water_temperature_dhw_input_channel, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
            "tank_end_c": (
                controller.dhw_part_load.end_temperature_channel,
                lt.LoadTypes.TEMPERATURE,
                lt.Units.CELSIUS,
            ),
            "ratio_run": (controller.dhw_part_load.ratio_run_channel, lt.LoadTypes.ANY, lt.Units.FRACTION),
            "flow_set_c": (
                controller.heating_flow_temperature_from_heat_distribution_system_channel,
                lt.LoadTypes.TEMPERATURE,
                lt.Units.CELSIUS,
            ),
            "outside_c": (
                controller.daily_avg_outside_temperature_input_channel,
                lt.LoadTypes.TEMPERATURE,
                lt.Units.CELSIUS,
            ),
        }
        stsv, fakes = Rig.wire(controller, list(names.values()))
        return controller, stsv, dict(zip(names, fakes))

    @staticmethod
    def set_inputs(
        stsv: Any, fakes: Dict[str, Any], *, tank_start_c: float, tank_end_c: float, ratio_run: float = 1.0
    ) -> None:
        """Write a cold winter day with a warm buffer, the given tank temperatures and the boiler's ratio run.

        Args:
            stsv: The controller's step values.
            fakes: The fakes by name.
            tank_start_c: The tank's start-of-step temperature, °C.
            tank_end_c: The tank's end-of-step temperature, °C.
            ratio_run: The part-load ratio the boiler reports it ran with, from 0 to 1.
        """
        for name, value in (
            ("buffer_c", 60.0),
            ("tank_start_c", tank_start_c),
            ("tank_end_c", tank_end_c),
            ("ratio_run", ratio_run),
            ("flow_set_c", 40.0),
            ("outside_c", 0.0),
        ):
            stsv.set_output_value(fakes[name], value)


class AllInputsRig:
    """A component whose every input reads a fake output of its own, set and read by field name."""

    @staticmethod
    def build(component: Any) -> Tuple[Any, Dict[str, Any]]:
        """Point every input of ``component`` at its own fake output; return the step values and the fakes by name.

        Args:
            component: The component under test.

        Returns:
            The step values and the fakes keyed by the field name of the input each one feeds.
        """
        channels = [(component_input, lt.LoadTypes.ANY, lt.Units.ANY) for component_input in component.inputs]
        stsv, fakes = Rig.wire(component, channels)
        return stsv, {component_input.field_name: fake for component_input, fake in zip(component.inputs, fakes)}

    @staticmethod
    def step(
        component: Any,
        stsv: Any,
        fakes: Dict[str, Any],
        values: Dict[str, float],
        *,
        timestep: int = 0,
        force_convergence: bool = False,
    ) -> Dict[str, float]:
        """Write ``values`` into the fakes (every other fake reads 0), simulate one pass and return the outputs by name.

        Args:
            component: The component under test.
            stsv: Its step values.
            fakes: Its fakes by input field name.
            values: The input values by field name.
            timestep: The step number passed to ``i_simulate``.
            force_convergence: Whether the pass is forced.

        Returns:
            Every output of the component by field name.
        """
        for field_name, fake in fakes.items():
            stsv.set_output_value(fake, values.get(field_name, 0.0))
        component.i_simulate(timestep, stsv, force_convergence)
        return {output.field_name: Rig.output(stsv, output) for output in component.outputs}


class HeatPumpRig:
    """A 10 kW generic air/water heat pump charging the hot-water tank, one step on fake inputs."""

    @staticmethod
    def build(
        seconds_per_timestep: int, minimum_running_time_in_seconds: Optional[int] = None, **extra: Any
    ) -> Tuple[Any, Any, Dict[str, Any]]:
        """Return the heat pump with hot water and a parallel storage, running for ten minutes already, and its rig.

        Args:
            seconds_per_timestep: The step length, s.
            minimum_running_time_in_seconds: With a value, the heat pump cycles and keeps a charge running for this
                long; without, it does not cycle.
            **extra: Further simulation-parameter arguments.

        Returns:
            The heat pump, its step values and its fakes by input field name.
        """
        config = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump")
        config.set_thermal_output_power_in_watt = 10000.0
        config.heating_reference_temperature_in_celsius = -7.0
        config.flow_temperature_in_celsius = 52.0
        config.cycling_mode = minimum_running_time_in_seconds is not None
        if minimum_running_time_in_seconds is not None:
            config.minimum_running_time_in_seconds = minimum_running_time_in_seconds
            config.minimum_idle_time_in_seconds = 600
        config.minimum_thermal_output_power_in_watt = 1500.0
        config.massflow_nominal_secondary_side_in_kg_per_s = 0.333
        config.with_domestic_hot_water_preparation = True
        config.position_hot_water_storage_in_system = PositionHotWaterStorageInSystemSetup.PARALLEL
        heat_pump = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib(
            config=config, my_simulation_parameters=Parameters.one_day(seconds_per_timestep, **extra)
        )
        heat_pump.state = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibState(
            time_on_heating=600,
            time_off=0,
            time_on_cooling=0,
            on_off_previous=2,
            cumulative_thermal_energy_tot_in_watt_hour=0,
            cumulative_thermal_energy_space_heating_in_watt_hour=0,
            cumulative_thermal_energy_dhw_in_watt_hour=0,
            cumulative_electrical_energy_tot_in_watt_hour=0,
            cumulative_electrical_energy_space_heating_in_watt_hour=0,
            cumulative_electrical_energy_dhw_in_watt_hour=0,
            counter_switch_space_heating=0,
            counter_switch_dhw=0,
            counter_onoff=0,
            delta_t_secondary_side=5,
            delta_t_primary_side=0,
        )
        stsv, fakes = AllInputsRig.build(heat_pump)
        return heat_pump, stsv, fakes

    @classmethod
    def charge(
        cls,
        seconds_per_timestep: int,
        ratio: float,
        state_dhw: float = 2.0,
        minimum_running_time_in_seconds: Optional[int] = None,
    ) -> Dict[str, float]:
        """Return every output of one hot-water step from a 55 °C tank at 2 °C outside, set temperature 60 °C.

        Args:
            seconds_per_timestep: The step length, s.
            ratio: The part-load ratio the hot-water controller commands.
            state_dhw: The hot-water controller's state, 2 for a charge.
            minimum_running_time_in_seconds: The heat pump's minimum running time, s, or None for no cycling.

        Returns:
            The heat pump's outputs by field name.
        """
        heat_pump, stsv, fakes = cls.build(seconds_per_timestep, minimum_running_time_in_seconds)
        pump = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib
        return AllInputsRig.step(
            heat_pump,
            stsv,
            fakes,
            {
                pump.OnOffSwitchDHW: state_dhw,
                pump.TemperatureInputPrimary: 2.0,
                pump.TemperatureAmbient: 2.0,
                pump.TemperatureInputSecondarySH: 35.0,
                pump.TemperatureInputSecondaryDHW: 55.0,
                pump.SupplyTemperatureSetForDHWInCelsius: 60.0,
                pump.PartLoadRatioDHW: ratio,
            },
        )


class HeatPumpControllerDhwRig:
    """The heat pump's hot-water controller on fake inputs: the tank's start and end temperature and the raise."""

    @staticmethod
    def build(seconds_per_timestep: int, **extra: Any) -> Tuple[Any, Any, Dict[str, Any]]:
        """Return the controller with the standard 40/60 °C band, its step values and its fakes by field name.

        Args:
            seconds_per_timestep: The step length, s.
            **extra: Further simulation-parameter arguments.

        Returns:
            The controller, its step values and its fakes.
        """
        controller_class = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerDHW
        config = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerDHWConfig.preset_standard(
            "HeatPumpControllerDHW"
        )
        controller = controller_class(config=config, my_simulation_parameters=Parameters.one_day(seconds_per_timestep, **extra))
        stsv, fakes = AllInputsRig.build(controller)
        return controller, stsv, fakes

    @staticmethod
    def values(*, start_c: float, end_c: float, raise_k: float = 0.0, ratio_run: float = 1.0) -> Dict[str, float]:
        """Return the controller's inputs by field name for a tank start and end temperature, a raise and a ratio run.

        Args:
            start_c: The tank's start-of-step temperature, °C.
            end_c: The tank's end-of-step temperature, °C.
            raise_k: The energy manager's raise of the set temperature, K.
            ratio_run: The part-load ratio the heat pump reports it ran with.

        Returns:
            The input values by field name.
        """
        controller_class = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerDHW
        return {
            controller_class.WaterTemperatureInputFromDHWStorage: start_c,
            controller_class.WaterTemperatureAtEndOfStepFromDHWStorageInCelsius: end_c,
            controller_class.DHWStorageTemperatureModifier: raise_k,
            controller_class.PartLoadRatioRunDHW: ratio_run,
        }


class ElectricHeaterRig:
    """A 6 kW electric heater charging the hot-water tank, one step on fake inputs."""

    @classmethod
    def charge(cls, seconds_per_timestep: int, ratio: float) -> Dict[str, float]:
        """Return every output of one hot-water step from a 50 °C tank mean, a 25 K lift and a 75 °C set temperature.

        Args:
            seconds_per_timestep: The step length, s.
            ratio: The part-load ratio the controller commands.

        Returns:
            The heater's outputs by field name.
        """
        config = generic_electric_heating.ElectricHeatingConfig.preset_resistive("ElectricHeating")
        config.with_domestic_hot_water_preparation = True
        config.maximum_electric_power_w = 6000.0
        heater_class = generic_electric_heating.ElectricHeating
        heater = heater_class(my_simulation_parameters=Parameters.one_day(seconds_per_timestep), config=config)
        stsv, fakes = AllInputsRig.build(heater)
        return AllInputsRig.step(
            heater,
            stsv,
            fakes,
            {
                heater_class.HeatingMode: HeatingMode.DOMESTIC_HOT_WATER.value,
                heater_class.DeltaTemperatureNeededForDHW: 25.0,
                heater_class.SupplyTemperatureSetForDHWInCelsius: 75.0,
                heater_class.WaterInputTemperatureDhw: 50.0,
                heater_class.PartLoadRatioDhw: ratio,
            },
        )


class ElectricHeatingControllerRig:
    """The electric heater's controller with hot water on fake inputs."""

    @staticmethod
    def build(seconds_per_timestep: int, **extra: Any) -> Tuple[Any, Any, Dict[str, Any]]:
        """Return the controller, its step values and its fakes by field name.

        Args:
            seconds_per_timestep: The step length, s.
            **extra: Further simulation-parameter arguments.

        Returns:
            The controller, its step values and its fakes.
        """
        config = generic_electric_heating.ElectricHeatingControllerConfig.preset_standard("ElectricHeatingController")
        config.set_heating_threshold_outside_temperature_in_celsius = 16.0
        config.specific_heating_load_of_building_in_watt_per_m2 = 40.0
        config.with_domestic_hot_water_preparation = True
        controller = generic_electric_heating.ElectricHeatingController(
            my_simulation_parameters=Parameters.one_day(seconds_per_timestep, **extra), config=config
        )
        stsv, fakes = AllInputsRig.build(controller)
        return controller, stsv, fakes

    @staticmethod
    def values(*, start_c: float, end_c: float, ratio_run: float = 1.0) -> Dict[str, float]:
        """Return the controller's inputs by field name on a cold day, for a tank start and end temperature and a ratio run.

        Args:
            start_c: The tank's start-of-step temperature, °C.
            end_c: The tank's end-of-step temperature, °C.
            ratio_run: The part-load ratio the heater reports it ran with.

        Returns:
            The input values by field name.
        """
        controller_class = generic_electric_heating.ElectricHeatingController
        return {
            controller_class.WaterTemperatureInputFromWarmWaterStorage: start_c,
            controller_class.WaterTemperatureAtEndOfStepFromWarmWaterStorageInCelsius: end_c,
            controller_class.DailyAverageOutsideTemperature: 0.0,
            controller_class.PartLoadRatioRunDhw: ratio_run,
        }
