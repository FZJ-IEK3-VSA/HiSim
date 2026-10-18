"""The controllers of a space-heating buffer's circuits command the part-load ratios their rules give, at 900 s.

The rules themselves are tested in ``tests/test_start_of_step_part_load.py`` and the devices' scaling of a step in the
boiler's and the distribution's own tests. These tests drive the two controllers once each, with every input a fake
output holding a hand-chosen value, and check the ratio, signal and lift they publish against hand-computed values:

* the boiler controller's raising ratio, the fraction of the step that ends the buffer at the aim (the heating
  curve's supply temperature plus half the 0.05 K target band);
* its steady charge: a buffer at its target whose end temperature lies inside the target band keeps the ratio run,
  and the charge runs at full signal with the 0.1 K lift of a charge above the target;
* the rest of a hot-water step: in the heating season the space-heating charge runs the raising ratio, at most the
  1 − hot-water ratio left of the step, at full signal with the lift to the supply temperature;
* the distribution controller's floor ratio, the largest fraction that keeps the buffer at the room's temperature.

A controller that published 1 whenever its device runs would pass every device test; these do not let it.
"""

from typing import ClassVar, Dict, List

import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components import heat_distribution_system
from hisim.components.dual_circuit_system import HeatingMode
from hisim.components.generic_boiler import GenericBoilerController, GenericBoilerControllerConfig
from hisim.config import ComponentID, DisplayConfig, SizingContext
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft

pytestmark = pytest.mark.base


class ControllerPass:
    """One unforced pass of a controller at 900 s whose every input is a fake output holding a value."""

    #: The step length, in s: above the 600 s part-load threshold.
    SECONDS_PER_TIMESTEP: ClassVar[int] = 900

    #: The timestep of the pass: late enough that no minimum running or resting time of the controller applies.
    TIMESTEP: ClassVar[int] = 50

    @classmethod
    def outputs(cls, controller: cp.Component, values: Dict[str, float]) -> Dict[str, float]:
        """Run one pass of ``controller`` with its inputs set from ``values`` by field name and return its outputs.

        Args:
            controller: The controller, its mode set as the test needs it.
            values: The value of every input, by field name; an input left out reads 0.

        Returns:
            Every output of the controller by field name.
        """
        fakes: List[cp.ComponentOutput] = []
        for component_input in controller.inputs:
            fake = cp.ComponentOutput(
                "Fake", component_input.field_name, lt.LoadTypes.ANY, lt.Units.ANY, component_id=ComponentID("Fake")
            )
            component_input.source_output = fake
            fakes.append(fake)
        fft.add_global_index_of_components([*fakes, controller])
        stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*fakes, controller]))
        for fake in fakes:
            stsv.values[fake.global_index] = values.get(fake.field_name, 0.0)
        controller.i_simulate(timestep=cls.TIMESTEP, stsv=stsv, force_convergence=False)
        return {output.field_name: stsv.values[output.global_index] for output in controller.outputs}

    @classmethod
    def boiler_controller(cls, mode: HeatingMode) -> GenericBoilerController:
        """A modulating 2-20 kW boiler controller with hot-water preparation, in ``mode`` since the step before.

        Args:
            mode: The controller's mode before the pass.

        Returns:
            The controller.
        """
        config = GenericBoilerControllerConfig.preset_modulating("BoilerController").resolve(
            SizingContext(maximal_thermal_power_in_watt=20000, minimal_thermal_power_in_watt=2000)
        )
        config.with_domestic_hot_water_preparation = True
        controller = GenericBoilerController(
            SimulationParameters.one_day_only(2021, cls.SECONDS_PER_TIMESTEP), config, DisplayConfig()
        )
        controller.controller_mode = mode
        return controller


class Boiler:
    """Input field names of the boiler controller, and the values every case shares."""

    C = GenericBoilerController

    #: The heating curve's supply temperature, the space-heating charge's target, in °C; its aim is 35.025 °C.
    SUPPLY_TEMPERATURE_IN_CELSIUS: ClassVar[float] = 35.0

    #: A hot-water tank at its 60 °C aim: no hot-water charge is due.
    TANK_AT_ITS_AIM: ClassVar[Dict[str, float]] = {
        GenericBoilerController.WaterTemperatureInputFromDHWStorage: 60.0,
        GenericBoilerController.WaterTemperatureAtEndOfStepFromDHWStorageInCelsius: 60.0,
    }


def test_the_boiler_controller_commands_the_ratio_that_ends_the_buffer_at_the_aim() -> None:
    """A buffer at 30 °C that a whole step ends at 38 °C, and at 26 °C without the boiler, gets 0.752 of the step.

    ``(35.025 − 26) / (38 − 26) = 0.752``, at full signal (the buffer lies the 5 K of full power below the supply
    temperature) and with the 5 K lift to the supply temperature; no remainder is commanded.
    """
    outputs = ControllerPass.outputs(
        ControllerPass.boiler_controller(HeatingMode.SPACE_HEATING),
        {
            **Boiler.TANK_AT_ITS_AIM,
            Boiler.C.WaterTemperatureInputFromWaterStorage: 30.0,
            Boiler.C.WaterTemperatureAtEndOfStepFromWaterStorageInCelsius: 38.0,
            Boiler.C.WaterTemperatureAtEndOfStepWithoutBoilerFromWaterStorageInCelsius: 26.0,
            Boiler.C.PartLoadRatioRunSh: 1.0,
            Boiler.C.SpaceHeatingSupplyTemperatureSetInCelsius: Boiler.SUPPLY_TEMPERATURE_IN_CELSIUS,
            Boiler.C.HeatingSeason: 1.0,
        },
    )
    assert outputs[Boiler.C.OperatingMode] == HeatingMode.SPACE_HEATING.value
    assert outputs[Boiler.C.PartLoadRatioSh] == pytest.approx(9.025 / 12.0)
    assert outputs[Boiler.C.TargetTemperatureShInCelsius] == Boiler.SUPPLY_TEMPERATURE_IN_CELSIUS
    assert outputs[Boiler.C.ControlSignalToGenericBoiler] == 1.0
    assert outputs[Boiler.C.TemperatureDelta] == pytest.approx(5.0)
    assert outputs[Boiler.C.ControlSignalForSpaceHeatingRemainder] == 0.0
    assert outputs[Boiler.C.TemperatureDeltaForSpaceHeatingRemainderInKelvin] == 0.0


def test_the_boiler_controller_keeps_a_steady_charge_inside_the_target_band() -> None:
    """A buffer at its 35 °C target that ends at 35.02 °C after a 0.4 charge keeps 0.4, at signal 1 and 0.1 K lift.

    35.02 °C lies inside the band from 35 to 35.05 °C, so the ratio run stays. The buffer starts at its target, so
    the controller's own lift is 0; a charge above the target asks for the 0.1 K lift that makes the boiler fire, at
    full signal, so the pump's largest flow sets the rise.
    """
    outputs = ControllerPass.outputs(
        ControllerPass.boiler_controller(HeatingMode.SPACE_HEATING),
        {
            **Boiler.TANK_AT_ITS_AIM,
            Boiler.C.WaterTemperatureInputFromWaterStorage: 35.0,
            Boiler.C.WaterTemperatureAtEndOfStepFromWaterStorageInCelsius: 35.02,
            Boiler.C.WaterTemperatureAtEndOfStepWithoutBoilerFromWaterStorageInCelsius: 33.0,
            Boiler.C.PartLoadRatioRunSh: 0.4,
            Boiler.C.SpaceHeatingSupplyTemperatureSetInCelsius: Boiler.SUPPLY_TEMPERATURE_IN_CELSIUS,
            Boiler.C.HeatingSeason: 1.0,
        },
    )
    assert outputs[Boiler.C.OperatingMode] == HeatingMode.SPACE_HEATING.value
    assert outputs[Boiler.C.PartLoadRatioSh] == 0.4
    assert outputs[Boiler.C.ControlSignalToGenericBoiler] == 1.0
    assert (
        outputs[Boiler.C.TemperatureDelta] == GenericBoilerController.ASKED_LIFT_OF_A_CHARGE_ABOVE_THE_TARGET_IN_KELVIN
    )


@pytest.mark.parametrize(
    ("hot_water_ratio_run", "expected_space_heating_ratio"),
    [
        (0.5, 0.25625),  # the raising ratio, 0.5 (35.025 − 34) / (36 − 34), fits into the 0.5 left
        (0.8, 0.2),  # the raising ratio does not fit: the 0.2 left of the step
    ],
)
def test_the_rest_of_a_hot_water_step_charges_the_buffer_within_the_budget(
    hot_water_ratio_run: float, expected_space_heating_ratio: float
) -> None:
    """A hot-water step whose charge ends at the tank's aim leaves 1 − r_hw of the step to the space-heating charge.

    The tank starts at 50 °C and ends at 60.03 °C, inside its band, so the hot-water ratio keeps its ratio run. The
    buffer starts at 30 °C; after a 0.5 charge it ends at 36 °C, at 34 °C without the boiler, so the raising ratio is
    ``0.5 (35.025 − 34) / (36 − 34) = 0.25625``, limited to the rest of the step. The remainder runs at signal 1 with
    the 5 K lift from the buffer's 30 °C to the 35 °C supply temperature.
    """
    outputs = ControllerPass.outputs(
        ControllerPass.boiler_controller(HeatingMode.DOMESTIC_HOT_WATER),
        {
            Boiler.C.WaterTemperatureInputFromDHWStorage: 50.0,
            Boiler.C.WaterTemperatureAtEndOfStepFromDHWStorageInCelsius: 60.03,
            Boiler.C.PartLoadRatioRunDhw: hot_water_ratio_run,
            Boiler.C.WaterTemperatureInputFromWaterStorage: 30.0,
            Boiler.C.WaterTemperatureAtEndOfStepFromWaterStorageInCelsius: 36.0,
            Boiler.C.WaterTemperatureAtEndOfStepWithoutBoilerFromWaterStorageInCelsius: 34.0,
            Boiler.C.PartLoadRatioRunSh: 0.5,
            Boiler.C.SpaceHeatingSupplyTemperatureSetInCelsius: Boiler.SUPPLY_TEMPERATURE_IN_CELSIUS,
            Boiler.C.HeatingSeason: 1.0,
        },
    )
    assert outputs[Boiler.C.OperatingMode] == HeatingMode.DOMESTIC_HOT_WATER.value
    assert outputs[Boiler.C.PartLoadRatioDhw] == hot_water_ratio_run
    assert outputs[Boiler.C.PartLoadRatioSh] == pytest.approx(expected_space_heating_ratio)
    assert outputs[Boiler.C.ControlSignalForSpaceHeatingRemainder] == 1.0
    assert outputs[Boiler.C.TemperatureDeltaForSpaceHeatingRemainderInKelvin] == pytest.approx(5.0)


def test_the_distribution_controller_cuts_the_draw_at_the_room_temperature() -> None:
    """A buffer that a whole step's draw ends at 12 °C, and at 30 °C without it, with the room at 20 °C, gets 0.556.

    ``(30 − 20) / (30 − 12) = 0.556``: the largest fraction that keeps the buffer at the room's temperature. The
    valve opens for heating on the building's positive demand in the heating season.
    """
    controller_class = heat_distribution_system.HeatDistributionController
    controller = controller_class(
        my_simulation_parameters=SimulationParameters.one_day_only(2021, ControllerPass.SECONDS_PER_TIMESTEP),
        config=heat_distribution_system.HeatDistributionControllerConfig(
            component_id=ComponentID(name="HeatDistributionController"), heating_load_of_building_in_watt=8000.0
        ),
    )
    outputs = ControllerPass.outputs(
        controller,
        {
            controller_class.TheoreticalThermalBuildingDemand: 3000.0,
            controller_class.HeatingSeason: 1.0,
            controller_class.TemperatureIndoorAirAtStartOfStepInCelsius: 20.0,
            controller_class.WaterTemperatureAtEndOfStepFromWaterStorageInCelsius: 12.0,
            controller_class.WaterTemperatureAtEndOfStepWithoutHeatDistributionFromWaterStorageInCelsius: 30.0,
            controller_class.PartLoadRatioRun: 1.0,
        },
    )
    assert outputs[controller_class.State] == 1
    assert outputs[controller_class.PartLoadRatio] == pytest.approx(10.0 / 18.0)
