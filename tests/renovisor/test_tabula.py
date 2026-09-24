"""T-TABULA: which archetype a dwelling is simulated as, and what that approximated.

Three rules. The typology comes from the kind of dwelling (three of the six answers have no
typology of their own and are approximations); the age band is the one whose year range contains
the construction year, clamped at both ends; the variant is always ``001``. Every generic-example
row is selectable: the ``Building`` component guards every zero envelope area (hisim-4g9.1), so
the former usable-row workaround -- a row without door or window geometry was skipped for the
nearest band that had one -- is gone.

``IE.N.SFH.05`` (1967-1977), the row the workaround used to skip for the mockup's 1975 house, is
the example the specification names on both sides.
"""

from typing import Optional

import pytest

from hisim.renovisor.constants import DesignTemperatures
from hisim.renovisor.tabula import (
    BuildingCodeSelector,
    TabulaIndex,
    TabulaTypology,
    TabulaUnresolvable,
)
from hisim.renovisor.vocabulary import BuildingType


def select(
    building_type: BuildingType,
    year: int,
    country: str = "IE",
    requested_code: Optional[str] = None,
):
    """Return the selection for one dwelling, with the arguments named for readability."""
    return BuildingCodeSelector.select(
        country=country,
        building_type=building_type,
        construction_year=year,
        requested_code=requested_code,
    )


@pytest.mark.base
class TestTheIndex:
    """What the processed TABULA table carries, read as HiSim's own reader reads it."""

    def test_every_country_with_a_generic_typology_is_indexed(self) -> None:
        """Seventeen countries carry ``.N.`` codes; Ireland and the Netherlands are two of them."""
        countries = TabulaIndex.countries()
        assert len(countries) == 17
        assert {"IE", "NL", "DE"} <= countries

    def test_spain_has_no_generic_typology_at_all(self) -> None:
        """``ES`` is in the request schema and not in the table, which is a refusal, not a gap."""
        assert "ES" not in TabulaIndex.countries()

    def test_ireland_carries_the_three_typologies_the_request_can_name(self) -> None:
        """Detached and bungalow are SFH, semi-detached and terraced TH, apartment AB."""
        assert TabulaIndex.typologies("IE") >= {"SFH", "TH", "AB"}

    def test_every_simulable_country_has_a_reviewed_design_temperature(self) -> None:
        """Every country a request can name carries a reviewed design temperature.

        TABULA cannot supply the design condition -- its ``Theta_e_Base`` is the 12 °C degree-day
        base -- so without that constant the weather cannot be built.
        """
        for country in TabulaIndex.countries() & {"IE", "NL", "ES"}:
            assert country in DesignTemperatures.BY_COUNTRY, country


@pytest.mark.base
class TestTheTypology:
    """Three of the six kinds of dwelling have no typology of their own."""

    @pytest.mark.parametrize(
        "building_type, typology, approximated",
        [
            (BuildingType.DETACHED_SFH, "SFH", False),
            (BuildingType.TERRACED_SFH, "TH", False),
            (BuildingType.APARTMENT, "AB", False),
            (BuildingType.SEMI_DETACHED_SFH, "TH", True),
            (BuildingType.BUNGALOW, "SFH", True),
            (BuildingType.OTHER, "SFH", True),
        ],
    )
    def test_each_kind_of_dwelling_maps_as_the_contract_says(
        self, building_type: BuildingType, typology: str, approximated: bool
    ) -> None:
        """§3.3's table, and which three of the six the report has to call approximated."""
        assert TabulaTypology.of(building_type) == (typology, approximated)

    def test_an_approximated_typology_is_said_out_loud(self) -> None:
        """A bungalow simulated as a single-family house is a note, not a silent substitution."""
        selection = select(BuildingType.BUNGALOW, 1990)

        assert selection.is_approximated()
        assert any("bungalow" in note for note in selection.notes)


@pytest.mark.base
class TestTheBand:
    """The band containing the construction year, clamped at both ends."""

    @pytest.mark.parametrize(
        "year, band",
        [(1900, "02"), (1929, "02"), (1930, "03"), (1949, "03"), (1950, "04"), (1966, "04")],
    )
    def test_the_boundaries_of_the_irish_single_family_bands(self, year: int, band: str) -> None:
        """A year on a boundary belongs to the band whose range contains it, not to its neighbour."""
        assert select(BuildingType.DETACHED_SFH, year).code == f"IE.N.SFH.{band}.Gen.ReEx.001.001"

    @pytest.mark.parametrize("year, band", [(1950, "04"), (1978, "06"), (1983, "07")])
    def test_the_german_single_family_bands_are_indexed_too(self, year: int, band: str) -> None:
        """The index is not Irish: every ``.N.`` country resolves the same way."""
        code = select(BuildingType.DETACHED_SFH, year, country="DE").code
        assert code.startswith(f"DE.N.SFH.{band}.")

    def test_a_year_before_every_band_is_clamped_with_a_note(self) -> None:
        """Outside the covered range the nearest band is used, and the note says which."""
        selection = select(BuildingType.APARTMENT, 1700)

        assert selection.is_approximated()
        assert any("nearest band" in note for note in selection.notes)

    def test_the_variant_is_always_the_existing_state(self) -> None:
        """Under rule 5 every U-value travels in the request, so ``002``/``003`` carry nothing."""
        assert select(BuildingType.DETACHED_SFH, 1990).code.endswith(".Gen.ReEx.001.001")


@pytest.mark.base
class TestEveryRowIsSelectable:
    """The former usable-row rule is gone: no row is refused for its geometry any more.

    ``IE.N.SFH.05`` has no door or window area in the table; it used to be skipped for a
    neighbouring band unless the request carried both areas. The ``Building`` guards those zero
    areas now, so the exact band is used again, with or without areas.
    """

    def test_the_mockups_house_lands_on_its_own_band(self) -> None:
        """1975 is band 05's range, and band 05 is used exactly, with no substitution note."""
        selection = select(BuildingType.DETACHED_SFH, 1975)

        assert selection.code == "IE.N.SFH.05.Gen.ReEx.001.001"
        assert not selection.is_approximated()

    def test_an_expert_code_for_the_zero_area_row_is_accepted(self) -> None:
        """The expert override used to refuse this row as unusable; there is nothing to refuse."""
        selection = select(
            BuildingType.DETACHED_SFH, 1975, requested_code="IE.N.SFH.05.Gen.ReEx.001.001"
        )

        assert selection.code == "IE.N.SFH.05.Gen.ReEx.001.001"
        assert not selection.notes

    def test_an_expert_code_skips_the_derivation_entirely(self) -> None:
        """``tabula_building_code`` is an override: it is used as it stands, with no notes."""
        selection = select(
            BuildingType.APARTMENT, 1900, requested_code="IE.N.SFH.08.Gen.ReEx.001.001"
        )

        assert selection.code == "IE.N.SFH.08.Gen.ReEx.001.001"
        assert not selection.notes

    def test_a_code_the_table_does_not_carry_is_refused(self) -> None:
        """An override still has to name a row that exists."""
        with pytest.raises(TabulaUnresolvable):
            select(BuildingType.DETACHED_SFH, 1975, requested_code="IE.N.SFH.99.Gen.ReEx.001.001")

    def test_a_country_with_no_typology_is_refused(self) -> None:
        """Spain has no ``.N.`` codes, which the request validation turns into a named problem."""
        with pytest.raises(TabulaUnresolvable):
            select(BuildingType.DETACHED_SFH, 1990, country="ES")
