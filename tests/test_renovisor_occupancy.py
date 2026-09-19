"""The dormant LoadProfileGenerator seam still works, although the MVP never enters it.

Decision D-C settled that every household is simulated as the one precomputed ``CHR01 Couple
both at Work`` profile the image ships. The matcher is kept because that is a deferral and not a
reversal: the moment the binary is in the image, ``OccupancyMode.LOCAL_LPG`` becomes selectable
and the translator needs a nearest neighbour over the catalogue again. A seam nobody tests rots,
so these tests keep it working and keep the deferral visible.
"""

import pytest

from hisim.renovisor.constants import OccupancyMode, PredefinedHousehold
from hisim.renovisor.occupancy import (
    HouseholdCatalogue,
    HouseholdComposition,
    HouseholdMatcher,
    HouseholdNameReading,
)


@pytest.mark.base
class TestReadingACatalogueName:
    """The catalogue describes a household in prose, and nothing else describes it at all."""

    @pytest.mark.parametrize(
        "name, adults, children",
        [
            ("CHR01 Couple both at Work", 2, 0),
            ("CHR03 Family, 1 child, both at work", 2, 1),
            ("CHR27 Family both at work, 2 children", 2, 2),
        ],
    )
    def test_a_name_the_rules_read_yields_its_composition(
        self, name: str, adults: int, children: int
    ) -> None:
        """A closed, ordered set of phrase patterns over exactly the catalogue's own prose."""
        composition = HouseholdNameReading.of(name)

        assert composition is not None
        assert composition.adults == adults
        assert composition.children == children

    def test_a_name_no_rule_reads_yields_nothing_rather_than_a_guess(self) -> None:
        """An entry that drops out of the choice is better than one that was invented."""
        assert HouseholdNameReading.of("CHR99 Something nobody described") is None

    def test_the_unreadable_names_are_a_question_the_rules_can_be_asked(self) -> None:
        """Widening the rules is then a decision somebody takes, not something that happens."""
        assert isinstance(HouseholdCatalogue.unreadable(), tuple)

    def test_a_composition_describes_itself_in_one_phrase(self) -> None:
        """It is the note a report line would carry, so it has to read as a sentence."""
        described = HouseholdComposition(adults=2, children=1, working_adults=2).describe()

        assert "2 adult(s)" in described and "1 child(ren)" in described


@pytest.mark.base
class TestTheCatalogue:
    """Read from the client library at import, never transcribed."""

    def test_it_offers_a_stable_and_non_empty_choice(self) -> None:
        """The order is the attribute name's, so every tie resolves the same way everywhere."""
        entries = HouseholdCatalogue.entries()

        assert len(entries) > 20
        assert [entry.attribute for entry in entries] == sorted(entry.attribute for entry in entries)

    def test_the_one_household_the_image_ships_a_profile_for_is_in_it(self) -> None:
        """Decision D-C names it, and the translator writes it into every file."""
        names = {entry.name for entry in HouseholdCatalogue.entries()}

        assert PredefinedHousehold.NAME in names


@pytest.mark.base
class TestTheMatcher:
    """Nearest by the one fact the calculation request carries: how many people live there."""

    def test_an_exact_household_size_matches_exactly(self) -> None:
        """Three residents find a three-person household, and the note says it was exact."""
        match = HouseholdMatcher().match(3)

        assert match.is_exact()
        assert "matched exactly" in match.note

    def test_a_size_the_catalogue_does_not_have_falls_to_the_nearest(self) -> None:
        """And says which household it became, which is the whole point of the note."""
        match = HouseholdMatcher().match(12)

        assert not match.is_exact()
        assert match.household.Name in match.note

    def test_the_same_request_always_chooses_the_same_household(self) -> None:
        """Ties fall to the alphabetically first attribute, so two runs agree (requirement R10)."""
        assert HouseholdMatcher().match(4).household.Name == HouseholdMatcher().match(4).household.Name


@pytest.mark.base
class TestTheModeTheMvpUses:
    """What the translator writes instead of calling any of the above."""

    def test_the_predefined_mode_names_the_acquisition_mode_hisim_takes(self) -> None:
        """Written explicitly so the connector's own fallback chain is never entered."""
        assert OccupancyMode.PREDEFINED_CHR01.acquisition_mode == "USE_PREDEFINED_PROFILE"
        assert OccupancyMode.LOCAL_LPG.acquisition_mode == "USE_LOCAL_LPG"

    def test_the_household_reference_uses_the_capitalised_keys_the_codec_reads(self) -> None:
        """``name``/``guid`` deserialise silently into an empty reference; ``Name``/``Guid`` do not."""
        reference = PredefinedHousehold.reference()

        assert reference["Name"] == PredefinedHousehold.NAME
        assert reference["Guid"] == {"StrVal": PredefinedHousehold.GUID}
