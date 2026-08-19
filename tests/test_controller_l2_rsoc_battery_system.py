"""Tests for the rSOC L2 battery-system controller config seam.

These tests verify that ``RsocBatteryControllerConfig`` can be constructed
without touching the filesystem or the module-global ``utils.HISIMPATH``: an
in-memory ``config_data`` dict (or an explicit ``config_path`` for
``read_config``) can be supplied so that config construction -- and therefore
the controller built on top of it -- is testable even when the
``rSOC_manufacturer_config.json`` inputs file is absent.
"""

# clean
import json
import pathlib

import pytest

from hisim import component as cp
from hisim.components import controller_l2_rsoc_battery_system as l2
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


def _make_rsoc_config_dict() -> dict[str, float]:
    """Return a representative in-memory rSOC manufacturer config variant."""
    return {
        "nom_load_soec": 40.0,
        "min_load_soec": 2.315,
        "max_load_soec": 49.64,
        "standby_load": 1.0,
        "nom_power_sofc": 10.0,
        "min_power_sofc": 1.7,
        "max_power_sofc": 13.0,
    }


@pytest.mark.base
def test_config_rsoc_from_in_memory_dict() -> None:
    """config_rsoc builds the config from an in-memory dict (no filesystem)."""
    config = l2.RsocBatteryControllerConfig.config_rsoc(
        rsoc_name="RSOC_TEST",
        operation_mode="StandbyLoad",
        config_data=_make_rsoc_config_dict(),
    )
    assert config.component_id.building is None
    assert config.component_id.name == "rSOC and Battery Controller"
    assert config.nom_load_soec_in_kw == 40.0
    assert config.min_load_soec_in_kw == 2.315
    assert config.max_load_soec_in_kw == 49.64
    assert config.standby_load_in_kw == 1.0
    assert config.nom_power_sofc_in_kw == 10.0
    assert config.min_power_sofc_in_kw == 1.7
    assert config.max_power_sofc_in_kw == 13.0
    assert config.operation_mode == "StandbyLoad"


@pytest.mark.base
def test_config_rsoc_building_override_and_defaults() -> None:
    """config_rsoc forwards the component identity and applies defaults for missing keys."""
    config = l2.RsocBatteryControllerConfig.config_rsoc(
        rsoc_name="RSOC_TEST",
        operation_mode="MinimumLoad",
        component_id=cp.ComponentID(name="rSOC and Battery Controller", building="BUI2"),
        config_data={"nom_load_soec": 40.0},
    )
    assert config.component_id.building == "BUI2"
    assert config.operation_mode == "MinimumLoad"
    assert config.nom_load_soec_in_kw == 40.0
    # Keys absent from the in-memory dict fall back to the documented defaults.
    assert config.min_load_soec_in_kw == 0.0
    assert config.max_load_soec_in_kw == 0.0
    assert config.standby_load_in_kw == 0.0
    assert config.nom_power_sofc_in_kw == 0.0
    assert config.min_power_sofc_in_kw == 0.0
    assert config.max_power_sofc_in_kw == 0.0


@pytest.mark.base
def test_read_config_with_explicit_path(tmp_path: pathlib.Path) -> None:
    """read_config reads a variant from an explicit path (no HISIMPATH coupling)."""
    variant = _make_rsoc_config_dict()
    config_file = tmp_path / "rSOC_manufacturer_config.json"
    config_file.write_text(json.dumps({"rSOC variants": {"RSOC_TEST": variant}}), encoding="utf-8")
    loaded = l2.RsocBatteryControllerConfig.read_config("RSOC_TEST", config_path=config_file)
    assert loaded == variant


@pytest.mark.base
def test_read_config_with_explicit_path_missing_variant(tmp_path: pathlib.Path) -> None:
    """read_config returns an empty dict for an unknown variant name."""
    config_file = tmp_path / "rSOC_manufacturer_config.json"
    config_file.write_text(
        json.dumps({"rSOC variants": {"RSOC_TEST": {"nom_load_soec": 1.0}}}),
        encoding="utf-8",
    )
    loaded = l2.RsocBatteryControllerConfig.read_config("DOES_NOT_EXIST", config_path=config_file)
    assert loaded == {}


@pytest.mark.base
def test_rsoc_battery_controller_built_from_in_memory_config() -> None:
    """The controller can be built and simulated from an in-memory config dict.

    This exercises the seam end to end: the config is constructed without the
    rSOC_manufacturer_config.json inputs file, the controller is wired up, and a
    single SOFC-mode timestep is simulated.
    """
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2021, seconds_per_timestep)

    config = l2.RsocBatteryControllerConfig.config_rsoc(
        rsoc_name="RSOC_TEST",
        operation_mode="StandbyLoad",
        config_data=_make_rsoc_config_dict(),
    )
    my_controller = l2.RsocBatteryController(
        my_simulation_parameters=my_simulation_parameters,
        config=config,
    )

    res_load = cp.ComponentOutput(
        "FakeRESLoad",
        l2.RsocBatteryController.RESLoad,
        lt.LoadTypes.ELECTRICITY,
        lt.Units.WATT,
        component_id=cp.ComponentID("FakeRESLoad"),
    )
    demand = cp.ComponentOutput(
        "FakeDemand",
        l2.RsocBatteryController.Demand,
        lt.LoadTypes.ELECTRICITY,
        lt.Units.WATT,
        component_id=cp.ComponentID("FakeDemand"),
    )

    number_of_outputs = fft.get_number_of_outputs([my_controller, res_load, demand])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    my_controller.load_input.source_output = res_load
    my_controller.demand_input.source_output = demand
    fft.add_global_index_of_components([my_controller, res_load, demand])

    # demand (5 kW) > res_load (0 kW) -> power_delta = +5 kW -> SOFC branch.
    # StandbyLoad with min_power_sofc (1.7) <= 5 <= max_power_sofc (13):
    #   load_to_system = 5, power_to_battery = 0.
    stsv.values[res_load.global_index] = 0.0
    stsv.values[demand.global_index] = 5000.0

    my_controller.i_restore_state()
    my_controller.i_simulate(0, stsv, False)

    assert stsv.values[my_controller.power.global_index] == 5.0
    assert stsv.values[my_controller.load_to_system.global_index] == 5.0
    assert stsv.values[my_controller.load_to_battery.global_index] == 0.0


def _make_legacy_serialized_payload() -> dict[str, object]:
    """Return a serialized config dict using the legacy mixedCase _in_kW keys.

    These are the keys that pre-rename configs wrote to JSON/HDF5. The
    ``field_name`` aliases on the renamed dataclass must keep loading them.
    """
    return {
        "component_id": {"name": "rSOC and Battery Controller", "building": None, "unit": None},
        "nom_load_soec_in_kW": 40.0,
        "min_load_soec_in_kW": 2.315,
        "max_load_soec_in_kW": 49.64,
        "standby_load_in_kW": 1.0,
        "nom_power_sofc_in_kW": 10.0,
        "min_power_sofc_in_kW": 1.7,
        "max_power_sofc_in_kW": 13.0,
        "operation_mode": "StandbyLoad",
    }


_LEGACY_IN_KW_KEYS = {
    "nom_load_soec_in_kW",
    "min_load_soec_in_kW",
    "max_load_soec_in_kW",
    "standby_load_in_kW",
    "nom_power_sofc_in_kW",
    "min_power_sofc_in_kW",
    "max_power_sofc_in_kW",
}


@pytest.mark.base
def test_config_serialization_preserves_legacy_kw_keys() -> None:
    """to_dict/to_json keep the original _in_kW serialization keys.

    The Python attributes were renamed to ``_in_kw`` for the prospector naming
    gate, but the on-disk serialization format must stay on ``_in_kW`` so that
    existing JSON/HDF5 configs remain readable (backward compatibility).
    """
    config = l2.RsocBatteryControllerConfig.config_rsoc(
        rsoc_name="RSOC_TEST",
        operation_mode="StandbyLoad",
        config_data=_make_rsoc_config_dict(),
    )
    serialized = config.to_dict()
    assert _LEGACY_IN_KW_KEYS.issubset(serialized.keys())
    # The renamed snake_case attributes must not leak into the serialized form.
    assert not any(key.endswith("_in_kw") for key in serialized)
    json_text = config.to_json()
    for key in _LEGACY_IN_KW_KEYS:
        assert key in json_text


@pytest.mark.base
def test_config_round_trip_from_legacy_kw_keys() -> None:
    """A legacy JSON payload using _in_kW keys deserializes into _in_kw fields.

    This is the core backward-compatibility guarantee: configs written before
    the rename still load with correct values, both from a dict and from JSON
    text (the path real config files take).
    """
    payload = _make_legacy_serialized_payload()
    config = l2.RsocBatteryControllerConfig.from_dict(payload)
    assert config.nom_load_soec_in_kw == 40.0
    assert config.min_load_soec_in_kw == 2.315
    assert config.max_load_soec_in_kw == 49.64
    assert config.standby_load_in_kw == 1.0
    assert config.nom_power_sofc_in_kw == 10.0
    assert config.min_power_sofc_in_kw == 1.7
    assert config.max_power_sofc_in_kw == 13.0
    assert config.operation_mode == "StandbyLoad"

    reloaded = l2.RsocBatteryControllerConfig.from_json(json.dumps(payload))
    assert reloaded.to_dict() == config.to_dict()
    assert reloaded.max_power_sofc_in_kw == 13.0


@pytest.mark.base
def test_get_string_dict_report_format_unchanged() -> None:
    """write_to_report keeps the pre-rename human-readable format.

    ``get_string_dict`` serializes via ``to_dict`` and then splits each key on
    ``_`` and capitalizes it. Because ``str.capitalize`` lowercases every
    character after the first, the report text is naturally identical whether
    the serialization key is ``_in_kW`` (the legacy ``field_name`` alias) or
    ``_in_kw`` (the Python attribute) -- both render as ``... in kw``. This
    test is therefore a regression guard for the report wording and values,
    not a verification of the ``field_name`` aliases; that guarantee is
    covered by ``test_config_serialization_preserves_legacy_kw_keys``.
    """
    config = l2.RsocBatteryControllerConfig.config_rsoc(
        rsoc_name="RSOC_TEST",
        operation_mode="StandbyLoad",
        config_data=_make_rsoc_config_dict(),
    )
    report = config.get_string_dict()
    assert "Nom load soec in kw: 40.0" in report
    assert "Max power sofc in kw: 13.0" in report
    # No raw snake_case attribute names leak into the report.
    assert not any("_in_kw" in entry for entry in report)
