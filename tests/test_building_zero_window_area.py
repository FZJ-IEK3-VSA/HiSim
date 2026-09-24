"""The zero-window-area guards in ``BuildingInformation`` (hisim-4g9.1).

A TABULA row whose reference window areas are both zero used to crash ``BuildingInformation``
twice over: the per-direction window scaling divided by the reference window area (findings log
entry 1), and the window U-value's area-weighted average divided by the same zero sum
(``simulation_issues.md`` item 2). Neither division runs for such a row any more, and what
happens instead depends on whether the row has window data anywhere else.

A row with no window data anywhere -- every window column zero -- keeps the window's own
zero-area guard, the same rule the door's descriptor declares: the scaling factor is 0, the only
value consistent with the reference window area of 0 the window area itself was derived from,
and the U-value keeps the raw ``U_Actual_Window_1`` instead of dividing by zero. A zero-area
element has zero conductance either way, so the kept U-value is the material's property, unused
by the physics. A window area *configured* for such a row is refused by name
(``ZeroReferenceWindowAreaError``): it would count in the transmission losses and vanish from
every orientation.

A row that states its windows per direction only -- zero reference areas, non-zero
``A_Window_<direction>`` columns -- is refused by name whether or not a window area is
configured: scaled by 0 it would silently become a building without windows. The real rows of
that kind are ``ES.ME.MFH.05.Gen.ReEx.001.001/.002/.003``, the characterization golden's only
``ZeroReferenceWindowAreaError`` entries of the default sweep.

The real rows with no window data anywhere are the 121 ``DE.DistrictMZLerch`` rows, whose
envelope geometry is zero for *every* element. With the minimal config they pass the window guard
and still crash one division later, on the floor element, which it does not cover
(hisim-4g9.16); with a configured window area they fail with ``ZeroReferenceWindowAreaError``
first. Both stay pinned as ``raises:`` entries of the golden, so the guard's scaling factor of 0
is tested here on a synthetic row.
"""

from typing import Optional

import pandas as pd
import pytest

from hisim.config import ComponentID
from hisim.components.building import BuildingConfig, BuildingInformation, ZeroReferenceWindowAreaError

#: The Spanish multi-family archetype whose TABULA rows list no reference window area at all
#: (``A_Window_1 == A_Window_2 == 0``) while their per-direction areas are filled in.
WINDOWS_PER_DIRECTION_ONLY_CODES = (
    "ES.ME.MFH.05.Gen.ReEx.001.001",
    "ES.ME.MFH.05.Gen.ReEx.001.002",
    "ES.ME.MFH.05.Gen.ReEx.001.003",
)

#: The per-direction window total of those rows [m2]: 18.9 + 18.9 + 55.35 + 60.54 + 0.
WINDOWS_PER_DIRECTION_ONLY_TOTAL_IN_M2 = 153.69

#: An ordinary German single-family home, with both reference window areas present.
NORMAL_GERMAN_CODE = "DE.N.SFH.05.Gen.ReEx.001.001"

#: The German row's reference window area ``A_Window_1 + A_Window_2`` [m2] and its TABULA
#: reference floor area ``A_C_Ref`` [m2].
NORMAL_GERMAN_REFERENCE_WINDOW_AREA_IN_M2 = 27.1
NORMAL_GERMAN_REFERENCE_FLOOR_AREA_IN_M2 = 121.2


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
class TestARowWithWindowsPerDirectionOnly:
    """``ES.ME.MFH.05`` states its windows per direction only, and is refused by name."""

    @pytest.mark.parametrize("building_code", WINDOWS_PER_DIRECTION_ONLY_CODES)
    def test_the_row_is_refused_by_name(self, building_code: str) -> None:
        """Scaled by 0 the row would lose 153.69 m2 of windows; the error names code, columns and total."""
        with pytest.raises(ZeroReferenceWindowAreaError) as refusal:
            BuildingInformation(config=_minimal_config(building_code))

        message = str(refusal.value)
        assert building_code in message
        assert "A_Window_1 and A_Window_2 are both zero" in message
        assert f"{WINDOWS_PER_DIRECTION_ONLY_TOTAL_IN_M2} m2" in message
        assert "only per direction" in message

    def test_a_configured_window_area_does_not_rescue_the_row(self) -> None:
        """The refusal is the row's, not the config's: a window area changes nothing."""
        config = _minimal_config(WINDOWS_PER_DIRECTION_ONLY_CODES[0])
        config.window_area_in_m2 = 25.0

        with pytest.raises(ZeroReferenceWindowAreaError, match="only per direction"):
            BuildingInformation(config=config)


@pytest.mark.base
class TestARowWithNoWindowDataAnywhere:
    """A synthetic row with every window column zero: controlled, and distinct window U-values.

    No real row with no window data anywhere gets past the window step -- the only ones, the
    ``DE.DistrictMZLerch`` rows, crash on their zero floor next (hisim-4g9.16). The synthetic row
    is the ordinary German row with every window column set to 0 and two distinct window
    U-values, which also shows that the guard keeps the *first* raw U-value rather than forming
    some average of the two. It is run through the two derivation pipelines on an instance built
    without ``__init__`` -- no monkeypatching of the shared TABULA cache, no fabricated columns.
    """

    @staticmethod
    def _information_without_window_data(window_area_in_m2: Optional[float] = None) -> BuildingInformation:
        """Run the two derivation pipelines over a synthetic row with every window column zero."""
        frame = BuildingInformation.read_housing_reference_dataframe()
        synthetic_row: pd.DataFrame = frame.loc[frame["Code_BuildingVariant"] == NORMAL_GERMAN_CODE].copy()
        for window_column in [column for column in synthetic_row.columns if column.startswith("A_Window_")]:
            synthetic_row[window_column] = 0.0
        synthetic_row["U_Actual_Window_1"] = 2.0
        synthetic_row["U_Actual_Window_2"] = 1.0

        information = BuildingInformation.__new__(BuildingInformation)
        information.buildingconfig = _minimal_config(NORMAL_GERMAN_CODE)
        information.buildingconfig.window_area_in_m2 = window_area_in_m2
        information.buildingdata_ref = synthetic_row
        information.get_building_area_parameters()
        information.get_building_heat_transfer_parameters()
        return information

    def test_the_scaling_factor_is_zero(self) -> None:
        """There is no reference distribution to rescale, so every direction scales to zero."""
        information = self._information_without_window_data()

        assert information.window_area_in_m2 == 0.0
        assert information.window_scaling_factor == 0.0
        assert information.scaled_window_areas_in_m2 == [0.0, 0.0, 0.0, 0.0, 0.0]

    def test_the_guard_keeps_the_first_u_value_not_an_average(self) -> None:
        """With 2.0 and 1.0 over zero areas, the kept value is 2.0, unaveraged."""
        information = self._information_without_window_data()

        assert information.window_u_value_in_watt_per_m2_per_kelvin == 2.0

    def test_the_window_conductance_is_zero(self) -> None:
        """A zero-area element contributes no heat loss, whatever its U-value."""
        information = self._information_without_window_data()

        assert information.heat_conductance_window_in_watt_per_kelvin == 0.0

    def test_a_configured_window_area_is_refused_by_name(self) -> None:
        """A configured area has no orientation to be distributed over, so the row is refused.

        Scaling it to zero per direction would keep the area in the transmission losses and drop
        it from the solar gains; the building would be inconsistent, so it fails loudly instead.
        """
        with pytest.raises(ZeroReferenceWindowAreaError, match="a window area of 25.0 m2 is configured"):
            self._information_without_window_data(window_area_in_m2=25.0)

    def test_a_configured_window_area_of_zero_is_consistent_and_accepted(self) -> None:
        """Zero configured over zero reference is the one configured value that needs no distribution."""
        information = self._information_without_window_data(window_area_in_m2=0.0)

        assert information.window_scaling_factor == 0.0
        assert information.scaled_window_areas_in_m2 == [0.0, 0.0, 0.0, 0.0, 0.0]


@pytest.mark.base
class TestTheGuardChangesNothingElse:
    """For a row with window areas present, both window divisions run exactly as before."""

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
        """The per-direction areas still rescale by derived area over reference area.

        Twice the reference floor area doubles the window area, so the factor is 2 over the
        row's reference window area of 27.1 m2, and every direction doubles with it.
        """
        information = BuildingInformation(
            config=_minimal_config(
                NORMAL_GERMAN_CODE, absolute_conditioned_floor_area_in_m2=2 * NORMAL_GERMAN_REFERENCE_FLOOR_AREA_IN_M2
            )
        )

        row = information.buildingdata_ref
        assert float(row["A_C_Ref"].values[0]) == NORMAL_GERMAN_REFERENCE_FLOOR_AREA_IN_M2
        assert (
            float(row["A_Window_1"].values[0]) + float(row["A_Window_2"].values[0])
            == NORMAL_GERMAN_REFERENCE_WINDOW_AREA_IN_M2
        )
        assert information.window_scaling_factor == pytest.approx(2.0)
        # South, East, North, West, Horizontal: 6.3, 5.7, 4.1, 8.9 and 0 m2 in the row, doubled.
        assert information.scaled_window_areas_in_m2 == pytest.approx([12.6, 11.4, 8.2, 17.8, 0.0])
