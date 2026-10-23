"""A system whose heat input is served by another of its components but left unconnected is refused before it runs.

A component's heat input (load type ``HEATING``) is *served* when the system holds a component whose class the
input's default connections name. The building's ``ThermalPowerDelivered`` is optional, so the mandatory-input check
lets an unconnected one pass; ``basic_household_only_heating`` ran that way, its boiler heating a buffer whose heat
reached no room while the room air fell to 0.7 °C (hisim-only-heating-house-unheated-6ds9). The simulator now refuses
such a system when it connects the components (:class:`hisim.component.ServedHeatInputs`).
"""

from pathlib import Path
from typing import ClassVar, List

import pytest
import yaml

from hisim import loadtypes as lt
from hisim.component import (
    Component,
    ComponentConnection,
    ComponentInput,
    ComponentOutput,
    LostHeatError,
    PhysicsComponent,
    ServedHeatInputs,
    SingleTimeStepValues,
)
from hisim.config import ComponentID, ConfigBase, DisplayConfig
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator

pytestmark = pytest.mark.base


class HeatProbeConfig(ConfigBase):
    """The configuration of the probes below: their identity only."""

    @classmethod
    def get_main_classname(cls) -> str:
        """Name no component class; the probes are test doubles."""
        return "tests.test_served_heat_inputs.HeaterProbe"


class HeatProbe(PhysicsComponent):
    """A component with no behaviour, the base of the heater and the room below."""

    MODELS_NO_DEVICE: ClassVar[bool] = True

    def __init__(self, my_simulation_parameters: SimulationParameters, name: str) -> None:
        """Build the probe with its identity and no ports."""
        super().__init__(
            name=name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=HeatProbeConfig(component_id=ComponentID(name=name)),
            my_display_config=DisplayConfig(),
        )

    def i_save_state(self) -> None:
        """Save nothing; the probe has no state."""

    def i_restore_state(self) -> None:
        """Restore nothing; the probe has no state."""

    def i_prepare_simulation(self) -> None:
        """Prepare nothing."""

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Check nothing."""

    def write_to_report(self) -> List[str]:
        """Return no report lines."""
        return []

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Do nothing; the tests below never run a time step."""


class HeaterProbe(HeatProbe):
    """A heater that publishes space-heating heat in W, like a heat distribution system."""

    #: The heat output.
    ThermalPowerInWatt: ClassVar[str] = "ThermalPowerInWatt"

    def __init__(self, my_simulation_parameters: SimulationParameters, name: str) -> None:
        """Build the heater with its heat output."""
        super().__init__(my_simulation_parameters, name)
        self.heat_output: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPowerInWatt,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description="The heat the heater delivers.",
        )


class RoomProbe(HeatProbe):
    """A room whose optional heat input is served by a :class:`HeaterProbe` by default, like a building."""

    #: The heat input.
    ThermalPowerDeliveredInWatt: ClassVar[str] = "ThermalPowerDeliveredInWatt"

    #: The room air temperature, the room's one output.
    TemperatureInCelsius: ClassVar[str] = "TemperatureInCelsius"

    def __init__(self, my_simulation_parameters: SimulationParameters, name: str) -> None:
        """Build the room with its optional heat input, its temperature and the default connection from a heater."""
        super().__init__(my_simulation_parameters, name)
        self.temperature_output: ComponentOutput = self.add_output(
            self.component_name,
            self.TemperatureInCelsius,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            output_description="The room air temperature (every component publishes at least one output).",
        )
        self.heat_input: ComponentInput = self.add_input(
            self.component_name, self.ThermalPowerDeliveredInWatt, lt.LoadTypes.HEATING, lt.Units.WATT, False
        )
        self.add_default_connections(
            [
                ComponentConnection(
                    self.ThermalPowerDeliveredInWatt, HeaterProbe.get_classname(), HeaterProbe.ThermalPowerInWatt
                )
            ]
        )


def probe_simulator(tmp_path: Path, *components: Component) -> Simulator:
    """Return a simulator of one 900 s day, writing only below ``tmp_path``, that holds the given components."""
    parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=900)
    parameters.result_directory = str(tmp_path / "results")
    simulator: Simulator = Simulator(
        module_directory=str(tmp_path), module_filename="heat", my_simulation_parameters=parameters
    )
    simulator.set_simulation_parameters(parameters)
    for component in components:
        simulator.add_component(component)
    return simulator


def test_a_served_heat_input_left_unconnected_is_refused_when_the_components_are_connected(tmp_path: Path) -> None:
    """Catches a system whose heater heats nothing because the room does not read it, as the only-heating house did."""
    parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=900)
    simulator = probe_simulator(tmp_path, HeaterProbe(parameters, "Heater"), RoomProbe(parameters, "Room"))
    with pytest.raises(LostHeatError) as refusal:
        simulator.connect_all_components()
    assert str(refusal.value).startswith(
        "Room.ThermalPowerDeliveredInWatt is not connected, although Heater (HeaterProbe.ThermalPowerInWatt) serves "
        "it by default: the heat would never arrive."
    )


def test_a_connected_heat_input_and_one_nothing_serves_are_accepted(tmp_path: Path) -> None:
    """Catches the refusal reaching a room that reads its heater, or one with no heater beside it (nothing is lost)."""
    parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=900)
    heater, room = HeaterProbe(parameters, "Heater"), RoomProbe(parameters, "Room")
    room.connect_only_predefined_connections(heater)
    probe_simulator(tmp_path, heater, room).connect_all_components()
    probe_simulator(tmp_path / "alone", RoomProbe(parameters, "Room")).connect_all_components()
    assert not ServedHeatInputs.lost({"Room": RoomProbe(parameters, "Room")})


def test_the_only_heating_houses_twin_reads_the_distribution_systems_heat() -> None:
    """Catches the only-heating house's twin dropping the building's heat input again.

    The bead is hisim-only-heating-house-unheated-6ds9. The simulator refuses either file if it loses the heat, but only when it runs; the twin is read here, without a
    run, so that a re-recording that drops the line fails among the fast tests.
    """
    twin = Path(__file__).resolve().parent.parent / "energy_systems" / "basic_household_only_heating.energy_system.yaml"
    components = yaml.safe_load(twin.read_text(encoding="utf-8"))["components"]
    assert "HeatDistributionSystem" in components["Building"]["inputs"]
