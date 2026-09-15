"""Tests of the TABULA building-code lookup, salvaged from the deleted v1 mapping tests.

The lookup is the one piece of the v1 translator that survived the rewrite (decision Q27): it
resolves a country, building type, construction year and refurbishment level onto a TABULA
archetype, working around the Irish rows that carry no door or window geometry and would crash the
``Building`` component. These tests pin the fallbacks, because a silent change of archetype is a
silent change of every U-value the base simulation starts from.
"""

import pytest

from hisim.renovisor.tabula_ie import TabulaLookupError, available_countries, select_building_code

pytestmark = pytest.mark.base


def test_irish_archetypes_are_indexed() -> None:
    """Ireland is in the processed TABULA table, which the whole translation layer assumes."""
    assert "IE" in available_countries()


def test_exact_band_and_variant_are_selected_without_notes() -> None:
    """A construction year inside a usable band selects it directly and records no fallback."""
    selection = select_building_code("IE", "SFH", 1988, 1)

    assert selection.building_code == "IE.N.SFH.07.Gen.ReEx.001.001"
    assert selection.notes == []


def test_missing_age_band_falls_back_to_the_nearest_one() -> None:
    """Irish apartments have no band for 1900, so the nearest usable band is used and reported."""
    selection = select_building_code("IE", "AB", 1900, 1)

    assert selection.building_code == "IE.N.AB.04.Gen.ReEx.001.001"
    assert selection.notes


def test_unusable_rows_fall_back_to_the_nearest_usable_band() -> None:
    """Bands whose rows lack door or window geometry are skipped, with the reason reported."""
    apartment = select_building_code("IE", "AB", 2015, 2)
    assert apartment.building_code == "IE.N.AB.07.Gen.ReEx.001.002"
    assert any("door/window geometry" in note for note in apartment.notes)

    detached = select_building_code("IE", "SFH", 1968, 1)
    assert detached.building_code == "IE.N.SFH.04.Gen.ReEx.001.001"
    assert any("door/window geometry" in note for note in detached.notes)


def test_unknown_country_raises() -> None:
    """A country with no TABULA rows is a lookup failure, not a silent substitution."""
    with pytest.raises(TabulaLookupError, match="ZZ"):
        select_building_code("ZZ", "SFH", 1988, 1)
