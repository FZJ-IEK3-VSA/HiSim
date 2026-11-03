"""The configured overrides of a TABULA building and the origin record (verification step 2b).

Every optional field of ``BuildingConfig`` means "take the TABULA row's value" when unset. The
building-model verification suite describes its buildings in the configuration and names a row
only because the config requires one, so every quantity the 5R1C model reads has a field, the
windows are a list with their own angles and optics, and ``BuildingInformation.value_origins``
says for each quantity whether it is the configuration's, the row's, or a rule's. The Building
takes its window geometries from ``BuildingInformation`` instead of assembling them from the five
TABULA directions itself; for a TABULA building they are the same seven numbers as before, which
the first tests pin for the default German single-family home.
"""

import dataclasses
from typing import Any, Dict

import pytest

from hisim.components import weather
from hisim.components.building import (
    Building,
    BuildingConfig,
    BuildingInformation,
    ConfiguredWindow,
    ValueOrigin,
)
from hisim.components.building.solar_gains import WindowGeometry
from hisim.config import ComponentID, SizingContext
from hisim.simulator import SimulationParameters


class GermanSingleFamilyHome:
    """The default German single-family home, as the TABULA row describes it at its own floor area."""

    BUILDING_CODE = "DE.N.SFH.05.Gen.ReEx.001.002"
    CONDITIONED_FLOOR_AREA_IN_M2 = 121.2
    HEATING_REFERENCE_TEMPERATURE_IN_CELSIUS = -7.0

    #: The row's optics factors ``F_f``, ``g_gl_n``, ``F_w`` and ``F_sh_vert``.
    FRAME_FRACTION = 0.3
    GLASS_TRANSMITTANCE = 0.6
    NONPERPENDICULAR = 0.9
    EXTERNAL_SHADING = 0.6

    #: The five windows ``Building.get_windows`` assembled before ``BuildingInformation`` took over:
    #: south, east, north, west at 90°, horizontal at 0° without azimuth, the row's areas.
    WINDOWS = (
        WindowGeometry(90.0, 180.0, 6.3, FRAME_FRACTION, GLASS_TRANSMITTANCE, NONPERPENDICULAR, EXTERNAL_SHADING),
        WindowGeometry(90.0, 90.0, 5.7, FRAME_FRACTION, GLASS_TRANSMITTANCE, NONPERPENDICULAR, EXTERNAL_SHADING),
        WindowGeometry(90.0, 0.0, 4.1, FRAME_FRACTION, GLASS_TRANSMITTANCE, NONPERPENDICULAR, EXTERNAL_SHADING),
        WindowGeometry(90.0, 270.0, 8.9, FRAME_FRACTION, GLASS_TRANSMITTANCE, NONPERPENDICULAR, EXTERNAL_SHADING),
        WindowGeometry(0.0, None, 0.0, FRAME_FRACTION, GLASS_TRANSMITTANCE, NONPERPENDICULAR, EXTERNAL_SHADING),
    )
    TOTAL_WINDOW_AREA_IN_M2 = 25.0

    #: The row's room height ``h_room`` in m and the two air change rates in 1/h.
    ROOM_HEIGHT_IN_M = 2.5
    USE_AIR_CHANGE_RATE_PER_HOUR = 0.4
    INFILTRATION_AIR_CHANGE_RATE_PER_HOUR = 0.2

    @classmethod
    def config(cls, **overrides: Any) -> BuildingConfig:
        """The row's building at its own floor area, with the given fields set on top."""
        config = BuildingConfig(
            component_id=ComponentID(name="Building"),
            building_code=cls.BUILDING_CODE,
            heating_reference_temperature_in_celsius=cls.HEATING_REFERENCE_TEMPERATURE_IN_CELSIUS,
            absolute_conditioned_floor_area_in_m2=cls.CONDITIONED_FLOOR_AREA_IN_M2,
        )
        return dataclasses.replace(config, **overrides)


class FullyConfigured:
    """A building whose every quantity the 5R1C reads is set in the configuration.

    The row is a carrier only; :meth:`BuildingInformation.value_origins` must name it nowhere.
    The numbers are arbitrary: the test is about the record, not about a building.
    """

    OVERRIDES: Dict[str, Any] = {
        "number_of_apartments": 1.0,
        "max_thermal_building_demand_in_watt": 5000.0,
        "floor_area_in_m2": 50.0,
        "floor_u_value_in_watt_per_m2_per_kelvin": 0.25,
        "floor_transmission_adjustment_factor": 1.0,
        "facade_area_in_m2": 70.0,
        "facade_u_value_in_watt_per_m2_per_kelvin": 0.4,
        "facade_transmission_adjustment_factor": 1.0,
        "roof_area_in_m2": 55.0,
        "roof_u_value_in_watt_per_m2_per_kelvin": 0.2,
        "roof_transmission_adjustment_factor": 1.0,
        "window_u_value_in_watt_per_m2_per_kelvin": 2.0,
        "windows": [ConfiguredWindow(10.0, 180.0, 90.0, 0.7, 0.1, 0.9, 1.0)],
        "door_area_in_m2": 0.0,
        "door_u_value_in_watt_per_m2_per_kelvin": 1.0,
        "use_air_change_rate_per_hour": 0.0,
        "infiltration_air_change_rate_per_hour": 0.6,
        "conditioned_air_volume_in_m3": 140.0,
        "thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin": 3.0e6,
        "effective_mass_area_in_m2": 100.0,
        "total_internal_surface_area_in_m2": 300.0,
    }

    #: Every quantity :meth:`BuildingInformation.value_origins` reports.
    QUANTITIES = frozenset(
        {
            "scaled_conditioned_floor_area_in_m2",
            "floor_area_in_m2",
            "facade_area_in_m2",
            "roof_area_in_m2",
            "window_area_in_m2",
            "door_area_in_m2",
            "floor_u_value_in_watt_per_m2_per_kelvin",
            "facade_u_value_in_watt_per_m2_per_kelvin",
            "roof_u_value_in_watt_per_m2_per_kelvin",
            "window_u_value_in_watt_per_m2_per_kelvin",
            "door_u_value_in_watt_per_m2_per_kelvin",
            "floor_adjustment_factor",
            "wall_adjustment_factor",
            "roof_adjustment_factor",
            "window_adjustment_factor",
            "door_adjustment_factor",
            "windows",
            "use_air_change_rate_per_hour",
            "infiltration_air_change_rate_per_hour",
            "conditioned_air_volume_in_m3",
            "thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin",
            "effective_mass_area_in_m2",
            "total_internal_surface_area_in_m2",
            "number_of_apartments",
            "max_thermal_building_demand_in_watt",
        }
    )


def information_for(**overrides: Any) -> BuildingInformation:
    """``BuildingInformation`` of the German single-family home with the given fields set."""
    return BuildingInformation(config=GermanSingleFamilyHome.config(**overrides))


@pytest.mark.base
class TestTheRowWindows:
    """A TABULA building's windows are the five directions, as the Building assembled them before."""

    def test_the_geometries_are_the_five_directions_with_the_row_values(self) -> None:
        """Order, angles, areas and the four factors are exactly the numbers of the former loop."""
        information = information_for()

        assert information.window_geometries() == GermanSingleFamilyHome.WINDOWS
        assert information.value_origins()["windows"].source == ValueOrigin.TABULA_ROW

    def test_the_building_takes_the_geometries_and_sums_their_areas(self) -> None:
        """``Building.get_windows`` hands out ``BuildingInformation``'s tuple and the same total as before."""
        weather_config = weather.WeatherConfig.preset_aachen("Weather")
        config = BuildingConfig.preset_german_single_family_home("Building")
        config.weather_identity = weather_config.identity()
        config = config.resolve(SizingContext(heating_reference_temperature_in_celsius=weather_config.heating_reference_temperature_in_celsius))
        building = Building(config=config, my_simulation_parameters=SimulationParameters.full_year(year=2021, seconds_per_timestep=3600))

        assert building.window_geometries == GermanSingleFamilyHome.WINDOWS
        assert building.total_scaled_windows_area == GermanSingleFamilyHome.TOTAL_WINDOW_AREA_IN_M2
        assert building.window_geometries is building.my_building_information.window_geometries()


@pytest.mark.base
class TestConfiguredWindows:
    """A window list replaces the row's directions, one geometry per item."""

    def test_each_window_becomes_one_geometry_with_its_own_angles(self) -> None:
        """A 30° roof light facing south-west and a facade window with the row's factors."""
        roof_light = ConfiguredWindow(4.0, 225.0, 30.0, 0.5, 0.1, 0.85, 0.95)
        facade_window = ConfiguredWindow(6.0, 180.0, 90.0)
        information = information_for(windows=[roof_light, facade_window])

        assert information.window_geometries() == (
            WindowGeometry(30.0, 225.0, 4.0, 0.1, 0.5, 0.85, 0.95),
            WindowGeometry(
                90.0,
                180.0,
                6.0,
                GermanSingleFamilyHome.FRAME_FRACTION,
                GermanSingleFamilyHome.GLASS_TRANSMITTANCE,
                GermanSingleFamilyHome.NONPERPENDICULAR,
                GermanSingleFamilyHome.EXTERNAL_SHADING,
            ),
        )
        assert information.window_area_in_m2 == 10.0
        assert information.heat_conductance_window_in_watt_per_kelvin == (information.window_u_value_in_watt_per_m2_per_kelvin * 10.0 * 1)
        assert not information.windows_directions
        assert not information.scaled_window_areas_in_m2
        assert information.value_origins()["windows"].source == ValueOrigin.CONFIGURED
        assert information.value_origins()["window_area_in_m2"].source == ValueOrigin.CONFIGURED

    def test_a_horizontal_window_has_no_azimuth(self) -> None:
        """Tilt 0 without an azimuth is the roof light the row's horizontal direction also is."""
        information = information_for(windows=[ConfiguredWindow(2.0, None, 0.0)])

        assert information.window_geometries()[0].azimuth_angle_in_degrees is None
        assert information.window_geometries()[0].tilt_angle_in_degrees == 0.0

    def test_a_list_beside_a_window_area_is_refused(self) -> None:
        """The list's sum is the window area; a second statement of it names both."""
        with pytest.raises(ValueError, match="window_area_in_m2"):
            information_for(windows=[ConfiguredWindow(6.0, 180.0, 90.0)], window_area_in_m2=6.0)

    def test_a_tilted_window_without_azimuth_is_refused(self) -> None:
        """Only a horizontal window may leave its azimuth out."""
        with pytest.raises(ValueError, match="azimuth"):
            ConfiguredWindow(6.0, None, 90.0)

    def test_a_negative_area_is_refused(self) -> None:
        """A window cannot have a negative area."""
        with pytest.raises(ValueError, match="negative area"):
            ConfiguredWindow(-1.0, 180.0, 90.0)


@pytest.mark.base
class TestVentilationOverrides:
    """The two air change rates and the volume reach the ventilation conductance as given."""

    def test_the_rates_and_the_volume_multiply_the_air_heat_capacity(self) -> None:
        """H_ve = 0.34 Wh/(m³K) · (use + infiltration) · V, nothing else."""
        information = information_for(use_air_change_rate_per_hour=0.0, infiltration_air_change_rate_per_hour=0.6, conditioned_air_volume_in_m3=140.0)

        assert information.heat_conductance_ventilation_in_watt_per_kelvin == 0.34 * (0.0 + 0.6) * 140.0
        origins = information.value_origins()
        for quantity in ("use_air_change_rate_per_hour", "infiltration_air_change_rate_per_hour", "conditioned_air_volume_in_m3"):
            assert origins[quantity].source == ValueOrigin.CONFIGURED

    def test_the_row_volume_is_room_height_times_floor_area_in_the_pinned_order(self) -> None:
        """Without a configured volume the product is the former one, operand by operand."""
        information = information_for(use_air_change_rate_per_hour=0.0, infiltration_air_change_rate_per_hour=0.6)

        assert information.heat_conductance_ventilation_in_watt_per_kelvin == (
            0.34 * (0.0 + 0.6) * GermanSingleFamilyHome.ROOM_HEIGHT_IN_M * information.scaled_conditioned_floor_area_in_m2
        )
        assert information.value_origins()["conditioned_air_volume_in_m3"].source == ValueOrigin.TABULA_ROW

    def test_the_row_rates_are_the_properties_of_the_information(self) -> None:
        """Unset, each rate is the row's, readable by name."""
        information = information_for()

        assert information.use_air_change_rate_per_hour == GermanSingleFamilyHome.USE_AIR_CHANGE_RATE_PER_HOUR
        assert information.air_infiltration_rate_per_hour == GermanSingleFamilyHome.INFILTRATION_AIR_CHANGE_RATE_PER_HOUR


@pytest.mark.base
class TestAdjustmentFactors:
    """A configured transmission adjustment factor is used as given, whatever the U-value's source."""

    def test_a_configured_floor_u_value_keeps_the_fixed_half_without_a_factor(self) -> None:
        """The undocumented 0.5 of a configured floor U-value stays for configs that do not set the factor."""
        information = information_for(floor_u_value_in_watt_per_m2_per_kelvin=0.2)

        assert information.floor_adjustment_factor_from_tabula == 0.5
        assert information.heat_conductance_floor_in_watt_per_kelvin == 0.2 * information.floor_area_in_m2 * 0.5
        assert information.value_origins()["floor_adjustment_factor"].source == ValueOrigin.DERIVED

    def test_a_configured_floor_factor_replaces_the_fixed_half(self) -> None:
        """A floor over outdoor air has the factor 1, and gets it when the config says so."""
        information = information_for(floor_u_value_in_watt_per_m2_per_kelvin=0.2, floor_transmission_adjustment_factor=1.0)

        assert information.floor_adjustment_factor_from_tabula == 1.0
        assert information.heat_conductance_floor_in_watt_per_kelvin == 0.2 * information.floor_area_in_m2 * 1.0
        assert information.value_origins()["floor_adjustment_factor"].source == ValueOrigin.CONFIGURED
        assert information.element_values()["floor"].adjustment_factor_from_tabula_row is False

    def test_a_configured_factor_also_replaces_the_row_column_when_the_u_value_is_the_row_s(self) -> None:
        """The facade's U-value stays the row's average; its factor is the configured 0.7."""
        information = information_for(facade_transmission_adjustment_factor=0.7)
        from_row = information_for()

        assert information.facade_u_value_in_watt_per_m2_per_kelvin == from_row.facade_u_value_in_watt_per_m2_per_kelvin
        assert information.facade_adjustment_factor_from_tabula == 0.7
        assert information.heat_conductance_facade_in_watt_per_kelvin == (information.facade_u_value_in_watt_per_m2_per_kelvin * information.facade_area_in_m2 * 0.7)
        assert information.value_origins()["facade_u_value_in_watt_per_m2_per_kelvin"].source == ValueOrigin.TABULA_ROW
        assert information.value_origins()["wall_adjustment_factor"].source == ValueOrigin.CONFIGURED


@pytest.mark.base
class TestMassAndSurfaceQuantities:
    """C_m, A_m and A_tot are taken as given, and the class table is not consulted for them."""

    def test_the_three_quantities_are_the_configured_ones(self) -> None:
        """The attributes the Building reads are the configured numbers; the Wh/m² form follows C_m."""
        information = information_for(
            thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin=3.0e6,
            effective_mass_area_in_m2=100.0,
            total_internal_surface_area_in_m2=300.0,
        )

        assert information.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin == 3.0e6
        assert information.effective_mass_area_in_m2 == 100.0
        assert information.total_internal_surface_area_in_m2 == 300.0
        assert information.thermal_capacity_of_building_thermal_mass_in_watthour_per_m2_per_kelvin == 3.0e6 / (3.6e3 * information.scaled_conditioned_floor_area_in_m2)
        origins = information.value_origins()
        for quantity in (
            "thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin",
            "effective_mass_area_in_m2",
            "total_internal_surface_area_in_m2",
        ):
            assert origins[quantity].source == ValueOrigin.CONFIGURED

    def test_unset_they_come_from_the_class_table_and_say_so(self) -> None:
        """The default is the medium class times the floor area, recorded as derived, not as the row's."""
        information = information_for()
        origins = information.value_origins()

        assert information.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin == (1.65e5 * information.scaled_conditioned_floor_area_in_m2)
        assert origins["thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin"].source == ValueOrigin.DERIVED
        assert "medium" in origins["thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin"].detail
        assert origins["total_internal_surface_area_in_m2"].source == ValueOrigin.DERIVED


@pytest.mark.base
class TestValueOrigins:
    """The record names every quantity the Building reads and where each came from."""

    def test_a_fully_configured_building_names_the_row_nowhere(self) -> None:
        """Every origin is configured, except the two factors that are always 1 and are derived."""
        origins = information_for(**FullyConfigured.OVERRIDES).value_origins()

        assert set(origins) == FullyConfigured.QUANTITIES
        assert {quantity for quantity, origin in origins.items() if origin.source == ValueOrigin.TABULA_ROW} == set()
        assert {quantity for quantity, origin in origins.items() if origin.source == ValueOrigin.DERIVED} == {
            "window_adjustment_factor",
            "door_adjustment_factor",
        }

    def test_the_minimal_config_takes_the_envelope_from_the_row(self) -> None:
        """Unset, the areas, U-values, windows, rates and volume are the row's; the rest is derived."""
        origins = information_for(absolute_conditioned_floor_area_in_m2=None).value_origins()

        assert set(origins) == FullyConfigured.QUANTITIES
        from_row = {quantity for quantity, origin in origins.items() if origin.source == ValueOrigin.TABULA_ROW}
        assert from_row == {
            "scaled_conditioned_floor_area_in_m2",
            "floor_area_in_m2",
            "facade_area_in_m2",
            "roof_area_in_m2",
            "window_area_in_m2",
            "door_area_in_m2",
            "floor_u_value_in_watt_per_m2_per_kelvin",
            "facade_u_value_in_watt_per_m2_per_kelvin",
            "roof_u_value_in_watt_per_m2_per_kelvin",
            "window_u_value_in_watt_per_m2_per_kelvin",
            "door_u_value_in_watt_per_m2_per_kelvin",
            "floor_adjustment_factor",
            "wall_adjustment_factor",
            "roof_adjustment_factor",
            "windows",
            "use_air_change_rate_per_hour",
            "infiltration_air_change_rate_per_hour",
            "conditioned_air_volume_in_m3",
            "number_of_apartments",
        }
        assert origins["scaled_conditioned_floor_area_in_m2"].detail == "A_C_Ref"
        assert origins["max_thermal_building_demand_in_watt"].source == ValueOrigin.DERIVED

    def test_the_record_is_a_copy(self) -> None:
        """A caller cannot change what the information reports."""
        information = information_for()
        information.value_origins().clear()

        assert set(information.value_origins()) == FullyConfigured.QUANTITIES


@pytest.mark.base
def test_a_config_with_windows_round_trips_through_its_dictionary_form() -> None:
    """The window list is wire format: ``to_dict`` and ``from_dict`` return the same windows."""
    config = GermanSingleFamilyHome.config(**FullyConfigured.OVERRIDES)

    restored = BuildingConfig.from_dict(config.to_dict())

    assert restored.windows == config.windows
    assert restored == config
