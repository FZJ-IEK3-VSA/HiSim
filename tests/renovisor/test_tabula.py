"""T-TABULA: which archetype a dwelling is simulated as, and what that approximated.

Three rules. The typology comes from the kind of dwelling (three of the six answers have no
typology of their own and are approximations); the age band is the one whose year range contains
the construction year, clamped at both ends; the variant is the one ``building.retrofit_status``
selects (``001`` when absent, ``001`` with a note where the band lacks it). Every generic-example
row is selectable: the ``Building`` component guards a zero door or window area and gives a row
without a door area TABULA's estimated door (hisim-4g9.1), so the former usable-row workaround --
a row without door or window geometry was skipped for the nearest band that had one -- is gone.

``IE.N.SFH.05`` (1967-1977), the row the workaround used to skip for the mockup's 1975 house, is
the example the specification names on both sides.
"""

from typing import Optional

import pytest

from hisim.renovisor.constants import DesignTemperatures
from hisim.renovisor.tabula import (
    ArchetypeEnvelope,
    BuildingCodeSelector,
    TabulaIndex,
    TabulaTypology,
    TabulaUnresolvable,
    TabulaVariantConflict,
)
from hisim.renovisor.vocabulary import BuildingType, RetrofitStatus, ThermalElement


def select(
    building_type: BuildingType,
    year: int,
    country: str = "IE",
    requested_code: Optional[str] = None,
    retrofit_status: Optional[RetrofitStatus] = None,
):
    """Return the selection for one dwelling, with the arguments named for readability."""
    return BuildingCodeSelector.select(
        country=country,
        building_type=building_type,
        construction_year=year,
        requested_code=requested_code,
        retrofit_status=retrofit_status,
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

    def test_without_a_retrofit_status_the_variant_is_the_existing_state(self) -> None:
        """Absent, ``retrofit_status`` is ``unrenovated``: variant ``001``."""
        assert select(BuildingType.DETACHED_SFH, 1990).code.endswith(".Gen.ReEx.001.001")


@pytest.mark.base
class TestTheVariant:
    """``building.retrofit_status`` selects the TABULA variant, the code's last three digits (§5.3 step 3)."""

    @pytest.mark.parametrize(
        "status, variant",
        [
            (RetrofitStatus.UNRENOVATED, "001"),
            (RetrofitStatus.USUAL_REFURB, "002"),
            (RetrofitStatus.ADVANCED_REFURB, "003"),
        ],
    )
    def test_each_status_selects_its_variant(self, status: RetrofitStatus, variant: str) -> None:
        """The mockup's band carries all three variants, so each is used exactly, with no note."""
        selection = select(BuildingType.DETACHED_SFH, 1975, retrofit_status=status)

        assert selection.code == f"IE.N.SFH.05.Gen.ReEx.001.{variant}"
        assert selection.variant == variant
        assert selection.retrofit_status is status
        assert selection.variant_note is None

    def test_every_variant_of_a_band_is_indexed(self) -> None:
        """The index knows which variants a band has, not only its ``001``."""
        bands = {band.band: band for band in TabulaIndex.bands()[("IE", "SFH")]}

        assert {"001", "002", "003"} <= bands["05"].variants
        assert "002" not in bands["10"].variants
        assert "003" in bands["10"].variants

    @pytest.mark.parametrize(
        "country, building_type", [("IE", BuildingType.DETACHED_SFH), ("NL", BuildingType.TERRACED_SFH)]
    )
    def test_a_band_without_the_wanted_variant_takes_the_existing_state_with_a_note(
        self, country: str, building_type: BuildingType
    ) -> None:
        """The newest Irish, Dutch and Belgian bands have no ``002``: ``001`` stands in, and the note names the gap."""
        selection = select(building_type, 2015, country=country, retrofit_status=RetrofitStatus.USUAL_REFURB)

        assert selection.code.endswith(".Gen.ReEx.001.001")
        assert selection.variant_note is not None
        assert "no variant 002 (usual_refurb)" in selection.variant_note
        assert not selection.is_approximated(), "the band and the typology were not approximated"

    def test_the_bands_without_the_usual_refurbishment_are_the_newest_irish_dutch_and_belgian_ones(self) -> None:
        """The capability document announces ``usual_refurb`` approximated because of exactly these bands."""
        expected = (
            "BE.N.AB.05", "BE.N.MFH.05", "BE.N.SFH.05", "BE.N.TH.05",
            "IE.N.AB.10", "IE.N.SFH.10",
            "NL.N.AB.06", "NL.N.MFH.06", "NL.N.SFH.06", "NL.N.TH.06",
        )

        assert TabulaIndex.bands_without("002") == expected
        assert not TabulaIndex.bands_without("001")
        note = select(BuildingType.DETACHED_SFH, 2015, retrofit_status=RetrofitStatus.USUAL_REFURB).variant_note
        assert note is not None and all(stem in note for stem in expected)

    def test_an_advanced_refurbishment_of_the_newest_band_has_its_own_variant(self) -> None:
        """``003`` is present wherever ``002`` is, and in the newest Irish band without it."""
        selection = select(BuildingType.DETACHED_SFH, 2015, retrofit_status=RetrofitStatus.ADVANCED_REFURB)

        assert selection.code == "IE.N.SFH.10.Gen.ReEx.001.003"
        assert selection.variant_note is None

    def test_a_code_contradicting_the_stated_status_is_refused(self) -> None:
        """The code's variant and the status's must agree when both are stated."""
        with pytest.raises(TabulaVariantConflict, match="002.*003|003.*002"):
            select(
                BuildingType.DETACHED_SFH,
                1975,
                requested_code="IE.N.SFH.05.Gen.ReEx.001.002",
                retrofit_status=RetrofitStatus.ADVANCED_REFURB,
            )

    def test_a_code_agreeing_with_the_stated_status_is_accepted(self) -> None:
        """Stating both is redundant, not wrong."""
        selection = select(
            BuildingType.DETACHED_SFH,
            1975,
            requested_code="IE.N.SFH.05.Gen.ReEx.001.002",
            retrofit_status=RetrofitStatus.USUAL_REFURB,
        )

        assert selection.code == "IE.N.SFH.05.Gen.ReEx.001.002"

    def test_without_a_status_the_codes_own_variant_stands(self) -> None:
        """Absent, the status does not override the expert's code with ``001``."""
        selection = select(BuildingType.DETACHED_SFH, 1975, requested_code="IE.N.SFH.05.Gen.ReEx.001.003")

        assert selection.variant == "003"
        assert selection.retrofit_status is RetrofitStatus.ADVANCED_REFURB


@pytest.mark.base
class TestTheArchetypeEnvelope:
    """What the ``Building`` makes of a row, asked of the ``Building`` itself.

    ``IE.N.SFH.05`` (1967-77) in its three variants: wall U 1.78 / 0.32 / 0.204 W/(m2K), air
    infiltration 0.4 / 0.2 / 0.1 1/h and thermal-bridge surcharge 0.15 / 0.15 / 0.10 W/(m2K).
    """

    @pytest.mark.parametrize(
        "variant, wall, infiltration, bridging",
        [("001", 1.78, 0.4, 0.15), ("002", 0.32, 0.2, 0.15), ("003", 0.204, 0.1, 0.1)],
    )
    def test_the_row_of_each_variant(self, variant: str, wall: float, infiltration: float, bridging: float) -> None:
        """The values the translator reports are the row's, as the Building reads it."""
        envelope = ArchetypeEnvelope.of(f"IE.N.SFH.05.Gen.ReEx.001.{variant}", 140)

        assert envelope.u_value(ThermalElement.FACADE) == pytest.approx(wall)
        assert envelope.air_infiltration_rate_per_hour == pytest.approx(infiltration)
        assert envelope.thermal_bridging_surcharge_in_watt_per_m2_per_kelvin == pytest.approx(bridging)

    def test_the_u_values_fall_from_unrenovated_to_advanced(self) -> None:
        """No element gets worse with the refurbishment, and the walls, roof and windows get better."""
        envelopes = [
            ArchetypeEnvelope.of(f"IE.N.SFH.05.Gen.ReEx.001.{variant}", 140) for variant in ("001", "002", "003")
        ]

        for element in ThermalElement:
            values = [envelope.u_value(element) for envelope in envelopes]
            assert values == sorted(values, reverse=True), element
        for element in (ThermalElement.FACADE, ThermalElement.ROOF, ThermalElement.WINDOW):
            assert envelopes[0].u_value(element) > envelopes[2].u_value(element)

    def test_a_row_without_a_door_u_value_reports_the_estimated_door(self) -> None:
        """``IE.N.SFH.05`` states a door U-value of 0 in every variant; the Building estimates one."""
        for variant in ("001", "002", "003"):
            door = ArchetypeEnvelope.of(f"IE.N.SFH.05.Gen.ReEx.001.{variant}", 140).elements[ThermalElement.DOOR]

            assert door.u_value_in_watt_per_m2_per_kelvin > 0
            assert "mean U_Actual_Door_1" in door.origin
            assert f"variant {variant}" in door.origin

    def test_the_floor_keeps_the_rows_adjustment_factor(self) -> None:
        """An element left to the row keeps the row's b_Transmission, not the fixed 0.5 of a stated floor."""
        floor = ArchetypeEnvelope.of("IE.N.SFH.05.Gen.ReEx.001.001", 140).elements[ThermalElement.FLOOR]

        assert "U_Actual_Floor_1" in floor.origin
        assert floor.adjustment_factor > 0

    def test_a_written_u_value_switches_to_the_fixed_factor_whatever_the_rows(self) -> None:
        """AT.N.SFH.01's floor has b_Transmission 1; a stated or insulated floor uses the fixed 0.5 instead.

        Kept by owner decision (2026-09-26); the mapping note of a defaulted-then-insulated
        element names both numbers.
        """
        elements = ArchetypeEnvelope.of("AT.N.SFH.01.Gen.ReEx.001.001", 140).elements

        assert elements[ThermalElement.FLOOR].adjustment_factor == pytest.approx(1.0)
        assert elements[ThermalElement.FLOOR].fixed_adjustment_factor == pytest.approx(0.5)
        for element in (ThermalElement.ROOF, ThermalElement.FACADE, ThermalElement.WINDOW, ThermalElement.DOOR):
            assert elements[element].fixed_adjustment_factor == pytest.approx(1.0)


@pytest.mark.base
class TestEveryRowIsSelectable:
    """The former usable-row rule is gone: no row is refused for its geometry any more.

    ``IE.N.SFH.05`` has 29.01 m2 of windows but no door area in the table; it used to be skipped
    for a neighbouring band unless the request carried both areas. The ``Building`` gives it
    TABULA's estimated door now, so the exact band is used again, with or without areas.
    """

    def test_the_mockups_house_lands_on_its_own_band(self) -> None:
        """1975 is band 05's range, and band 05 is used exactly, with no substitution note."""
        selection = select(BuildingType.DETACHED_SFH, 1975)

        assert selection.code == "IE.N.SFH.05.Gen.ReEx.001.001"
        assert not selection.is_approximated()

    def test_an_expert_code_for_the_row_without_a_door_area_is_accepted(self) -> None:
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

    def test_an_unknown_variant_of_an_existing_band_is_refused(self) -> None:
        """The whole code is looked up: band 05 exists, its variant ``009`` does not.

        Accepted, the code would only fail later, inside the simulation, when the ``Building``
        finds no row for it.
        """
        with pytest.raises(TabulaUnresolvable, match="IE.N.SFH.05.Gen.ReEx.001.009"):
            select(BuildingType.DETACHED_SFH, 1975, requested_code="IE.N.SFH.05.Gen.ReEx.001.009")

    def test_an_existing_refurbishment_variant_is_accepted(self) -> None:
        """The lookup is by whole code, so a variant the table does carry stays an override."""
        selection = select(
            BuildingType.DETACHED_SFH, 1975, requested_code="IE.N.SFH.05.Gen.ReEx.001.002"
        )

        assert selection.code == "IE.N.SFH.05.Gen.ReEx.001.002"

    def test_a_country_with_no_typology_is_refused(self) -> None:
        """Spain has no ``.N.`` codes, which the request validation turns into a named problem."""
        with pytest.raises(TabulaUnresolvable):
            select(BuildingType.DETACHED_SFH, 1990, country="ES")
