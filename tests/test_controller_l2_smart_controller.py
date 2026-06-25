"""Test for controller l2 smart controller."""

from typing import List

import pytest
from hisim.component import ComponentInput, ComponentOutput, SingleTimeStepValues
from hisim.components import controller_l2_smart_controller
from hisim.components.controller_l2_smart_controller import SmartController, SmartControllerConfig
from hisim.components.generic_heat_pump import GenericHeatPumpController
from hisim.simulationparameters import SimulationParameters
import hisim.loadtypes as lt


@pytest.mark.base
def test_smart_controller_default_config_name() -> None:
    """Test that the default SmartController config name has no leading space."""
    # Get default config
    default_config = controller_l2_smart_controller.SmartControllerConfig.get_default_config_ems()

    # Verify the name does not have a leading space
    assert not default_config.name.startswith(" "), (
        f"SmartController default name has a leading space: '{default_config.name}'"
    )

    # Verify the expected name
    assert default_config.name == "SmartController", (
        f"SmartController default name should be 'SmartController', got '{default_config.name}'"
    )


@pytest.mark.base
def test_smart_controller_default_config_building_name() -> None:
    """Test that the default SmartController config building_name is correct."""
    default_config = controller_l2_smart_controller.SmartControllerConfig.get_default_config_ems()
    assert default_config.building_name == "BUI1"


@pytest.mark.base
def test_smart_controller_custom_config() -> None:
    """Test that SmartController can be configured with custom values."""
    custom_name = "MyCustomController"
    custom_building = "MyBuilding"

    config = controller_l2_smart_controller.SmartControllerConfig(
        building_name=custom_building,
        name=custom_name,
    )

    assert config.name == custom_name
    assert config.building_name == custom_building


class _FakeWrappedController:
    """Lightweight stand-in for a wrapped controller.

    Mimics the small surface area that :class:`SmartController` relies on
    (``inputs``/``outputs`` for ``add_io`` and the ``i_save_state``/
    ``i_restore_state``/``i_simulate`` lifecycle hooks) without pulling in the
    real heat-pump or EV-charger controllers and their dependencies.
    """

    ElectricityInput = "ElectricityInput"
    ELECTRICITY_INPUT = "ElectricityInput"

    def __init__(self, name: str) -> None:
        self.component_name = name
        self.field_name = name
        self.inputs: List[ComponentInput] = [
            ComponentInput(name, "ElectricityInput", lt.LoadTypes.ELECTRICITY, lt.Units.WATT, True)
        ]
        self.outputs: List[ComponentOutput] = [
            ComponentOutput(
                name,
                "State",
                lt.LoadTypes.ANY,
                lt.Units.ANY,
                None,
                None,
                "fake state output",
            )
        ]
        self.saved = False
        self.restored = False
        self.simulated = 0

    def i_save_state(self) -> None:
        self.saved = True

    def i_restore_state(self) -> None:
        self.restored = True

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        self.simulated += 1


def _make_simulation_parameters() -> SimulationParameters:
    """Return a small, cheap SimulationParameters instance for tests."""
    return SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)


@pytest.mark.base
def test_smart_controller_accepts_injected_wrapped_controllers() -> None:
    """SmartController must accept injected wrapped controllers and skip internal build."""
    sp = _make_simulation_parameters()
    config = SmartControllerConfig.get_default_config_ems()

    fake_hp = _FakeWrappedController("FakeHeatPump")
    fake_ev = _FakeWrappedController("FakeEVCharger")

    controller = SmartController(
        my_simulation_parameters=sp,
        controllers=None,
        config=config,
        wrapped_controllers=[fake_hp, fake_ev],
    )

    # The injected controllers are used verbatim, no internal construction happened.
    assert controller.wrapped_controllers == [fake_hp, fake_ev]
    assert all(
        not isinstance(c, (GenericHeatPumpController,))
        for c in controller.wrapped_controllers
    )


@pytest.mark.base
def test_smart_controller_add_io_aggregates_injected_inputs_and_outputs() -> None:
    """add_io must aggregate the inputs/outputs of the injected controllers."""
    sp = _make_simulation_parameters()
    config = SmartControllerConfig.get_default_config_ems()

    fake_hp = _FakeWrappedController("FakeHeatPump")
    fake_ev = _FakeWrappedController("FakeEVCharger")

    controller = SmartController(
        my_simulation_parameters=sp,
        controllers=None,
        config=config,
        wrapped_controllers=[fake_hp, fake_ev],
    )

    assert len(controller.inputs) == 2
    assert len(controller.outputs) == 2
    input_field_names = [inp.field_name for inp in controller.inputs]
    output_field_names = [outp.field_name for outp in controller.outputs]
    assert input_field_names == ["ElectricityInput", "ElectricityInput"]
    assert output_field_names == ["State", "State"]


@pytest.mark.base
def test_smart_controller_delegates_lifecycle_to_injected_controllers() -> None:
    """i_save_state / i_restore_state / i_simulate delegate to the injected controllers."""
    sp = _make_simulation_parameters()
    config = SmartControllerConfig.get_default_config_ems()

    fake_hp = _FakeWrappedController("FakeHeatPump")
    fake_ev = _FakeWrappedController("FakeEVCharger")

    controller = SmartController(
        my_simulation_parameters=sp,
        controllers=None,
        config=config,
        wrapped_controllers=[fake_hp, fake_ev],
    )

    controller.i_save_state()
    assert fake_hp.saved and fake_ev.saved

    controller.i_restore_state()
    assert fake_hp.restored and fake_ev.restored

    stsv = SingleTimeStepValues(number_of_values=len(controller.inputs) + len(controller.outputs))
    controller.i_simulate(timestep=0, stsv=stsv, force_convergence=False)
    assert fake_hp.simulated == 1
    assert fake_ev.simulated == 1


@pytest.mark.base
def test_smart_controller_default_path_still_builds_real_controllers() -> None:
    """When wrapped_controllers is None the original construction path is preserved."""
    sp = _make_simulation_parameters()
    config = SmartControllerConfig.get_default_config_ems()

    # Only request the heat pump so we avoid the (separately broken) EV charger path.
    controller = SmartController(
        my_simulation_parameters=sp,
        controllers={"HeatPump": ["mode"]},
        config=config,
    )

    assert len(controller.wrapped_controllers) == 1
    assert isinstance(controller.wrapped_controllers[0], GenericHeatPumpController)
