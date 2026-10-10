"""The hplib heat pump books the heat its water carries: ``P_th = m c (T_out - T_in)`` and ``P_el = P_th / COP``.

hplib answers a return temperature with a thermal power, a mass flow and an outlet temperature, interpolated
between its 0.1 K grid points, the flow computed with hplib's own specific heat of water, 4200 J/(kg K). The storage
at the other end of the circuit integrates that flow at the same return, so the heat pump books the heat the flow
carries at HiSim's water ``c`` (:data:`hisim.hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K`), from the return temperature
it read, and its electricity at the step's COP; in the parallel mode that heat is hplib's own times 4180/4200.
The fixed-flow mode (a series storage or none) runs the pump at its nominal flow and books by the same rule.

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
from hisim.components.more_advanced_heat_pump_hplib_model import HplibResult
from hisim.components.more_advanced_heat_pump_hplib import (
    HeatPumpDhwState,
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
    electrical_power_for_cooling_in_watt = np.asarray(
        outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerForCooling], dtype=float
    )
    running_steps: Dict[str, int] = {}
    for circuit, (mass_flow, t_out, t_in, thermal, electrical) in HeatPumpTwins.CIRCUITS.items():
        mass_flow_in_kg_per_second = np.asarray(outputs[mass_flow], dtype=float)
        carried_thermal_power_in_watt = mass_flow_in_kg_per_second * hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K * (
            np.asarray(outputs[t_out], dtype=float) - np.asarray(outputs[t_in], dtype=float)
        )
        booked_power_in_watt = np.asarray(outputs[thermal], dtype=float)
        consumed_electrical_power_in_watt = np.asarray(outputs[electrical], dtype=float)
        np.testing.assert_allclose(
            booked_power_in_watt, carried_thermal_power_in_watt, rtol=1e-12, atol=1e-9, err_msg=circuit
        )
        running = mass_flow_in_kg_per_second > 0.0
        heating = running & (cop > 0.0)
        cooling = booked_power_in_watt < 0.0
        np.testing.assert_allclose(
            consumed_electrical_power_in_watt[heating],
            booked_power_in_watt[heating] / cop[heating],
            rtol=1e-12,
            err_msg=circuit,
        )
        np.testing.assert_allclose(
            electrical_power_for_cooling_in_watt[cooling],
            -booked_power_in_watt[cooling] / eer[cooling],
            rtol=1e-12,
            err_msg=circuit,
        )
        assert not booked_power_in_watt[~running].any(), circuit
        assert not consumed_electrical_power_in_watt[~running].any(), circuit
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

    #: The hot-water set temperature a step uses unless the test names one, in °C: the 75 °C maximal hot-water supply
    #: itself, so that only the maximum limits the hot-water supply.
    UNLIMITING_SET_TEMPERATURE_IN_CELSIUS = 75.0

    @staticmethod
    def step(heat_pump: MoreAdvancedHeatPumpHPLib, values: Dict[str, float]) -> Dict[str, float]:
        """Step ``heat_pump`` once with its inputs set from ``values`` by field name (0 for the rest).

        The hot-water set temperature is :attr:`UNLIMITING_SET_TEMPERATURE_IN_CELSIUS` unless ``values`` names one.

        Returns:
            Every output of the heat pump by field name.
        """
        values = {
            MoreAdvancedHeatPumpHPLib.SupplyTemperatureSetForDHWInCelsius: SingleStep.UNLIMITING_SET_TEMPERATURE_IN_CELSIUS,
            **values,
        }
        fakes: List[cp.ComponentOutput] = []
        for component_input in heat_pump.inputs:
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


def flow_thermal_power_in_watt(outputs: Dict[str, float], mass_flow: str, t_out: str, t_in: str) -> float:
    """The heat a circuit's water carries, ``m c (T_out - T_in)`` in W, from the heat pump's own outputs."""
    return hydronics.circuit_power_w(
        mass_flow_kg_per_s=outputs[mass_flow], t_supply_c=outputs[t_out], t_return_c=outputs[t_in]
    )


@pytest.mark.base
def test_the_booked_powers_are_the_flow_s_heat_and_that_heat_over_the_cop() -> None:
    """A flow of 0.4 kg/s from a return of 30.04 °C to 35.0 °C books 0.4 * 4180 * 4.96 = 8293.12 W, over the COP."""
    powers = MoreAdvancedHeatPumpHPLib.booked_heating_powers_in_watt(
        mass_flow_in_kg_per_second=0.4,
        outlet_temperature_in_celsius=35.0,
        return_temperature_in_celsius=30.04,
        cop=3.5,
    )
    assert powers.thermal_power_in_watt == pytest.approx(0.4 * 4180.0 * 4.96, rel=1e-12)
    assert powers.thermal_power_in_watt == pytest.approx(8293.12, rel=1e-12)
    assert powers.electrical_power_in_watt == powers.thermal_power_in_watt / 3.5


@pytest.mark.base
@pytest.mark.parametrize("cop", [0.0, -1.0])
def test_a_heating_circuit_without_a_positive_cop_is_refused(cop: float) -> None:
    """A COP of 0 or less would book infinite or negative electricity; the booking must refuse it by name."""
    with pytest.raises(ValueError, match="coefficient of performance above 0"):
        MoreAdvancedHeatPumpHPLib.booked_heating_powers_in_watt(
            mass_flow_in_kg_per_second=0.4,
            outlet_temperature_in_celsius=35.0,
            return_temperature_in_celsius=30.0,
            cop=cop,
        )


@pytest.mark.base
def test_active_cooling_draws_the_heat_removed_over_the_eer() -> None:
    """3000 W removed at an EER of 4 draws 750 W; a sign or division error in the cooling electricity fails this."""
    assert MoreAdvancedHeatPumpHPLib.active_cooling_electrical_power_in_watt(
        thermal_power_in_watt=-3000.0, eer=4.0
    ) == pytest.approx(750.0, rel=1e-15)


@pytest.mark.base
@pytest.mark.parametrize("eer", [0.0, -2.0])
def test_active_cooling_without_a_positive_eer_is_refused(eer: float) -> None:
    """An EER of 0 or less used to book free cooling silently; the cooling electricity must refuse it instead."""
    with pytest.raises(ValueError, match="energy efficiency ratio above 0"):
        MoreAdvancedHeatPumpHPLib.active_cooling_electrical_power_in_watt(thermal_power_in_watt=-3000.0, eer=eer)


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
    booked_power_in_watt = outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH]
    assert booked_power_in_watt == flow_thermal_power_in_watt(
        outputs,
        MoreAdvancedHeatPumpHPLib.MassFlowOutputSH,
        MoreAdvancedHeatPumpHPLib.TemperatureOutputSH,
        MoreAdvancedHeatPumpHPLib.TemperatureInputSH,
    )
    hplib_thermal_power_in_watt = outputs[MoreAdvancedHeatPumpHPLib.MassFlowOutputSH] * 4200.0 * 5.0
    assert booked_power_in_watt == pytest.approx(hplib_thermal_power_in_watt * 4180.0 / 4200.0, rel=1e-9)
    assert outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerSH] == (
        booked_power_in_watt / outputs[MoreAdvancedHeatPumpHPLib.COP]
    )


@pytest.mark.base
def test_hplib_s_results_are_interpolated_linearly_between_its_grid_points() -> None:
    """Between two 0.1 K grid points every result is the linear blend of the two: continuous in the return."""
    heat_pump = SingleStep.heat_pump(PositionHotWaterStorageInSystemSetup.PARALLEL)

    def results(return_temperature_in_celsius: float) -> HplibResult:
        """Return hplib's interpolated answer at this return, -7 °C outside."""
        return heat_pump.get_cached_results_or_run_hplib_simulation(
            source_temperature_in_celsius=-7.0,
            return_temperature_in_celsius=return_temperature_in_celsius,
            ambient_temperature_in_celsius=-7.0,
            mode=1,
            operation_mode="heating_sh",
            minimal_thermal_power_in_watt=1500.0,
        )

    lower, upper, between = results(47.0), results(47.1), results(47.04)
    for field in (
        "outlet_temperature_in_celsius",
        "mass_flow_in_kg_per_second",
        "thermal_power_in_watt",
        "electrical_power_in_watt",
        "cop",
    ):
        assert getattr(between, field) == pytest.approx(
            0.6 * getattr(lower, field) + 0.4 * getattr(upper, field), rel=1e-12
        )
    # on either side of a grid point the results meet: no step at 47.1 °C
    assert results(47.1 - 1e-9).mass_flow_in_kg_per_second == pytest.approx(upper.mass_flow_in_kg_per_second, rel=1e-6)
    assert results(47.1 + 1e-9).mass_flow_in_kg_per_second == pytest.approx(upper.mass_flow_in_kg_per_second, rel=1e-6)


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
    booked_power_in_watt = outputs[thermal]
    assert booked_power_in_watt == flow_thermal_power_in_watt(outputs, mass_flow, t_out, t_in)
    # 5 K at the nominal flow, ramped up after ten minutes of running: (1 - e^(-600/360)) of 6959.7 W.
    assert booked_power_in_watt == pytest.approx(0.333 * 4180.0 * 5.0 * (1.0 - np.exp(-600.0 / 360.0)), rel=1e-12)
    assert outputs[electrical] == booked_power_in_watt / outputs[MoreAdvancedHeatPumpHPLib.COP]


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
    booked_power_in_watt = outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH]
    assert booked_power_in_watt < 0.0
    assert booked_power_in_watt == flow_thermal_power_in_watt(
        outputs,
        MoreAdvancedHeatPumpHPLib.MassFlowOutputSH,
        MoreAdvancedHeatPumpHPLib.TemperatureOutputSH,
        MoreAdvancedHeatPumpHPLib.TemperatureInputSH,
    )
    assert outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerForCooling] == -booked_power_in_watt / outputs[
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
    booked_power_in_watt = outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerDHW]
    assert booked_power_in_watt == flow_thermal_power_in_watt(
        outputs,
        MoreAdvancedHeatPumpHPLib.MassFlowOutputDHW,
        MoreAdvancedHeatPumpHPLib.TemperatureOutputDHW,
        MoreAdvancedHeatPumpHPLib.TemperatureInputDHW,
    )
    assert booked_power_in_watt == pytest.approx(
        outputs[MoreAdvancedHeatPumpHPLib.MassFlowOutputDHW] * hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K * 2.96,
        rel=1e-9,
    )
    assert (
        outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerDHW]
        == booked_power_in_watt / outputs[MoreAdvancedHeatPumpHPLib.COP]
    )


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
            MoreAdvancedHeatPumpHPLib.SupplyTemperatureSetForDHWInCelsius: 60.0,
        },
    )
    assert outputs[MoreAdvancedHeatPumpHPLib.TemperatureOutputDHW] == 60.0
    booked_power_in_watt = outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerDHW]
    assert booked_power_in_watt == pytest.approx(
        outputs[MoreAdvancedHeatPumpHPLib.MassFlowOutputDHW] * hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K * 2.0,
        rel=1e-9,
    )
    assert (
        outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerDHW]
        == booked_power_in_watt / outputs[MoreAdvancedHeatPumpHPLib.COP]
    )


class DhwControllerTable:

    """The transitions of the heat pump's hot-water controller with the default 40/60 °C band, a namespace.

    Each row is (state before, tank start temperature in °C, energy manager's raise in K, state after). The rows
    cover every transition of :meth:`MoreAdvancedHeatPumpHPLibControllerDHW.next_state` and the boundary values of
    each condition: the switch-on below 40 °C, the switch-off within 0.5 K of 60 °C plus the raise, and the surplus
    switch-on below 59.5 °C while a raise is active.
    """

    OFF = HeatPumpDhwState.OFF
    ON = HeatPumpDhwState.ON
    ROWS = [
        (OFF, 39.99, 0.0, ON),  # cold tank: on
        (OFF, 40.0, 0.0, OFF),  # at the minimum: no switch-on (strict)
        (OFF, 50.0, 0.0, OFF),  # inside the band: keeps off
        (ON, 50.0, 0.0, ON),  # inside the band: keeps on
        (ON, 59.49, 0.0, ON),  # just below the switch-off point
        (ON, 59.5, 0.0, OFF),  # at the switch-off point: off
        (OFF, 59.49, 10.0, ON),  # surplus switch-on just below 59.5 °C
        (OFF, 59.5, 10.0, OFF),  # surplus switch-on stops at 59.5 °C: keeps off in the raised band
        (ON, 65.0, 10.0, ON),  # charging in the raised band [59.5, 69.5)
        (ON, 69.49, 10.0, ON),  # just below the raised switch-off point
        (ON, 69.5, 10.0, OFF),  # at the raised switch-off point: off
        (ON, 61.0, 0.0, OFF),  # the raise came and went: off without it
    ]


@pytest.mark.base
@pytest.mark.parametrize(("state", "storage_temperature_in_celsius", "raise_in_kelvin", "expected"), DhwControllerTable.ROWS)
def test_the_hot_water_controller_takes_every_transition_of_its_table(
    state: HeatPumpDhwState, storage_temperature_in_celsius: float, raise_in_kelvin: float, expected: HeatPumpDhwState
) -> None:
    """A transition off its edge would start or end charges at the wrong tank temperature.

    The surplus switch-on stopping at 59.5 °C rather than 60 °C is the row that keeps a raise that comes and goes
    within a step from toggling the charge.
    """
    assert (
        MoreAdvancedHeatPumpHPLibControllerDHW.next_state(
            state=state,
            storage_temperature_in_celsius=storage_temperature_in_celsius,
            raise_in_kelvin=raise_in_kelvin,
            minimum_temperature_in_celsius=40.0,
            maximum_temperature_in_celsius=60.0,
        )
        == expected
    )


def dhw_controller_with_fake_inputs() -> Tuple[MoreAdvancedHeatPumpHPLibControllerDHW, cp.SingleTimeStepValues, Dict[str, cp.ComponentOutput]]:
    """Return a default hot-water controller whose inputs read fake outputs, the step values and the fakes by name."""
    controller = MoreAdvancedHeatPumpHPLibControllerDHW(
        my_simulation_parameters=SimulationParameters.one_day_only(2021, 60),
        config=MoreAdvancedHeatPumpHPLibControllerDHWConfig.preset_standard("HeatPumpControllerDHW"),
    )
    fakes: Dict[str, cp.ComponentOutput] = {}
    for component_input in controller.inputs:
        fake = cp.ComponentOutput(
            "Fake", component_input.field_name, lt.LoadTypes.ANY, lt.Units.ANY, component_id=ComponentID("Fake")
        )
        component_input.source_output = fake
        fakes[component_input.field_name] = fake
    fft.add_global_index_of_components([*fakes.values(), controller])
    stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*fakes.values(), controller]))
    return controller, stsv, fakes


@pytest.mark.base
@pytest.mark.parametrize(
    ("start_temperature_in_celsius", "raise_in_kelvin", "expected_state", "expected_set_temperature_in_celsius"),
    [(59.4, 0.0, HeatPumpDhwState.ON, 60.0), (59.5, 0.0, HeatPumpDhwState.OFF, 60.0), (69.4, 10.0, HeatPumpDhwState.ON, 70.0), (69.5, 10.0, HeatPumpDhwState.OFF, 70.0)],
)
def test_the_hot_water_charge_ends_within_half_a_kelvin_of_the_set_temperature(
    start_temperature_in_celsius: float,
    raise_in_kelvin: float,
    expected_state: HeatPumpDhwState,
    expected_set_temperature_in_celsius: float,
) -> None:
    """A charge that ended only above the set temperature would never end, since the supply is capped at it.

    The controller publishes the set temperature it decides on, ``t_max`` plus the raise, and the state as the
    heat pump's hot-water signal.
    """
    controller, stsv, fakes = dhw_controller_with_fake_inputs()
    stsv.set_output_value(
        fakes[MoreAdvancedHeatPumpHPLibControllerDHW.WaterTemperatureInputFromDHWStorage], start_temperature_in_celsius
    )
    stsv.set_output_value(fakes[MoreAdvancedHeatPumpHPLibControllerDHW.DHWStorageTemperatureModifier], raise_in_kelvin)
    controller.state_dhw = HeatPumpDhwState.ON
    controller.i_simulate(timestep=0, stsv=stsv, force_convergence=False)
    assert controller.state_dhw == expected_state
    assert stsv.values[controller.state_dhw_channel.global_index] == expected_state.value
    assert (
        stsv.values[controller.supply_temperature_set_for_dhw_in_celsius_channel.global_index]
        == expected_set_temperature_in_celsius
    )


@pytest.mark.base
def test_a_zero_tank_temperature_on_the_first_pass_keeps_the_last_reading() -> None:
    """A 0 °C reading, the zeroed value of a pass before the tank ran, must not start a charge.

    The controller keeps the temperature it read last instead; without that, a controller simulated before the tank
    would switch on at every step's first pass and, since it keeps its decision between passes, charge all day.
    """
    controller, stsv, fakes = dhw_controller_with_fake_inputs()
    storage_fake = fakes[MoreAdvancedHeatPumpHPLibControllerDHW.WaterTemperatureInputFromDHWStorage]
    stsv.set_output_value(storage_fake, 50.0)
    controller.i_simulate(timestep=0, stsv=stsv, force_convergence=False)
    stsv.set_output_value(storage_fake, 0.0)
    controller.i_simulate(timestep=0, stsv=stsv, force_convergence=False)
    assert controller.state_dhw == HeatPumpDhwState.OFF
    assert controller.water_temperature_input_from_dhw_storage_in_celsius == 50.0


@pytest.mark.base
def test_passive_cooling_books_the_heat_its_flow_draws_and_only_the_brine_pump_s_electricity() -> None:
    """Passive cooling through the brine runs no compressor; booking compressor electricity or another heat would fail.

    A 25 °C return cooled towards a 20 °C set temperature at the nominal 0.333 kg/s: the circuit draws
    ``0.333 * 4180 * 5`` W, its supply is 20 °C, and only the brine pump's 100 W is electricity.
    """
    config = MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump")
    config.set_thermal_output_power_in_watt = 10000.0
    config.heating_reference_temperature_in_celsius = -7.0
    config.flow_temperature_in_celsius = 35.0
    config.cycling_mode = False
    config.minimum_thermal_output_power_in_watt = 1500.0
    config.massflow_nominal_secondary_side_in_kg_per_s = 0.333
    config.group_id = 2
    config.fluid_primary_side = "brine"
    config.specific_heat_capacity_of_primary_fluid = 3800.0
    config.electrical_input_power_brine_pump_in_watt = 100.0
    config.passive_cooling_with_brine = True
    heat_pump = MoreAdvancedHeatPumpHPLib(config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, 60))
    outputs = SingleStep.step(
        heat_pump,
        {
            MoreAdvancedHeatPumpHPLib.OnOffSwitchSH: -1,
            MoreAdvancedHeatPumpHPLib.TemperatureInputPrimary: 10.0,
            MoreAdvancedHeatPumpHPLib.TemperatureAmbient: 28.0,
            MoreAdvancedHeatPumpHPLib.TemperatureInputSecondarySH: 25.0,
            MoreAdvancedHeatPumpHPLib.SetHeatingTemperatureSH: 20.0,
        },
    )
    assert outputs[MoreAdvancedHeatPumpHPLib.TemperatureOutputSH] == pytest.approx(20.0, abs=1e-12)
    assert outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH] == pytest.approx(-0.333 * 4180.0 * 5.0, rel=1e-12)
    assert outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerTotal] == 100.0
    assert outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerForCooling] == 0.0
    assert outputs[MoreAdvancedHeatPumpHPLib.COP] == 0.0
