"""Tests of choosing the LoadProfileGenerator household nearest to a dwelling's residents.

The matcher is the one place in the translation layer where an exact answer does not exist: the
contract describes people and the LoadProfileGenerator offers sixty-odd fixed households, so what
is tested is not "the right household" but that the *order* of preference is the stated one, that
an exact composition is found when one exists, and that a composition with no exact entry is
relaxed in the stated order and says so.
"""

from typing import Any, Dict, List

import pytest

from hisim.renovisor.inventory import Inventory
from hisim.renovisor.occupancy import (
    HouseholdCatalogue,
    HouseholdMatcher,
    HouseholdNameReading,
    TravelRouteSetCatalogue,
)
from hisim.renovisor.reasons import ReasonCode, ValidationError

pytestmark = pytest.mark.base


def inventory_for(
    residents_type: List[str], employment: List[str], travel_route_set: Any = None
) -> Inventory:
    """Return the smallest inventory the matcher reads: one occupancy block.

    Args:
        residents_type: One ``"adult"`` or ``"kid"`` per resident.
        employment: One employment status per resident.
        travel_route_set: The commuting profile's name, or ``None``.

    Returns:
        The inventory; nothing outside ``occupancy_config`` is filled in, because nothing outside
        it is read.
    """
    document: Dict[str, Any] = {
        "occupancy_config": {
            "residents_count": len(residents_type),
            "residents_type": residents_type,
            "residents_employment_status": employment,
            "travel_route_set": travel_route_set,
        }
    }
    return Inventory.from_dict(document)


def test_a_working_couple_matches_the_catalogue_exactly() -> None:
    """Two working adults are a composition the catalogue has, so nothing is relaxed."""
    match = HouseholdMatcher().match(inventory_for(["adult", "adult"], ["full_time", "full_time"]))

    assert match.is_exact()
    assert match.matched.adults == 2
    assert match.matched.children == 0
    assert match.matched.working_adults == 2
    assert "matched exactly" in match.note


def test_a_single_worker_matches_a_single_household() -> None:
    """One working adult picks a one-adult household rather than a couple."""
    match = HouseholdMatcher().match(inventory_for(["adult"], ["full_time"]))

    assert match.is_exact()
    assert match.matched.adults == 1
    assert match.matched.working_adults == 1


def test_a_family_with_two_children_matches_on_the_children_too() -> None:
    """The child count is the second facet, so a two-child family beats a childless couple."""
    match = HouseholdMatcher().match(
        inventory_for(
            ["adult", "adult", "kid", "kid"],
            ["full_time", "full_time", "education", "education"],
        )
    )

    assert match.matched.adults == 2
    assert match.matched.children == 2
    assert match.matched.working_adults == 2


def test_a_retired_couple_matches_a_household_with_nobody_in_work() -> None:
    """Retirement is not employment, so the couple lands on a household whose name says so."""
    match = HouseholdMatcher().match(inventory_for(["adult", "adult"], ["retired", "retired"]))

    assert match.matched.adults == 2
    assert match.matched.working_adults == 0


def test_a_composition_the_catalogue_lacks_relaxes_the_adult_count_first() -> None:
    """The adult count is the first facet, so the nearest adult count wins over the child count."""
    residents = ["adult"] * 5 + ["kid"] * 4
    employment = ["full_time"] * 3 + ["unemployed", "retired"] + ["education"] * 4

    match = HouseholdMatcher().match(inventory_for(residents, employment))

    assert not match.is_exact()
    assert "relaxed:" in match.note
    assert match.requested.adults == 5
    assert match.matched.adults == max(
        entry.composition.adults for entry in HouseholdCatalogue.entries()
    )


def test_an_unknown_resident_type_is_a_validation_error() -> None:
    """The contract lists two resident types, and a third is the caller's mistake, not a guess."""
    with pytest.raises(ValidationError) as error:
        HouseholdMatcher().match(inventory_for(["adult", "robot"], ["full_time", "full_time"]))

    assert error.value.reason is ReasonCode.SCHEMA_VIOLATION
    assert error.value.path == "occupancy_config.residents_type[1]"


def test_the_travel_route_set_is_looked_up_by_name() -> None:
    """The contract carries the profile's display name, and the constructor takes the reference."""
    match = HouseholdMatcher().match(
        inventory_for(
            ["adult"], ["full_time"], travel_route_set="Travel Route Set for 10km Commuting Distance"
        )
    )

    assert match.travel_route_set is not None
    assert match.travel_route_set.Name == "Travel Route Set for 10km Commuting Distance"


def test_an_unknown_travel_route_set_is_a_validation_error() -> None:
    """A commuting profile the LoadProfileGenerator does not have is not silently dropped."""
    with pytest.raises(ValidationError) as error:
        TravelRouteSetCatalogue.by_name("Travel Route Set for 400km", "occupancy_config.travel_route_set")

    assert error.value.reason is ReasonCode.SCHEMA_VIOLATION
    assert "Travel Route Set for 10km Commuting Distance" in error.value.detail


def test_no_workplace_profile_can_be_chosen_as_a_dwelling() -> None:
    """The catalogue also ships offices, and an office must never become a household's occupancy."""
    names = [entry.name for entry in HouseholdCatalogue.entries()]

    assert names, "the catalogue yielded no households at all"
    assert all(HouseholdNameReading.is_dwelling(name) for name in names)
    assert not any("Office" in name for name in names)


def test_every_chosen_household_has_at_least_one_adult() -> None:
    """An entry whose name yields no adult count is left out rather than guessed at."""
    for entry in HouseholdCatalogue.entries():
        assert entry.composition.adults >= 1, entry.name


def test_the_unreadable_catalogue_names_are_few_and_named() -> None:
    """Widening the phrase rules must be a decision, so the names that fall through are asserted.

    Two of the sixty-five dwelling entries describe their composition in prose no rule reads:
    ``CHR06 Jak Jobless`` names a person rather than a household, and ``CHR20 one at work, one
    work home, 3 children`` counts its adults only by implication.
    """
    assert HouseholdCatalogue.unreadable() == (
        "CHR06 Jak Jobless",
        "CHR20 one at work, one work home, 3 children",
    )


def test_the_catalogue_order_is_stable() -> None:
    """Requirement R10: two collections of the catalogue choose the same household every time."""
    first = [entry.attribute for entry in HouseholdCatalogue.entries()]
    second = [entry.attribute for entry in HouseholdCatalogue.entries()]

    assert first == second == sorted(first)


def test_a_name_with_two_kinds_of_adult_adds_them_up() -> None:
    """A multigenerational household is a couple plus its seniors, not one or the other."""
    composition = HouseholdNameReading.of(
        "CHR15 Multigenerational Home: working couple, 2 children, 2 seniors"
    )

    assert composition is not None
    assert composition.adults == 4
    assert composition.children == 2
