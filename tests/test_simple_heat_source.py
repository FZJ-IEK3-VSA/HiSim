"""Tests for the SimpleHeatSource component with constant-power configuration."""

import json
import warnings

import pytest
from hisim import component as cp
from hisim.components import simple_heat_source
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
from tests import functions_for_testing as fft


@pytest.mark.base
def test_heat_source() -> None:
    """Test SimpleHeatSource with constant power configuration.

    Verifies that the component correctly calculates thermal power delivered
    when given mass flow and temperature inputs. Uses fake ComponentOutput
    objects for mass flow (0.3 kg/s) and temperature (5°C) inputs, and
    asserts that the thermal power delivered equals 5000.0 W.

    Args:
        None (uses default configuration and fake inputs).

    Returns:
        None (raises AssertionError if assertions fail).
    """

    # simulation parameters
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017, seconds_per_timestep)

    # default config
    my_heat_source_config = simple_heat_source.SimpleHeatSourceConfig.get_default_config_const_power()

    my_heat_source = simple_heat_source.SimpleHeatSource(
        config=my_heat_source_config, my_simulation_parameters=my_simulation_parameters
    )

    massflow = cp.ComponentOutput(
        "Fake_massflow", "Fake_massflow", lt.LoadTypes.ANY, lt.Units.ANY, component_id=cp.ComponentID("Fake_massflow")
    )
    temperature_input = cp.ComponentOutput(
        "Fake_t_in", "Fake_t_in", lt.LoadTypes.ANY, lt.Units.ANY, component_id=cp.ComponentID("Fake_t_in")
    )

    number_of_outputs = fft.get_number_of_outputs(
        [
            my_heat_source,
            massflow,
            temperature_input,
        ]
    )
    time_step_values: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    my_heat_source.massflow_input_channel.source_output = massflow
    my_heat_source.temperature_input_channel.source_output = temperature_input

    fft.add_global_index_of_components(
        [
            my_heat_source,
            massflow,
            temperature_input,
        ]
    )

    time_step_values.values[massflow.global_index] = 0.3
    time_step_values.values[temperature_input.global_index] = 5

    timestep = 1

    # Simulate
    my_heat_source.i_simulate(timestep, time_step_values, False)

    assert time_step_values.values[my_heat_source.thermal_power_delivered_channel.global_index] == pytest.approx(
        5000.0, rel=1e-6
    )


@pytest.mark.base
def test_config_serialization_uses_renamed_field_names() -> None:
    """to_dict/to_json must emit the current (renamed) field names.

    Issue #1603 renamed ``const_source`` -> ``heat_source_type`` and
    ``temperature_out_in_celsius`` -> ``temperature_output_in_celsius``.
    Serialization must always use the new names so newly written configs are
    forward-compatible.
    """
    config = simple_heat_source.SimpleHeatSourceConfig.get_default_config_const_power()
    as_dict = config.to_dict()
    assert "heat_source_type" in as_dict
    assert "temperature_output_in_celsius" in as_dict
    assert "const_source" not in as_dict
    assert "temperature_out_in_celsius" not in as_dict
    # Round-trip through JSON keeps the new names.
    as_json = config.to_json()
    assert "heat_source_type" in as_json
    assert "temperature_output_in_celsius" in as_json
    assert "const_source" not in as_json
    assert "temperature_out_in_celsius" not in as_json


@pytest.mark.base
def test_config_from_dict_accepts_legacy_field_names() -> None:
    """from_dict/from_json accept the pre-rename field names as aliases.

    A config JSON saved before the rename used ``const_source`` and
    ``temperature_out_in_celsius``. Loading it must still work and emit a
    DeprecationWarning guiding users to the new names.
    """
    config = simple_heat_source.SimpleHeatSourceConfig.get_default_config_const_temperature()

    # from_dict path: to_dict() returns enum members, which from_dict accepts.
    legacy_dict = config.to_dict()
    legacy_dict["const_source"] = legacy_dict.pop("heat_source_type")
    legacy_dict["temperature_out_in_celsius"] = legacy_dict.pop("temperature_output_in_celsius")
    with pytest.warns(DeprecationWarning, match="const_source"):
        loaded = simple_heat_source.SimpleHeatSourceConfig.from_dict(legacy_dict)
    assert loaded.heat_source_type is simple_heat_source.SimpleHeatSourceType.CONSTANT_TEMPERATURE
    assert loaded.temperature_output_in_celsius == 5

    # from_json path: to_json() serializes enums to strings; rename the keys in
    # the parsed dict and re-serialize to mimic a pre-rename JSON config.
    legacy_payload = json.loads(config.to_json())
    legacy_payload["const_source"] = legacy_payload.pop("heat_source_type")
    legacy_payload["temperature_out_in_celsius"] = legacy_payload.pop("temperature_output_in_celsius")
    legacy_json = json.dumps(legacy_payload)
    with pytest.warns(DeprecationWarning, match="temperature_out_in_celsius"):
        loaded_from_json = simple_heat_source.SimpleHeatSourceConfig.from_json(legacy_json)
    assert loaded_from_json.heat_source_type is simple_heat_source.SimpleHeatSourceType.CONSTANT_TEMPERATURE
    assert loaded_from_json.temperature_output_in_celsius == 5


@pytest.mark.base
def test_config_from_dict_new_name_takes_precedence() -> None:
    """When both old and new key are present, the new name wins and no data is lost."""
    config = simple_heat_source.SimpleHeatSourceConfig.get_default_config_const_power()
    both = config.to_dict()
    both["const_source"] = simple_heat_source.SimpleHeatSourceType.CONSTANT_TEMPERATURE.value
    both["temperature_out_in_celsius"] = 999

    with pytest.warns(DeprecationWarning):
        loaded = simple_heat_source.SimpleHeatSourceConfig.from_dict(both)
    # New names win.
    assert loaded.heat_source_type is simple_heat_source.SimpleHeatSourceType.CONSTANT_THERMAL_POWER
    assert loaded.temperature_output_in_celsius is None


@pytest.mark.base
def test_config_from_dict_without_legacy_names_emits_no_warning() -> None:
    """Loading a current-shape config must not warn (regression guard)."""
    config = simple_heat_source.SimpleHeatSourceConfig.get_default_config_const_power()
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        simple_heat_source.SimpleHeatSourceConfig.from_dict(config.to_dict())


@pytest.mark.base
def test_get_default_config_near_surface_brine_temperature() -> None:
    """The renamed factory sets the near-surface brine temperature mode."""
    config = simple_heat_source.SimpleHeatSourceConfig.get_default_config_near_surface_brine_temperature()
    assert config.heat_source_type is simple_heat_source.SimpleHeatSourceType.NEAR_SURFACE_BRINE_TEMPERATURE
    assert config.component_id.name == "HeatSourceVarBrineTemperature"


@pytest.mark.base
def test_deprecated_get_default_config_var_brinetemperature_alias() -> None:
    """The old abbreviated factory name still works and warns."""
    with pytest.warns(DeprecationWarning, match="get_default_config_var_brinetemperature"):
        config = simple_heat_source.SimpleHeatSourceConfig.get_default_config_var_brinetemperature()
    assert config.heat_source_type is simple_heat_source.SimpleHeatSourceType.NEAR_SURFACE_BRINE_TEMPERATURE
    assert config.component_id.name == "HeatSourceVarBrineTemperature"
