"""TABULA's estimated door for a row without a reference door area (hisim-4g9.1 review).

Many TABULA rows state no door: ``A_Door_1`` is 0 on 241 national generic-example rows (every
Danish and Norwegian one, several Irish, Belgian, French and Slovenian bands) and on many
regional rows. The zero-area guard kept such a building from dividing by zero, but left it
without a door. Every one of those rows carries TABULA's own estimate, ``A_Estim_Door``, and
``BuildingInformation`` uses it as the reference door area now, scaled like every other element
area. Its U-value is the row's ``U_Actual_Door_1`` when that is positive, else the mean door
U-value of the country's national rows of the same refurbishment variant that state a door (an
unrefurbished house must not get a partly refurbished door), else that mean over all their
variants, else a named default of 3.0 W/(m2 K) (TABULA's Irish door construction
``IE.Door.ReEx.01.01``; to be reviewed). The Building's report
says which of these the door came from. A row that has a door is untouched: its area and its
pinned ``(u * area) / area`` U-value are bit-identical to before, which the characterization
golden holds for every such code.
"""

from typing import Optional

import pandas as pd
import pytest

from hisim.config import ComponentID
from hisim.components.building import BuildingConfig, BuildingInformation

#: An Irish terraced house with no door area and no door U-value in its row.
IRISH_ROW_WITHOUT_A_DOOR = "IE.N.TH.04.Gen.ReEx.001.001"
#: Its ``A_Estim_Door`` [m2] and ``A_C_Ref`` [m2].
IRISH_ESTIMATED_DOOR_AREA_IN_M2 = 2.7
IRISH_REFERENCE_FLOOR_AREA_IN_M2 = 120.0

#: A Danish house: Denmark states no door on any national row, so the default applies.
DANISH_ROW_WITHOUT_A_DOOR = "DK.N.SFH.01.Gen.ReEx.001.001"
DANISH_ESTIMATED_DOOR_AREA_IN_M2 = 2.8

#: A Belgian block with no door area but a door U-value of its own (4.0 W/(m2 K)).
BELGIAN_ROW_WITHOUT_A_DOOR_AREA = "BE.N.AB.02.Gen.ReEx.001.001"
BELGIAN_ESTIMATED_DOOR_AREA_IN_M2 = 10.3
BELGIAN_ROW_DOOR_U_VALUE = 4.0

#: An ordinary German single-family home with a door of its own: 2.1 m2 at 3.0 W/(m2 K).
GERMAN_ROW_WITH_A_DOOR = "DE.N.SFH.05.Gen.ReEx.001.001"


def _minimal_config(
    building_code: str, absolute_conditioned_floor_area_in_m2: Optional[float] = None
) -> BuildingConfig:
    """The minimal config of the characterization harness, for one building code."""
    return BuildingConfig(
        component_id=ComponentID(name="Building"),
        building_code=building_code,
        building_heat_capacity_class="medium",
        initial_internal_temperature_in_celsius=22.0,
        heating_reference_temperature_in_celsius=-7.0,
        absolute_conditioned_floor_area_in_m2=absolute_conditioned_floor_area_in_m2,
        total_base_area_in_m2=None,
        number_of_apartments=None,
        max_thermal_building_demand_in_watt=None,
        floor_u_value_in_watt_per_m2_per_kelvin=None,
        floor_area_in_m2=None,
        facade_u_value_in_watt_per_m2_per_kelvin=None,
        facade_area_in_m2=None,
        roof_u_value_in_watt_per_m2_per_kelvin=None,
        roof_area_in_m2=None,
        window_u_value_in_watt_per_m2_per_kelvin=None,
        window_area_in_m2=None,
        door_u_value_in_watt_per_m2_per_kelvin=None,
        door_area_in_m2=None,
        set_heating_temperature_in_celsius=20.0,
        set_cooling_temperature_in_celsius=25.0,
        enable_opening_windows=False,
    )


@pytest.mark.base
class TestTheEstimatedDoorArea:
    """A row without ``A_Door_1`` gets ``A_Estim_Door``, scaled like every other element area."""

    def test_the_irish_row_gets_tabulas_estimated_door(self) -> None:
        """The row's own estimate, unscaled at the reference floor area."""
        information = BuildingInformation(config=_minimal_config(IRISH_ROW_WITHOUT_A_DOOR))

        assert float(information.buildingdata_ref["A_Door_1"].values[0]) == 0.0, "the row states no door"
        assert information.door_area_in_m2 == IRISH_ESTIMATED_DOOR_AREA_IN_M2
        assert information.heat_conductance_door_in_watt_per_kelvin > 0.0

    def test_the_estimate_scales_with_the_conditioned_floor_area(self) -> None:
        """Twice the reference floor area, twice the estimated door."""
        information = BuildingInformation(
            config=_minimal_config(
                IRISH_ROW_WITHOUT_A_DOOR, absolute_conditioned_floor_area_in_m2=2 * IRISH_REFERENCE_FLOOR_AREA_IN_M2
            )
        )

        assert information.door_area_in_m2 == pytest.approx(2 * IRISH_ESTIMATED_DOOR_AREA_IN_M2)

    def test_a_configured_door_area_still_wins(self) -> None:
        """The estimate stands in for the row's reference area, never for the config's."""
        config = _minimal_config(IRISH_ROW_WITHOUT_A_DOOR)
        config.door_area_in_m2 = 4.0
        information = BuildingInformation(config=config)

        assert information.door_area_in_m2 == 4.0
        assert information.door_u_value_in_watt_per_m2_per_kelvin == pytest.approx(2.87, abs=0.005)


@pytest.mark.base
class TestTheEstimatedDoorsUValue:
    """The row's U-value, else the national mean of its country, else the reviewed-later default."""

    def test_the_rows_own_u_value_is_used_when_it_states_one(self) -> None:
        """The Belgian row lists 4.0 W/(m2 K) for a door it gives no area to."""
        information = BuildingInformation(config=_minimal_config(BELGIAN_ROW_WITHOUT_A_DOOR_AREA))

        assert information.door_area_in_m2 == BELGIAN_ESTIMATED_DOOR_AREA_IN_M2
        assert information.door_u_value_in_watt_per_m2_per_kelvin == BELGIAN_ROW_DOOR_U_VALUE

    def test_an_irish_row_without_one_gets_the_irish_mean_of_its_variant(self) -> None:
        """No door U-value in the row: the mean over the Irish ``001`` rows that state a door.

        The existing state's doors average 2.87 W/(m2 K); every variant together, refurbished ones
        included, would give the unrefurbished house a better door of 2.28 W/(m2 K).
        """
        information = BuildingInformation(config=_minimal_config(IRISH_ROW_WITHOUT_A_DOOR))

        irish_existing_state_mean = BuildingInformation.national_mean_door_u_values()[("IE", "001")]
        assert irish_existing_state_mean == pytest.approx(2.87, abs=0.005)
        assert information.door_u_value_in_watt_per_m2_per_kelvin == irish_existing_state_mean

    def test_a_variant_without_a_door_in_its_country_falls_back_to_all_variants(self) -> None:
        """Variant ``099`` states no Irish door, so the mean over every Irish variant is used.

        No real doorless row needs this fallback today, so the Irish row runs through the two
        derivation pipelines under a code of that variant, on an instance built without
        ``__init__`` -- no monkeypatching of the shared TABULA cache.
        """
        frame = BuildingInformation.read_housing_reference_dataframe()
        row: pd.DataFrame = frame.loc[frame["Code_BuildingVariant"] == IRISH_ROW_WITHOUT_A_DOOR].copy()
        information = BuildingInformation.__new__(BuildingInformation)
        information.buildingconfig = _minimal_config("IE.N.TH.04.Gen.ReEx.001.099")
        information.buildingdata_ref = row
        information.get_building_area_parameters()
        information.get_building_heat_transfer_parameters()

        means = BuildingInformation.national_mean_door_u_values()
        assert ("IE", "099") not in means
        assert information.door_u_value_in_watt_per_m2_per_kelvin == means[("IE", None)]
        assert means[("IE", None)] == pytest.approx(2.28, abs=0.005)
        assert "of all variants" in information.door_report_lines[1]
        assert "none of variant 099 states one" in information.door_report_lines[1]

    def test_a_danish_row_gets_the_default(self) -> None:
        """Denmark states no door anywhere, so the named default of 3.0 W/(m2 K) applies."""
        information = BuildingInformation(config=_minimal_config(DANISH_ROW_WITHOUT_A_DOOR))

        assert information.door_area_in_m2 == DANISH_ESTIMATED_DOOR_AREA_IN_M2
        assert information.door_u_value_in_watt_per_m2_per_kelvin == 3.0
        assert BuildingInformation.DEFAULT_ESTIMATED_DOOR_U_VALUE_IN_WATT_PER_M2_PER_KELVIN == 3.0

    def test_a_configured_door_u_value_still_wins(self) -> None:
        """A U-value from the config is used as it stands, over the estimated door's area."""
        config = _minimal_config(DANISH_ROW_WITHOUT_A_DOOR)
        config.door_u_value_in_watt_per_m2_per_kelvin = 1.4
        information = BuildingInformation(config=config)

        assert information.door_area_in_m2 == DANISH_ESTIMATED_DOOR_AREA_IN_M2
        assert information.door_u_value_in_watt_per_m2_per_kelvin == 1.4

    @pytest.mark.parametrize(
        "country, variant, mean_u_value",
        [
            ("IE", "001", 2.87),
            ("FR", "001", 3.05),
            ("SI", "001", 2.08),
            ("BE", None, 3.34),
            ("FR", None, 2.2),
            ("IE", None, 2.28),
            ("NO", None, 3.5),
            ("SI", None, 1.57),
        ],
    )
    def test_the_national_means_of_the_countries_with_doorless_rows(
        self, country: str, variant: Optional[str], mean_u_value: float
    ) -> None:
        """Per variant, and over every variant (``None``), of the national rows that state a door."""
        means = BuildingInformation.national_mean_door_u_values()
        assert means[(country, variant)] == pytest.approx(mean_u_value, abs=0.01)

    def test_denmark_has_no_national_mean(self) -> None:
        """No Danish national row states a door, so Denmark falls through to the default."""
        means = BuildingInformation.national_mean_door_u_values()
        assert not [key for key in means if key[0] == "DK"]


@pytest.mark.base
class TestTheReportSaysWhereTheDoorCameFrom:
    """The Building's report lines name the estimate and the U-value's origin."""

    def test_an_estimated_door_with_a_national_mean(self) -> None:
        """Area from ``A_Estim_Door``, U-value from the Irish national rows."""
        information = BuildingInformation(config=_minimal_config(IRISH_ROW_WITHOUT_A_DOOR))
        area_line, u_value_line = information.door_report_lines

        assert area_line.startswith("Door Area [m2]: 2.70 ")
        assert "A_Estim_Door" in area_line
        assert u_value_line.startswith("Door U-Value [W/m2K]: 2.87 ")
        assert "IE national TABULA rows of variant 001" in u_value_line

    def test_an_estimated_door_with_the_default(self) -> None:
        """The default says it is one, and that it is to be reviewed."""
        _, u_value_line = BuildingInformation(config=_minimal_config(DANISH_ROW_WITHOUT_A_DOOR)).door_report_lines

        assert "default, to be reviewed" in u_value_line
        assert "IE.Door.ReEx.01.01" in u_value_line

    def test_an_estimated_door_with_the_rows_u_value(self) -> None:
        """The row's own U-value is named as the row's."""
        information = BuildingInformation(config=_minimal_config(BELGIAN_ROW_WITHOUT_A_DOOR_AREA))
        _, u_value_line = information.door_report_lines

        assert "TABULA row, U_Actual_Door_1, although the row states no door area" in u_value_line

    def test_a_row_with_a_door(self) -> None:
        """The ordinary case says nothing about estimates."""
        area_line, u_value_line = BuildingInformation(config=_minimal_config(GERMAN_ROW_WITH_A_DOOR)).door_report_lines

        assert area_line == "Door Area [m2]: 2.10 (TABULA reference door area A_Door_1)"
        assert u_value_line == "Door U-Value [W/m2K]: 3.00 (TABULA row, U_Actual_Door_1)"


@pytest.mark.base
class TestARowWithADoorIsUnchanged:
    """``A_Door_1 > 0``: the reference door and the pinned round trip, exactly as before."""

    def test_the_german_rows_door_is_its_own(self) -> None:
        """Area ``A_Door_1`` and U-value ``(u * area) / area``, bit for bit."""
        information = BuildingInformation(config=_minimal_config(GERMAN_ROW_WITH_A_DOOR))

        row = information.buildingdata_ref
        door_area = float(row["A_Door_1"].values[0])
        door_u_value = float(row["U_Actual_Door_1"].values[0])
        assert (door_area, door_u_value) == (2.1, 3.0), "the test row is an ordinary one"
        assert information.door_area_in_m2 == door_area
        assert information.door_u_value_in_watt_per_m2_per_kelvin == (door_u_value * door_area) / door_area
