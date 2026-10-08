"""``ComponentID.path`` (D5): the structured address, its serialization and what it leaves untouched."""

from __future__ import annotations

import dataclasses

import pytest

from hisim.config import AddressStep, ComponentID, NameSyntax
from tests.assemblies.mock_components import MockTankConfig


@pytest.mark.assemblies
def test_the_address_joins_the_import_the_instance_and_the_member_with_hyphens() -> None:
    """Catches a key that loses the instance, or a building no longer prefixing it."""
    east = ComponentID("PVSystem", path=(AddressStep("pv", "east"),), assembly="pv/array")
    assert (east.address, east.key) == ("pv-east-PVSystem", "pv-east-PVSystem")
    assert ComponentID("HeatPump", path=(AddressStep("heating"),)).key == "heating-HeatPump"
    assert ComponentID("PVSystem", building="BUI2", path=(AddressStep("pv", "east"),)).key == "BUI2_pv-east-PVSystem"
    assert ComponentID("Weather").address == "Weather"


@pytest.mark.assemblies
def test_the_display_name_is_presentation_and_never_identity() -> None:
    """Catches two identities differing only by their label comparing or hashing differently."""
    one = ComponentID("PVSystem", path=(AddressStep("pv", "east"),), display_name="PV array, east")
    other = dataclasses.replace(one, display_name="Something else")
    assert one == other and hash(one) == hash(other)


@pytest.mark.assemblies
def test_the_owning_assembly_is_informative_and_never_identity() -> None:
    """Catches two identities of one runtime name comparing or hashing differently by their assembly."""
    one = ComponentID("PVSystem", path=(AddressStep("pv", "east"),), assembly="pv/array")
    other = dataclasses.replace(one, assembly="other/array")
    assert one == other and hash(one) == hash(other) and one.key == other.key
    assert other.to_dict()["assembly"] == "other/array"


@pytest.mark.assemblies
def test_an_identity_outside_every_assembly_serializes_as_before_and_a_path_round_trips() -> None:
    """Catches the new fields changing the dump (and so every cache key) of a component without an address."""
    assert ComponentID("Weather", building="BUI1").to_dict() == {"name": "Weather", "building": "BUI1", "unit": None}
    east = ComponentID("PVSystem", path=(AddressStep("pv", "east"),), assembly="pv/array", display_name="PV")
    assert ComponentID.from_dict(east.to_dict()) == east


@pytest.mark.assemblies
def test_the_cache_key_ignores_where_a_member_sits() -> None:
    """Catches two arrays of identical configuration computing their series twice."""
    plain = MockTankConfig.preset_standard("Tank")
    member = dataclasses.replace(
        plain, component_id=ComponentID("Tank", path=(AddressStep("dhw"),), assembly="dhw/tank")
    )
    assert plain.cache_key_view().component_id == member.cache_key_view().component_id


@pytest.mark.assemblies
@pytest.mark.parametrize("step", [("pv-east", None), ("pv", "east west"), ("", None)])
def test_an_address_step_is_made_of_identifiers(step: tuple) -> None:
    """Catches a key containing the separator, which would make two addresses serialize alike."""
    with pytest.raises(ValueError):
        AddressStep(*step)


@pytest.mark.assemblies
def test_a_runtime_name_may_carry_the_separator_and_nothing_else() -> None:
    """Catches the component-name rule admitting a wildcard or an empty part."""
    NameSyntax.require_component_key("pv-east-PVSystem")
    for name in ("pv--PVSystem", "pv-*", "-PVSystem", "pv/east"):
        with pytest.raises(ValueError, match="not a usable component name"):
            NameSyntax.require_component_key(name)
