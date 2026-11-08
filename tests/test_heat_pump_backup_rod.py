"""The heat pump's backup heating rod: it runs only where the compressor falls short and adds only the missing heat.

Decision 8 of ``roadmap/heat_pump_model_rework_spec.md``: the rod sits inside the heat pump, its rated power is the
sized field ``heating_rod_rated_electrical_power_in_watt`` (9 kW, at most the machine's rated thermal power), and it
heats at a COP of 1 where the requested heat exceeds the compressor's capacity or the required outlet exceeds the
compressor's maximal outlet, hplib's COP-1 line until bead hisim-heat-pump-outlet-limit-2vq7. Its electricity is the
output ``ElectricalInputPowerHeatingRod`` with its own energy port and part of the heat pump's total. The tests check the
pure share between compressor and rod (:meth:`BackupHeatingRod.heating`), the compressor's maximal outlet, the heat pump
in one step at 60 s and at 900 s, the sizing law, the KPI split and that the SCOP rating knows no rod.
"""

import math
from typing import ClassVar, Dict, List, Tuple

import pandas as pd
import pytest

from hisim.components.more_advanced_heat_pump.heat_pump import (
    MoreAdvancedHeatPumpHPLib,
    MoreAdvancedHeatPumpHPLibConfig,
    PositionHotWaterStorageInSystemSetup,
)
from hisim.components.more_advanced_heat_pump.backup_rod import (
    BackupHeatingRod,
    RodAssistedHeating,
)
from hisim.components.more_advanced_heat_pump.seasonal_cop import (
    ScopApplication,
    StandardizedSeasonalCop,
)
from hisim.config import SizingContext
from hisim.loadtypes import EnergyBalanceCarrier, EnergyRole
from hisim.simulationparameters import SimulationParameters
from tests.test_heat_pump_flow_booking import SingleStep

pytestmark = pytest.mark.base


class RodCase:
    """A heating circuit of 0.4 kg/s from a 60 °C return to a 65 °C required outlet, at a compressor COP of 2.5.

    0.4 kg/s carries 1672 W per kelvin, so the full-load request is 8360 W.
    """

    #: The circuit's flow at full load, in kg/s.
    MASS_FLOW_IN_KG_PER_SECOND: ClassVar[float] = 0.4
    #: The circuit's return temperature, in °C.
    RETURN_IN_CELSIUS: ClassVar[float] = 60.0
    #: The outlet the step asks for, in °C.
    REQUIRED_OUTLET_IN_CELSIUS: ClassVar[float] = 65.0
    #: The heat the flow carries per kelvin, in W/K.
    HEAT_PER_KELVIN_IN_WATT_PER_KELVIN: ClassVar[float] = 0.4 * 4180.0

    @classmethod
    def heating(
        cls,
        *,
        rated_electrical_power_in_watt: float = 9000.0,
        part_load_ratio: float = 1.0,
        capacity_in_watt: float = 10000.0,
        maximal_outlet_in_celsius: float = 75.0,
    ) -> RodAssistedHeating:
        """Return the circuit's step shared between a compressor and a rod.

        Args:
            rated_electrical_power_in_watt: The rod's rated power, in W.
            part_load_ratio: The fraction of the step the request asks for at full load.
            capacity_in_watt: The compressor's thermal capacity, in W.
            maximal_outlet_in_celsius: The compressor's maximal outlet, in °C.

        Returns:
            The step.
        """
        return BackupHeatingRod(rated_electrical_power_in_watt=rated_electrical_power_in_watt).heating(
            part_load_ratio=part_load_ratio,
            mass_flow_in_kg_per_second=cls.MASS_FLOW_IN_KG_PER_SECOND,
            return_temperature_in_celsius=cls.RETURN_IN_CELSIUS,
            required_outlet_temperature_in_celsius=cls.REQUIRED_OUTLET_IN_CELSIUS,
            compressor_thermal_capacity_in_watt=capacity_in_watt,
            compressor_maximal_outlet_temperature_in_celsius=maximal_outlet_in_celsius,
            compressor_cop=2.5,
        )


class TestTheShareBetweenCompressorAndRod:
    """:meth:`BackupHeatingRod.heating`, the pure share of one step's request."""

    def test_where_the_compressor_copes_the_rod_does_not_run(self) -> None:
        """Catches a rod that runs without a shortfall: 8360 W asked of a 10 kW compressor that reaches 75 °C."""
        heating = RodCase.heating()
        assert heating.heating_rod_electrical_power_in_watt == 0.0
        assert heating.compressor_thermal_power_in_watt == pytest.approx(8360.0)
        assert heating.compressor_electrical_power_in_watt == pytest.approx(8360.0 / 2.5)
        assert heating.outlet_temperature_in_celsius == 65.0
        assert heating.compressor_running_share == pytest.approx(1.0)

    def test_a_request_above_the_capacity_gets_only_the_missing_heat_from_the_rod(self) -> None:
        """Case 1: 8360 W asked of a 6 kW compressor, so the rod adds 2360 W at a COP of 1, not more."""
        heating = RodCase.heating(capacity_in_watt=6000.0)
        assert heating.compressor_thermal_power_in_watt == pytest.approx(6000.0)
        assert heating.heating_rod_electrical_power_in_watt == pytest.approx(2360.0)
        assert heating.electrical_power_in_watt == pytest.approx(6000.0 / 2.5 + 2360.0)
        assert heating.outlet_temperature_in_celsius == 65.0

    def test_an_outlet_above_the_compressors_maximum_gets_the_lift_beyond_it_from_the_rod(self) -> None:
        """Case 2: the compressor stops at 62 °C, the rod lifts on to 65 °C, ``m c (T_required - T_max)`` = 5016 W."""
        heating = RodCase.heating(maximal_outlet_in_celsius=62.0)
        assert heating.compressor_thermal_power_in_watt == pytest.approx(RodCase.HEAT_PER_KELVIN_IN_WATT_PER_KELVIN * 2.0)
        assert heating.heating_rod_electrical_power_in_watt == pytest.approx(
            RodCase.HEAT_PER_KELVIN_IN_WATT_PER_KELVIN * 3.0
        )
        assert heating.outlet_temperature_in_celsius == 65.0
        assert heating.cop == pytest.approx(8360.0 / (3344.0 / 2.5 + 5016.0))

    def test_a_return_above_the_compressors_maximum_leaves_the_whole_lift_to_the_rod(self) -> None:
        """The compressor cannot heat water already above its maximal outlet: it stands, the rod lifts all 5 K."""
        heating = RodCase.heating(maximal_outlet_in_celsius=58.0)
        assert heating.compressor_thermal_power_in_watt == 0.0
        assert heating.compressor_electrical_power_in_watt == 0.0
        assert heating.compressor_running_share == 0.0
        assert heating.heating_rod_electrical_power_in_watt == pytest.approx(8360.0)
        assert heating.cop == pytest.approx(1.0)

    def test_what_the_rod_cannot_cover_is_not_delivered(self) -> None:
        """A 2 kW rod beside a 6 kW compressor delivers 8000 W of the 8360 W, at a lower outlet of 64.78 °C."""
        heating = RodCase.heating(rated_electrical_power_in_watt=2000.0, capacity_in_watt=6000.0)
        assert heating.heating_rod_electrical_power_in_watt == 2000.0
        assert heating.thermal_power_in_watt == pytest.approx(8000.0)
        assert heating.mass_flow_in_kg_per_second == RodCase.MASS_FLOW_IN_KG_PER_SECOND
        assert heating.outlet_temperature_in_celsius == pytest.approx(60.0 + 8000.0 / 1672.0)

    def test_a_heat_pump_without_a_rod_delivers_only_the_compressors_heat(self) -> None:
        """A rated power of 0: the 6 kW compressor's heat and nothing else."""
        heating = RodCase.heating(rated_electrical_power_in_watt=0.0, capacity_in_watt=6000.0)
        assert heating.heating_rod_electrical_power_in_watt == 0.0
        assert heating.thermal_power_in_watt == pytest.approx(6000.0)

    #: Per-step energy decision ``E_rod = min(Q_requested - Q_compressor,max, P_rod dt)`` in average powers: the part-load
    #: ratio, the compressor's maximal outlet in °C, and the rod's average power in W. The compressor stopping at 62 °C
    #: can deliver 3344 W over the whole step.
    PER_STEP_ROWS: ClassVar[List[Tuple[float, float, float]]] = [
        (0.4, 62.0, 0.0),
        (0.5, 62.0, 4180.0 - 3344.0),
        (1.0, 62.0, 8360.0 - 3344.0),
        (0.5, 75.0, 0.0),
    ]

    @pytest.mark.parametrize(("part_load_ratio", "maximal_outlet_in_celsius", "rod_in_watt"), PER_STEP_ROWS)
    def test_above_600_s_the_rod_covers_only_the_missing_share_of_the_step(
        self, part_load_ratio: float, maximal_outlet_in_celsius: float, rod_in_watt: float
    ) -> None:
        """Catches a rod that runs the whole requested share once the outlet crosses the line, as hplib's switch did.

        At a ratio of 0.4 the request (3344 W) is what the compressor can deliver at its reduced lift over the whole
        step, so the rod stays off; the published flow is the averaged flow at the required outlet.
        """
        heating = RodCase.heating(part_load_ratio=part_load_ratio, maximal_outlet_in_celsius=maximal_outlet_in_celsius)
        assert heating.heating_rod_electrical_power_in_watt == pytest.approx(rod_in_watt, abs=1e-9)
        assert heating.thermal_power_in_watt == pytest.approx(part_load_ratio * 8360.0)
        assert heating.mass_flow_in_kg_per_second == pytest.approx(part_load_ratio * 0.4)
        assert heating.outlet_temperature_in_celsius == 65.0

    def test_a_rod_rated_above_the_shortfall_adds_no_more_than_the_shortfall(self) -> None:
        """The rod is capped by the shortfall, not run at its rating: 9 kW rated, 2360 W missing, 2360 W drawn."""
        assert RodCase.heating(capacity_in_watt=6000.0).heating_rod_electrical_power_in_watt < 9000.0

    def test_a_step_at_ratio_zero_books_nothing(self) -> None:
        """No flow, heat or electricity, the outlet at the return and a COP of 0."""
        heating = RodCase.heating(part_load_ratio=0.0, maximal_outlet_in_celsius=62.0)
        assert heating.mass_flow_in_kg_per_second == 0.0
        assert heating.electrical_power_in_watt == 0.0
        assert heating.outlet_temperature_in_celsius == 60.0
        assert heating.cop == 0.0

    @pytest.mark.parametrize("rated_electrical_power_in_watt", [-1.0, math.inf, math.nan])
    def test_an_impossible_rated_power_is_refused(self, rated_electrical_power_in_watt: float) -> None:
        """A negative, infinite or undefined rating would book negative or infinite electricity."""
        with pytest.raises(ValueError, match="rated electrical power"):
            BackupHeatingRod(rated_electrical_power_in_watt=rated_electrical_power_in_watt)


class TestTheCompressorsMaximalOutlet:
    """:meth:`CarnotCopHeatPump.maximal_outlet_temperature_in_celsius`, hplib's COP-1 line as the interim limit."""

    #: The source temperature in °C and the 8 kW Generic air/water machine's maximal outlet there, in °C.
    AIR_ROWS: ClassVar[List[Tuple[float, float]]] = [(-7.0, 56.72), (0.0, 69.04), (7.0, 81.35)]

    @staticmethod
    def air_water_heat_pump() -> MoreAdvancedHeatPumpHPLib:
        """Build the 8 kW air/water heat pump of the rod tests, rated at -7 °C, at 60 s steps."""
        config = MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump").resolve(
            SizingContext(heating_load_in_watt=8000.0, heating_reference_temperature_in_celsius=-7.0)
        )
        return MoreAdvancedHeatPumpHPLib(
            my_simulation_parameters=SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60),
            config=config,
        )

    @pytest.mark.parametrize(("source_in_celsius", "maximal_outlet_in_celsius"), AIR_ROWS)
    def test_the_line_where_hplibs_cop_reaches_one(self, source_in_celsius: float, maximal_outlet_in_celsius: float) -> None:
        """Catches a limit off hplib's switch: hplib's own plane gives a COP of 1 exactly at the returned outlet."""
        machine = self.air_water_heat_pump().heatpump
        outlet = machine.maximal_outlet_temperature_in_celsius(source_in_celsius, source_in_celsius)
        assert outlet == pytest.approx(maximal_outlet_in_celsius, abs=0.01)
        hplib = machine.hplib_heat_pump
        cop = hplib.p1_cop * source_in_celsius + hplib.p2_cop * outlet + hplib.p3_cop + hplib.p4_cop * source_in_celsius
        assert cop == pytest.approx(1.0)

    def test_an_air_machine_reads_the_source_as_its_ambient(self) -> None:
        """Catches an air machine reading the ambient: hplib sets it to the source, so the argument changes nothing."""
        machine = self.air_water_heat_pump().heatpump
        assert machine.maximal_outlet_temperature_in_celsius(0.0, 20.0) == machine.maximal_outlet_temperature_in_celsius(
            0.0, 0.0
        )

    def test_a_plane_that_does_not_fall_with_the_outlet_has_no_limit(self) -> None:
        """A COP plane rising with the outlet never reaches 1 above the source: the limit is infinite."""
        machine = self.air_water_heat_pump().heatpump
        machine.hplib_heat_pump.p2_cop = 0.01
        assert machine.maximal_outlet_temperature_in_celsius(0.0, 0.0) == math.inf


class OneStep:
    """One step of the 10 kW air/water heat pump of :class:`tests.test_heat_pump_flow_booking.SingleStep`."""

    @staticmethod
    def space_heating(
        *, source_in_celsius: float, return_in_celsius: float, part_load_ratio: float = 1.0, seconds_per_timestep: int = 60
    ) -> Dict[str, float]:
        """Return every output of one space-heating step with a parallel buffer.

        Args:
            source_in_celsius: The outdoor (source and ambient) temperature, in °C.
            return_in_celsius: The buffer's return, in °C.
            part_load_ratio: The requested space-heating ratio.
            seconds_per_timestep: The step length, in s.

        Returns:
            The outputs by field name.
        """
        heat_pump = SingleStep.heat_pump(PositionHotWaterStorageInSystemSetup.PARALLEL, seconds_per_timestep)
        return SingleStep.step(
            heat_pump,
            {
                MoreAdvancedHeatPumpHPLib.TemperatureInputPrimary: source_in_celsius,
                MoreAdvancedHeatPumpHPLib.TemperatureAmbient: source_in_celsius,
                MoreAdvancedHeatPumpHPLib.TemperatureInputSecondarySH: return_in_celsius,
                MoreAdvancedHeatPumpHPLib.TemperatureInputSecondaryDHW: 45.0,
                MoreAdvancedHeatPumpHPLib.OnOffSwitchSH: 1,
                MoreAdvancedHeatPumpHPLib.RequestedPartLoadRatioSh: part_load_ratio,
            },
        )


class TestTheHeatPumpWithItsRod:
    """The rod inside :class:`MoreAdvancedHeatPumpHPLib`, one step at a time."""

    def test_a_mild_day_runs_no_rod(self) -> None:
        """10 °C outside and a 30 °C return: the compressor delivers 7.8 kW alone at a COP of 4.7."""
        outputs = OneStep.space_heating(source_in_celsius=10.0, return_in_celsius=30.0)
        assert outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerHeatingRod] == 0.0
        assert outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH] > 7000.0
        assert outputs[MoreAdvancedHeatPumpHPLib.COP] > 4.0

    def test_the_outlet_trigger_fires_at_the_interim_limit(self) -> None:
        """At 0 °C the compressor stops at 69.04 °C: a 66 °C return asking for 71 °C gets 1.96 K from the rod.

        A 63 °C return, whose 68 °C outlet the compressor reaches, gets none.
        """
        below = OneStep.space_heating(source_in_celsius=0.0, return_in_celsius=63.0)
        assert below[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerHeatingRod] == 0.0
        above = OneStep.space_heating(source_in_celsius=0.0, return_in_celsius=66.0)
        mass_flow = above[MoreAdvancedHeatPumpHPLib.MassFlowOutputSH]
        assert above[MoreAdvancedHeatPumpHPLib.TemperatureOutputSH] == pytest.approx(71.0)
        assert above[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerHeatingRod] == pytest.approx(
            mass_flow * 4180.0 * (71.0 - 69.038), rel=1e-4
        )
        # the compressor heats the rest at the Carnot COP at its maximal outlet, 0.451 * 342.19 / (69.04 + 4.45)
        compressor_heat_in_watt = (
            above[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH] - above[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerHeatingRod]
        )
        assert compressor_heat_in_watt / above[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerSH] == pytest.approx(
            0.451 * (69.038 + 273.15) / (69.038 + 4.45), rel=1e-4
        )

    def test_the_rods_electricity_is_its_own_and_in_the_total(self) -> None:
        """The total is the compressor's plus the rod's; the COP is the heat over both."""
        outputs = OneStep.space_heating(source_in_celsius=0.0, return_in_celsius=66.0)
        rod = outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerHeatingRod]
        compressor = outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerSH]
        assert rod > 0.0
        assert outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerTotal] == pytest.approx(compressor + rod)
        assert outputs[MoreAdvancedHeatPumpHPLib.COP] == pytest.approx(
            outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH] / (compressor + rod)
        )
        # the heat taken from the environment is the compressor's heat minus its electricity, without the rod
        assert outputs[MoreAdvancedHeatPumpHPLib.ThermalPowerFromEnvironment] == pytest.approx(
            outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH] - compressor - rod
        )

    def test_the_rods_output_carries_its_own_electricity_consumption_port(self) -> None:
        """Catches a rod booked into the space-heating port, which would hide it from the balance and the meter."""
        heat_pump = SingleStep.heat_pump(PositionHotWaterStorageInSystemSetup.PARALLEL)
        port = heat_pump.p_el_heating_rod.energy_port
        assert port is not None
        assert (port.role, port.carrier) == (EnergyRole.CONSUMPTION, EnergyBalanceCarrier.ELECTRICITY)

    def test_above_600_s_a_part_loaded_step_stretches_the_compressor_instead_of_running_the_rod(self) -> None:
        """At 900 s and a ratio of 0.5 the 66 °C return asks for half the heat, which the compressor delivers alone."""
        half = OneStep.space_heating(
            source_in_celsius=0.0, return_in_celsius=66.0, part_load_ratio=0.5, seconds_per_timestep=900
        )
        whole = OneStep.space_heating(
            source_in_celsius=0.0, return_in_celsius=66.0, part_load_ratio=1.0, seconds_per_timestep=900
        )
        assert half[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerHeatingRod] == 0.0
        assert half[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH] == pytest.approx(
            0.5 * whole[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH]
        )
        assert whole[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerHeatingRod] > 0.0


class TestTheRodsSize:
    """The sizing law of ``heating_rod_rated_electrical_power_in_watt``."""

    #: The building's heating load in W and the rod's rated power the law gives, in W.
    ROWS: ClassVar[List[Tuple[float, float]]] = [(16000.0, 9000.0), (9000.0, 9000.0), (6000.0, 6000.0)]

    @pytest.mark.parametrize(("heating_load_in_watt", "rated_electrical_power_in_watt"), ROWS)
    def test_nine_kilowatts_at_most_the_machines_thermal_power(
        self, heating_load_in_watt: float, rated_electrical_power_in_watt: float
    ) -> None:
        """Catches a rod larger than the machine it backs up, or a default other than the 9 kW element class."""
        config = MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump").resolve(
            SizingContext(heating_load_in_watt=heating_load_in_watt, heating_reference_temperature_in_celsius=-7.0)
        )
        assert config.heating_rod_rated_electrical_power_in_watt == rated_electrical_power_in_watt

    def test_a_stated_rod_is_kept(self) -> None:
        """A preset or request that states the rod keeps it: 3 kW stays 3 kW beside a 16 kW machine."""
        config = MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump")
        config.heating_rod_rated_electrical_power_in_watt = 3000.0
        resolved = config.resolve(
            SizingContext(heating_load_in_watt=16000.0, heating_reference_temperature_in_celsius=-7.0)
        )
        assert resolved.heating_rod_rated_electrical_power_in_watt == 3000.0

    def test_the_rod_is_no_part_of_the_seasonal_rating(self) -> None:
        """The EN 14825 rating and the SCOP calibration read the compressor only: the rod's size changes neither."""
        ratings = []
        for rated_electrical_power_in_watt in (0.0, 9000.0):
            config = MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump").resolve(
                SizingContext(heating_load_in_watt=8000.0, heating_reference_temperature_in_celsius=-7.0)
            )
            config.heating_rod_rated_electrical_power_in_watt = rated_electrical_power_in_watt
            config.standardized_scop_en14825_w55 = 3.0
            heat_pump = MoreAdvancedHeatPumpHPLib(
                my_simulation_parameters=SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60),
                config=config,
            )
            ratings.append(
                (
                    StandardizedSeasonalCop.of(heat_pump.heatpump, ScopApplication.W55, heat_pump.scop_calibration),
                    dict(heat_pump.scop_calibration.factors),
                )
            )
        assert ratings[0] == ratings[1]
        assert ratings[0][0] == pytest.approx(3.0, abs=1e-4)


class TestTheRodsKpiSplit:
    """:meth:`MoreAdvancedHeatPumpHPLib.heating_rod_energies_in_kilowatt_hour`."""

    def test_a_hot_water_step_books_to_hot_water_and_every_other_to_space_heating(self) -> None:
        """9000 W in a space-heating step and 3000 W in a hot-water step at 900 s: 2.25 and 0.75 kWh."""
        space_heating, hot_water = MoreAdvancedHeatPumpHPLib.heating_rod_energies_in_kilowatt_hour(
            heating_rod_power_in_watt=pd.Series([9000.0, 3000.0, 0.0]),
            on_off_state=pd.Series([1.0, 2.0, 0.0]),
            seconds_per_timestep=900,
        )
        assert (space_heating, hot_water) == pytest.approx((2.25, 0.75))

    def test_without_a_rod_column_both_are_zero(self) -> None:
        """An empty rod series, as from a run without the output, gives 0 kWh in either mode."""
        assert MoreAdvancedHeatPumpHPLib.heating_rod_energies_in_kilowatt_hour(
            heating_rod_power_in_watt=pd.Series([], dtype=float),
            on_off_state=pd.Series([], dtype=float),
            seconds_per_timestep=60,
        ) == (0.0, 0.0)


def test_the_cop_with_the_rod_is_heat_over_all_electricity() -> None:
    """3344 W from the compressor at a COP of 2.5 and 5016 W from the rod: 8360 W for 6353.6 W, a COP of 1.32."""
    heating = RodAssistedHeating(
        compressor_thermal_power_in_watt=3344.0,
        compressor_electrical_power_in_watt=3344.0 / 2.5,
        heating_rod_electrical_power_in_watt=5016.0,
        compressor_running_share=1.0,
        mass_flow_in_kg_per_second=0.4,
        outlet_temperature_in_celsius=65.0,
    )
    assert heating.thermal_power_in_watt == pytest.approx(8360.0)
    assert heating.electrical_power_in_watt == pytest.approx(6353.6)
    assert heating.cop == pytest.approx(8360.0 / 6353.6)
