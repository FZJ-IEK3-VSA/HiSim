"""After a forced step the heat pump's space-heating controller starts the next step in the mode it held.

The simulator restores every component before each pass and, after its free passes, forces the step: the controller
then holds its outputs, so the heat pump runs the ``State_SH`` of the last free pass. The controller must carry that
mode into the next step. When the simulator restored the controller in the forced passes too, its mode went back to
the start of the step, the next step started from the other mode and skipped the hysteresis band
(hisim-component-physics-4g9.28). Since the owner's decision of 2026-10-23 the simulator does not restore a controller
in a forced pass: a forced controller holds its last free pass, its outputs and its state.
"""

from pathlib import Path
from typing import ClassVar, Dict, List, Tuple

import pytest
import yaml

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.calculation_scope import CalculationScope
from hisim.components.heat_distribution_system import HeatDistributionSystemType
from hisim.components.more_advanced_heat_pump_hplib import (
    MoreAdvancedHeatPumpHPLibControllerSpaceHeating,
    MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig,
)
from hisim.config import ComponentID, SizingContext
from hisim.energy_system.executor import SimulationParametersReader, build_energy_system
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


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
        """Run one pass as the simulator does, and return the ``State_SH`` it publishes.

        A free pass restores the controller first; a forced pass does not, since the simulator does not restore a
        controller in a forced pass.

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
        if not forced:
            self.controller.i_restore_state()
        self.controller.i_simulate(timestep=0, stsv=self.stsv, force_convergence=forced)
        return float(self.stsv.values[self.controller.state_channel.global_index])


@pytest.mark.base
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
    assert passes.controller.previous_heatpump_mode == {1.0: "heating", 0.0: "off"}[held_state]


class HeatPumpTwinDays:
    """Four January days of the heat-pump twin at 900 s, with each step's committed and published space-heating mode.

    The twin runs with the predefined occupancy profile, so the test needs no load profile generator. On main before
    the simulator rule of 2026-10-23, these days had 21 forced steps, and in 4 of them (one each day, near 17:00) the
    last free pass switched heating off while the step started heating; each committed "heating".
    """

    #: The recorded heat-pump twin.
    TWIN: ClassVar[str] = "household_heatpump_building_sizer"
    #: The repository root, from which the twin's file is read.
    ROOT: ClassVar[Path] = Path(__file__).resolve().parents[1]
    #: The space-heating mode the controller keeps, as the ``State_SH`` value it publishes.
    STATE_OF_MODE: ClassVar[Dict[str, float]] = {"heating": 1.0, "cooling": -1.0, "off": 0.0}

    @classmethod
    def run(cls, work: Path) -> List[Tuple[bool, str, str, float]]:
        """Run the four days and return one record per step.

        Args:
            work: The directory the run writes to.

        Returns:
            Per step: whether it was forced, the mode it started from, the mode it committed and the ``State_SH`` it
            published.
        """
        text = (cls.ROOT / "energy_systems" / f"{cls.TWIN}.energy_system.yaml").read_text(encoding="utf-8")
        energy_system = work / f"{cls.TWIN}.energy_system.yaml"
        energy_system.write_text(text.replace("USE_LOCAL_LPG", "USE_PREDEFINED_PROFILE"), encoding="utf-8")
        values = {
            "start_date": "2021-01-25T00:00:00",
            "end_date": "2021-01-29T00:00:00",
            "seconds_per_timestep": 900,
            "country": "DE",
            "logging_level": 3,
            "post_processing_options": [],
        }
        path = work / "days.simulation.yaml"
        path.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
        results_directory = work / "results"
        records: List[Tuple[bool, str, str, float]] = []
        with CalculationScope.open(label=str(energy_system), run_directory=str(results_directory)):
            simulation_parameters = SimulationParametersReader.read(path)
            simulation_parameters.result_directory = str(results_directory)
            simulator = build_energy_system(energy_system, simulation_parameters).simulator
            (controller,) = [
                wrapped.my_component
                for wrapped in simulator.wrapped_components
                if isinstance(wrapped.my_component, MoreAdvancedHeatPumpHPLibControllerSpaceHeating)
            ]
            process_one_timestep = simulator.process_one_timestep

            def recording(step, stsv):  # the committed mode against the published State_SH, per step
                result = process_one_timestep(step, stsv)
                records.append(
                    (
                        bool(result[2]),
                        controller.previous_heatpump_mode,
                        controller.controller_heatpumpmode,
                        float(result[0].values[controller.state_channel.global_index]),
                    )
                )
                return result

            setattr(simulator, "process_one_timestep", recording)
            simulator.run_all_timesteps()
        return records


@pytest.mark.extendedbase
def test_every_step_of_the_heat_pump_twin_commits_the_space_heating_mode_it_published(tmp_path: Path) -> None:
    """Catches a forced step that commits the start-of-step mode while ``State_SH`` held the last free pass's mode.

    The reproduction of hisim-component-physics-4g9.28 on the twin (:class:`HeatPumpTwinDays`): forced steps whose
    last free pass switched heating off committed "heating", so the next step restarted the heat pump inside the
    hysteresis band. Every step must commit the mode it published, and the days must contain a forced step whose
    published mode differs from the mode it started from, or the test no longer tells the two behaviours apart.
    """
    records = HeatPumpTwinDays.run(tmp_path)

    mismatches = [
        step
        for step, (_, _, committed, published) in enumerate(records)
        if HeatPumpTwinDays.STATE_OF_MODE[committed] != published
    ]
    assert mismatches == []
    switched_in_forced_steps = [
        step
        for step, (forced, start, _, published) in enumerate(records)
        if forced and HeatPumpTwinDays.STATE_OF_MODE[start] != published
    ]
    assert switched_in_forced_steps
