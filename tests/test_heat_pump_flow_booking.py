"""The hplib heat pump books the heat its water carries: ``P_th = m c (T_out - T_in)`` and ``P_el = P_th / COP``.

hplib answers a return temperature rounded to 0.1 K with a thermal power, a mass flow and an outlet temperature,
the flow computed with hplib's own specific heat of water, 4200 J/(kg K). The storage at the other end of the
circuit integrates that flow at the unrounded return, so the heat pump books the heat the flow carries at HiSim's
water ``c`` (:data:`hisim.hydronics.WATER_SPECIFIC_HEAT_J_PER_KG_K`), from the return temperature it read, and its
electricity at the step's COP. The fixed-flow mode (a series storage or none) runs the pump at its nominal flow and
books by the same rule.

The unit tests step the component once in each mode; the run tests simulate every recorded heat-pump twin, the
grouped twins RenoVisor translates from and the composed heat-pump file for a winter day, and check every step.
"""

from pathlib import Path
from typing import Dict, List, Mapping, Tuple

import numpy as np
from numpy.typing import ArrayLike
import pandas as pd
import pytest
import yaml

from hisim import component as cp
from hisim import hydronics
from hisim import loadtypes as lt
from hisim.components.more_advanced_heat_pump_hplib import (
    MoreAdvancedHeatPumpHPLib,
    MoreAdvancedHeatPumpHPLibConfig,
    MoreAdvancedHeatPumpHPLibControllerDHW,
    MoreAdvancedHeatPumpHPLibControllerDHWConfig,
    MoreAdvancedHeatPumpHPLibState,
    PositionHotWaterStorageInSystemSetup,
)
from hisim.config import ComponentID
from hisim.energy_system.executor import run_energy_system
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


class HeatPumpTwins:
    """The energy-system files with an hplib heat pump, and how their runs are read."""

    #: The directory of the recorded twins.
    ENERGY_SYSTEMS = Path(__file__).resolve().parents[1] / "energy_systems"

    #: Every file in ``energy_systems/`` that builds a :class:`MoreAdvancedHeatPumpHPLib`.
    FILES: Tuple[str, ...] = (
        "household_heatpump_building_sizer.energy_system.yaml",
        "household_heatpump_building_sizer.grouped.energy_system.yaml",
        "household_heatpump_building_sizer.composed.energy_system.yaml",
        "household_heatpump_car_building_sizer.energy_system.yaml",
        "household_heatpump_car_building_sizer.grouped.energy_system.yaml",
        "household_heatpump_solar_thermal_building_sizer.energy_system.yaml",
        "household_heatpump_solar_thermal_building_sizer.grouped.energy_system.yaml",
        "automatic_default_connections.energy_system.yaml",
    )

    #: A January day: the heat pump charges the buffer and the hot-water cylinder, each on some steps.
    WINTER_DAY: Tuple[str, str] = ("2021-01-10T00:00:00", "2021-01-11T00:00:00")

    #: Per circuit, the heat pump's outputs: mass flow, outlet and return temperature, heat and electricity.
    CIRCUITS: Dict[str, Tuple[str, str, str, str, str]] = {
        "space heating": (
            MoreAdvancedHeatPumpHPLib.MassFlowOutputSH,
            MoreAdvancedHeatPumpHPLib.TemperatureOutputSH,
            MoreAdvancedHeatPumpHPLib.TemperatureInputSH,
            MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH,
            MoreAdvancedHeatPumpHPLib.ElectricalInputPowerSH,
        ),
        "hot water": (
            MoreAdvancedHeatPumpHPLib.MassFlowOutputDHW,
            MoreAdvancedHeatPumpHPLib.TemperatureOutputDHW,
            MoreAdvancedHeatPumpHPLib.TemperatureInputDHW,
            MoreAdvancedHeatPumpHPLib.ThermalOutputPowerDHW,
            MoreAdvancedHeatPumpHPLib.ElectricalInputPowerDHW,
        ),
    }

    @classmethod
    def run(cls, file_name: str, directory: Path, seconds_per_timestep: int) -> pd.DataFrame:
        """Simulate one file over :attr:`WINTER_DAY` and return its per-step results, one column per output.

        Args:
            file_name: A file of :attr:`FILES`.
            directory: An empty directory for the parameters file and the results.
            seconds_per_timestep: The resolution, 60 or 900 s.

        Returns:
            The simulator's result frame, columns named ``"<component> - <field> [<load type> - <unit>]"``.
        """
        parameters = directory / "winter_day.simulation.yaml"
        start, end = cls.WINTER_DAY
        settings = {
            "start_date": start,
            "end_date": end,
            "seconds_per_timestep": seconds_per_timestep,
            "country": "DE",
            "logging_level": 3,
            "post_processing_options": [],
        }
        parameters.write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")
        built = run_energy_system(
            cls.ENERGY_SYSTEMS / file_name, parameters, result_directory=str(directory / "results")
        )
        return built.simulator.results_data_frame

    @classmethod
    def columns(cls, results: pd.DataFrame) -> Dict[str, pd.Series]:
        """The heat pump's columns by field name, together with its COP.

        The heat pump is the one component with a ``ThermalOutputPowerSH`` output; in a composed file it carries
        its member address (``heating-HeatPump``) instead of the twin's name.

        Raises:
            AssertionError: If not exactly one component has that output.
        """
        marker = f" - {MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH} ["
        owners = [column.split(marker)[0] for column in results.columns if marker in column]
        assert len(owners) == 1, owners
        prefix = f"{owners[0]} - "
        return {
            column[len(prefix):].split(" [")[0]: results[column]
            for column in results.columns
            if column.startswith(prefix)
        }


def check_every_step(outputs: Mapping[str, ArrayLike]) -> Dict[str, int]:
    """Assert, on every step of both circuits, that the booked heat is the flow's heat and the electricity follows it.

    The flow's heat is ``m c (T_out - T_in)`` from the heat pump's own mass-flow, outlet and return outputs, in the
    order :func:`hisim.hydronics.circuit_power_w` evaluates it. On every heating step (a flow and a COP above 0) the
    electricity is that heat over the COP. On every cooling step (a negative booked heat) the electricity for
    cooling is the heat drawn over hplib's EER, ``ElectricalInputPowerForCooling = -P_th / EER``. On a step without
    flow the booked heat and the electricity are zero.

    Args:
        outputs: The heat pump's outputs by field name, one value per step.

    Returns:
        Per circuit, the number of steps with a flow.
    """
    cop = np.asarray(outputs[MoreAdvancedHeatPumpHPLib.COP], dtype=float)
    eer = np.asarray(outputs[MoreAdvancedHeatPumpHPLib.EER], dtype=float)
    electrical_for_cooling = np.asarray(outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerForCooling], dtype=float)
    running_steps: Dict[str, int] = {}
    for circuit, (mass_flow, t_out, t_in, thermal, electrical) in HeatPumpTwins.CIRCUITS.items():
        flow = np.asarray(outputs[mass_flow], dtype=float)
        carried = flow * hydronics.WATER_SPECIFIC_HEAT_J_PER_KG_K * (
            np.asarray(outputs[t_out], dtype=float) - np.asarray(outputs[t_in], dtype=float)
        )
        booked = np.asarray(outputs[thermal], dtype=float)
        consumed = np.asarray(outputs[electrical], dtype=float)
        np.testing.assert_allclose(booked, carried, rtol=1e-12, atol=1e-9, err_msg=circuit)
        running = flow > 0.0
        heating = running & (cop > 0.0)
        cooling = booked < 0.0
        np.testing.assert_allclose(consumed[heating], booked[heating] / cop[heating], rtol=1e-12, err_msg=circuit)
        np.testing.assert_allclose(
            electrical_for_cooling[cooling], -booked[cooling] / eer[cooling], rtol=1e-12, err_msg=circuit
        )
        assert not booked[~running].any() and not consumed[~running].any(), circuit
        running_steps[circuit] = int(running.sum())
    return running_steps


def run_and_check_every_step(file_name: str, directory: Path, seconds_per_timestep: int) -> None:
    """Simulate a twin over the winter day and apply :func:`check_every_step` to every step of its heat pump.

    Every circuit must run on some step of the day, or the check would hold on nothing.
    """
    results = HeatPumpTwins.run(file_name, directory, seconds_per_timestep)
    running_steps = check_every_step(HeatPumpTwins.columns(results))
    for circuit, steps in running_steps.items():
        assert steps > 0, f"the {circuit} circuit never runs on the winter day"


@pytest.mark.extendedbase2
@pytest.mark.parametrize("file_name", HeatPumpTwins.FILES)
def test_every_heat_pump_twin_books_the_heat_its_flow_carries_at_900_s(file_name: str, tmp_path: Path) -> None:
    """A winter day at 900 s of every heat-pump file: flow heat equals booked heat on every step, both circuits."""
    run_and_check_every_step(file_name, tmp_path, 900)


@pytest.mark.extendedbase2
@pytest.mark.parametrize("file_name", HeatPumpTwins.FILES)
def test_every_heat_pump_twin_books_the_heat_its_flow_carries_at_60_s(file_name: str, tmp_path: Path) -> None:
    """The same winter day at 60 s, where the heat pump starts and stops within the hour."""
    run_and_check_every_step(file_name, tmp_path, 60)


class SingleStep:
    """One step of a heat pump whose every input is a fake output holding a value."""

    @staticmethod
    def heat_pump(position: PositionHotWaterStorageInSystemSetup) -> MoreAdvancedHeatPumpHPLib:
        """A 10 kW generic air/water heat pump with hot-water preparation, running for ten minutes already.

        Args:
            position: Where its storage sits: ``PARALLEL`` (hplib's flow) or ``SERIES`` (the nominal flow).
        """
        config = MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump")
        config.set_thermal_output_power_in_watt = 10000.0
        config.heating_reference_temperature_in_celsius = -7.0
        config.flow_temperature_in_celsius = 52.0
        config.cycling_mode = False
        config.minimum_thermal_output_power_in_watt = 1500.0
        config.massflow_nominal_secondary_side_in_kg_per_s = 0.333
        config.with_domestic_hot_water_preparation = True
        config.position_hot_water_storage_in_system = position
        heat_pump = MoreAdvancedHeatPumpHPLib(
            config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, 60)
        )
        heat_pump.state = MoreAdvancedHeatPumpHPLibState(
            time_on_heating=600,
            time_off=0,
            time_on_cooling=0,
            on_off_previous=1,
            cumulative_thermal_energy_tot_in_watt_hour=0,
            cumulative_thermal_energy_sh_in_watt_hour=0,
            cumulative_thermal_energy_dhw_in_watt_hour=0,
            cumulative_electrical_energy_tot_in_watt_hour=0,
            cumulative_electrical_energy_sh_in_watt_hour=0,
            cumulative_electrical_energy_dhw_in_watt_hour=0,
            counter_switch_sh=0,
            counter_switch_dhw=0,
            counter_onoff=0,
            delta_t_secondary_side=5,
            delta_t_primary_side=0,
        )
        return heat_pump

    @staticmethod
    def step(heat_pump: MoreAdvancedHeatPumpHPLib, values: Dict[str, float]) -> Dict[str, float]:
        """Step ``heat_pump`` once with its inputs set from ``values`` by field name (0 for the rest).

        The hot-water set temperature, an optional input, stays unconnected unless ``values`` names it, so only the
        maximal supply temperature limits the hot-water supply.

        Returns:
            Every output of the heat pump by field name.
        """
        fakes: List[cp.ComponentOutput] = []
        for component_input in heat_pump.inputs:
            if component_input.field_name == MoreAdvancedHeatPumpHPLib.SupplyTemperatureSetDHW and (
                component_input.field_name not in values
            ):
                continue
            fake = cp.ComponentOutput(
                "Fake",
                component_input.field_name,
                lt.LoadTypes.ANY,
                lt.Units.ANY,
                component_id=ComponentID("Fake"),
            )
            component_input.source_output = fake
            fakes.append(fake)
        fft.add_global_index_of_components([*fakes, heat_pump])
        stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*fakes, heat_pump]))
        for fake in fakes:
            stsv.values[fake.global_index] = values.get(fake.field_name, 0.0)
        heat_pump.i_simulate(timestep=0, stsv=stsv, force_convergence=False)
        return {output.field_name: stsv.values[output.global_index] for output in heat_pump.outputs}


def flow_heat(outputs: Dict[str, float], mass_flow: str, t_out: str, t_in: str) -> float:
    """The heat a circuit's water carries, ``m c (T_out - T_in)`` in W, from the heat pump's own outputs."""
    return hydronics.circuit_power_w(outputs[mass_flow], outputs[t_out], outputs[t_in])


@pytest.mark.base
def test_the_booked_powers_are_the_flow_s_heat_and_that_heat_over_the_cop() -> None:
    """For hplib's 8400 W at 0.4 kg/s and 35.0 °C from a return of 30.04 °C (30.0 °C rounded), 8293.12 W is booked."""
    thermal, electrical = MoreAdvancedHeatPumpHPLib.booked_heating_powers(0.4, 35.0, 30.04, 3.5)
    assert thermal == pytest.approx(0.4 * 4180.0 * 4.96, rel=1e-12)
    assert thermal == pytest.approx(8293.12, rel=1e-12)
    assert electrical == thermal / 3.5


@pytest.mark.base
def test_hplib_s_flow_is_booked_at_the_unrounded_return_in_the_parallel_mode() -> None:
    """A return of 47.04 °C: hplib's results at 47.0 and 47.1 °C are interpolated, and the flow is booked from 47.04.

    The interpolated outlet is 52.04 °C, the 5 K lift hplib holds; the booked heat is the interpolated flow's at
    HiSim's 4180 J/(kg K), about 0.5 % below hplib's own heat at its 4200 J/(kg K).
    """
    outputs = SingleStep.step(
        SingleStep.heat_pump(PositionHotWaterStorageInSystemSetup.PARALLEL),
        {
            MoreAdvancedHeatPumpHPLib.OnOffSwitchSH: 1,
            MoreAdvancedHeatPumpHPLib.TemperatureInputPrimary: -7.0,
            MoreAdvancedHeatPumpHPLib.TemperatureAmbient: -7.0,
            MoreAdvancedHeatPumpHPLib.TemperatureInputSecondarySH: 47.04,
            MoreAdvancedHeatPumpHPLib.TemperatureInputSecondaryDHW: 50.0,
        },
    )
    assert outputs[MoreAdvancedHeatPumpHPLib.TemperatureOutputSH] == pytest.approx(52.04, abs=1e-9)
    booked = outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH]
    assert booked == flow_heat(
        outputs,
        MoreAdvancedHeatPumpHPLib.MassFlowOutputSH,
        MoreAdvancedHeatPumpHPLib.TemperatureOutputSH,
        MoreAdvancedHeatPumpHPLib.TemperatureInputSH,
    )
    hplib_heat = outputs[MoreAdvancedHeatPumpHPLib.MassFlowOutputSH] * 4200.0 * 5.0
    assert booked == pytest.approx(hplib_heat * 4180.0 / 4200.0, rel=1e-9)
    assert outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerSH] == booked / outputs[MoreAdvancedHeatPumpHPLib.COP]


@pytest.mark.base
def test_hplib_s_results_are_interpolated_linearly_between_its_grid_points() -> None:
    """Between two 0.1 K grid points every result is the linear blend of the two: continuous in the return."""
    heat_pump = SingleStep.heat_pump(PositionHotWaterStorageInSystemSetup.PARALLEL)

    def results(t_in_secondary: float):
        return heat_pump.get_cached_results_or_run_hplib_simulation(-7.0, t_in_secondary, -7.0, 1, "heating_sh", 1500.0)

    lower, upper, between = results(47.0), results(47.1), results(47.04)
    for key in ("T_out", "m_dot", "P_th", "P_el", "COP"):
        assert between[key] == pytest.approx(0.6 * lower[key] + 0.4 * upper[key], rel=1e-12)
    # on either side of a grid point the results meet: no step at 47.1 °C
    assert results(47.1 - 1e-9)["m_dot"] == pytest.approx(upper["m_dot"], rel=1e-6)
    assert results(47.1 + 1e-9)["m_dot"] == pytest.approx(upper["m_dot"], rel=1e-6)


@pytest.mark.base
@pytest.mark.parametrize(
    ("switches", "circuit"),
    [
        ({MoreAdvancedHeatPumpHPLib.OnOffSwitchSH: 1}, "space heating"),
        ({MoreAdvancedHeatPumpHPLib.OnOffSwitchDHW: 2}, "hot water"),
    ],
)
def test_the_fixed_flow_mode_books_the_heat_of_the_nominal_flow(switches: Dict[str, float], circuit: str) -> None:
    """A series storage: the pump runs at 0.333 kg/s, the power sets the outlet and the flow books the heat."""
    values = {
        MoreAdvancedHeatPumpHPLib.TemperatureInputPrimary: 2.0,
        MoreAdvancedHeatPumpHPLib.TemperatureAmbient: 2.0,
        MoreAdvancedHeatPumpHPLib.TemperatureInputSecondarySH: 30.04,
        MoreAdvancedHeatPumpHPLib.TemperatureInputSecondaryDHW: 45.03,
        MoreAdvancedHeatPumpHPLib.SetHeatingTemperatureSH: 40.0,
        **switches,
    }
    outputs = SingleStep.step(SingleStep.heat_pump(PositionHotWaterStorageInSystemSetup.SERIES), values)
    mass_flow, t_out, t_in, thermal, electrical = HeatPumpTwins.CIRCUITS[circuit]
    assert outputs[mass_flow] == 0.333
    booked = outputs[thermal]
    assert booked == flow_heat(outputs, mass_flow, t_out, t_in)
    # 5 K at the nominal flow, ramped up after ten minutes of running: (1 - e^(-600/360)) of 6959.7 W.
    assert booked == pytest.approx(0.333 * 4180.0 * 5.0 * (1.0 - np.exp(-600.0 / 360.0)), rel=1e-12)
    assert outputs[electrical] == booked / outputs[MoreAdvancedHeatPumpHPLib.COP]


@pytest.mark.base
def test_active_cooling_books_the_heat_its_flow_draws_and_that_heat_over_the_eer() -> None:
    """Cooling at 30 °C outside from a 22.04 °C return: a negative flow heat, electricity at hplib's EER."""
    outputs = SingleStep.step(
        SingleStep.heat_pump(PositionHotWaterStorageInSystemSetup.PARALLEL),
        {
            MoreAdvancedHeatPumpHPLib.OnOffSwitchSH: -1,
            MoreAdvancedHeatPumpHPLib.TemperatureInputPrimary: 30.0,
            MoreAdvancedHeatPumpHPLib.TemperatureAmbient: 30.0,
            MoreAdvancedHeatPumpHPLib.TemperatureInputSecondarySH: 22.04,
            MoreAdvancedHeatPumpHPLib.TemperatureInputSecondaryDHW: 50.0,
        },
    )
    booked = outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH]
    assert booked < 0.0
    assert booked == flow_heat(
        outputs,
        MoreAdvancedHeatPumpHPLib.MassFlowOutputSH,
        MoreAdvancedHeatPumpHPLib.TemperatureOutputSH,
        MoreAdvancedHeatPumpHPLib.TemperatureInputSH,
    )
    assert outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerForCooling] == -booked / outputs[
        MoreAdvancedHeatPumpHPLib.EER
    ]


@pytest.mark.base
def test_the_run_check_holds_on_an_active_cooling_step() -> None:
    """The run tests' per-step check on one cooling step, the branch no recorded twin reaches (none cools).

    The step books a negative heat, has a COP of 0 and so is no heating step, and its electricity for cooling is
    the heat drawn over hplib's EER. The check also fails when that electricity is off, so it checks something.
    """
    outputs = SingleStep.step(
        SingleStep.heat_pump(PositionHotWaterStorageInSystemSetup.PARALLEL),
        {
            MoreAdvancedHeatPumpHPLib.OnOffSwitchSH: -1,
            MoreAdvancedHeatPumpHPLib.TemperatureInputPrimary: 30.0,
            MoreAdvancedHeatPumpHPLib.TemperatureAmbient: 30.0,
            MoreAdvancedHeatPumpHPLib.TemperatureInputSecondarySH: 22.04,
            MoreAdvancedHeatPumpHPLib.TemperatureInputSecondaryDHW: 50.0,
        },
    )
    assert outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH] < 0.0
    assert outputs[MoreAdvancedHeatPumpHPLib.COP] == 0.0
    steps = {field_name: [value] for field_name, value in outputs.items()}
    assert check_every_step(steps) == {"space heating": 1, "hot water": 0}
    steps[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerForCooling] = [
        outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerForCooling] * 1.01
    ]
    with pytest.raises(AssertionError, match="space heating"):
        check_every_step(steps)


@pytest.mark.base
def test_a_constant_hot_water_power_with_a_parallel_storage_is_refused() -> None:
    """A constant power would book heat that hplib's flow does not carry, so the step refuses it by name."""
    heat_pump = SingleStep.heat_pump(PositionHotWaterStorageInSystemSetup.PARALLEL)
    with pytest.raises(ValueError, match="thermalpower_dhw_is_constant"):
        SingleStep.step(
            heat_pump,
            {
                MoreAdvancedHeatPumpHPLib.OnOffSwitchDHW: 2,
                MoreAdvancedHeatPumpHPLib.ThermalPowerIsConstantForDHW: 1,
                MoreAdvancedHeatPumpHPLib.MaxThermalPowerValueForDHW: 5000.0,
                MoreAdvancedHeatPumpHPLib.TemperatureInputPrimary: 2.0,
                MoreAdvancedHeatPumpHPLib.TemperatureAmbient: 2.0,
                MoreAdvancedHeatPumpHPLib.TemperatureInputSecondaryDHW: 45.0,
            },
        )


@pytest.mark.base
def test_a_hot_water_outlet_above_the_maximal_supply_temperature_is_throttled() -> None:
    """A 72.04 °C return: hplib's interpolated outlet, 77.04 °C, stops at the 75 °C limit; the flow books up to it.

    The flow stays hplib's, so the circuit carries ``m c (75 - 72.04)`` and the electricity is that heat over the COP.
    """
    outputs = SingleStep.step(
        SingleStep.heat_pump(PositionHotWaterStorageInSystemSetup.PARALLEL),
        {
            MoreAdvancedHeatPumpHPLib.OnOffSwitchDHW: 2,
            MoreAdvancedHeatPumpHPLib.TemperatureInputPrimary: 10.0,
            MoreAdvancedHeatPumpHPLib.TemperatureAmbient: 10.0,
            MoreAdvancedHeatPumpHPLib.TemperatureInputSecondarySH: 30.0,
            MoreAdvancedHeatPumpHPLib.TemperatureInputSecondaryDHW: 72.04,
        },
    )
    assert outputs[MoreAdvancedHeatPumpHPLib.TemperatureOutputDHW] == 75.0
    booked = outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerDHW]
    assert booked == flow_heat(
        outputs,
        MoreAdvancedHeatPumpHPLib.MassFlowOutputDHW,
        MoreAdvancedHeatPumpHPLib.TemperatureOutputDHW,
        MoreAdvancedHeatPumpHPLib.TemperatureInputDHW,
    )
    assert booked == pytest.approx(
        outputs[MoreAdvancedHeatPumpHPLib.MassFlowOutputDHW] * hydronics.WATER_SPECIFIC_HEAT_J_PER_KG_K * 2.96,
        rel=1e-9,
    )
    assert outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerDHW] == booked / outputs[MoreAdvancedHeatPumpHPLib.COP]


@pytest.mark.base
@pytest.mark.parametrize(
    ("t_out_c", "t_in_c", "expected_c"), [(77.0, 72.0, 75.0), (75.0, 70.0, 75.0), (79.0, 76.0, 76.0)]
)
def test_the_hot_water_supply_stays_within_the_limit_and_never_below_the_return(
    t_out_c: float, t_in_c: float, expected_c: float
) -> None:
    """Above the 75 °C limit the supply is the limit, or the return when that is hotter; below it is unchanged."""
    heat_pump = SingleStep.heat_pump(PositionHotWaterStorageInSystemSetup.PARALLEL)
    assert heat_pump.throttled_dhw_supply(t_out_c, t_in_c) == expected_c


@pytest.mark.base
def test_a_hot_water_outlet_above_the_controllers_set_temperature_stops_at_it() -> None:
    """With the controller's 60 °C set temperature, a 58 °C return's interpolated outlet stops at 60 °C.

    The flow stays hplib's, so the circuit carries ``m c (60 - 58)`` and the electricity is that heat over the COP.
    """
    outputs = SingleStep.step(
        SingleStep.heat_pump(PositionHotWaterStorageInSystemSetup.PARALLEL),
        {
            MoreAdvancedHeatPumpHPLib.OnOffSwitchDHW: 2,
            MoreAdvancedHeatPumpHPLib.TemperatureInputPrimary: 10.0,
            MoreAdvancedHeatPumpHPLib.TemperatureAmbient: 10.0,
            MoreAdvancedHeatPumpHPLib.TemperatureInputSecondarySH: 30.0,
            MoreAdvancedHeatPumpHPLib.TemperatureInputSecondaryDHW: 58.0,
            MoreAdvancedHeatPumpHPLib.SupplyTemperatureSetDHW: 60.0,
        },
    )
    assert outputs[MoreAdvancedHeatPumpHPLib.TemperatureOutputDHW] == 60.0
    booked = outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerDHW]
    assert booked == pytest.approx(
        outputs[MoreAdvancedHeatPumpHPLib.MassFlowOutputDHW] * hydronics.WATER_SPECIFIC_HEAT_J_PER_KG_K * 2.0,
        rel=1e-9,
    )
    assert outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerDHW] == booked / outputs[MoreAdvancedHeatPumpHPLib.COP]


@pytest.mark.base
@pytest.mark.parametrize(
    ("t_out_c", "t_in_c", "set_c", "expected_c"),
    [(63.0, 58.0, 60.0, 60.0), (59.0, 54.0, 60.0, 59.0), (77.0, 72.0, 80.0, 75.0), (66.0, 62.0, 70.0, 66.0)],
)
def test_the_hot_water_supply_stays_below_the_set_temperature_and_the_maximum(
    t_out_c: float, t_in_c: float, set_c: float, expected_c: float
) -> None:
    """The limit is the lower of the controller's set temperature and the 75 °C maximum."""
    heat_pump = SingleStep.heat_pump(PositionHotWaterStorageInSystemSetup.PARALLEL)
    assert heat_pump.throttled_dhw_supply(t_out_c, t_in_c, set_c) == expected_c


@pytest.mark.base
@pytest.mark.parametrize(
    ("start_temperature_c", "modifier_k", "expected_state", "expected_set_c"),
    [(59.4, 0.0, 2, 60.0), (59.5, 0.0, 0, 60.0), (69.4, 10.0, 2, 70.0), (69.5, 10.0, 0, 70.0)],
)
def test_the_hot_water_charge_ends_within_half_a_kelvin_of_the_set_temperature(
    start_temperature_c: float, modifier_k: float, expected_state: int, expected_set_c: float
) -> None:
    """A running charge ends once the tank's start temperature is within 0.5 K of t_max plus the raise.

    The heat pump's supply is capped at that set temperature, which the controller also states, so the tank
    approaches it without passing it; a 60 °C target ends the charge at 59.5 °C, a raised 70 °C one at 69.5 °C.
    """
    controller = MoreAdvancedHeatPumpHPLibControllerDHW(
        my_simulation_parameters=SimulationParameters.one_day_only(2021, 60),
        config=MoreAdvancedHeatPumpHPLibControllerDHWConfig.preset_standard("HeatPumpControllerDHW"),
    )
    fakes = []
    for component_input in controller.inputs:
        fake = cp.ComponentOutput(
            "Fake", component_input.field_name, lt.LoadTypes.ANY, lt.Units.ANY, component_id=ComponentID("Fake")
        )
        component_input.source_output = fake
        fakes.append(fake)
    fft.add_global_index_of_components([*fakes, controller])
    stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*fakes, controller]))
    values = {
        MoreAdvancedHeatPumpHPLibControllerDHW.WaterTemperatureInputFromDHWStorage: start_temperature_c,
        MoreAdvancedHeatPumpHPLibControllerDHW.DHWStorageTemperatureModifier: modifier_k,
    }
    for fake in fakes:
        stsv.values[fake.global_index] = values[fake.field_name]
    controller.state_dhw = 2
    controller.i_simulate(timestep=0, stsv=stsv, force_convergence=False)
    assert controller.state_dhw == expected_state
    assert stsv.values[controller.supply_temperature_set_dhw_channel.global_index] == expected_set_c
