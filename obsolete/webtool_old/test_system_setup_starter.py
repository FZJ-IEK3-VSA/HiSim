"""Test system setup starter."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any
import json

import pytest

from hisim.hisim_main import main
from hisim.system_setup_starter import make_system_setup


@pytest.mark.utsp
def test_system_setup_starter() -> None:
    """Run a simulation from JSON."""

    parameters_json: dict[str, Any] = {
        "path_to_module": "../system_setups/household_heat_pump.py",
        "simulation_parameters": {
            "start_date": "2021-01-01T00:00:00",
            "end_date": "2021-01-02T00:00:00",
            "seconds_per_timestep": 900,
            "post_processing_options": [9, 18, 19, 20, 22],
        },
        "system_setup_config": {
            # "some_subconf": {
            #     "some_attribute": "some_building"
            # },  # Raises AttributeError
            "building_type": "some_building",
            "hp_config": {
                "set_thermal_output_power_in_watt": {"value": 99999, "unit": "W"},
                # "cost": {"value": 1000000, "unit": "Euro"},
            },
        },
    }

    result_directory: str = "test_system_setup_starter_results"
    if Path(result_directory).is_dir():
        shutil.rmtree(result_directory)
    Path(result_directory).mkdir(parents=True)
    (
        path_to_module,
        simulation_parameters,
        module_config_path,
    ) = make_system_setup(
        parameters_json=parameters_json,
        result_directory=result_directory,
    )
    main(
        path_to_module,
        simulation_parameters,
        module_config_path,
    )
    # Check results
    assert (Path(result_directory) / "finished.flag").is_file()
    assert (Path(result_directory) / "results_for_webtool.json").is_file()

    assert (
        simulation_parameters.seconds_per_timestep  # type: ignore
        == parameters_json["simulation_parameters"]["seconds_per_timestep"]
    )

    if simulation_parameters:
        with open(Path(simulation_parameters.result_directory).joinpath("results_for_webtool.json"), "rb") as handle:
            results_for_webtool: dict[str, Any] = json.load(handle)
        assert (
            99999
            == results_for_webtool["components"]["AdvancedHeatPumpHPLib"]["configuration"][
                "set_thermal_output_power_in_watt"
            ]["value"]
        )
    else:
        raise ValueError("No simulations parameters created.")

    # Check if the costs of the heat pump have been adapted.
    # TODO: Rewrite to the new data path
    # with open(result_directory + "/results_for_webtool.json", "r", encoding="utf8") as file:
    #     webtool_kpis = json.load(file)
    # assert webtool_kpis["capexDict"]["column 1"]["HeatPumpHPLib [Investment in EUR] "] == 273.97
    # Remove result directory
    time.sleep(1)
    shutil.rmtree(result_directory)


@pytest.mark.utsp
def test_system_setup_starter_scaling() -> None:
    """Run a simulation from JSON with a scaled building configuration.

    Builds a system setup from a JSON payload that includes a detailed
    ``building_config`` (building code, floor area, heat-capacity class,
    etc.), executes the simulation via ``hisim_main.main``, and asserts
    that ``finished.flag`` and ``results_for_webtool.json`` are produced
    in the result directory.
    """

    parameters_json: dict[str, Any] = {
        "path_to_module": "../system_setups/household_heat_pump.py",
        "simulation_parameters": {
            "start_date": "2021-01-01T00:00:00",
            "end_date": "2021-01-02T00:00:00",
            "seconds_per_timestep": 900,
            "post_processing_options": [9, 18, 19, 20, 22],
        },
        "building_config": {
            "building_name": "BUI1",
            "name": "Building",
            "building_code": "DE.N.SFH.05.Gen.ReEx.001.002",
            "building_heat_capacity_class": "medium",
            "initial_internal_temperature_in_celsius": 23,
            "heating_reference_temperature_in_celsius": -7.0,
            "absolute_conditioned_floor_area_in_m2": 121.2,
            "total_base_area_in_m2": None,
            "number_of_apartments": 1,
            "predictive": False,
            "set_heating_temperature_in_celsius": 19.0,
            "set_cooling_temperature_in_celsius": 24.0,
            "enable_opening_windows": False,
            "max_thermal_building_demand_in_watt": None,
            "floor_u_value_in_watt_per_m2_per_kelvin": None,
            "floor_area_in_m2": None,
            "facade_u_value_in_watt_per_m2_per_kelvin": None,
            "facade_area_in_m2": None,
            "roof_u_value_in_watt_per_m2_per_kelvin": None,
            "roof_area_in_m2": None,
            "window_u_value_in_watt_per_m2_per_kelvin": None,
            "window_area_in_m2": None,
            "door_u_value_in_watt_per_m2_per_kelvin": None,
            "door_area_in_m2": None,
            "device_co2_footprint_in_kg": 1,
            "investment_costs_in_euro": 1,
            "maintenance_costs_in_euro_per_year": 0.01,
            # Despite "percentage" in its name, this value is a fraction in
            # the range [0, 1] (e.g. 0.3 == a 30% subsidy), consistent with
            # CapexCostDataClass and the device configs in configuration.py.
            "subsidy_as_percentage_of_investment_costs": 0,
            "lifetime_in_years": 1,
        },
    }

    result_directory: str = "test_system_setup_starter_scaling_results"
    if Path(result_directory).is_dir():
        shutil.rmtree(result_directory)
    Path(result_directory).mkdir(parents=True)
    (
        path_to_module,
        simulation_parameters,
        module_config_path,
    ) = make_system_setup(
        parameters_json=parameters_json,
        result_directory=result_directory,
    )
    main(
        path_to_module,
        simulation_parameters,
        module_config_path,
    )
    # Check results
    assert (Path(result_directory) / "finished.flag").is_file()
    assert (Path(result_directory) / "results_for_webtool.json").is_file()

    assert (
        simulation_parameters.seconds_per_timestep  # type: ignore
        == parameters_json["simulation_parameters"]["seconds_per_timestep"]
    )

    # Verify that the building configuration propagated to the simulation
    # results.  The webtool JSON stores each component's config via
    # config.to_dict(); BuildingConfig fields are plain values (not
    # {"value": ...} dicts like Quantity fields), so the assertions
    # compare directly against the scalar values set in building_config.
    with open(
        Path(simulation_parameters.result_directory).joinpath("results_for_webtool.json"),
        "rb",
    ) as handle:
        results_for_webtool: dict[str, Any] = json.load(handle)
    building_cfg = results_for_webtool["components"]["Building"]["configuration"]
    # Values that match the building_config input — confirm the structure:
    assert building_cfg["building_code"] == "DE.N.SFH.05.Gen.ReEx.001.002"
    assert building_cfg["absolute_conditioned_floor_area_in_m2"] == pytest.approx(121.2)
    # Values that differ from the unscaled default — these verify the config
    # was actually applied, not just matching the default:
    assert building_cfg["initial_internal_temperature_in_celsius"] == pytest.approx(23.0)
    assert building_cfg["number_of_apartments"] == pytest.approx(1.0)
    assert building_cfg["set_heating_temperature_in_celsius"] == pytest.approx(19.0)
    assert building_cfg["set_cooling_temperature_in_celsius"] == pytest.approx(24.0)

    # Remove result directory
    time.sleep(1)
    shutil.rmtree(result_directory)


@pytest.mark.base
def test_make_system_setup_rejects_list(tmp_path: Path) -> None:
    """A list of setups is not supported and must raise a clear error.

    The signature advertises ``dict[str, Any]`` only. Callers that ignore the
    type (e.g. feeding a JSON array via ``json.load``) must fail loudly with a
    ``NotImplementedError`` instead of producing a confusing ``TypeError`` deep
    inside the parser.
    """
    # Deliberately pass a list to exercise the runtime guard. The type ignore is
    # intentional: the signature (correctly) rejects lists at the type level, so
    # this call is only valid for testing the defensive runtime check.
    with pytest.raises(NotImplementedError):
        make_system_setup(parameters_json=[], result_directory=str(tmp_path))  # type: ignore[arg-type]


@pytest.mark.base
def test_make_system_setup_builds_config_from_dict(tmp_path: Path) -> None:
    """A valid parameter dict yields the module path, simulation parameters, and config files.

    This exercises ``make_system_setup`` up to the written config files without
    running the (slow, networked) simulation via ``hisim_main.main``.
    """
    parameters_json: dict[str, Any] = {
        "path_to_module": "some_system_setup.py",
        "simulation_parameters": {
            "start_date": "2021-01-01T00:00:00",
            "end_date": "2021-01-02T00:00:00",
            "seconds_per_timestep": 900,
        },
    }

    result = make_system_setup(
        parameters_json=parameters_json,
        result_directory=str(tmp_path),
    )

    assert result.path_to_module == parameters_json["path_to_module"]
    assert result.simulation_parameters.seconds_per_timestep == 900
    assert result.module_config_path == str(tmp_path / "module_config.json")
    assert (tmp_path / "module_config.json").is_file()
    assert (tmp_path / "simulation_parameters.json").is_file()

    with open(result.module_config_path, "r", encoding="utf8") as handle:
        module_config = json.load(handle)
    assert module_config == {
        "options": {},
        "building_config": {},
        "system_setup_config": {},
    }
