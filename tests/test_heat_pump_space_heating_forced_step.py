"""After a forced step the heat pump's space-heating controller starts the next step in the mode it held.

The simulator restores every component before each pass and, after its free passes, forces the step: the controller
then holds its outputs, so the heat pump runs the ``State_SH`` of the last free pass. The controller must carry that
mode into the next step. When the restore reset its mode to the start of the step instead, the next step started
from the other mode and skipped the hysteresis band (hisim-component-physics-4g9.28).
"""

from typing import ClassVar, Dict, List

import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components.heat_distribution_system import HeatDistributionSystemType
from hisim.components.more_advanced_heat_pump_hplib import (
    MoreAdvancedHeatPumpHPLibControllerSpaceHeating,
    MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig,
)
from hisim.config import ComponentID, SizingContext
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft

pytestmark = pytest.mark.base


class SpaceHeatingControllerPasses:
    """A floor-heating space-heating controller at 900 s whose inputs are fake outputs, run pass by pass.

    The supply temperature is 35 °C and the band's offsets are the preset's 5 K, so without a raise the controller
    switches heating on below 30 °C and off above 40 °C; the energy manager's 5 K raise moves both points up by 5 K.
    """

    #: The inputs every pass sees unless a pass sets them: a 35 °C supply temperature in the heating season.
    VALUES: ClassVar[Dict[str, float]] = {
        MoreAdvancedHeatPumpHPLibControllerSpaceHeating.SpaceHeatingSupplyTemperatureSetInCelsius: 35.0,
        MoreAdvancedHeatPumpHPLibControllerSpaceHeating.HeatingSeason: 1.0,
        MoreAdvancedHeatPumpHPLibControllerSpaceHeating.DailyAverageOutsideTemperature: 0.0,
        MoreAdvancedHeatPumpHPLibControllerSpaceHeating.PartLoadRatioRunSh: 1.0,
    }

    def __init__(self) -> None:
        """Build the controller, which starts off, and wire every input to a fake output."""
        config = MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig.preset_standard("SH").resolve(
            SizingContext(
                set_heating_temperature_in_celsius=21.0,
                heat_distribution_system_type=HeatDistributionSystemType.FLOORHEATING,
            )
        )
        self.controller = MoreAdvancedHeatPumpHPLibControllerSpaceHeating(
            config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, 900)
        )
        self.fakes: Dict[str, cp.ComponentOutput] = {}
        for component_input in self.controller.inputs:
            fake = cp.ComponentOutput(
                "Fake", component_input.field_name, lt.LoadTypes.ANY, lt.Units.ANY, component_id=ComponentID("Fake")
            )
            component_input.source_output = fake
            self.fakes[component_input.field_name] = fake
        components: List[object] = [*self.fakes.values(), self.controller]
        fft.add_global_index_of_components(components)
        self.stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs(components))

    def one_pass(self, *, buffer_in_celsius: float, raise_in_kelvin: float, forced: bool = False) -> float:
        """Run one pass as the simulator does, restore first, and return the ``State_SH`` it publishes.

        Args:
            buffer_in_celsius: The buffer's start-of-step temperature, in °C.
            raise_in_kelvin: The energy manager's raise of the buffer, in K.
            forced: Whether the simulator forces the pass.

        Returns:
            The published state: 1 heating, 0 off.
        """
        controller_class = MoreAdvancedHeatPumpHPLibControllerSpaceHeating
        values = {
            **self.VALUES,
            controller_class.WaterTemperatureInput: buffer_in_celsius,
            controller_class.SimpleHotWaterStorageTemperatureModifier: raise_in_kelvin,
            controller_class.WaterTemperatureAtEndOfStepFromWaterStorageInCelsius: buffer_in_celsius,
            controller_class.WaterTemperatureAtEndOfStepWithoutHeatPumpFromWaterStorageInCelsius: buffer_in_celsius,
        }
        for field_name, fake in self.fakes.items():
            self.stsv.values[fake.global_index] = values.get(field_name, 0.0)
        self.controller.i_restore_state()
        self.controller.i_simulate(timestep=0, stsv=self.stsv, force_convergence=forced)
        return float(self.stsv.values[self.controller.state_channel.global_index])


@pytest.mark.parametrize(
    "start_of_step_mode, buffer_in_celsius, held_state",
    [
        # off at 32 °C: the raise puts the switch-on point at 35 °C, so the raised passes switch heating on
        ("off", 32.0, 1.0),
        # heating at 42 °C: without the raise the switch-off point is 40 °C, so the unraised passes switch it off
        ("heating", 42.0, 0.0),
    ],
)
def test_after_a_forced_step_the_next_step_starts_in_the_mode_the_controller_held(
    start_of_step_mode: str, buffer_in_celsius: float, held_state: float
) -> None:
    """Catches a forced step that commits the start-of-step mode while ``State_SH`` held the last free pass's mode.

    The energy manager's raise alternates over the free passes, as in its on/off loop at the heat pump's own draw,
    so the controller's decision alternates with it; the last free pass decides the opposite of the start mode, and
    a forced pass with the other raise holds it. The next step's buffer, 33 °C without a raise, lies inside the band
    (30-40 °C), where the controller keeps the mode it starts with: it must be the held one, the mode the heat pump
    ran.
    """
    raise_of_held_decision_in_kelvin = 5.0 if held_state == 1.0 else 0.0
    other_raise_in_kelvin = 5.0 - raise_of_held_decision_in_kelvin
    passes = SpaceHeatingControllerPasses()
    passes.controller.i_save_state()
    if start_of_step_mode == "heating":
        # a step with the buffer at 25 °C, below the switch-on point, ends heating
        assert passes.one_pass(buffer_in_celsius=25.0, raise_in_kelvin=0.0) == 1.0
        passes.controller.i_save_state()
    for _ in range(6):
        published_without = passes.one_pass(buffer_in_celsius=buffer_in_celsius, raise_in_kelvin=other_raise_in_kelvin)
        assert published_without == 1.0 - held_state
        published_with = passes.one_pass(
            buffer_in_celsius=buffer_in_celsius, raise_in_kelvin=raise_of_held_decision_in_kelvin
        )
        assert published_with == held_state
    published_forced = passes.one_pass(
        buffer_in_celsius=buffer_in_celsius, raise_in_kelvin=other_raise_in_kelvin, forced=True
    )
    assert published_forced == held_state
    passes.controller.i_save_state()

    assert passes.one_pass(buffer_in_celsius=33.0, raise_in_kelvin=0.0) == held_state
    assert passes.controller.start_of_step_mode == {1.0: "heating", 0.0: "off"}[held_state]
