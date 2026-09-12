"""Tests for the PTXController display-config default and channel/config naming.

``PTXController.__init__`` previously used ``DisplayConfig()`` as a default
argument value, which is the classic Python mutable-default anti-pattern: the
same ``DisplayConfig`` instance would be shared across every call that omits
``my_display_config``. These tests verify that omitting the argument now yields
an independent ``DisplayConfig`` per instance (per the isolation guarantees in
KB-3284).

A second group of tests guards the clarity renames requested in issue #1534:
the output-channel constants ``PowerToBattery``/``EnergyToBattery`` (replacing
the misleading ``PowerToThird``/``EnergyToThird``) and the instance attribute
``ptx_controller_config`` (replacing ``ptxcontrollerconfig``).
"""

# clean

import dataclasses
import json
from typing import Any

import pytest

from hisim.config import ComponentID, DisplayConfig
from hisim.components.controller_l2_ptx_energy_management_system import (
    PtxOperationMode,
    PTXController,
    PTXControllerConfig,
)
from hisim.simulationparameters import SimulationParameters


def _make_config() -> PTXControllerConfig:
    """Return a minimal, valid ``PTXControllerConfig`` for instantiation."""
    return PTXControllerConfig(
        component_id=ComponentID(name="L2PtXController"),
        nom_load=100.0,
        min_load=20.0,
        max_load=100.0,
        standby_load=10.0,
        operation_mode=PtxOperationMode.NOMINAL_LOAD,
    )


def _make_sim_params() -> SimulationParameters:
    return SimulationParameters.full_year(year=2021, seconds_per_timestep=60)


@pytest.mark.base
def test_default_display_config_is_independent_per_instance() -> None:
    """Two default-constructed controllers must not share a DisplayConfig."""
    config = _make_config()
    sim_params = _make_sim_params()

    first = PTXController(my_simulation_parameters=sim_params, config=config)
    second = PTXController(my_simulation_parameters=sim_params, config=config)

    # Per KB-3284: rely on identity, not equality, to catch shared references.
    assert first.my_display_config is not second.my_display_config
    assert isinstance(first.my_display_config, DisplayConfig)
    assert isinstance(second.my_display_config, DisplayConfig)


@pytest.mark.base
def test_default_display_config_mutation_does_not_propagate() -> None:
    """Mutating one instance's display config must not affect another."""
    config = _make_config()
    sim_params = _make_sim_params()

    first = PTXController(my_simulation_parameters=sim_params, config=config)
    second = PTXController(my_simulation_parameters=sim_params, config=config)

    first.my_display_config.pretty_name = "first"
    first.my_display_config.display_in_webtool = True

    assert second.my_display_config.pretty_name is None
    assert second.my_display_config.display_in_webtool is False


@pytest.mark.base
def test_explicit_display_config_is_respected() -> None:
    """An explicitly passed DisplayConfig is used as-is."""
    config = _make_config()
    sim_params = _make_sim_params()
    explicit = DisplayConfig(pretty_name="custom", display_in_webtool=True)

    controller = PTXController(
        my_simulation_parameters=sim_params,
        config=config,
        my_display_config=explicit,
    )

    assert controller.my_display_config is explicit


@pytest.mark.base
def test_output_channel_names_use_battery_not_third() -> None:
    """PowerToBattery/EnergyToBattery replace the misleading PowerToThird/EnergyToThird."""
    assert PTXController.PowerToBattery == "PowerToBattery"
    assert PTXController.EnergyToBattery == "EnergyToBattery"
    # Old misleading names must no longer exist as class attributes.
    assert not hasattr(PTXController, "PowerToThird")
    assert not hasattr(PTXController, "EnergyToThird")


@pytest.mark.base
def test_output_channels_registered_with_renamed_field_names() -> None:
    """The output channel objects must carry the renamed field names."""
    config = _make_config()
    sim_params = _make_sim_params()
    controller = PTXController(my_simulation_parameters=sim_params, config=config)

    assert controller.load_to_battery.field_name == "PowerToBattery"
    assert controller.energy_to_battery.field_name == "EnergyToBattery"


@pytest.mark.base
def test_ptx_controller_config_attribute_name() -> None:
    """The config is stored as self.ptx_controller_config (not ptxcontrollerconfig)."""
    config = _make_config()
    sim_params = _make_sim_params()
    controller = PTXController(my_simulation_parameters=sim_params, config=config)

    assert controller.ptx_controller_config is config
    assert not hasattr(controller, "ptxcontrollerconfig")
    # write_to_report delegates to the config, so it must still work after the rename.
    assert controller.write_to_report() == config.get_string_dict()


@pytest.mark.base
def test_operation_mode_round_trips_as_the_legacy_string() -> None:
    """The enum-typed operation mode keeps the pre-enum wire value.

    ``operation_mode`` used to be a free-text ``str``, and configs already on disk
    spell the mode as ``"NominalLoad"`` and friends. Those literal strings are written
    out here rather than read back off the enum, so the test pins the wire contract
    instead of pinning the enum against itself: every one of them must still decode,
    and the enum must offer exactly them and nothing else.
    """
    config = _make_config()
    legacy_wire_values = ("NominalLoad", "MinimumLoad", "StandbyLoad", "StandbyandOffLoad")

    assert json.loads(config.to_json())["operation_mode"] == "NominalLoad"

    for wire_value in legacy_wire_values:
        payload = json.loads(config.to_json())
        payload["operation_mode"] = wire_value
        assert PTXControllerConfig.from_dict(payload).operation_mode.value == wire_value
        assert PTXControllerConfig.from_json(json.dumps(payload)).operation_mode.value == wire_value

    assert {mode.value for mode in PtxOperationMode} == set(legacy_wire_values)


@pytest.mark.base
def test_a_mode_that_names_nothing_is_refused_where_it_is_written() -> None:
    """A misspelt mode is refused by the config, not carried into the control law.

    The mode arrives from a JSON or YAML file, where the type checker cannot see it,
    which is why the misspelling below is typed ``Any``. Before the enum such a value
    never raised: it fell through the ``if``/``elif`` chain into a catch-all that
    quietly followed the load, so the controller ran a law nobody had asked for.
    """
    misspelt_mode: Any = "NominalLoadd"

    with pytest.raises(ValueError) as raised:
        PTXControllerConfig(
            component_id=ComponentID(name="L2PtXController"),
            nom_load=100.0,
            min_load=20.0,
            max_load=100.0,
            standby_load=10.0,
            operation_mode=misspelt_mode,
        )

    message = str(raised.value)
    assert "NominalLoadd" in message
    assert all(mode.value in message for mode in PtxOperationMode)


@pytest.mark.base
def test_the_report_names_the_operation_mode_by_its_value() -> None:
    """The report line carries the mode as written, not as ``PtxOperationMode.NOMINAL_LOAD``."""
    assert "Operation mode: NominalLoad" in _make_config().get_string_dict()


@pytest.mark.base
def test_replacing_the_mode_with_its_wire_string_yields_the_member() -> None:
    """``dataclasses.replace`` runs ``__post_init__``, so a string becomes the member.

    ``replace`` is how a config is edited in place in a setup, and a mode handed to it
    can come from a file just as the constructor's can, hence the ``Any`` annotation.
    """
    wire_value: Any = "StandbyLoad"

    replaced = dataclasses.replace(_make_config(), operation_mode=wire_value)

    assert replaced.operation_mode is PtxOperationMode.STANDBY_LOAD
