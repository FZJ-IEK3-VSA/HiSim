"""The zero-window-area guards in ``BuildingInformation`` (hisim-4g9.1).

A TABULA row whose reference window areas are both zero used to crash ``BuildingInformation``
twice over: the per-direction window scaling divided by the reference window area (findings log
entry 1), and the window U-value's area-weighted average divided by the same zero sum
(``simulation_issues.md`` item 2). Both divisions are guarded now, on the pattern of the door's
zero-area guard: the scaling factor is 0 -- the only value consistent with the reference window
area of 0 the window area itself was derived from -- and the U-value keeps the raw
``U_Actual_Window_1`` instead of dividing by zero. A zero-area element has zero conductance
either way, so the kept U-value is the material's property, unused by the physics.

The real rows this covers are ``ES.ME.MFH.05.Gen.ReEx.001.001/.002/.003``, the only
generic-example rows of the table with zero reference window areas; they used to raise and are
full snapshot entries of the characterization golden now. The 121 ``DE.DistrictMZLerch`` rows,
whose envelope geometry is zero for *every* element, still crash -- one division later, on the
floor element, which this guard does not cover (hisim-4g9.15) -- and stay pinned as
``raises:`` entries.
"""

import pandas as pd
import pytest

from hisim.config import ComponentID
from hisim.components.building import BuildingConfig, BuildingInformation

#: The Spanish multi-family archetype whose TABULA row lists no reference window area at all
#: (``A_Window_1 == A_Window_2 == 0``) while its per-direction areas are filled in.
ZERO_WINDOW_AREA_CODE = "ES.ME.MFH.05.Gen.ReEx.001.001"

#: An ordinary German single-family home, with both reference window areas present.
NORMAL_GERMAN_CODE = "DE.N.SFH.05.Gen.ReEx.001.001"


def _minimal_config(building_code: str) -> BuildingConfig:
    """The minimal config of the characterization harness, for one building code."""
    return BuildingConfig(
        component_id=ComponentID(name="Building"),
        building_code=building_code,
        building_heat_capacity_class="medium",
        initial_internal_temperature_in_celsius=22.0,
        heating_reference_temperature_in_celsius=-7.0,
        absolute_conditioned_floor_area_in_m2=None,
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
class TestTheZeroWindowAreaGuard:
    """A TABULA row with zero window areas initializes instead of dividing by zero."""

    def test_the_real_zero_area_row_initializes(self) -> None:
        """``ES.ME.MFH.05`` used to raise in the window scaling; the whole class now builds."""
        information = BuildingInformation(config=_minimal_config(ZERO_WINDOW_AREA_CODE))

        assert information.window_area_in_m2 == 0.0

    def test_the_scaling_factor_is_zero_for_a_zero_reference_area(self) -> None:
        """There is no reference distribution to rescale, so every direction scales to zero."""
        information = BuildingInformation(config=_minimal_config(ZERO_WINDOW_AREA_CODE))

        assert information.window_scaling_factor == 0.0
        assert information.scaled_window_areas_in_m2 == [0.0, 0.0, 0.0, 0.0, 0.0]

    def test_the_u_value_is_the_raw_first_tabula_u_value(self) -> None:
        """The weighted average cannot be formed; the guard keeps ``U_Actual_Window_1``."""
        information = BuildingInformation(config=_minimal_config(ZERO_WINDOW_AREA_CODE))

        raw_u_value = float(information.buildingdata_ref["U_Actual_Window_1"].values[0])
        assert raw_u_value == 3.37, "the test row is the one the guard is for"
        assert information.window_u_value_in_watt_per_m2_per_kelvin == raw_u_value

    def test_the_window_conductance_is_zero_either_way(self) -> None:
        """A zero-area element contributes no heat loss, whatever its U-value."""
        information = BuildingInformation(config=_minimal_config(ZERO_WINDOW_AREA_CODE))

        assert information.heat_conductance_window_in_watt_per_kelvin == 0.0

    def test_a_configured_window_area_cannot_rescue_the_scaling(self) -> None:
        """The divisor is the *reference* area, so a configured area still finds no distribution."""
        config = _minimal_config(ZERO_WINDOW_AREA_CODE)
        config.window_area_in_m2 = 25.0
        information = BuildingInformation(config=config)

        assert information.window_area_in_m2 == 25.0
        assert information.window_scaling_factor == 0.0
        assert information.scaled_window_areas_in_m2 == [0.0, 0.0, 0.0, 0.0, 0.0]


@pytest.mark.base
class TestTheGuardOnASyntheticRow:
    """The synthetic-row test: controlled, distinct window U-values over zero areas.

    The real zero-area row carries ``U_Actual_Window_1 == U_Actual_Window_2``, so it cannot
    show that the guard keeps the *first* raw U-value rather than forming some average of the
    two. A synthetic reference row with distinct U-values can; it is the real row with its two
    window U-values replaced, run through the two derivation pipelines on an instance built
    without ``__init__`` -- no monkeypatching of the shared TABULA cache, no fabricated columns.
    """

    @staticmethod
    def _information_with_distinct_window_u_values() -> BuildingInformation:
        """Run the two derivation pipelines over a synthetic zero-window-area row."""
        frame = BuildingInformation.read_housing_reference_dataframe()
        synthetic_row: pd.DataFrame = frame.loc[
            frame["Code_BuildingVariant"] == ZERO_WINDOW_AREA_CODE
        ].copy()
        synthetic_row["U_Actual_Window_1"] = 2.0
        synthetic_row["U_Actual_Window_2"] = 1.0

        information = BuildingInformation.__new__(BuildingInformation)
        information.buildingconfig = _minimal_config(ZERO_WINDOW_AREA_CODE)
        information.buildingdata_ref = synthetic_row
        information.get_building_area_parameters()
        information.get_building_heat_transfer_parameters()
        return information

    def test_the_guard_keeps_the_first_u_value_not_an_average(self) -> None:
        """With 2.0 and 1.0 over zero areas, the kept value is 2.0, unaveraged."""
        information = self._information_with_distinct_window_u_values()

        assert information.window_u_value_in_watt_per_m2_per_kelvin == 2.0

    def test_the_conductance_is_zero_either_way(self) -> None:
        """The synthetic row's zero areas still contribute no heat loss."""
        information = self._information_with_distinct_window_u_values()

        assert information.heat_conductance_window_in_watt_per_kelvin == 0.0
        assert information.window_scaling_factor == 0.0


@pytest.mark.base
class TestTheGuardChangesNothingElse:
    """For a row with window areas present, both windows divisions run exactly as before."""

    def test_a_normal_german_codes_window_u_value_is_unchanged(self) -> None:
        """With ``a1 + a2 > 0`` the weighted average is the same division it always was."""
        information = BuildingInformation(config=_minimal_config(NORMAL_GERMAN_CODE))

        row = information.buildingdata_ref
        area_1 = float(row["A_Window_1"].values[0])
        area_2 = float(row["A_Window_2"].values[0])
        u_value_1 = float(row["U_Actual_Window_1"].values[0])
        u_value_2 = float(row["U_Actual_Window_2"].values[0])
        assert area_1 > 0 and area_2 == 0.0, "the test row is an ordinary one"

        expected_u_value = (u_value_1 * area_1 + u_value_2 * area_2) / (area_1 + area_2)
        assert information.window_u_value_in_watt_per_m2_per_kelvin == expected_u_value

    def test_a_normal_german_codes_window_scaling_is_unchanged(self) -> None:
        """The per-direction areas still rescale by derived area over reference area."""
        information = BuildingInformation(config=_minimal_config(NORMAL_GERMAN_CODE))

        row = information.buildingdata_ref
        reference_area = float(row["A_Window_1"].values[0]) + float(row["A_Window_2"].values[0])
        assert information.window_scaling_factor == information.window_area_in_m2 / reference_area
        assert information.scaled_window_areas_in_m2 == [
            float(row["A_Window_" + direction].values[0]) * information.window_scaling_factor
            for direction in information.windows_directions
        ]
