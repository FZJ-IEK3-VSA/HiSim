"""The system builder turns a case into configurations that carry nothing of the carrier row."""

import json
from typing import Dict

import pytest

from hisim import loadtypes as lt
from hisim.components.building import BuildingInformation, ValueOrigin
from hisim.verification import system_builder
from hisim.verification.schema import Case

pytestmark = pytest.mark.base


@pytest.fixture(scope="module", name="built_600")
def fixture_built_600(cases: Dict[str, Case]) -> system_builder.BuiltCase:
    """Case 600, built once for the module."""
    return system_builder.build(cases["600"])


def test_the_building_of_case_600_takes_nothing_from_the_carrier_row(built_600: system_builder.BuiltCase) -> None:
    """Every origin is configured or derived; the carrier row is named and unused."""
    origins = built_600.parameters.origins

    assert built_600.building.building_code == system_builder.CARRIER_BUILDING_CODE
    assert {quantity for quantity, origin in origins.items() if origin.source == ValueOrigin.TABULA_ROW} == set()
    assert origins["windows"].source == ValueOrigin.CONFIGURED
    assert origins["thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin"].source == ValueOrigin.CONFIGURED


def test_the_technical_room_is_set_rather_than_sized_from_the_carrier_row(built_600: system_builder.BuiltCase) -> None:
    """The location main sizes from the row's cellar code is set, so the row does not decide it (gap analysis §17)."""
    assert built_600.building.technical_room_location is lt.InstallationLocation.HEATED_ZONE


def test_the_building_carries_the_case_values(built_600: system_builder.BuiltCase) -> None:
    """Areas, the U-values (the case's times the factor of D28), the volume, the adjusted infiltration, the window and the mass quantities."""
    config = built_600.building
    mass = built_600.parameters.mass
    factor = built_600.parameters.steady_state_transmission.factor

    assert (config.floor_area_in_m2, config.facade_area_in_m2, config.roof_area_in_m2) == (48.0, pytest.approx(63.6), 48.0)
    assert built_600.parameters.specified_u_values_by_kind == {"wall": 0.418, "roof": 0.276, "floor": 0.039}
    assert factor > 1.0
    assert config.floor_u_value_in_watt_per_m2_per_kelvin == pytest.approx(factor * 0.039)
    assert config.facade_u_value_in_watt_per_m2_per_kelvin == pytest.approx(factor * 0.418)
    assert config.conditioned_air_volume_in_m3 == 129.6
    assert config.use_air_change_rate_per_hour == 0.0
    assert config.infiltration_air_change_rate_per_hour == pytest.approx(0.5 * 1.0156 / 1.2255)
    assert config.windows is not None and len(config.windows) == 1
    window = config.windows[0]
    assert (window.area_in_m2, window.azimuth_angle_in_degrees, window.tilt_angle_in_degrees) == (12.0, 180.0, 90.0)
    assert window.glass_solar_transmittance == 0.769
    assert window.frame_area_fraction_reduction_factor == 0.0
    assert window.nonperpendicular_reduction_factor is not None and 0.5 < window.nonperpendicular_reduction_factor < 1.0
    assert config.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin == mass.thermal_capacity_in_joule_per_kelvin
    assert config.effective_mass_area_in_m2 == mass.effective_mass_area_in_m2
    assert config.total_internal_surface_area_in_m2 == pytest.approx(171.6)
    assert (config.set_heating_temperature_in_celsius, config.set_cooling_temperature_in_celsius) == (20.0, 27.0)
    assert config.door_area_in_m2 == 0.0
    assert built_600.setback is None


def test_building_information_reproduces_the_conductances(built_600: system_builder.BuiltCase) -> None:
    """H = f times U times A times 1 for the opaque elements and the window; H_ve = 0.34 times n times V."""
    information = BuildingInformation(config=built_600.building)
    parameters = built_600.parameters
    factor = parameters.steady_state_transmission.factor

    assert information.heat_conductance_facade_in_watt_per_kelvin == pytest.approx(factor * 0.418 * 63.6)
    assert information.heat_conductance_floor_in_watt_per_kelvin == pytest.approx(factor * 0.039 * 48.0)
    assert information.heat_conductance_roof_in_watt_per_kelvin == pytest.approx(factor * 0.276 * 48.0)
    assert parameters.specified_window_u_value_in_watt_per_m2_per_kelvin is not None
    assert information.heat_conductance_window_in_watt_per_kelvin == pytest.approx(factor * parameters.specified_window_u_value_in_watt_per_m2_per_kelvin * 12.0)
    assert information.heat_conductance_door_in_watt_per_kelvin == 0.0
    assert information.heat_conductance_ventilation_in_watt_per_kelvin == pytest.approx(0.34 * (0.5 * 1.0156 / 1.2255) * 129.6)
    assert len(information.window_geometries()) == 1


def test_the_network_keeps_the_u_a_sum_of_every_case_in_steady_state(cases: Dict[str, Case]) -> None:
    """H_tr,is in series with H_tr,w + H_tr,op equals the case's U-A sum, which is computed here from the case alone (D28)."""
    for case_id, case in cases.items():
        information = BuildingInformation(config=system_builder.build(case).building)
        transmission = (
            information.heat_conductance_window_in_watt_per_kelvin
            + information.heat_conductance_door_in_watt_per_kelvin
            + information.heat_conductance_facade_in_watt_per_kelvin
            + information.heat_conductance_roof_in_watt_per_kelvin
            + information.heat_conductance_floor_in_watt_per_kelvin
        )
        coupling = information.heat_transfer_coeff_indoor_air_and_internal_surface_fixed_value_in_watt_per_m2_per_kelvin * information.total_internal_surface_area_in_m2
        specified = sum(surface.area_in_m2 * case.construction_of(surface).u_value_air_to_air_in_watt_per_m2_per_kelvin for surface in case.surfaces)
        specified += sum(window.area_in_m2 * case.glazing_of(window).u_value_air_to_air_in_watt_per_m2_per_kelvin for window in case.windows)

        assert 1.0 / (1.0 / coupling + 1.0 / transmission) == pytest.approx(specified, rel=1e-12), case_id


def test_the_weather_is_the_denver_station(built_600: system_builder.BuiltCase) -> None:
    """The catalogue station of the case, with the EPW's coldest hour as the design temperature."""
    assert built_600.weather.location == "Denver Intl AP"
    assert built_600.weather.source_path.endswith("725650TYCST.epw")
    assert built_600.weather.heating_reference_temperature_in_celsius == built_600.parameters.heating_reference_temperature_in_celsius
    assert built_600.parameters.heating_reference_temperature_in_celsius < 0.0


def test_the_parameters_are_plain_data(built_600: system_builder.BuiltCase) -> None:
    """The record for parameters/<case>.json serialises as JSON and names the derivation."""
    data = json.loads(json.dumps(built_600.parameters.to_json_dict()))

    assert data["case"] == "600"
    assert data["air_change_rate"]["for_hisim_per_hour"] == pytest.approx(0.5 * 1.0156 / 1.2255)
    assert data["windows"][0]["window"] == "south"
    assert data["u_values"]["specified_by_kind_in_watt_per_m2_per_kelvin"]["wall"] == 0.418
    assert data["u_values"]["for_hisim_by_kind_in_watt_per_m2_per_kelvin"]["wall"] == pytest.approx(data["u_values"]["factor"] * 0.418)
    assert data["mass"]["surfaces"][0]["thickness"]["decisive_condition"] in ("a", "b", "c")
    assert data["origins"]["windows"]["source"] == "configured"


def test_case_640_gets_the_setback_controller(cases: Dict[str, Case]) -> None:
    """Minus ten kelvin from 23:00 to 07:00, stepping back at 07:00 (decision D18)."""
    setback = system_builder.setback_config(cases["640"])

    assert setback is not None
    assert setback.setback_delta_in_kelvin == -10.0
    assert (setback.night_start_time_in_hours, setback.night_end_time_in_hours) == (23, 7)


def test_a_free_float_case_carries_placeholder_set_points_and_no_setback(cases: Dict[str, Case]) -> None:
    """The configuration needs set points; the free-float run connects no heater to them."""
    built = system_builder.build(cases["900FF"])

    assert built.setback is None
    assert built.building.set_heating_temperature_in_celsius == system_builder.FREE_FLOAT_HEATING_SETPOINT_IN_CELSIUS
    assert built.building.set_cooling_temperature_in_celsius == system_builder.FREE_FLOAT_COOLING_SETPOINT_IN_CELSIUS
    assert built.parameters.mass.thermal_capacity_in_joule_per_kelvin / 1e6 == pytest.approx(13.350, abs=0.0005)


def test_case_620_has_two_windows_with_their_own_factors(cases: Dict[str, Case]) -> None:
    """East and west windows, each with its own F_w, and the area-weighted window U-value times the factor of D28."""
    built = system_builder.build(cases["620"])
    windows = built.building.windows
    specified = cases["620"].glazings["double_clear"].u_value_air_to_air_in_watt_per_m2_per_kelvin

    assert windows is not None and [window.azimuth_angle_in_degrees for window in windows] == [90.0, 270.0]
    assert {factor.window for factor in built.parameters.window_factors} == {"east", "west"}
    assert built.parameters.specified_window_u_value_in_watt_per_m2_per_kelvin == specified
    assert built.building.window_u_value_in_watt_per_m2_per_kelvin == pytest.approx(built.parameters.steady_state_transmission.factor * specified)
    assert built.building.facade_area_in_m2 == pytest.approx(21.6 + 10.2 + 10.2 + 21.6)
