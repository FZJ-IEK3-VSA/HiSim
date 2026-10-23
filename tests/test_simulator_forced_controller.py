"""A forced controller holds its last free pass: its outputs and its state.

The simulator saves every component's state at the start of a step and restores it before every pass. After 12 free
passes it forces the step (``force_convergence``): a controller then republishes the outputs of its last free pass and
decides nothing. The simulator does not restore a controller (kind L1 or L2) in a forced pass, so the step commits the
controller's state of its last free pass, the state that belongs to the outputs it held; physics is still restored in
every pass (owner decision of 2026-10-23, bead hisim-component-physics-4g9.28).

The rig is a loop that never settles. ``FlipController`` commands 1 when the plant's signal is 0 and 0 otherwise, and
keeps the command as its state, so its state changes in every free pass. ``EchoPlant`` publishes the command it
receives as its signal and adds it to an energy it restores in every pass. A forced pass holds the command, the plant
echoes the same value and the step converges.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, List, Tuple, Type

import pytest
from dataclasses_json import dataclass_json

from hisim import loadtypes as lt
from hisim.component import (
    Component,
    ComponentInput,
    ComponentOutput,
    L1ControllerComponent,
    L2ControllerComponent,
    PhysicsComponent,
    SingleTimeStepValues,
)
from hisim.config import ComponentID, ConfigBase, DisplayConfig
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator

pytestmark = pytest.mark.base


@dataclass_json
@dataclass
class FlipLoopStubConfig(ConfigBase):
    """Configuration of the controller and plant stubs of the loop: only their identity."""

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns the full class name of the L1 controller stub; the config belongs to a test double."""
        return FlipL1Controller.get_full_classname()

    component_id: ComponentID


class EventLog:
    """The calls the simulator made on one stub, in order, as (time step, event, value) tuples.

    The event is ``"save"``, ``"restore"`` or ``"simulate"``. A simulate records its force flag, a save the state it
    caches, a restore None.
    """

    def __init__(self) -> None:
        """Start an empty log."""
        self.events: List[Tuple[int, str, object]] = []
        self.timestep: int = 0

    def record(self, event: str, value: object = None) -> None:
        """Append one event at the current time step.

        Args:
            event: ``"save"``, ``"restore"`` or ``"simulate"``.
            value: The force flag of a simulate call, the cached state of a save, None for a restore.
        """
        self.events.append((self.timestep, event, value))

    def simulates_restored_first(self, timestep: int) -> List[Tuple[bool, bool]]:
        """Return, for every simulate call of a step, its force flag and whether a restore came right before it.

        Args:
            timestep: The step to look at.

        Returns:
            One (forced, restored first) pair per pass, in pass order.
        """
        step_events = [event for event in self.events if event[0] == timestep]
        return [
            (bool(force), index > 0 and step_events[index - 1][1] == "restore")
            for index, (_, event, force) in enumerate(step_events)
            if event == "simulate"
        ]


class FlipController(Component):
    """Commands the opposite of the plant's signal and keeps the command as its state.

    Its state is ``command``, saved at the start of the step and restored in every pass. A free pass sets it to 1 if
    the plant's signal is 0 and to 0 otherwise, and publishes it; a forced pass changes nothing and publishes nothing,
    so the output keeps the value of the last free pass. ``decisions`` lists the command of every free pass.

    This class has no kind; :class:`FlipL1Controller` and :class:`FlipL2Controller` add one.
    """

    MODELS_NO_DEVICE: ClassVar[bool] = True

    #: The input connected to the plant's signal.
    Signal: ClassVar[str] = "Signal"
    #: The command the controller publishes.
    Command: ClassVar[str] = "Command"

    def __init__(self, my_simulation_parameters: SimulationParameters, config: FlipLoopStubConfig) -> None:
        """Build the controller, which starts with command 1, and declare its input and output.

        Args:
            my_simulation_parameters: Simulation parameters of the run.
            config: The controller's identity.
        """
        self.config: FlipLoopStubConfig = config
        super().__init__(
            name=config.component_id.key,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=DisplayConfig(),
        )
        self.signal_input: ComponentInput = self.add_input(
            self.component_name, self.Signal, lt.LoadTypes.ANY, lt.Units.ANY, mandatory=True
        )
        self.command_output: ComponentOutput = self.add_output(
            self.component_name, self.Command, lt.LoadTypes.ANY, lt.Units.ANY, output_description="The command."
        )
        self.command: int = 1
        self.command_at_start_of_step: int = 1
        self.decisions: List[Tuple[int, int]] = []
        self.log = EventLog()

    def i_save_state(self) -> None:
        """Save the command the step starts from."""
        self.log.record("save", self.command)
        self.command_at_start_of_step = self.command

    def i_restore_state(self) -> None:
        """Reset the command to the start of the step."""
        self.log.record("restore")
        self.command = self.command_at_start_of_step

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation; nothing to prepare."""

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Checks nothing."""

    def write_to_report(self) -> List[str]:
        """Returns no report lines."""
        return []

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """In a free pass, command the opposite of the plant's signal; in a forced pass, hold."""
        self.log.timestep = timestep
        self.log.record("simulate", force_convergence)
        if force_convergence:
            return
        self.command = 1 if stsv.get_input_value(self.signal_input) == 0 else 0
        self.decisions.append((timestep, self.command))
        stsv.set_output_value(self.command_output, self.command)


class FlipL1Controller(FlipController, L1ControllerComponent):
    """The flip controller as an L1 controller."""


class FlipL2Controller(FlipController, L2ControllerComponent):
    """The flip controller as an L2 controller."""


class EchoPlant(PhysicsComponent):
    """Publishes the command it receives as its signal and adds it to an energy that it restores in every pass.

    ``energy`` is saved at the start of the step and restored in every pass, forced ones included, so after a step it
    is the energy of the step before plus the command of the last pass, never two commands.
    """

    MODELS_NO_DEVICE: ClassVar[bool] = True

    #: The input connected to the controller's command.
    Command: ClassVar[str] = "Command"
    #: The signal the plant publishes.
    Signal: ClassVar[str] = "Signal"

    def __init__(self, my_simulation_parameters: SimulationParameters, config: FlipLoopStubConfig) -> None:
        """Build the plant with zero energy, and declare its input and output.

        Args:
            my_simulation_parameters: Simulation parameters of the run.
            config: The plant's identity.
        """
        self.config: FlipLoopStubConfig = config
        super().__init__(
            name=config.component_id.key,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=DisplayConfig(),
        )
        self.command_input: ComponentInput = self.add_input(
            self.component_name, self.Command, lt.LoadTypes.ANY, lt.Units.ANY, mandatory=True
        )
        self.signal_output: ComponentOutput = self.add_output(
            self.component_name, self.Signal, lt.LoadTypes.ANY, lt.Units.ANY, output_description="The signal."
        )
        self.energy: float = 0.0
        self.energy_at_start_of_step: float = 0.0
        self.log = EventLog()

    def i_save_state(self) -> None:
        """Save the energy the step starts from."""
        self.log.record("save")
        self.energy_at_start_of_step = self.energy

    def i_restore_state(self) -> None:
        """Reset the energy to the start of the step."""
        self.log.record("restore")
        self.energy = self.energy_at_start_of_step

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation; nothing to prepare."""

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Checks nothing."""

    def write_to_report(self) -> List[str]:
        """Returns no report lines."""
        return []

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Echo the command as the signal and add it to the energy."""
        self.log.timestep = timestep
        self.log.record("simulate", force_convergence)
        command = stsv.get_input_value(self.command_input)
        self.energy += command
        stsv.set_output_value(self.signal_output, command)


class FlipLoop:
    """A simulator of one day at 900 s with the flip controller and the echo plant, prepared and wired."""

    def __init__(self, tmp_path: Path, controller_class: Type[FlipController]) -> None:
        """Build, add and connect the two stubs.

        Args:
            tmp_path: Directory for the simulator's module directory and results.
            controller_class: The flip controller of the kind under test.
        """
        parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=900)
        parameters.result_directory = str(tmp_path / "results")
        self.simulator = Simulator(
            module_directory=str(tmp_path), module_filename="forced_controller", my_simulation_parameters=parameters
        )
        self.simulator.set_simulation_parameters(parameters)
        self.controller = controller_class(parameters, FlipLoopStubConfig(component_id=ComponentID(name="Controller")))
        self.plant = EchoPlant(parameters, FlipLoopStubConfig(component_id=ComponentID(name="Plant")))
        self.controller.connect_input(FlipController.Signal, self.plant.component_name, EchoPlant.Signal)
        self.plant.connect_input(EchoPlant.Command, self.controller.component_name, FlipController.Command)
        self.simulator.add_component(self.controller)
        self.simulator.add_component(self.plant)
        self.simulator.prepare_calculation()
        self.simulator.connect_all_components()
        self.stsv = SingleTimeStepValues(len(self.simulator.all_outputs))

    def step(self, timestep: int) -> Tuple[SingleTimeStepValues, int, bool]:
        """Run one step from the values the previous one converged to.

        Args:
            timestep: The step to run.

        Returns:
            The converged values, the number of passes and whether the step was forced.
        """
        self.controller.log.timestep = self.plant.log.timestep = timestep
        self.stsv, passes, forced = self.simulator.process_one_timestep(timestep, self.stsv)
        return self.stsv, passes, forced


@pytest.mark.parametrize("controller_class", [FlipL1Controller, FlipL2Controller])
class TestAForcedControllerHoldsItsLastFreePass:
    """The flip loop needs a forced step; the controller commits the state of its last free pass."""

    def test_the_step_commits_the_state_of_the_last_free_pass(
        self, tmp_path: Path, controller_class: Type[FlipController]
    ) -> None:
        """Catches a forced pass that restores the controller, so the step commits its start-of-step command.

        The controller starts with command 1. Its free passes alternate 1, 0, 1, ...; the twelfth, the last free one,
        commands 0, which the held output shows. The step must commit 0, and the next step must start from it.
        """
        loop = FlipLoop(tmp_path, controller_class)

        stsv, _, forced = loop.step(0)

        assert forced
        free_decisions = [command for timestep, command in loop.controller.decisions if timestep == 0]
        assert len(free_decisions) == 12
        assert all(earlier != later for earlier, later in zip(free_decisions, free_decisions[1:]))
        last_free_decision = free_decisions[-1]
        assert last_free_decision != loop.controller.command_at_start_of_step
        assert stsv.get_input_value(loop.plant.command_input) == last_free_decision
        assert loop.controller.command == last_free_decision

        loop.step(1)

        assert (1, "save", last_free_decision) in loop.controller.log.events

    def test_a_forced_controller_is_not_restored_and_physics_is(
        self, tmp_path: Path, controller_class: Type[FlipController]
    ) -> None:
        """Catches a simulator that restores a controller in a forced pass, or that stops restoring physics there."""
        loop = FlipLoop(tmp_path, controller_class)

        _, passes, _ = loop.step(0)

        controller_passes = loop.controller.log.simulates_restored_first(0)
        plant_passes = loop.plant.log.simulates_restored_first(0)
        assert len(controller_passes) == len(plant_passes) == passes
        assert [restored for forced, restored in controller_passes] == [not forced for forced, _ in controller_passes]
        assert any(forced for forced, _ in controller_passes)
        assert all(restored for _, restored in plant_passes)
        # restored in every pass, the plant's energy holds one command, the last one, not one per forced pass
        assert loop.plant.energy == loop.controller.command
