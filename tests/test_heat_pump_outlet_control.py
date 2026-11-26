"""The hplib heat pump holds its outlet at the set point its controller gives it, modulating down to its minimum output.

In space heating the machine's secondary pump runs the nominal flow ``m`` and the compressor aims at ``P = max(m c
(T_set - T_ret), P_min)``, up to its capacity at the outlet that power gives; the backup rod adds only the lift beyond
the compressor's maximal outlet. The set point is the space-heating controller's target, wired by default in every
arrangement. The tests check the law at its edges on single steps, the capacity looked up per outlet, the wiring, the
sized fields (compressor control, minimum output, nominal flow), the start-up ramp and the refusals.
"""

import math
from typing import ClassVar, Dict, List, Tuple

import pytest

from hisim.components.more_advanced_heat_pump.controller_space_heating import (
    MoreAdvancedHeatPumpHPLibControllerSpaceHeating,
)
from hisim.components.more_advanced_heat_pump.heat_pump import (
    CompressorControl,
    MoreAdvancedHeatPumpHPLib,
    MoreAdvancedHeatPumpHPLibConfig,
    PositionHotWaterStorageInSystemSetup,
)
from hisim.components.more_advanced_heat_pump.hplib_operation import HeatPumpStepConditions
from hisim.component import ComponentConnection
from hisim.config import SizingContext
from hisim.simulationparameters import SimulationParameters
from hisim.subcomponents.pumps import ConstantFlowPump
from tests.test_heat_pump_flow_booking import SingleStep

pytestmark = pytest.mark.base


class OutletControlCase:
    """Steps of the 10 kW air/water heat pump of :class:`SingleStep` that has heated for long enough to be started up.

    Its nominal flow is 0.333 kg/s, which carries 1391.94 W per kelvin, and its minimum output 1500 W.
    """

    #: The heat the nominal flow carries per kelvin, in W/K.
    HEAT_PER_KELVIN_IN_WATT_PER_KELVIN: ClassVar[float] = 0.333 * 4180.0
    #: The minimum output of :class:`SingleStep`'s machine, in W.
    MINIMUM_OUTPUT_IN_WATT: ClassVar[float] = 1500.0
    #: How long the machine has heated before the step, in s: long enough that the start-up ramp is spent.
    STARTED_UP_IN_SECONDS: ClassVar[int] = 10**6

    @staticmethod
    def config() -> MoreAdvancedHeatPumpHPLibConfig:
        """Return the configuration of :class:`SingleStep`'s machine, for a test to state other fields on."""
        config = SingleStep.heat_pump(PositionHotWaterStorageInSystemSetup.PARALLEL).config
        assert isinstance(config, MoreAdvancedHeatPumpHPLibConfig)
        return config

    @classmethod
    def heat_pump(
        cls, *, compressor_control: CompressorControl = CompressorControl.INVERTER, seconds_per_timestep: int = 60
    ) -> MoreAdvancedHeatPumpHPLib:
        """Return the machine with a parallel buffer, started up, as an inverter or an on/off machine.

        An on/off machine's minimum output is its rated 10 kW, as its sizing law gives it.

        Args:
            compressor_control: How the compressor follows a load below its capacity.
            seconds_per_timestep: The step length, in s.

        Returns:
            The heat pump.
        """
        heat_pump = SingleStep.heat_pump(PositionHotWaterStorageInSystemSetup.PARALLEL, seconds_per_timestep)
        if compressor_control == CompressorControl.ON_OFF:
            config = cls.config()
            config.compressor_control = CompressorControl.ON_OFF
            config.minimum_thermal_output_power_in_watt = 10000.0
            state = heat_pump.state
            heat_pump = MoreAdvancedHeatPumpHPLib(
                config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, seconds_per_timestep)
            )
            heat_pump.state = state
        heat_pump.state.time_on_heating = cls.STARTED_UP_IN_SECONDS
        return heat_pump

    @classmethod
    def step(
        cls,
        heat_pump: MoreAdvancedHeatPumpHPLib,
        *,
        source_in_celsius: float,
        return_in_celsius: float,
        set_point_in_celsius: float,
        part_load_ratio: float = 1.0,
    ) -> Dict[str, float]:
        """Return every output of one space-heating step.

        Args:
            heat_pump: The machine.
            source_in_celsius: The outdoor (source and ambient) temperature, in °C.
            return_in_celsius: The buffer's return, in °C.
            set_point_in_celsius: The outlet the controller asks the machine to hold, in °C.
            part_load_ratio: The requested space-heating ratio.

        Returns:
            The outputs by field name.
        """
        return SingleStep.step(
            heat_pump,
            {
                MoreAdvancedHeatPumpHPLib.OnOffSwitchSH: 1,
                MoreAdvancedHeatPumpHPLib.TemperatureInputPrimary: source_in_celsius,
                MoreAdvancedHeatPumpHPLib.TemperatureAmbient: source_in_celsius,
                MoreAdvancedHeatPumpHPLib.TemperatureInputSecondarySH: return_in_celsius,
                MoreAdvancedHeatPumpHPLib.SetHeatingTemperatureSH: set_point_in_celsius,
                MoreAdvancedHeatPumpHPLib.TemperatureInputSecondaryDHW: 45.0,
                MoreAdvancedHeatPumpHPLib.RequestedPartLoadRatioSh: part_load_ratio,
            },
        )

    @staticmethod
    def conditions(*, source_in_celsius: float, return_in_celsius: float) -> HeatPumpStepConditions:
        """Return a step's temperatures with the given source and space-heating return, for a direct lookup.

        Args:
            source_in_celsius: The outdoor (source and ambient) temperature, in °C.
            return_in_celsius: The space-heating return, in °C.

        Returns:
            The step's temperatures.
        """
        return HeatPumpStepConditions(
            source_temperature_in_celsius=source_in_celsius,
            ambient_temperature_in_celsius=source_in_celsius,
            space_heating_return_temperature_in_celsius=return_in_celsius,
            hot_water_return_temperature_in_celsius=45.0,
            space_heating_set_temperature_in_celsius=return_in_celsius,
            hot_water_supply_set_temperature_in_celsius=None,
        )


class TestTheOutletControlLaw:
    """One space-heating step: ``P = clamp(m c (T_set - T_ret), P_min, P_cap)``, the outlet ``T_ret + P / (m c)``."""

    def test_inside_the_modulation_range_the_outlet_is_the_set_point(self) -> None:
        """A 31 °C return held at 33 °C: 2783.9 W, the outlet exactly 33 °C, the electricity at the COP of 33 °C.

        Catches a law that still holds hplib's 5 K lift, or that draws the electricity at another outlet's COP.
        """
        heat_pump = OutletControlCase.heat_pump()
        outputs = OutletControlCase.step(heat_pump, source_in_celsius=2.0, return_in_celsius=31.0, set_point_in_celsius=33.0)
        thermal_power_in_watt = outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH]
        assert outputs[MoreAdvancedHeatPumpHPLib.TemperatureOutputSH] == pytest.approx(33.0, abs=1e-9)
        assert outputs[MoreAdvancedHeatPumpHPLib.MassFlowOutputSH] == 0.333
        assert thermal_power_in_watt == pytest.approx(OutletControlCase.HEAT_PER_KELVIN_IN_WATT_PER_KELVIN * 2.0, rel=1e-9)
        assert outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerSH] == pytest.approx(
            thermal_power_in_watt
            / heat_pump.compressor_cop_at_outlet(source_temperature_in_celsius=2.0, outlet_temperature_in_celsius=33.0),
            rel=1e-6,
        )
        assert outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerHeatingRod] == 0.0

    @pytest.mark.parametrize("return_in_celsius", [33.0, 35.0])
    def test_a_return_at_or_above_the_set_point_runs_the_minimum_output_and_overheats(self, return_in_celsius: float) -> None:
        """A return at or above the 33 °C set point asks for nothing; the running machine holds its 1500 W minimum.

        Catches a machine that runs at 0 W while its controller keeps it on, which would never end the run: the outlet
        must rise above the set point so that the buffer reaches the controller's switch-off point.
        """
        outputs = OutletControlCase.step(
            OutletControlCase.heat_pump(), source_in_celsius=2.0, return_in_celsius=return_in_celsius, set_point_in_celsius=33.0
        )
        assert outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH] == pytest.approx(1500.0, rel=1e-9)
        assert outputs[MoreAdvancedHeatPumpHPLib.TemperatureOutputSH] == pytest.approx(
            return_in_celsius + 1500.0 / OutletControlCase.HEAT_PER_KELVIN_IN_WATT_PER_KELVIN, rel=1e-12
        )

    def test_a_return_far_below_the_set_point_runs_the_capacity_without_the_rod(self) -> None:
        """At -7 °C a 20 °C return asked for 45 °C gets the compressor's capacity, an outlet below 45 °C and no rod.

        Catches a rod that fills a cold return's request above the capacity (it runs only above the compressor's
        maximal outlet, 56.7 °C here) and a capacity taken anywhere but at the outlet the step asks for.
        """
        heat_pump = OutletControlCase.heat_pump()
        outputs = OutletControlCase.step(heat_pump, source_in_celsius=-7.0, return_in_celsius=20.0, set_point_in_celsius=45.0)
        capacity_in_watt = heat_pump.capacity_at_outlet(
            OutletControlCase.conditions(source_in_celsius=-7.0, return_in_celsius=20.0), outlet_temperature_in_celsius=45.0
        ).thermal_power_in_watt
        assert outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH] == pytest.approx(capacity_in_watt, rel=1e-9)
        assert outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH] < OutletControlCase.HEAT_PER_KELVIN_IN_WATT_PER_KELVIN * 25.0
        assert 20.0 < outputs[MoreAdvancedHeatPumpHPLib.TemperatureOutputSH] < 45.0
        assert outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerHeatingRod] == 0.0

    @pytest.mark.parametrize("return_in_celsius", [31.0, 33.0])
    def test_an_on_off_machine_runs_its_full_output_whenever_it_runs(self, return_in_celsius: float) -> None:
        """An on/off machine's minimum output is its rated 10 kW: a 2 K or a 0 K request both get 10 kW.

        Catches an on/off machine that modulates like an inverter.
        """
        outputs = OutletControlCase.step(
            OutletControlCase.heat_pump(compressor_control=CompressorControl.ON_OFF),
            source_in_celsius=2.0,
            return_in_celsius=return_in_celsius,
            set_point_in_celsius=33.0,
        )
        assert outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH] == pytest.approx(10000.0, rel=1e-9)

    def test_above_600_s_the_ratio_runs_its_share_of_the_law_s_power_at_the_law_s_outlet(self) -> None:
        """At 900 s and a ratio of 0.5 the step books half of 2783.9 W, at half the flow and the 33 °C outlet.

        Catches a part-loaded step that runs at full load instead of at the power its outlet control sets.
        """
        outputs = OutletControlCase.step(
            OutletControlCase.heat_pump(seconds_per_timestep=900),
            source_in_celsius=2.0,
            return_in_celsius=31.0,
            set_point_in_celsius=33.0,
            part_load_ratio=0.5,
        )
        assert outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH] == pytest.approx(
            0.5 * OutletControlCase.HEAT_PER_KELVIN_IN_WATT_PER_KELVIN * 2.0, rel=1e-9
        )
        assert outputs[MoreAdvancedHeatPumpHPLib.MassFlowOutputSH] == pytest.approx(0.5 * 0.333)
        assert outputs[MoreAdvancedHeatPumpHPLib.TemperatureOutputSH] == pytest.approx(33.0, abs=1e-9)


class TestTheCapacityAtTheOutlet:
    """:meth:`MoreAdvancedHeatPumpHPLib.capacity_at_outlet`: hplib's capacity cached per outlet, not per return."""

    def test_two_lifts_at_one_return_get_two_capacities(self) -> None:
        """A 30 °C return asked for 35 °C and for 40 °C: two outlets, two capacities and two COPs.

        Catches a cache keyed on the return alone, which would answer the second lift with the first lift's capacity.
        """
        heat_pump = OutletControlCase.heat_pump()
        conditions = OutletControlCase.conditions(source_in_celsius=2.0, return_in_celsius=30.0)
        at_35 = heat_pump.capacity_at_outlet(conditions, outlet_temperature_in_celsius=35.0)
        at_40 = heat_pump.capacity_at_outlet(conditions, outlet_temperature_in_celsius=40.0)
        assert at_35.thermal_power_in_watt != pytest.approx(at_40.thermal_power_in_watt, rel=1e-3)
        assert at_35.cop > at_40.cop

    def test_the_capacity_at_an_outlet_is_hplib_s_answer_five_kelvin_below_it(self) -> None:
        """The capacity at a 40 °C outlet is hplib's answer at a 35 °C return and its 5 K lift, whatever the return."""
        heat_pump = OutletControlCase.heat_pump()
        expected = heat_pump.get_cached_results_or_run_hplib_simulation(
            source_temperature_in_celsius=2.0,
            return_temperature_in_celsius=35.0,
            ambient_temperature_in_celsius=2.0,
            mode=1,
            operation_mode="heating_building",
            minimal_thermal_power_in_watt=1500.0,
        )
        for return_in_celsius in (25.0, 38.0):
            at_40 = heat_pump.capacity_at_outlet(
                OutletControlCase.conditions(source_in_celsius=2.0, return_in_celsius=return_in_celsius),
                outlet_temperature_in_celsius=40.0,
            )
            assert at_40 == expected
            assert at_40.outlet_temperature_in_celsius == pytest.approx(40.0)


class TestTheSetPointWiring:
    """The set point ``SetHeatingTemperatureSH`` in every arrangement, from the space-heating controller's target."""

    @pytest.mark.parametrize("position", list(PositionHotWaterStorageInSystemSetup))
    def test_every_arrangement_reads_the_controller_s_target_as_its_set_point(
        self, position: PositionHotWaterStorageInSystemSetup
    ) -> None:
        """Catches an arrangement without the set point, or one wired to anything but the controller's target."""
        heat_pump = SingleStep.heat_pump(position)
        controller = MoreAdvancedHeatPumpHPLibControllerSpaceHeating.get_classname()
        assert (
            ComponentConnection(
                MoreAdvancedHeatPumpHPLib.SetHeatingTemperatureSH,
                controller,
                MoreAdvancedHeatPumpHPLibControllerSpaceHeating.TargetTemperatureShInCelsius,
            )
            in heat_pump.default_connections[controller]
        )
        set_point = heat_pump.set_temperature_hp_space_heating
        assert set_point.field_name == MoreAdvancedHeatPumpHPLib.SetHeatingTemperatureSH
        assert set_point.is_mandatory


class TestTheSizedFields:
    """The compressor control, the minimum output and the nominal flow, sized from the machine's own fields."""

    #: hplib groups and the compressor control each has by default.
    GROUPS: ClassVar[List[Tuple[int, CompressorControl]]] = [
        (1, CompressorControl.INVERTER),
        (2, CompressorControl.ON_OFF),
        (3, CompressorControl.ON_OFF),
        (4, CompressorControl.ON_OFF),
        (5, CompressorControl.ON_OFF),
        (6, CompressorControl.ON_OFF),
    ]

    @pytest.mark.parametrize(("group_id", "compressor_control"), GROUPS)
    def test_an_air_water_machine_is_an_inverter_and_every_other_group_on_off(
        self, group_id: int, compressor_control: CompressorControl
    ) -> None:
        """Catches a brine/water machine sized as an inverter, or an air/water machine as on/off."""
        assert MoreAdvancedHeatPumpHPLibConfig.compressor_control_of_group(group_id) == compressor_control

    @pytest.mark.parametrize("group_id", [0, 7])
    def test_a_group_hplib_does_not_know_is_refused(self, group_id: int) -> None:
        """Catches a group outside hplib's 1 to 6 that would silently be sized as on/off."""
        with pytest.raises(ValueError, match="group is 1 to 6"):
            MoreAdvancedHeatPumpHPLibConfig.compressor_control_of_group(group_id)

    @pytest.mark.parametrize(
        ("share_of_rated_power", "minimum_output_in_watt"), [(0.25, 1945.1875), (1.0, 7780.75), (1e-9, 7.78075e-06)]
    )
    def test_the_minimum_output_is_its_share_of_the_rated_power(
        self, share_of_rated_power: float, minimum_output_in_watt: float
    ) -> None:
        """0.25 of 7780.75 W is 1945.19 W, the whole of it 7780.75 W."""
        assert MoreAdvancedHeatPumpHPLibConfig.minimum_thermal_output_power_in_watt_of(
            rated_thermal_power_in_watt=7780.75, share_of_rated_power=share_of_rated_power
        ) == pytest.approx(minimum_output_in_watt, rel=1e-12)

    @pytest.mark.parametrize("share_of_rated_power", [0.0, -0.25, 1.01, math.nan])
    def test_a_share_outside_zero_to_one_is_refused(self, share_of_rated_power: float) -> None:
        """Catches a minimum output of nothing, of more than the machine, or of NaN."""
        with pytest.raises(ValueError, match=r"share of the rated power lies in \(0, 1\]"):
            MoreAdvancedHeatPumpHPLibConfig.minimum_thermal_output_power_in_watt_of(
                rated_thermal_power_in_watt=7780.75, share_of_rated_power=share_of_rated_power
            )

    @pytest.mark.parametrize("rated_thermal_power_in_watt", [0.0, -7780.75, math.nan, math.inf])
    def test_a_rated_power_that_is_not_finite_and_positive_is_refused(self, rated_thermal_power_in_watt: float) -> None:
        """Catches a minimum output or a nominal flow sized from an impossible rated power."""
        with pytest.raises(ValueError, match="finite power above 0 W"):
            MoreAdvancedHeatPumpHPLibConfig.minimum_thermal_output_power_in_watt_of(
                rated_thermal_power_in_watt=rated_thermal_power_in_watt, share_of_rated_power=0.25
            )
        with pytest.raises(ValueError, match="finite power above 0 W"):
            MoreAdvancedHeatPumpHPLibConfig.nominal_mass_flow_in_kg_per_second_of(rated_thermal_power_in_watt)

    def test_the_nominal_flow_carries_the_rated_power_over_five_kelvin(self) -> None:
        """7780.75 W over 4180 J/(kg K) times 5 K is 0.37228 kg/s."""
        assert MoreAdvancedHeatPumpHPLibConfig.nominal_mass_flow_in_kg_per_second_of(7780.75) == pytest.approx(
            7780.75 / (4180.0 * 5.0), rel=1e-12
        )

    @staticmethod
    def resolved(**stated: object) -> MoreAdvancedHeatPumpHPLibConfig:
        """Return the air/water preset with the given fields stated, resolved for a 7780.75 W building at -7 °C."""
        config = MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump")
        for name, value in stated.items():
            setattr(config, name, value)
        return config.resolve(
            SizingContext(heating_load_in_watt=7780.75, heating_reference_temperature_in_celsius=-7.0)
        )

    def test_the_preset_sizes_an_inverter_at_a_quarter_of_its_power_and_its_flow_from_it(self) -> None:
        """Catches a preset that keeps the old 1800 W and 0.333 kg/s instead of sizing both from the machine."""
        config = self.resolved()
        assert config.compressor_control == CompressorControl.INVERTER
        assert config.minimum_thermal_output_power_in_watt == pytest.approx(1945.1875, rel=1e-12)
        assert config.massflow_nominal_secondary_side_in_kg_per_s == pytest.approx(0.3722847, rel=1e-6)

    def test_a_brine_water_machine_is_sized_on_off_at_its_whole_power(self) -> None:
        """A brine/water machine (group 2) has no modulation range: its minimum output is its rated 7780.75 W."""
        config = self.resolved(group_id=2)
        assert config.compressor_control == CompressorControl.ON_OFF
        assert config.minimum_thermal_output_power_in_watt == pytest.approx(7780.75, rel=1e-12)

    def test_stated_values_win(self) -> None:
        """A stated minimum output, flow and control are kept; an on/off control states the minimum output's law."""
        config = self.resolved(minimum_thermal_output_power_in_watt=2500.0, massflow_nominal_secondary_side_in_kg_per_s=0.5)
        assert config.minimum_thermal_output_power_in_watt == 2500.0
        assert config.massflow_nominal_secondary_side_in_kg_per_s == 0.5
        on_off = self.resolved(compressor_control=CompressorControl.ON_OFF)
        assert on_off.minimum_thermal_output_power_in_watt == pytest.approx(7780.75, rel=1e-12)

    @pytest.mark.parametrize(
        ("compressor_control", "hplib_minimal_thermal_power_in_watt"),
        [(CompressorControl.INVERTER, 1500.0), (CompressorControl.ON_OFF, 0.0)],
    )
    def test_only_an_inverter_lets_hplib_raise_its_capacity_to_the_minimum_output(
        self, compressor_control: CompressorControl, hplib_minimal_thermal_power_in_watt: float
    ) -> None:
        """Catches an on/off machine whose capacity hplib would raise to its whole rated power by speeding it up."""
        config = OutletControlCase.config()
        config.compressor_control = compressor_control
        heat_pump = MoreAdvancedHeatPumpHPLib(config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, 60))
        assert heat_pump.hplib_minimal_thermal_power_in_watt == hplib_minimal_thermal_power_in_watt

    @pytest.mark.parametrize("minimum_output_in_watt", [-1.0, math.nan, math.inf])
    def test_a_minimum_output_that_is_negative_or_not_finite_is_refused_when_the_heat_pump_is_built(
        self, minimum_output_in_watt: float
    ) -> None:
        """Catches a stated minimum output that would run the compressor at a negative or undefined power."""
        config = OutletControlCase.config()
        config.minimum_thermal_output_power_in_watt = minimum_output_in_watt
        with pytest.raises(ValueError, match="minimum_thermal_output_power_in_watt must be a finite power"):
            MoreAdvancedHeatPumpHPLib(config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, 60))


class TestTheStartUpRamp:
    """:meth:`MoreAdvancedHeatPumpHPLib.nominal_flow_operating_point`: the step's mean of ``1 - e^(-t / 120 s)``."""

    #: The power the operating point aims at, 0.333 kg/s over 5 K, in W.
    AIMED_POWER_IN_WATT: ClassVar[float] = 0.333 * 4180.0 * 5.0

    @classmethod
    def delivered_power_in_watt(cls, *, time_on_heating_in_seconds: float, seconds_per_timestep: float) -> float:
        """Return the power one step delivers after the machine heated for a time, in W.

        Args:
            time_on_heating_in_seconds: How long the machine heated before the step, in s.
            seconds_per_timestep: The step length, in s.

        Returns:
            The step's mean power, in W.
        """
        point = MoreAdvancedHeatPumpHPLib.nominal_flow_operating_point(
            pump=ConstantFlowPump(design_mass_flow_in_kg_per_second=0.333),
            return_temperature_in_celsius=30.0,
            lift_in_kelvin=5.0,
            minimal_thermal_power_in_watt=1500.0,
            time_on_heating_in_seconds=time_on_heating_in_seconds,
            seconds_per_timestep=seconds_per_timestep,
        )
        return (point.supply_temperature_in_celsius - 30.0) * 0.333 * 4180.0

    @pytest.mark.parametrize("running_time_in_seconds", [0, 60, 120, 600])
    def test_60_s_steps_deliver_the_integral_of_the_ramp(self, running_time_in_seconds: int) -> None:
        """After 0, 60, 120 and 600 s of 60 s steps the heat is ``P (t - tau (1 - e^(-t / tau)))`` with tau 120 s.

        Catches a ramp read at the start or the end of the step instead of over it: its heat would depend on the step
        length. After 60 s that is 21.3 % of a minute at full power, after 600 s 80.1 % of ten minutes.
        """
        heat_in_joule = sum(
            self.delivered_power_in_watt(time_on_heating_in_seconds=started, seconds_per_timestep=60) * 60
            for started in range(0, running_time_in_seconds, 60)
        )
        expected_in_joule = self.AIMED_POWER_IN_WATT * (
            running_time_in_seconds - 120.0 * (1.0 - math.exp(-running_time_in_seconds / 120.0))
        )
        assert heat_in_joule == pytest.approx(expected_in_joule, rel=1e-9, abs=1e-9)

    def test_fifteen_60_s_steps_deliver_the_heat_of_one_900_s_step(self) -> None:
        """Catches a start-up whose heat differs between a 60 s and a 900 s run of the same first quarter hour."""
        fine_in_joule = sum(
            self.delivered_power_in_watt(time_on_heating_in_seconds=started, seconds_per_timestep=60) * 60
            for started in range(0, 900, 60)
        )
        coarse_in_joule = self.delivered_power_in_watt(time_on_heating_in_seconds=0, seconds_per_timestep=900) * 900
        assert fine_in_joule == pytest.approx(coarse_in_joule, rel=1e-12)
        assert coarse_in_joule / (900 * self.AIMED_POWER_IN_WATT) == pytest.approx(1.0 - (120.0 / 900.0) * (1.0 - math.exp(-7.5)))

    def test_a_900_s_step_after_the_first_books_the_full_power(self) -> None:
        """At 900 s the ramp is spent within the first step: the second books 99.99 % of the aimed power."""
        assert self.delivered_power_in_watt(time_on_heating_in_seconds=900, seconds_per_timestep=900) == pytest.approx(
            self.AIMED_POWER_IN_WATT, rel=1e-4
        )
