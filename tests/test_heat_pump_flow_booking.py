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
        carried_thermal_power_in_watt = mass_flow_in_kg_per_second * hydronics.WATER_SPECIFIC_HEAT_J_PER_KG_K * (
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

    @staticmethod
    def step(heat_pump: MoreAdvancedHeatPumpHPLib, values: Dict[str, float]) -> Dict[str, float]:
        """Step ``heat_pump`` once with its inputs set from ``values`` by field name (0 for the rest).

        Returns:
            Every output of the heat pump by field name.
        """
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
    return hydronics.circuit_power_w(outputs[mass_flow], outputs[t_out], outputs[t_in])


@pytest.mark.base
def test_the_booked_powers_are_the_flow_s_heat_and_that_heat_over_the_cop() -> None:
    """For hplib's 8400 W at 0.4 kg/s and 35.0 °C from a return of 30.04 °C (30.0 °C rounded), 8293.12 W is booked."""
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
    """A return of 47.04 °C: hplib computes at 47.0 °C, the heat pump books its flow from 47.04 °C."""
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
    assert outputs[MoreAdvancedHeatPumpHPLib.TemperatureOutputSH] == 52.0
    booked_power_in_watt = outputs[MoreAdvancedHeatPumpHPLib.ThermalOutputPowerSH]
    assert booked_power_in_watt == flow_thermal_power_in_watt(
        outputs,
        MoreAdvancedHeatPumpHPLib.MassFlowOutputSH,
        MoreAdvancedHeatPumpHPLib.TemperatureOutputSH,
        MoreAdvancedHeatPumpHPLib.TemperatureInputSH,
    )
    # hplib's own heat, m * 4200 J/(kg K) * 5 K, is about 1.3 % more than the flow carries at 4.96 K and 4180.
    hplib_thermal_power_in_watt = outputs[MoreAdvancedHeatPumpHPLib.MassFlowOutputSH] * 4200.0 * 5.0
    assert booked_power_in_watt == pytest.approx(hplib_thermal_power_in_watt * 4180.0 / 4200.0 * 4.96 / 5.0, rel=1e-9)
    assert outputs[MoreAdvancedHeatPumpHPLib.ElectricalInputPowerSH] == (
        booked_power_in_watt / outputs[MoreAdvancedHeatPumpHPLib.COP]
    )


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
