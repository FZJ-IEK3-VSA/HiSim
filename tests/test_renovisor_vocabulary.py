"""Tests of the closed vocabularies the contract carries and HiSim does not already define.

Two things matter about them and nothing else does: every member's value equals its name, which is
what decision C3 fixed so a value is spelled the same at every hop, and the heat distribution
vocabulary is HiSim's own rather than a second copy of it.
"""

from enum import Enum
from typing import Tuple, Type

import pytest

from hisim.components.heat_distribution_system import HeatDistributionSystemType
from hisim.renovisor import vocabulary
from hisim.renovisor.vocabulary import (
    DhwSupply,
    FloorConstruction,
    HeatDistribution,
    HeatGenerator,
    Provenance,
    RetrofitStatus,
    SolarThermalSupplies,
    TabulaBuildingType,
    TemperatureControl,
    ThermalElement,
    VentilationType,
    VocabularyLookup,
    WallConstruction,
)

pytestmark = pytest.mark.base

VOCABULARIES: Tuple[Type[Enum], ...] = (
    HeatGenerator,
    DhwSupply,
    SolarThermalSupplies,
    VentilationType,
    TemperatureControl,
    RetrofitStatus,
    TabulaBuildingType,
    ThermalElement,
    FloorConstruction,
    WallConstruction,
    Provenance,
)


@pytest.mark.parametrize("vocabulary_class", VOCABULARIES, ids=lambda item: item.__name__)
def test_every_member_value_equals_its_name(vocabulary_class: Type[Enum]) -> None:
    """Decision C3: values are the UPPER_SNAKE member names, so no second spelling exists."""
    for member in vocabulary_class:
        assert member.value == member.name
        assert member.name == member.name.upper()


def test_heat_distribution_is_hisim_s_own_vocabulary() -> None:
    """HeatDistribution is a re-export, not a copy: a HiSim change cannot drift away from it."""
    assert HeatDistribution is HeatDistributionSystemType
    assert {member.name for member in HeatDistribution} == {
        "RADIATOR",
        "FLOORHEATING",
        "LOW_TEMPERATURE_RADIATOR",
    }


def test_the_eleven_heat_generators_are_all_there() -> None:
    """The catalogue's heating-system option has eleven values and this vocabulary carries them."""
    assert len(HeatGenerator) == 11


def test_the_five_thermal_elements_are_the_envelope_details_block() -> None:
    """Decision F5: measures resolve onto five elements, the ones the inventory carries."""
    assert [member.value for member in ThermalElement] == ["ROOF", "FACADE", "FLOOR", "WINDOW", "DOOR"]


def test_lookup_accepts_the_contract_s_lower_case_spelling() -> None:
    """The vendored contract still writes inventory enums in lower case; readers normalise."""
    assert VocabularyLookup.member(RetrofitStatus, "unrenovated") is RetrofitStatus.UNRENOVATED
    assert VocabularyLookup.member(RetrofitStatus, " USUAL_REFURB ") is RetrofitStatus.USUAL_REFURB


def test_lookup_raises_on_an_unknown_value() -> None:
    """An unknown value is a KeyError the caller turns into a reason code, not a default."""
    with pytest.raises(KeyError):
        VocabularyLookup.member(HeatGenerator, "nuclear")


def test_no_vocabulary_is_defined_twice() -> None:
    """Every enum in the module is listed in this test, so a new one cannot escape the checks."""
    defined = {
        name
        for name, value in vars(vocabulary).items()
        if isinstance(value, type) and issubclass(value, Enum) and value.__module__ == vocabulary.__name__
    }
    assert defined == {item.__name__ for item in VOCABULARIES}
